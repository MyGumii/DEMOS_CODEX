"use strict";
// UI-SIDEBAR-PLACEMENT: actual navigation rendering respects canonical placement.
const assert = require("node:assert/strict");
const fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function part(start, end) {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, start);
  return source.slice(a, b);
}
const nodes = new Map();
function node(id) {
  if (!nodes.has(id)) nodes.set(id, {id, hidden: false, innerHTML: "", textContent: "",
    replaceChildren() {this.rows = [];}, toggleAttribute() {}, querySelector() {return null;}, querySelectorAll() {return [];}});
  return nodes.get(id);
}
const page = (id, scope, placement, teamspace = "", parent = "") => ({id, title: id,
  workspace_id: "ws-a", owner_user_id: scope === "own" ? "owner" : "other", scope, parent_id: parent,
  placement: {scope: placement, teamspace_id: teamspace, binding_version: 1}});
const state = {user: {id: "owner"}, workspaceOwnerFilter: "", pages: [], workspaceAccess: null,
  navigation: {enabled: true, authoritative: true, collaborativeSharing: true, workspaceId: "ws-a", workspaces: []}};
let workspace = {id: "ws-a", name: "Workspace A", access: {role: "owner"}};
let grouped = [];
const context = {State: state, $: node, window: {}, document: {querySelector() {return null;}, body: {classList: {toggle() {}}}},
  selectedNavigationWorkspace: () => workspace,
  sidebarCapabilities: () => ({workspace, collaborative: true, guest: workspace.access.role === "guest", canCreate: true}),
  navigationSection: () => ({item_limit: 10}), visiblePages: () => state.pages,
  workspaceIconHtml: () => "", iconSvg: () => "", escapeHtml: String,
  pageCrumbs: item => [item], pageIconHtml: () => "",
  renderPageGroup: (target, pages) => {target.rows = Array.from(pages, item => item.id);},
};
vm.createContext(context);
vm.runInContext([
  part("function pageNavigationPlacement(", "function renderCollaborativeTeamspaceSidebar("),
  part("function syncCollaborativeSidebarPresentation(", "function localSidebarSections("),
  part("function renderBreadcrumb(", "async function goHome("),
  part("function syncWorkspaceLandingBanner(", "let SidePeekOpenSequence"),
].join("\n"), context);
context.renderCollaborativeTeamspaceSidebar = (ws, pages) => {
  grouped = Array.from(context.sidebarTeamspaceGroups(pages, ws), group => ({
    id: group.id, name: group.name, pages: Array.from(group.pages, item => item.id),
  }));
};
state.pages = [page("my-private", "own", "private"), page("private-child", "own", "private", "", "my-private"),
  page("shared-private", "shared", "private"), page("team-mine", "own", "teamspace", "team-a"),
  page("team-other", "shared", "teamspace", "team-a"), page("second-team", "shared", "teamspace", "team-b")];
state.workspaceAccess = {id: "ws-a", teamspaces: [{id: "team-a", name: "제품 개발"}, {id: "team-b", name: "디자인"}]};
context.syncCollaborativeSidebarPresentation();
assert.deepEqual(node("#ownPages").rows, ["my-private", "private-child"]);
assert.deepEqual(node("#sharedPages").rows, ["shared-private"]);
assert.deepEqual(grouped, [{id: "team-a", name: "제품 개발", pages: ["team-mine", "team-other"]},
  {id: "team-b", name: "디자인", pages: ["second-team"]}]);
assert.equal(new Set([...node("#ownPages").rows, ...node("#sharedPages").rows, ...grouped.flatMap(g => g.pages)]).size, state.pages.length);

// Detail responses can omit placement. A refreshed listing wins after a move.
const detail = {...state.pages[0], placement: undefined};
context.renderBreadcrumb(detail);
assert.equal(node("#breadcrumb").innerHTML.includes("Workspace A"), false);
assert.equal(node("#breadcrumb").innerHTML.includes("my-private"), true);
state.pages[0] = {...state.pages[0], placement: {scope: "teamspace", teamspace_id: "team-b", binding_version: 2}};
context.renderBreadcrumb(detail);
assert.equal(node("#breadcrumb").innerHTML.includes("디자인"), true);
context.syncCollaborativeSidebarPresentation();
assert.deepEqual(node("#ownPages").rows, ["private-child"]);
assert.deepEqual(grouped.find(g => g.id === "team-b").pages, ["my-private", "second-team"]);

// Metadata from a previous workspace must not supply a name for this one.
state.workspaceAccess = {id: "ws-old", teamspaces: [{id: "team-b", name: "이전 워크스페이스"}]};
context.renderBreadcrumb(detail);
assert.equal(node("#breadcrumb").innerHTML.includes("이전 워크스페이스"), false);
assert.equal(node("#breadcrumb").innerHTML.includes("팀스페이스"), true);
const selected = workspace;
workspace = null;
assert.doesNotThrow(() => context.renderBreadcrumb(detail), "a pending workspace read does not break page rendering");
workspace = selected;

// Guests keep one authorized shared list, even when source placement is private.
workspace = {...workspace, access: {role: "guest"}};
context.syncCollaborativeSidebarPresentation();
assert.deepEqual(node("#ownPages").rows, []);
assert.deepEqual(node("#sharedPages").rows, state.pages.map(p => p.id));
assert.deepEqual(node("#teamspacePages").rows, []);

workspace = {...workspace, access: {role: "owner"}};
state.workspaceAccess = {id: "ws-a", members: [{}, {}]};
state.navigation.workspaces = [workspace];
state.pageData = {blocks: []};
context.syncWorkspaceLandingBanner(state.pages[1]);
assert.equal(node("#workspaceLandingBanner").hidden, true, "a private page is not a workspace landing page");
const teamRoot = {...state.pages[3], parent_id: ""};
context.syncWorkspaceLandingBanner(teamRoot);
assert.equal(node("#workspaceLandingBanner").hidden, false);
state.pageData.blocks = [{type: "paragraph", content: "Already written"}];
context.syncWorkspaceLandingBanner(teamRoot);
assert.equal(node("#workspaceLandingBanner").hidden, true);
console.log("UI-SIDEBAR-PLACEMENT passed: mixed placement, hierarchy, moves, team names, guests, breadcrumb and landing banner");
