"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync(require.resolve("../notion_static/notion.js"),"utf8");
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
function fixture(viewId=""){
  const block={id:"block",type:"table",props:{database_id:"db",view:"table",active_view_id:"a",properties:[{id:"name",type:"title"}],views:[{id:"a",name:"A",type:"table"},{id:"b",name:"B",type:"board"}]}};
  const State={activePageId:"page",user:{id:"owner"},navigation:{workspaceId:"ws"},pageLoadSequence:1,pageData:{blocks:[block]}};
  let buttons,closed=0,dirty=0,editable=true,schema=true;const errors=[],shortcuts=[];
  const context={State,EditorCore:require("../notion_static/notion_editor_core.js"),DATABASE_BLOCK_TYPES:new Set(["table","board","list"]),
    window:{NotionLinkedDatabases:{reference:b=>b.props?.linked_database}},canEditDatabaseSchema:()=>schema,activePageCanEdit:()=>editable,
    databaseControlFocusScope:()=>JSON.stringify([State.activePageId,State.user.id,State.navigation.workspaceId,State.pageLoadSequence]),
    databaseProperties:b=>b.props.properties,escapeAttr:x=>x,openModal:(title,html,actions)=>{buttons=actions;},
    closeModal:()=>closed++,markDirty:()=>dirty++,captureEditorHistory(){},renderBlocks(){},setStatus:(...args)=>errors.push(args),
    $:selector=>selector==="#dbViewType"?{value:"list"}:selector==="#dbViewName"?{value:"Saved"}:{append(){}},
    document:{querySelector:()=>({}),createElement:()=>({append(){},addEventListener(type,fn){this[type]=fn;shortcuts.push(this);}})},
    anchorCurrentModal(){},setTimeout(){},openDatabasePropertyList:index=>{context.shortcutIndex=index;},openDatabaseFilterDialog(){},openDatabaseSortDialog(){},openDatabaseGroupDialog(){}};
  vm.createContext(context);vm.runInContext(part("const DATABASE_VIEW_LABELS =","function focusDatabaseViewTab("),context);
  vm.runInContext(part("function openDatabaseViewDialog(","function openDatabasePropertyList("),context);
  context.openDatabaseViewDialog(0,viewId);
  return {context,State,block,errors,shortcuts,run:label=>buttons.find(x=>x.label===label).action(),replace(){const current=structuredClone(block);State.pageData.blocks=[current];return current;},
    editable:value=>editable=value,schema:value=>schema=value,counts:()=>({closed,dirty})};
}

test("UI-DB-VIEW-DIALOG create saves to the current model after a pending cell refresh",()=>{
  const f=fixture(),current=f.replace();f.run("저장");assert.equal(current.props.views.length,3);assert.equal(current.props.views[2].name,"Saved");assert.equal(f.block.props.views.length,2);
});
test("UI-DB-VIEW-DIALOG edit preserves current settings after replacement",()=>{
  const f=fixture("b"),current=f.replace();current.props.views[1].filters=[{property:"name",value:"remote"}];f.run("저장");
  assert.equal(current.props.views[1].name,"Saved");assert.equal(current.props.views[1].filters[0].value,"remote");assert.equal(f.block.props.views[1].name,"B");
});
test("UI-DB-VIEW-DIALOG copy and delete use the latest view list",()=>{
  const copy=fixture("b"),current=copy.replace();current.props.views[1].name="Latest";copy.run("복제");assert.equal(current.props.views[2].name,"Latest 사본");
  const remove=fixture("b"),next=remove.replace();next.props.views.push({id:"c",name:"C",type:"list"});remove.run("보기 삭제");assert.deepEqual(next.props.views.map(v=>v.id),["a","c"]);
});
test("UI-DB-VIEW-DIALOG changed scopes sources and permissions reject old dialogs",()=>{
  for(const change of [f=>f.State.activePageId="other",f=>f.State.pageLoadSequence++,f=>f.State.user.id="other",f=>f.State.navigation.workspaceId="other",f=>f.editable(false),f=>f.schema(false),f=>f.State.pageData.blocks[0].props.database_id="other"]){
    const f=fixture();const current=f.replace();change(f);f.run("저장");assert.equal(current.props.views.length,2);assert.equal(f.counts().dirty,0);assert.equal(f.counts().closed,0);assert.equal(f.errors.length,1);
  }
});
test("UI-DB-VIEW-DIALOG removed last and maximum views are revalidated at action time",()=>{
  const gone=fixture("b");gone.replace().props.views.pop();gone.run("저장");assert.equal(gone.counts().dirty,0);
  const last=fixture("b");last.replace().props.views.shift();last.run("보기 삭제");assert.equal(last.counts().dirty,0);
  const maximum=fixture();maximum.replace().props.views=Array.from({length:100},(_,i)=>({id:String(i),name:String(i),type:"table"}));maximum.run("저장");assert.equal(maximum.counts().dirty,0);
});
test("UI-DB-VIEW-DIALOG shortcuts keep the same block when document order changes",()=>{
  const f=fixture(),current=f.replace();f.State.pageData.blocks.unshift({id:"earlier"});f.shortcuts.find(button=>button.textContent==="속성 ›").click();assert.equal(f.context.shortcutIndex,1);assert.equal(f.State.pageData.blocks[1],current);
});
