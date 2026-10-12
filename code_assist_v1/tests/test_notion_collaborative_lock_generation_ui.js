"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
function section(start, end) {
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert.ok(first >= 0 && last > first, `${start} production section exists`);
  return source.slice(first, last);
}
function runtime() {
  const page = {id:"a", workspace_id:"ws", collaborative_editing:true, title:"Unsaved title",
    settings:{locked:true}, lock_generation:"first", can_edit:true, can_change_settings:true,
    document_sequence:2, updated_at:2};
  const state = {user:{id:"owner"}, navigation:{sequence:1, workspaceId:"ws", collaborativeSharing:true},
    activePageId:"a", pageData:{page, blocks:[{id:"b", content:"Unsaved body"}]},
    sidePeek:{data:null}, pages:[{...page}], liveCollaboration:{writeAuthorized:true}};
  const grants = new Map([["a", {userId:"owner", workspaceId:"ws", lockGeneration:"first", token:"tab"}]]);
  const effects = [];
  const context = {State:state, TemporaryUnlockGrants:grants, console,
    window:{NotionMetadataSync:{scopeKey:() => "scope"}},
    $:() => ({value:""}), pageMetadataScope:() => ({}),
    iconScope:() => () => true, api:async () => ({page:{...page, lock_generation:"second", document_sequence:3}}),
    fetchWorkspacePagesSnapshot:async () => [],
    refreshPageSettingsControls:(_,changed) => effects.push(["controls",changed]),
    renderTopbarPageActions:() => effects.push(["topbar"]),
    renderPageLists:() => effects.push(["list"]),
    refreshOpenPageMenu:() => effects.push(["menu"]),
    renderBreadcrumb:() => {}, applyPageDisplayToLists:() => {},
    renderPageActivity:() => {}, syncPageTitleDisplays:() => {}, refreshPageReferenceDisplays:() => {}};
  vm.createContext(context);
  vm.runInContext(section("function pageIsLocked(", "function applyPageLayout("), context);
  vm.runInContext(section("async function refreshLivePageAppearance(", "function recordLiveReplayReceipt("), context);
  vm.runInContext(section("let PageMetadataReadSequence = 0;", "async function refreshVisiblePageActivity("), context);
  return {context, state, grants, effects, page};
}

(async () => {
  const live = runtime();
  await live.context.refreshLivePageAppearance("a");
  assert.equal(live.grants.has("a"), false, "live settings refresh revokes the prior lock generation");
  assert.equal(live.state.liveCollaboration.writeAuthorized, false);
  assert.equal(live.context.pageIsLocked(live.page), true);
  assert.ok(live.effects.some(([kind, changed]) => kind === "controls" && changed));
  assert.equal(live.state.pageData.blocks[0].content, "Unsaved body", "remote lock preserves the draft");

  const metadata = runtime();
  metadata.context.fetchWorkspacePagesSnapshot = async () => [{...metadata.page,
    settings:{locked:true}, lock_generation:"second", document_sequence:3, updated_at:3}];
  await metadata.context.refreshVisiblePageMetadata(["a"], "scope");
  assert.equal(metadata.grants.has("a"), false, "background metadata also revokes the old grant");
  assert.equal(metadata.state.liveCollaboration.writeAuthorized, false);
  assert.ok(metadata.effects.some(([kind, changed]) => kind === "controls" && changed));
  assert.equal(metadata.state.pageData.page.title, "Unsaved title");
  assert.equal(metadata.state.pageData.blocks[0].content, "Unsaved body");

  const unrelated = runtime();
  unrelated.context.fetchWorkspacePagesSnapshot = async () => [{...unrelated.page,
    lock_generation:"first", document_sequence:3, updated_at:3}];
  await unrelated.context.refreshVisiblePageMetadata(["a"], "scope");
  assert.equal(unrelated.grants.has("a"), true, "another edit does not revoke the current tab unlock");
  assert.equal(unrelated.state.liveCollaboration.writeAuthorized, true);
  console.log("UI-PAGE-SETTINGS: remote relock revokes stale tab grants and preserves unsaved text");
})().catch(error => {console.error(error); process.exitCode = 1;});
