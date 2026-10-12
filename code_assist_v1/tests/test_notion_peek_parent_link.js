"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const segment = source.slice(source.indexOf("function insertChildPageLinkInEditor("), source.indexOf("function bindHtmlEmbedBlock("));
const State = {activePageId: "parent", pageData: {blocks: []}};
let edits = 0, flushes = 0, fail = true, canEdit = true, composing = false, connections = 0, removed = 0;
const frame = {remove() {removed++;}};
const linked = new Set();
const connection = {
  async flush() { if (linked.size && fail) throw new Error("parent save pending"); },
  insertChildLink(page, parentId) { assert.equal(parentId, "parent"); linked.add(page.id); },
  dispose() {removed++;},
};
const context = {State, EditorHost: {isComposing: () => composing}, activePageCanEdit: () => canEdit,
  renderBlocks() {}, markDirty() {edits++;}, setSidePeekEditorStatus() {},
  flushMainEditorForSidePeek: async () => {flushes++; if (edits && fail) throw new Error("parent save pending");},
  document: {createElement: () => frame, body: {appendChild() {}}},
  window: {NotionEmbeddedEditorBridge: {connect: async (actualFrame, parentId, callbacks, options) => {
    connections++; assert.equal(actualFrame, frame); assert.equal(parentId, "parent");
    assert.equal(options.workspaceId, "workspace"); assert.equal(options.userId, "owner");
    assert.equal(actualFrame.hidden, true); return connection;
  }}},
};
vm.createContext(context); vm.runInContext(segment, context);
const holder = () => ({draft: {context: {parentId: "parent", collaborative: true, workspaceId: "workspace", userId: "owner"}}});
const page = {id: "child", title: "하위 문서"};
(async () => {
  const canonical = {...holder(), parentLinkSaved: true};
  await context.ensureDraftParentLink(canonical, page);
  assert.equal(flushes, 0); assert.equal(edits, 0); assert.equal(connections, 0,
    "a server-saved parent link must not open an editor or insert again");
  const h = holder();
  await assert.rejects(context.ensureDraftParentLink(h, page), /pending/);
  assert.equal(h.parentLinkSaved, undefined); assert.equal(State.pageData.blocks.length, 1);
  fail = false; await context.ensureDraftParentLink(h, page);
  assert.equal(h.parentLinkSaved, true); assert.equal(edits, 1, "save retry does not add another local CRDT block");
  assert.equal(State.pageData.blocks[0].props.page_id, "child");
  await context.ensureDraftParentLink(h, page); assert.equal(edits, 1);
  canEdit = false;
  assert.throws(() => context.insertChildPageLinkInEditor({id: "denied"}, "parent"), /편집 준비/);
  canEdit = true; composing = true;
  assert.throws(() => context.insertChildPageLinkInEditor({id: "ime"}, "parent"), /편집 준비/);
  composing = false;
  assert.throws(() => context.insertChildPageLinkInEditor(page, "other"), /편집 준비/);

  State.activePageId = "different-page"; fail = true;
  const background = holder();
  await assert.rejects(context.ensureDraftParentLink(background, page), /pending/);
  assert.equal(connections, 1); assert.equal(removed, 0, "a pending parent editor remains available for retry");
  fail = false; await context.ensureDraftParentLink(background, page);
  assert.equal(connections, 1); assert.equal(removed, 1); assert.equal(linked.size, 1);
  assert.equal(background.parentLinkSaved, true);
  console.log("UI-DRAFT-005: parent links use the editor, retain failures, avoid duplicates and protect scope/IME/permissions");
})().catch(error => {console.error(error); process.exitCode = 1;});
