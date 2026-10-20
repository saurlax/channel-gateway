import type { Route, RouteSource } from "../api.ts";

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
    sources.length === 1 && sources[0].channel_id !== "*" && sources[0].conversation_id !== "*"
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
