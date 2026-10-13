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
import { api, ApiError, type AdminSession, type Snapshot } from "./api";
import { Connections } from "@/components/connections";
import { Routes } from "@/components/routes";
import { Events } from "@/components/events";
import { ManagementDialog } from "@/components/management";
import { Empty, FormField } from "@/components/form-controls";
import { Dashboard } from "@/components/dashboard";
import { useMessageActivity } from "@/hooks/use-message-activity";

const titles: Record<string, MessageKey> = {
  overview: "Overview",
  channels: "Channels",
  hosts: "Agent hosts",
  routes: "Routes & access",
  events: "Messages",
};
function readPage() {
  const hash = location.hash.slice(1);
  return Object.hasOwn(titles, hash) ? hash : "overview";
}
const empty: Snapshot = { connections: [], routes: [], events: [], replies: [] };
export function App() {
  const { t, message, locale } = useLocale();
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  const [hostCapability, setHostCapability] = useState(false);
  const [csrfToken, setCsrfToken] = useState("");
  const [restoringSession, setRestoringSession] = useState(true);
  const [snapshot, setSnapshot] = useState(empty);
  const [page, setPage] = useState(readPage);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [updatedAt, setUpdatedAt] = useState("");
  const activity = useMessageActivity(snapshot.events, loaded && !!csrfToken);
  const [capability, setCapability] = useState("");
  const [capabilityLabel, setCapabilityLabel] = useState("");
  const refresh = useCallback(async () => {
    const [connections, routes, events, replies] = await Promise.all([
      api<Snapshot["connections"]>(csrfToken, "connections"),
      api<Snapshot["routes"]>(csrfToken, "routes"),
      api<Snapshot["events"]>(csrfToken, "events"),
      api<Snapshot["replies"]>(csrfToken, "replies"),
    ]);
    setSnapshot({ connections, routes, events, replies });
    setUpdatedAt(new Date().toISOString());
    setLoaded(true);
  }, [csrfToken]);
  useEffect(() => {
    const change = () => {
      if (location.hash === "#connections") history.replaceState(null, "", "#channels");
      if (location.hash === "#replies") history.replaceState(null, "", "#events");
      setPage(readPage());
      setError("");
    };
    change();
    window.addEventListener("hashchange", change);
    return () => window.removeEventListener("hashchange", change);
  }, []);
  useEffect(() => {
    let active = true;
    api<AdminSession>("", "auth/session")
      .then((session) => {
        if (active) setCsrfToken(session.csrf_token);
      })
      .catch((cause) => {
        if (active && !(cause instanceof ApiError && cause.status === 401)) setError(cause.message);
      })
      .finally(() => {
        if (active) setRestoringSession(false);
      });
    return () => {
      active = false;
    };
  }, []);
  useEffect(() => {
    if (!csrfToken) return;
    const load = () => {
      void refresh().catch((cause: Error) => {
        if (cause instanceof ApiError && cause.status === 401) clearSession();
        setError(cause.message);
      });
    };
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, [csrfToken, refresh]);
  async function action(path: string, body?: object, method = "POST") {
    setBusy(true);
    setError("");
    try {
      const result = await api<{
        agent_token?: string;
        mcp_key?: string;
        name?: string;
        id?: string;
      }>(csrfToken, path, {
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
      if (result.agent_token || result.mcp_key) {
        setHostCapability(!!result.mcp_key);
        setCapability(result.mcp_key ?? result.agent_token ?? "");
        setCapabilityLabel(
          result.name ??
            snapshot.routes.find((route) => route.id === path.split("/")[1])?.name ??
            "",
        );
      }
      return result;
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) clearSession();
      setError(cause instanceof Error ? cause.message : "Request failed.");
      return false;
    } finally {
      setBusy(false);
    }
  }
  function clearSession() {
    setCsrfToken("");
    setSnapshot(empty);
    setCapability("");
    setError("");
    setLoaded(false);
  }
  async function logout() {
    setBusy(true);
    try {
      await api(csrfToken, "auth/logout", { method: "POST" });
      clearSession();
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 401) clearSession();
      else setError(cause instanceof Error ? cause.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }
  if (restoringSession)
    return (
      <div className="flex min-h-svh items-center justify-center p-6">
        <Empty>{t("Restoring session…")}</Empty>
      </div>
    );
  if (!csrfToken)
    return (
      <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
        <div className="w-full max-w-sm">
          <LoginForm
            sessionError={error}
            onLogin={async (username, password) => {
              const session = await api<AdminSession>("", "auth/login", {
                method: "POST",
                body: JSON.stringify({ username, password }),
              });
              setError("");
              setCsrfToken(session.csrf_token);
            }}
          />
        </div>
      </div>
    );
  return (
    <SidebarProvider>
      <AppSidebar onLogout={() => void logout()} page={page} />
      <SidebarInset className="min-w-0">
        {/* Header and content layout from the official sidebar-07 block. */}
        <header className="flex h-16 shrink-0 items-center gap-2 border-b transition-[width,height] ease-linear group-has-data-[collapsible=icon]/sidebar-wrapper:h-12">
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
        <div className="flex flex-1 flex-col gap-5 p-4 md:p-6">
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
            title={t(hostCapability ? "{name}: MCP key" : "{name}: access token", {
              name: capabilityLabel || t("Conversation"),
            })}
            description={
              hostCapability
                ? t(
                    "This MCP key is shown once. It identifies the host and grants access to its enabled routes. Store it in your client’s secret configuration.",
                  )
                : t(
                    "Use this token as an MCP bearer credential. It is shown once and grants access to this route. All-conversation routes include their concrete conversations.",
                  )
            }
          >
            <FormField
              id="mcp-capability"
              label={hostCapability ? t("MCP key") : t("Conversation access token")}
            >
              <Input id="mcp-capability" readOnly value={capability} />
            </FormField>
            <FormField id="mcp-endpoint" label={t("MCP URL")}>
              <Input id="mcp-endpoint" readOnly value={`${location.origin}/mcp/`} />
            </FormField>
            <Button variant="outline" onClick={() => setCapability("")}>
              {t("Dismiss")}
            </Button>
          </ManagementDialog>
          {!loaded ? (
            <Empty>{t("Loading gateway configuration…")}</Empty>
          ) : (
            <>
              {page === "overview" && (
                <Dashboard snapshot={snapshot} activity={activity} updatedAt={updatedAt} />
              )}
              {(page === "channels" || page === "hosts") && (
                <Connections
                  key={page}
                  kind={page === "channels" ? "onebots" : "a13n"}
                  connections={snapshot.connections}
                  csrfToken={csrfToken}
                  refresh={refresh}
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
                  csrfToken={csrfToken}
                  newEventIds={activity.received}
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
