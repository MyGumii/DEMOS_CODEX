'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const renderer = require('../notion_static/notion_document_preview.js');
const bridge = require('../notion_static/notion_database_bridge.js');
const block = (type, content, props = {}, id = type) => ({id, type, content, props});
const data = {contract: 'code_assist.notion.search_preview.v1', page: {id: 'a', title: 'Page'}, references: {}, blocks: [
  block('heading1', 'Heading <literal>', {layout_group: 'columns', layout_columns: 2, layout_column: 0}),
  block('heading4', 'Fourth heading'),
  block('simple_table', '', {rows: [['A', '<script>']], header_row: true, layout_group: 'columns', layout_columns: 2, layout_column: 1}),
  block('todo', 'Done', {checked: true}), block('numbered', 'First'), block('numbered', 'Second', {}, 'second'),
  block('code', '<img onerror="bad">'), block('child_page', 'Old private title', {page_id: 'hidden'}),
  block('toggle', 'Closed', {open: false}), block('paragraph', 'Hidden child', {indent: 1}, 'hidden-child'),
  block('tabs', '', {tabs: [{id: 'first', name: 'One'}, {id: 'second', name: 'Two'}]}, 'tabs'),
  block('paragraph', 'Default first tab', {tab_group: 'tabs', tab_id: 'first'}, 'first-tab-member'),
  block('paragraph', 'Inactive second tab', {tab_group: 'tabs', tab_id: 'second'}, 'second-tab-member'),
]};
const before = JSON.stringify(data), html = renderer.render(data);
assert.equal(JSON.stringify(data), before, 'rendering never edits the source document');
assert.match(html, /<h3>Heading &lt;literal&gt;<\/h3>/);
assert.match(html, /<h6>Fourth heading<\/h6>/);
assert.match(html, /--preview-columns:2/); assert.match(html, /<th>&lt;script&gt;<\/th>/);
assert.match(html, /<ol start="2">/); assert.match(html, /접근할 수 없는 페이지/);
assert.match(html, /Default first tab/);
assert.doesNotMatch(html, /Old private title|Hidden child|Inactive second tab|<script|<img|<input|<textarea|contenteditable|draggable/);
assert.match(html, /&lt;img onerror=/);
assert.doesNotThrow(() => renderer.render({blocks: [block('paragraph', 'x', {layout_group:'x',layout_columns:2.8,layout_column:1.4,indent:1.5})]}));
const db = {id:'db',title:'Tasks',properties:[{id:'name',name:'Name',type:'title'}, {id:'calc',name:'Total',type:'formula'},
  {id:'related',name:'Related',type:'relation'}, {id:'person',name:'Owner',type:'person'}],
  views:[],rows:[{id:'private-row-id',cells:{name:'Release',calc:'stale',related:['private-target-id'],person:['private-person-id']},computed:{calc:84}}],
  relation_labels:{related:{'private-target-id':'Linked title'}}};
const dbHtml = renderer.render({databases:[db],blocks:[block('table','stale title',{database_id:'db'})]}, {
  databaseBlock:(b,d)=>bridge.canonicalToBlock(d,b), personLabel:()=> '테스터',
});
assert.match(dbHtml, /Tasks/); assert.match(dbHtml, /84/); assert.match(dbHtml, /Linked title/); assert.match(dbHtml, /테스터/);
assert.doesNotMatch(dbHtml, /stale|private-/);

