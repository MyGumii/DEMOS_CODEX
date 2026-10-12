'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8'),clone=structuredClone;
const part=(a,b)=>source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));
const cells=require('../notion_static/notion_linked_database_cells.js'),items=require('../notion_static/notion_linked_database_items.js');
const bridge=require('../notion_static/notion_database_bridge.js');
function fixture(){
  const database={id:'db',title:'Tasks',version:1,effective_role:'edit_content',capabilities:{atomic_cell_batch:true,max_cell_batch:1000},
    properties:[{id:'title',name:'Name',type:'title',config:{},version:1},{id:'status',name:'Status',type:'status',config:{options:['Waiting','Done']},version:1}],
    views:[{id:'table',name:'Table',type:'table',config:{},version:1}],rows:[
      {id:'row-a',cells:{title:'A',status:'Waiting'},cell_versions:{title:1,status:1}},
      {id:'row-b',cells:{title:'B',status:'Done'},cell_versions:{title:1,status:1}}]};
  const project=()=>bridge.canonicalToBlock(database,{id:'block',props:{database_id:'db'}});
  const State={user:{id:'owner'},activePageId:'document',navigation:{workspaceId:'workspace',sequence:1},liveCollaboration:{durableCursor:1},
    pageData:{page:{id:'document',workspace_epoch:1,document_epoch:1,document_sequence:1},blocks:[project()],databases:[clone(database)]}};
  const records=new Map(),receipts=new Map(),sent=[],reads=[],paints=[],statuses=[],activities=[];let operation=0,lose=false,canEdit=true,docseq=1,corrupt=null;
  const journal={list:async s=>[...records.values()].filter(e=>e.scope.page_id===s.page_id).map(e=>clone(e.entry)),
    put:async(scope,entry)=>records.set(entry.operation_id,{scope:clone(scope),entry:clone(entry)}),remove:async(s,id)=>records.delete(id)};
  const context={State,structuredClone,Map,Set,Promise,DATABASE_BLOCK_TYPES:new Set(bridge.ADOPTED_VIEW_TYPES),CSS:{escape:x=>x},location:{origin:'https://fixture.invalid'},
    crypto:{randomUUID:()=>`id-${++operation}`},document:{activeElement:{isConnected:true,focus(){}},querySelector:()=>null},$:()=>({focus(){}}),
    CollaborativeEditingOutbox:journal,DatabaseBridge:bridge,EditorCore:{normalizeBlock:x=>x},
    PageMetadataSync:{activity:(scope,page)=>activities.push([clone(scope),page])},
    window:{NotionLinkedDatabaseCells:cells,NotionLinkedDatabaseItems:items,NotionAiNotes:{beforeNavigate:async()=>true}},
    activePageCanEdit:()=>canEdit,activeFullPageDatabase:()=>null,
    collaborativeDatabaseActive:()=>true,collaborativePageBasePath:()=>`/api/notion/workspaces/${State.navigation.workspaceId}/pages/${State.activePageId}`,
    currentDatabaseDocumentSequence:()=>Math.max(State.pageData.page.document_sequence,State.liveCollaboration.durableCursor),
    flushPageDraft:async()=>true,closeSidePeek:async()=>!context.controller?.isOpen()||context.controller.close(false),
    databaseRows:block=>block.props.rows,databaseProperties:block=>block.props.properties,visibleDatabaseRows:block=>block.props.rows.map((item,index)=>({item,index})),
    renderLinkedItemPeek:(model,focus)=>paints.push({model,focus}),setStatus:(...args)=>statuses.push(args),
    renderBlocks:()=>context.syncOwnedDatabaseItem(),openPage:async()=>{},
    openSidePeek:()=>{throw Error('A native row must never be opened as a document');},
    api:async(url,options)=>{
      if(!options){reads.push(url);assert.match(url,/\/databases\/db$/);return {database:clone(database)};}
      assert.match(url,/\/pages\/document\/databases\/commands$/);const body=clone(options.body);sent.push(body);
      assert.equal(records.get(body.operation_id).scope.page_id,'owned-cells:document');
      assert.equal(records.get(body.operation_id).entry.kind,'owned.cells.v1');
      if(receipts.has(body.operation_id))return clone(receipts.get(body.operation_id));
      assert.equal(body.expected_document_sequence,docseq);
      const changed=body.payload.cells.map(c=>{
        const row=database.rows.find(r=>r.id===c.row_id);assert.equal(row.cell_versions[c.property_id],c.expected_target_version);
        row.cells[c.property_id]=c.value;row.cell_versions[c.property_id]++;
        return {row_id:c.row_id,property_id:c.property_id,target_version:row.cell_versions[c.property_id]};});
      const result={workspace_id:'workspace',document_id:'document',database_id:'db',target_id:'db',operation_id:body.operation_id,
        contract:'code_assist.notion.collaborative_database_cell_batch.v1',status:'database_command_committed',
        command_kind:'database.cells.set',document_sequence:++docseq,cells:changed};
      receipts.set(body.operation_id,clone(result));if(lose){lose=false;throw Object.assign(Error('connection lost'),{status:503});}
      if(corrupt){const modify=corrupt;corrupt=null;modify(result);}return result;
    }};
  vm.createContext(context);
  const boot=()=>{vm.runInContext(part('function linkedDatabaseScope()','function linkedItemFieldHtml(')+
      part('function openSubPageModal(','function changeBlockType(')+part('async function reloadCanonicalDatabases(','async function flushCollaborativeDatabaseSync('),context);
    context.controller=context.linkedItemController();};
  boot();
  return {context,State,database,sent,reads,paints,records,statuses,activities,journal,
    open:id=>context.openSubPageModal(id),lose:()=>{lose=true;},corrupt:modify=>{corrupt=modify;},readonly:()=>{canEdit=false;},
    reboot:()=>{vm.runInContext('OwnedDatabaseCells=null;LinkedDatabaseItems=null;',context);context.controller=context.linkedItemController();}};
}

