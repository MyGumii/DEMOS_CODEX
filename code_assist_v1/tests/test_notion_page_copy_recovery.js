"use strict";
const assert = require("node:assert/strict");
const {test} = require("node:test");
const Copy = require("../notion_static/notion_page_copy.js");
const {lockManager} = require("./notion_page_copy_test_helpers.js");
const scope = {userId: "owner", workspaceId: "ws", pageId: "source"};
const request = {operation_id: "copy-op-1", expected_document_sequence: 7, expected_workspace_epoch: 2, expected_document_epoch: 3};
const result = {contract: "code_assist.notion.canonical_page_copy.v1", source_page_id: "source",
  page: {id: "copy", workspace_id: "ws"}, page_ids: ["copy", "child"], workspace: {id: "ws", version: 4}};
const committed = {state: "committed", result, current: {archived: false}};
const tick = () => new Promise(resolve => setImmediate(resolve));
function memory() {
  const data = new Map();
  return {get length() {return data.size;}, key: i => [...data.keys()][i],
    getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key)};
}
function harness(saved = memory()) {
  const calls = [], intentCalls = []; let current = true, prepared = 0, retries = 0;
  const hooks = {current: () => current, prepare: async () => {prepared++; return request;}, beforeRetry: async () => {retries++;}};
  let handler = async url => url.endsWith("-result") ? committed : result;
  let intentHandler = async (_url, options) => ({state: "pending", source_page_id: scope.pageId, request: options.body});
  const locks = lockManager();
  const controller = Copy.create({storage: () => saved, locks: () => locks, transport: async (url, options) => {
    if (url.endsWith("-intent")) {intentCalls.push([url, options]); return intentHandler(url, options);}
    calls.push([url, options]); return handler(url, options);
  }});
  return {controller, hooks, calls, intentCalls, saved, prepared: () => prepared, retries: () => retries,
    transport: value => {handler = value;}, intent: value => {intentHandler = value;}, current: value => {current = value;}};
}

test("durable request precedes mutation; duplicate activation shares one flight", async () => {
  const h = harness(); let finish;
  h.transport(async (_url, options) => {
    assert.deepEqual(h.controller.list(scope)[0].request, request);
    assert.deepEqual(options.body, request);
    return new Promise(resolve => {finish = resolve;});
  });
  const first = h.controller.run(scope, h.hooks);
  assert.equal(h.controller.run(scope, h.hooks), first);
  await tick(); assert.equal(h.controller.pending(scope), true);
  finish(result); await first;
  assert.equal(h.calls.length, 1); assert.equal(h.prepared(), 1);
  assert.equal(h.controller.pending(scope), false);
  assert.equal(h.controller.list(scope)[0].confirmed, true);
  assert.equal(h.intentCalls.length, 1);
  assert.deepEqual(h.intentCalls[0][1].body, request);
});

test("lost intent acknowledgement keeps the request and never dispatches a copy", async () => {
  const h = harness(); h.intent(async () => {throw Error("intent response lost");});
  await assert.rejects(h.controller.run(scope, h.hooks), /intent response lost/);
  assert.equal(h.calls.length, 0);
  assert.deepEqual(h.controller.list(scope)[0].request, request);
  const reopened = harness(h.saved);
  reopened.transport(async url => url.endsWith("-result") ? {state: "unknown"} : result);
  assert.equal((await reopened.controller.run(scope, reopened.hooks)).state, "committed");
  assert.equal(reopened.intentCalls.length, 1);
  assert.equal(reopened.calls.filter(([url]) => url.endsWith("/duplicate")).length, 1);
  assert.equal(reopened.prepared(), 0);
});

test("invalid server intent identity cannot dispatch a copy", async () => {
  const h = harness(); h.intent(async () => ({state: "pending", source_page_id: "other", request}));
  await assert.rejects(h.controller.run(scope, h.hooks), /서버 기록/);
  assert.equal(h.calls.length, 0);
  assert.equal(h.controller.pending(scope), true);
});

test("server-discovered intent is adopted without replacing another unfinished local request", async () => {
  const h = harness();
  assert.equal(await h.controller.adopt(scope, request), true);
  assert.deepEqual(h.controller.list(scope)[0].request, request);
  assert.equal(await h.controller.adopt(scope, request), true);
  await assert.rejects(h.controller.adopt(scope, {...request, operation_id: "other"}),
    /다른 복제 요청/);
  assert.deepEqual(h.controller.list(scope)[0].request, request);
  assert.equal(h.calls.length, 0);
});

test("lost response, reload, archived source: receipt lookup needs no editor flush", async () => {
  const h = harness(); h.transport(async () => {throw Error("response lost after commit");});
  await assert.rejects(h.controller.run(scope, h.hooks), /response lost/);
  const reload = harness(h.saved);
  reload.hooks.prepare = reload.hooks.beforeRetry = async () => {throw Error("source is unavailable and draft is blocked");};
  reload.transport(async () => ({...committed, current: {archived: true}}));
  const recovered = await reload.controller.run(scope, reload.hooks);
  assert.equal(recovered.current.archived, true);
  assert.equal(reload.calls.length, 1); assert.match(reload.calls[0][0], /duplicate-result$/);
  assert.deepEqual(reload.calls[0][1].body, request);
  assert.equal(reload.controller.list(scope)[0].destinationId, "copy");
});

test("unknown receipt retries exact request, not fresh stamps or operation", async () => {
  const h = harness(); h.transport(async () => {throw Error("network");});
  await assert.rejects(h.controller.run(scope, h.hooks));
  const reload = harness(h.saved);
  reload.hooks.prepare = async () => {throw Error("must not replace request");};
  reload.transport(async url => url.endsWith("-result") ? {state: "unknown"} : result);
  await reload.controller.run(scope, reload.hooks);
  assert.equal(reload.retries(), 1); assert.equal(reload.calls.length, 2);
  for (const [, options] of reload.calls) assert.deepEqual(options.body, request);
});

