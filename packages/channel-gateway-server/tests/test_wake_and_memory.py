"""Wake admission, credentials, Memory Space isolation and async-child continuity."""

import asyncio
import json

import httpx
import pytest
from channel_gateway_server.adapters import HttpAdapters

from .test_bridge import gateway as gateway_fixture
from .test_bridge import tick, webhook
from .test_delivery_history import context

gateway = gateway_fixture
KEY = "private-decision-error"


@pytest.fixture
def setup(gateway, monkeypatch):
    client, _, upstream, channel, host = gateway
    upstream.unique_threads = True
    original = type(upstream).__call__
    state = {"decision": '{"process":false}', "calls": 0, "spaces": [], "child": False, "lost_memory": False}

    def handle(self, request):
        path = request.url.path
        if (
            path == "/api/v1/threads"
            and request.method == "POST"
            and json.loads(request.content).get("agent_id") == "agt_decision"
        ):
            self.requests.append(request)
            state["calls"] += 1
            if state.get("fail_decision"):
                return httpx.Response(401, json={"error": KEY})
            if state.pop("lost_decision", False):
                raise httpx.ReadTimeout("Lost decision receipt", request=request)
            return httpx.Response(
                201,
                json={
                    "thread": {"id": "thread_decision"},
                    "entry": {"id": "entry_decision"},
                    "run": None if state.get("deferred") else {"id": "run_decision"},
                },
            )
        if path == "/api/v1/threads/thread_decision/inbox/entry_decision":
            return httpx.Response(200, json={"status": "assigned", "assigned_run_id": "run_decision"})
        if path == "/api/v1/runs/run_decision":
            return httpx.Response(
                200, json={"status": state.get("decision_status", "completed"), "output": state["decision"]}
            )
        if path == "/api/v1/memory-spaces":
            self.requests.append(request)
            if request.method == "POST":
                space = {"id": "mspace_" + str(len(state["spaces"]) + 1), **json.loads(request.content)}
                state["spaces"].append(space)
                if state["lost_memory"]:
                    state["lost_memory"] = False
                    raise httpx.ReadTimeout("Lost memory receipt", request=request)
                return httpx.Response(201, json=space)
            scope = request.url.params["label"].split(":", 1)[1]
            return httpx.Response(
                200,
                json={"items": [s for s in state["spaces"] if s["labels"]["cgw-scope"] == scope], "next_cursor": None},
            )
        if path.endswith("/inbox") and request.method == "GET":
            self.requests.append(request)
            return httpx.Response(200, json={"items": [], "next_cursor": None})
        if path == "/api/v1/threads" and request.method == "GET":
            self.requests.append(request)
            if state.get("bad_children"):
                return httpx.Response(200, json={"items": "unavailable"})
            return httpx.Response(
                200,
                json={
                    "items": [
                        {"id": "thread_1", "origin_thread_id": None, "current_run_id": None},
                        {
                            "id": "thread_child",
                            "origin_thread_id": "thread_1",
                            "current_run_id": "run_child" if state["child"] else None,
                        },
                        {"id": "unrelated", "origin_thread_id": None, "current_run_id": "run_other"},
                    ],
                    "next_cursor": None,
                },
            )
        response = original(self, request)
        if path.startswith("/api/v1/threads/") and "/inbox" not in path and request.method == "GET":
            return httpx.Response(200, json={**response.json(), "session_id": "session_1"})
        return response

    monkeypatch.setattr(type(upstream), "__call__", handle)

    def configure(**options):
        response = client.post(
            "/api/v1/routes",
            json={
                "name": "Coordinator",
                "host_id": host["id"],
                "agent_id": "ap_example",
                "sources": [{"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "*"}],
                "sender_policy": "any",
                "thread_policy": "conversation",
                "session_idle_minutes": 0,
                "environment_scope": "conversation",
                "conversation_memory": False,
                "read_channel_ids": [channel["id"]],
                **options,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    return state, configure


def settle(gateway):
    for _ in range(5):
        tick(gateway)


def inputs(gateway):
    return [
        r
        for r in gateway[2].requests
        if r.method == "POST"
        and (r.url.path == "/api/v1/threads" or r.url.path.endswith("/inbox"))
        and json.loads(r.content).get("agent_id") != "agt_decision"
    ]


def test_unconfigured_route_processes_every_message(gateway, setup):
    state, configure = setup
    configure()
    webhook(gateway, text="Unrelated chatter")
    settle(gateway)
    assert len(inputs(gateway)) == 1 and state["calls"] == 0


def test_filtered_messages_are_retained_as_context_and_decisions_are_not_repeated(gateway, setup):
    state, configure = setup
    # Decision Agents retain Host-managed capabilities and memory defaults.
    gateway[2].agent_config = {
        "memory_mounts": [{"name": "reference", "memory_id": "mem_reference", "access": "write"}]
    }
    configure(wake_agent_id="agt_decision")
    webhook(gateway, text="Unrelated chatter", message_id=1)
    settle(gateway)
    assert gateway[0].get("/api/v1/events").json()[0]["state"] == "filtered"
    assert not inputs(gateway) and state["calls"] == 1
    state["decision"] = {"process": True, "reason": "Direct request"}
    webhook(gateway, text="Please create a video", message_id=2)
    settle(gateway)
    assert len(inputs(gateway)) == 1
    assert [item["text"] for item in context(inputs(gateway)[0])["history"]] == ["Unrelated chatter"]
    settle(gateway)
    assert state["calls"] == 2


def test_removing_decision_agent_processes_subsequent_messages(gateway, setup):
    state, configure = setup
    route = configure(wake_agent_id="agt_decision")
    webhook(gateway)
    settle(gateway)
    assert gateway[0].patch("/api/v1/routes/" + route["id"], json={"wake_agent_id": None}).status_code == 200
    webhook(gateway, message_id=2)
    settle(gateway)
    assert state["calls"] == 1 and len(inputs(gateway)) == 1


@pytest.mark.parametrize("failure", ["invalid", "upstream"])
def test_decision_failure_keeps_normal_delivery_and_records_sanitized_diagnostic(gateway, setup, failure):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    state["decision"] = '{"process":"false"}'
    state["fail_decision"] = failure == "upstream"
    webhook(gateway)
    settle(gateway)
    event = gateway[0].get("/api/v1/events").json()[0]
    assert event["state"] == "completed" and event["wake_error"]
    assert KEY not in json.dumps(event)
    assert len(inputs(gateway)) == 1


def test_lost_host_receipt_retries_frozen_body_without_reclassifying(gateway, setup):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    state["decision"] = '{"process":true}'
    gateway[2].fail_submit = True
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    assert len(inputs(gateway)) == 1
    state["decision"] = '{"process":false}'
    settle(gateway)
    assert state["calls"] == 1 and len(inputs(gateway)) == 2
    assert inputs(gateway)[0].content == inputs(gateway)[1].content
    assert inputs(gateway)[0].headers["Idempotency-Key"] == inputs(gateway)[1].headers["Idempotency-Key"]


def test_wake_gate_does_not_block_bridge_and_rechecks_revoked_route(gateway, setup, monkeypatch):
    _, configure = setup
    route = configure(wake_agent_id="agt_decision")
    release = asyncio.Event()

    async def blocked(*args, **kwargs):
        await release.wait()
        return True

    monkeypatch.setattr("channel_gateway_server.bridge.should_process", blocked)
    webhook(gateway)
    tick(gateway)  # Starts classification and returns while it is still waiting.
    assert not inputs(gateway)
    assert gateway[0].patch("/api/v1/routes/" + route["id"], json={"enabled": False}).status_code == 200
    gateway[0].portal.call(release.set)
    settle(gateway)
    assert not inputs(gateway)
    assert gateway[0].get("/api/v1/events").json()[0]["state"] == "blocked"


def test_spaces_are_reused_across_fresh_sessions_and_isolated_between_conversations(gateway, setup):
    state, configure = setup
    configure(conversation_memory=True)
    for index, conversation in [(1, 123), (2, 123), (3, 124)]:
        webhook(gateway, message_id=index, conversation=conversation)
        settle(gateway)
    bodies = [json.loads(r.content) for r in inputs(gateway)]
    spaces = [b["session"]["memory"]["spaces"][0]["space_id"] for b in bodies]
    assert spaces[0] == spaces[1] != spaces[2]
    assert len(state["spaces"]) == 2
    assert all(r.url.path == "/api/v1/threads" for r in inputs(gateway))
    assert bodies[1]["environments"][0]["environment_id"] == "env_1"
    assert all(b["session"]["memory"]["enabled"] for b in bodies)
    assert all("connection_tools" not in b.get("options", {}).get("overrides", {}) for b in bodies)


def test_lost_memory_creation_receipt_is_discovered_without_duplicate_space(gateway, setup):
    state, configure = setup
    configure(conversation_memory=True)
    state["lost_memory"] = True
    webhook(gateway)
    settle(gateway)
    assert len(state["spaces"]) == 1 and len(inputs(gateway)) == 1


def test_running_child_keeps_idle_coordinator_then_new_session_starts_when_children_end(gateway, setup):
    state, configure = setup
    configure(conversation_memory=True)
    webhook(gateway, message_id=1)
    settle(gateway)
    state["child"] = True
    webhook(gateway, message_id=2, text="Make that video vertical")
    settle(gateway)
    followup = json.loads(inputs(gateway)[1].content)
    assert inputs(gateway)[1].url.path == "/api/v1/threads/thread_1/inbox"
    assert followup["delivery"] == "steer" and "session" not in followup
    assert context(inputs(gateway)[1])["history"] == []
    assert "coordination" not in context(inputs(gateway)[1])
    state["child"] = False
    webhook(gateway, message_id=3)
    settle(gateway)
    assert inputs(gateway)[2].url.path == "/api/v1/threads"


def test_incomplete_child_observation_never_starts_an_independent_window(gateway, setup):
    state, configure = setup
    configure()
    webhook(gateway, message_id=1)
    settle(gateway)
    state["bad_children"] = True
    webhook(gateway, message_id=2)
    tick(gateway)
    assert len(inputs(gateway)) == 1
    event = gateway[0].get("/api/v1/events").json()[0]
    assert event["state"] == "queued" and event["attempts"] == 1


def test_environment_permission_ceiling_is_preserved_when_reusing_mounts(gateway):
    client, app, _, _, host = gateway
    ceiling = {"shell": "deny"}
    transport = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "name": "workspace",
                            "environment_id": "env_shared",
                            "working_directory": "/workspace",
                            "permission_ceiling": ceiling,
                        }
                    ],
                    "next_cursor": None,
                },
            )
        )
    )
    try:
        connection = client.portal.call(app.state.store.connection, host["id"])
        mounts = client.portal.call(HttpAdapters(transport).thread_environments, connection, "thread_1")
        assert mounts[0]["permission_ceiling"] == ceiling
    finally:
        client.portal.call(transport.aclose)


