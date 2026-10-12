"use strict";
// UI-META-004: only saved invalidations cross tabs; receivers reauthorize metadata.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const Sync = require("../notion_static/notion_metadata_sync.js");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function fixture() {
  let scope = {subject_id:"owner",workspace_id:"ws"}, next=0;
  const timers=new Map(), messages=[], reads=[], errors=[];
  const channel={postMessage:m=>messages.push(m),close(){this.closed=true;}};
  const host={channel,scope:()=>scope,hasPage:id=>["a","b"].includes(id),
    setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id),
    refresh:async(ids,key)=>reads.push({ids,key}),onError:e=>errors.push(e)};
  const sync=Sync.create(host);
  return {sync,channel,messages,reads,errors,host,setScope:value=>scope=value,
    async flush(){const callbacks=[...timers.values()];timers.clear();for(const callback of callbacks)await callback();}};
}
(async()=>{
  const a=fixture(),b=fixture(),scope=a.host.scope();
  a.sync.saved(scope,"a","First private title");
  assert.equal(a.messages.length,1);
  assert.ok(!JSON.stringify(a.messages).includes("private title"),"only an invalidation crosses tabs");
  b.channel.onmessage({data:a.messages[0]});b.channel.onmessage({data:a.messages[0]});
  assert.equal(b.reads.length,0);await b.flush();assert.deepEqual(b.reads[0].ids,["a"]);
  a.sync.saved(scope,"a","First private title");assert.equal(a.messages.length,1,"body-only ACKs do not repeatedly refresh lists");
  a.sync.saved(scope,"a","");assert.equal(a.messages.length,2,"removing a title is a real metadata change");
  a.sync.invalidate(['a','b','a']);
  assert.equal(a.messages.length,4,'moves can invalidate unchanged titles once per affected page');
  assert.ok(!JSON.stringify(a.messages.slice(2)).includes('title'));
  b.channel.onmessage({data:{...a.messages[1],workspace_id:"other"}});
  b.channel.onmessage({data:{...a.messages[1],subject_id:"other-user"}});
  b.channel.onmessage({data:{...a.messages[1],page_id:"not-bound"}});
  await b.flush();assert.equal(b.reads.length,1);
  b.channel.onmessage({data:a.messages[1]});b.setScope({subject_id:"owner",workspace_id:"other"});
  await b.flush();assert.equal(b.reads.length,1,"queued reads cannot follow a workspace change");
  a.channel.postMessage=()=>{throw Error("closed channel");};
  assert.doesNotThrow(()=>a.sync.saved(scope,"a","next"),"notification failure cannot block durable ACK removal");
  a.sync.dispose();assert.equal(a.channel.closed,true);

  const sender=fixture(),receiver=fixture(),navigationReads=[];
  receiver.host.refreshNavigation=async key=>navigationReads.push(key);
  sender.sync.navigationChanged(sender.host.scope());
  const notification=sender.messages[0];
  assert.deepEqual(Object.keys(notification).sort(),["contract","subject_id","workspace_id"],"creation notifications carry no private page ID or title");
  receiver.channel.onmessage({data:notification});receiver.channel.onmessage({data:notification});
  await receiver.flush();assert.equal(navigationReads.length,1,"new pages trigger an authorized list read without a known page ID");
  assert.equal(receiver.messages.length,0,"a received creation notification is not broadcast again");
  receiver.channel.onmessage({data:{...notification,subject_id:"other-user"}});
  receiver.channel.onmessage({data:{...notification,workspace_id:"other-workspace"}});
  await receiver.flush();assert.equal(navigationReads.length,1);
  receiver.channel.onmessage({data:notification});receiver.setScope({subject_id:"owner",workspace_id:"elsewhere"});
  await receiver.flush();assert.equal(navigationReads.length,1,"queued navigation reads do not follow a scope change");
  receiver.setScope(sender.host.scope());
  let finishNavigation;
  receiver.host.refreshNavigation=async()=>new Promise(resolve=>{finishNavigation=resolve;});
  receiver.sync.refreshNavigation();const runningNavigation=receiver.flush();
  receiver.channel.onmessage({data:notification});receiver.channel.onmessage({data:notification});
  receiver.host.refreshNavigation=async key=>navigationReads.push(key);
  finishNavigation();await runningNavigation;await receiver.flush();
  assert.equal(navigationReads.length,2,"notifications during a read coalesce into one follow-up read");
  receiver.sync.dispose();receiver.channel.onmessage({data:notification});await receiver.flush();assert.equal(navigationReads.length,2);

  const input={value:"typing"},body={blocks:[{content:"unsaved body"}]};
  const label={textContent:"old"},icon={innerHTML:"",matches:()=>true,setAttribute(name,value){this[name]=value;}};
  const row={dataset:{pageId:"a"},querySelector:selector=>selector===".page-title-label"?label:icon,setAttribute(name,value){this[name]=value;},remove(){this.removed=true;}};
  const State={user:{id:"owner"},navigation:{sequence:1,workspaceId:"ws",collaborativeSharing:true},
    activePageId:"a",pageData:{...body,page:{id:"a",title:"local IME text",collaborative_editing:true}},
    sidePeek:{},liveCollaboration:{crdtReady:true},pages:[{id:"a",title:"old",icon:""},{id:"b",title:"parent"}]};
  let links=0,recovery=0,resolve;
  const document={activeElement:input,querySelectorAll:()=>[row]};
  const context={State,document,window:{NotionMetadataSync:Sync},pageIconHtml:p=>`<svg>${p.icon}</svg>`,
    pageIsLocked:p=>Boolean(p?.settings?.locked),closeIconPickerAfterAccessChange(){},
    reconcileTemporaryUnlockGrant:()=>false,
    refreshPageReferenceDisplays:()=>links++,renderBreadcrumb(){},renderPageActivity(){},renderPageLists(){},enterCollaborativeRecoveryOnly:()=>recovery++,
    fetchWorkspacePagesSnapshot:()=>new Promise(r=>resolve=r)};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("function syncPageTitleDisplays("),source.indexOf("function installPageMetadataSync(")),context);
  context.syncPageTitleDisplays("a","<new title>");
  assert.equal(label.textContent,"<new title>");assert.equal(icon["aria-label"],"<new title> 아이콘 변경");
  assert.equal(label.title,"<new title>");assert.equal(row["aria-label"],"<new title>");
  assert.equal(document.activeElement,input);assert.equal(input.value,"typing");assert.equal(links,1);
  context.syncPageTitleDisplays("a","");assert.equal(label.textContent,"제목 없음");
  let request=context.refreshVisiblePageMetadata(["a"],Sync.scopeKey(scope));
  resolve([{id:"a",title:"older saved title",icon:"🚀",updated_at:123}]);await request;
  assert.equal(label.textContent,"local IME text");assert.equal(State.pageData.blocks[0].content,"unsaved body");
  assert.equal(State.pageData.page.updated_at,123,"authorized list metadata must refresh the open edit time too");
  request=context.refreshVisiblePageMetadata(["a"],Sync.scopeKey(scope));
  State.navigation.sequence++;resolve([{id:"a",title:"stale other workspace",icon:""}]);await request;
  assert.equal(label.textContent,"local IME text");
  const pending=[];
  context.fetchWorkspacePagesSnapshot=()=>new Promise(r=>pending.push(r));
  const first=context.refreshVisiblePageMetadata(["a"],Sync.scopeKey(scope));
  const second=context.refreshVisiblePageMetadata(["b"],Sync.scopeKey(scope));
  pending[1]([{id:"a",title:"new server a",icon:"new-icon",document_sequence:20},{id:"b",title:"new server b",document_sequence:20}]);
  await second;
  pending[0]([{id:"a",title:"old server a",icon:"old-icon",document_sequence:19},{id:"b",title:"old server b",document_sequence:19}]);
  await first;
  assert.equal(State.pages.find(p=>p.id==="a").icon,"new-icon");
  assert.equal(State.pages.find(p=>p.id==="b").title,"new server b","a later request must retain earlier pending page IDs");
  let orderRenders=0;context.renderPageLists=()=>orderRenders++;
  context.fetchWorkspacePagesSnapshot=async()=>State.pages.map(page=>({...page,position:7}));
  await context.refreshVisiblePageMetadata(["b"],Sync.scopeKey(scope));
  assert.equal(orderRenders,1,"a sibling-order invalidation must rerender the sidebar even when its parent is unchanged");
  assert.equal(State.pages.find(page=>page.id==="b").position,7);
  context.fetchWorkspacePagesSnapshot=async()=>[{id:"a",title:"saved title",document_sequence:25,updated_at:250}];
  await context.refreshVisiblePageMetadata(["a"],Sync.scopeKey(scope));
  assert.equal(State.pageData.page.activity_document_sequence,25,"list time stamps fence later activity responses too");
  State.pages=State.pages.filter(page=>page.id!=="a");
  context.fetchWorkspacePagesSnapshot=async()=>[{id:"a",title:"old title",document_sequence:24,updated_at:240}];
  await context.refreshVisiblePageMetadata(["a"],Sync.scopeKey(scope));
  assert.equal(State.pageData.page.updated_at,250,"an open page missing from the sidebar still rejects older metadata");
  context.fetchWorkspacePagesSnapshot=()=>new Promise(r=>resolve=r);
  request=context.refreshVisiblePageMetadata(["a"],Sync.scopeKey(scope));resolve([]);await request;
  assert.equal(recovery,1);assert.equal(row.removed,true);assert.equal(State.pageData.blocks[0].content,"unsaved body");
  let installed;
  context.PageMetadataSync=null;
  context.window.NotionMetadataSync={create:host=>{installed=host;return {refresh(){}};}};
  context.window.addEventListener=()=>{};context.document.addEventListener=()=>{};
  context.window.setTimeout=function(){assert.equal(this,context.window);return 42;};
  context.window.clearTimeout=function(id){assert.equal(this,context.window);assert.equal(id,42);};
  vm.runInContext(source.slice(source.indexOf("function installPageMetadataSync("),source.indexOf("function pageReferenceMetadata(")),context);
  context.installPageMetadataSync();
  assert.equal(installed.setTimeout(()=>{},180),42);installed.clearTimeout(42);
  console.log("UI-META-004: scoped saved-title invalidations, reauthorization, empty titles, input/focus preservation and stale-response rejection passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
