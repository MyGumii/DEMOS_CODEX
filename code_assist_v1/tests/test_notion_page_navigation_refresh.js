"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const Sync=require("../notion_static/notion_metadata_sync.js");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
function part(start,end){const a=source.indexOf(start),b=source.indexOf(end,a);assert(a>=0&&b>a);return source.slice(a,b);}
const editor={page:{id:"open-page",title:"입력 중"},blocks:[{content:"저장 전 본문"}]};
const State={user:{id:"owner"},navigation:{enabled:true,collaborativeSharing:true,workspaceId:"workspace",sequence:1},
  pages:[],activePageId:"open-page",pageData:editor,dirty:true,composing:true};
const search={value:""};let resolve,renders=0;
const references=[];
const context={State,window:{NotionMetadataSync:Sync},$:()=>search,document:{querySelector:()=>null},
  fetchWorkspacePagesSnapshot:async()=>[{id:"new-child",title:"새 페이지"}],
  loadDirectDatabaseNavigation:async()=>true,
  refreshPageReferenceDisplays:pages=>references.push(pages),
  renderPageLists:()=>renders++,renderWorkspacePopover(){},renderBreadcrumbPopover(){},renderSearchCreators(){},
  renderHome(){throw Error("navigation refresh must not replace the open editor");}};
vm.createContext(context);vm.runInContext([
  part("function pageMetadataScope()", "let PageMetadataReadSequence ="),
  part("let PageNavigationReadSequence =", "let PageMetadataPendingScope ="),
  part("function applyWorkspacePagesSnapshot(", "async function initializeWorkspaceNavigation("),
].join("\n"),context);
const scope=Sync.scopeKey({subject_id:"owner",workspace_id:"workspace"});
(async()=>{
  await context.refreshPageNavigation(scope);
  assert.equal(State.pages[0].id,"new-child");assert.equal(renders,1);
  assert.equal(State.activePageId,"open-page");assert.equal(State.pageData,editor);assert(State.dirty&&State.composing);
  context.fetchWorkspacePagesSnapshot=()=>new Promise(r=>resolve=r);
  let pending=context.refreshPageNavigation(scope);State.navigation.workspaceId="other";resolve([{id:"wrong"}]);await pending;
  assert.equal(renders,1);State.navigation.workspaceId="workspace";
  pending=context.refreshPageNavigation(scope);search.value="new filter";resolve([{id:"wrong"}]);await pending;
  assert.equal(renders,1);search.value="";
  pending=context.refreshPageNavigation(scope);State.navigation.sequence++;resolve([{id:"wrong"}]);await pending;
  assert.equal(renders,1);
  pending=context.refreshPageNavigation(scope);State.user.id="other-user";resolve([{id:"wrong"}]);await pending;
  assert.equal(renders,1);
  State.user.id="owner";
  context.document.querySelector=()=>({});
  search.value="visible";
  context.fetchWorkspacePagesSnapshot=async(_workspace,query)=>query
    ? [{id:"visible",title:"검색 결과"}]
    : [{id:"open-page",title:"최신 참조",icon:"📌"}];
  assert.equal(await context.loadWorkspacePagesSnapshot(),true);
  assert.deepEqual(Array.from(State.pages,p=>p.id),["visible"],"full references must not replace the filtered sidebar");
  assert.equal(references.at(-1)[0].title,"최신 참조");
  assert.equal(State.activePageId,"open-page");assert.equal(State.pageData,editor);assert(State.dirty&&State.composing);
  await context.refreshPageNavigation(scope);
  assert.equal(references.length,2,"filtered navigation invalidations also reauthorize full reference metadata");
  const delayed=[];
  context.fetchWorkspacePagesSnapshot=async(_workspace,query)=>query
    ? [{id:query}]
    : new Promise(done=>delayed.push(done));
  search.value="old-filter";
  const old=context.loadWorkspacePagesSnapshot();
  await new Promise(setImmediate);
  search.value="new-filter";
  const fresh=context.loadWorkspacePagesSnapshot();
  await new Promise(setImmediate);
  assert.equal(delayed.length,2);
  delayed[1]([{id:"open-page",title:"fresh reference",icon:""}]);assert.equal(await fresh,true);
  delayed[0]([{id:"open-page",title:"old reference",icon:"📌"}]);assert.equal(await old,false);
  assert.deepEqual(Array.from(State.pages,p=>p.id),["new-filter"]);
  assert.equal(references.length,3);assert.equal(references.at(-1)[0].title,"fresh reference");
  assert.equal(State.pageData,editor);assert(State.dirty&&State.composing);
  console.log("UI-NATIVE-NAVIGATION: new-page discovery preserves open drafts/IME and rejects stale scope/filter reads");
})().catch(error=>{console.error(error);process.exitCode=1;});
