# Connect QQ official Bot and Weixin iLink

Native channels connect directly to the platform, without a OneBots process.
Both use the existing a13n routing, sender allowlists, MCP scope, and reply approval.
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
6. Send a text message through the supported bot interaction. Restricted group
   replies remain drafts until an administrator reviews and approves them.
   Full access explicitly enables automatic replies.

QQ replies are passive replies tied to incoming message IDs, with platform limits
on their time window and count. A long Agent run or delayed approval can miss
that window. A rejected or unconfirmed send becomes `unknown`; inspect the
conversation before deciding whether another message is needed.

Upstream reference: [Tencent QQ Bot SDK](https://github.com/tencent-connect/qqbot-nodejs).

## Weixin official iLink

1. In **Channels → Add channel**, choose **Weixin**, enter a
   name, and select **Save & pair**. The management dialog opens for that channel.
2. Select **Scan to pair Weixin**, scan the QR code in Weixin, and confirm on
   your device. If verification is requested, enter the numeric code. Generate
   a new QR if the previous one expires or verification is blocked.
3. After pairing, the channel table shows its bot ID and connection status.
   CG stores credentials on the server and starts long polling; no public HTTP
   callback is needed. Pairing availability depends on upstream account access.
4. Send a text message to the bot and open **Messages**. Even without a route,
   CG displays the received sender ID and `private:USER_ID` conversation ID.
   The channel's `@im.bot` ID identifies the bot, not your user account.
5. Select **Create route** on the message row to prefill the channel and user ID.
   Choose the a13n Host and Agent ID. Enable private automatic replies or select
   **Full access**, then save and send a new message. Discovery messages are not
   replayed; iLink replies require a newly authorized incoming context token.
6. For a personal assistant or testing, instead choose **All conversations** and
   **Any sender**. Each new private conversation gets its own thread. Select
   **Full access** to retain the Agent's configured tools and reply automatically.
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

## Host binding and failure handling

The [bridge guide](onebots-a13n.md#optional-mcp-tools-in-a13n) covers optional MCP
binding, conversation tokens, input retry, and reply review for all channel types.
Channel credentials and reply context tokens are not sent to the Agent. Native
sending retains the same rule: no automatic retry when delivery may have happened.
A disconnected OneBot socket or unpaired Weixin account leaves approved replies
waiting for connectivity. A missing authorized native reply context blocks the
reply before sending. A Weixin success receipt confirms API acceptance using CG's
client ID, not a platform message ID or recipient read acknowledgement.
The official send response may omit `ret`; a successful HTTP JSON response with
no nonzero `ret` or `errcode` is accepted as success.

Use one process/replica, HTTPS remotely, and a private persistent state directory.
See [credential handling](../spec/02-identity-and-access.md#administrator-and-credential-handling).
