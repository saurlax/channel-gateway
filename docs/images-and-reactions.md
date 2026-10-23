# Images and message reactions

QQ official private/group/guild/DM conversations and OneBot support received images and
image replies. Weixin remains text-only. Image-only messages activate the Agent
under the same sources and sender policies as text. The console shows `[Image]`
for image-only input; it does not preview image pixels.

## Receive and read images

For a13n API Hosts, CG sends native URL attachment parts alongside the message.
The Host must allow the QQ CDN endpoints in its outbound endpoint policy and
use a model/configuration supporting image input. Temporary QQ download links
can expire; send the image again if the Host can no longer fetch it.

For MCP clients, call `discover(resource="conversations")`, select a readable `id`, then call
`read(resource="messages")`. Messages include `source_message_id` and `images` with
zero-based indices. Fetch the pixels using:

```json
{
  "name": "read",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "resource": "image",
    "arguments": {"source_message_id": "PLATFORM_MESSAGE_ID", "image_index": 0}
  }
}
```

The result is native MCP image content. Reads require the current conversation
read grant; read access never adds activation sources or reply authority. A
specific route token may omit `route_id`; a Host key or account-history handle
requires it. History never exposes signed image URLs.

Up to four images per message are projected. MCP downloads accept PNG, JPEG,
GIF, and WebP, at most 4 MiB each, from public HTTPS addresses without a domain
allowlist. Received HTTP URLs are upgraded to HTTPS. Embedded credentials,
fragments, nonstandard ports, local hostnames, non-global IP literals, local file
paths, redirects, unsupported formats, and expired links are refused. These
attachment sources remain in the private raw journal. a13n applies its own
attachment limits.

QQ sticker images arrive through these same attachment parts, even when the text
also contains a native emoji marker. CG renders that marker as `[QQ sticker]` or
a readable emoji label. Check the received image count and inspect the attached
image before describing a sticker; a text marker alone does not prove there is
no image.

To pass the original image directly to a media service, add
`"representation": "url"` to the image read arguments above. QQ attachments
support the same option with `resource: "attachment"` and `attachment_index`.
This authorized lookup returns a temporary source URL without downloading it.
Treat signed URLs as private: use them as tool inputs, not in channel text or
public logs. Expiry and third-party fetchability are controlled by the source;
CG cannot refresh an expired QQ URL. Default `content` reads still return bytes.

## Send images

Enable CG's `send_message` tool through the shared Host MCP connection.
Every route supports passive tool replies; QQ proactive images additionally
require `message.proactive`. A dedicated connection is optional for one concrete
source. Agent final output is never sent.
Check `discover(resource="conversations")` capability fields for the channel's supported formats.

```json
{
  "name": "send_message",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "content": [
      {"type": "text", "text": "Optional caption"},
      {"type": "image", "filename": "picture.png", "data_base64": "BASE64_IMAGE_BYTES", "media_type": "image/png"}
    ]
  }
}
```

Supply strict base64 without a `data:` prefix, at most 4 MiB decoded for OneBot and 16 MiB for QQ. QQ official
supports PNG/JPEG; OneBot also supports GIF/WebP. CG accepts no server-local path
as a file source. QQ also accepts a public HTTPS media URL; OneBot requires inline base64. A caption may contain up to 16000 characters.

The tool returns a queued `operation_id`; query it with `get_operation`. Replies are sent automatically and appear in
the existing conversation history with their delivery state. QQ uploads first,
then sends the image. With `message.proactive`, QQ tools default to proactive
delivery; `delivery: "passive"` explicitly uses incoming reply context and its
reply window. Proactive sends obey the [QQ permission and queue limits](qq-platform.md#message-creation).
`sent` means the platform confirmed
acceptance. `unknown` means a send might have taken effect: inspect the channel
before invoking the tool again. CG does not automatically resend uncertain actions.

## React to messages

OneBot message reactions require a provider implementing the optional
NapCat-compatible `set_msg_emoji_like` action. Standard OneBot v11 does not
guarantee this extension. CG reports it as optional in conversation capabilities.
QQ official C2C/group reactions and Weixin reactions are unavailable; QQ's official
reaction API supports guild channels, implemented by CG with the explicit
`message.react` route grant. Reactions return an action ID and are separate from
ordinary message replies; see [QQ platform tools](qq-platform.md).

```json
{
  "name": "execute_action",
  "arguments": {
    "route_id": "route_EXAMPLE",
    "action": "reaction",
    "arguments": {"source_message_id": "PLATFORM_MESSAGE_ID", "emoji_id": "76", "add": true}
  }
}
```

Use a decimal platform emoji ID, not a Unicode emoji. Set `add: false` to remove
the bot's reaction. The target must be a locally received message in the source
conversation, with its sender currently allowed by the route. An account-wide
read grant does not authorize reactions to other conversations or disallowed
senders. Revoking source authority blocks queued reactions. Unsupported actions
receive no fallback emoji message and are not automatically retried.

Ordinary incoming OneBot `face` segments appear as `[face:ID]` in message text;
these are distinct from reactions attached to another message.
