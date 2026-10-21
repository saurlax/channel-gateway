"""QQ platform contracts through signed callbacks, MCP, durable actions and HTTP."""

import base64
import hashlib
import json
import math
from types import SimpleNamespace

import httpx
import pytest
from channel_gateway_server.adapters import host_body
from channel_gateway_server.app import create_app
from channel_gateway_server.channels.qq import QQ_INTENTS, qq_message, signing_key
from channel_gateway_server.channels.qq_operations import GRANTS
from channel_gateway_server.models import Settings
from fastapi.testclient import TestClient

from .test_bridge import rpc
from .test_media import CDN, PNG, PNG_BASE64, SECRET


class Platform:
    def __init__(self):
        self.requests = []
        self.uploads = []
        self.on_request = None
        self.on_upload = None
        self.failure = None
        self.counter = 0
        self.host_output = "Automatic reply"
        self.media_data = b"RIFFwave-audio"
        self.media_type = "audio/wav"

    async def api(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "platform-token", "expires_in": 7200})
        if request.url.host == "host.test":
            if request.method == "POST":
                return httpx.Response(
                    201, json={"thread": {"id": "thread1"}, "entry": {"id": "entry1"}, "run": {"id": "run1"}}
                )
            return httpx.Response(200, json={"status": "completed", "output": self.host_output})
        assert request.headers["authorization"] == "QQBot platform-token"
        if self.on_request:
            await self.on_request(request)
        if self.failure:
            if self.failure == "timeout":
                raise httpx.ReadTimeout("private upstream body")
            return httpx.Response(self.failure, json={"code": 12345, "message": "private upstream body"})
        if path.endswith("/upload_prepare"):
            body = json.loads(request.content)
            block = 2 * 1024 * 1024
            return httpx.Response(
                200,
                json={
                    "upload_id": "private-upload",
                    "block_size": str(block),
                    "parts": [
                        {
                            "index": i,
                            "presigned_url": f"https://bucket.cos.ap-guangzhou.myqcloud.com/upload?signature=private&part={i}",
                            "block_size": str(block),
                        }
                        for i in range(math.ceil(int(body["file_size"]) / block))
                    ],
                },
            )
        if path.endswith("/upload_part_finish"):
            return httpx.Response(204)
        if path.endswith("/files"):
            return httpx.Response(200, json={"file_info": "private-file-info"})
        if path.endswith("/stream_messages"):
            return httpx.Response(200, json={"id": "stream-native"})
        if path.endswith("/messages") and request.method == "POST":
            self.counter += 1
            return httpx.Response(200, json={"id": f"out{self.counter}", "ext_info": {"ref_idx": "REFIDX_BOT"}})
        if path.startswith("/channels/") and request.method == "GET" and path.count("/") == 2:
            return httpx.Response(
                200,
                json={"id": path.split("/")[-1], "guild_id": "foreign-guild" if path.endswith("foreign") else "guildA"},
            )
        if request.method == "GET" and path.endswith(("/channels", "/schedules")):
            return httpx.Response(200, json=[{"id": "childB", "name": "Resource"}])
        if request.method in {"DELETE", "PUT", "PATCH"}:
            return httpx.Response(204)
        return httpx.Response(
            200,
            json={
                "id": "resource1",
                "name": "Resource",
                "auth_token": "private-context",
                "file_info": "private-file-info",
            },
        )

    async def media(self, request):
        self.uploads.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if self.on_upload:
            await self.on_upload(request)
        if request.method == "PUT":
            return httpx.Response(200)
        return httpx.Response(200, content=self.media_data, headers={"content-type": self.media_type})


@pytest.fixture
def platform(tmp_path):
    provider = Platform()
    api = httpx.AsyncClient(transport=httpx.MockTransport(provider.api))
    media = httpx.AsyncClient(transport=httpx.MockTransport(provider.media))
    app = create_app(
        settings=Settings(
            admin_username="admin",
            admin_password="platform-test-administrator",
            data_dir=tmp_path,
            worker_enabled=False,
        ),
        client=api,
        media_client=media,
    )
    with TestClient(app) as client:
        auth = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "platform-test-administrator"},
            headers={"X-Channel-Gateway-Request": "1"},
        ).json()
        client.headers["X-CSRF-Token"] = auth["csrf_token"]
        channel = client.post(
            "/api/v1/connections",
            json={"name": "QQ", "kind": "qq", "transport": "webhook", "app_id": "app1", "app_secret": SECRET},
        ).json()
        host = client.post("/api/v1/connections", json={"name": "MCP", "kind": "mcp"}).json()
        yield client, app, provider, channel, host
        client.portal.call(api.aclose)
        client.portal.call(media.aclose)


def configure(platform, kind="group", grants=(), conversation=None, **overrides):
    client, _, _, channel, host = platform
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Scope",
            "channel_id": channel["id"],
            "conversation_kind": kind,
            "conversation_id": conversation
            or {"private": "7", "group": "123", "guild": "childA", "guild_dm": "dmA"}[kind],
            "host_id": host["id"],
            "sender_policy": "any",
            "action_grants": list(grants),
            **overrides,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def hook(platform, packet):
    client, _, _, channel, _ = platform
    raw = json.dumps(packet).encode()
    timestamp = "1791580000"
    response = client.post(
        f"/api/v1/hooks/qq/{channel['id']}",
        content=raw,
        headers={
            "X-Signature-Timestamp": timestamp,
            "X-Signature-Ed25519": signing_key(SECRET).sign(timestamp.encode() + raw).hex(),
        },
    )
    assert response.status_code == 200, response.text


def incoming(platform, kind="group", mid="source1", **extra):
    event = {
        "op": 0,
        "t": {
            "private": "C2C_MESSAGE_CREATE",
            "group": "GROUP_MESSAGE_CREATE",
            "guild": "AT_MESSAGE_CREATE",
            "guild_dm": "DIRECT_MESSAGE_CREATE",
        }[kind],
        "d": {
            "id": mid,
            "content": "Hello",
            "author": {"user_openid": "7"}
            if kind == "private"
            else {"member_openid": "7"}
            if kind == "group"
            else {"id": "7"},
            "timestamp": "2026-10-20T12:00:00+08:00",
            "group_openid": "123",
            "channel_id": "childA",
            "guild_id": "dmA" if kind == "guild_dm" else "guildA",
            "src_guild_id": "guildA",
            **extra,
        },
    }
    hook(platform, event)
    return event


