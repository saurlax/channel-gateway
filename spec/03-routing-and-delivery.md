# Routing and delivery

This document owns route authorization, durable delivery and reply lifecycles.
[Session context and memory](09-session-context-and-memory.md) owns the implemented
session keepalive, environment selection, incremental context and memory contract.

## Optional wake decisions and scoped context

An a13n route may select optional `wake_agent_id`, a published Agent on that
route's Host. Null (the default) delivers every authorized message. Generic MCP
Hosts reject this setting. After source and sender authorization, CG asks the
selected Agent to classify the current event and at most eight authorized source
history messages (current text: 8000 characters; each prior text: 800). Media is
represented by presence. Bot mentions follow the same decision path.

Each decision uses a fresh Host Thread, independent from destination session,
environment and memory bindings. Session memory is disabled and initial standalone
mounts are empty. The Host resolves the Agent revision and retains authority over
its configured standalone memory, tools and subagents. CG does not grant route credentials or an origin handle to the check. Administrators
should select an Agent without side-effect tools. The result must be a JSON object
(or JSON string) containing a boolean `process`; additional fields are ignored.
Decision final output is consumed only for gating, never published to channels.

Requests, receipts, results and deadlines persist under a stable event/Host-endpoint/
decision-Agent key before destination submission. Lost receipts retry the same body
and key; restart resumes the same check. Deferred inputs resolve their assigned
run through the inbox entry. A check has one persisted 60-second deadline covering
submission and result polling, with at most eight background tasks. Terminal failures, unavailable Agents, malformed results and
timeouts process normally with sanitized diagnostics. Timeouts do not cancel Host
work. Shutdown cancels local tasks; durable checks remain resumable.

Negative decisions set the event to `filtered`, retain history and create no
destination input. Frozen destination requests never reclassify. Binding changes
require a new decision for queued input; settled historical events stay settled.
Authority is rechecked before freezing destination input. Legacy model-provider
APIs and console pages are removed, stored provider secrets and assignments are
retired at startup, and old routes default to full delivery until configured.

New a13n routes enable `conversation_memory` except for fixed shared destinations,
which require explicit `shared_memory`. New Sessions select a scoped writable
Memory Space and optional `global_memory_space_ids` with read-only access, while
inspecting standalone defaults on the root and pinned named-child revisions.
Writable standalone defaults and unpinned named-child revisions are rejected;
`memories: []` alone does not suppress defaults. The audited root revision is
selected explicitly on newly frozen submissions. Enabling or
changing selection starts a compatible binding rather than modifying settled
Session memory. Host selection failures never fall back to global defaults.

Task coordination belongs to the Host's Agent prompts, Skills and tools. CG adds
no delegation/coordinator instructions to newly prepared inputs. The retired
`coordinate_tasks` setting is rejected on management writes and removed from
stored routes at startup; frozen submissions retain their exact original bodies.
Idle rollover still checks root work, descendants and pending/assigned inputs,
independently of prompts. Incomplete observation fails retryably rather than
establishing idleness. Memory and lifecycle controls remain a13n-only.

See [session configuration](../docs/session-context.md) for configuration and limitations.

## Conversation routes

A configured route binds a nonempty `sources` list to one Agent Host. Each source
contains `channel_id`, `conversation_kind` (`private`, `group`, `guild`,
`guild_dm`, `channel`, `thread`, or `*`), and
`conversation_id`. `channel_id: "*"` includes current and future channel accounts;
`conversation_id: "*"` matches all conversations of that kind. A source with both
`conversation_kind: "*"` and `conversation_id: "*"` matches all supported
conversation types, including on future accounts selected by `channel_id: "*"`.
A wildcard kind requires a wildcard conversation ID. The console labels this
scope **All conversations** and labels private-only scope **All private chats**.
Existing private-only sources remain private-only until explicitly edited.
Private user IDs
and group IDs are distinct, literal platform identifiers, not linked identities.
Each source selects a conversation, not an individual sender within a group.
The console treats these sources as the complete activation filter. Legacy API
sender policies can further limit activation, but readable channel grants in
Connections are independent of activation and never create source bindings.
Source lists contain at most 100 rows; empty and duplicate lists are rejected.

An `a13n` Host requires an Agent ID; the console selects it from published,
unarchived agents on that Host. A generic `mcp` Host has no upstream agent ID
or dedicated a13n MCP binding. Sources, readable channels, name, Host, Agent, and reply policy
can be edited through management. Host/Agent and execution-policy changes are
refused while an input is queued or submitted. The
[authorization contract](02-identity-and-access.md) owns sender and tool authority.

