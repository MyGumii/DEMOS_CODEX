'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const actions=require('../notion_static/notion_synced_actions.js');
const instance={document_id:'page-b',block_id:'peer'},origin={document_id:'page-a',block_id:'original'};
const entry=(endpoint,original=false,location={kind:'page'})=>({endpoint,original,location,workspace_id:'ws',page_title:'제목 <b>',body_title:''});
const valid=()=>({contract:'code_assist.notion.synced_locations.v1',instance,origin,complete:true,
  entries:[entry(origin,true),entry(instance),entry({document_id:'page-c',block_id:'third'},false,{kind:'row',database_id:'db',row_id:'row'})],other_count:2});
test('UI-SYNC-LOCATIONS validates exact inventory including the invoking endpoint',()=>{
  const data=valid(),before=JSON.stringify(data);const others=actions.inventory(data,instance,origin);
  assert.deepEqual(others.map(e=>e.endpoint),[origin,instance,{document_id:'page-c',block_id:'third'}]);
  assert.equal(JSON.stringify(data),before);
});
test('UI-SYNC-LOCATIONS rejects partial, wrong scope, count, duplicate and changed original',()=>{
  for(const mutate of [d=>d.complete=false,d=>d.instance={...instance,document_id:'other'},d=>d.origin={...origin,block_id:'other'},
    d=>d.other_count=9,d=>d.entries.push(d.entries[1]),d=>d.entries[0].original=false,d=>d.entries[1].original=true,
    d=>d.entries[2].location={kind:'row'},d=>d.entries[2].page_title=null,d=>d.entries=d.entries.filter(e=>e.endpoint!==instance)]){
    const data=valid();mutate(data);assert.throws(()=>actions.inventory(data,instance,origin));
  }
});
test('UI-SYNC-LOCATIONS displays one original/current representative per page or DB body',()=>{
  const data=valid();data.entries.push(entry({...instance,block_id:'same-body'}));
  data.entries.push(entry({...origin,block_id:'origin-body-peer'}));
  const shown=actions.visibleLocations(data.entries,instance);
  assert.deepEqual(shown.map(e=>e.endpoint),[origin,instance,{document_id:'page-c',block_id:'third'}]);
  assert.equal(actions.visibleLocations(data.entries,origin)[0].original,true);
});
