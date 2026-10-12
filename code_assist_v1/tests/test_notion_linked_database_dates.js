'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const dates=require('../notion_static/notion_database_dates.js'),linked=require('../notion_static/notion_linked_databases.js');
function fixture(editable=true,undatedView='calendar',timelineScale='month'){
  let active=true,commits=[],writes=[];
  const next={getAttribute:()=> '1',hasAttribute:()=>false},property={value:'due'},scale={value:'week'},assign={dataset:{undatedAssign:'0',undatedField:'date',undatedView},isConnected:true,
    closest:selector=>selector==='.timeline-block'?{querySelector:()=>({scrollLeft:29*72})}:null};
  const button={addEventListener:()=>{}},body={querySelectorAll:selector=>selector==='[data-calendar-nav],[data-calendar-today]'?[next]:selector==='[data-calendar-property]'?[property]:selector==='[data-timeline-scale]'?[scale]:selector==='[data-undated-assign]'?[assign]:[]};
  const node={isConnected:true,classList:{toggle(){}},replaceChildren(){},querySelectorAll:()=>[],querySelector:selector=>selector==='[data-linked-body]'?body:selector==='[data-linked-refresh]'?button:null};
  const scope={},block={id:'linked',type:'calendar',props:{linked_database:{document_id:'source',database_id:'db'},active_view_id:'a',views:[{id:'a',name:'Calendar',type:'calendar',config:{month:'2026-09',calendar_date_property:'date'}},{id:'b',name:'Other',type:'table',config:{}}]}};
  const slot={id:'linked',node,block},data={database:{title:'Source'},source:{document_id:'source'}};
  const projected=[];
  const host={dates,current:()=>active,visible:()=>true,canEdit:()=>editable,canEditCells:()=>editable,load:async()=>data,
    project:(_data,current)=>{const config=current.props.views.find(v=>v.id===current.props.active_view_id).config;projected.push(structuredClone(config));return {type:undatedView,props:{...config,timeline_scale:undatedView==='timeline'?timelineScale:config.timeline_scale,timeline_anchor:'2026-09-26',properties:[{id:'date',type:'date'}],rows:[{_canonical_row_id:'row',_canonical_cell_versions:{date:2},date:''}]}};},
    render:()=>'',commit:(_scope,previous,updated)=>{commits.push({previous,updated});return true;},assignDate:async(...args)=>writes.push(args.at(-1))};
  return {view:linked.create(host),scope,slot,block,next,property,scale,assign,commits,writes,projected,leave:()=>active=false};
}
test('UI-LINKED-DATES controls persist only local view changes for an editor',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);const snapshot=structuredClone(f.block);
  f.next.onclick();assert.equal(f.commits[0].updated.props.views[0].config.month,'2026-10');
  f.property.onchange();assert.equal(f.commits[1].updated.props.views[0].config.calendar_date_property,'due');
  assert.deepEqual(f.block,snapshot);assert.deepEqual(f.commits[0].updated.props.views[1],f.block.props.views[1]);assert.equal(f.writes.length,0);
});
test('UI-LINKED-DATES a reader can navigate locally without source or instance writes',async()=>{
  const f=fixture(false);await f.view.mount(f.scope,[f.slot]);assert.equal(f.property.disabled,true);assert.equal(f.scale.disabled,false);
  f.next.onclick();assert.equal(f.projected.at(-1).month,'2026-10');f.next.onclick();assert.equal(f.projected.at(-1).month,'2026-11');
  f.scale.onchange();assert.equal(f.projected.at(-1).timeline_scale,'week');
  await f.assign.onclick();assert.equal(f.writes.length,0);assert.equal(f.commits.length,0);assert.equal(f.block.props.views[0].config.month,'2026-09');
});
test('UI-LINKED-DATES assigning a date uses one versioned cell write; stale page callbacks cannot write',async()=>{
  const f=fixture();await f.view.mount(f.scope,[f.slot]);await f.assign.onclick();assert.equal(f.writes.length,1);assert.equal(f.writes[0].row_id,'row');assert.equal(f.writes[0].property_id,'date');assert.equal(f.writes[0].expected_target_version,2);assert.equal(f.commits.length,0);
  f.leave();await f.assign.onclick();f.next.onclick();assert.equal(f.writes.length,1);assert.equal(f.commits.length,0);
});
test('UI-LINKED-DATES month timeline assignment writes a five-day range',async()=>{
  const f=fixture(true,'timeline');await f.view.mount(f.scope,[f.slot]);await f.assign.onclick();
  assert.equal(f.writes.length,1);
  assert.deepEqual(f.writes[0].value,dates.undatedAssignmentValue('timeline','month',new Date()));
});
test('UI-LINKED-DATES hour timeline assignment uses the visible scroll position',async()=>{
  const f=fixture(true,'timeline','hour');await f.view.mount(f.scope,[f.slot]);await f.assign.onclick();
  assert.equal(f.writes.length,1);
  assert.deepEqual(f.writes[0].value,
    {start:'2026-09-26',end:'2026-09-26',time:'08:15',end_time:'09:15'});
});
test('UI-LINKED-DATES an undated menu cannot move the last timeline row to first in detail navigation',()=>{
  const source=require('node:fs').readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
  const helper=source.slice(source.indexOf('function linkedItemVisibleRows('),source.indexOf('function linkedItemController('));
  const buttons=[['c',true],['a',false],['a',false],['b',false],['c',false]].map(([id,menu])=>({dataset:{linkedRowId:id},closest:()=>menu?{}:null}));
  const context={document:{querySelector:()=>({querySelectorAll:()=>buttons})},CSS:{escape:value=>value},databaseRows:block=>block.props.rows};
  require('node:vm').runInNewContext(helper+'; result=linkedItemVisibleRows({id:"linked",props:{rows:[{_canonical_row_id:"a"},{_canonical_row_id:"b"},{_canonical_row_id:"c"}]}})',context);
  assert.deepEqual(Array.from(context.result,entry=>entry.item._canonical_row_id),['a','b','c']);
});
