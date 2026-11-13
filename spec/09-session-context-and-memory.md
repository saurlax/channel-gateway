# Session context and memory

## Status and ownership

This is the accepted target contract from the November 9, 2026 design discussion.
The implementation now includes four conversation scopes, persistent keepalive,
independent environment selection, scoped/read-only-global Memory Space assembly,
incremental history, initiating-context handles for queued message provenance,
and Host-owned child-session coordination. Validation uses simulated Host/channel
endpoints; live Host installation acceptance has not been performed.
The [delivery contract](03-routing-and-delivery.md) owns the durable workflow.
This document owns the replacement session/context behavior; the existing
[access](02-identity-and-access.md) and [tools](04-agent-tools.md) contracts retain
ownership of credentials, source authorization and channel operations.

The previous `window` policy combined Thread scope, fresh-on-idle lifetime and
conversation environment reuse. Its stored configuration migrates without
changing its effective lifetime; new configuration exposes those dimensions
separately. Historical sends remain unattributed when no initiating context was
captured. Stream fragments use their existing action ledger; the queued-message
provenance projection does not claim stream-fragment history support.

## Independent configuration dimensions

The console presents exactly four conversation scopes:

| Scope | Binding |
| --- | --- |
| Conversation | One coordinator binding per channel account, conversation kind and conversation ID |
| Sender | One binding per verified sender within that concrete conversation |
| Fixed | One explicitly shared binding across the route's sources, optionally pinned to an existing Host Thread |
| Message | A fresh Thread/Session for every triggering message |

Scope chooses which inputs share a binding; it does not choose memory, history
assembly or environment lifetime. Configure those independently:

| Dimension | Target behavior |
| --- | --- |
| Session lifetime | Retain indefinitely, or roll over after a configured idle period; new routes default to 60 minutes |
| Initial history | Attach a bounded source-history window when starting a Session; default 30 messages, existing 0–100 range |
| Environment scope | Global shared, per channel conversation (default for new routes), or per Session; independent of conversation scope and lifetime |
| Memory | Select global and scoped memory with separate access permissions |
| Messages during execution | Preserve independent `steer` and `queue` choices |

The per-message scope always creates a fresh Session regardless of lifetime, but
can still reuse scoped memory and select any supported environment scope. Sender
scope does not implicitly isolate environments by sender. Fixed scope shares
Host history, but does not by itself select a global environment. No conversation
or environment scope links sender identities across accounts.

A fixed binding created by CG can roll over within its shared scope. An explicitly
pinned existing Thread cannot be silently replaced: configuration must select
indefinite retention or reject the incompatible rollover setting. Reset detaches
Thread bindings and history cursors while retaining scoped memory. Environment
retention follows the independently selected scope below.

Generic MCP clients retain ownership of their execution lifecycle. These Host
session controls apply only where the Host supports them.

## Console grouping

Route forms have five tabs: Basic, Routing, Session, Memory, and Permissions and
connections. Basic contains name, enabled state and a Danger zone with Delete
route. Routing
contains sources and the Host/Agent destination. Session contains the optional
wake decision Agent and separates
Context controls (scope, fixed Thread, keepalive, history and busy policy) from
Execution environment. Memory contains scoped/global memory selection and
conditional fixed-source sharing confirmation. Permissions and connections
contains account reads, platform grants and an optional dedicated MCP binding.

Generic MCP Hosts omit Session and Memory. Per-message scope hides idle timeout;
fixed Thread and shared-memory controls appear only when relevant. Tab switches
preserve the whole unsaved form, and validation selects the field's owning tab.
The bottom row contains only Save and Cancel, outside the panels, operating on
the whole form. Delete route lives in Basic's Danger zone; Reset conversations
lives in Session management; Rotate token lives in Permissions and connections'
Route token section. They retain their existing authorization and confirmation
behavior. Token rotation identifies revocation of the previous credential and
the need to update clients.

## Environment assembly and lifetime

Environment scope selects the execution workspace, not the model transcript or
memory permissions. The administrator chooses it independently of the four
conversation scopes, keepalive and history settings:

| Environment scope | Assembly and retention |
| --- | --- |
| Global shared | Mount the same environment set across participating channel conversations and Sessions, including across participating routes |
| Channel conversation | Mount one environment set per channel account, conversation kind and conversation ID, across senders and Session generations |
| Session | Provision an independent environment set for each new coordinator Session; later Runs within that Session keep it |

Global sharing is explicit and bounded to the selected Host/workspace and Agent
configuration. Participating routes select the same shared environment binding;
unrelated Hosts or Agents are not automatically pooled. Channel-conversation
bindings likewise include Host/workspace and Agent configuration. A channel
conversation means one concrete group, private chat or platform conversation,
not every conversation received by a bot account.

