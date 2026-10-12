'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),vm=require('node:vm');
const {create}=require('../notion_static/notion_linked_database_items.js');
const clone=structuredClone;
function fixture(){
  const scope={pageId:'instance',userId:'owner'},renders=[],writes=[],sourceOpens=[];
  const data={source:{document_id:'source',database_id:'db'},database:{effective_role:'edit',rows:[
    {id:'a',cells:{title:'A',status:'Waiting',date:'2026-09-16'},cell_versions:{title:1,status:1,date:1}},
    {id:'b',cells:{title:'B',status:'Done'},cell_versions:{title:2,status:2}},
  ]}};
  let active=true,editable=true,order=['a','b'],fail=false,release=null,gate=null,restores=0;
  const state={busy:false,pending:0,values:[]};
  const block=()=>({id:'linked',props:{rows:data.database.rows.map(r=>({...r.cells,_canonical_row_id:r.id})),properties:[{id:'title',type:'title'},{id:'status',type:'select'},{id:'date',type:'date'}]}});
  const host={current:()=>active,canEdit:()=>editable,rows:b=>b.props.rows,properties:b=>b.props.properties,
    visibleRows:b=>order.map(id=>({item:b.props.rows.find(r=>r._canonical_row_id===id)})).filter(e=>e.item),
    fieldEditable:()=>true,value:(p,v)=>v,error:e=>e.message,render:(model,focus)=>renders.push({model,focus}),status:()=>({...state}),restoreFocus:()=>restores++,
    openSource:async id=>sourceOpens.push(id),history:async()=>true,
    change:async(s,id,cells)=>{
      assert.equal(s,scope);assert.equal(id,'linked');writes.push(clone(cells));if(gate)await gate;
      if(fail){state.pending=1;state.values=cells.map(c=>c.value);controller.status(scope,'linked',state);throw Error('connection lost');}
      for(const cell of cells){const row=data.database.rows.find(r=>r.id===cell.row_id);if(row.cell_versions[cell.property_id]!==cell.expected_target_version)throw Error('version conflict');}
      for(const cell of cells){const row=data.database.rows.find(r=>r.id===cell.row_id);row.cells[cell.property_id]=cell.value;row.cell_versions[cell.property_id]++;}
      controller.update(scope,'linked',block(),data);
    },
    retry:async()=>{for(const cell of writes.at(-1)){const row=data.database.rows.find(r=>r.id===cell.row_id);row.cells[cell.property_id]=cell.value;row.cell_versions[cell.property_id]++;}controller.update(scope,'linked',block(),data);state.pending=0;controller.status(scope,'linked',state);},
    discard:async()=>{state.pending=0;controller.status(scope,'linked',state);},
  };
  const controller=create(host);
  return {controller,scope,data,block,writes,renders,sourceOpens,state,
    open:id=>controller.open(scope,'linked',block(),data,id,{}),update:()=>controller.update(scope,'linked',block(),data),
    reorder:ids=>{order=ids;},readonly:()=>{editable=false;},leave:()=>{active=false;},fail:()=>{fail=true;},
    wait:()=>{gate=new Promise(resolve=>{release=resolve;});},release:()=>{release();gate=null;},restores:()=>restores};
}

test('UI-LINKED-ITEM details update by stable source row ID and navigate the current filtered order',async()=>{
  const f=fixture();await f.open('a');f.reorder(['b','a']);f.update();
  assert.equal(f.controller.snapshot().position,1);assert.equal(f.controller.snapshot().next,false);
  assert.equal(await f.controller.navigate(-1),true);assert.equal(f.controller.snapshot().rowId,'b');
  f.reorder(['a']);f.update();assert.equal(f.controller.snapshot().position,-1);assert.equal(f.controller.snapshot().item.title,'B');
  assert.equal(await f.controller.navigate(1),false);assert.equal(f.writes.length,0);
});

