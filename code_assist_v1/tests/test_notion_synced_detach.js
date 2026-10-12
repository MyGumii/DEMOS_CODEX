const assert = require('node:assert/strict');
const controller = require('../notion_static/notion_synced_detach.js');

function memory() {
  const items = new Map();
  return {get length() {return items.size;}, key(index) {return [...items.keys()][index] ?? null;},
    getItem(key) {return items.has(key) ? items.get(key) : null;},
    setItem(key, value) {items.set(key, value);}, removeItem(key) {items.delete(key);}};
}
const scope = {userId: 'owner', workspaceId: 'workspace', pageId: 'destination', blockId: 'reference'};
const request = {operation_id: 'detach-once', origin: {document_id: 'source', block_id: 'original'},
  witnesses: [
    {document_id: 'source', workspace_id: 'workspace', workspace_epoch: 1,
      document_sequence: 2, document_epoch: 1, generation: 1},
    {document_id: 'destination', workspace_id: 'workspace', workspace_epoch: 1,
      document_sequence: 3, document_epoch: 1, generation: 1},
  ]};
const result = {contract: 'code_assist.notion.synced_detach_commit.v1', workspace_id: 'workspace',
  document_id: 'destination', block_id: 'reference', origin: request.origin,
  operation_id: request.operation_id, replayed: false};
const locks = () => ({request: async (_key, _options, action) => action({})});
const hooks = () => ({current: () => true, prepare: async () => structuredClone(request),
  beforeRetry: async () => {}});

async function lostResponseRecoversWithoutSecondWrite() {
  const storage = memory();
  let saves = 0, reads = 0;
  const instance = controller.create({storage: () => storage, locks,
    transport: async (url, options) => {
      assert.deepEqual(options.body, request);
      if (url.endsWith('/detach-result')) {reads++; return {state: 'committed', result: {...result, replayed: true},
        current: {archived: false, document_sequence: 8}};}
      assert.equal(instance.list(scope).length, 1, 'intent must be durable before a write');
      saves++;
      throw Object.assign(new Error('response lost after commit'), {status: 0});
    }});
  await assert.rejects(instance.run(scope, hooks()), /response lost/);
  assert.equal(saves, 1);
  assert.equal(instance.pending(scope), true);
  const recovered = await instance.run(scope, hooks());
  assert.equal(recovered.state, 'committed');
  assert.equal(recovered.current.document_sequence, 8);
  assert.equal(saves, 1);
  assert.equal(reads, 1);
  assert.equal(instance.list(scope)[0].confirmed, true);
  assert.equal(await instance.acknowledge(scope, request.operation_id), true);
  assert.equal(instance.list(scope).length, 0);
}

async function unknownReceiptRetriesExactRequest() {
  const storage = memory();
  let saves = 0, reads = 0, preparations = 0, retries = 0;
  const instance = controller.create({storage: () => storage, locks,
    transport: async (url, options) => {
      assert.deepEqual(options.body, request);
      if (url.endsWith('/detach-result')) {reads++; return {state: 'unknown'};}
      if (++saves === 1) throw new Error('network unavailable');
      return {...result, replayed: false};
    }});
  const actions = {current: () => true, prepare: async () => {preparations++; return structuredClone(request);},
    beforeRetry: async () => {retries++;}};
  await assert.rejects(instance.run(scope, actions), /network unavailable/);
  assert.deepEqual(await instance.run(scope, {...actions, lookupOnly: true}), {state: 'unknown'});
  const done = await instance.run(scope, actions);
  assert.equal(done.state, 'committed');
  assert.equal(done.result.block_id, scope.blockId);
  assert.deepEqual({saves, reads, preparations, retries}, {saves: 2, reads: 2, preparations: 1, retries: 1});
}

async function malformedAndUncoordinatedRequestsFailClosed() {
  const storage = memory();
  let sent = 0;
  const noLock = controller.create({storage: () => storage, locks: () => null,
    transport: async () => {sent++;}});
  await assert.rejects(noLock.run(scope, hooks()), /안전하게 조정/);
  const invalid = controller.create({storage: () => storage, locks,
    transport: async () => {sent++;}});
  await assert.rejects(invalid.run(scope, {...hooks(), prepare: async () => ({...request,
    witnesses: [request.witnesses[0], {...request.witnesses[0]}]})}), /확인할 수 없습니다/);
  assert.equal(sent, 0);
  const competing = controller.create({storage: () => storage,
    locks: () => ({request: async (_key, _options, action) => action(null)}),
    transport: async () => {sent++;}});
  assert.deepEqual(await competing.run(scope, hooks()), {state: 'busy'});
  assert.equal(sent, 0);
}

async function mismatchedReceiptCannotBeAccepted() {
  const storage = memory();
  const instance = controller.create({storage: () => storage, locks,
    transport: async () => ({...result, document_id: 'other-page'})});
  await assert.rejects(instance.run(scope, hooks()), /일치하지 않습니다/);
  assert.equal(instance.list(scope)[0].confirmed, false);
}

