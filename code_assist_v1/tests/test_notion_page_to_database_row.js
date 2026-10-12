const {test}=require('node:test');
const assert=require('node:assert/strict');
const returning=require('../notion_static/notion_page_to_database_row.js');

function fixture(sameParent=false,root=false){
  const scope={realm:'https://local.example',userId:'owner',workspaceId:'ws'};
  const target={contract:'code_assist.notion.page_to_database_row_target.v1',
    workspace_id:'ws',page_id:'row-a',row_id:'row-a',
    parent_document_id:root?'':sameParent?'source':'parent',
    destination_document_id:'source',destination_database_id:'database',
    expected_page_sequence:5,expected_parent_sequence:root?0:sameParent?12:10,
    expected_destination_sequence:12,expected_row_version:4,
    expected_workspace_epoch:2,expected_page_epoch:3,
    expected_parent_epoch:root?0:4,expected_destination_epoch:4};
  const saved=new Map(),receipts=new Map();let lost=true,writes=0,changed=0;
  const journal={list:async()=>[...saved.values()].map(entry=>structuredClone(entry)),
    put:async(_scope,entry)=>{saved.set(entry.operation_id,structuredClone(entry));},
    remove:async(_scope,id)=>{saved.delete(id);}};
  const host={journal,current:()=>true,canEdit:()=>true,
    operationId:()=>`return-${receipts.size+1}`,changed:async()=>{changed++;},
    send:async(_scope,pageId,body)=>{
      assert.equal(pageId,'row-a');
      if(!receipts.has(body.operation_id)){
        writes++;
        receipts.set(body.operation_id,{
          contract:'code_assist.notion.collaborative_page_to_database_row.v1',
          status:'page_returned_to_database',operation_id:body.operation_id,
          workspace_id:'ws',page_id:'row-a',row_id:'row-a',
          parent_document_id:target.parent_document_id,
          destination_document_id:'source',destination_database_id:'database',
          target_version:body.expected_row_version+1,
          document_sequence:body.expected_destination_sequence+(sameParent?3:1),
          parent_document_sequence:root?0:body.expected_parent_sequence+(sameParent?3:2),
        });
      }
      if(lost){lost=false;throw Error('response lost');}
      return structuredClone(receipts.get(body.operation_id));
    }};
  return {scope,target,saved,host,writes:()=>writes,changed:()=>changed};
}

for(const mode of ['cross','same','root'])test(`UI-R5 page return replays one ${mode}-parent result`,async()=>{
  const f=fixture(mode==='same',mode==='root'),first=returning.create(f.host);
  await assert.rejects(first.run(f.scope,f.target),/response lost/);
  assert.equal(f.saved.size,1);
  const reloaded=returning.create(f.host);
  assert.equal(await reloaded.recover(f.scope),1);
  assert.equal(f.writes(),1);
  assert.equal(f.saved.size,0);
  assert.equal(f.changed(),1);
});

test('UI-R5 root return rejects a forged parent fence',async()=>{
  const f=fixture(false,true);
  await assert.rejects(returning.create(f.host).run(f.scope,
    {...f.target,expected_parent_sequence:1}),/복귀 요청의 최신 문서 상태/);
  assert.equal(f.saved.size,0);
});

test('UI-R5 untrusted return receipt leaves its request available for recovery',async()=>{
  const f=fixture(),bad=returning.create({...f.host,
    send:async(scope,page,body)=>{
      const result=await f.host.send(scope,page,body);
      return {...result,destination_database_id:'other'};
    }});
  await assert.rejects(bad.run(f.scope,f.target),/response lost/);
  await assert.rejects(bad.recover(f.scope),/요청과 일치하지 않습니다/);
  assert.equal(f.saved.size,1);
});

test('UI-R5 same-parent return rejects mismatched document sequences',async()=>{
  const f=fixture(true),bad=returning.create({...f.host,
    send:async(scope,page,body)=>{
      const result=await f.host.send(scope,page,body);
      return {...result,parent_document_sequence:result.document_sequence-1};
    }});
  await assert.rejects(bad.run(f.scope,f.target),/response lost/);
  await assert.rejects(bad.recover(f.scope),/요청과 일치하지 않습니다/);
  assert.equal(f.saved.size,1);
});
