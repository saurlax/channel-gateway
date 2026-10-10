# Identity and access

## Authority model

The baseline has one administrator role, verified platform sender allowlists,
and conversation-scoped Agent capabilities. It does not provide full RBAC,
cross-channel identity linking, or general information-flow enforcement.

| Credential                     | Authority                                                                   |
| ------------------------------ | --------------------------------------------------------------------------- |
| CG administrator bearer token  | Manage connections, routes, message delivery, and reply decisions           |
| OneBot API token               | Authenticate outgoing account API calls                                     |
| OneBot callback signing secret | Verify incoming account event bytes                                         |
| a13n workspace API key         | Submit input and read execution within the upstream principal's permissions |
| CG route bearer token          | Use MCP for exactly one enabled route                                       |

These credentials are not interchangeable. A channel sender ID is identity data;
authorization is decided by the route allowlist, not by the message body.

## Administrator and credential handling

Management requires `CHANNEL_GATEWAY_ADMIN_TOKEN` with at least 32 characters.
Without a configured token, management returns HTTP 503. Invalid authentication
returns HTTP 401. Requests with a different browser origin are rejected. The
console keeps its administrator token in memory and clears it on sign-out;
sign-out does not rotate the configured server token.

Connection tokens and callback secrets are write-only through management views.
Validation responses omit submitted values; bridge errors do not return upstream
bodies, credentials, or conversation text. Requests use bounded timeouts and do
not follow redirects that could forward a credential to a different destination.

Runtime state contains upstream credentials and private conversation content
without encryption at rest. The state directory must be private (`0700`); the
service refuses a shared directory instead of changing its permissions. The
database file is restricted to its owner. Remote authenticated endpoints require
HTTPS and protection of the persistent volume. The default listener is loopback.

## Route access

A [route](03-routing-and-delivery.md#conversation-routes) authorizes one exact
channel account and conversation. A private route's sender allowlist is exactly
its conversation user. A group route requires an explicit nonempty sender
allowlist. Group automatic replies are refused; private automatic replies require
explicit opt-in.

Authorization is checked at ingress, when advancing input delivery, during MCP
access, and before sending a reply. Removing a sender hides that sender's messages
from subsequent MCP reads and blocks pending inputs from that sender. Replies
linked to such an input are blocked before sending. MCP-created replies have no
source event link; their authority is the route credential and current route
state, with the same group approval requirement.

Disabling a route blocks new work and invalidates its MCP authority. It does not
interrupt an external request already in flight, erase history, retract a sent
message, or cancel an a13n run. Re-enabling a route restores its still-current
token; rotate the token when the previous credential must remain revoked.

## Route capabilities

Creation and rotation reveal a random route token once. Storage retains its hash,
not the token. Rotation invalidates the previous token; calls rechecking that
token can fail after rotation. Ordinary route views never reveal it.

The MCP endpoint rejects browser origins and requires a valid token for an enabled
route. Administrator tokens grant no MCP route access. Tools also recheck the
route at invocation. Tokens convey neither arbitrary destination selection nor
the right to manage configuration. [Agent tools](04-agent-tools.md) owns tool
behavior and a13n connection binding.

## Privacy boundary

Separate conversation threads, scoped tools, allowlists, and administrator group
approval reduce exposure. They do not guarantee that an Agent with access to
other private resources will never disclose those resources. The a13n connection
tool override does not remove built-in toolsets, plugins, skills, or other Host
resource permissions. Administrators must select an Agent whose remaining access
is appropriate for the conversation. Prompts and upstream event filters do not
replace these authorization boundaries.
