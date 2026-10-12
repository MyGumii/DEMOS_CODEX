const {test} = require('node:test');
const assert = require('node:assert/strict');
const adoption = require('../notion_static/notion_synced_legacy_adoption.js');
const scope = {userId:'owner',workspaceId:'workspace'};
const request = {origin:{document_id:'original',block_id:'source'},
  references:[{document_id:'target',block_id:'reference'}], preparation_sha256:'sha256:'+'a'.repeat(64), operation_id:'adopt-once'};
const clone = value => structuredClone(value);
function memory() {
  const data = new Map();
  return {get length(){return data.size;},key:i=>[...data.keys()][i]??null,
    getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)};
}
const locks = () => ({request:async (_key,_options,fn)=>fn({})});
const hooks = (action='adopt') => ({action,current:()=>true,beforeWrite:async()=>{}});
function terminal(state='committed', value=request) {
  return {state,result:{contract:`code_assist.notion.synced_legacy_adoption_${state==='committed'?'commit':'cancel'}.v1`,
    workspace_id:scope.workspaceId,...clone(value),replayed:true,...(state==='cancelled'?{state}: {})},
    ...(state==='committed'?{current:[...new Set([value.origin,...value.references].map(item=>item.document_id))]
      .map(document_id=>({document_id,archived:false,document_sequence:8,document_epoch:1}))}: {})};
}

test('lost committed reply recovers the exact receipt after reload without a second adoption', async()=>{
  const storage=memory(); let committed=false, writes=0, intents=0;
  const transport=async(url,options)=>{
    assert.deepEqual(options.body,request);
    if(url.endsWith('/result'))return committed?terminal():{state:'unknown'};
    assert.equal(JSON.parse(storage.getItem(storage.key(0))).state,'pending');
    if(url.endsWith('/intent')){intents++;return {state:'pending',request:clone(request)};}
    writes++;committed=true;throw Error('lost response');
  };
  const first=adoption.create({storage:()=>storage,locks,transport});
  await assert.rejects(first.run(scope,request,hooks()),/lost response/);
  assert.equal(first.pending(scope),true);
  const reloaded=adoption.create({storage:()=>storage,locks,transport});
  assert.equal((await reloaded.run(scope,request,hooks())).state,'committed');
  assert.equal(reloaded.pending(scope),false);
  assert.equal(writes,1);assert.equal(intents,1);
});

test('server history request can be recovered with no local record and cancelled before dispatch', async()=>{
  const storage=memory(), calls=[];
  const controller=adoption.create({storage:()=>storage,locks,transport:async(url,options)=>{
    calls.push(url.split('/').pop());assert.deepEqual(options.body,request);
    assert.equal(controller.list(scope).length,1);
    return url.endsWith('/result')?{state:'unknown'}:terminal('cancelled');
  }});
  assert.equal((await controller.run(scope,request,hooks('cancel'))).state,'cancelled');
  assert.deepEqual(calls,['result','cancel']);assert.equal(controller.pending(scope),false);
});

test('scope change after intent leaves recovery intact and prevents adoption', async()=>{
  const storage=memory();let current=true;const calls=[];
  const controller=adoption.create({storage:()=>storage,locks,transport:async(url)=>{
    calls.push(url.split('/').pop());
    if(url.endsWith('/result'))return {state:'unknown'};
    current=false;return {state:'pending',request:clone(request)};
  }});
  await assert.rejects(controller.run(scope,request,{...hooks(),current:()=>current}),/워크스페이스/);
  assert.deepEqual(calls,['result','intent']);assert.equal(controller.pending(scope),true);
});

test('storage failures and corrupted pending records fail before any server mutation', async()=>{
  let calls=0;
  const storage=memory(), transport=async()=>{calls++;throw Error('unexpected request');};
  const controller=adoption.create({storage:()=>({...storage,setItem:()=>{throw Error('quota');}}),locks,transport});
  await assert.rejects(controller.run(scope,request,hooks()),/quota/);assert.equal(calls,0);
  const good=adoption.create({storage:()=>storage,locks,transport:async()=>({state:'unknown'})});
  await good.run(scope,request,hooks('result'));
  storage.setItem(storage.key(0),'{broken');
  await assert.rejects(good.run(scope,request,hooks()),/범위/);assert.equal(calls,0);
});

test('wrong workspace or endpoint receipt never marks the exact request complete', async()=>{
  for(const mutate of [value=>value.result.workspace_id='other',value=>value.result.origin.block_id='other',value=>value.current=[]]){
    const storage=memory(), result=terminal();mutate(result);
    const controller=adoption.create({storage:()=>storage,locks,transport:async()=>result});
    await assert.rejects(controller.run(scope,request,hooks()),/범위/);
    assert.equal(controller.pending(scope),true);
  }
});

test('full 127-reference request survives storage and cannot be replaced under the same operation', async()=>{
  const storage=memory(), large={...clone(request),references:Array.from({length:127},(_,i)=>({document_id:`document-${String(i).padStart(3,'0')}`,block_id:'한'.repeat(250)}))};
  let calls=0;
  const controller=adoption.create({storage:()=>storage,locks,transport:async()=>{calls++;return {state:'unknown'};}});
  assert.equal((await controller.run(scope,large,hooks('result'))).state,'unknown');
  assert.ok(storage.getItem(storage.key(0)).length>8192);
  assert.deepEqual(controller.list(scope)[0].request,large);
  await assert.rejects(controller.run(scope,{...large,preparation_sha256:'sha256:'+'b'.repeat(64)},hooks()),/범위/);
  assert.equal(calls,1);
});

test('two browser controllers coordinate the same operation before dispatch', async()=>{
  const storage=memory(), held=new Set();let unblock, writes=0;
  const waiting=new Promise(resolve=>unblock=resolve);
  const manager=()=>({request:async(key,_options,fn)=>{
    if(held.has(key))return fn(null);held.add(key);
    try{return await fn({});}finally{held.delete(key);}
  }});
  const transport=async(url)=>{
    if(url.endsWith('/result')){await waiting;return {state:'unknown'};}
    if(url.endsWith('/intent'))return {state:'pending',request:clone(request)};
    writes++;return terminal();
  };
  const first=adoption.create({storage:()=>storage,locks:manager,transport}), second=adoption.create({storage:()=>storage,locks:manager,transport});
  const pending=first.run(scope,request,hooks());
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal((await second.run(scope,request,hooks())).state,'busy');
  unblock();assert.equal((await pending).state,'committed');assert.equal(writes,1);
});
