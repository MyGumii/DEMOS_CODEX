"use strict";
const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const EditorCore=require("../notion_static/notion_editor_core.js");
const source=fs.readFileSync(process.env.NOTION_FOCUS_SOURCE||require.resolve("../notion_static/notion.js"),"utf8");
const clone=value=>JSON.parse(JSON.stringify(value));
const part=(a,b)=>{const start=source.indexOf(a),end=source.indexOf(b,start);assert.ok(start>=0&&end>start);return source.slice(start,end);};

function fixture(){
  const document={body:{},documentElement:{},activeElement:null};document.activeElement=document.body;
  const State={user:{id:"owner"},navigation:{workspaceId:"ws"},activePageId:"page",pageLoadSequence:1,
    activeBlockId:"database",selectedBlockIds:new Set(),pageData:{page:{title:"Page"},blocks:[{id:"database",type:"table",props:{query:"abcdef"}}]}};
  const title={value:"Page"},outside={closest:()=>null},root={rows:[],contains:node=>root.rows.includes(node?.row),
    removeAttribute(){},setAttribute(){},
    querySelectorAll:selector=>selector===".block-row[data-block-id]"?root.rows:[],appendChild:row=>root.rows.push(row)};
  Object.defineProperty(root,"innerHTML",{set(){if(root.contains(document.activeElement))document.activeElement=document.body;root.rows=[];}});
  let options={},timer;
  function makeControl(row,attribute,value=""){
    const attributes={[attribute]:value};const control={row,disabled:false,value:"abcdef",selectionStart:null,selectionEnd:null,
      getAttribute:name=>attributes[name]??null,hasAttribute:name=>name in attributes,
      closest:selector=>selector===".database-head, .database-toolbar"?row:selector===".block-row[data-block-id]"?row:selector==="[hidden], [inert]"&&(row.hidden||options.inert)?row:null,
      getClientRects:()=>options.invisible?[]:[{}],focus:()=>{document.activeElement=control;},
      setSelectionRange:(start,end,direction)=>Object.assign(control,{selectionStart:start,selectionEnd:end,selectionDirection:direction}),
      addEventListener:(name,fn)=>{control[name]=fn;}};
    if(attribute==="data-db-search")control.setSelectionRange(0,0,"none");
    return control;
  }
  document.createElement=()=>{
    const row={dataset:{},style:{setProperty(){}},classList:{toggle(){}},addEventListener(){}};
    row.controls=[makeControl(row,"data-db-view-id","table"),makeControl(row,"data-db-view-id","board"),makeControl(row,"data-db-search"),makeControl(row,"data-db-filter")];
    if(options.removeBoard)row.controls=row.controls.filter(c=>c.getAttribute("data-db-view-id")!=="board");
    row.controls.forEach(c=>{c.disabled=Boolean(options.disabled);});
    row.querySelectorAll=selector=>row.controls.filter(c=>c.hasAttribute(selector.slice(1,-1)));
    row.querySelector=selector=>selector.startsWith('[data-action=')?{addEventListener(){}}:row.querySelectorAll(selector)[0]||null;
    return row;
  };
  const context={document,State,EditorCore,$:selector=>selector==="#blocks"?root:selector==="#pageTitle"?title:null,
    window:{NotionBlockLayout:{mount:({root,rows})=>{rows.forEach(row=>root.appendChild(row));return {columns:[]};}}},
    collaborativeEditingActive:()=>false,syncedTextHoldsFocus:()=>false,SyncedTextRenderDeferred:false,
    DATABASE_BLOCK_TYPES:new Set(["table"]),activePageCanEdit:()=>false,
    renderDatabaseSearchResults(){},blockEditorHtml:()=>"",iconSvg:()=>"",setTimeout:fn=>{timer=fn;},clearTimeout(){},focusBlock(){throw Error("Unexpected paragraph focus");}};
  for(const name of ["normalizeTabsBlock","applyDatabaseFullPagePresentation","applyBlockVisual","attachBlockExtras","bindBlockDrag","renderRemoteCursors","mountSyncedReferences","mountLinkedDatabases","rebindBlockSlashMenu"])context[name]=()=>{};
  vm.createContext(context);
  vm.runInContext(part("function captureEditorHostSelection(","function focusEditorHostBlock("),context);
  vm.runInContext(part("function renderBlocks()", "function syncSyncedBlockPeers("),context);
  vm.runInContext(`function bindSearch(block,row,index){${part('  const dbSearch = row.querySelector("[data-db-search]");','  row.querySelector("[data-db-filter]")')}}`,context);
  const controls=()=>root.rows.find(row=>row.dataset.blockId==="database").controls;
  const find=(attribute,value)=>controls().find(c=>c.hasAttribute(attribute)&&(value===undefined||c.getAttribute(attribute)===value));
  context.renderBlocks();
  return {context,State,document,root,title,outside,find,replace:next=>{options=next||{};context.renderBlocks();},runTimer:()=>timer()};
}

