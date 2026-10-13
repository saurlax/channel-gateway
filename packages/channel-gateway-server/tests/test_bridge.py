"""Protocol and permission tests using real HTTP routes and mocked upstream APIs."""

import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from channel_gateway_server.app import create_app
from channel_gateway_server.models import Settings
from fastapi.testclient import TestClient

ADMIN_PASSWORD = "test-administrator-password-0123456789"
SECRET = "test-onebot-signing-secret"


class Upstreams:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail_submit = False
        self.fail_send = False
        self.queued_receipt = False
        self.run_status = "completed"
        self.output = "Hello from the agent"
        self.unique_threads = False
        self.thread_count = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("send_msg"):
            if self.fail_send:
                raise httpx.ReadTimeout("Simulated lost reply", request=request)
            assert json.loads(request.content)["message"][0]["type"] == "text"
            return httpx.Response(200, json={"status": "ok", "retcode": 0, "data": {"message_id": "500"}})
        if request.method == "POST":
            if self.fail_submit:
                self.fail_submit = False
                raise httpx.ReadTimeout("Simulated lost receipt", request=request)
            self.thread_count += 1
            thread_id = "thread_" + str(self.thread_count) if self.unique_threads else "thread_1"
            if self.unique_threads and "/inbox" in path:
                thread_id = path.split("/threads/")[1].split("/")[0]
            return httpx.Response(
                201,
                json={
                    "thread": {"id": thread_id},
                    "entry": {"id": "entry_1"},
                    "run": None if self.queued_receipt else {"id": "run_1"},
                },
            )
        if "/inbox/" in path:
            return httpx.Response(200, json={"status": "assigned", "assigned_run_id": "run_1"})
        return httpx.Response(200, json={"status": self.run_status, "output": self.output})


@pytest.fixture
def gateway(tmp_path: Path):
    upstream = Upstreams()
    transport = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    settings = Settings(admin_username="admin", admin_password=ADMIN_PASSWORD, data_dir=tmp_path, worker_enabled=False)
    app = create_app(settings=settings, static_dir=tmp_path, client=transport)
    with TestClient(app) as client:
        session = client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
            headers={"X-Channel-Gateway-Request": "1"},
        ).json()
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        channel = client.post(
            "/api/v1/connections",
            json={
                "name": "QQ",
                "kind": "onebots",
                "api_url": "https://onebots.test/qq/bot/onebot/v11",
                "token": "bot-token",
                "self_id": "99",
                "webhook_secret": SECRET,
            },
        ).json()
        host = client.post(
            "/api/v1/connections",
            json={
                "name": "a13n",
                "kind": "a13n",
                "api_url": "https://host.test/api/v1",
                "token": "host-token",
            },
        ).json()
        yield client, app, upstream, channel, host
        client.portal.call(transport.aclose)


def route(gateway, *, kind="group", source="output", conversation="123"):
    client, _, _, channel, host = gateway
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Test conversation",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "conversation_kind": kind,
            "conversation_id": conversation,
            "allowed_user_ids": [conversation] if kind == "private" else ["7"],
            "agent_id": "agt_example",
            "reply_source": source,
            "mcp_connection_id": "conn_scope_" + kind + conversation if source == "mcp" else None,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def webhook(gateway, *, user=7, kind="group", conversation=123, message_id=1, signed=True, self_id=99, text="Hello"):
    client, _, _, channel, _ = gateway
    body = {
        "post_type": "message",
        "message_type": kind,
        "self_id": self_id,
        "user_id": user,
        "group_id": conversation,
        "message_id": message_id,
        "time": 1720000000,
        "message": [{"type": "text", "data": {"text": text}}],
    }
    raw = json.dumps(body).encode()
    signature = "sha1=" + hmac.new(SECRET.encode(), raw, hashlib.sha1).hexdigest() if signed else "sha1=invalid"
    return client.post(
        f"/api/v1/hooks/onebots/{channel['id']}",
        content=raw,
        headers={"X-Signature": signature, "Content-Type": "application/json"},
    )


def tick(gateway):
    client, app, *_ = gateway
    client.portal.call(app.state.bridge.tick)


def test_verified_events_enforce_actor_and_account_and_deduplicate(gateway):
    client, _, upstream, _, _ = gateway
    route(gateway)
    assert webhook(gateway, signed=False).status_code == 401
    assert webhook(gateway, user=8, message_id=2).json()["state"] == "blocked"
    assert webhook(gateway, self_id=100).json()["state"] == "ignored"
    assert webhook(gateway).json()["state"] == "accepted"
    assert webhook(gateway).json()["state"] == "duplicate"
    assert len(client.get("/api/v1/events").json()) == 2
    tick(gateway)
    assert len(upstream.requests) == 1
    request = upstream.requests[0]
    assert request.url.path == "/api/v1/threads"
    assert request.headers["Authorization"] == "Bearer host-token"
    assert request.headers["Idempotency-Key"].startswith("evt_")
    body = json.loads(request.content)
    assert body["delivery"] == "next_run"
    assert body["options"]["overrides"]["connection_tools"] == []
    assert '"sender_id": "7"' in body["payload"]["content"][0]["text"]


