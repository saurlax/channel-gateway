# Channel platform API reference

Research date: **2026-10-21**. Scope: messaging/bot APIs and channel integration,
including the platforms discussed with the user and the Octop Gateway channel
inventory. This is API evidence and an integration reference, not a declaration
that CG implements these adapters. The target requirements are in
[Multi-platform channel integration](../spec/07-channel-integration.md).

## Evidence and current implementation

- **D**: capability documented by the provider's public API/reference.
- **F**: visible in a first-party SDK/source; deployed availability still needs
  account/version verification.
- **R**: visible in a reference integration, without a separately verified full
  provider contract.
- **N**: no general-purpose capability in the reviewed interface. Specialized
  interfaces or different account modes can exist.
- **U**: insufficient evidence to claim support; keep disabled until verified.

These describe upstream evidence, not CG implementation or granted permissions.
No new platform credentials were used and no live acceptance tests were run for
this research. Public pages, source code and generated API clients were inspected.
Some vendor pages returned empty content, generic navigation or HTTP 429; those
limitations are recorded rather than treated as proof of unsupported behavior.

At repository base `34bc569cb7824f50a12ae4575ae7a10021f0fdf1`, CG has QQ official,
OneBot v11 and text-only Weixin iLink. The working tree also has the five-tool
consolidation; it is not a new-platform implementation or deployment. Current
schemas and migration are in the [Agent tool guide](agent-tools.md).

In the tables below, **R/S/A** mean mapping to `read` / `send_message` /
`execute_action`. All adapters share `discover` and `get_operation`. Operation
names in new platform namespaces are target catalog identifiers, not current
callable tools. Exact native route parameters are supplied by CG's bound scope.

## Capability matrix

### Collaboration and community platforms

| Platform/account mode | Native history / search | Media receive / send | Reaction / message changes | Threads, rich content and streaming | CG status |
| --- | --- | --- | --- | --- | --- |
| QQ official C2C/group | N general list/search | D rich media; type/scene restrictions | N ordinary reactions; D scoped recall | Markdown/keyboards; C2C stream/typing | Implemented, scoped |
| QQ official guild/DM | D known-ID message; N confirmed general search | Images send; other received assets are distinct | D guild reactions, recall, permission-gated changes | Guild resources/forums, not generic chat threads | Implemented, scoped |
| Slack bot/user | D history/thread; conditional RTS search | D files, authenticated private downloads | D reactions, own edits/deletes, pins | Block Kit, modals, ephemeral messages, native streams | Profile only |
| Lark/Feishu app bot/user | D history; F current search implementation | D image/file/audio/video and other types | D reactions, edits/recall, pins | Topic/thread APIs, interactive/streaming cards | Profile only |
| Discord bot | D channel history and guild search | D attachment-based media | D reactions, edits/deletes, pins | Native threads/forums, components/commands/modals/polls; edits are not native text streams | Profile only |
| Telegram ordinary bot | N general timeline/search; specialized reads exist | D distinct photo/video/audio/voice/document types | D reactions, edits/deletes, pins | Topics/keyboards/polls; native temporary draft streaming | Profile only |
| Microsoft Teams agent + optional Graph | D Graph history; search identity-dependent | D, with Graph/file-consent distinctions | D agent chat reactions, own updates/deletes | Teams threads, Adaptive Cards, activity/stream features | Profile only |
| Google Chat app + optional user | D history with separate scopes; N general text search in reviewed list API | D attachments, upload requires user auth | D reactions require user auth | Space threads, app-auth cards/widgets | Profile only |
| Matrix client/bot device | D history/search, E2EE limits | D media, encrypted media needs crypto | D reactions, replacements/redactions | Room/thread relations, typing/read receipts | Profile only |
| Mattermost bot | D history/search | D files and post attachments | D reactions, edits/deletes, pins | Rooted threads, interactive messages/dialogs | Profile only |
| Zulip generic bot | D history/search within principal visibility | D upload/link attachments | D reactions, edit/delete | Channel/topic model; updates are not native token streams | Profile only |
| Rocket.Chat bot/user | D room history/search | D two-step file publication | D reactions, edit/delete/pin | Rooms/threads/discussions; server-version constraints | Profile only |

### Business, consumer and protocol channels

| Platform/account mode | Native history / search | Media receive / send | Reaction and other interaction | Principal boundary | CG status |
| --- | --- | --- | --- | --- | --- |
| Weixin iLink | N general history/search | F image/video/file, voice receive; outbound native voice U | F typing/quotes; reaction N in reviewed protocol | Paired account and private context token | Text only implemented |
| WeCom AI bot WebSocket | N general history/search in reviewed SDK | F media upload/reply/send; Python SDK coverage differs | F streams/template cards/feedback; reactions U | `bot_id` + bot secret | Profile only |
| WeCom self-built app / group Webhook | Different API surfaces; no implied chat archive | App media and Webhook content differ | App messages/cards; Webhook is not a full bot account | Corp/app token or destination Webhook | Separate profiles, not substitutes for AI bot |
| DingTalk app bot | N general history/search in reviewed bot API | D received download codes; F message/media sends | Cards, recall/read status; reactions U | App credential, robot code and installed conversation | Profile only |
| WhatsApp Cloud API | N general arbitrary chat timeline/search | D media/document/sticker | D reaction and interactive messages, status receipts | WABA + phone-number identity; business rules | Profile only; Groups eligibility gated |
| LINE Official Account | N general history/search | D receive files; send image/video/audio/sticker, no generic file object | Flex/templates/quick replies; reaction endpoint N | Messaging API channel and contact/group relationship | Profile only |
| OneBot v11 | D known-ID/forward read; bulk history is an extension | D segments; extensions determine file APIs | Reactions are provider extensions | Protocol implementation + exact `self_id` | Implemented subset |
| Signal linked client | N generic remote history via reviewed CLI | R client attachment send/receive | R reactions/receipts/typing/delete | Unofficial client/device, not an official bot API | Opt-in profile only |
| Yuanbao | U history/search | R image/file publication; audio/video represented as files | R message/heartbeat operations; reaction U | Reference bot binding and token protocol | Opt-in profile only |
| Xiaoyi | U IM history/search | R URI-based image/video/file artifacts | R task/session streaming and cancellation | Huawei Agent binding and A2A-style task context | Opt-in profile only |
| MQTT | N chat history/search | Opaque payload; media is an application protocol | No standardized chat reactions | Broker principal and topic ACL | Custom transport profile only |

