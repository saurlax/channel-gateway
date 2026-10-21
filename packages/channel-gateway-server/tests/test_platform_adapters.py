"""Registered platform contracts through HTTP ingress, MCP, persistence and native I/O."""

import base64
import hashlib
import hmac
import json
import time

import httpx
import pytest
from channel_gateway_server.app import create_app
from channel_gateway_server.channels.platforms.discord import DiscordGateway
from channel_gateway_server.models import Settings
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

from .test_bridge import rpc
from .test_media import PNG, PNG_BASE64

SECRET = "platform-signing-secret-for-tests"
ENCRYPT = "platform-encryption-key-for-tests"
CONFIG = {
    "slack": {"token": "xoxb-test-token", "self_id": "UBOT", "app_id": "T1", "webhook_secret": SECRET},
    "lark": {
        "app_id": "cli_test",
        "app_secret": SECRET,
        "self_id": "ou_bot",
        "webhook_secret": SECRET,
        "encrypt_key": ENCRYPT,
    },
    "discord": {"token": "discord-bot-test-token", "self_id": "999"},
}
TARGET = {"slack": "C1", "lark": "oc_chat", "discord": "123"}
MID = {"slack": "1792000000.000001", "lark": "om_source", "discord": "100000000000000001"}
EMOJI = {
    "slack": {"kind": "custom", "name": "thumbsup"},
    "lark": {"kind": "platform", "value": "THUMBSUP"},
    "discord": {"kind": "unicode", "value": "👍"},
}


