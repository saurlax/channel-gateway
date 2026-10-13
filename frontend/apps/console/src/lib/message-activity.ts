import type { ChannelEvent } from "../api";

// A refreshed list is an observation, not a stream or proof of an agent run.
export function messageActivity(previous: ChannelEvent[] | null, current: ChannelEvent[]) {
  if (!previous) return { received: [], accepted: false };
  const known = new Map(previous.map((event) => [event.id, event.state]));
  const isAccepted = (state: string | undefined) => state === "submitted" || state === "completed";
  return {
    received: current.filter((event) => !known.has(event.id)).map((event) => event.id),
    accepted: current.some((event) => isAccepted(event.state) && !isAccepted(known.get(event.id))),
  };
}
