import { useLocale } from "@/i18n";
import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { DialogFooter } from "@/components/ui/dialog";
import { DeleteResource } from "@/components/delete-resource";
import { Switch } from "@/components/ui/switch";
import { FormField, Choice, field, type Action } from "@/components/form-controls";
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
import { isHost } from "@/hosts";
import type { Snapshot } from "@/api";
import { eventAddress } from "@/conversations";

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
  const rows = snapshot.routes
    .filter((route) => !route.parent_route_id)
    .filter((route) =>
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
                    <p className="text-xs text-muted-foreground">
                      {message(record.conversation_kind)}:{" "}
                      {record.conversation_id === "*"
                        ? t("All conversations")
                        : record.conversation_id}
                    </p>
                  </TableCell>
                  <TableCell>{connectionName(record.channel_id)}</TableCell>
                  <TableCell>
                    <p>{connectionName(record.host_id)}</p>
                    <p className="text-xs">{record.agent_id}</p>
                    <p className="text-xs text-muted-foreground">
                      {record.conversation_id === "*"
                        ? t("Separate thread per conversation")
                        : (record.thread_id ??
                          (snapshot.connections.find((c) => c.id === record.host_id)?.kind === "mcp"
                            ? t("Client pulls through MCP")
                            : t("Created on first message")))}
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
                    <p className="mt-1 text-xs text-muted-foreground">
                      {record.access_mode === "full" ? t("Full access") : t("Restricted access")}
                    </p>
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{record.enabled ? t("Enabled") : t("Disabled")}</Badge>
                  </TableCell>
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
                      {t("Edit")}
                    </Button>
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
        description={
          snapshot.connections.find((c) => c.id === route?.host_id)?.kind === "mcp"
            ? t("Update allowed sender IDs for this client’s conversation.")
            : t(
                "Update sender IDs or attach a dedicated MCP connection. After rotating a route token, update its a13n bearer credential.",
              )
        }

        busy={busy}
        error={error}
      >
        {route && (
          <RouteAccess
            mcpOnly={snapshot.connections.find((c) => c.id === route.host_id)?.kind === "mcp"}
            route={route}
            action={action}
            busy={busy}
            onSaved={() => setSelected(null)}
            error={error}
            clearError={clearError}
          />
        )}
      </ManagementDialog>
    </div>
  );
}

export type RouteSource = {
  channel_id: string;
  conversation_kind: "private" | "group";
  conversation_id: string;
};

