"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const part = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const classes = new Set(), storage = new Map(), elements = new Map();
let narrow = true, focused = null, applied = 0;
const makeElement = () => ({attributes:{},hidden:false,disabled:false,inert:false,
  setAttribute(k,v) {this.attributes[k]=v;},removeAttribute(k) {delete this.attributes[k];},
  focus() {focused=this;},getClientRects() {return this.hidden ? [] : [{}];},
});
const get = selector => {if (!elements.has(selector)) elements.set(selector,makeElement()); return elements.get(selector);};
const sidebar = get(".sidebar"), first = get("#workspaceName"), last = get("#sidebarNewBtn");
sidebar.contains = element => [first,last].includes(element);
sidebar.querySelectorAll = () => [first,last];
const state = {user:{id:"alice"},navigation:{enabled:false,authoritative:false,workspaceId:""}};
let workspace = null;
const context = {$:get,State:state,selectedNavigationWorkspace:()=>workspace,
  document:{body:{classList:{toggle(c,on) {if(on) classes.add(c); else classes.delete(c);},contains:c=>classes.has(c)}},
    querySelectorAll:()=>[],querySelector:()=>null},
  cssEscape:value=>value,
  window:{matchMedia:()=>({matches:narrow})},
  localStorage:{getItem:k=>storage.get(k),setItem:(k,v)=>storage.set(k,v)},
  applyNavigationSections:()=>{applied+=1;},navigationControlsLocked:()=>false,scheduleNavigationPreferenceSave:()=>{},
};
vm.createContext(context);
vm.runInContext([
  part("function setSidebarCollapsed(", "function loadSidebarCollapsed("),
  part("let MobileSidebarOpen =", "function bindSidebarResize("),
  part("function sidebarCapabilities(", "function workspaceInitial("),
  part("function localSidebarSections(", "function renderPageLists("),
  part("function toggleNavigationSection(", "function renderWorkspacePopover("),
].join("\n"),context);
context.setMobileSidebarOpen(true);
assert.equal(sidebar.inert,false);
assert.equal(get("#workspaceMain").inert,true);
assert.equal(sidebar.attributes["aria-modal"],"true");
assert.equal(focused,first);
const key = (target, value, shift=false) => ({target,key:value,shiftKey:shift,preventDefault(){this.prevented=true;}});
const forward = key(last,"Tab"); context.trapMobileSidebarFocus(forward);
assert.equal(forward.prevented,true); assert.equal(focused,first);
const backward = key(first,"Tab",true); context.trapMobileSidebarFocus(backward);
assert.equal(backward.prevented,true); assert.equal(focused,last);
const popup = makeElement();
assert.equal(context.trapMobileSidebarFocus(key(popup,"Escape")),false,"separate popups own their Escape key");
context.trapMobileSidebarFocus(key(first,"Escape"));
assert.equal(get("#workspaceMain").inert,false);
assert.equal(focused,get("#sidebarRestoreBtn"));
assert.equal(classes.has("mobile-sidebar-open"),false);
assert.equal(sidebar.inert,true,"closed mobile navigation must leave the keyboard order");
narrow=false; context.setMobileSidebarOpen(true);
assert.equal(sidebar.inert,false);
assert.equal(classes.has("mobile-sidebar-open"),false);
assert.equal(storage.size,0,"temporary mobile opening must not rewrite desktop preferences");
context.setSidebarCollapsed(true,false);
assert.equal(sidebar.inert,true);assert.equal(sidebar.attributes["aria-hidden"],"true");
context.setSidebarCollapsed(false,false);
assert.equal(sidebar.inert,false);assert.equal(sidebar.attributes["aria-hidden"],undefined);
context.setSidebarCollapsed(true,false);narrow=true;context.setMobileSidebarOpen(true);
assert.equal(sidebar.inert,false,"mobile opening overrides the retained desktop collapsed preference");
context.setMobileSidebarOpen(false);narrow=false;context.syncSidebarAccessibility();
assert.equal(sidebar.inert,true,"returning to desktop restores the saved collapsed state");

