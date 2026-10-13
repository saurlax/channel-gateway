import { useLocale } from "@/i18n";
import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { DialogFooter } from "@/components/ui/dialog";
import { DeleteResource } from "@/components/delete-resource";
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
import type { Connection } from "@/api";
import { channelTypes, channelType, channelEndpoint } from "@/channels";
import { isHost } from "@/hosts";
import { WeixinLogin } from "@/components/weixin-login";

export function Connections({
  connections,
  kind,
  action,
  busy,
  error,
  clearError,
  csrfToken,
  refresh,
}: {
  connections: Connection[];
  kind: "onebots" | "a13n";
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
  csrfToken: string;
  refresh: () => Promise<void>;
}) {
  const { t, message } = useLocale();
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const isChannel = kind === "onebots";
  const records = connections.filter((connection) =>
    isChannel ? !isHost(connection) : isHost(connection),
  );
  const rows = records.filter((connection) =>
    matchesQuery(query, [
      connection.name,
      connection.id,
      !isChannel ? channelEndpoint(connection) : undefined,
      connection.self_id,
      connection.app_id,
      connection.state,
      channelType(connection) && t(channelType(connection)!.label),
      isHost(connection) ? connection.kind : undefined,
    ]),
  );
  const connection = records.find((record) => record.id === selected);
  const addLabel = isChannel ? t("Add channel") : t("Add agent host");
  return (
    <div className="grid grid-cols-1 gap-4">
      <ListToolbar
        query={query}
        onQueryChange={setQuery}
        label={isChannel ? t("Search channels…") : t("Search agent hosts…")}
      >
        <Button
          onClick={() => {
            clearError();
            setAdding(true);
          }}
          disabled={busy}
        >
          <Plus />
          {addLabel}
        </Button>
      </ListToolbar>
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Name")}</TableHead>
              <TableHead>{t("Service")}</TableHead>
              {!isChannel && <TableHead>{t("API base URL")}</TableHead>}
              {isChannel && (
                <>
                  <TableHead>{t("Account / application ID")}</TableHead>
                  <TableHead>{t("Status")}</TableHead>
                </>
              )}
              <TableHead>{t("Action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <EmptyRow columns={isChannel ? 5 : 4} filtered={!!query.trim()}>
                {isChannel ? t("No channels configured.") : t("No agent hosts configured.")}
              </EmptyRow>
            ) : (
              rows.map((record) => (
                <TableRow key={record.id}>
                  <TableCell>
                    <p className="font-medium">{record.name}</p>
                    <p className="text-xs text-muted-foreground">{record.id}</p>
                  </TableCell>
                  <TableCell className="whitespace-normal">
                    {isChannel
                      ? t(channelType(record)!.label)
                      : record.kind === "a13n"
                        ? t("a13n API + MCP")
                        : t("Generic MCP")}
                    {record.kind === "qq" && (
                      <p className="text-xs text-muted-foreground">
                        {record.transport === "webhook" ? t("HTTP callback") : "WebSocket"}
                      </p>
                    )}
                  </TableCell>
                  {!isChannel && (
                    <TableCell>
                      <p
                        className="max-w-48 truncate"
                        title={channelEndpoint(record) || t("Client pulls through MCP")}
                      >
                        {channelEndpoint(record) || t("Client pulls through MCP")}
                      </p>
                    </TableCell>
                  )}
                  {isChannel && (
                    <>
                      <TableCell>{record.self_id || record.app_id || t("Not paired")}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{message(record.state ?? "inactive")}</Badge>
                        {record.error && (
                          <p className="mt-1 max-w-xs whitespace-normal text-xs text-muted-foreground">
                            {message(record.error)}
                          </p>
                        )}
                      </TableCell>
                    </>
                  )}
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
                      {t("Manage")}
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
        title={addLabel}
        description={
          isChannel
            ? t("Choose a protocol and configure one bot account per channel.")
            : t("Choose API + MCP for a13n, or generic MCP for clients such as Codex.")
        }
        busy={busy}
        error={error}
      >
        <ConnectionForm
          key={adding ? "open" : "closed"}
          kind={kind}
          action={action}
          busy={busy}
          onSaved={(id, pair) => {
            setAdding(false);
            setQuery("");
            if (pair && id) setSelected(id);
          }}
        />
      </ManagementDialog>
      <ManagementDialog
        open={!!connection}
        onOpenChange={(open) => !open && setSelected(null)}
        title={connection?.name ?? t("Manage")}
        description={t("Connection details and credentials")}
        busy={busy}
        error={error}
      >
        {connection && (
          <ConnectionDetails
            key={connection.id}
            connection={connection}
            action={action}
            busy={busy}
            onSaved={() => setSelected(null)}
            csrfToken={csrfToken}
            refresh={refresh}
            error={error}
            clearError={clearError}
          />
        )}
      </ManagementDialog>
    </div>
  );
}

function ConnectionForm({
  kind,
  action,
  busy,
  onSaved,
}: {
  kind: "onebots" | "a13n";
  action: Action;
  busy: boolean;
  onSaved: (id?: string, pair?: boolean) => void;
}) {
  const { t } = useLocale();
  const isChannel = kind === "onebots";
  const [typeId, setTypeId] = useState<string>("http_reverse");
  const [hostType, setHostType] = useState("a13n");
  const [qqMode, setQQMode] = useState("websocket");
  const type = channelTypes.find((item) => item.id === typeId)!;
  const onebot = isChannel && type.kind === "onebots";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const base = { name: field(data, "name") };
    const body = !isChannel
      ? {
          ...base,
          kind: hostType,
          ...(hostType === "a13n"
            ? { api_url: field(data, "api_url"), token: field(data, "token") }
            : {}),
        }
      : {
          ...base,
          kind: type.kind,
          transport: type.kind === "qq" ? qqMode : type.transport,
          ...(onebot
            ? {
                token: field(data, "token"),
                self_id: field(data, "self_id"),
                ...(typeId !== "ws_reverse" ? { api_url: field(data, "api_url") } : {}),
                ...(typeId === "http_reverse"
                  ? { webhook_secret: field(data, "webhook_secret") }
                  : {}),
              }
            : {}),
          ...(type.kind === "qq"
            ? { app_id: field(data, "app_id"), app_secret: field(data, "app_secret") }
            : {}),
        };
    const saved = await action("connections", body);
    if (saved) onSaved(saved.id, isChannel && type.kind === "weixin");
  }
  return (
    <form className="grid gap-4" onSubmit={submit}>
      {isChannel && (
        <FormField id="channel-type" label={t("Channel type")}>
          <Choice
            id="channel-type"
            value={typeId}
            onChange={setTypeId}
            items={channelTypes.map((item) => [item.id, t(item.label)])}
          />
          <p className="text-xs text-muted-foreground">{t(type.help)}</p>
        </FormField>
      )}
      {!isChannel && (
        <FormField id="host-type" label={t("Host type")}>
          <Choice
            id="host-type"
            value={hostType}
            onChange={setHostType}
            items={[
              ["a13n", t("a13n API + MCP")],
              ["mcp", t("Generic MCP")],
            ]}
          />
          <p className="text-xs text-muted-foreground">
            {hostType === "a13n"
              ? t("Deliver messages through the API and optionally attach MCP tools.")
              : t(
                  "The client reads authorized conversations through MCP. This does not wake an idle client.",
                )}
          </p>
        </FormField>
      )}
      <FormField id="connection-name" label={t("Name")}>
        <Input
          id="connection-name"
          name="name"
          required
          maxLength={80}
          placeholder={
            isChannel ? t("Bot account") : hostType === "a13n" ? t("a13n workspace") : "Codex"
          }
        />
      </FormField>
      <div key={isChannel ? typeId : hostType} className="grid gap-4">
        {((!isChannel && hostType === "a13n") || (onebot && typeId !== "ws_reverse")) && (
          <FormField
            id="api-url"
            label={typeId === "ws" && isChannel ? t("Account WebSocket URL") : t("API base URL")}
          >
            <Input
              id="api-url"
              name="api_url"
              type="url"
              required
              placeholder={
                !isChannel
                  ? "https://a13n.example/api/v1"
                  : typeId === "ws"
                    ? "wss://onebots.example/qq/ACCOUNT/onebot/v11"
                    : "https://onebots.example/qq/ACCOUNT/onebot/v11"
              }
            />
          </FormField>
        )}
        {((!isChannel && hostType === "a13n") || onebot) && (
          <FormField
            id="api-token"
            label={isChannel ? t("OneBot access token") : t("Workspace API key")}
          >
            <Input id="api-token" name="token" type="password" required autoComplete="off" />
          </FormField>
        )}
        {onebot && (
          <FormField id="self-id" label={t("Bot self ID")}>
            <Input id="self-id" name="self_id" required maxLength={128} />
          </FormField>
        )}
        {onebot && typeId === "http_reverse" && (
          <FormField id="signing-secret" label={t("Webhook signing secret")}>
            <Input
              id="signing-secret"
              name="webhook_secret"
              type="password"
              required
              minLength={16}
              autoComplete="off"
            />
            <p className="text-xs text-muted-foreground">
              {t("Use the same secret in OneBots. Configure its reverse HTTP URL after saving.")}
            </p>
          </FormField>
        )}
        {isChannel && type.kind === "qq" && (
          <>
            <FormField id="qq-mode" label={t("Receive mode")}>
              <Choice
                id="qq-mode"
                value={qqMode}
                onChange={setQQMode}
                items={[
                  ["websocket", "WebSocket"],
                  ["webhook", t("HTTP callback")],
                ]}
              />
            </FormField>
            <FormField id="qq-app-id" label="AppID">
              <Input id="qq-app-id" name="app_id" required maxLength={128} />
            </FormField>
            <FormField id="qq-app-secret" label="AppSecret">
              <Input
                id="qq-app-secret"
                name="app_secret"
                required
                type="password"
                autoComplete="off"
              />
            </FormField>
          </>
        )}
      </div>
      <Button disabled={busy} type="submit">
        {busy
          ? t("Saving…")
          : isChannel && type.kind === "weixin"
            ? t("Save & pair")
            : t("Save connection")}
      </Button>
    </form>
  );
}

function ConnectionDetails({
  connection,
  action,
  busy,
  onSaved,
  csrfToken,
  refresh,
  error,
  clearError,
}: {
  connection: Connection;
  action: Action;
  busy: boolean;
  onSaved: () => void;
  csrfToken: string;
  refresh: () => Promise<void>;
  error: string;
  clearError: () => void;
}) {
  const { t } = useLocale();
  const reverseHTTP =
    connection.kind === "onebots" &&
    (!connection.transport || connection.transport === "http_reverse");
  const qq = connection.kind === "qq";
  const type = channelType(connection);
  return (
    <div className="grid gap-4">
      {type && <p className="text-sm text-muted-foreground">{t(type.help)}</p>}
      {(connection.kind === "onebots" || connection.kind === "a13n") && (
        <FormField
          id="connection-url"
          label={
            connection.transport === "ws_reverse"
              ? t("Gateway WebSocket URL")
              : connection.transport === "ws"
                ? t("Account WebSocket URL")
                : t("API base URL")
          }
        >
          <Input id="connection-url" readOnly value={channelEndpoint(connection)} />
        </FormField>
      )}
      {(reverseHTTP || (qq && connection.transport === "webhook")) && (
        <FormField id="connection-hook" label={t("Reverse HTTP webhook")}>
          <Input
            id="connection-hook"
            readOnly
            value={`${location.origin}/api/v1/hooks/${connection.kind}/${connection.id}`}
          />
        </FormField>
      )}
      {isHost(connection) && (
        <>
          <FormField id="host-mcp-url" label={t("MCP URL")}>
            <Input id="host-mcp-url" readOnly value={`${location.origin}/mcp/`} />
          </FormField>
          <p className="text-xs text-muted-foreground">
            {t(
              "Use this host’s MCP key as the bearer credential. It grants access to all enabled routes assigned to this host.",
            )}
          </p>
        </>
      )}
      {connection.kind === "mcp" ? null : connection.kind === "weixin" ? (
        <WeixinLogin connection={connection} csrfToken={csrfToken} refresh={refresh} />
      ) : (
        <form
          id="connection-credentials"
          className="grid gap-4"
          onSubmit={async (event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            const value = field(data, "token");
            const secret = field(data, "secret");
            if (
              await action(
                `connections/${connection.id}`,
                {
                  ...(value ? { [qq ? "app_secret" : "token"]: value } : {}),
                  ...(secret ? { webhook_secret: secret } : {}),
                },
                "PATCH",
              )
            )
              onSaved();
          }}
        >
          <FormField
            id="new-token"
            label={qq ? t("New AppSecret (optional)") : t("New API token (optional)")}
          >
            <Input id="new-token" name="token" type="password" autoComplete="off" />
          </FormField>
          {reverseHTTP && (
            <FormField id="new-secret" label={t("New signing secret (optional)")}>
              <Input
                id="new-secret"
                name="secret"
                type="password"
                minLength={16}
                autoComplete="off"
              />
            </FormField>
          )}
        </form>
      )}
      <DialogFooter>
        <DeleteResource
          label={isHost(connection) ? t("Delete agent host") : t("Delete channel")}
          title={
            isHost(connection)
              ? t("Delete agent host {name}?", { name: connection.name })
              : t("Delete channel {name}?", { name: connection.name })
          }
          description={
            isHost(connection)
              ? t(
                  "This removes the host and its credentials, revokes its MCP key, disables assigned routes, and blocks pending work. Message and reply history is retained. This cannot be undone.",
                )
              : t(
                  "This removes the channel and its credentials, disables its routes, and blocks pending work. Message and reply history is retained. This cannot be undone.",
                )
          }
          path={`connections/${connection.id}`}
          action={action}
          busy={busy}
          error={error}
          clearError={clearError}
          onDeleted={onSaved}
        />
        {isHost(connection) && (
          <Button
            type="button"
            variant="outline"
            disabled={busy}
            onClick={async () => {
              if (await action(`connections/${connection.id}/mcp-key`)) onSaved();
            }}
          >
            {t("Rotate MCP key")}
          </Button>
        )}
        {connection.kind !== "mcp" && connection.kind !== "weixin" && (
          <Button type="submit" form="connection-credentials" disabled={busy}>
            {busy ? t("Saving…") : t("Save credentials")}
          </Button>
        )}
      </DialogFooter>
    </div>
  );
}
