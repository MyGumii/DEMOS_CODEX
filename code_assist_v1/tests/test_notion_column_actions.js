'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure');
const {CollaborationEditor,loadPinnedYjs}=require('./notion_collaboration_test_runtime');
const b=(id,type='paragraph',props={},content=id)=>({id,type,content,props});
const col=(group,column)=>({layout_group:group,layout_column:column,layout_columns:2});
const parents=rows=>Object.fromEntries(core.blockOutline(rows).map((e,i)=>[rows[i].id,rows[e.parentIndex]?.id||null]));
const fixture=()=>[b('outer','callout',{children:['holder','outside']}),b('holder','paragraph',{...col('outer-cols',0),layout_container:true,children:['left','right']},''),b('left','toggle',{...col('inner-cols',0),children:['deep-a','deep-b'],open:false,rich_text_html:'<b>left</b>'}),b('deep-a','paragraph',col('deep-cols',0)),b('deep-b','paragraph',col('deep-cols',1)),b('right','paragraph',col('inner-cols',1)),b('outside','paragraph',col('outer-cols',1)),b('tail')];

test('UI-COLUMN-ACTIONS extract one inner column into the outer column, preserving nested content and input',()=>{
 const rows=fixture(),before=structuredClone(rows),out=structure.removeColumnLayout(rows,[2]),by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.equal(parents(out).left,'outer');assert.deepEqual(by.outer.props.children,['holder','left','outside']);assert.deepEqual(by.holder.props.children,['right']);
 assert.equal(by.left.props.layout_group,'outer-cols');assert.equal(by.left.props.layout_column,0);assert.equal(by.left.props.open,false);assert.equal(by.left.props.rich_text_html,'<b>left</b>');
 assert.equal(by.right.props.layout_group,'outer-cols');assert.equal(by.right.props.layout_column,0);
 assert.equal(parents(out)['deep-a'],'left');assert.equal(by['deep-a'].props.layout_group,'deep-cols');assert.equal(by['deep-b'].props.layout_column,1);assert.deepEqual(rows,before);
});
test('UI-COLUMN-ACTIONS flat extraction carries inherited children and leaves independent inner grids intact',()=>{
 const rows=[b('left','callout',{...col('g',0),children:['same','inner']}),b('same','paragraph',{...col('g',0),indent:1}),b('inner','paragraph',col('inside',1)),b('right','paragraph',col('g',1)),b('tail')];
 const out=structure.removeColumnLayout(rows,[0,1,2]),by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.deepEqual(out.map(b=>b.id),['right','left','same','inner','tail']);assert.equal(by.left.props.layout_group,undefined);assert.equal(by.same.props.layout_group,undefined);assert.equal(by.inner.props.layout_group,'inside');assert.equal(parents(out).same,'left');
 assert.equal(by.right.props.layout_group,undefined);
});
test('UI-COLUMN-ACTIONS removing a middle column compacts remaining column slots without touching other groups',()=>{
 const rows=[b('a','paragraph',{...col('g',0),layout_columns:3}),b('b','paragraph',{...col('g',1),layout_columns:3}),b('c','paragraph',{...col('g',2),layout_columns:3}),b('plain')];
 const out=structure.removeColumnLayout(rows,[1]),by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.deepEqual(out.map(b=>b.id),['a','c','b','plain']);assert.equal(by.a.props.layout_columns,2);assert.equal(by.c.props.layout_columns,2);assert.equal(by.c.props.layout_column,1);assert.equal(by.b.props.layout_group,undefined);
});
test('UI-COLUMN-ACTIONS extracting inherited child leaves the actual grid and preserves former owner',()=>{
 const rows=[b('left','callout',{...col('g',0),children:['child']}),b('child','paragraph',{...col('g',0),indent:1}),b('right','paragraph',col('g',1)),b('tail')];
 const out=structure.removeColumnLayout(rows,[1]),by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.deepEqual(out.map(b=>b.id),['left','right','child','tail']);assert.equal(parents(out).child,null);assert.equal(by.child.props.layout_group,undefined);assert.deepEqual(by.left.props.children,[]);assert.equal(by.left.content,'left');
});
test('UI-COLUMN-ACTIONS extracting all wrapper contents prunes only the empty internal owner and keeps selection order',()=>{
 const out=structure.removeColumnLayout(fixture(),[2,5]),by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.equal(by.holder,undefined);assert.deepEqual(by.outer.props.children,['left','right','outside']);assert.equal(parents(out).right,'outer');assert.equal(by.right.props.layout_group,'outer-cols');assert.equal(by['deep-a'].props.layout_group,'deep-cols');
 const again=structure.removeColumnLayout(out,[out.findIndex(b=>b.id==='left'),out.findIndex(b=>b.id==='right')]);
 assert.equal(again.find(b=>b.id==='left').props.layout_group,undefined);assert.equal(parents(again).left,'outer');assert.equal(again.find(b=>b.id==='deep-b').props.layout_group,'deep-cols');
});
test('UI-COLUMN-ACTIONS dragging all children away cannot expose a ghost owner paragraph',()=>{
 const rows=fixture(),out=structure.moveRelative(rows,[2,5],7,'after').blocks,by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.equal(by.holder,undefined);assert.deepEqual(by.outer.props.children,['outside']);assert.equal(parents(out).left,null);assert.equal(parents(out)['deep-a'],'left');assert.equal(by['deep-a'].props.layout_group,'deep-cols');
});
test('UI-COLUMN-ACTIONS indent into a layout owner does not add a hidden indentation step',()=>{
 const rows=[b('holder','paragraph',{layout_container:true,children:['child']},''),b('child'),b('peer')];
 const out=structure.indent(rows,[2],1),outline=core.blockOutline(out),peer=out.findIndex(b=>b.id==='peer');
 assert.equal(parents(out).peer,'holder');assert.equal(outline[peer].visualIndent,outline[0].visualIndent);
});
function handlerContext(blocks,override={}){
 const State={pageData:{blocks},selectedBlockIds:new Set()},notes=[];
 const ctx={State,EditorCore:core,window:{NotionBlockStructure:structure},activePageCanEdit:()=>true,selectedBlockIndices:i=>[i],markDirty(){},captureEditorHistory(){},clearBlockSelection(){},closeBlockMenu(){},renderBlocks(){},setStatus:message=>notes.push(message),...override};
 const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');vm.createContext(ctx);vm.runInContext(main.slice(main.indexOf('function handleBlockAction('),main.indexOf('function renderBlockFlyout(')),ctx);return {ctx,State,notes};
}
test('UI-COLUMN-ACTIONS actual menu saves extraction through Yjs; undo and redo preserve owned columns',t=>{
 const {ctx,State,notes}=handlerContext(fixture()),Y=loadPinnedYjs();
 const host=core.createEditorHostAdapter({readDocument:()=>({page_id:'page',title:'Title',blocks:State.pageData.blocks,can_edit:true}),applyProjection:value=>{State.pageData.blocks=structuredClone(value.blocks);return {applied:true};}});
 const bridge=CollaborationEditor.createEditorBridge({Y,host});bridge.configureDocument(host.readDocument());bridge.finishReplay();t.after(()=>{bridge.dispose();host.dispose();});
 ctx.markDirty=()=>host.notifyLocalMutation('document');const before=structuredClone(State.pageData.blocks);bridge.stopCapturing();ctx.handleBlockAction('remove_column_layout',2);
 assert.deepEqual(notes,[]);assert.equal(parents(State.pageData.blocks).left,'outer');assert.deepEqual(bridge.projection().blocks,State.pageData.blocks);
 bridge.stopCapturing();bridge.undo();assert.deepEqual(State.pageData.blocks,before);bridge.redo();assert.equal(parents(State.pageData.blocks).left,'outer');
});
test('UI-COLUMN-ACTIONS denied or failed extraction leaves document and history untouched',()=>{
 for(const denied of [true,false]){
  let saved=0;const rows=fixture(),before=structuredClone(rows),{ctx,State,notes}=handlerContext(rows,{activePageCanEdit:()=>!denied,markDirty:()=>saved++,window:{NotionBlockStructure:{removeColumnLayout(){throw Error('invalid hierarchy');}}}});
  ctx.handleBlockAction('remove_column_layout',2);assert.deepEqual(State.pageData.blocks,before);assert.equal(saved,0);assert.equal(notes.length,denied?0:1);
 }
});