def test_memory_binding_survives_store_reopen(gateway, setup):
    state, configure = setup
    configure(conversation_memory=True)
    webhook(gateway, message_id=1)
    settle(gateway)
    client, app, *_ = gateway
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    webhook(gateway, message_id=2)
    settle(gateway)
    assert len(state["spaces"]) == 1
    assert json.loads(inputs(gateway)[0].content)["session"] == json.loads(inputs(gateway)[1].content)["session"]


def test_enabling_memory_starts_a_session_with_scoped_mounts(gateway, setup):
    _, configure = setup
    configured = configure()
    webhook(gateway, message_id=1)
    settle(gateway)
    assert gateway[0].patch("/api/v1/routes/" + configured["id"], json={"conversation_memory": True}).status_code == 200
    gateway[2].run_status = "running"
    webhook(gateway, message_id=2)
    tick(gateway)
    assert inputs(gateway)[1].url.path == "/api/v1/threads"
    assert "session" in json.loads(inputs(gateway)[1].content)


def test_retired_coordination_setting_is_removed_without_rewriting_frozen_inputs(gateway, setup):
    _, configure = setup
    configure(conversation_memory=True)
    identifier = webhook(gateway).json()["event_id"]
    settle(gateway)
    client, app, *_ = gateway

    async def legacy():
        async with app.state.store.transaction() as db:
            await db.execute("UPDATE routes SET value=json_set(value,'$.coordinate_tasks',1)")
            await db.execute(
                "UPDATE events SET body=json_set(body,'$.legacy_coordination','frozen instructions') WHERE id=?",
                (identifier,),
            )

    client.portal.call(legacy)
    before = client.portal.call(app.state.store.rows, "SELECT body FROM events WHERE id=?", (identifier,))
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    stored = client.portal.call(app.state.store.rows, "SELECT value FROM routes")
    assert all("coordinate_tasks" not in json.loads(row["value"]) for row in stored)
    assert all("coordinate_tasks" not in row for row in client.get("/api/v1/routes").json())
    assert client.portal.call(app.state.store.rows, "SELECT body FROM events WHERE id=?", (identifier,)) == before


