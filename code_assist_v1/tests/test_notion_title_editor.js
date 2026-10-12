"use strict";
const assert = require("node:assert/strict"), test = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const TitleEditor = require("../notion_static/notion_title_editor.js");
const EditorCore = require("../notion_static/notion_editor_core.js");
const {CollaborationEditor, loadPinnedYjs} = require("./notion_collaboration_test_runtime.js");
const Y = loadPinnedYjs();
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const copy = value => JSON.parse(JSON.stringify(value));

function fixture(t, title = "제목 앞 뒤쪽 내용") {
  const control = {value: title, selectionStart: 5, selectionEnd: 5, dataset: {}};
  const state = {activePageId: "title-page", pageData: {page: {id: "title-page", title, collaborative_editing: true, collaborative_block_history: true},
    blocks: [{id: "old-body", type: "paragraph", content: "기존 본문", props: {}},
      {id: "old-reference", type: "synced_block", content: "", props: {synced_from: {document_id: "origin", block_id: "source"}}}]},
    selectedBlockIds: new Set(["old-body"]), activeBlockId: "old-body"};
  let editable = true, focus = null;
  const host = EditorCore.createEditorHostAdapter({
    readDocument: () => ({page_id: state.activePageId, title: control.value, blocks: copy(state.pageData.blocks), can_edit: editable}),
    applyProjection: data => {control.value = data.title; state.pageData.page.title = data.title; state.pageData.blocks = copy(data.blocks); return {applied: true};},
    onListenerError(error) {throw error;},
  });
  const bridge = CollaborationEditor.createEditorBridge({Y, host});
  bridge.configureDocument(host.readDocument()); bridge.finishReplay();
  const events = []; bridge.subscribe(event => events.push(event));
  const context = {State: state, window: {NotionTitleEditor: TitleEditor}, EditorCore, CollaborativeEditorBridge: bridge,
    activePageCanEdit: () => editable, captureEditorHistory() {},
    clearBlockSelection() {state.selectedBlockIds.clear();}, syncPageTitleDisplays() {}, renderBreadcrumb() {}, renderBlocks() {},
    markDirty() {host.notifyLocalMutation("document");}, focusBlock(index, offset) {focus = {index, offset};},
    document: {activeElement: {closest: () => null}}, collaborativeDatabaseActive: () => false, setStatus() {},
    restoreEditorHostSelection(selection) {focus = selection;},
    $: selector => selector === "#pageTitle" ? control : {hidden: true},
    syncEditorBlock(editor, block) {block.content = editor.innerText;},
    selectionIsCollapsed: () => true, caretOffset: () => 0, finishComposingSlash() {}};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("function splitPageTitleIntoBody("), source.indexOf("function initEvents(")), context);
  vm.runInContext(source.slice(source.indexOf("function applyEditorHistory("), source.indexOf("function selectedBlockIndices(")), context);
  vm.runInContext(source.slice(source.indexOf("function handleBlockEditorKeydown("), source.indexOf("function linkPreviewHtml(")), context);
  t.after(() => {bridge.dispose(); host.dispose();});
  return {control, state, context, bridge, host, events, get focus() {return focus;}, set editable(value) {editable = value;}};
}

test("UI-TITLE-001 Enter splits at the caret and preserves existing blocks and references", t => {
  const f = fixture(t), before = copy(f.state.pageData.blocks);
  f.context.splitPageTitleIntoBody(f.control);
  assert.equal(f.control.value, "제목 앞 ");
  assert.equal(f.state.pageData.blocks[0].content, "뒤쪽 내용");
  assert.deepEqual(f.state.pageData.blocks.slice(1), before);
  assert.deepEqual(f.focus, {index: 0, offset: 0});
  assert.equal(f.state.selectedBlockIds.size, 0);
  assert.equal(f.events.filter(e => e.type === "local-update").length, 1, "title and new block share one Yjs transaction");
  assert.equal(f.bridge.projection().title, f.control.value);
});

function backspace(f, flags = {}, index = 0) {
  const block = f.state.pageData.blocks[index];
  let prevented = false;
  f.context.handleBlockEditorKeydown({key: "Backspace", ...flags, preventDefault() {prevented = true;}},
    {dataset: {}, innerText: block.content}, block, index);
  return prevented;
}

