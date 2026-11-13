"""Session continuity, independent environments and verified cross-chat provenance."""

import json
from functools import partial

import pytest

from .test_bridge import create_rule, route_token, rpc, source, tick, webhook
from .test_bridge import gateway as gateway_fixture
from .test_delivery_history import context
from .test_thread_delivery import submissions

gateway = gateway_fixture


def configure(gateway, **options):
    return create_rule(gateway, [source(gateway[3]["id"], "group", "*")], **options)


def finish(gateway):
    tick(gateway)
    tick(gateway)


def body(request):
    return json.loads(request.content)


def origin_handle(request):
    return context(request)["origin_context"]


def expire(gateway):
    async def update():
        async with gateway[1].state.store.transaction() as db:
            await db.execute("UPDATE session_activity SET last_input=0,idle_since=0")

    gateway[0].portal.call(update)


def test_new_route_defaults_and_four_scope_schema(gateway):
    client, _, _, channel, host = gateway
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Default",
            "host_id": host["id"],
            "agent_id": "agent",
            "sources": [source(channel["id"], "group", "*")],
            "sender_policy": "any",
        },
    )
    assert response.status_code == 201, response.text
    route = response.json()
    assert route["thread_policy"] == "conversation"
    assert route["session_idle_minutes"] == 60
    assert route["environment_scope"] == "conversation"
    assert route["conversation_memory"] is True
    assert client.patch(f"/api/v1/routes/{route['id']}", json={"thread_policy": "window"}).status_code == 422


@pytest.mark.parametrize("scope", ["global", "conversation", "session"])
def test_environment_scope_is_independent_of_fresh_message_sessions(gateway, scope):
    configure(gateway, thread_policy="message", environment_scope=scope)
    gateway[2].unique_threads = True
    for index, conversation in enumerate([123, 123, 124, 123], 1):
        webhook(gateway, message_id=index, conversation=conversation)
        finish(gateway)
    requests = submissions(gateway)
    assert len(requests) == 4 and all(r.url.path == "/api/v1/threads" for r in requests)
    reuse = ["environments" in body(r) for r in requests]
    assert (
        reuse
        == {"global": [False, True, True, True], "conversation": [False, True, False, True], "session": [False] * 4}[
            scope
        ]
    )


def test_global_environment_is_shared_across_routes_and_survives_restart(gateway):
    channel = gateway[3]["id"]
    for conversation in ["123", "124"]:
        create_rule(
            gateway, [source(channel, "group", conversation)], thread_policy="message", environment_scope="global"
        )
    webhook(gateway, message_id=1)
    finish(gateway)
    gateway[0].portal.call(gateway[1].state.store.close)
    gateway[0].portal.call(gateway[1].state.store.open)
    webhook(gateway, message_id=2, conversation=124)
    finish(gateway)
    assert body(submissions(gateway)[1])["environments"][0]["environment_id"] == "env_1"


@pytest.mark.parametrize("scope", ["conversation", "session"])
def test_keepalive_continues_then_rolls_over_with_selected_environment(gateway, scope):
    configured = configure(gateway, session_idle_minutes=60, environment_scope=scope)
    gateway[2].unique_threads = True
    webhook(gateway, message_id=1, text="First")
    finish(gateway)
    webhook(gateway, message_id=2, text="Follow-up")
    finish(gateway)
    assert submissions(gateway)[1].url.path == "/api/v1/threads/thread_1/inbox"
    assert context(submissions(gateway)[1])["history"] == []
    expire(gateway)
    webhook(gateway, message_id=3)
    finish(gateway)
    request = submissions(gateway)[2]
    assert request.url.path == "/api/v1/threads"
    assert ("environments" in body(request)) == (scope == "conversation")
    assert [m["text"] for m in context(request)["history"]] == ["First", "Follow-up"]
    finish(gateway)
    # Reset must not resurrect old coordinator bindings or erase conversation environments.
    response = gateway[0].post(f"/api/v1/routes/{configured['id']}/reset-thread")
    assert response.status_code == 200, response.text
    webhook(gateway, message_id=4)
    finish(gateway)
    request = submissions(gateway)[3]
    assert request.url.path == "/api/v1/threads"
    assert ("environments" in body(request)) == (scope == "conversation")


