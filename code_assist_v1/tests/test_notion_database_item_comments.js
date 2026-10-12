'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),api=require('../notion_static/notion_database_item_comments.js');
const stable=value=>Array.isArray(value)?value.map(stable):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,stable(value[k])])):value;
const clone=structuredClone,wait=async fn=>{for(let i=0;i<1000&&!fn();i++)await new Promise(r=>setImmediate(r));assert.ok(fn());};
function fixture(){
  const context={realm:'https://fixture.invalid',userId:'owner',workspaceId:'ws',pageId:'source',databaseId:'db',rowId:'row'},records=new Map(),sent=[],receipts=new Map(),changes=[];
  let n=0,live=true,storage=false,lose=false,bad,readFailure=false,gate,storageGate,readGate,reads=0,prepare=async()=>true,controller;
  const remote={contract:'code_assist.notion.live_collaboration_authority.v1',status:'comments_loaded',workspace_id:'ws',document_id:'source',item_target:api.target(context),workspace_epoch:1,document_epoch:2,document_sequence:4,role:'comment',items:[]};
  const key=c=>JSON.stringify(c),journal={list:async c=>clone(records.get(key(c))||[]),put:async(c,e)=>{if(storageGate)await storageGate;if(storage)throw Error('storage failed');records.set(key(c),[stable(clone(e))]);}};
  const host={journal,current:()=>live,read:async()=>{reads++;if(readFailure)throw Error('read failed');const result=clone(remote);if(readGate)await readGate;return result;},
    operationId:()=>`operation-${++n}`,commentId:()=>`comment-${++n}`,mentions:()=>['reader'],prepare:()=>prepare(),changed:async(c,r)=>changes.push(r),
    send:async(c,r)=>{
      assert.deepEqual([...records.values()].flat()[0].request,r);sent.push(clone(r));if(gate)await gate;
      if(receipts.has(r.body.operation_id))return clone(receipts.get(r.body.operation_id));
      const b=r.body,item=remote.items.find(item=>item.id===r.comment),result={contract:remote.contract,status:{create:'comment_created',edit:'comment_updated',delete:'comment_deleted',resolve:'comment_resolution_set',react:'comment_reaction_toggled'}[r.action],operation_id:b.operation_id,comment_id:r.comment,workspace_id:'ws',document_id:'source',workspace_epoch:1,document_epoch:2,document_sequence:++remote.document_sequence};
      if(r.action==='create')remote.items.push({id:r.comment,document_id:'source',anchor:api.target(context),author_subject_id:'owner',content:b.content,parent_comment_id:b.parent_comment_id,row_version:1});
      else if(r.action==='delete'){remote.items=remote.items.filter(i=>i!==item);result.deleted=true;}
      else {assert.equal(b.expected_row_version,item.row_version);result.row_version=++item.row_version;if(r.action==='edit')item.content=b.content;if(r.action==='resolve')result.resolved=item.resolved=b.resolved;if(r.action==='react'){result.emoji=b.emoji;result.added=true;}}
      receipts.set(b.operation_id,clone(result));if(lose){lose=false;throw Error('response lost');}if(bad){bad(result);bad=null;}return result;
    }};
  controller=api.create(host);
  return {context,remote,records,sent,receipts,changes,get c(){return controller;},bind:()=>controller.bind(context),view:()=>controller.snapshot(context),draft:content=>controller.draft(context,{content}),
    submit:()=>controller.mutate(context,'create'),retry:()=>controller.retry(context),restart:()=>controller=api.create(host),lose:()=>lose=true,bad:f=>bad=f,storage:v=>storage=v,live:v=>live=v,gate:p=>gate=p,storageGate:p=>storageGate=p,prepare:f=>prepare=f,readFailure:v=>readFailure=v,readGate:p=>readGate=p,reads:()=>reads};
}
test('UI-ITEM-COMMENTS comment-only role drafts survive reload and navigation without posting',async()=>{
  const f=fixture();await f.bind();assert.equal(f.view().canComment,true);f.draft('한글 초안\n두 번째 줄');assert.equal(await f.c.beforeLeave(f.context),true);assert.equal(f.sent.length,0);
  f.restart();await f.bind();assert.equal(f.view().draft.content,'한글 초안\n두 번째 줄');assert.equal(await f.submit(),true);assert.equal(f.remote.items.length,1);assert.equal(f.view().draft.content,'');assert.equal(f.sent[0].body.anchor.row_id,'row');
});
test('UI-ITEM-COMMENTS lost acknowledgment recovers the exact immutable request after restart',async()=>{
  const f=fixture();await f.bind();f.draft('중복 금지');f.lose();assert.equal(await f.submit(),false);assert.equal(await f.c.beforeLeave(f.context),false);f.restart();await f.bind();
  assert.equal(f.view().pending,true);assert.equal(await f.submit(),false);assert.equal(await f.retry(),true);assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.receipts.size,1);assert.equal(f.view().draft.content,'');
});
test('UI-ITEM-COMMENTS storage failures cannot post or lose drafts, and navigation retries persistence',async()=>{
  const f=fixture();await f.bind();f.storage(true);f.draft('keep');assert.equal(await f.c.beforeLeave(f.context),false);assert.equal(await f.submit(),false);assert.equal(f.sent.length,0);
  f.storage(false);assert.equal(await f.retry(),true);assert.equal(f.remote.items[0].content,'keep');
});
test('UI-ITEM-COMMENTS composition and failed preceding item save prevent submission',async()=>{
  const f=fixture();await f.bind();f.draft('제목');f.c.composing(f.context,true);assert.equal(await f.submit(),false);assert.equal(await f.c.beforeLeave(f.context),false);
  f.c.composing(f.context,false);f.prepare(async()=>false);assert.equal(await f.submit(),false);assert.equal(f.sent.length,0);assert.equal(f.view().draft.content,'제목');
});
test('UI-ITEM-COMMENTS prepare can persist the draft without recursively blocking itself',async()=>{
  const f=fixture();await f.bind();f.draft('ready');f.prepare(()=>f.c.beforeLeave(f.context));assert.equal(await f.submit(),true);
});
test('UI-ITEM-COMMENTS stale read identity and view-only permission never authorize posting',async()=>{
  for(const change of [r=>r.role='view',r=>r.item_target.row_id='other',r=>r.workspace_id='other',r=>r.document_id='other']){const f=fixture();change(f.remote);await f.bind();f.draft('private');assert.equal(await f.submit(),false);assert.equal(f.sent.length,0);}
});
test('UI-ITEM-COMMENTS the actual owner role returned by the service permits the composer',async()=>{
  const f=fixture();f.remote.role='owner';await f.bind();assert.equal(f.view().canComment,true);f.draft('owner');assert.equal(await f.submit(),true);
  f.remote.items[0].author_subject_id='someone';await f.c.refresh(f.context);assert.equal(f.c.canEdit(f.context,f.remote.items[0]),true);
});
test('UI-ITEM-COMMENTS scope is rechecked after durable storage and late receipts do not refresh another item',async()=>{
  const f=fixture();await f.bind();f.draft('late');await f.c.beforeLeave(f.context);let release;f.gate(new Promise(r=>release=r));const saving=f.submit();await wait(()=>f.sent.length);f.live(false);release();assert.equal(await saving,true);assert.equal(f.changes.length,0);
  const g=fixture();await g.bind();g.draft('never');await g.c.beforeLeave(g.context);let persistRelease;g.storageGate(new Promise(r=>persistRelease=r));const blocked=g.submit();await wait(()=>g.view().pending);g.live(false);persistRelease();assert.equal(await blocked,false);assert.equal(g.sent.length,0);
});
test('UI-ITEM-COMMENTS wrong receipt identity or epochs retains the request for exact recovery',async()=>{
  for(const mutate of [r=>r.operation_id='bad',r=>r.comment_id='other',r=>r.document_id='host',r=>r.workspace_id='other',r=>r.workspace_epoch++,r=>r.status='wrong']){const f=fixture();await f.bind();f.draft('receipt');f.bad(mutate);assert.equal(await f.submit(),false);assert.equal(f.view().pending,true);assert.equal(await f.retry(),true);assert.equal(f.receipts.size,1);}
});
test('UI-ITEM-COMMENTS reply, author edit, resolution, reaction and deletion use current versions',async()=>{
  const f=fixture();await f.bind();f.draft('root');await f.submit();const id=f.remote.items[0].id;
  f.c.draft(f.context,{content:'reply',parent:id});await f.submit();assert.equal(f.remote.items[1].parent_comment_id,id);
  f.c.draft(f.context,{content:'edited',editing:id,parent:''});assert.equal(await f.c.mutate(f.context,'edit',id),true);
  assert.equal(await f.c.mutate(f.context,'resolve',id,true),true);assert.equal(await f.c.mutate(f.context,'react',id,'👍'),true);assert.equal(await f.c.mutate(f.context,'delete',id),true);
  assert.equal(f.sent[3].body.expected_row_version,2);assert.equal(f.sent[4].body.expected_row_version,3);assert.equal(f.sent[5].body.expected_row_version,4);
});
test('UI-ITEM-COMMENTS author permissions and missing reply parents reject before sending',async()=>{
  const f=fixture();await f.bind();f.draft('one');await f.submit();const item=f.remote.items[0];item.author_subject_id='someone';await f.c.refresh(f.context);
  f.draft('edit');assert.equal(await f.c.mutate(f.context,'edit',item.id),false);assert.equal(f.sent.length,1);
  f.c.draft(f.context,{content:'reply',parent:'gone'});assert.equal(await f.submit(),false);assert.equal(f.sent.length,1);
});
test('UI-ITEM-COMMENTS concurrent clicks send once; failed post-ACK read never resends',async()=>{
  const f=fixture();await f.bind();f.draft('once');let release;f.gate(new Promise(r=>release=r));const saving=f.submit();await wait(()=>f.sent.length);assert.equal(await f.submit(),false);f.readFailure(true);release();assert.equal(await saving,true);assert.equal(f.view().pending,false);assert.equal(await f.retry(),false);assert.equal(f.sent.length,1);
});
test('UI-ITEM-COMMENTS journal namespace separates actor/workspace/source/database/row and rejects malformed recovery',async()=>{
  const f=fixture(),base=await api.journalScope(f.context);for(const field of ['realm','userId','workspaceId','pageId','databaseId','rowId'])assert.notDeepEqual(await api.journalScope({...f.context,[field]:'other'}),base);
  await f.bind();f.draft('safe');await f.c.beforeLeave(f.context);[...f.records.values()][0][0].identity='wrong';f.restart();await f.bind();assert.equal(f.view().ready,false);assert.equal(await f.c.park(f.context),false);assert.equal(await f.submit(),false);
});
test('UI-ITEM-COMMENTS stale tabs cannot overwrite a newer persisted request or draft',async()=>{
  const f=fixture();await f.bind();f.draft('first');await f.c.beforeLeave(f.context);const old=f.c;f.restart();await f.bind();
  f.draft('newer');await f.c.beforeLeave(f.context);old.draft(f.context,{content:'stale'});assert.equal(await old.beforeLeave(f.context),false);
  assert.equal([...f.records.values()][0][0].draft.content,'newer');assert.equal(f.sent.length,0);
});
test('UI-ITEM-COMMENTS local discard clears an uncertain request without deleting its committed server comment',async()=>{
  const f=fixture();await f.bind();f.draft('committed');f.lose();assert.equal(await f.submit(),false);assert.equal(await f.c.discard(f.context),true);
  assert.equal(f.view().pending,false);assert.equal(f.view().draft.content,'');assert.equal(f.remote.items[0].content,'committed');assert.equal(f.sent.length,1);
});
test('UI-ITEM-COMMENTS encrypted serialization reorders keys without creating a false cross-tab conflict',async()=>{
  const f=fixture();await f.bind();f.draft('one');await f.c.beforeLeave(f.context);f.draft('two');await f.c.beforeLeave(f.context);assert.equal(await f.submit(),true);assert.equal(f.view().pending,false);
});
test('UI-ITEM-COMMENTS editing preserves its original version across reload and rejects concurrent changes',async()=>{
  const f=fixture();await f.bind();f.draft('original');await f.submit();const id=f.remote.items[0].id;
  f.c.draft(f.context,{editing:id,content:'my pending edit'});await f.c.beforeLeave(f.context);f.remote.items[0].row_version++;f.remote.items[0].content='remote edit';
  f.restart();await f.bind();assert.equal(await f.c.mutate(f.context,'edit',id),false);assert.equal(f.view().draft.content,'my pending edit');assert.equal(f.remote.items[0].content,'remote edit');assert.equal(f.sent.length,1);
});
test('UI-ITEM-COMMENTS a source change during initial loading queues a fresh authority read',async()=>{
  const f=fixture();let release;f.readGate(new Promise(r=>release=r));const binding=f.bind();await wait(()=>f.reads()===1);
  f.remote.role='view';const first=f.c.refresh(f.context),second=f.c.refresh(f.context);release();await binding;await Promise.all([first,second]);
  assert.equal(f.reads(),2);assert.equal(f.view().canComment,false);
});
test('UI-ITEM-COMMENTS queued authority reads stop when the user leaves the source',async()=>{
  const f=fixture();let release;f.readGate(new Promise(r=>release=r));const binding=f.bind();await wait(()=>f.reads()===1);
  const refreshing=f.c.refresh(f.context);f.live(false);release();await binding;assert.equal(await refreshing,false);assert.equal(f.reads(),1);
});
test('UI-ITEM-COMMENTS a source change during an active refresh is read after the stale response',async()=>{
  const f=fixture();await f.bind();let release;f.readGate(new Promise(r=>release=r));
  const first=f.c.refresh(f.context);await wait(()=>f.reads()===2);
  f.remote.role='view';const second=f.c.refresh(f.context);release();await Promise.all([first,second]);
  assert.equal(f.reads(),3);assert.equal(f.view().canComment,false);
});
test('UI-ITEM-COMMENTS parking waits for in-flight persistence without sending another request',async()=>{
  const f=fixture();await f.bind();f.draft('parked');let release;f.gate(new Promise(r=>release=r));f.lose();const sending=f.submit();await wait(()=>f.sent.length===1);
  f.live(false);const parked=f.c.park(f.context);release();assert.equal(await sending,false);assert.equal(await parked,true);assert.equal(f.view().pending,true);assert.equal(f.sent.length,1);
});

