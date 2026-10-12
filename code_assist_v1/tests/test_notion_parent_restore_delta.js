'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {transform}=require('../notion_collaboration/page_move_delta.cjs');
const Y=globalThis.NotionYjs;
const rich={id:'rich',type:'paragraph',content:'한글 원래 서식',props:{color:'blue'}},other={id:'other',type:'paragraph',content:'다른 문단',props:{}};
function request({visible=false,replacement=false,orphan=false}={}) {
  const base={document_id:'parent',generation:1,frames:[],seed:{title:'제목 원문',blocks:[rich,other]},child_id:'absent',insert:null};
  const bootstrap=transform(base).update,doc=new Y.Doc();Y.applyUpdate(doc,Buffer.from(bootstrap,'base64'));
  const text=doc.getMap('blocks').get('rich').get('content');text.format(0,text.length,{bold:true,italic:true});
  const archived=Buffer.from(Y.encodeStateAsUpdate(doc));
  if(!visible){doc.getArray('block-order').delete(0,1);if(!orphan)doc.getMap('blocks').delete('rich');}
  const current=Buffer.from(Y.encodeStateAsUpdate(doc));doc.destroy();
  const values=replacement?{id:'rich',type:'heading2',content:'명시한 새 본문',props:{color:'red'}}:rich;
  const command={command_id:'restore-command',document_sequence:4,kind:'block.insert',target_id:'rich',after_block_id:null,restore_deleted:true};
  return {...base,seed:{title:'오래된 제목',blocks:[values,other]},frames:[current.toString('base64')],post_frame_insert_ids:['rich'],
    post_frame_structure:{contract:'code_assist.notion.parent_structure_witness.v1',document_id:'parent',generation:1,
      through_document_sequence:3,commands:[command],retired_ids:[],historical_move_ids:[],restores:[{
        contract:'code_assist.notion.parent_restore_recovery.v1',document_id:'parent',generation:1,
        command_id:'restore-command',document_sequence:4,target_id:'rich',deletion_command_id:'deleted-command',
        deletion_document_sequence:2,deleted_version:1,restored_version:2,through_update_sequence:1,values,
        frames:[{payload:archived.toString('base64'),sha256:'sha256:'+crypto.createHash('sha256').update(archived).digest('hex')}],frame_bytes:archived.length}]}};
}
function replay(input,delta) {
  const doc=new Y.Doc();for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
  Y.applyUpdate(doc,Buffer.from(delta.update,'base64'));return doc;
}
test('an actually removed rich block is cloned from its exact deletion range',()=>{
  const input=request(),delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['rich','other']);
  assert.deepEqual(doc.getMap('blocks').get('rich').get('content').toDelta(),[{insert:rich.content,attributes:{bold:true,italic:true}}]);
  assert.equal(doc.getText('page-title').toString(),'제목 원문');
  assert.deepEqual(delta.reconciled_insert_ids,['rich']);doc.destroy();
});
test('explicit replacement uses exactly the requested fields',()=>{
  const input=request({replacement:true}),delta=transform(input),doc=replay(input,delta);
  const item=doc.getMap('blocks').get('rich');assert.equal(item.get('type'),'heading2');
  assert.deepEqual(item.get('content').toDelta(),[{insert:'명시한 새 본문'}]);
  assert.deepEqual(JSON.parse(item.get('props')),{color:'red'});doc.destroy();
});
test('a visible restored identity retains its map, attributes and concurrent text',()=>{
  const input=request({visible:true}),delta=transform(input),doc=new Y.Doc();Y.applyUpdate(doc,Buffer.from(input.frames[0],'base64'));
  const text=doc.getMap('blocks').get('rich').get('content');text.insert(0,'동시 입력 ');const expected=text.toDelta();
  Y.applyUpdate(doc,Buffer.from(delta.update,'base64'));
  assert.equal(doc.getMap('blocks').get('rich').get('content'),text);assert.deepEqual(text.toDelta(),expected);
  assert.equal(delta.changed,false);doc.destroy();
});
test('an orphan map can be restored only with its exact deletion witness',()=>{
  const input=request({orphan:true}),delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['rich','other']);
  assert.deepEqual(doc.getMap('blocks').get('rich').get('content').toDelta(),[{insert:rich.content,attributes:{bold:true,italic:true}}]);doc.destroy();
});
for(const[label,change]of[
  ['missing witness',input=>input.post_frame_structure.restores=[]],
  ['wrong generation',input=>input.post_frame_structure.restores[0].generation=2],
  ['wrong target',input=>input.post_frame_structure.restores[0].target_id='other'],
  ['wrong version',input=>input.post_frame_structure.restores[0].restored_version=9],
  ['wrong command',input=>input.post_frame_structure.restores[0].command_id='other-command'],
  ['bad checksum',input=>input.post_frame_structure.restores[0].frames[0].sha256='sha256:'+'0'.repeat(64)],
  ['bad byte count',input=>input.post_frame_structure.restores[0].frame_bytes++],
  ['changed canonical body',input=>input.seed.blocks[0]={...input.seed.blocks[0],content:'unexplained'}],
])test(label+' refuses restoration before a delta',()=>{
  const input=request();change(input);assert.throws(()=>transform(input));
});
