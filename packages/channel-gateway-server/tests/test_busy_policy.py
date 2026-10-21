"""Active Host runs, FIFO submissions, frozen retry bodies and shared-run output."""

import json

import httpx
import pytest

from .test_bridge import gateway as gateway_fixture
from .test_bridge import route, tick, webhook

gateway = gateway_fixture


class BusyHost:
    """A native inbox admits input now and assigns steers only at a safe boundary."""

    def __init__(self, fallback):
        self.fallback = fallback
        self.requests = []
        self.receipts = {}
        self.entries = {}
        self.runs = {}
        self.active = {}
        self.fail_after_accept = False
        self.fail_entry_read = False

    def start(self, thread, entry):
        run_id = "busy_run_" + str(len(self.runs) + 1)
        self.runs[run_id] = {"id": run_id, "status": "running", "output": "Final output", "thread_id": thread}
        entry.update(status="consumed", assigned_run_id=run_id)
        self.active[thread] = run_id
        return self.runs[run_id]

    def incorporate(self, thread=None):
        for (entry_thread, _), entry in self.entries.items():
            if (
                entry["status"] == "pending"
                and entry["delivery"] == "steer"
                and (thread is None or thread == entry_thread)
            ):
                entry.update(status="consumed", assigned_run_id=self.active[entry_thread])

    def complete(self, run_id, output="Final output"):
        self.runs[run_id].update(status="completed", output=output)
        thread = self.runs[run_id]["thread_id"]
        self.active.pop(thread, None)
        for (entry_thread, _), entry in self.entries.items():
            if entry_thread == thread and entry["status"] == "pending":
                self.start(thread, entry)
                break

    def __call__(self, request):
        path = request.url.path
        if request.url.host != "host.test" or (path.endswith(("/agents", "/threads")) and request.method == "GET"):
            return self.fallback(request)
        self.requests.append(request)
        if request.method == "POST":
            key = request.headers["Idempotency-Key"]
            if key in self.receipts:
                return httpx.Response(200, json=self.receipts[key])
            body = json.loads(request.content)
            thread = (
                path.split("/threads/")[1].split("/")[0]
                if "/threads/" in path
                else "busy_thread_" + str(len(self.receipts) + 1)
            )
            entry_id = "busy_entry_" + str(len(self.entries) + 1)
            entry = {"id": entry_id, "status": "pending", "assigned_run_id": None, "delivery": body["delivery"]}
            self.entries[(thread, entry_id)] = entry
            run = None if thread in self.active else self.start(thread, entry)
            receipt = {"thread": {"id": thread}, "entry": dict(entry), "run": dict(run) if run else None}
            self.receipts[key] = receipt
            if self.fail_after_accept:
                self.fail_after_accept = False
                raise httpx.ReadTimeout("Lost acceptance receipt")
            return httpx.Response(201, json=receipt)
        if "/inbox/" in path:
            if self.fail_entry_read:
                raise httpx.ReadTimeout("Entry assignment unavailable")
            thread = path.split("/threads/")[1].split("/")[0]
            return httpx.Response(200, json=self.entries[(thread, path.rsplit("/", 1)[-1])])
        if "/runs/" in path:
            return httpx.Response(200, json=self.runs[path.rsplit("/", 1)[-1]])
        return self.fallback(request)

    @property
    def posts(self):
        return [request for request in self.requests if request.method == "POST"]


@pytest.fixture
def busy(gateway):
    host = BusyHost(gateway[2])
    gateway[1].state.bridge.adapters.client._transport = httpx.MockTransport(host)
    configured = route(gateway)
    return gateway, host, configured


def emit(busy, index, **kwargs):
    gateway, _, _ = busy
    result = webhook(gateway, message_id=index, **kwargs)
    assert result.status_code == 200
    return result.json()["event_id"]


