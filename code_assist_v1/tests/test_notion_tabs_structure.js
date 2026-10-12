'use strict';
const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const core=require('../notion_static/notion_editor_core'),structure=require('../notion_static/notion_block_structure');
const {CollaborationEditor,loadPinnedYjs}=require('./notion_collaboration_test_runtime');
const Y=loadPinnedYjs(),main=fs.readFileSync(require.resolve('../notion_static/notion.js'),'utf8');
const b=(id,type='paragraph',props={},content=id)=>({id,type,content,props});
const tabs=(id,props={})=>b(id,'tabs',{tabs:[{id:'a',name:'A'},{id:'b',name:'B'},{id:'c',name:'C'}],active_tab_id:'a',...props},'');
const parents=rows=>Object.fromEntries(core.blockOutline(rows).map((entry,i)=>[rows[i].id,rows[entry.parentIndex]?.id||null]));
const source=()=>[b('owner','callout',{children:['tabs','tail']}),tabs('tabs',{layout_group:'outer',layout_column:1,children:['nested','other','third']}),
 tabs('nested',{tab_group:'tabs',tab_id:'a',children:['hidden','alternate']}),b('hidden','toggle',{tab_group:'nested',tab_id:'a',children:['deep'],open:false}),
 b('deep','paragraph',{rich_text_html:'<b>deep</b>'}),b('alternate','paragraph',{tab_group:'nested',tab_id:'b'}),
 b('other','paragraph',{tab_group:'tabs',tab_id:'b'}),b('third','paragraph',{tab_group:'tabs',tab_id:'c'}),b('tail')];

test('UI-TABS wrapping an owned tree keeps its identities, formatting, columns and nested tab bindings',()=>{
 const rows=[tabs('outer'),b('source','callout',{children:['left','inner'],tab_group:'outer',tab_id:'a',text_color:'red',rich_text_html:'<b>source</b>'}),
 b('left','paragraph',{layout_group:'g',layout_column:0}),tabs('inner',{layout_group:'g',layout_column:1,children:['deep']}),b('deep','paragraph',{tab_group:'inner',tab_id:'b'})];
 const before=structuredClone(rows),controller=core.createBlock('tabs'),empty=b('empty');
 const out=structure.wrapTabs(rows,1,controller,[empty]),map=parents(out),by=Object.fromEntries(out.map(row=>[row.id,row]));
 assert.equal(map[controller.id],'outer');assert.equal(map.source,controller.id);assert.equal(map.left,'source');assert.equal(map.inner,'source');assert.equal(map.deep,'inner');
 assert.equal(by.source.type,'callout');assert.equal(by.source.props.rich_text_html,'<b>source</b>');assert.equal(by.source.props.text_color,'red');
 assert.equal(by.source.props.tab_group,controller.id);assert.equal(by.inner.props.layout_group,'g');assert.equal(by.deep.props.tab_group,'inner');
 assert.equal(core.blockOutline(out)[out.findIndex(row=>row.id==='empty')].hiddenByTab,true);assert.deepEqual(rows,before);
});

test('UI-TABS appending a panel follows the complete nested subtree and remains owned by the controller',()=>{
 const rows=source(),before=structuredClone(rows),out=structure.appendTab(rows,1,{id:'new',name:'New'},b('blank'));
 assert.equal(parents(out).blank,'tabs');assert.equal(out.find(row=>row.id==='blank').props.layout_column,1);
 assert.deepEqual(out.find(row=>row.id==='tabs').props.children,['nested','other','third','blank']);
 assert.deepEqual(out.slice(2,6).map(row=>row.id),['nested','hidden','deep','alternate']);
 assert.equal(core.blockOutline(out)[out.findIndex(row=>row.id==='deep')].hiddenByTab,true);assert.deepEqual(rows,before);
});

test('UI-TABS deleting a panel removes all hidden nested descendants and leaves other panels and siblings untouched',()=>{
 const rows=source(),before=structuredClone(rows),out=structure.removeTab(rows,1,'a');
 assert.deepEqual(out.map(row=>row.id),['owner','tabs','other','third','tail']);
 assert.deepEqual(out[1].props.children,['other','third']);assert.equal(out[1].props.active_tab_id,'b');
 assert.equal(parents(out).tail,'owner');assert.equal(out[2].props.tab_id,'b');assert.deepEqual(rows,before);
});

