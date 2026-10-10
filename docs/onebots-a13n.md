# Set up the OneBots and a13n text bridge

This guide connects one bot account to an a13n Agent using HTTP API calls and
signed HTTP callbacks. Add further accounts separately; WebSocket and direct
QQ/Weixin connections are not currently available. See the
[channel contract](../spec/01-channel-adapters.md) for protocol and message limits.

## Connect a channel and host

1. Set `CHANNEL_GATEWAY_ADMIN_TOKEN` to a random token of at least 32 characters.
   Set `CHANNEL_GATEWAY_DATA_DIR` to a private persistent directory and start
   `make serve`. Open the console and sign in with the administrator token.
2. In **Channels**, select **Add channel** and add a **OneBots** account. The API
   base is the complete OneBot v11 account endpoint, for example
   `https://onebots.example/qq/ACCOUNT/onebot/v11`. Enter the account's
   `access_token`, its event `self_id`, and a signing secret of at least 16
   characters. Credentials are write-only through management views.
3. Select **Manage** on the channel row and copy its reverse HTTP webhook URL.
   Configure that account's OneBot v11 protocol with `use_http: true`, the matching `access_token`,
   `secret`, and the webhook URL in `http_reverse`. OneBots signs the raw JSON
   with `X-Signature: sha1=<HMAC-SHA1>`. Notice and heartbeat events are ignored.
   See [OneBots](https://github.com/lc-cn/onebots).
4. In **Agent hosts**, select **Add agent host** and add an **a13n** host using
   its API base, e.g. `https://a13n.example/api/v1`, and a workspace API key
   permitted to read runs
   and run the selected agent. No interactive login session is used.
5. In **Routes & access**, select **Add a route** and choose both connections,
   a private user ID or group ID, allowed platform sender IDs, and the a13n
   agent ID. A private route
   authorizes exactly that user. A group requires an explicit sender allowlist.
   Start with **Final run output** and no MCP connection ID.
6. Send a text message as an authorized user. **Messages** shows acceptance,
   host submission, the created thread, and any failure. **Reply approvals**
   shows the resulting text draft and its exact destination in a table. Select
   **Review** to open and edit it before selecting **Approve and send**.

The signing secret must match OneBots' configuration. Start with a private test
conversation and an Agent whose permissions are appropriate for its participants.
The [access contract](../spec/02-identity-and-access.md) explains the authorization
and privacy boundaries; the [route contract](../spec/03-routing-and-delivery.md#conversation-routes)
explains conversation and thread binding.

Select **Manage** on a channel or host row to replace API tokens and signing
secrets. URLs and destinations cannot be edited; create another connection or
route when a different target is needed. Select **Edit access** on a route row to
edit sender allowlists and MCP binding. Disable the route to stop further work;
this cannot retract a sent message or interrupt an external request already in
flight.

## Optional MCP tools in a13n

The bridge works without MCP. Its MCP Streamable HTTP endpoint is
`https://GATEWAY/mcp/` (including the trailing slash). It exposes:

- `read_conversation(limit=20)`: received text from the authorized conversation,
  restricted to currently authorized senders, up to 100 records.
- `send_message(text)`: create a reply for that same destination. It never
  accepts an arbitrary account, conversation, or operation.

Create a route first and copy its one-time conversation access token. In a13n,
create a **separate MCP connection for this route** with that endpoint and the
route token as its **bearer credential**. The equivalent a13n create body is:

```json
{
  "type": "mcp",
  "name": "Channel Gateway team chat",
  "config": { "url": "https://GATEWAY/mcp/" },
  "auth": "bearer",
  "credential": { "token": "ROUTE_TOKEN" }
}
```

Copy the resulting a13n connection ID into the route's **Access & MCP binding**.
Choose **MCP send_message tool** if the agent should explicitly request replies;
otherwise **Final run output** makes `read_conversation` available while the
bridge generates drafts from the completed run output. These modes avoid
sending both a tool reply and the final output for the same run.

Choose an Agent whose remaining permissions are appropriate for the conversation.
The bridge's connection-tool selection does not remove built-in toolsets, plugins,
skills, or access to other Host resources. See the
[Agent tool contract](../spec/04-agent-tools.md) for exact tool selection and scope.

Rotate a route token from its row action, copy the one-time value, and replace the
bearer credential in its a13n connection. Wait for active input to finish before
changing the MCP binding. Never put the token in `mcp_headers`; use the bearer
credential field shown above.

## Diagnose delivery

In **Messages**, inspect the message details, assigned thread/run, and diagnostic.
If a message is `submitted`, check the assigned run in a13n; a waiting run needs
to be resolved there. For a failed event, fix connection or Agent configuration
and select **Retry**. Keep replacement a13n keys within the same principal and
workspace. Retrying resumes the recorded command or polling operation.

In **Reply approvals**, open **Review**, verify the exact destination, edit the
draft if needed, then approve or reject it. A `sent` reply has a confirmed channel
receipt. If a reply is `unknown`, inspect the channel before deciding whether
another message is needed; CG does not automatically resend it. The
[delivery contract](../spec/03-routing-and-delivery.md) defines all states and
completion/retry boundaries.

## Deployment boundaries

Use **one process and one replica**, HTTPS for remote endpoints, and a private
persistent state directory (`0700`). Protect this volume: it contains credentials
and conversation history without encryption at rest. See
[runtime scope](../spec/00-overview.md#runtime-and-persistence) and
[credential handling](../spec/02-identity-and-access.md#administrator-and-credential-handling).

Consult the [specification index](../spec/README.md#compatibility-boundary) for
the supported baseline and excluded integrations before configuring a new source.

## UI provenance

The console uses official shadcn **sidebar-07** and **login-01** blocks with
application-specific labels and controls, official components, and the official
neutral theme. It introduces no separate visual theme. Sources:

- [sidebar-07 registry](https://ui.shadcn.com/r/styles/new-york-v4/sidebar-07.json)
- [login-01 registry](https://ui.shadcn.com/r/styles/new-york-v4/login-01.json)
- [neutral theme](https://ui.shadcn.com/r/colors/neutral.json)
