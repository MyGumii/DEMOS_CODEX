"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../notion_static/notion.js'),'utf8');
const segment=source.slice(source.indexOf('function requestLiveSync()'),source.indexOf('function sameCollaborativeScope('))
  +source.slice(source.indexOf('function recordLiveReplayReceipt('),source.indexOf('function scheduleLiveCanonicalRefresh('));
assert(segment.includes('completeLiveReplayDrain'));
function runtime(){
  const live={connected:true,syncPending:false,initialReplay:false,replayCursor:5,durableCursor:100,liveCursor:100};
  const sent=[],errors=[];
  const context={State:{liveCollaboration:live},collaborativeEditingActive:()=>true,flushCollaborativeOutbox(){},
    sendLiveDocument:value=>{sent.push(value);return true;},setStatus:value=>errors.push(value),
    completeInitialCollaborativeReplay(){live.initialReplay=false;live.ready=true;}};
  vm.createContext(context);vm.runInContext(segment,context);
  return {live,sent,errors,context};
}
const r=runtime();r.context.requestLiveSync();
assert.equal(r.sent[0].op,'replay');assert.equal(r.sent[0].after_document_sequence,5,'newer ACK cannot skip a service update');
r.context.requestLiveSync();assert.equal(r.sent.length,1,'only one replay in flight');
r.context.recordLiveReplayReceipt({next_document_sequence:8,high_water_sequence:100,has_more:true});
assert.equal(r.live.replayCursor,5,'receipt is not delivery');
r.context.completeLiveReplayDrain(128);assert.equal(r.live.replayCursor,5,'full drain may leave queued replay frames');
assert.equal(r.sent.at(-1).op,'drain');
r.context.completeLiveReplayDrain(4);
assert.equal(r.live.replayCursor,8);assert.equal(r.sent.at(-1).after_document_sequence,8,'batch cursor ignores newer broadcast');
r.context.recordLiveReplayReceipt({next_document_sequence:100,high_water_sequence:100,has_more:false});
r.context.completeLiveReplayDrain(0);
assert.equal(r.live.replayCursor,100);assert.equal(r.live.durableReplayPending,false);
assert.equal(r.sent.at(-1).op,'live_replay','comments and appearance still refresh');
const initial=runtime();initial.live.initialReplay=true;initial.live.replayCursor=0;
initial.context.recordLiveReplayReceipt({next_document_sequence:3,high_water_sequence:9,has_more:true});
initial.context.completeLiveReplayDrain(3);assert.equal(initial.live.ready,undefined);
assert.equal(initial.sent.at(-1).after_document_sequence,3);
initial.context.recordLiveReplayReceipt({next_document_sequence:9,high_water_sequence:9,has_more:false});
initial.context.completeLiveReplayDrain(1);assert.equal(initial.live.ready,true);
const old=runtime();old.context.recordLiveReplayReceipt({high_water_sequence:8,has_more:false});
old.context.completeLiveReplayDrain(1);assert.equal(old.live.replayCursor,8,'unpaged older receipt remains compatible');
const invalid=runtime();assert.equal(invalid.context.recordLiveReplayReceipt({next_document_sequence:4,has_more:true}),false);
assert.equal(invalid.live.replayCursor,5);assert.equal(invalid.sent.length,0);
const ordinary=runtime();ordinary.context.collaborativeEditingActive=()=>false;ordinary.context.requestLiveSync();
assert.equal(ordinary.sent[0].op,'live_replay');
console.log('PASS service update replay: missed service delta, paging, queue drain, initial replay, compatibility, ordinary comments');
