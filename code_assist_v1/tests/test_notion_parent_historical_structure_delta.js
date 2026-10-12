'use strict';
const test=require('node:test'), assert=require('node:assert/strict');
const {transform}=require('../notion_collaboration/page_move_delta.cjs');
const Y=globalThis.NotionYjs;
const blocks=['a','b','c','d'].map(id=>({id,type:'paragraph',content:'서식 '+id,props:{color:'blue'}}));
function cmd(kind,id,after,sequence) {
  return {command_id:'history-'+sequence,document_sequence:sequence,kind:'block.'+kind,
    target_id:id,after_block_id:after,restore_deleted:false};
}
function request(final,commands,current=blocks) {
  const base={document_id:'parent',generation:1,frames:[],seed:{title:'현재 제목',blocks:current},child_id:'absent',insert:null};
  const frame=transform(base).update;
  return {...base,frames:[frame],seed:{title:'예전 SQL 제목',blocks:final.map(id=>blocks.find(b=>b.id===id))},
    post_frame_insert_ids:[],post_frame_structure:{contract:'code_assist.notion.parent_structure_witness.v1',
      document_id:'parent',generation:1,through_document_sequence:9,commands:[],
      retired_ids:commands.filter(c=>c.kind==='block.delete'&&!final.includes(c.target_id)).map(c=>c.target_id),
      historical_move_ids:commands.filter(c=>c.kind==='block.move'&&final.includes(c.target_id)).map(c=>c.target_id),
      historical_structure:{contract:'code_assist.notion.parent_historical_structure.v1',document_id:'parent',generation:1,
        through_document_sequence:9,anchor:{checkpoint_id:'initial',revision:1,document_sequence:1,
          content_sha256:'sha256:'+'1'.repeat(64),block_ids:blocks.map(b=>b.id)},commands}}};
}
function replay(input,delta) {
  const doc=new Y.Doc();for(const frame of input.frames) Y.applyUpdate(doc,Buffer.from(frame,'base64'));
  Y.applyUpdate(doc,Buffer.from(delta.update,'base64'));return doc;
}
test('older deletion preserves the surviving attributed text and later concurrent input',()=>{
  const input=request(['a','c','d'],[cmd('delete','b',null,2)]);
  const doc=new Y.Doc();Y.applyUpdate(doc,Buffer.from(input.frames[0],'base64'));
  const text=doc.getMap('blocks').get('a').get('content');text.format(0,text.length,{bold:true,color:'#337ea9'});
  input.frames=[Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64')];
  const delta=transform(input);text.insert(0,'동시 입력 ');const expected=text.toDelta();
  Y.applyUpdate(doc,Buffer.from(delta.update,'base64'));
  assert.deepEqual(doc.getArray('block-order').toArray(),['a','c','d']);
  assert.equal(doc.getMap('blocks').has('b'),false);
  assert.equal(doc.getMap('blocks').get('a').get('content'),text);
  assert.deepEqual(text.toDelta(),expected);
  assert.equal(doc.getText('page-title').toString(),'현재 제목');doc.destroy();
});
test('a partially applied multiple-move history converges without reapplying moves',()=>{
  // Canonical: A,B,C,D -> B,C,A,D -> C,A,D,B. Projection applied only move B.
  const input=request(['c','a','d','b'],[cmd('move','a','c',2),cmd('move','b','d',3)],
    [blocks[0],blocks[2],blocks[3],blocks[1]]);
  const delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['c','a','d','b']);
  const next={...input,frames:[...input.frames,delta.update]};
  const second=transform(next);assert.equal(second.reconciled_structure_changed,false);
  assert.equal(second.changed,false);doc.destroy();
});
test('certified older reorder can combine with a proven current-tail insert',()=>{
  const input=request(['b','c','d','a'],[cmd('move','a','d',2),cmd('insert','new','a',8)]);
  input.seed.blocks.push({id:'new',type:'paragraph',content:'새 문단',props:{}});
  input.post_frame_insert_ids=['new'];
  const delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['b','c','d','a','new']);
  assert.deepEqual(delta.reconciled_insert_ids,['new']);doc.destroy();
});
for(const [label,change] of [
  ['wrong document',input=>input.post_frame_structure.historical_structure.document_id='elsewhere'],
  ['wrong generation',input=>input.post_frame_structure.historical_structure.generation=2],
  ['invalid hash',input=>input.post_frame_structure.historical_structure.anchor.content_sha256='sha256:'+'z'.repeat(64)],
  ['duplicate anchor',input=>input.post_frame_structure.historical_structure.anchor.block_ids.push('a')],
  ['reused sequence',input=>input.post_frame_structure.historical_structure.commands.push(cmd('move','a','d',2))],
  ['outside sequence',input=>input.post_frame_structure.historical_structure.commands[0].document_sequence=10],
  ['missing anchor',input=>input.post_frame_structure.historical_structure.commands[0].after_block_id='unknown'],
  ['unproven canonical order',input=>input.seed.blocks.reverse()],
  ['rich restoration',input=>input.post_frame_structure.historical_structure.commands[0].restore_deleted=true],
]) test(label+' refuses historical repair',()=>{
  const input=request(['b','c','d','a'],[cmd('move','a','d',2)]);change(input);assert.throws(()=>transform(input));
});
test('unexplained projected deletion refuses repair',()=>{
  const input=request(['b','c','d','a'],[cmd('move','a','d',2)],
    [...blocks,{id:'unknown',type:'paragraph',content:'새 협업 문단',props:{}}]);
  assert.throws(()=>transform(input),/projected identities/);
});
test('reordered untouched identities refuse repair',()=>{
  const input=request(['b','c','d','a'],[cmd('move','a','d',2)],
    [blocks[0],blocks[2],blocks[1],blocks[3]]);
  assert.throws(()=>transform(input),/untouched order/);
});
test('a unique older insertion uses canonical values without replacing existing text',()=>{
  const input=request(['b','c','d','a'],[cmd('move','a','d',2),cmd('insert','new','a',3)]);
  input.seed.blocks.push({id:'new',type:'paragraph',content:'새 문단',props:{}});
  const delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(delta.reconciled_insert_ids,['new']);
  assert.deepEqual(doc.getArray('block-order').toArray(),['b','c','d','a','new']);
  assert.equal(doc.getMap('blocks').get('new').get('content').toString(),'새 문단');doc.destroy();
});
test('legitimate CRDT-only ahead identities keep the existing compatibility behavior',()=>{
  const input=request(['a','b','c','d'],[],[...blocks,{id:'ahead',type:'paragraph',content:'협업 추가 문단',props:{}}]);
  const delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['a','b','c','d','ahead']);
  assert.equal(delta.changed,false);doc.destroy();
});
test('an exact DB archive certificate works without a typed block tombstone',()=>{
  const input=request(['a','c','d'],[{...cmd('delete','b',null,2),origin:'owned_database_trash'}]);
  input.post_frame_structure.retired_ids=[];
  const delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['a','c','d']);
  assert.equal(doc.getMap('blocks').has('b'),false);doc.destroy();
});
