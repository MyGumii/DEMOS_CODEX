"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const core = require("../notion_static/notion_editor_core.js");
const collaboration = require("../notion_static/notion_collaboration_editor.js");
const fileUpload = require("../notion_static/notion_file_upload.js");
const databaseBridge = require("../notion_static/notion_database_bridge.js");
const yjsBundlePath = path.join(__dirname, "..", "notion_static", "vendor", "yjs-13.6.32.bundle.js");
const yjsContext = {
  crypto: globalThis.crypto,
  TextEncoder,
  TextDecoder,
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  performance,
};
vm.runInNewContext(fs.readFileSync(yjsBundlePath, "utf8"), yjsContext, { filename: "yjs-13.6.32.bundle.js" });
const sourcePath = path.join(__dirname, "..", "notion_static", "notion.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/\bboot\(\);\s*$/, "");

const inertNode = {
  hidden: true,
  className: "",
  value: "",
  dataset: {},
  classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
  addEventListener() {},
  removeEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  removeAttribute() {},
  setAttribute() {},
  appendChild() {},
  focus() {},
};
const documentStub = {
  querySelector() { return inertNode; },
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement() { return { ...inertNode }; },
  documentElement: { dataset: {} },
  body: { classList: inertNode.classList },
};
const locationStub = { origin: "http://localhost", hostname: "localhost", pathname: "/workspace" };
const windowStub = {
  NotionEditorCore: core,
  NotionCollaborationEditor: collaboration,
  NotionFileUpload: fileUpload,
  NotionDatabaseBridge: databaseBridge,
  NotionLinkedDatabases: require("../notion_static/notion_linked_databases.js"),
  NotionDatabaseDates: require("../notion_static/notion_database_dates.js"),
  NotionYjs: yjsContext.NotionYjs,
  CSS: { escape: value => String(value) },
  location: locationStub,
  addEventListener() {},
  matchMedia() { return { matches: false }; },
};
const localStorageStub = {
  values: new Map(),
  getItem(key) { return this.values.get(key) ?? null; },
  setItem(key, value) { this.values.set(key, String(value)); },
};

const context = {
  console,
  document: documentStub,
  window: windowStub,
  location: locationStub,
  history: { pushState() {}, replaceState() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  localStorage: localStorageStub,
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  requestAnimationFrame: callback => callback(),
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  crypto: globalThis.crypto,
};
windowStub.document = documentStub;

const probe = `
  State.user = { id: "tester" };
  const probeBlock = EditorCore.createBlock("paragraph", { content: "hello" });
  State.pageData = { page: { id: "page", can_edit: true, settings: {} }, blocks: [probeBlock] };
  const probeEditor = { dataset: {}, closest() { return null; } };
  handleBlockEditorKeydown({
    key: "a", code: "KeyA", ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() {}, stopPropagation() {},
  }, probeEditor, probeBlock, 0);
  let compositionPrevented = false;
  probeEditor.dataset.imeComposing = "true";
  handleBlockEditorKeydown({
    key: "Enter", code: "Enter", keyCode: 229, isComposing: true,
    ctrlKey: false, metaKey: false, altKey: false, shiftKey: false,
    preventDefault() { compositionPrevented = true; }, stopPropagation() {},
  }, probeEditor, probeBlock, 0);
  const compositionBlockCount = State.pageData.blocks.length;
  State.pageData.page.title = "Alpha page";
  State.pageData.blocks = [EditorCore.createBlock("paragraph", { content: "alpha beta alpha" })];
  const pageFindMatchCount = collectPageFindMatches("alpha").length;

  const database = EditorCore.createBlock("table");
  State.pageData.blocks.push(database);
  const firstView = activeDatabaseView(database);
  database.props.filters = [{ property: "status", operator: "equals", value: "완료" }];
  database.props.property_visibility.status = false;
  syncActiveDatabaseView(database);
  const secondView = { id: "view_board", name: "보드", type: "board", filters: [], sorts: [] };
  database.props.views.push(secondView);
  activateDatabaseView(database, secondView.id);
  const groupedDatabase = EditorCore.createBlock("table", {
    props: {
      filters: [{ property: "title", operator: "contains", value: "UI-ROW" }],
      sorts: [{ property: "title", direction: "desc" }],
      group_property: "status",
      rows: [
        { title: "UI-ROW-A", status: "진행 중" },
        { title: "UI-ROW-B", status: "완료" },
        { title: "UI-ROW-C", status: "진행 중" },
      ],
    },
  });
  const groupedRows = groupedDatabaseRows(groupedDatabase);
  const related = EditorCore.createBlock("table", {
    props: {
      properties: [
        { id: "title", name: "Name", type: "title" },
        { id: "amount", name: "Amount", type: "number" },
      ],
      rows: [
        { title: "One", amount: 10, page_id: "related_one" },
        { title: "Two", amount: 20, page_id: "related_two" },
      ],
    },
  });
  const sourceDatabase = EditorCore.createBlock("table", {
    props: {
      properties: [
        { id: "title", name: "Name", type: "title" },
        { id: "related", name: "Related", type: "relation", relation_database_block_id: related.id, relation_multiple: true },
        { id: "total", name: "Total", type: "rollup", rollup_relation_property: "related", rollup_target_property: "amount", rollup_calculation: "sum" },
      ],
      rows: [{ title: "Source", related: ["related_one", "related_two"], page_id: "source_one" }],
    },
  });
  State.pageData.blocks = [sourceDatabase, related];
  const rollupProperty = databaseProperties(sourceDatabase).find(property => property.id === "total");
  const dashboardProbe = EditorCore.createBlock("dashboard", { content: "Dashboard", props: { rows: [] } });
  const formProbe = EditorCore.createBlock("form", { content: "Form", props: { rows: [] } });
  const feedProbe = EditorCore.createBlock("feed", { content: "Feed", props: { rows: [] } });
  const mapProbe = EditorCore.createBlock("map", { content: "Map", props: { properties: [{ id: "title", name: "Name", type: "title" }, { id: "place", name: "Place", type: "place" }], rows: [] } });
  const calendarProbe = EditorCore.createBlock("calendar", { content: "Calendar", props: { month: "2026-08", rows: [{ title: "Range", date: { start: "2026-08-02", end: "2026-08-04" } }] } });
  const tabsProbe = EditorCore.createBlock("tabs");
  const navigationProbe = EditorCore.createBlock("table", { props: { rows: [{ title: "One", page_id: "nav_one" }, { title: "Two", page_id: "nav_two" }] } });
  State.activePageId = "page";
  State.pageData.blocks = [navigationProbe];
  State.sidePeek = { pageId: "nav_one", data: { page: { id: "nav_one" } }, databaseContext: { sourcePageId: "page", blockId: navigationProbe.id, rowIndex: 0 } };
  const sidePeekNavigation = sidePeekDatabaseNavigation();
  State.pageData.blocks = [sourceDatabase, related];
  applyColorTheme("dark");
  let outlookModalMarkup = "";
  openModal = (_title, body) => { outlookModalMarkup = body; };
  State.outlook = {
    enabled: true,
    connected: true,
    connector: "microsoft_graph",
    settings: {
      connector: "microsoft_graph",
      sync_interval_seconds: 300,
      daily_digest_enabled: true,
      daily_digest_time: "09:00",
      browser_notifications: false,
    },
    connector_status: {
      available: true,
      authenticated: true,
      account: { display_name: "Graph Tester", email: "tester@example.com" },
      configuration: { configured: true, single_tenant: true, permissions: "Delegated Mail.Read" },
      inbox_count: 42,
      unread_count: 7,
    },
    processed_count: 3,
    ai_provider: "hynix_gemma4",
  };
  renderOutlookSettingsModal(State.outlook);
  const hostPreviousState = {
    activePageId: State.activePageId,
    pageData: State.pageData,
    activeBlockId: State.activeBlockId,
    editRevision: State.editRevision,
    serverRevision: State.serverRevision,
    dirty: State.dirty,
    history: State.history,
  };
  const hostPreviousTitle = document.querySelector("#pageTitle").value;
  const hostPreviousRenderBlocks = renderBlocks;
  const hostPreviousRenderBreadcrumb = renderBreadcrumb;
  renderBlocks = () => {};
  renderBreadcrumb = () => {};
  State.activePageId = "host-page";
  State.pageData = {
    page: { id: "host-page", title: "Host local", can_edit: true, settings: {} },
    blocks: [EditorCore.createBlock("paragraph", { id: "host-block", content: "local" })],
  };
  State.activeBlockId = "host-block";
  State.editRevision = 10;
  State.serverRevision = 20;
  State.dirty = false;
  document.querySelector("#pageTitle").value = "Host local";
  const hostSnapshot = EditorHost.readDocument();
  hostSnapshot.blocks[0].content = "caller mutation";
  const hostCloneIsolated = State.pageData.blocks[0].content === "local";
  const hostEvents = [];
  const unsubscribeHost = EditorHost.subscribe(event => hostEvents.push(event));
  markDirty();
  clearTimeout(State.saveTimer);
  clearTimeout(State.historyTimer);
  State.saveTimer = null;
  State.historyTimer = null;
  const hostLocalMutationEvent = hostEvents.some(event => event.type === "local-mutation" && event.edit_revision === 11);
  State.dirty = false;
  EditorHost.beginComposition("host-block");
  const hostDeferredResult = EditorHost.applyRemoteProjection({
    page_id: "host-page",
    title: "Host remote",
    blocks: [EditorCore.createBlock("paragraph", { id: "host-block", content: "remote" })],
    server_revision: 21,
  });
  const hostCompositionDeferred = hostDeferredResult.deferred === true && State.pageData.page.title === "Host local";
  EditorHost.endComposition("host-block");
  const hostProjectionApplied = State.pageData.page.title === "Host remote"
    && State.pageData.blocks[0].content === "remote"
    && State.serverRevision === 21;
  const hostSequenceMonotonic = hostEvents.every((event, index) => index === 0 || event.sequence > hostEvents[index - 1].sequence);
  const hostDescriptor = Object.getOwnPropertyDescriptor(window, "NotionEditorHost");
  const hostPublicSurface = window.NotionEditorHost === EditorHost
    && hostDescriptor?.writable === false
    && hostDescriptor?.configurable === false
    && !Object.keys(EditorHost).some(key => /state|binding/i.test(key));
  unsubscribeHost();
  State.activePageId = hostPreviousState.activePageId;
  State.pageData = hostPreviousState.pageData;
  State.activeBlockId = hostPreviousState.activeBlockId;
  State.editRevision = hostPreviousState.editRevision;
  State.serverRevision = hostPreviousState.serverRevision;
  State.dirty = hostPreviousState.dirty;
  State.history = hostPreviousState.history;
  document.querySelector("#pageTitle").value = hostPreviousTitle;
  renderBlocks = hostPreviousRenderBlocks;
  renderBreadcrumb = hostPreviousRenderBreadcrumb;

  globalThis.__probeResult = {
    ordinaryKeyHandled: true,
    compositionKeyIgnored: !compositionPrevented && compositionBlockCount === 1,
    keydownContainsColumnAction: handleBlockEditorKeydown.toString().includes("remove_column_layout"),
    blockActionContainsColumnAction: handleBlockAction.toString().includes("remove_column_layout"),
    firstViewFilter: firstView.filters[0]?.value || "",
    firstViewStatusVisible: firstView.property_visibility.status,
    secondViewStatusVisible: databasePropertyVisible(database, databaseProperties(database).find(property => property.id === "status")),
    activeViewId: database.props.active_view_id,
    activeViewType: database.props.view,
    groupedRowCount: groupedRows.reduce((count, group) => count + group.rows.length, 0),
    groupedRowOrder: groupedRows.flatMap(group => group.rows.map(entry => entry.item.title)).join(","),
    groupCount: groupedRows.length,
    rollupSum: calculateDatabaseRollup(sourceDatabase, rollupProperty, sourceDatabase.props.rows[0]),
    relationOptionCount: databaseRelationOptions(databaseProperties(sourceDatabase).find(property => property.id === "related")).length,
    dashboardRenders: dashboardBlockHtml(dashboardProbe, true).includes("dashboard-grid"),
    formRenders: formBlockHtml(formProbe, true).includes("data-database-form"),
    feedRenders: feedBlockHtml(feedProbe, true).includes("database-feed"),
    mapRenders: mapBlockHtml(mapProbe, true).includes("database-map"),
    colorTheme: document.documentElement.dataset.theme,
    persistedTheme: localStorage.getItem("notion.colorTheme"),
    calendarRangeOccurrences: (calendarBlockHtml(calendarProbe, true).match(/Range/g) || []).length,
    pageFindMatchCount,
    sidePeekNavigationCount: sidePeekNavigation.rows.length,
    sidePeekNavigationPosition: sidePeekNavigation.position,
    tabsRenderCount: (tabsBlockHtml(tabsProbe, true).match(/data-tab-select/g) || []).length,
    outlookConnectionLabel: outlookConnectionLabel(State.outlook),
    outlookHasControls: ["outlookToggleBtn", "outlookSaveBtn", "outlookSyncNowBtn", "outlookDigestNowBtn"].every(id => outlookModalMarkup.includes(id)),
    outlookHasDailyTime: outlookModalMarkup.includes('value="09:00"'),
    safeRichTextHttps: normalizeSafeRichTextUrl("https://example.test/path"),
    safeRichTextBareDomain: normalizeSafeRichTextUrl("example.test/path"),
    blockedRichTextJavascript: normalizeSafeRichTextUrl("javascript:alert(1)"),
    blockedRichTextObfuscatedJavascript: normalizeSafeRichTextUrl("java\\nscript:alert(1)"),
    blockedRichTextData: normalizeSafeRichTextUrl("data:text/html,unsafe"),
    outlookExplainsReadOnly: outlookModalMarkup.includes("읽기 전용"),
    outlookExplainsSingleTenant: outlookModalMarkup.includes("Single-tenant"),
    hostCloneIsolated,
    hostLocalMutationEvent,
    hostCompositionDeferred,
    hostProjectionApplied,
    hostSequenceMonotonic,
    hostPublicSurface,
  };
`;

vm.runInNewContext(`${source}\n${probe}`, context, { filename: "notion.js" });
const result = context.__probeResult;
assert.equal(result.ordinaryKeyHandled, true, "ordinary editor input must not throw");
assert.equal(result.compositionKeyIgnored, true, "IME keydown must not split a block or run editor shortcuts");
assert.equal(result.hostCloneIsolated, true, "the public host returns detached document snapshots");
assert.equal(result.hostLocalMutationEvent, true, "legacy markDirty emits through the public host boundary");
assert.equal(result.hostCompositionDeferred, true, "remote projections wait for IME composition end");
assert.equal(result.hostProjectionApplied, true, "the host applies a projection without direct external State access");
assert.equal(result.hostSequenceMonotonic, true, "host events have a deterministic monotonic sequence");
assert.equal(result.hostPublicSurface, true, "window exposes only the immutable host adapter, not private bindings");
assert.match(source, /editor\.addEventListener\("compositionstart",[\s\S]*?EditorHost\.beginComposition\(block\.id \|\| ""\)/);
assert.match(source, /editor\.addEventListener\("compositionend",[\s\S]*?EditorHost\.endComposition\(block\.id \|\| ""\)/);
assert.match(source, /EditorHost\.notifyLifecycle\("document-loaded", \{ page_id: State\.activePageId \}\)/);
assert.equal(result.keydownContainsColumnAction, false, "block actions must not leak into the keyboard handler");
assert.equal(result.blockActionContainsColumnAction, true, "column removal belongs to the block action handler");
assert.equal(result.firstViewFilter, "완료", "each database view stores its own filters");
assert.equal(result.firstViewStatusVisible, false, "property visibility is saved per view");
assert.equal(result.secondViewStatusVisible, true, "a different view keeps its own property visibility");
assert.equal(result.activeViewId, "view_board");
assert.equal(result.activeViewType, "board");
assert.equal(result.groupedRowCount, 3, "grouping must not mutate or drop source rows");
assert.equal(result.groupedRowOrder, "UI-ROW-C,UI-ROW-A,UI-ROW-B", "filter, sort, and group order is deterministic");
assert.equal(result.groupCount, 2, "status grouping renders one section per visible value");
assert.equal(result.rollupSum, "30", "rollups calculate from related database rows");
assert.equal(result.relationOptionCount, 2, "relations are scoped to their target database");
assert.equal(result.dashboardRenders, true);
assert.equal(result.formRenders, true);
assert.equal(result.feedRenders, true);
assert.equal(result.mapRenders, true);
assert.equal(result.colorTheme, "dark");
assert.equal(result.persistedTheme, "dark");
assert.equal(result.calendarRangeOccurrences, 3, "calendar date ranges span every included day");
assert.equal(result.pageFindMatchCount, 3, "page find searches the title and every occurrence in block text");
assert.equal(result.sidePeekNavigationCount, 2, "database side peek navigation follows visible rows");
assert.equal(result.sidePeekNavigationPosition, 0);
assert.equal(result.tabsRenderCount, 2, "tabs render one selectable control per section");
assert.equal(result.outlookConnectionLabel, "연결됨");
assert.equal(result.outlookHasControls, true, "Outlook settings expose connect, save, sync, and digest controls");
assert.equal(result.outlookHasDailyTime, true, "Outlook settings render the daily digest time");
assert.equal(result.safeRichTextHttps, "https://example.test/path");
assert.equal(result.safeRichTextBareDomain, "https://example.test/path");
assert.equal(result.blockedRichTextJavascript, "");
assert.equal(result.blockedRichTextObfuscatedJavascript, "");
assert.equal(result.blockedRichTextData, "");
assert.equal(result.outlookExplainsReadOnly, true, "Outlook settings explain that mailbox access is read-only");
assert.equal(result.outlookExplainsSingleTenant, true, "Outlook settings explain the single-tenant boundary");

const html = fs.readFileSync(path.join(__dirname, "..", "notion_static", "index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "..", "notion_static", "notion.css"), "utf8");
assert.match(html, /id="sidePeek"[^>]+role="region"[^>]+aria-label="사이드 보기"/);
assert.match(html, /id="sidePeekBody"[^>]+role="region"[^>]+tabindex="-1"/);
assert.match(html, /id="inspector"[^>]+role="dialog"[^>]+aria-modal="false"[^>]+aria-labelledby="drawerTitle"[^>]+tabindex="-1"/);
assert.match(html, /id="contextPanel"[^>]+role="region"[^>]+aria-label="Second Brain 세션 컨텍스트"[^>]+tabindex="-1"/);
assert.match(html, /id="sidebarRestoreBtn"[^>]+aria-label="사이드바 열기"/);
assert.match(html, /id="collapseBtn"[^>]+aria-label="사이드바 접기"/);
assert.match(html, /id="homeNavBtn"[^>]+role="tab"[^>]+aria-selected="true"/, "sidebar navigation exposes the selected tab independently of the main destination");
assert.match(html, /id="skipToContent"[^>]+href="#workspaceMain"/);
assert.match(html, /id="workspaceMain"[^>]+class="workspace-main"[^>]+tabindex="-1"/);
assert.match(html, /id="pageTitle"[^>]+aria-label="페이지 제목"/);
assert.match(html, /id="pageEditorGroup"[^>]+role="group"[^>]+aria-label="페이지 편집기"[^>]+tabindex="-1"/);
assert.match(html, /id="searchResults"[^>]+role="listbox"[^>]+aria-label="검색 결과"/);
assert.match(html, /id="drawerCloseBtn"[^>]+aria-label="닫기"/);
assert.match(html, /id="blockMenu"[^>]+role="dialog"[^>]+aria-label="블록 메뉴"/);
assert.match(source, /class="menu-search" role="combobox"[^>]+aria-controls="blockMenuOptions"[^>]+aria-expanded="true"/);
assert.match(source, /data-menu-empty/);
assert.match(source, /closeBlockMenu\(true\)/, "slash-menu Escape restores focus to its source block");
assert.match(source, /const PageFocusPolicy = Object\.freeze\(\{[\s\S]*?PAGE_CANVAS: "page-canvas"[\s\S]*?PAGE_EDITOR_GROUP: "page-editor-group"[\s\S]*?PAGE_TITLE: "page-title"[\s\S]*?PRESERVE: "preserve"/);
assert.match(source, /async function openPage\(pageId, push = true, focusPolicy = PageFocusPolicy\.PAGE_CANVAS\)/);
assert.match(source, /btn\.setAttribute\("role", "option"\);\s*btn\.setAttribute\("aria-selected", item\.id === State\.activeSearchPageId \? "true" : "false"\);/);
// Search dispatch changed when independent databases were added. Verify the
// actual destination and focus behavior instead of the spelling of a call.
{
  const originalQuery = documentStub.querySelector;
  const originalClose = context.closeSearchOverlay, originalPage = context.openPage;
  const originalDatabase = context.openIndependentDatabase;
  const searchOverlay = {hidden: false}, calls = [];
  documentStub.querySelector = selector => selector === '#searchOverlay' ? searchOverlay : originalQuery(selector);
  context.closeSearchOverlay = restore => calls.push(['close', restore]);
  context.openPage = (...args) => calls.push(['page', ...args]);
  context.openIndependentDatabase = (...args) => calls.push(['database', ...args]);
  vm.runInContext(`const searchRuntimeBefore = {
    items:State.searchItems, pending:State.searchPending, workspace:State.navigation.workspaceId
  }; State.searchPending=false;State.navigation.workspaceId='search-workspace';
  State.searchItems=[{id:'destination-page'},{id:'database:target',kind:'database',database_id:'target'}];`, context);
  context.openSearchItem({id:'destination-page'});
  assert.deepEqual(calls,[['close',false],['page','destination-page',true,'preserve']],
    'pointer result opening closes search without stealing the destination focus');
  calls.length=0;
  context.openSearchItem({id:'database:target',kind:'database',database_id:'target'});
  assert.deepEqual(calls,[['close',false],['database','search-workspace','target']],
    'database results retain their current authorized workspace destination');
  calls.length=0;
  context.openSearchItem({id:'stale-page'});
  vm.runInContext('State.searchPending=true;', context);
  context.openSearchItem({id:'destination-page'});
  vm.runInContext('State.searchPending=false;', context);
  searchOverlay.hidden=true;
  context.openSearchItem({id:'destination-page'});
  assert.deepEqual(calls,[], 'stale, pending and closed-overlay results cannot initiate navigation');
  vm.runInContext(`State.searchItems=searchRuntimeBefore.items;
    State.searchPending=searchRuntimeBefore.pending;
    State.navigation.workspaceId=searchRuntimeBefore.workspace;`, context);
  documentStub.querySelector=originalQuery;
  context.closeSearchOverlay=originalClose;context.openPage=originalPage;
  context.openIndependentDatabase=originalDatabase;
}
// Page editing alone must not expose sharing controls. Exercise role changes
// and focus selection on the actual runtime functions, including hidden inputs.
{
  const originalQuery = documentStub.querySelector;
  const nodes = new Map();
  for (const id of ["pageShareForm", "sharePrincipalType", "shareUser", "shareRole", "shareInherit", "shareBtn",
    "pageInheritedShareUser", "pageInheritedShareRole", "pageInheritedShareInviteBtn", "workspaceMemberUser"]) {
    nodes.set("#" + id, {hidden: false, disabled: false, value: "reader", visible: true,
      getClientRects() { return this.visible ? [{}] : []; }});
  }
  documentStub.querySelector = selector => nodes.get(selector) || null;
  const inspector = {name: "share dialog"};
  nodes.get("#workspaceMemberUser").visible = false;
  vm.runInContext("const shareControlPageBefore = State.pageData; State.pageData = {page:{can_edit:true,can_share:true}};", context);
  context.syncPageShareControls();
  assert.equal(nodes.get("#pageShareForm").hidden, false);
  assert.equal(context.drawerInitialFocus("share", inspector), nodes.get("#shareUser"));
  vm.runInContext("State.pageData.page.can_share = false;", context);
  context.syncPageShareControls();
  assert.equal(nodes.get("#pageShareForm").hidden, true);
  for (const [selector, node] of nodes) {
    if (!["#pageShareForm", "#workspaceMemberUser"].includes(selector)) assert.equal(node.disabled, true, selector);
  }
  assert.equal(context.drawerInitialFocus("share", inspector), inspector, "readers start in the dialog, never an invite control");
  vm.runInContext("State.pageData.page.can_share = true;", context);
  context.syncPageShareControls();
  assert.equal(nodes.get("#pageShareForm").hidden, false, "returning to an owned page restores its controls");
  assert.equal(nodes.get("#shareBtn").disabled, false);
  nodes.get("#shareUser").visible = false;
  assert.equal(context.drawerInitialFocus("share", inspector), nodes.get("#pageInheritedShareUser"));
  nodes.get("#pageInheritedShareUser").visible = false;
  nodes.get("#workspaceMemberUser").visible = true;
  assert.equal(context.drawerInitialFocus("share", inspector), nodes.get("#workspaceMemberUser"));
  assert.equal(context.drawerInitialFocus("comments", inspector), inspector);
  vm.runInContext("State.pageData = shareControlPageBefore;", context);
  documentStub.querySelector = originalQuery;
}
assert.match(source, /if \(pageId\) await openPage\(pageId, false, PageFocusPolicy\.PRESERVE\);/, "startup preserves browser focus instead of forcing the page canvas");
assert.match(source, /search\?\.focus\(\);\s*filterBlockMenu\(query\);/);
assert.match(source, /function filterBlockMenu\(query\)[\s\S]*?refreshBlockMenuSelection\(\);/, "initial query filtering still establishes keyboard selection");
assert.match(source, /State\.selectedBlockIds = new Set\(/, "a second select-all promotes the text selection to block selection");
assert.match(source, /applyEditorHistory\(event\.shiftKey \? 1 : -1, \{\s*blockIndex: index,/);
assert.match(source, /function bindSidePeekSaveShortcut\(control\)/);
assert.match(source, /restoreSidePeekFocus\(focusSnapshot\)/, "side-peek saves restore the active editing control");
assert.match(source, /class="table-grid dynamic-table-grid" style="--database-columns:/, "database tables keep the observed visual container");
assert.doesNotMatch(source, /class="table-grid dynamic-table-grid" role="(?:table|grid)"/, "database tables do not invent reference-absent table or grid semantics");
assert.match(source, /data-db-group class="\$\{block\.props\?\.group_property \? "active" : ""\}"/, "database grouping exposes persisted active state");
assert.match(source, /class="database-view-tabs" role="tablist" aria-label="데이터베이스 보기"/, "database views expose a named tab list");
assert.match(source, /role="tab" aria-selected="\$\{activeView\?\.id === item\.id \? "true" : "false"\}"/, "database view tabs expose selected state");
assert.match(source, /function focusDatabaseViewTab\(block, viewId\)[\s\S]*?tab\?\.focus\(\{ preventScroll: true \}\);/, "database view switches restore focus after rerender");
assert.match(source, /renderBlocks\(\);\s*focusDatabaseViewTab\(block, viewId\);/, "view activation invokes focus restoration after rendering");
assert.match(source, /let PageFindReturnFocus = null;/, "in-page find tracks its invoking control");
assert.match(source, /const restoreInputFocus = document\.activeElement === input;[\s\S]*?input\.focus\(\{ preventScroll: true \}\);/, "in-page find keeps the query field focused while highlighting matches");
assert.match(source, /const returnFocus = PageFindReturnFocus;\s*PageFindReturnFocus = null;[\s\S]*?returnFocus\.focus\(\{ preventScroll: true \}\);/, "closing in-page find restores its invoking control");
assert.match(source, /class="comment-thread" role="group" aria-label="댓글 스레드" tabindex="-1" data-comment-thread=/, "comment threads expose a programmatic focus target");
assert.match(source, /function focusCommentResolveControl\(commentId\)[\s\S]*?thread\?\.querySelector\("\[data-comment-resolve\]"\)\?\.focus\(\{ preventScroll: true \}\);/, "reopened comments restore focus to the resolve control");
assert.match(source, /async function submitComment\([\s\S]*?await loadComments\(\);[\s\S]*?scrollIntoView\(\{block: "nearest"\}\);\s*setTimeout\(\(\) => input\.focus\(\{ preventScroll: true \}\), 0\);/, "new comments reveal the saved reply then return focus to the comment input");
assert.match(source, /const nextResolved = !item\.resolved;[\s\S]*?body: \{ resolved: nextResolved \},[\s\S]*?if \(nextResolved && State\.commentFilter === "open"\)[\s\S]*?focusCommentResolveControl\(commentId\);/, "resolved comments leave the open list while reopen restores the action focus");
// Drawers now accept selected-comment options. Exercise focus restoration on
// the real open/close functions without depending on their parameter spelling.
{
  const originalQuery = documentStub.querySelector, originalActive = documentStub.activeElement;
  const originalTimer = context.setTimeout;
  const stubNames = ['closeInlineComments', 'clearPendingSelectedComment', 'setActiveNav',
    'loadShares', 'loadPublicLink', 'loadSessions', 'loadComments'];
  const originals = new Map(stubNames.map(name => [name, context[name]]));
  const nodes = new Map(), focusCalls = [], pendingTimers = [];
  const makeNode = name => ({...inertNode, name, hidden: false, disabled: false, dataset: {},
    isConnected: true, getClientRects() { return this.hidden ? [] : [{}]; },
    focus(options) { documentStub.activeElement = this; focusCalls.push([name, options?.preventScroll]); }});
  for (const id of ['inspector', 'drawerTitle', 'drawerCloseBtn', 'shareAccessTooltip',
    'sharePanel', 'contextPanel', 'filesPanel', 'aiPanel', 'commentsPanel', 'historyPanel',
    'pageUpdatesPanel', 'databaseUpdatesPanel', 'publicShareSection', 'shareUser',
    'pageInheritedShareUser', 'workspaceMemberUser']) nodes.set('#' + id, makeNode(id));
  const inspector = nodes.get('#inspector'), trigger = makeNode('trigger'), second = makeNode('second');
  inspector.hidden = true;
  documentStub.activeElement = trigger;
  documentStub.querySelector = selector => nodes.get(selector) || null;
  context.setTimeout = callback => { pendingTimers.push(callback); return pendingTimers.length; };
  for (const name of stubNames) context[name] = () => Promise.resolve();
  vm.runInContext(`const drawerRuntimeBefore = {mode:State.drawerMode, returnFocus:DrawerReturnFocus,
    commentBlockId:State.commentBlockId, commentSelection:State.commentSelection};`, context);
  const runTimers = () => { while (pendingTimers.length) pendingTimers.shift()(); };
  context.openDrawer('share');
  runTimers();
  assert.equal(inspector.hidden, false);
  assert.deepEqual(focusCalls, [['shareUser', true]], 'sharing starts at a visible enabled invite input');
  documentStub.activeElement = second;
  context.openDrawer('comments', {commentBlockId:'target-block', commentSelection:null});
  runTimers();
  assert.equal(vm.runInContext('State.commentBlockId', context), 'target-block');
  assert.deepEqual(focusCalls.at(-1), ['inspector', true], 'switching drawer modes focuses the dialog');
  context.closeDrawer(true);
  runTimers();
  assert.equal(inspector.hidden, true);
  assert.deepEqual(focusCalls.at(-1), ['trigger', true], 'mode switches preserve the original invoking control');
  for (const id of ['shareUser', 'pageInheritedShareUser', 'workspaceMemberUser']) nodes.get('#' + id).disabled = true;
  documentStub.activeElement = trigger;
  context.openDrawer('share');
  runTimers();
  assert.deepEqual(focusCalls.at(-1), ['inspector', true], 'roles without usable invite inputs focus the dialog');
  const focusCount = focusCalls.length;
  context.closeDrawer();
  runTimers();
  assert.equal(focusCalls.length, focusCount, 'programmatic dismissal does not unexpectedly move focus');
  vm.runInContext(`State.drawerMode=drawerRuntimeBefore.mode;DrawerReturnFocus=drawerRuntimeBefore.returnFocus;
    State.commentBlockId=drawerRuntimeBefore.commentBlockId;State.commentSelection=drawerRuntimeBefore.commentSelection;`, context);
  documentStub.querySelector=originalQuery;documentStub.activeElement=originalActive;
  context.setTimeout=originalTimer;
  for (const [name, implementation] of originals) context[name]=implementation;
}
assert.match(html, /id="shareAccessTooltip"[^>]+role="dialog"[^>]+aria-label="페이지 액세스"[^>]+hidden/, "the share trigger has the observed focused-access tooltip surface");
assert.match(source, /function setShareAccessTooltipVisible\(visible\)[\s\S]*?tooltip\.hidden = !visible;/, "share access tooltip visibility is explicit");
assert.match(source, /if \(!\$\("#inspector"\)\.hidden\) \{\s*e\.preventDefault\(\);\s*closeDrawer\(true\);\s*return;/, "Escape closes the visible drawer and requests focus restoration");
assert.match(source, /function focusSessionContext\(\)[\s\S]*?panel\.focus\(\{ preventScroll: true \}\);/, "session attachment mutations expose a stable focus target");
assert.match(source, /setStatus\("Second Brain Agent 세션에 일시 첨부됨", "ok"\);\s*focusSessionContext\(\);/, "attaching a page restores focus to session context");
assert.match(source, /setStatus\("연결 해제됨", "ok"\);\s*focusSessionContext\(\);/, "detaching a page restores focus to session context");
assert.match(source, /const structuredTarget = row\?\.querySelector\("\[data-equation-trigger\], \.database-block \[data-db-search\]"\)/, "structured insertion focuses the equation trigger or database search input");
assert.match(source, /const databaseField = active\?\.dataset\?\.dbField/, "side-peek focus snapshots include typed database properties");
assert.match(source, /if \(sidePeekCanApply\) \{\s*const focusSnapshot = sidePeekFocusSnapshot\(\);[\s\S]*?renderSidePeek\(\);\s*restoreSidePeekFocus\(focusSnapshot\);/, "background row revalidation preserves side-peek focus");
assert.match(source, /async function openSidePeekAsFullPage\(/);
assert.match(source, /requestAnimationFrame\(\(\) => \$\("#sidePeekTitle"\)\?\.focus\(\{ preventScroll: true \}\)\);/, "side peek initially focuses its page title");
assert.match(source, /if \(!\(await closeSidePeek\(\)\)\) return;\s*if \(databaseBlockId\) await openSidebarDatabase\(targetPageId, databaseBlockId\);\s*else await openPage\(targetPageId, true, PageFocusPolicy\.PRESERVE\);\s*document\.activeElement\?\.blur\?\.\(\);/, "side-peek full-page transition requires a successful close, handles database pages and releases focus");
assert.match(source, /class="block-editor" role="textbox" aria-label="페이지 블록" aria-multiline="true"/, "page blocks expose a stable accessible name");
assert.match(source, /function visibleModalFocusables\(dialog\)[\s\S]*?function trapVisibleModalFocus\(event\)/, "modal focus trapping is centralized over visible controls");
assert.match(source, /document\.addEventListener\("keydown", e => \{\s*if \(e\.defaultPrevented[^\n]+\) return;\s*if \(trapVisibleModalFocus\(e\)\) return;/, "the document keyboard boundary respects IME before trapping active modal focus");
assert.match(source, /if \(e\.key === "Escape"\) \{\s*if \(!\$\("#sidePeekSharePopover"\)\.hidden\) \{[\s\S]*?return;\s*\}\s*if \(!\$\("#modalBg"\)\.hidden\) \{\s*e\.preventDefault\(\);\s*closeModal\(\);\s*return;/, "Escape dismisses the share popover before an unlocked modal through the focus-restoring close path");
assert.match(css, /\.notion-root \{\s*display: grid;\s*grid-template-columns: var\(--sidebar-width, 270px\) minmax\(0, 1fr\);/, "desktop navigation keeps its resizable 270px default");
// The mobile sidebar now overlays the document. Exercise its focus and dismissal
// behavior instead of freezing the previous stacked sidebar's CSS dimensions.
require("./test_notion_sidebar_shell.js");
assert.match(css, /@media \(max-width: 720px\) \{[\s\S]*?\.topbar \{[\s\S]*?overflow-x: auto;[\s\S]*?\.home-view,\s*body:not\(\.embedded-editor-mode\) #pageView \{\s*width: calc\(100% - 28px\);/, "narrow page sizing overrides desktop modes while keeping embedded editors independent");
assert.match(css, /@media \(max-width: 720px\) \{[\s\S]*?\.inspector \{\s*width: min\(348px, 100vw\);/, "the narrow inspector cannot exceed the viewport");
assert.match(css, /@media \(max-width: 820px\) \{[\s\S]*?\.side-peek \{\s*width: 100vw;\s*min-width: 0;/, "side peek drops its desktop minimum width before the narrow breakpoint");
assert.match(css, /:is\([\s\S]*?\):focus-visible \{\s*outline: 2px solid var\(--blue\) !important;\s*outline-offset: 2px !important;/, "keyboard focus receives a visible two-pixel indicator across native and composite controls");
assert.match(css, /@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?scroll-behavior: auto !important;[\s\S]*?animation-duration: \.01ms !important;[\s\S]*?transition-duration: \.01ms !important;/, "reduced-motion removes animated and smooth transitions");
assert.match(css, /@media \(forced-colors: active\) \{[\s\S]*?:focus-visible \{\s*outline-color: Highlight !important;/, "forced-colors preserves a system focus highlight");

console.log("notion_ui_runtime: ok");
