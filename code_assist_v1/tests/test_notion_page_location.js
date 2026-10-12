"use strict";
// UI-LOCATION-001: exercise actual header and move-menu code with current navigation data.
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function part(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return source.slice(a, b);
}
function fixture() {
  const nodes = new Map();
  let focused = "", closedPageMenus = 0, placed = 0;
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {id: id.slice(1), hidden: true, isConnected: true, attributes: {}, listeners: {},
      innerHTML: "", setAttribute(key, value) {this.attributes[key] = value;}, removeAttribute(key) {delete this.attributes[key];},
      addEventListener(type, listener) {this.listeners[type] = listener;},
      closest() {return null;}, querySelectorAll() {return [];}, focus() {focused = this.id;}});
    return nodes.get(id);
  };
  const page = {id: "p-a", workspace_id: "ws-a", scope: "own", can_edit: true, can_move: true, settings: {}};
  const workspace = {id: "ws-a", name: "A", access: {role: "owner"}};
  const state = {activePageId: page.id, pageData: {page}, pages: [{...page, placement: {scope: "private"}}],
    liveCollaboration: {}, workspaceAccess: {id: "ws-a", teamspaces: [{id: "team-a", name: "제품 개발"}]},
    navigation: {enabled: true, collaborativeSharing: true, workspaceId: "ws-a"}};
  const context = {State: state, $: node, selectedNavigationWorkspace: () => workspace,
    pageIsLocked: item => Boolean(item?.settings?.locked), iconSvg: name => `<svg data-icon="${name}"></svg>`, escapeHtml: String,
    escapeAttr: String, descendantPageIds: () => new Set(), ownPages: () => state.pages, iconScope: () => () => true,
    closeNewPageMenu() {}, closePageMenu() {closedPageMenus++;},
    window: {NotionUiInteraction: {place() {placed++;}}}};
  vm.createContext(context);
  vm.runInContext([
    part("function pageNavigationPlacement(", "function renderCollaborativeTeamspaceSidebar("),
    part("function pageCanMove(", "function pageCanCreateChild("),
    part("function closeMovePageMenu(", "function organizeTopbarPageMenu("),
    part("function openMovePageDialog(", "async function performArchivePage("),
    part("function pageLocationPresentation(", "async function goHome("),
  ].join("\n"), context);
  return {context, state, page, workspace, node, get focused() {return focused;}, get placed() {return placed;},
    get closedPageMenus() {return closedPageMenus;}, click() {context.openPageLocationMenu({currentTarget: node("#pageLocationBtn")});}};
}

test("UI-LOCATION-001 private placement describes location without making a sharing claim", () => {
  const f = fixture();
  f.context.renderPageLocation(f.page);
  const button = f.node("#pageLocationBtn");
  assert.equal(button.hidden, false);
  assert.equal(button.disabled, false);
  assert.match(button.innerHTML, /개인 페이지/);
  assert.doesNotMatch(button.title, /본인만|액세스 가능/);
  // An inherited/direct grant is independent of the page's sidebar location.
  f.page.access_sources = [{kind: "subject", id: "another-person"}];
  assert.equal(f.context.pageLocationPresentation(f.page).label, "개인 페이지");
});

test("UI-LOCATION-002 fresh placement and exact workspace metadata determine the label", () => {
  const f = fixture();
  f.page.placement = {scope: "private"};
  f.state.pages[0].placement = {scope: "teamspace", teamspace_id: "team-a"};
  assert.equal(f.context.pageLocationPresentation(f.page).label, "제품 개발");
  f.state.workspaceAccess.id = "old-workspace";
  assert.equal(f.context.pageLocationPresentation(f.page).label, "팀스페이스");
  f.workspace.access.role = "guest";
  assert.equal(f.context.pageLocationPresentation(f.page).label, "공유된 페이지");
  f.workspace.id = "ws-other";
  assert.equal(f.context.pageLocationPresentation(f.page), null);
});

