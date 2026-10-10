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

The initial bridge implements OneBot v11 text ingestion and sending, an a13n
HTTP adapter, SQLite persistence, and official MCP Streamable HTTP tools.
`models.py` owns validated management inputs; `storage.py` owns short storage
operations; `bridge.py` owns durable workflow; `app.py` owns authentication and
process lifecycle. Keep new adapters outside HTTP route handlers.

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

Channel accounts and Agent Hosts have separate sidebar entries and management
pages. The shared connection API is an implementation detail; each page filters
its resource type and creates only that type. Sidebar navigation uses static
Gateway and Activity group labels with direct links below them; do not add
collapsible group buttons or an additional Platform heading.

Use a search toolbar followed by a table for channels, hosts, routes, messages,
and reply approvals. Search the relevant displayed fields, preserve table headers
for empty lists, and distinguish no records from no matches. Keep table overflow
inside the table frame, including on mobile.

Creation starts from an Add button in the toolbar and opens a shadcn Dialog.
Editing credentials, route permissions, viewing details, and reviewing replies
start from table row actions and open dialogs. Do not place permanent creation or
per-record edit forms beside or below the list. Activity records are created by
channel ingestion or agent execution and have no manual Add action.

Use the official shadcn blocks, neutral theme, and components with minimal layout
adjustments. Dialogs need an accessible title and description, keyboard dismissal,
focus restoration, inline errors, and a scrollable form on small screens. Preserve
entered values on failure; close and clear form credentials after a successful
save or cancellation. Keep group destination review and approval inside the reply
dialog. Token displays remain in memory and appear only once in their own dialog.

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

No release or publication automation is configured yet. Versioning and supported
adapter contracts will be established before publishing packages.