class Provider:
    def __init__(self, kind):
        self.kind, self.requests, self.downloads = kind, [], []
        self.failure = None
        self.on_api = None
        self.on_media = None
        self.extra_history = []
        self.blob, self.mime = PNG, "image/png"

    async def api(self, request):
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v10")
        if self.on_api:
            await self.on_api(request)
        if self.failure and request.method != "GET":
            if self.failure == "timeout":
                raise httpx.ReadTimeout("upstream secret payload")
            return httpx.Response(self.failure)
        if self.kind == "slack":
            assert request.headers["authorization"] == "Bearer xoxb-test-token"
            method = path.split("/")[-1]
            value = {"ok": True}
            if method == "auth.test":
                return httpx.Response(200, json={"ok": True, "team_id": "T1", "user_id": "UBOT"})
            if method == "chat.postMessage":
                value.update(ts="1792000001.000001")
            elif method == "files.getUploadURLExternal":
                value.update(file_id="F1", upload_url="https://files.slack.com/upload/v1/abc?signature=private")
            elif method == "files.info":
                value.update(file={"shares": {"public": {"C1": [{"ts": "1792000001.000001"}]}}})
            elif method in {"conversations.history", "conversations.replies"}:
                value.update(
                    messages=[self.event()["event"], *self.extra_history],
                    response_metadata={"next_cursor": "private-native-cursor"},
                )
            return httpx.Response(200, json=value)
        if self.kind == "lark":
            if path.endswith("tenant_access_token/internal"):
                return httpx.Response(
                    200, json={"code": 0, "tenant_access_token": "tenant-private-token", "expire": 7200}
                )
            assert request.headers["authorization"] == "Bearer tenant-private-token"
            value = {}
            if path.endswith("/bot/v3/info"):
                return httpx.Response(200, json={"code": 0, "bot": {"open_id": "ou_bot"}})
            if path.endswith("/images"):
                value["image_key"] = "img_upload"
            elif path.endswith("/files"):
                value["file_key"] = "file_upload"
            elif path.endswith("/reactions") and request.method == "GET":
                value.update(
                    items=[
                        {
                            "reaction_id": "rx1",
                            "operator": {"operator_type": "app", "operator_id": "cli_test"},
                            "emoji_type": "THUMBSUP",
                        }
                    ]
                )
            elif path.endswith("/messages") and request.method == "GET":
                raw = self.event()["event"]["message"]
                value.update(
                    items=[{**raw, "sender": {"id_type": "open_id", "id": "ou_user"}}, *self.extra_history],
                    has_more=True,
                    page_token="private-native-cursor",
                )
            else:
                value["message_id"] = "om_sent"
            return httpx.Response(200, json={"code": 0, "data": value})
        assert request.headers["authorization"] == "Bot discord-bot-test-token"
        if path == "/users/@me":
            return httpx.Response(200, json={"id": "999", "bot": True})
        if path.endswith("/messages") and request.method == "GET":
            return httpx.Response(200, json=[self.event()["d"], *self.extra_history])
        if path.startswith("/channels/") and path.count("/") == 2:
            return httpx.Response(200, json={"id": "123", "guild_id": "456", "type": 0})
        if path.endswith("/messages"):
            return httpx.Response(200, json={"id": "200000000000000001", "channel_id": "123"})
        return httpx.Response(204)

    async def media(self, request):
        self.downloads.append(request)
        if self.on_media:
            await self.on_media(request)
        assert "cookie" not in request.headers
        if request.method == "POST":
            assert "authorization" not in request.headers
            return httpx.Response(200)
        if self.kind == "slack":
            assert request.url.host == "files.slack.com"
            assert request.headers["authorization"] == "Bearer xoxb-test-token"
        elif self.kind == "lark":
            assert request.url.host == "open.feishu.cn"
            assert request.headers["authorization"] == "Bearer tenant-private-token"
        else:
            assert request.url.host == "cdn.discordapp.com"
            assert "authorization" not in request.headers
        return httpx.Response(200, content=self.blob, headers={"content-type": self.mime})

    def event(self, *, image=True, mid=None, text="Incoming"):
        mid = mid or MID[self.kind]
        if self.kind == "slack":
            return {
                "type": "event_callback",
                "team_id": "T1",
                "event_id": "Ev1",
                "event": {
                    "type": "message",
                    "channel": "C1",
                    "user": "UUSER",
                    "text": text,
                    "ts": mid,
                    "files": [
                        {
                            "name": "test.png",
                            "mimetype": "image/png",
                            "url_private": "https://files.slack.com/files-pri/T1-F1/file.png",
                        }
                    ]
                    if image
                    else [],
                },
            }
        if self.kind == "lark":
            return {
                "schema": "2.0",
                "header": {
                    "event_type": "im.message.receive_v1",
                    "app_id": "cli_test",
                    "token": SECRET,
                    "event_id": "ev1",
                },
                "event": {
                    "sender": {"sender_type": "user", "sender_id": {"open_id": "ou_user"}},
                    "message": {
                        "chat_id": "oc_chat",
                        "message_id": mid,
                        "chat_type": "group",
                        "message_type": "image" if image else "text",
                        "create_time": "1792000000000",
                        "content": json.dumps({"image_key": "img_original"} if image else {"text": text}),
                    },
                },
            }
        return {
            "op": 0,
            "t": "MESSAGE_CREATE",
            "s": 2,
            "d": {
                "id": mid,
                "channel_id": "123",
                "guild_id": "456",
                "author": {"id": "777"},
                "content": text,
                "timestamp": "2026-10-21T01:02:03+00:00",
                "attachments": [
                    {
                        "url": "https://cdn.discordapp.com/attachments/123/456/file.png?ex=private",
                        "filename": "test.png",
                        "content_type": "image/png",
                    }
                ]
                if image
                else [],
            },
        }


@pytest.fixture(params=["slack", "lark", "discord"])
def platform_gateway(request, tmp_path):
    kind = request.param
    provider = Provider(kind)
    http = httpx.AsyncClient(transport=httpx.MockTransport(provider.api))
    media = httpx.AsyncClient(transport=httpx.MockTransport(provider.media))
    app = create_app(
        settings=Settings(admin_username="admin", admin_password=SECRET, data_dir=tmp_path, worker_enabled=False),
        client=http,
        media_client=media,
    )
    with TestClient(app) as client:
        auth = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": SECRET},
            headers={"X-Channel-Gateway-Request": "1"},
        ).json()
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        response = client.post("/api/v1/connections", json={"name": kind, "kind": kind, **CONFIG[kind]})
        assert response.status_code == 201, response.text
        channel = response.json()
        host = client.post("/api/v1/connections", json={"name": "MCP", "kind": "mcp"}).json()
        response = client.post(
            "/api/v1/routes",
            json={
                "name": "Scope",
                "channel_id": channel["id"],
                "host_id": host["id"],
                "conversation_kind": "group" if kind == "lark" else "channel",
                "conversation_id": TARGET[kind],
                "sender_policy": "any",
                "reply_source": "mcp",
                "action_grants": ["message.react", "message.recall", "history.read"],
            },
        )
        assert response.status_code == 201, response.text
        yield kind, client, app, provider, channel, response.json()
        client.portal.call(http.aclose)
        client.portal.call(media.aclose)


