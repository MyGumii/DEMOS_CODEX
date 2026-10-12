'use strict';
const assert=require('node:assert/strict'),{test}=require('node:test'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
function code(name){const start=source.indexOf(`function ${name}(`);return source.slice(start,source.indexOf('\nfunction ',start+10));}
const deferred=()=>{let resolve;const promise=new Promise(yes=>{resolve=yes;});return {promise,resolve};};
function fixture(){
  const attributes={},calls=[],anchor={id:'pageMoreBtn',isConnected:true,focus(){calls.push('anchor-focus');}};
  const search={value:'잠금',dataset:{},selectionStart:1,selectionEnd:2};
  const button={dataset:{pageAction:'lock'},setAttribute:(key,value)=>{attributes[key]=value;}};
  const page={id:'page',can_edit:true,can_change_settings:true,settings:{locked:false}},session={};
  const menu={hidden:false,dataset:{pageId:'page',surface:'page'},pageMenuSubject:'owner',pageMenuWorkspace:'ws',
    pageMenuPage:page,pageMenuSession:session,pageMenuAnchor:anchor,scrollTop:70,
    contains:node=>[search,button].includes(node),querySelector:()=>search,querySelectorAll:()=>[button],
    setAttribute:(key,value)=>{attributes[key]=value;}};
  const State={user:{id:'owner'},activePageId:'page',navigation:{workspaceId:'ws',collaborativeSharing:true},pageData:{page,blocks:[{content:'unsaved'}]},dirty:true};
  const document={activeElement:search,body:{},getElementById:()=>null};
  const context={State,document,CSS:{escape:String},$:id=>id==='#pageMenu'?menu:anchor,
    pageIsLocked:page=>!!page?.settings?.locked,pageCanChangeSettings:page=>!!page?.can_change_settings,iconSvg:name=>`<svg>${name}</svg>`,
    closePageMenu:()=>{menu.hidden=true;calls.push('close');},openPageMenu:(...args)=>calls.push(args),
    updatePageSettings:async()=>true};
  vm.createContext(context);vm.runInContext(code('refreshOpenPageMenu'),context);vm.runInContext(code('setPageMenuSettingsPending'),context);
  vm.runInContext(code('renderPageLockState'),context);
  const start=source.indexOf('async function handlePageMenuAction(');
  vm.runInContext(source.slice(start,source.indexOf('\nconst PageSettingsIntents',start)),context);
  return {context,State,document,page,menu,search,button,anchor,attributes,calls};
}
test('UI-PAGE-MENU-STATE remote lock refresh keeps the exact page, search, caret, focus and scroll',()=>{
  const h=fixture(),fresh={...h.page,settings:{locked:true}};h.context.refreshOpenPageMenu(fresh);
  const [page,anchor,surface,restore]=h.calls[0];assert.equal(page,fresh);assert.equal(anchor,h.anchor);assert.equal(surface,'page');
  assert.deepEqual({...restore},{query:'잠금',focused:true,menuFocused:false,action:undefined,search:true,start:1,end:2,scrollTop:70});
  assert.equal(h.menu.pageMenuPage,fresh);assert.equal(h.State.dirty,true);assert.equal(h.State.pageData.blocks[0].content,'unsaved');
});
test('UI-PAGE-MENU-STATE notification for another page is ignored and context switches dismiss stale actions',()=>{
  const h=fixture();h.context.refreshOpenPageMenu({...h.page,id:'other'});assert.equal(h.calls.length,0);
  h.State.navigation.workspaceId='other';h.context.refreshOpenPageMenu(h.page);assert.deepEqual(h.calls,['close']);
});
test('UI-PAGE-MENU-STATE an older metadata response cannot restore unlocked actions after a newer lock',()=>{
  const h=fixture();h.menu.pageMenuPage={...h.page,document_sequence:5,settings:{locked:true}};
  h.context.refreshOpenPageMenu({...h.page,document_sequence:4});assert.equal(h.calls.length,0);
  assert.equal(h.menu.pageMenuPage.settings.locked,true);
});
test('UI-PAGE-MENU-STATE row rerender reanchors within its own section and never switches to another row',()=>{
  const h=fixture(),replacement={isConnected:true};h.menu.dataset.surface='row';h.menu.pageMenuRootId='ownPages';h.anchor.isConnected=false;
  h.document.getElementById=id=>{assert.equal(id,'ownPages');return {querySelector:selector=>{assert.equal(selector,'[data-page-menu="page"]');return replacement;}};};
  h.context.refreshOpenPageMenu(h.page);assert.equal(h.calls[0][1],replacement);
  h.calls.length=0;h.document.getElementById=()=>null;h.context.refreshOpenPageMenu(h.page);assert.deepEqual(h.calls,['close','anchor-focus']);
});
test('UI-PAGE-MENU-STATE composition retains the input node and defers rebuilding while actions are inert',()=>{
  const h=fixture(),fresh={...h.page,settings:{locked:true}};h.search.dataset.imeComposing='true';
  h.context.refreshOpenPageMenu(fresh);assert.equal(h.calls.length,0);assert.equal(h.menu.pageMenuPendingRefresh,fresh);
  assert.equal(h.button.disabled,true);assert.equal(h.search.value,'잠금');
  delete h.search.dataset.imeComposing;h.context.refreshOpenPageMenu(fresh);assert.equal(h.calls.length,1);
});
test('UI-PAGE-MENU-STATE changing a setting keeps the menu open and allows only one pending request',async()=>{
  const h=fixture(),pending=deferred(),writes=[];
  h.context.updatePageSettings=async(settings,id)=>{writes.push({settings,id});await pending.promise;h.menu.pageMenuPage={...h.page,settings:{locked:true}};};
  const first=h.context.handlePageMenuAction('lock',h.page,h.anchor);
  await h.context.handlePageMenuAction('lock',h.page,h.anchor);
  assert.equal(h.menu.hidden,false);assert.equal(writes.length,1);assert.equal(h.attributes['aria-disabled'],'true');
  pending.resolve();await first;assert.equal(h.menu.pageMenuSettingsPending,false);assert.equal(h.attributes['aria-disabled'],'false');
  assert.equal(h.calls[0][0].settings.locked,true);assert.equal(writes[0].id,'page');assert.equal(writes[0].settings.locked,true);
});
test('UI-PAGE-MENU-STATE delayed save cannot restore a closed or different menu',async()=>{
  for(const change of [h=>{h.menu.hidden=true;},h=>{h.menu.pageMenuSession={};h.menu.dataset.pageId='other';}]) {
    const h=fixture(),pending=deferred();h.context.updatePageSettings=()=>pending.promise;
    const task=h.context.handlePageMenuAction('small_text',h.page,h.anchor);change(h);pending.resolve(false);await task;
    assert.equal(h.calls.length,0);
  }
});
test('UI-PAGE-MENU-STATE topbar lock is visible to readers and only actionable with settings permission',()=>{
  const h=fixture();h.context.$=()=>h.anchor;
  h.context.renderPageLockState({...h.page,settings:{locked:true}});assert.equal(h.anchor.hidden,false);assert.equal(h.anchor.disabled,false);
  assert.match(h.anchor.innerHTML,/lock/);
  h.context.renderPageLockState({...h.page,can_change_settings:false,settings:{locked:true}});assert.equal(h.anchor.hidden,false);assert.equal(h.anchor.disabled,true);
  h.context.renderPageLockState(h.page);assert.equal(h.anchor.hidden,true);
  h.context.renderPageLockState(null);assert.equal(h.anchor.hidden,true);
});
test('UI-PAGE-MENU-STATE removing lock closes its popover and returns keyboard focus without stealing body focus',()=>{
  const h=fixture();h.menu.dataset.surface='lock';h.context.refreshOpenPageMenu(h.page);assert.deepEqual(h.calls,['close','anchor-focus']);
  const outside=fixture();outside.document.activeElement={};outside.menu.dataset.surface='lock';outside.context.refreshOpenPageMenu(outside.page);assert.deepEqual(outside.calls,['close']);
});
