"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const rowSource = source.slice(source.indexOf("function pageRow("), source.indexOf("function clearPageDropIndicators("));
const calls = [];
const collapsedIds = new Set(), favoriteCollapsedIds = new Set();
const node = () => ({style: {}, dataset: {}, listeners: {}, setAttribute() {}, addEventListener(name, fn) {this.listeners[name] = fn;}});
const menuButton = node(), childButton = node();
const context = {
  State: {collapsedPageIds: collapsedIds, favoriteCollapsedPageIds: favoriteCollapsedIds},
  collapsedPageSet: rootId => rootId === 'favoritePages' ? favoriteCollapsedIds : collapsedIds,
  document: {createElement: () => ({...node(), querySelector: selector => selector.includes("data-page-menu") ? menuButton : childButton})},
  escapeAttr: String, escapeHtml: String, iconSvg: () => "<svg></svg>", pageIconHtml: () => "<svg></svg>", pageIsFavorite: () => false,
  pageIsLocked: page => Boolean(page.settings?.locked),
  bindPageDrag() {}, togglePageCollapsed: id => calls.push(["collapse", id]), openPage: id => calls.push(["page", id]),
  openPageMenu: page => calls.push(["menu", page.id]), openNewPageSidePeek: page => calls.push(["child", page.id]),
  closePageNavigationMenus() {},
};
vm.createContext(context);
vm.runInContext(source.slice(source.indexOf("function pageCanChangeAppearance("), source.indexOf("const AppearanceIntents")), context);
vm.runInContext(rowSource, context);
vm.runInContext(source.slice(source.indexOf("function pageContextShortcutAction("), source.indexOf("function openPageMenu(")), context);
context.handlePageMenuAction = async (action,page) => calls.push([action,page.id]);
const row = context.pageRow({id: "parent", scope: "own", role:"owner", can_edit: true}, 0, false, true, true, "ownPages");
const event = (target, key) => ({target, currentTarget: target, key, preventDefault() {this.prevented = true;}, stopPropagation() {}});
for (const button of [menuButton, childButton]) {
  for (const key of ["Enter", " "]) {
    const e = event(button, key); row.listeners.keydown(e);
    assert.equal(e.prevented, undefined, "parent row must not cancel native button activation");
  }
}
assert.deepEqual(calls, []);
menuButton.listeners.click(event(menuButton)); childButton.listeners.click(event(childButton));
assert.deepEqual(calls, [["menu", "parent"], ["child", "parent"]]);
calls.length = 0;
row.listeners.keydown(event(row, "Enter")); row.listeners.keydown(event(row, " "));
assert.deepEqual(calls, [["page", "parent"], ["page", "parent"]]);
calls.length = 0;
row.listeners.keydown(event(row, "ArrowLeft")); assert.deepEqual(calls, [["collapse", "parent"]]);
calls.length = 0; context.State.collapsedPageIds.add("parent");
row.listeners.keydown(event(row, "ArrowRight")); assert.deepEqual(calls, [["collapse", "parent"]]);
calls.length = 0; row.listeners.keydown({...event(row, "Enter"), isComposing: true}); assert.deepEqual(calls, []);
row.listeners.keydown({...event(row,"Enter"),ctrlKey:true,shiftKey:true});
row.listeners.keydown({...event(menuButton,"r"),ctrlKey:true,shiftKey:true});
row.listeners.keydown({...event(row,"d"),ctrlKey:true});
assert.deepEqual(calls,[["new_tab","parent"],["rename","parent"],["duplicate","parent"]],"row shortcuts retain row identity and do not open the main page");
calls.length=0;row.listeners.keydown({...event(row,"Enter"),ctrlKey:true,shiftKey:true,repeat:true});assert.deepEqual(calls,[]);
console.log("UI-SHELL-002 R6: row/button Enter-Space separation, composition and tree collapse passed");
