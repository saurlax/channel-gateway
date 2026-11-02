# GitHub channel

This contract covers the API-configured GitHub adapter. Setup and executable
configuration examples live in [the GitHub guide](../docs/github.md). Protocol
behavior is locally tested; live installation acceptance is not yet verified.

## Identity and admission

One `github` connection binds either one App installation (`webhook`) or one
ordinary machine user (`polling`). Both project into `thread:REPOSITORY_ID:NUMBER`
addresses. Stable numeric repository/user IDs are authority; logins only recognize
mentions. Account IDs, App IDs, installation IDs and caller IDs are distinct.
The core models and existing Host adapters remain unchanged.

App callbacks verify SHA256 HMAC of bounded original bytes and installation ID.
App JWTs acquire repository-restricted installation tokens, renewed before expiry.
Machine users verify `/user` and use classic PAT notifications. App and fine-grained
tokens are not Notifications credentials. Notification and attachment URLs never
select credential forwarding destinations; REST requests use fixed GitHub.com
endpoints, bounded responses/deadlines, pinned API version and no redirects.

Every projected invocation must pass the channel's repository IDs, numeric caller
policy and `invoke` action, followed by current route authorization. Default actions
are empty; owner-only is the default caller policy. Disabling owner-only replaces
that single caller with the explicit allowlist. No collaborator role, public read
access, comment content, diff content, model response or upstream scope can expand
this policy. Review requests also require `review` and the exact requested account.
Own messages and bot actors never activate. New addressed descriptions/comments,
review submissions and review requests activate; edits and unrelated discussion do
not. Follow-ups require a new mention or review request.

Host submission rechecks channel authorization. Existing a13n Thread/inbox APIs
own execution; generic MCP clients retain their existing available-input behavior.
No GitHub token, private key or installation token enters Host payloads, tool
results or an execution environment. The channel policy is independently editable
through authenticated management. Credential/policy changes stop and restart the
account task and invalidate stale ingress snapshots.

## Retention and routing

Authenticated packets enter the private channel journal before projection and
ACK/checkpoint. Existing source-message identities deduplicate model work across
callback retries and notification scans/restarts. Denied and unsupported packets
remain private journal records; they grant no Agent activation or reply authority.
Normal comments and inline discussions share an issue/PR conversation, with native
comment IDs retained in verified context. Existing thread delivery policies apply.

Machine-user polling scans read and unread notifications, only in configured
repositories, and never changes notification read state. It fetches descriptions,
issue comments and PR reviews/comments/timeline actors. Review requests discovered
in timelines require the account still be a requested reviewer. Each notification's
update stamp is stored after its complete bounded scan is ingested. Failure before
that leaves it retryable. Existing source IDs suppress duplicate model inputs.
Initial scans can activate historical retained mentions. Page-limit errors do not
skip incomplete data. This is notification coverage, not global mention search.
Polling honors the server's minimum interval and rate-reset/cooldown headers.

## Agent operations

The public five-tool inventory does not change. `discover` adds finite schemas:

- `read` native `github.pull_request`: current bound PR head/base SHA and changed
  files/patches, under the channel's `read` capability and invocation scope.
- `send_message` with optional `native.github`: ordinary comments; `mode: review`
  for COMMENT reviews; `mode: reply` for inline discussion replies. These require
  `comment` or `review` independently. Reviews also require `read` for validation.
- `execute_action` native `github.create_pr`: draft PR from an existing same-repo
  branch, or bounded UTF-8 files committed on a new `codex/` branch through Git
  trees/commits/refs, under `create_pr`. Files never name local paths; traversal,
  Git metadata and workflow paths are denied. Existing refs are not updated.
- `get_operation` resolves the existing message/action ledgers and confirmed
  publication substeps. Discovery is not authorization.

An operation freezes the current verified invocation. Execution compares it with
current channel context and checks current route/channel authority before writes,
including after token acquisition. A newer invocation blocks an older queued
operation; an Agent can submit a new operation under current authority. Code and
comments are untrusted input; they never become grant configuration.

PR diff reads check the head before and after pagination. Reviews require a full
expected head SHA, exact changed path and valid LEFT/RIGHT diff line. Missing or
truncated patches cannot authorize an inline location. A final head check precedes
a review pinned with `commit_id`. Inline replies validate parent PR, current head
and live position. An upstream push can race the last check and write: association
with the specified commit is guaranteed by the request, but atomic freshness is
not claimed. Approvals, requested changes and multiline comments are unsupported.

With file changes, PR creation verifies the expected base-branch SHA, creates a
new tree and commit, then a new branch and draft PR. Without files it verifies
the existing head-branch SHA. Same-repository routing is fixed server-side; forks,
force pushes, merges, binary files and deletion are unsupported. The Host owns
code execution/testing; CG does not run repository code.

## Completion

Only explicit message tools create comments/reviews; Agent final output is never
sent. Message/action intent is persisted before side effects. Existing claims
prevent concurrent/restart re-execution. Unknown write outcomes are never retried
automatically. Multi-step code publication records confirmed tree/commit/branch
effects without claiming rollback. A later error can leave partial effects even
when PR creation fails. A successful HTTP response must contain its expected
provider receipt; invalid/lost receipts are unknown. Rate-limited writes fail
before attempting an external write when a cooldown is already known.

Revocation cannot interrupt a request already in flight or retract Host history.
Storage retains credentials and raw packets privately without at-rest encryption;
execution environments must not mount CG's state directory. GitHub Enterprise,
App creation/installation UI, OAuth user refresh, general repository search,
checkout and live acceptance are outside this implementation.
