'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure');
const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const b=(id,type='paragraph',props={},content=id)=>({id,type,content,props});
const parentMap=rows=>Object.fromEntries(core.blockOutline(rows).map((entry,i)=>[rows[i].id,rows[entry.parentIndex]?.id||null]));
function harness(rows,hooks={}){
 const notes=[],ctx={EditorCore:core,window:{NotionBlockStructure:structure,NotionSyncedReferences:{explicit:()=>false}},
  State:{activePageId:'page',user:{id:'owner'},pageData:{page:{},blocks:structuredClone(rows)},selectedBlockIds:new Set(),uploadMode:'file',uploadInsertIndex:1,uploadReplaceIndex:null},
  editable:true,activePageCanEdit:()=>ctx.editable,NON_ADOPTED_BLOCK_TYPES:new Set(),DATABASE_BLOCK_TYPES:new Set(['table']),
  canEditDatabaseSchema:()=>true,selectionContainsDatabase:()=>false,databaseStructureMutationAllowed:()=>true,
  selectedBlockIndices:index=>ctx.selection||[index],setStatus:message=>notes.push(message),
  markDirty:()=>ctx.State.dirty=true,captureEditorHistory(){},renderBlocks(){},closeBlockMenu(){},focusBlock:(index,offset)=>ctx.focus={index,offset},
  editorPlainText:editor=>editor.textContent,
  flushPageDraft:async()=>{await hooks.flush?.(ctx);return true;},collaborativeFileBasePath:()=>'/collaboration',
  uploadCollaborativeFile:async()=>{await hooks.upload?.(ctx);return {file:{id:'uploaded',original_name:'note.txt',mime_type:'text/plain'}};},
  refreshOpenedPageAfterMutation:async()=>({}),savePage:async()=>{ctx.saved=(ctx.saved||0)+1;ctx.State.dirty=false;},loadPages:async()=>{},
  api:async(url,options)=>{ctx.request={url,options};await hooks.create?.(ctx);return {page:{id:'child-page',title:options.body.title}};}};
 vm.createContext(ctx);
 for(const [start,end] of [
  ['function captureBlockInsertion(','async function insertChildPageAfter('],
  ['async function insertChildPageAfter(','const PendingDatabaseItemCreates'],
  ['function changeBlockType(','function duplicateTabsBlockGroup('],
  ['function applyMarkdownShortcut(','function pasteMultilineAt('],
  ['async function uploadSelectedFile(','async function importDataFile('],
 ])vm.runInContext(main.slice(main.indexOf(start),main.indexOf(end)),ctx);
 return {ctx,notes};
}
test('UI-TYPED replacement preserves stable identity, all owned columns and presentation placement',()=>{
 const rows=[b('outer','callout',{children:['parent']}),b('parent','toggle',{children:['left','right'],tab_group:'tabs',tab_id:'b',indent:2,visual_indent:1,text_color:'red',open:false}),b('left','paragraph',{layout_group:'g',layout_column:0}),b('right','paragraph',{layout_group:'g',layout_column:1})];
 const before=structuredClone(rows),out=structure.replace(rows,1,b('unused','heading1'));
 assert.deepEqual(out.map(row=>row.id),rows.map(row=>row.id));assert.equal(out[1].type,'heading1');
 assert.deepEqual(out[1].props.children,['left','right']);assert.equal(out[1].props.tab_group,'tabs');assert.equal(out[1].props.visual_indent,1);
 assert.equal(out[1].props.text_color,'red');assert.equal(parentMap(out).right,'parent');assert.deepEqual(rows,before);
});
test('UI-TYPED conversion out of tabs exposes all owned panels and preserves nested tab controllers',()=>{
 const rows=[b('tabs','tabs',{tabs:[{id:'a'},{id:'b'}],active_tab_id:'a'}),b('nested','tabs',{tab_group:'tabs',tab_id:'a',tabs:[{id:'x'}],active_tab_id:'x'}),b('deep','paragraph',{tab_group:'nested',tab_id:'x'}),b('second','paragraph',{tab_group:'tabs',tab_id:'b'})];
 const out=structure.replace(rows,0,b('new','heading1'));
 assert.deepEqual(parentMap(out),{tabs:null,nested:'tabs',deep:'nested',second:'tabs'});
 assert.equal(out[1].props.tab_group,undefined);assert.equal(out[2].props.tab_group,'nested');
 assert.equal(out[3].props.tab_group,undefined);assert.equal(core.blockOutline(out)[3].hiddenByTab,false);
});
test('UI-TYPED replacement retains inline owned objects without borrowing the input object',()=>{
 const rows=[b('parent','callout',{children:[b('child')]})],out=structure.replace(rows,0,b('new','quote'));
 assert.deepEqual(out[0].props.children,rows[0].props.children);out[0].props.children[0].content='edited';assert.equal(rows[0].props.children[0].content,'child');
});
test('UI-TYPED actual type menu preserves rich text, colors, tab membership and flat child edges',()=>{
 const rows=[b('parent','callout',{children:['child'],tab_group:'controller',tab_id:'b',text_color:'red',rich_text_html:'<b>parent</b>'}),b('child')];
 const {ctx}=harness(rows);ctx.changeBlockType(0,'heading1');
 const out=ctx.State.pageData.blocks;assert.equal(out[0].type,'heading1');assert.deepEqual(out[0].props.children,['child']);
 assert.equal(out[0].props.tab_group,'controller');assert.equal(out[0].props.rich_text_html,'<b>parent</b>');assert.equal(out[0].props.text_color,'red');
 assert.equal(parentMap(out).child,'parent');
});
test('UI-TYPED multi-selection conversion follows IDs when serialization reorders noncontiguous children',()=>{
 const rows=[b('parent','callout',{children:['child']}),b('other'),b('child')],{ctx}=harness(rows);ctx.selection=[0,1];ctx.changeBlockType(0,'heading2');
 assert.equal(ctx.State.pageData.blocks.find(row=>row.id==='other').type,'heading2');assert.equal(ctx.State.pageData.blocks.find(row=>row.id==='child').type,'paragraph');
});
test('UI-TYPED markdown divider conversion retains its descendants and inserts a real sibling afterward',()=>{
 const rows=[b('owner','callout',{children:['anchor']}),b('anchor','paragraph',{children:['child'],layout_group:'g',layout_column:1,tab_group:'t',tab_id:'a'},'---'),b('child')];
 const {ctx}=harness(rows);assert.equal(ctx.applyMarkdownShortcut({textContent:'---'},ctx.State.pageData.blocks[1],1),true);
 const out=ctx.State.pageData.blocks,map=parentMap(out),next=out.at(-1);
 assert.equal(out[1].type,'divider');assert.equal(map.child,'anchor');assert.equal(map[next.id],'owner');
 assert.equal(next.props.layout_column,1);assert.equal(next.props.tab_group,'t');assert.equal(ctx.focus.index,out.length-1);
});
test('UI-TYPED delayed upload follows the selected block after a concurrent insertion',async()=>{
 const rows=[b('owner','callout',{children:['anchor']}),b('anchor','paragraph',{layout_group:'g',layout_column:1})];
 const {ctx}=harness(rows,{upload(c){c.State.pageData.blocks.unshift(b('remote'));}});ctx.State.uploadInsertIndex=2;
 await ctx.uploadSelectedFile({name:'note.txt',type:'text/plain'});
 const out=ctx.State.pageData.blocks,asset=out.find(row=>row.props.file_id==='uploaded');
 assert.ok(asset);assert.equal(parentMap(out)[asset.id],'owner');assert.equal(asset.props.layout_column,1);assert.equal(ctx.saved,1);
 assert.equal(out.findIndex(row=>row.id===asset.id),out.findIndex(row=>row.id==='anchor')+1);
});
test('UI-TYPED delayed upload never inserts into a newly opened page',async()=>{
 const {ctx,notes}=harness([b('anchor')],{upload(c){c.State.activePageId='other';c.State.pageData.blocks=[b('untouched')];}});
 await ctx.uploadSelectedFile({name:'note.txt',type:'text/plain'});
 assert.deepEqual(ctx.State.pageData.blocks,[b('untouched')]);assert.equal(ctx.saved,undefined);assert.ok(notes.some(note=>note.includes('파일은 업로드됐지만')));
});
test('UI-TYPED delayed replacement refuses changed content and leaves the current formatting untouched',async()=>{
 const {ctx}=harness([b('anchor')],{upload(c){c.State.pageData.blocks[0].content='new input';c.State.pageData.blocks[0].props.rich_text_html='<b>new input</b>';}});
 ctx.State.uploadReplaceIndex=0;ctx.State.uploadInsertIndex=0;
 await ctx.uploadSelectedFile({name:'note.txt',type:'text/plain'});
 assert.equal(ctx.State.pageData.blocks[0].content,'new input');assert.equal(ctx.State.pageData.blocks[0].props.rich_text_html,'<b>new input</b>');assert.equal(ctx.saved,undefined);
});
test('UI-TYPED uploaded replacement keeps owned content and its stable link identity',async()=>{
 const rows=[b('owner','callout',{children:['anchor']}),b('anchor','paragraph',{children:['child'],tab_group:'tabs',tab_id:'a'}),b('child')];
 const {ctx}=harness(rows);ctx.State.uploadReplaceIndex=1;ctx.State.uploadInsertIndex=1;
 await ctx.uploadSelectedFile({name:'note.txt',type:'text/plain'});
 const out=ctx.State.pageData.blocks,asset=out.find(row=>row.id==='anchor');assert.equal(asset.type,'file');assert.equal(asset.props.file_id,'uploaded');
 assert.deepEqual(asset.props.children,['child']);assert.equal(asset.props.tab_group,'tabs');assert.equal(parentMap(out).child,'anchor');
});
test('UI-TYPED delayed child-page insertion retains its anchor parent after another block arrives',async()=>{
 const rows=[b('owner','callout',{children:['anchor']}),b('anchor')];
 const {ctx}=harness(rows,{create(c){c.State.pageData.blocks.unshift(b('remote'));}});await ctx.insertChildPageAfter(1,false);
 const out=ctx.State.pageData.blocks,child=out.find(row=>row.type==='child_page');assert.ok(child);assert.equal(parentMap(out)[child.id],'owner');
 assert.equal(child.props.page_id,'child-page');assert.equal(ctx.request.options.body.parent_id,'page');assert.equal(ctx.saved,1);
});
test('UI-TYPED insertion intent rejects deleted anchors, access loss and actor changes',()=>{
 for(const change of [ctx=>ctx.State.pageData.blocks=[],ctx=>ctx.editable=false,ctx=>ctx.State.user.id='someone']){
  const {ctx}=harness([b('anchor')]),target=ctx.captureBlockInsertion(0);change(ctx);const before=structuredClone(ctx.State.pageData.blocks);
  assert.throws(()=>ctx.applyBlockInsertion(target,b('new')));assert.deepEqual(ctx.State.pageData.blocks,before);
 }
});
test('UI-TYPED replacement signatures ignore property ordering without ignoring changed values',()=>{
 assert.equal(structure.fingerprint(b('a','paragraph',{indent:1,text_color:'red'})),structure.fingerprint(b('a','paragraph',{text_color:'red',indent:1})));
 assert.notEqual(structure.fingerprint(b('a','paragraph',{indent:1})),structure.fingerprint(b('a','paragraph',{indent:2})));
});

