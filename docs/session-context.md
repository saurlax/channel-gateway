# Wake decisions and conversation context

Wake decisions are optional, per-route Agent calls on the selected a13n Host.
CG has no model-provider configuration or direct model API credentials.

## Select a wake decision Agent

Open **Routes → Edit → Session → Wake decision Agent**. Leave **Process every
message** selected to deliver every authorized message. Otherwise select a
published Agent from the route's Host. The decision applies to every authorized
message, including bot mentions and messages received during execution.
Changing the Host clears the console selection.

The decision Agent receives the current message and up to eight recent authorized
messages from that source conversation. Current text is capped at 8000 characters;
each prior text is capped at 800. Media is represented by presence only. CG asks
for `{"process": true}` or `{"process": false}`; additional output fields are
ignored, but `process` must be a boolean. Configure this Agent in
a13n for classification, with no messaging tools or side effects. CG inherits
its configured capabilities; the classification instruction is not a tool sandbox.

Each check uses a separate fresh Thread with session memory disabled and no
conversation memory, shared environment binding, route token or origin handle.
The Host resolves the Agent revision and keeps its configured standalone memory,
tools and subagents. CG does not apply the destination's managed-memory audit to
the decision Agent. Destination settings remain independent.

A false decision marks the incoming event `filtered`. It stays in history and may
appear in later context. A true decision allows normal destination delivery. Only
explicit Agent message tools publish replies; decision final output is consumed
only as a private gate and is never sent to a channel.

Unavailable Agents, failed/cancelled runs, malformed output and timeouts fall back
to normal delivery. Events expose `wake_agent_id`, `wake_process` and sanitized
`wake_error` diagnostics. Checks run in at most eight background tasks, with a
single 60-second persisted deadline covering submission and result polling. Polling
and sends remain independent. A timeout does not cancel the Host run, so keep the decision Agent free of side effects.

CG persists each frozen request, deadline and receipt with a separate stable
idempotency key. Lost receipts retry the same request; restart resumes accepted
work rather than creating another Thread. A frozen destination request never
reclassifies. Decisions are scoped to the event, Host endpoint and decision Agent;
changes to that binding require another check for still-queued input. Route and
sender authority are checked before delivery.

On upgrade, old model-provider records and route assignments are removed.
Existing routes process all authorized input until a decision Agent is selected.
Historical filtered events stay filtered; already-frozen delivery stays unchanged.
Old database backups may still contain retired model credentials.

## Input presentation

CG submits the current message, bounded channel history and source metadata
together as one contextual input. Images retain their media parts. Videos, audio
and files are lightweight attachment references; use `read` to obtain their source
URLs or contents when needed. They are not eagerly downloaded by the Host before
the Agent can respond.
Large snapshots use one structured input rather than dropping history or adding
separate supplemental instructions. Messages arriving during execution still
follow **Messages during execution**; Host tool-completion notifications are
independent of initial input packaging.

The first input for a source in a Thread includes brief setup instructions. While
the Thread is retained, subsequent inputs carry only current message identifiers,
media references and history collected since the previous accepted input. Setup,
memory policy and execution provenance are not repeated. A fresh Thread after
idle rollover or reset receives setup again. Subagent Run/Session identifiers
come from the Host's delegation tools, not the channel message envelope.

## Keep memory across sessions

In **Routes → Memory**, **Isolate conversation memory** is independent of conversation
continuity and enabled by default for new non-fixed a13n routes. CG creates and
retains a writable Memory Space per source/Agent binding; sender scope includes
the sender, and per-message Sessions reuse the concrete conversation's Space.
Optional **Global Memory Spaces** is a searchable multi-select of enabled Spaces
on the selected Host, showing names and read-only access. Load more pages or retry
a failed catalog request without losing selection. Selected Spaces mount under
`global-1`, `global-2`, etc. with read-only access. The scoped writable mount is
named `conversation`. Fixed scope requires explicit **Shared memory across
sources** and uses one route-scoped Space without merging existing source memory.

New Sessions explicitly choose these Spaces; continuations keep their settled
selection. Empty initial standalone mounts do not suppress Agent defaults. CG
inspects the published root revision and all pinned named-child revisions, refusing
writable standalone defaults or unpinned named children. It selects the audited
root revision in each newly frozen managed input, without editing Agent resources. Disabling managed memory retains the
Agent's memory selection and its write permissions. Conversations using the same
Agent-selected Space can search each other's memories; disabling isolation does
not itself select or merge Spaces. Memory changes require idle
inputs and a compatible new binding. Pinned existing Threads keep their existing
selection. CG never edits or republishes the Agent to change its tools.

This requires Host Memory Space APIs, new-thread `session.memory` and explicit
`memories` selection. The upstream API key needs permission to create/read Spaces and run
with them. Unsupported selection fails retryably rather than falling back to
shared defaults. Lost Space creations are recovered by their `cgw-scope` label.
Reset and rollover retain scoped memory. Route deletion does not delete Host
Spaces; existing global contents are neither migrated nor cleaned automatically.

Memory guidance keeps temporary roleplay/persona changes in Session notes. Only
explicit lasting local agreements belong in the conversation Space. Its
`global/MEMORY.md` is local to that Space; it is not Agent-wide global memory.
Retain compact source, deliverable and unfinished-work references. Writing useful
memory remains Agent behavior. Host Space inheritance constrains descendants;
other configured capabilities and shared filesystem access remain authoritative.

## Configure long work in the Host

Configure delegation, progress tracking and subagent-session inspection in the
a13n Agent's instructions or Skills. CG does not inject task-coordination
instructions or expose a coordination switch. It does not publish Agent revisions,
change tool selections or configure subagent environment edges.

Configure asynchronous subagents in the Host beforehand. Their environment edges
remain authoritative: `shared` reuses the parent's mounts; `dedicated` creates a
separate environment. CG preserves permission ceilings when mounting selected
reused environments in a new Session.

Before idle rollover, CG checks the coordinator's current Run, active descendant
Threads and pending inbox entries. Active child work keeps input directed to the
same coordinator even after its root Run ends. Incomplete observation is an error,
not evidence of completion. This lifecycle check is independent of Agent prompts.

A Host-owned coordination prompt can retain child-session references in scoped
memory and inspect them on follow-ups, subject to Host permissions across Session
generations. CG transports source/history/memory context and authorized messages;
it does not interpret business task state or manage arbitrary external jobs.
