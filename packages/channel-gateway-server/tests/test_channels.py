"""Official protocols and transport failure boundaries, with no real accounts."""

import asyncio
import json
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
from channel_gateway_server.app import create_app
from channel_gateway_server.channels.onebot import OneBotSession
from channel_gateway_server.channels.qq import QQGateway, qq_message, signing_key
from channel_gateway_server.channels.weixin import LoginRequired, WeixinClient, weixin_message
from channel_gateway_server.models import ConnectionInput, Settings
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.server import serve

ADMIN = "test-native-administrator-password-012345"
APP_SECRET = "test-qq-app-secret"


def qq_event(*, group=False, sender="user1", mid="msg1", text="Hello"):
    return {
        "op": 0,
        "s": 2,
        "t": "GROUP_AT_MESSAGE_CREATE" if group else "C2C_MESSAGE_CREATE",
        "d": {
            "id": mid,
            "content": text,
            "timestamp": "2026-10-10T12:00:00+08:00",
            "group_openid": "group1",
            "author": {"member_openid" if group else "user_openid": sender},
        },
    }


def wx_event(*, user="user1", bot="bot1", mid=1, token="private-context"):
    return {
        "message_type": 1,
        "from_user_id": user,
        "to_user_id": bot,
        "message_id": mid,
        "create_time_ms": 1791580000000,
        "item_list": [{"type": 1, "text_item": {"text": "Hello"}}],
        "context_token": token,
    }


class Providers:
    def __init__(self):
        self.requests = []
        self.qr_status = {"status": "wait"}
        self.expired = False
        self.fail_send = False
        self.weixin_send_receipt = {"ret": 0}
        self.qq_send_id = "qq-reply1"

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("getAppAccessToken"):
            return httpx.Response(200, json={"access_token": "qq-access-token", "expires_in": "7200"})
        if path.endswith("get_bot_qrcode"):
            assert "authorization" not in request.headers
            assert "base_info" not in json.loads(request.content)
            return httpx.Response(
                200, json={"qrcode": "login-opaque", "qrcode_img_content": "https://weixin.qq.com/test-login"}
            )
        if path.endswith("get_qrcode_status"):
            assert "authorization" not in request.headers
            assert "x-wechat-uin" not in request.headers
            return httpx.Response(200, json=self.qr_status)
        if "ilink/bot/" in path:
            assert request.headers["AuthorizationType"] == "ilink_bot_token"
            assert request.headers["authorization"] == "Bearer wx-token"
            if self.expired:
                return httpx.Response(200, json={"ret": -14})
            if self.fail_send and path.endswith("sendmessage"):
                raise httpx.ReadTimeout("upstream private response")
            if path.endswith("sendmessage"):
                return httpx.Response(200, json=self.weixin_send_receipt)
            return httpx.Response(200, json={"ret": 0})
        if path.endswith("/messages"):
            assert request.headers["authorization"] == "QQBot qq-access-token"
            if self.fail_send:
                raise httpx.ReadTimeout("upstream private response")
            return httpx.Response(200, json={"id": self.qq_send_id})
        if request.method == "POST":
            return httpx.Response(
                201, json={"thread": {"id": "thread1"}, "entry": {"id": "entry1"}, "run": {"id": "run1"}}
            )
        return httpx.Response(200, json={"status": "completed", "output": "Agent reply"})


@pytest.fixture
def native(tmp_path: Path):
    provider = Providers()
    http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app = create_app(
        settings=Settings(admin_username="admin", admin_password=ADMIN, data_dir=tmp_path, worker_enabled=False),
        client=http,
    )
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN},
            headers={"X-Channel-Gateway-Request": "1"},
        ).json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        host = add(client, kind="a13n", api_url="https://a13n.test/api/v1", token="host-key")
        yield client, app, provider, host
        client.portal.call(http.aclose)


def add(client, **config):
    response = client.post("/api/v1/connections", json={"name": "Test account", **config})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize("kind", ["qq", "weixin"])
def test_delete_native_channel_stops_runtime_and_clears_pairing(native, kind):
    import asyncio

    client, app, _, _ = native
    config = {"app_id": "app1", "app_secret": APP_SECRET} if kind == "qq" else {}
    channel = add(client, kind=kind, **config)
    identifier = channel["id"]

    async def arrange():
        task = asyncio.create_task(asyncio.Event().wait())
        app.state.channels.tasks[identifier] = task
        app.state.channels.logins[identifier] = {"id": "pending-login"}
        await app.state.store.set_channel_state(identifier, "private-context", {"token": "private"})
        return task

    task = client.portal.call(arrange)
    assert client.delete(f"/api/v1/connections/{identifier}").status_code == 200
    assert task.cancelled()
    assert identifier not in app.state.channels.tasks
    assert identifier not in app.state.channels.logins
    assert identifier not in app.state.channels.states
    assert not client.portal.call(
        app.state.store.rows, "SELECT * FROM channel_state WHERE connection_id=?", (identifier,)
    )


