"use strict";
// UI-SHELL-005: selected navigation survives pages, overlays and late reads.
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
const elements=new Map();let focused=null,appearanceRefreshes=0;
function element(id){
  const classes=new Set();
  return {id,hidden:true,isConnected:true,attributes:{},dataset:{},innerHTML:"",classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)},
    setAttribute(k,v){this.attributes[k]=v;},replaceChildren(){this.innerHTML="";},removeAttribute(k){delete this.attributes[k];},getClientRects(){return this.hidden?[]:[{}];},focus(){focused=this;}};
}
for(const id of ["homeNavBtn","sidebarChatNavBtn","sidebarMeetingsNavBtn","inboxNavBtn","sidebarSearchBtn","documentAiDrawerBtn","aiHomeView","meetingsView","inboxOverlay","inspector","inboxList","inboxStatus","inboxRefreshBtn","sidebarRestoreBtn","workspaceMain"]) elements.set("#"+id,element(id));
const nav=["homeNavBtn","sidebarChatNavBtn","sidebarMeetingsNavBtn","inboxNavBtn","sidebarSearchBtn"].map(id=>elements.get("#"+id));
nav.forEach(el=>el.hidden=false);
nav.slice(0,4).forEach((el,index)=>el.dataset.sidebarTab=["pages","chat","meetings","inbox"][index]);
const state={sidebarMode:"pages",activePageId:"page-a",user:{id:"owner"},navigation:{workspaceId:"ws-a",workspaces:[]},pages:[],users:[],inboxItems:[]};
const context={State:state,window:{},$:id=>elements.get(id),document:{activeElement:nav[3],querySelectorAll:()=>nav},closeDrawer(){},
  api:async()=>({items:[]}),renderInboxBadge(){},renderInboxItems(){},escapeHtml:String,
  PersonalWorkspaceAppearance:{refresh(){appearanceRefreshes++;}}};
vm.createContext(context);
vm.runInContext([part("function setActiveNav(","function applyAdoptedSurfacePolicy("),part("let InboxLoadSequence =","function renderInboxItems(")].join("\n"),context);
// Load the real menu/keyboard handlers without bootstrapping unrelated image
// services that were added between these functions.
vm.runInContext([
  part("function openWorkspacePopover(","function personalWorkspaceScope("),
  part("function handleWorkspaceHeaderKeydown(","const WorkspaceIconIntents ="),
].join("\n"),context);
const workspaceButton=element("workspaceName"),collapseButton=element("collapseBtn"),firstMenuButton=element("firstWorkspaceAction");
elements.set("#workspaceName",workspaceButton);
elements.set("#workspacePopover button:not([disabled])",firstMenuButton);
const key=(target,value,extra={})=>{const event={target,key:value,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;},...extra};context.handleWorkspaceHeaderKeydown(event);return event;};
assert.equal(key(workspaceButton,"Enter").prevented,true,"the custom focus handling consumes native activation to avoid a second toggle");
assert.equal(state.workspacePopoverOpen,true);assert.equal(focused,firstMenuButton);
assert.equal(appearanceRefreshes,1,"opening the menu refreshes the personal appearance through its service boundary");
assert.equal(key(firstMenuButton,"Escape").stopped,true);assert.equal(state.workspacePopoverOpen,false);assert.equal(focused,workspaceButton);
key(collapseButton,"Enter");assert.equal(state.workspacePopoverOpen,false,"the sibling collapse button never opens the workspace menu");
key(firstMenuButton," ");assert.equal(state.workspacePopoverOpen,false,"menu actions keep their own native activation");
key(workspaceButton,"Enter",{isComposing:true});assert.equal(state.workspacePopoverOpen,false);
key(workspaceButton," ");assert.equal(state.workspacePopoverOpen,true);key(workspaceButton," ");assert.equal(state.workspacePopoverOpen,false);
const active=()=>nav.filter(el=>el.attributes["aria-selected"]==="true").map(el=>el.id);
(async()=>{
  context.setActiveNav("");assert.deepEqual(active(),["homeNavBtn"]);
  context.setActiveNav("search");assert.deepEqual(active(),["homeNavBtn"]);
  state.sidebarMode="inbox";context.setActiveNav("");assert.deepEqual(active(),["inboxNavBtn"]);
  context.setActiveNav("home");assert.deepEqual(active(),["inboxNavBtn"]);
  state.sidebarMode="meetings";state.activePageId="";elements.get("#meetingsView").hidden=false;
  context.setActiveNav("search");assert.deepEqual(active(),["sidebarMeetingsNavBtn"]);
  state.sidebarMode="chat";elements.get("#meetingsView").hidden=true;elements.get("#aiHomeView").hidden=false;
  context.setActiveNav("");assert.deepEqual(active(),["sidebarChatNavBtn"]);
  let resolve;context.api=()=>new Promise(r=>resolve=r);
  const pending=context.loadInboxItems(true);state.navigation.workspaceId="ws-b";state.inboxItems=[{id:"current-workspace"}];
  resolve({items:[{id:"previous-workspace"}]});await pending;
  assert.deepEqual(state.inboxItems,[{id:"current-workspace"}]);
  console.log("UI-SHELL-005 passed: page selection, search/inbox return, focus, stale workspace response");
})().catch(e=>{console.error(e);process.exitCode=1;});
