"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const slice=(start,end)=>source.slice(source.indexOf(start),source.indexOf(end));
const Draft=require("../notion_static/notion_page_draft.js");
const clone=value=>JSON.parse(JSON.stringify(value));
const nodes=new Map();
const get=selector=>{if(!nodes.has(selector))nodes.set(selector,{hidden:true,value:"",textContent:"",innerHTML:"",focus(){},replaceChildren(){}});return nodes.get(selector);};
const State={user:{id:"owner"},navigation:{workspaceId:"",enabled:false,workspaces:[]},workspaceOwnerFilter:"",
  collapsedPageIds:new Set(),activePageId:"parent",dirty:false};
let createCount=0,saveCount=0,requests=[],fail=true,created=null,blockSave=false;
const context={State,$:get,LinkedDatabaseItems:null,activeDatabaseDatePickerAnchor:null,
  window:{NotionPageDraft:Draft,NotionAiNotes:{beforeNavigate:async()=>true}},
  document:{activeElement:null},EditorCore:{deepClone:clone},newOperationId:()=>"operation-1",
  isNarrowSidebar:()=>false,syncSidePeekMobileTrigger(){},
  setSidePeekCreatePresentation(){},
  closeSidePeekSharePopover(){},
  clearReservedChildRoute(){},rememberReservedChildRoute(){},
  syncSidePeekStarterVisibility(){},
  requestAnimationFrame:fn=>fn(),
  setStatus(){},clearTimeout(){},setTimeout(){return 1;},saveCollapsedPages(){},renderSidePeek(){},renderPageLists(){},
  sidePeekFocusSnapshot:()=>null,restoreSidePeekFocus(){},loadPages:async()=>{},
  ensureSidePeekEditor:async()=>true,ensureDraftParentLink:async()=>{},
  resolveSidePeekDatabaseContext:()=>null,setSidePeekEditorStatus(){},
  flushPageDraft:async()=>true,refreshOpenedPageAfterMutation:async()=>{},refreshOtherOpenPagesAfterMutation:async()=>{},
  normalizedPageData:clone,serverRevisionFrom:data=>data?.revision||null,
  applySaveMetadata(holder,data){holder.serverRevision=data.revision;holder.atomicSaveEnabled=true;},
  atomicSaveRequest:(holder,data,title,blocks)=>({base_revision:holder.serverRevision,operation_id:"save-1",page:{title},blocks}),
  api:async(url,options)=>{
    if(url==="/api/notion/pages"){
      createCount++;requests.push(clone(options.body));
      if(!created)created={page:{id:"child",title:options.body.title,can_edit:true},blocks:clone(options.body.blocks),
        revision:1,capabilities:{atomic_save:true,local_page_draft_create:true}};
      if(fail){fail=false;throw new Error("response lost after server commit");}
      return clone(created);
    }
    if(url.endsWith("/save")){
      saveCount++;
      if(blockSave)throw new Error("save unavailable");
      return {...clone(created),page:{...created.page,...options.body.page},blocks:clone(options.body.blocks),revision:2};
    }
    throw new Error("unexpected request "+url);
  },
};
vm.createContext(context);
vm.runInContext([
  slice("let SidePeekOpenSequence =", "async function openSidePeek(pageId"),
  slice("async function closeSidePeek(","function resolveSidePeekDatabaseContext("),
  slice("function markSidePeekDirty(","async function openSidePeekAsFullPage("),
].join("\n"),context);
function holder(title=""){
  return {pageId:"",data:{page:{title,can_edit:true},blocks:[{id:"body",type:"paragraph",content:"",props:{}}]},
    draft:Draft.create({userId:"owner",workspaceId:"",ownerFilter:"",parentId:"parent",parentTitle:"부모"},"intent"),
    editRevision:0,dirty:false,saveInFlight:null,saveConflict:null};
}
(async()=>{
  State.sidePeek=holder();get("#sidePeek").hidden=false;
  assert.equal(await context.closeSidePeek(),true);assert.equal(createCount,0);assert.equal(get("#sidePeek").hidden,true);
  State.sidePeek=holder("처음 제목");get("#sidePeekTitle").value="처음 제목";
  context.markSidePeekDirty();State.sidePeek.composing=true;
  await context.saveSidePeek();assert.equal(createCount,0,"IME composition defers initial creation");
  assert.equal(await context.closeSidePeek(),false,"composition cannot close the draft");
  State.sidePeek.composing=false;
  await context.saveSidePeek();assert.equal(State.sidePeek.dirty,true);assert.equal(State.sidePeek.pageId,"");
  State.sidePeek.data.page.title="추가 입력한 제목";get("#sidePeekTitle").value="추가 입력한 제목";
  State.sidePeek.data.blocks[0].content="응답 유실 후에도 입력";context.markSidePeekDirty();
  blockSave=true;await context.saveSidePeek();
  assert.deepEqual(requests[0],requests[1]);assert.equal(State.sidePeek.pageId,"child");
  assert.equal(State.sidePeek.dirty,true);assert.equal(State.sidePeek.data.blocks[0].content,"응답 유실 후에도 입력");
  assert.equal(await context.closeSidePeek(),false,"body-save failure keeps the draft open");
  blockSave=false;await context.saveSidePeek();
  assert.equal(createCount,2,"one create plus one exact retry; later body-save retries use the returned ID");
  assert.equal(State.sidePeek.dirty,false);assert.equal(State.sidePeek.data.page.title,"추가 입력한 제목");
  assert.equal(State.sidePeek.data.blocks[0].content,"응답 유실 후에도 입력");
  assert.equal(await context.closeSidePeek(),true);
  assert.equal(saveCount,2,"clean close does not issue another save after the acknowledged retry");
  const pendingEditor=holder("보존할 제목");
  pendingEditor.pageId="child";pendingEditor.draft=null;pendingEditor.embeddedRequired=true;
  pendingEditor.dirty=false;
  let flushAttempts=0,childDirty=true,connectionDisposed=false;
  pendingEditor.embedded={
    state:()=>({dirty:childDirty,composing:false}),
    flush:async()=>{
      flushAttempts++;
      if(flushAttempts===1)throw new Error("저장이 아직 완료되지 않았습니다.");
      childDirty=false;
      return {page:{id:"child",title:"보존할 제목"},blocks:[{id:"body",content:"입력 유지"}]};
    },
  };
  pendingEditor.embeddedConnection={dispose(){connectionDisposed=true;}};
  State.sidePeek=pendingEditor;get("#sidePeek").hidden=false;
  assert.equal(await context.closeSidePeek(),false,"pending child save must keep the panel and input open");
  assert.equal(get("#sidePeek").hidden,false);
  assert.equal(State.sidePeek,pendingEditor);
  assert.equal(connectionDisposed,false);
  assert.equal(await context.closeSidePeek(),true,"a successful retry may close the saved child editor");
  assert.equal(flushAttempts,2);
  assert.equal(connectionDisposed,true);
  assert.equal(get("#sidePeek").hidden,true);
  console.log("UI-DRAFT-002: actual side-peek integration preserves IME, response-loss edits, saved IDs and close failure state");
})().catch(error=>{console.error(error);process.exitCode=1;});