## Media representation and identity constraints

A generic uploaded file can contain image/video/audio bytes without becoming a
native image, video or voice-note message. The initial adapter must advertise
these separately. D/F/R/U have the same evidence meaning as above; every row is
also subject to gateway limits and the selected account's actual permission.

| Profile | Received resources | Outbound representation to implement or gate |
| --- | --- | --- |
| QQ C2C/group | D image and rich-media attachments | D native image/video/audio/file flow under scene rules; PNG/JPEG image codec |
| QQ guild/DM | F projected attachment metadata; upstream events determine availability | Image endpoint only in initial generic media send; other media sends gated |
| Slack | D authenticated file assets | D files; image/video presentation follows Slack file processing; voice-note UI not inferred |
| Lark/Feishu | D image/file and audio/media resources | D distinct image, file, audio and media types; voice codec and video thumbnail requirements validated |
| Discord | D attachments with MIME/native metadata | D attachment publication; native voice-message flags/metadata require an explicit supported variant |
| Telegram | D photo/video/audio/voice/document/sticker | D method-specific native types; generic document and voice note remain distinct |
| Weixin iLink | F image/video/file and voice | F image/video/file; native voice send U until the full pipeline is verified |
| WeCom AI bot | F image/voice/file callbacks; native video receive U | F image/file/voice/video upload and publication in current Node source; Python feature parity not assumed |
| WeCom app/Webhook | Mode-specific | App media APIs and Webhook content schemas cannot borrow AI bot media rights |
| DingTalk app bot | D received download-code resources | F robot message templates; exact audio/video/file `msgKey`/upload combinations need tenant acceptance before advertising each type |
| Teams | D authorized activity/file resources | D Graph-backed files or personal file-consent flow; native voice-note publication U |
| Google Chat app/user | D attachment metadata/download under granted identity | D uploaded attachment with user OAuth; app-only card image URLs are a different capability |
| Matrix | D room media, possibly encrypted | D `m.image`, `m.video`, `m.audio`, `m.file`; voice-specific extensions require negotiated support |
| Mattermost | D authorized file assets | D file-backed post attachments; native audio/voice semantics only when verified for that server |
| Zulip | D authorized upload/link resources | D uploaded files embedded in content; not a claim of native video/voice bubbles |
| Rocket.Chat | D authorized room files | D upload followed by room publication; native file/voice types follow server version |
| LINE | D image/video/audio/file downloads | D image/video/audio/sticker; N generic file-send object |
| WhatsApp | D webhook media references | D image/video/audio/document/sticker; native voice flags need their own verified schema |
| OneBot | D standard segment definitions, implementation-dependent delivery | Declared supported segments only; file uploads and native voice metadata may be extensions |
| Signal | R client attachment events | R client attachment sends; media flags and disappearing-message behavior pinned to daemon version |
| Yuanbao | R resource references | R native images; video/audio/general files use file message bodies in the reference |
| Xiaoyi | R URI artifacts | R image/video/file URI artifacts; binary upload and outbound audio U |
| MQTT | Application-defined opaque payload | N native IM media; custom asset protocol must be specified before enabling media |

## Account, transport and address mapping

These are target integration bindings. They do not add configuration modes to
current CG. A native ID is always qualified by the configured CG connection.

| Profile | Initial receive path | Credential/principal | Native address and thread mapping |
| --- | --- | --- | --- |
| QQ | Existing Gateway or callback | AppID/AppSecret | C2C/group OpenID; guild child-channel ID; separate DM guild ID |
| Slack | Socket Mode; HTTP Events alternative | Workspace bot token + app-level socket token, or signing secret | Workspace + channel; replies use exact `thread_ts` |
| Lark/Feishu | Official WebSocket worker; HTTP alternative | Tenant app credentials; optional separate user OAuth | Tenant + `chat_id`; `thread_id`/message root retained |
| Discord | Gateway plus interaction callbacks | Installed bot token; interaction public key | Guild/server context + DM/channel/thread ID; thread parent verified |
| Telegram | Long polling first; verified Webhook alternative | Ordinary bot token | Signed `chat_id`; optional `message_thread_id` |
| Weixin | Existing iLink long polling | QR-paired bot/account | Verified private peer + context token; group mode gated |
| WeCom AI bot | Official bot WebSocket | `bot_id` + bot secret | Verified chat type/`chatid`; sender is separate |
| WeCom app / Webhook | Verified encrypted app callback / no general Webhook inbox | Corp/app identity / destination Webhook | App-authorized user/chat or exact bound Webhook destination |
| DingTalk | Stream callbacks | Client credential + robot code | Corp + `openConversationId`/private recipient; preserve staff-ID namespace |
| Teams | Verified Teams/Agents activity endpoint | Installed Entra app/agent; optional Graph principal | Tenant + native conversation reference; team/channel/root as applicable |
| Google Chat | Verified app interactions; optional Workspace Events/Pub/Sub | App principal; separate user OAuth | Space resource name + thread resource name |
| Matrix | Client `/sync` | Dedicated bot user/device token | Homeserver + room ID + event/thread relation |
| Mattermost | Authenticated WebSocket | Bot account/access token | Server + channel ID; post `root_id` for thread |
| Zulip | Registered event queue long polling | Generic bot email/API key | Organization + channel or DM recipient set; topic string retained |
| Rocket.Chat | Authenticated realtime subscriptions | Server user/bot token | Room ID + thread `tmid` |
| LINE | Signed Webhook | Messaging channel token/secret | Verified user/group/room ID; private reply token |
| WhatsApp | Signed Cloud API Webhook | WABA/phone-number token | Phone-number account + verified peer `wa_id`; Groups ID only when qualified |
| OneBot | Existing signed callback/universal socket | Transport token + exact `self_id` | Exact protocol private/group ID, independent of official QQ OpenIDs |
| Signal | Supervised local RPC receive stream | Linked/registered account device | Account + native peer/group identity; isolated daemon state |
| Yuanbao | Pinned reference WebSocket protocol | Verified reference app binding | Authenticated native conversation/sender; experimental access gate |
| Xiaoyi | Verified Agent WebSocket/task events | AK/SK + Agent ID | Bound Agent + session/task; no invented IM group identity |
| MQTT | Broker subscription | TLS/client principal + topic ACL | Server-owned device/endpoint mapping; topics are not Agent-selected destinations |

