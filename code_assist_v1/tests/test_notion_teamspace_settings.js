"use strict";
const {test}=require("node:test"), assert=require("node:assert/strict"), fs=require("node:fs"), vm=require("node:vm"), path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion_teamspace_settings.js"),"utf8");
const clone=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function fixture() {
  let scope="alice/ws/1",serial=0;const requests=[];
  const data={teamspace:{id:"ts-one",workspace_id:"ws",name:"First"},workspace_version:2,members:[],capabilities:{rename:true}};
  const context={};vm.runInNewContext(source,context);
  const host={scope:()=>scope,actor:()=>scope.split("/")[0],operationId:()=>"op-"+(++serial),api:(url,options)=>{
    const d=deferred();requests.push({...d,url,options});return d.promise;}};
  return {controller:context.NotionTeamspaceSettings.create(host),requests,data,scope:value=>scope=value};
}
test("UI-TEAMSPACE-SETTINGS exact target, immutable retry, dedupe and current value after old receipt",async()=>{
  const f=fixture(), opening=f.controller.open("ws","ts-one");f.requests[0].resolve(f.data);const session=await opening;
  const first=session.save("New"), duplicate=session.save("Ignored");
  assert.equal(f.requests.length,2);assert.equal(f.requests[1].url,"/api/notion/workspaces/ws/teamspaces/ts-one");
  const request=clone(f.requests[1].options.body);
  f.requests[1].reject(Object.assign(Error("response lost"),{status:503}));
  await Promise.all([assert.rejects(first),assert.rejects(duplicate)]);
  const reopen=f.controller.open("ws","ts-one");f.requests[2].resolve({...f.data,teamspace:{...f.data.teamspace,name:"New"},workspace_version:3});
  const resumed=await reopen;assert.deepEqual(clone(resumed.pending),request);
  const retry=resumed.save("Cannot replace pending name");assert.deepEqual(clone(f.requests[3].options.body),request);
  f.requests[3].resolve({teamspace:{name:"New"}});await new Promise(setImmediate);
  f.requests[4].resolve({...f.data,teamspace:{...f.data.teamspace,name:"Later"},workspace_version:4});
  assert.equal((await retry).teamspace.name,"Later");assert.equal(resumed.pending,null);
});
test("UI-TEAMSPACE-SETTINGS stale workspace/actor results never update current surface",async()=>{
  const f=fixture(), opening=f.controller.open("ws","ts-one");f.scope("alice/other/2");f.requests[0].resolve(f.data);
  assert.equal(await opening,null);
  f.scope("alice/ws/3");const reopen=f.controller.open("ws","ts-one");f.requests[1].resolve(f.data);const s=await reopen;
  const saving=s.save("Name");f.scope("bob/ws/4");f.requests[2].resolve({});await new Promise(setImmediate);f.requests[3].resolve(f.data);
  assert.equal(await saving,null);assert.equal(await s.save("Stale click"),null);assert.equal(f.requests.length,4);
});
test("UI-TEAMSPACE-SETTINGS read-only, identity mismatch, version conflict and invalid name",async()=>{
  const f=fixture(), bad=f.controller.open("ws","ts-one");f.requests[0].resolve({...f.data,teamspace:{...f.data.teamspace,id:"another"}});
  await assert.rejects(bad,/정보/);
  const opening=f.controller.open("ws","ts-one");f.requests[1].resolve({...f.data,capabilities:{rename:false}});const readonly=await opening;
  await assert.rejects(readonly.save("Name"),/권한/);assert.equal(f.requests.length,2);
  const next=f.controller.open("ws","ts-one");f.requests[2].resolve(f.data);const s=await next;
  await assert.rejects(s.save("  "),/입력/);
  const saving=s.save("Name");f.requests[3].reject(Object.assign(Error("conflict"),{status:409}));
  await assert.rejects(saving,/다시 열어/);assert.equal(s.pending,null);assert.equal(s.data.capabilities.rename,false);
  await assert.rejects(s.save("Name"),/권한/);assert.equal(f.requests.length,4);
});

test("UI-TEAMSPACE-SETTINGS changing an anchored menu into a dialog releases pending resize placement",()=>{
  const listeners={},frames=[];let observed=0,unobserved=0;
  const context={window:{innerWidth:1000,innerHeight:800,addEventListener:(key,fn)=>listeners[key]=fn},
    requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},
    ResizeObserver:class {observe(){observed++;} unobserve(){unobserved++;}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,"../notion_static/notion_ui_interaction.js"),"utf8"),context);
  const panel={style:{},isConnected:true,hidden:false,getClientRects:()=>[{}],getBoundingClientRect:()=>({width:340,height:100})};
  const anchor={isConnected:true,getBoundingClientRect:()=>({left:100,right:130,top:40,bottom:60})};
  context.NotionUiInteraction.place(panel,anchor);listeners.resize();
  context.NotionUiInteraction.releasePlacement(panel);panel.style={};frames.shift()();
  assert.deepEqual(panel.style,{});assert.equal(observed,1);assert.equal(unobserved,1);
  context.NotionUiInteraction.place(panel,anchor);assert.equal(observed,2);
});

test("UI-TEAMSPACE-SETTINGS Escape resolves the current team button after redraw, or the mobile sidebar trigger",()=>{
  const main=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
  let current=true,target={getClientRects:()=>[{}]};const mobile={};
  const context={State:{navigation:{workspaceId:"ws"}},iconScope:()=>()=>current,cssEscape:v=>v,
    $:selector=>selector==="#teamspacePages"?{querySelector:()=>target}:mobile};
  vm.runInNewContext(main.slice(main.indexOf("function teamspaceFocusResolver("),main.indexOf("function openTeamspaceMenu(")),context);
  const resolver=context.teamspaceFocusResolver("ws","ts-one"),original=target;
  target={getClientRects:()=>[{}]};assert.notEqual(resolver(),original);assert.equal(resolver(),target);
  target={getClientRects:()=>[]};assert.equal(resolver(),mobile);
  current=false;assert.equal(resolver(),null);
});
