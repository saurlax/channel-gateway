"""Owned channel tasks, account status, and provider-specific text sending."""

import asyncio
import secrets
import sqlite3
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress

import httpx
from channel_gateway_core import Message
from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from ..adapters import AdapterError, HttpAdapters
from ..models import route_allows_sender, weixin_url
from ..storage import Store
from .common import MAX_EVENT_BYTES, SendBlocked, identifier
from .onebot import OneBotSession, ReceiveMessage
from .qq import QQClient, QQGateway, gateway_url
from .weixin import WEIXIN_BASE, LoginRequired, WeixinClient, qr_image, weixin_message


class AccountSocket(connect):
    """Account endpoints must not redirect a credential-bearing handshake."""

    def process_redirect(self, exc: Exception) -> Exception:
        return exc


def transport(connection: dict) -> str:
    return connection.get("transport") or {"onebots": "http_reverse", "qq": "websocket", "weixin": "polling"}.get(
        connection["kind"], ""
    )


def capabilities(connection: dict) -> dict:
    return {
        "receive": not (connection["kind"] == "onebots" and transport(connection) == "http"),
        "send": connection["kind"] not in {"a13n", "mcp"},
        "conversations": ["private"] if connection["kind"] == "weixin" else ["private", "group"],
        "content": ["text"],
    }


