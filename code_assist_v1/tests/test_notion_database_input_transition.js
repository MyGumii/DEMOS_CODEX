"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync(require.resolve("../notion_static/notion.js"),"utf8");
const section=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));

function fixture(){
  const handlers={},timers=new Map();let next=0,renders=0,editable=true;
  const cell={disabled:false,dataset:{dbRow:"0",dbField:"name"},value:"edited",matches:()=>true};
  const root={contains:node=>node===cell};
  const document={activeElement:cell,addEventListener:(name,fn)=>handlers[name]=fn};
  const block={id:"db",props:{rows:[{_canonical_row_id:"a",name:"old"},{_canonical_row_id:"b",name:"other"}],properties:[{id:"name",type:"title"}]}};
  const State={user:{id:"owner"},navigation:{workspaceId:"ws"},activePageId:"page",pageLoadSequence:1,pageData:{blocks:[block]}};
  const context={document,State,window:{addEventListener:(name,fn)=>handlers[name]=fn},$:()=>root,
    databaseRows:b=>b.props.rows,databaseProperties:b=>b.props.properties,activePageCanEdit:()=>editable,
    setTimeout:fn=>{timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)};
  vm.createContext(context);
  vm.runInContext(section("function databaseControlFocusScope()", "function captureDatabaseControlFocus()"),context);
  context.renderBlocks=()=>{if(!context.deferDatabaseInputRender(root))renders++;};
  context.currentDatabaseInputRow(block,cell);
  context.bindDatabaseInputTransitions();
  return {context,State,cell,block,document,handlers,root,renders:()=>renders,editable:value=>editable=value,
    drain(){const pending=[...timers.values()];timers.clear();pending.forEach(fn=>fn());}};
}

test("UI-DB-INPUT typing retains the native cell until focus leaves",()=>{
  const f=fixture();f.context.renderBlocks();assert.equal(f.renders(),0);
  f.document.activeElement={};f.handlers.focusout({target:f.cell});f.drain();assert.equal(f.renders(),1);
});

test("UI-DB-INPUT a held pointer keeps the destination alive through change, save response and click",()=>{
  const f=fixture();f.handlers.pointerdown({button:0,pointerId:7});
  f.document.activeElement={};f.context.renderBlocks();f.handlers.focusout({target:f.cell});f.drain();
  f.context.renderBlocks();assert.equal(f.renders(),0);
  f.handlers.pointerup({pointerId:8});f.drain();assert.equal(f.renders(),0);
  f.handlers.pointerup({pointerId:7});f.context.renderBlocks();assert.equal(f.renders(),0);
  // Browser click has now finished; exactly one deferred render follows it.
  f.drain();assert.equal(f.renders(),1);
});

test("UI-DB-INPUT keyboard Tab into another cell keeps that input mounted",()=>{
  const f=fixture();f.context.renderBlocks();f.handlers.focusout({target:f.cell});f.drain();assert.equal(f.renders(),0);
  f.document.activeElement={};f.handlers.focusout({target:f.cell});f.drain();assert.equal(f.renders(),1);
});

test("UI-DB-INPUT cancellation and window blur release a held interaction",()=>{
  for(const event of ["pointercancel","blur"]){
    const f=fixture();f.handlers.pointerdown({button:0,pointerId:7});f.document.activeElement={};f.context.renderBlocks();
    f.handlers[event]({pointerId:7});f.drain();assert.equal(f.renders(),1);
  }
});

test("UI-DB-INPUT delayed render cannot redraw another page or permission context",()=>{
  for(const change of [f=>f.State.activePageId="other",f=>f.State.user.id="other",f=>f.State.navigation.workspaceId="other",f=>f.State.pageLoadSequence++]){
    const f=fixture();f.context.renderBlocks();f.handlers.focusout({target:f.cell});change(f);f.document.activeElement={};f.drain();assert.equal(f.renders(),0);
  }
  const f=fixture();f.editable(false);f.context.renderBlocks();assert.equal(f.renders(),1);
  const navigated=fixture();navigated.State.activePageId="other";navigated.context.renderBlocks();
  assert.equal(navigated.renders(),1,"an old focused DOM input must not block the new page");
  const removed=fixture();removed.State.pageData.blocks=[];removed.context.renderBlocks();assert.equal(removed.renders(),1);
  const replaced=fixture();replaced.State.pageData.blocks[0].props.database_id="different-source";
  replaced.context.renderBlocks();assert.equal(replaced.renders(),1);
});

test("UI-DB-INPUT change resolves the same row after the projection is replaced and reordered",()=>{
  const f=fixture(),resolve=f.context.currentDatabaseInputRow(f.block,f.cell);
  const current=structuredClone(f.block);current.props.rows.reverse();f.State.pageData.blocks=[current];
  const result=resolve();assert.equal(result.item._canonical_row_id,"a");assert.equal(result.block,current);
  result.item.name="edited";assert.equal(current.props.rows[1].name,"edited");assert.equal(f.block.props.rows[0].name,"old");
});

test("UI-DB-INPUT removed rows, revoked editing and changed pages reject stale handlers",()=>{
  for(const change of [f=>f.State.pageData.blocks[0].props.rows.shift(),f=>f.editable(false),f=>f.cell.disabled=true,f=>f.State.activePageId="other",f=>f.State.pageLoadSequence++,f=>f.State.pageData.blocks[0].props.database_id="different-source"]){
    const f=fixture(),resolve=f.context.currentDatabaseInputRow(f.block,f.cell);change(f);assert.equal(resolve(),null);
  }
});

test("UI-DB-INPUT actual cell change listener commits to the current row",async()=>{
  const f=fixture(),handlers={};f.cell.addEventListener=(name,fn)=>handlers[name]=fn;
  Object.assign(f.context,{canEditDatabaseSchema:()=>true,markDirty(){},captureEditorHistory(){},collaborativeDatabaseActive:()=>true});
  f.context.row={querySelectorAll:()=>[f.cell]};f.context.block=f.block;
  vm.runInContext(section('  row.querySelectorAll("[data-db-row][data-db-field]").forEach(input => {','  row.querySelectorAll("[data-db-row][data-db-date-end]")'),f.context);
  const current=structuredClone(f.block);current.props.rows.reverse();f.State.pageData.blocks=[current];
  await handlers.change();assert.equal(current.props.rows[1].name,"edited");assert.equal(current.props.rows[0].name,"other");
  assert.equal(f.renders(),0);assert.equal(f.block.props.rows[0].name,"old");
});
