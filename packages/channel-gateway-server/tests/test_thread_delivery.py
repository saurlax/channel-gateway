"""Host selection, thread scope isolation, and durable delivery retries."""

import pytest

from .test_bridge import gateway as gateway_fixture
from .test_bridge import tick, webhook

gateway = gateway_fixture


def configure(gateway, policy, *, fixed=None):
    client, _, upstream, channel, host = gateway
    upstream.unique_threads = True
    response = client.post(
        "/api/v1/routes",
        json={
            "name": "Thread delivery",
            "host_id": host["id"],
            "agent_id": "ap_example",
            "sender_policy": "any",
            "sources": [{"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "*"}],
            "thread_policy": policy,
            "fixed_thread_id": fixed,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def deliver(gateway, index, *, user=7, conversation=123):
    result = webhook(gateway, user=user, conversation=conversation, message_id=index)
    assert result.status_code == 200, result.text
    tick(gateway)
    tick(gateway)
    return gateway[0].get("/api/v1/events").json()[0]


def submissions(gateway):
    return [r for r in gateway[2].requests if r.method == "POST" and not r.url.path.endswith("send_msg")]


@pytest.mark.parametrize("policy", ["conversation", "sender", "fixed", "message"])
def test_thread_scope_reuse_and_isolation(gateway, policy):
    configure(gateway, policy)
    events = [
        deliver(gateway, 1),
        deliver(gateway, 2),
        deliver(gateway, 3, user=8),
        deliver(gateway, 4, conversation=124),
    ]
    threads = [row["thread_id"] for row in events]
    assert len(set(threads)) == {"conversation": 2, "sender": 3, "fixed": 1, "message": 4}[policy]
    if policy != "message":
        assert threads[0] == threads[1]
    if policy == "sender":
        assert threads[0] != threads[2] != threads[3]
    assert [r.url.path for r in submissions(gateway)].count("/api/v1/threads") == len(set(threads))
    reply = gateway[0].get("/api/v1/replies").json()[0]
    assert reply["address"]["conversation_id"] == "group:124"


def test_fixed_existing_thread_is_checked_and_receives_every_input(gateway):
    configure(gateway, "fixed", fixed="thread_existing")
    assert deliver(gateway, 1)["thread_id"] == "thread_existing"
    assert deliver(gateway, 2, conversation=124)["thread_id"] == "thread_existing"
    assert all(r.url.path == "/api/v1/threads/thread_existing/inbox" for r in submissions(gateway))


@pytest.mark.parametrize("unavailable", ["archived_threads", "missing_threads"])
def test_fixed_destination_must_be_open_and_accessible_on_its_host(gateway, unavailable):
    configured = configure(gateway, "conversation")
    getattr(gateway[2], unavailable).add("thread_existing")
    response = gateway[0].patch(
        f"/api/v1/routes/{configured['id']}",
        json={"thread_policy": "fixed", "fixed_thread_id": "thread_existing"},
    )
    assert response.status_code == 422
    assert "Private upstream error" not in response.text
    assert gateway[0].get("/api/v1/routes").json()[0]["thread_policy"] == "conversation"


def test_fixed_thread_serializes_inputs_across_conversations(gateway):
    configure(gateway, "fixed")
    webhook(gateway, message_id=1)
    webhook(gateway, conversation=124, message_id=2)
    tick(gateway)
    assert len(submissions(gateway)) == 1
    tick(gateway)
    tick(gateway)
    assert [r.url.path for r in submissions(gateway)] == ["/api/v1/threads", "/api/v1/threads/thread_1/inbox"]


def test_fixed_route_can_be_disabled_when_upstream_thread_is_unavailable(gateway):
    configured = configure(gateway, "fixed", fixed="thread_existing")
    gateway[2].missing_threads.add("thread_existing")
    before = len(gateway[2].requests)
    response = gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert len(gateway[2].requests) == before


@pytest.mark.parametrize("policy", ["fixed", "sender", "message"])
def test_lost_receipt_retries_exact_creation_and_idempotency_key(gateway, policy):
    configure(gateway, policy)
    gateway[2].fail_submit = True
    webhook(gateway, message_id=1)
    tick(gateway)
    first = submissions(gateway)[0]
    tick(gateway)
    retried = submissions(gateway)[1]
    assert first.url == retried.url
    assert first.content == retried.content
    assert first.headers["Idempotency-Key"] == retried.headers["Idempotency-Key"]


def test_thread_policy_changes_wait_for_active_work_then_start_fresh(gateway):
    configured = configure(gateway, "conversation")
    webhook(gateway, message_id=1)
    response = gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"thread_policy": "sender"})
    assert response.status_code == 409
    tick(gateway)
    tick(gateway)
    first_thread = gateway[0].get("/api/v1/events").json()[0]["thread_id"]
    assert gateway[0].patch(f"/api/v1/routes/{configured['id']}", json={"thread_policy": "sender"}).status_code == 200
    assert deliver(gateway, 2)["thread_id"] != first_thread


def test_message_history_keeps_each_replys_actual_thread(gateway):
    configure(gateway, "message")
    a = deliver(gateway, 1)
    b = deliver(gateway, 2)
    channel = gateway[3]
    history = (
        gateway[0]
        .get(
            "/api/v1/history",
            params={"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "123"},
        )
        .json()["messages"]
    )
    replies = [item for item in history if item["direction"] == "outgoing"]
    assert [item["thread_id"] for item in replies] == [a["thread_id"], b["thread_id"]]


def test_binding_survives_storage_reopen(gateway):
    configured = configure(gateway, "sender")
    first = deliver(gateway, 1)
    client, app, *_ = gateway
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    assert deliver(gateway, 2)["thread_id"] == first["thread_id"]
    assert configured["thread_policy"] == "sender"


def test_host_catalog_is_admin_only_paginated_and_filters_unusable_resources(gateway):
    client, _, upstream, channel, host = gateway
    upstream.agent_page["items"].extend(
        [
            {"id": "ap_archived", "archived_at": "2026-01-01", "default_revision_id": "rev_1"},
            {"id": "ap_draft", "default_revision_id": None},
        ]
    )
    upstream.agent_page["next_cursor"] = "page:2&opaque"
    url = f"/api/v1/connections/{host['id']}/host-resources/agents"
    result = client.get(url, params={"cursor": "page:1&opaque"})
    assert result.status_code == 200
    assert result.json() == {"items": [{"id": "ap_example", "name": "Example agent"}], "next_cursor": "page:2&opaque"}
    assert upstream.requests[-1].url.params["cursor"] == "page:1&opaque"
    assert upstream.requests[-1].url.params["archived"] == "false"
    threads = client.get(f"/api/v1/connections/{host['id']}/host-resources/threads").json()
    assert threads["items"] == [{"id": "thread_existing", "name": "Existing conversation"}]
    assert client.get(f"/api/v1/connections/{channel['id']}/host-resources/agents").status_code == 422
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_host_catalog_errors_do_not_expose_upstream_data(gateway):
    client, _, upstream, _, host = gateway
    upstream.agent_page = {"detail": "secret-credential-and-message-content"}
    response = client.get(f"/api/v1/connections/{host['id']}/host-resources/agents")
    assert response.status_code == 502
    assert "secret-credential" not in response.text


def test_fixed_thread_cannot_be_used_with_another_policy_or_generic_host(gateway):
    client, _, _, channel, host = gateway
    generic = client.post("/api/v1/connections", json={"name": "Generic", "kind": "mcp"}).json()
    body = {
        "name": "Invalid",
        "host_id": host["id"],
        "agent_id": "ap_example",
        "sender_policy": "any",
        "sources": [{"channel_id": channel["id"], "conversation_kind": "group", "conversation_id": "123"}],
    }
    assert client.post("/api/v1/routes", json={**body, "fixed_thread_id": "thread_existing"}).status_code == 422
    assert (
        client.post(
            "/api/v1/routes",
            json={
                **body,
                "host_id": generic["id"],
                "agent_id": None,
                "reply_source": "mcp",
                "thread_policy": "message",
            },
        ).status_code
        == 422
    )
