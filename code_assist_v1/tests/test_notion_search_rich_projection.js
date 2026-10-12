'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),path=require('node:path');
const {spawnSync}=require('node:child_process');
require('../notion_static/vendor/yjs-13.6.32.bundle.js');
const Y=globalThis.NotionYjs;
function project(doc,rich=true,identities=false) {
  const request={contract:'code_assist.notion.search_projection.v1',
    documents:[[Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64')]],include_rich_text:rich,include_block_identities:identities};
  const result=spawnSync(process.execPath,[path.join(__dirname,'../notion_collaboration/search_projection.cjs')],
    {input:JSON.stringify(request),encoding:'utf8',timeout:15000,windowsHide:true});
  assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout).documents[0];
}
test('rich replay exposes exact title and partial text runs without changing the default projection',()=>{
  const doc=new Y.Doc();doc.getText('page-title').insert(0,'한글 제목',{italic:true});
  const item=new Y.Map(),text=new Y.Text();
  item.set('type','paragraph');item.set('content',text);item.set('props','{"color":"blue"}');
  doc.getMap('blocks').set('text',item);doc.getArray('block-order').push(['text']);
  text.insert(0,'앞 문장 ');text.insert(text.length,'강조',{bold:true,color:'red'});
  const plain=project(doc,false),rich=project(doc);
  assert.deepEqual({title:rich.title,blocks:rich.blocks},plain);
  assert.deepEqual(rich.rich_text.title,[{insert:'한글 제목',attributes:{italic:true}}]);
  assert.deepEqual(rich.rich_text.blocks.text,[{insert:'앞 문장 '},{insert:'강조',attributes:{bold:true,color:'red'}}]);
  doc.destroy();
});
test('rich replay keeps reserved-looking block IDs as ordinary keys and legacy strings as plain runs',()=>{
  const doc=new Y.Doc();for(const id of ['__proto__','constructor']) {
    const item=new Y.Map();item.set('content','본문 '+id);doc.getMap('blocks').set(id,item);doc.getArray('block-order').push([id]);
  }
  const result=project(doc);assert.equal(result.rich_text.title,null);
  assert.deepEqual(Object.keys(result.rich_text.blocks),['__proto__','constructor']);
  for(const id of ['__proto__','constructor'])assert.deepEqual(result.rich_text.blocks[id],[{insert:'본문 '+id}]);
  doc.destroy();
});
test('title-only rich replay retains a missing body instead of inventing an empty document',()=>{
  const doc=new Y.Doc();doc.getText('page-title').insert(0,'제목만');
  assert.deepEqual(project(doc),{title:'제목만',blocks:null,rich_text:{title:[{insert:'제목만'}],blocks:{}}});
  doc.destroy();
});
test('optional structural identities distinguish a new restored map from the deletion state vector',()=>{
  const doc=new Y.Doc(),original=new Y.Map();original.set('content',new Y.Text('처음'));
  doc.getMap('blocks').set('text',original);doc.getArray('block-order').push(['text']);
  const archive=project(doc,true,true),first=archive.block_identities.text;
  assert(first.clock<archive.state_vector[first.client]);
  const restored=new Y.Map();restored.set('content',new Y.Text('복원 뒤의 입력'));doc.getMap('blocks').set('text',restored);
  const current=project(doc,true,true),next=current.block_identities.text;
  assert(next.clock>=(archive.state_vector[next.client]||0));
  assert.deepEqual(next,{client:restored._item.id.client,clock:restored._item.id.clock});
  assert.equal(project(doc).block_identities,undefined);doc.destroy();
});