export function RouteForm({
  initial,
  snapshot,
  action,
  busy,
  onSaved,
}: {
  initial?: RouteSource;
  snapshot: Snapshot;
  action: Action;
  busy: boolean;
  onSaved: () => void;
}) {
  const { t } = useLocale();
  const channels = snapshot.connections.filter((c) => !isHost(c));
  const hosts = snapshot.connections.filter(isHost);
  const [channel, setChannel] = useState(initial?.channel_id ?? channels[0]?.id ?? "");
  const [host, setHost] = useState(hosts[0]?.id ?? "");
  const mcpOnly = hosts.find((c) => c.id === host)?.kind === "mcp";
  const [kind, setKind] = useState(initial?.conversation_kind ?? "private");
  const [scope, setScope] = useState("specific");
  const [conversation, setConversation] = useState(initial?.conversation_id ?? "");
  const [senderPolicy, setSenderPolicy] = useState("allowlist");
  const [accessMode, setAccessMode] = useState("restricted");
  const currentChannel = channels.find((item) => item.id === channel);
  const privateOnly = currentChannel?.kind === "weixin";
  const [source, setSource] = useState("output");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const target = scope === "all" ? "*" : conversation;
    if (
      await action("routes", {
        name: field(data, "name"),
        channel_id: channel,
        host_id: host,
        conversation_kind: kind,
        conversation_id: target,
        sender_policy: senderPolicy,
        access_mode: accessMode,
        allowed_user_ids:
          senderPolicy === "any"
            ? []
            : kind === "private" && scope === "specific"
              ? [target]
              : field(data, "users")
                  .split(/[\s,]+/)
                  .filter(Boolean),
        agent_id: mcpOnly ? null : field(data, "agent_id"),
        reply_source: mcpOnly ? "mcp" : source,
        mcp_connection_id:
          mcpOnly || scope === "all" ? null : field(data, "mcp_connection_id") || null,
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
          onChange={(value) => setKind(value as "private" | "group")}
          items={[
            ["private", t("Private chat")],
            ...(!privateOnly ? [["group", t("Group chat")] as [string, string]] : []),
          ]}
        />
      </FormField>
      <FormField id="route-channel" label={t("Channel account")}>
        <Choice
          id="route-channel"
          value={channel}
          onChange={(value) => {
            setChannel(value);
            if (channels.find((item) => item.id === value)?.kind === "weixin") setKind("private");
          }}
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
      <FormField id="conversation-scope" label={t("Conversation scope")}>
        <Choice
          id="conversation-scope"
          value={scope}
          onChange={(value) => {
            setScope(value);
            if (value === "all") {
              setSenderPolicy("any");
              setSource("output");
            }
          }}
          items={[
            ["specific", t("Specific conversation")],
            ["all", t("All conversations")],
          ]}
        />
        {scope === "all" && (
          <p className="text-xs text-muted-foreground">
            {t("New conversations get separate threads. Specific routes take priority.")}
          </p>
        )}
      </FormField>
      {scope === "specific" && (
        <FormField id="conversation-id" label={kind === "group" ? t("Group ID") : t("User ID")}>
          <Input
            id="conversation-id"
            name="conversation_id"
            required
            value={conversation}
            onChange={(event) => setConversation(event.target.value)}
          />
          <ObservedConversations
            snapshot={snapshot}
            channel={channel}
            kind={kind}
            onSelect={setConversation}
          />
        </FormField>
      )}
      <FormField id="sender-policy" label={t("Sender access")}>
        <Choice
          id="sender-policy"
          value={senderPolicy}
          onChange={setSenderPolicy}
          items={[
            ["allowlist", t("Allowed sender IDs")],
            ["any", t("Any sender")],
          ]}
        />
      </FormField>
      <AccessMode value={accessMode} onChange={setAccessMode} />
      {!mcpOnly && (
        <FormField id="agent-id" label={t("a13n agent ID")}>
          <Input id="agent-id" name="agent_id" required placeholder="ap_…" />
        </FormField>
      )}
      {senderPolicy === "allowlist" && (kind === "group" || scope === "all") && (
        <FormField id="allowed-users" label={t("Allowed sender IDs")}>
          <Input id="allowed-users" name="users" required placeholder="12345, 67890" />
          <p className="text-xs text-muted-foreground">
            {t("Use platform user IDs, separated by commas. Display names do not grant access.")}
          </p>
        </FormField>
      )}
      {!mcpOnly && (
        <>
          <FormField id="reply-source" label={t("Reply source")}>
            <Choice
              id="reply-source"
              value={source}
              onChange={setSource}
              items={[
                ["output", t("Final run output")],
                ...(scope !== "all"
                  ? [["mcp", t("MCP send_message tool")] as [string, string]]
                  : []),
              ]}
            />
          </FormField>
          {scope !== "all" && (
            <FormField id="mcp-connection" label={t("a13n MCP connection ID (optional)")}>
              <Input id="mcp-connection" name="mcp_connection_id" required={source === "mcp"} />
              <p className="text-xs text-muted-foreground">
                {t(
                  "Use a dedicated MCP connection for this route. You can bind it after creating the route.",
                )}
              </p>
            </FormField>
          )}
        </>
      )}
      {currentChannel?.capabilities?.receive === false && (
        <p className="text-sm text-muted-foreground">
          {t(
            "This channel sends only. It cannot receive messages or automatically start an agent run.",
          )}
        </p>
      )}
      <Button disabled={busy || !channel || !host} type="submit">
        {busy ? t("Saving…") : t("Create route")}
      </Button>
    </form>
  );
}

function RouteAccess({
  mcpOnly,
  route,
  action,
  busy,
  onSaved,
  error,
  clearError,
}: {
  mcpOnly: boolean;
  route: import("@/api").Route;
  action: Action;
  busy: boolean;
  onSaved: () => void;
  error: string;
  clearError: () => void;
}) {
  const { t } = useLocale();
  const [source, setSource] = useState(route.reply_source);
  const [senderPolicy, setSenderPolicy] = useState(route.sender_policy);
  const [accessMode, setAccessMode] = useState(route.access_mode);
  const [enabled, setEnabled] = useState(route.enabled);
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
              enabled,
              sender_policy: senderPolicy,
              access_mode: accessMode,
              allowed_user_ids:
                senderPolicy === "any"
                  ? []
                  : route.conversation_kind === "private" && route.conversation_id !== "*"
                    ? [route.conversation_id]
                    : field(data, "users")
                        .split(/[\s,]+/)
                        .filter(Boolean),
              reply_source: mcpOnly ? "mcp" : source,
              mcp_connection_id:
                mcpOnly || route.conversation_id === "*"
                  ? null
                  : field(data, "mcp_connection_id") || null,
            },
            "PATCH",
          )
        )
          onSaved();
      }}
    >
      <FormField id="edit-sender-policy" label={t("Sender access")}>
        <Choice
          id="edit-sender-policy"
          value={senderPolicy}
          onChange={(value) => setSenderPolicy(value as "allowlist" | "any")}
          items={[
            ["allowlist", t("Allowed sender IDs")],
            ["any", t("Any sender")],
          ]}
        />
      </FormField>
      <AccessMode
        value={accessMode}
        onChange={(value) => setAccessMode(value as "restricted" | "full")}
      />
      {senderPolicy === "allowlist" && (
        <FormField
          id={`users-${route.id}`}
          label={t("{name}: allowed sender IDs", { name: route.name })}
        >
          <Input
            id={`users-${route.id}`}
            name="users"
            defaultValue={
              route.conversation_kind === "private" && route.conversation_id !== "*"
                ? route.conversation_id
                : route.allowed_user_ids.join(", ")
            }
            required
            readOnly={route.conversation_kind === "private" && route.conversation_id !== "*"}
          />
        </FormField>
      )}
      {!mcpOnly && route.conversation_id !== "*" && (
        <>
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
        </>
      )}
      <FormField id="route-enabled" label={t("Enable route")}>
        <Switch id="route-enabled" checked={enabled} onCheckedChange={setEnabled} disabled={busy} />
      </FormField>
      <DialogFooter className="md:col-span-2">
        <DeleteResource
          label={t("Delete route")}
          title={t("Delete route {name}?", { name: route.name })}
          description={t(
            "This removes the route and its conversation bindings, revokes its tokens, and blocks pending work. Message and reply history is retained. This cannot be undone.",
          )}
          path={`routes/${route.id}`}
          action={action}
          busy={busy}
          error={error}
          clearError={clearError}
          onDeleted={onSaved}
        />
        <Button
          type="button"
          variant="outline"
          disabled={busy}
          onClick={async () => {
            if (await action(`routes/${route.id}/token`)) onSaved();
          }}
        >
          {t("Rotate token")}
        </Button>
        <Button type="submit" disabled={busy}>
          {t("Save access & binding")}
        </Button>
      </DialogFooter>
    </form>
  );
}

