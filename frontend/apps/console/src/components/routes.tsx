import { useLocale } from "@/i18n";
import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { FormField, Choice, field, type Action } from "@/components/form-controls";
import { Switch } from "@/components/ui/switch";
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
import type { Snapshot } from "@/api";

export function Routes({
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
  const { t, message } = useLocale();
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const connectionName = (id: string) =>
    snapshot.connections.find((connection) => connection.id === id)?.name ?? id;
  const rows = snapshot.routes.filter((route) =>
    matchesQuery(query, [
      route.name,
      route.id,
      route.conversation_id,
      message(route.conversation_kind),
      route.agent_id,
      route.thread_id,
      connectionName(route.channel_id),
      connectionName(route.host_id),
      ...route.allowed_user_ids,
    ]),
  );
  const route = snapshot.routes.find((record) => record.id === selected);
  const canAdd =
    snapshot.connections.some((connection) => connection.kind === "onebots") &&
    snapshot.connections.some((connection) => connection.kind === "a13n");
  return (
    <div className="grid grid-cols-1 gap-4">
      {!canAdd && (
        <p className="text-sm text-muted-foreground">
          {t("Add a OneBots connection and an a13n host first.")}
        </p>
      )}
      <ListToolbar query={query} onQueryChange={setQuery} label={t("Search routes…")}>
        <Button
          disabled={busy || !canAdd}
          onClick={() => {
            clearError();
            setAdding(true);
          }}
        >
          <Plus />
          {t("Add a route")}
        </Button>
      </ListToolbar>
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Name / destination")}</TableHead>
              <TableHead>{t("Channel account")}</TableHead>
              <TableHead>{t("Agent / thread")}</TableHead>
              <TableHead>{t("Allowed users")}</TableHead>
              <TableHead>{t("Reply policy")}</TableHead>
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
                    <p className="text-xs text-muted-foreground">
                      {message(record.conversation_kind)} · {record.conversation_id}
                    </p>
                  </TableCell>
                  <TableCell>{connectionName(record.channel_id)}</TableCell>
                  <TableCell>
                    <p>{connectionName(record.host_id)}</p>
                    <p className="text-xs">{record.agent_id}</p>
                    <p className="text-xs text-muted-foreground">
                      {record.thread_id ?? t("Created on first message")}
                    </p>
                  </TableCell>
                  <TableCell>{record.allowed_user_ids.join(", ")}</TableCell>
                  <TableCell>
                    <Badge variant="outline">
                      {record.auto_reply ? t("Automatic private reply") : t("Approval required")}
                    </Badge>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {record.reply_source === "output" ? t("Final run output") : t("MCP tools")}
                    </p>
                  </TableCell>
                  <TableCell>
                    <Switch
                      aria-label={t("Enable {name}", { name: record.name })}
                      checked={record.enabled}
                      disabled={busy}
                      onCheckedChange={(enabled) =>
                        void action(`routes/${record.id}`, { enabled }, "PATCH")
                      }
                    />
                  </TableCell>
                  <TableCell>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => {
                          clearError();
                          setSelected(record.id);
                        }}
                      >
                        {t("Edit access")}
                      </Button>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={busy}
                        onClick={() => void action(`routes/${record.id}/token`)}
                      >
                        {t("Rotate token")}
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </TableFrame>
      <ManagementDialog
        open={adding}
        onOpenChange={setAdding}
        title={t("Add a route")}
        description={t(
          "Authorize a conversation and its senders, then choose the agent that should receive their messages.",
        )}
        busy={busy}
        error={error}
        wide
      >
        <RouteForm
          snapshot={snapshot}
          action={action}
          busy={busy}
          onSaved={() => {
            setAdding(false);
            setQuery("");
          }}
        />
      </ManagementDialog>
      <ManagementDialog
        open={!!route}
        onOpenChange={(open) => !open && setSelected(null)}
        title={route?.name ?? t("Access & MCP binding")}
        description={t(
          "Update sender IDs or attach a dedicated MCP connection. After rotating a route token, update its a13n bearer credential.",
        )}
        busy={busy}
        error={error}
      >
        {route && (
          <RouteAccess
            route={route}
            action={action}
            busy={busy}
            onSaved={() => setSelected(null)}
          />
        )}
      </ManagementDialog>
    </div>
  );
}

