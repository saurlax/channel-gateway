# QQ platform operations

This contract extends the text/image baseline to official QQ C2C, group,
guild-channel (`guild`), and guild direct-message (`guild_dm`) conversations.
Platform credentials, approval requirements, private-domain restrictions, and
invitation-only APIs remain enforced by QQ; listing an implemented adapter
operation never asserts that an application has obtained that permission.

## Conversation identity and ingestion

`private` uses a user OpenID, `group` a group OpenID, `guild` a child-channel ID,
and `guild_dm` the DM guild ID returned by QQ. Connection IDs remain part of every
identity. Guild IDs are taken from authenticated inbound events and retained in
private conversation context; tool arguments cannot replace them. Guild and DM
IDs are separate namespaces even when their native strings coincide. Existing
private/group addresses, source precedence, sender policies, and thread delivery
semantics remain compatible. QQ Gateway intents cover the implemented guild,
message, reaction, membership, interaction, forum, and audio event families.

Messages project bounded text, images, audio, video, files, received cards, and
quoted/nested message content. The verified outer author is authoritative.
Attachments retain private source URLs and expose opaque indices and safe metadata
in history. Native Host URL parts carry supported attachments; MCP can retrieve
bounded attachment bytes separately. Private authentication fields are removed
from structured projections. Unsupported packets remain in the private raw journal.
Direct and quoted group files use the same attachment projection, including
QQ's regional `*.ftn.qq.com` file CDN. An empty quoted text field must not discard
its supported file attachment. The shared media URL validation and credential-free,
bounded download rules still apply; this does not allow arbitrary `qq.com` hosts.
Verified Host context includes each attachment's index, kind, media type, and
filename. A Host can place video and documents in its sandbox for command-based
parsing without passing their raw binary contents to the primary model. A generic
CDN Content-Type must not replace a more specific authenticated attachment type
in MCP reads. UTF-8 text attachments return text resources rather than binary
model input; other attachments retain typed native resources.

Platform notifications have their own sanitized durable projection, independent
of messages and Agent activation. Authorized event reads match the selected
conversation or its verified guild. Account-wide notifications require a bot-wide
grant. Interaction callbacks are acknowledged promptly after durable recording;
this acknowledges receipt, not completion of a button's requested operation.
Button/menu events with a verified actor, source conversation, and event time
project as interaction input under the existing source and sender policy. Their
opaque source ID is distinct from platform message IDs; the private event reply
ID is saved atomically with input. Replies use `event_id`, not a fabricated
`msg_id`. Other notifications do not activate Agents by default. Interaction
entries cannot be targets for message recall or reactions.
Duplicate interaction events do not deliberately issue another acknowledgement.

## MCP design

QQ uses the fixed five-tool inventory in [Agent tools](04-agent-tools.md).
`send_message` handles text, Markdown, media, cards, and ordered C2C stream
fragments. `execute_action` handles reactions, recall, typing, and registered
resource mutations. `read` handles received images/attachments, sanitized events,
and registered resource queries. `discover` returns schemas and capabilities;
`get_operation` queries either durable ledger. QQ message options use the `qq`
native namespace, and resource operations use `qq.<catalog-name>`.

No tool accepts an arbitrary HTTP method, API path, credential, or destination.
Resource operations use a fixed catalog and validated argument allowlists. A
conversation-scoped operation obtains its destination from the route. A guild
operation obtains its guild from verified context. Child-channel operations that
take another channel ID verify guild membership before accessing it. Bot-wide
operations explicitly require a bot-wide grant. The catalog covers metadata,
menus/panels, group membership/blacklists/moderation/join approval strategies,
guilds/subchannels/members/roles/permissions, announcements/pins/schedules/forums,
audio/microphone control, message settings, and API-permission discovery/requests.
Deprecated subchannel-announcement APIs are excluded in favor of pins.
Miniapp SDK operations remain client-side; CG supplies the verified identifiers
and supported service-link configuration rather than executing a QQ client SDK.

## Explicit action grants

`action_grants` is a route resource-authorization list, separate from the Agent
Host's configured tool list. CG never changes the Agent's enabled tools. The
default is empty; upgrades grant no new management authority. Supported grants:

- `resource.read`: native resource and notification reads within source scope.
- `message.react`: reactions independently of final-output versus MCP replies.
- `message.recall`: recall the bot's own confirmed sent messages.
- `message.recall_members`: recall source-authorized received messages.
- `message.proactive`: proactive/recall-period sends without a passive source.
- `group.manage`: group membership, blacklist, moderation, and join-request actions.
- `guild.manage`: guild members, roles, permissions, and subchannel administration.
- `content.manage`: announcements, pins, schedules, forums, and guild audio.
- `bot.manage`: account-wide menus/panels/sharing and approval-policy administration.

