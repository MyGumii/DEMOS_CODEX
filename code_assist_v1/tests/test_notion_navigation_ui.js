"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const core = require("../notion_static/notion_editor_core.js");
const databaseBridge = require("../notion_static/notion_database_bridge.js");
const fileUpload = require("../notion_static/notion_file_upload.js");
const { CollaborationEditor, loadPinnedYjs } = require("./notion_collaboration_test_runtime.js");
const sourcePath = path.join(__dirname, "..", "notion_static", "notion.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/\bboot\(\);\s*$/, "");

function classList() {
  const values = new Set();
  return {
    add(...items) { items.forEach(item => values.add(item)); },
    remove(...items) { items.forEach(item => values.delete(item)); },
    toggle(item, force) {
      if (force === true) values.add(item);
      else if (force === false) values.delete(item);
      else if (values.has(item)) values.delete(item);
      else values.add(item);
      return values.has(item);
    },
    contains(item) { return values.has(item); },
  };
}

const inertNode = {
  hidden: true,
  className: "",
  value: "",
  textContent: "",
  innerHTML: "",
  dataset: {},
  children: [],
  style: { setProperty() {} },
  classList: classList(),
  addEventListener() {},
  removeEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  setAttribute() {},
  removeAttribute() {},
  toggleAttribute() {},
  appendChild() {},
  replaceChildren() { this.children = []; this.innerHTML = ""; },
  focus() {},
};
const documentStub = {
  querySelector(selector) { return selector === "#workspaceToast" ? null : inertNode; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return { ...inertNode, classList: classList(), dataset: {} }; },
  documentElement: { dataset: {}, style: { setProperty() {} } },
  body: { classList: classList() },
};
const locationStub = {
  href: "http://localhost/workspace",
  origin: "http://localhost",
  hostname: "localhost",
  pathname: "/workspace",
  search: "",
};
const localStorageStub = {
  values: new Map(),
  getItem(key) { return this.values.get(key) ?? null; },
  setItem(key, value) { this.values.set(key, String(value)); },
};
const context = {
  console,
  document: documentStub,
  window: {
    NotionEditorCore: core,
    NotionCollaborationEditor: CollaborationEditor,
    NotionYjs: loadPinnedYjs(),
    NotionFileUpload: fileUpload,
    NotionDatabaseBridge: databaseBridge,
    NotionAiNotes: { beforeNavigate: async () => true },
    CSS: { escape: value => String(value) },
    location: locationStub,
    addEventListener() {},
    matchMedia() { return { matches: false }; },
    confirm() { return true; },
  },
  location: locationStub,
  history: { pushState() {}, replaceState() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  localStorage: localStorageStub,
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  requestAnimationFrame: callback => callback(),
  getComputedStyle: () => ({ getPropertyValue: () => "270" }),
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  crypto: globalThis.crypto,
};
context.window.document = documentStub;

const sections = ["favorites", "recents", "teamspaces", "shared", "private"].map(
  (section, display_order) => ({
    section,
    display_order,
    hidden: false,
    collapsed: false,
    item_limit: 10,
  }),
);

const snapshot = (workspaceId, preferenceVersion = 0) => ({
  workspace_id: workspaceId,
  subject_id: "tester",
  membership_epoch: 1,
  preference_version: preferenceVersion,
  favorite_version: 0,
  sidebar: { width_px: 312, collapsed: true },
  sections,
  favorites: [
    { source_owner_subject_id: "owner-a", source_page_id: "page-a", position: 0 },
  ],
});

const probe = `
(async () => {
  const originalLoadWorkspaceNavigationSnapshot = loadWorkspaceNavigationSnapshot;
  const originalCapabilityRefresh = refreshWorkspaceNavigationCapability;
  const disabled = normalizeWorkspaceNavigationBootstrap({
    items: [{ id: "ws-a", name: "A", access: { role: "owner" } }],
    capabilities: { navigation_ui: false },
  }, "ws-a");
  assert.equal(disabled.enabled, false);
  State.user = { id: "tester" };
  State.favorites = new Set(["legacy-favorite"]);
  api = async requestPath => {
    assert.equal(requestPath, "/api/notion/workspaces");
    return {
      items: [{ id: "ws-a", name: "A", access: { role: "owner" } }],
      capabilities: { navigation_ui: false },
    };
  };
  assert.equal(await initializeWorkspaceNavigation(), false);
  assert.equal(State.navigation.enabled, false);
  assert.equal(State.favorites.has("legacy-favorite"), true, "default-off must preserve legacy local state");

  const enabled = normalizeWorkspaceNavigationBootstrap({
    items: [
      { id: "ws-a", name: "A", access: { role: "owner" } },
      { id: "ws-guest", name: "Guest", access: { role: "guest" } },
      { id: "ws-b", name: "B", access: { role: "member" } },
    ],
    capabilities: { navigation_ui: true },
  }, "ws-b");
  assert.equal(enabled.enabled, true);
  assert.deepEqual(enabled.workspaces.map(item => item.id), ["ws-a", "ws-b"]);
  assert.equal(enabled.workspaceId, "ws-b");
  assert.throws(
    () => normalizeWorkspaceNavigationBootstrap({
      items: [
        { id: "ws-a", name: "A", access: { role: "owner" } },
        { id: "ws-a", name: "Duplicate", access: { role: "member" } },
      ],
      capabilities: { navigation_ui: true },
    }, ""),
    /identity is duplicated/,
  );
  const authoritativeEmpty = normalizeWorkspaceNavigationBootstrap({
    items: [{ id: "ws-guest", name: "Guest", access: { role: "guest" } }],
    capabilities: { navigation_ui: true },
  }, "");
  assert.equal(authoritativeEmpty.enabled, false);
  assert.equal(authoritativeEmpty.authoritative, true);
  const collaborativeGuest = normalizeWorkspaceNavigationBootstrap({
    items: [{ id: "ws-guest", name: "Shared", access: { role: "guest" } }],
    capabilities: { navigation_ui: true, collaborative_iam_sharing: true },
  }, "ws-guest");
  assert.equal(collaborativeGuest.enabled, true);
  assert.equal(collaborativeGuest.collaborativeSharing, true);
  assert.equal(collaborativeGuest.databaseFavoriteSupported, false);
  assert.deepEqual(collaborativeGuest.workspaces.map(item => item.id), ["ws-guest"]);
  const databaseFavoritesEnabled = normalizeWorkspaceNavigationBootstrap({
    items: [{ id: "ws-guest", name: "Shared", access: { role: "guest" } }],
    capabilities: { navigation_ui: true, collaborative_iam_sharing: true,
      collaborative_database_favorites: true },
  }, "ws-guest");
  assert.equal(databaseFavoritesEnabled.databaseFavoriteSupported, true);

  const normalized = normalizeWorkspaceNavigationSnapshot(__snapshot("ws-a"), "ws-a");
  assert.equal(normalized.sidebar.width_px, 312);
  assert.equal(normalized.favoriteKeys.has(navigationFavoriteKey("owner-a", "page-a")), true);
  const mixedFavorites = normalizeWorkspaceNavigationSnapshot({
    ...__snapshot("ws-a"),
    favorites: [
      { source_owner_subject_id: "owner-a", source_page_id: "page-a", position: 0 },
      { source_owner_subject_id: "owner-a", source_page_id: "page-a", database_block_id: "db-a", position: 1 },
    ],
  }, "ws-a");
  assert.equal(mixedFavorites.favoriteKeys.size, 1);
  assert.equal(mixedFavorites.databaseFavoriteKeys.has(navigationDatabaseFavoriteKey("owner-a", "page-a", "db-a")), true);
  assert.equal(mixedFavorites.favorites[1].database_block_id, "db-a");
  assert.throws(
    () => normalizeWorkspaceNavigationSnapshot({
      ...__snapshot("ws-a"),
      favorites: [
        { source_owner_subject_id: "owner-a", source_page_id: "page-a", database_block_id: "db-a", position: 0 },
        { source_owner_subject_id: "owner-a", source_page_id: "page-a", database_block_id: "db-a", position: 1 },
      ],
    }, "ws-a"),
    /favorite is duplicated/,
  );
  assert.throws(
    () => normalizeWorkspaceNavigationSnapshot({
      ...__snapshot("ws-a"),
      favorites: [{ source_owner_subject_id: "owner-a", source_page_id: "page-a", position: 1 }],
    }, "ws-a"),
    /favorite is invalid/,
  );
  assert.throws(
    () => normalizeWorkspaceNavigationSnapshot({ ...__snapshot("ws-a"), subject_id: "other" }, "ws-a"),
    /subject is invalid/,
  );
  const scopedPages = normalizeWorkspacePagesPayload({
    workspace_id: "ws-a",
    count: 1,
    degraded: false,
    degraded_sources: [],
    items: [{
      id: "page-a",
      title: "Synthetic A",
      owner_user_id: "owner-a",
      workspace_id: "ws-a",
      placement: { scope: "private", teamspace_id: "", binding_version: 1 },
    }],
  }, "ws-a");
  assert.deepEqual(scopedPages.map(item => item.id), ["page-a"]);
  assert.throws(
    () => normalizeWorkspacePagesPayload({
      workspace_id: "ws-b",
      count: 0,
      degraded: false,
      degraded_sources: [],
      items: [],
    }, "ws-a"),
    /identity is invalid/,
  );
  assert.throws(
    () => normalizeWorkspacePagesPayload({
      workspace_id: "ws-a",
      count: 2,
      degraded: false,
      degraded_sources: [],
      items: [
        scopedPages[0],
        { ...scopedPages[0] },
      ],
    }, "ws-a"),
    /item is invalid/,
  );

  let appliedWidth = 0;
  let appliedCollapsed = null;
  setSidebarWidth = (value, persist) => { appliedWidth = value; assert.equal(persist, false); };
  setSidebarCollapsed = (value, persist) => { appliedCollapsed = value; assert.equal(persist, false); };
  renderPageLists = () => {};
  applyNavigationSections = () => {};
  renderWorkspacePopover = () => {};
  State.navigation.workspaces = [{ id: "ws-a", name: "A", access: { role: "owner" } }];
  State.navigation.workspaceId = "ws-a";
  applyWorkspaceNavigationSnapshot(normalized);
  assert.equal(appliedWidth, 312);
  assert.equal(appliedCollapsed, true, "server snapshot must override local sidebar state");
  State.navigation.workspaces[0].access.role = "guest";
  applyWorkspaceNavigationSnapshot(normalized);
  assert.equal(appliedCollapsed, true, "guest role must not force open a saved collapsed sidebar");
  applyWorkspaceNavigationSnapshot({...normalized, sidebar: {...normalized.sidebar, collapsed:false}});
  assert.equal(appliedCollapsed, false, "guest saved expansion uses the same snapshot path");
  State.navigation.workspaces[0].access.role = "owner";
  applyWorkspaceNavigationSnapshot(normalized);

  State.user = { id: "tester" };
  State.favorites = new Set(["local-page"]);
  State.navigation.authoritative = false;
  State.navigation.enabled = false;
  assert.equal(pageIsFavorite({ id: "local-page", owner_user_id: "owner-a" }), true);
  State.navigation.authoritative = true;
  assert.equal(pageIsFavorite({ id: "local-page", owner_user_id: "owner-a" }), false);
  State.navigation.authoritative = true;
  State.navigation.enabled = true;
  State.navigation.favoriteKeys = normalized.favoriteKeys;
  assert.equal(pageIsFavorite({ id: "page-a", owner_user_id: "owner-a" }), true);
  assert.equal(pageIsFavorite({ id: "page-a", owner_user_id: "owner-b" }), false);

  const requests = [];
  let firstRequest = true;
  api = async (requestPath, options) => {
    requests.push({ requestPath, body: JSON.stringify(options.body) });
    if (firstRequest) {
      firstRequest = false;
      throw new TypeError("network uncertain");
    }
    return { ok: true };
  };
  const requestOptions = { method: "PUT", body: { operation_id: "stable-op", value: 1 } };
  await requestNavigationMutation("/navigation/favorite", requestOptions);
  assert.equal(requests.length, 2);
  assert.deepEqual(requests[0], requests[1], "network replay must preserve path and operation payload");
  let releaseCapability;
  let reservedRequestCalls = 0;
  refreshWorkspaceNavigationCapability = () => new Promise(resolve => { releaseCapability = resolve; });
  const reservedMutation = runNavigationMutation({
    optimistic: () => {},
    request: async () => { reservedRequestCalls += 1; },
    successMessage: "unused",
  });
  assert.equal(State.navigation.mutationReserved, true);
  const rejectedConcurrentMutation = await runNavigationMutation({
    optimistic: () => {},
    request: async () => { reservedRequestCalls += 1; },
    successMessage: "unused",
  });
  assert.equal(rejectedConcurrentMutation, false);
  releaseCapability(false);
  assert.equal(await reservedMutation, false);
  assert.equal(reservedRequestCalls, 0);
  const concurrentMutationRejected = (
    rejectedConcurrentMutation === false
    && State.navigation.mutationReserved === false
  );
  refreshWorkspaceNavigationCapability = async () => true;

  State.navigation.enabled = true;
  State.navigation.workspaceId = "ws-a";
  State.navigation.snapshot = normalized;
  State.navigation.sections = normalized.sections.map(item => ({ ...item }));
  State.navigation.favoriteKeys = new Set(normalized.favoriteKeys);
  State.navigation.favoritePositions = new Map(normalized.favoritePositions);
  State.navigation.sequence = 0;
  State.navigation.mutationInFlight = null;
  loadWorkspaceNavigationSnapshot = async () => {
    applyWorkspaceNavigationSnapshot(normalizeWorkspaceNavigationSnapshot({
      ...__snapshot("ws-a", 1),
      favorite_version: 1,
      favorites: [],
    }, "ws-a"));
    return true;
  };
  const staleError = new Error("stale");
  staleError.status = 409;
  await runNavigationMutation({
    optimistic: () => State.navigation.favoriteKeys.add(navigationFavoriteKey("owner-b", "page-b")),
    request: async () => { throw staleError; },
    successMessage: "unused",
  });
  assert.equal(State.navigation.favoriteKeys.size, 0, "409 must reload canonical favorites");

  applyWorkspaceNavigationSnapshot(normalized);
  loadWorkspaceNavigationSnapshot = async () => { throw new Error("reload unavailable"); };
  await runNavigationMutation({
    optimistic: () => State.navigation.favoriteKeys.clear(),
    request: async () => { throw staleError; },
    successMessage: "unused",
  });
  assert.equal(
    State.navigation.favoriteKeys.has(navigationFavoriteKey("owner-a", "page-a")),
    true,
    "failed canonical reload must rollback optimistic state",
  );
  const conflictReloadsCanonical = State.navigation.favoriteKeys.has(
    navigationFavoriteKey("owner-a", "page-a"),
  );

  const revokedError = new Error("revoked");
  revokedError.status = 404;
  loadWorkspaceNavigationSnapshot = async () => { throw revokedError; };
  await runNavigationMutation({
    optimistic: () => State.navigation.favoriteKeys.add(navigationFavoriteKey("owner-b", "page-b")),
    request: async () => { throw staleError; },
    successMessage: "unused",
  });
  const revokedFailClosed = (
    State.navigation.enabled === false
    && State.navigation.authoritative === true
    && State.navigation.favoriteKeys.size === 0
  );

  State.navigation.enabled = true;
  State.navigation.authoritative = true;
  State.navigation.workspaceId = "ws-a";
  applyWorkspaceNavigationSnapshot(normalized);

  api = async requestPath => {
    assert.equal(requestPath, "/api/notion/workspaces");
    return {
      items: [{ id: "ws-a", name: "A", access: { role: "owner" } }],
      capabilities: { navigation_ui: false },
    };
  };
  refreshWorkspaceNavigationCapability = originalCapabilityRefresh;
  const killSwitchStopsInteraction = !(await refreshWorkspaceNavigationCapability());
  assert.equal(State.navigation.enabled, false);

  loadWorkspaceNavigationSnapshot = originalLoadWorkspaceNavigationSnapshot;
  refreshWorkspaceNavigationCapability = async () => true;

  const resolvers = new Map();
  const applied = [];
  api = requestPath => new Promise(resolve => resolvers.set(requestPath + ":" + resolvers.size, resolve));
  applyWorkspaceNavigationSnapshot = item => applied.push(item.preference_version);
  State.navigation.enabled = true;
  State.navigation.workspaceId = "ws-a";
  State.navigation.sequence = 0;
  const oldLoad = loadWorkspaceNavigationSnapshot("ws-a");
  const newLoad = loadWorkspaceNavigationSnapshot("ws-a");
  const entries = [...resolvers.values()];
  entries[1](__snapshot("ws-a", 2));
  assert.equal(await newLoad, true);
  entries[0](__snapshot("ws-a", 1));
  assert.equal(await oldLoad, false);
  assert.deepEqual(applied, [2], "late navigation responses must not overwrite the newest snapshot");

  State.navigation.workspaces = [
    { id: "ws-a", name: "A", access: { role: "owner" } },
    { id: "ws-b", name: "B", access: { role: "member" } },
  ];
  State.navigation.workspaceId = "ws-a";
  State.navigation.snapshot = normalized;
  State.navigation.mutationInFlight = null;
  let sourceLoads = 0;
  flushAllOpenDrafts = async () => false;
  loadWorkspaceNavigationSnapshot = async () => { sourceLoads += 1; return true; };
  await switchCanonicalWorkspace("ws-b");
  assert.equal(State.navigation.workspaceId, "ws-a");
  assert.equal(sourceLoads, 0, "failed draft flush must stop workspace navigation I/O");

  flushAllOpenDrafts = async () => true;
  State.navigation.preferenceTimer = setTimeout(() => {}, 10000);
  persistNavigationPreferences = async () => false;
  await switchCanonicalWorkspace("ws-b");
  assert.equal(State.navigation.workspaceId, "ws-a");
  assert.equal(sourceLoads, 0, "failed preference flush must stop workspace navigation I/O");
  const navigationSwitchFlushesHold = (
    State.navigation.workspaceId === "ws-a" && sourceLoads === 0
  );

  const originalQuerySelector = document.querySelector;
  const originalLoadShares = loadShares;
  const fallbackNode = originalQuerySelector.call(document, "#sharing-test-fallback");
  const shareNodes = new Map([
    ["#shareUser", { ...fallbackNode, value: "member-one" }],
    ["#sharePrincipalType", { ...fallbackNode, value: "subject" }],
    ["#shareRole", { ...fallbackNode, value: "edit_content" }],
    ["#shareInherit", { ...fallbackNode, checked: true }],
  ]);
  document.querySelector = selector => shareNodes.get(selector) || fallbackNode;
  State.activePageId = "page / alpha";
  State.navigation.workspaceId = "workspace / alpha";
  State.navigation.collaborativeSharing = true;
  State.shareAccess = { workspace_epoch: 7, document_epoch: 11 };
  const collaborationRequests = [];
  let finishPersistedRemoval;
  State.liveCollaboration.persistenceChain = new Promise(resolve => {
    finishPersistedRemoval = resolve;
  });
  api = async (requestPath, options = {}) => {
    collaborationRequests.push({ requestPath, options });
    return {};
  };
  loadShares = async () => {};
  const pendingShare = sharePage();
  await Promise.resolve();
  assert.equal(
    collaborationRequests.length,
    0,
    "a new access epoch must wait for durable removal of the acknowledged outbox entry",
  );
  finishPersistedRemoval();
  await pendingShare;
  State.liveCollaboration.persistenceChain = Promise.resolve();
  await revokeShare("member-one", "subject");
  assert.equal(
    collaborationRequests[0].requestPath,
    "/api/notion/workspaces/workspace%20%2F%20alpha/pages/page%20%2F%20alpha/shares",
  );
  assert.equal(collaborationRequests[0].options.method, "POST");
  assert.deepEqual(
    JSON.parse(JSON.stringify(collaborationRequests[0].options.body)),
    {
      principal_type: "subject",
      principal_id: "member-one",
      role: "edit_content",
      inherit_to_descendants: true,
      expected_workspace_epoch: 7,
      expected_document_epoch: 11,
      operation_id: collaborationRequests[0].options.body.operation_id,
    },
  );
  assert.match(
    collaborationRequests[0].options.body.operation_id,
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  assert.equal(
    collaborationRequests[1].requestPath,
    "/api/notion/workspaces/workspace%20%2F%20alpha/pages/page%20%2F%20alpha/shares/subject/member-one",
  );
  assert.equal(collaborationRequests[1].options.method, "DELETE");
  assert.equal(collaborationRequests[1].options.body.expected_workspace_epoch, 7);
  assert.equal(collaborationRequests[1].options.body.expected_document_epoch, 11);
  const pageShareUsesIamEpochs = collaborationRequests.length === 2;
  document.querySelector = originalQuerySelector;
  loadShares = originalLoadShares;

  globalThis.__navigationProbe = {
    disabledByDefault: !disabled.enabled,
    guestExcluded: enabled.workspaces.every(item => item.access.role !== "guest"),
    staleResponseFenced: applied.length === 1 && applied[0] === 2,
    exactNetworkReplay: requests.length === 2 && requests[0].body === requests[1].body,
    draftFlushStopsSwitch: navigationSwitchFlushesHold,
    serverSnapshotWins: appliedWidth === 312 && appliedCollapsed === true,
    preferenceFlushStopsSwitch: navigationSwitchFlushesHold,
    conflictReloadsCanonical,
    revokedFavoriteFailsClosed: revokedFailClosed,
    killSwitchStopsInteraction,
    concurrentMutationRejected,
    collaborativeGuestIncluded: collaborativeGuest.enabled,
    pageShareUsesIamEpochs,
  };
})().catch(error => { globalThis.__navigationProbeError = error; });
`;

context.assert = assert;
context.__snapshot = snapshot;
vm.runInNewContext(`${source}\n${probe}`, context, { filename: "notion.js" });

(async () => {
  for (let index = 0; index < 100 && !context.__navigationProbe && !context.__navigationProbeError; index += 1) {
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  if (context.__navigationProbeError) throw context.__navigationProbeError;
  for (const field of [
    "disabledByDefault",
    "guestExcluded",
    "staleResponseFenced",
    "exactNetworkReplay",
    "draftFlushStopsSwitch",
    "serverSnapshotWins",
    "preferenceFlushStopsSwitch",
    "conflictReloadsCanonical",
    "revokedFavoriteFailsClosed",
    "killSwitchStopsInteraction",
    "concurrentMutationRejected",
    "collaborativeGuestIncluded",
    "pageShareUsesIamEpochs",
  ]) {
    assert.equal(context.__navigationProbe[field], true, `${field} must hold`);
  }

  const html = fs.readFileSync(path.join(__dirname, "..", "notion_static", "index.html"), "utf8");
  const css = fs.readFileSync(path.join(__dirname, "..", "notion_static", "notion.css"), "utf8");
  for (const section of ["favorites", "recents", "teamspaces", "shared", "private"]) {
    assert.match(html, new RegExp(`data-navigation-section=["']${section}["']`));
    assert.match(html, new RegExp(`data-navigation-toggle=["']${section}["']`));
  }
  assert.match(html, /navigationSyncStatus[^>]+role="status"[^>]+aria-live="polite"/);
  assert.match(html, /sidebarResize[^>]+role="separator"[^>]+aria-orientation="vertical"[^>]+tabindex="0"/);
  for (const id of [
    "workspaceMemberSection",
    "workspaceMemberUser",
    "workspaceMemberRole",
    "workspaceMemberBtn",
    "shareAccessExplanation",
    "sharePrincipalType",
    "shareInherit",
  ]) {
    assert.match(html, new RegExp(`id=["']${id}["']`));
  }
  assert.match(html, /newPageMenu[^>]+role="menu"[^>]+aria-label="새 페이지 유형"/);
  assert.match(html, /movePageMenu[^>]+role="dialog"[^>]+aria-label="페이지 옮길 곳 선택"/);
  assert.match(html, /workspaceSidebarGroup[^>]+role="group"[^>]+tabindex="0"[^>]+aria-label="워크스페이스 사이드바"/);
  assert.match(source, /visitedWorkspaceIds:\s*new Set\(\)/);
  assert.match(source, /new Set\(\[bootstrap\.workspaceId\]\)/);
  assert.match(source, /switched && firstVisit[\s\S]*?#workspaceSidebarGroup[\s\S]*?#workspaceName/);
  assert.match(source, /navigation-settings-row[^`]+tabindex="-1"/);
  assert.match(source, /setAttribute\("aria-busy", active \? "true" : "false"\)/);
  assert.match(source, /\["ArrowLeft", "ArrowRight", "Home"\]/);
  assert.match(source, /requestAnimationFrame\(\(\) => \{[\s\S]*?data-navigation-focus/);
  assert.match(source, /sidebarNewBtn"\)\.addEventListener\("click", event => openNewPageMenu/);
  // The current menu passes its destination and template to createPage.
  // Its execution and stale-action guards are tested in test_notion_workspace_home.js.
  assert.match(source, /id="movePageSearch"[\s\S]*?placeholder="페이지 옮길 곳 선택"/);
  const moveMenuSource = source.slice(source.indexOf("function openMovePageDialog("), source.indexOf("async function performArchivePage("));
  assert.match(moveMenuSource, /await movePageSafely\(page\.id, body\)/);
  assert.match(moveMenuSource, /data-move-parent[\s\S]*?moveOrRefresh\(\{parent_id:/);
  assert.doesNotMatch(source, /id="movePageParent"/);
  assert.doesNotMatch(source, /You're a guest|Add a comment\.\.\.|Set up workspace|"Shared"|"Library"|"Help"/);
  assert.match(html, /id="homeView"[^>]+aria-label="홈"/);
  assert.match(source, /data-inbox-group="\$\{group\.key\}"/);
  assert.match(source, /label: "오늘"[\s\S]*?label: "어제"[\s\S]*?label: "이전"/);
  assert.equal(css.includes('content: "7";'), false);
  assert.equal(css.includes('content: "2분";'), false);
  for (const id of [
    "pageInheritedShareUser",
    "pageInheritedShareRole",
    "pageInheritedShareInviteBtn",
    "pageInheritedSharePeople",
    "pageInheritedShareAdvancedBtn",
    "pageInheritedShareCopyBtn",
    "pageInheritedShareDetails",
  ]) assert.match(html, new RegExp(`id=["']${id}["']`));
  assert.match(source, /function renderPageInheritedShareSurface\(\)/);
  assert.match(source, /pageInheritedShareInviteBtn"\)\?\.addEventListener\("click", shareFromPageInheritedSurface\)/);
  assert.match(source, /data-page-share-revoke="\$\{escapeAttr\(item\.id\)\}"/);
  assert.match(source, /revokeShare\(button\.dataset\.pageShareRevoke, "subject"\)/);
  assert.doesNotMatch(css, /data-page-inherited-share="true"[^{}]+#sharePanel > :not\(\.page-inherited-share-prompt\)[^{]*\{[^}]*opacity:\s*0/);
  assert.match(css, /data-page-inherited-share="true"[^{}]+\.page-inherited-share-prompt\s*\{[^}]*pointer-events:\s*auto/);
  assert.doesNotMatch(css, /data-page-direct-guest-share="true"[^{}]*\{[^}]*right:\s*-91px/);
  assert.doesNotMatch(css, /data-page-direct-guest-share="true"[^{}]+\.page-editor-group\s*\{[^}]*transform:/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*?\.sidebar-resize\s*\{[\s\S]*?display:\s*none/);
  console.log("notion_navigation_ui: ok");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