function RouteForm({
  snapshot,
  action,
  busy,
  onSaved,
}: {
  snapshot: Snapshot;
  action: Action;
  busy: boolean;
  onSaved: () => void;
}) {
  const { t } = useLocale();
  const channels = snapshot.connections.filter((c) => c.kind === "onebots");
  const hosts = snapshot.connections.filter((c) => c.kind === "a13n");
  const [channel, setChannel] = useState(channels[0]?.id ?? "");
  const [host, setHost] = useState(hosts[0]?.id ?? "");
  const [kind, setKind] = useState("private");
  const [source, setSource] = useState("output");
  const [automatic, setAutomatic] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const conversation = field(data, "conversation_id");
    if (
      await action("routes", {
        name: field(data, "name"),
        channel_id: channel,
        host_id: host,
        conversation_kind: kind,
        conversation_id: conversation,
        allowed_user_ids:
          kind === "private"
            ? [conversation]
            : field(data, "users")
                .split(/[\s,]+/)
                .filter(Boolean),
        agent_id: field(data, "agent_id"),
        auto_reply: kind === "private" && automatic,
        reply_source: source,
        mcp_connection_id: field(data, "mcp_connection_id") || null,
      })
    ) {
      onSaved();
    }
  }
  return (
    <form className="grid gap-4 md:grid-cols-2" onSubmit={submit}>
      <FormField id="route-name" label={t("Name")}>
        <Input id="route-name" name="name" required maxLength={80} placeholder={t("Team chat")} />
      </FormField>
      <FormField id="conversation-kind" label={t("Conversation type")}>
        <Choice
          id="conversation-kind"
          value={kind}
          onChange={setKind}
          items={[
            ["private", t("Private chat")],
            ["group", t("Group chat")],
          ]}
        />
      </FormField>
      <FormField id="route-channel" label={t("Channel account")}>
        <Choice
          id="route-channel"
          value={channel}
          onChange={setChannel}
          items={channels.map((c) => [c.id, c.name])}
        />
      </FormField>
      <FormField id="route-host" label={t("Agent host")}>
        <Choice
          id="route-host"
          value={host}
          onChange={setHost}
          items={hosts.map((c) => [c.id, c.name])}
        />
      </FormField>
      <FormField id="conversation-id" label={kind === "group" ? t("Group ID") : t("User ID")}>
        <Input id="conversation-id" name="conversation_id" required />
      </FormField>
      <FormField id="agent-id" label={t("a13n agent ID")}>
        <Input id="agent-id" name="agent_id" required placeholder="agt_…" />
      </FormField>
      {kind === "group" && (
        <FormField id="allowed-users" label={t("Allowed sender IDs")}>
          <Input id="allowed-users" name="users" required placeholder="12345, 67890" />
          <p className="text-xs text-muted-foreground">
            {t("Use platform user IDs, separated by commas. Display names do not grant access.")}
          </p>
        </FormField>
      )}
      <FormField id="reply-source" label={t("Reply source")}>
        <Choice
          id="reply-source"
          value={source}
          onChange={setSource}
          items={[
            ["output", t("Final run output")],
            ["mcp", t("MCP send_message tool")],
          ]}
        />
      </FormField>
      <FormField id="mcp-connection" label={t("a13n MCP connection ID (optional)")}>
        <Input id="mcp-connection" name="mcp_connection_id" required={source === "mcp"} />
        <p className="text-xs text-muted-foreground">
          {t(
            "Use a dedicated MCP connection for this route. You can bind it after creating the route.",
          )}
        </p>
      </FormField>
      {kind === "private" && (
        <div className="flex items-center gap-2">
          <Switch id="automatic" checked={automatic} onCheckedChange={setAutomatic} />
          <Label htmlFor="automatic">{t("Allow automatic replies to this user")}</Label>
        </div>
      )}
      {kind === "group" && (
        <p className="text-sm text-muted-foreground">
          {t("Group replies require administrator approval.")}
        </p>
      )}
      <Button disabled={busy || !channel || !host} type="submit">
        {busy ? t("Saving…") : t("Create route")}
      </Button>
    </form>
  );
}

function RouteAccess({
  route,
  action,
  busy,
  onSaved,
}: {
  route: import("@/api").Route;
  action: Action;
  busy: boolean;
  onSaved: () => void;
}) {
  const { t } = useLocale();
  const [source, setSource] = useState(route.reply_source);
  return (
    <form
      className="grid gap-4 md:grid-cols-2"
      onSubmit={async (event) => {
        event.preventDefault();
        const data = new FormData(event.currentTarget);
        if (
          await action(
            `routes/${route.id}`,
            {
              allowed_user_ids: field(data, "users")
                .split(/[\s,]+/)
                .filter(Boolean),
              reply_source: source,
              mcp_connection_id: field(data, "mcp_connection_id") || null,
            },
            "PATCH",
          )
        )
          onSaved();
      }}
    >
      <FormField
        id={`users-${route.id}`}
        label={t("{name} · allowed sender IDs", { name: route.name })}
      >
        <Input
          id={`users-${route.id}`}
          name="users"
          defaultValue={route.allowed_user_ids.join(", ")}
          required
          readOnly={route.conversation_kind === "private"}
        />
      </FormField>
      <FormField id={`mcp-${route.id}`} label={t("a13n MCP connection ID")}>
        <Input
          id={`mcp-${route.id}`}
          name="mcp_connection_id"
          defaultValue={route.mcp_connection_id ?? ""}
          required={source === "mcp"}
        />
      </FormField>
      <FormField id={`source-${route.id}`} label={t("Reply source")}>
        <Choice
          id={`source-${route.id}`}
          value={source}
          onChange={(value) => setSource(value as "output" | "mcp")}
          items={[
            ["output", t("Final run output")],
            ["mcp", t("MCP send_message tool")],
          ]}
        />
      </FormField>
      <Button type="submit" variant="outline" disabled={busy}>
        {t("Save access & binding")}
      </Button>
    </form>
  );
}
