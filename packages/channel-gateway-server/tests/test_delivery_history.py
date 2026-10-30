"""Bounded, authorized chat snapshots and independent default executions."""

import json
from functools import partial

import pytest

from .test_bridge import gateway as gateway_fixture
from .test_bridge import route, send_tool, tick, webhook
from .test_thread_delivery import submissions

gateway = gateway_fixture


def context(request):
    text = json.loads(request.content)["payload"]["content"][0]["text"]
    value = json.loads(text.split("\n", 1)[1].split("\n\nMessage:\n", 1)[0])
    value["history"] = next(
        (
            part["value"]["channel_history"]
            for part in json.loads(request.content)["payload"]["content"]
            if part["type"] == "json" and "channel_history" in part["value"]
        ),
        [],
    )
    return value


def test_default_execution_is_fresh_with_latest_thirty_messages(gateway):
    client, _, upstream, channel, host = gateway
    upstream.unique_threads = True
    configured = client.post(
        "/api/v1/routes",
        json={
            "name": "Bounded",
            "channel_id": channel["id"],
            "host_id": host["id"],
            "agent_id": "agent",
            "conversation_kind": "group",
            "conversation_id": "123",
            "allowed_user_ids": ["7"],
        },
    ).json()
    assert configured["thread_policy"] == "window"
    assert configured["history_limit"] == 30
    for index in range(33):
        webhook(gateway, message_id=index, text=f"Message {index}")
        tick(gateway)
        tick(gateway)
    requests = submissions(gateway)
    assert len(requests) == 33
    assert all(request.url.path == "/api/v1/threads" for request in requests)
    assert context(requests[0])["history"] == []
    assert "environments" not in json.loads(requests[0].content)
    assert all(json.loads(r.content)["environments"][0]["environment_id"] == "env_1" for r in requests[1:])
    history = context(requests[-1])["history"]
    assert [item["text"] for item in history] == [f"Message {index}" for index in range(2, 32)]
    assert len({item["thread_id"] for item in client.get("/api/v1/events").json()}) == 33


def test_history_includes_only_authorized_same_conversation_and_confirmed_sends(gateway):
    configured = route(gateway)
    route(gateway, conversation="124")
    webhook(gateway, text="Earlier", message_id=1)
    tick(gateway)
    send_tool(gateway)
    tick(gateway)
    webhook(gateway, user=8, text="Denied sender", message_id=2)
    webhook(gateway, conversation=124, text="Other group", message_id=3)
    webhook(gateway, text="Current", message_id=4)
    tick(gateway)
    request = next(r for r in reversed(submissions(gateway)) if context(r)["route_id"] == configured["id"])
    history = context(request)["history"]
    assert [item["direction"] for item in history] == ["incoming", "outgoing"]
    assert history[0]["text"] == "Earlier"
    assert "Denied sender" not in json.dumps(history)
    assert "Other group" not in json.dumps(history)
    assert "Current" not in json.dumps(history)


@pytest.mark.parametrize("limit", [0, 1, 100])
def test_history_limit_updates_and_frozen_retries(gateway, limit):
    configured = route(gateway)
    webhook(gateway, message_id=1, text="Earlier")
    tick(gateway)
    tick(gateway)
    client = gateway[0]
    assert client.patch(f"/api/v1/routes/{configured['id']}", json={"history_limit": limit}).status_code == 200
    gateway[2].fail_submit = True
    webhook(gateway, message_id=2)
    tick(gateway)
    frozen = submissions(gateway)[-1]
    assert len(context(frozen)["history"]) == min(limit, 1)
    client.patch(f"/api/v1/routes/{configured['id']}", json={"history_limit": 0 if limit else 10})
    tick(gateway)
    assert submissions(gateway)[-1].content == frozen.content
    assert submissions(gateway)[-1].headers["Idempotency-Key"] == frozen.headers["Idempotency-Key"]


@pytest.mark.parametrize("limit", [-1, 101, 1.5, True, "30", None])
def test_invalid_history_limits_rejected(gateway, limit):
    configured = route(gateway)
    assert gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"history_limit": limit}).status_code == 422


def test_no_read_grant_excludes_history(gateway):
    configured = route(gateway)
    webhook(gateway, message_id=1)
    tick(gateway)
    tick(gateway)
    gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"read_channel_ids": []})
    webhook(gateway, message_id=2)
    tick(gateway)
    assert context(submissions(gateway)[-1])["history"] == []


def test_legacy_upgrade_is_once_and_preserves_frozen_requests(gateway):
    configured = route(gateway)
    client, app, *_ = gateway
    gateway[2].fail_submit = True
    webhook(gateway)
    tick(gateway)
    frozen = submissions(gateway)[-1]

    async def legacy():
        async with app.state.store.transaction() as db:
            await db.execute(
                "UPDATE routes SET value=json_remove(value,'$.history_limit') WHERE id=?", (configured["id"],)
            )

    client.portal.call(legacy)
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    migrated = client.get("/api/v1/routes").json()[0]
    assert migrated["thread_policy"] == "window"
    assert migrated["history_limit"] == 30
    tick(gateway)
    assert submissions(gateway)[-1].content == frozen.content
    assert submissions(gateway)[-1].url == frozen.url
    tick(gateway)
    assert client.patch(f"/api/v1/routes/{configured['id']}", json={"thread_policy": "sender"}).status_code == 200
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    assert client.portal.call(partial(app.state.store.route, configured["id"]))["thread_policy"] == "sender"


