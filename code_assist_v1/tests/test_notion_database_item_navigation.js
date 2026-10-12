'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const items=require('../notion_static/notion_linked_database_items.js');
function fixture(){
  const scope={pageId:'page'},data={source:{document_id:'page',database_id:'db'},database:{rows:[]}},
    block={props:{rows:[{_canonical_row_id:'a',title:'A'},{_canonical_row_id:'b',title:'B'}],properties:[]}},modes=[],saves=[],closed=[];
  let live=true,dirty=true,saveResult=false,gate;
  const host={current:()=>live,canEdit:()=>true,rows:b=>b.props.rows,properties:b=>b.props.properties,visibleRows:b=>b.props.rows.map(item=>({item})),
    render:()=>{},status:()=>({}),hasBody:()=>true,bodyPending:()=>dirty,closed:s=>closed.push(s),modeChanged:(s,push)=>modes.push([s.mode,s.rowId,push]),
    saveBody:async s=>{saves.push(s.rowId);if(gate)await gate;if(saveResult)dirty=false;return saveResult;}};
  const c=items.create(host);
  return {c,scope,block,data,modes,saves,closed,open:()=>c.open(scope,'block',block,data,'a'),allow:()=>saveResult=true,leave:()=>live=false,gate:p=>gate=p};
}
test('UI-ITEM-BODY side/full layout retains dirty and uncertain drafts without sending a save',async()=>{
  const f=fixture();await f.open();assert.equal(f.c.pending(),true);assert.equal(await f.c.mode('full'),true);
  assert.equal(f.c.snapshot().rowId,'a');assert.equal(f.c.snapshot().mode,'full');assert.equal(f.saves.length,0);
  assert.equal(await f.c.close(),false);assert.equal(f.c.snapshot().rowId,'a');
  assert.equal(await f.c.mode('side'),true);assert.equal(f.c.pending(),true);assert.equal(f.saves.length,1);
  f.leave();assert.equal(await f.c.mode('full'),false);
});
test('UI-ITEM-BODY row navigation waits for body save and retains full-page identity',async()=>{
  const f=fixture();await f.open();await f.c.mode('full');assert.equal(await f.c.navigate(1),false);assert.equal(f.c.snapshot().rowId,'a');
  f.allow();let release;f.gate(new Promise(r=>release=r));const moving=f.c.navigate(1);await Promise.resolve();assert.equal(f.c.snapshot().rowId,'a');
  release();assert.equal(await moving,true);assert.equal(f.c.snapshot().rowId,'b');assert.equal(f.c.snapshot().mode,'full');assert.deepEqual(f.modes.at(-1),['full','b',false]);
  assert.equal(await f.c.close(),true);assert.equal(f.c.snapshot(),null);assert.equal(f.closed.at(-1).rowId,'b');
});
test('UI-ITEM-BODY URL mode roundtrip keeps source document and unrelated query parameters',()=>{
  const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const part=source.slice(source.indexOf('function databaseItemRoute()'),source.indexOf('async function openDatabaseItemFull()'));
  let url=new URL('http://fixture.invalid/page/page?keep=yes');const events=[];
  const ctx={URL,URLSearchParams,location:{get href(){return url.href;},get search(){return url.search;}},history:{
    pushState:(state,x,path)=>{events.push(['push',state,path]);url=new URL(path,url);},replaceState:(state,x,path)=>{events.push(['replace',state,path]);url=new URL(path,url);}}};
  vm.createContext(ctx);vm.runInContext(part,ctx);const state={scope:{pageId:'page'},data:{source:{database_id:'db'}},rowId:'a',mode:'full'};
  ctx.updateDatabaseItemRoute(state,true);assert.equal(url.searchParams.get('item_row'),'a');assert.equal(url.pathname,'/page/page');assert.equal(url.searchParams.get('keep'),'yes');
  state.rowId='b';ctx.updateDatabaseItemRoute(state,false);assert.equal(url.searchParams.get('item_row'),'b');
  state.mode='side';ctx.updateDatabaseItemRoute(state,false);assert.equal(url.search,'?keep=yes');assert.deepEqual(events.map(e=>e[0]),['push','replace','replace']);
});

