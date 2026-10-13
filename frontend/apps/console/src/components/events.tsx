import { useState } from "react";
import { useLocale } from "@/i18n";
import { RouteForm, type RouteSource } from "@/components/routes";
import {
  conversations,
  conversationKey,
  eventAddress,
  sourceFromAddress,
  type Conversation,
} from "@/conversations";
import type { Snapshot } from "@/api";
import type { Action } from "@/components/form-controls";
import { ConversationDialog } from "@/components/conversation-history";
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
  csrfToken,
  newEventIds,
  action,
  busy,
  error,
  clearError,
}: {
  snapshot: Snapshot;
  csrfToken: string;
  newEventIds: string[];
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
}) {
  const { t, message, date } = useLocale();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Conversation | null>(null);
  const [routeSource, setRouteSource] = useState<RouteSource | null>(null);
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
  const conversation = groups.find((group) => group.key === selected?.key) ?? selected;
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
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => {
                          clearError();
                          setSelected(group);
                        }}
                      >
                        {t("View conversation")}
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
                            onClick={() => {
                              clearError();
                              setRouteSource(group.source);
                            }}
                          >
                            {t("Create route")}
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
      {conversation && (
        <ConversationDialog
          key={conversation.key}
          open
          onOpenChange={(open) => !open && setSelected(null)}
          title={
            conversation.route?.name ??
            conversation.name ??
            channelName(conversation.source.channel_id)
          }
          source={conversation.source}
          snapshot={snapshot}
          csrfToken={csrfToken}
          action={action}
          busy={busy}
          error={error}
        />
      )}
    </div>
  );
}
