"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const EditorCore = require("../notion_static/notion_editor_core.js");
const fileUpload = require("../notion_static/notion_file_upload.js");
const databaseBridge = require("../notion_static/notion_database_bridge.js");
const { CollaborationEditor, loadPinnedYjs } = require("./notion_collaboration_test_runtime.js");
const sourcePath = path.join(__dirname, "..", "notion_static", "notion.js");
const source = fs.readFileSync(sourcePath, "utf8").replace(/\bboot\(\);\s*$/, "");

function inertNode() {
  return {
    hidden: true,
    className: "",
    textContent: "",
    innerHTML: "",
    value: "",
    dataset: {},
    classList: {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    },
    addEventListener() {},
    removeEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    setAttribute() {},
    appendChild() {},
    focus() {},
  };
}

const nodes = new Map();
const getNode = selector => {
  if (!nodes.has(selector)) nodes.set(selector, inertNode());
  return nodes.get(selector);
};
const documentStub = {
  querySelector: getNode,
  querySelectorAll() { return []; },
  addEventListener() {},
  createElement: inertNode,
  documentElement: { dataset: {} },
  body: { classList: inertNode().classList },
};
const locationStub = {
  origin: "http://localhost",
  href: "http://localhost/workspace",
  hostname: "localhost",
  pathname: "/workspace",
};
const windowStub = {
  NotionEditorCore: EditorCore,
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
const context = {
  console,
  document: documentStub,
  window: windowStub,
  location: locationStub,
  history: { pushState() {}, replaceState() {} },
  navigator: { clipboard: { writeText: async () => {} } },
  localStorage: { getItem() { return null; }, setItem() {} },
  fetch: async (url, init = {}) => {
    const body = typeof init.body === "string" ? JSON.parse(init.body) : (init.body || null);
    requests.push({ url, method: init.method || "GET", body });
    const response = responseQueue.shift();
    if (!response) throw new Error(`unexpected fetch: ${init.method || "GET"} ${url}`);
    if (response.throws) throw new Error(response.throws);
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

const probe = String.raw`
  loadPages = async () => {};
  loadInboxItems = async () => {};
  loadPageHistory = async () => {};
  renderBreadcrumb = () => {};
  renderEditor = () => {};
  renderSidePeek = () => {};
  resetEditorHistory = () => {};

  let latestModal = null;
  let closeCount = 0;
  openModal = (title, html, actions = []) => {
    latestModal = { title, html, actions };
  };
  closeModal = () => { closeCount += 1; };

  const PREVIEW_HASH = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

  function pagePayload(id, revision, title, blocks = []) {
    return {
      capabilities: { atomic_save: true },
      revision,
      page: {
        id,
        title,
        icon: "📄",
        cover: "",
        settings: { full_width: false },
        can_edit: true,
        owner_user_id: "tester",
        scope: "private",
        role: "owner",
        updated_at: revision,
      },
      blocks,
      files: [],
    };
  }

  function previewPayload(hash = PREVIEW_HASH) {
    return {
      markdown: "# Publication preview",
      source_snapshot: { source_snapshot_sha256: hash },
    };
  }

  function copyRequests() {
    return __requests.map(request => ({
      url: request.url,
      method: request.method,
      body: request.body == null ? null : EditorCore.deepClone(request.body),
    }));
  }

  function resetScenario({ mainDirty = false, sideDirty = false } = {}) {
    clearTimeout(State.saveTimer);
    clearTimeout(State.sidePeek?.saveTimer);
    __requests.length = 0;
    __responses.length = 0;
    latestModal = null;
    closeCount = 0;

    const mainBlocks = [EditorCore.createBlock("paragraph", { content: "main draft body" })];
    const sideBlocks = [EditorCore.createBlock("paragraph", { content: "side draft body" })];
    State.activePageId = "page-1";
    State.pageData = normalizedPageData(pagePayload("page-1", 7, "Main draft", mainBlocks));
    Object.assign(State, {
      dirty: mainDirty,
      saveTimer: null,
      saveInFlight: null,
      saveQueued: false,
      editRevision: mainDirty ? 1 : 0,
      serverRevision: 7,
      atomicSaveEnabled: true,
      saveRetry: null,
      saveConflict: null,
    });
    State.sidePeek = {
      pageId: sideDirty ? "page-1" : "",
      data: sideDirty ? normalizedPageData(pagePayload("page-1", 11, "Side draft", sideBlocks)) : null,
      databaseContext: null,
      dirty: sideDirty,
      saveTimer: null,
      editRevision: sideDirty ? 1 : 0,
      serverRevision: sideDirty ? 11 : null,
      atomicSaveEnabled: sideDirty,
      saveInFlight: null,
      saveQueued: false,
      saveRetry: null,
      saveConflict: null,
    };
    document.querySelector("#pageTitle").value = "Main draft";
    document.querySelector("#sidePeekTitle").value = "Side draft";
    return { mainBlocks, sideBlocks };
  }

  globalThis.__publicationProbe = (async () => {
    const result = { previewHash: PREVIEW_HASH };

    const main = resetScenario({ mainDirty: true });
    __responses.push(
      { status: 200, data: pagePayload("page-1", 8, "Main draft", main.mainBlocks) },
      { status: 200, data: previewPayload() },
    );
    await publishSecondBrain();
    result.mainDirty = {
      requests: copyRequests(),
      dirty: State.dirty,
      modalTitle: latestModal?.title || "",
      modalActions: latestModal?.actions?.map(action => ({ label: action.label, primary: Boolean(action.primary) })) || [],
      responsesRemaining: __responses.length,
    };

    const side = resetScenario({ sideDirty: true });
    __responses.push(
      { status: 200, data: pagePayload("page-1", 12, "Side draft", side.sideBlocks) },
      { status: 200, data: previewPayload() },
    );
    await publishSecondBrain();
    result.sideDirty = {
      requests: copyRequests(),
      dirty: State.sidePeek.dirty,
      modalTitle: latestModal?.title || "",
      responsesRemaining: __responses.length,
    };

    resetScenario();
    __responses.push({ status: 200, data: previewPayload() });
    await publishSecondBrain();
    result.previewOnly = {
      requests: copyRequests(),
      modalTitle: latestModal?.title || "",
      responsesRemaining: __responses.length,
    };

    resetScenario();
    __responses.push({ status: 200, data: previewPayload() });
    await publishSecondBrain();
    const approveAction = latestModal?.actions?.find(action => action.primary);
    __responses.push({
      status: 200,
      data: { source_id: "source-1", wiki_path: "wiki/page-1.md", image_count: 0, graph_edges_added: 0 },
    });
    await approveAction?.action();
    result.approval = {
      requests: copyRequests(),
      closeCount,
      modalTitle: latestModal?.title || "",
      responsesRemaining: __responses.length,
    };

    resetScenario({ mainDirty: true });
    __responses.push({
      status: 409,
      data: { error: "revision_conflict", message: "revision conflict", current_revision: 8 },
    });
    await publishSecondBrain();
    result.mainConflict = {
      requests: copyRequests(),
      dirty: State.dirty,
      conflicted: Boolean(State.saveConflict),
      responsesRemaining: __responses.length,
    };

    resetScenario({ sideDirty: true });
    __responses.push({
      status: 409,
      data: { error: "revision_conflict", message: "revision conflict", current_revision: 12 },
    });
    await publishSecondBrain();
    result.sideConflict = {
      requests: copyRequests(),
      dirty: State.sidePeek.dirty,
      conflicted: Boolean(State.sidePeek.saveConflict),
      responsesRemaining: __responses.length,
    };

    resetScenario();
    __responses.push({ status: 503, data: { error: "preview_unavailable", message: "preview unavailable" } });
    await publishSecondBrain();
    result.previewFailure = {
      requests: copyRequests(),
      modalOpened: Boolean(latestModal),
      responsesRemaining: __responses.length,
    };

    resetScenario();
    __responses.push({ status: 200, data: previewPayload() });
    await publishSecondBrain();
    const cancelAction = latestModal?.actions?.find(action => !action.primary);
    await cancelAction?.action();
    result.cancel = {
      requests: copyRequests(),
      closeCount,
      responsesRemaining: __responses.length,
    };

    return result;
  })();
`;

vm.runInNewContext(`${source}\n${probe}`, context, { filename: sourcePath });

function requestShape(request) {
  return { method: request.method, url: request.url };
}

function assertNoPublishMutation(requestList, label) {
  assert.equal(
    requestList.some(request => request.method === "POST" && request.url.endsWith("/publish_second_brain")),
    false,
    `${label} must not issue the publication mutation`,
  );
}

(async () => {
  const result = JSON.parse(JSON.stringify(await context.__publicationProbe));
  const pageBase = "/api/notion/pages/page-1";

  assert.deepEqual(
    result.mainDirty.requests.map(requestShape),
    [
      { method: "POST", url: `${pageBase}/save` },
      { method: "GET", url: `${pageBase}/publish_preview` },
    ],
    "a dirty main editor must finish its atomic save before requesting a preview",
  );
  assert.deepEqual(
    Object.keys(result.mainDirty.requests[0].body).sort(),
    ["base_revision", "blocks", "operation_id", "page"],
  );
  assert.equal(result.mainDirty.requests[0].body.base_revision, 7);
  assert.equal(result.mainDirty.requests[0].body.page.title, "Main draft");
  assert.equal(result.mainDirty.requests[0].body.blocks[0].content, "main draft body");
  assert.equal(result.mainDirty.requests[1].body, null);
  assert.equal(result.mainDirty.dirty, false);
  assert.equal(result.mainDirty.modalTitle, "Second Brain 업로드 확인");
  assert.deepEqual(result.mainDirty.modalActions, [
    { label: "취소", primary: false },
    { label: "승인 후 업로드", primary: true },
  ]);
  assert.equal(result.mainDirty.responsesRemaining, 0);
  assertNoPublishMutation(result.mainDirty.requests, "main-dirty preview");

  assert.deepEqual(
    result.sideDirty.requests.map(requestShape),
    [
      { method: "POST", url: `${pageBase}/save` },
      { method: "GET", url: `${pageBase}/publish_preview` },
    ],
    "a dirty side peek must finish its atomic save before requesting a preview",
  );
  assert.deepEqual(
    Object.keys(result.sideDirty.requests[0].body).sort(),
    ["base_revision", "blocks", "operation_id", "page"],
  );
  assert.equal(result.sideDirty.requests[0].body.base_revision, 11);
  assert.equal(result.sideDirty.requests[0].body.page.title, "Side draft");
  assert.equal(result.sideDirty.requests[0].body.blocks[0].content, "side draft body");
  assert.equal(result.sideDirty.requests[1].body, null);
  assert.equal(result.sideDirty.dirty, false);
  assert.equal(result.sideDirty.modalTitle, "Second Brain 업로드 확인");
  assert.equal(result.sideDirty.responsesRemaining, 0);
  assertNoPublishMutation(result.sideDirty.requests, "side-dirty preview");

  assert.deepEqual(result.previewOnly.requests.map(requestShape), [
    { method: "GET", url: `${pageBase}/publish_preview` },
  ]);
  assert.equal(result.previewOnly.requests[0].body, null);
  assert.equal(result.previewOnly.modalTitle, "Second Brain 업로드 확인");
  assert.equal(result.previewOnly.responsesRemaining, 0);
  assertNoPublishMutation(result.previewOnly.requests, "preview-only action");

  assert.deepEqual(result.approval.requests.map(requestShape), [
    { method: "GET", url: `${pageBase}/publish_preview` },
    { method: "POST", url: `${pageBase}/publish_second_brain` },
  ]);
  assert.equal(result.approval.requests[0].body, null);
  assert.deepEqual(result.approval.requests[1].body, {
    expected_source_snapshot_sha256: result.previewHash,
  });
  assert.equal(result.approval.closeCount, 1);
  assert.equal(result.approval.modalTitle, "Published");
  assert.equal(result.approval.responsesRemaining, 0);

  for (const [label, scenario, expectedBaseRevision] of [
    ["main save conflict", result.mainConflict, 7],
    ["side-peek save conflict", result.sideConflict, 11],
  ]) {
    assert.deepEqual(scenario.requests.map(requestShape), [
      { method: "POST", url: `${pageBase}/save` },
    ]);
    assert.equal(scenario.requests[0].body.base_revision, expectedBaseRevision);
    assert.equal(scenario.dirty, true);
    assert.equal(scenario.conflicted, true);
    assert.equal(scenario.responsesRemaining, 0);
    assertNoPublishMutation(scenario.requests, label);
  }

  assert.deepEqual(result.previewFailure.requests.map(requestShape), [
    { method: "GET", url: `${pageBase}/publish_preview` },
  ]);
  assert.equal(result.previewFailure.modalOpened, false);
  assert.equal(result.previewFailure.responsesRemaining, 0);
  assertNoPublishMutation(result.previewFailure.requests, "preview failure");

  assert.deepEqual(result.cancel.requests.map(requestShape), [
    { method: "GET", url: `${pageBase}/publish_preview` },
  ]);
  assert.equal(result.cancel.closeCount, 1);
  assert.equal(result.cancel.responsesRemaining, 0);
  assertNoPublishMutation(result.cancel.requests, "cancel action");

  console.log("notion publication UI tests passed");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
