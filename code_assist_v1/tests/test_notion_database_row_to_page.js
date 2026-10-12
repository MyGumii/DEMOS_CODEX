const {test}=require('node:test');
const assert=require('node:assert/strict');
const movement=require('../notion_static/notion_database_row_to_page.js');

function fixture(parentId='parent'){
  const scope={realm:'https://local.example',userId:'owner',workspaceId:'ws',pageId:'source'};
  const sourceWitness={document_id:'source',document_sequence:8,workspace_epoch:2,document_epoch:3};
  const parentWitness=parentId==='source'?sourceWitness
    :{document_id:parentId,document_sequence:12,workspace_epoch:2,document_epoch:4};
  const source={source:{document_id:'source',database_id:'database'},
    database:{effective_role:'edit_content',capabilities:{row_move_batch:true},
      rows:[{id:'row-a',version:2},{id:'row-b',version:3}]},witnesses:[sourceWitness]};
  const parent={page:{id:parentId,workspace_id:'ws',can_edit:true},witnesses:[parentWitness]};
  const saved=new Map(),receipts=new Map();let lost=true,writes=0,changed=0;
  const journal={list:async()=>[...saved.values()].map(entry=>structuredClone(entry)),
    put:async(_scope,entry)=>{saved.set(entry.operation_id,structuredClone(entry));},
    remove:async(_scope,id)=>{saved.delete(id);}};
  const host={journal,current:()=>true,canEdit:()=>true,
    operationId:()=>`row-page-${receipts.size+1}`,changed:async()=>{changed++;},
    send:async(_scope,body)=>{
      if(!receipts.has(body.operation_id)){
        writes++;
        const same=body.payload.parent_document_id==='source',count=body.payload.rows.length;
        receipts.set(body.operation_id,{
          contract:'code_assist.notion.collaborative_database_row_to_page_batch.v1',
          status:'database_command_committed',operation_id:body.operation_id,
          workspace_id:'ws',source_document_id:'source',source_database_id:'database',
          parent_document_id:parentId,command_kind:'database.rows.move_to_page',
          document_sequence:body.expected_document_sequence+(same?count+2:1),
          parent_document_sequence:body.payload.expected_parent_sequence+(same?count+2:count+1),
          rows:body.payload.rows.map(row=>({row_id:row.row_id,page_id:row.row_id,
            target_version:row.expected_target_version+1})),
        });
      }
      if(lost){lost=false;throw Error('response lost');}
      return structuredClone(receipts.get(body.operation_id));
    }};
  return {scope,source,parent,saved,host,writes:()=>writes,changed:()=>changed};
}

test('UI-R5 selected rows moving to an ordinary page recover after response loss',async()=>{
  const f=fixture(),first=movement.create(f.host);
  await assert.rejects(first.run(f.scope,'source-block',f.source,f.parent,
    ['row-a','row-b']),/response lost/);
  assert.equal(f.writes(),1);
  assert.equal(f.saved.size,1);
  const afterReload=movement.create(f.host);
  assert.equal(await afterReload.recover(f.scope),1);
  assert.equal(f.saved.size,0);
  assert.equal(f.writes(),1);
  assert.equal(f.changed(),1);
});

test('UI-R5 same-parent page placement verifies its combined sequence',async()=>{
  const f=fixture('source'),controller=movement.create(f.host);
  await assert.rejects(controller.run(f.scope,'source-block',f.source,f.parent,
    ['row-a']),/response lost/);
  assert.equal(await controller.recover(f.scope),1);
  assert.equal(f.writes(),1);
});

test('UI-R5 invalid page witness and untrusted receipt cannot clear the journal',async()=>{
  const f=fixture();let sends=0;
  const wrong={...f.parent,witnesses:[{...f.parent.witnesses[0],workspace_epoch:99}]};
  const controller=movement.create({...f.host,send:async()=>{sends++;}});
  await assert.rejects(controller.run(f.scope,'source-block',f.source,wrong,
    ['row-a']),/최신 상태/);
  assert.equal(sends,0);
  const badHost={...f.host,send:async(scope,body)=>{
    const result=await f.host.send(scope,body);
    return {...result,rows:[{...result.rows[0],page_id:'wrong-page'}]};
  }};
  const bad=movement.create(badHost);
  await assert.rejects(bad.run(f.scope,'source-block',f.source,f.parent,
    ['row-a']),/response lost/);
  await assert.rejects(bad.recover(f.scope),/요청과 일치하지 않습니다/);
  assert.equal(f.saved.size,1);
});
