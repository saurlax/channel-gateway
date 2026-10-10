import { useRef, type ReactNode } from "react";
import { useLocale } from "@/i18n";
import { Input } from "@/components/ui/input";
import { TableCell, TableRow } from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

export function matchesQuery(query: string, values: (string | null | undefined)[]) {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/);
  const content = values.filter(Boolean).join(" ").toLocaleLowerCase();
  return terms.every((term) => content.includes(term));
}

export function ListToolbar({
  query,
  onQueryChange,
  label,
  children,
}: {
  query: string;
  onQueryChange: (query: string) => void;
  label: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
      <Input
        type="search"
        aria-label={label}
        placeholder={label}
        value={query}
        onChange={(event) => onQueryChange(event.target.value)}
        className="sm:max-w-sm"
      />
      {children}
    </div>
  );
}

export function TableFrame({ children }: { children: ReactNode }) {
  return <div className="min-w-0 rounded-md border">{children}</div>;
}

export function EmptyRow({
  columns,
  filtered,
  children,
}: {
  columns: number;
  filtered: boolean;
  children: ReactNode;
}) {
  const { t } = useLocale();
  return (
    <TableRow>
      <TableCell colSpan={columns} className="h-24 text-center whitespace-normal">
        {filtered ? t("No matching records.") : children}
      </TableCell>
    </TableRow>
  );
}

export function ManagementDialog({
  open,
  onOpenChange,
  title,
  description,
  busy = false,
  error = "",
  wide = false,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  busy?: boolean;
  error?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  const { t, message } = useLocale();
  const opener = useRef<HTMLElement | null>(null);
  return (
    <Dialog open={open} onOpenChange={(value) => !busy && onOpenChange(value)}>
      <DialogContent
        className={`max-h-[90svh] overflow-y-auto ${wide ? "sm:max-w-2xl" : ""}`}
        onOpenAutoFocus={() => {
          opener.current =
            document.activeElement instanceof HTMLElement ? document.activeElement : null;
        }}
        onCloseAutoFocus={(event) => {
          // Keep focus in a newer dialog during token or navigation handoffs.
          if (document.querySelector('[role="dialog"][data-state="open"]')) {
            event.preventDefault();
            return;
          }
          if (opener.current?.isConnected) {
            event.preventDefault();
            opener.current.focus();
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <AlertTitle>{t("Action needs attention")}</AlertTitle>
            <AlertDescription>{message(error)}</AlertDescription>
          </Alert>
        )}
        {children}
      </DialogContent>
    </Dialog>
  );
}
