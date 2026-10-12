"use strict";
const assert = require("node:assert/strict"), fs = require("node:fs"), path = require("node:path"), vm = require("node:vm");
const Draft = require("../notion_static/notion_page_draft.js");
const Icons = require("../notion_static/notion_icons.js");
const source = fs.readFileSync(path.join(__dirname, "../notion_static/notion.js"), "utf8");
const slice = (start, end) => {
  const a = source.indexOf(start), b = source.indexOf(end, a);
  assert.ok(a >= 0 && b > a, `source functions exist: ${start}`);
  return source.slice(a, b);
};
const clone = value => JSON.parse(JSON.stringify(value));
const gate = () => { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; };
const png = () => Object.assign(new Blob([Uint8Array.from([137,80,78,71,13,10,26,10,0,0,0,0])], {type:"image/png"}), {name:"icon.png"});

function harness(collaborative = false) {
  const calls = [], statuses = [], nodes = new Map(), hooks = {};
  const $ = selector => {
    if (!nodes.has(selector)) nodes.set(selector, {hidden:false, value:undefined, textContent:"", innerHTML:"", isConnected:true,
      classList:{toggle(){}}, setAttribute(){}, focus(){}, replaceChildren(){}});
    return nodes.get(selector);
  };
  const State = {user:{id:"owner"}, navigation:{workspaceId:collaborative ? "workspace" : "", enabled:collaborative,
    workspaces:collaborative ? [{id:"workspace",version:1}] : []}, workspaceOwnerFilter:"", collapsedPageIds:new Set(), activePageId:"parent"};
  let canonical, uploaded = 0;
  const childState = {dirty:false, composing:false, title:""};
  const appearance = async icon => {
    calls.push({type:"appearance", icon});
    if (hooks.appearance) await hooks.appearance();
    canonical.page.icon = icon; canonical.revision++; canonical.page.document_sequence++;
    return clone(canonical);
  };
  const context = {State, $, PageMetadataSync:null, LinkedDatabaseItems:null, activeDatabaseDatePickerAnchor:null,
    window:{NotionPageDraft:Draft, NotionIcons:Icons, NotionAiNotes:{beforeNavigate:async()=>true}},
    document:{activeElement:null}, EditorCore:{deepClone:clone}, AbortController, FormData,
    requestAnimationFrame:fn=>fn(), clearTimeout(){}, setTimeout(){return 1;},
    setStatus:(message,type)=>statuses.push({message,type}), isNarrowSidebar:()=>false,
    setSidePeekCreatePresentation(){}, syncSidePeekStarterVisibility(){}, renderSidePeekBreadcrumb(){},
    closeSidePeekSharePopover(){},
    newOperationId:()=>"one-create", saveCollapsedPages(){}, renderSidePeek(){},
    expandPageInSidebar(){},
    sidePeekFocusSnapshot:()=>null, restoreSidePeekFocus(){},
    loadPages:async()=>{assert.equal(collaborative,false,"collaborative draft appearance must preserve the active editor");}, refreshPageNavigation:async()=>{},
    flushPageDraft:async()=>true, flushSyncedText:async()=>true,
    refreshOpenedPageAfterMutation:async()=>{}, refreshOtherOpenPagesAfterMutation:async()=>{},
    normalizedPageData:clone,
    pageCanChangeAppearance:page=>Boolean(page?.can_edit && !page.locked),
    pageCustomIconContentHtml:value=>value,
    setSidePeekEditorStatus:(message,failed)=>statuses.push({message,failed}),
    resolveSidePeekDatabaseContext:()=>null, syncSidePeekDatabaseTitle(){},
    renderPageLists:()=>calls.push({type:"list-refresh"}), renderBreadcrumbPopover(){},
    atomicSaveRequest:(holder,data,title,blocks)=>({base_revision:holder.serverRevision,operation_id:"one-body",page:{title},blocks}),
    ensureDraftParentLink:async()=>{calls.push({type:"parent-link"});},
    collaborativeFileBasePath:pageId=>collaborative ? `/api/notion/workspaces/workspace/pages/${pageId}/files` : "",
    authenticatedFetch:async()=>{calls.push({type:"upload",pageId:"child"});uploaded++;return {ok:true,json:async()=>({file:{id:"file_icon"}})};},
    ResumableFileUpload:{upload:async({pageId})=>{calls.push({type:"upload",pageId});uploaded++;return {file:{id:"file_icon",file_id:"file_icon",
      contract:"code_assist.notion.collaborative_file_authority.v1",document_id:pageId,state:"active",document_sequence:++canonical.page.document_sequence}};}},
    saveCollaborativeAppearance:async(pageId,updates)=>appearance(updates.icon),
    ensureSidePeekEditor:async holder=>{
      if (!holder.embedded) {
        holder.embedded = {
          state:()=>({...childState}), snapshot:()=>clone(canonical), focus(){},
          flush:async()=>{
            calls.push({type:"body-flush"});
            canonical.page.title = holder.data.page.title; canonical.blocks = clone(holder.data.blocks);
            childState.dirty = false; return clone(canonical);
          },
        };
      }
      return true;
    },
    api:async(url,options)=>{
      if (url.endsWith("/collaboration")) return clone(canonical);
      if (url === (collaborative ? "/api/notion/workspaces/workspace/pages" : "/api/notion/pages")) {
        calls.push({type:"create",body:clone(options.body)});
        canonical = {page:{id:"child",title:options.body.title || "제목 없음",icon:options.body.icon || "",can_edit:true,
          document_sequence:1,workspace_epoch:1,document_epoch:1},
          blocks:clone(options.body.blocks || []),revision:1,capabilities:{atomic_save:true,local_page_draft_create:true}};
        return collaborative ? {page:{id:"child"},workspace:{id:"workspace",version:2}} : clone(canonical);
      }
      if (url === "/api/notion/pages/child" && options?.method === "PATCH") return appearance(options.body.icon);
      if (url === "/api/notion/pages/child") {
        if (hooks.read) await hooks.read();
        return clone(canonical);
      }
      if (url === "/api/notion/pages/child/save") {
        calls.push({type:"body-save",body:clone(options.body)});
        assert.equal(options.body.base_revision, canonical.revision, "body save uses the revision after icon application");
        canonical.page.title = options.body.page.title; canonical.blocks = clone(options.body.blocks); canonical.revision++;
        return clone(canonical);
      }
      throw new Error("unexpected request " + url);
    },
  };
  vm.createContext(context);
  vm.runInContext([
    slice("function serverRevisionFrom(", "function normalizedPageData("),
    slice("function iconScope(", "function pageCanChangeAppearance("),
    slice("async function flushPageDraft(", "async function flushAllOpenDrafts("),
    slice("async function uploadPageIcon(", "async function openIconPicker("),
    slice("let SidePeekOpenSequence =", "async function openSidePeek(pageId"),
    slice("async function closeSidePeek(", "function resolveSidePeekDatabaseContext("),
    slice("function syncSidePeekEditorState(", "async function ensureSidePeekEditor("),
    slice("function draftAppearanceCurrent(", "async function openSidePeekAsFullPage("),
  ].join("\n"), context);
  function newHolder() {
    State.sidePeek = {pageId:"",data:{page:{title:"",icon:"",can_edit:true},blocks:[{id:"body",type:"paragraph",content:"",props:{}}]},
      draft:Draft.create({userId:"owner",workspaceId:State.navigation.workspaceId,ownerFilter:"",parentId:"parent",parentTitle:"부모",
        navigationEnabled:collaborative,collaborative,baseVersion:1}, "one-create"),editRevision:0,dirty:false};
    return State.sidePeek;
  }
  return {context, State, calls, statuses, hooks, childState, newHolder, canonical:()=>canonical, uploaded:()=>uploaded};
}

