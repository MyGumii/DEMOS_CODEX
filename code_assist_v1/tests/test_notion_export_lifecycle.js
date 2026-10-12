'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
function harness(){
  const stopped=[],started=[],status=[],editor={},side={},flags={};
  const context={hasPendingPageSettings:()=>flags.settings,hasPendingPageLifecycle:()=>flags.lifecycle,
    embeddedEditorState:()=>editor,State:{activePageId:'page-1',sidePeek:side},LinkedDatabaseItems:{pending:()=>flags.database},
    stopLiveCollaboration:options=>stopped.push(options),startLiveCollaboration:async()=>{started.push(true);},setStatus:(...args)=>status.push(args)};
  vm.createContext(context);
  const start=source.indexOf('function protectPendingPageOnUnload('),end=source.indexOf('function aiHomeIdFromPath()',start);
  assert.ok(start>=0&&end>start);vm.runInContext(source.slice(start,end),context);
  const unload=()=>{let prevented=false;const event={preventDefault:()=>prevented=true};context.protectPendingPageOnUnload(event);return {prevented,returnValue:event.returnValue};};
  return {context,stopped,started,status,editor,side,flags,unload};
}
test('UI-EXPORT-LIFECYCLE download or cancelled navigation never stops live editing',()=>{
  const h=harness();assert.deepEqual(h.unload(),{prevented:false,returnValue:undefined});assert.deepEqual(h.stopped,[]);
  h.editor.dirty=true;assert.deepEqual(h.unload(),{prevented:true,returnValue:''});assert.deepEqual(h.stopped,[]);
});
test('UI-EXPORT-LIFECYCLE pending settings, lifecycle, IME, side peek and database writes remain protected',()=>{
  for(const prepare of [h=>h.flags.settings=true,h=>h.flags.lifecycle=true,h=>h.editor.composing=true,
    h=>h.side.dirty=true,h=>h.side.composing=true,h=>h.side.embedded={state:()=>({dirty:true})},
    h=>h.side.parentLinkFlight=Promise.resolve(),h=>h.flags.database=true]){
    const h=harness();prepare(h);assert.equal(h.unload().prevented,true);assert.deepEqual(h.stopped,[]);
  }
});
test('UI-EXPORT-LIFECYCLE actual departure preserves editor state and bfcache return reconnects',async()=>{
  const h=harness();h.context.suspendPageCollaboration();assert.equal(h.stopped[0].preserveEditingState,true);
  h.context.resumePageCollaboration({persisted:false});assert.deepEqual(h.started,[]);
  h.context.resumePageCollaboration({persisted:true});await Promise.resolve();assert.deepEqual(h.started,[true]);
  h.context.State.activePageId='';h.context.resumePageCollaboration({persisted:true});assert.equal(h.started.length,1);
});
test('UI-EXPORT-LIFECYCLE reconnect errors are visible instead of silently dropping the connection',async()=>{
  const h=harness();h.context.startLiveCollaboration=async()=>{throw Error('unavailable');};
  h.context.resumePageCollaboration({persisted:true});await Promise.resolve();await Promise.resolve();
  assert.equal(h.status.length,1);assert.equal(h.status[0][1],'error');assert.match(h.status[0][0],/unavailable/);
});
