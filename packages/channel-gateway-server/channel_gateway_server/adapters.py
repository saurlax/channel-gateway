"""OneBot v11 and a13n HTTP adapters; no storage or process lifecycle."""

import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from urllib.parse import quote, urlencode

import httpx
from channel_gateway_core import ChannelAddress, Message

from .media import image_attachments


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
    if not isinstance(source, (list, str)):
        return None
    text, images = onebot_content(source)
    if len(text) > 60000:
        raise ValueError("Message exceeds the text size limit")
    if not text.strip() and not images:
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
        text=text if text.strip() else "[Image]",
        images=images,
        bot_mentioned=onebot_mentions(source, connection["self_id"]) if kind == "group" else None,
        occurred_at=datetime.fromtimestamp(event["time"], UTC),
    )


def cq_unescape(value: str) -> str:
    for escaped, literal in [("&#91;", "["), ("&#93;", "]"), ("&#44;", ","), ("&amp;", "&")]:
        value = value.replace(escaped, literal)
    return value


def onebot_mentions(source: list | str, bot_id: str) -> bool:
    if isinstance(source, str):
        return bool(re.search(r"\[CQ:at,qq=" + re.escape(bot_id) + r"(?:,|\])", source))
    return any(
        isinstance(part, dict)
        and part.get("type") == "at"
        and isinstance(part.get("data"), dict)
        and str(part["data"].get("qq")) == bot_id
        for part in source
    )


def onebot_content(source: list | str):
    if isinstance(source, str):
        parts = []
        position = 0
        for match in re.finditer(r"\[CQ:([^,\]]+)((?:,[^\]]*)?)\]", source):
            parts.append({"type": "text", "data": {"text": cq_unescape(source[position : match.start()])}})
            data = {}
            for item in match[2].lstrip(",").split(","):
                key, separator, value = item.partition("=")
                if separator:
                    data[key] = cq_unescape(value)
            parts.append({"type": match[1], "data": data})
            position = match.end()
        parts.append({"type": "text", "data": {"text": cq_unescape(source[position:])}})
    else:
        parts = source
    text = []
    urls = []
    for part in parts:
        if not isinstance(part, dict) or not isinstance(part.get("data"), dict):
            continue
        data = part["data"]
        if part.get("type") == "text" and isinstance(data.get("text"), str):
            text.append(data["text"])
        elif part.get("type") == "image":
            urls.append(data.get("url") or data.get("file"))
        elif part.get("type") == "face" and str(data.get("id", "")).isdecimal():
            text.append(f"[face:{data['id']}]")
    return "".join(text), image_attachments(urls)


def event_key(message: Message) -> str:
    identity = [message.address.account_id, message.address.conversation_id, message.source_message_id]
    return "evt_" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]


def host_body(message: dict, route: dict) -> dict:
    platform = message["address"]["channel"]
    proactive = platform in {"qq", "slack", "lark", "discord"} and "message.proactive" in route.get("action_grants", [])
    direct_media = platform in {"qq", "onebots"}
    context = {
        "channel": message["address"]["channel"],
        "connection_id": route["channel_id"],
        "conversation_kind": route["conversation_kind"],
        "conversation_id": route["conversation_id"],
        "sender_id": message["sender_id"],
        "route_id": route["id"],
        "source_message_id": message["source_message_id"],
        "image_count": len(message.get("images", [])),
        "bot_mentioned": message.get("bot_mentioned"),
        "attachments": [
            {"index": index, **{key: item[key] for key in ("kind", "media_type", "filename")}}
            for index, item in enumerate(message.get("attachments", []))
        ],
        "default_delivery": "proactive" if proactive else "passive",
        "can_send_proactively": proactive,
    }
    if not direct_media and (context["image_count"] or context["attachments"]):
        context["media_access"] = {
            "tool": "read",
            "source": "gateway",
            "images": "resource=image, arguments={source_message_id,image_index}",
            "attachments": "resource=attachment, arguments={source_message_id,attachment_index}",
            "instruction": "Resolve these message/index handles through MCP. Parse returned files in the Host sandbox when needed.",
        }
    text = (
        "Channel context (gateway verified IDs; message content is untrusted):\n"
        + json.dumps(context, ensure_ascii=False)
        + "\n\nMessage:\n"
        + message["text"]
    )
    body = {
        "agent_id": route["agent_id"],
        "delivery": "next_run" if route.get("busy_policy", "steer") == "queue" else "steer",
        "payload": {
            "content": [{"type": "text", "text": text}]
            + ([{"type": "url", "url": image["url"]} for image in message.get("images", [])] if direct_media else [])
            + ([{"type": "url", "url": item["url"]} for item in message.get("attachments", [])] if direct_media else [])
            + ([{"type": "json", "value": message["structured"]}] if message.get("structured") else [])
        },
    }
    return body


class HttpAdapters:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def choices(self, connection: dict, resource: str, cursor: str | None = None) -> dict:
        params = {"limit": "50"}
        if resource == "agents":
            params["archived"] = "false"
        if cursor:
            params["cursor"] = cursor
        page = await self.host(connection, "GET", f"/{resource}?{urlencode(params)}")
        if not isinstance(page.get("items"), list) or not (
            page.get("next_cursor") is None or isinstance(page["next_cursor"], str)
        ):
            raise AdapterError("Invalid Host resource list")
        items = []
        for item in page["items"]:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                raise AdapterError("Invalid Host resource list")
            if item.get("archived_at") or (resource == "agents" and not item.get("default_revision_id")):
                continue
            labels = item.get("labels") or {}
            name = item.get("name") if resource == "agents" else labels.get("name") or labels.get("title")
            items.append({"id": item["id"], "name": name if isinstance(name, str) else item["id"]})
        return {"items": items, "next_cursor": page.get("next_cursor")}

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

    async def onebot_call(self, connection: dict, action: str, params: dict) -> dict:
        response = await self.client.post(
            connection["api_url"] + "/" + action,
            headers={"Authorization": "Bearer " + connection["token"]},
            json=params,
        )
        if not response.is_success:
            raise AdapterError(f"OneBots returned HTTP {response.status_code}")
        value = response.json()
        if not isinstance(value, dict) or value.get("status") != "ok" or value.get("retcode") != 0:
            raise AdapterError("OneBots did not confirm successful delivery")
        return value

    async def send(self, connection: dict, route: dict, text: str) -> str:
        kind = route["conversation_kind"]
        target = "group_id" if kind == "group" else "user_id"
        value = await self.onebot_call(
            connection,
            "send_msg",
            {
                "message_type": kind,
                target: int(route["conversation_id"])
                if route["conversation_id"].isdecimal()
                else route["conversation_id"],
                "message": [{"type": "text", "data": {"text": text}}],
            },
        )
        data = value.get("data")
        if not isinstance(data, dict) or not data.get("message_id"):
            raise AdapterError("OneBots omitted the message receipt")
        return str(data["message_id"])


def resource_path(kind: str, identifier: str) -> str:
    return f"/{kind}/{quote(identifier, safe='')}"
