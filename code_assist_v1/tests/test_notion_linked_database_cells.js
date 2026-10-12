'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const api=require('../notion_static/notion_linked_database_cells.js');
const copy=structuredClone;
function fixture({scoped=false}={}){
  const scope={realm:'https://test.invalid',userId:'owner',workspaceId:'ws',pageId:'instance'},records=new Map(),receipts=new Map(),sent=[],states=[];
  const witness=id=>({document_id:id,workspace_id:'ws',workspace_epoch:1,document_sequence:1,document_epoch:1,generation:1});
  const data={source:{document_id:'source',database_id:'db'},witnesses:[witness('instance'),witness('source')],database:{effective_role:'edit_content',rows:[{id:'row',cells:{title:'Before',date:'2026-09-16'},cell_versions:{title:1,date:1}}]}};
  if(scoped){
    data.source={database_id:'db'};
    data.instance_witness={workspace_epoch:1,document_sequence:1,document_epoch:1,generation:1};
    data.access={workspace_epoch:1,document_sequence:1,document_epoch:1,database_epoch:1};
    delete data.witnesses;
  }
  let active=true,edit=true,count=0,lose=false,corrupt=null,controller;
  const journal={list:async()=>[...records.values()].map(value=>copy(value)),put:async(s,e)=>{assert.equal(s.page_id,'linked-cells:instance');records.set(e.operation_id,copy(e));},remove:async(s,id)=>records.delete(id)};
  const host={current:()=>active,canEdit:()=>edit,operationId:()=>`operation-${++count}`,journal,state:(_,__,state)=>states.push(copy(state)),
    changed:async()=>controller.bind(scope,'block',data),
    send:async(_,__,body)=>{
      assert.ok(records.has(body.operation_id),'must persist before sending');sent.push(copy(body));
      if(receipts.has(body.operation_id))return copy(receipts.get(body.operation_id));
      const row=data.database.rows[0];
      for(const c of body.cells)if(c.expected_target_version!==row.cell_versions[c.property_id])throw Error('version conflict');
      const cells=body.cells.map(c=>{row.cells[c.property_id]=c.value;row.cell_versions[c.property_id]++;return {...c,target_version:row.cell_versions[c.property_id]};});
      if(scoped)data.access.document_sequence++;
      else data.witnesses[1].document_sequence++;
      const result=scoped
        ?{contract:'code_assist.notion.database_sharing.v1',workspace_id:'ws',database_id:'db',
          operation_id:body.operation_id,document_sequence:data.access.document_sequence,cells}
        :{workspace_id:'ws',document_id:'source',database_id:'db',command_kind:'database.cells.set',linked_instance:{document_id:'instance',block_id:'block'},cells};
      if(corrupt){const change=corrupt;corrupt=null;change(result);}
      receipts.set(body.operation_id,copy(result));if(lose){lose=false;throw Error('lost response');}return result;
    }};
  controller=api.create(host);
  return {scope,data,records,sent,states,host,controller,cells:()=>[{row_id:'row',property_id:'title',value:'After',expected_target_version:1}],
    lose:()=>{lose=true;},corrupt:change=>{corrupt=change;},navigate:()=>{active=false;},readonly:()=>{edit=false;},recreate:()=>{controller=api.create(host);return controller;}};
}

test('UI-LINKED-CELLS direct change and undo/redo use source versions without mutating the view',async()=>{
  const f=fixture();await f.controller.bind(f.scope,'block',f.data);
  assert.equal(f.controller.status(f.scope,'block').canUndo,false);
  await f.controller.change(f.scope,'block',f.cells());assert.equal(f.data.database.rows[0].cells.title,'After');assert.equal(f.records.size,0);
  assert.equal(f.controller.status(f.scope,'block').canUndo,true);
  await f.controller.undo(f.scope,'block');assert.equal(f.data.database.rows[0].cells.title,'Before');
  assert.equal(f.controller.status(f.scope,'block').canUndo,false);
  await f.controller.redo(f.scope,'block');assert.equal(f.data.database.rows[0].cells.title,'After');
  assert.deepEqual(f.sent.map(r=>r.cells[0].expected_target_version),[1,2,3]);
  assert.ok(f.sent.every(r=>r.source.document_id==='source'&&r.instance_witness.document_id==='instance'));
});