def test_default_steer_submits_while_running_and_emits_one_final_reply(busy):
    gateway, host, configured = busy
    assert configured["busy_policy"] == "steer"
    first = emit(busy, 1)
    tick(gateway)
    second = emit(busy, 2)
    tick(gateway)
    assert len(host.posts) == 2
    assert host.posts[1].url.path == "/api/v1/threads/busy_thread_1/inbox"
    assert json.loads(host.posts[1].content)["delivery"] == "steer"
    assert gateway[0].get("/api/v1/replies").json() == []
    host.incorporate()
    host.complete("busy_run_1")
    tick(gateway)
    replies = gateway[0].get("/api/v1/replies").json()
    assert len(replies) == 1 and replies[0]["event_id"] == second
    assert replies[0]["state"] == "sent"
    assert {row["state"] for row in gateway[0].get("/api/v1/events").json() if row["id"] in {first, second}} == {
        "completed"
    }
    gateway[0].portal.call(gateway[1].state.store.close)
    gateway[0].portal.call(gateway[1].state.store.open)
    tick(gateway)
    assert len(gateway[0].get("/api/v1/replies").json()) == 1


def test_queue_waits_for_completion_then_delivers_in_order(busy):
    gateway, host, configured = busy
    updated = gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": "queue"})
    assert updated.status_code == 200 and updated.json()["busy_policy"] == "queue"
    first = emit(busy, 1)
    tick(gateway)
    second, third = emit(busy, 2), emit(busy, 3)
    tick(gateway)
    assert len(host.posts) == 1
    assert json.loads(host.posts[0].content)["delivery"] == "next_run"
    host.complete("busy_run_1")
    tick(gateway)
    tick(gateway)
    assert len(host.posts) == 2
    assert second in host.posts[-1].headers["Idempotency-Key"]
    assert json.loads(host.posts[-1].content)["delivery"] == "next_run"
    tick(gateway)
    assert len(host.posts) == 2
    host.complete("busy_run_2")
    tick(gateway)
    tick(gateway)
    assert len(host.posts) == 3 and host.posts[-1].headers["Idempotency-Key"] == third
    host.complete("busy_run_3")
    tick(gateway)
    assert {reply["event_id"] for reply in gateway[0].get("/api/v1/replies").json()} == {first, second, third}


def test_unincorporated_steer_is_not_misattributed_to_earlier_run(busy):
    gateway, host, _ = busy
    first = emit(busy, 1)
    tick(gateway)
    second = emit(busy, 2)
    tick(gateway)
    host.complete("busy_run_1", "First answer")  # Pending entry starts a distinct run.
    tick(gateway)
    assert gateway[0].get("/api/v1/replies").json()[0]["event_id"] == first
    host.complete("busy_run_2", "Second answer")
    tick(gateway)
    assert [(row["event_id"], row["text"]) for row in gateway[0].get("/api/v1/replies").json()] == [
        (second, "Second answer"),
        (first, "First answer"),
    ]


def test_lost_receipt_blocks_later_submissions_and_retries_exact_frozen_policy(busy):
    gateway, host, configured = busy
    host.fail_after_accept = True
    emit(busy, 1)
    emit(busy, 2)
    tick(gateway)
    assert len(host.entries) == 1
    first = host.posts[0]
    assert gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": "queue"}).status_code == 200
    tick(gateway)
    assert first.url == host.posts[1].url and first.content == host.posts[1].content
    assert first.headers["Idempotency-Key"] == host.posts[1].headers["Idempotency-Key"]
    assert len(host.entries) == 1
    tick(gateway)
    assert len(host.posts) == 2  # New, unfrozen input now queues.


def test_busy_policy_changes_apply_to_unfrozen_input_without_resetting_thread(busy):
    gateway, host, configured = busy
    emit(busy, 1)
    tick(gateway)
    gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": "queue"})
    emit(busy, 2)
    tick(gateway)
    assert len(host.posts) == 1
    assert gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": "steer"}).status_code == 200
    tick(gateway)
    assert len(host.posts) == 2
    assert host.posts[-1].url.path.endswith("/busy_thread_1/inbox")
    assert json.loads(host.posts[-1].content)["delivery"] == "steer"


