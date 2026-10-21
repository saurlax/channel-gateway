# Agent tools

## MCP surface

The implemented five-tool surface is defined here. The first Slack/Lark/Discord
slice adds typed reactions, explicit threads and native history. Remaining
expansion requirements live in [Multi-platform channel integration](07-channel-integration.md).

CG exposes official MCP Streamable HTTP at `/mcp/`, including the trailing slash.
It uses stateless HTTP requests with JSON responses. Access requires the
[host or route capability](02-identity-and-access.md#host-capabilities), independent of
administrator and upstream credentials. The tool transport does not create or
wake an Agent run; the [Host delivery workflow](03-routing-and-delivery.md) owns
that operation.

The public inventory contains exactly five tools for every credential and channel.
No legacy tool aliases are registered. Adding conversations or adapters changes
resource data and operation catalogs, never creates per-conversation tools.

| Tool | Behavior |
| --- | --- |
| `discover(resource='conversations', route_id=None)` | List authorized handles, or inspect a selected handle's capabilities and operation schemas |
| `read(resource, arguments=None, route_id=None, source=None)` | Read `messages`, `image`, `attachment`, `events`, or a registered `native` resource |
| `send_message(content, route_id=None, delivery=None, reply_to=None, stream=None, native=None)` | Queue text, media, cards, or a stream fragment |
| `execute_action(action, arguments=None, route_id=None)` | Queue `reaction`, `recall`, `typing`, or a registered `native` mutation |
| `get_operation(operation_id, route_id=None)` | Query this route's durable message, stream, or action status |

`discover(resource='capabilities')` distinguishes adapter support, configured route
authority, and upstream permissions that CG has not verified. It describes the
conversation kind, content types, bounds, default delivery, stream constraints,
and native message-option schema. `discover(resource='operations')` returns
common read/action argument schemas and applicable namespaced native operations,
including grants and platform restrictions. Discovery never grants authority.
A read-only account-history handle can discover its own capabilities but does
not gain native resource or mutation authority.

`read.arguments` uses the schema for its selected resource. `messages` accepts
`limit` and an optional `cursor`; received media uses `source_message_id` and
`image_index` or `attachment_index`. Unknown arguments, including nested
`route_id`, are rejected. Message reads default to `source='gateway'` and return
`source`, `coverage` with `complete: false`, and `next_cursor`. Slack/Lark/Discord native history requires `source='platform'` and the exact
source route's `history.read` grant. Other adapters reject native-history reads
without falling back to gateway data. Native cursors and media provenance are
account/authority bound and expire after 15 minutes. Native resource reads use `source='platform'` and a catalog
operation. Other supported reads refer to gateway-collected handles/events.
Cursors select records strictly before the oldest returned persistence time and
ID; each page reapplies current access and remains chronological. A full page
may return a cursor whose following page is empty. Invalid cursors fail.

`send_message.content` is a typed array containing at most one text part and one
media part. Text has `type: 'text'`, `text`, and optional `format` (`text` or
`markdown`). Media has `type` (`image`, `audio`, `video`, `file`), `media_type`,
`filename`, and exactly one of `data_base64` or `url`. QQ supports text plus one
media item, with guild media restricted to images; OneBot supports inline images;
Weixin supports text only. Slack/Lark/Discord accept inline image/audio/video/file
parts under their [declared restrictions](../docs/slack-lark-discord.md).
Unsupported combinations fail before queuing anything.
QQ/OneBot media cannot currently combine with Markdown, a quoted reply, native options, or
a stream. This does not claim future adapters support these same combinations.
QQ native options are `native: {'qq': {'keyboard': ..., 'embed': ..., 'ark': ...}}`;
unknown namespaces/options fail. Cards can use empty `content`. `reply_to` selects
a locally known message under source authority. `stream` contains `index`,
`stream_handle`, `finished`, and `input_mode`; it supports QQ C2C passive streams
on MCP-output routes only, with text content and no other send options.

Native reads/mutations accept `arguments: {'operation': 'qq.<catalog-name>',
'parameters': {...}}`. The catalog validates every argument and route-bound
scope; raw HTTP methods, paths, credentials, and arbitrary destinations are not
accepted. Platform-specific schemas remain behind parameters rather than adding
MCP tools. Reactions, recalls, and typing remain non-message actions with their
own admission and execution checks. Reads have read-only annotations; the generic
action tool conservatively has destructive annotations because some actions are
destructive. Annotations never replace per-operation grants.

All sends/actions return `operation_id` and `state`; existing `reply_id` or
`action_id` fields identify the underlying unchanged ledger, and stream receipts
also carry their handle. `get_operation` resolves either ledger by its persisted
ID and checks exact route ownership. Message results expose only a confirmed
platform message ID; action results retain their existing grant checks. Revoking
send authority hides message results. Queued is not confirmed delivery, and
`sent`/`succeeded` is platform acceptance, not a recipient read receipt. Unknown
outcomes are never automatically repeated. Polling does not create Agent work.

This is a breaking MCP interface change. Clients must refresh their tool inventory,
enable the five entry points, and update prompts/calls. Existing route/Host keys,
grants, conversation bindings, queues, and stored receipts require no rewrite.
CG does not modify a13n connection tools or Agent instructions automatically.

[Images and reactions](05-images-and-reactions.md) owns image bounds, private URL
handling, platform format differences, and reaction target checks. These actions
inherit existing read/reply scopes and durable reply states. Listing includes
channel capabilities; optional OneBot extensions are not universal capabilities.

A host key requires explicit `route_id` obtained from `discover(resource='conversations')`.
A specific route token may omit it for backward compatibility, or provide its own
route ID. An all-conversation route token lists its enabled concrete children and
requires an explicit child `route_id`; template routes are never send destinations.
Source-list tokens also list enabled concrete children. They may omit `route_id`
only when configured for exactly one concrete channel and user/group and exactly
one authorized child exists. Specific configured sources can materialize a handle
before receiving a message; wildcard sources materialize observed conversations.
Read-only account-history handles are also returned when `read_channel_ids` is
configured. They are deterministic opaque IDs, not stored routes or send
destinations; use an explicit `route_id` to read them. Providing any unauthorized
handle fails without exposing destination content.
Listing returns route ID (`id`), name, channel connection ID, conversation kind
and ID, and `can_read`/`can_reply` flags. Read-only handles have `can_reply: false`. Source destinations can be reply-authorized
without being readable when their account is outside the explicit read grant. It exposes neither sender allowlists nor credentials.
For enabled all-conversation scopes, listing materializes handles for previously
observed authorized conversations without triggering Agent work. Specific routes,
including disabled ones or those assigned to another Host, remain authoritative.

Reads exclude credentials and private protocol reply contexts, including QQ
resume state and Weixin `context_token`. Tool replies inherit the configured
channel's [capability and reply constraints](01-channel-adapters.md).

Tools accept only currently authorized route/conversation handles, never arbitrary
account or conversation destinations. A host key can read across explicit readable
channels granted by its enabled assigned route configurations; credentials and
current channel presence are checked on each invocation. QQ native resource and
moderation operations require separate explicit grants under the
[platform contract](06-qq-platform.md). Complete native history retrieval is not implemented.

`read(resource='messages')` clamps the combined requested limit to 1–100 and reads the source
account/conversation's stored messages: current-route deliveries plus
unmatched/blocked observations, including messages received before route creation
or while the route was disabled. Current sender authorization is applied before
selecting the newest records, returned in chronological order by persistence time
and ID. This legacy policy applies when `read_channel_ids` is absent/null. An
explicit channel grant reads all stored incoming messages for that account/conversation,
including unmatched, blocked, and archived-binding history, independently of
trigger sources or sender allowlists. `[]` denies all reads, and `["*"]` includes
future channel accounts. Reading never replays old inputs or starts Agent work.

Messages linked to an archived/deleted route remain excluded from replacement
legacy source-scoped capabilities; explicit channel grants include that retained
incoming history and administrator history retains them. The raw channel-event
journal, image download URLs/bytes, and private protocol contexts are never MCP message
history. Gateway-source tools read only what CG received and sent, not complete native
platform history. Each record has a stable `history_id`, persistence `created_at`,
and `direction` (`incoming` or `outgoing`). The combined newest records are
returned chronologically by creation time and ID. Only `sent` bot messages enter
this history; queued, sending, failed, blocked, unknown, and non-message actions
are excluded. Outgoing records include their platform `source_message_id`, text,
and safe media descriptions, never base64, upload tickets, source URLs, or private
reply payloads. Media descriptions on outgoing records are not received-image or
attachment read handles. Existing sent records are included without replay.

Legacy source-scoped reads include only the current route's sends, excluding
event-linked replies whose original sender is no longer authorized. Explicit
account read grants include sent history from active and archived routes in that
exact account/conversation, under the same present-account and read-grant checks
as incoming history. Read-only conversation discovery includes outgoing-only
history and still grants no sending authority.
Image bytes are available separately through `read(resource='image')` under the same current
read policy, not embedded in every history response.

`send_message` accepts up to 16000 characters (nonempty text or a supported guild
card is required) in any currently source-authorized writable route. All channel
messages use tools; no Agent final output is forwarded. Proactive delivery still
requires `message.proactive`; ungranted routes use authorized passive context.
Tool delivery defaults and rate budgets follow the
[QQ platform contract](06-qq-platform.md#passive-replies-and-proactive-tools).
It returns a reply ID and initial state; this is a request
receipt, not confirmed platform delivery. The [reply lifecycle](03-routing-and-delivery.md#reply-lifecycle)
governs automatic sending. Calls are not deduplicated by an MCP request ID;
repeating a tool invocation can create another reply.

QQ Markdown, keyboards, references, proactive sends, media, streaming, typing,
guild reactions, recalls, attachment/event reads, and the native resource catalog
are defined by [QQ platform operations](06-qq-platform.md). Resource mutations and
message actions use their own action IDs and durable action ledger. They are not
aliases of `send_message`. Capability discovery includes operation schemas, current
route grants, and invitation/private-domain restrictions; it does not assert that
the QQ application has obtained every upstream permission.

## a13n binding

An Agent sharing one Host trust boundary uses one MCP connection authenticated
with that Host's key. The tool inventory is fixed across conversations; each
invocation selects an authorized `route_id`. Adding a group, private chat, or
guild channel changes route data, not tool names, connection count, or Tool Search
groups. Per-conversation Tool Search groups are not required. This shared
connection supports exact, multi-source and wildcard route configurations.
For an intentionally narrower capability, one concrete source can optionally bind
one dedicated a13n MCP connection using its route token. The connection cannot be
shared by two routes on a Host. No dedicated binding is required when using the
Host key. Route read grants can exceed trigger sources. Without an MCP connection
in the Agent's configuration, it cannot send channel messages; final output is
never a substitute.

CG always preserves the selected Agent's configured tools. Host submission bodies
never set `options.overrides.connection_tools`. Configure enabled connections and
tools in the Agent interface; storing a dedicated connection ID on a route does
not add or select any tools in a13n. The dedicated connection must already be
included in that Agent's configuration. CG does not add tools absent from the
Agent or override upstream resource authorization. The
[privacy boundary](02-identity-and-access.md#privacy-boundary) still applies.

The removed `access_mode` field is rejected by route creation/update APIs and
omitted from route views. Historical stored values are ignored and removed on
startup; no resave is needed to inherit Agent configuration. Previously frozen
queued/failed submission bodies containing the old connection-tool override are
blocked before retry rather than changed under the same idempotency key. Send a
new message to start a new command. Already submitted runs continue their normal
completion workflow.

Changing the optional MCP connection binding is refused while an input is
queued/submitted, including inputs in an all-conversation route's children.
Rotating a token does not rewrite a frozen Host submission body. After rotation,
the administrator must replace the bearer credential in the corresponding a13n
connection. Route credentials must not be placed in `mcp_headers`, which are
ordinary thread context rather than a secret credential mechanism.

## Adapter extensions

New adapters retain exactly five public tools. `discover` includes platform-read
schemas, actual text/upload bounds, support versus unknown upstream availability,
and unsupported features. `read(source='platform')` reads history or a cached
history asset; received gateway assets continue to use the gateway source.
`execute_action(action='reaction')` accepts a discriminated `emoji`: Unicode
value, custom name/ID, or platform value/type. Legacy QQ/OneBot numeric arguments
remain accepted; mixing typed and legacy fields is refused. Representations are
validated by the selected adapter both at admission and execution.

Slack/Lark upload operations record confirmed intermediate `effects` associated
with the parent reply ledger. Lark upload keys are represented by hashed asset
references. An upload is not a sent message. A final missing/uncertain message
receipt remains unknown, with known partial effects retained for inspection;
there are no automatic write retries. Existing queued work and old identifiers
are preserved. Unsupported native options, search and streams for the new
adapters fail before any publication is queued.
