'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const api=require('../notion_static/notion_synced_body_view.js');
const receipt=require('../notion_static/notion_synced_detach.js');
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const tick=async()=>{for(let n=0;n<8;n++)await Promise.resolve();};
function harness(t){
  const priorWindow=global.window,priorDocument=global.document;
  // This service harness has no real toolbar DOM; actual popup/focus behavior
  // is exercised by the isolated browser gates.
  global.window={addEventListener(){},removeEventListener(){},NotionSyncedActions:{bind:()=>({dispose(){}})}};
  global.document={hidden:false,addEventListener(){},removeEventListener(){}};
  const scope={userId:'owner',workspaceId:'ws',pageId:'physical-owner'},location={kind:'row',database_id:'db',row_id:'row'};
  const elements={};
  for(const key of ['.synced-reference-content','.synced-reference-status','[data-synced-open]','[data-synced-refresh]','[data-synced-detach]'])elements[key]={hidden:true,childElementCount:0,replaceChildren(){},textContent:''};
  const node={dataset:{syncedReference:'peer'},isConnected:true,closest:()=>null,setAttribute(){},querySelector:key=>elements[key]};
  const canvas={isConnected:true,querySelectorAll:()=>[node],contains:()=>false};
  const data={instance:{document_id:scope.pageId,block_id:'peer'},origin:{document_id:'source',block_id:'original'},locations:{instance:location},witnesses:[]};
  const h={allowed:true,editable:true,flush:true,uncertain:false,writes:0,changes:[],data:structuredClone(data),scope,location,elements,node,canvas};
  h.result={state:'committed',result:{storage:'database_body',location:structuredClone(location),target_version:2},current:{archived:false}};
  const host={scope,location,current:()=>h.allowed,editable:()=>h.editable,changed:async(...args)=>h.changes.push(args),state(){},renderText(){},bindText(){},error:error=>{h.error=error;},
    load:async()=>h.data,detachPending:()=>h.uncertain,
    detach:async(id,hooks)=>{h.hooks=hooks;if(!h.uncertain)await hooks.prepare();h.writes++;return h.wait?h.wait.promise:h.result;},
    text:{create(options){h.textOptions=options;return {flushPage:async()=>h.flush,bind:async()=>{if(h.bindWait)await h.bindWait.promise;},snapshot:()=>null,pending:()=>false,unavailable(){}};}},
    references:{create(options){h.referenceOptions=options;let mounted=[];return {mount:async(context,slots)=>{mounted=slots;h.slots=slots;for(const slot of slots)options.display(slot,{phase:'ready',data});},refresh:async()=>{for(const slot of mounted)options.display(slot,{phase:'ready',data});},dispose(){},clear(){}};}},
  };
  h.view=api.create(host);h.view.mount(canvas,[{id:'peer',type:'synced_block',props:{synced_from:data.origin}}]);
  t.after(()=>{h.view.dispose();global.window=priorWindow;global.document=priorDocument;});return h;
}

