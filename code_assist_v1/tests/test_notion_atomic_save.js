"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const core = require("../notion_static/notion_editor_core.js");
const fileUpload = require("../notion_static/notion_file_upload.js");
const databaseBridge = require("../notion_static/notion_database_bridge.js");
const { CollaborationEditor, loadPinnedYjs } = require("./notion_collaboration_test_runtime.js");
const sourcePath = path.join(__dirname, "..", "notion_static", "notion.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/\bboot\(\);\s*$/, "");

function nodeStub() {
  return {
    hidden: true,
    className: "",
    textContent: "",
    innerHTML: "",
    value: "",
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    setAttribute() {},
    removeAttribute() {},
    appendChild() {},
    replaceChildren() {},
    focus() {},
  };
}

const nodes = new Map();
const getNode = selector => {
  if (!nodes.has(selector)) nodes.set(selector, nodeStub());
  return nodes.get(selector);
};
const documentStub = {
  querySelector: getNode,
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement: nodeStub,
  documentElement: { dataset: {} },
  body: { classList: nodeStub().classList },
};
const locationStub = {
  origin: "http://localhost",
  href: "http://localhost/workspace",
  hostname: "localhost",
  pathname: "/workspace",
};
const windowStub = {
  NotionPageRename: require("../notion_static/notion_page_rename.js"),
  NotionEmbeddedEditorBridge: require("../notion_static/notion_embedded_editor.js"),
  NotionPageDraft: require("../notion_static/notion_page_draft.js"),
  NotionAiNotes: { beforeNavigate: async () => true },
  NotionEditorCore: core,
  NotionBlockStructure: require('../notion_static/notion_block_structure'),
  NotionCollaborationEditor: CollaborationEditor,
  NotionFileUpload: fileUpload,
  NotionDatabaseBridge: databaseBridge,
  NotionYjs: loadPinnedYjs(),
  CSS: { escape: value => String(value) },
  location: locationStub,
  addEventListener() {},
  matchMedia() { return { matches: false }; },
  open() {},
  confirm() { return false; },
};
const responseQueue = [];
const requests = [];
const sessionValues = new Map();
const sessionStorageStub = {
  get length() { return sessionValues.size; },
  key(index) { return [...sessionValues.keys()][index] ?? null; },
  getItem(key) { return sessionValues.has(String(key)) ? sessionValues.get(String(key)) : null; },
  setItem(key, value) { sessionValues.set(String(key), String(value)); },
  removeItem(key) { sessionValues.delete(String(key)); },
};
const context = {
  console,
  document: documentStub,
  window: windowStub,
  location: locationStub,
  history: { pushState() {}, replaceState() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  localStorage: { getItem() { return null; }, setItem() {} },
  sessionStorage: sessionStorageStub,
  fetch: async (url, init = {}) => {
    const requestBody = typeof init.body === "string" ? JSON.parse(init.body) : (init.body || null);
    requests.push({ url, method: init.method || "GET", body: requestBody });
    const response = responseQueue.shift();
    if (!response) throw new Error("unexpected fetch");
    if (response.throws) throw new Error(response.throws);
    if (response.wait) await response.wait;
    return {
      ok: response.status >= 200 && response.status < 300,
      status: response.status,
      json: async () => response.data,
    };
  },
  requestAnimationFrame: callback => callback(),
  setTimeout,
  clearTimeout,
  URL,
  URLSearchParams,
  FormData,
  Blob,
  crypto: globalThis.crypto,
  __responses: responseQueue,
  __requests: requests,
};
windowStub.document = documentStub;

const probe = `
  loadPages = async () => {};
  loadInboxItems = async () => {};
  loadPageHistory = async () => {};
  renderBreadcrumb = () => {};
  renderEditor = () => {};
  renderSidePeek = () => {};
  resetEditorHistory = () => {};
  let conflictModal = null;
  openModal = (title, html, actions) => { conflictModal = { title, html, actions }; };
  closeModal = () => {};

  function savedPage(revision, title = "Saved", blocks = []) {
    return pagePayload("page-1", revision, title, blocks);
  }

  function pagePayload(id, revision, title = "Saved", blocks = [], files = []) {
    return {
      capabilities: { atomic_save: true, local_page_draft_create: true },
      revision,
      page: {
        id, title, icon: "📄", cover: "blue", settings: { full_width: true },
        can_edit: true, owner_user_id: "tester", scope: "private", role: "owner", updated_at: 1,
      },
      blocks,
      files,
    };
  }

  function resetMain({ atomic = true, revision = 7, title = "Draft" } = {}) {
    clearTimeout(State.saveTimer);
    Object.assign(State, {
      activePageId: "page-1",
      pageData: {
        page: {
          id: "page-1", title, icon: "📄", cover: "blue", settings: { full_width: true },
          can_edit: true, owner_user_id: "tester", scope: "private", role: "owner", updated_at: 1,
        },
        blocks: [EditorCore.createBlock("paragraph", { content: "draft body" })],
        files: [],
      },
      dirty: true,
      saveTimer: null,
      saveInFlight: null,
      saveQueued: false,
      editRevision: 1,
      serverRevision: revision,
      atomicSaveEnabled: atomic,
      saveRetry: null,
      saveConflict: null,
    });
    State.sidePeek = {
      pageId: "", data: null, databaseContext: null, dirty: false, saveTimer: null,
      editRevision: 0, serverRevision: null, atomicSaveEnabled: false,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    document.querySelector("#pageTitle").value = title;
  }

  globalThis.__probePromise = (async () => {
    const result = {};

    document.body.dataset = {notionReady:"true"};
    resetMain({title:"Before rename"});
    State.dirty = false;
    __requests.length = 0;
    __responses.push({status:200,data:savedPage(8,"After rename",EditorCore.deepClone(State.pageData.blocks))});
    const rename = window.NotionPageRename.createSession({pageId:"page-1",title:"Before rename"}, {
      current:()=>true, acquire:async()=>mainRenameEditor(), saved() {},
    });
    await rename.save("After rename");
    result.rename = {requests:JSON.parse(JSON.stringify(__requests)),title:State.pageData.page.title,body:State.pageData.blocks[0].content,icon:State.pageData.page.icon};
    rename.dismiss();
    const staleRenameEditor = mainRenameEditor();
    State.activePageId = "other-open-page";
    __requests.length = 0;
    try { await staleRenameEditor.flush(); result.staleRenameError = ""; }
    catch (error) { result.staleRenameError = error.message; }
    result.staleRenameWrites = __requests.length;

    resetMain();
    __requests.length = 0;
    __responses.push({ status: 200, data: savedPage(8) });
    await savePage();
    result.atomicRequest = __requests[0];
    result.atomicDirty = State.dirty;
    result.atomicRevision = State.serverRevision;
    result.atomicRequestCount = __requests.length;

    resetMain({ atomic: false, revision: 99, title: "Legacy" });
    __requests.length = 0;
    __responses.push(
      { status: 200, data: {} },
      { status: 200, data: savedPage(null, "Legacy") },
    );
    await savePage();
    result.legacyRequests = __requests.map(item => ({ url: item.url, method: item.method }));

    resetMain({ revision: 20, title: "Retry" });
    __requests.length = 0;
    __responses.push({ throws: "connection lost" });
    await savePage();
    __responses.push({ status: 200, data: savedPage(21, "Retry") });
    await savePage();
    result.retryOperationIds = __requests.map(item => item.body?.operation_id);

    resetMain({ revision: 30, title: "Changed A" });
    __requests.length = 0;
    __responses.push({ throws: "connection lost" });
    await savePage();
    document.querySelector("#pageTitle").value = "Changed B";
    State.pageData.page.title = "Changed B";
    State.editRevision += 1;
    __responses.push({ status: 200, data: savedPage(31, "Changed B") });
    await savePage();
    result.changedOperationIds = __requests.map(item => item.body?.operation_id);

    resetMain({ revision: 80, title: "In flight" });
    __requests.length = 0;
    let releaseInFlight;
    const inFlightWait = new Promise(resolve => { releaseInFlight = resolve; });
    __responses.push({ status: 200, data: savedPage(81, "In flight"), wait: inFlightWait });
    const inFlightSave = savePage();
    State.pageData.blocks[0].content = "local edit after request";
    State.pageData.page.title = "Local edit after request";
    document.querySelector("#pageTitle").value = "Local edit after request";
    State.editRevision += 1;
    State.dirty = true;
    savePage();
    releaseInFlight();
    await inFlightSave;
    clearTimeout(State.saveTimer);
    State.saveTimer = null;
    result.inFlightEdit = {
      content: State.pageData.blocks[0].content,
      title: State.pageData.page.title,
      dirty: State.dirty,
      revision: State.serverRevision,
    };

    resetMain({ revision: 40, title: "Conflict draft" });
    __requests.length = 0;
    conflictModal = null;
    __responses.push({ status: 409, data: { error: "revision_conflict", current_revision: 41 } });
    await savePage();
    scheduleSave();
    result.conflict = {
      dirty: State.dirty,
      currentRevision: State.saveConflict?.currentRevision,
      timer: State.saveTimer,
      status: document.querySelector("#status").textContent,
      modalTitle: conflictModal?.title,
      actionLabels: conflictModal?.actions?.map(item => item.label) || [],
      requestCount: __requests.length,
    };

    State.activePageId = "";
    State.dirty = false;
    State.sidePeek = {
      pageId: "page-1",
      data: {
        page: {
          id: "page-1", title: "Peek", icon: "", cover: "", settings: {},
          can_edit: true, owner_user_id: "tester", scope: "private", role: "owner",
        },
        blocks: [EditorCore.createBlock("paragraph", { content: "peek body" })],
        files: [],
      },
      databaseContext: null,
      dirty: true,
      saveTimer: null,
      editRevision: 2,
      serverRevision: 50,
      atomicSaveEnabled: true,
      saveInFlight: null,
      saveQueued: false,
      saveRetry: null,
      saveConflict: null,
    };
    document.querySelector("#sidePeekTitle").value = "Peek changed";
    __requests.length = 0;
    __responses.push({ status: 200, data: savedPage(51, "Peek changed", State.sidePeek.data.blocks) });
    await saveSidePeek();
    result.sidePeek = {
      request: __requests[0],
      revision: State.sidePeek.serverRevision,
      dirty: State.sidePeek.dirty,
    };

    resetMain({ revision: 60, title: "Appearance draft" });
    __requests.length = 0;
    __responses.push(
      { status: 200, data: savedPage(61, "Appearance draft", State.pageData.blocks) },
      { status: 200, data: savedPage(61, "Appearance draft", State.pageData.blocks) },
      { status: 200, data: { ...savedPage(62, "Appearance draft", State.pageData.blocks), page: { ...savedPage(62).page, icon: "⭐" } } },
    );
    await updatePageAppearance({ icon: "⭐" });
    result.appearanceFlush = {
      requests: __requests.map(item => ({ url: item.url, method: item.method, baseRevision: item.body?.base_revision })),
      revision: State.serverRevision,
    };

    resetMain({ revision: 70, title: "Settings conflict" });
    __requests.length = 0;
    __responses.push({ status: 409, data: { error: "revision_conflict", current_revision: 71 } });
    await updatePageSettings({ full_width: false }, "page-1");
    result.settingsConflictRequests = __requests.map(item => ({ url: item.url, method: item.method }));

    result.revisionValidation = {
      zeroRevision: serverRevisionFrom({ revision: 0 }),
      negativeRevision: serverRevisionFrom({ revision: -1 }),
      positiveRevision: serverRevisionFrom({ revision: 1 }),
      zeroBaseRejected: (() => {
        try {
          atomicSaveRequest({}, savedPage(1), "Invalid", [], 0);
          return false;
        } catch {
          return true;
        }
      })(),
    };

    resetMain({ revision: 80, title: "Before refresh" });
    State.dirty = false;
    State.sidePeek = {
      pageId: "page-1", data: normalizedPageData(savedPage(80, "Before refresh")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 80, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    await refreshOpenedPageAfterMutation("page-1", savedPage(81, "After refresh"));
    result.crossSurfaceRefresh = {
      mainRevision: State.serverRevision,
      sideRevision: State.sidePeek.serverRevision,
      mainTitle: State.pageData.page.title,
      sideTitle: State.sidePeek.data.page.title,
    };

    resetMain({ revision: 82, title: "Local mutation draft" });
    await refreshOpenedPageAfterMutation("page-1", savedPage(83, "Server mutation"));
    result.refreshConflict = {
      revision: State.serverRevision,
      title: State.pageData.page.title,
      dirty: State.dirty,
      conflicted: Boolean(State.saveConflict),
      timer: State.saveTimer,
    };

    resetMain({ revision: 84, title: "Saved source" });
    State.dirty = false;
    State.sidePeek = {
      pageId: "unrelated-draft",
      data: normalizedPageData(pagePayload("unrelated-draft", 90, "Local unrelated draft")),
      databaseContext: null,
      dirty: true,
      saveTimer: null,
      editRevision: 2,
      serverRevision: 90,
      atomicSaveEnabled: true,
      saveInFlight: null,
      saveQueued: false,
      saveRetry: null,
      saveConflict: null,
    };
    State.sidePeek.data.page.title = "Local unrelated draft";
    __requests.length = 0;
    __responses.push({ status: 200, data: pagePayload("unrelated-draft", 90, "Server unchanged") });
    await refreshOtherOpenPagesAfterMutation("page-1");
    result.unrelatedDirtyRefresh = {
      dirty: State.sidePeek.dirty,
      conflicted: Boolean(State.sidePeek.saveConflict),
      title: State.sidePeek.data.page.title,
    };

    resetMain({ revision: 90, title: "Moved" });
    State.dirty = false;
    State.sidePeek = {
      pageId: "parent-1", data: normalizedPageData(pagePayload("parent-1", 100, "Parent")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 100, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    __requests.length = 0;
    __responses.push(
      { status: 200, data: pagePayload("page-1", 91, "Moved") },
      { status: 200, data: pagePayload("parent-1", 101, "Parent") },
      { status: 200, data: {items: []} },
    );
    await movePageSafely("page-1", { parent_id: "parent-1" });
    result.moveRefresh = {
      requests: __requests.map(item => ({ url: item.url, method: item.method })),
      mainRevision: State.serverRevision,
      sideRevision: State.sidePeek.serverRevision,
    };

    resetMain({ revision: 110, title: "Upload page" });
    State.dirty = false;
    State.uploadMode = "image";
    State.uploadInsertIndex = 1;
    State.uploadReplaceIndex = null;
    const uploadFile = new Blob(["image"], { type: "image/png" });
    Object.defineProperty(uploadFile, "name", { value: "image.png" });
    const uploaded = { id: "file-1", original_name: "image.png", mime_type: "image/png", size: 5 };
    const uploadedBlock = EditorCore.createBlock("image", { content: "image.png", props: { file_id: "file-1" } });
    __requests.length = 0;
    __responses.push(
      { status: 200, data: { status: "uploaded", file: uploaded } },
      { status: 200, data: pagePayload("page-1", 111, "Upload page", State.pageData.blocks, [uploaded]) },
      { status: 200, data: pagePayload("page-1", 112, "Upload page", [State.pageData.blocks[0], uploadedBlock], [uploaded]) },
      { status: 200, data: pagePayload("page-1", 112, "Upload page", [State.pageData.blocks[0], uploadedBlock], [uploaded]) },
    );
    await uploadSelectedFile(uploadFile);
    clearTimeout(State.saveTimer);
    State.saveTimer = null;
    result.uploadRefresh = {
      requests: __requests.map(item => ({ url: item.url, method: item.method, baseRevision: item.body?.base_revision })),
      revision: State.serverRevision,
      dirty: State.dirty,
      fileCount: State.pageData.files.length,
      hasUploadedBlock: State.pageData.blocks.some(item => item.props?.file_id === "file-1"),
    };

    resetMain({ revision: 120, title: "Form page" });
    State.dirty = false;
    const formBlock = EditorCore.createBlock("form", {
      props: { rows: [], properties: [{ id: "title", name: "Name", type: "title" }] },
    });
    State.pageData.blocks = [formBlock];
    const formOutput = { textContent: "" };
    const formButton = { disabled: false };
    let formReset = false;
    const form = {
      querySelectorAll() { return [{ dataset: { formField: "title" }, type: "text", value: "Response" }]; },
      querySelector(selector) {
        if (selector === "[type=submit]") return formButton;
        if (selector === "[data-form-result]") return formOutput;
        return null;
      },
      reset() { formReset = true; },
    };
    const responseRow = { title: "Response", page_id: "response-1" };
    const refreshedFormBlock = EditorCore.deepClone(formBlock);
    refreshedFormBlock.props.rows = [responseRow];
    __requests.length = 0;
    __responses.push(
      { status: 200, data: { status: "submitted", row: responseRow, message: "Done" } },
      { status: 200, data: pagePayload("page-1", 121, "Form page", [refreshedFormBlock]) },
    );
    await submitDatabaseForm(formBlock, form);
    result.formRefresh = {
      requests: __requests.map(item => ({ url: item.url, method: item.method })),
      revision: State.serverRevision,
      rowCount: State.pageData.blocks[0].props.rows.length,
      reset: formReset,
    };

    resetMain({ revision: 130, title: "Parent page" });
    State.dirty = false;
    State.sidePeek = {
      pageId: "page-1", data: normalizedPageData(pagePayload("page-1", 130, "Parent page")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 130, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    const childBlock = EditorCore.createBlock("child_page", { content: "Child", props: { page_id: "child-1", title: "Child" } });
    __requests.length = 0;
    __responses.push(
      { status: 200, data: pagePayload("page-1", 130, "Parent page") },
      { status: 200, data: pagePayload("child-1", 1, "Child") },
      { status: 200, data: pagePayload("page-1", 131, "Parent page", [childBlock]) },
    );
    await createSidePeekChildPage();
    const requestsAtDraftOpen = __requests.length;
    State.sidePeek.data.page.title = "Child";
    document.querySelector("#sidePeekTitle").value = "Child";
    markSidePeekDirty();
    await saveSidePeek();
    result.childRefresh = {
      requestsAtDraftOpen,
      requests: __requests.map(item => ({ url: item.url, method: item.method, parentId: item.body?.parent_id, operationId: item.body?.operation_id })),
      mainRevision: State.serverRevision,
      mainHasChild: State.pageData.blocks.some(item => item.props?.page_id === "child-1"),
      sidePageId: State.sidePeek.pageId,
      sideRevision: State.sidePeek.serverRevision,
    };

    resetMain({ revision: 140, title: "Ancestor" });
    State.dirty = false;
    State.sidePeek = {
      pageId: "other-open", data: normalizedPageData(pagePayload("other-open", 150, "Other")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 150, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    __requests.length = 0;
    __responses.push(
      { status: 200, data: pagePayload("restored-1", 2, "Restored") },
      { status: 200, data: pagePayload("page-1", 141, "Ancestor") },
      { status: 200, data: pagePayload("other-open", 151, "Other") },
    );
    await restorePageSafely("restored-1");
    result.restoreRefresh = {
      requests: __requests.map(item => ({ url: item.url, method: item.method })),
      mainRevision: State.serverRevision,
      sideRevision: State.sidePeek.serverRevision,
    };

    resetMain({ revision: 180, title: "Version source" });
    State.drawerMode = "history";
    State.dirty = false;
    State.sidePeek = {
      pageId: "version-synced", data: normalizedPageData(pagePayload("version-synced", 190, "Version synced")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 190, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    window.confirm = () => true;
    __requests.length = 0;
    __responses.push(
      { status: 200, data: pagePayload("page-1", 181, "Restored version") },
      { status: 200, data: pagePayload("version-synced", 191, "Version synced") },
    );
    await restorePageVersion("version-1");
    window.confirm = () => false;
    result.versionRestoreRefresh = {
      requests: __requests.map(item => ({ url: item.url, method: item.method, expectedRevision: item.body?.expected_revision })),
      mainRevision: State.serverRevision,
      sideRevision: State.sidePeek.serverRevision,
      title: State.pageData.page.title,
    };

    resetMain({ revision: 160, title: "Archive root" });
    State.dirty = false;
    State.pages = [
      { id: "page-1", parent_id: "", scope: "own" },
      { id: "archive-child", parent_id: "page-1", scope: "own" },
    ];
    State.sidePeek = {
      pageId: "archive-child", data: normalizedPageData(pagePayload("archive-child", 10, "Archive child")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 10, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    __requests.length = 0;
    __responses.push({ status: 200, data: { status: "archived", id: "page-1", archived_count: 2 } });
    await performArchivePage("page-1");
    result.archiveSafety = {
      requests: __requests.map(item => ({ url: item.url, method: item.method })),
      activePageId: State.activePageId,
      sidePageId: State.sidePeek.pageId,
      dirty: State.dirty,
    };

    resetMain({ revision: 170, title: "Synced source" });
    State.sidePeek = {
      pageId: "synced-copy", data: normalizedPageData(pagePayload("synced-copy", 200, "Synced copy")), databaseContext: null,
      dirty: false, saveTimer: null, editRevision: 0, serverRevision: 200, atomicSaveEnabled: true,
      saveInFlight: null, saveQueued: false, saveRetry: null, saveConflict: null,
    };
    __requests.length = 0;
    __responses.push(
      { status: 200, data: savedPage(171, "Synced source", State.pageData.blocks) },
      { status: 200, data: pagePayload("synced-copy", 201, "Synced copy") },
    );
    await savePage();
    result.syncedSurfaceRefresh = {
      requests: __requests.map(item => ({ url: item.url, method: item.method })),
      mainRevision: State.serverRevision,
      sideRevision: State.sidePeek.serverRevision,
    };

    State.user = { id: "draft-user" };
    State.navigation.workspaceId = "draft-workspace";
    State.workspaceOwnerFilter = "";
    resetMain({ revision: 220, title: "Recovered draft" });
    State.pageData.blocks[0].content = "network draft";
    State.pageData.blocks[0].created_at = 10;
    State.pageData.blocks[0].updated_at = 11;
    State.pageData.blocks[0].position = 1;
    document.querySelector("#pageTitle").value = "Recovered draft";
    const failedDraftRequest = atomicSaveRequest(
      State,
      State.pageData,
      "Recovered draft",
      State.pageData.blocks,
      State.serverRevision,
    );
    persistMainEditorDraft();
    const storedDraftCount = sessionStorage.length;
    State.pageData = normalizedPageData(savedPage(
      220,
      "Recovered draft",
      [EditorCore.createBlock("paragraph", {
        id: State.pageData.blocks[0].id,
        content: "server body",
      })],
    ));
    State.serverRevision = 220;
    State.editRevision = 0;
    State.dirty = false;
    State.saveRetry = null;
    State.saveConflict = null;
    const recoveredDraft = restoreMainEditorDraft("page-1");
    State.pageData.blocks = State.pageData.blocks.map(EditorCore.normalizeBlock);
    document.querySelector("#pageTitle").value = State.pageData.page.title;
    const retryDraftRequest = atomicSaveRequest(
      State,
      State.pageData,
      State.pageData.page.title,
      State.pageData.blocks,
      State.serverRevision,
    );
    State.pageData = normalizedPageData(savedPage(
      221,
      "Recovered draft",
      [EditorCore.createBlock("paragraph", { content: "new server body" })],
    ));
    State.serverRevision = 221;
    State.editRevision = 0;
    State.dirty = false;
    State.saveRetry = null;
    State.saveConflict = null;
    const conflictedDraft = restoreMainEditorDraft("page-1");
    result.draftRecovery = {
      storedDraftCount,
      recovered: recoveredDraft?.conflicted === false,
      exactRequestReused: JSON.stringify(failedDraftRequest) === JSON.stringify(retryDraftRequest),
      operationReused: failedDraftRequest.operation_id === retryDraftRequest.operation_id,
      conflictRecovered: conflictedDraft?.conflicted === true,
      conflictRevision: State.saveConflict?.currentRevision,
    };
    clearMainEditorDraft("page-1");
    result.draftRecovery.clearedDraftCount = sessionStorage.length;

    resetMain({ revision: 210, title: "Publish source" });
    State.dirty = false;
    conflictModal = null;
    __requests.length = 0;
    __responses.push(
      {
        status: 200,
        data: {
          page: State.pageData.page,
          markdown: "# Publish source\\n",
          image_count: 0,
          file_count: 0,
          source_snapshot: { source_snapshot_sha256: "a".repeat(64), revision: 210 },
        },
      },
      { status: 200, data: { status: "published", source_id: "source-1" } },
    );
    await publishPageToSecondBrain("page-1");
    const approvalAction = conflictModal?.actions?.find(item => item.primary);
    await approvalAction.action();
    result.publicationSnapshot = {
      requests: __requests.map(item => ({ url: item.url, method: item.method, body: item.body })),
      approvalLabel: approvalAction.label,
    };

    result.capabilityGate = {
      explicit: atomicSaveSupported({ capabilities: { atomic_save: true }, revision: 1 }),
      revisionOnly: atomicSaveSupported({ revision: 1 }),
      absent: atomicSaveSupported({}),
    };
    return result;
  })();
`;

vm.runInNewContext(`${source}\n${probe}`, context, { filename: "notion.js" });

(async () => {
  const result = await context.__probePromise;

  assert.equal(result.atomicRequestCount, 1, "atomic save uses one request");
  assert.equal(result.atomicRequest.url, "/api/notion/pages/page-1/save");
  assert.equal(result.atomicRequest.method, "POST");
  assert.equal(result.atomicRequest.body.base_revision, 7);
  assert.equal(typeof result.atomicRequest.body.operation_id, "string");
  assert.equal(result.atomicRequest.body.page.title, "Draft");
  assert.equal(result.atomicRequest.body.page.icon, "📄");
  assert.equal(result.atomicRequest.body.page.cover, "blue");
  assert.equal(result.atomicRequest.body.page.settings.full_width, true);
  assert.equal(result.atomicRequest.body.blocks.length, 1);
  assert.equal(result.atomicDirty, false);
  assert.equal(result.atomicRevision, 8);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.legacyRequests)),
    [
      { url: "/api/notion/pages/page-1", method: "PATCH" },
      { url: "/api/notion/pages/page-1/blocks", method: "PUT" },
    ],
    "unmigrated pages retain the legacy two-request save path",
  );

  assert.equal(result.retryOperationIds.length, 2);
  assert.equal(result.retryOperationIds[0], result.retryOperationIds[1], "the same payload reuses its operation id");
  assert.notEqual(result.changedOperationIds[0], result.changedOperationIds[1], "a changed payload receives a new operation id");
  assert.equal(result.inFlightEdit.content, "local edit after request");
  assert.equal(result.inFlightEdit.title, "Local edit after request");
  assert.equal(result.inFlightEdit.dirty, true, "edits made during an in-flight save remain dirty");
  assert.equal(result.inFlightEdit.revision, 81, "the queued edit advances from the acknowledged server revision");

  assert.equal(result.conflict.dirty, true, "a conflicting draft remains dirty");
  assert.equal(result.conflict.currentRevision, 41);
  assert.equal(result.conflict.timer, null, "conflict disables automatic retry");
  assert.match(result.conflict.status, /편집본.*보존/);
  assert.equal(result.conflict.modalTitle, "저장 충돌");
  assert.ok(result.conflict.actionLabels.includes("서버 버전 새 탭에서 보기"));
  assert.ok(result.conflict.actionLabels.includes("편집본 버리고 다시 불러오기"));
  assert.equal(result.conflict.requestCount, 1, "a conflict never falls back to legacy writes");

  assert.equal(result.sidePeek.request.url, "/api/notion/pages/page-1/save");
  assert.equal(result.sidePeek.request.body.base_revision, 50);
  assert.equal(result.sidePeek.request.body.page.title, "Peek changed");
  assert.equal(result.sidePeek.revision, 51);
  assert.equal(result.sidePeek.dirty, false);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.appearanceFlush.requests)),
    [
      { url: "/api/notion/pages/page-1/save", method: "POST", baseRevision: 60 },
      { url: "/api/notion/pages/page-1", method: "GET" },
      { url: "/api/notion/pages/page-1", method: "PATCH" },
    ],
    "appearance changes flush the complete atomic draft before their PATCH",
  );
  assert.equal(result.appearanceFlush.revision, 62, "PATCH responses advance the local server revision");
  assert.deepEqual(
    JSON.parse(JSON.stringify(result.settingsConflictRequests)),
    [{ url: "/api/notion/pages/page-1/save", method: "POST" }],
    "settings changes never PATCH after a conflicting draft save",
  );

  assert.equal(result.revisionValidation.zeroRevision, null);
  assert.equal(result.revisionValidation.negativeRevision, null);
  assert.equal(result.revisionValidation.positiveRevision, 1);
  assert.equal(result.revisionValidation.zeroBaseRejected, true, "base_revision must be a positive integer");

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.crossSurfaceRefresh)),
    { mainRevision: 81, sideRevision: 81, mainTitle: "After refresh", sideTitle: "After refresh" },
    "one server mutation refreshes both clean open surfaces",
  );
  assert.equal(result.refreshConflict.revision, 83);
  assert.equal(result.refreshConflict.title, "Local mutation draft", "refresh never overwrites a dirty draft");
  assert.equal(result.refreshConflict.dirty, true);
  assert.equal(result.refreshConflict.conflicted, true);
  assert.equal(result.refreshConflict.timer, null, "an external mutation conflict stops autosave");
  assert.deepEqual(
    JSON.parse(JSON.stringify(result.unrelatedDirtyRefresh)),
    { dirty: true, conflicted: false, title: "Local unrelated draft" },
    "refreshing an unrelated page at the same revision preserves its draft without a false conflict",
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.moveRefresh.requests)),
    [
      { url: "/api/notion/pages/page-1/move", method: "POST" },
      { url: "/api/notion/pages/parent-1", method: "GET" },
      { url: "/api/notion/pages/page-1/backlinks", method: "GET" },
    ],
  );
  assert.equal(result.moveRefresh.mainRevision, 91);
  assert.equal(result.moveRefresh.sideRevision, 101);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.uploadRefresh.requests)),
    [
      { url: "/api/notion/pages/page-1/files", method: "POST" },
      { url: "/api/notion/pages/page-1", method: "GET" },
      { url: "/api/notion/pages/page-1/save", method: "POST", baseRevision: 111 },
      { url: "/api/notion/pages/page-1", method: "GET" },
    ],
    "file insertion refreshes the upload revision before its atomic block save",
  );
  assert.equal(result.uploadRefresh.revision, 112);
  assert.equal(result.uploadRefresh.dirty, false);
  assert.equal(result.uploadRefresh.fileCount, 1);
  assert.equal(result.uploadRefresh.hasUploadedBlock, true);

  assert.equal(result.formRefresh.requests.length, 2);
  assert.match(result.formRefresh.requests[0].url, /^\/api\/notion\/pages\/page-1\/forms\/.+\/submit$/);
  assert.equal(result.formRefresh.requests[0].method, "POST");
  assert.deepEqual(
    JSON.parse(JSON.stringify(result.formRefresh.requests[1])),
    { url: "/api/notion/pages/page-1", method: "GET" },
  );
  assert.equal(result.formRefresh.revision, 121);
  assert.equal(result.formRefresh.rowCount, 1);
  assert.equal(result.formRefresh.reset, true);

  assert.equal(result.childRefresh.requestsAtDraftOpen, 1, "opening a child draft only checks its parent");
  assert.equal(result.childRefresh.requests[0].method, "GET");
  assert.equal(result.childRefresh.requests[1].url, "/api/notion/pages");
  assert.equal(result.childRefresh.requests[1].method, "POST");
  assert.equal(result.childRefresh.requests[1].parentId, "page-1");
  assert.ok(result.childRefresh.requests[1].operationId);
  assert.deepEqual(
    result.childRefresh.requests.slice(2).map(item => item.url),
    ["/api/notion/pages/page-1"],
  );
  assert.equal(result.childRefresh.mainRevision, 131);
  assert.equal(result.childRefresh.mainHasChild, true);
  assert.equal(result.childRefresh.sidePageId, "child-1");
  assert.equal(result.childRefresh.sideRevision, 1);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.restoreRefresh.requests)),
    [
      { url: "/api/notion/trash/restored-1/restore", method: "POST" },
      { url: "/api/notion/pages/page-1", method: "GET" },
      { url: "/api/notion/pages/other-open", method: "GET" },
    ],
  );
  assert.equal(result.restoreRefresh.mainRevision, 141);
  assert.equal(result.restoreRefresh.sideRevision, 151);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.versionRestoreRefresh.requests)),
    [
      { url: "/api/notion/pages/page-1/versions/version-1/restore", method: "POST", expectedRevision: 180 },
      { url: "/api/notion/pages/version-synced", method: "GET" },
    ],
  );
  assert.equal(result.versionRestoreRefresh.mainRevision, 181);
  assert.equal(result.versionRestoreRefresh.sideRevision, 191);
  assert.equal(result.versionRestoreRefresh.title, "Restored version");

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.archiveSafety.requests)),
    [{ url: "/api/notion/pages/page-1", method: "DELETE" }],
  );
  assert.equal(result.archiveSafety.activePageId, "");
  assert.equal(result.archiveSafety.sidePageId, "");
  assert.equal(result.archiveSafety.dirty, false);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.syncedSurfaceRefresh.requests)),
    [
      { url: "/api/notion/pages/page-1/save", method: "POST" },
      { url: "/api/notion/pages/synced-copy", method: "GET" },
    ],
    "a save refreshes another open page that may contain a propagated synced block",
  );
  assert.equal(result.syncedSurfaceRefresh.mainRevision, 171);
  assert.equal(result.syncedSurfaceRefresh.sideRevision, 201);

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.draftRecovery)),
    {
      storedDraftCount: 1,
      recovered: true,
      exactRequestReused: true,
      operationReused: true,
      conflictRecovered: true,
      conflictRevision: 221,
      clearedDraftCount: 0,
    },
    "a failed atomic request survives reload, reuses its exact request, and fails closed on revision drift",
  );

  assert.deepEqual(
    JSON.parse(JSON.stringify(result.publicationSnapshot.requests)),
    [
      { url: "/api/notion/pages/page-1/publish_preview", method: "GET", body: null },
      {
        url: "/api/notion/pages/page-1/publish_second_brain",
        method: "POST",
        body: { expected_source_snapshot_sha256: "a".repeat(64) },
      },
    ],
    "publication approval binds the commit to the previewed source snapshot",
  );
  assert.equal(result.publicationSnapshot.approvalLabel, "승인 후 업로드");

  assert.equal(result.capabilityGate.explicit, true);
  assert.equal(result.capabilityGate.revisionOnly, false, "revision alone must not opt a legacy store into atomic saves");
  assert.equal(result.capabilityGate.absent, false);

  assert.equal(result.rename.requests.length,1);
  assert.equal(result.rename.requests[0].url,"/api/notion/pages/page-1/save");
  assert.equal(result.rename.requests[0].method,"POST");
  assert.equal(result.rename.title,"After rename");
  assert.equal(result.rename.body,"draft body");
  assert.equal(result.rename.icon,"📄");
  assert.match(result.staleRenameError,/달라졌습니다/);
  assert.equal(result.staleRenameWrites,0,"an old rename session cannot save a newly opened page");
  console.log("notion_atomic_save: ok (including page rename through the real atomic editor)");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
