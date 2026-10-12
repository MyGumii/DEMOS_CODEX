'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const groups=require('../notion_static/notion_sidebar_page_groups.js');
const page=(id,parent_id='',position=0)=>({id,parent_id,position,title:id});

test('UI-SIDEBAR-R7 page limits retain complete branches and every remaining page is reachable',()=>{
  const pages=[page('root-1'),...Array.from({length:20},(_,i)=>page('child-'+i,i?'child-'+(i-1):'root-1')),
    ...Array.from({length:12},(_,i)=>page('root-'+(i+2),'',i+1))];
  const original=JSON.stringify(pages),short=groups.plan(pages,{limit:10,tree:true});
  assert.equal(short.items.length,30);assert.equal(short.hiddenCount,3);assert.equal(short.canExpand,true);
  assert.ok(short.items.some(p=>p.id==='child-19'));
  assert.deepEqual(groups.plan(pages,{limit:10,tree:true,expanded:true}).items,pages);
  assert.equal(JSON.stringify(pages),original);
});

test('UI-SIDEBAR-R7 unordered descendants and inaccessible parents never lose reachable branches',()=>{
  const pages=[page('child','parent'),page('orphan','unavailable',2),page('parent','',1)];
  assert.deepEqual(groups.plan(pages,{limit:1,tree:true}).items.map(p=>p.id),['child','parent']);
  assert.deepEqual(groups.plan(pages,{limit:1,tree:true,expanded:true}).items,pages);
});

test('UI-SIDEBAR-R7 compact mode keeps the current page and its ancestors visible',()=>{
  const pages=[page('first','',1),page('second','',2),page('third','',3),page('child','third')];
  assert.deepEqual(groups.plan(pages,{limit:1,tree:true,activeId:'child'}).items.map(p=>p.id),['first','third','child']);
  assert.equal(groups.plan(pages,{limit:1,tree:true,activeId:'missing'}).items.length,1);
});

test('UI-SIDEBAR-R7 flat favorites preserve their stored order and do not add unlisted parents',()=>{
  const pages=[page('second','parent',2),page('first','parent',1),page('third')];
  assert.deepEqual(groups.plan(pages,{limit:2}).items.map(p=>p.id),['second','first']);
  assert.equal(groups.plan(pages).items.length,3);
});

test('UI-SIDEBAR-R7 malformed cycles terminate and remain reachable without inventing ancestors',()=>{
  const pages=[page('a','b'),page('b','a'),page('self','self'),page('another')];
  assert.equal(groups.plan(pages,{limit:1,tree:true}).items.length,2);
  assert.equal(groups.plan(pages,{limit:1,tree:true,expanded:true}).items.length,4);
});

test('UI-SIDEBAR-R7 section expansion keeps focus and is isolated by account and workspace',()=>{
  let scope='owner/workspace-a',locked=false,drawn=[];
  const toggle={attributes:{},setAttribute(k,v){this.attributes[k]=v;},focus(){this.focused=true;},
    scrollIntoView(options){this.scrollOptions=options;}};
  const host={root:{id:'test-private'},toggle,pages:[page('a'),page('b'),page('c')],limit:1,
    options:{tree:true},label:'개인 페이지',scope:()=>scope,locked:()=>locked,draw:(_root,pages)=>drawn=pages};
  groups.render(host);assert.equal(drawn.length,1);assert.equal(toggle.hidden,false);
  const oldClick=toggle.onclick;locked=true;oldClick();assert.equal(drawn.length,1);
  locked=false;oldClick();assert.equal(drawn.length,3);assert.equal(toggle.focused,true);
  assert.deepEqual(toggle.scrollOptions,{block:'nearest',inline:'nearest'},'moved keyboard focus stays visible');
  assert.equal(toggle.attributes['aria-expanded'],'true');
  scope='other/workspace-a';groups.render(host);assert.equal(drawn.length,1);
  oldClick();assert.equal(drawn.length,1,'stale controls cannot affect another user');
  scope='owner/workspace-b';groups.render(host);assert.equal(drawn.length,1);
  scope='owner/workspace-a';groups.render(host);assert.equal(drawn.length,3);
  host.pages=[page('remaining')];groups.render(host);assert.equal(drawn.length,1);assert.equal(toggle.hidden,true);
});

test('UI-SIDEBAR-R7 an older shell without a more button keeps the full authorized list reachable',()=>{
  let drawn=[];
  groups.render({root:{id:'older-shell'},pages:[page('a'),page('b')],options:{tree:true},limit:1,
    scope:()=>'',locked:()=>false,draw:(_root,pages)=>drawn=pages});
  assert.equal(drawn.length,2);
});
