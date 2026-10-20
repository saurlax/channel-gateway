import assert from "node:assert/strict";
import test from "node:test";
import { newRoutePath, routeSourceFromSearch } from "../src/conversations.ts";

test("route creation links preserve exact source identity across reloads", () => {
  const source = {
    channel_id: "conn_example",
    conversation_kind: "group",
    conversation_id: "id:with spaces&?#",
  };
  const url = new URL(newRoutePath(source), "https://gateway.example");
  assert.equal(url.pathname, "/routes/new");
  assert.deepEqual(routeSourceFromSearch(url.searchParams), source);
  assert.equal(routeSourceFromSearch(new URLSearchParams()), undefined);
  assert.equal(
    routeSourceFromSearch(new URLSearchParams({ ...source, conversation_kind: "unknown" })),
    undefined,
  );
});
import { conversations, mergeHistory } from "../src/conversations.ts";

const route = {
  id: "route1",
  channel_id: "account1",
  conversation_kind: "private",
  conversation_id: "user1",
  name: "Private conversation",
  thread_id: "thread1",
};
const received = (id, account = "account1", conversation = "private:user1") => ({
  id,
  route_id: null,
  state: "unmatched",
  created_at: "2026-10-13T01:00:00Z",
  thread_id: null,
  run_id: null,
  error: null,
  message: {
    text: "Incoming",
    sender_id: "user1",
    address: { account_id: account, conversation_id: conversation },
  },
});

test("conversation grouping combines incoming and outgoing while isolating accounts and kinds", () => {
  const snapshot = {
    connections: [],
    routes: [route],
    events: [
      received("event1"),
      received("event2", "account2"),
      received("event3", "account1", "group:user1"),
    ],
    replies: [
      {
        id: "reply1",
        route_id: "route1",
        text: "Agent answer",
        state: "sent",
        created_at: "2026-10-13T01:01:00Z",
        error: null,
      },
    ],
  };
  const groups = conversations(snapshot);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].route.id, "route1");
  assert.equal(groups[0].latest.direction, "outgoing");
  assert.deepEqual(groups[0].search, [
    "Incoming",
    "user1",
    "unmatched",
    "Agent answer",
    "",
    "sent",
  ]);
});

test("history page merging preserves earlier messages and updates status without duplicates", () => {
  const older = { id: "e1", created_at: "2026-10-13T01:00:00Z", state: "submitted" };
  const reply = { id: "r1", created_at: "2026-10-13T01:01:00Z", state: "queued" };
  const merged = mergeHistory(
    [older, reply],
    [
      { ...reply, state: "sent" },
      { id: "e2", created_at: "2026-10-13T01:02:00Z" },
    ],
  );
  assert.deepEqual(
    merged.map((entry) => entry.id),
    ["e1", "r1", "e2"],
  );
  assert.equal(merged[1].state, "sent");
});

test("enabled routes remain discoverable in Messages when their history is older than the overview", () => {
  const snapshot = {
    connections: [],
    routes: [
      { ...route, enabled: true },
      { ...route, id: "inactive", conversation_id: "user2", enabled: false },
    ],
    events: [],
    replies: [],
  };
  const groups = conversations(snapshot);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].route.id, "route1");
  assert.equal(groups[0].latest, undefined);
});

test("deleted route replies remain in their original conversation", () => {
  const snapshot = {
    connections: [],
    routes: [],
    events: [],
    replies: [
      {
        id: "archived-reply",
        route_id: "deleted-route",
        route_name: "Old route",
        address: { account_id: "account1", conversation_id: "private:user1" },
        text: "Retained answer",
        state: "sent",
        created_at: "2026-10-13T01:00:00Z",
        error: null,
      },
    ],
  };
  const groups = conversations(snapshot);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].name, "Old route");
  assert.equal(groups[0].source.conversation_id, "user1");
  assert.equal(groups[0].latest.text, "Retained answer");
});

test("guild channels and guild direct messages retain separate identities and action history", () => {
  for (const kind of ["guild", "guild_dm"]) {
    const source = { channel_id: "qq", conversation_kind: kind, conversation_id: "same-id" };
    const url = new URL(newRoutePath(source), "https://gateway.example");
    assert.deepEqual(routeSourceFromSearch(url.searchParams), source);
  }
  const snapshot = {
    connections: [],
    routes: [],
    events: [],
    replies: [],
    actions: ["guild", "guild_dm"].map((kind, index) => ({
      id: `action${index}`,
      route_id: `route${index}`,
      operation: "reaction",
      text: "[reaction]",
      address: { account_id: "qq", conversation_id: `${kind}:same-id` },
      state: "succeeded",
      error: null,
      created_at: "2026-10-20T00:00:00Z",
    })),
  };
  const groups = conversations(snapshot);
  assert.equal(groups.length, 2);
  assert.deepEqual(
    new Set(groups.map((group) => group.source.conversation_kind)),
    new Set(["guild", "guild_dm"]),
  );
  assert.ok(groups.every((group) => group.latest.direction === "action"));
});