def test_restricted_group_reply_is_automatic_and_does_not_repeat(gateway):
    client, _, upstream, _, _ = gateway
    configured = route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    replies = client.get("/api/v1/replies").json()
    assert replies[0]["state"] == "sent"
    tick(gateway)
    tick(gateway)
    sends = [r for r in upstream.requests if r.url.path.endswith("send_msg")]
    assert len(sends) == 1
    assert json.loads(sends[0].content)["group_id"] == 123
    assert client.get("/api/v1/replies").json()[0]["state"] == "sent"
    webhook(gateway, message_id=2)
    tick(gateway)
    assert upstream.requests[-1].url.path == "/api/v1/threads/thread_1/inbox"
    assert client.get("/api/v1/routes").json()[0]["thread_id"] == "thread_1"
    assert configured["thread_id"] is None


def test_queued_a13n_receipt_is_resolved_through_its_exact_entry(gateway):
    _, _, upstream, _, _ = gateway
    upstream.queued_receipt = True
    route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    reads = [request for request in upstream.requests if request.method == "GET"]
    assert reads[-2].url.path == "/api/v1/threads/thread_1/inbox/entry_1"
    assert reads[-1].url.path == "/api/v1/runs/run_1"


def test_lost_submission_receipt_retries_frozen_path_body_and_key(gateway):
    client, app, upstream, *_ = gateway
    configured = route(gateway)
    upstream.fail_submit = True
    response = webhook(gateway)
    tick(gateway)
    first = upstream.requests[-1]
    # A different receipt has meanwhile bound the route. The failed command still
    # must retry /threads rather than changing its idempotent operation to /inbox.
    client.portal.call(
        app.state.store.submitted,
        response.json()["event_id"],
        configured["id"],
        {"thread": {"id": "thread_1"}, "entry": {"id": "entry_1"}, "run": None},
    )
    client.portal.call(lambda: app.state.store.update_event(response.json()["event_id"], state="queued"))
    tick(gateway)
    second = upstream.requests[-1]
    assert first.url == second.url
    assert first.content == second.content
    assert first.headers["Idempotency-Key"] == second.headers["Idempotency-Key"]


def test_private_auto_reply_and_unknown_send_are_not_retried(gateway):
    client, _, upstream, *_ = gateway
    route(gateway, kind="private", conversation="7")
    upstream.fail_send = True
    webhook(gateway, kind="private")
    tick(gateway)
    tick(gateway)
    tick(gateway)
    assert len([r for r in upstream.requests if r.url.path.endswith("send_msg")]) == 1
    assert client.get("/api/v1/replies").json()[0]["state"] == "unknown"


def test_revoking_route_blocks_accepted_events_and_replies(gateway):
    client, _, upstream, *_ = gateway
    configured = route(gateway)
    webhook(gateway)
    client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": False})
    tick(gateway)
    assert not upstream.requests
    assert client.get("/api/v1/events").json()[0]["state"] == "blocked"


def test_secrets_never_appear_in_views_or_validation_errors(gateway):
    client, *_ = gateway
    connections = client.get("/api/v1/connections").text
    assert SECRET not in connections and "host-token" not in connections and "bot-token" not in connections
    bad = client.post(
        "/api/v1/connections",
        json={"name": "bad", "kind": "a13n", "token": "secret-value", "api_url": "file:///tmp/secret-value"},
    )
    assert bad.status_code == 422
    assert "secret-value" not in bad.text
    client.cookies.clear()
    assert client.get("/api/v1/connections").status_code == 401
    assert client.get("/api/v1/routes").status_code == 401


def test_different_conversations_and_channels_cannot_share_a_route(gateway):
    client, _, _, channel, host = gateway
    route(gateway)
    duplicate = client.post(
        "/api/v1/routes",
        json={
            "name": "Other",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "conversation_kind": "group",
            "conversation_id": "123",
            "allowed_user_ids": ["8"],
            "agent_id": "agt_other",
        },
    )
    assert duplicate.status_code == 409
    assert webhook(gateway, kind="private").json()["state"] == "blocked"
    assert webhook(gateway, conversation=999).json()["state"] == "blocked"


def test_removed_automatic_reply_setting_is_rejected(gateway):
    client, _, _, channel, host = gateway
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Unsafe",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "conversation_kind": "group",
            "conversation_id": "123",
            "allowed_user_ids": ["7"],
            "agent_id": "agt_test",
            "auto_reply": True,
        },
    )
    assert response.status_code == 422


def test_reply_review_endpoints_are_removed(gateway):
    client, *_ = gateway
    for operation in ("approve", "reject"):
        assert client.post(f"/api/v1/replies/reply_missing/{operation}", json={"text": "unused"}).status_code == 404


