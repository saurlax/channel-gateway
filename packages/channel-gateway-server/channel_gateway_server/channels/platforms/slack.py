"""Slack Events API, bot Web API, and authenticated private file retrieval."""

import asyncio
import hashlib
import hmac
import re
import time
from datetime import UTC, datetime

from channel_gateway_core import Attachment, ChannelAddress, ImageAttachment, Message

from ...adapters import AdapterError
from ..common import OperationRejected, identifier
from .common import PlatformClient, remote_url, response_data


def verify(connection: dict, raw: bytes, timestamp: str, signature: str) -> bool:
    if len(timestamp) > 12 or not timestamp.isdecimal() or abs(time.time() - int(timestamp)) > 300:
        return False
    digest = hmac.new(connection["webhook_secret"].encode(), b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256)
    return hmac.compare_digest("v0=" + digest.hexdigest(), signature)


def slack_message(packet: dict, connection: dict) -> Message | None:
    if packet.get("team_id") != connection["app_id"]:
        raise ValueError("Slack workspace does not match this installation")
    event = packet.get("event", {})
    if packet.get("type") != "event_callback" or event.get("type") not in {"message", "app_mention"}:
        return None
    if event.get("subtype") not in {None, "file_share", "thread_broadcast"}:
        return None  # Edits/deletes are journaled, never replayed as new messages.
    if event.get("user") == connection["self_id"]:
        return None
    user, channel = identifier(event["user"]), identifier(event["channel"])
    mid = identifier(event["ts"])
    root = event.get("thread_ts")
    kind = "thread" if root and root != mid else "private" if channel.startswith("D") else "channel"
    conversation = channel + ":" + identifier(root) if kind == "thread" else channel
    text = event.get("text", "")
    if not isinstance(text, str):
        raise ValueError("Invalid Slack message text")
    mentioned = bool(re.search(r"<@" + re.escape(connection["self_id"]) + r">", text))
    images, attachments = [], []
    for item in event.get("files", []):
        url = remote_url(item["url_private"], {"files.slack.com"})
        mime = item.get("mimetype") or "application/octet-stream"
        if mime.startswith("image/") and len(images) < 4:
            images.append(ImageAttachment(url=url))
        else:
            attachments.append(
                Attachment(
                    kind="video" if mime.startswith("video/") else "audio" if mime.startswith("audio/") else "file",
                    url=url,
                    media_type=mime,
                    filename=item.get("name") or "attachment",
                )
            )
    if not text.strip() and not images and not attachments:
        return None
    return Message(
        address=ChannelAddress(channel="slack", account_id=connection["id"], conversation_id=kind + ":" + conversation),
        source_message_id=mid,
        sender_id=user,
        text=text,
        images=tuple(images),
        attachments=tuple(attachments),
        bot_mentioned=mentioned if kind != "private" else None,
        occurred_at=datetime.fromtimestamp(float(mid), UTC),
    )


