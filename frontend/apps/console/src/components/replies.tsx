import { useState } from "react";
import { useLocale } from "@/i18n";
import type { Snapshot, Reply, Route } from "@/api";
import type { Action } from "@/components/form-controls";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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

export function Replies({
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
  const findRoute = (id: string) => snapshot.routes.find((route) => route.id === id);
  const rows = snapshot.replies.filter((reply) => {
    const route = findRoute(reply.route_id);
    return matchesQuery(query, [
      reply.id,
      reply.text,
      reply.state,
      message(reply.state),
      route?.name,
      route?.conversation_id,
      route && message(route.conversation_kind),
    ]);
  });
  const reply = snapshot.replies.find((record) => record.id === selected);
  return (
    <div className="grid grid-cols-1 gap-4">
      <ListToolbar query={query} onQueryChange={setQuery} label={t("Search replies…")} />
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Name / destination")}</TableHead>
              <TableHead>{t("Reply text")}</TableHead>
              <TableHead>{t("Status")}</TableHead>
              <TableHead>{t("Created at")}</TableHead>
              <TableHead>{t("Action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <EmptyRow columns={5} filtered={!!query.trim()}>
                {t(
                  "No reply drafts yet. Completed agent runs or MCP reply requests will appear here.",
                )}
              </EmptyRow>
            ) : (
              rows.map((record) => {
                const route = findRoute(record.route_id);
                return (
                  <TableRow key={record.id}>
                    <TableCell>
                      <p className="font-medium">{route?.name ?? record.route_id}</p>
                      <p className="text-xs text-muted-foreground">
                        {route ? message(route.conversation_kind) : ""} · {route?.conversation_id}
                      </p>
                    </TableCell>
                    <TableCell>
                      <p className="max-w-sm truncate">{record.text}</p>
                    </TableCell>
                    <TableCell>
                      <Badge variant="secondary">{message(record.state)}</Badge>
                    </TableCell>
                    <TableCell>{date(record.created_at)}</TableCell>
                    <TableCell>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => {
                          clearError();
                          setSelected(record.id);
                        }}
                      >
                        {record.state === "draft" ? t("Review") : t("View details")}
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </TableFrame>
      <ManagementDialog
        open={!!reply}
        onOpenChange={(open) => !open && setSelected(null)}
        title={t("Reply details")}
        description={
          reply
            ? `${findRoute(reply.route_id)?.name ?? reply.route_id} · ${date(reply.created_at)}`
            : ""
        }
        busy={busy}
        error={error}
      >
        {reply && (
          <ReplyReview
            key={reply.id}
            reply={reply}
            route={findRoute(reply.route_id)}
            action={action}
            busy={busy}
            onSaved={() => setSelected(null)}
          />
        )}
      </ManagementDialog>
    </div>
  );
}

function ReplyReview({
  reply,
  route,
  action,
  busy,
  onSaved,
}: {
  reply: Reply;
  route?: Route;
  action: Action;
  busy: boolean;
  onSaved: () => void;
}) {
  const { t, message } = useLocale();
  const [text, setText] = useState(reply.text);
  return (
    <div className="grid gap-4">
      <Badge variant="secondary">{message(reply.state)}</Badge>
      <p className="text-sm">
        {t("Destination:")} {route ? message(route.conversation_kind) : ""} {route?.conversation_id}
      </p>
      {reply.state === "draft" && (
        <Alert>
          <AlertTitle>{t("Review the destination before sending")}</AlertTitle>
          <AlertDescription>
            {t(
              "Group replies always require approval. Confirm that the draft is appropriate for everyone in the destination conversation.",
            )}
          </AlertDescription>
        </Alert>
      )}
      <Label htmlFor="reply-text">{t("Reply text")}</Label>
      <Textarea
        id="reply-text"
        value={text}
        onChange={(event) => setText(event.target.value)}
        readOnly={reply.state !== "draft"}
        maxLength={16000}
        rows={6}
      />
      {reply.error && (
        <p role="alert" className="text-sm text-muted-foreground">
          {message(reply.error)}
        </p>
      )}
      {reply.state === "draft" && (
        <div className="flex gap-2">
          <Button
            disabled={busy || !text.trim() || !route?.enabled}
            onClick={async () => {
              if (await action(`replies/${reply.id}/approve`, { text })) onSaved();
            }}
          >
            {t("Approve and send")}
          </Button>
          <Button
            variant="outline"
            disabled={busy}
            onClick={async () => {
              if (await action(`replies/${reply.id}/reject`)) onSaved();
            }}
          >
            {t("Reject")}
          </Button>
        </div>
      )}
    </div>
  );
}