test('UI-TABS duplicating a controller copies every hidden descendant with independent identities and outer placement',()=>{
 const rows=source(),before=structuredClone(rows),out=structure.duplicateTree(rows,1),originals=new Set(rows.map(row=>row.id)),copies=out.filter(row=>!originals.has(row.id));
 assert.equal(copies.length,7);const controller=copies[0],nested=copies.find(row=>row.type==='tabs'&&row.id!==controller.id),deep=copies.find(row=>row.content==='deep');
 assert.equal(parents(out)[controller.id],'owner');assert.equal(controller.props.layout_group,'outer');assert.equal(controller.props.layout_column,1);
 assert.equal(nested.props.tab_group,controller.id);assert.notEqual(nested.props.tab_id,'a');
 assert.equal(parents(out)[deep.id],copies.find(row=>row.content==='hidden').id);assert.equal(deep.props.rich_text_html,'<b>deep</b>');
 assert.ok(copies.every(row=>!['tabs','nested'].includes(row.props.tab_group)));
 assert.equal(out.findIndex(row=>row.id===controller.id),out.findIndex(row=>row.id==='third')+1);
 deep.content='copy only';assert.equal(out.find(row=>row.id==='deep').content,'deep');assert.deepEqual(rows,before);
});

test('UI-TABS invalid additions and clone failures leave the original tree intact',()=>{
 const rows=source(),before=structuredClone(rows);
 assert.throws(()=>structure.appendTab(rows,1,{id:'a',name:'duplicate'},b('new')));
 assert.throws(()=>structure.duplicateTree(rows,1,()=>{throw Error('blocked reference');}));
 assert.throws(()=>structure.wrapTabs(rows,0,tabs('owner'),[]));assert.deepEqual(rows,before);
});

function harness(t,rows){
 const notes=[],State={activePageId:'page',pageData:{page:{},blocks:structuredClone(rows)},selectedBlockIds:new Set()};
 let ctx;
 const host=core.createEditorHostAdapter({readDocument:()=>({page_id:'page',title:'Title',blocks:State.pageData.blocks,can_edit:ctx?.editable!==false}),applyProjection:value=>{State.pageData.blocks=structuredClone(value.blocks);return {applied:true};},onListenerError:error=>{throw error;}});
 const bridge=CollaborationEditor.createEditorBridge({Y,host});bridge.configureDocument(host.readDocument());bridge.finishReplay();
 ctx={State,EditorCore:core,window:{NotionBlockStructure:structure,confirm:()=>true},crypto:require('node:crypto').webcrypto,
  editable:true,activePageCanEdit:()=>ctx.editable,selectedBlockIndices:index=>ctx.selected||[index],
  markDirty:()=>host.notifyLocalMutation('document'),captureEditorHistory(){},renderBlocks(){},closeBlockMenu(){},closeModal(){},
  setStatus:message=>notes.push(message),focusBlock:(index,offset)=>ctx.focus={index,offset},copyEditorBlock:(_,copy)=>copy,
  openModal:(title,html,buttons)=>ctx.buttons=buttons,$:()=>({value:ctx.rename,focus(){}}),setTimeout:fn=>fn(),escapeAttr:value=>value};
 vm.createContext(ctx);
 for(const [start,end] of [['function newTabId()','function tabsBlockHtml('],['function addTabsBlockTab(','let SyncedReferenceViews'],['function createTabsBlock(','function insertBlockAfter('],['function duplicateTabsBlockGroup(','function handleBlockAction(']])vm.runInContext(main.slice(main.indexOf(start),main.indexOf(end)),ctx);
 t.after(()=>{bridge.dispose();host.dispose();});return {ctx,State,bridge,notes};
}