const timeout = setTimeout(()=>{throw new Error("draft appearance save did not settle; check for recursive flush waits");},5000);
(async()=>{
  {
    const h = harness(), holder = h.newHolder();
    await h.context.saveDraftIcon(holder, {icon:""});
    assert.equal(await h.context.closeSidePeek(), true);
    assert.equal(h.calls.filter(call=>call.type==="create").length, 0,
      "removing an absent icon and cancelling an empty draft do not create a page");
  }
  {
    const h = harness(), holder = h.newHolder();
    await h.context.saveDraftIcon(holder, {icon:"🌿"});
    assert.equal(holder.pageId, "child"); assert.equal(holder.dirty, false);
    assert.equal(h.canonical().page.icon, "🌿");
    assert.deepEqual(h.calls.map(c=>c.type), ["create"], "general creation already saves an emoji without a second write");
  }
  {
    const h = harness(), holder = h.newHolder(), file = png();
    h.hooks.appearance = async()=>{throw new Error("response unavailable");};
    await assert.rejects(h.context.saveDraftIcon(holder, {file}), /response unavailable/);
    assert.equal(holder.pageId, "child"); assert.equal(holder.pendingAppearance.uploadedValue, "nf:file_icon");
    assert.equal(await h.context.closeSidePeek(), false, "failed image application keeps the draft open");
    delete h.hooks.appearance;
    await h.context.saveDraftIcon(holder, {file});
    assert.equal(h.uploaded(), 1, "same picker retry reuses the uploaded file");
    assert.equal(h.calls.filter(c=>c.type === "create").length, 1);
    assert.equal(holder.pendingAppearance, null); assert.equal(holder.dirty, false);
    assert.equal(h.canonical().page.icon, "nf:file_icon");
  }
  {
    const h = harness(), holder = h.newHolder(), started = gate(), finish = gate();
    h.hooks.appearance = async()=>{started.resolve();await finish.promise;};
    const saving = h.context.saveDraftIcon(holder, {file:png()});
    await started.promise;
    holder.data.blocks[0].content = "아이콘 저장 중 추가한 본문"; h.context.markSidePeekDirty();
    finish.resolve(); await assert.rejects(saving, /저장이 아직/);
    assert.equal(holder.data.blocks[0].content, "아이콘 저장 중 추가한 본문");
    assert.equal(holder.dirty, true); assert.equal(holder.pendingAppearance, null);
    await h.context.saveSidePeek();
    assert.equal(h.canonical().blocks[0].content, "아이콘 저장 중 추가한 본문");
    assert.equal(holder.dirty, false);
  }
  {
    const h = harness(), holder = h.newHolder(), started = gate(), finish = gate();
    h.hooks.appearance = async()=>{started.resolve();await finish.promise;};
    const saving = h.context.saveDraftIcon(holder, {file:png()});
    await started.promise;
    const next = h.newHolder(), statusCount = h.statuses.length;
    finish.reject(new Error("old request failed")); await saving;
    assert.equal(h.State.sidePeek, next); assert.equal(next.dirty, false);
    assert.equal(next.saveError, undefined); assert.equal(h.statuses.length, statusCount, "old failure cannot show in a new draft");
  }
  {
    const h = harness(), holder = h.newHolder();
    holder.data.page.can_edit = false;
    await assert.rejects(h.context.saveDraftIcon(holder, {icon:"🌿"}), /변경할 수 없습니다/);
    assert.equal(h.calls.length, 0);
  }
  {
    const h = harness(true), holder = h.newHolder();
    await h.context.saveDraftIcon(holder, {icon:"🌿"});
    assert.deepEqual(h.calls.map(c=>c.type), ["create","body-flush","parent-link","appearance"]);
    assert.equal(h.canonical().page.icon, "🌿"); assert.equal(holder.dirty, false);
    h.hooks.appearance = async()=>{throw new Error("아이콘 저장 응답 없음");};
    await assert.rejects(h.context.saveDraftIcon(holder, {file:png()}), /아이콘 저장 응답 없음/);
    h.context.syncSidePeekEditorState(holder, h.childState);
    assert.equal(holder.dirty, true, "a clean child editor cannot hide pending icon changes");
    assert.equal(h.statuses.at(-1).failed, true, "child state keeps the icon failure and retry affordance");
    assert.equal(await h.context.closeSidePeek(), false);
    delete h.hooks.appearance;
    await h.context.saveSidePeek();
    assert.equal(holder.dirty, false); assert.equal(holder.pendingAppearance, null);
    assert.equal(h.uploaded(), 1);
    h.State.pages = [{id:"child",title:"old title",icon:"🌿"}];
    h.canonical().page.icon = "";
    h.context.syncSidePeekEditorState(holder, {...h.childState,dirty:true});
    assert.equal(h.State.pages[0].icon, "🌿", "unsaved child state does not claim saved metadata");
    h.context.syncSidePeekEditorState(holder, h.childState);
    assert.equal(h.State.pages[0].icon, "", "a saved iframe icon removal propagates to the parent list");
    assert.equal(h.calls.at(-1).type, "list-refresh");
    const count = h.calls.length;
    h.newHolder();h.context.syncSidePeekEditorState(holder, h.childState);
    assert.equal(h.calls.length, count, "a previous iframe cannot refresh another draft's page list");
  }
  console.log("UI-DRAFT-ICON-001: empty cancel, icon-only creation, file reuse, typing, stale errors, permissions and collaborative child state passed");
})().catch(error=>{console.error(error);process.exitCode=1;}).finally(()=>clearTimeout(timeout));
