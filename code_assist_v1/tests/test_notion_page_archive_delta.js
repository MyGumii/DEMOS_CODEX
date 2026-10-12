'use strict';
const assert = require('node:assert/strict');
const {transform} = require('../notion_collaboration/page_move_delta.cjs');
const Y = globalThis.NotionYjs;
const seed = {title:'보존',blocks:[
  {id:'before',type:'paragraph',content:'앞 문단',props:{}},
  {id:'old-link',type:'child_page',content:'하위 페이지',props:{page_id:'child'}},
  {id:'after',type:'heading1',content:'뒤 제목',props:{}},
]};
const input = {document_id:'archive-parent',generation:1,seed,frames:[],child_id:'child',insert:null};
const archived = transform(input);
assert.deepEqual(archived.restore_anchor,{previous_block_id:'before',next_block_id:'after',index:1});
for(const removed of [[],['after'],['before','after']]) {
  const doc = new Y.Doc(); Y.applyUpdate(doc,Buffer.from(archived.update,'base64'));
  const order = doc.getArray('block-order'), blocks = doc.getMap('blocks');
  for(const id of removed) {
    const index = order.toArray().indexOf(id); if(index>=0) order.delete(index,1); blocks.delete(id);
  }
  const snapshot = Buffer.from(Y.encodeStateAsUpdate(doc)).toString('base64');
  const restored = transform({...input,frames:[snapshot],restore_anchor:archived.restore_anchor,
    insert:{id:'restored-link',type:'child_page',content:'하위 페이지',props:{page_id:'child'}}});
  if(!removed.includes('before')) blocks.get('before').get('content').insert(0,'동시 편집 ');
  Y.applyUpdate(doc,Buffer.from(restored.update,'base64'));
  assert.deepEqual(order.toArray(),['before','restored-link','after'].filter(id=>!removed.includes(id)));
  assert.equal(doc.getText('page-title').toString(),'보존');
  if(!removed.includes('before')) assert.equal(blocks.get('before').get('content').toString(),'동시 편집 앞 문단');
  doc.destroy();
}
console.log('UI-PAGE-ARCHIVE: restore position, removed anchors and concurrent body preservation passed');
