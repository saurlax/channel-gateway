# Multi-platform channel integration

Status: design contract for adapter expansion, requested 2026-10-21. The implementation
progress below distinguishes the delivered slice from remaining targets. The current adapter baseline is
owned by [Channel adapters](01-channel-adapters.md); the five-tool implementation
in the working tree is owned by [Agent tools](04-agent-tools.md).

The dated [platform API reference](../docs/platform-api-reference.md) records API
evidence, identity restrictions, integration profiles, and release gates. A
platform appearing there is not an enabled CG connection kind. New configuration
fields, conversation kinds, grants, and tool argument variants in this document
become available only with their implementation and migration tests.


## Implementation progress

The first implemented slice adds a common bounded async client/profile layer,
Slack signed HTTP events, Lark signed encrypted HTTP events, Discord Gateway v10,
explicit channel/thread routes, inline media, adapter-owned downloads, native
history under `history.read`, typed reactions, recall, Discord typing and upload
effects. The [baseline contract](01-channel-adapters.md#slack-lark-and-discord)
and [setup guide](../docs/slack-lark-discord.md) own the exact current behavior.
Native protocol and failure tests use simulated endpoints. Live test-conversation
acceptance remains outstanding; this is not a parity or deployment claim.

Slack Socket Mode, the isolated official Lark WebSocket worker, interaction
callbacks, thread inheritance, native search, cards, streams, native extension
catalogs and later platform phases remain unimplemented. Current `thread`
conversation IDs use Slack channel/root timestamp, Lark chat/root message
(resolving native thread ID after ownership verification), or Discord thread
channel ID. Upload effects provide parent-scoped step evidence; a generalized
child-operation ledger remains a target. Existing QQ/OneBot/Weixin behavior and
legacy route identities remain supported.

## Scope and invariants

The integration scope is messaging: conversations, messages, attachments,
reactions, interaction callbacks, message changes, membership, and associated
channel resources. Calendars, documents, business workflows, live calls, and
meeting recording are separate products/contracts even when a vendor exposes
them under the same application. An uploaded video is not a live video session.

All adapters use exactly these public MCP tools:

| Tool | Responsibility |
| --- | --- |
| `discover` | Authorized conversation handles, capabilities, operation schemas |
| `read` | Gateway/native history, messages, assets, events, native resources |
| `send_message` | New content, explicit replies, native cards, stream publishing |
| `execute_action` | Reactions, changes to existing messages, status, resource mutations |
| `get_operation` | Durable outcome, provider correlation, and safe diagnostics |

Conversation count, platform count, and native endpoint count never increase the
tool inventory. Reactions and typing are actions, not message creation. Native
endpoint coverage is a finite adapter-owned catalog with validated arguments,
not an HTTP proxy. A new integration must preserve the access and lifecycle
contracts in [Identity and access](02-identity-and-access.md) and
[Routing and delivery](03-routing-and-delivery.md).

There are three independent decisions: what the platform delivered, whether a
route starts Agent work, and whether the Agent publishes a response. Receiving
all messages never implies replying to all messages. An Agent stays silent by
completing without a message-tool call; final output is never published. An adapter must not invent an @mention
from text matching, or turn a reaction event into an addressed user request.

## Adapter responsibilities

The target adapter interface separates transport, projection, reads, sending,
actions, and capability evaluation. Its implementation may use an async vendor
SDK or bounded async HTTP/WebSocket operations. It must not import an Agent Host
SDK or invoke an LLM. Importing an adapter starts no work and reads no secrets.

| Surface | Contract |
| --- | --- |
| Configuration and binding | Validate connection identity, installation mode, credential references, API region and supported transport |
| `start` / `stop` | Own and close subscriptions, sockets, polling, token refresh and session renewal |
| Ingress verification | Authenticate packets before durable journaling; preserve verified native identities |
| Projection | Produce normalized messages, message changes, reactions, interactions and notifications |
| Capability evaluation | Separate adapter support, configured authority, observed availability and unverified requirements |
| History/resource reads | Execute only a declared read operation under its selected route and credential identity |
| Asset retrieval | Resolve opaque received-media references using vendor-specific auth, decryption and bounds |
| Message publishing | Translate validated content and delivery context to native sends, uploads and streams |
| Action execution | Apply a registered mutation with its own target checks and grant |
| Receipt normalization | Distinguish provider acceptance, delivery/read status, explicit rejection and uncertainty |

The server continues to own keys, route authorization, journals, outboxes,
idempotency records and process lifecycle. Core models remain independent of
FastAPI, a13n, and all vendor SDKs. SDK state and retry policy must not bypass the
durable workflow. Blocking SDK components require an isolated worker/sidecar and
an explicit shutdown/deadline contract; they cannot block the async service loop.

One connection represents one bot/installation identity. A Slack workspace, Lark
tenant, Teams tenant, WhatsApp phone-number binding, and Signal linked device are
not interchangeable accounts. OAuth user credentials are a separate selected
principal with separate storage/revocation, never an automatic fallback for a
bot operation. Channel API credentials remain separate from Host MCP keys.

## Conversation and message identity

Identity is structured data before any storage serialization:

| Field | Meaning |
| --- | --- |
| `connection_id` | CG account/installation boundary |
| `platform` / `account_mode` | Adapter and selected bot, application, user, bridge or device mode |
| `tenant_id` | Native workspace/organization namespace when present |
| `conversation_kind` / native ID | Exact addressable chat, room, child channel or thread |
| `parent` | Verified containing conversation/server, not inferred from a display name |
| `thread` | Native root/topic identifier and thread semantics when present |
| `message_id` | Native identity scoped to its actual conversation and account |
| `sender` | Native sender identity plus bot/user/system attribution |

Current `private`, `group`, `guild`, and `guild_dm` values retain their existing
meaning and serialized identities. In QQ, `guild` denotes a child channel; in
Discord, a guild ID denotes a server and is not a send destination. New adapters
need shared `channel` and `thread` kinds where their native address warrants
them. Task channels can use `session`, and custom/device transports can use
`endpoint`, preserving their non-chat lifecycle. Support requires coordinated
model/API/storage/console changes. Room or
space labels do not require a new kind merely because the vendor uses a new noun.

Use opaque identifiers with adapter-defined bounds. Slack timestamps retain
their exact string representation; Discord snowflakes are strings; Telegram
chat IDs retain signs and full precision. Native composite identifiers use a
validated structured encoding, not concatenation with an ambiguous delimiter.
Display names never select credentials, authority, or a replacement destination.

Threads are not all equivalent:

| Native form | Required representation |
| --- | --- |
| Slack replies | Parent channel plus exact `thread_ts` |
| Discord thread/forum post | Independently addressable child channel plus verified parent |
| Lark/Teams/Google Chat replies | Native thread/root alongside the containing chat/channel/space |
| Telegram forum topic | Chat ID plus `message_thread_id`; topic messages must not fall into another topic |
| Zulip topic | Channel ID plus topic string; renames/moves are explicit changes, not an immutable thread ID |
| Matrix relation | Room ID and `m.thread`/reply relation; room authorization still applies |
| QQ forum | Forum resource; not automatically treated as a Slack-style reply thread |

For new adapters, source configuration must explicitly select whether a parent
source includes visible child threads (`include_threads`, default false). A child
inherits source authority only after verified parent membership and current
native visibility checks. It receives a concrete handle and isolated delivery
binding; simply knowing a parent ID is insufficient. A parent read grant does
not authorize private or archived child threads that the principal cannot read.
Existing sources are unchanged by this new option.

A topic rename/move that cannot be proven to preserve identity must not merge
old and new Agent threads. Store the original native addressing data on history.
Deleting/recreating a native channel or moving it across an account boundary
cannot silently reuse the old CG binding.

## Ingress and subscription lifecycle

Authenticate HTTP callbacks using the vendor's signature/JWT/secret-header
contract over the original bytes. Validate tenant/application/account binding
as well as transport authenticity. WebSocket sessions authenticate their account
and verify event routing; an authenticated socket alone does not authorize every
sender or conversation in a received payload.

Persist authenticated packets and normalized changes before acknowledging an
event, advancing a polling cursor, or committing a resumable sequence. Do not
wait for Agent execution. Interaction deadlines require protocol acknowledgement
or deferment followed by separate durable response publishing. Callback reply
tokens, Slack action tokens, file keys and signed URLs remain private protocol
state and are not placed in prompts, MCP results, or public diagnostics.

Deduplicate normalized events by native event identity and account. Message
identity alone is insufficient for edits, reaction changes, status receipts or
repeated task updates. Snapshot/version/time identifiers distinguish changes.
Resume gaps, expired queues and unavailable native history must be reported as
coverage gaps; reconnect is not proof of complete backfill.

Initial sync, retained MQTT data and history backfill may populate authorized
history but never replay old Agent work by default. Own-message echoes update or
correlate the existing sent record without creating a second incoming task.
Messages from other bots follow an explicit activation policy to prevent loops.
Pure membership, typing, receipt and reaction events remain notifications unless
an administrator explicitly selects an activation rule for that event type.

## Capability and operation discovery

Each discovered capability must describe the selected account mode and exact
conversation, not just the vendor's product-wide feature list. The target shape
has independently evaluated fields:

```json
{
  "capability": "message.reaction.add",
  "support": "native",
  "authorized": false,
  "availability": "unknown",
  "principal_type": "bot",
  "required_grants": ["message.react"],
  "required_native_permissions": ["ADD_REACTIONS", "READ_MESSAGE_HISTORY"],
  "constraints": {"emoji_types": ["unicode", "custom"]},
  "evidence": {"checked_at": "2026-10-21", "source": "official_api"}
}
```

`support` is `native`, `emulated`, `extension`, `unsupported`, or `unverified`.
`availability` is `available`, `unavailable`, or `unknown`, with safe reason
codes such as `missing_scope`, `missing_membership`, `expired_reply_context`,
`offline`, `rate_limited`, or `requires_user_identity`. An application grant
configured in CG is not evidence that the upstream application obtained a scope.
Runtime probes update observations without broadening local authority.

Separate receive/send types, inline file/card support, limits, legal media source
forms, reply windows, native draft streaming, edit emulation, progress indicators,
and live media. Report whether a reaction is a set, add/remove operation, or an
optional provider extension. Adapter limits and upstream limits are distinct.

`discover(resource='operations')` returns finite namespaced catalogs such as
`slack.*`, `lark.*`, `discord.*`, and `qq.*`. Each operation includes its schema,
identity types, scope, grants, read/mutation classification, idempotency and
availability restrictions. Writable payloads, including nested target IDs and
card options, must not escape that scope. Listing operations grants no authority.

Each native read/action also declares an allowlisted result schema and scope
checks for returned resources. Returning the entire SDK response with a token
blacklist is insufficient. Nested messages, users, threads, files and snippets
must remain within the authorized result scope; private download/upload URLs,
callback capabilities and credential material become scoped handles or are
omitted. Action-status reads reapply the operation's current grant.

## Mapping to the five MCP tools

The current wire schemas remain documented in [Agent tools](04-agent-tools.md).
The following are extension requirements for new adapters, not claims that these
argument variants are already accepted by the current implementation.

| Capability family | MCP entry point | Target parameters and behavior |
| --- | --- | --- |
| Conversations/capabilities/catalog | `discover` | Existing `resource` selection and authorized `route_id` |
| Collected history | `read` | `resource='messages', source='gateway'`, bounded cursor and filters |
| Native history | `read` | `resource='messages', source='platform'`, explicit native-history grant |
| Native single-message/thread read | `read` | Registered `resource` or native operation with bound target |
| Full-text search | `read` | `resource='search'`, explicit source, query, time/author/thread filters and bounded cursor |
| Received asset | `read` | Opaque `asset_id` or existing authorized message/index handle |
| Members/roles/reactions/pins/events | `read` | Common resource where semantics match, otherwise registered native read |
| Text/image/audio/video/file | `send_message` | Typed `content[]`, adapter-validated combinations and optional thread/reply |
| Card/block/template/keyboard | `send_message` | Namespaced `native` content with its registered schema |
| Native stream or draft | `send_message` | Ordered `stream` fragments and a gateway-owned handle |
| Emoji/sticker content or poll creation | `send_message` | Native message content; not confused with a reaction on an existing message |
| Reaction/recall/typing | `execute_action` | Common action plus its discovered argument schema |
| Edit/pin/read state/moderation/card update | `execute_action` | Common action when semantics match; otherwise a finite native mutation |
| Send/action/stream outcome | `get_operation` | Gateway operation ID; safe provider receipt and capability-specific status |

Use typed reaction values (`unicode`, native/custom identifier and platform type)
when new adapters need them. Do not force Slack names, Discord custom emoji,
Telegram Unicode, or Lark reaction types into QQ decimal emoji IDs. Existing QQ
and OneBot argument codecs remain scoped to their existing implementation until
the shared variant is advertised. No platform-specific reaction tool is added.

Card creation publishes content; card updates mutate known messages/cards.
Slash commands, buttons and modals enter through adapter events, not an MCP
server pretending to receive an HTTP callback. Native command registration and
moderation require explicit administrative operations and grants.

### Target request schemas

The outer tool names remain fixed. New argument variants must be versioned and
advertised before clients use them. These examples show the intended mapping;
they are not executable against today's QQ-only native-history implementation.

Native history and search retain the selected gateway scope:

```json
{
  "name": "read",
  "arguments": {
    "route_id": "route_SLACK_CHANNEL",
    "resource": "messages",
    "source": "platform",
    "arguments": {"limit": 20, "thread_id": "thread_AUTHORIZED_HANDLE"}
  }
}
```

`messages` arguments are bounded `limit`, opaque `cursor`, optional narrower
`thread_id`, and explicit `start_time`/`end_time` RFC3339 boundaries. `search`
adds `query`, `sender_ids` and attachment-kind filters. Search must specify its
source; omission cannot choose the broadest upstream identity. Filters constrain
the route's scope and never replace its destination. Unsupported filters fail
instead of being ignored. `thread_id` is a CG-resolved authorized thread handle,
not an arbitrary native ID. Pages return a source/principal/filter-bound cursor.

New message content keeps platform options under the selected namespace:

```json
{
  "name": "send_message",
  "arguments": {
    "route_id": "route_DISCORD_THREAD",
    "content": [{"type": "text", "text": "The result is ready."}],
    "reply_to": "KNOWN_SOURCE_MESSAGE_ID",
    "native": {"discord": {"allowed_mentions": {"parse": []}}}
  }
}
```

The target content variants are text, image, audio, video, file and explicitly
registered native content. Asset content references an authorized `asset_id`, or
an adapter-supported bounded byte/URL source. Stickers, polls, templates and
cards use their native message schema until shared semantics are implemented.
Common `thread_id`, `reply_to`, stream and native options cannot disagree about
the destination. A quote's attachment does not become the new message's asset.
Unsupported rich content is never silently replaced by plain text or a link.
Adapters advertise allowed part counts, MIME types, captions, format, mentions,
source forms and combinations; the current two-part ceiling is not a universal
vendor limit. Outbound mention defaults suppress accidental broad mentions.

A reaction remains a separate action even when a vendor posts it to a messages
endpoint:

```json
{
  "name": "execute_action",
  "arguments": {
    "route_id": "route_DISCORD_THREAD",
    "action": "reaction",
    "arguments": {
      "source_message_id": "KNOWN_SOURCE_MESSAGE_ID",
      "emoji": {"kind": "unicode", "value": "👍"},
      "add": true
    }
  }
}
```

Target emoji variants are Unicode (`value`), custom (`id` and/or `name`, optional
`animated`), and platform-native (`value`, optional native type). The adapter's
schema constrains each variant: Discord custom emoji need their native identity;
Slack can require names; QQ can require numeric IDs/types; Lark can require its
reaction type string. Do not guess conversions from display text. Reject a
request that combines the new `emoji` and legacy `emoji_id` forms. A platform
set/replace API must preserve the intended add/remove result for the bot's own
reaction and cannot clear other participants' reactions implicitly.

For native operations, preserve the existing envelope:

```json
{
  "name": "execute_action",
  "arguments": {
    "route_id": "route_SLACK_CHANNEL",
    "action": "native",
    "arguments": {
      "operation": "slack.pin_message",
      "parameters": {"source_message_id": "KNOWN_SOURCE_MESSAGE_ID"}
    }
  }
}
```

Catalog identifiers describe gateway operations rather than permitting callers
to choose vendor HTTP methods or paths. Native argument validation also applies
to IDs embedded in cards, polls, attachments and resource payloads. The catalog
may include native-only features without changing any of the five tool names.

### Native history and search authority

Current `read_channel_ids` grants cover CG-received history; they do not silently
grant native archive access. Native history and search require new explicit
`history.read` and `history.search` grants respectively, in addition to a current
authorized source route, the selected upstream principal, scopes and membership.
First releases restrict native history/search to that route's exact conversation
and authorized thread scope. Read-only observed-account handles remain gateway
history handles unless a later implemented contract explicitly grants native scope.

Adapters must constrain queries to the bound destination and revalidate every
returned message, snippet, file and thread before exposing results. A broad OAuth
scope or global search API is not a gateway read grant. Slack user-scoped search
cannot be attached silently to a shared bot trust boundary. Filter expressions
supplied by the Agent cannot override server-owned scope predicates.

Every page reports `source`, coverage/visibility constraints, effective identity,
next cursor, requested time range and known gaps/truncation. Cursors bind source,
account, conversation, principal, filters and ordering; they are not bearer
credentials. Changing filters or principal invalidates the cursor. Explicit
platform reads fail as unsupported/unauthorized instead of returning gateway data
with a misleading native-history label. Search index completeness and timeline
completeness are distinct.

### Media and quoted content

An asset handle identifies a message attachment, revision/index, owning account
and conversation. It is not a download URL, credential or read grant. Resolve it
under current read authority, and recheck after external I/O. Cross-conversation
forwarding also requires independent destination send authority. A current
message's attachments and its quoted message's attachments retain separate
provenance; neither is replaced by the previous turn's media.

Media retrieval belongs to the adapter because authentication differs. QQ may
use a credential-free signed CDN URL; Slack may require an authenticated private
file request; iLink/WeCom may require decryption; Teams may require Graph consent.
Use separate API and CDN clients, bounded downloads, verified redirect rules and
credential forwarding policies. Never attach a bot token to an unrelated CDN
host. MIME declarations, byte signatures, filename and decoded size are checked
as appropriate. Do not infer outbound voice-bubble support from an audio enum.

MCP media remains typed image/audio/resource content or an authorized asset
handle. Unsupported model input goes to the Host sandbox for command-based
parsing. CG does not choose a visual/video-understanding model. Logical file-tool
paths and shell paths are the Host environment adapter's responsibility; channel
adapters do not invent filesystem paths or accept arbitrary server-local paths.

### Reply, proactive send and streaming

Automatic final text replies preserve the triggering conversation, thread and
private native reply context. Tool sends select an already authorized destination.
Existing QQ passive/proactive/wakeup behavior remains owned by the QQ contract.
Other adapters declare their own supported delivery modes and windows; current
QQ-only mode checks cannot be reused as universal platform behavior.

Any new proactive mode requires explicit destination send authority and local
`message.proactive` authority where that mode is exposed. Native installation,
contact relationships, group switches, service windows, templates and consent
still apply. Expired passive replies never silently become proactive sends.
Unsupported content/thread/card combinations fail before side effects; a
multi-call upload/publish workflow records its substeps and cannot return partial
success as an entirely successful message.

Streaming distinguishes persistent native streams, temporary native drafts,
editable cards and message-edit emulation. The adapter owns conversion from
ordered CG fragments to that mechanism and advertises it. Telegram drafts need
an explicit final persistent send; a successful draft update is not a sent
history record. Slack streams need native stop/finalization; Lark cards need
versioned card updates. Streams are not inferred from a WebSocket named Stream.
Only user-visible response/progress content is publishable; hidden model
reasoning and tool credentials are not channel stream events.

Store the operation, snapshot/context and fragment ordering before external
effects. Reuse a gateway handle rather than accepting arbitrary native stream IDs.
On uncertainty, expose the durable outcome and stop unsafe continuation. Once
published, content may remain visible even if later fragments fail. Normalize
the confirmed final message and its changes without duplicating each fragment
as a new incoming task or claiming an unconfirmed final answer was delivered.

## Authorization, delivery and compatibility

Existing grant strings and policies are preserved. New operations declare their
own read, send, edit, pin, moderation or account-management authority; they never
inherit arbitrary mutation authority from permission to send text. New grants
default to absent. Upstream user/account identity is fixed at admission and
rechecked at execution; credentials cannot be substituted under a frozen request.

Use the existing independent reply and action ledgers behind `operation_id`.
Store provider correlation, scope and receipt strength. HTTP 200, file upload,
broker PUBACK, message acceptance and recipient read state are different facts.
Delivery/read callbacks may enrich a receipt but cannot fabricate evidence for
platforms that provide none. Message edits/deletes become authorized revisions
or tombstones; raw-journal retention remains a separate explicit policy.

If a declared publication needs multiple native messages, persist a parent
operation and its child receipts. `sent` requires all required publications to
be confirmed; a known rejection reports `failed` with confirmed child effects,
and any uncertain child makes the aggregate `unknown`. `get_operation` reports
safe child states and `publication_complete`, so the Agent cannot interpret a
partly sent caption/media pair as fully delivered or safely repeat the whole
operation. Internal upload receipts are not published message receipts.

Honor per-method, account, destination and stream limits, provider response
headers and `Retry-After`. Queue before reserving an external attempt; preserve
fair progress for other conversations. A queued throttle is not a failed send.
Read retries may be bounded. Retrying an uncertain write requires a documented
native idempotency guarantee for that operation; no SDK's hidden retry loop can
override the current conservative unknown-outcome policy. Existing QQ failed/
unknown handling is unchanged.

Schema expansion requires migrations for new connection modes, conversation
kinds, nested thread fields and grants. Existing identities, tokens, pending
outboxes and Host bindings are not rewritten implicitly. Refreshable operation
schemas carry an adapter/schema revision. a13n retains one MCP connection per
trust boundary and its own configured tool permissions; changing platform or
group count does not create Tool Search groups. No deployment or Host prompt
update is performed by publishing this specification.

## Integration sequence and release gates

1. Validate common contracts against existing QQ/OneBot behavior and implement
   Slack, Lark and Discord as independent adapters. Each must cover real native
   history/threads/media and reaction semantics before claiming parity.
2. Extend Weixin media and add Telegram, WeCom AI bot, DingTalk and Teams using
   their distinct modes and SDK/permission constraints. Native drafts/cards and
   Graph access are separate gated capabilities, not prerequisites for basic
   text where unsupported.
3. Add Google Chat, Matrix, Mattermost, Zulip, Rocket.Chat, LINE and WhatsApp from
   their profiles. App/user consent, E2EE and business eligibility stay explicit.
4. Keep Signal, Yuanbao, Xiaoyi and MQTT opt-in profiles. Implementation-only or
   custom-protocol evidence cannot establish a vendor-wide availability claim.

Every adapter release requires the common checks below plus its reference
profile's gates. A failed optional feature disables that capability without
reporting unsupported text/history as implemented.

| Area | Required evidence |
| --- | --- |
| Identity/auth | Wrong tenant/account, invalid signature, revoked credential, bot/user separation, inaccessible parent/thread |
| Ingress | Duplicate delivery, persistence failure before ACK/cursor, reconnect gap, own echo, edit/delete/reaction event distinction |
| Activation | Ordinary non-addressed discussion, verified mention, explicit task without mention, notification-only event, silent Agent envelope |
| Read boundary | Gateway/native source separation, explicit history/search grants, cross-route result filtering, pagination and revocation between pages |
| Media | Image/video/audio/file receive and send separately; expiry/decryption/auth; exact quoted attachment; MIME/size failures; unsupported native voice |
| Publishing | Text, quote/thread, card callback, reaction add/remove, edit/recall/pin where declared; unsupported combinations fail before queuing |
| Lifecycle | Confirmed/explicit-rejected/unknown outcomes, rate limits, crash after upload or partial stream, no duplicate publication after restart |
| MCP/Host | Exactly five tools across accounts/conversations; current schemas; one Host connection; typed media and sandbox paths; no prompt/permission rewrite |

Tests must include mocked protocol failures and scoped live acceptance in a
designated test conversation. Native permission tests need least-privilege and
denied cases, not only an administrator credential. Record the upstream API/SDK
version, date, account mode and granted scopes with acceptance results.