Selection prefers a specific user/group over all conversations, then a specific
channel over all channels, then a specific conversation kind over all kinds.
Equal-specificity overlaps between configured routes
are rejected atomically, including disabled routes. A disabled or sender-denying
specific rule never falls back to a broader rule. Each account/kind/conversation
has at most one live concrete binding and cannot fan out to multiple Agents.

Source-list configurations have concrete child bindings for each matched account
and conversation. Children keep separate source identities and inherit current access and
reply policy. A source removal, more-specific replacement, or Host/Agent change
archives obsolete children, blocks their pending inputs/replies, and removes
their private reply contexts. History stays available to administrators but is
not inherited by replacement MCP bindings. No old inputs are replayed.

The legacy single-source API and stored routes remain readable and editable.
Opening or saving an unchanged source does not rewrite a legacy binding. When a
legacy exact route is expanded, its configuration ID and token remain stable;
its thread and history move atomically to a concrete child if that source and
Agent still apply. Other children start fresh. Existing sender restrictions are
preserved rather than broadened during conversion.

For an `a13n` Host, `thread_policy` chooses delivery context:

| Policy | Thread scope |
| --- | --- |
| `conversation` (default) | One thread for each concrete channel account/kind/conversation |
| `sender` | One thread per verified sender within each concrete conversation; the same sender in another group or account is independent |
| `fixed` | All sources of the configured route share one thread; select `fixed_thread_id` or create it on the first submitted message |
| `message` | Each incoming message creates a fresh thread and session |

An existing fixed thread must be accessible and unarchived on the selected Host
when saved. The console offers existing threads and an explicit create-on-first-message
choice. `fixed_thread_id` is rejected for other policies; generic MCP Hosts accept
only the default policy and no fixed thread. New managed a13n routes default to
`session_idle_minutes: 60` and `environment_scope: conversation`, except fixed
routes default to Session environments. `session_idle_minutes: null` retains a
Thread indefinitely; zero rolls over whenever idle. Positive values specify the
idle timeout in minutes without a gateway-imposed maximum. Message scope always starts a fresh Session. Pinned existing
Threads require indefinite retention and keep their existing memory/environment
selection; attempts to replace those selections are rejected. Cross-conversation
fixed bindings reject conversation-scoped environments.

Upgrade maps stored `window` routes to conversation scope, zero-minute idle
rollover and conversation environments. Existing conversation/sender/fixed
bindings retain indefinite lifetime and their settled mounts; message bindings
retain independent environments. Frozen submissions keep their original requests.
Generic MCP routes retain conversation scope and no Host execution controls.
Fixed threads intentionally share upstream history across sources; removing a
source cannot retract history already submitted to that thread.

Thread bindings persist across restarts. Before an external request, an event
freezes its binding scope, target and body; transport retries reuse that exact request and
idempotency key, including for `message`. Only the oldest unconfirmed submission
in a destination scope advances. Accepted inputs are polled independently;
the busy-input policy below controls whether they delay new submissions.
Independent sender or per-message threads do not block each other. Fixed delivery
shares its scope across all sources, including other routes explicitly targeting
the same Host/thread. Tool calls explicitly select their authorized source route.
Changes to thread policy or fixed destination are refused during queued/submitted
work, and obsolete children are archived so the next input starts under the new
thread policy. Per-event thread IDs identify the actual delivered thread rather
than a conversation's last thread. This remains single-process scheduling, not a
distributed worker protocol.

Administrator-only `POST /api/v1/routes/{id}/reset-thread` detaches the current
thread bindings of an a13n route and all its concrete children. Conversation
policy resets every source conversation's thread; sender policy resets all
sender threads within those conversations. An automatically created shared fixed
thread is also detached across its parent and all siblings when reset through a
concrete child. The operation atomically detaches bindings without
waiting for queued inputs or running Agents. The next unfrozen input in each
scope creates a fresh thread and session, independently of old thread queues.
Shared environment provisioning retains its independent reconciliation rules.
Frozen requests keep their original destination and idempotency key; their
receipts cannot restore a detached binding. Active runs retain their tool access
and reply context. Route identity, credentials, access, other routes, channel
message history, and upstream threads remain unchanged. No historical input is
replayed. Resetting before the first message is harmless.
The console exposes **Reset conversations** with confirmation on the route edit
page. Confirmation identifies the route-wide scope, including all conversations
and senders. Generic MCP routes are rejected; explicitly pinned fixed-thread
routes require choosing a new shared destination in route settings, and
per-message routes already start fresh for every input.

