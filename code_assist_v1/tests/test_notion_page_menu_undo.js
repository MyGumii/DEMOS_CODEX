"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const page = {id: "page", can_edit: true, collaborative_editing: true, settings: {}};
const state = {activePageId: page.id, pageData: {page, blocks: [
  {id: "grid", props: {database_id: "database"}}, {id: "paragraph", content: "Body"},
], databases: [{id: "database", capabilities: {atomic_cell_batch: true}}]}, lastHistoryGrid: null};
const calls = [];
const menu = {dataset: {pageId: page.id}, pageMenuHistoryGridBlockId: "grid", hidden: false};
const context = {
  State: state, console,
  $: () => menu,
  closePageMenu: () => {menu.hidden = true; menu.pageMenuHistoryGridBlockId = null;},
  activePageCanEdit: () => true,
  renderTopbarPageActions: () => {},
  applyEditorHistory: (delta, target) => calls.push({delta, target}),
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("async function handlePageMenuAction("),
  source.indexOf("function pageCanChangeSettings(")), context);

async function run() {
  await context.handlePageMenuAction("undo", page);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].delta, -1);
  assert.equal(calls[0].target.gridBlockId, "grid", "menu keeps the focused cell target before closing");
  assert.equal(menu.pageMenuHistoryGridBlockId, null, "closing clears the saved target");

  let gridUndos = 0, bodyUndos = 0;
  const editor = {
    State: state, console,
    document: {activeElement: {closest: () => null}},
    window: {NotionLinkedDatabases: {reference: () => false}},
    collaborativeDatabaseActive: () => true,
    databaseRangeHistory: () => ({undo: async id => {assert.equal(id, "database"); gridUndos += 1; return true;}}),
    CollaborativeEditorBridge: {undo: () => {bodyUndos += 1; return false;}},
    setStatus: () => {},
  };
  vm.createContext(editor);
  vm.runInContext(source.slice(source.indexOf("function rememberEditorHistoryFocus("),
    source.indexOf("function restoreTitleBoundaryHistoryFocus(")), editor);
  editor.applyEditorHistory(-1, calls[0].target);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(gridUndos, 1, "cell history is selected while the menu owns focus");
  assert.equal(bodyUndos, 0, "page body history stays intact");

  const row = {dataset: {blockId: "grid"}};
  editor.rememberEditorHistoryFocus({target: {closest: selector =>
    selector === "#blocks .block-row" ? row : selector === ".dynamic-table-grid" ? {} : null}});
  assert.equal(state.lastHistoryGrid.blockId, "grid");
  editor.rememberEditorHistoryFocus({target: {closest: selector =>
    selector === "#blocks .block-row" ? {dataset: {blockId: "paragraph"}} : null}});
  assert.equal(state.lastHistoryGrid, null, "focusing body text drops a stale cell target");
  editor.applyEditorHistory(-1);
  assert.equal(bodyUndos, 1, "ordinary page undo still uses body history");
  console.log("UI-PAGE-MENU-UNDO: cell and page histories remain separated across menu focus");
}
run().catch(error => {console.error(error); process.exitCode = 1;});
