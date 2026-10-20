# QQ platform tools

CG supports QQ official private, group, guild-channel, and guild direct-message
conversations. QQ application permissions remain authoritative. Invitation-only,
private-domain, administrator, audio-bot, and client-version restrictions are
reported by the operation catalog; CG does not grant those platform permissions.

## Configure conversations and resource authority

In a route's Sources, select a user, group, guild child channel, or guild direct
message. A guild source uses the **child channel ID**, not the parent guild ID.
A guild direct-message source uses the **DM guild ID** from QQ's DM event or
created session. The same ID in another conversation kind or account is separate.
Wildcard choices cover future conversations of the selected kind.

Send an authorized message to establish verified guild context before using
guild-scoped resource operations. Agents cannot supply a replacement guild ID.
For operations on another child channel, `target_channel_id` is accepted only
after verifying that the child belongs to this same guild. Creating a DM session
does not authorize a new reply destination; configure its returned DM guild ID
as a source, or use an existing wildcard DM source.

Connections contains **Platform action authorization**. All new grants start
disabled. Select only the required resource authority:

| Grant | Authority |
| --- | --- |
| `resource.read` | Native resource and sanitized notification reads |
| `message.react` | Independent reactions on source-authorized messages |
| `message.recall` | Recall the bot's confirmed sent messages |
| `message.recall_members` | Recall source-authorized received messages |
| `message.proactive` | Proactive and C2C recall-period message delivery |
| `group.manage` | Group membership, blacklist, moderation, join-request actions |
| `guild.manage` | Guild members, roles, permissions, and subchannel administration |
| `content.manage` | Guild announcements, pins, schedules, forums, and audio |
| `bot.manage` | Account-wide menus, panels, share links, and automatic approval policies |

These are gateway resource grants. Enable the corresponding MCP tools separately
in the Agent Host; CG never rewrites the Agent's enabled tool list. Readable-channel
grants do not confer these permissions. Parent route grants propagate to their
current source bindings and are checked again before execution.

Guild reactions can run on a final-output route with `message.react`. QQ message
tools can also run there with `message.proactive`. Legacy MCP OneBot reactions retain
their existing scope and require the provider's optional compatible extension.

## Message creation

`send_message` accepts plain text or `message_format: "markdown"`, optional
`keyboard`, and `reply_to_source_message_id` for a locally known quoted message.
Guild messages may also supply native `embed` or `ark` content where QQ permits
it. Structured message options are bounded to 64 KiB. Markdown and button
availability remain governed by the QQ application and client.

For automatic text replies plus proactive tool sends, choose **Final run output**,
enable **Proactive messaging** (`message.proactive`), and configure the desired
MCP tools on the Agent. Final text replies to the triggering message; calls to
`send_message`, `send_image`, and `send_media` default to proactive delivery.
An existing MCP route without the proactive grant keeps its passive default.
`list_conversations` reports `default_delivery` and `can_send_proactively`.

Pass `delivery: "passive"` explicitly when a tool should reply using the latest
authorized incoming context. `delivery: "proactive"` and C2C-only `"wakeup"`
require `message.proactive`. Proactive sends omit the original message/event ID
and passive sequence. They need no fresh incoming message. Final output retains
its own original context and never automatically becomes proactive after expiry.
Delivery is fixed when queued, and authority is rechecked before sending and
between upload steps. Avoid repeating tool-sent content in final output.

QQ must allow proactive messages for the user/group; check `get_group_bot_state`
and its `allow_proactive_msg` field for groups. A gateway grant does not change
that QQ setting. Official group limits are 30 messages/minute per unverified bot
or 60/minute per verified bot, 20/minute per group, and 1000/group/day, not 30 QPS.
CG uses conservative persistent ceilings: 5 proactive attempts/second and
30/minute per AppID, plus 20/minute and 1000/day per destination. Daily limits
reset at midnight in China Standard Time. These apply to all proactive/wakeup
tool sends; QQ's additional C2C/guild/DM limits remain authoritative.
Throttled messages stay queued without uploads or API retries. Other
destinations and passive replies continue. Reservations survive restart and
consume budget even if a claimed attempt fails. Explicit rejection is `failed`;
uncertain delivery is `unknown`. Neither is automatically retried.

`send_media(media_kind, media_type, filename, data_base64=None, url=None,
caption='', route_id=None)` accepts exactly one source. `media_kind` is `image`,
`audio`, `video`, or `file`. Inline base64 is limited to 16 MiB decoded; larger
than 4 MiB uses prepare/part/finish/merge internally. A public HTTPS URL delegates
download to QQ under QQ's own size limits. CG accepts no local file path.
Presigned part uploads carry no bot token or cookies. Upload tickets remain private.

The documented guild send endpoint provides image URL/multipart fields and
Embed/ARK/Markdown; it does not document the C2C/group generic file-upload flow.
CG therefore sends native images in guild conversations, receives other media,
and exposes guild audio control separately. It refuses unsupported generic guild
media sends before queuing rather than guessing undocumented fields. QQ's broad
message-type overview and older guild send page describe different surfaces.

