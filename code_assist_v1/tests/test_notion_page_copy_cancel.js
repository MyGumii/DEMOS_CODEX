"use strict";
const assert = require("node:assert/strict"), {test} = require("node:test");
const Copy = require("../notion_static/notion_page_copy.js");
const {memory, lockManager} = require("./notion_page_copy_test_helpers.js");
const scope = {userId: "owner", workspaceId: "ws", pageId: "source"};
const body = {operation_id: "uncertain-original", expected_document_sequence: 7, expected_workspace_epoch: 2, expected_document_epoch: 3};
const cancelled = {state: "cancelled", result: {contract: "code_assist.notion.canonical_page_copy_cancelled.v1",
  state: "cancelled", workspace_id: "ws", source_page_id: "source", operation_id: body.operation_id, copy_request: body}};
const committed = {state: "committed", result: {contract: "code_assist.notion.canonical_page_copy.v1",
  source_page_id: "source", workspace: {id: "ws"}, page: {id: "copy", workspace_id: "ws"}, page_ids: ["copy"]}, current: {archived: false}};
async function setup() {
  const storage = memory(), locks = lockManager(), calls = [];
  let handler = async () => {throw Error("response lost");};
  const controller = Copy.create({storage: () => storage, locks: () => locks, transport: async (url, options) => {
    if (url.endsWith("-intent")) return {state: "pending", source_page_id: scope.pageId, request: options.body};
    calls.push({url, body: options.body}); return handler(url, options);
  }});
  const hooks = {current: () => true, prepare: async () => body, beforeRetry: async () => {throw Error("blocked draft");}};
  await assert.rejects(controller.run(scope, hooks));
  return {controller, hooks, calls, storage, handle: value => {handler = value;}};
}
test("uncertain copy can be retired with exact original stamps without flushing a blocked draft", async () => {
  const h = await setup(); h.handle(async () => cancelled);
  assert.equal((await h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id})).state, "cancelled");
  assert.match(h.calls.at(-1).url, /duplicate-cancel$/); assert.deepEqual(h.calls.at(-1).body, body);
  assert.deepEqual(h.controller.list(scope), []); assert.equal(h.controller.pending(scope), false);
  assert.equal(JSON.parse(h.storage.getItem(h.storage.key(0))).cancelled, true);
});
test("lost cancellation response is recovered as cancelled instead of resending copy", async () => {
  const h = await setup();
  await assert.rejects(h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id}));
  assert.equal(h.controller.pending(scope), true);
  h.handle(async () => cancelled);
  assert.equal((await h.controller.run(scope, h.hooks)).state, "cancelled");
  assert.match(h.calls.at(-1).url, /duplicate-result$/); assert.equal(h.controller.pending(scope), false);
});
test("copy completed before cancellation remains discoverable and cannot be undone", async () => {
  const h = await setup(); h.handle(async () => committed);
  assert.equal((await h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id})).state, "committed");
  assert.equal(h.controller.list(scope)[0].destinationId, "copy");
  h.handle(async () => cancelled);
  await assert.rejects(h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id}));
  assert.equal(h.controller.list(scope)[0].confirmed, true, "contradictory cancellation cannot erase a known completed receipt");
});
test("stale operation, scope change and malformed cancellation all retain the original request", async () => {
  const h = await setup(); const before = h.calls.length;
  await assert.rejects(h.controller.cancel(scope, {...h.hooks, operationId: "other"}));
  await assert.rejects(h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id, current: () => false}));
  assert.equal(h.calls.length, before);
  for (const value of [{state: "unknown"}, {...cancelled, result: {...cancelled.result, workspace_id: "other"}},
    {...cancelled, result: {...cancelled.result, copy_request: {...body, expected_document_sequence: 8}}}]) {
    h.handle(async () => value);
    await assert.rejects(h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id}));
    assert.equal(h.controller.list(scope)[0].request.operation_id, body.operation_id);
  }
});
test("a fresh explicit copy after terminal cancellation uses a new operation", async () => {
  const h = await setup(); h.handle(async () => cancelled);
  await h.controller.cancel(scope, {...h.hooks, operationId: body.operation_id});
  h.handle(async () => committed.result);
  await h.controller.run(scope, {...h.hooks, prepare: async () => ({...body, operation_id: "new-explicit-copy"})});
  assert.equal(h.calls.at(-1).body.operation_id, "new-explicit-copy");
  assert.equal(h.controller.list(scope)[0].confirmed, true);
});