For channel-conversation scope, sender Threads in the same group share its
environment. For global scope, otherwise isolated chat Sessions share files and
environment state. For Session scope, per-message delivery provisions a new
environment for every message; a retained 60-minute chat Session reuses its
environment until session rollover. Ordinary context deltas and Host compaction
do not create a new environment. Channel-conversation and global environments
survive rollover and conversation reset. A Session reset selects a new environment
only under Session scope. Detaching never implies destroying an upstream
environment that another Session may still use.

Use the Host's normal environment provisioning policy on first creation, then
persist the selected mounts for the environment binding. Reuse preserves names,
environment IDs, working directories and permission ceilings. Restart and retry
must not create a second environment after an uncertain creation; reconcile the
Host-accepted creation before advancing later submissions on that binding.
Missing, destroyed or incompatible environments produce an explicit failure,
not silent replacement with an empty workspace.

One Host Session has one settled environment selection. A fixed Session spanning
multiple channel conversations cannot switch environment sets on each input.
Reject per-conversation selection for that combination unless the Host can
enforce the requested behavior; never silently substitute global sharing. An
existing pinned Thread retains its existing selection; configuring scoped memory
or a global/conversation environment replacement for it is rejected.
Changing environment scope applies to a compatible new Session after pending
work settles; it does not remount a running coordinator or its children, copy
files, or destroy old environments.

Shared environments expose their files to every participating execution, even
when model history and memory remain separate. The console must identify this
scope explicitly; channel read grants cannot undo file access already granted
through a shared mount. Sharing is an administrator-selected access boundary,
not strict conversation isolation. Concurrent work uses separate task directories
and the Host's configured permissions; a shared environment does not imply a
global execution queue or protection from concurrent file changes.

Subagent environment inheritance remains controlled by the Host's configured
shared/dedicated edges. CG chooses the coordinator's mounts and does not override
those edges. Durable memory and deliverable references must not assume that
environment files survive Host destruction or that mounting files restores model
history. New environment scope settings add no gateway-managed task directories
or Task/Ticket abstraction.

## Session keepalive and active work

A model turn or root Run ending does not end the conversation Session. On an
authorized triggering message, continue the retained Thread if it has active
work or has not reached its configured idle timeout. An idle continuation starts
another Run in the same Thread; active input follows the busy policy.

For rollover, active work includes the root Run, pending or assigned coordinator
inbox entries, and active descendant Threads belonging to that coordinator.
Observe these through Host public APIs. An incomplete observation fails delivery
retryably rather than establishing idleness. Do not inspect unrelated roots or
infer the status of arbitrary external jobs.

The idle clock starts when the scope becomes idle, using the later of its latest
Host-accepted triggering input and latest observed transition to no active work.
Persist this state across restarts. Filtered chatter, history reads, operation
polls and duplicate deliveries do not renew keepalive. Observe active work before
expiring a binding; crossing the timeout during execution never splits it. If a
restart leaves the transition time unknown, begin idle retention at the first
confirmed idle observation instead of assuming work ended earlier.

After the timeout, the next triggering message creates a fresh Thread/Session
with initial history and selected memory/environment settings. No timer starts
Agent work by itself. Admission remains serialized within the binding scope.
Freeze the chosen generation, target, complete payload, context cursor and mounts
before submission; retries keep that exact request and idempotency key even if
the timeout, work state or configuration subsequently changes.

## Incremental context assembly

For each accepted input, submit the current message, bounded history, structured
message data, verified initiating-context handle and any coverage reference as
one contextual content part. Within that part, distinguish the current message
from untrusted historical and structured data. Use one text part within the Host's
65,536-character limit, or one JSON part for a larger complete snapshot; never
split context into supplemental instructions or silently truncate it. Media keeps
its native URL parts for the first four QQ or OneBot images. Additional images
and non-image attachments use message/index references and are fetched only on
demand. This changes input presentation, not the independent
steer/queue policy or Host-owned tool-completion notifications. Frozen submissions
keep their original representation and idempotency key across upgrades and retries.
Retain existing chronological ordering, media projection, exact
source scope and current read checks. A message-count bound is not a token bound
or proof that the supplied history is complete.

Only the first gateway input accepted for a concrete source in a Thread includes
the brief bootstrap instructions and static channel/account/kind identifiers.
Continuations retain the current route, conversation, sender, message and opaque
initiating-context handle, with current media references and newly collected
history only. Empty history and media fields are omitted. Fixed shared Threads
retain account/channel/kind identifiers on every source message. Existing pinned
Threads receive the bootstrap on their first CG input; reset and idle rollover
start a new bootstrap. Host-owned memory guidance and delegation tools supply
memory policy and child execution identifiers rather than repeating them in each
message. Automatic history omits ledger IDs and verbose Run/Thread/Session
provenance; full provenance remains available through authorized MCP history reads.

