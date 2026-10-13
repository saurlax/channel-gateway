import { useEffect, useRef, useState } from "react";
import type { ChannelEvent } from "@/api";
import { messageActivity } from "@/lib/message-activity";

const idle = { received: [] as string[], accepted: false, sequence: 0 };

export function useMessageActivity(events: ChannelEvent[], enabled: boolean) {
  const previous = useRef<ChannelEvent[] | null>(null);
  const [activity, setActivity] = useState(idle);
  useEffect(() => {
    if (!enabled) {
      previous.current = null;
      setActivity(idle);
      return;
    }
    const next = messageActivity(previous.current, events);
    previous.current = events;
    if (!next.received.length && !next.accepted) {
      setActivity((value) => ({ ...idle, sequence: value.sequence }));
      return;
    }
    setActivity((value) => ({ ...next, sequence: value.sequence + 1 }));
    const timer = window.setTimeout(() => {
      setActivity((value) => ({ ...idle, sequence: value.sequence }));
    }, 1800);
    return () => window.clearTimeout(timer);
  }, [events, enabled]);
  return activity;
}
