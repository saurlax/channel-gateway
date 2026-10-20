"""Scoped message creation, independent actions, and discoverable QQ resources."""

import base64
import json
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.types import AudioContent, BlobResourceContents, EmbeddedResource, ToolAnnotations
from pydantic import AnyUrl, Field

from .channels.actions import known_message, reaction_authority
from .channels.qq_operations import OPERATIONS, applicable, check_json, execute_operation, require_grant, safe_result
from .media import MAX_MEDIA_BYTES, decode_media, image_type, public_media_url
from .models import route_allows_sender


def install_platform_tools(server, store, bridge, media, authorized_route, readable_route):
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

    @server.tool()
    async def send_message(
        text: Annotated[str, Field(max_length=16000)] = "",
        route_id: str | None = None,
        message_format: Literal["text", "markdown"] = "text",
        keyboard: dict | None = None,
        reply_to_source_message_id: str | None = None,
        delivery: Literal["passive", "proactive", "wakeup"] = "passive",
        embed: dict | None = None,
        ark: dict | None = None,
    ) -> dict:
        """Queue message content. Markdown/keyboards/quotes require QQ. Proactive delivery requires message.proactive. Reactions and recall use separate tools."""
        route, connection = await writable_route(route_id)
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
        payload = {"kind": "message", **options} if extended else None
        reply = await store.queue_reply(route, text or "[Card]", payload=payload)
        bridge.wake.set()
        return {"reply_id": reply["id"], "state": reply["state"]}

    @server.tool()
    async def send_media(
        media_kind: Literal["image", "audio", "video", "file"],
        media_type: Annotated[str, Field(min_length=1, max_length=128)],
        filename: Annotated[str, Field(min_length=1, max_length=255)],
        data_base64: Annotated[str, Field(max_length=4 * ((MAX_MEDIA_BYTES + 2) // 3))] | None = None,
        url: Annotated[str, Field(max_length=4096)] | None = None,
        caption: Annotated[str, Field(max_length=16000)] = "",
        route_id: str | None = None,
        delivery: Literal["passive", "proactive", "wakeup"] = "passive",
    ) -> dict:
        """Queue QQ media. Provide either public HTTPS URL or strict base64 (16 MiB max). Larger inline files use multipart upload. Guild binary uploads support images."""
        route, connection = await writable_route(route_id)
        if connection["kind"] != "qq":
            raise ValueError("Use send_image for other supported channels")
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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def set_message_reaction(
        source_message_id: Annotated[str, Field(min_length=1, max_length=1024)],
        emoji_id: Annotated[str, Field(min_length=1, max_length=20, pattern=r"^[0-9]+$")],
        add: bool = True,
        route_id: str | None = None,
        emoji_type: Literal[1, 2] = 1,
    ) -> dict:
        """Add/remove a reaction on an existing message. Independent of sending message content. QQ guild or optional OneBot extension only."""
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection or not (
            connection["kind"] == "onebots" or (connection["kind"] == "qq" and route["conversation_kind"] == "guild")
        ):
            raise ValueError("Message reactions are not supported by this channel")
        grant = reaction_authority(route, connection["kind"])
        await known_message(store, route, source_message_id)
        return await queue_action(
            route,
            "reaction",
            {"source_message_id": source_message_id, "emoji_id": emoji_id, "add": add, "emoji_type": emoji_type},
            grant,
        )

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def recall_message(
        message_id: Annotated[str, Field(min_length=1, max_length=1024)],
        route_id: str | None = None,
        hide_tip: bool = False,
    ) -> dict:
        """Recall a known QQ message. Requires message.recall for the bot's own messages or message.recall_members for received messages."""
        route, _ = await qq_route(route_id)
        ownership, _ = await known_message(store, route, message_id)
        grant = "message.recall" if ownership == "sent" else "message.recall_members"
        require_grant(route, grant)
        return await queue_action(route, "recall", {"message_id": message_id, "hide_tip": hide_tip}, grant)

    @server.tool()
    async def set_typing(seconds: Annotated[int, Field(ge=1, le=60)] = 30, route_id: str | None = None) -> dict:
        """Show QQ C2C typing state. This is a status action, not a text reply."""
        route, connection = await writable_route(route_id)
        if connection["kind"] != "qq" or route["conversation_kind"] != "private":
            raise ValueError("Typing state is supported in QQ C2C")
        return await queue_action(route, "typing", {"seconds": seconds}, "message.send")

    @server.tool()
    async def send_stream_message(
        text: Annotated[str, Field(min_length=1, max_length=16000)],
        index: Annotated[int, Field(ge=0, le=10000)] = 0,
        stream_handle: str | None = None,
        finished: bool = False,
        message_format: Literal["text", "markdown"] = "text",
        input_mode: Literal["append", "replace"] = "append",
        route_id: str | None = None,
    ) -> dict:
        """Queue an ordered QQ C2C stream fragment. Start at index zero; reuse the returned private handle and increment index. Poll get_channel_action before continuing after uncertainty."""
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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def list_channel_operations(route_id: str | None = None) -> dict:
        """Discover QQ resource operations and their exact argument schemas, scope, required route grants, and platform restrictions. Discovery itself grants no authority."""
        route, _ = await qq_route(route_id)
        return {"operations": [item.describe(route) for item in OPERATIONS.values() if applicable(item, route)]}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def query_channel_resource(
        operation: str, arguments: dict | None = None, route_id: str | None = None
    ) -> dict:
        """Read a native QQ resource using an operation from list_channel_operations. No arbitrary API paths or destinations are accepted."""
        route, connection = await qq_route(route_id)
        definition = OPERATIONS.get(operation)
        if definition is None or not definition.readonly:
            raise ValueError("Choose a read-only operation from list_channel_operations")
        value = await execute_operation(store, bridge.channels.qq(connection), route, definition, arguments or {})
        current = await authorized_route(route["id"])
        require_grant(current, definition.grant)
        if current != route:
            raise ValueError("Route authority changed during resource read")
        return {"result": safe_result(value)}

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True))
    async def perform_channel_action(
        operation: str, arguments: dict | None = None, route_id: str | None = None
    ) -> dict:
        """Queue a scoped QQ resource mutation. Discover operation schemas and required grants first. Sending message content, reacting, and recall use separate tools."""
        route, _ = await qq_route(route_id)
        definition = OPERATIONS.get(operation)
        if definition is None or definition.readonly or not applicable(definition, route):
            raise ValueError("Choose an applicable mutation from list_channel_operations")
        require_grant(route, definition.grant)
        definition.validate(arguments or {})
        return await queue_action(route, "resource:" + operation, arguments or {}, definition.grant)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
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

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
    async def read_attachment(
        source_message_id: str, attachment_index: Annotated[int, Field(ge=0, le=15)] = 0, route_id: str | None = None
    ) -> list[AudioContent | EmbeddedResource]:
        """Read authorized received audio/video/file bytes (16 MiB max). Uses the same current read grant as conversation history, with no arbitrary URL input."""
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
        data, mime = await media.read_blob(item["url"])
        current = await readable_route(route["id"])
        if await attachment(current) != item:
            raise ValueError("Attachment access changed during download")
        encoded = base64.b64encode(data).decode()
        if mime.startswith("audio/"):
            return [AudioContent(type="audio", data=encoded, mimeType=mime)]
        return [
            EmbeddedResource(
                type="resource",
                resource=BlobResourceContents(
                    uri=AnyUrl("channel-attachment:" + quote(source_message_id, safe="") + "/" + str(attachment_index)),
                    mimeType=mime,
                    blob=encoded,
                ),
            )
        ]
