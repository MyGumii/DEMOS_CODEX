"use strict";
const assert = require("node:assert/strict"), test = require("node:test");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function fixture() {
  const state = {user: {id: "owner"}, navigation: {workspaceId: "ws-a", sequence: 1},
    activePageId: "page-a", pageData: {page: {id: "page-a"}}, sidePeek: {}, backlinks: [{id: "old-parent"}]};
  const requests = [];
  let renders = 0;
  const ctx = {State: state, api: () => new Promise((resolve, reject) => requests.push({resolve, reject})),
    collaborativeSearchWorkspacePath: () => `/api/notion/workspaces/${state.navigation.workspaceId}`,
    renderBacklinksButton() {renders++;}, loadPages: async () => {}, pageMetadataScope: () => null,
    collaborativeEditingActive: () => false, renderPageLists() {}, renderBreadcrumb() {},
    refreshOpenPagesAfterMutation: async () => ({})};
  vm.createContext(ctx);
  vm.runInContext(source.slice(source.indexOf("let BacklinksRequestSequence ="), source.indexOf("function renderBacklinksButton(")), ctx);
  vm.runInContext(source.slice(source.indexOf("async function refreshAfterCollaborativeMove("), source.indexOf("async function movePageSafely(")), ctx);
  return {ctx, state, requests, get renders() {return renders;}};
}

test("UI-LOCATION-007 late pre-move backlink response cannot restore an obsolete parent", async () => {
  const f = fixture();
  const old = f.ctx.loadBacklinks(), latest = f.ctx.loadBacklinks();
  f.requests[1].resolve({items: []});
  await latest;
  f.requests[0].resolve({items: [{id: "removed-parent"}]});
  await old;
  assert.equal(f.state.backlinks.length, 0);
  assert.equal(f.renders, 1);
});

test("UI-LOCATION-008 previous identity, workspace, page, navigation or request cannot replace current backlinks", async () => {
  const changes = [f => f.state.user.id = "other", f => f.state.navigation.workspaceId = "ws-b",
    f => f.state.activePageId = "page-b", f => f.state.navigation.sequence++];
  for (const change of changes) {
    for (const failure of [false, true]) {
      const f = fixture();
      const pending = f.ctx.loadBacklinks();
      change(f);
      f.state.backlinks = [{id: "current-parent"}];
      if (failure) f.requests[0].reject(new Error("old request failed"));
      else f.requests[0].resolve({items: [{id: "stale-parent"}]});
      await pending;
      assert.equal(f.state.backlinks[0].id, "current-parent");
      assert.equal(f.renders, 0);
    }
  }
});

test("UI-LOCATION-009 both move refresh paths await fresh backlinks; failure hides stale count", async () => {
  for (const name of ["refreshAfterCollaborativeMove", "refreshAfterOrdinaryMove"]) {
    const f = fixture();
    let completed = false;
    const moved = f.ctx[name]().then(() => {completed = true;});
    // Flush the finite metadata awaits to reach the deferred backlink request.
    for (let n = 0; n < 5; n++) await Promise.resolve();
    assert.equal(completed, false);
    assert.equal(f.requests.length, 1);
    f.requests[0].reject(new Error("network unavailable"));
    await moved;
    assert.equal(f.state.backlinks.length, 0);
    assert.equal(f.renders, 1);
  }
});
