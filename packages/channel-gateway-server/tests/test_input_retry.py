"""Administrator retries separate failed execution from uncertain Host acceptance."""

import asyncio
import json
import sqlite3
from functools import partial

import httpx
import pytest

from .test_bridge import gateway as gateway_fixture
from .test_bridge import tick, webhook
from .test_thread_delivery import configure, deliver, submissions

gateway = gateway_fixture


@pytest.mark.parametrize("policy", ["conversation", "sender", "fixed", "message", "window"])
@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_retry_failed_run_submits_new_execution_with_current_configuration(gateway, policy, status):
    configured = configure(gateway, policy)
    client, _, upstream, *_ = gateway
    upstream.run_status = status
    failed = deliver(gateway, 1)
    assert failed["state"] == "failed"
    original = submissions(gateway)[0]
    assert client.patch(f"/api/v1/routes/{configured['id']}", json={"busy_policy": "queue"}).status_code == 200

    response = client.post(f"/api/v1/events/{failed['id']}/retry")
    assert response.status_code == 200, response.text
    assert response.json() == {"state": "queued"}
    assert client.post(f"/api/v1/events/{failed['id']}/retry").status_code == 409
    upstream.run_status = "completed"
    tick(gateway)
    tick(gateway)

    retried = submissions(gateway)[-1]
    assert len(submissions(gateway)) == 2
    assert retried.headers["Idempotency-Key"] != original.headers["Idempotency-Key"]
    assert json.loads(retried.content)["delivery"] == "next_run"
    completed = client.get("/api/v1/events").json()[0]
    assert completed["id"] == failed["id"]
    assert completed["run_id"] != failed["run_id"]
    assert completed["state"] == "completed"
    assert len(client.get("/api/v1/events").json()) == 1
    assert webhook(gateway, message_id=1).json()["state"] == "duplicate"
    assert client.get("/api/v1/replies").json() == []


def test_lost_retry_receipt_reuses_new_key_and_frozen_request_after_restart(gateway):
    configure(gateway, "message")
    client, app, upstream, *_ = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)
    original = submissions(gateway)[0]
    assert client.post(f"/api/v1/events/{failed['id']}/retry").status_code == 200
    upstream.fail_submit = True
    tick(gateway)
    lost = submissions(gateway)[-1]
    client.portal.call(app.state.store.close)
    client.portal.call(app.state.store.open)
    tick(gateway)
    recovered = submissions(gateway)[-1]
    assert lost.headers["Idempotency-Key"] != original.headers["Idempotency-Key"]
    assert recovered.headers["Idempotency-Key"] == lost.headers["Idempotency-Key"]
    assert recovered.url == lost.url
    assert recovered.content == lost.content


@pytest.mark.parametrize("status", ["accepted", "running", "waiting", "completed", "unknown"])
def test_retry_polling_failure_never_resubmits_active_completed_or_unrecognized_run(gateway, status):
    configure(gateway, "conversation")
    client, app, upstream, *_ = gateway
    original = deliver(gateway, 1)
    client.portal.call(partial(app.state.store.update_event, original["id"], state="failed", attempts=5))
    upstream.run_status = status
    assert client.post(f"/api/v1/events/{original['id']}/retry").json() == {"state": "submitted"}
    tick(gateway)
    assert len(submissions(gateway)) == 1
    current = client.get("/api/v1/events").json()[0]
    assert current["run_id"] == original["run_id"]
    expected = {"completed": "completed", "unknown": "failed"}.get(status, "submitted")
    assert current["state"] == expected


def test_retry_without_receipt_preserves_original_submission(gateway):
    configure(gateway, "message")
    client, _, upstream, *_ = gateway
    webhook(gateway, message_id=1)
    for _ in range(5):
        upstream.fail_submit = True
        tick(gateway)
    failed = client.get("/api/v1/events").json()[0]
    original = submissions(gateway)[0]
    assert failed["state"] == "failed" and failed["entry_id"] is None
    assert client.post(f"/api/v1/events/{failed['id']}/retry").json() == {"state": "queued"}
    tick(gateway)
    retried = submissions(gateway)[-1]
    assert retried.headers["Idempotency-Key"] == original.headers["Idempotency-Key"]
    assert retried.content == original.content
    assert retried.url == original.url


