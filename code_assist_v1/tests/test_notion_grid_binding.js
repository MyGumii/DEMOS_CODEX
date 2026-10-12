const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const gridApi=require('../notion_static/notion_grid_selection');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const code=source.slice(source.indexOf('function gridRowKey('),source.indexOf('function attachBlockExtras('));
let adapter, editable=true, shared=false, saves=0;
const properties=[{id:'score',type:'number'},{id:'text',type:'text'}];
const block={type:'table',id:'db',props:{rows:[{score:1,text:'a'},{score:2,text:'b'}],properties}};
const grid={querySelectorAll:()=>[]};
const context={window:{NotionLinkedDatabases:require('../notion_static/notion_linked_databases'),NotionGridSelection:{...gridApi,bind:(node,host)=>adapter=host}},State:{pageData:{blocks:[block]},users:[],user:{id:'owner'}},
 databaseRows:b=>b.props.rows,databaseProperties:b=>b.props.properties,activePageCanEdit:()=>editable,collaborativeDatabaseActive:()=>shared,
 clearBlockSelection:()=>{},canEditDatabaseSchema:()=>true,captureEditorHistory:()=>{},markDirty:()=>saves++,renderBlocks:()=>{},
 commitDatabaseGridRange:(block,changes)=>{context.sentRange=changes;},
 document:{querySelector:()=>null},CSS:{escape:x=>x},setStatus:()=>{},databaseDisplayValue:String};
vm.createContext(context);vm.runInContext(code+'\nbindBlockGrid(testRow,testBlock)',Object.assign(context,{testRow:{querySelector:()=>grid},testBlock:block}));
// A successful auto-save replaces row objects without rebuilding bound DOM.
block.props.rows=structuredClone(block.props.rows);
adapter.change([{row:'db:row:0',column:'score',value:'100'},{row:'db:row:1',column:'score',value:'200'}]);
assert.deepEqual(block.props.rows.map(r=>r.score),[100,200]);assert.equal(saves,1);
assert.throws(()=>adapter.change([{row:'db:row:0',column:'score',value:'300'},{row:'db:row:1',column:'score',value:'oops'}]));
assert.deepEqual(block.props.rows.map(r=>r.score),[100,200]);assert.equal(saves,1);
editable=false;assert.throws(()=>adapter.change([{row:'db:row:0',column:'score',value:'400'}]));assert.equal(saves,1);
editable=true;shared=true;assert.throws(()=>adapter.change([{row:'db:row:0',column:'score',value:'400'},{row:'db:row:1',column:'score',value:'500'}]));assert.equal(saves,1);
context.State.pageData.databases=[{id:'db',capabilities:{atomic_cell_batch:true,max_cell_batch:1000}}];
adapter.change([{row:'db:row:0',column:'score',value:'400'},{row:'db:row:1',column:'score',value:'500'}]);
assert.deepEqual(block.props.rows.map(r=>r.score),[100,200]);assert.equal(saves,1);
assert.deepEqual(Array.from(context.sentRange,item=>item.value),[400,500]);
console.log('Grid binding survives auto-save row replacement; invalid/readonly/unsupported batches leave all rows unchanged');
const propertyCode=source.slice(source.indexOf('function databaseProperties('),source.indexOf('function databasePropertyId('));
vm.runInContext(propertyCode,context);
const canonical={props:{database_id:'server-db',_canonical_version:1,properties:[{id:'score',type:'number'}]}};
assert.equal(context.databaseProperties(canonical).length,1);
assert.equal(canonical.props.properties.length,1);
// Capture-phase selection must allow a row opener and links to reach their
// own click handlers; otherwise details can never open from a table.
const listeners={};
gridApi.bind({dataset:{},setAttribute:()=>{},querySelectorAll:()=>[],addEventListener:(name,handler)=>listeners[name]=handler},{});
for(const tag of ['button','a']){
  let prevented=false,stopped=false;
  listeners.click({target:{closest:selector=>selector==='button,a'?{tagName:tag}:{}},preventDefault:()=>prevented=true,stopPropagation:()=>stopped=true});
  assert.equal(prevented,false);assert.equal(stopped,false);
}
let cellPrevented=false;
listeners.click({target:{closest:selector=>selector==='[data-grid-row]'?{}:null},preventDefault:()=>cellPrevented=true,stopPropagation:()=>{}});
assert.equal(cellPrevented,true);
