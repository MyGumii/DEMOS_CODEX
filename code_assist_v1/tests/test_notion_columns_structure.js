'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure'),clipboard=require('../notion_static/notion_block_clipboard');
const {CollaborationEditor,loadPinnedYjs}=require('./notion_collaboration_test_runtime');
const b=(id,type='paragraph',props={},content=id)=>({id,type,props,content});
const parents=rows=>Object.fromEntries(core.blockOutline(rows).map((e,i)=>[rows[i].id,rows[e.parentIndex]?.id||null]));
const fixture=()=>[b('owner','callout',{children:['source','right']}),b('source','toggle',{open:false,children:['deep','tabs'],layout_group:'outer',layout_column:0,rich_text_html:'<b>source</b>'}),b('deep','paragraph',{layout_group:'inner',layout_column:0}),b('tabs','tabs',{layout_group:'inner',layout_column:1,tabs:[{id:'a'},{id:'b'}],active_tab_id:'a',children:['hidden']}),b('hidden','paragraph',{tab_group:'tabs',tab_id:'b'}),b('right','paragraph',{layout_group:'outer',layout_column:1})];

test('UI-COLUMNS conversion preserves identity, rich text, closed children, nested columns and tabs',()=>{
 const rows=fixture(),before=structuredClone(rows),{blocks,focusId}=structure.columns(rows,1,3,{replace:true});
 const p=parents(blocks),by=Object.fromEntries(blocks.map(b=>[b.id,b])),wrapper=by[p.source];
 assert.equal(focusId,'source');assert.equal(p[wrapper.id],'owner');assert.equal(core.isLayoutContainer(wrapper),true);
 assert.equal(wrapper.props.layout_group,'outer');assert.equal(wrapper.props.layout_column,0);assert.equal(wrapper.props.children.length,3);
 assert.equal(by.source.type,'toggle');assert.equal(by.source.content,'source');assert.equal(by.source.props.rich_text_html,'<b>source</b>');assert.equal(by.source.props.open,false);
 assert.equal(by.source.props.layout_column,0);assert.notEqual(by.source.props.layout_group,'outer');
 assert.equal(p.deep,'source');assert.equal(p.tabs,'source');assert.equal(p.hidden,'tabs');assert.equal(by.deep.props.layout_group,'inner');
 assert.equal(by.hidden.props.tab_group,'tabs');assert.equal(by.right.props.layout_group,'outer');assert.deepEqual(rows,before);
});

test('UI-COLUMNS insert in a column adds an owned inner grid without splitting the anchor subtree',()=>{
 const rows=fixture(),out=structure.columns(rows,1,2).blocks,p=parents(out),wrapper=out.find(core.isLayoutContainer);
 assert.equal(p[wrapper.id],'owner');assert.equal(wrapper.props.layout_group,'outer');
 assert.ok(out.indexOf(wrapper)>out.findIndex(b=>b.id==='hidden'));assert.equal(p.source,'owner');assert.equal(wrapper.props.children.length,2);
 assert.deepEqual(wrapper.props.children.map(id=>out.find(b=>b.id===id).props.layout_column),[0,1]);
});

test('UI-COLUMNS plus before inserts the inner grid ahead of the anchor subtree',()=>{
 const rows=fixture(),out=structure.columns(rows,1,2,{before:true}).blocks,p=parents(out);
 const wrapper=out.find(core.isLayoutContainer);
 assert.equal(p[wrapper.id],'owner');
 assert.ok(out.indexOf(wrapper)<out.findIndex(block=>block.id==='source'));
 assert.equal(p.source,'owner');assert.equal(p.deep,'source');
 assert.deepEqual(rows,fixture());
});

