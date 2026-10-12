'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const core=require('../notion_static/notion_editor_core'),layout=require('../notion_static/notion_block_layout');
const structure=require('../notion_static/notion_block_structure');
const block=(id,type='paragraph',props={})=>({id,type,content:id,props});
class Element {
  constructor(doc){this.ownerDocument=doc;this.children=[];this.dataset={};this.hidden=false;this.style={setProperty:(key,value)=>{this.style[key]=value;}};}
  appendChild(child){this.children.push(child);child.parentNode=this;return child;}
}
const doc={createElement:()=>new Element(doc)};
function mount(blocks,fullPageId=''){
  const root=new Element(doc),outline=core.blockOutline(blocks),rows=blocks.map((b,i)=>{const row=new Element(doc);row.dataset.blockId=b.id;row.hidden=fullPageId?b.id!==fullPageId:outline[i].collapsed||outline[i].hiddenByTab;return row;});
  const result=layout.mount({root,blocks,rows,outline,fullPageId,applyVisual:(element,block)=>{element.presentation=structuredClone(block.props);}});
  return {root,rows,outline,...result};
}
const all=root=>[root,...root.children.flatMap(all)];

test('UI-NESTED-LAYOUT closed outer toggle and inactive outer tab hide every inner panel',()=>{
  const rows=[block('toggle','toggle',{open:false}),block('tabs','tabs',{indent:1,active_tab_id:'a'}),block('nested','tabs',{tab_group:'tabs',tab_id:'a',active_tab_id:'c'}),block('deep','paragraph',{tab_group:'nested',tab_id:'c'}),block('tail')];
  let outline=core.blockOutline(rows);assert.deepEqual(outline.map(e=>e.collapsed),[false,true,true,true,false]);
  assert.deepEqual(core.expandBlockSelection(rows,[0]),[0,1,2,3]);
  rows[0].props.open=true;rows[1].props.active_tab_id='b';outline=core.blockOutline(rows);
  assert.deepEqual(outline.map(e=>e.hiddenByTab),[false,false,true,true,false]);
  assert.equal(outline[3].visualIndent,1);
});

test('UI-NESTED-LAYOUT selection cannot capture an indented block in a different column',()=>{
  const rows=[block('left','toggle',{layout_group:'g',layout_column:0}),block('own','paragraph',{indent:1,layout_group:'g',layout_column:0}),block('right','paragraph',{indent:2,layout_group:'g',layout_column:1})];
  assert.deepEqual(core.expandBlockSelection(rows,[0]),[0,1]);
  assert.deepEqual(core.removeBlocks(rows,[0]).map(b=>b.id),['right']);
});

test('UI-NESTED-LAYOUT explicit child order works even when the owner appears later',()=>{
  const rows=[block('a'),block('b'),block('parent','callout',{children:['b','a']})],before=structuredClone(rows);
  const view=mount(rows);assert.deepEqual(view.outline[2].childIndices,[1,0]);
  const children=view.root.children[0].children[1];assert.deepEqual(children.children.map(row=>row.dataset.blockId),['b','a']);
  assert.equal(view.outline[0].calloutIndex,2);assert.deepEqual(rows,before);
});

test('UI-NESTED-LAYOUT columns are inside their callout and nested columns stay inside outer columns',()=>{
  const rows=[block('outer','callout',{background_color:'yellow',children:['left','right']}),
    block('left','callout',{layout_group:'outside',layout_column:0,children:['inner-a','inner-b']}),
    block('inner-a','paragraph',{layout_group:'inside',layout_column:0}),block('inner-b','paragraph',{layout_group:'inside',layout_column:1}),
    block('right','paragraph',{layout_group:'outside',layout_column:1}),block('tail')];
  const view=mount(rows),grids=all(view.root).filter(e=>e.className==='block-column-layout');
  assert.equal(grids.length,2);assert.equal(grids[0].parentNode.className,'block-callout-children');
  assert.equal(grids[1].parentNode.parentNode.dataset.branchId,'left');
  assert.equal(grids[1].parentNode.parentNode.parentNode,grids[0].children[0]);
  assert.deepEqual(view.columns.map(c=>[c.groupId,c.memberIds]),[['outside',['left','right']],['outside',['left','right']],['inside',['inner-a','inner-b']],['inside',['inner-a','inner-b']]]);
  assert.equal(view.root.children.at(-1).dataset.blockId,'tail');
});

test('UI-NESTED-LAYOUT converted inner grid has no extra text row or indentation and keeps outside sibling',()=>{
 const source=[block('left','paragraph',{layout_group:'outer',layout_column:0}),block('right','paragraph',{layout_group:'outer',layout_column:1})];
 const blocks=structure.columns(source,0,2,{replace:true}).blocks,view=mount(blocks);
 const wrapper=blocks.findIndex(core.isLayoutContainer),left=blocks.findIndex(b=>b.id==='left');
 assert.equal(all(view.root).includes(view.rows[wrapper]),false);assert.equal(view.outline[left].visualIndent,0);
 const grids=all(view.root).filter(e=>e.className==='block-column-layout');assert.equal(grids.length,2);
 assert.equal(grids[1].parentNode.parentNode.parentNode,grids[0].children[0]);
 assert.equal(view.rows[blocks.findIndex(b=>b.id==='right')].parentNode,grids[0].children[1]);
});

