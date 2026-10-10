# Contributing

Channel Gateway is in its foundation stage. Bug reports, focused improvements, and
channel or Agent Host integrations are welcome. Search existing
[issues](https://github.com/saurlax/channel-gateway/issues) before opening a proposal.
Discuss material changes to routing, identity, delivery guarantees, or adapter
contracts in an issue before implementation. Small corrections can go directly
to a pull request.

## Development setup

Use Python 3.13, [uv](https://docs.astral.sh/uv/), Make, Node.js 24 or later, and the
pnpm version pinned in `frontend/package.json`. From a checkout:

```bash
make install
```

Run the backend and frontend in separate terminals:

```bash
make dev-api
```

```bash
make dev-web
```

The frontend uses `http://127.0.0.1:5173` and proxies API requests to the backend
at `http://127.0.0.1:8310`. To use one process serving the built console instead,
run `make serve`.

## Validation

| Change | Required local checks |
| --- | --- |
| Python behavior | `make check-python test` |
| Frontend behavior | `make check-web build-web` |
| Packaging or dependencies | Affected checks, locked installation, and `make build` |
| Documentation | Verify commands, relative links, and implementation claims |

`make check test build` validates the complete initial project. Run focused tests
when they cover the affected behavior; broaden checks when shared contracts or
new failures require it. Do not claim a check passed if it did not run.

Use `uv add --package PACKAGE DEPENDENCY` for runtime dependencies and `uv add
--dev DEPENDENCY` for development tools. Update and commit `uv.lock` with manifest
changes. Manage frontend dependencies with pnpm and commit its lockfile.

Format Python with `uv run ruff format .` and lint fixes with `uv run ruff check
--fix .`. Format frontend sources with `pnpm --dir frontend run format`.

## Code and documentation

Follow [DEVELOPMENT.md](DEVELOPMENT.md). Write canonical repository content in
English. Add tests for behavior and failure cases, rather than mirroring the
implementation. Keep documentation consistent with what users can actually run.
Generated console assets, virtual environments, local state, and credentials are
not committed.

## Pull requests

Use a scoped Conventional Commit title, such as `feat(channels): receive OneBot
messages`. Describe the concrete problem, resulting behavior, and relevant
validation. Link an issue when it explains an agreed design. Keep changes small
enough to review and avoid unrelated cleanup.

## Security

Follow [SECURITY.md](SECURITY.md) for private vulnerability reports. Remove tokens,
private conversation content, and personal data from public reports and examples.
