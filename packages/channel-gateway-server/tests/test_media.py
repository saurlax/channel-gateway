"""Image and reaction workflows through signed ingress, MCP, and real storage."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import pytest
from channel_gateway_core import ChannelAddress, ImageAttachment, Message
from channel_gateway_server.adapters import onebot_message
from channel_gateway_server.app import create_app
from channel_gateway_server.channels.onebot import OneBotSession
from channel_gateway_server.channels.qq import qq_message, signing_key
from channel_gateway_server.media import MAX_IMAGE_BYTES, MediaReader, decode_image, image_url
from channel_gateway_server.models import Settings
from fastapi.testclient import TestClient
from pydantic import ValidationError

from .test_bridge import rpc

PNG_BASE64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
PNG = base64.b64decode(PNG_BASE64)
CDN = "https://multimedia.nt.qq.com.cn/download?fileid=1&rkey=private-image-key"
SECRET = "test-media-signing-secret"


class Providers:
    def __init__(self):
        self.requests = []
        self.downloads = []
        self.fail_send = False
        self.unsupported_reaction = False
        self.missing_upload_receipt = False
        self.on_upload = None
        self.on_download = None
        self.media_status = 200
        self.media_type = "image/png"
        self.media_bytes = PNG

    async def api(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "qq-token", "expires_in": 7200})
        if path.endswith("/files"):
            if self.on_upload:
                await self.on_upload()
            return httpx.Response(200, json={} if self.missing_upload_receipt else {"file_info": "private-file-info"})
        if path.endswith(("/send_msg", "/messages", "/set_msg_emoji_like")):
            if self.fail_send:
                raise httpx.ReadTimeout("private-provider-error")
            if path.endswith("set_msg_emoji_like") and self.unsupported_reaction:
                return httpx.Response(200, json={"status": "failed", "retcode": 1404})
            if path.endswith("/messages"):
                return httpx.Response(200, json={"id": "sent-qq"})
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": "501"}})
        return httpx.Response(201, json={"thread": {"id": "thread1"}, "entry": {"id": "entry1"}})

    async def media(self, request):
        self.downloads.append(request)
        if self.on_download:
            await self.on_download()
        return httpx.Response(
            self.media_status,
            content=self.media_bytes,
            headers={"content-type": self.media_type, "location": "http://127.0.0.1/private"},
        )


@pytest.fixture
def service(tmp_path):
    provider = Providers()
    api = httpx.AsyncClient(transport=httpx.MockTransport(provider.api))
    media = httpx.AsyncClient(transport=httpx.MockTransport(provider.media))
    app = create_app(
        settings=Settings(
            admin_username="admin", admin_password="test-media-administrator", data_dir=tmp_path, worker_enabled=False
        ),
        client=api,
        media_client=media,
    )
    with TestClient(app) as client:
        auth = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "test-media-administrator"},
            headers={"X-Channel-Gateway-Request": "1"},
        ).json()
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        yield client, app, provider
        client.portal.call(api.aclose)
        client.portal.call(media.aclose)


def configure(service, kind="onebots", *, read_channel_ids=None, conversation="123", host=None):
    client, _, _ = service
    config = (
        {"kind": "qq", "transport": "webhook", "app_id": "app1", "app_secret": SECRET}
        if kind == "qq"
        else {
            "kind": "onebots",
            "api_url": "https://onebot.test",
            "token": "private-bot-token",
            "self_id": "99",
            "webhook_secret": SECRET,
        }
    )
    channel = client.post("/api/v1/connections", json={"name": "Channel", **config}).json()
    if host is None:
        host = client.post("/api/v1/connections", json={"name": "MCP host", "kind": "mcp"}).json()
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Conversation",
            "channel_id": channel["id"],
            "conversation_kind": "group",
            "conversation_id": conversation,
            "host_id": host["id"],
            **({"agent_id": "agt_test"} if host["kind"] == "a13n" else {}),
            "allowed_user_ids": ["7"],
            "read_channel_ids": read_channel_ids,
            "action_grants": ["message.react"] if kind == "onebots" else [],
        },
    )
    assert response.status_code == 201, response.text
    return channel, response.json(), host


def ingest(service, channel, *, mid="1", sender="7", conversation="123", message=None):
    client, _, _ = service
    if channel["kind"] == "qq":
        event = {
            "op": 0,
            "t": "GROUP_AT_MESSAGE_CREATE",
            "d": {
                "id": mid,
                "author": {"member_openid": sender},
                "group_openid": conversation,
                "timestamp": "2026-10-10T12:00:00+08:00",
                "content": "",
                "attachments": [{"content_type": "image/png", "url": CDN}],
            },
        }
        raw = json.dumps(event).encode()
        timestamp = "1791580000"
        headers = {
            "X-Signature-Timestamp": timestamp,
            "X-Signature-Ed25519": signing_key(SECRET).sign(timestamp.encode() + raw).hex(),
        }
    else:
        event = {
            "post_type": "message",
            "message_type": "group",
            "self_id": 99,
            "user_id": sender,
            "group_id": conversation,
            "message_id": mid,
            "time": 1720000000,
            "message": message if message is not None else [{"type": "image", "data": {"url": CDN}}],
        }
        raw = json.dumps(event).encode()
        headers = {"X-Signature": "sha1=" + hmac.new(SECRET.encode(), raw, hashlib.sha1).hexdigest()}
    response = client.post(f"/api/v1/hooks/{channel['kind']}/{channel['id']}", content=raw, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def call(service, configured, name, **arguments):
    response = rpc(service[0], configured["agent_token"], "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def result_json(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


@pytest.mark.parametrize("kind", ["onebots", "qq"])
def test_image_only_ingress_native_mcp_reads_and_private_urls(service, kind):
    client, app, provider = service
    channel, configured, _ = configure(service, kind)
    first = ingest(service, channel)
    duplicate = ingest(service, channel)
    if kind == "onebots":
        assert first["state"] == "accepted" and duplicate["state"] == "duplicate"
    assert len(client.get("/api/v1/events").json()) == 1
    assert not provider.downloads
    history = result_json(call(service, configured, "read", resource="messages", arguments={}))["messages"]
    assert history[0]["source_message_id"] == "1" and history[0]["images"] == [{"index": 0}]
    assert history[0]["text"] == "[Image]"
    assert "private-image-key" not in str(history) + client.get("/api/v1/events").text
    image = call(service, configured, "read", resource="image", arguments={"source_message_id": "1"})
    assert not image.get("isError"), image
    assert image["content"][0] == {"type": "image", "data": PNG_BASE64, "mimeType": "image/png"}
    assert not provider.downloads[0].headers.get("authorization")
    assert not provider.downloads[0].headers.get("cookie")
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    assert not call(service, configured, "read", resource="image", arguments={"source_message_id": "1"}).get("isError")


def test_mcp_image_lookup_is_not_limited_to_recent_history(service):
    client, app, _ = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    stored = client.portal.call(app.state.store.connection, channel["id"])

    async def populate():
        for index in range(2, 105):
            message = Message(
                address=ChannelAddress(channel="onebots", account_id=channel["id"], conversation_id="group:123"),
                source_message_id=str(index),
                sender_id="7",
                text="Later text",
                occurred_at=datetime.now(UTC),
            )
            await app.state.bridge.ingest(stored, message)

    client.portal.call(populate)
    assert not call(service, configured, "read", resource="image", arguments={"source_message_id": "1"}).get("isError")


@pytest.mark.parametrize(
    "mode", ["disabled", "read_revoked", "other_conversation", "other_sender", "deleted", "rotated"]
)
def test_image_access_is_scoped_and_revocable(service, mode):
    client, _, provider = service
    channel, configured, _ = configure(service)
    ingest(
        service,
        channel,
        conversation="456" if mode == "other_conversation" else "123",
        sender="8" if mode == "other_sender" else "7",
    )
    if mode == "disabled":
        client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": False})
    elif mode == "read_revoked":
        client.patch(f"/api/v1/routes/{configured['id']}", json={"read_channel_ids": []})
    elif mode == "deleted":
        client.delete(f"/api/v1/connections/{channel['id']}")
    elif mode == "rotated":
        assert client.post(f"/api/v1/routes/{configured['id']}/token").status_code == 200
    response = rpc(
        client,
        configured["agent_token"],
        "tools/call",
        {"name": "read", "arguments": {"resource": "image", "arguments": {"source_message_id": "1"}}},
    )
    assert response.status_code == 401 or response.json()["result"].get("isError")
    assert not provider.downloads


def test_revoke_image_access_during_download(service):
    client, app, provider = service
    channel, configured, _ = configure(service)
    ingest(service, channel)

    async def revoke():
        current = await app.state.store.route(configured["id"])
        assert await app.state.store.update_route(configured["id"], {**current, "enabled": False}, current)

    provider.on_download = revoke
    assert call(service, configured, "read", resource="image", arguments={"source_message_id": "1"}).get("isError")
    assert len(provider.downloads) == 1
    assert client.portal.call(app.state.store.route, configured["id"])["enabled"] is False


def test_account_image_read_grants_do_not_grant_actions(service):
    client, _, _ = service
    channel, configured, _ = configure(service, read_channel_ids=["*"])
    ingest(service, channel, sender="8", conversation="456")
    listed = result_json(call(service, configured, "discover", resource="conversations"))["conversations"]
    readonly = next(row for row in listed if row["conversation_id"] == "456")
    assert readonly["can_read"] and not readonly["can_reply"]
    assert not call(
        service, configured, "read", resource="image", arguments={"source_message_id": "1"}, route_id=readonly["id"]
    ).get("isError")
    for name, arguments in [
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
        ("execute_action", {"action": "reaction", "arguments": {"source_message_id": "1", "emoji_id": "76"}}),
    ]:
        assert call(service, configured, name, route_id=readonly["id"], **arguments).get("isError")
    assert not client.get("/api/v1/replies").json()


@pytest.mark.parametrize("kind", ["onebots", "qq"])
def test_image_send_uses_durable_queue_and_platform_receipts(service, kind):
    client, app, provider = service
    channel, configured, _ = configure(service, kind)
    ingest(service, channel)
    receipt = result_json(
        call(
            service,
            configured,
            "send_message",
            content=[
                {"type": "text", "text": "A picture"},
                {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64},
            ],
        )
    )
    assert receipt["state"] == "queued" and not provider.requests
    assert PNG_BASE64 not in client.get("/api/v1/replies").text
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    client.portal.call(app.state.bridge.tick)
    reply = client.get("/api/v1/replies").json()[0]
    assert reply["state"] == "sent" and reply["message_id"] == ("sent-qq" if kind == "qq" else "501")
    body = json.loads(provider.requests[-1].content)
    if kind == "onebots":
        assert provider.requests[-1].url.path == "/send_msg"
        assert body["message"] == [
            {"type": "text", "data": {"text": "A picture"}},
            {"type": "image", "data": {"file": "base64://" + PNG_BASE64}},
        ]
    else:
        upload = json.loads(
            next(request for request in provider.requests if request.url.path.endswith("/files")).content
        )
        assert upload == {"file_type": 1, "file_name": "image.png", "file_data": PNG_BASE64, "srv_send_msg": False}
        assert provider.requests[-1].url.path == "/v2/groups/123/messages"
        assert body == {
            "content": "A picture",
            "msg_type": 7,
            "msg_id": "1",
            "msg_seq": 1,
            "media": {"file_info": "private-file-info"},
        }
        assert "private-file-info" not in client.get("/api/v1/replies").text


@pytest.mark.parametrize("kind", ["onebots", "qq"])
def test_lost_image_send_is_not_automatically_repeated(service, kind):
    client, app, provider = service
    channel, configured, _ = configure(service, kind)
    ingest(service, channel)
    result_json(
        call(
            service,
            configured,
            "send_message",
            content=[{"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}],
        )
    )
    provider.fail_send = True
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies").json()[0]["state"] == "unknown"
    count = len(provider.requests)
    client.portal.call(app.state.bridge.tick)
    assert len(provider.requests) == count


def test_qq_upload_does_not_send_after_route_revocation(service):
    client, app, provider = service
    channel, configured, _ = configure(service, "qq")
    ingest(service, channel)
    result_json(
        call(
            service,
            configured,
            "send_message",
            content=[{"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}],
        )
    )

    async def revoke():
        current = await app.state.store.route(configured["id"])
        assert await app.state.store.update_route(configured["id"], {**current, "enabled": False}, current)

    provider.on_upload = revoke
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies").json()[0]["state"] == "blocked"
    assert not any(request.url.path.endswith("/messages") for request in provider.requests)


@pytest.mark.parametrize("add", [True, False])
def test_optional_onebot_reactions_add_and_remove(service, add):
    client, app, provider = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    listing = result_json(call(service, configured, "discover", resource="conversations"))["conversations"]
    assert listing[0]["capabilities"]["message_reactions"] == "optional:set_msg_emoji_like"
    result_json(
        call(
            service,
            configured,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": "1", "emoji_id": "76", "add": add},
        )
    )
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/actions").json()[0]["state"] == "succeeded"
    assert provider.requests[-1].url.path == "/set_msg_emoji_like"
    assert json.loads(provider.requests[-1].content) == {"message_id": "1", "emoji_id": "76", "set": add}


def test_reaction_target_sender_revocation_blocks_queued_action(service):
    client, app, provider = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    result_json(
        call(
            service,
            configured,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": "1", "emoji_id": "76"},
        )
    )
    client.patch(f"/api/v1/routes/{configured['id']}", json={"allowed_user_ids": ["8"]})
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/actions").json()[0]["state"] == "blocked"
    assert not provider.requests


def test_unsupported_provider_reactions_do_not_fall_back_to_messages(service):
    client, app, provider = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    provider.unsupported_reaction = True
    result_json(
        call(
            service,
            configured,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": "1", "emoji_id": "76"},
        )
    )
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/actions").json()[0]["state"] == "unknown"
    client.portal.call(app.state.bridge.tick)
    assert len(provider.requests) == 1


def test_official_qq_group_reactions_are_rejected_before_queueing(service):
    client, _, provider = service
    channel, configured, _ = configure(service, "qq")
    ingest(service, channel)
    assert call(
        service, configured, "execute_action", action="reaction", arguments={"source_message_id": "1", "emoji_id": "76"}
    ).get("isError")
    assert not client.get("/api/v1/replies").json() and not provider.requests


@pytest.mark.parametrize("mid,emoji", [("missing", "76"), ("1", "😀"), ("1", ""), ("1", "1" * 21)])
def test_reactions_require_known_source_messages_and_platform_emoji_ids(service, mid, emoji):
    client, _, _ = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    assert call(
        service,
        configured,
        "execute_action",
        action="reaction",
        arguments={"source_message_id": mid, "emoji_id": emoji},
    ).get("isError")
    assert not client.get("/api/v1/replies").json()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/image",
        "file:///etc/passwd",
        "https://localhost/image",
        "https://media.internal/image",
        "https://192.168.1.1/image",
        "https://[::1]/image",
        "https://169.254.169.254/image",
        "https://user:password@gchat.qpic.cn/image",
        "https://multimedia.nt.qq.com:444/image",
        "https://gchat.qpic.cn/image#secret",
        "https://gchat.qpic.cn\\@127.0.0.1/image",
    ],
)
def test_invalid_media_download_urls_are_rejected(url):
    assert image_url(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://grouptalk.c2c.qq.com:8443/file",
        "https://user:secret@grouptalk.c2c.qq.com/file",
        "https://@grouptalk.c2c.qq.com/file",
        "https://grouptalk.c2c.qq.com/file#fragment",
        "https://grouptalk.c2c.qq.com/file\n",
        "https://gzc-download.ftn.qq.com:8443/file",
        "https://user:secret@gzc-download.ftn.qq.com/file",
    ],
)
def test_media_urls_reject_credentials_fragments_and_nonstandard_ports(url):
    assert image_url(url) is None


@pytest.mark.parametrize("host", ["grouptalk.c2c.qq.com", "media.example.test", "other.qq.com"])
@pytest.mark.parametrize("scheme", ["https:", "http:", ""])
def test_received_media_urls_accept_public_hosts_without_a_domain_allowlist(host, scheme):
    assert image_url(f"{scheme}//{host}/file?key=private") == f"https://{host}/file?key=private"


@pytest.mark.parametrize("problem", ["redirect", "oversize", "not_image", "wrong_signature"])
def test_image_download_failures_do_not_return_pixels(service, problem):
    _, _, provider = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    if problem == "redirect":
        provider.media_status = 302
    elif problem == "oversize":
        provider.media_bytes = PNG + b"x" * MAX_IMAGE_BYTES
    elif problem == "not_image":
        provider.media_type = "text/html"
    else:
        provider.media_type = "image/jpeg"
    response = call(service, configured, "read", resource="image", arguments={"source_message_id": "1"})
    assert response.get("isError")
    assert all(content["type"] != "image" for content in response["content"])
    assert len(provider.downloads) == 1
    assert "private-image-key" not in str(response)


@pytest.mark.parametrize(
    "encoded,media_type",
    [("not base64", "image/png"), (PNG_BASE64, "image/jpeg"), (PNG_BASE64, "text/html"), ("", "image/png")],
)
def test_invalid_outbound_images_are_not_queued(service, encoded, media_type):
    client, _, provider = service
    _, configured, _ = configure(service)
    assert call(
        service,
        configured,
        "send_message",
        content=[{"type": "image", "media_type": media_type, "filename": "image.png", "data_base64": encoded}],
    ).get("isError")
    assert not client.get("/api/v1/replies").json() and not provider.requests


def test_core_accepts_image_only_and_rejects_empty_messages():
    fields = dict(
        address=ChannelAddress(channel="qq", account_id="account", conversation_id="group:123"),
        source_message_id="source",
        sender_id="7",
        occurred_at=datetime.now(UTC),
    )
    message = Message(**fields, images=(ImageAttachment(url=CDN),))
    assert Message.model_validate_json(message.model_dump_json()) == message
    with pytest.raises(ValidationError, match="text or images"):
        Message(**fields)


def test_onebot_cq_images_faces_and_escaped_text():
    event = dict(
        post_type="message",
        message_type="group",
        self_id="99",
        user_id="7",
        group_id="123",
        message_id="1",
        time=1720000000,
    )
    cq = "[CQ:image,url=https://gchat.qpic.cn/image?a=1&amp;b=2][CQ:face,id=76]&#91;hello&#93;"
    message = onebot_message({**event, "message": cq}, {"id": "account", "self_id": "99"})
    assert message and message.images[0].url == "https://gchat.qpic.cn/image?a=1&b=2"
    assert message.text == "[face:76][hello]"
    with pytest.raises(ValueError, match="image count"):
        onebot_message(
            {**event, "message": [{"type": "image", "data": {"url": CDN}}] * 5}, {"id": "account", "self_id": "99"}
        )


def test_qq_http_cdn_urls_are_upgraded_and_nonimages_are_ignored():
    event = {
        "op": 0,
        "t": "C2C_MESSAGE_CREATE",
        "d": {
            "id": "1",
            "author": {"user_openid": "7"},
            "timestamp": "2026-10-10T12:00:00+08:00",
            "attachments": [{"content_type": "image/png", "url": "http://gchat.qpic.cn/image"}],
        },
    }
    message = qq_message(event, {"id": "account", "kind": "qq"})
    assert message and message.images[0].url == "https://gchat.qpic.cn/image"
    event["d"]["attachments"][0]["content_type"] = "video/mp4"
    assert qq_message(event, {"id": "account", "kind": "qq"}).attachments[0].kind == "video"


def test_a13n_receives_native_url_parts_and_verified_image_context(service):
    client, app, provider = service
    host = client.post(
        "/api/v1/connections",
        json={"name": "a13n", "kind": "a13n", "api_url": "https://host.test/api/v1", "token": "private-host-token"},
    ).json()
    channel, _, _ = configure(service, "qq", host=host)
    ingest(service, channel)
    client.portal.call(app.state.bridge.tick)
    request = provider.requests[0]
    body = json.loads(request.content)
    assert request.url.path == "/api/v1/threads"
    assert request.headers["authorization"] == "Bearer private-host-token"
    assert body["payload"]["content"][1] == {"type": "url", "url": CDN}
    assert '"source_message_id": "1"' in body["payload"]["content"][0]["text"]
    assert '"image_count": 1' in body["payload"]["content"][0]["text"]
    assert not provider.downloads
    assert "private-image-key" not in client.get("/api/v1/events").text


def test_qq_missing_upload_receipt_never_sends_a_message(service):
    client, app, provider = service
    channel, configured, _ = configure(service, "qq")
    ingest(service, channel)
    result_json(
        call(
            service,
            configured,
            "send_message",
            content=[{"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}],
        )
    )
    provider.missing_upload_receipt = True
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies").json()[0]["state"] == "unknown"
    assert not any(request.url.path.endswith("/messages") for request in provider.requests)


def test_unsupported_qq_image_format_is_rejected_without_upload(service):
    client, _, provider = service
    _, configured, _ = configure(service, "qq")
    gif = base64.b64encode(b"GIF89a" + b"0" * 20).decode()
    assert call(
        service,
        configured,
        "send_message",
        content=[{"type": "image", "media_type": "image/gif", "filename": "image.png", "data_base64": gif}],
    ).get("isError")
    assert not provider.requests and not client.get("/api/v1/replies").json()


@pytest.mark.parametrize("action", ["image", "reaction"])
def test_onebot_socket_images_and_reactions_use_echo_receipts(service, action):
    client, app, _ = service
    channel = client.post(
        "/api/v1/connections",
        json={"name": "Socket", "kind": "onebots", "transport": "ws_reverse", "token": "socket-token", "self_id": "99"},
    ).json()
    host = client.post("/api/v1/connections", json={"name": "MCP", "kind": "mcp"}).json()
    configured = client.post(
        "/api/v1/routes",
        json={
            "name": "Socket route",
            "action_grants": ["message.react"],
            "channel_id": channel["id"],
            "host_id": host["id"],
            "conversation_kind": "group",
            "conversation_id": "123",
            "allowed_user_ids": ["7"],
        },
    ).json()
    sent = []

    async def arrange():
        connection = await app.state.store.connection(channel["id"])

        async def send(raw):
            request = json.loads(raw)
            sent.append(request)
            await session.dispatch(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "echo": request["echo"],
                        "data": {"message_id": "502"} if action == "image" else None,
                    }
                )
            )

        session = OneBotSession(connection, app.state.bridge.ingest, send, app.state.store)
        app.state.channels.sessions[channel["id"]] = session
        await session.dispatch(
            json.dumps(
                {
                    "post_type": "message",
                    "message_type": "group",
                    "self_id": 99,
                    "user_id": 7,
                    "group_id": 123,
                    "message_id": 1,
                    "time": 1720000000,
                    "message": [{"type": "image", "data": {"url": CDN}}],
                }
            )
        )

    client.portal.call(arrange)
    arguments = (
        {"content": [{"type": "image", "data_base64": PNG_BASE64, "media_type": "image/png", "filename": "test.png"}]}
        if action == "image"
        else {"action": "reaction", "arguments": {"source_message_id": "1", "emoji_id": "76"}}
    )
    result_json(call(service, configured, "send_message" if action == "image" else "execute_action", **arguments))
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies" if action == "image" else "/api/v1/actions").json()[0]["state"] == (
        "sent" if action == "image" else "succeeded"
    )
    assert sent[0]["action"] == ("send_msg" if action == "image" else "set_msg_emoji_like")
    if action == "image":
        assert sent[0]["params"]["message"] == [{"type": "image", "data": {"file": "base64://" + PNG_BASE64}}]


def test_media_reader_strips_default_credentials_and_cookies():
    import asyncio

    async def verify():
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(
                200, content=PNG, headers={"content-type": "image/png", "set-cookie": "tracking=private; Path=/"}
            )

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(respond),
            headers={"authorization": "private-token"},
            cookies={"initial": "private-cookie"},
        ) as http:
            reader = MediaReader(http)
            for _ in range(2):
                assert await reader.read(CDN) == (PNG, "image/png")
        assert all("authorization" not in request.headers and "cookie" not in request.headers for request in requests)

    asyncio.run(verify())


def test_streamed_image_size_limit_without_content_length():
    import asyncio

    class Oversized(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(65):
                yield b"x" * 65536

    async def verify():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, headers={"content-type": "image/png"}, stream=Oversized())
            )
        ) as http:
            with pytest.raises(ValueError, match="size limit"):
                await MediaReader(http).read(CDN)

    asyncio.run(verify())


def test_outgoing_base64_decoded_size_is_bounded():
    with pytest.raises(ValueError, match="at most 4 MiB"):
        decode_image(base64.b64encode(PNG + b"x" * MAX_IMAGE_BYTES).decode(), "image/png")


@pytest.mark.parametrize("content_type", ["", "application/octet-stream", "binary/octet-stream"])
def test_generic_cdn_media_types_are_inferred_from_image_signature(service, content_type):
    _, _, provider = service
    channel, configured, _ = configure(service)
    ingest(service, channel)
    provider.media_type = content_type
    result = call(service, configured, "read", resource="image", arguments={"source_message_id": "1"})
    assert not result.get("isError"), result
    assert result["content"][0]["mimeType"] == "image/png"