test("UI-LOCATION-003 lock, permission loss, recovery and policy change prevent opening", () => {
  for (const change of [f => f.page.can_move = false, f => f.page.settings.locked = true,
    f => f.state.liveCollaboration.recoveryOnly = true, f => f.state.liveCollaboration.offlinePolicyBlocked = true]) {
    const f = fixture();
    f.context.renderPageLocation(f.page);
    change(f); // A previously enabled button must recheck authority at click time.
    f.click();
    assert.equal(f.placed, 0);
    assert.equal(f.node("#pageLocationBtn").disabled, true);
    assert.equal(f.node("#movePageMenu").hidden, true);
  }
});

test("UI-LOCATION-004 clicking opens current page move dialog, Escape returns focus", () => {
  const f = fixture();
  f.context.renderPageLocation(f.page);
  const anchor = f.node("#pageLocationBtn");
  let labelMarkup = anchor.innerHTML, replacedChildren = 0;
  Object.defineProperty(anchor, "innerHTML", {get() {return labelMarkup;}, set(value) {replacedChildren++; labelMarkup = value;}});
  f.click();
  assert.equal(replacedChildren, 0, "clicking the label must preserve its ancestry for the document outside-click handler");
  const menu = f.node("#movePageMenu"), button = f.node("#pageLocationBtn");
  assert.equal(menu.hidden, false);
  assert.equal(menu.movePageId, f.page.id);
  assert.equal(button.attributes["aria-expanded"], "true");
  assert.equal(f.focused, "movePageSearch");
  assert.equal(f.closedPageMenus, 1);
  let stopped = 0;
  menu.onkeydown({key: "Escape", isComposing: true});
  assert.equal(menu.hidden, false, "IME Escape does not close the move menu");
  menu.onkeydown({key: "Escape", target: f.node("#movePageResults"), preventDefault() {stopped++;}, stopPropagation() {stopped++;}});
  assert.equal(stopped, 2);
  assert.equal(menu.hidden, true);
  assert.equal(button.attributes["aria-expanded"], "false");
  assert.equal(f.focused, "pageLocationBtn");
  assert.equal(menu.onkeydown, null);
});

test("UI-LOCATION-003 direct menu and drop entry reject a locked page or lost move permission", () => {
  for (const change of [f => f.page.can_move = false, f => f.page.settings.locked = true]) {
    for (const initialMove of [null, {parent_id: "parent-b"}]) {
      const f = fixture(); change(f);
      f.context.openMovePageDialog(f.page, f.node("#pageLocationBtn"), initialMove);
      assert.equal(f.placed, 0);
      assert.equal(f.node("#movePageMenu").hidden, true);
      assert.equal(f.node("#movePageMenu").movePageRequest, undefined);
    }
  }
});

test("UI-LOCATION-005 second click toggles; page switch or refreshed permission closes stale menu", () => {
  const f = fixture();
  f.click(); f.click();
  assert.equal(f.node("#movePageMenu").hidden, true);
  f.click();
  f.page.can_move = false;
  f.context.renderPageLocation(f.page);
  assert.equal(f.node("#movePageMenu").hidden, true);
  f.page.can_move = true; f.click();
  f.state.activePageId = "p-b";
  f.context.renderPageLocation(f.page);
  assert.equal(f.node("#pageLocationBtn").hidden, true);
  assert.equal(f.node("#movePageMenu").hidden, true);
  f.state.activePageId = "";
  f.context.renderPageLocation(null);
  assert.equal(f.node("#pageLocationBtn").hidden, true);
});

test("UI-LOCATION-006 ordinary own/shared pages retain their capabilities; missing collaborative placement is not guessed", () => {
  const f = fixture();
  delete f.state.pages[0].placement;
  assert.equal(f.context.pageLocationPresentation(f.page), null);
  f.state.navigation.collaborativeSharing = false;
  assert.equal(f.context.pageLocationPresentation(f.page).label, "개인 페이지");
  f.page.scope = "shared";
  assert.equal(f.context.pageLocationPresentation(f.page).label, "공유된 페이지");
  assert.equal(f.context.pageLocationPresentation(f.page).canMove, false);
});
