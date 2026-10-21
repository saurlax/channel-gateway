# Development Standards

## Boundaries

[spec/00-overview.md](spec/00-overview.md) owns the accepted architecture and
runtime scope. These standards govern how components are implemented.

- **Core** owns reusable communication models. It imports neither the web
  framework nor individual channel or Agent Host SDKs.
- **Channel adapters** own platform connections and translate platform events into
  communication models. Each adapter declares its actual capabilities.
- **Agent Host adapters** own Thread binding and input delivery for a particular
  Host. a13n integration uses its public APIs, not internal Service modules.
- **MCP tools** expose authorized conversation reads and channel actions. Tool
  transport does not itself own the lifecycle that wakes an idle Agent Host.
- **Server** owns HTTP routes, configuration, storage, listeners, startup, and
  shutdown. Importing a package has no process side effects.
- **Console** calls the management API and contains no channel credentials or
  independent routing rules.

The bridge implements OneBot v11 HTTP/WebSocket and native QQ official media/guild
adapters, a Weixin iLink text adapter, Slack/Lark signed callbacks, a Discord Gateway adapter, an a13n HTTP adapter, SQLite persistence, and generic MCP Hosts with official
MCP Streamable HTTP tools. `channels/` owns protocol parsing, connection tasks,
pairing, capabilities, and native sending.
`models.py` owns validated management inputs; `storage.py` owns short storage
operations; `bridge.py` owns durable workflow; `app.py` owns authentication and
process lifecycle. Keep new adapters outside HTTP route handlers.
The [QQ platform contract](spec/06-qq-platform.md) owns guild/DM routing, resource grants,
independent action execution, and the fixed resource operation catalog.
`qq_operations.py` defines permitted native paths and schemas; no MCP tool accepts
a raw method/path. The [image and reaction contract](spec/05-images-and-reactions.md) owns image
projection, credential-free CDN reads, and optional OneBot reaction actions.

## Implementation

Use clear domain names and direct control flow. Keep transport parsing separate
from application behavior so that tools, CLI commands, and embedded Hosts can
reuse the same operations. Library configuration is explicit; executable entry
points own logging and process startup.

Service I/O is async. External operations have explicit deadlines. When storage
is added, transactions remain short and never span network calls, streams,
listener waits, or Agent execution. Delivery state must distinguish acceptance by
a Host from completion of an Agent run. Preserve source identities for deduplication;
do not claim exactly-once external effects merely because input is idempotent.

A message's channel identity is data, not authorization. Define credential and
conversation access boundaries before exposing adapters. Follow the owning
[identity and access](spec/02-identity-and-access.md),
[delivery](spec/03-routing-and-delivery.md), and
[Agent tool](spec/04-agent-tools.md) contracts rather than deriving permission
rules from UI state or upstream display names.

## Extensibility

Public adapter and event contracts must be independent of a13n-specific object
identities. Keep platform capability differences visible: text messages, stored
audio, and live calls have different lifecycles. A text envelope is not a complete
telephone or radio protocol.

The core can be embedded as a Python library. A standalone deployment owns its
listeners; an embedded deployment's Host owns them. Keep the process entry point
and frontend optional so future Hosts can reuse the same application behavior.

## Console management patterns

Host creation selects a13n API + MCP or generic MCP. Generic Hosts need no
upstream endpoint/key; their independent MCP key is shown once and can be rotated
from Manage. Route forms show a13n agent/binding fields only for API Hosts.

Channel accounts and Agent Hosts have separate sidebar entries and management
pages. Channel tables omit connection endpoints. Official QQ and Weixin API
addresses are configured internally and are not displayed as form fields. Keep
required upstream OneBot addresses in its configuration form, and generated
inbound URLs in connection details for copying. The shared connection API is an implementation detail; each page filters
its resource type and creates only that type. Sidebar navigation uses static
Gateway and Activity group labels with direct links below them; do not add
collapsible group buttons or an additional Platform heading.

The default Overview page summarizes configuration and the latest activity window.
Keep the animated gateway connection line on Overview only, not on resource or
activity management pages. Label bounded message/reply counts as recent records;
count configured routes separately from automatically created child routes.

Interface copy is concise, formal, and precise. Use short labels for resources,
states, actions, and data scope. Omit introductory paragraphs, demo or presentation
copy, animation explanations, and implementation commentary from product pages.
Keep instructions that users need to enter credentials, choose permissions, or
confirm destructive actions and destination authorization.

Use a search toolbar followed by a table for channels, hosts, routes, and message
conversations. Incoming messages and Agent replies share the same chronological
conversation history, opened only from the Messages panel. Route rows have one
Edit action opening `/routes/{id}/edit`; token rotation, enable/disable settings,
and deletion are on that page. Channel and Agent Host management dialogs and route
edit pages expose deletion using the shared red outline button and official Alert
Dialog confirmation. Do not add a
separate reply activity page. Search the relevant displayed fields, preserve table headers
for empty lists, and distinguish no records from no matches. Keep table overflow
inside the table frame, including on mobile.