def add_route(native, channel, *, group=False):
    client, _, _, host = native
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Conversation",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "agent_id": "agt_test",
            "conversation_kind": "group" if group else "private",
            "conversation_id": "group1" if group else "user1",
            "allowed_user_ids": ["user1"],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def qq_hook(native, channel, event, *, valid=True):
    client, *_ = native
    raw = json.dumps(event).encode()
    timestamp = "1791580000"
    signature = signing_key(APP_SECRET).sign(timestamp.encode() + raw).hex() if valid else "00" * 64
    return client.post(
        f"/api/v1/hooks/qq/{channel['id']}",
        content=raw,
        headers={"X-Signature-Timestamp": timestamp, "X-Signature-Ed25519": signature},
    )


@pytest.mark.parametrize("variant", ["unmatched", "notice", "media", "invalid", "bot"])
def test_qq_archives_verified_events_without_a_route(native, variant):
    client, app, _, _ = native
    channel = add(client, kind="qq", transport="webhook", app_id="app1", app_secret=APP_SECRET)
    event = qq_event(group=True)
    if variant == "notice":
        event = {"op": 0, "t": "GROUP_ADD_ROBOT", "d": {"group_openid": "group1"}}
    elif variant == "media":
        event["d"].update(content="", attachments=[{"url": "https://media.test/image", "content_type": "image/png"}])
    elif variant == "invalid":
        event["d"].pop("author")
    elif variant == "bot":
        event["d"]["author"]["bot"] = True
    assert qq_hook(native, channel, event, valid=False).status_code == 401
    response = qq_hook(native, channel, event)
    assert response.status_code == (422 if variant == "invalid" else 200)
    rows = client.portal.call(app.state.store.rows, "SELECT * FROM channel_events WHERE channel_id=?", (channel["id"],))
    assert len(rows) == 1
    assert json.loads(rows[0]["payload"]) == event
    assert rows[0]["event_type"] == event["t"]
    assert rows[0]["state"] == {"unmatched": "blocked", "invalid": "invalid"}.get(variant, "ignored")
    assert not client.portal.call(app.state.store.pending)
    assert "media.test" not in client.get("/api/v1/events").text


def test_qq_archive_failure_does_not_advance_gateway_resume(native, monkeypatch):
    import sqlite3

    client, app, _, _ = native
    channel = add(client, kind="qq", app_id="app1", app_secret=APP_SECRET)
    stored = client.portal.call(app.state.store.connection, channel["id"])

    async def scenario():
        async def archive_failed(*args):
            raise sqlite3.OperationalError("Disk unavailable")

        class Socket:
            async def close(self):
                pass

        runtime = QQGateway(app.state.channels.qq(stored), app.state.store, app.state.bridge.ingest, lambda state: None)
        runtime.session = {"session_id": "existing", "seq": 1}
        await app.state.store.set_channel_state(channel["id"], "qq_gateway", runtime.session)
        monkeypatch.setattr(app.state.store, "archive_channel_event", archive_failed)
        with pytest.raises(sqlite3.OperationalError):
            await runtime.dispatch({"op": 0, "t": "GROUP_ADD_ROBOT", "s": 2, "d": {}}, Socket())
        assert (await app.state.store.channel_state(channel["id"], "qq_gateway"))["seq"] == 1
        assert not await app.state.store.events()

    client.portal.call(scenario)


def test_qq_duplicate_arrivals_are_archived_without_duplicate_delivery(native):
    client, app, _, _ = native
    channel = add(client, kind="qq", transport="webhook", app_id="app1", app_secret=APP_SECRET)
    add_route(native, channel)
    for _ in range(2):
        assert qq_hook(native, channel, qq_event()).status_code == 200
    rows = client.portal.call(app.state.store.rows, "SELECT * FROM channel_events ORDER BY rowid")
    assert [row["state"] for row in rows] == ["accepted", "duplicate"]
    assert len({row["message_event_id"] for row in rows}) == 1
    assert len(client.portal.call(app.state.store.pending)) == 1
    assert b"qq-access-token" not in b"".join(row["payload"] for row in rows)