test('UI-COLUMNS slash conversion retains children and never clears remaining source content',()=>{
 const rows=fixture();rows[1].content='';delete rows[1].props.rich_text_html;
 const out=structure.columns(rows,1,2,{replace:true,clear:true}).blocks;
 assert.equal(out.find(b=>b.id==='source').type,'paragraph');assert.equal(parents(out).deep,'source');
 const populated=structure.columns(fixture(),1,2,{replace:true,clear:true}).blocks;
 assert.equal(populated.find(b=>b.id==='source').content,'source');assert.equal(populated.find(b=>b.id==='source').props.rich_text_html,'<b>source</b>');
});

test('UI-COLUMNS wrapped layout survives structured clipboard without adding visual indentation',()=>{
 const blocks=structure.columns(fixture(),1,2,{replace:true}).blocks;let n=0;
 const out=clipboard.decode(clipboard.encode(blocks),{createId:()=>`copy-${++n}`,shape:'flat'}),outline=core.blockOutline(out);
 const wrapper=out.findIndex(core.isLayoutContainer),child=out.findIndex(b=>b.content==='source');
 assert.equal(outline[child].parentIndex,wrapper);assert.equal(outline[child].visualIndent,outline[wrapper].visualIndent);
 assert.equal(out.find(b=>b.content==='hidden').props.tab_group,out.find(b=>b.type==='tabs').id);
});

test('UI-COLUMNS actual UI handler commits nested conversion to Yjs and undo/redo preserves original ownership',t=>{
 const Y=loadPinnedYjs(),State={pageData:{blocks:fixture()},selectedBlockIds:new Set()},notes=[];
 const host=core.createEditorHostAdapter({readDocument:()=>({page_id:'page',title:'Title',blocks:State.pageData.blocks,can_edit:true}),applyProjection:value=>{State.pageData.blocks=structuredClone(value.blocks);return {applied:true};}});
 const bridge=CollaborationEditor.createEditorBridge({Y,host});bridge.configureDocument(host.readDocument());bridge.finishReplay();t.after(()=>{bridge.dispose();host.dispose();});
 const ctx={State,window:{NotionBlockStructure:structure},activePageCanEdit:()=>true,markDirty:()=>host.notifyLocalMutation('document'),captureEditorHistory(){},closeBlockMenu(){},renderBlocks(){},focusBlock:index=>ctx.focus=index,setStatus:message=>notes.push(message)};
 const main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');vm.createContext(ctx);vm.runInContext(main.slice(main.indexOf('function createColumnLayout('),main.indexOf('function chooseBlockType(')),ctx);
 const before=structuredClone(State.pageData.blocks);bridge.stopCapturing();ctx.createColumnLayout(1,2,'convert');assert.deepEqual(notes,[]);
 assert.equal(State.pageData.blocks[ctx.focus].id,'source');assert.deepEqual(bridge.projection().blocks,State.pageData.blocks);
 bridge.stopCapturing();bridge.undo();assert.deepEqual(State.pageData.blocks,before);bridge.redo();assert.equal(State.pageData.blocks.filter(core.isLayoutContainer).length,1);
});

test('UI-COLUMNS insertion failure cannot mutate either source or incoming nested layout',()=>{
 const rows=fixture(),before=structuredClone(rows),incoming=[b('source','paragraph',{layout_group:'g',layout_column:0}),b('copy','paragraph',{layout_group:'g',layout_column:1})],original=structuredClone(incoming);
 assert.throws(()=>structure.insert(rows,1,incoming));assert.deepEqual(rows,before);assert.deepEqual(incoming,original);
});

test('UI-COLUMNS invisible owners never conceal text and add no empty HTML clipboard paragraph',()=>{
 const wrapper=b('wrapper','paragraph',{layout_container:true,children:[b('inside')]},'');
 assert.equal(core.isLayoutContainer(wrapper),true);assert.equal(clipboard.html([wrapper]),'<p>inside</p>');
 wrapper.content='keep this';assert.equal(core.isLayoutContainer(wrapper),false);assert.match(clipboard.html([wrapper]),/keep this/);
 wrapper.content='';wrapper.props.rich_text_html='<b>keep formatting</b>';assert.equal(core.isLayoutContainer(wrapper),false);
});