def test_sender_contexts_can_share_conversation_environment(gateway):
    configure(gateway, thread_policy="sender", environment_scope="conversation")
    gateway[2].unique_threads = True
    for index, user in enumerate([7, 8, 7], 1):
        webhook(gateway, message_id=index, user=user)
        finish(gateway)
    requests = submissions(gateway)
    assert [r.url.path for r in requests] == ["/api/v1/threads", "/api/v1/threads", "/api/v1/threads/thread_1/inbox"]
    assert "environments" in body(requests[1]) and "environments" not in body(requests[2])


def test_incremental_context_recovers_chatter_without_repeating_inputs(gateway):
    configure(gateway, read_channel_ids=["*"], session_idle_minutes=60)
    webhook(gateway, message_id=1, text="Initial")
    finish(gateway)
    # A collected event that was not delivered to this Session remains history.
    webhook(gateway, message_id=2, text="Intervening")
    event = gateway[0].get("/api/v1/events").json()[0]
    gateway[0].portal.call(partial(gateway[1].state.store.update_event, event["id"], state="filtered"))
    webhook(gateway, message_id=3, text="Current")
    finish(gateway)
    assert [m["text"] for m in context(submissions(gateway)[1])["history"]] == ["Intervening"]
    webhook(gateway, message_id=4)
    finish(gateway)
    assert context(submissions(gateway)[2])["history"] == []


def test_sixty_minute_thread_receives_bootstrap_once_then_only_message_delta(gateway):
    configure(gateway, read_channel_ids=["*"], session_idle_minutes=60)
    gateway[2].unique_threads = True
    webhook(gateway, message_id=1, text="First")
    finish(gateway)
    first = context(submissions(gateway)[0])
    assert first["instructions"] and first["channel"] == "onebots"
    for index in (2, 3):
        webhook(gateway, message_id=index, text=f"Follow-up {index}")
        finish(gateway)
        request = submissions(gateway)[index - 1]
        assert request.url.path == "/api/v1/threads/thread_1/inbox"
        current = context(request)
        assert not {"instructions", "channel", "connection_id", "conversation_kind", "channel_history"} & current.keys()
        assert current["route_id"] == first["route_id"] and current["conversation_id"] == first["conversation_id"]
        assert current["source_message_id"] == str(index)
        assert current["origin_context"] != first["origin_context"]
    expire(gateway)
    webhook(gateway, message_id=4, text="New window")
    finish(gateway)
    assert submissions(gateway)[-1].url.path == "/api/v1/threads"
    assert context(submissions(gateway)[-1])["instructions"]


def test_fixed_thread_keeps_source_account_identity_on_continuation(gateway):
    configure(gateway, thread_policy="fixed", conversation_memory=False, environment_scope="session")
    for index, conversation in enumerate([123, 124, 123], start=1):
        webhook(gateway, message_id=index, conversation=conversation)
        finish(gateway)
    current = context(submissions(gateway)[-1])
    assert "instructions" not in current
    assert current["conversation_id"] == "123" and current["channel"] == "onebots"
    assert current["connection_id"] == gateway[3]["id"]


def test_memory_uses_scoped_write_and_explicit_read_only_global_spaces(gateway):
    configure(gateway, conversation_memory=True, global_memory_space_ids=["mspace_global"], session_idle_minutes=0)
    webhook(gateway, message_id=1, text="Act as a catgirl")
    finish(gateway)
    webhook(gateway, message_id=2)
    finish(gateway)
    bodies = [body(r) for r in submissions(gateway)]
    assert bodies[0]["session"] == bodies[1]["session"]
    mounts = bodies[0]["session"]["memory"]["spaces"]
    assert mounts[0]["access"] == "write" and mounts[0]["space_id"] != "mspace_global"
    assert mounts[1] == {"name": "global-1", "space_id": "mspace_global", "access": "read"}
    assert bodies[0]["memories"] == []


def send(gateway, route_id, handle):
    return rpc(
        gateway[0],
        route_token(gateway[0], route_id),
        "tools/call",
        {
            "name": "send_message",
            "arguments": {
                "route_id": route_id,
                "origin_context": handle,
                "content": [{"type": "text", "text": "Please review the document"}],
            },
        },
    ).json()["result"]


