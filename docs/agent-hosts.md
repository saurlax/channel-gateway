# Connect an Agent Host

In **Agent hosts**, choose **Add agent host**, select a Host type, and name the
client or runtime. Channel accounts remain in **Channels**. Each Host receives
its own MCP key; upstream a13n API credentials are separate.

## a13n API + MCP

Enter the a13n API base URL, such as `https://a13n.example/api/v1`, and a workspace
API key allowed to run the selected Agent and read its execution. The workspace
is determined by that upstream key. On a route, select an Agent from the Host list. The key needs workspace read
permission to list published Agents and existing threads.

CG submits inputs, uses the route's thread delivery policy, and tracks its runs.
The default creates a fresh thread per input with up to 30 previous channel
messages and reuses the same conversation's environments; see [thread delivery](onebots-a13n.md#thread-delivery).
Bounded-history delivery queues each conversation's inputs. With continuous
threads, **Messages during execution** defaults to joining the current run (`steer`);
choose queue mode to wait for its completion. See the
[busy-input setting](onebots-a13n.md#messages-during-execution) for scope, retries
and shared-run reply behavior.
All channel messages use tools. Configure one shared MCP
connection with the Host key and select destinations with `route_id`, following
the [a13n MCP setup](onebots-a13n.md#mcp-tools-in-a13n). Adding conversations
does not add connections or tools. This key grants access to every enabled route
assigned to the Host; use separate Hosts or deliberately narrower route credentials
when clients must not share that access. Separate threads alone do not narrow
the Agent's configured MCP authority.

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

Under **Routes & access**, **Add a route** opens a dedicated configuration page.
Set its name and enabled state in **Basic information**. In **Routing**, configure
the channel and user/group rows in **Sources** and select this Host in **Destination**.
Choose readable accounts in **Connections**. a13n-specific fields are omitted and replies
use MCP. The client calls `discover(resource="conversations")`, then supplies an authorized
conversation’s `id` as `route_id`:

```json
{
  "name": "read",
  "arguments": { "resource": "messages", "route_id": "route_EXAMPLE", "arguments": {"limit": 20} }
}
```

```json
{
  "name": "send_message",
  "arguments": { "route_id": "route_EXAMPLE", "content": [{"type": "text", "text": "Hello"}] }
}
```

Messages become **Available to client**. The client actively reads them; MCP does
not start an idle Codex session, and a read does not prove execution completion.
Reading is repeatable, not a consuming inbox. Use source message IDs to avoid
processing the same input twice. The tool reads CG's incoming and confirmed sent message history,
including messages observed before route creation or while delivery was disabled,
under its current readable-channel grant. Reading does not replay those messages
as Agent work. Records distinguish `incoming` and `outgoing`; queued, failed,
blocked, and uncertain sends are not presented as sent chat content. Outgoing
media descriptions contain no private upload data or received-media read handles.
It excludes raw event packets and private context tokens. Legacy
API routes without explicit readable channels retain source/sender scope and
exclude messages bound to deleted routes. All-conversation scopes discover previously observed authorized
conversations when listed. This is not native platform history. Reply requests
are queued and sent automatically after current route authorization
checks, for private and group conversations. In **Messages → View conversation**, received messages and outgoing replies
appear together in chronological order.
Use `read(resource="image")` for received pixels, `send_message` with an image
content part for image replies, and `execute_action(action="reaction")` for reactions.
The [tool guide](agent-tools.md) documents the fixed inventory and migration. See
[images and reactions](images-and-reactions.md) for arguments and platform limits.

## Key scope and rotation

The Host key authorizes the readable channels and source reply destinations of
its currently enabled assigned routes. Different clients needing separate access must have separate Hosts and
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

### Readable channels

In a route's **Connections** tab, select the bot accounts whose received messages
the Agent can read. Access covers every observed private/group conversation in
those channels, including retained history. **All channels** includes accounts
added later; selecting none disables message reads. Source rows independently
determine which new messages trigger the Agent and where it can reply.

MCP `discover(resource="conversations")` marks readable/replyable entries with `can_read` and
`can_reply`. Read-only handles require an explicit `route_id` and cannot be used
with `send_message`. Disabling/deleting a route or removing its read grant revokes
access on subsequent calls. Existing routes keep their previous API read scope
until saved with an explicit readable-channel selection.
