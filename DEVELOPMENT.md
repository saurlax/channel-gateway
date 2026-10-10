# Development Standards

## Boundaries

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

Channel adapters, Host adapters, persistence, and MCP tools are planned; the
initial code implements only message models and the service/console foundation.
Add these modules when their first behavior is implemented, rather than creating
empty adapter frameworks.

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
conversation access boundaries before exposing adapters. The initial service
binds to loopback, has no authentication, and contains no real account state;
authentication is required before sharing a management service with credentials
or private conversations.

## Extensibility

Public adapter and event contracts must be independent of a13n-specific object
identities. Keep platform capability differences visible: text messages, stored
audio, and live calls have different lifecycles. A text envelope is not a complete
telephone or radio protocol.

The core can be embedded as a Python library. A standalone deployment owns its
listeners; an embedded deployment's Host owns them. Keep the process entry point
and frontend optional so future Hosts can reuse the same application behavior.

## Packaging

Python packages use uv workspace sources and Hatchling, with `0.0.0` source
versions. The React/TypeScript/Vite console is private build input. Its static
output is bundled into the server package when built, and is not committed.
Installed Python packages do not require Node.js. A headless package can be built
without console assets; `make build` includes the console.

No release or publication automation is configured yet. Versioning and supported
adapter contracts will be established before publishing packages.
