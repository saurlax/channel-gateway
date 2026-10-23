"""QQ official Bot OpenAPI, Ed25519 callbacks, and Gateway protocol."""

import asyncio
import base64
import hashlib
import json
import re
import time
from contextlib import suppress
from datetime import datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from channel_gateway_core import Attachment, ChannelAddress, Message
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.exceptions import ConnectionClosed

from ..adapters import AdapterError
from ..media import decode_media, image_attachments, image_url
from ..storage import Store
from .common import OperationRejected, SendBlocked, identifier, response_object
from .ingress import ProjectionError, begin_event, event_type, ingest_parsed
from .qq_notifications import record_notification
from .qq_operations import safe_result

QQ_API = "https://api.sgroup.qq.com"
QQ_TOKEN = "https://bots.qq.com/app/getAppAccessToken"
QQ_INTENTS = sum(1 << bit for bit in (0, 10, 12, 25, 26, 27, 30))
QQ_ALL_INTENTS = sum(1 << bit for bit in (0, 1, 9, 10, 12, 18, 19, 25, 26, 27, 28, 29, 30))


def face_text(text: str) -> str:
    """Render face markers as text; their accompanying image attachments remain authoritative."""

    def replace(match):
        if match[1] == "6":
            return "[QQ sticker]"
        label = ""
        if len(match[3]) <= 4096:
            try:
                value = json.loads(base64.b64decode(match[3], validate=True))
                if isinstance(value, dict) and isinstance(value.get("text"), str):
                    label = value["text"][:256]
            except (ValueError, UnicodeError):
                pass
        return f"[QQ emoji: {label or match[2]}]"

    return re.sub(r'<faceType=(\d+),faceId="(\d+)",ext="([^"]*)">', replace, text)