test('UI-NESTED-LAYOUT descendants sharing an outer column do not create another grid',()=>{
  const view=mount([block('c','callout',{layout_group:'g',layout_column:0}),block('p','paragraph',{indent:1,layout_group:'g',layout_column:0}),block('right','paragraph',{layout_group:'g',layout_column:1})]);
  assert.equal(all(view.root).filter(e=>e.className==='block-column-layout').length,1);
  assert.equal(view.rows[1].parentNode.className,'block-callout-children');
  assert.equal(view.rows[1].style['--block-indent'],'0');
});

test('UI-NESTED-LAYOUT column add targets are not duplicated for inner columns or mixed across tabs',()=>{
  const view=mount([block('tabs','tabs',{active_tab_id:'a'}),
    block('a','paragraph',{tab_group:'tabs',tab_id:'a',layout_group:'g',layout_column:0}),
    block('b','paragraph',{tab_group:'tabs',tab_id:'b',layout_group:'g',layout_column:0})]);
  const grids=all(view.root).filter(e=>e.className==='block-column-layout');
  assert.equal(view.columns.length,4);assert.deepEqual(grids.map(g=>g.hidden),[false,true]);
  assert.deepEqual(view.columns.map(c=>c.memberIds),[['a'],['a'],['b'],['b']]);
});

test('UI-NESTED-LAYOUT full-page database bypasses hidden ancestors and all layout containers',()=>{
  const view=mount([block('toggle','toggle',{open:false}),block('table','table',{indent:1,layout_group:'g',layout_column:1})],'table');
  assert.deepEqual(view.root.children.map(e=>e.dataset.blockId),['table']);assert.equal(view.rows[1].hidden,false);
  assert.equal(view.columns.length,0);assert.equal(view.rows[1].style['--block-indent'],'0');
});

test('UI-NESTED-LAYOUT legacy spacing and presentation apply once per ownership level',()=>{
  const view=mount([block('c','callout',{visual_indent:2,text_color:'red'}),block('p','paragraph',{indent:1,visual_indent:1,background_color:'blue'}),block('deep','paragraph',{indent:3}),block('end')]);
  assert.equal(view.root.children[0].style['--callout-indent'],'2');
  const middle=view.rows[1].parentNode;assert.equal(middle.style['--branch-indent'],'1');
  assert.equal(middle.presentation.background_color,'blue');
  assert.equal(view.rows[2].style['--block-indent'],'1');assert.equal(view.rows[3].style['--block-indent'],'0');
});

test('UI-NESTED-LAYOUT copied and deleted explicit children keep valid independent ownership',()=>{
  const rows=[block('parent','callout',{children:['a','b']}),block('a'),block('b')];
  const copied=core.duplicateBlocks(rows,[1]),ids=copied[0].props.children;
  assert.deepEqual(ids,[copied[1].id,copied[2].id,'b']);assert.equal(core.blockOutline(copied)[2].parentIndex,0);
  const full=core.duplicateBlocks(rows,[0]);assert.deepEqual(full[3].props.children,[full[4].id,full[5].id]);
  const removed=core.removeBlocks(rows,[1]);assert.deepEqual(removed[0].props.children,['b']);assert.equal(core.blockOutline(removed)[1].parentIndex,0);
});

test('UI-NESTED-LAYOUT cycles, ambiguous owners and unresolved children fail before mounting',()=>{
  for(const rows of [[block('p','paragraph',{children:['p']})],[block('a','paragraph',{children:['c']}),block('b','paragraph',{children:['c']}),block('c')],[block('p','paragraph',{children:['missing']})]]) assert.throws(()=>core.blockOutline(rows));
});

test('UI-NESTED-LAYOUT Enter and merging preserve explicit sibling edges and remaining children',()=>{
  const rows=[block('parent','callout',{children:['a','b']}),block('a'),block('b')];
  const [first,second]=core.splitBlock(rows[1],1);
  core.replaceBlockRange(rows,1,1,first,second);
  assert.deepEqual(rows[0].props.children,['a',second.id,'b']);
  assert.deepEqual(core.blockOutline(rows).map(e=>e.parentIndex),[-1,0,0,0]);
  const merged=core.mergeBlocks(first,second).block;
  core.replaceBlockRange(rows,1,2,merged);
  assert.deepEqual(rows[0].props.children,['a','b']);assert.equal(rows[1].content,'a');
});

test('UI-NESTED-LAYOUT insert controls keep the requested side of an explicit child',()=>{
  const rows=[block('parent','callout',{children:['a','b']}),block('a'),block('b')];
  core.insertSiblingOwnership(rows,'b',['before'],true);core.insertSiblingOwnership(rows,'a',['after']);
  assert.deepEqual(rows[0].props.children,['a','after','before','b']);
});
