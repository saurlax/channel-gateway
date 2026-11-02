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
KEY = "test-model-key-never-return"


@pytest.fixture
def setup(gateway, monkeypatch):
    client, _, upstream, channel, host = gateway
    upstream.unique_threads = True
    original = type(upstream).__call__
    state = {"decision": '{"process":false}', "calls": 0, "spaces": [], "child": False, "lost_memory": False}

    def handle(self, request):
        path = request.url.path
        if request.url.host == "model.test":
            self.requests.append(request)
            state["calls"] += 1
            if state.get("fail_model"):
                return httpx.Response(401, json={"error": KEY})
            return httpx.Response(200, json={"choices": [{"message": {"content": state["decision"]}}]})
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
    provider = client.post(
        "/api/v1/model-providers",
        json={
            "name": "Gateway models",
            "base_url": "https://model.test/v1/",
            "api_key": KEY,
            "text_model": "text",
            "decision_model": "decision",
        },
    )
    assert provider.status_code == 201, provider.text
    state["provider"] = provider.json()

    def configure(**options):
        response = client.post(
            "/api/v1/routes",
            json={
                "name": "Coordinator",
                "host_id": host["id"],
                "agent_id": "ap_example",
                "sources": [{"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "*"}],
                "sender_policy": "any",
                "thread_policy": "window",
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
        if r.method == "POST" and (r.url.path == "/api/v1/threads" or r.url.path.endswith("/inbox"))
    ]


def test_provider_credentials_remain_private_and_replacement_preserves_omitted_key(gateway, setup):
    state, _ = setup
    client = gateway[0]
    provider = state["provider"]
    assert provider["base_url"] == "https://model.test/v1"
    assert provider["has_api_key"] and KEY not in json.dumps(provider)
    assert KEY not in client.get("/api/v1/model-providers").text
    body = {k: v for k, v in provider.items() if k not in {"id", "has_api_key"}}
    body["text_model"] = "replacement"
    assert client.patch("/api/v1/model-providers/" + provider["id"], json=body).status_code == 200
    assert client.post("/api/v1/model-providers/" + provider["id"] + "/test").status_code == 200
    model_requests = [r for r in gateway[2].requests if r.url.host == "model.test"]
    assert all(r.headers["Authorization"] == "Bearer " + KEY for r in model_requests)
    assert [json.loads(r.content)["model"] for r in model_requests] == ["replacement", "decision"]
    state["fail_model"] = True
    response = client.post("/api/v1/model-providers/" + provider["id"] + "/test")
    assert response.status_code == 422 and KEY not in response.text
    client.cookies.clear()
    assert client.get("/api/v1/model-providers").status_code == 401


def test_filtered_messages_are_retained_as_context_and_decisions_are_not_repeated(gateway, setup):
    state, configure = setup
    configure(wake_provider_id=state["provider"]["id"])
    webhook(gateway, text="Unrelated chatter", message_id=1)
    settle(gateway)
    assert gateway[0].get("/api/v1/events").json()[0]["state"] == "filtered"
    assert not inputs(gateway) and state["calls"] == 1
    state["decision"] = '{"process":true}'
    webhook(gateway, text="Please create a video", message_id=2)
    settle(gateway)
    assert len(inputs(gateway)) == 1
    assert [item["text"] for item in context(inputs(gateway)[0])["history"]] == ["Unrelated chatter"]
    settle(gateway)
    assert state["calls"] == 2
    assert gateway[0].delete("/api/v1/model-providers/" + state["provider"]["id"]).status_code == 409


def test_removing_parent_assignment_allows_deleting_provider_despite_old_child_snapshot(gateway, setup):
    state, configure = setup
    route = configure(wake_provider_id=state["provider"]["id"])
    webhook(gateway)
    settle(gateway)
    assert gateway[0].patch("/api/v1/routes/" + route["id"], json={"wake_provider_id": None}).status_code == 200
    assert gateway[0].delete("/api/v1/model-providers/" + state["provider"]["id"]).status_code == 200


@pytest.mark.parametrize("failure", ["invalid", "upstream"])
def test_model_failure_keeps_normal_delivery_and_records_sanitized_diagnostic(gateway, setup, failure):
    state, configure = setup
    configure(wake_provider_id=state["provider"]["id"])
    state["decision"] = '{"process":"false"}'
    state["fail_model"] = failure == "upstream"
    webhook(gateway)
    settle(gateway)
    event = gateway[0].get("/api/v1/events").json()[0]
    assert event["state"] == "completed" and event["wake_error"]
    assert KEY not in json.dumps(event)
    assert len(inputs(gateway)) == 1


def test_lost_host_receipt_retries_frozen_body_without_reclassifying(gateway, setup):
    state, configure = setup
    configure(wake_provider_id=state["provider"]["id"])
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
    state, configure = setup
    route = configure(wake_provider_id=state["provider"]["id"])
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
    configure(coordinate_tasks=True, conversation_memory=True)
    webhook(gateway, message_id=1)
    settle(gateway)
    state["child"] = True
    webhook(gateway, message_id=2, text="Make that video vertical")
    settle(gateway)
    followup = json.loads(inputs(gateway)[1].content)
    assert inputs(gateway)[1].url.path == "/api/v1/threads/thread_1/inbox"
    assert followup["delivery"] == "steer" and "session" not in followup
    assert context(inputs(gateway)[1])["history"] == []
    assert "coordination" in context(inputs(gateway)[1])
    state["child"] = False
    webhook(gateway, message_id=3)
    settle(gateway)
    assert inputs(gateway)[2].url.path == "/api/v1/threads"


def test_incomplete_child_observation_never_starts_an_independent_window(gateway, setup):
    state, configure = setup
    configure(coordinate_tasks=True)
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


def test_enabling_memory_on_legacy_window_does_not_steer_into_global_session(gateway, setup):
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
