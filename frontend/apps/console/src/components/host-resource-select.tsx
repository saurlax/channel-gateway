import { useEffect, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { api } from "@/api";
import { useLocale } from "@/i18n";
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

export function HostResourceSelect({
  id,
  host,
  resource,
  value,
  onChange,
  disabled,
  allowCreate = false,
}: {
  id: string;
  host: string;
  resource: "agents" | "threads";
  value: string;
  onChange: (id: string) => void;
  disabled: boolean;
  allowCreate?: boolean;
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
    setLoading(true);
    setError("");
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
  const selected = page.items.find((item) => item.id === value);
  const options = value && !selected ? [{ id: value, name: value }, ...page.items] : page.items;
  const label =
    selected?.name ?? (value || t(allowCreate ? "Created on first message" : "Choose an agent"));
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
            <CommandList>
              <CommandEmpty>{loading ? t("Loading…") : t("No matching records.")}</CommandEmpty>
              <CommandGroup>
                {allowCreate && (
                  <CommandItem
                    value="create-first-message"
                    onSelect={() => {
                      onChange("");
                      setOpen(false);
                    }}
                  >
                    {t("Created on first message")}
                    {!value && <Check className="ml-auto" />}
                  </CommandItem>
                )}
                {options.map((item) => (
                  <CommandItem
                    key={item.id}
                    value={`${item.name} ${item.id}`}
                    onSelect={() => {
                      onChange(item.id);
                      setOpen(false);
                    }}
                  >
                    <span className="grid min-w-0 gap-1">
                      <span className="truncate">{item.name}</span>
                      {item.name !== item.id && (
                        <span className="truncate text-xs text-muted-foreground">{item.id}</span>
                      )}
                    </span>
                    {value === item.id && <Check className="ml-auto shrink-0" />}
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
