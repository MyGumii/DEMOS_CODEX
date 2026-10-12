'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const core=require('../notion_static/notion_editor_core.js');
const linked=require('../notion_static/notion_linked_databases.js');
const bridge=require('../notion_static/notion_database_bridge.js');
const fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
function fixture(){return linked.descriptor('instance',{document_id:'source-page',id:'source-db'},
  {id:'source-view',name:'Local',type:'table',config:{filters:[{property:'title',operator:'contains',value:'A'}],sorts:[],ignored:'not adopted'}},{indent:1});}

test('linked descriptors survive normalization without invented row/property caches',()=>{
  const block=fixture();assert.notEqual(block.props.active_view_id,'source-view');
  assert.deepEqual(core.normalizeBlock(core.normalizeBlock(block)),block);
  assert.deepEqual(block.props.views[0].config,{filters:[{property:'title',operator:'contains',value:'A'}],sorts:[]});
  assert.equal(block.content,'');assert.equal(block.props.indent,1);
  assert.deepEqual(bridge.diffPage([core.normalizeBlock(block)],[]),[]);
});

test('linked database full page opens, labels its breadcrumb, and returns focus to its view',()=>{
  const block=fixture(),page={id:'instance-page'};
  const breadcrumb={children:[],append(...children){this.children.push(...children);}};
  let renders=0,focusSelector='',focusCount=0;
  const context={State:{pageData:{page,blocks:[block]},databaseFullPageBlockId:'',activePageId:page.id},
    DATABASE_BLOCK_TYPES:new Set(['table']),window:{NotionLinkedDatabases:linked},
    document:{createElement:()=>({setAttribute(){}}),querySelector:selector=>{
      focusSelector=selector;
      return selector==='[data-db-title]'?null:{focus(){focusCount++;}};}},
    CSS:{escape:value=>value},URL,$:(selector)=>selector==='#breadcrumb'?breadcrumb:{scrollTo(){}},
    renderBreadcrumb:()=>{breadcrumb.children=[];},closeDrawer:()=>{},
    renderBlocks:()=>{renders++;},renderPageLists:()=>{},requestAnimationFrame:callback=>callback(),
    EmbeddedEditorFrame:false,
    location:{origin:'http://localhost',href:'http://localhost/page/instance-page'},
    history:{pushState:(_,__,route)=>{context.location.href=new URL(route,context.location.origin).href;},
      replaceState:(_,__,route)=>{context.location.href=new URL(route,context.location.origin).href;}}};
  vm.createContext(context);
  for(const [start,end] of [
    ['function renderDatabaseFullPageBreadcrumb(','function applyDatabaseFullPagePresentation('],
    ['function openDatabaseFullPage(','function independentDatabaseRoute('],
    ['let DatabaseFullPageReturnFocus = null;','function showDatabaseTemplatePicker('],
  ])vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
  context.renderDatabaseFullPageBreadcrumb(block);
  assert.equal(breadcrumb.children.at(-1).textContent,'연결된 데이터베이스');
  assert.equal(context.openLinkedDatabaseFullPage(block.id),true);
  assert.equal(context.State.databaseFullPageBlockId,block.id);
  assert.equal(new URL(context.location.href).searchParams.get('linked_db'),block.id);
  assert.match(focusSelector,/data-linked-database/);
  assert.equal(context.closeDatabaseFullPage(),true);
  assert.equal(context.State.databaseFullPageBlockId,'');
  assert.equal(new URL(context.location.href).searchParams.has('linked_db'),false);
  assert.match(focusSelector,/data-linked-open-full-page/);
  assert.equal(focusCount,2);
  assert.equal(renders,2);
  assert.equal(context.State.pageData.blocks[0],block);
});

