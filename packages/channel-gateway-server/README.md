# Channel Gateway Server

FastAPI service, command-line entry point, and optional bundled React console.
The current foundation exposes process health, implementation status, and API
documentation. Channel listeners, persistence, MCP tools, and Agent Host delivery
are not implemented yet.

From the repository root, `make serve` builds the console and starts the service.
`make dev-api` starts the backend without requiring a frontend build. Run
`channel-gateway serve --help` for listen options.

Build the frontend before packaging to include it in the wheel and source
archive. Headless packages remain usable without frontend assets or Node.js.