## Input history, keepalive and environments

`history_limit` is 0–100, default 30, and bounds the initial authorized history
snapshot. The current event is separate. Continuations attach a delta since the
last confirmed context position for that Thread and concrete source, bounded at
100 messages; they do not repeat the initial history window. Zero omits automatic
history. Sender scope retains sender-only history restrictions. Read grants,
media projection and exclusion of unsent/failed/unknown replies remain unchanged.
A separate JSON content part bounds message count rather than tokens.

Context snapshots order incoming records by collection time and outgoing records
by confirmed channel acceptance time, so a send queued before the previous cursor
but confirmed later is not lost. Historical sends without acceptance timestamps
use their stored creation time. A full delta includes a coverage warning and an
authorized history-read reference. MCP/admin history pagination retains its existing
creation-time ordering; recovery starts from the latest gateway page and follows
its cursors. Only a Host receipt advances the context position. Later arrivals
remain outside the triggering event's snapshot.

Within keepalive, new messages retain the coordinator Thread. Active root,
descendant and queued work prevent rollover; idle time is recorded persistently
from confirmed observations. Expiry creates a fresh Thread/Session on the next
trigger, never by a timer alone. The Host owns safe-boundary steering and automatic
token-based compaction. Compaction does not change Session or environment identity.

Snapshot, destination and mount selection are frozen with the Host request and
reused on every transport retry, even if the run completes before a lost receipt
is retried. Existing frozen requests retain their original bodies and destinations.

`environment_scope` selects `global`, `conversation` or `session` independently
of Thread scope. Global shares one mount set for participating routes on the same
Host and Agent; conversation shares per concrete account/kind/conversation across
senders and Session generations; session uses normal Agent provisioning for each
new Session. Shared environment creation is reserved before submission; uncertain
receipts must reconcile before another Session can use that environment. Reuse
reads desired mounts from the originating Host Thread, preserving directories and
permission ceilings, and fails rather than silently replacing missing mounts.
Reset retains global/conversation environment bindings and detaches Session ones.

The detailed rules and compatibility boundaries are in
[Session context and memory](09-session-context-and-memory.md).

## Messages during execution

`busy_policy` applies independently to all a13n conversation scopes:

| Policy | Submission behavior |
| --- | --- |
| `steer` (default) | Submit new input immediately with a13n `delivery: steer`; compatible input joins the active run at its next safe boundary |
| `queue` | Keep new input queued until earlier submitted work in the destination thread finishes, then submit FIFO with `delivery: next_run` |

A steer continues the current run; it does not call the interrupt/cancel API.
The Host owns safe-boundary incorporation, compatibility and waiting/resume
semantics. An idle thread starts a run for either policy. A fresh per-message
thread is independent; queue mode does not impose a global account or Host queue.
Generic MCP Hosts expose no execution control and reject non-default busy policy.

Missing stored policies default to `steer`. Children inherit the current parent's
policy. The policy may change during execution without changing thread identity
or resetting bindings. A change affects only requests whose bodies are not yet
frozen. Previously frozen bodies retain their delivery selection on every transport retry;
a lost acceptance receipt still blocks later submissions in that scope until
resolved. Each cycle reserves capacity for polling accepted work as well as
admitting new requests.

Several steers may be assigned to one native run. Each input tracks its exact inbox
entry and assigned run independently. A completed run completes its accepted inputs;
CG never reads, validates, serializes, forwards or retries the run's final output.
An Agent sends channel messages only through the authorized MCP message tools,
using an explicit concrete `route_id`. No tool call means no channel message.
A final text, empty value, object or legacy reply envelope has no delivery effect.
Every newly prepared Host input includes a `channel_output` context declaring
`send_message` as its output tool and final output as private. Already frozen
requests retain their original bodies.

Administrator-only `GET /api/v1/connections/{id}/host-resources/{resource}` supports
`agents`, `threads` and `memory-spaces` for a configured a13n Host. Memory Space
choices request `enabled=true`, exclude disabled Spaces and project names/IDs
without guidance, files or backing-store details. It uses that Host's server-side
credential, forwards a bounded opaque `cursor` with a page size of 50, and returns
only `{items: [{id, name}], next_cursor}`. Archived resources and unpublished
agents are omitted. Failure returns a safe diagnostic without upstream bodies or
credentials. The browser can search loaded choices and request later pages.

