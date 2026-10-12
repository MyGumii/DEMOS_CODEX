"use strict";
// UI-META-005: acknowledged body activity, bounded reads and preserved editing state.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const Sync = require("../notion_static/notion_metadata_sync.js");
const source = fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
function fixture() {
  let scope={subject_id:"owner",workspace_id:"ws"},next=0;
  const timers=new Map(),messages=[],reads=[],errors=[];
  const channel={postMessage:value=>messages.push(value),close(){}};
  const host={scope:()=>scope,channel,hasPage:id=>["a","b"].includes(id),
    setTimeout(fn,delay){timers.set(++next,{fn,delay});return next;},clearTimeout:id=>timers.delete(id),
    refresh(){throw Error("body activity must not replay every workspace title");},
    refreshActivity:async(ids,key)=>reads.push({ids,key}),onError:error=>errors.push(error)};
  const sync=Sync.create(host);
  return {sync,host,timers,messages,reads,errors,channel,setScope:value=>scope=value,
    async tick(){const batch=[...timers.values()];timers.clear();await Promise.all(batch.map(item=>item.fn()));}};
}
(async()=>{
  const local=fixture(),remote=fixture(),scope=local.host.scope();
  local.sync.activity(scope,"a");const firstTimer=[...local.timers.keys()][0];
  for(let i=0;i<200;i++)local.sync.activity(scope,"a");
  local.sync.activity(scope,"b");
  assert.deepEqual([...local.timers.keys()],[firstTimer],"continuous typing cannot indefinitely postpone activity reads");
  assert.equal([...local.timers.values()][0].delay,750);
  await local.tick();assert.equal(local.reads.length,1);assert.deepEqual(local.reads[0].ids,["a","b"]);
  assert.equal(local.messages.length,2,"one invalidation per page in the acknowledged batch");
  assert.notEqual(local.messages[0].contract,"notion-page-metadata-invalidated-v1","older title-only clients must ignore body-activity messages instead of replaying workspace titles");
  assert.deepEqual(Object.keys(local.messages[0]).sort(),["contract","kind","page_id","subject_id","workspace_id"].sort());
  for(const data of local.messages)remote.channel.onmessage({data});
  await remote.tick();assert.equal(remote.reads.length,1);assert.equal(remote.messages.length,0,"received activity never echoes");
  remote.channel.onmessage({data:{...local.messages[0],subject_id:"another"}});
  remote.channel.onmessage({data:{...local.messages[0],workspace_id:"another"}});
  remote.channel.onmessage({data:{...local.messages[0],page_id:"unknown"}});
  await remote.tick();assert.equal(remote.reads.length,1);
  let release;local.host.refreshActivity=()=>new Promise(resolve=>release=resolve);
  local.sync.activity(scope,"a");const inFlight=local.tick();
  local.sync.activity(scope,"a");local.sync.activity(scope,"b");
  assert.equal(local.timers.size,0,"a slow request cannot accumulate concurrent activity batches");
  release();await inFlight;assert.equal(local.timers.size,1,"changes while reading are refreshed afterward");
  local.host.refreshActivity=async(ids,key)=>local.reads.push({ids,key});await local.tick();
  assert.equal(local.reads.length,2);
  local.sync.activity(scope,"a");local.setScope({subject_id:"owner",workspace_id:"other"});await local.tick();
  assert.equal(local.reads.length,2,"pending activity cannot follow a workspace switch");
  local.setScope(scope);local.host.refreshActivity=async()=>{throw Error("offline");};
  local.sync.activity(scope,"a");await local.tick();assert.equal(local.errors.length,1);
  assert.equal(local.timers.size,0,"a failed read does not spin or report a fabricated edit time");
  local.sync.activity(scope,"b");local.sync.dispose();await local.tick();assert.equal(local.errors.length,1);

  const input={value:"/제목",composing:true},body=[{id:"block",content:"unsaved 한글"}];
  const page={id:"a",updated_at:10,document_sequence:2,workspace_epoch:1,document_epoch:1,title:"typing",collaborative_editing:true};
  const peek={id:"b",updated_at:9,document_sequence:1};
  const State={user:{id:"owner"},navigation:{sequence:1,workspaceId:"ws",collaborativeSharing:true},
    activePageId:"a",pageData:{page,blocks:body},pages:[{...page},{...peek}],sidePeek:{data:{page:peek,blocks:body},dirty:true},
    liveCollaboration:{crdtReady:true,pendingEnvelopes:new Map(),persistenceChain:Promise.resolve(),editingScope:{...scope,page_id:"a"}},dirty:true};
  const time={},meta={},peekLabel={dataset:{pageActivity:"b"}};
  const document={activeElement:input,querySelectorAll:()=>[peekLabel]};
  const requests=[],notifications=[],outbox=[];
  const context={State,document,window:{NotionMetadataSync:Sync},$:id=>id==="#pageEditTime"?time:id==="#pageMeta"?meta:null,
    formatTime:value=>`time-${value}`,formatRelative:value=>`relative-${value}`,pageIsLocked:()=>false,
    activeFullPageDatabase:()=>null,
    api:url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject})),
    PageMetadataSync:{activity:(...args)=>notifications.push(args),saved(){}},
    CollaborativeEditingOutbox:{remove:async(...args)=>outbox.push(args)},updateCollaborativeSyncStatus(){}};
  vm.createContext(context);
  for(const [start,end] of [["function renderPageActivity(","function renderEditor("],
    ["function pageMetadataScope(","let PageMetadataReadSequence"],
    ["async function refreshVisiblePageActivity(","function installPageMetadataSync("],
    ["function removeCollaborativeOperation(","async function prepareCollaborativeEditingSession("]]) {
    vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
  }
  const key=Sync.scopeKey(scope),fresh=(id,seq,at)=>({page:{id,workspace_id:"ws",document_sequence:seq,updated_at:at,workspace_epoch:1,document_epoch:1}});
  let pending=context.refreshVisiblePageActivity(["a","b"],key);
  assert.ok(requests[0].url.endsWith("/pages/a/activity"));
  requests[0].resolve(fresh("a",7,70));requests[1].resolve(fresh("b",4,40));await pending;
  assert.equal(page.updated_at,70);assert.equal(time.hidden,true);assert.equal(time.textContent,"relative-70 편집");assert.equal(peekLabel.textContent,"time-40");
  assert.equal(page.document_sequence,2,"activity cannot pretend the body snapshot advanced");
  assert.equal(page.activity_document_sequence,7);assert.equal(State.dirty,true);assert.equal(State.sidePeek.dirty,true);
  assert.equal(State.pageData.blocks,body);assert.equal(document.activeElement,input);assert.equal(input.value,"/제목");assert.equal(input.composing,true);
  pending=context.refreshVisiblePageActivity(["a"],key);requests[2].resolve(fresh("a",6,60));await pending;assert.equal(page.updated_at,70);
  pending=context.refreshVisiblePageActivity(["a"],key);requests[3].resolve(fresh("wrong-page",8,80));await pending;assert.equal(page.updated_at,70);
  pending=context.refreshVisiblePageActivity(["a"],key);State.navigation.sequence++;requests[4].resolve(fresh("a",9,90));await pending;assert.equal(page.updated_at,70);
  pending=context.refreshVisiblePageActivity(["a"],key);State.user.id="another";requests[5].resolve(fresh("a",10,100));await pending;assert.equal(page.updated_at,70);
  State.user.id="owner";
  pending=context.refreshVisiblePageActivity(["a"],key);requests[6].reject(Error("permission revoked"));await pending;assert.equal(page.updated_at,70);
  page.document_epoch=3;pending=context.refreshVisiblePageActivity(["a"],key);requests[7].resolve(fresh("a",11,110));await pending;assert.equal(page.updated_at,70);
  State.liveCollaboration.pendingEnvelopes.set("saved-body",{recovery_snapshot:{title:"typing"}});
  assert.equal(context.removeCollaborativeOperation("saved-body"),true);
  assert.equal(context.removeCollaborativeOperation("saved-body"),false);
  assert.equal(notifications.length,1,"only the durable removal, not unsaved typing, schedules local activity");
  await State.liveCollaboration.persistenceChain;assert.equal(outbox.length,1);assert.equal(State.liveCollaboration.unsyncedCount,0);
  context.collaborativeEditingActive=()=>true;
  context.CollaborativeEditorBridge={applyRemoteAndProject:()=>({operation_ids:[]})};
  context.renderPresenceAvatars=()=>{};context.scheduleLiveCanonicalRefresh=()=>{};
  vm.runInContext(source.slice(source.indexOf("function handleLiveFrames("),source.indexOf("function scheduleLiveReconnect(")),context);
  context.handleLiveFrames([{kind:"crdt.update",document_sequence:12}]);
  assert.equal(notifications.length,2,"a remote body frame also refreshes activity without a local pending operation");
  context.handleLiveFrames([]);assert.equal(notifications.length,2,"an idle live poll does not read activity");
  console.log("UI-META-005: batched local/remote body activity, no workspace replay, single in-flight batch, scope/sequence/epoch fences and IME/dirty/outbox preservation passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
