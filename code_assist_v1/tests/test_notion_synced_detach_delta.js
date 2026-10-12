'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y = globalThis.NotionYjs;
const {transform, CONTRACT} = require('../notion_collaboration/synced_detach_delta.cjs');

const block = (id, content='', props={}, type='paragraph') => ({id, type, content, props});
function seed(blocks, title='기존 제목') {
  const doc = new Y.Doc(), order = doc.getArray('block-order'), map = doc.getMap('blocks');
  doc.getText('page-title').insert(0, title);
  order.insert(0, blocks.map(value => value.id));
  for (const value of blocks) {
    const item = new Y.Map(); map.set(value.id, item);
    item.set('type', value.type); item.set('props', JSON.stringify(value.props));
    item.set('content', new Y.Text());
    if (value.content) item.get('content').insert(0, value.content);
  }
  const update = Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64');
  doc.destroy();
  return update;
}
function replay(frames) {
  const doc = new Y.Doc();
  for (const frame of frames) Y.applyUpdate(doc, new Uint8Array(Buffer.from(frame,'base64')));
  return doc;
}
function request(frames, current, replacement, generation=1) {
  return {contract:CONTRACT, document_id:'destination', generation, frames,
    seed:{title:'기존 제목', blocks:current}, replacement};
}
function content(doc) {
  const order = doc.getArray('block-order').toArray(), map = doc.getMap('blocks');
  return order.map(id => {
    const item = map.get(id);
    return {id, type:item.get('type'), content:item.get('content').toString(), props:JSON.parse(item.get('props'))};
  });
}

test('UI-SYNC-DETACH-DELTA retains untouched Y.Text and changes the reference in place', () => {
  const before=[block('a','변하지 않는 문장'), block('ref','', {synced_from:{document_id:'original',block_id:'source'}}, 'synced_block'), block('z','마지막')];
  const after=[before[0], block('slot','',{children:['scope','local'],layout_container:true}),
    block('scope','',{children:['ref'],layout_container:true,background_color:'yellow'}),
    block('ref','분리된 한😀글',{indent:0}),block('local','참조에 쓴 글'),before[2]];
  const frame=seed(before), delta=transform(request([frame],before,after));
  assert.deepEqual(delta.added_ids,['slot','scope','local']);
  assert.deepEqual(delta.removed_ids,[]);
  assert.ok(delta.updates.length && delta.updates.every(x=>Buffer.from(x,'base64').length<=1048576));
  const doc=replay([frame]);
  const sameText=doc.getMap('blocks').get('a').get('content');
  const tail=doc.getMap('blocks').get('z').get('content');
  for(const update of delta.updates) Y.applyUpdate(doc,new Uint8Array(Buffer.from(update,'base64')));
  assert.deepEqual(content(doc),after);
  assert.strictEqual(doc.getMap('blocks').get('a').get('content'),sameText);
  assert.strictEqual(doc.getMap('blocks').get('z').get('content'),tail);
  assert.equal(doc.getText('page-title').toString(),'기존 제목');
  doc.destroy();
});

test('UI-SYNC-DETACH-DELTA bootstraps a generation-one body with no Yjs frames', () => {
  const before=[block('ref','',{},'synced_block')],after=[block('ref','독립')];
  const delta=transform(request([],before,after));
  const doc=replay(delta.updates);
  assert.deepEqual(content(doc),after);
  assert.equal(doc.getText('page-title').toString(),'기존 제목');
  assert.equal(delta.source_update_count,0);
  doc.destroy();
});

test('UI-SYNC-DETACH-DELTA keeps a title-only generation-one frame', () => {
  const title=new Y.Doc(); title.getText('page-title').insert(0,'원격 최신 제목');
  const frame=Buffer.from(Y.encodeStateAsUpdate(title)).toString('base64'); title.destroy();
  const before=[block('ref','',{},'synced_block')], after=[block('ref','독립')];
  const delta=transform(request([frame],before,after));
  const doc=replay([frame,...delta.updates]);
  assert.deepEqual(content(doc),after);
  assert.equal(doc.getText('page-title').toString(),'원격 최신 제목');
  doc.destroy();
});

test('UI-SYNC-DETACH-DELTA splits long text into bounded replayable updates', () => {
  const before=[block('ref','',{},'synced_block')],large='한😀글'.repeat(200000);
  const after=[block('ref',large)];
  const frame=seed(before), delta=transform(request([frame],before,after));
  assert.ok(delta.updates.length>1);
  assert.ok(delta.updates.every(x=>Buffer.from(x,'base64').length<=1048576));
  const doc=replay([frame,...delta.updates]);
  assert.equal(content(doc)[0].content,large);
  doc.destroy();
});

test('UI-SYNC-DETACH-DELTA preserves an emoji across a text chunk boundary', () => {
  const before=[block('ref','',{},'synced_block')];
  const expected=[block('ref','x'.repeat(32767)+'😀'+'끝')];
  const frame=seed(before), delta=transform(request([frame],before,expected));
  const doc=replay([frame,...delta.updates]);
  assert.equal(content(doc)[0].content,expected[0].content);
  doc.destroy();
});

test('UI-SYNC-DETACH-DELTA rejects stale capture, missing generation and duplicate IDs', () => {
  const before=[block('ref','원래',{},'synced_block')], frame=seed(before);
  assert.throws(()=>transform(request([frame],[block('ref','낡은',{},'synced_block')],[block('ref','독립')])),/differs/);
  assert.throws(()=>transform(request([],before,[block('ref','독립')],2)),/generation/);
  assert.throws(()=>transform(request([frame],before,[block('ref'),block('ref')])),/Invalid detached block/);
  assert.throws(()=>transform(request([frame],before,before)),/did not change/);
});