For continuation, attach only authorized channel records not previously accepted
into that Session's context. This includes intervening collected messages and
confirmed bot sends that the Host would otherwise not know about. Do not append
the same recent 30 messages on every input. Use stable gateway record identities
to distinguish the current trigger, earlier delivered triggers, history and sends.
Only a confirmed Host receipt advances the accepted context cursor; an uncertain
submission preserves its frozen snapshot. Independent sender bindings keep
independent cursors and retain sender-scoped history restrictions.

Keep context progress per Session and concrete source even under fixed scope.
A send's tool result is not proof that its final channel acceptance or content
already appears in every affected Session. Deduplicate known facts using stable
operation/message identifiers, not by comparing text.

If a delta exceeds the assembly budget, include an explicit coverage gap and an
authorized pagination reference rather than representing a tail window as the
whole interval. A cursor advanced past omitted records retains that gap and its recovery
reference in the accepted Host payload. Recovery starts at the latest gateway
history page and follows its creation-time cursors because send acceptance can
postdate creation. Agents can use existing authorized history reads for
older context. Missing read authority omits content and recovery access; it never
expands the grant. Later arrivals remain outside the frozen event snapshot.

## Global, conversation and session memory

Memory has three logical lifetimes:

| Layer | Contents and default authority |
| --- | --- |
| Global | Administrator-confirmed Agent identity, standing rules and reusable cross-conversation knowledge; read-only to channel executions |
| Conversation | Durable agreements, preferences, decisions, deliverable references and unfinished-work references for one scoped binding; writable there |
| Session | Temporary roleplay, working notes and execution details; local to the current Session |

New managed routes default to scoped writable memory and do not inherit writable
global defaults. A request to act as a character, such as a catgirl, applies to
the current Session unless explicitly established as a lasting local agreement;
even then it belongs only in that conversation's memory. It does not modify the
Agent's global identity or other conversations. Writing global memory requires
separate administrator-controlled authority that an ordinary channel message
cannot grant. No new per-reply review flow is introduced.

Conversation-memory identity includes route ownership, Host, Agent, account,
conversation kind and conversation ID. Sender scope additionally includes the
sender. Per-message Sessions reuse their concrete conversation's memory. Reset
and session rollover retain memory; route replacement does not adopt another
binding's memory. Existing global contents are not automatically copied, cleaned
or migrated into conversation memories.

Fixed scope explicitly combines source context. It must disclose its shared
memory scope and require an administrator's explicit selection of shared writable
memory before combining previously isolated conversation memories. Do not silently
merge existing Spaces. Source-specific retrieval inside a shared Session does not
provide strict isolation after that Session has read the content.

Enforce access through Host memory selection and mount permissions, including
standalone mounts and child inheritance. Empty initial standalone mounts do not
suppress Agent defaults. CG audits the published root revision and the pinned
revision graph of named children, following each revision once and accepting
additional Host configuration fields. It permits only read-only standalone
defaults and explicitly selects the audited root
revision. Unpinned named-child defaults are rejected because they could change
between inspection and execution. Agent definitions and tool selections are not
rewritten. A prompt or a directory called `global/` is not an access boundary.
Distinguish the Agent-wide global layer from a Host Space's internal `global/MEMORY.md` path in instructions. A Host incapable of
enforcing the requested selection/access must report incompatibility, not silently
fall back to writable shared memory. Existing pinned Threads must satisfy the
requested effective memory policy before adoption.

Global Memory Space choices use the selected Host's enabled Space catalog through
administrator-only resource discovery. The console shows a searchable multi-select
with names, read-only markers, pagination and retry; changing Hosts clears the
selection. Existing selections outside the loaded page remain removable without
being silently dropped. Catalog responses expose only IDs/names and page cursors.

Memory writes remain Agent behavior rather than an automatic extraction or
completion guarantee. Store compact indexes with source and child-session
references; do not use memory as an unbounded transcript or credential store.

## Cross-conversation message provenance

A cross-conversation send records both its authorized destination and its
verified initiating context: source route/conversation, triggering event and
sender when available, Host Thread/Session/Run identifiers when available, and
the durable send operation. Confirmed acceptance adds the destination message
identifier where the platform supplies one. A gateway failure notice remains a
distinct gateway-authored operation under the existing delivery contract.

Derive execution provenance from authenticated Host context or an opaque
gateway-issued context handle bound to the initiating event and caller. Raw
Agent-supplied source IDs are claims, not verified attribution. A route credential alone does not identify which of its Sessions initiated a
tool call. Host-wide MCP credentials are retired; origin handles must be scoped
to the same authorized route configuration as the destination.
Clients without verified initiating context remain supported, with provenance
explicitly unavailable; never guess the latest event or fabricate a requester.

