import type { Snapshot } from "../api";

export function dashboardSummary(snapshot: Snapshot) {
  const channels = snapshot.connections.filter((item) =>
    ["onebots", "qq", "weixin"].includes(item.kind),
  );
  const hosts = snapshot.connections.filter((item) => ["a13n", "mcp"].includes(item.kind));
  const routes = snapshot.routes.filter((route) => !route.parent_route_id);
  const count = (...states: string[]) =>
    snapshot.events.filter((event) => states.includes(event.state)).length;
  return {
    channels: channels.length,
    hosts: hosts.length,
    apiHosts: hosts.filter((host) => host.kind === "a13n").length,
    mcpHosts: hosts.filter((host) => host.kind === "mcp").length,
    routes: routes.length,
    enabledRoutes: routes.filter((route) => route.enabled).length,
    messages: snapshot.events.length,
    queued: count("queued"),
    submitted: count("submitted"),
    available: count("available"),
    completed: count("completed"),
    failed: count("failed"),
    blocked: count("blocked"),
    unmatched: count("unmatched"),
    replyIssues: snapshot.replies.filter((reply) => ["blocked", "unknown"].includes(reply.state))
      .length,
  };
}
