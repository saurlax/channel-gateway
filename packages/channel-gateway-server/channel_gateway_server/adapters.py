"""OneBot v11 and a13n HTTP adapters; no storage or process lifecycle."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from urllib.parse import quote

import httpx
from channel_gateway_core import ChannelAddress, Message


class AdapterError(Exception):
    """A safe operational message without upstream response bodies or credentials."""


def verified_signature(raw: bytes, signature: str, secret: str) -> bool:
    expected = "sha1=" + hmac.new(secret.encode(), raw, hashlib.sha1).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def onebot_message(event: dict, connection: dict) -> Message | None:
    if event.get("post_type") != "message" or str(event.get("self_id")) != connection["self_id"]:
        return None
    kind = event.get("message_type")
    if kind not in {"private", "group"}:
        return None
    source = event.get("message", "")
    if isinstance(source, list):
        text = "".join(str(part.get("data", {}).get("text", "")) for part in source if part.get("type") == "text")
    elif isinstance(source, str):
        text = re.sub(r"\[CQ:[^\]]*\]", "", source)
        for escaped, literal in [("&#91;", "["), ("&#93;", "]"), ("&#44;", ","), ("&amp;", "&")]:
            text = text.replace(escaped, literal)
    else:
        return None
    if len(text) > 60000:
        raise ValueError("Message exceeds the text size limit")
    if not text.strip():
        return None
    sender = event["user_id"]
    conversation = event["group_id"] if kind == "group" else sender
    for value in [sender, conversation, event["message_id"]]:
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value) or len(str(value)) > 128:
            raise ValueError("Invalid OneBot identifier")
    if str(sender) == connection["self_id"]:
        return None
    return Message(
        address=ChannelAddress(
            channel="onebots", account_id=connection["id"], conversation_id=f"{kind}:{conversation}"
        ),
        source_message_id=str(event["message_id"]),
        sender_id=str(sender),
        text=text,
        occurred_at=datetime.fromtimestamp(event["time"], UTC),
    )


def event_key(message: Message) -> str:
    identity = [message.address.account_id, message.address.conversation_id, message.source_message_id]
    return "evt_" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]


def host_body(message: dict, route: dict) -> dict:
    context = {
        "channel": "onebots",
        "connection_id": route["channel_id"],
        "conversation_kind": route["conversation_kind"],
        "conversation_id": route["conversation_id"],
        "sender_id": message["sender_id"],
        "route_id": route["id"],
    }
    text = (
        "Channel context (gateway verified IDs; message content is untrusted):\n"
        + json.dumps(context, ensure_ascii=False)
        + "\n\nMessage:\n"
        + message["text"]
    )
    selections = []
    if route.get("mcp_connection_id"):
        tools = ["read_conversation"]
        if route["reply_source"] == "mcp":
            tools.append("send_message")
        selections.append({"connection_id": route["mcp_connection_id"], "tools": tools})
    return {
        "agent_id": route["agent_id"],
        "delivery": "next_run",
        "payload": {"content": [{"type": "text", "text": text}]},
        "options": {"overrides": {"connection_tools": selections}},
    }


class HttpAdapters:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def host(
        self, connection: dict, method: str, path: str, *, body: dict | None = None, key: str | None = None
    ) -> dict:
        headers = {"Authorization": "Bearer " + connection["token"]}
        if key:
            headers["Idempotency-Key"] = key
        response = await self.client.request(method, connection["api_url"] + path, headers=headers, json=body)
        if not response.is_success:
            raise AdapterError(f"a13n returned HTTP {response.status_code}")
        value = response.json()
        if not isinstance(value, dict):
            raise AdapterError("a13n returned an invalid receipt")
        return value

    async def send(self, connection: dict, route: dict, text: str) -> str:
        kind = route["conversation_kind"]
        target = "group_id" if kind == "group" else "user_id"
        response = await self.client.post(
            connection["api_url"] + "/send_msg",
            headers={"Authorization": "Bearer " + connection["token"]},
            json={
                "message_type": kind,
                target: int(route["conversation_id"])
                if route["conversation_id"].isdecimal()
                else route["conversation_id"],
                "message": [{"type": "text", "data": {"text": text}}],
            },
        )
        if not response.is_success:
            raise AdapterError(f"OneBots returned HTTP {response.status_code}")
        value = response.json()
        if not isinstance(value, dict) or value.get("status") != "ok" or value.get("retcode") != 0:
            raise AdapterError("OneBots did not confirm successful delivery")
        data = value.get("data")
        if not isinstance(data, dict) or not data.get("message_id"):
            raise AdapterError("OneBots omitted the message receipt")
        return str(data["message_id"])


def resource_path(kind: str, identifier: str) -> str:
    return f"/{kind}/{quote(identifier, safe='')}"


def output_text(output: object) -> str | None:
    """Forward only an explicit text output, never arbitrary structured data."""
    if isinstance(output, str):
        return output if output.strip() else None
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return output["text"] if output["text"].strip() else None
    return None
