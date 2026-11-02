# Model decisions and conversation context

CG can classify incoming messages before submitting them to an a13n Host. This
is optional and configured per route. Existing routes keep processing every
authorized message until an administrator selects a provider.

## Configure models

Open **Model providers → Add model provider**. Enter:

- **API base URL**: an OpenAI-compatible Chat Completions base URL, including its
  API prefix, for example `https://example.com/v1`.
- **API key**: entered in the console and stored only on the server. It is never
  returned by list or edit APIs. Leave the replacement key empty when editing to
  keep the saved credential.
- **Text model**: the default model ID accepted by the provider.
- **Decision model**: an optional model ID for wake decisions. When omitted,
  decisions use the text model.

**Test saved models** makes a small inference request to each configured role.
It uses saved values, not unsaved form edits. Provider model usage is billed by
the configured service. Keys are stored in the private gateway SQLite database,
like other upstream credentials; protect its backups accordingly.

The console supports searching, editing, disabling and deleting providers.
Remove route assignments before deleting a provider.

## Select a wake decision provider

Open **Routes → Edit → Routing → Wake decision model**. Selecting a provider
allows it to see the current message and up to eight recent authorized messages
from that source conversation. Current message text is capped at 8000 characters;
each prior message is capped at 800 characters. Images and attachments are
represented by presence, not sent to the decision model.

The classifier processes requests, relevant follow-ups, corrections,
cancellations and status questions, and filters unrelated chatter. Verified bot
mentions bypass semantic filtering. Classification applies during execution too;
relevant input continues to the coordinator rather than every child Agent.
It never broadens route, sender, read or reply authority.

A filtered message remains in gateway history with state `filtered` and can
appear in later authorized history reads and snapshots. The decision is saved
before delivery; a lost Host receipt retries its original frozen submission
without classifying again. The events API includes `wake_provider_id`,
`wake_process` and sanitized `wake_error` diagnostics.

Classification has at most eight concurrent requests and a 20-second per-request
timeout. Slow classification does not block run polling or reply delivery. A
disabled or unavailable provider, timeout or malformed decision falls back to
normal Host delivery. Classification interrupted by process shutdown is retried
from the durable incoming event, not treated as a negative decision.

## Keep memory across bounded sessions

With **Bounded history, shared environment**, enable **Conversation memory**.
CG creates and remembers an a13n Memory Space for that concrete source and Agent
binding. Fresh sessions explicitly select this Space instead of inheriting the
Agent's default Memory Space selection. Other source conversations and Agent
bindings use different Spaces. New sessions still receive the configured history
window, 30 messages by default. Existing global memory is not copied or migrated.

This requires an a13n Host supporting Memory Spaces and new-thread `session`
settings. The Host key needs permission to create Spaces and run with them.
Failure leaves delivery retryable; CG never silently substitutes shared memory.
An uncertain Space creation is recovered by its `cgw-scope` label before creating
another. Gateway restart preserves the local binding. Route deletion does not
delete Host memory or its history. Reset conversations retains the Space.

CG adds guidance to use `global/MEMORY.md` for confirmed agreements, decisions,
deliverable references and unfinished work. Here `global/` is scoped to that
conversation's Space; a13n supplies session-local notes separately. Writing useful
memory remains Agent behavior, not an automatic completion guarantee.

The selection is not a new security boundary: existing channel read grants,
Agent standalone memory mounts, child Agent defaults and Host permissions still
apply. Audit those when strict isolation is required. This feature neither edits
the Agent nor removes its separately configured resources.

## Coordinate long work

**Coordinate long tasks** adds coordinator guidance to verified channel context.
It asks the Agent to delegate long work, route follow-ups to the relevant child,
verify results, and save useful memory before ending. It does not publish or edit
an a13n Agent revision, enable unconfigured tools, or invent a subagent roster.

Configure asynchronous subagents in the Host beforehand. Their configured
environment edges remain authoritative: `shared` reuses the parent's mounts;
`dedicated` creates a separate environment. To reuse an existing Modal environment,
the Host must already mount it and the relevant child edges must use `shared`.
CG preserves environment permission ceilings when copying desired mounts into
a fresh window Thread. Shared files still need separate task work directories.

For window delivery, CG checks the coordinator's current run, active descendant
threads and pending inbox entries before considering the window idle. A running
child keeps new input directed to its original coordinator, even if the root run
has ended. An incomplete child listing is an error, not proof of completion.
Unrelated sibling roots do not keep that coordinator active.

CG does not manage arbitrary external jobs, guarantee that an Agent uses
subagents, enforce semantic completion, or migrate work between coding harnesses.