context.toggleNavigationSection("private");
assert.equal(context.localSidebarSections().find(s=>s.section==="private").collapsed,true);
assert.equal(context.localSidebarSections().find(s=>s.section==="recents").hidden,false);
assert.equal(context.localSidebarSections().find(s=>s.section==="recents").collapsed,false);
storage.set("notion.sidebarSections.alice.local.layout",JSON.stringify({recents:{hidden:true}}));
assert.equal(context.localSidebarSections().find(s=>s.section==="recents").hidden,true,"saved user visibility remains authoritative");
storage.delete("notion.sidebarSections.alice.local.layout");
assert.equal(applied,1);
state.user.id="bob";
assert.equal(context.localSidebarSections().find(s=>s.section==="private").collapsed,false);
state.user.id="alice"; state.navigation.workspaceId="different";
assert.equal(context.localSidebarSections().find(s=>s.section==="private").collapsed,false);
state.navigation.enabled=true; state.navigation.authoritative=true;
for (const role of ["owner","member","restricted_member","guest"]) {
  workspace={access:{role}};
  assert.equal(context.sidebarCapabilities().canCreate,role==="owner",`creation contract for ${role}`);
}
workspace={access:{role:"owner"}};
state.navigation.collaborativeSharing=true;
assert.equal(context.sidebarCapabilities().canCreate,false,"an older collaborative server must not create through the legacy path");
assert.equal(context.sidebarCapabilities().canManagePages,true,"creation availability does not disable existing page management");
state.navigation.collaborativePageCreation=true;
assert.equal(context.sidebarCapabilities().canCreate,true);
console.log("UI-SHELL-004: mobile focus/escape, account-workspace section preferences and creation capabilities passed");

let renderedRecent = [];
context.renderPageGroup = (node,pages) => {renderedRecent = Array.from(pages, page => page.id);};
const recent = Array.from({length:7},(_,i)=>({id:`recent-${i}`}));
state.user.id = "alice"; state.navigation.workspaceId = "recent-a"; state.workspaceOwnerFilter = "";
context.renderRecentPageGroup(recent);
const recentToggle = get("#recentPagesToggle");
assert.deepEqual(renderedRecent,["recent-0","recent-1","recent-2"]);
assert.equal(recentToggle.hidden,false);
recentToggle.onclick();
assert.equal(renderedRecent.length,7);
assert.equal(recentToggle.attributes["aria-expanded"],"true");
assert.equal(focused,recentToggle);
recentToggle.onclick();
assert.equal(renderedRecent.length,3);
context.renderRecentPageGroup(recent.slice(0,2));
assert.equal(recentToggle.hidden,true);
context.renderRecentPageGroup(recent);
const staleClick = recentToggle.onclick;
state.navigation.workspaceId = "recent-b";
renderedRecent = ["new-workspace"];
staleClick();
assert.deepEqual(renderedRecent,["new-workspace"],"an old toggle cannot redraw another workspace");
context.renderRecentPageGroup(recent);
assert.equal(renderedRecent.length,3,"expansion stays within the original workspace");
recentToggle.onclick();
state.user.id = "bob"; context.renderRecentPageGroup(recent);
assert.equal(renderedRecent.length,3,"expansion stays within the original account");
const previousAccountClick = recentToggle.onclick;
state.workspaceOwnerFilter = "someone-else"; renderedRecent = ["filtered"];
previousAccountClick();
assert.deepEqual(renderedRecent,["filtered"],"an old toggle cannot redraw a different owner filter");
context.renderRecentPageGroup(recent);
context.navigationControlsLocked = () => true;
recentToggle.onclick();
assert.equal(renderedRecent.length,3,"pending navigation prevents stale list actions");
console.log("UI-SHELL-004: recent expansion, short lists, focus and stale scope actions passed");

state.user.id="alice";state.navigation.workspaceId="recent-a";state.navigation.enabled=false;
state.pages=[{id:"older",updated_at:10},{id:"newer",updated_at:20}];
assert.deepEqual(Array.from(context.recentPageIds()),["newer","older"],"existing users keep their prior ordering until a visit");
context.rememberRecentPageVisit("older");
assert.deepEqual(Array.from(context.recentPageIds()),["older","newer"],"opening a page moves it to the front");
context.rememberRecentPageVisit("older");
assert.deepEqual(Array.from(context.recentPageIds()),["older","newer"],"reopening does not duplicate the entry");
let recentRenderCount=0, statusMessage="";
context.renderPageLists=()=>{recentRenderCount++;};
context.setStatus=message=>{statusMessage=message;};
context.removeRecentPage("older");
assert.deepEqual(Array.from(context.recentPageIds()),["newer"],"remove hides only the recent entry");
assert.equal(state.pages.length,2,"removing from recents must not delete a page");
assert.equal(recentRenderCount,1);assert.match(statusMessage,/최근 항목/);
assert.equal(focused,get("#workspaceHomeBtn"),"removing a recent item restores focus to an available navigation target");
state.navigation.workspaceId="recent-b";
assert.deepEqual(Array.from(context.recentPageIds()),["newer","older"],"recent history stays within its workspace");
state.user.id="bob";state.navigation.workspaceId="recent-a";
assert.deepEqual(Array.from(context.recentPageIds()),["newer","older"],"recent history stays within its account");
state.user.id="alice";
assert.deepEqual(Array.from(context.recentPageIds()),["newer"]);
console.log("UI-SHELL-004: visit order, removal and account/workspace isolation passed");