def decoded(value):
    assert not value.get("isError"), value
    return json.loads(value["content"][0]["text"])


def tool(gateway, name, **arguments):
    _, client, _, _, _, route = gateway
    return rpc(client, route["agent_token"], "tools/call", {"name": name, "arguments": arguments}).json()["result"]


def tick(gateway):
    gateway[1].portal.call(gateway[2].state.bridge.tick)


def hook(gateway, packet=None, *, bad=False, expired=False):
    kind, client, app, provider, channel, _ = gateway
    packet = packet or provider.event()
    timestamp = str(int(time.time()) - (600 if expired else 0))
    if kind == "discord":

        async def dispatch():
            connection = await app.state.store.connection(channel["id"])
            runtime = DiscordGateway(
                app.state.channels.platform(connection), app.state.store, app.state.bridge.ingest, lambda _: None
            )

            class Socket:
                async def close(self):
                    pass

            await runtime.dispatch(json.dumps(packet), Socket())

        client.portal.call(dispatch)
        return None
    if kind == "slack":
        raw = json.dumps(packet).encode()
        digest = "v0=" + hmac.new(SECRET.encode(), b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        headers = {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": digest if not bad else "v0=invalid"}
    else:
        packet = {"token": SECRET, **packet}
        padder = padding.PKCS7(128).padder()
        padded = padder.update(json.dumps(packet).encode()) + padder.finalize()
        iv = b"0" * 16
        cipher = Cipher(algorithms.AES(hashlib.sha256(ENCRYPT.encode()).digest()), modes.CBC(iv)).encryptor()
        raw = json.dumps(
            {"encrypt": base64.b64encode(iv + cipher.update(padded) + cipher.finalize()).decode()}
        ).encode()
        digest = hashlib.sha256(timestamp.encode() + b"nonce" + ENCRYPT.encode() + raw).hexdigest()
        headers = {
            "X-Lark-Request-Timestamp": timestamp,
            "X-Lark-Request-Nonce": "nonce",
            "X-Lark-Signature": digest if not bad else "invalid",
        }
    return client.post(f"/api/v1/hooks/{kind}/{channel['id']}", content=raw, headers=headers)


def test_ingress_is_durable_deduplicated_and_has_fixed_tools(platform_gateway):
    _, client, app, _, channel, route = platform_gateway
    hook(platform_gateway)
    hook(platform_gateway)
    events = client.get("/api/v1/events").json()
    assert len(events) == 1
    assert events[0]["message"]["address"]["account_id"] == channel["id"]
    history = decoded(tool(platform_gateway, "read", resource="messages"))
    assert history["messages"][0]["images"] == [{"index": 0}]
    assert "private" not in json.dumps(history["messages"][0].get("images"))
    tools = rpc(client, route["agent_token"], "tools/list", {}).json()["result"]["tools"]
    assert {item["name"] for item in tools} == {"discover", "read", "send_message", "execute_action", "get_operation"}
    rows = client.portal.call(app.state.store.rows, "SELECT * FROM channel_events")
    assert len(rows) == 2


def test_signed_callbacks_reject_forgery_expiry_and_cross_installation(platform_gateway):
    kind, client, _, provider, _, _ = platform_gateway
    if kind == "discord":
        pytest.skip("Discord authenticates the bot Gateway handshake")
    assert hook(platform_gateway, bad=True).status_code == 401
    assert hook(platform_gateway, expired=True).status_code == 401
    packet = provider.event()
    if kind == "slack":
        packet["team_id"] = "T_OTHER"
        assert hook(platform_gateway, packet).status_code == 422
    else:
        packet["header"]["app_id"] = "cli_other"
        assert hook(platform_gateway, packet).status_code == 401
    assert client.get("/api/v1/events").json() == []


def test_private_image_downloads_use_only_adapter_credentials(platform_gateway):
    _, _, _, provider, _, _ = platform_gateway
    hook(platform_gateway)
    value = tool(platform_gateway, "read", resource="image", arguments={"source_message_id": MID[provider.kind]})
    assert not value.get("isError"), value
    assert value["content"][0]["type"] == "image"
    assert base64.b64decode(value["content"][0]["data"]) == PNG
    assert len(provider.downloads) == 1


@pytest.mark.parametrize(
    "media_kind,mime",
    [(None, None), ("image", "image/png"), ("audio", "audio/ogg"), ("video", "video/mp4"), ("file", "application/pdf")],
)
def test_messages_media_and_receipts(platform_gateway, media_kind, mime):
    kind, client, _, provider, _, _ = platform_gateway
    content = [{"type": "text", "text": "Reply"}] if media_kind is None or kind != "lark" else []
    if media_kind:
        content.append(
            {
                "type": media_kind,
                "media_type": mime,
                "filename": "test.png" if media_kind == "image" else "test.bin",
                "data_base64": PNG_BASE64 if media_kind == "image" else base64.b64encode(b"media-bytes").decode(),
            }
        )
    queued = decoded(tool(platform_gateway, "send_message", content=content))
    tick(platform_gateway)
    value = decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))
    assert value["state"] == "sent", value
    assert value["result"]["message_id"]
    if media_kind and kind == "slack":
        assert [item["stage"] for item in value["effects"]] == ["upload_prepared", "uploaded", "published_file"]
    history = decoded(tool(platform_gateway, "read", resource="messages"))
    assert any(item["direction"] == "outgoing" for item in history["messages"])
    assert len(client.get("/api/v1/replies").json()) == 1
    if kind == "discord" and media_kind:
        assert b"payload_json" in provider.requests[-1].content