def decision_requests(gateway):
    return [
        r
        for r in gateway[2].requests
        if r.method == "POST"
        and r.url.path == "/api/v1/threads"
        and json.loads(r.content).get("agent_id") == "agt_decision"
    ]


def test_lost_decision_receipt_reuses_frozen_request_and_key(gateway, setup):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    state["lost_decision"] = True
    state["decision"] = {"process": True}
    webhook(gateway)
    settle(gateway)
    requests = decision_requests(gateway)
    assert len(requests) == 2 and len(inputs(gateway)) == 1
    assert requests[0].content == requests[1].content
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
    assert requests[0].headers["Idempotency-Key"] != inputs(gateway)[0].headers["Idempotency-Key"]
    body = json.loads(requests[0].content)
    assert body["session"]["memory"] == {"enabled": False, "spaces": []}
    assert body["memories"] == []
    assert "origin_context" not in requests[0].content.decode()


def test_deferred_decision_input_resolves_assigned_run(gateway, setup):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    state["deferred"] = True
    webhook(gateway)
    settle(gateway)
    assert state["calls"] == 1 and not inputs(gateway)
    assert gateway[0].get("/api/v1/events").json()[0]["state"] == "filtered"


def test_restart_resumes_decision_without_new_host_input(gateway, setup):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    state["decision_status"] = "running"
    webhook(gateway)
    settle(gateway)
    client, app, *_ = gateway

    async def wait_receipt():
        async with asyncio.timeout(2):
            while True:
                rows = await app.state.store.rows("SELECT value FROM wake_requests")
                if rows and "receipt" in json.loads(rows[0]["value"]):
                    break
                await asyncio.sleep(0.01)

    client.portal.call(wait_receipt)
    client.portal.call(app.state.bridge.close)
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    state["decision_status"] = "completed"
    settle(gateway)
    assert state["calls"] == 1 and not inputs(gateway)
    assert client.get("/api/v1/events").json()[0]["state"] == "filtered"


