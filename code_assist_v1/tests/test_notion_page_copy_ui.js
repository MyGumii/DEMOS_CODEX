"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm");
const {test} = require("node:test");
const Copy = require("../notion_static/notion_page_copy.js");
const {lockManager} = require("./notion_page_copy_test_helpers.js");
const source = fs.readFileSync(require.resolve("../notion_static/notion.js"), "utf8");
const tick = () => new Promise(resolve => setImmediate(resolve));
const json = value => JSON.parse(JSON.stringify(value));
function memory() {
  const entries = new Map();
  return {get length() {return entries.size;}, key: i => [...entries.keys()][i], getItem: key => entries.get(key) ?? null,
    setItem: (key, value) => entries.set(key, value), removeItem: key => entries.delete(key)};
}
function element() {
  return {children: [], events: {}, hidden: false, inert: false, append(...nodes) {this.children.push(...nodes);},
    appendChild(node) {this.children.push(node);}, replaceChildren(...nodes) {this.children = nodes;},
    addEventListener(type, fn) {this.events[type] = fn;}};
}
function runtime(storage = memory()) {
  const state = {user: {id: "owner"}, navigation: {enabled: true, collaborativeSharing: true, collaborativePageCopy: true,
    workspaceId: "ws", sequence: 1}, activePageId: "source", pageData: {page: {id: "source", collaborative_editing: true},
    blocks: [{content: "main unsaved"}]}, dirty: true, sidePeek: {pageId: "peek", dirty: true}, pages: []};
  const current = {id: "source", workspace_id: "ws", can_edit: true, owner_user_id: "owner",
    document_sequence: 7, workspace_epoch: 2, document_epoch: 3, role: "full_access"};
  const result = {contract: "code_assist.notion.canonical_page_copy.v1", source_page_id: "source",
    page: {id: "copy", workspace_id: "ws"}, page_ids: ["copy"], workspace: {id: "ws", version: 4}};
  const panel = element(), main = element(), peek = element(), calls = [], effects = [];
  let handler = async url => url.endsWith("/collaboration") ? {page: current}
    : url.endsWith("-result") ? {state: "committed", result, current: {archived: false}} : result;
  const ctx = {State: state, window: {NotionPageCopy: Copy, localStorage: storage, sessionStorage: memory(), navigator: {locks: lockManager()}}, console,
    $: selector => ({"#pageCopyStatus": panel, "#pageView": main, "#sidePeek": peek})[selector] || null,
    document: {createElement: element, createTextNode: text => ({textContent: text})},
    sidebarCapabilities: () => ({canManagePages: true}), pageIsLocked: page => Boolean(page?.settings?.locked),
    embeddedEditorState: () => ({composing: state.composing}),
    flushPageMoveEditors: async () => {effects.push("flush"); return true;},
    navigationOperationId: () => "copy-op-1", setStatus: (text, tone) => effects.push(["status", text, tone]),
    fetchWorkspacePagesSnapshot: async () => {effects.push("fetch-list"); return [current, result.page];},
    applyWorkspacePagesSnapshot: (items, options) => effects.push(["apply-list", options]),
    api: async (url, options = {}) => {
      if (url.endsWith("-intent")) return {state: "pending", source_page_id: "source", request: options.body};
      calls.push([url, options]); return handler(url, options);
    },
    openPage: async id => {effects.push(["open", id]); return false;}};
  vm.createContext(ctx);
  for (const [start, end] of [
    ["function pageCanManageLifecycle(", "function clearPageDropIndicators("],
    ["async function duplicatePage(", "const PageRenameSessions"],
    ["function pageLifecycleScope(", "function pageLifecycleRetainsEditors("],
    ["const PageMoveInputLocks", "async function flushPageMoveEditors("],
    ["function normalizeWorkspaceNavigationBootstrap(", "function normalizeWorkspaceNavigationSnapshot("],
  ]) vm.runInContext(source.slice(source.indexOf(start), source.indexOf(end)), ctx);
  return {ctx, state, current, result, panel, main, peek, calls, effects, storage,
    handler: value => {handler = value;}, buttons: () => panel.children.flatMap(row => row.children).filter(node => node.type === "button")};
}

