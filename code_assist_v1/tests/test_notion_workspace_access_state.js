"use strict";
// UI-WORKSPACE-ACCESS: obsolete directory reads must not replace current UI.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const {test} = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function part(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a);
  return source.slice(a, b);
}
function setup() {
  const State = {user: {id: "owner"}, workspaceAccess: null, pages: [],
    navigation: {workspaceId: "ws-a", sequence: 1, collaborativeSharing: true}};
  const requests = [], renders = [];
  const context = {State, $: () => ({hidden: false}),
    api: url => new Promise((resolve, reject) => requests.push({url, resolve, reject})),
    selectedNavigationWorkspace: () => ({id: State.navigation.workspaceId, access: {role: "owner"}}),
    renderWorkspaceMembers: () => renders.push("members"), renderUsers: () => renders.push("users"),
    renderCollaborativeTeamspaceSidebar: () => renders.push("teams"), applyNavigationSections: () => renders.push("sections"),
    renderBreadcrumb: () => renders.push("breadcrumb"), renderPageLocation: () => renders.push("location"),
    renderHome: () => renders.push("home"),
    visiblePages: () => State.pages,
  };
  vm.createContext(context);
  vm.runInContext(part("function pageNavigationPlacement(", "const TeamspaceSettings =")
    + part("async function loadWorkspaceAccess(", "function renderWorkspaceMembers("), context);
  return {context, State, requests, renders};
}
const payload = (id, name = id) => ({workspace: {id, teamspaces: [{id: name, name, role: "owner"}]}});

test("UI-WORKSPACE-ACCESS current response refreshes directory and current page labels", async () => {
  const h = setup(); h.State.pageData = {page: {id: "page-a", workspace_id: "ws-a"}};
  const pending = h.context.loadWorkspaceAccess();
  h.requests[0].resolve(payload("ws-a"));
  assert.equal(await pending, true);
  assert.equal(h.State.workspaceAccess.id, "ws-a");
  assert.deepEqual(h.renders, ["members", "users", "teams", "sections", "breadcrumb", "location"]);
});

test("UI-WORKSPACE-ACCESS newest same-workspace request wins over late success and failure", async () => {
  for (const fails of [false, true]) {
    const h = setup(), old = h.context.loadWorkspaceAccess(), current = h.context.loadWorkspaceAccess();
    h.requests[1].resolve(payload("ws-a", "current")); await current;
    const before = h.renders.length;
    if (fails) h.requests[0].reject(new Error("late failure")); else h.requests[0].resolve(payload("ws-a", "old"));
    assert.equal(await old, false);
    assert.equal(h.State.workspaceAccess.teamspaces[0].name, "current");
    assert.equal(h.renders.length, before);
  }
});

test("UI-WORKSPACE-ACCESS account, workspace, navigation generation and disabled context discard reads", async () => {
  const changes = [h => h.State.user.id = "another", h => h.State.navigation.workspaceId = "ws-b",
    h => h.State.navigation.sequence++, h => h.State.navigation.collaborativeSharing = false];
  for (const change of changes) {
    const h = setup(), pending = h.context.loadWorkspaceAccess();
    change(h); h.requests[0].resolve(payload("ws-a"));
    assert.equal(await pending, false);
    assert.equal(h.State.workspaceAccess, null); assert.deepEqual(h.renders, []);
  }
});

test("UI-WORKSPACE-ACCESS mismatched payload clears only current metadata and guest avoids team rendering", async () => {
  const h = setup(); h.State.workspaceAccess = payload("ws-a").workspace;
  const pending = h.context.loadWorkspaceAccess(); h.requests[0].resolve(payload("ws-b"));
  assert.equal(await pending, false); assert.equal(h.State.workspaceAccess, null);
  h.renders.length = 0;
  h.context.selectedNavigationWorkspace = () => ({id: "ws-a", access: {role: "guest"}});
  const guest = h.context.loadWorkspaceAccess(); h.requests[1].resolve({workspace: {id: "ws-a", teamspaces: []}});
  assert.equal(await guest, true); assert.deepEqual(h.renders, ["members", "users"]);
});

test("UI-WORKSPACE-ACCESS empty joined spaces remain visible without exposing unjoined directory entries", () => {
  const h = setup(), workspace = {id: "ws-a"};
  h.State.workspaceAccess = {id: "ws-a", teamspaces: [
    {id: "empty", name: "빈 팀", role: "owner"}, {id: "member", name: "참여 팀", role: "member"},
    {id: "directory", name: "미참여 공개 팀", role: ""},
  ]};
  const pages = [{id: "page", placement: {scope: "teamspace", teamspace_id: "unknown"}}];
  const groups = h.context.sidebarTeamspaceGroups(pages, workspace, {includeEmpty: true});
  assert.deepEqual(Array.from(groups, g => g.id), ["empty", "member", "unknown"]);
  assert.equal(groups[2].name, "팀스페이스");
  assert.deepEqual(Array.from(h.context.sidebarTeamspaceGroups(pages, workspace), g => g.id), ["unknown"],
    "a page breadcrumb still resolves only its own teamspace");
  h.State.workspaceAccess.id = "ws-old";
  assert.deepEqual(Array.from(h.context.sidebarTeamspaceGroups([], workspace, {includeEmpty: true})), []);
});