function AccessMode({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { t } = useLocale();
  return (
    <FormField id="access-mode" label={t("Permission mode")}>
      <Choice
        id="access-mode"
        value={value}
        onChange={onChange}
        items={[
          ["restricted", t("Restricted access")],
          ["full", t("Full access")],
        ]}
      />
      <p className="text-xs text-muted-foreground">
        {value === "full"
          ? t(
              "Use the agent's configured tools. Applies only to this route, without gateway administrator access.",
            )
          : t("Limit connection tools to those assigned to this route.")}
      </p>
    </FormField>
  );
}

function ObservedConversations({
  snapshot,
  channel,
  kind,
  onSelect,
}: {
  snapshot: Snapshot;
  channel: string;
  kind: string;
  onSelect: (value: string) => void;
}) {
  const { t } = useLocale();
  const conversations = [
    ...new Set(
      snapshot.events.flatMap((event) => {
        const address = eventAddress(snapshot, event);
        return address?.account_id === channel && address.conversation_id.startsWith(kind + ":")
          ? [address.conversation_id.slice(kind.length + 1)]
          : [];
      }),
    ),
  ];
  if (!conversations.length) return null;
  return (
    <FormField id="observed-conversation" label={t("Choose a received conversation")}>
      <Choice
        id="observed-conversation"
        value=""
        onChange={onSelect}
        placeholder={t("Choose a received conversation")}
        items={conversations.map((id) => [id, id])}
      />
    </FormField>
  );
}