def read(gateway, route_id):
    response = rpc(
        gateway[0],
        route_token(gateway[0], route_id),
        "tools/call",
        {"name": "read", "arguments": {"route_id": route_id, "resource": "messages"}},
    ).json()["result"]
    assert not response.get("isError"), response
    return json.loads(response["content"][0]["text"])["messages"]


def test_cross_chat_send_recovers_verified_origin_and_respects_source_read_grants(gateway):
    configured = configure(gateway, read_channel_ids=["*"], session_idle_minutes=60)
    webhook(gateway, message_id=1, text="Ask the other group to review")
    tick(gateway)  # Source context is active, not yet completed.
    source_request = submissions(gateway)[0]
    webhook(gateway, message_id=2, conversation=124, text="Target")
    children = gateway[0].get("/api/v1/routes").json()
    destination = next(r for r in children if r["conversation_id"] == "124")
    result = send(gateway, destination["id"], origin_handle(source_request))
    assert not result.get("isError"), result
    finish(gateway)
    outgoing = [m for m in read(gateway, destination["id"]) if m.get("direction") == "outgoing"]
    assert outgoing[0]["provenance"]["status"] == "verified"
    assert outgoing[0]["provenance"]["reason"] == "Ask the other group to review"
    webhook(gateway, message_id=3, conversation=124, text="?")
    finish(gateway)
    assert any(
        m.get("provenance", {}).get("status") == "verified" for m in context(submissions(gateway)[-1])["history"]
    )
    # Legacy source-only reads retain destination history but cannot reveal another source's request.
    assert gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"read_channel_ids": None}).status_code == 200
    outgoing = [m for m in read(gateway, destination["id"]) if m.get("direction") == "outgoing"]
    assert outgoing[0]["provenance"] == {"status": "restricted"}


def test_running_agent_can_reply_with_its_original_context_after_reset(gateway):
    configured = configure(gateway, busy_policy="queue")
    client, _, upstream, *_ = gateway
    upstream.unique_threads = True
    upstream.run_status = "running"
    webhook(gateway, message_id=1)
    tick(gateway)
    first = client.get("/api/v1/events").json()[0]
    handle = origin_handle(submissions(gateway)[0])

    assert client.post(f"/api/v1/routes/{configured['id']}/reset-thread").status_code == 200
    webhook(gateway, message_id=2)
    tick(gateway)
    latest = client.get("/api/v1/events").json()[0]
    assert latest["thread_id"] != first["thread_id"]
    assert latest["state"] == "submitted"

    result = send(gateway, first["route_id"], handle)
    assert not result.get("isError"), result
    tick(gateway)
    outgoing = [m for m in read(gateway, first["route_id"]) if m.get("direction") == "outgoing"]
    assert outgoing[0]["provenance"]["status"] == "verified"


def test_forged_or_completed_origin_context_is_rejected(gateway):
    configure(gateway)
    webhook(gateway)
    tick(gateway)
    request = submissions(gateway)[0]
    route_id = context(request)["route_id"]
    assert send(gateway, route_id, "forged").get("isError")
    finish(gateway)
    assert send(gateway, route_id, origin_handle(request)).get("isError")
    assert gateway[0].get("/api/v1/replies").json() == []


def test_lost_environment_creation_receipt_does_not_create_second_shared_environment(gateway):
    configure(gateway, thread_policy="message", environment_scope="global")
    gateway[2].unique_threads = True
    gateway[2].fail_submit = True
    webhook(gateway, message_id=1)
    webhook(gateway, message_id=2, conversation=124)
    tick(gateway)
    assert len(submissions(gateway)) == 1
    frozen = submissions(gateway)[0]
    finish(gateway)
    requests = submissions(gateway)
    assert requests[1].content == frozen.content
    assert requests[1].headers["Idempotency-Key"] == frozen.headers["Idempotency-Key"]
    assert "environments" in body(requests[2])


