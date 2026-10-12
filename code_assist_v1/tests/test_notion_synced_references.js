'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs'), vm = require('node:vm');
const synced = require('../notion_static/notion_synced_references.js');
const editorCore = require('../notion_static/notion_editor_core.js');
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const tick = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
const origin = {document_id: 'source', block_id: 'original'};
const response = (id = 'instance', text = 'current source') => ({contract: 'code_assist.notion.synced_block.v1',
  instance: {document_id: 'page', block_id: id}, origin: {...origin},
  block: {id: 'original', type: 'synced_block', content: text, props: {synced_from: null}}});
function harness(load) {
  const context = {pageId: 'page', userId: 'owner'}, events = [], pending = [], state = new Map();
  const host = {current: value => value === context && host.allowed, allowed: true,
    visible: slot => host.shown && !slot?.hidden, shown: true,
    display: (slot, event) => { events.push([slot.id, event.phase]); state.set(slot.id, event); },
    load: load || ((scope, id, signal) => { const task = deferred(); pending.push({id, signal, ...task}); return task.promise; })};
  return {host, context, events, pending, state, view: synced.create(host)};
}
const slot = (id = 'instance') => ({id, origin: {...origin}});

test('UI-SYNC-VIEW current read owns display only; refresh failure removes former content', async () => {
  const h = harness(), source = slot(), snapshot = JSON.stringify(source);
  const first = h.view.mount(h.context, [source]);
  assert.equal(h.state.get(source.id).phase, 'loading');
  h.pending[0].resolve(response()); await first;
  assert.equal(h.state.get(source.id).data.block.content, 'current source');
  const next = h.view.refresh();
  assert.equal(h.state.get(source.id).phase, 'loading');
  assert.equal(h.state.get(source.id).data, undefined);
  h.pending[1].reject(Object.assign(new Error('revoked'), {status: 404})); await next;
  assert.deepEqual(h.state.get(source.id), {phase: 'error', denied: true});
  assert.equal(JSON.stringify(source), snapshot);
});

test('UI-SYNC-VIEW late response after remount cannot render into the next page', async () => {
  const h = harness();
  const first = h.view.mount(h.context, [slot()]);
  const second = h.view.mount(h.context, [slot('next')]);
  assert.equal(h.pending[0].signal.aborted, true);
  h.pending[1].resolve(response('next', 'new page')); await second;
  h.pending[0].resolve(response()); await first;
  assert.equal(h.state.get('instance').phase, 'idle');
  assert.equal(h.state.get('next').data.block.content, 'new page');
});

test('UI-SYNC-VIEW page scope loss rejects pending display, hide clears and aborts', async () => {
  const h = harness(); const flight = h.view.mount(h.context, [slot()]);
  h.host.allowed = false; h.pending[0].resolve(response()); await flight;
  assert.equal(h.events.some(([, phase]) => phase === 'ready'), false);
  h.host.allowed = true; const next = h.view.refresh();
  h.host.shown = false; await h.view.refresh();
  assert.equal(h.pending[1].signal.aborted, true);
  assert.equal(h.state.get('instance').phase, 'idle');
  h.pending[1].resolve(response()); await next;
  assert.equal(h.state.get('instance').phase, 'idle');
});

test('UI-SYNC-VIEW validates original, instance, contract and explicit identity', async () => {
  for (const change of [data => {data.instance.block_id = 'other';}, data => {data.origin.document_id = 'other';},
    data => {data.contract = 'other';}, data => {data.block.id = 'other';},
    data => {delete data.block.props.synced_from;}, data => {data.block.props.synced_from = {...origin};}]) {
    const data = response(); change(data);
    const h = harness(async () => data); await h.view.mount(h.context, [slot()]);
    assert.deepEqual(h.state.get('instance'), {phase: 'error', denied: false});
  }
});

test('UI-SYNC-VIEW deduplicates refresh and bounds concurrent reads to two', async () => {
  const h = harness(), list = [slot('a'), slot('b'), slot('c'), slot('d')];
  const flight = h.view.mount(h.context, list); const duplicate = h.view.refresh();
  assert.equal(h.pending.length, 2);
  h.pending[0].resolve(response('a')); await tick();
  assert.equal(h.pending.length, 3);
  h.pending[1].resolve(response('b')); await tick();
  assert.equal(h.pending.length, 4);
  h.pending[2].resolve(response('c')); h.pending[3].resolve(response('d'));
  await Promise.all([flight, duplicate]);
  assert.equal(h.events.filter(([, phase]) => phase === 'ready').length, 4);
});
test('UI-SYNC-VIEW after-save refresh invalidates an earlier flight and waits for a new read', async () => {
  const h = harness(); const first = h.view.mount(h.context, [slot()]);
  const fresh = h.view.refresh({afterFlight:true});
  h.pending[0].resolve(response('instance','before save')); await tick(); await tick();
  assert.equal(h.events.some(([, phase]) => phase === 'ready'), false);
  assert.equal(h.pending.length,2);
  h.pending[1].resolve(response('instance','after save')); await Promise.all([first,fresh]);
  assert.equal(h.state.get('instance').data.block.content,'after save');
  assert.equal(h.events.filter(([, phase]) => phase === 'ready').length,1);
});

