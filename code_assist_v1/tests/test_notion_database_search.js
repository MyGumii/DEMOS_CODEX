"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync(require.resolve("../notion_static/notion.js"),"utf8");
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));

function fixture(){
  const block={id:"db-block",type:"table",props:{database_id:"db",query:"another user's old query"}};
  const State={user:{id:"owner"},navigation:{workspaceId:"ws"},activePageId:"page",pageLoadSequence:1,pageData:{blocks:[block]}};
  const callbacks={},jobs=new Map();let n=0,gridBinds=0,resultBinds=0;
  function node(kind,parent){
    const value={kind,parent,nodeType:1,matches:()=>kind==="head"||kind==="toolbar",remove(){this.parent.childNodes=this.parent.childNodes.filter(x=>x!==this);}};
    return value;
  }
  const current={childNodes:[],append(child){child.remove();child.parent=current;current.childNodes.push(child);}};
  const head=node("head",current),toolbar=node("toolbar",current),oldResult=node("old-result",current);
  current.childNodes=[head,toolbar,oldResult];
  const next={childNodes:[]};const newResult=node("new-result",next);next.childNodes=[node("head",next),node("toolbar",next),newResult];
  const input={value:"",disabled:false,matches:()=>true,hasAttribute:()=>true,addEventListener:(type,fn)=>callbacks[type]=fn};
  const row={dataset:{blockId:block.id},contains:target=>target===input,querySelector:selector=>selector===".database-block"?current:input};
  const document={activeElement:input,querySelectorAll:()=>[row],createElement:()=>({content:{querySelector:()=>next}})};
  const context={State,document,window:{NotionLinkedDatabases:{reference:b=>b.props?.linked_database}},DATABASE_BLOCK_TYPES:new Set(["table","board"]),
    activePageCanEdit:()=>true,blockEditorHtml:()=>"<div></div>",databaseRows:b=>b.props.rows||[],
    bindBlockGrid:()=>gridBinds++,bindDatabaseResults:()=>resultBinds++,setTimeout:fn=>{jobs.set(++n,fn);return n;},clearTimeout:id=>jobs.delete(id)};
  vm.createContext(context);
  vm.runInContext(part("function databaseControlFocusScope()","function captureDatabaseControlFocus()"),context);
  vm.runInContext(part("function renderDatabaseSearchResults(","function bindDatabaseResults("),context);
  vm.runInContext('function bindSearch(row,block){'+part('  const dbSearch = row.querySelector("[data-db-search]");','  row.querySelector("[data-db-filter]")')+'}',context);
  return {context,State,block,input,row,document,current,head,toolbar,oldResult,newResult,callbacks,
    binds:()=>[gridBinds,resultBinds],drain(){const pending=[...jobs.values()];jobs.clear();pending.forEach(fn=>fn());}};
}

test("UI-DB-SEARCH query is local UI state and never changes document props",()=>{
  const f=fixture(),before=JSON.stringify(f.block);
  assert.equal(f.context.databaseSearchQuery(f.block),"");
  f.context.databaseSearchQuery(f.block,"두 번째");
  assert.equal(f.context.databaseSearchQuery(f.block),"두 번째");assert.equal(JSON.stringify(f.block),before);
});

test("UI-DB-SEARCH remote replacement and view changes retain the local query",()=>{
  const f=fixture();f.context.databaseSearchQuery(f.block,"local");
  const replacement=structuredClone(f.block);replacement.props.query="remote";replacement.type="board";
  f.State.pageData.blocks=[replacement];assert.equal(f.context.databaseSearchQuery(replacement),"local");
  f.context.databaseSearchQuery(replacement,"");assert.equal(f.context.databaseSearchQuery(replacement),"");
});

test("UI-DB-SEARCH account workspace page and load boundaries clear the query",()=>{
  for(const change of [f=>f.State.user.id="other",f=>f.State.navigation.workspaceId="other",f=>f.State.activePageId="other",f=>f.State.pageLoadSequence++]){
    const f=fixture();f.context.databaseSearchQuery(f.block,"local");change(f);assert.equal(f.context.databaseSearchQuery(f.block),"");
  }
});

