'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict');
const view=require('../notion_static/notion_synced_content.js');
const node=(id,type='paragraph',props={},children=[])=>({id,type,props,content:id,children});
test('UI-SYNC-TREE heading, callout, closed toggle, lists and table retain nested content without editing',()=>{
 const tree=node('origin','synced_block',{synced_from:null},[node('heading','heading2'),node('callout','callout',{background_color:'yellow'},[node('inside')]),node('toggle','toggle',{open:false},[node('hidden')]),node('one','numbered'),node('two','numbered'),node('table','simple_table',{rows:[['H','<img onerror=x>'],['A','B']],header_row:true})]);
 const before=JSON.stringify(tree),html=view.render(tree);
 assert.match(html,/<h2>heading<\/h2>/);assert.match(html,/<aside[^>]*>.*inside.*<\/aside>/);assert.match(html,/<details><summary>toggle<\/summary>.*hidden/);
 assert.match(html,/<ol start="2">/);assert.match(html,/<th>&lt;img onerror=x&gt;<\/th>/);assert.match(html,/--synced-bg:var\(--block-bg-yellow\)/);
 assert.doesNotMatch(html,/contenteditable|data-block-id|onerror="|<img onerror/);assert.equal(JSON.stringify(tree),before);
});
test('UI-SYNC-TREE all tab panels and columns render locally, including inactive content',()=>{
 const tree=node('origin','synced_block',{},[node('tabs','tabs',{tabs:[{id:'a',name:'A'},{id:'b',name:'B'}],active_tab_id:'b'},[node('left','paragraph',{tab_id:'a',layout_group:'g',layout_column:0}),node('right','paragraph',{tab_id:'a',layout_group:'g',layout_column:1}),node('second','paragraph',{tab_id:'b'})])]);
 const html=view.render(tree);assert.match(html,/aria-selected="false" tabindex="-1"/);assert.match(html,/aria-selected="true" tabindex="0"/);
 assert.match(html,/synced-content-columns/);assert.match(html,/left/);assert.match(html,/right/);assert.match(html,/second/);assert.match(html,/data-synced-panel="synced-panel-1" hidden/);
});
test('UI-SYNC-TREE rich renderer receives current blocks, assets retain authenticated file route',()=>{
 const tree=node('origin','synced_block',{},[node('photo','image',{file_id:'file_one'}),node('audio','audio',{file_id:'file_two'})]);
 const before=JSON.stringify(tree),html=view.render(tree,{richHtml:b=>`<strong>${b.content}</strong>`,fileUrl:id=>'/api/notion/collaboration/files/'+id});
 assert.match(html,/<strong>origin<\/strong>/);assert.match(html,/img loading="lazy" src="\/api\/notion\/collaboration\/files\/file_one"/);assert.match(html,/<audio controls preload="none"/);
 assert.equal(JSON.stringify(tree),before);assert.doesNotMatch(view.render(tree,{fileUrl:()=> 'javascript:alert(1)'}),/src=|javascript:/);
});
test('UI-SYNC-TREE malformed hierarchy and nested unresolved references cannot masquerade as original content',()=>{
 const nested=node('reference','synced_block',{synced_from:{document_id:'other',block_id:'x'}},[node('private-child')]);nested.content='untrusted cached private body';
 const html=view.render(node('origin','synced_block',{},[nested]));assert.doesNotMatch(html,/private body|private-child/);assert.match(html,/중첩된 동기화 참조/);
 const repeat=node('same');assert.throws(()=>view.render(node('origin','synced_block',{},[repeat,repeat])));
});
test('UI-SYNC-TREE local expansion survives replacement by stable block identity',()=>{
 const detail={open:true},original={dataset:{syncedNode:'toggle'},querySelector:selector=>selector.endsWith('details')?detail:null};
 const state=view.capture({querySelectorAll:()=>[original]});
 const replacement={open:false},unrelated={open:false};
 const item=(id,target)=>({dataset:{syncedNode:id},querySelector:()=>target,querySelectorAll:()=>[]});
 view.restore({querySelectorAll:()=>[item('new-toggle',unrelated),item('toggle',replacement)]},state);
 assert.equal(replacement.open,true);assert.equal(unrelated.open,false);
});
test('UI-SYNC-TREE local indent gaps and visual offsets survive without moving the outer reference',()=>{
 const html=view.render(node('origin','synced_block',{indent:3},[node('gap','paragraph',{indent:6,visual_indent:1})]));
 assert.match(html,/data-synced-node="origin"[^>]*style="margin-left:0px"/);
 assert.match(html,/data-synced-node="gap"[^>]*style="margin-left:72px"/);
});

test('UI-SYNC-TREE layout containers carry nested columns without extra empty editors or inherited grids',()=>{
 const holder=node('holder','paragraph',{layout_container:true,layout_group:'outer',layout_column:0},[
  node('left','callout',{layout_group:'inner',layout_column:0},[node('child','paragraph',{layout_group:'inner',layout_column:0})]),
  node('right','paragraph',{layout_group:'inner',layout_column:1})]);holder.content='';
 const tree=node('origin','synced_block',{},[holder,node('outside','paragraph',{layout_group:'outer',layout_column:1})]);
 const editors=[],html=view.render(tree,{textEditor:block=>{editors.push(block.id);return block.content;}});
 assert.equal((html.match(/class="synced-content-columns"/g)||[]).length,2);
 assert.ok(!editors.includes('holder'));assert.ok(editors.includes('child'));assert.doesNotMatch(html,/<p><br><\/p>/);
 assert.match(html,/data-synced-node="holder"[^>]*margin-top:0;margin-bottom:0/);
 holder.content='keep me';assert.match(view.render(tree),/keep me/);
});

test('UI-SYNC-TREE numbered lists count independently in each column and reset within that column',()=>{
 const item=(id,type,column)=>node(id,type,{layout_group:'g',layout_columns:2,layout_column:column});
 const tree=node('origin','synced_block',{},[item('left-one','numbered',0),item('right-one','numbered',1),item('left-two','numbered',0),item('right-text','paragraph',1),item('left-three','numbered',0),item('right-new','numbered',1)]);
 const before=JSON.stringify(tree),html=view.render(tree);
 assert.equal((html.match(/class="synced-content-columns"/g)||[]).length,1);
 for(const [id,start] of [['left-one',1],['left-two',2],['left-three',3],['right-one',1],['right-new',1]])assert.match(html,new RegExp(`data-synced-node="${id}"[^>]*><ol start="${start}">`));
 assert.equal(JSON.stringify(tree),before);
});
