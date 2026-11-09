# Identity and access

## Wake decision authority

Only authenticated administrators select the route's optional wake decision Agent.
It runs on the same a13n Host using that Host's upstream API credential, with
bounded authorized same-source context. CG passes neither route tokens nor origin
handles. The decision does not broaden source, sender, read or reply authority.
Agent-configured tools and capabilities remain authoritative; use a dedicated
Agent without side-effect tools. No model-provider credentials are stored by CG.
The [delivery contract](03-routing-and-delivery.md) owns failure and retry behavior.

## Authority model

The baseline has one administrator role, verified platform sender allowlists,
and route/conversation-scoped Agent capabilities. It does not provide full RBAC,
cross-channel identity linking, or general information-flow enforcement.

| Credential                      | Authority                                                                   |
| ------------------------------- | --------------------------------------------------------------------------- |
| CG administrator session cookie | Manage connections, routes, message delivery, and reply records           |
| OneBot access token             | Authenticate account API calls or a universal WebSocket session             |
| QQ AppID / AppSecret            | Obtain API tokens and verify official callbacks                             |
| Slack bot token / signing secret | Bot Web API and installed workspace event verification |
| Lark app secret / verification token / encrypt key | Tenant bot API and signed encrypted callbacks |
| Discord bot token | Bot REST and Gateway identity |
| Weixin bot token                | Poll and send for one paired iLink bot account                              |
| OneBot callback signing secret  | Verify incoming account event bytes                                         |
| a13n workspace API key          | Submit input and read execution within the upstream principal's permissions |
| CG route bearer token           | Use MCP for the enabled conversations selected by one configured route      |

Private protocol reply contexts and pairing sessions are server-only state,
not Agent capabilities. [Channel adapters](01-channel-adapters.md) owns their
account binding, pairing authority, and lifecycle.

These credentials are not interchangeable. A channel sender ID is identity data;
authorization is decided by the route sender policy, not by the message body.

## Administrator and credential handling

Management uses one administrator account configured by `CGW_ADMIN_USERNAME`
and `CGW_ADMIN_PASSWORD`; neither has a default. The username is 1-128 characters
and the password 8-4096 characters. The same length constraint applies to
local and remote deployments. Configuring only one or an invalid length
refuses startup. Without either, management and login return HTTP 503 while
health/status remain available. Legacy administrator bearer tokens and environment
names are unsupported. MCP route keys remain independent bearer credentials.

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

