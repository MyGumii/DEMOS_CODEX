'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const synced = require('../notion_static/notion_synced_references.js');
const core = require('../notion_static/notion_editor_core.js');
const main = fs.readFileSync(require.resolve('../notion_static/notion.js'), 'utf8');
const extract = name => { const start = main.indexOf(`function ${name}(`); assert.ok(start >= 0); return main.slice(start, main.indexOf('\nfunction ', start + 10)); };
const clone = value => JSON.parse(JSON.stringify(value));
const text = () => ({id:'source',type:'paragraph',content:'굵은 원본',props:{rich_text_html:'<strong>굵은 원본</strong>',layout_group:'columns',layout_column:1,layout_columns:2,text_color:'red',background_color:'yellow'}});
function harness(blocks=[text()], collaborative=true) {
  const notices=[], state={activePageId:'page',pageData:{page:{collaborative_synced_read:collaborative},blocks:clone(blocks)},selectedBlockIds:new Set()};
  const context={State:state,EditorCore:core,window:{NotionSyncedReferences:synced,NotionBlockStructure:require('../notion_static/notion_block_structure')},structuredClone,
    NON_ADOPTED_BLOCK_TYPES:new Set(),DATABASE_BLOCK_TYPES:new Set(),selectedBlockIndices:index=>[index??0],
    selectionContainsDatabase:()=>false,databaseStructureMutationAllowed:()=>true,markDirty:()=>{context.dirty++;},dirty:0,
    captureEditorHistory:()=>{},clearBlockSelection:()=>{},closeBlockMenu:()=>{},renderBlocks:()=>{},focusBlock:()=>{},activePageCanEdit:()=>true,
    setStatus:message=>notices.push(message),normalizeTabsBlock:block=>block.props.tabs,newTabId:()=>`tab-${++context.ordinal}`,ordinal:0};
  vm.createContext(context);
  for(const name of ['newBlock','copyEditorBlock','duplicateSelectedEditorBlocks','changeBlockType','duplicateTabsBlockGroup','handleBlockAction']) vm.runInContext(extract(name),context);
  return {context,state,notices};
}

test('UI-SYNC-CREATE new collaborative blocks declare originals; ordinary/other side-page creation stays legacy',()=>{
  const h=harness();const block=h.context.newBlock('synced_block');
  assert.equal(block.props.synced_from,null);assert.equal(block.props.sync_id,undefined);
  const ordinary=h.context.newBlock('synced_block',{collaborative_synced_read:false});
  assert.equal(Object.hasOwn(ordinary.props,'synced_from'),false);assert.ok(ordinary.props.sync_id);
  const saved=core.normalizeBlock(block);assert.equal(saved.props.synced_from,null);assert.equal(saved.props.sync_id,undefined);
});

test('UI-SYNC-CREATE actual conversion preserves current rich text and declares explicit original',()=>{
  const h=harness();h.context.changeBlockType(0,'synced_block');
  assert.equal(h.state.pageData.blocks[0].props.synced_from,null);
  assert.equal(h.state.pageData.blocks[0].props.rich_text_html,'<strong>굵은 원본</strong>');
  assert.equal(h.state.pageData.blocks[0].content,'굵은 원본');assert.equal(h.context.dirty,1);
  assert.equal(h.state.pageData.blocks[0].props.text_color,'red');assert.equal(h.state.pageData.blocks[0].props.background_color,'yellow');
  const before=clone(h.state.pageData.blocks);h.context.changeBlockType(0,'paragraph');
  assert.deepEqual(clone(h.state.pageData.blocks),before);assert.equal(h.context.dirty,1);assert.equal(h.notices.length,1);
});

test('UI-SYNC-CREATE actual sync-copy creates one original and an empty reference without editing source text',()=>{
  const h=harness();h.context.handleBlockAction('sync_copy',0);
  const [source,copy]=h.state.pageData.blocks;
  assert.equal(source.id,'source');assert.equal(source.props.synced_from,null);
  assert.equal(source.props.rich_text_html,text().props.rich_text_html);
  assert.equal(copy.content,'');assert.equal(copy.props.rich_text_html,undefined);assert.equal(copy.props.children,undefined);
  assert.deepEqual(copy.props.synced_from,{document_id:'page',block_id:'source'});
  assert.equal(copy.props.layout_group,source.props.layout_group);assert.equal(copy.props.layout_column,1);
  assert.equal(copy.props.text_color,'red');assert.equal(copy.props.background_color,'yellow');
  assert.equal(copy.props.sync_id,undefined);assert.equal(h.context.dirty,1);
});

