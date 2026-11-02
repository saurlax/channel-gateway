import { useState, type FormEvent } from "react";
import { Plus } from "lucide-react";
import type { ModelProvider } from "@/api";
import { useLocale } from "@/i18n";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { DialogFooter } from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FormField, field, type Action } from "@/components/form-controls";
import {
  ListToolbar,
  TableFrame,
  EmptyRow,
  ManagementDialog,
  matchesQuery,
} from "@/components/management";
import { DeleteResource } from "@/components/delete-resource";

export function ModelProviders({
  providers,
  action,
  busy,
  error,
  clearError,
}: {
  providers: ModelProvider[];
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
}) {
  const { t } = useLocale();
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [tested, setTested] = useState(false);
  const selected = providers.find((provider) => provider.id === editing);
  const rows = providers.filter((provider) =>
    matchesQuery(query, [
      provider.name,
      provider.base_url,
      provider.text_model,
      provider.decision_model,
    ]),
  );
  const close = () => {
    setEditing(null);
    setTested(false);
  };
  return (
    <div className="grid min-w-0 gap-4">
      <ListToolbar query={query} onQueryChange={setQuery} label={t("Search model providers…")}>
        <Button
          disabled={busy}
          onClick={() => {
            clearError();
            setTested(false);
            setEditing("new");
          }}
        >
          <Plus />
          {t("Add model provider")}
        </Button>
      </ListToolbar>
      <TableFrame>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("Name")}</TableHead>
              <TableHead>{t("API base URL")}</TableHead>
              <TableHead>{t("Model")}</TableHead>
              <TableHead>{t("Status")}</TableHead>
              <TableHead>{t("Action")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {!rows.length ? (
              <EmptyRow columns={5} filtered={!!query.trim()}>
                {t("No model providers configured.")}
              </EmptyRow>
            ) : (
              rows.map((provider) => (
                <TableRow key={provider.id}>
                  <TableCell className="font-medium">{provider.name}</TableCell>
                  <TableCell>{provider.base_url}</TableCell>
                  <TableCell>
                    {provider.decision_model && provider.decision_model !== provider.text_model ? (
                      <div className="grid gap-1">
                        <p>
                          <span className="text-muted-foreground">{t("Text model")}: </span>
                          {provider.text_model}
                        </p>
                        <p>
                          <span className="text-muted-foreground">{t("Decision model")}: </span>
                          {provider.decision_model}
                        </p>
                      </div>
                    ) : (
                      provider.text_model
                    )}
                  </TableCell>
                  <TableCell>
                    <Badge variant="outline">{t(provider.enabled ? "Enabled" : "Disabled")}</Badge>
                  </TableCell>
                  <TableCell>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => {
                        clearError();
                        setTested(false);
                        setEditing(provider.id);
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
        open={!!editing}
        onOpenChange={(open) => !open && close()}
        busy={busy}
        error={error}
        title={t(selected ? "Edit model provider" : "Add model provider")}
        description={t(
          "OpenAI-compatible API. Keys are stored on the server and never returned. Routes explicitly select their decision provider.",
        )}
      >
        {editing && (
          <ProviderForm
            key={editing}
            provider={selected}
            busy={busy}
            onCancel={close}
            onSubmit={async (body) => {
              clearError();
              if (
                await action(
                  selected ? `model-providers/${selected.id}` : "model-providers",
                  body,
                  selected ? "PATCH" : "POST",
                )
              )
                close();
            }}
          />
        )}
        {selected && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={async () => {
                clearError();
                setTested(false);
                if (await action(`model-providers/${selected.id}/test`)) setTested(true);
              }}
            >
              {t("Test saved models")}
            </Button>
            {tested && (
              <span role="status" className="text-sm text-muted-foreground">
                {t("Model connection verified")}
              </span>
            )}
            <DeleteResource
              label={t("Delete provider")}
              title={t("Delete this provider?")}
              description={t(
                "Remove the provider and its saved key. Assigned routes must be updated first.",
              )}
              path={`model-providers/${selected.id}`}
              action={action}
              busy={busy}
              error={error}
              clearError={clearError}
              onDeleted={close}
            />
          </div>
        )}
      </ManagementDialog>
    </div>
  );
}

function ProviderForm({
  provider,
  busy,
  onSubmit,
  onCancel,
}: {
  provider?: ModelProvider;
  busy: boolean;
  onSubmit: (body: object) => Promise<void>;
  onCancel: () => void;
}) {
  const { t } = useLocale();
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    await onSubmit({
      name: field(data, "name"),
      base_url: field(data, "base_url"),
      text_model: field(data, "text_model"),
      decision_model: field(data, "decision_model") || null,
      enabled,
      ...(field(data, "api_key") ? { api_key: field(data, "api_key") } : {}),
    });
  }
  return (
    <form className="grid gap-4" onSubmit={submit}>
      <FormField id="provider-name" label={t("Name")}>
        <Input
          id="provider-name"
          name="name"
          required
          maxLength={80}
          defaultValue={provider?.name}
          disabled={busy}
        />
      </FormField>
      <FormField id="provider-url" label={t("API base URL")}>
        <Input
          id="provider-url"
          name="base_url"
          type="url"
          required
          placeholder="https://example.com/v1"
          defaultValue={provider?.base_url}
          disabled={busy}
        />
      </FormField>
      <FormField id="provider-key" label={t(provider ? "New API key (optional)" : "API key")}>
        <Input
          id="provider-key"
          name="api_key"
          type="password"
          autoComplete="new-password"
          required={!provider}
          disabled={busy}
        />
      </FormField>
      <FormField id="provider-text" label={t("Text model")}>
        <Input
          id="provider-text"
          name="text_model"
          required
          maxLength={200}
          defaultValue={provider?.text_model}
          disabled={busy}
        />
      </FormField>
      <FormField id="provider-decision" label={t("Decision model")}>
        <Input
          id="provider-decision"
          name="decision_model"
          maxLength={200}
          defaultValue={provider?.decision_model ?? ""}
          disabled={busy}
        />
        <p className="text-xs text-muted-foreground">
          {t("Optional. Uses the text model when omitted.")}
        </p>
      </FormField>
      <FormField id="provider-enabled" label={t("Enabled")}>
        <Switch
          id="provider-enabled"
          checked={enabled}
          onCheckedChange={setEnabled}
          disabled={busy}
        />
      </FormField>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onCancel} disabled={busy}>
          {t("Cancel")}
        </Button>
        <Button type="submit" disabled={busy}>
          {t("Save")}
        </Button>
      </DialogFooter>
    </form>
  );
}
