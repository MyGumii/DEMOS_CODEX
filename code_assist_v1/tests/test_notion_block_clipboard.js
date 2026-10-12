const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const core=require('../notion_static/notion_editor_core');
const grid=require('../notion_static/notion_grid_selection');
const source=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const blocks=[
 core.createBlock('toggle',{id:'t',content:'닫힌 토글',props:{open:false}}),
 core.createBlock('paragraph',{id:'child',content:'숨겨진 내용',props:{indent:1}}),
 core.createBlock('callout',{id:'c',content:'메모',props:{icon:'💡',rich_text_html:'<b>메모</b>'}}),
 core.createBlock('simple_table',{id:'s',props:{rows:[['A','B']]}}),
];
assert.deepEqual(core.expandBlockSelection(blocks,[0]),[0,1]);
assert.deepEqual(core.removeBlocks(blocks,[0]).map(b=>b.id),['c','s']);
assert.deepEqual(core.moveBlockSelection(blocks,[0],1).map(b=>b.id),['c','t','child','s']);
assert.deepEqual(core.moveBlockSelection(blocks,[2],-1).map(b=>b.id),['c','t','child','s']);
assert.deepEqual(core.moveBlockSelection(blocks,[1],-1).map(b=>b.id),['t','child','c','s']);
const duplicate=core.duplicateBlocks(blocks,[0,2]);
assert.deepEqual(duplicate.map(b=>b.content),['닫힌 토글','숨겨진 내용','메모','닫힌 토글','숨겨진 내용','메모','']);
assert.equal(new Set(duplicate.map(b=>b.id)).size,duplicate.length);
const layout=core.cloneBlocks([core.createBlock('paragraph',{props:{layout_group:'g',layout_column:0}}),core.createBlock('paragraph',{props:{layout_group:'g',layout_column:1}})]);
assert.equal(layout[0].props.layout_group,layout[1].props.layout_group);assert.notEqual(layout[0].props.layout_group,'g');
let editable=true,saves=0,lastStatus='',pasted;
const context={window:{NotionGridSelection:grid,NotionBlockClipboard:require('../notion_static/notion_block_clipboard'),NotionBlockStructure:require('../notion_static/notion_block_structure')},location:{origin:'http://localhost'},notionFileUrl:id=>'/file/'+id,EditorCore:core,DATABASE_BLOCK_TYPES:new Set(['table']),State:{pageData:{blocks:structuredClone(blocks)},selectedBlockIds:new Set(['t','c','s'])},
 activePageCanEdit:()=>editable,selectedBlockIndices:()=>context.State.pageData.blocks.map((b,i)=>context.State.selectedBlockIds.has(b.id)?i:-1).filter(i=>i>=0),
 setStatus:text=>lastStatus=text,captureEditorHistory:()=>{},markDirty:()=>saves++,renderBlocks:()=>{},clearBlockSelection:()=>context.State.selectedBlockIds.clear(),sanitizeRichHtml:x=>x,document:{activeElement:null}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf('function selectedBlocksPlainText('),source.indexOf('function databaseStructureMutationAllowed(')),context);
vm.runInContext(source.slice(source.indexOf('function pasteStructuredBlocks('),source.indexOf('\nfunction renderSearchFilterChips(')),context);
const clipboard=new Map(), event={target:{closest:()=>null},preventDefault(){},stopPropagation(){},clipboardData:{setData:(type,value)=>clipboard.set(type,value),getData:type=>clipboard.get(type)||''}};
context.handleSelectedBlocksClipboard({...event,clipboardData:{setData(){throw Error('denied');}}},true);
assert.equal(context.State.pageData.blocks.length,4);assert.equal(saves,0);assert.match(lastStatus,/유지/);
editable=false;context.handleSelectedBlocksClipboard(event,true);assert.equal(saves,0);
context.handleSelectedBlocksClipboard(event);
assert.equal(clipboard.get('text/plain'),'닫힌 토글\n숨겨진 내용\n메모\nA\tB','readonly mixed selection must preserve table cells in ordinary text');
assert.equal(saves,0,'copying a readonly table cannot dirty the document');
const originalRows=context.State.pageData.blocks[3].props.rows;
const complexRows=[['첫 줄\n둘째 줄','탭\t포함'],['"인용"','']];
context.State.pageData.blocks[3].props.rows=complexRows;
const encodedTable=context.selectedBlocksPlainText([3]);
assert.deepEqual(grid.parseTsv(encodedTable),complexRows,'multiline, tabs, quotes and empty cells must survive a table text round trip');
assert.deepEqual(context.State.pageData.blocks[3].props.rows,complexRows,'serialization does not rewrite the source cells');
context.State.pageData.blocks[3].props.rows=originalRows;
editable=true;context.handleSelectedBlocksClipboard(event);
const wire=JSON.parse(clipboard.get('application/x-notion-local-blocks+json'));
assert.equal(wire.shape,'tree');
assert.deepEqual(wire.blocks.map(b=>b.id),['t','c','s']);
assert.deepEqual(wire.blocks[0].props.children.map(b=>b.id),['child'],'collapsed content stays owned by its toggle on the wire');
context.pasteStructuredBlocks(event,3);pasted=context.State.pageData.blocks.slice(4);
assert.deepEqual(pasted.map(b=>b.type),['toggle','paragraph','callout','simple_table']);
assert.deepEqual(pasted[3].props.rows,[['A','B']]);assert.equal(pasted[2].props.rich_text_html,'<b>메모</b>');
assert.ok(pasted.every(b=>!blocks.some(old=>old.id===b.id)));assert.equal(saves,1);
clipboard.set('application/x-notion-local-blocks+json',JSON.stringify({version:1,blocks:[{type:'table',props:{}}]}));
context.pasteStructuredBlocks(event,0);assert.equal(saves,1);
console.log('Structured clipboard preserves mixed blocks, subtree commands, independent IDs and rejects failed/readonly/DB writes');
