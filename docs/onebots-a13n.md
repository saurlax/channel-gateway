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
   in **Basic information**. In **Routing → Sources**, each row
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
   In **Connections**, select **Readable channels**.
   Each readable channel grants access to all received private/group conversations
   for that bot account; it does not trigger the Agent or authorize replies there.
   Select **All channels** to include future accounts, or select none to deny reads.
   Configure the shared Host MCP connection for message sends, without a dedicated
   route binding. Each conversation gets its own thread
   unless you select another [thread delivery policy](#thread-delivery).
6. Send a text message as an authorized user. **Messages** lists conversations, including
   those without a route. Select **View conversation** to open its chat history
   page. Received messages appear on the left and Agent replies on the right,
   with timestamps, delivery states, and diagnostics. Use **Load earlier messages**
   for older history and **Back to messages** to return to the conversation list.
   Authorized private and group replies are sent automatically without review.

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

Choose **Conversation continuity** in **Routing → Destination**. Options are
include bounded history with shared environments, ongoing chats, personal
assistance in groups, independent tasks, and cross-source collaboration. Each option includes a description; the selected
option's description remains below the selector.

- **Bounded history, shared environment** is the default. Each message gets a
  fresh thread with the latest channel history, reusing the same conversation's
  environment mounts. Files stay in the environment while model context starts
  fresh. Messages execute in order within each conversation. The first message
  follows the Agent's normal environment setup. Environment lookup or mount
  failures stop delivery rather than silently creating an empty replacement.
- **Continue each private chat or group** keeps one thread for each source channel
  account and private/group conversation. This retains upstream model history.
- **Keep each group member separate** keeps a thread for each sender inside a source
  conversation. The same person in another group or account uses another thread.
- **Start fresh for every message** starts a fresh thread and session for every message.
  An Agent with a default environment template can consequently create a new
  environment for each message. Retrying a delivery reuses the same request and
  does not intentionally create another thread.
- **Share context across all sources** shares one thread across all sources on this route. Select an
  existing thread from the Host list, or leave **Created on first message** selected.
  All admitted sources share its upstream history; removing a source does not
  erase history already delivered there.

Replies stay attached to their original source conversation under every policy.
Wait for queued/submitted inputs to finish before changing the Agent or thread
policy. On upgrade, existing default conversation routes switch to bounded
history once, retaining their previous thread as the environment origin. Explicit
sender, fixed, and per-message policies remain unchanged. Frozen deliveries keep
their original request. No upstream thread or environment is deleted.

Set **History message limit** in **Routing → Destination**. It defaults to **30**
and accepts **0–100**. It counts previous incoming and confirmed sent messages
combined, excluding the current message. **0** omits history. Only messages from
the current conversation within the route's read permissions are attached; an
empty readable-channel grant omits history. This is a message-count limit, not
a token budget. Media appear as counts with source IDs for authorized tool reads.

Continuous-thread choices keep upstream context regardless of the attached
history limit. Current a13n Service automatically compacts at its configured token
threshold (90% of the model context window by default). CG does not invoke a
manual compaction API. Use bounded history when a strict channel-history count is
required; use continuous threads when preserving model/tool state is preferred.

To clear a route's Agent context, open **Routes → Edit → Reset conversations**
and confirm. Wait for all of that route's pending inputs to finish first.
This resets every conversation and sender thread on the route, including
automatically created conversation children. Future messages start fresh a13n
threads and sessions; gateway history and previous upstream threads remain
available. Other routes are unaffected. An explicitly selected fixed shared
thread must instead be changed in the destination selector; per-message
delivery already starts fresh. A new Agent revision applies to new runs even in
an existing thread, so resetting is for clearing history rather than publishing
prompt changes.

### Messages during execution

Bounded-history delivery always processes each conversation in order and hides
this selector. The following options apply to the other thread policies.

In **Routing → Destination → Messages during execution**, choose:

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

For an Agent that shares one Host trust boundary, configure **one MCP connection**
in a13n with the Host's MCP key. Copy it when creating the Host, or obtain a new
one through **Agent hosts → Manage → Rotate MCP key**. Use the key as the
**bearer credential**. The equivalent a13n create body is:

```json
{
  "type": "mcp",
  "name": "Channel Gateway",
  "config": { "url": "https://GATEWAY/mcp/", "tools": null },
  "auth": "bearer",
  "credential": { "token": "HOST_MCP_KEY" }
}
```

Select that connection and the desired tools in the Agent interface. The gateway
exposes one fixed tool inventory, including the additional
[QQ tools and operations](qq-platform.md). Call `discover(resource="conversations")` to discover
current authorized destinations, then pass the selected `id` as `route_id` to
reads, sends, and actions. For an incoming channel message, use its gateway-verified
`route_id`. Adding groups, private chats, or guild channels only changes route
data; it does not require another connection, new tools, or per-conversation Tool
Search groups. Native resource operations use the same fixed tools with an
`operation` argument.

Use the shared Host-key connection for all message sends and pass the verified
concrete `route_id` explicitly. Exact, multi-source and wildcard sources all use
the same tools. QQ proactive delivery requires `message.proactive`; passive
sending remains available without that grant.

Dedicated route credentials are optional for narrower access. One concrete source
can bind a dedicated MCP connection using that route's token. This is optional
isolation, not a requirement for sending or adding groups.
An Agent configured with both shared and dedicated connections still has the
shared connection's full Host authority.

Choose an Agent whose remaining permissions are appropriate for all conversations
assigned to the Host. CG does not override the Agent's connection tools, built-in
toolsets, plugins, skills, or access to other Host resources. The Host key does not
broaden route sources, sender policy, read grants, or action grants. See the
[Agent tool contract](../spec/04-agent-tools.md) for exact tool selection and scope.

When rotating a Host key, replace the bearer credential in its shared connection.
For a dedicated route connection, rotate the route token instead. Wait for active
input to finish before changing a route MCP binding. Never put either key in
`mcp_headers`; use the bearer credential field shown above.

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
workspace. Retrying resumes the recorded command or polling operation.

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
