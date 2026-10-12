'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const body=require('../notion_static/notion_database_item_body.js');
const clone=structuredClone;
const blocks=text=>[{id:'block-a',type:'paragraph',content:text,props:{rich_text_html:text}}];
function fixture(){
  const scope={realm:'https://fixture.invalid',userId:'owner',workspaceId:'ws',pageId:'page'};
  let data={source:{database_id:'db'},database:{id:'db',effective_role:'edit_content',capabilities:{row_content:true},rows:[{id:'row',version:1,body:{blocks:[]}}]},
    witnesses:[{document_id:'page',document_sequence:4,workspace_epoch:2,document_epoch:3}]};
  const records=new Map(),sent=[],receipts=new Map(),changes=[],states=[];let seq=0,live=true,allowed=true,lose=false,storage=false,corrupt,sendGate,journalGate;
  const key=s=>JSON.stringify(s),journal={list:async s=>[...(records.get(key(s))||new Map()).values()].map(e=>clone(e)),
    put:async(s,e)=>{if(journalGate)await journalGate;if(storage)throw Error('storage unavailable');if(!records.has(key(s)))records.set(key(s),new Map());records.get(key(s)).set(e.operation_id,clone(e));},
    remove:async(s,id)=>{if(storage)throw Error('storage unavailable');records.get(key(s))?.delete(id);}};
  let c;
  const host={journal,current:()=>live,canEdit:()=>allowed,operationId:()=>`operation-${++seq}`,state:(...a)=>states.push(a),
    changed:async(s,b,r)=>{changes.push(r);c.update(scope,'db','row',data);},send:async(s,request)=>{
      const saved=[...records.values()].flatMap(m=>[...m.values()]).find(e=>e.operation_id===request.operation_id);
      assert.deepEqual(saved.request,request,'HTTP must follow durable exact request');sent.push(clone(request));if(sendGate)await sendGate;
      if(receipts.has(request.operation_id)){assert.deepEqual(request,receipts.get(request.operation_id).request);return clone(receipts.get(request.operation_id).result);}
      assert.equal(request.expected_target_version,data.database.rows[0].version);assert.equal(request.expected_document_sequence,data.witnesses[0].document_sequence);
      const row=data.database.rows[0];row.body={blocks:body.normalize(request.payload.blocks)};row.version++;data.witnesses[0].document_sequence++;
      const result={contract:'code_assist.notion.collaborative_database_surface_command.v1',status:'database_command_committed',
        operation_id:request.operation_id,workspace_id:scope.workspaceId,document_id:scope.pageId,database_id:'db',target_id:'row',
        command_kind:request.command_kind,target_version:row.version,document_sequence:data.witnesses[0].document_sequence,body:clone(row.body)};
      receipts.set(request.operation_id,{request:clone(request),result:clone(result)});
      if(lose){lose=false;throw Error('response lost');}if(corrupt){corrupt(result);corrupt=null;}return result;
    }};
  c=body.create(host);
  return {scope,records,sent,receipts,changes,states,data,get c(){return c;},bind:()=>c.bind(scope,'db-block',data,'row'),
    edit:text=>c.draft(scope,'db','row',blocks(text)),save:()=>c.flush(scope,'db','row'),view:()=>c.snapshot(scope,'db','row'),
    restart:()=>{c=body.create(host);},storage:v=>storage=v,allowed:v=>allowed=v,live:v=>live=v,lose:()=>lose=true,corrupt:f=>corrupt=f,
    gateSend:p=>sendGate=p,gateJournal:p=>journalGate=p,
    update:()=>c.update(scope,'db','row',data),remote:text=>{data.database.rows[0].body={blocks:body.normalize(blocks(text))};data.database.rows[0].version++;data.witnesses[0].document_sequence++;c.update(scope,'db','row',data);}};
}
const until=async check=>{for(let i=0;i<1000&&!check();i++)await new Promise(r=>setImmediate(r));assert.ok(check(),'expected async transition');};

