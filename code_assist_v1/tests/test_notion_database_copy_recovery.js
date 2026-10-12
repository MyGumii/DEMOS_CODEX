'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const Copy = require('../notion_static/notion_database_copy.js');
const {memory, lockManager} = require('./notion_page_copy_test_helpers.js');

const scope = {userId:'owner', workspaceId:'ws', pageId:'page', databaseId:'db-source'};
const request = {operation_id:'database-copy-op', expected_document_sequence:7,
  expected_workspace_epoch:2, expected_document_epoch:3, include_content:true};
const result = {contract:'code_assist.notion.owned_database_copy.v1', workspace_id:'ws',
  document_id:'page', source_database_id:'db-source', database_id:'db-copy',
  block_id:'block-copy', include_content:true};
const committed = {state:'committed', result, current:{archived:false}};

function harness(saved = memory()) {
  const calls = [], locks = lockManager();
  let handler = async url => url.endsWith('-result') ? committed : result;
  let ready = true, prepared = 0, retried = 0;
  const hooks = {includeContent:true, current:() => ready,
    prepare:async () => {prepared++; return request;},
    beforeRetry:async () => {retried++;}};
  const controller = Copy.create({storage:() => saved, locks:() => locks,
    transport:async (url, options) => {calls.push([url, options]); return handler(url, options);}});
  return {controller, hooks, calls, saved, handler:value => {handler = value;},
    current:value => {ready = value;}, prepared:() => prepared, retried:() => retried};
}

test('database copy stores an immutable request before POST and confirms exact identities', async () => {
  const h = harness();
  h.handler(async (_url, options) => {
    assert.deepEqual(h.controller.list(scope)[0].request, request);
    assert.deepEqual(options.body, request);
    return result;
  });
  const saved = await h.controller.run(scope, h.hooks);
  assert.equal(saved.state, 'committed');
  assert.equal(h.prepared(), 1);
  assert.equal(h.controller.list(scope)[0].destinationId, 'db-copy');
  assert.equal(h.controller.pending(scope), false);
  assert.equal(await h.controller.acknowledge(scope, request.operation_id), true);
  assert.deepEqual(h.controller.list(scope), []);
});

test('lost response is recovered by receipt lookup without a second POST', async () => {
  const h = harness();
  h.handler(async () => {throw Error('response lost');});
  await assert.rejects(h.controller.run(scope, h.hooks), /response lost/);
  const reload = harness(h.saved);
  reload.hooks.prepare = reload.hooks.beforeRetry = async () => {throw Error('draft is unavailable');};
  reload.handler(async url => {
    assert.match(url, /copy-result$/);
    return committed;
  });
  const recovered = await reload.controller.run(scope, reload.hooks);
  assert.equal(recovered.result.database_id, 'db-copy');
  assert.equal(reload.calls.length, 1);
  assert.equal(reload.prepared(), 0);
});

test('unknown receipt retries the same mode, operation and fences', async () => {
  const h = harness();
  h.handler(async () => {throw Error('network');});
  await assert.rejects(h.controller.run(scope, h.hooks));
  const reload = harness(h.saved);
  reload.handler(async url => url.endsWith('-result') ? {state:'unknown'} : result);
  await reload.controller.run(scope, reload.hooks);
  assert.equal(reload.retried(), 1);
  assert.equal(reload.prepared(), 0);
  assert.deepEqual(reload.calls.map(([, options]) => options.body), [request, request]);
  await assert.rejects(reload.controller.run(scope, {...reload.hooks, includeContent:false}),
    /다른 복제 요청/);
});

test('invalid result and denied storage never send a fresh duplicate', async () => {
  const h = harness();
  h.handler(async () => ({...result, document_id:'another-page'}));
  await assert.rejects(h.controller.run(scope, h.hooks), /일치하지/);
  assert.equal(h.controller.list(scope)[0].confirmed, false);
  const unavailable = harness(null);
  await assert.rejects(unavailable.controller.run(scope, unavailable.hooks));
  assert.equal(unavailable.calls.length, 0);
});
