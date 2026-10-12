"use strict";
const assert = require("node:assert/strict"), {test} = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const read = name => fs.readFileSync(path.join(__dirname,"../notion_static/"+name),"utf8");
const part = (source,a,b) => source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function dom() {
  const nodes=new Map();let focused=null;
  const make=()=>{const classes=new Set();return {hidden:true,disabled:false,checked:false,value:"",dataset:{},attrs:{},children:[],listeners:{},
    classList:{add:v=>classes.add(v),remove:v=>classes.delete(v),contains:v=>classes.has(v),toggle:(v,on)=>on?classes.add(v):classes.delete(v)},
    setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this.attrs[k];},
    addEventListener(k,v){this.listeners[k]=v;},append(...v){this.children.push(...v);},
    replaceChildren(...v){this.children=v;},focus(){focused=this;}};};
  const get=id=>{if(!nodes.has(id))nodes.set(id,make());return nodes.get(id);};
  return {get,make,focus:()=>focused};
}
function tabs() {
  const d=dom(),calls=[],source=read("notion.js");
  const nav=["homeNavBtn","sidebarChatNavBtn","sidebarMeetingsNavBtn","inboxNavBtn","sidebarSearchBtn"].map(id=>Object.assign(d.get("#"+id),{id}));
  nav.slice(0,4).forEach((el,i)=>el.dataset.sidebarTab=["pages","chat","meetings","inbox"][i]);
  const body={page:{id:"page-a"},blocks:[{id:"block-a",content:"편집 중"}]};
  const State={sidebarMode:"pages",activePageId:"page-a",pageData:body,dirty:true,history:{entries:["before","after"],index:1},selectedBlockIds:new Set(["block-a"])};
  const context={State,$:d.get,document:{querySelectorAll:s=>s==="[data-sidebar-tab]"?nav.slice(0,4):nav},
    window:{NotionAiHome:{showHistory:()=>calls.push("history"),hideHistory:()=>calls.push("hide-history")}},
    closePageNavigationMenus:()=>calls.push("close-menu"),closeInboxFilterMenu:()=>calls.push("close-inbox-filter"),loadInboxItems:()=>calls.push("inbox"),
    openDrawer:()=>calls.push("drawer")};
  vm.createContext(context);
  vm.runInContext(part(source,"function setActiveNav(","function applyAdoptedSurfacePolicy(")
    +part(source,"let SidebarMeetings =","async function switchWorkspaceOwner("),context);
  context.meetings={showSidebar:()=>calls.push("meetings"),leave:()=>calls.push("hide-meetings")};
  vm.runInContext("SidebarMeetings = meetings",context);
  context.bindSidebarTabs();return {d,nav,State,body,calls,context};
}
test("UI-SIDEBAR-TABS switching lists preserves unsaved document, history and selected cells",()=>{
  const h=tabs(),history=h.State.history,selection=h.State.selectedBlockIds;
  for(const [index,panel] of [[1,"#aiHomeSidebar"],[2,"#sidebarMeetingsPanel"],[3,"#inboxOverlay"],[0,"#sidebarPagePanel"]]) {
    h.nav[index].listeners.click();
    assert.equal(h.d.get(panel).hidden,false);
    assert.equal(h.State.pageData,h.body);assert.equal(h.State.dirty,true);
    assert.equal(h.State.activePageId,"page-a");assert.equal(h.State.history,history);assert.equal(h.State.selectedBlockIds,selection);
    assert.equal(h.nav[index].attrs["aria-selected"],"true");
    assert.equal(h.nav.filter(el=>el.attrs["aria-selected"]==="true").length,1);
  }
});
test("UI-SIDEBAR-TABS keyboard selection and overlays do not derive the tab from the main view",()=>{
  const h=tabs();let prevented=false;
  h.nav[0].listeners.keydown({key:"ArrowLeft",preventDefault(){prevented=true;}});
  assert.equal(prevented,true);assert.equal(h.State.sidebarMode,"inbox");assert.equal(h.d.focus(),h.nav[3]);
  h.context.setActiveNav("home");assert.equal(h.nav[3].attrs["aria-selected"],"true");
  h.context.setActiveNav("search");assert.equal(h.nav[3].attrs["aria-selected"],"true");
  h.nav[3].listeners.keydown({key:"Home",preventDefault(){}});
  assert.equal(h.State.sidebarMode,"pages");assert.equal(h.d.get("#inboxOverlay").hidden,true);
  h.nav[0].listeners.keydown({key:"End",isComposing:true,preventDefault(){throw Error("IME consumed");}});
  assert.equal(h.State.sidebarMode,"pages");
});
test("UI-SIDEBAR-TABS committed workspace switches leave old page, meeting and chat URLs; explicit destination routing is retained",async()=>{
  for(const pathname of ["/workspace","/ai/old-chat","/page/old-page"]) for(const preserveLocation of [false,true]) {
    const State={pageLoadSequence:0,searchRequestSequence:0,navigation:{enabled:true,workspaceId:"a",sequence:1,workspaces:[{id:"b"}],visitedWorkspaceIds:new Set()}};
    const paths=[],directLoads=[],directGate=deferred(),d=dom(),context={State,$:d.get,location:{pathname,search:"?view=meetings"},
      window:{NotionAiNotes:{beforeNavigate:async()=>true}},flushPendingNavigationPreferences:async()=>true,flushIndependentDatabaseItemBodyBeforeNavigate:async()=>true,navigationControlsLocked:()=>false,
      refreshWorkspaceNavigationCapability:async()=>true,flushAllOpenDrafts:async()=>true,closeSidePeek:async()=>true,closeWorkspacePopover(){},
      setNavigationPending(){},api:async()=>({}),normalizeWorkspaceNavigationSnapshot:()=>({}),normalizeWorkspacePagesPayload:()=>[],
      goHome:async()=>true,applyWorkspaceNavigationSnapshot(){},applyWorkspacePagesSnapshot(){},loadDirectDatabaseNavigation:(id,sequence)=>{directLoads.push([id,sequence]);return directGate.promise;},renderSearchResults(){},renderSearchPreview(){},renderEditor(){},
      history:{replaceState:(_,__,url)=>paths.push(url)},localStorage:{setItem(){}},navigationSelectionKey:()=>"scope",loadWorkspaceAccess(){},requestAnimationFrame:fn=>fn(),
      setStatus:message=>{throw Error(message);}};
    vm.runInNewContext(part(read("notion.js"),"async function switchCanonicalWorkspace(","async function toggleWorkspaceFavorite("),context);
    const switched=context.switchCanonicalWorkspace("b",{preserveLocation});
    assert.equal(await Promise.race([switched,new Promise(resolve=>setTimeout(()=>resolve("optional list blocked navigation"),100))]),true);
    assert.equal(State.navigation.workspaceId,"b");assert.deepEqual(paths,preserveLocation?[]:["/workspace"]);
    assert.deepEqual(directLoads,[["b",2]]);
    directGate.resolve(true);
  }
});