test('UI-OWNED-ITEM row buttons open canonical properties; title/status save and undo/redo preserve the other row',async()=>{
  const f=fixture();await f.open('row-a');const c=f.context.controller;
  assert.equal(c.snapshot().rowId,'row-a');assert.equal(c.snapshot().scope.itemKind,'owned');
  assert.equal(f.reads.length,1);c.draft('title','한글 항목');c.draft('status','Done');assert.equal(await c.save(),true);
  assert.equal(f.database.rows[0].cells.title,'한글 항목');assert.equal(f.database.rows[1].cells.title,'B');assert.equal(f.sent.length,1);
  assert.equal(f.records.size,0);assert.equal(c.snapshot().item.title,'한글 항목');
  assert.deepEqual(f.activities,[[{subject_id:'owner',workspace_id:'workspace'},'document']]);
  assert.equal(await c.history(-1),true);assert.equal(f.database.rows[0].cells.title,'A');
  assert.equal(await c.history(1),true);assert.equal(f.database.rows[0].cells.title,'한글 항목');
  assert.equal(await c.navigate(1),true);assert.equal(c.snapshot().rowId,'row-b');assert.equal(await c.close(),true);
});

test('UI-OWNED-ITEM response loss keeps its exact operation across reload and does not write twice',async()=>{
  const f=fixture();await f.open('row-a');f.lose();f.context.controller.draft('title','Recovered');
  assert.equal(await f.context.controller.save(),false);assert.equal(await f.context.controller.close(),false);
  assert.equal(f.records.size,1);assert.equal(f.database.rows[0].cell_versions.title,2);
  f.reboot();await f.open('row-a');
  assert.equal(f.records.size,0);assert.deepEqual(f.sent[0],f.sent[1]);assert.equal(f.database.rows[0].cell_versions.title,2);
  assert.equal(f.context.controller.snapshot().item.title,'Recovered');assert.equal(f.context.controller.snapshot().drafts.size,0);
});

