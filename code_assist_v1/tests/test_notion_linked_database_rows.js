'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const api=require('../notion_static/notion_linked_database_rows.js');

function fixture(){
  const scope={realm:'https://test.invalid',userId:'owner',workspaceId:'ws',pageId:'instance'};
  const data={source:{database_id:'db'},
    instance_witness:{workspace_epoch:1,document_sequence:2,document_epoch:1,generation:1},
    access:{workspace_epoch:1,document_sequence:4,document_epoch:1,database_epoch:1},
    database:{effective_role:'edit_content',properties:[{id:'title',type:'title'}]}};
  const records=new Map(),receipts=new Map(),sent=[],states=[];
  let active=true,edit=true,lose=false,number=0,controller;
  const journal={list:async()=>[...records.values()].map(value=>structuredClone(value)),
    put:async(journalScope,envelope)=>{
      assert.equal(journalScope.page_id,'linked-database-rows:instance');
      records.set(envelope.operation_id,structuredClone(envelope));
    },remove:async(_,id)=>records.delete(id)};
  const host={journal,current:()=>active,canEdit:()=>edit,
    operationId:()=>`operation-${++number}`,
    state:(_,__,status)=>states.push(structuredClone(status)),changed:async()=>{},
    send:async(_,block,body)=>{
      assert.equal(block,'block');
      assert.ok(records.has(body.operation_id),'request must be stored before sending');
      sent.push(structuredClone(body));
      if(receipts.has(body.operation_id))return structuredClone(receipts.get(body.operation_id));
      const receipt={contract:'code_assist.notion.database_sharing.v1',workspace_id:'ws',
        database_id:'db',operation_id:body.operation_id,command_kind:'database.row.create',
        document_sequence:body.source_access.document_sequence+1,
        linked_instance:{document_id:'instance',block_id:'block'},row_id:'row-1',
        ...(body.template_id?{template_id:body.template_id}:{})};
      receipts.set(body.operation_id,structuredClone(receipt));
      if(lose){lose=false;throw Error('response lost');}
      return receipt;
    }};
  controller=api.create(host);
  return {scope,data,records,receipts,sent,states,host,
    controller:()=>controller,restart:()=>{controller=api.create(host);return controller;},
    lose:()=>{lose=true;},leave:()=>{active=false;},readonly:()=>{edit=false;}};
}

test('UI-LINKED-ROWS stores the scoped request before source row creation',async()=>{
  const f=fixture();await f.controller().bind(f.scope,'block',f.data);
  await f.controller().createRow(f.scope,'block');
  assert.deepEqual(Object.keys(f.sent[0]).sort(),
    ['instance_witness','operation_id','source','source_access','title']);
  assert.equal(JSON.stringify(f.sent[0]).includes('document_id'),false);
  assert.equal(f.sent[0].title,'');
  assert.equal(f.records.size,0,JSON.stringify(f.states.at(-1)));
});

test('UI-LINKED-ROWS lost response reuses one operation after restart',async()=>{
  const f=fixture();await f.controller().bind(f.scope,'block',f.data);f.lose();
  await assert.rejects(f.controller().createRow(f.scope,'block'),/response lost/);
  assert.equal(f.records.size,1);
  await assert.rejects(f.controller().createRow(f.scope,'block'),/보관한/);
  await f.restart().bind(f.scope,'block',f.data);
  assert.equal(f.records.size,0,JSON.stringify(f.states.at(-1)));
  assert.deepEqual(f.sent[0],f.sent[1]);
});

test('UI-LINKED-ROWS readonly and changed source cannot send',async()=>{
  const f=fixture();await f.controller().bind(f.scope,'block',f.data);f.readonly();
  await assert.rejects(f.controller().createRow(f.scope,'block'),/권한/);
  assert.equal(f.sent.length,0);
  const g=fixture();await g.controller().bind(g.scope,'block',g.data);g.lose();
  await assert.rejects(g.controller().createRow(g.scope,'block'),/response lost/);
  await g.controller().bind(g.scope,'block',{...g.data,source:{database_id:'different'}});
  await assert.rejects(g.controller().retry(g.scope,'block'),/원본이 변경/);
  assert.equal(g.sent.length,1);
});

test('UI-LINKED-ROWS invalid receipt leaves durable request for retry',async()=>{
  const f=fixture();f.host.send=async(_,__,body)=>{
    f.sent.push(structuredClone(body));
    return {contract:'code_assist.notion.database_sharing.v1',document_id:'private'};
  };
  await f.controller().bind(f.scope,'block',f.data);
  await assert.rejects(f.controller().createRow(f.scope,'block'),/응답/);
  assert.equal(f.records.size,1);
  assert.equal(f.controller().status(f.scope,'block').pending,true);
});

test('UI-LINKED-ROWS offers blank and accessible named templates',async()=>{
  const blank=fixture();await blank.controller().bind(blank.scope,'block',blank.data);
  await blank.controller().createRow(blank.scope,'block',{blank:true});
  assert.equal(blank.sent[0].use_default_template,false);
  assert.equal(Object.hasOwn(blank.sent[0],'template_id'),false);
  const named=fixture();named.data.database.templates=[{id:'template-1',state:'active'}];
  await named.controller().bind(named.scope,'block',named.data);
  await named.controller().createRow(named.scope,'block',{templateId:'template-1'});
  assert.equal(named.sent[0].template_id,'template-1');
  assert.equal(Object.hasOwn(named.sent[0],'use_default_template'),false);
});

test('UI-LINKED-ROWS inaccessible default requires explicit blank choice',async()=>{
  const f=fixture();f.data.database.default_template={template_id:'template-1'};
  f.data.database.templates=[{id:'template-1',state:'active',body_unavailable:true}];
  await f.controller().bind(f.scope,'block',f.data);
  await assert.rejects(f.controller().createRow(f.scope,'block'),/비어 있음/);
  await assert.rejects(f.controller().createRow(f.scope,'block',{templateId:'template-1'}),/접근/);
  assert.equal(f.sent.length,0);
  await f.controller().createRow(f.scope,'block',{blank:true});
  assert.equal(f.sent[0].use_default_template,false);
});
