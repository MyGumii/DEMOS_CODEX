"use strict";
const assert = require("node:assert/strict"), test = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const EditorCore = require("../notion_static/notion_editor_core.js");
const {CollaborationEditor, loadPinnedYjs} = require("./notion_collaboration_test_runtime.js");
const Y = loadPinnedYjs(), source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));

function fixture(t, blocks) {
  let editable = true, focus, menu, context;
  const statuses=[];
  const state = {activePageId: "insert-page", selectedBlockIds: new Set([blocks[0].id]),
    pageData: {page: {title: "페이지 제목"}, blocks: copy(blocks)}};
  const host = EditorCore.createEditorHostAdapter({
    readDocument: () => ({page_id: state.activePageId, title: state.pageData.page.title, blocks: copy(state.pageData.blocks), can_edit: editable}),
    applyProjection: projection => {state.pageData.blocks = copy(projection.blocks); return {applied: true};},
    captureSelection: () => context?.captureEditorHostSelection(),
    restoreSelection: selection => context?.restoreEditorHostSelection(selection),
    onListenerError(error) {throw error;},
  });
  const bridge = CollaborationEditor.createEditorBridge({Y, host});
  bridge.configureDocument(host.readDocument()); bridge.finishReplay();
  const events = []; bridge.subscribe(event => events.push(event));
  context = {State: state, EditorCore, CollaborativeEditorBridge: bridge,
    window:{NotionBlockStructure:require('../notion_static/notion_block_structure')},
    setStatus:(message,type)=>{statuses.push([message,type]);if(type!=='error')assert.fail(message);},
    activePageCanEdit: () => editable, captureEditorHistory() {},
    markDirty: () => host.notifyLocalMutation("document"), renderBlocks() {},
    focusBlock(index, offset) {focus = {index, offset};},
    document: {querySelector: () => ({})},
    openBlockMenu(index, anchor, mode) {
      menu = {index, mode};
      state.insertedBlockMenu={pageId:state.activePageId,blockId:state.pageData.blocks[index].id};
    },
    closeBlockMenu() {menu = null; state.slashToken = null; state.insertedBlockMenu = null;},
    $: () => ({}),
    NON_ADOPTED_BLOCK_TYPES: new Set(), DATABASE_BLOCK_TYPES: new Set(),
    changeBlockType(index, type) {state.pageData.blocks[index].type = type; host.notifyLocalMutation("document");},
    setTimeout, clearTimeout};
  vm.createContext(context);
  for (const [start, end] of [["function insertSimpleBlockFromControl(", "async function insertChildPageAfter("],
    ["function captureEditorHostSelection(", "function focusEditorHostBlock("],
    ["function chooseBlockType(", "function slashPreferences("]]) {
    vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
  }
  t.after(() => {bridge.dispose(); host.dispose();});
  return {state, context, bridge, host, events, statuses, get focus() {return focus;}, get menu() {return menu;}, set editable(value) {editable = value;}};
}

test("UI-INSERT-001 plus creates a blank before opening its menu; Escape keeps it and undo removes it", t => {
  const original = [{id: "first", type: "paragraph", content: "원본", props: {}},
    {id: "second", type: "paragraph", content: "다음", props: {}}];
  const f = fixture(t, original);
  f.state.pageData.blocks[0].content = "기존 입력 보존"; f.host.notifyLocalMutation("document");
  const beforeUpdates=f.events.filter(e=>e.type==='local-update').length;
  f.context.insertBlockFromControl(0, true);
  const inserted=f.state.pageData.blocks[0];
  assert.equal(inserted.type,'paragraph'); assert.equal(inserted.content,'');
  assert.deepEqual(f.state.pageData.blocks.slice(1), [{...original[0],content:'기존 입력 보존'},original[1]]);
  assert.deepEqual(f.focus, {index:0,offset:0});
  assert.deepEqual(f.menu, {index: 0, mode: "inserted"});
  f.context.closeBlockMenu();
  assert.equal(f.state.pageData.blocks[0].id,inserted.id);
  assert.equal(f.events.filter(e=>e.type==='local-update').length,beforeUpdates+1);
  assert.equal(f.bridge.undo(), true);
  assert.deepEqual(f.state.pageData.blocks, [{...original[0],content:'기존 입력 보존'},original[1]]);
  assert.equal(f.bridge.undo(), true);
  assert.deepEqual(f.state.pageData.blocks, original);
  assert.equal(f.bridge.redo(), true);
  assert.deepEqual(f.state.pageData.blocks, [{...original[0],content:'기존 입력 보존'},original[1]]);
  assert.equal(f.bridge.redo(), true);
  assert.equal(f.state.pageData.blocks[0].id,inserted.id);
  assert.equal(f.state.pageData.page.title, "페이지 제목");
});

test('UI-INSERT-ASYNC slash removal is in Yjs before native child creation or the file chooser can replay remote edits',t=>{
  for(const type of ['child_page','file']){
    const f=fixture(t,[{id:'source',type:'paragraph',content:'/페이지',props:{}}]);
    const editor={textContent:'/페이지'};
    f.state.slashToken={blockId:'source',start:0,end:4,text:'/페이지'};
    f.context.document={querySelector:()=>editor,createRange:()=>({setStart(){},setEnd(){},deleteContents(){editor.textContent='';}})};
    f.context.textPointAtOffset=(_,offset)=>({node:editor,offset});
    f.context.syncEditorBlock=(node,block)=>{block.content=node.textContent;};
    let checked=false;
    const verify=()=>{assert.equal(f.bridge.projection().blocks[0].content,'');checked=true;};
    f.context.insertChildPageAfter=verify;f.context.$=()=>({click:verify});
    f.context.chooseBlockType(type,0,'slash');assert.equal(checked,true);
    assert.equal(f.state.pageData.blocks[0].id,'source');
  }
});

