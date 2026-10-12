'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {transform}=require('../notion_collaboration/page_move_delta.cjs');
const Y=globalThis.NotionYjs;
const oldBlocks=[
  {id:'rich',type:'heading1',content:'서식 원문',props:{color:'blue'}},
  {id:'victim',type:'paragraph',content:'삭제할 문단',props:{}},
  {id:'table',type:'simple_table',content:'',props:{rows:[['한글','B']]}}
];
function command(kind,id,after=null,sequence=2) {
  return {command_id:'command-'+sequence,document_sequence:sequence,kind:'block.'+kind,
    target_id:id,after_block_id:after,restore_deleted:false};
}
function input(blocks,commands) {
  const base={document_id:'parent',generation:1,frames:[],seed:{title:'제목 원문',blocks:oldBlocks},child_id:'absent',insert:null};
  const bootstrap=transform(base).update;
  return {...base,frames:[bootstrap],seed:{title:'오래된 SQL 제목',blocks},post_frame_insert_ids:[],
    post_frame_structure:{contract:'code_assist.notion.parent_structure_witness.v1',document_id:'parent',generation:1,
      through_document_sequence:1,commands,retired_ids:[],historical_move_ids:[]}};
}
function replay(request,delta) {
  const doc=new Y.Doc();for(const frame of request.frames) Y.applyUpdate(doc,Buffer.from(frame,'base64'));
  Y.applyUpdate(doc,Buffer.from(delta.update,'base64'));return doc;
}
test('typed deletion removes only the witnessed body and map',()=>{
  const request=input([oldBlocks[0],oldBlocks[2]],[command('delete','victim')]);
  const delta=transform(request),doc=replay(request,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['rich','table']);
  assert.equal(doc.getMap('blocks').has('victim'),false);
  assert.equal(doc.getText('page-title').toString(),'제목 원문');
  assert.equal(delta.reconciled_structure_changed,true);doc.destroy();
});
test('typed reorder preserves attributed Y.Text, concurrent input and table cells',()=>{
  const request=input([oldBlocks[1],oldBlocks[2],oldBlocks[0]],[command('move','rich','table')]);
  const source=new Y.Doc();Y.applyUpdate(source,Buffer.from(request.frames[0],'base64'));
  const text=source.getMap('blocks').get('rich').get('content');text.format(0,text.length,{bold:true});
  request.frames=[Buffer.from(Y.encodeStateAsUpdate(source)).toString('base64')];
  const delta=transform(request);
  text.insert(0,'동시 입력 ');const attributed=text.toDelta();
  Y.applyUpdate(source,Buffer.from(delta.update,'base64'));
  assert.deepEqual(text.toDelta(),attributed);
  assert.equal(source.getMap('blocks').get('rich').get('content'),text);
  assert.deepEqual(source.getArray('block-order').toArray(),['victim','table','rich']);
  assert.deepEqual(JSON.parse(source.getMap('blocks').get('table').get('props')).rows,[['한글','B']]);
  source.destroy();
});
test('an inserted then deleted transient identity is never resurrected',()=>{
  const request=input(oldBlocks,[command('insert','transient','rich'),command('move','transient',null,3),command('delete','transient',null,4)]);
  const delta=transform(request),doc=replay(request,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),oldBlocks.map(block=>block.id));
  assert.equal(doc.getMap('blocks').has('transient'),false);
  assert.equal(delta.reconciled_structure_changed,false);doc.destroy();
});
for(const [label,change] of [
  ['unexplained order',request=>request.seed.blocks.reverse()],
  ['missing anchor',request=>request.post_frame_structure.commands[0].after_block_id='unknown'],
  ['wrong document',request=>request.post_frame_structure.document_id='other'],
  ['wrong generation',request=>request.post_frame_structure.generation=2],
  ['reused sequence',request=>request.post_frame_structure.commands.push({...request.post_frame_structure.commands[0]})],
  ['unscoped restore',request=>request.post_frame_structure.commands[0].restore_deleted=true],
]) test(label+' refuses a new durable delta',()=>{
  const request=input([oldBlocks[1],oldBlocks[2],oldBlocks[0]],[command('move','rich','table')]);
  change(request);assert.throws(()=>transform(request));
});
test('an older deleted identity cannot leak into a new link delta',()=>{
  const request=input([oldBlocks[0],oldBlocks[2]],[]);request.post_frame_structure.retired_ids=['victim'];
  assert.throws(()=>transform(request),/Older parent deletion/);
});
test('an older unexplained typed order requires recovery',()=>{
  const request=input([oldBlocks[1],oldBlocks[2],oldBlocks[0]],[]);request.post_frame_structure.historical_move_ids=['rich'];
  assert.throws(()=>transform(request),/Older parent order/);
});