@pytest.mark.parametrize("group", [False, True])
def test_qq_signed_ingress_durable_host_delivery_and_reply(native, group):
    client, app, provider, _ = native
    channel = add(client, kind="qq", transport="webhook", app_id="app1", app_secret=APP_SECRET)
    configured = add_route(native, channel, group=group)
    assert "app_secret" not in channel
    assert channel["capabilities"]["conversations"] == ["private", "group"]
    assert qq_hook(native, channel, qq_event(group=group), valid=False).status_code == 401
    assert qq_hook(native, channel, qq_event(group=group, sender="intruder", mid="intruder-msg")).json() == {
        "op": 12,
        "d": 0,
    }
    assert client.get("/api/v1/events").json()[0]["route_id"] is None
    for _ in range(2):
        assert qq_hook(native, channel, qq_event(group=group)).status_code == 200
    events = client.get("/api/v1/events").json()
    assert len(events) == 2
    assert len([event for event in events if event["route_id"] == configured["id"]]) == 1
    assert events[0]["message"]["address"]["channel"] == "qq"
    client.portal.call(app.state.bridge.tick)
    host_request = next(r for r in provider.requests if r.url.path.endswith("/threads"))
    assert '"channel": "qq"' in json.loads(host_request.content)["payload"]["content"][0]["text"]
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies").json()[0]["state"] == "sent"
    outgoing = [r for r in provider.requests if r.url.path.endswith("/messages")]
    assert len(outgoing) == 1
    body = json.loads(outgoing[0].content)
    assert body["msg_id"] == "msg1" and body["msg_seq"] == 1
    assert outgoing[0].url.path == f"/v2/{'groups/group1' if group else 'users/user1'}/messages"
    client.portal.call(app.state.bridge.tick)
    assert len([r for r in provider.requests if r.url.path.endswith("/messages")]) == 1
    assert configured["thread_id"] is None


def test_qq_callback_challenge_and_unknown_send(native):
    client, app, provider, _ = native
    channel = add(client, kind="qq", transport="webhook", app_id="app1", app_secret=APP_SECRET)
    response = client.post(
        f"/api/v1/hooks/qq/{channel['id']}", json={"op": 13, "d": {"plain_token": "challenge", "event_ts": "123"}}
    )
    assert response.status_code == 200
    signing_key(APP_SECRET).public_key().verify(bytes.fromhex(response.json()["signature"]), b"123challenge")
    add_route(native, channel)
    qq_hook(native, channel, qq_event())
    provider.fail_send = True
    client.portal.call(app.state.bridge.tick)
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies").json()[0]["state"] == "unknown"
    client.portal.call(app.state.bridge.tick)
    assert len([r for r in provider.requests if r.url.path.endswith("/messages")]) == 1


@pytest.mark.parametrize("group", [False, True])
def test_qq_long_message_ids_are_preserved_through_ingress_and_passive_replies(native, group):
    client, app, provider, _ = native
    channel = add(client, kind="qq", transport="webhook", app_id="app1", app_secret=APP_SECRET)
    add_route(native, channel, group=group)
    incoming_id = "ROBOT1.0_" + "a" * 128
    provider.qq_send_id = "ROBOT1.0_" + "b" * 128
    event = qq_event(group=group, mid=incoming_id)
    for _ in range(2):
        assert qq_hook(native, channel, event).status_code == 200
    stored = client.get("/api/v1/events").json()
    assert len(stored) == 1 and stored[0]["message"]["source_message_id"] == incoming_id
    client.portal.call(app.state.bridge.tick)
    client.portal.call(app.state.bridge.tick)
    outgoing = [r for r in provider.requests if r.url.path.endswith("/messages")]
    assert len(outgoing) == 1 and json.loads(outgoing[0].content)["msg_id"] == incoming_id
    reply = client.get("/api/v1/replies").json()[0]
    assert reply["state"] == "sent" and reply["message_id"] == provider.qq_send_id


def test_qq_message_id_bound_does_not_expand_sender_or_conversation_bounds():
    connection = {"id": "conn_test", "kind": "qq"}
    with pytest.raises(ValueError, match="Invalid channel identifier"):
        qq_message(qq_event(group=True, mid="m" * 1025), connection)
    with pytest.raises(ValueError, match="Invalid channel identifier"):
        qq_message(qq_event(group=True, mid="m" * 137, sender="u" * 129), connection)


