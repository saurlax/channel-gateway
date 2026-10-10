# Channel adapters

## Account binding

A channel connection represents exactly one configured bot account. Creation
accepts an account's complete OneBot v11 HTTP API base, API token, expected
event `self_id`, and callback signing secret. The URL and account identity are
immutable through the management API; credentials can be replaced.

Several connections may point to the same OneBots instance. Each connection has
an independent CG identity and account binding. One addition creates one source;
there is no instance discovery, bot multi-selection, or implicit source group.

An CG connection ID scopes channel addresses and deduplication. Platform
`self_id` alone is not a globally unique CG identity. An event is interpreted
under the connection selected by its callback path, authenticated with that
connection's signing secret, and checked against its configured `self_id`.
Display names and unverified headers cannot select an authorized source.

## Transport compatibility

The baseline supports OneBot v11 HTTP API calls plus reverse HTTP callbacks.

| Transport         | Connection direction   | Role                     | Baseline        |
| ----------------- | ---------------------- | ------------------------ | --------------- |
| HTTP API          | CG requests OneBots    | Channel operations       | Implemented     |
| Reverse HTTP      | OneBots posts to CG    | Incoming events          | Implemented     |
| Forward WebSocket | CG connects to OneBots | Events and/or operations | Not implemented |
| Reverse WebSocket | OneBots connects to CG | Events and/or operations | Not implemented |

HTTP API by itself is not an event subscription. Current callbacks use
`POST /api/v1/hooks/onebots/{connection_id}`. The signature is
`X-Signature: sha1=<HMAC-SHA1(signing_secret, raw_request_body)>`. CG checks the
raw bytes before parsing. An invalid signature is rejected; the configured
account identity must also match before a message can enter routing.

API calls use bearer authentication and the configured account base. Text
sending calls `/send_msg` with explicit `message_type`, `user_id` or `group_id`,
and a text segment array. A successful send requires HTTP success, OneBot
`status: ok`, `retcode: 0`, and a message ID receipt. Missing confirmation has
the [unknown-outcome semantics](03-routing-and-delivery.md#reply-lifecycle).

These are OneBot protocol semantics. Native QQ Gateway, QQ callbacks, and Weixin
iLink long polling have separate authentication and event formats; an HTTP or WS
URL does not make them OneBot-compatible. The baseline connects those platforms
only when OneBots provides the configured OneBot account endpoint.

## Text event projection

Supported events have `post_type: message` and `message_type: private` or `group`.
The adapter extracts text segments from array messages. For string messages it
removes CQ segments and decodes supported CQ text escapes. It does not convert
media or unsupported segments into fabricated text.

The normalized text message carries the CG account address, private/group
conversation address, source message ID, sender ID, text, and source timestamp.
Private conversation identity uses the sender; group conversation identity uses
the group ID. Source identities are preserved independently of display names.

| Input condition                                                                                                  | Result                             |
| ---------------------------------------------------------------------------------------------------------------- | ---------------------------------- |
| Invalid signature                                                                                                | HTTP 401, no ingestion             |
| Invalid supported message shape                                                                                  | HTTP 422, no ingestion             |
| Callback larger than 1 MiB                                                                                       | HTTP 413, no ingestion             |
| Text longer than 60000 characters                                                                                | HTTP 422, no ingestion             |
| Other event type, mismatched `self_id`, unsupported conversation type, bot self-message, or empty extracted text | `ignored`                          |
| Missing/disabled route or unauthorized sender                                                                    | `blocked`                          |
| Authorized new source identity persisted                                                                         | `accepted` with event ID           |
| Previously persisted source identity                                                                             | `duplicate` with the same event ID |

Deduplication uses the CG connection, conversation kind/ID, and source message ID.
The source timestamp is message data, not a permission grant. Accepted input
enters the [delivery workflow](03-routing-and-delivery.md); an ingress response
does not confirm Agent completion or a channel reply.
