"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const core = require("../notion_static/notion_editor_core.js");
const databaseBridge = require("../notion_static/notion_database_bridge.js");
const fileUpload = require("../notion_static/notion_file_upload.js");
const { CollaborationEditor, loadPinnedYjs } = require("./notion_collaboration_test_runtime.js");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8").replace(/\bboot\(\);\s*$/, "");
const noop = () => {};
const node = { hidden: true, value: "", dataset: {}, children: [], style: { setProperty: noop },
  classList: { add: noop, remove: noop, toggle: noop, contains: () => false },
  addEventListener: noop, removeEventListener: noop, querySelector: () => null, querySelectorAll: () => [],
  setAttribute: noop, toggleAttribute: noop, appendChild: noop, replaceChildren: noop, focus: noop };
const document = { querySelector: () => node, querySelectorAll: () => [], addEventListener: noop,
  createElement: () => ({ ...node }), documentElement: node, body: node };
const location = { href: "http://localhost/page/page-b", origin: "http://localhost", hostname: "localhost", pathname: "/page/page-b", search: "" };
const storage = new Map();
const paths = [], statuses = [], calls = [];
const context = { console, document, location, URL, URLSearchParams, setTimeout, clearTimeout,
  requestAnimationFrame: cb => cb(), getComputedStyle: () => ({ getPropertyValue: () => "240" }),
  localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
  history: { pushState: (_s, _t, url) => paths.push(url), replaceState: (_s, _t, url) => paths.push(url) },
  navigator: {}, crypto: globalThis.crypto, fetch: async () => ({ ok: true, json: async () => ({}) }),
  window: { document, location, NotionEditorCore: core, NotionCollaborationEditor: CollaborationEditor,
    NotionYjs: loadPinnedYjs(), NotionFileUpload: fileUpload, NotionDatabaseBridge: databaseBridge,
    NotionAiNotes: { beforeNavigate: async () => true }, CSS: { escape: String },
    addEventListener: noop, matchMedia: () => ({ matches: false }) },
  __status: text => statuses.push(text), __save: async () => {}, __calls: calls,
};
vm.createContext(context);
vm.runInContext(source + `
  api = (...args) => globalThis.__api(...args);
  setStatus = text => __status(text);
  savePage = () => __save();
  flushPendingNavigationPreferences = async () => true;
  closeSidePeek = async () => true;
  closeWorkspacePopover = closePageFind = closeDrawer = switchSidebarMode = () => {};
  renderPageLists = renderEditor = renderHome = renderWorkspacePopover = renderBreadcrumbPopover = () => {};
  renderSearchResults = renderSearchPreview = renderSearchCreators = () => {};
  applyWorkspaceNavigationSnapshot = snapshot => { State.navigation.snapshot = snapshot; };
  setNavigationPending = () => {};
  stopLiveCollaboration = () => __calls.push('stop-live');
  startLiveCollaboration = async () => {};
  loadShares = loadSessions = loadBacklinks = loadPublicLink = async () => {};
  restoreMainEditorDraft = () => null;
  resetEditorHistory = scrollToLocationBlock = applyPageFocusPolicy = () => {};
  EditorHost.notifyLifecycle = () => {};
  globalThis.runtime = { State, openPage, goHome, switchCanonicalWorkspace,
    refreshWorkspaceNavigationCapability, normalizeWorkspaceNavigationBootstrap };
`, context, { filename: "notion.js" });
const { State, openPage, goHome, switchCanonicalWorkspace, refreshWorkspaceNavigationCapability } = context.runtime;
const workspaces = ["a", "b", "c"].map(id => ({ id: `ws-${id}`, name: id, access: { role: "guest" } }));
const item = id => ({ id: `page-${id}`, workspace_id: `ws-${id}`, owner_user_id: "owner", title: id, placement: { binding_version: 1 } });
const nav = id => ({ workspace_id: `ws-${id}`, subject_id: "reader", membership_epoch: 1, preference_version: 0,
  favorite_version: 0, sidebar: { width_px: 240, collapsed: false }, favorites: [],
  sections: ["favorites", "recents", "teamspaces", "shared", "private"].map((section, display_order) =>
    ({ section, display_order, hidden: false, collapsed: false, item_limit: 10 })) });
