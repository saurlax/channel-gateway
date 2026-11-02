# GitHub

GitHub is an API-configured channel (`kind: github`) with two credential modes.
The existing a13n Host API executes agent work; generic MCP Hosts can read inputs
and use the same five tools. No GitHub credentials are delivered to either Host.
The console displays GitHub channels and supports credential rotation/deletion,
but does not yet provide a GitHub creation or policy form; use authenticated
management API requests, documented at `/api/docs`.

## Supported scope

- Issue/PR descriptions and new comments mentioning the configured login activate
  an authorized route. App bots also recognize the mention without `[bot]`.
  PR review submissions and inline review comments can also
  mention it. Every follow-up must mention it again; unrelated discussion and
  edits never activate an agent.
- A review request addressed to this account activates only when its requesting
  actor is authorized for both `invoke` and `review`.
- Ordinary replies use issue comments, including on PRs. Explicit inline replies
  use the PR review discussion endpoint. Reviews are `COMMENT` reviews, with a
  summary and optional single-line inline comments. Approve/request-changes,
  multiline comments, merges, workflow administration, forks, binary files,
  file deletion and automatic repository checkout are not implemented.
- Agents can read the bound PR's changed-file patches and head/base SHAs. Large
  discussions/diffs fail at bounded page limits; omitted/binary patches cannot
  receive inline comments. This is not general repository history or search.
- Agents can open a draft PR from an existing branch in the same repository, or
  submit up to 20 UTF-8 text files (100,000 characters each) to a new `codex/`
  branch through Git trees/commits/refs. Existing refs are never overwritten.
  Files use regular `100644` mode. Workflow files and traversal paths are denied.
  Execution, tests and file editing remain responsibilities of the configured
  Host. The Host passes file contents through MCP, never a local filesystem path.

No live GitHub acceptance has been performed. Local tests simulate REST APIs,
callbacks, persistence, permission denial, token renewal and uncertain writes.

## Trust boundary

Configure stable numeric user and repository IDs, obtained independently through
GitHub's authenticated API. Logins are used only to recognize mentions and check
machine-user credential identity. They are not caller authorization.

`github.owner_only` defaults to true and permits only `owner_user_id`.
When false, **only** `allowed_user_ids` may invoke; the owner is not implicitly
included. Repository IDs are mandatory and actions default to empty. Explicit
`invoke`, `read`, `comment`, `review`, and `create_pr` capabilities are independent.
A review also requires `read` to validate its current diff. A route's current
sender policy and destination scope apply in addition to this channel policy.
Public readability, installation access, upstream collaborator status and text
in a comment or code file never grant an action. Configure the Host's own tools
for the admitted callers; `invoke` allows that Host to incur execution costs.

The backend checks callers before enqueueing model work and again before Host
submission. Message/action admission freezes the current verified invocation;
publication rechecks it and the current route/channel policy. A newer invocation
in the conversation blocks a queued publication frozen under an older context;
the agent must submit a fresh tool request. Revocation cannot cancel an upstream
request already in flight or erase data already submitted to the Host.

## GitHub App

Create/install the App yourself. Grant repository Metadata read, Issues read/write,
and Pull requests read/write; add Contents read/write only for code publication.
Choose selected repositories. Subscribe to Issues, Issue comment, Pull request,
Pull request review and Pull request review comment events. Enable a webhook at
`https://GATEWAY/api/v1/hooks/github/CONNECTION_ID`, using the same random secret
configured below. Enable HTTPS at your reverse proxy and retain the service's
single-process/single-replica deployment model.

Create a channel with `POST /api/v1/connections`:

```json
{
  "name": "GitHub App",
  "kind": "github",
  "self_id": "900001",
  "app_id": "100001",
  "private_key": "-----BEGIN RSA PRIVATE KEY-----\nREPLACE_WITH_PEM\n-----END RSA PRIVATE KEY-----",
  "webhook_secret": "replace-with-a-random-signing-secret",
  "github": {
    "mode": "app",
    "login": "example-agent[bot]",
    "installation_id": 200001,
    "owner_user_id": "300001",
    "owner_only": true,
    "repository_ids": ["400001"],
    "actions": ["invoke", "read", "comment", "review"]
  }
}
```

`self_id` is the bot's numeric user ID, not the App ID. One connection binds one
installation. The service signs short-lived RS256 App JWTs and acquires installation
tokens restricted to the configured repository IDs, caching until 60 seconds
before expiry. Token creation requires actual installation authorization; no
OAuth installation flow or App creation is performed by CG. Expired/revoked
credentials fail closed. Installation tokens are not used for notifications.

Callbacks verify HMAC-SHA256 over the original bounded body and require the
configured installation ID. Authenticated packets are journaled before model
submission. Supported message IDs deduplicate model delivery across redelivery
and restart. Bot-authored events and this account's own events never activate.
Unsupported event types, including ping, are acknowledged without activation.

## Machine user

Use a dedicated ordinary GitHub account with a **classic PAT**. As verified in
GitHub's official documentation on 2026-10-30, Notifications REST endpoints do not
support GitHub App user tokens, installation tokens or fine-grained PATs. Grant
`notifications`, and the appropriate repository scopes for the required reads
and writes (`public_repo` for public repository writes; `repo` for private
repositories). Organization SSO and repository permissions still apply. Reading
public code does not grant write permission. CG checks `/user` against the numeric
account ID and configured login before polling or publishing.

