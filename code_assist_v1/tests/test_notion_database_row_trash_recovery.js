'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const Trash = require('../notion_static/notion_database_trash.js');
const {memory, lockManager} = require('./notion_page_copy_test_helpers.js');

const scope = {userId:'owner', workspaceId:'ws', documentId:'page', databaseId:'db', rowId:'row'};
const request = {document_id:'page', archived:true, operation_id:'row-trash-op',
  expected_document_sequence:7, expected_workspace_epoch:2,
  expected_document_epoch:4, expected_database_version:3, expected_row_version:5};
const result = {contract:'code_assist.notion.database_row_trash.v1',
  workspace_id:'ws', document_id:'page', database_id:'db', row_id:'row',
  archived:true, operation_id:'row-trash-op'};

function harness(saved = memory(), locks = lockManager()) {
  const calls = [];
  let handler = async url => url.endsWith('/lifecycle-result')
    ? {state:'committed', result} : result;
  const controller = Trash.createRow({storage:() => saved, locks:() => locks,
    transport:async (url, options) => {calls.push([url, options]); return handler(url, options);}});
  const hooks = {archived:true, current:() => true,
    prepare:async () => request, beforeRetry:async () => {}};
  return {controller, hooks, calls, saved, handler:value => {handler = value;}};
}

test('row Trash stores exact request and targets one row', async () => {
  const h = harness();
  h.handler(async (url, options) => {
    assert.match(url, /\/databases\/db\/rows\/row\/trash$/);
    assert.deepEqual(h.controller.list(scope)[0].request, request);
    assert.deepEqual(options.body, Object.fromEntries(Object.entries(request)
      .filter(([key]) => key !== 'archived')));
    return result;
  });
  assert.equal((await h.controller.run(scope, h.hooks)).state, 'committed');
  assert.equal(await h.controller.acknowledge(scope, request.operation_id), true);
  assert.deepEqual(h.controller.list(scope), []);
});

test('lost row Trash response recovers without a second mutation', async () => {
  const h = harness();
  h.handler(async () => {throw Error('response lost');});
  await assert.rejects(h.controller.run(scope, h.hooks), /response lost/);
  const reload = harness(h.saved);
  reload.hooks.prepare = reload.hooks.beforeRetry = async () => {throw Error('must not prepare');};
  reload.handler(async url => {
    assert.match(url, /\/rows\/row\/lifecycle-result$/);
    return {state:'committed', result};
  });
  assert.equal((await reload.controller.run(scope, reload.hooks)).state, 'committed');
  assert.equal(reload.calls.length, 1);
});

test('row identity mismatch never confirms another row receipt', async () => {
  const h = harness();
  h.handler(async () => ({...result, row_id:'other'}));
  await assert.rejects(h.controller.run(scope, h.hooks), /일치하지/);
  assert.equal(h.controller.list(scope)[0].confirmed, false);
});

