"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const source=fs.readFileSync(require.resolve("../notion_static/notion.js"),"utf8");
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const plain=value=>JSON.parse(JSON.stringify(value));
function fixture(kind,propertyId="") {
  const block={id:"block",type:"table",content:"Database",props:{database_id:"db",view:"table",active_view_id:"a",
    properties:[{id:"name",name:"Name",type:"title"},{id:"status",name:"Status",type:"status",options:["Todo"]},{id:"date",name:"Date",type:"date"}],
    rows:[{id:"row",name:"Original",status:"Todo"}],filters:[],sorts:[],group_property:"",property_visibility:{name:true,status:true,date:true},
    views:[{id:"a",name:"A",type:"table"},{id:"b",name:"B",type:"board"}]}};
  const State={activePageId:"page",user:{id:"owner"},navigation:{workspaceId:"ws"},pageLoadSequence:1,pageData:{blocks:[block]}};
  let buttons,dirty=0,closed=0,editable=true,schema=true;
  const errors=[],fields={},remove={};
  const visibility=["name","status","date"].map(id=>({dataset:{visibleProperty:id},checked:true}));
  const filter=[{property:"name",operator:"contains",value:"Latest"}],sort=[{property:"name",direction:"desc"}];
  const domRows=(rules,prefix)=>rules.map(rule=>({querySelector:selector=>({value:rule[selector.slice(prefix.length+1,-1)]})}));
  const context={State,EditorCore:require("../notion_static/notion_editor_core.js"),DATABASE_BLOCK_TYPES:new Set(["table","board","list"]),
    window:{NotionLinkedDatabases:{reference:b=>b.props?.linked_database,filterGroupWithoutProperty:()=>null,
      GROUP_SORTS:[["manual","수동"],["asc","오름차순"]]}},canEditDatabaseSchema:()=>schema,activePageCanEdit:()=>editable,
    collaborativeDatabaseActive:()=>false,linkedDatabaseScope:()=>null,
    databaseControlFocusScope:()=>JSON.stringify([State.activePageId,State.user.id,State.navigation.workspaceId,State.pageLoadSequence]),
    databaseProperties:b=>b.props.properties,databaseRows:b=>b.props.rows,databasePropertyVisible:(b,p)=>p.type==="title"||b.props.property_visibility[p.id]!==false,
    databaseRelationTarget:()=>null,blockTypeMeta:()=>["","Database"],safeExternalUrl:x=>x,escapeAttr:x=>x,escapeHtml:x=>x,
    openModal:(title,html,actions)=>{context.modal={title,html};buttons=actions;},closeModal:()=>closed++,markDirty:()=>dirty++,
    captureEditorHistory(){},renderBlocks(){},setStatus:(...args)=>errors.push(args),setTimeout(){},
    $:selector=>selector==="#dbRelationTargetSearch"?null
      :selector==="#modalBody"?{querySelectorAll:()=>visibility}:(fields[selector]??={value:"",checked:false}),
    document:{querySelectorAll:selector=>{
      if(selector.endsWith(".database-filter-rule"))return domRows(filter,"data-filter-");
      if(selector.endsWith(".database-sort-rule"))return domRows(sort,"data-sort-");
      const key=selector.includes("remove-filter")?"filter":"sort";
      return [{dataset:{removeFilter:"0",removeSort:"0"},addEventListener:(event,fn)=>remove[key]=fn}];
    }}};
  fields["#dbPropertyName"]={value:"Saved"};fields["#dbPropertyType"]={value:"text"};fields["#dbGroupProperty"]={value:"status"};
  vm.createContext(context);vm.runInContext(part("const DATABASE_VIEW_LABELS =","function focusDatabaseViewTab("),context);
  vm.runInContext(part(source.includes("function databaseSettingsDialogTarget(")?"function databaseSettingsDialogTarget(":"function openDatabasePropertyList(","function bindBoardCardDrag("),context);
  const open={visibility:"openDatabasePropertyList",property:"openDatabasePropertyDialog",filter:"openDatabaseFilterDialog",sort:"openDatabaseSortDialog",group:"openDatabaseGroupDialog"}[kind];
  context[open](0,propertyId||undefined);
  return {context,State,block,fields,visibility,filter,sort,remove,errors,run:label=>buttons.find(x=>x.label===label).action(),
    replace(){const next=structuredClone(block);State.pageData.blocks=[next];return next;},editable:x=>editable=x,schema:x=>schema=x,counts:()=>({dirty,closed})};
}