```json
{
  "name": "GitHub machine user",
  "kind": "github",
  "self_id": "900002",
  "token": "REPLACE_WITH_CLASSIC_PAT",
  "github": {
    "mode": "machine_user",
    "login": "example-machine",
    "owner_user_id": "300001",
    "repository_ids": ["400001", "400002"],
    "actions": ["invoke", "read", "comment", "review", "create_pr"],
    "poll_seconds": 60
  }
}
```

Polling reads `/notifications?all=true` without marking notifications read. It
uses both read and unread notifications, filters to configured repository IDs
and Issue/PR subjects, then fetches descriptions, issue comments, review comments,
reviews and review-request timeline actors. Active review requests must still
list the machine user among requested reviewers. Notification URLs must match
the fixed `api.github.com/repos/OWNER/REPO/issues|pulls/NUMBER` endpoint; arbitrary
URLs never receive the PAT. Stable source IDs share callback deduplication.

Notification update checkpoints persist only after all fetched events are
journaled/ingested. Failed batches are retried without skipping them. At most
1,000 notifications and 3,100 items per discussion collection are scanned per
cycle. Exceeding the limits reports a connection error; it never advances an
incomplete checkpoint. First startup may process old mentions in retained
notifications. Review this scope before enabling an expensive Host. Notifications
must actually be delivered to this account; polling is not a global GitHub mention
search. New mentions in participating discussions continue the same route.
The interval honors `X-Poll-Interval`; rate-limit/reset headers delay reads, and
writes fail closed during an active cooldown instead of retrying automatically.

## Routes and MCP

Conversations use `conversation_kind: thread` and
`conversation_id: REPOSITORY_ID:ISSUE_OR_PR_NUMBER`, for example `400001:12`.
Use the existing route API with that channel ID, a Host ID and, for a13n, an Agent
ID. Wildcard routes materialize independent conversation children. Use per-
conversation or window delivery for isolated working environments; a fixed
thread intentionally shares execution history. The existing [Host setup](agent-hosts.md)
and [a13n guide](onebots-a13n.md) apply without a new execution service.

`discover(resource='operations', route_id=...)` advertises schemas. Examples below
are tool arguments, with the authorized `route_id` supplied on every Host-key call.

Read the current PR:

```json
{"resource":"native","source":"platform","arguments":{"operation":"github.pull_request","parameters":{}}}
```

Send a normal issue/PR comment:

```json
{"content":[{"type":"text","text":"The fix is ready."}]}
```

Publish a review through `send_message` (replace SHA with the read result):

```json
{
  "content": [{"type":"text","text":"One issue found."}],
  "native": {"github": {
    "mode":"review",
    "head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "comments":[{"path":"src/main.py","line":12,"side":"RIGHT","body":"Handle the empty input."}]
  }}
}
```

Reply in an existing inline discussion using
`native: {"github":{"mode":"reply","comment_id":12345}}`. The API verifies the
comment belongs to the bound PR and is on its current head with a live diff
position. Diff reads and reviews recheck head SHA, paths and LEFT/RIGHT line
positions. Reviews explicitly pin `commit_id`. A concurrent push after the final
check can still race the external request; GitHub will associate the result with
the supplied commit. CG does not claim an atomic check-and-publish guarantee.

Create a draft PR with `execute_action`:

```json
{
  "action":"native",
  "arguments":{"operation":"github.create_pr","parameters":{
    "head":"codex/fix-empty-input",
    "base":"main",
    "head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "title":"Handle empty input",
    "body":"Validation: unit tests passed in the execution Host.",
    "files":[{"path":"src/main.py","content":"print('example replacement')\n"}]
  }}
}
```

With `files`, `head_sha` is the expected **base branch** SHA; CG makes a new commit
and new branch. Without `files`, it is the expected **existing head branch** SHA.
This action always targets the invoking conversation's repository; it cannot
select another repository or a fork. Enable `create_pr` explicitly before use.

Poll `get_operation`. Writes use the existing durable reply/action ledgers.
A crash or timeout after a write is `unknown` and is never automatically resent.
Multi-step code publication records confirmed tree, commit and branch effects;
these are returned with the action status. A later rejection can leave those
effects even when PR creation fails. Inspect GitHub before a fresh request;
CG does not automatically delete partial branches or roll back commits.
Agent final output is private and never posted automatically.

## Operations

Use authenticated API login and CSRF as described in the [access contract](../spec/02-identity-and-access.md).
Do not place secrets in shell history or tracked files. `PATCH /api/v1/connections/ID`
accepts replacement `token`, `private_key`, `webhook_secret`, and a complete
`github` policy. Policy changes restart the listener and invalidate pending
publication snapshots. Account/mode changes should use a separate connection.
Keys/tokens are write-only in management responses. SQLite contains private
credentials and journaled messages unencrypted at rest; keep `CGW_DATA_DIR` private
and persistent. Never mount it into the execution Host or an untrusted repository.

Official API references checked on 2026-10-30:

- [Notifications and supported credentials](https://docs.github.com/en/rest/activity/notifications)
- [Installation tokens and expiry](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
- [PR reviews and commit_id](https://docs.github.com/en/rest/pulls/reviews)
- [Review comments and replies](https://docs.github.com/en/rest/pulls/comments)
- [Git trees](https://docs.github.com/en/rest/git/trees), [commits](https://docs.github.com/en/rest/git/commits), [references](https://docs.github.com/en/rest/git/refs)

REST requests pin `X-GitHub-Api-Version: 2022-11-28`. GitHub Enterprise Server,
OAuth refresh for user credentials, OAuth installation UI and real-account
permission acceptance remain outside this implementation.
