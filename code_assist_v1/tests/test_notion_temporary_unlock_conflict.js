"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function section(start, end) {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `missing production section: ${start}`);
  return source.slice(first, last);
}

const title = { value: "Unsaved title", disabled: false, dataset: {} };
const lockButton = { hidden: false, disabled: false, innerHTML: "" };
const page = {
  id: "locked-page", title: "Original title", settings: { locked: true },
  can_edit: true, can_change_settings: true, collaborative_editing: false,
};
const state = {
  user: { id: "owner" }, activePageId: page.id,
  pageData: { page, blocks: [{ id: "b1", content: "Unsaved body" }] },
  dirty: true, editRevision: 1, serverRevision: 4, atomicSaveEnabled: true,
  saveConflict: null, saveTimer: null, saveInFlight: null, saveQueued: false,
  pageLoadSequence: 1,
};
const grants = new Map([[page.id, { userId: "owner", token: "tab-only-grant" }]]);
let renderedLocked = null, draftPersisted = 0, topbarRenders = 0, sidebarRenders = 0;
const context = {
  State: state, TemporaryUnlockGrants: grants,
  EditorCore: { deepClone: value => JSON.parse(JSON.stringify(value)) },
  EditorHost: { isComposing: () => false },
  $: selector => selector === "#pageTitle" ? title : selector === "#pageLockBtn" ? lockButton : null,
  document: { removeEventListener() {}, addEventListener() {} },
  requestAnimationFrame: callback => callback(), clearTimeout, setTimeout,
  applyPageLayout() {},
  renderBlocks: () => { renderedLocked = context.pageIsLocked(page); },
  pageCanChangeSettings: current => current.can_change_settings,
  iconSvg: () => "",
  renderTopbarPageActions: current => { topbarRenders += 1; context.renderPageLockState(current); },
  renderPageLists: () => { sidebarRenders += 1; },
  atomicSaveRequest: () => ({ operation_id: "conflicting-save" }),
  persistMainEditorDraft: () => { draftPersisted += 1; },
  setStatus() {},
  api: async () => { throw Object.assign(new Error("revision conflict"), {
    status: 409, data: { current_revision: 5 },
  }); },
  showSaveConflict: (_, pageId, error) => {
    state.saveConflict = { pageId, currentRevision: error.data.current_revision };
  },
};
vm.createContext(context);
vm.runInContext([
  "let PendingSettingsCompositionRefresh = null;",
  section("function pageIsLocked(", "function applyPageLayout("),
  section("function refreshPageSettingsControls(", "async function saveCollaborativePageSettings("),
  section("function renderPageLockState(", "function renderTopbarPageActions("),
  section("async function savePage()", "async function uploadCollaborativeFile("),
].join("\n"), context);

(async () => {
  assert.equal(context.pageIsLocked(page), false, "the tab grant initially allows editing");
  await context.savePage();
  assert.equal(grants.has(page.id), false, "a conflicting save drops the stale grant");
  assert.equal(title.disabled, true, "the title becomes read-only immediately");
  assert.equal(renderedLocked, true, "body blocks are rendered as locked");
  assert.match(lockButton.innerHTML, /잠금/);
  assert.equal(topbarRenders, 1, "the lock chip is refreshed");
  assert.equal(sidebarRenders, 1, "sidebar row actions return to locked mode");
  assert.equal(state.dirty, true, "the conflicting draft remains unsaved");
  assert.deepEqual(state.pageData.blocks, [{ id: "b1", content: "Unsaved body" }]);
  assert.equal(title.value, "Unsaved title");
  assert.equal(state.saveConflict.currentRevision, 5);
  assert.ok(draftPersisted >= 1, "the draft is retained for recovery");
  console.log("Temporary unlock conflict preserves the draft and relocks the page UI");
})().catch(error => { console.error(error); process.exitCode = 1; });