function historyFixture() {
  const d=dom(),requests=[],opens=[],source=read("notion_ai_home.js");let key="alice/ws-a";
  const timers=new Map();let timerId=0;
  const context={$:d.get,node:(tag,text,cls)=>Object.assign(d.make(),{tag,textContent:text,className:cls}),uuid:()=>"archive-op",URLSearchParams,
    setTimeout:fn=>{timers.set(++timerId,fn);return timerId;},clearTimeout:id=>timers.delete(id),
    host:{key:()=>key,workspaceId:()=>key.split("/")[1],api:(url,options)=>{const p=deferred();requests.push({...p,url,options});return p.promise;}},
    open:async id=>opens.push(id)};
  vm.createContext(context);vm.runInContext(`let historyVisible=false,historyKey="",historySession=null,historyLoading=false,historyCursor=null,listSerial=0,visible=false,cid="";
    let historySearchTimer=null,historyComposing=false,historyGroups=new Map(),historyIds=new Set();`
    +part(source,"  function historyDateGroup(","  async function download("),context);
  const rows=()=>d.get("aiHomeHistory").children.flatMap(group=>group.children[1].children);
  return {d,requests,opens,context,rows,scope:v=>key=v,timers,flush:()=>{const all=[...timers.values()];timers.clear();all.forEach(fn=>fn());}};
}

test("UI-CHAT-HISTORY local calendar groups handle midnight, old months and invalid dates",()=>{
  const h=historyFixture(),now=new Date(2026,8,22,0,5);
  const label=(year,month,day)=>h.context.historyDateGroup(new Date(year,month,day,23,59).getTime()/1000,now).label;
  assert.equal(label(2026,8,22),"오늘");assert.equal(label(2026,8,21),"어제");
  assert.equal(label(2026,8,16),"지난 7일");assert.equal(label(2026,8,15),"지난 30일");
  assert.equal(label(2026,7,1),"2026년 8월");assert.equal(h.context.historyDateGroup(undefined,now).label,"날짜 없음");
});