def test_legacy_reply_upgrade_does_not_send_old_drafts_or_expand_access(gateway, tmp_path):
    from channel_gateway_server.storage import Store

    client, app, upstream, *_ = gateway
    configured = route(gateway)
    draft = client.portal.call(app.state.store.queue_reply, configured, "Unreviewed legacy content")
    approved = client.portal.call(app.state.store.queue_reply, configured, "Previously approved content")
    rejected = client.portal.call(app.state.store.queue_reply, configured, "Rejected legacy content")
    client.portal.call(lambda: app.state.store.finish_reply(draft["id"], "draft"))
    client.portal.call(lambda: app.state.store.finish_reply(approved["id"], "approved"))
    client.portal.call(lambda: app.state.store.finish_reply(rejected["id"], "rejected"))

    async def old_setting():
        async with app.state.store.transaction() as db:
            await db.execute("UPDATE routes SET value=json_set(value,'$.auto_reply',json('false'))")

    client.portal.call(old_setting)
    other = Store(tmp_path / "gateway.sqlite3")
    client.portal.call(other.open)
    upgraded = {row["id"]: row for row in client.portal.call(other.replies)}
    assert upgraded[draft["id"]]["state"] == "blocked"
    assert upgraded[approved["id"]]["state"] == "queued"
    assert upgraded[rejected["id"]]["state"] == "rejected"
    stored = client.portal.call(other.routes)[0]
    assert "auto_reply" not in stored and stored["access_mode"] == "restricted"
    assert stored["allowed_user_ids"] == ["7"]
    client.portal.call(other.close)
    tick(gateway)
    assert len(upstream.requests) == 1
    assert json.loads(upstream.requests[0].content)["message"][0]["data"]["text"] == "Previously approved content"


def test_route_revocation_blocks_a_queued_reply(gateway):
    client, app, upstream, *_ = gateway
    configured = route(gateway)
    client.portal.call(app.state.store.queue_reply, configured, "Do not send")
    assert client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": False}).status_code == 200
    tick(gateway)
    assert not upstream.requests
    assert client.get("/api/v1/replies").json()[0]["state"] == "blocked"


@pytest.mark.parametrize("kind,conversation", [("private", "7"), ("group", "123")])
def test_conversation_history_merges_received_and_sent_messages_with_pagination(gateway, kind, conversation):
    client, app, upstream, channel, host = gateway
    webhook(gateway, kind=kind, text="Before route creation")
    route(gateway, kind=kind, conversation=conversation)
    webhook(gateway, kind=kind, message_id=2, text="Routed input")
    tick(gateway)
    tick(gateway)
    webhook(gateway, kind=kind, message_id=3, text="Next input")
    other = client.post(
        "/api/v1/connections",
        json={
            "name": "Other account",
            "kind": "onebots",
            "api_url": "https://other.test",
            "self_id": "99",
            "token": "other-token",
            "webhook_secret": SECRET,
        },
    ).json()
    other_gateway = (client, app, upstream, other, host)
    webhook(other_gateway, kind=kind, text="Other account history")
    opposite = "group" if kind == "private" else "private"
    webhook(gateway, kind=opposite, message_id=4, text="Other conversation")
    params = {"channel_id": channel["id"], "conversation_kind": kind, "conversation_id": conversation}
    response = client.get("/api/v1/history", params=params)
    assert response.status_code == 200, response.text
    history = response.json()["messages"]
    assert len(history) == 4
    assert {row["text"] for row in history} == {
        "Before route creation",
        "Routed input",
        "Next input",
        "Hello from the agent",
    }
    assert {row["direction"] for row in history} == {"incoming", "outgoing"}
    assert all(key not in response.text for key in ("host-token", SECRET, "other-token", "context_token", '"body"'))
    first = client.get("/api/v1/history", params={**params, "limit": 2}).json()
    assert first["has_more"]
    oldest = first["messages"][0]
    webhook(gateway, kind=kind, message_id=5, text="New arrival after pagination")
    second = client.get(
        "/api/v1/history",
        params={**params, "limit": 2, "before_created_at": oldest["created_at"], "before_id": oldest["id"]},
    ).json()
    assert not second["has_more"]
    assert [row["id"] for row in second["messages"] + first["messages"]] == [row["id"] for row in history]
    assert client.get("/api/v1/history", params={**params, "before_id": oldest["id"]}).status_code == 422
    assert client.get("/api/v1/history", params={**params, "limit": 101}).status_code == 422
    client.cookies.clear()
    assert client.get("/api/v1/history", params=params).status_code == 401


def rpc(client, token, method, params):
    return client.post(
        "/mcp/",
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )


def test_mcp_credentials_scope_history_and_actions_and_rotate(gateway):
    client, _, upstream, *_ = gateway
    group = route(gateway, source="mcp")
    private = route(gateway, kind="private", conversation="7", source="mcp")
    webhook(gateway, text="Group message")
    webhook(gateway, kind="private", message_id=2, text="Private secret")
    token = group["agent_token"]
    response = rpc(client, token, "tools/call", {"name": "read_conversation", "arguments": {}})
    assert response.status_code == 200, response.text
    assert "Group message" in response.text and "Private secret" not in response.text
    sent = rpc(client, token, "tools/call", {"name": "send_message", "arguments": {"text": "Requested reply"}})
    assert sent.status_code == 200 and "queued" in sent.text
    assert client.get("/api/v1/replies").json()[0]["route_id"] == group["id"]
    assert not upstream.requests
    assert rpc(client, private["agent_token"], "tools/list", {}).status_code == 200
    client.post(f"/api/v1/routes/{group['id']}/token")
    assert rpc(client, token, "tools/list", {}).status_code == 401


def test_mcp_does_not_accept_administrator_credentials_or_browser_origins(gateway):
    client, *_ = gateway
    configured = route(gateway)
    assert rpc(client, ADMIN_PASSWORD, "tools/list", {}).status_code == 401
    response = client.post(
        "/mcp/",
        headers={"Authorization": "Bearer " + configured["agent_token"], "Origin": "https://evil.test"},
        json={},
    )
    assert response.status_code == 403


