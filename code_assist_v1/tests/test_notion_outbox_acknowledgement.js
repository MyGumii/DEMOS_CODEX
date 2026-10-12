'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const clone=v=>JSON.parse(JSON.stringify(v));
const scope={subject_id:'owner',workspace_id:'workspace',page_id:'page'};
function setup(entry={}) {
 const envelope={operation_id:'op',client_id:'client',session_binding_sha256:'session',client_sequence:2,
   acknowledgement_sequence:3,payload_base64:'AQI=',structure_commands:[],...entry};
 const live={clientId:'client',sessionBindingSha256:'session',durableCursor:8,generation:1,editingScope:scope,
   connected:true,crdtReady:true,writeAuthorized:true,sendQueueActive:false,pendingEnvelopes:new Map([['op',envelope]]),
   persistenceChain:Promise.resolve(),typedCommandChain:Promise.resolve()};
 const saved=[],sent=[],recovery=[];
 const context={State:{liveCollaboration:live},Number,collaborativeEditingActive:()=>true,
   sortedPendingEnvelopes:()=>[...live.pendingEnvelopes.values()],collaborativeEditingScope:()=>scope,
   sameCollaborativeScope:(a,b)=>JSON.stringify(a)===JSON.stringify(b),
   persistCollaborativeEnvelope:async(s,e)=>saved.push({scope:clone(s),envelope:clone(e)}),
   commitCollaborativeStructureCommands:async()=>{},sendLiveDocument:m=>{sent.push(clone(m));return true;},
   updateCollaborativeSyncStatus(){},setStatus(){},enterCollaborativeRecoveryOnly:reason=>{recovery.push(reason);live.recoveryOnly=true;}};
 vm.createContext(context);
 vm.runInContext(source.slice(source.indexOf('async function prepareCollaborativeSubmission('),source.indexOf('function decodeLiveFrame(')),context);
 return {context,live,envelope,saved,sent,recovery};
}
test('UI-OUTBOX-ACK queued unsent update acknowledges preceding saves and freezes the exact retry before sending',async()=>{
 const h=setup({submission_started:false});
 await h.context.flushCollaborativeOutbox();
 assert.equal(h.sent[0].acknowledgement_sequence,8);assert.equal(h.saved[0].envelope.submission_started,true);
 h.live.sendQueueActive=false;h.live.durableCursor=20;
 await h.context.flushCollaborativeOutbox();assert.deepEqual(h.sent[1],h.sent[0]);
});
test('UI-OUTBOX-ACK legacy uncertain update is sent unchanged, then only an exact definite rejection permits rebasing',async()=>{
 const h=setup();await h.context.flushCollaborativeOutbox();assert.equal(h.sent[0].acknowledgement_sequence,3);
 const msg={error:'ACKNOWLEDGEMENT_CONFLICT',operation_id:'op',client_sequence:2,acknowledgement_sequence:3};
 for(const patch of [{operation_id:'other'},{client_sequence:1},{acknowledgement_sequence:2},{error:'PROTOCOL_REJECTED'}]) {
  assert.equal(await h.context.retryRejectedCollaborativeAcknowledgement({...msg,...patch}),false);
 }
 assert.equal(h.sent.length,1);
 assert.equal(await h.context.retryRejectedCollaborativeAcknowledgement(msg),true);
 assert.equal(h.sent.length,2);assert.equal(h.sent[1].acknowledgement_sequence,8);
 assert.equal(h.sent[1].operation_id,'op');assert.equal(h.sent[1].payload_base64,'AQI=');
 assert.equal(h.saved[1].envelope.submission_started,false);assert.equal(h.saved[2].envelope.submission_started,true);
 assert.equal(h.live.pendingEnvelopes.size,1,'not removed before durable acknowledgement');
});
test('UI-OUTBOX-ACK ambiguous disconnect and reload retain exact request including acknowledgement',async()=>{
 const first=setup({submission_started:false});await first.context.flushCollaborativeOutbox();
 const reloaded=setup(first.saved[0].envelope);reloaded.live.durableCursor=100;
 await reloaded.context.flushCollaborativeOutbox();assert.deepEqual(reloaded.sent[0],first.sent[0]);
});
test('UI-OUTBOX-ACK no forward durable cursor retains draft in recovery without repeated submissions',async()=>{
 const h=setup({submission_started:true,acknowledgement_sequence:8});await h.context.flushCollaborativeOutbox();
 await h.context.retryRejectedCollaborativeAcknowledgement({error:'ACKNOWLEDGEMENT_CONFLICT',operation_id:'op',client_sequence:2,acknowledgement_sequence:8});
 assert.equal(h.recovery.length,1);await h.context.flushCollaborativeOutbox();assert.equal(h.sent.length,1);
 assert.equal(h.live.pendingEnvelopes.size,1);
});
test('UI-OUTBOX-ACK persistence failure prevents transmission and stale scope prevents retry',async()=>{
 const h=setup({submission_started:false});h.context.persistCollaborativeEnvelope=async()=>{throw Error('disk');};
 await h.context.flushCollaborativeOutbox();assert.equal(h.sent.length,0);
 const other=setup();await other.context.flushCollaborativeOutbox();
 other.context.persistCollaborativeEnvelope=async()=>{other.live.generation++;};
 await other.context.retryRejectedCollaborativeAcknowledgement({error:'ACKNOWLEDGEMENT_CONFLICT',operation_id:'op',client_sequence:2,acknowledgement_sequence:3});
 assert.equal(other.sent.length,1);
});
