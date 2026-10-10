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

ADMIN_TOKEN = "test-administrator-token-0123456789"
SECRET = "test-onebot-signing-secret"


class Upstreams:
    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail_submit = False
        self.fail_send = False
        self.queued_receipt = False
        self.run_status = "completed"
        self.output = "Hello from the agent"

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
            return httpx.Response(
                201,
                json={
                    "thread": {"id": "thread_1"},
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
    settings = Settings(admin_token=ADMIN_TOKEN, data_dir=tmp_path, worker_enabled=False)
    app = create_app(settings=settings, static_dir=tmp_path, client=transport)
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + ADMIN_TOKEN
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


def route(gateway, *, kind="group", auto=False, source="output", conversation="123"):
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
            "auto_reply": auto,
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
    assert webhook(gateway, user=8).json()["state"] == "blocked"
    assert webhook(gateway, self_id=100).json()["state"] == "ignored"
    assert webhook(gateway).json()["state"] == "accepted"
    assert webhook(gateway).json()["state"] == "duplicate"
    assert len(client.get("/api/v1/events").json()) == 1
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


def test_group_reply_requires_approval_and_does_not_repeat(gateway):
    client, _, upstream, _, _ = gateway
    configured = route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    replies = client.get("/api/v1/replies").json()
    assert replies[0]["state"] == "draft"
    assert not any(r.url.path.endswith("send_msg") for r in upstream.requests)
    assert (
        client.post(f"/api/v1/replies/{replies[0]['id']}/approve", json={"text": "Reviewed group reply"}).status_code
        == 200
    )
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
    assert upstream.requests[-2].url.path == "/api/v1/threads/thread_1/inbox/entry_1"
    assert upstream.requests[-1].url.path == "/api/v1/runs/run_1"


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
    route(gateway, kind="private", auto=True, conversation="7")
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
    client.headers.pop("Authorization")
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


def test_group_automatic_reply_is_refused(gateway):
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
    assert sent.status_code == 200 and "draft" in sent.text
    assert client.get("/api/v1/replies").json()[0]["route_id"] == group["id"]
    assert not upstream.requests
    assert rpc(client, private["agent_token"], "tools/list", {}).status_code == 200
    client.post(f"/api/v1/routes/{group['id']}/token")
    assert rpc(client, token, "tools/list", {}).status_code == 401


def test_mcp_does_not_accept_administrator_tokens_or_browser_origins(gateway):
    client, *_ = gateway
    configured = route(gateway)
    assert rpc(client, ADMIN_TOKEN, "tools/list", {}).status_code == 401
    response = client.post(
        "/mcp/",
        headers={"Authorization": "Bearer " + configured["agent_token"], "Origin": "https://evil.test"},
        json={},
    )
    assert response.status_code == 403


def test_structured_output_is_not_serialized_into_a_channel(gateway):
    client, _, upstream, *_ = gateway
    upstream.output = {"private_record": {"password": "not-a-public-reply"}}
    route(gateway)
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    assert client.get("/api/v1/replies").json() == []
    assert client.get("/api/v1/events").json()[0]["state"] == "completed"


def test_drafts_and_thread_binding_survive_restart_without_resending(gateway, tmp_path):
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
    assert len(upstream.requests) == 2


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
    draft = client.portal.call(lambda: app.state.store.draft(configured, "Approved before restart"))
    client.portal.call(lambda: app.state.store.decide(draft["id"], approve=True))
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
