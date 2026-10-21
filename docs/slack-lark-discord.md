# Slack, Feishu/Lark, and Discord

This guide describes the first implemented adapter slice of the
[multi-platform design](../spec/07-channel-integration.md). Protocol behavior is
covered by simulated native endpoints and failure tests. Live installation,
permissions, and media processing still require acceptance in a designated test
conversation. These adapters have not been deployed or configured automatically.

## Available surface

| Capability | Slack bot | Feishu/Lark tenant bot | Discord bot |
| --- | --- | --- | --- |
| Receive | Signed Events API HTTP | Signed, encrypted HTTP callbacks | Resumable Gateway v10 |
| Conversations | DM, channel, explicit thread | Private chat, group, explicit thread | DM, channel, explicit thread |
| Text | Plain text or Slack Markdown | Plain text | Plain text, 2,000 characters |
| Media send | External upload and file share | Separate image/file/audio/media messages | Multipart message attachments |
| Media read | Authenticated private file download | Message-resource API with tenant token | Credential-free attachment CDN |
| Native history | Scoped history/replies, subject to bot method permissions | Chat/thread message list | Channel/thread message list |
| Reactions | Add/remove by emoji name | Add/remove this app's emoji reaction | Add/remove own Unicode/custom reaction |
| Recall | Own confirmed messages | Own confirmed messages | Known messages under the matching recall grant |
| Typing | Unsupported | Unsupported | Native typing indicator |
| Search, cards, native catalogs, streams | Not yet implemented | Not yet implemented | Not yet implemented |

All accounts expose the same [five MCP tools](agent-tools.md). Unsupported
combinations fail before queuing. Socket Mode, Lark WebSocket workers, Discord
interaction callbacks, thread inheritance, native search, cards, edits, pins,
and streams remain follow-up slices; official platform support is not a claim
that CG already exposes them. User OAuth is not accepted as a substitute for a
bot credential.

## Configure one installation

Use **Channels → Add channel** or authenticated `POST /api/v1/connections`.
Credentials are write-only and stored separately from Host MCP keys. The API does
not return bot tokens, signing secrets, app secrets, or encryption keys.

| Field | Slack | Feishu/Lark | Discord |
| --- | --- | --- | --- |
| `kind` | `slack` | `lark` | `discord` |
| `transport` | `webhook` | `webhook` | `websocket` |
| `self_id` | Bot user ID, `U…` | Bot open ID, `ou_…` | Bot user snowflake string |
| `token` | Bot OAuth `xoxb-…` | Omit | Bot token |
| `app_id` | Installed workspace/team ID, `T…` | AppID, `cli_…` | Omit |
| `app_secret` | Omit | AppSecret | Omit |
| `webhook_secret` | Slack signing secret | Event verification token | Omit |
| `encrypt_key` | Omit | Event Encrypt Key, required | Omit |
| `api_url` | Omit; official endpoint fixed | `https://open.feishu.cn` (default) or `https://open.larksuite.com` | Omit; official endpoint fixed |
| `discord_intents` | Omit | Omit | Optional Gateway intent bitmask |

Slack and Lark callback URLs appear in **Manage** after saving:
`https://GATEWAY/api/v1/hooks/slack/CONNECTION_ID` and
`https://GATEWAY/api/v1/hooks/lark/CONNECTION_ID`. Use HTTPS. Callback signatures
are checked against the exact body and a five-minute timestamp window. Lark
requires an encrypted callback body and the configured verification token; its
application ID must match. Slack event workspace IDs must match the installation.
Callbacks finish durable ingestion before acknowledging; they never await Agent
execution. Invalid authentication cannot create conversation records.

For Slack, install bot scopes appropriate to the operations: `chat:write`,
`files:read`, `files:write`, `reactions:write`, and the relevant conversation
history scopes. Invite the bot to the desired channels and subscribe to the
appropriate message events and `app_mention`. Duplicate mention/message events
are deduplicated by the exact channel and timestamp. Some history/replies methods
restrict token types or membership; CG does not fall back to user OAuth. A
permission error is an error, never an empty gateway-history substitution.

For Lark, enable the bot, encrypted `im.message.receive_v1` events, and the IM
permissions needed for message send, resource access, history and reactions.
Reading unmentioned group messages requires its own application permission.
Use the region matching the installation. The gateway obtains and caches a
tenant access token; it does not start the blocking official WebSocket client.

