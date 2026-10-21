"""Shared tool admission for registered adapters; route authority stays server-owned."""

import hashlib
import json
import secrets
import time
from typing import Annotated

from pydantic import Field

from .channels.actions import MessageDelivery, known_message, message_delivery
from .channels.platforms.common import CONNECTION_KEYS, PLATFORMS, PROFILES
from .channels.qq_operations import require_grant
from .media import decode_media, image_type, public_message
from .models import route_allows_sender


def authority_key(route, connection):
    snapshot = [route, {key: connection.get(key) for key in CONNECTION_KEYS}]
    return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()


def install_adapter_operations(operations, store, bridge, authorized_route):
    async def context(route_id, *, history=False):
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection or connection["kind"] not in PLATFORMS or bridge.channels is None:
            raise ValueError("Choose a supported platform route")
        if history:
            require_grant(route, "history.read")
        return route, connection, bridge.channels.platform(connection)

    @operations.register
    async def send_platform_message(
        content: list[dict],
        route_id: str | None = None,
        delivery: MessageDelivery | None = None,
        reply_to: str | None = None,
    ) -> dict:
        from .mcp_surface import MediaPart, TextPart

        route, connection, _ = await context(route_id)
        selected = message_delivery(route, connection, delivery)
        texts = [TextPart.model_validate(part) for part in content if part.get("type") == "text"]
        media = [MediaPart.model_validate(part) for part in content if part.get("type") != "text"]
        if len(texts) > 1 or len(media) > 1 or not content:
            raise ValueError("Use one text and at most one media part")
        text = texts[0].text if texts else ""
        fmt = texts[0].format if texts else "text"
        if fmt != "text" and connection["kind"] != "slack":
            raise ValueError("This adapter currently sends plain text; rich text uses a separate platform contract")
        if len(text) > PROFILES[connection["kind"]].max_text or (not text.strip() and not media):
            raise ValueError("Message content is empty or exceeds the platform text limit")
        payload: dict = {"kind": "message", "message_format": fmt, "delivery": selected}
        if reply_to:
            await known_message(store, route, reply_to)
            payload["reference_id"] = reply_to
            if connection["kind"] == "slack" and route["conversation_kind"] != "thread":
                raise ValueError("Bind the Slack thread explicitly before replying in it")
        if media:
            item = media[0]
            if item.url:
                raise ValueError("This adapter uploads inline media; download files in the Host sandbox first")
            blob = decode_media(item.data_base64 or "")
            if len(blob) > PROFILES[connection["kind"]].max_media:
                raise ValueError("Media exceeds the platform upload limit")
            if item.type == "image" and image_type(blob) != item.media_type:
                raise ValueError("Image media type does not match its signature")
            if item.type != "file" and not item.media_type.startswith(item.type + "/"):
                raise ValueError("Media kind and type do not match")
            if connection["kind"] == "lark":
                if text or reply_to or fmt != "text":
                    raise ValueError("Lark media is a separate message; send its caption separately")
                if item.type == "audio" and item.media_type not in {"audio/ogg", "audio/opus"}:
                    raise ValueError("Lark audio uploads require Opus; convert in the Host sandbox")
                if item.type == "video" and item.media_type != "video/mp4":
                    raise ValueError("Lark video uploads require MP4")
            if connection["kind"] == "slack" and reply_to:
                raise ValueError("Slack file replies use the bound thread, without a separate quote")
            payload.update(
                kind="media",
                media_kind=item.type,
                media_type=item.media_type,
                filename=item.filename,
                image_base64=item.data_base64,
                caption=text,
            )
        reply = await store.queue_reply(route, text or "[Attachment]", payload=payload)
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    @operations.register
    async def read_platform_messages(
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
        cursor: Annotated[str, Field(max_length=256)] | None = None,
        route_id: str | None = None,
    ) -> dict:
        route, connection, client = await context(route_id, history=True)
        key = authority_key(route, connection)
        await store.prune_history_cache(connection["id"])
        native_cursor = None
        if cursor:
            state = await store.channel_state(connection["id"], "history_cursor:" + cursor)
            if state.get("authority") != key or state.get("expires", 0) < time.time():
                raise ValueError("History cursor is expired or belongs to another scope")
            native_cursor = state["cursor"]
        await client.ensure_identity()
        current, current_connection, _ = await context(route["id"], history=True)
        if authority_key(current, current_connection) != key:
            raise ValueError("History authority changed before the read")
        messages, next_cursor = await client.history(route, limit, native_cursor)
        if len(messages) > limit:
            raise ValueError("Platform returned more history than requested")
        messages = [
            message
            for message in messages
            if route_allows_sender(route, message["sender_id"]) or message["sender_id"] == connection["self_id"]
        ]
        for message in messages:
            message["address"]["conversation_id"] = route["conversation_kind"] + ":" + route["conversation_id"]
            # Cache asset provenance privately, never import native history into a
            # gateway account-read grant or replay it into an Agent Host.
            await store.set_channel_state(
                connection["id"],
                "history_message:" + route["id"] + ":" + message["source_message_id"],
                {"authority": key, "expires": time.time() + 900, "message": message},
            )
        current, current_connection, _ = await context(route["id"], history=True)
        if authority_key(current, current_connection) != key:
            raise ValueError("History authority changed during the read")
        handle = None
        if next_cursor:
            handle = secrets.token_urlsafe(24)
            await store.set_channel_state(
                connection["id"],
                "history_cursor:" + handle,
                {"authority": key, "expires": time.time() + 900, "cursor": next_cursor},
            )
        return {
            "source": "platform",
            "coverage": {"complete": False, "scope": "exact_route", "identity": "bot"},
            "messages": [public_message(item) for item in sorted(messages, key=lambda item: item["occurred_at"])],
            "next_cursor": handle,
        }

    @operations.register
    async def read_platform_asset(
        source_message_id: Annotated[str, Field(min_length=1, max_length=1024)],
        index: Annotated[int, Field(ge=0, le=15)] = 0,
        image: bool = False,
        route_id: str | None = None,
    ):
        route, connection, client = await context(route_id, history=True)
        key = authority_key(route, connection)
        state = await store.channel_state(connection["id"], "history_message:" + route["id"] + ":" + source_message_id)
        if state.get("authority") != key or state.get("expires", 0) < time.time():
            raise ValueError("Read native history again to obtain a current asset handle")
        items = state["message"]["images" if image else "attachments"]
        if not 0 <= index < len(items):
            raise ValueError("Choose an asset index from this native history message")
        item = items[index]
        data, mime = await client.read_asset(item, image=image)
        current, current_connection, _ = await context(route["id"], history=True)
        if authority_key(current, current_connection) != key:
            raise ValueError("Asset authority changed during the read")
        if image:
            from mcp.server.fastmcp.utilities.types import Image

            return Image(data=data, format=mime.removeprefix("image/"))
        from .mcp_platform import attachment_content

        return attachment_content(data, mime, item, source_message_id, index)