test("read-only result check and blocked drafts never resend", async () => {
  const h = harness(); h.transport(async () => {throw Error("network");});
  await assert.rejects(h.controller.run(scope, h.hooks));
  h.transport(async () => ({state: "unknown"}));
  const before = h.calls.length;
  assert.deepEqual(await h.controller.run(scope, {...h.hooks, lookupOnly: true}), {state: "unknown"});
  assert.equal(h.calls.length, before + 1); assert.equal(h.retries(), 0);
  h.hooks.beforeRetry = async () => {throw Error("IME or blocked draft");};
  await assert.rejects(h.controller.run(scope, h.hooks), /IME/);
  assert.equal(h.calls.length, before + 2); assert.equal(h.controller.pending(scope), true);
});

test("storage denied, silent write failure and malformed records fail before POST", async () => {
  for (const saved of [null, {...memory(), getItem: () => null, setItem() {}}, {...memory(), getItem: () => "{broken"}]) {
    const h = harness(saved); await assert.rejects(h.controller.run(scope, h.hooks));
    assert.equal(h.calls.length, 0);
  }
  const h = harness(); const original = h.saved.setItem;
  h.saved.setItem = () => {throw Error("quota");};
  await assert.rejects(h.controller.run(scope, h.hooks)); assert.equal(h.calls.length, 0);
  h.saved.setItem = original;
  await h.controller.run(scope, h.hooks); assert.equal(h.calls.length, 1);
});

test("late commit persists for original account; other scope cannot list or acknowledge it", async () => {
  const h = harness(); let finish;
  h.transport(async () => new Promise(resolve => {finish = resolve;}));
  const pending = h.controller.run(scope, h.hooks); await tick();
  h.current(false); finish(result); await pending;
  assert.equal(h.controller.list(scope)[0].confirmed, true);
  assert.deepEqual(h.controller.list({...scope, userId: "other"}), []);
  assert.deepEqual(h.controller.list({...scope, workspaceId: "other"}), []);
  assert.equal(await h.controller.acknowledge({...scope, userId: "other"}, request.operation_id), false);
});

test("navigation during prepare never sends or persists a new copy", async () => {
  const h = harness(); h.hooks.prepare = async () => {h.current(false); return request;};
  await assert.rejects(h.controller.run(scope, h.hooks), /화면/);
  assert.equal(h.calls.length, 0); assert.deepEqual(h.controller.list(scope), []);
});

test("wrong receipt identities and malformed status never confirm or create another copy", async () => {
  const h = harness(); h.transport(async () => {throw Error("network");});
  await assert.rejects(h.controller.run(scope, h.hooks));
  for (const bad of [null, {state: "committed", result}, {...committed, result: {...result, source_page_id: "other"}},
    {...committed, result: {...result, page: {...result.page, workspace_id: "other"}}}]) {
    h.transport(async () => bad); await assert.rejects(h.controller.run(scope, h.hooks));
    assert.equal(h.controller.list(scope)[0].confirmed, false);
    assert.match(h.calls.at(-1)[0], /duplicate-result$/);
  }
});

test("confirmed receipt is reauthorized after reload and cannot become a new mutation", async () => {
  const h = harness(); await h.controller.run(scope, h.hooks);
  const reload = harness(h.saved); reload.transport(async () => ({state: "unknown"}));
  await assert.rejects(reload.controller.run(scope, reload.hooks));
  assert.equal(reload.calls.length, 1); assert.match(reload.calls[0][0], /duplicate-result$/);
  reload.transport(async () => {throw Object.assign(Error("ownership revoked"), {status: 404});});
  await assert.rejects(reload.controller.run(scope, reload.hooks), /ownership revoked/);
  assert.equal(reload.controller.list(scope)[0].confirmed, true);
});

test("pre-registration rejection releases the local request; post-registration rejection keeps it recoverable", async () => {
  const refused = harness();
  refused.intent(async () => {throw Object.assign(Error("registration refused"), {status: 409});});
  await assert.rejects(refused.controller.run(scope, refused.hooks), /registration refused/);
  assert.equal(refused.controller.list(scope).length, 0);
  assert.equal(refused.calls.length, 0);

  const h = harness();
  h.transport(async () => {throw Object.assign(Error("unsupported source shape"), {status: 422});});
  await assert.rejects(h.controller.run(scope, h.hooks), /unsupported source shape/);
  assert.deepEqual(h.controller.list(scope)[0].request, request);
  const reopened = harness(h.saved);
  reopened.transport(async url => url.endsWith("-result") ? {state: "unknown"}
    : Promise.reject(Object.assign(Error("source changed"), {status: 409})));
  await assert.rejects(reopened.controller.run(scope, reopened.hooks), /source changed/);
  assert.deepEqual(reopened.controller.list(scope)[0].request, request);
  assert.equal(reopened.prepared(), 0);
  assert.equal(await reopened.controller.acknowledge(scope, request.operation_id), false);
});

test("acknowledgement is exact and confirmed; failed marker write cannot permit another operation", async () => {
  const h = harness(); await h.controller.run(scope, h.hooks);
  assert.equal(await h.controller.acknowledge(scope, "old-op"), false);
  const original = h.saved.setItem; h.saved.setItem = () => {};
  await assert.rejects(h.controller.acknowledge(scope, request.operation_id));
  assert.equal(h.controller.list(scope).length, 1);
  h.saved.setItem = original;
  assert.equal(await h.controller.acknowledge(scope, request.operation_id), true);
  assert.deepEqual(h.controller.list(scope), []);
});
