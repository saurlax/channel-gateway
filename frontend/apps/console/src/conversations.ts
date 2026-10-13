import type { ChannelEvent, HistoryMessage, Route, Snapshot } from "./api.ts";

export function eventAddress(snapshot: Snapshot, event: ChannelEvent) {
  if (event.message.address) return event.message.address;
  const route = snapshot.routes.find((route) => route.id === event.route_id);
  return route && route.conversation_id !== "*"
    ? {
        account_id: route.channel_id,
        conversation_id: `${route.conversation_kind}:${route.conversation_id}`,
      }
    : undefined;
}

export type ConversationSource = {
  channel_id: string;
  conversation_kind: "private" | "group";
  conversation_id: string;
};

export type Conversation = {
  key: string;
  source: ConversationSource;
  route?: Route;
  name?: string;
  latest?: HistoryMessage;
  search: string[];
};

export function conversationKey(source: ConversationSource) {
  return JSON.stringify([source.channel_id, source.conversation_kind, source.conversation_id]);
}

export function sourceFromAddress(address: { account_id: string; conversation_id: string }) {
  const [kind, ...parts] = address.conversation_id.split(":");
  if (kind !== "private" && kind !== "group") return undefined;
  return {
    channel_id: address.account_id,
    conversation_kind: kind,
    conversation_id: parts.join(":"),
  } as ConversationSource;
}

export function conversations(snapshot: Snapshot): Conversation[] {
  const groups = new Map<string, Conversation>();
  function add(source: ConversationSource, entry: HistoryMessage, name?: string | null) {
    const key = conversationKey(source);
    let group = groups.get(key);
    if (!group) {
      group = {
        key,
        source,
        latest: entry,
        search: [],
        name: name ?? undefined,
        route: snapshot.routes.find(
          (route) =>
            route.channel_id === source.channel_id &&
            route.conversation_kind === source.conversation_kind &&
            route.conversation_id === source.conversation_id,
        ),
      };
      groups.set(key, group);
    }
    group.search.push(entry.text, entry.sender_id ?? "", entry.state);
    if (!group.latest || compareMessages(group.latest, entry) < 0) group.latest = entry;
  }
  for (const event of snapshot.events) {
    const address = eventAddress(snapshot, event);
    const source = address && sourceFromAddress(address);
    if (source)
      add(
        source,
        {
          ...event,
          direction: "incoming",
          text: event.message.text,
          sender_id: event.message.sender_id,
          run_id: event.run_id,
        },
        event.route_name,
      );
  }
  for (const reply of snapshot.replies) {
    const route = snapshot.routes.find((route) => route.id === reply.route_id);
    const source = route ?? (reply.address && sourceFromAddress(reply.address));
    if (source && source.conversation_id !== "*")
      add(
        source,
        {
          ...reply,
          direction: "outgoing",
          sender_id: null,
          thread_id: route?.thread_id ?? null,
          run_id: null,
        },
        reply.route_name,
      );
  }
  for (const route of snapshot.routes) {
    if (!route.enabled || route.conversation_id === "*") continue;
    const key = conversationKey(route);
    if (!groups.has(key)) groups.set(key, { key, source: route, route, search: [] });
  }
  return [...groups.values()].sort((a, b) =>
    a.latest && b.latest
      ? compareMessages(b.latest, a.latest)
      : a.latest
        ? -1
        : b.latest
          ? 1
          : a.key.localeCompare(b.key),
  );
}

export function compareMessages(a: HistoryMessage, b: HistoryMessage) {
  return a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id);
}

export function mergeHistory(existing: HistoryMessage[], incoming: HistoryMessage[]) {
  const records = new Map(existing.map((entry) => [entry.id, entry]));
  for (const entry of incoming) records.set(entry.id, entry);
  return [...records.values()].sort(compareMessages);
}