test("UI-TITLE-009 first paragraph Backspace joins title atomically and undo restores exact block identity", t => {
  const f = fixture(t, "원래 제목"), before = copy(f.state.pageData.blocks);
  assert.equal(backspace(f), true);
  assert.equal(f.control.value, "원래 제목기존 본문");
  assert.deepEqual(f.state.pageData.blocks, before.slice(1), "reference stays intact");
  assert.deepEqual(copy(f.focus), {kind: "title", start: 5, end: 5});
  assert.equal(f.events.filter(e => e.type === "local-update").length, 1);
  f.context.handlePageTitleKeydown({target: f.control, key: "z", ctrlKey: true, preventDefault() {}});
  assert.equal(f.control.value, "원래 제목");
  assert.deepEqual(f.state.pageData.blocks, before);
  assert.deepEqual(f.focus, {index: 0, offset: 0});
  f.context.applyEditorHistory(1, {blockIndex: 0, offset: 0});
  assert.equal(f.control.value, "원래 제목기존 본문");
  assert.deepEqual(copy(f.focus), {kind: "title", start: 5, end: 5});
});

test("UI-TITLE-010 merge is isolated from typing before and after the boundary edit", t => {
  const f = fixture(t);
  f.state.pageData.blocks[0].content = "병합 전 입력";
  f.host.notifyLocalMutation("document");
  backspace(f);
  const merged = f.control.value;
  f.control.value += "추가 입력"; f.host.notifyLocalMutation("document");
  assert.equal(f.bridge.undo(), true); assert.equal(f.control.value, merged);
  assert.equal(f.bridge.undo(), true);
  assert.equal(f.state.pageData.blocks[0].content, "병합 전 입력");
  assert.equal(f.bridge.undo(), true);
  assert.equal(f.state.pageData.blocks[0].content, "기존 본문");
});

test("UI-TITLE-011 structured and nested content, IME, modifier keys and noncollapsed selections cannot join title", t => {
  for (const patch of [{type: "synced_block"}, {type: "heading1"}, {type: "table"},
    {props: {indent: 1}}, {props: {layout_group: "columns"}}, {props: {tab_group: "tabs"}},
    {props: {synced_from: {document_id: "origin", block_id: "source"}}}]) {
    const f = fixture(t); Object.assign(f.state.pageData.blocks[0], patch);
    const before = copy(f.state.pageData);
    assert.equal(backspace(f), false);
    assert.deepEqual(copy(f.state.pageData), before);
  }
  const parent = fixture(t); parent.state.pageData.blocks[1].props.indent = 1;
  assert.equal(backspace(parent), false, "a paragraph with descendants must keep its identity");
  for (const flags of [{isComposing: true}, {keyCode: 229}, {ctrlKey: true}, {altKey: true}, {shiftKey: true}]) {
    const f = fixture(t); assert.equal(backspace(f, flags), false); assert.equal(f.events.length, 0);
  }
  const selected = fixture(t); selected.context.selectionIsCollapsed = () => false;
  assert.equal(backspace(selected), false);
  const middle = fixture(t); middle.context.caretOffset = () => 2;
  assert.equal(backspace(middle), false);
  const locked = fixture(t); locked.editable = false;
  assert.equal(backspace(locked), false);
  const oldServer = fixture(t); delete oldServer.state.pageData.page.collaborative_block_history;
  assert.equal(backspace(oldServer), false, "new merge must wait for structural history support");
});

test("UI-TITLE-012 a sole paragraph leaves a saved empty editing row and restores without duplicates", t => {
  const f = fixture(t, "");
  f.state.pageData.blocks.splice(1); f.host.notifyLocalMutation("document"); f.bridge.stopCapturing();
  const before = copy(f.state.pageData.blocks);
  backspace(f);
  assert.equal(f.control.value, "기존 본문");
  assert.equal(f.state.pageData.blocks.length, 1);
  assert.equal(f.state.pageData.blocks[0].content, "");
  assert.notEqual(f.state.pageData.blocks[0].id, before[0].id);
  assert.deepEqual(copy(f.bridge.projection().blocks), f.state.pageData.blocks);
  f.context.applyEditorHistory(-1, {title: true});
  assert.equal(f.control.value, ""); assert.deepEqual(f.state.pageData.blocks, before);
});