test('UI-LINKED-CELLS independent source writes and exact retry omit the private parent ID',async()=>{
  const f=fixture({scoped:true});await f.controller.bind(f.scope,'block',f.data);f.lose();
  await assert.rejects(f.controller.change(f.scope,'block',f.cells()),/lost response/);
  assert.equal(f.records.size,1);
  assert.deepEqual(Object.keys(f.sent[0]).sort(),['cells','instance_witness','operation_id','source','source_access']);
  assert.equal(JSON.stringify(f.sent[0]).includes('source-page'),false);
  assert.equal(JSON.stringify(f.sent[0]).includes('document_id'),false);
  const restarted=f.recreate();await restarted.bind(f.scope,'block',f.data);
  assert.equal(f.records.size,0);assert.deepEqual(f.sent[0],f.sent[1]);
  await restarted.undo(f.scope,'block');
  assert.equal(f.data.database.rows[0].cells.title,'Before');
});

test('UI-LINKED-CELLS scoped receipt rejects a leaked parent ID and preserves recovery',async()=>{
  const f=fixture({scoped:true});await f.controller.bind(f.scope,'block',f.data);
  f.corrupt(result=>{result.document_id='source';});
  await assert.rejects(f.controller.change(f.scope,'block',f.cells()),/결과/);
  assert.equal(f.records.size,1);
  assert.equal(f.sent.length,1);
  assert.equal(JSON.stringify(f.sent[0]).includes('document_id'),false);
});

test('UI-LINKED-CELLS structured date range remains one source cell through undo',async()=>{
  const f=fixture();await f.controller.bind(f.scope,'block',f.data);
  const range={start:'2026-09-16',end:'2026-09-18'};
  await f.controller.change(f.scope,'block',[{row_id:'row',property_id:'date',value:range,expected_target_version:1}]);
  assert.deepEqual(f.data.database.rows[0].cells.date,range);
  assert.equal(f.controller.status(f.scope,'block').canUndo,true);
  await f.controller.undo(f.scope,'block');
  assert.equal(f.data.database.rows[0].cells.date,'2026-09-16');
  assert.deepEqual(f.sent.map(request=>request.cells[0].expected_target_version),[1,2]);
});

test('UI-LINKED-CELLS response loss survives a recreated controller and replays exact operation once',async()=>{
  const f=fixture();await f.controller.bind(f.scope,'block',f.data);f.lose();
  await assert.rejects(f.controller.change(f.scope,'block',f.cells()),/lost response/);
  assert.equal(f.records.size,1);assert.equal(f.data.database.rows[0].cell_versions.title,2);
  const restarted=f.recreate();await restarted.bind(f.scope,'block',f.data);
  assert.equal(f.records.size,0);assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.data.database.rows[0].cell_versions.title,2);
  await restarted.undo(f.scope,'block');assert.equal(f.data.database.rows[0].cells.title,'Before');
});

test('UI-LINKED-CELLS readonly, failed durable storage and navigation never send an unjournaled write',async()=>{
  for(const mode of ['readonly','storage','navigation']){
    const f=fixture();await f.controller.bind(f.scope,'block',f.data);
    if(mode==='readonly')f.readonly();
    if(mode==='storage')f.host.journal.put=async()=>{throw Error('storage unavailable');};
    if(mode==='navigation'){const put=f.host.journal.put;f.host.journal.put=async(...args)=>{await put(...args);f.navigate();};}
    if(mode==='navigation')assert.equal(await f.controller.change(f.scope,'block',f.cells()),false);
    else await assert.rejects(f.controller.change(f.scope,'block',f.cells()));
    assert.equal(f.sent.length,0);assert.equal(f.records.size,mode==='navigation'?1:0);
  }
});

test('UI-LINKED-CELLS stale undo does not overwrite another author and scope mismatch retains recovery data',async()=>{
  const f=fixture();await f.controller.bind(f.scope,'block',f.data);await f.controller.change(f.scope,'block',f.cells());
  f.data.database.rows[0].cells.title='Someone else';f.data.database.rows[0].cell_versions.title++;
  await f.controller.bind(f.scope,'block',f.data);await assert.rejects(f.controller.undo(f.scope,'block'),/셀이 변경/);
  assert.equal(f.sent.length,1);assert.equal(f.records.size,0);
  const g=fixture();await g.controller.bind(g.scope,'block',g.data);
  g.host.send=async()=>({document_id:'wrong-source'});
  await assert.rejects(g.controller.change(g.scope,'block',g.cells()),/응답/);assert.equal(g.records.size,1);
  await g.controller.discard(g.scope,'block');assert.equal(g.records.size,0);
});