test('UI-ITEM-BODY durable body save advances once and does not change source fields',async()=>{
  const f=fixture();await f.bind();f.edit('본문');assert.equal(await f.save(),true);
  assert.equal(f.data.database.rows[0].body.blocks[0].content,'본문');assert.equal(f.data.database.rows[0].version,2);
  assert.equal(f.view().dirty,false);assert.equal(f.c.pending(f.scope,'db','row'),false);assert.equal(f.sent.length,1);
  assert.equal(await f.save(),true);assert.equal(f.sent.length,1);assert.equal([...f.records.values()][0].size,0);
});
test('UI-ITEM-BODY response loss reloads the draft and replays only the identical request',async()=>{
  const f=fixture();await f.bind();f.edit('보존');f.lose();assert.equal(await f.save(),false);assert.equal(f.view().pending,true);
  f.restart();await f.bind();assert.equal(f.view().blocks[0].content,'보존');assert.equal(f.edit('must stay frozen'),false);
  assert.equal(await f.save(),true);assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.receipts.size,1);assert.equal(f.view().dirty,false);
});
test('UI-ITEM-BODY newer typing during an in-flight save is persisted as the next version',async()=>{
  const f=fixture();await f.bind();let release;f.gateSend(new Promise(r=>release=r));f.edit('first');const save=f.save();
  await until(()=>f.sent.length===1);assert.equal(f.edit('second'),true);release();assert.equal(await save,true);
  assert.equal(f.sent.length,2);assert.equal(f.sent[1].expected_target_version,2);assert.equal(f.sent[1].expected_document_sequence,5);
  assert.equal(f.data.database.rows[0].body.blocks[0].content,'second');assert.equal(f.view().dirty,false);
});
test('UI-ITEM-BODY a recovered draft keeps its original remote version and never rebases silently',async()=>{
  const f=fixture();await f.bind();f.edit('local');await until(()=>[...f.records.values()][0]?.has('draft'));f.remote('remote');f.restart();await f.bind();
  assert.equal(f.view().conflict,true);assert.equal(f.view().blocks[0].content,'local');assert.equal(await f.save(),false);assert.equal(f.sent.length,0);
  assert.equal(await f.c.discard(f.scope,'db','row'),true);assert.equal(f.view().blocks[0].content,'remote');assert.equal(f.view().dirty,false);
});
test('UI-ITEM-BODY storage failure sends nothing and explicit retry recovers without retyping',async()=>{
  const f=fixture();await f.bind();f.storage(true);f.edit('local');assert.equal(await f.save(),false);assert.equal(f.sent.length,0);assert.equal(f.view().dirty,true);
  f.storage(false);assert.equal(await f.save(),true);assert.equal(f.sent.length,1);assert.equal(f.data.database.rows[0].body.blocks[0].content,'local');
});
test('UI-ITEM-BODY current scope and permission are checked again after journal persistence',async()=>{
  for(const invalidate of [f=>f.live(false),f=>f.allowed(false)]){
    const f=fixture();await f.bind();let release;f.gateJournal(new Promise(r=>release=r));f.edit('kept');const saving=f.save();
    await new Promise(r=>setImmediate(r));invalidate(f);release();assert.equal(await saving,false);assert.equal(f.sent.length,0);assert.equal(f.view().dirty,true);
  }
});
test('UI-ITEM-BODY access loss parks a local draft without sending and can retry local storage',async()=>{
  const f=fixture();await f.bind();f.edit('kept after revoke');f.live(false);
  assert.equal(await f.c.park(f.scope,'db','row'),true);
  assert.equal(f.sent.length,0);
  assert.equal([...f.records.values()][0].get('draft').blocks[0].content,'kept after revoke');
  const g=fixture();await g.bind();g.storage(true);g.edit('copy me');g.live(false);
  assert.equal(await g.c.park(g.scope,'db','row'),false);
  assert.equal(g.sent.length,0);
  g.storage(false);
  assert.equal(await g.c.park(g.scope,'db','row'),true);
  assert.equal([...g.records.values()][0].get('draft').blocks[0].content,'copy me');
});
test('UI-ITEM-BODY composition and readonly capability never send or discard text implicitly',async()=>{
  const f=fixture();await f.bind();f.edit('조합');f.c.composing(f.scope,'db','row',true);assert.equal(await f.save(),false);assert.equal(f.sent.length,0);
  f.c.composing(f.scope,'db','row',false);assert.equal(await f.save(),true);
  for(const change of [d=>d.database.effective_role='view',d=>d.database.capabilities.row_content=false,d=>d.database.rows=[]]){
    const g=fixture();change(g.data);await g.bind();assert.equal(g.view().editable,false);assert.equal(g.edit('denied'),false);assert.equal(g.sent.length,0);
  }
});
test('UI-ITEM-BODY a late receipt does not refresh another active page',async()=>{
  const f=fixture();await f.bind();let release;f.gateSend(new Promise(r=>release=r));f.edit('sent');const save=f.save();await until(()=>f.sent.length===1);
  f.live(false);release();assert.equal(await save,true);assert.equal(f.changes.length,0);assert.equal(f.receipts.size,1);
});
test('UI-ITEM-BODY mismatched receipts retain exact recovery evidence',async()=>{
  for(const change of [r=>r.operation_id='other',r=>r.document_id='other',r=>r.workspace_id='other',r=>r.database_id='other',r=>r.target_id='other',r=>r.target_version++,r=>r.document_sequence++,r=>r.body.blocks=[],r=>r.contract='other',r=>r.linked_instance={}]){
    const f=fixture();await f.bind();f.edit('kept');f.corrupt(change);assert.equal(await f.save(),false);assert.equal(f.view().pending,true);
    assert.equal(await f.save(),true);assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.receipts.size,1);
  }
});
test('UI-ITEM-BODY journal scope separates rows, documents, workspaces and actors within the identifier bound',async()=>{
  const f=fixture(),a=await body.journalScope(f.scope,'db','row');assert.ok(a.page_id.length<160);assert.equal(a.realm,f.scope.realm);
  assert.notEqual(a.page_id,(await body.journalScope(f.scope,'db','other')).page_id);
  assert.notEqual(a.page_id,(await body.journalScope({...f.scope,pageId:'other'},'db','row')).page_id);
  assert.notDeepEqual(a,await body.journalScope({...f.scope,userId:'other'},'db','row'));
  assert.notDeepEqual(a,await body.journalScope({...f.scope,workspaceId:'other'},'db','row'));
});
test('UI-ITEM-BODY malformed or mismatched recovery entries cannot become a write',async()=>{
  const f=fixture(),scope=await body.journalScope(f.scope,'db','row');f.records.set(JSON.stringify(scope),new Map([['draft',{kind:'owned.body.draft.v1',operation_id:'draft',database_id:'other',row_id:'row',base_version:1,blocks:[]}]]));
  await f.bind();assert.equal(f.view().ready,false);assert.match(f.view().error,/일치하지/);assert.equal(await f.save(),false);assert.equal(f.sent.length,0);
  assert.equal(await f.c.discard(f.scope,'db','row'),true);assert.equal(f.view().ready,true);assert.equal(f.edit('fresh draft'),true);assert.equal(await f.save(),true);
});
test('UI-ITEM-BODY failed local discard keeps the recoverable draft and can be retried',async()=>{
  const f=fixture();await f.bind();f.edit('recoverable');await until(()=>[...f.records.values()][0]?.has('draft'));
  f.storage(true);assert.equal(await f.c.discard(f.scope,'db','row'),false);assert.equal(f.view().blocks[0].content,'recoverable');assert.equal(f.sent.length,0);
  f.storage(false);assert.equal(await f.c.discard(f.scope,'db','row'),true);assert.deepEqual(f.view().blocks,[]);assert.equal(f.view().editable,true);
});