class ChannelManager:
    def __init__(self, store: Store, http: httpx.AsyncClient):
        self.store = store
        self.http = http
        self.enabled = False
        self.receive: ReceiveMessage | None = None
        self.tasks: dict[str, asyncio.Task] = {}
        self.states: dict[str, dict] = {}
        self.sessions: dict[str, OneBotSession] = {}
        self.session_closers: dict[str, Callable[[], Awaitable[None]]] = {}
        self.qq_clients: dict[str, QQClient] = {}
        self.logins: dict[str, dict] = {}
        self.login_locks: dict[str, asyncio.Lock] = {}

    def status(self, connection: dict) -> dict:
        default = "login_required" if connection["kind"] == "weixin" and not connection.get("token") else "inactive"
        return {
            "transport": transport(connection),
            "capabilities": capabilities(connection),
            **self.states.get(connection["id"], {"state": default, "error": None}),
        }

    def set_status(self, connection_id: str, state: str, error: str | None = None) -> None:
        self.states[connection_id] = {"state": state, "error": error}

    async def start(self, receive: ReceiveMessage, *, enabled: bool) -> None:
        self.receive = receive
        self.enabled = enabled
        for connection in await self.store.connections():
            await self.restart(connection)

    async def stop(self, identifier: str) -> None:
        task = self.tasks.pop(identifier, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        session = self.sessions.pop(identifier, None)
        if session:
            session.disconnected()
        closer = self.session_closers.pop(identifier, None)
        if closer:
            await closer()
        self.qq_clients.pop(identifier, None)

    async def restart(self, connection: dict) -> None:
        identifier = connection["id"]
        await self.stop(identifier)
        if connection["kind"] in {"a13n", "mcp"}:
            return
        mode = transport(connection)
        if mode in {"http", "http_reverse", "ws_reverse", "webhook"}:
            state = {
                "http": "http_only",
                "http_reverse": "callback_ready",
                "ws_reverse": "awaiting_connection",
                "webhook": "callback_ready",
            }[mode]
            self.set_status(identifier, state)
        elif connection["kind"] == "weixin" and not connection.get("token"):
            self.set_status(identifier, "login_required")
        elif self.enabled:
            self.set_status(identifier, "connecting")
            self.tasks[identifier] = asyncio.create_task(self.run(connection))
        else:
            self.set_status(identifier, "inactive")

    async def close(self) -> None:
        self.enabled = False
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for closer in list(self.session_closers.values()):
            with suppress(Exception):
                await closer()
        self.tasks.clear()
        self.logins.clear()

    async def ingest(self, connection: dict, message: Message | None, context: dict | None = None) -> dict:
        assert self.receive is not None
        return await self.receive(connection, message, context)

    async def run(self, connection: dict) -> None:
        delay = 1
        while True:
            try:
                if connection["kind"] == "weixin":
                    await self.poll_weixin(connection)
                elif connection["kind"] == "qq":
                    await self.connect_qq(connection)
                else:
                    await self.connect_onebot(connection)
                delay = 1
            except LoginRequired:
                self.set_status(connection["id"], "login_required", "Weixin session expired; scan a new login QR code")
                return
            except (
                AdapterError,
                sqlite3.Error,
                httpx.HTTPError,
                WebSocketException,
                OSError,
                TimeoutError,
                OverflowError,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
            ):
                # Upstream payloads and credentials never reach diagnostics.
                self.set_status(
                    connection["id"], "reconnecting", "Channel connection failed; check credentials and network"
                )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def connect_onebot(self, connection: dict) -> None:
        async with AccountSocket(
            connection["api_url"],
            additional_headers={"Authorization": "Bearer " + connection["token"]},
            proxy=None,
            open_timeout=15,
            max_size=MAX_EVENT_BYTES,
        ) as socket:
            await self.serve_onebot(connection, socket.send, socket.close, socket)

    async def serve_onebot(self, connection: dict, send, close, incoming) -> None:
        identifier = connection["id"]
        if identifier in self.sessions:
            raise AdapterError("This account already has a WebSocket connection")
        current = await self.store.connection(identifier)
        if current != connection:
            raise AdapterError("Connection changed; reconnect")
        session = OneBotSession(connection, self.ingest, send)
        self.sessions[identifier] = session
        self.session_closers[identifier] = close
        self.set_status(identifier, "connected")
        try:
            async for raw in incoming:
                try:
                    await session.dispatch(raw)
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    self.set_status(identifier, "connected", "Invalid channel event was ignored")
        finally:
            session.disconnected()
            if self.sessions.get(identifier) is session:
                self.sessions.pop(identifier, None)
                self.session_closers.pop(identifier, None)
                self.set_status(
                    identifier, "awaiting_connection" if transport(connection) == "ws_reverse" else "reconnecting"
                )

    def qq(self, connection: dict) -> QQClient:
        identifier = connection["id"]
        if identifier not in self.qq_clients:
            self.qq_clients[identifier] = QQClient(connection, self.http)
        return self.qq_clients[identifier]

    async def connect_qq(self, connection: dict) -> None:
        client = self.qq(connection)
        identity = await client.request("GET", "/users/@me")
        client.connection = {**connection, "self_id": identifier(identity["id"])}
        gateway = await client.request("GET", "/gateway")
        async with AccountSocket(
            gateway_url(gateway["url"]), proxy=None, open_timeout=15, max_size=MAX_EVENT_BYTES
        ) as socket:
            runtime = QQGateway(client, self.store, self.ingest, lambda state: self.set_status(connection["id"], state))
            try:
                await runtime.run(socket)
            finally:
                if socket.close_code in {4006, 4007, 4009}:
                    await self.store.set_channel_state(connection["id"], "qq_gateway", {})
                if socket.close_code == 4004:
                    client.expires = 0

    async def poll_weixin(self, connection: dict) -> None:
        client = WeixinClient(connection, self.http)
        cursor = (await self.store.channel_state(connection["id"], "weixin_cursor")).get("cursor", "")
        await client.request("msg/notifystart", {})
        self.set_status(connection["id"], "connected")
        try:
            while True:
                try:
                    batch = await client.updates(cursor)
                except httpx.ReadTimeout:
                    continue
                if not isinstance(batch.get("msgs", []), list):
                    raise AdapterError("Weixin returned an invalid update batch")
                for raw in batch.get("msgs", []):
                    try:
                        message = weixin_message(raw, connection)
                        token = raw.get("context_token")
                        if token is not None and (not isinstance(token, str) or len(token) > 16384):
                            raise ValueError("Invalid reply context")
                        context = {"context_token": token} if token else None
                    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                        self.set_status(connection["id"], "connected", "Invalid channel event was ignored")
                        continue
                    await self.ingest(connection, message, context)
                next_cursor = batch.get("get_updates_buf")
                if isinstance(next_cursor, str) and next_cursor:
                    await self.store.set_channel_state(connection["id"], "weixin_cursor", {"cursor": next_cursor})
                    cursor = next_cursor
                if not batch.get("msgs"):
                    await asyncio.sleep(0.25)
        finally:
            with suppress(AdapterError, httpx.HTTPError, ValueError):
                await client.request("msg/notifystop", {}, timeout=5)

    def ready(self, connection: dict) -> bool:
        if connection["kind"] == "onebots" and transport(connection) in {"ws", "ws_reverse"}:
            return connection["id"] in self.sessions
        if connection["kind"] == "weixin":
            return (
                bool(connection.get("token")) and self.states.get(connection["id"], {}).get("state") != "login_required"
            )
        return True

    async def send(self, connection: dict, route: dict, text: str, reply: dict) -> str:
        if connection["kind"] == "onebots":
            if transport(connection) in {"http", "http_reverse"}:
                return await HttpAdapters(self.http).send(connection, route, text)
            session = self.sessions.get(connection["id"])
            if not session:
                raise SendBlocked("Channel is disconnected")
            target = "group_id" if route["conversation_kind"] == "group" else "user_id"
            receipt = await session.call(
                "send_msg",
                {
                    "message_type": route["conversation_kind"],
                    target: int(route["conversation_id"])
                    if route["conversation_id"].isdecimal()
                    else route["conversation_id"],
                    "message": [{"type": "text", "data": {"text": text}}],
                },
            )
            return identifier(receipt["data"]["message_id"])
        context = await self.store.channel_state(
            connection["id"], "context:" + route["conversation_kind"] + ":" + route["conversation_id"]
        )
        if (
            not isinstance(context.get("sender_id"), str)
            or not context["sender_id"]
            or not route_allows_sender(route, context["sender_id"])
        ):
            raise SendBlocked("No authorized reply context; wait for an incoming message")
        if connection["kind"] == "qq":
            if reply.get("event_id"):
                source = await self.store.event(reply["event_id"])
                if source:
                    context = {**context, "message_id": source["message"]["source_message_id"]}
            return await self.qq(connection).send(route, text, context, self.store)
        if not isinstance(context.get("context_token"), str) or not context["context_token"]:
            raise SendBlocked("No Weixin context token; wait for an incoming message")
        return await WeixinClient(connection, self.http).send(
            route["conversation_id"], text, context["context_token"], reply["id"]
        )

    async def begin_login(self, connection: dict) -> dict:
        async with self.login_locks.setdefault(connection["id"], asyncio.Lock()):
            value = await WeixinClient(connection, self.http).start_login()
            login = {
                "login_id": "login_" + secrets.token_hex(16),
                "qrcode": value["qrcode"],
                "host": WEIXIN_BASE,
                "expires": time.monotonic() + 300,
                "expected": connection,
            }
            self.logins[connection["id"]] = login
            # Prefer the provider's QR content; render it locally without fetching remote images.
            content = value.get("qrcode_img_content") or value["qrcode"]
            if not isinstance(content, str) or len(content) > 4096:
                raise AdapterError("Weixin returned invalid QR content")
            return {"login_id": login["login_id"], "status": "wait", "qr_image": qr_image(content)}

    async def poll_login(self, connection: dict, login_id: str, verify_code: str | None) -> dict:
        async with self.login_locks.setdefault(connection["id"], asyncio.Lock()):
            login = self.logins.get(connection["id"])
            if not login or login["login_id"] != login_id or time.monotonic() >= login["expires"]:
                return {"status": "expired"}
            value = await WeixinClient(connection, self.http).poll_login(login, verify_code)
            state = value.get("status")
            if state == "confirmed":
                account = identifier(value["ilink_bot_id"])
                if connection.get("self_id") and connection["self_id"] != account:
                    raise AdapterError("The QR code belongs to a different bot; add a separate channel")
                token = value.get("bot_token")
                if not isinstance(token, str) or not token or len(token) > 4096:
                    raise AdapterError("Weixin returned invalid pairing credentials")
                updated = {**connection, "self_id": account, "token": token, "api_url": weixin_url(value["baseurl"])}
                if not await self.store.update_connection(connection["id"], updated, login["expected"]):
                    self.logins.pop(connection["id"], None)
                    raise AdapterError("Connection changed; start pairing again")
                self.logins.pop(connection["id"], None)
                await self.restart(updated)
                return {"status": "confirmed", "self_id": account}
            if state not in {
                "wait",
                "scaned",
                "expired",
                "need_verifycode",
                "verify_code_blocked",
                "scaned_but_redirect",
                "binded_redirect",
            }:
                raise AdapterError("Weixin returned an invalid pairing status")
            if state in {"expired", "verify_code_blocked", "binded_redirect"}:
                self.logins.pop(connection["id"], None)
            return {"status": state}
