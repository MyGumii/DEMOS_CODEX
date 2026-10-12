'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const style=require('../notion_static/notion_block_style');
const b=(id,props={})=>({id,type:'paragraph',content:'유지할 글',props});
function row(){const classes=new Set(),values=new Map();return {classes,values,classList:{toggle:(key,on)=>on?classes.add(key):classes.delete(key)},style:{setProperty:(key,value)=>values.set(key,value),removeProperty:key=>values.delete(key)}};}
test('UI-BLOCK-STYLE repaint removes a previous block color and never inserts arbitrary CSS',()=>{
  const el=row();style.apply(el,b('a',{text_color:'red',background_color:'yellow'}));assert.equal(el.values.size,2);
  style.apply(el,b('b',{text_color:'red;display:none',background_color:'__proto__'}));assert.equal(el.values.size,0);assert.equal(el.classes.size,0);
  style.apply(el,b('c',{text_color:'blue'}));assert.equal(el.values.get('--block-fg'),'var(--block-text-blue)');assert.equal(el.values.has('--block-bg'),false);
});
test('UI-BLOCK-STYLE changing a nested selection preserves siblings, IDs, rich text and opaque properties',()=>{
  const source=[b('parent',{children:[b('child',{rich_text_html:'<b>유지할 글</b>',background_color:'yellow',future:{a:1}}),b('sibling')],open:false}),b('tail',{indent:2})],before=structuredClone(source);
  const result=style.paint(source,['child','tail'],'text_color','purple');assert.deepEqual(source,before);
  assert.deepEqual(result.blocks[0].props.children[1],before[0].props.children[1]);assert.equal(result.blocks[0].props.open,false);
  assert.deepEqual(result.blocks[0].props.children[0],{...before[0].props.children[0],props:{...before[0].props.children[0].props,text_color:'purple'}});
  assert.equal(result.blocks[1].props.indent,2);assert.deepEqual(result.ids,['child','tail']);
});
test('UI-BLOCK-STYLE parent styling does not rewrite unselected descendants',()=>{
  const source=[b('p',{children:[b('c',{text_color:'red'})]})];const result=style.paint(source,['p'],'background_color','blue');
  assert.deepEqual(result.blocks[0].props.children,source[0].props.children);
  assert.equal(result.blocks[0].props.background_color,'blue');
});
test('UI-BLOCK-STYLE default resets only the requested property, retaining layout and attachments',()=>{
  const source=[b('a',{text_color:'blue',background_color:'yellow',file_id:'f',indent:2})];
  const result=style.paint(source,['a'],'background_color','default');assert.deepEqual(result.blocks[0].props,{text_color:'blue',file_id:'f',indent:2});
  assert.equal(source[0].props.background_color,'yellow');
});
test('UI-BLOCK-STYLE invalid fields, unknown colors and no-op selections produce no mutation',()=>{
  const source=[b('a',{text_color:'blue'})],before=structuredClone(source);
  for(const [ids,key,color] of [[['a'],'text_color','blue'],[['missing'],'text_color','red'],[['a'],'__proto__','blue'],[['a'],'text_color','url(test)']])assert.equal(style.paint(source,ids,key,color),null);
  assert.deepEqual(source,before);
});
test('UI-BLOCK-STYLE current selection distinguishes mixed values and default without inventing colors',()=>{
  assert.equal(style.colors.length,10);assert.equal(style.value({},'text_color'),'default');assert.equal(style.value({text_color:'red'},'text_color'),'red');
  assert.equal(style.value({background_color:'constructor'},'background_color'),'default');
});
