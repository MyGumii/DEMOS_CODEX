'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const api=require('../notion_static/notion_linked_database_templates.js');

function fixture(){
  const scope={realm:'https://test.invalid',userId:'owner',workspaceId:'ws',pageId:'instance'};
  const data={source:{database_id:'db'},
    instance_witness:{workspace_epoch:1,document_sequence:2,document_epoch:1,generation:1},
    access:{workspace_epoch:1,document_sequence:4,document_epoch:1,database_epoch:1},
    database:{effective_role:'edit',templates:[]}};
  const records=new Map(),receipts=new Map(),sent=[];
  let active=true,edit=true,lose=false,number=0,controller;
  const journal={list:async()=>[...records.values()].map(value=>structuredClone(value)),
    put:async(journalScope,envelope)=>{
      assert.equal(journalScope.page_id,'linked-database-templates:instance');
      records.set(envelope.operation_id,structuredClone(envelope));
    },remove:async(_,id)=>records.delete(id)};
  const host={journal,current:()=>active,canEdit:()=>edit,
    operationId:()=>`operation-${++number}`,templateId:()=>`template-${number}`,
    changed:async()=>{},state:()=>{},
    send:async(_,block,body)=>{
      assert.equal(block,'block');
      assert.ok(records.has(body.operation_id),'request must be stored before sending');
      sent.push(structuredClone(body));
      if(receipts.has(body.operation_id))return structuredClone(receipts.get(body.operation_id));
      const receipt={contract:'code_assist.notion.database_sharing.v1',workspace_id:'ws',
        database_id:'db',operation_id:body.operation_id,command_kind:'database.template.create',
        target_id:body.target_id,target_version:1,
        document_sequence:body.source_access.document_sequence+1,
        linked_instance:{document_id:'instance',block_id:'block'}};
      receipts.set(body.operation_id,structuredClone(receipt));
      if(lose){lose=false;throw Error('response lost');}
      return receipt;
    },
    sendUpdate:async(_,block,body)=>{
      assert.equal(block,'block');
      assert.ok(records.has(body.operation_id),'update must be stored before sending');
      sent.push(structuredClone(body));
      if(receipts.has(body.operation_id))return structuredClone(receipts.get(body.operation_id));
      const receipt={contract:'code_assist.notion.database_sharing.v1',workspace_id:'ws',
        database_id:'db',operation_id:body.operation_id,command_kind:'database.template.update',
        target_id:body.target_id,target_version:body.expected_target_version+1,
        document_sequence:body.source_access.document_sequence+1,
        linked_instance:{document_id:'instance',block_id:'block'}};
      receipts.set(body.operation_id,structuredClone(receipt));
      if(lose){lose=false;throw Error('response lost');}
      return receipt;
    }};
  controller=api.create(host);
  return {scope,data,records,sent,host,controller:()=>controller,
    restart:()=>{controller=api.create(host);return controller;},
    lose:()=>{lose=true;},leave:()=>{active=false;},readonly:()=>{edit=false;}};
}

test('UI-LINKED-TEMPLATE stores one scoped blank-template request before sending',async()=>{
  const f=fixture();await f.controller().bind(f.scope,'block',f.data);
  await f.controller().createTemplate(f.scope,'block','  회의록  ');
  assert.deepEqual(Object.keys(f.sent[0]).sort(),
    ['instance_witness','name','operation_id','source','source_access','target_id']);
  assert.equal(f.sent[0].name,'회의록');
  assert.equal(JSON.stringify(f.sent[0]).includes('document_id'),false);
  assert.equal(f.records.size,0);
});

test('UI-LINKED-TEMPLATE lost response replays the same request after restart',async()=>{
  const f=fixture();await f.controller().bind(f.scope,'block',f.data);f.lose();
  await assert.rejects(f.controller().createTemplate(f.scope,'block','회의록'),/response lost/);
  assert.equal(f.records.size,1);
  await assert.rejects(f.controller().createTemplate(f.scope,'block','다른 이름'),/보관한/);
  await f.restart().bind(f.scope,'block',f.data);
  assert.equal(f.records.size,0);
  assert.deepEqual(f.sent[0],f.sent[1]);
});

