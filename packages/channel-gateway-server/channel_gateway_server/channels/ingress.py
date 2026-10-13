"""Route-independent retention of authenticated inbound channel packets."""

import json
from collections.abc import Awaitable, Callable

from channel_gateway_core import Message

from ..adapters import event_key
from ..storage import Store
from .common import MAX_EVENT_BYTES, event_object

type ProjectEvent = Callable[[dict], tuple[Message | None, dict | None]]
type ReceiveMessage = Callable[[dict, Message | None, dict | None], Awaitable[dict]]
PROJECTION_ERRORS = (ValueError, TypeError, KeyError, AttributeError, OverflowError)


class ProjectionError(ValueError):
    """Malformed input was archived; storage/receiver failures are separate."""


def event_type(event: dict) -> str:
    value = event.get("t")
    if isinstance(value, str):
        return value[:128]
    if event.get("post_type"):
        return ":".join(
            str(event[k]) for k in ("post_type", "message_type", "notice_type", "meta_event_type") if k in event
        )[:128]
    if "op" in event:
        return "opcode:" + str(event["op"])[:32]
    if "message_type" in event:
        return "message:" + str(event["message_type"])[:32]
    return "unknown"


async def begin_event(store: Store, connection: dict, raw: object) -> tuple[str | None, dict]:
    payload = raw if isinstance(raw, bytes) else raw.encode() if isinstance(raw, str) else json.dumps(raw).encode()
    if len(payload) > MAX_EVENT_BYTES:
        raise ProjectionError("Event exceeds the size limit")
    identifier = await store.archive_channel_event(connection, payload)
    try:
        event = event_object(payload)
    except PROJECTION_ERRORS:
        if identifier:
            await store.finish_channel_event(identifier, "invalid", error="Invalid channel event object")
        raise ProjectionError("Invalid channel event object") from None
    if identifier:
        await store.finish_channel_event(identifier, "received", event_type=event_type(event))
    return identifier, event


async def ingest_parsed(
    store: Store,
    connection: dict,
    identifier: str | None,
    event: dict,
    receive: ReceiveMessage,
    project: ProjectEvent,
) -> dict:
    if identifier is None:
        return {"state": "blocked"}
    try:
        message, context = project(event)
    except PROJECTION_ERRORS:
        await store.finish_channel_event(
            identifier, "invalid", event_type=event_type(event), error="Invalid channel message projection"
        )
        raise ProjectionError("Invalid channel message projection") from None
    result = await receive(connection, message, context)
    await store.finish_channel_event(
        identifier,
        result["state"],
        event_type=event_type(event),
        message_event_id=event_key(message) if message else None,
    )
    return result


async def ingest_event(
    store: Store,
    connection: dict,
    raw: object,
    receive: ReceiveMessage,
    project: ProjectEvent,
) -> dict:
    identifier, event = await begin_event(store, connection, raw)
    return await ingest_parsed(store, connection, identifier, event, receive, project)