def test_native_history_never_triggers_host_and_filters_foreign_results(platform_gateway):
    kind, client, _, provider, _, _ = platform_gateway
    if kind == "slack":
        provider.extra_history = [{**provider.event()["event"], "channel": "FOREIGN", "text": "secret"}]
    elif kind == "lark":
        provider.extra_history = [
            {
                **provider.event()["event"]["message"],
                "chat_id": "FOREIGN",
                "sender": {"id_type": "open_id", "id": "other"},
            }
        ]
    else:
        provider.extra_history = [{**provider.event()["d"], "channel_id": "FOREIGN", "content": "secret"}]
    value = decoded(tool(platform_gateway, "read", resource="messages", source="platform", arguments={"limit": 1}))
    assert value["source"] == "platform" and len(value["messages"]) == 1
    assert "private-native-cursor" not in json.dumps(value)
    assert "secret" not in json.dumps(value)
    assert value["messages"][0]["images"] == [{"index": 0}]
    image = tool(
        platform_gateway, "read", resource="image", source="platform", arguments={"source_message_id": MID[kind]}
    )
    assert image["content"][0]["type"] == "image", image
    assert client.get("/api/v1/events").json() == []
    assert decoded(tool(platform_gateway, "read", resource="messages"))["messages"] == []


def test_native_history_grant_is_separate_and_revocation_invalidates_cursor_and_assets(platform_gateway):
    kind, client, _, provider, _, route = platform_gateway
    value = decoded(tool(platform_gateway, "read", resource="messages", source="platform", arguments={"limit": 1}))
    assert value["next_cursor"]
    client.patch(f"/api/v1/routes/{route['id']}", json={"action_grants": [], "read_channel_ids": ["*"]})
    before = len(provider.requests)
    denied = tool(
        platform_gateway, "read", resource="messages", source="platform", arguments={"cursor": value["next_cursor"]}
    )
    assert denied["isError"]
    assert len(provider.requests) == before
    client.patch(f"/api/v1/routes/{route['id']}", json={"action_grants": ["history.read"]})
    denied = tool(
        platform_gateway, "read", resource="image", source="platform", arguments={"source_message_id": MID[kind]}
    )
    assert denied["isError"]


