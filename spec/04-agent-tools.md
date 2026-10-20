# Agent tools

## MCP surface

CG exposes official MCP Streamable HTTP at `/mcp/`, including the trailing slash.
It uses stateless HTTP requests with JSON responses. Access requires the
[host or route capability](02-identity-and-access.md#host-capabilities), independent of
administrator and upstream credentials. The tool transport does not create or
wake an Agent run; the [Host delivery workflow](03-routing-and-delivery.md) owns
that operation.

| Tool                                         | Behavior                                                                |
| -------------------------------------------- | ----------------------------------------------------------------------- |
| `list_conversations()`                       | List readable conversations and source reply destinations, without message content |
| `read_conversation(limit=20, route_id=None)` | Read locally received text and image indices from an authorized conversation |
| `send_message(text, route_id=None)`          | Request a reply to that authorized conversation                         |
| `read_image(source_message_id, image_index=0, route_id=None)` | Return an authorized received image as native MCP image content |
| `send_image(image_base64, media_type, caption='', route_id=None)` | Queue an image reply |
| `set_message_reaction(source_message_id, emoji_id, add=True, route_id=None)` | Queue an optional OneBot message reaction |

[Images and reactions](05-images-and-reactions.md) owns image bounds, private URL
handling, platform format differences, and reaction target checks. These actions
inherit existing read/reply scopes and durable reply states. Listing includes
channel capabilities; optional OneBot extensions are not universal capabilities.

A host key requires explicit `route_id` obtained from `list_conversations`.
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
and ID, reply source, and `can_read`/`can_reply` flags. Read-only handles have null
reply source and `can_reply: false`. Source destinations can be reply-authorized
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

`read_conversation` clamps the requested limit to 1–100 and reads the source
account/conversation's stored incoming messages: current-route deliveries plus
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
history. Tools read only what CG received and projected, not complete native
platform history, and do not include outgoing replies as incoming messages.
Image bytes are available separately through `read_image` under the same current
read policy, not embedded in every history response.

`send_message` accepts up to 16000 characters (nonempty text or a supported guild
card is required) and accepts invocation only for
`reply_source: mcp`, or a QQ route with explicit `message.proactive` authority.
The latter can retain final-output passive replies while tools proactively send
messages/media. Tool delivery defaults and rate budgets follow the
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
groups. Per-conversation Tool Search groups are not required. Final-output routes
need no dedicated MCP binding; QQ proactive tools additionally require the
explicit route grant described in the [QQ contract](06-qq-platform.md#passive-replies-and-proactive-tools).

For an intentionally narrower client capability, a route with exactly one concrete source can bind
one dedicated a13n MCP connection. Multi-source and wildcard a13n rules use final
run output, while generic MCP clients can use multi-source scopes directly.
The same dedicated connection cannot
be shared by two routes on a Host. Its URL points to CG `/mcp/`, and its bearer
credential is that route's token. The shared Host connection spans all enabled
assigned route read grants and source destinations. A route
token includes its configured readable channels, which can exceed its trigger sources. Without a binding, the a13n bridge operates through
final run output without requiring MCP.

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

Changing reply source or MCP connection binding is refused while an input is
queued/submitted, including inputs in an all-conversation route's children.
Rotating a token does not rewrite a frozen Host submission body. After rotation,
the administrator must replace the bearer credential in the corresponding a13n
connection. Route credentials must not be placed in `mcp_headers`, which are
ordinary thread context rather than a secret credential mechanism.
