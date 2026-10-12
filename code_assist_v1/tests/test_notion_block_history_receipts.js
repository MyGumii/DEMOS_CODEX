"use strict";
const assert = require('node:assert/strict'), test = require('node:test'), vm = require('node:vm'), fs = require('node:fs');
const source = fs.readFileSync(require.resolve('../notion_static/notion.js'), 'utf8');
const clone = value => JSON.parse(JSON.stringify(value));
const scope = {workspace_id:'workspace',page_id:'page',subject_id:'owner'};
test('UI-BLOCK-HISTORY server capability survives the real page normalizer and is revoked by an older response',()=>{
  const context={serverRevisionFrom:()=>1,DatabaseBridge:{applyCanonicalDatabases:blocks=>blocks},EditorCore:{normalizeBlock:b=>b}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function normalizedPageData('),source.indexOf('function normalizedSavedPageDataPreservingBlockBindings(')),context);
  const current=context.normalizedPageData({page:{id:'page'},blocks:[],capabilities:{collaborative_block_history:true}});
  assert.equal(current.page.collaborative_block_history,true);
  assert.equal(context.normalizedPageData({page:{id:'page'}},current).page.collaborative_block_history,true);
  assert.equal(context.normalizedPageData({page:{id:'page'},capabilities:{atomic_save:true}},current).page.collaborative_block_history,false);
});
function harness(api) {
  const stored = [];
  const context = {State:{pageData:{page:{collaborative_block_history:true}},liveCollaboration:{durableCursor:99}},
    api, persistCollaborativeEnvelope:async(_scope,envelope)=>stored.push(clone(envelope)), encodeURIComponent};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf('function matchingCollaborativeCommandReceipt('), source.indexOf('async function flushCollaborativeOutbox(')),context);
  return {context,stored};
}
function envelope() {return {operation_id:'parent',workspace_epoch:2,document_epoch:3,structure_commands:[
  {operation_id:'parent:command:1',command_kind:'block.insert',target_id:'block',payload:{type:'paragraph',props:{a:1,b:2},restore_deleted:true}}]};}
function receipt(e) {return {command:{...clone(e.structure_commands[0]),expected_document_sequence:5,
  expected_workspace_epoch:2,expected_document_epoch:3,expected_target_version:null},result:{document_sequence:6}};}

test('UI-BLOCK-HISTORY receipt matching rejects changed intent or epochs while ignoring object key order',()=>{
  const {context}=harness(), e=envelope(), r=receipt(e);
  r.command.payload.props={b:2,a:1};
  assert.equal(context.matchingCollaborativeCommandReceipt(r,e.structure_commands[0],e),true);
  for(const patch of [{target_id:'other'},{operation_id:'other'},{command_kind:'block.delete'},
    {expected_document_epoch:4},{expected_workspace_epoch:4},{payload:{}},{expected_target_version:1}]) {
    assert.equal(context.matchingCollaborativeCommandReceipt({...r,command:{...r.command,...patch}},e.structure_commands[0],e),false);
  }
});

test('UI-BLOCK-HISTORY ambiguous duplicate sender replays exact receipt instead of changing the operation request',async()=>{
  const e=envelope(), r=receipt(e), calls=[];
  const h=harness(async(path,options={})=>{
    calls.push({path,...clone(options)});
    if(calls.length===1) throw {status:409,data:{error:'collaboration_operation_id_conflict'}};
    if(calls.length===2) return {receipt:r};
    return {document_sequence:6};
  });
  await h.context.commitCollaborativeStructureCommands(scope,e);
  assert.equal(calls.length,3);
  assert.equal(calls[0].body.expected_document_sequence,99);
  assert.deepEqual(calls[2].body,r.command);
  assert.equal(e.structure_commands[0].committed_document_sequence,6);
  assert.equal(e.acknowledgement_sequence,99,'the durable cursor never moves backward');
  assert.match(calls[1].path,/workspaces\/workspace\/pages\/page\/editing\/commands\?operation_id=/);
});

test('UI-BLOCK-HISTORY a missing receipt uses bounded sequence retry; conflicting intent never succeeds',async()=>{
  const e=envelope(); let calls=0;
  const h=harness(async(_path,options={})=>{
    calls++;
    if(calls===1)throw {status:409,data:{current_document_sequence:100}};
    if(calls===2)return {receipt:null};
    assert.equal(options.body.expected_document_sequence,100); return {document_sequence:101};
  });
  await h.context.commitCollaborativeStructureCommands(scope,e);
  assert.equal(calls,3); assert.equal(e.structure_commands[0].committed_document_sequence,101);
  const wrong=envelope(), r=receipt(wrong); r.command.payload={content:'different'};
  const conflict={status:409,data:{error:'collaboration_operation_id_conflict'}};
  const bad=harness(async(_path,options={})=>{if(options.method==='POST')throw conflict; return {receipt:r};});
  await assert.rejects(bad.context.commitCollaborativeStructureCommands(scope,wrong),error=>error===conflict);
  assert.equal(wrong.structure_commands[0].committed_document_sequence,undefined);
});

test('UI-BLOCK-HISTORY reconnect retains its identity and leaves another editor outbox untouched',async()=>{
  for(const ownEntry of [false,true]) {
    const other={operation_id:'other-op',client_id:'other-client',session_binding_sha256:'other-session',client_sequence:3};
    const own={operation_id:'own-op',client_id:'own-client',session_binding_sha256:'own-session',client_sequence:4};
    const entries=ownEntry?[other,own]:[other], before=clone(entries);
    const live={clientId:'own-client',sessionBindingSha256:'own-session',clientSequence:2,durableCursor:8};
    const context={State:{liveCollaboration:live},collaborativeEditingScope:()=>scope,
      CollaborativeEditingOutbox:{list:async()=>entries},clearCollaborativeRecoveryBanner(){},
      enterCollaborativeRecoveryOnly(){throw Error('An unrelated editor must not block this session');}};
    vm.createContext(context);
    vm.runInContext(source.slice(source.indexOf('async function prepareCollaborativeEditingSession('),source.indexOf('function queueCollaborativeUpdate(')),context);
    await context.prepareCollaborativeEditingSession({preserveIdentity:true});
    assert.equal(live.clientId,'own-client'); assert.equal(live.sessionBindingSha256,'own-session');
    assert.deepEqual([...live.pendingEnvelopes.keys()],ownEntry?['own-op']:[]);
    assert.deepEqual(entries,before,'other session data is retained in durable storage');
  }
});
