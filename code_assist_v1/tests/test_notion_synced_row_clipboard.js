const test=require('node:test'),assert=require('node:assert/strict');
const paste=require('../notion_static/notion_synced_row_clipboard.js');
const scope={realm:'http://127.0.0.1:10322',userId:'owner',workspaceId:'ws',pageId:'page',databaseId:'db',rowId:'row'};
const request=()=>({source:{workspace_id:'ws',document_id:'source',block_id:'original'},anchor_id:'anchor',replace_empty:false,
  expected_target_version:4,expected_workspace_epoch:2,expected_document_epoch:3,expected_database_epoch:null,operation_id:'paste-one'});
const result=value=>({contract:paste.CONTRACT,workspace_id:'ws',document_id:'page',database_id:'db',row_id:'row',
  reference_id:'reference',operation_id:value.operation_id,replayed:false,target_version:5,document_sequence:8,
  source:{block_id:'original',document_id:'source',workspace_id:'ws'},location:{kind:'row',database_id:'db',row_id:'row'}});
function harness(options={}){
  const records=options.records||new Map(),calls=[];let current=true,editable=true,failStorage=false,locked=false;
  const host={storage:()=>({getItem:key=>records.get(key)??null,setItem:(key,value)=>{if(failStorage)throw Error('storage');records.set(key,value);},removeItem:key=>records.delete(key)}),
    locks:()=>({request:async(_key,_options,callback)=>callback(locked?null:{})}),current:()=>current,editable:()=>editable,
    transport:async(url,body)=>{calls.push({url,body:structuredClone(body)});assert.ok(records.size);return options.transport?options.transport(url,body):result(body.body);},changed:()=>{}};
  const controller=paste.create(scope,host),hooks={current:()=>true,prepare:async()=>request()};
  return {records,calls,controller,hooks,host,setCurrent:value=>current=value,setEditable:value=>editable=value,setStorage:value=>failStorage=value,setLock:value=>locked=value};
}
test('UI-SYNC-ROW-CLIPBOARD intent is durable before send, descriptor carries no body and receipt requires acknowledgement',async()=>{
  const h=harness();const outcome=await h.controller.run(h.hooks);
  assert.equal(outcome.state,'committed');assert.equal(h.calls.length,1);assert.equal(h.controller.pending(),true);
  const record=JSON.parse([...h.records.values()][0]);assert.equal(record.confirmed,true);assert.deepEqual(record.request,request());
  assert.equal(JSON.stringify(record).includes('blocks'),false);
  assert.equal(await h.controller.acknowledge('different-operation'),false);
  assert.equal(await h.controller.acknowledge('paste-one'),true);assert.equal(h.controller.pending(),false);
});
test('UI-SYNC-ROW-CLIPBOARD lost response is recovered from exact receipt without a new paste',async()=>{
  const h=harness({transport:async()=>{throw Error('lost response');}});
  await assert.rejects(h.controller.run(h.hooks),/lost response/);assert.equal(h.records.size,1);
  const recovered=harness({records:h.records,transport:async url=>{assert.ok(url.endsWith('-result'));return {state:'committed',result:{...result(request()),replayed:true}};}});
  const outcome=await recovered.controller.run({current:()=>true,prepare:async()=>{throw Error('must not prepare');}});
  assert.equal(outcome.state,'committed');assert.equal(recovered.calls.length,1);
});
test('UI-SYNC-ROW-CLIPBOARD an unknown receipt retries the immutable operation',async()=>{
  const h=harness({transport:async()=>{throw Error('offline');}});await assert.rejects(h.controller.run(h.hooks));
  const retry=harness({records:h.records,transport:async(url,body)=>url.endsWith('-result')?{state:'unknown'}:result(body.body)});
  await retry.controller.run({current:()=>true,prepare:async()=>{throw Error('new operation forbidden');}});
  assert.equal(retry.calls.length,2);assert.deepEqual(retry.calls[0].body.body,retry.calls[1].body.body);
});
test('UI-SYNC-ROW-CLIPBOARD stale request clears only after a current unknown receipt',async()=>{
  const stale=Object.assign(Error('stale'),{status:409,data:{code:'synced_source_changed'}});
  const h=harness({transport:async url=>{if(url.endsWith('-result'))return {state:'unknown'};throw stale;}});
  assert.equal((await h.controller.run(h.hooks)).state,'stale');assert.equal(h.records.size,0);
});
test('UI-SYNC-ROW-CLIPBOARD a commit racing a stale response wins exact receipt recovery',async()=>{
  const h=harness({transport:async url=>{if(url.endsWith('-result'))return {state:'committed',result:result(request())};throw Object.assign(Error('stale'),{status:409,data:{code:'synced_source_changed'}});}});
  assert.equal((await h.controller.run(h.hooks)).state,'committed');assert.equal(h.controller.pending(),true);
});
test('UI-SYNC-ROW-CLIPBOARD navigation or concurrent input during preparation never creates durable intent or saves',async()=>{
  for(const invalidation of ['navigation','input']){
    const h=harness();let inputCurrent=true;
    await assert.rejects(h.controller.run({current:()=>inputCurrent,prepare:async()=>{if(invalidation==='navigation')h.setCurrent(false);else inputCurrent=false;return request();}}));
    assert.equal(h.records.size,0);assert.equal(h.calls.length,0);
  }
});
test('UI-SYNC-ROW-CLIPBOARD changed rights and storage failure stop before mutation',async()=>{
  for(const action of ['role','storage']){
    const h=harness();if(action==='storage')h.setStorage(true);
    await assert.rejects(h.controller.run({current:()=>true,prepare:async()=>{if(action==='role')h.setEditable(false);return request();}}));
    assert.equal(h.calls.length,0);assert.equal(h.records.size,0);
  }
});
test('UI-SYNC-ROW-CLIPBOARD a committed receipt is recoverable with current read permission',async()=>{
  const h=harness();await h.controller.run(h.hooks);
  const retry=harness({records:h.records,transport:async()=>({state:'committed',result:{...result(request()),replayed:true}})});retry.setEditable(false);
  assert.equal((await retry.controller.run(retry.hooks)).state,'committed');assert.equal(retry.calls.length,1);
});
test('UI-SYNC-ROW-CLIPBOARD malformed local record and mismatched server receipt stay fenced',async()=>{
  const h=harness();h.records.set(paste.key(scope),'{bad');assert.equal(h.controller.pending(),true);await assert.rejects(h.controller.run(h.hooks));assert.equal(h.calls.length,0);
  const mismatch=harness({transport:async()=>({...result(request()),row_id:'other'})});await assert.rejects(mismatch.controller.run(mismatch.hooks),/대상 항목/);assert.equal(mismatch.controller.pending(),true);
});
test('UI-SYNC-ROW-CLIPBOARD no operation starts while another tab owns the row lock',async()=>{
  const h=harness();h.setLock(true);assert.equal((await h.controller.run(h.hooks)).state,'busy');assert.equal(h.calls.length,0);assert.equal(h.records.size,0);
});
test('UI-SYNC-ROW-CLIPBOARD scope keys isolate realm actor physical owner database and row',()=>{
  for(const field of ['realm','userId','workspaceId','pageId','databaseId','rowId']){
    assert.notEqual(paste.key(scope),paste.key({...scope,[field]:field==='realm'?'https://example.com':'other'}));
  }
});
test('UI-SYNC-ROW-CLIPBOARD closed request rejects whole bodies grants and invalid versions',()=>{
  for(const value of [{...request(),blocks:[]},{...request(),grant:'edit'},{...request(),expected_target_version:true},
    {...request(),expected_database_epoch:0},{...request(),source:{...request().source,content:'cached'}},
    {...request(),anchor_id:null,replace_empty:true}])assert.equal(paste.validRequest(value),false);
});
