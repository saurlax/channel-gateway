# Images and message reactions

## Scope and compatibility

QQ official C2C/group/guild/DM and OneBot v11 messages support text and up to four image
attachments, including image-only input. Existing text envelopes, route policies,
deduplication, and final-text output remain compatible. Weixin remains text-only.
Images do not add trigger sources, read grants, or reply destinations.

Normalized images retain a private download URL. Management and MCP history expose
only a zero-based image index, never that URL. Original packets remain in the
private event journal. Only image attachments explicitly received from an
authenticated channel are readable; tools accept no arbitrary download URL.
PNG, JPEG, GIF, and WebP are readable through MCP, bounded to 4 MiB per image. Attachments are
not eagerly downloaded during ingestion. Expired, oversized, unsupported, or
unavailable images produce a tool error without losing the original message.

## Projection and Host delivery

QQ projects image `attachments`; OneBot projects `image` segments from arrays or
CQ strings. QQ CDN HTTP URLs are upgraded to HTTPS. Only HTTPS URLs on `qpic.cn`
(including subdomains), `multimedia.nt.qq.com`, `multimedia.nt.qq.com.cn`, or regional
`*.cos.*.myqcloud.com` hosts, with no embedded credentials,
fragment, or nonstandard port, become images. Local paths, base64 input segments,
and other hosts are not downloadable attachments. Unsupported attachments remain
in the raw journal. More than four supported images invalidates projection rather
than silently dropping content. Ordinary OneBot `face` segments project as
`[face:ID]` text; sending faces and reacting to an existing message are distinct.

An a13n submission includes the verified channel context, source message ID,
image count, and text, followed by native `type: url` content parts. The authorized
Host receives the image URLs for its own bounded attachment ingestion, under its
endpoint and model policies. URLs may expire before execution; frozen submissions
are never rewritten on retry. Generic MCP Hosts use `read_image`. Image-only
messages use a compact `[Image]` activity summary and enter the same route workflow
as text. Image bytes are not fetched or stored inside an ingestion transaction.

## Agent tools and authority

`list_conversations` includes channel capability information, distinguishing
supported image formats and optional protocol extensions.

- `read_image(source_message_id, image_index=0, route_id=None)` returns native MCP
  image content. It applies exactly the current `read_conversation` policy,
  including explicit account grants and legacy sender/archived-route exclusions.
  Lookup is scoped to that conversation and is not limited to the latest 100
  messages. Access is checked again after the download before returning pixels.
  Downloads have a 15-second total deadline, streaming byte bound, and no redirects,
  channel credentials, cookies, or environment proxies. The response's image
  signature and explicit image media type must agree. Missing or generic binary
  Content-Type is inferred from the supported image signature.
- `send_image(image_base64, media_type, caption='', route_id=None)` queues one
  image with an optional caption of at most 16000 characters. Input is strict
  base64, at most 4 MiB decoded, with a matching image signature. QQ accepts PNG
  and JPEG; OneBot also accepts GIF and WebP. It requires the same current
  destination authority as `send_message`: MCP replies, or an explicitly granted
  QQ proactive tool send alongside final-output replies. Optional `delivery`
  follows the [QQ delivery contract](06-qq-platform.md#passive-replies-and-proactive-tools).
- `set_message_reaction(source_message_id, emoji_id, add=True, route_id=None)`
  queues a reaction to a locally received, currently source-authorized message
  in that exact conversation. Read-only account grants cannot authorize reactions.
  Emoji IDs are decimal strings of at most 20 digits. OneBot uses the optional
  NapCat-compatible `set_msg_emoji_like` extension with `set: add`. This is not
  part of standard OneBot v11: unsupported providers return a failed/unconfirmed
  action, without fallback to sending an emoji message. QQ official reactions
  are unsupported for C2C/group conversations; guild-channel reactions require
  the explicit `message.react` resource grant and support `emoji_type` 1 or 2.
  See [QQ platform operations](06-qq-platform.md). Weixin reactions are unsupported.

Tools cannot select an arbitrary account, native message in another conversation,
local file, or remote URL. Reaction target authorization is checked both at queue
time and immediately before sending; sender revocation blocks queued actions.
Image reads require read access; image sends require reply authority. Independent
reactions use `message.react`; legacy MCP OneBot replies retain their existing reaction scope.
All actions recheck connection presence and route authority before execution.

## Sending and persistence

Image replies reuse durable reply states and automatic execution. New reaction
requests use the independent [action ledger](06-qq-platform.md#execution-and-receipts),
with an action ID rather than a reply ID. Existing historical reaction replies
are preserved.
An additive private `reply_payloads` table stores immutable action data separately
from public reply records; text replies and existing databases need no rewrite.
History exposes a caption/compact action summary, never base64 or platform upload
tokens. The same queued/sending/sent/blocked/failed/unknown semantics apply, including
interrupted-send recovery and no automatic resend after a possible external effect.
Repeating an MCP call may create another action.

QQ uploads image base64 with `file_type: 1`, `file_name`, and
`srv_send_msg: false` to the destination-specific `/v2/users/{openid}/files` or
`/v2/groups/{openid}/files`, then sends `msg_type: 7` with `media.file_info`,
the authorized passive `msg_id`, and a durable `msg_seq`. It never uses upload's
direct-send mode. The context and route authority are rechecked between upload
and the message request. Upload tokens remain private. Passive reply windows and
quotas still apply. Final sends require a message-ID receipt.

OneBot sends text/image segment arrays with `base64://` image content through
`send_msg`, identically over HTTP or the universal WebSocket. Reactions use the
same request/echo correlation but require only `status: ok` and `retcode: 0`;
their stored receipt correlation is the target message ID, not a new message ID.

## Protocol references

- [QQ rich media](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/rich-media.html)
- [QQ group uploads](https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/v2_groups_group_openid_files.post.html)
- [QQ reactions (guild only)](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/emoji.html)
- [OneBot v11 segments](https://github.com/botuniverse/onebot-11/blob/master/message/segment.md)
- [NapCat reaction extension](https://github.com/NapNeko/NapCatQQ/blob/main/packages/napcat-onebot/action/msg/SetMsgEmojiLike.ts)
