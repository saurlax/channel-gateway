import assert from "node:assert/strict";
import test from "node:test";
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