def test_late_confirmed_send_is_not_lost_behind_the_context_cursor(gateway):
    configure(gateway)
    webhook(gateway)
    finish(gateway)
    request = submissions(gateway)[0]
    route_id = context(request)["route_id"]
    store = gateway[1].state.store
    route = gateway[0].portal.call(store.route, route_id)
    reply = gateway[0].portal.call(store.queue_reply, route, "Delayed send")

    async def old_creation():
        async with store.transaction() as db:
            await db.execute("UPDATE replies SET created_at='2020-01-01T00:00:00.000Z' WHERE id=?", (reply["id"],))

    gateway[0].portal.call(old_creation)
    snapshot = {"created_at": "2050-01-01T00:00:00.000Z", "id": "evt_future", "message": json.dumps({"sender_id": "7"})}
    assert gateway[0].portal.call(store.delivery_history, snapshot, route, "thread_1") == []
    gateway[0].portal.call(partial(store.finish_reply, reply["id"], "sent", message_id="confirmed"))
    history = gateway[0].portal.call(store.delivery_history, snapshot, route, "thread_1")
    assert [m["text"] for m in history] == ["Delayed send"]


def test_legacy_window_migration_preserves_environment_and_frozen_body(gateway):
    from channel_gateway_server.thread_policy import thread_binding_key

    configured = configure(gateway)
    gateway[2].unique_threads = True
    webhook(gateway, message_id=1)
    finish(gateway)
    store = gateway[1].state.store

    async def make_legacy():
        async with store.transaction() as db:
            async with db.execute("SELECT * FROM routes") as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                value = json.loads(row["value"])
                old_key = thread_binding_key(value, {})
                value.pop("session_idle_minutes", None)
                value.pop("environment_scope", None)
                value["thread_policy"] = "window"
                key = thread_binding_key(value, {})
                await db.execute("UPDATE routes SET value=? WHERE id=?", (json.dumps(value), row["id"]))
                await db.execute("UPDATE event_thread_bindings SET binding_key=? WHERE binding_key=?", (key, old_key))
                await db.execute("UPDATE thread_bindings SET binding_key=? WHERE binding_key=?", (key, old_key))

    gateway[0].portal.call(make_legacy)
    gateway[0].portal.call(store.close)
    gateway[0].portal.call(store.open)
    parent = gateway[0].portal.call(store.route, configured["id"])
    assert parent["thread_policy"] == "conversation" and parent["session_idle_minutes"] == 0
    assert parent["environment_scope"] == "conversation"
    webhook(gateway, message_id=2)
    finish(gateway)
    assert submissions(gateway)[-1].url.path == "/api/v1/threads"
    assert body(submissions(gateway)[-1])["environments"][0]["environment_id"] == "env_1"


def test_old_delivery_receipt_cannot_restore_a_superseded_session(gateway):
    configure(gateway, session_idle_minutes=0)
    gateway[2].unique_threads = True
    first = webhook(gateway, message_id=1).json()["event_id"]
    finish(gateway)
    webhook(gateway, message_id=2)
    finish(gateway)
    store = gateway[1].state.store
    route_id = context(submissions(gateway)[0])["route_id"]
    gateway[0].portal.call(
        store.submitted,
        first,
        route_id,
        {"thread": {"id": "thread_1"}, "entry": {"id": "entry_1"}, "run": {"id": "run_1"}},
    )
    route = gateway[0].portal.call(store.route, route_id)
    assert gateway[0].portal.call(store.environment_thread, route, {"sender_id": "7"}) == "thread_2"


def test_full_incremental_window_retains_a_recovery_reference(gateway):
    configure(gateway, read_channel_ids=["*"], session_idle_minutes=60)
    webhook(gateway, message_id=1)
    finish(gateway)
    for index in range(2, 108):
        webhook(gateway, message_id=index, text=f"Collected {index}")
    store = gateway[1].state.store

    async def filter_chatter():
        async with store.transaction() as db:
            await db.execute("UPDATE events SET state='filtered' WHERE state='queued'")

    gateway[0].portal.call(filter_chatter)
    webhook(gateway, message_id=108)
    finish(gateway)
    request = submissions(gateway)[1]
    assert len(context(request)["history"]) == 100
    assert len(body(request)["payload"]["content"]) == 1
    coverage = context(request)["history_coverage"]
    assert coverage["complete"] is False and coverage["read"]["resource"] == "messages"
    frozen = gateway[0].portal.call(store.rows, "SELECT body FROM events WHERE target LIKE '%/inbox'", ())
    assert "history_coverage" in frozen[0]["body"]


