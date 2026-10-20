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
const fieldClass = "grid min-w-0 grid-cols-1 gap-2";
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
  disabled,
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  items: Array<[string, string, string?]>;
  placeholder?: string;
  disabled?: boolean;
}) {
  const { t } = useLocale();
  const selected = items.find(([key]) => key === value);
  const described = items.some(([, , description]) => description);
  return (
    <>
      <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
        <SelectTrigger
          id={id}
          className="w-full"
          aria-describedby={selected?.[2] ? `${id}-description` : undefined}
        >
          <SelectValue placeholder={placeholder ?? t("Choose…")}>{selected?.[1]}</SelectValue>
        </SelectTrigger>
        <SelectContent
          position={described ? "popper" : "item-aligned"}
          className={
            described ? "w-[var(--radix-select-trigger-width)] max-w-[calc(100vw-2rem)]" : undefined
          }
        >
          {items.map(([key, label, description]) => (
            <SelectItem key={key} value={key} textValue={label}>
              {description ? (
                <span className="grid gap-1 whitespace-normal">
                  <span className="font-medium">{label}</span>
                  <span className="text-xs text-muted-foreground">{description}</span>
                </span>
              ) : (
                label
              )}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {selected?.[2] && (
        <p id={`${id}-description`} className="text-xs text-muted-foreground">
          {selected[2]}
        </p>
      )}
    </>
  );
}
