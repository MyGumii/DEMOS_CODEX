"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const slice = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));

function node() {
  const classes = new Set();
  return {style: {}, dataset: {}, attributes: {}, listeners: {}, hidden: false,
    classList: {add: x => classes.add(x), remove: x => classes.delete(x)},
    setAttribute(key, value) {this.attributes[key] = value;},
    removeAttribute(key) {delete this.attributes[key];},
    addEventListener(key, value) {this.listeners[key] = value;},
    getBoundingClientRect: () => ({left: 120, bottom: 80}),
    focus() {this.focused = true;}, append() {}, prepend() {},
    querySelectorAll: () => [], querySelector: () => null,
  };
}

function fixture() {
  const nodes = new Map(), calls = [], child = node(), menuButton = node(), oldAnchor = node();
  const get = key => {if (!nodes.has(key)) nodes.set(key, node()); return nodes.get(key);};
  oldAnchor.setAttribute("aria-expanded", "true");
  get("#pageMenu").pageMenuAnchor = oldAnchor;
  get("#movePageMenu").movePageAnchor = {id: "pageLocationBtn", setAttribute() {}};
  get("#movePageMenu").movePageRequest = {busy: false};
  const State = {activePageId: "main", user: {id: "owner"}, navigation: {enabled: false},
    collapsedPageIds: new Set(), favoriteCollapsedPageIds: new Set(), workspacePopoverOpen: true,
    pageData: {page: {id: "main"}, blocks: [{content: "미저장 본문"}]}, dirty: true,
    sidePeek: {dirty: true, data: {blocks: [{content: "옆 보기 입력"}]}}};
  const record = (action, page) => calls.push({action, id: page.id,
    menusClosed: ["#pageMenu", "#movePageMenu", "#newPageMenu"].every(id => get(id).hidden),
    workspaceClosed: !State.workspacePopoverOpen});
  const context = {State, $: get, window: {innerWidth: 1200, innerHeight: 800, NotionUiInteraction: {place() {}}},
    collapsedPageSet: rootId => rootId === "favoritePages" ? State.favoriteCollapsedPageIds : State.collapsedPageIds,
    document: {createElement: () => ({...node(), querySelector: selector =>
      selector === "[data-page-menu]" ? menuButton : selector === "[data-page-child]" ? child : null})},
    escapeAttr: String, escapeHtml: String, formatTime: String, iconSvg: () => "", pageIconHtml: () => "",
    pageIsFavorite: () => false, pageIsLocked: () => false, pageCanChangeAppearance: () => true,
    pageCanChangeSettings: () => false, bindPageDrag() {}, organizeTopbarPageMenu() {}, decorateActionIcons() {},
    closeDatabaseCopySubmenu() {}, closeMoveDatabaseMenu() {},
    openIconPicker: page => record("icon", page), openNewPageSidePeek: page => record("child", page),
    openPage: id => record("page", {id}), openSidePeek: id => record("peek", {id}),
    togglePageCollapsed: id => record("collapse", {id}), PageFocusPolicy: {PAGE_CANVAS: "canvas", PRESERVE: "preserve"},
  };
  vm.createContext(context);
  vm.runInContext([
    slice("function pageRow(", "function clearPageDropIndicators("),
    slice("function closePageMenu(", "function focusPageMenuAfterLifecycle("),
    slice("function closeNewPageMenu(", "function openNewPageMenu("),
    slice("function closeMovePageMenu(", "function organizeTopbarPageMenu("),
    slice("function closeWorkspacePopover(", "function toggleWorkspacePopover("),
    slice("function pageMenuFooter(", "function openPageMenu("),
    slice("function markPageMenuItems(", "const PAGE_ICON_ASSET_PATHS"),
    slice("function openPageMenu(", "async function handlePageMenuAction("),
  ].join("\n"), context);
  const page = {id: "row-target", title: "다른 페이지", scope: "own", role: "owner", can_edit: true};
  const row = context.pageRow(page, 0, false, true, true, "ownPages");
  const event = (selectors = [], currentTarget = row) => ({currentTarget, detail: 1,
    target: {closest: selector => selectors.includes(selector) ? currentTarget : null},
    preventDefault() {}, stopPropagation() {this.stopped = true;}});
  return {context, State, get, row, child, menuButton, oldAnchor, calls, event};
}

test("UI-SHELL-005 child-page entry dismisses the previous menus before draft navigation", () => {
  const f = fixture();
  f.child.listeners.click(f.event([".page-row-action"], f.child));
  assert.deepEqual(f.calls, [{action: "child", id: "row-target", menusClosed: true, workspaceClosed: true}]);
  assert.equal(f.oldAnchor.attributes["aria-expanded"], "false");
  assert.equal(f.get("#movePageMenu").movePageRequest, null, "the dismissed destination cannot finish a stale UI request");
  assert.equal(f.State.pageData.blocks[0].content, "미저장 본문");
  assert.equal(f.State.sidePeek.data.blocks[0].content, "옆 보기 입력");
  assert.equal(f.State.sidePeek.dirty, true);
});

for (const [action, selectors, altKey] of [
  ["icon", ["[data-page-icon-picker]"], false],
  ["collapse", ["[data-page-toggle]"], false],
  ["page", [], false], ["peek", [], true],
]) {
  test(`UI-SHELL-005 sidebar ${action} entry clears stopped-propagation menus and retains its target`, () => {
    const f = fixture();
    f.row.listeners.click({...f.event(selectors), altKey});
    assert.deepEqual(f.calls, [{action, id: "row-target", menusClosed: true, workspaceClosed: true}]);
    assert.equal(f.State.dirty, true);
  });
}

test("UI-SHELL-005 row menu replaces other navigation menus without activating the row", () => {
  const f = fixture();
  f.menuButton.listeners.click(f.event([".page-row-action"], f.menuButton));
  f.row.listeners.click(f.event([".page-row-action"], f.menuButton));
  assert.deepEqual(f.calls, []);
  assert.equal(f.get("#pageMenu").hidden, false);
  assert.equal(f.get("#pageMenu").pageMenuAnchor, f.menuButton);
  assert.equal(f.menuButton.attributes["aria-expanded"], "true");
  assert.equal(f.oldAnchor.attributes["aria-expanded"], "false");
  assert.equal(f.get("#movePageMenu").hidden, true);
  assert.equal(f.get("#newPageMenu").hidden, true);
  assert.equal(f.State.workspacePopoverOpen, false);
  assert.ok(!f.get("#pageMenu").innerHTML.includes("page-font-picker"));
});
