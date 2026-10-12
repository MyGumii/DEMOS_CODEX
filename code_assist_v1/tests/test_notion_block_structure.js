'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure'),clipboard=require('../notion_static/notion_block_clipboard');
const b=(id,type='paragraph',props={})=>({id,type,content:id,props});
const ids=blocks=>blocks.map(b=>b.id);
const parents=blocks=>Object.fromEntries(core.blockOutline(blocks).map((e,i)=>[blocks[i].id,blocks[e.parentIndex]?.id||null]));
const source=()=>[b('p','callout',{children:['a','c']}),b('a','toggle',{children:['hidden'],open:false}),b('outside'),b('hidden'),b('c'),b('target')];

test('UI-STRUCTURE vertical move uses explicit sibling order and carries a noncontiguous subtree',()=>{
 const rows=source(),before=structuredClone(rows),out=structure.vertical(rows,[1],1);
 assert.deepEqual(ids(out),['p','c','a','hidden','outside','target']);assert.deepEqual(out[0].props.children,['c','a']);
 assert.equal(parents(out).hidden,'a');assert.deepEqual(rows,before);
 assert.deepEqual(ids(structure.vertical(out,[2],-1)),['p','a','hidden','c','outside','target']);
});
test('UI-STRUCTURE vertical movement remains within each column and keeps multi-selection order',()=>{
 const rows=[b('a','paragraph',{layout_group:'g',layout_column:0}),b('b','paragraph',{layout_group:'g',layout_column:1}),b('c','paragraph',{layout_group:'g',layout_column:0}),b('d','paragraph',{layout_group:'g',layout_column:0})];
 assert.deepEqual(ids(structure.vertical(rows,[0,2],1)),['d','b','a','c']);
});
test('UI-STRUCTURE drag adopts destination ownership without dropping hidden descendants',()=>{
 const rows=source(),out=structure.moveRelative(rows,[1],5,'after');
 assert.deepEqual(ids(out.blocks),['p','c','outside','target','a','hidden']);
 assert.deepEqual(out.ids,['a','hidden']);assert.deepEqual(out.blocks[0].props.children,['c']);
 assert.equal(parents(out.blocks).a,null);assert.equal(parents(out.blocks).hidden,'a');
 assert.equal(out.blocks.find(b=>b.id==='a').props.open,false);
});
test('UI-STRUCTURE dropping a parent onto its descendant is a no-op',()=>{
 const rows=source(),out=structure.moveRelative(rows,[0],3,'after');assert.deepEqual(out.blocks,rows);assert.deepEqual(out.ids,[]);
});
test('UI-STRUCTURE drag transfers only inherited outer column and tab context',()=>{
 const rows=[b('from','tabs',{active_tab_id:'a'}),b('move','callout',{tab_group:'from',tab_id:'a',layout_group:'old',layout_column:0,children:['nested','same']}),
  b('nested','paragraph',{layout_group:'inner',layout_column:1}),b('same','paragraph',{tab_group:'from',tab_id:'a',layout_group:'old',layout_column:0}),
  b('to','tabs',{active_tab_id:'b'}),b('target','paragraph',{tab_group:'to',tab_id:'b',layout_group:'destination',layout_column:1,layout_columns:3})];
 const out=structure.moveRelative(rows,[1],5,'before').blocks,by=Object.fromEntries(out.map(b=>[b.id,b]));
 assert.equal(parents(out).move,'to');assert.equal(by.move.props.tab_group,'to');assert.equal(by.same.props.layout_group,'destination');
 assert.equal(by.same.props.layout_column,1);assert.equal(by.nested.props.layout_group,'inner');
});
test('UI-STRUCTURE indent and outdent reparent complete sibling selections in stable order',()=>{
 const rows=[b('first','callout'),b('a','toggle',{children:['hidden'],open:false}),b('hidden'),b('b'),b('tail')];
 const nested=structure.indent(rows,[1,3],1),p=parents(nested);
 assert.equal(p.a,'first');assert.equal(p.b,'first');assert.equal(p.hidden,'a');assert.deepEqual(nested[0].props.children,['a','b']);
 const out=structure.indent(nested,[1,3],-1);assert.deepEqual(ids(out),ids(rows));assert.equal(parents(out).a,null);assert.equal(parents(out).hidden,'a');
});
test('UI-STRUCTURE indentation into a tab selects its active panel and outdent clears membership',()=>{
 const rows=[b('tabs','tabs',{tabs:[{id:'a',name:'A'},{id:'b',name:'B'}],active_tab_id:'b'}),b('item')];
 const into=structure.indent(rows,[1],1);assert.equal(into[1].props.tab_group,'tabs');assert.equal(into[1].props.tab_id,'b');
 assert.equal(parents(into).item,'tabs');const out=structure.indent(into,[1],-1);
 assert.equal(out[1].props.tab_group,undefined);assert.equal(parents(out).item,null);
});
test('UI-STRUCTURE paste inserts roots as siblings and preserves incoming child ownership',()=>{
 const rows=[b('parent','callout',{children:['anchor','tail']}),b('anchor'),b('tail')];
 const incoming=[b('copy','toggle',{children:['copy-child'],open:false}),b('copy-child')];
 const out=structure.insert(rows,1,incoming),p=parents(out);
 assert.deepEqual(out[0].props.children,['anchor','copy','tail']);assert.equal(p.copy,'parent');assert.equal(p['copy-child'],'copy');
 assert.deepEqual(ids(out),['parent','anchor','copy','copy-child','tail']);
});
test('UI-STRUCTURE replacing an empty leaf keeps its old parent but cannot discard owned children',()=>{
 const rows=[b('parent','callout',{children:['anchor']}),b('anchor')];
 const out=structure.insert(rows,1,[b('new')],{replace:true});assert.deepEqual(ids(out),['parent','new']);assert.equal(parents(out).new,'parent');
 assert.throws(()=>structure.insert(rows,0,[b('new')],{replace:true}));assert.deepEqual(rows[0].props.children,['anchor']);
});
test('UI-STRUCTURE clipboard roundtrip uses actual explicit ownership across columns and tabs',()=>{
 const rows=[b('parent','callout',{children:['right','left']}),b('left','paragraph',{layout_group:'g',layout_column:0}),b('right','paragraph',{layout_group:'g',layout_column:1})];
 const before=structuredClone(rows);let n=0;
 const decoded=clipboard.decode(clipboard.encode(rows),{createId:()=>`copy-${++n}`,shape:'flat'});
 assert.deepEqual(decoded[0].props.children,[decoded[1].id,decoded[2].id]);
 assert.equal(decoded[1].props.layout_column,1);assert.equal(decoded[2].props.layout_column,0);assert.deepEqual(rows,before);
 assert.deepEqual(clipboard.tree(decoded)[0].props.children.map(b=>b.content),['right','left']);
});
test('UI-STRUCTURE copied root column groups retain their columns when pasted into plain content',()=>{
 const columns=[b('left','paragraph',{layout_group:'g',layout_column:0}),b('right','paragraph',{layout_group:'g',layout_column:1})];
 const out=structure.insert([b('anchor')],0,columns);assert.deepEqual(out.slice(1).map(b=>[b.props.layout_group,b.props.layout_column]),[['g',0],['g',1]]);
 const target=[b('anchor','paragraph',{layout_group:'outer',layout_column:0})];
 const nested=structure.insert(target,0,columns),wrapper=nested[1];
 assert.equal(core.isLayoutContainer(wrapper),true);assert.equal(wrapper.props.layout_group,'outer');
 assert.equal(parents(nested).left,wrapper.id);assert.equal(parents(nested).right,wrapper.id);
 assert.equal(nested[2].props.layout_group,'g');assert.equal(nested[3].props.layout_column,1);
 assert.deepEqual(target[0].props,{layout_group:'outer',layout_column:0});
});
test('UI-STRUCTURE unsuccessful edits leave both source and incoming descriptors untouched',()=>{
 const rows=[b('a')],incoming=[b('a')],before=structuredClone(rows);assert.throws(()=>structure.insert(rows,0,incoming));assert.deepEqual(rows,before);assert.deepEqual(incoming,before);
});
