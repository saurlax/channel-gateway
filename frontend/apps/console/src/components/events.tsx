import { useState } from "react";
import { useLocale } from "@/i18n";
import { RouteForm, type RouteSource } from "@/components/routes";
import { eventAddress, type Snapshot } from "@/api";
import type { Action } from "@/components/form-controls";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  ListToolbar,
  TableFrame,
  EmptyRow,
  ManagementDialog,
  matchesQuery,
} from "@/components/management";
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
  action,
  busy,
  error,
  clearError,
}: {
  snapshot: Snapshot;
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
}) {
  const { t, message, date } = useLocale();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [routeSource, setRouteSource] = useState<RouteSource | null>(null);
  const conversationName = (record: import("@/api").ChannelEvent) => {
    const address = eventAddress(snapshot, record);
    if (!address) return record.route_id ?? t("Not created");
    const channel = snapshot.connections.find((item) => item.id === address.account_id);
    const route = snapshot.routes.find((item) => item.id === record.route_id);
    return route?.name ?? channel?.name ?? address.account_id;
  };
  const rows = snapshot.events.filter((event) =>
    matchesQuery(query, [
      event.id,
      conversationName(event),
      event.message.sender_id,
      event.message.text,
      event.state,
      message(event.state),
      event.thread_id,
      event.error,
    ]),
  );
  const event = snapshot.events.find((record) => record.id === selected);
  const threadLabel = (record: import("@/api").ChannelEvent) => {
    const route = snapshot.routes.find((route) => route.id === record.route_id);
    const host = snapshot.connections.find((connection) => connection.id === route?.host_id);
    return (
      record.thread_id ?? (host?.kind === "mcp" ? t("Client pulls through MCP") : t("Not created"))
    );
  };
  return (
    <div className="grid grid-cols-1 gap-4">
      <p className="text-sm text-muted-foreground">
        {t(
          "Received channel messages, including conversations without a route. Refreshes every five seconds.",
        )}
      </p>
      <ListToolbar query={query} onQueryChange={setQuery} label={t("Search messages…")} />
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Conversation")}</TableHead>
              <TableHead>{t("Sender / message")}</TableHead>
              <TableHead>{t("Delivery")}</TableHead>
              <TableHead>{t("Created at")}</TableHead>
              <TableHead>{t("Agent thread")}</TableHead>
              <TableHead>{t("Action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <EmptyRow columns={6} filtered={!!query.trim()}>
                {t(
                  "No messages yet. Send a message to a connected channel to discover its sender ID.",
                )}
              </EmptyRow>
            ) : (
              rows.map((record) => (
                <TableRow key={record.id}>
                  <TableCell className="max-w-48 whitespace-normal">
                    <p>{conversationName(record)}</p>
                    <p className="break-all text-xs text-muted-foreground">
                      {eventAddress(snapshot, record)?.conversation_id}
                    </p>
                  </TableCell>
                  <TableCell className="max-w-56 whitespace-normal">
                    <p className="break-all text-xs text-muted-foreground">
                      {t("Sender ID")}: {record.message.sender_id}
                    </p>
                    <p className="whitespace-pre-wrap break-words">{record.message.text}</p>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{message(record.state)}</Badge>
                  </TableCell>
                  <TableCell>{date(record.created_at)}</TableCell>
                  <TableCell className="max-w-32 truncate text-xs" title={threadLabel(record)}>
                    {threadLabel(record)}
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          clearError();
                          setSelected(record.id);
                        }}
                      >
                        {t("View details")}
                      </Button>
                      {!record.route_id && eventAddress(snapshot, record) && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={
                            busy ||
                            !snapshot.connections.some((c) => c.kind === "a13n" || c.kind === "mcp")
                          }
                          onClick={() => {
                            clearError();
                            const address = eventAddress(snapshot, record);
                            if (!address) return;
                            const [kind, ...parts] = address.conversation_id.split(":");
                            setRouteSource({
                              channel_id: address.account_id,
                              conversation_kind: kind as "private" | "group",
                              conversation_id: parts.join(":"),
                            });
                          }}
                        >
                          {t("Create route")}
                        </Button>
                      )}
                      {record.state === "failed" && (
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() => void action(`events/${record.id}/retry`)}
                        >
                          {t("Retry")}
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
      <ManagementDialog
        open={!!routeSource}
        onOpenChange={(open) => !open && setRouteSource(null)}
        title={t("Add a route")}
        description={t("Use the sender and conversation IDs from this received message.")}
        busy={busy}
        error={error}
        wide
      >
        {routeSource && (
          <RouteForm
            initial={routeSource}
            snapshot={snapshot}
            action={action}
            busy={busy}
            onSaved={() => setRouteSource(null)}
          />
        )}
      </ManagementDialog>
      <ManagementDialog
        open={!!event}
        onOpenChange={(open) => !open && setSelected(null)}
        title={t("Message details")}
        description={event ? `${conversationName(event)}: ${date(event.created_at)}` : ""}
        error={error}
        busy={busy}
      >
        {event && (
          <div className="grid gap-4">
            <Badge variant="secondary">{message(event.state)}</Badge>
            <p className="break-all text-sm">
              {t("Sender ID")}: {event.message.sender_id}
            </p>
            <p className="break-all text-sm">
              {t("Conversation")}: {eventAddress(snapshot, event)?.conversation_id}
            </p>
            <p className="whitespace-pre-wrap break-words">{event.message.text}</p>
            <p className="break-all text-xs text-muted-foreground">
              {t("Agent thread")}: {threadLabel(event)}
            </p>
            {event.error && (
              <p role="alert" className="text-sm text-muted-foreground">
                {message(event.error)}
              </p>
            )}
            {event.state === "failed" && (
              <Button disabled={busy} onClick={() => void action(`events/${event.id}/retry`)}>
                {t("Retry")}
              </Button>
            )}
          </div>
        )}
      </ManagementDialog>
    </div>
  );
}