def test_window_steers_active_work_without_repeating_history_and_keeps_conversations_separate(gateway):
    _, _, upstream, channel, _ = gateway
    from .test_bridge import create_rule, source

    create_rule(gateway, [source(channel["id"], "group", "*")], thread_policy="window")
    upstream.unique_threads = True
    upstream.run_status = "running"
    webhook(gateway, message_id=1)
    tick(gateway)
    webhook(gateway, message_id=2)
    webhook(gateway, conversation=124, message_id=3)
    tick(gateway)
    requests = submissions(gateway)
    assert len(requests) == 3
    assert requests[1].url.path == "/api/v1/threads/thread_1/inbox"
    assert json.loads(requests[1].content)["delivery"] == "steer"
    assert all(part["type"] != "json" for part in json.loads(requests[1].content)["payload"]["content"])
    assert requests[2].url.path == "/api/v1/threads"
    upstream.run_status = "completed"
    tick(gateway)
    webhook(gateway, message_id=4)
    tick(gateway)
    assert submissions(gateway)[-1].url.path == "/api/v1/threads"
    lookups = [r.url.path for r in upstream.requests if r.url.path.endswith("/environments")]
    assert lookups == ["/api/v1/threads/thread_1/environments"]
    assert context(submissions(gateway)[-1])["conversation_id"] == "123"
    assert len(context(submissions(gateway)[-1])["history"]) == 2


def test_failed_environment_lookup_does_not_submit_a_replacement_thread(gateway):
    from .test_bridge import create_rule, source

    client, app, _, channel, _ = gateway
    create_rule(gateway, [source(channel["id"], "group", "123")], thread_policy="window")
    webhook(gateway, message_id=1)
    tick(gateway)
    tick(gateway)
    original = app.state.bridge.adapters.thread_environments

    async def unavailable(*args):
        from channel_gateway_server.adapters import AdapterError

        raise AdapterError("Environment unavailable")

    app.state.bridge.adapters.thread_environments = unavailable
    webhook(gateway, message_id=2)
    tick(gateway)
    assert len(submissions(gateway)) == 1
    assert client.get("/api/v1/events").json()[0]["attempts"] == 1
    app.state.bridge.adapters.thread_environments = original
    tick(gateway)
    assert len(submissions(gateway)) == 2
    assert json.loads(submissions(gateway)[-1].content)["environments"][0]["environment_id"] == "env_1"


def test_large_history_uses_json_part_instead_of_exceeding_host_text_limit(gateway):
    from channel_gateway_server.adapters import host_body

    configured = route(gateway)
    message = {
        "address": {"channel": "onebots"},
        "sender_id": "7",
        "source_message_id": "now",
        "text": "x" * 60000,
    }
    body = host_body(message, configured, [{"text": "y" * 60000} for _ in range(30)])
    assert len(body["payload"]["content"][0]["text"]) <= 65536
    assert len(body["payload"]["content"][-1]["value"]["channel_history"]) == 30


def test_legacy_conversation_upgrade_and_restart_keep_original_environment(gateway):
    configured = route(gateway)
    client, app, upstream, *_ = gateway
    upstream.unique_threads = True
    webhook(gateway, message_id=1)
    tick(gateway)
    tick(gateway)

    async def legacy():
        async with app.state.store.transaction() as db:
            await db.execute(
                "UPDATE routes SET value=json_remove(value,'$.history_limit') WHERE id=?", (configured["id"],)
            )

    client.portal.call(legacy)
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    webhook(gateway, message_id=2)
    tick(gateway)
    tick(gateway)
    assert submissions(gateway)[-1].url.path == "/api/v1/threads"
    assert json.loads(submissions(gateway)[-1].content)["environments"][0]["environment_id"] == "env_1"
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    webhook(gateway, message_id=3)
    tick(gateway)
    lookups = [r.url.path for r in upstream.requests if r.url.path.endswith("/environments")]
    assert lookups == ["/api/v1/threads/thread_1/environments", "/api/v1/threads/thread_2/environments"]


def test_sender_threads_do_not_automatically_attach_other_members_history(gateway):
    from .test_bridge import create_rule, source

    _, _, _, channel, _ = gateway
    create_rule(
        gateway,
        [source(channel["id"], "group", "123")],
        thread_policy="sender",
        read_channel_ids=["*"],
    )
    for index, user in enumerate([7, 8, 7], start=1):
        webhook(gateway, message_id=index, user=user, text=f"Sender {user}")
        tick(gateway)
        tick(gateway)
    assert [item["text"] for item in context(submissions(gateway)[-1])["history"]] == ["Sender 7"]