def test_reactions_and_recall_are_independent_actions(platform_gateway):
    kind, client, _, _, _, route = platform_gateway
    hook(platform_gateway)
    queued = decoded(
        tool(
            platform_gateway,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": MID[kind], "emoji": EMOJI[kind]},
        )
    )
    tick(platform_gateway)
    assert decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))["state"] == "succeeded"
    assert client.get("/api/v1/replies").json() == []
    queued = decoded(
        tool(
            platform_gateway,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": MID[kind], "emoji": EMOJI[kind], "add": False},
        )
    )
    tick(platform_gateway)
    assert decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))["state"] == "succeeded"
    queued = decoded(tool(platform_gateway, "send_message", content=[{"type": "text", "text": "Own"}]))
    tick(platform_gateway)
    mid = decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))["result"]["message_id"]
    recall = decoded(tool(platform_gateway, "execute_action", action="recall", arguments={"message_id": mid}))
    tick(platform_gateway)
    assert decoded(tool(platform_gateway, "get_operation", operation_id=recall["operation_id"]))["state"] == "succeeded"
    client.patch(f"/api/v1/routes/{route['id']}", json={"action_grants": []})
    assert tool(
        platform_gateway,
        "execute_action",
        action="reaction",
        arguments={"source_message_id": MID[kind], "emoji": EMOJI[kind]},
    )["isError"]


@pytest.mark.parametrize("failure,expected", [("timeout", "unknown"), (403, "failed"), (429, "failed")])
def test_unconfirmed_writes_are_never_retried(platform_gateway, failure, expected):
    _, _, _, provider, _, _ = platform_gateway
    provider.failure = failure
    queued = decoded(tool(platform_gateway, "send_message", content=[{"type": "text", "text": "One attempt"}]))
    tick(platform_gateway)
    assert decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))["state"] == expected
    attempts = len(provider.requests)
    tick(platform_gateway)
    assert len(provider.requests) == attempts


def test_capabilities_and_validation_are_honest(platform_gateway):
    kind, _, _, provider, _, _ = platform_gateway
    value = decoded(tool(platform_gateway, "discover", resource="capabilities"))
    assert value["read"]["platform_history"]["authorized"] is True
    assert value["adapter"]["features"]["stream"]["support"] == "unsupported"
    assert value["send"]["stream"]["supported"] is False
    bad = tool(
        platform_gateway,
        "send_message",
        content=[
            {"type": "file", "media_type": "application/pdf", "filename": "x.pdf", "url": "https://files.example/x"}
        ],
    )
    assert bad["isError"] and provider.requests == []
    assert tool(platform_gateway, "send_message", content=[{"type": "text", "text": "stream"}], stream={"index": 0})[
        "isError"
    ]
    if kind == "discord":
        assert tool(platform_gateway, "send_message", content=[{"type": "text", "text": "x" * 2001}])["isError"]


@pytest.mark.parametrize(
    "media_kind,mime", [("audio", "audio/ogg"), ("video", "video/mp4"), ("file", "application/pdf")]
)
def test_received_attachments_return_exact_native_content(platform_gateway, media_kind, mime):
    kind, _, _, provider, _, _ = platform_gateway
    packet = provider.event()
    if kind == "slack":
        packet["event"]["files"][0].update(mimetype=mime, name="received.bin")
    elif kind == "lark":
        packet["event"]["message"].update(
            message_type={"audio": "audio", "video": "media", "file": "file"}[media_kind],
            content=json.dumps({"file_key": "file_current", "file_name": "received.bin"}),
        )
    else:
        packet["d"]["attachments"][0].update(content_type=mime, filename="received.bin")
    provider.blob, provider.mime = b"current-resource-bytes", mime
    hook(platform_gateway, packet)
    history = decoded(tool(platform_gateway, "read", resource="messages"))
    assert history["messages"][0]["images"] == []
    assert history["messages"][0]["attachments"][0]["kind"] == media_kind
    value = tool(platform_gateway, "read", resource="attachment", arguments={"source_message_id": MID[kind]})
    assert not value.get("isError"), value
    content = value["content"][0]
    blob = content["data"] if media_kind == "audio" else content["resource"]["blob"]
    assert base64.b64decode(blob) == provider.blob