For Discord, invite the bot with channel access and the permissions needed for
send, attach files, history and reactions. Enable **Message Content Intent** in
the Developer Portal where required. The default intents include guilds,
guild/direct messages, reactions and message content. Gateway authentication
checks the READY bot identity. Heartbeats, reconnects and resume positions belong
to the channel lifecycle, and positions advance after durable ingestion.

## Conversation routes and history authority

A route binds one concrete destination. Native identifiers remain strings.

| Platform | Kind | `conversation_id` |
| --- | --- | --- |
| Slack | `private` / `channel` | Native DM/channel ID |
| Slack | `thread` | `CHANNEL_ID:ROOT_TS`, retaining exact decimal timestamp |
| Lark | `private` / `group` | Native chat ID |
| Lark | `thread` | `CHAT_ID:ROOT_MESSAGE_ID`; CG verifies the root and resolves its native thread ID |
| Discord | `private` / `channel` / `thread` | Native channel/thread snowflake; a guild/server ID is not a send target |

A channel/group source does not include its child threads. Create explicit thread
sources, or a separate intentional all-threads source. A broad `thread` wildcard
is an explicit grant to those threads, not inherited parent visibility. Incoming
Discord channel types are verified with its channel API before selecting a
channel or thread route.

**Readable channels** continues to cover gateway-collected messages only. For
native history, explicitly enable **Read native platform history**
(`action_grants: ["history.read"]`) on the source route. Read-only account
handles do not gain native history access. Native reads cannot accept an arbitrary
channel or thread ID. Search is not implemented, even if `history.search` is
reserved in the management schema.

```json
{
  "resource": "messages",
  "source": "platform",
  "route_id": "route_from_discover",
  "arguments": {"limit": 20}
}
```

Pass this to `read`, then pass the returned `next_cursor` for the following page.
Native cursors are opaque, expire after 15 minutes, and bind the route, sender
policy, grants and account credentials. All pages and asset results recheck
current authority. Results expose message/index handles instead of private file
URLs. Retrieve native-history assets with `source: "platform"` and the usual
`source_message_id` plus `image_index` / `attachment_index`. Handles expire with
the cached history; reread history to refresh them. Received gateway assets use
`source: "gateway"` or omit it. Native history is never replayed into Agent work
or imported into an account's gateway-read grant.

## Send and act

Use `send_message` with one text part and at most one media part. New adapters
accept inline base64 media; they reject remote upload URLs. CG limits uploads to
16 MiB for Slack/Lark and 10 MiB for Discord. Received image reads remain bounded
to 4 MiB. Provider limits and permission checks can be stricter. Lark sends media
as a separate message: send a caption separately; audio requires Opus and video
requires MP4. These are ordinary audio/video attachments, not guaranteed native
voice-note UI. Use the Host sandbox to inspect/convert media; CG returns no local
filesystem paths.

Explicit `reply_to` must name a known authorized message. Slack replies stay in
the explicitly bound thread; posting to another thread requires another route.
Lark verifies thread-root ownership before sending. Discord disables mention
parsing; Slack escapes mention syntax. All replies use `send_message`; final
Agent output is never sent. Proactive sends require `message.proactive`.
`wakeup` remains QQ C2C-specific.

Reactions use `execute_action(action: "reaction")`, independently of message
creation, under `message.react`:

| Platform | `arguments.emoji` example |
| --- | --- |
| Slack | `{"kind":"custom","name":"thumbsup"}` |
| Lark | `{"kind":"platform","value":"THUMBSUP"}` |
| Discord Unicode | `{"kind":"unicode","value":"👍"}` |
| Discord custom | `{"kind":"custom","name":"wave","id":"123456789012345678"}` |
| Existing QQ/OneBot | `{"kind":"platform","value":"123","type":1}`; legacy `emoji_id` remains accepted |

Also supply `source_message_id` and optional `add: false` for removal. Typed and
legacy emoji fields cannot be mixed. Recall requires `message.recall` for bot
messages and, where supported, `message.recall_members` for member messages.
Native-history messages can become action targets only while that exact route's
history grant and cached provenance remain valid.

Poll `get_operation` after sending or acting. `sent` / `succeeded` confirms provider
acceptance, not recipient reading. Slack and Lark uploads retain `effects` for
confirmed intermediate stages. A file accepted by Slack without a resolvable
share timestamp remains `unknown` with a `published_file` effect, never a false
message receipt. Explicit rejections are `failed`; uncertain writes are
`unknown`. No adapter automatically retries an uncertain write. Check the test
conversation and recorded effects before submitting again.
