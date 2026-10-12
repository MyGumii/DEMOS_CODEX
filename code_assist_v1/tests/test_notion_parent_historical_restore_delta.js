'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {transform}=require('../notion_collaboration/page_move_delta.cjs');
const Y=globalThis.NotionYjs;
const rich={id:'rich',type:'paragraph',content:'원래 한글 서식',props:{color:'blue'}},other={id:'other',type:'paragraph',content:'다른 문단',props:{}};
function request({visible=false,replacement=false,orphan=false}={}) {
  const base={document_id:'parent',generation:1,frames:[],seed:{title:'제목 원문',blocks:[rich,other]},child_id:'absent',insert:null};
  const doc=new Y.Doc();Y.applyUpdate(doc,Buffer.from(transform(base).update,'base64'));
  const text=doc.getMap('blocks').get('rich').get('content');text.format(0,text.length,{bold:true,italic:true});
  const archive=Buffer.from(Y.encodeStateAsUpdate(doc));
  if(!visible){doc.getArray('block-order').delete(0,1);if(!orphan)doc.getMap('blocks').delete('rich');}
  doc.getMap('blocks').get('other').get('content').insert(0,'나중 입력 ');
  const current=Buffer.from(Y.encodeStateAsUpdate(doc));doc.destroy();
  const values=replacement?{id:'rich',type:'heading2',content:'명시한 새 내용',props:{color:'red'}}:rich;
  const deletion={command_id:'delete-command',document_sequence:2,kind:'block.delete',target_id:'rich',after_block_id:null,restore_deleted:false,target_version:1};
  const restore={command_id:'restore-command',document_sequence:4,kind:'block.insert',target_id:'rich',after_block_id:null,restore_deleted:true};
  const proof={contract:'code_assist.notion.parent_restore_recovery.v1',document_id:'parent',generation:1,command_id:restore.command_id,
    document_sequence:4,target_id:'rich',deletion_command_id:deletion.command_id,deletion_document_sequence:2,
    deleted_version:1,restored_version:2,through_update_sequence:1,values,frame_bytes:archive.length,historical:true,
    frames:[{payload:archive.toString('base64'),sha256:'sha256:'+crypto.createHash('sha256').update(archive).digest('hex')}]};
  const binding=Object.fromEntries(Object.entries(proof).filter(([key])=>!['frames','frame_bytes','historical'].includes(key)));
  return {...base,frames:[current.toString('base64')],seed:{title:'오래된 제목',blocks:[values,other]},post_frame_insert_ids:[],
    post_frame_structure:{contract:'code_assist.notion.parent_structure_witness.v1',document_id:'parent',generation:1,
      through_document_sequence:6,commands:[],retired_ids:[],historical_move_ids:[],restores:[proof],
      historical_structure:{contract:'code_assist.notion.parent_historical_structure.v1',document_id:'parent',generation:1,
        through_document_sequence:6,anchor:{checkpoint_id:'initial',revision:1,document_sequence:1,
          content_sha256:'sha256:'+'1'.repeat(64),block_ids:['rich','other']},commands:[deletion,restore],restore_bindings:[binding]}}};
}
function replay(input,delta) {
  const doc=new Y.Doc();for(const frame of input.frames)Y.applyUpdate(doc,Buffer.from(frame,'base64'));
  Y.applyUpdate(doc,Buffer.from(delta.update,'base64'));return doc;
}
for(const replacement of [false,true])test('older missing restore retains later unrelated input; replacement='+replacement,()=>{
  const input=request({replacement}),delta=transform(input),doc=replay(input,delta);
  assert.deepEqual(doc.getArray('block-order').toArray(),['rich','other']);
  assert.deepEqual(doc.getMap('blocks').get('rich').get('content').toDelta(),replacement?
    [{insert:'명시한 새 내용'}]:[{insert:rich.content,attributes:{bold:true,italic:true}}]);
  assert.equal(doc.getMap('blocks').get('other').get('content').toString(),'나중 입력 다른 문단');
  assert.equal(doc.getText('page-title').toString(),'제목 원문');doc.destroy();
});
for(const replacement of [false,true])test('a proven newer restored map retains later target edits; replacement='+replacement,()=>{
  const input=request({replacement}),first=transform(input),doc=replay(input,first);
  const item=doc.getMap('blocks').get('rich'),text=item.get('content');text.insert(0,'새 입력 ',{underline:true});
  const expected=text.toDelta();input.frames=[Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64')];
  const next=transform(input);Y.applyUpdate(doc,Buffer.from(next.update,'base64'));
  assert.equal(doc.getMap('blocks').get('rich'),item);assert.deepEqual(text.toDelta(),expected);
  assert.equal(next.changed,false);doc.destroy();
});
test('original-value restore on its older visible map retains later text and attributes',()=>{
  const input=request({visible:true}),doc=replay(input,{update:Buffer.from([0,0]).toString('base64')});
  const text=doc.getMap('blocks').get('rich').get('content');text.insert(0,'나중 본문 ',{underline:true});
  const expected=text.toDelta();input.frames=[Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64')];
  Y.applyUpdate(doc,Buffer.from(transform(input).update,'base64'));assert.deepEqual(text.toDelta(),expected);doc.destroy();
});
test('ambiguous replacement on a still older map is refused',()=>{
  assert.throws(()=>transform(request({visible:true,replacement:true})),/unexplained older map/);
});
test('exact historical recovery replaces an orphan map without losing archived attributes',()=>{
  const input=request({orphan:true}),doc=replay(input,transform(input));
  assert.deepEqual(doc.getMap('blocks').get('rich').get('content').toDelta(),[{insert:rich.content,attributes:{bold:true,italic:true}}]);doc.destroy();
});
for(const[label,change]of[
  ['missing recovery',input=>input.post_frame_structure.restores=[]],
  ['wrong frame boundary',input=>input.post_frame_structure.restores[0].historical=false],
  ['missing deletion version',input=>delete input.post_frame_structure.historical_structure.commands[0].target_version],
  ['wrong preceding deletion',input=>input.post_frame_structure.historical_structure.commands[0].command_id='another-delete'],
  ['wrong header binding',input=>input.post_frame_structure.historical_structure.restore_bindings[0].deleted_version=9],
  ['wrong recovery checksum',input=>input.post_frame_structure.restores[0].frames[0].sha256='sha256:'+'0'.repeat(64)],
])test(label+' refuses older restoration before a delta',()=>{const input=request();change(input);assert.throws(()=>transform(input));});