Administrator-only `DELETE /api/v1/routes/{id}` removes an active route and any
concrete children of an all-conversation parent. One transaction blocks pending
inputs and queued replies, clears their private incoming reply contexts, archives
the source identity, policy and thread binding without token hashes, and releases
the account/conversation uniqueness constraint. Removed route tokens no longer
authorize MCP. Historical received messages, observations, replies, and receipts
remain readable through administrator conversation history. Recreating the same
source creates a new route identity and thread binding; it does not replay old
inputs or restore an archived token. In-flight external requests or Agent runs
cannot be retracted. Creating routes or children and updating bindings recheck
resource existence in their storage transaction so deletion cannot be undone by
a stale configuration write.

## All-conversation routes and identity discovery

`conversation_id: "*"` creates an all-conversation route for one channel account
and conversation kind. A specific route takes priority, even when disabled or
its sender policy denies the input; denial never falls back to a broader route.
When an authorized new conversation matches `*`, CG persists a concrete child
route with `parent_route_id`. Each child retains its source identity; the thread
policy above determines its thread and input queue.
Its enabled state and sender, permission, reply policies are rechecked against
its parent at every authorization boundary. Parent edits apply to existing
children; child access cannot be independently edited. The console lists parent
configuration; management API views also expose concrete children and bindings.
The source tuple remains unique and cannot fan out.
MCP listing also materializes authorized previously observed conversations under
an enabled `*` route, without queuing old inputs or creating upstream threads.
Existing exact routes take priority even when disabled or assigned to another
Host; history discovery cannot override that assignment.

Multi-source and wildcard a13n routes use a route-token MCP connection and
explicit concrete route IDs. A dedicated route MCP binding remains optional and
is supported only for one concrete source. Existing exact routes retain their
bindings and legacy sender allowlists. Tool selection always follows the Agent
configuration; a dedicated binding is optional for route-token tool sends.

Verified supported messages without a route appear in management as `unmatched`,
with nullable `route_id`. Denied ingress appears as `blocked`, also without a
route binding. Both are durable administrator observations, deduplicated by the
same account/conversation/source-message identity as delivered inputs. They
never enter the worker queue. MCP can read their text after the source
conversation and sender are authorized by its current route. Adding a route or
provider redelivery does not replay them as Agent work. A new message is required
to trigger execution after configuration.
Earlier messages discarded by older versions cannot be recovered from local
state. Pairing credentials and reply-context tokens are never included in these
observations.

