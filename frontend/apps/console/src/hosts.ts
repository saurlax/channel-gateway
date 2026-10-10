import type { Connection } from "@/api";

export function isHost(connection: Connection) {
  return connection.kind === "a13n" || connection.kind === "mcp";
}