def test_assignment_read_failure_defers_final_output_without_duplicates(busy):
    gateway, host, _ = busy
    emit(busy, 1)
    tick(gateway)
    emit(busy, 2)
    tick(gateway)
    host.incorporate()
    host.complete("busy_run_1")
    host.fail_entry_read = True
    tick(gateway)
    assert gateway[0].get("/api/v1/replies").json() == []
    host.fail_entry_read = False
    tick(gateway)
    assert len(gateway[0].get("/api/v1/replies").json()) == 1


@pytest.mark.parametrize("invalid", ["interrupt", "next_run", "unknown", None])
def test_invalid_busy_policy_is_rejected(busy, invalid):
    gateway, _, configured = busy
    response = gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": invalid})
    assert response.status_code == 422


def configure_sources(busy, *, policy="steer", thread="fixed"):
    gateway, _, configured = busy
    response = gateway[0].patch(
        f"/api/v1/routes/{configured['id']}",
        json={
            "sources": [{"channel_id": gateway[3]["id"], "conversation_kind": "group", "conversation_id": "*"}],
            "sender_policy": "any",
            "allowed_user_ids": [],
            "thread_policy": thread,
            "busy_policy": policy,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize("policy", ["steer", "queue"])
def test_shared_thread_obeys_policy_across_source_conversations(busy, policy):
    gateway, host, _ = busy
    configured = configure_sources(busy, policy=policy)
    first = emit(busy, 1, conversation=123)
    tick(gateway)
    second = emit(busy, 2, conversation=124)
    tick(gateway)
    assert len(host.posts) == (2 if policy == "steer" else 1)
    children = [
        row for row in gateway[0].get("/api/v1/routes").json() if row.get("parent_route_id") == configured["id"]
    ]
    assert children and all(row["busy_policy"] == policy for row in children)
    if policy == "steer":
        host.incorporate()
    host.complete("busy_run_1")
    tick(gateway)
    if policy == "queue":
        tick(gateway)
        assert host.posts[-1].url.path.endswith("/busy_thread_1/inbox")
        host.complete("busy_run_2")
        tick(gateway)
    replies = gateway[0].get("/api/v1/replies").json()
    assert len(replies) == (1 if policy == "steer" else 2)
    assert replies[0]["event_id"] == second
    assert replies[0]["address"]["conversation_id"] == "group:124"
    if policy == "queue":
        assert replies[1]["event_id"] == first


@pytest.mark.parametrize("latest_state", ["blocked", "failed"])
def test_revoked_latest_steer_never_redirects_combined_output_to_previous_source(busy, latest_state):
    gateway, host, configured = busy
    configure_sources(busy)
    emit(busy, 1, conversation=123)
    tick(gateway)
    second = emit(busy, 2, conversation=124)
    tick(gateway)
    host.incorporate()
    if latest_state == "blocked":
        # Revoke the second source before CG knows its native run assignment.
        response = gateway[0].patch(
            f"/api/v1/routes/{configured['id']}",
            json={
                "sources": [{"channel_id": gateway[3]["id"], "conversation_kind": "group", "conversation_id": "123"}],
            },
        )
        assert response.status_code == 200, response.text
    else:
        gateway[0].portal.call(
            lambda: gateway[1].state.store.update_event(second, state="failed", error="Host request failed")
        )
    host.complete("busy_run_1", "Combined answer containing both sources")
    tick(gateway)
    assert gateway[0].get("/api/v1/replies").json() == []


@pytest.mark.parametrize("thread_policy", ["conversation", "sender", "message"])
def test_independent_threads_do_not_block_each_other_in_queue_mode(busy, thread_policy):
    gateway, host, _ = busy
    configure_sources(busy, policy="queue", thread=thread_policy)
    emit(busy, 1)
    tick(gateway)
    emit(
        busy,
        2,
        **(
            {"conversation": 124}
            if thread_policy == "conversation"
            else {"user": 8}
            if thread_policy == "sender"
            else {}
        ),
    )
    tick(gateway)
    assert len(host.posts) == 2 and len(host.active) == 2
    assert all(request.url.path == "/api/v1/threads" for request in host.posts)


def test_parent_policy_change_updates_existing_children_without_rebinding(busy):
    gateway, host, configured = busy
    configure_sources(busy, policy="queue", thread="conversation")
    emit(busy, 1)
    tick(gateway)
    children = [
        row for row in gateway[0].get("/api/v1/routes").json() if row.get("parent_route_id") == configured["id"]
    ]
    before = {row["id"] for row in children}
    emit(busy, 2)
    tick(gateway)
    assert len(host.posts) == 1
    assert gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": "steer"}).status_code == 200
    tick(gateway)
    after = [row for row in gateway[0].get("/api/v1/routes").json() if row.get("parent_route_id") == configured["id"]]
    assert {row["id"] for row in after} == before
    assert all(row["busy_policy"] == "steer" for row in after)
    assert len(host.posts) == 2 and host.posts[-1].url.path.endswith("/busy_thread_1/inbox")


def test_legacy_routes_default_to_steer_without_rewriting_frozen_requests(busy):
    gateway, host, configured = busy

    async def remove_setting():
        async with gateway[1].state.store.transaction() as db:
            await db.execute(
                "UPDATE routes SET value=json_remove(value,'$.busy_policy') WHERE id=?", (configured["id"],)
            )

    gateway[0].portal.call(remove_setting)
    saved = gateway[0].get("/api/v1/routes").json()[0]
    assert saved["busy_policy"] == "steer"
    emit(busy, 1)
    tick(gateway)
    assert json.loads(host.posts[-1].content)["delivery"] == "steer"


@pytest.mark.parametrize("output", [{"reply": False, "text": "Silent"}, {"unsupported": True}])
def test_shared_run_silent_or_unsupported_output_is_decided_once(busy, output):
    gateway, host, _ = busy
    emit(busy, 1)
    tick(gateway)
    emit(busy, 2)
    tick(gateway)
    host.incorporate()
    host.complete("busy_run_1", output)
    tick(gateway)
    assert gateway[0].get("/api/v1/replies").json() == []
    assert {event["state"] for event in gateway[0].get("/api/v1/events").json()} == {"completed"}
    rows = gateway[0].portal.call(gateway[1].state.store.rows, "SELECT * FROM host_run_outputs")
    assert len(rows) == 1


def test_generic_mcp_host_does_not_claim_execution_control(busy):
    gateway, _, configured = busy
    generic = gateway[0].post("/api/v1/connections", json={"name": "MCP", "kind": "mcp"}).json()
    body = {"host_id": generic["id"], "agent_id": None, "reply_source": "mcp", "busy_policy": "queue"}
    response = gateway[0].patch(f"/api/v1/routes/{configured['id']}", json=body)
    assert response.status_code == 422


def test_rejected_steer_does_not_take_the_original_inputs_reply(busy):
    gateway, host, _ = busy
    first = emit(busy, 1)
    tick(gateway)
    second = emit(busy, 2)
    tick(gateway)
    host.entries[("busy_thread_1", "busy_entry_2")].update(status="failed", assigned_run_id="busy_run_1")
    host.complete("busy_run_1")
    tick(gateway)
    replies = gateway[0].get("/api/v1/replies").json()
    assert len(replies) == 1 and replies[0]["event_id"] == first
    events = {row["id"]: row for row in gateway[0].get("/api/v1/events").json()}
    assert events[second]["state"] == "failed"


def test_many_steers_are_submitted_fifo_without_waiting_for_run_end(busy):
    gateway, host, _ = busy
    ids = [emit(busy, index) for index in range(1, 5)]
    for index in range(4):
        tick(gateway)
        assert len(host.posts) == index + 1
    assert [request.headers["Idempotency-Key"] for request in host.posts] == ids
    assert len({request.url.path for request in host.posts[1:]}) == 1
    host.incorporate()
    host.complete("busy_run_1")
    tick(gateway)
    replies = gateway[0].get("/api/v1/replies").json()
    assert len(replies) == 1 and replies[0]["event_id"] == ids[-1]
