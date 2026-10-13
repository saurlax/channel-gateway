"""Official MCP Streamable HTTP tools, scoped by revocable host or route capabilities."""

from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .bridge import Bridge
from .models import ReplyInput
from .storage import Store

current_token: ContextVar[str] = ContextVar("channel_gateway_mcp_token")


def create_mcp(store: Store, bridge: Bridge) -> FastMCP:
    server = FastMCP(
        "Channel Gateway",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # Bearer auth is enforced by the outer ASGI application. Cross-origin
        # browser requests are refused there; deployment domains need no wildcard.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        instructions="List authorized conversations, then pass their route_id to read or reply. A route credential can omit route_id. Replies to authorized conversations are queued for automatic sending. MCP reads do not wake or complete an agent run.",
    )

    async def authorized_routes() -> list[dict]:
        token = current_token.get()
        route = await store.route_for_token(token)
        if route and route["enabled"]:
            if route["conversation_id"] == "*":
                await store.materialize_observed_routes(route)
                return [
                    row for row in await store.routes() if row.get("parent_route_id") == route["id"] and row["enabled"]
                ]
            return [route]
        host = await store.host_for_token(token)
        if host:
            for parent in await store.routes():
                if parent["host_id"] == host["id"] and parent["enabled"] and parent["conversation_id"] == "*":
                    await store.materialize_observed_routes(parent)
            return [
                row
                for row in await store.routes()
                if row["host_id"] == host["id"] and row["enabled"] and row["conversation_id"] != "*"
            ]
        raise ValueError("Channel access is disabled or revoked")

    async def authorized_route(route_id: str | None) -> dict:
        routes = await authorized_routes()
        credential_route = await store.route_for_token(current_token.get())
        if route_id is None and credential_route and credential_route["conversation_id"] != "*":
            return routes[0]
        route = next((row for row in routes if row["id"] == route_id), None)
        if route is None:
            raise ValueError("Choose an authorized route_id from list_conversations")
        return route

    @server.tool()
    async def list_conversations() -> dict:
        """List enabled conversations assigned to this credential, without message content."""
        return {
            "conversations": [
                {
                    key: route[key]
                    for key in ("id", "name", "channel_id", "conversation_kind", "conversation_id", "reply_source")
                }
                for route in await authorized_routes()
            ]
        }

    @server.tool()
    async def read_conversation(limit: int = 20, route_id: str | None = None) -> dict:
        """Read received text messages from this credential's authorized conversation."""
        route = await authorized_route(route_id)
        messages = await store.received_messages(route, limit=limit)
        return {
            "conversation_kind": route["conversation_kind"],
            "conversation_id": route["conversation_id"],
            "messages": messages,
        }

    @server.tool()
    async def send_message(text: str, route_id: str | None = None) -> dict:
        """Request a text reply to this conversation. Replies are sent automatically after current authorization checks."""
        route = await authorized_route(route_id)
        if route["reply_source"] != "mcp":
            raise ValueError("This route forwards final run output; return reply text from the run instead")
        validated = ReplyInput(text=text)
        reply = await store.queue_reply(route, validated.text)
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    return server


class RouteAuthorization:
    def __init__(self, app: ASGIApp, store: Store):
        self.app = app
        self.store = store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        # Agent clients do not send Origin. Refuse browser origins to prevent
        # DNS rebinding and cross-origin use of capability credentials.
        if b"origin" in headers:
            await JSONResponse({"detail": "Browser origins are not accepted by the MCP endpoint"}, status_code=403)(
                scope, receive, send
            )
            return
        auth = headers.get(b"authorization", b"").decode("latin-1")
        token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
        route = await self.store.route_for_token(token) if token else None
        host = await self.store.host_for_token(token) if token else None
        if not host and (not route or not route["enabled"]):
            await JSONResponse({"detail": "A valid host key or route token is required"}, status_code=401)(
                scope, receive, send
            )
            return
        context = current_token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            current_token.reset(context)
