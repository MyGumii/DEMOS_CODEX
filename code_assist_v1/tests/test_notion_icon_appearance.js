"use strict";
// UI-ICON-002: appearance changes preserve concurrent text and target identity.
const assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const block = source.slice(source.indexOf("function iconScope()"), source.indexOf("async function uploadPageIcon("));
const clone = value => JSON.parse(JSON.stringify(value));
function runtime() {
  const initial = {id:"a",can_edit:true,title:"A",icon:"",settings:{},updated_at:1};
  const state = {user:{id:"owner"},navigation:{workspaceId:"ws"},activePageId:"a",pageData:{page:initial,blocks:[{content:"original"}]},sidePeek:{},dirty:false,editRevision:1,serverRevision:1};
  const calls = []; let respond;
  const context = {State:state, console, pageIsLocked:p=>!!p.settings?.locked,flushPageDraft:async()=>true,
    api:async(url,options={})=>{calls.push([url,options.method||"GET"]); if(options.method!=="PATCH")return {page:clone(initial)};
      return new Promise(resolve=>{respond=resolve;});},
    applySaveMetadata:(holder,data)=>{holder.serverRevision=data.revision;},serverRevisionFrom:data=>data.revision??null,
    applyPageLayout(){},renderBreadcrumb(){},renderTopbarPageActions(){},loadPages:async()=>{},setStatus(){},$:()=>null};
  vm.createContext(context); vm.runInContext(block,context);
  return {context,state,calls,respond:data=>respond(data)};
}
const tick = ()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const r=runtime(), pending=r.context.updatePageAppearance({icon:"ni:flask:blue"},"a",{throwError:true});
  await tick(); r.state.pageData.blocks[0].content="typed while saving";r.state.dirty=true;r.state.editRevision=2;
  r.state.saveRetry={operationId:"pending-text"};r.state.saveConflict={current_revision:3};
  r.respond({page:{id:"a",title:"old title",icon:"ni:flask:blue",updated_at:2},blocks:[{content:"stale"}],revision:2});
  assert.equal(await pending,true); assert.equal(r.state.pageData.blocks[0].content,"typed while saving");
  assert.equal(r.state.pageData.page.title,"A");assert.equal(r.state.dirty,true);assert.equal(r.state.pageData.page.icon,"ni:flask:blue");
  assert.equal(r.state.saveRetry.operationId,"pending-text");assert.equal(r.state.saveConflict.current_revision,3);
  const switched=runtime(), task=switched.context.updatePageAppearance({icon:"⭐"},"a"); await tick();
  switched.state.activePageId="b";switched.state.pageData={page:{id:"b",title:"B",icon:""},blocks:[{content:"B"}]};
  switched.respond({page:{id:"a",icon:"⭐"},revision:3}); await task;
  assert.deepEqual(switched.state.pageData,{page:{id:"b",title:"B",icon:""},blocks:[{content:"B"}]});
  assert.deepEqual(switched.calls,[["/api/notion/pages/a","GET"],["/api/notion/pages/a","PATCH"]]);
  const scope=runtime();scope.context.flushPageDraft=async()=>{scope.state.navigation.workspaceId="other";return true;};
  await assert.rejects(scope.context.updatePageAppearance({icon:"⭐"},"a",{throwError:true}));assert.equal(scope.calls.length,0);
  const denied=runtime();denied.context.api=async()=>({page:{id:"a",can_edit:false}});
  await assert.rejects(denied.context.updatePageAppearance({icon:"⭐"},"a",{throwError:true}));
  console.log("UI-ICON-002 passed: concurrent text, navigation, workspace switch and permission denial");
})().catch(error=>{console.error(error);process.exitCode=1;});