def test_weixin_pairing_account_binding_cancellation_and_redaction(native):
    client, app, provider, _ = native
    channel = add(client, kind="weixin")
    path = f"/api/v1/connections/{channel['id']}/login"
    assert channel["state"] == "login_required"
    first = client.post(path).json()
    login = client.post(path).json()
    assert first["login_id"] != login["login_id"]
    assert login["qr_image"].startswith("data:image/svg+xml;base64,")
    assert client.post(path + "/status", json={"login_id": first["login_id"]}).json() == {"status": "expired"}
    provider.qr_status = {"status": "need_verifycode"}
    assert client.post(path + "/status", json={"login_id": login["login_id"]}).json()["status"] == "need_verifycode"
    provider.qr_status = {"status": "scaned_but_redirect", "redirect_host": "evil.example"}
    assert client.post(path + "/status", json={"login_id": login["login_id"]}).status_code == 502
    provider.qr_status = {"status": "scaned_but_redirect", "redirect_host": "ilinkai.weixin.qq.com"}
    client.post(path + "/status", json={"login_id": login["login_id"]})
    provider.qr_status = {
        "status": "confirmed",
        "bot_token": "wx-token",
        "ilink_bot_id": "bot1",
        "baseurl": "https://ilinkai.weixin.qq.com",
    }
    response = client.post(path + "/status", json={"login_id": login["login_id"], "verify_code": "12345"})
    assert response.json() == {"status": "confirmed", "self_id": "bot1"}
    assert provider.requests[-1].url.params["verify_code"] == "12345"
    assert "wx-token" not in response.text + client.get("/api/v1/connections").text
    stored = client.portal.call(app.state.store.connection, channel["id"])
    assert stored["token"] == "wx-token"
    new_login = client.post(path).json()
    provider.qr_status["ilink_bot_id"] = "different-bot"
    assert client.post(path + "/status", json={"login_id": new_login["login_id"]}).status_code == 502
    assert client.portal.call(app.state.store.connection, channel["id"])["self_id"] == "bot1"
    client.post(path + "/cancel", json={"login_id": new_login["login_id"]})
    assert client.post(path + "/status", json={"login_id": new_login["login_id"]}).json()["status"] == "expired"


@pytest.mark.parametrize("receipt", [{"ret": 0}, {}, {"errcode": 0}])
def test_weixin_context_is_private_and_account_scoped(native, receipt):
    client, app, provider, _ = native
    provider.weixin_send_receipt = receipt
    channel = add(client, kind="weixin", token="wx-token", self_id="bot1")
    configured = add_route(native, channel)
    other = add(client, kind="weixin", token="other-token", self_id="bot2")
    second_route = add_route(native, other)
    stored = client.portal.call(app.state.store.connection, channel["id"])
    message = weixin_message(wx_event(), stored)
    assert weixin_message(wx_event(bot="bot2"), stored) is None
    assert weixin_message({**wx_event(), "message_type": 2}, stored) is None
    assert weixin_message({**wx_event(), "group_id": "g1"}, stored) is None
    assert (
        client.portal.call(app.state.bridge.ingest, stored, message, {"context_token": "private-context"})["state"]
        == "accepted"
    )
    assert "private-context" not in client.get("/api/v1/events").text
    client.portal.call(app.state.bridge.tick)
    client.portal.call(app.state.bridge.tick)
    outgoing = next(r for r in provider.requests if r.url.path.endswith("sendmessage"))
    body = json.loads(outgoing.content)
    assert body["msg"]["context_token"] == "private-context"
    assert body["msg"]["to_user_id"] == "user1"
    replies = client.get("/api/v1/replies").json()
    assert replies[0]["state"] == "sent" and replies[0]["message_id"] == body["msg"]["client_id"]
    reply = client.portal.call(app.state.store.queue_reply, second_route, "Do not cross accounts")
    client.portal.call(app.state.bridge.send, reply)
    assert client.get("/api/v1/replies").json()[0]["state"] == "blocked"
    # A revoked context sender cannot authorize a new tool reply.
    current_route = client.portal.call(app.state.store.route, configured["id"])
    assert current_route["conversation_id"] == "user1"
    bad_group = client.post(
        "/api/v1/routes",
        json={
            **{k: v for k, v in configured.items() if k not in {"id", "thread_id", "agent_token"}},
            "conversation_kind": "group",
            "conversation_id": "g1",
        },
    )
    assert bad_group.status_code == 422