def call(platform, route, name, **arguments):
    response = rpc(platform[0], route["agent_token"], "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def decoded(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def tick(platform):
    platform[0].portal.call(platform[1].state.bridge.tick)


def status(platform, route, receipt):
    return decoded(call(platform, route, "get_operation", operation_id=receipt["action_id"]))


@pytest.mark.parametrize(
    "kind,path",
    [
        ("private", "/v2/users/7/messages"),
        ("group", "/v2/groups/123/messages"),
        ("guild", "/channels/childA/messages"),
        ("guild_dm", "/dms/dmA/messages"),
    ],
)
def test_each_qq_conversation_kind_routes_and_sends_to_its_own_native_endpoint(platform, kind, path):
    client, _, provider, channel, _ = platform
    route = configure(platform, kind)
    incoming(platform, kind)
    result = decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Native reply"}]))
    assert result["state"] == "queued"
    tick(platform)
    sent = [r for r in provider.requests if r.url.path.endswith("/messages")]
    assert sent[-1].url.path == path
    body = json.loads(sent[-1].content)
    assert body["msg_id"] == "source1" and body["content"] == "Native reply"
    assert ("msg_type" in body) == (kind in {"private", "group"})
    assert client.get("/api/v1/replies").json()[0]["state"] == "sent"
    recorded = decoded(call(platform, route, "read", resource="messages", arguments={}))["messages"]
    assert any(m["direction"] == "outgoing" and m["text"] == "Native reply" for m in recorded)
    history = client.get(
        "/api/v1/history",
        params={"channel_id": channel["id"], "conversation_kind": kind, "conversation_id": route["conversation_id"]},
    ).json()
    assert {m["text"] for m in history["messages"]} == {"Hello", "Native reply"}


def test_markdown_keyboard_and_native_quote_are_content_not_actions(platform):
    _, _, provider, _, _ = platform
    route = configure(platform)
    incoming(platform, message_scene={"ext": ["msg_idx=REFIDX_SOURCE", "auth_token=private-source"]})
    keyboard = {"content": {"rows": [{"buttons": [{"id": "ok", "action": {"type": 1, "data": "ok"}}]}]}}
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[{"type": "text", "text": "**Answer**", "format": "markdown"}],
            reply_to="source1",
            native={"qq": {"keyboard": keyboard}},
        )
    )
    tick(platform)
    body = json.loads(provider.requests[-1].content)
    assert body["markdown"] == {"content": "**Answer**"} and "content" not in body
    assert body["keyboard"] == keyboard and body["message_reference"] == {"message_id": "REFIDX_SOURCE"}
    assert not platform[0].get("/api/v1/actions").json()


def test_actions_have_independent_grants_and_never_appear_as_sent_replies(platform):
    client, _, provider, channel, _ = platform
    host = client.post(
        "/api/v1/connections",
        json={"name": "API host", "kind": "a13n", "api_url": "https://host.test", "token": "host-token"},
    ).json()
    route = configure(platform, "guild", ["message.react"], host_id=host["id"], agent_id="agent1")
    incoming(platform, "guild")
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="reaction",
            arguments={"source_message_id": "source1", "emoji_id": "203", "emoji_type": 1},
        )
    )
    # Directly advance the action, avoiding unrelated Host input execution.
    action = client.portal.call(platform[1].state.store.channel_action, receipt["action_id"])
    client.portal.call(platform[1].state.channels.execute_action, action)
    assert status(platform, route, receipt)["state"] == "succeeded"
    assert (
        provider.requests[-1].method == "PUT"
        and provider.requests[-1].url.path == "/channels/childA/messages/source1/reactions/1/203"
    )
    assert not client.get("/api/v1/replies").json()
    history = client.get(
        "/api/v1/history",
        params={"channel_id": channel["id"], "conversation_kind": "guild", "conversation_id": "childA"},
    ).json()
    assert any(row["direction"] == "action" and row["text"] == "[reaction]" for row in history["messages"])


@pytest.mark.parametrize("kind", ["private", "group", "guild_dm"])
def test_official_reactions_are_rejected_outside_guild_channels(platform, kind):
    route = configure(platform, kind, ["message.react"])
    incoming(platform, kind)
    assert call(
        platform,
        route,
        "execute_action",
        action="reaction",
        arguments={"source_message_id": "source1", "emoji_id": "203"},
    ).get("isError")
    assert not platform[2].requests


def test_native_resources_and_management_are_denied_by_default(platform):
    route = configure(platform)
    catalog = decoded(call(platform, route, "discover", resource="operations"))["native"]
    assert catalog and all(not item["authorized"] for item in catalog)
    assert call(
        platform, route, "read", resource="native", arguments={"operation": "qq.get_group", "parameters": {}}
    ).get("isError")
    assert call(
        platform,
        route,
        "execute_action",
        action="native",
        arguments={"operation": "qq.set_group_mutes", "parameters": {"members": []}},
    ).get("isError")
    assert not platform[2].requests and not platform[0].get("/api/v1/actions").json()


@pytest.mark.parametrize(
    "name,args,method,path",
    [
        ("get_group", {}, "GET", "/v2/groups/123/info"),
        ("list_group_members", {"limit": 20}, "GET", "/v2/groups/123/members"),
        ("get_group_member", {"member_openid": "member8"}, "GET", "/v2/groups/123/members/member8"),
        ("list_join_requests", {}, "GET", "/v2/groups/123/join_request_list"),
        ("get_group_mutes", {}, "GET", "/v2/groups/123/restrict_chat_setting"),
        (
            "set_group_mutes",
            {"members": [{"op": "add", "member_openid": "member8", "mute_expire_at": "2026-10-21T10:00:00+08:00"}]},
            "POST",
            "/v2/groups/123/restrict_chat_setting",
        ),
        (
            "approve_join_request",
            {"member_openid": "member8", "op": "approve"},
            "POST",
            "/v2/groups/123/approval_join_request/member8",
        ),
        ("remove_group_members", {"member_openids": ["member8"]}, "POST", "/v2/groups/123/batch_remove_members"),
        (
            "set_group_blacklist",
            {"op": "del", "member_openids": ["member8"]},
            "POST",
            "/v2/groups/123/member_blacklist",
        ),
        ("set_menu", {"menu": {"items": []}}, "PUT", "/v2/menu"),
        ("create_panel", {"scope": "group", "panel": {"items": []}, "group_openids": ["123"]}, "POST", "/v2/panels"),
        (
            "set_panel_targets",
            {"panel_id": "panel1", "op": "add", "group_openids": ["123"]},
            "PUT",
            "/v2/panels/panel1/target",
        ),
        ("create_join_strategy", {"group_openids": ["123"]}, "POST", "/v2/groups/join_approval_strategy"),
        (
            "set_join_whitelist",
            {"strategy_id": "strategy1", "op": "add", "whitelist_users": ["1234567"]},
            "POST",
            "/v2/groups/join_approval_strategy/strategy1/whitelist_users",
        ),
        (
            "execute_join_strategy",
            {"strategy_id": "strategy1"},
            "POST",
            "/v2/groups/join_approval_strategy/strategy1/execute",
        ),
    ],
)
def test_group_and_account_operations_follow_official_methods_and_scoped_paths(platform, name, args, method, path):
    route = configure(platform, grants=GRANTS)
    tool = "read" if method == "GET" else "execute_action"
    selector = {"resource": "native"} if method == "GET" else {"action": "native"}
    receipt = decoded(
        call(platform, route, tool, **selector, arguments={"operation": "qq." + name, "parameters": args})
    )
    if method != "GET":
        tick(platform)
        assert status(platform, route, receipt)["state"] == "succeeded"
    request = platform[2].requests[-1]
    assert request.method == method and request.url.path == path
    if method == "GET":
        assert "private-context" not in str(receipt) and "private-file-info" not in str(receipt)