function reset() {
  paths.length = statuses.length = calls.length = 0;
  State.user = { id: "reader" };
  Object.assign(State.navigation, { enabled: true, authoritative: true, collaborativeSharing: true,
    collaborativeEditing: false, pageWorkspaceResolution: true, workspaces, workspaceId: "ws-a",
    snapshot: nav("a"), sequence: 0, visitedWorkspaceIds: new Set(["ws-a"]), mutationReserved: false,
    mutationInFlight: null, workspaceTransitionSequence: null });
  State.pages = [item("a")]; State.activePageId = "page-a";
  State.pageData = { page: { ...item("a"), can_edit: true }, blocks: [{ id: "original", type: "paragraph", content: "preserve" }] };
  State.dirty = false; State.sidePeek.dirty = false; State.sidePeek.draft = false; State.sidePeek.embeddedRequired = false;
  State.selectedBlockIds.clear();
  node.value = "";
  context.__save = async () => { State.dirty = false; };
  context.window.NotionAiNotes.beforeNavigate = async () => true;
  context.__api = async url => {
    calls.push(url);
    if (url === "/api/notion/workspaces") return { items: workspaces, capabilities: {
      navigation_ui: true, collaborative_iam_sharing: true, page_workspace_resolution: true } };
    const match = url.match(/\/workspaces\/ws-([abc])\/(navigation|pages\?q=)$/);
    if (match) return match[2] === "navigation" ? nav(match[1]) : {
      workspace_id: `ws-${match[1]}`, items: [item(match[1])], count: 1, degraded: false, degraded_sources: [] };
    const resolve = url.match(/^\/api\/notion\/pages\/page-([abc])\/workspace$/);
    if (resolve) return { page_id: `page-${resolve[1]}`, workspace_id: `ws-${resolve[1]}` };
    const body = url.match(/^\/api\/notion\/workspaces\/ws-([abc])\/pages\/page-\1\/collaboration$/);
    if (body) return { page: { ...item(body[1]), can_edit: false, can_share: false },
      blocks: [{ id: `body-${body[1]}`, type: "paragraph", content: body[1] }], databases: [], files: [], revision: 1,
      capabilities: { atomic_save: true } };
    throw new Error(`Unexpected endpoint: ${url}`);
  };
}
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  reset();
  await openPage("page-b", false);
  assert.equal(State.navigation.workspaceId, "ws-b");
  assert.equal(State.activePageId, "page-b");
  assert.equal(State.pageData.blocks[0].content, "b");
  assert.deepEqual(paths, [], "startup must retain its direct URL");
  assert.equal(storage.get("notion.navigationWorkspace.reader"), "ws-b");
  assert.ok(!calls.includes("/api/notion/pages/page-b"), "canonical reads must not require a legacy projection");

  reset();
  State.pages = []; node.value = "filter hides authorized page";
  await openPage("page-a");
  assert.equal(State.activePageId, "page-a");
  assert.equal(node.value, "");
  assert.equal(State.pages.length, 1, "same-workspace links clear an excluding sidebar filter");

  reset();
  State.dirty = true;
  context.__save = async () => {};
  const original = State.pageData;
  await openPage("page-b");
  assert.equal(State.navigation.workspaceId, "ws-a");
  assert.equal(State.pageData, original);
  assert.equal(State.dirty, true);
  assert.ok(!calls.some(url => /ws-b\/navigation/.test(url)));
  assert.deepEqual(paths, []);

  reset();
  const failureApi = context.__api;
  context.__api = url => url.endsWith("/workspace") ? Promise.reject(new Error("not found")) : failureApi(url);
  await openPage("page-b");
  assert.equal(State.activePageId, "page-a");
  assert.equal(State.pageData.blocks[0].content, "preserve");

  reset();
  const failedSnapshotApi = context.__api;
  context.__api = url => url.includes("ws-b/navigation") ? Promise.reject(new Error("network")) : failedSnapshotApi(url);
  assert.equal(await switchCanonicalWorkspace("ws-b"), false);
  assert.equal(State.activePageId, "page-a");
  assert.equal(State.navigation.workspaceId, "ws-a");
  assert.ok(!calls.includes("stop-live"), "failed destination must preserve the source editor");

  reset();
  const slow = deferred(), slowApi = context.__api;
  context.__api = url => url.includes("ws-b/navigation") ? slow.promise : slowApi(url);
  const olderSwitch = switchCanonicalWorkspace("ws-b");
  await tick();
  assert.equal(await switchCanonicalWorkspace("ws-c"), true);
  slow.reject(new Error("late failure"));
  assert.equal(await olderSwitch, false);
  assert.equal(State.navigation.workspaceId, "ws-c", "late failure cannot roll back newer navigation");

  reset();
  const lateResolve = deferred(), resolveApi = context.__api;
  context.__api = url => url === "/api/notion/pages/page-b/workspace" ? lateResolve.promise : resolveApi(url);
  const olderPage = openPage("page-b");
  await tick();
  await goHome();
  lateResolve.resolve({ page_id: "page-b", workspace_id: "ws-b" });
  await olderPage;
  assert.equal(State.activePageId, "");
  assert.equal(State.navigation.workspaceId, "ws-a");

  reset();
  const preflight = deferred(); let preflights = 0;
  context.window.NotionAiNotes.beforeNavigate = () => ++preflights === 1 ? preflight.promise : Promise.resolve(true);
  const olderPreflight = openPage("page-b");
  await goHome(); preflight.resolve(true); await olderPreflight;
  assert.equal(State.activePageId, "", "an old AI preflight must not supersede a newer Home navigation");
  assert.ok(!calls.some(url => /page-b/.test(url)));

  reset();
  const bodyPending = deferred(), bodyApi = context.__api;
  context.__api = url => url.endsWith("page-a/collaboration") ? bodyPending.promise : bodyApi(url);
  const refresh = openPage("page-a"); await tick();
  State.dirty = true; State.pageData.blocks[0].content = "typed while loading";
  context.__save = async () => {};
  bodyPending.resolve(await bodyApi("/api/notion/workspaces/ws-a/pages/page-a/collaboration"));
  await refresh;
  assert.equal(State.pageData.blocks[0].content, "typed while loading");
  assert.equal(State.dirty, true);
  assert.ok(!calls.includes("stop-live"));

  reset();
  const staleCaps = deferred(); context.__api = () => staleCaps.promise;
  const check = refreshWorkspaceNavigationCapability(State.pageLoadSequence);
  State.pageLoadSequence += 1; State.navigation.workspaceId = "ws-c";
  staleCaps.reject(new Error("late network error"));
  assert.equal(await check, false);
  assert.equal(State.navigation.enabled, true);
  assert.equal(State.navigation.workspaceId, "ws-c");
  reset();
  const destinationPending = deferred(), lateTypingApi = context.__api;
  context.__api = url => url.includes("ws-b/navigation") ? destinationPending.promise : lateTypingApi(url);
  const whileTyping = switchCanonicalWorkspace("ws-b"); await tick();
  State.dirty = true; State.pageData.blocks[0].content = "typed while destination loads";
  context.__save = async () => {};
  destinationPending.resolve(nav("b"));
  assert.equal(await whileTyping, false);
  assert.equal(State.navigation.workspaceId, "ws-a");
  assert.equal(State.pageData.blocks[0].content, "typed while destination loads");
  assert.equal(State.dirty, true);

  reset();
  const staleSuccess = deferred(), latestApi = context.__api;
  context.__api = url => url.includes("ws-b/navigation") ? staleSuccess.promise : latestApi(url);
  const oldSuccess = switchCanonicalWorkspace("ws-b"); await tick();
  await switchCanonicalWorkspace("ws-c");
  staleSuccess.resolve(nav("b")); await oldSuccess;
  assert.equal(State.navigation.workspaceId, "ws-c");

  reset();
  const refreshPending = deferred(), refreshApi = context.__api;
  context.__api = url => url.endsWith("page-a/collaboration") ? refreshPending.promise : refreshApi(url);
  const samePage = openPage("page-a"); await tick();
  State.dirty = true; State.pageData.blocks[0].content = "new successfully saved text";
  refreshPending.resolve(await refreshApi("/api/notion/workspaces/ws-a/pages/page-a/collaboration"));
  await samePage;
  assert.equal(State.dirty, false);
  assert.equal(State.pageData.blocks[0].content, "new successfully saved text",
    "a pre-save snapshot must not overwrite a successful same-page edit");
  reset();
  State.navigation.enabled = State.navigation.authoritative = State.navigation.collaborativeSharing = false;
  context.__api = async url => {
    calls.push(url);
    assert.equal(url, "/api/notion/pages/page-legacy");
    return { page: { id: "page-legacy", title: "General document", can_edit: true }, revision: 7,
      blocks: [{ id: "legacy-block", type: "paragraph", content: "general body" }], capabilities: { atomic_save: true } };
  };
  await openPage("page-legacy");
  assert.equal(State.activePageId, "page-legacy");
  assert.equal(State.pageData.blocks[0].content, "general body");
  assert.equal(State.serverRevision, 7);
  assert.equal(State.atomicSaveEnabled, true);
  console.log("UI-DIRECT-LINK: 14 runtime scenarios passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
