"use strict";
const assert = require("node:assert/strict"), {test} = require("node:test");
const Copy = require("../notion_static/notion_page_copy.js");
const {memory, lockManager} = require("./notion_page_copy_test_helpers.js");
const scope = {userId: "owner", workspaceId: "ws", pageId: "source"};
const tick = () => new Promise(resolve => setImmediate(resolve));
function fixture() {
  const storage = memory(), locks = lockManager(), receipts = new Map(), calls = [];
  let count = 0, committed = 0, response = async result => result;
  const hooks = {current: () => true, prepare: async () => ({operation_id: `copy-${++count}`,
    expected_document_sequence: 7, expected_workspace_epoch: 2, expected_document_epoch: 3}), beforeRetry: async () => {}};
  const transport = async (url, {body}) => {
    if (url.endsWith("-intent")) return {state: "pending", source_page_id: scope.pageId, request: body};
    calls.push({url, operation: body.operation_id});
    if (url.endsWith("-result")) return receipts.has(body.operation_id)
      ? {state: "committed", result: receipts.get(body.operation_id), current: {archived: false}} : {state: "unknown"};
    let result = receipts.get(body.operation_id);
    if (!result) {
      result = {contract: "code_assist.notion.canonical_page_copy.v1", source_page_id: "source", workspace: {id: "ws"},
        page: {id: `destination-${++committed}`, workspace_id: "ws"}, page_ids: [`destination-${committed}`]};
      receipts.set(body.operation_id, result);
    }
    return response(result);
  };
  const controller = (options = {}) => Copy.create({storage: () => storage, locks: () => locks, transport, ...options});
  return {storage, locks, hooks, calls, controller, receipts, copies: () => committed, preparations: () => count,
    respond: handler => {response = handler;}};
}

test("two independent controllers share storage and only one sends a mutation while locked", async () => {
  const f = fixture(), a = f.controller(), b = f.controller(); let finish;
  f.respond(result => new Promise(resolve => {finish = () => resolve(result);}));
  const first = a.run(scope, f.hooks); await tick();
  assert.deepEqual(await b.run(scope, f.hooks), {state: "busy"});
  assert.equal(f.preparations(), 1); assert.equal(f.copies(), 1); assert.equal(f.calls.length, 1);
  finish(); await first;
  await b.run(scope, f.hooks);
  assert.equal(f.calls.length, 2); assert.match(f.calls[1].url, /duplicate-result$/);
  assert.equal(f.copies(), 1); assert.equal(b.list(scope)[0].destinationId, "destination-1");
});

test("closing the originating controller leaves shared receipt discovery for a new controller", async () => {
  const f = fixture(); f.respond(async () => {throw Error("response lost");});
  await assert.rejects(f.controller().run(scope, f.hooks));
  const reopened = f.controller(); assert.equal(reopened.list(scope).length, 1);
  const noFlush = {...f.hooks, prepare: async () => {throw Error("closed editor");}, beforeRetry: async () => {throw Error("blocked editor");}};
  assert.equal((await reopened.run(scope, noFlush)).state, "committed");
  assert.equal(f.copies(), 1); assert.match(f.calls.at(-1).url, /duplicate-result$/);
});

test("a delayed activation issued before acknowledgement cannot start a second copy", async () => {
  const f = fixture(), a = f.controller(); await a.run(scope, f.hooks);
  const operation = a.list(scope)[0].request.operation_id; let grant;
  const b = f.controller({locks: () => ({request: (...args) => new Promise(resolve => {
    grant = () => resolve(f.locks.request(...args));
  })})});
  const delayed = b.run(scope, f.hooks); await tick();
  assert.equal(await a.acknowledge(scope, operation), true);
  assert.equal(a.list(scope).length, 0); assert.equal(f.storage.length, 1, "terminal marker is retained");
  grant(); assert.equal((await delayed).state, "committed");
  assert.equal(f.copies(), 1); assert.equal(a.list(scope).length, 0, "late receipt does not reopen acknowledged banner");
  await a.run(scope, f.hooks); assert.equal(f.copies(), 2, "a new explicit activation after acknowledgement starts the next copy");
});