@pytest.mark.parametrize(
    "name,args,method,path",
    [
        ("get_guild", {}, "GET", "/guilds/guildA"),
        ("list_subchannels", {}, "GET", "/guilds/guildA/channels"),
        ("list_guild_members", {"limit": 100}, "GET", "/guilds/guildA/members"),
        ("create_subchannel", {"name": "childB", "type": 0}, "POST", "/guilds/guildA/channels"),
        ("update_subchannel", {"target_channel_id": "childB", "name": "Renamed"}, "PATCH", "/channels/childB"),
        ("delete_subchannel", {"target_channel_id": "childB"}, "DELETE", "/channels/childB"),
        ("create_role", {"name": "Team", "color": 4294967295}, "POST", "/guilds/guildA/roles"),
        (
            "add_role_member",
            {"role_id": "role1", "user_id": "member8"},
            "PUT",
            "/guilds/guildA/members/member8/roles/role1",
        ),
        (
            "set_member_permissions",
            {"user_id": "member8", "add": "1", "remove": "0"},
            "PUT",
            "/channels/childA/members/member8/permissions",
        ),
        ("mute_guild", {"mute_seconds": "60", "user_ids": ["member8"]}, "PATCH", "/guilds/guildA/mute"),
        (
            "remove_guild_member",
            {"user_id": "member8", "delete_history_msg_days": -1},
            "DELETE",
            "/guilds/guildA/members/member8",
        ),
        (
            "create_announcement",
            {"channel_id": "childB", "message_id": "native-message"},
            "POST",
            "/guilds/guildA/announces",
        ),
        ("add_pin", {"message_id": "native-message"}, "PUT", "/channels/childA/pins/native-message"),
        (
            "create_schedule",
            {"schedule": {"name": "Meeting", "jump_channel_id": "childB"}},
            "POST",
            "/channels/childA/schedules",
        ),
        ("create_thread", {"title": "Title", "content": "Body", "format": 1}, "PUT", "/channels/childA/threads"),
        (
            "control_audio",
            {"status": 0, "audio_url": "https://example.com/audio.mp3"},
            "POST",
            "/channels/childA/audio",
        ),
        ("join_microphone", {}, "PUT", "/channels/childA/mic"),
        ("leave_microphone", {}, "DELETE", "/channels/childA/mic"),
        (
            "request_api_permission",
            {
                "channel_id": "childB",
                "api_identify": {"path": "/guilds/{guild_id}", "method": "GET"},
                "desc": "Read guild",
            },
            "POST",
            "/guilds/guildA/api_permission/demand",
        ),
    ],
)
def test_guild_operations_bind_verified_guild_and_accept_empty_or_list_receipts(platform, name, args, method, path):
    route = configure(platform, "guild", GRANTS)
    incoming(platform, "guild")
    tool = "read" if method == "GET" else "execute_action"
    selector = {"resource": "native"} if method == "GET" else {"action": "native"}
    receipt = decoded(
        call(platform, route, tool, **selector, arguments={"operation": "qq." + name, "parameters": args})
    )
    if method != "GET":
        tick(platform)
        assert status(platform, route, receipt)["state"] == "succeeded"
    request = platform[2].requests[-1]
    assert request.method == method and request.url.path == path


def test_foreign_guild_targets_are_denied_before_mutation(platform):
    route = configure(platform, "guild", GRANTS)
    incoming(platform, "guild")
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="native",
            arguments={"operation": "qq.delete_subchannel", "parameters": {"target_channel_id": "foreign"}},
        )
    )
    tick(platform)
    assert status(platform, route, receipt)["state"] == "blocked"
    assert not any(r.method == "DELETE" for r in platform[2].requests)


@pytest.mark.parametrize(
    "args",
    [
        {"guild_id": "evil", "name": "Role"},
        {"role_id": "../other", "name": "Role"},
        {"role_id": "role1", "name": "Role", "authorization": "secret"},
    ],
)
def test_agents_cannot_override_scope_credentials_or_api_paths(platform, args):
    route = configure(platform, "guild", GRANTS)
    incoming(platform, "guild")
    assert call(
        platform,
        route,
        "execute_action",
        action="native",
        arguments={"operation": "qq.update_role", "parameters": args},
    ).get("isError")
    assert not platform[2].requests and not platform[0].get("/api/v1/actions").json()


def test_revoking_a_grant_blocks_pending_actions_and_hides_results(platform):
    client, _, provider, _, _ = platform
    route = configure(platform, grants=["group.manage"])
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="native",
            arguments={"operation": "qq.set_group_mutes", "parameters": {"members": []}},
        )
    )
    changed = client.patch(f"/api/v1/routes/{route['id']}", json={"action_grants": []})
    assert changed.status_code == 200, changed.text
    tick(platform)
    assert status(platform, route, receipt)["state"] == "blocked" and not provider.requests


@pytest.mark.parametrize(
    "failure,expected", [(403, "failed"), (200, "failed"), (500, "unknown"), ("timeout", "unknown")]
)
def test_platform_denials_and_uncertain_effects_are_distinct_and_not_retried(platform, failure, expected):
    route = configure(platform, grants=["group.manage"])
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="native",
            arguments={"operation": "qq.set_group_mutes", "parameters": {"members": []}},
        )
    )
    platform[2].failure = failure
    tick(platform)
    result = status(platform, route, receipt)
    assert result["state"] == expected and "private upstream" not in str(result)
    count = len(platform[2].requests)
    tick(platform)
    assert len(platform[2].requests) == count


def test_stream_fragments_are_ordered_private_and_anchored_to_the_first_source(platform):
    route = configure(platform, "private")
    incoming(platform, "private")
    first = decoded(
        call(platform, route, "send_message", content=[{"type": "text", "text": "One"}], stream={"index": 0})
    )
    tick(platform)
    incoming(platform, "private", mid="source2")
    final = decoded(
        call(
            platform,
            route,
            "send_message",
            content=[{"type": "text", "text": "Two"}],
            stream={"index": 1, "stream_handle": first["stream_handle"], "finished": True},
        )
    )
    tick(platform)
    assert status(platform, route, final)["state"] == "succeeded"
    requests = [json.loads(r.content) for r in platform[2].requests if r.url.path.endswith("/stream_messages")]
    assert [r["index"] for r in requests] == [0, 1] and [r["msg_id"] for r in requests] == ["source1", "source1"]
    assert requests[1]["stream_msg_id"] == "stream-native" and requests[1]["input_state"] == 10
    assert call(
        platform,
        route,
        "send_message",
        content=[{"type": "text", "text": "Late"}],
        stream={"index": 2, "stream_handle": first["stream_handle"]},
    ).get("isError")
    assert "One" not in platform[0].get("/api/v1/actions").text


def test_stream_failure_does_not_continue_or_resend_fragments(platform):
    route = configure(platform, "private")
    incoming(platform, "private")
    first = decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "One"}], stream={}))
    second = decoded(
        call(
            platform,
            route,
            "send_message",
            content=[{"type": "text", "text": "Two"}],
            stream={"index": 1, "stream_handle": first["stream_handle"]},
        )
    )
    platform[2].failure = "timeout"
    tick(platform)
    assert status(platform, route, first)["state"] == "unknown"
    assert status(platform, route, second)["state"] == "blocked"
    assert len([r for r in platform[2].requests if r.url.path.endswith("/stream_messages")]) == 1


