"""QQ official Bot OpenAPI, Ed25519 callbacks, and Gateway protocol."""

import asyncio
import json
import re
import time
from contextlib import suppress
from datetime import datetime
from urllib.parse import quote, urlsplit

import httpx
from channel_gateway_core import Message
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.exceptions import ConnectionClosed

from ..adapters import AdapterError
from ..storage import Store
from .common import identifier, response_object, text_message
from .ingress import ProjectionError, begin_event, event_type, ingest_parsed

QQ_API = "https://api.sgroup.qq.com"
QQ_TOKEN = "https://bots.qq.com/app/getAppAccessToken"


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
    event_type = event.get("t")
    if event_type not in {"C2C_MESSAGE_CREATE", "GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"}:
        return None
    data = event["d"]
    author = data["author"]
    if author.get("bot"):
        return None
    private = event_type == "C2C_MESSAGE_CREATE"
    sender = identifier(author["user_openid" if private else "member_openid"])
    kind = "private" if private else "group"
    conversation = sender if private else identifier(data["group_openid"])
    content = data.get("content")
    if isinstance(content, str):
        content = re.sub(r"<@!?" + re.escape(str(connection.get("self_id") or "")) + r">", "", content).strip()
    return text_message(
        connection,
        kind,
        conversation,
        sender,
        data["id"],
        content,
        datetime.fromisoformat(data["timestamp"]),
        message_id_limit=1024,
    )


class QQClient:
    def __init__(self, connection: dict, http: httpx.AsyncClient):
        self.connection = connection
        self.http = http
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

    async def request(self, method: str, path: str, body: dict | None = None) -> dict:
        token = await self.access_token()
        response = await self.http.request(
            method, QQ_API + path, json=body, headers={"Authorization": "QQBot " + token}
        )
        if response.status_code == 401:
            self.expires = 0
        # Sending is never retried after possible external effects.
        return response_object(response, "QQ")

    async def send(self, route: dict, text: str, context: dict, store: Store) -> str:
        message_id = context["message_id"]
        sequence = await store.next_channel_sequence(self.connection["id"], message_id)
        resource = "users" if route["conversation_kind"] == "private" else "groups"
        value = await self.request(
            "POST",
            f"/v2/{resource}/{quote(route['conversation_id'], safe='')}/messages",
            {"content": text, "msg_type": 0, "msg_id": message_id, "msg_seq": sequence},
        )
        return identifier(value["id"], max_length=1024)


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
            await socket.send(json.dumps({"op": 2, "d": {"token": token, "intents": 1 << 25, "shard": [0, 1]}}))
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