const source = fs.readFileSync(require.resolve('../notion_static/notion.js'), 'utf8');
function extract(name) {
  const start = new RegExp(`^(?:async )?function ${name}\\(`, 'm').exec(source);
  assert.ok(start, name); const tail = source.slice(start.index), next = /\n(?:async )?function \w+\(/.exec(tail);
  return tail.slice(0, next ? next.index : undefined);
}
function harness() {
  const nodes = new Map(), requests = [], opened = [];
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {hidden:false,innerHTML:'old',attrs:{},listeners:{},
      replaceChildren(){this.innerHTML='';},setAttribute(k,v){this.attrs[k]=v;},addEventListener(k,v){this.listeners[k]=v;}});
    return nodes.get(id);
  };
  const c = vm.createContext({
    State:{searchItems:[{id:'a'},{id:'b'}],activeSearchPageId:'a',searchRequestSequence:1,searchPreviewSequence:0,
      user:{id:'owner'},navigation:{authoritative:true,enabled:true,collaborativeSearch:true,workspaceId:'ws'}},
    $:node,window:{NotionDocumentPreview:renderer},structuredClone,DatabaseBridge:bridge,
    api:url=>new Promise((resolve,reject)=>requests.push({url,resolve,reject})),
    searchPreviewRichHtml:()=>'',searchPreviewPersonLabel:()=>'',notionFileUrl:()=>'',visibleDatabaseRows:()=>[],
    pageIconHtml:()=>'',iconSvg:()=>'',escapeHtml:String,pageUrl:id=>'/page/'+id,copyText(){},
    closeSearchOverlay(){node('#searchOverlay').hidden=true;},openPage:id=>opened.push(id),
  });
  vm.runInContext(['globalSearchScopeKey','searchResultCanOpen','invalidateSearchPreview','renderSearchPreview'].map(extract).join('\n'),c);
  return {c,node,requests,opened};
}
const payload = (id, content=id) => ({page:{id,title:content},blocks:[block('paragraph',content)]});
(async()=>{
  {
    const h=harness(),a=h.c.renderSearchPreview();
    assert.match(h.requests[0].url,/workspaces\/ws\/pages\/a\/search-preview$/);
    h.c.State.activeSearchPageId='b'; const b=h.c.renderSearchPreview();
    h.requests[1].resolve(payload('b','Latest body')); await b;
    const openOld=h.node('#previewOpenBtn').listeners.click;
    h.requests[0].resolve(payload('a','Stale body')); await a;
    assert.match(h.node('#searchPreview').innerHTML,/Latest body/); assert.doesNotMatch(h.node('#searchPreview').innerHTML,/Stale/);
    h.c.State.activeSearchPageId='a'; openOld(); assert.deepEqual(h.opened,[]);
    const denied=h.c.renderSearchPreview(); assert.doesNotMatch(h.node('#searchPreview').innerHTML,/Latest body/);
    h.requests[2].reject(Error('접근 권한 없음')); await denied;
    assert.match(h.node('#searchPreview').innerHTML,/접근 권한 없음/);
    const retry=h.node('#previewRetryBtn').listeners.click;
    h.c.State.searchRequestSequence++; retry(); assert.equal(h.requests.length,3);
  }
  for(const change of [h=>h.node('#searchOverlay').hidden=true,h=>h.c.State.searchRequestSequence++,
    h=>h.c.State.navigation.workspaceId='other',h=>h.c.State.user.id='other',h=>h.c.invalidateSearchPreview()]){
    const h=harness(),pending=h.c.renderSearchPreview(); change(h);
    h.requests[0].resolve(payload('a','Private body')); await pending;
    assert.doesNotMatch(h.node('#searchPreview').innerHTML,/Private body/);
    assert.equal(h.node('#searchPreview').attrs['aria-busy'],'false');
  }
  {
    const h=harness(); h.c.State.searchPreviewVisible=false; await h.c.renderSearchPreview();
    assert.equal(h.requests.length,0); h.c.State.searchPreviewVisible=true; h.c.State.navigation.authoritative=false;
    const pending=h.c.renderSearchPreview(); assert.equal(h.requests[0].url,'/api/notion/pages/a');
    h.requests[0].resolve(payload('a','General body')); await pending;
    assert.match(h.node('#searchPreview').innerHTML,/General body/);
    h.node('#previewOpenBtn').listeners.click(); assert.deepEqual(h.opened,['a']);
  }
  {
    const h=harness(),pending=h.c.renderSearchPreview();
    h.requests[0].resolve(payload('a')); await pending;
    h.c.window.getSelection=()=>({isCollapsed:false});
    h.node('#previewDocumentOpen').listeners.click(); assert.deepEqual(h.opened,[]);
    h.node('#previewDocumentOpen').listeners.keydown({key:'Enter',isComposing:true}); assert.deepEqual(h.opened,[]);
    h.node('#previewDocumentOpen').listeners.keydown({key:' ',preventDefault(){}}); assert.deepEqual(h.opened,['a']);
  }
  console.log('UI-SEARCH-PREVIEW: passive structure, escaping, current computed/reference labels, no mutation; async order, scope, close, hidden, retry and general route passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
