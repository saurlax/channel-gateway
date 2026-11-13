"""GitHub trust boundaries and durable routing against simulated REST endpoints."""

import asyncio
import hashlib
import hmac
import json

import httpx
import pytest
from channel_gateway_server.channels.common import SendBlocked
from channel_gateway_server.channels.github import GitHubClient, authorize, project, validate_position
from channel_gateway_server.models import ConnectionInput, public_connection
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .test_bridge import gateway, route, rpc  # noqa: F401

SHA = "a" * 40
SECRET = "github-test-webhook-secret"
POLICY = {
    "mode": "app",
    "login": "gateway[bot]",
    "installation_id": 42,
    "owner_user_id": "7",
    "repository_ids": ["123"],
    "actions": ["invoke", "read", "comment", "review", "create_pr"],
}
KEY = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    .decode()
)
CONFIG = {
    "name": "GitHub",
    "kind": "github",
    "self_id": "99",
    "app_id": "100",
    "private_key": KEY,
    "webhook_secret": SECRET,
    "github": POLICY,
}


def packet():
    return {
        "event": "issue_comment",
        "payload": {
            "action": "created",
            "installation": {"id": 42},
            "repository": {"id": 123, "full_name": "owner/repo"},
            "issue": {"number": 3},
            "sender": {"id": 7, "type": "User"},
            "comment": {"id": 8, "user": {"id": 7}, "body": "@gateway[bot] help", "created_at": "2026-10-30T00:00:00Z"},
        },
    }


def connection():
    return {"id": "github_1", **ConnectionInput.model_validate(CONFIG).stored()}


@pytest.mark.parametrize("mutation", ["actor", "repo", "installation", "self", "bot", "author", "mention", "edit"])
def test_projection_denies_untrusted_activation(mutation):
    p = packet()
    d = p["payload"]
    if mutation == "actor":
        d["sender"]["id"] = 66
    if mutation == "repo":
        d["repository"]["id"] = 456
    if mutation == "installation":
        d["installation"]["id"] = 43
    if mutation == "self":
        d["sender"]["id"] = 99
    if mutation == "bot":
        d["sender"]["type"] = "Bot"
    if mutation == "author":
        d["comment"]["user"]["id"] = 66
    if mutation == "mention":
        d["comment"]["body"] = "@gateway[bot]-other help"
    if mutation == "edit":
        d["action"] = "edited"
    assert project(p, connection()) == (None, None)


def test_policy_stable_ids_and_explicit_actions():
    c = connection()
    authorize(c, "7", "123", "invoke")
    c["github"]["actions"] = []
    with pytest.raises(SendBlocked):
        authorize(c, "7", "123", "invoke")
    c["github"].update(owner_only=False, allowed_user_ids=["8"], actions=["invoke"])
    with pytest.raises(SendBlocked):
        authorize(c, "7", "123", "invoke")
    authorize(c, "8", "123", "invoke")
    assert "private_key" not in public_connection(connection())
    assert "token" not in public_connection(connection())


def test_signed_malformed_callback_is_archived_before_parsing(gateway):  # noqa: F811
    client, app, *_ = gateway
    channel = client.post("/api/v1/connections", json=CONFIG).json()
    raw = b'{"broken":'
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
    response = client.post(
        f"/api/v1/hooks/github/{channel['id']}",
        content=raw,
        headers={"X-GitHub-Event": "issue_comment", "X-Hub-Signature-256": signature},
    )
    assert response.status_code == 422
    rows = client.portal.call(app.state.store.rows, "SELECT payload,state,event_type FROM channel_events")
    assert rows == [{"payload": raw, "state": "invalid", "event_type": "issue_comment"}]


def test_inline_positions():
    files = [{"filename": "a.py", "patch": "@@ -1,2 +1,2 @@\n same\n-old\n+new"}]
    validate_position(files, {"path": "a.py", "line": 2, "side": "RIGHT", "body": "Fix"})
    for change in ({"line": 3}, {"path": "other"}, {"side": "BOTH"}):
        with pytest.raises(SendBlocked):
            validate_position(files, {"path": "a.py", "line": 2, "side": "RIGHT", "body": "Fix", **change})