function fileHost(change=()=>{}) {
 const State={user:{id:'owner'},navigation:{workspaceId:'ws'},activePageId:'page',pageData:{page:{id:'page',document_sequence:2},blocks:[b('draft')]},liveCollaboration:{durableCursor:8}};
 const current={page:{id:'page',can_edit:true,document_sequence:9,workspace_epoch:3,document_epoch:1}};
 const calls=[],receipt={id:'file',file_id:'file',document_id:'page',contract:'code_assist.notion.collaborative_file_authority.v1',state:'active',document_sequence:10};
 const ctx={State,AbortController,collaborativeFileBasePath:()=>'/files',pageIsLocked:page=>page.locked,
  api:async url=>{calls.push(url);change({State,current});return current;},authenticatedFetch(){},setStatus(){},
  ResumableFileUpload:{upload:async options=>{calls.push(options);return {file:receipt};}}};
 vm.createContext(ctx);
 for(const [start,end] of [['function iconScope()','function pageCanChangeAppearance('],['async function uploadPageResource(','async function openIconPicker('],['async function uploadCollaborativeFile(','async function loadCollaborativeFiles(']]) {
  vm.runInContext(main.slice(main.indexOf(start),main.indexOf(end)),ctx);
 }
 return {ctx,State,calls,receipt};
}