test('UI-ITEM-BODY old database link opens a moved row in its current owned database',async()=>{
  const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const part=source.slice(source.indexOf('function ownedDatabaseItemRouteBlock('),source.indexOf('function ownedItemData('));
  const opened=[],modes=[],messages=[];
  const pageData={blocks:[
    {id:'source-block',props:{database_id:'source'}},
    {id:'destination-block',props:{database_id:'destination'}},
    {id:'linked-block',props:{database_id:'linked',linked_database:{database_id:'remote'}}},
  ],databases:[
    {id:'source',rows:[]},
    {id:'destination',rows:[{id:'moved-row'}]},
    {id:'linked',rows:[{id:'linked-row'}]},
  ]};
  const ctx={State:{pageData},openOwnedDatabaseItem:async(block,row)=>opened.push([block,row]),
    LinkedDatabaseItems:{snapshot:()=>({rowId:'moved-row'}),mode:async(...args)=>modes.push(args)},
    setStatus:(...args)=>messages.push(args)};
  vm.createContext(ctx);vm.runInContext(part,ctx);
  await ctx.restoreDatabaseItemRoute({database:'source',row:'moved-row'});
  assert.deepEqual(opened,[['destination-block','moved-row']]);
  assert.deepEqual(modes,[['full',false]]);
  assert.equal(messages.length,0);
  await ctx.restoreDatabaseItemRoute({database:'source',row:'missing-row'});
  await ctx.restoreDatabaseItemRoute({database:'linked',row:'linked-row'});
  assert.equal(opened.length,1);
  assert.deepEqual(messages.map(item=>item[0]),[
    '데이터베이스 항목을 찾을 수 없습니다.','데이터베이스 항목을 찾을 수 없습니다.',
  ]);
});

test('UI-ITEM-BODY old database link opens a row converted into an ordinary page',async()=>{
  const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const part=source.slice(source.indexOf('function ownedDatabaseItemRouteBlock('),source.indexOf('function ownedItemData('));
  let url=new URL('http://fixture.invalid/page/source?keep=yes&item_db=source-db&item_row=row-page&item_view=full#heading');
  const paths=[],opened=[],messages=[];
  const state={pageData:{blocks:[],databases:[]},activePageId:'source',pageLoadSequence:1,
    navigation:{workspaceId:'workspace',collaborativeSharing:true}};
  const ctx={State:state,URL,PageFocusPolicy:{PRESERVE:'preserve'},
    location:{get href(){return url.href;},get pathname(){return url.pathname;},get search(){return url.search;},get hash(){return url.hash;}},
    history:{replaceState:(_state,_title,path)=>{paths.push(path);url=new URL(path,url);}},
    api:async path=>{assert.equal(path,'/api/notion/workspaces/workspace/database-rows/row-page/location');
      return {contract:'code_assist.notion.database_row_location.v1',workspace_id:'workspace',
        document_id:'row-page',row_id:'row-page',placement:'page'};},
    pageUrl:id=>`http://fixture.invalid/page/${id}`,
    openPage:async(id,_history,focus)=>{opened.push([id,focus]);state.activePageId=id;state.pageLoadSequence++;},
    setStatus:(...args)=>messages.push(args)};
  vm.createContext(ctx);vm.runInContext(part,ctx);
  await ctx.restoreDatabaseItemRoute({database:'source-db',row:'row-page'});
  assert.equal(url.pathname,'/page/row-page');
  assert.equal(url.search,'?keep=yes');
  assert.equal(url.hash,'#heading');
  assert.equal(paths.length,1);
  assert.deepEqual(opened,[['row-page','preserve']]);
  assert.deepEqual(messages,[]);
});
