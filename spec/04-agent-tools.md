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
| `list_conversations()`                       | List enabled routes assigned to the credential, without message content |
| `read_conversation(limit=20, route_id=None)` | Read locally received text from an authorized conversation              |
| `send_message(text, route_id=None)`          | Request a reply to that authorized conversation                         |

A host key requires explicit `route_id` obtained from `list_conversations`.
A specific route token may omit it for backward compatibility, or provide its own
route ID. An all-conversation route token lists its enabled concrete children and
requires an explicit child `route_id`; template routes are never send destinations. Providing any other route fails without exposing destination content.
Listing returns route ID (`id`), name, channel connection ID, conversation kind
and ID, and reply source. It exposes neither sender allowlists nor credentials.
For enabled all-conversation scopes, listing materializes handles for previously
observed authorized conversations without triggering Agent work. Specific routes,
including disabled ones or those assigned to another Host, remain authoritative.

Reads exclude credentials and private protocol reply contexts, including QQ
resume state and Weixin `context_token`. Tool replies inherit the configured
channel's [capability and reply constraints](01-channel-adapters.md).

Tools accept only currently authorized route IDs, never arbitrary account or
conversation destinations. A host key can read across its assigned channel
routes. Moderation and native history retrieval are not implemented.

`read_conversation` clamps the requested limit to 1–100 and reads the source
account/conversation's stored incoming text: current-route deliveries plus
unmatched/blocked observations, including messages received before route creation
or while the route was disabled. Current sender authorization is applied before
selecting the newest records, returned in chronological order by persistence time
and ID. Reading never replays old inputs or starts Agent work.

Messages linked to an archived/deleted route remain excluded from replacement
route capabilities; administrator history retains them. The raw channel-event
journal, media payloads, and private protocol contexts are never MCP message
history. Tools read only what CG received and projected, not complete native
platform history, and do not include outgoing replies as incoming messages.

`send_message` accepts 1–16000 characters and accepts invocation only for
`reply_source: mcp`. It returns a reply ID and initial state; this is a request
receipt, not confirmed platform delivery. The [reply lifecycle](03-routing-and-delivery.md#reply-lifecycle)
governs automatic sending. Calls are not deduplicated by an MCP request ID;
repeating a tool invocation can create another reply.

## a13n binding

For conversation isolation, each route can bind one dedicated a13n MCP connection. The same connection cannot
be shared by two routes on a Host. Its URL points to CG `/mcp/`, and its bearer
credential is that route's token. A Host key can also be configured in a client to access the Host’s assigned
routes, but grants broader access than a route token. Without a binding, the a13n bridge operates through
final run output without requiring MCP.

Restricted-mode submitted inputs explicitly set `options.overrides.connection_tools`:

- No binding selects an empty connection-tool list.
- `reply_source: output` with a binding selects `read_conversation`.
- `reply_source: mcp` selects `read_conversation` and `send_message`, and requires
  a binding.

This prevents the run from inheriting unrelated revision connection tools and
avoids generating both a tool-requested reply and a final-output reply. It does
not restrict other a13n resource or built-in tool permissions; the
[privacy boundary](02-identity-and-access.md#privacy-boundary) still applies.

Full mode omits the connection-tool override, preserving the selected Agent's
configured tools. It does not add tools absent from that Agent or override
upstream resource authorization. A configured dedicated connection must already
be included in the Agent's own tool configuration in this mode.

Changing permission mode, reply source or MCP connection binding is refused while
an input is queued/submitted, including inputs in an all-conversation route's
children. Changing access or rotating a token does not rewrite a frozen
Host submission body. After rotation, the administrator must replace the bearer
credential in the corresponding a13n connection. Route credentials must not be
placed in `mcp_headers`, which are ordinary thread context rather than a secret
credential mechanism.
