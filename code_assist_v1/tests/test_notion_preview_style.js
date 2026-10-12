'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const preview=require('../notion_static/notion_document_preview'),style=require('../notion_static/notion_block_style');
const block=(id,type='paragraph',props={})=>({id,type,content:id,props});
// Read the generated passive HTML's ancestry without a browser or a production parser dependency.
function parse(html){
  const root={tag:'root',attrs:{},children:[]},stack=[root],voids=new Set(['br','img','hr','input','meta','link']);
  for(const match of html.matchAll(/<\/?([a-z][\w-]*)([^>]*)>|([^<]+)/gi)){
    if(match[3]){stack.at(-1).children.push({text:match[3]});continue;}
    const tag=match[1].toLowerCase();
    if(match[0].startsWith('</')){assert.equal(stack.at(-1).tag,tag);stack.pop();continue;}
    const node={tag,attrs:Object.fromEntries([...match[2].matchAll(/([\w-]+)="([^"]*)"/g)].map(m=>[m[1],m[2]])),children:[]};
    stack.at(-1).children.push(node);if(!voids.has(tag))stack.push(node);
  }
  assert.equal(stack.length,1);return root;
}
const find=(node,predicate)=>[...(predicate(node)?[node]:[]),...(node.children||[]).flatMap(child=>find(child,predicate))];
const hasClass=(node,name)=>(node.attrs?.class||'').split(' ').includes(name);
const text=node=>node.text||(node.children||[]).map(text).join('');

test('UI-PREVIEW-STYLE a callout owns its visible descendants and ends before independent roots',()=>{
  const blocks=[block('notice','callout',{text_color:'purple',background_color:'yellow'}),block('child','paragraph',{indent:1}),block('bullet','bullet',{indent:1,text_color:'red'}),block('tail')];
  const before=structuredClone(blocks),html=preview.render({blocks}),tree=parse(html);
  const groups=find(tree,n=>hasClass(n,'preview-callout-group'));assert.equal(groups.length,1);
  assert.equal(text(groups[0]),'💡noticechildbullet');assert.ok(!text(groups[0]).includes('tail'));
  assert.doesNotMatch(groups[0].attrs.style,/--block-(fg|bg):/);
  const child=find(groups[0],n=>n.attrs?.['data-block-type']==='paragraph')[0];
  assert.equal(text(child),'child');assert.ok(!hasClass(child,'has-text-color'));
  const bullet=find(groups[0],n=>n.attrs?.['data-block-type']==='bullet')[0];assert.ok(!hasClass(bullet,'has-text-color'));
  assert.deepEqual(blocks,before);
});
test('UI-PREVIEW-STYLE stored colors remain untouched while the passive summary uses neutral presentation',()=>{
  for(const {value} of style.colors){
    const node=find(parse(preview.render({blocks:[block('body','paragraph',{text_color:value,background_color:value})]})),n=>n.attrs?.['data-block-type']==='paragraph')[0];
    assert.equal(hasClass(node,'has-text-color'),false);assert.equal(hasClass(node,'has-bg-color'),false);
    assert.doesNotMatch(node.attrs.style,/--block-(fg|bg):/);
  }
});
test('UI-PREVIEW-STYLE nested callouts retain local offsets and independent default backgrounds',()=>{
  const blocks=[block('outer','callout',{background_color:'yellow'}),block('inner','callout',{indent:1,visual_indent:1}),block('deep','paragraph',{indent:2}),block('outside','paragraph',{visual_indent:2})];
  const tree=parse(preview.render({blocks})),groups=find(tree,n=>hasClass(n,'preview-callout-group'));
  assert.equal(groups.length,2);assert.equal(text(groups[1]),'💡innerdeep');assert.ok(!hasClass(groups[1],'has-bg-color'));
  assert.match(groups[1].attrs.style,/--preview-indent:1/);
  assert.match(find(tree,n=>n.attrs?.['data-block-type']==='paragraph'&&text(n)==='outside')[0].attrs.style,/--preview-indent:2/);
});
test('UI-PREVIEW-STYLE closed children and inactive tab callouts stay absent',()=>{
  const blocks=[block('notice','callout'),block('closed','toggle',{indent:1,open:false}),block('secret','paragraph',{indent:2}),block('sibling','paragraph',{indent:1}),block('tabs','tabs',{tabs:[{id:'a',name:'A'},{id:'b',name:'B'}],active_tab_id:'a'}),block('inactive','callout',{tab_group:'tabs',tab_id:'b'}),block('inactive-child','paragraph',{tab_group:'tabs',tab_id:'b',indent:1})];
  const html=preview.render({blocks}),tree=parse(html);
  assert.doesNotMatch(html,/secret|inactive/);assert.equal(find(tree,n=>hasClass(n,'preview-callout-group')).length,1);
  assert.match(text(find(tree,n=>hasClass(n,'preview-callout-group'))[0]),/siblings?$/);
});
test('UI-PREVIEW-STYLE column callouts cannot capture blocks in another column',()=>{
  const blocks=[block('notice','callout',{layout_group:'columns',layout_column:0}),block('child','paragraph',{indent:1,layout_group:'columns',layout_column:0}),block('other','paragraph',{indent:2,layout_group:'columns',layout_column:1})];
  const tree=parse(preview.render({blocks})),group=find(tree,n=>hasClass(n,'preview-callout-group'))[0];
  assert.equal(text(group),'💡noticechild');assert.ok(text(tree).includes('other'));
  const columns=find(tree,n=>hasClass(n,'preview-columns'))[0];assert.equal(columns.children.length,2);assert.equal(text(columns.children[1]),'other');
});
test('UI-PREVIEW-STYLE untrusted names cannot add CSS and empty callouts remain passive',()=>{
  const html=preview.render({blocks:[{...block('empty','callout',{text_color:'red;display:none',background_color:'url(https://invalid.test/a)'}),content:''},block('<script>alert(1)</script>')]});
  assert.doesNotMatch(html,/display:none|invalid\.test|<script|contenteditable|<button|<input/);
  const group=find(parse(html),n=>hasClass(n,'preview-callout-group'))[0];assert.equal(text(group),'💡');
  assert.doesNotMatch(group.attrs.style,/--block-(fg|bg):/);assert.match(html,/&lt;script&gt;/);
});