test("UI-CHAT-HISTORY searching invalidates old actions immediately and ignores out-of-order responses",async()=>{
  const h=historyFixture(),first=h.context.showHistory();h.requests[0].resolve({items:[{id:"old",title:"Old"}],next_cursor:"50"});await first;
  const old=h.rows()[0].children[0];h.d.get("aiHomeHistorySearch").value="alpha";h.context.searchHistory();
  await old.listeners.click();await h.context.history(true);assert.equal(h.opens.length,0);assert.equal(h.requests.length,1);
  h.flush();assert.match(h.requests[1].url,/q=alpha/);
  h.d.get("aiHomeHistorySearch").value="제목";h.context.searchHistory();h.flush();
  h.requests[2].resolve({items:[{id:"new",title:"제목"}]});await new Promise(setImmediate);
  h.requests[1].resolve({items:[{id:"wrong",title:"alpha"}]});await new Promise(setImmediate);
  assert.deepEqual(h.rows().map(row=>row.dataset.chatId),["new"]);
});

test("UI-CHAT-HISTORY grouped pagination keeps each date heading once and deduplicates rows",async()=>{
  const h=historyFixture(),first=h.context.showHistory(),now=Date.now()/1000;
  h.requests[0].resolve({items:[{id:"a",title:"A",updated_at:now}],next_cursor:"50"});await first;
  const more=h.context.history(true);h.requests[1].resolve({items:[{id:"a",title:"A",updated_at:now},{id:"b",title:"B",updated_at:now},{id:"c",title:"C",updated_at:now-864000}]});await more;
  assert.deepEqual(h.rows().map(row=>row.dataset.chatId),["a","b","c"]);assert.equal(h.d.get("aiHomeHistory").children.length,2);
  assert.equal(h.d.get("aiHomeHistoryTitle").textContent,"오늘");
});

test("UI-CHAT-HISTORY close search restores focus and leaving cancels pending queries",async()=>{
  const h=historyFixture(),first=h.context.showHistory();h.requests[0].resolve({items:[]});await first;
  h.context.setHistorySearch(true);assert.equal(h.d.focus(),h.d.get("aiHomeHistorySearch"));
  h.d.get("aiHomeHistorySearch").value="draft";h.context.searchHistory();h.context.setHistorySearch(false);
  assert.equal(h.timers.size,0);assert.equal(h.d.get("aiHomeHistorySearch").value,"");
  assert.equal(h.d.focus(),h.d.get("aiHomeHistorySearchBtn"));assert.equal(h.d.get("aiHomeHistorySearchBox").hidden,true);
  h.requests[1].resolve({items:[]});await new Promise(setImmediate);
  h.d.get("aiHomeHistorySearch").value="pending";h.context.searchHistory();h.context.hideHistory();h.flush();assert.equal(h.requests.length,2);
  h.scope("bob/ws-b");const next=h.context.showHistory();assert.equal(h.d.get("aiHomeHistorySearch").value,"");h.requests[2].resolve({items:[]});await next;
});

test("UI-CHAT-HISTORY composition suppresses queries and failed pagination retains visible results",async()=>{
  const h=historyFixture(),first=h.context.showHistory();h.requests[0].resolve({items:[{id:"a",title:"A"}],next_cursor:"50"});await first;
  const more=h.context.history(true);h.requests[1].reject(Error("offline"));await more;
  assert.equal(h.rows().length,1);assert.match(h.d.get("aiHomeHistoryStatus").textContent,/offline/);
  vm.runInContext('historyComposing=true',h.context);h.d.get("aiHomeHistorySearch").value="제";
  h.context.searchHistory();h.flush();await h.context.history();assert.equal(h.requests.length,2);
  vm.runInContext('historyComposing=false',h.context);h.context.searchHistory();h.flush();
  h.requests[2].resolve({items:[]});await new Promise(setImmediate);assert.equal(h.d.get("aiHomeHistoryStatus").textContent,"검색된 채팅이 없습니다.");
});