Destination continuation includes the bot's confirmed sends and authorized causal
references even when another Session performed the send. This lets the Agent
answer a recipient's follow-up without depending on the initiating Session's
model history. Explicit reply-to linkage takes precedence; if an unthreaded
follow-up has several possible antecedents, expose those candidates without
inventing a causal match. Queued, failed and unknown operations are not described
as successfully sent.

Authorization of destination history does not grant access to a private source.
Project source identity, requester and reason only when current source-read
authority allows them; otherwise show the destination-visible send with its
restricted or unavailable provenance. Internal attribution never automatically
authorizes disclosure to the recipient. Recheck source and destination authority
on retrieval and before sending. Provenance does not itself grant activation,
reply permission, cross-channel identity equivalence or native reply credentials.

Do not merge all channel Sessions to recover send provenance. A destination send
alone creates no extra Agent run; the next authorized trigger can assemble it.

## Agent coordination through existing subagent Sessions

Keep long-work coordination in the Agent and Host. CG introduces no Task/Ticket
resource, work scheduler, subscription engine or business completion state.

CG does not inject delegation, task tracking or child-inspection instructions into
new Host inputs. Configure those behaviors in the a13n Agent's own prompts or
Skills. CG supplies verified source metadata, authorized history, memory scope and
communication tools, and observes active child work only to decide Session
retention. Business task state remains outside CG.

The Host-owned instructions may ask an Agent to retain a compact source/goal/child
Session index, query actual child state on progress questions and corrections,
and forward relevant instructions through existing authorized Host tools. A saved
state is a hint, not live truth. Querying a Session does not itself resume, steer
or cancel it. An ID grants no access across coordinator generations; Host tools
and permissions must support that inspection. An inaccessible child is not proof
of completion or permission to duplicate work.

The retired `coordinate_tasks` configuration is removed from stored route and
archive values and rejected on management writes. Already frozen submissions keep
their original payload for idempotency. New submissions never regain coordinator
instructions from stale stored settings. This change does not disable descendant
activity checks before idle rollover or rewrite Agent revisions/tool selections.

## Compatibility and release gates

Preserve existing route IDs, credentials, grants, pending work and frozen requests.
Replace legacy `window` with conversation scope, idle rollover of zero, bounded
initial history and channel-conversation environments, preserving its existing fresh-on-idle
behavior. New routes default to 60 minutes; existing conversation/sender/fixed
bindings retain indefinite lifetime unless explicitly changed. Per-message
behavior remains fresh on every input. Do not repeat the old migration that
silently converted continuing conversation bindings into window delivery.
For other existing policies, preserve their effective environment selection:
continuing bindings keep their settled mounts, fixed bindings retain their shared
mounts and per-message bindings retain independent provisioning. Do not apply the
new channel-conversation default retroactively or copy environments during migration.

Existing memory selections retain their effective policy until an explicit
administrator update. Expose inherited writable global memory clearly; changing
to isolated memory starts a compatible new binding rather than steering into an
already settled incompatible Session. Historical sends without provenance remain
unattributed. Migration cannot rewrite already accepted Host history or erase
memory contamination from earlier executions.

Implementation must update the owning baseline contracts, management schemas,
console localization and user instructions together. Required acceptance cases:

- Exactly four scope choices, with history, environment and memory settings
  available independently; unsupported combinations fail explicitly.
- Global environments shared across participating routes, channel-conversation
  environments shared across that conversation's senders and Session generations,
  and Session environments replaced only when a new Session starts. Verify reset,
  restart, uncertain creation and fixed/pinned compatibility for each scope.
- Continuation within 60 minutes, rollover at expiry, and retention during root,
  descendant and queued work, including restart and busy-policy behavior.
- Frozen-request retry across expiry without changing destination or duplicating
  assembled history; concurrent sources and sender scopes retain separate cursors.
- Recovery of intervening history with bounded deltas, explicit gaps and current
  authorization checks; failed or unknown sends never appear as confirmed sends.
- Session-local roleplay never gains global write authority, conversation memory
  stays isolated, and child/standalone mounts cannot bypass the selected policy.
- A send initiated in A and confirmed in B is understood on B's follow-up, with
  attribution protected when source authority is absent or revoked.
- New inputs contain no task-coordination guidance, retired route settings are
  removed without rewriting frozen inputs, and active descendants still prevent
  idle rollover. Subagent inspection behavior belongs to Host acceptance.
- Legacy window migration, existing pinned Threads, reset and memory retention
  preserve their declared behavior without rewriting frozen submissions.

Tests exercise the implemented behavior and failure boundaries. Natural-language
memory maintenance and choosing/inspecting the correct subagent remain Agent
behavior; passing gateway tests does not prove those model behaviors or live Host
compatibility.