For C2C streaming, call `send_stream_message(text, index=0)`, retain the returned
`stream_handle`, increment `index` for each fragment, and set `finished: true` on
the last fragment. Fragments keep the first message's passive context. `input_mode`
is `append` or `replace`; already published prefixes remain subject to QQ's rules.
Unknown or failed fragments stop continuation. `set_typing(seconds=30)` is a
separate C2C status action, with a 1–60 second duration.

## Actions and resource discovery

Reactions and recalls do not send message content:

```json
{
  "name": "set_message_reaction",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "source_message_id": "KNOWN_MESSAGE_ID",
    "emoji_id": "203",
    "emoji_type": 1,
    "add": true
  }
}
```

QQ reactions are guild-channel-only; `add: false` removes a reaction. Use
`recall_message(message_id, route_id)` for a known sent or received message.
Own-message recall and member moderation require different grants. QQ retains
the final decision about ownership, permissions, and recall deadlines.

For other platform functions, first call `list_channel_operations(route_id)`.
Each entry includes its exact argument schema, scope, required grant, local
authorization, and platform restriction. Use `query_channel_resource` for reads
and `perform_channel_action` for mutations. Neither accepts a raw API URL/path.

```json
{
  "name": "perform_channel_action",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "operation": "set_group_mutes",
    "arguments": {
      "members": [{"op": "del", "member_openid": "MEMBER_OPENID"}]
    }
  }
}
```

Mutations return an `action_id`. Poll `get_channel_action(action_id, route_id)`:
`queued`/`sending` are pending, `succeeded` has a confirmed platform receipt,
`failed` is an explicit QQ rejection, `blocked` means gateway authority prevented
execution, and `unknown` means an effect may have occurred. Verify unknown actions
in QQ before repeating them. Restart never automatically repeats interrupted
actions. Existing Messages history shows these as **Channel action**, separately
from ordinary Agent replies. Management action views expose no arguments or
credentials. Action results become unreadable after the required grant is revoked.

## Received attachments, cards, and notifications

`read_conversation` exposes image and attachment indices and sanitized card data.
`read_image` returns image pixels; `read_attachment(source_message_id,
attachment_index=0, route_id=None)` returns native MCP audio or an embedded file
resource, at most 16 MiB. Reads recheck current authority after download and
never accept arbitrary source URLs. a13n receives native URL and JSON input parts;
its model and outbound endpoint policy determine how these are consumed.

`read_channel_events` returns sanitized notifications for the source conversation
or verified parent guild. Account notifications require `bot.manage` in addition
to `resource.read`. This includes friend/bot/member changes, receive switches,
join requests, interactions, subscription status, reactions, message audit,
forum changes, and audio/member events. It does not expose the raw event journal
or activate the Agent for every notification. Button/menu callbacks are acknowledged
after durable retention without waiting for Agent execution. Callbacks with a
verified actor and conversation are delivered under the existing source policy;
the response uses the event's private reply context. Feedback and system changes
remain notifications unless an Agent explicitly reads them.

## Gateway subscriptions and client-side features

QQ connections expose an optional **QQ Gateway intents** integer. The default
subscribes to standard guild metadata, reactions, DMs, C2C/group, interactions,
message audit, and public guild messages. Private-domain messages, members,
forums, and audio events need appropriate application permissions and an explicit
intent mask. The mask is an event subscription, not a permission grant. It can be
set at creation or updated from Manage; updates restart the connection. Existing
WebSocket resume state is reset on configuration replacement.

The official SDK's miniapp APIs (`qq.getGuildInfo`, encrypted open data, launch
parameters) run in a QQ miniapp client. CG supplies verified guild/conversation
identifiers and service/keyboard link configuration; it does not execute the
miniapp SDK on the server. Deprecated child-channel announcement APIs are replaced
by pins. These distinctions avoid reporting an obsolete or client-only function
as a generally available server operation.

