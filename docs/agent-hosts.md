# Connect an Agent Host

In **Agent hosts**, choose **Add agent host**, select a Host type, and name the
client or runtime. Channel accounts remain in **Channels**. Each Host receives
its own MCP key; upstream a13n API credentials are separate.

## a13n API + MCP

Enter the a13n API base URL, such as `https://a13n.example/api/v1`, and a workspace
API key allowed to run the selected Agent and read its execution. The workspace
is determined by that upstream key. On a route, enter the a13n Agent ID.

CG submits inputs, creates one thread per conversation, and tracks its runs.
MCP is optional for final-output replies. To give a thread access to only its
conversation, follow the [dedicated route MCP setup](onebots-a13n.md#optional-mcp-tools-in-a13n).
The Host key can instead grant a client access to every enabled route assigned
to the Host; do not substitute it where a thread must remain conversation-scoped.

## Generic MCP

Choose **Generic MCP** for clients such as Codex or any client supporting MCP
Streamable HTTP. No upstream API endpoint, API key, or Agent ID is needed.
Copy the one-time MCP key after saving. Configure the client with:

```text
Transport: Streamable HTTP
URL: https://GATEWAY/mcp/
Authorization: Bearer HOST_MCP_KEY
```

Use the client’s secret/environment configuration for the key. The URL requires
the trailing slash. Local clients can use `http://127.0.0.1:8310/mcp/`; remote
clients need a reachable HTTPS gateway. The console displays the URL using its
current origin, so open it through the public domain when copying a remote URL.

Create a route and select this Host. a13n-specific fields are omitted and replies
use MCP. The client calls `list_conversations`, then supplies an authorized
conversation’s `id` as `route_id`:

```json
{
  "name": "read_conversation",
  "arguments": { "route_id": "route_EXAMPLE", "limit": 20 }
}
```

```json
{
  "name": "send_message",
  "arguments": { "route_id": "route_EXAMPLE", "text": "Hello" }
}
```

Messages become **Available to client**. The client actively reads them; MCP does
not start an idle Codex session, and a read does not prove execution completion.
Reading is repeatable, not a consuming inbox. Use source message IDs to avoid
processing the same input twice. The tool reads CG's received text history,
including messages observed before route creation or while delivery was disabled,
under the current sender policy. Reading does not replay those messages as Agent
work. It excludes raw event packets, private context tokens, and messages bound
to deleted routes. All-conversation scopes discover previously observed authorized
conversations when listed. This is not native platform history. Reply requests
are queued and sent automatically after current route authorization
checks, for private and group conversations. In **Messages → View conversation**, received messages and outgoing replies
appear together in chronological order.

## Key scope and rotation

The Host key authorizes only its currently enabled routes and their allowed
senders. Different clients needing separate access must have separate Hosts and
keys. The key cannot create routes or choose arbitrary destinations. Route tokens
remain available when a narrower single-conversation capability is useful.

Keys are shown once and stored only as hashes. **Manage → Rotate MCP key** reveals
a replacement and immediately invalidates the previous key; update the client’s
secret configuration. Existing Hosts created before Host keys were supported
can obtain a key using this action. Upstream API credentials are unaffected.

See the [access contract](../spec/02-identity-and-access.md#host-capabilities),
[tool contract](../spec/04-agent-tools.md), and
[delivery contract](../spec/03-routing-and-delivery.md#generic-mcp-delivery).

## Remove a Host

Open **Agent hosts → Manage → Delete agent host** and confirm its name. CG removes
its upstream credentials and MCP key, disables assigned routes, and blocks pending
work. Channel accounts and administrator message/reply history remain. Existing
upstream runs or external requests already in flight cannot be cancelled by this
operation. Delete the disabled routes separately if their source conversations
should be bound to another Host.
