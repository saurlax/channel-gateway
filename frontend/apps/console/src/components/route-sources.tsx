import { useState } from "react";
import { Check, ChevronsUpDown, Plus, Trash2 } from "lucide-react";
import type { ConversationKind, RouteSource, Snapshot } from "@/api";
import { conversations } from "@/conversations";
import { useLocale } from "@/i18n";
import { isHost } from "@/hosts";
import { sourceLabel } from "@/lib/route-sources";
import { Choice, FormField } from "@/components/form-controls";
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

export function RouteSources({
  sources,
  onChange,
  snapshot,
  disabled,
}: {
  sources: (RouteSource & { key: string })[];
  onChange: (sources: (RouteSource & { key: string })[]) => void;
  snapshot: Snapshot;
  disabled: boolean;
}) {
  const { t } = useLocale();
  const channels = snapshot.connections.filter((c) => !isHost(c));
  return (
    <div className="grid gap-3">
      {sources.map((source, index) => (
        <div
          key={source.key}
          className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] items-end gap-2"
        >
          <FormField id={`source-channel-${source.key}`} label={t("Channel")}>
            <Choice
              disabled={disabled}
              id={`source-channel-${source.key}`}
              value={source.channel_id}
              onChange={(channel_id) =>
                onChange(
                  sources.map((row) => (row.key === source.key ? { ...row, channel_id } : row)),
                )
              }
              items={[
                ["*", t("All channels")],
                ...channels.map((c): [string, string] => [c.id, c.name]),
                ...(source.channel_id !== "*" && !channels.some((c) => c.id === source.channel_id)
                  ? [[source.channel_id, source.channel_id] as [string, string]]
                  : []),
              ]}
            />
          </FormField>
          <FormField id={`source-target-${source.key}`} label={t("Source conversation")}>
            <SourceTarget
              id={`source-target-${source.key}`}
              source={source}
              snapshot={snapshot}
              disabled={disabled}
              onChange={(target) =>
                onChange(
                  sources.map((row) => (row.key === source.key ? { ...row, ...target } : row)),
                )
              }
            />
          </FormField>
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={disabled}
            aria-label={t("Remove source {number}", { number: index + 1 })}
            onClick={() => onChange(sources.filter((row) => row.key !== source.key))}
          >
            <Trash2 />
          </Button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        size="icon"
        disabled={disabled || sources.length >= 100}
        aria-label={t("Add source")}
        onClick={() =>
          onChange([
            ...sources,
            {
              key: crypto.randomUUID(),
              channel_id: "*",
              conversation_kind: "private",
              conversation_id: "",
            },
          ])
        }
      >
        <Plus />
      </Button>
    </div>
  );
}

function SourceTarget({
  id,
  source,
  snapshot,
  onChange,
  disabled,
}: {
  id: string;
  source: RouteSource;
  snapshot: Snapshot;
  onChange: (target: Pick<RouteSource, "conversation_kind" | "conversation_id">) => void;
  disabled: boolean;
}) {
  const { t } = useLocale();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const channel = snapshot.connections.find((c) => c.id === source.channel_id);
  const allowedKinds: ConversationKind[] = (channel?.capabilities?.conversations as
    ConversationKind[] | undefined) ?? [
    "private",
    "group",
    "guild",
    "guild_dm",
    "channel",
    "thread",
  ];
  const label = (kind: RouteSource["conversation_kind"], id: string) =>
    t(
      sourceLabel({ channel_id: source.channel_id, conversation_kind: kind, conversation_id: id }),
      { id },
    );
  const options = new Map<
    string,
    { conversation_kind: RouteSource["conversation_kind"]; conversation_id: string; label: string }
  >();
  const add = (kind: RouteSource["conversation_kind"], id: string, name?: string | null) => {
    if (kind !== "*" && !allowedKinds.includes(kind)) return;
    options.set(`${kind}:${id}`, {
      conversation_kind: kind,
      conversation_id: id,
      label: name ? `${name} / ${label(kind, id)}` : label(kind, id),
    });
  };
  add("*", "*");
  for (const kind of allowedKinds) add(kind, "*");
  for (const group of conversations(snapshot)) {
    if (source.channel_id === "*" || group.source.channel_id === source.channel_id)
      add(group.source.conversation_kind, group.source.conversation_id, group.name);
  }
  if (source.conversation_id) add(source.conversation_kind, source.conversation_id);
  const typed = query.trim();
  if (/^[A-Za-z0-9_.:@-]{1,128}$/.test(typed)) {
    for (const kind of allowedKinds) add(kind, typed);
  }
  const selected = `${source.conversation_kind}:${source.conversation_id}`;
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        setOpen(value);
        setQuery("");
      }}
    >
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className="w-full min-w-0 justify-between font-normal"
        >
          <span className="truncate">
            {source.conversation_id
              ? label(source.conversation_kind, source.conversation_id)
              : t("Choose a conversation")}
          </span>
          <ChevronsUpDown className="shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 max-w-[calc(100vw-2rem)] p-0">
        <Command>
          <CommandInput
            value={query}
            onValueChange={setQuery}
            placeholder={t("Search or enter a conversation ID…")}
          />
          <CommandList>
            <CommandEmpty>{t("No matching records.")}</CommandEmpty>
            <CommandGroup>
              {[...options.entries()].map(([key, target]) => (
                <CommandItem
                  key={key}
                  value={target.label}
                  onSelect={() => {
                    onChange({
                      conversation_kind: target.conversation_kind,
                      conversation_id: target.conversation_id,
                    });
                    setOpen(false);
                    setQuery("");
                  }}
                >
                  <span className="min-w-0 break-all">{target.label}</span>
                  <Check className={`ml-auto ${selected === key ? "opacity-100" : "opacity-0"}`} />
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
