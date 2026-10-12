'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),api=require('../notion_static/notion_database_item_appearance.js');
const clone=structuredClone;
function fixture(){
  const scope={realm:'https://fixture.invalid',userId:'owner',workspaceId:'ws',pageId:'page'},records=new Map(),sent=[],receipts=new Map(),changes=[];
  const data={source:{database_id:'db'},database:{id:'db',effective_role:'edit_content',capabilities:{row_appearance:true},rows:[{id:'row',version:1,cells:{title:'unchanged'},body:{blocks:[]}}]},witnesses:[{document_id:'page',document_sequence:4,workspace_epoch:2,document_epoch:3}]};
  let c,sequence=0,live=true,allowed=true,lose=false,storage=false,corrupt,sendGate,journalGate;
  const key=s=>JSON.stringify(s),journal={list:async s=>[...(records.get(key(s))||new Map()).values()].map(e=>clone(e)),
    put:async(s,e)=>{if(journalGate)await journalGate;if(storage)throw Error('storage failure');if(!records.has(key(s)))records.set(key(s),new Map());records.get(key(s)).set(e.operation_id,clone(e));},
    remove:async(s,id)=>{if(storage)throw Error('storage failure');records.get(key(s))?.delete(id);}};
  const host={journal,current:()=>live,canEdit:()=>allowed,operationId:()=>`appearance-${++sequence}`,
    changed:async(s,b,r)=>{changes.push(r);c.update(scope,'db','row',data);},send:async(s,request)=>{
      const persisted=[...records.values()].flatMap(m=>[...m.values()]).find(e=>e.operation_id===request.operation_id);assert.deepEqual(persisted.request,request);
      sent.push(clone(request));if(sendGate)await sendGate;
      if(receipts.has(request.operation_id)){assert.deepEqual(receipts.get(request.operation_id).request,request);return clone(receipts.get(request.operation_id).result);}
      const row=data.database.rows[0];assert.equal(request.expected_target_version,row.version);assert.equal(request.expected_document_sequence,data.witnesses[0].document_sequence);
      row.appearance=api.apply(row.appearance,request.payload);row.version++;data.witnesses[0].document_sequence++;
      const result={contract:'code_assist.notion.collaborative_database_surface_command.v1',status:'database_command_committed',operation_id:request.operation_id,
        workspace_id:'ws',document_id:'page',database_id:'db',target_id:'row',command_kind:request.command_kind,target_version:row.version,
        document_sequence:data.witnesses[0].document_sequence,appearance:clone(row.appearance)};
      receipts.set(request.operation_id,{request:clone(request),result:clone(result)});if(lose){lose=false;throw Error('response lost');}if(corrupt){corrupt(result);corrupt=null;}return result;
    }};
  c=api.create(host);
  return {scope,data,records,sent,receipts,changes,get c(){return c;},bind:()=>c.bind(scope,'block',data,'row'),set:v=>c.set(scope,'db','row',v),flush:()=>c.flush(scope,'db','row'),discard:()=>c.discard(scope,'db','row'),view:()=>c.snapshot(scope,'db','row'),
    restart:()=>c=api.create(host),lose:()=>lose=true,storage:v=>storage=v,allowed:v=>allowed=v,live:v=>live=v,corrupt:f=>corrupt=f,gate:p=>sendGate=p,journalGate:p=>journalGate=p};
}
const until=async fn=>{for(let i=0;i<1000&&!fn();i++)await new Promise(r=>setImmediate(r));assert.ok(fn());};
test('UI-ITEM-APPEARANCE partial icon/cover/crop writes preserve body and cells',async()=>{
  const f=fixture();await f.bind();assert.equal(await f.set({icon:'⭐',cover:'https://fixture.invalid/a.png',cover_position:.2}),true);
  assert.equal(await f.set({icon:'🚀'}),true);assert.equal(f.view().appearance.cover_position,.2);assert.equal(await f.set({cover:''}),true);
  assert.deepEqual(f.view().appearance,{icon:'🚀',cover:'',cover_position:.5});assert.deepEqual(f.data.database.rows[0].cells,{title:'unchanged'});assert.deepEqual(f.data.database.rows[0].body,{blocks:[]});
  assert.equal(await f.set({icon:'🚀'}),true);assert.equal(f.sent.length,3);assert.equal(f.c.pending(f.scope,'db','row'),false);
});
test('UI-ITEM-APPEARANCE lost result reloads exact immutable request and never sends a replacement',async()=>{
  const f=fixture();await f.bind();f.lose();assert.equal(await f.set({icon:'⭐'}),false);f.restart();await f.bind();assert.equal(f.view().appearance.icon,'⭐');assert.equal(f.view().pending,true);
  assert.equal(await f.set({icon:'🚀'}),false);assert.equal(await f.flush(),true);assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.receipts.size,1);assert.equal(f.view().pending,false);
});
test('UI-ITEM-APPEARANCE storage failure sends nothing and retains the chosen value for retry',async()=>{
  const f=fixture();await f.bind();f.storage(true);assert.equal(await f.set({icon:'⭐'}),false);assert.equal(f.sent.length,0);assert.equal(f.view().appearance.icon,'⭐');
  f.storage(false);assert.equal(await f.flush(),true);assert.equal(f.sent.length,1);
});
test('UI-ITEM-APPEARANCE permission/scope is checked again after durable persistence',async()=>{
  for(const change of [f=>f.allowed(false),f=>f.live(false)]){const f=fixture();await f.bind();let release;f.journalGate(new Promise(r=>release=r));const saving=f.set({icon:'⭐'});await new Promise(r=>setImmediate(r));change(f);release();assert.equal(await saving,false);assert.equal(f.sent.length,0);assert.equal(f.view().pending,true);}
});
test('UI-ITEM-APPEARANCE readonly capability, removed row and duplicate clicks cannot write',async()=>{
  for(const change of [d=>d.database.effective_role='view',d=>d.database.capabilities.row_appearance=false,d=>d.database.rows=[]]){const f=fixture();change(f.data);await f.bind();assert.equal(await f.set({icon:'⭐'}),false);assert.equal(f.sent.length,0);}
  const f=fixture();await f.bind();let release;f.gate(new Promise(r=>release=r));const saving=f.set({icon:'⭐'});await until(()=>f.sent.length);assert.equal(await f.set({icon:'🚀'}),false);release();assert.equal(await saving,true);assert.equal(f.sent.length,1);
});
test('UI-ITEM-APPEARANCE receipt identity, versions, shape and values are strictly matched',async()=>{
  for(const change of [r=>r.operation_id='other',r=>r.workspace_id='other',r=>r.document_id='other',r=>r.database_id='other',r=>r.target_id='other',r=>r.target_version++,r=>r.document_sequence++,r=>r.appearance.icon='🚀',r=>delete r.appearance.cover,r=>r.appearance.icon=' ⭐ ',r=>r.contract='other',r=>r.linked_instance={}]){
    const f=fixture();await f.bind();f.corrupt(change);assert.equal(await f.set({icon:'⭐'}),false);assert.equal(f.view().pending,true);assert.equal(await f.flush(),true);assert.equal(f.receipts.size,1);assert.deepEqual(f.sent[0],f.sent[1]);
  }
});
test('UI-ITEM-APPEARANCE late completion never refreshes a different active page',async()=>{
  const f=fixture();await f.bind();let release;f.gate(new Promise(r=>release=r));const saving=f.set({icon:'⭐'});await until(()=>f.sent.length);f.live(false);release();assert.equal(await saving,true);assert.equal(f.changes.length,0);
});
test('UI-ITEM-APPEARANCE malformed recovered source requires explicit local discard',async()=>{
  const f=fixture();await f.bind();f.lose();await f.set({icon:'⭐'});const record=[...f.records.values()][0];record.values().next().value.identity='different-source';f.restart();await f.bind();
  assert.equal(f.view().ready,false);assert.equal(await f.flush(),false);assert.equal(f.sent.length,1);assert.equal(await f.discard(),true);assert.equal(f.view().ready,true);assert.equal(f.view().appearance.icon,'⭐');
});
test('UI-ITEM-APPEARANCE failed discard keeps recovery and in-flight discard is refused',async()=>{
  const f=fixture();await f.bind();f.lose();await f.set({icon:'⭐'});f.storage(true);assert.equal(await f.discard(),false);assert.equal(f.view().pending,true);f.storage(false);assert.equal(await f.discard(),true);assert.equal(f.view().pending,false);
  let release;f.gate(new Promise(r=>release=r));const saving=f.set({icon:'🚀'});await until(()=>f.sent.length===2);assert.equal(await f.discard(),false);release();assert.equal(await saving,true);
});
test('UI-ITEM-APPEARANCE only recognized own fields and finite crop values are accepted',()=>{
  for(const value of [{constructor:'x'},{toString:'x'},JSON.parse('{"__proto__":"x"}'),{},[],{cover_position:NaN},{cover_position:true},{cover_position:1.01},{cover_position:-.01},{icon:12}])assert.throws(()=>api.updates(value));
});
test('UI-ITEM-APPEARANCE projection shows the saved icon without creating a database cell',()=>{
  const bridge=require('../notion_static/notion_database_bridge.js');
  const data={id:'db',title:'Tasks',version:1,properties:[{id:'title',name:'Name',type:'title',version:1,config:{}}],views:[{id:'view',name:'Table',type:'table',state:'active',version:1,config:{}}],templates:[],rows:[{id:'row',version:2,cells:{title:'Task'},cell_versions:{title:1},appearance:{icon:'⭐',cover:'',cover_position:.5}}]};
  const block=bridge.canonicalToBlock(data);assert.deepEqual(block.props.rows[0]._canonical_appearance,data.rows[0].appearance);
  assert.deepEqual(bridge.diffDatabase(data,bridge.blockToDesired(block)),[]);block.props.rows[0]._canonical_appearance.icon='🚀';
  assert.equal(data.rows[0].appearance.icon,'⭐');assert.deepEqual(bridge.diffDatabase(data,bridge.blockToDesired(block)),[]);
});
test('UI-ITEM-APPEARANCE journal identity separates actor/workspace/document/row and body namespace',async()=>{
  const f=fixture(),value=await api.journalScope(f.scope,'db','row');assert.ok(value.page_id.length<160);assert.ok(value.page_id.startsWith('item-appearance-'));
  assert.notDeepEqual(value,await api.journalScope({...f.scope,userId:'other'},'db','row'));assert.notDeepEqual(value,await api.journalScope({...f.scope,workspaceId:'other'},'db','row'));
  assert.notDeepEqual(value,await api.journalScope({...f.scope,pageId:'other'},'db','row'));assert.notDeepEqual(value,await api.journalScope(f.scope,'db','other'));
});
