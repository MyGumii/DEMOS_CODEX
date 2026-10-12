'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const linked=require('../notion_static/notion_linked_databases.js');
function fixture({rowEditable=false,cellEditable=true,searchEnabled=false}={}){
  let visible=true,editing=false,searchFocused=false,templateMenuOpen=false,denied=false,loads=0,renders=0,active=true,rowCreates=0,rowBinds=0;
  const button={addEventListener:()=>{}},rowButton={addEventListener:(_,callback)=>{rowButton.click=callback;}},body={querySelectorAll:()=>[]};
  const searchInput={matches:()=>searchFocused,addEventListener:()=>{}};
  const templateMenu={matches:()=>templateMenuOpen};
  const node={isConnected:true,innerHTML:'',classList:{toggle:()=>{}},replaceChildren(){this.innerHTML='';},querySelectorAll:()=>[],
    querySelector:selector=>selector==='[data-linked-body]'?body:selector==='[data-linked-search]'&&searchEnabled?searchInput:selector==='[data-linked-row-create]'&&rowEditable?rowButton:selector==='[data-linked-template-menu]'&&rowEditable?templateMenu:selector==='[data-linked-refresh]'||selector==='button'?button:null};
  const data={source:{document_id:'source',database_id:'db'},access:{document_sequence:1},
    database:{title:'Source',effective_role:'edit'},views:[],active_view_id:'local',sequence:1};
  const scope={},slot={id:'instance',node,block:{id:'instance',type:'table',props:{views:[{id:'local',name:'Local',type:'table',config:{}}],active_view_id:'local'}}};
  const host={current:()=>active,visible:()=>visible,canEdit:()=>false,canEditCells:()=>cellEditable,interacting:()=>editing,
    ...(searchEnabled?{searchValue:()=>'',searchChange:()=>{}}:{}),
    canCreateRows:()=>rowEditable,canCreateTemplates:(_scope,value)=>rowEditable&&value.database.effective_role==='edit',bindRows:()=>{rowBinds++;},createRow:async()=>{rowCreates++;},
    load:async()=>{loads++;if(denied)throw {status:404};return structuredClone(data);},project:()=>slot.block,render:()=>{renders++;return 'Current rows';}};
  return {view:linked.create(host),scope,slot,node,data,rowButton,rowCounts:()=>({rowCreates,rowBinds}),counts:()=>({loads,renders}),hide:()=>{visible=false;},show:()=>{visible=true;},edit:value=>{editing=value;},openTemplateMenu:value=>{templateMenuOpen=value;},focusSearch:value=>{searchFocused=value;},deny:()=>{denied=true;},allow:()=>{denied=false;},leave:()=>{active=false;}};
}
test('UI-LINKED-REFRESH identical reads and hidden tabs preserve the active cell DOM',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);const html=f.node.innerHTML;
  await f.view.refresh();assert.deepEqual(f.counts(),{loads:2,renders:1});
  f.hide();await f.view.refresh();assert.equal(f.node.innerHTML,html);assert.deepEqual(f.counts(),{loads:2,renders:1});
  f.show();f.edit(true);f.data.sequence++;await f.view.refresh();assert.equal(f.counts().renders,1);
  f.edit(false);await f.view.refresh();assert.equal(f.counts().renders,2);
});
test('UI-LINKED-REFRESH revoked access clears old values even while a cell has focus',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);f.edit(true);f.deny();await f.view.refresh();
  assert.ok(!f.node.innerHTML.includes('Current rows'));assert.match(f.node.innerHTML,/접근할 수 없거나/);
  f.leave();await f.view.refresh();assert.equal(f.node.innerHTML,'');
});
test('UI-LINKED-REFRESH recovering the same source version replaces the failed read screen',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);f.deny();await f.view.refresh();assert.equal(f.slot.data,null);
  f.allow();await f.view.refresh();assert.match(f.node.innerHTML,/Current rows/);assert.equal(f.counts().renders,2);
});
test('UI-LINKED-REFRESH a saved cell resolves the current slot after a page redraw',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);
  assert.equal(f.view.slot('instance'),f.slot);
  const replacement={...f.slot,node:{...f.node,isConnected:true}};
  await f.view.mount(f.scope,[replacement]);
  assert.equal(f.view.slot('instance'),replacement);
  assert.notEqual(f.view.slot('instance'),f.slot);
  f.leave();
  assert.equal(f.view.slot('instance'),null);
});
test('UI-LINKED-REFRESH row creation control follows source edit permission and current page',async()=>{
  const readonly=fixture();await readonly.view.mount(readonly.scope,[readonly.slot]);
  assert.doesNotMatch(readonly.node.innerHTML,/data-linked-row-create/);
  const writable=fixture({rowEditable:true});await writable.view.mount(writable.scope,[writable.slot]);
  assert.match(writable.node.innerHTML,/data-linked-row-create/);
  assert.equal(writable.rowCounts().rowBinds,1);
  writable.rowButton.click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(writable.rowCounts().rowCreates,1);
  writable.leave();writable.rowButton.click();await new Promise(resolve=>setImmediate(resolve));
  assert.equal(writable.rowCounts().rowCreates,1);
});
test('UI-LINKED-REFRESH reader search keeps its input during background refresh',async()=>{
  const f=fixture({cellEditable:false,searchEnabled:true});await f.view.mount(f.scope,[f.slot]);
  f.edit(true);f.focusSearch(true);f.data.sequence++;
  await f.view.refresh();assert.equal(f.counts().renders,1);
  f.focusSearch(false);f.edit(false);await f.view.refresh();
  assert.equal(f.counts().renders,2);
});
test('UI-LINKED-REFRESH forced refresh keeps the open template picker until action or access change',async()=>{
  const f=fixture({rowEditable:true});await f.view.mount(f.scope,[f.slot]);
  f.openTemplateMenu(true);f.data.sequence++;
  await f.view.refresh(true);assert.equal(f.counts().renders,1);
  f.data.access.document_sequence++;
  await f.view.refresh(true);assert.equal(f.counts().renders,2);
  f.data.database.effective_role='read';
  await f.view.refresh(true);assert.equal(f.counts().renders,3);
});
test('UI-LINKED-REFRESH identical forced reads preserve open relation and file menus',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);
  let relationClosed=0,fileClosed=0;
  f.slot.relationPickerOpen=true;
  f.slot.closeRelationPicker=()=>{relationClosed++;f.slot.relationPickerOpen=false;};
  await f.view.refresh(true);
  assert.equal(f.counts().renders,1);assert.equal(relationClosed,0);
  f.data.sequence++;
  await f.view.refresh(true);
  assert.equal(f.counts().renders,2);assert.equal(relationClosed,1);
  f.slot.closeFileMenu=()=>{fileClosed++;f.slot.closeFileMenu=null;};
  await f.view.refresh(true);
  assert.equal(f.counts().renders,2);assert.equal(fileClosed,0);
  f.data.access.document_sequence++;
  await f.view.refresh(true);
  assert.equal(f.counts().renders,3);assert.equal(fileClosed,1);
});