@pytest.mark.parametrize(
    "media_kind,mime,file_type",
    [("image", "image/png", 1), ("audio", "audio/mpeg", 3), ("video", "video/mp4", 2), ("file", "application/pdf", 4)],
)
def test_qq_media_uploads_use_correct_types_and_passive_receipts(platform, media_kind, mime, file_type):
    route = configure(platform)
    incoming(platform)
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[
                {"type": media_kind, "media_type": mime, "filename": "asset.bin", "url": "https://example.com/asset"}
            ],
        )
    )
    tick(platform)
    upload = next(r for r in platform[2].requests if r.url.path.endswith("/files"))
    assert json.loads(upload.content)["file_type"] == file_type and json.loads(upload.content)["srv_send_msg"] is False
    sent = json.loads(platform[2].requests[-1].content)
    assert sent["msg_type"] == 7 and sent["media"] == {"file_info": "private-file-info"}
    history = decoded(call(platform, route, "read", resource="messages", arguments={}))["messages"]
    outgoing = next(m for m in history if m["direction"] == "outgoing")
    assert outgoing["media"] == [{"kind": media_kind, "media_type": mime, "filename": "asset.bin"}]
    assert outgoing["images"] == [] and outgoing["attachments"] == []
    assert "https://example.com/asset" not in str(outgoing) and "private-file-info" not in str(outgoing)


def test_sent_image_history_contains_no_payload_bytes_or_received_image_handle(platform):
    route = configure(platform)
    incoming(platform)
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[
                {"type": "text", "text": "Bot image"},
                {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64},
            ],
        )
    )
    tick(platform)
    history = decoded(call(platform, route, "read", resource="messages", arguments={}))["messages"]
    outgoing = next(m for m in history if m["direction"] == "outgoing")
    assert outgoing["text"] == "Bot image" and outgoing["media"][0]["kind"] == "image"
    assert outgoing["images"] == [] and PNG_BASE64 not in str(outgoing)
    assert call(
        platform, route, "read", resource="image", arguments={"source_message_id": outgoing["source_message_id"]}
    ).get("isError")


def test_large_inline_media_uses_credential_free_multipart_upload(platform):
    route = configure(platform)
    incoming(platform)
    content = b"file-content" * (500000)
    encoded = base64.b64encode(content).decode()
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[
                {
                    "type": "file",
                    "media_type": "application/octet-stream",
                    "filename": "large.bin",
                    "data_base64": encoded,
                }
            ],
        )
    )
    tick(platform)
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == "sent"
    prepare = json.loads(next(r for r in platform[2].requests if r.url.path.endswith("/upload_prepare")).content)
    assert prepare["md5"] == hashlib.md5(content).hexdigest() and int(prepare["file_size"]) == len(content)
    assert b"".join(r.content for r in platform[2].uploads) == content
    merge = json.loads(next(r for r in platform[2].requests if r.url.path.endswith("/files")).content)
    assert merge["upload_id"] == "private-upload" and merge["srv_send_msg"] is False
    assert encoded not in platform[0].get("/api/v1/replies").text


def test_guild_image_uses_native_multipart_and_never_group_uploads(platform):
    route = configure(platform, "guild")
    incoming(platform, "guild")
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[
                {"type": "text", "text": "Image"},
                {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64},
            ],
        )
    )
    tick(platform)
    request = platform[2].requests[-1]
    assert request.url.path == "/channels/childA/messages" and request.headers["content-type"].startswith(
        "multipart/form-data"
    )
    assert PNG in request.content and b"file_image" in request.content
    assert not any(r.url.path.endswith("/files") for r in platform[2].requests)


def test_proactive_messages_need_explicit_authority_and_no_fabricated_source(platform):
    route = configure(platform)
    assert call(platform, route, "send_message", content=[{"type": "text", "text": "Hello"}], delivery="proactive").get(
        "isError"
    )
    platform[0].patch(f"/api/v1/routes/{route['id']}", json={"action_grants": ["message.proactive"]})
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Hello"}], delivery="proactive"))
    tick(platform)
    body = json.loads(platform[2].requests[-1].content)
    assert "msg_id" not in body and body["content"] == "Hello"


def test_recall_distinguishes_bot_ownership_and_member_moderation(platform):
    route = configure(platform, grants=["message.recall"])
    incoming(platform)
    assert call(platform, route, "execute_action", action="recall", arguments={"message_id": "source1"}).get("isError")
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Reply"}]))
    tick(platform)
    receipt = decoded(call(platform, route, "execute_action", action="recall", arguments={"message_id": "out1"}))
    tick(platform)
    assert status(platform, route, receipt)["state"] == "succeeded"
    assert (
        platform[2].requests[-1].method == "DELETE"
        and platform[2].requests[-1].url.path == "/v2/groups/123/messages/out1"
    )


@pytest.mark.parametrize("quoted", [False, True])
def test_qq_group_file_cdn_reaches_history_attachment_reads_and_host(platform, quoted):
    client, _, provider, _, _ = platform
    host = client.post(
        "/api/v1/connections",
        json={"name": "a13n", "kind": "a13n", "api_url": "https://host.test/api/v1", "token": "host-token"},
    ).json()
    route = configure(platform, host_id=host["id"], agent_id="agent1")
    url = "https://gzc-download.ftn.qq.com/ftn_handler/file?key=private-file-key"
    attachment = {"content_type": "file", "filename": "notes.txt", "url": url, "size": 21}
    provider.media_data = b"Quoted file contents."
    provider.media_type = "application/octet-stream"
    incoming(
        platform,
        content="Read this file.",
        **(
            {"msg_elements": [{"content": "", "attachments": [attachment]}]}
            if quoted
            else {"attachments": [attachment]}
        ),
    )
    assert not provider.uploads
    history = decoded(call(platform, route, "read", resource="messages", arguments={}))["messages"][0]
    assert history["attachments"] == [{"index": 0, "kind": "file", "media_type": "file", "filename": "notes.txt"}]
    assert "private-file-key" not in str(history) + client.get("/api/v1/events").text
    result = call(
        platform,
        route,
        "read",
        resource="attachment",
        arguments={"source_message_id": "source1", "attachment_index": 0},
    )
    assert not result.get("isError"), result
    resource = result["content"][0]["resource"]
    assert resource["mimeType"] == "text/plain" and resource["text"] == "Quoted file contents."
    assert len(provider.uploads) == 1 and str(provider.uploads[0].url) == url
    tick(platform)
    request = next(r for r in provider.requests if r.url.host == "host.test" and r.method == "POST")
    body = json.loads(request.content)
    assert body["payload"]["content"][1] == {"type": "url", "url": url}
    context = json.loads(body["payload"]["content"][0]["text"].split("\n", 1)[1].split("\n\nMessage:", 1)[0])
    assert context["attachments"] == history["attachments"]


