import { useState } from "react";
import { useLocale } from "@/i18n";
import {
  conversations,
  conversationKey,
  eventAddress,
  sourceFromAddress,
  newRoutePath,
  conversationPath,
} from "@/conversations";
import type { Snapshot } from "@/api";
import { ConsoleLink } from "@/components/console-link";
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

export function Events({
  snapshot,
  newEventIds,
  busy,
}: {
  snapshot: Snapshot;
  newEventIds: string[];
  busy: boolean;
}) {
  const { t, message, date } = useLocale();
  const [query, setQuery] = useState("");
  const groups = conversations(snapshot);
  const newConversations = new Set(
    snapshot.events
      .filter((event) => newEventIds.includes(event.id))
      .flatMap((event) => {
        const address = eventAddress(snapshot, event);
        const source = address && sourceFromAddress(address);
        return source ? [conversationKey(source)] : [];
      }),
  );
  const channelName = (id: string) =>
    snapshot.connections.find((item) => item.id === id)?.name ?? id;
  const rows = groups.filter((group) =>
    matchesQuery(query, [
      group.route?.name,
      group.name,
      channelName(group.source.channel_id),
      group.source.conversation_id,
      message(group.source.conversation_kind),
      ...group.search,
    ]),
  );
  return (
    <div className="grid grid-cols-1 gap-4">
      <ListToolbar query={query} onQueryChange={setQuery} label={t("Search messages…")} />
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Conversation")}</TableHead>
              <TableHead>{t("Channel account")}</TableHead>
              <TableHead>{t("Latest message")}</TableHead>
              <TableHead>{t("Updated at")}</TableHead>
              <TableHead>{t("Action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <EmptyRow columns={5} filtered={!!query.trim()}>
                {t("No conversations yet.")}
              </EmptyRow>
            ) : (
              rows.map((group) => (
                <TableRow
                  key={group.key}
                  className={newConversations.has(group.key) ? "signal-new-message" : undefined}
                >
                  <TableCell className="max-w-48 whitespace-normal">
                    <p className="font-medium">
                      {group.route?.name ?? group.name ?? channelName(group.source.channel_id)}
                    </p>
                    <p className="break-all text-xs text-muted-foreground">
                      {message(group.source.conversation_kind)}: {group.source.conversation_id}
                    </p>
                  </TableCell>
                  <TableCell>{channelName(group.source.channel_id)}</TableCell>
                  <TableCell>
                    <p className="max-w-sm truncate">
                      {group.latest?.text ?? t("No recent messages")}
                    </p>
                    {group.latest && (
                      <Badge variant="secondary">{message(group.latest.state)}</Badge>
                    )}
                  </TableCell>
                  <TableCell>{group.latest ? date(group.latest.created_at) : "—"}</TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button variant="outline" size="sm" asChild>
                        <ConsoleLink href={conversationPath(group.source)}>
                          {t("View conversation")}
                        </ConsoleLink>
                      </Button>
                      {!group.route &&
                        snapshot.connections.some((c) => c.id === group.source.channel_id) && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={
                              busy ||
                              !snapshot.connections.some(
                                (c) => c.kind === "a13n" || c.kind === "mcp",
                              )
                            }
                            asChild
                          >
                            <ConsoleLink href={newRoutePath(group.source)}>
                              {t("Create route")}
                            </ConsoleLink>
                          </Button>
                        )}
                    </div>
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
