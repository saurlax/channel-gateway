import { useState } from "react";
import { Trash2 } from "lucide-react";
import { useLocale } from "@/i18n";
import type { Action } from "@/components/form-controls";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
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

export function DeleteResource({
  label,
  title,
  description,
  path,
  action,
  busy,
  error,
  clearError,
  onDeleted,
}: {
  label: string;
  title: string;
  description: string;
  path: string;
  action: Action;
  busy: boolean;
  error: string;
  clearError: () => void;
  onDeleted: () => void;
}) {
  const { t, message } = useLocale();
  const [open, setOpen] = useState(false);
  return (
    <AlertDialog open={open} onOpenChange={(value) => !busy && setOpen(value)}>
      <AlertDialogTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="border-destructive text-destructive hover:bg-destructive/10 hover:text-destructive focus-visible:border-destructive focus-visible:ring-destructive/20 dark:border-destructive dark:hover:bg-destructive/10"
          disabled={busy}
          onClick={clearError}
        >
          <Trash2 />
          {label}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertTitle>{t("Action needs attention")}</AlertTitle>
            <AlertDescription>{message(error)}</AlertDescription>
          </Alert>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={busy}>{t("Cancel")}</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={busy}
            onClick={async (event) => {
              event.preventDefault();
              if (await action(path, undefined, "DELETE")) onDeleted();
            }}
          >
            {busy ? t("Deleting…") : t("Confirm deletion")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