test('UI-TYPED body upload refreshes its version after edits without skipping body replay',async()=>{
 const h=fileHost(),result=await h.ctx.uploadCollaborativeFile({name:'note.txt'},'page');
 assert.equal(h.calls[0],'/api/notion/workspaces/ws/pages/page/collaboration');
 assert.equal(h.calls[1].documentSequence,9);assert.equal(h.calls[1].workspaceEpoch,3);assert.equal(h.calls[1].documentEpoch,1);
 assert.equal(result.file,h.receipt);assert.equal(h.State.pageData.fileUploadSequence,10);
 assert.equal(h.State.pageData.page.document_sequence,2);assert.equal(h.State.liveCollaboration.durableCursor,8);
 assert.deepEqual(h.State.pageData.blocks,[b('draft')]);
});

test('UI-TYPED body upload revalidates edit permission, lock, scope and version before sending bytes',async()=>{
 for(const change of [h=>h.current.page.can_edit=false,h=>h.current.page.locked=true,h=>h.State.user.id='other',h=>h.State.navigation.workspaceId='other',h=>h.current.page.document_sequence=0]) {
  const h=fileHost(change);await assert.rejects(h.ctx.uploadCollaborativeFile({name:'note.txt'},'page'));
  assert.equal(h.calls.length,1);assert.equal(h.State.pageData.fileUploadSequence,undefined);
 }
});

