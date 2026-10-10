"""Official MCP Streamable HTTP tools, scoped by a revocable route capability."""

from contextvars import ContextVar

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .bridge import Bridge
from .models import ReplyInput
from .storage import Store

current_token: ContextVar[str] = ContextVar("channel_gateway_route_token")


def create_mcp(store: Store, bridge: Bridge) -> FastMCP:
    server = FastMCP(
        "Channel Gateway",
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        # Bearer auth is enforced by the outer ASGI application. Cross-origin
        # browser requests are refused there; deployment domains need no wildcard.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        instructions="Tools are scoped to one authorized channel conversation. Group messages become drafts for administrator approval.",
    )

    async def authorized_route() -> dict:
        route = await store.route_for_token(current_token.get())
        if not route or not route["enabled"]:
            raise ValueError("Channel access is disabled or revoked")
        return route

    @server.tool()
    async def read_conversation(limit: int = 20) -> dict:
        """Read received text messages from this credential's authorized conversation."""
        route = await authorized_route()
        messages = await store.events(route_id=route["id"], limit=max(1, min(limit, 100)))
        return {
            "conversation_kind": route["conversation_kind"],
            "conversation_id": route["conversation_id"],
            "messages": [
                row["message"] for row in reversed(messages) if row["message"]["sender_id"] in route["allowed_user_ids"]
            ],
        }

    @server.tool()
    async def send_message(text: str) -> dict:
        """Request a text reply to this conversation. Group replies require administrator approval."""
        route = await authorized_route()
        if route["reply_source"] != "mcp":
            raise ValueError("This route forwards final run output; return reply text from the run instead")
        validated = ReplyInput(text=text)
        reply = await store.draft(route, validated.text)
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
        if not route or not route["enabled"]:
            await JSONResponse({"detail": "A valid route token is required"}, status_code=401)(scope, receive, send)
            return
        context = current_token.set(token)
        try:
            await self.app(scope, receive, send)
        finally:
            current_token.reset(context)