def test_source_revocation_blocks_an_attributed_send_to_an_enabled_destination(gateway):
    channel = gateway[3]["id"]
    first = create_rule(gateway, [source(channel, "group", "123"), source(channel, "group", "124")])
    webhook(gateway, message_id=1)
    tick(gateway)
    request = submissions(gateway)[0]
    webhook(gateway, message_id=2, conversation=124, user=8)
    destination = next(r for r in gateway[0].get("/api/v1/routes").json() if r["conversation_id"] == "124")
    assert not send(gateway, destination["id"], origin_handle(request)).get("isError")
    assert (
        gateway[0]
        .patch(f"/api/v1/routes/{first['id']}", json={"sender_policy": "allowlist", "allowed_user_ids": ["8"]})
        .status_code
        == 200
    )
    finish(gateway)
    replies = gateway[0].get("/api/v1/replies").json()
    assert len(replies) == 1 and replies[0]["state"] == "blocked"
    assert not any(r.url.path.endswith("send_msg") for r in gateway[2].requests)


@pytest.mark.parametrize(
    "configuration",
    [
        {"memory_mounts": [{"name": "global", "memory_id": "mem_global", "access": "write"}]},
        {"subagents": {"named": {"worker": {"agent_id": "worker"}}}},
    ],
)
def test_managed_memory_fails_closed_for_unsafe_agent_defaults(gateway, configuration):
    configure(gateway, conversation_memory=True)
    gateway[2].agent_config = configuration
    webhook(gateway)
    tick(gateway)
    assert submissions(gateway) == []
    event = gateway[0].get("/api/v1/events").json()[0]
    assert event["state"] == "queued" and event["attempts"] == 1
    assert not gateway[2].memory_spaces


def test_managed_memory_freezes_the_audited_root_revision(gateway):
    configure(gateway, conversation_memory=True)
    gateway[2].agent_config = {
        "memory_mounts": [{"name": "reference", "memory_id": "mem_global", "access": "read"}],
        "subagents": {
            "named": {"worker": {"agent_id": "agt_worker", "revision_id": "rev_1"}},
            "max_concurrent": 4,
        },
    }
    webhook(gateway)
    finish(gateway)
    assert body(submissions(gateway)[0])["agent_revision_id"] == "rev_1"


def test_completed_root_context_remains_valid_while_its_child_is_active(gateway, monkeypatch):
    import httpx

    configure(gateway, session_idle_minutes=60)
    state = {"child_active": True}
    original = type(gateway[2]).__call__

    def with_child(self, request):
        if request.method == "GET" and request.url.path == "/api/v1/threads" and "session_id" in request.url.params:
            self.requests.append(request)
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "id": "child",
                            "origin_thread_id": "thread_1",
                            "current_run_id": "child_run" if state["child_active"] else None,
                        }
                    ],
                    "next_cursor": None,
                },
            )
        return original(self, request)

    monkeypatch.setattr(type(gateway[2]), "__call__", with_child)
    webhook(gateway)
    finish(gateway)
    assert gateway[0].get("/api/v1/events").json()[0]["state"] == "completed"
    request = submissions(gateway)[0]
    route_id = context(request)["route_id"]
    handle = origin_handle(request)
    assert not send(gateway, route_id, handle).get("isError")
    state["child_active"] = False
    assert send(gateway, route_id, handle).get("isError")
    assert handle not in gateway[0].get("/api/v1/events").text


def test_shared_environment_waits_for_run_admission_not_just_a_thread_receipt(gateway):
    configure(gateway, thread_policy="message", environment_scope="global")
    gateway[2].queued_receipt = True
    webhook(gateway, message_id=1)
    webhook(gateway, message_id=2, conversation=124)
    tick(gateway)
    assert len(submissions(gateway)) == 1
    finish(gateway)
    assert len(submissions(gateway)) == 2
    assert body(submissions(gateway)[1])["environments"][0]["environment_id"] == "env_1"