Reference: [official API index](https://bot.q.qq.com/wiki/develop/api-v2/autogen/)
and [accepted gateway contract](../spec/06-qq-platform.md).

## Resource operation inventory

The following catalog is implemented in addition to the dedicated message,
stream, typing, reaction, recall, attachment, and notification tools. Availability
is still subject to the listed route grant and QQ application permissions.

| Operation | Mode | Scope | Required grant | Platform restriction |
| --- | --- | --- | --- | --- |
| `get_bot` | Read | account | `resource.read` | platform permissions apply |
| `list_guilds` | Read | account | `bot.manage` | platform permissions apply |
| `create_share_link` | Action | account | `bot.manage` | platform permissions apply |
| `get_menu` | Read | account | `bot.manage` | platform permissions apply |
| `set_menu` | Action | account | `bot.manage` | platform permissions apply |
| `list_panels` | Read | account | `bot.manage` | platform permissions apply |
| `create_panel` | Action | account | `bot.manage` | platform permissions apply |
| `get_panel` | Read | account | `bot.manage` | platform permissions apply |
| `update_panel` | Action | account | `bot.manage` | platform permissions apply |
| `delete_panel` | Action | account | `bot.manage` | platform permissions apply |
| `set_panel_targets` | Action | account | `bot.manage` | platform permissions apply |
| `get_group` | Read | group | `resource.read` | platform permissions apply |
| `get_group_bot_state` | Read | group | `resource.read` | platform permissions apply |
| `list_group_members` | Read | group | `resource.read` | invitation only |
| `get_group_member` | Read | group | `resource.read` | invitation only |
| `remove_group_members` | Action | group | `group.manage` | invitation only; administrator |
| `list_group_blacklist` | Read | group | `resource.read` | invitation only |
| `set_group_blacklist` | Action | group | `group.manage` | invitation only; administrator |
| `list_join_requests` | Read | group | `resource.read` | administrator |
| `approve_join_request` | Action | group | `group.manage` | administrator |
| `get_group_mutes` | Read | group | `resource.read` | administrator |
| `set_group_mutes` | Action | group | `group.manage` | administrator; maximum 30 days |
| `list_join_strategies` | Read | account | `bot.manage` | platform permissions apply |
| `create_join_strategy` | Action | account | `bot.manage` | platform permissions apply |
| `update_join_strategy` | Action | account | `bot.manage` | platform permissions apply |
| `delete_join_strategy` | Action | account | `bot.manage` | platform permissions apply |
| `execute_join_strategy` | Action | account | `bot.manage` | platform permissions apply |
| `set_join_whitelist` | Action | account | `bot.manage` | platform permissions apply |
| `get_guild` | Read | guild | `resource.read` | platform permissions apply |
| `open_direct_message` | Action | guild | `guild.manage` | platform permissions apply |
| `list_subchannels` | Read | guild | `resource.read` | platform permissions apply |
| `create_subchannel` | Action | guild | `guild.manage` | private-domain administrator |
| `get_subchannel` | Read | channel | `resource.read` | platform permissions apply |
| `update_subchannel` | Action | channel | `guild.manage` | private-domain administrator |
| `delete_subchannel` | Action | channel | `guild.manage` | private-domain administrator |
| `get_online_members` | Read | channel | `resource.read` | platform permissions apply |
| `list_guild_members` | Read | guild | `resource.read` | private-domain only |
| `get_guild_member` | Read | guild | `resource.read` | platform permissions apply |
| `remove_guild_member` | Action | guild | `guild.manage` | administrator |
| `list_roles` | Read | guild | `resource.read` | platform permissions apply |
| `list_role_members` | Read | guild | `resource.read` | private-domain only |
| `create_role` | Action | guild | `guild.manage` | administrator |
| `update_role` | Action | guild | `guild.manage` | administrator |
| `delete_role` | Action | guild | `guild.manage` | administrator |
| `add_role_member` | Action | guild | `guild.manage` | administrator |
| `remove_role_member` | Action | guild | `guild.manage` | administrator |
| `get_member_permissions` | Read | channel | `resource.read` | platform permissions apply |
| `set_member_permissions` | Action | channel | `guild.manage` | administrator |
| `get_role_permissions` | Read | channel | `resource.read` | platform permissions apply |
| `set_role_permissions` | Action | channel | `guild.manage` | administrator |
| `mute_guild_member` | Action | guild | `guild.manage` | administrator |
| `mute_guild` | Action | guild | `guild.manage` | administrator |
| `get_message_settings` | Read | guild | `resource.read` | platform permissions apply |
| `get_api_permissions` | Read | guild | `resource.read` | platform permissions apply |
| `request_api_permission` | Action | guild | `guild.manage` | platform permissions apply |
| `create_announcement` | Action | guild | `content.manage` | platform permissions apply |
| `delete_announcement` | Action | guild | `content.manage` | platform permissions apply |
| `list_pins` | Read | channel | `resource.read` | platform permissions apply |
| `list_reaction_users` | Read | channel | `resource.read` | platform permissions apply |
| `add_pin` | Action | channel | `content.manage` | platform permissions apply |
| `remove_pin` | Action | channel | `content.manage` | platform permissions apply |
| `list_schedules` | Read | channel | `resource.read` | platform permissions apply |
| `get_schedule` | Read | channel | `resource.read` | platform permissions apply |
| `create_schedule` | Action | channel | `content.manage` | administrator |
| `update_schedule` | Action | channel | `content.manage` | administrator |
| `delete_schedule` | Action | channel | `content.manage` | administrator |
| `list_threads` | Read | channel | `resource.read` | private-domain only |
| `get_thread` | Read | channel | `resource.read` | private-domain only |
| `create_thread` | Action | channel | `content.manage` | private-domain only |
| `delete_thread` | Action | channel | `content.manage` | private-domain only |
| `control_audio` | Action | channel | `content.manage` | audio bot permission required |
| `join_microphone` | Action | channel | `content.manage` | audio bot permission required |
| `leave_microphone` | Action | channel | `content.manage` | audio bot permission required |
