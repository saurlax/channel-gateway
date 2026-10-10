# Repository Guide

Channel Gateway connects communication channels to Agent Hosts. Keep channel ingestion,
Host delivery, agent-facing tools, and process lifecycle separate.

## Sources and workflow

- Read `README.md` for scope and implementation status.
- Follow `CONTRIBUTING.md` for setup, checks, and contribution workflow.
- Follow `DEVELOPMENT.md` for engineering boundaries.
- Read `spec/README.md` and the owning contract before changing channel identity,
  authorization, routing, delivery, or Agent tools. Update affected specs,
  implementation, tests, and user instructions together.
- Use GitHub Issues for proposals and unresolved design questions. Do not describe
  planned integrations as implemented features.
- Write canonical code and documentation in English.

`spec/` owns accepted technical contracts; `docs/` owns user instructions.
Keep proposals and unresolved decisions in Issues, not specifications. Do not
turn a planned transport or integration into an implemented-feature claim.

## Changes

Keep changes focused and preserve unrelated work and secrets. Importing a library
must not start listeners, read credentials, or configure logging. Use async I/O in
service code; do not hold transactions across external I/O or Agent execution.
Keep the core independent of FastAPI, a13n, and individual channel SDKs.

Run checks for the affected surface and report actual outcomes. Local editing does
not authorize commits, pushes, GitHub writes, deployments, or releases. Carry out
those stages only when the user has authorized them.

## Console management interfaces

- Do not use middle-dot separators in interface copy. Use clear labels or
  separate lines. Channel type labels use plain QQ and localized Weixin names,
  or OneBot followed by its transport. Do not wrap channel types or transports
  in parentheses.

- Sidebar navigation uses static Gateway and Activity section labels with direct
  resource links underneath; these sections do not collapse.

- Keep Channel and Agent Host management in separate navigation panels. Share
  implementation helpers where useful, but give each resource its own list,
  search, creation flow, and labels.
- Agent Hosts support a13n API + MCP and generic MCP clients. Keep upstream
  credentials separate from Host MCP keys; enforce route assignment on the server.
  Generic MCP routes have no a13n agent or connection binding fields.
- Management pages use a search toolbar above a table. Show empty and filtered
  states inside the table; keep the layout usable on narrow screens with table
  scrolling contained in its frame.
- For resources users can create, put an Add button in the toolbar and open the
  creation form in a modal. Editing, credentials, details, and reply review also
  open from row actions in modals. Read-only activity lists need no Add button.
- Use the official shadcn blocks and components with their standard styles. Keep
  custom styling minimal. Preserve browser-language localization, accessible
  labels, keyboard interaction, and destination/approval checks when moving forms.

[DEVELOPMENT.md](DEVELOPMENT.md#console-management-patterns) owns the detailed
console interaction standards.
