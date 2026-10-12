"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b));
const clone=value=>JSON.parse(JSON.stringify(value));
const icons=require("../notion_static/notion_icons.js");
function fixture(){
  const State={user:{id:"owner"},navigation:{workspaceId:"one"},pageData:{blocks:[{content:"미저장 내용"}]}};
  const current={id:"one",icon:"",version:1,access:{role:"owner"}},hooks={},requests=[],receipts=new Map();
  let next=0,renders=0;
  const context={State,window:{NotionIcons:{validateImage:async()=>"image/png"}},newOperationId:()=>`image-${++next}`,
    api:async()=>{if(hooks.read)await hooks.read();return {workspace:clone(current),capabilities:{workspace_image:hooks.supported!==false}};},
    authenticatedFetch:async(url,options)=>{
      assert.equal(url,"/api/notion/workspaces/one/icon-image");
      requests.push({...options,headers:{...options.headers}});
      const op=options.headers["X-Notion-Operation-Id"];
      if(receipts.has(op))return {ok:true};
      if(hooks.write)await hooks.write();
      if(hooks.status)return {ok:false,status:hooks.status,json:async()=>({message:"conflict"})};
      assert.equal(Number(options.headers["X-Notion-Base-Version"]),current.version);
      current.icon="nw:wi_"+"a".repeat(40);current.version++;receipts.set(op,true);
      if(hooks.after)await hooks.after();
      return {ok:true};
    },applyNavigationWorkspaceResult:value=>{State.workspace=clone(value);},renderPageLists:()=>renders++,renderWorkspacePopover(){},setStatus(){}};
  vm.createContext(context);
  vm.runInContext(part("function iconScope()","function pageCanChangeAppearance(")+part("const WorkspaceImageIntents =","async function openWorkspaceIconPicker("),context);
  return {State,current,hooks,requests,context,renders:()=>renders};
}
(async()=>{
  const image={name:"synthetic.png"};
  {
    const f=fixture();f.hooks.after=async()=>{throw new Error("response lost");};
    await assert.rejects(f.context.saveWorkspaceImage("one",image),/response lost/);
    f.current.icon="🌱";f.current.version++;
    await f.context.saveWorkspaceImage("one",image);
    assert.deepEqual(f.requests[0].headers,f.requests[1].headers,"retry retains exact version and operation ID");
    assert.equal(f.requests[0].body,image,"the chosen file remains the retry body");
    assert.equal(f.State.workspace.icon,"🌱","old success receipts cannot restore old workspace appearance");
    assert.equal(f.State.pageData.blocks[0].content,"미저장 내용");
  }
  {
    const f=fixture();let release;f.hooks.write=()=>new Promise(resolve=>{release=resolve;});
    const first=f.context.saveWorkspaceImage("one",image),second=f.context.saveWorkspaceImage("one",image);
    await new Promise(resolve=>setImmediate(resolve));assert.equal(f.requests.length,1);
    release();await Promise.all([first,second]);assert.equal(f.current.version,2);
  }
  {
    const f=fixture();f.hooks.status=409;
    await assert.rejects(f.context.saveWorkspaceImage("one",image),/다시 눌러/);
    delete f.hooks.status;f.current.version++;
    await f.context.saveWorkspaceImage("one",image);
    assert.notEqual(f.requests[0].headers["X-Notion-Operation-Id"],f.requests[1].headers["X-Notion-Operation-Id"]);
  }
  for(const change of [f=>f.current.access.role="member",f=>f.hooks.supported=false,
                       f=>f.hooks.read=async()=>{f.State.navigation.workspaceId="two";}]){
    const f=fixture();change(f);await assert.rejects(f.context.saveWorkspaceImage("one",image));assert.equal(f.requests.length,0);
  }
  {
    const f=fixture();f.hooks.after=async()=>{f.State.navigation.workspaceId="two";};
    await f.context.saveWorkspaceImage("one",image);assert.equal(f.renders(),0);assert.equal(f.State.workspace,undefined);
  }
  const value="nw:wi_"+"a".repeat(40);
  assert.equal(icons.parse(value).kind,"workspace-file");
  assert(icons.workspace({icon:value}).includes("/api/notion/workspace-images/wi_"));
  for(const invalid of ["nw:../../private","nw:wi_\"onload=x","nw:other"]){
    assert.equal(icons.parse(invalid).kind,"default");assert(!icons.content(invalid).includes("workspace-images"));
  }
  console.log("UI-WS-IMAGE-UI: upload retry, duplicate apply, conflict, permissions, scope and workspace image rendering passed");
})().catch(error=>{console.error(error);process.exitCode=1;});
