'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const Trash = require('../notion_static/notion_database_trash.js');
const {memory, lockManager} = require('./notion_page_copy_test_helpers.js');

const scope = {userId:'owner', workspaceId:'ws', documentId:'page', databaseId:'db'};
const request = {document_id:'page', archived:true, operation_id:'database-trash-op',
  expected_document_sequence:7, expected_workspace_epoch:2,
  expected_document_epoch:4, expected_database_version:3};
const result = {contract:'code_assist.notion.owned_database_trash.v1',
  workspace_id:'ws', document_id:'page', database_id:'db', block_id:'db-block',
  archived:true, operation_id:'database-trash-op'};
const committed = {state:'committed', result, current:{archived:true}};

function harness(saved = memory(), locks = lockManager()) {
  const calls = [];
  let handler = async url => url.endsWith('/lifecycle-result') ? committed : result;
  let ready = true, prepared = 0, retried = 0;
  const hooks = {archived:true, current:() => ready,
    prepare:async () => {prepared++; return request;},
    beforeRetry:async () => {retried++;}};
  const controller = Trash.create({storage:() => saved, locks:() => locks,
    transport:async (url, options) => {calls.push([url, options]); return handler(url, options);}});
  return {controller, hooks, calls, saved, handler:value => {handler = value;},
    prepared:() => prepared, retried:() => retried};
}

test('database Trash stores exact request before POST and acknowledges receipt', async () => {
  const h = harness();
  h.handler(async (_url, options) => {
    assert.deepEqual(h.controller.list(scope)[0].request, request);
    assert.deepEqual(options.body, Object.fromEntries(Object.entries(request)
      .filter(([key]) => key !== 'archived')));
    return result;
  });
  assert.equal((await h.controller.run(scope, h.hooks)).state, 'committed');
  assert.equal(h.prepared(), 1);
  assert.equal(await h.controller.acknowledge(scope, request.operation_id), true);
  assert.deepEqual(h.controller.list(scope), []);
});

test('lost Trash response recovers through receipt without second mutation', async () => {
  const h = harness();
  h.handler(async () => {throw Error('response lost');});
  await assert.rejects(h.controller.run(scope, h.hooks), /response lost/);
  const reload = harness(h.saved);
  reload.hooks.prepare = reload.hooks.beforeRetry = async () => {throw Error('draft unavailable');};
  reload.handler(async url => {assert.match(url, /lifecycle-result$/); return committed;});
  const recovered = await reload.controller.run(scope, reload.hooks);
  assert.equal(recovered.result.operation_id, request.operation_id);
  assert.equal(reload.calls.length, 1);
  assert.equal(reload.prepared(), 0);
});

test('unknown receipt retries same operation and blocks opposite action', async () => {
  const h = harness();
  h.handler(async () => {throw Error('network');});
  await assert.rejects(h.controller.run(scope, h.hooks));
  const reload = harness(h.saved);
  await assert.rejects(reload.controller.run(scope, {...reload.hooks, archived:false}),
    /반대 작업/);
  reload.handler(async url => url.endsWith('/lifecycle-result') ? {state:'unknown'} : result);
  await reload.controller.run(scope, reload.hooks);
  assert.equal(reload.retried(), 1);
  assert.equal(reload.prepared(), 0);
  assert.equal(reload.calls[0][1].body.operation_id, request.operation_id);
  assert.equal(reload.calls[1][1].body.operation_id, request.operation_id);
});

test('confirmed archive permits restore without an extra acknowledgment click', async () => {
  const h = harness();
  await h.controller.run(scope, h.hooks);
  const restoreRequest = {...request, archived:false, operation_id:'database-restore-op',
    expected_document_sequence:9, expected_database_version:4};
  const restoreResult = {...result, archived:false, operation_id:'database-restore-op'};
  h.handler(async url => url.endsWith('/restore') ? restoreResult : committed);
  const restored = await h.controller.run(scope, {...h.hooks, archived:false,
    prepare:async () => restoreRequest});
  assert.equal(restored.result.archived, false);
  assert.deepEqual(h.controller.list(scope)[0].request, restoreRequest);
});

test('invalid result and unavailable storage do not send a new mutation', async () => {
  const h = harness();
  h.handler(async () => ({...result, database_id:'other'}));
  await assert.rejects(h.controller.run(scope, h.hooks), /일치하지/);
  assert.equal(h.controller.list(scope)[0].confirmed, false);
  const unavailable = harness(null);
  await assert.rejects(unavailable.controller.run(scope, unavailable.hooks));
  assert.equal(unavailable.calls.length, 0);
});

test('another tab holding the Trash lock cannot duplicate POST', async () => {
  const saved = memory(), locks = lockManager();
  const first = harness(saved, locks), second = harness(saved, locks);
  let started, finish;
  const entered = new Promise(resolve => {started = resolve;});
  const blocked = new Promise(resolve => {finish = resolve;});
  first.handler(async () => {started(); return blocked;});
  const pending = first.controller.run(scope, first.hooks);
  await entered;
  assert.deepEqual(await second.controller.run(scope, second.hooks), {state:'busy'});
  assert.equal(second.calls.length, 0);
  finish(result);
  assert.equal((await pending).state, 'committed');
});