Channel collection precedes routing: every bounded authenticated packet is
retained under the [channel-event journal contract](01-channel-adapters.md#route-independent-event-retention),
including events that cannot be projected to a supported message. Route
selection controls Agent delivery and capability access, never packet retention.

## Input lifecycle

| Durable state | Meaning                                                                                   |
| ------------- | ----------------------------------------------------------------------------------------- |
| `queued`      | Authorized input is persisted; Host acceptance is not confirmed                           |
| `available`   | Input is locally available to an MCP client; no Host receipt or run completion is implied |
| `submitted`   | a13n accepted the input; its run may still be queued, running, or waiting                 |
| `completed`   | The assigned run completed and its output was handled                                     |
| `blocked`     | Current route/source authorization prevents further processing                            |
| `failed`      | Host operations exhausted the failure limit, or entry/run processing ended unsuccessfully |

```mermaid
stateDiagram-v2
    [*] --> queued: Persist authorized input
    queued --> available: MCP client input available
    queued --> submitted: Persist Host receipt
    submitted --> completed: Handle completed run
    queued --> blocked: Permission revoked
    submitted --> blocked: Permission revoked
    queued --> failed: Host request failures
    submitted --> failed: Host request failures or unsuccessful entry/run
    failed --> queued: Retry without entry receipt
    failed --> queued: Retry after confirmed unsuccessful execution
    failed --> submitted: Retry with entry receipt
```

Before the first submission attempt, CG persists the target path and complete
request body. Creation uses `POST /threads`; continuation uses
`POST /threads/{thread_id}/inbox`, with `delivery: steer` by default or
`next_run` for queue mode. The event ID is the
initial `Idempotency-Key`. A lost receipt retries the same target, body, and key,
even when configuration or thread observations change later.

A submission receipt records the thread, inbox entry, and optional run ID.
Without a run ID, CG polls that exact entry for assignment, then polls its run.
Host acceptance does not prove execution completion. Accepted, running, and
waiting runs remain `submitted`; waiting work is resolved in a13n.

Host request/parsing failures increment attempts; five failures mark the event
`failed`. Valid run observations reset that failure counter. Administrator retry
resets attempts. Without an entry receipt it resumes the frozen submission with
the same key. With a receipt it first reads the assigned run, resolving the exact
entry when necessary. A confirmed `failed` or `cancelled` run, or a `cancelled` or
`rejected` entry with no assigned run, reserves a new delivery under the same
source event. It clears the old request and receipt, selects current routing and
Agent configuration, and durably allocates a fresh idempotency key before the new
submission. Repeated transport attempts, including after restart, reuse that new
delivery's frozen target, body and key. Source-event deduplication is unchanged.
Concurrent administrator retries may reserve only one new delivery.
The previous terminal Run remains admission evidence for a retained shared
environment until the replacement receipt arrives. Its initiating-context handle
is retired; the new execution receives a fresh handle.

Active, completed, pending or unrecognized Host outcomes resume polling without
creating another execution. A failed status read leaves the event unchanged;
the administrator receives a safe error. Route/Host changes during the read
refuse the stale retry. A new execution can repeat effects an unsuccessful run
already performed; CG does not resend existing channel replies automatically.
Replacement a13n keys must
retain the same upstream principal/workspace for the upstream idempotency scope
to remain valid.

## Agent output and failure notices

Only `send_message` publishes Agent-authored channel messages. Run completion never
creates a reply, parses `reply`/`text`, checks final-text size or retries output
validation. Agents may keep ordinary text final results for private execution
records; these never enter channel history. Quiet participation means completing
without a message-tool invocation, not producing a special envelope or an empty
model response. A model/execution failure is still a failed input; CG never treats
partial text as a successful message.

For a13n inputs, CG atomically marks a terminal failure and queues one fixed-text
failure notice linked to the triggering event. This covers unsuccessful inbox
assignment, an unsuccessful terminal Run, and exhaustion of the five Host request
attempts. Intermediate request failures and accepted/running/waiting or completed
Runs create no notice. Known categories include rate/quota rejection (HTTP 429),
authorization, upstream availability, unavailable configuration, model recovery
exhaustion and cancellation; unknown failures use a generic diagnostic. Arbitrary
Host error messages, response bodies, partial output, prompts and credentials are
never forwarded. The management event retains its failure state.

The notice goes through the durable reply queue with the original event's source
and current route/sender authorization; official QQ uses that event's saved
passive reply context. Queued notices survive restart. A persistent event marker
prevents another notice after polling, restart or manual input retry. Existing
historical failures are not backfilled. A revoked destination blocks the queued
notice, and an uncertain send stays `unknown` without automatic resend. Channel
acceptance remains subject to platform reply windows and quotas. Generic MCP
Hosts have no observable Run lifecycle and do not generate these notices.

The removed `reply_source` setting is rejected on create/update and removed from
stored route configuration at startup. Already queued event-linked automatic
replies are blocked with `Automatic output delivery retired`; explicitly marked
Host failure notices and tool-created queued
messages keep their delivery state. Historical sent/failed/unknown records remain
readable. Existing OneBot MCP reaction authority is converted into its explicit
`message.react.onebot` grant, which applies only to OneBot; other routes gain no
reaction authority.

## Generic MCP delivery

A `mcp` Host has no API endpoint or upstream key. Its routes require
no a13n agent/MCP-connection bindings. The worker
reauthorizes each queued event and marks it `available` without external I/O,
thread creation, or run tracking. Subsequent queued events can advance once the
oldest event is available. Reads do not acknowledge, consume, or complete events;
clients choose their own polling and deduplication strategy using source message
identities. Available history is always filtered by current access at tool time.

No MCP notification, background client wakeup, or autonomous client run is
promised. Clients request replies through MCP under the current route authorization rules.
The gateway cannot infer that a client has finished processing a message.

## Reply lifecycle

| Durable state | Meaning                                                                |
| ------------- | ---------------------------------------------------------------------- |
| `queued`      | Text awaits automatic sending after current authorization checks       |
| `sending`     | CG claimed the reply and may have issued the external request          |
| `sent`        | A confirmed channel acceptance receipt is persisted                    |
| `blocked`     | Route or linked-source authorization prevents sending                  |
| `failed`      | QQ explicitly rejected the operation, including an HTTP rate rejection |
| `unknown`     | Delivery may have happened, but CG has no confirmed successful outcome |

Explicit MCP message requests and the failure notices above create queued replies for
private, group, guild-channel, and guild-DM conversations. There is no approve/reject API or manual review
step. Agent tool configuration is managed in the Host.
The worker rechecks route/source access, claims a queued reply, and issues one
channel request. Disabling the route or revoking a linked input's sender blocks
pending replies before sending.

OneBot and QQ receipts contain platform message IDs; Weixin acceptance receipts
use CG's client ID because the provider returns no platform message ID. `sent`
does not prove recipient delivery/reading. Offline OneBot sockets and unpaired
Weixin accounts leave replies `queued` until ready. Missing authorized native
reply context becomes `blocked` before any send request; native platform reply
windows still apply. [Channel adapters](01-channel-adapters.md) owns these protocol
constraints and private context handling.

A timeout, malformed/unconfirmed receipt, or interrupted sending request produces
`unknown`; the baseline conservatively does not assume that such an operation is
safe to repeat. On restart, persisted `sending` replies become `unknown`. Unknown
replies are never automatically retransmitted, and no manual resend operation is
defined. Verify the channel before separately deciding whether another message is
necessary.
An explicit QQ rejection (including HTTP 429 or a nonzero native error code)
produces `failed` and is not automatically retried. Local proactive rate limits
leave unattempted requests `queued`, independently of these failure states.

For existing databases, startup converts previously approved replies to `queued`
and retires unreviewed drafts as `blocked` with a diagnostic; old drafts are never
silently sent. Historical rejected, sent, blocked, and unknown records remain
unchanged. Stored `auto_reply` settings are removed without changing sender access
permissions. New route inputs and updates reject that removed
field. The console includes outgoing records in message conversation history, without
reply editing, decisions, or a separate reply page.

Input deduplication and Host idempotency do not imply exactly-once channel replies.
Channel sending provides no external idempotency guarantee in this baseline.
Queued replies, thread binding, and confirmed receipts survive restart without
resending completed work.

## Administrator conversation history

`GET /api/v1/history` requires an administrator session and the exact
`channel_id`, `conversation_kind`, and `conversation_id`. It merges accepted
incoming events, unmatched/blocked observations, and replies for that source
account and concrete conversation. Incoming records have direction `incoming`;
replies have direction `outgoing`. Parent all-conversation routes are not history
destinations: Messages lists concrete conversations separately.

Records expose ID, route ID, text, sender ID for incoming messages, delivery state,
error, creation time, and any thread/run identifiers. They expose no credentials,
private reply contexts, or frozen Host request bodies. Legacy incoming events
without an address use their retained route's source identity. This is an
administrator history API, independent of MCP capabilities. MCP conversation
reads expose the authorized message subset defined by the
[Agent tool contract](04-agent-tools.md), excluding management diagnostics.

History uses persistence creation time and ID for deterministic chronological
ordering. Each request reads the newest 100 records by default, with `limit`
bounded to 1–100, and returns `messages` in ascending order plus `has_more`.
Earlier pages require both `before_created_at` and `before_id` from the oldest
record of the current page; the cursor uses an exclusive comparison. The console
can load earlier messages and refresh the latest page without duplicating entries.
The conversation overview combines recent activity with enabled concrete routes,
so older route histories remain accessible. Conversation history opens only from
Messages, on the standalone `/events/conversation` page with exact source query
parameters. Incoming entries align left and Agent replies align right, with
avatars aligned to the top. Incoming headers show sender IDs without direction
labels; replies omit sender headers and repeated connection IDs. Direct
links and reloads retain the exact account and conversation, including sources
outside the overview's recent records. Loading earlier messages preserves scroll
position; refresh follows new entries only when already near the bottom.
Route rows expose one Edit action with token rotation and enable/disable
settings on its dedicated edit page. Failed incoming deliveries retain their administrator
retry action in conversation history.

## GitHub conversations

GitHub uses `thread:REPOSITORY_ID:NUMBER` and the existing Host workflow and durable ledgers. The [GitHub contract](08-github.md) owns callback/notification deduplication, invocation snapshots, review freshness and partial PR publication effects.