def test_weixin_poll_cursor_commits_after_ingestion_and_session_expiry(native):
    client, app, provider, _ = native
    channel = add(client, kind="weixin", token="wx-token", self_id="bot1")
    add_route(native, channel)
    stored = client.portal.call(app.state.store.connection, channel["id"])

    async def scenario():
        seen = asyncio.Event()
        calls = []

        async def respond(request):
            calls.append(request)
            if request.url.path.endswith("getupdates"):
                if len([r for r in calls if r.url.path.endswith("getupdates")]) == 1:
                    return httpx.Response(
                        200,
                        json={
                            "ret": 0,
                            "msgs": [
                                wx_event(),
                                wx_event(user="intruder", mid=2),
                                {**wx_event(mid=4), "item_list": [{"type": 2, "image_item": {"url": "media"}}]},
                                None,
                            ],
                            "get_updates_buf": "cursor1",
                        },
                    )
                seen.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json={"ret": 0})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            old = app.state.channels.http
            app.state.channels.http = http
            task = asyncio.create_task(app.state.channels.poll_weixin(stored))
            await asyncio.wait_for(seen.wait(), 2)
            assert await app.state.store.channel_state(channel["id"], "weixin_cursor") == {"cursor": "cursor1"}
            assert len(await app.state.store.events()) == 2
            archived = await app.state.store.rows("SELECT * FROM channel_events ORDER BY rowid")
            assert [row["state"] for row in archived] == ["accepted", "blocked", "ignored", "invalid"]
            assert json.loads(archived[2]["payload"])["item_list"][0]["type"] == 2
            assert json.loads(archived[3]["payload"]) is None
            assert (
                json.loads([r for r in calls if r.url.path.endswith("getupdates")][1].content)["get_updates_buf"]
                == "cursor1"
            )
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            app.state.channels.http = old

        # Persistence failure must not acknowledge the next provider cursor.
        async def fail_ingest(*args):
            raise RuntimeError("storage failure")

        async def batch(request):
            return httpx.Response(200, json={"ret": 0, "msgs": [wx_event(mid=3)], "get_updates_buf": "cursor2"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(batch)) as http:
            old_http, old_receive = app.state.channels.http, app.state.channels.receive
            app.state.channels.http, app.state.channels.receive = http, fail_ingest
            with pytest.raises(RuntimeError):
                await app.state.channels.poll_weixin(stored)
            assert await app.state.store.channel_state(channel["id"], "weixin_cursor") == {"cursor": "cursor1"}
            app.state.channels.http, app.state.channels.receive = old_http, old_receive

    client.portal.call(scenario)
    provider.expired = True
    client.portal.call(app.state.channels.run, stored)
    assert app.state.channels.status(stored)["state"] == "login_required"

    async def expired_send():
        with pytest.raises(LoginRequired):
            await WeixinClient(stored, app.state.channels.http).send("user1", "Hi", "ctx", "reply1")

    client.portal.call(expired_send)


@pytest.mark.parametrize(
    "mode, url",
    [
        ("http", "https://bot.test/api"),
        ("http_reverse", "https://bot.test/api"),
        ("ws", "wss://bot.test/api"),
        ("ws_reverse", None),
    ],
)
def test_onebot_modes_and_invalid_config_are_explicit(native, mode, url):
    client, *_ = native
    config = {"kind": "onebots", "transport": mode, "self_id": "99", "token": "bot-token"}
    if url:
        config["api_url"] = url
    if mode == "http_reverse":
        config["webhook_secret"] = "long-signing-secret"
    connection = add(client, **config)
    assert connection["capabilities"]["receive"] is (mode != "http")
    invalid = client.post(
        "/api/v1/connections", json={"name": "Wrong protocol", **config, "app_secret": "never-echo-secret"}
    )
    assert invalid.status_code == 422 and "never-echo-secret" not in invalid.text
    if mode != "http_reverse":
        assert client.post(f"/api/v1/hooks/onebots/{connection['id']}", content="{}").status_code == 409


def test_reverse_websocket_auth_receipts_rotation_and_offline_deferral(native):
    client, app, _, _ = native
    channel = add(client, kind="onebots", transport="ws_reverse", self_id="99", token="bot-token")
    configured = add_route(native, channel)
    path = f"/api/v1/ws/onebots/{channel['id']}"
    headers = {"Authorization": "Bearer bot-token", "X-Self-ID": "99", "X-Client-Role": "Universal"}
    for bad in (
        {**headers, "Authorization": "Bearer wrong"},
        {**headers, "X-Self-ID": "100"},
        {**headers, "Origin": "http://evil.test"},
        {**headers, "X-Client-Role": "Event"},
    ):
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(path, headers=bad):
            pass
    reply = client.portal.call(app.state.store.queue_reply, configured, "Hello")
    client.portal.call(app.state.bridge.send, reply)
    assert client.get("/api/v1/replies").json()[0]["state"] == "queued"
    with client.websocket_connect(path, headers=headers) as socket:
        with pytest.raises(WebSocketDisconnect), client.websocket_connect(path, headers=headers):
            pass
        socket.send_json(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": "user1",
                "self_id": "99",
                "message_id": 1,
                "time": 1791580000,
                "message": "Hello",
            }
        )
        socket.send_json({"echo": "unmatched-receipt", "status": "ok", "retcode": 0})
        pending = client.portal.start_task_soon(app.state.bridge.send, reply)
        request = socket.receive_json()
        assert request["action"] == "send_msg" and request["params"]["user_id"] == "user1"
        socket.send_json({"echo": request["echo"], "status": "ok", "retcode": 0, "data": {"message_id": "sent1"}})
        pending.result(timeout=3)
        assert client.get("/api/v1/replies").json()[0]["state"] == "sent"
        assert client.post(f"/api/v1/connections/{channel['id']}/login").status_code == 404
        rotated = client.patch(f"/api/v1/connections/{channel['id']}", json={"token": "rotated-token"})
        assert rotated.status_code == 200
        assert channel["id"] not in app.state.channels.sessions
        assert socket.receive()["type"] == "websocket.close"
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(path, headers=headers):
        pass


