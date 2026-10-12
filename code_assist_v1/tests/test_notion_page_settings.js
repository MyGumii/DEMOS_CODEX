"use strict";
// UI-PAGE-SETTINGS: exercise the actual handlers with delayed metadata responses.
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const clone = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
function runtime(collaborative = true) {
  const initial = {id: "a", workspace_id: "ws", title: "Current title", can_edit: true, can_change_settings: true,
    settings: {}, document_sequence: 1, workspace_epoch: 1, document_epoch: 1};
  const state = {user: {id: "owner"}, navigation: {workspaceId: "ws", collaborativeSharing: collaborative}, pageLoadSequence: 1,
    activePageId: "a", pageData: {page: clone(initial), blocks: [{content: "Current body"}]}, sidePeek: {}, dirty: false, editRevision: 1, serverRevision: 1};
  const calls = [], statuses = [], renders = [], listeners = new Map();
  const node = {dataset: {}, disabled: false}; let respond, composing = false, operation = 0;
  const context = {State: state, console, pageIsLocked: page => !!page?.settings?.locked,
    TemporaryUnlockGrants: new Map(),
    iconScope: () => {const ws = state.navigation.workspaceId; return () => ws === state.navigation.workspaceId;},
    flushPageDraft: async () => true, navigationOperationId: () => `settings-${++operation}`,
    api: async (url, options = {}) => {calls.push([url, options]); if (options.method !== "PATCH") return {page: clone(initial)};
      return new Promise(resolve => {respond = resolve;});},
    loadPages: async () => {throw new Error("Do not reload a filtered list and clear the editor during metadata updates");},
    renderPageLists: () => {}, setStatus: text => statuses.push(text), serverRevisionFrom: data => data.revision ?? null,
    applyPageLayout: () => renders.push("layout"), renderPageLockState: () => renders.push("lock-state"), refreshOpenPageMenu: () => renders.push("menu-state"),
    renderBlocks: () => renders.push("blocks"), renderSidePeek: () => renders.push("peek"),
    activePageCanEdit: () => !state.pageData.page.settings.locked, EditorHost: {isComposing: () => composing},
    $: () => node, document: {addEventListener: (event, fn) => listeners.set(event, fn), removeEventListener: event => listeners.delete(event)},
    requestAnimationFrame: fn => fn()};
  vm.createContext(context);
  vm.runInContext(source.slice(source.indexOf("function pageCanChangeSettings("), source.indexOf("let ActiveIconPicker")), context);
  return {context, state, calls, statuses, renders, listeners, initial, node, respond: value => respond(value), setComposing: value => {composing = value;}};
}
(async () => {
  const r = runtime(); let server = clone(r.initial), patchResolve;
  r.context.api = async (url, options = {}) => {
    r.calls.push([url, options]);
    if (options.method === "PATCH") return new Promise(resolve => {patchResolve = resolve;});
    return {page: clone(server), blocks: [{content: "Stale server body"}], revision: 1};
  };
  const pending = r.context.updatePageSettings({full_width: true}, "a"); await tick();
  assert.equal(r.context.hasPendingPageSettings(), true, `reload protection covers the whole settings operation: ${r.statuses.join(', ')}`);
  r.state.pageData.blocks[0].content = "typed during setting save"; r.state.dirty = true;
  r.state.saveRetry = {operationId: "text-save"}; r.state.saveConflict = {revision: 2};
  server = {...server, title: "Stale server title", settings: {full_width: true}, document_sequence: 2};
  patchResolve({page: {...server, settings: {full_width: false}}});
  assert.equal(await pending, true);
  assert.equal(r.context.hasPendingPageSettings(), false);
  assert.equal(r.state.pageData.page.settings.full_width, true, "read current metadata after the receipt");
  assert.equal(r.state.pageData.page.title, "Current title");
  assert.equal(r.state.pageData.blocks[0].content, "typed during setting save");
  assert.equal(r.state.dirty, true); assert.equal(r.state.saveRetry.operationId, "text-save");
  assert.equal(r.state.saveConflict.revision, 2); assert.ok(!r.renders.includes("blocks"));
  assert.deepEqual(r.calls.map(([url]) => url), ["/api/notion/workspaces/ws/pages/a/collaboration", "/api/notion/workspaces/ws/pages/a/settings", "/api/notion/workspaces/ws/pages/a/collaboration"]);

  const denied = runtime(); denied.context.api = async () => ({page: {...denied.initial, can_change_settings: false}});
  assert.equal(await denied.context.updatePageSettings({font: "serif"}, "a"), false);
  assert.equal(denied.context.hasPendingPageSettings(), false);
  const failedDraft = runtime(); failedDraft.context.flushPageDraft = async () => false;
  assert.equal(await failedDraft.context.updatePageSettings({full_width: true}, "a"), false);
  assert.equal(failedDraft.calls.length, 0);

  const retry = runtime(); let writes = 0, finalFont = "serif";
  retry.context.api = async (url, options = {}) => {
    retry.calls.push([url, clone(options)]);
    if (options.method === "PATCH") {if (++writes === 1) throw new Error("lost response"); return {};}
    return {page: {...retry.initial, document_sequence: writes ? 8 : 1, settings: {font: writes ? finalFont : "default"}}};
  };
  assert.equal(await retry.context.updatePageSettings({font: "serif"}, "a"), false);
  finalFont = "mono";
  assert.equal(await retry.context.updatePageSettings({font: "serif"}, "a"), true);
  const requests = retry.calls.filter(([, options]) => options.method === "PATCH").map(([, options]) => options.body);
  assert.deepEqual(requests[0], requests[1], "uncertain retries retain the exact operation and original CAS");
  assert.equal(retry.state.pageData.page.settings.font, "mono");

  const switched = runtime(); let finish;
  switched.context.api = async (url, options = {}) => options.method === "PATCH"
    ? new Promise(resolve => {finish = resolve;}) : {page: {...switched.initial, settings: {font: "serif"}}};
  const switchTask = switched.context.updatePageSettings({font: "serif"}, "a"); await tick();
  switched.state.navigation.workspaceId = "elsewhere"; finish({}); await switchTask;
  assert.deepEqual(switched.state.pageData.page.settings, {});

  const newPage = runtime();
  const oldTask = newPage.context.updatePageSettings({full_width: true}, "a"); await tick();
  newPage.state.activePageId = "b"; newPage.state.pageData = {page: {id: "b", settings: {}}, blocks: [{content: "B"}]};
  newPage.respond({}); await oldTask;
  assert.equal(newPage.state.pageData.blocks[0].content, "B");

  const legacy = runtime(false); const generalTask = legacy.context.updatePageSettings({font: "serif"}, "a"); await tick();
  legacy.state.dirty = true; legacy.state.pageData.blocks[0].content = "new general text";
  legacy.respond({page: {...legacy.initial, title: "old", settings: {font: "serif"}}, blocks: [], revision: 2});
  assert.equal(await generalTask, true); assert.equal(legacy.state.pageData.blocks[0].content, "new general text");
  assert.equal(legacy.state.dirty, true); assert.equal(legacy.state.serverRevision, 2);
  assert.deepEqual(legacy.calls.map(([url]) => url), ["/api/notion/pages/a", "/api/notion/pages/a"]);

  const locked = runtime(); locked.state.pageData.page.settings.locked = true; locked.setComposing(true);
  locked.context.refreshPageSettingsControls("a", true);
  assert.ok(!locked.renders.includes("blocks"), "remote lock must not discard an IME composition");
  locked.setComposing(false); locked.listeners.get("compositionend")();
  assert.equal(locked.node.disabled, true); assert.equal(locked.renders.filter(x => x === "blocks").length, 1);
  const deferredNavigation = runtime(); deferredNavigation.setComposing(true);
  deferredNavigation.context.refreshPageSettingsControls("a", true); deferredNavigation.state.pageLoadSequence += 1;
  deferredNavigation.listeners.get("compositionend")(); assert.ok(!deferredNavigation.renders.includes("blocks"));

  const stub = () => {};
  const menu = {style: {}, dataset: {}, hidden: true, innerHTML: "", setAttribute: stub,
    removeAttribute: stub, focus: stub, querySelectorAll: () => [], querySelector: () => null,
    prepend() {}, append() {}};
  const anchor = {setAttribute: stub, getBoundingClientRect: () => ({left: 600, bottom: 40}), focus: stub};
  const menus = {State: {navigation: {collaborativeSharing: true}, user: {id: "owner"}}, $: () => menu,
    closePageMenu: stub, closePageNavigationMenus: stub, pageIsFavorite: () => false,
    TemporaryUnlockGrants: new Map(),
    pageGloballyLocked: p => !!p.settings?.locked,
    pageCanChangeSettings: page => page.can_change_settings === true,
    pageCanManageLifecycle: () => false, pageCanCreateChild: () => false,
    iconSvg: () => "", escapeHtml: String, formatTime: String, organizeTopbarPageMenu: stub, decorateActionIcons: stub,
    pageMenuFooter: () => "", pageCanDuplicate: () => true, markPageMenuItems: stub,
    pageMenuCanUndo: page => Boolean(page.can_edit && !page.settings?.locked),
    window: {innerWidth: 1920, innerHeight: 863, NotionUiInteraction: {place: stub}},
    document: {createElement: () => ({setAttribute: stub, addEventListener: stub, focus: stub})}};
  vm.createContext(menus);
  vm.runInContext(source.slice(source.indexOf("function pageIsLocked("), source.indexOf("function activePageCanEdit(")), menus);
  vm.runInContext(source.slice(source.indexOf("function pageCanMove("), source.indexOf("function pageCanArchive(")), menus);
  vm.runInContext(source.slice(source.indexOf("function pageCanArchive("), source.indexOf("function clearPageDropIndicators(")), menus);
  vm.runInContext(source.slice(source.indexOf("function openPageMenu("), source.indexOf("async function handlePageMenuAction(")), menus);
  menus.pageMenuCanUndo = () => false;
  menus.openPageMenu({id: "a", can_edit: true, can_change_settings: true, settings: {}}, anchor, "page");
  assert.ok(!menu.innerHTML.includes('data-page-action="undo"'), "empty local history hides undo like Notion");
  menus.pageMenuCanUndo = page => Boolean(page.can_edit && !page.settings?.locked);
  for (const [role, canEdit, canSettings] of [["view", false, false], ["comment", false, false], ["edit_content", true, false], ["edit", true, true], ["full_access", true, true]]) {
    menus.openPageMenu({id: "a", can_edit: canEdit, can_change_settings: canSettings, settings: {}}, anchor, "page");
    assert.ok(menu.innerHTML.includes('data-page-action="copy_content"'), `${role} can copy readable page content`);
    assert.equal(menu.innerHTML.includes('data-page-action="undo"'), canEdit, `${role} can undo only with edit access`);
    assert.equal(menu.innerHTML.includes('data-page-action="full_width"'), canSettings, role);
    assert.equal(menu.innerHTML.includes('data-page-action="lock"'), canSettings, role);
    assert.equal(menu.innerHTML.includes('data-page-action="trash"'), false, role);
    assert.equal(menu.innerHTML.includes('data-page-action="import"'), false, role);
  }
  menus.openPageMenu({id: "a", can_edit: true, can_move: true, can_change_settings: true, settings: {locked: true}}, anchor, "page");
  assert.ok(menu.innerHTML.includes("페이지 잠금 해제"));
  assert.ok(!menu.innerHTML.includes('data-page-action="full_width"'));
  for (const action of ["rename", "move", "duplicate", "trash", "import", "undo"]) {
    assert.ok(!menu.innerHTML.includes(`data-page-action="${action}"`), `locked page excludes ${action}`);
  }
  for (const action of ["copy_link", "copy_content", "history", "export", "new_tab", "side_peek"]) {
    assert.ok(menu.innerHTML.includes(`data-page-action="${action}"`), `locked page retains ${action}`);
  }
  menus.State.navigation.collaborativeSharing = false;
  menus.TemporaryUnlockGrants.set("a", {userId:"owner", token:"tab-grant"});
  menus.openPageMenu({id:"a", scope:"own", role:"owner", can_edit:true, can_move:true,
    can_change_settings:true, settings:{locked:true}}, anchor, "page");
  for (const action of ["rename", "move", "duplicate", "trash", "full_width"]) {
    assert.ok(menu.innerHTML.includes(`data-page-action="${action}"`),
      `personal tab unlock restores ${action} while global lock remains on`);
  }
  menus.TemporaryUnlockGrants.delete("a");
  menus.State.navigation.collaborativeSharing = true;
  menus.State.navigation.workspaceId = "ws";
  const teamPage = {id:"a", workspace_id:"ws", collaborative_editing:true, can_edit:true,
    can_share:true, can_move:false, can_change_settings:true, settings:{locked:true}};
  menus.openPageMenu(teamPage, anchor, "lock");
  assert.ok(menu.innerHTML.includes('data-page-action="temporary_unlock"'),
    "locked team page offers a current-tab unlock");
  menus.TemporaryUnlockGrants.set("a", {userId:"owner", workspaceId:"ws", token:"team-tab-grant"});
  menus.openPageMenu(teamPage, anchor, "lock");
  assert.ok(menu.innerHTML.includes('data-page-action="relock_local"'),
    "the granted team tab offers a local relock");
  menus.openPageMenu(teamPage, anchor, "page");
  assert.ok(menu.innerHTML.includes('data-page-action="move"')
    && menu.innerHTML.includes('data-page-action="full_width"'),
    "the granted team tab restores owner actions while global lock remains on");
  menus.TemporaryUnlockGrants.delete("a");
  menus.pageCanManageLifecycle = menus.pageCanCreateChild = () => true;
  menus.State.navigation.collaborativePageArchive = true;
  menus.openPageMenu({id: "a", can_edit: true, can_move: true, can_change_settings: true, settings: {}}, anchor, "page");
  assert.ok(menu.innerHTML.includes('data-page-action="trash"') && menu.innerHTML.includes('data-page-action="import"'));
  menus.State.lastHistoryGrid = {pageId: "a", blockId: "focused-grid"};
  menus.openPageMenu({id: "a", can_edit: true, can_change_settings: true, settings: {}}, anchor, "page");
  assert.equal(menu.pageMenuHistoryGridBlockId, "focused-grid", "topbar menu captures the editor target before taking focus");
  menus.openPageMenu({id: "other", can_edit: true, can_change_settings: true, settings: {}}, anchor, "page");
  assert.equal(menu.pageMenuHistoryGridBlockId, null, "another page cannot inherit the grid undo target");
  for (const collaborativeSharing of [false, true]) {
    menus.State.navigation.collaborativeSharing = collaborativeSharing;
    for (const surface of ["row", "page"]) {
      for (const locked of [true, false]) {
        menus.openPageMenu({id: "a", scope: "own", role: "owner", can_edit: true, can_move: true,
          can_change_settings: true, settings: {locked}}, anchor, surface);
        for (const action of ["rename", "move"]) {
          assert.equal(menu.innerHTML.includes(`data-page-action="${action}"`), !locked,
            `${collaborativeSharing ? "collaborative" : "ordinary"} ${surface} ${action} respects lock`);
        }
        assert.equal(menu.innerHTML.includes('data-page-action="lock"'), surface === "page");
        assert.equal(menu.innerHTML.includes('data-page-action="copy_content"'), surface === "page");
        assert.equal(menu.innerHTML.includes('data-page-action="undo"'), surface === "page" && !locked);
        assert.ok(menu.innerHTML.includes('data-page-action="copy_link"'));
      }
    }
  }
  const lockedTeamPage = {id:"a", workspace_id:"ws", collaborative_editing:true,
    can_edit:true, settings:{locked:true}};
  const editor = {State:{user:{id:"owner"}, navigation:{workspaceId:"ws"}, activePageId:"a",
    pageData:{page:lockedTeamPage}, liveCollaboration:{crdtReady:true,writeAuthorized:false,
      offlinePolicyBlocked:false,recoveryOnly:false}},
    TemporaryUnlockGrants:new Map([["a",{userId:"owner",workspaceId:"ws",token:"team-tab-grant"}]]),
    CollaborativeEditingOutbox:{}, CollaborativeEditorBridge:{descriptor:()=>({page_id:"a"})}};
  vm.createContext(editor);
  vm.runInContext(source.slice(source.indexOf("function pageIsLocked("), source.indexOf("function applyPageLayout(")), editor);
  assert.equal(editor.activePageCanEdit(), false, "the locked team editor waits for a write-authorized live ticket");
  editor.State.liveCollaboration.writeAuthorized = true;
  assert.equal(editor.activePageCanEdit(), true, "the same tab edits after its write ticket is confirmed");
  editor.TemporaryUnlockGrants.delete("a");
  assert.equal(editor.activePageCanEdit(), false, "local relock closes editing even with an old live ticket");
  lockedTeamPage.lock_generation = "lock-first";
  editor.TemporaryUnlockGrants.set("a", {userId:"owner", workspaceId:"ws", lockGeneration:"lock-first", token:"team-tab-grant"});
  editor.State.liveCollaboration.writeAuthorized = true;
  assert.equal(editor.activePageCanEdit(), true, "a grant matches the current collaborative lock generation");
  lockedTeamPage.lock_generation = "lock-second";
  assert.equal(editor.activePageCanEdit(), false, "global relock invalidates the old tab grant before another write");
  assert.equal(editor.reconcileTemporaryUnlockGrant(lockedTeamPage), true);
  assert.equal(editor.TemporaryUnlockGrants.has("a"), false);
  assert.equal(editor.State.liveCollaboration.writeAuthorized, false);
  console.log("UI-PAGE-SETTINGS: canonical/general preservation, retry, navigation, IME and five menu roles passed");
})().catch(error => {console.error(error); process.exitCode = 1;});
