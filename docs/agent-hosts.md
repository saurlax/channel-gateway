# Connect an Agent Host

In **Agent hosts**, choose **Add agent host**, select a Host type, and name the
client or runtime. Channel accounts remain in **Channels**. Hosts do not issue MCP
credentials or configure a CGW MCP URL. Create a route, then copy its generated
route MCP token and endpoint from the one-time dialog. Rotation is available in
**Routes → Edit → Permissions and connections → Route token**.

In Host management, **Replacement a13n API key (optional)** accepts a key issued
by a13n; leaving it empty keeps the saved upstream credential. This is separate
from a route's CGW-issued MCP token, which cannot be user-defined.

## a13n API + MCP

Enter the a13n API base URL, such as `https://a13n.example/api/v1`, and a workspace
API key allowed to run the selected Agent and read its execution. The workspace
is determined by that upstream key. On a route, select an Agent from the Host list. The key needs workspace read
permission to list published Agents and existing threads.

CG submits inputs, uses the route's thread delivery policy, and tracks its runs.
New routes retain each chat's Session for 60 idle minutes and reuse its environment
across Session changes. Conversation scope, idle timeout and environment scope
(global, conversation or Session) are independent; see
[thread delivery](onebots-a13n.md#thread-delivery). New non-fixed routes use scoped
writable memory; optional global Spaces are mounted read-only, replacing default
Space selection. Standalone defaults must be read-only; the root and pinned named
child revisions are inspected before managed inputs are frozen. The upstream API key
needs Agent revision reads, Memory Space read/create authority
and public Thread, descendant, inbox and environment reads. A Host lacking those
APIs fails explicitly rather than silently using a different policy.
**Messages during execution** defaults to joining the current run (`steer`);
choose queue mode to wait for completion. See the
[busy-input setting](onebots-a13n.md#messages-during-execution).
All channel messages use tools. Configure an MCP connection in a13n using the
route's generated token and the endpoint shown with it. One multi-source or
wildcard route covers its authorized conversations through that connection.
Separate route configurations require their own credentials/connections. Sharing
an Agent or Host grants no implicit access to another route. See the
[a13n MCP setup](onebots-a13n.md#mcp-tools-in-a13n).

## Generic MCP

Choose **Generic MCP** for clients such as Codex or any client supporting MCP
Streamable HTTP. No upstream API endpoint, API key, or Agent ID is needed.
Create a route assigned to this Host, then copy its one-time route token.
Configure the client with:

```text
Transport: Streamable HTTP
URL: https://GATEWAY/mcp/
Authorization: Bearer ROUTE_MCP_TOKEN
```

Use the client’s secret/environment configuration for the key. The URL requires
the trailing slash. Local clients can use `http://127.0.0.1:8310/mcp/`; remote
clients need a reachable HTTPS gateway. The console displays the URL using its
current origin, so open it through the public domain when copying a remote URL.

Under **Routes & access**, **Add a route** opens a dedicated configuration page.
Set its name and enabled state in **Basic**. In **Routing**, configure
the channel and user/group rows in **Sources** and select this Host in **Destination**.
Choose readable accounts in **Permissions and connections**. a13n-specific fields are omitted and replies
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

Only route tokens authenticate CGW MCP. A token grants that route's configured
readable channels, source destinations and platform actions, including its enabled
concrete children. It never aggregates other routes on the same Host. Separate
clients can use separate route credentials on one Host. Read-only handles grant
no sending authority.

Tokens are shown once and stored only as hashes. **Routes → Edit → Permissions
and connections → Route token → Rotate token** generates a replacement and
immediately invalidates the previous token. Update the corresponding client's
bearer credential. Upstream a13n API credentials are unaffected.

Host-wide MCP keys and their rotation endpoint are retired. Existing Host keys
stop authenticating immediately after upgrade and their stored hashes are removed
at startup. Replace them with route-issued tokens; no broad credential is silently
converted into access to one or more routes.

See the [access contract](../spec/02-identity-and-access.md#route-only-mcp-credentials),
[tool contract](../spec/04-agent-tools.md), and
[delivery contract](../spec/03-routing-and-delivery.md#generic-mcp-delivery).

## Remove a Host

Open **Agent hosts → Manage → Delete agent host** and confirm its name. CG removes
its upstream credentials, disables assigned routes, and blocks pending
work. Channel accounts and administrator message/reply history remain. Existing
upstream runs or external requests already in flight cannot be cancelled by this
operation. Delete the disabled routes separately if their source conversations
should be bound to another Host.

### Readable channels

In a route's **Permissions and connections** tab, select the bot accounts whose received messages
the Agent can read. Access covers every observed private/group conversation in
those channels, including retained history. **All channels** includes accounts
added later; selecting none disables message reads. Source rows independently
determine which new messages trigger the Agent and where it can reply.

MCP `discover(resource="conversations")` marks readable/replyable entries with `can_read` and
`can_reply`. Read-only handles require an explicit `route_id` and cannot be used
with `send_message`. Disabling/deleting a route or removing its read grant revokes
access on subsequent calls. Existing routes keep their previous API read scope
until saved with an explicit readable-channel selection.
