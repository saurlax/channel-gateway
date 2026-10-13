# Channel adapters

## Account binding

A channel connection represents one bot account. Its `kind` is `onebots`, `qq`,
or `weixin`; `a13n` connections are Agent Hosts. There is no instance discovery,
bot multi-selection, or implicit aggregation. Several connections can target the
same OneBots instance, each with an independent account binding.

CG's connection ID scopes conversation addresses, private protocol state, and
input deduplication. Platform account IDs are not globally unique CG identities.
Account URLs, protocol kind/transport, and configured account or application IDs
are immutable through management. Credentials are replaceable. Weixin pairing
establishes its account ID and official API host; later pairing must use that same
account. A different account requires another connection.

Administrator-only `DELETE /api/v1/connections/{id}` removes a channel's stored
configuration and credentials, stops its listener/socket/polling task, cancels
pending pairing, and clears private protocol state. Agent Host deletion follows
the [access contract](02-identity-and-access.md#resource-deletion).
Deletion atomically disables all associated routes, including concrete children,
blocks queued/submitted inputs and queued replies, and preserves routes,
message history, thread bindings, and completed delivery receipts. Retained routes
cannot be edited or re-enabled for the deleted channel. Their MCP authority is
disabled under the [access contract](02-identity-and-access.md#route-access).
Deletion does not retract external requests already in flight, sent messages, or
upstream a13n runs. Re-adding the same account creates a new connection identity;
it does not restore old routes or credentials. The console requires explicit
confirmation from the channel's management dialog.

## Transport compatibility

All implemented adapters project text and send text. They retain authenticated
incoming packets, including non-text messages and unsupported events, in the
private channel-event journal before projection. Media processing, voice, live
calls, moderation actions, native history retrieval, QQ guild channels, and
Weixin group conversations are outside this baseline. A platform's installer CLI
is not an additional wire transport.

| Kind      | Transport      | Incoming events                         | Sending                 |
| --------- | -------------- | --------------------------------------- | ----------------------- |
| `onebots` | `http`         | None                                    | Account HTTP API        |
| `onebots` | `http_reverse` | Signed account HTTP callback            | Account HTTP API        |
| `onebots` | `ws`           | CG connects account universal WebSocket | Same socket             |
| `onebots` | `ws_reverse`   | Account connects CG universal WebSocket | Same socket             |
| `qq`      | `websocket`    | Official QQ Gateway                     | Official QQ OpenAPI     |
| `qq`      | `webhook`      | Official Ed25519 HTTP callback          | Official QQ OpenAPI     |
| `weixin`  | `polling`      | Official iLink long polling             | Official iLink HTTP API |

A OneBot HTTP API alone is not an event subscription and cannot automatically
wake an Agent. Native QQ and Weixin have separate authentication and event
formats; an HTTP/WS URL does not make them OneBot-compatible. Existing OneBot
records with no `transport` retain `http_reverse` semantics. New OneBot records
also default to `http_reverse`; QQ defaults to `websocket`, Weixin to `polling`.
The additive private protocol-state table does not rewrite existing records.

Channel management views expose capabilities (receiving, sending, conversation
kinds, and content types), transport, and process-local status with safe
diagnostics. `callback_ready` indicates configured callback handling, not observed
upstream connectivity; `http_only` indicates sending without subscription.
Socket/polling statuses include `connecting`, `connected`, `reconnecting`,
`awaiting_connection`, `inactive`, and `login_required`. These observations are
not delivery receipts. The standalone process owns tasks and closes them before
storage and HTTP-client shutdown. Transient connection failures reconnect with
bounded backoff; Weixin expiry requires explicit pairing.

## OneBot v11

OneBot configuration requires an access token and expected `self_id`. Forward
HTTP and reverse HTTP use a complete account HTTP(S) API base. Forward WebSocket
uses an account WS(S) universal endpoint. Reverse WebSocket has a gateway-generated
URL and accepts no upstream API URL. Only reverse HTTP uses a callback secret,
with at least 16 characters.

`POST /api/v1/hooks/onebots/{connection_id}` verifies the raw body using
`X-Signature: sha1=<HMAC-SHA1(signing_secret, raw_request_body)>` before projection.
Forward WebSocket authenticates with `Authorization: Bearer <access_token>`.
Reverse WebSocket at `/api/v1/ws/onebots/{connection_id}` requires that bearer
credential, exact `X-Self-ID`, and `X-Client-Role: Universal`. Browser origins,
wrong accounts/roles, and duplicate active sockets are rejected. Every message
also checks its `self_id`; display names cannot select another source.

Sending calls `send_msg` with an explicit private/group destination and text
segment array. HTTP uses bearer authentication and `/send_msg`. Socket requests
use unpredictable `echo` values and wait at most 15 seconds for their matching
response. Success requires `status: ok`, `retcode: 0`, and a message ID. A lost or
invalid receipt follows the [reply failure contract](03-routing-and-delivery.md#reply-lifecycle).
There is no automatic resend after a possibly effective request.

Incoming events require `post_type: message` and `message_type: private` or
`group`. Array messages contribute text segments; strings have CQ segments removed
and supported CQ text escapes decoded. Bot self-messages and unsupported/empty
content are retained as raw packets without fabricated text delivery.

## QQ official Bot

A connection requires `app_id`, write-only ASCII `app_secret`, and either receive
mode. It uses the fixed official token and OpenAPI endpoints; management rejects
arbitrary API hosts, imported access tokens, and OneBot identity/secret fields.
Token acquisition posts application credentials to
`https://bots.qq.com/app/getAppAccessToken`. API requests to
`https://api.sgroup.qq.com` use `Authorization: QQBot <access_token>`. Tokens are
cached in memory with early expiry, and invalidated on an API authentication
failure. Sending is not retried on authentication failure.

Gateway mode obtains the bot identity and official WSS URL through OpenAPI. It
handles Hello, Identify (group/C2C intent), heartbeat/acknowledgement, Resume,
reconnect, and invalid session opcodes. Session ID and sequence are durable;
resume position advances only after input handling returns. Persistence failure
does not acknowledge a later sequence. Missing heartbeat acknowledgement closes
the connection. Invalid sessions and expiry close codes clear the resume state;
application credential replacement also starts a new session.
Every received bounded frame, including Hello, heartbeat acknowledgements,
notifications, bot messages, and unsupported dispatches, is archived before
handling. Resume position advances only after the archive and any normalized
message/delivery records are durable.

`POST /api/v1/hooks/qq/{connection_id}` implements official opcode-13 validation,
signing `event_ts + plain_token` with Ed25519 using the AppSecret-derived key.
Other packets require `X-Signature-Timestamp` and `X-Signature-Ed25519` verified
against the raw body. Dispatch acknowledgement `{"op":12,"d":0}` follows
successful durable input handling, not Agent execution. Validation challenges
are retained separately from authenticated message ingestion. Invalid signatures
are rejected without recording unverified traffic. Signed malformed packets are
retained with a safe projection diagnostic before returning HTTP 422.

Supported dispatches are `C2C_MESSAGE_CREATE`, `GROUP_AT_MESSAGE_CREATE`, and
`GROUP_MESSAGE_CREATE`. Account-scoped OpenIDs identify users and groups; ordinary
QQ numbers are not substitutes. Replies post text to `/v2/users/{openid}/messages`
or `/v2/groups/{openid}/messages` with the source `msg_id` and a durable increasing
`msg_seq` per source message. Event-linked output uses that event's source ID;
MCP replies use the latest authorized incoming conversation context. QQ's passive
reply window and message quota still apply. A successful receipt requires a
platform message ID.

Receiving ordinary group messages requires QQ's **receive all messages** feature
to be enabled for the bot. The existing group/C2C intent covers that event; local
archiving cannot retrieve messages QQ never pushed. With the current routing
baseline, every authorized projected text message can start Agent work; recording
ordinary chatter without activating the Agent is not a separate policy yet.

QQ source and receipt message IDs are opaque strings accepted up to 1024
characters and preserved exactly for deduplication, passive reply correlation,
and receipts. They are separate from account, sender, and conversation identifiers,
which remain bounded to 128 characters. Official group message IDs can exceed
128 characters; applying the account identifier bound to them would discard valid
group events and misclassify successful send receipts.

## Weixin official iLink

An unpaired connection uses `https://ilinkai.weixin.qq.com`. Administrator-only
`POST /connections/{id}/login` creates a QR session with an unpredictable login ID
and a five-minute local expiry. QR content is rendered locally as an SVG data URL;
no remote QR image is fetched. Creating a new QR supersedes the previous one.
`POST /connections/{id}/login/status` polls that exact session and optionally
submits a numeric verification code. `POST /connections/{id}/login/cancel`
invalidates only its matching session. QR polling sends application headers,
without a bot bearer credential. Login sessions are in memory and end at restart.

Confirmation stores `bot_token`, `ilink_bot_id`, and official `baseurl`, and starts
polling. Tokens never appear in management or pairing responses. Pairing checks
that the configuration still matches its starting snapshot and that an existing
account ID is unchanged. Redirect and confirmation hosts must be HTTPS root
URLs under `*.weixin.qq.com`, without embedded credentials, nonstandard ports,
queries, or fragments. Imported credentials require both bot ID and token.

Authenticated requests use `AuthorizationType: ilink_bot_token`, bearer token,
random encoded `X-WECHAT-UIN`, and documented application/version headers. Wire
metadata follows the upstream 2.4.8 protocol baseline; CG's `bot_agent` identifies
Channel Gateway separately. Long polling calls `getupdates` with a durable cursor
and a 45-second timeout. A read timeout repeats the same cursor. The next cursor
is persisted only after every batch item is persisted, ignored, or denied; a
storage failure leaves the prior cursor intact. Platform `ret`/`errcode` -14 sets
`login_required` and stops reconnection until the administrator pairs again.

Only user-authored private text addressed to the configured bot is accepted.
Group messages, bot echoes, other recipients, and non-text content are retained
in the raw journal without text delivery.
A valid authorized input atomically records a private reply context alongside
its message: account, conversation, source ID/time, authorized sender, and
`context_token`. Older inputs do not replace newer contexts. Context tokens are
bounded, stored server-side, and excluded from management message views, MCP,
and Host input.

`sendmessage` uses the exact account/conversation's latest authorized context,
text item, bot message type/state, and CG reply ID as `client_id`. Missing context
blocks before a send request. A successful HTTP JSON response with no nonzero
`ret` or `errcode` confirms provider acceptance; either code may be omitted by
the official send endpoint. iLink returns no platform message ID, so the stored
receipt correlation is the CG client ID.
This does not prove recipient delivery or reading. Sending is never automatically
retried after a possible external effect.

## Shared projection and authorization

### Route-independent event retention

`channel_events` is an append-only journal of authenticated channel arrivals.
Each row retains the source connection, transport, original packet bytes, event
type, receive time, projection/handling state, and optional normalized message
ID. Recording does not require a route or an allowed sender. OneBot socket
receipts and notices, QQ protocol/control frames, and individual Weixin update
items use the same retention boundary. Repeated arrivals create separate journal
rows while normalized messages and Agent submissions remain deduplicated.

Non-text content, attachments, bot/self messages, account-mismatched payloads,
unsupported events, and malformed bounded packets remain in this journal even
when no text is projected. Failed processing leaves its raw packet retained as
`received` or `invalid`; storage failures prevent callback acknowledgement or
cursor/resume advancement. Oversized packets are rejected under transport bounds.
Stale/deleted connection snapshots cannot record or project new arrivals.

The journal is private server storage. Incoming protocol context tokens and
attachment metadata may be present; it is never exposed in management message
views, MCP tools, or Host inputs. Outbound authentication packets, API credentials,
and HTTP authorization headers are not journaled. No retention expiry is defined.
Existing normalized message history remains intact on upgrade; previously
discarded original packets cannot be reconstructed.

### Text projection and delivery

Normalized messages carry the CG account address, private/group conversation,
source message ID, verified sender ID, text, and timestamp. Private identity uses
the sender; group identity uses the platform group. Connection credentials and
reply contexts never enter the normalized message. Accepted input enters the
[delivery workflow](03-routing-and-delivery.md), using the same sender policies,
conversation isolation, scoped MCP, and configured reply permissions for every protocol.

Callbacks/socket frames are bounded to 1 MiB and incoming text to 60000 characters.
Invalid signatures reject callbacks with HTTP 401; oversized callbacks return
413; malformed supported callback messages return 422. Malformed socket/poll
items are ignored with safe diagnostics so a poison item cannot permanently pin
reconnection. Storage errors are not classified as successfully handled inputs.
Unsupported events, account mismatches, bot messages, and empty text do not enter
text delivery; their authenticated packets remain in the private journal.
Missing/disabled routes and unauthorized senders are blocked from delivery;
supported verified messages are retained for administrator identity discovery,
as defined by the [routing contract](03-routing-and-delivery.md#all-conversation-routes-and-identity-discovery). Authorized messages
return `accepted` or `duplicate` with the same event ID. Deduplication scopes the
CG connection, conversation kind/ID, and source message ID.

Credential replacement closes existing sockets/tasks, invalidates pending pairing,
and restarts the connection. In-flight external effects cannot be retracted.
Reply context and polling cursors survive same-account credential changes; QQ
resume authentication is reset. Old authenticated connection snapshots cannot
initiate new routed input after a credential change. Credentials and current
route access are server authority, independently of any UI state.

## Protocol references

- [OneBot v11](https://github.com/botuniverse/onebot-11)
- [Tencent QQ Bot SDK](https://github.com/tencent-connect/qqbot-nodejs)
- [QQ full-group-message event](https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html)
- [Tencent Weixin protocol](https://github.com/Tencent/openclaw-weixin/blob/main/docs/protocol.md)