def test_audio_video_files_cards_and_nested_quotes_are_projected_without_private_context(platform):
    route = configure(platform)
    packet = incoming(
        platform,
        content="",
        attachments=[
            {"content_type": "voice", "url": CDN, "voice_wav_url": CDN},
            {"content_type": "video/mp4", "url": CDN, "filename": "clip.mp4"},
            {"content_type": "file", "url": CDN, "filename": "file.pdf"},
        ],
        ark_data={"ark_type": "miniapp", "title": "App", "auth_token": "private-token"},
        msg_elements=[{"content": "Quoted user text"}],
    )
    history = decoded(call(platform, route, "read", resource="messages", arguments={}))["messages"][0]
    assert [item["kind"] for item in history["attachments"]] == ["audio", "video", "file"]
    assert "Quoted user text" in history["text"] and history["structured"]["card"]["title"] == "App"
    assert "private-token" not in str(history) and "private-image-key" not in str(history)
    image = call(
        platform,
        route,
        "read",
        resource="attachment",
        arguments={"source_message_id": "source1", "attachment_index": 0},
    )
    assert not image.get("isError"), image
    assert image["content"][0]["type"] == "audio" and image["content"][0]["mimeType"] == "audio/wav"
    message = qq_message(packet, {"id": "account", "kind": "qq"})
    assert message.sender_id == "7"
    body = host_body(message.model_dump(mode="json"), route)
    context = json.loads(body["payload"]["content"][0]["text"].split("\n", 1)[1].split("\n\nMessage:", 1)[0])
    assert context["attachments"] == history["attachments"]
    assert "url" not in str(context["attachments"])


@pytest.mark.parametrize(
    "declared,filename,data,mime,text",
    [
        ("video/mp4", "test.mp4", b"\x00\x00\x00\x20ftypisom", "video/mp4", None),
        ("file", "notes.txt", "File verification: 中文 42".encode(), "text/plain", "File verification: 中文 42"),
        ("file", "data.json", b'{"value":42}', "application/json", '{"value":42}'),
        ("file", "notes.pdf", b"%PDF-1.7\n", "application/pdf", None),
    ],
)
def test_attachment_reads_preserve_types_and_inline_text_from_generic_cdn(
    platform, declared, filename, data, mime, text
):
    route = configure(platform)
    platform[2].media_type = "application/octet-stream"
    platform[2].media_data = data
    incoming(platform, attachments=[{"content_type": declared, "filename": filename, "url": CDN}])
    result = call(platform, route, "read", resource="attachment", arguments={"source_message_id": "source1"})
    assert not result.get("isError"), result
    resource = result["content"][0]["resource"]
    assert resource["mimeType"] == mime
    if text is not None:
        assert resource["text"] == text and "blob" not in resource
    else:
        assert base64.b64decode(resource["blob"]) == data


def test_non_utf8_text_attachment_returns_tool_error_without_binary_model_input(platform):
    route = configure(platform)
    platform[2].media_type = "application/octet-stream"
    platform[2].media_data = b"\xff\xfeinvalid utf8"
    incoming(platform, attachments=[{"content_type": "file", "filename": "notes.txt", "url": CDN}])
    result = call(platform, route, "read", resource="attachment", arguments={"source_message_id": "source1"})
    assert result["isError"] and all(part["type"] == "text" for part in result["content"])


def test_scoped_notifications_are_durable_sanitized_and_not_agent_inputs(platform):
    client, app, _, _, _ = platform
    route = configure(platform, grants=["resource.read"])
    packet = {
        "op": 0,
        "id": "notice1",
        "t": "GROUP_MEMBER_ADD",
        "d": {
            "group_openid": "123",
            "member_openid": "8",
            "auth_token": "private",
            "message_scene": {"ext": ["auth_token=private"]},
        },
    }
    hook(platform, packet)
    hook(platform, {**packet, "id": "notice2", "d": {**packet["d"], "group_openid": "456"}})
    events = decoded(call(platform, route, "read", resource="events", arguments={}))["events"]
    assert len(events) == 1 and events[0]["event_type"] == "GROUP_MEMBER_ADD"
    assert "private" not in str(events)
    assert not client.portal.call(app.state.store.pending)
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    assert len(decoded(call(platform, route, "read", resource="events", arguments={}))["events"]) == 1


def test_interaction_acknowledgement_is_prompt_and_not_repeated_for_duplicate_events(platform):
    configure(platform, grants=["resource.read"])
    packet = {
        "op": 0,
        "id": "INTERACTION_CREATE:opaque",
        "t": "INTERACTION_CREATE",
        "d": {"id": "interaction1", "type": 11, "group_openid": "123", "data": {"resolved": {"button_id": "ok"}}},
    }
    hook(platform, packet)
    hook(platform, packet)
    acks = [r for r in platform[2].requests if r.url.path.startswith("/interactions/")]
    assert (
        len(acks) == 1
        and acks[0].url.path == "/interactions/interaction1"
        and json.loads(acks[0].content) == {"code": 0}
    )


def test_interrupted_actions_remain_unknown_after_restart_and_are_not_replayed(platform):
    client, app, provider, _, _ = platform
    route = configure(platform, grants=["group.manage"])
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="native",
            arguments={"operation": "qq.set_group_mutes", "parameters": {"members": []}},
        )
    )
    assert client.portal.call(app.state.store.claim_channel_action, receipt["action_id"])
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    tick(platform)
    assert status(platform, route, receipt)["state"] == "unknown" and not provider.requests


def test_action_receipts_are_not_readable_by_another_route(platform):
    route = configure(platform, grants=["group.manage"])
    other = configure(platform, conversation="456", grants=["group.manage"])
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="native",
            arguments={"operation": "qq.set_group_mutes", "parameters": {"members": []}},
        )
    )
    assert call(platform, other, "get_operation", operation_id=receipt["action_id"]).get("isError")


def test_custom_gateway_intents_are_validated_and_saved_without_credentials(platform):
    client, _, _, channel, _ = platform
    changed = client.patch(f"/api/v1/connections/{channel['id']}", json={"qq_intents": QQ_INTENTS | (1 << 19)})
    assert changed.status_code == 200, changed.text
    assert changed.json()["qq_intents"] & (1 << 19)
    assert "app_secret" not in changed.json()
    assert client.patch(f"/api/v1/connections/{channel['id']}", json={"qq_intents": -1}).status_code == 422


def test_new_guild_reactions_require_explicit_grant_even_on_mcp_reply_routes(platform):
    route = configure(platform, "guild")
    incoming(platform, "guild")
    assert call(
        platform,
        route,
        "execute_action",
        action="reaction",
        arguments={"source_message_id": "source1", "emoji_id": "203"},
    ).get("isError")
    assert not platform[2].requests


def test_multipart_revocation_stops_following_parts_and_final_send(platform):
    client, app, provider, _, _ = platform
    route = configure(platform)
    incoming(platform)
    encoded = base64.b64encode(b"x" * (5 * 1024 * 1024)).decode()
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[
                {
                    "type": "file",
                    "media_type": "application/octet-stream",
                    "filename": "large.bin",
                    "data_base64": encoded,
                }
            ],
        )
    )

    async def revoke(request):
        current = await app.state.store.route(route["id"])
        assert await app.state.store.update_route(route["id"], {**current, "enabled": False}, current)

    provider.on_upload = revoke
    tick(platform)
    assert len(provider.uploads) == 1
    assert client.get("/api/v1/replies").json()[0]["state"] == "blocked"
    assert not any(r.url.path.endswith(("/files", "/messages", "/upload_part_finish")) for r in provider.requests)