function samePageReferenceNeedsOnlyOneWitness() {
  const local = {...scope, pageId: 'source'};
  const input = {...request, witnesses: [request.witnesses[0]]};
  assert.equal(controller.validRequest(input, local), true);
  assert.equal(controller.validRequest({...input, witnesses: [request.witnesses[1]]}, local), false);
}

async function twoTabsDoNotSaveTheSameBlockAtOnce() {
  const storage = memory();
  let occupied = false, finishSave;
  let saves = 0, receipts = 0;
  const coordinatedLocks = () => ({request: async (_key, options, action) => {
    assert.equal(options.ifAvailable, true);
    if (occupied) return action(null);
    occupied = true;
    try {return await action({});} finally {occupied = false;}
  }});
  const transport = async url => {
    if (url.endsWith('/detach-result')) {
      receipts++;
      return {state: 'committed', result: {...result, replayed: true},
        current: {archived: false, document_sequence: 8}};
    }
    saves++;
    await new Promise(resolve => {finishSave = resolve;});
    return result;
  };
  const firstTab = controller.create({storage: () => storage, locks: coordinatedLocks, transport});
  const secondTab = controller.create({storage: () => storage, locks: coordinatedLocks, transport});
  const first = firstTab.run(scope, hooks());
  for (let tick = 0; tick < 10 && !finishSave; tick++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(saves, 1);
  assert.equal(firstTab.list(scope).length, 1);
  assert.deepEqual(await secondTab.run(scope, hooks()), {state: 'busy'});
  assert.equal(saves, 1);
  finishSave();
  assert.equal((await first).state, 'committed');
  assert.equal((await secondTab.run(scope, hooks())).state, 'committed');
  assert.deepEqual({saves, receipts}, {saves: 1, receipts: 1});
}

async function staleWitnessClearsOnlyAfterAnUnknownExactReceipt() {
  const storage = memory();
  const newer = {...structuredClone(request), operation_id: 'detach-after-change'};
  newer.witnesses[1].document_epoch += 1;
  let saves = 0, receipts = 0, prepared = 0;
  const instance = controller.create({storage: () => storage, locks,
    transport: async (url, options) => {
      if (url.endsWith('/detach-result')) {receipts++; return {state: 'unknown'};}
      saves++;
      if (options.body.operation_id === request.operation_id) {
        throw Object.assign(new Error('stale'), {status: 409, data: {code: 'synced_source_changed'}});
      }
      assert.deepEqual(options.body, newer);
      return {...result, operation_id: newer.operation_id};
    }});
  const actions = {current: () => true, prepare: async () => {
    prepared++;
    return prepared === 1 ? structuredClone(request) : structuredClone(newer);
  }, beforeRetry: async () => {}};
  assert.deepEqual(await instance.run(scope, actions), {state: 'stale'});
  assert.equal(instance.list(scope).length, 0);
  assert.equal((await instance.run(scope, actions)).state, 'committed');
  assert.deepEqual({saves, receipts, prepared}, {saves: 2, receipts: 1, prepared: 2});
}

async function staleWitnessDoesNotDiscardACommittedOrUncertainOperation() {
  const storage = memory();
  const instance = controller.create({storage: () => storage, locks,
    transport: async url => {
      if (url.endsWith('/detach-result')) {
        return {state: 'committed', result: {...result, replayed: true},
          current: {archived: false, document_sequence: 8}};
      }
      throw Object.assign(new Error('stale'), {status: 409, data: {code: 'synced_source_changed'}});
    }});
  assert.equal((await instance.run(scope, hooks())).state, 'committed');
  assert.equal(instance.list(scope)[0].confirmed, true);
  await instance.acknowledge(scope, request.operation_id);
  const uncertainStorage = memory();
  const uncertain = controller.create({storage: () => uncertainStorage, locks,
    transport: async url => {
      if (url.endsWith('/detach-result')) throw Object.assign(new Error('no view'), {status: 404});
      throw Object.assign(new Error('stale'), {status: 409, data: {code: 'synced_source_changed'}});
    }});
  await assert.rejects(uncertain.run(scope, hooks()), /no view/);
  assert.equal(uncertain.list(scope).length, 1);
}

async function unrelatedConflictKeepsTheExactRequest() {
  const storage = memory();
  const instance = controller.create({storage: () => storage, locks,
    transport: async () => {
      throw Object.assign(new Error('conflict'), {status: 409, data: {code: 'operation_id_conflict'}});
    }});
  await assert.rejects(instance.run(scope, hooks()), /conflict/);
  assert.equal(instance.list(scope)[0].request.operation_id, request.operation_id);
}

(async () => {
  await lostResponseRecoversWithoutSecondWrite();
  await unknownReceiptRetriesExactRequest();
  await malformedAndUncoordinatedRequestsFailClosed();
  await mismatchedReceiptCannotBeAccepted();
  samePageReferenceNeedsOnlyOneWitness();
  await twoTabsDoNotSaveTheSameBlockAtOnce();
  await staleWitnessClearsOnlyAfterAnUnknownExactReceipt();
  await staleWitnessDoesNotDiscardACommittedOrUncertainOperation();
  await unrelatedConflictKeepsTheExactRequest();
  console.log('synced detach browser intent tests passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