test('UI-OWNED-ITEM confirmed writes survive a failed follow-up read without resubmitting their draft',async()=>{
  const f=fixture();await f.open('row-a');const c=f.context.controller,api=f.context.api;
  f.context.api=(url,options)=>options?api(url,options):Promise.reject(Object.assign(Error('read unavailable'),{status:503}));
  c.draft('title','Confirmed');assert.equal(await c.save(),true);
  assert.equal(f.records.size,0);assert.equal(c.snapshot().drafts.size,0);assert.equal(c.snapshot().item.title,'Confirmed');
  assert.equal(f.sent.length,1);assert.match(f.statuses.at(-1)[0],/변경은 저장했지만/);
  assert.equal(await c.close(),true);assert.equal(f.sent.length,1);
});

test('UI-OWNED-ITEM a late write receipt cannot overwrite a newer cell already received from collaboration',async()=>{
  const f=fixture();await f.open('row-a');const c=f.context.controller,api=f.context.api;
  f.context.api=async(url,options)=>{
    if(!options)throw Object.assign(Error('read unavailable'),{status:503});
    const result=await api(url,options),row=f.State.pageData.databases[0].rows[0];
    row.cells.title='Newer remote value';row.cell_versions.title=3;return result;
  };
  c.draft('title','Confirmed');assert.equal(await c.save(),true);
  assert.equal(c.snapshot().item.title,'Newer remote value');assert.equal(f.records.size,0);
  assert.equal(c.snapshot().drafts.size,0);assert.equal(f.sent.length,1);
});

test('UI-OWNED-ITEM permission denial during refresh closes cached detail instead of keeping it editable',async()=>{
  const f=fixture();await f.open('row-a');
  f.context.api=async()=>{throw Object.assign(Error('denied'),{status:403});};
  await f.context.refreshDatabaseItemPeek();assert.equal(f.context.controller.snapshot(),null);
  assert.match(f.statuses.at(-1)[0],/접근할 수 없습니다/);assert.equal(f.sent.length,0);
});

test('UI-OWNED-ITEM read-only access opens detail without mutation and IME blocks closing',async()=>{
  const f=fixture();f.readonly();await f.open('row-a');assert.equal(f.context.controller.snapshot().editable,false);
  assert.equal(f.context.controller.draft('title','No'),false);assert.equal(f.sent.length,0);
  const g=fixture();await g.open('row-a');g.context.controller.composing(true);g.context.controller.draft('title','조합');
  assert.equal(await g.context.controller.close(),false);assert.equal(g.sent.length,0);
  g.context.controller.composing(false);assert.equal(await g.context.controller.close(),true);assert.equal(g.database.rows[0].cells.title,'조합');
});

test('UI-OWNED-ITEM mismatched receipts preserve the journal until the exact committed result is recovered',async()=>{
  for(const mutate of [r=>r.operation_id='other',r=>r.target_id='other',r=>r.contract='other',
      r=>r.status='other',r=>r.document_sequence++,r=>r.cells[0].target_version++,r=>r.document_id='other',
      r=>r.linked_instance={document_id:'document',block_id:'block'}]){
    const f=fixture();await f.open('row-a');f.corrupt(mutate);const c=f.context.controller;
    c.draft('title','Kept');assert.equal(await c.save(),false);assert.equal(f.records.size,1);
    assert.equal(c.snapshot().status.pending,1);assert.equal(await c.close(),false);
    await c.retry();assert.equal(f.records.size,0);assert.deepEqual(f.sent[0],f.sent[1]);
    assert.equal(f.database.rows[0].cell_versions.title,2);assert.equal(c.snapshot().item.title,'Kept');
  }
});

test('UI-OWNED-ITEM stale actor/workspace/page reads cannot replace the current database',async()=>{
  for(const mutate of [f=>f.State.user.id='another',f=>f.State.navigation.workspaceId='other',f=>f.State.activePageId='elsewhere',f=>f.State.navigation.sequence++]){
    const f=fixture();let release;const read=f.context.api;f.context.api=(...args)=>new Promise(r=>{release=()=>read(...args).then(r);});
    const opening=f.open('row-a');while(!release)await Promise.resolve();mutate(f);release();await opening;
    assert.equal(f.context.controller.snapshot(),null);assert.equal(f.sent.length,0);assert.equal(f.State.pageData.databases[0].rows[0].cells.title,'A');
  }
});

