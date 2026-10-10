# Identity and access

## Authority model

The baseline has one administrator role, verified platform sender allowlists,
and host/conversation-scoped Agent capabilities. It does not provide full RBAC,
cross-channel identity linking, or general information-flow enforcement.

| Credential                      | Authority                                                                   |
| ------------------------------- | --------------------------------------------------------------------------- |
| CG administrator session cookie | Manage connections, routes, message delivery, and reply decisions           |
| OneBot access token             | Authenticate account API calls or a universal WebSocket session             |
| QQ AppID / AppSecret            | Obtain API tokens and verify official callbacks                             |
| Weixin bot token                | Poll and send for one paired iLink bot account                              |
| OneBot callback signing secret  | Verify incoming account event bytes                                         |
| a13n workspace API key          | Submit input and read execution within the upstream principal's permissions |
| CG host MCP key                 | Use MCP for enabled routes assigned to one Host                             |
| CG route bearer token           | Use MCP for exactly one enabled route                                       |

Private protocol reply contexts and pairing sessions are server-only state,
not Agent capabilities. [Channel adapters](01-channel-adapters.md) owns their
account binding, pairing authority, and lifecycle.

These credentials are not interchangeable. A channel sender ID is identity data;
authorization is decided by the route sender policy, not by the message body.

## Administrator and credential handling

Management uses one administrator account configured by `CG_ADMIN_USERNAME`
and `CG_ADMIN_PASSWORD`; neither has a default. The username is 1-128 characters
and the password 8-4096 characters. The same length constraint applies to
local and remote deployments. Configuring only one or an invalid length
refuses startup. Without either, management and login return HTTP 503 while
health/status remain available. Legacy administrator bearer tokens and environment
names are unsupported. MCP Host/route keys remain independent bearer credentials.

`POST /api/v1/auth/login` accepts JSON `username` and `password` with
`X-Channel-Gateway-Request: 1`. Password verification runs off the service event
loop against an in-memory salted scrypt hash; no password is persisted or returned.
Wrong credentials return the same HTTP 401 diagnostic. Login attempts are limited
to five per source address per 60 seconds; excess returns HTTP 429 with
`Retry-After: 60`. Source tracking and simultaneous sessions have bounded capacity.

Successful login sets a random host-only `channel_gateway_session` cookie scoped to
`/api/v1`, with HttpOnly, SameSite Strict, and Secure on HTTPS requests. It expires
12 hours after login. The server retains only its hash and process-local session
state; a process restart or credential change followed by restart invalidates
sessions. Re-login replaces that browser's current session. The response contains
`username` and `csrf_token`, not the session credential.

`GET /api/v1/auth/session` restores the current session's username and CSRF token.
Management requires a valid, unexpired cookie. Unsafe management methods require
that session's `X-CSRF-Token`; mismatches return HTTP 403. Login and management
reject a browser Origin different from the request origin. Cross-origin browser
clients are unsupported. `POST /api/v1/auth/logout` requires CSRF, invalidates the
server-side session, clears the cookie, and returns HTTP 204. Replaying the old
cookie cannot restore access. Invalid or expired sessions return HTTP 401.

The console restores login after refresh, keeps the CSRF token in memory, and
clears management state on sign-out or session expiration. It stores neither the
password nor session credential in browser storage. An administrator session,
password, or CSRF token grants no MCP access.

Connection tokens, application secrets, and callback secrets are write-only through management views.
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
channel account and conversation scope. `sender_policy` is `allowlist` by default,
requiring a nonempty `allowed_user_ids`, or explicitly `any`, requiring an empty
list and accepting any verified platform sender within the source scope. A
specific private allowlist route authorizes exactly its conversation user.
An all-conversation route can allow selected sender IDs or any sender.

`access_mode` is `restricted` by default. Restricted group replies require review;
restricted private replies require an explicit automatic-reply opt-in. `full`
retains the selected a13n Agent's configured tools and enables automatic replies,
including group replies. It grants neither gateway management access nor channel
destinations outside the route. For generic MCP clients it changes reply policy,
not client execution permissions. Native platform reply limits still apply.
The console exposes these choices separately; neither is silently enabled on
existing routes. Full access with any sender trusts every sender who can reach
that channel scope to invoke the selected Agent's configured capabilities.

Supported messages without an authorized route are persisted for administrator
identity discovery. They have no Agent capability, do not wake a Host, and are
excluded from MCP. Management views show verified account, conversation and
sender IDs and received text, without private protocol reply contexts.

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
route. Administrator sessions grant no MCP route access. Tools also recheck the
route at invocation. Tokens convey neither arbitrary destination selection nor
the right to manage configuration. [Agent tools](04-agent-tools.md) owns tool
behavior and a13n connection binding.

## Host capabilities

Creating an `a13n` or `mcp` Host reveals an independent random `mcp_key` once.
`POST /connections/{id}/mcp-key` rotates it, invalidating the previous key. Only
its SHA-256 hash is persisted in `host_keys`; ordinary connection views expose
neither key nor hash. Existing Hosts gain a key by rotation without changing
upstream credentials or route tokens.

A host key authorizes all currently enabled routes assigned to that Host, and
requires explicit `route_id` for reads and replies. Every invocation rechecks the
credential, Host assignment, route status, and current sender policy. A key
for another Host, an administrator session, or an upstream credential conveys no
access. Route keys retain their narrower scope and cannot select another route.
Host keys cannot configure routes or select arbitrary channel destinations.

A Host represents one client trust boundary: issue separate Hosts and keys to
clients that must not share conversations. An a13n Host key spans its assigned
conversations; retain dedicated route credentials for conversation-isolated
thread tools. Rotation does not cancel an operation already in flight.

## Privacy boundary

Separate conversation threads, scoped tools, allowlists, and administrator group
approval reduce exposure. They do not guarantee that an Agent with access to
other private resources will never disclose those resources. The a13n connection
tool override does not remove built-in toolsets, plugins, skills, or other Host
resource permissions. Administrators must select an Agent whose remaining access
is appropriate for the conversation. Prompts and upstream event filters do not
replace these authorization boundaries.
