"use strict";
// UI-ICON-006: preserve an uncertain write's identity and re-read its source.
const assert = require("node:assert/strict"), vm = require("node:vm"), fs = require("node:fs"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const block = source.slice(source.indexOf("function iconScope()"),source.indexOf("async function uploadPageIcon("));
function runtime() {
  let serial=0;
  const state={user:{id:"owner"},navigation:{workspaceId:"ws",collaborativeSharing:true},activePageId:"a",
    pageData:{page:{id:"a",icon:"",can_edit:true,document_sequence:1},blocks:[{content:"typing"}]},sidePeek:{},dirty:true};
  const remote={id:"a",icon:"",can_edit:true,can_change_appearance:true,document_sequence:1,workspace_epoch:2,document_epoch:3};
  const writes=[], reads=[];
  const context={State:state,pageIsLocked:p=>!!p.settings?.locked,flushPageDraft:async()=>true,newOperationId:()=>`operation-${++serial}`,
    applyPageLayout(){},renderBreadcrumb(){},renderTopbarPageActions(){},loadPages:async()=>{},setStatus(){},$:()=>null,serverRevisionFrom:()=>null,
    api:async(url,opts={})=>{
      if(opts.method!=="PATCH") {reads.push(url);return {page:{...remote},capabilities:{collaborative_appearance:true}};}
      assert.ok(url.endsWith("/appearance")); writes.push(JSON.parse(JSON.stringify(opts.body)));
      remote.icon=opts.body.updates.icon;remote.document_sequence++;
      if(writes.length===1) throw Object.assign(new Error("response lost"),{status:503});
      return {page:{...remote}};
    }};
  vm.createContext(context);vm.runInContext(block,context);
  return {state,remote,writes,reads,context};
}
(async()=>{
  const r=runtime();
  await assert.rejects(r.context.updatePageAppearance({icon:"ni:flask:blue"},"a",{throwError:true}),/response lost/);
  r.remote.icon="⭐"; // a later change must be respected after receipt replay
  r.context.api=async(url,opts={})=>{
    if(opts.method==="PATCH") {r.writes.push(JSON.parse(JSON.stringify(opts.body)));return {page:{icon:"ni:flask:blue"}};}
    r.reads.push(url);return {page:{...r.remote},capabilities:{collaborative_appearance:true}};
  };
  assert.equal(await r.context.updatePageAppearance({icon:"ni:flask:blue"},"a",{throwError:true}),true);
  assert.deepEqual(r.writes[0],r.writes[1]);
  assert.equal(r.state.pageData.page.icon,"⭐");
  assert.equal(r.state.pageData.blocks[0].content,"typing");assert.equal(r.state.dirty,true);
  assert.ok(r.reads.every(url=>url.includes("/workspaces/ws/pages/a/collaboration")));
  const conflict=runtime();let count=0;
  conflict.context.api=async(url,opts={})=>{
    if(opts.method!=="PATCH") return {page:{...conflict.remote},capabilities:{collaborative_appearance:true}};
    conflict.writes.push(JSON.parse(JSON.stringify(opts.body)));
    if(!count++) {conflict.remote.document_sequence=9;throw Object.assign(new Error("conflict"),{status:409,data:{code:"document_sequence_conflict"}});}
    conflict.remote.icon="";return {page:{...conflict.remote}};
  };
  await assert.rejects(conflict.context.updatePageAppearance({icon:""},"a",{throwError:true}),/다시 적용/);
  await conflict.context.updatePageAppearance({icon:""},"a",{throwError:true});
  assert.notEqual(conflict.writes[0].operation_id,conflict.writes[1].operation_id);
  assert.equal(conflict.writes[1].expected_document_sequence,9);
  const denied=runtime();denied.remote.can_change_appearance=false;
  await assert.rejects(denied.context.updatePageAppearance({icon:"⭐"},"a",{throwError:true}));
  assert.equal(denied.writes.length,0);
  console.log("UI-ICON-006 passed: exact retry, fresh metadata, conflict recovery, role, text preservation");
})().catch(error=>{console.error(error);process.exitCode=1;});
