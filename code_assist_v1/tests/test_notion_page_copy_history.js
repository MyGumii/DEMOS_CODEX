"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), {test} = require("node:test");
const source = fs.readFileSync(require.resolve("../notion_static/notion.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
function element() {
  return {children: [], events: {}, hidden: false, textContent: "", append(...children) {this.children.push(...children);},
    appendChild(child) {this.children.push(child);}, replaceChildren(...children) {this.children = [...children];},
    setAttribute() {}, addEventListener(event, fn) {this.events[event] = fn;}};
}
function item(operation = "one", options = {}) {
  return {operation_id: operation, created_at: 123, state: "committed", current: {archived: false},
    result: {page: {id: `copy-${operation}`, title: "<img src=x onerror=alert(1)>", workspace_id: "ws"}}, ...options};
}
function runtime() {
  const root = element(), modal = element(), calls = [], opened = [];
  const state = {user: {id: "owner"}, navigation: {workspaceId: "ws", sequence: 1, collaborativePageCopy: true},
    pageData: {blocks: ["dirty body"]}, sidePeek: {dirty: true}, activePageId: "source"};
  let handler = async () => ({items: [item()], next_cursor: null});
  let pendingHandler = async () => ({items: [], next_cursor: null});
  let available = true, canManage = true;
  const ctx = {State: state, document: {createElement: element}, sidebarCapabilities: () => ({canManagePages: canManage}),
    $: selector => selector === "#pageCopyHistory" ? (available ? root : null) : selector === "#modalBg" ? modal : null,
    openModal: () => {modal.hidden = false;}, closeModal: () => {modal.hidden = true;}, formatTime: value => `time-${value}`,
    api: async url => {
      if (url.includes("page-copy-intents")) return pendingHandler(url);
      calls.push(url); return handler(url);
    }, openPage: async id => {opened.push(id);}, console};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf("function pageLifecycleScope("), source.indexOf("function pageLifecycleRetainsEditors(")), ctx);
  vm.runInContext(source.slice(source.indexOf("async function openPageCopyHistory("), source.indexOf("const PageRenameSessions")), ctx);
  return {ctx, state, root, modal, calls, opened, handler: fn => {handler = fn;},
    pendingHandler: fn => {pendingHandler = fn;}, canManage: value => {canManage = value;},
    replaceModal: () => {available = false;}, status: () => root.children[0], rows: () => root.children[1]?.children || [],
    more: () => root.children[2], pendingRows: () => root.children[3]?.children[1]?.children || [],
    refresh: () => root.children[4]};
}
test("history can adopt and recover a server-owned in-flight request after local storage loss", async () => {
  const h = runtime(), originalBody = h.state.pageData, originalPeek = h.state.sidePeek;
  const request = {operation_id: "in-flight", expected_document_sequence: 7,
    expected_workspace_epoch: 2, expected_document_epoch: 3};
  h.pendingHandler(async () => ({items: [{state: "pending", source_page_id: "source",
    operation_id: "in-flight", request, created_at: 124}], next_cursor: null}));
  const adopted = [], actions = [];
  h.ctx.getPageCopyController = () => ({adopt: async (scope, body) => {
    adopted.push([scope, body]); return true;
  }});
  h.ctx.performCollaborativePageCopy = async (pageId, options) => {
    actions.push([pageId, options]); return {state: "unknown"};
  };
  await h.ctx.openPageCopyHistory();
  assert.equal(h.pendingRows().length, 1);
  await h.pendingRows()[0].children[1].events.click();
  assert.equal(adopted[0][0].pageId, "source");
  assert.deepEqual(adopted[0][1], request);
  assert.equal(actions[0][0], "source");
  assert.equal(actions[0][1].operationId, "in-flight");
  assert.equal(actions[0][1].lookupOnly, true);
  assert.equal(h.state.pageData, originalBody);
  assert.equal(h.state.sidePeek, originalPeek);
});
test("history keeps a request visible while it becomes terminal and can refresh a changed result", async () => {
  const h = runtime();
  const request = {operation_id: "in-flight", expected_document_sequence: 7,
    expected_workspace_epoch: 2, expected_document_epoch: 3};
  const pending = {state: "pending", source_page_id: "source",
    operation_id: "in-flight", request, created_at: 124};
  h.pendingHandler(async () => ({items: [pending], next_cursor: null}));
  h.handler(async () => ({items: [item("in-flight")], next_cursor: null}));
  await h.ctx.openPageCopyHistory();
  assert.equal(h.rows().length, 1);
  assert.equal(h.pendingRows()[0].hidden, true, "terminal receipt supersedes a stale pending row");

  h.pendingHandler(async () => ({items: [], next_cursor: null}));
  await h.refresh().events.click();
  assert.equal(h.rows().length, 1);
  assert.equal(h.pendingRows().length, 0);
  assert.equal(h.refresh().disabled, false);
});
test("history can refresh a pending request after another window completes it", async () => {
  const h = runtime();
  const request = {operation_id: "later", expected_document_sequence: 7,
    expected_workspace_epoch: 2, expected_document_epoch: 3};
  h.pendingHandler(async () => ({items: [{state: "pending", source_page_id: "source",
    operation_id: "later", request, created_at: 124}], next_cursor: null}));
  h.handler(async () => ({items: [], next_cursor: null}));
  await h.ctx.openPageCopyHistory();
  assert.equal(h.pendingRows().length, 1);
  assert.equal(h.pendingRows()[0].hidden, false);

  h.pendingHandler(async () => ({items: [], next_cursor: null}));
  h.handler(async () => ({items: [item("later")], next_cursor: null}));
  await h.refresh().events.click();
  assert.equal(h.pendingRows().length, 0);
  assert.equal(h.rows().length, 1);
  assert.equal(h.rows()[0].children[0].textContent.includes("time-123"), true);
});
test("history discovers completed and cancelled results without local storage or replacing drafts", async () => {
  const h = runtime(), body = h.state.pageData, peek = h.state.sidePeek;
  h.handler(async () => ({items: [item(), item("archived", {current: {archived: true}}),
    {operation_id: "cancelled", created_at: 123, state: "cancelled", result: {workspace_id: "ws"}}], next_cursor: null}));
  await h.ctx.openPageCopyHistory(); assert.equal(h.rows().length, 3);
  assert.equal(h.rows()[0].children[0].textContent, "<img src=x onerror=alert(1)> · time-123", "title is plain text, not markup");
  assert.equal(h.rows()[1].children.length, 1); assert.equal(h.rows()[2].children.length, 1);
  assert.equal(h.state.pageData, body); assert.equal(h.state.sidePeek, peek);
  await h.rows()[0].children[1].events.click();
  assert.deepEqual(h.opened, ["copy-one"]); assert.equal(h.modal.hidden, false, "failed/blocked open retains the result dialog");
});
test("history paginates once, suppresses repeated results and closes after guarded opening succeeds", async () => {
  const h = runtime(); let complete;
  h.handler(async () => ({items: [item()], next_cursor: "next-page"}));
  await h.ctx.openPageCopyHistory(); assert.equal(h.more().hidden, false);
  h.handler(async () => new Promise(resolve => {complete = resolve;}));
  const second = h.more().events.click(); await tick();
  await h.more().events.click(); assert.equal(h.calls.length, 2); assert.match(h.calls[1], /cursor=next-page/);
  complete({items: [item(), item("two")], next_cursor: null}); await second;
  assert.equal(h.rows().length, 2); assert.equal(h.more().hidden, true);
  h.ctx.openPage = async id => {h.state.activePageId = id;};
  await h.rows()[1].children[1].events.click(); assert.equal(h.modal.hidden, true);
});
test("history ignores late results after workspace or modal changes", async () => {
  for (const change of [h => {h.state.navigation.workspaceId = "other";}, h => h.replaceModal()]) {
    const h = runtime(); let finish;
    h.handler(async () => new Promise(resolve => {finish = resolve;}));
    const pending = h.ctx.openPageCopyHistory(); await tick(); change(h);
    finish({items: [item()], next_cursor: null}); await pending;
    assert.equal(h.rows().length, 0);
  }
});
test("history requires capability and ownership and offers retry after a failed read", async () => {
  for (const change of [h => {h.state.navigation.collaborativePageCopy = false;}, h => h.canManage(false)]) {
    const h = runtime(); change(h); await h.ctx.openPageCopyHistory(); assert.equal(h.calls.length, 0);
  }
  const h = runtime(); h.handler(async () => {throw Error("offline");});
  await h.ctx.openPageCopyHistory(); assert.equal(h.status().textContent, "offline"); assert.equal(h.more().hidden, false);
  h.handler(async () => ({items: [], next_cursor: null})); await h.more().events.click();
  assert.match(h.status().textContent, /결과가 없습니다/); assert.equal(h.more().hidden, true);
});