function directFixture(){
  const context={realm:'https://fixture.invalid',userId:'viewer',workspaceId:'ws',
    pageId:'independent-database:db',databaseId:'db',rowId:'row',databaseOnly:true};
  const records=new Map(),sent=[],receipts=new Map();let n=0,lose=false,bad=null,controller;
  const remote={contract:'code_assist.notion.database_sharing.v1',workspace_id:'ws',
    database_id:'db',row_id:'row',item_target:api.target(context),role:'comment',
    workspace_epoch:1,document_epoch:2,database_epoch:3,document_sequence:4,items:[]};
  const journal={list:async key=>clone(records.get(JSON.stringify(key))||[]),
    put:async(key,value)=>records.set(JSON.stringify(key),[stable(clone(value))])};
  const host={journal,current:()=>true,read:async()=>clone(remote),
    operationId:()=>`operation-${++n}`,commentId:()=>`comment-${++n}`,
    mentions:content=>[...content.matchAll(/@([a-z]+)/g)].map(match=>match[1]),
    send:async(_context,request)=>{
      sent.push(clone(request));const b=request.body;
      if(receipts.has(b.operation_id))return clone(receipts.get(b.operation_id));
      const result={contract:remote.contract,workspace_id:'ws',database_id:'db',row_id:'row',
        comment_id:request.comment,operation_id:b.operation_id,workspace_epoch:1,
        document_epoch:2,database_epoch:3,document_sequence:++remote.document_sequence};
      const item=remote.items.find(item=>item.id===request.comment);
      if(request.action==='create')remote.items.push({id:request.comment,anchor:api.target(context),
        author_subject_id:'viewer',content:b.content,parent_comment_id:b.parent_comment_id||null,row_version:1});
      else if(request.action==='delete'){remote.items=remote.items.filter(other=>other!==item);result.deleted=true;}
      else {result.row_version=++item.row_version;
        if(request.action==='edit')item.content=b.content;
        if(request.action==='resolve')result.resolved=item.resolved=b.resolved;
        if(request.action==='react'){result.emoji=b.emoji;result.added=true;}}
      receipts.set(b.operation_id,clone(result));
      if(lose){lose=false;throw Error('response lost');}
      if(bad){bad(result);bad=null;}
      return result;
    }};
  controller=api.create(host);
  return {context,remote,records,sent,receipts,
    get c(){return controller;},bind:()=>controller.bind(context),
    draft:content=>controller.draft(context,{content}),
    submit:()=>controller.mutate(context,'create'),
    restart:()=>controller=api.create(host),lose:()=>lose=true,bad:mutate=>bad=mutate};
}