test('UI-SYNC-VIEW hidden rows are not fetched; becoming visible reads current content', async () => {
  const h = harness(), hidden = {...slot(), hidden: true};
  await h.view.mount(h.context, [hidden]); assert.equal(h.pending.length, 0);
  hidden.hidden = false; const flight = h.view.refresh();
  h.pending[0].resolve(response()); await flight;
  assert.equal(h.state.get('instance').phase, 'ready');
  h.view.dispose(); assert.equal(h.state.get('instance').phase, 'idle');
  await h.view.refresh(); assert.equal(h.pending.length, 1);
});

test('UI-SYNC-VIEW references never become editable or receive legacy peer copies', () => {
  const main = fs.readFileSync(require.resolve('../notion_static/notion.js'), 'utf8');
  const peers = main.slice(main.indexOf('function syncSyncedBlockPeers('), main.indexOf('function handleBlockPaste('));
  const blocks = [
    {id: 'legacy', type: 'synced_block', content: 'new legacy text', props: {sync_id: 'same'}},
    {id: 'instance', type: 'synced_block', content: '', props: {sync_id: 'same', synced_from: {...origin}}},
    {id: 'original', type: 'synced_block', content: 'explicit source', props: {sync_id: 'same', synced_from: null}},
    {id: 'legacy-peer', type: 'synced_block', content: 'old', props: {sync_id: 'same'}},
  ];
  const context = {State: {pageData: {blocks}}, window: {NotionSyncedReferences: synced,NotionSyncedActions:require('../notion_static/notion_synced_actions.js'),
    NotionLinkedDatabases: require('../notion_static/notion_linked_databases.js')},
    EditorCore: {deepClone: value => JSON.parse(JSON.stringify(value))}, document: {querySelector: () => null}};
  vm.createContext(context); vm.runInContext(peers, context);
  context.syncSyncedBlockPeers(blocks[0], 0);
  assert.equal(blocks[3].content, 'new legacy text');
  assert.equal(blocks[1].content, ''); assert.deepEqual(blocks[1].props.synced_from, origin);
  assert.equal(blocks[2].content, 'explicit source');
  const before = JSON.stringify(blocks); context.syncSyncedBlockPeers(blocks[2], 2);
  assert.equal(JSON.stringify(blocks), before);
  const start = main.indexOf('function blockEditorHtml(');
  const renderer = main.slice(start, main.indexOf('\nfunction ', start + 10));
  Object.assign(context, {escapeAttr: value => value, richTextEditorHtml: () => 'EDITABLE',
    syncedBlockCreationAvailable: () => false});
  vm.runInContext(renderer, context);
  const html = context.blockEditorHtml(blocks[1], true);
  assert.match(html, /data-synced-reference/);
  assert.doesNotMatch(html, /EDITABLE|contenteditable|class="block-editor"/);
  assert.match(context.blockEditorHtml(blocks[2], true), /EDITABLE/);
});

test('UI-SYNC-VIEW Backspace and Delete cannot merge adjacent text into a reference', () => {
  const main = fs.readFileSync(require.resolve('../notion_static/notion.js'), 'utf8');
  const start = main.indexOf('function handleBlockEditorKeydown(');
  const handler = main.slice(start, main.indexOf('\nfunction ', start + 10));
  for (const key of ['Backspace', 'Delete']) {
    const text = {id: 'text', type: 'paragraph', content: 'keep text', props: {}};
    const ref = {id: 'ref', type: 'synced_block', content: '', props: {synced_from: {...origin}}};
    const blocks = key === 'Backspace' ? [ref, text] : [text, ref];
    const original = JSON.stringify(blocks);
    let prevented = false, blurred = false, selected = -1;
    const context = {State: {pageData: {blocks}}, window: {NotionSyncedReferences: synced},
      activePageCanEdit: () => true, $: () => ({hidden: true}), selectionIsCollapsed: () => true,
      caretOffset: () => key === 'Backspace' ? 0 : text.content.length, editorPlainText: () => text.content,
      EditorCore: {isTextBlock: () => true, blockOutline: editorCore.blockOutline,
        mergeBlocks: () => {throw new Error('Cannot merge a reference');}},
      selectBlockHandle: index => {selected = index;},
      syncEditorBlock: () => {throw new Error('Cannot rewrite the adjacent text');}};
    vm.createContext(context); vm.runInContext(handler, context);
    context.handleBlockEditorKeydown({key, preventDefault: () => {prevented = true;}},
      {dataset: {}, blur: () => {blurred = true;}}, text, blocks.indexOf(text));
    assert.equal(prevented, true); assert.equal(blurred, true);
    assert.equal(selected, blocks.indexOf(ref)); assert.equal(JSON.stringify(blocks), original);
  }
});