test('UI-LINKED-TEMPLATE blocks content-only, revoked and changed-source writes',async()=>{
  const f=fixture();f.data.database.effective_role='edit_content';
  assert.equal(api.editable(f.data),false);
  await f.controller().bind(f.scope,'block',f.data);
  await assert.rejects(f.controller().createTemplate(f.scope,'block','회의록'),/권한/);
  const g=fixture();await g.controller().bind(g.scope,'block',g.data);g.readonly();
  await assert.rejects(g.controller().createTemplate(g.scope,'block','회의록'),/권한/);
  const h=fixture();await h.controller().bind(h.scope,'block',h.data);h.lose();
  await assert.rejects(h.controller().createTemplate(h.scope,'block','회의록'),/response lost/);
  await h.controller().bind(h.scope,'block',{...h.data,source:{database_id:'other'}});
  await assert.rejects(h.controller().retry(h.scope,'block'),/원본이 변경/);
  assert.equal(h.sent.length,1);
});

test('UI-LINKED-TEMPLATE keeps an invalid receipt for exact retry',async()=>{
  const f=fixture();f.host.send=async()=>({contract:'code_assist.notion.database_sharing.v1',
    document_id:'private'});
  await f.controller().bind(f.scope,'block',f.data);
  await assert.rejects(f.controller().createTemplate(f.scope,'block','회의록'),/결과/);
  assert.equal(f.records.size,1);
  assert.equal(f.controller().status(f.scope,'block').pending,true);
});

test('UI-LINKED-TEMPLATE updates body and defaults with one versioned request',async()=>{
  const f=fixture();f.data.database.templates=[{id:'template-1',name:'Old',state:'active',
    version:2,body:[],default_values:{}}];
  await f.controller().bind(f.scope,'block',f.data);
  await f.controller().updateTemplate(f.scope,'block','template-1',{
    name:'New',body:[{id:'note',type:'paragraph',content:'Agenda',props:{}}],
    defaultValues:{title:'Meeting'},
  });
  assert.equal(f.sent[0].expected_target_version,2);
  assert.equal(f.sent[0].body[0].content,'Agenda');
  assert.deepEqual(f.sent[0].default_values,{title:'Meeting'});
  assert.equal(f.records.size,0);
});

test('UI-LINKED-TEMPLATE update response loss reuses the saved draft',async()=>{
  const f=fixture();f.data.database.templates=[{id:'template-1',name:'Old',state:'active',
    version:1,body:[],default_values:{}}];
  await f.controller().bind(f.scope,'block',f.data);f.lose();
  await assert.rejects(f.controller().updateTemplate(f.scope,'block','template-1',{
    name:'New',body:[],defaultValues:{}}),/response lost/);
  await f.restart().bind(f.scope,'block',f.data);
  assert.deepEqual(f.sent[0],f.sent[1]);
  assert.equal(f.records.size,0);
});

test('UI-LINKED-TEMPLATE caller base version cannot be replaced by a newer controller projection',async()=>{
  const f=fixture();f.data.database.templates=[{id:'template-1',name:'Old',state:'active',version:2,body:[],default_values:{}}];
  await f.controller().bind(f.scope,'block',f.data);
  const latest=structuredClone(f.data);latest.database.templates[0].version=3;latest.access.document_sequence=5;
  await f.controller().bind(f.scope,'block',latest);
  await assert.rejects(f.controller().updateTemplate(f.scope,'block','template-1',{
    expectedVersion:2,name:'Old renamed',body:[{id:'stale',type:'paragraph',content:'old source',props:{}}],defaultValues:{}}),/다른 곳에서 변경/);
  assert.equal(f.sent.length,0);assert.equal(f.records.size,0);
});

test('UI-LINKED-TEMPLATE clean adopted source version is fenced while retaining current access witnesses',async()=>{
  const f=fixture();f.data.database.templates=[{id:'template-1',name:'Current',state:'active',version:3,body:[],default_values:{}}];
  await f.controller().bind(f.scope,'block',f.data);
  await f.controller().updateTemplate(f.scope,'block','template-1',{expectedVersion:3,name:'Current',body:[],defaultValues:{}});
  assert.equal(f.sent[0].expected_target_version,3);assert.deepEqual(f.sent[0].source_access,f.data.access);
});

test('UI-LINKED-TEMPLATE invalid explicit base versions never become remote requests',async()=>{
  for(const expectedVersion of [0,-1,1.1,'2',null,Number.MAX_SAFE_INTEGER+1]){
    const f=fixture();f.data.database.templates=[{id:'template-1',name:'Old',state:'active',version:2,body:[],default_values:{}}];
    await f.controller().bind(f.scope,'block',f.data);
    await assert.rejects(f.controller().updateTemplate(f.scope,'block','template-1',{expectedVersion,name:'New',body:[],defaultValues:{}}),/다른 곳에서 변경/);
    assert.equal(f.sent.length,0);assert.equal(f.records.size,0);
  }
});
