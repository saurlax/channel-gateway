import { useState } from "react";
import { useLocale } from "@/i18n";
import type { Snapshot } from "@/api";
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
  const routeName = (id: string) => snapshot.routes.find((route) => route.id === id)?.name ?? id;
  const rows = snapshot.events.filter((event) =>
    matchesQuery(query, [
      event.id,
      routeName(event.route_id),
      event.message.sender_id,
      event.message.text,
      event.state,
      message(event.state),
      event.thread_id,
      event.error,
    ]),
  );
  const event = snapshot.events.find((record) => record.id === selected);
  return (
    <div className="grid grid-cols-1 gap-4">
      <p className="text-sm text-muted-foreground">
        {t(
          "Messages accepted from verified channels and authorized senders. Refreshes every five seconds.",
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
                {t("No messages yet. Connect OneBots and enable a conversation route to begin.")}
              </EmptyRow>
            ) : (
              rows.map((record) => (
                <TableRow key={record.id}>
                  <TableCell>{routeName(record.route_id)}</TableCell>
                  <TableCell>
                    <p className="text-xs text-muted-foreground">{record.message.sender_id}</p>
                    <p className="max-w-sm truncate">{record.message.text}</p>
                  </TableCell>
                  <TableCell>
                    <Badge variant="secondary">{message(record.state)}</Badge>
                  </TableCell>
                  <TableCell>{date(record.created_at)}</TableCell>
                  <TableCell className="text-xs">{record.thread_id ?? t("Not created")}</TableCell>
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
        open={!!event}
        onOpenChange={(open) => !open && setSelected(null)}
        title={t("Message details")}
        description={event ? `${routeName(event.route_id)} · ${date(event.created_at)}` : ""}
        error={error}
        busy={busy}
      >
        {event && (
          <div className="grid gap-4">
            <Badge variant="secondary">{message(event.state)}</Badge>
            <p className="text-xs text-muted-foreground">{event.message.sender_id}</p>
            <p className="whitespace-pre-wrap break-words">{event.message.text}</p>
            <p className="break-all text-xs text-muted-foreground">
              {t("Agent thread")}: {event.thread_id ?? t("Not created")}
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
