"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const Draft = require("../notion_static/notion_page_draft.js");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
const clone = value => JSON.parse(JSON.stringify(value));
const nodes = new Map();
const node = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {hidden: true, focus() {}, replaceChildren() {}, contains() {return false;}});
  return nodes.get(selector);
};
const State = {user: {id: "owner"}, navigation: {workspaceId: "workspace", enabled: true, workspaces: [{id: "workspace", version: 4, access: {role: "owner"}}]},
  workspaceOwnerFilter: "", collapsedPageIds: new Set(), activePageId: "parent"};
let requests = [], conflict = true, failSave = true, disposed = false, transferred, saved, opened = "";
const childState = {dirty: true, composing: false};
const child = {
  state: () => ({...childState}), snapshot: () => clone(saved || transferred),
  async flush() {
    if (failSave) throw new Error("server acknowledgement unavailable");
    childState.dirty = false; saved = clone(transferred); return saved;
  }, dispose() { disposed = true; },
};
const context = {State, PageMetadataSync:null, LinkedDatabaseItems:null, activeDatabaseDatePickerAnchor:null,
  EmbeddedEditorFrame: false, $: node, window: {NotionAiNotes: {beforeNavigate: async () => true}},
  document: {activeElement: {blur() {}}}, clearTimeout() {}, setTimeout() {}, requestAnimationFrame: fn => fn(),
  isNarrowSidebar: () => false, setStatus() {}, updateSidePeekDraftStatus() {}, setSidePeekEditorStatus() {},
  setSidePeekCreatePresentation() {}, syncSidePeekStarterVisibility() {},
  saveCollapsedPages() {}, loadPages: async () => {throw new Error("collaborative draft save must preserve the active editor");}, refreshPageNavigation: async () => {}, flushPageDraft: async () => true,
  expandPageInSidebar() {},
  resolveSidePeekDatabaseContext: () => null,
  ensureDraftParentLink: async holder => {assert.equal(holder.parentLinkSaved, true, "the canonical creation receipt suppresses a second parent link");},
  PageFocusPolicy: {PRESERVE: "preserve"}, openPage: async id => {opened = id;},
  ensureSidePeekEditor: async holder => {
    if (!holder.embedded) {
      assert.equal(holder.embeddedInitial.pageId, "new-child");
      assert.equal(holder.embeddedInitial.title, "최초 제목");
      transferred = clone(holder.data); holder.embeddedInitial = null;
      holder.embedded = child; holder.embeddedConnection = child;
    }
    return true;
  },
  api: async (url, options) => {
    requests.push({url, body: clone(options.body)});
    assert.equal(url, "/api/notion/workspaces/workspace/pages", "collaborative creation never uses a legacy body-save route");
    if (conflict) {conflict = false; throw Object.assign(new Error("version conflict"), {status: 409, data: {current_version: 5}});}
    State.sidePeek.data.blocks[0].content = "생성 응답을 기다리며 추가 입력";
    State.sidePeek.data.page.title = "수정한 제목";
    return {page: {id: "new-child"}, workspace: {id: "workspace", version: 6}, parent_link_saved: true};
  },
};
vm.createContext(context);
vm.runInContext([
  slice("let SidePeekOpenSequence =", "async function openSidePeek(pageId"),
  slice("async function closeSidePeek(", "function resolveSidePeekDatabaseContext("),
  slice("function markSidePeekDirty(", "function addSidePeekBlock("),
].join("\n"), context);

(async () => {
  State.sidePeek = {pageId: "", data: {page: {title: "최초 제목", can_edit: true}, blocks: [{id: "body", type: "paragraph", content: "첫 입력", props: {}}]},
    dirty: true, editRevision: 1, draft: Draft.create({userId: "owner", workspaceId: "workspace", ownerFilter: "",
      parentId: "parent", parentTitle: "부모", navigationEnabled: true, baseVersion: 4, collaborative: true}, "one-create")};
  await context.saveSidePeek();
  assert.equal(requests.length, 2);
  assert.equal(requests[0].body.base_version, 4); assert.equal(requests[1].body.base_version, 5);
  assert.equal(requests[0].body.operation_id, requests[1].body.operation_id);
  assert.equal(requests[1].body.title, "최초 제목");
  assert.equal(State.navigation.workspaces[0].access.role, "owner", "metadata response must not erase access presentation");
  assert.equal(State.sidePeek.pageId, "new-child");
  assert.equal(State.sidePeek.parentLinkSaved, true);
  assert.ok(transferred, State.sidePeek.saveError || "child editor was not connected");
  assert.equal(transferred.blocks[0].content, "생성 응답을 기다리며 추가 입력");
  assert.equal(transferred.page.title, "수정한 제목");
  assert.equal(State.sidePeek.dirty, true);
  assert.equal(await context.closeSidePeek(false), false);
  assert.equal(disposed, false);
  await context.openSidePeekAsFullPage(); assert.equal(opened, "", "failed flush cannot navigate away");
  failSave = false;
  await context.openSidePeekAsFullPage();
  assert.equal(opened, "new-child"); assert.equal(disposed, true);
  assert.equal(requests.length, 2, "retries after creation only flush the known child editor");
  console.log("UI-DRAFT-004: workspace conflict, permission metadata, latest draft handoff, save failure and full-page transition passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