test('UI-TABS actual conversion retains selected owned content in Yjs and supports undo/redo',t=>{
 const rows=[b('callout','callout',{children:['child'],text_color:'purple'}),b('child','paragraph',{rich_text_html:'<i>child</i>'}),b('tail')];
 const {ctx,State,bridge,notes}=harness(t,rows);bridge.stopCapturing();ctx.createTabsBlock(0,true);
 assert.deepEqual(notes,[]);assert.equal(State.pageData.blocks.length,5);assert.equal(State.pageData.blocks[1].id,'callout');
 assert.equal(bridge.projection().blocks.find(row=>row.id==='child').props.rich_text_html,'<i>child</i>');
 bridge.stopCapturing();assert.equal(bridge.undo(),true);assert.deepEqual(State.pageData.blocks,rows);
 assert.equal(bridge.redo(),true);assert.equal(parents(State.pageData.blocks).callout,State.pageData.blocks[0].id);
});

test('UI-TABS plus insertion above an owned block keeps both siblings under their owner',t=>{
 const rows=[b('owner','callout',{children:['anchor','tail']}),b('anchor','paragraph',{layout_group:'columns',layout_column:1}),b('tail')];
 const {ctx,State,bridge,notes}=harness(t,rows);bridge.stopCapturing();ctx.createTabsBlock(1,false,true);
 assert.deepEqual(notes,[]);
 const controller=State.pageData.blocks.find(row=>row.type==='tabs'),map=parents(State.pageData.blocks);
 assert.ok(controller);assert.equal(map[controller.id],'owner');assert.equal(map.anchor,'owner');
 assert.ok(State.pageData.blocks.findIndex(row=>row.id===controller.id)<State.pageData.blocks.findIndex(row=>row.id==='anchor'));
 assert.equal(controller.props.layout_column,1);
 bridge.stopCapturing();assert.equal(bridge.undo(),true);assert.deepEqual(State.pageData.blocks,rows);
});

test('UI-TABS actual multi-selection wraps selected roots once and preserves their descendants',t=>{
 const rows=[b('parent','callout',{children:['child']}),b('child'),b('sibling')],{ctx,State}=harness(t,rows);
 ctx.selected=[0,1,2];ctx.createTabsBlock(0,true);
 assert.equal(State.pageData.blocks.filter(row=>row.type==='tabs').length,2);assert.equal(parents(State.pageData.blocks).child,'parent');
 assert.equal(new Set(State.pageData.blocks.map(row=>row.id)).size,State.pageData.blocks.length);
});

test('UI-TABS actual add follows the current controller ID after its position changes',t=>{
 const {ctx,State,notes}=harness(t,source()),stale=structuredClone(State.pageData.blocks[1]);
 State.pageData.blocks.unshift(b('remote'));ctx.addTabsBlockTab(stale,1);
 assert.deepEqual(notes,[]);
 const controller=State.pageData.blocks.find(row=>row.id==='tabs'),added=controller.props.tabs.at(-1);
 const member=State.pageData.blocks.find(row=>row.props.tab_group==='tabs'&&row.props.tab_id===added.id);
 assert.ok(member);assert.equal(parents(State.pageData.blocks)[member.id],'tabs');
});

test('UI-TABS actual delete is complete in Yjs and undo restores the complete original panel',t=>{
 const {ctx,State,bridge}=harness(t,source());bridge.stopCapturing();ctx.deleteTabsBlockTab(State.pageData.blocks[1],'a');
 assert.deepEqual(State.pageData.blocks.map(row=>row.id),['owner','tabs','other','third','tail']);
 assert.deepEqual(bridge.projection().blocks.map(row=>row.id),State.pageData.blocks.map(row=>row.id));
 bridge.stopCapturing();bridge.undo();assert.equal(State.pageData.blocks.find(row=>row.id==='deep').props.rich_text_html,'<b>deep</b>');
});

