"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const segment=source.slice(source.indexOf("const PendingDatabaseItemCreates ="),source.indexOf("function openSubPageModal("));
function runtime(){
  const block={id:"db",type:"table",props:{database_id:"db",rows:[],properties:[{id:"name",type:"title"},{id:"date",type:"date"}]}};
  const State={activePageId:"page",user:{id:"owner"},pageData:{blocks:[block],databases:[{id:"db",rows:[]}]}};
  const statuses=[],rows=[],focus=[];let ids=0;
  const context={State,Map,Date,structuredClone,CSS:{escape:x=>x},crypto:{randomUUID:()=>`random-${++ids}`},
    collaborativeDatabaseActive:()=>true,activePageCanEdit:()=>true,collaborativePageBasePath:()=>`/workspace/${State.activePageId}`,
    flushPageDraft:async()=>true,databaseProperties:b=>b.props.properties,databaseRows:b=>b.props.rows,
    databasePropertyId:(b,type)=>b.props.properties.find(p=>p.type===type).id,
    flushCollaborativeDatabaseSync:async()=>{State.pageData.databases[0].rows=block.props.rows.map(row=>({id:row.page_id}));rows.push(...block.props.rows);},
    DatabaseBridge:{diffPage:()=>[]},markDirty(){},renderBlocks(){},setStatus:(...args)=>statuses.push(args),
    document:{querySelector:selector=>({focus:()=>focus.push(selector),select(){}})},
    api:()=>{throw Error("Canonical rows must never POST a legacy page");}};
  vm.createContext(context);vm.runInContext(segment,context);
  return {context,State,block,statuses,rows,focus};
}
(async()=>{
  const h=runtime();await h.context.createDatabaseItem(0,"table");
  assert.equal(h.rows.length,1);assert.equal(h.rows[0].name,"새 페이지");assert.equal(h.focus.length,1);
  const failed=runtime();let count=0;
  failed.context.flushCollaborativeDatabaseSync=async()=>{if(++count===1)throw Error("response lost");failed.State.pageData.databases[0].rows=failed.block.props.rows.map(r=>({id:r.page_id}));};
  await failed.context.createDatabaseItem(0,"table");const rowId=failed.block.props.rows[0].page_id;
  await failed.context.createDatabaseItem(0,"table");
  assert.equal(failed.block.props.rows.length,1);assert.equal(failed.State.pageData.databases[0].rows[0].id,rowId);
  const concurrent=runtime();let release;concurrent.context.flushPageDraft=()=>new Promise(r=>{release=r;});
  const flight=concurrent.context.createDatabaseItem(0,"calendar","2026-09-20");
  await concurrent.context.createDatabaseItem(0,"calendar","2026-09-21");release(true);await flight;
  assert.equal(concurrent.rows.length,1);assert.equal(concurrent.rows[0].date,"2026-09-20");
  const switched=runtime();switched.context.flushPageDraft=async()=>{switched.State.activePageId="other";return true;};
  await switched.context.createDatabaseItem(0,"table");assert.equal(switched.block.props.rows.length,0);
  const denied=runtime();denied.context.activePageCanEdit=()=>false;await denied.context.createDatabaseItem(0,"table");assert.equal(denied.rows.length,0);
  console.log("native database item creation: canonical routing, retry identity, double click, navigation and permission guards passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
