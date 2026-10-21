import type { Route, RouteSource } from "../api.ts";

export const sourceLabels = {
  "*": ["All conversations", "All conversations"],
  private: ["All private chats", "Private chat {id}"],
  group: ["All groups", "Group {id}"],
  guild: ["All guild channels", "Guild channel {id}"],
  guild_dm: ["All guild direct messages", "Guild direct message {id}"],
  channel: ["All channel conversations", "Channel {id}"],
  thread: ["All message threads", "Message thread {id}"],
} as const;

export function sourceLabel(source: RouteSource) {
  return sourceLabels[source.conversation_kind][source.conversation_id === "*" ? 0 : 1];
}

export function routeSources(route: Route): RouteSource[] {
  return (
    route.sources ?? [
      {
        channel_id: route.channel_id,
        conversation_kind: route.conversation_kind,
        conversation_id: route.conversation_id,
      },
    ]
  );
}

export function singleSource(sources: RouteSource[]) {
  return (
    sources.length === 1 &&
    sources[0].channel_id !== "*" &&
    sources[0].conversation_kind !== "*" &&
    sources[0].conversation_id !== "*"
  );
}

export function duplicateSources(sources: RouteSource[]) {
  return (
    new Set(
      sources.map((source) =>
        JSON.stringify([source.channel_id, source.conversation_kind, source.conversation_id]),
      ),
    ).size !== sources.length
  );
}