test("UI-DB-SEARCH instances and changed database sources do not share queries",()=>{
  const f=fixture();f.context.databaseSearchQuery(f.block,"local");
  for(const change of [b=>b.id="other",b=>b.props.database_id="other",b=>b.props.linked_database={document_id:"other",database_id:"db"}]){
    const other=structuredClone(f.block);change(other);assert.equal(f.context.databaseSearchQuery(other),"");
  }
});

test("UI-DB-SEARCH results refresh leaves the actual toolbar and focused input mounted",()=>{
  const f=fixture();assert.equal(f.context.renderDatabaseSearchResults(f.block.id),true);
  assert.deepEqual(f.current.childNodes,[f.head,f.toolbar,f.newResult]);assert.equal(f.document.activeElement,f.input);
  assert.deepEqual(f.binds(),[1,1]);assert.equal(f.current.childNodes.includes(f.oldResult),false);
});

test("UI-DB-SEARCH missing and linked targets cannot replace another result surface",()=>{
  const f=fixture();assert.equal(f.context.renderDatabaseSearchResults("other"),false);
  f.block.props.linked_database={document_id:"source",database_id:"db"};assert.equal(f.context.renderDatabaseSearchResults(f.block.id),false);
  assert.deepEqual(f.binds(),[0,0]);assert.deepEqual(f.current.childNodes,[f.head,f.toolbar,f.oldResult]);
});

test("UI-DB-SEARCH input debounce uses current identity and refreshes results without full rendering",()=>{
  const f=fixture();f.context.bindSearch(f.row,f.block);f.State.pageData.blocks=[structuredClone(f.block)];
  f.input.value="typed";f.callbacks.input();f.drain();
  assert.equal(f.context.databaseSearchQuery(f.State.pageData.blocks[0]),"typed");assert.deepEqual(f.binds(),[1,1]);
  assert.equal(f.context.deferDatabaseInputRender({contains:()=>true}),true,"unrelated redraw must retain the active search input");
});

test("UI-DB-SEARCH stale input events and timers cannot affect a new page",()=>{
  const f=fixture();f.context.bindSearch(f.row,f.block);f.input.value="typed";f.callbacks.input();
  f.State.pageLoadSequence++;f.drain();f.input.value="stale";f.callbacks.input();f.drain();
  assert.deepEqual(f.binds(),[0,0]);assert.equal(f.context.databaseSearchQuery(f.block),"");
});

test("UI-DB-SEARCH a delayed result cannot replace a cell the user has started editing",()=>{
  const f=fixture();f.context.bindSearch(f.row,f.block);f.input.value="typed";f.callbacks.input();
  f.input.hasAttribute=()=>false;f.drain();assert.deepEqual(f.binds(),[0,0]);
  f.document.activeElement={};assert.equal(f.context.renderDatabaseSearchResults(f.block.id),true);
  assert.deepEqual(f.binds(),[1,1]);
});

test("UI-DB-SEARCH a delayed result cannot remove a pointer's destination before click",()=>{
  const f=fixture();vm.runInContext('DatabaseInputTransition.pointer={scope:databaseControlFocusScope()}',f.context);
  assert.equal(f.context.renderDatabaseSearchResults(f.block.id),false);assert.deepEqual(f.binds(),[0,0]);
  vm.runInContext('DatabaseInputTransition.pointer=null',f.context);
  assert.equal(f.context.renderDatabaseSearchResults(f.block.id),true);
});

test("UI-DB-SEARCH typing in a second database does not cancel the first database's result update",()=>{
  const f=fixture(),second=structuredClone(f.block),callbacks={},updated=[];
  second.id="second-block";second.props.database_id="second-db";f.State.pageData.blocks.push(second);
  const input={value:"second",addEventListener:(type,fn)=>callbacks[type]=fn};
  f.context.renderDatabaseSearchResults=id=>updated.push(id);
  f.context.bindSearch(f.row,f.block);f.context.bindSearch({querySelector:()=>input},second);
  f.input.value="first";f.callbacks.input();callbacks.input();f.drain();
  assert.deepEqual(updated,[f.block.id,second.id]);
  assert.equal(f.context.databaseSearchQuery(f.block),"first");assert.equal(f.context.databaseSearchQuery(second),"second");
});