def signing_key(secret: str) -> Ed25519PrivateKey:
    seed = secret.encode("ascii")
    if not seed:
        raise ValueError("QQ AppSecret is required")
    return Ed25519PrivateKey.from_private_bytes((seed * (32 // len(seed) + 1))[:32])


def verify_callback(raw: bytes, timestamp: str, signature: str, secret: str) -> bool:
    if not timestamp or not signature:
        return False
    try:
        signing_key(secret).public_key().verify(bytes.fromhex(signature), timestamp.encode() + raw)
    except (ValueError, InvalidSignature, UnicodeError):
        return False
    return True


def validation_response(event: dict, secret: str) -> dict:
    data = event["d"]
    token = data["plain_token"]
    timestamp = data["event_ts"]
    if (
        not isinstance(token, str)
        or not isinstance(timestamp, str)
        or not token
        or len(token) > 4096
        or len(timestamp) > 128
    ):
        raise ValueError("Invalid QQ callback validation")
    return {"plain_token": token, "signature": signing_key(secret).sign((timestamp + token).encode()).hex()}


def qq_message(event: dict, connection: dict) -> Message | None:
    if event.get("op") != 0:
        return None
    event_name = event.get("t")
    if event_name == "INTERACTION_CREATE":
        data = event.get("d") or {}
        if data.get("type") not in {11, 12}:
            return None
        resolved = (data.get("data") or {}).get("resolved") or {}
        scene = data.get("scene")
        kind = {"c2c": "private", "group": "group", "guild": "guild"}.get(scene if isinstance(scene, str) else "")
        sender = (
            data.get("user_openid")
            if kind == "private"
            else data.get("group_member_openid")
            if kind == "group"
            else resolved.get("user_id")
        )
        conversation = (
            sender if kind == "private" else data.get("group_openid") if kind == "group" else data.get("channel_id")
        )
        if not kind or not sender or not conversation or not data.get("timestamp"):
            return None
        event_id = identifier(data["id"], max_length=1024)
        return Message(
            address=ChannelAddress(
                channel="qq", account_id=connection["id"], conversation_id=f"{kind}:{identifier(conversation)}"
            ),
            source_message_id="interaction:" + hashlib.sha256(event_id.encode()).hexdigest(),
            sender_id=identifier(sender),
            text="[Interaction] " + json.dumps(safe_result(resolved), ensure_ascii=False),
            structured={"interaction_type": data["type"], "data": safe_result(resolved)},
            guild_id=identifier(data["guild_id"]) if kind == "guild" else None,
            reply_event_id=event_id,
            occurred_at=datetime.fromisoformat(data["timestamp"]),
        )
    kinds = {
        "C2C_MESSAGE_CREATE": "private",
        "GROUP_AT_MESSAGE_CREATE": "group",
        "GROUP_MESSAGE_CREATE": "group",
        "AT_MESSAGE_CREATE": "guild",
        "MESSAGE_CREATE": "guild",
        "DIRECT_MESSAGE_CREATE": "guild_dm",
    }
    if event_name not in kinds:
        return None
    kind = kinds[event_name]
    data = event["d"]
    author = data["author"]
    if author.get("bot"):
        return None
    sender = identifier(author["user_openid" if kind == "private" else "member_openid" if kind == "group" else "id"])
    if kind == "private":
        conversation = sender
    else:
        conversation = identifier(
            data["group_openid" if kind == "group" else "channel_id" if kind == "guild" else "guild_id"]
        )
    text = data.get("content") or ""
    if not isinstance(text, str):
        raise ValueError("Invalid QQ message text")
    text = face_text(re.sub(r"<@!?" + re.escape(str(connection.get("self_id") or "")) + r">", "", text)).strip()
    raw_attachments = list(data.get("attachments") or [])
    structured = {}
    if isinstance(data.get("ark_data"), dict):
        structured["card"] = safe_result(data["ark_data"])

    def nested(items, depth=0):
        if depth > 4 or not isinstance(items, list):
            raise ValueError("Invalid nested QQ message")
        output = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("content"), str):
                output.append(face_text(item["content"]))
            raw_attachments.extend(item.get("attachments") or [])
            if item.get("msg_elements"):
                output.extend(nested(item["msg_elements"], depth + 1))
        return output

    if data.get("msg_elements"):
        quoted = nested(data["msg_elements"])
        if quoted:
            text += "\n[Quoted content]\n" + "\n".join(quoted)
    images = image_attachments(
        [
            item.get("url")
            for item in raw_attachments
            if isinstance(item, dict) and str(item.get("content_type", "")).startswith("image/")
        ]
    )
    attachments = []
    for item in raw_attachments:
        if not isinstance(item, dict):
            raise ValueError("Invalid QQ attachment")
        mime = str(item.get("content_type") or "application/octet-stream")
        if mime.startswith("image/"):
            continue
        url = image_url(item.get("voice_wav_url") or item.get("url"))
        if url:
            attachments.append(
                Attachment(
                    kind="audio"
                    if mime.startswith("audio/") or mime == "voice"
                    else "video"
                    if mime.startswith("video/")
                    else "file",
                    url=url,
                    media_type=mime,
                    filename=item.get("filename") or "attachment",
                )
            )
    if not text.strip() and not images and not attachments and not structured:
        return None
    reference_id = None
    for value in (data.get("message_scene") or {}).get("ext", []):
        if isinstance(value, str) and value.startswith("msg_idx="):
            reference_id = value.partition("=")[2]
    guild_id = (
        data.get("guild_id")
        if kind == "guild"
        else data.get("src_guild_id") or data.get("source_guild_id")
        if kind == "guild_dm"
        else None
    )
    return Message(
        address=ChannelAddress(channel="qq", account_id=connection["id"], conversation_id=f"{kind}:{conversation}"),
        source_message_id=identifier(data["id"], max_length=1024),
        sender_id=sender,
        text=text if text.strip() else "[Image]" if images else "[Attachment]" if attachments else "[Card]",
        images=images,
        attachments=tuple(attachments),
        structured=structured,
        guild_id=identifier(guild_id) if guild_id else None,
        native_reference_id=reference_id,
        bot_mentioned=event_name in {"GROUP_AT_MESSAGE_CREATE", "AT_MESSAGE_CREATE"}
        if kind in {"group", "guild"}
        else None,
        occurred_at=datetime.fromisoformat(data["timestamp"]),
    )


class QQClient:
    def __init__(self, connection: dict, http: httpx.AsyncClient, media_http: httpx.AsyncClient | None = None):
        self.connection = connection
        self.http = http
        self.media_http = media_http
        self.token = ""
        self.expires = 0.0
        self.token_lock = asyncio.Lock()

    async def access_token(self) -> str:
        async with self.token_lock:
            if self.token and time.monotonic() < self.expires:
                return self.token
            value = response_object(
                await self.http.post(
                    QQ_TOKEN, json={"appId": self.connection["app_id"], "clientSecret": self.connection["app_secret"]}
                ),
                "QQ",
            )
            token = value.get("access_token")
            expires = float(value.get("expires_in", 7200))
            if not isinstance(token, str) or not token or not 0 < expires <= 86400:
                raise AdapterError("QQ returned an invalid access token")
            self.token = token
            self.expires = time.monotonic() + max(1, expires - min(60, expires / 3))
            return token

    async def request_value(self, method: str, path: str, body: dict | None = None, *, files=None) -> Any:
        token = await self.access_token()
        async with self.http.stream(
            method,
            QQ_API + path,
            json=body if files is None else None,
            files=files,
            data=body if files is not None else None,
            headers={"Authorization": "QQBot " + token, "Accept-Encoding": "identity"},
            timeout=15,
            follow_redirects=False,
        ) as response:
            if response.status_code == 401:
                self.expires = 0
            if 400 <= response.status_code < 500 and response.status_code != 408:
                raise OperationRejected(f"QQ rejected the operation (HTTP {response.status_code})")
            if not response.is_success:
                raise AdapterError(f"QQ returned HTTP {response.status_code}")
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                raise AdapterError("Compressed QQ API responses are not supported")
            content = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                if len(content) + len(chunk) > 4 * 1024 * 1024:
                    raise AdapterError("QQ returned an oversized response")
                content.extend(chunk)
            if not content:
                return {}
            value = json.loads(content)
        if not isinstance(value, (dict, list)):
            raise AdapterError("QQ returned an invalid receipt")
        if isinstance(value, dict) and value.get("code") not in {None, 0, "0"}:
            code = str(value.get("code"))
            raise OperationRejected(
                "QQ rejected the operation (code " + (code if code.isdecimal() and len(code) < 20 else "unknown") + ")"
            )
        return value

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        value = await self.request_value(method, path, body)
        if not isinstance(value, dict):
            raise AdapterError("QQ returned an invalid object receipt")
        return value

    async def upload_image(self, route: dict, payload: dict) -> str:
        resource = "users" if route["conversation_kind"] == "private" else "groups"
        suffix = "png" if payload["media_type"] == "image/png" else "jpg"
        value = await self.request(
            "POST",
            f"/v2/{resource}/{quote(route['conversation_id'], safe='')}/files",
            {
                "file_type": 1,
                "file_name": "image." + suffix,
                "file_data": payload["image_base64"],
                "srv_send_msg": False,
            },
        )
        info = value.get("file_info")
        if not isinstance(info, str) or not 0 < len(info) <= 16384:
            raise AdapterError("QQ omitted the image upload receipt")
        return info

    async def upload_media(self, route: dict, payload: dict, connection: dict, check_access) -> str:
        base = self.messages_path(route).removesuffix("/messages")
        kind = payload["media_kind"]
        body = {
            "file_type": {"image": 1, "video": 2, "audio": 3, "file": 4}[kind],
            "file_name": payload["filename"],
            "srv_send_msg": False,
        }
        if payload.get("url"):
            body["url"] = payload["url"]
        else:
            data = decode_media(payload["image_base64"])
            if len(data) <= 4 * 1024 * 1024:
                body["file_data"] = payload["image_base64"]
            else:
                prepared = await self.request(
                    "POST",
                    base + "/upload_prepare",
                    {
                        "file_type": body["file_type"],
                        "file_name": payload["filename"],
                        "file_size": str(len(data)),
                        "md5": hashlib.md5(data).hexdigest(),
                        "sha1": hashlib.sha1(data).hexdigest(),
                        "md5_10m": hashlib.md5(data[:10002432]).hexdigest(),
                    },
                )
                block = int(prepared["block_size"])
                parts = prepared["parts"]
                upload_id = identifier(prepared["upload_id"], max_length=1024)
                if (
                    not 0 < block <= 16 * 1024 * 1024
                    or not isinstance(parts, list)
                    or len(parts) != (len(data) + block - 1) // block
                ):
                    raise AdapterError("QQ returned an invalid multipart upload plan")
                for index, part in enumerate(parts):
                    url = image_url(part["presigned_url"])
                    if not url or part["index"] != index or self.media_http is None:
                        raise AdapterError("QQ returned an invalid upload destination")
                    chunk = data[index * block : (index + 1) * block]
                    await check_access(connection, route)
                    request = self.media_http.build_request("PUT", url, content=chunk)
                    request.headers.pop("authorization", None)
                    request.headers.pop("cookie", None)
                    response = await self.media_http.send(request, auth=None, follow_redirects=False)
                    if not response.is_success:
                        raise AdapterError("QQ multipart upload was not confirmed")
                    await check_access(connection, route)
                    await self.request(
                        "POST",
                        base + "/upload_part_finish",
                        {
                            "upload_id": upload_id,
                            "part_index": index,
                            "block_size": str(len(chunk)),
                            "md5": hashlib.md5(chunk).hexdigest(),
                        },
                    )
                body["upload_id"] = upload_id
        await check_access(connection, route)
        response = await self.request("POST", base + "/files", body)
        info = response.get("file_info")
        if not isinstance(info, str) or not 0 < len(info) <= 16384:
            raise AdapterError("QQ omitted the media upload receipt")
        return info

    @staticmethod
    def messages_path(route: dict) -> str:
        target = quote(route["conversation_id"], safe="")
        kind = route["conversation_kind"]
        return (
            f"/v2/users/{target}/messages"
            if kind == "private"
            else f"/v2/groups/{target}/messages"
            if kind == "group"
            else f"/channels/{target}/messages"
            if kind == "guild"
            else f"/dms/{target}/messages"
        )

    async def send(
        self,
        route: dict,
        text: str,
        context: dict,
        store: Store,
        file_info: str | None = None,
        payload: dict | None = None,
    ) -> str:
        payload = payload or {}
        delivery = payload.get("delivery", "passive")
        body: dict = {"content": text}
        if route["conversation_kind"] in {"private", "group"}:
            body["msg_type"] = 7 if file_info else 2 if payload.get("message_format") == "markdown" else 0
        if payload.get("message_format") == "markdown":
            body.pop("content", None)
            body["markdown"] = {"content": text}
        if file_info:
            body["media"] = {"file_info": file_info}
        for field in ("embed", "ark"):
            if payload.get(field):
                body[field] = payload[field]
        if payload.get("keyboard"):
            body["keyboard"] = payload["keyboard"]
        if payload.get("reference_id"):
            body["message_reference"] = {"message_id": payload["reference_id"]}
        if delivery == "passive":
            message_id = context["message_id"]
            body.update({"event_id": context["event_id"]} if context.get("event_id") else {"msg_id": message_id})
            body["msg_seq"] = await store.next_channel_sequence(self.connection["id"], message_id)
        elif delivery == "wakeup":
            body["is_wakeup"] = True
        files = None
        if route["conversation_kind"] in {"guild", "guild_dm"} and payload.get("kind") in {"image", "media"}:
            if not payload["media_type"].startswith("image/"):
                raise SendBlocked(
                    "Guild sending currently supports image attachments; QQ exposes no general guild file upload API"
                )
            if payload.get("url"):
                body["image"] = payload["url"]
            else:
                data = base64.b64decode(payload["image_base64"], validate=True)
                files = {
                    "file_image": (
                        payload.get("filename", "image.jpg" if payload["media_type"] == "image/jpeg" else "image.png"),
                        data,
                        payload["media_type"],
                    )
                }
                body = {
                    key: json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                    for key, value in body.items()
                }
        value = await self.request_value("POST", self.messages_path(route), body, files=files)
        message_id = identifier(value["id"], max_length=1024)
        reference_id = (value.get("ext_info") or {}).get("ref_idx")
        if reference_id:
            await store.set_channel_state(self.connection["id"], "reference:" + message_id, {"id": reference_id})
        return message_id


class QQGateway:
    def __init__(self, client: QQClient, store: Store, receive, status):
        self.client = client
        self.store = store
        self.receive = receive
        self.status = status
        self.session: dict = {}
        self.acknowledged = True

    async def run(self, socket) -> None:
        self.session = await self.store.channel_state(self.client.connection["id"], "qq_gateway")
        hello = await asyncio.wait_for(socket.recv(), 15)
        hello_id, packet = await begin_event(self.store, self.client.connection, hello)
        if hello_id is None:
            await socket.close()
            return
        if packet.get("op") != 10:
            raise AdapterError("QQ Gateway omitted Hello")
        interval = float(packet["d"]["heartbeat_interval"]) / 1000
        if not 0.1 <= interval <= 300:
            raise AdapterError("QQ Gateway heartbeat interval is invalid")
        if hello_id:
            await self.store.finish_channel_event(hello_id, "control", event_type=event_type(packet))
        token = "QQBot " + await self.client.access_token()
        if self.session.get("session_id") and self.session.get("seq") is not None:
            data = {"token": token, **self.session}
            await socket.send(json.dumps({"op": 6, "d": data}))
        else:
            await socket.send(
                json.dumps(
                    {
                        "op": 2,
                        "d": {
                            "token": token,
                            "intents": self.client.connection.get("qq_intents") or QQ_INTENTS,
                            "shard": [0, 1],
                        },
                    }
                )
            )
        self.acknowledged = True
        heartbeat = asyncio.create_task(self.heartbeat(socket, interval))
        try:
            async for raw in socket:
                await self.dispatch(raw, socket)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def heartbeat(self, socket, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            if not self.acknowledged:
                await socket.close()
                return
            self.acknowledged = False
            try:
                await socket.send(json.dumps({"op": 1, "d": self.session.get("seq")}))
            except (ConnectionClosed, OSError):
                await socket.close()
                return

    async def dispatch(self, raw: dict | str | bytes, socket) -> None:
        archive_id, event = await begin_event(self.store, self.client.connection, raw)
        if archive_id is None:
            await socket.close()
            return
        processed = False
        if event.get("op") == 11:
            self.acknowledged = True
        elif event.get("op") in {7, 9}:
            if event["op"] == 9 and not event.get("d"):
                self.session = {}
                await self.store.set_channel_state(self.client.connection["id"], "qq_gateway", {})
                self.client.expires = 0
            await socket.close()
        elif event.get("op") == 0:
            await record_notification(self.store, self.client.connection, event, self.client)
            if event.get("t") == "READY":
                self.session["session_id"] = identifier(event["d"]["session_id"])
                self.status("connected")
            elif event.get("t") == "RESUMED":
                self.status("connected")
            else:
                try:
                    await ingest_parsed(
                        self.store,
                        self.client.connection,
                        archive_id,
                        event,
                        self.receive,
                        lambda value: (qq_message(value, self.client.connection), None),
                    )
                except ProjectionError:
                    pass
                processed = True
            if not processed:
                await self.store.finish_channel_event(archive_id, "control", event_type=event_type(event))
            if isinstance(event.get("s"), int):
                self.session["seq"] = event["s"]
                # Advance resume position only after gateway ingestion returns.
                await self.store.set_channel_state(self.client.connection["id"], "qq_gateway", self.session)
            return
        await self.store.finish_channel_event(archive_id, "control", event_type=event_type(event))


def gateway_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "wss"
        or not parsed.hostname
        or not parsed.hostname.endswith(".qq.com")
        or parsed.username
        or parsed.password
    ):
        raise AdapterError("QQ returned an invalid Gateway URL")
    return value
