"""Only explicit message-tool calls may publish channel content."""

import pytest
from channel_gateway_server.channels.actions import reaction_authority
from channel_gateway_server.channels.common import SendBlocked

from .test_bridge import create_rule, route, send_tool, source, tick, webhook
from .test_bridge import gateway as gateway_fixture

gateway = gateway_fixture


@pytest.mark.parametrize("output", [None, "Private completion", {"reply": True, "text": "Do not send"}])
def test_completed_outputs_are_ignored_without_validation_or_retry(gateway, output):
    client, _, upstream, *_ = gateway
    route(gateway)
    upstream.output = output
    webhook(gateway)
    tick(gateway)
    tick(gateway)
    event = client.get("/api/v1/events").json()[0]
    assert event["state"] == "completed" and event["error"] is None and event["attempts"] == 0
    assert client.get("/api/v1/replies").json() == []
    assert len([r for r in upstream.requests if r.method == "POST"]) == 1


def test_route_token_sends_for_all_sources_without_a_dedicated_binding(gateway):
    client, _, upstream, *_ = gateway
    configured = create_rule(gateway, [source(kind="*")])
    assert configured["mcp_connection_id"] is None
    webhook(gateway)
    requested = send_tool(gateway, "Tool answer")
    tick(gateway)
    tick(gateway)
    replies = client.get("/api/v1/replies").json()
    assert len(replies) == 1 and replies[0]["id"] == requested["reply_id"]
    assert replies[0]["state"] == "sent" and replies[0]["event_id"] is None
    assert len([r for r in upstream.requests if r.url.path.endswith("send_msg")]) == 1


def test_upgrade_blocks_old_automatic_replies_and_preserves_tool_sends_and_scoped_grants(gateway):
    client, app, _, _, _ = gateway
    configured = route(gateway)
    webhook(gateway)
    requested = send_tool(gateway, "Pending tool send")
    old_mcp = route(gateway, conversation="456")
    event = client.get("/api/v1/events").json()[0]

    async def seed_old_version():
        async with app.state.store.transaction() as db:
            await db.execute(
                "UPDATE routes SET value=json_set(value,'$.reply_source','output') WHERE id=?", (configured["id"],)
            )
            await db.execute(
                "UPDATE routes SET value=json_set(value,'$.reply_source','mcp') WHERE id=?", (old_mcp["id"],)
            )
            await db.execute(
                "INSERT INTO replies(id,route_id,event_id,text,state) VALUES('reply_old',?,?,?,'queued')",
                (configured["id"], event["id"], "Old automatic output"),
            )

    client.portal.call(seed_old_version)
    for _ in range(2):
        client.portal.call(app.state.store.close)
        client.portal.call(app.state.store.open)
    rows = {r["id"]: r for r in client.get("/api/v1/replies").json()}
    assert rows["reply_old"]["state"] == "blocked"
    assert rows[requested["reply_id"]]["state"] == "queued"
    saved = {r["id"]: r for r in client.get("/api/v1/routes").json()}
    assert all("reply_source" not in r for r in saved.values())
    assert saved[configured["id"]]["action_grants"] == []
    grants = saved[old_mcp["id"]]["action_grants"]
    assert grants == ["message.react.onebot"]
    assert reaction_authority(saved[old_mcp["id"]], "onebots") == "message.react.onebot"
    with pytest.raises(SendBlocked):
        reaction_authority(saved[old_mcp["id"]], "qq")
    tick(gateway)
    assert next(r for r in client.get("/api/v1/replies").json() if r["id"] == requested["reply_id"])["state"] == "sent"