test("delayed activation cannot recreate a definitively rejected operation", async () => {
  const f = fixture(); let grant, attempts = 0;
  const reject = async () => {attempts++; throw Object.assign(Error("source changed"), {status: 409});};
  const a = f.controller({transport: reject});
  const b = f.controller({transport: reject, locks: () => ({request: (...args) => new Promise(resolve => {
    grant = () => resolve(f.locks.request(...args));
  })})});
  const delayed = b.run(scope, f.hooks); await tick();
  await assert.rejects(a.run(scope, f.hooks)); grant();
  await assert.rejects(delayed, /거절/); assert.equal(attempts, 1);
  assert.equal(a.pending(scope), false);
  await assert.rejects(a.run(scope, f.hooks)); assert.equal(attempts, 2, "new explicit retry is allowed");
});

test("old recovery control cannot act on a newer source operation", async () => {
  const f = fixture(), a = f.controller(); await a.run(scope, f.hooks);
  const original = a.list(scope)[0].request.operation_id;
  await a.acknowledge(scope, original); await a.run(scope, f.hooks);
  const before = f.calls.length;
  await assert.rejects(a.run(scope, {...f.hooks, operationId: original}), /요청이 바뀌/);
  assert.equal(f.calls.length, before); assert.equal(await a.acknowledge(scope, original), false);
  assert.equal(a.list(scope)[0].request.operation_id, "copy-2");
});

test("missing or denied Web Locks never fall back to uncoordinated writes", async () => {
  for (const manager of [null, {}, {request: async () => {throw Error("denied");}}]) {
    const f = fixture(), a = f.controller({locks: () => manager});
    await assert.rejects(a.run(scope, f.hooks)); assert.equal(f.calls.length, 0); assert.equal(f.storage.length, 0);
  }
});

test("storage notifications re-read actual shared records and ignore unrelated origin areas", async () => {
  const f = fixture(); let changes = 0;
  const b = f.controller({changed: () => {changes++;}});
  await f.controller().run(scope, f.hooks);
  b.observe({key: "notion.colorTheme", storageArea: f.storage}); assert.equal(changes, 0);
  b.observe({key: f.storage.key(0), storageArea: memory()}); assert.equal(changes, 0);
  b.observe({key: f.storage.key(0), storageArea: f.storage, newValue: "malicious or stale event content"});
  assert.equal(changes, 1); assert.equal(b.list(scope)[0].confirmed, true);
  b.observe({key: null, storageArea: f.storage}); assert.equal(changes, 2);
  assert.deepEqual(b.list({...scope, userId: "other"}), []);
});

test("legacy tab-local intent migrates under lock only after verified shared persistence", async () => {
  const f = fixture(), old = memory(); await f.controller().run(scope, f.hooks);
  const key = f.storage.key(0), value = f.storage.getItem(key);
  old.setItem(key, value); f.storage.removeItem(key);
  const a = f.controller({legacyStorage: () => old}); assert.equal(a.list(scope).length, 1);
  await a.run(scope, f.hooks);
  assert.equal(old.length, 0); assert.equal(a.list(scope)[0].request.operation_id, "copy-1");
  assert.equal(f.copies(), 1); assert.match(f.calls.at(-1).url, /duplicate-result$/);
});

test("legacy migration preserves both records on mismatch or shared write failure", async () => {
  const f = fixture(), old = memory(); await f.controller().run(scope, f.hooks);
  const key = f.storage.key(0), current = f.storage.getItem(key), stale = JSON.parse(current);
  stale.request.operation_id = "different-original"; old.setItem(key, JSON.stringify(stale));
  const a = f.controller({legacyStorage: () => old}), before = f.calls.length;
  await assert.rejects(a.run(scope, f.hooks), /서로 다른/);
  assert.equal(f.storage.getItem(key), current); assert.equal(old.length, 1); assert.equal(f.calls.length, before);
  old.setItem(key, current); f.storage.removeItem(key);
  f.storage.setItem = () => {throw Error("quota");};
  await assert.rejects(a.run(scope, f.hooks));
  assert.equal(old.getItem(key), current); assert.equal(f.calls.length, before);
});

test("acknowledgement cannot release another controller's in-flight operation", async () => {
  const f = fixture(), a = f.controller(), b = f.controller(); let finish;
  f.respond(result => new Promise(resolve => {finish = () => resolve(result);}));
  const first = a.run(scope, f.hooks); await tick();
  const operation = b.list(scope)[0].request.operation_id;
  assert.equal(await b.acknowledge(scope, operation), false); finish(); await first;
  assert.equal(b.list(scope)[0].confirmed, true);
});
