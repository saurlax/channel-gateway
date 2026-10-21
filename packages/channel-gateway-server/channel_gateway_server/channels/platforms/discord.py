"""Discord bot REST and resumable Gateway lifecycle, without SDK write retries."""

import asyncio
import json
from contextlib import suppress
from datetime import datetime
from urllib.parse import quote, urlsplit

from channel_gateway_core import Attachment, ChannelAddress, ImageAttachment, Message

from ...adapters import AdapterError
from ...media import decode_media
from ..common import OperationRejected, identifier
from ..ingress import ProjectionError, begin_event, ingest_parsed
from .common import PlatformClient, remote_url

DISCORD_INTENTS = 1 | 512 | 1024 | 4096 | 8192 | 32768


def discord_message(packet: dict, connection: dict, threads: set[str] | None = None) -> Message | None:
    if packet.get("t") != "MESSAGE_CREATE":
        return None
    raw = packet["d"]
    user = identifier(raw["author"]["id"])
    if user == connection["self_id"]:
        return None
    channel = identifier(raw["channel_id"])
    kind = "private" if not raw.get("guild_id") else "thread" if channel in (threads or set()) else "channel"
    text = raw.get("content", "")
    if not isinstance(text, str):
        raise ValueError("Invalid Discord message text")
    images, attachments = [], []
    for item in raw.get("attachments", []):
        url = remote_url(item["url"], {"cdn.discordapp.com", "media.discordapp.net"}, prefix="/attachments/")
        mime = item.get("content_type") or "application/octet-stream"
        if mime.startswith("image/") and len(images) < 4:
            images.append(ImageAttachment(url=url))
        else:
            attachments.append(
                Attachment(
                    kind="video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file",
                    url=url,
                    media_type=mime,
                    filename=item.get("filename") or "attachment",
                )
            )
    if not text.strip() and not images and not attachments:
        return None
    return Message(
        address=ChannelAddress(channel="discord", account_id=connection["id"], conversation_id=kind + ":" + channel),
        source_message_id=identifier(raw["id"]),
        sender_id=user,
        text=text,
        images=tuple(images),
        attachments=tuple(attachments),
        guild_id=identifier(raw["guild_id"]) if raw.get("guild_id") else None,
        bot_mentioned=any(item.get("id") == connection["self_id"] for item in raw.get("mentions", []))
        if kind != "private"
        else None,
        occurred_at=datetime.fromisoformat(raw["timestamp"]),
    )


class DiscordClient(PlatformClient):
    base = "https://discord.com/api/v10"
    provider = "Discord"

    async def headers(self):
        return {"Authorization": "Bot " + self.connection["token"]}

    async def ensure_identity(self):
        async with self.identity_lock:
            if not self.identity_verified:
                value = await self.request("GET", "/users/@me")
                if (
                    not isinstance(value, dict)
                    or value.get("id") != self.connection["self_id"]
                    or value.get("bot") is not True
                ):
                    raise OperationRejected("Discord token does not match the configured bot identity")
                self.identity_verified = True

    @staticmethod
    def path(route):
        return "/channels/" + quote(route["conversation_id"], safe="")

    async def send(self, route, text, payload, check, effect):
        body = {"content": text, "allowed_mentions": {"parse": [], "replied_user": False}}
        if payload.get("reference_id"):
            body["message_reference"] = {"message_id": payload["reference_id"], "fail_if_not_exists": True}
        files, data = None, None
        if payload.get("kind") in {"image", "media"}:
            blob = decode_media(payload["image_base64"])
            body["attachments"] = [{"id": 0, "filename": payload["filename"]}]
            data, files = (
                {"payload_json": json.dumps(body)},
                {"files[0]": (payload["filename"], blob, payload["media_type"])},
            )
            body = None
        await check()
        value = await self.request("POST", self.path(route) + "/messages", body=body, data=data, files=files)
        if not isinstance(value, dict) or value.get("channel_id") != route["conversation_id"]:
            raise AdapterError("Discord returned an unexpected message target")
        return identifier(value["id"])

    async def history(self, route, limit, cursor):
        params = {"limit": limit, **({"before": cursor} if cursor else {})}
        value = await self.request("GET", self.path(route) + "/messages", params=params)
        if not isinstance(value, list):
            raise AdapterError("Discord returned invalid history")
        messages = []
        for item in value:
            if item.get("channel_id") != route["conversation_id"]:
                continue
            packet = {"t": "MESSAGE_CREATE", "d": item}
            if route["conversation_kind"] != "private":
                packet["d"] = {**item, "guild_id": "history"}
            message = discord_message(
                packet,
                {**self.connection, "self_id": "__history__"},
                {route["conversation_id"]} if route["conversation_kind"] == "thread" else set(),
            )
            if message:
                messages.append(message.model_dump(mode="json"))
        return messages, identifier(value[-1]["id"]) if len(value) == limit else None

    async def read_asset(self, item, *, image):
        return await self.download(
            remote_url(item["url"], {"cdn.discordapp.com", "media.discordapp.net"}, prefix="/attachments/"), image=image
        )

    async def reaction(self, route, args, check):
        emoji = args["emoji"]
        await check()
        if emoji["kind"] == "unicode":
            value = emoji["value"]
        elif emoji["kind"] == "custom" and emoji.get("id") and emoji.get("name"):
            value = emoji["name"] + ":" + emoji["id"]
        else:
            raise ValueError("Discord requires a Unicode emoji or custom name and ID")
        return await self.request(
            "PUT" if args["add"] else "DELETE",
            self.path(route)
            + "/messages/"
            + quote(args["source_message_id"], safe="")
            + "/reactions/"
            + quote(value, safe="")
            + "/@me",
        )

    async def recall(self, route, message_id):
        return await self.request("DELETE", self.path(route) + "/messages/" + quote(message_id, safe=""))

    async def typing(self, route):
        return await self.request("POST", self.path(route) + "/typing")