function nativeChildHost(hooks={}) {
 const h=harness([b('owner','callout',{children:['anchor']}),b('anchor','paragraph',{layout_group:'g',layout_column:1},'')]);
 h.ctx.State.navigation={enabled:true,workspaceId:'ws',collaborativeSharing:true,collaborativePageCreation:true,workspaces:[{id:'ws',version:2}]};
 h.ctx.selectedNavigationWorkspace=()=>h.ctx.State.navigation.workspaces[0];
 h.ctx.navigationOperationId=()=> 'create-once';h.ctx.requestLiveSync=()=>{};
 h.ctx.api=async(url,options)=>{
  h.ctx.request={url,options};await hooks.create?.(h.ctx);
  if(!h.ctx.State.pageData.blocks.some(row=>row.id==='server-link'))h.ctx.State.pageData.blocks.push(b('server-link','child_page',{page_id:'native-child'},options.body.title));
  return {page:{id:'native-child',title:options.body.title},workspace:{id:'ws',version:3},parent_link_saved:true};
 };
 return h;
}

test('UI-TYPED native child creation moves the committed link into its target column exactly once',async()=>{
 const {ctx}=nativeChildHost();await ctx.insertChildPageAfter(1,true);
 const rows=ctx.State.pageData.blocks,link=rows.find(row=>row.id==='server-link');
 assert.equal(ctx.request.url,'/api/notion/workspaces/ws/pages');assert.equal(ctx.request.options.body.operation_id,'create-once');
 assert.equal(rows.filter(row=>row.type==='child_page').length,1);assert.equal(rows.some(row=>row.id==='anchor'),false);
 assert.equal(parentMap(rows)[link.id],'owner');assert.equal(link.props.layout_column,1);assert.equal(ctx.State.navigation.workspaces[0].version,3);
});

