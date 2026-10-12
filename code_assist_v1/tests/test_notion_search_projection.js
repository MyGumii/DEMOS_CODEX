'use strict';
const assert = require('node:assert/strict');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;
const Editor = require('../notion_static/notion_collaboration_editor.js');
const seed = {page_id: 'search-test-page', title: '검색 제목', blocks: [
  {id: 'text', type: 'paragraph', content: '내용', props: {rich_text_html: '<b>내용</b>'}},
  {id: 'grid', type: 'simple_table', content: '', props: {rows: [['A', 'B']]}},
]};
const bridge = Editor.createEditorBridge({Y, host: {readDocument: () => seed,
  applyRemoteProjection: () => ({applied: true}), subscribe: () => () => {}}});
bridge.configureDocument(seed); bridge.finishReplay();
const baseline = bridge.encodeState();
const left = new Y.Doc(), right = new Y.Doc();
Y.applyUpdate(left, baseline); Y.applyUpdate(right, baseline);
const vector = Y.encodeStateVector(left);
left.getMap('blocks').get('text').get('content').insert(0, '동시 ');
right.getMap('blocks').get('grid').set('props', JSON.stringify({rows: [['새 값', '42']]}));
const changes = [Y.encodeStateAsUpdate(left, vector), Y.encodeStateAsUpdate(right, vector)];
bridge.applyFrames(changes.map(payload => ({kind: 'crdt.update', payload})));
const expected = bridge.projection();
const encode = value => Buffer.from(value).toString('base64');
const request = {contract: 'code_assist.notion.search_projection.v1',
  documents: [[baseline, ...changes].map(encode), [baseline, ...changes.slice().reverse()].map(encode)]};
const result = spawnSync(process.execPath, [path.join(__dirname, '../notion_collaboration/search_projection.cjs')],
  {input: JSON.stringify(request), encoding: 'utf8', timeout: 15000, windowsHide: true});
assert.equal(result.status, 0, result.stderr);
const response = JSON.parse(result.stdout);
assert.equal(response.contract, request.contract);
for (const document of response.documents) {
  assert.deepEqual(document, {title: expected.title, blocks: expected.blocks});
}
assert.equal(expected.blocks[0].content, '동시 내용');
assert.deepEqual(expected.blocks[1].props.rows, [['새 값', '42']]);
const invalid = spawnSync(process.execPath, [path.join(__dirname, '../notion_collaboration/search_projection.cjs')],
  {input: JSON.stringify({...request, documents: [[encode(changes[0])]]}), encoding: 'utf8', timeout: 15000, windowsHide: true});
assert.notEqual(invalid.status, 0, 'missing replay dependencies must not produce a partial index');
assert.equal(invalid.stdout, '');
assert(!invalid.stderr.includes('내용'), 'payload content must not enter diagnostics');
bridge.dispose(); left.destroy(); right.destroy();
console.log('UI-SEARCH: current-body projection matches the editor for concurrent text/table edits; incomplete replay rejected');
