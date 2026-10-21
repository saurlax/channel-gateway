import { useState, type FormEvent } from "react";
import type { Route, RouteSource, Snapshot, ThreadPolicy } from "@/api";
import { useLocale } from "@/i18n";
import { isHost } from "@/hosts";
import { duplicateSources, routeSources, singleSource } from "@/lib/route-sources";
import { RouteSources } from "@/components/route-sources";
import { HostResourceSelect } from "@/components/host-resource-select";
import { ConsoleLink } from "@/components/console-link";
import { DeleteResource } from "@/components/delete-resource";
import { Choice, FormField, type Action } from "@/components/form-controls";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Alert, AlertDescription } from "@/components/ui/alert";

export function RouteConfiguration({
  route,
  initial,
  snapshot,
  action,
  busy,
  error,
  clearError,
  onSaved,
}: {
  route?: Route;
  initial?: RouteSource;
  snapshot: Snapshot;
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
  onSaved: () => void;
}) {
  const { t, message } = useLocale();
  const hosts = snapshot.connections.filter(isHost);
  const channels = snapshot.connections.filter((connection) => !isHost(connection));
  const [tab, setTab] = useState("basic");
  const [name, setName] = useState(route?.name ?? "");
  const [sources, setSources] = useState<(RouteSource & { key: string })[]>(() =>
    (route
      ? routeSources(route)
      : [
          initial ?? {
            channel_id: snapshot.connections.find((c) => !isHost(c))?.id ?? "*",
            conversation_kind: "private" as const,
            conversation_id: "",
          },
        ]
    ).map((source) => ({ ...source, key: crypto.randomUUID() })),
  );
  const [host, setHost] = useState(route?.host_id ?? hosts[0]?.id ?? "");
  const [agent, setAgent] = useState(route?.agent_id ?? "");
  const [threadPolicy, setThreadPolicy] = useState<ThreadPolicy>(
    route?.thread_policy ?? "conversation",
  );
  const [fixedThread, setFixedThread] = useState(route?.fixed_thread_id ?? "");
  const [readChannels, setReadChannels] = useState<string[]>(() => {
    if (route?.read_channel_ids != null) return route.read_channel_ids;
    const selected = [
      ...new Set(
        (route
          ? routeSources(route)
          : [
              initial ?? {
                channel_id: channels[0]?.id ?? "*",
              },
            ]
        ).map((source) => source.channel_id),
      ),
    ];
    return selected.includes("*") ? ["*"] : selected;
  });
  const [actionGrants, setActionGrants] = useState<string[]>(route?.action_grants ?? []);
  const [replySource, setReplySource] = useState(route?.reply_source ?? "output");
  const [binding, setBinding] = useState(route?.mcp_connection_id ?? "");
  const [enabled, setEnabled] = useState(route?.enabled ?? true);
  const [validation, setValidation] = useState("");
  const mcpOnly = hosts.find((c) => c.id === host)?.kind === "mcp";
  const dedicated = singleSource(sources);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    clearError();
    setValidation("");
    if (!name.trim()) {
      setTab("basic");
      setValidation("Enter a route name.");
      return;
    }
    if (
      !sources.length ||
      sources.some((source) => !source.channel_id || !source.conversation_id)
    ) {
      setTab("routing");
      setValidation("Choose at least one channel and user or group.");
      return;
    }
    if (duplicateSources(sources)) {
      setTab("routing");
      setValidation("Duplicate route sources are not allowed");
      return;
    }
    if (
      sources.some(
        (source) =>
          source.channel_id !== "*" &&
          !snapshot.connections
            .find((c) => c.id === source.channel_id)
            ?.capabilities?.conversations.includes(source.conversation_kind),
      )
    ) {
      setTab("routing");
      setValidation("This channel does not support that conversation type");
      return;
    }
    if (!host || (!mcpOnly && !agent.trim())) {
      setTab("routing");
      setValidation("Complete the agent destination.");
      return;
    }
    if (!mcpOnly && dedicated && replySource === "mcp" && !binding.trim()) {
      setTab("connections");
      setValidation("Enter the a13n MCP connection ID.");
      return;
    }
    const body = {
      name: name.trim(),
      sources: sources.map(({ channel_id, conversation_kind, conversation_id }) => ({
        channel_id,
        conversation_kind,
        conversation_id,
      })),
      host_id: host,
      agent_id: mcpOnly ? null : agent.trim(),
      thread_policy: mcpOnly ? "conversation" : threadPolicy,
      fixed_thread_id: !mcpOnly && threadPolicy === "fixed" ? fixedThread || null : null,
      read_channel_ids: readChannels,
      action_grants: actionGrants,
      enabled,
      sender_policy: "any",
      allowed_user_ids: [],
      reply_source: mcpOnly ? "mcp" : dedicated ? replySource : "output",
      mcp_connection_id: mcpOnly || !dedicated ? null : binding.trim() || null,
    };
    if (await action(route ? `routes/${route.id}` : "routes", body, route ? "PATCH" : "POST"))
      onSaved();
  }
  return (
    <form noValidate className="grid min-w-0 gap-6" onSubmit={submit}>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList aria-label={t("Route configuration")}>
          <TabsTrigger value="basic">{t("Basic information")}</TabsTrigger>
          <TabsTrigger value="routing">{t("Routing")}</TabsTrigger>
          <TabsTrigger value="connections">{t("Connections")}</TabsTrigger>
        </TabsList>
        <TabsContent value="basic" className="mt-4 gap-4 data-[state=active]:grid">
          <FormField id="route-name" label={t("Name")}>
            <Input
              id="route-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={80}
              aria-required
              autoComplete="off"
              disabled={busy}
            />
          </FormField>
          <FormField id="route-enabled" label={t("Enable route")}>
            <Switch
              id="route-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
              disabled={busy}
            />
          </FormField>
        </TabsContent>
        <TabsContent value="routing" className="mt-4 gap-6 data-[state=active]:grid">
          <section className="grid min-w-0 gap-4" aria-labelledby="route-sources-heading">
            <h2 id="route-sources-heading" className="text-base font-medium">
              {t("Sources")}
            </h2>
            <RouteSources
              sources={sources}
              onChange={setSources}
              snapshot={snapshot}
              disabled={busy}
            />
          </section>
          <section className="grid min-w-0 gap-4" aria-labelledby="route-destination-heading">
            <h2 id="route-destination-heading" className="text-base font-medium">
              {t("Destination")}
            </h2>
            <FormField id="route-host" label={t("Agent host")}>
              <Choice
                disabled={busy}
                id="route-host"
                value={host}
                onChange={(value) => {
                  setHost(value);
                  setAgent("");
                  setFixedThread("");
                  setBinding("");
                }}
                items={hosts.map((c) => [c.id, c.name])}
              />
            </FormField>
            {!mcpOnly && (
              <FormField id="agent-id" label={t("Agent")}>
                <HostResourceSelect
                  key={`${host}-agents`}
                  id="agent-id"
                  host={host}
                  resource="agents"
                  value={agent}
                  onChange={setAgent}
                  disabled={busy}
                />
              </FormField>
            )}
            {!mcpOnly && (
              <>
                <FormField id="thread-policy" label={t("Conversation continuity")}>
                  <Choice
                    id="thread-policy"
                    value={threadPolicy}
                    onChange={(value) => setThreadPolicy(value as ThreadPolicy)}
                    disabled={busy}
                    items={[
                      [
                        "conversation",
                        t("Continue each private chat or group"),
                        t(
                          "For ongoing conversations. Each private chat or group on a bot account keeps its own thread; group members share history.",
                        ),
                      ],
                      [
                        "sender",
                        t("Keep each group member separate"),
                        t(
                          "For personal assistance in groups. Each member keeps separate history within that group, without sharing across groups or bot accounts.",
                        ),
                      ],
                      [
                        "message",
                        t("Start fresh for every message"),
                        t(
                          "For independent questions and tasks. Each message starts a new thread without prior chat history; a new environment may also be created.",
                        ),
                      ],
                      [
                        "fixed",
                        t("Share context across all sources"),
                        t(
                          "For cross-source collaboration. All private chats and groups selected by this route share one thread and its history.",
                        ),
                      ],
                    ]}
                  />
                </FormField>
                {threadPolicy === "fixed" && (
                  <FormField id="fixed-thread" label={t("Shared thread")}>
                    <HostResourceSelect
                      key={`${host}-threads`}
                      id="fixed-thread"
                      host={host}
                      resource="threads"
                      value={fixedThread}
                      onChange={setFixedThread}
                      disabled={busy}
                      allowCreate
                    />
                  </FormField>
                )}
              </>
            )}
          </section>
        </TabsContent>
        <TabsContent value="connections" className="mt-4 gap-4 data-[state=active]:grid">
          {!mcpOnly && dedicated && (
            <>
              <FormField id="reply-source" label={t("Reply source")}>
                <Choice
                  disabled={busy}
                  id="reply-source"
                  value={replySource}
                  onChange={(value) => setReplySource(value as "output" | "mcp")}
                  items={[
                    ["output", t("Final run output")],
                    ["mcp", t("MCP send_message tool")],
                  ]}
                />
              </FormField>
              <FormField id="mcp-connection" label={t("a13n MCP connection ID (optional)")}>
                <Input
                  id="mcp-connection"
                  value={binding}
                  onChange={(event) => setBinding(event.target.value)}
                  disabled={busy}
                />
                <p className="text-xs text-muted-foreground">
                  {t(
                    "Use a dedicated MCP connection for this route. You can bind it after creating the route.",
                  )}
                </p>
              </FormField>
            </>
          )}
          <fieldset className="grid gap-3">
            <legend className="mb-3 text-sm font-medium">
              {t("Platform action authorization")}
            </legend>
            <p className="text-sm text-muted-foreground">
              {t(
                "Source-scoped resource authority. Bot settings apply to the entire account. Agent tools are configured in the Host.",
              )}
            </p>
            {(
              [
                ["history.read", "Read native platform history"],
                ["resource.read", "Read platform resources"],
                ["message.react", "React to messages"],
                ["message.recall", "Recall bot messages"],
                ["message.recall_members", "Recall member messages"],
                ["message.proactive", "Send proactive messages"],
                ["group.manage", "Manage group members and approvals"],
                ["guild.manage", "Manage guilds and permissions"],
                ["content.manage", "Manage guild content and audio"],
                ["bot.manage", "Manage account-wide bot settings"],
              ] as const
            ).map(([grant, label]) => (
              <div key={grant} className="flex items-center gap-3">
                <Checkbox
                  id={`action-${grant}`}
                  checked={actionGrants.includes(grant)}
                  disabled={busy}
                  onCheckedChange={(checked) =>
                    setActionGrants((current) =>
                      checked === true
                        ? [...current, grant]
                        : current.filter((value) => value !== grant),
                    )
                  }
                />
                <label htmlFor={`action-${grant}`} className="text-sm">
                  {t(label)}
                </label>
              </div>
            ))}
          </fieldset>
          <fieldset className="grid gap-3">
            <legend className="mb-3 text-sm font-medium">{t("Readable channels")}</legend>
            <div className="flex items-center gap-3">
              <Checkbox
                id="read-all-channels"
                checked={readChannels.includes("*")}
                onCheckedChange={(checked) => setReadChannels(checked === true ? ["*"] : [])}
                disabled={busy}
              />
              <label htmlFor="read-all-channels" className="text-sm">
                {t("All channels")}
              </label>
            </div>
            {channels.map((channel) => (
              <div key={channel.id} className="flex min-w-0 items-center gap-3">
                <Checkbox
                  id={`read-channel-${channel.id}`}
                  checked={readChannels.includes("*") || readChannels.includes(channel.id)}
                  onCheckedChange={(checked) =>
                    setReadChannels((current) =>
                      checked === true
                        ? [...current, channel.id]
                        : current.filter((id) => id !== channel.id),
                    )
                  }
                  disabled={busy || readChannels.includes("*")}
                />
                <label htmlFor={`read-channel-${channel.id}`} className="min-w-0 break-all text-sm">
                  {channel.name}
                </label>
              </div>
            ))}
          </fieldset>
        </TabsContent>
      </Tabs>
      {validation && (
        <Alert variant="destructive">
          <AlertDescription>{message(validation)}</AlertDescription>
        </Alert>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        {route && (
          <>
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
              onClick={() => void action(`routes/${route.id}/token`)}
            >
              {t("Rotate token")}
            </Button>
          </>
        )}
        <Button asChild variant="outline" disabled={busy}>
          <ConsoleLink href="/routes">{t("Cancel")}</ConsoleLink>
        </Button>
        <Button type="submit" disabled={busy}>
          {busy ? t("Saving…") : route ? t("Save route") : t("Create route")}
        </Button>
      </div>
    </form>
  );
}
