# Connect QQ official Bot and Weixin iLink

Native channels connect directly to the platform, without a OneBots process.
Both use the existing a13n routing, sender allowlists, MCP scope, and automatic replies.
The current adapters support text; voice, images, calls, QQ guild channels, and
Weixin groups are not supported. See the [channel contract](../spec/01-channel-adapters.md)
for authentication and delivery details.

## QQ official Bot

1. Create/configure a QQ official Bot application and enable its private/group
   permissions and the platform's test or production access as appropriate.
2. In **Channels → Add channel**, choose **QQ**. Enter the name,
   AppID, and AppSecret. Select **WebSocket** for an outbound Gateway connection,
   or **HTTP callback** when you have a publicly reachable HTTPS gateway.
3. For WebSocket, the service obtains its upstream URL, identifies, heartbeats,
   and reconnects. Check the connection status in the channel table.
4. For HTTP callback, save, open **Manage**, and copy the reverse HTTP webhook
   URL into the QQ platform callback configuration. CG handles official URL
   verification and verifies event signatures using the AppSecret.
5. Create a conversation route using the platform **user OpenID** or **group
   OpenID**, and allowed sender OpenIDs. These are account-scoped identifiers,
   not ordinary QQ numbers. The bot/application must already have access to the
   conversation. Select the a13n host and Agent as described in the
   [bridge guide](onebots-a13n.md#connect-a-channel-and-host).
6. Send a text message through the supported bot interaction. Replies are sent automatically after route authorization checks, for both
   private and group conversations.

QQ replies are passive replies tied to incoming message IDs, with platform limits
on their time window and count. A long Agent run or delayed connection can miss
that window. A rejected or unconfirmed send becomes `unknown`; inspect the
conversation before deciding whether another message is needed.

Upstream reference: [Tencent QQ Bot SDK](https://github.com/tencent-connect/qqbot-nodejs).

Private and group routes are independent. An existing private route does not
authorize a group. Send a text message mentioning the bot, then open **Messages**
and use **Create route** on that group conversation. Configure its group OpenID
and allowed member OpenIDs; ordinary QQ numbers are not substitutes. A received
message without a matching route appears in history but does not invoke the
Agent. After creating the route, send a new message; discovery messages are not
replayed. QQ source and receipt message IDs are retained as opaque values, including
group message IDs longer than 128 characters.

## Weixin official iLink

1. In **Channels → Add channel**, choose **Weixin**, enter a
   name, and select **Save & pair**. The management dialog opens for that channel.
2. Select **Scan to pair Weixin**, scan the QR code in Weixin, and confirm on
   your device. If verification is requested, enter the numeric code. Generate
   a new QR if the previous one expires or verification is blocked.
3. After pairing, the channel table shows its bot ID and connection status.
   CG stores credentials on the server and starts long polling; no public HTTP
   callback is needed. Pairing availability depends on upstream account access.
4. Send a text message to the bot and open **Messages → View conversation**. Even without a route,
   CG displays the received sender ID and `private:USER_ID` conversation ID.
   The channel's `@im.bot` ID identifies the bot, not your user account.
5. Select **Create route** on the conversation row to prefill the channel and user ID.
   Choose the a13n Host and Agent ID. Save the route and send a new message. Select **Full access** only when the
   Agent should retain its configured connection tools. Discovery messages are not
   replayed; iLink replies require a newly authorized incoming context token.
6. For a personal assistant or testing, instead choose **All conversations** and
   **Any sender**. Each new private conversation gets its own thread. Select
   **Full access** to retain the Agent's configured tools. Replies are automatic
   in both permission modes.
   This allows anyone who can contact the bot to invoke those capabilities;
   use a specific user route when only your account should have access.
7. If the session expires, open **Manage** and pair the same bot account again.
   Use a new channel for a different bot. Closing the dialog cancels its pending
   QR session; restarting the gateway also removes pending QR sessions.

The management API additionally permits imported credentials with both `token`
and `self_id`. The console uses QR pairing and does not display the bot token.
The adapter follows the upstream documented 2.4.8 wire metadata and uses
`Channel Gateway/0.0.0` for its own agent identification.

Upstream reference: [Tencent Weixin iLink protocol](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md).

## Received message and event storage

CG stores every bounded authenticated channel packet in the private SQLite
`channel_events` table before text parsing and routing. This includes non-text
messages and attachment metadata, notices, bot messages, malformed packets,
and QQ Gateway control frames. No route or sender allowlist is needed to retain
an incoming packet. Duplicate arrivals are recorded individually; text delivery
remains deduplicated. Invalid callback signatures and oversized traffic are
rejected. Journal rows remain after route/channel deletion and server restart.

Messages received without an enabled authorized route do not trigger the Agent.
Once their conversation and sender are authorized, MCP can read their stored
text, including history from before route creation or while delivery was disabled.
This does not replay old inputs. Media processing and raw protocol-event reads
are not MCP tools; private context tokens never enter Agent inputs or tool results.

For QQ, receiving every ordinary group message additionally requires enabling
**receive all messages** in QQ's bot settings. CG already subscribes to the
group/C2C intent and handles `GROUP_MESSAGE_CREATE`; it cannot collect messages
QQ does not push or recover packets discarded before this journal was added.
The current route policy activates the Agent for every authorized incoming text;
there is no separate **record all, reply only when mentioned** switch yet.

See the [QQ full-message event](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)
and [channel retention contract](../spec/01-channel-adapters.md#route-independent-event-retention).

## Host binding and failure handling

To remove a channel, open **Channels → Manage → Delete channel** and confirm its
name. This stops its connection, removes its stored credentials, disables all
associated routes, and blocks pending inputs and replies. Message and reply
history remains available to the administrator. Existing upstream runs and
messages already sent are unaffected. Adding the account again requires new
credentials and routes.

The [bridge guide](onebots-a13n.md#optional-mcp-tools-in-a13n) covers optional MCP
binding, conversation tokens, input retry, and reply diagnostics for all channel types.
Channel credentials and reply context tokens are not sent to the Agent. Native
sending retains the same rule: no automatic retry when delivery may have happened.
A disconnected OneBot socket or unpaired Weixin account leaves queued replies
waiting for connectivity. A missing authorized native reply context blocks the
reply before sending. A Weixin success receipt confirms API acceptance using CG's
client ID, not a platform message ID or recipient read acknowledgement.
The official send response may omit `ret`; a successful HTTP JSON response with
no nonzero `ret` or `errcode` is accepted as success.

Use one process/replica, HTTPS remotely, and a private persistent state directory.
See [credential handling](../spec/02-identity-and-access.md#administrator-and-credential-handling).