def test_onebot_pending_receipt_disconnect_is_unknown(native):
    client, app, _, _ = native
    channel = add(client, kind="onebots", transport="ws_reverse", self_id="99", token="bot-token")
    configured = add_route(native, channel)
    reply = client.portal.call(app.state.store.queue_reply, configured, "One attempt")

    async def scenario():
        started = asyncio.Event()

        async def send(raw):
            started.set()

        async def receive(*args):
            return {"state": "ignored"}

        stored = await app.state.store.connection(channel["id"])
        session = OneBotSession(stored, receive, send, app.state.store)
        app.state.channels.sessions[channel["id"]] = session
        sending = asyncio.create_task(app.state.bridge.send(reply))
        await started.wait()
        session.disconnected()
        await sending
        assert not session.pending

    client.portal.call(scenario)
    assert client.get("/api/v1/replies").json()[0]["state"] == "unknown"


def test_forward_websocket_connects_with_token_and_expected_account(native):
    client, app, _, _ = native

    async def scenario():
        received = asyncio.Event()

        async def bot(socket):
            assert socket.request.headers["Authorization"] == "Bearer bot-token"
            await socket.send(
                json.dumps(
                    {
                        "post_type": "message",
                        "message_type": "private",
                        "self_id": 99,
                        "user_id": "user1",
                        "message_id": 1,
                        "time": 1791580000,
                        "message": "Hello",
                    }
                )
            )
            await received.wait()

        async with serve(bot, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            connection = await app.state.store.add_connection(
                ConnectionInput(
                    name="WS",
                    kind="onebots",
                    transport="ws",
                    api_url=f"ws://127.0.0.1:{port}",
                    token="bot-token",
                    self_id="99",
                ).stored()
            )

            async def receive(c, message, context):
                assert message.sender_id == "user1" and message.address.account_id == connection["id"]
                received.set()
                return {"state": "accepted"}

            previous = app.state.channels.receive
            app.state.channels.receive = receive
            await asyncio.wait_for(app.state.channels.connect_onebot(connection), 3)
            app.state.channels.receive = previous
            assert received.is_set()

    client.portal.call(scenario)


def test_qq_gateway_identify_resume_and_persistence_failure(native):
    client, app, _, _ = native
    channel = add(client, kind="qq", app_id="app1", app_secret=APP_SECRET)
    stored = client.portal.call(app.state.store.connection, channel["id"])

    class Socket:
        def __init__(self, events):
            self.events, self.sent = events, []

        async def recv(self):
            return json.dumps({"op": 10, "d": {"heartbeat_interval": 30000}})

        async def send(self, raw):
            self.sent.append(json.loads(raw))

        async def close(self):
            pass

        async def __aiter__(self):
            for event in self.events:
                yield json.dumps(event)

    async def scenario():
        qq = app.state.channels.qq(stored)

        async def receive(c, message, ctx):
            before = await app.state.store.channel_state(channel["id"], "qq_gateway")
            assert before["seq"] == 1
            return {"state": "accepted"}

        runtime = QQGateway(qq, app.state.store, receive, lambda state: None)
        socket = Socket([{"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session1"}}, qq_event()])
        await runtime.run(socket)
        archives = await app.state.store.rows("SELECT * FROM channel_events ORDER BY rowid")
        assert [row["event_type"] for row in archives] == ["opcode:10", "READY", "C2C_MESSAGE_CREATE"]
        assert socket.sent[0]["op"] == 2 and socket.sent[0]["d"]["intents"] == 1 << 25
        assert await app.state.store.channel_state(channel["id"], "qq_gateway") == {"session_id": "session1", "seq": 2}
        resume = Socket([])
        await runtime.run(resume)
        assert resume.sent[0] == {"op": 6, "d": {"token": "QQBot qq-access-token", "session_id": "session1", "seq": 2}}

        async def failed(*args):
            raise RuntimeError("storage failure")

        runtime.receive = failed
        with pytest.raises(RuntimeError):
            await runtime.dispatch({**qq_event(), "s": 3}, resume)
        assert (await app.state.store.channel_state(channel["id"], "qq_gateway"))["seq"] == 2
        assert (await app.state.store.rows("SELECT * FROM channel_events ORDER BY rowid DESC LIMIT 1"))[0][
            "state"
        ] == "received"

        async def value_error(*args):
            raise ValueError("Receiver failure must not be classified as invalid input")

        runtime.receive = value_error
        with pytest.raises(ValueError, match="Receiver failure"):
            await runtime.dispatch({**qq_event(), "s": 3}, resume)
        assert (await app.state.store.channel_state(channel["id"], "qq_gateway"))["seq"] == 2
        await runtime.dispatch({"op": 9, "d": False}, resume)
        assert await app.state.store.channel_state(channel["id"], "qq_gateway") == {}
        assert qq.expires == 0

    client.portal.call(scenario)