class API:
    def __init__(self):
        self.requests, self.tokens, self.fail = [], 0, False

    def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/access_tokens"):
            self.tokens += 1
            assert json.loads(request.content) == {"repository_ids": [123]}
            return httpx.Response(
                201, json={"token": "installation-private-token", "expires_at": "2099-01-01T00:00:00Z"}
            )
        assert request.headers["authorization"] in {"Bearer installation-private-token", "Bearer classic-private-token"}
        if path == "/user":
            return httpx.Response(200, json={"id": 99, "login": "machine"})
        if path == "/repos/owner/repo":
            return httpx.Response(200, json={"id": 123})
        if path.endswith("/files"):
            return httpx.Response(200, json=[{"filename": "a.py", "patch": "@@ -1 +1 @@\n-old\n+new"}])
        if path.endswith("/pulls/3"):
            return httpx.Response(200, json={"head": {"sha": SHA}, "base": {"sha": "b" * 40}})
        if request.method == "POST" and self.fail:
            raise httpx.ReadTimeout("private token must not escape")
        return httpx.Response(201, json={"id": 1000, "number": 4, "html_url": "https://github.com/owner/repo/pull/4"})


def test_installation_refresh_and_stale_review():
    async def run():
        api = API()
        async with httpx.AsyncClient(transport=httpx.MockTransport(api)) as http:
            client = GitHubClient(connection(), http)
            await asyncio.gather(client.headers(), client.headers())
            assert api.tokens == 1
            client.expires = 0
            await client.headers()
            assert api.tokens == 2
            context = project(packet(), connection())[1]

            async def check():
                pass

            with pytest.raises(SendBlocked):
                await client.publish(context, "Review", {"mode": "review", "head_sha": "b" * 40}, check)
            assert not any(r.url.path.endswith("/reviews") for r in api.requests)

    asyncio.run(run())


def test_signed_callback_dedup_tools_and_unknown_write(gateway):  # noqa: F811
    client, app, _, _, host = gateway
    api = API()
    app.state.channels.http = httpx.AsyncClient(transport=httpx.MockTransport(api))
    added = client.post("/api/v1/connections", json=CONFIG)
    assert added.status_code == 201, added.text
    channel = added.json()
    assert "private_key" not in channel
    assert KEY not in added.text
    configured = route((client, app, None, channel, host), kind="thread", conversation="123:3", source="mcp")
    raw = json.dumps(packet()["payload"]).encode()
    url = "/api/v1/hooks/github/" + channel["id"]
    headers = {
        "X-GitHub-Event": "issue_comment",
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
    }
    assert client.post(url, content=raw).status_code == 401
    assert client.post(url, content=raw, headers=headers).json()["state"] == "accepted"
    assert client.post(url, content=raw, headers=headers).json()["state"] == "duplicate"
    token = configured["agent_token"]
    result = rpc(
        client,
        token,
        "tools/call",
        {"name": "send_message", "arguments": {"content": [{"type": "text", "text": "Reply"}]}},
    ).json()
    assert not result["result"].get("isError"), result
    api.fail = True
    client.portal.call(app.state.bridge.tick)
    replies = client.get("/api/v1/replies").json()
    assert replies[0]["state"] == "unknown"
    assert "private token" not in replies[0]["error"]
    count = sum(r.method == "POST" and r.url.path.endswith("/comments") for r in api.requests)
    client.portal.call(app.state.bridge.tick)
    assert sum(r.method == "POST" and r.url.path.endswith("/comments") for r in api.requests) == count == 1
    client.portal.call(app.state.channels.http.aclose)


def machine_connection():
    config = {
        "name": "Machine",
        "kind": "github",
        "self_id": "99",
        "token": "classic-private-token",
        "github": {**POLICY, "mode": "machine_user", "installation_id": None, "login": "machine"},
    }
    return {"id": "github_machine", **ConnectionInput.model_validate(config).stored()}