test("UI-TITLE-013 split then join can undo/redo across the same block without confusing focus", t => {
  const f = fixture(t), before = copy(f.state.pageData.blocks), originalTitle = f.control.value;
  f.context.splitPageTitleIntoBody(f.control);
  backspace(f);
  assert.equal(f.control.value, originalTitle);
  f.context.applyEditorHistory(-1, {title: true});
  assert.deepEqual(f.focus, {index: 0, offset: 0});
  f.context.applyEditorHistory(-1, {blockIndex: 0});
  assert.deepEqual(copy(f.focus), {kind: "title", start: 5, end: 5});
  assert.deepEqual(f.state.pageData.blocks, before);
  f.context.applyEditorHistory(1, {title: true});
  assert.deepEqual(f.focus, {index: 0, offset: 0});
  f.context.applyEditorHistory(1, {blockIndex: 0});
  assert.deepEqual(copy(f.focus), {kind: "title", start: 5, end: 5});
  assert.equal(f.control.value, originalTitle); assert.deepEqual(f.state.pageData.blocks, before);
});

test("UI-TITLE-002 end, start and selected-range Enter preserve exactly the unselected text", t => {
  for (const [start, end, title, body] of [[4, 4, "A😀B", ""], [0, 0, "", "A😀B"], [1, 3, "A", "B"]]) {
    const f = fixture(t, "A😀B");
    f.control.selectionStart = start; f.control.selectionEnd = end;
    f.context.splitPageTitleIntoBody(f.control);
    assert.equal(f.control.value, title);
    assert.equal(f.state.pageData.blocks[0].content, body);
    assert.equal(f.state.pageData.blocks[0].type, "paragraph");
  }
});

test("UI-TITLE-003 one undo restores the title and removes only its new block, independently of immediate typing", t => {
  const f = fixture(t, "원래 제목"), before = copy(f.state.pageData.blocks);
  f.control.value = "제목 앞 뒤쪽 내용"; f.host.notifyLocalMutation("document");
  f.context.splitPageTitleIntoBody(f.control);
  const split = copy(f.bridge.projection());
  f.state.pageData.blocks[0].content = "새 본문 입력"; f.host.notifyLocalMutation("document");
  assert.equal(f.bridge.undo(), true);
  assert.equal(f.state.pageData.blocks[0].content, split.blocks[0].content, "subsequent typing is its own undo group");
  assert.equal(f.bridge.undo(), true);
  assert.equal(f.control.value, "제목 앞 뒤쪽 내용", "preceding title typing is retained");
  assert.deepEqual(f.state.pageData.blocks, before);
  assert.equal(f.bridge.redo(), true);
  assert.deepEqual(copy(f.bridge.projection()), split);
});

test("UI-TITLE-004 IME composition and denied edit cannot split the title", t => {
  for (const flag of [{isComposing: true}, {keyCode: 229}, {dataset: {imeComposing: "true"}}]) {
    const f = fixture(t), before = copy(f.bridge.projection());
    if (flag.dataset) f.control.dataset = flag.dataset;
    let prevented = false;
    f.context.handlePageTitleKeydown({target: f.control, key: "Enter", ...flag, preventDefault() {prevented = true;}});
    assert.equal(prevented, false);
    assert.deepEqual(copy(f.bridge.projection()), before);
  }
  const denied = fixture(t); denied.editable = false;
  assert.equal(denied.context.splitPageTitleIntoBody(denied.control), false);
  assert.equal(denied.events.length, 0);
});

test("UI-TITLE-005 Shift+Enter does not insert a title line break or change body", t => {
  const f = fixture(t); let prevented = false;
  f.context.handlePageTitleKeydown({target: f.control, key: "Enter", shiftKey: true, preventDefault() {prevented = true;}});
  assert.equal(prevented, true);
  assert.equal(f.events.length, 0);
  assert.equal(f.focus, null);
});

test("UI-TITLE-006 wrapping follows width changes without editing values or looping on height", () => {
  let observerCallback, fits = 0, width = 280, height = 192;
  const control = {tagName: "TEXTAREA", value: "한글 제목 ".repeat(20), selectionStart: 4, selectionEnd: 7,
    isConnected: true, style: {}, getClientRects: () => [1], getBoundingClientRect: () => ({width}),
    offsetHeight: 48, clientHeight: 48, get scrollHeight() {fits++; return height;}};
  const unobserved = [];
  const context = {ResizeObserver: class {constructor(callback) {observerCallback = callback;} observe() {} unobserve(c) {unobserved.push(c);}}};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../notion_static/notion_title_editor.js"), "utf8"), context);
  context.NotionTitleEditor.watch(control);
  assert.equal(control.style.height, "192px");
  observerCallback([{target: control}]);
  assert.equal(fits, 1, "height notification with unchanged width must not cause another write");
  width = 720; height = 96;
  observerCallback([{target: control}]);
  assert.equal(control.style.height, "96px");
  assert.equal(control.value, "한글 제목 ".repeat(20));
  assert.equal(control.selectionStart, 4); assert.equal(control.selectionEnd, 7);
  control.isConnected = false;
  observerCallback([{target: control}]);
  assert.deepEqual(unobserved, [control]);
});

