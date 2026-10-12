'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const {CollaborationEditor, loadPinnedYjs} = require('./notion_collaboration_test_runtime.js');
const EditorCore = require('../notion_static/notion_editor_core.js');
const Y = loadPinnedYjs();
const clone = value => JSON.parse(JSON.stringify(value));
const frame = payload => ({kind: 'crdt.update', payload: new Uint8Array(payload)});
function bodyOnly() {
  const doc = new Y.Doc();
  doc.getArray('block-order').insert(0, ['block-a']);
  const block = new Y.Map(), text = new Y.Text();
  doc.getMap('blocks').set('block-a', block);
  block.set('type', 'paragraph'); block.set('props', '{}'); block.set('content', text);
  text.insert(0, 'latest body');
  return doc;
}
function harness(t, {title = '기존 제목', payload, pending = [], finish = true} = {}) {
  const state = {page_id: 'partial-title-page', title,
    blocks: [{id: 'block-a', type: 'paragraph', content: 'old body', props: {}}],
    can_edit: true, server_revision: 1};
  const events = [], projections = [];
  const host = EditorCore.createEditorHostAdapter({readDocument: () => clone(state),
    applyProjection: data => {Object.assign(state, clone(data)); projections.push(clone(data)); return {applied: true};}});
  const bridge = CollaborationEditor.createEditorBridge({Y, host});
  bridge.subscribe(event => events.push(event)); bridge.configureDocument(state);
  if (payload) bridge.applyFrames([frame(payload)]);
  if (finish) bridge.finishReplay(pending.map(entry => ({payload: new Uint8Array(entry.payload)})));
  t.after(() => bridge.dispose());
  return {state, host, bridge, events, projections};
}
const updates = h => h.events.filter(event => event.type === 'local-update');
const seed = h => updates(h).find(event => event.seed);

test('UI-CRDT-TITLE body-only replay preserves the exact canonical title and latest body', t => {
  for (const title of ['기존 제목', '  공백 유지  ', '']) {
    const doc = bodyOnly(); t.after(() => doc.destroy());
    const h = harness(t, {title, payload: Y.encodeStateAsUpdate(doc)});
    assert.equal(h.state.title, title);
    assert.equal(h.state.blocks[0].content, 'latest body');
    h.bridge.finishReplay();
    assert.equal(updates(h).length, title ? 1 : 0, 'only one missing-title seed, never a full body reseed');
    assert.equal(h.bridge.undo(), false, 'canonical title initialization is not an editable undo step');
    if (title) {
      const originalBody = doc.getMap('blocks').get('block-a').get('content').toString();
      Y.applyUpdate(doc, seed(h).update);
      assert.equal(doc.getText('page-title').toString(), title);
      assert.equal(doc.getMap('blocks').get('block-a').get('content').toString(), originalBody);
      assert.deepEqual(Array.from(doc.getArray('block-order')), ['block-a']);
    }
  }
});

test('UI-CRDT-TITLE an intentionally cleared remote title stays empty', t => {
  const doc = bodyOnly(); t.after(() => doc.destroy());
  const title = doc.getText('page-title'); title.insert(0, 'old title'); title.delete(0, title.length);
  const h = harness(t, {payload: Y.encodeStateAsUpdate(doc)});
  assert.equal(h.state.title, ''); assert.equal(h.state.blocks[0].content, 'latest body');
  assert.equal(updates(h).length, 0);
});

test('UI-CRDT-TITLE reading before replay does not manufacture title presence', t => {
  const doc = bodyOnly(); t.after(() => doc.destroy());
  const h = harness(t, {payload: Y.encodeStateAsUpdate(doc), finish: false});
  assert.equal(h.bridge.projection().title, '기존 제목');
  const before = new Y.Doc(); t.after(() => before.destroy());
  Y.applyUpdate(before, h.bridge.encodeState());
  assert.equal(before.share.has('page-title'), false);
  h.bridge.finishReplay(); assert.equal(h.state.title, '기존 제목'); assert.ok(seed(h));
});

test('UI-CRDT-TITLE pending offline title intent is applied before missing-title initialization', t => {
  for (const cleared of [false, true]) {
    const doc = bodyOnly(); t.after(() => doc.destroy());
    const body = Y.encodeStateAsUpdate(doc), vector = Y.encodeStateVector(doc);
    const title = doc.getText('page-title'); title.insert(0, 'offline title');
    if (cleared) title.delete(0, title.length);
    const h = harness(t, {payload: body, pending: [{payload: Y.encodeStateAsUpdate(doc, vector)}]});
    assert.equal(h.state.title, cleared ? '' : 'offline title');
    assert.equal(seed(h), undefined, 'offline rename/clear must not receive a second baseline title');
    assert.equal(h.state.blocks[0].content, 'latest body');
  }
});

test('UI-CRDT-TITLE simultaneous missing-title seeds and Korean edits converge without duplication', t => {
  const doc = bodyOnly(); t.after(() => doc.destroy());
  const payload = Y.encodeStateAsUpdate(doc), left = harness(t, {payload}), right = harness(t, {payload});
  assert.ok(seed(left) && seed(right));
  assert.equal(CollaborationEditor.bytesToBase64(seed(left).update), CollaborationEditor.bytesToBase64(seed(right).update));
  left.bridge.applyRemoteAndProject([frame(seed(right).update)]);
  right.bridge.applyRemoteAndProject([frame(seed(left).update)]);
  assert.equal(left.state.title, '기존 제목'); assert.equal(right.state.title, '기존 제목');
  left.events.length = 0; right.events.length = 0;
  left.state.title = '앞 기존 제목'; left.host.notifyLocalMutation('document');
  right.state.title = '기존 제목 뒤'; right.host.notifyLocalMutation('document');
  const leftUpdate = updates(left).at(-1), rightUpdate = updates(right).at(-1);
  left.bridge.applyRemoteAndProject([frame(rightUpdate.update)]);
  right.bridge.applyRemoteAndProject([frame(leftUpdate.update)]);
  assert.equal(left.state.title, '앞 기존 제목 뒤'); assert.equal(right.state.title, left.state.title);
  assert.equal(left.bridge.undo(), true); assert.equal(left.state.title, '기존 제목 뒤');
  right.bridge.applyRemoteAndProject([frame(updates(left).at(-1).update)]);
  assert.equal(right.state.title, left.state.title);
  const reopened = harness(t, {title: 'stale canonical', payload: left.bridge.encodeState()});
  assert.equal(reopened.state.title, '기존 제목 뒤'); assert.equal(seed(reopened), undefined);
});

test('UI-CRDT-TITLE a replay containing title data wins over stale baseline metadata', t => {
  const doc = bodyOnly(); t.after(() => doc.destroy());
  doc.getText('page-title').insert(0, 'current shared title');
  const h = harness(t, {payload: Y.encodeStateAsUpdate(doc)});
  assert.equal(h.state.title, 'current shared title'); assert.equal(updates(h).length, 0);
});
