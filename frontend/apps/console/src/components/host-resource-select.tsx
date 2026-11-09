import { useEffect, useState } from "react";
import { Check, ChevronsUpDown, X } from "lucide-react";
import { api } from "@/api";
import { useLocale } from "@/i18n";
import type { MessageKey } from "@/i18n/messages";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";

type ResourcePage = { items: { id: string; name: string }[]; next_cursor: string | null };

type CommonProps = {
  id: string;
  host: string;
  disabled: boolean;
};

type SingleProps = CommonProps & {
  resource: "agents" | "threads";
  value: string;
  onChange: (id: string) => void;
  allowCreate?: boolean;
  emptyLabel?: MessageKey;
};

export function HostResourceSelect({ value, onChange, ...props }: SingleProps) {
  return (
    <HostResourcePicker
      {...props}
      values={value ? [value] : []}
      onChange={(values) => onChange(values[0] ?? "")}
    />
  );
}

export function HostMemorySpaceSelect(
  props: CommonProps & { values: string[]; onChange: (ids: string[]) => void },
) {
  return <HostResourcePicker {...props} resource="memory-spaces" multiple />;
}

function HostResourcePicker({
  id,
  host,
  resource,
  values,
  onChange,
  disabled,
  allowCreate = false,
  emptyLabel,
  multiple = false,
}: CommonProps & {
  resource: "agents" | "threads" | "memory-spaces";
  values: string[];
  onChange: (ids: string[]) => void;
  allowCreate?: boolean;
  emptyLabel?: MessageKey;
  multiple?: boolean;
}) {
  const { t, message } = useLocale();
  const [open, setOpen] = useState(false);
  const [page, setPage] = useState<ResourcePage>({ items: [], next_cursor: null });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const path = `connections/${encodeURIComponent(host)}/host-resources/${resource}`;
  useEffect(() => {
    const controller = new AbortController();
    setPage({ items: [], next_cursor: null });
    setError("");
    if (!host) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    api<ResourcePage>("", path, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setPage(result);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setError(String(reason.message));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, reload]);
  async function loadMore() {
    setLoading(true);
    setError("");
    try {
      const result = await api<ResourcePage>(
        "",
        `${path}?${new URLSearchParams({ cursor: page.next_cursor! })}`,
      );
      setPage((previous) => ({
        items: [
          ...new Map([...previous.items, ...result.items].map((item) => [item.id, item])).values(),
        ],
        next_cursor: result.next_cursor,
      }));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }
  const selected = values.map(
    (value) => page.items.find((item) => item.id === value) ?? { id: value, name: value },
  );
  const options = [
    ...selected.filter((item) => !page.items.some((option) => option.id === item.id)),
    ...page.items,
  ];
  const label = selected.length
    ? selected.map((item) => item.name).join(", ")
    : emptyLabel
      ? t(emptyLabel)
      : t(
          multiple
            ? "Choose Memory Spaces"
            : allowCreate
              ? "Created on first message"
              : "Choose an agent",
        );
  function select(value: string) {
    if (multiple) {
      onChange(values.includes(value) ? values.filter((id) => id !== value) : [...values, value]);
    } else {
      onChange([value]);
      setOpen(false);
    }
  }
  return (
    <div className="grid min-w-0 gap-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            role="combobox"
            aria-expanded={open}
            disabled={disabled || !host}
            className="w-full min-w-0 justify-between font-normal"
          >
            <span className="truncate">{label}</span>
            <ChevronsUpDown className="shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-96 max-w-[calc(100vw-2rem)] p-0">
          <Command>
            <CommandInput placeholder={t("Search by name or ID…")} />
            <CommandList aria-multiselectable={multiple || undefined}>
              <CommandEmpty>{loading ? t("Loading…") : t("No matching records.")}</CommandEmpty>
              <CommandGroup>
                {(allowCreate || emptyLabel) && (
                  <CommandItem
                    value="create-first-message"
                    onSelect={() => {
                      onChange([]);
                      setOpen(false);
                    }}
                  >
                    {t(emptyLabel ?? "Created on first message")}
                    {!values.length && <Check className="ml-auto" />}
                  </CommandItem>
                )}
                {options.map((item) => (
                  <CommandItem
                    key={item.id}
                    value={`${item.name} ${item.id}`}
                    onSelect={() => select(item.id)}
                    aria-selected={values.includes(item.id)}
                  >
                    <span className="grid min-w-0 gap-1">
                      <span className="truncate">{item.name}</span>
                      {item.name !== item.id && (
                        <span className="truncate text-xs text-muted-foreground">{item.id}</span>
                      )}
                    </span>
                    {multiple && (
                      <Badge variant="outline" className="ml-auto">
                        {t("Read only")}
                      </Badge>
                    )}
                    {values.includes(item.id) && (
                      <Check className={multiple ? "shrink-0" : "ml-auto shrink-0"} />
                    )}
                  </CommandItem>
                ))}
              </CommandGroup>
            </CommandList>
            {page.next_cursor && (
              <Button
                type="button"
                variant="ghost"
                disabled={loading}
                onClick={() => void loadMore()}
              >
                {loading ? t("Loading…") : t("Load more")}
              </Button>
            )}
          </Command>
        </PopoverContent>
      </Popover>
      {multiple && selected.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {selected.map((item) => (
            <Badge key={item.id} variant="secondary" className="max-w-full gap-1">
              <span className="truncate">{item.name}</span>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(values.filter((value) => value !== item.id))}
                aria-label={t("Remove {name}", { name: item.name })}
                className="shrink-0 rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
              >
                <X className="size-3" aria-hidden="true" />
              </button>
            </Badge>
          ))}
        </div>
      )}
      {error && (
        <div className="flex flex-wrap items-center gap-2 text-sm" role="alert">
          <span className="text-destructive">{message(error)}</span>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={loading || disabled}
            onClick={() => setReload((count) => count + 1)}
          >
            {t("Retry")}
          </Button>
        </div>
      )}
    </div>
  );
}