test('UI-ITEM-COMMENTS direct database request survives a lost receipt without a page identifier',async()=>{
  const f=directFixture();await f.bind();f.draft('direct comment');f.lose();
  assert.equal(await f.submit(),false);assert.equal(f.c.pending(f.context),true);
  assert.deepEqual(Object.keys(f.sent[0].body).sort(),[
    'comment_id','content','expected_database_epoch','expected_document_epoch',
    'expected_workspace_epoch','mention_subject_ids','operation_id','parent_comment_id'].sort());
  assert.equal(JSON.stringify(f.sent[0]).includes('source'),false);
  f.restart();await f.bind();assert.equal(await f.c.retry(f.context),true);
  assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.remote.items.length,1);
});

test('UI-ITEM-COMMENTS direct database edit, reply, resolve, reaction and delete use scoped receipts',async()=>{
  const f=directFixture();await f.bind();f.draft('root');await f.submit();const id=f.remote.items[0].id;
  f.c.draft(f.context,{content:'reply',parent:id});await f.submit();
  assert.equal(f.remote.items[1].parent_comment_id,id);
  f.c.draft(f.context,{content:'edited',editing:id,parent:''});
  assert.equal(await f.c.mutate(f.context,'edit',id),true);
  assert.equal(await f.c.mutate(f.context,'resolve',id,true),true);
  assert.equal(await f.c.mutate(f.context,'react',id,'👍'),true);
  assert.equal(await f.c.mutate(f.context,'delete',id),true);
  assert.equal(f.sent[2].body.expected_database_epoch,3);
  assert.deepEqual(f.sent[2].body.mention_subject_ids,[]);
  assert.equal(f.remote.items.some(item=>item.id===id),false);
});

test('UI-ITEM-COMMENTS direct mentions are included in durable create and edit requests',async()=>{
  const f=directFixture();await f.bind();f.draft('hello @collab');
  assert.equal(await f.submit(),true);
  const id=f.remote.items[0].id;
  assert.deepEqual(f.sent[0].body.mention_subject_ids,['collab']);
  f.c.draft(f.context,{content:'again @friend',editing:id});
  assert.equal(await f.c.mutate(f.context,'edit',id),true);
  assert.deepEqual(f.sent[1].body.mention_subject_ids,['friend']);
  assert.equal(JSON.stringify(f.sent).includes('document_id'),false);
});

test('UI-ITEM-COMMENTS direct database rejects a parent-shaped receipt and retains its exact retry',async()=>{
  const f=directFixture();await f.bind();f.draft('safe');
  f.bad(result=>result.document_id='private-parent');
  assert.equal(await f.submit(),false);assert.equal(f.c.pending(f.context),true);
  assert.equal(await f.c.retry(f.context),true);
  assert.deepEqual(f.sent[0],f.sent[1]);
});