test("UI-TITLE-007 undo returns to the original title caret and redo focuses the new block", t => {
  for (const offset of [5, 11]) {
    const f = fixture(t);
    f.control.selectionStart = f.control.selectionEnd = Math.min(offset, f.control.value.length);
    const expected = f.control.selectionStart;
    f.context.splitPageTitleIntoBody(f.control);
    f.context.applyEditorHistory(-1, {blockIndex: 0, offset: 0});
    assert.deepEqual(copy(f.focus), {kind: "title", start: expected, end: expected});
    let prevented = false;
    f.context.handlePageTitleKeydown({target: f.control, key: "y", ctrlKey: true, preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.deepEqual(f.focus, {index: 0, offset: 0});
  }
});

test("UI-TITLE-008 ordinary history preserves an empty split title and restores its navigation label", t => {
  const f = fixture(t), labels = [];
  f.state.pageData.blocks = f.state.pageData.blocks.map(EditorCore.normalizeBlock);
  const before = copy(f.state.pageData.blocks);
  f.state.pageData.page.collaborative_editing = false;
  f.state.history = {entries: [], index: -1};
  Object.assign(f.context, {$: () => f.control, clearTimeout() {}, setTimeout(fn) {fn(); return 0;}, scheduleSave() {},
    editorSnapshot: () => ({title: f.control.value, blocks: copy(f.state.pageData.blocks)}),
    syncPageTitleDisplays(id, title) {labels.push(title);}});
  vm.runInContext(source.slice(source.indexOf("function captureEditorHistory("), source.indexOf("function applyEditorHistory(")), f.context);
  f.context.markDirty = () => {f.state.dirty = true; f.context.captureEditorHistory(true);};
  f.context.captureEditorHistory(true);
  f.control.selectionStart = f.control.selectionEnd = 0;
  f.context.splitPageTitleIntoBody(f.control);
  assert.equal(f.control.value, "");
  f.context.applyEditorHistory(-1, {blockIndex: 0, offset: 0});
  assert.equal(f.control.value, "제목 앞 뒤쪽 내용");
  assert.equal(labels.at(-1), f.control.value);
  assert.deepEqual(copy(f.focus), {kind: "title", start: 0, end: 0});
  assert.deepEqual(f.state.pageData.blocks, before);
  f.context.applyEditorHistory(1, {title: true, offset: 0});
  assert.equal(f.control.value, "");
  assert.equal(f.state.pageData.page.title, "");
  assert.equal(labels.at(-1), "");
  assert.equal(f.state.pageData.blocks[0].content, "제목 앞 뒤쪽 내용");
});

test("UI-TITLE-014 ordinary history restores a joined paragraph and title exactly", t => {
  const f = fixture(t, "");
  f.state.pageData.blocks = f.state.pageData.blocks.map(EditorCore.normalizeBlock);
  const before = copy(f.state.pageData.blocks);
  f.state.pageData.page.collaborative_editing = false;
  f.state.history = {entries: [], index: -1};
  Object.assign(f.context, {clearTimeout() {}, setTimeout(fn) {fn(); return 0;}, scheduleSave() {},
    editorSnapshot: () => ({title: f.control.value, blocks: copy(f.state.pageData.blocks)})});
  vm.runInContext(source.slice(source.indexOf("function captureEditorHistory("), source.indexOf("function applyEditorHistory(")), f.context);
  f.context.markDirty = () => {f.state.dirty = true;};
  f.context.captureEditorHistory(true);
  assert.equal(backspace(f), true);
  assert.equal(f.control.value, "기존 본문");
  f.context.applyEditorHistory(-1, {title: true, offset: 0});
  assert.equal(f.control.value, ""); assert.deepEqual(f.state.pageData.blocks, before);
  assert.deepEqual(f.focus, {index: 0, offset: 0});
  f.context.applyEditorHistory(1, {blockIndex: 0, offset: 0});
  assert.equal(f.control.value, "기존 본문");
  assert.deepEqual(copy(f.focus), {kind: "title", start: 0, end: 0});
});