def test_old_onebot_connections_and_credential_rotation_preserve_context(native):
    client, app, _, _ = native
    channel = add(
        client,
        kind="onebots",
        api_url="https://bot.test",
        token="bot-token",
        self_id="99",
        webhook_secret="signing-secret-012345",
    )

    async def old_row():
        value = await app.state.store.connection(channel["id"])
        legacy = {k: v for k, v in value.items() if k not in {"transport", "app_id", "app_secret"}}
        assert await app.state.store.update_connection(channel["id"], legacy, value)
        await app.state.store.set_channel_state(channel["id"], "context:private:user1", {"message_id": "source1"})
        return legacy

    legacy = client.portal.call(old_row)
    assert (
        next(row for row in client.get("/api/v1/connections").json() if row["id"] == channel["id"])["transport"]
        == "http_reverse"
    )
    response = client.patch(f"/api/v1/connections/{channel['id']}", json={"token": "new-token"})
    assert response.status_code == 200 and "new-token" not in response.text
    assert client.portal.call(app.state.store.channel_state, channel["id"], "context:private:user1") == {
        "message_id": "source1"
    }

    async def stale():
        # Old authenticated sockets/callbacks cannot enter new account configuration.
        message = qq_message(qq_event(), {**legacy, "kind": "qq"})
        assert await app.state.bridge.ingest(legacy, message) == {"state": "blocked"}

    client.portal.call(stale)


@pytest.mark.parametrize("failure", ["timeout", {"ret": 1}, {"errcode": 1}])
def test_weixin_unconfirmed_send_does_not_repeat(native, failure):
    client, app, provider, _ = native
    channel = add(client, kind="weixin", token="wx-token", self_id="bot1")
    add_route(native, channel)
    stored = client.portal.call(app.state.store.connection, channel["id"])
    client.portal.call(
        app.state.bridge.ingest, stored, weixin_message(wx_event(), stored), {"context_token": "private-context"}
    )
    if failure == "timeout":
        provider.fail_send = True
    else:
        provider.weixin_send_receipt = failure
    client.portal.call(app.state.bridge.tick)
    client.portal.call(app.state.bridge.tick)
    assert client.get("/api/v1/replies").json()[0]["state"] == "unknown"
    client.portal.call(app.state.bridge.tick)
    assert len([r for r in provider.requests if r.url.path.endswith("sendmessage")]) == 1
    assert "private" not in client.get("/api/v1/replies").json()[0]["error"]


