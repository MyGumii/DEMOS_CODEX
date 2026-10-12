'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const Activity=require('../notion_static/notion_page_activity.js'),Sync=require('../notion_static/notion_metadata_sync.js');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const edit={actor_subject_id:'editor',document_sequence:5,edited_at:40};
test('UI-MENU-EDITOR actual editor and edit time are independent of owner and later comment time',()=>{
  const page={owner_user_id:'owner',updated_at:80,document_sequence:6,last_edit:edit};
  assert.deepEqual(Activity.footer(page,[{id:'owner',display_name:'Owner'},{id:'editor',display_name:'편집자'}],String),{byline:'편집자 최종 편집',time:'40'});
  assert.equal(Activity.footer(page,[],String).byline,'editor 최종 편집');
});
test('UI-MENU-EDITOR unknown history never invents an editor from the owner or signed-in user',()=>{
  for(const last_edit of [undefined,null,{...edit,document_sequence:7},{...edit,actor_subject_id:''},{...edit,edited_at:-1}]){
    assert.deepEqual(Activity.footer({owner_user_id:'owner',document_sequence:6,updated_at:80,last_edit},[{id:'owner'}],String),{byline:'최종 편집',time:'80'});
  }
});
function fixture(){
  const page={id:'a',owner_user_id:'owner',workspace_id:'ws',document_sequence:2,updated_at:20,workspace_epoch:1,document_epoch:1};
  const input={value:'한글 검색',selectionStart:2,selectionEnd:4},foot={innerHTML:''},requests=[];
  const menu={hidden:false,dataset:{pageId:'a'},pageMenuSubject:'owner',pageMenuWorkspace:'ws',pageMenuPage:{...page},querySelector:()=>foot};
  const State={user:{id:'owner'},users:[{id:'editor',display_name:'Editor <safe>'}],navigation:{sequence:1,workspaceId:'ws'},pages:[{...page}],pageData:{page,blocks:[{content:'unsaved'}]},sidePeek:{data:null},dirty:true};
  const scope={subject_id:'owner',workspace_id:'ws'},key=Sync.scopeKey(scope);
  const context={State,document:{activeElement:input},window:{NotionMetadataSync:Sync,NotionPageActivity:Activity},$:()=>menu,
    pageMetadataScope:()=>({subject_id:State.user.id,workspace_id:State.navigation.workspaceId}),formatTime:String,
    escapeHtml:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),renderPageActivity(){},
    api:url=>new Promise(resolve=>requests.push({url,resolve}))};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('async function refreshVisiblePageActivity('),source.indexOf('function installPageMetadataSync(')),context);
  const response={page:{...page,document_sequence:6,updated_at:80,last_edit:edit}};
  return {context,State,page,menu,foot,input,requests,response,refresh:()=>context.refreshVisiblePageActivity(['a'],key)};
}
test('UI-MENU-EDITOR late activity updates only menu footer and preserves text, focus and body versions',async()=>{
  const f=fixture(),body=f.State.pageData.blocks,p=f.refresh();f.requests[0].resolve(f.response);await p;
  assert.equal(f.foot.innerHTML,'Editor &lt;safe&gt; 최종 편집<br>40');assert.equal(f.page.document_sequence,2);
  assert.equal(f.page.activity_document_sequence,6);assert.deepEqual(f.page.last_edit,edit);
  assert.equal(f.State.pageData.blocks,body);assert.equal(f.State.dirty,true);assert.equal(f.context.document.activeElement,f.input);assert.equal(f.input.value,'한글 검색');assert.equal(f.input.selectionStart,2);
});
test('UI-MENU-EDITOR stale source responses cannot replace current metadata or another menu footer',async()=>{
  const f=fixture();f.page.activity_document_sequence=8;f.page.last_edit={...edit,actor_subject_id:'newer',document_sequence:8};
  f.menu.pageMenuPage=f.page;let p=f.refresh();f.requests[0].resolve(f.response);await p;assert.equal(f.page.last_edit.actor_subject_id,'newer');
  f.foot.innerHTML='other menu';p=f.refresh();f.menu.dataset.pageId='b';f.requests[1].resolve(f.response);await p;assert.equal(f.foot.innerHTML,'other menu');
  p=f.refresh();f.State.navigation.workspaceId='other';f.requests[2].resolve(f.response);await p;assert.equal(f.foot.innerHTML,'other menu');
});
test('UI-MENU-EDITOR an open menu polls one source, serializes slow reads and stops on closure',async()=>{
  let current=true,visible=true,reads=0,release;const timers=new Map();let next=0;
  const watcher=Activity.watch({current:()=>current,visible:()=>visible,read:()=>{reads++;return new Promise(r=>release=r);},
    setTimeout:(fn,delay)=>{assert.equal(delay,15000);timers.set(++next,fn);return next;},clearTimeout:id=>timers.delete(id)});
  assert.equal(reads,1);assert.equal(timers.size,0);release();await new Promise(r=>setImmediate(r));assert.equal(timers.size,1);
  let tick=timers.values().next().value;timers.clear();visible=false;await tick();assert.equal(reads,1);assert.equal(timers.size,1);
  tick=timers.values().next().value;timers.clear();visible=true;const reading=tick();assert.equal(reads,2);
  watcher.stop();release();await reading;assert.equal(timers.size,0);
  current=false;Activity.watch({current:()=>current});assert.equal(reads,2);
});
test('UI-MENU-ANCHOR sidebar redraw reattaches an open row menu to the current button',()=>{
  const page={id:'a',document_sequence:2},old={isConnected:false},fresh={isConnected:true},focus={dataset:{pageAction:'rename'}},opened=[];
  const menu={hidden:false,dataset:{pageId:'a',surface:'row'},pageMenuSubject:'owner',pageMenuWorkspace:'ws',pageMenuPage:page,
    pageMenuAnchor:old,pageMenuRootId:'ownPages',contains:x=>x===focus,querySelector:()=>null,scrollTop:12};
  const nodes=new Map([['#pageMenu',menu],['#ownPages',{querySelector:()=>fresh}]]);
  const context={State:{user:{id:'owner'},navigation:{enabled:false,workspaceId:'ws'},pages:[page],legacyDatabaseFavorites:new Set()},CSS:{escape:String},
    $:id=>nodes.get(id)||{},document:{activeElement:focus,getElementById:id=>nodes.get('#'+id)},
    visiblePages:()=>[],ownPages:()=>[page],sharedPages:()=>[],pageIsFavorite:()=>false,
    closePageMenu:()=>{throw Error('Existing row remains available');},openPageMenu:(p,a,s,restore)=>opened.push({p,a,s,restore})};
  for(const name of ['pageListFocusSnapshot','restorePageListFocus','prepareSidebarDatabaseRender','renderPageLifecycleStatus','renderPageCopyStatus','renderDatabaseCopyStatus','renderDatabaseRelationPairStatus','renderDatabaseMoveStatus','renderDatabaseTrashStatus','renderDatabaseRowTrashStatus','renderDatabaseRowMoveStatus','renderSyncedDetachStatus','renderPageGroup','renderFavoritePageGroup','renderSidebarPageGroup','renderRecentPageGroup','bindPageListRootDrop','syncCollaborativeSidebarPresentation','applyNavigationSections','refreshPageReferenceDisplays'])context[name]=()=>{};
  vm.createContext(context);
  for(const [a,b] of [['function renderPageLists(','function applyNavigationSections('],['function refreshOpenPageMenu(','function setPageMenuSettingsPending(']])vm.runInContext(source.slice(source.indexOf(a),source.indexOf(b)),context);
  context.renderPageLists();assert.equal(opened.length,1);assert.equal(opened[0].a,fresh);assert.equal(opened[0].p,page);
  assert.equal(opened[0].restore.action,'rename');assert.equal(opened[0].restore.focused,true);assert.equal(opened[0].restore.scrollTop,12);
});