A [route](03-routing-and-delivery.md#conversation-routes) has separate activation
and message-read scopes. Sources select incoming conversations that trigger the
Agent and authorize replies to those conversations. `read_channel_ids` explicitly
authorizes incoming message and image reads, and confirmed bot-message history,
across every private/group conversation received
by the selected channel accounts, regardless of source matching or sender policy.
An empty list grants no reads; `["*"]` includes current and future channels.
Missing/null retains legacy source-and-sender-scoped reads for existing API clients
and stored routes. Channel read grants never create routing bindings, wake an
Agent, or grant reply authority. The console configures them in Connections,
defaulting to the source channels on an existing unconfigured form; saving is an
explicit update.

The configured source scopes govern activation. A wildcard channel includes future channel
accounts; it does not link people across platforms. The same user/group ID is
matched literally on each selected account. Group selection authorizes the group
as a destination; sender restrictions are checked separately.
An explicit all-conversations source authorizes every supported conversation kind
on its selected accounts. Existing private-only sources are never expanded automatically.
`sender_policy` is `allowlist` by default at the API boundary,
requiring a nonempty `allowed_user_ids`, or explicitly `any`, requiring an empty
list and accepting any verified platform sender within the source scope. A
specific legacy QQ/OneBot/Weixin private allowlist route authorizes exactly its
conversation user. Slack/Lark/Discord private conversation IDs identify containers,
so their sender allowlists use separate verified user IDs.
An all-conversation or multi-source route can allow selected sender IDs or any
sender. The console uses Sources as the complete trigger condition and submits `any`
with an empty sender list when creating or saving a route. It exposes no separate
sender-policy control. Existing API allowlists remain enforced until explicitly
replaced by a management update. Removing a source revokes its child capability and blocks
pending work atomically; other selected sources remain authorized.

Thread delivery policy does not change activation, read grants, or reply
authority. Fixed delivery intentionally combines the upstream history of all
source conversations on its route. Per-sender delivery separates threads only
within each source conversation; it does not link identities across channels.
Host resource discovery is administrator-only and uses the selected Host's
server-side key, without exposing that key or upstream message history to the
selector. Existing fixed destinations are checked against the same Host.

Agent tool permissions are configured only in the Agent Host interface. CG
always preserves the Agent's configured connection tools and exposes no route
tool-permission mode. Historical `access_mode` values are ignored; the management
API rejects that removed field. Readable channel grants, source reply authority,
and explicit [platform resource action grants](06-qq-platform.md#explicit-action-grants)
remain enforced independently by CG. Resource grants are not Agent tool selection.
A route token grants no CG administrator access. QQ resource
administration requires its separate, initially empty route grants and upstream
permissions. Authorized source replies are sent automatically;
there is no per-reply review or automatic-reply opt-in. Native platform reply
limits still apply. Select an Agent whose configured capabilities are appropriate
for every sender admitted by the source scope.

All bounded authenticated arrivals are privately retained regardless of routes
and sender allowlists. Supported messages without an authorized route are also
persisted for administrator identity discovery. They do not wake a Host or
create an Agent capability. An enabled route can subsequently authorize MCP
reads of observed history under the current read grant, without replaying old inputs.
Explicit channel reads include retained history from archived/deleted bindings;
legacy reads keep the original source/sender and archived-history exclusions.
Raw packets and their private protocol
metadata remain excluded from MCP. Management views show verified account, conversation and
sender IDs and received text, without private protocol reply contexts.

Authorization is checked at ingress, when advancing input delivery, during MCP
access, and before sending a reply. Removing a legacy allowed sender blocks pending inputs from that sender and hides
their messages from legacy source-scoped MCP reads. An explicit account-wide
read grant continues to authorize those messages independently. Replies
linked to such an input are blocked before sending. MCP-created replies retain their destination authority in the route credential
and current route state. Supplying a gateway-issued `origin_context` additionally
links the initiating source through private provenance, independently of the
legacy `replies.event_id` passive anchor. The handle must belong to a submitted
input on the same Host and a source authorized by the caller. A completed input
also requires verified active coordinator or descendant work. Source
revocation blocks attributed queued sends. Reads project requester/reason only
under current source-read authority; restricted provenance conveys no private
source fields. Unattributed clients keep their existing destination authority.
See [session context and memory](09-session-context-and-memory.md) for memory and
shared-environment boundaries.

Disabling a route blocks new work and invalidates its MCP authority. It does not
interrupt an external request already in flight, erase history, retract a sent
message, or cancel an a13n run. Re-enabling a route restores its still-current
token; rotate the token when the previous credential must remain revoked.

## Resource deletion

Deleting a channel also removes its explicit ID from active readable-channel
lists atomically. Wildcard grants continue to cover only currently present channels.
Other readable accounts remain authorized.

`DELETE /api/v1/connections/{id}` requires an administrator session and CSRF token.
For an Agent Host, it removes upstream credentials,
disables every assigned route including concrete children, and blocks queued or
submitted inputs and queued replies in the same short transaction. It does not
remove channel accounts, cancel upstream runs, or retract external requests
already in flight. Retained routes cannot regain authority while their Host is
missing. Route MCP credentials are rechecked against current resources;
deleting the Host invalidates its key and disables route capabilities. Channel
deletion has the [channel lifecycle](01-channel-adapters.md#account-binding).

`DELETE /api/v1/routes/{id}` revokes that route and, for an all-conversation parent,
its concrete children. The [delivery contract](03-routing-and-delivery.md#conversation-routes)
owns binding removal and history retention. Deletion never grants access to old history through a replacement route's legacy
source-scoped MCP capability. Explicit readable-channel grants authorize retained
incoming channel history independently of deleted routing bindings. Console deletion starts
inside channel and Host management dialogs or on the route edit page using the shared red outline button
and official Alert Dialog confirmation; ordinary row actions remain compact.

## Route capabilities

Creation and rotation reveal a random route token once. Storage retains its hash,
not the token. Rotation invalidates the previous token; calls rechecking that
token can fail after rotation. Ordinary route views never reveal it.

The MCP endpoint rejects browser origins and requires a valid token for an enabled
route. Administrator sessions grant no MCP route access. Tools also recheck the
route at invocation. Tokens convey configured channel reads, source reply authority,
and explicitly configured platform resource grants, never
arbitrary send destinations or the right to manage configuration. [Agent tools](04-agent-tools.md) owns tool
behavior and a13n connection binding.

## Route-only MCP credentials

Only enabled-route tokens authenticate MCP. Host creation returns no `mcp_key`,
and the removed `POST /connections/{id}/mcp-key` endpoint returns 404. No Host form
contains a CGW MCP endpoint or credential-generation control. Startup removes the
legacy `host_keys` table; previously issued Host keys cannot authenticate even
before cleanup because no authentication path looks them up.

A route token grants only that configuration's current read grants, source reply
authority and platform actions, including enabled concrete children. It cannot
select another route merely because both share a Host, Agent or channel. Its
explicit read grants can still authorize account-history handles outside its
activation sources; those handles are read-only. A single concrete source may
omit `route_id`; multi-source and wildcard tokens require an authorized concrete
child. Every tool call and queued send rechecks current authority and resources.

Generate/rotate tokens only through route creation or the route rotation action.
Store only hashes, show the generated value once read-only and never accept
user-selected capability values. Rotation invalidates the previous credential
without cancelling an operation already in flight. Upgrades preserve existing
route credentials and upstream API keys; operators must replace retired Host
keys with route-issued credentials in their clients. Sharing a Host does not
combine token permissions. Clients configured with multiple route connections
have the combined authority explicitly granted to those connections.

## Privacy boundary

Separate conversation threads, scoped tools, and allowlists reduce exposure. They do not guarantee that an Agent with access to
other private resources will never disclose those resources. CG does not change built-in toolsets, plugins, skills, connection tools, or other
Host resource permissions. Administrators must select an Agent whose remaining access
is appropriate for the conversation. Prompts and upstream event filters do not
replace these authorization boundaries.

## Native history authority

`history.read` separately authorizes native history on a concrete source route
for Slack, Lark and Discord. `read_channel_ids` continues to grant only collected
gateway records, including read-only conversation handles. Native history is
never silently substituted for gateway history, or vice versa. Native search
remains unsupported; `history.search` is reserved and grants no callable search.

Native cursors and asset provenance bind the complete route authority and account
credential snapshot, expire after 15 minutes, and are rechecked before results
return. Caches are private, pruned by expiry and bounded per account. Native
history does not enter the received-message archive, activate an Agent, or become
visible through another route's account-read grant. Native results and sender
filters stay within the exact route; arbitrary native destinations are refused.
A cached native message can become an action target only while its history grant
and provenance remain valid. Output grants are also rechecked when reading
operation results and intermediate upload effects.

## GitHub authorization

GitHub adds a channel-level, default-deny caller/repository/action policy before Host activation, independent of source routes and upstream token scopes. The [GitHub contract](08-github.md) owns its numeric IDs, owner-only mode, revalidation and secret isolation.