test('UI-OWNED-ITEM outdated selection cannot replace a newer row opened on the same page',async()=>{
  const f=fixture();let release;const read=f.context.api;let first=true;
  f.context.api=(...args)=>{if(first){first=false;return new Promise(r=>{release=()=>read(...args).then(r);});}return read(...args);};
  const a=f.open('row-a');while(!release)await Promise.resolve();await f.open('row-b');release();await a;
  assert.equal(f.context.controller.snapshot().rowId,'row-b');
});

test('UI-OWNED-ITEM board order, repeated date buttons, and close focus follow the visible rows',async()=>{
  const f=fixture(),focused=[];
  const nodes=[{dataset:{tableRow:'1'}},{dataset:{calendarRow:'0'}},{dataset:{tableRow:'0'}}];
  const root={querySelectorAll:()=>nodes,querySelector:selector=>({focus:()=>focused.push(selector)})};
  f.context.document.querySelector=selector=>selector.startsWith('[data-block-id=')?root:null;
  await f.open('row-a');const c=f.context.controller;
  assert.equal(c.snapshot().position,1);assert.equal(c.snapshot().previous,true);assert.equal(c.snapshot().next,false);
  assert.equal(await c.navigate(-1),true);assert.equal(c.snapshot().rowId,'row-b');
  assert.equal(await c.close(),true);assert.match(focused[0],/data-table-row="1"/);
  assert.equal(f.sent.length,0);
});

test('UI-OWNED-ITEM opening and closing an unchanged row keeps the first view click bound to current data',async()=>{
  const f=fixture();f.database.views.push({id:'board',name:'Board',type:'board',config:{},version:1});
  f.State.pageData.databases=[clone(f.database)];
  f.State.pageData.blocks=[bridge.canonicalToBlock(f.database,{id:'block',props:{}})];
  f.context.EditorCore.deepClone=clone;f.context.canEditDatabaseSchema=()=>false;
  f.context.setTimeout=()=>{};
  vm.runInContext(part('const DATABASE_VIEW_LABELS =','function calculateDatabaseFormula('),f.context);
  const handlers=part('  row.querySelectorAll("[data-db-view-id]")','  row.querySelector("[data-db-add-view]")');
  vm.runInContext(`function bindViewControls(block,row){${handlers}}`,f.context);
  let choose;
  const bind=()=>f.context.bindViewControls(f.State.pageData.blocks[0],{
    querySelectorAll:()=>[{dataset:{dbViewId:'board'},addEventListener:(name,handler)=>choose=handler}],querySelector:()=>null,
  });
  f.context.renderBlocks=()=>{bind();f.context.syncOwnedDatabaseItem();};bind();
  await f.open('row-a');await f.context.controller.close();choose();
  assert.equal(f.State.pageData.blocks[0].props.active_view_id,'board');
  assert.equal(f.State.pageData.blocks[0].props.view,'board');assert.equal(f.sent.length,0);
  // A refresh may also land between painting the button and receiving its click.
  f.database.views[1].config={filters:[{property:'status',operator:'is',value:'Done'}]};
  f.State.pageData.blocks=[bridge.canonicalToBlock(f.database,{id:'block',props:{}})];
  choose();assert.equal(f.State.pageData.blocks[0].props.active_view_id,'board');
  assert.equal(f.State.pageData.blocks[0].props.filters[0].value,'Done');
  f.State.pageData.blocks[0].props.views=f.State.pageData.blocks[0].props.views.filter(v=>v.id!=='board');
  let dirtied=0;f.context.canEditDatabaseSchema=()=>true;f.context.markDirty=()=>dirtied++;f.context.captureEditorHistory=()=>{};
  choose();assert.equal(dirtied,0,'a removed view must not dirty the document');
});