def test_machine_identity_and_notification_checkpoint(gateway):  # noqa: F811
    from channel_gateway_server.channels.github import poll_notifications

    client, app, _, _, _ = gateway
    config = machine_connection()
    channel = client.post("/api/v1/connections", json={k: v for k, v in config.items() if k != "id"}).json()
    saved = client.portal.call(app.state.store.connection, channel["id"])
    visits = []

    def api(request):
        visits.append(request)
        assert request.url.host == "api.github.com"
        assert request.headers["authorization"] == "Bearer classic-private-token"
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"id": 99, "login": "machine"})
        if path == "/notifications":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "n1",
                        "updated_at": "2026-10-30T00:00:00Z",
                        "repository": {"id": 123, "full_name": "owner/repo"},
                        "subject": {"type": "Issue", "url": "https://api.github.com/repos/owner/repo/issues/3"},
                    }
                ],
            )
        if path.endswith("/issues/3"):
            return httpx.Response(200, json={"id": 30, "number": 3, "user": {"id": 7}, "body": "description"})
        if path.endswith("/comments"):
            return httpx.Response(200, json=[{**packet()["payload"]["comment"], "body": "@machine help"}])
        raise AssertionError(path)

    app.state.channels.http = httpx.AsyncClient(transport=httpx.MockTransport(api))

    # Cancel only the polling sleep: the checkpoint must already be durable.
    async def once():
        task = asyncio.create_task(poll_notifications(app.state.channels, saved))
        for _ in range(100):
            if await app.state.store.channel_state(saved["id"], "github_notification:n1"):
                break
            await asyncio.sleep(0.001)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    client.portal.call(once)
    assert client.portal.call(app.state.store.channel_state, saved["id"], "github_notification:n1") == {
        "updated_at": "2026-10-30T00:00:00Z"
    }
    before = len([r for r in visits if r.url.path.endswith("/comments")])
    client.portal.call(once)
    assert len([r for r in visits if r.url.path.endswith("/comments")]) == before == 1
    assert all(r.method == "GET" for r in visits)
    client.portal.call(app.state.channels.http.aclose)