test('UI-LINKED-ITEM property edits batch only source cells and wait before closing',async()=>{
  const f=fixture();await f.open('a');const original=f.block();f.controller.draft('title','Edited A');f.controller.draft('status','Done');f.wait();
  const closing=f.controller.close();await Promise.resolve();assert.equal(f.controller.isOpen(),true);assert.equal(f.controller.snapshot().busy,true);
  f.release();assert.equal(await closing,true);assert.equal(f.controller.isOpen(),false);assert.equal(f.restores(),1);
  assert.deepEqual(f.writes,[[{row_id:'a',property_id:'title',value:'Edited A',expected_target_version:1},{row_id:'a',property_id:'status',value:'Done',expected_target_version:1}]]);
  assert.equal(original.props.rows[0].title,'A');assert.equal(f.data.database.rows[1].cells.title,'B');
});

test('UI-LINKED-ITEM a refresh preserves draft text and its original version, rejecting stale edits',async()=>{
  const f=fixture();await f.open('a');f.controller.draft('title','My draft');
  f.data.database.rows[0].cells.title='Other author';f.data.database.rows[0].cell_versions.title=2;f.update();
  assert.equal(f.controller.snapshot().drafts.get('title').value,'My draft');
  assert.equal(await f.controller.navigate(1),false);assert.equal(f.controller.snapshot().rowId,'a');
  assert.equal(f.writes[0][0].expected_target_version,1);assert.equal(f.data.database.rows[0].cells.title,'Other author');
  assert.equal(f.controller.snapshot().error,'version conflict');
});

test('UI-LINKED-ITEM a date picker opened before a remote edit cannot overwrite the newer date',async()=>{
  const f=fixture();await f.open('a');const openedVersion=f.data.database.rows[0].cell_versions.date;
  f.data.database.rows[0].cells.date='2026-09-17';f.data.database.rows[0].cell_versions.date=2;f.update();
  assert.equal(f.controller.draft('date','2026-09-18',openedVersion),false);
  assert.match(f.controller.snapshot().error,/다시 열어/);
  assert.equal(await f.controller.save(),true);
  assert.equal(f.writes.length,0);assert.equal(f.data.database.rows[0].cells.date,'2026-09-17');
  assert.equal(f.controller.draft('date','2026-09-18',2),true);
  assert.equal(await f.controller.save(),true);
  assert.equal(f.writes[0][0].expected_target_version,2);
});

test('UI-LINKED-ITEM response recovery clears the journaled draft without sending another version',async()=>{
  const f=fixture();await f.open('a');f.controller.draft('title','Offline draft');f.fail();
  assert.equal(await f.controller.save(),false);assert.equal(await f.controller.close(),false);
  assert.equal(f.controller.snapshot().drafts.get('title').value,'Offline draft');
  await f.controller.retry();assert.equal(f.controller.snapshot().drafts.size,0);assert.equal(f.controller.snapshot().item.title,'Offline draft');
  assert.equal(await f.controller.close(),true);assert.equal(f.writes.length,1);
});

test('UI-LINKED-ITEM composition and no-op edits do not issue writes or accidentally navigate',async()=>{
  const f=fixture();await f.open('a');f.controller.draft('title','A');assert.equal(await f.controller.save(),true);assert.equal(f.writes.length,0);
  f.controller.composing(true);f.controller.draft('title','한');assert.equal(await f.controller.close(),false);assert.equal(f.writes.length,0);
  f.controller.composing(false);f.controller.draft('title','한글');assert.equal(await f.controller.save(),true);assert.equal(f.data.database.rows[0].cells.title,'한글');
});