@pytest.mark.parametrize("entry_status", ["consumed", "pending", "cancelled", "rejected"])
def test_retry_resolves_entry_receipt_before_deciding_to_resubmit(gateway, monkeypatch, entry_status):
    configure(gateway, "conversation")
    client, app, upstream, *_ = gateway
    upstream.queued_receipt = True
    webhook(gateway, message_id=1)
    tick(gateway)
    event = client.get("/api/v1/events").json()[0]
    client.portal.call(partial(app.state.store.update_event, event["id"], state="failed"))
    host = app.state.bridge.adapters.host

    async def read(connection, method, path, **kwargs):
        if method == "GET" and "/inbox/" in path:
            return {"status": entry_status, "assigned_run_id": "run_1" if entry_status == "consumed" else None}
        return await host(connection, method, path, **kwargs)

    monkeypatch.setattr(app.state.bridge.adapters, "host", read)
    upstream.run_status = "failed"
    expected = "submitted" if entry_status == "pending" else "queued"
    assert client.post(f"/api/v1/events/{event['id']}/retry").json() == {"state": expected}


def test_retry_status_error_does_not_change_event_or_leak_upstream_details(gateway, monkeypatch):
    configure(gateway, "conversation")
    client, app, upstream, *_ = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)

    async def unavailable(*args, **kwargs):
        raise httpx.ReadTimeout("Private upstream error including credentials")

    monkeypatch.setattr(app.state.bridge.adapters, "host", unavailable)
    response = client.post(f"/api/v1/events/{failed['id']}/retry")
    assert response.status_code == 502
    assert "Private upstream" not in response.text
    assert client.get("/api/v1/events").json()[0] == failed
    assert len(submissions(gateway)) == 1


def test_concurrent_retries_reserve_only_one_execution(gateway):
    configure(gateway, "conversation")
    client, app, upstream, *_ = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)

    async def retry_twice():
        return await asyncio.gather(
            app.state.bridge.retry(failed["id"]), app.state.bridge.retry(failed["id"]), return_exceptions=True
        )

    results = client.portal.call(retry_twice)
    assert results.count("queued") == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    tick(gateway)
    assert len(submissions(gateway)) == 2


def test_existing_database_upgrade_preserves_delivery_receipts(gateway):
    configure(gateway, "conversation")
    client, app, upstream, *_ = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)
    client.portal.call(app.state.store.close)
    with sqlite3.connect(app.state.store.path) as db:
        db.execute("ALTER TABLE events DROP COLUMN delivery_key")
    client.portal.call(app.state.store.open)
    assert client.get("/api/v1/events").json()[0] == failed
    assert client.post(f"/api/v1/events/{failed['id']}/retry").json() == {"state": "queued"}


def test_retry_requires_administrator(gateway):
    configure(gateway, "conversation")
    client, _, upstream, *_ = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)
    client.cookies.clear()
    assert client.post(f"/api/v1/events/{failed['id']}/retry").status_code == 401


@pytest.mark.parametrize("change", ["route", "host", "event"])
def test_retry_rejects_changes_while_checking_host_status(gateway, monkeypatch, change):
    configured = configure(gateway, "conversation")
    client, app, upstream, _, host_connection = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)

    async def read(*args, **kwargs):
        store = app.state.store
        if change == "route":
            route = await store.route(configured["id"])
            assert await store.update_route(route["id"], {**route, "enabled": False}, route)
        elif change == "host":
            host = await store.connection(host_connection["id"])
            assert await store.update_connection(host["id"], {**host, "token": "replacement-token"}, host)
        else:
            await store.update_event(failed["id"], error="Concurrent diagnostic")
        return {"status": "failed"}

    monkeypatch.setattr(app.state.bridge.adapters, "host", read)
    assert client.post(f"/api/v1/events/{failed['id']}/retry").status_code == 409
    current = client.get("/api/v1/events").json()[0]
    assert current["state"] == "failed"
    assert current["entry_id"] == failed["entry_id"]
    assert current["run_id"] == failed["run_id"]
    assert len(submissions(gateway)) == 1


def test_each_confirmed_failure_gets_a_distinct_retry_key(gateway):
    configure(gateway, "conversation")
    client, _, upstream, *_ = gateway
    upstream.run_status = "failed"
    failed = deliver(gateway, 1)
    for _ in range(2):
        assert client.post(f"/api/v1/events/{failed['id']}/retry").json() == {"state": "queued"}
        tick(gateway)
        tick(gateway)
    assert len({request.headers["Idempotency-Key"] for request in submissions(gateway)}) == 3
