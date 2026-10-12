'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure');
const b=(id,props={},type='paragraph',content=id)=>({id,type,content,props});
const col=(group,column,count=2)=>({layout_group:group,layout_column:column,layout_columns:count});
const by=rows=>Object.fromEntries(rows.map(block=>[block.id,block]));
const parents=rows=>Object.fromEntries(core.blockOutline(rows).map((e,i)=>[rows[i].id,rows[e.parentIndex]?.id||null]));

test('UI-COLUMN-COMPACTION dragging a middle column out closes the gap and preserves source and selection',()=>{
 const rows=[b('a',col('g',0,3)),b('b',col('g',1,3)),b('c',col('g',2,3)),b('tail')],before=structuredClone(rows);
 const result=structure.moveRelative(rows,[1],3,'after'),out=by(result.blocks);
 assert.deepEqual(result.blocks.map(b=>b.id),['a','c','tail','b']);assert.deepEqual(result.ids,['b']);
 assert.deepEqual(out.a.props,col('g',0));assert.deepEqual(out.c.props,col('g',1));assert.equal(out.b.props.layout_group,undefined);assert.deepEqual(rows,before);
});
test('UI-COLUMN-COMPACTION last block leaving a two-column layout restores the remaining full width',()=>{
 const out=by(structure.moveRelative([b('a',col('g',0)),b('b',col('g',1)),b('tail')],[0],2,'after').blocks);
 assert.equal(out.a.props.layout_group,undefined);assert.equal(out.b.props.layout_group,undefined);
});
test('UI-COLUMN-COMPACTION moving within the same column does not collapse deliberately sparse layouts',()=>{
 const rows=[b('a',col('g',0,4)),b('b',col('g',2,4)),b('c',col('g',0,4)),b('tail')];
 const out=by(structure.moveRelative(rows,[2],0,'before').blocks);
 assert.deepEqual(out.b.props,col('g',2,4));assert.equal(out.a.props.layout_columns,4);assert.equal(out.c.props.layout_columns,4);
});
test('UI-COLUMN-COMPACTION moving into another source column updates destination descendants without flattening independent grids',()=>{
 const rows=[b('a',col('g',0)),b('b',{...col('g',1),children:['same','inner']},'callout'),b('same',{...col('g',1),indent:1}),b('inner',col('nested',1)),b('tail')];
 const result=structure.moveRelative(rows,[0],1,'after'),out=by(result.blocks);
 assert.equal(out.a.props.layout_group,undefined);assert.equal(out.b.props.layout_group,undefined);assert.equal(out.same.props.layout_group,undefined);
 assert.equal(out.inner.props.layout_group,'nested');assert.equal(parents(result.blocks).same,'b');assert.equal(parents(result.blocks).inner,'b');
});
test('UI-COLUMN-COMPACTION moving the last contents out prunes nested owners then compacts the outer layout',()=>{
 const rows=[b('outer',{children:['holder','right']},'callout'),b('holder',{...col('g',0),layout_container:true,children:['inner']},'paragraph',''),b('inner',{layout_container:true,children:['a','b']},'paragraph',''),b('a',col('h',0)),b('b',col('h',1)),b('right',col('g',1)),b('tail')];
 const result=structure.moveRelative(rows,[3,4],6,'after'),out=by(result.blocks);
 assert.equal(out.holder,undefined);assert.equal(out.inner,undefined);assert.equal(out.right.props.layout_group,undefined);assert.deepEqual(out.outer.props.children,['right']);
 assert.deepEqual(result.ids,['a','b']);assert.equal(parents(result.blocks).a,null);assert.equal(parents(result.blocks).right,'outer');
});
test('UI-COLUMN-COMPACTION outdent compacts only the departed inner grid and keeps the enclosing column',()=>{
 const rows=[b('outer',{children:['holder','right']},'callout'),b('holder',{...col('g',0),layout_container:true,children:['a','b']},'paragraph',''),b('a',col('h',0)),b('b',col('h',1)),b('right',col('g',1))];
 const result=structure.indent(rows,[2],-1),out=by(result);
 assert.equal(parents(result).a,'outer');assert.equal(out.a.props.layout_group,'g');assert.equal(out.a.props.layout_column,0);
 assert.equal(out.b.props.layout_group,'g');assert.equal(out.b.props.layout_column,0);assert.equal(out.right.props.layout_column,1);
});
test('UI-COLUMN-COMPACTION a non-vacating move preserves other members and unrelated sparse grids',()=>{
 const rows=[b('a',col('g',0)),b('same',col('g',0)),b('right',col('g',1)),b('unrelated',col('other',2,4)),b('tail')],before=structuredClone(rows);
 const out=by(structure.moveRelative(rows,[0],4,'after').blocks);
 for(const id of ['same','right','unrelated'])assert.deepEqual(out[id],before.find(b=>b.id===id));
});
test('UI-COLUMN-COMPACTION shrinking a layout keeps distinct tab panels separate',()=>{
 const rows=[b('tabs',{tabs:[{id:'x'},{id:'y'}],active_tab_id:'x',children:['a','b','hidden']} ,'tabs'),b('a',{...col('g',0),tab_group:'tabs',tab_id:'x'}),b('b',{...col('g',1),tab_group:'tabs',tab_id:'x'}),b('hidden',{...col('g',1,4),tab_group:'tabs',tab_id:'y'}),b('tail')];
 const result=structure.moveRelative(rows,[1],4,'after'),out=by(result.blocks);
 assert.equal(out.b.props.layout_group,undefined);assert.equal(out.b.props.tab_id,'x');assert.deepEqual(out.hidden,rows[3]);assert.equal(out.a.props.tab_group,undefined);
});
test('UI-COLUMN-COMPACTION root outdent reduces the whole subtree offset even without a parent',()=>{
 const rows=[b('root',{indent:2,children:['child'],open:false},'toggle'),b('child',{indent:3,...col('inner',1),visual_indent:1})],before=structuredClone(rows);
 const out=structure.indent(rows,[0],-1),outline=core.blockOutline(out),prior=core.blockOutline(rows);
 assert.equal(out[0].props.indent,1);assert.equal(out[1].props.indent,2);assert.equal(out[1].props.layout_group,'inner');assert.equal(out[0].props.open,false);
 assert.deepEqual(outline.map(e=>e.visualIndent),prior.map(e=>e.visualIndent-1));assert.equal(parents(out).child,'root');assert.deepEqual(rows,before);
});