## 1. QQ official Bot

**Sources:** [API catalog](https://bot.q.qq.com/wiki/develop/api-v2/autogen/),
[message rules](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html),
[guild send](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/message/send.html),
[official SDK](https://github.com/tencent-connect/botpy).

AppID/AppSecret obtains an access token; the official Gateway or verified Ed25519
callback receives events. C2C/group OpenIDs are account-scoped. Guild child-channel
IDs and DM guild IDs are separate destinations. Receiving all group messages
requires the upstream feature; it does not recover old history.

| MCP mapping | Native surface |
| --- | --- |
| R messages/assets/events | CG history; guild known-ID read; scoped notifications/CDN assets |
| S text/media | `/v2/users/{openid}/messages`, `/v2/groups/{openid}/messages`, scene-specific files; `/channels/{id}/messages`, `/dms/{id}/messages` |
| S stream | C2C `/v2/users/{openid}/stream_messages` |
| A reaction/recall/resources | Guild message reactions; scene-specific delete; finite group/guild/resource catalog |

Passive windows/quotas and proactive limits differ. Group limits are measured
per minute/day in the reviewed rules, not a universal 30 QPS. Use the existing
[QQ contract](../spec/06-qq-platform.md), grants and persistent budgets. Guild
generic file uploads cannot be inferred from C2C/group rich-media APIs.

**Integration:** retain the existing adapter; route native operations through
the `qq.` namespace and preserve private reply/reference state.
**Gates:** all scenes, passive expiry, proactive switch, guild-only reactions,
media/quote provenance, uncertain upload/send, and callback event acknowledgements.

## 2. Slack

**Sources:** [Events API](https://docs.slack.dev/apis/events-api/),
[Socket Mode](https://docs.slack.dev/apis/events-api/using-socket-mode/),
[history](https://docs.slack.dev/reference/methods/conversations.history/),
[thread replies](https://docs.slack.dev/reference/methods/conversations.replies/),
[RTS search](https://docs.slack.dev/apis/web-api/real-time-search-api/),
[upload](https://docs.slack.dev/reference/methods/files.getUploadURLExternal/),
[streaming](https://docs.slack.dev/reference/methods/chat.startStream/),
[method index](https://docs.slack.dev/reference/methods/).

Use one installed workspace identity, a bot OAuth token, and either an app-level
Socket Mode token with `connections:write` or an HTTP signing secret. Persist
workspace/channel IDs and exact message `ts`; thread `thread_ts` is not another
workspace. Start with `app_mention` and authorized message event subscriptions.

| MCP mapping | Native surface and permission family |
| --- | --- |
| R timeline/thread | `conversations.history`, `conversations.replies`; relevant `*:history`, membership |
| R search | `assistant.search.context`; `search:read.*`, identity and action-token requirements |
| R/S assets | Private file download with `files:read`; upload URL then `files.completeUploadExternal`, `files:write` |
| S messages/cards/stream | `chat.postMessage`, Block Kit, `chat.startStream`/`appendStream`/`stopStream`; `chat:write` |
| A | `reactions.add/remove`, `chat.update/delete`, `pins.add/remove`; operation-specific scopes |

RTS bot calls require an event-provided `action_token`; private search or use
outside Slack requires user OAuth. RTS is limited to internal/directory-published
apps. A generic bot token is not unrestricted global search. Store action tokens
privately and filter all search output to gateway scope. Rate limits follow the
method/distribution rules and response headers. Use the new upload sequence,
not retired `files.upload`. HTTP event ACKs precede Agent work.

**Integration:** async Slack SDK transport/Web API wrapper, preserving callbacks,
message changes and safe mention controls. Native blocks/ephemeral/scheduling
use `native.slack` schemas and `slack.*` operations without extra MCP tools.
**Gates:** private-channel denial, thread separation, expired action token, file
auth, stream finalization, app reinstall/revocation and throttled history.

## 3. Lark / Feishu

**Sources:** [IM API index](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1),
[history](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/message/list),
[content formats](https://open.feishu.cn/document/server-docs/im-v1/message-content-description/create_json),
[WebSocket events](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-),
[CardKit](https://open.feishu.cn/document/cardkit-v1/card/create),
[SDK WebSocket lifecycle](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/ws/client.py),
[official search implementation](https://github.com/larksuite/cli/blob/017b19a793ad71794a02887ede04757f3d82e10d/shortcuts/im/im_messages_search.go).

Use App ID/secret and tenant access tokens for app-bot operations. User OAuth is
a separate principal. Feishu and Lark regional hosts must match the installation.
Prefer official long connections for a self-hosted app; retain signed/encrypted
HTTP callback support where the application mode requires it. Verify the tenant,
bot identity and event ID. Full group visibility is a separate permission.

| MCP mapping | Native surface |
| --- | --- |
| R timeline/message/thread | `GET /im/v1/messages`, message get/mget and thread operations; bot membership/scopes |
| R search | `POST /im/v1/messages/search`, followed by `GET /im/v1/messages/mget` |
| R/S assets | `/im/v1/images`, `/im/v1/files`, message-resource download; native keys stay private |
| S content/reply | `POST /im/v1/messages`, `POST /im/v1/messages/{id}/reply`; text/post/image/audio/media/file/interactive variants |
| A reactions/changes/pins | `/im/v1/messages/{id}/reactions`, update/patch/delete, `/im/v1/pins` |
| S stream / A card update | CardKit card entity + element content/settings/versioned updates |

The current first-party CLI explicitly declares bot/user search with
`search:message`; its workflow also enriches reactions and chat context. This
supersedes a blanket claim that bot search does not exist. Endpoint availability,
scopes, charging and visibility still require tenant testing. Do not mistake an
older locally installed CLI's missing command for proof about the remote API.

**Integration:** async SDK REST methods plus an isolated worker for the official
WebSocket client, whose `start()` owns its event loop. The worker bridges events
to CG's durable ingress and waits for persistence before ACK; it needs bounded
shutdown and must not block the service loop. Preserve `chat_id`, `message_id`,
`thread_id`, mentions and tenant/app namespace. CardKit is an explicit capability.
**Gates:** unmentioned group input, least-privilege history/search, regional auth,
thread pagination, media resource ownership, callback deadline and card versions.

## 4. Discord

**Sources:** [Gateway](https://docs.discord.com/developers/events/gateway),
[messages/history/search/reactions](https://docs.discord.com/developers/resources/message),
[interactions](https://docs.discord.com/developers/interactions/receiving-and-responding),
[channels/threads](https://docs.discord.com/developers/resources/channel),
[rate limits](https://docs.discord.com/developers/topics/rate-limits).

Use a bot token and installed bot permissions. Receive Gateway dispatches with
heartbeat/resume and explicit intents. HTTP interactions additionally verify the
application public-key signature. A guild is a server; a thread is a native
channel with its own ID/visibility. DM availability does not imply group-DM access.

| MCP mapping | Native surface |
| --- | --- |
| R history/search | `GET /channels/{id}/messages`, `GET /guilds/{id}/messages/search`; history permission/intents |
| R/S assets and content | Attachment URLs; multipart `POST /channels/{id}/messages`; embeds/components/stickers/polls |
| A | Message reaction PUT/DELETE/list, message PATCH/DELETE, channel pins and typing |
| Native interactions | Command/component/modal events and interaction callback/follow-up endpoints |

`MESSAGE_CONTENT` affects content, embeds, attachments, components and polls.
Guild search is now documented: do not label it universally unavailable to bots.
An unbuilt index can return 202 and a retry hint. Ordinary message content has a
different size bound from CG's text ceiling. Safe `allowed_mentions` prevents
unintended pings. Initial interaction response is due within three seconds;
follow-up tokens last fifteen minutes and are private. Message editing is not
native token streaming.

**Integration:** an async Gateway adapter plus REST/interaction client, with
`native.discord` options and `discord.*` administrative resources.
**Gates:** private/archived threads, missing intent, search 202, multipart media,
reaction removal, interaction defer/expiry, global and route rate buckets.

## 5. Telegram

**Sources:** [Bot API](https://core.telegram.org/bots/api),
[update/privacy FAQ](https://core.telegram.org/bots/faq),
[first-party server implementation](https://github.com/tdlib/telegram-bot-api).

Use an ordinary bot token and either long polling or a Webhook; these receive
modes are mutually exclusive. Webhooks verify the configured secret header.
Persist `update_id` before advancing offset. Keep signed chat IDs, message IDs
and topic IDs distinct. Privacy mode/admin status determines group visibility.

| MCP mapping | Native surface |
| --- | --- |
| R events/assets | `getUpdates`, `getFile`; store received updates for gateway history |
| S | `sendMessage`, photo/video/audio/voice/document/sticker/media-group variants and `ReplyParameters` |
| S stream | `sendMessageDraft` / `sendRichMessageDraft`, then final `sendMessage` / `sendRichMessage` |
| A | `setMessageReaction`, `editMessage*`, `deleteMessage`, `pinChatMessage`, `sendChatAction` |
| Native | Topics, polls, keyboards, callback-query answers and administrative operations |

The reviewed Bot API 10.3 supports temporary private-chat drafts: a preview is
not a persistent message. There is no general ordinary-bot chat-history/search
endpoint. `getUserPersonalChatMessages` reads profile-associated chat posts, not
arbitrary private conversation history. Updates are not a permanent archive.
Reaction limits and paid/custom reaction rules differ from message sending.
Business/Secretary/guest modes have separate rights and are outside the first
ordinary-bot adapter; they cannot silently substitute user access.

**Integration:** async Bot API client with durable polling or verified callbacks.
**Gates:** privacy mode, topic isolation, callback duplicates, draft final send,
bot relationship/admin restrictions and file/voice versus generic audio behavior.

## 6. Weixin iLink

**Sources:** [first-party protocol](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md),
[media sending implementation](https://github.com/Tencent/openclaw-weixin/blob/main/src/messaging/send.ts),
[API types](https://github.com/Tencent/openclaw-weixin/blob/main/src/api/types.ts).

Pair by QR, bind the returned account/base URL, and persist the polling cursor.
Keep the bot token, context token, upload parameters and AES media keys private.

| MCP mapping | Native surface |
| --- | --- |
| R events/assets | `/ilink/bot/getupdates`; referenced encrypted CDN media |
| S | `/ilink/bot/getuploadurl` + upload, `/ilink/bot/sendmessage` |
| A typing | `/ilink/bot/getconfig` ticket, `/ilink/bot/sendtyping` |
| R history / A reactions | Gateway history only; no general history/reaction method in the reviewed protocol |

The first-party code supports images, videos and files; captions/media can be
separate requests. It also receives voice. A voice enum/upload type alone does
not prove a complete outbound native-voice pipeline. A `group_id` type field
does not establish general public group-bot availability. The reference send
helper's behavior without a context token is not an acceptance guarantee.

**Integration:** extend the existing private text adapter through explicit media
capabilities, rather than treating it as QQ or a generic public-URL sender.
**Gates:** pairing expiry, account changes, encrypted media, caption partial
publication, context expiry and duplicate update batches. Group/voice send remain
disabled until separately verified. CG currently implements text only.

## 7. WeCom: select an account mode

**Sources:** [AI bot Node SDK](https://github.com/WecomTeam/aibot-node-sdk/tree/80615b987ef69c6028ad764924609247c0725955),
[Python SDK](https://github.com/WecomTeam/wecom-aibot-python-sdk),
[enterprise application API](https://developer.work.weixin.qq.com/document/path/90236),
[group Webhook](https://developer.work.weixin.qq.com/document/path/91770).

The initial conversational integration uses the **AI bot WebSocket** mode with
bot ID/secret and `wss://openws.work.weixin.qq.com`. Corporate app credentials
and a destination Webhook are separate modes with separate capability catalogs.

| Mode / MCP mapping | Native surface |
| --- | --- |
| AI bot ingress | `aibot_subscribe`, message/event callbacks, heartbeat and frame receipts |
| AI bot S/A | Respond/send message, stream/template-card update; `uploadMedia`, `replyMedia`, `sendMediaMessage` in current Node SDK |
| AI bot R assets | Authenticated media metadata plus download/AES decryption |
| App mode S/resources | App access token, `/cgi-bin/message/send`, temporary media APIs and verified encrypted callbacks |
| Webhook mode S | Destination-specific `/cgi-bin/webhook/send`; supported content subset only |

The reviewed Python SDK supports streams/cards and decrypted receives, while the
newer Node source also contains chunked media upload/reply/send. Do not claim SDK
parity. General chat history/search and reactions are not established by these
AI bot interfaces. Enterprise compliance archiving is a different product/access
contract, not ordinary bot history permission.

**Integration:** implement the verified async wire protocol or a maintained
async SDK path; do not introduce a Node runtime solely by assumption.
**Gates:** least-privilege bot subscription, passive frame versus proactive send,
media upload/expiry/decryption, stream finish, card feedback, and no invented
history/reaction support. Ported SDK auto-retries must obey CG's write policy.

## 8. DingTalk app bot

**Sources:** [Stream protocol](https://opensource.dingtalk.com/developerpedia/docs/learn/stream/protocol/),
[bot receive model](https://opensource.dingtalk.com/developerpedia/docs/learn/bot/appbot/receive/),
[first-party generated robot client](https://github.com/alibabacloud-go/dingtalk/blob/1986c966942afc67b8ccae59d29d57989a45fe76/robot_1_0/client.go),
[cards](https://open.dingtalk.com/document/development/message-card-sending-and-updating).

Use an app credential/token and robot code. Prefer outbound Stream connection
registration followed by WebSocket callbacks; support verified HTTP callbacks as
a separate transport. Bot DM input and group @bot input differ from full group
visibility. Preserve corp, staff, open-conversation and native message IDs.

| MCP mapping | Native surface |
| --- | --- |
| Ingress | `POST /v1.0/gateway/connections/open`; callback topic `/v1.0/im/bot/messages/get` |
| R attachment | `POST /v1.0/robot/messageFiles/download`, using received `downloadCode` |
| S | `/v1.0/robot/oToMessages/batchSend`, `/v1.0/robot/groupMessages/send`; callback `sessionWebhook` where applicable |
| A recall/status | `/v1.0/robot/groupMessages/recall`, `/v1.0/robot/otoMessages/batchRecall`; own-message read-status queries |
| S/A native cards | Documented card creation/update and callbacks; separate AI-card streaming capability |

Stream is inbound transport, not proof of token-streamed text. Read-status and
process-query keys describe bot sends, not general history. Ordinary full chat
history/search and reactions remain unverified/unavailable in the reviewed bot
catalog. Self-built group Webhooks and enterprise app bots do not have identical
permissions. Dynamic session Webhooks are private expiring reply capabilities.

**Integration:** async Stream lifecycle plus REST/card wrapper and a `dingtalk.`
catalog. **Gates:** session expiry, installed group authorization, exact download
code ownership, group @ visibility, card versions and own-message recall receipts.

## 9. Microsoft Teams

**Sources:** [supported SDK direction](https://learn.microsoft.com/en-us/azure/bot-service/bot-builder-basics-teams),
[Python Teams SDK](https://learn.microsoft.com/en-us/python/api/msteams-sdk-python),
[Graph chat history](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0),
[resource-specific consent](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/resource-specific-consent),
[proactive agent messages](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages),
[file access](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/bots-filesv4),
[agent reactions](https://learn.microsoft.com/en-us/microsoftteams/platform/agents-in-teams/agent-reactions),
[Graph message sending](https://learn.microsoft.com/en-us/graph/api/chatmessage-post?view=graph-rest-1.0).

Use a Teams app/agent installation, Entra identity and current Teams or Microsoft
365 Agents SDK. Archived Bot Framework SDK is not the new adapter foundation.
Validate inbound service tokens and tenant binding. Store the native conversation
reference, chat/channel/root IDs and installation visibility.
Broader message visibility needs explicit RSC/event permissions; default agent
interaction delivery is not evidence of full team/channel monitoring.

| MCP mapping | Native surface |
| --- | --- |
| R history | Graph `/chats/{id}/messages`, channel messages/replies; delegated or RSC/application permission as documented |
| S | Teams agent send/reply/proactive APIs; Adaptive Cards; known conversation reference |
| R/S files | Graph OneDrive/SharePoint; personal-only file-consent workflow where applicable |
| A | Agent message update/delete, typing, Teams API `ReactionClient` add/delete |
| Native resources | Graph/Teams operations with separately selected identity and consent |

Agent reactions are documented and generally available in 2026. Graph app-only
history permission does not imply ordinary app-only Graph message publishing;
migration permissions are not a general send workaround. File-consent APIs are
personal-only; Graph file access covers other scopes with its own consent.
Proactive messaging requires the relevant app installation. National clouds and
private/shared channels need explicit capability checks.

**Integration:** current async Teams/Agents ingress and messaging, optional Graph
read/file client. **Gates:** tenant mismatch, missing installation/RSC, private
channels, group-file denial, reaction scope and delegated-principal separation.

## 10. Google Chat

**Sources:** [interaction delivery](https://developers.google.com/workspace/chat/receive-respond-interactions),
[Workspace Events](https://developers.google.com/workspace/events/guides/events-chat),
[message list](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/list),
[create](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages/create),
[reaction create](https://developers.google.com/workspace/chat/api/reference/rest/v1/spaces.messages.reactions/create),
[media upload](https://developers.google.com/workspace/chat/api/reference/rest/v1/media/upload).

Use a Chat app with verified HTTP interactions and app authentication. Broader
space event ingestion uses Workspace Events subscriptions/Pub/Sub and renewal;
an @app interaction endpoint alone is not complete space monitoring. User OAuth
and administrator-approved app scopes are distinct credential profiles.

| MCP mapping | Native surface |
| --- | --- |
| R timeline | `spaces.messages.list`; admin-approved `chat.app.messages.readonly` or user message-read scope |
| S cards/text | `spaces.messages.create`, `chat.bot` for app-auth text/cards/widgets; thread addressing |
| R/S assets | Attachment metadata/download; `media.upload` requires user scopes |
| A | Message patch/delete; reaction create/delete requires user authentication |

App-auth history excludes private messages in a space. The list filter supports
date/thread constraints, not general full-text search. User-auth publication
does not automatically get app-auth cards/widgets. Do not silently borrow a user
token to add a reaction or upload a file for the bot.

**Integration:** app-only text/card first, separately authorized user upload/
reaction capabilities; optional Workspace Events subscription owner.
**Gates:** app-only denial cases, subscription expiry/renewal, thread scope,
private-message exclusion and resource ownership across app/user identities.

## 11. Matrix

**Sources:** [Client-Server specification](https://spec.matrix.org/latest/client-server-api/),
[Application Service specification](https://spec.matrix.org/latest/application-service-api/).

Use a dedicated bot user/device and scoped access token on a configured
homeserver. Start with client `/sync`; an application-service bridge is a
separate privileged deployment profile. Persist sync/device and crypto state.

| MCP mapping | Native surface |
| --- | --- |
| R | `/sync`, `/rooms/{roomId}/messages`, room event, relations and `/search` |
| R/S assets | Authenticated media download/upload, plus encrypted-media metadata |
| S | Room send event with transaction ID and reply/thread relations |
| A | `m.reaction`, replacement (`m.replace`), redaction, typing/receipt/state APIs |

History visibility, room membership and encryption determine readability.
Server search cannot search plaintext that the server does not possess in E2EE
rooms. An encrypted room is unsupported until device keys, verification,
decryption, encrypted media and restart persistence are implemented. A failed
decryption cannot become empty but supposedly complete history.

**Integration:** async Matrix client adapter with a persistent crypto store when
E2EE is enabled; common message/action operations plus `matrix.` room resources.
**Gates:** initial-sync non-activation, missing room keys, room permissions,
transaction-ID replay, redaction, federation gaps and authenticated media.

## 12. Mattermost

**Sources:** [bot identity](https://docs.mattermost.com/developers/integrate/reference/bot-accounts),
[posts](https://docs.mattermost.com/api/reference/posts),
[files](https://docs.mattermost.com/api/reference/files),
[reactions](https://docs.mattermost.com/api/reference/reactions),
[official API source](https://github.com/mattermost/mattermost/tree/master/api/v4/source).

Use one bot account/access token per configured server identity. REST requests
use bearer auth; receive authenticated WebSocket events. Explicitly bind channel
and team visibility even when a server administrator grants broad bot access.

| MCP mapping | Native surface |
| --- | --- |
| R | API v4 channel posts, post/thread get, team/visible post search |
| R/S assets | `/api/v4/files` upload/download; attach returned file IDs to posts |
| S | `POST /api/v4/posts`; `root_id` selects a thread; interactive post properties |
| A | Post update/delete/pin and `/api/v4/reactions` create/remove |

Capabilities depend on server version, license/configuration and bot role.
Webhooks alone do not provide history or full event visibility. Public file-link
generation must not bypass scoped attachment reads. Editing a post is a declared
emulation option, not a native streaming guarantee.

**Integration:** async REST/WebSocket adapter with version discovery.
**Gates:** private channel membership, thread `root_id`, read/search denial,
file ownership, role change and post-create versus update event deduplication.

## 13. Zulip

**Sources:** [register queue](https://zulip.com/api/register-queue),
[events](https://zulip.com/api/get-events),
[history/search](https://zulip.com/api/get-messages),
[send](https://zulip.com/api/send-message),
[upload](https://zulip.com/api/upload-file),
[reactions](https://zulip.com/api/add-reaction).

Use a generic bot email/API key with HTTP Basic authentication. Register an event
queue and long-poll using queue/event IDs. Incoming-Webhook-only bot modes are a
different capability profile. Subscriptions and history visibility constrain
what a newly created bot can read.

| MCP mapping | Native surface |
| --- | --- |
| R | `GET /api/v1/messages` with narrow/search filters and anchor pagination |
| R/S assets | `POST /api/v1/user_uploads`; returned upload paths in message content |
| S | `POST /api/v1/messages`, explicit channel/DM recipient and topic |
| A | Message patch/delete; message reactions; typing and permitted channel operations |

Channel/topic identity is essential: topic names can change and messages can
move. Record these changes explicitly; do not identify a topic by the first
message ID. Shared-history searches can expose earlier public content under
upstream policy, so gateway native-history grants remain required.

**Integration:** async REST plus queue consumer; maintain topic addressing and
server feature level. **Gates:** expired queue, topic rename/move, unseen private
channel, bot subscription, narrow pagination and file-link authorization.

## 14. Rocket.Chat

**Sources:** [messaging](https://developer.rocket.chat/apidocs/messaging-api),
[room history](https://developer.rocket.chat/apidocs/get-room-history),
[search](https://developer.rocket.chat/apidocs/search-message),
[reaction](https://developer.rocket.chat/apidocs/react-to-message),
[media upload](https://developer.rocket.chat/apidocs/upload-media-files-to-a-room),
[API index](https://developer.rocket.chat/llms.txt).

Use a configured server, bot/user identity and auth token; REST requires
`X-Auth-Token` and `X-User-Id`. Native realtime event subscriptions require their
own authenticated lifecycle. A room ID is the send scope; thread `tmid` is
preserved separately.

| MCP mapping | Native surface |
| --- | --- |
| R | `/api/v1/rooms.history`, `/api/v1/chat.search`, message get |
| R/S assets | `/api/v1/rooms.media/{rid}`, followed by the documented uploaded-file send endpoint |
| S | `chat.sendMessage`/`chat.postMessage`, native room/thread fields |
| A | `chat.react`, message update/delete/pin and permitted room operations |

Use current room history rather than depending on realtime `loadHistory` methods
scheduled for removal. File upload is distinct from publication. History and
search still require room access even when anonymous public reads are enabled
on the server. Extensions and license-specific operations are advertised by
server version/configuration.

**Integration:** versioned async REST for reads/mutations and authenticated
realtime subscriptions for ingress. Do not implement chat mutations through
deprecated DDP method calls. Validate the subscription/login path against the
actual server release before enabling ingestion.
**Gates:** private-room access, auth-token rotation, current upload/publish flow,
reaction setter semantics, thread visibility and deprecated endpoint rejection.

## 15. LINE Official Account

**Sources:** [Messaging API reference](https://developers.line.biz/en/reference/messaging-api/nojs/),
[message features](https://developers.line.biz/en/docs/messaging-api/overview/),
[signature verification](https://developers.line.biz/en/docs/messaging-api/verify-webhook-signature/).

Use a Messaging API channel access token and channel secret. Verify the original
HTTP body with HMAC-SHA256; retain Webhook event IDs and redelivery flags. Bind
user/group/room sources from verified events rather than display names.

| MCP mapping | Native surface |
| --- | --- |
| R assets | `GET /v2/bot/message/{messageId}/content`; received image/video/audio/file |
| S | `/v2/bot/message/reply`, `/push`; supported image/video/audio/sticker/Flex/template objects |
| A/status | Loading indicator where supported; finite rich-menu/native operations |
| R history / A reaction | Gateway history; no general history/search or reaction-send endpoint in reviewed catalog |

Reply tokens are short-lived and single-use; proactive pushes have relationship,
quota and billing rules. Media content can expire. There is no ordinary generic
outbound file object corresponding to the received file type. A sticker message
is not an emoji reaction on an existing message. Loading animation is not
universal typing or content streaming.

**Integration:** signed Webhook plus async Messaging API client; retain reply
context and publish Flex schemas through `native.line`.
**Gates:** duplicate/redelivered reply token, expired media, group eligibility,
quota rejection and unsupported generic-file admission before any send.

## 16. WhatsApp Cloud API

**Sources:** [Meta official collection](https://www.postman.com/meta/whatsapp-business-platform/overview),
[message objects](https://www.postman.com/meta/whatsapp-business-platform/folder/1dtuocp/messages-object),
[media](https://www.postman.com/meta/whatsapp-business-platform/request/zsq66eh/download-media),
[Direct Send announcement](https://developers.meta.com/resources/videos/whatsapp-direct-send-api/),
[Groups documentation](https://developers.facebook.com/documentation/business-messaging/whatsapp/groups/).

Use a registered business phone-number ID, WABA and server-side access token.
Verify subscription challenges and Webhook signatures/application binding.
Message webhooks and status callbacks have different semantics.

| MCP mapping | Native surface |
| --- | --- |
| R/S media | Graph media upload, media-ID URL retrieval, authenticated download |
| S | Graph `/{phone-number-id}/messages`; text/media/document/sticker/template/interactive variants |
| A | Native `reaction` payload through the messages endpoint; read/typing status where supported |
| R history | Gateway received/sent history; coexistence/history migration is a separate contract |

HTTP path does not determine MCP classification: a reaction posted through the
messages endpoint remains `execute_action`. Preserve reply `context.id` and
`wamid` correlation. Service windows, message categories and business eligibility
constrain outbound sends. Meta's June 2026 Direct Send utility feature prevents
a blanket claim that every out-of-window send requires a preapproved template.
Groups API documentation returned 429; its exact current eligibility/limits were
not independently verified. Keep group support gated, not universally absent.

**Integration:** direct Meta Graph/Webhook adapter with pinned Graph version and
server-owned phone-number destination binding.
**Gates:** denied category/window, template/Direct Send eligibility, media expiry,
reaction target, status ordering and approved Groups access before group release.

## 17. OneBot v11 and implementation extensions

**Sources:** [standard](https://github.com/botuniverse/onebot-11),
[API](https://11.onebot.dev/api/),
[message segments](https://github.com/botuniverse/onebot-11/blob/master/message/segment.md),
[existing CG contract](../spec/01-channel-adapters.md#onebot-v11).

OneBot is a protocol, not a provider entitlement. Authenticate the configured
transport and exact `self_id`. HTTP API, signed reverse HTTP and universal
forward/reverse WebSocket have different receive capabilities.

| MCP mapping | Native surface |
| --- | --- |
| R | `get_msg`, `get_forward_msg`, standard group/member information; gateway timeline |
| S | `send_msg` and supported message segments |
| A | `delete_msg`, permitted group management; optional provider reaction setter |
| Extensions | Bulk history, group-file upload/download, `set_msg_emoji_like` only under a declared implementation/version profile |

An implementation must identify its extensions and schemas; probing an arbitrary
action name is not discovery. OneBot IDs and QQ official OpenIDs do not share a
namespace. Standard segment names alone do not prove every implementation can
receive and send those types. Keep the currently supported image/reaction subset
and its existing grants until separately extended.

**Integration:** retain current transport adapters, add finite optional extension
catalogs under `onebot.`/implementation namespaces.
**Gates:** wrong account/role, no socket receipt, CQ parsing, extension absence,
cross-group file reference and standard-versus-extension capability reporting.

## 18. Signal linked client

**Sources:** [signal-cli](https://github.com/AsamK/signal-cli),
[CLI/RPC behavior](https://github.com/AsamK/signal-cli/blob/master/man/signal-cli.1.adoc),
[JSON-RPC](https://github.com/AsamK/signal-cli/blob/master/man/signal-cli-jsonrpc.5.adoc).

This is an unofficial client bridge, not an official Signal bot service. Bind
one registered/linked account and device identity to a supervised local
signal-cli daemon. Keep its encrypted session/device database persistent.

| MCP mapping | Native surface |
| --- | --- |
| R | Received daemon events and authorized attachments; CG history |
| S | JSON-RPC `send`, including validated attachment handles |
| A | Version-supported reaction, typing, receipt and remote-delete commands |
| Native resources | Declared group/contact operations within the selected account |

The adapter must not execute shell command strings assembled from message data.
Use a local authenticated/isolated RPC boundary, deadlines and supervised process
lifecycle. Device linking, key/session errors, disappearing messages and local
attachment availability affect capabilities. Remote archive search is not
established by the reviewed receive interface.

**Integration:** opt-in sidecar adapter, isolated from CG's async process.
**Gates:** account mismatch, device unlink, sidecar restart, key changes,
attachment path confinement and unknown RPC publication outcome.

## 19. Yuanbao

**Evidence R:** [pinned Octop adapter](https://github.com/TencentCloud/octop-gateway/tree/224ed078ce20042fb5fa59240d6e3c7c34bcd67f/src/octop_gateway/channels/yuanbao).
No independently complete public Yuanbao bot API contract was verified.

The reference binds app key/secret, obtains a signed token, authenticates a
binary WebSocket protocol, acknowledges pushed messages and uses media resource
download/upload credentials. These are implementation observations, not proof
of generally available app onboarding or stable API entitlement.

| MCP mapping | Observed reference behavior |
| --- | --- |
| R | Received messages and resource downloads; gateway history |
| S | Native text/image bodies; uploaded audio/video/file use file bodies |
| A | Reply heartbeat/status where supported; other actions unverified |

**Integration:** opt-in experimental adapter only after an authorized test
binding and official access terms/endpoints are verified. Preserve conversation
and sender identities from authenticated pushes. Do not copy arbitrary endpoint
configuration or log token responses from the example.
**Gates:** protocol/version pin, auth expiry, binary push deduplication, media
representation and confirmed send receipts. History, reactions and native voice
remain disabled without separate evidence.

## 20. Xiaoyi

**Sources:** [Huawei OpenClaw mode](https://developer.huawei.com/consumer/cn/doc/doccenter-celia/openclaw-0000002763003871),
[cloud A2A configuration](https://developer.huawei.com/consumer/cn/doc/doccenter-celia/a2a-basic-configuration-0000002746111014),
[pinned reference adapter](https://github.com/TencentCloud/octop-gateway/tree/224ed078ce20042fb5fa59240d6e3c7c34bcd67f/src/octop_gateway/channels/xiaoyi).

Huawei documents Agent/A2A onboarding, but some fetched pages contained only
navigation. Protocol details here are R evidence from the pinned adapter. It
binds AK/SK/Agent ID, connects the Huawei WebSocket and handles `message/stream`,
task/session IDs, status updates, artifacts, cancellation and clear-context events.

| MCP mapping | Target meaning |
| --- | --- |
| R | Retained task/session events and authorized URI artifacts |
| S | User-visible text/artifact stream for that task/session |
| A | Task cancellation/status operations, only where the Host can confirm the effect |

It is an Agent task channel, not evidence of an IM group archive/reaction API.
The reference's image/video/file URI artifacts are not a generic binary upload
service. Its reasoning/tool-hint events do not authorize exposing hidden model
reasoning. An immediate cancellation acknowledgement is not proof an a13n run
stopped. Cross-Host cancellation needs an explicit Host capability contract.

**Integration:** opt-in session/task adapter with task-to-Host binding. Preserve
session isolation and use verified TLS endpoints.
**Gates:** actual access approval, task reconnect, duplicate/cancel races, artifact
expiry and successful Host cancellation before advertising cancellable tasks.

## 21. MQTT

**Sources:** [MQTT 5.0 standard](https://docs.oasis-open.org/mqtt/mqtt/v5.0/os/mqtt-v5.0-os.html),
[pinned Octop example](https://github.com/TencentCloud/octop-gateway/blob/224ed078ce20042fb5fa59240d6e3c7c34bcd67f/src/octop_gateway/channels/mqtt.py).

MQTT supplies publish/subscribe delivery, not standardized chats, files or emoji.
Configure broker TLS/authentication and server-owned topic mappings/ACLs. A
connection binds an application/device namespace, not arbitrary Agent-selected
publish or wildcard subscription topics.

| MCP mapping | Required application contract |
| --- | --- |
| R | Persisted authorized application envelopes; no broker chat archive |
| S | Validated publish envelope with application message ID and destination |
| A | Only explicitly defined application commands; no invented native chat reactions |
| Media | Explicit attachment transport contract; a URL in text is not native media delivery |

The Octop example renders media as text links/placeholders. MQTT QoS applies to
protocol delivery and does not prove device execution or Agent exactly-once
effects. Retained snapshots do not automatically trigger old tasks. PUBACK is a
broker receipt, not a recipient read receipt. Application receipts need their
own correlated event schema.

**Integration:** opt-in custom transport, initially text/envelope only; no
universal IoT action catalog. **Gates:** topic escape, publisher spoofing, retained
replay, QoS duplicates, reconnect ordering and broker-versus-device receipts.

## Octop comparison and integration boundaries

The reviewed [Octop Gateway](https://github.com/TencentCloud/octop-gateway/tree/224ed078ce20042fb5fa59240d6e3c7c34bcd67f)
has ten built-in channel adapters: Feishu, DingTalk, QQ, WeCom, Weixin, Yuanbao,
Xiaoyi, MQTT, Telegram and Discord. Its message/content model, media backend,
push separation and group visibility/activation split are useful references.
Its adapter inventory is not a complete native API capability matrix.

CG retains separate Host delivery, current authorization and durable operations.
It must not inherit reference defaults such as broad channel allowlists, media
fallback-to-text behavior, token-response logging, TLS exceptions or implicit SDK
write retries. Reference source establishes only the behavior actually inspected.
Other Octop connectors such as documents/calendars are not channel adapters.

Web Chat, email, SMS and IRC remain separate extension directions. Web Chat
requires an authenticated session/attachment protocol; email needs mailbox and
MIME/thread semantics; SMS requires a selected provider and delivery model; IRC
requires negotiated capabilities. This research does not define or enable those
providers, and they are not required for the initial messaging adapter sequence.

## Acceptance evidence to collect before each release

Record the API/SDK/schema version, account mode, native granted scopes and test
conversation. Exercise inbound/outbound text and every advertised media type
separately; thread/quote/card/reaction semantics; denied permissions; source- and
principal-bound history/search pagination; token/URL expiry; callback duplicates;
rate rejection; crash recovery and unknown outcomes. Re-run relevant capability
checks on reinstall, scope/role change and SDK/API upgrade. Claims that remain U
or R-only stay gated until these checks establish the actual authorized surface.