def test_notifications_edits_and_own_echoes_never_start_agent_work(platform_gateway):
    kind, client, _, provider, _, _ = platform_gateway
    packet = provider.event(image=False)
    if kind == "slack":
        packet["event"]["user"] = CONFIG[kind]["self_id"]
    elif kind == "lark":
        packet["event"]["sender"]["sender_id"]["open_id"] = CONFIG[kind]["self_id"]
    else:
        packet["d"]["author"]["id"] = CONFIG[kind]["self_id"]
    hook(platform_gateway, packet)
    packet = provider.event(image=False)
    if kind == "slack":
        packet["event"].update(type="message", subtype="message_changed")
    elif kind == "lark":
        packet["header"]["event_type"] = "im.message.recalled_v1"
    else:
        packet["t"] = "MESSAGE_UPDATE"
    hook(platform_gateway, packet)
    assert client.get("/api/v1/events").json() == []


def test_queue_revocation_blocks_actions_and_messages_before_network(platform_gateway):
    kind, client, _, provider, _, route = platform_gateway
    hook(platform_gateway)
    message = decoded(tool(platform_gateway, "send_message", content=[{"type": "text", "text": "Pending"}]))
    reaction = decoded(
        tool(
            platform_gateway,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": MID[kind], "emoji": EMOJI[kind]},
        )
    )
    client.patch(f"/api/v1/routes/{route['id']}", json={"enabled": False})
    before = len(provider.requests)
    tick(platform_gateway)
    assert len(provider.requests) == before
    replies = client.get("/api/v1/replies").json()
    assert next(row for row in replies if row["id"] == message["operation_id"])["state"] == "blocked"
    actions = client.portal.call(platform_gateway[2].state.store.channel_action, reaction["operation_id"])
    assert actions["state"] == "blocked"


def test_attachment_revocation_during_download_withholds_content(platform_gateway):
    kind, client, app, provider, _, route = platform_gateway
    hook(platform_gateway)

    async def revoke(_request):
        async with app.state.store.transaction() as db:
            await db.execute(
                "UPDATE routes SET value=json_set(value,'$.read_channel_ids',json('[]')) WHERE id=?", (route["id"],)
            )

    provider.on_media = revoke
    value = tool(platform_gateway, "read", resource="image", arguments={"source_message_id": MID[kind]})
    assert value["isError"] and all(item["type"] != "image" for item in value["content"])
    assert len(provider.downloads) == 1
    assert client.get("/api/v1/replies").json() == []


def test_cursor_cannot_be_reused_in_another_conversation(platform_gateway):
    _, client, app, _, channel, route = platform_gateway
    page = decoded(tool(platform_gateway, "read", resource="messages", source="platform", arguments={"limit": 1}))
    other = client.post(
        "/api/v1/routes",
        json={
            "name": "Other",
            "channel_id": channel["id"],
            "host_id": route["host_id"],
            "conversation_kind": route["conversation_kind"],
            "conversation_id": "OTHER",
            "sender_policy": "any",
            "reply_source": "mcp",
            "action_grants": ["history.read"],
        },
    ).json()
    other_gateway = (*platform_gateway[:5], other)
    value = tool(
        other_gateway, "read", resource="messages", source="platform", arguments={"cursor": page["next_cursor"]}
    )
    assert value["isError"]
    assert client.portal.call(app.state.store.rows, "SELECT * FROM events") == []


def test_invalid_emoji_representations_fail_before_queueing(platform_gateway):
    kind, client, _, _, _, _ = platform_gateway
    hook(platform_gateway)
    for arguments in [
        {"source_message_id": MID[kind], "emoji": EMOJI[kind], "emoji_id": "123"},
        {"source_message_id": MID[kind], "emoji": {"kind": "custom", "name": "../other"}},
        {"source_message_id": MID[kind], "emoji": {"kind": "unicode", "value": "x", "channel_id": "OTHER"}},
    ]:
        assert tool(platform_gateway, "execute_action", action="reaction", arguments=arguments)["isError"]
    assert client.get("/api/v1/replies").json() == []


