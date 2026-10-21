"""Feishu/Lark signed encrypted callbacks and tenant-bot REST operations."""

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from urllib.parse import quote

from channel_gateway_core import Attachment, ChannelAddress, ImageAttachment, Message
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ...adapters import AdapterError
from ...media import decode_media
from ..common import OperationRejected, identifier
from .common import PlatformClient


def verified_packet(connection: dict, raw: bytes, timestamp: str, nonce: str, signature: str) -> dict:
    if (
        len(timestamp) > 12
        or not timestamp.isdecimal()
        or abs(time.time() - int(timestamp)) > 300
        or not 0 < len(nonce) <= 256
    ):
        raise ValueError("Invalid Lark callback timestamp")
    key = connection["encrypt_key"]
    digest = hashlib.sha256(timestamp.encode() + nonce.encode() + key.encode() + raw).hexdigest()
    if not hmac.compare_digest(digest, signature):
        raise ValueError("Invalid Lark callback signature")
    envelope = json.loads(raw)
    encrypted = base64.b64decode(envelope["encrypt"], validate=True)
    if len(encrypted) < 32:
        raise ValueError("Invalid encrypted callback")
    decryptor = Cipher(algorithms.AES(hashlib.sha256(key.encode()).digest()), modes.CBC(encrypted[:16])).decryptor()
    padded = decryptor.update(encrypted[16:]) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    packet = json.loads(unpadder.update(padded) + unpadder.finalize())
    header = packet.get("header", {})
    token = packet.get("token", header.get("token", ""))
    if not isinstance(token, str) or not hmac.compare_digest(token, connection["webhook_secret"]):
        raise ValueError("Invalid Lark verification token")
    if header and header.get("app_id") != connection["app_id"]:
        raise ValueError("Lark application does not match")
    return packet


def lark_message(packet: dict, connection: dict) -> Message | None:
    if packet.get("header", {}).get("event_type") != "im.message.receive_v1":
        return None
    event = packet["event"]
    sender = event["sender"]
    user = identifier(sender["sender_id"]["open_id"])
    if sender.get("sender_type") == "app" or user == connection["self_id"]:
        return None
    raw = event["message"]
    return project_message(raw, connection, user)


def project_message(raw: dict, connection: dict, user: str) -> Message | None:
    chat, mid = identifier(raw["chat_id"]), identifier(raw["message_id"])
    root = raw.get("root_id")
    kind = "thread" if root else "private" if raw.get("chat_type") == "p2p" else "group"
    conversation = chat + ":" + identifier(root) if root else chat
    content = json.loads(raw.get("content", raw.get("body", {}).get("content", "{}")))
    if not isinstance(content, dict):
        raise ValueError("Invalid Lark message content")
    message_type = raw.get("message_type", raw.get("msg_type"))
    text, images, attachments = "", [], []

    def asset(key: str) -> str:
        return "lark-resource:" + mid + ":" + identifier(key)

    if message_type == "text":
        text = content.get("text", "")
    elif message_type == "image":
        images.append(ImageAttachment(url=asset(content["image_key"])))
    elif message_type in {"file", "audio", "media"}:
        kind_media = {"audio": "audio", "media": "video", "file": "file"}[message_type]
        attachments.append(
            Attachment(
                kind="audio" if kind_media == "audio" else "video" if kind_media == "video" else "file",
                url=asset(content["file_key"]),
                filename=content.get("file_name")
                or (
                    "audio.opus"
                    if message_type == "audio"
                    else "video.mp4"
                    if message_type == "media"
                    else "attachment"
                ),
                media_type={"audio": "audio/ogg", "media": "video/mp4", "file": "application/octet-stream"}[
                    message_type
                ],
            )
        )
    elif message_type == "post":
        # Receive rich text as visible text/images; links remain text, never authority.
        post = content if "content" in content else next(iter(content.values()), {})
        lines = [post.get("title", "")]
        for row in post.get("content", []):
            words = []
            for part in row:
                if part.get("tag") == "img" and len(images) < 4:
                    images.append(ImageAttachment(url=asset(part["image_key"])))
                elif part.get("tag") in {"text", "a", "at", "code_block"}:
                    words.append(part.get("text", part.get("user_name", "")))
            lines.append("".join(words))
        text = "\n".join(lines).strip()
    else:
        return None
    if not isinstance(text, str):
        raise ValueError("Invalid Lark message text")
    if not text.strip() and not images and not attachments:
        return None
    return Message(
        address=ChannelAddress(channel="lark", account_id=connection["id"], conversation_id=kind + ":" + conversation),
        source_message_id=mid,
        sender_id=user,
        text=text,
        images=tuple(images),
        attachments=tuple(attachments),
        bot_mentioned=any(
            item.get("id", {}).get("open_id") == connection["self_id"] for item in raw.get("mentions", [])
        )
        if kind != "private"
        else None,
        occurred_at=datetime.fromtimestamp(int(raw["create_time"]) / 1000, UTC),
    )


