'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const b=(id,props={},content=id)=>({id,type:'paragraph',content,props});
function pasteContext(blocks,commands=structure){
 const events=[];
 const context={EditorCore:core,State:{pageData:{blocks}},window:{NotionBlockStructure:commands},
  caretOffset:()=>2,editorPlainText:()=>blocks[1].content,markDirty:()=>events.push('save'),renderBlocks:()=>events.push('render'),
  captureEditorHistory:()=>{},focusBlock:(index,offset)=>events.push({index,offset}),setStatus:message=>events.push({message})};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function pasteMultilineAt('),source.indexOf('function bindBlockDrag(')),context);
 return {context,events};
}
test('UI-STRUCTURE multiline paste retains parent and column membership, tail text and focus',()=>{
 const rows=[b('parent',{children:['anchor']}),b('anchor',{layout_group:'g',layout_column:1},'abcd')];
 const {context,events}=pasteContext(rows);context.pasteMultilineAt(1,{},'one\ntwo');
 const out=context.State.pageData.blocks;
 assert.equal(out[1].content,'abone');assert.equal(out[2].content,'twocd');
 assert.deepEqual(out[0].props.children,['anchor',out[2].id]);assert.equal(out[2].props.layout_column,1);
 assert.equal(rows[1].content,'abcd');assert.deepEqual(events,['save','render',{index:2,offset:3}]);
});
test('UI-STRUCTURE rejected multiline paste preserves original text and rich formatting without saving',()=>{
 const rows=[b('parent'),b('anchor',{rich_text_html:'<b>abcd</b>'},'abcd')],before=structuredClone(rows);
 const {context,events}=pasteContext(rows,{insert(){throw Error('unsupported placement');}});
 context.pasteMultilineAt(1,{},'one\ntwo');assert.deepEqual(context.State.pageData.blocks,before);
 assert.deepEqual(events,[{message:'unsupported placement'}]);
});
test('UI-STRUCTURE native drop handler moves a closed subtree and cleans its former parent',()=>{
 const rows=[b('parent',{children:['fold']}),{...b('fold',{children:['hidden'],open:false}),type:'toggle'},b('hidden'),b('target')];
 const handlers={},handle={addEventListener:(key,fn)=>handlers['handle:'+key]=fn},row={querySelector:()=>handle,classList:{add(){},remove(){}},addEventListener:(key,fn)=>handlers[key]=fn};
 let saves=0,renders=0;
 const context={EditorCore:core,window:{NotionBlockStructure:structure},State:{pageData:{blocks:rows},selectedBlockIds:new Set(['fold']),blockDrag:{id:'fold',ids:['fold'],overId:'target',position:'after'}},
  markDirty:()=>saves++,captureEditorHistory:()=>{},renderBlocks:()=>renders++,setStatus:message=>assert.fail(message)};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function bindBlockDrag('),source.indexOf('function addBlockToColumn(')),context);
 context.bindBlockDrag(row,rows[3],3,true);handlers.drop({altKey:false,preventDefault(){}});
 assert.deepEqual(context.State.pageData.blocks.map(b=>b.id),['parent','target','fold','hidden']);
 assert.deepEqual(context.State.pageData.blocks[0].props.children,[]);
 assert.deepEqual(context.State.pageData.blocks[2].props.children,['hidden']);
 assert.equal(context.State.pageData.blocks[2].props.open,false);assert.equal(saves,1);assert.equal(renders,1);
 assert.deepEqual([...context.State.selectedBlockIds],['fold','hidden']);assert.equal(context.State.blockDrag.id,'');
});
test('UI-STRUCTURE entering a narrow column accepts an immediate drop without another hover event',()=>{
 const rows=[b('parent',{children:['target']}),b('target',{layout_group:'inner',layout_column:1,layout_columns:2}),b('source')];
 const handlers={},handle={addEventListener:(key,fn)=>handlers['handle:'+key]=fn},classes=new Set();
 const row={querySelector:()=>handle,classList:{add:value=>classes.add(value)},getBoundingClientRect:()=>({top:100,height:28}),addEventListener:(key,fn)=>handlers[key]=fn};
 let saved=0;
 const context={EditorCore:core,window:{NotionBlockStructure:structure},State:{pageData:{blocks:rows},selectedBlockIds:new Set(),blockDrag:{id:'source',ids:['source'],overId:'old-target',position:'before'}},
  document:{querySelectorAll:()=>[]},markDirty:()=>saved++,captureEditorHistory:()=>{},renderBlocks:()=>{},setStatus:message=>assert.fail(message)};
 vm.createContext(context);vm.runInContext(source.slice(source.indexOf('function bindBlockDrag('),source.indexOf('function addBlockToColumn(')),context);
 context.bindBlockDrag(row,rows[1],1,true);
 const enter={clientY:124,altKey:false,dataTransfer:{},preventDefault(){this.defaultPrevented=true;}};
 handlers.dragenter(enter);
 assert.equal(enter.defaultPrevented,true);assert.equal(enter.dataTransfer.dropEffect,'move');assert.equal(classes.has('drop-after'),true);
 assert.equal(context.State.blockDrag.overId,'target');assert.equal(context.State.blockDrag.position,'after');
 handlers.drop({altKey:false,preventDefault(){}});
 const result=context.State.pageData.blocks;
 assert.deepEqual(result[0].props.children,['target','source']);assert.equal(result[2].props.layout_group,'inner');
 assert.equal(result[2].props.layout_column,1);assert.equal(saved,1);
});