Message tools require an authorized source destination. QQ routes with
`message.proactive` permit message tools on both final-output and MCP routes.
Without that grant, existing MCP routes retain passive tool replies, and
final-output routes reject message tools. Independent actions may also run on
final-output routes when explicitly granted.
Legacy MCP OneBot reactions retain their existing authority. Read-channel grants
never authorize mutations or native resource management. Parent grants propagate
to current children and are rechecked at invocation and execution. Removing a
grant, changing a source, disabling/deleting a route, or changing its channel
blocks queued actions before further external effects.

## Passive replies and proactive tools

Final Agent text on `reply_source: output` remains a passive reply to that
input's saved `msg_id` or `event_id`. A tool send does not suppress a later final
output; the Agent must avoid repeating already sent content. No expired passive
reply is automatically promoted to proactive delivery.

`send_message` accepts optional `delivery` for text and media.
Omitting it selects proactive delivery on QQ routes with `message.proactive`;
otherwise legacy MCP routes keep passive delivery. Explicit `passive`,
`proactive`, and C2C-only `wakeup` override that default under current authority.
Delivery is saved in the queued payload, never re-derived after grant changes.
Proactive bodies omit `msg_id`, `event_id`, and passive `msg_seq`. Final output
and explicit passive tool sends continue to use authorized native context.
OneBot and Weixin behavior does not change. Discovery returns `default_delivery`
and `can_send_proactively` alongside `can_reply`.
Verified a13n input context also includes `reply_source`, `default_delivery`,
and `can_send_proactively`, so ordinary final-text replies do not need a tool
discovery round merely to determine their delivery policy. These fields are
context, not replacement authority; tools and workers still recheck the route.

Proactive/wakeup attempts share persistent, conservative budgets by QQ AppID:
5 per rolling second, 30 per rolling minute, 20 per destination per rolling
minute, and 1000 per destination per China Standard Time calendar day. These
are local ceilings, not promises of platform entitlement; QQ certification,
guild/DM limits, permissions, opt-outs and other native quotas still apply.
Reservation and reply claiming are atomic and contain no external I/O. Claimed
attempts consume budget even after rejection or uncertain delivery. Throttled
replies remain `queued` without uploading or sending. Other conversations and
passive replies remain eligible; existing attempts survive restart. Queues are
selected fairly across destinations and delivery modes, preserving order within
each destination/mode. Grant revocation blocks queued sends, including throttled
ones. QQ rejection is `failed`; uncertain delivery is `unknown`. Neither is
automatically retried, and restart never repeats interrupted sends.

## Execution and receipts

New non-message actions use an additive `channel_actions` ledger, not the replies
table. States distinguish `queued`, `sending`, `succeeded`, `blocked`,
`failed` (explicit platform rejection), and `unknown`. Arguments and credentials
are private; safe operation/result metadata
is available by action ID only within current route authority. The worker claims
an action atomically. Interrupted `sending` actions become `unknown` on restart;
uncertain external effects are never automatically repeated. Actions appear as
actions in existing conversation history, without a separate approval page.
Existing legacy reaction reply records remain intact.

Native QQ endpoints may return objects, lists, or empty successful responses.
Nonzero QQ error codes are failures even on HTTP 200. Native permission errors
are not converted into successful capabilities. Requests have bounded response
sizes and deadlines, and redirects never carry credentials to another host.

Media uploads use the destination's own endpoint. URL uploads and bounded base64
uploads are supported (16 MiB decoded maximum); inline files larger than 4 MiB
use prepare/part PUT/finish/merge. MCP request bodies are bounded to the base64
expansion of 16 MiB plus 64 KiB for envelope/options. URL uploads remain subject
to QQ's own size and download policies.
Presigned PUTs use a separate credential-free transport, no redirects, and trusted
QQ/COS hosts. Upload tokens remain private. Authorization is checked between
external steps. QQ guild image multipart sending uses the guild API; unsupported
guild media types fail explicitly rather than silently becoming text. Stream
fragments are serialized per handle and checked against current route authority;
stream handles cannot select another account or conversation.

## References

- [Current QQ API and event index](https://bot.q.qq.com/wiki/develop/api-v2/autogen/)
- [Message types](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/overview.html)
- [Menus and command panels](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/menu-panel/index.html)
- [Guild API documentation](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/channel/index.html)

The operation catalog and tool schemas are the executable inventory. User
instructions describe platform-gated capabilities separately from local adapter
support. Tests use mocked platform receipts; live-account permission checks are
not implied by unit-test success.