test('two tabs cannot submit the same row action together', async () => {
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

test('selected rows retain separate recovery receipts', async () => {
  const h = harness();
  await h.controller.run(scope, h.hooks);
  const second = {...scope, rowId:'row-two'};
  const secondRequest = {...request, operation_id:'row-two-trash-op'};
  h.handler(async url => url.endsWith('/lifecycle-result')
    ? {state:'unknown'} : {...result, row_id:'row-two', operation_id:'row-two-trash-op'});
  await h.controller.run(second, {...h.hooks, prepare:async () => secondRequest});
  assert.deepEqual(h.controller.list(scope).map(item => item.rowId).sort(),
    ['row', 'row-two']);
});

test('row and whole-database receipts use separate storage keys', async () => {
  const saved = memory(), locks = lockManager();
  const row = harness(saved, locks);
  await row.controller.run(scope, row.hooks);
  const database = Trash.create({storage:() => saved, locks:() => locks,
    transport:async () => {throw Error('no transport needed');}});
  assert.deepEqual(database.list(scope), []);
  assert.equal(row.controller.list(scope).length, 1);
});

test('row title remains with the recovery receipt after reload', async () => {
  const h = harness();
  await h.controller.run(scope, {...h.hooks, label:'첫 행'});
  assert.equal(harness(h.saved).controller.list(scope)[0].label, '첫 행');
});

const batchScope = {userId:'owner', workspaceId:'ws', documentId:'page', databaseId:'db'};
const batchRequest = {document_id:'page', archived:true, operation_id:'rows-trash-op',
  expected_document_sequence:7, expected_workspace_epoch:2,
  expected_document_epoch:4, expected_database_version:3,
  rows:[{row_id:'row', expected_row_version:5},
    {row_id:'row-two', expected_row_version:2}]};
const batchResult = {contract:'code_assist.notion.database_row_trash_batch.v1',
  workspace_id:'ws', document_id:'page', database_id:'db', archived:true,
  operation_id:'rows-trash-op', rows:[{row_id:'row', row_version:6},
    {row_id:'row-two', row_version:3}]};

function batchHarness(saved = memory(), locks = lockManager()) {
  const calls = [];
  let handler = async () => batchResult;
  const controller = Trash.createBatch({storage:() => saved, locks:() => locks,
    transport:async (url, options) => {calls.push([url, options]); return handler(url, options);}});
  const hooks = {archived:true, expectedRowIds:['row', 'row-two'],
    replaceConfirmed:true, current:() => true, prepare:async () => batchRequest,
    beforeRetry:async () => {}};
  return {controller, hooks, calls, saved, handler:value => {handler = value;}};
}

test('selected rows use one exact durable batch request', async () => {
  const h = batchHarness();
  h.handler(async (url, options) => {
    assert.match(url, /\/databases\/db\/rows\/trash-batch$/);
    assert.deepEqual(h.controller.list(batchScope)[0].request, batchRequest);
    assert.deepEqual(options.body, Object.fromEntries(Object.entries(batchRequest)
      .filter(([key]) => key !== 'archived')));
    return batchResult;
  });
  assert.equal((await h.controller.run(batchScope, h.hooks)).state, 'committed');
  assert.equal(h.calls.length, 1);
  assert.equal(await h.controller.acknowledge(batchScope, batchRequest.operation_id), true);
  assert.deepEqual(h.controller.list(batchScope), []);
});

test('lost batch response recovers the same receipt without another mutation', async () => {
  const h = batchHarness();
  h.handler(async () => {throw Error('response lost');});
  await assert.rejects(h.controller.run(batchScope, h.hooks), /response lost/);
  const reload = batchHarness(h.saved);
  reload.hooks.prepare = reload.hooks.beforeRetry = async () => {throw Error('must not prepare');};
  reload.handler(async url => {
    assert.match(url, /\/rows\/trash-batch\/lifecycle-result$/);
    return {state:'committed', result:batchResult};
  });
  assert.equal((await reload.controller.run(batchScope, reload.hooks)).state, 'committed');
  assert.equal(reload.calls.length, 1);
});

test('batch receipt rejects a different row and blocks a different pending selection', async () => {
  const h = batchHarness();
  h.handler(async () => ({...batchResult, rows:[batchResult.rows[1], batchResult.rows[0]]}));
  await assert.rejects(h.controller.run(batchScope, h.hooks), /일치하지/);
  assert.equal(h.controller.list(batchScope)[0].confirmed, false);
  await assert.rejects(h.controller.run(batchScope,
    {...h.hooks, expectedRowIds:['row', 'row-three']}), /이전 여러 항목/);
});

test('two tabs cannot submit the same selected-row batch together', async () => {
  const saved = memory(), locks = lockManager();
  const first = batchHarness(saved, locks), second = batchHarness(saved, locks);
  let started, finish;
  const entered = new Promise(resolve => {started = resolve;});
  const blocked = new Promise(resolve => {finish = resolve;});
  first.handler(async () => {started(); return blocked;});
  const pending = first.controller.run(batchScope, first.hooks);
  await entered;
  assert.deepEqual(await second.controller.run(batchScope, second.hooks), {state:'busy'});
  assert.equal(second.calls.length, 0);
  finish(batchResult);
  assert.equal((await pending).state, 'committed');
});

test('confirmed selected-row archive can restore both rows as one request', async () => {
  const h = batchHarness();
  await h.controller.run(batchScope, h.hooks);
  const restoreRequest = {...batchRequest, archived:false,
    operation_id:'rows-restore-op', expected_document_sequence:8,
    rows:[{row_id:'row', expected_row_version:6},
      {row_id:'row-two', expected_row_version:3}]};
  const restoreResult = {...batchResult, archived:false,
    operation_id:'rows-restore-op', rows:[{row_id:'row', row_version:7},
      {row_id:'row-two', row_version:4}]};
  h.handler(async (url, options) => {
    assert.match(url, /\/databases\/db\/rows\/restore-batch$/);
    assert.deepEqual(options.body.rows, restoreRequest.rows);
    return restoreResult;
  });
  const hooks = {...h.hooks, archived:false, prepare:async () => restoreRequest};
  assert.equal((await h.controller.run(batchScope, hooks)).state, 'committed');
  assert.equal(h.controller.list(batchScope)[0].request.archived, false);
  assert.equal(h.calls.length, 2);
});

test('lost selected-row restore response checks its restore receipt', async () => {
  const h = batchHarness();
  const restoreRequest = {...batchRequest, archived:false,
    operation_id:'rows-restore-op', expected_document_sequence:8,
    rows:[{row_id:'row', expected_row_version:6},
      {row_id:'row-two', expected_row_version:3}]};
  const restoreResult = {...batchResult, archived:false,
    operation_id:'rows-restore-op', rows:[{row_id:'row', row_version:7},
      {row_id:'row-two', row_version:4}]};
  h.handler(async () => {throw Error('restore response lost');});
  const hooks = {...h.hooks, archived:false, prepare:async () => restoreRequest};
  await assert.rejects(h.controller.run(batchScope, hooks), /restore response lost/);
  const reload = batchHarness(h.saved);
  reload.handler(async url => {
    assert.match(url, /\/rows\/restore-batch\/lifecycle-result$/);
    return {state:'committed', result:restoreResult};
  });
  assert.equal((await reload.controller.run(batchScope,
    {...reload.hooks, archived:false,
      prepare:async () => {throw Error('must not prepare');}})).state, 'committed');
  assert.equal(reload.calls.length, 1);
});

test('a 100-row batch remains readable from durable browser storage', async () => {
  const h = batchHarness();
  const rows = Array.from({length:100}, (_, index) => ({
    row_id:`row-${String(index).padStart(3, '0')}${'x'.repeat(249)}`,
    expected_row_version:1,
  }));
  const request = {...batchRequest, operation_id:'hundred-rows', rows};
  h.handler(async () => ({...batchResult, operation_id:'hundred-rows',
    rows:rows.map(item => ({row_id:item.row_id, row_version:2}))}));
  assert.equal((await h.controller.run(batchScope, {...h.hooks,
    expectedRowIds:rows.map(item => item.row_id), prepare:async () => request})).state,
  'committed');
  assert.equal(batchHarness(h.saved).controller.list(batchScope)[0].request.rows.length, 100);
});
