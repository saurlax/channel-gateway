# Agent tools

## MCP surface

CG exposes official MCP Streamable HTTP at `/mcp/`, including the trailing slash.
It uses stateless HTTP requests with JSON responses. Access requires the
[route capability](02-identity-and-access.md#route-capabilities), independent of
administrator and upstream credentials. The tool transport does not create or
wake an Agent run; the [Host delivery workflow](03-routing-and-delivery.md) owns
that operation.

| Tool                          | Behavior                                                    |
| ----------------------------- | ----------------------------------------------------------- |
| `read_conversation(limit=20)` | Read locally received text from the authorized conversation |
| `send_message(text)`          | Request a reply to that same conversation                   |

Neither tool accepts an arbitrary account, conversation destination, or platform
action. Cross-channel reads, moderation, and native history retrieval are not
part of the current tool surface.

`read_conversation` clamps the requested limit to 1–100, retrieves the most recent
records for the route, filters against its current sender allowlist, and returns
the remaining messages in chronological order. It can return fewer records than
the limit. It reads CG's stored incoming messages, not the platform's complete
history, and does not include outgoing replies as incoming messages.

`send_message` accepts 1–16000 characters and accepts invocation only for
`reply_source: mcp`. It returns a reply ID and initial state; this is a request
receipt, not confirmed platform delivery. The [reply lifecycle](03-routing-and-delivery.md#reply-lifecycle)
governs approval and sending. Calls are not deduplicated by an MCP request ID;
repeating a tool invocation can create another reply.

## a13n binding

Each route can bind one dedicated a13n MCP connection. The same connection cannot
be shared by two routes on a Host. Its URL points to CG `/mcp/`, and its bearer
credential is that route's token. Without a binding, the bridge operates through
final run output without requiring MCP.

Every submitted input explicitly sets `options.overrides.connection_tools`:

- No binding selects an empty connection-tool list.
- `reply_source: output` with a binding selects `read_conversation`.
- `reply_source: mcp` selects `read_conversation` and `send_message`, and requires
  a binding.

This prevents the run from inheriting unrelated revision connection tools and
avoids generating both a tool-requested reply and a final-output reply. It does
not restrict other a13n resource or built-in tool permissions; the
[privacy boundary](02-identity-and-access.md#privacy-boundary) still applies.

Changing reply source or MCP connection binding is refused while an input is
queued/submitted. Changing access or rotating a token does not rewrite a frozen
Host submission body. After rotation, the administrator must replace the bearer
credential in the corresponding a13n connection. Route credentials must not be
placed in `mcp_headers`, which are ordinary thread context rather than a secret
credential mechanism.
