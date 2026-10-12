'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const Move = require('../notion_static/notion_database_move.js');
const {memory, lockManager} = require('./notion_page_copy_test_helpers.js');

const scope = {userId:'owner', workspaceId:'ws', sourceDocumentId:'page-src', databaseId:'db'};
const request = {source_document_id:'page-src', destination_document_id:'page-dst',
  operation_id:'database-move-op', expected_source_sequence:7,
  expected_destination_sequence:3, expected_workspace_epoch:2,
  expected_source_epoch:4, expected_destination_epoch:5};
const result = {contract:'code_assist.notion.owned_database_move.v1', workspace_id:'ws',
  source_document_id:'page-src', destination_document_id:'page-dst', database_id:'db',
  block_id:'db-block', operation_id:'database-move-op'};
const committed = {state:'committed', result, current:{destination_archived:false}};

function harness(saved = memory(), locks = lockManager()) {
  const calls = [];
  let handler = async url => url.endsWith('-result') ? committed : result;
  let ready = true, prepared = 0, retried = 0;
  const hooks = {destinationId:'page-dst', current:() => ready,
    prepare:async () => {prepared++; return request;},
    beforeRetry:async () => {retried++;}};
  const controller = Move.create({storage:() => saved, locks:() => locks,
    transport:async (url, options) => {calls.push([url, options]); return handler(url, options);}});
  return {controller, hooks, calls, saved, handler:value => {handler = value;},
    current:value => {ready = value;}, prepared:() => prepared, retried:() => retried};
}

test('database move saves the exact request before POST and confirms identities', async () => {
  const h = harness();
  h.handler(async (_url, options) => {
    assert.deepEqual(h.controller.list(scope)[0].request, request);
    assert.deepEqual(options.body, request);
    return result;
  });
  const saved = await h.controller.run(scope, h.hooks);
  assert.equal(saved.state, 'committed');
  assert.equal(h.prepared(), 1);
  assert.equal(h.controller.list(scope)[0].request.destination_document_id, 'page-dst');
  assert.equal(h.controller.pending(scope), false);
  assert.equal(await h.controller.acknowledge(scope, request.operation_id), true);
  assert.deepEqual(h.controller.list(scope), []);
});

test('lost move response recovers via receipt without another POST', async () => {
  const h = harness();
  h.handler(async () => {throw Error('response lost');});
  await assert.rejects(h.controller.run(scope, h.hooks), /response lost/);
  const reload = harness(h.saved);
  reload.hooks.prepare = reload.hooks.beforeRetry = async () => {throw Error('draft unavailable');};
  reload.handler(async url => {assert.match(url, /move-result$/); return committed;});
  const recovered = await reload.controller.run(scope, reload.hooks);
  assert.equal(recovered.result.database_id, 'db');
  assert.equal(reload.calls.length, 1);
  assert.equal(reload.prepared(), 0);
});

test('unknown move receipt retries the same destination, operation and fences', async () => {
  const h = harness();
  h.handler(async () => {throw Error('network');});
  await assert.rejects(h.controller.run(scope, h.hooks));
  const reload = harness(h.saved);
  reload.handler(async url => url.endsWith('-result') ? {state:'unknown'} : result);
  await reload.controller.run(scope, reload.hooks);
  assert.equal(reload.retried(), 1);
  assert.equal(reload.prepared(), 0);
  assert.deepEqual(reload.calls.map(([, options]) => options.body), [request, request]);
  await assert.rejects(reload.controller.run(scope, {...reload.hooks, destinationId:'other-page'}),
    /다른 이동 요청/);
});

test('invalid move result or unavailable storage never starts a fresh move', async () => {
  const h = harness();
  h.handler(async () => ({...result, destination_document_id:'other-page'}));
  await assert.rejects(h.controller.run(scope, h.hooks), /일치하지/);
  assert.equal(h.controller.list(scope)[0].confirmed, false);
  const unavailable = harness(null);
  await assert.rejects(unavailable.controller.run(scope, unavailable.hooks));
  assert.equal(unavailable.calls.length, 0);
});

test('another tab holding the move lock cannot send a duplicate POST', async () => {
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
