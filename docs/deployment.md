# Container deployment

## Published image

The CI workflow builds and publishes `ghcr.io/saurlax/channel-gateway` after the
Python and frontend jobs pass on a `main` push or a manual run on `main`.
Pull requests run checks without publishing. The Linux AMD64 runtime image
includes the console and is checked for startup, health, console delivery, and
administrator login before publication. No registry token needs to be added to
repository secrets; publishing uses the workflow's scoped `GITHUB_TOKEN`.

Tags are `dev`, `latest`, `main`, and `sha-<full commit SHA>`. The first three
point to the same latest successful `main` publication. The existing Zeabur
service uses `dev`. Use the commit tag or published digest when an immutable
reference is needed. Image metadata links the package to its source commit.
Make the GHCR package public for anonymous platform pulls; private packages need
a read-only registry credential configured in the hosting platform.

## Runtime and persistent state

Build the image with `docker build -t channel-gateway .`. It builds the console with
Node.js 24 and pnpm, then installs the locked Python 3.13 uv workspace. The runtime
needs no Node.js. The listener uses port 8080.

Mount a persistent volume at `/data` and configure these variables:

| Variable | Value |
| --- | --- |
| `CGW_ADMIN_USERNAME` | Your management username |
| `CGW_ADMIN_PASSWORD` | A private password of at least eight characters |
| `CGW_DATA_DIR` | `/data/state` |

The container prepares the private state directory on the mounted volume, then
starts the service as UID/GID 10001. Use a single replica: SQLite storage, channel
pollers, and pairing sessions belong to one process. Preserve the volume across
deployments. Backups contain upstream credentials and must remain private.

Place the service behind a trusted HTTPS reverse proxy. This image trusts incoming
forwarded headers so cookies and same-origin checks use the public HTTPS origin.
Only the platform proxy may reach the container listener; do not expose this
configuration directly to untrusted clients. Public health checks use `/healthz`.

The console uses `/overview`, `/channels`, `/hosts`, `/routes`, and `/events`,
with `/routes/new` and `/routes/{id}/edit` for route configuration.
Forward these paths to the gateway along with `/`, `/assets/`, `/api/`, and
`/mcp/`; the bundled server serves console pages directly, including on reload.
No proxy rewrite or redirect is needed. `/` displays Overview. Replace old
hash-based bookmarks with the corresponding path; legacy hashes are ignored.

For Zeabur, deploy into the intended project, attach the volume, configure the
three variables, and assign an HTTPS domain. Reuse the recorded service ID for
later deployments. Verify management login before configuring channels and Hosts.
Pair Weixin only after the service is stable, and stop any previous poller for the
same bot before moving it to this deployment.

## Zeabur image updates

The existing service and volume are recorded in [CLAUDE.md](../CLAUDE.md).
Its public URL is `https://cgw.saurlax.com` and its MCP endpoint is
`https://cgw.saurlax.com/mcp/`.
Use its GHCR image configuration rather than uploading the source to Zeabur.
Publishing an image and rolling out the service are separate operations.

After a successful publication, apply `dev` on the same service to pull its
current image. Updating the registry tag alone does not restart Zeabur:

```bash
npx zeabur@latest service update tag \
  --id 6aca42e54873e494859cef83 \
  --env-id 6abb56df4fc8a0a35a5dda61 \
  --tag dev --yes --interactive=false
```

The update restarts the service while preserving the `/data` volume, variables,
domain, and MCP endpoint. Do not delete/recreate the service or switch versions
by uploading a new source deployment. Wait for successful image publication,
check pending work before restarting, and verify `/healthz`, login, channel
connectivity, and a13n MCP access after the update. To roll back, select an earlier
published commit tag on the same service.
