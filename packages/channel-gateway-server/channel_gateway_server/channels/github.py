"""GitHub installation callbacks and classic-PAT notification ingestion.

Credentials remain inside the gateway. No arbitrary URLs, paths or HTTP methods
are accepted from an Agent. Writes are attempted once by the existing ledgers.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import UTC, datetime
from typing import Any

from channel_gateway_core import ChannelAddress, Message
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..adapters import AdapterError
from .common import OperationRejected, SendBlocked
from .ingress import ingest_event
from .platforms.common import PlatformClient, response_data

SHA = re.compile(r"^[0-9a-f]{40}$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}/(?!\.{1,2}$)[A-Za-z0-9_.-]{1,100}$")


def authorize(connection: dict, sender: str, repository: str, action: str) -> None:
    policy = connection["github"]
    users = {policy["owner_user_id"]} if policy["owner_only"] else set(policy["allowed_user_ids"])
    if sender not in users or repository not in policy["repository_ids"] or action not in policy["actions"]:
        raise SendBlocked("GitHub caller, repository or action is not authorized")


def verify(connection: dict, raw: bytes, signature: str) -> bool:
    expected = "sha256=" + hmac.new(connection["webhook_secret"].encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), signature.encode())


def project(packet: dict, connection: dict) -> tuple[Message | None, dict | None]:
    event, payload = packet["event"], packet["payload"]
    policy = connection["github"]
    if policy["mode"] == "app" and payload.get("installation", {}).get("id") != policy["installation_id"]:
        return None, None
    if event not in {"issues", "issue_comment", "pull_request", "pull_request_review", "pull_request_review_comment"}:
        return None, None
    required = {
        "issues": {"issue"},
        "issue_comment": {"issue", "comment"},
        "pull_request": {"pull_request"},
        "pull_request_review": {"pull_request", "review"},
        "pull_request_review_comment": {"pull_request", "comment"},
    }[event]
    # The event header is not covered by GitHub's body HMAC. Its declared type
    # must match the signed entities, preventing cross-type replay activation.
    if {key for key in ("issue", "pull_request", "comment", "review") if key in payload} != required:
        return None, None
    repo = payload["repository"]
    rid = str(repo["id"])
    actor = payload.get("sender", {})
    sender = str(actor.get("id", ""))
    if sender == connection["self_id"] or actor.get("type") == "Bot":
        return None, None
    try:
        authorize(connection, sender, rid, "invoke")
    except SendBlocked:
        return None, None
    item = payload.get("issue") or payload.get("pull_request") or {}
    number = item.get("number")
    if type(number) is not int or number < 1 or not REPO.fullmatch(repo["full_name"]):
        raise ValueError("Invalid GitHub destination")
    review_request = event == "pull_request" and payload.get("action") == "review_requested"
    if review_request:
        if str(payload.get("requested_reviewer", {}).get("id")) != connection["self_id"]:
            return None, None
        try:
            authorize(connection, sender, rid, "review")
        except SendBlocked:
            return None, None
        text = "Review requested.\n" + (item.get("body") or "")
        source_id = "request:" + str(item["id"]) + ":" + item["head"]["sha"] + ":" + sender
        action = "review"
        when = item.get("updated_at")
    else:
        if event not in {
            "issues",
            "issue_comment",
            "pull_request_review_comment",
            "pull_request_review",
            "pull_request",
        }:
            return None, None
        if payload.get("action") not in {"opened", "created", "submitted"}:
            return None, None
        comment = payload.get("comment") or payload.get("review") or item
        text = comment.get("body") or ""
        # Verify authorship independently of the webhook sender.
        if str(comment.get("user", {}).get("id")) != sender:
            return None, None
        login = policy["login"]
        mention_text = text
        if policy["mode"] == "app" and login.endswith("[bot]"):
            login = login.removesuffix("[bot]")
            # GitHub commonly addresses App bots without the API login suffix.
            mention_text = re.sub(r"\[bot\]", "", text, flags=re.I)
        if not re.search(r"(?<![\w-])@" + re.escape(login) + r"(?![\w-])", mention_text, re.I):
            return None, None
        source_id = event + ":" + str(comment["id"])
        action, when = "comment", comment.get("created_at") or comment.get("submitted_at")
    context = {"repository_id": rid, "repository": repo["full_name"], "number": number, "sender_id": sender}
    if event == "pull_request_review_comment":
        context["review_comment_id"] = payload["comment"]["id"]
    message = Message(
        address=ChannelAddress(channel="github", account_id=connection["id"], conversation_id=f"thread:{rid}:{number}"),
        source_message_id=source_id,
        sender_id=sender,
        text=text[:60000],
        bot_mentioned=True,
        occurred_at=datetime.fromisoformat(when.replace("Z", "+00:00")) if when else datetime.now(UTC),
        structured={"github": {**context, "requested_action": action}},
    )
    return message, context


class GitHubClient(PlatformClient):
    base, provider = "https://api.github.com", "GitHub"

    def __init__(self, connection, http):
        super().__init__(connection, http, http)
        self.access_token = ""
        self.expires = 0.0
        self.token_lock = asyncio.Lock()
        self.not_before = 0.0
        self.poll_interval = connection["github"]["poll_seconds"]

    async def request(self, method, path, **kwargs) -> Any:
        if time.time() < self.not_before:
            if method != "GET":
                raise SendBlocked("GitHub rate limit is active; retry after the reset")
            await asyncio.sleep(self.not_before - time.time())
        headers = {**await self.headers(), "Accept-Encoding": "identity"}
        if kwargs.get("check"):
            await kwargs["check"]()
        async with asyncio.timeout(30):
            async with self.http.stream(
                method,
                self.base + path,
                json=kwargs.get("body"),
                params=kwargs.get("params"),
                headers=headers,
                follow_redirects=False,
                timeout=20,
            ) as response:
                if path == "/notifications":
                    self.poll_interval = max(self.poll_interval, int(response.headers.get("x-poll-interval", "60")))
                if response.status_code in {403, 429}:
                    delay = response.headers.get("retry-after", "60")
                    reset = response.headers.get("x-ratelimit-reset", "0")
                    self.not_before = max(
                        time.time() + (int(delay) if delay.isdecimal() else 60), int(reset) if reset.isdecimal() else 0
                    )
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    raise OperationRejected(f"GitHub rejected the operation (HTTP {response.status_code})")
                if not response.is_success:
                    raise AdapterError("GitHub operation was not confirmed")
                value = json.loads(await response_data(response))
        if not isinstance(value, (dict, list)):
            raise AdapterError("GitHub returned an invalid response")
        return value

    async def headers(self):
        if self.connection["github"]["mode"] == "machine_user":
            token = self.connection["token"]
        else:
            async with self.token_lock:
                if time.time() >= self.expires - 60:
                    now = int(time.time())

                    def encode(value):
                        return base64.urlsafe_b64encode(value).rstrip(b"=")

                    data = (
                        encode(b'{"alg":"RS256","typ":"JWT"}')
                        + b"."
                        + encode(
                            json.dumps({"iat": now - 60, "exp": now + 540, "iss": self.connection["app_id"]}).encode()
                        )
                    )
                    key = serialization.load_pem_private_key(self.connection["private_key"].encode(), password=None)
                    if not isinstance(key, rsa.RSAPrivateKey):
                        raise SendBlocked("GitHub App requires an RSA private key")
                    jwt = (data + b"." + encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256()))).decode()
                    # Use a separate base client to avoid recursively acquiring a token.
                    auth = PlatformClient({"token": jwt}, self.http, self.http)
                    auth.base, auth.provider = self.base, self.provider
                    installation = self.connection["github"]["installation_id"]
                    value = await auth.request(
                        "POST",
                        f"/app/installations/{installation}/access_tokens",
                        body={"repository_ids": [int(x) for x in self.connection["github"]["repository_ids"]]},
                    )
                    if not isinstance(value, dict):
                        raise AdapterError("Invalid installation token response")
                    self.access_token = value["token"]
                    self.expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00")).timestamp()
                token = self.access_token
        return {
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def ensure_identity(self):
        async with self.identity_lock:
            if self.identity_verified:
                return
            if self.connection["github"]["mode"] == "machine_user":
                value = await self.request("GET", "/user")
                if (
                    str(value["id"]) != self.connection["self_id"]
                    or value["login"].lower() != self.connection["github"]["login"].lower()
                ):
                    raise SendBlocked("GitHub token does not match the configured machine user")
            self.identity_verified = True

    async def destination(self, context: dict, action: str):
        authorize(self.connection, context["sender_id"], context["repository_id"], action)
        repo = context["repository"]
        if not REPO.fullmatch(repo) or type(context["number"]) is not int or context["number"] < 1:
            raise SendBlocked("Invalid GitHub destination")
        await self.ensure_identity()
        actual = await self.request("GET", "/repos/" + repo)
        if str(actual["id"]) != context["repository_id"]:
            raise SendBlocked("GitHub repository identity changed")
        return "/repos/" + repo

    async def snapshot(self, context):
        root = await self.destination(context, "read")
        pull = await self.request("GET", root + f"/pulls/{context['number']}")
        files = []
        for page in range(1, 32):
            batch = await self.request(
                "GET", root + f"/pulls/{context['number']}/files", params={"per_page": 100, "page": page}
            )
            files.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise SendBlocked("PR exceeds the supported file limit")
        current = await self.request("GET", root + f"/pulls/{context['number']}")
        if current["head"]["sha"] != pull["head"]["sha"]:
            raise SendBlocked("PR head changed during diff retrieval")
        return {"head_sha": pull["head"]["sha"], "base_sha": pull["base"]["sha"], "files": files}

    async def publish(self, context, text, options, check):
        mode = options.get("mode", "comment")
        root = await self.destination(context, "review" if mode == "review" else "comment")
        number = context["number"]
        if mode == "review":
            sha = options.get("head_sha", "")
            if not SHA.fullmatch(sha):
                raise SendBlocked("Review requires a full head SHA")
            snapshot = await self.snapshot(context)
            if snapshot["head_sha"] != sha:
                raise SendBlocked("PR head changed; read and review the current version")
            comments = options.get("comments", [])
            for comment in comments:
                validate_position(snapshot["files"], comment)
            # Re-fetch immediately before writing. GitHub commits the review to commit_id.
            pull = await self.request("GET", root + f"/pulls/{number}")
            if pull["head"]["sha"] != sha:
                raise SendBlocked("PR head changed before review publication")
            await check()
            value = await self.request(
                "POST",
                root + f"/pulls/{number}/reviews",
                check=check,
                body={"commit_id": sha, "body": text, "event": "COMMENT", "comments": comments},
            )
        elif mode == "reply":
            cid = options.get("comment_id")
            if type(cid) is not int or cid < 1:
                raise SendBlocked("Choose a review comment")
            comment = await self.request("GET", root + f"/pulls/comments/{cid}")
            if comment.get("pull_request_url") != self.base + root + f"/pulls/{number}":
                raise SendBlocked("Review comment belongs to another PR")
            pull = await self.request("GET", root + f"/pulls/{number}")
            if comment.get("commit_id") != pull["head"]["sha"] or comment.get("position") is None:
                raise SendBlocked("Review discussion is outdated")
            await check()
            value = await self.request(
                "POST", root + f"/pulls/{number}/comments/{cid}/replies", check=check, body={"body": text}
            )
        else:
            await check()
            value = await self.request("POST", root + f"/issues/{number}/comments", check=check, body={"body": text})
        return str(value["id"])

    async def create_pr(self, context, args, check, effect):
        root = await self.destination(context, "create_pr")
        # Code execution belongs to the Host. Publish bounded file changes through
        # Git data, or an existing branch, without exporting credentials.
        from urllib.parse import quote

        head, base = args["head"], args["base"]
        if (
            not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", head)
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", base)
            or head == base
        ):
            raise SendBlocked("Choose distinct branch names in this repository")
        files = args.get("files", [])
        ref = await self.request("GET", root + "/git/ref/heads/" + quote(base if files else head, safe=""))
        if not SHA.fullmatch(args["head_sha"]) or ref["object"]["sha"] != args["head_sha"]:
            raise SendBlocked("PR source branch changed")
        if files:
            if not head.startswith("codex/") or ".." in head or "//" in head or head.endswith("/"):
                raise SendBlocked("Code publication requires a new codex/ branch")
            commit = await self.request("GET", root + "/git/commits/" + args["head_sha"])
            tree = []
            for file in files:
                path = file["path"]
                if (
                    path.startswith("/")
                    or any(p in {"", ".", "..", ".git"} for p in path.split("/"))
                    or "\\" in path
                    or path.startswith(".github/workflows/")
                ):
                    raise SendBlocked("Unsupported code publication path")
                tree.append({"path": path, "mode": "100644", "type": "blob", "content": file["content"]})
            await check()
            created = await self.request(
                "POST", root + "/git/trees", check=check, body={"base_tree": commit["tree"]["sha"], "tree": tree}
            )
            await effect("tree", created["sha"])
            await check()
            created = await self.request(
                "POST",
                root + "/git/commits",
                check=check,
                body={"message": args["title"], "tree": created["sha"], "parents": [args["head_sha"]]},
            )
            await effect("commit", created["sha"])
            await check()
            await self.request(
                "POST", root + "/git/refs", check=check, body={"ref": "refs/heads/" + head, "sha": created["sha"]}
            )
            await effect("branch", head)
        await check()
        value = await self.request(
            "POST",
            root + "/pulls",
            check=check,
            body={"head": head, "base": base, "title": args["title"], "body": args["body"], "draft": True},
        )
        return {"number": value["number"], "url": value["html_url"]}


def validate_position(files, comment):
    if (
        set(comment) != {"path", "line", "side", "body"}
        or comment["side"] not in {"LEFT", "RIGHT"}
        or type(comment["line"]) is not int
        or comment["line"] < 1
        or not isinstance(comment["body"], str)
        or not 1 <= len(comment["body"]) <= 16000
    ):
        raise SendBlocked("Invalid inline review comment")
    file = next((f for f in files if f["filename"] == comment["path"]), None)
    if not file or not file.get("patch"):
        raise SendBlocked("Inline review requires an available diff patch")
    old, new = 0, 0
    for line in file["patch"].splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old, new = int(match[1]), int(match[2])
            continue
        if not line or line.startswith("\\"):
            continue
        side = comment["side"]
        if (side == "LEFT" and line[0] in " -" and old == comment["line"]) or (
            side == "RIGHT" and line[0] in " +" and new == comment["line"]
        ):
            return
        old += line[0] in " -"
        new += line[0] in " +"
    raise SendBlocked("Inline review line is outside the current diff")


async def poll_notifications(manager, connection):
    """Never mark notifications read; checkpoint only after durable ingestion."""
    client = manager.github(connection)
    await client.ensure_identity()
    manager.set_status(connection["id"], "connected")
    while True:
        for page in range(1, 11):
            notifications = await client.request(
                "GET", "/notifications", params={"all": "true", "per_page": 100, "page": page}
            )
            for notification in notifications:
                repo = notification["repository"]
                if str(repo["id"]) not in connection["github"]["repository_ids"] or notification["subject"][
                    "type"
                ] not in {"Issue", "PullRequest"}:
                    continue
                key = "github_notification:" + str(notification["id"])
                stamp = {"updated_at": notification["updated_at"]}
                if await manager.store.channel_state(connection["id"], key) == stamp:
                    continue
                # Notification URLs are data, never credential forwarding authority.
                url = notification["subject"]["url"]
                prefix = client.base + "/repos/" + repo["full_name"]
                if not REPO.fullmatch(repo["full_name"]) or not re.fullmatch(
                    re.escape(prefix) + r"/(issues|pulls)/[1-9][0-9]*", url
                ):
                    raise ValueError("Invalid notification subject")
                item = await client.request("GET", url.removeprefix(client.base))
                root = "/repos/" + repo["full_name"]
                n = item["number"]
                is_pr = notification["subject"]["type"] == "PullRequest"
                collections = [("issue_comment", f"/issues/{n}/comments")]
                if is_pr:
                    collections += [
                        ("pull_request_review_comment", f"/pulls/{n}/comments"),
                        ("pull_request_review", f"/pulls/{n}/reviews"),
                        ("pull_request", f"/issues/{n}/timeline"),
                    ]
                await ingest_event(
                    manager.store,
                    connection,
                    {
                        "event": "pull_request" if is_pr else "issues",
                        "payload": {
                            "action": "opened",
                            "repository": repo,
                            "pull_request" if is_pr else "issue": item,
                            "sender": item["user"],
                        },
                    },
                    manager.ingest,
                    lambda p: project(p, connection),
                )
                for event, path in collections:
                    timeline = []
                    for detail_page in range(1, 32):
                        batch = await client.request("GET", root + path, params={"per_page": 100, "page": detail_page})
                        if event == "pull_request":
                            timeline.extend(batch)
                            if len(batch) == 100:
                                continue
                            # Only the latest request/removal for this account can
                            # establish the actor of its current review request.
                            latest = next(
                                (
                                    entry
                                    for entry in reversed(timeline)
                                    if entry.get("event") in {"review_requested", "review_request_removed"}
                                    and str(entry.get("requested_reviewer", {}).get("id")) == connection["self_id"]
                                ),
                                None,
                            )
                            batch = [latest] if latest else []
                        for comment in batch:
                            payload = {"repository": repo, "pull_request" if is_pr else "issue": item}
                            if event == "pull_request":
                                if comment.get("event") != "review_requested" or not any(
                                    str(user["id"]) == connection["self_id"]
                                    for user in item.get("requested_reviewers", [])
                                ):
                                    continue
                                payload.update(
                                    action="review_requested",
                                    sender=comment["actor"],
                                    requested_reviewer=comment.get("requested_reviewer", {}),
                                )
                            else:
                                payload.update(
                                    action="submitted" if event == "pull_request_review" else "created",
                                    sender=comment["user"],
                                )
                                payload["review" if event == "pull_request_review" else "comment"] = comment
                            await ingest_event(
                                manager.store,
                                connection,
                                {"event": event, "payload": payload},
                                manager.ingest,
                                lambda p: project(p, connection),
                            )
                        if len(batch) < 100:
                            break
                    else:
                        raise AdapterError("GitHub discussion exceeds the supported page limit")
                await manager.store.set_channel_state(connection["id"], key, stamp)
            if len(notifications) < 100:
                break
        else:
            raise AdapterError("GitHub notification backlog exceeds the supported page limit")
        await asyncio.sleep(client.poll_interval)


async def authorized_context(store, connection, route, action):
    context = await store.channel_state(connection["id"], "context:thread:" + route["conversation_id"])
    if (
        route["conversation_kind"] != "thread"
        or route["conversation_id"] != f"{context.get('repository_id')}:{context.get('number')}"
    ):
        raise SendBlocked("Wait for an authorized GitHub invocation in this conversation")
    from ..models import route_allows_sender

    if not route_allows_sender(route, context.get("sender_id", "")):
        raise SendBlocked("GitHub invocation authority was revoked")
    authorize(connection, context["sender_id"], context["repository_id"], action)
    return context
