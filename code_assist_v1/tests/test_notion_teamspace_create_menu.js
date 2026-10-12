"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
const chooser=require("../notion_static/notion_creation_chooser.js");
const source=fs.readFileSync(path.join(__dirname,"../notion_static/notion.js"),"utf8");
function control(){return {value:"",events:{},focus(){},addEventListener(type,fn){this.events[type]=fn;}};}
function surface(){
  const search=control(),select=control(),empty={hidden:true},results={buttons:[],querySelector(){return this.buttons[0];},querySelectorAll(){return this.buttons;}};
  Object.defineProperty(results,"innerHTML",{set(html){this.html=html;this.buttons=[...html.matchAll(/data-create-template="([^"]+)"/g)].map(match=>({...control(),dataset:{createTemplate:match[1]}}));}});
  const root={isConnected:true,search,select,results,empty,querySelector(selector){return ({'.creation-search':search,'select':select,'[data-creation-results]':results,'.creation-empty':empty})[selector];}};
  Object.defineProperty(root,"innerHTML",{set(html){this.html=html;select.value=html.match(/<option value="([^"]*)" selected>/)?.[1]||"";}});
  return root;
}
function fixture() {
  let current=true,root,target={getClientRects:()=>[{}]};
  const requests=[],workspace={id:"ws",access:{role:"member"}},group={id:"team",name:"제품 팀"},modal={hidden:true,classList:{add(){}}},mobile={};
  const State={workspaceOwnerFilter:"",workspaceAccess:{id:"ws",teamspaces:[{id:"team",name:"제품 팀",role:"member"}]},
    navigation:{workspaceId:"ws",enabled:true,collaborativeSharing:true,collaborativeTeamPageCreation:true,
      pageCreationTemplates:["page","database","linked_database","meeting_notes","task_tracker"],sequence:1}};
  const c={State,document:{activeElement:null},window:{NotionCreationChooser:chooser},
    sidebarCapabilities:()=>({canCreate:workspace.access.role==="owner"}),selectedNavigationWorkspace:()=>workspace,
    iconScope:()=>()=>current,cssEscape:x=>x,iconSvg:()=>"<svg></svg>",
    navigationControlsLocked:()=>false,closePageNavigationMenus(){},openModal(){modal.hidden=false;root=surface();},
    closeModal(){modal.hidden=true;},createPage:(...args)=>requests.push(args),
    $:sel=>sel==="#pageCreationChooser"?root:sel==="#modalBg"?modal:sel==="#teamspacePages"?{querySelector:()=>target}:mobile};
  vm.createContext(c);vm.runInContext(source.slice(source.indexOf("function teamspaceFocusResolver("),source.indexOf("function openTeamspaceMenu(")),c);
  return {c,State,workspace,group,modal,mobile,requests,root:()=>root,
    click:()=>root.results.buttons[0].events.click(),invalidate:()=>current=false,
    hideAnchor:()=>target={getClientRects:()=>[]},replaceRoot:()=>root=surface(),open:()=>c.openTeamspaceCreateMenu(workspace,group,{})};
}
test("UI-CREATION-CHOOSER team context commits once and restores its current trigger",()=>{
  const f=fixture();f.open();assert.equal(f.root().select.value,"team");f.click();f.click();assert.equal(f.requests.length,1);
  assert.equal(f.requests[0][1].teamspaceId,"team");assert.equal(f.requests[0][1].template,"page");
  f.hideAnchor();assert.equal(f.c.ModalReturnFocus(),f.mobile);f.invalidate();assert.equal(f.c.ModalReturnFocus(),null);
});
test("UI-CREATION-CHOOSER stale surface, revoked team and changed workspace cannot submit",()=>{
  for(const change of [f=>f.invalidate(),f=>f.State.navigation.sequence++,f=>f.State.workspaceAccess.teamspaces=[],
      f=>{f.State.navigation.workspaceId="other";f.workspace.id="other";}]) {
    const f=fixture();f.open();change(f);f.click();assert.equal(f.requests.length,0);
  }
  const f=fixture();f.open();const old=f.root().results.buttons[0];f.replaceRoot();old.events.click();assert.equal(f.requests.length,0);
});
test("UI-CREATION-CHOOSER search, keyboard entry, destination changes and capability filtering",()=>{
  const f=fixture();f.workspace.access.role="owner";f.open();const root=f.root();
  root.search.value="회의";root.search.events.input();assert.equal(root.results.buttons.length,1);
  let focused=false;root.results.buttons[0].focus=()=>focused=true;
  root.search.events.keydown({key:"ArrowDown",preventDefault(){}});assert.equal(focused,true);
  root.select.value="";f.click();assert.equal(f.requests[0][1].template,"meeting_notes");assert.equal(f.requests[0][1].teamspaceId,"");
  assert.deepEqual(chooser.filter(["page"],"database"),[]);
  assert.equal(chooser.filter(["task_tracker"],"보드")[0].id,"task_tracker");
  assert.equal(chooser.filter(["linked_database"],"원본")[0].id,"linked_database");
  const empty=fixture();empty.open();empty.root().search.value="없는 항목";empty.root().search.events.input();
  assert.equal(empty.root().results.buttons.length,0);assert.equal(empty.root().empty.hidden,false);
});
