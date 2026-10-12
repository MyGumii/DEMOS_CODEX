const test=require('node:test'),assert=require('node:assert/strict');
const {create}=require('../notion_static/notion_synced_owner_scope.js');
const scope=()=>({realm:'http://fixture',userId:'actor',workspaceId:'workspace',pageId:'visible-page'});
const data=epoch=>({source:{database_id:'db'},access:{workspace_epoch:1,document_epoch:epoch,database_epoch:1},database:{effective_role:'edit'}});
const defer=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve};};
const settle=()=>new Promise(r=>setImmediate(r));

test('UI-SYNC-OWNER explicit source uses its exact witness, never visible-page identity for another owner',()=>{
  const s=scope(),calls=[];const client=create({current:()=>true,resolve:()=>calls.push('unexpected')});
  assert.deepEqual(client.get(s,'block',{source:{document_id:'physical',database_id:'db'},witnesses:[{document_id:'physical',workspace_id:'source-workspace'}]}),{phase:'ready',documentId:'physical',workspaceId:'source-workspace'});
  assert.equal(client.get(s,'block',{source:{document_id:'physical',database_id:'db'}}),null);
  assert.deepEqual(calls,[]);
});
test('UI-SYNC-OWNER DB-only response resolves through current authorized source locator without manufacturing a page',async()=>{
  const s=scope(),calls=[],notifications=[];const client=create({current:()=>true,
    resolve:async(context,db,signal)=>{calls.push([context,db,signal]);return {workspace_id:'workspace',source:{id:'db',document_id:'physical'}};},changed:(...a)=>notifications.push(a)});
  const record=client.get(s,'linked',data(1));assert.equal(record.phase,'loading');await settle();
  assert.equal(record.phase,'ready');assert.equal(record.documentId,'physical');assert.equal(record.workspaceId,'workspace');
  assert.equal(calls.length,1);assert.equal(calls[0][0],s);assert.equal(calls[0][1],'db');
  assert.equal(client.get(s,'linked',data(1)),record);assert.equal(notifications.length,1);
});
test('UI-SYNC-OWNER missing parent authority or mismatched response stays unavailable',async()=>{
  for(const result of [{workspace_id:'workspace',source:{id:'db'}},{workspace_id:'other',source:{id:'db',document_id:'secret'}},
    {workspace_id:'workspace',source:{id:'other',document_id:'secret'}},{workspace_id:'workspace',source:{id:'db',document_id:'x'.repeat(161)}}]){
    const s=scope(),client=create({current:()=>true,resolve:async()=>result,changed:()=>{}}),record=client.get(s,'linked',data(1));await settle();
    assert.equal(record.phase,'unavailable');assert.equal(record.documentId,undefined);
  }
});
test('UI-SYNC-OWNER late lookup after actor/navigation authority loss cannot install an owner or notify UI',async()=>{
  const pending=defer(),s=scope();let live=true,changed=0;
  const client=create({current:()=>live,resolve:()=>pending.promise,changed:()=>changed++});
  const record=client.get(s,'linked',data(1));live=false;pending.resolve({workspace_id:'workspace',source:{id:'db',document_id:'physical'}});await settle();
  assert.equal(record.documentId,undefined);assert.equal(changed,0);assert.equal(client.get(s,'linked',data(1)),null);
});
test('UI-SYNC-OWNER reopening same visible page aborts old scope and only the new scope may bind',async()=>{
  const first=defer(),second=defer(),one=scope(),two=scope(),notifications=[];let count=0;
  const client=create({current:()=>true,resolve:()=>++count===1?first.promise:second.promise,changed:s=>notifications.push(s)});
  const old=client.get(one,'linked',data(1)),fresh=client.get(two,'linked',data(1));assert.equal(old.controller.signal.aborted,true);
  first.resolve({workspace_id:'workspace',source:{id:'db',document_id:'stale'}});second.resolve({workspace_id:'workspace',source:{id:'db',document_id:'current'}});await settle();
  assert.equal(old.documentId,undefined);assert.equal(fresh.documentId,'current');assert.deepEqual(notifications,[two]);
});
test('UI-SYNC-OWNER authorization epoch change forces new discovery and failures never inherit prior owner',async()=>{
  const s=scope();let count=0;
  const client=create({current:()=>true,resolve:async()=>++count===1?{workspace_id:'workspace',source:{id:'db',document_id:'physical'}}:{workspace_id:'workspace',source:{id:'db'}},changed:()=>{}});
  const first=client.get(s,'linked',data(1));await settle();assert.equal(first.phase,'ready');
  const revoked=client.get(s,'linked',data(2));assert.notEqual(first,revoked);await settle();assert.equal(revoked.phase,'unavailable');assert.equal(revoked.documentId,undefined);
});
test('UI-SYNC-OWNER disposing aborts pending resolution without late install or UI notification',async()=>{
  const s=scope(),pending=defer();let notified=0;
  const client=create({current:()=>true,resolve:()=>pending.promise,changed:()=>notified++});
  const old=client.get(s,'linked',data(1));client.clear();assert.equal(old.controller.signal.aborted,true);
  pending.resolve({workspace_id:'workspace',source:{id:'db',document_id:'physical'}});await settle();assert.equal(old.documentId,undefined);assert.equal(notified,0);
});

test('UI-SYNC-OWNER wait returns an explicit witnessed owner without a lookup',async()=>{
  const s=scope();let calls=0;
  const client=create({current:()=>true,resolve:()=>calls++});
  assert.deepEqual(await client.wait(s,'block',{source:{document_id:'physical'},witnesses:[{document_id:'physical',workspace_id:'source-workspace'}]}),
    {phase:'ready',documentId:'physical',workspaceId:'source-workspace'});
  assert.equal(calls,0);
});
test('UI-SYNC-OWNER wait joins one lookup and returns its exact current owner',async()=>{
  const s=scope(),pending=defer();let calls=0,done=false;
  const client=create({current:()=>true,resolve:()=>{calls++;return pending.promise;},changed:()=>{}});
  const record=client.get(s,'linked',data(1));const waiting=client.wait(s,'linked',data(1)).then(value=>{done=true;return value;});
  await settle();assert.equal(done,false);assert.equal(calls,1);
  pending.resolve({workspace_id:'workspace',source:{id:'db',document_id:'physical'}});
  assert.equal(await waiting,record);assert.equal(record.documentId,'physical');
});
test('UI-SYNC-OWNER cancelled wait never resurrects a lookup or returns a stale owner',async()=>{
  for(const change of ['clear','obsolete','reopen']){
    const s=scope(),pending=defer();let calls=0,live=true;
    const client=create({current:()=>live,resolve:()=>{calls++;return pending.promise;},changed:()=>{}});
    const waiting=client.wait(s,'linked',data(1));
    if(change==='clear')client.clear();else if(change==='obsolete')live=false;else client.get(scope(),'linked',data(1));
    pending.resolve({workspace_id:'workspace',source:{id:'db',document_id:'physical'}});
    assert.equal(await waiting,null);assert.equal(calls,change==='reopen'?2:1);
  }
});
test('UI-SYNC-OWNER wait preserves missing authority as unavailable',async()=>{
  const client=create({current:()=>true,resolve:async()=>({workspace_id:'workspace',source:{id:'db'}}),changed:()=>{}});
  assert.equal(await client.wait(scope(),'linked',data(1)),null);
});
