"""Scoped message creation, independent actions, and discoverable QQ resources."""

import base64
import json
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import AudioContent, BlobResourceContents, EmbeddedResource, TextResourceContents
from pydantic import AnyUrl, Field

from .channels.actions import known_message, message_delivery, reaction_authority
from .channels.emoji import Emoji, validate_platform_emoji
from .channels.platforms.common import PLATFORMS
from .channels.qq_operations import OPERATIONS, applicable, check_json, execute_operation, require_grant, safe_result
from .media import MAX_MEDIA_BYTES, attachment_media_type, decode_media, image_type, public_media_url
from .models import route_allows_sender


def install_platform_operations(operations, store, bridge, media, authorized_route, readable_route):
    async def qq_route(route_id):
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection or connection["kind"] != "qq" or bridge.channels is None:
            raise ValueError("Choose an official QQ source conversation")
        return route, connection

    async def writable_route(route_id):
        route = await authorized_route(route_id)
        if route["reply_source"] != "mcp":
            raise ValueError("This route forwards final run output; return reply text from the run instead")
        connection = await store.connection(route["channel_id"])
        if not connection:
            raise ValueError("Channel is unavailable")
        return route, connection

    async def queue_action(route, name, arguments, grant):
        receipt = await store.queue_channel_action(route, name, arguments, grant)
        bridge.wake.set()
        return receipt

    async def message_route(route_id, delivery):
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection:
            raise ValueError("Channel is unavailable")
        return route, connection, message_delivery(route, connection, delivery)

    @operations.register
    async def send_message(
        text: Annotated[str, Field(max_length=16000)] = "",
        route_id: str | None = None,
        message_format: Literal["text", "markdown"] = "text",
        keyboard: dict | None = None,
        reply_to_source_message_id: str | None = None,
        delivery: Literal["passive", "proactive", "wakeup"] | None = None,
        embed: dict | None = None,
        ark: dict | None = None,
    ) -> dict:
        """Queue a message. QQ defaults to proactive with message.proactive, including final-output routes; otherwise existing MCP routes default to passive. Explicit delivery overrides the default. Reactions and recall use separate tools."""
        route, connection, delivery = await message_route(route_id, delivery)
        if not text and not embed and not ark:
            raise ValueError("A message requires content")
        options = {
            "message_format": message_format,
            "keyboard": keyboard,
            "delivery": delivery,
            "embed": embed,
            "ark": ark,
        }
        check_json(options)
        if len(json.dumps(options)) > 65536:
            raise ValueError("Message options exceed the size limit")
        extended = (
            message_format != "text"
            or keyboard is not None
            or reply_to_source_message_id
            or delivery != "passive"
            or embed
            or ark
        )
        if extended and connection["kind"] != "qq":
            raise ValueError("This channel supports plain text and its declared media tools")
        if (embed or ark) and route["conversation_kind"] not in {"guild", "guild_dm"}:
            raise ValueError("Embed/ARK sending requires a QQ guild conversation")
        if delivery != "passive":
            require_grant(route, "message.proactive")
        if delivery == "wakeup" and route["conversation_kind"] != "private":
            raise ValueError("Wakeup delivery is available in C2C conversations")
        if reply_to_source_message_id:
            ownership, message = await known_message(store, route, reply_to_source_message_id)
            ref = (message or {}).get("native_reference_id")
            if ownership == "sent":
                ref = (await store.channel_state(connection["id"], "reference:" + reply_to_source_message_id)).get("id")
            options["reference_id"] = ref or reply_to_source_message_id
        payload = {"kind": "message", **options} if extended or connection["kind"] == "qq" else None
        reply = await store.queue_reply(route, text or "[Card]", payload=payload)
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    @operations.register
    async def send_media(
        media_kind: Literal["image", "audio", "video", "file"],
        media_type: Annotated[str, Field(min_length=1, max_length=128)],
        filename: Annotated[str, Field(min_length=1, max_length=255)],
        data_base64: Annotated[str, Field(max_length=4 * ((MAX_MEDIA_BYTES + 2) // 3))] | None = None,
        url: Annotated[str, Field(max_length=4096)] | None = None,
        caption: Annotated[str, Field(max_length=16000)] = "",
        route_id: str | None = None,
        delivery: Literal["passive", "proactive", "wakeup"] | None = None,
    ) -> dict:
        """Queue QQ media, defaulting to proactive with message.proactive. Explicit delivery='passive' uses reply context. Provide public HTTPS URL or strict base64 (16 MiB max). Guild uploads support images."""
        route, connection, delivery = await message_route(route_id, delivery)
        if connection["kind"] != "qq":
            raise ValueError("This media operation requires official QQ")
        if bool(data_base64) == bool(url):
            raise ValueError("Choose exactly one media URL or base64 data")
        if any(c in filename for c in ("/", "\\", "\r", "\n", "\0")):
            raise ValueError("Use a plain file name")
        if url:
            public_media_url(url)
        if data_base64:
            data = decode_media(data_base64)
            if media_kind == "image" and image_type(data) != media_type:
                raise ValueError("Image media type does not match its signature")
        if media_kind != "file" and not media_type.startswith(
            {"image": "image/", "audio": "audio/", "video": "video/"}[media_kind]
        ):
            raise ValueError("Media kind and type do not match")
        if route["conversation_kind"] in {"guild", "guild_dm"} and media_kind != "image":
            raise ValueError(
                "The documented guild send API supports image attachments; use guild audio operations for playback"
            )
        if delivery != "passive":
            require_grant(route, "message.proactive")
        if delivery == "wakeup" and route["conversation_kind"] != "private":
            raise ValueError("Wakeup delivery is available in C2C conversations")
        payload = {
            "kind": "media",
            "media_kind": media_kind,
            "media_type": media_type,
            "filename": filename,
            "image_base64": data_base64,
            "url": url,
            "caption": caption,
            "delivery": delivery,
        }
        reply = await store.queue_reply(route, caption or f"[{media_kind.title()}]", payload=payload)
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    @operations.register
    async def set_message_reaction(
        source_message_id: Annotated[str, Field(min_length=1, max_length=1024)],
        emoji_id: Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")] | None = None,
        add: bool = True,
        route_id: str | None = None,
        emoji_type: Literal[1, 2] | None = None,
        emoji: Emoji | None = None,
    ) -> dict:
        """Add/remove a reaction on an existing message. Independent of sending message content. QQ guild or optional OneBot extension only."""
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection or not (
            connection["kind"] in PLATFORMS
            or connection["kind"] == "onebots"
            or (connection["kind"] == "qq" and route["conversation_kind"] == "guild")
        ):
            raise ValueError("Message reactions are not supported by this channel")
        if emoji is not None and emoji_type is not None:
            raise ValueError("Typed emoji cannot include legacy emoji_type")
        if (emoji is None) == (emoji_id is None):
            raise ValueError("Choose exactly one typed emoji or legacy emoji_id")
        value = validate_platform_emoji(
            connection["kind"], emoji or {"kind": "platform", "value": emoji_id, "type": emoji_type or 1}
        )
        if connection["kind"] in PLATFORMS and emoji is None:
            raise ValueError("This platform requires a typed emoji")
        grant = reaction_authority(route, connection["kind"])
        await known_message(store, route, source_message_id)
        return await queue_action(
            route,
            "reaction",
            {
                "source_message_id": source_message_id,
                "emoji": value,
                "emoji_id": value.get("value", ""),
                "add": add,
                "emoji_type": value.get("type", 1),
            },
            grant,
        )

    @operations.register
    async def recall_message(
        message_id: Annotated[str, Field(min_length=1, max_length=1024)],
        route_id: str | None = None,
        hide_tip: bool = False,
    ) -> dict:
        """Recall a known QQ message. Requires message.recall for the bot's own messages or message.recall_members for received messages."""
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection or connection["kind"] not in {"qq", *PLATFORMS}:
            raise ValueError("Recall is not supported by this channel")
        ownership, _ = await known_message(store, route, message_id)
        if connection["kind"] in {"slack", "lark"} and ownership != "sent":
            raise ValueError("This adapter can recall only the bot's confirmed sends")
        grant = "message.recall" if ownership == "sent" else "message.recall_members"
        require_grant(route, grant)
        return await queue_action(route, "recall", {"message_id": message_id, "hide_tip": hide_tip}, grant)

    @operations.register
    async def set_typing(seconds: Annotated[int, Field(ge=1, le=60)] = 30, route_id: str | None = None) -> dict:
        """Show QQ C2C typing state. This is a status action, not a text reply."""
        route, connection = await writable_route(route_id)
        if connection["kind"] != "discord" and (connection["kind"] != "qq" or route["conversation_kind"] != "private"):
            raise ValueError("Typing state is supported in QQ C2C and Discord")
        return await queue_action(route, "typing", {"seconds": seconds}, "message.send")

    @operations.register
    async def send_stream_message(
        text: Annotated[str, Field(min_length=1, max_length=16000)],
        index: Annotated[int, Field(ge=0, le=10000)] = 0,
        stream_handle: str | None = None,
        finished: bool = False,
        message_format: Literal["text", "markdown"] = "text",
        input_mode: Literal["append", "replace"] = "append",
        route_id: str | None = None,
    ) -> dict:
        """Queue an ordered QQ C2C stream fragment. Start at index zero; reuse the returned private handle and increment index. Poll get_operation before continuing after uncertainty."""
        route, connection = await writable_route(route_id)
        if connection["kind"] != "qq" or route["conversation_kind"] != "private":
            raise ValueError("Streaming is supported in QQ C2C only")
        context = await store.channel_state(connection["id"], "context:private:" + route["conversation_id"])
        if not context.get("message_id") or not route_allows_sender(route, context.get("sender_id", "")):
            raise ValueError("Wait for an authorized incoming message")
        receipt = await store.queue_stream_action(
            route,
            stream_handle,
            {
                "text": text,
                "index": index,
                "finished": finished,
                "message_format": message_format,
                "input_mode": input_mode,
            },
            context,
        )
        bridge.wake.set()
        return receipt

    @operations.register
    async def query_channel_resource(
        operation: str, arguments: dict | None = None, route_id: str | None = None
    ) -> dict:
        """Read a native QQ resource using an operation from discover(resource='operations'). No arbitrary API paths or destinations are accepted."""
        route, connection = await qq_route(route_id)
        definition = OPERATIONS.get(operation)
        if definition is None or not definition.readonly:
            raise ValueError("Choose a read-only operation from discover(resource='operations')")
        value = await execute_operation(store, bridge.channels.qq(connection), route, definition, arguments or {})
        current = await authorized_route(route["id"])
        require_grant(current, definition.grant)
        if current != route:
            raise ValueError("Route authority changed during resource read")
        return {"result": safe_result(value)}

    @operations.register
    async def perform_channel_action(
        operation: str, arguments: dict | None = None, route_id: str | None = None
    ) -> dict:
        """Queue a scoped QQ resource mutation. Discover operation schemas and required grants first. Message creation and message actions use their dedicated operation types."""
        route, _ = await qq_route(route_id)
        definition = OPERATIONS.get(operation)
        if definition is None or definition.readonly or not applicable(definition, route):
            raise ValueError("Choose an applicable mutation from discover(resource='operations')")
        require_grant(route, definition.grant)
        definition.validate(arguments or {})
        return await queue_action(route, "resource:" + operation, arguments or {}, definition.grant)

    @operations.register
    async def get_channel_action(action_id: str, route_id: str | None = None) -> dict:
        """Read this route's durable action status. Unknown means verify upstream before repeating. Results are hidden after their grant is revoked."""
        route = await authorized_route(route_id)
        row = await store.channel_action(action_id)
        if not row or row["route_id"] != route["id"]:
            raise ValueError("Choose an action belonging to this authorized route")
        allowed = row["required_grant"] in route.get("action_grants", []) or (
            row["required_grant"] in {"legacy.react", "message.send"} and route["reply_source"] == "mcp"
        )
        return {
            "action_id": row["id"],
            "operation": row["operation"],
            "state": row["state"],
            "error": row["error"],
            "result": json.loads(row["result"]) if allowed and row["result"] else None,
        }

    @operations.register
    async def read_channel_events(limit: Annotated[int, Field(ge=1, le=100)] = 20, route_id: str | None = None) -> dict:
        """Read sanitized QQ notifications for this conversation or verified parent guild. Does not expose raw packets or activate agents."""
        route, connection = await qq_route(route_id)
        require_grant(route, "resource.read")
        context = await store.channel_state(
            connection["id"], "context:" + route["conversation_kind"] + ":" + route["conversation_id"]
        )
        rows = await store.rows(
            "SELECT id,event_type,data,created_at FROM channel_notifications WHERE channel_id=? AND ((scope_kind=? AND scope_id=?) OR (scope_kind='guild_parent' AND scope_id=?) OR (? AND scope_kind='account')) ORDER BY rowid DESC LIMIT ?",
            (
                connection["id"],
                route["conversation_kind"],
                route["conversation_id"],
                context.get("guild_id", ""),
                "bot.manage" in route.get("action_grants", []),
                limit,
            ),
        )
        return {"events": [{**row, "data": json.loads(row["data"])} for row in reversed(rows)]}

    @operations.register
    async def read_attachment(
        source_message_id: str, attachment_index: Annotated[int, Field(ge=0, le=15)] = 0, route_id: str | None = None
    ) -> list[AudioContent | EmbeddedResource]:
        """Read authorized received attachments (16 MiB max). UTF-8 text returns a text resource; other media returns typed native bytes. Clients whose model cannot accept a media type must parse the Host-delivered file in their sandbox instead. No arbitrary URL input."""
        route = await readable_route(route_id)

        async def attachment(current):
            rows = (
                await store.received_channel_messages(current, source_message_id=source_message_id, limit=1)
                if current["channel_read"]
                else await store.received_messages(current, source_message_id=source_message_id, limit=1)
            )
            items = rows[0].get("attachments", []) if rows else []
            if not 0 <= attachment_index < len(items):
                raise ValueError("Choose an attachment from an authorized received message")
            return items[attachment_index]

        item = await attachment(route)
        connection = await store.connection(route["channel_id"])
        if connection and connection["kind"] in PLATFORMS and bridge.channels:
            await bridge.channels.platform(connection).ensure_identity()
            data, mime = await bridge.channels.platform(connection).read_asset(item, image=False)
        else:
            data, mime = await media.read_blob(item["url"])
        if connection != await store.connection(route["channel_id"]):
            raise ValueError("Channel credentials changed during download")
        current = await readable_route(route["id"])
        if await attachment(current) != item:
            raise ValueError("Attachment access changed during download")
        return attachment_content(data, mime, item, source_message_id, attachment_index)


def attachment_content(data, mime, item, source_message_id, attachment_index) -> list[AudioContent | EmbeddedResource]:
    mime = attachment_media_type(data, mime, item["media_type"], item["filename"])
    uri = AnyUrl("channel-attachment:" + quote(source_message_id, safe="") + "/" + str(attachment_index))
    if mime.startswith("text/") or mime in {"application/json", "application/xml", "application/yaml"}:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("Text attachment is not UTF-8; parse the Host-delivered file in its sandbox") from None
        return [EmbeddedResource(type="resource", resource=TextResourceContents(uri=uri, mimeType=mime, text=text))]
    encoded = base64.b64encode(data).decode()
    if mime.startswith("audio/"):
        return [AudioContent(type="audio", data=encoded, mimeType=mime)]
    return [
        EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri=uri,
                mimeType=mime,
                blob=encoded,
            ),
        )
    ]
