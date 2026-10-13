import assert from "node:assert/strict";
import test from "node:test";
import { dashboardSummary } from "../src/lib/dashboard.ts";

test("dashboard separates channel and host resources and excludes generated routes", () => {
  const result = dashboardSummary({
    connections: ["onebots", "qq", "weixin", "a13n", "mcp"].map((kind) => ({ kind })),
    routes: [
      { id: "parent", enabled: true },
      { parent_route_id: "parent", enabled: true },
      { id: "disabled", enabled: false },
    ],
    events: [],
    replies: [],
  });
  assert.equal(result.channels, 3);
  assert.equal(result.hosts, 2);
  assert.equal(result.apiHosts, 1);
  assert.equal(result.mcpHosts, 1);
  assert.equal(result.routes, 2);
  assert.equal(result.enabledRoutes, 1);
});

test("recent activity keeps MCP availability, acceptance, completion and reply delivery separate", () => {
  const result = dashboardSummary({
    connections: [],
    routes: [],
    events: [
      "queued",
      "submitted",
      "available",
      "available",
      "completed",
      "failed",
      "blocked",
      "unmatched",
    ].map((state) => ({ state })),
    replies: ["approved", "sending", "sent", "blocked", "unknown"].map((state) => ({
      state,
    })),
  });
  assert.equal(result.messages, 8);
  assert.equal(result.available, 2);
  for (const key of ["queued", "submitted", "completed", "failed", "blocked", "unmatched"])
    assert.equal(result[key], 1);
  assert.equal(result.replyIssues, 2);
});

test("an empty gateway has zero counts without invented activity", () => {
  const result = dashboardSummary({ connections: [], routes: [], events: [], replies: [] });
  assert.ok(Object.values(result).every((value) => value === 0));
});