def test_thread_binding_is_explicit_and_preserves_native_target(platform_gateway):
    kind, client, _, provider, channel, route = platform_gateway
    packet = provider.event(image=False)
    if kind == "slack":
        packet["event"]["thread_ts"] = "1791000000.000001"
        conversation = "C1:1791000000.000001"
    elif kind == "lark":
        packet["event"]["message"]["root_id"] = "om_root"
        conversation = "oc_chat:om_root"
        original = provider.api

        async def api(request):
            if request.url.path.endswith("/messages/om_root"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {"items": [{"message_id": "om_root", "chat_id": "oc_chat", "thread_id": "omt_thread"}]},
                    },
                )
            return await original(request)

        provider.api = api
        platform_gateway[2].state.channels.http._transport = httpx.MockTransport(api)
    else:
        conversation = "123"

        async def thread_type(request):
            # Thread membership is verified from the native channel endpoint.
            if request.url.path.endswith("/channels/123"):
                return httpx.Response(200, json={"id": "123", "guild_id": "456", "type": 11})
            return await original(request)

        original = provider.api
        platform_gateway[2].state.channels.http._transport = httpx.MockTransport(thread_type)
    hook(platform_gateway, packet)
    assert client.get("/api/v1/events").json()[0]["state"] == "unmatched"
    assert decoded(tool(platform_gateway, "read", resource="messages"))["messages"] == []
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Thread",
            "channel_id": channel["id"],
            "host_id": route["host_id"],
            "conversation_kind": "thread",
            "conversation_id": conversation,
            "sender_policy": "any",
            "reply_source": "mcp",
        },
    )
    assert response.status_code == 201, response.text
    thread_gateway = (*platform_gateway[:5], response.json())
    queued = decoded(tool(thread_gateway, "send_message", content=[{"type": "text", "text": "In thread"}]))
    tick(platform_gateway)
    status = decoded(tool(thread_gateway, "get_operation", operation_id=queued["operation_id"]))
    assert status["state"] == "sent", status
    request = provider.requests[-1]
    body = json.loads(request.content)
    if kind == "slack":
        assert body["thread_ts"] == "1791000000.000001" and body["channel"] == "C1"
    elif kind == "lark":
        assert request.url.path.endswith("/om_root/reply") and body["reply_in_thread"] is True
    else:
        assert request.url.path.endswith("/channels/123/messages")


def test_partial_slack_upload_retains_confirmed_steps(platform_gateway):
    kind, _, _, provider, _, _ = platform_gateway
    if kind != "slack":
        return

    async def lose_receipt(request):
        if request.url.path.endswith("files.info"):
            raise httpx.ReadTimeout("private provider context")

    provider.on_api = lose_receipt
    queued = decoded(
        tool(
            platform_gateway,
            "send_message",
            content=[
                {
                    "type": "file",
                    "media_type": "application/pdf",
                    "filename": "a.pdf",
                    "data_base64": base64.b64encode(b"%PDF-file").decode(),
                }
            ],
        )
    )
    tick(platform_gateway)
    value = decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))
    assert value["state"] == "unknown"
    assert [item["stage"] for item in value["effects"]] == ["upload_prepared", "uploaded", "published_file"]
    attempts = len(provider.requests)
    tick(platform_gateway)
    assert len(provider.requests) == attempts


def test_bot_identity_mismatch_is_rejected_before_publication(platform_gateway):
    kind, _, app, provider, channel, _ = platform_gateway
    original = provider.api

    async def mismatched(request):
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "team_id": "T_OTHER", "user_id": "UBOT"})
        if request.url.path.endswith("/bot/v3/info"):
            return httpx.Response(200, json={"code": 0, "bot": {"open_id": "ou_other"}})
        if request.url.path.endswith("/users/@me"):
            return httpx.Response(200, json={"id": "other", "bot": True})
        return await original(request)

    app.state.channels.http._transport = httpx.MockTransport(mismatched)
    queued = decoded(tool(platform_gateway, "send_message", content=[{"type": "text", "text": "Must not publish"}]))
    tick(platform_gateway)
    assert decoded(tool(platform_gateway, "get_operation", operation_id=queued["operation_id"]))["state"] == "failed"
    assert not any(request.url.path.endswith(("chat.postMessage", "/messages")) for request in provider.requests)
    public = platform_gateway[1].get("/api/v1/connections").text
    assert SECRET not in public and ENCRYPT not in public
    assert CONFIG[kind].get("token", "a-secret-not-in-use") not in public
    assert channel["id"] in public


