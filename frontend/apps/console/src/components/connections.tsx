import { useLocale } from "@/i18n";
import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField, field, type Action } from "@/components/form-controls";
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

export function Connections({
  connections,
  kind,
  action,
  busy,
  error,
  clearError,
}: {
  connections: Connection[];
  kind: Connection["kind"];
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
}) {
  const { t } = useLocale();
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const isChannel = kind === "onebots";
  const records = connections.filter((connection) => connection.kind === kind);
  const rows = records.filter((connection) =>
    matchesQuery(query, [
      connection.name,
      connection.id,
      connection.api_url,
      connection.self_id,
      isChannel ? "OneBots" : "a13n",
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
              <TableHead>{t("API base URL")}</TableHead>
              {isChannel && <TableHead>{t("Bot self ID")}</TableHead>}
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
                  <TableCell>{isChannel ? "OneBots" : "a13n"}</TableCell>
                  <TableCell>
                    <p className="max-w-sm truncate" title={record.api_url}>
                      {record.api_url}
                    </p>
                  </TableCell>
                  {isChannel && <TableCell>{record.self_id}</TableCell>}
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
            ? t("Use the account’s OneBot v11 HTTP API and signing secret.")
            : t("Use an a13n workspace API key with permission to run the selected agent.")
        }
        busy={busy}
        error={error}
      >
        <ConnectionForm
          kind={kind}
          action={action}
          busy={busy}
          onSaved={() => {
            setAdding(false);
            setQuery("");
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
            connection={connection}
            action={action}
            busy={busy}
            onSaved={() => setSelected(null)}
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
  kind: Connection["kind"];
  action: Action;
  busy: boolean;
  onSaved: () => void;
}) {
  const { t } = useLocale();
  const isChannel = kind === "onebots";
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    if (
      await action("connections", {
        name: field(data, "name"),
        kind,
        api_url: field(data, "api_url"),
        token: field(data, "token"),
        ...(isChannel
          ? { self_id: field(data, "self_id"), webhook_secret: field(data, "webhook_secret") }
          : {}),
      })
    )
      onSaved();
  }
  return (
    <form className="grid gap-4" onSubmit={submit}>
      <FormField id="connection-name" label={t("Name")}>
        <Input
          id="connection-name"
          name="name"
          required
          maxLength={80}
          placeholder={isChannel ? t("QQ bot") : t("a13n workspace")}
        />
      </FormField>
      <FormField id="api-url" label={t("API base URL")}>
        <Input
          id="api-url"
          name="api_url"
          type="url"
          required
          placeholder={
            isChannel
              ? "https://onebots.example/qq/ACCOUNT/onebot/v11"
              : "https://a13n.example/api/v1"
          }
        />
      </FormField>
      <FormField
        id="api-token"
        label={isChannel ? t("OneBot access token") : t("Workspace API key")}
      >
        <Input id="api-token" name="token" type="password" required autoComplete="off" />
      </FormField>
      {isChannel && (
        <>
          <FormField id="self-id" label={t("Bot self ID")}>
            <Input id="self-id" name="self_id" required />
          </FormField>
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
        </>
      )}
      <Button disabled={busy} type="submit">
        {busy ? t("Saving…") : t("Save connection")}
      </Button>
    </form>
  );
}

function ConnectionDetails({
  connection,
  action,
  busy,
  onSaved,
}: {
  connection: Connection;
  action: Action;
  busy: boolean;
  onSaved: () => void;
}) {
  const { t } = useLocale();
  return (
    <div className="grid gap-4">
      <FormField id="connection-url" label={t("API base URL")}>
        <Input id="connection-url" readOnly value={connection.api_url} />
      </FormField>
      {connection.kind === "onebots" && (
        <FormField id="connection-hook" label={t("Reverse HTTP webhook")}>
          <Input
            id="connection-hook"
            readOnly
            value={`${location.origin}/api/v1/hooks/onebots/${connection.id}`}
          />
        </FormField>
      )}
      <form
        className="grid gap-4"
        onSubmit={async (event) => {
          event.preventDefault();
          const data = new FormData(event.currentTarget);
          const token = field(data, "token");
          const secret = field(data, "secret");
          if (
            await action(
              `connections/${connection.id}`,
              { ...(token ? { token } : {}), ...(secret ? { webhook_secret: secret } : {}) },
              "PATCH",
            )
          )
            onSaved();
        }}
      >
        <FormField id="new-token" label={t("New API token (optional)")}>
          <Input id="new-token" name="token" type="password" autoComplete="off" />
        </FormField>
        {connection.kind === "onebots" && (
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
        <Button type="submit" disabled={busy}>
          {busy ? t("Saving…") : t("Save credentials")}
        </Button>
      </form>
    </div>
  );
}
