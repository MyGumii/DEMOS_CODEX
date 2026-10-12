'use strict';
const assert=require('node:assert/strict');
const Cells=require('../notion_static/notion_independent_database_cells.js');

const scope={realm:'https://example.test',userId:'editor',workspaceId:'workspace-e1',databaseId:'database-e1'};
const projection={role:'edit_content',document_sequence:7,workspace_epoch:2,
  document_epoch:3,database_epoch:4,
  database:{id:'database-e1',properties:[{id:'title',type:'title'}],
    rows:[{id:'row-e1',cells:{title:'Before'},cell_versions:{title:5}}]}};
const journalEntries=new Map(),sent=[],changed=[];
let responseMode='lost',allowed=true;
const journal={
  async list(){return [...journalEntries.values()].map(entry=>structuredClone(entry));},
  async put(_scope,entry){journalEntries.set(entry.operation_id,structuredClone(entry));},
  async remove(_scope,id){journalEntries.delete(id);},
};
const controller=Cells.create({journal,current:()=>true,canEdit:()=>allowed,
  operationId:()=>`operation-${sent.length+1}`,
  send:async(_scope,request)=>{
    sent.push(structuredClone(request));
    if(responseMode==='lost')throw Error('Failed to fetch');
    return {contract:'code_assist.notion.database_sharing.v1',workspace_id:scope.workspaceId,
      database_id:scope.databaseId,operation_id:request.operation_id,
      document_sequence:request.expected_document_sequence+1,
      cells:request.cells.map(cell=>({row_id:cell.row_id,property_id:cell.property_id,
        target_version:cell.expected_target_version+1})),replayed:sent.length>1};
  },changed:async(_scope,result)=>changed.push(result),state:()=>{},
});

(async()=>{
  await controller.bind(scope,projection);
  const cell={row_id:'row-e1',property_id:'title',value:'After',expected_target_version:5};
  await assert.rejects(controller.change(scope,cell),/Failed to fetch/);
  assert.equal(controller.status(scope).pending,1);
  assert.deepEqual(controller.status(scope).cells,[cell]);
  assert.equal(journalEntries.size,1,'lost responses retain the exact request');
  responseMode='replay';
  await controller.retry(scope);
  assert.deepEqual(sent[0],sent[1],'retry reuses one operation and witness');
  assert.equal(controller.status(scope).pending,0);
  assert.equal(journalEntries.size,0);
  assert.equal(changed.length,1);
  await controller.bind(scope,{...projection,role:'view'});
  await assert.rejects(controller.change(scope,cell),/편집 권한/);
  assert.equal(sent.length,2);
  const refreshEntries=new Map(),refreshSends=[];
  const refreshController=Cells.create({
    journal:{async list(){return [...refreshEntries.values()];},
      async put(_scope,entry){refreshEntries.set(entry.operation_id,structuredClone(entry));},
      async remove(_scope,id){refreshEntries.delete(id);}},
    current:()=>true,canEdit:()=>true,operationId:()=>`refresh-operation`,
    send:async(_scope,request)=>{
      refreshSends.push(structuredClone(request));
      return {contract:'code_assist.notion.database_sharing.v1',
        workspace_id:scope.workspaceId,database_id:scope.databaseId,
        operation_id:request.operation_id,
        document_sequence:request.expected_document_sequence+1,
        cells:[{row_id:'row-e1',property_id:'title',target_version:6}]};
    },changed:async()=>{throw Error('refresh unavailable');},state:()=>{},
  });
  await refreshController.bind(scope,projection);
  await refreshController.change(scope,cell);
  assert.equal(refreshController.status(scope).pending,0,'confirmed saves are not retried after refresh failure');
  assert.match(refreshController.status(scope).error,/저장은 완료됐지만/);
  assert.equal(await refreshController.retry(scope),false);
  assert.equal(refreshSends.length,1);
  console.log('UI-R10 independent database cells: durable replay and role downgrade passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