test("UI-DB-SETTINGS filter applies to refreshed model without losing current rows or other settings",()=>{
 const f=fixture("filter"),current=f.replace();current.props.rows[0].name="Remote";current.props.sorts=[{property:"date",direction:"asc"}];
 f.run("적용");assert.deepEqual(plain(current.props.filters),f.filter);assert.equal(current.props.rows[0].name,"Remote");assert.equal(current.props.sorts[0].property,"date");assert.equal(f.block.props.filters.length,0);
});
test("UI-DB-SETTINGS sort and group save and clear after a cell response replaces the block",()=>{
 for(const kind of ["sort","group"]){const f=fixture(kind),current=f.replace();f.run("적용");assert.equal(kind==="sort"?current.props.sorts[0]?.direction:current.props.group_property,kind==="sort"?"desc":"status");}
 for(const [kind,label] of [["filter","필터 지우기"],["sort","정렬 지우기"]]){const f=fixture(kind);f.block.props[kind+"s"]=[{property:"name"}];const current=f.replace();f.run(label);assert.equal(current.props[kind+"s"].length,0);}
});
test("UI-DB-SETTINGS visibility merges edited toggles with new and remotely changed properties",()=>{
 const f=fixture("visibility"),current=f.replace();current.props.property_visibility.date=false;current.props.properties.push({id:"new",type:"text"});current.props.property_visibility.new=false;
 f.visibility.find(v=>v.dataset.visibleProperty==="status").checked=false;f.run("저장");
 assert.deepEqual(plain(current.props.property_visibility),{name:true,status:false,date:false,new:false});assert.equal(f.block.props.property_visibility.status,true);
});
test("UI-DB-SETTINGS property create edit duplicate and delete use current properties and row values",()=>{
 const create=fixture("property"),created=create.replace();created.props.properties.push({id:"remote",type:"text"});create.run("저장");assert.equal(created.props.properties.length,5);assert.equal(created.props.properties[4].name,"Saved");
 const edit=fixture("property","status"),edited=edit.replace();edit.run("저장");assert.equal(edited.props.properties[1].name,"Saved");assert.equal(edit.block.props.properties[1].name,"Status");
 const copy=fixture("property","status"),copied=copy.replace();copied.props.rows[0].status="Current";copy.run("복제");assert.equal(copied.props.properties.length,4);assert.equal(copied.props.rows[0][copied.props.properties[2].id],"Current");
 const del=fixture("property","status"),deleted=del.replace();deleted.props.properties.push({id:"remote",type:"text"});del.run("속성 삭제");assert.deepEqual(deleted.props.properties.map(p=>p.id),["name","date","remote"]);assert.equal(deleted.props.rows[0].status,undefined);
});
test("UI-DB-SETTINGS all dialogs reject page account source view permission and deleted-block changes",()=>{
 const changes=[f=>f.State.activePageId="other",f=>f.State.pageLoadSequence++,f=>f.State.user.id="other",f=>f.State.navigation.workspaceId="other",f=>f.editable(false),f=>f.schema(false),f=>f.State.pageData.blocks[0].props.database_id="other",f=>f.State.pageData.blocks[0].props.active_view_id="b",f=>f.State.pageData.blocks[0].type="paragraph",f=>f.State.pageData.blocks[0].props.linked_database={},f=>f.State.pageData.blocks=[]];
 for(const kind of ["visibility","property","filter","sort","group"])for(const change of changes){const f=fixture(kind);f.replace();change(f);const before=JSON.stringify(f.State.pageData);f.run(["visibility","property"].includes(kind)?"저장":"적용");assert.equal(f.counts().dirty,0,kind);assert.equal(f.counts().closed,0,kind);assert.equal(JSON.stringify(f.State.pageData),before,kind);assert.equal(f.errors.length,1,kind);}
});
test("UI-DB-SETTINGS removed properties changed title types and property limits are revalidated",()=>{
 for(const label of ["저장","복제","속성 삭제"]){const f=fixture("property","status"),current=f.replace();current.props.properties.splice(1,1);f.run(label);assert.equal(f.counts().dirty,0);}
 for(const label of ["저장","복제","속성 삭제"]){const f=fixture("property","status"),current=f.replace();current.props.properties[1].type="title";f.run(label);assert.equal(f.counts().dirty,0);}
 for(const [id,label] of [["","저장"],["status","복제"]]){const f=fixture("property",id),current=f.replace();while(current.props.properties.length<500)current.props.properties.push({id:"p"+current.props.properties.length,type:"text"});f.run(label);assert.equal(f.counts().dirty,0);assert.equal(current.props.properties.length,500);}
});
test("UI-DB-SETTINGS filter sort and group reject removed selected properties without closing drafts",()=>{
 for(const kind of ["filter","sort","group"]){const f=fixture(kind),current=f.replace();current.props.properties=current.props.properties.filter(p=>p.id!==(kind==="group"?"status":"name"));f.run("적용");assert.equal(f.counts().dirty,0,kind);assert.equal(f.counts().closed,0,kind);assert.equal(f.errors.length,1,kind);}
});
test("UI-DB-SETTINGS adding and removing rule rows still targets the same block after reordering",()=>{
 for(const kind of ["filter","sort"])for(const remove of [false,true]){const f=fixture(kind),current=f.replace();f.State.pageData.blocks.unshift({id:"unrelated",type:"paragraph",props:{}});if(remove)f.remove[kind]();else f.run(kind==="filter"?"＋ 필터 추가":"＋ 정렬 추가");f.run("적용");assert.equal(current.props[kind+"s"].length,1);assert.equal(f.State.pageData.blocks[0].props[kind+"s"],undefined);}
});