test("UI-DB-FOCUS redraw keeps the selected view focused by block and view identity",()=>{
  const f=fixture(),old=f.find("data-db-view-id","board");old.focus();
  f.State.pageData.blocks.unshift({id:"earlier",type:"table",props:{}});f.replace();
  assert.notEqual(f.document.activeElement,old);
  assert.equal(f.document.activeElement,f.find("data-db-view-id","board"));
});

test("UI-DB-FOCUS search redraw preserves a backwards selection rather than jumping to the end",()=>{
  const f=fixture(),search=f.find("data-db-search");search.focus();search.setSelectionRange(1,4,"backward");f.replace();
  const next=f.find("data-db-search");assert.equal(f.document.activeElement,next);
  assert.deepEqual([next.selectionStart,next.selectionEnd,next.selectionDirection],[1,4,"backward"]);
});

test("UI-DB-FOCUS removed disabled hidden or inert controls never receive restored focus",()=>{
  for(const options of [{removeBoard:true},{disabled:true},{invisible:true},{inert:true}]){
    const f=fixture();f.find("data-db-view-id","board").focus();f.replace(options);assert.equal(f.document.activeElement,f.document.body);
  }
});

test("UI-DB-FOCUS scoped snapshots cannot focus another page account workspace or load",()=>{
  for(const change of [f=>f.State.activePageId="other",f=>f.State.user.id="other",f=>f.State.navigation.workspaceId="other",f=>f.State.pageLoadSequence++]){
    const f=fixture();f.find("data-db-view-id","board").focus();const snapshot=f.context.captureDatabaseControlFocus();
    f.document.activeElement=f.document.body;change(f);assert.equal(f.context.restoreDatabaseControlFocus(snapshot),false);
    assert.equal(f.document.activeElement,f.document.body);
  }
});

test("UI-DB-FOCUS remote editor projection preserves controls and does not steal sidebar or item focus",()=>{
  const f=fixture(),host=EditorCore.createEditorHostAdapter({readDocument:()=>({page_id:"page",blocks:f.State.pageData.blocks}),
    applyProjection:()=>{f.replace();return {applied:true};},captureSelection:f.context.captureEditorHostSelection,restoreSelection:f.context.restoreEditorHostSelection});
  f.find("data-db-view-id","board").focus();const snapshot=f.context.captureEditorHostSelection();
  assert.equal(snapshot.kind,"database-control");
  host.applyRemoteProjection({page_id:"page",title:"Page",blocks:clone(f.State.pageData.blocks)});
  assert.equal(f.document.activeElement,f.find("data-db-view-id","board"));
  f.document.activeElement=f.outside;assert.equal(f.context.captureEditorHostSelection().kind,"none");
  assert.equal(f.context.restoreDatabaseControlFocus(snapshot),false);f.replace();assert.equal(f.document.activeElement,f.outside);host.dispose();
});

test("UI-DB-FOCUS search delay uses current block and keeps focus where the user moved",()=>{
  const f=fixture(),old=f.State.pageData.blocks[0],row=f.root.rows[0],search=f.find("data-db-search");
  f.context.bindSearch(old,row,0);f.State.pageData.blocks=[clone(old)];search.value="updated";search.input();
  assert.equal(f.context.databaseSearchQuery(f.State.pageData.blocks[0]),"updated");
  assert.equal(f.State.pageData.blocks[0].props.query,"abcdef");assert.equal(old.props.query,"abcdef");
  f.document.activeElement=f.outside;f.runTimer();assert.equal(f.document.activeElement,f.outside);
});

test("UI-DB-FOCUS a queued search does not redraw a newly opened page",()=>{
  const f=fixture(),row=f.root.rows[0];f.context.bindSearch(f.State.pageData.blocks[0],row,0);
  f.find("data-db-search").input();f.State.activePageId="other";f.runTimer();assert.equal(f.root.rows[0],row);
});
