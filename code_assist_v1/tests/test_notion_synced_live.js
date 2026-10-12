'use strict';
const assert = require('node:assert/strict');
const {test} = require('node:test');
const live = require('../notion_static/notion_synced_live.js');
const synced = require('../notion_static/notion_synced_references.js');
const tick = async () => {for (let i=0;i<12;i++) await Promise.resolve();};
const deferred = () => {let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return {promise,resolve,reject};};
function clock() {
  let id=0;const timers=new Map();
  return {timers, schedule:(fn,ms)=>{timers.set(++id,{fn,ms});return id;},cancel:id=>timers.delete(id),
    async next(ms){const found=[...timers].find(([,timer])=>timer.ms===ms);assert.ok(found,`missing ${ms}ms timer`);timers.delete(found[0]);found[1].fn();await tick();}};
}
const witness = (document_id='source', document_sequence=2) => ({document_id,document_sequence,workspace_id:'workspace',workspace_epoch:1,document_epoch:1,generation:1});
function observer() {
  const timing=clock(),events=[],sockets=[],issues=[],context={userId:'owner'};
  const host={...timing,current:()=>host.allowed,visible:()=>host.shown,allowed:true,shown:true,
    issue:async (ctx,doc,signal)=>{issues.push(signal);return {clientId:'client',binding:'binding',ticket:{
      subject_id:'owner',workspace_id:'workspace',document_id:'source',client_id:'client',session_binding_sha256:'binding',
      capabilities:['read'],token:'isolated-test-token',gateway_url:'ws://localhost/realtime',last_document_sequence:2,workspace_epoch:1,document_epoch:1}};},
    socket:(url,protocol)=>{
      assert.equal(protocol,'code-assist-collaboration-v1');const listeners={};
      const socket={readyState:0,sent:[],addEventListener:(name,fn)=>{listeners[name]=fn;},
        send:value=>socket.sent.push(JSON.parse(value)),close:()=>{socket.readyState=3;listeners.close?.();},
        open:()=>{socket.readyState=1;listeners.open();},message:data=>listeners.message({data:JSON.stringify(data)})};
      sockets.push(socket);return socket;
    }};
  return {host,events,sockets,issues,context,start:()=>live.watch(host,context,witness(),event=>events.push(event))};
}
const receipt=(head=2)=>({type:'live_replay_queued',next_document_sequence:head,high_water_sequence:head});
const frames=(values=[])=>({type:'frames',frames:values});