def test_mcp_reads_authorized_history_independently_of_ingress_routing(gateway):
    client, app, upstream, _, host = gateway
    webhook(gateway, text="Before route", message_id=10)
    configured = route(gateway)
    client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": False})
    webhook(gateway, text="While disabled", message_id=11)
    client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": True})
    webhook(gateway, text="Routed message", message_id=12)
    webhook(gateway, text="Denied sender", user=8, message_id=13)
    webhook(gateway, text="Other conversation", conversation=456, message_id=14)

    def read(limit):
        result = rpc(
            client,
            host["mcp_key"],
            "tools/call",
            {"name": "read_conversation", "arguments": {"route_id": configured["id"], "limit": limit}},
        ).json()["result"]
        assert not result.get("isError")
        return json.loads(result["content"][0]["text"])["messages"]

    assert [m["text"] for m in read(20)] == ["Before route", "While disabled", "Routed message"]
    assert [m["text"] for m in read(1)] == ["Routed message"]
    client.patch(f"/api/v1/routes/{configured['id']}", json={"allowed_user_ids": ["7", "8"]})
    assert [m["text"] for m in read(20)] == ["Before route", "While disabled", "Routed message", "Denied sender"]
    assert len(client.portal.call(app.state.store.pending)) == 1
    assert not upstream.requests


def test_mcp_observed_history_does_not_restore_deleted_route_history(gateway):
    client, _, _, _, host = gateway
    configured = route(gateway)
    webhook(gateway, text="Archived route history", message_id=21)
    assert client.delete(f"/api/v1/routes/{configured['id']}").status_code == 200
    webhook(gateway, text="Received without a route", message_id=22)
    replacement = route(gateway)
    read = rpc(
        client,
        host["mcp_key"],
        "tools/call",
        {
            "name": "read_conversation",
            "arguments": {"route_id": replacement["id"]},
        },
    )
    assert "Received without a route" in read.text
    assert "Archived route history" not in read.text


def test_mcp_all_conversation_scope_discovers_previously_received_conversations(gateway):
    client, app, upstream, _, host = gateway
    webhook(gateway, kind="private", text="Previously received private text", message_id=31)
    webhook(gateway, kind="private", user=8, text="Explicitly denied", message_id=32)
    specific = route(gateway, kind="private", conversation="8")
    client.patch(f"/api/v1/routes/{specific['id']}", json={"enabled": False})
    parent = catchall(gateway)
    for key in (host["mcp_key"], parent["agent_token"]):
        listing = rpc(client, key, "tools/call", {"name": "list_conversations", "arguments": {}}).json()["result"]
        conversations = json.loads(listing["content"][0]["text"])["conversations"]
        assert [row["conversation_id"] for row in conversations] == ["7"]
        read = rpc(
            client,
            key,
            "tools/call",
            {
                "name": "read_conversation",
                "arguments": {"route_id": conversations[0]["id"]},
            },
        )
        assert "Previously received private text" in read.text and "Explicitly denied" not in read.text
    assert not client.portal.call(app.state.store.pending)
    assert not upstream.requests


def test_structured_output_is_not_serialized_into_a_channel(gateway):
    client, _, upstream, *_ = gateway
    upstream.output = {"private_record": {"password": "not-a-public-reply"}}
    route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    assert client.get("/api/v1/replies").json() == []
    assert client.get("/api/v1/events").json()[0]["state"] == "completed"


def test_sent_replies_and_thread_binding_survive_restart_without_resending(gateway, tmp_path):
    client, _, upstream, *_ = gateway
    route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    rows = client.get("/api/v1/replies").json()
    # Read the same durable file from an independent store instance; no HTTP
    # client or Agent lifecycle is persisted in memory.
    from channel_gateway_server.storage import Store

    other = Store(tmp_path / "gateway.sqlite3")
    client.portal.call(other.open)
    assert client.portal.call(other.replies)[0]["id"] == rows[0]["id"]
    assert client.portal.call(other.routes)[0]["thread_id"] == "thread_1"
    client.portal.call(other.close)
    assert len(upstream.requests) == 3


def test_access_changes_reauthorize_pending_events_and_mcp_history(gateway):
    client, _, upstream, *_ = gateway
    configured = route(gateway, source="mcp")
    webhook(gateway)
    response = client.patch(
        f"/api/v1/routes/{configured['id']}",
        json={
            "allowed_user_ids": ["8"],
            "reply_source": configured["reply_source"],
            "mcp_connection_id": configured["mcp_connection_id"],
        },
    )
    assert response.status_code == 200
    tick(gateway)
    assert not upstream.requests
    assert client.get("/api/v1/events").json()[0]["state"] == "blocked"
    response = rpc(client, configured["agent_token"], "tools/call", {"name": "read_conversation", "arguments": {}})
    assert "Hello" not in response.text


def test_mcp_connection_override_is_scoped_and_cannot_change_in_flight(gateway):
    client, _, upstream, *_ = gateway
    configured = route(gateway, source="mcp")
    webhook(gateway)
    changed = client.patch(f"/api/v1/routes/{configured['id']}", json={"mcp_connection_id": "conn_other"})
    assert changed.status_code == 409
    tick(gateway)
    selections = json.loads(upstream.requests[0].content)["options"]["overrides"]["connection_tools"]
    assert selections == [
        {"connection_id": configured["mcp_connection_id"], "tools": ["read_conversation", "send_message"]}
    ]
    tick(gateway)
    assert client.get("/api/v1/replies").json() == []


