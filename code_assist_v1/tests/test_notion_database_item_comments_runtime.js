'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const comments=require('../notion_static/notion_database_item_comments.js'),source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const until=async fn=>{for(let i=0;i<1000&&!fn();i++)await new Promise(r=>setImmediate(r));assert.ok(fn());};
function fixture(){
  const scope={realm:'https://fixture.invalid',userId:'reader',workspaceId:'host-workspace',pageId:'host'},calls=[],records=new Map(),renders=[];
  let lost=false,disposed=0,hostView,views=0;
  const data={source:{document_id:'source',database_id:'db'},database:{effective_role:'comment',capabilities:{row_comments:true},rows:[{id:'row'}]},
    witnesses:[{document_id:'source',workspace_id:'source-workspace',document_sequence:2,workspace_epoch:3,document_epoch:4}]};
  const model={scope,data,rowId:'row',blockId:'linked',item:{title:'Read-only title'},editable:false};
  const remote={contract:'code_assist.notion.live_collaboration_authority.v1',status:'comments_loaded',workspace_id:'source-workspace',document_id:'source',
    item_target:{kind:'database_row',database_id:'db',row_id:'row'},document_sequence:2,workspace_epoch:3,document_epoch:4,role:'comment',items:[]};
  const root={},body={querySelector:()=>model.item?root:null};
  const context={ItemComments:null,ItemCommentsUi:null,structuredClone,crypto:globalThis.crypto,navigator:{locks:{request:async(name,work)=>work()}},
    State:{user:{id:'reader'},users:[]},commentMentionSubjectIds:()=>[],linkedDatabaseScopeCurrent:s=>s===model.scope,
    LinkedDatabaseItems:{snapshot:()=>model,save:async()=>true},LinkedDatabaseViews:{refresh:async()=>views++},loadInboxItems:async()=>{},
    CollaborativeEditingOutbox:{list:async s=>structuredClone(records.get(JSON.stringify(s))||[]),put:async(s,e)=>records.set(JSON.stringify(s),[structuredClone(e)])},
    window:{NotionDatabaseItemComments:comments,NotionLinkedDatabaseCells:{errorMessage:e=>e.message},
      NotionDatabaseItemCommentsView:{create:(node,host)=>{hostView=host;return {render:v=>renders.push(v),dispose:()=>disposed++};}}},
    api:async(url,options)=>{
      calls.push({url,options:options?structuredClone(options):null});assert.match(url,/\/source-workspace\/pages\/source\/comments/);
      if(!options)return structuredClone(remote);
      assert.equal(options.body.anchor.database_id,'db');assert.equal(options.body.anchor.row_id,'row');
      const b=options.body;
      if(!remote.items.some(item=>item.id===b.comment_id))remote.items.push({id:b.comment_id,content:b.content,document_id:'source',anchor:structuredClone(remote.item_target),author_subject_id:'reader',row_version:1});
      const result={...remote,status:'comment_created',comment_id:b.comment_id,operation_id:b.operation_id,document_sequence:3};
      if(lost){lost=false;throw Error('lost');}return result;
    }};
  vm.createContext(context);vm.runInContext(part('function databaseItemCommentContext(','function itemAppearanceController()')+'\n'+part('async function refreshDatabaseItemPeek()','function linkedItemFieldHtml('),context);
  return {context,model,remote,calls,records,renders,body,mount:()=>context.syncDatabaseItemComments(model,body),host:()=>hostView,
    view:()=>context.ItemComments.snapshot(context.databaseItemCommentContext(model)),lost:()=>lost=true,disposed:()=>disposed,views:()=>views};
}
test('UI-ITEM-COMMENTS-RUNTIME read-only linked host posts only to its real source workspace and row',async()=>{
  const f=fixture();f.mount();await until(()=>f.view()?.canComment);f.host().draft({content:'Source comment'});assert.equal(await f.host().submit(),true);
  const writes=f.calls.filter(c=>c.options);assert.equal(writes.length,1);assert.match(writes[0].url,/source-workspace\/pages\/source\/comments$/);assert.equal(f.model.editable,false);assert.equal(f.remote.items[0].content,'Source comment');
});
test('UI-ITEM-COMMENTS-RUNTIME a changed source witness refreshes role and comments without replacing its draft',async()=>{
  const f=fixture();f.mount();await until(()=>f.view()?.canComment);f.host().draft({content:'Keep this draft'});await f.context.beforeDatabaseItemCommentsLeave(f.model);
  f.remote.role='view';f.remote.document_epoch++;f.model.data.witnesses[0].document_epoch++;f.mount();await until(()=>!f.view().canComment);
  assert.equal(f.view().draft.content,'Keep this draft');assert.equal(f.calls.length,2);assert.equal(await f.host().submit(),false);
});
test('UI-ITEM-COMMENTS-RUNTIME explicit refresh also reloads comments when the DB witness is unchanged',async()=>{
  const f=fixture();f.mount();await until(()=>f.view()?.canComment);
  f.remote.items.push({id:'external',content:'New comment',document_id:'source',anchor:structuredClone(f.remote.item_target),author_subject_id:'other',row_version:1});
  await f.context.refreshDatabaseItemPeek();assert.equal(f.view().items[0].content,'New comment');assert.equal(f.views(),1);assert.equal(f.calls.length,2);
});
test('UI-ITEM-COMMENTS-RUNTIME an inaccessible pane parks its exact request and recovers after authorized reopen',async()=>{
  const f=fixture();f.mount();await until(()=>f.view()?.canComment);f.host().draft({content:'Keep request'});f.lost();assert.equal(await f.host().submit(),false);
  f.model.unavailable='Source denied';f.model.item=null;f.mount();assert.equal(f.disposed(),1);assert.equal(await f.context.beforeDatabaseItemCommentsLeave(f.model),true);
  assert.equal(f.calls.filter(c=>c.options).length,1);assert.equal(f.view().pending,true);
  f.model.scope={...f.model.scope};f.model.unavailable='';f.model.item={title:'Restored'};f.mount();await until(()=>f.view().canComment);
  assert.equal(await f.host().retry(),true);const writes=f.calls.filter(c=>c.options);assert.deepEqual(writes[0].options,writes[1].options);assert.equal(f.remote.items.length,1);
});
