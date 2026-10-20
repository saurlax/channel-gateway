import { ArrowLeft } from "lucide-react";
import type { Snapshot } from "@/api";
import { useLocale } from "@/i18n";
import { navigate, routeEditor } from "@/lib/navigation";
import { routeSourceFromSearch } from "@/conversations";
import { ConsoleLink } from "@/components/console-link";
import { RouteConfiguration } from "@/components/route-configuration";
import { Empty, type Action } from "@/components/form-controls";
import { Button } from "@/components/ui/button";
import { isHost } from "@/hosts";

export function RoutePage({
  url,
  snapshot,
  action,
  busy,
  error,
  clearError,
}: {
  url: URL;
  snapshot: Snapshot;
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
}) {
  const { t, message } = useLocale();
  const editor = routeEditor(url.pathname);
  const route =
    editor && "id" in editor
      ? snapshot.routes.find((record) => record.id === editor.id && !record.parent_route_id)
      : undefined;
  const canAdd = snapshot.connections.some((c) => !isHost(c)) && snapshot.connections.some(isHost);
  return (
    <div className="grid min-w-0 gap-4">
      <Button asChild variant="outline" className="w-fit" disabled={busy}>
        <ConsoleLink href="/routes">
          <ArrowLeft />
          {t("Back to routes")}
        </ConsoleLink>
      </Button>
      {editor && "create" in editor && !canAdd ? (
        <Empty>{t("Add a channel and an agent host first.")}</Empty>
      ) : route || (editor && "create" in editor) ? (
        <div className="w-full max-w-2xl min-w-0">
          <RouteConfiguration
            key={route?.id ?? "new"}
            route={route}
            initial={routeSourceFromSearch(url.searchParams)}
            snapshot={snapshot}
            action={action}
            busy={busy}
            error={error}
            clearError={clearError}
            onSaved={() => navigate("/routes")}
          />
        </div>
      ) : (
        <Empty>{message("Route not found")}</Empty>
      )}
    </div>
  );
}
