"""Shared bounded parsing and private protocol errors."""

import json
from datetime import datetime

import httpx
from channel_gateway_core import ChannelAddress, Message

from ..adapters import AdapterError

MAX_EVENT_BYTES = 1024 * 1024


class SendBlocked(AdapterError):
    """A reply cannot be issued; no channel request was attempted."""


def event_object(raw: str | bytes) -> dict:
    if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_EVENT_BYTES:
        raise ValueError("Event exceeds the size limit")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected an event object")
    return value


def response_object(response: httpx.Response, provider: str) -> dict:
    if not response.is_success:
        raise AdapterError(f"{provider} returned HTTP {response.status_code}")
    if len(response.content) > 4 * MAX_EVENT_BYTES:
        raise AdapterError(f"{provider} response exceeds the size limit")
    value = response.json()
    if not isinstance(value, dict):
        raise AdapterError(f"{provider} returned an invalid receipt")
    return value


def identifier(value: object, *, max_length: int = 128) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value) or len(str(value)) > max_length:
        raise ValueError("Invalid channel identifier")
    return str(value)


def text_message(
    connection: dict,
    kind: str,
    conversation: str,
    sender: str,
    message_id: object,
    text: object,
    when: datetime,
    *,
    message_id_limit: int = 128,
) -> Message | None:
    if not isinstance(text, str) or not text.strip():
        return None
    if len(text) > 60000:
        raise ValueError("Message exceeds the text size limit")
    return Message(
        address=ChannelAddress(
            channel=connection["kind"],
            account_id=connection["id"],
            conversation_id=f"{kind}:{identifier(conversation)}",
        ),
        source_message_id=identifier(message_id, max_length=message_id_limit),
        sender_id=identifier(sender),
        text=text,
        occurred_at=when,
    )
