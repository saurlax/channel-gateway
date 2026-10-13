import assert from "node:assert/strict";
import test from "node:test";
import { messageActivity } from "../src/lib/message-activity.ts";

const event = (id, state = "queued") => ({ id, state });

test("initial history and unchanged or reordered refreshes do not signal activity", () => {
  const history = [event("a", "completed"), event("b", "available")];
  const idle = { received: [], accepted: false };
  assert.deepEqual(messageActivity(null, history), idle);
  assert.deepEqual(messageActivity(history, [...history].reverse()), idle);
  assert.deepEqual(messageActivity(history, history.slice(0, 1)), idle);
});

test("new events signal ingestion without claiming generic MCP acceptance", () => {
  assert.deepEqual(messageActivity([], [event("a", "available"), event("b", "unmatched")]), {
    received: ["a", "b"],
    accepted: false,
  });
  for (const state of ["queued", "available", "blocked", "failed"]) {
    assert.equal(messageActivity([event("a")], [event("a", state)]).accepted, false);
  }
});

test("newly observed Host acceptance pulses once, including completion between polls", () => {
  for (const state of ["submitted", "completed"]) {
    assert.equal(messageActivity([event("a")], [event("a", state)]).accepted, true);
    assert.deepEqual(messageActivity([], [event("a", state)]), {
      received: ["a"],
      accepted: true,
    });
  }
  assert.equal(
    messageActivity([event("a", "submitted")], [event("a", "completed")]).accepted,
    false,
  );
});