def test_native_read_does_not_return_results_after_grant_revocation(platform):
    client, app, provider, _, _ = platform
    route = configure(platform, grants=["resource.read"])

    async def revoke(request):
        current = await app.state.store.route(route["id"])
        assert await app.state.store.update_route(route["id"], {**current, "action_grants": []}, current)

    provider.on_request = revoke
    result = call(platform, route, "read", resource="native", arguments={"operation": "qq.get_group", "parameters": {}})
    assert result.get("isError") and "Resource" not in str(result)
    assert client.portal.call(app.state.store.route, route["id"])["action_grants"] == []


def test_source_children_inherit_current_action_grants_without_read_expansion(platform):
    client, _, _, channel, host = platform
    parent = client.post(
        "/api/v1/routes",
        json={
            "name": "Groups",
            "sources": [{"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "*"}],
            "host_id": host["id"],
            "sender_policy": "any",
            "action_grants": ["resource.read"],
        },
    ).json()
    incoming(platform)
    child = next(row for row in client.get("/api/v1/routes").json() if row.get("parent_route_id") == parent["id"])
    assert child["action_grants"] == ["resource.read"]
    decoded(
        call(
            platform,
            parent,
            "read",
            resource="native",
            arguments={"operation": "qq.get_group", "parameters": {}},
            route_id=child["id"],
        )
    )
    assert client.patch(f"/api/v1/routes/{parent['id']}", json={"action_grants": []}).status_code == 200
    assert call(
        platform,
        parent,
        "read",
        resource="native",
        arguments={"operation": "qq.get_group", "parameters": {}},
        route_id=child["id"],
    ).get("isError")


def test_deleting_route_blocks_actions_atomically_and_preserves_history(platform):
    client, app, _, channel, _ = platform
    route = configure(platform, grants=["group.manage"])
    receipt = decoded(
        call(
            platform,
            route,
            "execute_action",
            action="native",
            arguments={"operation": "qq.set_group_mutes", "parameters": {"members": []}},
        )
    )
    assert client.delete(f"/api/v1/routes/{route['id']}").status_code == 200
    row = client.portal.call(app.state.store.channel_action, receipt["action_id"])
    assert row["state"] == "blocked"
    history = client.get(
        "/api/v1/history", params={"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "123"}
    ).json()
    assert history["messages"][0]["direction"] == "action"


def test_tool_annotations_and_catalog_keep_mutation_and_read_surfaces_separate(platform):
    route = configure(platform, "guild", GRANTS)
    tools = rpc(platform[0], route["agent_token"], "tools/list", {}).json()["result"]["tools"]
    by_name = {tool["name"]: tool for tool in tools}
    assert by_name["read"]["annotations"]["readOnlyHint"] is True
    assert by_name["execute_action"]["annotations"]["destructiveHint"] is True
    assert set(by_name) == {"discover", "read", "send_message", "execute_action", "get_operation"}
    assert "native" in by_name["send_message"]["inputSchema"]["properties"]
    catalog = decoded(call(platform, route, "discover", resource="operations"))["native"]
    operations = {item["name"].removeprefix("qq."): item for item in catalog}
    assert operations["join_microphone"]["restriction"] == "audio bot permission required"
    assert operations["list_guild_members"]["restriction"] == "private-domain only"
    assert "set_group_mutes" not in operations


@pytest.mark.parametrize(
    "kind,scene,actor",
    [
        ("private", "c2c", {"user_openid": "7"}),
        ("group", "group", {"group_openid": "123", "group_member_openid": "7"}),
        ("guild", "guild", {"guild_id": "guildA", "channel_id": "childA"}),
    ],
)
def test_button_interactions_activate_only_authorized_sources_and_reply_with_event_context(
    platform, kind, scene, actor
):
    client, _, provider, _, _ = platform
    route = configure(platform, kind)
    packet = {
        "op": 0,
        "id": "INTERACTION_CREATE:outer",
        "t": "INTERACTION_CREATE",
        "d": {
            "id": "button-event",
            "type": 11,
            "scene": scene,
            "timestamp": "2026-10-20T12:00:00+08:00",
            **actor,
            "data": {
                "resolved": {
                    "button_data": "confirm",
                    "button_id": "ok",
                    **({"user_id": "7"} if kind == "guild" else {}),
                }
            },
        },
    }
    hook(platform, packet)
    hook(platform, packet)
    events = client.get("/api/v1/events").json()
    assert len(events) == 1 and events[0]["route_id"] == route["id"]
    assert events[0]["message"]["sender_id"] == "7" and events[0]["message"]["text"].startswith("[Interaction]")
    assert "reply_event_id" not in str(events)
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Confirmed"}]))
    tick(platform)
    body = json.loads(provider.requests[-1].content)
    assert body["event_id"] == "button-event" and "msg_id" not in body
    assert len([r for r in provider.requests if r.url.path.startswith("/interactions/")]) == 1
    if kind == "private":
        first = decoded(
            call(platform, route, "send_message", content=[{"type": "text", "text": "Streaming"}], stream={})
        )
        tick(platform)
        assert status(platform, route, first)["state"] == "succeeded"
        stream = json.loads(provider.requests[-1].content)
        assert stream["event_id"] == "button-event" and "msg_id" not in stream


def test_non_button_interactions_do_not_emit_acknowledgements_or_agent_inputs(platform):
    configure(platform, "private")
    hook(
        platform,
        {
            "op": 0,
            "t": "INTERACTION_CREATE",
            "d": {
                "id": "feedback",
                "type": 13,
                "scene": "c2c",
                "user_openid": "7",
                "timestamp": "2026-10-20T12:00:00+08:00",
                "data": {"resolved": {"feedback_opt": "LIKE"}},
            },
        },
    )
    assert not platform[2].requests
    assert not platform[0].get("/api/v1/events").json()


def output_route(platform, grants=()):
    client = platform[0]
    host = client.post(
        "/api/v1/connections",
        json={"name": "a13n", "kind": "a13n", "api_url": "https://host.test/api/v1", "token": "test-host-key"},
    ).json()
    return configure(platform, grants=grants, host_id=host["id"], agent_id="agent1")


def sent_messages(platform):
    return [r for r in platform[2].requests if r.method == "POST" and r.url.path.endswith("/messages")]


def test_only_proactive_tool_send_publishes_and_final_output_is_ignored(platform):
    route = output_route(platform, ["message.proactive"])
    incoming(platform)
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Notification"}]))
    tick(platform)
    tick(platform)
    submission = next(r for r in platform[2].requests if r.url.host == "host.test" and r.method == "POST")
    context_text = json.loads(submission.content)["payload"]["content"][0]["text"]
    assert "reply_source" not in context_text and '"default_delivery": "proactive"' in context_text
    bodies = [json.loads(r.content) for r in sent_messages(platform)]
    assert len(bodies) == 1
    assert (
        bodies[0]["content"] == "Notification"
        and "msg_id" not in bodies[0]
        and "event_id" not in bodies[0]
        and "msg_seq" not in bodies[0]
    )
    view = decoded(call(platform, route, "discover", resource="conversations"))["conversations"][0]
    assert "reply_source" not in view and view["can_reply"] and view["can_send_proactively"]
    assert view["default_delivery"] == "proactive"


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("send_message", {"content": [{"type": "text", "text": "Notification"}]}),
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "test.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
    ],
)
def test_output_routes_send_proactive_media_without_incoming_context(platform, tool, arguments):
    route = output_route(platform, ["message.proactive"])
    decoded(call(platform, route, tool, **arguments))
    tick(platform)
    assert len(sent_messages(platform)) == 1
    body = json.loads(sent_messages(platform)[0].content)
    assert not {"msg_id", "event_id", "msg_seq", "is_wakeup"} & body.keys()
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == "sent"


@pytest.mark.parametrize(
    "tool,arguments",
    [
        ("send_message", {"content": [{"type": "text", "text": "Explicit passive"}]}),
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "test.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
    ],
)
def test_explicit_passive_tools_keep_source_context_on_output_routes(platform, tool, arguments):
    route = output_route(platform, ["message.proactive"])
    incoming(platform)
    decoded(call(platform, route, tool, delivery="passive", **arguments))
    tick(platform)
    assert json.loads(sent_messages(platform)[0].content)["msg_id"] == "source1"


def test_a13n_route_without_proactive_grant_accepts_passive_tools_only(platform):
    route = output_route(platform)
    incoming(platform)
    for tool, arguments in [
        ("send_message", {"content": [{"type": "text", "text": "No"}]}),
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
        (
            "send_message",
            {
                "content": [
                    {"type": "image", "media_type": "image/png", "filename": "test.png", "data_base64": PNG_BASE64}
                ]
            },
        ),
    ]:
        assert not call(platform, route, tool, **arguments).get("isError")
        assert call(platform, route, tool, delivery="proactive", **arguments).get("isError")
    assert len(platform[0].get("/api/v1/replies").json()) == 3


def test_queued_delivery_is_frozen_and_revocation_blocks_proactive_sends(platform):
    route = configure(platform)
    incoming(platform)
    first = decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Passive before grant"}]))
    platform[0].patch("/api/v1/routes/" + route["id"], json={"action_grants": ["message.proactive"]})
    tick(platform)
    assert json.loads(sent_messages(platform)[0].content)["msg_id"] == "source1"
    assert platform[0].portal.call(platform[1].state.store.reply_payload, first["reply_id"])["delivery"] == "passive"
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Proactive after grant"}]))
    platform[0].patch("/api/v1/routes/" + route["id"], json={"action_grants": []})
    tick(platform)
    assert len(sent_messages(platform)) == 1
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == "blocked"


def test_proactive_second_budget_survives_restart_and_does_not_retry_sent_messages(platform, monkeypatch):
    now = [1792470000.0]
    monkeypatch.setattr("channel_gateway_server.storage.time", SimpleNamespace(time=lambda: now[0]))
    route = configure(platform, grants=["message.proactive"])
    for i in range(6):
        decoded(call(platform, route, "send_message", content=[{"type": "text", "text": str(i)}]))
    tick(platform)
    assert len(sent_messages(platform)) == 5
    store = platform[1].state.store
    platform[0].portal.call(store.close)
    platform[0].portal.call(store.open)
    tick(platform)
    assert len(sent_messages(platform)) == 5
    assert sum(x["state"] == "queued" for x in platform[0].get("/api/v1/replies").json()) == 1
    now[0] += 1.01
    tick(platform)
    tick(platform)
    assert [json.loads(r.content)["content"] for r in sent_messages(platform)] == ["0", "1", "2", "3", "4", "5"]


def test_conversation_budget_keeps_other_destinations_and_passive_replies_moving(platform, monkeypatch):
    now = [1792470000.0]
    monkeypatch.setattr("channel_gateway_server.storage.time", SimpleNamespace(time=lambda: now[0]))
    route = configure(platform, grants=["message.proactive"])
    for i in range(21):
        decoded(call(platform, route, "send_message", content=[{"type": "text", "text": str(i)}]))
    for _ in range(4):
        tick(platform)
        now[0] += 2
    assert len(sent_messages(platform)) == 20
    other = configure(platform, kind="private", grants=["message.proactive"])
    decoded(call(platform, other, "send_message", content=[{"type": "text", "text": "Other destination"}]))
    incoming(platform)
    decoded(
        call(platform, route, "send_message", content=[{"type": "text", "text": "Passive bypass"}], delivery="passive")
    )
    tick(platform)
    assert len(sent_messages(platform)) == 22
    assert [json.loads(r.content)["content"] for r in sent_messages(platform)][-2:] == [
        "Other destination",
        "Passive bypass",
    ]
    now[0] += 60
    tick(platform)
    assert json.loads(sent_messages(platform)[-1].content)["content"] == "20"


def test_proactive_account_minute_budget_is_shared_across_conversations(platform, monkeypatch):
    now = [1792470000.0]
    monkeypatch.setattr("channel_gateway_server.storage.time", SimpleNamespace(time=lambda: now[0]))
    for conversation in ["123", "456"]:
        route = configure(platform, grants=["message.proactive"], conversation=conversation)
        for i in range(20):
            decoded(call(platform, route, "send_message", content=[{"type": "text", "text": str(i)}]))
    for _ in range(7):
        tick(platform)
        now[0] += 2
    assert len(sent_messages(platform)) == 30
    assert sum(x["state"] == "queued" for x in platform[0].get("/api/v1/replies").json()) == 10


def test_proactive_daily_budget_resets_at_china_midnight(platform, monkeypatch):
    now = [1792470000.0]
    monkeypatch.setattr("channel_gateway_server.storage.time", SimpleNamespace(time=lambda: now[0]))
    day_start = ((now[0] + 8 * 3600) // 86400) * 86400 - 8 * 3600
    route = configure(platform, grants=["message.proactive"])
    store = platform[1].state.store

    async def seed():
        async with store.transaction() as db:
            await db.executemany(
                "INSERT INTO qq_send_attempts(account_id,conversation_key,created_at) VALUES(?,?,?)",
                [("app1", "group:123", now[0] - 120)] * 1000,
            )

    platform[0].portal.call(seed)
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Tomorrow"}]))
    tick(platform)
    assert not sent_messages(platform)
    now[0] = day_start + 86400
    tick(platform)
    assert len(sent_messages(platform)) == 1


@pytest.mark.parametrize("failure,state", [(403, "failed"), (429, "failed"), ("timeout", "unknown")])
def test_proactive_rejections_and_uncertain_sends_are_not_retried(platform, failure, state):
    route = configure(platform, grants=["message.proactive"])
    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Attempt once"}]))
    platform[2].failure = failure
    tick(platform)
    tick(platform)
    assert len(sent_messages(platform)) == 1
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == state


def test_expired_passive_reply_does_not_become_proactive(platform):
    route = output_route(platform, ["message.proactive"])
    incoming(platform)
    tick(platform)

    async def reject_message(request):
        if request.url.path.endswith("/messages"):
            platform[2].failure = 400

    decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "Passive"}], delivery="passive"))
    platform[2].on_request = reject_message
    tick(platform)
    tick(platform)
    assert len(sent_messages(platform)) == 1
    assert json.loads(sent_messages(platform)[0].content)["msg_id"] == "source1"
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == "failed"