test('UI-SYNC-CREATE block duplicate preserves original identity; reference copies do not chain',()=>{
  const source=synced.original(text()),h=harness([source]);h.context.handleBlockAction('duplicate',0);
  const first=h.state.pageData.blocks[1];assert.deepEqual(first.props.synced_from,{document_id:'page',block_id:'source'});
  assert.deepEqual(h.state.pageData.blocks[0],source);
  h.context.handleBlockAction('sync_copy',1);
  const second=h.state.pageData.blocks[2];assert.notEqual(second.id,first.id);
  assert.deepEqual(second.props.synced_from,first.props.synced_from);assert.equal(second.content,'');
  assert.equal(h.context.duplicateSelectedEditorBlocks([0,1,2]),true);
  assert.equal(h.state.pageData.blocks.length,6);
  for(const item of h.state.pageData.blocks.slice(3))assert.deepEqual(item.props.synced_from,first.props.synced_from);
});

test('UI-SYNC-CREATE external instance and tab duplication preserve original endpoints and new tab layout',()=>{
  const external={id:'external',type:'synced_block',content:'',props:{synced_from:{document_id:'other',block_id:'original'},tab_group:'tabs',tab_id:'a'}};
  const tabs={id:'tabs',type:'tabs',content:'',props:{tabs:[{id:'a',name:'하나'}],active_tab_id:'a'}};
  const h=harness([tabs,external]);h.context.handleBlockAction('duplicate',0);
  const [, ,newTabs,copy]=h.state.pageData.blocks;
  assert.deepEqual(copy.props.synced_from,external.props.synced_from);
  assert.equal(copy.props.tab_group,newTabs.id);assert.equal(copy.props.tab_id,newTabs.props.tabs[0].id);
  assert.equal(copy.content,'');
});

test('UI-SYNC-CREATE malformed reference fails before mutating source or adding blocks',()=>{
  for(const source of [
    {...text(),type:'synced_block',props:{synced_from:{document_id:'other',block_id:'original'}}},
    {...text(),type:'synced_block',content:'',props:{synced_from:{document_id:'',block_id:'original'}}},
    {...text(),type:'synced_block',content:'',props:{synced_from:{document_id:'page',block_id:'source'}}},
  ])for(const action of ['sync_copy','duplicate']){
    const h=harness([source]),before=clone(h.state.pageData.blocks);h.context.handleBlockAction(action,0);
    assert.deepEqual(h.state.pageData.blocks,before);assert.equal(h.context.dirty,0);assert.equal(h.notices.length,1);
  }
});

test('UI-SYNC-CREATE existing legacy groups keep compatibility without inventing an original',()=>{
  const source={...text(),type:'synced_block',props:{sync_id:'legacy-group'}};
  assert.throws(()=>synced.original(source),/원본 위치/);
  for(const action of ['sync_copy','duplicate']) {
    const h=harness([source]);h.context.handleBlockAction(action,0);
    const copy=h.state.pageData.blocks[1];assert.equal(copy.content,source.content);
    assert.equal(copy.props.sync_id,'legacy-group');assert.equal(Object.hasOwn(copy.props,'synced_from'),false);
    assert.equal(h.notices.length,0);
  }
  const converted=harness([source]);converted.context.changeBlockType(0,'paragraph');
  assert.equal(converted.state.pageData.blocks[0].type,'paragraph');assert.equal(converted.notices.length,0);
});

test('UI-SYNC-CREATE ordinary sync-copy keeps its existing same-group compatibility behavior',()=>{
  const h=harness([text()],false);h.context.handleBlockAction('sync_copy',0);
  const [source,copy]=h.state.pageData.blocks;
  assert.equal(copy.content,source.content);assert.equal(copy.props.sync_id,source.props.sync_id);
  assert.equal(Object.hasOwn(source.props,'synced_from'),false);
});
