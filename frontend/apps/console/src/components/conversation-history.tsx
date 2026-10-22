import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowLeft, Bot, MessageSquare } from "lucide-react";
import { useLocale } from "@/i18n";
import { api, type HistoryMessage, type HistoryPage, type Snapshot } from "@/api";
import {
  conversationKey,
  conversations,
  mergeHistory,
  routeSourceFromSearch,
  type ConversationSource,
} from "@/conversations";
import type { Action } from "@/components/form-controls";
import { ConsoleLink } from "@/components/console-link";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import {
  Message,
  MessageAvatar,
  MessageContent,
  MessageFooter,
  MessageHeader,
} from "@/components/ui/message";

export function ConversationPage({
  url,
  snapshot,
  csrfToken,
  action,
  busy,
}: {
  url: URL;
  snapshot: Snapshot;
  csrfToken: string;
  action: Action;
  busy: boolean;
}) {
  const { t, message } = useLocale();
  const source = routeSourceFromSearch(url.searchParams);
  const group =
    source && conversations(snapshot).find((item) => item.key === conversationKey(source));
  const account = snapshot.connections.find((item) => item.id === source?.channel_id);
  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col gap-4">
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b pb-4">
        <Button variant="outline" size="sm" asChild>
          <ConsoleLink href="/events">
            <ArrowLeft />
            {t("Back to messages")}
          </ConsoleLink>
        </Button>
        {source && (
          <div className="min-w-0 flex-1">
            <h2 className="break-words text-sm font-medium">
              {group?.route?.name ?? group?.name ?? account?.name ?? source.channel_id}
            </h2>
            <p className="break-all text-xs text-muted-foreground">
              {account?.name ?? source.channel_id} / {message(source.conversation_kind)}:{" "}
              {source.conversation_id}
            </p>
          </div>
        )}
      </div>
      {source ? (
        <ConversationMessages
          key={conversationKey(source)}
          source={source}
          snapshot={snapshot}
          csrfToken={csrfToken}
          action={action}
          busy={busy}
        />
      ) : (
        <p role="alert" className="text-sm text-destructive">
          {t("Conversation not found.")}
        </p>
      )}
    </section>
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
  const pinned = useRef(true);
  const prepend = useRef<{ height: number; top: number } | null>(null);
  const query = new URLSearchParams(source).toString();
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
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element || loading) return;
    if (prepend.current) {
      element.scrollTop = prepend.current.top + element.scrollHeight - prepend.current.height;
      prepend.current = null;
    } else if (!initialized.current || pinned.current) {
      element.scrollTop = element.scrollHeight;
    }
    initialized.current = true;
  }, [loading, messages]);
  async function loadOlder() {
    const oldest = messages[0];
    if (!oldest || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const cursor = new URLSearchParams({
        before_created_at: oldest.created_at,
        before_id: oldest.id,
      });
      const page = await api<HistoryPage>(csrfToken, `history?${query}&${cursor}`);
      loadedOlder.current = true;
      const element = scroller.current;
      if (element) prepend.current = { height: element.scrollHeight, top: element.scrollTop };
      pinned.current = false;
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
    <>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {message(error)}
        </p>
      )}
      <div
        ref={scroller}
        className="min-h-0 flex-1 overflow-y-auto overscroll-contain [overflow-anchor:none]"
        tabIndex={0}
        role="region"
        aria-label={t("Conversation history")}
        aria-busy={loading || loadingOlder}
        onScroll={(event) => {
          const element = event.currentTarget;
          pinned.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}
      >
        <div className="mx-auto flex max-w-4xl flex-col gap-6 px-1 pb-6 sm:px-4">
          {hasMore && (
            <Button
              className="self-center"
              variant="outline"
              size="sm"
              disabled={loadingOlder}
              onClick={() => void loadOlder()}
            >
              {loadingOlder ? t("Loading…") : t("Load earlier messages")}
            </Button>
          )}
          {loading ? (
            <p className="py-8 text-center text-sm text-muted-foreground" role="status">
              {t("Loading…")}
            </p>
          ) : messages.length === 0 ? (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {t("No messages in this conversation yet.")}
            </p>
          ) : (
            <ol className="flex min-w-0 flex-col gap-6">
              {messages.map((entry) => {
                const incoming = entry.direction === "incoming";
                return (
                  <li key={entry.id}>
                    <Message align={incoming ? "start" : "end"}>
                      <MessageAvatar>
                        <Avatar>
                          <AvatarFallback>
                            {incoming ? (
                              <MessageSquare className="size-4" aria-hidden="true" />
                            ) : (
                              <Bot className="size-4" aria-hidden="true" />
                            )}
                          </AvatarFallback>
                        </Avatar>
                      </MessageAvatar>
                      <MessageContent>
                        {incoming && entry.sender_id && (
                          <MessageHeader className="[overflow-wrap:anywhere]">
                            {entry.sender_id}
                          </MessageHeader>
                        )}
                        <Bubble variant={incoming ? "secondary" : "tinted"}>
                          <BubbleContent className="whitespace-pre-wrap [overflow-wrap:anywhere]">
                            {entry.text}
                          </BubbleContent>
                        </Bubble>
                        <MessageFooter className="flex-wrap gap-x-2 gap-y-1">
                          <time dateTime={entry.created_at}>{date(entry.created_at)}</time>
                          <Badge variant="secondary">{message(entry.state)}</Badge>
                          {incoming && entry.state === "failed" && (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy}
                              onClick={() => void action(`events/${entry.id}/retry`)}
                            >
                              {t("Retry")}
                            </Button>
                          )}
                        </MessageFooter>
                        {entry.error && (
                          <p className="max-w-full whitespace-pre-wrap break-words px-3 text-xs text-destructive [overflow-wrap:anywhere]">
                            {message(entry.error)}
                            {entry.thread_id && (
                              <span className="mt-1 block">
                                {t("Agent thread")}: {entry.thread_id}
                              </span>
                            )}
                          </p>
                        )}
                      </MessageContent>
                    </Message>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </div>
    </>
  );
}