test("capability is explicit, IAM-scoped, and every duplicate entry refuses legacy fallback", async () => {
  const h = runtime();
  const capabilities = {navigation_ui: true, collaborative_page_copy: true};
  assert.equal(h.ctx.normalizeWorkspaceNavigationBootstrap({capabilities, items: []}).collaborativePageCopy, false);
  capabilities.collaborative_iam_sharing = true;
  assert.equal(h.ctx.normalizeWorkspaceNavigationBootstrap({capabilities, items: []}).collaborativePageCopy, true);
  h.state.navigation.collaborativePageCopy = false;
  assert.equal(h.ctx.pageCanDuplicate(h.current), false);
  await h.ctx.duplicatePage("source"); assert.equal(h.calls.length, 0);
  h.state.navigation.collaborativePageCopy = true;
  assert.equal(h.ctx.pageCanDuplicate(h.current), true);
  assert.equal(h.ctx.pageCanDuplicate({...h.current, owner_user_id: "other"}), false);
  assert.equal(h.ctx.pageCanDuplicate({...h.current, settings: {locked: true}}), true);
  assert.equal(h.ctx.pageCanDuplicate({...h.current, can_edit: false, settings: {locked: true}}), false);
  h.state.navigation.collaborativePageCopy = false;
  assert.equal(h.ctx.pageCanDuplicate({...h.current, settings: {locked: true}}), false);
  h.state.navigation.collaborativeSharing = false;
  assert.equal(h.ctx.pageCanDuplicate({...h.current, role: "owner", settings: {locked: true}}), false);
});

test("real input hold protects main and peek; one copy refreshes only navigation", async () => {
  const h = runtime(); let finish;
  const body = h.state.pageData, side = h.state.sidePeek;
  h.handler(async url => url.endsWith("/collaboration") ? {page: h.current} : new Promise(resolve => {finish = resolve;}));
  const first = h.ctx.performCollaborativePageCopy("source");
  assert.equal(first, h.ctx.performCollaborativePageCopy("source"));
  await tick(); assert.equal(h.main.inert, true); assert.equal(h.peek.inert, true);
  assert.equal(h.ctx.hasPendingPageCopy(), true);
  assert.equal(h.panel.hidden, false);
  assert.ok(h.buttons().length > 0 && h.buttons().every(button => button.disabled));
  finish(h.result); await first;
  assert.equal(h.main.inert, false); assert.equal(h.peek.inert, false);
  assert.equal(h.state.pageData, body); assert.equal(h.state.sidePeek, side);
  assert.equal(h.state.dirty, true); assert.equal(h.state.sidePeek.dirty, true);
  assert.deepEqual(json(h.effects.find(value => value?.[0] === "apply-list")[1]), {preserveActivePage: true});
  assert.equal(h.calls.length, 2);
  assert.equal(h.buttons().some(button => button.textContent === "복제본 열기"), true);
});

test("main IME, side-peek IME and failed flush prevent both metadata and copy requests", async () => {
  for (const mode of ["main", "peek", "flush"]) {
    const h = runtime();
    if (mode === "main") h.state.composing = true;
    if (mode === "peek") h.state.sidePeek.composing = true;
    if (mode === "flush") h.ctx.flushPageMoveEditors = async () => false;
    await assert.rejects(h.ctx.performCollaborativePageCopy("source"));
    assert.equal(h.calls.length, 0); assert.equal(h.main.inert, false); assert.equal(h.peek.inert, false);
  }
});

test("reload panel recovers receipt before touching blocked or dirty editors", async () => {
  const before = runtime(); before.handler(async url => {
    if (url.endsWith("/collaboration")) return {page: before.current};
    throw Error("lost response");
  });
  await assert.rejects(before.ctx.performCollaborativePageCopy("source"));
  const h = runtime(before.storage), body = h.state.pageData, side = h.state.sidePeek;
  h.state.composing = true; h.state.sidePeek.composing = true;
  h.ctx.flushPageMoveEditors = async () => {throw Error("must not flush");};
  h.ctx.renderPageCopyStatus();
  const check = h.buttons().find(button => button.textContent === "결과 확인");
  assert.ok(check); await check.events.click();
  assert.equal(h.calls.length, 1); assert.match(h.calls[0][0], /duplicate-result$/);
  assert.equal(h.state.pageData, body); assert.equal(h.state.sidePeek, side);
  assert.equal(h.state.dirty, true); assert.equal(h.state.sidePeek.dirty, true);
  assert.equal(h.main.inert, false); assert.equal(h.peek.inert, false);
});

test("late receipt after workspace change is durable but cannot overwrite the new UI", async () => {
  const h = runtime(); let finish;
  h.handler(async url => url.endsWith("/collaboration") ? {page: h.current} : new Promise(resolve => {finish = resolve;}));
  const pending = h.ctx.performCollaborativePageCopy("source"); await tick();
  h.state.navigation.workspaceId = "other"; h.state.navigation.sequence++;
  h.state.activePageId = "new-page"; h.state.pageData.blocks[0].content = "other workspace draft";
  finish(h.result); await pending;
  assert.deepEqual(h.effects, ["flush"]); assert.equal(h.state.pageData.blocks[0].content, "other workspace draft");
  assert.equal(h.panel.hidden, true);
  assert.equal(h.ctx.getPageCopyController().list({userId: "owner", workspaceId: "ws"})[0].confirmed, true);
});

