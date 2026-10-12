"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
const clone=value=>JSON.parse(JSON.stringify(value));
function fixture(){
  const State={user:{id:"owner"},navigation:{workspaceId:"one",workspaces:[{id:"one",icon:"",name:"workspace",access:{role:"owner"}}]},pageData:{blocks:[{content:"미저장 본문"}]}};
  const current={id:"one",icon:"",name:"workspace",version:1,access:{role:"owner"}};
  const requests=[],receipts=new Map(),hooks={};let id=0,renders=0;
  const context={State,newOperationId:()=>`op-${++id}`,renderPageLists:()=>renders++,renderWorkspacePopover(){},setStatus(){},
    api:async(url,options)=>{
      if(!options){if(hooks.read)await hooks.read();return {workspace:clone(current),capabilities:{workspace_appearance:true}};}
      assert.equal(url,"/api/notion/workspaces/one/appearance");requests.push(clone(options.body));
      if(receipts.has(options.body.operation_id))return clone(receipts.get(options.body.operation_id));
      if(hooks.beforeWrite)await hooks.beforeWrite();
      assert.equal(options.body.base_version,current.version);current.icon=options.body.icon;current.version++;
      const receipt={workspace:clone(current)};receipts.set(options.body.operation_id,receipt);
      if(hooks.afterWrite)await hooks.afterWrite();return receipt;
    }};
  vm.createContext(context);vm.runInContext(part("function iconScope(","function pageCanChangeAppearance(")+part("const WorkspaceIconIntents =","async function openWorkspaceIconPicker("),context);
  return {State,current,requests,hooks,context,renders:()=>renders};
}
(async()=>{
  {
    const f=fixture();f.hooks.afterWrite=async()=>{throw new Error("response lost");};
    await assert.rejects(f.context.saveWorkspaceIcon("one","ni:rocket:purple"),/response lost/);
    f.current.icon="";f.current.version++;
    await f.context.saveWorkspaceIcon("one","ni:rocket:purple");
    assert.deepEqual(f.requests[0],f.requests[1],"uncertain response reuses the exact original operation");
    assert.equal(f.State.navigation.workspaces[0].icon,"","old receipt is followed by a fresh authoritative read");
    assert.equal(f.State.navigation.workspaces[0].access.role,"owner");
    assert.equal(f.State.pageData.blocks[0].content,"미저장 본문");
  }
  {
    const f=fixture();f.current.access.role="member";
    await assert.rejects(f.context.saveWorkspaceIcon("one","🌱"),/권한/);assert.equal(f.requests.length,0);
  }
  {
    const f=fixture();f.hooks.afterWrite=async()=>{f.State.navigation.workspaceId="two";};
    await f.context.saveWorkspaceIcon("one","🌱");assert.equal(f.renders(),0,"a completed write cannot repaint a different workspace");
  }
  {
    const f=fixture();f.hooks.beforeWrite=async()=>{f.current.version++;throw Object.assign(new Error("conflict"),{status:409});};
    await assert.rejects(f.context.saveWorkspaceIcon("one","🌱"),/다시 선택/);
    delete f.hooks.beforeWrite;await f.context.saveWorkspaceIcon("one","🌱");
    assert.notEqual(f.requests[0].operation_id,f.requests[1].operation_id,"a confirmed conflict requires a fresh application");
    assert.equal(f.State.navigation.workspaces[0].icon,"🌱");
  }
  console.log("UI-WS-ICON-002: exact retry, fresh receipt read, permissions, workspace switch and conflict reapplication passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