test('UI-TABS actual duplicate copies nested content and is read-only safe',t=>{
 const {ctx,State,bridge}=harness(t,source());ctx.editable=false;const before=structuredClone(State.pageData.blocks);
 ctx.addTabsBlockTab(State.pageData.blocks[1],1);ctx.deleteTabsBlockTab(State.pageData.blocks[1],'a');assert.equal(ctx.duplicateTabsBlockGroup(1),false);assert.deepEqual(State.pageData.blocks,before);
 ctx.editable=true;assert.equal(ctx.duplicateTabsBlockGroup(1),true);ctx.markDirty();
 assert.equal(bridge.projection().blocks.filter(row=>row.content==='deep').length,2);
});

test('UI-TABS open settings edits the current tab after projection and rejects a changed page',t=>{
 const {ctx,State,bridge,notes}=harness(t,source());ctx.openTabsBlockTabSettings(State.pageData.blocks[1],'a');
 State.pageData.blocks=structuredClone(State.pageData.blocks);ctx.rename='Renamed';ctx.buttons.find(button=>button.label==='저장').action();
 assert.equal(bridge.projection().blocks.find(row=>row.id==='tabs').props.tabs[0].name,'Renamed');
 ctx.openTabsBlockTabSettings(State.pageData.blocks[1],'b');State.activePageId='other';ctx.rename='Do not write';
 ctx.buttons.find(button=>button.label==='저장').action();assert.equal(State.pageData.blocks.find(row=>row.id==='tabs').props.tabs[1].name,'B');
 assert.ok(notes.at(-1).includes('탭이 변경'));
});

test('UI-TABS reopening waits for durable CRDT replay before rendering stale catalog ownership',t=>{
 const seed=[b('owner','callout',{children:['source']}),b('source','callout',{children:['child']}),b('child')];
 const writer=harness(t,seed),frames=[];
 writer.bridge.subscribe(event=>{if(event.type==='local-update')frames.push({kind:'crdt.update',payload:event.update});});
 // Capture a complete starting document, then the actual tab conversion update.
 frames.push({kind:'crdt.snapshot',payload:writer.bridge.encodeState()});
 writer.ctx.createTabsBlock(1,true);
 const final=writer.bridge.projection(),newIds=new Set(seed.map(row=>row.id));
 const catalog=[...structuredClone(seed),...final.blocks.filter(row=>!newIds.has(row.id))];
 assert.throws(()=>core.blockOutline(catalog),/shared block child/);
 const State={pageData:{blocks:catalog},liveCollaboration:{crdtReady:false}},root={textContent:'old page',attributes:{},setAttribute(k,v){this.attributes[k]=v;},removeAttribute(k){delete this.attributes[k];}};
 let layouts=0;
 const ctx={State,$:()=>root,DatabaseViewSettingsOpen:null,DatabaseFileActionMenu:{},DatabaseFileAddMenu:{},
  pendingLinkedDatePicker:null,dismissDatePickerBeforeRedraw(){},deferDatabaseInputRender:()=>false,
  collaborativeEditingActive:()=>true,syncedTextHoldsFocus:()=>{core.blockOutline(State.pageData.blocks);layouts++;return true;},SyncedTextRenderDeferred:false};
 vm.createContext(ctx);
 const start=main.indexOf('function renderBlocks()'),end=main.indexOf('\nfunction ',start+1);
 vm.runInContext(main.slice(start,end),ctx);
 const host=core.createEditorHostAdapter({readDocument:()=>({page_id:'page',title:'Title',blocks:State.pageData.blocks,can_edit:true}),applyProjection:value=>{State.pageData.blocks=structuredClone(value.blocks);return {applied:true};}});
 const reader=CollaborationEditor.createEditorBridge({Y,host});
 t.after(()=>{reader.dispose();host.dispose();});reader.configureDocument(host.readDocument());
 ctx.renderBlocks();assert.equal(layouts,0);assert.equal(root.attributes['aria-busy'],'true');assert.deepEqual(State.pageData.blocks,catalog);
 reader.applyFrames(frames);reader.finishReplay();State.liveCollaboration.crdtReady=true;
 ctx.renderBlocks();assert.equal(layouts,1);assert.equal(root.attributes['aria-busy'],undefined);
 assert.deepEqual(State.pageData.blocks,final.blocks);assert.equal(parents(State.pageData.blocks).source,final.blocks[1].id);
});
