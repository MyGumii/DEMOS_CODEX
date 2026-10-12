"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
function fixture() {
  const listeners=new Map(), focused=[],target={disabled:true,focus:()=>focused.push("title"),select:()=>focused.push("select")};
  const c={State:{activePageId:"page",pageLoadSequence:1,user:{id:"owner"},navigation:{workspaceId:"ws"}},$:()=>target,
    document:{addEventListener:(name,fn)=>listeners.set(name,fn),removeEventListener:name=>listeners.delete(name)}};
  vm.createContext(c);vm.runInContext(source.slice(source.indexOf("const PageFocusPolicy ="),source.indexOf("function serverRevisionFrom(")),c);
  return {c,target,listeners,focused,start:()=>vm.runInContext('applyPageFocusPolicy(PageFocusPolicy.PAGE_TITLE)',c),
    replay:()=>{target.disabled=false;vm.runInContext('PendingPageTitleFocus?.()',c);}};
}
test("UI-PAGE-TITLE-FOCUS waits for collaborative replay before selecting new page title",()=>{
  const f=fixture();f.start();assert.deepEqual(f.focused,[]);f.replay();
  assert.deepEqual(f.focused,["title","select"]);assert.equal(f.listeners.size,0);
  f.replay();assert.equal(f.focused.length,2);
});
test("UI-PAGE-TITLE-FOCUS never steals focus after user input, account or navigation changes",()=>{
  for(const change of [f=>f.listeners.get("pointerdown")(),f=>f.listeners.get("keydown")(),
      f=>f.c.State.pageLoadSequence++,f=>f.c.State.activePageId="other",f=>f.c.State.user.id="other",
      f=>f.c.State.navigation.workspaceId="other"]) {
    const f=fixture();f.start();change(f);f.replay();assert.deepEqual(f.focused,[]);assert.equal(f.listeners.size,0);
  }
});
test("UI-TEAM-CREATE side peek resolves the same row action after a sidebar redraw",()=>{
  let current=true, queried;
  const replacement={},original={isConnected:true,getClientRects:()=>[{}],dataset:{pageChild:"parent"},closest:()=>({id:"team-tree"})};
  const c={iconScope:()=>()=>current,cssEscape:x=>x,document:{getElementById:id=>{
    assert.equal(id,"team-tree");return {querySelector:selector=>{queried=selector;return replacement;}};}}};
  vm.createContext(c);vm.runInContext(source.slice(source.indexOf("function sidePeekReturnFocusResolver("),source.indexOf("async function openNewPageSidePeek(")),c);
  const resolve=c.sidePeekReturnFocusResolver(original);assert.equal(resolve(),original);
  original.isConnected=false;assert.equal(resolve(),replacement);assert.equal(queried,'[data-page-child="parent"]');
  current=false;assert.equal(resolve(),null);
});
