"use strict";
const assert = require("node:assert/strict"), {test} = require("node:test");
const fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const source = fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
const part = (a,b) => source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
function setup(role="owner") {
  const State={user:{id:"alice"},pages:[],navigation:{enabled:true,collaborativeSharing:true,workspaceId:"ws-a",sequence:1}};
  const workspace={id:"ws-a",name:"Current workspace",access:{role}};
  const calls=[],root={classList:{add(){},remove(){}},setAttribute(){},controls:[]};
  function control(dataset={}) {return {dataset,addEventListener(_,fn){this.click=fn;}};}
  Object.defineProperty(root,"innerHTML",{set(value){this.html=value;this.controls=[...value.matchAll(/data-home-page="([^"]+)"/g)].map(m=>control({homePage:m[1]}));
    this.search=control();this.create=value.includes("data-home-create")?control():null;},get(){return this.html;}});
  root.querySelectorAll=()=>root.controls;
  root.querySelector=selector=>selector==="[data-home-search]"?root.search:root.create;
  const context={State,PageFocusPolicy:{PAGE_CANVAS:"canvas",PRESERVE:"preserve"},
    pageIsFavorite:p=>p.favorite===true,pageNavigationPlacement:p=>p.placement,
    sidebarTeamspaceGroups:()=>[{name:"Actual team"}],
    sidebarCapabilities:()=>({canCreate:workspace.access.role==="owner"}),
    escapeHtml:s=>String(s).replaceAll("<","&lt;").replaceAll(">","&gt;"),escapeAttr:String,
    pageIconHtml:()=>"icon",iconSvg:()=>"svg",formatRelative:()=>"recent",
    openPage:(...args)=>calls.push(["page",...args]),openSearchOverlay:()=>calls.push(["search"]),
    openNewPageMenu:()=>calls.push(["create"])};
  vm.createContext(context);vm.runInContext(part("function collaborativeHomePages(","function renderHome("),context);
  return {State,workspace,root,calls,context,render:()=>context.renderCollaborativeWorkspaceHome(root,workspace)};
}
const page=(id,more={})=>({id,workspace_id:"ws-a",title:id,updated_at:1,scope:"own",...more});

test("UI-WORKSPACE-HOME only current authorized list supplies distinct unarchived pages",()=>{
  const h=setup();h.State.pages=[page("one"),page("foreign",{workspace_id:"ws-b"}),page("unknown",{workspace_id:undefined}),
    page("archived",{archived:true}),page("one"),page("newest",{updated_at:20})];
  assert.deepEqual(Array.from(h.context.collaborativeHomePages(h.workspace),p=>p.id),["newest","one"]);
  h.State.navigation.workspaceId="ws-b";assert.equal(h.context.collaborativeHomePages(h.workspace).length,0);
});

test("UI-WORKSPACE-HOME cards use real titles, icons and location; legacy overview is excluded",()=>{
  const h=setup();h.State.pages=[page("<title>",{favorite:true}),page("shared",{scope:"shared"}),
    page("team",{placement:{scope:"teamspace",teamspace_id:"team-a"}})];
  h.State.overview={calendar_events:[{title:"PRIVATE OVERVIEW",page_id:"foreign"}]};h.render();
  assert.match(h.root.html,/&lt;title&gt;/);assert.match(h.root.html,/Actual team/);assert.match(h.root.html,/공유된 페이지/);
  assert.doesNotMatch(h.root.html,/PRIVATE OVERVIEW|HQ|팀스페이스 홈|Slack|팀장/);
  assert.match(h.root.html,/즐겨찾기/);assert.match(h.root.html,/최근 수정한 페이지/);
});

test("UI-WORKSPACE-HOME reader and guest get the same navigation without creation",()=>{
  for(const role of ["member","restricted_member","guest"]) {
    const h=setup(role);h.State.pages=[page("shared")];h.render();assert.equal(h.root.create,null);
    h.root.controls[0].click({detail:0});assert.deepEqual(h.calls,[["page","shared",true,"canvas"]]);
    if(role==="guest")assert.doesNotMatch(h.root.html,/개인 페이지/);
  }
});

test("UI-WORKSPACE-HOME empty state and current actions use existing search and creation flows",()=>{
  const h=setup();h.render();assert.match(h.root.html,/표시할 페이지가 없습니다/);
  h.root.search.click();h.root.create.click({currentTarget:h.root.create});
  assert.deepEqual(h.calls,[["search"],["create"]]);
});

test("UI-WORKSPACE-HOME old controls cannot navigate or create after scope or permission changes",()=>{
  for(const change of [h=>h.State.user.id="bob",h=>h.State.navigation.workspaceId="ws-b",
    h=>h.State.navigation.sequence++,h=>h.State.navigation.enabled=false,h=>h.render()]) {
    const h=setup();h.State.pages=[page("one")];h.render();const {search,create,controls}=h.root;
    change(h);search.click();create.click({currentTarget:create});controls[0].click({detail:1});assert.deepEqual(h.calls,[]);
  }
  const h=setup();h.State.pages=[page("one")];h.render();h.State.pages=[];
  h.root.controls[0].click({detail:1});assert.deepEqual(h.calls,[]);
  h.workspace.access.role="guest";h.root.create.click({currentTarget:h.root.create});assert.deepEqual(h.calls,[]);
});

test("UI-WORKSPACE-HOME new-page menu returns to its actual trigger and rejects an old workspace",async()=>{
  const h=setup();let focus="",created=0,action;
  const anchor={isConnected:true,getBoundingClientRect:()=>({left:20,top:80}),focus(){focus="home";}};
  const item={dataset:{newPageType:"page"},addEventListener(_,fn){action=fn;},focus(){}};
  const menu={style:{},querySelector:()=>item,querySelectorAll:()=>[item]};
  Object.assign(h.context,{$:s=>s==="#newPageMenu"?menu:{focus(){focus="sidebar";}},
    window:{innerWidth:1000,innerHeight:800,NotionUiInteraction:{place(){}}},
    pageCreationDestinations:()=>[{id:"",name:"개인 페이지"}],iconSvg:()=>"",
    closeMovePageMenu(){},closePageMenu(){},createPage:async()=>created++});
  vm.runInContext(part("function closeNewPageMenu(","function closeMovePageMenu("),h.context);
  h.context.openNewPageMenu(anchor);let stopped=false;
  menu.onkeydown({key:"Escape",preventDefault(){},stopPropagation(){stopped=true;}});
  assert.equal(stopped,true);assert.equal(focus,"home");assert.equal(menu.onkeydown,null);
  h.context.openNewPageMenu(anchor);h.State.navigation.workspaceId="ws-b";await action();assert.equal(created,0);
  h.State.navigation.workspaceId="ws-a";h.context.openNewPageMenu(anchor);await action();assert.equal(created,1);
  await action();assert.equal(created,1,"the detached menu item cannot create twice after closing");
});