test("UI-INSERT-002 inserting above or below a nested block retains its own column, tab and indent", t => {
  const structural = {layout_group: "group", layout_column: 1, layout_columns: 2, tab_group: "tabs", tab_id: "tab", indent: 2};
  const original = [{id: "other-column", type: "paragraph", content: "이전 열", props: {layout_column: 0}},
    {id: "target", type: "toggle", content: "부모", props: {...structural, rich_text_html: "<b>부모</b>", checked: true}},
    {id: "nested", type: "paragraph", content: "하위", props: {...structural, indent: 3}},
    {id: "sibling", type: "paragraph", content: "다음", props: structural}];
  for (const before of [true, false]) {
    const f = fixture(t, original); f.context.insertBlockFromControl(1, before);
    const at = before ? 1 : 3, blank = f.state.pageData.blocks[at];
    assert.equal(blank.type,'paragraph'); assert.equal(blank.content,'');
    assert.deepEqual(copy(blank.props), structural);
    f.context.chooseBlockType("heading1", 1, "inserted");
    const added = f.state.pageData.blocks[at];
    assert.equal(added.id,blank.id);
    assert.deepEqual(copy(added.props), structural);
    const expected=original.map(block=>block.id==='target'?{...block,props:{...block.props,children:['nested']}}:block);
    assert.deepEqual(f.state.pageData.blocks.filter(b => b.id !== added.id), expected, 'existing content and properties survive with inferred ownership made explicit');
    assert.equal(f.state.pageData.blocks[at].type, "heading1");
    assert.deepEqual(copy(f.state.pageData.blocks[at].props), structural, "menu conversion preserves nesting");
    assert.equal(f.state.pageData.blocks.length, original.length + 1, "only the chosen block is inserted");
  }
});

test("UI-INSERT-003 readonly and stale controls do not modify the document", t => {
  const original = [{id: "one", type: "paragraph", content: "그대로", props: {}}];
  const f = fixture(t, original); f.editable = false;
  f.context.insertBlockFromControl(0, true); f.editable = true;
  for (const index of [-1, 999, "0", 0.5]) f.context.insertBlockFromControl(index, false);
  assert.deepEqual(f.state.pageData.blocks, original); assert.equal(f.menu, undefined);
});

test("UI-INSERT-004 menu search focus remains stable after the new blank is rendered", t => {
  const f = fixture(t, [{id: "original", type: "paragraph", content: "본문", props: {}}]);
  f.context.insertBlockFromControl(0, true);
  f.context.document.activeElement = {closest: selector => selector.includes("#blockMenu") ? {} : null};
  f.host.applyRemoteProjection({...f.host.readDocument(), blocks: copy(f.state.pageData.blocks)});
  assert.deepEqual(f.focus,{index:0,offset:0});
  assert.deepEqual(copy(f.context.captureEditorHostSelection()), {kind: "none"});
});

test("UI-INSERT-005 menu choice follows the inserted blank after remote insertion and refuses a deleted target", t => {
  const f = fixture(t, [{id: "original", type: "paragraph", content: "본문", props: {}}]);
  f.context.insertBlockFromControl(0, true);
  f.state.pageData.blocks.unshift({id: "remote", type: "paragraph", content: "원격", props: {}});
  f.context.chooseBlockType("heading1", 0, "inserted");
  assert.equal(f.state.pageData.blocks[0].type, "paragraph");
  assert.equal(f.state.pageData.blocks[1].type, "heading1");
  assert.equal(f.state.pageData.blocks[2].id, 'original');
  f.context.insertBlockFromControl(0, true);
  f.state.pageData.blocks.shift();
  const before = copy(f.state.pageData.blocks);
  f.context.chooseBlockType("heading1", 0, "inserted");
  assert.deepEqual(copy(f.state.pageData.blocks), before);
  assert.equal(f.menu,null);
});

test("UI-COPY-HTTP-001 unavailable collaborative duplication has no legacy request or draft flush", async () => {
  const statuses = [], state = {navigation: {collaborativeSharing: true}};
  const context = {State: state, setStatus: (...args) => statuses.push(args),
    pageCanManageLifecycle: page => Boolean(page?.can_edit),
    api: () => assert.fail("must not call legacy API"), flushPageDraft: () => assert.fail("must not modify draft")};
  vm.createContext(context);
  for (const [start, end] of [["function pageCanDuplicate(", "function clearPageDropIndicators("],
    ["async function duplicatePage(", "const PageRenameSessions ="]]) {
    vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), context);
  }
  const page = {id: "canonical", can_edit: true, role: "owner"};
  assert.equal(context.pageCanDuplicate(page), false);
  await context.duplicatePage(page.id); assert.equal(statuses.length, 1);
  state.navigation.collaborativeSharing = false;
  assert.equal(context.pageCanDuplicate(page), true);
  assert.equal(context.pageCanDuplicate({...page, can_edit: false}), false);
  assert.equal(context.pageCanDuplicate(undefined), false);
});