def test_weixin_login_expiry_and_configuration_change(native):
    client, app, provider, _ = native
    channel = add(client, kind="weixin")
    path = f"/api/v1/connections/{channel['id']}/login"
    login = client.post(path).json()
    app.state.channels.logins[channel["id"]]["expires"] = 0
    before = len(provider.requests)
    assert client.post(path + "/status", json={"login_id": login["login_id"]}).json() == {"status": "expired"}
    assert len(provider.requests) == before
    login = client.post(path).json()

    async def concurrent_change():
        stored = await app.state.store.connection(channel["id"])
        assert await app.state.store.update_connection(
            channel["id"], {**stored, "token": "current-token", "self_id": "bot1"}, stored
        )

    client.portal.call(concurrent_change)
    provider.qr_status = {
        "status": "confirmed",
        "bot_token": "wx-token",
        "ilink_bot_id": "bot1",
        "baseurl": "https://ilinkai.weixin.qq.com",
    }
    assert client.post(path + "/status", json={"login_id": login["login_id"]}).status_code == 502
    assert client.portal.call(app.state.store.connection, channel["id"])["token"] == "current-token"
    assert "wx-token" not in client.get("/api/v1/connections").text


def test_qq_heartbeat_closes_on_missing_ack_and_source_sequences_are_durable(native):
    client, app, _, _ = native
    channel = add(client, kind="qq", app_id="app1", app_secret=APP_SECRET)
    stored = client.portal.call(app.state.store.connection, channel["id"])

    class Socket:
        def __init__(self):
            self.closed, self.sent = False, []

        async def send(self, raw):
            self.sent.append(json.loads(raw))

        async def close(self):
            self.closed = True

    async def scenario():
        runtime = QQGateway(app.state.channels.qq(stored), app.state.store, None, None)
        runtime.session = {"seq": 23}
        socket = Socket()
        await asyncio.wait_for(runtime.heartbeat(socket, 0.001), 1)
        assert socket.closed and socket.sent == [{"op": 1, "d": 23}]
        assert await app.state.store.next_channel_sequence(channel["id"], "msg1") == 1
        assert await app.state.store.next_channel_sequence(channel["id"], "msg1") == 2
        assert await app.state.store.next_channel_sequence("different-account", "msg1") == 1

    client.portal.call(scenario)


def test_account_socket_refuses_handshake_redirect(native):
    client, app, _, _ = native

    async def scenario():
        reached = []

        async def target(socket):
            reached.append(socket.request.headers.get("Authorization"))

        async with serve(target, "127.0.0.1", 0) as target_server:
            target_port = target_server.sockets[0].getsockname()[1]

            async def redirect(connection, request):
                response = connection.respond(302, "Redirect")
                response.headers["Location"] = f"ws://127.0.0.1:{target_port}/"
                return response

            async with serve(target, "127.0.0.1", 0, process_request=redirect) as server:
                port = server.sockets[0].getsockname()[1]
                connection = ConnectionInput(
                    name="Redirect",
                    kind="onebots",
                    transport="ws",
                    api_url=f"ws://127.0.0.1:{port}",
                    token="account-secret",
                    self_id="99",
                ).stored()
                connection["id"] = "conn_redirect_test"
                from websockets.exceptions import InvalidStatus

                with pytest.raises(InvalidStatus):
                    await app.state.channels.connect_onebot(connection)
                assert reached == []

    client.portal.call(scenario)


def test_weixin_any_sender_full_access_replies_with_authorized_context(native):
    client, app, provider, host = native
    channel = add(client, kind="weixin", token="wx-token", self_id="bot1")
    parent = client.post(
        "/api/v1/routes",
        json={
            "name": "Assistant",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "agent_id": "agt_test",
            "conversation_kind": "private",
            "conversation_id": "*",
            "sender_policy": "any",
            "access_mode": "full",
        },
    ).json()
    stored = client.portal.call(app.state.store.connection, channel["id"])
    message = weixin_message(wx_event(user="new-user"), stored)
    assert (
        client.portal.call(app.state.bridge.ingest, stored, message, {"context_token": "private-context"})["state"]
        == "accepted"
    )
    client.portal.call(app.state.bridge.tick)
    client.portal.call(app.state.bridge.tick)
    outgoing = next(request for request in provider.requests if request.url.path.endswith("sendmessage"))
    body = json.loads(outgoing.content)
    assert body["msg"]["to_user_id"] == "new-user"
    assert body["msg"]["context_token"] == "private-context"
    assert client.get("/api/v1/replies").json()[0]["state"] == "sent"
    # Any sender still needs an actual authenticated incoming reply context.
    child = client.portal.call(app.state.store.conversation_route, parent, "no-context")
    assert "agent_token" not in child
    reply = client.portal.call(app.state.store.queue_reply, child, "Missing context")
    client.portal.call(app.state.bridge.send, reply)
    assert client.get("/api/v1/replies").json()[0]["state"] == "blocked"
    assert len([request for request in provider.requests if request.url.path.endswith("sendmessage")]) == 1