Creation starts from an Add button in the toolbar. Routes open `/routes/new`;
channel and Agent Host creation open a shadcn Dialog. Creating a route from Messages
opens the same page with the source conversation prefilled. Route pages support
direct visits and reloads, show missing routes without redirecting, and return to
the list after saving or deleting. Token rotation keeps the edit page open and
shows the new token once in its own dialog. Use a single-column form directly on
the page without a Card wrapper, and provide a Back to routes link and Cancel action.
Route configuration uses Basic information, Routing, and Connections tabs. Basic
information contains name and enabled state. Routing shows Sources and Destination
together; Connections contains reply source, the supported a13n MCP connection
binding, and readable channels. Sources use repeatable rows with two input columns
(channel, user/group) and a trailing delete button,
including at narrow widths. A compact plus button adds a row. Channel choices
include All channels, which includes future channels; target choices distinguish
All conversations, All private chats, and All groups and support observed identities
or explicit platform IDs. All conversations includes every supported conversation
type; existing private-only sources keep their scope until explicitly edited.
Sources are the complete console trigger condition; do not add a separate sender
permission control. Destination selects the Host and Agent from its published,
unarchived Agent list, then chooses thread delivery: per conversation (default),
per sender within a conversation, one fixed thread, or a fresh thread per message.
Present Conversation continuity choices in use-case order: ongoing private/group
chats, personal assistance within groups, independent messages, then shared
context across sources. Each choice has a concise scope/use description in the
menu and below the selected value; connect the latter with aria-describedby.
The Messages during execution selector applies to a13n Hosts, defaults to joining
the current run at a safe boundary, and offers FIFO queueing until completion.
It is independent of conversation continuity and hidden for generic MCP clients.
Fixed delivery offers existing unarchived Host threads and create-on-first-message.
Use searchable shadcn comboboxes with paginated resource loading, loading/error
states and retry. Host changes clear the selected Agent and fixed thread.
Connections configures reply source and the supported a13n MCP connection binding,
and selects readable
channel accounts using checkboxes. Agent tool permissions are configured in the
Agent Host interface; CG never overrides its connection-tool list. Enabled state
uses a Switch. Read grants cover every received
conversation on a selected account, with no per-user visibility controls. They
do not add activation sources or authorize replies. All channels includes future
accounts, and selecting no channels denies reads.
Tab switching preserves unsaved values, and validation selects the relevant tab.
Editing credentials, viewing details, and viewing reply details start from table
row actions and open dialogs. Modal fields always use one column at every width.
Do not place permanent creation or per-record edit forms beside or below the list. Activity records are created by
channel ingestion or agent execution and have no manual Add action.

Before changing console UI, consult the [official shadcn Blocks](https://ui.shadcn.com/blocks)
and adapt the closest existing block's layout and interaction design. Retain its
standard spacing, typography, component variants, and action placement. When no
block covers the interaction, follow the [official component examples](https://ui.shadcn.com/docs/components):
form actions use Dialog footers and destructive confirmation uses Alert Dialog.
Keep neutral surfaces and limit custom styling to necessary product and
responsive adjustments; do not invent a custom design when an official pattern
fits.

The Signal theme uses jade semantic tokens for primary actions, focus, and active
navigation on neutral surfaces. Keep destructive feedback distinct from the brand
color. Message feedback is brief and driven by changes observed during refresh;
initial history and unchanged polls must not animate. Host-side motion requires
observed `submitted` or `completed` input, never generic MCP `available` state.
Honor `prefers-reduced-motion` with static feedback.

Interface copy must not use middle-dot separators; use clear labels,
or separate lines. Channel types use plain QQ and localized Weixin names, or
OneBot followed by its transport, without parentheses. Dialogs need an accessible title and description, keyboard dismissal,
focus restoration, inline errors, and a scrollable form on small screens. Preserve
entered values on failure; close and clear form credentials after a successful
save or cancellation. Replies are read-only delivery records. Authorized private and group replies
are sent automatically; do not add per-reply administrator review or decisions. Token displays remain in memory and appear only once in their own dialog.

## Console localization

Keep interface copy in `frontend/apps/console/src/i18n/messages.ts` and use
`useLocale()` in rendered components. Locale selection follows the ordered
`navigator.languages` list and updates on `languagechange`; unsupported language
preferences fall back to English. Set the document language and format timestamps
with the selected locale. Translate known server diagnostics only at display time;
preserve unknown diagnostics, conversation content, user-defined names, and IDs.

## Packaging

Python packages use uv workspace sources and Hatchling, with `0.0.0` source
versions. The React/TypeScript/Vite console is private build input. Its static
output is bundled into the server package when built, and is not committed.
Installed Python packages do not require Node.js. A headless package can be built
without console assets; `make build` includes the console.

CI publishes the checked Linux AMD64 container to GHCR on `main` pushes and
manual runs on `main`, with `dev`, `main`, `latest`, and full commit SHA tags. Python
package releases are not automated. Versioning and supported adapter contracts
will be established before publishing Python packages. Deployment and image
rollback instructions live in [docs/deployment.md](docs/deployment.md).