def test_credential_rotation_does_not_disclose_replacement_secrets(gateway):
    client, _, _, channel, _ = gateway
    rotated = client.patch(
        f"/api/v1/connections/{channel['id']}",
        json={"token": "new-bot-token", "webhook_secret": "new-onebot-signing-secret"},
    )
    assert rotated.status_code == 200
    assert "new-bot-token" not in rotated.text and "new-onebot-signing-secret" not in rotated.text
    assert webhook(gateway).status_code == 401


def test_interrupted_outbound_delivery_becomes_unknown_on_restart(gateway, tmp_path):
    from channel_gateway_server.storage import Store

    client, app, *_ = gateway
    configured = route(gateway)
    draft = client.portal.call(lambda: app.state.store.queue_reply(configured, "Queued before restart"))
    assert client.portal.call(app.state.store.claim_reply, draft["id"])
    other = Store(tmp_path / "gateway.sqlite3")
    client.portal.call(other.open)
    assert client.portal.call(other.replies)[0]["state"] == "unknown"
    client.portal.call(other.close)


def test_stale_route_writes_cannot_restore_revoked_access(gateway):
    client, app, *_ = gateway
    configured = route(gateway)
    old = client.portal.call(app.state.store.route, configured["id"])
    value = {key: item for key, item in old.items() if key != "thread_id"}
    disabled = {**value, "enabled": False}
    assert client.portal.call(app.state.store.update_route, configured["id"], disabled, old)
    stale_edit = {**value, "allowed_user_ids": ["8"]}
    assert not client.portal.call(app.state.store.update_route, configured["id"], stale_edit, old)
    assert not client.get("/api/v1/routes").json()[0]["enabled"]


def test_channel_deletion_preserves_history_and_blocks_pending_work(gateway):
    client, app, upstream, channel, host = gateway
    configured = route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    historical = client.get("/api/v1/replies").json()[0]
    webhook(gateway, message_id=2)
    tick(gateway)
    webhook(gateway, message_id=3)
    pending = client.portal.call(app.state.store.queue_reply, configured, "Pending reply")
    sent = client.portal.call(app.state.store.queue_reply, configured, "Already sent")
    client.portal.call(lambda: app.state.store.finish_reply(sent["id"], "sent", message_id="receipt"))
    calls = len(upstream.requests)
    assert client.delete(f"/api/v1/connections/{channel['id']}").status_code == 200
    assert client.portal.call(app.state.store.connection, channel["id"]) is None
    assert client.portal.call(app.state.store.connection, host["id"]) is not None
    stored = client.get("/api/v1/routes").json()[0]
    assert not stored["enabled"] and stored["thread_id"] == "thread_1"
    assert sorted(row["state"] for row in client.get("/api/v1/events").json()) == ["blocked", "blocked", "completed"]
    replies = {row["id"]: row for row in client.get("/api/v1/replies").json()}
    assert replies[historical["id"]]["state"] == "sent"
    assert replies[pending["id"]]["state"] == "blocked"
    assert replies[sent["id"]]["state"] == "sent" and replies[sent["id"]]["message_id"] == "receipt"
    assert not client.portal.call(
        app.state.store.rows, "SELECT * FROM channel_state WHERE connection_id=?", (channel["id"],)
    )
    assert client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": True}).status_code == 409
    assert webhook(gateway, message_id=4).status_code == 404
    assert client.delete(f"/api/v1/connections/{channel['id']}").status_code == 404
    tick(gateway)
    assert len(upstream.requests) == calls


def test_channel_deletion_revokes_parent_and_child_mcp_access(gateway):
    client, app, _, channel, _ = gateway
    host = client.post("/api/v1/connections", json={"name": "Client", "kind": "mcp"}).json()
    parent = catchall(gateway, host=host)
    webhook(gateway, kind="private", text="Retained private history")
    tick(gateway)
    old = client.portal.call(app.state.store.route, parent["id"])
    assert client.delete(f"/api/v1/connections/{channel['id']}").status_code == 200
    routes = client.get("/api/v1/routes").json()
    assert len(routes) == 2 and all(not row["enabled"] for row in routes)
    assert rpc(client, parent["agent_token"], "tools/list", {}).status_code == 401
    listing = rpc(client, host["mcp_key"], "tools/call", {"name": "list_conversations", "arguments": {}})
    assert listing.status_code == 200 and all(row["id"] not in listing.text for row in routes)
    value = {key: item for key, item in old.items() if key != "thread_id"}
    assert not client.portal.call(app.state.store.update_route, parent["id"], value, old)
    assert client.get("/api/v1/events").json()[0]["message"]["text"] == "Retained private history"