def test_revoking_proactive_grant_blocks_throttled_queue_without_waiting(platform, monkeypatch):
    now = [1792470000.0]
    monkeypatch.setattr("channel_gateway_server.storage.time", SimpleNamespace(time=lambda: now[0]))
    route = configure(platform, grants=["message.proactive"])
    for i in range(6):
        decoded(call(platform, route, "send_message", content=[{"type": "text", "text": str(i)}]))
    tick(platform)
    assert len(sent_messages(platform)) == 5
    platform[0].patch("/api/v1/routes/" + route["id"], json={"action_grants": []})
    tick(platform)
    assert len(sent_messages(platform)) == 5
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == "blocked"


def test_proactive_image_rechecks_grant_after_upload(platform):
    route = output_route(platform, ["message.proactive"])
    decoded(
        call(
            platform,
            route,
            "send_message",
            content=[{"type": "image", "media_type": "image/png", "filename": "image.png", "data_base64": PNG_BASE64}],
        )
    )
    store = platform[1].state.store

    async def revoke(request):
        if request.url.path.endswith("/files"):
            current = await store.route(route["id"])
            assert current is not None
            await store.update_route(route["id"], {**current, "action_grants": []}, current)

    platform[2].on_request = revoke
    tick(platform)
    assert any(r.url.path.endswith("/files") for r in platform[2].requests)
    assert not sent_messages(platform)
    assert platform[0].get("/api/v1/replies").json()[0]["state"] == "blocked"


