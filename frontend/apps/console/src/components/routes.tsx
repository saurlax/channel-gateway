import { useLocale } from "@/i18n";
import { useState } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ListToolbar, TableFrame, EmptyRow, matchesQuery } from "@/components/management";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { isHost } from "@/hosts";
import type { Snapshot } from "@/api";
import { routeSources } from "@/lib/route-sources";
import { ConsoleLink } from "@/components/console-link";
import { routeEditPath } from "@/lib/navigation";

export function Routes({ snapshot, busy }: { snapshot: Snapshot; busy: boolean }) {
  const { t, message } = useLocale();
  const [query, setQuery] = useState("");
  const connectionName = (id: string) =>
    snapshot.connections.find((connection) => connection.id === id)?.name ?? id;
  const rows = snapshot.routes
    .filter((route) => !route.parent_route_id)
    .filter((route) =>
      matchesQuery(query, [
        route.name,
        route.id,
        ...routeSources(route).flatMap((source) => [
          source.conversation_id,
          connectionName(source.channel_id),
        ]),
        message(route.conversation_kind),
        route.agent_id,
        route.thread_id,
        connectionName(route.channel_id),
        connectionName(route.host_id),
        ...route.allowed_user_ids,
      ]),
    );
  const canAdd =
    snapshot.connections.some((connection) => !isHost(connection)) &&
    snapshot.connections.some(isHost);
  return (
    <div className="grid grid-cols-1 gap-4">
      {!canAdd && (
        <p className="text-sm text-muted-foreground">
          {t("Add a channel and an agent host first.")}
        </p>
      )}
      <ListToolbar query={query} onQueryChange={setQuery} label={t("Search routes…")}>
        <Button asChild disabled={busy || !canAdd}>
          <ConsoleLink href="/routes/new">
            <Plus />
            {t("Add a route")}
          </ConsoleLink>
        </Button>
      </ListToolbar>
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Name")}</TableHead>
              <TableHead>{t("Sources")}</TableHead>
              <TableHead>{t("Agent / thread")}</TableHead>
              <TableHead>{t("Allowed users")}</TableHead>
              <TableHead>{t("Reply source")}</TableHead>
              <TableHead>{t("Access")}</TableHead>
              <TableHead>{t("Action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <EmptyRow columns={7} filtered={!!query.trim()}>
                {t("No routes yet.")}
              </EmptyRow>
            ) : (
              rows.map((record) => (
                <TableRow key={record.id}>
                  <TableCell>
                    <p className="font-medium">{record.name}</p>
                  </TableCell>
                  <TableCell>
                    <div className="grid gap-1">
                      {routeSources(record).map((source, index) => (
                        <p key={index} className="text-xs">
                          {source.channel_id === "*"
                            ? t("All channels")
                            : connectionName(source.channel_id)}{" "}
                          /{" "}
                          {source.conversation_id === "*"
                            ? t(source.conversation_kind === "private" ? "All users" : "All groups")
                            : t(
                                source.conversation_kind === "private" ? "User {id}" : "Group {id}",
                                { id: source.conversation_id },
                              )}
                        </p>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell>
                    <p>{connectionName(record.host_id)}</p>
                    <p className="text-xs">{record.agent_id}</p>
                    <p className="text-xs text-muted-foreground">
                      {snapshot.connections.find((c) => c.id === record.host_id)?.kind === "mcp"
                        ? t("Client pulls through MCP")
                        : record.thread_policy === "fixed"
                          ? (record.fixed_thread_id ?? t("Share context across all sources"))
                          : record.thread_policy === "sender"
                            ? t("Keep each group member separate")
                            : record.thread_policy === "message"
                              ? t("Start fresh for every message")
                              : (record.thread_id ?? t("Continue each private chat or group"))}
                    </p>
                  </TableCell>
                  <TableCell>
                    {record.sender_policy === "any"
                      ? t("Any sender")
                      : record.allowed_user_ids.join(", ")}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {record.reply_source === "output" ? t("Final run output") : t("MCP tools")}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{record.enabled ? t("Enabled") : t("Disabled")}</Badge>
                  </TableCell>
                  <TableCell>
                    <Button variant="outline" size="sm" disabled={busy} asChild>
                      <ConsoleLink href={routeEditPath(record.id)}>{t("Edit")}</ConsoleLink>
                    </Button>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableFrame>
    </div>
  );
}
