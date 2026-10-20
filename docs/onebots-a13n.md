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
   added later; **All users** and **All groups** match their respective conversation
   types. These sources determine which incoming messages trigger the Agent.
   In **Routing → Destination**, choose the Agent Host and select an Agent from
   its list. Search by name or ID; use **Load more** for later pages. Choose
   **Conversation continuity** (one thread per private chat or group by default).
   In **Connections**, select **Readable channels**.
   Each readable channel grants access to all received private/group conversations
   for that bot account; it does not trigger the Agent or authorize replies there.
   Select **All channels** to include future accounts, or select none to deny reads.
   Start with final
   run output and no dedicated MCP binding. Each conversation gets its own thread
   unless you select another [thread delivery policy](#thread-delivery).
6. Send a text message as an authorized user. **Messages** lists conversations, including
   those without a route. Select **View conversation** to see incoming text and
   Agent replies in chronological order, with delivery states and diagnostics.
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
ordered by use: ongoing chats, personal assistance in groups, independent tasks,
and cross-source collaboration. Each option includes a description; the selected
option's description remains below the selector.

- **Continue each private chat or group** keeps one thread for each source channel
  account and private/group conversation. This is the default for existing routes.
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
policy. Existing bound conversations remain unchanged until you change the policy.

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

## Optional MCP tools in a13n

The bridge works without MCP. Its MCP Streamable HTTP endpoint is
`https://GATEWAY/mcp/` (including the trailing slash). It exposes:

- `read_conversation(limit=20)`: received text and image indices from the authorized conversation,
  up to 100 records. Readable-channel grants include all received conversations
  on those accounts; trigger sources only govern activation and reply destinations.
- `send_message(text)`: create a reply for that same destination. It never
  accepts an arbitrary account, conversation, or operation.
- `read_image(source_message_id, image_index=0)`: return an authorized received image.
- `send_image(image_base64, media_type, caption='')`: queue an image reply.
- `set_message_reaction(source_message_id, emoji_id, add=True)`: queue a message
  reaction when the provider supports the optional NapCat-compatible extension.

See [images and reactions](images-and-reactions.md) for formats, bounds, native
a13n attachments, and capability differences. Final-run-output routes forward
text. QQ message/image tools can run alongside final text with `message.proactive`;
guild reactions use `message.react`. OneBot image tools still require MCP replies.
All tools must be enabled in the Agent Host.

Create a route first and copy its one-time conversation access token. In a13n,
create a **separate MCP connection for this route** with that endpoint and the
route token as its **bearer credential**. The equivalent a13n create body is:

```json
{
  "type": "mcp",
  "name": "Channel Gateway team chat",
  "config": { "url": "https://GATEWAY/mcp/" },
  "auth": "bearer",
  "credential": { "token": "ROUTE_TOKEN" }
}
```

For a route with one specific channel and user/group, copy the resulting a13n
connection ID into the **Connections** tab. Multi-source or wildcard a13n routes
use final run output instead of a shared dedicated connection.
In **Connections**, choose **MCP send_message tool** if the agent should explicitly request replies;
otherwise **Final run output** queues automatic replies from the completed run
output. For QQ, enable `message.proactive` to permit proactive message/media tools
alongside those automatic passive replies. Without that grant, final-output
routes reject message tools. Configure the desired
connection tools in the Agent interface, including `read_conversation` when needed.
CG always uses the Agent's configured tool list; a route binding does not enable
connection tools automatically.

When one Agent uses several CG connections, configure a13n **Tool Search** in
proxy mode with a separate group for each connection, for example `channel_history`,
`qq_group`, and `qq_private`. This avoids same-name MCP tool collisions while
keeping credentials and destinations separate. The Agent discovers schemas with
`search_proxy_tools` and invokes the selected group's tool with `call_proxy_tool`.
Use the verified incoming `route_id` and the matching group. For QQ routes using
final output, ordinary replies belong in final text; use proactive tools for
explicit sends, media, or a later notification. Do not repeat a tool-sent answer
in final output. See [QQ delivery and limits](qq-platform.md#message-creation).

Choose an Agent whose remaining permissions are appropriate for the conversation.
CG does not override the Agent's connection tools, built-in toolsets, plugins,
skills, or access to other Host resources. See the
[Agent tool contract](../spec/04-agent-tools.md) for exact tool selection and scope.

Open a route's **Edit** page, rotate its token, copy the one-time value, and replace the
bearer credential in its a13n connection. Wait for active input to finish before
changing the MCP binding. Never put the token in `mcp_headers`; use the bearer
credential field shown above.

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
