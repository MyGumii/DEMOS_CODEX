const {test}=require('node:test');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const icons=require('../notion_static/notion_database_row_icon_batch.js');

test('selected-row icon response loss replays the same persisted operation once',async()=>{
  const scope={realm:'https://local.example',userId:'owner',workspaceId:'ws',pageId:'page'};
  const data={source:{database_id:'db'},database:{effective_role:'edit_content',capabilities:{row_icon_batch:true},
    rows:[{id:'row-a',version:2},{id:'row-b',version:3}]},
    witnesses:[{document_id:'page',document_sequence:8,workspace_epoch:1,document_epoch:1}]};
  const saved=new Map(),receipts=new Map();let lost=true,writes=0,changed=0;
  const journal={
    list:async()=>[...saved.values()].map(entry=>structuredClone(entry)),
    put:async(_scope,entry)=>{saved.set(entry.operation_id,structuredClone(entry));},
    remove:async(_scope,id)=>{saved.delete(id);},
  };
  const host={journal,current:()=>true,canEdit:()=>true,operationId:()=>`op-${receipts.size+1}`,
    changed:async()=>{changed++;},
    send:async(_scope,body)=>{
      if(!receipts.has(body.operation_id)){
        writes++;
        receipts.set(body.operation_id,{contract:'code_assist.notion.collaborative_database_row_icon_batch.v1',
          status:'database_command_committed',operation_id:body.operation_id,workspace_id:'ws',
          document_id:'page',database_id:'db',target_id:'db',command_kind:'database.rows.icon.set',
          document_sequence:9,rows:body.payload.rows.map(row=>({row_id:row.row_id,
            target_version:row.expected_target_version+1,appearance:{icon:body.payload.icon}}))});
      }
      if(lost){lost=false;throw Error('lost response');}
      return structuredClone(receipts.get(body.operation_id));
    },
  };
  const controller=icons.create(host);
  await assert.rejects(controller.run(scope,'block',data,['row-a','row-b'],'🚀'),/lost response/);
  assert.equal(writes,1);
  assert.equal(saved.size,1);
  assert.equal(changed,0);
  assert.equal(await controller.run(scope,'block',data,['row-a','row-b'],'🚀'),true);
  assert.equal(writes,1);
  assert.equal(saved.size,0);
  assert.equal(changed,1);
});

test('invalid durable storage or changed page stops an icon write',async()=>{
  const scope={realm:'local',userId:'owner',workspaceId:'ws',pageId:'page'};
  const data={source:{database_id:'db'},database:{effective_role:'edit_content',capabilities:{row_icon_batch:true},
    rows:[{id:'row-a',version:1}]},
    witnesses:[{document_id:'page',document_sequence:1,workspace_epoch:1,document_epoch:1}]};
  let sends=0,current=true;
  const controller=icons.create({journal:{list:async()=>[],put:async()=>{throw Error('storage full');},
    remove:async()=>{}},current:()=>current,canEdit:()=>true,
    operationId:()=>`op-${crypto.randomUUID()}`,send:async()=>{sends++;},changed:async()=>{}});
  await assert.rejects(controller.run(scope,'block',data,['row-a'],'😀'),/storage full/);
  current=false;
  await assert.rejects(controller.run(scope,'block',data,['row-a'],'😀'),/변경할 수 없습니다/);
  assert.equal(sends,0);
});