def test_five_tool_inventory_is_constant_across_conversations_and_keys(platform):
    client, _, _, _, host = platform
    routes = [configure(platform, conversation=str(500 + i)) for i in range(4)]
    routes.append(configure(platform, "guild"))
    expected = {"discover", "read", "send_message", "execute_action", "get_operation"}
    for key in [host["mcp_key"], *(route["agent_token"] for route in routes)]:
        tools = rpc(client, key, "tools/list", {}).json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == expected
        assert len(tools) == 5
    assert client.get("/api/v1/replies").json() == []
    assert client.get("/api/v1/actions").json() == []


@pytest.mark.parametrize(
    "old_name", ["read_conversation", "send_image", "send_media", "set_message_reaction", "list_channel_operations"]
)
def test_removed_tool_names_cannot_queue_work(platform, old_name):
    route = configure(platform)
    assert call(platform, route, old_name).get("isError")
    assert not platform[0].get("/api/v1/replies").json()
    assert not platform[0].get("/api/v1/actions").json()


@pytest.mark.parametrize("read_channel_ids", [None, ["*"]])
def test_message_read_source_and_cursor_cover_mixed_history(platform, read_channel_ids):
    route = configure(platform, read_channel_ids=read_channel_ids)
    for i in range(5):
        incoming(platform, mid=f"source{i}")
        decoded(call(platform, route, "send_message", content=[{"type": "text", "text": f"Bot {i}"}]))
        tick(platform)
    all_messages = decoded(call(platform, route, "read", resource="messages", arguments={"limit": 100}))["messages"]
    assert len(all_messages) == 10
    cursor = None
    pages = []
    for _ in range(7):
        page = decoded(
            call(
                platform,
                route,
                "read",
                resource="messages",
                arguments={"limit": 2, **({"cursor": cursor} if cursor else {})},
            )
        )
        assert page["source"] == "gateway" and page["coverage"]["complete"] is False
        pages.insert(0, page["messages"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert [m["history_id"] for page in pages for m in page] == [m["history_id"] for m in all_messages]
    assert call(platform, route, "read", resource="messages", source="platform").get("isError")
    assert call(platform, route, "read", resource="messages", arguments={"cursor": "invalid"}).get("isError")
    platform[0].patch(f"/api/v1/routes/{route['id']}", json={"read_channel_ids": []})
    assert call(platform, route, "read", resource="messages", arguments={"cursor": page["next_cursor"]}).get("isError")


def test_common_and_native_argument_validation_has_no_authority_escape(platform):
    route = configure(platform, grants=GRANTS)
    incoming(platform)
    for resource, arguments in [
        ("messages", {"route_id": "another"}),
        ("messages", {"unknown": True}),
        ("native", {"operation": "qq.get_group", "parameters": {"group_id": "another"}}),
        ("native", {"operation": "qq.get_group", "method": "GET", "path": "/users/@me"}),
        ("native", {"operation": "get_group", "parameters": {}}),
        ("native", {"operation": "slack.get_group", "parameters": {}}),
    ]:
        assert call(platform, route, "read", resource=resource, arguments=arguments).get("isError")
    assert call(
        platform,
        route,
        "execute_action",
        action="reaction",
        arguments={"source_message_id": "source1", "emoji_id": "203", "unexpected": True},
    ).get("isError")
    assert not platform[2].requests
    assert not platform[0].get("/api/v1/actions").json()


@pytest.mark.parametrize(
    "options",
    [
        {"content": [{"type": "text", "text": "One"}, {"type": "text", "text": "Two"}]},
        {"content": [{"type": "text", "text": "One"}], "native": {"qq": {"unknown": 1}}},
        {"content": [{"type": "text", "text": "One"}], "native": {"slack": {}}},
        {
            "content": [{"type": "image", "media_type": "image/png", "filename": "x.png", "data_base64": PNG_BASE64}],
            "reply_to": "source1",
        },
        {
            "content": [
                {
                    "type": "image",
                    "media_type": "image/png",
                    "filename": "x.png",
                    "data_base64": PNG_BASE64,
                    "url": "https://example.com/x.png",
                }
            ]
        },
        {"content": [{"type": "text", "text": "One"}], "stream": {"index": 0}, "delivery": "passive"},
    ],
)
def test_unsupported_content_combinations_fail_atomically(platform, options):
    route = configure(platform)
    incoming(platform)
    assert call(platform, route, "send_message", **options).get("isError")
    assert not platform[0].get("/api/v1/replies").json()
    assert not platform[0].get("/api/v1/actions").json()
    assert not platform[2].requests


def test_unified_message_operation_status_is_scoped_and_revocable(platform):
    route = output_route(platform, ["message.proactive"])
    queued = decoded(call(platform, route, "send_message", content=[{"type": "text", "text": "One"}]))
    operation = queued["operation_id"]
    before = decoded(call(platform, route, "get_operation", operation_id=operation))
    assert before["state"] == "queued" and before["result"] is None
    tick(platform)
    after = decoded(call(platform, route, "get_operation", operation_id=operation))
    assert after["state"] == "sent" and after["result"] == {"message_id": "out1"}
    other = configure(platform, conversation="456")
    assert call(platform, other, "get_operation", operation_id=operation).get("isError")
    platform[0].patch(f"/api/v1/routes/{route['id']}", json={"action_grants": []})
    assert decoded(call(platform, route, "get_operation", operation_id=operation))["result"] is None


def test_read_only_handle_discovers_capabilities_without_write_authority(platform):
    route = configure(platform, conversation="456", read_channel_ids=["*"])
    incoming(platform)
    listed = decoded(call(platform, route, "discover"))["conversations"]
    readonly = next(row for row in listed if row["conversation_id"] == "123")
    result = decoded(call(platform, route, "discover", resource="operations", route_id=readonly["id"]))
    assert result["native"] == []
    assert result["capabilities"]["read"]["messages"]["authorized"]
    assert result["capabilities"]["send"]["authorized"] is False
    assert not result["capabilities"]["actions"]["reaction"]["authorized"]
    assert call(
        platform, route, "read", resource="native", route_id=readonly["id"], arguments={"operation": "qq.get_group"}
    ).get("isError")
