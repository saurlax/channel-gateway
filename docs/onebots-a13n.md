# Set up the OneBots and a13n bridge

This guide connects one bot account to an a13n Agent using HTTP API calls and
signed HTTP callbacks. Add further accounts separately. Other OneBot transports
are described below; see the [native QQ/Weixin guide](official-channels.md) for
direct official platform connections. See the
[channel contract](../spec/01-channel-adapters.md) for protocol and message limits.

## Connect a channel and host

1. Set `CGW_ADMIN_USERNAME` and `CGW_ADMIN_PASSWORD` to your administrator
   account and password of at least 8 characters.
   Set `CGW_DATA_DIR` to a private persistent directory and start
   `make serve`. Open the console and sign in with that account and password.
2. In **Channels**, select **Add channel**, choose **OneBot reverse HTTP**,
   and add one account. The API
   base is the complete OneBot v11 account endpoint, for example
   `https://onebots.example/qq/ACCOUNT/onebot/v11`. Enter the account's
   `access_token`, its event `self_id`, and a signing secret of at least 16
   characters. Credentials are write-only through management views.
3. Select **Manage** on the channel row and copy its reverse HTTP webhook URL.
   Configure that account's OneBot v11 protocol with `use_http: true`, the matching `access_token`,
   `secret`, and the webhook URL in `http_reverse`. OneBots signs the raw JSON
   with `X-Signature: sha1=<HMAC-SHA1>`. Notice and heartbeat events are retained
   in the private channel-event journal without triggering the Agent.
   See [OneBots](https://github.com/lc-cn/onebots).
4. In **Agent hosts**, select **Add agent host** and choose **a13n API + MCP**, and add a host using
   its API base, e.g. `https://a13n.example/api/v1`, and a workspace API key
   permitted to read Agents, threads, and runs
   and run the selected agent. No interactive login session is used.
5. In **Routes & access**, select **Add a route**. Set its name and enabled state
   in **Basic**. In **Routing → Sources**, each row
   selects a channel and a user or group. Search observed conversations or enter
   a platform ID and select its user/group type. Use the plus button for another
   row and the row's delete button to remove it. **All channels** includes channels
   added later. **All conversations** matches every supported conversation type;
   choose **All channels / All conversations** to receive all supported messages
   across current and future accounts. **All private chats** matches only private
   conversations, while **All groups** matches only groups. Existing private-only
   routes retain that scope until edited. These sources determine which incoming
   messages trigger the Agent; older unmatched messages are not replayed after saving.
   In **Routing → Destination**, choose the Agent Host and select an Agent from
   its list. Search by name or ID; use **Load more** for later pages. Choose
   **Conversation continuity** (one thread per private chat or group by default).
   In **Permissions and connections**, select **Readable channels**.
   Each readable channel grants access to all received private/group conversations
   for that bot account; it does not trigger the Agent or authorize replies there.
   Select **All channels** to include future accounts, or select none to deny reads.
   Configure a route-token MCP connection for message sends, without a dedicated
   route binding. Each conversation gets its own thread
   unless you select another [thread delivery policy](#thread-delivery).
6. Send a text message as an authorized user. **Messages** lists conversations, including
   those without a route. Select **View conversation** to open its chat history
   page. Received messages appear on the left and Agent replies on the right,
   with timestamps, delivery states, and diagnostics. Use **Load earlier messages**
   for older history and **Back to messages** to return to the conversation list.
  Authorized private and group replies are sent automatically without review.
  A terminal a13n failure also queues a short gateway failure notice in the source
  conversation. Known causes such as service rate/quota rejection are summarized;
  private upstream error bodies and Agent output are never forwarded. A notice
  is queued once per input, including across restart and manual retry, and remains
  subject to current route access and the platform's reply window. Older failed
  inputs are not replayed or backfilled with notices.

The signing secret must match OneBots' configuration. Start with a private test
conversation and an Agent whose permissions are appropriate for its participants.
The [access contract](../spec/02-identity-and-access.md) explains the authorization
and privacy boundaries; the [route contract](../spec/03-routing-and-delivery.md#conversation-routes)
explains conversation and thread binding.

Select **Manage** on a channel or host row to replace API tokens and signing
secrets. Connection URLs cannot be edited; create another connection for a new
endpoint. Select **Edit** on a route row to change its **Sources** or **Destination**,
edit readable channels and MCP binding, toggle **Enable route**, or select
**Delete route** and confirm. Deletion removes the binding and its tokens while
retaining administrator conversation history. Recreating it starts a new thread.
Disable the route to stop further work;
this cannot retract a sent message or interrupt an external request already in
flight.

## Thread delivery

Choose **Conversation continuity** in **Session → Context**:

- **Continue each private chat or group** (default): each concrete source
  conversation keeps its own coordinator Thread.
- **Keep each group member separate**: each sender keeps a separate Thread
  within that conversation, without linking people across groups or accounts.
- **Start fresh for every message**: each triggering message starts a new Session.
- **Share context across all sources**: the route shares a Thread across sources.
  Select an existing Thread or **Created on first message**. Removing a source
  cannot erase context already submitted to a shared Thread.

In **Session → Context**, **Session idle timeout (minutes)** defaults to **60** for new a13n routes. Empty
retains the Session indefinitely; **0** starts fresh whenever idle. Accepted values
are **0 or greater**, with no gateway-imposed upper limit. An active root Run, descendant or pending coordinator input keeps
its Session regardless of timeout. Per-message scope always starts fresh. Pinned
existing Threads require indefinite retention and keep their existing memory and
environment selection; choose create-on-first-message for new selection settings.

In **Session → Execution environment**, **Environment scope** is independent of conversation continuity:

| Option | Behavior |
| --- | --- |
| **Global shared environment** | Participating routes on the same Host and Agent share one environment set across chats and Sessions |
| **One environment per conversation** (new-route default) | Each bot account's concrete group or private chat keeps its environment across senders and Session generations |
| **Follow session lifetime** | Every new coordinator Session provisions its own environment; continuing Runs reuse it |

A bot account with several chats does not constitute one conversation. Separate
sender Threads can share their chat's environment. Global sharing exposes files
to every participating execution, even with separate model history and memory.
Use separate work directories for concurrent tasks. Fixed Threads spanning
multiple conversations require global or Session environments. Lookup/mount
failures stop delivery instead of creating an empty replacement. Subagent
shared/dedicated environment edges remain configured in the Host.

In **Session → Context**, **History message limit** defaults to **30** and accepts **0–100**; **0** omits
automatic history. New Sessions receive that many previous authorized incoming
and confirmed sent messages, excluding the current trigger. Continuing Sessions
receive only new context since their last accepted input, bounded to 100 records.
A full delta identifies possible missing context and a gateway history-read
reference. Earlier history remains readable through authorized pagination. Media
appear as source handles/counts. These are message counts, not token budgets;
a13n still owns automatic compaction.

In **Memory**, enable **Conversation memory** (on by default for new non-fixed a13n routes) to
reuse scoped writable memory across Session changes. Optionally enter **Global
Memory Spaces** with the searchable multi-select; choices show Host Space names
and read-only access. Pagination and retry preserve current selections. Changing
the Host clears its selected Spaces. Host Space defaults are replaced. Root and named-child revisions must have
only read-only standalone memory defaults; named child revisions must be pinned.
CG refuses unsafe or uninspectable configurations without editing the Agent. Fixed shared Threads require **Shared memory across sources**; existing
source Spaces are not merged. See [memory configuration](session-context.md#keep-memory-across-sessions).

Wait for queued/submitted inputs to finish before changing Session, environment
or memory policies. Upgrades retain existing continuing Threads; legacy `window`
routes become conversation scope with zero-minute retention and conversation
reused environments. Existing per-message routes retain Session environments.
Frozen deliveries retain their original requests. No upstream environment,
Thread or memory is deleted or automatically cleaned during migration.

To clear a route's Agent context, open **Routes → Edit → Session → Reset conversations**
and confirm. The reset takes effect immediately, even while an Agent is running.
This resets every conversation and sender thread on the route, including
automatically created conversation children. New deliveries start fresh a13n
threads and sessions. Inputs already prepared for a thread keep their original
destination, and active runs can still use tools and reply. Gateway history,
scoped memory and previous upstream threads remain available. Global/conversation
environments are retained; their initial provisioning must still be confirmed
before reuse. Session-scoped environments are replaced for the next Session.
Other routes are unaffected. An explicitly selected fixed shared
thread must instead be changed in the destination selector; per-message
delivery already starts fresh. A new Agent revision applies to new runs even in
an existing thread, so resetting is for clearing history rather than publishing
prompt changes.

### Messages during execution

The busy policy is independent of conversation scope, keepalive and environment selection.

In **Session → Context → Messages during execution**, choose:

- **Join current execution** (default): CG immediately submits `steer`. Compatible
  messages join the active a13n run at its next safe boundary.
- **Queue for the next run**: CG waits for earlier work in the destination thread
  to finish, then submits messages in order using `next_run`.

This is a per-route setting, inherited by its conversation children. Independent
threads remain independent. Shared threads apply the policy across their source
conversations. Generic MCP clients own their execution and do not show this setting.
The immediate mode continues the active task; it does not cancel the run. Waiting
on a question/approval still follows a13n's resume rules.

The management field is `busy_policy: "steer" | "queue"`; missing values default
to `steer`, including older routes. You can change it while a run is active.
The new choice affects only input not already frozen for submission; retrying a
lost receipt preserves its original body and idempotency key.

Multiple messages can join one run. Each input tracks the run assigned to its
inbox entry. Completion never sends final text. An Agent uses `send_message` with
the gateway-verified route ID when a channel response is warranted.

## Other OneBot transports

Choose the transport in **Add channel**:

- **Forward HTTP**: enter the account HTTP API URL, access token, and self ID.
  This sends only; it cannot receive messages or automatically start an Agent run.
- **Forward WebSocket**: enter the complete account universal WS(S) URL, access
  token, and expected self ID. CG connects to it for both events and API calls.
- **Reverse WebSocket**: enter the access token and expected self ID. Save, open
  **Manage**, and copy the gateway WebSocket URL into the bot's reverse WebSocket
  configuration. Its handshake must include `Authorization: Bearer <token>`,
  `X-Self-ID: <configured ID>`, and `X-Client-Role: Universal`.

The four options do not discover or combine several bot accounts. Enable the
matching transport in your OneBots account configuration. Reverse HTTP also
requires the signing secret; WebSocket modes do not. The channel table displays
status without endpoint URLs; account addresses and generated inbound URLs are
available through **Manage**. Inbound addresses use the origin used to open
the console.
When behind a proxy, open the console through its public HTTPS origin to obtain
public HTTPS/WSS addresses, and enable WebSocket proxy upgrades. Route access and
automatic reply delivery remain the same for every transport.

Generic clients such as Codex use the [Agent Host guide](agent-hosts.md) instead
of a13n API delivery.

## MCP tools in a13n

The bridge works without MCP. Its MCP Streamable HTTP endpoint is
`https://GATEWAY/mcp/` (including the trailing slash). It exposes:

- `discover`: list authorized conversations and inspect capability/operation schemas.
- `read`: read messages, received images/attachments, sanitized events, and native resources.
- `send_message`: queue text, supported media/cards, or stream fragments.
- `execute_action`: queue reactions, recalls, typing, and native mutations.
- `get_operation`: inspect confirmed or uncertain outcomes in either durable ledger.

See the [tool guide](agent-tools.md) for exact request examples and migration.
Refresh the MCP inventory, enable these five tools, and update Agent instructions
that reference removed tool names. Existing credentials and route grants persist.

See [images and reactions](images-and-reactions.md) for formats, bounds, native
a13n attachments, and capability differences. All channel replies, including
ordinary text, use `send_message`. Final Agent output is never forwarded.
QQ proactive sends require `message.proactive`; reactions use `message.react`.
All required tools must be enabled in the Agent Host.

Create a route, copy its generated token and MCP URL from the one-time dialog,
and configure a corresponding MCP connection in a13n. Use the token as the
**bearer credential**. The equivalent a13n create body is:

```json
{
  "type": "mcp",
  "name": "Channel Gateway route",
  "config": { "url": "https://GATEWAY/mcp/", "tools": null },
  "auth": "bearer",
  "credential": { "token": "ROUTE_MCP_TOKEN" }
}
```

Select that connection and the desired tools in the Agent interface. A route
token covers its configured sources, readable channels and action grants. Call
`discover(resource="conversations")`, then supply the returned concrete `id` as
`route_id` to reads, sends and actions. A single exact source may omit `route_id`;
multi-source/wildcard routes require a concrete child. Read-only handles always
require an explicit ID and cannot send. Adding conversations under the same
route uses the same connection and five tools. Separate route configurations
need separate tokens, even when they share a Host or Agent.

One concrete source can optionally record its dedicated a13n MCP connection ID
in the route form. The connection must use that route's token; this binding does
not add tools, broaden access or replace the Agent's configured connection list.
An Agent configured with several route connections has their combined configured
capabilities, so select an Agent appropriate for every admitted sender.

Rotate credentials in **Routes → Edit → Permissions and connections → Route
token** and update the corresponding a13n bearer credential. Host-wide MCP keys
are no longer issued or accepted; upstream a13n workspace API keys are separate.
Never put a route token in `mcp_headers`.

## Quiet group participation

Full-message collection does not require a reply to every message. Configure the
Agent with ordinary text output, without a `reply`/`text` structured-output schema
or output-format retry prompts. Use `send_message` only when a channel response is
warranted. For quiet observation, complete without any message/action tool call.
A private final completion note may end the run normally; CG never publishes it.
Do not require a truly empty model response, because ordinary a13n text output
expects a final result.

Stay silent for ordinary group discussion, quoted requests addressed to others,
and remarks about the bot. Respond through tools when mentioned or clearly
addressed with a request. Verified `bot_mentioned` is a relevance hint, not new
authority. Private chats normally warrant tool-delivered answers. Never treat
partial text from a failed run as a channel reply.

`read(resource="messages")` combines incoming and confirmed outgoing messages, with
`direction`, `history_id`, and `created_at`. Outgoing media has safe descriptions,
not incoming-media read handles. Unconfirmed sends and non-message actions do not
appear as sent conversation content. Existing read grants remain authoritative.

## Diagnose delivery

In **Messages**, select **View conversation**. Inspect incoming messages and
outgoing Agent replies together, with delivery diagnostics. Each concrete
conversation has its own row, including those created by an all-conversation
route. Use **Load earlier messages** to read older history.
If a message is `submitted`, check the assigned run in a13n; a waiting run needs
to be resolved there. For a failed event, fix connection or Agent configuration
and select **Retry**. Keep replacement a13n keys within the same principal and
workspace. Retry checks the Host before replacing an accepted input. A confirmed
failed or cancelled execution is submitted again using current configuration;
an uncertain submission keeps its original request and idempotency key, and
active or completed work is only polled. If status cannot be confirmed, Retry
leaves the event unchanged. Retrying an unsuccessful execution can repeat effects
it already performed; inspect any existing Agent replies first.

Outgoing entries in the same conversation show their text and delivery status. A `queued` reply is waiting for sending; a `sent` reply has a confirmed
channel receipt. If a reply is `unknown`, inspect the channel before deciding whether
another message is needed; CG does not automatically resend it. The
[delivery contract](../spec/03-routing-and-delivery.md) defines all states and
completion/retry boundaries. When upgrading from manual review, old unreviewed
drafts are blocked rather than sent; send a new input to obtain a current reply.

## Deployment boundaries

Use **one process and one replica**, HTTPS for remote endpoints, and a private
persistent state directory (`0700`). Protect this volume: it contains credentials
and conversation history without encryption at rest. See
[runtime scope](../spec/00-overview.md#runtime-and-persistence) and
[credential handling](../spec/02-identity-and-access.md#administrator-and-credential-handling).

Consult the [specification index](../spec/README.md#compatibility-boundary) for
the supported baseline and excluded integrations before configuring a new source.

## UI provenance

The console uses official shadcn **sidebar-07** and **login-01** blocks with
application-specific labels and controls, official components, and the official
neutral theme. It introduces no separate visual theme. Sources:

- [sidebar-07 registry](https://ui.shadcn.com/r/styles/new-york-v4/sidebar-07.json)
- [login-01 registry](https://ui.shadcn.com/r/styles/new-york-v4/login-01.json)
- [neutral theme](https://ui.shadcn.com/r/colors/neutral.json)