def test_channel_deletion_requires_admin_and_csrf(gateway):
    client, _, _, channel, _ = gateway
    path = f"/api/v1/connections/{channel['id']}"
    assert client.delete(path, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    assert client.delete(path, headers={"Origin": "https://other.test"}).status_code == 403
    client.cookies.clear()
    assert client.delete(path).status_code == 401


@pytest.mark.parametrize("kind", ["a13n", "mcp"])
def test_host_deletion_revokes_keys_and_stops_only_assigned_routes(gateway, kind):
    client, app, upstream, channel, existing_host = gateway
    if kind == "a13n":
        host, configured = existing_host, route(gateway)
    else:
        host, configured = mcp_host_route(gateway, conversation="123")
    webhook(gateway)
    client.portal.call(app.state.store.queue_reply, configured, "Pending outbound")
    assert client.delete(f"/api/v1/connections/{host['id']}").status_code == 200
    assert client.portal.call(app.state.store.connection, channel["id"]) is not None
    assert client.portal.call(app.state.store.connection, host["id"]) is None
    assert not client.portal.call(app.state.store.rows, "SELECT * FROM host_keys WHERE host_id=?", (host["id"],))
    assert rpc(client, host["mcp_key"], "tools/list", {}).status_code == 401
    assert rpc(client, configured["agent_token"], "tools/list", {}).status_code == 401
    assert not client.get("/api/v1/routes").json()[0]["enabled"]
    tick(gateway)
    assert not upstream.requests
    assert client.get("/api/v1/events").json()[0]["state"] == "blocked"
    assert client.get("/api/v1/replies").json()[0]["state"] == "blocked"
    assert client.delete(f"/api/v1/connections/{host['id']}").status_code == 404


@pytest.mark.parametrize("all_conversations", [False, True])
def test_route_deletion_releases_binding_and_retains_conversation_history(gateway, all_conversations):
    client, app, upstream, channel, _ = gateway
    configured = catchall(gateway, kind="group") if all_conversations else route(gateway)
    first = webhook(gateway).json()["event_id"]
    tick(gateway)
    tick(gateway)
    webhook(gateway, message_id=2)
    tick(gateway)
    webhook(gateway, message_id=3)
    concrete = next(row for row in client.get("/api/v1/routes").json() if row["conversation_id"] == "123")
    client.portal.call(app.state.store.queue_reply, concrete, "Do not send after removal")

    async def legacy_address():
        async with app.state.store.transaction() as db:
            await db.execute("UPDATE events SET message=json_remove(message,'$.address') WHERE id=?", (first,))

    client.portal.call(legacy_address)
    path = f"/api/v1/routes/{configured['id']}"
    assert client.delete(path, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    before = len(upstream.requests)
    assert client.delete(path).status_code == 200
    assert client.get("/api/v1/routes").json() == []
    assert rpc(client, configured["agent_token"], "tools/list", {}).status_code == 401
    tick(gateway)
    assert len(upstream.requests) == before
    params = {"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "123"}
    history = client.get("/api/v1/history", params=params).json()["messages"]
    assert len(history) == 5
    assert {row["direction"] for row in history} == {"incoming", "outgoing"}
    assert any(row["text"] == "Hello from the agent" and row["state"] == "sent" for row in history)
    assert any(row["text"] == "Do not send after removal" and row["state"] == "blocked" for row in history)
    assert all(
        reply.get("address") == {"account_id": channel["id"], "conversation_id": "group:123"}
        for reply in client.get("/api/v1/replies").json()
    )
    assert all(
        event["message"]["address"]["account_id"] == channel["id"] for event in client.get("/api/v1/events").json()
    )
    if all_conversations:
        with pytest.raises(ValueError):
            client.portal.call(app.state.store.conversation_route, configured, "456")
    replacement = route(gateway)
    assert replacement["id"] != configured["id"] and replacement["thread_id"] is None
    assert client.delete(path).status_code == 404
    client.cookies.clear()
    assert client.delete(f"/api/v1/routes/{replacement['id']}").status_code == 401


def test_shared_state_directory_is_refused_without_changing_permissions(gateway, tmp_path):
    from channel_gateway_server.storage import Store

    client, *_ = gateway
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    state = Store(shared / "gateway.sqlite3")
    with pytest.raises(ValueError, match="must be private"):
        client.portal.call(state.open)
    assert shared.stat().st_mode & 0o777 == 0o755
    assert not state.path.exists()


def mcp_host_route(gateway, *, conversation="456"):
    client, _, _, channel, _ = gateway
    host = client.post("/api/v1/connections", json={"name": "Codex", "kind": "mcp"}).json()
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Codex conversation",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "conversation_kind": "group",
            "conversation_id": conversation,
            "allowed_user_ids": ["7"],
            "reply_source": "mcp",
        },
    )
    assert response.status_code == 201, response.text
    return host, response.json()


def test_host_keys_enforce_assignment_and_explicit_conversation_selection(gateway):
    client, _, upstream, *_ = gateway
    host, configured = mcp_host_route(gateway)
    other = route(gateway, source="mcp")
    webhook(gateway, conversation=456, text="Codex input")
    webhook(gateway, text="Other host private content", message_id=2)
    key = host["mcp_key"]
    listed = rpc(client, key, "tools/call", {"name": "list_conversations", "arguments": {}})
    assert configured["id"] in listed.text and other["id"] not in listed.text
    assert "Codex input" not in listed.text
    for tool, args in (("read_conversation", {}), ("send_message", {"text": "Not allowed"})):
        denied = rpc(client, key, "tools/call", {"name": tool, "arguments": {**args, "route_id": other["id"]}})
        assert denied.json()["result"]["isError"] is True
        assert "Other host private content" not in denied.text
    missing = rpc(client, key, "tools/call", {"name": "read_conversation", "arguments": {}})
    assert missing.json()["result"]["isError"] is True
    read = rpc(client, key, "tools/call", {"name": "read_conversation", "arguments": {"route_id": configured["id"]}})
    assert "Codex input" in read.text and "Other host private content" not in read.text
    # Existing per-route capabilities also cannot select another route.
    denied = rpc(
        client,
        other["agent_token"],
        "tools/call",
        {"name": "read_conversation", "arguments": {"route_id": configured["id"]}},
    )
    assert denied.json()["result"]["isError"] is True
    sent = rpc(
        client,
        key,
        "tools/call",
        {"name": "send_message", "arguments": {"route_id": configured["id"], "text": "Draft reply"}},
    )
    assert "queued" in sent.text and not upstream.requests
    assert client.get("/api/v1/replies").json()[0]["route_id"] == configured["id"]


def test_mcp_pull_inputs_never_submit_or_claim_run_completion(gateway):
    client, _, upstream, *_ = gateway
    host, configured = mcp_host_route(gateway)
    webhook(gateway, conversation=456)
    webhook(gateway, conversation=456, message_id=2)
    tick(gateway)
    tick(gateway)
    assert not upstream.requests
    events = client.get("/api/v1/events").json()
    assert len(events) == 2 and all(event["state"] == "available" for event in events)
    assert all(event["run_id"] is None and event["thread_id"] is None for event in events)
    rpc(
        client,
        host["mcp_key"],
        "tools/call",
        {"name": "read_conversation", "arguments": {"route_id": configured["id"]}},
    )
    assert all(event["state"] == "available" for event in client.get("/api/v1/events").json())
    assert client.get("/api/v1/replies").json() == []


def test_host_key_rotation_and_current_route_access(gateway):
    client, app, _, channel, a13n = gateway
    host, configured = mcp_host_route(gateway)
    webhook(gateway, conversation=456)
    key = host["mcp_key"]
    assert key not in client.get("/api/v1/connections").text
    assert client.portal.call(app.state.store.host_for_token, key)["id"] == host["id"]
    assert client.post(f"/api/v1/connections/{channel['id']}/mcp-key").status_code == 404
    rotated = client.post(f"/api/v1/connections/{host['id']}/mcp-key").json()["mcp_key"]
    assert rpc(client, key, "tools/list", {}).status_code == 401
    assert rpc(client, rotated, "tools/list", {}).status_code == 200
    client.patch(f"/api/v1/routes/{configured['id']}", json={"allowed_user_ids": ["8"]})
    read = rpc(
        client, rotated, "tools/call", {"name": "read_conversation", "arguments": {"route_id": configured["id"]}}
    )
    assert "Hello" not in read.text
    client.patch(f"/api/v1/routes/{configured['id']}", json={"enabled": False})
    listed = rpc(client, rotated, "tools/call", {"name": "list_conversations", "arguments": {}})
    assert configured["id"] not in listed.text
    denied = rpc(
        client,
        rotated,
        "tools/call",
        {"name": "send_message", "arguments": {"route_id": configured["id"], "text": "Blocked"}},
    )
    assert denied.json()["result"]["isError"] is True
    # API + MCP hosts receive independent keys as well.
    other = route(gateway)
    listed = rpc(client, a13n["mcp_key"], "tools/call", {"name": "list_conversations", "arguments": {}})
    assert other["id"] in listed.text and configured["id"] not in listed.text


def test_mcp_hosts_reject_upstream_credentials_and_a13n_bindings(gateway):
    client, _, _, channel, host = gateway
    assert (
        client.post("/api/v1/connections", json={"name": "Wrong", "kind": "mcp", "token": "must-not-leak"}).status_code
        == 422
    )
    generic = client.post("/api/v1/connections", json={"name": "Codex", "kind": "mcp"}).json()
    payload = {
        "name": "Test",
        "channel_id": channel["id"],
        "host_id": generic["id"],
        "conversation_kind": "private",
        "conversation_id": "7",
        "allowed_user_ids": ["7"],
        "reply_source": "mcp",
    }
    for field in ({"agent_id": "ap_wrong"}, {"mcp_connection_id": "conn_wrong"}, {"reply_source": "output"}):
        assert client.post("/api/v1/routes", json={**payload, **field}).status_code == 422
    assert (
        client.post("/api/v1/routes", json={**payload, "host_id": host["id"], "reply_source": "output"}).status_code
        == 422
    )
    created = client.post("/api/v1/routes", json=payload).json()
    assert client.patch(f"/api/v1/routes/{created['id']}", json={"reply_source": "output"}).status_code == 422


def catchall(gateway, *, kind="private", access="full", host=None):
    client, _, _, channel, configured_host = gateway
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Personal assistant",
            "channel_id": channel["id"],
            "host_id": (host or configured_host)["id"],
            "conversation_kind": kind,
            "conversation_id": "*",
            "sender_policy": "any",
            "access_mode": access,
            "agent_id": None if host else "agt_example",
            "reply_source": "mcp" if host else "output",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_unrouted_messages_discover_identity_without_delivery_or_mcp_access(gateway):
    client, _, upstream, _, host = gateway
    webhook(gateway, kind="private", text="Discover my sender")
    webhook(gateway, kind="private", text="Discover my sender")
    rows = client.get("/api/v1/events").json()
    assert len(rows) == 1
    assert rows[0]["state"] == "unmatched" and rows[0]["route_id"] is None
    assert rows[0]["message"]["sender_id"] == "7"
    assert rows[0]["message"]["address"]["conversation_id"] == "private:7"
    tick(gateway)
    assert not upstream.requests
    assert (
        "Discover my sender"
        not in rpc(client, host["mcp_key"], "tools/call", {"name": "list_conversations", "arguments": {}}).text
    )
    route(gateway, kind="private", conversation="7")
    # Creating a route never replays earlier observations, even on provider redelivery.
    assert webhook(gateway, kind="private").json()["state"] == "duplicate"
    tick(gateway)
    assert not upstream.requests
    assert webhook(gateway, kind="private", message_id=2).json()["state"] == "accepted"
    tick(gateway)
    assert len(upstream.requests) == 1


def test_all_conversations_create_independent_bindings_and_reuse_each_thread(gateway):
    client, _, upstream, *_ = gateway
    upstream.unique_threads = True
    parent = catchall(gateway)
    webhook(gateway, kind="private", user=7)
    webhook(gateway, kind="private", user=8)
    tick(gateway)
    submissions = [request for request in upstream.requests if request.method == "POST"]
    assert len(submissions) == 2 and all(request.url.path.endswith("/threads") for request in submissions)
    assert all("options" not in json.loads(request.content) for request in submissions)
    children = [row for row in client.get("/api/v1/routes").json() if row["parent_route_id"] == parent["id"]]
    assert len(children) == 2
    assert len({child["thread_id"] for child in children}) == 2
    tick(gateway)
    webhook(gateway, kind="private", user=7, message_id=2)
    webhook(gateway, kind="private", user=8, message_id=2)
    tick(gateway)
    assert {str(request.url.path) for request in upstream.requests[-2:]} == {
        "/api/v1/threads/thread_1/inbox",
        "/api/v1/threads/thread_2/inbox",
    }
    assert client.patch(f"/api/v1/routes/{children[0]['id']}", json={"enabled": False}).status_code == 422


def test_specific_route_precedes_catchall_even_when_disabled(gateway):
    client, _, upstream, *_ = gateway
    specific = route(gateway, kind="private", conversation="7")
    catchall(gateway)
    client.patch(f"/api/v1/routes/{specific['id']}", json={"enabled": False})
    assert webhook(gateway, kind="private").json()["state"] == "blocked"
    tick(gateway)
    assert not upstream.requests
    assert webhook(gateway, kind="private", user=8).json()["state"] == "accepted"
    tick(gateway)
    assert len(upstream.requests) == 1


def test_parent_policy_revokes_child_ingress_pending_work_and_mcp_history(gateway):
    client, _, upstream, *_ = gateway
    host = client.post("/api/v1/connections", json={"name": "Client", "kind": "mcp"}).json()
    parent = catchall(gateway, host=host, access="restricted")
    webhook(gateway, kind="private", user=7, text="First sender")
    webhook(gateway, kind="private", user=8, text="Second sender")
    child = next(row for row in client.get("/api/v1/routes").json() if row["conversation_id"] == "8")
    assert (
        client.patch(
            f"/api/v1/routes/{parent['id']}", json={"sender_policy": "allowlist", "allowed_user_ids": ["7"]}
        ).status_code
        == 200
    )
    tick(gateway)
    assert not upstream.requests
    events = client.get("/api/v1/events").json()
    assert {row["message"]["sender_id"]: row["state"] for row in events} == {"7": "available", "8": "blocked"}
    response = rpc(
        client,
        parent["agent_token"],
        "tools/call",
        {"name": "read_conversation", "arguments": {"route_id": child["id"]}},
    )
    assert "Second sender" not in response.text
    client.patch(f"/api/v1/routes/{parent['id']}", json={"enabled": False})
    assert rpc(client, parent["agent_token"], "tools/list", {}).status_code == 401
    listing = rpc(client, host["mcp_key"], "tools/call", {"name": "list_conversations", "arguments": {}})
    assert child["id"] not in listing.text
    assert webhook(gateway, kind="private", user=7, message_id=3).json()["state"] == "blocked"


def test_full_group_access_retains_tools_and_automatically_replies(gateway):
    client, _, upstream, *_ = gateway
    catchall(gateway, kind="group")
    webhook(gateway, user=8)
    tick(gateway)
    tick(gateway)
    assert client.get("/api/v1/replies").json()[0]["state"] == "sent"
    assert "options" not in json.loads(upstream.requests[0].content)


def test_full_access_downgrade_keeps_automatic_group_delivery(gateway):
    client, app, upstream, *_ = gateway
    parent = catchall(gateway, kind="group")
    child = client.portal.call(app.state.store.conversation_route, parent, "123")
    reply = client.portal.call(app.state.store.queue_reply, child, "Automatic reply after downgrade")
    assert reply["state"] == "queued"
    response = client.patch(f"/api/v1/routes/{parent['id']}", json={"access_mode": "restricted"})
    assert response.status_code == 200, response.text
    tick(gateway)
    assert len(upstream.requests) == 1
    assert client.get("/api/v1/replies").json()[0]["state"] == "sent"


def test_permission_mode_change_waits_for_current_run(gateway):
    client, *_ = gateway
    parent = catchall(gateway)
    webhook(gateway, kind="private")
    response = client.patch(f"/api/v1/routes/{parent['id']}", json={"access_mode": "restricted"})
    assert response.status_code == 409
