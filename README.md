# Channel Gateway

[![CI](https://github.com/saurlax/channel-gateway/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/saurlax/channel-gateway/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)

**The communication gateway for AI agents.**

Channel Gateway is an open-source project for connecting AI agents to the places people
communicate. Incoming events can be routed to an Agent Host, while agents use MCP
tools to read conversations and act across channels. The goal is a reusable
communication layer: any channel, any agent.

Channel ingestion and Agent Host delivery have separate adapters. This allows a
conversation to reach an agent in a13n or another Host, while the agent can consult
other authorized channels before deciding where and how to respond.

> **Project status:** An initial OneBots ↔ a13n text bridge is implemented, with
> signed event ingestion, conversation routes, persistent delivery records,
> scoped MCP tools, and a management console built from official shadcn blocks.
> Cross-channel identity linking, full RBAC, moderation actions, and voice/radio
> integrations remain planned. This is a single-process prototype.

## How it fits together

The implemented bridge separates channel ingestion, host execution, and
agent-facing communication tools. Group replies pass through administrator approval.

```mermaid
flowchart LR
    Channels[IM and future voice channels] -->|Incoming events| Gateway[Channel Gateway]
    Gateway -->|Host adapter submits input| Hosts[a13n and other Agent Hosts]
    Hosts --> Agents[Agent runs]
    Agents -->|Read conversations and send messages| MCP[Channel Gateway MCP tools]
    MCP --> Gateway
    Gateway -->|Channel adapter sends a reply| Channels
```

- **Connect channels.** Receive platform events through channel adapters, starting
  with OneBots using OneBot v11 reverse HTTP. Telephone and radio require separate media
  and session adapters; they are future directions.
- **Deliver to agents.** Bind channel conversations to Agent Host threads and
  submit input through the Host's supported API. MCP notifications alone do not
  create or wake an Agent run.
- **Give agents communication tools.** Expose conversation reads and channel
  actions through MCP, with access scoped to the authorized channels.
- **Embed or run separately.** Keep reusable Python behavior independent of the
  HTTP server and optional console so it can later be embedded in a Host.

Channel Gateway is independent of [Agent Foundation (a13n)](https://github.com/converge-ai-labs/agent-foundation)
and [OneBots](https://github.com/lc-cn/onebots). The initial development layout
follows a13n's Python/uv and React/TypeScript/Vite approach.

## Run from source

You need **Python 3.13**, [**uv**](https://docs.astral.sh/uv/), **Make**, **Node.js 24
or later**, and **pnpm 10.34.5** for the console. There are no published packages or
container images yet.

```bash
git clone https://github.com/saurlax/channel-gateway.git
cd channel-gateway
make install
export CHANNEL_GATEWAY_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
make serve
```

Open **<http://127.0.0.1:8310>** for the console and
**<http://127.0.0.1:8310/api/docs>** for the API documentation. The console displays
separate channel and Agent Host panels, conversation routes, received messages,
and reply approvals. Management pages use a search toolbar and table; Add and
row actions open dialogs. Sign in
with the administrator token you configured. Follow the
[OneBots and a13n setup guide](docs/onebots-a13n.md) to connect real accounts.

The console follows the browser's language preferences automatically. It supports
English and Simplified Chinese, choosing the first supported language and falling
back to English. Chinese language variants currently use Simplified Chinese.
Channel messages, user-defined names, and identifiers remain in their original
language.
Stop the foreground service with `Ctrl+C`.

To run only the backend, without Node.js or pnpm:

```bash
uv sync --locked
export CHANNEL_GATEWAY_ADMIN_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run --locked channel-gateway serve
```

The backend exposes `GET /healthz`, `GET /api/v1/status`, and
`GET /api/openapi.json`. It serves the console only when frontend assets are
present. Run `uv run channel-gateway serve --help` for listen options.

The service binds to loopback by default. Management requires a bearer token of
at least 32 characters. Without one, health and status remain available but
management is disabled. SQLite state defaults to `.channel-gateway/`; set
`CHANNEL_GATEWAY_DATA_DIR` to a persistent private directory when hosting the service.
Credentials and message history are stored there, without encryption at rest.
Use one process and one replica. Expose authenticated endpoints through HTTPS.

## Develop

Python packages are managed by a uv workspace. The private frontend uses a pnpm
workspace, with compiled assets bundled into the server package. Node.js is needed
to build the console, not to run a built Python package.

| Work on                             | Command        |
| ----------------------------------- | -------------- |
| Install locked dependencies         | `make install` |
| Backend with reload                 | `make dev-api` |
| Frontend with hot reload            | `make dev-web` |
| Serve the built console and backend | `make serve`   |
| Formatting, lint, and type checks   | `make check`   |
| Backend and core tests              | `make test`    |
| Console and Python package builds   | `make build`   |

Run `make dev-api` and `make dev-web` in separate terminals. The frontend at
`http://127.0.0.1:5173` proxies API requests to the backend at
`http://127.0.0.1:8310`.

```text
packages/
  channel-gateway-core/    Reusable channel addresses and text message models
  channel-gateway-server/    FastAPI application, CLI, and optional console assets
frontend/
  apps/console/         React + TypeScript + Vite management console
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and validation and
[DEVELOPMENT.md](DEVELOPMENT.md) for component boundaries and engineering standards.
The [specification index](spec/README.md) leads to the accepted architecture,
channel, access, delivery, and Agent tool contracts. User setup instructions live
in `docs/`; unresolved designs and plans belong in GitHub Issues.

## Contribute

Bug reports, documentation improvements, and focused integrations are welcome.
Search [GitHub Issues](https://github.com/saurlax/channel-gateway/issues) before opening a
proposal. Discuss routing semantics, delivery guarantees, and new adapter
contracts before implementing them. Keep implementation claims aligned with the
code that users can run.

For private vulnerability reports, follow [SECURITY.md](SECURITY.md).

## License

Channel Gateway is licensed under the [Apache License 2.0](LICENSE).

The console includes MIT-licensed [shadcn/ui](https://github.com/shadcn-ui/ui)
components and the official [sidebar-07](https://ui.shadcn.com/blocks/sidebar) and
[login-01](https://ui.shadcn.com/blocks/authentication) blocks. Their license is
retained in the frontend and bundled assets.

Copyright 2026 Saurlax.