test("late copy error cannot replace status on a different page in the same workspace", async () => {
  const h = runtime(); let fail;
  h.handler(async url => url.endsWith("/collaboration") ? {page: h.current} : new Promise((_resolve, reject) => {fail = reject;}));
  const pending = h.ctx.duplicatePage("source"); await tick();
  h.state.activePageId = "other-page";
  fail(Error("network lost")); await pending;
  assert.deepEqual(h.effects, ["flush"]);
  assert.equal(h.ctx.hasPendingPageCopy(), true);
});

test("list failure after commit retains result; subsequent action only reads the receipt", async () => {
  const h = runtime(); h.ctx.fetchWorkspacePagesSnapshot = async () => {throw Error("list offline");};
  await assert.rejects(h.ctx.performCollaborativePageCopy("source"), /list offline/);
  const firstCount = h.calls.length;
  h.ctx.fetchWorkspacePagesSnapshot = async () => [];
  await h.ctx.performCollaborativePageCopy("source");
  assert.equal(h.calls.length, firstCount + 1); assert.match(h.calls.at(-1)[0], /duplicate-result$/);
  assert.equal(h.effects.filter(item => item === "flush").length, 1);
});

test("opening a recovered copy rechecks current archive state; stale panel cannot acknowledge another scope", async () => {
  const h = runtime(); await h.ctx.performCollaborativePageCopy("source");
  const open = h.buttons().find(button => button.textContent === "복제본 열기");
  const acknowledge = h.buttons().find(button => button.textContent === "확인");
  h.handler(async () => ({state: "committed", result: h.result, current: {archived: true}}));
  await open.events.click(); assert.equal(h.effects.some(item => item?.[0] === "open"), false);
  h.state.navigation.workspaceId = "other"; await acknowledge.events.click();
  assert.equal(h.ctx.getPageCopyController().list({userId: "owner", workspaceId: "ws"}).length, 1);
});

test("shared storage notification discovers another tab's receipt without replacing either editor", async () => {
  const h = runtime(), page = h.state.pageData, side = h.state.sidePeek;
  const own = h.ctx.getPageCopyController(); h.ctx.renderPageCopyStatus();
  const other = Copy.create({storage: () => h.storage, locks: () => h.ctx.window.navigator.locks,
    transport: async (url, options) => url.endsWith("-intent")
      ? {state: "pending", source_page_id: "source", request: options.body} : h.result});
  await other.run({userId: "owner", workspaceId: "ws", pageId: "source"}, {current: () => true,
    prepare: async () => ({operation_id: "other-tab-op", expected_document_sequence: 7, expected_workspace_epoch: 2, expected_document_epoch: 3})});
  assert.equal(h.panel.hidden, true);
  own.observe({key: h.storage.key(0), storageArea: h.storage});
  assert.equal(h.panel.hidden, false); assert.ok(h.buttons().some(button => button.textContent === "복제본 열기"));
  assert.equal(h.state.pageData, page); assert.equal(h.state.sidePeek, side); assert.deepEqual(h.effects, []);
  h.state.user.id = "other-user"; own.observe({key: h.storage.key(0), storageArea: h.storage});
  assert.equal(h.panel.hidden, true);
});

test("actual recovery-panel cancellation retires uncertain request while preserving composing main and peek", async () => {
  const h = runtime(); h.handler(async url => {
    if (url.endsWith("/collaboration")) return {page: h.current};
    throw Error("response lost");
  });
  await assert.rejects(h.ctx.performCollaborativePageCopy("source"));
  const body = h.calls.at(-1)[1].body, page = h.state.pageData, side = h.state.sidePeek;
  h.state.composing = true; h.state.sidePeek.composing = true;
  h.handler(async () => ({state: "cancelled", result: {contract: "code_assist.notion.canonical_page_copy_cancelled.v1",
    state: "cancelled", workspace_id: "ws", source_page_id: "source", operation_id: body.operation_id, copy_request: body}}));
  const cancel = h.buttons().find(button => button.textContent === "요청 취소");
  assert.ok(cancel); await cancel.events.click();
  assert.match(h.calls.at(-1)[0], /duplicate-cancel$/);
  assert.equal(h.state.pageData, page); assert.equal(h.state.sidePeek, side);
  assert.equal(h.effects.filter(item => item === "flush").length, 1);
  assert.equal(h.panel.hidden, true); assert.equal(h.ctx.hasPendingPageCopy(), false);
});
