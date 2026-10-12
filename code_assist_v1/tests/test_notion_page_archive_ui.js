'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const Embedded=require('../notion_static/notion_embedded_editor.js');
function recoveryPanel(ctx) {
  const document={activeElement:null};
  function element(tag) {
    return {tag,children:[],handlers:{},dataset:{},hidden:false,
      append(...nodes){this.children.push(...nodes);},appendChild(node){this.children.push(node);},
      replaceChildren(...nodes){this.children=nodes;},addEventListener(type,handler){this.handlers[type]=handler;},
      focus(){document.activeElement=this;}};
  }
  document.createElement=element;document.createTextNode=text=>({textContent:text});
  const root=element('div');ctx.document=document;ctx.$=selector=>selector==='#pageLifecycleStatus'?root:null;
  ctx.setStatus=()=>{};
  return {root,button:()=>root.children[0]?.children.find(node=>node.tag==='button')};
}
function runtime(kind='trash') {
  const state={user:{id:'owner'},navigation:{enabled:true,workspaceId:'ws',sequence:1,collaborativeSharing:true,collaborativePageArchive:true},
    activePageId:'page',pageLoadSequence:1,pageData:{page:{id:'page'},blocks:[{content:'preserve'}]},sidePeek:{pageId:'child'},dirty:false,pages:[]};
  const current={id:'page',workspace_id:'ws',can_edit:true,owner_user_id:'owner',document_sequence:4,workspace_epoch:2,document_epoch:1};
  const result={page_id:'page',page_ids:['page','child'],workspace:{id:'ws',version:3},workspace_epoch:3,refresh_page_ids:['page','child']};
  const calls=[],effects=[];let count=0,locks=0;
  const ctx={State:state,console,$:()=>null,history:{pushState:(...args)=>effects.push(['history',args])},
    embeddedEditorState:()=>({dirty:state.dirty,blocked:state.liveCollaboration?.recoveryOnly}),
    fetchWorkspacePagesSnapshot:async()=>[],applyWorkspacePagesSnapshot:(items,options)=>{state.pages=items;effects.push(['preserve-list',options]);},
    enterCollaborativeRecoveryOnly:message=>{state.liveCollaboration={...state.liveCollaboration,recoveryOnly:true,recoveryReason:message};effects.push(['recovery']);},
    startLiveCollaboration:async()=>effects.push(['reconnect']),
    hasPendingPageCopy:()=>false,
    pageCanManageLifecycle:p=>!!p?.can_edit,holdPageMoveInputs:()=>{locks++;return()=>locks--;},
    flushPageMoveEditors:async()=>true,navigationOperationId:()=>`archive-op-${++count}`,
    PageMetadataSync:{invalidate:ids=>effects.push(['invalidate',ids])},applyNavigationWorkspaceResult:w=>effects.push(['workspace',w]),
    closeSidePeek:async()=>{state.sidePeek.pageId='';effects.push(['close-peek']);},
    stopLiveCollaboration:()=>effects.push(['stop']),renderEditor:()=>effects.push(['render']),
    refreshAfterCollaborativeMove:async epoch=>effects.push(['refresh',epoch]),
    api:async(url,options={})=>{calls.push([url,options]);return options.method==='POST'?result:kind==='trash'?{page:current}:{items:[current]};}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('function pageCanArchive('),source.indexOf('function clearPageDropIndicators(')),ctx);
  vm.runInContext(source.slice(source.indexOf('const CollaborativePageLifecycleIntents'),source.indexOf('const MovePageIntents')),ctx);
  return {ctx,state,current,result,calls,effects,locks:()=>locks};
}
(async()=>{
  const r=runtime();let finish;
  r.ctx.api=async(url,options={})=>{r.calls.push([url,options]);if(options.method==='POST')return new Promise(resolve=>finish=resolve);return {page:r.current};};
  const first=r.ctx.performCollaborativePageLifecycle('trash','page');
  assert.equal(r.ctx.performCollaborativePageLifecycle('trash','page'),first,'double activation shares one operation');
  await tick();assert.equal(r.ctx.hasPendingPageLifecycle(),true);assert.equal(r.locks(),1);
  finish(r.result);await first;
  assert.equal(r.calls.filter(([,o])=>o.method==='POST').length,1);
  assert.equal(r.calls[1][1].body.expected_document_sequence,4);
  assert.equal(r.state.activePageId,'');assert.equal(r.state.sidePeek.pageId,'');
  assert.equal(r.ctx.hasPendingPageLifecycle(),false);assert.equal(r.locks(),0);
  assert.ok(r.effects.some(([name])=>name==='stop'));

  const lost=runtime();let posts=0;
  lost.ctx.api=async(url,options={})=>{lost.calls.push([url,options]);if(url.endsWith('/lifecycle-result'))return {state:'unknown'};if(options.method==='POST'){
    if(++posts===1)throw new Error('response lost');return lost.result;}return {page:lost.current};};
  await assert.rejects(lost.ctx.performCollaborativePageLifecycle('trash','page'),/response lost/);
  assert.equal(lost.ctx.hasPendingPageLifecycle(),true,'uncertain receipt protects reload');
  const original=JSON.stringify(lost.calls.at(-1)[1].body);
  await lost.ctx.performCollaborativePageLifecycle('trash','page');
  assert.equal(JSON.stringify(lost.calls.at(-1)[1].body),original);
  assert.equal(lost.calls.filter(([,o])=>o.method!=='POST').length,1,'retry needs no ordinary read of the now-archived document');

  const refresh=runtime();let attempts=0;
  refresh.ctx.refreshAfterCollaborativeMove=async()=>{if(++attempts===1)throw Error('refresh failed');};
  await assert.rejects(refresh.ctx.performCollaborativePageLifecycle('trash','page'),e=>e.lifecycleSaved===true);
  await refresh.ctx.performCollaborativePageLifecycle('trash','page');
  assert.equal(refresh.calls.filter(([,o])=>o.method==='POST').length,1,'retry after a receipt only refreshes the UI');

  const changed=runtime();let read;
  changed.ctx.api=async(url,options={})=>{changed.calls.push([url,options]);return new Promise(resolve=>read=resolve);};
  const stale=changed.ctx.performCollaborativePageLifecycle('trash','page');await tick();
  changed.state.navigation.workspaceId='other';read({page:changed.current});
  await assert.rejects(stale,/화면이 바뀌어/);assert.equal(changed.calls.length,1);assert.equal(changed.locks(),0);

  const late=runtime();let ack;
  late.ctx.api=async(url,options={})=>options.method==='POST'?new Promise(resolve=>ack=resolve):{page:late.current};
  const sent=late.ctx.performCollaborativePageLifecycle('trash','page');await tick();
  late.state.navigation.workspaceId='other';late.state.activePageId='new';late.state.pageData.blocks[0].content='new workspace draft';
  ack(late.result);await sent;
  assert.equal(late.state.activePageId,'new');assert.equal(late.state.pageData.blocks[0].content,'new workspace draft');
  assert.equal(late.effects.length,0);

  const ime=runtime();ime.ctx.holdPageMoveInputs=()=>{throw Error('composition');};
  await assert.rejects(ime.ctx.performCollaborativePageLifecycle('trash','page'),/composition/);
  assert.equal(ime.ctx.hasPendingPageLifecycle(),false);assert.equal(ime.calls.length,0);
  const unsaved=runtime();unsaved.ctx.flushPageMoveEditors=async()=>false;
  await assert.rejects(unsaved.ctx.performCollaborativePageLifecycle('trash','page'),/저장되지 않은/);
  assert.equal(unsaved.calls.length,0);assert.equal(unsaved.locks(),0);

  const conflict=runtime();let tries=0;
  conflict.ctx.api=async(url,options={})=>{conflict.calls.push([url,options]);if(options.method==='POST'&&++tries===1)throw Object.assign(Error('conflict'),{status:409});return options.method==='POST'?conflict.result:{page:conflict.current};};
  await assert.rejects(conflict.ctx.performCollaborativePageLifecycle('trash','page'),/conflict/);
  assert.equal(conflict.ctx.hasPendingPageLifecycle(),false);
  await conflict.ctx.performCollaborativePageLifecycle('trash','page');
  assert.notEqual(conflict.calls[1][1].body.operation_id,conflict.calls[3][1].body.operation_id);

  const restore=runtime('restore');await restore.ctx.performCollaborativePageLifecycle('restore','page');
  assert.equal(restore.calls[0][0],'/api/notion/workspaces/ws/trash');
  assert.equal(restore.calls[1][0],'/api/notion/workspaces/ws/trash/page/restore');
  assert.equal(restore.state.pageData.blocks[0].content,'preserve');
  const old=runtime();old.state.navigation.collaborativePageArchive=false;
  assert.equal(old.ctx.pageCanArchive(old.current),false);
  await assert.rejects(old.ctx.performCollaborativePageLifecycle('trash','page'),/사용할 수 없습니다/);assert.equal(old.calls.length,0);
  const recovery=runtime();recovery.state.liveCollaboration={generation:7,recoveryOnly:true};
  recovery.state.dirty=true;recovery.state.pageData.blocks[0].content='unsaved recovery draft';
  Object.assign(recovery.ctx,{renderPageLists:()=>{},renderWorkspacePopover:()=>{},renderBreadcrumbPopover:()=>{},renderSearchCreators:()=>{},renderHome:()=>{},fetchWorkspacePagesSnapshot:async()=>[]});
  vm.runInContext(source.slice(source.indexOf('function applyWorkspacePagesSnapshot('),source.indexOf('async function loadWorkspacePagesSnapshot(')),recovery.ctx);
  vm.runInContext(source.slice(source.indexOf('async function refreshUnavailableLivePage('),source.indexOf('async function startLiveCollaboration(')),recovery.ctx);
  await recovery.ctx.refreshUnavailableLivePage(7);
  assert.equal(recovery.state.pages.length,0);assert.equal(recovery.state.activePageId,'page');
  assert.equal(recovery.state.pageData.blocks[0].content,'unsaved recovery draft');assert.equal(recovery.state.dirty,true);
  assert.equal(recovery.ctx.pageCanArchive(recovery.current),false);
  let unavailable;recovery.ctx.fetchWorkspacePagesSnapshot=async()=>new Promise(resolve=>unavailable=resolve);
  const staleRecovery=recovery.ctx.refreshUnavailableLivePage(7);recovery.state.navigation.workspaceId='other';
  recovery.state.pages=[{id:'other-page'}];unavailable([]);await staleRecovery;
  assert.equal(recovery.state.pages[0].id,'other-page');

  const trash=runtime();const root={innerHTML:''},filter={value:'first'},requests=[];let renders=0;
  Object.assign(trash.ctx,{$:selector=>selector==='#trashResults'?root:filter,URLSearchParams,escapeHtml:String,renderTrash:()=>renders++,
    api:async()=>new Promise(resolve=>requests.push(resolve))});
  vm.runInContext(source.slice(source.indexOf('let TrashLoadSequence ='),source.indexOf('function renderTrash(')),trash.ctx);
  const earlier=trash.ctx.loadTrash();filter.value='second';const latest=trash.ctx.loadTrash();
  requests[1]({items:[{id:'second'}]});await latest;requests[0]({items:[{id:'first'}]});await earlier;
  assert.equal(trash.state.trashItems[0].id,'second');assert.equal(renders,1,'late filter response cannot overwrite the latest list');
  const oldWorkspace=trash.ctx.loadTrash();trash.state.navigation.workspaceId='other';requests[2]({items:[{id:'old-workspace'}]});await oldWorkspace;
  assert.equal(trash.state.trashItems[0].id,'second');assert.equal(renders,1);

  const fenced=runtime(), panel=recoveryPanel(fenced.ctx);let writes=0,flushes=0;
  fenced.ctx.fetchWorkspacePagesSnapshot=async()=>writes>=2?[{id:'page'}]:[];
  const originalFlush=fenced.ctx.flushPageMoveEditors;
  fenced.ctx.flushPageMoveEditors=async()=>{flushes++;return originalFlush();};
  fenced.ctx.api=async(url,options={})=>{fenced.calls.push([url,options]);
    if(url.endsWith('/lifecycle-result'))return {state:'committed',result:fenced.result,current:{archived:true,workspace:{id:'ws',version:3}}};
    if(url.endsWith('/restore')){writes++;return {...fenced.result,archived:false,restored_count:2};}
    if(options.method==='POST'){writes++;throw Error('response lost');}
    return url.endsWith('/trash')?{items:[fenced.current]}:{page:fenced.current};};
  await assert.rejects(fenced.ctx.performCollaborativePageLifecycle('trash','page'),/response lost/);
  assert.equal(panel.button().textContent,'다시 확인');assert.equal(panel.button().disabled,false);
  fenced.state.liveCollaboration={recoveryOnly:true,pendingEnvelopes:new Map([['local-change',{content:'late local input'}]])};
  fenced.state.dirty=true;fenced.state.sidePeek.dirty=true;
  const retainedBody=fenced.state.pageData, retainedPending=fenced.state.liveCollaboration.pendingEnvelopes;
  // Exercise the actual embedded flush contract that used to reject a receipt
  // retry before it could reach the server after the archive access fence.
  fenced.ctx.mainRenameEditor=()=>Embedded.createChild({save:async()=>{},snapshot:()=>retainedBody,
    state:()=>({ready:true,dirty:true,blocked:true,message:'archived editor cannot save'})});
  fenced.ctx.flushAllOpenDrafts=async()=>true;
  fenced.state.pageData.page.collaborative_editing=true;
  vm.runInContext(source.slice(source.indexOf('async function flushPageMoveEditors('),source.indexOf('async function refreshAfterCollaborativeMove(')),fenced.ctx);
  await assert.rejects(fenced.ctx.flushPageMoveEditors(),/archived editor cannot save/);
  await panel.button().handlers.click();
  assert.equal(writes,1,'receipt lookup never resends a committed archive');
  assert.equal(flushes,1,'only the initial archive needs a writable editor');
  assert.equal(fenced.state.pageData,retainedBody);assert.equal(fenced.state.liveCollaboration.pendingEnvelopes,retainedPending);
  assert.equal(fenced.state.sidePeek.dirty,true);assert.equal(fenced.state.activePageId,'page');
  assert.equal(panel.button().textContent,'페이지 복원');assert.equal(fenced.ctx.hasPendingPageLifecycle(),false);
  await panel.button().handlers.click();
  assert.equal(writes,2,'the recovery panel restores the page without saving into an archived editor');
  assert.equal(fenced.state.pageData,retainedBody);assert.equal(fenced.state.liveCollaboration.pendingEnvelopes,retainedPending);
  assert.equal(fenced.state.dirty,true);assert.equal(fenced.state.sidePeek.dirty,true);
  assert.match(fenced.state.liveCollaboration.recoveryReason,/페이지를 복원했습니다/);
  assert.equal(panel.button().textContent,'확인');await panel.button().handlers.click();assert.equal(panel.root.hidden,true);

  const superseded=runtime();const laterPanel=recoveryPanel(superseded.ctx);let originalWrites=0;
  superseded.ctx.api=async(url,options={})=>{
    if(url.endsWith('/lifecycle-result'))return {state:'committed',result:superseded.result,current:{archived:false,workspace:{id:'ws',version:8}}};
    if(options.method==='POST'){originalWrites++;throw Error('lost');}return {page:superseded.current};};
  await assert.rejects(superseded.ctx.performCollaborativePageLifecycle('trash','page'),/lost/);
  await superseded.ctx.performCollaborativePageLifecycle('trash','page');
  assert.equal(originalWrites,1);assert.equal(superseded.state.activePageId,'page');
  assert.equal(superseded.state.liveCollaboration?.recoveryOnly,undefined,'an old receipt cannot re-archive a restored page in the UI');
  assert.equal(laterPanel.button().textContent,'확인');
  superseded.state.navigation.workspaceId='other';superseded.ctx.renderPageLifecycleStatus();assert.equal(laterPanel.root.hidden,true);

  const unknown=runtime();let unknownWrites=0;
  unknown.ctx.api=async(url,options={})=>{
    if(url.endsWith('/lifecycle-result'))return {state:'unknown'};
    if(options.method==='POST'){unknownWrites++;throw Error('lost');}return {page:unknown.current};};
  await assert.rejects(unknown.ctx.performCollaborativePageLifecycle('trash','page'),/lost/);
  unknown.ctx.flushPageMoveEditors=async()=>{throw Error('blocked editor');};
  await assert.rejects(unknown.ctx.performCollaborativePageLifecycle('trash','page'),/blocked editor/);
  assert.equal(unknownWrites,1,'unknown is not proof that retrying a blocked editor is safe');
  assert.equal(unknown.ctx.hasPendingPageLifecycle(),true);

  const overlay=runtime('restore'), restoreButton={disabled:false,isConnected:true};let opened=0,closed=0;
  overlay.state.dirty=true;overlay.state.liveCollaboration={recoveryOnly:true};
  overlay.ctx.fetchWorkspacePagesSnapshot=async()=>[{id:'page'}];
  await overlay.ctx.performCollaborativePageLifecycle('restore','page',{preserveEditors:true});
  Object.assign(overlay.ctx,{CSS:{escape:String},document:{querySelector:()=>restoreButton},
    restorePageSafely:id=>overlay.ctx.performCollaborativePageLifecycle('restore',id),loadTrash:async()=>true,
    closeTrashOverlay:()=>closed++,openPage:async()=>opened++,focusCurrentBreadcrumbAfterLifecycle(){},setStatus(){}});
  vm.runInContext(source.slice(source.indexOf('async function restoreTrashPage('),source.indexOf('async function deleteTrashPage(')),overlay.ctx);
  await overlay.ctx.restoreTrashPage('page');
  assert.equal(opened,0,'trash overlay restoration cannot reopen and replace a preserved recovery editor');
  assert.equal(closed,1);assert.equal(restoreButton.disabled,false);assert.equal(overlay.state.dirty,true);
  assert.equal(overlay.state.pageData.blocks[0].content,'preserve');
  assert.equal(overlay.calls.filter(([,options])=>options.method==='POST').length,1);
  console.log('UI-PAGE-ARCHIVE: 16 runtime scenario groups passed, including receipt lookup after live access loss, recovery-panel/overlay restore, unsaved main/peek preservation, superseded receipts and unknown outcomes');
})().catch(error=>{console.error(error);process.exitCode=1;});