class SlackClient(PlatformClient):
    base = "https://slack.com/api/"
    provider = "Slack"

    async def request(self, method, path, **kwargs):
        value = await super().request(method, path, **kwargs)
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise OperationRejected("Slack rejected the operation; check membership and scopes")
        return value

    async def ensure_identity(self):
        async with self.identity_lock:
            if not self.identity_verified:
                value = await self.request("POST", "auth.test")
                if (
                    value.get("team_id") != self.connection["app_id"]
                    or value.get("user_id") != self.connection["self_id"]
                ):
                    raise OperationRejected("Slack token does not match the configured bot installation")
                self.identity_verified = True

    @staticmethod
    def target(route: dict) -> tuple[str, str | None]:
        if route["conversation_kind"] == "thread":
            channel, root = route["conversation_id"].split(":", 1)
            return identifier(channel), identifier(root)
        return route["conversation_id"], None

    async def send(self, route, text, payload, check, effect) -> str:
        channel, root = self.target(route)
        body = {
            "channel": channel,
            "text": text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"),
            "parse": "none",
            "link_names": False,
            "unfurl_links": False,
            "unfurl_media": False,
            "mrkdwn": payload.get("message_format") == "markdown",
        }
        if root or payload.get("reference_id"):
            body["thread_ts"] = root or payload["reference_id"]
        if payload.get("kind") not in {"image", "media"}:
            await check()
            value = await self.request("POST", "chat.postMessage", body=body)
            return identifier(value["ts"])
        from ...media import decode_media

        data = decode_media(payload["image_base64"])
        prepared = await self.request(
            "GET", "files.getUploadURLExternal", params={"filename": payload["filename"], "length": len(data)}
        )
        file_id = identifier(prepared["file_id"])
        await effect("upload_prepared", file_id)
        url = remote_url(prepared["upload_url"], {"files.slack.com"}, prefix="/upload/")
        await check()
        request = self.media_http.build_request(
            "POST", url, content=data, headers={"Content-Type": "application/octet-stream"}
        )
        request.headers.pop("authorization", None)
        request.headers.pop("cookie", None)
        async with asyncio.timeout(30):
            response = await self.media_http.send(request, auth=None, follow_redirects=False, stream=True)
            try:
                if not response.is_success:
                    raise AdapterError("Slack file upload was not confirmed")
                await response_data(response, limit=65536)
            finally:
                await response.aclose()
        await effect("uploaded", file_id)
        await check()
        complete = {"files": [{"id": file_id, "title": payload["filename"]}], "channel_id": channel}
        if text:
            complete["initial_comment"] = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if root:
            complete["thread_ts"] = root
        await self.request("POST", "files.completeUploadExternal", body=complete)
        await effect("published_file", file_id)
        # Completion's file ID is not a message ID; resolve the actual share ts.
        value = await self.request("GET", "files.info", params={"file": file_id})
        shares = value.get("file", {}).get("shares", {})
        for visibility in ("private", "public"):
            for share in shares.get(visibility, {}).get(channel, []):
                if (share.get("thread_ts") or None) == root:
                    return identifier(share["ts"])
        raise AdapterError("Slack accepted the file but omitted its message receipt")

    async def history(self, route: dict, limit: int, cursor: str | None) -> tuple[list[dict], str | None]:
        channel, root = self.target(route)
        params = {"channel": channel, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        if root:
            params["ts"] = root
        value = await self.request("GET", "conversations.replies" if root else "conversations.history", params=params)
        messages = []
        for item in value.get("messages", []):
            if item.get("channel", channel) != channel:
                continue
            if not root and item.get("thread_ts") not in {None, item.get("ts")}:
                continue
            if not item.get("user"):
                continue
            # Project only this exact API target; native history never expands scope.
            if root and item.get("thread_ts", item.get("ts")) != root:
                continue
            packet = {
                "team_id": self.connection["app_id"],
                "type": "event_callback",
                "event": {**item, "type": "message", "channel": channel},
            }
            message = slack_message(packet, {**self.connection, "self_id": "__history__"})
            if message:
                messages.append(message.model_dump(mode="json"))
        return messages, value.get("response_metadata", {}).get("next_cursor") or None

    async def read_asset(self, item: dict, *, image: bool):
        return await self.download(
            remote_url(item["url"], {"files.slack.com"}), headers=await self.headers(), image=image
        )

    async def reaction(self, route, args, check):
        channel, _ = self.target(route)
        emoji = args["emoji"]
        await check()
        if emoji["kind"] != "custom" or not emoji.get("name") or emoji.get("id"):
            raise ValueError("Slack reactions require a custom emoji name without colons")
        return await self.request(
            "POST",
            "reactions.add" if args["add"] else "reactions.remove",
            body={"channel": channel, "timestamp": args["source_message_id"], "name": emoji["name"]},
        )

    async def recall(self, route, message_id):
        channel, _ = self.target(route)
        return await self.request("POST", "chat.delete", body={"channel": channel, "ts": message_id})