test('UI-SYNC-DB-DETACH body action resolves the exact physical instance and refreshes canonical body',async t=>{
  const h=harness(t);await tick();assert.equal(h.elements['[data-synced-detach]'].hidden,false);
  await h.view.detach('peer');assert.equal(h.writes,1);assert.equal(h.changes.length,1);
  assert.equal(h.hooks.current(),true);assert.equal(h.view.pending(),false);
  h.hooks.validateResult(h.result.result);
});
test('UI-SYNC-DB-DETACH awaited body refresh includes the new reference witness binding',async t=>{
  const h=harness(t);await tick();h.bindWait=deferred();let refreshed=false;
  const flight=h.view.refresh().then(()=>{refreshed=true;});await tick();
  assert.equal(refreshed,false);h.bindWait.resolve();await flight;assert.equal(refreshed,true);
});
test('UI-SYNC-DB-DETACH unresolved text drafts prevent detach without starting a mutation',async t=>{
  const h=harness(t);await tick();h.flush=false;await h.view.detach('peer');
  assert.equal(h.writes,0);assert.equal(h.changes.length,0);assert.equal(h.view.detachBusy(),false);
});
test('UI-SYNC-DB-DETACH changed body identity is rejected before any writer',async t=>{
  const h=harness(t);await tick();h.data.locations.instance={kind:'template',database_id:'db',template_id:'template'};
  await assert.rejects(h.view.detach('peer'),/본문 위치/);assert.equal(h.writes,0);assert.equal(h.view.pending(),false);
});
test('UI-SYNC-DB-DETACH changed original is rejected before any writer',async t=>{
  const h=harness(t);await tick();h.data.origin={document_id:'source',block_id:'different'};
  await assert.rejects(h.view.detach('peer'),/본문 위치/);assert.equal(h.writes,0);
});
test('UI-SYNC-DB-DETACH pending action blocks text and whole-body saves and duplicate clicks',async t=>{
  const h=harness(t);await tick();h.wait=deferred();const flight=h.view.detach('peer');await tick();
  assert.equal(h.view.pending(),true);assert.equal(h.textOptions.canEdit(),false);
  assert.equal(await h.view.flush(),false);assert.equal(h.elements['[data-synced-detach]'].disabled,true);
  assert.deepEqual(await h.view.detach('peer'),{state:'busy'});assert.equal(h.writes,1);
  h.wait.resolve(h.result);await flight;assert.equal(h.view.pending(),false);assert.equal(h.elements['[data-synced-detach]'].disabled,false);
});
test('UI-SYNC-DB-DETACH late result cannot refresh a replaced or closed body',async t=>{
  const h=harness(t);await tick();h.wait=deferred();const flight=h.view.detach('peer');await tick();
  h.allowed=false;h.wait.resolve(h.result);await flight;assert.equal(h.changes.length,0);
});
test('UI-SYNC-DB-DETACH ordinary drafts hide new detach actions',async t=>{
  const h=harness(t);await tick();h.editable=false;h.view.syncEditing();
  assert.equal(h.elements['[data-synced-detach]'].hidden,true);assert.deepEqual(await h.view.detach('peer'),{state:'busy'});assert.equal(h.writes,0);
});
test('UI-SYNC-DB-DETACH uncertain receipt stays recoverable after edit permission loss',async t=>{
  const h=harness(t);await tick();h.uncertain=true;h.editable=false;h.view.syncEditing();
  assert.equal(h.elements['[data-synced-detach]'].hidden,false);
  assert.equal(h.elements['[data-synced-detach]'].textContent,'해제 결과 확인 · 재시도');
  await h.view.detach('peer',{lookupOnly:true});assert.equal(h.writes,1);assert.equal(h.changes.length,1);
});
test('UI-SYNC-DB-DETACH receipt location and version are checked before confirmation',async t=>{
  const h=harness(t);await tick();await h.view.detach('peer');
  assert.throws(()=>h.hooks.validateResult({...h.result.result,location:{kind:'row',database_id:'db',row_id:'other'}}),/본문 위치/);
  assert.throws(()=>h.hooks.validateResult({...h.result.result,target_version:0}),/본문 위치/);
});
test('UI-SYNC-DB-DETACH exact journal is not confirmed when a body receipt fails validation',async()=>{
  const values=new Map(),storage={get length(){return values.size;},key:n=>[...values.keys()][n],getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),removeItem:key=>values.delete(key)};
  const scope={userId:'owner',workspaceId:'ws',pageId:'page',blockId:'peer'};
  const request={operation_id:'operation',origin:{document_id:'page',block_id:'original'},witnesses:[{document_id:'page',workspace_id:'ws',workspace_epoch:1,document_epoch:1,document_sequence:1,generation:1}]};
  const controller=receipt.create({storage:()=>storage,locks:()=>({request:async(key,options,action)=>action({})}),transport:async()=>({contract:'code_assist.notion.synced_detach_commit.v1',workspace_id:'ws',document_id:'page',block_id:'peer',operation_id:'operation',origin:request.origin,replayed:false})});
  await assert.rejects(controller.run(scope,{current:()=>true,prepare:async()=>request,validateResult:()=>{throw Error('wrong body');}}),/wrong body/);
  assert.equal(controller.list(scope)[0].confirmed,false);assert.equal(controller.pending(scope),true);
});

function focusedReference(h){
  h.canvas.contains=node=>node===h.node;
  global.document.activeElement={closest:()=>h.node};
  return [{id:'peer',type:'synced_block',props:{synced_from:structuredClone(h.data.origin)}}];
}
test('UI-SYNC-TEMPLATE-ORIGINAL text and toolbar focus retain the same reference across save state changes',async t=>{
  const h=harness(t);await tick();const blocks=focusedReference(h);
  assert.equal(h.view.holdsFocus(blocks),true);
  h.editable=false; // Read-only actions do not acquire edit permission.
  assert.equal(h.view.holdsFocus(blocks),true);
  assert.equal(h.textOptions.canEdit(),false);
});
test('UI-SYNC-TEMPLATE-ORIGINAL removed, detached or retargeted reference cannot defer the body redraw',async t=>{
  const h=harness(t);await tick();const blocks=focusedReference(h);
  assert.equal(h.view.holdsFocus([]),false);
  assert.equal(h.view.holdsFocus([{id:'peer',type:'paragraph',content:'detached',props:{}}]),false);
  blocks[0].props.synced_from.block_id='replacement';
  assert.equal(h.view.holdsFocus(blocks),false);
});
test('UI-SYNC-TEMPLATE-ORIGINAL scope loss, reader unavailability and disconnected markup cannot retain focus',async t=>{
  const h=harness(t);await tick();const blocks=focusedReference(h);
  h.allowed=false;assert.equal(h.view.holdsFocus(blocks),false);h.allowed=true;
  h.node.isConnected=false;assert.equal(h.view.holdsFocus(blocks),false);h.node.isConnected=true;
  h.referenceOptions.display(h.slots[0],{phase:'error',denied:true});
  assert.equal(h.view.holdsFocus(blocks),false);
  assert.equal(h.elements['[data-synced-open]'].hidden,true);
  global.document.activeElement={closest:()=>null};assert.equal(h.view.holdsFocus(blocks),false);
});
