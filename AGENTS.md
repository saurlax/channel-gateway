# Repository Guide

Channel Gateway connects communication channels to Agent Hosts. Keep channel ingestion,
Host delivery, agent-facing tools, and process lifecycle separate.

## Sources and workflow

- Read `README.md` for scope and implementation status.
- Follow `CONTRIBUTING.md` for setup, checks, and contribution workflow.
- Follow `DEVELOPMENT.md` for engineering boundaries.
- Use GitHub Issues for proposals and unresolved design questions. Do not describe
  planned integrations as implemented features.
- Write canonical code and documentation in English.

## Changes

Keep changes focused and preserve unrelated work and secrets. Importing a library
must not start listeners, read credentials, or configure logging. Use async I/O in
service code; do not hold transactions across external I/O or Agent execution.
Keep the core independent of FastAPI, a13n, and individual channel SDKs.

Run checks for the affected surface and report actual outcomes. Local editing does
not authorize commits, pushes, GitHub writes, deployments, or releases. Carry out
those stages only when the user has authorized them.