test('unrelated editor mutations and row lookup do not alter linked descriptors',()=>{
  const block=fixture(),before=JSON.stringify(block),state={pageData:{page:{collaborative_editing:true},blocks:[block]},liveCollaboration:{connected:true},editRevision:0};
  const context={State:state,window:{NotionLinkedDatabases:linked},DATABASE_BLOCK_TYPES:new Set(['table']),
    syncActiveDatabaseView:()=>{throw Error('must not flatten linked view configs');},EditorHost:{notifyLocalMutation:()=>{}},scheduleCollaborativeDatabaseSync:()=>{},setStatus:()=>{}};
  vm.createContext(context);
  for(const [start,end] of [['function markDirty()','function scheduleSave()'],['function databaseRows(','function databasePropertyId(']])
    vm.runInContext(source.slice(source.indexOf(start),source.indexOf(end)),context);
  context.markDirty();assert.equal(context.databaseRows(block).length,0);assert.equal(context.databaseProperties(block).length,0);
  assert.equal(JSON.stringify(block),before);assert.equal(state.editRevision,1);
});

test('canceling the linked source picker leaves the slash text and original block intact',()=>{
  const block={id:'paragraph',type:'paragraph',content:'/연결',props:{}},before=JSON.stringify(block);let picked;
  const context={State:{activePageId:'page',pageData:{blocks:[block]},slashToken:{blockId:'paragraph',start:0,end:3,text:'/연결'}},
    closeBlockMenu:()=>{},activePageCanEdit:()=>true,NON_ADOPTED_BLOCK_TYPES:new Set(),DATABASE_BLOCK_TYPES:new Set(['table']),
    canEditDatabaseSchema:()=>true,openLinkedDatabasePicker:(...args)=>{picked=args;}};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function chooseBlockType('),source.indexOf('function slashPreferences(')),context);
  context.chooseBlockType('linked_database',0,'slash');
  assert.equal(picked[0],0);assert.equal(picked[1],'slash');assert.equal(picked[2].text,'/연결');assert.equal(JSON.stringify(block),before);
});

test('plus opens the linked source picker against its inserted blank and keeps that blank until a source is chosen',()=>{
  const block={id:'anchor',type:'paragraph',content:'keep',props:{indent:1}},before=JSON.stringify(block);
  const blank={id:'blank',type:'paragraph',content:'',props:{indent:1}};
  let picked,closed=0;
  const context={State:{activePageId:'page',pageData:{blocks:[blank,block]},insertedBlockMenu:{pageId:'page',blockId:'blank'}},
    closeBlockMenu:()=>{closed++;},activePageCanEdit:()=>true,
    NON_ADOPTED_BLOCK_TYPES:new Set(),DATABASE_BLOCK_TYPES:new Set(['table']),canEditDatabaseSchema:()=>true,
    openLinkedDatabasePicker:(...args)=>{picked=args;}};
  vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function chooseBlockType('),source.indexOf('function slashPreferences(')),context);
  context.chooseBlockType('linked_database',1,'inserted');
  assert.equal(closed,1);assert.deepEqual(picked,[0,'slash',null]);
  assert.equal(JSON.stringify(block),before);assert.equal(context.State.pageData.blocks.length,2);
  assert.equal(JSON.stringify(context.State.pageData.blocks[0]),JSON.stringify(blank));
});

test('database-only linked picker preserves the blank on a lost response and commits once on retry',async()=>{
  class Element {
    constructor(){this.children=[];this.hidden=false;this.textContent='';this.value='';}
    append(...nodes){this.children.push(...nodes);}
    replaceChildren(...nodes){this.children=[...nodes];}
    contains(node){return node===results;}
    addEventListener(){}
    focus(){}
  }
  const modal=new Element(),search=new Element(),results=new Element(),more=new Element(),background=new Element();
  const nodes={'#modalBody':modal,'#linkedSourceSearch':search,'#linkedSourceResults':results,
    '#linkedSourceMore':more,'#modalBg':background};
  const blank={id:'blank',type:'paragraph',content:'',props:{indent:1}};
  const state={user:{id:'member'},activePageId:'host',pageData:{blocks:[blank]},
    navigation:{workspaceId:'ws',sequence:1}};
  const calls=[];let posts=0,saves=0;
  const context={State:state,location:{origin:'http://test'},structuredClone,Date,
    document:{createElement:()=>new Element()},$:(selector)=>nodes[selector],
    openModal:()=>{},closeModal:()=>{background.hidden=true;},setStatus:()=>{},
    personalLinkedDatabaseAvailable:()=>false,collaborativeDatabaseActive:()=>true,activePageCanEdit:()=>true,
    linkedDatabaseScope:()=>({data:state.pageData,userId:'member',workspaceId:'ws',pageId:'host',navigationSequence:1}),
    linkedDatabaseScopeCurrent:scope=>scope.data===state.pageData&&scope.pageId===state.activePageId,
    flushPageDraft:async()=>true,requestLiveSync:()=>{},
    navigationOperationId:()=> 'insert-once',
    window:{NotionLinkedDatabases:{localView:view=>({type:view.type,name:view.name,config:view.config||{}})}},
    DatabaseBridge:{ADOPTED_VIEW_TYPES:['table']},
    EditorCore:{createBlock:()=>({id:'linked-once'}),removeBlocks:(rows,indexes)=>rows.filter((_,i)=>!indexes.includes(i))},
    CollaborativeEditorBridge:{stopCapturing:()=>{}},captureEditorHistory:()=>{},
    markDirty:()=>{},renderBlocks:()=>{},savePage:async()=>{saves++;},
    api:async(url,options)=>{
      calls.push({url,body:options?.body});
      if(url.includes('/database-sources?'))return {items:[{id:'db-only',title:'Shared'}],next_after:null};
      if(url.endsWith('/databases/db-only'))return {database:{id:'db-only',title:'Shared',views:[]}};
      if(url.endsWith('/collaboration'))return {page:{document_sequence:4,workspace_epoch:2,document_epoch:3},blocks:[blank]};
      if(url.endsWith('/linked-databases')){
        posts++;
        if(posts===1)throw Error('응답 유실');
        state.pageData.blocks.push({id:options.body.block_id,type:'table',content:'',props:{linked_database:{database_id:'db-only'}}});
        return {target_id:options.body.block_id,document_id:'host'};
      }
      throw Error(`unexpected request: ${url}`);
    }};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('const PendingScopedLinkedBlockInserts = new Map();'),
    source.indexOf('function blockEditorHtml(',source.indexOf('const PendingScopedLinkedBlockInserts = new Map();'))),context);
  await context.openLinkedDatabasePicker(0,'slash',null);
  await results.children[0].onclick();
  results.children[1].onclick();
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(posts,1);
  assert.deepEqual(state.pageData.blocks,[blank]);
  assert.equal(results.children[1].textContent,'다시 시도');
  results.children[1].onclick();
  await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(posts,2);
  assert.deepEqual(state.pageData.blocks.map(block=>block.id),['linked-once']);
  assert.equal(saves,1);
  assert.deepEqual(calls.filter(call=>call.url.endsWith('/linked-databases')).map(call=>call.body.operation_id),
    ['insert-once','insert-once']);
  assert.equal(calls.find(call=>call.url.endsWith('/linked-databases')).body.after_block_id,null);
});

test('database-only linked table files use a database-scoped download URL',()=>{
  const context={State:{navigation:{workspaceId:'ws'}},
    escapeAttr:value=>String(value),escapeHtml:value=>String(value),
    safeExternalUrl:value=>value,databaseFilePreviewKind:()=>'',
    databaseFileActionsHtml:()=>'',window:{NotionLinkedDatabases:linked}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function notionFileUrl('),
    source.indexOf('function databaseFilePreviewKind(')),context);
  vm.runInContext(source.slice(source.indexOf('function databasePropertyInput('),
    source.indexOf('async function uploadDatabasePropertyFiles(')),context);
  const property={id:'attachment',type:'files'},row={attachment:[{file_id:'file_one',name:'One'}]};
  const scoped={props:{linked_database:{database_id:'db-only'}}};
  const old={props:{linked_database:{document_id:'parent',database_id:'db-only'}}};
  const expected='/api/notion/workspaces/ws/databases/db-only/files/file_one';
  assert.ok(context.databasePropertyInput(scoped,property,row,0,false).includes(expected));
  assert.ok(context.databasePropertyInput(old,property,row,0,false).includes('/api/notion/collaboration/files/file_one'));
  assert.equal(context.databaseFileScope({database_id:'db-only'},'ws').databaseId,'db-only');
  assert.equal(context.databaseFileScope({document_id:'parent',database_id:'db-only'},'ws'),null);
  assert.equal(context.linkedItemFileEditable({editable:true,scope:{itemKind:'linked',workspaceId:'ws'},
    data:{source:{database_id:'db-only'}}}),true);
  assert.equal(context.linkedItemFileEditable({editable:true,scope:{itemKind:'linked',workspaceId:'ws'},
    data:{source:{document_id:'parent',database_id:'db-only'}}}),false);
});

test('database-only linked item body media uses the database file route',()=>{
  let model,options;
  const slot={},editor={dispose:()=>{},render:()=>{}};
  const context={ItemBodyUi:null,LinkedDatabaseItems:{snapshot:()=>model},
    window:{NotionDatabaseItemEditor:{create:(_slot,settings)=>{options=settings;return editor;}},
      NotionDatabaseItemBody:{normalize:value=>value,equal:(a,b)=>JSON.stringify(a)===JSON.stringify(b)}},
    EditorCore:{},location:{origin:'http://test'},
    escapeHtml:value=>String(value),sanitizeRichHtml:value=>String(value),
    blockRichHtml:()=>'',syncEditorBlock:()=>{},caretOffset:()=>0,setCaretOffset:()=>{}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function notionFileUrl('),
    source.indexOf('function databaseFilePreviewKind(')),context);
  vm.runInContext(source.slice(source.indexOf('function syncDatabaseItemBody('),
    source.indexOf('async function applyDatabaseTemplateToEmptyItem(')),context);
  const scope={workspaceId:'ws',pageId:'host',itemKind:'linked'};
  model={scope,rowId:'row',blockId:'link',data:{source:{database_id:'db-only'},
    database:{capabilities:{row_content:true},rows:[{id:'row',body:{blocks:[]}}]}}};
  context.syncDatabaseItemBody(model,{querySelector:()=>slot});
  assert.equal(options.fileUrl('file_one'),
    '/api/notion/workspaces/ws/databases/db-only/files/file_one');
  model.data.source={document_id:'parent',database_id:'db-only'};
  assert.equal(options.fileUrl('file_one'),'/api/notion/collaboration/files/file_one');
});

test('database-only file upload uses a fresh database witness and checks its receipt',async()=>{
  let options;
  const scope={workspaceId:'ws',userId:'viewer'},source={
    contract:'code_assist.notion.database_sharing.v1',workspace_id:'ws',database_id:'db-only',
    database:{id:'db-only'},role:'edit_content',document_sequence:9,
    workspace_epoch:2,document_epoch:3,database_epoch:4};
  const context={State:{csrfToken:'csrf'},linkedDatabaseScopeCurrent:()=>true,
    api:async url=>{assert.equal(url,'/api/notion/workspaces/ws/databases/db-only');return source;},
    authenticatedFetch:()=>{},ResumableFileUpload:{upload:async value=>{
      options=value;
      return {file:{contract:'code_assist.notion.collaborative_file_authority.v1',
        database_id:'db-only',file_id:'file-one',id:'file-one',state:'active',document_sequence:10}};
    }}};
  vm.createContext(context);
  vm.runInContext(sourceCode('async function uploadDatabaseResource(', 'async function workspaceCustomEmojiAvailable('),context);
  const receipt=await context.uploadDatabaseResource({name:'one.png'},scope,'db-only',{aborted:false});
  assert.equal(receipt.id,'file-one');
  assert.equal(options.baseUrl,'/api/notion/workspaces/ws/databases/db-only/files');
  assert.equal(options.databaseEpoch,4);
  assert.equal(options.documentSequence,9);
  assert.equal(options.databaseId,'db-only');
});

test('linked item file selection uploads to the database before saving its cell',async()=>{
  const events=[],scope={itemKind:'linked',workspaceId:'ws',pageId:'host'},ui={fileUploadBusy:false};
  const model={editable:true,scope,data:{source:{database_id:'db-only'},
    database:{rows:[{id:'row',cell_versions:{attachment:0}}]}},
    blockId:'linked-block',rowId:'row',drafts:new Map(),item:{attachment:[]}};
  const input={files:[{name:'one.png'}],value:'selected',disabled:false,isConnected:true,
    closest:()=>({dataset:{linkedItemFiles:'attachment'}})};
  const context={LinkedDatabaseItems:{snapshot:()=>model,save:async()=>{events.push('save');return true;},
    draft:(field,value,version)=>{events.push(['draft',field,value,version]);return true;}},
    initializeLinkedItemPeek:()=>ui,linkedItemFileEditable:()=>true,
    linkedDatabaseScopeCurrent:()=>true,flushPageDraft:async()=>true,
    uploadDatabaseResource:async()=>{events.push('upload');return {id:'file-one',original_name:'one.png',size_bytes:3};},
    LinkedDatabaseViews:{refresh:async()=>{events.push('refresh');}},
    refreshOwnedDatabaseItem:()=>{throw Error('must not refresh owner page');},
    refreshOpenedPageAfterMutation:()=>{throw Error('must not open owner page');},
    loadCollaborativeFiles:()=>{throw Error('must not list owner files');},
    setStatus:()=>{},AbortController};
  vm.createContext(context);
  vm.runInContext(sourceCode('async function uploadLinkedItemPropertyFiles(', 'function initializeLinkedItemPeek('),context);
  await context.uploadLinkedItemPropertyFiles(input);
  assert.deepEqual(events.map(event=>Array.isArray(event)?event[0]:event),
    ['save','upload','refresh','draft','save']);
  assert.equal(events[3][2][0].file_id,'file-one');
  assert.equal(events[3][3],0);
  assert.equal(ui.fileUploadBusy,false);
});

function sourceCode(start,end){return source.slice(source.indexOf(start),source.indexOf(end,source.indexOf(start)));}

test('canonical title changes never PATCH a legacy page; ordinary page titles still do',async()=>{
  const start=source.indexOf('  row.querySelectorAll("[data-db-row][data-db-field]").forEach(input => {');
  const end=source.indexOf('  row.querySelectorAll("[data-db-row][data-db-date-end]")',start);
  assert.ok(start>0&&end>start);
  for(const mode of ['canonical','canonical-without-marker','ordinary']){
    let change;const requests=[],item={page_id:'row-a',title:'Before'};
    if(mode==='canonical')item._canonical_row_id='row-a';
    const input={dataset:{dbRow:'0',dbField:'title'},type:'text',value:'After',addEventListener:(_,fn)=>{change=fn;}};
    let dirty=0;
    const block={props:{rows:[item]}};
    const context={row:{querySelectorAll:selector=>selector.includes('[data-db-field]')?[input]:[]},block,State:{user:{id:'owner'},pageData:{databases:[]}},
      databaseRows:()=>[item],databaseProperties:()=>[{id:'title',type:'title'}],canEditDatabaseSchema:()=>true,
      currentDatabaseInputRow:()=>()=>({block,item}),
      collaborativeDatabaseActive:()=>mode!=='ordinary',markDirty:()=>{dirty++;},captureEditorHistory:()=>{},
      flushPageDraft:async()=>true,api:async(...args)=>{requests.push(args);return {};},
      refreshOpenedPageAfterMutation:async()=>{},loadPages:async()=>{},renderBlocks:()=>{},
      setStatus:()=>{throw Error('unexpected save error');}};
    vm.createContext(context);vm.runInContext(source.slice(start,end),context);await change();
    assert.equal(item.title,'After');assert.equal(dirty,1);
    assert.equal(requests.length,mode==='ordinary'?1:0);
    if(requests.length){assert.equal(requests[0][0],'/api/notion/pages/row-a');assert.equal(requests[0][1].body.title,'After');}
  }
});
