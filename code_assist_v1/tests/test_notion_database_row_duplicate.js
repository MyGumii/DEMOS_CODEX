const {test}=require('node:test');
const assert=require('node:assert/strict');
const duplicate=require('../notion_static/notion_database_row_duplicate.js');

test('selected-row duplicate replays an uncertain request instead of creating a second copy',async()=>{
  const scope={realm:'https://local.example',userId:'owner',workspaceId:'ws',pageId:'page'};
  const data={source:{database_id:'db'},database:{effective_role:'edit_content',
    capabilities:{row_duplicate_batch:true},rows:[{id:'row-a',version:2},{id:'row-b',version:3}]},
    witnesses:[{document_id:'page',document_sequence:8,workspace_epoch:1,document_epoch:1}]};
  const saved=new Map(),receipts=new Map();let lost=true,writes=0,changed=0;
  const journal={list:async()=>[...saved.values()].map(entry=>structuredClone(entry)),
    put:async(_scope,entry)=>{saved.set(entry.operation_id,structuredClone(entry));},
    remove:async(_scope,id)=>{saved.delete(id);}};
  const host={journal,current:()=>true,canEdit:()=>true,operationId:()=>`op-${receipts.size+1}`,
    changed:async()=>{changed++;},
    send:async(_scope,body)=>{
      if(!receipts.has(body.operation_id)){
        writes++;
        receipts.set(body.operation_id,{contract:'code_assist.notion.collaborative_database_row_duplicate_batch.v1',
          status:'database_command_committed',operation_id:body.operation_id,workspace_id:'ws',
          document_id:'page',database_id:'db',target_id:'db',command_kind:'database.rows.duplicate',
          document_sequence:9,rows:body.payload.rows.map((row,index)=>({source_row_id:row.row_id,
            row_id:`copy-${row.row_id}`,position:index*2+1,target_version:1}))});
      }
      if(lost){lost=false;throw Error('lost response');}
      return structuredClone(receipts.get(body.operation_id));
    }};
  const controller=duplicate.create(host);
  await assert.rejects(controller.run(scope,'block',data,['row-a','row-b']),/lost response/);
  assert.equal(writes,1);
  assert.equal(saved.size,1);
  assert.equal(changed,0);
  assert.equal(await controller.run(scope,'block',data,['row-a','row-b']),true);
  assert.equal(writes,1);
  assert.equal(saved.size,0);
  assert.equal(changed,1);
});

test('invalid durable storage and stale screen prevent duplication',async()=>{
  const scope={realm:'local',userId:'owner',workspaceId:'ws',pageId:'page'};
  const data={source:{database_id:'db'},database:{effective_role:'edit_content',
    capabilities:{row_duplicate_batch:true},rows:[{id:'row-a',version:1}]},
    witnesses:[{document_id:'page',document_sequence:1,workspace_epoch:1,document_epoch:1}]};
  let sends=0,current=true;
  const controller=duplicate.create({journal:{list:async()=>[],put:async()=>{throw Error('storage full');},
    remove:async()=>{}},current:()=>current,canEdit:()=>true,
    operationId:()=>`op-a`,send:async()=>{sends++;},changed:async()=>{}});
  await assert.rejects(controller.run(scope,'block',data,['row-a']),/storage full/);
  current=false;
  await assert.rejects(controller.run(scope,'block',data,['row-a']),/복제할 수 없습니다/);
  assert.equal(sends,0);
});
