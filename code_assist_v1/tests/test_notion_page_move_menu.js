"use strict";
// UI-MOVE-003: exercise the actual move-menu handler across asynchronous failures.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../notion_static/notion.js'),'utf8');
function fixture(collaborative=true) {
  const state={pageData:{page:{}},sidePeek:{},navigation:{collaborativeSharing:collaborative},collapsedPageIds:new Set()};
  const requestContext={busy:false},menu={movePageRequest:requestContext,setAttribute(){},removeAttribute(){}};
  const main={inert:false},peek={inert:false},input={disabled:false},options=[{disabled:false},{disabled:false}];
  const errorLabel={hidden:true,children:[],appendChild(child){this.children.push(child);}};
  Object.defineProperty(errorLabel,'textContent',{get(){return this.text;},set(value){this.text=value;this.children=[];}});
  const stats={posts:0,refreshes:0,closed:0,scope:true,statuses:[]};
  const ctx={State:state,requestContext,menu,input,initialMove:null,results:{querySelectorAll:()=>options},option:{dataset:{moveParent:'parent'}},page:{id:'child'},treeId:'tree',
    sameScope:()=>stats.scope,saveCollapsedPages(){},focusPageTreeRow(){},
    closeMovePageMenu(){stats.closed++;menu.movePageRequest=null;},setStatus(text){stats.statuses.push(text);},
    $:selector=>({'#pageView':main,'#sidePeek':peek,'#movePageError':errorLabel}[selector]),
    document:{createElement:()=>({addEventListener(type,callback){this[type]=callback;}})},
    movePageSafely:async()=>{stats.posts++;return {workspace_epoch:5};},
    refreshAfterCollaborativeMove:async()=>{stats.refreshes++;},loadPages:async()=>{},
  };
  vm.createContext(ctx);
  const start=source.indexOf('const MovePageIntents = new Map()');
  vm.runInContext(source.slice(start,source.indexOf('async function flushPageMoveEditors()',start)),ctx);
  const handlerStart=source.indexOf('async function moveOrRefresh(');
  const actualHandler=vm.runInContext('('+source.slice(handlerStart,source.indexOf('\n  const renderResults',handlerStart))+')',ctx);
  const handler=(body={parent_id:'parent'})=>actualHandler(body);
  return {ctx,state,stats,handler,requestContext,menu,main,peek,input,options,errorLabel};
}
(async()=>{
  const pending=fixture();let finish;
  pending.ctx.movePageSafely=()=>{pending.stats.posts++;return new Promise(resolve=>finish=resolve);};
  const first=pending.handler();
  assert.equal(pending.main.inert,true);
  assert.equal(pending.peek.inert,true);
  assert.ok(pending.input.disabled&&pending.options.every(option=>option.disabled));
  await pending.handler();
  assert.equal(pending.stats.posts,1,'double click cannot start a second move');
  finish({workspace_epoch:5});await first;
  assert.equal(pending.main.inert,false);
  assert.equal(pending.stats.closed,1);

  const failedRefresh=fixture();
  failedRefresh.ctx.refreshAfterCollaborativeMove=async()=>{if(++failedRefresh.stats.refreshes===1)throw new Error('offline');};
  await failedRefresh.handler();
  assert.equal(failedRefresh.stats.posts,1);
  assert.match(failedRefresh.errorLabel.textContent,/이동은 저장됐지만/);
  assert.equal(failedRefresh.main.inert,false,'failed refresh restores the editor');
  assert.ok(failedRefresh.input.disabled,'saved move destinations stay disabled');
  assert.equal(failedRefresh.errorLabel.children[0].textContent,'화면 갱신 다시 시도');
  await failedRefresh.errorLabel.children[0].click();
  assert.equal(failedRefresh.stats.posts,1,'refresh retry never repeats the saved mutation');
  assert.equal(failedRefresh.stats.refreshes,2);
  assert.equal(failedRefresh.stats.closed,1);

  const rejected=fixture();
  rejected.ctx.movePageSafely=async()=>{throw new Error('ACK pending');};
  await rejected.handler();
  assert.match(rejected.errorLabel.textContent,/페이지 이동 실패/);
  assert.equal(rejected.main.inert,false);
  assert.equal(rejected.input.disabled,false);
  assert.equal(rejected.requestContext.busy,false);

  const switched=fixture();
  switched.ctx.movePageSafely=async()=>{switched.stats.scope=false;return {workspace_epoch:5};};
  await switched.handler();
  assert.equal(switched.main.inert,false,'scope change also releases held inputs');
  assert.equal(switched.stats.refreshes,0);
  assert.equal(switched.stats.statuses.length,0,'an old menu cannot report status on another workspace');

  const ordinary=fixture(false);
  ordinary.ctx.movePageSafely=async()=>{ordinary.stats.posts++;const error=new Error('parent refresh failed');error.moveSaved=true;error.result={page:{id:'child'}};throw error;};
  ordinary.ctx.refreshAfterOrdinaryMove=async()=>{ordinary.stats.refreshes++;};
  await ordinary.handler();
  await ordinary.errorLabel.children[0].click();
  assert.equal(ordinary.stats.posts,1);
  assert.equal(ordinary.stats.refreshes,1,'ordinary retry refreshes the open documents, not just the sidebar');
  assert.equal(ordinary.stats.closed,1);
  console.log('UI-MOVE-003: busy/ACK/scope cleanup and refresh-only retry preserve a single saved move');
})().catch(error=>{console.error(error);process.exitCode=1;});