test('UI-SYNC-LIVE observer connects exact scope and only reads; head changes invalidate without using payloads',async()=>{
  const h=observer(),stop=h.start();await tick();const s=h.sockets[0];s.open();s.message({type:'connected'});
  assert.deepEqual(s.sent[0],{op:'connect',token:'isolated-test-token',subject_id:'owner',workspace_id:'workspace',page_id:'source',client_id:'client',session_binding_sha256:'binding'});
  assert.deepEqual(s.sent[1],{op:'live_replay',after_document_sequence:2,limit:1});
  s.message(receipt(4));assert.equal(h.events.at(-1).type,'changed');assert.equal(h.events.at(-1).sequence,4);
  s.message(frames([{workspace_epoch:1,page_epoch:1,document_sequence:5,payload:'never rendered'}]));
  await h.host.next(650);assert.equal(s.sent.at(-1).after_document_sequence,5);
  assert.ok(s.sent.every(message=>['connect','live_replay','drain'].includes(message.op)));
  assert.equal(JSON.stringify(h.events).includes('never rendered'),false);stop();assert.equal(h.host.timers.size,0);
});
test('UI-SYNC-LIVE source change between initial GET and ticket, or epoch change, triggers current read',async()=>{
  for (const change of [ticket=>{ticket.last_document_sequence=3;},ticket=>{ticket.document_epoch=2;}]) {
    const h=observer(),issue=h.host.issue;h.host.issue=async(...args)=>{const value=await issue(...args);change(value.ticket);return value;};
    const stop=h.start();await tick();h.sockets[0].open();h.sockets[0].message({type:'connected'});
    assert.equal(h.events.some(event=>event.type==='changed'),true);stop();
  }
});
test('UI-SYNC-LIVE disconnect clears content and reconnects; obsolete socket cannot invalidate current connection',async()=>{
  const h=observer(),stop=h.start();await tick();const first=h.sockets[0];first.open();first.message({type:'connected'});first.close();
  assert.equal(h.events.at(-1).type,'unavailable');await h.host.next(250);const second=h.sockets[1];
  second.open();second.message({type:'connected'});const count=h.events.length;
  first.message(receipt(99));assert.equal(h.events.length,count);stop();assert.equal(second.readyState,3);
});
test('UI-SYNC-LIVE malformed protocol, wrong epoch and regressing head fail closed',async()=>{
  for (const invalid of [null,{type:'connected'},frames(),receipt(1),{type:'error'}]) {
    const h=observer(),stop=h.start();await tick();const s=h.sockets[0];s.open();s.message({type:'connected'});s.message(invalid);
    assert.equal(h.events.at(-1).type,'unavailable');assert.equal(s.readyState,3);stop();
  }
  const h=observer(),stop=h.start();await tick();const s=h.sockets[0];s.open();s.message({type:'connected'});s.message(receipt());
  s.message(frames([{workspace_epoch:2,page_epoch:1,document_sequence:3}]));assert.equal(h.events.at(-1).type,'unavailable');stop();
});
test('UI-SYNC-LIVE invalid ticket cannot open a socket; stopped pending issue cannot leak one',async()=>{
  const h=observer(),issue=h.host.issue;h.host.issue=async(...args)=>{const value=await issue(...args);value.ticket.document_id='other';return value;};
  const stop=h.start();await tick();assert.equal(h.sockets.length,0);assert.equal(h.events.at(-1).type,'unavailable');stop();
  const second=observer(),pending=deferred(),normal=await second.host.issue();second.host.issue=()=>pending.promise;
  const finish=second.start();finish();pending.resolve(normal);await tick();assert.equal(second.sockets.length,0);assert.equal(second.host.timers.size,0);
});
test('UI-SYNC-LIVE silent transport times out; hidden or obsolete scope stops reconnection',async()=>{
  const h=observer(),stop=h.start();await tick();await h.host.next(10000);assert.equal(h.events.at(-1).type,'unavailable');
  h.host.allowed=false;await h.host.next(250);assert.equal(h.sockets.length,1);assert.equal(h.host.timers.size,0);stop();
});

const origin={document_id:'source',block_id:'original'};
const response=(id='one',seq=2,text='A')=>({contract:'code_assist.notion.synced_block.v1',instance:{document_id:'page',block_id:id},origin,
  block:{id:'original',type:'synced_block',content:text,props:{synced_from:null}},witnesses:[witness('page'),witness('source',seq)]});
