"use strict";
const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../notion_static/notion.js'), 'utf8');
const implementation = source.slice(source.indexOf('function restorePageListFocus('), source.indexOf('function directDatabaseSidebarRow('));
let focused = null;
function row(id, {compact = false, visible = true, controls = {}, dataset = {}} = {}) {
  return {dataset: {pageId: id, ...dataset}, classList: {contains: name => name === 'compact' && compact},
    getClientRects: () => visible ? [{}] : [], querySelector: selector => controls[selector] || null,
    focus: () => { focused = id; }};
}
let local = [], rendered = [], rootExists = true;
const context = {document: {
  getElementById: () => rootExists ? {querySelectorAll: () => local} : null,
  querySelectorAll: () => rendered,
}};
vm.createContext(context); vm.runInContext(implementation, context);
const snapshot = {rootId: 'teamPages', id: 'page-a', control: 'data-page-child', index: 0};
local = rendered = [row('page-a')];
context.restorePageListFocus(snapshot);
assert.equal(focused, 'page-a', 'removing the focused add button retains focus on its accessible page');
const more = {getClientRects: () => [{}], focus: () => { focused = 'page-a-more'; }};
local = rendered = [row('page-a', {controls: {'[data-page-menu]': more}})];
context.restorePageListFocus({...snapshot, control: 'data-page-menu'});
assert.equal(focused, 'page-a-more', 'a surviving action keeps its own focus');
rootExists = false; local = [];
rendered = [row('page-a-recent', {compact: true, dataset: {pageId: 'page-a'}}), row('page-a-shared', {dataset: {pageId: 'page-a'}})];
context.restorePageListFocus(snapshot);
assert.equal(focused, 'page-a-shared', 'moving sections prefers the visible full page row over a recent duplicate');
rootExists = true; local = [row('page-a', {visible: false})];
context.restorePageListFocus(snapshot);
assert.equal(focused, 'page-a-shared', 'hidden tree rows do not swallow the restored focus');
rendered = []; focused = 'outside';
context.restorePageListFocus(snapshot);
assert.equal(focused, 'outside', 'an inaccessible page cannot revive a stale row or move focus to another page');
local = rendered = [row('db-row', {dataset: {sidebarDbParent: 'parent', sidebarDbBlock: 'table', sidebarDbView: ''}})];
context.restorePageListFocus({rootId: 'sharedPages', database: true, parentId: 'parent', blockId: 'table', viewId: '', control: 'data-sidebar-db-add'});
assert.equal(focused, 'db-row', 'a removed database add action falls back to the same table row');
rootExists = false; local = []; rendered = [row('direct-db', {dataset: {directDbId: 'database-a'}})];
context.restorePageListFocus({rootId: 'favorites', directDatabaseId: 'database-a'});
assert.equal(focused, 'direct-db', 'direct database focus survives a move to an accessible section');
console.log('UI-FOCUS-011: surviving controls, removed add actions, section moves and inaccessible/hidden rows preserve valid sidebar focus');

const dragImplementation = source.slice(source.indexOf('function bindPageDrag('), source.indexOf('function bindPageListRootDrop('));
function dragFixture() {
  const state = {user:{id:'owner'}, navigation:{workspaceId:'workspace-a', collaborativeSharing:true},
    workspaceOwnerFilter:'owner', activePageId:'open-page', pageDrag:{id:'',mode:'',targetId:''},
    pages:[{id:'parent',can_edit:true},{id:'child',can_edit:true}]};
  const restored = [], dialogs = [];
  const c = {State:state, pageCanMove:p=>p?.can_edit === true, pageIsLocked:()=>false,
    pageListFocusSnapshot:()=>({rootId:'ownPages',id:'parent',index:0,control:''}),
    restorePageListFocus:snapshot=>restored.push(snapshot), navigationControlsLocked:()=>false,
    clearPageDropIndicators:()=>{}, isInvalidPageDrop:(from,to)=>from==='parent'&&to==='child',
    pageDropMode:()=> 'inside', markPageDrop:()=>{}, setStatus:()=>{},
    openMovePageDialog:(page,anchor,body)=>dialogs.push({id:page.id,body}), setTimeout:fn=>fn()};
  vm.createContext(c); vm.runInContext(dragImplementation,c);
  function bind(id) {
    const handlers = {}, node = {addEventListener:(name,handler)=>handlers[name]=handler,
      classList:{add:()=>{},remove:()=>{}},contains:()=>false,querySelector:()=>null};
    c.bindPageDrag(node,state.pages.find(page=>page.id===id)); return handlers;
  }
  const parent=bind('parent'),child=bind('child');
  const event={dataTransfer:{setData:()=>{},getData:()=>state.pageDrag.id},
    preventDefault:()=>{},stopPropagation:()=>{}};
  return {state,c,parent,child,event,restored,dialogs};
}
{
  const f=dragFixture(); f.parent.dragstart(f.event); f.child.dragover(f.event);
  assert.equal(f.event.dataTransfer.dropEffect,'none');
  f.parent.dragend();
  assert.equal(f.restored.length,1,'a prohibited descendant drop returns focus to its source');
  assert.equal(f.restored[0].id,'parent'); assert.equal(f.dialogs.length,0);
  f.parent.dragend(); assert.equal(f.restored.length,1,'duplicate dragend cannot reuse old focus ownership');
}
{
  const f=dragFixture(); f.child.dragstart(f.event); f.parent.drop(f.event); f.child.dragend();
  assert.equal(f.dialogs.length,1); assert.equal(f.dialogs[0].id,'child');
  assert.equal(f.restored.length,0,'a successful drop leaves focus with the move dialog');
}
for (const change of [f=>f.state.user.id='other',f=>f.state.navigation.workspaceId='workspace-b',
    f=>f.state.workspaceOwnerFilter='other',f=>f.state.activePageId='another-page',
    f=>f.c.navigationControlsLocked=()=>true]) {
  const f=dragFixture(); f.parent.dragstart(f.event); change(f); f.parent.dragend();
  assert.equal(f.restored.length,0,'cancelled drag cannot reclaim focus in another or locked scope');
}
console.log('UI-FOCUS-012: cancelled drag source focus, successful dialog ownership and stale scope guards passed');
