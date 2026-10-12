"use strict";
// UI-MOVE-005: all drop modes use the menu's ACK/retry/reconnect path.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../notion_static/notion.js'),'utf8');
const listeners=()=>({handlers:{},dataset:{},classList:{add(){},remove(){}},
  addEventListener(name,callback){this.handlers[name]=callback;},querySelector(){return null;},contains(){return false;}});
const moved={id:'source',scope:'shared',can_move:true,can_edit:true},target={id:'target',scope:'shared',can_move:false,can_edit:true};
const state={pages:[moved,target],navigation:{collaborativeSharing:true},pageDrag:{id:'source',mode:'after',targetId:'target'}};
const calls=[],ctx={State:state,Array,console,setTimeout:callback=>callback(),
  pageIsLocked:page=>Boolean(page.settings?.locked),isInvalidPageDrop:()=>false,
  clearPageDropIndicators(){},markPageDrop(){},setStatus(){},pageDropMode:()=>state.mode||'after',
  openMovePageDialog:(page,anchor,body)=>calls.push({id:page.id,anchor,body}),
  selectedNavigationWorkspace:()=>({access:{role:state.role||'owner'}}),$:()=>null};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('function pageCanMove('),source.indexOf('function clearPageDropIndicators(')),ctx);
vm.runInContext(source.slice(source.indexOf('function bindPageDrag('),source.indexOf('function closePageMenu(')),ctx);
const row=listeners();ctx.bindPageDrag(row,target,'teamspacePageTree');
const event=()=>({target:{closest:()=>null},preventDefault(){this.prevented=true;},stopPropagation(){},dataTransfer:{getData:()=>'',setData(){}}});
const over=event();row.handlers.dragover(over);
assert.equal(over.prevented,true);
assert.equal(over.dataTransfer.dropEffect,'move','an accepted target explicitly advertises the native move operation');
for(const [mode,body] of [['before',{before_id:'target'}],['after',{after_id:'target'}],['inside',{parent_id:'target'}]]) {
  state.pageDrag={id:'source',mode,targetId:'target'};
  row.handlers.drop(event());
  assert.deepEqual(JSON.parse(JSON.stringify(calls.pop().body)),body);
  assert.equal(state.pageDrag.id,'');
}
assert.equal(ctx.pageCanMove(moved),true,'a shared page with full access can be moved');
moved.can_move=false;state.pageDrag={id:'source'};row.handlers.drop(event());
assert.equal(calls.length,0,'edit permission alone cannot start moving a collaborative source');
moved.can_move=true;target.can_edit=false;state.pageDrag={id:'source',mode:'inside',targetId:'target'};
row.handlers.drop(event());assert.equal(calls.length,0,'read-only destinations cannot receive a child');
state.pageDrag={id:'source',mode:'before',targetId:'target'};
row.handlers.drop(event());assert.equal(calls.pop().body.before_id,'target','relative order is authorized against the common parent by the server');
const root={...listeners(),id:'teamspacePageTree',querySelectorAll:()=>[]};
ctx.bindPageListRootDrop(root);state.pageDrag={id:'source'};
root.handlers.drop(event());assert.equal(calls.pop().body.parent_id,'');
state.role='guest';state.pageDrag={id:'source'};root.handlers.drop(event());
assert.equal(calls.length,0,'guest root drops are blocked before requesting a mutation');
vm.runInContext(source.slice(source.indexOf('function descendantPageIds('),source.indexOf('function bindPageDrag(')),ctx);
state.pages.push({id:'shared-child',parent_id:'source',scope:'shared'});
assert.equal(ctx.isInvalidPageDrop('source','shared-child'),true,'descendant exclusion includes accessible shared pages');
console.log('UI-MOVE-005: before/after/inside/root drops share the saved-move workflow and permission checks');