def test_timed_out_decision_falls_back_without_resubmitting(gateway, setup):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    state["decision_status"] = "running"
    webhook(gateway)
    settle(gateway)
    client, app, *_ = gateway
    client.portal.call(app.state.bridge.close)

    async def expire():
        rows = await app.state.store.rows("SELECT * FROM wake_requests")
        assert len(rows) == 1
        value = json.loads(rows[0]["value"])
        value["deadline"] = 0
        await app.state.store.save_wake_request(rows[0]["id"], value)

    client.portal.call(expire)
    settle(gateway)
    event = client.get("/api/v1/events").json()[0]
    assert event["state"] == "completed" and event["wake_error"]
    assert state["calls"] == 1 and len(inputs(gateway)) == 1


def test_provider_migration_removes_secrets_and_assignments(gateway, setup):
    _, configure = setup
    route = configure()
    client, app, *_ = gateway

    async def legacy():
        async with app.state.store.transaction() as db:
            await db.execute("CREATE TABLE model_providers (id TEXT PRIMARY KEY,value TEXT NOT NULL)")
            await db.execute("INSERT INTO model_providers VALUES (?,?)", ("old", json.dumps({"api_key": KEY})))
            await db.execute(
                "UPDATE routes SET value=json_set(value,'$.wake_provider_id','old') WHERE id=?", (route["id"],)
            )
            await db.execute("DROP TABLE wake_decisions")
            await db.execute(
                "CREATE TABLE wake_decisions (event_id TEXT PRIMARY KEY,provider_id TEXT NOT NULL,process INTEGER NOT NULL,error TEXT)"
            )
            await db.execute("INSERT INTO wake_decisions VALUES ('historical','old',0,NULL)")

    client.portal.call(legacy)
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    assert not client.portal.call(app.state.store.rows, "SELECT name FROM sqlite_master WHERE name='model_providers'")
    stored = next(row for row in client.get("/api/v1/routes").json() if row["id"] == route["id"])
    assert "wake_provider_id" not in stored and stored["wake_agent_id"] is None
    assert client.portal.call(app.state.store.rows, "SELECT process FROM wake_decisions")[0]["process"] == 0