class LarkClient(PlatformClient):
    provider = "Lark"

    def __init__(self, connection, http, media_http):
        super().__init__(connection, http, media_http)
        self.base = connection["api_url"] + "/open-apis"
        self.token = ""
        self.expires = 0.0
        self.lock = asyncio.Lock()

    async def headers(self):
        async with self.lock:
            if time.monotonic() >= self.expires:
                # Token requests cannot recurse through the authenticated request method.
                async with self.http.stream(
                    "POST",
                    self.base + "/auth/v3/tenant_access_token/internal",
                    json={"app_id": self.connection["app_id"], "app_secret": self.connection["app_secret"]},
                    headers={"Accept-Encoding": "identity"},
                    follow_redirects=False,
                    timeout=15,
                ) as response:
                    from .common import response_data

                    if 400 <= response.status_code < 500 and response.status_code != 408:
                        raise OperationRejected("Lark tenant authentication failed")
                    if not response.is_success:
                        raise AdapterError("Lark authentication was not confirmed")
                    value = json.loads(await response_data(response))
                    if not response.is_success or value.get("code") != 0:
                        raise OperationRejected("Lark tenant authentication failed")
                token = value.get("tenant_access_token")
                expiry = value.get("expire")
                if (
                    not isinstance(token, str)
                    or not 0 < len(token) <= 4096
                    or not isinstance(expiry, int)
                    or not 0 < expiry <= 86400
                ):
                    raise AdapterError("Lark returned an invalid tenant token")
                self.token, self.expires = token, time.monotonic() + max(1, expiry - 60)
        return {"Authorization": "Bearer " + self.token}

    async def request(self, method, path, **kwargs):
        value = await super().request(method, path, **kwargs)
        if not isinstance(value, dict) or value.get("code") != 0:
            raise OperationRejected("Lark rejected the operation; check tenant permissions")
        return value.get("data", {})

    async def ensure_identity(self):
        async with self.identity_lock:
            if not self.identity_verified:
                value = await super().request("GET", "/bot/v3/info")
                if (
                    not isinstance(value, dict)
                    or value.get("code") != 0
                    or value.get("bot", {}).get("open_id") != self.connection["self_id"]
                ):
                    raise OperationRejected("Lark credentials do not match the configured bot identity")
                self.identity_verified = True

    @staticmethod
    def target(route):
        if route["conversation_kind"] == "thread":
            chat, root = route["conversation_id"].split(":", 1)
            return identifier(chat), identifier(root)
        return route["conversation_id"], None

    async def thread(self, route):
        chat, root = self.target(route)
        if not root:
            return chat, None, None
        value = await self.request("GET", "/im/v1/messages/" + quote(root, safe=""))
        item = next((item for item in value.get("items", []) if item.get("message_id") == root), None)
        if not item or item.get("chat_id") != chat or not item.get("thread_id"):
            raise ValueError("Lark thread root does not belong to the authorized chat")
        return chat, root, identifier(item["thread_id"])

    async def send(self, route, text, payload, check, effect):
        chat, root, _ = await self.thread(route)
        message_type, content = "text", {"text": text}
        if payload.get("kind") in {"image", "media"}:
            data = decode_media(payload["image_base64"])
            kind = payload.get("media_kind", "image")
            await check()
            if kind == "image":
                value = await self.request(
                    "POST",
                    "/im/v1/images",
                    data={"image_type": "message"},
                    files={"image": (payload["filename"], data, payload["media_type"])},
                )
                message_type, content = "image", {"image_key": identifier(value["image_key"])}
            else:
                file_type = "opus" if kind == "audio" else "mp4" if kind == "video" else "stream"
                value = await self.request(
                    "POST",
                    "/im/v1/files",
                    data={"file_type": file_type, "file_name": payload["filename"]},
                    files={"file": (payload["filename"], data, payload["media_type"])},
                )
                message_type, content = (
                    {"audio": "audio", "video": "media", "file": "file"}[kind],
                    {"file_key": identifier(value["file_key"])},
                )
            await effect("uploaded", "asset_" + hashlib.sha256(next(iter(content.values())).encode()).hexdigest()[:24])
        await check()
        reference = root or payload.get("reference_id")
        body = {"msg_type": message_type, "content": json.dumps(content, ensure_ascii=False)}
        if reference:
            value = await self.request(
                "POST",
                "/im/v1/messages/" + quote(reference, safe="") + "/reply",
                body={**body, "reply_in_thread": bool(root)},
            )
        else:
            value = await self.request(
                "POST", "/im/v1/messages", params={"receive_id_type": "chat_id"}, body={**body, "receive_id": chat}
            )
        return identifier(value["message_id"])

    async def history(self, route, limit, cursor):
        chat, root, thread_id = await self.thread(route)
        params = {
            "container_id_type": "thread" if root else "chat",
            "container_id": thread_id or chat,
            "page_size": limit,
            "sort_type": "ByCreateTimeDesc",
        }
        if cursor:
            params["page_token"] = cursor
        value = await self.request("GET", "/im/v1/messages", params=params)
        messages = []
        for item in value.get("items", []):
            if item.get("chat_id") != chat or item.get("deleted"):
                continue
            if not root and item.get("root_id"):
                continue
            if root and (item.get("root_id") or item.get("message_id")) != root:
                continue
            sender = item.get("sender", {})
            if sender.get("id_type") != "open_id":
                continue
            message = project_message(item, self.connection, identifier(sender["id"]))
            if message:
                messages.append(message.model_dump(mode="json"))
        return messages, value.get("page_token") if value.get("has_more") else None

    async def read_asset(self, item, *, image):
        # Native keys are resolved on this account's fixed API host, never as URLs.
        scheme, mid, key = item["url"].split(":", 2)
        if scheme != "lark-resource":
            raise ValueError("Invalid Lark resource handle")
        url = (
            self.base
            + "/im/v1/messages/"
            + quote(identifier(mid), safe="")
            + "/resources/"
            + quote(identifier(key), safe="")
            + "?type="
            + ("image" if image else "file")
        )
        return await self.download(url, headers=await self.headers(), image=image)

    async def reaction(self, route, args, check):
        mid = quote(args["source_message_id"], safe="")
        emoji = args["emoji"]
        await check()
        if emoji["kind"] != "platform" or emoji.get("type") is not None:
            raise ValueError("Lark reactions require a platform emoji_type value")
        path = "/im/v1/messages/" + mid + "/reactions"
        if args["add"]:
            return await self.request("POST", path, body={"reaction_type": {"emoji_type": emoji["value"]}})
        # Delete only this app's reaction, never another user's reaction ID.
        token = None
        for _ in range(10):
            value = await self.request(
                "GET", path, params={"page_size": 100, **({"page_token": token} if token else {})}
            )
            for item in value.get("items", []):
                if (
                    item.get("operator", {}).get("operator_type") == "app"
                    and item.get("operator", {}).get("operator_id") == self.connection["app_id"]
                    and item.get("emoji_type", item.get("reaction_type", {}).get("emoji_type")) == emoji["value"]
                ):
                    await check()
                    return await self.request("DELETE", path + "/" + quote(identifier(item["reaction_id"]), safe=""))
            if not value.get("has_more"):
                return {}
            token = value["page_token"]
        raise ValueError("Reaction list exceeds the bounded lookup limit")

    async def recall(self, route, message_id):
        return await self.request("DELETE", "/im/v1/messages/" + quote(message_id, safe=""))
