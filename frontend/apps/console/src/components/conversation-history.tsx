import { useEffect, useRef, useState } from "react";
import { Bot, MessageSquare } from "lucide-react";
import { useLocale } from "@/i18n";
import { api, type HistoryMessage, type HistoryPage, type Snapshot } from "@/api";
import { conversationKey, mergeHistory, type ConversationSource } from "@/conversations";
import type { Action } from "@/components/form-controls";
import { ManagementDialog } from "@/components/management";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
}: {
  source: ConversationSource;
  snapshot: Snapshot;
  csrfToken: string;
  action: Action;
  busy: boolean;
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
      <p className="break-all text-sm text-muted-foreground">
        {message(source.conversation_kind)}: {source.conversation_id}
      </p>
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
                      ? source.conversation_kind === "group"
                        ? `${t("Sender ID")}: ${entry.sender_id}`
                        : t("Received message")
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
