# Channel Gateway Server

FastAPI management service, OneBot v11 and official QQ/Weixin text channels to
a13n, SQLite persistence,
host/route-scoped MCP tools and generic MCP client Hosts, and a bundled shadcn management console.

See [the bridge setup guide](../../docs/onebots-a13n.md),
[official channel setup](../../docs/official-channels.md),
[Agent Hosts](../../docs/agent-hosts.md), and the
[technical contracts](../../spec/README.md). Management needs
`CGW_ADMIN_USERNAME` and `CGW_ADMIN_PASSWORD` (at least 8 characters),
with administrator cookie sessions. State is stored under
`CGW_DATA_DIR`, defaulting to `.channel-gateway`; use one process/replica.

From the repository root, `make serve` builds the console and starts the service.
`make dev-api` starts the backend without requiring a frontend build. Run
`channel-gateway serve --help` for listen options.

Build the frontend before packaging to include it in the wheel and source
archive. Headless packages remain usable without frontend assets or Node.js.