def gateway_url(url: str) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "wss"
        or parsed.hostname != "gateway.discord.gg"
        or parsed.port not in {None, 443}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AdapterError("Discord returned an invalid Gateway URL")
    return url.rstrip("/") + "/?v=10&encoding=json"


class DiscordGateway:
    def __init__(self, client, store, receive, status):
        self.client, self.store, self.receive, self.status = client, store, receive, status
        self.session = {}
        self.acknowledged = True

    async def run(self, socket):
        self.session = await self.store.channel_state(self.client.connection["id"], "discord_gateway")
        raw = await asyncio.wait_for(socket.recv(), 15)
        archive_id, hello = await begin_event(self.store, self.client.connection, raw)
        if archive_id is None:
            await socket.close()
            return
        if hello.get("op") != 10:
            raise AdapterError("Discord Gateway omitted Hello")
        interval = float(hello["d"]["heartbeat_interval"]) / 1000
        if not 0.1 <= interval <= 300:
            raise AdapterError("Invalid Discord heartbeat interval")
        await self.store.finish_channel_event(archive_id, "control", event_type="HELLO")
        if self.session.get("session_id") and self.session.get("seq") is not None:
            packet = {
                "op": 6,
                "d": {
                    "token": self.client.connection["token"],
                    "session_id": self.session["session_id"],
                    "seq": self.session["seq"],
                },
            }
        else:
            packet = {
                "op": 2,
                "d": {
                    "token": self.client.connection["token"],
                    "intents": self.client.connection.get("discord_intents") or DISCORD_INTENTS,
                    "properties": {"os": "linux", "browser": "channel-gateway", "device": "channel-gateway"},
                },
            }
        await socket.send(json.dumps(packet))
        self.acknowledged = True
        heartbeat = asyncio.create_task(self.heartbeat(socket, interval))
        try:
            async for raw in socket:
                await self.dispatch(raw, socket)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def heartbeat(self, socket, interval):
        while True:
            await asyncio.sleep(interval)
            if not self.acknowledged:
                await socket.close()
                return
            self.acknowledged = False
            await socket.send(json.dumps({"op": 1, "d": self.session.get("seq")}))

    async def dispatch(self, raw, socket):
        archive_id, packet = await begin_event(self.store, self.client.connection, raw)
        if archive_id is None:
            await socket.close()
            return
        op, event = packet.get("op"), packet.get("t")
        if op == 11:
            self.acknowledged = True
        elif op == 1:
            await socket.send(json.dumps({"op": 1, "d": self.session.get("seq")}))
        elif op in {7, 9}:
            if op == 9 and packet.get("d") is not True:
                self.session = {}
                await self.store.set_channel_state(self.client.connection["id"], "discord_gateway", {})
            await socket.close()
        elif op == 0:
            if event == "READY":
                if packet["d"]["user"]["id"] != self.client.connection["self_id"]:
                    raise AdapterError("Discord bot identity does not match")
                self.session.update(
                    session_id=identifier(packet["d"]["session_id"]), resume_url=packet["d"]["resume_gateway_url"]
                )
                self.status("connected")
            elif event == "RESUMED":
                self.status("connected")
            # GUILD_CREATE contains joined threads; THREAD_* changes are separately retained.
            elif event == "GUILD_CREATE":
                threads = self.session.setdefault("threads", [])
                self.session["threads"] = list(
                    set(threads) | {identifier(item["id"]) for item in packet["d"].get("threads", [])}
                )
            elif event in {"THREAD_CREATE", "THREAD_UPDATE", "THREAD_DELETE"}:
                threads = set(self.session.get("threads", []))
                threads.discard(identifier(packet["d"]["id"]))
                if event != "THREAD_DELETE":
                    threads.add(identifier(packet["d"]["id"]))
                self.session["threads"] = list(threads)
            elif event == "THREAD_LIST_SYNC":
                self.session["threads"] = list(
                    set(self.session.get("threads", []))
                    | {identifier(item["id"]) for item in packet["d"].get("threads", [])}
                )
            if event == "MESSAGE_CREATE" and packet["d"].get("guild_id"):
                channel_id = identifier(packet["d"]["channel_id"])
                channel = await self.client.request("GET", "/channels/" + quote(channel_id, safe=""))
                if (
                    not isinstance(channel, dict)
                    or channel.get("id") != channel_id
                    or channel.get("guild_id") != packet["d"]["guild_id"]
                ):
                    raise AdapterError("Discord channel identity could not be verified")
                if channel.get("type") in {10, 11, 12}:
                    threads = set(self.session.get("threads", []))
                    threads.add(channel_id)
                    self.session["threads"] = list(threads)
            try:
                await ingest_parsed(
                    self.store,
                    self.client.connection,
                    archive_id,
                    packet,
                    self.receive,
                    lambda value: (
                        discord_message(value, self.client.connection, set(self.session.get("threads", []))),
                        None,
                    ),
                )
            except ProjectionError:
                pass
            if isinstance(packet.get("s"), int):
                # Persist resume position only after durable ingress, never after Host execution.
                self.session["seq"] = packet["s"]
                await self.store.set_channel_state(self.client.connection["id"], "discord_gateway", self.session)
            return
        await self.store.finish_channel_event(archive_id, "control", event_type=event or "opcode:" + str(op))
