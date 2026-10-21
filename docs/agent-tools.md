# Agent communication tools

CG exposes five tools over authenticated Streamable HTTP at `/mcp/`. The same
inventory serves all authorized conversations. A new group or channel does not
create another tool or MCP connection. Supported adapters are OneBot, QQ official
Bot, Weixin iLink, Slack, Feishu/Lark and Discord. Each exposes only its implemented
capabilities; see the [new adapter setup guide](slack-lark-discord.md).

| Tool | Use |
| --- | --- |
| `discover` | Select conversation handles and inspect capabilities/schemas |
| `read` | Read history, received media, notifications, or native resources |
| `send_message` | Queue text, supported media/cards, or a stream fragment |
| `execute_action` | Queue reactions, recall, typing, or native mutations |
| `get_operation` | Inspect message/action outcomes |

## Select a conversation

Call `discover` with no arguments. Choose a returned conversation `id` and pass it
as `route_id`. Host keys and account-history handles require an explicit handle;
a single-source route token may omit it. `can_read` and `can_reply` are separate.
Never substitute native account, user, group, or guild IDs for a gateway handle.

Call `discover(resource="capabilities", route_id=...)` for adapter support, route
authorization, content limits, and upstream restrictions. Application permissions
are not inferred from gateway grants. `discover(resource="operations", route_id=...)`
returns exact common argument schemas plus applicable namespaced native operations.
Read-only account-history handles do not grant resource or mutation authority.

## Read history and attachments

```json
{
  "name": "read",
  "arguments": {
    "resource": "messages",
    "route_id": "route_EXAMPLE",
    "source": "gateway",
    "arguments": {"limit": 20}
  }
}
```

The response identifies its source and incomplete coverage. It contains received
messages and confirmed sent messages, in chronological order. To read earlier
records, pass `next_cursor` as `arguments.cursor`. A full last page may return a
cursor followed by an empty page. Every request reapplies current read grants.
`source: "platform"` reads native history on Slack/Lark/Discord source routes
under a separate `history.read` grant. Other adapters reject this source explicitly.
Native reads never fall back to gateway records.

Use `resource: "image"` with `arguments: {"source_message_id": "ID", "image_index": 0}`
for pixels, or `resource: "attachment"` with `attachment_index` for a received file.
These indices come from received history, not outgoing media descriptions.
Native MCP image/audio/resource content remains typed. Let the Agent Host place
video and documents in its sandbox for command-based parsing when the model
cannot consume the native media type. No local path or arbitrary download URL is
accepted by these reads. `resource: "events"` reads authorized sanitized QQ events.

## Send content

```json
{
  "name": "send_message",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "content": [
      {"type": "text", "text": "A picture"},
      {"type": "image", "filename": "picture.png", "media_type": "image/png", "data_base64": "BASE64_BYTES"}
    ]
  }
}
```

One text part and at most one media part are supported. Media types are `image`,
`audio`, `video`, and `file`, subject to the adapter and conversation. Provide
exactly one of `data_base64` or `url`. QQ allows public HTTPS sources and up to
16 MiB inline data; guild conversations allow image sends. OneBot allows inline
images up to 4 MiB. QQ images are PNG/JPEG; OneBot also allows GIF/WebP. Weixin is
text-only. Unsupported combinations fail before anything is queued.

Text uses optional `format: "markdown"` on QQ and Slack. `reply_to` references a known
source-authorized message. QQ options use `native: {"qq": {"keyboard": ...}}`,
or `embed`/`ark` for guild cards. QQ cards can use empty `content`. QQ/OneBot media cannot
currently combine with Markdown, a quote, native options, or a stream. New-platform
combinations follow their adapter guide.

QQ sends default to proactive when the route has `message.proactive`; otherwise
all routes keep passive tool delivery. `delivery` explicitly selects `passive`,
`proactive`, or C2C-only `wakeup`. Agent final output is never sent. See [QQ delivery and limits](qq-platform.md#message-creation).

For a QQ C2C stream, send one text part with
`stream: {"index": 0}`. Reuse the returned `stream_handle`, increment `index`, and
set `finished: true` on the last fragment. Optional `input_mode` is `append` or
`replace`. Do not combine streams with explicit delivery, quotes, or native options.

## Execute actions and inspect outcomes

```json
{
  "name": "execute_action",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "action": "reaction",
    "arguments": {"source_message_id": "ID", "emoji_id": "203", "add": true}
  }
}
```

Other common actions are `recall` (`message_id`, optional `hide_tip`) and `typing`
(`seconds`). They retain their platform restrictions and grants. QQ reactions
are guild-only; OneBot requires an optional compatible extension. There is no
fallback emoji message. Reaction permission does not grant recall or moderation.

Native reads use `read(resource="native")`; mutations use
`execute_action(action="native")`. Both take arguments such as
`{"operation": "qq.get_group", "parameters": {}}`, using an operation discovered
for that route. Unknown names, unregistered parameters, nested route overrides,
and arbitrary API paths are rejected. The gateway supplies the bound destination.

Every queued send/action returns `operation_id`. Call
`get_operation(operation_id=..., route_id=...)` to inspect it. `queued` and
`sending` are pending; `sent` and `succeeded` confirm platform acceptance, not
recipient reading. `blocked` means gateway checks stopped execution, `failed`
means explicit rejection, and `unknown` means an effect may already have occurred.
Verify unknown outcomes in the channel before repeating. Repeated invocations
are not deduplicated automatically.

## Update an existing client

Refresh the MCP tool inventory and enable the five tools in the Agent Host.
Replace old tool names in Agent instructions and saved calls:

| Previous calls | Replacement |
| --- | --- |
| `list_conversations`, `list_channel_operations` | `discover` |
| `read_conversation`, `read_image`, `read_attachment`, `read_channel_events`, `query_channel_resource` | `read` with the corresponding resource |
| `send_message`, `send_image`, `send_media`, `send_stream_message` | `send_message` with content and optional stream |
| `set_message_reaction`, `recall_message`, `set_typing`, `perform_channel_action` | `execute_action` |
| `get_channel_action` | `get_operation`; it also supports message sends |

Old tool aliases are removed. Existing keys, grants, route bindings, queues, and
receipts persist. Native operation names acquire the `qq.` namespace only at the
MCP boundary. CG does not rewrite the Host's enabled tools or Agent prompt.

## Slack, Lark and Discord

The [adapter guide](slack-lark-discord.md) covers the first new-platform slice,
including `history.read`, `source: "platform"` history/assets, explicit thread
addresses, typed reactions, inline media and upload effects. The public tool
inventory remains five. Earlier QQ-specific format restrictions in this guide
must not be inferred as support for new-platform cards, native search or streams.
Those features remain unimplemented for the new adapters.