def test_decision_observes_bot_mentions_instead_of_bypassing(gateway, setup):
    state, configure = setup
    configure(wake_agent_id="agt_decision")
    response = webhook(gateway)
    client, app, *_ = gateway

    async def mention():
        async with app.state.store.transaction() as db:
            await db.execute(
                "UPDATE events SET message=json_set(message,'$.bot_mentioned',json('true')) WHERE id=?",
                (response.json()["event_id"],),
            )

    client.portal.call(mention)
    settle(gateway)
    event = client.get("/api/v1/events").json()[0]
    assert event["state"] == "filtered" and state["calls"] == 1
    context = json.loads(decision_requests(gateway)[0].content)["payload"]["content"][1]["value"]
    assert context["current_message"]["bot_mentioned"] is True


def test_generic_host_rejects_decision_agent(gateway, setup):
    _, configure = setup
    route = configure()
    client = gateway[0]
    host = client.post("/api/v1/connections", json={"name": "Generic", "kind": "mcp"}).json()
    response = client.patch(
        "/api/v1/routes/" + route["id"],
        json={
            "host_id": host["id"],
            "agent_id": None,
            "session_idle_minutes": None,
            "environment_scope": "session",
            "wake_agent_id": "agt_decision",
        },
    )
    assert response.status_code == 422 and "a13n Host" in response.text


def test_host_change_discards_pending_decision(gateway, setup, monkeypatch):
    _, configure = setup
    configure(wake_agent_id="agt_decision")
    client, app, _, _, host = gateway
    started = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def blocked(store, adapters, connection, *args, **kwargs):
        seen.append(connection["api_url"])
        if len(seen) == 1:
            started.set()
            await release.wait()
            return False
        return True

    monkeypatch.setattr("channel_gateway_server.bridge.should_process", blocked)
    webhook(gateway)
    tick(gateway)
    client.portal.call(started.wait)

    async def change_host():
        previous = await app.state.store.connection(host["id"])
        await app.state.store.update_connection(
            host["id"], {**previous, "api_url": "https://new-host.test/api/v1"}, previous
        )

    client.portal.call(change_host)
    client.portal.call(release.set)
    settle(gateway)
    assert len(seen) == 2 and seen[0] != seen[1]
    assert len(inputs(gateway)) == 1
    assert inputs(gateway)[0].url.host == "new-host.test"


@pytest.mark.parametrize("child", [False, True])
def test_decision_distinguishes_retained_thread_from_active_work(gateway, setup, child):
    state, configure = setup
    configure(wake_agent_id="agt_decision", session_idle_minutes=None)
    state["decision"] = '{"process":true}'
    webhook(gateway)
    settle(gateway)
    state["child"] = child
    webhook(gateway, message_id=2)
    settle(gateway)
    body = json.loads(decision_requests(gateway)[1].content)
    assert body["payload"]["content"][1]["value"]["active_execution"] is child