test('UI-LINKED-ITEM revoked access, removed blocks and navigation clear cached item values',async()=>{
  const f=fixture();await f.open('a');f.controller.draft('title','unsaved');f.readonly();f.update();
  assert.equal(f.controller.snapshot().editable,false);assert.equal(f.controller.snapshot().drafts.size,0);assert.equal(f.controller.draft('title','forbidden'),false);
  f.controller.unavailable(f.scope,'linked','Access denied');assert.equal(f.controller.snapshot().item,null);assert.deepEqual(f.controller.snapshot().data.database.rows,[]);
  f.controller.reconcile(f.scope,[]);assert.equal(f.controller.snapshot(),null);
  const g=fixture();await g.open('a');g.leave();g.update();assert.equal(g.controller.snapshot(),null);assert.equal(g.writes.length,0);
});

test('UI-LINKED-ITEM overlapping open requests select only the newest requested row',async()=>{
  const f=fixture();const first=f.open('a'),second=f.open('b');assert.equal(await first,false);assert.equal(await second,true);assert.equal(f.controller.snapshot().rowId,'b');
  await f.controller.source();assert.deepEqual(f.sourceOpens,['source']);assert.equal(f.controller.isOpen(),false);
});

test('UI-LINKED-ITEM board groups and duplicate timeline buttons use visible order once per source item',()=>{
  const source=require('node:fs').readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const helper=source.slice(source.indexOf('function linkedItemVisibleRows('),source.indexOf('function linkedItemController('));
  const context={document:{querySelector:()=>({querySelectorAll:()=>['b','a','b'].map(id=>({dataset:{linkedRowId:id}}))})},CSS:{escape:value=>value},databaseRows:block=>block.props.rows};
  require('node:vm').runInNewContext(helper+'; result=linkedItemVisibleRows({id:"linked",props:{rows:[{_canonical_row_id:"a"},{_canonical_row_id:"b"},{_canonical_row_id:"filtered"}]}})',context);
  assert.deepEqual(Array.from(context.result,entry=>entry.item._canonical_row_id),['b','a']);assert.deepEqual(Array.from(context.result,entry=>entry.index),[1,0]);
});

test('UI-LINKED-ITEM closing the item dismisses only its own date picker',()=>{
  const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const code=source.slice(source.indexOf('function renderLinkedItemPeek('),source.indexOf('function linkedCellController('));
  const anchor={},outside={},body={replaceChildren(){}},root={hidden:false,classList:{remove(){}},contains:item=>item===anchor};
  let dismissed=0;
  const context={initializeLinkedItemPeek:()=>({root,body,signature:'row-a',mode:'side'}),
    activeDatabaseDatePickerAnchor:anchor,activeDatabaseDatePickerDismiss:()=>dismissed++,
    document:{body:{classList:{remove(){}}}},$:()=>({inert:false}),
    ItemBodyUi:null,ItemAppearanceUi:null,ItemCommentsUi:null,
    DatabaseFileActionMenu:{toggle:null},syncSidePeekMobileTrigger(){}};
  vm.createContext(context);vm.runInContext(code,context);
  context.renderLinkedItemPeek(null);
  assert.equal(dismissed,1);assert.equal(root.hidden,true);
  context.activeDatabaseDatePickerAnchor=outside;root.hidden=false;
  context.renderLinkedItemPeek(null);
  assert.equal(dismissed,1);
});

test('UI-LINKED-ITEM a refreshed date picker stays on the same source row after sorting',()=>{
  const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const code=source.slice(source.indexOf('function refreshedDatabaseDateButton('),source.indexOf('function openDatabaseDatePicker('));
  const context={};vm.createContext(context);vm.runInContext(code,context);
  const previous={dataset:{dbDatePicker:'date',dbRowId:'row-a',dbRow:'0'}};
  const wrong={dataset:{dbDatePicker:'date',dbRowId:'row-b',dbRow:'0'}};
  const current={dataset:{dbDatePicker:'date',dbRowId:'row-a',dbRow:'1'}};
  const root={querySelectorAll:()=>[wrong,current]};
  assert.equal(context.refreshedDatabaseDateButton(root,previous),current);
  assert.equal(context.refreshedDatabaseDateButton({querySelectorAll:()=>[wrong]},previous),null);
});