def test_code_publication_checks_sha_and_records_partial_effects():
    async def run():
        api = API()

        def provider(request):
            path = request.url.path
            if "/git/ref/" in path:
                return httpx.Response(200, json={"object": {"sha": SHA}})
            if "/git/commits/" in path:
                return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
            if path.endswith(("/git/trees", "/git/commits")):
                return httpx.Response(201, json={"sha": "c" * 40})
            if path.endswith("/git/refs"):
                return httpx.Response(201, json={"ref": "refs/heads/codex/fix"})
            return api(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            c = GitHubClient(machine_connection(), http)
            context = project(packet(), connection())[1]
            effects, checks = [], []

            async def check():
                checks.append(True)

            async def effect(stage, value):
                effects.append((stage, value))

            args = {
                "head": "codex/fix",
                "base": "main",
                "head_sha": SHA,
                "title": "Fix",
                "body": "",
                "files": [{"path": "src/a.py", "content": "fixed"}],
            }
            value = await c.create_pr(context, args, check, effect)
            assert value["number"] == 4
            assert [x[0] for x in effects] == ["tree", "commit", "branch"]
            assert json.loads(api.requests[-1].content)["draft"] is True
            assert len(checks) >= 4
            with pytest.raises(SendBlocked):
                await c.create_pr(context, {**args, "head_sha": "b" * 40}, check, effect)
            with pytest.raises(SendBlocked):
                await c.create_pr(context, {**args, "files": [{"path": "../secret", "content": "x"}]}, check, effect)

    asyncio.run(run())


def test_revocation_before_host_submission(gateway):  # noqa: F811
    client, app, upstream, _, host = gateway
    channel = client.post("/api/v1/connections", json=CONFIG).json()
    route((client, app, None, channel, host), kind="thread", conversation="123:3", source="mcp")
    raw = json.dumps(packet()["payload"]).encode()
    headers = {
        "X-GitHub-Event": "issue_comment",
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
    }
    assert (
        client.post("/api/v1/hooks/github/" + channel["id"], content=raw, headers=headers).json()["state"] == "accepted"
    )
    response = client.patch("/api/v1/connections/" + channel["id"], json={"github": {**POLICY, "actions": []}})
    assert response.status_code == 200, response.text
    before = len(upstream.requests)
    client.portal.call(app.state.bridge.tick)
    assert len(upstream.requests) == before
    assert client.get("/api/v1/events").json()[0]["state"] == "blocked"


def test_native_pr_tools_publish_bounded_code(gateway):  # noqa: F811
    client, app, _, _, host = gateway
    api = API()

    def provider(request):
        path = request.url.path
        if "/git/ref/" in path:
            return httpx.Response(200, json={"object": {"sha": SHA}})
        if "/git/commits/" in path:
            return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
        if path.endswith(("/git/trees", "/git/commits")):
            return httpx.Response(201, json={"sha": "c" * 40})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/codex/fix"})
        return api(request)

    app.state.channels.http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    channel = client.post("/api/v1/connections", json=CONFIG).json()
    configured = route((client, app, None, channel, host), kind="thread", conversation="123:3", source="mcp")
    raw = json.dumps(packet()["payload"]).encode()
    headers = {
        "X-GitHub-Event": "issue_comment",
        "X-Hub-Signature-256": "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest(),
    }
    client.post("/api/v1/hooks/github/" + channel["id"], content=raw, headers=headers)
    token = configured["agent_token"]
    tools = rpc(client, token, "tools/list", {}).json()["result"]["tools"]
    assert len(tools) == 5

    def tool(name, args):
        result = rpc(client, token, "tools/call", {"name": name, "arguments": args}).json()["result"]
        assert not result.get("isError"), result
        return json.loads(result["content"][0]["text"])

    snapshot = tool("read", {"resource": "native", "arguments": {"operation": "github.pull_request"}})
    assert snapshot["head_sha"] == SHA
    sent = tool(
        "send_message",
        {
            "content": [{"type": "text", "text": "Review"}],
            "native": {
                "github": {
                    "mode": "review",
                    "head_sha": SHA,
                    "comments": [{"path": "a.py", "line": 1, "side": "RIGHT", "body": "Fix"}],
                }
            },
        },
    )
    client.portal.call(app.state.bridge.tick)
    assert tool("get_operation", {"operation_id": sent["operation_id"]})["state"] == "sent"
    action = tool(
        "execute_action",
        {
            "action": "native",
            "arguments": {
                "operation": "github.create_pr",
                "parameters": {
                    "head": "codex/fix",
                    "base": "main",
                    "head_sha": SHA,
                    "title": "Fix",
                    "files": [{"path": "src/main.py", "content": "fixed"}],
                },
            },
        },
    )
    client.portal.call(app.state.bridge.tick)
    status = tool("get_operation", {"operation_id": action["operation_id"]})
    assert status["state"] == "succeeded", status
    assert [effect["stage"] for effect in status["effects"]] == ["tree", "commit", "branch"]
    client.portal.call(app.state.channels.http.aclose)


def test_app_bot_mention_alias_and_review_request_actor():
    p = packet()
    p["payload"]["comment"]["body"] = "@gateway review this"
    assert project(p, connection())[0] is not None
    p = {
        "event": "pull_request",
        "payload": {
            "action": "review_requested",
            "installation": {"id": 42},
            "repository": {"id": 123, "full_name": "owner/repo"},
            "pull_request": {"id": 30, "number": 3, "head": {"sha": SHA}, "updated_at": "2026-10-30T00:00:00Z"},
            "sender": {"id": 7},
            "requested_reviewer": {"id": 99},
        },
    }
    assert project(p, connection())[0] is not None
    c = connection()
    c["github"]["actions"].remove("review")
    assert project(p, c) == (None, None)
    p["payload"]["sender"]["id"] = 8
    assert project(p, connection()) == (None, None)


def test_event_header_cannot_reclassify_signed_comment():
    for event in ("issues", "pull_request", "pull_request_review", "pull_request_review_comment"):
        p = packet()
        p["event"] = event
        assert project(p, connection()) == (None, None)


@pytest.mark.parametrize("scope", ["current", "other_pr", "outdated"])
def test_inline_reply_scope_and_freshness(scope):
    async def run():
        api = API()

        def provider(request):
            if request.url.path.endswith("/pulls/comments/55"):
                return httpx.Response(
                    200,
                    json={
                        "pull_request_url": "https://api.github.com/repos/owner/repo/pulls/"
                        + ("4" if scope == "other_pr" else "3"),
                        "commit_id": "b" * 40 if scope == "outdated" else SHA,
                        "position": 1,
                    },
                )
            return api(request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as http:
            c = GitHubClient(machine_connection(), http)
            context = project(packet(), connection())[1]

            async def check():
                pass

            if scope == "current":
                assert await c.publish(context, "Reply", {"mode": "reply", "comment_id": 55}, check) == "1000"
            else:
                with pytest.raises(SendBlocked):
                    await c.publish(context, "Reply", {"mode": "reply", "comment_id": 55}, check)
                assert not any(r.method == "POST" for r in api.requests)

    asyncio.run(run())