def test_discord_resume_advances_only_after_durable_ingress(platform_gateway, monkeypatch):
    kind, client, app, provider, channel, _ = platform_gateway
    if kind != "discord":
        return

    class Socket:
        def __init__(self, events=()):
            self.sent, self.events, self.closed = [], events, False

        async def recv(self):
            return json.dumps({"op": 10, "d": {"heartbeat_interval": 60000}})

        async def send(self, raw):
            self.sent.append(json.loads(raw))

        async def close(self):
            self.closed = True

        async def __aiter__(self):
            for event in self.events:
                yield json.dumps(event)

    async def scenario():
        connection = await app.state.store.connection(channel["id"])
        runtime = DiscordGateway(
            app.state.channels.platform(connection), app.state.store, app.state.bridge.ingest, lambda _: None
        )
        ready = {
            "op": 0,
            "t": "READY",
            "s": 1,
            "d": {"user": {"id": "999"}, "session_id": "sess1", "resume_gateway_url": "wss://gateway.discord.gg"},
        }
        socket = Socket([ready, provider.event()])
        await runtime.run(socket)
        assert socket.sent[0]["op"] == 2
        state = await app.state.store.channel_state(channel["id"], "discord_gateway")
        assert state["seq"] == 2 and state["session_id"] == "sess1"
        resume = Socket()
        await runtime.run(resume)
        assert resume.sent[0]["op"] == 6 and resume.sent[0]["d"]["seq"] == 2

        async def fail(*_args):
            raise OSError("disk unavailable")

        with monkeypatch.context() as patch:
            patch.setattr(app.state.store, "archive_channel_event", fail)
            with pytest.raises(OSError):
                await runtime.dispatch({**provider.event(), "s": 3}, Socket())
        assert (await app.state.store.channel_state(channel["id"], "discord_gateway"))["seq"] == 2
        invalid = Socket()
        await runtime.dispatch({"op": 9, "d": False}, invalid)
        assert invalid.closed
        assert await app.state.store.channel_state(channel["id"], "discord_gateway") == {}

    client.portal.call(scenario)


def test_host_delivery_uses_media_handles_and_automatic_text_reply(platform_gateway):
    kind, client, app, provider, _, route = platform_gateway
    host_requests = []
    original = provider.api

    async def with_host(request):
        if request.url.host == "host.test":
            host_requests.append(request)
            assert request.headers["authorization"] == "Bearer host-secret"
            if request.method == "POST":
                return httpx.Response(
                    201, json={"thread": {"id": "thread1"}, "entry": {"id": "entry1"}, "run": {"id": "run1"}}
                )
            return httpx.Response(200, json={"status": "completed", "output": "Automatic text reply"})
        return await original(request)

    app.state.channels.http._transport = httpx.MockTransport(with_host)
    host = client.post(
        "/api/v1/connections",
        json={"name": "a13n", "kind": "a13n", "api_url": "https://host.test/api/v1", "token": "host-secret"},
    ).json()
    updated = client.patch(
        f"/api/v1/routes/{route['id']}", json={"host_id": host["id"], "agent_id": "agent1", "reply_source": "output"}
    )
    assert updated.status_code == 200, updated.text
    hook(platform_gateway)
    tick(platform_gateway)
    assert len(host_requests) == 1
    content = json.loads(host_requests[0].content)["payload"]["content"]
    assert [part["type"] for part in content] == ["text"]
    context = json.loads(content[0]["text"].split("\n", 1)[1].split("\n\nMessage:", 1)[0])
    assert context["source_message_id"] == MID[kind] and context["image_count"] == 1
    assert context["media_access"]["tool"] == "read"
    assert context["media_access"]["source"] == "gateway"
    assert "files.slack.com" not in content[0]["text"] and "lark-resource:" not in content[0]["text"]
    tick(platform_gateway)
    replies = client.get("/api/v1/replies").json()
    assert len(replies) == 1 and replies[0]["state"] == "sent", replies
