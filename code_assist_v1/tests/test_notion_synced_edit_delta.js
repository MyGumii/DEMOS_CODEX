'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const {transform}=require('../notion_collaboration/synced_edit_delta.cjs');
const Y=globalThis.NotionYjs;
const block=(id,type='paragraph',props={})=>({id,type,props,content:id});
const update=(id,content,rich_text_html=null)=>({id,content,rich_text_html});
function seed(blocks){return {document_id:'page-one',generation:1,seed:{title:'Unchanged title',blocks},frames:[],updates:[]};}
function materialize(payload){const doc=new Y.Doc();Y.applyUpdate(doc,new Uint8Array(Buffer.from(payload,'base64')));return doc;}
test('UI-SYNC-EDIT changes only requested text and rich HTML with flat/inline children',()=>{
 const input=seed([block('origin','synced_block',{synced_from:null,children:[block('inline','heading2',{text_color:'blue'})]}),block('flat','paragraph',{indent:1,rich_text_html:'<i>flat</i>'}),block('sibling')]);
 input.updates=[update('inline','새 제목','<b>새 제목</b>'),update('flat','일반 글')];const before=JSON.stringify(input);
 const result=transform(input),doc=materialize(result.update),blocks=doc.getMap('blocks');
 assert.equal(doc.getText('page-title').toString(),'Unchanged title');assert.deepEqual(doc.getArray('block-order').toArray(),['origin','flat','sibling']);
 assert.equal(blocks.get('sibling').get('content').toString(),'sibling');
 const child=JSON.parse(blocks.get('origin').get('props')).children[0];assert.equal(child.content,'새 제목');assert.equal(child.props.text_color,'blue');assert.equal(child.props.rich_text_html,'<b>새 제목</b>');
 assert.equal(blocks.get('flat').get('content').toString(),'일반 글');assert.deepEqual(JSON.parse(blocks.get('flat').get('props')),{indent:1});assert.equal(JSON.stringify(input),before);doc.destroy();
});
test('UI-SYNC-EDIT delta applies to existing state and survives duplicate delivery',()=>{
 const input=seed([block('origin','synced_block',{synced_from:null}),block('sibling')]);input.updates=[update('origin','A😀B')];const first=transform(input);
 const next={...input,frames:[first.update],updates:[update('origin','A😀한글B')]};const delta=transform(next),doc=materialize(first.update);
 Y.applyUpdate(doc,new Uint8Array(Buffer.from(delta.update,'base64')));Y.applyUpdate(doc,new Uint8Array(Buffer.from(delta.update,'base64')));
 assert.equal(doc.getMap('blocks').get('origin').get('content').toString(),'A😀한글B');assert.equal(doc.getMap('blocks').get('sibling').get('content').toString(),'sibling');doc.destroy();
});
test('UI-SYNC-EDIT bootstrap agrees with a concurrent browser bootstrap',()=>{
 const input=seed([block('origin','synced_block',{synced_from:null}),block('sibling')]);
 const Core=require('../notion_static/notion_editor_core.js'),Editor=require('../notion_static/notion_collaboration_editor.js');
 let snapshot={...structuredClone(input.seed),page_id:input.document_id,can_edit:true,server_revision:1,edit_revision:0};
 const host=Core.createEditorHostAdapter({readDocument:()=>structuredClone(snapshot),applyProjection(value){snapshot={...snapshot,...value};return {applied:true};},captureSelection:()=>null,restoreSelection:()=>true,focusBlock:()=>true});
 const bridge=Editor.createEditorBridge({Y,host}),events=[];bridge.subscribe(event=>events.push(event));
 bridge.configureDocument(snapshot);host.notifyLifecycle('document-loaded',{page_id:input.document_id});bridge.finishReplay();
 const browserSeed=events.find(event=>event.seed);assert.ok(browserSeed);
 const doc=new Y.Doc();Y.applyUpdate(doc,browserSeed.update);
 input.updates=[update('origin','edited')];const edited=transform(input);
 Y.applyUpdate(doc,new Uint8Array(Buffer.from(edited.update,'base64')));
 assert.deepEqual(doc.getArray('block-order').toArray(),['origin','sibling']);assert.equal(doc.getText('page-title').toString(),'Unchanged title');assert.equal(doc.getMap('blocks').get('origin').get('content').toString(),'edited');doc.destroy();bridge.dispose();
});
test('UI-SYNC-EDIT rejects unavailable targets and missing later generation',()=>{
 const input=seed([block('origin')]);input.updates=[update('missing','no')];assert.throws(()=>transform(input));input.generation=2;assert.throws(()=>transform(input));
});
test('UI-SYNC-EDIT replacing emoji that share a surrogate prefix retains valid text',()=>{
 const input=seed([block('origin')]);input.updates=[update('origin','한😀글')];const first=transform(input);
 const delta=transform({...input,frames:[first.update],updates:[update('origin','한😁글')]});const doc=materialize(first.update);
 Y.applyUpdate(doc,new Uint8Array(Buffer.from(delta.update,'base64')));
 assert.equal(doc.getMap('blocks').get('origin').get('content').toString(),'한😁글');doc.destroy();
});