test("UI-CHAT-HISTORY clearing the query then immediately closing still reloads the unfiltered list",async()=>{
  const h=historyFixture(),first=h.context.showHistory();h.requests[0].resolve({items:[{id:"a",title:"A"}]});await first;
  h.context.setHistorySearch(true);h.d.get("aiHomeHistorySearch").value="typed";h.context.searchHistory();
  h.d.get("aiHomeHistorySearch").value="";h.context.searchHistory();h.context.setHistorySearch(false);
  assert.equal(h.timers.size,0);assert.equal(h.requests.length,2);assert.doesNotMatch(h.requests[1].url,/q=/);
  h.requests[1].resolve({items:[{id:"a",title:"A"}]});await new Promise(setImmediate);
  assert.equal(h.rows().length,1);assert.equal(h.d.get("aiHomeHistoryStatus").textContent,"");
});
test("UI-SIDEBAR-TABS chat history reads independently and preserves earlier rows during pagination",async()=>{
  const h=historyFixture(),first=h.context.showHistory();
  assert.equal(h.requests[0].url,"/api/notion/ai-home/conversations?workspace_id=ws-a&archived=0");
  assert.equal(h.requests[0].options,undefined);
  h.requests[0].resolve({items:[{id:"a",title:"대화"}],next_cursor:"next"});await first;
  const old=h.rows()[0].children[0];
  const more=h.context.history(true);h.requests[1].resolve({items:[{id:"b",title:"다음"}],next_cursor:null});await more;
  await old.listeners.click();assert.deepEqual(h.opens,["a"]);
  assert.equal(h.rows().length,2);
  assert.equal(h.requests.filter(r=>r.options?.method==="POST").length,0);
});
test("UI-SIDEBAR-TABS hidden and obsolete chat history cannot display late data or perform actions",async()=>{
  const h=historyFixture(),first=h.context.showHistory();
  h.requests[0].resolve({items:[{id:"a",title:"A"}],next_cursor:null});await first;
  const [open,archive]=h.rows()[0].children;
  h.context.hideHistory();await open.listeners.click();await archive.listeners.click();
  assert.equal(h.requests.length,1);assert.equal(h.opens.length,0);
  const again=h.context.showHistory();h.scope("bob/ws-b");
  h.requests[1].reject(Error("previous workspace failure"));await again;
  assert.doesNotMatch(h.d.get("aiHomeHistoryStatus").textContent,/previous workspace failure/);
  const current=h.context.showHistory();h.requests[2].resolve({items:[{id:"b",title:"B"}]});await current;
  assert.equal(h.rows().length,1);
  await open.listeners.click();assert.equal(h.opens.length,0);
});
test("UI-SIDEBAR-TABS archive from a sidebar list does not open a new main conversation",async()=>{
  const h=historyFixture(),first=h.context.showHistory();
  h.requests[0].resolve({items:[{id:"a",title:"A",archived:false}]});await first;
  const archive=h.rows()[0].children[1];
  const operation=archive.listeners.click();await archive.listeners.click();
  assert.equal(h.requests.length,2);assert.equal(h.requests[1].options.method,"POST");
  h.requests[1].resolve({id:"a",archived:true});await new Promise(setImmediate);
  h.requests[2].resolve({items:[]});await operation;
  assert.equal(h.opens.length,0);assert.equal(h.d.get("aiHomeHistory").children.length,0);
});
test("UI-SIDEBAR-TABS switching archived filter invalidates old chat actions before new data arrives",async()=>{
  const h=historyFixture(),first=h.context.showHistory();
  h.requests[0].resolve({items:[{id:"a",title:"A"}],next_cursor:"more"});await first;
  const stale=h.rows()[0].children[0];
  h.d.get("aiHomeArchived").checked=true;const archived=h.context.history();
  await stale.listeners.click();await h.context.history(true);assert.equal(h.opens.length,0);assert.equal(h.requests.length,2);
  h.requests[1].resolve({items:[]});await archived;
});
test("UI-SIDEBAR-TABS meetings sidebar uses real list actions without navigating or focusing the document",async()=>{
  const d=dom(),requests=[],opened=[];let key="a",creates=0;
  const context={module:{exports:{}},document:{getElementById:d.get,createElement:d.make},setTimeout,clearTimeout};
  vm.runInNewContext(read("notion_meetings.js"),context);
  const meetings=context.module.exports({sidebar:true,ids:{meetingList:"sidebarList",meetingsView:"sidebarView"}});
  meetings.init({key:()=>key,canCreate:()=>true,icon:()=>"",relative:()=>"now",
    enter:()=>{throw Error("sidebar must not enter main view");},route:()=>{throw Error("sidebar changed URL");},
    list:()=>{const p=deferred();requests.push(p);return p.promise;},openPage:item=>opened.push(item.id),create:async()=>creates++});
  const first=meetings.showSidebar();requests[0].resolve({items:[{id:"note",title:"Note",note_count:1}],count:1});await first;
  assert.equal(d.focus(),null);assert.equal(d.get("sidebarView").hidden,false);
  d.get("sidebarList").children[0].listeners.click();assert.deepEqual(opened,["note"]);
  await meetings.showSidebar();assert.equal(requests.length,1,"selecting the active tab retains its list");
  key="b";await d.get("meetingNewBtn").listeners.click();assert.equal(creates,0);
  const second=meetings.showSidebar();meetings.leave();requests[1].resolve({items:[{id:"late"}]});await second;
  assert.equal(d.get("sidebarList").children.length,0);assert.equal(d.get("sidebarView").hidden,true);
});
