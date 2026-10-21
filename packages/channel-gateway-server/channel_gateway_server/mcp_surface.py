"""Five stable, platform-independent MCP entry points."""

import json
from typing import Annotated, Literal

from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .channels.actions import proactive_authority, tool_message_allowed
from .channels.manager import capabilities
from .channels.platforms.common import PLATFORMS
from .channels.qq_operations import OPERATIONS, applicable, check_json
from .media import MAX_MEDIA_BYTES

READS = {
    "messages": "read_conversation",
    "image": "read_image",
    "attachment": "read_attachment",
    "events": "read_channel_events",
}
ACTIONS = {"reaction": "set_message_reaction", "recall": "recall_message", "typing": "set_typing"}


class TextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text"] = "text"
    text: str = Field(max_length=16000)
    format: Literal["text", "markdown"] = "text"


class MediaPart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["image", "audio", "video", "file"]
    media_type: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    data_base64: str | None = Field(default=None, min_length=1, max_length=4 * ((MAX_MEDIA_BYTES + 2) // 3))
    url: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def source(self):
        if bool(self.data_base64) == bool(self.url):
            raise ValueError("Choose exactly one media URL or base64 data")
        if any(c in self.filename for c in ("/", "\\", "\r", "\n", "\0")):
            raise ValueError("Use a plain file name")
        return self


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int = Field(default=0, ge=0, le=10000)
    stream_handle: str | None = Field(default=None, min_length=1, max_length=1024)
    finished: bool = False
    input_mode: Literal["append", "replace"] = "append"


class QQMessageOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyboard: dict | None = None
    embed: dict | None = None
    ark: dict | None = None


type ContentPart = Annotated[TextPart | MediaPart, Field(discriminator="type")]


def receipt(value: dict, kind: str) -> dict:
    """Use the existing durable ledger ID for both messages and actions."""
    return {"operation_id": value.get("reply_id", value.get("action_id")), "kind": kind, **value}


def native_arguments(arguments: dict | None) -> dict:
    arguments = arguments or {}
    if arguments.keys() - {"operation", "parameters"}:
        raise ValueError("Native operations accept operation and parameters only")
    operation = arguments.get("operation")
    if not isinstance(operation, str) or not operation.startswith("qq."):
        raise ValueError("Choose a registered namespaced operation from discover(resource='operations')")
    return {"operation": operation.removeprefix("qq."), "arguments": arguments.get("parameters", {})}


def install_tools(server, operations, store, authorized_route, readable_route):
    async def view(route_id):
        try:
            return await authorized_route(route_id)
        except ValueError:
            return await readable_route(route_id)

    async def describe(route):
        connection = await store.connection(route["channel_id"])
        if not connection:
            raise ValueError("Channel is unavailable")
        kind, scene = connection["kind"], route["conversation_kind"]
        adapter = capabilities(connection)
        platform = kind in PLATFORMS
        writable = not route["id"].startswith("read_")
        grants = route.get("action_grants", []) if writable else []
        can_read = route.get("can_read", route.get("read_channel_ids") is None)
        # An explicit account read grant can also cover a source destination.
        if not can_read:
            try:
                await readable_route(route["id"])
                can_read = True
            except ValueError:
                pass
        can_send = writable and tool_message_allowed(route, connection)
        return {
            "route_id": route["id"],
            "platform": kind,
            "conversation_kind": scene,
            "adapter": capabilities(connection),
            "read": {
                "messages": {"supported": True, "authorized": can_read, "source": "gateway", "complete": False},
                "platform_history": {
                    "supported": platform,
                    "authorized": "history.read" in grants,
                    "source": "platform",
                    "scope": "exact_route",
                },
                "image": {"supported": kind in {"qq", "onebots", *PLATFORMS}, "authorized": can_read},
                "attachment": {"supported": kind == "qq" or platform, "authorized": can_read},
                "events": {"supported": kind == "qq", "authorized": "resource.read" in grants},
            },
            "send": {
                "authorized": can_send,
                "content_types": (
                    ["text", "image"] + (["audio", "video", "file"] if scene in {"private", "group"} else [])
                )
                if kind == "qq"
                else ["text", "image", "audio", "video", "file"]
                if platform
                else ["text", "image"]
                if kind == "onebots"
                else ["text"],
                "default_delivery": "proactive" if can_send and proactive_authority(route, connection) else "passive",
                "stream": {
                    "supported": kind == "qq" and scene == "private",
                    "authorized": can_send,
                },
                "max_text_characters": adapter.get("max_text_characters", 16000),
                "max_media_parts": 1,
                "max_media_bytes": adapter.get("max_media_bytes", MAX_MEDIA_BYTES if kind == "qq" else 4 * 1024 * 1024),
                "native_options_schema": QQMessageOptions.model_json_schema() if kind == "qq" else None,
            },
            "actions": {
                "reaction": {
                    "supported": platform or kind == "onebots" or (kind == "qq" and scene == "guild"),
                    "authorized": "message.react" in grants or (kind == "onebots" and "message.react.onebot" in grants),
                    "restriction": "platform emoji representation required"
                    if platform
                    else "optional OneBot extension"
                    if kind == "onebots"
                    else "QQ guild only",
                },
                "recall": {
                    "supported": kind == "qq" or platform,
                    "grants": [grant for grant in grants if grant in {"message.recall", "message.recall_members"}],
                },
                "typing": {
                    "supported": (kind == "qq" and scene == "private") or adapter.get("typing", False),
                    "authorized": can_send,
                },
            },
            "upstream_permissions": "not_verified; platform permissions, reply windows and rate limits still apply",
        }

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def discover(
        resource: Literal["conversations", "capabilities", "operations"] = "conversations",
        route_id: str | None = None,
    ) -> dict:
        """List authorized conversations, or inspect one conversation's capabilities and validated operation schemas. Tool names never depend on platform or conversation count."""
        if resource == "conversations":
            if route_id is not None:
                raise ValueError("Conversation listing does not accept route_id")
            return await operations.call("list_conversations", {}, None)
        route = await view(route_id)
        description = await describe(route)
        if resource == "capabilities":
            return description
        native = []
        if description["platform"] == "qq" and not route["id"].startswith("read_"):
            native = [
                {**item.describe(route), "name": "qq." + item.name}
                for item in OPERATIONS.values()
                if applicable(item, route)
            ]
        return {
            "route_id": route["id"],
            "read": {name: operations.schema(handler) for name, handler in READS.items()},
            "platform_read": {
                "messages": operations.schema("read_platform_messages"),
                "asset": operations.schema("read_platform_asset"),
            }
            if description["platform"] in PLATFORMS
            else {},
            "actions": {name: operations.schema(handler) for name, handler in ACTIONS.items()},
            "native": native,
            "capabilities": description,
        }

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False), structured_output=False)
    async def read(
        resource: Literal["messages", "image", "attachment", "events", "native"],
        arguments: dict | None = None,
        route_id: str | None = None,
        source: Literal["gateway", "platform"] | None = None,
    ):
        """Read history, a received image/attachment, sanitized events, or a registered native resource. Discover argument schemas first. Image/audio/resource bytes retain native MCP content types; parse unsupported formats in the Host sandbox. Messages default to gateway history, never complete native history."""
        if resource == "native":
            if source == "gateway":
                raise ValueError("Native resources use source='platform'")
            return {
                "source": "platform",
                **await operations.call("query_channel_resource", native_arguments(arguments), route_id),
            }
        if source == "platform" and resource in {"messages", "image", "attachment"}:
            if resource == "messages":
                return await operations.call("read_platform_messages", arguments, route_id)
            args = dict(arguments or {})
            allowed = {"source_message_id", "image_index" if resource == "image" else "attachment_index"}
            if args.keys() - allowed:
                raise ValueError("Choose an asset using source_message_id and its index")
            index = args.pop("image_index" if resource == "image" else "attachment_index", 0)
            return await operations.call(
                "read_platform_asset", {**args, "index": index, "image": resource == "image"}, route_id
            )
        if source == "platform":
            raise ValueError(
                "Native platform history is not implemented; gateway reads cover received and confirmed sent records only"
            )
        return await operations.call(READS[resource], arguments, route_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False))
    async def send_message(
        content: Annotated[list[ContentPart], Field(max_length=2)],
        route_id: str | None = None,
        delivery: Literal["passive", "proactive", "wakeup"] | None = None,
        reply_to: Annotated[str, Field(min_length=1, max_length=1024)] | None = None,
        stream: StreamOptions | None = None,
        native: dict | None = None,
    ) -> dict:
        """Queue content: text plus at most one media part. Text parts have text/format; media parts have type, media_type, filename and exactly one of data_base64/url. QQ cards/keyboards use native={'qq': {...}}. Stream fragments use stream options. Poll get_operation; queued is not delivery. Reactions use execute_action."""
        check_json(native or {})
        if native is not None and (set(native) != {"qq"} or not isinstance(native["qq"], dict)):
            raise ValueError("Choose registered native options from discover(resource='capabilities')")
        options = QQMessageOptions.model_validate((native or {}).get("qq", {})).model_dump()
        if len(json.dumps(options)) > 65536:
            raise ValueError("Message options exceed the size limit")
        texts = [part for part in content if isinstance(part, TextPart)]
        media = [part for part in content if isinstance(part, MediaPart)]
        if len(texts) > 1 or len(media) > 1:
            raise ValueError("This adapter supports one text part and at most one media part")
        text = texts[0].text if texts else ""
        message_format = texts[0].format if texts else "text"
        route = await authorized_route(route_id)
        connection = await store.connection(route["channel_id"])
        if not connection:
            raise ValueError("Channel is unavailable")
        if connection["kind"] in PLATFORMS:
            if native is not None or stream is not None:
                raise ValueError("This adapter does not yet support native message options or streaming")
            return receipt(
                await operations.call(
                    "send_platform_message",
                    {"content": [part.model_dump() for part in content], "delivery": delivery, "reply_to": reply_to},
                    route_id,
                ),
                "message",
            )
        if native is not None and connection["kind"] != "qq":
            raise ValueError("QQ native options require official QQ")
        if media:
            if stream or native is not None or reply_to is not None or message_format != "text":
                raise ValueError("This adapter cannot combine media with stream, native options, a quote, or Markdown")
            part = media[0]
            if part.type == "image" and part.media_type not in capabilities(connection)["image_send_types"]:
                raise ValueError("Image format is not supported by this channel")
            if connection["kind"] == "qq":
                result = await operations.call(
                    "send_media",
                    {
                        "media_kind": part.type,
                        "media_type": part.media_type,
                        "filename": part.filename,
                        "data_base64": part.data_base64,
                        "url": part.url,
                        "caption": text,
                        "delivery": delivery,
                    },
                    route_id,
                )
            elif connection["kind"] == "onebots" and part.type == "image" and part.data_base64:
                result = await operations.call(
                    "send_image",
                    {
                        "image_base64": part.data_base64,
                        "media_type": part.media_type,
                        "caption": text,
                        "delivery": delivery,
                    },
                    route_id,
                )
            else:
                raise ValueError("Media content or source is not supported by this channel")
            return receipt(result, "message")
        if stream is not None:
            if delivery is not None or reply_to is not None or native is not None:
                raise ValueError(
                    "Stream fragments cannot override delivery, quote a message, or include native options"
                )
            result = await operations.call(
                "send_stream_message",
                {
                    **stream.model_dump(),
                    "text": text,
                    "message_format": message_format,
                },
                route_id,
            )
            return receipt(result, "stream")
        result = await operations.call(
            "send_message",
            {
                "text": text,
                "message_format": message_format,
                "delivery": delivery,
                "reply_to_source_message_id": reply_to,
                **options,
            },
            route_id,
        )
        return receipt(result, "message")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False))
    async def execute_action(
        action: Literal["reaction", "recall", "typing", "native"],
        arguments: dict | None = None,
        route_id: str | None = None,
    ) -> dict:
        """Queue a non-message action under its own grants. Use discover(resource='operations') for exact schemas. Native actions use {'operation':'qq.<name>','parameters':{...}}; arbitrary HTTP paths and destinations are refused."""
        result = await operations.call(
            "perform_channel_action" if action == "native" else ACTIONS[action],
            native_arguments(arguments) if action == "native" else arguments,
            route_id,
        )
        return receipt(result, "action")

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
    async def get_operation(
        operation_id: Annotated[str, Field(min_length=1, max_length=128)],
        route_id: str | None = None,
    ) -> dict:
        """Read a durable message, stream or action result in this authorized route. Unknown outcomes must be verified before repeating. Sent/succeeded means platform acceptance, not recipient reading."""
        route = await authorized_route(route_id)
        if operation_id.startswith("action_"):
            result = await operations.call("get_channel_action", {"action_id": operation_id}, route_id)
            return receipt(result, "stream" if result["operation"] == "stream_message" else "action")
        rows = await store.rows(
            "SELECT id,route_id,state,error,message_id FROM replies WHERE id=? AND route_id=?",
            (operation_id, route["id"]),
        )
        if not rows:
            raise ValueError("Choose an operation belonging to this authorized route")
        row = rows[0]
        connection = await store.connection(route["channel_id"])
        allowed = connection and tool_message_allowed(route, connection)
        payload = await store.reply_payload(operation_id)
        if payload and payload.get("delivery") in {"proactive", "wakeup"}:
            allowed = allowed and "message.proactive" in route.get("action_grants", [])
        return {
            "operation_id": row["id"],
            "kind": "message",
            "state": row["state"],
            "error": row["error"],
            "result": {"message_id": row["message_id"]} if allowed and row["state"] == "sent" else None,
            "effects": await store.operation_effects(row["id"]) if allowed else [],
        }
