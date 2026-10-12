"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const page = {id: "page", can_edit: true, settings: {}};
const State = {activePageId: "page", pageData: {page: {id: "page", collaborative_editing: true},
  blocks: [{id: "grid", props: {database_id: "database"}}],
  databases: [{id: "database", capabilities: {atomic_cell_batch: true}}]}, history: {index: 0}};
let databaseActive = false, linked = false, bodyUndo = false, captured = 0;
const context = {State, pageIsLocked: item => !!item.settings?.locked,
  collaborativeDatabaseActive: () => databaseActive,
  window: {NotionLinkedDatabases: {reference: () => linked}},
  LinkedDatabaseCells: null, DatabaseRangeController: null,
  linkedDatabaseScope: () => ({pageId: "page"}),
  CollaborativeEditorBridge: {descriptor: () => ({page_id: "page", ready: true, can_undo: bodyUndo})},
  captureEditorHistory: () => {captured++;}};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("function pageMenuCanUndo("), source.indexOf("function openPageMenu(")), context);

assert.equal(context.pageMenuCanUndo(page), false, "an untouched collaborative page hides undo");
bodyUndo = true;
assert.equal(context.pageMenuCanUndo(page), true, "a local text edit exposes undo");
assert.equal(context.pageMenuCanUndo({...page, id: "other"}), false, "another page cannot borrow local history");
assert.equal(context.pageMenuCanUndo({...page, settings: {locked: true}}), false, "a locked page cannot undo");
assert.equal(context.pageMenuCanUndo({...page, can_edit: false}), false, "a reader cannot undo");

databaseActive = true;
assert.equal(context.pageMenuCanUndo(page, "grid"), false, "body history cannot masquerade as focused cell history");
context.DatabaseRangeController = {canUndo: id => id === "database"};
assert.equal(context.pageMenuCanUndo(page, "grid"), true, "a committed cell edit exposes undo");
assert.equal(context.pageMenuCanUndo(page, "missing"), false, "a removed grid cannot reuse stale history");

linked = true;
context.LinkedDatabaseCells = {status: (_, id) => ({canUndo: id === "grid"})};
assert.equal(context.pageMenuCanUndo(page, "grid"), true, "a linked cell edit exposes its own history");
context.LinkedDatabaseCells = null;
assert.equal(context.pageMenuCanUndo(page, "grid"), false, "an unloaded linked controller hides undo");

databaseActive = false;
State.pageData.page.collaborative_editing = false;
assert.equal(context.pageMenuCanUndo(page), false, "legacy baseline snapshot has no undo step");
State.history.index = 1;
assert.equal(context.pageMenuCanUndo(page), true, "legacy local snapshot exposes undo");
assert.equal(captured, 2, "legacy history is flushed before testing visibility");
console.log("UI-PAGE-MENU-UNDO: availability follows the active page and focused history");
