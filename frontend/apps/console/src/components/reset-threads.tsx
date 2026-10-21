import { useState } from "react";
import { RotateCcw } from "lucide-react";
import { useLocale } from "@/i18n";
import type { Action } from "@/components/form-controls";
import { Button } from "@/components/ui/button";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

export function ResetThreads({
  routeId,
  action,
  busy,
  error,
  clearError,
}: {
  routeId: string;
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
}) {
  const { t, message } = useLocale();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState(false);
  return (
    <div className="grid gap-2">
      <AlertDialog open={open} onOpenChange={(value) => !busy && setOpen(value)}>
        <AlertDialogTrigger asChild>
          <Button
            type="button"
            variant="outline"
            className="w-fit"
            disabled={busy}
            onClick={clearError}
          >
            <RotateCcw />
            {t("Reset conversations")}
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("Reset this route's conversations?")}</AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                "All conversation and sender threads on this route will be reset. Future messages start new Agent threads. Message history is retained. Pending messages must finish first.",
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {message(error)}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={busy}>{t("Cancel")}</AlertDialogCancel>
            <AlertDialogAction
              disabled={busy}
              onClick={async (event) => {
                event.preventDefault();
                if (await action(`routes/${routeId}/reset-thread`)) {
                  setOpen(false);
                  setDone(true);
                }
              }}
            >
              {busy ? t("Resetting…") : t("Confirm reset")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {done && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("Route conversations reset. Future messages start new Agent threads.")}
        </p>
      )}
    </div>
  );
}
