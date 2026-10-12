'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {transform}=require('../notion_collaboration/page_move_delta.cjs');
const Y=globalThis.NotionYjs;
const seed={title:'원래 제목',blocks:[
  {id:'a',type:'paragraph',content:'SQL의 예전 문장',props:{rich_text_html:'<strong>서식 유지</strong>'}},
  {id:'b',type:'heading1',content:'다음 제목',props:{}},
]};
const basic={document_id:'parent',generation:1,seed,frames:[],child_id:'new-child',insert:null};
const frame=transform({...basic,child_id:'absent'}).update;
const note={id:'note',type:'paragraph',content:'늦게 추가한 한글',props:{rich_text_html:'<em>늦게 추가한 한글</em>',nested:{color:'red',tags:['가','나']}}};
const child={id:'link',type:'child_page',content:'새 하위 페이지',props:{page_id:'new-child'}};
function replay(update,initial=frame) {const d=new Y.Doc();Y.applyUpdate(d,Buffer.from(initial,'base64'));Y.applyUpdate(d,Buffer.from(update,'base64'));return d;}

test('UI-CREATE-INSERT: proven note and rich props survive child creation and concurrent original text',()=>{
  const live=new Y.Doc();Y.applyUpdate(live,Buffer.from(frame,'base64'));
  live.getMap('blocks').get('a').get('content').insert(0,'현재 ');
  const current=Buffer.from(Y.encodeStateAsUpdate(live)).toString('base64');
  const changed=transform({...basic,seed:{...seed,blocks:[note,...seed.blocks]},frames:[current],insert:child,post_frame_insert_ids:['note']});
  live.getMap('blocks').get('a').get('content').insert(0,'동시 입력 ');
  Y.applyUpdate(live,Buffer.from(changed.update,'base64'));
  assert.deepEqual(changed.reconciled_insert_ids,['note']);
  assert.deepEqual(live.getArray('block-order').toArray(),['note','a','b','link']);
  assert.equal(live.getMap('blocks').get('a').get('content').toString(),'동시 입력 현재 SQL의 예전 문장');
  assert.deepEqual(JSON.parse(live.getMap('blocks').get('a').get('props')),seed.blocks[0].props);
  assert.deepEqual(JSON.parse(live.getMap('blocks').get('note').get('props')),note.props);
  assert.equal(live.getText('page-title').toString(),seed.title);live.destroy();
});

test('UI-CREATE-INSERT: multiple proven inserts keep exact canonical positions; unopted callers retain legacy behavior',()=>{
  const middle={...note,id:'middle'},last={...note,id:'last'};
  const canonical={...seed,blocks:[note,seed.blocks[0],middle,seed.blocks[1],last]};
  const input={...basic,seed:canonical,frames:[frame],insert:child};
  const updated=transform({...input,post_frame_insert_ids:['last','note','middle']});
  const d=replay(updated.update);
  assert.deepEqual(d.getArray('block-order').toArray(),['note','a','middle','b','last','link']);
  assert.equal(updated.after_block_id,'last');d.destroy();
  const legacy=transform(input),old=replay(legacy.update);
  assert.deepEqual(old.getArray('block-order').toArray(),['a','b','link']);old.destroy();
});

test('UI-CREATE-INSERT: missing provenance, duplicate IDs and altered known order are refused',()=>{
  const input={...basic,frames:[frame],seed:{...seed,blocks:[note,...seed.blocks]},insert:child};
  assert.throws(()=>transform({...input,post_frame_insert_ids:[]}),/requires reconciliation/);
  assert.throws(()=>transform({...input,post_frame_insert_ids:['note','note']}),/Invalid parent insert proof/);
  assert.throws(()=>transform({...input,post_frame_insert_ids:['foreign']}),/outside canonical body/);
  assert.throws(()=>transform({...input,seed:{...seed,blocks:[note,...seed.blocks.toReversed()]},post_frame_insert_ids:['note']}),/requires reconciliation/);
});

test('UI-CREATE-INSERT: an orphan map identity cannot be overwritten by a canonical insert',()=>{
  const d=new Y.Doc();Y.applyUpdate(d,Buffer.from(frame,'base64'));
  const map=new Y.Map();map.set('type','paragraph');d.getMap('blocks').set('note',map);
  const orphan=Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64');
  assert.throws(()=>transform({...basic,frames:[orphan],seed:{...seed,blocks:[note,...seed.blocks]},insert:child,post_frame_insert_ids:['note']}),/identity conflicts/);d.destroy();
});

test('UI-CREATE-INSERT: empty proof never replaces current text or markup from stale SQL seed',()=>{
  const d=new Y.Doc();Y.applyUpdate(d,Buffer.from(frame,'base64'));
  const block=d.getMap('blocks').get('a');block.get('content').delete(0,block.get('content').length);block.get('content').insert(0,'현재 본문');block.set('props',JSON.stringify({rich_text_html:'<u>현재 본문</u>'}));
  const current=Buffer.from(Y.encodeStateAsUpdate(d)).toString('base64');
  const update=transform({...basic,frames:[current],insert:child,post_frame_insert_ids:[]});
  Y.applyUpdate(d,Buffer.from(update.update,'base64'));
  assert.deepEqual(update.reconciled_insert_ids,[]);assert.equal(block.get('content').toString(),'현재 본문');
  assert.deepEqual(JSON.parse(block.get('props')),{rich_text_html:'<u>현재 본문</u>'});d.destroy();
});
