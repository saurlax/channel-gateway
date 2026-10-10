import { useLocale } from "@/i18n";
import type { ReactNode } from "react";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
export type Action = (
  path: string,
  body?: object,
  method?: string,
) => Promise<{ id?: string } | false>;
const fieldClass = "grid gap-2";
export function field(data: FormData, key: string) {
  return String(data.get(key) ?? "").trim();
}
export function FormField({
  id,
  label,
  children,
}: {
  id: string;
  label: string;
  children: ReactNode;
}) {
  return (
    <div className={fieldClass}>
      <Label htmlFor={id}>{label}</Label>
      {children}
    </div>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-sm text-muted-foreground">{children}</p>;
}
export function Choice({
  id,
  value,
  onChange,
  items,
  placeholder,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  items: Array<[string, string]>;
  placeholder?: string;
}) {
  const { t } = useLocale();
  return (
    <Select value={value || undefined} onValueChange={onChange}>
      <SelectTrigger id={id} className="w-full">
        <SelectValue placeholder={placeholder ?? t("Choose…")} />
      </SelectTrigger>
      <SelectContent>
        {items.map(([key, label]) => (
          <SelectItem key={key} value={key}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
