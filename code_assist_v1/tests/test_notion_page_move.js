"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../notion_static/notion.js'),'utf8');
function context() {
  const page={page:{id:'page-a',title:'Current collaborative title',collaborative_editing:true},blocks:[{id:'live',type:'paragraph',content:'Unsynced body'}]};
  const state={user:{id:'owner'},navigation:{collaborativeSharing:true,workspaceId:'ws-a',sequence:1},activePageId:'page-a',pageData:page,dirty:false,editRevision:8,
    sidePeek:{pageId:'page-a',data:page,dirty:false,editRevision:2}};
  const calls=[],ctx={State:state,console,JSON,Map,Promise,Error,crypto:require('node:crypto').webcrypto,
    api:async(url,options)=>{calls.push({url,options});return {page:{id:'page-a',title:'Stale ordinary title'},blocks:[]};},
    pageMetadataScope:()=>({subject_id:state.user.id,workspace_id:state.navigation.workspaceId}),
    window:{NotionMetadataSync:{scopeKey:scope=>JSON.stringify(scope)}},
    refreshVisiblePageMetadata:async(ids,key)=>{calls.push({metadata:ids,key});},
    flushAllOpenDrafts:async()=>true,refreshOpenPagesAfterMutation:async()=>{calls.push({refresh:true});},loadBacklinks:async()=>{calls.push({backlinks:true});},
    mainRenameEditor:()=>({flush:async()=>{}}),PageMetadataSync:null,
    EditorCore:{deepClone:value=>JSON.parse(JSON.stringify(value))},normalizedPageData:value=>value,
    applySaveMetadata(){},renderEditor(){calls.push({render:true});},resetEditorHistory(){},
    renderSidePeek(){calls.push({peek:true});},sidePeekFocusSnapshot:()=>null,restoreSidePeekFocus(){},serverRevisionFrom:()=>null,showSaveConflict(){}};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf('async function refreshOpenedPageAfterMutation('),source.indexOf('async function refreshOpenPagesAfterMutation(')),ctx);
  const moveStart=source.indexOf('const MovePageIntents = new Map()');
  vm.runInContext(source.slice(moveStart>=0?moveStart:source.indexOf('async function movePageSafely('),source.indexOf('function markDirty()')),ctx);
  return {ctx,state,calls,page};
}
(async()=>{
  const f=context();
  const result=await f.ctx.refreshOpenedPageAfterMutation('page-a',{page:{id:'page-a',title:'Wrong storage'},blocks:[]});
  assert.equal(f.state.pageData,f.page,'current CRDT document must not be replaced by an ordinary snapshot');
  assert.equal(f.state.sidePeek.data,f.page,'peek CRDT document must retain its live body');
  assert.equal(f.state.pageData.page.title,'Current collaborative title');
  assert.equal(f.state.pageData.blocks[0].content,'Unsynced body');
  assert.equal(f.calls.some(c=>c.url),false,'collaborative refresh must not read ordinary storage');
  assert.deepEqual(Array.from(f.calls[0].metadata),['page-a']);
  assert.equal(result.metadataRefreshed,true);
  const unsupported=context();
  await assert.rejects(unsupported.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),/협업 페이지 이동/);
  assert.equal(unsupported.calls.some(c=>c.options?.method==='POST'),false,'missing collaborative move capability never falls back to a legacy write');
  const retry=context();let posts=0;
  retry.ctx.api=async(url,options)=>{
    retry.calls.push({url,options});
    if(!options)return {capabilities:{collaborative_page_move:true},page:{id:'page-a',workspace_id:'ws-a',document_sequence:posts?99:60,workspace_epoch:1,document_epoch:1}};
    if(++posts===1)throw new Error('response lost');
    return {status:'moved'};
  };
  await assert.rejects(retry.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),/response lost/);
  await retry.ctx.movePageSafely('page-a',{parent_id:'parent-a'});
  const writes=retry.calls.filter(c=>c.options);
  assert.equal(writes.length,2);
  assert.ok(writes.every(c=>c.url==='/api/notion/workspaces/ws-a/pages/page-a/move'));
  assert.equal(writes[0].options.body.operation_id,writes[1].options.body.operation_id,'uncertain response retry keeps its operation identity');
  assert.equal(writes[1].options.body.expected_document_sequence,60,'retry retains the exact original request despite a newer read');
  const switched=context();
  switched.ctx.flushAllOpenDrafts=async()=>{switched.state.navigation.workspaceId='ws-b';return true;};
  await assert.rejects(switched.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),/워크스페이스/);
  assert.equal(switched.calls.length,0);
  const pending=context();
  pending.ctx.mainRenameEditor=()=>({flush:async()=>{throw new Error('ACK pending');}});
  await assert.rejects(pending.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),/ACK pending/);
  assert.equal(pending.calls.length,0,'a failed collaborative flush must prevent metadata reads and writes');
  const changedPage=context();
  changedPage.ctx.mainRenameEditor=()=>({flush:async()=>{changedPage.state.activePageId='other';}});
  await assert.rejects(changedPage.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),/열린 페이지/);
  assert.equal(changedPage.calls.length,0);
  const changedDuringRead=context();
  changedDuringRead.ctx.api=async(url,options)=>{
    changedDuringRead.calls.push({url,options});
    changedDuringRead.state.activePageId='newly-opened';
    return {capabilities:{collaborative_page_move:true},page:{id:'page-a',workspace_id:'ws-a',document_sequence:1,workspace_epoch:1,document_epoch:1}};
  };
  await assert.rejects(changedDuringRead.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),/열린 페이지/);
  assert.equal(changedDuringRead.calls.filter(c=>c.options).length,0,'navigation during preflight must not start an unflushed move');
  const locks=context(),mainSurface={inert:false},peekSurface={inert:true};
  locks.ctx.$=selector=>selector==='#pageView'?mainSurface:peekSurface;
  locks.ctx.embeddedEditorState=()=>({composing:false});
  const releaseFirst=locks.ctx.holdPageMoveInputs(),releaseSecond=locks.ctx.holdPageMoveInputs();
  releaseFirst();
  assert.equal(mainSurface.inert,true,'a second pending move keeps inputs held');
  releaseFirst();
  releaseSecond();
  assert.equal(mainSurface.inert,false,'all holds released restores the original editor state');
  assert.equal(peekSurface.inert,true,'an existing unrelated input lock is preserved');
  const ordinaryFailure=context();ordinaryFailure.state.navigation.collaborativeSharing=false;
  ordinaryFailure.ctx.refreshOpenPagesAfterMutation=async()=>({'page-a':{failed:true,error:new Error('refresh unavailable')}});
  await assert.rejects(ordinaryFailure.ctx.movePageSafely('page-a',{parent_id:'parent-a'}),error=>error.moveSaved===true&&/refresh unavailable/.test(error.message));
  assert.equal(ordinaryFailure.calls.filter(c=>c.options?.method==='POST').length,1,'a saved ordinary move is reported separately from its failed refresh');
  const wrong=context();wrong.state.navigation.collaborativeSharing=false;wrong.page.page.collaborative_editing=false;
  assert.equal((await wrong.ctx.refreshOpenedPageAfterMutation('page-a',{page:{id:'page-b'},blocks:[]})).failed,true);
  assert.equal(wrong.state.pageData,wrong.page);
  const stale=context();stale.state.navigation.collaborativeSharing=false;stale.page.page.collaborative_editing=false;
  stale.ctx.api=async()=>{stale.state.navigation.workspaceId='ws-b';return {page:{id:'page-a',title:'Old workspace'},blocks:[]};};
  await stale.ctx.refreshOpenedPageAfterMutation('page-a');
  assert.equal(stale.state.pageData,stale.page,'late ordinary responses cannot overwrite another workspace');
  const failed=context();failed.ctx.refreshVisiblePageMetadata=async()=>{throw new Error('offline');};
  assert.equal((await failed.ctx.refreshOpenedPageAfterMutation('page-a')).failed,true);
  assert.equal(failed.state.pageData,failed.page);
  console.log('UI-SHELL-002: collaborative post-mutation refresh preserves live documents; unsupported moves never write legacy storage');
})().catch(error=>{console.error(error);process.exitCode=1;});
