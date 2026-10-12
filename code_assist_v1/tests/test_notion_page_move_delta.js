'use strict';
const assert=require('node:assert/strict');
const {transform}=require('../notion_collaboration/page_move_delta.cjs');
const Y=globalThis.NotionYjs;
const Editor=require('../notion_static/notion_collaboration_editor.js');
const seed={page_id:'page-delta-test',title:'제목 보존',blocks:[
  {id:'paragraph-a',type:'heading1',content:'사용자 본문',props:{'2':'two','10':'ten',nested:{z:1,a:2}}},
  {id:'child-a',type:'child_page',content:'Child',props:{page_id:'page-child'}},
  {id:'table-a',type:'simple_table',content:'',props:{rows:[['A','B'],['C','D']]}},
]};
const bridge=Editor.createEditorBridge({Y,host:{readDocument:()=>seed,applyRemoteProjection:()=>({applied:true}),subscribe:()=>()=>{}}});
bridge.configureDocument(seed); bridge.finishReplay();
const baseline=Buffer.from(bridge.encodeState());
const input={document_id:seed.page_id,generation:1,seed,frames:[],child_id:'absent',insert:null};
assert.deepEqual(Buffer.from(transform(input).update,'base64'),baseline,'server bootstrap must match browser CRDT identities and serialization');
const base64=baseline.toString('base64');
const moved=transform({...input,frames:[base64],child_id:'page-child'});
const live=new Y.Doc();Y.applyUpdate(live,baseline);
// A concurrent text insertion after preparation must survive applying the link delta.
live.getMap('blocks').get('paragraph-a').get('content').insert(0,'동시 입력 ');
Y.applyUpdate(live,Buffer.from(moved.update,'base64'));
assert.deepEqual(moved.removed,['child-a']);
assert.deepEqual(live.getArray('block-order').toArray(),['paragraph-a','table-a']);
assert.equal(live.getMap('blocks').get('paragraph-a').get('content').toString(),'동시 입력 사용자 본문');
assert.equal(live.getText('page-title').toString(),seed.title);
assert.deepEqual(JSON.parse(live.getMap('blocks').get('table-a').get('props')).rows,[['A','B'],['C','D']]);
const converted=new Y.Doc();Y.applyUpdate(converted,baseline);converted.getMap('blocks').get('child-a').set('type','paragraph');
const retained=transform({...input,frames:[Buffer.from(Y.encodeStateAsUpdate(converted)).toString('base64')],child_id:'page-child'});
assert.deepEqual(retained.removed,[],'a former child link converted to a paragraph is user content');
const inserted=transform({...input,frames:[base64],child_id:'page-child',insert:{id:'child-new',type:'child_page',content:'Child',props:{page_id:'page-child'}}});
const destination=new Y.Doc();Y.applyUpdate(destination,baseline);Y.applyUpdate(destination,Buffer.from(inserted.update,'base64'));
assert.deepEqual(destination.getArray('block-order').toArray(),['paragraph-a','table-a','child-new']);
const batchLinks=[
  {id:'batch-link-a',type:'child_page',content:'A',props:{page_id:'batch-page-a'}},
  {id:'batch-link-b',type:'child_page',content:'B',props:{page_id:'batch-page-b'}},
];
const batch=transform({...input,frames:[base64],insert_many:batchLinks});
assert.deepEqual(batch.placed_many,[
  {id:'batch-link-a',after_block_id:'table-a',page_id:'batch-page-a'},
  {id:'batch-link-b',after_block_id:'batch-link-a',page_id:'batch-page-b'},
]);
const batchDoc=new Y.Doc();Y.applyUpdate(batchDoc,baseline);
batchDoc.getMap('blocks').get('paragraph-a').get('content').insert(0,'동시 입력 ');
Y.applyUpdate(batchDoc,Buffer.from(batch.update,'base64'));
assert.deepEqual(batchDoc.getArray('block-order').toArray(),
  ['paragraph-a','child-a','table-a','batch-link-a','batch-link-b']);
assert.equal(batchDoc.getMap('blocks').get('paragraph-a').get('content').toString(),
  '동시 입력 사용자 본문');
assert.throws(()=>transform({...input,frames:[base64],insert_many:[batchLinks[0],batchLinks[0]]}),
  /Batch child-page link identity conflicts/);
const relativeSeed={...seed,blocks:[...seed.blocks,{id:'sibling-link',type:'child_page',content:'Sibling',props:{page_id:'page-sibling'}}]};
const relativeBase=transform({...input,seed:relativeSeed}).update;
const sameParent={...input,seed:relativeSeed,frames:[relativeBase],child_id:'page-child',relocate:true,
  insert:{id:'unused-new-link',type:'child_page',content:'Child',props:{page_id:'page-child'}},
  relative_page_id:'page-sibling',relative_position:'after'};
const reordered=transform(sameParent);
const orderDoc=new Y.Doc();Y.applyUpdate(orderDoc,Buffer.from(relativeBase,'base64'));
orderDoc.getMap('blocks').get('child-a').get('content').insert(0,'동시 링크 이름 ');
Y.applyUpdate(orderDoc,Buffer.from(reordered.update,'base64'));
assert.deepEqual(orderDoc.getArray('block-order').toArray(),['paragraph-a','table-a','sibling-link','child-a']);
assert.equal(reordered.placed,'child-a');assert.equal(reordered.inserted,false);assert.equal(reordered.changed,true);
assert.equal(orderDoc.getMap('blocks').get('child-a').get('content').toString(),'동시 링크 이름 Child');
assert.deepEqual(reordered.removed,[],'same-parent order retains the original link identity');
const noop=transform({...sameParent,frames:[Buffer.from(Y.encodeStateAsUpdate(orderDoc)).toString('base64')]});
assert.equal(noop.changed,false);
assert.throws(()=>transform({...sameParent,relative_page_id:'missing-page'}),/Relative page link/);
orderDoc.destroy();
bridge.dispose();live.destroy();converted.destroy();destination.destroy();batchDoc.destroy();
console.log('UI-MOVE-001: exact browser bootstrap, link-only updates and concurrent text preservation');
