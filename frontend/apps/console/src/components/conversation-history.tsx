import { useEffect, useRef, useState } from "react";
import { Bot, MessageSquare, RotateCcw } from "lucide-react";
import { useLocale } from "@/i18n";
import { api, type HistoryMessage, type HistoryPage, type Snapshot } from "@/api";
import { conversationKey, mergeHistory, type ConversationSource } from "@/conversations";
import type { Action } from "@/components/form-controls";
import { ManagementDialog } from "@/components/management";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemGroup,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item";

export function ConversationDialog({
  open,
  onOpenChange,
  title,
  source,
  snapshot,
  csrfToken,
  action,
  busy,
  error = "",
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  source: ConversationSource;
  snapshot: Snapshot;
  csrfToken: string;
  action: Action;
  busy: boolean;
  error?: string;
}) {
  const { t } = useLocale();
  return (
    <ManagementDialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      description={t("Received messages and agent replies in chronological order.")}
      wide
      error={error}
    >
      <ConversationMessages
        key={conversationKey(source)}
        source={source}
        snapshot={snapshot}
        csrfToken={csrfToken}
        action={action}
        busy={busy}
        actionError={error}
      />
    </ManagementDialog>
  );
}

function ConversationMessages({
  source,
  snapshot,
  csrfToken,
  action,
  busy,
  actionError,
}: {
  source: ConversationSource;
  snapshot: Snapshot;
  csrfToken: string;
  action: Action;
  busy: boolean;
  actionError: string;
}) {
  const { t, message, date } = useLocale();
  const [messages, setMessages] = useState<HistoryMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [error, setError] = useState("");
  const loadedOlder = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const initialized = useRef(false);
  const [resetOpen, setResetOpen] = useState(false);
  const [resetDone, setResetDone] = useState(false);
  const route = snapshot.routes.find(
    (item) =>
      item.channel_id === source.channel_id &&
      item.conversation_kind === source.conversation_kind &&
      item.conversation_id === source.conversation_id,
  );
  const canReset =
    route &&
    snapshot.connections.some((host) => host.id === route.host_id && host.kind === "a13n") &&
    ["conversation", "sender"].includes(route.thread_policy ?? "conversation");
  const query = new URLSearchParams({
    channel_id: source.channel_id,
    conversation_kind: source.conversation_kind,
    conversation_id: source.conversation_id,
  }).toString();
  useEffect(() => {
    let active = true;
    void api<HistoryPage>(csrfToken, `history?${query}`)
      .then((page) => {
        if (!active) return;
        setMessages((existing) => mergeHistory(existing, page.messages));
        if (!loadedOlder.current) setHasMore(page.has_more);
        setError("");
      })
      .catch((cause: Error) => active && setError(cause.message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [csrfToken, query, snapshot]);
  useEffect(() => {
    if (!loading && messages.length && !initialized.current && scroller.current) {
      scroller.current.scrollTop = scroller.current.scrollHeight;
      initialized.current = true;
    }
  }, [loading, messages]);
  async function loadOlder() {
    const oldest = messages[0];
    if (!oldest) return;
    setLoadingOlder(true);
    try {
      const cursor = new URLSearchParams({
        before_created_at: oldest.created_at,
        before_id: oldest.id,
      });
      const page = await api<HistoryPage>(csrfToken, `history?${query}&${cursor}`);
      loadedOlder.current = true;
      setMessages((existing) => mergeHistory(existing, page.messages));
      setHasMore(page.has_more);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Request failed.");
    } finally {
      setLoadingOlder(false);
    }
  }
  return (
    <div className="grid min-w-0 gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 break-all text-sm text-muted-foreground">
          {message(source.conversation_kind)}: {source.conversation_id}
        </p>
        {canReset && (
          <AlertDialog open={resetOpen} onOpenChange={(value) => !busy && setResetOpen(value)}>
            <AlertDialogTrigger asChild>
              <Button variant="outline" size="sm" disabled={busy}>
                <RotateCcw />
                {t("Reset conversation")}
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("Reset conversation?")}</AlertDialogTitle>
                <AlertDialogDescription>
                  {t(
                    "The next message starts a new Agent thread. Gateway history is retained. All sender threads in this conversation are reset. Pending messages must finish first.",
                  )}
                </AlertDialogDescription>
              </AlertDialogHeader>
              {actionError && (
                <p role="alert" className="text-sm text-destructive">
                  {message(actionError)}
                </p>
              )}
              <AlertDialogFooter>
                <AlertDialogCancel disabled={busy}>{t("Cancel")}</AlertDialogCancel>
                <AlertDialogAction
                  disabled={busy}
                  onClick={async (event) => {
                    event.preventDefault();
                    if (await action(`routes/${route.id}/reset-thread`)) {
                      setResetOpen(false);
                      setResetDone(true);
                    }
                  }}
                >
                  {busy ? t("Resetting…") : t("Confirm reset")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        )}
      </div>
      {resetDone && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("Conversation reset. The next message starts a new Agent thread.")}
        </p>
      )}
      {error && <p role="alert">{message(error)}</p>}
      <div
        ref={scroller}
        className="max-h-[60svh] overflow-y-auto"
        tabIndex={0}
        role="region"
        aria-label={t("Conversation history")}
      >
        {hasMore && (
          <Button variant="outline" disabled={loadingOlder} onClick={() => void loadOlder()}>
            {loadingOlder ? t("Loading…") : t("Load earlier messages")}
          </Button>
        )}
        {loading ? (
          <p className="text-sm text-muted-foreground">{t("Loading…")}</p>
        ) : messages.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {t("No messages in this conversation yet.")}
          </p>
        ) : (
          <ItemGroup className="gap-4">
            {messages.map((entry) => (
              <Item
                key={entry.id}
                variant={entry.direction === "incoming" ? "outline" : "muted"}
                role="listitem"
              >
                <ItemMedia variant="icon">
                  {entry.direction === "incoming" ? <MessageSquare /> : <Bot />}
                </ItemMedia>
                <ItemContent className="min-w-0">
                  <ItemTitle className="break-all">
                    {entry.direction === "incoming"
                      ? ["group", "guild"].includes(source.conversation_kind)
                        ? `${t("Sender ID")}: ${entry.sender_id}`
                        : t("Received message")
                      : entry.direction === "action"
                        ? t("Channel action")
                        : t("Agent reply")}
                  </ItemTitle>
                  <ItemDescription>{date(entry.created_at)}</ItemDescription>
                  <p className="whitespace-pre-wrap break-words text-sm">{entry.text}</p>
                  {entry.error && <ItemDescription>{message(entry.error)}</ItemDescription>}
                  {entry.error && entry.thread_id && (
                    <ItemDescription className="break-all">
                      {t("Agent thread")}: {entry.thread_id}
                    </ItemDescription>
                  )}
                </ItemContent>
                <ItemActions className="self-start">
                  <Badge variant="secondary">{message(entry.state)}</Badge>
                  {entry.direction === "incoming" && entry.state === "failed" && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busy}
                      onClick={() => void action(`events/${entry.id}/retry`)}
                    >
                      {t("Retry")}
                    </Button>
                  )}
                </ItemActions>
              </Item>
            ))}
          </ItemGroup>
        )}
      </div>
    </div>
  );
}
