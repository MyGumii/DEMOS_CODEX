'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),{webcrypto}=require('node:crypto');
const api=require('../notion_static/notion_synced_text.js');
const clone=v=>structuredClone(v),deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};};
const scope={realm:'http://fixture',userId:'owner',workspaceId:'ws',pageId:'reference-page'};
const text=(content,id='original')=>({id,content,rich_text_html:null});
function data(){const block={id:'original',type:'synced_block',content:'base',props:{synced_from:null},children:[{id:'child',type:'heading2',content:'child base',props:{},children:[]}]};return {
  instance:{document_id:scope.pageId,block_id:'ref'},origin:{document_id:'source',block_id:'original'},block,tree:clone(block),capabilities:{edit_text:true},
  witnesses:[scope.pageId,'source'].map(document_id=>({document_id,workspace_id:'ws',document_sequence:1,document_epoch:1,workspace_epoch:1,generation:1}))};}
function harness(storage=new Map()){
  const h={data:data(),calls:[],events:[],active:true,canEdit:true,storage,number:0};
  h.journal={async list(s){return clone(storage.get(s.page_id)||[]);},async put(s,e){storage.set(s.page_id,[...(storage.get(s.page_id)||[]).filter(v=>v.operation_id!==e.operation_id),clone(e)]);},async remove(s,id){const next=(storage.get(s.page_id)||[]).filter(v=>v.operation_id!==id);if(next.length)storage.set(s.page_id,next);else storage.delete(s.page_id);}};
  h.commit=request=>{
    const w=h.data.witnesses.find(w=>w.document_id==='source');w.document_sequence++;
    const updates=new Map(request.updates.map(v=>[v.id,v]));
    function apply(n){if(updates.has(n.id)){const v=updates.get(n.id);n.content=v.content;n.props.rich_text_html=v.rich_text_html;}for(const c of n.children||[])apply(c);}apply(h.data.tree);
    return {contract:'code_assist.notion.synced_text_edit.v1',instance:clone(h.data.instance),origin:clone(h.data.origin),operation_id:request.operation_id,
      document_id:'source',workspace_id:'ws',workspace_epoch:1,document_epoch:1,generation:1,document_sequence:w.document_sequence,changed_block_ids:request.updates.map(v=>v.id)};
  };
  h.send=async request=>h.commit(request);
  h.current=s=>h.active&&s.userId===scope.userId;
  h.controller=api.create({crypto:webcrypto,journal:h.journal,current:s=>h.current(s),canEdit:()=>h.canEdit,
    operationId:()=>`edit-${++h.number}`,state:(s,b,v)=>h.events.push(v),send:async(s,b,r)=>{h.calls.push(clone(r));return h.send(r);},
    changed:()=>h.controller.bind(scope,'ref',h.data)});
  h.bind=()=>h.controller.bind(scope,'ref',h.data);h.draft=v=>h.controller.draft(scope,'ref',v);h.flush=()=>h.controller.flush(scope,'ref');h.view=()=>h.controller.snapshot(scope,'ref');return h;
}
test('UI-SYNC-TEXT source-only text request is durable before send; exact receipt clears draft',async()=>{
 const h=harness();await h.bind();h.draft(text('한😀글'));
 h.send=async r=>{assert.equal([...h.storage.values()][0][0].request.operation_id,r.operation_id);return h.commit(r);};
 assert.equal(await h.flush(),true);assert.equal(h.calls.length,1);assert.deepEqual(h.calls[0].updates,[text('한😀글')]);
 assert.equal(h.view().dirty,false);assert.equal(h.storage.size,0);assert.equal(h.data.instance.block_id,'ref');
});
test('UI-SYNC-TEXT composition keeps a durable draft and cannot send partially composed text',async()=>{
 const h=harness();await h.bind();h.controller.composing(scope,'ref',true);h.draft(text('ㅈ'));
 assert.equal(await h.flush(),false);assert.equal(h.calls.length,0);assert.equal(h.controller.pending(scope),true);
 h.draft(text('제목'));h.controller.composing(scope,'ref',false);assert.equal(await h.flush(),true);assert.equal(h.calls[0].updates[0].content,'제목');
});
test('UI-SYNC-TEXT lost response retries identical operation and body after controller recreation',async()=>{
 const h=harness();await h.bind();h.draft(text('saved'));let receipt;
 h.send=async r=>{receipt=h.commit(r);throw Error('NetworkError');};
 assert.equal(await h.flush(),false);assert.equal(h.view().pending,true);
 assert.equal(await h.controller.discard(scope,'ref'),false);
 const restored=harness(h.storage);restored.data=clone(h.data);await restored.bind();restored.send=async()=>receipt;
 assert.equal(await restored.flush(),true);assert.deepEqual(restored.calls[0],h.calls[0]);assert.equal(restored.view().dirty,false);
});
test('UI-SYNC-TEXT typing during a save commits the later text in a second versioned request',async()=>{
 const h=harness();await h.bind();h.draft(text('first'));const wait=deferred(),started=deferred();
 h.send=async r=>{if(h.calls.length===1){started.resolve();await wait.promise;}return h.commit(r);};
 const save=h.flush();await started.promise;assert.equal(h.draft(text('second')),true);wait.resolve();
 assert.equal(await save,true);assert.equal(h.calls.length,2);assert.equal(h.calls[1].witnesses[1].document_sequence,2);assert.equal(h.data.tree.content,'second');
});
test('UI-SYNC-TEXT same-leaf remote conflict retains input; explicit rebase uses latest witness',async()=>{
 const h=harness();await h.bind();h.draft(text('mine'));h.data.tree.content='theirs';h.data.witnesses[1].document_sequence=4;await h.bind();
 assert.equal(h.view().conflict,true);assert.equal(h.view().tree.content,'mine');assert.equal(await h.flush(),false);assert.equal(h.calls.length,0);
 assert.deepEqual(h.view().differences,[{id:'original',latest:'theirs',draft:'mine'}]);
 assert.equal(await h.controller.rebase(scope,'ref'),true);assert.equal(h.calls[0].witnesses[1].document_sequence,4);assert.equal(h.data.tree.content,'mine');
});
test('UI-SYNC-TEXT unrelated remote text is retained while an unchanged leaf can save',async()=>{
 const h=harness();await h.bind();h.draft(text('mine'));h.data.tree.children[0].content='remote child';h.data.witnesses[1].document_sequence=5;await h.bind();
 assert.equal(h.view().conflict,false);assert.equal(await h.flush(),true);assert.equal(h.data.tree.children[0].content,'remote child');
});
test('UI-SYNC-TEXT access denial clears rendered source while preserving encrypted draft',async()=>{
 const h=harness();await h.bind();h.draft(text('mine'));h.send=async()=>{throw Object.assign(Error('denied'),{status:403});};
 assert.equal(await h.flush(),false);assert.equal(h.view().tree,null);assert.equal(h.view().editable,false);assert.equal(h.storage.size,1);
 assert.deepEqual(h.view().differences,[]);
 h.controller.unavailable(scope,'ref');h.data.capabilities.edit_text=false;await h.bind();assert.equal(h.draft(text('forbidden')),false);assert.equal(h.view().tree.content,'mine');
});
test('UI-SYNC-TEXT storage failure prevents send and keeps the draft recoverable in memory',async()=>{
 const h=harness();await h.bind();h.journal.put=async()=>{throw Error('disk full');};h.draft(text('mine'));
 assert.equal(await h.flush(),false);assert.equal(h.calls.length,0);assert.equal(h.view().tree.content,'mine');assert.equal(h.view().dirty,true);
});
test('UI-SYNC-TEXT access loss parks a draft before navigation, without another write',async()=>{
 const h=harness();await h.bind();h.draft(text('not lost'));h.controller.unavailable(scope,'ref');
 assert.equal(await h.controller.flushPage(scope),true);assert.equal(h.calls.length,0);assert.equal(h.storage.size,1);
 const restored=harness(h.storage);await restored.bind();assert.equal(restored.view().tree.content,'not lost');assert.equal(restored.view().dirty,true);
});
test('UI-SYNC-TEXT 409 resolves failed request without discarding input or auto-overwriting',async()=>{
 const h=harness();await h.bind();h.draft(text('mine'));h.send=async()=>{h.data.tree.content='winner';h.data.witnesses[1].document_sequence=3;throw Object.assign(Error('conflict'),{status:409});};
 assert.equal(await h.flush(),false);assert.equal(h.view().pending,false);assert.equal(h.view().conflict,true);assert.equal(h.view().tree.content,'mine');
 assert.equal(await h.controller.discard(scope,'ref'),true);assert.equal(h.view().tree.content,'winner');assert.equal(h.storage.size,0);
});
test('UI-SYNC-TEXT malformed save reply retains exact request and never reports saved',async()=>{
 const h=harness();await h.bind();h.draft(text('mine'));h.send=async r=>({...h.commit(r),instance:{document_id:'other',block_id:'ref'}});
 assert.equal(await h.flush(),false);assert.equal(h.view().pending,true);assert.equal(h.storage.size,1);
});
test('UI-SYNC-TEXT journal scopes separate user, workspace, page and instance; retargeted drafts do not apply',async()=>{
 const first=await api.journalScope(scope,'ref',webcrypto),second=await api.journalScope({...scope,pageId:'other'},'ref',webcrypto);
 assert.notEqual(first.page_id,second.page_id);assert.notEqual(first.page_id,(await api.journalScope(scope,'ref2',webcrypto)).page_id);
 const h=harness();await h.bind();h.draft(text('mine'));h.data.origin.block_id='replacement';await h.bind();
 assert.equal(h.view().available,true);assert.equal(h.view().editable,true);assert.equal(h.view().tree.content,'base');assert.equal(h.view().parked,1);
 assert.equal(await h.flush(),true);assert.equal(h.calls.length,0);
});
test('UI-SYNC-TEXT nested external references and deleted leaves cannot accept edits',async()=>{
 const h=harness();h.data.tree.children.push({id:'nested',type:'synced_block',content:'',props:{synced_from:{document_id:'external',block_id:'x'}},children:[{id:'secret',type:'text',content:'private',props:{}}]});
 await h.bind();assert.equal(h.draft(text('bad','secret')),false);h.draft(text('child edit','child'));h.data.tree.children=[];await h.bind();
 assert.equal(h.view().conflict,true);assert.equal(await h.controller.rebase(scope,'ref'),false);assert.equal(h.calls.length,0);
});
test('UI-SYNC-TEXT retargeted origins keep independent drafts across recreation and selective discard',async()=>{
 const h=harness();await h.bind();h.draft(text('mine A'));const original=clone(h.data);
 h.data.origin={document_id:'source-B',block_id:'original-B'};h.data.tree.id='original-B';h.data.tree.content='base B';
 await h.bind();assert.equal(h.view().tree.content,'base B');assert.equal(h.view().parked,1);
 h.draft(text('mine B','original-B'));assert.equal(await h.controller.flushPage({...scope,pageId:'irrelevant'}),true);
 h.controller.unavailable(scope,'ref');assert.equal(await h.controller.flushPage(scope),true);
 assert.equal([...h.storage.values()][0].length,2);assert.equal(h.calls.length,0);
 const reload=harness(h.storage);reload.data=clone(h.data);await reload.bind();
 assert.equal(reload.view().tree.content,'mine B');assert.equal(reload.view().parked,1);
 assert.equal(await reload.controller.discard(scope,'ref'),true);assert.equal([...h.storage.values()][0].length,1);
 reload.data=original;await reload.bind();assert.equal(reload.view().tree.content,'mine A');assert.equal(reload.view().parked,0);
 assert.equal(await reload.flush(),true);assert.equal(reload.storage.size,0);
});
test('UI-SYNC-TEXT a legacy draft survives opening a different origin and returns unchanged',async()=>{
 const h=harness();await h.bind();h.draft(text('legacy A'));h.controller.unavailable(scope,'ref');await h.controller.flushPage(scope);
 const entries=[...h.storage.values()][0];entries[0].operation_id='draft';
 const restored=harness(h.storage),original=clone(restored.data);restored.data.origin.block_id='B';restored.data.tree.id='B';restored.data.tree.content='base B';
 await restored.bind();assert.equal(restored.view().tree.content,'base B');assert.equal(restored.view().parked,1);
 restored.data=original;await restored.bind();assert.equal(restored.view().tree.content,'legacy A');
 assert.equal(await restored.flush(),true);assert.equal(restored.storage.size,0);
});
test('UI-SYNC-TEXT retarget during an uncertain save never replays against the new endpoint',async()=>{
 const h=harness();await h.bind();h.draft(text('pending A'));const wait=deferred(),started=deferred(),original=clone(h.data);
 h.send=async()=>{started.resolve();await wait.promise;throw Error('response lost');};const flight=h.flush();await started.promise;
 h.data.origin.block_id='B';h.data.tree.id='B';h.data.tree.content='base B';await h.bind();
 assert.equal(h.view().editable,true);wait.resolve();assert.equal(await flight,false);assert.equal(h.view().parked,1);
 assert.equal(await h.flush(),true);assert.equal(h.calls.length,1);
 h.data=original;await h.bind();assert.equal(h.view().pending,true);h.send=r=>h.commit(r);
 assert.equal(await h.flush(),true);assert.deepEqual(h.calls[1],h.calls[0]);
});
test('UI-SYNC-TEXT denial during journal load cannot resurrect a source',async()=>{
 const h=harness(),wait=deferred();h.journal.list=()=>wait.promise;
 const loading=h.bind();h.controller.unavailable(scope,'ref');wait.resolve([]);await loading;
 assert.equal(h.view(),null);assert.equal(h.draft(text('forbidden')),false);
 await h.bind();assert.equal(h.view().editable,true);
});
test('UI-SYNC-TEXT a late binding cannot replace the newest endpoint',async()=>{
 const h=harness(),wait=deferred();h.journal.list=()=>wait.promise;const first=h.bind();
 h.data=clone(h.data);h.data.origin.block_id='B';h.data.tree.id='B';h.data.tree.content='B';const second=h.bind();
 wait.resolve([]);await Promise.all([first,second]);assert.equal(h.view().tree.id,'B');assert.equal(h.view().editable,true);
});
test('UI-SYNC-TEXT stale input and menu callbacks cannot edit a new origin with the same leaf ID',async()=>{
 const h=harness();await h.bind();const old=clone(h.data.origin);
 h.data.origin.document_id='another-source';await h.bind();h.draft(text('new source draft'));
 assert.equal(h.controller.draft(scope,'ref',text('old composition end'),old),false);
 h.controller.composing(scope,'ref',true,old);assert.equal(h.view().composing,false);
 assert.equal(await h.controller.discard(scope,'ref',old),false);
 assert.equal(await h.controller.rebase(scope,'ref',old),false);
 await h.controller.flush(scope,'ref',old);assert.equal(h.calls.length,0);assert.equal(h.view().tree.content,'new source draft');
});
test('UI-SYNC-TEXT a denial from an obsolete navigation cannot hide the current source',async()=>{
 const h=harness(),oldScope={...scope,navigation:1},newScope={...scope,navigation:2};
 h.current=s=>s.navigation===1;await h.controller.bind(oldScope,'ref',h.data);
 h.current=s=>s.navigation===2;await h.controller.bind(newScope,'ref',h.data);
 h.controller.unavailable(oldScope,'ref');assert.equal(h.controller.snapshot(newScope,'ref').available,true);
 h.controller.unavailable(newScope,'ref');assert.equal(h.controller.snapshot(newScope,'ref').available,false);
});
