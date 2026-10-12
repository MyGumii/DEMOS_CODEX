const {test}=require('node:test');
const assert=require('node:assert/strict');
const movement=require('../notion_static/notion_database_row_move.js');

function fixture(){
  const scope={realm:'https://local.example',userId:'owner',workspaceId:'ws',pageId:'page'};
  const witness={document_id:'page',document_sequence:8,workspace_epoch:1,document_epoch:1};
  const source={source:{document_id:'page',database_id:'source'},
    database:{effective_role:'edit_content',capabilities:{row_move_batch:true},
      rows:[{id:'row-a',version:2},{id:'row-b',version:3}]},witnesses:[witness]};
  const destination={source:{document_id:'page',database_id:'destination'},
    database:{effective_role:'edit_content',capabilities:{row_move_batch:true},rows:[]},witnesses:[witness]};
  const saved=new Map(),receipts=new Map();let lost=true,writes=0,changed=0;
  const journal={list:async()=>[...saved.values()].map(entry=>structuredClone(entry)),
    put:async(_scope,entry)=>{saved.set(entry.operation_id,structuredClone(entry));},
    remove:async(_scope,id)=>{saved.delete(id);}};
  const host={journal,current:()=>true,canEdit:()=>true,
    operationId:()=>`move-${receipts.size+1}`,changed:async()=>{changed++;},
    send:async(_scope,body)=>{
      if(!receipts.has(body.operation_id)){
        writes++;
        receipts.set(body.operation_id,{contract:'code_assist.notion.collaborative_database_row_move_batch.v1',
          status:'database_command_committed',operation_id:body.operation_id,workspace_id:'ws',
          source_document_id:'page',destination_document_id:'page',
          source_database_id:'source',destination_database_id:'destination',
          command_kind:'database.rows.move',document_sequence:9,
          destination_document_sequence:body.payload.expected_destination_sequence+1,
          rows:body.payload.rows.map((row,index)=>({row_id:row.row_id,
            target_version:row.expected_target_version+1,position:index}))});
      }
      if(lost){lost=false;throw Error('response lost');}
      return structuredClone(receipts.get(body.operation_id));
    }};
  return {scope,source,destination,saved,receipts,host,
    writes:()=>writes,changed:()=>changed};
}

test('selected-row move retries its stored operation after a lost response',async()=>{
  const f=fixture(),controller=movement.create(f.host);
  await assert.rejects(controller.run(f.scope,'source-block',f.source,f.destination,
    ['row-a','row-b']),/response lost/);
  assert.equal(f.writes(),1);
  assert.equal(f.saved.size,1);
  assert.equal(f.changed(),0);
  assert.equal(await controller.run(f.scope,'source-block',f.source,f.destination,
    ['row-a','row-b']),true);
  assert.equal(f.writes(),1);
  assert.equal(f.saved.size,0);
  assert.equal(f.changed(),1);
});

test('a new controller can recover a saved move without the old row selection',async()=>{
  const f=fixture(),beforeReload=movement.create(f.host);
  await assert.rejects(beforeReload.run(f.scope,'source-block',f.source,f.destination,
    ['row-a','row-b']),/response lost/);
  const afterReload=movement.create(f.host);
  assert.equal((await afterReload.pendingAll(f.scope)).length,1);
  assert.equal(await afterReload.recover(f.scope),1);
  assert.equal(f.writes(),1);
  assert.equal(f.changed(),1);
  assert.equal(f.saved.size,0);
});

test('selected-row move refuses to send when durable storage fails or witnesses differ',async()=>{
  const f=fixture();let sends=0;
  const controller=movement.create({...f.host,journal:{list:async()=>[],
    put:async()=>{throw Error('storage full');},remove:async()=>{}},
    send:async()=>{sends++;}});
  await assert.rejects(controller.run(f.scope,'source-block',f.source,f.destination,
    ['row-a']),/storage full/);
  const stale={...f.destination,witnesses:[{...f.destination.witnesses[0],document_sequence:9}]};
  await assert.rejects(controller.run(f.scope,'source-block',f.source,stale,
    ['row-a']),/문서 상태/);
  assert.equal(sends,0);
});

test('cross-document destination keeps its own witness and survives reload recovery',async()=>{
  const f=fixture();
  f.destination.source.document_id='other-page';
  f.destination.witnesses=[{document_id:'other-page',document_sequence:14,
    workspace_epoch:1,document_epoch:2}];
  const send=f.host.send;
  f.host.send=async(scope,body)=>{
    const result=await send(scope,body);
    result.destination_document_id='other-page';
    return result;
  };
  const beforeReload=movement.create(f.host);
  await assert.rejects(beforeReload.run(f.scope,'source-block',f.source,f.destination,
    ['row-a']),/response lost/);
  const entry=[...f.saved.values()][0];
  assert.equal(entry.request.payload.destination_document_id,'other-page');
  assert.equal(entry.request.payload.expected_destination_sequence,14);
  assert.equal(entry.request.payload.expected_destination_epoch,2);
  assert.equal(movement.valid(entry,f.scope,'source'),true);
  const afterReload=movement.create(f.host);
  assert.equal(await afterReload.recover(f.scope),1);
  assert.equal(f.writes(),1);
  assert.equal(f.saved.size,0);
});
