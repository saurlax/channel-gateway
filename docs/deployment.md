# Container deployment

Build the image with `docker build -t channel-gateway .`. It builds the console with
Node.js 24 and pnpm, then installs the locked Python 3.13 uv workspace. The runtime
needs no Node.js. The listener uses port 8080.

Mount a persistent volume at `/data` and configure these variables:

| Variable | Value |
| --- | --- |
| `CG_ADMIN_USERNAME` | Your management username |
| `CG_ADMIN_PASSWORD` | A private password of at least eight characters |
| `CG_DATA_DIR` | `/data/state` |

The container prepares the private state directory on the mounted volume, then
starts the service as UID/GID 10001. Use a single replica: SQLite storage, channel
pollers, and pairing sessions belong to one process. Preserve the volume across
deployments. Backups contain upstream credentials and must remain private.

Place the service behind a trusted HTTPS reverse proxy. This image trusts incoming
forwarded headers so cookies and same-origin checks use the public HTTPS origin.
Only the platform proxy may reach the container listener; do not expose this
configuration directly to untrusted clients. Public health checks use `/healthz`.
