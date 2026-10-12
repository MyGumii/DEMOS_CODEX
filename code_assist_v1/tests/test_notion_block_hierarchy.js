'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const core=require('../notion_static/notion_editor_core'),clipboard=require('../notion_static/notion_block_clipboard');
const block=(id,type='paragraph',props={})=>({id,type,content:id,props});
const source=()=>[block('toggle','toggle',{open:false,children:[block('hidden')]}),block('independent','paragraph',{indent:2}),block('tail')];
test('UI-BLOCK-HIERARCHY independent item indentation is not captured by a preceding closed toggle',()=>{
  const flat=clipboard.flat(source());
  assert.deepEqual(core.expandBlockSelection(flat,[0]),[0,1]);
  assert.deepEqual(core.removeBlocks(flat,[0]).map(b=>b.id),['independent','tail']);
  assert.deepEqual(core.moveBlockSelection(flat,[0],1).map(b=>b.id),['independent','toggle','hidden','tail']);
  assert.equal(flat[2].props.indent,0);assert.equal(flat[2].props.visual_indent,2);
});
test('UI-BLOCK-HIERARCHY item-to-page-to-item retains local offsets and actual children',()=>{
  const body=[block('callout','callout',{indent:2,background_color:'yellow',children:[block('child','paragraph',{indent:1}),block('inner','callout',{children:[block('deep')]})]}),...source()];
  const flat=clipboard.flat(body),back=clipboard.tree(flat);
  const normalized=value=>JSON.parse(JSON.stringify(value,(key,v)=>key==='indent'&&v===0?undefined:v));
  assert.deepEqual(normalized(back),normalized(body));
  assert.deepEqual(core.blockOutline(flat).map(e=>e.visualIndent),[2,4,3,4,0,1,2,0]);
  assert.equal(core.blockOutline(flat)[6].collapsed,false);
});
test('UI-BLOCK-HIERARCHY callout ownership follows real ancestors and ends at sibling roots',()=>{
  const flat=[block('c','callout'),block('p','paragraph',{indent:1}),block('t','toggle',{indent:1,open:false}),block('d','paragraph',{indent:2}),block('s'),block('i','paragraph',{visual_indent:3})];
  const plan=core.blockOutline(flat);
  assert.deepEqual(plan.map(e=>e.calloutIndex),[-1,0,0,0,-1,-1]);
  assert.deepEqual(plan.map(e=>e.parentIndex),[-1,0,0,2,-1,-1]);
  assert.deepEqual(plan.map(e=>e.collapsed),[false,false,false,true,false,false]);
});
test('UI-BLOCK-HIERARCHY callout and collapsed state cannot leak between columns or tab bodies',()=>{
  const flat=[block('a','callout',{layout_group:'g',layout_column:0}),block('t','toggle',{indent:1,open:false,layout_group:'g',layout_column:0}),block('other','paragraph',{indent:2,layout_group:'g',layout_column:1}),block('tab','paragraph',{indent:3,tab_group:'tabs',tab_id:'x'})];
  const plan=core.blockOutline(flat);assert.equal(plan[1].calloutIndex,0);
  for(const index of [2,3]){assert.equal(plan[index].calloutIndex,-1);assert.equal(plan[index].parentIndex,-1);assert.equal(plan[index].collapsed,false);}
});
test('UI-BLOCK-HIERARCHY legacy flat indentation keeps its existing subtree meaning',()=>{
  const flat=[block('p','toggle',{indent:2,open:false}),block('c','paragraph',{indent:4}),block('s','paragraph',{indent:2})];
  assert.deepEqual(core.expandBlockSelection(flat,[0]),[0,1]);
  const tree=clipboard.tree(flat);assert.equal(tree[0].props.children[0].props.indent,1);
  const again=clipboard.flat(tree);assert.deepEqual(again.map(b=>b.props.indent),[0,1,0]);
  assert.deepEqual(core.blockOutline(again).map(e=>e.visualIndent),[0,2,0]);
});
test('UI-BLOCK-HIERARCHY split, duplicate and outdent retain independent spacing without changing source',()=>{
  const flat=clipboard.flat(source()),before=structuredClone(flat),independent=flat[2];
  assert.equal(core.splitBlock(independent,3)[1].props.visual_indent,2);
  assert.equal(core.duplicateBlocks(flat,[2])[3].props.visual_indent,2);
  assert.equal(core.setIndent(independent,-1).props.visual_indent,1);
  assert.deepEqual(flat,before);
});
test('UI-BLOCK-HIERARCHY visual offsets are bounded and untrusted values do not become CSS numbers',()=>{
  const rows=[block('a','paragraph',{visual_indent:Infinity}),block('b','paragraph',{visual_indent:'2;display:none'}),block('c','paragraph',{visual_indent:-3})];
  assert.deepEqual(core.blockOutline(rows).map(e=>e.visualIndent),[0,0,0]);
  assert.equal(core.blockOutline([block('max','paragraph',{visual_indent:8})])[0].visualIndent,8);
});

test('UI-BLOCK-HIERARCHY search preview keeps independent indents visible and isolates collapsed columns',()=>{
  const preview=require('../notion_static/notion_document_preview');
  const flat=clipboard.flat(source()),before=structuredClone(flat);
  const html=preview.render({blocks:flat});
  assert.match(html,/--preview-indent:2\"><p>independent/);assert.doesNotMatch(html,/>hidden</);
  const columns=[block('toggle','toggle',{open:false,layout_group:'g',layout_column:0}),block('other','paragraph',{indent:1,layout_group:'g',layout_column:1})];
  assert.match(preview.render({blocks:columns}),/>other</);assert.deepEqual(flat,before);
});

test('UI-BLOCK-HIERARCHY Enter in a nested paragraph preserves its container and following siblings',()=>{
  const child=block('child','paragraph',{indent:1}),before=structuredClone(child);
  const split=core.splitBlock(child,child.content.length);
  const flat=[block('callout','callout'),...split,block('bullet','bullet',{indent:1}),block('tail')];
  assert.equal(split[1].props.indent,1);
  assert.deepEqual(core.blockOutline(flat).map(e=>e.calloutIndex),[-1,0,0,0,-1]);
  assert.deepEqual(child,before);
});
