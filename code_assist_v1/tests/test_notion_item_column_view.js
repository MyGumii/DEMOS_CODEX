'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../notion_static/notion_editor_core');
const source=fs.readFileSync(require.resolve('../notion_static/notion_database_item_editor'),'utf8');
const b=(id,props={},type='paragraph',content=id)=>({id,type,content,props});
function render(blocks,editable=true){
 const escape=v=>String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;');
 const ctx={core,escape,canEdit:()=>editable,types:new Set(['paragraph','callout','toggle','numbered']),mediaTypes:new Set(),host:{html:block=>escape(block.content)}};
 vm.createContext(ctx);vm.runInContext(source.slice(source.indexOf('    function listHtml('),source.indexOf('    function draw(){')),ctx);
 return ctx.listHtml(blocks);
}
test('UI-ITEM-COLUMNS copied nested columns keep each editable text without an extra owner row',()=>{
 const rows=[b('holder',{layout_container:true,layout_group:'outer',layout_column:0,children:[
  b('inside',{layout_group:'inner',layout_column:0,children:[b('child',{layout_group:'inner',layout_column:0})]},'callout'),
  b('right',{layout_group:'inner',layout_column:1})]},'paragraph',''),b('outside',{layout_group:'outer',layout_column:1})];
 const before=structuredClone(rows),html=render(rows);
 assert.equal((html.match(/class="block-column-layout"/g)||[]).length,2);
 assert.doesNotMatch(html,/data-body-(?:text|block)="holder"/);
 for(const id of ['inside','child','right','outside'])assert.equal((html.match(new RegExp(`data-body-text="${id}"`,'g'))||[]).length,1);
 assert.deepEqual(rows,before);
});
test('UI-ITEM-COLUMNS readonly columns preserve closed children without edit controls',()=>{
 const html=render([b('toggle',{layout_group:'g',layout_column:0,open:false,children:[b('hidden')]},'toggle'),b('right',{layout_group:'g',layout_column:1})],false);
 assert.match(html,/item-body-children" hidden/);assert.match(html,/data-body-text="hidden" contenteditable="false"/);
 assert.doesNotMatch(html,/data-body-menu=|contenteditable="true"/);
});
test('UI-ITEM-COLUMNS numbered lists count separately within each column and flagged text stays visible',()=>{
 const html=render([b('a',{layout_group:'g',layout_column:0},'numbered'),b('b',{layout_group:'g',layout_column:1},'numbered'),b('c',{layout_group:'g',layout_column:0},'numbered'),b('marked',{layout_container:true,children:[b('nested')]})]);
 assert.equal((html.match(/item-body-marker">1\./g)||[]).length,2);assert.equal((html.match(/item-body-marker">2\./g)||[]).length,1);
 assert.match(html,/data-body-text="marked"/);
});