test('UI-TYPED native child creation above a block keeps its column and original block',async()=>{
 const {ctx}=nativeChildHost();await ctx.insertChildPageAfter(1,false,true);
 const rows=ctx.State.pageData.blocks,link=rows.find(row=>row.id==='server-link');
 assert.equal(rows.filter(row=>row.type==='child_page').length,1);
 assert.equal(parentMap(rows)[link.id],'owner');assert.equal(link.props.layout_column,1);
 assert.ok(rows.findIndex(row=>row.id===link.id)<rows.findIndex(row=>row.id==='anchor'));
 assert.ok(rows.some(row=>row.id==='anchor'));assert.equal(ctx.saved,1);
});

test('UI-TYPED native child create retry retains its operation after an uncertain response',async()=>{
 let first=true;const {ctx}=nativeChildHost({create(){if(first){first=false;throw Error('response lost');}}});
 await ctx.insertChildPageAfter(1,true);const operation=ctx.request.options.body.operation_id;
 await ctx.insertChildPageAfter(1,true);assert.equal(ctx.request.options.body.operation_id,operation);
 assert.equal(ctx.State.pageData.blocks.filter(row=>row.type==='child_page').length,1);
});

test('UI-TYPED native child creation cannot overwrite new input while its request is pending',async()=>{
 const {ctx,notes}=nativeChildHost({create(c){c.State.pageData.blocks[1].content='new input';}});
 await ctx.insertChildPageAfter(1,true);assert.equal(ctx.State.pageData.blocks[1].content,'new input');
 assert.equal(ctx.State.pageData.blocks.find(row=>row.id==='server-link').props.layout_group,undefined);
 assert.ok(notes.at(-1).startsWith('페이지는 생성됐지만'));
});

test('UI-TYPED native page conversion uses the whole source as the title and consumes one committed parent link',async()=>{
 const {ctx}=nativeChildHost();ctx.State.pageData.blocks[1].content='전환해도 남아야 하는 문구';
 await ctx.insertChildPageAfter(1,true);
 assert.equal(ctx.request.options.body.title,'전환해도 남아야 하는 문구');
 const links=ctx.State.pageData.blocks.filter(row=>row.type==='child_page');
 assert.equal(links.length,1);assert.equal(links[0].content,'전환해도 남아야 하는 문구');
 assert.equal(parentMap(ctx.State.pageData.blocks)[links[0].id],'owner');assert.equal(ctx.saved,1);
});

test('UI-TYPED page conversion rejects unsupported title length and native descendants before creating anything',async()=>{
 for(const change of [c=>c.State.pageData.blocks[1].content='가'.repeat(161),c=>{c.State.pageData.blocks[1].props.children=['deep'];c.State.pageData.blocks.push(b('deep'));}]){
  const {ctx}=nativeChildHost();change(ctx);const before=structuredClone(ctx.State.pageData.blocks);
  await ctx.insertChildPageAfter(1,true);assert.equal(ctx.request,undefined);assert.deepEqual(ctx.State.pageData.blocks,before);
 }
});

test('UI-TYPED personal page conversion preserves text while ordinary insertion still creates a blank page',async()=>{
 for(const replace of [true,false]) {
  const {ctx}=harness([b('anchor','paragraph',{},'남아야 하는 원문')]);
  await ctx.insertChildPageAfter(0,replace);
  assert.equal(ctx.request.options.body.title,replace?'남아야 하는 원문':'새 페이지');
  assert.equal(ctx.State.pageData.blocks.filter(row=>row.type==='child_page').length,1);
  assert.equal(ctx.State.pageData.blocks.some(row=>row.type==='paragraph'),!replace);
 }
});

test('UI-TYPED page conversion never replaces source when the created title was normalized unexpectedly',async()=>{
 const {ctx,notes}=harness([b('anchor','paragraph',{},'원문')]);
 ctx.api=async()=>({page:{id:'child-page',title:'다른 제목'}});
 await ctx.insertChildPageAfter(0,true);
 assert.equal(ctx.State.pageData.blocks[0].content,'원문');assert.equal(ctx.State.pageData.blocks[0].type,'paragraph');
 assert.equal(ctx.saved,undefined);assert.ok(notes.at(-1).includes('원문은 유지됩니다'));
});
