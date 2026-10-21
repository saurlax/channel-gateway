import assert from "node:assert/strict";
import test from "node:test";
import {
  duplicateSources,
  routeSources,
  singleSource,
  sourceLabel,
} from "../src/lib/route-sources.ts";

test("legacy route sources retain account, conversation kind and exact scope", () => {
  const legacy = { channel_id: "qq", conversation_kind: "group", conversation_id: "123" };
  assert.deepEqual(routeSources(legacy), [legacy]);
  const sources = [legacy, { channel_id: "*", conversation_kind: "private", conversation_id: "*" }];
  assert.deepEqual(routeSources({ ...legacy, sources }), sources);
});

test("source duplication compares the full account and conversation identity", () => {
  const source = { channel_id: "qq", conversation_kind: "private", conversation_id: "7" };
  assert.ok(duplicateSources([source, { ...source, key: "another-row" }]));
  assert.equal(duplicateSources([source, { ...source, conversation_kind: "group" }]), false);
  assert.equal(duplicateSources([source, { ...source, channel_id: "weixin" }]), false);
});

test("dedicated MCP binding is available only for one concrete source", () => {
  const source = { channel_id: "qq", conversation_kind: "private", conversation_id: "7" };
  assert.ok(singleSource([source]));
  assert.equal(singleSource([]), false);
  assert.equal(singleSource([source, { ...source, conversation_id: "8" }]), false);
  assert.equal(singleSource([{ ...source, channel_id: "*" }]), false);
  assert.equal(singleSource([{ ...source, conversation_id: "*" }]), false);
});

test("all conversations and private-only scopes stay distinct when reopened", () => {
  const source = { channel_id: "*", conversation_kind: "*", conversation_id: "*" };
  assert.deepEqual(routeSources({ sources: [source] }), [source]);
  assert.equal(sourceLabel(source), "All conversations");
  assert.equal(sourceLabel({ ...source, conversation_kind: "private" }), "All private chats");
  assert.equal(sourceLabel({ ...source, conversation_kind: "group" }), "All groups");
  assert.equal(sourceLabel({ ...source, conversation_kind: "guild" }), "All guild channels");
  assert.equal(singleSource([source]), false);
});
