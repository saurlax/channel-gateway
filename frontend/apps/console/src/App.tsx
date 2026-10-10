import type { MessageKey } from "@/i18n/messages";
import { useLocale } from "@/i18n";
import { useCallback, useEffect, useState } from "react";
import { AppSidebar } from "@/components/app-sidebar";
import { LoginForm } from "@/components/login-form";
import { SidebarInset, SidebarProvider, SidebarTrigger } from "@/components/ui/sidebar";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { api, type Snapshot } from "./api";
import { Connections } from "@/components/connections";
import { Routes } from "@/components/routes";
import { Events } from "@/components/events";
import { Replies } from "@/components/replies";
import { ManagementDialog } from "@/components/management";
import { Empty } from "@/components/form-controls";

const titles: Record<string, MessageKey> = {
  channels: "Channels",
  hosts: "Agent hosts",
  routes: "Routes & access",
  events: "Messages",
  replies: "Reply approvals",
};
function readPage() {
  const hash = location.hash.slice(1);
  return Object.hasOwn(titles, hash) ? hash : "channels";
}
const empty: Snapshot = { connections: [], routes: [], events: [], replies: [] };
export function App() {
  const { t, message, locale } = useLocale();
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const [token, setToken] = useState("");
  const [snapshot, setSnapshot] = useState(empty);
  const [page, setPage] = useState(readPage);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [capability, setCapability] = useState("");
  const [capabilityLabel, setCapabilityLabel] = useState("");
  const refresh = useCallback(async () => {
    const [connections, routes, events, replies] = await Promise.all([
      api<Snapshot["connections"]>(token, "connections"),
      api<Snapshot["routes"]>(token, "routes"),
      api<Snapshot["events"]>(token, "events"),
      api<Snapshot["replies"]>(token, "replies"),
    ]);
    setSnapshot({ connections, routes, events, replies });
    setLoaded(true);
  }, [token]);
  useEffect(() => {
    const change = () => {
      if (location.hash === "#connections") history.replaceState(null, "", "#channels");
      setPage(readPage());
      setError("");
    };
    change();
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    if (!token) return;
    const load = () => {
      void refresh().catch((cause: Error) => setError(cause.message));
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [token, refresh]);
  async function action(path: string, body?: object, method = "POST") {
    setBusy(true);
    setError("");
    try {
      const result = await api<{ agent_token?: string; name?: string }>(token, path, {
        method,
        ...(body ? { body: JSON.stringify(body) } : {}),
      });
      try {
        await refresh();
      } catch {
        setError(
          "Saved, but the list could not refresh. Refresh the page data before trying again.",
        );
      }
      if (result.agent_token) {
        setCapability(result.agent_token);
        setCapabilityLabel(
          result.name ??
            snapshot.routes.find((route) => route.id === path.split("/")[1])?.name ??
            "",
        );
      }
      return true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed.");
      return false;
    } finally {
      setBusy(false);
    }
  }
  function logout() {
    setToken("");
    setSnapshot(empty);
    setCapability("");
    setError("");
    setLoaded(false);
  }
  if (!token)
    return (
      <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
        <div className="w-full max-w-sm">
          <LoginForm
            onLogin={async (value) => {
              await api(value, "connections");
              setToken(value);
            }}
          />
        </div>
      </div>
    );
  return (
    <SidebarProvider>
      <AppSidebar onLogout={logout} page={page} />
      <SidebarInset className="min-w-0">
        {/* Header and content layout from the official sidebar-07 block. */}
        <header className="flex h-16 shrink-0 items-center gap-2 transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
          <div className="flex items-center gap-2 px-4">
            <SidebarTrigger className="-ml-1" />
            <Separator orientation="vertical" className="mr-2 data-[orientation=vertical]:h-4" />
            <Breadcrumb>
              <BreadcrumbList>
                <BreadcrumbItem className="hidden md:block">Channel Gateway</BreadcrumbItem>
                <BreadcrumbSeparator className="hidden md:block" />
                <BreadcrumbItem>
                  <BreadcrumbPage>{t(titles[page])}</BreadcrumbPage>
                </BreadcrumbItem>
              </BreadcrumbList>
            </Breadcrumb>
          </div>
        </header>
        <div className="flex flex-1 flex-col gap-4 p-4 pt-0">
          <div className="flex items-center justify-between gap-4">
            <h1 className="text-2xl font-semibold">{t(titles[page])}</h1>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => {
                void refresh()
                  .then(() => setError(""))
                  .catch((cause: Error) => setError(cause.message));
              }}
            >
              {t("Refresh")}
            </Button>
          </div>
          {error && (
            <Alert variant="destructive">
              <AlertTitle>{t("Action needs attention")}</AlertTitle>
              <AlertDescription>{message(error)}</AlertDescription>
            </Alert>
          )}
          <ManagementDialog
            open={!!capability}
            onOpenChange={(open) => !open && setCapability("")}
            title={t("{name} · access token", { name: capabilityLabel || t("Conversation") })}
            description={t(
              "Use this token as the bearer credential of a dedicated a13n MCP connection. It is shown once and grants access to this route only.",
            )}
          >
            <Input aria-label={t("Conversation access token")} readOnly value={capability} />
            <Button variant="outline" onClick={() => setCapability("")}>
              {t("Dismiss")}
            </Button>
          </ManagementDialog>
          {!loaded ? (
            <Empty>{t("Loading gateway configuration…")}</Empty>
          ) : (
            <>
              {(page === "channels" || page === "hosts") && (
                <Connections
                  key={page}
                  kind={page === "channels" ? "onebots" : "a13n"}
                  connections={snapshot.connections}
                  action={action}
                  busy={busy}
                  error={error}
                  clearError={() => setError("")}
                />
              )}
              {page === "routes" && (
                <Routes
                  snapshot={snapshot}
                  action={action}
                  busy={busy}
                  error={error}
                  clearError={() => setError("")}
                />
              )}
              {page === "events" && (
                <Events
                  snapshot={snapshot}
                  action={action}
                  busy={busy}
                  error={error}
                  clearError={() => setError("")}
                />
              )}
              {page === "replies" && (
                <Replies
                  snapshot={snapshot}
                  action={action}
                  busy={busy}
                  error={error}
                  clearError={() => setError("")}
                />
              )}
            </>
          )}
        </div>
      </SidebarInset>
    </SidebarProvider>
  );
}
