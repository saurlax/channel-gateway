"""Official MCP Streamable HTTP tools, scoped by revocable host or route capabilities."""

import hashlib
import json
from contextvars import ContextVar
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .bridge import Bridge
from .channels.actions import MessageDelivery, message_delivery, proactive_authority, tool_message_allowed
from .channels.manager import capabilities
from .media import MAX_IMAGE_BASE64, MAX_MEDIA_BYTES, MediaReader, public_message
from .models import ImageReplyInput
from .storage import Store

current_token: ContextVar[str] = ContextVar("channel_gateway_mcp_token")


def create_mcp(store: Store, bridge: Bridge, media: MediaReader) -> FastMCP:
    server = FastMCP(
        "Channel Gateway",
        stateless_http=True,
        json_response=True,
        max_request_body_size=4 * ((MAX_MEDIA_BYTES + 2) // 3) + 65536,
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
        if route_id is None and credential_route:
            sources = credential_route.get("sources")
            single_source = bool(
                sources
                and len(sources) == 1
                and sources[0]["channel_id"] != "*"
                and sources[0]["conversation_id"] != "*"
            )
            if credential_route["conversation_id"] != "*" or (single_source and len(routes) == 1):
                return routes[0]
        route = next((row for row in routes if row["id"] == route_id), None)
        if route is None:
            raise ValueError("Choose an authorized route_id from list_conversations")
        return route

    async def readable_routes() -> list[dict]:
        routes = await authorized_routes()
        token = current_token.get()
        credential_route = await store.route_for_token(token)
        if credential_route:
            configured = [credential_route]
        else:
            host = await store.host_for_token(token)
            if not host:
                raise ValueError("Channel access is disabled or revoked")
            configured = [
                row
                for row in await store.routes()
                if row["host_id"] == host["id"] and row["enabled"] and not row.get("parent_route_id")
            ]
        channel_ids = list({channel for row in configured for channel in row.get("read_channel_ids") or []})
        connections = {
            row["id"]: row for row in await store.connections() if row["kind"] in {"onebots", "qq", "weixin"}
        }
        channels = set(connections)
        views = {}
        for route in routes:
            channel_read = route["channel_id"] in channels and (
                "*" in channel_ids or route["channel_id"] in channel_ids
            )
            identity = (route["channel_id"], route["conversation_kind"], route["conversation_id"])
            views[identity] = {
                **route,
                "can_read": channel_read or route.get("read_channel_ids") is None,
                "can_reply": tool_message_allowed(route, connections[route["channel_id"]])
                if route["channel_id"] in connections
                else False,
                "channel_read": channel_read,
            }
        for conversation in await store.readable_conversations(channel_ids):
            identity = (conversation["channel_id"], conversation["conversation_kind"], conversation["conversation_id"])
            if identity in views:
                continue
            identifier = "read_" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()
            views[identity] = {
                **conversation,
                "id": identifier,
                "name": f"{conversation['conversation_kind']}:{conversation['conversation_id']}",
                "reply_source": None,
                "can_read": True,
                "can_reply": False,
                "channel_read": True,
            }
        return list(views.values())

    @server.tool()
    async def list_conversations() -> dict:
        """List readable conversations and reply destinations, with can_read/can_reply flags."""
        conversations = []
        for route in await readable_routes():
            connection = await store.connection(route["channel_id"])
            conversations.append(
                {
                    **{
                        key: route[key]
                        for key in (
                            "id",
                            "name",
                            "channel_id",
                            "conversation_kind",
                            "conversation_id",
                            "reply_source",
                            "can_read",
                            "can_reply",
                        )
                    },
                    "capabilities": capabilities(connection) if connection else {},
                    "default_delivery": (
                        "proactive" if connection and proactive_authority(route, connection) else "passive"
                    )
                    if route["can_reply"]
                    else None,
                    "can_send_proactively": bool(
                        connection and route["can_reply"] and proactive_authority(route, connection)
                    ),
                }
            )
        return {"conversations": conversations}

    async def readable_route(route_id: str | None) -> dict:
        if route_id is None:
            route_id = (await authorized_route(None))["id"]
        route = next((row for row in await readable_routes() if row["id"] == route_id and row["can_read"]), None)
        if not route:
            raise ValueError("Choose a readable route_id from list_conversations")
        return route

    async def received_image(route: dict, source_message_id: str, image_index: int) -> dict:
        messages = (
            await store.received_channel_messages(route, source_message_id=source_message_id, limit=1)
            if route["channel_read"]
            else await store.received_messages(route, source_message_id=source_message_id, limit=1)
        )
        images = messages[0].get("images", []) if messages else []
        if not 0 <= image_index < len(images):
            raise ValueError("Choose an image from an authorized received message")
        return images[image_index]

    @server.tool()
    async def read_conversation(limit: int = 20, route_id: str | None = None) -> dict:
        """Read incoming messages and confirmed bot sends in chronological order. Outgoing media descriptions are not received-media read handles."""
        route = await readable_route(route_id)
        messages = await store.conversation_messages(route, limit=limit)
        return {
            "conversation_kind": route["conversation_kind"],
            "conversation_id": route["conversation_id"],
            "messages": [public_message(message) for message in messages],
        }

    @server.tool()
    async def read_image(
        source_message_id: Annotated[str, Field(min_length=1, max_length=1024)],
        image_index: Annotated[int, Field(ge=0, le=3)] = 0,
        route_id: str | None = None,
    ) -> Image:
        """Read an authorized received image as native image content. Use source_message_id and image index from history."""
        route = await readable_route(route_id)
        image = await received_image(route, source_message_id, image_index)
        data, media_type = await media.read(image["url"])
        current = await readable_route(route["id"])
        if await received_image(current, source_message_id, image_index) != image:
            raise ValueError("Image access changed during download")
        return Image(data=data, format=media_type.removeprefix("image/"))

    async def reply_route(route_id: str | None, delivery: MessageDelivery | None) -> tuple[dict, dict, MessageDelivery]:
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection:
            raise ValueError("Channel is unavailable")
        return route, connection, message_delivery(route, connection, delivery)

    @server.tool()
    async def send_image(
        image_base64: Annotated[str, Field(min_length=1, max_length=MAX_IMAGE_BASE64)],
        media_type: Literal["image/png", "image/jpeg", "image/gif", "image/webp"],
        caption: Annotated[str, Field(max_length=16000)] = "",
        route_id: str | None = None,
        delivery: Literal["passive", "proactive", "wakeup"] | None = None,
    ) -> dict:
        """Queue an image (strict base64, at most 4 MiB). QQ defaults to proactive with message.proactive; explicit delivery='passive' uses reply context. Other channels keep passive MCP replies."""
        route, connection, delivery = await reply_route(route_id, delivery)
        validated = ImageReplyInput.model_validate(
            {"image_base64": image_base64, "media_type": media_type, "caption": caption}
        )
        if validated.media_type not in capabilities(connection)["image_send_types"]:
            raise ValueError("Image format is not supported by this channel")
        reply = await store.queue_reply(
            route, caption or "[Image]", payload={"kind": "image", **validated.model_dump(), "delivery": delivery}
        )
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    from .mcp_platform import install_platform_tools

    install_platform_tools(server, store, bridge, media, authorized_route, readable_route)

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