function view() {
  const timing=clock(),state=new Map(),loads=[],watches=[],context={pageId:'page',workspaceId:'workspace'},slots=['one','two'].map(id=>({id,origin}));
  const host={...timing,current:value=>value===context&&host.allowed,allowed:true,shown:true,visible:()=>host.shown,
    display:(slot,event)=>state.set(slot.id,event),load:(ctx,id,signal)=>{const p=deferred();loads.push({...p,id,signal});return p.promise;},
    watch:(ctx,doc,notify)=>{const item={doc,notify,stopped:false};watches.push(item);return ()=>{item.stopped=true;};}};
  return {host,context,slots,state,loads,watches,controller:synced.create(host)};
}
async function mounted(h) {const task=h.controller.mount(h.context,h.slots);h.loads[0].resolve(response('one'));h.loads[1].resolve(response('two'));await task;}
test('UI-SYNC-LIVE views share two document observers and source events refresh both empty instances',async()=>{
  const h=view(),before=JSON.stringify(h.slots);await mounted(h);assert.equal(h.watches.length,2);
  h.watches.find(item=>item.doc.document_id==='source').notify({type:'changed',sequence:3});
  assert.equal(h.state.get('one').data,undefined);assert.equal(h.loads.length,4);
  h.loads[2].resolve(response('one',3,'B'));h.loads[3].resolve(response('two',3,'B'));await tick();
  assert.equal(h.state.get('one').data.block.content,'B');assert.equal(h.state.get('two').data.block.content,'B');
  assert.equal(JSON.stringify(h.slots),before);h.controller.dispose();assert.ok(h.watches.every(item=>item.stopped));
});
test('UI-SYNC-LIVE event during GET suppresses stale response and follows with latest read',async()=>{
  const h=view();await mounted(h);const task=h.controller.refresh();const source=h.watches.find(item=>item.doc.document_id==='source');
  source.notify({type:'changed',sequence:3});h.loads[2].resolve(response('one',2,'stale'));h.loads[3].resolve(response('two',2,'stale'));await tick();
  assert.equal(h.loads.length,6);assert.equal(h.state.get('one').data,undefined);
  h.loads[4].resolve(response('one',3,'current'));h.loads[5].resolve(response('two',3,'current'));await task;
  assert.equal(h.state.get('one').data.block.content,'current');h.controller.dispose();
});
test('UI-SYNC-LIVE either document unavailability redacts, rechecks both grants, and recovers automatically',async()=>{
  for(const document of ['page','source']) {
    const h=view();await mounted(h);const observer=h.watches.find(item=>item.doc.document_id===document);
    observer.notify({type:'unavailable',sequence:2});assert.equal(h.state.get('one').data,undefined);
    for(const load of h.loads.slice(2))load.reject(Object.assign(new Error('denied'),{status:404}));await tick();
    assert.equal(h.state.get('one').denied,true);observer.notify({type:'unavailable',sequence:2});assert.equal(h.loads.length,4);
    observer.notify({type:'available',sequence:2});h.loads[4].resolve(response('one'));h.loads[5].resolve(response('two'));await tick();
    assert.equal(h.state.get('one').phase,'ready');h.controller.dispose();
  }
});
test('UI-SYNC-LIVE stale witness retries with a bound; hiding cancels observers, reads and retries',async()=>{
  const h=view();await mounted(h);h.watches[1].notify({type:'changed',sequence:4});
  h.loads[2].resolve(response('one',3));h.loads[3].resolve(response('two',3));await tick();
  assert.equal(h.state.get('one').data,undefined);assert.equal(h.loads.length,4);assert.equal(h.host.timers.size,2);
  await h.host.next(650);assert.equal(h.loads.length,5);
  h.host.shown=false;await h.controller.refresh();assert.ok(h.watches.every(item=>item.stopped));assert.equal(h.host.timers.size,0);assert.equal(h.loads[4].signal.aborted,true);
  h.loads[4].resolve(response('one',4));await tick();assert.equal(h.state.get('one').phase,'idle');
});
test('UI-SYNC-LIVE witness scope and shape are mandatory before starting observers',async()=>{
  for(const mutate of [data=>{delete data.witnesses;},data=>{data.witnesses[0].workspace_id='other';},
    data=>{data.witnesses[1].document_id='other';},data=>{data.witnesses[1].document_sequence=NaN;}]) {
    const h=view(),task=h.controller.mount(h.context,[h.slots[0]]),data=response();mutate(data);h.loads[0].resolve(data);await task;
    assert.equal(h.state.get('one').phase,'error');assert.equal(h.watches.length,0);h.controller.dispose();
  }
});
test('UI-SYNC-LIVE same-page original and reference use one observer; old mount events cannot touch next view',async()=>{
  const h=view();h.slots=[{id:'one',origin:{document_id:'page',block_id:'original'}}];
  const task=h.controller.mount(h.context,h.slots),data=response();data.origin={...h.slots[0].origin};data.witnesses=[witness('page')];
  h.loads[0].resolve(data);await task;assert.equal(h.watches.length,1);const old=h.watches[0];
  const next=h.controller.mount(h.context,h.slots);assert.equal(old.stopped,true);old.notify({type:'changed',sequence:99});
  h.loads[1].resolve(data);await next;assert.equal(h.loads.length,2);assert.equal(h.state.get('one').data.block.content,'A');h.controller.dispose();
});
