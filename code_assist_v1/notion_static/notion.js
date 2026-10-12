const State = {
  user: null,
  csrfToken: "",
  pages: [],
  overview: null,
  outlook: null,
  activePageId: "",
  pageLoadSequence: 0,
  pageData: null,
  users: [],
  sessions: [],
  dirty: false,
  saveTimer: null,
  saveInFlight: null,
  saveQueued: false,
  editRevision: 0,
  serverRevision: null,
  atomicSaveEnabled: false,
  saveRetry: null,
  saveConflict: null,
  uploadMode: "file",
  uploadInsertIndex: null,
  uploadReplaceIndex: null,
  drawerMode: "",
  menuIndex: -1,
  menuMode: "insert",
  sidebarMode: "pages",
  searchItems: [],
  activeSearchPageId: "",
  searchTitleOnly: false,
  searchTimer: null,
  searchRequestSequence: 0,
  pageFind: { query: "", matches: [], index: -1 },
  dbSearchTimer: null,
  databaseFullPageBlockId: "",
  databaseSync: {
    timer: null,
    inFlight: null,
    queued: false,
    generation: 0,
  },
  linkBlockIndex: -1,
  workspaceOwnerFilter: "",
  trashItems: [],
  publicLink: null,
  shareAccess: null,
  workspaceAccess: null,
  collapsedPageIds: new Set(),
  pageDrag: { id: "", mode: "", targetId: "" },
  sidePeek: {
    pageId: "",
    data: null,
    databaseContext: null,
    dirty: false,
    saveTimer: null,
    editRevision: 0,
    serverRevision: null,
    atomicSaveEnabled: false,
    saveInFlight: null,
    saveQueued: false,
    saveRetry: null,
    saveConflict: null,
  },
  favorites: new Set(),
  navigation: {
    enabled: false,
    authoritative: false,
    workspaces: [],
    workspaceId: "",
    snapshot: null,
    favoriteKeys: new Set(),
    favoritePositions: new Map(),
    sections: [],
    sequence: 0,
    visitedWorkspaceIds: new Set(),
    mutationReserved: false,
    mutationInFlight: null,
    preferenceTimer: null,
    settingsDraft: null,
    focusRestore: null,
    pendingPageCreate: null,
    pendingPageLifecycle: null,
    collaborativeSharing: false,
    collaborativeEditing: false,
    collaborativeFiles: false,
    collaborativeHistory: false,
    collaborativeSearch: false,
  },
  workspacePopoverOpen: false,
  comments: [],
  inboxItems: [],
  liveCollaboration: {
    socket: null,
    generation: 0,
    clientId: "",
    sessionBindingSha256: "",
    liveCursor: 0,
    heartbeatTimer: null,
    syncTimer: null,
    reconnectTimer: null,
    reconnectAttempt: 0,
    connected: false,
    syncPending: false,
    refreshTimer: null,
    participants: new Map(),
    clientSequence: 1,
    durableCursor: 0,
    workspaceEpoch: 0,
    documentEpoch: 0,
    writeAuthorized: false,
    initialReplay: false,
    initialReplayHasMore: false,
    initialReplayNext: 0,
    crdtReady: false,
    editingScope: null,
    pendingEnvelopes: new Map(),
    persistenceChain: Promise.resolve(),
    typedCommandChain: Promise.resolve(),
    sendQueueActive: false,
    unsyncedCount: 0,
    offlinePolicyBlocked: false,
    recoveryOnly: false,
    recoveryReason: "",
    lastError: "",
    errorHistory: [],
  },
  commentFilter: "open",
  commentPersonFilter: "",
  commentReplyParentId: "",
  versions: [],
  commentBlockId: "",
  commentSelection: null,
  history: { entries: [], index: -1 },
  historyTimer: null,
  historyApplying: false,
  selectedBlockIds: new Set(),
  activeBlockId: "",
  blockDrag: { id: "", overId: "", position: "" },
  menuQuery: "",
  menuSelectedIndex: 0,
  slashSourceIndex: -1,
  aiResult: null,
  mention: { blockIndex: -1, start: -1, end: -1, selectedIndex: 0 },
  backlinks: [],
  inlineSelection: null,
  lastBlockColor: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const EditorCore = window.NotionEditorCore;
const CollaborationEditor = window.NotionCollaborationEditor;
const ResumableFileUpload = window.NotionFileUpload;
const DatabaseBridge = window.NotionDatabaseBridge;
const DATABASE_BLOCK_TYPES = new Set(["table", "board", "calendar", "list", "gallery", "timeline"]);
const NON_ADOPTED_BLOCK_TYPES = new Set(["chart", "dashboard", "form", "feed", "map"]);
const NAVIGATION_SECTION_KEYS = ["favorites", "recents", "teamspaces", "shared", "private"];
const NAVIGATION_SECTION_LABELS = {
  favorites: "즐겨찾기",
  recents: "최근 항목",
  teamspaces: "팀스페이스",
  shared: "공유된 페이지",
  private: "개인 페이지",
};

function applyColorTheme(theme, persist = true) {
  const selected = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = selected;
  if (persist) localStorage.setItem("notion.colorTheme", selected);
}

function toggleColorTheme() {
  applyColorTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  renderWorkspacePopover();
}

function loadColorTheme() {
  const saved = localStorage.getItem("notion.colorTheme");
  const preferred = window.matchMedia?.("(prefers-color-scheme: dark)")?.matches ? "dark" : "light";
  applyColorTheme(saved || preferred, false);
}

if (!EditorCore) {
  throw new Error("Notion editor core failed to load");
}
if (!CollaborationEditor || !window.NotionYjs || !ResumableFileUpload || !DatabaseBridge) {
  throw new Error("Notion collaboration editor runtime failed to load");
}

const EditorHost = EditorCore.createEditorHostAdapter({
  readDocument: readEditorHostDocument,
  applyProjection: applyEditorHostProjection,
  captureSelection: captureEditorHostSelection,
  restoreSelection: restoreEditorHostSelection,
  focusBlock: focusEditorHostBlock,
});
Object.defineProperty(window, "NotionEditorHost", {
  value: EditorHost,
  writable: false,
  configurable: false,
  enumerable: true,
});

const CollaborativeEditorBridge = CollaborationEditor.createEditorBridge({
  Y: window.NotionYjs,
  host: EditorHost,
});
let CollaborativeEditingOutbox = null;
try {
  CollaborativeEditingOutbox = CollaborationEditor.createEncryptedOutbox();
} catch {
  State.liveCollaboration.offlinePolicyBlocked = true;
}
CollaborativeEditorBridge.subscribe(event => {
  if (event.type === "local-update") queueCollaborativeUpdate(event);
});
Object.defineProperty(window, "NotionCollaborativeEditor", {
  value: CollaborativeEditorBridge,
  writable: false,
  configurable: false,
  enumerable: true,
});

function setStatus(text, kind = "") {
  const el = $("#status");
  if (!el) return;
  el.textContent = text;
  el.className = "status " + kind;
}

const PageFocusPolicy = Object.freeze({
  PAGE_CANVAS: "page-canvas",
  PAGE_EDITOR_GROUP: "page-editor-group",
  PAGE_TITLE: "page-title",
  PRESERVE: "preserve",
});

function applyPageFocusPolicy(policy) {
  if (policy === PageFocusPolicy.PRESERVE) return;
  const target = policy === PageFocusPolicy.PAGE_TITLE
    ? $("#pageTitle")
    : policy === PageFocusPolicy.PAGE_EDITOR_GROUP
      ? $("#pageEditorGroup")
      : $("#pageView");
  target?.focus({ preventScroll: true });
  if (policy === PageFocusPolicy.PAGE_TITLE) target?.select?.();
}

function serverRevisionFrom(data) {
  const candidates = [data?.revision, data?.page?.revision];
  for (const candidate of candidates) {
    if (candidate === null || candidate === undefined || candidate === "") continue;
    const revision = Number(candidate);
    if (Number.isSafeInteger(revision) && revision >= 1) return revision;
  }
  return null;
}

function atomicSaveSupported(data) {
  return data?.capabilities?.atomic_save === true;
}

function applySaveMetadata(holder, data) {
  const revision = serverRevisionFrom(data);
  if (revision !== null) holder.serverRevision = revision;
  if (Object.prototype.hasOwnProperty.call(data?.capabilities || {}, "atomic_save")) {
    holder.atomicSaveEnabled = data.capabilities.atomic_save === true;
  }
  holder.saveRetry = null;
  holder.saveConflict = null;
  return revision;
}

function normalizedPageData(data, fallback = null) {
  const source = data || {};
  const previous = fallback || {};
  const revision = serverRevisionFrom(source);
  const page = source.page
    ? { ...(previous.page || {}), ...source.page }
    : previous.page || null;
  if (page && revision !== null) page.revision = revision;
  const databases = source.databases || previous.databases || [];
  const blocks = DatabaseBridge.applyCanonicalDatabases(
    source.blocks || previous.blocks || [],
    databases,
  ).map(EditorCore.normalizeBlock);
  return {
    page,
    blocks,
    databases,
    files: source.files || previous.files || [],
    ...(revision !== null ? { revision } : {}),
  };
}

function normalizedSavedPageDataPreservingBlockBindings(data, current) {
  const normalized = normalizedPageData(data, current);
  const currentBlocks = Array.isArray(current?.blocks) ? current.blocks : [];
  const incomingBlocks = Array.isArray(normalized.blocks) ? normalized.blocks : [];
  const currentById = new Map(
    currentBlocks.filter(block => block?.id).map(block => [block.id, block]),
  );
  const reconciled = incomingBlocks.map((incoming, index) => {
    const target = currentById.get(incoming?.id) || currentBlocks[index];
    if (!target || typeof target !== "object") return EditorCore.deepClone(incoming);
    const next = EditorCore.deepClone(incoming);
    Object.keys(target).forEach(key => delete target[key]);
    Object.assign(target, next);
    return target;
  });
  currentBlocks.splice(0, currentBlocks.length, ...reconciled);
  normalized.blocks = currentBlocks;
  return normalized;
}

function newOperationId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `notion-save-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function atomicSaveComparable(request) {
  const normalizedBlocks = Array.isArray(request?.blocks)
    ? request.blocks.map(block => {
      const normalized = EditorCore.normalizeBlock(block);
      return {
        id: normalized.id,
        type: normalized.type,
        content: normalized.content,
        props: EditorCore.deepClone(normalized.props || {}),
      };
    })
    : [];
  return JSON.stringify({
    base_revision: request?.base_revision,
    page: {
      title: String(request?.page?.title ?? "Untitled"),
      icon: String(request?.page?.icon || ""),
      cover: String(request?.page?.cover || ""),
      settings: EditorCore.deepClone(request?.page?.settings || {}),
    },
    blocks: normalizedBlocks,
  });
}

function atomicSaveRequest(holder, data, title, blocks, baseRevision) {
  if (!Number.isSafeInteger(baseRevision) || baseRevision < 1) {
    throw new Error("서버 문서 버전을 확인할 수 없어 안전하게 저장할 수 없습니다. 페이지를 다시 불러와 주세요.");
  }
  const request = {
    base_revision: baseRevision,
    page: {
      title: String(title ?? "Untitled"),
      icon: String(data?.page?.icon || ""),
      cover: String(data?.page?.cover || ""),
      settings: EditorCore.deepClone(data?.page?.settings || {}),
    },
    blocks: EditorCore.deepClone(blocks || []),
  };
  const fingerprint = JSON.stringify(request);
  const comparable = atomicSaveComparable(request);
  if (
    holder.saveRetry?.comparable === comparable
    && holder.saveRetry?.exactRequest?.operation_id === holder.saveRetry.operationId
  ) {
    return EditorCore.deepClone(holder.saveRetry.exactRequest);
  }
  const operationId = holder.saveRetry?.fingerprint === fingerprint
    ? holder.saveRetry.operationId
    : newOperationId();
  const exactRequest = {
    base_revision: request.base_revision,
    operation_id: operationId,
    page: request.page,
    blocks: request.blocks,
  };
  holder.saveRetry = {
    fingerprint,
    comparable,
    operationId,
    exactRequest: EditorCore.deepClone(exactRequest),
  };
  return exactRequest;
}

const EDITOR_DRAFT_PREFIX = "code-assist.notion.editor-draft.v1";

function editorDraftScope(pageId) {
  const userId = String(State.user?.id || "");
  const workspaceId = String(
    State.navigation.workspaceId || State.workspaceOwnerFilter || "legacy",
  );
  const normalizedPageId = String(pageId || "");
  if (!userId || !workspaceId || !normalizedPageId) return null;
  return { userId, workspaceId, pageId: normalizedPageId };
}

function editorDraftKey(pageId) {
  const scope = editorDraftScope(pageId);
  if (!scope) return "";
  return [
    EDITOR_DRAFT_PREFIX,
    scope.userId,
    scope.workspaceId,
    scope.pageId,
  ].map(encodeURIComponent).join(":");
}

function editorDraftPagePayload(data, title, blocks) {
  return {
    page: {
      title: String(title ?? "Untitled"),
      icon: String(data?.page?.icon || ""),
      cover: String(data?.page?.cover || ""),
      settings: EditorCore.deepClone(data?.page?.settings || {}),
    },
    blocks: EditorCore.deepClone(blocks || []),
  };
}

function persistMainEditorDraft() {
  const storage = globalThis.sessionStorage;
  const data = State.pageData;
  const pageId = data?.page?.id;
  if (data?.page?.collaborative_editing === true) return false;
  const scope = editorDraftScope(pageId);
  const key = editorDraftKey(pageId);
  if (!storage || !scope || !key || !State.dirty) return false;
  const payload = editorDraftPagePayload(
    data,
    $("#pageTitle")?.value ?? data.page?.title,
    data.blocks,
  );
  const draftRequest = {
    base_revision: State.serverRevision,
    page: payload.page,
    blocks: payload.blocks,
  };
  const exactRetry = State.saveRetry?.exactRequest;
  const retryRequest = (
    exactRetry
    && exactRetry.operation_id === State.saveRetry?.operationId
    && atomicSaveComparable(exactRetry) === atomicSaveComparable(draftRequest)
  ) ? EditorCore.deepClone(exactRetry) : null;
  const record = {
    schema_version: 1,
    user_id: scope.userId,
    workspace_id: scope.workspaceId,
    page_id: scope.pageId,
    base_revision: State.serverRevision,
    edit_revision: State.editRevision,
    operation_id: String(retryRequest?.operation_id || ""),
    retry_request: retryRequest,
    page: payload.page,
    blocks: payload.blocks,
  };
  try {
    storage.setItem(key, JSON.stringify(record));
    return true;
  } catch {
    return false;
  }
}

function clearMainEditorDraft(pageId) {
  const storage = globalThis.sessionStorage;
  const key = editorDraftKey(pageId);
  if (!storage || !key) return;
  try { storage.removeItem(key); } catch {}
}

function clearCurrentUserEditorDrafts() {
  const storage = globalThis.sessionStorage;
  const userId = String(State.user?.id || "");
  if (!storage || !userId) return;
  const prefix = `${encodeURIComponent(EDITOR_DRAFT_PREFIX)}:${encodeURIComponent(userId)}:`;
  try {
    const keys = [];
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (key?.startsWith(prefix)) keys.push(key);
    }
    keys.forEach(key => storage.removeItem(key));
  } catch {}
}

function readMainEditorDraft(pageId) {
  const storage = globalThis.sessionStorage;
  const scope = editorDraftScope(pageId);
  const key = editorDraftKey(pageId);
  if (!storage || !scope || !key) return null;
  try {
    const parsed = JSON.parse(storage.getItem(key) || "null");
    if (
      !parsed
      || parsed.schema_version !== 1
      || parsed.user_id !== scope.userId
      || parsed.workspace_id !== scope.workspaceId
      || parsed.page_id !== scope.pageId
      || typeof parsed.page !== "object"
      || Array.isArray(parsed.page)
      || typeof parsed.page.title !== "string"
      || typeof parsed.page.icon !== "string"
      || typeof parsed.page.cover !== "string"
      || typeof parsed.page.settings !== "object"
      || Array.isArray(parsed.page.settings)
      || !Array.isArray(parsed.blocks)
      || parsed.blocks.length > 10_000
      || !Number.isSafeInteger(parsed.base_revision)
      || parsed.base_revision < 1
      || !Number.isSafeInteger(parsed.edit_revision)
      || parsed.edit_revision < 1
      || typeof parsed.operation_id !== "string"
      || parsed.operation_id.length > 200
      || (
        parsed.retry_request !== null
        && parsed.retry_request !== undefined
        && (
          typeof parsed.retry_request !== "object"
          || Array.isArray(parsed.retry_request)
          || parsed.retry_request.base_revision !== parsed.base_revision
          || parsed.retry_request.operation_id !== parsed.operation_id
          || typeof parsed.retry_request.page !== "object"
          || Array.isArray(parsed.retry_request.page)
          || !Array.isArray(parsed.retry_request.blocks)
          || parsed.retry_request.blocks.length > 10_000
        )
      )
    ) return null;
    return parsed;
  } catch {
    return null;
  }
}

function restoreMainEditorDraft(pageId) {
  const record = readMainEditorDraft(pageId);
  if (!record || !State.pageData) return null;
  const serverPayload = editorDraftPagePayload(
    State.pageData,
    State.pageData.page?.title,
    State.pageData.blocks,
  );
  if (JSON.stringify(serverPayload) === JSON.stringify({ page: record.page, blocks: record.blocks })) {
    clearMainEditorDraft(pageId);
    return null;
  }
  State.pageData.page.title = record.page.title;
  State.pageData.page.icon = record.page.icon;
  State.pageData.page.cover = record.page.cover;
  State.pageData.page.settings = EditorCore.deepClone(record.page.settings);
  State.pageData.blocks = record.blocks.map(EditorCore.normalizeBlock);
  State.editRevision = record.edit_revision;
  State.dirty = true;
  State.saveQueued = false;
  const conflicted = record.base_revision !== State.serverRevision;
  State.saveConflict = conflicted
    ? { pageId, currentRevision: State.serverRevision }
    : null;
  if (!conflicted && record.operation_id) {
    const draftRequest = {
      base_revision: record.base_revision,
      page: record.page,
      blocks: record.blocks,
    };
    const retryRequest = record.retry_request;
    if (
      retryRequest
      && atomicSaveComparable(retryRequest) === atomicSaveComparable(draftRequest)
    ) {
      State.saveRetry = {
        fingerprint: JSON.stringify({
          base_revision: retryRequest.base_revision,
          page: retryRequest.page,
          blocks: retryRequest.blocks,
        }),
        comparable: atomicSaveComparable(retryRequest),
        operationId: retryRequest.operation_id,
        exactRequest: EditorCore.deepClone(retryRequest),
      };
    } else {
      State.saveRetry = {
        fingerprint: JSON.stringify(draftRequest),
        comparable: atomicSaveComparable(draftRequest),
        operationId: record.operation_id,
        exactRequest: {
          ...EditorCore.deepClone(draftRequest),
          operation_id: record.operation_id,
        },
      };
    }
  } else {
    State.saveRetry = null;
  }
  return { conflicted };
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(value) {
  return escapeHtml(value).replace(/'/g, "&#39;");
}

function escapeRegExp(value) {
  return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function cssEscape(value) {
  if (window.CSS?.escape) return window.CSS.escape(String(value || ""));
  return String(value || "").replace(/[^a-zA-Z0-9_-]/g, "\\$&");
}

function pageUrl(pageId) {
  return `${location.origin}/page/${encodeURIComponent(pageId || "")}`;
}

function collapsedPagesKey() {
  return `notion.collapsedPages.${State.user?.id || "guest"}`;
}

function loadCollapsedPages() {
  try {
    const raw = localStorage.getItem(collapsedPagesKey()) || "[]";
    State.collapsedPageIds = new Set(JSON.parse(raw));
  } catch {
    State.collapsedPageIds = new Set();
  }
}

function saveCollapsedPages() {
  try {
    localStorage.setItem(collapsedPagesKey(), JSON.stringify([...State.collapsedPageIds]));
  } catch {}
}

function focusPageTreeRow(pageId, rootId = "ownPages") {
  requestAnimationFrame(() => {
    document.querySelector(
      `#${cssEscape(rootId)} .page-row[data-page-id="${cssEscape(pageId)}"]`,
    )?.focus();
  });
}

function togglePageCollapsed(pageId, focusRootId = "") {
  if (State.collapsedPageIds.has(pageId)) State.collapsedPageIds.delete(pageId);
  else State.collapsedPageIds.add(pageId);
  saveCollapsedPages();
  renderPageLists();
  renderBreadcrumbPopover();
  if (focusRootId) focusPageTreeRow(pageId, focusRootId);
}

function favoritePagesKey() {
  return `notion.favoritePages.${State.user?.id || "guest"}`;
}

function loadFavoritePages() {
  try {
    State.favorites = new Set(JSON.parse(localStorage.getItem(favoritePagesKey()) || "[]"));
  } catch {
    State.favorites = new Set();
  }
}

function saveFavoritePages() {
  try {
    localStorage.setItem(favoritePagesKey(), JSON.stringify([...State.favorites]));
  } catch {}
}

function navigationFavoriteKey(sourceOwnerSubjectId, sourcePageId) {
  return `${String(sourceOwnerSubjectId || "")}\u0000${String(sourcePageId || "")}`;
}

function pageSourceLocator(page) {
  return {
    sourceOwnerSubjectId: String(page?.owner_user_id || State.user?.id || ""),
    sourcePageId: String(page?.id || ""),
  };
}

function pageIsFavorite(page) {
  if (!State.navigation.enabled && !State.navigation.authoritative) {
    return State.favorites.has(String(page?.id || ""));
  }
  const locator = pageSourceLocator(page);
  return State.navigation.favoriteKeys.has(
    navigationFavoriteKey(locator.sourceOwnerSubjectId, locator.sourcePageId),
  );
}

function navigationSelectionKey() {
  return `notion.navigationWorkspace.${State.user?.id || "guest"}`;
}

function navigationOperationId(kind) {
  const suffix = typeof globalThis.crypto?.randomUUID === "function"
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `np2b-ui-${kind}-${suffix}`;
}

function navigationSection(sectionKey, sections = State.navigation.sections) {
  return (sections || []).find(item => item.section === sectionKey) || null;
}

function normalizeWorkspaceNavigationBootstrap(payload, preferredWorkspaceId = "") {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("workspace navigation bootstrap is invalid");
  }
  if (payload.capabilities?.navigation_ui !== true) {
    return { enabled: false, authoritative: false, collaborativeSharing: false, collaborativeEditing: false, collaborativeFiles: false, collaborativeHistory: false, collaborativeSearch: false, workspaces: [], workspaceId: "" };
  }
  const collaborativeSharing = payload.capabilities?.collaborative_iam_sharing === true;
  const collaborativeEditing = payload.capabilities?.collaborative_editing === true;
  const collaborativeFiles = payload.capabilities?.collaborative_files === true;
  const collaborativeHistory = payload.capabilities?.collaborative_history === true;
  const collaborativeSearch = payload.capabilities?.collaborative_search === true;
  const workspaces = Array.isArray(payload.items)
    ? payload.items.filter(item => (
        item
        && typeof item === "object"
        && typeof item.id === "string"
        && item.id
        && typeof item.name === "string"
        && item.access
        && typeof item.access === "object"
        && (item.access.role !== "guest" || collaborativeSharing)
      ))
    : [];
  if (new Set(workspaces.map(item => item.id)).size !== workspaces.length) {
    throw new Error("workspace navigation bootstrap identity is duplicated");
  }
  if (!workspaces.length) {
    return { enabled: false, authoritative: true, collaborativeSharing, collaborativeEditing, collaborativeFiles, collaborativeHistory, collaborativeSearch, workspaces: [], workspaceId: "" };
  }
  const preferred = String(preferredWorkspaceId || "");
  const selected = workspaces.find(item => item.id === preferred) || workspaces[0];
  return { enabled: true, authoritative: true, collaborativeSharing, collaborativeEditing, collaborativeFiles, collaborativeHistory, collaborativeSearch, workspaces, workspaceId: selected.id };
}

function normalizeWorkspaceNavigationSnapshot(payload, workspaceId) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("workspace navigation snapshot is invalid");
  }
  if (payload.workspace_id !== workspaceId) {
    throw new Error("workspace navigation snapshot identity is invalid");
  }
  if (payload.subject_id !== State.user?.id) {
    throw new Error("workspace navigation subject is invalid");
  }
  for (const field of ["membership_epoch", "preference_version", "favorite_version"]) {
    if (!Number.isInteger(payload[field]) || payload[field] < 0) {
      throw new Error("workspace navigation snapshot version is invalid");
    }
  }
  if (
    !payload.sidebar
    || typeof payload.sidebar !== "object"
    || !Number.isInteger(payload.sidebar.width_px)
    || payload.sidebar.width_px < 180
    || payload.sidebar.width_px > 600
    || typeof payload.sidebar.collapsed !== "boolean"
  ) {
    throw new Error("workspace navigation sidebar is invalid");
  }
  if (!Array.isArray(payload.sections) || payload.sections.length !== NAVIGATION_SECTION_KEYS.length) {
    throw new Error("workspace navigation sections are invalid");
  }
  const sectionNames = new Set();
  const sectionOrders = new Set();
  const sections = payload.sections.map(item => {
    if (
      !item
      || typeof item !== "object"
      || !NAVIGATION_SECTION_KEYS.includes(item.section)
      || sectionNames.has(item.section)
      || !Number.isInteger(item.display_order)
      || item.display_order < 0
      || item.display_order >= NAVIGATION_SECTION_KEYS.length
      || sectionOrders.has(item.display_order)
      || typeof item.hidden !== "boolean"
      || typeof item.collapsed !== "boolean"
      || !Number.isInteger(item.item_limit)
      || item.item_limit < 1
      || item.item_limit > 100
    ) {
      throw new Error("workspace navigation section is invalid");
    }
    sectionNames.add(item.section);
    sectionOrders.add(item.display_order);
    return {
      section: item.section,
      display_order: item.display_order,
      hidden: item.hidden,
      collapsed: item.collapsed,
      item_limit: item.item_limit,
    };
  });
  if (NAVIGATION_SECTION_KEYS.some(key => !sectionNames.has(key))) {
    throw new Error("workspace navigation section allowlist is incomplete");
  }
  if (!Array.isArray(payload.favorites)) {
    throw new Error("workspace navigation favorites are invalid");
  }
  const favoriteKeys = new Set();
  const favoritePositions = new Map();
  const favorites = payload.favorites.map((item, index) => {
    if (
      !item
      || typeof item !== "object"
      || typeof item.source_owner_subject_id !== "string"
      || !item.source_owner_subject_id
      || typeof item.source_page_id !== "string"
      || !item.source_page_id
      || !Number.isInteger(item.position)
      || item.position !== index
    ) {
      throw new Error("workspace navigation favorite is invalid");
    }
    const key = navigationFavoriteKey(item.source_owner_subject_id, item.source_page_id);
    if (favoriteKeys.has(key)) throw new Error("workspace navigation favorite is duplicated");
    favoriteKeys.add(key);
    favoritePositions.set(key, item.position);
    return {
      source_owner_subject_id: item.source_owner_subject_id,
      source_page_id: item.source_page_id,
      position: item.position,
    };
  });
  return {
    workspace_id: workspaceId,
    subject_id: String(payload.subject_id || ""),
    membership_epoch: payload.membership_epoch,
    preference_version: payload.preference_version,
    favorite_version: payload.favorite_version,
    sidebar: {
      width_px: payload.sidebar.width_px,
      collapsed: payload.sidebar.collapsed,
    },
    sections,
    favorites,
    favoriteKeys,
    favoritePositions,
  };
}

function normalizeWorkspacePagesPayload(payload, workspaceId) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("workspace page snapshot is invalid");
  }
  if (
    payload.workspace_id !== workspaceId
    || !Array.isArray(payload.items)
    || !Number.isInteger(payload.count)
    || payload.count !== payload.items.length
    || typeof payload.degraded !== "boolean"
    || !Array.isArray(payload.degraded_sources)
  ) {
    throw new Error("workspace page snapshot identity is invalid");
  }
  const pageIds = new Set();
  return payload.items.map(item => {
    if (
      !item
      || typeof item !== "object"
      || typeof item.id !== "string"
      || !item.id
      || pageIds.has(item.id)
      || item.workspace_id !== workspaceId
      || typeof item.owner_user_id !== "string"
      || !item.owner_user_id
      || !item.placement
      || typeof item.placement !== "object"
      || !Number.isInteger(item.placement.binding_version)
      || item.placement.binding_version < 1
    ) {
      throw new Error("workspace page snapshot item is invalid");
    }
    pageIds.add(item.id);
    return { ...item, placement: { ...item.placement } };
  });
}

function cloneNavigationSnapshot(snapshot = State.navigation.snapshot) {
  if (!snapshot) return null;
  const payload = {
    workspace_id: snapshot.workspace_id,
    subject_id: snapshot.subject_id,
    membership_epoch: snapshot.membership_epoch,
    preference_version: snapshot.preference_version,
    favorite_version: snapshot.favorite_version,
    sidebar: { ...snapshot.sidebar },
    sections: snapshot.sections.map(item => ({ ...item })),
    favorites: snapshot.favorites.map(item => ({ ...item })),
  };
  return normalizeWorkspaceNavigationSnapshot(payload, snapshot.workspace_id);
}

function setNavigationPending(pending, message = "") {
  const active = Boolean(pending);
  const panel = $("#sidebarPagePanel");
  const status = $("#navigationSyncStatus");
  panel?.setAttribute("aria-busy", active ? "true" : "false");
  document.body.classList.toggle("workspace-navigation-pending", active);
  if (status) {
    status.hidden = !active && !message;
    status.textContent = message || (active ? "탐색 설정 동기화 중" : "");
  }
}

function selectedNavigationWorkspace() {
  return State.navigation.workspaces.find(item => item.id === State.navigation.workspaceId) || null;
}

function applyNavigationWorkspaceResult(workspace) {
  if (!workspace?.id) return;
  const index = State.navigation.workspaces.findIndex(item => item.id === workspace.id);
  if (index >= 0) State.navigation.workspaces[index] = { ...workspace };
}

function navigationPageLifecycleIntent(kind, pageId) {
  const selected = selectedNavigationWorkspace();
  if (!selected || !Number.isInteger(selected.version) || selected.version < 1) {
    throw new Error("활성 워크스페이스 버전을 확인할 수 없습니다.");
  }
  const pending = State.navigation.pendingPageLifecycle;
  const sameIntent = pending
    && pending.kind === kind
    && pending.pageId === pageId
    && pending.workspaceId === State.navigation.workspaceId;
  const intent = sameIntent ? pending : {
    baseVersion: selected.version,
    kind,
    operationId: navigationOperationId(`page-${kind}`),
    pageId,
    workspaceId: State.navigation.workspaceId,
  };
  State.navigation.pendingPageLifecycle = intent;
  return intent;
}

function applyWorkspaceNavigationSnapshot(snapshot) {
  State.navigation.snapshot = snapshot;
  State.navigation.sections = snapshot.sections.map(item => ({ ...item }));
  State.navigation.favoriteKeys = new Set(snapshot.favoriteKeys);
  State.navigation.favoritePositions = new Map(snapshot.favoritePositions);
  setSidebarWidth(snapshot.sidebar.width_px, false);
  const workspace = selectedNavigationWorkspace();
  const directGuest = String(workspace?.access?.role || "") === "guest";
  setSidebarCollapsed(directGuest ? false : snapshot.sidebar.collapsed, false);
  if (workspace) $("#workspaceTitle").textContent = workspace.name;
  renderPageLists();
  applyNavigationSections();
  renderWorkspacePopover();
}

function setWorkspaceNavigationEnabled(enabled) {
  State.navigation.enabled = Boolean(enabled);
  document.body.classList.toggle("workspace-navigation-enabled", State.navigation.enabled);
  $("#navigationSettingsBtn")?.toggleAttribute("hidden", !State.navigation.enabled);
  $(".search-row")?.classList.toggle("navigation-enabled", State.navigation.enabled);
  const resize = $("#sidebarResize");
  resize?.setAttribute("aria-valuemin", State.navigation.enabled ? "180" : "220");
  resize?.setAttribute("aria-valuemax", State.navigation.enabled ? "600" : "480");
  if (!State.navigation.enabled) {
    clearTimeout(State.navigation.preferenceTimer);
    State.navigation.preferenceTimer = null;
    State.navigation.workspaces = [];
    State.navigation.workspaceId = "";
    State.navigation.snapshot = null;
    State.navigation.favoriteKeys = new Set();
    State.navigation.favoritePositions = new Map();
    State.navigation.sections = [];
    State.navigation.visitedWorkspaceIds = new Set();
    State.navigation.collaborativeSharing = false;
    State.navigation.collaborativeEditing = false;
    State.navigation.collaborativeFiles = false;
    State.navigation.collaborativeHistory = false;
    State.navigation.collaborativeSearch = false;
    State.searchRequestSequence += 1;
    State.searchItems = [];
    State.activeSearchPageId = "";
    setNavigationPending(false);
    const label = State.user?.display_name || State.user?.id || "admin";
    $("#workspaceTitle").textContent = `${label}의 Notion`;
    closeWorkspacePopover();
    renderWorkspacePopover();
  }
}

async function loadWorkspaceNavigationSnapshot(workspaceId = State.navigation.workspaceId) {
  const requestedWorkspaceId = String(workspaceId || "");
  if (!State.navigation.enabled || !requestedWorkspaceId) return false;
  const sequence = ++State.navigation.sequence;
  const payload = await api(
    `/api/notion/workspaces/${encodeURIComponent(requestedWorkspaceId)}/navigation`,
  );
  if (
    sequence !== State.navigation.sequence
    || requestedWorkspaceId !== State.navigation.workspaceId
  ) return false;
  const snapshot = normalizeWorkspaceNavigationSnapshot(payload, requestedWorkspaceId);
  applyWorkspaceNavigationSnapshot(snapshot);
  return true;
}

async function fetchWorkspacePagesSnapshot(
  workspaceId,
  query = "",
  expectedSequence = State.navigation.sequence,
) {
  const requestedWorkspaceId = String(workspaceId || "");
  if (!State.navigation.enabled || !requestedWorkspaceId) {
    throw new Error("workspace page scope is unavailable");
  }
  const payload = await api(
    `/api/notion/workspaces/${encodeURIComponent(requestedWorkspaceId)}/pages?q=${encodeURIComponent(query)}`,
  );
  if (
    expectedSequence !== State.navigation.sequence
    || requestedWorkspaceId !== State.navigation.workspaceId
  ) return null;
  return normalizeWorkspacePagesPayload(payload, requestedWorkspaceId);
}

function applyWorkspacePagesSnapshot(items) {
  State.pages = items;
  State.workspaceOwnerFilter = "";
  if (State.activePageId && !State.pages.some(page => page.id === State.activePageId)) {
    State.activePageId = "";
    State.pageData = null;
    State.serverRevision = null;
    State.atomicSaveEnabled = false;
    State.saveRetry = null;
    State.saveConflict = null;
    State.dirty = false;
  }
  renderPageLists();
  renderWorkspacePopover();
  renderBreadcrumbPopover();
  renderSearchCreators();
  if (!State.activePageId) renderHome();
}

async function loadWorkspacePagesSnapshot(workspaceId = State.navigation.workspaceId) {
  const expectedSequence = State.navigation.sequence;
  const items = await fetchWorkspacePagesSnapshot(
    workspaceId,
    $("#pageSearch")?.value || "",
    expectedSequence,
  );
  if (!items) return false;
  applyWorkspacePagesSnapshot(items);
  return true;
}

async function initializeWorkspaceNavigation() {
  let payload;
  try {
    payload = await api("/api/notion/workspaces");
  } catch {
    State.navigation.authoritative = false;
    setWorkspaceNavigationEnabled(false);
    return false;
  }
  let preferred = "";
  try { preferred = localStorage.getItem(navigationSelectionKey()) || ""; } catch {}
  let bootstrap;
  try {
    bootstrap = normalizeWorkspaceNavigationBootstrap(payload, preferred);
  } catch {
    State.navigation.authoritative = payload.capabilities?.navigation_ui === true;
    setWorkspaceNavigationEnabled(false);
    renderPageLists();
    return false;
  }
  State.navigation.authoritative = bootstrap.authoritative;
  if (!bootstrap.enabled) {
    setWorkspaceNavigationEnabled(false);
    renderPageLists();
    return false;
  }
  State.navigation.workspaces = bootstrap.workspaces;
  State.navigation.workspaceId = bootstrap.workspaceId;
  State.navigation.collaborativeSharing = bootstrap.collaborativeSharing;
  State.navigation.collaborativeEditing = bootstrap.collaborativeEditing;
  State.navigation.collaborativeFiles = bootstrap.collaborativeFiles;
  State.navigation.collaborativeHistory = bootstrap.collaborativeHistory;
  State.navigation.collaborativeSearch = bootstrap.collaborativeSearch;
  State.navigation.visitedWorkspaceIds = new Set([bootstrap.workspaceId]);
  setWorkspaceNavigationEnabled(true);
  try {
    await loadWorkspaceNavigationSnapshot(bootstrap.workspaceId);
    if (!(await loadWorkspacePagesSnapshot(bootstrap.workspaceId))) {
      throw new Error("workspace page snapshot was superseded");
    }
  } catch {
    // The workspace endpoint declared itself authoritative.  Never fall back
    // to the legacy all-owner page list after that boundary is established.
    State.pages = [];
    State.activePageId = "";
    State.pageData = null;
    renderPageLists();
    renderEditor();
    setStatus("워크스페이스 페이지를 안전하게 불러오지 못했습니다.", "error");
    return false;
  }
  try { localStorage.setItem(navigationSelectionKey(), bootstrap.workspaceId); } catch {}
  return true;
}

async function refreshWorkspaceNavigationCapability() {
  if (!State.navigation.enabled || !State.navigation.workspaceId) return false;
  let payload;
  try {
    payload = await api("/api/notion/workspaces");
  } catch (error) {
    State.navigation.authoritative = true;
    setWorkspaceNavigationEnabled(false);
    State.favorites = new Set();
    renderPageLists();
    setStatus("탐색 기능 상태 확인 실패: " + error.message, "error");
    return false;
  }
  let bootstrap;
  try {
    bootstrap = normalizeWorkspaceNavigationBootstrap(payload, State.navigation.workspaceId);
  } catch {
    State.navigation.authoritative = payload.capabilities?.navigation_ui === true;
    setWorkspaceNavigationEnabled(false);
    renderPageLists();
    return false;
  }
  const currentStillAllowed = bootstrap.workspaces.some(
    item => item.id === State.navigation.workspaceId,
  );
  if (!bootstrap.enabled || !currentStillAllowed) {
    State.navigation.authoritative = bootstrap.authoritative;
    setWorkspaceNavigationEnabled(false);
    State.favorites = new Set();
    renderPageLists();
    setStatus("워크스페이스 탐색 기능이 비활성화되었습니다.", "error");
    return false;
  }
  State.navigation.authoritative = true;
  State.navigation.workspaces = bootstrap.workspaces;
  State.navigation.collaborativeSharing = bootstrap.collaborativeSharing;
  State.navigation.collaborativeEditing = bootstrap.collaborativeEditing;
  State.navigation.collaborativeFiles = bootstrap.collaborativeFiles;
  State.navigation.collaborativeHistory = bootstrap.collaborativeHistory;
  State.navigation.collaborativeSearch = bootstrap.collaborativeSearch;
  return true;
}

function navigationControlsLocked() {
  return Boolean(
    State.navigation.enabled
    && (State.navigation.mutationReserved || State.navigation.mutationInFlight),
  );
}

function navigationPreferencePayload() {
  const snapshot = State.navigation.snapshot;
  if (!snapshot) throw new Error("workspace navigation snapshot is unavailable");
  const width = Math.round(
    Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width"),
    ) || snapshot.sidebar.width_px,
  );
  return {
    expected_preference_version: snapshot.preference_version,
    sidebar_width_px: width,
    sidebar_collapsed: document.body.classList.contains("sidebar-collapsed"),
    sections: State.navigation.sections.map(item => ({ ...item })),
    operation_id: navigationOperationId("preferences"),
  };
}

async function requestNavigationMutation(path, options) {
  try {
    return await api(path, options);
  } catch (error) {
    if (error?.status) throw error;
    return api(path, options);
  }
}

async function runNavigationMutation({ optimistic, request, successMessage }) {
  if (!State.navigation.enabled || !State.navigation.snapshot) return false;
  if (navigationControlsLocked()) {
    setStatus("탐색 설정을 동기화하는 중입니다.", "error");
    return false;
  }
  State.navigation.mutationReserved = true;
  setNavigationPending(true, "탐색 기능 상태 확인 중");
  try {
    if (!(await refreshWorkspaceNavigationCapability())) return false;
    const workspaceId = State.navigation.workspaceId;
    const sequence = State.navigation.sequence;
    const previous = cloneNavigationSnapshot();
    if (typeof optimistic === "function") optimistic();
    renderPageLists();
    applyNavigationSections();
    setNavigationPending(true, "탐색 설정 동기화 중");
    State.navigation.mutationInFlight = (async () => {
      try {
        await request();
        if (
          workspaceId !== State.navigation.workspaceId
          || sequence !== State.navigation.sequence
        ) return false;
        await loadWorkspaceNavigationSnapshot(workspaceId);
        setStatus(successMessage, "ok");
        return true;
      } catch (error) {
        let reloaded = false;
        let reloadError = null;
        if (workspaceId === State.navigation.workspaceId) {
          try {
            reloaded = await loadWorkspaceNavigationSnapshot(workspaceId);
          } catch (currentError) {
            reloadError = currentError;
          }
        }
        if (!reloaded && [403, 404].includes(Number(reloadError?.status))) {
          State.navigation.authoritative = true;
          setWorkspaceNavigationEnabled(false);
          State.favorites = new Set();
          renderPageLists();
          reloaded = true;
        }
        if (!reloaded && previous && workspaceId === State.navigation.workspaceId) {
          applyWorkspaceNavigationSnapshot(previous);
        }
        setStatus("탐색 설정 동기화 실패: " + error.message, "error");
        return false;
      } finally {
        State.navigation.mutationInFlight = null;
      }
    })();
    return await State.navigation.mutationInFlight;
  } finally {
    State.navigation.mutationReserved = false;
    if (!State.navigation.mutationInFlight) {
      setNavigationPending(false);
    }
  }
}

function scheduleNavigationPreferenceSave() {
  if (!State.navigation.enabled || !State.navigation.snapshot) return;
  clearTimeout(State.navigation.preferenceTimer);
  State.navigation.preferenceTimer = setTimeout(() => {
    State.navigation.preferenceTimer = null;
    persistNavigationPreferences();
  }, 250);
}

async function persistNavigationPreferences() {
  if (!State.navigation.enabled || !State.navigation.snapshot) return false;
  if (navigationControlsLocked()) {
    scheduleNavigationPreferenceSave();
    return false;
  }
  const workspaceId = State.navigation.workspaceId;
  const payload = navigationPreferencePayload();
  return runNavigationMutation({
    optimistic: () => {
      State.navigation.snapshot.sidebar = {
        width_px: payload.sidebar_width_px,
        collapsed: payload.sidebar_collapsed,
      };
      State.navigation.snapshot.sections = payload.sections.map(item => ({ ...item }));
    },
    request: () => requestNavigationMutation(
      `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/navigation/preferences`,
      { method: "PATCH", body: payload },
    ),
    successMessage: "사이드바 설정을 저장했습니다.",
  });
}

async function flushPendingNavigationPreferences() {
  if (!State.navigation.enabled) return true;
  if (State.navigation.preferenceTimer) {
    clearTimeout(State.navigation.preferenceTimer);
    State.navigation.preferenceTimer = null;
    return persistNavigationPreferences();
  }
  if (State.navigation.mutationInFlight) {
    return Boolean(await State.navigation.mutationInFlight);
  }
  return true;
}

function setSidebarWidth(value, persist = true) {
  const minimum = State.navigation.enabled ? 180 : 220;
  const maximum = State.navigation.enabled ? 600 : 480;
  const width = Math.max(minimum, Math.min(maximum, Number(value) || 270));
  document.documentElement.style.setProperty("--sidebar-width", `${width}px`);
  $("#sidebarResize")?.setAttribute("aria-valuenow", String(Math.round(width)));
  if (persist && State.navigation.enabled) {
    scheduleNavigationPreferenceSave();
  } else if (persist) {
    try { localStorage.setItem("notion.sidebarWidth", String(width)); } catch {}
  }
  return width;
}

function loadSidebarWidth() {
  let width = 270;
  try { width = Number(localStorage.getItem("notion.sidebarWidth") || 270); } catch {}
  setSidebarWidth(width, false);
}

function setSidebarCollapsed(collapsed, persist = true) {
  document.body.classList.toggle("sidebar-collapsed", Boolean(collapsed));
  if (persist && State.navigation.enabled) {
    scheduleNavigationPreferenceSave();
  } else if (persist) {
    try { localStorage.setItem("notion.sidebarCollapsed", collapsed ? "1" : "0"); } catch {}
  }
}

function loadSidebarCollapsed() {
  try { setSidebarCollapsed(localStorage.getItem("notion.sidebarCollapsed") === "1", false); } catch {}
}

function bindSidebarResize() {
  const handle = $("#sidebarResize");
  if (!handle) return;
  let pointerId = null;
  handle.addEventListener("pointerdown", event => {
    if (navigationControlsLocked()) return;
    pointerId = event.pointerId;
    handle.setPointerCapture(pointerId);
    handle.classList.add("dragging");
    setSidebarCollapsed(false);
    event.preventDefault();
  });
  handle.addEventListener("pointermove", event => {
    if (pointerId !== event.pointerId || !handle.hasPointerCapture(pointerId)) return;
    setSidebarWidth(event.clientX);
  });
  const finish = event => {
    if (pointerId !== event.pointerId) return;
    if (handle.hasPointerCapture(pointerId)) handle.releasePointerCapture(pointerId);
    pointerId = null;
    handle.classList.remove("dragging");
  };
  handle.addEventListener("pointerup", finish);
  handle.addEventListener("pointercancel", finish);
  handle.addEventListener("dblclick", () => {
    if (!navigationControlsLocked()) setSidebarWidth(270);
  });
  handle.addEventListener("keydown", event => {
    if (!["ArrowLeft", "ArrowRight", "Home"].includes(event.key)) return;
    if (navigationControlsLocked()) return;
    event.preventDefault();
    const current = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width")) || 270;
    setSidebarWidth(event.key === "Home" ? 270 : current + (event.key === "ArrowLeft" ? -10 : 10));
  });
}

function normalizeUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (/^(https?:|mailto:|tel:|\/)/i.test(text)) return text;
  return `https://${text}`;
}

function normalizeSafeRichTextUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const schemeProbe = text.slice(0, 256).replace(/[\u0000-\u0020]+/g, "");
  const scheme = schemeProbe.match(/^([a-z][a-z0-9+.-]*):/i)?.[1]?.toLowerCase() || "";
  if (scheme && !["http", "https", "mailto", "tel"].includes(scheme)) return "";
  const normalized = normalizeUrl(text);
  try {
    const parsed = new URL(normalized, location.origin);
    if (!["http:", "https:", "mailto:", "tel:"].includes(parsed.protocol)) return "";
  } catch {
    return "";
  }
  return normalized;
}

async function copyText(text, okMessage = "복사됨") {
  try {
    await navigator.clipboard.writeText(text);
    setStatus(okMessage, "ok");
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    setStatus(okMessage, "ok");
  }
}

function highlightedText(text, terms = []) {
  const cleanTerms = terms.filter(Boolean).map(escapeRegExp);
  const safe = escapeHtml(text || "");
  if (!cleanTerms.length) return safe;
  const re = new RegExp(`(${cleanTerms.join("|")})`, "gi");
  return safe.replace(re, "<mark>$1</mark>");
}

function iconSvg(name, extraClass = "") {
  const paths = {
    home: "<path d='M4.5 11.2 12 4.8l7.5 6.4v8h-5v-5h-5v5h-5z' fill='none' stroke='currentColor' stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round'/>",
    chat: "<path d='M5 5.5h14v10H9l-4 3z' fill='none' stroke='currentColor' stroke-width='1.7' stroke-linejoin='round'/>",
    doc: "<path d='M7 3.5h7l3 3V20.5H7z' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linejoin='round'/><path d='M14 3.5v3h3M9.5 10h5M9.5 13h5M9.5 16h3.5' fill='none' stroke='currentColor' stroke-width='1.4' stroke-linecap='round'/>",
    list: "<circle cx='5' cy='6' r='1.25' fill='currentColor'/><circle cx='5' cy='12' r='1.25' fill='currentColor'/><circle cx='5' cy='18' r='1.25' fill='currentColor'/><path d='M9 6h10M9 12h10M9 18h10' fill='none' stroke='currentColor' stroke-width='2.2' stroke-linecap='square'/>",
    page: "<path d='M6.5 4h8l3 3v13H6.5z' fill='none' stroke='currentColor' stroke-width='1.6' stroke-linejoin='round'/><path d='M14.5 4v3h3' fill='none' stroke='currentColor' stroke-width='1.6'/>",
    plus: "<path d='M12 5v14M5 12h14' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round'/>",
    drag: "<circle cx='9' cy='7' r='1.2' fill='currentColor'/><circle cx='15' cy='7' r='1.2' fill='currentColor'/><circle cx='9' cy='12' r='1.2' fill='currentColor'/><circle cx='15' cy='12' r='1.2' fill='currentColor'/><circle cx='9' cy='17' r='1.2' fill='currentColor'/><circle cx='15' cy='17' r='1.2' fill='currentColor'/>",
    table: "<rect x='4' y='5' width='16' height='14' rx='1.5' fill='none' stroke='currentColor' stroke-width='1.6'/><path d='M4 10h16M4 14.5h16M10 5v14M15 5v14' stroke='currentColor' stroke-width='1.3'/>",
    calendar: "<rect x='4' y='5' width='16' height='15' rx='2' fill='none' stroke='currentColor' stroke-width='1.6'/><path d='M8 3.5v3M16 3.5v3M4 9h16' fill='none' stroke='currentColor' stroke-width='1.5' stroke-linecap='round'/>",
    link: "<path d='M9.5 7.5 8 7.5a4.5 4.5 0 0 0 0 9h3M14.5 16.5H16a4.5 4.5 0 0 0 0-9h-3M9 12h6' fill='none' stroke='currentColor' stroke-width='1.7' stroke-linecap='round'/>",
    right: "<path d='m10 7 5 5-5 5' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>",
    down: "<path d='m7 10 5 5 5-5' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>",
    trash: "<path d='M5 7h14M9 7V5h6v2M8 10v8M12 10v8M16 10v8M7 7l1 14h8l1-14' fill='none' stroke='currentColor' stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'/>",
  };
  return `<svg class="svg-icon ${extraClass}" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.doc}</svg>`;
}

const PAGE_ICON_ASSET_PATHS = Object.freeze({
  "👋": "/notion_static/assets/emoji/1f44b.svg",
  "🏠": "/notion_static/assets/emoji/1f3e0.svg",
});

function pageCustomIconContentHtml(icon) {
  const normalizedIcon = String(icon || "").trim();
  if (normalizedIcon === "list") {
    return iconSvg("list", "notion-list-icon");
  }
  const assetPath = PAGE_ICON_ASSET_PATHS[normalizedIcon] || "";
  if (assetPath) {
    return `<img class="notion-emoji-image" src="${escapeAttr(assetPath)}" alt="" draggable="false" aria-hidden="true">`;
  }
  return escapeHtml(normalizedIcon);
}

function pageIconHtml(pageOrIcon, extraClass = "") {
  const icon = typeof pageOrIcon === "string" ? pageOrIcon : (pageOrIcon?.icon || "");
  if (icon && icon.trim()) {
    return `<span class="page-custom-icon ${escapeAttr(extraClass)}">${pageCustomIconContentHtml(icon)}</span>`;
  }
  return iconSvg("doc", extraClass);
}

function formatTime(ts) {
  if (!ts) return "-";
  try {
    return new Date(Number(ts) * 1000).toLocaleString();
  } catch {
    return "-";
  }
}

function formatRelative(ts) {
  const n = Number(ts || 0);
  if (!n) return "-";
  const diff = Math.max(0, Date.now() - n * 1000);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "방금";
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}일 전`;
  return new Date(n * 1000).toLocaleDateString();
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + "MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + "KB";
  return n + "B";
}

function notionFileUrl(fileId) {
  const id = String(fileId || "");
  if (!id) return "";
  const prefix = id.startsWith("file_")
    ? "/api/notion/collaboration/files/"
    : "/api/notion/files/";
  return `${prefix}${encodeURIComponent(id)}`;
}

function collaborativeFileBasePath(pageId = State.activePageId) {
  if (!State.navigation.collaborativeFiles || !State.navigation.workspaceId || !pageId) return "";
  return `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/pages/${encodeURIComponent(pageId)}/files`;
}

function collaborativeSearchWorkspacePath() {
  if (!State.navigation.collaborativeSearch || !State.navigation.workspaceId) return "";
  return `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}`;
}

async function canonicalWorkspaceEpoch() {
  const base = collaborativeSearchWorkspacePath();
  if (!base) throw new Error("워크스페이스 검색 권한 범위를 확인할 수 없습니다.");
  const result = await api(`${base}/search?limit=1`);
  const epoch = Number(result.workspace_epoch || 0);
  if (!Number.isSafeInteger(epoch) || epoch < 1) {
    throw new Error("워크스페이스 권한 버전을 확인할 수 없습니다.");
  }
  return epoch;
}

async function authenticatedFetch(path, opts = {}) {
  const init = { ...opts, headers: { ...(opts.headers || {}) } };
  const method = String(init.method || "GET").toUpperCase();
  const target = String(path || "");
  const resolved = new URL(target, location.href || (location.origin + "/"));
  const sameOrigin = ["http:", "https:"].includes(resolved.protocol)
    && resolved.origin === location.origin;
  if (sameOrigin && !["GET", "HEAD", "OPTIONS"].includes(method) && State.csrfToken) {
    init.headers["X-CSRF-Token"] = State.csrfToken;
  }
  return fetch(path, init);
}

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: opts.headers || {} };
  if (opts.body !== undefined) {
    init.headers = { "Content-Type": "application/json", ...init.headers };
    init.body = JSON.stringify(opts.body);
  }
  const res = await authenticatedFetch(path, init);
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    let responseData = null;
    try {
      responseData = await res.json();
      message = responseData.error || responseData.message || message;
    } catch {}
    const error = new Error(message);
    error.status = res.status;
    error.data = responseData;
    throw error;
  }
  return res.json();
}

async function loadAuth() {
  const data = await api("/api/code/auth/me");
  if (!data.authenticated) {
    window.location.href = "/login";
    return false;
  }
  State.user = data.user || null;
  State.csrfToken = String(data.csrf_token || "");
  loadCollapsedPages();
  loadFavoritePages();
  const label = State.user?.display_name || State.user?.id || "admin";
  $("#workspaceTitle").textContent = `${label}의 Notion`;
  $("#globalSearchInput").placeholder = `${label}의 Notion에서 검색 또는 질문`;
  return true;
}

async function loadOverview() {
  try {
    State.overview = await api("/api/notion/overview");
  } catch {
    State.overview = null;
  }
}

async function loadOutlookStatus(probe = false) {
  try {
    State.outlook = await api(`/api/notion/outlook/status${probe ? "?probe=1" : ""}`);
  } catch (error) {
    State.outlook = {
      enabled: false,
      connected: false,
      settings: {},
      connector_status: {},
      last_error: error.message,
    };
  }
  if (!State.activePageId && State.pages.length) renderHome();
  return State.outlook;
}

async function loadPages() {
  const q = $("#pageSearch")?.value || "";
  if (State.navigation.authoritative) {
    if (State.navigation.enabled && State.navigation.workspaceId) {
      return loadWorkspacePagesSnapshot(State.navigation.workspaceId);
    }
    applyWorkspacePagesSnapshot([]);
    return false;
  }
  const data = await api(`/api/notion/pages?q=${encodeURIComponent(q)}`);
  State.pages = data.items || [];
  if (State.workspaceOwnerFilter && !sharedOwnerIds().has(State.workspaceOwnerFilter)) {
    State.workspaceOwnerFilter = "";
  }
  renderPageLists();
  renderWorkspacePopover();
  renderBreadcrumbPopover();
  renderSearchCreators();
  if (!State.activePageId) renderHome();
}

function ownPages() {
  if (State.workspaceOwnerFilter) return [];
  return State.pages.filter(p => p.scope !== "shared");
}

function sharedPages() {
  return State.pages.filter(p => {
    if (p.scope !== "shared") return false;
    return !State.workspaceOwnerFilter || p.owner_user_id === State.workspaceOwnerFilter;
  });
}

function visiblePages() {
  return State.workspaceOwnerFilter ? sharedPages() : State.pages;
}

function sharedOwnerIds() {
  return new Set(
    State.pages
      .filter(p => p.scope === "shared" && p.owner_user_id)
      .map(p => p.owner_user_id),
  );
}

function guestOwners() {
  const me = State.user?.id || "";
  return Array.from(sharedOwnerIds())
    .filter(owner => owner && owner !== me && sharedPageCountForOwner(owner) > 0)
    .sort();
}

function sharedPageCountForOwner(owner) {
  return State.pages.filter(p => p.scope === "shared" && p.owner_user_id === owner).length;
}

function renderCollaborativeTeamspaceSidebar(workspace) {
  const root = $("#teamspacePages");
  if (!root) return;
  root.innerHTML = `
    <button class="teamspace-hq-row" type="button" aria-label="${escapeAttr(workspace.name)} HQ 홈">
      <span aria-hidden="true">⌄</span><span aria-hidden="true">◆</span><strong>${escapeHtml(workspace.name)} HQ</strong><span aria-hidden="true">＋</span><span aria-hidden="true">…</span>
    </button>
    <button class="teamspace-sidebar-home ${State.activePageId ? "" : "active"}" type="button">
      <span aria-hidden="true">🏠</span><span>팀스페이스 홈</span>
    </button>
    <div class="teamspace-sidebar-pages"></div>
    <button class="teamspace-sidebar-add" type="button">＋&nbsp; 새로 추가</button>
  `;
  root.querySelector(".teamspace-hq-row")?.addEventListener("click", () => goHome(true));
  root.querySelector(".teamspace-sidebar-home")?.addEventListener("click", () => goHome(true));
  root.querySelector(".teamspace-sidebar-add")?.addEventListener("click", () => createPage());
  const pageRoot = root.querySelector(".teamspace-sidebar-pages");
  [...State.pages]
    .sort((left, right) => (left.position || 0) - (right.position || 0))
    .forEach(page => appendPageRow(pageRoot, page, 0, false, false, false));
}

function syncCollaborativeSidebarPresentation() {
  const workspace = selectedNavigationWorkspace();
  const active = Boolean(
    State.navigation.enabled
    && State.navigation.collaborativeSharing
    && workspace,
  );
  const guest = active && String(workspace?.access?.role || "") === "guest";
  const members = Array.isArray(State.workspaceAccess?.members)
    ? State.workspaceAccess.members
    : [];
  const hasOtherWorkspaceMember = members.some(item => item.subject_id !== State.user?.id);
  const directShares = Array.isArray(State.shareAccess?.items) ? State.shareAccess.items : [];
  const inheritedWorkspaceShareOpen = Boolean(document.querySelector(
    '#inspector[data-page-inherited-share="true"]:not([data-page-direct-guest-share="true"]):not([hidden])',
  ));
  const directGuestOwner = active && !guest && !inheritedWorkspaceShareOpen
    && !hasOtherWorkspaceMember && directShares.some(item => {
      const principalType = item.principal_type || "subject";
      const principalId = item.principal_id || item.shared_user_id;
      return principalType === "subject" && principalId !== State.user?.id;
    });
  document.body.classList.toggle("collaborative-workspace-ui", active);
  document.body.classList.toggle("guest-direct-workspace-ui", guest);
  $("#collaborativeSidebarApps")?.toggleAttribute("hidden", !active || guest);
  $("#collaborativeSidebarFooter")?.toggleAttribute("hidden", !active || guest);
  $("#guestDirectSidebarLinks")?.toggleAttribute("hidden", !guest);
  $("#guestDirectSidebarFooter")?.toggleAttribute("hidden", !guest);
  $("#sidebarChatNavBtn")?.toggleAttribute("hidden", !active || guest);
  $("#sidebarNewBtn")?.toggleAttribute("hidden", guest);
  if (!active) {
    const cube = document.querySelector(".notion-cube");
    if (cube) cube.textContent = "N";
    $("#homeNavBtn")?.querySelector("span:last-child")?.replaceChildren("홈");
    return;
  }
  const cube = document.querySelector(".notion-cube");
  if (cube) cube.textContent = String(workspace.icon || workspace.name?.[0] || "R").slice(0, 1);
  const sections = $("#navigationSections");
  const privateSection = $("#privateSection");
  const teamspacesSection = $("#teamspacesSection");
  if (guest) {
    const sharedSection = $("#sharedSection");
    $("#favoritesSection").hidden = true;
    $("#recentSection").hidden = true;
    privateSection.hidden = true;
    teamspacesSection.hidden = true;
    sharedSection.hidden = false;
    sharedSection.classList.remove("navigation-section-collapsed");
    sharedSection.querySelector(".section-label span").textContent = "공유된 페이지";
    $("#sharedPages").hidden = false;
    sections.append(sharedSection);
    $("#homeNavBtn")?.querySelector("span:last-child")?.replaceChildren("홈");
    return;
  }
  $("#sharedSection")?.querySelector(".section-label span")?.replaceChildren("공유된 페이지");
  $("#homeNavBtn")?.querySelector("span:last-child")?.replaceChildren("홈");
  $("#favoritesSection").hidden = true;
  $("#recentSection").hidden = true;
  $("#sharedSection").hidden = true;
  $("#favoritePages").innerHTML = "";
  $("#recentPages").innerHTML = "";
  $("#sharedPages").innerHTML = "";
  privateSection.hidden = false;
  teamspacesSection.hidden = false;
  privateSection.classList.remove("navigation-section-collapsed");
  teamspacesSection.classList.remove("navigation-section-collapsed");
  $("#ownPages").hidden = false;
  $("#teamspacePages").hidden = false;
  $("#ownPages").innerHTML = '<button class="collaborative-sidebar-new-page" type="button">＋&nbsp; 새 페이지 추가</button>';
  $("#ownPages").querySelector("button")?.addEventListener("click", () => createPage());
  renderCollaborativeTeamspaceSidebar(workspace);
  if (directGuestOwner) {
    const teamspaceRoot = $("#teamspacePages");
    const sharedSection = $("#sharedSection");
    const sharedRoot = $("#sharedPages");
    teamspaceRoot?.querySelector(".teamspace-sidebar-home")?.toggleAttribute("hidden", true);
    teamspaceRoot?.querySelector(".teamspace-sidebar-pages")?.replaceChildren();
    sharedSection.hidden = false;
    sharedSection.classList.remove("navigation-section-collapsed");
    sharedSection.querySelector(".section-label span").textContent = "공유된 페이지";
    sharedRoot.hidden = false;
    renderPageGroup(
      sharedRoot,
      State.pages.filter(page => page.id === State.activePageId),
      { tree: true, treeLabel: "공유된 페이지" },
    );
    sections.append(privateSection, teamspacesSection, sharedSection);
  } else {
    sections.append(privateSection, teamspacesSection);
  }
}

function renderPageLists() {
  const favoriteLimit = State.navigation.enabled
    ? (navigationSection("favorites")?.item_limit || 10)
    : Number.MAX_SAFE_INTEGER;
  const favorites = visiblePages()
    .filter(page => pageIsFavorite(page))
    .sort((left, right) => {
      if (!State.navigation.enabled) return 0;
      const leftLocator = pageSourceLocator(left);
      const rightLocator = pageSourceLocator(right);
      return (
        (State.navigation.favoritePositions.get(navigationFavoriteKey(
          leftLocator.sourceOwnerSubjectId,
          leftLocator.sourcePageId,
        )) ?? Number.MAX_SAFE_INTEGER)
        - (State.navigation.favoritePositions.get(navigationFavoriteKey(
          rightLocator.sourceOwnerSubjectId,
          rightLocator.sourcePageId,
        )) ?? Number.MAX_SAFE_INTEGER)
      );
    })
    .slice(0, favoriteLimit);
  $("#favoritesSection").hidden = !favorites.length;
  renderPageGroup($("#favoritePages"), favorites, { flat: true });
  const recentLimit = State.navigation.enabled
    ? (navigationSection("recents")?.item_limit || 10)
    : 5;
  const recent = [...visiblePages()]
    .sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0))
    .slice(0, recentLimit);
  renderPageGroup($("#recentPages"), recent, { flat: true });
  const privateLimit = State.navigation.enabled
    ? (navigationSection("private")?.item_limit || 10)
    : Number.MAX_SAFE_INTEGER;
  const sharedLimit = State.navigation.enabled
    ? (navigationSection("shared")?.item_limit || 10)
    : Number.MAX_SAFE_INTEGER;
  renderPageGroup($("#ownPages"), ownPages().slice(0, privateLimit), {
    tree: true,
    treeLabel: "개인 페이지",
  });
  renderPageGroup($("#sharedPages"), sharedPages().slice(0, sharedLimit), {
    tree: true,
    treeLabel: "공유된 페이지",
  });
  bindPageListRootDrop($("#ownPages"));
  if (State.navigation.enabled) applyNavigationSections();
  syncCollaborativeSidebarPresentation();
}

function applyNavigationSections() {
  const root = $("#navigationSections");
  if (!root || !State.navigation.enabled || !State.navigation.sections.length) return;
  const focusKey = State.navigation.focusRestore;
  const ordered = [...State.navigation.sections].sort(
    (left, right) => left.display_order - right.display_order,
  );
  ordered.forEach(item => {
    const section = root.querySelector(`[data-navigation-section="${item.section}"]`);
    if (!section) return;
    root.appendChild(section);
    const hasFavoriteRows = item.section !== "favorites" || Boolean($("#favoritePages")?.children.length);
    const hasTeamspaceRows = item.section !== "teamspaces" || Boolean($("#teamspacePages")?.children.length);
    section.hidden = item.hidden || !hasFavoriteRows || !hasTeamspaceRows;
    section.classList.toggle("navigation-section-collapsed", item.collapsed);
    const list = section.querySelector(".page-list");
    if (list) list.hidden = item.collapsed;
    const toggle = section.querySelector("[data-navigation-toggle]");
    if (toggle) {
      toggle.setAttribute("aria-expanded", item.collapsed ? "false" : "true");
      toggle.setAttribute(
        "aria-label",
        `${NAVIGATION_SECTION_LABELS[item.section]} ${item.collapsed ? "펼치기" : "접기"}`,
      );
    }
  });
  if (focusKey) {
    State.navigation.focusRestore = null;
    requestAnimationFrame(() => {
      root.querySelector(`[data-navigation-focus="${focusKey}"]`)?.focus();
    });
  }
  syncCollaborativeSidebarPresentation();
}

function toggleNavigationSection(sectionKey, focusKey = "") {
  if (!State.navigation.enabled || navigationControlsLocked()) return;
  const item = navigationSection(sectionKey);
  if (!item) return;
  item.collapsed = !item.collapsed;
  State.navigation.focusRestore = focusKey || `toggle-${sectionKey}`;
  applyNavigationSections();
  scheduleNavigationPreferenceSave();
}

function renderWorkspacePopover() {
  const root = $("#workspaceHoverPages");
  if (!root) return;
  root.innerHTML = "";
  if (State.navigation.enabled) {
    const selected = selectedNavigationWorkspace();
    const rows = State.navigation.workspaces.map(workspace => `
      <button type="button" class="workspace-switch-row ${workspace.id === State.navigation.workspaceId ? "active" : ""}" data-navigation-workspace="${escapeHtml(workspace.id)}">
        <span class="workspace-switch-avatar">${escapeHtml((workspace.name || "W").slice(0, 1).toUpperCase())}</span>
        <span><strong>${escapeHtml(workspace.name)}</strong><small>${escapeHtml(workspace.access?.role || "member")}</small></span>
        <span>${workspace.id === State.navigation.workspaceId ? "✓" : ""}</span>
      </button>
    `);
    root.innerHTML = `
      <div class="workspace-popover-head">
        <span class="workspace-switch-avatar">${escapeHtml((selected?.name || "W").slice(0, 1).toUpperCase())}</span>
        <span><strong>${escapeHtml(selected?.name || "Workspace")}</strong><small>서버 동기화 탐색 설정</small></span>
      </div>
      <div class="workspace-account">${escapeHtml(State.user?.email || State.user?.id || "")}</div>
      ${rows.join("")}
      ${State.navigation.collaborativeSharing ? '<button type="button" class="workspace-menu-row" data-create-collaborative-workspace><span>＋</span><span>새 워크스페이스</span></button>' : ""}
      ${Array.isArray(State.workspaceAccess?.members) || ["owner", "membership_admin"].includes(String(selected?.access?.role || "")) ? '<button type="button" class="workspace-menu-row" data-workspace-action="people"><span>♧</span><span>설정과 멤버</span></button>' : ""}
      <button type="button" class="workspace-menu-row" data-navigation-settings><span>⚙</span><span>사이드바 설정</span></button>
      <button type="button" class="workspace-menu-row" data-workspace-action="theme"><span>${document.documentElement.dataset.theme === "dark" ? "☀" : "☾"}</span><span>${document.documentElement.dataset.theme === "dark" ? "라이트 모드" : "다크 모드"}</span></button>
      <button type="button" class="workspace-menu-row subtle" data-workspace-action="logout"><span></span><span>로그아웃</span></button>
    `;
    root.querySelectorAll("[data-navigation-workspace]").forEach(button => {
      button.addEventListener("click", () => switchCanonicalWorkspace(button.dataset.navigationWorkspace || ""));
    });
    root.querySelector("[data-navigation-settings]")?.addEventListener("click", openNavigationSettings);
    root.querySelector("[data-create-collaborative-workspace]")?.addEventListener("click", createCollaborativeWorkspace);
    root.querySelectorAll("[data-workspace-action]").forEach(button => {
      button.addEventListener("click", event => handleWorkspaceAction(event.currentTarget.dataset.workspaceAction));
    });
    return;
  }
  const label = State.user?.display_name || State.user?.id || "admin";
  const owners = guestOwners();
  const email = State.user?.email || State.user?.id || label;
  const rows = [
    `<button type="button" class="workspace-switch-row ${State.workspaceOwnerFilter ? "" : "active"}" data-owner="">
      <span class="workspace-switch-avatar">${escapeHtml((label || "N").slice(0, 1).toUpperCase())}</span>
      <span><strong>${escapeHtml(label)}의 Notion</strong><small>내 워크스페이스</small></span>
      <span>${State.workspaceOwnerFilter ? "" : "✓"}</span>
    </button>`,
    ...owners.map(owner => {
      const count = sharedPageCountForOwner(owner);
      return `
      <button type="button" class="workspace-switch-row ${State.workspaceOwnerFilter === owner ? "active" : ""}" data-owner="${escapeHtml(owner)}">
        <span class="workspace-switch-avatar muted">${escapeHtml(owner.slice(0, 1).toUpperCase())}</span>
        <span><strong>${escapeHtml(owner)}의 Notion</strong><small><em>게스트</em> ${count ? `${count}개 공유 페이지` : "공유받은 페이지 없음"}</small></span>
        <span>${State.workspaceOwnerFilter === owner ? "✓" : ""}</span>
      </button>
    `;
    }),
  ];
  root.innerHTML = `
    <div class="workspace-popover-head">
      <span class="workspace-switch-avatar">${escapeHtml((label || "N").slice(0, 1).toUpperCase())}</span>
      <span><strong>${escapeHtml(label)}의 Notion</strong><small>로컬 워크스페이스 · ${escapeHtml(State.pages.filter(p => p.scope === "own").length)}개 페이지</small></span>
    </div>
    <div class="workspace-account">${escapeHtml(email)}</div>
    ${rows.join("")}
    <button type="button" class="workspace-menu-row" data-workspace-action="theme"><span>${document.documentElement.dataset.theme === "dark" ? "☀" : "☾"}</span><span>${document.documentElement.dataset.theme === "dark" ? "라이트 모드" : "다크 모드"}</span></button>
    <button type="button" class="workspace-menu-row subtle" data-workspace-action="logout"><span></span><span>로그아웃</span></button>
  `;
  root.querySelectorAll("[data-owner]").forEach(btn => btn.addEventListener("click", () => switchWorkspaceOwner(btn.dataset.owner || "")));
  root.querySelectorAll("[data-workspace-action]").forEach(btn => btn.addEventListener("click", e => handleWorkspaceAction(e.currentTarget.dataset.workspaceAction)));
}

async function createCollaborativeWorkspace() {
  if (!State.navigation.collaborativeSharing) return;
  const name = window.prompt("새 워크스페이스 이름", "새 워크스페이스")?.trim();
  if (!name) return;
  try {
    const result = await api("/api/notion/workspaces", {
      method: "POST",
      body: { name, icon: name.slice(0, 1).toUpperCase(), operation_id: navigationOperationId("workspace-create") },
    });
    const payload = await api("/api/notion/workspaces");
    const bootstrap = normalizeWorkspaceNavigationBootstrap(payload, result.workspace?.id || "");
    State.navigation.workspaces = bootstrap.workspaces;
    State.navigation.collaborativeSharing = bootstrap.collaborativeSharing;
    State.navigation.collaborativeEditing = bootstrap.collaborativeEditing;
    State.navigation.collaborativeFiles = bootstrap.collaborativeFiles;
    State.navigation.collaborativeHistory = bootstrap.collaborativeHistory;
    State.navigation.collaborativeSearch = bootstrap.collaborativeSearch;
    await switchCanonicalWorkspace(result.workspace?.id || bootstrap.workspaceId);
    setStatus("워크스페이스를 만들었습니다.", "ok");
  } catch (error) {
    setStatus("워크스페이스 생성 실패: " + error.message, "error");
  }
}

function renderBreadcrumbPopover() {
  const root = $("#breadcrumbHoverPages");
  if (!root) return;
  root.innerHTML = "";
  const pages = visiblePages();
  if (!pages.length) {
    root.innerHTML = '<div class="mini-row"><span>페이지 없음</span></div>';
    return;
  }
  renderPageGroup(root, pages, { compact: true });
}

function renderSearchCreators() {
  const sel = $("#searchCreatorSelect");
  if (!sel) return;
  const current = sel.value;
  const owners = Array.from(new Set(State.pages.map(p => p.owner_user_id).filter(Boolean))).sort();
  sel.innerHTML = [
    '<option value="">생성자</option>',
    '<option value="me">나</option>',
    ...owners.map(owner => `<option value="${escapeHtml(owner)}">${escapeHtml(owner)}</option>`),
  ].join("");
  if ([...sel.options].some(opt => opt.value === current)) sel.value = current;
}

function renderPageGroup(root, pages, opts = {}) {
  if (!root) return;
  root.innerHTML = "";
  if (opts.tree) {
    root.setAttribute?.("role", "tree");
    root.setAttribute?.("aria-label", String(opts.treeLabel || "페이지"));
  } else {
    root.removeAttribute?.("role");
    root.removeAttribute?.("aria-label");
  }
  if (!pages.length) {
    const empty = document.createElement("div");
    empty.className = "mini-row";
    empty.innerHTML = "<span>비어 있음</span>";
    root.appendChild(empty);
    return;
  }
  if (opts.flat) {
    pages.forEach(page => appendPageRow(root, page, 0, Boolean(opts.compact), false, false));
    return;
  }

  const byParent = new Map();
  const pageIds = new Set(pages.map(page => page.id).filter(Boolean));
  pages.forEach(page => {
    const rawParent = page.parent_id || "";
    const key = rawParent && rawParent !== page.id && pageIds.has(rawParent) ? rawParent : "";
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(page);
  });
  const rendered = new Set();
  const markHiddenDescendants = parentId => {
    (byParent.get(parentId) || []).forEach(page => {
      if (rendered.has(page.id)) return;
      rendered.add(page.id);
      markHiddenDescendants(page.id);
    });
  };
  const drawLevel = (parentId, depth) => {
    const rows = byParent.get(parentId) || [];
    rows.sort((a, b) => (a.position || 0) - (b.position || 0) || String(a.title).localeCompare(String(b.title)));
    rows.forEach(page => {
      const hasChildren = Boolean((byParent.get(page.id) || []).length);
      rendered.add(page.id);
      appendPageRow(
        root,
        page,
        depth,
        Boolean(opts.compact),
        hasChildren,
        Boolean(opts.tree),
      );
      if (State.collapsedPageIds.has(page.id)) markHiddenDescendants(page.id);
      else drawLevel(page.id, depth + 1);
    });
  };
  drawLevel("", 0);
  pages
    .filter(page => !rendered.has(page.id))
    .forEach(page => appendPageRow(
      root,
      page,
      0,
      Boolean(opts.compact),
      false,
      Boolean(opts.tree),
    ));
}

function appendPageRow(
  root,
  page,
  depth,
  compact = false,
  hasChildren = false,
  tree = false,
) {
  try {
    root.appendChild(pageRow(page, depth, compact, hasChildren, tree, root.id));
  } catch (err) {
    const fallback = document.createElement("button");
    fallback.type = "button";
    fallback.className = "page-row";
    fallback.style.paddingLeft = `${8 + depth * 14}px`;
    fallback.innerHTML = `<span></span><span class="page-icon">${iconSvg("doc")}</span><span class="page-title-label">${escapeHtml(page.title || "제목 없음")}</span>`;
    fallback.addEventListener("click", event => openPage(
      page.id,
      true,
      event.detail === 0 ? PageFocusPolicy.PAGE_CANVAS : PageFocusPolicy.PRESERVE,
    ));
    root.appendChild(fallback);
  }
}

function pageRow(
  page,
  depth,
  compact = false,
  hasChildren = false,
  tree = false,
  rootId = "",
) {
  const btn = document.createElement("div");
  btn.tabIndex = 0;
  btn.setAttribute("role", tree ? "treeitem" : "button");
  if (tree) {
    btn.setAttribute("aria-level", String(depth + 1));
    if (hasChildren) {
      btn.setAttribute(
        "aria-expanded",
        State.collapsedPageIds.has(page.id) ? "false" : "true",
      );
    }
  }
  btn.className = "page-row" + (compact ? " compact" : "") + (page.id === State.activePageId ? " active" : "");
  btn.dataset.pageId = page.id;
  btn.dataset.parentId = page.parent_id || "";
  btn.style.paddingLeft = `${8 + depth * 14}px`;
  btn.draggable = !compact && page.scope === "own" && page.can_edit;
  const scope = page.scope === "shared" ? page.role || "view" : "";
  const collapsed = State.collapsedPageIds.has(page.id);
  const canRowEdit = !compact && page.can_edit;
  const favoriteMark = pageIsFavorite(page) ? "★" : "";
  btn.innerHTML = `
    <span class="page-disclosure ${hasChildren ? "" : "empty"}" data-page-toggle="${hasChildren ? page.id : ""}" aria-label="${collapsed ? "expand" : "collapse"}">${hasChildren ? iconSvg(collapsed ? "right" : "down") : ""}</span>
    <span class="page-icon">${pageIconHtml(page)}</span>
    <span class="page-title-label">${escapeHtml(page.title || "제목 없음")}</span>
    <span class="scope-pill">${compact ? "" : escapeHtml(scope)}</span>
    <span class="page-favorite-mark">${favoriteMark}</span>
    ${canRowEdit ? `
      <span class="page-row-actions">
        <button type="button" class="page-row-action" data-page-menu="${escapeAttr(page.id)}" title="페이지 옵션">…</button>
        <button type="button" class="page-row-action" data-page-child="${escapeAttr(page.id)}" title="하위 페이지 추가">＋</button>
      </span>
    ` : ""}
  `;
  btn.addEventListener("click", e => {
    if (e.target.closest("[data-page-toggle]")) {
      e.stopPropagation();
      if (hasChildren) togglePageCollapsed(page.id, rootId);
      return;
    }
    if (e.target.closest(".page-row-action")) return;
    if (e.altKey) openSidePeek(page.id);
    else openPage(
      page.id,
      true,
      e.detail === 0 ? PageFocusPolicy.PAGE_CANVAS : PageFocusPolicy.PRESERVE,
    );
  });
  btn.addEventListener("keydown", e => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openPage(page.id);
    } else if (e.key === "ArrowRight" && hasChildren) {
      e.preventDefault();
      if (State.collapsedPageIds.has(page.id)) {
        togglePageCollapsed(page.id, rootId);
      }
    } else if (e.key === "ArrowLeft" && hasChildren) {
      e.preventDefault();
    }
  });
  btn.querySelector("[data-page-menu]")?.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    openPageMenu(page, e.currentTarget);
  });
  btn.querySelector("[data-page-child]")?.addEventListener("click", e => {
    e.preventDefault();
    e.stopPropagation();
    createPage(page.id);
  });
  if (btn.draggable) bindPageDrag(btn, page, rootId);
  return btn;
}

function clearPageDropIndicators() {
  document.querySelectorAll(".page-row.drag-before, .page-row.drag-after, .page-row.drag-inside, .page-row.dragging").forEach(el => {
    el.classList.remove("drag-before", "drag-after", "drag-inside", "dragging");
  });
}

function pageDropMode(e, row) {
  const rect = row.getBoundingClientRect();
  const y = e.clientY - rect.top;
  if (y < rect.height * 0.28) return "before";
  if (y > rect.height * 0.72) return "after";
  return "inside";
}

function markPageDrop(row, mode) {
  clearPageDropIndicators();
  row.classList.add(mode === "before" ? "drag-before" : mode === "after" ? "drag-after" : "drag-inside");
}

function descendantPageIds(pageId) {
  const childrenByParent = new Map();
  ownPages().forEach(page => {
    const parentId = page.parent_id || "";
    if (!parentId) return;
    if (!childrenByParent.has(parentId)) childrenByParent.set(parentId, []);
    childrenByParent.get(parentId).push(page.id);
  });
  const descendants = new Set();
  const pending = [...(childrenByParent.get(pageId) || [])];
  while (pending.length) {
    const id = pending.pop();
    if (!id || descendants.has(id)) continue;
    descendants.add(id);
    pending.push(...(childrenByParent.get(id) || []));
  }
  return descendants;
}

function isInvalidPageDrop(draggedId, targetId) {
  if (!draggedId || !targetId || draggedId === targetId) return true;
  return descendantPageIds(draggedId).has(targetId);
}

function bindPageDrag(row, page, rootId = "ownPages") {
  row.addEventListener("dragstart", e => {
    State.pageDrag = { id: page.id, mode: "", targetId: "" };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", page.id);
    setTimeout(() => row.classList.add("dragging"), 0);
  });
  row.addEventListener("dragover", e => {
    const dragged = State.pageDrag.id || e.dataTransfer.getData("text/plain");
    if (!dragged || dragged === page.id || page.scope !== "own" || !page.can_edit) return;
    if (isInvalidPageDrop(dragged, page.id)) {
      clearPageDropIndicators();
      e.dataTransfer.dropEffect = "none";
      return;
    }
    e.preventDefault();
    const mode = pageDropMode(e, row);
    State.pageDrag = { id: dragged, mode, targetId: page.id };
    markPageDrop(row, mode);
  });
  row.addEventListener("dragleave", e => {
    if (!row.contains(e.relatedTarget)) row.classList.remove("drag-before", "drag-after", "drag-inside");
  });
  row.addEventListener("drop", async e => {
    const dragged = State.pageDrag.id || e.dataTransfer.getData("text/plain");
    if (!dragged || dragged === page.id) return;
    e.preventDefault();
    if (isInvalidPageDrop(dragged, page.id)) {
      clearPageDropIndicators();
      setStatus("자기 하위 페이지 쪽으로는 이동할 수 없습니다.", "error");
      return;
    }
    const mode = State.pageDrag.targetId === page.id ? State.pageDrag.mode : pageDropMode(e, row);
    const body = mode === "inside"
      ? { parent_id: page.id }
      : mode === "before"
        ? { before_id: page.id }
        : { after_id: page.id };
    try {
      await movePageSafely(dragged, body);
      if (mode === "inside") {
        State.collapsedPageIds.delete(page.id);
        saveCollapsedPages();
      }
      await Promise.all([loadPages(), loadInboxItems(false)]);
      focusPageTreeRow(dragged, rootId || "ownPages");
      setStatus("페이지 위치를 이동했습니다.", "ok");
    } catch (err) {
      setStatus("페이지 이동 실패: " + err.message, "error");
    } finally {
      State.pageDrag = { id: "", mode: "", targetId: "" };
      clearPageDropIndicators();
    }
  });
  row.addEventListener("dragend", () => {
    State.pageDrag = { id: "", mode: "", targetId: "" };
    clearPageDropIndicators();
  });
}

function bindPageListRootDrop(root) {
  if (!root || root.dataset.rootDropBound === "1") return;
  root.dataset.rootDropBound = "1";
  root.addEventListener("dragover", e => {
    const dragged = State.pageDrag.id || e.dataTransfer?.getData("text/plain");
    if (!dragged || e.target.closest(".page-row")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    root.classList.add("drop-root");
    State.pageDrag = { id: dragged, mode: "root", targetId: "" };
  });
  root.addEventListener("dragleave", e => {
    if (!root.contains(e.relatedTarget)) root.classList.remove("drop-root");
  });
  root.addEventListener("drop", async e => {
    const dragged = State.pageDrag.id || e.dataTransfer?.getData("text/plain");
    if (!dragged || e.target.closest(".page-row")) return;
    e.preventDefault();
    try {
      await movePageSafely(dragged, { parent_id: "" });
      await loadPages();
      focusPageTreeRow(dragged, root.id || "ownPages");
      setStatus("페이지를 최상위로 이동했습니다.", "ok");
    } catch (err) {
      setStatus("페이지 이동 실패: " + err.message, "error");
    } finally {
      root.classList.remove("drop-root");
      State.pageDrag = { id: "", mode: "", targetId: "" };
      clearPageDropIndicators();
    }
  });
}

function closePageMenu() {
  const menu = $("#pageMenu");
  if (menu) menu.hidden = true;
}

function focusPageMenuAfterLifecycle({ transientHome = false } = {}) {
  const button = $("#pageMoreBtn");
  if (!button) return;
  if (transientHome && !State.pageData) {
    button.hidden = false;
    button.dataset.lifecycleFocusOnly = "true";
    button.addEventListener("blur", () => {
      if (!State.pageData) button.hidden = true;
      delete button.dataset.lifecycleFocusOnly;
    }, { once: true });
  }
  requestAnimationFrame(() => button.focus({ preventScroll: true }));
}

function focusCurrentBreadcrumbAfterLifecycle() {
  requestAnimationFrame(() => {
    const segments = document.querySelectorAll("#breadcrumb .breadcrumb-segment");
    segments[segments.length - 1]?.focus({ preventScroll: true });
  });
}

function closeNewPageMenu(restoreFocus = false) {
  const menu = $("#newPageMenu");
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = "";
  if (restoreFocus) $("#sidebarNewBtn")?.focus({ preventScroll: true });
}

function openNewPageMenu(anchor) {
  const menu = $("#newPageMenu");
  if (!menu || !anchor) return;
  closeMovePageMenu();
  closePageMenu();
  const rect = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - 286, rect.left - 4))}px`;
  menu.style.top = `${Math.max(8, Math.min(window.innerHeight - 72, rect.top - 48))}px`;
  menu.innerHTML = `
    <button type="button" class="menu-item" role="menuitem" data-new-page-type="page">
      <span>${iconSvg("page")}</span><span>페이지</span><small></small>
    </button>
  `;
  const pageType = menu.querySelector('[data-new-page-type="page"]');
  pageType?.addEventListener("click", async () => {
    closeNewPageMenu();
    await createPage();
  });
  menu.hidden = false;
  pageType?.focus({ preventScroll: true });
}

function closeMovePageMenu(restoreFocus = false) {
  const menu = $("#movePageMenu");
  if (!menu) return;
  menu.hidden = true;
  menu.innerHTML = "";
  if (restoreFocus) $("#pageMoreBtn")?.focus({ preventScroll: true });
}

function openPageMenu(page, anchor) {
  const menu = $("#pageMenu");
  if (!menu || !page) return;
  const rect = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - 286, rect.left - 220))}px`;
  menu.style.top = `${Math.max(8, Math.min(window.innerHeight - 430, rect.bottom + 4))}px`;
  const isFavorite = pageIsFavorite(page);
  const canManage = Boolean(page.can_edit);
  const canMove = page.role === "owner";
  const settings = page.settings || {};
  const manageItems = canManage ? `
    ${canMove ? '<button type="button" class="menu-item" data-page-action="duplicate"><span>⧉</span><span>복제</span><small>Ctrl+D</small></button>' : ""}
    <button type="button" class="menu-item" data-page-action="rename"><span>✎</span><span>이름 바꾸기</span><small>Ctrl+Shift+R</small></button>
    ${canMove ? '<button type="button" class="menu-item" data-page-action="move"><span>↪</span><span>옮기기</span><small></small></button>' : ""}
    <button type="button" class="menu-item danger-item" data-page-action="trash"><span>${iconSvg("trash")}</span><span>휴지통으로 이동</span><small></small></button>
    <div class="menu-separator"></div>
    <div class="page-font-picker">
      <button type="button" data-page-action="font_default" class="${!settings.font || settings.font === "default" ? "active" : ""}"><span>Aa</span><small>기본</small></button>
      <button type="button" data-page-action="font_serif" class="${settings.font === "serif" ? "active" : ""}"><span class="serif-sample">Ag</span><small>세리프</small></button>
      <button type="button" data-page-action="font_mono" class="${settings.font === "mono" ? "active" : ""}"><span class="mono-sample">Ag</span><small>고정폭</small></button>
    </div>
    <button type="button" class="menu-item" data-page-action="small_text"><span>A</span><span>작은 텍스트</span><small>${settings.small_text ? "✓" : ""}</small></button>
    <button type="button" class="menu-item" data-page-action="full_width"><span>↔</span><span>전체 너비</span><small>${settings.full_width ? "✓" : ""}</small></button>
    <button type="button" class="menu-item" data-page-action="lock"><span>${settings.locked ? "🔓" : "🔒"}</span><span>${settings.locked ? "페이지 잠금 해제" : "페이지 잠금"}</span><small></small></button>
    <button type="button" class="menu-item" data-page-action="history"><span>↶</span><span>페이지 기록</span><small></small></button>
    <button type="button" class="menu-item" data-page-action="export"><span>↓</span><span>내보내기</span><small>Markdown</small></button>
    <button type="button" class="menu-item" data-page-action="import"><span>↑</span><span>가져오기</span><small>Markdown · CSV</small></button>
    <div class="menu-separator"></div>
    <button type="button" class="menu-item" data-page-action="second_brain"><span>↗</span><span>Second Brain에 게시</span><small></small></button>
  ` : "";
  menu.innerHTML = `
    <div class="menu-label">페이지</div>
    <button type="button" class="menu-item" data-page-action="favorite"><span>${isFavorite ? "★" : "☆"}</span><span>${isFavorite ? "즐겨찾기에서 제거" : "즐겨찾기에 추가"}</span><small></small></button>
    <button type="button" class="menu-item" data-page-action="copy_link"><span>${iconSvg("link")}</span><span>링크 복사</span><small></small></button>
    <button type="button" class="menu-item" data-page-action="context"><span>${iconSvg("chat")}</span><span>Chat에 추가</span><small></small></button>
    <button type="button" class="menu-item" data-page-action="files"><span>${iconSvg("doc")}</span><span>첨부파일</span><small></small></button>
    ${manageItems}
    <div class="menu-separator"></div>
    <button type="button" class="menu-item" data-page-action="new_tab"><span>↗</span><span>새 탭에서 열기</span><small></small></button>
    <button type="button" class="menu-item" data-page-action="side_peek"><span>▣</span><span>사이드 보기에서 열기</span><small>Alt+Click</small></button>
    <div class="menu-foot">${escapeHtml(page.owner_user_id || State.user?.id || "admin")} 최종 편집<br>${escapeHtml(formatTime(page.updated_at))}</div>
  `;
  menu.querySelectorAll("[data-page-action]").forEach(btn => {
    btn.addEventListener("click", () => handlePageMenuAction(btn.dataset.pageAction, page));
  });
  menu.hidden = false;
}

async function handlePageMenuAction(action, page) {
  closePageMenu();
  if (!page?.id) return;
  if (action === "favorite") {
    if (State.navigation.enabled) {
      await toggleWorkspaceFavorite(page);
    } else {
      if (State.favorites.has(page.id)) State.favorites.delete(page.id);
      else State.favorites.add(page.id);
      saveFavoritePages();
      renderPageLists();
      setStatus(State.favorites.has(page.id) ? "즐겨찾기에 추가했습니다." : "즐겨찾기에서 제거했습니다.", "ok");
    }
  } else if (action === "copy_link") {
    copyText(pageUrl(page.id), "페이지 링크 복사됨");
  } else if (action === "context") {
    openDrawer("context");
  } else if (action === "files") {
    openDrawer("files");
  } else if (action === "duplicate") {
    await duplicatePage(page.id);
  } else if (action === "rename") {
    await renamePage(page);
  } else if (action === "move") {
    openMovePageDialog(page);
  } else if (action === "trash") {
    await archivePageById(page.id);
  } else if (action.startsWith("font_")) {
    await updatePageSettings({ font: action.slice(5) }, page.id);
  } else if (action === "small_text") {
    await updatePageSettings({ small_text: !page.settings?.small_text }, page.id);
  } else if (action === "full_width") {
    await updatePageSettings({ full_width: !page.settings?.full_width }, page.id);
  } else if (action === "lock") {
    await updatePageSettings({ locked: !page.settings?.locked }, page.id);
  } else if (action === "history") {
    if (State.activePageId !== page.id) await openPage(page.id);
    openDrawer("history");
  } else if (action === "export") {
    await openPageExportDialog(page);
  } else if (action === "import") {
    $("#markdownInput").dataset.parentId = page.id;
    $("#markdownInput").click();
  } else if (action === "second_brain") {
    await publishPageToSecondBrain(page.id);
  } else if (action === "new_tab") {
    window.open(pageUrl(page.id), "_blank", "noopener");
  } else if (action === "side_peek") {
    openSidePeek(page.id);
  }
  renderTopbarPageActions(State.pageData?.page || null);
}

async function updatePageSettings(settings, pageId = State.activePageId) {
  const activePage = State.pageData?.page;
  const sidePage = State.sidePeek.data?.page;
  const page = activePage?.id === pageId
    ? activePage
    : pageSummaryById(pageId) || (sidePage?.id === pageId ? sidePage : null);
  if (!pageId || !page?.can_edit) return;
  try {
    if (!(await flushPageDraft(pageId))) {
      setStatus("저장되지 않은 편집본이 있어 페이지 설정을 변경하지 않았습니다.", "error");
      return;
    }
    const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}`, {
      method: "PATCH",
      body: { settings },
    });
    if (activePage?.id === pageId) {
      State.pageData = normalizedPageData(data, State.pageData);
      applySaveMetadata(State, data);
      State.dirty = false;
      renderEditor();
      resetEditorHistory();
    }
    if (State.sidePeek.pageId === pageId) {
      State.sidePeek.data = normalizedPageData(data, State.sidePeek.data);
      applySaveMetadata(State.sidePeek, data);
      State.sidePeek.dirty = false;
      renderSidePeek();
    }
    await loadPages();
    setStatus(data.page?.settings?.locked ? "페이지가 잠겼습니다." : "페이지 설정 저장됨", "ok");
  } catch (error) {
    setStatus("페이지 설정 실패: " + error.message, "error");
  }
}

async function updatePageAppearance(updates) {
  const page = State.pageData?.page;
  if (!page?.id || !page.can_edit || pageIsLocked(page)) return;
  try {
    if (!(await flushPageDraft(page.id))) {
      setStatus("저장되지 않은 편집본이 있어 페이지 모양을 변경하지 않았습니다.", "error");
      return;
    }
    const data = await api(`/api/notion/pages/${encodeURIComponent(page.id)}`, {
      method: "PATCH",
      body: updates,
    });
    State.pageData = normalizedPageData(data, State.pageData);
    applySaveMetadata(State, data);
    State.dirty = false;
    if (State.sidePeek.pageId === page.id && !State.sidePeek.dirty) {
      State.sidePeek.data = normalizedPageData(data, State.sidePeek.data);
      applySaveMetadata(State.sidePeek, data);
      renderSidePeek();
    }
    renderEditor();
    resetEditorHistory();
    await loadPages();
    setStatus("페이지 모양 저장됨", "ok");
  } catch (error) {
    setStatus("페이지 모양 저장 실패: " + error.message, "error");
  }
}

function openIconPicker() {
  if (!activePageCanEdit()) return;
  const icons = ["😀", "😊", "🚀", "💡", "📌", "📚", "📝", "✅", "🎯", "🧠", "🔬", "💻", "📊", "🗓️", "⭐", "🔥", "🌱", "🌐", "🔒", "❤️"];
  openModal("아이콘", `<div class="icon-picker">${icons.map(icon => `<button type="button" data-page-icon="${escapeAttr(icon)}">${icon}</button>`).join("")}</div>`, [
    { label: "아이콘 제거", action: async () => { closeModal(); await updatePageAppearance({ icon: "" }); } },
    { label: "닫기", action: closeModal },
  ]);
  document.querySelectorAll("[data-page-icon]").forEach(button => button.addEventListener("click", async () => {
    const icon = button.dataset.pageIcon || "";
    closeModal();
    await updatePageAppearance({ icon });
  }));
}

function openCoverPicker() {
  if (!activePageCanEdit()) return;
  const covers = [
    "linear-gradient(120deg,#dfe9f3 0%,#fff 100%)",
    "linear-gradient(120deg,#f6d365 0%,#fda085 100%)",
    "linear-gradient(120deg,#84fab0 0%,#8fd3f4 100%)",
    "linear-gradient(120deg,#a18cd1 0%,#fbc2eb 100%)",
    "linear-gradient(120deg,#fccb90 0%,#d57eeb 100%)",
    "linear-gradient(120deg,#e0c3fc 0%,#8ec5fc 100%)",
  ];
  openModal("커버", `
    <div class="cover-picker">${covers.map(cover => `<button type="button" data-page-cover="${escapeAttr(cover)}" style="background:${escapeAttr(cover)}"></button>`).join("")}</div>
    <label class="cover-url-label">이미지 URL<input id="coverUrlInput" type="url" placeholder="https://example.com/cover.jpg"></label>
  `, [
    { label: "취소", action: closeModal },
    { label: "URL 적용", primary: true, action: async () => {
      const value = safeExternalUrl($("#coverUrlInput")?.value || "");
      if (!value) return;
      closeModal();
      await updatePageAppearance({ cover: value });
    } },
  ]);
  document.querySelectorAll("[data-page-cover]").forEach(button => button.addEventListener("click", async () => {
    closeModal();
    await updatePageAppearance({ cover: button.dataset.pageCover || "" });
  }));
}

async function loadBacklinks() {
  const pageId = State.activePageId;
  if (!pageId) return;
  try {
    const workspaceBase = collaborativeSearchWorkspacePath();
    const data = await api(workspaceBase
      ? `${workspaceBase}/pages/${encodeURIComponent(pageId)}/backlinks`
      : `/api/notion/pages/${encodeURIComponent(pageId)}/backlinks`);
    if (State.activePageId !== pageId) return;
    State.backlinks = data.items || [];
  } catch {
    if (State.activePageId === pageId) State.backlinks = [];
  }
  renderBacklinksButton();
}

function renderBacklinksButton() {
  const button = $("#pageBacklinksBtn");
  if (!button) return;
  const count = State.backlinks.length;
  button.hidden = !State.pageData || !count;
  button.textContent = count ? `↙ ${count}개의 백링크` : "";
}

function openBacklinksDialog() {
  if (!State.backlinks.length) return;
  openModal("이 페이지의 백링크", `
    <div class="backlink-list">${State.backlinks.map((item, index) => `
      <button type="button" data-open-backlink="${index}">
        <span>${pageIconHtml(item)}</span>
        <strong>${escapeHtml(item.title || "제목 없음")}</strong>
        <small>${item.match_count || 1}곳에서 연결</small>
      </button>
    `).join("")}</div>
  `);
  document.querySelectorAll("[data-open-backlink]").forEach(button => button.addEventListener("click", async () => {
    const item = State.backlinks[Number(button.dataset.openBacklink)];
    if (!item) return;
    closeModal();
    await openPage(item.id);
    const blockId = item.matching_block_ids?.[0];
    if (blockId) {
      history.replaceState({ pageId: item.id }, "", `${pageUrl(item.id)}#block-${encodeURIComponent(blockId)}`);
      scrollToLocationBlock();
    }
  }));
}

function inboxSeenKey() {
  return `notion.inbox.seen.${State.user?.id || "guest"}`;
}

function inboxSeenIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(inboxSeenKey()) || "[]"));
  } catch {
    return new Set();
  }
}

function saveInboxSeenIds(ids) {
  try {
    localStorage.setItem(inboxSeenKey(), JSON.stringify([...ids].slice(-1000)));
  } catch {}
}

function markInboxItemsSeen(ids) {
  const seen = inboxSeenIds();
  ids.forEach(id => { if (id) seen.add(id); });
  saveInboxSeenIds(seen);
  renderInboxBadge();
}

function outlookDesktopNotifiedKey() {
  return `notion.outlook.notified.${State.user?.id || "guest"}`;
}

function outlookDesktopNotifiedIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(outlookDesktopNotifiedKey()) || "[]"));
  } catch {
    return new Set();
  }
}

function notifyNewOutlookDigests() {
  if (!State.outlook?.settings?.browser_notifications) return;
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;
  const notified = outlookDesktopNotifiedIds();
  let changed = false;
  State.inboxItems
    .filter(item => item.kind === "outlook_digest" && item.due && !notified.has(item.id))
    .forEach(item => {
      const notification = new Notification("Outlook 미열람 주요 메일", {
        body: item.message || item.page_title || "오늘의 미열람 메일 브리핑이 준비됐습니다.",
        tag: item.id,
      });
      notification.onclick = () => {
        window.focus();
        if (item.page_id) openPage(item.page_id);
      };
      notified.add(item.id);
      changed = true;
    });
  if (changed) {
    try { localStorage.setItem(outlookDesktopNotifiedKey(), JSON.stringify([...notified].slice(-365))); } catch {}
  }
}

function renderInboxBadge() {
  const badge = $("#inboxBadge");
  if (!badge) return;
  const seen = inboxSeenIds();
  const count = State.inboxItems.filter(item => item.due && !seen.has(item.id)).length;
  badge.hidden = count === 0;
  badge.textContent = count > 99 ? "99+" : String(count);
}

async function loadInboxItems(renderPanel = false) {
  if (!State.user) return;
  try {
    const workspaceId = (State.navigation.collaborativeSharing || State.navigation.collaborativeSearch)
      ? State.navigation.workspaceId
      : "";
    const [legacyResult, liveResult, reminderResult] = await Promise.allSettled([
      api("/api/notion/inbox?limit=300"),
      workspaceId && State.navigation.collaborativeSharing
        ? api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/inbox?limit=200`)
        : Promise.resolve({ items: [] }),
      workspaceId && State.navigation.collaborativeSearch
        ? api(`/api/notion/workspaces/${encodeURIComponent(workspaceId)}/reminders?limit=200`)
        : Promise.resolve({ items: [] }),
    ]);
    const legacyItems = legacyResult.status === "fulfilled"
      ? (legacyResult.value.items || []).filter(
          item => item.kind !== "outlook_digest"
            && (!State.navigation.collaborativeSearch || item.kind !== "reminder"),
        )
      : [];
    const liveItems = liveResult.status === "fulfilled"
      ? (liveResult.value.items || []).map(item => {
          const page = State.pages.find(candidate => candidate.id === item.document_id);
          const comment = (State.comments || []).find(candidate => candidate.id === item.comment_id);
          const label = {
            comment_mentioned: "댓글에서 나를 멘션함",
            comment_replied: "내 댓글에 답글을 남김",
            comment_reacted: "내 댓글에 반응함",
          }[item.kind] || "댓글 알림";
          const commentPreview = String(comment?.content || "")
            .replace(/(^|\s)@[\w.-]+/g, " ")
            .trim();
          const pageTitle = page?.title || "공유 페이지";
          return {
            ...item,
            collaborative: true,
            canonical_kind: item.kind,
            kind: item.kind === "comment_mentioned" ? "mention" : "comment",
            page_id: item.document_id,
            page_title: item.kind === "comment_mentioned" ? `${pageTitle}에 댓글을 달았습니다.` : pageTitle,
            message: commentPreview || label,
            due: !item.read,
          };
      })
      : [];
    const reminderItems = reminderResult.status === "fulfilled"
      ? (reminderResult.value.items || []).map(item => ({
          ...item,
          canonicalReminder: true,
          collaborative: false,
          kind: "reminder",
        }))
      : [];
    const workspace = (State.workspaceAccess?.id === workspaceId ? State.workspaceAccess : null)
      || (State.navigation.workspaces || []).find(item => item.id === workspaceId)
      || null;
    const workspaceMembershipItems = workspace
      && State.user?.id
      && String(workspace.owner_subject_id || "") !== String(State.user.id)
      && String(workspace.access?.role || "") !== "guest"
      ? [{
          id: `workspace-membership:${workspace.id}:${workspace.access?.epoch || 1}`,
          kind: "workspace_membership",
          collaborative: false,
          workspace_id: workspace.id,
          page_id: State.activePageId || "",
          page_title: `${State.users.find(item => item.id === workspace.owner_subject_id)?.display_name || workspace.owner_subject_id || "워크스페이스 소유자"}님이 다음에 추가함`,
          message: workspace.name || "워크스페이스",
          due: true,
          created_at: Number(workspace.updated_at || workspace.created_at || Math.floor(Date.now() / 1000)),
        }]
      : [];
    State.inboxItems = [...liveItems, ...workspaceMembershipItems, ...reminderItems, ...legacyItems];
    renderInboxBadge();
    if (renderPanel || !$("#inboxOverlay")?.hidden) renderInboxItems();
  } catch (error) {
    if (renderPanel) $("#inboxList").innerHTML = `<div class="inbox-empty">${escapeHtml(error.message)}</div>`;
  }
}

function renderInboxItems() {
  const root = $("#inboxList");
  if (!root) return;
  const filter = $("#inboxFilter")?.value || "all";
  const seen = inboxSeenIds();
  const items = State.inboxItems.filter(item => {
    if (filter === "due") return Boolean(item.due);
    if (filter === "upcoming") return item.kind === "reminder" && !item.due;
    if (filter === "mention") return item.kind === "mention";
    return true;
  });
  const itemHtml = item => {
    const unread = item.due && !seen.has(item.id);
    const when = item.kind === "reminder" && item.remind_at
      ? new Date(item.remind_at).toLocaleString()
      : formatRelative(item.created_at);
    const icon = item.kind === "reminder" ? "⏰" : (item.kind === "workspace_membership" ? "!" : "@");
    return `<button type="button" class="inbox-item${unread ? " unread" : ""}" data-inbox-kind="${escapeAttr(item.kind || "comment")}" data-inbox-item="${escapeAttr(item.id)}">
      <span class="inbox-item-icon">${icon}</span>
      <span class="inbox-item-main"><strong>${escapeHtml(item.page_title || "제목 없음")}</strong><span>${escapeHtml(item.message || (item.kind === "reminder" ? "리마인더" : "나를 멘션함"))}</span></span>
      <small>${escapeHtml(when)}</small>
    </button>`;
  };
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const groups = [
    { key: "today", label: "오늘", items: [] },
    { key: "yesterday", label: "어제", items: [] },
    { key: "earlier", label: "이전", items: [] },
  ];
  items.forEach(item => {
    const createdAt = new Date(item.created_at || item.remind_at || 0);
    const dayDelta = Number.isNaN(createdAt.getTime())
      ? 2
      : Math.floor((startOfToday.getTime() - new Date(createdAt.getFullYear(), createdAt.getMonth(), createdAt.getDate()).getTime()) / 86400000);
    const group = dayDelta <= 0 ? groups[0] : (dayDelta === 1 ? groups[1] : groups[2]);
    group.items.push(item);
  });
  root.innerHTML = items.length ? groups.filter(group => group.items.length).map(group => `
    <section class="inbox-group" data-inbox-group="${group.key}" aria-label="${group.label}">
      <h3 class="inbox-group-label">${group.label}</h3>
      ${group.items.map(itemHtml).join("")}
    </section>`).join("") : '<div class="inbox-empty">표시할 알림이 없습니다.</div>';
  root.querySelectorAll("[data-inbox-item]").forEach(button => button.addEventListener("click", async () => {
    const item = State.inboxItems.find(candidate => candidate.id === button.dataset.inboxItem);
    if (!item) return;
    if (item.collaborative && !item.read) {
      try {
        await api(
          `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/inbox/${encodeURIComponent(item.id)}`,
          { method: "PATCH", body: { read: true, operation_id: newOperationId() } },
        );
        item.read = true;
        item.due = false;
      } catch {}
    }
    markInboxItemsSeen([item.id]);
    $("#inboxOverlay").hidden = true;
    await openPage(item.page_id);
    if (item.block_id) {
      history.replaceState({ pageId: item.page_id }, "", `${pageUrl(item.page_id)}#block-${encodeURIComponent(item.block_id)}`);
      scrollToLocationBlock();
    }
  }));
}

async function openInboxOverlay() {
  if (!$("#inspector")?.hidden) closeDrawer(false);
  $("#inboxOverlay").hidden = false;
  $("#inboxList").innerHTML = '<div class="inbox-empty">불러오는 중...</div>';
  await loadInboxItems(true);
}

async function markAllInboxItemsRead() {
  const collaborative = State.inboxItems.filter(item => item.collaborative && !item.read);
  await Promise.allSettled(collaborative.map(item => api(
    `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/inbox/${encodeURIComponent(item.id)}`,
    { method: "PATCH", body: { read: true, operation_id: newOperationId() } },
  )));
  collaborative.forEach(item => {
    item.read = true;
    item.due = false;
  });
  markInboxItemsSeen(State.inboxItems.filter(item => item.due).map(item => item.id));
  renderInboxItems();
  renderInboxBadge();
}

async function loadComments(blockId = State.commentBlockId) {
  const pageId = State.activePageId;
  if (!pageId) return;
  State.commentBlockId = blockId || "";
  try {
    const collaborativeBase = collaborativeCommentBasePath();
    const data = await api(
      collaborativeBase || `/api/notion/pages/${encodeURIComponent(pageId)}/comments`,
    );
    State.comments = (data.items || []).map(item => ({
      ...item,
      author_user_id: item.author_user_id || item.author_subject_id,
      parent_id: item.parent_id || item.parent_comment_id,
    }));
    if (!State.comments.some(item => item.id === State.commentReplyParentId)) setCommentReply("");
    renderComments();
  } catch (error) {
    $("#commentList").innerHTML = `<div class="mini-row"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function commentMentionSubjectIds(content) {
  const text = String(content || "").toLocaleLowerCase();
  return State.users.filter(user => {
    const id = String(user.id || "").toLocaleLowerCase();
    const displayName = String(user.display_name || "").toLocaleLowerCase();
    return (id && text.includes(`@${id}`))
      || (displayName && text.includes(`@${displayName}`));
  }).map(user => user.id);
}

function collaborativeCommentCommand(item = null) {
  return {
    expected_workspace_epoch: Number(State.pageData?.page?.workspace_epoch || 0),
    expected_document_epoch: Number(State.pageData?.page?.document_epoch || 0),
    ...(item ? { expected_row_version: Number(item.row_version || 0) } : {}),
    operation_id: newOperationId(),
  };
}

function renderComments() {
  const root = $("#commentList");
  if (!root) return;
  const role = State.pageData?.page?.role || "view";
  const canComment = ["comment", "edit", "owner"].includes(role);
  const personSelect = $("#commentPersonFilterSelect");
  const authors = [...new Set(State.comments.map(item => item.author_user_id).filter(Boolean))].sort();
  personSelect.innerHTML = `<option value="">모든 사람</option>${authors.map(author => `<option value="${escapeAttr(author)}" ${State.commentPersonFilter === author ? "selected" : ""}>${escapeHtml(author)}</option>`).join("")}`;
  const roots = State.comments.filter(item => !item.parent_id).filter(item => {
    if (State.commentBlockId && item.block_id !== State.commentBlockId) return false;
    if (State.commentPersonFilter && item.author_user_id !== State.commentPersonFilter
      && !State.comments.some(reply => reply.parent_id === item.id && reply.author_user_id === State.commentPersonFilter)) return false;
    if (State.commentFilter === "open") return !item.resolved;
    if (State.commentFilter === "resolved") return Boolean(item.resolved);
    return true;
  });
  const commentHtml = (item, isReply = false) => {
    const canManage = item.author_user_id === State.user?.id || State.pageData?.page?.role === "owner";
    const reactions = item.reactions && typeof item.reactions === "object" ? item.reactions : {};
    const emojiChoices = Object.keys(reactions).filter(emoji => (
      Array.isArray(reactions[emoji]) && reactions[emoji].length
    ));
    return `<article class="comment-item${item.resolved ? " resolved" : ""}${isReply ? " comment-reply" : ""}">
        <div class="comment-meta"><strong>${escapeHtml(item.author_user_id || "")}</strong><small>${escapeHtml(formatTime(item.created_at))}</small></div>
        ${!isReply && item.anchor?.text ? `<blockquote class="comment-anchor">${escapeHtml(item.anchor.text)}</blockquote>` : ""}
        <p>${escapeHtml(item.content || "").replace(/\n/g, "<br>")}</p>
        <div class="comment-reactions">${emojiChoices.map(emoji => {
          const users = Array.isArray(reactions[emoji]) ? reactions[emoji] : [];
          const reacted = users.includes(State.user?.id);
          return `<button type="button" data-comment-reaction="${escapeAttr(item.id)}" data-reaction-emoji="${escapeAttr(emoji)}" class="${reacted ? "active" : ""}" title="${escapeAttr(users.join(", "))}" ${canComment ? "" : "disabled"}>${escapeHtml(emoji)}${users.length ? ` ${users.length}` : ""}</button>`;
        }).join("")}${canComment && !isReply ? `<button type="button" data-comment-reaction="${escapeAttr(item.id)}" data-reaction-emoji="🥳" class="comment-reaction-add" title="반응 추가">☺</button>` : ""}</div>
        <div class="comment-actions">
          ${canComment && !isReply ? `<button type="button" data-comment-reply="${escapeAttr(item.id)}">답글${item.reply_count ? ` ${item.reply_count}` : ""}</button>` : ""}
          ${canManage ? `<button type="button" data-comment-edit="${escapeAttr(item.id)}">편집</button>${!isReply ? `<button type="button" data-comment-resolve="${escapeAttr(item.id)}">${item.resolved ? "다시 열기" : "해결"}</button>` : ""}<button type="button" data-comment-delete="${escapeAttr(item.id)}">삭제</button>` : ""}
        </div>
      </article>`;
  };
  root.innerHTML = roots.length ? roots.map(item => {
    const replies = State.comments.filter(reply => reply.parent_id === item.id);
    return `<section class="comment-thread" role="group" aria-label="댓글 스레드" tabindex="-1" data-comment-thread="${escapeAttr(item.id)}">${commentHtml(item)}${replies.length ? `<div class="comment-replies">${replies.map(reply => commentHtml(reply, true)).join("")}</div>` : ""}</section>`;
  }).join("") : '<div class="mini-row"><span>댓글 없음</span></div>';
  root.querySelectorAll("[data-comment-edit]").forEach(button => button.addEventListener("click", () => editComment(button.dataset.commentEdit)));
  root.querySelectorAll("[data-comment-resolve]").forEach(button => button.addEventListener("click", () => toggleCommentResolved(button.dataset.commentResolve)));
  root.querySelectorAll("[data-comment-delete]").forEach(button => button.addEventListener("click", () => deleteComment(button.dataset.commentDelete)));
  root.querySelectorAll("[data-comment-reply]").forEach(button => button.addEventListener("click", () => setCommentReply(button.dataset.commentReply)));
  root.querySelectorAll("[data-comment-reaction]").forEach(button => button.addEventListener("click", () => toggleCommentReaction(button.dataset.commentReaction, button.dataset.reactionEmoji)));
  renderPageCommentPreview();
  $("#commentInput").disabled = !canComment;
  $("#commentSubmitBtn").disabled = !canComment;
}

function renderPageCommentPreview() {
  const preview = $("#pageCommentPreview");
  if (!preview) return;
  const page = State.pageData?.page;
  if (String(page?.iam_role || "") !== "comment") {
    preview.innerHTML = "";
    return;
  }
  const root = State.comments.find(item => !item.parent_id && !item.resolved);
  if (!root) {
    preview.innerHTML = "";
    return;
  }
  const replies = State.comments.filter(item => item.parent_id === root.id);
  const reactionEntries = Object.entries(
    root.reactions && typeof root.reactions === "object" ? root.reactions : {},
  ).filter(([, users]) => Array.isArray(users) && users.length);
  const previewContentHtml = item => {
    const content = String(item.content || "");
    const mention = content.match(/^(.*?)(\s+@\S+)\s*$/);
    return mention
      ? `${escapeHtml(mention[1])}<span class="page-comment-preview-mention">${escapeHtml(mention[2])}</span>`
      : escapeHtml(content);
  };
  const personHtml = (item, reply = false) => `
    <article class="page-comment-preview-item${reply ? " reply" : ""}">
      <span class="page-comment-preview-avatar" aria-hidden="true">${escapeHtml(String(item.author_user_id || "").slice(0, 1))}</span>
      <strong>${escapeHtml(item.author_user_id || "")}</strong>
      <p>${previewContentHtml(item).replace(/\n/g, "<br>")}</p>
    </article>`;
  preview.innerHTML = `${personHtml(root)}
    <div class="page-comment-preview-reactions">
      ${reactionEntries.map(([emoji, users]) => `<button type="button" data-page-comment-reaction="${escapeAttr(root.id)}" data-reaction-emoji="${escapeAttr(emoji)}" class="${users.includes(State.user?.id) ? "active" : ""}">${escapeHtml(emoji)} ${users.length}</button>`).join("")}
      <button type="button" class="page-comment-preview-reaction-add" aria-label="반응 추가">☺</button>
    </div>
    ${replies.map(reply => personHtml(reply, true)).join("")}`;
  preview.querySelectorAll("[data-page-comment-reaction]").forEach(button => {
    button.addEventListener("click", () => toggleCommentReaction(
      button.dataset.pageCommentReaction,
      button.dataset.reactionEmoji,
    ));
  });
}

function focusCommentResolveControl(commentId) {
  if (!commentId) return;
  setTimeout(() => {
    const thread = [...document.querySelectorAll("[data-comment-thread]")]
      .find(candidate => candidate.dataset.commentThread === commentId);
    thread?.querySelector("[data-comment-resolve]")?.focus({ preventScroll: true });
  }, 0);
}

function setCommentReply(parentId = "") {
  State.commentReplyParentId = parentId || "";
  const item = State.comments.find(comment => comment.id === State.commentReplyParentId);
  const context = $("#commentReplyContext");
  context.hidden = !item;
  context.querySelector("span").textContent = item ? `${item.author_user_id || "사용자"}에게 답글` : "";
  $("#commentInput").placeholder = item ? "답글을 입력하세요" : "댓글을 입력하세요";
  if (item) $("#commentInput").focus();
}

async function submitComment() {
  const input = $("#commentInput");
  const content = input?.value.trim();
  if (!content || !State.activePageId) return;
  try {
    const collaborativeBase = collaborativeCommentBasePath();
    let data;
    if (collaborativeBase) {
      data = await api(collaborativeBase, {
        method: "POST",
        body: {
          comment_id: `comment_${crypto.randomUUID().replaceAll("-", "")}`,
          content,
          block_id: State.commentBlockId || null,
          anchor: State.commentSelection || {},
          parent_comment_id: State.commentReplyParentId || null,
          mention_subject_ids: commentMentionSubjectIds(content),
          ...collaborativeCommentCommand(),
        },
      });
    } else {
      data = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/comments`, {
        method: "POST",
        body: { content, block_id: State.commentBlockId, anchor: State.commentSelection || {}, parent_id: State.commentReplyParentId },
      });
    }
    const threadId = data.comment?.parent_id || data.comment?.id || State.commentReplyParentId;
    input.value = "";
    State.commentSelection = null;
    setCommentReply("");
    await loadComments();
    setTimeout(() => input.focus({ preventScroll: true }), 0);
    setStatus("댓글 추가됨", "ok");
  } catch (error) {
    setStatus("댓글 추가 실패: " + error.message, "error");
  }
}

async function toggleCommentReaction(commentId, emoji) {
  if (!commentId || !emoji) return;
  try {
    const item = State.comments.find(comment => comment.id === commentId);
    const collaborativeBase = collaborativeCommentBasePath();
    await api(
      collaborativeBase
        ? `${collaborativeBase}/${encodeURIComponent(commentId)}/reactions`
        : `/api/notion/pages/${encodeURIComponent(State.activePageId)}/comments/${encodeURIComponent(commentId)}/reactions`,
      {
        method: "POST",
        body: collaborativeBase
          ? { emoji, ...collaborativeCommentCommand(item) }
          : { emoji },
      },
    );
    await loadComments();
  } catch (error) {
    setStatus("댓글 반응 변경 실패: " + error.message, "error");
  }
}

async function toggleCommentResolved(commentId) {
  const item = State.comments.find(comment => comment.id === commentId);
  if (!item) return;
  const nextResolved = !item.resolved;
  try {
    const collaborativeBase = collaborativeCommentBasePath();
    if (collaborativeBase) {
      await api(`${collaborativeBase}/${encodeURIComponent(commentId)}`, {
        method: "PATCH",
        body: { resolved: nextResolved, ...collaborativeCommentCommand(item) },
      });
    } else {
      await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/comments/${encodeURIComponent(commentId)}`, {
        method: "PATCH",
        body: { resolved: nextResolved },
      });
    }
    await loadComments();
    if (nextResolved && State.commentFilter === "open") {
      document.activeElement?.blur?.();
      return;
    }
    focusCommentResolveControl(commentId);
  } catch (error) {
    setStatus("댓글 변경 실패: " + error.message, "error");
  }
}

async function editComment(commentId) {
  const item = State.comments.find(comment => comment.id === commentId);
  if (!item) return;
  const content = window.prompt("댓글 편집", item.content || "");
  if (content == null || !content.trim() || content.trim() === item.content) return;
  try {
    const collaborativeBase = collaborativeCommentBasePath();
    await api(collaborativeBase
      ? `${collaborativeBase}/${encodeURIComponent(commentId)}`
      : `/api/notion/pages/${encodeURIComponent(State.activePageId)}/comments/${encodeURIComponent(commentId)}`, {
      method: "PATCH",
      body: collaborativeBase
        ? {
          content: content.trim(),
          mention_subject_ids: commentMentionSubjectIds(content),
          ...collaborativeCommentCommand(item),
        }
        : { content: content.trim() },
    });
    await loadComments();
    setStatus("댓글을 편집했습니다.", "ok");
  } catch (error) {
    setStatus("댓글 편집 실패: " + error.message, "error");
  }
}

async function deleteComment(commentId) {
  if (!window.confirm("댓글을 삭제할까요?")) return;
  try {
    const item = State.comments.find(comment => comment.id === commentId);
    const collaborativeBase = collaborativeCommentBasePath();
    await api(collaborativeBase
      ? `${collaborativeBase}/${encodeURIComponent(commentId)}`
      : `/api/notion/pages/${encodeURIComponent(State.activePageId)}/comments/${encodeURIComponent(commentId)}`, {
      method: "DELETE",
      ...(collaborativeBase ? { body: collaborativeCommentCommand(item) } : {}),
    });
    await loadComments();
  } catch (error) {
    setStatus("댓글 삭제 실패: " + error.message, "error");
  }
}

async function loadPageHistory() {
  if (!State.activePageId) return;
  const root = $("#historyList");
  if (State.navigation.collaborativeHistory && State.navigation.workspaceId) {
    root.innerHTML = '<div class="mini-row"><span>불러오는 중...</span></div>';
    try {
      const base = `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/pages/${encodeURIComponent(State.activePageId)}/history`;
      const data = await api(`${base}?limit=100`);
      State.versions = data.items || [];
      root.innerHTML = State.versions.length ? State.versions.map(item => `
        <div class="history-item">
          <div><strong>${escapeHtml(`버전 ${item.revision}`)}</strong><small>${escapeHtml(formatTime(item.created_at))} · ${formatBytes(item.snapshot_size || 0)}${item.publication_revision_id ? " · 게시됨" : ""}</small></div>
          <button type="button" data-version-restore="${escapeAttr(item.revision)}" ${item.revision === data.current_revision ? "disabled" : ""}>복원</button>
        </div>
      `).join("") : '<div class="mini-row"><span>저장된 버전 없음</span></div>';
      root.querySelectorAll("[data-version-restore]").forEach(button => button.addEventListener("click", () => restorePageVersion(button.dataset.versionRestore)));
    } catch (error) {
      root.innerHTML = `<div class="mini-row"><span>${escapeHtml(error.message)}</span></div>`;
    }
    return;
  }
  root.innerHTML = '<div class="mini-row"><span>불러오는 중...</span></div>';
  try {
    const data = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/versions`);
    State.versions = data.items || [];
    root.innerHTML = State.versions.length ? State.versions.map((item, index) => `
      <div class="history-item">
        <div><strong>${escapeHtml(item.title || "제목 없음")}</strong><small>${escapeHtml(formatTime(item.created_at))} · ${formatBytes(item.snapshot_size || 0)}</small></div>
        <button type="button" data-version-restore="${escapeAttr(item.id)}" ${index === 0 ? "disabled" : ""}>복원</button>
      </div>
    `).join("") : '<div class="mini-row"><span>저장된 버전 없음</span></div>';
    root.querySelectorAll("[data-version-restore]").forEach(button => button.addEventListener("click", () => restorePageVersion(button.dataset.versionRestore)));
  } catch (error) {
    root.innerHTML = `<div class="mini-row"><span>${escapeHtml(error.message)}</span></div>`;
  }
}

async function restorePageVersion(versionId) {
  if (State.navigation.collaborativeHistory && State.navigation.workspaceId) {
    if (!versionId || !window.confirm("이 버전으로 페이지를 복원할까요? 현재 내용도 기록에 유지됩니다.")) return;
    try {
      const pageId = State.activePageId;
      if (!(await flushAllOpenDrafts())) {
        setStatus("저장되지 않은 편집본이 있어 버전을 복원하지 않았습니다.", "error");
        return;
      }
      const page = State.pageData?.page || {};
      const base = `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/pages/${encodeURIComponent(pageId)}/history`;
      await api(`${base}/revisions/${encodeURIComponent(versionId)}/restore`, {
        method: "POST",
        body: {
          expected_revision: Number(State.serverRevision || page.revision),
          expected_document_sequence: Number(page.document_sequence),
          expected_workspace_epoch: Number(page.workspace_epoch),
          expected_document_epoch: Number(page.document_epoch),
          operation_id: newOperationId(),
        },
      });
      stopLiveCollaboration();
      await openPage(pageId, false, PageFocusPolicy.PRESERVE);
      await Promise.all([loadPages(), loadPageHistory()]);
      setStatus("페이지 버전을 복원했습니다.", "ok");
    } catch (error) {
      setStatus("버전 복원 실패: " + error.message, "error");
    }
    return;
  }
  if (!versionId || !window.confirm("이 버전으로 페이지를 복원할까요? 현재 내용도 기록에 남습니다.")) return;
  try {
    const pageId = State.activePageId;
    if (!(await flushAllOpenDrafts())) {
      setStatus("저장되지 않은 편집본이 있어 버전을 복원하지 않았습니다.", "error");
      return;
    }
    const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/versions/${encodeURIComponent(versionId)}/restore`, { method: "POST", body: {} });
    await refreshOpenPagesAfterMutation({ payloadByPageId: { [pageId]: data } });
    await Promise.all([loadPages(), loadPageHistory()]);
    setStatus("페이지 버전 복원됨", "ok");
  } catch (error) {
    setStatus("버전 복원 실패: " + error.message, "error");
  }
}

async function duplicatePage(pageId) {
  try {
    if (!(await flushPageDraft(pageId))) {
      setStatus("저장되지 않은 편집본이 있어 페이지를 복제하지 않았습니다.", "error");
      return;
    }
    let data;
    if (State.navigation.enabled && State.navigation.workspaceId) {
      const intent = navigationPageLifecycleIntent("duplicate", pageId);
      try {
        data = await api(
          `/api/notion/workspaces/${encodeURIComponent(intent.workspaceId)}/pages/${encodeURIComponent(pageId)}/duplicate`,
          {
            method: "POST",
            body: {
              base_version: intent.baseVersion,
              operation_id: intent.operationId,
            },
          },
        );
      } catch (error) {
        if (
          error?.status === 409
          && Number.isInteger(error?.data?.current_version)
          && error.data.current_version >= 1
        ) intent.baseVersion = error.data.current_version;
        throw error;
      }
      State.navigation.pendingPageLifecycle = null;
      applyNavigationWorkspaceResult(data.workspace);
    } else {
      data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/duplicate`, { method: "POST", body: {} });
    }
    await loadPages();
    if (data.page?.id) {
      await openPage(data.page.id);
      focusPageMenuAfterLifecycle();
    }
    setStatus("페이지를 복제했습니다.", "ok");
  } catch (e) {
    setStatus("복제 실패: " + e.message, "error");
  }
}

async function renamePage(page) {
  const title = window.prompt("새 페이지 이름", page.title || "제목 없음");
  if (title === null) return;
  const next = title.trim();
  if (!next) return;
  try {
    if (!(await flushPageDraft(page.id))) {
      setStatus("저장되지 않은 편집본이 있어 이름을 변경하지 않았습니다.", "error");
      return;
    }
    const data = await api(`/api/notion/pages/${encodeURIComponent(page.id)}`, { method: "PATCH", body: { title: next } });
    if (State.activePageId === page.id) applySaveMetadata(State, data);
    if (State.sidePeek.pageId === page.id) applySaveMetadata(State.sidePeek, data);
    await loadPages();
    if (State.activePageId === page.id) await openPage(page.id, false);
    if (State.sidePeek.pageId === page.id) await openSidePeek(page.id);
    setStatus("이름을 변경했습니다.", "ok");
  } catch (e) {
    setStatus("이름 변경 실패: " + e.message, "error");
  }
}

function openMovePageDialog(page) {
  const blocked = descendantPageIds(page.id);
  const candidates = ownPages().filter(item => item.id !== page.id && !blocked.has(item.id));
  const menu = $("#movePageMenu");
  const anchor = $("#pageMoreBtn");
  if (!menu || !anchor) return;
  closeNewPageMenu();
  const rect = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - 344, rect.right - 336))}px`;
  menu.style.top = `${Math.max(8, Math.min(window.innerHeight - 430, rect.bottom + 4))}px`;
  menu.innerHTML = `
    <input id="movePageSearch" class="menu-search" type="search" placeholder="페이지 옮길 곳 선택" aria-label="페이지 옮길 곳 선택">
    <div id="movePageResults" role="listbox" aria-label="이동 대상"></div>
  `;
  const input = $("#movePageSearch");
  const results = $("#movePageResults");
  const renderResults = query => {
    const normalized = String(query || "").trim().toLowerCase();
    const visible = candidates.filter(item => !normalized || String(item.title || "제목 없음").toLowerCase().includes(normalized));
    const topLevelVisible = !normalized || "개인 페이지".includes(normalized);
    results.innerHTML = `${topLevelVisible ? '<button type="button" class="menu-item" role="option" data-move-parent=""><span>⌂</span><span>개인 페이지</span><small></small></button>' : ""}${visible.map(item => `<button type="button" class="menu-item" role="option" data-move-parent="${escapeAttr(item.id)}"><span>${pageIconHtml(item)}</span><span>${escapeHtml(item.title || "제목 없음")}</span><small></small></button>`).join("") || (!topLevelVisible ? '<div class="menu-empty">일치하는 페이지가 없습니다.</div>' : "")}`;
    results.querySelectorAll("[data-move-parent]").forEach(option => {
      option.addEventListener("click", async () => {
        const parentId = option.dataset.moveParent || "";
        try {
          await movePageSafely(page.id, { parent_id: parentId });
          closeMovePageMenu();
          await loadPages();
          $("#pageMoreBtn")?.focus({ preventScroll: true });
          setStatus("페이지를 옮겼습니다.", "ok");
        } catch (e) {
          setStatus("페이지 이동 실패: " + e.message, "error");
        }
      });
    });
  };
  input?.addEventListener("input", event => renderResults(event.target.value));
  input?.addEventListener("keydown", event => {
    if (event.key !== "Escape") return;
    event.preventDefault();
    closeMovePageMenu(true);
  });
  renderResults("");
  menu.hidden = false;
  input?.focus({ preventScroll: true });
}

async function performArchivePage(pageId) {
  const affectedPageIds = new Set([pageId, ...descendantPageIds(pageId)]);
  const previouslyOpenPageIds = openPageIds();
  if (!(await flushAllOpenDrafts())) {
    throw new Error("저장되지 않은 편집본이 있어 페이지를 휴지통으로 이동하지 않았습니다.");
  }
  if (State.navigation.enabled && State.navigation.workspaceId) {
    const intent = navigationPageLifecycleIntent("trash", pageId);
    try {
      const data = await api(
        `/api/notion/workspaces/${encodeURIComponent(intent.workspaceId)}/pages/${encodeURIComponent(pageId)}/trash`,
        {
          method: "POST",
          body: {
            base_version: intent.baseVersion,
            operation_id: intent.operationId,
          },
        },
      );
      State.navigation.pendingPageLifecycle = null;
      applyNavigationWorkspaceResult(data.workspace);
    } catch (error) {
      if (
        error?.status === 409
        && Number.isInteger(error?.data?.current_version)
        && error.data.current_version >= 1
      ) intent.baseVersion = error.data.current_version;
      throw error;
    }
  } else {
    await api(`/api/notion/pages/${encodeURIComponent(pageId)}`, { method: "DELETE" });
  }
  for (const openPageId of previouslyOpenPageIds) {
    if (affectedPageIds.has(openPageId)) continue;
    const refreshResult = await refreshOpenedPageAfterMutation(openPageId);
    if (refreshResult.failed && refreshResult.error?.status === 404) affectedPageIds.add(openPageId);
  }
  if (affectedPageIds.has(State.activePageId)) {
      State.activePageId = "";
      State.pageData = null;
      State.serverRevision = null;
      State.atomicSaveEnabled = false;
      State.saveRetry = null;
      State.saveConflict = null;
      State.dirty = false;
      history.pushState({ home: true }, "", "/workspace");
      renderEditor();
  }
  if (affectedPageIds.has(State.sidePeek.pageId)) await closeSidePeek();
  return affectedPageIds;
}

async function archivePageById(pageId) {
  if (!window.confirm("이 페이지를 휴지통으로 이동할까요?")) return;
  try {
    await performArchivePage(pageId);
    await loadPages();
    focusPageMenuAfterLifecycle({ transientHome: true });
    setStatus("휴지통으로 이동했습니다.", "ok");
  } catch (e) {
    setStatus("휴지통 이동 실패: " + e.message, "error");
  }
}

function setActiveNav(mode) {
  document.querySelectorAll(".icon-nav").forEach(btn => btn.classList.remove("active"));
  const homeNav = $("#homeNavBtn");
  homeNav?.setAttribute("aria-selected", mode === "home" ? "true" : "false");
  if (mode === "home") homeNav?.classList.add("active");
  if (mode === "ai") $("#notionAiNavBtn")?.classList.add("active");
  if (mode === "search") $("#sidebarSearchBtn")?.classList.add("active");
}

function applyAdoptedSurfacePolicy() {
  const publicSection = $("#publicShareSection");
  if (!publicSection) return;
  const hidden = State.navigation.authoritative === true;
  publicSection.hidden = hidden;
  publicSection.setAttribute("aria-hidden", hidden ? "true" : "false");
}

function switchSidebarMode(mode = "pages") {
  if (mode === "agent") mode = "pages";
  State.sidebarMode = mode;
  $("#sidebarPagePanel").hidden = mode !== "pages";
  $("#sidebarAgentPanel").hidden = mode !== "agent";
  setActiveNav(mode === "agent" ? "ai" : (State.activePageId ? "" : "home"));
}

function switchWorkspaceOwner(owner = "") {
  State.workspaceOwnerFilter = owner;
  const label = State.user?.display_name || State.user?.id || "admin";
  $("#workspaceTitle").textContent = owner ? `${owner}의 Notion` : `${label}의 Notion`;
  closeWorkspacePopover();
  renderPageLists();
  renderWorkspacePopover();
  renderBreadcrumbPopover();
  if (!State.activePageId) renderHome();
}

async function switchCanonicalWorkspace(workspaceId) {
  const targetId = String(workspaceId || "");
  if (
    !State.navigation.enabled
    || !targetId
    || targetId === State.navigation.workspaceId
  ) {
    closeWorkspacePopover();
    return;
  }
  if (!(await window.NotionAiNotes.beforeNavigate())) return;
  if (!(await flushPendingNavigationPreferences())) {
    setStatus("사이드바 설정을 저장하지 못해 워크스페이스를 전환하지 않았습니다.", "error");
    return;
  }
  if (navigationControlsLocked()) {
    setStatus("탐색 설정 동기화가 끝난 뒤 워크스페이스를 전환하세요.", "error");
    return;
  }
  if (!(await refreshWorkspaceNavigationCapability())) return;
  if (!State.navigation.workspaces.some(item => item.id === targetId)) return;
  if (!(await flushAllOpenDrafts())) {
    setStatus("저장되지 않은 편집이 있어 워크스페이스를 전환하지 않았습니다.", "error");
    return;
  }
  await closeSidePeek();
  closeWorkspacePopover();
  const previousWorkspaceId = State.navigation.workspaceId;
  const previousSnapshot = cloneNavigationSnapshot();
  const previousPages = State.pages;
  const firstVisit = !State.navigation.visitedWorkspaceIds.has(targetId);
  let switched = false;
  State.navigation.sequence += 1;
  State.navigation.workspaceId = targetId;
  State.workspaceAccess = null;
  State.shareAccess = null;
  State.pages = [];
  State.searchRequestSequence += 1;
  State.searchItems = [];
  State.activeSearchPageId = "";
  renderSearchResults();
  renderSearchPreview();
  await goHome(false);
  if (location.pathname.startsWith("/page/")) {
    history.replaceState({}, "", "/workspace");
  }
  setNavigationPending(true, "워크스페이스 탐색 설정 불러오는 중");
  try {
    await loadWorkspaceNavigationSnapshot(targetId);
    if (!(await loadWorkspacePagesSnapshot(targetId))) {
      throw new Error("workspace page snapshot was superseded");
    }
    try { localStorage.setItem(navigationSelectionKey(), targetId); } catch {}
    State.navigation.visitedWorkspaceIds.add(targetId);
    switched = true;
  } catch (error) {
    State.navigation.sequence += 1;
    State.navigation.workspaceId = previousWorkspaceId;
    State.pages = previousPages;
    if (previousSnapshot) applyWorkspaceNavigationSnapshot(previousSnapshot);
    renderPageLists();
    renderEditor();
    setStatus("워크스페이스 전환 실패: " + error.message, "error");
  } finally {
    setNavigationPending(false);
    requestAnimationFrame(() => {
      const focusTarget = switched && firstVisit
        ? $("#workspaceSidebarGroup")
        : $("#workspaceName");
      focusTarget?.focus({ preventScroll: true });
    });
  }
}

async function toggleWorkspaceFavorite(page) {
  if (!State.navigation.enabled || !State.navigation.snapshot || !page?.id) return false;
  const locator = pageSourceLocator(page);
  if (!locator.sourceOwnerSubjectId || !locator.sourcePageId) return false;
  const key = navigationFavoriteKey(locator.sourceOwnerSubjectId, locator.sourcePageId);
  const removing = State.navigation.favoriteKeys.has(key);
  const workspaceId = State.navigation.workspaceId;
  const expectedVersion = State.navigation.snapshot.favorite_version;
  const operationId = navigationOperationId(removing ? "favorite-remove" : "favorite-add");
  const path = `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/navigation/favorites`;
  const body = {
    source_owner_subject_id: locator.sourceOwnerSubjectId,
    source_page_id: locator.sourcePageId,
    expected_favorite_version: expectedVersion,
    operation_id: operationId,
  };
  return runNavigationMutation({
    optimistic: () => {
      if (removing) {
        State.navigation.favoriteKeys.delete(key);
        State.navigation.favoritePositions.delete(key);
        State.navigation.snapshot.favorites = State.navigation.snapshot.favorites
          .filter(item => navigationFavoriteKey(
            item.source_owner_subject_id,
            item.source_page_id,
          ) !== key)
          .map((item, position) => ({ ...item, position }));
      } else {
        const position = State.navigation.snapshot.favorites.length;
        State.navigation.favoriteKeys.add(key);
        State.navigation.favoritePositions.set(key, position);
        State.navigation.snapshot.favorites.push({
          source_owner_subject_id: locator.sourceOwnerSubjectId,
          source_page_id: locator.sourcePageId,
          position,
        });
      }
    },
    request: () => requestNavigationMutation(path, {
      method: removing ? "DELETE" : "PUT",
      body,
    }),
    successMessage: removing
      ? "즐겨찾기에서 제거했습니다."
      : "즐겨찾기에 추가했습니다.",
  });
}

function navigationSettingsMarkup(sections) {
  const ordered = [...sections].sort((left, right) => left.display_order - right.display_order);
  return `
    <p class="panel-help">섹션 표시, 접힘, 순서를 현재 워크스페이스에 저장합니다.</p>
    <div class="navigation-settings-list" role="list">
      ${ordered.map((item, index) => `
        <div class="navigation-settings-row" role="listitem" tabindex="-1" data-navigation-settings-row="${item.section}">
          <strong>${escapeHtml(NAVIGATION_SECTION_LABELS[item.section])}</strong>
          <label><input type="checkbox" data-navigation-setting="hidden" data-section="${item.section}" ${item.hidden ? "checked" : ""}> 숨김</label>
          <label><input type="checkbox" data-navigation-setting="collapsed" data-section="${item.section}" ${item.collapsed ? "checked" : ""}> 접힘</label>
          <label>개수 <input type="number" min="1" max="100" value="${item.item_limit}" data-navigation-setting="item_limit" data-section="${item.section}"></label>
          <span class="navigation-order-controls">
            <button type="button" data-navigation-move="-1" data-section="${item.section}" aria-label="${escapeHtml(NAVIGATION_SECTION_LABELS[item.section])} 위로 이동" ${index === 0 ? "disabled" : ""}>↑</button>
            <button type="button" data-navigation-move="1" data-section="${item.section}" aria-label="${escapeHtml(NAVIGATION_SECTION_LABELS[item.section])} 아래로 이동" ${index === ordered.length - 1 ? "disabled" : ""}>↓</button>
          </span>
        </div>
      `).join("")}
    </div>
  `;
}

function renderNavigationSettings(focusSelector = "") {
  if (!State.navigation.settingsDraft) return;
  $("#modalBody").innerHTML = navigationSettingsMarkup(State.navigation.settingsDraft);
  document.querySelectorAll("#modalBody [data-navigation-setting]").forEach(input => {
    input.addEventListener("change", () => {
      const item = navigationSection(input.dataset.section, State.navigation.settingsDraft);
      if (!item) return;
      if (input.dataset.navigationSetting === "item_limit") {
        item.item_limit = Math.max(1, Math.min(100, Math.trunc(Number(input.value)) || 10));
      } else {
        item[input.dataset.navigationSetting] = Boolean(input.checked);
      }
    });
  });
  document.querySelectorAll("#modalBody [data-navigation-move]").forEach(button => {
    button.addEventListener("click", () => {
      const section = button.dataset.section;
      const direction = Number(button.dataset.navigationMove);
      const ordered = [...State.navigation.settingsDraft].sort(
        (left, right) => left.display_order - right.display_order,
      );
      const index = ordered.findIndex(item => item.section === section);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= ordered.length) return;
      [ordered[index], ordered[target]] = [ordered[target], ordered[index]];
      ordered.forEach((item, displayOrder) => { item.display_order = displayOrder; });
      State.navigation.settingsDraft = ordered;
      renderNavigationSettings(`[data-navigation-settings-row="${section}"]`);
    });
  });
  if (focusSelector) requestAnimationFrame(() => $(focusSelector)?.focus());
}

function openNavigationSettings() {
  if (!State.navigation.enabled || navigationControlsLocked()) return;
  closeWorkspacePopover();
  State.navigation.settingsDraft = State.navigation.sections.map(item => ({ ...item }));
  openModal("사이드바 설정", navigationSettingsMarkup(State.navigation.settingsDraft), [
    { label: "취소", action: closeModal },
    {
      label: "저장",
      primary: true,
      action: async () => {
        State.navigation.sections = State.navigation.settingsDraft.map(item => ({ ...item }));
        State.navigation.settingsDraft = null;
        closeModal();
        applyNavigationSections();
        await persistNavigationPreferences();
      },
    },
  ]);
  renderNavigationSettings();
}

function openWorkspacePopover() {
  State.workspacePopoverOpen = true;
  const root = $("#workspaceName");
  root?.classList.remove("suppress-hover");
  root?.classList.add("open");
}

function closeWorkspacePopover() {
  State.workspacePopoverOpen = false;
  const root = $("#workspaceName");
  root?.classList.remove("open");
  root?.classList.add("suppress-hover");
}

function toggleWorkspacePopover() {
  if (State.workspacePopoverOpen) closeWorkspacePopover();
  else openWorkspacePopover();
}

function handleWorkspaceAction(action) {
  if (action === "logout") {
    logout();
    return;
  }
  closeWorkspacePopover();
  if (action === "theme") {
    toggleColorTheme();
    return;
  }
  if (action === "people") {
    openShareSurface("workspace-members");
    return;
  }
  if (action === "settings") {
    openHelpOverlay();
    return;
  }
  if (action === "account") {
    openModal("계정 추가", "<p>다른 계정으로 접근하려면 로그아웃 후 해당 계정으로 로그인하세요.</p>", [{ label: "확인", primary: true, action: closeModal }]);
    return;
  }
}

function outlookSettingsFormValue(id, fallback = "") {
  const element = $(id);
  return element ? element.value : fallback;
}

function outlookSettingsBody(enabledOverride = null) {
  const enabled = enabledOverride === null ? Boolean(State.outlook?.enabled) : Boolean(enabledOverride);
  return {
    enabled,
    connector: outlookSettingsFormValue("#outlookConnector", State.outlook?.connector || "microsoft_graph"),
    sync_interval_seconds: Number(outlookSettingsFormValue("#outlookSyncInterval", "60")) || 60,
    daily_digest_enabled: Boolean($("#outlookDigestEnabled")?.checked),
    daily_digest_time: outlookSettingsFormValue("#outlookDigestTime", "09:00") || "09:00",
    browser_notifications: Boolean($("#outlookBrowserNotifications")?.checked),
  };
}

function outlookConnectionLabel(data = State.outlook) {
  const connector = data?.connector_status || {};
  if (data?.connector === "microsoft_graph" && !connector.configuration?.configured) return "서버 설정 필요";
  if (data?.enabled && data?.connected) return "연결됨";
  if (data?.connector === "microsoft_graph" && connector.authenticated) return "로그인됨 · 동기화 꺼짐";
  if (data?.enabled) return "연결 확인 필요";
  return "꺼짐";
}

function renderOutlookSettingsModal(data = State.outlook, message = "") {
  const status = data || {};
  const settings = status.settings || {};
  const connector = status.connector_status || {};
  const connectorType = status.connector || settings.connector || "microsoft_graph";
  const isGraph = connectorType === "microsoft_graph";
  const graphConfiguration = connector.configuration || {};
  const account = connector.account || {};
  const available = Boolean(connector.available);
  const enabled = Boolean(status.enabled);
  const lastSync = status.last_sync_at ? formatTime(status.last_sync_at) : "아직 동기화하지 않음";
  const lastDigest = status.last_digest_at ? formatTime(status.last_digest_at) : "아직 브리핑 없음";
  const permission = typeof Notification === "undefined" ? "지원 안 함" : Notification.permission;
  const connectorTitle = isGraph ? "Microsoft 365 Outlook" : "이 서버의 Classic Outlook";
  const connectorIdentity = isGraph
    ? (account.email || account.display_name || (graphConfiguration.configured ? "회사 계정 로그인 필요" : "Entra 앱 설정 필요"))
    : (connector.profile_name || "Outlook 프로필");
  const connectorError = status.last_error || connector.error || "";
  const missingConfiguration = isGraph && !graphConfiguration.configured
    ? `<div class="outlook-settings-error"><strong>Graph 설정이 필요합니다.</strong><small>${escapeHtml((graphConfiguration.missing || []).join(" · "))}</small></div>`
    : "";
  openModal("Microsoft Outlook 연결", `
    <div class="outlook-settings">
      <div class="outlook-connection-head ${enabled && available ? "connected" : ""}">
        <span class="outlook-logo">O</span>
        <span><strong>${escapeHtml(connectorTitle)}</strong><small>${escapeHtml(outlookConnectionLabel(status))} · ${escapeHtml(connectorIdentity)}</small></span>
        <span class="outlook-state-dot" aria-hidden="true"></span>
      </div>
      ${message ? `<div class="outlook-settings-message">${escapeHtml(message)}</div>` : ""}
      ${connectorError ? `<div class="outlook-settings-error">${escapeHtml(connectorError)}</div>` : ""}
      ${missingConfiguration}
      <p class="outlook-settings-help">${isGraph
        ? "회사 Microsoft 계정으로 직접 로그인해 본인의 받은편지함만 읽기 전용으로 연결합니다. Mail.Read 위임 권한만 사용하며 메일을 수정하거나 발송하지 않습니다."
        : "웹서버 Windows에 로그인된 Classic Outlook 받은편지함을 읽기 전용으로 동기화합니다. 원격 사용자의 PC Outlook에는 접근하지 않습니다."}</p>
      ${isGraph ? '<div class="outlook-security-note"><strong>Single-tenant</strong><span>등록된 회사 Tenant 계정만 로그인 가능 · 토큰은 사용자별 암호화 저장</span></div>' : ""}
      <div class="outlook-stats">
        <div><small>받은편지함</small><strong>${Number(connector.inbox_count || 0).toLocaleString()}</strong></div>
        <div><small>미열람</small><strong>${Number(connector.unread_count || 0).toLocaleString()}</strong></div>
        <div><small>저장된 메일</small><strong>${Number(status.processed_count || 0).toLocaleString()}</strong></div>
      </div>
      <div class="outlook-setting-grid">
        <label><span>연결 방식</span><select id="outlookConnector" ${enabled ? "disabled" : ""}>
          <option value="microsoft_graph" ${isGraph ? "selected" : ""}>Microsoft Graph · 회사 계정 (권장)</option>
          <option value="classic_outlook" ${!isGraph ? "selected" : ""}>이 서버의 Classic Outlook · 로컬</option>
        </select></label>
        <label><span>새 메일 확인 주기</span><select id="outlookSyncInterval">
          <option value="60" ${Number(settings.sync_interval_seconds || 60) === 60 ? "selected" : ""}>1분</option>
          <option value="300" ${Number(settings.sync_interval_seconds) === 300 ? "selected" : ""}>5분</option>
          <option value="900" ${Number(settings.sync_interval_seconds) === 900 ? "selected" : ""}>15분</option>
          <option value="1800" ${Number(settings.sync_interval_seconds) === 1800 ? "selected" : ""}>30분</option>
        </select></label>
        <label><span>미열람 메일 브리핑 시각</span><input id="outlookDigestTime" type="time" value="${escapeAttr(settings.daily_digest_time || "09:00")}"></label>
      </div>
      <label class="outlook-check-row"><input id="outlookDigestEnabled" type="checkbox" ${settings.daily_digest_enabled !== false ? "checked" : ""}><span><strong>매일 미열람 주요 메일 알림</strong><small>설정 시각 이후 첫 실행 때 중요 메일 브리핑 페이지와 받은 편지함 알림을 만듭니다.</small></span></label>
      <label class="outlook-check-row"><input id="outlookBrowserNotifications" type="checkbox" ${settings.browser_notifications !== false ? "checked" : ""}><span><strong>브라우저 알림</strong><small>Notion이 열려 있을 때 새 브리핑을 알립니다. 현재 권한: ${escapeHtml(permission)}</small></span></label>
      <div class="outlook-runtime-row"><span>마지막 동기화 <strong>${escapeHtml(lastSync)}</strong></span><span>마지막 브리핑 <strong>${escapeHtml(lastDigest)}</strong></span><span>요약 엔진 <strong>${escapeHtml(status.ai_provider || "-")}</strong></span></div>
      <div class="outlook-actions">
        <button id="outlookToggleBtn" type="button" class="${enabled ? "danger" : "primary"}">${enabled ? "Outlook 연결 끄기" : (isGraph ? "Microsoft 계정으로 연결" : "이 서버의 Outlook 연결")}</button>
        <button id="outlookSaveBtn" type="button" ${enabled ? "" : "disabled"}>설정 저장</button>
        <button id="outlookSyncNowBtn" type="button" ${enabled ? "" : "disabled"}>지금 동기화</button>
        <button id="outlookDigestNowBtn" type="button" ${enabled ? "" : "disabled"}>브리핑 지금 만들기</button>
      </div>
    </div>
  `, [{ label: "닫기", action: closeModal }]);
  bindOutlookSettingsModal();
}

async function waitForOutlookJob(jobId, label) {
  if (!jobId) return null;
  for (let attempt = 0; attempt < 240; attempt += 1) {
    await new Promise(resolve => window.setTimeout(resolve, 1000));
    const job = await api(`/api/code/jobs/${encodeURIComponent(jobId)}`);
    if (["done", "failed", "cancelled"].includes(job.status)) {
      if (job.status !== "done") throw new Error(job.error || `${label} 실패`);
      return job.result || {};
    }
  }
  throw new Error(`${label} 작업이 아직 실행 중입니다. 잠시 후 상태를 다시 확인해 주세요.`);
}

async function refreshOutlookUi(message = "", probe = false) {
  await Promise.all([loadOutlookStatus(probe), loadPages(), loadInboxItems()]);
  renderOutlookSettingsModal(State.outlook, message);
  if (!State.activePageId) renderHome();
}

async function saveOutlookSettings(enabledOverride = null) {
  const body = outlookSettingsBody(enabledOverride);
  if (body.browser_notifications && typeof Notification !== "undefined" && Notification.permission === "default") {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") body.browser_notifications = false;
  }
  State.outlook = await api("/api/notion/outlook/settings", { method: "PUT", body });
  renderWorkspacePopover();
  if (!State.activePageId) renderHome();
  return State.outlook;
}

function bindOutlookSettingsModal() {
  $("#outlookConnector")?.addEventListener("change", event => {
    const button = $("#outlookToggleBtn");
    if (!button || State.outlook?.enabled) return;
    button.textContent = event.currentTarget.value === "microsoft_graph"
      ? "Microsoft 계정으로 연결"
      : "이 서버의 Outlook 연결";
  });
  $("#outlookToggleBtn")?.addEventListener("click", async event => {
    const enabling = !State.outlook?.enabled;
    event.currentTarget.disabled = true;
    try {
      if (!enabling) {
        State.outlook = await api("/api/notion/outlook/connection", { method: "DELETE" });
        renderWorkspacePopover();
        if (!State.activePageId) renderHome();
        renderOutlookSettingsModal(State.outlook, "Outlook 연결을 끄고 저장된 인증 토큰을 제거했습니다.");
        return;
      }
      const selectedConnector = outlookSettingsFormValue("#outlookConnector", State.outlook?.connector || "microsoft_graph");
      if (selectedConnector === "microsoft_graph") {
        const data = await saveOutlookSettings(false);
        const configuration = data.connector_status?.configuration || {};
        if (!configuration.configured) {
          throw new Error(`서버 Graph 설정이 필요합니다: ${(configuration.missing || []).join(", ")}`);
        }
        window.location.assign("/api/notion/outlook/oauth/start");
        return;
      }
      const data = await saveOutlookSettings(true);
      const jobId = data.job?.job_id || "";
      renderOutlookSettingsModal(data, "이 서버의 Classic Outlook 연결을 활성화했습니다. 첫 동기화를 시작합니다.");
      if (jobId) {
        try {
          const result = await waitForOutlookJob(jobId, "첫 메일 동기화");
          await refreshOutlookUi(`첫 동기화 완료 · 새 메일 ${Number(result.created || 0)}건 저장`);
        } catch (error) {
          await refreshOutlookUi(error.message);
        }
      }
    } catch (error) {
      renderOutlookSettingsModal(State.outlook, `연결 실패: ${error.message}`);
    }
  });
  $("#outlookSaveBtn")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      await saveOutlookSettings(true);
      await refreshOutlookUi("Outlook 설정을 저장했습니다.");
    } catch (error) {
      renderOutlookSettingsModal(State.outlook, `설정 저장 실패: ${error.message}`);
    }
  });
  $("#outlookSyncNowBtn")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      renderOutlookSettingsModal(State.outlook, "새 메일을 확인하고 요약하는 중입니다.");
      const queued = await api("/api/notion/outlook/sync", { method: "POST" });
      const result = await waitForOutlookJob(queued.job?.job_id || "", "Outlook 동기화");
      await refreshOutlookUi(`동기화 완료 · 새 메일 ${Number(result.created || 0)}건, 갱신 ${Number(result.updated || 0)}건`);
    } catch (error) {
      await refreshOutlookUi(`동기화 실패: ${error.message}`);
    }
  });
  $("#outlookDigestNowBtn")?.addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      renderOutlookSettingsModal(State.outlook, "미열람 중요 메일 브리핑을 만드는 중입니다.");
      const queued = await api("/api/notion/outlook/digest", { method: "POST" });
      const result = await waitForOutlookJob(queued.job?.job_id || "", "Outlook 브리핑");
      await refreshOutlookUi(result.page_id ? `브리핑 완료 · 주요 메일 ${Number(result.count || 0)}건` : "현재 미열람 메일이 없습니다.");
    } catch (error) {
      await refreshOutlookUi(`브리핑 생성 실패: ${error.message}`);
    }
  });
}

async function openOutlookSettings(message = "") {
  closeWorkspacePopover();
  openModal("Microsoft Outlook 연결", '<div class="outlook-settings-loading">Outlook 연결 상태를 확인하는 중...</div>');
  await loadOutlookStatus(true);
  renderOutlookSettingsModal(State.outlook, message);
}

function pageSummaryById(pageId) {
  return State.pages.find(p => p.id === pageId) || null;
}

function defaultPageId() {
  const visible = visiblePages();
  const own = ownPages();
  const rootOwn = own.filter(p => !(p.parent_id || ""));
  const rootVisible = visible.filter(p => !(p.parent_id || ""));
  return (
    rootOwn.find(p => (p.title || "").toLowerCase() === "second brain home")?.id ||
    rootOwn[0]?.id ||
    rootVisible[0]?.id ||
    own[0]?.id ||
    visible[0]?.id ||
    ""
  );
}

function pageCrumbs(page) {
  const crumbs = [];
  let current = page;
  const seen = new Set();
  while (current && current.id && !seen.has(current.id)) {
    seen.add(current.id);
    crumbs.unshift(current);
    current = current.parent_id ? pageSummaryById(current.parent_id) : null;
  }
  return crumbs.length ? crumbs : [page];
}

function renderBreadcrumb(page = null) {
  const root = $("#breadcrumb");
  if (!page) {
    const workspace = selectedNavigationWorkspace();
    if (State.navigation.enabled && State.navigation.collaborativeSharing && workspace?.name) {
      root.innerHTML = `<span class="breadcrumb-home" aria-hidden="true">${iconSvg("home")}</span><span class="breadcrumb-workspace">${escapeHtml(workspace.name)} HQ</span><span class="breadcrumb-sep">/</span><span class="breadcrumb-segment teamspace-home-breadcrumb">🏠&nbsp; 팀스페이스 홈</span>`;
    } else {
      root.textContent = "홈";
    }
    return;
  }
  const crumbs = pageCrumbs(page);
  const workspace = selectedNavigationWorkspace();
  if (String(workspace?.access?.role || "") === "guest") {
    root.innerHTML = `<button type="button" class="breadcrumb-segment" data-crumb-id="${escapeHtml(page.id)}">${escapeHtml(page.title || "제목 없음")}</button>`;
    return;
  }
  const workspaceCrumb = State.navigation.enabled && workspace?.name
    ? `<span class="breadcrumb-home" aria-hidden="true">${iconSvg("home")}</span><span class="breadcrumb-workspace">${escapeHtml(workspace.name)} HQ</span><span class="breadcrumb-sep">/</span>`
    : "";
  root.innerHTML = workspaceCrumb + crumbs.map((item, idx) => `
    <button type="button" class="breadcrumb-segment" data-crumb-id="${escapeHtml(item.id)}">${escapeHtml(item.title || "제목 없음")}</button>
    ${idx < crumbs.length - 1 ? '<span class="breadcrumb-sep">/</span>' : ""}
  `).join("");
  root.querySelectorAll("[data-crumb-id]").forEach(btn => {
    const crumb = crumbs.find(item => item.id === btn.dataset.crumbId);
    if (crumb) btn.insertAdjacentHTML("afterbegin", pageIconHtml(crumb, "breadcrumb-page-icon"));
    btn.addEventListener("click", () => openPage(btn.dataset.crumbId));
  });
}

async function goHome(push = true, focusContent = true) {
  if (!(await window.NotionAiNotes.beforeNavigate())) return;
  State.pageLoadSequence += 1;
  if (State.dirty) await savePage();
  if (State.dirty) {
    setStatus("저장 실패로 페이지를 이동하지 않았습니다.", "error");
    return;
  }
  closePageFind();
  if (State.activePageId) {
    EditorHost.notifyLifecycle("document-unloaded", { page_id: State.activePageId });
  }
  stopLiveCollaboration();
  if (State.activePageId) {
    EditorHost.notifyLifecycle("document-unloaded", { page_id: State.activePageId });
  }
  State.activePageId = "";
  State.pageData = null;
  State.databaseFullPageBlockId = "";
  State.serverRevision = null;
  State.atomicSaveEnabled = false;
  State.saveRetry = null;
  State.saveConflict = null;
  State.dirty = false;
  State.selectedBlockIds.clear();
  State.activeBlockId = "";
  State.history = { entries: [], index: -1 };
  closeDrawer();
  switchSidebarMode("pages");
  if (push) history.pushState({ home: true }, "", "/workspace");
  renderPageLists();
  renderEditor();
  if (focusContent) {
    requestAnimationFrame(() => $("#homeView")?.focus({ preventScroll: true }));
  }
}

function cardHtml(page) {
  return `
    <button class="home-card" type="button" data-page-id="${escapeHtml(page.id)}">
      <span class="home-card-title">${escapeHtml(page.title || "제목 없음")}</span>
      <span class="home-card-meta">${escapeHtml(formatRelative(page.updated_at))}</span>
    </button>
  `;
}

function renderCollaborativeWorkspaceHome(root, workspace) {
  root.classList.add("teamspace-home-view");
  root.setAttribute("aria-label", `${workspace.name || "워크스페이스"} 팀스페이스 홈`);
  root.innerHTML = `
    <div class="teamspace-home-icon" aria-hidden="true">${pageCustomIconContentHtml("🏠")}</div>
    <h1 class="teamspace-home-title">팀스페이스 홈</h1>
    <button class="teamspace-home-collapse" type="button" aria-label="팀스페이스 홈 안내 접기">−</button>
    <p class="teamspace-home-intro">다른 동료들이 우리 팀과 팀의 업무를 알아갈 수 있는 공간을 마련해 주세요. 왼쪽 사이드바의 + 버튼을 눌러 페이지를 추가할 수 있습니다. 프로세스 문서나 프로젝트 로드맵을 추가해보는 건 어떨까요?</p>
    <section class="teamspace-home-welcome" aria-label="팀 안내">
      <strong><span class="teamspace-home-welcome-icon">${pageCustomIconContentHtml("👋")}</span><span>우리 팀에 오신 걸 환영합니다! 우리 팀의 미션은요...</span></strong>
      <p>질문이 있으신가요? 아래 연락처로 알려주세요.</p>
      <ul>
        <li>Slack 채널:</li>
        <li>이메일:</li>
        <li>팀장:</li>
      </ul>
    </section>
    <div class="teamspace-home-columns">
      <section>
        <h2>팀 소개</h2>
        <p>@를 입력하고 팀원을 태그하세요</p>
      </section>
      <section>
        <h2>팀 리소스</h2>
        <p>/를 입력하고 원하는 블록을 추가하세요</p>
      </section>
    </div>
  `;
  root.querySelector(".teamspace-home-collapse")?.addEventListener("click", event => {
    const collapsed = root.classList.toggle("teamspace-home-collapsed");
    event.currentTarget.textContent = collapsed ? "+" : "−";
    event.currentTarget.setAttribute(
      "aria-label",
      collapsed ? "팀스페이스 홈 안내 펼치기" : "팀스페이스 홈 안내 접기",
    );
  });
}

function renderHome() {
  const root = $("#homeView");
  const workspace = selectedNavigationWorkspace();
  if (State.navigation.enabled && State.navigation.collaborativeSharing && workspace) {
    renderCollaborativeWorkspaceHome(root, workspace);
    return;
  }
  root.classList.remove("teamspace-home-view", "teamspace-home-collapsed");
  root.setAttribute("aria-label", "홈");
  const recent = [...visiblePages()].sort((a, b) => Number(b.updated_at || 0) - Number(a.updated_at || 0)).slice(0, 4);
  const own = ownPages().slice(0, 4);
  const shared = sharedPages().slice(0, 4);
  const sessions = State.overview?.sessions || [];
  const sources = State.overview?.sources || [];
  const attached = State.overview?.attached_sessions || [];
  const calendarEvents = State.overview?.calendar_events || [];
  const calendarHtml = calendarEvents.length
    ? `<div class="home-calendar-events">${calendarEvents.map(event => `
        <button type="button" data-home-calendar-page="${escapeAttr(event.page_id || event.source_page_id)}">
          <time>${escapeHtml(event.date || "")}</time>
          <span>${escapeHtml(event.title || "제목 없음")}</span>
          <small>${escapeHtml(event.source_page_title || "")}</small>
        </button>
      `).join("")}</div>`
    : '<div class="calendar-empty-message">향후 7일 동안 예정된 이벤트가 없습니다.</div>';
  root.innerHTML = `
    <h1 class="home-hello">안녕하세요.</h1>

    <section class="home-section">
      <h2 class="home-section-title">캘린더</h2>
      <div class="calendar-empty">
        <div>
          ${calendarHtml}
          <button type="button" id="calendarConnectBtn">＋ 캘린더 페이지 만들기</button>
        </div>
      </div>
    </section>

    <section class="home-section">
      <h2 class="home-section-title">최근 항목</h2>
      <div class="recent-grid">
        ${recent.map(cardHtml).join("")}
        <button class="home-card" type="button" id="homeNewPageBtn">
          <span class="home-card-title">새 페이지</span>
          <span class="home-card-meta">문서 만들기</span>
        </button>
      </div>
    </section>

    <section class="home-section">
      <h2 class="home-section-title">공유된 페이지</h2>
      <div class="page-card-grid">${shared.length ? shared.map(cardHtml).join("") : '<div class="mini-row"><span>공유된 페이지 없음</span></div>'}</div>
    </section>

    <section class="home-section">
      <h2 class="home-section-title">개인 페이지</h2>
      <div class="page-card-grid">${own.length ? own.map(cardHtml).join("") : '<div class="mini-row"><span>개인 페이지 없음</span></div>'}</div>
    </section>

    <section class="home-section">
      <h2 class="home-section-title">Second Brain 연결</h2>
      <div class="agent-card">
        <div>
          <strong>Second Brain Home은 작업 대시보드입니다.</strong>
          <p>Inbox에는 대화/문서 캡처를 모으고, Projects/Areas/Resources에서 정제한 뒤, 안정된 내용만 Second Brain으로 업로드합니다.</p>
          <button type="button" id="homeTemplateBtn">Second Brain 템플릿 만들기</button>
        </div>
        <div class="overview-list">
          <div class="mini-row"><span>최근 세션</span><small>${sessions.length}</small></div>
          <div class="mini-row"><span>Second Brain source</span><small>${sources.length}</small></div>
          <div class="mini-row"><span>Notion context 연결 세션</span><small>${attached.length}</small></div>
        </div>
      </div>
    </section>
  `;
  root.querySelectorAll("[data-page-id]").forEach(btn => btn.addEventListener("click", () => openPage(btn.dataset.pageId)));
  root.querySelectorAll("[data-home-calendar-page]").forEach(btn => btn.addEventListener("click", () => openPage(btn.dataset.homeCalendarPage)));
  $("#homeNewPageBtn")?.addEventListener("click", () => createPage());
  $("#homeTemplateBtn")?.addEventListener("click", createSecondBrainTemplate);
  $("#calendarConnectBtn")?.addEventListener("click", createCalendarPage);
}

async function createCalendarPage() {
  const created = await createPage("", { title: "캘린더" });
  if (!created || !State.pageData || State.activePageId !== created.page?.id) return;
  const seed = State.pageData.blocks?.[0];
  State.pageData.blocks = [EditorCore.createBlock("calendar", {
    id: seed?.id,
    content: "캘린더",
  })];
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  await savePage();
}

async function createPage(parentId = "", options = {}) {
  if (typeof parentId !== "string") parentId = "";
  const title = String(options?.title || "제목 없음").trim().slice(0, 200) || "제목 없음";
  try {
    setStatus(parentId ? "하위 페이지 생성 중..." : "페이지 생성 중...");
    if (parentId && !(await flushPageDraft(parentId))) {
      setStatus("저장되지 않은 상위 페이지 편집본이 있어 하위 페이지를 만들지 않았습니다.", "error");
      return;
    }
    let data;
    if (State.navigation.enabled && State.navigation.workspaceId) {
      const selected = selectedNavigationWorkspace();
      if (!selected || !Number.isInteger(selected.version) || selected.version < 1) {
        throw new Error("활성 워크스페이스 버전을 확인할 수 없습니다.");
      }
      const pending = State.navigation.pendingPageCreate;
      const sameIntent = pending
        && pending.workspaceId === State.navigation.workspaceId
        && pending.parentId === parentId;
      const intent = sameIntent ? pending : {
        baseVersion: selected.version,
        operationId: navigationOperationId("page-create"),
        parentId,
        workspaceId: State.navigation.workspaceId,
      };
      State.navigation.pendingPageCreate = intent;
      try {
        data = await api(
          `/api/notion/workspaces/${encodeURIComponent(intent.workspaceId)}/pages`,
          {
            method: "POST",
            body: {
              base_version: intent.baseVersion,
              operation_id: intent.operationId,
              parent_id: intent.parentId,
              title,
            },
          },
        );
      } catch (error) {
        if (
          error?.status === 409
          && Number.isInteger(error?.data?.current_version)
          && error.data.current_version >= 1
        ) {
          intent.baseVersion = error.data.current_version;
        }
        throw error;
      }
      State.navigation.pendingPageCreate = null;
      const workspaceIndex = State.navigation.workspaces.findIndex(
        item => item.id === data.workspace?.id,
      );
      if (workspaceIndex >= 0) {
        State.navigation.workspaces[workspaceIndex] = { ...data.workspace };
      }
    } else {
      data = await api("/api/notion/pages", {
        method: "POST",
        body: { title, parent_id: parentId, add_to_parent_blocks: Boolean(parentId) },
      });
    }
    if (parentId) {
      State.collapsedPageIds.delete(parentId);
      saveCollapsedPages();
      await refreshOpenedPageAfterMutation(parentId);
    }
    await loadPages();
    await openPage(data.page.id, true, PageFocusPolicy.PAGE_TITLE);
    setStatus(parentId ? "하위 페이지 생성됨" : "페이지 생성됨", "ok");
    return data;
  } catch (e) {
    setStatus("생성 실패: " + e.message, "error");
    return null;
  }
}

async function createSecondBrainTemplate() {
  try {
    setStatus("Second Brain 템플릿 생성 중...");
    if (!(await flushAllOpenDrafts())) {
      setStatus("저장되지 않은 편집본이 있어 템플릿을 만들지 않았습니다.", "error");
      return;
    }
    const data = await api("/api/notion/templates/second_brain", { method: "POST", body: {} });
    await refreshOpenPagesAfterMutation();
    await Promise.all([loadOverview(), loadPages()]);
    if (data.home_page_id) await openPage(data.home_page_id);
    setStatus(data.status === "existing" ? "기존 템플릿을 열었습니다" : "템플릿 생성 완료", "ok");
  } catch (e) {
    setStatus("템플릿 생성 실패: " + e.message, "error");
  }
}

async function openPage(pageId, push = true, focusPolicy = PageFocusPolicy.PAGE_CANVAS) {
  if (!(await window.NotionAiNotes.beforeNavigate())) return;
  const pageLoadSequence = ++State.pageLoadSequence;
  const pageLoadIsCurrent = () => pageLoadSequence === State.pageLoadSequence;
  if (
    State.navigation.authoritative
    && !State.pages.some(page => page.id === String(pageId || ""))
  ) {
    await goHome(false);
    if (location.pathname.startsWith("/page/")) {
      history.replaceState({}, "", "/workspace");
    }
    setStatus("현재 워크스페이스에서 페이지를 찾을 수 없습니다.", "error");
    return;
  }
  if (State.dirty) await savePage();
  if (!pageLoadIsCurrent()) return;
  if (State.dirty) {
    setStatus("저장 실패로 페이지를 이동하지 않았습니다.", "error");
    return;
  }
  closePageFind();
  stopLiveCollaboration();
  State.databaseFullPageBlockId = "";
  let loadedPage = false;
  try {
    setStatus("페이지 로드 중...");
    let data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}`);
    if (!pageLoadIsCurrent()) return;
    if (State.navigation.collaborativeSharing && State.navigation.workspaceId) {
      const legacyPage = data.page || {};
      const collaborative = await api(
        `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/pages/${encodeURIComponent(pageId)}/collaboration`,
      );
      if (!pageLoadIsCurrent()) return;
      data = {
        ...data,
        page: {
          ...legacyPage,
          ...(collaborative.page || {}),
          icon: collaborative.page?.icon || legacyPage.icon || "",
          cover: collaborative.page?.cover || legacyPage.cover || "",
        },
        blocks: collaborative.blocks || data.blocks || [],
        databases: collaborative.databases || data.databases || [],
        files: collaborative.files || data.files || [],
        revision: collaborative.revision ?? data.revision,
        capabilities: {
          ...(data.capabilities || {}),
          collaborative_iam: true,
          collaborative_editing: State.navigation.collaborativeEditing === true,
          collaborative_databases: collaborative.capabilities?.collaborative_databases === true,
          collaborative_files: collaborative.capabilities?.collaborative_files === true,
          collaborative_history: collaborative.capabilities?.collaborative_history === true,
        },
      };
    }
    data.page = {
      ...(data.page || {}),
      collaborative_editing: data.capabilities?.collaborative_editing === true,
      collaborative_databases: data.capabilities?.collaborative_databases === true,
      collaborative_files: data.capabilities?.collaborative_files === true,
      collaborative_history: data.capabilities?.collaborative_history === true,
    };
    State.activePageId = pageId;
    State.pageData = normalizedPageData(data);
    State.serverRevision = serverRevisionFrom(data);
    State.atomicSaveEnabled = atomicSaveSupported(data);
    State.dirty = false;
    State.editRevision = 0;
    State.saveRetry = null;
    State.saveConflict = null;
    State.saveQueued = false;
    State.backlinks = [];
    const collaborativeEditing = State.pageData.page?.collaborative_editing === true;
    if (collaborativeEditing) clearMainEditorDraft(pageId);
    const draftRecovery = collaborativeEditing ? null : restoreMainEditorDraft(pageId);
    const resolvedFocusPolicy = draftRecovery
      ? PageFocusPolicy.PAGE_CANVAS
      : focusPolicy;
    if (draftRecovery) {
      clearTimeout(State.saveTimer);
      State.saveTimer = null;
    }
    loadedPage = true;
    if (push) history.pushState({ pageId }, "", `/page/${encodeURIComponent(pageId)}`);
    State.selectedBlockIds.clear();
    State.activeBlockId = "";
    renderPageLists();
    renderEditor();
    if (collaborativeEditing) {
      CollaborativeEditorBridge.configureDocument(readEditorHostDocument());
    }
    resetEditorHistory();
    EditorHost.notifyLifecycle("document-loaded", { page_id: State.activePageId });
    void startLiveCollaboration().catch(error => {
      setStatus(`실시간 협업 시작 실패: ${error.message}`, "error");
    });
    requestAnimationFrame(() => {
      scrollToLocationBlock();
      applyPageFocusPolicy(resolvedFocusPolicy);
    });
    const supplementalLoads = [loadShares(), loadSessions(), loadBacklinks()];
    if (!$("#publicShareSection")?.hidden) supplementalLoads.push(loadPublicLink());
    await Promise.allSettled(supplementalLoads);
    if (!pageLoadIsCurrent()) return;
    if (draftRecovery?.conflicted) {
      setStatus(conflictStatusText("main"), "error");
    } else if (draftRecovery) {
      setStatus("저장되지 않은 편집본을 복구했습니다. Ctrl+S로 다시 저장할 수 있습니다.", "error");
    } else {
      setStatus("Ready", "ok");
    }
  } catch (e) {
    if (!pageLoadIsCurrent()) return;
    if (!loadedPage) {
      State.activePageId = "";
      State.pageData = null;
      State.serverRevision = null;
      State.atomicSaveEnabled = false;
      State.saveRetry = null;
      State.saveConflict = null;
      State.dirty = false;
      renderPageLists();
      renderEditor();
      if (location.pathname.startsWith("/page/")) history.replaceState({}, "", "/workspace");
    }
    setStatus("로드 실패: " + e.message, "error");
  }
}

function pageIsLocked(page) {
  return Boolean(page?.settings?.locked);
}

function activePageCanEdit() {
  const page = State.pageData?.page;
  if (!page?.can_edit || pageIsLocked(page)) return false;
  if (page.collaborative_editing !== true) return true;
  const live = State.liveCollaboration;
  return Boolean(
    CollaborativeEditingOutbox
    && live.crdtReady
    && !live.offlinePolicyBlocked
    && !live.recoveryOnly
    && CollaborativeEditorBridge.descriptor().page_id === String(page.id || ""),
  );
}

function applyPageLayout(page) {
  const view = $("#pageView");
  const settings = page?.settings || {};
  view.classList.toggle("full-width", Boolean(settings.full_width));
  view.classList.toggle("small-text", Boolean(settings.small_text));
  view.classList.remove("font-default", "font-serif", "font-mono");
  view.classList.add(`font-${["serif", "mono"].includes(settings.font) ? settings.font : "default"}`);
  view.classList.toggle("page-locked", pageIsLocked(page));

  const cover = $("#pageCover");
  const coverValue = String(page?.cover || "");
  cover.hidden = !coverValue;
  cover.style.backgroundImage = "";
  cover.style.background = "";
  if (coverValue) {
    if (/^https?:\/\//i.test(coverValue)) cover.style.backgroundImage = `url("${coverValue.replace(/["\\]/g, "")}")`;
    else if (/^(linear-gradient|radial-gradient)\(/i.test(coverValue)) cover.style.background = coverValue;
    else cover.style.background = coverValue;
  }

  const iconButton = $("#pageIconBtn");
  iconButton.hidden = !page?.icon;
  iconButton.innerHTML = page?.icon ? pageCustomIconContentHtml(page.icon) : "";
  $("#addIconBtn").hidden = Boolean(page?.icon) || !page?.can_edit;
  $("#addCoverBtn").hidden = Boolean(coverValue) || !page?.can_edit;
  $("#addCommentBtn").hidden = !page;
  $("#changeCoverBtn").hidden = !page?.can_edit;
  $("#removeCoverBtn").hidden = !page?.can_edit;
}

function renderTopbarPageActions(page = null) {
  const editTime = $("#pageEditTime");
  const favorite = $("#favoritePageBtn");
  if (editTime) {
    editTime.hidden = !page;
    const guestRole = String(page?.iam_role || "");
    const directGuest = document.body.dataset.pageIamGuest === "true";
    editTime.textContent = !page
      ? ""
      : (directGuest && ["comment", "view"].includes(guestRole)
        ? "지금 편집"
        : `${formatRelative(page.updated_at)} 편집`);
  }
  if (favorite) {
    const active = Boolean(page && pageIsFavorite(page));
    favorite.hidden = !page;
    favorite.textContent = active ? "★" : "☆";
    favorite.title = active ? "즐겨찾기에서 제거" : "즐겨찾기에 추가";
    favorite.setAttribute("aria-label", favorite.title);
  }
}

function renderEditor() {
  const home = $("#homeView");
  const view = $("#pageView");
  if (!State.pageData) {
    delete document.body.dataset.pageIamRole;
    delete document.body.dataset.pageIamGuest;
    const guestBanner = $("#guestAccessBanner");
    if (guestBanner) guestBanner.hidden = true;
    const landingBanner = $("#workspaceLandingBanner");
    if (landingBanner) landingBanner.hidden = true;
    setActiveNav("home");
    home.hidden = false;
    view.hidden = true;
    renderBreadcrumb(null);
    renderBreadcrumbPopover();
    renderHome();
    renderInspectorEmpty();
    renderTopbarPageActions(null);
    const moreBtn = $("#pageMoreBtn");
    if (moreBtn) moreBtn.hidden = true;
    $("#pageTrashBtn").hidden = true;
    $("#pageBacklinksBtn").hidden = true;
    return;
  }
  setActiveNav(State.sidebarMode === "agent" ? "ai" : "");
  home.hidden = true;
  view.hidden = false;
  const page = State.pageData.page;
  syncGuestAccessPresentation(page);
  applyPageLayout(page);
  renderBreadcrumb(page);
  renderBreadcrumbPopover();
  renderTopbarPageActions(page);
  $("#pageTitle").value = page.title || "제목 없음";
  $("#pageTitle").disabled = !activePageCanEdit();
  $("#pageMeta").textContent = `소유자 ${page.owner_user_id} · ${page.scope} · ${page.role} · ${formatTime(page.updated_at)} · ${pageIsLocked(page) ? "페이지 잠김" : "자동 저장"}`;
  const commentAffordance = $("#pageCommentAffordance");
  if (commentAffordance) commentAffordance.hidden = String(page.iam_role || "") !== "comment";
  const commentAffordanceAvatar = $("#pageCommentAffordanceAvatar");
  if (commentAffordanceAvatar) commentAffordanceAvatar.textContent = String(State.user?.display_name || State.user?.id || "").slice(0, 1);
  renderPageCommentPreview();
  renderBacklinksButton();
  State.pageData.blocks = State.pageData.blocks || [];
  renderBlocks();
  renderFiles();
  const moreBtn = $("#pageMoreBtn");
  if (moreBtn) moreBtn.hidden = false;
  $("#shareBtn").disabled = !page.can_share;
  $("#pageTrashBtn").hidden = !page.can_edit;
}

function syncGuestAccessPresentation(page) {
  const role = String(page?.iam_role || "");
  const workspace = (State.navigation.workspaces || [])
    .find(item => item.id === State.navigation.workspaceId);
  const guest = String(workspace?.access?.role || "") === "guest";
  if (guest) setSidebarCollapsed(false, false);
  const autoOpenWorkspacePopover = false;
  if (document.body.dataset.guestWorkspacePopoverAuto === "true") {
    delete document.body.dataset.guestWorkspacePopoverAuto;
    closeWorkspacePopover();
  }
  syncGuestDirectWorkspacePopoverVisual(autoOpenWorkspacePopover, workspace);
  if (guest) {
    document.body.dataset.pageIamRole = role;
    document.body.dataset.pageIamGuest = "true";
  } else {
    delete document.body.dataset.pageIamRole;
    delete document.body.dataset.pageIamGuest;
  }
  const commentAffordanceButton = $("#pageCommentAffordanceBtn");
  if (commentAffordanceButton) {
    commentAffordanceButton.textContent = "댓글 추가";
  }
  const banner = $("#guestAccessBanner");
  syncWorkspaceLandingBanner(page, {guest, workspace});
  if (!banner) return;
  const visible = guest && ["edit_content", "comment", "view"].includes(role);
  banner.hidden = !visible;
  if (!visible) return;
  const workspaceName = workspace?.name || "워크스페이스";
  const bannerText = banner.querySelector("span");
  if (bannerText) {
    bannerText.replaceChildren();
    const strong = document.createElement("strong");
    strong.id = "guestAccessWorkspace";
    strong.textContent = workspaceName;
    bannerText.append(strong, "의 게스트이십니다. 내 워크스페이스에서 Notion에 대한 전체 사용 권한을 얻으세요.");
  }
  const createButton = $("#guestWorkspaceCreateBtn");
  if (createButton) createButton.textContent = "워크스페이스 생성하기";
  const sharedLabel = $("#sharedSection")?.querySelector(".section-label span");
  if (sharedLabel) sharedLabel.textContent = "공유된 페이지";
  const links = $("#guestDirectSidebarLinks")?.querySelectorAll("button");
  if (links?.[0]) links[0].lastChild.textContent = "라이브러리";
  if (links?.[1]) links[1].lastChild.textContent = "도움말";
  const guestNew = $("#guestDirectNewBtn");
  if (guestNew?.lastChild) guestNew.lastChild.textContent = "신규";
  const homeLabel = $("#homeNavBtn")?.querySelector("span:last-child");
  if (homeLabel) homeLabel.textContent = "홈";
}

function syncGuestDirectWorkspacePopoverVisual(visible, selectedWorkspace = null) {
  const visual = $("#guestDirectWorkspacePopoverVisual");
  if (!visual) return;
  visual.hidden = !visible;
  if (!visible) return;
  const selected = selectedWorkspace
    || (State.navigation.workspaces || []).find(item => item.id === State.navigation.workspaceId)
    || {};
  const selectedName = selected.name || "워크스페이스";
  $("#guestDirectWorkspaceName").textContent = selectedName;
  $("#guestDirectWorkspaceIcon").textContent = String(selected.icon || selectedName[0] || "R").slice(0, 1);
  $("#guestDirectWorkspaceAccount").textContent = State.user?.email || State.user?.id || "계정";
  const unique = new Map();
  (State.navigation.workspaces || []).forEach(item => {
    const name = String(item.name || "").trim();
    if (!name || /^C0 Browser\s/i.test(name) || name === selectedName) return;
    if (!unique.has(name)) unique.set(name, item);
  });
  const rows = [...unique.values()].slice(0, 3);
  rows.push(selected);
  $("#guestDirectWorkspaceList").innerHTML = rows.slice(0, 4).map(item => {
    const name = item.name || "워크스페이스";
    const current = item.id === State.navigation.workspaceId;
    const guestLabel = String(item.access?.role || "") === "guest" ? "<small>게스트</small>" : "";
    return `<span class="guest-direct-workspace-row"><span>${escapeHtml(String(item.icon || name[0] || "R").slice(0, 1))}</span><strong>${escapeHtml(name)}</strong>${guestLabel}<span>${current ? "✓" : ""}</span></span>`;
  }).join("");
}

function syncWorkspaceLandingBanner(page, context = {}) {
  const banner = $("#workspaceLandingBanner");
  if (!banner) return;
  const workspace = context.workspace || (State.navigation.workspaces || [])
    .find(item => item.id === State.navigation.workspaceId);
  const guest = context.guest ?? String(workspace?.access?.role || "") === "guest";
  const members = State.workspaceAccess?.members;
  const collaborativeBlocks = window.NotionCollaborativeEditor?.projection?.()?.blocks;
  const visibleBlocks = Array.isArray(collaborativeBlocks)
    ? collaborativeBlocks
    : (State.pageData?.blocks || []);
  const hasPageContent = visibleBlocks.some(block => {
    if (String(block?.content || "").trim()) return true;
    if ((block?.props?.rows || []).length) return true;
    return ["image", "file", "bookmark", "child_page"].includes(String(block?.type || ""));
  });
  banner.hidden = !(
    page
    && !guest
    && !hasPageContent
    && !(page.parent_id || "")
    && String(page.owner_user_id || "") === String(State.user?.id || "")
    && Array.isArray(members)
    && members.length > 1
  );
}

async function openSidePeek(pageId, databaseContext = null) {
  if (!pageId) return;
  if (!(await window.NotionAiNotes.beforeNavigate())) return;
  if (State.sidePeek.dirty) {
    await saveSidePeek();
    if (State.sidePeek.dirty) return;
  }
  try {
    setStatus("사이드 보기 로드 중...");
    const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}`);
    State.sidePeek = {
      pageId,
      data: normalizedPageData(data),
      databaseContext,
      dirty: false,
      saveTimer: null,
      editRevision: 0,
      serverRevision: serverRevisionFrom(data),
      atomicSaveEnabled: atomicSaveSupported(data),
      saveInFlight: null,
      saveQueued: false,
      saveRetry: null,
      saveConflict: null,
    };
    renderSidePeek();
    $("#sidePeek").hidden = false;
    requestAnimationFrame(() => $("#sidePeekTitle")?.focus({ preventScroll: true }));
    setStatus("사이드 보기 열림", "ok");
  } catch (e) {
    setStatus("사이드 보기 실패: " + e.message, "error");
  }
}

async function closeSidePeek() {
  if (!(await window.NotionAiNotes.beforeNavigate())) return;
  if (State.sidePeek.dirty) await saveSidePeek();
  if (State.sidePeek.dirty) {
    setStatus("저장 실패로 사이드 보기를 닫지 않았습니다.", "error");
    return;
  }
  clearTimeout(State.sidePeek.saveTimer);
  State.sidePeek = {
    pageId: "",
    data: null,
    databaseContext: null,
    dirty: false,
    saveTimer: null,
    editRevision: 0,
    serverRevision: null,
    atomicSaveEnabled: false,
    saveInFlight: null,
    saveQueued: false,
    saveRetry: null,
    saveConflict: null,
  };
  $("#sidePeek").hidden = true;
  $("#sidePeekBody").innerHTML = "";
}

function resolveSidePeekDatabaseContext() {
  const context = State.sidePeek.databaseContext;
  if (!context || context.sourcePageId !== State.activePageId || !State.pageData) return null;
  const block = State.pageData.blocks.find(item => item.id === context.blockId);
  if (!block || !DATABASE_BLOCK_TYPES.has(block.type)) return null;
  const rows = databaseRows(block);
  let rowIndex = Number(context.rowIndex);
  if (!rows[rowIndex] || rows[rowIndex].page_id !== State.sidePeek.pageId) {
    rowIndex = rows.findIndex(item => item?.page_id === State.sidePeek.pageId);
  }
  if (rowIndex < 0) return null;
  return { block, row: rows[rowIndex], rowIndex };
}

function sidePeekDatabaseNavigation(context = resolveSidePeekDatabaseContext()) {
  if (!context) return { rows: [], position: -1 };
  const rows = visibleDatabaseRows(context.block).filter(item => item.item?.page_id);
  return { rows, position: rows.findIndex(item => item.index === context.rowIndex) };
}

async function navigateSidePeekDatabase(delta) {
  const context = resolveSidePeekDatabaseContext();
  const navigation = sidePeekDatabaseNavigation(context);
  if (!context || navigation.position < 0) return;
  const nextPosition = navigation.position + Number(delta || 0);
  if (nextPosition < 0 || nextPosition >= navigation.rows.length) {
    setStatus(delta < 0 ? "첫 번째 데이터베이스 페이지입니다." : "마지막 데이터베이스 페이지입니다.");
    return;
  }
  const target = navigation.rows[nextPosition];
  await openSidePeek(target.item.page_id, {
    sourcePageId: State.activePageId,
    blockId: context.block.id,
    rowIndex: target.index,
  });
}

function sidePeekDatabasePropertiesHtml(context, canEdit) {
  if (!context) return "";
  const properties = databaseProperties(context.block).filter(property => property.type !== "title");
  if (!properties.length) return "";
  return `<div class="side-peek-properties">${properties.map(property => `
    <label><span>${escapeHtml(property.name || property.id)}</span><span>${databasePropertyInput(context.block, property, context.row, context.rowIndex, canEdit)}</span></label>
  `).join("")}</div>`;
}

function renderSidePeek() {
  const data = State.sidePeek.data;
  const page = data?.page || {};
  const blocks = data?.blocks || [];
  $("#sidePeekCrumb").textContent = page.title || "Side peek";
  const canEdit = Boolean(page.can_edit) && !pageIsLocked(page);
  const databaseContext = resolveSidePeekDatabaseContext();
  const canEditDatabase = Boolean(databaseContext) && activePageCanEdit();
  const navigation = sidePeekDatabaseNavigation(databaseContext);
  const previousButton = $("#sidePeekPrevBtn");
  const nextButton = $("#sidePeekNextBtn");
  if (previousButton) {
    previousButton.hidden = !databaseContext;
    previousButton.disabled = navigation.position <= 0;
  }
  if (nextButton) {
    nextButton.hidden = !databaseContext;
    nextButton.disabled = navigation.position < 0 || navigation.position >= navigation.rows.length - 1;
  }
  const body = $("#sidePeekBody");
  body.innerHTML = `
    <input id="sidePeekTitle" class="side-peek-title" value="${escapeAttr(page.title || "제목 없음")}" ${canEdit ? "" : "disabled"}>
    <div class="side-peek-meta">${escapeHtml(page.owner_user_id || "")} · ${escapeHtml(page.scope || "")} · ${escapeHtml(formatTime(page.updated_at))} · 자동 저장</div>
    ${sidePeekDatabasePropertiesHtml(databaseContext, canEditDatabase)}
    <div id="sidePeekBlocks" class="side-peek-blocks"></div>
    ${canEdit ? `<button type="button" id="sidePeekAddBlock" class="side-peek-add-row">＋ 블록 추가</button>
      <button type="button" id="sidePeekAddChild" class="side-peek-add-row">＋ 하위 페이지 추가</button>` : ""}
  `;
  $("#sidePeekTitle")?.addEventListener("input", e => {
    page.title = e.target.value;
    if (databaseContext) {
      const titleField = databasePropertyId(databaseContext.block, "title", "title");
      databaseContext.row[titleField] = e.target.value;
      databaseContext.row._last_edited_time = new Date().toISOString();
      databaseContext.row._last_edited_by = State.user?.id || "";
      markDirty();
    }
    markSidePeekDirty();
  });
  bindSidePeekSaveShortcut($("#sidePeekTitle"));
  body.querySelectorAll(".side-peek-properties [data-db-field]").forEach(input => input.addEventListener("change", () => {
    if (!databaseContext) return;
    const field = input.dataset.dbField;
    databaseContext.row[field] = input.type === "checkbox"
      ? input.checked
      : input.multiple
        ? [...input.selectedOptions].map(option => option.value).filter(Boolean)
        : input.value;
    databaseContext.row._last_edited_time = new Date().toISOString();
    databaseContext.row._last_edited_by = State.user?.id || "";
    const property = databaseProperties(databaseContext.block).find(item => item.id === field);
    if (["select", "multi_select", "status"].includes(property?.type)) {
      const values = String(databaseContext.row[field] || "").split(",").map(value => value.trim()).filter(Boolean);
      property.options = [...new Set([...(property.options || []), ...values])].slice(0, 200);
    }
    markDirty();
    captureEditorHistory(true);
  }));
  body.querySelectorAll(".side-peek-properties [data-db-date-end]").forEach(input => input.addEventListener("change", () => {
    if (!databaseContext) return;
    const field = input.dataset.dbDateEnd;
    databaseContext.row[`${field}_end`] = input.value;
    databaseContext.row._last_edited_time = new Date().toISOString();
    databaseContext.row._last_edited_by = State.user?.id || "";
    markDirty();
    captureEditorHistory(true);
  }));
  body.querySelectorAll(".side-peek-properties [data-db-date-reminder]").forEach(button => button.addEventListener("click", () => {
    if (!databaseContext) return;
    openDatabaseDateReminderDialog(
      databaseContext.block,
      databaseContext.rowIndex,
      button.dataset.dbDateReminder || "",
      () => { renderBlocks(); renderSidePeek(); },
    );
  }));
  body.querySelectorAll(".side-peek-properties [data-db-file-input]").forEach(input => input.addEventListener("change", () => {
    if (!databaseContext) return;
    uploadDatabasePropertyFiles(
      databaseContext.block,
      databaseContext.rowIndex,
      input.dataset.dbFileInput || "",
      input.files,
      () => { renderBlocks(); renderSidePeek(); },
    );
  }));
  body.querySelectorAll(".side-peek-properties [data-db-file-remove]").forEach(button => button.addEventListener("click", () => {
    if (!databaseContext) return;
    removeDatabasePropertyFile(
      databaseContext.block,
      databaseContext.rowIndex,
      button.dataset.dbFileRemove || "",
      Number(button.dataset.fileIndex),
      () => { renderBlocks(); renderSidePeek(); },
    );
  }));
  body.querySelectorAll(".side-peek-properties [data-db-cell-button]").forEach(button => button.addEventListener("click", () => {
    if (!databaseContext) return;
    const property = databaseProperties(databaseContext.block).find(item => item.id === button.dataset.dbCellButton);
    const url = safeExternalUrl(property?.button_url || "");
    if (url) window.open(url, "_blank", "noopener");
  }));
  const root = $("#sidePeekBlocks");
  if (!blocks.length && canEdit) blocks.push(newBlock("paragraph"));
  const tabControllers = new Map(blocks.filter(block => block.type === "tabs" && block.id).map(block => {
    normalizeTabsBlock(block);
    return [block.id, block];
  }));
  blocks.forEach((block, index) => {
    const row = document.createElement("div");
    row.className = "side-peek-block";
    row.dataset.type = block.type || "paragraph";
    const tabController = tabControllers.get(String(block.props?.tab_group || ""));
    row.hidden = Boolean(tabController && String(block.props?.tab_id || "") !== String(tabController.props?.active_tab_id || ""));
    row.innerHTML = sidePeekBlockHtml(block, index, canEdit);
    root.appendChild(row);
    bindSidePeekBlock(row, block, index, canEdit);
  });
  $("#sidePeekAddBlock")?.addEventListener("click", () => addSidePeekBlock("paragraph"));
  $("#sidePeekAddChild")?.addEventListener("click", createSidePeekChildPage);
}

function bindHtmlEmbedBlock(row, block, sidePeek) {
  const pageId = sidePeek ? State.sidePeek.pageId : State.activePageId;
  const pageSequence = State.pageLoadSequence;
  const currentData = () => sidePeek
    ? (State.sidePeek.pageId === pageId ? State.sidePeek.data : null)
    : (State.activePageId === pageId && State.pageLoadSequence === pageSequence ? State.pageData : null);
  const getBlock = () => currentData()?.blocks?.find(item => item.id === block.id);
  const canEdit = () => Boolean(currentData()?.page?.can_edit && !pageIsLocked(currentData().page)
    && (sidePeek || activePageCanEdit()) && getBlock()?.type === "embed");
  window.NotionHtmlEmbed.bind(row, {
    getBlock, canEdit,
    change: source => { if (canEdit()) { getBlock().content = source; sidePeek ? markSidePeekDirty() : markDirty(); } },
    save: () => canEdit() ? (sidePeek ? saveSidePeek() : savePage()) : false,
  });
}

function bindEquationBlock(row, block, sidePeek) {
  const pageId = sidePeek ? State.sidePeek.pageId : State.activePageId;
  const pageSequence = State.pageLoadSequence;
  const blockId = block.id;
  const currentData = () => sidePeek
    ? (State.sidePeek.pageId === pageId ? State.sidePeek.data : null)
    : (State.activePageId === pageId && State.pageLoadSequence === pageSequence ? State.pageData : null);
  const canEdit = () => Boolean(currentData()?.page?.can_edit
    && !pageIsLocked(currentData().page) && (sidePeek || activePageCanEdit()));
  window.NotionEquationEditor.bind(row, {
    getBlock: () => currentData()?.blocks?.find(item => item.id === blockId),
    canEdit,
    change: source => {
      const current = currentData()?.blocks?.find(item => item.id === blockId);
      if (!canEdit() || !current || current.type !== "equation") return;
      current.content = source;
      delete current.props?.rich_text_html;
      if (sidePeek) {
        markSidePeekDirty();
        // Side-peek autosave rebuilds its DOM. Commit when the popover closes,
        // rather than tearing down the input while the user is still editing.
        clearTimeout(State.sidePeek.saveTimer);
      } else markDirty();
    },
    beginComposition: () => { if (!sidePeek) EditorHost.beginComposition(blockId); },
    endComposition: () => { if (!sidePeek) EditorHost.endComposition(blockId); },
    save: () => canEdit() ? (sidePeek ? saveSidePeek() : savePage()) : false,
  });
}

function bindAiNotesBlock(row, block, sidePeek) {
  const pageId = sidePeek ? State.sidePeek.pageId : State.activePageId;
  const pageSequence = State.pageLoadSequence;
  const blockId = block.id;
  const currentData = () => {
    if (sidePeek) return State.sidePeek.pageId === pageId ? State.sidePeek.data : null;
    return State.activePageId === pageId && State.pageLoadSequence === pageSequence ? State.pageData : null;
  };
  window.NotionAiNotes.bind(row, {
    blockId,
    scope: { page_id: pageId, workspace_id: currentData()?.page?.workspace_id || State.navigation.workspaceId || "" },
    getBlock: () => currentData()?.blocks?.find(item => item.id === blockId),
    canEdit: () => Boolean(currentData()?.page?.can_edit && !pageIsLocked(currentData().page) && (sidePeek || activePageCanEdit())),
    changed: () => sidePeek ? markSidePeekDirty() : markDirty(),
    save: async () => {
      // Transcription and summary can append during an existing autosave. Drain
      // those queued revisions without changing the shared page-save contract.
      for (let attempt = 0; attempt < 4; attempt++) {
        if (!currentData()) return false;
        const pending = [];
        if (State.activePageId === pageId && State.saveInFlight) pending.push(State.saveInFlight);
        if (State.sidePeek.pageId === pageId && State.sidePeek.saveInFlight) pending.push(State.sidePeek.saveInFlight);
        await Promise.all(pending);
        if (!currentData() || !currentData().page.can_edit || pageIsLocked(currentData().page)) return false;
        if (!sidePeek && currentData().page.collaborative_editing === true) {
          await savePage();
          const live = State.liveCollaboration;
          // The established encrypted outbox is the durable offline boundary;
          // a websocket acknowledgement may still be pending.
          return !live.recoveryOnly && !live.offlinePolicyBlocked;
        }
        const mainRevision = State.editRevision;
        const peekRevision = State.sidePeek.editRevision;
        if (await flushPageDraft(pageId)) return true;
        const queued = (State.activePageId === pageId && !State.saveConflict
          && (State.saveQueued || State.saveInFlight || State.editRevision !== mainRevision || !State.dirty))
          || (State.sidePeek.pageId === pageId && !State.sidePeek.saveConflict
            && (State.sidePeek.saveQueued || State.sidePeek.saveInFlight || State.sidePeek.editRevision !== peekRevision || !State.sidePeek.dirty));
        if (!queued) return false;
      }
      return false;
    },
    request: api,
    uploadAudio: async (item, consent, signal, language = "") => {
      if (!currentData()) throw new Error("페이지가 변경되었습니다.");
      const form = new FormData();
      form.append("file", item.blob, item.blob.name || "meeting.wav");
      form.append("page_id", pageId);
      form.append("workspace_id", currentData()?.page?.workspace_id || State.navigation.workspaceId || "");
      form.append("segment_id", item.id);
      form.append("language", language);
      form.append("consent", String(Boolean(consent)));
      const response = await authenticatedFetch("/api/notion/ai-notes/transcribe", { method: "POST", body: form, signal });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || data.error || "음성 인식 요청 실패");
      if (data.segment_id !== item.id || typeof data.text !== "string") throw new Error("음성 구간 응답이 일치하지 않습니다.");
      return data;
    },
  });
}

function sidePeekBlockHtml(block, index, canEdit) {
  const type = block.type || "paragraph";
  if (type === "ai_notes") return window.NotionAiNotes.html(block, canEdit);
  if (type === "embed" && block.props?.embed_mode === "html") return window.NotionHtmlEmbed.html(block, canEdit);
  const disabled = canEdit ? "" : "disabled";
  if (type === "divider") return "<hr>";
  if (type === "equation") {
    return window.NotionEquationEditor.html(block.content, canEdit);
  }
  if (type === "tabs") return tabsBlockHtml(block, canEdit);
  if (type === "todo") {
    return `<label class="todo-control"><input type="checkbox" data-side-check="${index}" ${block.props?.checked ? "checked" : ""} ${disabled}><textarea data-side-text="${index}" ${disabled}>${escapeHtml(block.content || "")}</textarea></label>`;
  }
  if (type === "child_page") {
    const pageId = block.props?.page_id || "";
    const title = block.content || block.props?.title || "하위 페이지";
    return `<button type="button" class="side-peek-child" data-side-child="${escapeAttr(pageId)}">${pageIconHtml("")}${escapeHtml(title)}</button>
      <button type="button" data-side-child-full="${escapeAttr(pageId)}">전체 페이지</button>`;
  }
  if (DATABASE_BLOCK_TYPES.has(type) && type !== "calendar") {
    const rows = Array.isArray(block.props?.rows) ? block.props.rows : [];
    return `<strong>${escapeHtml(block.content || blockTypeMeta(type)[1])}</strong>
      ${rows.map(row => `<button type="button" class="side-peek-child" data-side-child="${escapeAttr(row.page_id || "")}">${pageIconHtml("")}${escapeHtml(databaseRowTitle(block, row))}</button>`).join("")}`;
  }
  if (type === "calendar") {
    const rows = Array.isArray(block.props?.rows) ? block.props.rows : (Array.isArray(block.props?.events) ? block.props.events : []);
    return `<strong>${escapeHtml(block.content || "Calendar")}</strong>
      ${rows.map(row => `<button type="button" class="side-peek-child" data-side-child="${escapeAttr(row.page_id || "")}">${escapeHtml(row.date || "")} · ${escapeHtml(row.title || "새 페이지")}</button>`).join("")}`;
  }
  if (["image", "file", "video", "audio", "pdf"].includes(type)) {
    const fileId = block.props?.file_id || "";
    return `<a href="${escapeAttr(notionFileUrl(fileId))}" target="_blank" rel="noopener">${escapeHtml(block.content || block.props?.original_name || "첨부파일")}</a>`;
  }
  return `<textarea data-side-text="${index}" ${disabled}>${escapeHtml(block.content || "")}</textarea>`;
}

function bindSidePeekBlock(row, block, index, canEdit) {
  if (block.type === "ai_notes") bindAiNotesBlock(row, block, true);
  if (block.type === "equation") bindEquationBlock(row, block, true);
  if (block.type === "embed" && block.props?.embed_mode === "html") bindHtmlEmbedBlock(row, block, true);
  row.querySelectorAll("[data-tab-select]").forEach(button => button.addEventListener("click", () => {
    block.props.active_tab_id = button.dataset.tabSelect || block.props.active_tab_id;
    markSidePeekDirty();
    renderSidePeek();
  }));
  row.querySelector("[data-tab-add]")?.addEventListener("click", () => addSidePeekTab(block, index));
  row.querySelectorAll("[data-tab-menu]").forEach(button => button.addEventListener("click", () => openSidePeekTabSettings(block, button.dataset.tabMenu || "")));
  row.querySelectorAll("[data-side-text]").forEach(textarea => {
    textarea.addEventListener("input", e => {
      block.content = e.target.value;
      if (block.type === "equation") {
        block.props = block.props || {};
        delete block.props.rich_text_html;
        const preview = row.querySelector(".equation-preview");
        if (preview && window.NotionMath) preview.innerHTML = window.NotionMath.renderHtml(block.content);
      }
      markSidePeekDirty();
      resizeTextarea(e.target);
    });
    bindSidePeekSaveShortcut(textarea);
    setTimeout(() => resizeTextarea(textarea), 0);
  });
  row.querySelector("[data-side-check]")?.addEventListener("change", e => {
    block.props = block.props || {};
    block.props.checked = e.target.checked;
    markSidePeekDirty();
  });
  row.querySelectorAll("[data-side-child]").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.sideChild || "";
      if (id) openSidePeek(id);
    });
  });
  row.querySelectorAll("[data-side-child-full]").forEach(btn => {
    btn.addEventListener("click", () => {
      const id = btn.dataset.sideChildFull || "";
      if (id) openSidePeekAsFullPage(id);
    });
  });
}

function bindSidePeekSaveShortcut(control) {
  control?.addEventListener("keydown", event => {
    const command = event.ctrlKey || event.metaKey;
    if (!command || event.key.toLowerCase() !== "s") return;
    event.preventDefault();
    saveSidePeek();
  });
}

function sidePeekFocusSnapshot() {
  const active = document.activeElement;
  if (active?.id === "sidePeekTitle") {
    return { selector: "#sidePeekTitle", offset: Number(active.selectionStart) || 0 };
  }
  const databaseField = active?.dataset?.dbField;
  if (databaseField != null) {
    return {
      selector: `[data-db-field="${CSS.escape(String(databaseField))}"]`,
      offset: Number(active.selectionStart) || 0,
    };
  }
  const databaseDateEnd = active?.dataset?.dbDateEnd;
  if (databaseDateEnd != null) {
    return {
      selector: `[data-db-date-end="${CSS.escape(String(databaseDateEnd))}"]`,
      offset: Number(active.selectionStart) || 0,
    };
  }
  const index = active?.dataset?.sideText;
  if (index != null) {
    return {
      selector: `[data-side-text="${CSS.escape(String(index))}"]`,
      offset: Number(active.selectionStart) || 0,
    };
  }
  return null;
}

function restoreSidePeekFocus(snapshot) {
  if (!snapshot?.selector) return;
  requestAnimationFrame(() => {
    const target = $("#sidePeekBody")?.querySelector(snapshot.selector);
    if (!target) return;
    target.focus({ preventScroll: true });
    const offset = Math.max(0, Math.min(String(target.value || "").length, snapshot.offset));
    target.setSelectionRange?.(offset, offset);
  });
}

function addSidePeekTab(block, blockIndex) {
  if (!State.sidePeek.data?.page?.can_edit || pageIsLocked(State.sidePeek.data.page)) return;
  const tabs = normalizeTabsBlock(block);
  if (tabs.length >= 20) return;
  const tab = { id: newTabId(), name: `탭 ${tabs.length + 1}` };
  tabs.push(tab);
  block.props.active_tab_id = tab.id;
  const blocks = State.sidePeek.data.blocks || [];
  const members = blocks.map((item, index) => ({ item, index })).filter(({ item }) => item.props?.tab_group === block.id);
  const insertAt = members.length ? members[members.length - 1].index + 1 : blockIndex + 1;
  blocks.splice(insertAt, 0, EditorCore.createBlock("paragraph", { props: { tab_group: block.id, tab_id: tab.id } }));
  markSidePeekDirty();
  renderSidePeek();
}

function deleteSidePeekTab(block, tabId) {
  const tabs = normalizeTabsBlock(block);
  if (tabs.length <= 2 || !window.confirm("이 탭과 탭 안의 블록을 삭제할까요?")) return;
  block.props.tabs = tabs.filter(item => item.id !== tabId);
  State.sidePeek.data.blocks = (State.sidePeek.data.blocks || []).filter(item => !(item.props?.tab_group === block.id && item.props?.tab_id === tabId));
  if (block.props.active_tab_id === tabId) block.props.active_tab_id = block.props.tabs[0].id;
  closeModal();
  markSidePeekDirty();
  renderSidePeek();
}

function openSidePeekTabSettings(block, tabId) {
  const tabs = normalizeTabsBlock(block);
  const tab = tabs.find(item => item.id === tabId);
  if (!tab) return;
  openModal("탭 설정", `<label class="database-rule-form">탭 이름<input id="sidePeekTabNameInput" value="${escapeAttr(tab.name)}" maxlength="100"></label>`, [
    ...(tabs.length > 2 ? [{ label: "탭 삭제", action: () => deleteSidePeekTab(block, tabId) }] : []),
    { label: "취소", action: closeModal },
    { label: "저장", primary: true, action: () => {
      tab.name = String($("#sidePeekTabNameInput")?.value || tab.name).trim().slice(0, 100) || tab.name;
      closeModal();
      markSidePeekDirty();
      renderSidePeek();
    } },
  ]);
}

function markSidePeekDirty() {
  State.sidePeek.dirty = true;
  State.sidePeek.editRevision += 1;
  setStatus("사이드 보기 저장 대기 중");
  clearTimeout(State.sidePeek.saveTimer);
  State.sidePeek.saveTimer = null;
  if (State.sidePeek.saveConflict) {
    setStatus(conflictStatusText("sidePeek"), "error");
    return;
  }
  State.sidePeek.saveTimer = setTimeout(saveSidePeek, 900);
}

async function saveSidePeek() {
  const data = State.sidePeek.data;
  const pageId = State.sidePeek.pageId;
  if (!data || !pageId || !State.sidePeek.dirty) return;
  if (State.sidePeek.saveConflict) {
    setStatus(conflictStatusText("sidePeek"), "error");
    return;
  }
  if (State.sidePeek.saveInFlight) {
    State.sidePeek.saveQueued = true;
    return State.sidePeek.saveInFlight;
  }
  const title = $("#sidePeekTitle")?.value || data.page?.title || "Untitled";
  const blocks = EditorCore.deepClone(data.blocks || []);
  const revision = State.sidePeek.editRevision;
  const focusSnapshot = sidePeekFocusSnapshot();
  const useAtomicSave = State.sidePeek.atomicSaveEnabled;
  let request;
  if (useAtomicSave) {
    try {
      request = atomicSaveRequest(State.sidePeek, data, title, blocks, State.sidePeek.serverRevision);
    } catch (error) {
      State.sidePeek.dirty = true;
      setStatus("사이드 보기 저장 실패: " + error.message, "error");
      return;
    }
  }
  State.sidePeek.saveQueued = false;
  State.sidePeek.saveInFlight = (async () => {
    try {
      let saved;
      if (useAtomicSave) {
        saved = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/save`, {
          method: "POST",
          body: request,
        });
      } else {
        await api(`/api/notion/pages/${encodeURIComponent(pageId)}`, {
          method: "PATCH",
          body: { title, _defer_version: true },
        });
        saved = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/blocks`, {
          method: "PUT",
          body: { blocks },
        });
      }
      const savedRevision = serverRevisionFrom(saved);
      if (useAtomicSave && savedRevision === null) throw new Error("저장 응답에 문서 버전이 없습니다.");
      if (State.sidePeek.pageId !== pageId) return;
      if (savedRevision !== null) State.sidePeek.serverRevision = savedRevision;
      if (useAtomicSave && State.sidePeek.saveRetry?.operationId === request.operation_id) State.sidePeek.saveRetry = null;
      State.sidePeek.saveConflict = null;
      if (State.sidePeek.editRevision === revision) {
        State.sidePeek.data = normalizedPageData(saved, State.sidePeek.data);
        State.sidePeek.dirty = false;
        renderSidePeek();
        restoreSidePeekFocus(focusSnapshot);
        if (State.activePageId === pageId && !State.dirty) {
          State.pageData = normalizedPageData(saved, State.pageData);
          applySaveMetadata(State, saved);
          renderEditor();
          resetEditorHistory();
        } else if (State.activePageId === pageId && State.dirty) {
          if (savedRevision !== null) State.serverRevision = savedRevision;
          showSaveConflict("main", pageId, { data: { current_revision: savedRevision } });
        }
        if (!(State.activePageId === pageId && State.saveConflict)) {
          setStatus("사이드 보기 자동 저장", "ok");
        }
      } else if (State.sidePeek.pageId === pageId) {
        State.sidePeek.dirty = true;
        State.sidePeek.saveQueued = true;
      }
      await refreshOtherOpenPagesAfterMutation(pageId);
      await loadPages();
    } catch (e) {
      State.sidePeek.dirty = true;
      if (useAtomicSave && e.status === 409) showSaveConflict("sidePeek", pageId, e);
      else setStatus("사이드 보기 저장 실패: " + e.message, "error");
    } finally {
      if (State.sidePeek.pageId === pageId) {
        State.sidePeek.saveInFlight = null;
        if (State.sidePeek.dirty && State.sidePeek.saveQueued && !State.sidePeek.saveConflict) {
          clearTimeout(State.sidePeek.saveTimer);
          State.sidePeek.saveTimer = setTimeout(saveSidePeek, 100);
        }
      }
    }
  })();
  return State.sidePeek.saveInFlight;
}

async function openSidePeekAsFullPage(pageId = State.sidePeek.pageId) {
  const targetPageId = String(pageId || "");
  if (!targetPageId) return;
  if (State.sidePeek.dirty) await saveSidePeek();
  if (State.sidePeek.dirty) return;
  await closeSidePeek();
  if (State.sidePeek.pageId) return;
  await openPage(targetPageId, true, PageFocusPolicy.PRESERVE);
  document.activeElement?.blur?.();
}

function addSidePeekBlock(type = "paragraph") {
  if (!State.sidePeek.data?.page?.can_edit || pageIsLocked(State.sidePeek.data.page)) return;
  State.sidePeek.data.blocks = State.sidePeek.data.blocks || [];
  State.sidePeek.data.blocks.push(newBlock(type));
  markSidePeekDirty();
  renderSidePeek();
}

async function createSidePeekChildPage() {
  const parentId = State.sidePeek.pageId;
  if (!parentId) return;
  try {
    if (!(await flushPageDraft(parentId))) {
      setStatus("저장되지 않은 상위 페이지 편집본이 있어 하위 페이지를 만들지 않았습니다.", "error");
      return;
    }
    const data = await api("/api/notion/pages", {
      method: "POST",
      body: { title: "새 페이지", parent_id: parentId, add_to_parent_blocks: true },
    });
    const page = data.page || {};
    await refreshOpenedPageAfterMutation(parentId);
    await loadPages();
    if (page.id) await openSidePeek(page.id);
  } catch (e) {
    setStatus("하위 페이지 생성 실패: " + e.message, "error");
  }
}

function blockTypeMeta(type) {
  if (type === "layout_columns_2") return ["Ⅱ", "2개의 열", ""];
  if (type === "layout_columns_3") return ["Ⅲ", "3개의 열", ""];
  const item = EditorCore.BLOCK_BY_TYPE[type] || EditorCore.BLOCK_BY_TYPE.paragraph;
  return [item.icon, item.label, item.shortcut || ""];
}

const TEXT_COLORS = {
  default: "",
  gray: "#787774",
  brown: "#9f6b53",
  orange: "#d9730d",
  yellow: "#cb912f",
  green: "#448361",
  blue: "#337ea9",
  purple: "#9065b0",
  pink: "#c14c8a",
  red: "#d44c47",
};

const BG_COLORS = {
  default: "",
  gray: "#f1f1ef",
  brown: "#f4eeee",
  orange: "#faeadc",
  yellow: "#fbf3db",
  green: "#edf3ec",
  blue: "#e7f3f8",
  purple: "#f4f0f7",
  pink: "#f9eef3",
  red: "#fdebec",
};

function applyBlockVisual(row, block) {
  const color = block.props?.text_color || "";
  const bg = block.props?.background_color || "";
  if (TEXT_COLORS[color]) row.style.setProperty("--block-fg", TEXT_COLORS[color]);
  if (BG_COLORS[bg]) row.style.setProperty("--block-bg", BG_COLORS[bg]);
  if (TEXT_COLORS[color]) row.classList.add("has-text-color");
  if (BG_COLORS[bg]) row.classList.add("has-bg-color");
}

function editorSnapshot() {
  return {
    title: $("#pageTitle")?.value ?? State.pageData?.page?.title ?? "제목 없음",
    blocks: EditorCore.deepClone(State.pageData?.blocks || []),
  };
}

function readEditorHostDocument() {
  if (!State.pageData?.page || !State.activePageId) return null;
  return {
    page_id: State.activePageId,
    title: $("#pageTitle")?.value ?? State.pageData.page.title ?? "",
    blocks: State.pageData.blocks || [],
    can_edit: activePageCanEdit(),
    server_revision: State.serverRevision,
    edit_revision: State.editRevision,
  };
}

function captureEditorHostSelection() {
  const active = document.activeElement;
  const title = $("#pageTitle");
  if (active && active === title) {
    return {
      kind: "title",
      start: Number(title.selectionStart || 0),
      end: Number(title.selectionEnd ?? title.selectionStart ?? 0),
    };
  }
  const editor = active?.closest?.(".block-editor");
  const row = editor?.closest?.(".block-row");
  const index = Number(row?.dataset?.index);
  const block = Number.isInteger(index) ? State.pageData?.blocks?.[index] : null;
  if (editor && block) {
    const range = selectionOffsets(editor);
    const caret = caretOffset(editor);
    return {
      kind: "block",
      block_id: String(block.id || ""),
      start: Number(range?.start ?? caret),
      end: Number(range?.end ?? caret),
    };
  }
  return State.activeBlockId
    ? { kind: "block", block_id: String(State.activeBlockId), start: 0, end: 0 }
    : { kind: "none" };
}

function restoreEditorHostSelection(selection) {
  if (!selection || typeof selection !== "object") return false;
  if (selection.kind === "title") {
    const title = $("#pageTitle");
    if (!title) return false;
    title.focus({ preventScroll: true });
    const start = Math.max(0, Math.min(title.value.length, Number(selection.start) || 0));
    const end = Math.max(start, Math.min(title.value.length, Number(selection.end) || start));
    title.setSelectionRange?.(start, end);
    return true;
  }
  if (selection.kind !== "block") return false;
  const index = (State.pageData?.blocks || []).findIndex(block => block.id === String(selection.block_id || ""));
  if (index < 0) return false;
  focusBlock(index, Number(selection.end ?? selection.start) || 0);
  return true;
}

function focusEditorHostBlock(blockId, offset = 0) {
  const index = (State.pageData?.blocks || []).findIndex(block => block.id === String(blockId || ""));
  if (index < 0) return false;
  focusBlock(index, offset);
  return true;
}

function applyEditorHostProjection(projection) {
  if (!State.pageData?.page || State.activePageId !== projection.page_id) {
    return { applied: false, reason: "page-mismatch" };
  }
  State.pageData.page.title = String(projection.title || "");
  const projectedBlocks = projection.blocks || [];
  State.pageData.blocks = (
    collaborativeDatabaseSyncPending()
      ? preservePendingDatabaseBlocks(projectedBlocks)
      : DatabaseBridge.applyCanonicalDatabases(
        projectedBlocks,
        State.pageData.databases || [],
      )
  ).map(EditorCore.normalizeBlock);
  if (projection.server_revision != null) State.serverRevision = projection.server_revision;
  const title = $("#pageTitle");
  if (title) title.value = State.pageData.page.title;
  renderBreadcrumb(State.pageData.page);
  renderBlocks();
  return { applied: true };
}

function preservePendingDatabaseBlocks(projectedBlocks) {
  const localById = new Map();
  for (const block of State.pageData?.blocks || []) {
    if (!DATABASE_BLOCK_TYPES.has(block?.type) && !block?.props?.database_id) continue;
    const blockId = String(block?.id || "");
    const databaseId = String(block?.props?.database_id || "");
    if (blockId) localById.set(blockId, block);
    if (databaseId) localById.set(databaseId, block);
  }
  return (projectedBlocks || []).map(block => {
    const databaseId = String(block?.props?.database_id || block?.id || "");
    const local = localById.get(databaseId);
    return local ? EditorCore.deepClone(local) : block;
  });
}

function resetEditorHistory() {
  clearTimeout(State.historyTimer);
  State.historyTimer = null;
  State.historyApplying = false;
  State.history = { entries: [], index: -1 };
  if (State.pageData) State.history = EditorCore.pushHistory(State.history, editorSnapshot());
}

function captureEditorHistory(immediate = false) {
  if (!State.pageData || State.historyApplying || State.pageData.page?.collaborative_editing === true) return;
  clearTimeout(State.historyTimer);
  const capture = () => {
    State.historyTimer = null;
    State.history = EditorCore.pushHistory(State.history, editorSnapshot());
  };
  if (immediate) capture();
  else State.historyTimer = setTimeout(capture, 300);
}

function applyEditorHistory(delta, focusTarget = null) {
  if (!State.pageData) return;
  if (State.pageData.page?.collaborative_editing === true) {
    const changed = delta < 0
      ? CollaborativeEditorBridge.undo()
      : CollaborativeEditorBridge.redo();
    if (changed) {
      State.editRevision += 1;
      setStatus(delta < 0 ? "실행 취소" : "다시 실행", "ok");
    }
    return;
  }
  if (delta < 0 || State.historyTimer) captureEditorHistory(true);
  const stepped = EditorCore.historyStep(State.history, delta);
  if (!stepped.snapshot || stepped.history.index === State.history.index) return;
  State.historyApplying = true;
  State.history = stepped.history;
  State.pageData.blocks = (stepped.snapshot.blocks || []).map(EditorCore.normalizeBlock);
  State.pageData.page.title = stepped.snapshot.title || "제목 없음";
  $("#pageTitle").value = stepped.snapshot.title || "제목 없음";
  State.selectedBlockIds.clear();
  State.dirty = true;
  renderBlocks();
  scheduleSave();
  setStatus(delta < 0 ? "실행 취소" : "다시 실행", "ok");
  State.historyApplying = false;
  if (focusTarget?.title) {
    setTimeout(() => {
      const title = $("#pageTitle");
      if (!title) return;
      title.focus();
      const offset = Math.max(0, Math.min(title.value.length, Number(focusTarget.offset) || 0));
      title.setSelectionRange?.(offset, offset);
    }, 0);
  } else if (Number.isInteger(focusTarget?.blockIndex)) {
    const index = Math.max(0, Math.min(State.pageData.blocks.length - 1, focusTarget.blockIndex));
    const content = State.pageData.blocks[index]?.content || "";
    focusBlock(index, Math.min(content.length, Number(focusTarget.offset) || 0));
  }
}

function selectedBlockIndices(fallbackIndex = -1) {
  const blocks = State.pageData?.blocks || [];
  const selected = blocks
    .map((block, index) => State.selectedBlockIds.has(block.id) ? index : -1)
    .filter(index => index >= 0);
  if (!selected.length && fallbackIndex >= 0 && fallbackIndex < blocks.length) return [fallbackIndex];
  return selected;
}

function selectBlockHandle(index, extend = false) {
  const blocks = State.pageData?.blocks || [];
  const block = blocks[index];
  if (!block) return;
  if (extend && State.activeBlockId) {
    const anchor = blocks.findIndex(item => item.id === State.activeBlockId);
    if (anchor >= 0) {
      State.selectedBlockIds.clear();
      const [start, end] = [anchor, index].sort((a, b) => a - b);
      for (let i = start; i <= end; i += 1) State.selectedBlockIds.add(blocks[i].id);
    }
  } else {
    State.selectedBlockIds = new Set([block.id]);
    State.activeBlockId = block.id;
  }
  document.querySelectorAll(".block-row").forEach(row => {
    row.classList.toggle("selected", State.selectedBlockIds.has(row.dataset.blockId));
  });
}

function toggleBlockSelectionAt(index) {
  const block = State.pageData?.blocks?.[index];
  if (!block) return;
  if (State.selectedBlockIds.has(block.id)) State.selectedBlockIds.delete(block.id);
  else State.selectedBlockIds.add(block.id);
  State.activeBlockId = block.id;
  document.querySelectorAll(".block-row").forEach(row => {
    row.classList.toggle("selected", State.selectedBlockIds.has(row.dataset.blockId));
  });
}

function clearBlockSelection() {
  State.selectedBlockIds.clear();
  document.querySelectorAll(".block-row.selected").forEach(row => row.classList.remove("selected"));
}

function selectedBlocksPlainText() {
  const blocks = State.pageData?.blocks || [];
  return selectedBlockIndices().map(index => {
    const block = blocks[index];
    const content = block?.content || "";
    if (block?.type === "heading1") return `# ${content}`;
    if (block?.type === "heading2") return `## ${content}`;
    if (block?.type === "heading3") return `### ${content}`;
    if (block?.type === "toggle_heading1") return `# ${content}`;
    if (block?.type === "toggle_heading2") return `## ${content}`;
    if (block?.type === "toggle_heading3") return `### ${content}`;
    if (block?.type === "todo") return `- [${block.props?.checked ? "x" : " "}] ${content}`;
    if (block?.type === "bullet") return `- ${content}`;
    if (block?.type === "numbered") return `1. ${content}`;
    if (block?.type === "quote") return `> ${content}`;
    if (block?.type === "divider") return "---";
    return content;
  }).join("\n");
}

async function copySelectedBlocks(removeAfter = false) {
  const indices = selectedBlockIndices();
  if (!indices.length) return;
  if (removeAfter && selectionContainsDatabase(indices)) return;
  await copyText(selectedBlocksPlainText(), removeAfter ? "블록 잘라냄" : "블록 복사됨");
  if (removeAfter) {
    State.pageData.blocks = EditorCore.removeBlocks(State.pageData.blocks, indices);
    clearBlockSelection();
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  }
}

function selectionContainsDatabase(indices) {
  return (indices || []).some(
    index => DATABASE_BLOCK_TYPES.has(State.pageData?.blocks?.[index]?.type),
  );
}

function databaseStructureMutationAllowed(indices) {
  return !selectionContainsDatabase(indices);
}

function moveSelectedBlocks(delta) {
  const blocks = State.pageData?.blocks || [];
  const selected = new Set(State.selectedBlockIds);
  if (!selected.size || !delta) return false;
  if (delta < 0) {
    for (let index = 1; index < blocks.length; index += 1) {
      if (selected.has(blocks[index].id) && !selected.has(blocks[index - 1].id)) {
        [blocks[index - 1], blocks[index]] = [blocks[index], blocks[index - 1]];
      }
    }
  } else {
    for (let index = blocks.length - 2; index >= 0; index -= 1) {
      if (selected.has(blocks[index].id) && !selected.has(blocks[index + 1].id)) {
        [blocks[index], blocks[index + 1]] = [blocks[index + 1], blocks[index]];
      }
    }
  }
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  return true;
}

function sanitizeRichHtml(value) {
  const template = document.createElement("template");
  template.innerHTML = String(value || "");
  const allowed = new Set(["B", "STRONG", "I", "EM", "U", "S", "STRIKE", "CODE", "A", "BR", "SPAN"]);
  const blocked = new Set(["SCRIPT", "STYLE", "IFRAME", "OBJECT", "EMBED", "LINK", "META", "IMG", "SVG", "MATH"]);
  [...template.content.querySelectorAll("*")].reverse().forEach(el => {
    if (blocked.has(el.tagName)) {
      el.remove();
      return;
    }
    if (!allowed.has(el.tagName)) {
      el.replaceWith(...el.childNodes);
      return;
    }
    const rawHref = el.tagName === "A" ? (el.getAttribute("href") || "") : "";
    const notionDate = el.tagName === "SPAN" ? (el.getAttribute("data-notion-date") || "") : "";
    const notionUser = el.tagName === "SPAN" ? (el.getAttribute("data-notion-user") || "") : "";
    const notionMentionId = el.tagName === "SPAN" ? (el.getAttribute("data-notion-mention-id") || "") : "";
    const notionReminder = el.tagName === "SPAN" ? (el.getAttribute("data-notion-reminder") || "") : "";
    const notionReminderId = el.tagName === "SPAN" ? (el.getAttribute("data-notion-reminder-id") || "") : "";
    const notionReminderUser = el.tagName === "SPAN" ? (el.getAttribute("data-notion-reminder-user") || "") : "";
    const notionReminderLead = el.tagName === "SPAN" ? (el.getAttribute("data-notion-reminder-lead") || "") : "";
    [...el.attributes].forEach(attr => el.removeAttribute(attr.name));
    if (/^\d{4}-\d{2}-\d{2}$/.test(notionDate)) el.setAttribute("data-notion-date", notionDate);
    if (/^[a-zA-Z0-9._@+\-]{1,200}$/.test(notionUser)) el.setAttribute("data-notion-user", notionUser);
    if (/^[a-zA-Z0-9_-]{1,120}$/.test(notionMentionId)) el.setAttribute("data-notion-mention-id", notionMentionId);
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(notionReminder)) el.setAttribute("data-notion-reminder", notionReminder);
    if (/^[a-zA-Z0-9_-]{1,120}$/.test(notionReminderId)) el.setAttribute("data-notion-reminder-id", notionReminderId);
    if (/^[a-zA-Z0-9._@+\-]{1,200}$/.test(notionReminderUser)) el.setAttribute("data-notion-reminder-user", notionReminderUser);
    if (/^\d{1,6}$/.test(notionReminderLead)) el.setAttribute("data-notion-reminder-lead", notionReminderLead);
    if (el.tagName === "A") {
      const href = normalizeSafeRichTextUrl(rawHref);
      if (href) {
        el.setAttribute("href", href);
        el.setAttribute("target", "_blank");
        el.setAttribute("rel", "noopener noreferrer");
      } else {
        el.replaceWith(...el.childNodes);
      }
    }
  });
  return template.innerHTML;
}

function blockRichHtml(block) {
  const stored = block.props?.rich_text_html;
  if (stored) {
    const template = document.createElement("template");
    template.innerHTML = sanitizeRichHtml(stored);
    template.content.querySelectorAll("a[href]").forEach(anchor => {
      try {
        const url = new URL(anchor.getAttribute("href"), location.origin);
        const match = url.origin === location.origin && url.pathname.match(/^\/page\/(.+)$/);
        if (!match) return;
        const pageId = decodeURIComponent(match[1]);
        const page = State.pages.find(item => item.id === pageId);
        if (page) anchor.textContent = page.title || "제목 없음";
        anchor.classList.add("page-mention");
        anchor.removeAttribute("target");
      } catch {}
    });
    template.content.querySelectorAll("span[data-notion-date]").forEach(span => {
      const value = span.getAttribute("data-notion-date") || "";
      const date = new Date(`${value}T12:00:00`);
      const today = new Date();
      const day = new Date(today.getFullYear(), today.getMonth(), today.getDate(), 12);
      const diff = Math.round((date.getTime() - day.getTime()) / 86400000);
      span.textContent = diff === 0 ? "오늘" : (diff === 1 ? "내일" : (diff === -1 ? "어제" : date.toLocaleDateString()));
      span.classList.add("date-mention");
    });
    template.content.querySelectorAll("span[data-notion-user]").forEach(span => {
      const userId = span.getAttribute("data-notion-user") || "";
      const people = [State.user, ...State.users].filter(Boolean);
      const person = people.find(item => item.id === userId);
      span.textContent = `@${person?.display_name || person?.id || userId}`;
      span.classList.add("user-mention");
    });
    template.content.querySelectorAll("span[data-notion-reminder]").forEach(span => {
      const value = span.getAttribute("data-notion-reminder") || "";
      const lead = Math.max(0, Number(span.getAttribute("data-notion-reminder-lead") || 0));
      const date = new Date(value);
      const trigger = new Date(date.getTime() - lead * 60000);
      span.textContent = formatInlineReminder(value);
      span.classList.add("reminder-mention");
      span.classList.toggle("due", Number.isFinite(trigger.getTime()) && trigger.getTime() <= Date.now());
    });
    return template.innerHTML;
  }
  return escapeHtml(block.content || "").replace(/\n/g, "<br>");
}

function formatInlineReminder(value) {
  const date = new Date(String(value || ""));
  if (!Number.isFinite(date.getTime())) return "@리마인더";
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diff = Math.round((target.getTime() - today.getTime()) / 86400000);
  const day = diff === 0 ? "오늘" : diff === 1 ? "내일" : diff === -1 ? "어제" : date.toLocaleDateString();
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return `@${day} ${time}`;
}

function editorPlainText(editor) {
  return String(editor?.innerText || "").replace(/\u00a0/g, " ").replace(/\n$/, "");
}

function syncEditorBlock(editor, block) {
  if (!editor || !block) return;
  block.content = editorPlainText(editor);
  block.props = block.props || {};
  if (block.type === "equation") {
    delete block.props.rich_text_html;
    const preview = editor.closest(".block-row")?.querySelector(".equation-preview");
    if (preview && window.NotionMath) preview.innerHTML = window.NotionMath.renderHtml(block.content);
    return;
  }
  const html = sanitizeRichHtml(editor.innerHTML);
  const plainHtml = escapeHtml(block.content).replace(/\n/g, "<br>");
  if (html && html !== plainHtml && html !== block.content) block.props.rich_text_html = html;
  else delete block.props.rich_text_html;
}

function caretOffset(editor) {
  const selection = window.getSelection();
  if (!selection?.rangeCount || !editor.contains(selection.anchorNode)) return 0;
  const range = selection.getRangeAt(0).cloneRange();
  range.selectNodeContents(editor);
  range.setEnd(selection.anchorNode, selection.anchorOffset);
  return range.toString().length;
}

function selectionIsCollapsed(editor) {
  const selection = window.getSelection();
  return Boolean(selection?.isCollapsed && selection.rangeCount && editor.contains(selection.anchorNode));
}

function selectionOffsets(editor) {
  const selection = window.getSelection();
  if (!selection?.rangeCount || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!editor.contains(range.startContainer) || !editor.contains(range.endContainer)) return null;
  const startRange = range.cloneRange();
  startRange.selectNodeContents(editor);
  startRange.setEnd(range.startContainer, range.startOffset);
  const endRange = range.cloneRange();
  endRange.selectNodeContents(editor);
  endRange.setEnd(range.endContainer, range.endOffset);
  const start = startRange.toString().length;
  const end = endRange.toString().length;
  return { start, end, text: range.toString().slice(0, 1000) };
}

function setCaretOffset(editor, offset = 0) {
  if (!editor) return;
  const target = Math.max(0, Number(offset) || 0);
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  let remaining = target;
  let node = walker.nextNode();
  while (node) {
    const length = node.nodeValue?.length || 0;
    if (remaining <= length) {
      const range = document.createRange();
      range.setStart(node, remaining);
      range.collapse(true);
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      return;
    }
    remaining -= length;
    node = walker.nextNode();
  }
  const range = document.createRange();
  range.selectNodeContents(editor);
  range.collapse(false);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function textPointAtOffset(editor, offset) {
  const target = Math.max(0, Number(offset) || 0);
  const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
  let remaining = target;
  let node = walker.nextNode();
  while (node) {
    const length = node.nodeValue?.length || 0;
    if (remaining <= length) return { node, offset: remaining };
    remaining -= length;
    node = walker.nextNode();
  }
  if (!editor.lastChild || editor.lastChild.nodeType !== Node.TEXT_NODE) editor.appendChild(document.createTextNode(""));
  const last = editor.lastChild;
  return { node: last, offset: last.nodeValue?.length || 0 };
}

function closeMentionMenu() {
  $("#mentionMenu").hidden = true;
  State.mention = { blockIndex: -1, start: -1, end: -1, selectedIndex: 0 };
}

function mentionButtons() {
  return [...document.querySelectorAll("#mentionMenu [data-mention-user], #mentionMenu [data-mention-page], #mentionMenu [data-mention-date], #mentionMenu [data-mention-reminder]")];
}

function refreshMentionSelection() {
  const buttons = mentionButtons();
  if (!buttons.length) return;
  State.mention.selectedIndex = Math.max(0, Math.min(buttons.length - 1, State.mention.selectedIndex));
  buttons.forEach((button, index) => button.classList.toggle("menu-active", index === State.mention.selectedIndex));
  buttons[State.mention.selectedIndex]?.scrollIntoView({ block: "nearest" });
}

function moveMentionSelection(delta) {
  const buttons = mentionButtons();
  if (!buttons.length) return;
  State.mention.selectedIndex = (State.mention.selectedIndex + delta + buttons.length) % buttons.length;
  refreshMentionSelection();
}

function insertPageMention(pageId) {
  const state = { ...State.mention };
  const editor = document.querySelector(`.block-row[data-index="${state.blockIndex}"] .block-editor`);
  const page = State.pages.find(item => item.id === pageId);
  const block = State.pageData?.blocks?.[state.blockIndex];
  if (!editor || !page || !block) return closeMentionMenu();
  editor.focus();
  const start = textPointAtOffset(editor, state.start);
  const end = textPointAtOffset(editor, state.end);
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  range.deleteContents();
  const anchor = document.createElement("a");
  anchor.href = `/page/${encodeURIComponent(page.id)}`;
  anchor.className = "page-mention";
  anchor.textContent = page.title || "제목 없음";
  const space = document.createTextNode("\u00a0");
  range.insertNode(space);
  range.insertNode(anchor);
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStartAfter(space);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  syncEditorBlock(editor, block);
  markDirty();
  closeMentionMenu();
}

function insertUserMention(userId) {
  const state = { ...State.mention };
  const editor = document.querySelector(`.block-row[data-index="${state.blockIndex}"] .block-editor`);
  const people = [State.user, ...State.users].filter(Boolean);
  const person = people.find(item => item.id === userId);
  const block = State.pageData?.blocks?.[state.blockIndex];
  if (!editor || !person || !block) return closeMentionMenu();
  editor.focus();
  const start = textPointAtOffset(editor, state.start);
  const end = textPointAtOffset(editor, state.end);
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  range.deleteContents();
  const span = document.createElement("span");
  span.dataset.notionUser = person.id;
  span.dataset.notionMentionId = `mention_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
  span.className = "user-mention";
  span.textContent = `@${person.display_name || person.id}`;
  const space = document.createTextNode("\u00a0");
  range.insertNode(space);
  range.insertNode(span);
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStartAfter(space);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  syncEditorBlock(editor, block);
  markDirty();
  closeMentionMenu();
}

function insertDateMention(value) {
  const state = { ...State.mention };
  const editor = document.querySelector(`.block-row[data-index="${state.blockIndex}"] .block-editor`);
  const block = State.pageData?.blocks?.[state.blockIndex];
  if (!editor || !block || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return closeMentionMenu();
  editor.focus();
  const start = textPointAtOffset(editor, state.start);
  const end = textPointAtOffset(editor, state.end);
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  range.deleteContents();
  const span = document.createElement("span");
  span.dataset.notionDate = value;
  span.className = "date-mention";
  span.textContent = value;
  const space = document.createTextNode("\u00a0");
  range.insertNode(space);
  range.insertNode(span);
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStartAfter(space);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  syncEditorBlock(editor, block);
  markDirty();
  closeMentionMenu();
}

function reminderPeopleForEditor(editor) {
  const mentionedIds = new Set([
    State.user?.id || "",
    ...[...(editor?.querySelectorAll?.("[data-notion-user]") || [])].map(span => span.dataset.notionUser || ""),
  ]);
  return [State.user, ...State.users]
    .filter((person, index, all) => person?.id && mentionedIds.has(person.id) && all.findIndex(candidate => candidate?.id === person.id) === index);
}

function insertReminderMention(value, leadMinutes, userId) {
  const state = { ...State.mention };
  const editor = document.querySelector(`.block-row[data-index="${state.blockIndex}"] .block-editor`);
  const block = State.pageData?.blocks?.[state.blockIndex];
  if (!editor || !block || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
    closeModal();
    return closeMentionMenu();
  }
  editor.focus();
  const start = textPointAtOffset(editor, state.start);
  const end = textPointAtOffset(editor, state.end);
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  range.deleteContents();
  const span = document.createElement("span");
  span.dataset.notionReminder = value;
  span.dataset.notionReminderLead = String(Math.max(0, Number(leadMinutes) || 0));
  span.dataset.notionReminderUser = userId || State.user?.id || "";
  span.dataset.notionReminderId = `reminder_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
  span.className = "reminder-mention";
  span.textContent = formatInlineReminder(value);
  const space = document.createTextNode("\u00a0");
  range.insertNode(space);
  range.insertNode(span);
  const selection = window.getSelection();
  const caret = document.createRange();
  caret.setStartAfter(space);
  caret.collapse(true);
  selection.removeAllRanges();
  selection.addRange(caret);
  syncEditorBlock(editor, block);
  closeModal();
  closeMentionMenu();
  markDirty();
  loadInboxItems(false);
}

function openInlineReminderDialog(existingSpan = null, editor = null, block = null) {
  if (!activePageCanEdit()) return;
  if (!existingSpan) {
    const state = { ...State.mention };
    editor = document.querySelector(`.block-row[data-index="${state.blockIndex}"] .block-editor`);
    block = State.pageData?.blocks?.[state.blockIndex];
  }
  if (!editor || !block) return;
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  tomorrow.setHours(9, 0, 0, 0);
  const localValue = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, "0")}-${String(tomorrow.getDate()).padStart(2, "0")}T09:00`;
  const currentValue = existingSpan?.dataset.notionReminder || localValue;
  const currentLead = existingSpan?.dataset.notionReminderLead || "0";
  const people = reminderPeopleForEditor(editor);
  const currentUser = existingSpan?.dataset.notionReminderUser || State.user?.id || people[0]?.id || "";
  $("#mentionMenu").hidden = true;
  openModal(existingSpan ? "리마인더 편집" : "리마인더 만들기", `
    <div class="database-rule-form reminder-form">
      <label>날짜와 시간<input id="reminderDateTime" type="datetime-local" value="${escapeAttr(currentValue)}"></label>
      <label>알림 시점<select id="reminderLead"><option value="0" ${currentLead === "0" ? "selected" : ""}>정각</option><option value="10" ${currentLead === "10" ? "selected" : ""}>10분 전</option><option value="30" ${currentLead === "30" ? "selected" : ""}>30분 전</option><option value="60" ${currentLead === "60" ? "selected" : ""}>1시간 전</option><option value="1440" ${currentLead === "1440" ? "selected" : ""}>1일 전</option><option value="10080" ${currentLead === "10080" ? "selected" : ""}>1주 전</option></select></label>
      <label>알림 받을 사람<select id="reminderUser">${people.map(person => `<option value="${escapeAttr(person.id)}" ${person.id === currentUser ? "selected" : ""}>${escapeHtml(person.display_name || person.id)}</option>`).join("")}</select></label>
    </div>
    <p class="panel-help">다른 사람에게 알리려면 같은 블록에서 먼저 그 사람을 @멘션하세요.</p>
  `, [
    ...(existingSpan ? [{ label: "리마인더 삭제", action: () => {
      existingSpan.remove();
      syncEditorBlock(editor, block);
      closeModal();
      markDirty();
      renderBlocks();
      loadInboxItems(false);
    } }] : []),
    { label: "취소", action: () => { closeModal(); if (!existingSpan) closeMentionMenu(); } },
    { label: "저장", primary: true, action: () => {
      const value = $("#reminderDateTime").value;
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
        setStatus("리마인더 날짜와 시간을 선택해 주세요.", "error");
        return;
      }
      const lead = $("#reminderLead").value || "0";
      const userId = $("#reminderUser").value || State.user?.id || "";
      if (existingSpan) {
        existingSpan.dataset.notionReminder = value;
        existingSpan.dataset.notionReminderLead = lead;
        existingSpan.dataset.notionReminderUser = userId;
        existingSpan.textContent = formatInlineReminder(value);
        syncEditorBlock(editor, block);
        closeModal();
        markDirty();
        renderBlocks();
        loadInboxItems(false);
      } else {
        insertReminderMention(value, lead, userId);
      }
    } },
  ]);
}

function openDatabaseDateReminderDialog(block, rowIndex, field, onChange = renderBlocks) {
  if (!activePageCanEdit()) return;
  const item = databaseRows(block)[rowIndex];
  if (!item || !field) return;
  const key = `${field}_reminder`;
  const current = item[key] && typeof item[key] === "object" ? item[key] : null;
  const rawDate = item[field] && typeof item[field] === "object" ? item[field].start : item[field];
  const selectedDate = /^\d{4}-\d{2}-\d{2}$/.test(String(rawDate || "").slice(0, 10))
    ? String(rawDate).slice(0, 10)
    : localIsoDate(new Date());
  const initial = current?.at || `${selectedDate}T09:00`;
  const lead = String(current?.lead || 0);
  openModal(current ? "날짜 리마인더 편집" : "날짜 리마인더", `
    <div class="database-rule-form reminder-form">
      <label>날짜와 시간<input id="dbReminderDateTime" type="datetime-local" value="${escapeAttr(initial)}"></label>
      <label>알림 시점<select id="dbReminderLead"><option value="0" ${lead === "0" ? "selected" : ""}>정각</option><option value="10" ${lead === "10" ? "selected" : ""}>10분 전</option><option value="30" ${lead === "30" ? "selected" : ""}>30분 전</option><option value="60" ${lead === "60" ? "selected" : ""}>1시간 전</option><option value="1440" ${lead === "1440" ? "selected" : ""}>1일 전</option><option value="10080" ${lead === "10080" ? "selected" : ""}>1주 전</option></select></label>
    </div>
  `, [
    ...(current ? [{ label: "리마인더 삭제", action: () => {
      delete item[key];
      closeModal();
      markDirty();
      captureEditorHistory(true);
      onChange();
      loadInboxItems(false);
    } }] : []),
    { label: "취소", action: closeModal },
    { label: "저장", primary: true, action: () => {
      const value = $("#dbReminderDateTime").value;
      if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
        setStatus("리마인더 날짜와 시간을 선택해 주세요.", "error");
        return;
      }
      item[field] = value.slice(0, 10);
      item[key] = {
        id: current?.id || `dbreminder_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`,
        at: value,
        lead: Number($("#dbReminderLead").value || 0),
        user_id: State.user?.id || "",
      };
      item._last_edited_time = new Date().toISOString();
      item._last_edited_by = State.user?.id || "";
      closeModal();
      markDirty();
      captureEditorHistory(true);
      onChange();
      loadInboxItems(false);
    } },
  ]);
}

function openMentionMenu(index, row, query, start, end) {
  closeBlockMenu();
  const menu = $("#mentionMenu");
  const needle = String(query || "").toLowerCase();
  const pages = visiblePages()
    .filter(page => page.id !== State.activePageId && (!needle || String(page.title || "").toLowerCase().includes(needle)))
    .slice(0, 12);
  const people = [State.user, ...State.users]
    .filter((person, personIndex, all) => person?.id && all.findIndex(candidate => candidate?.id === person.id) === personIndex)
    .filter(person => !needle || String(person.display_name || person.id).toLowerCase().includes(needle))
    .slice(0, 12);
  const base = new Date();
  const dateCandidates = [
    ["어제", -1, "yesterday"], ["오늘", 0, "today"], ["내일", 1, "tomorrow"],
  ].filter(([label, _delta, english]) => !needle || label.includes(needle) || english.startsWith(needle)).map(([label, delta]) => {
    const date = new Date(base.getFullYear(), base.getMonth(), base.getDate() + delta, 12);
    const value = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
    return { label, value };
  });
  const showReminder = !needle || "remind".startsWith(needle) || "리마인더".startsWith(needle);
  const rect = row.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - 320, rect.left + 44))}px`;
  menu.style.top = `${Math.min(window.innerHeight - 360, rect.bottom + 4)}px`;
  menu.innerHTML = `
    ${people.length ? `<div class="menu-label">사람</div>${people.map(person => `
      <button type="button" class="menu-item" data-mention-user="${escapeAttr(person.id)}">
        <span class="mention-avatar">${escapeHtml(String(person.display_name || person.id).slice(0, 1).toUpperCase())}</span><span>${escapeHtml(person.display_name || person.id)}</span><small>${person.id === State.user?.id ? "나" : "사람"}</small>
      </button>
    `).join("")}` : ""}
    <div class="menu-label">페이지 멘션</div>
    ${pages.length ? pages.map(page => `
      <button type="button" class="menu-item" data-mention-page="${escapeAttr(page.id)}">
        <span>${pageIconHtml(page)}</span><span>${escapeHtml(page.title || "제목 없음")}</span><small>${escapeHtml(page.scope === "shared" ? "공유됨" : "페이지")}</small>
      </button>
    `).join("") : (!dateCandidates.length ? '<div class="menu-empty">일치하는 멘션이 없습니다.</div>' : "")}
    ${dateCandidates.length ? `<div class="menu-label">날짜</div>${dateCandidates.map(item => `
      <button type="button" class="menu-item" data-mention-date="${item.value}"><span>▣</span><span>${item.label}</span><small>${item.value}</small></button>
    `).join("")}` : ""}
    ${showReminder ? '<div class="menu-label">리마인더</div><button type="button" class="menu-item" data-mention-reminder><span>⏰</span><span>리마인더 만들기</span><small>@remind</small></button>' : ""}
  `;
  State.mention = { blockIndex: index, start, end, selectedIndex: 0 };
  menu.hidden = false;
  menu.querySelectorAll("[data-mention-user]").forEach(button => {
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => insertUserMention(button.dataset.mentionUser));
  });
  menu.querySelectorAll("[data-mention-page]").forEach(button => {
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => insertPageMention(button.dataset.mentionPage));
  });
  menu.querySelectorAll("[data-mention-date]").forEach(button => {
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => insertDateMention(button.dataset.mentionDate));
  });
  menu.querySelector("[data-mention-reminder]")?.addEventListener("mousedown", event => event.preventDefault());
  menu.querySelector("[data-mention-reminder]")?.addEventListener("click", () => openInlineReminderDialog());
  refreshMentionSelection();
}

function focusBlock(index, offset = 0) {
  const row = document.querySelector(`.block-row[data-index="${index}"]`);
  const editor = row?.querySelector(".block-editor");
  if (!editor) {
    const structuredTarget = row?.querySelector("[data-equation-trigger], .database-block [data-db-search]");
    structuredTarget?.focus({ preventScroll: true });
    return;
  }
  setTimeout(() => {
    if (!editor.isConnected) return;
    editor.focus();
    setCaretOffset(editor, offset);
  }, 0);
}

function applyInlineFormat(command, value = null) {
  document.execCommand(command, false, value);
  const editor = document.activeElement?.closest?.(".block-editor");
  if (!editor) return;
  const index = Number(editor.closest(".block-row")?.dataset.index);
  const block = State.pageData?.blocks?.[index];
  syncEditorBlock(editor, block);
  markDirty();
}

function updateInlineToolbar() {
  const toolbar = $("#inlineToolbar");
  const selection = window.getSelection();
  if (!selection?.rangeCount || selection.isCollapsed) {
    if (!toolbar.matches(":hover")) {
      toolbar.hidden = true;
      State.inlineSelection = null;
    }
    return;
  }
  const anchorElement = selection.anchorNode?.nodeType === Node.ELEMENT_NODE
    ? selection.anchorNode
    : selection.anchorNode?.parentElement;
  const editor = anchorElement?.closest?.(".block-editor");
  if (!editor || !editor.contains(selection.focusNode) || !activePageCanEdit()) {
    toolbar.hidden = true;
    State.inlineSelection = null;
    return;
  }
  const row = editor.closest(".block-row");
  const range = selection.getRangeAt(0).cloneRange();
  const rect = range.getBoundingClientRect();
  if (!rect.width && !rect.height) return;
  State.inlineSelection = { editor, index: Number(row?.dataset.index), range };
  toolbar.hidden = false;
  const width = toolbar.offsetWidth || 260;
  toolbar.style.left = `${Math.max(8, Math.min(window.innerWidth - width - 8, rect.left + rect.width / 2 - width / 2))}px`;
  toolbar.style.top = `${Math.max(8, rect.top - (toolbar.offsetHeight || 38) - 8)}px`;
}

function restoreInlineSelection() {
  const saved = State.inlineSelection;
  if (!saved?.editor?.isConnected || !saved.range) return null;
  saved.editor.focus();
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(saved.range);
  return saved;
}

function applyToolbarFormat(action) {
  const saved = restoreInlineSelection();
  if (!saved) return;
  const block = State.pageData?.blocks?.[saved.index];
  if (!block) return;
  if (action === "code") {
    toggleInlineCode();
  } else if (action === "link") {
    const value = window.prompt("링크 URL", "https://");
    if (value) applyInlineFormat("createLink", normalizeUrl(value));
  } else if (action === "comment") {
    State.commentBlockId = block.id || "";
    State.commentSelection = selectionOffsets(saved.editor);
    $("#inlineToolbar").hidden = true;
    openDrawer("comments");
    setTimeout(() => $("#commentInput")?.focus(), 0);
  } else {
    applyInlineFormat(action);
  }
  syncEditorBlock(saved.editor, block);
}

function toggleInlineCode() {
  const selection = window.getSelection();
  const editor = document.activeElement?.closest?.(".block-editor");
  if (!selection?.rangeCount || selection.isCollapsed || !editor || !editor.contains(selection.anchorNode)) return;
  const range = selection.getRangeAt(0);
  const startElement = range.startContainer.nodeType === Node.ELEMENT_NODE
    ? range.startContainer
    : range.startContainer.parentElement;
  const existing = startElement?.closest?.("code");
  if (existing && editor.contains(existing) && existing.contains(range.endContainer)) {
    const parent = existing.parentNode;
    while (existing.firstChild) parent.insertBefore(existing.firstChild, existing);
    existing.remove();
  } else {
    const code = document.createElement("code");
    code.appendChild(range.extractContents());
    range.insertNode(code);
    const nextRange = document.createRange();
    nextRange.selectNodeContents(code);
    selection.removeAllRanges();
    selection.addRange(nextRange);
  }
  const index = Number(editor.closest(".block-row")?.dataset.index);
  syncEditorBlock(editor, State.pageData?.blocks?.[index]);
  markDirty();
}

function insertSoftBreak(editor, block) {
  if (!document.execCommand("insertLineBreak", false, null)) {
    document.execCommand("insertHTML", false, "<br>");
  }
  syncEditorBlock(editor, block);
  markDirty();
}

function scrollToLocationBlock() {
  const hash = decodeURIComponent(location.hash || "");
  if (!hash.startsWith("#block-")) return;
  const target = document.getElementById(hash.slice(1));
  if (!target) return;
  target.scrollIntoView({ block: "center" });
  target.classList.add("block-linked");
  setTimeout(() => target.classList.remove("block-linked"), 1400);
}

function applyMarkdownShortcut(editor, block, index) {
  const shortcut = EditorCore.markdownShortcut(editorPlainText(editor));
  if (!shortcut) return false;
  block.type = shortcut.type;
  block.content = shortcut.content;
  const layout = block.props?.layout_group ? {
    layout_group: block.props.layout_group,
    layout_column: Number(block.props.layout_column || 0),
    layout_columns: Number(block.props.layout_columns || 2),
  } : {};
  block.props = { ...EditorCore.createBlock(shortcut.type).props, ...layout };
  if (shortcut.type === "divider") {
    State.pageData.blocks.splice(index + 1, 0, EditorCore.createBlock("paragraph"));
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    focusBlock(index + 1, 0);
    return true;
  }
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  focusBlock(index, 0);
  return true;
}

function pasteMultilineAt(index, editor, text) {
  const blocks = State.pageData?.blocks || [];
  const block = blocks[index];
  if (!block) return;
  const offset = caretOffset(editor);
  const content = editorPlainText(editor);
  const before = content.slice(0, offset);
  const after = content.slice(offset);
  const pasted = EditorCore.blocksFromPlainText(text);
  if (!pasted.length) return;
  block.content = before + pasted[0].content;
  block.props = block.props || {};
  delete block.props.rich_text_html;
  const tail = pasted.slice(1);
  if (!tail.length) {
    block.content += after;
    markDirty();
    renderBlocks();
    focusBlock(index, before.length + pasted[0].content.length);
    return;
  }
  tail[tail.length - 1].content += after;
  blocks.splice(index + 1, 0, ...tail);
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  focusBlock(index + tail.length, tail[tail.length - 1].content.length - after.length);
}

function bindBlockDrag(row, block, index, canEdit) {
  const handle = row.querySelector(".block-handle");
  if (!handle || !canEdit) return;
  handle.draggable = true;
  handle.addEventListener("dragstart", event => {
    const ids = State.selectedBlockIds.has(block.id) ? [...State.selectedBlockIds] : [block.id];
    State.blockDrag = { id: block.id, ids, overId: "", position: "" };
    event.dataTransfer.effectAllowed = event.altKey ? "copyMove" : "move";
    event.dataTransfer.setData("application/x-notion-blocks", JSON.stringify(ids));
    event.dataTransfer.setData("text/plain", block.content || blockTypeMeta(block.type)[1]);
    row.classList.add("dragging");
  });
  row.addEventListener("dragover", event => {
    if (!State.blockDrag.id || State.blockDrag.ids?.includes(block.id)) return;
    event.preventDefault();
    const rect = row.getBoundingClientRect();
    const position = event.clientY < rect.top + rect.height / 2 ? "before" : "after";
    document.querySelectorAll(".block-row.drop-before, .block-row.drop-after").forEach(item => item.classList.remove("drop-before", "drop-after"));
    row.classList.add(position === "before" ? "drop-before" : "drop-after");
    State.blockDrag.overId = block.id;
    State.blockDrag.position = position;
  });
  row.addEventListener("drop", event => {
    if (!State.blockDrag.id || !State.blockDrag.overId) return;
    event.preventDefault();
    const blocks = State.pageData.blocks;
    const draggedIds = State.blockDrag.ids || [State.blockDrag.id];
    const moving = blocks.filter(item => draggedIds.includes(item.id));
    let remaining = blocks.filter(item => !draggedIds.includes(item.id));
    let target = remaining.findIndex(item => item.id === State.blockDrag.overId);
    if (target < 0) return;
    if (State.blockDrag.position === "after") target += 1;
    const inserted = event.altKey
      ? moving.map(item => EditorCore.createBlock(item.type, { content: item.content, props: item.props }))
      : moving;
    remaining.splice(target, 0, ...inserted);
    State.pageData.blocks = remaining;
    State.selectedBlockIds = new Set(inserted.map(item => item.id));
    State.blockDrag = { id: "", ids: [], overId: "", position: "" };
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  handle.addEventListener("dragend", () => {
    State.blockDrag = { id: "", ids: [], overId: "", position: "" };
    document.querySelectorAll(".block-row.dragging, .block-row.drop-before, .block-row.drop-after").forEach(item => item.classList.remove("dragging", "drop-before", "drop-after"));
  });
}

function addBlockToColumn(groupId, columnIndex, columnCount) {
  if (!activePageCanEdit() || !groupId) return;
  const blocks = State.pageData?.blocks || [];
  const members = blocks
    .map((block, index) => ({ block, index }))
    .filter(({ block }) => block.props?.layout_group === groupId);
  if (!members.length) return;
  const sameColumn = members.filter(({ block }) => Number(block.props?.layout_column || 0) === columnIndex);
  const nextColumn = members.find(({ block }) => Number(block.props?.layout_column || 0) > columnIndex);
  const insertAt = sameColumn.length
    ? sameColumn[sameColumn.length - 1].index + 1
    : (nextColumn?.index ?? members[members.length - 1].index + 1);
  const block = EditorCore.createBlock("paragraph", {
    props: { layout_group: groupId, layout_column: columnIndex, layout_columns: columnCount },
  });
  blocks.splice(insertAt, 0, block);
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  focusBlock(insertAt, 0);
}

function commitBlockEditorInput(editor, block, index, row) {
  syncEditorBlock(editor, block);
  syncSyncedBlockPeers(block, index);
  if (block.type === "equation") {
    markDirty();
    return;
  }
  if (applyMarkdownShortcut(editor, block, index)) return;
  const offset = caretOffset(editor);
  const mention = block.content.slice(0, offset).match(/(?:^|\s)@([^\s@]{0,80})$/);
  if (mention) {
    const query = mention[1] || "";
    openMentionMenu(index, row, query, offset - query.length - 1, offset);
  } else if (State.mention.blockIndex === index) {
    closeMentionMenu();
  }
  const slash = block.content.match(/^\/([^\s]*)$/);
  if (slash) openBlockMenu(index, row, "slash", slash[1] || "");
  else if (State.menuMode === "slash" && State.slashSourceIndex === index) closeBlockMenu();
  markDirty();
}

function renderBlocks() {
  const root = $("#blocks");
  root.innerHTML = "";
  State.pageData.blocks = (State.pageData.blocks || []).map(EditorCore.normalizeBlock);
  const blocks = State.pageData.blocks;
  if (!blocks.length) blocks.push(EditorCore.createBlock("paragraph"));
  let fullPageDatabase = blocks.find(block => (
    block.id === State.databaseFullPageBlockId
    && DATABASE_BLOCK_TYPES.has(block.type)
  )) || null;
  if (!fullPageDatabase) State.databaseFullPageBlockId = "";
  applyDatabaseFullPagePresentation(fullPageDatabase);
  const tabControllers = new Map(blocks.filter(block => block.type === "tabs" && block.id).map(block => {
    normalizeTabsBlock(block);
    return [block.id, block];
  }));
  const collapsedToggleIndents = [];
  const numberedCounters = Array(EditorCore.MAX_INDENT + 1).fill(0);
  const canEdit = activePageCanEdit();
  let activeLayout = null;
  blocks.forEach((block, index) => {
    const indent = Math.max(0, Number(block.props?.indent || 0));
    while (collapsedToggleIndents.length && indent <= collapsedToggleIndents[collapsedToggleIndents.length - 1]) collapsedToggleIndents.pop();
    const hiddenByToggle = collapsedToggleIndents.length > 0;
    const tabGroup = String(block.props?.tab_group || "");
    const tabController = tabControllers.get(tabGroup);
    const hiddenByTab = Boolean(tabController && String(block.props?.tab_id || "") !== String(tabController.props?.active_tab_id || ""));
    const layoutGroup = String(block.props?.layout_group || "");
    const layoutColumns = Math.max(2, Math.min(5, Number(block.props?.layout_columns || 2)));
    const layoutColumn = Math.max(0, Math.min(layoutColumns - 1, Number(block.props?.layout_column || 0)));
    let appendTarget = root;
    if (layoutGroup) {
      if (!activeLayout || activeLayout.groupId !== layoutGroup || activeLayout.columns.length !== layoutColumns) {
        const wrapper = document.createElement("div");
        wrapper.className = "block-column-layout";
        wrapper.dataset.layoutGroup = layoutGroup;
        wrapper.dataset.layoutColumns = String(layoutColumns);
        wrapper.style.setProperty("--layout-columns", String(layoutColumns));
        const columns = Array.from({ length: layoutColumns }, (_, columnIndex) => {
          const column = document.createElement("div");
          column.className = "block-column";
          column.dataset.layoutColumn = String(columnIndex);
          wrapper.appendChild(column);
          return column;
        });
        root.appendChild(wrapper);
        activeLayout = { groupId: layoutGroup, columns };
      }
      appendTarget = activeLayout.columns[layoutColumn];
    } else {
      activeLayout = null;
    }
    const row = document.createElement("div");
    row.className = "block-row" + (State.selectedBlockIds.has(block.id) ? " selected" : "");
    row.dataset.type = block.type || "paragraph";
    row.dataset.index = String(index);
    row.dataset.blockId = block.id || "";
    if (tabGroup) row.dataset.tabGroup = tabGroup;
    row.style.setProperty("--block-indent", String(indent));
    if (block.type === "numbered") {
      numberedCounters[indent] += 1;
      numberedCounters.fill(0, indent + 1);
      row.style.setProperty("--notion-list-number", `"${numberedCounters[indent]}."`);
    } else {
      numberedCounters.fill(0);
    }
    row.hidden = hiddenByToggle || hiddenByTab || Boolean(fullPageDatabase && block.id !== fullPageDatabase.id);
    row.classList.toggle("full-page-database-row", Boolean(fullPageDatabase && block.id === fullPageDatabase.id));
    row.id = block.id ? `block-${block.id}` : "";
    row.innerHTML = `
      <div class="block-controls">
        <button type="button" class="block-control" data-action="insert" title="아래에 블록 추가" ${canEdit ? "" : "disabled"}>${iconSvg("plus")}</button>
        <button type="button" class="block-control block-handle" data-action="menu" title="블록 메뉴" ${canEdit ? "" : "disabled"}>${iconSvg("drag")}</button>
      </div>
      <div class="block-main">${blockEditorHtml(block, canEdit)}</div>
    `;
    applyBlockVisual(row, block);
    const editor = row.querySelector(".block-editor");
    if (editor) {
      editor.addEventListener("focus", () => {
        State.activeBlockId = block.id;
        clearBlockSelection();
      });
      editor.addEventListener("compositionstart", () => {
        editor.dataset.imeComposing = "true";
        EditorHost.beginComposition(block.id || "");
      });
      editor.addEventListener("compositionend", () => {
        editor.dataset.imeComposing = "false";
        try {
          commitBlockEditorInput(editor, block, index, row);
        } finally {
          EditorHost.endComposition(block.id || "");
        }
      });
      editor.addEventListener("input", event => {
        if (event.isComposing || editor.dataset.imeComposing === "true") {
          syncEditorBlock(editor, block);
          syncSyncedBlockPeers(block, index);
          return;
        }
        commitBlockEditorInput(editor, block, index, row);
      });
      editor.addEventListener("keydown", event => handleBlockEditorKeydown(event, editor, block, index));
      editor.addEventListener("paste", event => handleBlockPaste(event, editor, block, index));
      editor.addEventListener("click", event => {
        const reminder = event.target.closest?.("span[data-notion-reminder]");
        if (reminder) {
          event.preventDefault();
          openInlineReminderDialog(reminder, editor, block);
          return;
        }
        const anchor = event.target.closest?.("a[href]");
        if (!anchor) return;
        try {
          const url = new URL(anchor.getAttribute("href"), location.origin);
          const match = url.origin === location.origin && url.pathname.match(/^\/page\/(.+)$/);
          if (!match) return;
          event.preventDefault();
          openPage(decodeURIComponent(match[1]));
        } catch {}
      });
    }
    const checkbox = row.querySelector(".todo-control input");
    if (checkbox) {
      checkbox.addEventListener("change", e => {
        block.props = block.props || {};
        block.props.checked = e.target.checked;
        markDirty();
      });
    }
    row.querySelector("[data-toggle-block]")?.addEventListener("click", () => {
      block.props = block.props || {};
      block.props.open = block.props.open === false;
      markDirty();
      renderBlocks();
    });
    if (block.type === "ai_notes") bindAiNotesBlock(row, block, false);
    if (block.type === "equation") bindEquationBlock(row, block, false);
    if (block.type === "embed" && block.props?.embed_mode === "html") bindHtmlEmbedBlock(row, block, false);
    attachBlockExtras(row, block, index, canEdit);
    row.querySelector('[data-action="insert"]').addEventListener("click", e => openBlockMenu(index, e.currentTarget, "insert"));
    row.querySelector('[data-action="menu"]').addEventListener("click", e => {
      selectBlockHandle(index, e.shiftKey);
      openBlockMenu(index, e.currentTarget, "actions");
    });
    row.addEventListener("click", event => {
      if (!event.altKey || !event.shiftKey) return;
      event.preventDefault();
      event.stopPropagation();
      toggleBlockSelectionAt(index);
    }, true);
    bindBlockDrag(row, block, index, canEdit);
    appendTarget.appendChild(row);
    if (["toggle", "toggle_heading1", "toggle_heading2", "toggle_heading3"].includes(block.type) && block.props?.open === false) collapsedToggleIndents.push(indent);
  });
  if (canEdit) {
    root.querySelectorAll(".block-column-layout").forEach(layout => {
      const groupId = layout.dataset.layoutGroup || "";
      const columnCount = Number(layout.dataset.layoutColumns || 2);
      layout.querySelectorAll(".block-column").forEach(column => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "block-column-add";
        button.textContent = "＋";
        button.title = "이 열에 블록 추가";
        const columnIndex = Number(column.dataset.layoutColumn || 0);
        button.addEventListener("click", () => addBlockToColumn(groupId, columnIndex, columnCount));
        column.appendChild(button);
      });
    });
  }
  window.NotionPageOutline?.refresh();
  renderRemoteCursors();
}

function syncSyncedBlockPeers(source, sourceIndex) {
  if (source?.type !== "synced_block" || !source.props?.sync_id) return;
  State.pageData.blocks.forEach((block, index) => {
    if (index === sourceIndex || block.type !== "synced_block" || block.props?.sync_id !== source.props.sync_id) return;
    block.content = source.content;
    const indent = block.props?.indent;
    block.props = EditorCore.deepClone(source.props || {});
    if (indent != null) block.props.indent = indent;
    const peer = document.querySelector(`.block-row[data-index="${index}"] .block-editor`);
    if (peer && peer !== document.activeElement) peer.innerHTML = blockRichHtml(block);
  });
}

function handleBlockPaste(event, editor, block, index) {
  if (!activePageCanEdit()) return;
  const text = event.clipboardData?.getData("text/plain") || "";
  const html = event.clipboardData?.getData("text/html") || "";
  if (block.type === "equation") {
    event.preventDefault();
    document.execCommand("insertText", false, text);
    syncEditorBlock(editor, block);
    markDirty();
    return;
  }
  const selection = window.getSelection();
  if (selection && !selection.isCollapsed && /^(https?:\/\/|mailto:|tel:)/i.test(text.trim())) {
    event.preventDefault();
    document.execCommand("createLink", false, normalizeUrl(text.trim()));
    syncEditorBlock(editor, block);
    markDirty();
    return;
  }
  if (/\r?\n/.test(text)) {
    event.preventDefault();
    pasteMultilineAt(index, editor, text);
    return;
  }
  event.preventDefault();
  if (html) document.execCommand("insertHTML", false, sanitizeRichHtml(html));
  else document.execCommand("insertText", false, text);
  syncEditorBlock(editor, block);
  markDirty();
}

function openBlockPreview(block) {
  const type = block?.type || "";
  const fileId = String(block?.props?.file_id || "");
  const internalUrl = notionFileUrl(fileId);
  const externalUrl = safeExternalUrl(block?.props?.url || block?.props?.link_url || "");
  const url = internalUrl || externalUrl;
  if (!url) return false;
  const label = block?.props?.name || block?.content || blockTypeMeta(type)[1];
  let preview = "";
  if (type === "image") preview = `<img src="${escapeAttr(url)}" alt="${escapeAttr(label)}">`;
  else if (type === "video") preview = `<video src="${escapeAttr(url)}" controls autoplay></video>`;
  else if (type === "audio") preview = `<audio src="${escapeAttr(url)}" controls autoplay></audio>`;
  else if (type === "pdf") preview = `<iframe src="${escapeAttr(url)}" title="${escapeAttr(label)}"></iframe>`;
  else if (type === "embed") preview = `<iframe src="${escapeAttr(url)}" title="${escapeAttr(label)}" sandbox="allow-scripts allow-same-origin allow-popups" referrerpolicy="no-referrer"></iframe>`;
  else return false;
  openModal(label, `<div class="block-preview-modal">${preview}</div>`, [{ label: "닫기", action: closeModal }]);
  return true;
}

function modifyBlockAt(index) {
  const block = State.pageData?.blocks?.[index];
  if (!block) return false;
  if (block.type === "todo") {
    block.props = block.props || {};
    block.props.checked = !block.props.checked;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    focusBlock(index, block.content.length);
    return true;
  }
  if (["toggle", "toggle_heading1", "toggle_heading2", "toggle_heading3"].includes(block.type)) {
    block.props = block.props || {};
    block.props.open = block.props.open === false;
    markDirty();
    renderBlocks();
    focusBlock(index, block.content.length);
    return true;
  }
  if (block.type === "child_page" && block.props?.page_id) {
    openPage(block.props.page_id);
    return true;
  }
  if (block.type === "button") {
    runNotionButton(block, index);
    return true;
  }
  return openBlockPreview(block);
}

function toggleAllBlocks() {
  const toggles = (State.pageData?.blocks || []).filter(block => [
    "toggle", "toggle_heading1", "toggle_heading2", "toggle_heading3",
  ].includes(block.type));
  if (!toggles.length) return false;
  const shouldOpen = toggles.some(block => block.props?.open === false);
  toggles.forEach(block => {
    block.props = block.props || {};
    block.props.open = shouldOpen;
  });
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  setStatus(shouldOpen ? "모든 토글을 펼쳤습니다." : "모든 토글을 접었습니다.", "ok");
  return true;
}

function handleBlockEditorKeydown(event, editor, block, index) {
  if (!activePageCanEdit()) return;
  const key = event.key;
  const command = event.ctrlKey || event.metaKey;
  if (
    event.isComposing
    || editor.dataset?.imeComposing === "true"
    || event.keyCode === 229
  ) return;
  const slashMenuOpen = !$("#blockMenu").hidden && State.menuMode === "slash" && State.slashSourceIndex === index;
  const mentionMenuOpen = !$("#mentionMenu").hidden && State.mention.blockIndex === index;

  if (mentionMenuOpen && ["ArrowDown", "ArrowUp", "Enter"].includes(key)) {
    event.preventDefault();
    if (key === "ArrowDown") moveMentionSelection(1);
    else if (key === "ArrowUp") moveMentionSelection(-1);
    else mentionButtons()[State.mention.selectedIndex]?.click();
    return;
  }
  if (mentionMenuOpen && key === "Escape") {
    event.preventDefault();
    closeMentionMenu();
    return;
  }

  if (slashMenuOpen && ["ArrowDown", "ArrowUp", "Enter"].includes(key)) {
    event.preventDefault();
    if (key === "ArrowDown") moveBlockMenuSelection(1);
    else if (key === "ArrowUp") moveBlockMenuSelection(-1);
    else activateBlockMenuSelection();
    return;
  }
  if (slashMenuOpen && key === "Escape") {
    event.preventDefault();
    closeBlockMenu();
    return;
  }

  if (command && !event.shiftKey && key.toLowerCase() === "a") {
    const selectedText = selectionOffsets(editor);
    const textLength = editorPlainText(editor).length;
    const coversEditor = selectedText
      && Math.min(selectedText.start, selectedText.end) === 0
      && Math.max(selectedText.start, selectedText.end) === textLength;
    if (coversEditor) {
      event.preventDefault();
      event.stopPropagation();
      State.selectedBlockIds = new Set(
        (State.pageData?.blocks || []).map(candidate => candidate.id),
      );
      State.activeBlockId = block.id;
      document.querySelectorAll(".block-row").forEach(row => {
        row.classList.toggle("selected", State.selectedBlockIds.has(row.dataset.blockId));
      });
      return;
    }
  }

  if (command && key.toLowerCase() === "s") {
    event.preventDefault();
    syncEditorBlock(editor, block);
    captureEditorHistory(true);
    savePage();
    return;
  }
  if (command && key.toLowerCase() === "p") {
    event.preventDefault();
    openSearchOverlay();
    return;
  }
  if (command && key.toLowerCase() === "z") {
    event.preventDefault();
    applyEditorHistory(event.shiftKey ? 1 : -1, {
      blockIndex: index,
      offset: caretOffset(editor),
    });
    return;
  }
  if (command && key.toLowerCase() === "y") {
    event.preventDefault();
    applyEditorHistory(1, {
      blockIndex: index,
      offset: caretOffset(editor),
    });
    return;
  }
  if (command && !event.shiftKey && ["b", "i", "u", "e"].includes(key.toLowerCase())) {
    event.preventDefault();
    const format = { b: "bold", i: "italic", u: "underline" }[key.toLowerCase()];
    if (key.toLowerCase() === "e") toggleInlineCode();
    else applyInlineFormat(format);
    syncEditorBlock(editor, block);
    return;
  }
  if (command && event.shiftKey && key.toLowerCase() === "s") {
    event.preventDefault();
    applyInlineFormat("strikeThrough");
    syncEditorBlock(editor, block);
    return;
  }
  if (command && !event.shiftKey && key.toLowerCase() === "k") {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) {
      event.preventDefault();
      event.stopPropagation();
      openSearchOverlay();
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const url = window.prompt("링크 URL", "https://");
    if (url) applyInlineFormat("createLink", normalizeUrl(url));
    return;
  }
  if (command && key === "/") {
    event.preventDefault();
    syncEditorBlock(editor, block);
    const anchor = editor.closest(".block-row")?.querySelector('[data-action="menu"]') || editor;
    selectBlockHandle(index);
    openBlockMenu(index, anchor, "actions");
    return;
  }
  if (command && key === "Enter") {
    event.preventDefault();
    syncEditorBlock(editor, block);
    if (block.type === "equation") {
      markDirty();
      editor.blur();
      return;
    }
    modifyBlockAt(index);
    return;
  }
  if (block.type === "equation" && key === "Enter") return;
  if (command && event.altKey && key.toLowerCase() === "t") {
    event.preventDefault();
    toggleAllBlocks();
    return;
  }
  if (command && event.shiftKey && key.toLowerCase() === "h") {
    const offset = caretOffset(editor);
    if (applyLastBlockColor(index)) {
      event.preventDefault();
      focusBlock(index, Math.min(block.content.length, offset));
    }
    return;
  }
  if (command && !event.shiftKey && key.toLowerCase() === "d") {
    event.preventDefault();
    const indices = selectedBlockIndices(index);
    if (!databaseStructureMutationAllowed(indices)) return;
    State.pageData.blocks = EditorCore.duplicateBlocks(State.pageData.blocks, indices);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    focusBlock(index + 1, 0);
    return;
  }
  if (command && event.shiftKey && (key === "ArrowUp" || key === "ArrowDown")) {
    event.preventDefault();
    const offset = caretOffset(editor);
    const target = key === "ArrowUp" ? index - 1 : index + 1;
    State.pageData.blocks = EditorCore.moveBlock(State.pageData.blocks, index, target);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    focusBlock(Math.max(0, Math.min(State.pageData.blocks.length - 1, target)), offset);
    return;
  }
  if (command && event.shiftKey && key.toLowerCase() === "m") {
    event.preventDefault();
    State.commentBlockId = block.id || "";
    State.commentSelection = selectionOffsets(editor);
    openDrawer("comments");
    setTimeout(() => $("#commentInput")?.focus(), 0);
    return;
  }
  const shortcutDigit = /^[0-9]$/.test(key) ? key : (/^Digit[0-9]$/.test(event.code || "") ? event.code.slice(-1) : "");
  if (command && event.shiftKey && shortcutDigit) {
    const mapping = {
      "0": "paragraph", "1": "heading1", "2": "heading2", "3": "heading3", "4": "todo",
      "5": "bullet", "6": "numbered", "7": "toggle", "8": "code", "9": "child_page",
    };
    if (mapping[shortcutDigit]) {
      event.preventDefault();
      if (mapping[shortcutDigit] === "child_page") insertChildPageAfter(index, true);
      else changeBlockType(index, mapping[shortcutDigit]);
      return;
    }
  }
  if (event.altKey && event.shiftKey && key.toLowerCase() === "l") {
    event.preventDefault();
    copyText(`${pageUrl(State.activePageId)}#block-${block.id || index}`, "블록 링크 복사됨");
    return;
  }
  if (key === "Escape") {
    event.preventDefault();
    syncEditorBlock(editor, block);
    editor.blur();
    selectBlockHandle(index);
    return;
  }
  if (key === "Tab") {
    event.preventDefault();
    if (block.type === "code") {
      document.execCommand("insertText", false, event.shiftKey ? "" : "\t");
      syncEditorBlock(editor, block);
      markDirty();
      return;
    }
    const offset = caretOffset(editor);
    State.pageData.blocks[index] = EditorCore.setIndent(block, event.shiftKey ? -1 : 1);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    focusBlock(index, offset);
    return;
  }
  if (key === "Enter" && event.shiftKey) {
    event.preventDefault();
    insertSoftBreak(editor, block);
    return;
  }
  if (key === "Enter" && !event.shiftKey) {
    if (block.type === "code" && !command) {
      event.preventDefault();
      insertSoftBreak(editor, block);
      return;
    }
    event.preventDefault();
    syncEditorBlock(editor, block);
    if (!block.content && EditorCore.LIST_BLOCK_TYPES.has(block.type)) {
      const layout = block.props?.layout_group ? {
        layout_group: block.props.layout_group,
        layout_column: Number(block.props.layout_column || 0),
        layout_columns: Number(block.props.layout_columns || 2),
      } : {};
      block.type = "paragraph";
      block.props = layout;
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
      focusBlock(index, 0);
      return;
    }
    const offset = caretOffset(editor);
    const [first, second] = EditorCore.splitBlock(block, offset);
    State.pageData.blocks.splice(index, 1, first, second);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    focusBlock(index + 1, 0);
    return;
  }
  if (key === "Backspace" && selectionIsCollapsed(editor) && caretOffset(editor) === 0) {
    if (!block.content && block.type !== "paragraph") {
      event.preventDefault();
      const layout = block.props?.layout_group ? {
        layout_group: block.props.layout_group,
        layout_column: Number(block.props.layout_column || 0),
        layout_columns: Number(block.props.layout_columns || 2),
      } : {};
      block.type = "paragraph";
      block.props = layout;
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
      focusBlock(index, 0);
      return;
    }
    if (index > 0 && EditorCore.isTextBlock(State.pageData.blocks[index - 1].type)) {
      event.preventDefault();
      syncEditorBlock(editor, block);
      const merged = EditorCore.mergeBlocks(State.pageData.blocks[index - 1], block);
      State.pageData.blocks.splice(index - 1, 2, merged.block);
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
      focusBlock(index - 1, merged.caret);
    }
  }
  if (key === "Delete" && selectionIsCollapsed(editor) && caretOffset(editor) === editorPlainText(editor).length) {
    const next = State.pageData.blocks[index + 1];
    if (next && EditorCore.isTextBlock(next.type)) {
      event.preventDefault();
      syncEditorBlock(editor, block);
      const merged = EditorCore.mergeBlocks(block, next);
      State.pageData.blocks.splice(index, 2, merged.block);
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
      focusBlock(index, merged.caret);
    }
  }
}

function linkPreviewHtml(block, index, canEdit) {
  const url = normalizeUrl(block.props?.link_url || "");
  if (!url) return "";
  return `
    <div class="link-preview" data-link-index="${index}">
      <span>⌁</span>
      <a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(url)}</a>
      <button type="button" data-link-copy="${index}" title="링크 복사">⧉</button>
      ${canEdit ? `<button type="button" data-link-edit="${index}" title="편집">편집</button>` : ""}
    </div>
  `;
}

function mediaToolbarHtml(index, url, type, canEdit) {
  if (!url) return "";
  return `
    <div class="media-toolbar">
      <button type="button" data-media-comment="${index}" title="댓글">☰</button>
      <button type="button" data-media-caption="${index}" title="캡션">▤</button>
      <button type="button" data-action-plus="${index}" title="아래에 블록 추가" ${canEdit ? "" : "disabled"}>＋</button>
      <a href="${url}" target="_blank" title="확대">⌕</a>
      <a href="${url}" download title="다운로드">↓</a>
      <button type="button" data-media-more="${index}" title="더보기">…</button>
    </div>
  `;
}

function richTextEditorHtml(block, canEdit, placeholder = "입력하세요. '/'로 블록을 선택할 수 있습니다.") {
  return `<div class="block-editor" role="textbox" aria-label="페이지 블록" aria-multiline="true" contenteditable="${canEdit ? "true" : "false"}" spellcheck="true" data-placeholder="${escapeAttr(placeholder)}">${blockRichHtml(block)}</div>`;
}

function safeExternalUrl(value) {
  const url = normalizeUrl(value || "");
  return /^https?:\/\//i.test(url) ? url : "";
}

function tableOfContentsHtml() {
  const headings = (State.pageData?.blocks || []).filter(block => [
    "heading1", "heading2", "heading3", "toggle_heading1", "toggle_heading2", "toggle_heading3",
  ].includes(block.type) && block.content);
  if (!headings.length) return '<div class="toc-empty">제목 블록을 추가하면 목차가 표시됩니다.</div>';
  return `<nav class="table-of-contents">${headings.map(block => {
    const level = Number(block.type.slice(-1)) || 1;
    return `<a href="#block-${escapeAttr(block.id || "")}" style="--toc-level:${level}">${escapeHtml(block.content)}</a>`;
  }).join("")}</nav>`;
}

function breadcrumbBlockHtml() {
  const crumbs = pageCrumbs(State.pageData?.page || {});
  return `<nav class="breadcrumb-block">${crumbs.map((page, index) => `
    <button type="button" data-breadcrumb-page="${escapeAttr(page.id || "")}">${escapeHtml(page.title || "제목 없음")}</button>
    ${index < crumbs.length - 1 ? "<span>/</span>" : ""}
  `).join("")}</nav>`;
}

function sidePeekIconHtml() {
  return '<svg viewBox="0 0 20 20" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true" focusable="false"><rect x="2" y="3" width="16" height="14" rx="2"/><path d="M12 3v14M5 10h5m-2-2 2 2-2 2"/></svg>';
}

function linkToPageBlockHtml(block, canEdit) {
  const pageId = String(block.props?.page_id || "");
  const linked = State.pages.find(page => page.id === pageId);
  if (linked) {
    return `<div class="child-page-row linked-page-row"><button type="button" class="linked-page-block" data-linked-page-open="${escapeAttr(linked.id)}">${pageIconHtml(linked)}<span>${escapeHtml(linked.title || "제목 없음")}</span></button>
      <button type="button" class="child-page-side" data-child-page-side="${escapeAttr(linked.id)}" aria-label="${escapeAttr(linked.title || "제목 없음")} 사이드 보기로 열기" title="사이드 보기로 열기 (Alt+클릭)">${sidePeekIconHtml()}<span>열기</span></button></div>
      ${canEdit ? `<select class="linked-page-select" data-linked-page-select><option value="">페이지 변경...</option>${visiblePages().filter(page => page.id !== State.activePageId).map(page => `<option value="${escapeAttr(page.id)}">${escapeHtml(page.title || "제목 없음")}</option>`).join("")}</select>` : ""}`;
  }
  return canEdit
    ? `<select class="linked-page-select" data-linked-page-select><option value="">연결할 페이지 선택</option>${visiblePages().filter(page => page.id !== State.activePageId).map(page => `<option value="${escapeAttr(page.id)}">${escapeHtml(page.title || "제목 없음")}</option>`).join("")}</select>`
    : '<span class="muted-block">연결된 페이지 없음</span>';
}

function simpleTableBlockHtml(block, canEdit) {
  block.props = block.props || {};
  let rows = Array.isArray(block.props.rows) ? block.props.rows.filter(row => Array.isArray(row)) : [];
  if (!rows.length) rows = [["", ""], ["", ""]];
  const columnCount = Math.max(1, ...rows.map(row => row.length));
  rows = rows.map(row => Array.from({ length: columnCount }, (_, index) => String(row[index] || "")));
  block.props.rows = rows;
  return `<div class="simple-table-wrap">
    <div class="simple-table" style="--simple-columns:${columnCount}">${rows.map((row, rowIndex) => row.map((value, columnIndex) => `
      <input class="${(block.props.header_row && rowIndex === 0) || (block.props.header_column && columnIndex === 0) ? "table-header-cell" : ""}" data-simple-cell data-row="${rowIndex}" data-column="${columnIndex}" value="${escapeAttr(value)}" ${canEdit ? "" : "disabled"}>
    `).join("")).join("")}</div>
    ${canEdit ? `<div class="simple-table-actions"><button type="button" data-simple-add-row>＋ 행</button><button type="button" data-simple-add-column>＋ 열</button><label><input type="checkbox" data-simple-header-row ${block.props.header_row ? "checked" : ""}> 머리글 행</label><label><input type="checkbox" data-simple-header-column ${block.props.header_column ? "checked" : ""}> 머리글 열</label></div>` : ""}
  </div>`;
}

function buttonBlockHtml(block, index, canEdit) {
  const props = block.props || {};
  const action = ["insert_block", "new_page", "open_url"].includes(props.action) ? props.action : "insert_block";
  const label = props.label || block.content || "버튼";
  return `<div class="notion-button-block">
    <button type="button" class="notion-action-button" data-run-button="${index}" ${!canEdit && action !== "open_url" ? "disabled" : ""}>⚡ ${escapeHtml(label)}</button>
    ${canEdit ? `<details><summary>버튼 설정</summary><div class="button-config">
      <input data-button-label value="${escapeAttr(label)}" placeholder="버튼 이름">
      <select data-button-action><option value="insert_block" ${action === "insert_block" ? "selected" : ""}>아래에 블록 삽입</option><option value="new_page" ${action === "new_page" ? "selected" : ""}>하위 페이지 만들기</option><option value="open_url" ${action === "open_url" ? "selected" : ""}>URL 열기</option></select>
      <input data-button-url value="${escapeAttr(props.url || "")}" placeholder="https://" ${action === "open_url" ? "" : "hidden"}>
    </div></details>` : ""}
  </div>`;
}

function newTabId() {
  return `tab_${crypto?.randomUUID?.().replace(/-/g, "") || Date.now().toString(36) + Math.random().toString(36).slice(2)}`;
}

function normalizeTabsBlock(block) {
  block.props = block.props && typeof block.props === "object" ? block.props : {};
  const seen = new Set();
  let tabs = Array.isArray(block.props.tabs) ? block.props.tabs : [];
  tabs = tabs.filter(item => item && item.id && !seen.has(String(item.id)) && seen.add(String(item.id))).slice(0, 20).map((item, index) => ({
    id: String(item.id).slice(0, 100),
    name: String(item.name || `탭 ${index + 1}`).slice(0, 100),
  }));
  while (tabs.length < 2) tabs.push({ id: newTabId(), name: `탭 ${tabs.length + 1}` });
  block.props.tabs = tabs;
  if (!tabs.some(item => item.id === block.props.active_tab_id)) block.props.active_tab_id = tabs[0].id;
  return tabs;
}

function tabsBlockHtml(block, canEdit) {
  const tabs = normalizeTabsBlock(block);
  return `<div class="notion-tabs-block" role="tablist" aria-label="페이지 탭">
    ${tabs.map(item => `<span class="notion-tab-chip${item.id === block.props.active_tab_id ? " active" : ""}">
      <button type="button" role="tab" aria-selected="${item.id === block.props.active_tab_id}" data-tab-select="${escapeAttr(item.id)}">${escapeHtml(item.name)}</button>
      ${canEdit ? `<button type="button" class="notion-tab-menu" data-tab-menu="${escapeAttr(item.id)}" title="탭 설정">•••</button>` : ""}
    </span>`).join("")}
    ${canEdit && tabs.length < 20 ? '<button type="button" class="notion-tab-add" data-tab-add title="탭 추가">＋</button>' : ""}
  </div>`;
}

function selectTabsBlockTab(block, tabId) {
  const tabs = normalizeTabsBlock(block);
  if (!tabs.some(item => item.id === tabId)) return;
  block.props.active_tab_id = tabId;
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
}

function addTabsBlockTab(block, blockIndex) {
  const tabs = normalizeTabsBlock(block);
  if (tabs.length >= 20 || !activePageCanEdit()) return;
  const tab = { id: newTabId(), name: `탭 ${tabs.length + 1}` };
  tabs.push(tab);
  block.props.active_tab_id = tab.id;
  const members = State.pageData.blocks.map((item, index) => ({ item, index })).filter(({ item }) => item.props?.tab_group === block.id);
  const insertAt = members.length ? members[members.length - 1].index + 1 : blockIndex + 1;
  const member = EditorCore.createBlock("paragraph", {
    props: {
      tab_group: block.id,
      tab_id: tab.id,
      ...(block.props?.layout_group ? {
        layout_group: block.props.layout_group,
        layout_column: Number(block.props.layout_column || 0),
        layout_columns: Number(block.props.layout_columns || 2),
      } : {}),
    },
  });
  State.pageData.blocks.splice(insertAt, 0, member);
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  focusBlock(insertAt, 0);
}

function deleteTabsBlockTab(block, tabId) {
  const tabs = normalizeTabsBlock(block);
  if (tabs.length <= 2 || !tabs.some(item => item.id === tabId)) return;
  if (!window.confirm("이 탭과 탭 안의 블록을 삭제할까요?")) return;
  block.props.tabs = tabs.filter(item => item.id !== tabId);
  State.pageData.blocks = State.pageData.blocks.filter(item => !(item.props?.tab_group === block.id && item.props?.tab_id === tabId));
  if (block.props.active_tab_id === tabId) block.props.active_tab_id = block.props.tabs[0].id;
  closeModal();
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
}

function openTabsBlockTabSettings(block, tabId) {
  const tabs = normalizeTabsBlock(block);
  const tab = tabs.find(item => item.id === tabId);
  if (!tab) return;
  openModal("탭 설정", `<label class="database-rule-form">탭 이름<input id="tabNameInput" value="${escapeAttr(tab.name)}" maxlength="100"></label>`, [
    ...(tabs.length > 2 ? [{ label: "탭 삭제", action: () => deleteTabsBlockTab(block, tabId) }] : []),
    { label: "취소", action: closeModal },
    { label: "저장", primary: true, action: () => {
      tab.name = String($("#tabNameInput")?.value || tab.name).trim().slice(0, 100) || tab.name;
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
  ]);
  setTimeout(() => $("#tabNameInput")?.focus(), 0);
}

function blockEditorHtml(block, canEdit) {
  const type = block.type || "paragraph";
  if (type === "ai_notes") return window.NotionAiNotes.html(block, canEdit);
  if (type === "divider") return "<hr>";
  if (type === "table_of_contents") return tableOfContentsHtml();
  if (type === "breadcrumb") return breadcrumbBlockHtml();
  if (type === "link_to_page") return linkToPageBlockHtml(block, canEdit);
  if (type === "simple_table") return simpleTableBlockHtml(block, canEdit);
  if (type === "button") return buttonBlockHtml(block, State.pageData.blocks.indexOf(block), canEdit);
  if (type === "synced_block") return `<div class="synced-block"><div class="synced-label">↻ 동기화됨</div>${richTextEditorHtml(block, canEdit, "동기화 블록")}</div>`;
  if (type === "tabs") return tabsBlockHtml(block, canEdit);
  if (type === "child_page") {
    const pageId = block.props?.page_id || "";
    const title = block.content || block.props?.title || "새 페이지";
    if (block.props?.archived) {
      return `<div class="child-page-row archived-child-page">
        <span class="child-page-block">${pageIconHtml("")}<span>${escapeHtml(title)}</span><small>휴지통</small></span>
        ${canEdit ? `<button type="button" class="child-page-side" data-child-page-restore="${escapeAttr(pageId)}">복원</button>` : ""}
      </div>`;
    }
    return `
      <div class="child-page-row">
      <button type="button" class="child-page-block" data-child-page="${escapeHtml(pageId)}">
        ${pageIconHtml("")}
        <span>${escapeHtml(title)}</span>
      </button>
      <button type="button" class="child-page-side" data-child-page-side="${escapeAttr(pageId)}" aria-label="${escapeAttr(title)} 사이드 보기로 열기" title="사이드 보기로 열기 (Alt+클릭)">${sidePeekIconHtml()}<span>열기</span></button>
      </div>
    `;
  }
  if (DATABASE_BLOCK_TYPES.has(type)) return databaseBlockHtml(block, canEdit);
  if (type === "todo") {
    const checked = block.props?.checked ? "checked" : "";
    return `
      <label class="todo-control"><input type="checkbox" ${checked} ${canEdit ? "" : "disabled"}><span aria-hidden="true"></span></label>
      ${richTextEditorHtml(block, canEdit, "할 일")}
    `;
  }
  if (["toggle", "toggle_heading1", "toggle_heading2", "toggle_heading3"].includes(type)) {
    const open = block.props?.open !== false;
    const placeholder = type === "toggle" ? "토글" : `제목 토글 ${type.slice(-1)}`;
    return `<div class="toggle-line"><button type="button" class="toggle-button${open ? " open" : ""}" data-toggle-block aria-expanded="${open}">▶</button>${richTextEditorHtml(block, canEdit, placeholder)}</div>`;
  }
  if (type === "callout") {
    return `<div class="callout-block"><span class="callout-icon">${escapeHtml(block.props?.icon || "💡")}</span>${richTextEditorHtml(block, canEdit, "콜아웃")}</div>`;
  }
  if (type === "equation") {
    return window.NotionEquationEditor.html(block.content, canEdit);
  }
  if (["bookmark", "embed"].includes(type)) {
    if (type === "embed" && block.props?.embed_mode === "html") return window.NotionHtmlEmbed.html(block, canEdit);
    const url = safeExternalUrl(block.props?.url || block.props?.link_url || "");
    return `<div class="url-block ${type}-block">
      <input class="block-url-input" data-block-url type="url" value="${escapeAttr(url)}" placeholder="https://example.com" ${canEdit ? "" : "disabled"}>
      ${url ? (type === "embed"
        ? `<iframe src="${escapeAttr(url)}" loading="lazy" sandbox="allow-scripts allow-same-origin allow-popups" referrerpolicy="no-referrer"></iframe>`
        : `<a href="${escapeAttr(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(block.content || url)}</a>`) : ""}
      ${richTextEditorHtml(block, canEdit, type === "bookmark" ? "북마크 설명" : "임베드 설명")}
    </div>`;
  }
  if (["image", "file", "video", "audio", "pdf"].includes(type)) {
    const fileId = block.props?.file_id || "";
    const name = block.props?.name || block.content || "Attachment";
    const url = notionFileUrl(fileId);
    let media = "";
    if (type === "image" && fileId) media = `<img src="${url}" alt="${escapeAttr(name)}">`;
    if (type === "video" && fileId) media = `<video src="${url}" controls preload="metadata"></video>`;
    if (type === "audio" && fileId) media = `<audio src="${url}" controls preload="metadata"></audio>`;
    if (type === "pdf" && fileId) media = `<iframe class="pdf-frame" src="${url}" title="${escapeAttr(name)}"></iframe>`;
    const comment = block.props?.comment ? `<div class="media-comment-note">${escapeHtml(block.props.comment)}</div>` : "";
    return `
      <div class="media-block">
        ${mediaToolbarHtml(State.pageData.blocks.indexOf(block), url, type, canEdit)}
        <div class="file-preview">
          ${media}
          ${url ? `<a href="${url}" target="_blank">${escapeHtml(name)}</a>` : `<span>${escapeHtml(name)}</span>`}
        </div>
      </div>
      ${comment}
      ${richTextEditorHtml(block, canEdit, "캡션")}
    `;
  }
  const placeholder = type === "code" ? "코드" : "입력하세요. '/'로 블록을 선택할 수 있습니다.";
  const editor = richTextEditorHtml(block, canEdit, placeholder);
  if (type === "code") {
    const language = block.props?.language || "plain text";
    const languages = ["plain text", "javascript", "typescript", "python", "java", "c", "cpp", "csharp", "go", "rust", "sql", "json", "html", "css", "bash", "powershell", "markdown", "yaml"];
    return `<div class="code-block-wrap">
      <div class="code-toolbar"><select data-code-language ${canEdit ? "" : "disabled"}>${languages.map(item => `<option value="${item}" ${item === language ? "selected" : ""}>${item}</option>`).join("")}</select><button type="button" data-code-copy>복사</button></div>
      <pre class="code-shell">${editor}</pre>
      <input class="code-caption" data-code-caption value="${escapeAttr(block.props?.caption || "")}" placeholder="캡션" ${canEdit ? "" : "disabled"}>
    </div>`;
  }
  return `${linkPreviewHtml(block, State.pageData.blocks.indexOf(block), canEdit)}${editor}`;
}

function databaseRows(block) {
  block.props = block.props || {};
  if (!Array.isArray(block.props.rows)) block.props.rows = Array.isArray(block.props.events) ? block.props.events : [];
  delete block.props.events;
  return block.props.rows;
}

function databaseProperties(block) {
  block.props = block.props || {};
  if (!Array.isArray(block.props.properties) || !block.props.properties.length) {
    block.props.properties = [
      { id: "title", name: "이름", type: "title" },
      { id: "status", name: "상태", type: "status" },
      { id: "date", name: "날짜", type: "date" },
      { id: "tags", name: "태그", type: "multi_select" },
    ];
  }
  if (!block.props.properties.some(item => item?.type === "title")) {
    block.props.properties.unshift({ id: "title", name: "이름", type: "title" });
  }
  return block.props.properties.filter(item => item && item.id).slice(0, 500);
}

function databasePropertyId(block, types, fallback) {
  const accepted = new Set(Array.isArray(types) ? types : [types]);
  return databaseProperties(block).find(item => accepted.has(item.type))?.id || fallback;
}

function databaseRowTitle(block, item) {
  const field = databasePropertyId(block, "title", "title");
  return String(item?.[field] || item?.title || "새 페이지");
}

const DATABASE_VIEW_LABELS = {
  table: "표",
  board: "보드",
  timeline: "타임라인",
  calendar: "캘린더",
  list: "목록",
  gallery: "갤러리",
};

const DATABASE_VIEW_STATE_KEYS = [
  "filters", "sorts", "group_property", "month", "timeline_anchor", "timeline_scale", "timeline_date_property",
  "chart_group_property", "chart_measure", "chart_type", "board_group_property", "calendar_date_property",
  "dashboard_widgets", "dashboard_edit_mode", "form_title", "form_description", "form_submit_label",
  "form_required_properties", "form_success_message", "feed_date_property", "map_place_property",
  "property_visibility",
];

function databaseViews(block) {
  block.props = block.props || {};
  let views = Array.isArray(block.props.views)
    ? block.props.views.filter(view => view && view.id && DATABASE_BLOCK_TYPES.has(view.type)).slice(0, 100)
    : [];
  if (!views.length) {
    const type = DATABASE_BLOCK_TYPES.has(block.props.view)
      ? block.props.view
      : (DATABASE_BLOCK_TYPES.has(block.type) ? block.type : "table");
    views = [{
      id: `view_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
      name: DATABASE_VIEW_LABELS[type] || "보기",
      type,
      filters: EditorCore.deepClone(Array.isArray(block.props.filters) ? block.props.filters : []),
      sorts: EditorCore.deepClone(Array.isArray(block.props.sorts) ? block.props.sorts : []),
      group_property: block.props.group_property || "",
      month: block.props.month || "",
      timeline_anchor: block.props.timeline_anchor || "",
      timeline_scale: block.props.timeline_scale || "month",
      timeline_date_property: block.props.timeline_date_property || "",
      chart_group_property: block.props.chart_group_property || "",
      chart_measure: block.props.chart_measure || "__count",
      chart_type: block.props.chart_type || "bar",
      board_group_property: block.props.board_group_property || "",
      calendar_date_property: block.props.calendar_date_property || "",
      dashboard_widgets: EditorCore.deepClone(Array.isArray(block.props.dashboard_widgets) ? block.props.dashboard_widgets : []),
      dashboard_edit_mode: Boolean(block.props.dashboard_edit_mode),
      form_title: block.props.form_title || "",
      form_description: block.props.form_description || "",
      form_submit_label: block.props.form_submit_label || "제출",
      form_required_properties: EditorCore.deepClone(Array.isArray(block.props.form_required_properties) ? block.props.form_required_properties : []),
      form_success_message: block.props.form_success_message || "응답이 제출되었습니다.",
      feed_date_property: block.props.feed_date_property || "",
      map_place_property: block.props.map_place_property || "",
      property_visibility: block.props.property_visibility && typeof block.props.property_visibility === "object"
        ? EditorCore.deepClone(block.props.property_visibility)
        : Object.fromEntries(databaseProperties(block).map(property => [property.id, !property.hidden])),
    }];
  }
  views.forEach((view, index) => {
    view.name = String(view.name || DATABASE_VIEW_LABELS[view.type] || `보기 ${index + 1}`).slice(0, 100);
    view.filters = Array.isArray(view.filters) ? view.filters : [];
    view.sorts = Array.isArray(view.sorts) ? view.sorts : [];
  });
  block.props.views = views;
  if (!views.some(view => view.id === block.props.active_view_id)) block.props.active_view_id = views[0].id;
  if (!block.props.property_visibility || typeof block.props.property_visibility !== "object") {
    const active = views.find(view => view.id === block.props.active_view_id) || views[0];
    block.props.property_visibility = EditorCore.deepClone(active.property_visibility && typeof active.property_visibility === "object" ? active.property_visibility : {});
  }
  return views;
}

function activeDatabaseView(block) {
  const views = databaseViews(block);
  return views.find(view => view.id === block.props.active_view_id) || views[0];
}

function syncActiveDatabaseView(block) {
  const view = activeDatabaseView(block);
  if (!view) return;
  view.type = DATABASE_BLOCK_TYPES.has(block.props.view) ? block.props.view : view.type;
  DATABASE_VIEW_STATE_KEYS.forEach(key => {
    if (["filters", "sorts", "dashboard_widgets", "form_required_properties"].includes(key)) view[key] = EditorCore.deepClone(Array.isArray(block.props[key]) ? block.props[key] : []);
    else if (key === "property_visibility") view[key] = EditorCore.deepClone(block.props[key] && typeof block.props[key] === "object" ? block.props[key] : {});
    else view[key] = block.props[key] ?? "";
  });
}

function applyDatabaseView(block, view) {
  if (!view) return;
  block.props.active_view_id = view.id;
  block.props.view = view.type;
  DATABASE_VIEW_STATE_KEYS.forEach(key => {
    if (["filters", "sorts", "dashboard_widgets", "form_required_properties"].includes(key)) block.props[key] = EditorCore.deepClone(Array.isArray(view[key]) ? view[key] : []);
    else if (key === "property_visibility") block.props[key] = EditorCore.deepClone(view[key] && typeof view[key] === "object" ? view[key] : {});
    else block.props[key] = view[key] ?? "";
  });
}

function focusDatabaseViewTab(block, viewId) {
  const blockId = String(block?.id || "");
  if (!blockId || !viewId) return;
  setTimeout(() => {
    const row = [...document.querySelectorAll(".block-row[data-block-id]")]
      .find(candidate => candidate.dataset.blockId === blockId);
    const tab = [...(row?.querySelectorAll("[data-db-view-id]") || [])]
      .find(candidate => candidate.dataset.dbViewId === viewId);
    tab?.focus({ preventScroll: true });
  }, 0);
}

function databasePropertyVisible(block, property) {
  if (property?.type === "title") return true;
  const visibility = block.props?.property_visibility;
  if (visibility && typeof visibility === "object" && Object.prototype.hasOwnProperty.call(visibility, property.id)) {
    return visibility[property.id] !== false;
  }
  return !property?.hidden;
}

function activateDatabaseView(block, viewId) {
  syncActiveDatabaseView(block);
  const view = databaseViews(block).find(item => item.id === viewId);
  if (view) applyDatabaseView(block, view);
}

function calculateDatabaseFormula(block, property, item) {
  let expression = String(property?.expression || "").trim();
  if (!expression) return "";
  expression = expression.replace(/prop\(["']([^"']+)["']\)/g, (_match, name) => {
    const target = databaseProperties(block).find(candidate => candidate.name === name || candidate.id === name);
    const value = target ? item?.[target.id] : "";
    const number = Number(value);
    return Number.isFinite(number) && String(value).trim() !== "" ? String(number) : "0";
  });
  if (!/^[\d+\-*/().\s]+$/.test(expression)) return expression.slice(0, 120);
  try {
    const tokens = expression.match(/\d+(?:\.\d+)?|[()+\-*/]/g) || [];
    const values = [];
    const operators = [];
    const precedence = { "+": 1, "-": 1, "*": 2, "/": 2 };
    const apply = () => {
      const operator = operators.pop();
      const right = Number(values.pop() || 0);
      const left = Number(values.pop() || 0);
      values.push(operator === "+" ? left + right : operator === "-" ? left - right : operator === "*" ? left * right : right === 0 ? 0 : left / right);
    };
    tokens.forEach(token => {
      if (/^\d/.test(token)) values.push(Number(token));
      else if (token === "(") operators.push(token);
      else if (token === ")") {
        while (operators.length && operators[operators.length - 1] !== "(") apply();
        operators.pop();
      } else {
        while (operators.length && operators[operators.length - 1] !== "(" && precedence[operators[operators.length - 1]] >= precedence[token]) apply();
        operators.push(token);
      }
    });
    while (operators.length) apply();
    const result = values.pop();
    return Number.isFinite(result) ? String(Math.round(result * 1e9) / 1e9) : "";
  } catch {
    return "";
  }
}

function relationValueIds(value) {
  if (Array.isArray(value)) return [...new Set(value.map(item => String(item?.page_id || item || "").trim()).filter(Boolean))];
  return [...new Set(String(value || "").split(",").map(item => item.trim()).filter(Boolean))];
}

function databaseRelationTarget(property) {
  const blocks = State.pageData?.blocks || [];
  return blocks.find(candidate => DATABASE_BLOCK_TYPES.has(candidate.type) && candidate.id === property?.relation_database_block_id) || null;
}

function databaseRelationOptions(property) {
  const target = databaseRelationTarget(property);
  if (target) {
    return databaseRows(target).filter(item => !item?.archived && item?.page_id).map(item => ({
      id: String(item.page_id),
      label: databaseRowTitle(target, item),
    }));
  }
  return visiblePages().filter(page => page.id !== State.activePageId).map(page => ({ id: String(page.id), label: page.title || "제목 없음" }));
}

function calculateDatabaseRollup(block, property, item) {
  const relationProperty = databaseProperties(block).find(candidate => candidate.id === property?.rollup_relation_property && candidate.type === "relation");
  if (!relationProperty) return "";
  const pageIds = relationValueIds(item?.[relationProperty.id]);
  const target = databaseRelationTarget(relationProperty);
  const targetRows = target ? databaseRows(target) : [];
  const targetProperty = target ? databaseProperties(target).find(candidate => candidate.id === property?.rollup_target_property) : null;
  const values = pageIds.map(pageId => {
    const relatedRow = targetRows.find(candidate => String(candidate?.page_id || "") === pageId);
    if (relatedRow) {
      if (targetProperty?.type === "formula") return calculateDatabaseFormula(target, targetProperty, relatedRow);
      return targetProperty ? relatedRow[targetProperty.id] : databaseRowTitle(target, relatedRow);
    }
    const page = State.pages.find(candidate => candidate.id === pageId);
    return page?.title || "";
  }).filter(value => value !== "" && value != null);
  const calculation = property?.rollup_calculation || "show_original";
  if (calculation === "count") return String(pageIds.length);
  if (calculation === "count_values") return String(values.length);
  if (calculation === "count_unique") return String(new Set(values.map(databaseDisplayValue)).size);
  if (calculation === "percent_checked") {
    return values.length ? `${Math.round(values.filter(Boolean).length / values.length * 100)}%` : "0%";
  }
  const numbers = values.map(Number).filter(Number.isFinite);
  if (calculation === "sum") return String(numbers.reduce((sum, value) => sum + value, 0));
  if (calculation === "average") return numbers.length ? String(Math.round(numbers.reduce((sum, value) => sum + value, 0) / numbers.length * 1e9) / 1e9) : "";
  if (calculation === "min") return numbers.length ? String(Math.min(...numbers)) : "";
  if (calculation === "max") return numbers.length ? String(Math.max(...numbers)) : "";
  const displayed = values.map(databaseDisplayValue).filter(Boolean).sort();
  if (calculation === "earliest") return displayed[0] || "";
  if (calculation === "latest") return displayed[displayed.length - 1] || "";
  return displayed.join(", ");
}

function databasePropertyInput(block, property, item, rowIndex, canEdit) {
  const field = property.id;
  const value = item?.[field];
  const common = `data-db-row="${rowIndex}" data-db-field="${escapeAttr(field)}" ${canEdit ? "" : "disabled"}`;
  if (property.type === "checkbox") return `<input type="checkbox" ${common} ${value ? "checked" : ""}>`;
  if (property.type === "number") return `<input type="number" ${common} value="${escapeAttr(value ?? "")}">`;
  if (property.type === "date") {
    const start = value && typeof value === "object" ? value.start : value;
    const end = value && typeof value === "object" ? value.end : item?.[`${field}_end`];
    const reminder = item?.[`${field}_reminder`];
    return `<span class="database-date-range"><input type="date" ${common} value="${escapeAttr(String(start || "").slice(0, 10))}" aria-label="${escapeAttr(property.name || "날짜")} 시작"><input type="date" data-db-row="${rowIndex}" data-db-date-end="${escapeAttr(field)}" value="${escapeAttr(String(end || "").slice(0, 10))}" aria-label="${escapeAttr(property.name || "날짜")} 종료" ${canEdit ? "" : "disabled"}><button type="button" class="database-date-reminder${reminder ? " active" : ""}" data-db-row="${rowIndex}" data-db-date-reminder="${escapeAttr(field)}" title="리마인더" ${canEdit ? "" : "disabled"}>⏰</button></span>`;
  }
  if (property.type === "url") return `<input type="url" ${common} value="${escapeAttr(value || "")}" placeholder="https://">`;
  if (property.type === "email") return `<input type="email" ${common} value="${escapeAttr(value || "")}" placeholder="name@example.com">`;
  if (property.type === "phone") return `<input type="tel" ${common} value="${escapeAttr(value || "")}" placeholder="전화번호">`;
  if (property.type === "place") return `<input ${common} value="${escapeAttr(value || "")}" placeholder="주소 또는 장소">`;
  if (property.type === "person") {
    const people = [{ id: State.user?.id || "", display_name: State.user?.display_name || State.user?.id || "나" }, ...State.users]
      .filter((person, index, all) => person.id && all.findIndex(candidate => candidate.id === person.id) === index);
    return `<select ${common}><option value="">비어 있음</option>${people.map(person => `<option value="${escapeAttr(person.id)}" ${value === person.id ? "selected" : ""}>${escapeHtml(person.display_name || person.id)}</option>`).join("")}</select>`;
  }
  if (["select", "multi_select", "status"].includes(property.type)) {
    const defaults = property.type === "status" ? ["시작 전", "진행 중", "완료"] : [];
    const options = [...new Set([...(Array.isArray(property.options) ? property.options : []), ...defaults, ...(value ? String(value).split(",").map(item => item.trim()) : [])].filter(Boolean))];
    const listId = `db_options_${escapeAttr(property.id)}_${rowIndex}`;
    return `<input ${common} list="${listId}" value="${escapeAttr(value || "")}" placeholder="${property.type === "multi_select" ? "쉼표로 여러 값 입력" : "선택 또는 새 값 입력"}"><datalist id="${listId}">${options.map(option => `<option value="${escapeAttr(option)}"></option>`).join("")}</datalist>`;
  }
  if (["created_time", "last_edited_time"].includes(property.type)) {
    const timestamp = property.type === "created_time" ? item?._created_time : item?._last_edited_time;
    return `<input value="${escapeAttr(timestamp || "")}" disabled>`;
  }
  if (["created_by", "last_edited_by"].includes(property.type)) {
    const user = property.type === "created_by" ? item?._created_by : item?._last_edited_by;
    return `<input value="${escapeAttr(user || "")}" disabled>`;
  }
  if (property.type === "unique_id") return `<input value="${escapeAttr(`${property.prefix || "ID"}-${rowIndex + 1}`)}" disabled>`;
  if (property.type === "formula") return `<input value="${escapeAttr(calculateDatabaseFormula(block, property, item))}" disabled>`;
  if (property.type === "rollup") return `<input value="${escapeAttr(calculateDatabaseRollup(block, property, item))}" disabled>`;
  if (property.type === "relation") {
    const selected = new Set(relationValueIds(value));
    const multiple = Boolean(property.relation_multiple);
    return `<select ${common} ${multiple ? 'multiple size="3"' : ""}><option value="" ${selected.size ? "" : "selected"}>연결 없음</option>${databaseRelationOptions(property).map(option => `<option value="${escapeAttr(option.id)}" ${selected.has(option.id) ? "selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}</select>`;
  }
  if (property.type === "files") {
    const files = Array.isArray(value) ? value : (value ? [{ url: String(value), name: String(value) }] : []);
    return `<span class="database-files-property">${files.map((file, fileIndex) => {
      const url = file?.file_id ? notionFileUrl(file.file_id) : safeExternalUrl(file?.url || "");
      const name = file?.name || file?.original_name || `파일 ${fileIndex + 1}`;
      return `<span class="database-file-chip">${url ? `<a href="${escapeAttr(url)}" target="_blank" rel="noopener">${escapeHtml(name)}</a>` : `<span>${escapeHtml(name)}</span>`}${canEdit ? `<button type="button" data-db-row="${rowIndex}" data-db-file-remove="${escapeAttr(field)}" data-file-index="${fileIndex}" title="파일 제거">×</button>` : ""}</span>`;
    }).join("")}<label class="database-file-add" title="파일 업로드">＋<input type="file" data-db-row="${rowIndex}" data-db-file-input="${escapeAttr(field)}" multiple ${canEdit ? "" : "disabled"}></label></span>`;
  }
  if (property.type === "button") return `<button type="button" class="database-property-button" data-db-cell-button="${escapeAttr(field)}" data-db-row="${rowIndex}">⚡ ${escapeHtml(property.button_label || property.name || "실행")}</button>`;
  return `<input ${common} value="${escapeAttr(value || "")}" placeholder="${escapeAttr(property.name || "값")}">`;
}

async function uploadDatabasePropertyFiles(block, rowIndex, field, selectedFiles, onChange = renderBlocks) {
  const item = databaseRows(block)[rowIndex];
  const blockId = block?.id || "";
  const pageId = State.activePageId;
  const files = [...(selectedFiles || [])].slice(0, 20);
  if (!item || !field || !files.length || !activePageCanEdit()) return;
  const current = Array.isArray(item[field]) ? item[field].slice() : [];
  try {
    setStatus("데이터베이스 파일 업로드 중...");
    if (!(await flushPageDraft(pageId))) {
      setStatus("저장되지 않은 편집본이 있어 파일을 업로드하지 않았습니다.", "error");
      return;
    }
    for (const file of files) {
      if (current.length >= 20) break;
      let data;
      if (collaborativeFileBasePath(pageId)) {
        data = await uploadCollaborativeFile(file, pageId);
      } else {
        const form = new FormData();
        form.append("file", file);
        form.append("insert_block", "0");
        const response = await authenticatedFetch(`/api/notion/pages/${encodeURIComponent(pageId)}/files`, { method: "POST", body: form });
        data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      }
      const refreshResult = await refreshOpenedPageAfterMutation(pageId);
      if (refreshResult.failed) throw refreshResult.error;
      if (refreshResult.mainConflicted || refreshResult.sidePeekConflicted) return;
      const uploaded = data.file || {};
      current.push({
        file_id: uploaded.id || "",
        name: uploaded.original_name || file.name,
        mime_type: uploaded.mime_type || file.type || "",
        size: uploaded.size || file.size || 0,
      });
    }
    const currentBlock = (State.pageData?.blocks || []).find(candidate => candidate.id === blockId);
    const currentItem = currentBlock ? databaseRows(currentBlock)[rowIndex] : null;
    if (!currentItem) throw new Error("업로드 후 데이터베이스 행을 다시 찾을 수 없습니다.");
    currentItem[field] = current;
    currentItem._last_edited_time = new Date().toISOString();
    currentItem._last_edited_by = State.user?.id || "";
    markDirty();
    captureEditorHistory(true);
    onChange();
    renderFiles();
    setStatus("데이터베이스 파일을 업로드했습니다.", "ok");
  } catch (error) {
    setStatus("데이터베이스 파일 업로드 실패: " + error.message, "error");
  }
}

function removeDatabasePropertyFile(block, rowIndex, field, fileIndex, onChange = renderBlocks) {
  const item = databaseRows(block)[rowIndex];
  if (!item || !Array.isArray(item[field]) || !activePageCanEdit()) return;
  item[field].splice(fileIndex, 1);
  item._last_edited_time = new Date().toISOString();
  item._last_edited_by = State.user?.id || "";
  markDirty();
  captureEditorHistory(true);
  onChange();
}

function visibleDatabaseRows(block) {
  const rows = databaseRows(block);
  const query = String(block.props?.query || "").trim().toLowerCase();
  const filters = Array.isArray(block.props?.filters) ? block.props.filters : [];
  const sorts = Array.isArray(block.props?.sorts) ? block.props.sorts : [];
  let items = rows.map((item, index) => ({ item, index })).filter(({ item }) => !item?.archived);
  if (query) items = items.filter(({ item }) => Object.values(item || {}).some(value => String(value || "").toLowerCase().includes(query)));
  filters.forEach(filter => {
    const property = String(filter?.property || "");
    const operator = String(filter?.operator || "contains");
    const expected = String(filter?.value || "").toLowerCase();
    items = items.filter(({ item }) => {
      const actual = String(item?.[property] ?? "").toLowerCase();
      if (operator === "equals") return actual === expected;
      if (operator === "not_equals") return actual !== expected;
      if (operator === "not_contains") return !actual.includes(expected);
      if (operator === "greater_than") return Number(actual) > Number(expected);
      if (operator === "less_than") return Number(actual) < Number(expected);
      if (operator === "on_or_before") return actual && actual <= expected;
      if (operator === "on_or_after") return actual && actual >= expected;
      if (operator === "not_empty") return Boolean(actual);
      if (operator === "empty") return !actual;
      return actual.includes(expected);
    });
  });
  [...sorts].reverse().forEach(sort => {
    const property = String(sort?.property || "title");
    const direction = sort?.direction === "desc" ? -1 : 1;
    items.sort((a, b) => String(a.item?.[property] ?? "").localeCompare(String(b.item?.[property] ?? ""), undefined, { numeric: true }) * direction);
  });
  return items;
}

function renderDatabaseFullPageBreadcrumb(block) {
  const page = State.pageData?.page;
  if (!page || !block) {
    renderBreadcrumb(page || null);
    return;
  }
  renderBreadcrumb(page);
  const root = $("#breadcrumb");
  const separator = document.createElement("span");
  separator.className = "breadcrumb-sep";
  separator.textContent = "/";
  const current = document.createElement("span");
  current.className = "breadcrumb-segment database-breadcrumb-current";
  current.setAttribute("aria-current", "page");
  current.textContent = block.content || "Database";
  root.append(separator, current);
}

function applyDatabaseFullPagePresentation(block = null) {
  const view = $("#pageView");
  const active = Boolean(block);
  view?.classList.toggle("database-full-page", active);
  if (active) renderDatabaseFullPageBreadcrumb(block);
  else if (State.pageData?.page) renderBreadcrumb(State.pageData.page);
}

function openDatabaseFullPage(blockId) {
  const block = (State.pageData?.blocks || []).find(candidate => (
    candidate.id === String(blockId || "")
    && DATABASE_BLOCK_TYPES.has(candidate.type)
  ));
  if (!block) return false;
  State.databaseFullPageBlockId = block.id;
  closeDrawer();
  renderBlocks();
  $("#workspaceMain")?.scrollTo({ top: 0, behavior: "auto" });
  requestAnimationFrame(() => document.querySelector("[data-db-title]")?.focus({ preventScroll: true }));
  return true;
}

function closeDatabaseFullPage() {
  if (!State.databaseFullPageBlockId) return false;
  const blockId = State.databaseFullPageBlockId;
  State.databaseFullPageBlockId = "";
  renderBlocks();
  requestAnimationFrame(() => document.querySelector(`[data-block-id="${CSS.escape(blockId)}"] [data-db-open-full-page]`)?.focus({ preventScroll: true }));
  return true;
}

function databaseHeaderHtml(block, canEdit, view) {
  const index = State.pageData.blocks.indexOf(block);
  const views = databaseViews(block);
  const activeView = activeDatabaseView(block);
  const canEditSchema = canEdit && canEditDatabaseSchema();
  const showSchemaGhostControls = !canEditSchema
    && String(State.pageData?.page?.iam_role || "") === "edit_content";
  const showReadOnlySchemaControls = !canEditSchema
    && String(State.pageData?.page?.iam_role || "") === "comment";
  const fullPage = State.databaseFullPageBlockId === block.id;
  const presentedViews = fullPage && activeView ? [activeView] : views;
  return `
    <div class="database-head">
      <div class="database-title-cluster">
        <input class="database-title-input" data-db-title value="${escapeAttr(block.content || "데이터베이스")}" ${canEditSchema ? "" : "disabled"}>
        ${fullPage
          ? `<button type="button" class="database-full-page-toggle" data-db-close-full-page aria-label="인라인 데이터베이스로 돌아가기">×</button>`
          : `<button type="button" class="database-full-page-toggle" data-db-open-full-page="${escapeAttr(block.id)}" aria-label="전체 페이지로 열기">↗</button>`}
      </div>
      <button type="button" data-database-add="${index}" ${canEdit ? "" : "disabled"}>${iconSvg("plus")} 새로 만들기</button>
    </div>
    <div class="database-toolbar">
      <div class="database-view-tabs" role="tablist" aria-label="데이터베이스 보기">${presentedViews.map(item => `<button type="button" role="tab" aria-selected="${activeView?.id === item.id ? "true" : "false"}" data-db-view-id="${escapeAttr(item.id)}" class="${activeView?.id === item.id ? "active" : ""}" title="${escapeAttr(DATABASE_VIEW_LABELS[item.type] || item.type)}">${escapeHtml(fullPage ? (DATABASE_VIEW_LABELS[item.type] || item.name) : item.name)}</button>`).join("")}${canEditSchema && !fullPage ? `<button type="button" data-db-add-view title="보기 추가" aria-label="보기 추가">＋</button>` : ""}</div>
      ${fullPage && views.length > 1 ? `<select class="database-full-page-view-select" data-db-full-page-view-select aria-label="다른 데이터베이스 보기"><option value="" selected>+${views.length - 1}</option>${views.filter(item => item.id !== activeView?.id).map(item => `<option value="${escapeAttr(item.id)}">${escapeHtml(item.name)}</option>`).join("")}</select>` : ""}
      ${canEditSchema ? `<button type="button" data-db-view-menu title="현재 보기 설정">•••</button>` : ""}
      ${canEditSchema || showReadOnlySchemaControls ? `<button type="button" data-db-filter class="${block.props?.filters?.length ? "active" : ""}" ${canEditSchema ? "" : "disabled"}>필터</button>` : (showSchemaGhostControls ? '<span class="database-schema-control-ghost filter" aria-hidden="true"></span>' : "")}
      ${canEditSchema || showReadOnlySchemaControls ? `<button type="button" data-db-sort class="${block.props?.sorts?.length ? "active" : ""}" ${canEditSchema ? "" : "disabled"}>정렬</button>` : (showSchemaGhostControls ? '<span class="database-schema-control-ghost sort" aria-hidden="true"></span>' : "")}
      <button type="button" data-db-automation disabled>자동화</button>
      <button type="button" data-db-ai disabled>AI</button>
      <input data-db-search type="search" value="${escapeAttr(block.props?.query || "")}" placeholder="검색">
      <button type="button" data-db-expand disabled>확장</button>
      ${canEditSchema || showReadOnlySchemaControls ? `<button type="button" data-db-group class="${block.props?.group_property ? "active" : ""}" ${canEditSchema ? "" : "disabled"}>그룹</button>` : (showSchemaGhostControls ? '<span class="database-schema-control-ghost group" aria-hidden="true"></span>' : "")}
    </div>
  `;
}

function databaseBlockHtml(block, canEdit) {
  block.props = block.props || {};
  const view = DATABASE_BLOCK_TYPES.has(block.props.view)
    ? block.props.view
    : (DATABASE_BLOCK_TYPES.has(block.type) ? block.type : "table");
  block.props.view = view;
  if (view === "table") return tableBlockHtml(block, canEdit);
  if (view === "calendar") return calendarBlockHtml(block, canEdit);
  if (view === "timeline") return timelineBlockHtml(block, canEdit);
  if (view === "chart") return chartBlockHtml(block, canEdit);
  if (view === "dashboard") return dashboardBlockHtml(block, canEdit);
  if (view === "form") return formBlockHtml(block, canEdit);
  if (view === "feed") return feedBlockHtml(block, canEdit);
  if (view === "map") return mapBlockHtml(block, canEdit);
  const rows = visibleDatabaseRows(block);
  const header = databaseHeaderHtml(block, canEdit, view);
  const groupProperties = databaseProperties(block).filter(property => ["status", "select", "person", "checkbox", "multi_select"].includes(property.type));
  const groupProperty = groupProperties.find(property => property.id === block.props?.board_group_property) || groupProperties[0];
  const statusField = groupProperty?.id || databasePropertyId(block, ["status", "select"], "status");
  block.props.board_group_property = statusField;
  const dateField = databasePropertyId(block, "date", "date");
  const tagsField = databasePropertyId(block, "multi_select", "tags");
  if (view === "list") {
    return `<div class="database-block list-database">${header}<div class="database-list">${rows.map(({ item, index }) => `<button type="button" data-table-row="${index}">${pageIconHtml("")}<span>${escapeHtml(databaseRowTitle(block, item))}</span><small>${escapeHtml(item[statusField] || item[dateField] || "")}</small></button>`).join("")}</div></div>`;
  }
  if (view === "gallery") {
    return `<div class="database-block gallery-database">${header}<div class="gallery-grid">${rows.map(({ item, index }) => `<button type="button" class="gallery-card" data-table-row="${index}"><span class="gallery-cover">${escapeHtml(item.cover || "")}</span><strong>${escapeHtml(databaseRowTitle(block, item))}</strong><small>${escapeHtml(item[tagsField] || "")}</small></button>`).join("")}</div></div>`;
  }
  const groups = new Map();
  const predefinedGroups = groupProperty?.type === "checkbox"
    ? ["체크 안 됨", "체크됨"]
    : [...(groupProperty?.type === "status" ? ["시작 전", "진행 중", "완료"] : []), ...(Array.isArray(groupProperty?.options) ? groupProperty.options : [])];
  [...new Set(predefinedGroups.filter(Boolean))].forEach(key => groups.set(key, []));
  rows.forEach(entry => {
    const raw = entry.item[statusField];
    const keys = groupProperty?.type === "checkbox"
      ? [raw ? "체크됨" : "체크 안 됨"]
      : groupProperty?.type === "multi_select"
        ? String(raw || "").split(",").map(value => value.trim()).filter(Boolean)
        : [raw || "상태 없음"];
    (keys.length ? keys : ["상태 없음"]).forEach(key => {
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(entry);
    });
  });
  if (!groups.size) groups.set("상태 없음", []);
  return `<div class="database-block board-database">${header}${canEditDatabaseSchema() ? `<div class="board-view-controls"><label>그룹 <select data-board-group>${groupProperties.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === statusField ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select></label></div>` : ""}<div class="board-columns">${[...groups.entries()].map(([status, items]) => `<section class="board-column" data-board-status="${escapeAttr(status)}" data-board-property="${escapeAttr(statusField)}"><div class="board-column-title">${escapeHtml(status)} <small>${items.length}</small></div>${items.map(({ item, index }) => `<button type="button" draggable="${canEdit ? "true" : "false"}" class="board-card" data-table-row="${index}"><strong>${escapeHtml(databaseRowTitle(block, item))}</strong><small>${escapeHtml(item[dateField] || item[tagsField] || "")}</small></button>`).join("")}</section>`).join("")}</div></div>`;
}

function tableBlockHtml(block, canEdit) {
  const rows = visibleDatabaseRows(block);
  const properties = databaseProperties(block).filter(property => databasePropertyVisible(block, property));
  const rowGroups = groupedDatabaseRows(block, rows);
  const showReadOnlySchemaControls = !canEditDatabaseSchema()
    && String(State.pageData?.page?.iam_role || "") === "comment";
  const showSchemaGhostControls = !canEditDatabaseSchema()
    && String(State.pageData?.page?.iam_role || "") === "edit_content";
  return `
    <div class="database-block table-block">
      ${databaseHeaderHtml(block, canEdit, "table")}
      <div class="table-grid dynamic-table-grid" style="--database-columns:${properties.length}">
        <div class="table-row table-header">${properties.map(property => canEditDatabaseSchema() ? `<button type="button" data-db-property="${escapeAttr(property.id)}">${escapeHtml(property.name || property.id)} <small>${escapeHtml(property.type || "text")}</small></button>` : `<span>${escapeHtml(property.name || property.id)} <small>${escapeHtml(property.type || "text")}</small></span>`).join("")}</div>
        ${rowGroups.map(group => `${group.grouped ? `<div class="database-group-label"><span>${escapeHtml(group.label)} <small>${group.rows.length}</small></span></div>` : ""}${group.rows.map(({ item, index }) => `
            <div class="table-row">
              ${properties.map((property, propertyIndex) => `<span>${propertyIndex === 0 ? `${pageIconHtml("")}<button type="button" class="database-open-page database-side-peek" data-table-row="${index}" title="사이드 보기로 열기" aria-label="${escapeAttr(databaseRowTitle(block, item))} 사이드 보기로 열기">${sidePeekIconHtml()}</button>` : ""}${databasePropertyInput(block, property, item, index, canEdit)}</span>`).join("")}
            </div>
          `).join("")}`).join("")}
        ${canEditDatabaseSchema() || showReadOnlySchemaControls ? `<button type="button" class="database-add-property" data-db-add-property ${canEdit && canEditDatabaseSchema() ? "" : "disabled"}>＋ 속성 추가</button>` : (showSchemaGhostControls ? '<span class="database-add-property database-add-property-ghost" aria-hidden="true">＋ 속성 추가</span>' : "")}
      </div>
    </div>
  `;
}

function calendarBlockHtml(block, canEdit) {
  const visible = visibleDatabaseRows(block);
  const dateProperties = databaseProperties(block).filter(property => property.type === "date");
  const dateProperty = dateProperties.find(property => property.id === block.props?.calendar_date_property) || dateProperties[0];
  const dateField = dateProperty?.id || "date";
  block.props.calendar_date_property = dateField;
  const requestedMonth = /^\d{4}-\d{2}$/.test(block.props?.month || "") ? block.props.month : new Date().toISOString().slice(0, 7);
  const now = new Date(`${requestedMonth}-01T12:00:00`);
  const year = now.getFullYear();
  const month = now.getMonth();
  const first = new Date(year, month, 1, 12);
  const start = new Date(year, month, 1 - first.getDay(), 12);
  const cells = Array.from({ length: 42 }, (_, i) => {
    const d = new Date(start);
    d.setDate(start.getDate() + i);
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    const dayNumber = dateDayNumber(iso);
    const dayEvents = visible.filter(({ item }) => {
      const range = databaseDateRange(item, dateField);
      const startDay = dateDayNumber(range.start);
      const endDay = dateDayNumber(range.end || range.start);
      return Number.isFinite(dayNumber) && Number.isFinite(startDay) && dayNumber >= startDay && dayNumber <= endDay;
    });
    const out = d.getMonth() !== month ? " muted" : "";
    return `
      <div class="calendar-cell${out}" data-calendar-date="${iso}">
        <div class="calendar-day">${d.getDate()}</div>
        ${dayEvents.map(({ item, index }) => `
          <button type="button" class="calendar-event" data-calendar-row="${index}" data-calendar-page="${escapeAttr(item.page_id || "")}">${escapeHtml(databaseRowTitle(block, item))}</button>
        `).join("")}
        <button type="button" class="calendar-add" data-calendar-add="${iso}" ${canEdit ? "" : "disabled"}>${iconSvg("plus")}</button>
      </div>
    `;
  });
  return `
    <div class="database-block calendar-block">
      ${databaseHeaderHtml(block, canEdit, "calendar")}
      <div class="calendar-nav">${canEditDatabaseSchema() ? `<select data-calendar-property>${dateProperties.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === dateField ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select>` : ""}<button type="button" data-calendar-nav="-1">‹</button><strong>${year}년 ${month + 1}월</strong><button type="button" data-calendar-nav="1">›</button><button type="button" data-calendar-today>오늘</button></div>
      <div class="calendar-weekdays"><span>일</span><span>월</span><span>화</span><span>수</span><span>목</span><span>금</span><span>토</span></div>
      <div class="calendar-grid">${cells.join("")}</div>
    </div>
  `;
}

function databaseDateRange(item, field) {
  const value = item?.[field];
  const start = String(value && typeof value === "object" ? value.start || "" : value || "").slice(0, 10);
  const end = String(value && typeof value === "object" ? value.end || "" : item?.[`${field}_end`] || "").slice(0, 10);
  return {
    start: /^\d{4}-\d{2}-\d{2}$/.test(start) ? start : "",
    end: /^\d{4}-\d{2}-\d{2}$/.test(end) ? end : start,
  };
}

function localIsoDate(date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function dateDayNumber(value) {
  const parts = String(value || "").slice(0, 10).split("-").map(Number);
  return parts.length === 3 && parts.every(Number.isFinite) ? Math.floor(Date.UTC(parts[0], parts[1] - 1, parts[2]) / 86400000) : NaN;
}

function timelineBlockHtml(block, canEdit) {
  const properties = databaseProperties(block);
  const dateProperties = properties.filter(property => property.type === "date");
  const selected = dateProperties.find(property => property.id === block.props?.timeline_date_property) || dateProperties[0];
  const header = databaseHeaderHtml(block, canEdit, "timeline");
  if (!selected) {
    return `<div class="database-block timeline-block">${header}<div class="database-empty-view">타임라인에는 날짜 속성이 필요합니다.${canEdit ? " 표 보기에서 날짜 속성을 추가해 주세요." : ""}</div></div>`;
  }

  block.props.timeline_date_property = selected.id;
  const scale = ["month", "quarter", "year"].includes(block.props?.timeline_scale) ? block.props.timeline_scale : "month";
  block.props.timeline_scale = scale;
  const anchorValue = /^\d{4}-\d{2}-\d{2}$/.test(block.props?.timeline_anchor || "")
    ? block.props.timeline_anchor
    : localIsoDate(new Date());
  const anchor = new Date(`${anchorValue}T12:00:00`);
  let rangeStart;
  let rangeEnd;
  let slotCount;
  let rangeLabel;
  if (scale === "year") {
    rangeStart = new Date(anchor.getFullYear(), 0, 1, 12);
    rangeEnd = new Date(anchor.getFullYear() + 1, 0, 1, 12);
    slotCount = 12;
    rangeLabel = `${anchor.getFullYear()}년`;
  } else if (scale === "quarter") {
    const quarterMonth = Math.floor(anchor.getMonth() / 3) * 3;
    rangeStart = new Date(anchor.getFullYear(), quarterMonth, 1, 12);
    rangeEnd = new Date(anchor.getFullYear(), quarterMonth + 3, 1, 12);
    slotCount = Math.ceil((dateDayNumber(localIsoDate(rangeEnd)) - dateDayNumber(localIsoDate(rangeStart))) / 7);
    rangeLabel = `${rangeStart.getFullYear()}년 ${Math.floor(quarterMonth / 3) + 1}분기`;
  } else {
    rangeStart = new Date(anchor.getFullYear(), anchor.getMonth(), 1, 12);
    rangeEnd = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 1, 12);
    slotCount = dateDayNumber(localIsoDate(rangeEnd)) - dateDayNumber(localIsoDate(rangeStart));
    rangeLabel = `${rangeStart.getFullYear()}년 ${rangeStart.getMonth() + 1}월`;
  }
  const rangeStartIso = localIsoDate(rangeStart);
  const rangeStartDay = dateDayNumber(rangeStartIso);
  const rangeEndDay = dateDayNumber(localIsoDate(rangeEnd));
  const slotFor = iso => {
    const date = new Date(`${iso}T12:00:00`);
    if (scale === "year") return (date.getFullYear() - rangeStart.getFullYear()) * 12 + date.getMonth();
    const dayOffset = dateDayNumber(iso) - rangeStartDay;
    return scale === "quarter" ? Math.floor(dayOffset / 7) : dayOffset;
  };
  const slotLabels = Array.from({ length: slotCount }, (_, index) => {
    const date = new Date(rangeStart);
    if (scale === "year") date.setMonth(index);
    else date.setDate(date.getDate() + index * (scale === "quarter" ? 7 : 1));
    const label = scale === "year" ? `${date.getMonth() + 1}월` : scale === "quarter" ? `${date.getMonth() + 1}/${date.getDate()}` : `${date.getDate()}`;
    const weekend = scale === "month" && [0, 6].includes(date.getDay()) ? " weekend" : "";
    return `<span class="timeline-slot-label${weekend}">${label}</span>`;
  }).join("");
  const rows = visibleDatabaseRows(block);
  const timelineRows = rows.map(({ item, index }) => {
    const range = databaseDateRange(item, selected.id);
    const itemStartDay = dateDayNumber(range.start);
    const itemEndDay = dateDayNumber(range.end);
    const overlaps = range.start && itemStartDay < rangeEndDay && itemEndDay >= rangeStartDay;
    let bar = `<span class="timeline-no-date">${range.start ? "현재 범위 밖" : "날짜 없음"}</span>`;
    if (overlaps) {
      const startSlot = Math.max(0, Math.min(slotCount - 1, slotFor(range.start)));
      const endSlot = Math.max(startSlot, Math.min(slotCount - 1, slotFor(range.end)));
      const span = Math.max(1, endSlot - startSlot + 1);
      bar = `<button type="button" class="timeline-bar" data-table-row="${index}" style="grid-column:${startSlot + 2} / span ${span}" title="${escapeAttr(`${range.start}${range.end && range.end !== range.start ? ` → ${range.end}` : ""}`)}">${escapeHtml(databaseRowTitle(block, item))}</button>`;
    }
    return `<div class="timeline-row" style="--timeline-slots:${slotCount}"><button type="button" class="timeline-page" data-table-row="${index}">${pageIconHtml("")}<span>${escapeHtml(databaseRowTitle(block, item))}</span></button>${bar}</div>`;
  }).join("");
  return `<div class="database-block timeline-block">
    ${header}
    <div class="timeline-controls">
      ${canEditDatabaseSchema() ? `<select data-timeline-property>${dateProperties.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === selected.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select>` : ""}
      <button type="button" data-timeline-nav="-1">‹</button><strong>${rangeLabel}</strong><button type="button" data-timeline-nav="1">›</button>
      <button type="button" data-timeline-today>오늘</button>
      ${canEditDatabaseSchema() ? `<select data-timeline-scale><option value="month" ${scale === "month" ? "selected" : ""}>월</option><option value="quarter" ${scale === "quarter" ? "selected" : ""}>분기</option><option value="year" ${scale === "year" ? "selected" : ""}>연도</option></select>` : ""}
    </div>
    <div class="timeline-scroll"><div class="timeline-header" style="--timeline-slots:${slotCount}"><span>${escapeHtml(selected.name || "날짜")}</span>${slotLabels}</div>${timelineRows || '<div class="database-empty-view">표시할 페이지가 없습니다.</div>'}</div>
  </div>`;
}

function chartBlockHtml(block, canEdit) {
  const properties = databaseProperties(block);
  const groupCandidates = properties.filter(property => !["files", "button", "formula", "rollup"].includes(property.type));
  const numberProperties = properties.filter(property => property.type === "number");
  const defaultGroup = groupCandidates.find(property => ["status", "select", "multi_select"].includes(property.type)) || groupCandidates[0];
  const groupProperty = groupCandidates.find(property => property.id === block.props?.chart_group_property) || defaultGroup;
  const measureId = block.props?.chart_measure === "__count" || numberProperties.some(property => property.id === block.props?.chart_measure)
    ? block.props.chart_measure
    : "__count";
  const chartType = ["bar", "line", "donut"].includes(block.props?.chart_type) ? block.props.chart_type : "bar";
  block.props.chart_group_property = groupProperty?.id || "title";
  block.props.chart_measure = measureId;
  block.props.chart_type = chartType;

  const groups = new Map();
  visibleDatabaseRows(block).forEach(({ item }) => {
    const raw = item?.[groupProperty?.id || "title"];
    const values = groupProperty?.type === "multi_select"
      ? String(raw || "").split(",").map(value => value.trim()).filter(Boolean)
      : [String(raw || "비어 있음")];
    (values.length ? values : ["비어 있음"]).forEach(value => {
      const amount = measureId === "__count" ? 1 : Number(item?.[measureId] || 0);
      groups.set(value, (groups.get(value) || 0) + (Number.isFinite(amount) ? amount : 0));
    });
  });
  const entries = [...groups.entries()].slice(0, 50);
  const max = Math.max(1, ...entries.map(([, value]) => Math.abs(value)));
  const total = entries.reduce((sum, [, value]) => sum + Math.max(0, value), 0) || 1;
  const colors = ["#2383e2", "#9065b0", "#0f7b6c", "#d9730d", "#e03e3e", "#6b6b6b", "#cb912f", "#337ea9"];
  let visual = '<div class="database-empty-view">차트로 표시할 데이터가 없습니다.</div>';
  if (entries.length && chartType === "bar") {
    visual = `<div class="chart-bars">${entries.map(([label, value], index) => `<button type="button" class="chart-bar-row" data-chart-filter="${escapeAttr(label)}"><span>${escapeHtml(label)}</span><i style="--chart-width:${Math.max(2, Math.abs(value) / max * 100)}%;--chart-color:${colors[index % colors.length]}"></i><strong>${escapeHtml(value)}</strong></button>`).join("")}</div>`;
  } else if (entries.length && chartType === "line") {
    const points = entries.map(([, value], index) => {
      const x = entries.length === 1 ? 320 : 24 + index * (592 / (entries.length - 1));
      const y = 196 - Math.abs(value) / max * 168;
      return { x, y };
    });
    visual = `<div class="chart-line-wrap"><svg class="chart-line" viewBox="0 0 640 220" role="img" aria-label="선 차트"><path d="M ${points.map(point => `${point.x} ${point.y}`).join(" L ")}"></path>${points.map((point, index) => `<circle cx="${point.x}" cy="${point.y}" r="5"><title>${escapeHtml(`${entries[index][0]}: ${entries[index][1]}`)}</title></circle>`).join("")}</svg><div class="chart-legend">${entries.map(([label, value]) => `<button type="button" data-chart-filter="${escapeAttr(label)}"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></button>`).join("")}</div></div>`;
  } else if (entries.length && chartType === "donut") {
    let cursor = 0;
    const gradient = entries.map(([, value], index) => {
      const start = cursor;
      cursor += Math.max(0, value) / total * 100;
      return `${colors[index % colors.length]} ${start}% ${cursor}%`;
    }).join(",");
    visual = `<div class="chart-donut-wrap"><div class="chart-donut" style="background:conic-gradient(${gradient})"><span>${escapeHtml(Math.round(total * 100) / 100)}</span></div><div class="chart-legend">${entries.map(([label, value], index) => `<button type="button" data-chart-filter="${escapeAttr(label)}"><i style="--chart-color:${colors[index % colors.length]}"></i><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></button>`).join("")}</div></div>`;
  }
  return `<div class="database-block chart-block">
    ${databaseHeaderHtml(block, canEdit, "chart")}
    <div class="chart-controls">
      <label>그룹 <select data-chart-group ${canEdit ? "" : "disabled"}>${groupCandidates.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === groupProperty?.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select></label>
      <label>값 <select data-chart-measure ${canEdit ? "" : "disabled"}><option value="__count" ${measureId === "__count" ? "selected" : ""}>페이지 수</option>${numberProperties.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === measureId ? "selected" : ""}>${escapeHtml(`${property.name || property.id} 합계`)}</option>`).join("")}</select></label>
      <label>차트 <select data-chart-type ${canEdit ? "" : "disabled"}><option value="bar" ${chartType === "bar" ? "selected" : ""}>막대</option><option value="line" ${chartType === "line" ? "selected" : ""}>선</option><option value="donut" ${chartType === "donut" ? "selected" : ""}>도넛</option></select></label>
    </div>
    ${visual}
  </div>`;
}

function databaseDisplayValue(value) {
  if (value == null || value === "") return "";
  if (value === true) return "체크됨";
  if (value === false) return "체크 안 됨";
  if (Array.isArray(value)) return value.map(item => databaseDisplayValue(item?.name || item)).filter(Boolean).join(", ");
  if (typeof value === "object") {
    const start = value.start || "";
    const end = value.end || "";
    if (start || end) return `${start}${end && end !== start ? ` → ${end}` : ""}`;
    return "";
  }
  return String(value);
}

function dashboardWidgets(block) {
  block.props = block.props || {};
  let widgets = Array.isArray(block.props.dashboard_widgets)
    ? block.props.dashboard_widgets.filter(widget => widget && typeof widget === "object").slice(0, 12)
    : [];
  if (!widgets.length) {
    widgets = [
      { id: `widget_${Date.now().toString(36)}_count`, type: "stat", title: "전체 페이지", width: 1 },
      { id: `widget_${Date.now().toString(36)}_status`, type: "board", title: "상태별 페이지", width: 2 },
      { id: `widget_${Date.now().toString(36)}_recent`, type: "table", title: "최근 항목", width: 1 },
    ];
  }
  widgets.forEach((widget, index) => {
    widget.id = String(widget.id || `widget_${Date.now().toString(36)}_${index}`);
    widget.type = ["stat", "table", "board", "chart", "calendar", "timeline"].includes(widget.type) ? widget.type : "table";
    widget.title = String(widget.title || "위젯").slice(0, 100);
    widget.width = Math.max(1, Math.min(4, Number(widget.width || 1)));
  });
  block.props.dashboard_widgets = widgets;
  return widgets;
}

function dashboardWidgetContent(block, widget, rows) {
  const titleField = databasePropertyId(block, "title", "title");
  const statusField = databasePropertyId(block, ["status", "select"], "status");
  const dateField = databasePropertyId(block, "date", "date");
  if (widget.type === "stat") {
    return `<div class="dashboard-stat"><strong>${rows.length}</strong><span>페이지</span></div>`;
  }
  if (widget.type === "board" || widget.type === "chart") {
    const counts = new Map();
    rows.forEach(({ item }) => {
      const key = databaseDisplayValue(item?.[statusField]) || "상태 없음";
      counts.set(key, (counts.get(key) || 0) + 1);
    });
    const max = Math.max(1, ...counts.values());
    return `<div class="dashboard-groups">${[...counts.entries()].map(([label, count]) => `<div><span>${escapeHtml(label)}</span><i style="--dashboard-bar:${count / max * 100}%"></i><strong>${count}</strong></div>`).join("") || '<p class="database-empty-view">표시할 항목이 없습니다.</p>'}</div>`;
  }
  if (widget.type === "calendar" || widget.type === "timeline") {
    const dated = rows.filter(({ item }) => databaseDisplayValue(item?.[dateField])).sort((a, b) => databaseDisplayValue(a.item?.[dateField]).localeCompare(databaseDisplayValue(b.item?.[dateField]))).slice(0, 8);
    return `<div class="dashboard-list">${dated.map(({ item, index }) => `<button type="button" data-table-row="${index}"><time>${escapeHtml(databaseDisplayValue(item?.[dateField]).slice(0, 10))}</time><span>${escapeHtml(item?.[titleField] || "새 페이지")}</span></button>`).join("") || '<p class="database-empty-view">날짜가 있는 항목이 없습니다.</p>'}</div>`;
  }
  return `<div class="dashboard-list">${rows.slice(0, 10).map(({ item, index }) => `<button type="button" data-table-row="${index}"><span>${escapeHtml(item?.[titleField] || "새 페이지")}</span><small>${escapeHtml(databaseDisplayValue(item?.[statusField]) || databaseDisplayValue(item?.[dateField]))}</small></button>`).join("") || '<p class="database-empty-view">표시할 항목이 없습니다.</p>'}</div>`;
}

function dashboardBlockHtml(block, canEdit) {
  const rows = visibleDatabaseRows(block);
  const widgets = dashboardWidgets(block);
  const editing = canEdit && Boolean(block.props?.dashboard_edit_mode);
  const types = { stat: "수치", table: "표", board: "보드", chart: "차트", calendar: "캘린더", timeline: "타임라인" };
  return `<div class="database-block dashboard-block">
    ${databaseHeaderHtml(block, canEdit, "dashboard")}
    <div class="dashboard-toolbar"><span>위젯 ${widgets.length}/12</span>${canEdit ? `<button type="button" data-dashboard-mode>${editing ? "보기 완료" : "대시보드 편집"}</button>${editing && widgets.length < 12 ? '<button type="button" data-dashboard-add>＋ 위젯 추가</button>' : ""}` : ""}</div>
    <div class="dashboard-grid">${widgets.map((widget, index) => `<section class="dashboard-widget${editing ? " editing" : ""}" style="--dashboard-span:${widget.width}" data-dashboard-widget="${escapeAttr(widget.id)}">
      <div class="dashboard-widget-head">${editing ? `<input data-dashboard-title value="${escapeAttr(widget.title)}" maxlength="100"><select data-dashboard-widget-type>${Object.entries(types).map(([value, label]) => `<option value="${value}" ${widget.type === value ? "selected" : ""}>${label}</option>`).join("")}</select><select data-dashboard-widget-width title="너비"><option value="1" ${widget.width === 1 ? "selected" : ""}>1칸</option><option value="2" ${widget.width === 2 ? "selected" : ""}>2칸</option><option value="3" ${widget.width === 3 ? "selected" : ""}>3칸</option><option value="4" ${widget.width === 4 ? "selected" : ""}>4칸</option></select><button type="button" data-dashboard-widget-action="left" ${index === 0 ? "disabled" : ""}>←</button><button type="button" data-dashboard-widget-action="right" ${index === widgets.length - 1 ? "disabled" : ""}>→</button><button type="button" data-dashboard-widget-action="delete">×</button>` : `<strong>${escapeHtml(widget.title)}</strong>`}</div>
      ${dashboardWidgetContent(block, widget, rows)}
    </section>`).join("")}</div>
  </div>`;
}

function formWritableProperties(block) {
  const readOnly = new Set(["formula", "rollup", "created_time", "created_by", "last_edited_time", "last_edited_by", "button", "unique_id", "files"]);
  return databaseProperties(block).filter(property => !readOnly.has(property.type) && databasePropertyVisible(block, property));
}

function databaseFormControl(property, required) {
  const field = escapeAttr(property.id);
  const common = `data-form-field="${field}" ${required ? "required" : ""}`;
  if (property.type === "checkbox") return `<input type="checkbox" ${common}>`;
  if (property.type === "number") return `<input type="number" ${common}>`;
  if (property.type === "date") return `<input type="date" ${common}>`;
  if (property.type === "email") return `<input type="email" ${common} placeholder="name@example.com">`;
  if (property.type === "url") return `<input type="url" ${common} placeholder="https://">`;
  if (property.type === "phone") return `<input type="tel" ${common} placeholder="전화번호">`;
  if (property.type === "place") return `<input ${common} placeholder="주소 또는 장소">`;
  if (property.type === "person") {
    const people = [{ id: State.user?.id || "", display_name: State.user?.display_name || State.user?.id || "나" }, ...State.users]
      .filter((person, index, all) => person.id && all.findIndex(candidate => candidate.id === person.id) === index);
    return `<select ${common}><option value="">선택</option>${people.map(person => `<option value="${escapeAttr(person.id)}">${escapeHtml(person.display_name || person.id)}</option>`).join("")}</select>`;
  }
  if (["select", "status"].includes(property.type)) {
    const defaults = property.type === "status" ? ["시작 전", "진행 중", "완료"] : [];
    const options = [...new Set([...(property.options || []), ...defaults].filter(Boolean))];
    return `<select ${common}><option value="">선택</option>${options.map(option => `<option value="${escapeAttr(option)}">${escapeHtml(option)}</option>`).join("")}</select>`;
  }
  if (property.type === "multi_select") return `<input ${common} placeholder="쉼표로 여러 값을 구분">`;
  if (property.type === "relation") return `<select ${common}><option value="">연결 없음</option>${visiblePages().filter(page => page.id !== State.activePageId).map(page => `<option value="${escapeAttr(page.id)}">${escapeHtml(page.title || "제목 없음")}</option>`).join("")}</select>`;
  if (property.type === "text") return `<textarea ${common} rows="3"></textarea>`;
  return `<input ${common} placeholder="${escapeAttr(property.name || "응답")}">`;
}

function formBlockHtml(block, canEdit) {
  block.props = block.props || {};
  const fields = formWritableProperties(block);
  const required = new Set(Array.isArray(block.props.form_required_properties) ? block.props.form_required_properties : []);
  const title = block.props.form_title || block.content || "새 폼";
  const description = block.props.form_description || "";
  const submitLabel = block.props.form_submit_label || "제출";
  return `<div class="database-block form-database">
    ${databaseHeaderHtml(block, canEdit, "form")}
    ${canEdit ? `<div class="form-builder-controls"><label>폼 제목<input data-form-config="form_title" value="${escapeAttr(title)}" maxlength="160"></label><label>설명<textarea data-form-config="form_description" rows="2">${escapeHtml(description)}</textarea></label><label>제출 버튼<input data-form-config="form_submit_label" value="${escapeAttr(submitLabel)}" maxlength="40"></label><label>제출 후 메시지<input data-form-config="form_success_message" value="${escapeAttr(block.props.form_success_message || "응답이 제출되었습니다.")}" maxlength="200"></label></div>` : ""}
    <form class="database-form" data-database-form>
      <header><h3>${escapeHtml(title)}</h3>${description ? `<p>${escapeHtml(description)}</p>` : ""}</header>
      ${fields.map(property => `<label class="database-form-field"><span>${escapeHtml(property.name || property.id)}${required.has(property.id) ? '<b aria-label="필수">*</b>' : ""}</span>${canEdit && property.type !== "title" ? `<small><input type="checkbox" data-form-required="${escapeAttr(property.id)}" ${required.has(property.id) ? "checked" : ""}> 필수</small>` : ""}${databaseFormControl(property, required.has(property.id))}</label>`).join("")}
      ${fields.length ? `<button type="submit" class="database-form-submit">${escapeHtml(submitLabel)}</button>` : '<p class="database-empty-view">제출 가능한 속성이 없습니다.</p>'}
      <output data-form-result aria-live="polite"></output>
    </form>
  </div>`;
}

function feedBlockHtml(block, canEdit) {
  const dateProperties = databaseProperties(block).filter(property => ["date", "created_time", "last_edited_time"].includes(property.type));
  const selected = dateProperties.find(property => property.id === block.props?.feed_date_property) || dateProperties[0];
  block.props.feed_date_property = selected?.id || "";
  const rows = visibleDatabaseRows(block).slice().sort((a, b) => {
    const value = item => selected?.type === "created_time" ? item._created_time : selected?.type === "last_edited_time" ? item._last_edited_time : item?.[selected?.id || ""];
    return databaseDisplayValue(value(b.item)).localeCompare(databaseDisplayValue(value(a.item)));
  });
  const visibleProps = databaseProperties(block).filter(property => property.type !== "title" && databasePropertyVisible(block, property)).slice(0, 4);
  return `<div class="database-block feed-database">
    ${databaseHeaderHtml(block, canEdit, "feed")}
    <div class="feed-controls"><label>피드 날짜 <select data-feed-property ${canEdit ? "" : "disabled"}>${dateProperties.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === selected?.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select></label></div>
    <div class="database-feed">${rows.map(({ item, index }) => {
      const dateValue = selected?.type === "created_time" ? item._created_time : selected?.type === "last_edited_time" ? item._last_edited_time : item?.[selected?.id || ""];
      return `<article class="feed-card"><button type="button" data-table-row="${index}" class="feed-card-main"><span class="feed-avatar">${escapeHtml(String(item._last_edited_by || item._created_by || "?").slice(0, 1).toUpperCase())}</span><span><strong>${escapeHtml(databaseRowTitle(block, item))}</strong><time>${escapeHtml(databaseDisplayValue(dateValue) || "날짜 없음")}</time></span></button><div class="feed-properties">${visibleProps.map(property => { const value = databaseDisplayValue(item?.[property.id]); return value ? `<span><small>${escapeHtml(property.name || property.id)}</small>${escapeHtml(value)}</span>` : ""; }).join("")}</div></article>`;
    }).join("") || '<p class="database-empty-view">피드에 표시할 항목이 없습니다.</p>'}</div>
  </div>`;
}

function mapPinPosition(value, axis) {
  let hash = axis === "x" ? 17 : 53;
  for (const char of String(value || "")) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return 8 + (hash % 84);
}

function mapBlockHtml(block, canEdit) {
  const candidates = databaseProperties(block).filter(property => ["place", "text"].includes(property.type));
  const selected = candidates.find(property => property.id === block.props?.map_place_property) || candidates.find(property => property.type === "place") || candidates[0];
  block.props.map_place_property = selected?.id || "";
  const rows = visibleDatabaseRows(block).filter(({ item }) => databaseDisplayValue(item?.[selected?.id || ""]));
  return `<div class="database-block map-database">
    ${databaseHeaderHtml(block, canEdit, "map")}
    <div class="map-controls"><label>장소 <select data-map-property ${canEdit ? "" : "disabled"}>${candidates.map(property => `<option value="${escapeAttr(property.id)}" ${property.id === selected?.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select></label></div>
    ${selected ? `<div class="database-map"><div class="map-canvas" role="img" aria-label="${escapeAttr(selected.name || "장소")} 지도">${rows.map(({ item, index }) => { const address = databaseDisplayValue(item[selected.id]); return `<button type="button" class="map-pin" data-table-row="${index}" style="--map-x:${mapPinPosition(address, "x")};--map-y:${mapPinPosition(address, "y")}" title="${escapeAttr(`${databaseRowTitle(block, item)} · ${address}`)}"><span>${index + 1}</span></button>`; }).join("")}<div class="map-grid-lines"></div></div><div class="map-list">${rows.map(({ item, index }) => { const address = databaseDisplayValue(item[selected.id]); const mapUrl = `https://www.openstreetmap.org/search?query=${encodeURIComponent(address)}`; return `<div><button type="button" data-table-row="${index}"><strong>${escapeHtml(databaseRowTitle(block, item))}</strong><span>${escapeHtml(address)}</span></button><a href="${escapeAttr(mapUrl)}" target="_blank" rel="noopener">지도에서 열기</a></div>`; }).join("") || '<p class="database-empty-view">장소가 있는 항목이 없습니다.</p>'}</div></div>` : '<p class="database-empty-view">지도 보기에는 장소 또는 텍스트 속성이 필요합니다.</p>'}
  </div>`;
}

async function submitDatabaseForm(block, form) {
  if (!block?.id || !form || !State.activePageId) return;
  const pageId = State.activePageId;
  const values = {};
  form.querySelectorAll("[data-form-field]").forEach(input => {
    values[input.dataset.formField || ""] = input.type === "checkbox" ? input.checked : input.value;
  });
  const button = form.querySelector("[type=submit]");
  const output = form.querySelector("[data-form-result]");
  if (button) button.disabled = true;
  if (output) output.textContent = "제출 중...";
  try {
    if (!(await flushPageDraft(pageId))) {
      if (output) output.textContent = "저장되지 않은 편집본을 먼저 해결해 주세요.";
      return;
    }
    const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/forms/${encodeURIComponent(block.id)}/submit`, {
      method: "POST",
      body: { values },
    });
    const row = data.row || {};
    const refreshResult = await refreshOpenedPageAfterMutation(pageId);
    if (refreshResult.failed) throw refreshResult.error;
    if (refreshResult.mainConflicted || refreshResult.sidePeekConflicted) {
      form.reset();
      return;
    }
    form.reset();
    if (output) output.textContent = data.message || block.props.form_success_message || "응답이 제출되었습니다.";
    await loadPages();
    setStatus("폼 응답이 제출되었습니다.", "ok");
  } catch (error) {
    if (output) output.textContent = "제출 실패: " + error.message;
    setStatus("폼 제출 실패: " + error.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

async function runNotionButton(block, index) {
  const action = block.props?.action || "insert_block";
  if (action === "open_url") {
    const url = safeExternalUrl(block.props?.url || "");
    if (url) window.open(url, "_blank", "noopener");
    else setStatus("버튼 URL을 설정해 주세요.", "error");
    return;
  }
  if (!activePageCanEdit()) return;
  if (action === "insert_block") {
    insertBlockAfter(index, "paragraph", true);
    return;
  }
  if (action === "new_page") {
    try {
      const data = await api("/api/notion/pages", {
        method: "POST",
        body: { title: block.props?.new_page_title || "새 페이지", parent_id: State.activePageId },
      });
      await loadPages();
      await openPage(data.page.id);
    } catch (error) {
      setStatus("버튼 실행 실패: " + error.message, "error");
    }
  }
}

function attachBlockExtras(row, block, index, canEdit) {
  row.querySelectorAll("[data-tab-select]").forEach(button => button.addEventListener("click", () => selectTabsBlockTab(block, button.dataset.tabSelect || "")));
  row.querySelectorAll("[data-tab-menu]").forEach(button => button.addEventListener("click", () => openTabsBlockTabSettings(block, button.dataset.tabMenu || "")));
  row.querySelector("[data-tab-add]")?.addEventListener("click", () => addTabsBlockTab(block, index));
  row.querySelector("[data-code-language]")?.addEventListener("change", event => {
    block.props = block.props || {};
    block.props.language = event.target.value;
    markDirty();
  });
  row.querySelector("[data-code-caption]")?.addEventListener("input", event => {
    block.props = block.props || {};
    block.props.caption = event.target.value;
    markDirty();
  });
  row.querySelector("[data-code-copy]")?.addEventListener("click", () => copyText(block.content || "", "코드 복사됨"));
  row.querySelectorAll("[data-breadcrumb-page]").forEach(button => button.addEventListener("click", () => openPage(button.dataset.breadcrumbPage)));
  row.querySelector("[data-linked-page-open]")?.addEventListener("click", event => {
    const pageId = event.currentTarget.dataset.linkedPageOpen;
    if (event.altKey) openSidePeek(pageId);
    else openPage(pageId);
  });
  row.querySelector("[data-linked-page-select]")?.addEventListener("change", event => {
    if (!event.target.value) return;
    block.props = block.props || {};
    block.props.page_id = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelectorAll("[data-simple-cell]").forEach(input => input.addEventListener("input", event => {
    const rowIndex = Number(event.target.dataset.row);
    const columnIndex = Number(event.target.dataset.column);
    block.props.rows[rowIndex][columnIndex] = event.target.value;
    markDirty();
  }));
  row.querySelector("[data-simple-add-row]")?.addEventListener("click", () => {
    const count = Math.max(1, ...(block.props.rows || []).map(item => item.length));
    block.props.rows.push(Array(count).fill(""));
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelector("[data-simple-add-column]")?.addEventListener("click", () => {
    block.props.rows.forEach(item => item.push(""));
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelector("[data-simple-header-row]")?.addEventListener("change", event => {
    block.props.header_row = event.target.checked;
    markDirty();
    renderBlocks();
  });
  row.querySelector("[data-simple-header-column]")?.addEventListener("change", event => {
    block.props.header_column = event.target.checked;
    markDirty();
    renderBlocks();
  });
  row.querySelector("[data-run-button]")?.addEventListener("click", () => runNotionButton(block, index));
  const buttonLabel = row.querySelector("[data-button-label]");
  buttonLabel?.addEventListener("input", () => {
    block.props = block.props || {};
    block.props.label = buttonLabel.value;
    block.content = buttonLabel.value;
    markDirty();
  });
  row.querySelector("[data-button-action]")?.addEventListener("change", event => {
    block.props = block.props || {};
    block.props.action = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelector("[data-button-url]")?.addEventListener("change", event => {
    block.props.url = safeExternalUrl(event.target.value);
    markDirty();
  });
  row.querySelector(`[data-link-copy="${index}"]`)?.addEventListener("click", e => {
    e.preventDefault();
    copyText(normalizeUrl(block.props?.link_url || ""), "링크 복사됨");
  });
  row.querySelector(`[data-link-edit="${index}"]`)?.addEventListener("click", e => openLinkPopover(index, e.currentTarget));
  row.querySelector(`[data-media-comment="${index}"]`)?.addEventListener("click", () => editMediaComment(index));
  row.querySelector(`[data-media-caption="${index}"]`)?.addEventListener("click", () => row.querySelector(".block-editor")?.focus());
  row.querySelector(`[data-action-plus="${index}"]`)?.addEventListener("click", () => insertBlockAfter(index, "paragraph", true));
  row.querySelector(`[data-media-ai="${index}"]`)?.addEventListener("click", () => askAboutMediaBlock(block));
  row.querySelector(`[data-media-more="${index}"]`)?.addEventListener("click", e => openBlockMenu(index, e.currentTarget, "actions"));
  row.querySelector("[data-child-page]")?.addEventListener("click", e => {
    const pageId = e.currentTarget.dataset.childPage;
    if (pageId) {
      if (e.altKey) openSidePeek(pageId);
      else openPage(pageId);
    }
  });
  row.querySelector("[data-child-page-side]")?.addEventListener("click", e => {
    const pageId = e.currentTarget.dataset.childPageSide;
    if (pageId) openSidePeek(pageId);
  });
  row.querySelector("[data-child-page-restore]")?.addEventListener("click", async e => {
    const pageId = e.currentTarget.dataset.childPageRestore;
    if (!pageId) return;
    try {
      await restorePageSafely(pageId);
      await loadPages();
      setStatus("하위 페이지를 복원했습니다.", "ok");
    } catch (error) {
      setStatus("하위 페이지 복원 실패: " + error.message, "error");
    }
  });
  row.querySelector(`[data-database-add="${index}"]`)?.addEventListener("click", () => {
    const view = block.props?.view || block.type;
    const datedView = ["calendar", "timeline"].includes(view);
    createDatabaseItem(index, datedView ? view : "table", datedView ? new Date().toISOString().slice(0, 10) : "");
  });
  row.querySelectorAll("[data-table-row]").forEach(btn => btn.addEventListener("click", () => {
    const rowIndex = Number(btn.dataset.tableRow);
    const item = block.props?.rows?.[rowIndex];
    if (item?.page_id) openSubPageModal(item.page_id, item);
  }));
  row.querySelectorAll("[data-calendar-add]").forEach(btn => btn.addEventListener("click", () => createDatabaseItem(index, "calendar", btn.dataset.calendarAdd)));
  row.querySelectorAll("[data-calendar-page]").forEach(btn => btn.addEventListener("click", () => {
    const pageId = btn.dataset.calendarPage;
    if (!pageId) return;
    const eventIndex = Number(btn.dataset.calendarRow);
    const items = Array.isArray(block.props?.rows) ? block.props.rows : (block.props?.events || []);
    const item = items[eventIndex] || { page_id: pageId };
    openSubPageModal(pageId, item);
  }));
  row.querySelectorAll("[data-db-view-id]").forEach(button => button.addEventListener("click", () => {
    const viewId = button.dataset.dbViewId || "";
    activateDatabaseView(block, viewId);
    if (canEditDatabaseSchema()) {
      markDirty();
      captureEditorHistory(true);
    }
    renderBlocks();
    focusDatabaseViewTab(block, viewId);
  }));
  row.querySelector("[data-db-full-page-view-select]")?.addEventListener("change", event => {
    const viewId = event.target.value || "";
    if (!viewId) return;
    activateDatabaseView(block, viewId);
    if (canEditDatabaseSchema()) {
      markDirty();
      captureEditorHistory(true);
    }
    renderBlocks();
    document.querySelector("[data-db-full-page-view-select]")?.focus({ preventScroll: true });
  });
  row.querySelector("[data-db-add-view]")?.addEventListener("click", () => openDatabaseViewDialog(index));
  row.querySelector("[data-db-view-menu]")?.addEventListener("click", () => openDatabaseViewDialog(index, block.props?.active_view_id || ""));
  const dbTitle = row.querySelector("[data-db-title]");
  dbTitle?.addEventListener("input", () => {
    block.content = dbTitle.value;
    if (State.databaseFullPageBlockId === block.id) {
      const current = document.querySelector(".database-breadcrumb-current");
      if (current) current.textContent = block.content || "Database";
    }
    markDirty();
  });
  row.querySelector("[data-db-open-full-page]")?.addEventListener("click", () => openDatabaseFullPage(block.id));
  row.querySelector("[data-db-close-full-page]")?.addEventListener("click", () => closeDatabaseFullPage());
  const dbSearch = row.querySelector("[data-db-search]");
  dbSearch?.addEventListener("input", () => {
    block.props = block.props || {};
    block.props.query = dbSearch.value;
    clearTimeout(State.dbSearchTimer);
    State.dbSearchTimer = setTimeout(() => {
      renderBlocks();
      const next = document.querySelector(`.block-row[data-index="${index}"] [data-db-search]`);
      next?.focus();
      next?.setSelectionRange(next.value.length, next.value.length);
    }, 180);
  });
  row.querySelector("[data-db-filter]")?.addEventListener("click", () => openDatabaseFilterDialog(index));
  row.querySelector("[data-db-sort]")?.addEventListener("click", () => openDatabaseSortDialog(index));
  row.querySelector("[data-db-group]")?.addEventListener("click", () => openDatabaseGroupDialog(index));
  row.querySelector("[data-db-add-property]")?.addEventListener("click", () => openDatabasePropertyDialog(index));
  row.querySelectorAll("[data-db-property]").forEach(button => button.addEventListener("click", () => openDatabasePropertyDialog(index, button.dataset.dbProperty)));
  row.querySelectorAll("[data-db-row][data-db-field]").forEach(input => input.addEventListener("change", async () => {
    const rowIndex = Number(input.dataset.dbRow);
    const field = input.dataset.dbField;
    const item = databaseRows(block)[rowIndex];
    if (!item || !field) return;
    item[field] = input.type === "checkbox"
      ? input.checked
      : input.multiple
        ? [...input.selectedOptions].map(option => option.value).filter(Boolean)
        : input.value;
    item._last_edited_time = new Date().toISOString();
    item._last_edited_by = State.user?.id || "";
    const property = databaseProperties(block).find(candidate => candidate.id === field);
    if (canEditDatabaseSchema() && ["select", "multi_select", "status"].includes(property?.type)) {
      const values = String(item[field] || "").split(",").map(value => value.trim()).filter(Boolean);
      property.options = [...new Set([...(Array.isArray(property.options) ? property.options : []), ...values])].slice(0, 200);
    }
    markDirty();
    captureEditorHistory(true);
    if (property?.type === "title" && item.page_id) {
      try {
        if (!(await flushPageDraft(item.page_id))) {
          setStatus("열려 있는 데이터베이스 페이지의 편집본을 먼저 해결해 주세요.", "error");
          return;
        }
        const data = await api(`/api/notion/pages/${encodeURIComponent(item.page_id)}`, { method: "PATCH", body: { title: input.value || "새 페이지" } });
        await refreshOpenedPageAfterMutation(item.page_id, data);
        await loadPages();
      } catch (error) {
        setStatus("데이터베이스 페이지 이름 저장 실패: " + error.message, "error");
      }
    }
    renderBlocks();
  }));
  row.querySelectorAll("[data-db-row][data-db-date-end]").forEach(input => input.addEventListener("change", () => {
    const rowIndex = Number(input.dataset.dbRow);
    const field = input.dataset.dbDateEnd;
    const item = databaseRows(block)[rowIndex];
    if (!item || !field) return;
    item[`${field}_end`] = input.value;
    item._last_edited_time = new Date().toISOString();
    item._last_edited_by = State.user?.id || "";
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  }));
  row.querySelectorAll("[data-db-row][data-db-date-reminder]").forEach(button => button.addEventListener("click", () => {
    openDatabaseDateReminderDialog(block, Number(button.dataset.dbRow), button.dataset.dbDateReminder || "");
  }));
  row.querySelectorAll("[data-db-row][data-db-file-input]").forEach(input => input.addEventListener("change", () => {
    uploadDatabasePropertyFiles(block, Number(input.dataset.dbRow), input.dataset.dbFileInput || "", input.files);
  }));
  row.querySelectorAll("[data-db-row][data-db-file-remove]").forEach(button => button.addEventListener("click", () => {
    removeDatabasePropertyFile(block, Number(button.dataset.dbRow), button.dataset.dbFileRemove || "", Number(button.dataset.fileIndex));
  }));
  row.querySelectorAll("[data-db-cell-button]").forEach(button => button.addEventListener("click", () => {
    const property = databaseProperties(block).find(candidate => candidate.id === button.dataset.dbCellButton);
    const url = safeExternalUrl(property?.button_url || "");
    if (url) window.open(url, "_blank", "noopener");
    else setStatus("데이터베이스 버튼 URL을 설정해 주세요.", "error");
  }));
  row.querySelectorAll("[data-calendar-nav]").forEach(button => button.addEventListener("click", () => {
    const current = /^\d{4}-\d{2}$/.test(block.props?.month || "") ? block.props.month : new Date().toISOString().slice(0, 7);
    const date = new Date(`${current}-01T12:00:00`);
    date.setMonth(date.getMonth() + Number(button.dataset.calendarNav || 0));
    block.props.month = `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
    if (canEditDatabaseSchema()) markDirty();
    renderBlocks();
  }));
  row.querySelector("[data-calendar-today]")?.addEventListener("click", () => {
    block.props.month = new Date().toISOString().slice(0, 7);
    if (canEditDatabaseSchema()) markDirty();
    renderBlocks();
  });
  row.querySelector("[data-calendar-property]")?.addEventListener("change", event => {
    block.props.calendar_date_property = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelector("[data-board-group]")?.addEventListener("change", event => {
    block.props.board_group_property = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelectorAll("[data-timeline-nav]").forEach(button => button.addEventListener("click", () => {
    const current = /^\d{4}-\d{2}-\d{2}$/.test(block.props?.timeline_anchor || "") ? block.props.timeline_anchor : localIsoDate(new Date());
    const date = new Date(`${current}T12:00:00`);
    const step = block.props?.timeline_scale === "year" ? 12 : block.props?.timeline_scale === "quarter" ? 3 : 1;
    date.setMonth(date.getMonth() + Number(button.dataset.timelineNav || 0) * step);
    block.props.timeline_anchor = localIsoDate(date);
    if (canEditDatabaseSchema()) markDirty();
    renderBlocks();
  }));
  row.querySelector("[data-timeline-today]")?.addEventListener("click", () => {
    block.props.timeline_anchor = localIsoDate(new Date());
    if (canEditDatabaseSchema()) markDirty();
    renderBlocks();
  });
  row.querySelector("[data-timeline-scale]")?.addEventListener("change", event => {
    block.props.timeline_scale = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelector("[data-timeline-property]")?.addEventListener("change", event => {
    block.props.timeline_date_property = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  [["[data-chart-group]", "chart_group_property"], ["[data-chart-measure]", "chart_measure"], ["[data-chart-type]", "chart_type"]].forEach(([selector, property]) => {
    row.querySelector(selector)?.addEventListener("change", event => {
      block.props[property] = event.target.value;
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    });
  });
  row.querySelectorAll("[data-chart-filter]").forEach(button => button.addEventListener("click", () => {
    const property = block.props?.chart_group_property || databasePropertyId(block, "title", "title");
    const value = button.dataset.chartFilter || "";
    const filters = Array.isArray(block.props.filters) ? block.props.filters : [];
    const current = filters.find(filter => filter.property === property && filter.operator === "equals");
    block.props.filters = current?.value === value
      ? filters.filter(filter => filter !== current)
      : [...filters.filter(filter => filter.property !== property), { property, operator: "equals", value }];
    if (canEdit) {
      markDirty();
      captureEditorHistory(true);
    }
    renderBlocks();
  }));
  row.querySelector("[data-dashboard-mode]")?.addEventListener("click", () => {
    block.props.dashboard_edit_mode = !block.props.dashboard_edit_mode;
    markDirty();
    renderBlocks();
  });
  row.querySelector("[data-dashboard-add]")?.addEventListener("click", () => {
    const widgets = dashboardWidgets(block);
    if (widgets.length >= 12) return;
    widgets.push({ id: `widget_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`, type: "table", title: "새 위젯", width: 1 });
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelectorAll("[data-dashboard-widget]").forEach(element => {
    const widget = dashboardWidgets(block).find(item => item.id === element.dataset.dashboardWidget);
    if (!widget) return;
    element.querySelector("[data-dashboard-title]")?.addEventListener("change", event => {
      widget.title = event.target.value.trim().slice(0, 100) || "위젯";
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    });
    element.querySelector("[data-dashboard-widget-type]")?.addEventListener("change", event => {
      widget.type = event.target.value;
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    });
    element.querySelector("[data-dashboard-widget-width]")?.addEventListener("change", event => {
      widget.width = Math.max(1, Math.min(4, Number(event.target.value || 1)));
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    });
    element.querySelectorAll("[data-dashboard-widget-action]").forEach(button => button.addEventListener("click", () => {
      const widgets = dashboardWidgets(block);
      const widgetIndex = widgets.findIndex(item => item.id === widget.id);
      const action = button.dataset.dashboardWidgetAction;
      if (action === "delete") widgets.splice(widgetIndex, 1);
      else if (action === "left" && widgetIndex > 0) [widgets[widgetIndex - 1], widgets[widgetIndex]] = [widgets[widgetIndex], widgets[widgetIndex - 1]];
      else if (action === "right" && widgetIndex >= 0 && widgetIndex < widgets.length - 1) [widgets[widgetIndex + 1], widgets[widgetIndex]] = [widgets[widgetIndex], widgets[widgetIndex + 1]];
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    }));
  });
  row.querySelectorAll("[data-form-config]").forEach(input => input.addEventListener("change", () => {
    const key = input.dataset.formConfig || "";
    if (!key) return;
    block.props[key] = input.value.slice(0, key === "form_description" ? 2000 : 200);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  }));
  row.querySelectorAll("[data-form-required]").forEach(input => input.addEventListener("change", () => {
    const id = input.dataset.formRequired || "";
    const required = new Set(Array.isArray(block.props.form_required_properties) ? block.props.form_required_properties : []);
    if (input.checked) required.add(id);
    else required.delete(id);
    block.props.form_required_properties = [...required].filter(Boolean);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  }));
  row.querySelector("[data-database-form]")?.addEventListener("submit", event => {
    event.preventDefault();
    submitDatabaseForm(block, event.currentTarget);
  });
  row.querySelector("[data-feed-property]")?.addEventListener("change", event => {
    block.props.feed_date_property = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  row.querySelector("[data-map-property]")?.addEventListener("change", event => {
    block.props.map_place_property = event.target.value;
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
  });
  bindBoardCardDrag(row, block, canEdit);
  const urlInput = row.querySelector("[data-block-url]");
  if (urlInput) {
    const commitUrl = () => {
      block.props = block.props || {};
      block.props.url = safeExternalUrl(urlInput.value);
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    };
    urlInput.addEventListener("keydown", event => {
      if (event.key === "Enter") {
        event.preventDefault();
        commitUrl();
      }
    });
    urlInput.addEventListener("blur", commitUrl, { once: true });
  }
  if (!canEdit) {
    row.querySelectorAll("[data-link-edit], [data-media-comment], [data-media-caption], [data-action-plus]").forEach(btn => {
      btn.disabled = true;
    });
  }
}

function openDatabaseViewDialog(blockIndex, viewId = "") {
  const block = State.pageData?.blocks?.[blockIndex];
  if (!block || !canEditDatabaseSchema()) return;
  syncActiveDatabaseView(block);
  const views = databaseViews(block);
  const view = views.find(item => item.id === viewId) || null;
  if (!view && views.length >= 100) {
    setStatus("데이터베이스 보기는 최대 100개까지 만들 수 있습니다.", "error");
    return;
  }
  const initialType = view?.type || "table";
  openModal(view ? "보기 설정" : "새 보기", `
    <div class="database-rule-form">
      <label>보기 이름<input id="dbViewName" value="${escapeAttr(view?.name || DATABASE_VIEW_LABELS[initialType])}" maxlength="100" placeholder="보기 이름"></label>
      <label>레이아웃<select id="dbViewType">${Object.entries(DATABASE_VIEW_LABELS).map(([value, label]) => `<option value="${value}" ${initialType === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
    </div>
  `, [
    ...(view ? [{ label: "복제", action: () => {
      syncActiveDatabaseView(block);
      const copy = EditorCore.deepClone(view);
      copy.id = `view_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
      copy.name = `${view.name || DATABASE_VIEW_LABELS[view.type]} 사본`.slice(0, 100);
      block.props.views.push(copy);
      applyDatabaseView(block, copy);
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } }] : []),
    ...(view && views.length > 1 ? [{ label: "보기 삭제", action: () => {
      block.props.views = views.filter(item => item.id !== view.id);
      applyDatabaseView(block, block.props.views[0]);
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } }] : []),
    { label: "취소", action: closeModal },
    { label: "저장", primary: true, action: () => {
      const type = DATABASE_BLOCK_TYPES.has($("#dbViewType").value) ? $("#dbViewType").value : "table";
      const name = $("#dbViewName").value.trim().slice(0, 100) || DATABASE_VIEW_LABELS[type];
      if (view) {
        view.name = name;
        view.type = type;
        applyDatabaseView(block, view);
      } else {
        syncActiveDatabaseView(block);
        const created = {
          id: `view_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`,
          name,
          type,
          filters: [],
          sorts: [],
          group_property: "",
          timeline_scale: "month",
          chart_measure: "__count",
          chart_type: "bar",
          board_group_property: "",
          calendar_date_property: "",
          property_visibility: Object.fromEntries(databaseProperties(block).map(property => [property.id, true])),
        };
        block.props.views.push(created);
        applyDatabaseView(block, created);
      }
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
  ]);
  setTimeout(() => $("#dbViewName")?.select(), 0);
}

function openDatabasePropertyDialog(blockIndex, propertyId = "") {
  const block = State.pageData?.blocks?.[blockIndex];
  if (!block || !canEditDatabaseSchema()) return;
  const properties = databaseProperties(block);
  const property = properties.find(item => item.id === propertyId) || null;
  if (!property && properties.length >= 500) {
    setStatus("데이터베이스 속성은 최대 500개까지 만들 수 있습니다.", "error");
    return;
  }
  const isTitle = property?.type === "title";
  const hiddenInView = Boolean(property && !databasePropertyVisible(block, property));
  const databaseBlocks = (State.pageData?.blocks || []).filter(candidate => DATABASE_BLOCK_TYPES.has(candidate.type));
  const relationProperties = properties.filter(candidate => candidate.type === "relation");
  const selectedRollupRelation = relationProperties.find(candidate => candidate.id === property?.rollup_relation_property) || relationProperties[0];
  const selectedRelationTarget = databaseRelationTarget(selectedRollupRelation || property);
  const rollupTargetProperties = selectedRelationTarget ? databaseProperties(selectedRelationTarget) : [];
  const types = [
    ["text", "텍스트"], ["number", "숫자"], ["select", "선택"], ["multi_select", "다중 선택"],
    ["status", "상태"], ["date", "날짜"], ["formula", "수식"], ["relation", "관계형"],
    ["rollup", "롤업"], ["person", "사람"], ["files", "파일과 미디어"], ["checkbox", "체크박스"],
    ["url", "URL"], ["email", "이메일"], ["phone", "전화번호"], ["created_time", "생성 일시"],
    ["last_edited_time", "최종 편집 일시"],
  ];
  openModal(property ? "속성 편집" : "속성 추가", `
    <div class="database-rule-form">
      <input id="dbPropertyName" value="${escapeAttr(property?.name || "새 속성")}" placeholder="속성 이름">
      <select id="dbPropertyType" ${isTitle ? "disabled" : ""}>
        ${isTitle ? '<option value="title">제목</option>' : types.map(([value, label]) => `<option value="${value}" ${property?.type === value ? "selected" : ""}>${label}</option>`).join("")}
      </select>
      <label>선택 옵션<input id="dbPropertyOptions" value="${escapeAttr((property?.options || []).join(", "))}" placeholder="예: 시작 전, 진행 중, 완료"></label>
      <label>수식<input id="dbPropertyFormula" value="${escapeAttr(property?.expression || "")}" placeholder='예: prop("가격") * prop("수량")'></label>
      <label>관계형 대상 데이터베이스<select id="dbPropertyRelationDatabase"><option value="">워크스페이스 페이지</option>${databaseBlocks.map(candidate => `<option value="${escapeAttr(candidate.id)}" ${property?.relation_database_block_id === candidate.id ? "selected" : ""}>${escapeHtml(candidate.content || blockTypeMeta(candidate.type)[1])}</option>`).join("")}</select></label>
      <label class="database-inline-check"><input id="dbPropertyRelationMultiple" type="checkbox" ${property?.relation_multiple ? "checked" : ""}> 여러 페이지 연결 허용</label>
      <label>롤업 관계형 속성<select id="dbPropertyRollupRelation"><option value="">관계형 속성 선택</option>${relationProperties.map(candidate => `<option value="${escapeAttr(candidate.id)}" ${candidate.id === property?.rollup_relation_property ? "selected" : ""}>${escapeHtml(candidate.name || candidate.id)}</option>`).join("")}</select></label>
      <label>롤업 대상 속성<select id="dbPropertyRollupTarget"><option value="">제목</option>${rollupTargetProperties.map(candidate => `<option value="${escapeAttr(candidate.id)}" ${candidate.id === property?.rollup_target_property ? "selected" : ""}>${escapeHtml(candidate.name || candidate.id)}</option>`).join("")}</select></label>
      <label>롤업 계산<select id="dbPropertyRollupCalculation"><option value="show_original" ${property?.rollup_calculation === "show_original" ? "selected" : ""}>원본 표시</option><option value="count" ${property?.rollup_calculation === "count" ? "selected" : ""}>연결 수</option><option value="count_values" ${property?.rollup_calculation === "count_values" ? "selected" : ""}>값 수</option><option value="count_unique" ${property?.rollup_calculation === "count_unique" ? "selected" : ""}>고유 값 수</option><option value="sum" ${property?.rollup_calculation === "sum" ? "selected" : ""}>합계</option><option value="average" ${property?.rollup_calculation === "average" ? "selected" : ""}>평균</option><option value="min" ${property?.rollup_calculation === "min" ? "selected" : ""}>최솟값</option><option value="max" ${property?.rollup_calculation === "max" ? "selected" : ""}>최댓값</option><option value="earliest" ${property?.rollup_calculation === "earliest" ? "selected" : ""}>가장 이른 날짜</option><option value="latest" ${property?.rollup_calculation === "latest" ? "selected" : ""}>가장 늦은 날짜</option><option value="percent_checked" ${property?.rollup_calculation === "percent_checked" ? "selected" : ""}>체크 비율</option></select></label>
      <label>버튼 이름<input id="dbPropertyButtonLabel" value="${escapeAttr(property?.button_label || property?.name || "실행")}"></label>
      <label>버튼 URL<input id="dbPropertyButtonUrl" type="url" value="${escapeAttr(property?.button_url || "")}" placeholder="https://"></label>
      <label>ID 접두사<input id="dbPropertyPrefix" value="${escapeAttr(property?.prefix || "ID")}" maxlength="12"></label>
      <label class="database-inline-check"><input id="dbPropertyHidden" type="checkbox" ${hiddenInView ? "checked" : ""} ${isTitle ? "disabled" : ""}> 이 보기에서 숨기기</label>
    </div>
  `, [
    ...(property && !isTitle ? [{ label: "복제", action: () => {
      const id = `prop_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
      const copy = EditorCore.deepClone(property);
      copy.id = id;
      copy.name = `${property.name || "속성"} 사본`;
      const propertyIndex = properties.indexOf(property);
      block.props.properties.splice(propertyIndex + 1, 0, copy);
      databaseRows(block).forEach(item => { item[id] = EditorCore.deepClone(item[property.id] ?? ""); });
      databaseViews(block).forEach(view => {
        view.property_visibility = view.property_visibility && typeof view.property_visibility === "object" ? view.property_visibility : {};
        view.property_visibility[id] = view.property_visibility[property.id] !== false;
      });
      block.props.property_visibility = block.props.property_visibility || {};
      block.props.property_visibility[id] = databasePropertyVisible(block, property);
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } }] : []),
    ...(property && !isTitle ? [{ label: "속성 삭제", action: () => {
      block.props.properties = properties.filter(item => item.id !== property.id);
      databaseRows(block).forEach(item => {
        delete item[property.id];
        delete item[`${property.id}_end`];
        delete item[`${property.id}_reminder`];
      });
      block.props.filters = (block.props.filters || []).filter(item => item.property !== property.id);
      block.props.sorts = (block.props.sorts || []).filter(item => item.property !== property.id);
      if (block.props.group_property === property.id) block.props.group_property = "";
      databaseViews(block).forEach(view => {
        if (view.property_visibility && typeof view.property_visibility === "object") delete view.property_visibility[property.id];
        view.filters = (view.filters || []).filter(item => item.property !== property.id);
        view.sorts = (view.sorts || []).filter(item => item.property !== property.id);
        if (view.group_property === property.id) view.group_property = "";
      });
      if (block.props.property_visibility) delete block.props.property_visibility[property.id];
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } }] : []),
    { label: "취소", action: closeModal },
    { label: "저장", primary: true, action: () => {
      const name = $("#dbPropertyName").value.trim() || "새 속성";
      const settings = {
        name,
        options: $("#dbPropertyOptions").value.split(",").map(value => value.trim()).filter(Boolean).slice(0, 200),
        expression: $("#dbPropertyFormula").value.trim().slice(0, 500),
        relation_database_block_id: $("#dbPropertyRelationDatabase").value,
        relation_multiple: $("#dbPropertyRelationMultiple").checked,
        rollup_relation_property: $("#dbPropertyRollupRelation").value,
        rollup_target_property: $("#dbPropertyRollupTarget").value,
        rollup_calculation: $("#dbPropertyRollupCalculation").value || "show_original",
        button_label: $("#dbPropertyButtonLabel").value.trim().slice(0, 80) || "실행",
        button_url: safeExternalUrl($("#dbPropertyButtonUrl").value),
        prefix: $("#dbPropertyPrefix").value.trim().slice(0, 12) || "ID",
      };
      const visibleInView = isTitle || !$("#dbPropertyHidden").checked;
      block.props.property_visibility = block.props.property_visibility && typeof block.props.property_visibility === "object" ? block.props.property_visibility : {};
      if (property) {
        Object.assign(property, settings);
        delete property.hidden;
        block.props.property_visibility[property.id] = visibleInView;
        if (!isTitle) property.type = $("#dbPropertyType").value;
      } else {
        const id = `prop_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
        block.props.properties.push({ id, type: $("#dbPropertyType").value || "text", ...settings });
        databaseViews(block).forEach(view => {
          view.property_visibility = view.property_visibility && typeof view.property_visibility === "object" ? view.property_visibility : {};
          view.property_visibility[id] = view.id === block.props.active_view_id ? visibleInView : true;
        });
        block.props.property_visibility[id] = visibleInView;
      }
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
  ]);
}

function databaseFilterDraftFromDom() {
  return [...document.querySelectorAll("#modalBody .database-filter-rule")].map(row => ({
    property: row.querySelector("[data-filter-property]")?.value || "title",
    operator: row.querySelector("[data-filter-operator]")?.value || "contains",
    value: row.querySelector("[data-filter-value]")?.value || "",
  }));
}

function openDatabaseFilterDialog(blockIndex, draftFilters = null) {
  const block = State.pageData?.blocks?.[blockIndex];
  if (!block || !canEditDatabaseSchema()) return;
  const properties = databaseProperties(block);
  const filters = Array.isArray(draftFilters)
    ? EditorCore.deepClone(draftFilters)
    : EditorCore.deepClone(Array.isArray(block.props?.filters) ? block.props.filters : []);
  if (!filters.length) filters.push({ property: properties[0]?.id || "title", operator: "contains", value: "" });
  openModal("데이터베이스 필터", `
    <div class="database-rules">
      ${filters.map((filter, index) => `<div class="database-rule-form database-filter-rule">
        <select data-filter-property>${properties.map(property => `<option value="${escapeAttr(property.id)}" ${filter?.property === property.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select>
        <select data-filter-operator>
          <option value="contains" ${filter?.operator === "contains" ? "selected" : ""}>포함</option>
          <option value="not_contains" ${filter?.operator === "not_contains" ? "selected" : ""}>포함하지 않음</option>
          <option value="equals" ${filter?.operator === "equals" ? "selected" : ""}>같음</option>
          <option value="not_equals" ${filter?.operator === "not_equals" ? "selected" : ""}>같지 않음</option>
          <option value="greater_than" ${filter?.operator === "greater_than" ? "selected" : ""}>보다 큼</option>
          <option value="less_than" ${filter?.operator === "less_than" ? "selected" : ""}>보다 작음</option>
          <option value="on_or_before" ${filter?.operator === "on_or_before" ? "selected" : ""}>이전 또는 같음</option>
          <option value="on_or_after" ${filter?.operator === "on_or_after" ? "selected" : ""}>이후 또는 같음</option>
          <option value="not_empty" ${filter?.operator === "not_empty" ? "selected" : ""}>비어 있지 않음</option>
          <option value="empty" ${filter?.operator === "empty" ? "selected" : ""}>비어 있음</option>
        </select>
        <input data-filter-value value="${escapeAttr(filter?.value || "")}" placeholder="값">
        <button type="button" data-remove-filter="${index}" title="필터 삭제">×</button>
      </div>`).join("")}
    </div>
  `, [
    { label: "필터 지우기", action: () => {
      block.props.filters = [];
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
    { label: "취소", action: closeModal },
    { label: "＋ 필터 추가", action: () => {
      const next = databaseFilterDraftFromDom();
      next.push({ property: properties[0]?.id || "title", operator: "contains", value: "" });
      openDatabaseFilterDialog(blockIndex, next);
    } },
    { label: "적용", primary: true, action: () => {
      block.props.filters = databaseFilterDraftFromDom();
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
  ]);
  document.querySelectorAll("#modalBody [data-remove-filter]").forEach(button => button.addEventListener("click", () => {
    const next = databaseFilterDraftFromDom();
    next.splice(Number(button.dataset.removeFilter), 1);
    openDatabaseFilterDialog(blockIndex, next);
  }));
}

function databaseSortDraftFromDom() {
  return [...document.querySelectorAll("#modalBody .database-sort-rule")].map(row => ({
    property: row.querySelector("[data-sort-property]")?.value || "title",
    direction: row.querySelector("[data-sort-direction]")?.value === "desc" ? "desc" : "asc",
  }));
}

function databaseGroupableProperties(block) {
  return databaseProperties(block).filter(property => property.type !== "title");
}

function databaseGroupLabel(property, item) {
  const value = item?.[property.id];
  if (property.type === "checkbox") return value ? "체크됨" : "체크 안 됨";
  return databaseDisplayValue(value) || "비어 있음";
}

function groupedDatabaseRows(block, rows = visibleDatabaseRows(block)) {
  const property = databaseGroupableProperties(block).find(
    candidate => candidate.id === block.props?.group_property,
  );
  if (!property) return [{ grouped: false, label: "", rows }];
  const groups = new Map();
  rows.forEach(entry => {
    const label = databaseGroupLabel(property, entry.item);
    if (!groups.has(label)) groups.set(label, []);
    groups.get(label).push(entry);
  });
  return [...groups.entries()].map(([label, groupedRows]) => ({
    grouped: true,
    label,
    rows: groupedRows,
  }));
}

function openDatabaseGroupDialog(blockIndex) {
  const block = State.pageData?.blocks?.[blockIndex];
  if (!block || !canEditDatabaseSchema()) return;
  const properties = databaseGroupableProperties(block);
  openModal("데이터베이스 그룹", `
    <div class="database-rule-form database-group-rule">
      <label>속성<select id="dbGroupProperty"><option value="">그룹 없음</option>${properties.map(property => `<option value="${escapeAttr(property.id)}" ${block.props?.group_property === property.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select></label>
    </div>
  `, [
    { label: "취소", action: closeModal },
    { label: "적용", primary: true, action: () => {
      const selected = String($("#dbGroupProperty")?.value || "");
      block.props.group_property = properties.some(property => property.id === selected) ? selected : "";
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
  ]);
  setTimeout(() => $("#dbGroupProperty")?.focus(), 0);
}

function openDatabaseSortDialog(blockIndex, draftSorts = null) {
  const block = State.pageData?.blocks?.[blockIndex];
  if (!block || !canEditDatabaseSchema()) return;
  const properties = databaseProperties(block);
  const sorts = Array.isArray(draftSorts)
    ? EditorCore.deepClone(draftSorts)
    : EditorCore.deepClone(Array.isArray(block.props?.sorts) ? block.props.sorts : []);
  if (!sorts.length) sorts.push({ property: properties[0]?.id || "title", direction: "asc" });
  openModal("데이터베이스 정렬", `
    <div class="database-rules">
      ${sorts.map((sort, index) => `<div class="database-rule-form database-sort-rule">
        <select data-sort-property>${properties.map(property => `<option value="${escapeAttr(property.id)}" ${sort?.property === property.id ? "selected" : ""}>${escapeHtml(property.name || property.id)}</option>`).join("")}</select>
        <select data-sort-direction><option value="asc" ${sort?.direction !== "desc" ? "selected" : ""}>오름차순</option><option value="desc" ${sort?.direction === "desc" ? "selected" : ""}>내림차순</option></select>
        <button type="button" data-remove-sort="${index}" title="정렬 삭제">×</button>
      </div>`).join("")}
    </div>
  `, [
    { label: "정렬 지우기", action: () => {
      block.props.sorts = [];
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
    { label: "취소", action: closeModal },
    { label: "＋ 정렬 추가", action: () => {
      const next = databaseSortDraftFromDom();
      next.push({ property: properties[0]?.id || "title", direction: "asc" });
      openDatabaseSortDialog(blockIndex, next);
    } },
    { label: "적용", primary: true, action: () => {
      block.props.sorts = databaseSortDraftFromDom();
      closeModal();
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    } },
  ]);
  document.querySelectorAll("#modalBody [data-remove-sort]").forEach(button => button.addEventListener("click", () => {
    const next = databaseSortDraftFromDom();
    next.splice(Number(button.dataset.removeSort), 1);
    openDatabaseSortDialog(blockIndex, next);
  }));
}

function bindBoardCardDrag(root, block, canEdit) {
  if (!canEdit || block.props?.view !== "board") return;
  let draggedRow = -1;
  root.querySelectorAll(".board-card[data-table-row]").forEach(card => {
    card.addEventListener("dragstart", event => {
      draggedRow = Number(card.dataset.tableRow);
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("application/x-notion-database-row", String(draggedRow));
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => {
      draggedRow = -1;
      root.querySelectorAll(".board-card.dragging, .board-column.drag-over").forEach(item => item.classList.remove("dragging", "drag-over"));
    });
  });
  root.querySelectorAll(".board-column").forEach(column => {
    column.addEventListener("dragover", event => {
      if (draggedRow < 0) return;
      event.preventDefault();
      column.classList.add("drag-over");
    });
    column.addEventListener("dragleave", event => {
      if (!column.contains(event.relatedTarget)) column.classList.remove("drag-over");
    });
    column.addEventListener("drop", event => {
      if (draggedRow < 0) return;
      event.preventDefault();
      const item = databaseRows(block)[draggedRow];
      if (!item) return;
      const property = column.dataset.boardProperty || "status";
      const propertyConfig = databaseProperties(block).find(candidate => candidate.id === property);
      item[property] = propertyConfig?.type === "checkbox"
        ? column.dataset.boardStatus === "체크됨"
        : (column.dataset.boardStatus === "상태 없음" ? "" : column.dataset.boardStatus);
      markDirty();
      captureEditorHistory(true);
      renderBlocks();
    });
  });
}

function askAboutMediaBlock(block) {
  switchSidebarMode("agent");
  const name = block.props?.name || block.content || "첨부파일";
  $("#sidebarAiQuestion").value = `이 첨부파일 "${name}"이 현재 페이지에서 어떤 의미인지 설명해줘.`;
  $("#sidebarAiQuestion").focus();
}

function editMediaComment(index) {
  if (!activePageCanEdit()) return;
  const block = State.pageData.blocks[index];
  if (!block) return;
  const current = block.props?.comment || "";
  openModal(
    "댓글",
    `<textarea id="mediaCommentInput" class="ai-question" placeholder="댓글을 입력하세요">${escapeHtml(current)}</textarea>`,
    [
      { label: "취소", action: closeModal },
      {
        label: "저장",
        primary: true,
        action: () => {
          block.props = block.props || {};
          block.props.comment = $("#mediaCommentInput").value.trim();
          closeModal();
          markDirty();
          renderBlocks();
        },
      },
    ],
  );
  setTimeout(() => $("#mediaCommentInput")?.focus(), 0);
}

function openLinkPopover(index, anchor) {
  if (!activePageCanEdit()) return;
  const block = State.pageData.blocks[index];
  if (!block) return;
  State.linkBlockIndex = index;
  const pop = $("#linkPopover");
  const current = normalizeUrl(block.props?.link_url || "");
  pop.innerHTML = `
    <div class="link-popover-row">
      <input id="linkUrlInput" type="url" placeholder="https://example.com" value="${escapeHtml(current)}">
      <button type="button" id="linkSaveBtn">편집</button>
      <button type="button" id="linkCopyBtn">⧉</button>
    </div>
    <div class="link-popover-row" style="margin-top:6px; grid-template-columns: 1fr auto;">
      <button type="button" id="linkRemoveBtn">링크 제거</button>
      <button type="button" id="linkCloseBtn">닫기</button>
    </div>
  `;
  const rect = anchor.getBoundingClientRect();
  pop.style.left = `${Math.max(8, Math.min(window.innerWidth - 356, rect.left))}px`;
  pop.style.top = `${Math.min(window.innerHeight - 108, rect.bottom + 6)}px`;
  pop.hidden = false;
  $("#linkSaveBtn").addEventListener("click", saveLinkFromPopover);
  $("#linkUrlInput").addEventListener("keydown", e => {
    if (e.key === "Enter") saveLinkFromPopover();
  });
  $("#linkCopyBtn").addEventListener("click", () => copyText(normalizeUrl($("#linkUrlInput").value), "링크 복사됨"));
  $("#linkRemoveBtn").addEventListener("click", () => {
    block.props = block.props || {};
    delete block.props.link_url;
    closeLinkPopover();
    markDirty();
    renderBlocks();
  });
  $("#linkCloseBtn").addEventListener("click", closeLinkPopover);
  $("#linkUrlInput").focus();
}

function saveLinkFromPopover() {
  const index = State.linkBlockIndex;
  const block = State.pageData?.blocks?.[index];
  if (!block) return;
  block.props = block.props || {};
  block.props.link_url = normalizeUrl($("#linkUrlInput").value);
  closeLinkPopover();
  markDirty();
  renderBlocks();
}

function closeLinkPopover() {
  $("#linkPopover").hidden = true;
  State.linkBlockIndex = -1;
}

function resizeTextarea(el) {
  el.style.height = "auto";
  el.style.height = Math.min(480, Math.max(32, el.scrollHeight)) + "px";
}

function newBlock(type = "paragraph") {
  return EditorCore.createBlock(type);
}

function createTabsBlock(index, replace = false) {
  if (!activePageCanEdit()) return;
  const source = State.pageData.blocks[index];
  const layout = source?.props?.layout_group ? {
    layout_group: source.props.layout_group,
    layout_column: Number(source.props.layout_column || 0),
    layout_columns: Number(source.props.layout_columns || 2),
  } : {};
  const controller = EditorCore.createBlock("tabs", { props: layout });
  const tabs = normalizeTabsBlock(controller);
  const sourceContent = replace && source && !String(source.content || "").startsWith("/") ? String(source.content || "") : "";
  const members = tabs.map((tab, memberIndex) => EditorCore.createBlock("paragraph", {
    content: memberIndex === 0 ? sourceContent : "",
    props: { ...layout, tab_group: controller.id, tab_id: tab.id },
  }));
  const insertAt = replace ? index : index + 1;
  State.pageData.blocks.splice(insertAt, replace ? 1 : 0, controller, ...members);
  State.selectedBlockIds.clear();
  markDirty();
  captureEditorHistory(true);
  closeBlockMenu();
  renderBlocks();
  focusBlock(insertAt + 1, sourceContent.length);
}

function insertBlockAfter(index, type = "paragraph", focus = false) {
  if (!activePageCanEdit()) return;
  const source = State.pageData.blocks[index];
  const block = newBlock(type);
  if (source?.props?.layout_group) {
    block.props = {
      ...(block.props || {}),
      layout_group: source.props.layout_group,
      layout_column: Number(source.props.layout_column || 0),
      layout_columns: Number(source.props.layout_columns || 2),
    };
  }
  if (source?.props?.tab_group) {
    block.props = {
      ...(block.props || {}),
      tab_group: source.props.tab_group,
      tab_id: source.props.tab_id,
    };
  }
  State.pageData.blocks.splice(index + 1, 0, block);
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  if (focus) {
    focusBlock(index + 1, 0);
  }
}

async function insertChildPageAfter(index, replace = false) {
  if (!activePageCanEdit()) return;
  try {
    setStatus("하위 페이지 생성 중...");
    if (!(await flushPageDraft(State.activePageId))) {
      setStatus("저장되지 않은 상위 페이지 편집본이 있어 하위 페이지를 만들지 않았습니다.", "error");
      return;
    }
    const data = await api("/api/notion/pages", {
      method: "POST",
      body: { title: "새 페이지", parent_id: State.activePageId },
    });
    const page = data.page || {};
    const source = State.pageData.blocks[index];
    const block = {
      id: "tmp_" + Math.random().toString(16).slice(2),
      type: "child_page",
      content: page.title || "새 페이지",
      props: { page_id: page.id, title: page.title || "새 페이지" },
    };
    if (source?.props?.layout_group) {
      Object.assign(block.props, {
        layout_group: source.props.layout_group,
        layout_column: Number(source.props.layout_column || 0),
        layout_columns: Number(source.props.layout_columns || 2),
      });
    }
    if (source?.props?.tab_group) {
      Object.assign(block.props, {
        tab_group: source.props.tab_group,
        tab_id: source.props.tab_id,
      });
    }
    if (replace) State.pageData.blocks.splice(index, 1, block);
    else State.pageData.blocks.splice(index + 1, 0, block);
    markDirty();
    renderBlocks();
    await savePage();
    if (State.dirty) return;
    await loadPages();
    setStatus("하위 페이지 생성됨", "ok");
  } catch (e) {
    setStatus("하위 페이지 생성 실패: " + e.message, "error");
  }
}

async function createDatabaseItem(blockIndex, kind, date = "") {
  const block = State.pageData?.blocks?.[blockIndex];
  if (!block || !activePageCanEdit()) return;
  try {
    setStatus("페이지 항목 생성 중...");
    if (!(await flushPageDraft(State.activePageId))) {
      setStatus("저장되지 않은 데이터베이스 편집본이 있어 항목을 만들지 않았습니다.", "error");
      return;
    }
    const title = "새 페이지";
    const data = await api("/api/notion/pages", {
      method: "POST",
      body: { title, parent_id: State.activePageId },
    });
    const page = data.page || {};
    block.props = block.props || {};
    block.props.rows = Array.isArray(block.props.rows) ? block.props.rows : (Array.isArray(block.props.events) ? block.props.events : []);
    delete block.props.events;
    const titleField = databasePropertyId(block, "title", "title");
    const now = new Date().toISOString();
    const item = {
      [titleField]: title,
      page_id: page.id,
      _created_time: now,
      _last_edited_time: now,
      _created_by: State.user?.id || "",
      _last_edited_by: State.user?.id || "",
    };
    databaseProperties(block).forEach(property => {
      if (property.type === "title") return;
      item[property.id] = property.type === "checkbox" ? false : "";
    });
    const dateField = kind === "calendar"
      ? (block.props.calendar_date_property || databasePropertyId(block, "date", "date"))
      : kind === "timeline"
        ? (block.props.timeline_date_property || databasePropertyId(block, "date", "date"))
        : databasePropertyId(block, "date", "date");
    if (kind === "calendar" || date) item[dateField] = date || new Date().toISOString().slice(0, 10);
    block.props.rows.push(item);
    markDirty();
    captureEditorHistory(true);
    renderBlocks();
    await savePage();
    if (State.dirty) return;
    await loadPages();
    openSubPageModal(page.id, { title, date });
    setStatus("페이지 항목 생성됨", "ok");
  } catch (e) {
    setStatus("페이지 항목 생성 실패: " + e.message, "error");
  }
}

function openSubPageModal(pageId) {
  const block = (State.pageData?.blocks || []).find(candidate => DATABASE_BLOCK_TYPES.has(candidate.type)
    && databaseRows(candidate).some(item => item?.page_id === pageId));
  const rowIndex = block ? databaseRows(block).findIndex(item => item?.page_id === pageId) : -1;
  const context = block && rowIndex >= 0 ? {
    sourcePageId: State.activePageId,
    blockId: block.id,
    rowIndex,
  } : null;
  openSidePeek(pageId, context);
}

function changeBlockType(index, type) {
  if (NON_ADOPTED_BLOCK_TYPES.has(type)) return;
  if (DATABASE_BLOCK_TYPES.has(type) && !canEditDatabaseSchema()) return;
  if (type === "tabs") {
    createTabsBlock(index, true);
    return;
  }
  const indices = selectedBlockIndices(index);
  if (!indices.length) return;
  if (selectionContainsDatabase(indices)) return;
  if (!databaseStructureMutationAllowed(indices)) return;
  indices.forEach(itemIndex => {
    const block = State.pageData.blocks[itemIndex];
    if (!block) return;
    const defaults = EditorCore.createBlock(type).props;
    const indent = Number(block.props?.indent || 0);
    const databaseTypes = DATABASE_BLOCK_TYPES;
    const previousType = block.type;
    const existingProps = block.props || {};
    const layout = existingProps.layout_group ? {
      layout_group: existingProps.layout_group,
      layout_column: Number(existingProps.layout_column || 0),
      layout_columns: Number(existingProps.layout_columns || 2),
    } : {};
    block.type = type;
    block.props = databaseTypes.has(type) && databaseTypes.has(previousType)
      ? { ...existingProps, view: type }
      : { ...defaults, ...(indent ? { indent } : {}), ...layout };
  });
  markDirty();
  captureEditorHistory(true);
  closeBlockMenu();
  renderBlocks();
  focusBlock(index, 0);
}

function duplicateTabsBlockGroup(index) {
  const source = State.pageData.blocks[index];
  if (!source || source.type !== "tabs") return false;
  const sourceTabs = normalizeTabsBlock(source);
  const tabMap = Object.fromEntries(sourceTabs.map(tab => [tab.id, newTabId()]));
  const controller = EditorCore.createBlock("tabs", {
    content: source.content,
    props: EditorCore.deepClone(source.props || {}),
  });
  controller.props.tabs = sourceTabs.map(tab => ({ id: tabMap[tab.id], name: tab.name }));
  controller.props.active_tab_id = tabMap[source.props.active_tab_id] || controller.props.tabs[0].id;
  const members = State.pageData.blocks
    .map((block, memberIndex) => ({ block, memberIndex }))
    .filter(({ block }) => block.props?.tab_group === source.id);
  const copies = members.map(({ block }) => EditorCore.createBlock(block.type, {
    content: block.content,
    props: {
      ...EditorCore.deepClone(block.props || {}),
      tab_group: controller.id,
      tab_id: tabMap[block.props?.tab_id] || controller.props.tabs[0].id,
    },
  }));
  const insertAt = Math.max(index, ...members.map(item => item.memberIndex)) + 1;
  State.pageData.blocks.splice(insertAt, 0, controller, ...copies);
  return true;
}

function handleBlockAction(action, index) {
  const blocks = State.pageData.blocks || [];
  const indices = selectedBlockIndices(index);
  if (action === "remove" && selectionContainsDatabase(indices)) return;
  if (action === "duplicate" && selectionContainsDatabase(indices)) return;
  if (action === "remove") {
    const expanded = new Set(indices);
    indices.forEach(itemIndex => {
      const controllerId = blocks[itemIndex]?.type === "tabs" ? blocks[itemIndex]?.id : "";
      if (!controllerId) return;
      blocks.forEach((candidate, candidateIndex) => {
        if (candidate.props?.tab_group === controllerId) expanded.add(candidateIndex);
      });
    });
    State.pageData.blocks = EditorCore.removeBlocks(blocks, [...expanded]);
  } else if (action === "duplicate") {
    if (!(indices.length === 1 && duplicateTabsBlockGroup(indices[0]))) {
      State.pageData.blocks = EditorCore.duplicateBlocks(blocks, indices);
    }
  } else if (action === "sync_copy") {
    const source = blocks[index];
    if (!source || !EditorCore.isTextBlock(source.type)) return;
    source.type = "synced_block";
    source.props = source.props || {};
    source.props.sync_id = source.props.sync_id || `sync_${source.id || Date.now().toString(36)}`;
    const copy = EditorCore.createBlock("synced_block", {
      content: source.content,
      props: EditorCore.deepClone(source.props),
    });
    State.pageData.blocks.splice(index + 1, 0, copy);
  } else if (action === "remove_column_layout") {
    indices.forEach(itemIndex => {
      const props = blocks[itemIndex]?.props;
      if (!props) return;
      delete props.layout_group;
      delete props.layout_column;
      delete props.layout_columns;
    });
  } else if (action === "up" && index > 0) {
    State.pageData.blocks = EditorCore.moveBlock(blocks, index, index - 1);
  } else if (action === "down" && index < blocks.length - 1) {
    State.pageData.blocks = EditorCore.moveBlock(blocks, index, index + 1);
  } else if (action === "copy_link") {
    copyText(`${pageUrl(State.activePageId)}#block-${blocks[index]?.id || index}`, "블록 링크 복사됨");
    closeBlockMenu();
    return;
  } else if (action === "suggest_edit") {
    closeBlockMenu();
    startBlockAiAction(index, "improve");
    return;
  } else if (action === "add_link") {
    const anchor = document.querySelector(`#block-${cssEscape(blocks[index]?.id || "")} [data-action="menu"]`) || $("#blockMenu");
    openLinkPopover(index, anchor);
    closeBlockMenu();
    return;
  } else if (action === "comment") {
    State.commentBlockId = blocks[index]?.id || "";
    State.commentSelection = null;
    openDrawer("comments");
    closeBlockMenu();
    return;
  }
  if (!State.pageData.blocks.length) State.pageData.blocks.push(newBlock("paragraph"));
  markDirty();
  captureEditorHistory(true);
  clearBlockSelection();
  closeBlockMenu();
  renderBlocks();
}

function renderBlockFlyout(kind, index, anchor) {
  const menu = $("#blockMenu");
  const flyout = $("#blockFlyout");
  if (!flyout) return;
  const rect = anchor.getBoundingClientRect();
  flyout.style.left = `${Math.min(window.innerWidth - 236, rect.right + 6)}px`;
  flyout.style.top = `${Math.min(window.innerHeight - 430, rect.top - 8)}px`;
  if (kind === "convert") {
    const types = [
      "paragraph", "heading1", "heading2", "heading3", "toggle_heading1", "toggle_heading2",
      "toggle_heading3", "child_page", "bullet", "numbered", "todo", "toggle", "callout", "code",
      "quote", "equation", "tabs", "layout_columns_2", "layout_columns_3", "table", "board", "list", "gallery", "calendar", "timeline",
    ].filter(type => !DATABASE_BLOCK_TYPES.has(type) || canEditDatabaseSchema());
    flyout.innerHTML = `
      ${types.map(type => {
        const [icon, label] = blockTypeMeta(type);
        return `<button type="button" class="menu-item" data-flyout-type="${type}"><span>${icon}</span><span>${label}</span><small>${State.pageData.blocks[index]?.type === type ? "✓" : ""}</small></button>`;
      }).join("")}
    `;
    flyout.querySelectorAll("[data-flyout-type]").forEach(btn => btn.addEventListener("click", () => {
      const type = btn.dataset.flyoutType;
      if (type === "child_page") insertChildPageAfter(index, true);
      else if (type === "layout_columns_2" || type === "layout_columns_3") createColumnLayout(index, type.endsWith("3") ? 3 : 2, "convert");
      else changeBlockType(index, type);
    }));
  } else if (kind === "color") {
    const names = [
      ["default", "기본"],
      ["gray", "회색"],
      ["brown", "갈색"],
      ["orange", "주황색"],
      ["yellow", "노란색"],
      ["green", "초록색"],
      ["blue", "파란색"],
      ["purple", "보라색"],
      ["pink", "분홍색"],
      ["red", "빨간색"],
    ];
    flyout.innerHTML = `
      <div class="menu-label">텍스트 색상</div>
      ${names.map(([value, label]) => `<button type="button" class="menu-item" data-text-color="${value}"><span class="color-swatch text-${value}">A</span><span>${label} 텍스트</span><small></small></button>`).join("")}
      <div class="menu-label">배경 색상</div>
      ${names.map(([value, label]) => `<button type="button" class="menu-item" data-bg-color="${value}"><span class="color-swatch bg-${value}"></span><span>${label} 배경</span><small></small></button>`).join("")}
    `;
    flyout.querySelectorAll("[data-text-color]").forEach(btn => btn.addEventListener("click", () => setBlockColor(index, "text_color", btn.dataset.textColor)));
    flyout.querySelectorAll("[data-bg-color]").forEach(btn => btn.addEventListener("click", () => setBlockColor(index, "background_color", btn.dataset.bgColor)));
  } else if (kind === "move") {
    flyout.innerHTML = `
      <button type="button" class="menu-item" data-move-action="up"><span>↑</span><span>위로 옮기기</span><small></small></button>
      <button type="button" class="menu-item" data-move-action="down"><span>↓</span><span>아래로 옮기기</span><small></small></button>
    `;
    flyout.querySelectorAll("[data-move-action]").forEach(btn => btn.addEventListener("click", () => handleBlockAction(btn.dataset.moveAction, index)));
  }
  flyout.hidden = false;
  menu.addEventListener("mouseleave", () => { if (flyout) flyout.hidden = true; }, { once: true });
}

function setBlockColor(index, key, value) {
  const indices = selectedBlockIndices(index);
  indices.forEach(itemIndex => {
    const block = State.pageData?.blocks?.[itemIndex];
    if (!block) return;
    block.props = block.props || {};
    if (!value || value === "default") delete block.props[key];
    else block.props[key] = value;
  });
  State.lastBlockColor = { key, value };
  markDirty();
  captureEditorHistory(true);
  closeBlockMenu();
  renderBlocks();
}

function applyLastBlockColor(index) {
  const last = State.lastBlockColor;
  if (!last?.key || !last?.value) return false;
  const indices = selectedBlockIndices(index);
  indices.forEach(itemIndex => {
    const block = State.pageData?.blocks?.[itemIndex];
    if (!block) return;
    block.props = block.props || {};
    if (last.value === "default") delete block.props[last.key];
    else block.props[last.key] = last.value;
  });
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  return true;
}

function visibleBlockMenuButtons() {
  return [...document.querySelectorAll("#blockMenu [data-insert-type], #blockMenu [data-action], #blockMenu [data-ai-block]")]
    .filter(button => !button.hidden && button.offsetParent !== null && !button.disabled);
}

function refreshBlockMenuSelection() {
  const buttons = visibleBlockMenuButtons();
  const search = $("#blockMenu")?.querySelector(".menu-search");
  if (!buttons.length) {
    search?.removeAttribute("aria-activedescendant");
    return;
  }
  State.menuSelectedIndex = Math.max(0, Math.min(buttons.length - 1, State.menuSelectedIndex));
  buttons.forEach((button, index) => {
    const active = index === State.menuSelectedIndex;
    button.classList.toggle("menu-active", active);
    button.setAttribute("aria-selected", active ? "true" : "false");
  });
  const active = buttons[State.menuSelectedIndex];
  if (active) {
    search?.setAttribute("aria-activedescendant", active.id);
    active.scrollIntoView({ block: "nearest" });
  }
}

function moveBlockMenuSelection(delta) {
  const buttons = visibleBlockMenuButtons();
  if (!buttons.length) return;
  State.menuSelectedIndex = (State.menuSelectedIndex + Number(delta || 0) + buttons.length) % buttons.length;
  refreshBlockMenuSelection();
}

function activateBlockMenuSelection() {
  visibleBlockMenuButtons()[State.menuSelectedIndex]?.click();
}

function filterBlockMenu(query) {
  const value = String(query || "").trim().toLowerCase();
  const menu = $("#blockMenu");
  const buttons = [...(menu?.querySelectorAll("[data-insert-type], [data-action], [data-ai-block]") || [])];
  buttons.forEach(button => {
    button.hidden = !window.NotionSlashMenu.matches(button.dataset.searchText || button.textContent.toLowerCase(), value);
    const row = button.closest(".slash-command-row");
    if (row) row.hidden = button.hidden;
  });
  menu?.querySelectorAll("[data-command-group]").forEach(group => {
    group.hidden = ![...group.querySelectorAll("[data-insert-type]")].some(button => !button.hidden);
  });
  const empty = menu?.querySelector("[data-menu-empty]");
  if (empty) empty.hidden = buttons.some(button => !button.hidden);
  State.menuSelectedIndex = 0;
  refreshBlockMenuSelection();
}

function createColumnLayout(index, count, mode = "insert") {
  if (!activePageCanEdit()) return;
  const columnCount = Math.max(2, Math.min(5, Number(count) || 2));
  const blocks = State.pageData?.blocks || [];
  const groupId = `layout_${window.crypto?.randomUUID?.().replace(/-/g, "") || `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`}`;
  const layoutProps = columnIndex => ({
    layout_group: groupId,
    layout_column: columnIndex,
    layout_columns: columnCount,
  });
  let focusIndex = index + 1;
  if (mode === "slash" || mode === "convert") {
    const current = blocks[index];
    if (!current) return;
    if (mode === "slash") {
      current.type = "paragraph";
      current.content = "";
      current.props = {};
    }
    current.props = { ...(current.props || {}), ...layoutProps(0) };
    const additions = Array.from({ length: columnCount - 1 }, (_, offset) => EditorCore.createBlock("paragraph", {
      props: layoutProps(offset + 1),
    }));
    blocks.splice(index + 1, 0, ...additions);
    focusIndex = index;
  } else {
    const additions = Array.from({ length: columnCount }, (_, columnIndex) => EditorCore.createBlock("paragraph", {
      props: layoutProps(columnIndex),
    }));
    blocks.splice(index + 1, 0, ...additions);
  }
  markDirty();
  captureEditorHistory(true);
  closeBlockMenu();
  renderBlocks();
  focusBlock(focusIndex, 0);
}

function chooseBlockType(type, index, mode) {
  closeBlockMenu();
  if (!activePageCanEdit()) return;
  if (type === "html_embed") {
    const source = State.pageData.blocks[index];
    const layout = Object.fromEntries(Object.entries(source?.props || {}).filter(([key]) => ["layout_group", "layout_column", "layout_columns", "tab_group", "tab_id", "indent"].includes(key)));
    const block = EditorCore.createBlock("embed", { props: { ...layout, embed_mode: "html" } });
    if (mode === "slash") { block.id = source.id; State.pageData.blocks[index] = block; }
    else State.pageData.blocks.splice(index + 1, 0, block);
    markDirty(); captureEditorHistory(true); renderBlocks();
    document.querySelector(`#blocks .block-row[data-index="${mode === "slash" ? index : index + 1}"] [data-html-editor] textarea`)?.focus();
    return;
  }
  if (NON_ADOPTED_BLOCK_TYPES.has(type)) return;
  if (DATABASE_BLOCK_TYPES.has(type) && !canEditDatabaseSchema()) return;
  const sourcePageId = State.activePageId;
  const sourcePageSequence = State.pageLoadSequence;
  if (type === "equation") setTimeout(() => {
    if (State.activePageId !== sourcePageId || State.pageLoadSequence !== sourcePageSequence) return;
    const target = mode === "slash" || mode === "convert" ? index : index + 1;
    document.querySelector(`#blocks .block-row[data-index="${target}"] [data-equation-trigger]`)?.click();
  }, 0);
  if (/^layout_columns_[2-5]$/.test(type)) {
    createColumnLayout(index, Number(type.slice(-1)), mode);
    return;
  }
  if (type === "tabs") {
    createTabsBlock(index, mode === "slash");
    return;
  }
  const uploadTypes = new Set(["image", "file", "video", "audio", "pdf"]);
  if (uploadTypes.has(type)) {
    State.uploadInsertIndex = mode === "slash" ? index : index + 1;
    State.uploadReplaceIndex = mode === "slash" ? index : null;
    State.uploadMode = type;
    const accepts = { image: "image/*", video: "video/*", audio: "audio/*", pdf: "application/pdf", file: "" };
    $("#fileInput").accept = accepts[type] || "";
    if (mode === "slash") {
      State.pageData.blocks[index].content = "";
      delete State.pageData.blocks[index].props?.rich_text_html;
    }
    $("#fileInput").click();
    return;
  }
  if (type === "child_page") {
    insertChildPageAfter(index, mode === "slash");
    return;
  }
  if (mode === "slash") {
    const block = State.pageData.blocks[index];
    const layout = block.props?.layout_group ? {
      layout_group: block.props.layout_group,
      layout_column: Number(block.props.layout_column || 0),
      layout_columns: Number(block.props.layout_columns || 2),
    } : {};
    block.content = "";
    block.props = layout;
    changeBlockType(index, type);
    if (type === "divider") insertBlockAfter(index, "paragraph", true);
    return;
  }
  insertBlockAfter(index, type, true);
}

function slashPreferences() {
  let storage;
  try { storage = window.localStorage; } catch (_) { /* Storage may be disabled. */ }
  return window.NotionSlashMenu.preferences(storage, State.user?.id, State.navigation.workspaceId || State.pageData?.page?.workspace_id);
}

function openBlockMenu(index, anchor, mode = "insert", query = "") {
  State.menuIndex = index;
  State.menuMode = mode;
  State.menuQuery = query || "";
  State.menuSelectedIndex = 0;
  State.slashSourceIndex = mode === "slash" ? index : -1;
  const menu = $("#blockMenu");
  const rect = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - 320, rect.left))}px`;
  menu.style.top = `${Math.max(8, Math.min(window.innerHeight - 440, rect.bottom + 4))}px`;
  const block = State.pageData.blocks[index] || {};
  const prefs = slashPreferences();
  const definitions = window.NotionSlashMenu.personalized(window.NotionSlashMenu.definitions(EditorCore.filterBlockDefinitions(""))
    .filter(item => !NON_ADOPTED_BLOCK_TYPES.has(item.type))
    .filter(item => !DATABASE_BLOCK_TYPES.has(item.type) || canEditDatabaseSchema()), prefs.value);
  const commandGroups = [...new Set(definitions.map(item => item.group))];
  const typeItems = commandGroups.map((group, groupIndex) => `<div data-command-group role="group" aria-label="${escapeAttr(group)}"><div class="menu-label">${escapeHtml(group)}</div>${definitions.filter(item => item.group === group).map(item => (
    `<div class="slash-command-row"><button id="block-menu-option-${groupIndex}-${escapeAttr(item.type)}" type="button" class="menu-item" role="option" aria-selected="false" data-insert-type="${escapeAttr(item.type)}" data-search-text="${escapeAttr(window.NotionSlashMenu.searchText(item))}"><span class="slash-command-icon">${window.NotionSlashMenu.icon(item.type)}</span><span>${escapeHtml(item.label)}${item.detail ? `<span class="slash-command-detail"> · ${escapeHtml(item.detail)}</span>` : ""}</span><small>${escapeHtml(item.shortcut)}</small></button><button type="button" class="slash-pin" data-slash-pin="${escapeAttr(item.type)}" aria-pressed="${prefs.value.pins.includes(item.type)}" aria-label="${escapeAttr(item.label)} 추천 고정" title="추천 고정/해제">${prefs.value.pins.includes(item.type) ? "★" : "☆"}</button></div>`
  )).join("")}</div>`).join("");
  const databaseBlock = DATABASE_BLOCK_TYPES.has(block.type);
  const actionItems = `
    <div class="menu-label">${escapeHtml(blockTypeMeta(block.type || "paragraph")[1])}</div>
    ${databaseBlock ? "" : '<button type="button" class="menu-item" data-submenu="convert"><span>↔</span><span>전환</span><small>›</small></button>'}
    <button type="button" class="menu-item" data-submenu="color"><span>▰</span><span>색</span><small>›</small></button>
    <button type="button" class="menu-item" data-action="copy_link"><span>⌁</span><span>블록 링크 복사</span><small>Alt+Shift+L</small></button>
    <button type="button" class="menu-item" data-action="add_link"><span>⌁</span><span>하이퍼링크 추가</span><small>URL</small></button>
    <button type="button" class="menu-item" data-action="comment"><span>☰</span><span>댓글</span><small>Ctrl+Shift+M</small></button>
    ${databaseBlock ? "" : '<button type="button" class="menu-item" data-action="duplicate"><span>⧉</span><span>복제</span><small>Ctrl+D</small></button>'}
    ${EditorCore.isTextBlock(block.type) ? '<button type="button" class="menu-item" data-action="sync_copy"><span>↻</span><span>동기화 사본 만들기</span><small></small></button>' : ""}
    <button type="button" class="menu-item" data-submenu="move"><span>↱</span><span>옮기기</span><small>›</small></button>
    ${block.props?.layout_group ? '<button type="button" class="menu-item" data-action="remove_column_layout"><span>↔</span><span>열에서 꺼내기</span><small></small></button>' : ""}
    ${databaseBlock ? "" : '<button type="button" class="menu-item danger-item" data-action="remove"><span>⌫</span><span>삭제</span><small>Del</small></button>'}
    <div class="menu-foot">${escapeHtml(State.user?.display_name || State.user?.id || "admin")} 최종 편집<br>${escapeHtml(formatTime(block.updated_at || State.pageData.page.updated_at))}</div>
  `;
  menu.innerHTML = `
    <input class="menu-search" role="combobox" aria-label="${mode === "actions" ? "블록 작업 검색" : "블록 유형 검색"}" aria-autocomplete="list" aria-controls="blockMenuOptions" aria-expanded="true" placeholder="${mode === "actions" ? "작업을 검색하세요" : "블록 유형을 검색하세요"}" value="${escapeAttr(query)}">
    ${mode === "actions" ? "" : `<div class="slash-preferences"><label>추천 순서 <select data-slash-sort aria-label="추천 순서"><option value="frequent" ${prefs.value.mode === "frequent" ? "selected" : ""}>최근 30일 사용 빈도</option><option value="recent" ${prefs.value.mode === "recent" ? "selected" : ""}>최근 사용</option></select></label><small data-slash-status role="status">☆ 고정 · 최대 8개 · 이 브라우저에 저장</small></div>`}
    <div id="blockMenuOptions" role="listbox" aria-label="${mode === "actions" ? "블록 작업" : "블록 유형"}">
      ${mode === "actions" ? actionItems : `${typeItems}<div class="menu-empty" data-menu-empty hidden>일치하는 블록이 없습니다.</div>`}
    </div>
    <button type="button" class="menu-item" data-close><span>×</span><span>메뉴 닫기</span><small>esc</small></button>
    <div id="blockFlyout" class="menu-flyout" hidden></div>
  `;
  menu.hidden = false;
  menu.querySelectorAll("#blockMenuOptions [data-action], #blockMenuOptions [data-ai-block]").forEach((button, itemIndex) => {
    button.id = `block-menu-option-${itemIndex}`;
    button.setAttribute("role", "option");
    button.setAttribute("aria-selected", "false");
  });
  menu.querySelectorAll("[data-insert-type]").forEach(btn => btn.addEventListener("click", () => {
    const type = btn.dataset.insertType;
    // Count explicit command use (including opening a file picker), not hover/search/pin.
    if (activePageCanEdit()) slashPreferences().record(type);
    chooseBlockType(type, index, mode);
  }));
  menu.querySelector("[data-slash-sort]")?.addEventListener("click", event => event.stopPropagation());
  menu.querySelector("[data-slash-sort]")?.addEventListener("change", event => {
    prefs.value.mode = event.target.value === "recent" ? "recent" : "frequent";
    if (!prefs.save()) { menu.querySelector("[data-slash-status]").textContent = "브라우저 저장소를 사용할 수 없습니다."; return; }
    openBlockMenu(index, anchor, mode, menu.querySelector(".menu-search").value);
    menu.querySelector("[data-slash-sort]")?.focus();
  });
  menu.querySelectorAll("[data-slash-pin]").forEach(button => button.addEventListener("click", event => {
    event.stopPropagation(); // Rebuilding detaches the target; do not trigger the document outside-click closer.
    const type = button.dataset.slashPin;
    if (!prefs.toggle(type)) { menu.querySelector("[data-slash-status]").textContent = "최대 8개까지 고정할 수 있습니다. 먼저 하나를 해제하세요."; return; }
    if (!prefs.save()) { menu.querySelector("[data-slash-status]").textContent = "브라우저 저장소를 사용할 수 없습니다."; return; }
    openBlockMenu(index, anchor, mode, menu.querySelector(".menu-search").value);
    [...menu.querySelectorAll("[data-slash-pin]")].find(node => node.dataset.slashPin === type && !node.closest(".slash-command-row").hidden)?.focus();
  }));
  menu.querySelectorAll("[data-action]").forEach(btn => btn.addEventListener("click", () => handleBlockAction(btn.dataset.action, index)));
  menu.querySelectorAll("[data-submenu]").forEach(btn => {
    btn.addEventListener("mouseenter", () => renderBlockFlyout(btn.dataset.submenu, index, btn));
    btn.addEventListener("click", () => renderBlockFlyout(btn.dataset.submenu, index, btn));
  });
  menu.querySelector("[data-close]")?.addEventListener("click", closeBlockMenu);
  const search = menu.querySelector(".menu-search");
  search?.addEventListener("input", event => filterBlockMenu(event.target.value));
  search?.addEventListener("keydown", event => {
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp" || event.key === "Enter") {
      event.preventDefault();
      if (event.key === "ArrowDown") moveBlockMenuSelection(1);
      else if (event.key === "ArrowUp") moveBlockMenuSelection(-1);
      else activateBlockMenuSelection();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeBlockMenu(true);
    }
  });
  search?.focus();
  filterBlockMenu(query);
}

function closeBlockMenu(restoreSlashFocus = false) {
  const sourceIndex = State.slashSourceIndex;
  const menu = $("#blockMenu");
  menu.hidden = true;
  menu.querySelector(".menu-search")?.setAttribute("aria-expanded", "false");
  $("#blockFlyout")?.setAttribute("hidden", "");
  State.slashSourceIndex = -1;
  State.menuQuery = "";
  if (restoreSlashFocus && sourceIndex >= 0) {
    const block = State.pageData?.blocks?.[sourceIndex];
    focusBlock(sourceIndex, String(block?.content || "").length);
  }
}

function conflictStatusText(target) {
  const locationLabel = target === "sidePeek" ? "사이드 피크 페이지" : "페이지";
  return `저장 충돌: 서버의 ${locationLabel}가 변경되었습니다. 현재 편집본은 이 창에 그대로 보존됩니다.`;
}

function showSaveConflict(target, pageId, error) {
  const holder = target === "sidePeek" ? State.sidePeek : State;
  const currentRevision = Number(error?.data?.current_revision);
  holder.saveConflict = {
    pageId,
    currentRevision: Number.isSafeInteger(currentRevision) && currentRevision >= 1 ? currentRevision : null,
  };
  clearTimeout(holder.saveTimer);
  holder.saveTimer = null;
  holder.saveQueued = false;
  const statusText = conflictStatusText(target);
  setStatus(statusText, "error");
  openModal(
    "저장 충돌",
    `<p>${escapeHtml(statusText)}</p><p>서버 버전을 새 탭에서 비교하거나, 현재 편집본을 명시적으로 버리고 최신 버전을 다시 불러올 수 있습니다.</p>`,
    [
      { label: "편집본 유지", action: closeModal },
      {
        label: "서버 버전 새 탭에서 보기",
        action: () => window.open(pageUrl(pageId), "_blank", "noopener"),
      },
      {
        label: "편집본 버리고 다시 불러오기",
        action: () => discardConflictDraftAndReload(target),
      },
    ],
  );
}

async function discardConflictDraftAndReload(target) {
  const holder = target === "sidePeek" ? State.sidePeek : State;
  const pageId = holder.saveConflict?.pageId || (target === "sidePeek" ? State.sidePeek.pageId : State.activePageId);
  if (!pageId) return;
  if (!window.confirm("현재 편집본을 버리고 서버의 최신 버전을 다시 불러올까요? 이 작업은 되돌릴 수 없습니다.")) return;
  try {
    setStatus("서버의 최신 버전을 불러오는 중...");
    const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}`);
    const revision = serverRevisionFrom(data);
    if (atomicSaveSupported(data) && revision === null) throw new Error("서버 응답에 문서 버전이 없습니다.");
    const normalized = normalizedPageData(data);
    if (target === "sidePeek") {
      if (State.sidePeek.pageId !== pageId) return;
      clearTimeout(State.sidePeek.saveTimer);
      State.sidePeek.data = normalized;
      State.sidePeek.serverRevision = revision;
      State.sidePeek.atomicSaveEnabled = atomicSaveSupported(data);
      State.sidePeek.dirty = false;
      State.sidePeek.saveRetry = null;
      State.sidePeek.saveConflict = null;
      State.sidePeek.saveQueued = false;
      State.sidePeek.editRevision += 1;
      renderSidePeek();
      if (State.activePageId === pageId && !State.dirty) {
        State.pageData = normalizedPageData(data);
        State.serverRevision = revision;
        State.atomicSaveEnabled = atomicSaveSupported(data);
        State.saveRetry = null;
        State.saveConflict = null;
        renderEditor();
        resetEditorHistory();
      }
    } else {
      if (State.activePageId !== pageId) return;
      clearTimeout(State.saveTimer);
      State.pageData = normalized;
      State.serverRevision = revision;
      State.atomicSaveEnabled = atomicSaveSupported(data);
      State.dirty = false;
      State.saveRetry = null;
      State.saveConflict = null;
      State.saveQueued = false;
      State.editRevision += 1;
      clearMainEditorDraft(pageId);
      renderEditor();
      resetEditorHistory();
      if (State.sidePeek.pageId === pageId && !State.sidePeek.dirty) {
        State.sidePeek.data = normalizedPageData(data);
        State.sidePeek.serverRevision = revision;
        State.sidePeek.atomicSaveEnabled = atomicSaveSupported(data);
        renderSidePeek();
      }
    }
    closeModal();
    await Promise.all([loadPages(), loadInboxItems(false)]);
    setStatus("서버의 최신 버전을 불러왔습니다.", "ok");
  } catch (error) {
    setStatus("최신 버전 불러오기 실패: " + error.message, "error");
  }
}

async function flushPageDraft(pageId) {
  if (State.activePageId === pageId && State.dirty) {
    await savePage();
    if (State.dirty) return false;
  }
  if (State.sidePeek.pageId === pageId && State.sidePeek.dirty) {
    await saveSidePeek();
    if (State.sidePeek.dirty) return false;
  }
  return true;
}

async function flushAllOpenDrafts() {
  if (State.dirty) {
    await savePage();
    if (State.dirty) return false;
  }
  if (State.sidePeek.dirty) {
    await saveSidePeek();
    if (State.sidePeek.dirty) return false;
  }
  return true;
}

function openPageIds() {
  return [...new Set([State.activePageId, State.sidePeek.pageId].filter(Boolean))];
}

async function refreshOpenedPageAfterMutation(pageId, payload = null) {
  const id = String(pageId || "");
  if (!id) return { mainApplied: false, sidePeekApplied: false, failed: false };
  const mainSnapshot = State.activePageId === id
    ? {
        editRevision: State.editRevision,
        clean: !State.dirty,
        serverRevision: State.serverRevision,
        atomicSaveEnabled: State.atomicSaveEnabled,
      }
    : null;
  const sidePeekSnapshot = State.sidePeek.pageId === id
    ? {
        editRevision: State.sidePeek.editRevision,
        clean: !State.sidePeek.dirty,
        serverRevision: State.sidePeek.serverRevision,
        atomicSaveEnabled: State.sidePeek.atomicSaveEnabled,
      }
    : null;
  let data = payload;
  try {
    if (!data?.page || !Array.isArray(data?.blocks)) {
      data = await api(`/api/notion/pages/${encodeURIComponent(id)}`);
    }
  } catch (error) {
    return { mainApplied: false, sidePeekApplied: false, failed: true, error };
  }
  let mainApplied = false;
  let sidePeekApplied = false;
  const mainCanApply = Boolean(
    mainSnapshot?.clean
    && State.activePageId === id
    && !State.dirty
    && State.editRevision === mainSnapshot.editRevision
  );
  const sidePeekCanApply = Boolean(
    sidePeekSnapshot?.clean
    && State.sidePeek.pageId === id
    && !State.sidePeek.dirty
    && State.sidePeek.editRevision === sidePeekSnapshot.editRevision
  );
  if (mainCanApply) {
    State.pageData = normalizedPageData(EditorCore.deepClone(data), State.pageData);
    applySaveMetadata(State, data);
    renderEditor();
    resetEditorHistory();
    mainApplied = true;
  }
  if (sidePeekCanApply) {
    const focusSnapshot = sidePeekFocusSnapshot();
    State.sidePeek.data = normalizedPageData(EditorCore.deepClone(data), State.sidePeek.data);
    applySaveMetadata(State.sidePeek, data);
    renderSidePeek();
    restoreSidePeekFocus(focusSnapshot);
    sidePeekApplied = true;
  }
  const currentRevision = serverRevisionFrom(data);
  const revisionChanged = snapshot => Boolean(
    snapshot?.atomicSaveEnabled
    && (currentRevision === null || currentRevision !== snapshot.serverRevision)
  );
  const mainConflicted = Boolean(
    mainSnapshot && !mainCanApply && State.activePageId === id && revisionChanged(mainSnapshot)
  );
  const sidePeekConflicted = Boolean(
    sidePeekSnapshot && !sidePeekCanApply && State.sidePeek.pageId === id && revisionChanged(sidePeekSnapshot)
  );
  if (mainConflicted) {
    if (currentRevision !== null) State.serverRevision = currentRevision;
    showSaveConflict("main", id, { data: { current_revision: currentRevision } });
  }
  if (sidePeekConflicted) {
    if (currentRevision !== null) State.sidePeek.serverRevision = currentRevision;
    if (mainConflicted) {
      State.sidePeek.saveConflict = { pageId: id, currentRevision };
      clearTimeout(State.sidePeek.saveTimer);
      State.sidePeek.saveTimer = null;
      State.sidePeek.saveQueued = false;
    } else {
      showSaveConflict("sidePeek", id, { data: { current_revision: currentRevision } });
    }
  }
  return { mainApplied, sidePeekApplied, mainConflicted, sidePeekConflicted, failed: false };
}

async function refreshOpenPagesAfterMutation({ affectedPageIds = null, payloadByPageId = {} } = {}) {
  const affected = affectedPageIds ? new Set([...affectedPageIds].filter(Boolean).map(String)) : null;
  const results = {};
  for (const pageId of openPageIds()) {
    if (affected && !affected.has(pageId)) continue;
    results[pageId] = await refreshOpenedPageAfterMutation(pageId, payloadByPageId[pageId] || null);
  }
  return results;
}

async function refreshOtherOpenPagesAfterMutation(pageId) {
  const affected = openPageIds().filter(id => id !== pageId);
  if (!affected.length) return {};
  return refreshOpenPagesAfterMutation({ affectedPageIds: affected });
}

async function restorePageSafely(pageId) {
  if (!(await flushAllOpenDrafts())) {
    throw new Error("저장되지 않은 편집본이 있어 페이지를 복원하지 않았습니다.");
  }
  let data;
  if (State.navigation.enabled && State.navigation.workspaceId) {
    const intent = navigationPageLifecycleIntent("restore", pageId);
    try {
      data = await api(
        `/api/notion/workspaces/${encodeURIComponent(intent.workspaceId)}/trash/${encodeURIComponent(pageId)}/restore`,
        {
          method: "POST",
          body: {
            base_version: intent.baseVersion,
            operation_id: intent.operationId,
          },
        },
      );
      State.navigation.pendingPageLifecycle = null;
      applyNavigationWorkspaceResult(data.workspace);
    } catch (error) {
      if (
        error?.status === 409
        && Number.isInteger(error?.data?.current_version)
        && error.data.current_version >= 1
      ) intent.baseVersion = error.data.current_version;
      throw error;
    }
  } else {
    data = await api(`/api/notion/trash/${encodeURIComponent(pageId)}/restore`, {
      method: "POST",
      body: {},
    });
  }
  await refreshOpenPagesAfterMutation({ payloadByPageId: { [pageId]: data } });
  return data;
}

async function movePageSafely(pageId, body) {
  if (!(await flushAllOpenDrafts())) {
    throw new Error("저장되지 않은 편집본이 있어 페이지를 이동하지 않았습니다.");
  }
  const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/move`, {
    method: "POST",
    body,
  });
  await refreshOpenPagesAfterMutation({ payloadByPageId: { [pageId]: data } });
  return data;
}

function markDirty() {
  (State.pageData?.blocks || []).forEach(block => {
    if (DATABASE_BLOCK_TYPES.has(block.type)) syncActiveDatabaseView(block);
  });
  if (State.pageData?.page?.collaborative_editing === true) {
    State.dirty = false;
    State.editRevision += 1;
    EditorHost.notifyLocalMutation("document", { block_id: State.activeBlockId || "" });
    scheduleCollaborativeDatabaseSync();
    const live = State.liveCollaboration;
    setStatus(
      live.connected ? "동기화 중…" : "암호화된 오프라인 변경 저장 중…",
      live.connected ? "" : "error",
    );
    return;
  }
  State.dirty = true;
  State.editRevision += 1;
  persistMainEditorDraft();
  setStatus("저장 대기 중");
  captureEditorHistory();
  scheduleSave();
  EditorHost.notifyLocalMutation("document", { block_id: State.activeBlockId || "" });
}

function scheduleSave() {
  if (State.pageData?.page?.collaborative_editing === true) return;
  clearTimeout(State.saveTimer);
  State.saveTimer = null;
  persistMainEditorDraft();
  if (State.saveConflict) {
    setStatus(conflictStatusText("main"), "error");
    return;
  }
  State.saveTimer = setTimeout(() => savePage(), 900);
}

async function savePage() {
  if (State.pageData?.page?.collaborative_editing === true) {
    await State.liveCollaboration.persistenceChain.catch(() => {});
    await flushCollaborativeDatabaseSync();
    const live = State.liveCollaboration;
    if (live.recoveryOnly || live.offlinePolicyBlocked) {
      setStatus("로컬 편집 복구가 필요합니다.", "error");
    } else if (live.unsyncedCount > 0) {
      setStatus(
        live.connected ? "동기화 중…" : `오프라인 변경 ${live.unsyncedCount}개를 암호화해 보관 중입니다.`,
        live.connected ? "" : "error",
      );
    } else {
      setStatus("저장됨", "ok");
    }
    return;
  }
  if (!State.pageData || !State.dirty) return;
  clearTimeout(State.saveTimer);
  State.saveTimer = null;
  if (State.saveConflict) {
    showSaveConflict("main", State.activePageId, {
      data: { current_revision: State.saveConflict.currentRevision },
    });
    return;
  }
  if (pageIsLocked(State.pageData.page)) {
    State.dirty = false;
    setStatus("페이지가 잠겨 있습니다.", "error");
    return;
  }
  if (State.saveInFlight) {
    State.saveQueued = true;
    return State.saveInFlight;
  }
  const pageId = State.pageData.page.id;
  const title = $("#pageTitle").value;
  const blocks = EditorCore.deepClone(State.pageData.blocks);
  const revision = State.editRevision;
  const useAtomicSave = State.atomicSaveEnabled;
  let request;
  if (useAtomicSave) {
    try {
      request = atomicSaveRequest(State, State.pageData, title, blocks, State.serverRevision);
    } catch (error) {
      State.dirty = true;
      setStatus("저장 실패: " + error.message, "error");
      return;
    }
  }
  persistMainEditorDraft();
  State.saveQueued = false;
  State.saveInFlight = (async () => {
    try {
      setStatus("자동 저장 중...");
      let saved;
      if (useAtomicSave) {
        saved = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/save`, {
          method: "POST",
          body: request,
        });
      } else {
        await api(`/api/notion/pages/${encodeURIComponent(pageId)}`, {
          method: "PATCH",
          body: { title, _defer_version: true },
        });
        saved = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/blocks`, {
          method: "PUT",
          body: { blocks },
        });
      }
      const savedRevision = serverRevisionFrom(saved);
      if (useAtomicSave && savedRevision === null) throw new Error("저장 응답에 문서 버전이 없습니다.");
      if (savedRevision !== null) State.serverRevision = savedRevision;
      if (useAtomicSave && State.saveRetry?.operationId === request.operation_id) State.saveRetry = null;
      State.saveConflict = null;
      if (State.activePageId === pageId && State.editRevision === revision) {
        State.pageData = normalizedSavedPageDataPreservingBlockBindings(
          saved,
          State.pageData,
        );
        State.dirty = false;
        clearMainEditorDraft(pageId);
        renderBreadcrumb(State.pageData.page);
        $("#pageMeta").textContent = `소유자 ${State.pageData.page.owner_user_id} · ${State.pageData.page.scope} · ${State.pageData.page.role} · ${formatTime(State.pageData.page.updated_at)} · 자동 저장`;
        setStatus("저장됨", "ok");
      } else if (State.activePageId === pageId) {
        State.pageData.page.updated_at = saved.page?.updated_at || State.pageData.page.updated_at;
        State.dirty = true;
        State.saveQueued = true;
        persistMainEditorDraft();
        setStatus("새 변경사항 저장 대기 중");
      }
      if (State.sidePeek.pageId === pageId) {
        if (!State.sidePeek.dirty) {
          State.sidePeek.data = normalizedPageData(EditorCore.deepClone(saved), State.sidePeek.data);
          applySaveMetadata(State.sidePeek, saved);
          renderSidePeek();
        } else {
          if (savedRevision !== null) State.sidePeek.serverRevision = savedRevision;
          showSaveConflict("sidePeek", pageId, { data: { current_revision: savedRevision } });
        }
      }
      await refreshOtherOpenPagesAfterMutation(pageId);
      await Promise.all([loadPages(), loadInboxItems(false)]);
    } catch (e) {
      State.dirty = true;
      persistMainEditorDraft();
      if (useAtomicSave && e.status === 409) showSaveConflict("main", pageId, e);
      else setStatus("저장 실패: " + e.message, "error");
    } finally {
      State.saveInFlight = null;
      if (State.dirty && State.saveQueued && !State.saveConflict) scheduleSave();
    }
  })();
  return State.saveInFlight;
}

async function uploadCollaborativeFile(file, pageId = State.activePageId) {
  const baseUrl = collaborativeFileBasePath(pageId);
  const page = State.pageData?.page;
  if (!baseUrl || page?.id !== pageId) throw new Error("협업 파일 업로드 범위를 확인할 수 없습니다.");
  for (const field of ["document_sequence", "workspace_epoch", "document_epoch"]) {
    if (!Number.isSafeInteger(Number(page[field])) || Number(page[field]) < 1) {
      throw new Error("협업 파일 업로드 버전을 확인할 수 없습니다. 페이지를 새로고침해 주세요.");
    }
  }
  return ResumableFileUpload.upload({
    file,
    baseUrl,
    subjectId: State.user?.id || "",
    workspaceId: State.navigation.workspaceId,
    pageId,
    documentSequence: Number(page.document_sequence),
    workspaceEpoch: Number(page.workspace_epoch),
    documentEpoch: Number(page.document_epoch),
    csrfToken: State.csrfToken,
    fetch: authenticatedFetch,
    onProgress: progress => {
      const percent = progress.total
        ? Math.min(100, Math.floor(progress.loaded * 100 / progress.total))
        : 100;
      setStatus(progress.phase === "hashing" ? `파일 확인 중 ${percent}%` : `업로드 중 ${percent}%`);
    },
  });
}

async function loadCollaborativeFiles() {
  const baseUrl = collaborativeFileBasePath();
  if (!baseUrl || !State.pageData) return false;
  const pageId = State.activePageId;
  try {
    const result = await api(baseUrl);
    if (pageId !== State.activePageId || !State.pageData) return false;
    State.pageData.files = Array.isArray(result.files) ? result.files : [];
    renderFiles();
    return true;
  } catch (error) {
    setStatus("첨부파일 목록을 불러오지 못했습니다: " + error.message, "error");
    return false;
  }
}

async function deleteCollaborativeFile(fileId) {
  const baseUrl = collaborativeFileBasePath();
  const page = State.pageData?.page;
  if (!baseUrl || !page?.can_edit || !fileId) return;
  try {
    await api(`${baseUrl}/${encodeURIComponent(fileId)}`, {
      method: "DELETE",
      body: {
        expected_document_sequence: Number(page.document_sequence),
        expected_workspace_epoch: Number(page.workspace_epoch),
        expected_document_epoch: Number(page.document_epoch),
        operation_id: newOperationId(),
      },
    });
    await refreshOpenedPageAfterMutation(page.id);
    setStatus("첨부파일을 삭제했습니다.", "ok");
  } catch (error) {
    setStatus("첨부파일 삭제 실패: " + error.message, "error");
  }
}

function renderFiles() {
  const root = $("#fileList");
  root.innerHTML = "";
  const files = State.pageData?.files || [];
  if (!files.length) {
    root.innerHTML = '<div class="mini-row"><span>첨부파일 없음</span></div>';
    return;
  }
  files.forEach(file => {
    const row = document.createElement("div");
    row.className = "mini-row";
    const url = file.url || notionFileUrl(file.id || file.file_id);
    const canDelete = State.navigation.collaborativeFiles
      && String(file.id || file.file_id || "").startsWith("file_")
      && activePageCanEdit();
    row.innerHTML = `<span><a href="${escapeAttr(url)}" target="_blank" rel="noopener">${escapeHtml(file.original_name || file.name || "첨부파일")}</a></span><small>${formatBytes(file.size ?? file.size_bytes)}</small>${canDelete ? `<button type="button" data-collaborative-file-delete="${escapeAttr(file.id || file.file_id)}" aria-label="첨부파일 삭제">×</button>` : ""}`;
    row.querySelector("[data-collaborative-file-delete]")?.addEventListener("click", event => {
      void deleteCollaborativeFile(event.currentTarget.dataset.collaborativeFileDelete || "");
    });
    root.appendChild(row);
  });
}

async function uploadSelectedFile(file) {
  if (!file || !State.pageData) return;
  try {
    setStatus("업로드 중...");
    const pageId = State.activePageId;
    if (!(await flushPageDraft(pageId))) {
      setStatus("저장되지 않은 편집본이 있어 파일을 업로드하지 않았습니다.", "error");
      return;
    }
    const insertAt = State.uploadInsertIndex;
    const collaborativeUpload = Boolean(collaborativeFileBasePath(pageId));
    let data;
    if (collaborativeUpload) {
      data = await uploadCollaborativeFile(file, pageId);
    } else {
      const form = new FormData();
      form.append("file", file);
      form.append("insert_block", insertAt == null ? "1" : "0");
      form.append("block_type", State.uploadMode);
      const res = await authenticatedFetch(`/api/notion/pages/${encodeURIComponent(pageId)}/files`, { method: "POST", body: form });
      data = await res.json().catch(() => ({}));
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        msg = data.error || msg;
        throw new Error(msg);
      }
    }
    const refreshResult = await refreshOpenedPageAfterMutation(pageId);
    if (refreshResult.failed) throw refreshResult.error;
    if (refreshResult.mainConflicted || refreshResult.sidePeekConflicted) return;
    if (insertAt != null || collaborativeUpload) {
      const item = data.file || {};
      const block = {
        id: "tmp_" + Math.random().toString(16).slice(2),
        type: State.uploadMode || (file.type.startsWith("image/") ? "image" : "file"),
        content: item.original_name || file.name,
        props: {
          file_id: item.id || "",
          name: item.original_name || file.name,
          mime_type: item.mime_type || file.type || "",
        },
      };
      const resolvedInsertAt = insertAt == null ? State.pageData.blocks.length : insertAt;
      const layoutSource = State.pageData.blocks[State.uploadReplaceIndex ?? Math.max(0, resolvedInsertAt - 1)];
      if (layoutSource?.props?.layout_group) {
        Object.assign(block.props, {
          layout_group: layoutSource.props.layout_group,
          layout_column: Number(layoutSource.props.layout_column || 0),
          layout_columns: Number(layoutSource.props.layout_columns || 2),
        });
      }
      if (State.uploadReplaceIndex != null) State.pageData.blocks.splice(State.uploadReplaceIndex, 1, block);
      else State.pageData.blocks.splice(resolvedInsertAt, 0, block);
      markDirty();
      captureEditorHistory(true);
      await savePage();
      if (State.dirty) return;
    }
    await refreshOpenedPageAfterMutation(pageId);
    setStatus("업로드됨", "ok");
  } catch (e) {
    setStatus("업로드 실패: " + e.message, "error");
  } finally {
    State.uploadInsertIndex = null;
    State.uploadReplaceIndex = null;
  }
}

async function importDataFile(file, parentId = "") {
  if (!file) return;
  try {
    const isCsv = /\.csv$/i.test(file.name || "") || String(file.type || "").toLowerCase().includes("csv");
    setStatus(isCsv ? "CSV 데이터베이스 가져오는 중..." : "Markdown 가져오는 중...");
    const content = await file.text();
    if (new TextEncoder().encode(content).length > 2_000_000) throw new Error("가져올 파일은 2MB 이하여야 합니다.");
    const title = file.name.replace(/\.(md|markdown|txt|csv)$/i, "") || "가져온 페이지";
    const workspaceBase = collaborativeSearchWorkspacePath();
    const data = workspaceBase
      ? await api(`${workspaceBase}/imports/${isCsv ? "csv" : "markdown"}`, {
          method: "POST",
          body: {
            title,
            [isCsv ? "csv" : "markdown"]: content,
            parent_id: parentId || "",
            expected_workspace_epoch: await canonicalWorkspaceEpoch(),
            operation_id: newOperationId(),
          },
        })
      : await api(isCsv ? "/api/notion/import_csv" : "/api/notion/import_markdown", {
          method: "POST",
          body: { title, [isCsv ? "csv" : "markdown"]: content, parent_id: parentId || "" },
        });
    await loadPages();
    await openPage(data.document_id || data.page?.id);
    setStatus(isCsv ? "CSV를 데이터베이스로 가져왔습니다." : "Markdown 페이지를 가져왔습니다.", "ok");
  } catch (error) {
    setStatus("가져오기 실패: " + error.message, "error");
  }
}

async function loadUsers() {
  try {
    const data = await api("/api/notion/users");
    State.users = data.items || [];
  } catch {
    State.users = [];
  }
  renderUsers();
  renderWorkspacePopover();
  renderSearchCreators();
}

function renderUsers() {
  renderSharePrincipalOptions();
  const workspaceSelect = $("#workspaceMemberUser");
  if (workspaceSelect) {
    const existing = new Set((State.workspaceAccess?.members || []).map(item => item.subject_id));
    const candidates = State.users.filter(item => item.id !== State.user?.id && !existing.has(item.id));
    workspaceSelect.innerHTML = candidates.length
      ? '<option value="">이름이나 이메일 검색</option>' + candidates.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.display_name || item.id)}</option>`).join("")
      : '<option value="">초대할 사용자 없음</option>';
    workspaceSelect.value = "";
    syncWorkspaceInviteControls();
  }
}

function renderSharePrincipalOptions() {
  const select = $("#shareUser");
  if (!select) return;
  const kind = $("#sharePrincipalType")?.value || "subject";
  let items = [];
  if (kind === "group") {
    items = (State.workspaceAccess?.groups || []).map(item => ({ id: item.id, label: item.name }));
  } else if (kind === "teamspace") {
    items = (State.workspaceAccess?.teamspaces || []).map(item => ({ id: item.id, label: item.name }));
  } else {
    items = State.users
      .filter(item => item.id !== State.user?.id)
      .map(item => ({ id: item.id, label: item.display_name || item.id }));
  }
  select.innerHTML = items.length
    ? items.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label)}</option>`).join("")
    : `<option value="">${kind === "subject" ? "사용자 없음" : "대상 없음"}</option>`;
}

function collaborativePageBasePath() {
  if (!State.navigation.collaborativeSharing || !State.navigation.workspaceId || !State.activePageId) return "";
  return `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/pages/${encodeURIComponent(State.activePageId)}`;
}

function collaborativeDatabaseActive() {
  return Boolean(
    collaborativeEditingActive()
    && State.pageData?.page?.collaborative_databases === true
    && collaborativePageBasePath(),
  );
}

function collaborativeDatabaseSyncPending() {
  const sync = State.databaseSync;
  return Boolean(
    collaborativeDatabaseActive()
    && (sync.timer || sync.inFlight || sync.queued),
  );
}

function canEditDatabaseSchema() {
  if (!collaborativeDatabaseActive()) return activePageCanEdit();
  return ["edit", "full_access"].includes(String(State.pageData?.page?.iam_role || ""));
}

function isDatabaseSchemaCommand(commandKind) {
  return commandKind === "database.update"
    || commandKind.startsWith("database.property.")
    || commandKind.startsWith("database.view.")
    || commandKind.startsWith("database.template.")
    || commandKind === "database.create";
}

function currentDatabaseDocumentSequence() {
  return Math.max(
    1,
    Number(State.pageData?.page?.document_sequence || 0),
    Number(State.liveCollaboration?.durableCursor || 0),
  );
}

async function sendCollaborativeDatabaseCommand(action, operationId) {
  const base = collaborativePageBasePath();
  if (!base) throw new Error("협업 데이터베이스 경로를 사용할 수 없습니다.");
  if (isDatabaseSchemaCommand(action.command_kind) && !canEditDatabaseSchema()) {
    throw new Error("데이터베이스 구조를 변경할 권한이 없습니다.");
  }
  let expectedSequence = currentDatabaseDocumentSequence();
  for (let attempt = 0; attempt < 5; attempt += 1) {
    try {
      const result = await api(`${base}/databases/commands`, {
        method: "POST",
        body: {
          database_id: action.database_id,
          command_kind: action.command_kind,
          target_id: action.target_id,
          payload: action.payload || {},
          expected_document_sequence: expectedSequence,
          expected_workspace_epoch: Number(State.pageData?.page?.workspace_epoch || 0),
          expected_document_epoch: Number(State.pageData?.page?.document_epoch || 0),
          expected_target_version: action.expected_target_version,
          operation_id: operationId,
        },
      });
      const committed = Number(result.document_sequence || 0);
      if (committed < 1) throw new Error("데이터베이스 명령 응답에 문서 시퀀스가 없습니다.");
      State.pageData.page.document_sequence = committed;
      State.liveCollaboration.durableCursor = Math.max(
        Number(State.liveCollaboration.durableCursor || 0),
        committed,
      );
      return result;
    } catch (error) {
      const current = Number(error?.data?.current_document_sequence || 0);
      if (error?.status === 409 && current > 0 && attempt < 4) {
        expectedSequence = current;
        State.pageData.page.document_sequence = current;
        State.liveCollaboration.durableCursor = Math.max(
          Number(State.liveCollaboration.durableCursor || 0),
          current,
        );
        continue;
      }
      throw error;
    }
  }
  throw new Error("데이터베이스 명령을 커밋하지 못했습니다.");
}

async function reloadCanonicalDatabases(databaseIds, { applyToBlocks = true } = {}) {
  const base = collaborativePageBasePath();
  if (!base || !State.pageData) return [];
  const pageId = State.activePageId;
  const requested = [...new Set(
    Array.from(databaseIds || [], value => String(value || "")).filter(Boolean),
  )];
  const loaded = [];
  for (const databaseId of requested) {
    const result = await api(`${base}/databases/${encodeURIComponent(databaseId)}`);
    if (result.database) loaded.push(result.database);
  }
  if (State.activePageId !== pageId || !State.pageData) return [];
  const merged = new Map((State.pageData.databases || []).map(item => [String(item.id || ""), item]));
  loaded.forEach(item => merged.set(String(item.id || ""), item));
  State.pageData.databases = [...merged.values()];
  if (applyToBlocks) {
    State.pageData.blocks = DatabaseBridge.applyCanonicalDatabases(
      State.pageData.blocks || [],
      State.pageData.databases,
    ).map(EditorCore.normalizeBlock);
  }
  return loaded;
}

async function flushCollaborativeDatabaseSync() {
  const sync = State.databaseSync;
  clearTimeout(sync.timer);
  sync.timer = null;
  if (!collaborativeDatabaseActive() || !State.pageData) return false;
  if (sync.inFlight) {
    sync.queued = true;
    return sync.inFlight;
  }
  const pageId = State.activePageId;
  const generation = sync.generation;
  const actions = DatabaseBridge.diffPage(
    State.pageData.blocks || [],
    State.pageData.databases || [],
  );
  if (!actions.length) return false;
  const task = (async () => {
    const touched = new Set();
    try {
      for (let index = 0; index < actions.length; index += 1) {
        if (State.activePageId !== pageId) return false;
        const action = actions[index];
        touched.add(action.database_id);
        await sendCollaborativeDatabaseCommand(
          action,
          `dbui-${Date.now().toString(36)}-${index}-${crypto.randomUUID().replaceAll("-", "")}`,
        );
      }
      const unchanged = sync.generation === generation && State.activePageId === pageId;
      await reloadCanonicalDatabases(touched, { applyToBlocks: unchanged });
      if (unchanged) renderBlocks();
      return true;
    } catch (error) {
      try { await reloadCanonicalDatabases(touched, { applyToBlocks: false }); } catch {}
      setStatus(`데이터베이스 동기화 실패: ${error.message}`, "error");
      sync.queued = true;
      return false;
    }
  })();
  sync.inFlight = task;
  try {
    return await task;
  } finally {
    sync.inFlight = null;
    if (sync.queued && State.activePageId === pageId) {
      sync.queued = false;
      scheduleCollaborativeDatabaseSync();
    }
  }
}

function scheduleCollaborativeDatabaseSync() {
  if (!collaborativeDatabaseActive()) return;
  const sync = State.databaseSync;
  sync.generation += 1;
  sync.queued = Boolean(sync.inFlight);
  clearTimeout(sync.timer);
  sync.timer = setTimeout(() => { void flushCollaborativeDatabaseSync(); }, 450);
}

function collaborativeCommentBasePath() {
  const base = collaborativePageBasePath();
  return base ? `${base}/comments` : "";
}

function collaborativeEditingActive() {
  return Boolean(
    State.navigation.collaborativeEditing
    && State.pageData?.page?.collaborative_editing === true
    && State.navigation.workspaceId
    && State.activePageId,
  );
}

function collaborativeEditingScope(page = State.pageData?.page) {
  const realm = String(location.origin || "");
  const subjectId = String(State.user?.id || "");
  const workspaceId = String(State.navigation.workspaceId || page?.workspace_id || "");
  const pageId = String(page?.id || State.activePageId || "");
  if (!realm || !subjectId || !workspaceId || !pageId) return null;
  return {
    realm,
    subject_id: subjectId,
    workspace_id: workspaceId,
    page_id: pageId,
  };
}

function collaborationOperationId(prefix = "edit") {
  return `e1p2-${prefix}-${crypto.randomUUID().replaceAll("-", "")}`;
}

function clearCollaborativeRecoveryBanner() {
  document.querySelector("#collaborationRecoveryBanner")?.remove();
}

function downloadCollaborativeRecovery(entries, reason) {
  const projection = CollaborativeEditorBridge.projection();
  const payload = {
    contract: "code_assist.notion.local_collaboration_recovery.v1",
    exported_at: new Date().toISOString(),
    reason: String(reason || "manual-recovery"),
    scope: collaborativeEditingScope(),
    projection,
    pending_operations: (entries || []).map(entry => ({
      operation_id: entry.operation_id,
      client_sequence: entry.client_sequence,
      acknowledgement_sequence: entry.acknowledgement_sequence,
      workspace_epoch: entry.workspace_epoch,
      document_epoch: entry.document_epoch,
      created_at: entry.created_at,
      payload_base64: entry.payload_base64,
      structure_commands: entry.structure_commands || [],
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `notion-local-recovery-${String(State.activePageId || "page").replace(/[^A-Za-z0-9._-]/g, "_")}.json`;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

function showCollaborativeRecoveryBanner(reason, entries = null) {
  clearCollaborativeRecoveryBanner();
  const pageView = $("#pageView");
  if (!pageView) return;
  const pending = entries || [...State.liveCollaboration.pendingEnvelopes.values()];
  const banner = document.createElement("div");
  banner.id = "collaborationRecoveryBanner";
  banner.className = "collaboration-recovery-banner";
  banner.setAttribute("role", "alert");
  const text = document.createElement("span");
  text.textContent = reason || "이 브라우저의 로컬 편집은 자동 동기화할 수 없습니다.";
  banner.appendChild(text);
  if (pending.length) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "로컬 편집 내보내기";
    button.addEventListener("click", () => downloadCollaborativeRecovery(pending, reason));
    banner.appendChild(button);
  }
  pageView.prepend(banner);
}

function enterCollaborativeRecoveryOnly(reason, entries = null) {
  const live = State.liveCollaboration;
  live.recoveryOnly = true;
  live.recoveryReason = String(reason || "로컬 편집 복구가 필요합니다.");
  live.sendQueueActive = false;
  const title = $("#pageTitle");
  if (title) title.disabled = true;
  if (State.pageData) renderBlocks();
  showCollaborativeRecoveryBanner(live.recoveryReason, entries);
  setStatus(live.recoveryReason, "error");
}

function participantColor(subjectId) {
  const colors = ["#2f80ed", "#e03e3e", "#0f7b6c", "#9b51e0", "#d9730d", "#0084c7"];
  let hash = 0;
  for (const character of String(subjectId || "")) hash = ((hash * 31) + character.charCodeAt(0)) >>> 0;
  return colors[hash % colors.length];
}

function editorTextPoint(editor, offset) {
  if (!editor || typeof document.createTreeWalker !== "function") return null;
  const walker = document.createTreeWalker(editor, 4);
  let remaining = Math.max(0, Number(offset) || 0);
  let node = walker.nextNode();
  let last = null;
  while (node) {
    last = node;
    const length = String(node.nodeValue || "").length;
    if (remaining <= length) return { node, offset: remaining };
    remaining -= length;
    node = walker.nextNode();
  }
  return last ? { node: last, offset: String(last.nodeValue || "").length } : null;
}

function renderRemoteCursors() {
  document.querySelectorAll(".remote-collaboration-caret,.remote-collaboration-selection")
    .forEach(node => node.remove());
  if (!State.pageData || !State.liveCollaboration.crdtReady) return;
  const participants = [...State.liveCollaboration.participants.values()]
    .filter(item => item.subject_id && item.subject_id !== State.user?.id && item.cursor_block_id);
  participants.forEach(item => {
    const row = [...document.querySelectorAll(".block-row[data-block-id]")]
      .find(candidate => candidate.dataset.blockId === String(item.cursor_block_id));
    const editor = row?.querySelector(".block-editor");
    if (!row || !editor || row.hidden) return;
    const user = State.users.find(candidate => candidate.id === item.subject_id);
    const label = user?.display_name || item.subject_id;
    const color = participantColor(item.subject_id);
    const start = Math.max(0, Number(item.selection_start || 0));
    const end = Math.max(start, Number(item.selection_end ?? start));
    const rowRect = row.getBoundingClientRect?.();
    const startPoint = editorTextPoint(editor, start);
    let caretRect = editor.getBoundingClientRect?.();
    if (startPoint && typeof document.createRange === "function") {
      try {
        const range = document.createRange();
        range.setStart(startPoint.node, startPoint.offset);
        range.collapse(true);
        caretRect = range.getBoundingClientRect() || caretRect;
      } catch {}
    }
    if (!rowRect || !caretRect) return;
    if (end > start && startPoint && typeof document.createRange === "function") {
      const endPoint = editorTextPoint(editor, end);
      if (endPoint) {
        try {
          const range = document.createRange();
          range.setStart(startPoint.node, startPoint.offset);
          range.setEnd(endPoint.node, endPoint.offset);
          [...range.getClientRects()].slice(0, 24).forEach(rect => {
            const highlight = document.createElement("span");
            highlight.className = "remote-collaboration-selection";
            highlight.style.setProperty("--remote-color", color);
            highlight.style.left = `${rect.left - rowRect.left}px`;
            highlight.style.top = `${rect.top - rowRect.top}px`;
            highlight.style.width = `${Math.max(1, rect.width)}px`;
            highlight.style.height = `${Math.max(2, rect.height)}px`;
            highlight.setAttribute("aria-hidden", "true");
            row.appendChild(highlight);
          });
        } catch {}
      }
    }
    const caret = document.createElement("span");
    caret.className = "remote-collaboration-caret";
    caret.style.setProperty("--remote-color", color);
    caret.style.left = `${Math.max(48, caretRect.left - rowRect.left)}px`;
    caret.style.top = `${Math.max(0, caretRect.top - rowRect.top)}px`;
    caret.style.height = `${Math.max(18, caretRect.height || 22)}px`;
    caret.innerHTML = `<span>${escapeHtml(label)}</span>`;
    caret.setAttribute("aria-hidden", "true");
    row.appendChild(caret);
  });
}

function renderPresenceAvatars() {
  const root = $("#presenceAvatars");
  if (!root) return;
  const participants = [...State.liveCollaboration.participants.values()]
    .filter(item => item.subject_id)
    .sort((left, right) => left.subject_id.localeCompare(right.subject_id));
  root.hidden = participants.length === 0;
  root.setAttribute(
    "aria-label",
    participants.length ? `현재 페이지 참여자 ${participants.length}명` : "현재 페이지 참여자 없음",
  );
  root.innerHTML = participants.slice(0, 5).map(item => {
    const user = State.users.find(candidate => candidate.id === item.subject_id);
    const label = user?.display_name || item.subject_id;
    const initial = String(label || "?").slice(0, 1).toUpperCase();
    return `<span class="presence-avatar${item.state === "idle" ? " idle" : ""}" title="${escapeAttr(label)}">${escapeHtml(initial)}</span>`;
  }).join("");
  renderRemoteCursors();
}

function stopLiveCollaboration({ preserveEditingState = false } = {}) {
  const live = State.liveCollaboration;
  live.generation += 1;
  ["heartbeatTimer", "syncTimer", "reconnectTimer", "refreshTimer"].forEach(key => {
    if (live[key]) clearTimeout(live[key]);
    live[key] = null;
  });
  const socket = live.socket;
  live.socket = null;
  live.connected = false;
  live.syncPending = false;
  live.participants.clear();
  if (!preserveEditingState) {
    live.clientId = "";
    live.sessionBindingSha256 = "";
    live.clientSequence = 1;
    live.durableCursor = 0;
    live.workspaceEpoch = 0;
    live.documentEpoch = 0;
    live.writeAuthorized = false;
    live.initialReplay = false;
    live.initialReplayHasMore = false;
    live.initialReplayNext = 0;
    live.crdtReady = false;
    live.editingScope = null;
    live.pendingEnvelopes = new Map();
    live.sendQueueActive = false;
    live.unsyncedCount = 0;
    live.recoveryOnly = false;
    live.recoveryReason = "";
    live.lastError = "";
    live.errorHistory = [];
    live.offlinePolicyBlocked = !CollaborativeEditingOutbox;
    clearCollaborativeRecoveryBanner();
  }
  renderPresenceAvatars();
  if (socket && socket.readyState < WebSocket.CLOSING) socket.close(1000, "page_changed");
}

async function collaborationSessionBinding() {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return `sha256:${[...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, "0")).join("")}`;
}

function sendLiveDocument(document) {
  const socket = State.liveCollaboration.socket;
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(document));
  return true;
}

function sendPresenceHeartbeat() {
  const payload = { state: document.hidden ? "idle" : "active" };
  const selection = EditorHost.captureSelection();
  if (selection?.kind === "block" && selection.block_id) {
    payload.cursor_block_id = selection.block_id;
    payload.selection_start = Math.max(0, Number(selection.start || 0));
    payload.selection_end = Math.max(payload.selection_start, Number(selection.end ?? selection.start ?? 0));
  } else if (State.activeBlockId) {
    payload.cursor_block_id = State.activeBlockId;
    payload.selection_start = 0;
    payload.selection_end = 0;
  }
  sendLiveDocument({ op: "presence", ttl_ms: 15000, payload });
}

function requestLiveSync() {
  const live = State.liveCollaboration;
  if (!live.connected || live.syncPending || live.initialReplay) return;
  flushCollaborativeOutbox();
  live.syncPending = sendLiveDocument({
    op: "live_replay",
    after_document_sequence: live.liveCursor,
    limit: 100,
  });
}

function sameCollaborativeScope(left, right) {
  return Boolean(
    left && right
    && left.realm === right.realm
    && left.subject_id === right.subject_id
    && left.workspace_id === right.workspace_id
    && left.page_id === right.page_id,
  );
}

function sortedPendingEnvelopes() {
  return [...State.liveCollaboration.pendingEnvelopes.values()].sort((left, right) => (
    Number(left.client_sequence || 0) - Number(right.client_sequence || 0)
    || Number(left.created_at || 0) - Number(right.created_at || 0)
    || String(left.operation_id || "").localeCompare(String(right.operation_id || ""))
  ));
}

async function persistCollaborativeEnvelope(scope, envelope) {
  if (!CollaborativeEditingOutbox) throw new Error("암호화된 오프라인 저장소를 사용할 수 없습니다.");
  await CollaborativeEditingOutbox.put(scope, envelope);
  return envelope;
}

function updateCollaborativeSyncStatus() {
  const live = State.liveCollaboration;
  if (!collaborativeEditingActive() || live.recoveryOnly) return;
  if (live.unsyncedCount > 0) {
    setStatus(
      live.connected ? `변경 ${live.unsyncedCount}개 동기화 중…` : `오프라인 변경 ${live.unsyncedCount}개를 암호화해 보관 중입니다.`,
      live.connected ? "" : "error",
    );
  } else if (live.crdtReady) {
    setStatus("저장됨", "ok");
  }
}

function removeCollaborativeOperation(operationId) {
  const live = State.liveCollaboration;
  const operation = String(operationId || "");
  const envelope = live.pendingEnvelopes.get(operation);
  if (!envelope) return false;
  const scope = live.editingScope ? { ...live.editingScope } : collaborativeEditingScope();
  live.pendingEnvelopes.delete(operation);
  live.unsyncedCount = live.pendingEnvelopes.size;
  if (scope && CollaborativeEditingOutbox) {
    live.persistenceChain = live.persistenceChain
      .catch(() => {})
      .then(() => CollaborativeEditingOutbox.remove(scope, operation))
      .catch(() => {});
  }
  updateCollaborativeSyncStatus();
  return true;
}

async function prepareCollaborativeEditingSession({ preserveIdentity = false } = {}) {
  const live = State.liveCollaboration;
  const scope = collaborativeEditingScope();
  if (!scope || !CollaborativeEditingOutbox) {
    live.offlinePolicyBlocked = true;
    throw new Error("암호화된 오프라인 저장소를 사용할 수 없어 편집을 잠갔습니다.");
  }
  const entries = await CollaborativeEditingOutbox.list(scope);
  const identities = new Set(entries.map(entry => (
    `${String(entry.client_id || "")}\n${String(entry.session_binding_sha256 || "")}`
  )));
  if (identities.size > 1) {
    live.pendingEnvelopes = new Map(entries.map(entry => [String(entry.operation_id), entry]));
    live.unsyncedCount = entries.length;
    live.editingScope = scope;
    enterCollaborativeRecoveryOnly(
      "여러 브라우저 편집 세션의 미전송 변경이 함께 발견되어 자동 병합을 중지했습니다.",
      entries,
    );
  }
  const first = entries[0] || null;
  if (!preserveIdentity || !live.clientId || !live.sessionBindingSha256) {
    live.clientId = first?.client_id || `web_${crypto.randomUUID().replaceAll("-", "")}`;
    live.sessionBindingSha256 = first?.session_binding_sha256 || await collaborationSessionBinding();
  }
  if (
    first
    && (first.client_id !== live.clientId || first.session_binding_sha256 !== live.sessionBindingSha256)
  ) {
    live.clientId = first.client_id;
    live.sessionBindingSha256 = first.session_binding_sha256;
  }
  live.clientSequence = Math.max(
    preserveIdentity ? Number(live.clientSequence || 1) : 1,
    ...entries.map(entry => Number(entry.client_sequence || 0) + 1),
  );
  live.durableCursor = Math.max(
    preserveIdentity ? Number(live.durableCursor || 0) : 0,
    ...entries.map(entry => Number(entry.acknowledgement_sequence || 0)),
  );
  live.editingScope = scope;
  live.pendingEnvelopes = new Map(entries.map(entry => [String(entry.operation_id), entry]));
  live.unsyncedCount = entries.length;
  live.offlinePolicyBlocked = false;
  if (identities.size <= 1) {
    live.recoveryOnly = false;
    live.recoveryReason = "";
    clearCollaborativeRecoveryBanner();
  }
  return entries;
}

function queueCollaborativeUpdate(event) {
  const live = State.liveCollaboration;
  if (
    !collaborativeEditingActive()
    || State.pageData?.page?.can_edit !== true
    || !live.writeAuthorized
    || live.recoveryOnly
    || live.offlinePolicyBlocked
    || event.page_id !== State.activePageId
  ) return Promise.resolve(false);
  const scope = live.editingScope ? { ...live.editingScope } : collaborativeEditingScope();
  if (!scope || !live.clientId || !live.sessionBindingSha256) return Promise.resolve(false);
  const operationId = collaborationOperationId(event.seed ? "seed" : "update");
  const envelope = {
    contract: "code_assist.notion.collaborative_browser_envelope.v1",
    operation_id: operationId,
    client_id: live.clientId,
    session_binding_sha256: live.sessionBindingSha256,
    client_sequence: live.clientSequence,
    acknowledgement_sequence: Math.max(0, Number(live.durableCursor || 0)),
    workspace_epoch: live.workspaceEpoch,
    document_epoch: live.documentEpoch,
    payload_base64: CollaborationEditor.bytesToBase64(event.update),
    structure_commands: (event.structure_commands || []).map((command, index) => ({
      ...EditorCore.deepClone(command),
      operation_id: `${operationId}:command:${index + 1}`,
      expected_document_sequence: null,
      committed_document_sequence: null,
    })),
    recovery_snapshot: EditorCore.deepClone(event.snapshot || null),
    created_at: Date.now(),
  };
  live.clientSequence += 1;
  const generation = live.generation;
  const task = live.persistenceChain
    .catch(() => {})
    .then(async () => {
      await persistCollaborativeEnvelope(scope, envelope);
      if (
        generation === live.generation
        && sameCollaborativeScope(scope, live.editingScope)
      ) {
        live.pendingEnvelopes.set(operationId, envelope);
        live.unsyncedCount = live.pendingEnvelopes.size;
        updateCollaborativeSyncStatus();
        flushCollaborativeOutbox();
      }
      return true;
    });
  live.persistenceChain = task.catch(error => {
    if (generation === live.generation) {
      live.offlinePolicyBlocked = true;
      enterCollaborativeRecoveryOnly(`로컬 편집을 암호화해 보관하지 못했습니다: ${error.message}`);
    }
    return false;
  });
  return task;
}

async function commitCollaborativeStructureCommands(scope, envelope) {
  const live = State.liveCollaboration;
  const commands = Array.isArray(envelope.structure_commands) ? envelope.structure_commands : [];
  for (let index = 0; index < commands.length; index += 1) {
    const command = commands[index];
    if (Number(command.committed_document_sequence || 0) > 0) {
      live.durableCursor = Math.max(live.durableCursor, Number(command.committed_document_sequence));
      continue;
    }
    command.operation_id ||= `${envelope.operation_id}:command:${index + 1}`;
    let attempts = 0;
    while (attempts < 2) {
      attempts += 1;
      command.expected_document_sequence ||= Math.max(1, Number(live.durableCursor || 1));
      await persistCollaborativeEnvelope(scope, envelope);
      try {
        const result = await api(`${collaborativePageBasePath()}/editing/commands`, {
          method: "POST",
          body: {
            command_kind: command.command_kind,
            target_id: command.target_id,
            payload: command.payload || {},
            expected_document_sequence: command.expected_document_sequence,
            expected_workspace_epoch: envelope.workspace_epoch,
            expected_document_epoch: envelope.document_epoch,
            operation_id: command.operation_id,
          },
        });
        command.committed_document_sequence = Number(result.document_sequence || 0);
        if (command.committed_document_sequence < 1) throw new Error("구조 명령 응답에 문서 시퀀스가 없습니다.");
        live.durableCursor = Math.max(live.durableCursor, command.committed_document_sequence);
        envelope.acknowledgement_sequence = live.durableCursor;
        await persistCollaborativeEnvelope(scope, envelope);
        break;
      } catch (error) {
        const current = Number(error?.data?.current_document_sequence || 0);
        if (error?.status === 409 && current > 0 && attempts < 2) {
          command.expected_document_sequence = current;
          live.durableCursor = Math.max(live.durableCursor, current);
          await persistCollaborativeEnvelope(scope, envelope);
          continue;
        }
        throw error;
      }
    }
  }
  return envelope;
}

async function flushCollaborativeOutbox() {
  const live = State.liveCollaboration;
  if (
    !collaborativeEditingActive()
    || !live.connected
    || !live.crdtReady
    || !live.writeAuthorized
    || live.sendQueueActive
    || live.recoveryOnly
    || live.offlinePolicyBlocked
  ) return false;
  const envelope = sortedPendingEnvelopes().find(item => (
    item.client_id === live.clientId
    && item.session_binding_sha256 === live.sessionBindingSha256
  ));
  if (!envelope) {
    updateCollaborativeSyncStatus();
    return false;
  }
  const scope = live.editingScope ? { ...live.editingScope } : collaborativeEditingScope();
  if (!scope) return false;
  const generation = live.generation;
  live.sendQueueActive = true;
  try {
    live.typedCommandChain = live.typedCommandChain
      .catch(() => {})
      .then(() => commitCollaborativeStructureCommands(scope, envelope));
    await live.typedCommandChain;
    if (
      generation !== live.generation
      || !sameCollaborativeScope(scope, live.editingScope)
      || !sendLiveDocument({
        op: "submit",
        operation_id: envelope.operation_id,
        client_sequence: envelope.client_sequence,
        acknowledgement_sequence: envelope.acknowledgement_sequence,
        payload_base64: envelope.payload_base64,
      })
    ) {
      live.sendQueueActive = false;
      return false;
    }
    updateCollaborativeSyncStatus();
    return true;
  } catch (error) {
    live.sendQueueActive = false;
    if ([401, 403, 404].includes(Number(error?.status || 0))) {
      enterCollaborativeRecoveryOnly(
        "현재 권한으로는 보관된 로컬 편집을 다시 적용할 수 없습니다.",
      );
    } else {
      setStatus(`오프라인 변경을 암호화해 보관 중입니다: ${error.message}`, "error");
    }
    return false;
  }
}

function decodeLiveFrame(frame) {
  const binary = atob(frame.payload_base64 || "");
  const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

function scheduleLiveCanonicalRefresh() {
  const live = State.liveCollaboration;
  clearTimeout(live.refreshTimer);
  const pageId = State.activePageId;
  live.refreshTimer = setTimeout(async () => {
    const tasks = [];
    const databaseIds = (State.pageData?.databases || [])
      .map(database => String(database?.id || ""))
      .filter(Boolean);
    if (State.activePageId === pageId && collaborativeDatabaseActive() && databaseIds.length) {
      const applyToBlocks = !collaborativeDatabaseSyncPending();
      tasks.push(reloadCanonicalDatabases(databaseIds, { applyToBlocks }).then(loaded => {
        if (loaded.length && applyToBlocks && State.activePageId === pageId) renderBlocks();
      }));
    }
    if (collaborativeCommentBasePath()) {
      tasks.push(loadComments(State.commentBlockId));
    }
    if (State.navigation.collaborativeSharing || State.navigation.collaborativeSearch) {
      tasks.push(loadInboxItems(false));
    }
    if (State.navigation.collaborativeSearch && !$("#searchOverlay")?.hidden) {
      tasks.push(runGlobalSearch());
    }
    await Promise.allSettled(tasks);
  }, 80);
}

function handleLiveFrames(frames) {
  let canonicalChanged = false;
  const live = State.liveCollaboration;
  const crdtFrames = (frames || []).filter(frame => ["crdt.snapshot", "crdt.update"].includes(frame?.kind));
  if (crdtFrames.length && collaborativeEditingActive()) {
    const result = live.initialReplay
      ? CollaborativeEditorBridge.applyFrames(crdtFrames)
      : CollaborativeEditorBridge.applyRemoteAndProject(crdtFrames);
    result.operation_ids.forEach(removeCollaborativeOperation);
  }
  (frames || []).forEach(frame => {
    if (Number.isInteger(frame.document_sequence)) {
      live.durableCursor = Math.max(live.durableCursor, frame.document_sequence);
      live.initialReplayNext = Math.max(live.initialReplayNext, frame.document_sequence);
    }
    if (
      String(frame.kind || "").startsWith("comment.")
      || String(frame.kind || "").startsWith("page.grant_")
      || String(frame.kind || "").startsWith("workspace.member_")
      || frame.kind === "typed.command.committed"
    ) {
      canonicalChanged = true;
      if (Number.isInteger(frame.document_sequence)) {
        live.liveCursor = Math.max(
          live.liveCursor,
          frame.document_sequence,
        );
      }
      return;
    }
    if (frame.kind === "presence.update" || frame.kind === "presence.leave") {
      try {
        const item = decodeLiveFrame(frame);
        if (frame.kind === "presence.leave" || item.state === "left") {
          State.liveCollaboration.participants.delete(item.subject_id);
        } else {
          State.liveCollaboration.participants.set(item.subject_id, item);
        }
      } catch {}
    }
  });
  renderPresenceAvatars();
  if (canonicalChanged) scheduleLiveCanonicalRefresh();
}

function scheduleLiveReconnect(generation) {
  const live = State.liveCollaboration;
  if (
    generation !== live.generation
    || !collaborativePageBasePath()
    || live.reconnectTimer
  ) return;
  const delay = Math.min(5000, 250 * (2 ** live.reconnectAttempt));
  live.reconnectAttempt += 1;
  live.reconnectTimer = setTimeout(() => {
    live.reconnectTimer = null;
    if (generation === live.generation) {
      void startLiveCollaboration().catch(error => {
        setStatus(`실시간 협업 재연결 실패: ${error.message}`, "error");
      });
    }
  }, delay);
}

function startLivePolling() {
  const live = State.liveCollaboration;
  clearInterval(live.heartbeatTimer);
  clearInterval(live.syncTimer);
  sendPresenceHeartbeat();
  requestLiveSync();
  live.heartbeatTimer = setInterval(sendPresenceHeartbeat, 5000);
  live.syncTimer = setInterval(requestLiveSync, 650);
}

function completeInitialCollaborativeReplay() {
  const live = State.liveCollaboration;
  const pending = sortedPendingEnvelopes();
  live.crdtReady = true;
  CollaborativeEditorBridge.finishReplay(pending);
  live.initialReplay = false;
  const title = $("#pageTitle");
  if (title) title.disabled = !activePageCanEdit();
  renderBlocks();
  if (live.recoveryOnly) {
    showCollaborativeRecoveryBanner(live.recoveryReason, pending);
  } else {
    clearCollaborativeRecoveryBanner();
    updateCollaborativeSyncStatus();
  }
  startLivePolling();
  flushCollaborativeOutbox();
}

async function startLiveCollaboration() {
  const base = collaborativePageBasePath();
  if (!base || !window.WebSocket || !window.crypto?.subtle) {
    stopLiveCollaboration();
    return;
  }
  const currentScope = collaborativeEditingScope();
  const descriptor = CollaborativeEditorBridge.descriptor();
  const preserveEditingState = Boolean(
    collaborativeEditingActive()
    && descriptor.ready
    && descriptor.page_id === State.activePageId
    && sameCollaborativeScope(currentScope, State.liveCollaboration.editingScope),
  );
  stopLiveCollaboration({ preserveEditingState });
  const live = State.liveCollaboration;
  const generation = live.generation;
  let pendingEntries = [];
  if (collaborativeEditingActive()) {
    try {
      pendingEntries = await prepareCollaborativeEditingSession({ preserveIdentity: preserveEditingState });
    } catch (error) {
      if (generation === live.generation) {
        enterCollaborativeRecoveryOnly(error.message);
      }
      return;
    }
  } else {
    live.clientId = `web_${crypto.randomUUID().replaceAll("-", "")}`;
    live.sessionBindingSha256 = await collaborationSessionBinding();
  }
  const clientId = live.clientId;
  const sessionBindingSha256 = live.sessionBindingSha256;
  if (generation !== live.generation || !collaborativePageBasePath()) return;
  let ticket;
  try {
    ticket = await api(`${base}/realtime-ticket`, {
      method: "POST",
      body: { client_id: clientId, session_binding_sha256: sessionBindingSha256 },
    });
  } catch (error) {
    if (generation === live.generation) {
      if (collaborativeEditingActive() && [401, 403, 404].includes(Number(error?.status || 0))) {
        enterCollaborativeRecoveryOnly(
          "현재 권한으로는 이 브라우저에 보관된 로컬 편집을 다시 적용할 수 없습니다.",
          pendingEntries,
        );
      } else {
        setStatus("실시간 연결을 다시 시도하고 있습니다.", "error");
        scheduleLiveReconnect(generation);
      }
    }
    return;
  }
  if (generation !== live.generation) return;
  live.liveCursor = Number(ticket.last_document_sequence || 0);
  live.durableCursor = Math.max(live.durableCursor, live.liveCursor);
  live.workspaceEpoch = Number(ticket.workspace_epoch || 0);
  live.documentEpoch = Number(ticket.document_epoch || 0);
  live.writeAuthorized = (ticket.capabilities || []).includes("write");
  if (
    collaborativeEditingActive()
    && pendingEntries.some(entry => (
      Number(entry.workspace_epoch || 0) !== live.workspaceEpoch
      || Number(entry.document_epoch || 0) !== live.documentEpoch
    ))
  ) {
    enterCollaborativeRecoveryOnly(
      "접근 권한 버전이 변경되어 보관된 로컬 편집의 자동 재적용을 중지했습니다.",
      pendingEntries,
    );
  }
  const socket = new WebSocket(ticket.gateway_url, "code-assist-collaboration-v1");
  live.socket = socket;
  socket.addEventListener("open", () => {
    if (generation !== live.generation) return socket.close();
    sendLiveDocument({
      op: "connect",
      token: ticket.token,
      subject_id: ticket.subject_id,
      workspace_id: ticket.workspace_id,
      page_id: ticket.document_id,
      client_id: clientId,
      session_binding_sha256: sessionBindingSha256,
    });
  });
  socket.addEventListener("message", event => {
    if (generation !== live.generation) return;
    let message;
    try { message = JSON.parse(event.data); } catch { return; }
    if (message.type === "connected") {
      live.connected = true;
      live.lastError = "";
      live.reconnectAttempt = 0;
      if (collaborativeEditingActive()) {
        live.initialReplay = true;
        live.initialReplayHasMore = false;
        live.initialReplayNext = 0;
        sendLiveDocument({ op: "replay", after_document_sequence: 0, limit: 100 });
      } else {
        startLivePolling();
      }
    } else if (message.type === "replay_queued") {
      live.initialReplayHasMore = message.has_more === true;
      sendLiveDocument({ op: "drain", max_frames: 128 });
    } else if (message.type === "live_replay_queued") {
      live.liveCursor = Math.max(
        live.liveCursor,
        Number(message.next_document_sequence || 0),
      );
      sendLiveDocument({ op: "drain", max_frames: 128 });
    } else if (message.type === "frames") {
      live.syncPending = false;
      handleLiveFrames(message.frames);
      if (live.initialReplay) {
        if (live.initialReplayHasMore) {
          sendLiveDocument({
            op: "replay",
            after_document_sequence: live.initialReplayNext,
            limit: 100,
          });
        } else {
          completeInitialCollaborativeReplay();
        }
      }
    } else if (message.type === "ack") {
      const acknowledgement = message.acknowledgement || {};
      live.sendQueueActive = false;
      live.durableCursor = Math.max(
        live.durableCursor,
        Number(acknowledgement.document_sequence || 0),
      );
      live.clientSequence = Math.max(
        live.clientSequence,
        Number(acknowledgement.client_sequence || 0) + 1,
      );
      removeCollaborativeOperation(acknowledgement.operation_id);
      flushCollaborativeOutbox();
    } else if (message.type === "error") {
      live.syncPending = false;
      live.sendQueueActive = false;
      live.lastError = String(message.error || "collaboration_gateway_error");
      live.errorHistory.push(live.lastError);
      if (live.errorHistory.length > 20) live.errorHistory.shift();
      setStatus(`실시간 협업 오류: ${live.lastError}`, "error");
    }
  });
  socket.addEventListener("close", event => {
    if (generation !== live.generation) return;
    live.socket = null;
    live.connected = false;
    live.syncPending = false;
    live.sendQueueActive = false;
    if (event.code !== 1000) {
      live.lastError = live.lastError || `socket_closed_${event.code}:${event.reason || "unknown"}`;
    }
    clearInterval(live.heartbeatTimer);
    clearInterval(live.syncTimer);
    live.heartbeatTimer = null;
    live.syncTimer = null;
    live.participants.clear();
    renderPresenceAvatars();
    scheduleLiveReconnect(generation);
  });
  socket.addEventListener("error", () => {
    if (generation === live.generation && socket.readyState < WebSocket.CLOSING) socket.close();
  });
}

async function loadWorkspaceAccess() {
  const section = $("#workspaceMemberSection");
  if (!State.navigation.collaborativeSharing || !State.navigation.workspaceId) {
    State.workspaceAccess = null;
    if (section) section.hidden = true;
    return;
  }
  try {
    const data = await api(`/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}`);
    State.workspaceAccess = data.workspace || null;
  } catch {
    State.workspaceAccess = null;
  }
  renderWorkspaceMembers();
  renderUsers();
}

function renderWorkspaceMembers() {
  const section = $("#workspaceMemberSection");
  const root = $("#workspaceMemberList");
  if (!section || !root) return;
  const members = State.workspaceAccess?.members;
  const canManage = Array.isArray(members);
  section.hidden = !canManage;
  root.innerHTML = "";
  if (!canManage) return;
  const memberCount = $("#workspaceMemberCount");
  if (memberCount) memberCount.textContent = String(members.length);
  members.forEach(item => {
    const row = document.createElement("div");
    row.className = "mini-row";
    const self = item.subject_id === State.user?.id;
    row.innerHTML = `<span class="member-identity"><strong>${escapeHtml(item.subject_id)}</strong></span><span class="member-teamspaces">팀스페이스 1개⌄</span><span class="member-groups">없음</span><span class="member-actions"><select aria-label="${escapeHtml(item.subject_id)} 역할" data-member-role><option value="restricted_member">제한된 구성원</option><option value="member">구성원</option><option value="membership_admin">구성원 관리자</option><option value="owner">워크스페이스 소유자</option></select><button type="button" class="danger" data-member-revoke ${self ? "disabled" : ""}>×</button></span>`;
    const role = row.querySelector("[data-member-role]");
    role.value = item.role;
    role.disabled = self && item.role === "owner";
    role.addEventListener("change", () => setWorkspaceMember(item.subject_id, role.value));
    row.querySelector("[data-member-revoke]")?.addEventListener("click", () => revokeWorkspaceMember(item.subject_id));
    root.appendChild(row);
  });
  syncWorkspaceLandingBanner(State.pageData?.page);
  syncShareSurfacePresentation();
}

async function setWorkspaceMember(subjectId, role) {
  if (!subjectId || !State.workspaceAccess) return;
  try {
    await api(`/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/members/${encodeURIComponent(subjectId)}`, {
      method: "PUT",
      body: {
        role,
        expected_workspace_epoch: State.workspaceAccess.security_epoch,
        operation_id: newOperationId(),
      },
    });
    await Promise.all([
      loadWorkspaceAccess(),
      loadShares(),
      refreshWorkspaceNavigationCapability(),
    ]);
    setStatus("워크스페이스 구성원 권한을 저장했습니다.", "ok");
  } catch (error) {
    await loadWorkspaceAccess();
    setStatus("워크스페이스 구성원 변경 실패: " + error.message, "error");
  }
}

async function inviteWorkspaceMember() {
  const subjectId = $("#workspaceMemberUser")?.value || "";
  const role = $("#workspaceMemberRole")?.value || "member";
  if (!subjectId) return;
  await setWorkspaceMember(subjectId, role);
  const inspector = $("#inspector");
  if (inspector && !inspector.hidden) {
    inspector.dataset.shareSurfaceLock = "workspace-members";
    applyShareSurfacePresentation("workspace-members");
  }
}

async function revokeWorkspaceMember(subjectId) {
  if (!subjectId || !State.workspaceAccess) return;
  try {
    await api(`/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/members/${encodeURIComponent(subjectId)}`, {
      method: "DELETE",
      body: {
        expected_workspace_epoch: State.workspaceAccess.security_epoch,
        operation_id: newOperationId(),
      },
    });
    await Promise.all([loadWorkspaceAccess(), loadShares(), loadPages()]);
    setStatus("워크스페이스 구성원을 해제했습니다.", "ok");
  } catch (error) {
    await loadWorkspaceAccess();
    setStatus("워크스페이스 구성원 해제 실패: " + error.message, "error");
  }
}

function renderShareAccessExplanation(access) {
  const root = $("#shareAccessExplanation");
  if (!root) return;
  if (!access) {
    root.textContent = "";
    syncPageInheritedSharePrompt(false);
    return;
  }
  const sources = access.sources || access.access_sources || [];
  syncPageInheritedSharePrompt(sources.some(item => item.inherited === true));
  const sourceLabels = sources.map(item => {
    const inherited = item.inherited ? " · 상위 페이지에서 상속" : "";
    const principal = item.principal_id ? ` · ${item.principal_id}` : "";
    return `<span>${escapeHtml(item.kind || "access")}${escapeHtml(principal)} · ${escapeHtml(item.role || "view")}${escapeHtml(inherited)}</span>`;
  }).join("");
  root.innerHTML = `<strong>현재 유효 역할: ${escapeHtml(access.role || access.iam_role || "view")}</strong>${sourceLabels ? `<div class="share-source-list">${sourceLabels}</div>` : ""}`;
}

function syncPageInheritedSharePrompt(visible) {
  const inspector = $("#inspector");
  const prompt = $("#pageInheritedSharePrompt");
  if (inspector) {
    if (visible) inspector.dataset.pageInheritedShare = "true";
    else delete inspector.dataset.pageInheritedShare;
  }
  if (prompt) prompt.hidden = !visible;
  if (!visible) return;
  const workspace = State.workspaceAccess
    || (State.navigation.workspaces || []).find(item => item.id === State.navigation.workspaceId)
    || {};
  const workspaceName = workspace.name || "워크스페이스";
  const memberLabel = $("#pageInheritedShareMemberLabel");
  const generalLabel = $("#pageInheritedShareGeneralLabel");
  const workspaceIcon = $("#pageInheritedShareWorkspaceIcon");
  if (memberLabel) memberLabel.textContent = `${workspaceName}의 멤버`;
  if (generalLabel) generalLabel.textContent = `${workspaceName}의 모든 사용자`;
  if (workspaceIcon) workspaceIcon.textContent = String(workspace.icon || workspaceName[0] || "R").slice(0, 1);
  renderPageInheritedShareSurface();
}

function shareRoleLabel(role) {
  return ({
    owner: "전체 허용",
    full_access: "전체 허용",
    membership_admin: "구성원 관리",
    member: "편집 허용",
    restricted_member: "제한된 구성원",
    edit: "편집",
    edit_content: "콘텐츠 편집",
    comment: "댓글",
    view: "보기",
  })[String(role || "")] || String(role || "보기");
}

function renderPageInheritedShareSurface() {
  const root = $("#pageInheritedSharePeople");
  if (!root) return;
  const members = Array.isArray(State.workspaceAccess?.members)
    ? State.workspaceAccess.members
    : [];
  const directShares = Array.isArray(State.shareAccess?.items)
    ? State.shareAccess.items
    : [];
  const people = new Map();
  members.forEach(item => people.set(item.subject_id, {
    id: item.subject_id,
    role: item.role,
    source: "워크스페이스 멤버",
  }));
  directShares.filter(item => (item.principal_type || "subject") === "subject").forEach(item => {
    const id = item.principal_id || item.shared_user_id;
    if (!id) return;
    people.set(id, {id, role: item.role, source: "페이지 공유", direct: true});
  });
  root.innerHTML = [...people.values()].map(item => {
    const principal = State.users.find(user => user.id === item.id);
    const displayName = principal?.display_name || item.id;
    const initial = String(displayName || item.id).trim().slice(0, 1).toLocaleUpperCase();
    return `<div class="page-inherited-share-person" data-share-principal="${escapeAttr(item.id)}">
      <span class="page-inherited-share-avatar" aria-hidden="true">${escapeHtml(initial)}</span>
      <span class="page-inherited-share-identity"><strong>${escapeHtml(displayName)}</strong><small>${escapeHtml(item.source)}</small></span>
      <span class="page-inherited-share-role">${escapeHtml(shareRoleLabel(item.role))}</span>
      ${item.direct ? `<button type="button" class="danger" data-page-share-revoke="${escapeAttr(item.id)}" aria-label="${escapeAttr(displayName)} 공유 해제">×</button>` : ""}
    </div>`;
  }).join("") || '<div class="mini-row"><span>표시할 사용자가 없습니다.</span></div>';
  root.querySelectorAll("[data-page-share-revoke]").forEach(button => button.addEventListener("click", () => {
    revokeShare(button.dataset.pageShareRevoke, "subject");
  }));

  const userSelect = $("#pageInheritedShareUser");
  const sourceSelect = $("#shareUser");
  if (userSelect && sourceSelect) {
    const previous = userSelect.value;
    userSelect.innerHTML = '<option value="">이름이나 이메일 검색</option>' + sourceSelect.innerHTML;
    userSelect.value = [...userSelect.options].some(option => option.value === previous) ? previous : "";
  }
  const inviteButton = $("#pageInheritedShareInviteBtn");
  if (inviteButton) inviteButton.disabled = !State.pageData?.page?.can_share || !userSelect?.value;
  const sources = State.shareAccess?.effective_access?.sources
    || State.shareAccess?.effective_access?.access_sources
    || [];
  const inheritedSource = sources.find(item => item.inherited === true);
  const workspaceRole = $("#pageInheritedShareWorkspaceRole");
  const effectiveRole = $("#pageInheritedShareEffectiveRole");
  if (workspaceRole) workspaceRole.textContent = shareRoleLabel(inheritedSource?.role);
  if (effectiveRole) effectiveRole.textContent = shareRoleLabel(
    State.shareAccess?.effective_access?.role || State.shareAccess?.effective_access?.iam_role,
  );
  const memberSummary = $("#pageInheritedShareMemberLabel")?.nextElementSibling;
  if (memberSummary) memberSummary.textContent = `팀스페이스 · ${members.length}명의 사용자`;
  const details = $("#pageInheritedShareDetails");
  if (details) {
    details.innerHTML = sources.length
      ? sources.map(item => `<div>${escapeHtml(item.kind || "access")} · ${escapeHtml(item.principal_id || "워크스페이스")} · ${escapeHtml(shareRoleLabel(item.role))}${item.inherited ? " · 상속됨" : ""}</div>`).join("")
      : "추가 권한 원본이 없습니다.";
  }
}

async function shareFromPageInheritedSurface() {
  const user = $("#pageInheritedShareUser")?.value || "";
  if (!user) return;
  $("#sharePrincipalType").value = "subject";
  $("#shareUser").value = user;
  $("#shareRole").value = $("#pageInheritedShareRole")?.value || "edit_content";
  // Sharing a parent page from the adopted Notion-style popover includes its
  // descendants.  The compact surface intentionally has no legacy checkbox;
  // preserving inheritance here makes that visible path match the product
  // semantics and lets later role changes retain the same subtree boundary.
  $("#shareInherit").checked = true;
  await sharePage();
  renderPageInheritedShareSurface();
}

async function loadShares() {
  const root = $("#shareList");
  if (!State.pageData) {
    root.innerHTML = "";
    return;
  }
  const inspector = $("#inspector");
  const loadingGeneration = (Number(State.shareLoadGeneration) || 0) + 1;
  State.shareLoadGeneration = loadingGeneration;
  if (inspector && !inspector.hidden && State.drawerMode === "share") {
    inspector.dataset.shareLoading = "true";
  }
  try {
    await loadWorkspaceAccess();
    const base = collaborativePageBasePath();
    if (base) {
      if (State.pageData.page.can_share) {
        const data = await api(`${base}/shares`);
        State.shareAccess = data;
        renderShareAccessExplanation(data.effective_access);
        renderShares(data.items || []);
      } else {
        const data = await api(`${base}/collaboration`);
        State.shareAccess = {
          workspace_epoch: data.page?.workspace_epoch,
          document_epoch: data.page?.document_epoch,
          effective_access: {
            role: data.page?.iam_role,
            sources: data.page?.access_sources || [],
          },
        };
        renderShareAccessExplanation(State.shareAccess.effective_access);
        root.innerHTML = '<div class="mini-row"><span>전체 권한이 있어야 공유 설정을 변경할 수 있습니다.</span></div>';
      }
      return;
    }
    if (!State.pageData.page.can_share) {
      root.innerHTML = '<div class="mini-row"><span>소유자만 공유 설정 가능</span></div>';
      return;
    }
    const data = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/shares`);
    State.shareAccess = null;
    renderShareAccessExplanation(null);
    renderShares(data.items || []);
  } catch (e) {
    root.innerHTML = `<div class="mini-row"><span>${escapeHtml(e.message)}</span></div>`;
  } finally {
    syncShareSurfacePresentation();
    if (State.shareLoadGeneration === loadingGeneration) {
      State.shareLoadSettledGeneration = loadingGeneration;
      if (inspector) delete inspector.dataset.shareLoading;
    }
  }
}

function syncShareSurfacePresentation() {
  const inspector = $("#inspector");
  if (!inspector || inspector.hidden || State.drawerMode !== "share") return;
  if (inspector.dataset.shareSurfaceLock) {
    applyShareSurfacePresentation(inspector.dataset.shareSurfaceLock);
    return;
  }
  const members = Array.isArray(State.workspaceAccess?.members)
    ? State.workspaceAccess.members
    : [];
  const hasOtherWorkspaceMember = members.some(item => item.subject_id !== State.user?.id);
  const directShares = Array.isArray(State.shareAccess?.items) ? State.shareAccess.items : [];
  const sources = State.shareAccess?.effective_access?.sources
    || State.shareAccess?.effective_access?.access_sources
    || [];
  const inheritedPageAccess = sources.some(item => item.inherited === true);
  let surface = "page-popover";
  if (!inheritedPageAccess && directShares.length === 0 && members.length > 0) {
    surface = hasOtherWorkspaceMember ? "workspace-members" : "workspace-invite";
  }
  applyShareSurfacePresentation(surface);
}

function applyShareSurfacePresentation(surface) {
  const inspector = $("#inspector");
  if (!inspector || !["workspace-invite", "workspace-members", "page-popover"].includes(surface)) return;
  inspector.dataset.shareSurface = surface;
  inspector.setAttribute("aria-modal", surface === "page-popover" ? "false" : "true");
  const nav = inspector.querySelector(".workspace-settings-nav");
  const intro = inspector.querySelector(".workspace-invite-intro");
  const messageLabel = inspector.querySelector(".workspace-invite-message-label");
  const message = inspector.querySelector(".workspace-invite-message");
  const cancel = inspector.querySelector(".workspace-invite-cancel");
  if (nav) nav.hidden = surface !== "workspace-members";
  if (intro) intro.hidden = surface !== "workspace-invite";
  if (messageLabel) messageLabel.hidden = surface !== "workspace-invite";
  if (message) message.hidden = surface !== "workspace-invite";
  if (cancel) cancel.hidden = surface !== "workspace-invite";
  syncWorkspaceInviteControls();
  const labels = {
    "workspace-invite": "멤버 추가하기",
    "workspace-members": "사람",
    "page-popover": "공유",
  };
  $("#drawerTitle").textContent = labels[surface];
  const memberButton = $("#workspaceMemberBtn");
  if (memberButton) {
    memberButton.textContent = surface === "workspace-members" ? "멤버 추가하기⌄" : "초대 보내기";
  }
}

function openShareSurface(surface) {
  openDrawer("share");
  const inspector = $("#inspector");
  if (inspector) inspector.dataset.shareSurfaceLock = surface;
  applyShareSurfacePresentation(surface);
}

function syncWorkspaceInviteControls() {
  const inspector = $("#inspector");
  const inviteSurface = inspector?.dataset.shareSurface === "workspace-invite";
  const subjectId = $("#workspaceMemberUser")?.value || "";
  const button = $("#workspaceMemberBtn");
  if (button) button.disabled = inviteSurface && !subjectId;
}

function renderShares(items) {
  const root = $("#shareList");
  const inspector = $("#inspector");
  const members = Array.isArray(State.workspaceAccess?.members)
    ? State.workspaceAccess.members
    : [];
  const hasOtherWorkspaceMember = members.some(item => item.subject_id !== State.user?.id);
  const directEditShare = items.find(item => {
    const principalType = item.principal_type || "subject";
    const principalId = item.principal_id || item.shared_user_id;
    return principalType === "subject" && principalId !== State.user?.id && item.role === "edit_content";
  });
  const directGuestShare = Boolean(directEditShare && !hasOtherWorkspaceMember);
  const showGuestUpgrade = false;
  if (showGuestUpgrade) syncPageInheritedSharePrompt(false);
  const prompt = $("#pageGuestUpgradePrompt");
  if (inspector) {
    if (directGuestShare) inspector.dataset.pageDirectGuestShare = "true";
    else delete inspector.dataset.pageDirectGuestShare;
    if (showGuestUpgrade) inspector.dataset.pageGuestUpgrade = "true";
    else delete inspector.dataset.pageGuestUpgrade;
  }
  if (prompt) prompt.hidden = !showGuestUpgrade;
  if (directGuestShare) {
    const generalLabel = $("#pageInheritedShareGeneralLabel");
    const workspaceIcon = $("#pageInheritedShareWorkspaceIcon");
    if (generalLabel) generalLabel.textContent = "초대받은 사용자만⌄";
    if (workspaceIcon) {
      workspaceIcon.innerHTML = `
        <svg viewBox="0 0 16 16" focusable="false" aria-hidden="true">
          <path d="M5 6V4.5a3 3 0 0 1 6 0V6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <rect x="3" y="6" width="10" height="8" rx="1" fill="currentColor"/>
          <circle cx="8" cy="9.5" r="1.25" fill="#f0efed"/>
          <rect x="7.25" y="10" width="1.5" height="2" rx=".75" fill="#f0efed"/>
        </svg>`;
    }
  }
  const identity = $("#pageGuestUpgradeIdentity");
  if (identity && directEditShare) {
    const principalId = directEditShare.principal_id || directEditShare.shared_user_id;
    const principal = State.users.find(item => item.id === principalId);
    identity.textContent = `${principal?.display_name || principalId}님은 이미 이 페이지의 게스트입니다.`;
  }
  root.innerHTML = "";
  syncCollaborativeSidebarPresentation();
  if (!items.length) {
    root.innerHTML = '<div class="mini-row"><span>공유 없음</span></div>';
    return;
  }
  items.forEach(item => {
    const row = document.createElement("div");
    row.className = "mini-row";
    const principalId = item.principal_id || item.shared_user_id;
    const principalType = item.principal_type || "subject";
    const inherited = item.inherit_to_descendants ? " · 하위 페이지 포함" : "";
    row.innerHTML = `<span>${escapeHtml(principalId)} <small>${escapeHtml(principalType)} · ${escapeHtml(item.role)}${escapeHtml(inherited)}</small></span><button type="button" class="danger">×</button>`;
    row.querySelector("button").addEventListener("click", () => revokeShare(principalId, principalType));
    root.appendChild(row);
  });
}

async function loadPublicLink() {
  const status = $("#publicShareStatus");
  if (!status) return;
  if (!State.pageData || !State.activePageId) {
    State.publicLink = null;
    renderPublicLink();
    return;
  }
  if (!State.pageData.page.can_share) {
    State.publicLink = { enabled: false, owner_only: true };
    renderPublicLink();
    return;
  }
  try {
    status.textContent = "확인 중...";
    State.publicLink = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/public_link`);
  } catch (e) {
    State.publicLink = { enabled: false, error: e.message };
  }
  renderPublicLink();
}

function renderPublicLink() {
  const status = $("#publicShareStatus");
  const urlInput = $("#publicShareUrl");
  const enableBtn = $("#publicShareEnableBtn");
  const copyBtn = $("#publicShareCopyBtn");
  const disableBtn = $("#publicShareDisableBtn");
  const duplicateOption = $("#publicShareDuplicateOption");
  const allowDuplicate = $("#publicShareAllowDuplicate");
  if (!status || !urlInput || !enableBtn || !copyBtn || !disableBtn) return;
  const item = State.publicLink || {};
  const canShare = Boolean(State.pageData?.page?.can_share);
  enableBtn.disabled = !canShare;
  enableBtn.hidden = false;
  copyBtn.hidden = true;
  disableBtn.hidden = true;
  if (duplicateOption) duplicateOption.hidden = true;
  if (allowDuplicate) {
    allowDuplicate.disabled = true;
    allowDuplicate.checked = true;
  }
  urlInput.hidden = true;
  urlInput.value = "";
  if (!State.activePageId) {
    status.textContent = "페이지를 먼저 선택하세요.";
    return;
  }
  if (!canShare) {
    status.textContent = "소유자만 웹 게시를 설정할 수 있습니다.";
    return;
  }
  if (item.error) {
    status.textContent = item.error;
    return;
  }
  if (item.enabled && item.url) {
    status.textContent = "웹에 게시됨 · 링크가 있는 사람은 읽기 전용으로 볼 수 있습니다.";
    urlInput.hidden = false;
    urlInput.value = item.url;
    enableBtn.hidden = true;
    copyBtn.hidden = false;
    disableBtn.hidden = false;
    if (duplicateOption) duplicateOption.hidden = false;
    if (allowDuplicate) {
      allowDuplicate.disabled = false;
      allowDuplicate.checked = item.allow_duplicate !== false;
    }
    return;
  }
  status.textContent = "아직 웹에 게시되지 않았습니다.";
  enableBtn.hidden = false;
}

async function enablePublicLink() {
  if (!State.activePageId) return;
  try {
    setStatus("공개 링크 생성 중...");
    State.publicLink = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/public_link`, {
      method: "POST",
      body: {},
    });
    renderPublicLink();
    setStatus("공개 링크 생성됨", "ok");
  } catch (e) {
    setStatus("공개 링크 생성 실패: " + e.message, "error");
  }
}

async function disablePublicLink() {
  if (!State.activePageId) return;
  if (!window.confirm("이 페이지의 공개 웹 링크를 중지할까요? 기존 링크는 더 이상 열리지 않습니다.")) return;
  try {
    setStatus("공개 링크 중지 중...");
    State.publicLink = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/public_link`, {
      method: "DELETE",
      body: {},
    });
    renderPublicLink();
    setStatus("공개 링크 중지됨", "ok");
  } catch (e) {
    setStatus("공개 링크 중지 실패: " + e.message, "error");
  }
}

async function updatePublicDuplicatePermission() {
  if (!State.activePageId || !State.publicLink?.enabled) return;
  const checkbox = $("#publicShareAllowDuplicate");
  if (!checkbox) return;
  const requested = checkbox.checked;
  checkbox.disabled = true;
  try {
    State.publicLink = await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/public_link`, {
      method: "PATCH",
      body: { allow_duplicate: requested },
    });
    renderPublicLink();
    setStatus(requested ? "공개 페이지 복제를 허용했습니다." : "공개 페이지 복제를 차단했습니다.", "ok");
  } catch (e) {
    checkbox.checked = !requested;
    checkbox.disabled = false;
    setStatus("공개 페이지 복제 설정 실패: " + e.message, "error");
  }
}

async function sharePage() {
  if (!State.activePageId) return;
  const principalId = $("#shareUser").value;
  if (!principalId) return;
  try {
    setStatus("공유 중...");
    const base = collaborativePageBasePath();
    if (base) {
      // A grant changes the document access epoch. Let any already-acked
      // outbox removal finish first so reopening the same page cannot mistake
      // that stale durable envelope for an offline edit from the new epoch.
      await State.liveCollaboration.persistenceChain.catch(() => {});
      if (!State.shareAccess?.workspace_epoch || !State.shareAccess?.document_epoch) {
        await loadShares();
      }
      await api(`${base}/shares`, {
        method: "POST",
        body: {
          principal_type: $("#sharePrincipalType")?.value || "subject",
          principal_id: principalId,
          role: $("#shareRole").value,
          inherit_to_descendants: Boolean($("#shareInherit")?.checked),
          expected_workspace_epoch: State.shareAccess.workspace_epoch,
          expected_document_epoch: State.shareAccess.document_epoch,
          operation_id: newOperationId(),
        },
      });
    } else {
      await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/shares`, {
        method: "POST",
        body: { shared_user_id: principalId, role: $("#shareRole").value },
      });
    }
    await loadShares();
    setStatus("공유됨", "ok");
  } catch (e) {
    setStatus("공유 실패: " + e.message, "error");
  }
}

async function revokeShare(principalId, principalType = "subject") {
  try {
    const base = collaborativePageBasePath();
    if (base) {
      await api(`${base}/shares/${encodeURIComponent(principalType)}/${encodeURIComponent(principalId)}`, {
        method: "DELETE",
        body: {
          expected_workspace_epoch: State.shareAccess.workspace_epoch,
          expected_document_epoch: State.shareAccess.document_epoch,
          operation_id: newOperationId(),
        },
      });
    } else {
      await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/shares/${encodeURIComponent(principalId)}`, { method: "DELETE" });
    }
    await loadShares();
    setStatus("공유 해제됨", "ok");
  } catch (e) {
    setStatus("공유 해제 실패: " + e.message, "error");
  }
}

async function archiveCurrentPage() {
  if (!State.activePageId || !State.pageData?.page?.can_edit) return;
  if (!window.confirm("현재 페이지를 휴지통으로 이동할까요?")) return;
  try {
    await performArchivePage(State.activePageId);
    await Promise.all([loadPages(), loadTrash()]);
    requestAnimationFrame(() => $("#trashBtn")?.focus());
    setStatus("페이지가 휴지통으로 이동됨", "ok");
  } catch (e) {
    setStatus("페이지 삭제 실패: " + e.message, "error");
  }
}

async function loadSessions() {
  try {
    const data = await api("/api/notion/chat/sessions?limit=30");
    State.sessions = data.items || [];
  } catch {
    State.sessions = [];
  }
  renderSessions();
}

function renderSessions() {
  const sel = $("#sessionSelect");
  sel.innerHTML = State.sessions.length
    ? State.sessions.map(s => `<option value="${escapeHtml(s.session_id)}">${escapeHtml(s.title || s.session_id)}</option>`).join("")
    : '<option value="">세션 없음</option>';
  loadAttachedPages();
}

function focusSessionContext() {
  const panel = $("#contextPanel");
  if (!panel || panel.hidden || $("#inspector").hidden) return;
  panel.focus({ preventScroll: true });
}

async function attachPage() {
  const sessionId = $("#sessionSelect").value;
  if (!State.activePageId || !sessionId) return;
  try {
    setStatus("세션에 일시 첨부 중...");
    await api(`/api/notion/pages/${encodeURIComponent(State.activePageId)}/attach`, {
      method: "POST",
      body: { session_id: sessionId },
    });
    await Promise.all([loadAttachedPages(), loadOverview()]);
    renderHome();
    setStatus("Second Brain Agent 세션에 일시 첨부됨", "ok");
    focusSessionContext();
  } catch (e) {
    setStatus("세션 첨부 실패: " + e.message, "error");
  }
}

async function loadAttachedPages() {
  const root = $("#attachedPages");
  const sessionId = $("#sessionSelect").value;
  if (!sessionId) {
    root.innerHTML = '<div class="mini-row"><span>선택된 세션 없음</span></div>';
    return;
  }
  try {
    const data = await api(`/api/notion/session_pages?session_id=${encodeURIComponent(sessionId)}`);
    const items = data.items || [];
    if (!items.length) {
      root.innerHTML = '<div class="mini-row"><span>연결된 페이지 없음</span></div>';
      return;
    }
    root.innerHTML = "";
    items.forEach(item => {
      const row = document.createElement("div");
      row.className = "mini-row";
      row.innerHTML = `<span>${escapeHtml(item.page?.title || item.page_id)}</span><button type="button" class="danger">×</button>`;
      row.querySelector("button").addEventListener("click", () => detachPage(sessionId, item.page_id));
      root.appendChild(row);
    });
  } catch (e) {
    root.innerHTML = `<div class="mini-row"><span>${escapeHtml(e.message)}</span></div>`;
  }
}

async function detachPage(sessionId, pageId) {
  try {
    await api(`/api/notion/session_pages/${encodeURIComponent(pageId)}?session_id=${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    await Promise.all([loadAttachedPages(), loadOverview()]);
    setStatus("연결 해제됨", "ok");
    focusSessionContext();
  } catch (e) {
    setStatus("연결 해제 실패: " + e.message, "error");
  }
}

async function openPageExportDialog(page = State.pageData?.page) {
  if (!page?.id) return;
  if (State.activePageId === page.id && State.dirty) {
    await savePage();
    if (State.dirty) return;
  }
  let data = State.activePageId === page.id ? State.pageData : null;
  if (!data) {
    try {
      data = await api(`/api/notion/pages/${encodeURIComponent(page.id)}`);
    } catch (error) {
      setStatus("내보내기 준비 실패: " + error.message, "error");
      return;
    }
  }
  const databases = (data?.blocks || []).filter(block => DATABASE_BLOCK_TYPES.has(block.type));
  const workspaceBase = collaborativeSearchWorkspacePath();
  const exportBase = workspaceBase
    ? `${workspaceBase}/pages/${encodeURIComponent(page.id)}`
    : `/api/notion/pages/${encodeURIComponent(page.id)}`;
  openModal("내보내기", `
    <p>페이지를 Markdown 또는 HTML로 저장할 수 있습니다. 데이터베이스는 CSV로 내보내며, 인쇄 대화상자에서는 PDF 저장을 선택할 수 있습니다.</p>
    <div class="export-summary"><strong>${escapeHtml(page.title || "제목 없음")}</strong><span>${(data?.blocks || []).length}개 블록 · ${databases.length}개 데이터베이스</span></div>
  `, [
    { label: "취소", action: closeModal },
    { label: "Markdown", action: () => { closeModal(); window.location.href = `${exportBase}/export_markdown${workspaceBase ? "" : "?download=1"}`; } },
    { label: "HTML", action: () => { closeModal(); window.location.href = `${exportBase}/export_html`; } },
    ...databases.map(block => ({ label: `CSV · ${String(block.content || "데이터베이스").slice(0, 28)}`, action: () => { closeModal(); window.location.href = `${exportBase}/export_csv?block_id=${encodeURIComponent(block.id)}`; } })),
    { label: "인쇄 / PDF", primary: true, action: () => {
      closeModal();
      if (State.activePageId === page.id) window.print();
      else window.open(pageUrl(page.id), "_blank", "noopener");
    } },
  ]);
}

async function publishSecondBrain() {
  if (!State.activePageId) return;
  if (State.dirty) {
    await savePage();
    if (State.dirty) return;
  }
  return publishPageToSecondBrain(State.activePageId);
}

const CanonicalNotionPublication = {
  controller: null,
  transport: null,
};

let ModalReturnFocus = null;

function visibleModalFocusables(dialog) {
  if (!(dialog instanceof HTMLElement)) return [];
  const selector = [
    "a[href]",
    "button:not([disabled])",
    "input:not([disabled]):not([type='hidden'])",
    "select:not([disabled])",
    "textarea:not([disabled])",
    "[contenteditable='true']",
    "[tabindex]:not([tabindex='-1'])",
  ].join(",");
  return [...dialog.querySelectorAll(selector)].filter(element => {
    if (!(element instanceof HTMLElement) || element.hidden) return false;
    const style = getComputedStyle(element);
    return style.display !== "none"
      && style.visibility !== "hidden"
      && element.getClientRects().length > 0;
  });
}

function trapVisibleModalFocus(event) {
  if (event.key !== "Tab") return false;
  const active = document.activeElement;
  const activeDialog = active instanceof Element
    ? active.closest("[role='dialog'][aria-modal='true']")
    : null;
  const dialog = activeDialog || [...document.querySelectorAll("[role='dialog'][aria-modal='true']")]
    .reverse()
    .find(element => !element.closest("[hidden]") && element.getClientRects().length);
  if (!(dialog instanceof HTMLElement)) return false;
  const focusables = visibleModalFocusables(dialog);
  event.preventDefault();
  if (!focusables.length) {
    dialog.focus({ preventScroll: true });
    return true;
  }
  const index = focusables.indexOf(active);
  if (index < 0) {
    (event.shiftKey ? focusables.at(-1) : focusables[0]).focus({ preventScroll: true });
    return true;
  }
  const next = event.shiftKey
    ? focusables[(index - 1 + focusables.length) % focusables.length]
    : focusables[(index + 1) % focusables.length];
  next.focus({ preventScroll: true });
  return true;
}

function showModalWithFocus() {
  const background = $("#modalBg");
  if (background.hidden) {
    const active = document.activeElement;
    ModalReturnFocus = active instanceof HTMLElement && active !== document.body
      ? active
      : null;
  }
  background.hidden = false;
  const dialog = background.querySelector(".modal");
  if (dialog instanceof HTMLElement) dialog.focus({ preventScroll: true });
}

function restoreModalFocus() {
  const target = ModalReturnFocus;
  ModalReturnFocus = null;
  if (
    target instanceof HTMLElement
    && target.isConnected
    && !target.hidden
    && target.getClientRects().length
  ) {
    target.focus({ preventScroll: true });
  }
}

function canonicalPublicationModal() {
  return {
    open({ title, body, footButtons = [], closeable = true }) {
      $("#modalTitle").textContent = title || "";
      const modalBody = $("#modalBody");
      modalBody.replaceChildren();
      if (body instanceof Node) modalBody.appendChild(body);
      else modalBody.textContent = String(body || "");
      const foot = $("#modalActions");
      foot.replaceChildren();
      for (const item of footButtons) {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = item.label;
        if (item.primary) button.className = "primary";
        button.addEventListener("click", async () => {
          button.disabled = true;
          try {
            const result = item.onClick ? await item.onClick() : undefined;
            if (result !== false) closeModal(true);
          } catch (_) {
            // The shared publication renderer already surfaced a stable error.
          } finally {
            button.disabled = false;
          }
        });
        foot.appendChild(button);
      }
      foot.hidden = !footButtons.length;
      $("#modalClose").hidden = !closeable;
      const background = $("#modalBg");
      if (closeable) delete background.dataset.locked;
      else background.dataset.locked = "1";
      showModalWithFocus();
    },
  };
}

function openCanonicalPublicationTarget(target) {
  const agentPort = location.port === "10112" ? "10111" : "10011";
  const url = new URL(`http://${location.hostname || "localhost"}:${agentPort}/`);
  url.searchParams.set("canonical_space_id", target.spaceId);
  url.searchParams.set("canonical_item_id", target.itemId);
  window.open(url.toString(), "_blank", "noopener");
}

function canonicalNotionPublicationTransport() {
  if (!CanonicalNotionPublication.transport) {
    const operationIdFactory = () => window.crypto.randomUUID();
    CanonicalNotionPublication.transport = window.KnowledgeCoreTransport.createTransport({
      csrfToken: () => State.csrfToken || "",
      operationIdFactory,
    });
  }
  return CanonicalNotionPublication.transport;
}

function canonicalNotionPublicationController() {
  if (!CanonicalNotionPublication.controller) {
    const operationIdFactory = () => window.crypto.randomUUID();
    const renderer = window.KnowledgePublicationUI.createDomRenderer({
      documentRef: document,
      modal: canonicalPublicationModal(),
      openTarget: openCanonicalPublicationTarget,
      toast: (message, kind) => setStatus(message, kind),
    });
    CanonicalNotionPublication.controller = window.KnowledgePublicationUI.createController({
      operationIdFactory,
      renderer,
      transport: canonicalNotionPublicationTransport(),
    });
  }
  return CanonicalNotionPublication.controller;
}

async function publishPageToSecondBrain(pageId) {
  if (!pageId) return;
  if (State.activePageId === pageId && State.dirty) {
    await savePage();
    if (State.dirty) return;
  }
  if (State.sidePeek.pageId === pageId && State.sidePeek.dirty) {
    await saveSidePeek();
    if (State.sidePeek.dirty) return;
  }
  if (!window.NotionPublicationBridge || !window.KnowledgeCoreTransport || !window.KnowledgePublicationUI) {
    return publishPageToSecondBrainLegacy(pageId);
  }
  try {
    return await window.NotionPublicationBridge.publish({
      pageId,
      probePublication: () => canonicalNotionPublicationTransport().probePublication(),
      openCanonical: async () => {
        const result = await canonicalNotionPublicationController().open({ pageId, sourceType: "notion" });
        setStatus("지식 게시 미리보기가 준비되었습니다.", "ok");
        return result;
      },
      openLegacy: publishPageToSecondBrainLegacy,
    });
  } catch (error) {
    setStatus("지식 게시 실패(legacy 재시도 없음): " + error.message, "error");
  }
}

async function publishPageToSecondBrainLegacy(pageId) {
  try {
    setStatus("Markdown preview 생성 중...");
    const preview = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/publish_preview`);
    openModal(
      "Second Brain 업로드 확인",
      `
        <p>아래 Markdown을 Second Brain source로 업로드합니다. 승인하면 source markdown으로 저장되고 강한 graph 관계만 자동 반영됩니다.</p>
        <pre>${escapeHtml(preview.markdown || "")}</pre>
      `,
      [
        { label: "취소", action: closeModal },
        {
          label: "승인 후 업로드",
          primary: true,
          action: () => confirmPublishPageToSecondBrain(
            pageId,
            preview.source_snapshot?.source_snapshot_sha256 || "",
          ),
        },
      ],
    );
    setStatus("승인 대기 중");
  } catch (e) {
    setStatus("Preview 실패: " + e.message, "error");
  }
}

async function confirmPublishPageToSecondBrain(pageId, sourceSnapshotSha256 = "") {
  if (!pageId) return;
  try {
    closeModal();
    setStatus("Second Brain으로 보내는 중...");
    const data = await api(`/api/notion/pages/${encodeURIComponent(pageId)}/publish_second_brain`, {
      method: "POST",
      body: {
        expected_source_snapshot_sha256: sourceSnapshotSha256,
      },
    });
    const text = [
      `source: ${data.source_id || "-"}`,
      `wiki path: ${data.wiki_path || "-"}`,
      `images: ${data.image_count || 0}`,
      `graph edges: ${data.graph_edges_added || 0}`,
      `maintenance: ${data.pending_candidate_count || 0}`,
    ].join("\n");
    openModal("Published", `<pre>${escapeHtml(text)}</pre>`);
    setStatus("Second Brain으로 보냈습니다.", "ok");
  } catch (e) {
    setStatus("Publish 실패: " + e.message, "error");
  }
}

function blockAiQuestion(action, text) {
  const source = String(text || "").trim();
  const targetLanguage = /[가-힣]/.test(source) ? "영어" : "한국어";
  const instructions = {
    improve: "의미와 사실을 유지하면서 더 명확하고 자연스럽게 다시 써줘. 결과 텍스트만 반환해줘.",
    spelling: "맞춤법, 띄어쓰기, 문법 오류만 고쳐줘. 의미와 어조는 유지하고 결과 텍스트만 반환해줘.",
    shorter: "핵심 의미를 유지하면서 더 짧고 직접적으로 줄여줘. 결과 텍스트만 반환해줘.",
    longer: "원문의 사실을 바꾸지 말고 유용한 설명과 구체성을 더해 확장해줘. 결과 텍스트만 반환해줘.",
    continue: "원문의 어조와 맥락을 이어 자연스러운 다음 내용을 작성해줘. 새로 이어지는 내용만 반환해줘.",
    summarize: "핵심을 놓치지 않는 짧은 요약으로 바꿔줘. 결과 텍스트만 반환해줘.",
    action_items: "원문에서 실제로 해야 할 일만 추출해 각 줄을 '- [ ] '로 시작하는 체크리스트로 작성해줘. 추측하지 말고 체크리스트만 반환해줘.",
    translate: `${targetLanguage}로 자연스럽게 번역해줘. 번역문만 반환해줘.`,
    professional: "의미를 유지하면서 전문적이고 간결한 업무 문체로 바꿔줘. 결과 텍스트만 반환해줘.",
    simplify: "전문 지식이 없는 사람도 이해할 수 있게 쉽게 풀어 써줘. 결과 텍스트만 반환해줘.",
  };
  return `${instructions[action] || instructions.improve}\n\n=== 편집할 블록 ===\n${source}`;
}

async function startBlockAiAction(index, action = "improve") {
  const block = State.pageData?.blocks?.[index];
  if (!block) return;
  const text = block.content || block.props?.title || "";
  const question = blockAiQuestion(action, text);
  State.aiResult = {
    pageId: State.activePageId,
    blockIndex: index,
    action,
    question,
    answer: "",
  };
  openDrawer("ai");
  $("#aiQuestion").value = question;
  await askNotionAi("#aiQuestion", "#aiAnswer", State.aiResult);
}

function applyAiResult(action) {
  const result = State.aiResult;
  const actions = $("#aiResultActions");
  if (!result) return;
  if (action === "discard") {
    State.aiResult = null;
    $("#aiAnswer").textContent = "";
    actions.hidden = true;
    return;
  }
  if (action === "retry") {
    $("#aiQuestion").value = result.question || "";
    askNotionAi("#aiQuestion", "#aiAnswer", result);
    return;
  }
  if (!activePageCanEdit() || State.activePageId !== result.pageId) {
    setStatus("원본 페이지를 편집할 수 없어 AI 결과를 적용하지 못했습니다.", "error");
    return;
  }
  const index = Number(result.blockIndex);
  if (!State.pageData.blocks[index] || !String(result.answer || "").trim()) return;
  const generated = EditorCore.blocksFromPlainText(result.answer);
  if (action === "replace") State.pageData.blocks.splice(index, 1, ...generated);
  else if (action === "insert") State.pageData.blocks.splice(index + 1, 0, ...generated);
  else return;
  markDirty();
  captureEditorHistory(true);
  renderBlocks();
  focusBlock(action === "replace" ? index : index + 1, 0);
  setStatus(action === "replace" ? "AI 결과로 블록을 바꿨습니다." : "AI 결과를 아래에 삽입했습니다.", "ok");
  State.aiResult = null;
  actions.hidden = true;
}

async function askNotionAi(questionSelector = "#aiQuestion", answerSelector = "#aiAnswer", aiContext = null) {
  const questionEl = typeof questionSelector === "string" ? $(questionSelector) : questionSelector;
  const answerEl = typeof answerSelector === "string" ? $(answerSelector) : answerSelector;
  const q = questionEl?.value.trim();
  if (!q) return;
  const resultActions = $("#aiResultActions");
  if (answerEl?.id === "aiAnswer") resultActions.hidden = true;
  try {
    answerEl.textContent = "생각 중...";
    setStatus("Notion AI 응답 생성 중...");
    const data = await api("/api/notion/ai/ask", {
      method: "POST",
      body: { question: q, page_id: State.activePageId },
    });
    answerEl.textContent = data.answer || "";
    const target = aiContext || (answerEl?.id === "aiAnswer" ? State.aiResult : null);
    if (target && Number.isInteger(Number(target.blockIndex))) {
      State.aiResult = {
        ...target,
        pageId: target.pageId || State.activePageId,
        question: q,
        answer: data.answer || "",
      };
      resultActions.hidden = !data.answer;
    }
    setStatus(`Notion AI 완료 (${data.model_kind || "-"})`, "ok");
  } catch (e) {
    answerEl.textContent = e.message;
    setStatus("Notion AI 실패: " + e.message, "error");
  }
}

let DrawerReturnFocus = null;

function setShareAccessTooltipVisible(visible) {
  const tooltip = $("#shareAccessTooltip");
  if (tooltip) tooltip.hidden = !visible;
}

function openDrawer(mode) {
  if (mode === "ai") return;
  const inspector = $("#inspector");
  if (inspector.hidden) {
    const active = document.activeElement;
    DrawerReturnFocus = active && active !== inspector && typeof active.focus === "function"
      ? active
      : null;
  }
  State.drawerMode = mode;
  inspector.dataset.mode = mode;
  delete inspector.dataset.shareSurface;
  delete inspector.dataset.shareSurfaceLock;
  setShareAccessTooltipVisible(false);
  const labels = { share: "공유", context: "세션에 일시 첨부", files: "첨부파일", ai: "Notion AI", comments: "댓글", history: "페이지 기록" };
  $("#drawerTitle").textContent = labels[mode] || "페이지 옵션";
  inspector.hidden = false;
  $("#sharePanel").hidden = mode !== "share";
  $("#contextPanel").hidden = mode !== "context";
  $("#filesPanel").hidden = mode !== "files";
  $("#aiPanel").hidden = mode !== "ai";
  $("#commentsPanel").hidden = mode !== "comments";
  $("#historyPanel").hidden = mode !== "history";
  setActiveNav(mode === "ai" ? "ai" : "");
  if (mode === "share") {
    loadShares();
    if (!$("#publicShareSection")?.hidden) loadPublicLink();
  }
  if (mode === "context") loadSessions();
  if (mode === "files") {
    renderFiles();
    if (State.navigation.collaborativeFiles) void loadCollaborativeFiles();
  }
  if (mode === "comments") loadComments(State.commentBlockId);
  if (mode === "history") loadPageHistory();
  setTimeout(() => {
    const initialFocus = mode === "share" ? $("#shareUser") : inspector;
    initialFocus?.focus({ preventScroll: true });
  }, 0);
}

function closeDrawer(restoreFocus = false) {
  const returnFocus = restoreFocus ? DrawerReturnFocus : null;
  DrawerReturnFocus = null;
  State.drawerMode = "";
  setShareAccessTooltipVisible(false);
  $("#inspector").hidden = true;
  delete $("#inspector").dataset.mode;
  delete $("#inspector").dataset.shareSurface;
  delete $("#inspector").dataset.shareSurfaceLock;
  delete $("#inspector").dataset.shareLoading;
  $("#inspector").setAttribute("aria-modal", "false");
  setActiveNav(State.sidebarMode === "agent" ? "ai" : (State.activePageId ? "" : "home"));
  if (returnFocus && typeof returnFocus.focus === "function") {
    setTimeout(() => returnFocus.focus({ preventScroll: true }), 0);
  }
}

function showAccessBoundary(workspaceAlias) {
  const view = $("#accessBoundaryView");
  const dialog = $("#accessBoundaryDialog");
  const title = $("#accessBoundaryTitle");
  const description = dialog?.querySelector("p");
  const requestButton = $("#accessBoundaryRequestBtn");
  if (!view || !dialog || !title || !description || !requestButton) return;
  const aliasName = String(workspaceAlias || "워크스페이스").trim() || "워크스페이스";
  const alias = document.createElement("span");
  alias.id = "accessBoundaryWorkspaceAlias";
  alias.textContent = aliasName;
  title.replaceChildren();
  dialog.dataset.language = "ko";
  title.append(alias, document.createTextNode("에서 이 페이지에 대한 사용 권한 없음"));
  description.textContent = "소유자가 승인할 수 있도록 이 페이지에 대한 사용 권한을 요청하세요.";
  requestButton.textContent = "사용 권한 요청";
  requestButton.disabled = false;
  view.hidden = false;
  dialog.focus({ preventScroll: true });
}

function hideAccessBoundary() {
  const view = $("#accessBoundaryView");
  if (view) view.hidden = true;
}

let SearchReturnFocus = null;
let PageFindReturnFocus = null;

function openSearchOverlay(seed = "") {
  const overlay = $("#searchOverlay");
  const input = $("#globalSearchInput");
  if (overlay.hidden) {
    const active = document.activeElement;
    SearchReturnFocus = active && active !== input && typeof active.focus === "function"
      ? active
      : null;
  }
  overlay.hidden = false;
  setActiveNav("search");
  if (seed) input.value = seed;
  if (State.activePageId || seed) {
    input.focus({ preventScroll: true });
  } else {
    $("#sidebarSearchBtn")?.focus({ preventScroll: true });
  }
  setTimeout(() => {
    if (overlay.hidden) return;
    renderSearchCreators();
    runGlobalSearch();
  }, 0);
}

function closeSearchOverlay(restoreFocus = true) {
  $("#searchOverlay").hidden = true;
  setActiveNav(State.sidebarMode === "agent" ? "ai" : (State.activePageId ? "" : "home"));
  const returnFocus = restoreFocus ? SearchReturnFocus : null;
  SearchReturnFocus = null;
  if (returnFocus && returnFocus.isConnected !== false) {
    returnFocus.focus({ preventScroll: true });
  }
}

function pageFindOccurrences(text, query, matchFactory, limit = 1000) {
  const matches = [];
  const source = String(text || "");
  const needle = String(query || "").toLowerCase();
  if (!needle) return matches;
  const haystack = source.toLowerCase();
  let offset = 0;
  while (matches.length < limit) {
    const found = haystack.indexOf(needle, offset);
    if (found < 0) break;
    matches.push(matchFactory(found));
    offset = found + Math.max(1, needle.length);
  }
  return matches;
}

function collectPageFindMatches(query) {
  if (!State.pageData || !String(query || "").trim()) return [];
  const needle = String(query).trim();
  const matches = pageFindOccurrences(State.pageData.page?.title || "", needle, offset => ({ kind: "title", blockIndex: -1, offset }));
  for (let blockIndex = 0; blockIndex < (State.pageData.blocks || []).length && matches.length < 1000; blockIndex += 1) {
    const block = State.pageData.blocks[blockIndex] || {};
    matches.push(...pageFindOccurrences(block.content || "", needle, offset => ({ kind: "block", blockIndex, offset }), 1000 - matches.length));
    if (DATABASE_BLOCK_TYPES.has(block.type) && matches.length < 1000) {
      const databaseText = databaseRows(block).flatMap(item => Object.values(item || {}).map(databaseDisplayValue)).join("\n");
      matches.push(...pageFindOccurrences(databaseText, needle, () => ({ kind: "database", blockIndex, offset: -1 }), 1000 - matches.length));
    }
  }
  return matches.slice(0, 1000);
}

function clearPageFindHighlight() {
  document.querySelectorAll(".page-find-hit").forEach(element => element.classList.remove("page-find-hit"));
}

function selectEditorText(editor, startOffset, length) {
  if (!editor) return;
  const start = textPointAtOffset(editor, startOffset);
  const end = textPointAtOffset(editor, startOffset + length);
  const range = document.createRange();
  range.setStart(start.node, start.offset);
  range.setEnd(end.node, end.offset);
  const selection = window.getSelection();
  selection.removeAllRanges();
  selection.addRange(range);
}

function showPageFindMatch() {
  clearPageFindHighlight();
  const input = $("#pageFindInput");
  const restoreInputFocus = document.activeElement === input;
  const restoreFindFocus = () => {
    if (restoreInputFocus && input?.isConnected) input.focus({ preventScroll: true });
  };
  const total = State.pageFind.matches.length;
  const count = $("#pageFindCount");
  if (count) count.textContent = total ? `${State.pageFind.index + 1}/${total}` : "0/0";
  $("#pageFindPrev")?.toggleAttribute?.("disabled", !total);
  $("#pageFindNext")?.toggleAttribute?.("disabled", !total);
  if (!total || State.pageFind.index < 0) return;
  const match = State.pageFind.matches[State.pageFind.index];
  if (match.kind === "title") {
    const title = $("#pageTitle");
    title?.focus();
    title?.setSelectionRange?.(match.offset, match.offset + State.pageFind.query.length);
    title?.scrollIntoView?.({ block: "center" });
    restoreFindFocus();
    return;
  }
  const row = document.querySelector(`.block-row[data-index="${match.blockIndex}"]`);
  row?.classList.add("page-find-hit");
  row?.scrollIntoView?.({ block: "center", behavior: "smooth" });
  if (match.kind === "block" && match.offset >= 0) {
    const editor = row?.querySelector(".block-editor");
    editor?.focus();
    selectEditorText(editor, match.offset, State.pageFind.query.length);
  }
  restoreFindFocus();
}

function refreshPageFind(resetIndex = true) {
  const input = $("#pageFindInput");
  State.pageFind.query = String(input?.value || "").trim();
  State.pageFind.matches = collectPageFindMatches(State.pageFind.query);
  if (resetIndex) State.pageFind.index = State.pageFind.matches.length ? 0 : -1;
  else if (State.pageFind.index >= State.pageFind.matches.length) State.pageFind.index = State.pageFind.matches.length - 1;
  showPageFindMatch();
}

function movePageFind(delta) {
  const total = State.pageFind.matches.length;
  if (!total) return;
  State.pageFind.index = (State.pageFind.index + delta + total) % total;
  showPageFindMatch();
}

function openPageFind() {
  if (!State.pageData) return;
  const bar = $("#pageFindBar");
  const input = $("#pageFindInput");
  if (!bar || !input) return;
  if (bar.hidden) {
    const active = document.activeElement;
    PageFindReturnFocus = active && active !== input && typeof active.focus === "function"
      ? active
      : null;
  }
  bar.hidden = false;
  input.value = State.pageFind.query || "";
  refreshPageFind(true);
  setTimeout(() => { input.focus(); input.select(); }, 0);
}

function closePageFind() {
  const bar = $("#pageFindBar");
  if (bar) bar.hidden = true;
  clearPageFindHighlight();
  State.pageFind = { query: "", matches: [], index: -1 };
  const returnFocus = PageFindReturnFocus;
  PageFindReturnFocus = null;
  if (returnFocus && returnFocus.isConnected !== false) {
    returnFocus.focus({ preventScroll: true });
  }
}

function scheduleGlobalSearch() {
  clearTimeout(State.searchTimer);
  State.searchTimer = setTimeout(runGlobalSearch, 160);
}

function canonicalSearchDateAfter(value) {
  const now = new Date();
  if (value === "today") {
    return Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000);
  }
  const seconds = { week: 7 * 86400, month: 31 * 86400, year: 366 * 86400 }[value];
  return seconds ? Math.max(0, Math.floor(Date.now() / 1000) - seconds) : 0;
}

function canonicalSearchSort(value) {
  if (value === "updated_desc" || value === "created_desc") return "recent";
  return ["relevance", "oldest", "title"].includes(value) ? value : "relevance";
}

async function runGlobalSearch() {
  const requestSequence = ++State.searchRequestSequence;
  const query = $("#globalSearchInput").value.trim();
  const creator = $("#searchCreatorSelect").value;
  const scope = $("#searchScopeSelect").value;
  const date = $("#searchDateSelect").value;
  const sort = $("#searchSortSelect").value;
  const params = new URLSearchParams({
    q: query,
    creator,
    scope,
    date,
    sort,
    title_only: State.searchTitleOnly ? "1" : "0",
  });
  const root = $("#searchResults");
  root.innerHTML = '<div class="mini-row"><span>검색 중...</span></div>';
  try {
    if (State.navigation.authoritative) {
      if (!State.navigation.enabled || !State.navigation.workspaceId) {
        State.searchItems = [];
      } else if (State.navigation.collaborativeSearch) {
        const workspaceId = State.navigation.workspaceId;
        const canonicalParams = new URLSearchParams({
          q: query,
          creator,
          scope,
          date_after: String(canonicalSearchDateAfter(date)),
          sort: canonicalSearchSort(sort),
          title_only: State.searchTitleOnly ? "1" : "0",
          limit: "100",
        });
        const data = await api(
          `/api/notion/workspaces/${encodeURIComponent(workspaceId)}/search?${canonicalParams.toString()}`,
        );
        if (
          requestSequence !== State.searchRequestSequence
          || workspaceId !== State.navigation.workspaceId
        ) return;
        State.searchItems = data.items || [];
      } else {
        const items = await fetchWorkspacePagesSnapshot(
          State.navigation.workspaceId,
          query,
          State.navigation.sequence,
        );
        if (!items || requestSequence !== State.searchRequestSequence) return;
        State.searchItems = items.filter(item => {
          if (creator === "me" && item.owner_user_id !== State.user?.id) return false;
          if (creator && creator !== "me" && item.owner_user_id !== creator) return false;
          if (scope === "private" && item.scope === "shared") return false;
          if (scope === "shared" && item.scope !== "shared") return false;
          return true;
        });
        if (sort === "oldest") {
          State.searchItems.sort((left, right) => Number(left.updated_at || 0) - Number(right.updated_at || 0));
        } else if (sort === "title") {
          State.searchItems.sort((left, right) => String(left.title || "").localeCompare(String(right.title || "")));
        } else {
          State.searchItems.sort((left, right) => Number(right.updated_at || 0) - Number(left.updated_at || 0));
        }
      }
    } else {
      const data = await api(`/api/notion/search?${params.toString()}`);
      if (requestSequence !== State.searchRequestSequence) return;
      State.searchItems = data.items || [];
    }
    State.activeSearchPageId = State.searchItems[0]?.id || "";
    renderSearchResults();
    renderSearchPreview();
  } catch (e) {
    if (requestSequence !== State.searchRequestSequence) return;
    root.innerHTML = `<div class="mini-row"><span>${escapeHtml(e.message)}</span></div>`;
    $("#searchPreview").innerHTML = "";
  }
}

function searchDayLabel(ts) {
  const n = Number(ts || 0) * 1000;
  if (!n) return "기타";
  const d = new Date(n);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  if (day === start) return "오늘";
  if (day === start - 86400000) return "어제";
  return d.toLocaleDateString();
}

function renderSearchResults() {
  const root = $("#searchResults");
  root.innerHTML = "";
  if (!State.searchItems.length) {
    root.innerHTML = '<div class="mini-row"><span>검색 결과 없음</span></div>';
    return;
  }
  let lastLabel = "";
  const terms = searchTermsForHighlight();
  State.searchItems.forEach(item => {
    const label = searchDayLabel(item.updated_at);
    if (label !== lastLabel) {
      const group = document.createElement("div");
      group.className = "search-group-label";
      group.textContent = label;
      root.appendChild(group);
      lastLabel = label;
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "search-result-row" + (item.id === State.activeSearchPageId ? " active" : "");
    btn.setAttribute("role", "option");
    btn.setAttribute("aria-selected", item.id === State.activeSearchPageId ? "true" : "false");
    btn.innerHTML = `
      <span class="page-icon">${pageIconHtml(item)}</span>
      <span class="search-title-line">
        <strong>${highlightedText(item.title || "제목 없음", terms)}</strong>
        <div class="search-snippet">${highlightedText(item.snippet || "", terms)}</div>
      </span>
      <small>${escapeHtml(item.owner_user_id || "")}</small>
    `;
    btn.addEventListener("click", () => {
      State.activeSearchPageId = item.id;
      renderSearchResults();
      renderSearchPreview();
    });
    btn.addEventListener("dblclick", () => {
      closeSearchOverlay(false);
      openPage(item.id, true, PageFocusPolicy.PRESERVE);
    });
    root.appendChild(btn);
  });
}

function searchTermsForHighlight() {
  const query = $("#globalSearchInput")?.value || "";
  return query.split(/\s+/).filter(term => term.length >= 2);
}

function renderSearchPreview() {
  const root = $("#searchPreview");
  const item = State.searchItems.find(page => page.id === State.activeSearchPageId);
  if (!item) {
    root.innerHTML = "";
    return;
  }
  const terms = searchTermsForHighlight();
  root.innerHTML = `
    <div class="search-preview-actions">
      <button type="button" id="previewCopyLinkBtn" title="링크 복사">⧉</button>
      <button type="button" id="previewOpenBtn" title="열기">↗</button>
    </div>
    <div class="search-preview-icon">${pageIconHtml(item, "search-preview-svg")}</div>
    <small>${escapeHtml(item.owner_user_id || "")}</small>
    <h3>${escapeHtml(item.title || "제목 없음")}</h3>
    <p>${highlightedText(item.snippet || "미리보기 없음", terms)}</p>
  `;
  $("#previewCopyLinkBtn").addEventListener("click", () => copyText(pageUrl(item.id), "페이지 링크 복사됨"));
  $("#previewOpenBtn").addEventListener("click", () => {
    closeSearchOverlay();
    openPage(item.id);
  });
}

async function openTrashOverlay() {
  $("#trashOverlay").hidden = false;
  await loadTrash();
  setTimeout(() => $("#trashSearchInput")?.focus(), 0);
}

function closeTrashOverlay() {
  $("#trashOverlay").hidden = true;
}

async function loadTrash() {
  const query = $("#trashSearchInput")?.value || "";
  const editor = $("#trashEditorFilter")?.value || "";
  const scope = $("#trashScopeFilter")?.value || "all";
  const root = $("#trashResults");
  root.innerHTML = '<div class="mini-row"><span>불러오는 중...</span></div>';
  try {
    const params = new URLSearchParams(
      State.navigation.enabled && State.navigation.workspaceId
        ? { q: query }
        : { q: query, editor, scope },
    );
    const endpoint = State.navigation.enabled && State.navigation.workspaceId
      ? `/api/notion/workspaces/${encodeURIComponent(State.navigation.workspaceId)}/trash`
      : "/api/notion/trash";
    const data = await api(`${endpoint}?${params.toString()}`);
    State.trashItems = data.items || [];
    renderTrash();
  } catch (e) {
    root.innerHTML = `<div class="mini-row"><span>${escapeHtml(e.message)}</span></div>`;
  }
}

function renderTrash() {
  const root = $("#trashResults");
  root.innerHTML = "";
  if (!State.trashItems.length) {
    root.innerHTML = '<div class="mini-row"><span>휴지통이 비어 있습니다.</span></div>';
    return;
  }
  State.trashItems.forEach(item => {
    const row = document.createElement("div");
    row.className = "trash-row";
    row.innerHTML = `
      <span class="page-icon">${pageIconHtml(item)}</span>
      <span class="trash-title">
        <strong>${escapeHtml(item.title || "제목 없음")}</strong>
        <small>${escapeHtml(item.workspace_scope === "workspace" ? "팀스페이스" : (item.parent_id ? "하위 페이지" : "개인 공간"))} · ${item.days_left ?? 30}일 남음</small>
      </span>
      <button type="button" data-restore="${escapeHtml(item.id)}" title="복원">↶</button>
      <button type="button" data-delete="${escapeHtml(item.id)}" title="영구 삭제">⌫</button>
    `;
    row.querySelector("[data-restore]").addEventListener("click", () => restoreTrashPage(item.id));
    row.querySelector("[data-delete]").addEventListener("click", () => deleteTrashPage(item.id));
    root.appendChild(row);
  });
}

async function restoreTrashPage(pageId) {
  try {
    const data = await restorePageSafely(pageId);
    await Promise.all([loadPages(), loadTrash()]);
    closeTrashOverlay();
    if (data.page?.id) {
      await openPage(data.page.id);
      focusCurrentBreadcrumbAfterLifecycle();
    }
    setStatus("페이지 복원됨", "ok");
  } catch (e) {
    setStatus("복원 실패: " + e.message, "error");
  }
}

async function deleteTrashPage(pageId) {
  if (!window.confirm("이 페이지를 영구 삭제할까요? 이 작업은 되돌릴 수 없습니다.")) return;
  try {
    await api(`/api/notion/trash/${encodeURIComponent(pageId)}`, { method: "DELETE" });
    await loadTrash();
    setStatus("영구 삭제됨", "ok");
  } catch (e) {
    setStatus("영구 삭제 실패: " + e.message, "error");
  }
}

function openHelpOverlay() {
  $("#helpOverlay").hidden = false;
}

function closeHelpOverlay() {
  $("#helpOverlay").hidden = true;
}

function openModal(title, html, actions = []) {
  $("#modalTitle").textContent = title || "";
  $("#modalBody").innerHTML = html || "";
  const foot = $("#modalActions");
  foot.innerHTML = "";
  actions.forEach(item => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = item.label;
    if (item.primary) btn.className = "primary";
    btn.addEventListener("click", item.action);
    foot.appendChild(btn);
  });
  foot.hidden = !actions.length;
  $("#modalClose").hidden = false;
  delete $("#modalBg").dataset.locked;
  showModalWithFocus();
}

function closeModal(force = false) {
  if (!force && $("#modalBg").dataset.locked === "1") return;
  State.navigation.settingsDraft = null;
  delete $("#modalBg").dataset.locked;
  $("#modalBg").hidden = true;
  restoreModalFocus();
}

function renderInspectorEmpty() {
  $("#shareList").innerHTML = "";
  $("#fileList").innerHTML = "";
  $("#attachedPages").innerHTML = "";
  State.publicLink = null;
  renderPublicLink();
}

async function logout() {
  if (!(await window.NotionAiNotes.beforeNavigate())) return;
  const subjectId = String(State.user?.id || "");
  try {
    await State.liveCollaboration.persistenceChain;
    if (CollaborativeEditingOutbox && subjectId) {
      await CollaborativeEditingOutbox.clearSubject({
        realm: String(location.origin || ""),
        subject_id: subjectId,
      });
    }
  } catch (error) {
    setStatus(`로그아웃 전에 로컬 협업 데이터를 지우지 못했습니다: ${error.message}`, "error");
    return;
  }
  try {
    await authenticatedFetch("/api/code/auth/logout", { method: "POST" });
  } catch {}
  stopLiveCollaboration();
  if (State.activePageId) {
    EditorHost.notifyLifecycle("document-unloaded", { page_id: State.activePageId });
  }
  clearCurrentUserEditorDrafts();
  window.location.href = "/login";
}

function setDynamicLinks() {
  const host = location.hostname || "localhost";
  const agentPort = location.port === "10112" ? "10111" : "10011";
  $("#codeAgentLink").href = `http://${host}:${agentPort}`;
}

function initEvents() {
  bindSidebarResize();
  document.addEventListener("selectionchange", updateInlineToolbar);
  window.addEventListener("resize", renderRemoteCursors);
  document.addEventListener("scroll", renderRemoteCursors, true);
  document.querySelectorAll("[data-inline-format]").forEach(button => {
    button.addEventListener("mousedown", event => event.preventDefault());
    button.addEventListener("click", () => applyToolbarFormat(button.dataset.inlineFormat));
  });
  $("#workspaceName").addEventListener("click", e => {
    if (e.target.closest("#collapseBtn")) return;
    if (e.target.closest(".workspace-popover")) return;
    toggleWorkspacePopover();
  });
  $("#workspaceName").addEventListener("keydown", e => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggleWorkspacePopover();
    }
  });
  $("#workspaceName").addEventListener("pointerleave", e => {
    e.currentTarget.classList.remove("suppress-hover");
  });
  $("#homeNavBtn").addEventListener("click", event => {
    switchSidebarMode("pages");
    goHome(true, event.detail === 0);
  });
  $("#sidebarMemberInviteBtn").addEventListener("click", () => {
    openShareSurface("workspace-invite");
  });
  $("#sidebarChatNavBtn").addEventListener("click", openInboxOverlay);
  $("#sidebarNewChatBtn").addEventListener("click", openInboxOverlay);
  $("#sidebarComposeBtn").addEventListener("click", () => createPage());
  $("#collaborativeHelpBtn").addEventListener("click", () => $("#helpBtn").click());
  $("#collaborativeTrashBtn").addEventListener("click", () => $("#trashBtn").click());
  $("#inboxNavBtn").addEventListener("click", openInboxOverlay);
  $("#notionAiNavBtn").addEventListener("click", () => switchSidebarMode("agent"));
  $("#sidebarSearchBtn").addEventListener("click", () => openSearchOverlay());
  $("#sidebarNewBtn").addEventListener("click", event => openNewPageMenu(event.currentTarget));
  $("#newPageBtn").addEventListener("click", () => createPage());
  $("#templateBtn").addEventListener("click", createSecondBrainTemplate);
  $("#collapseBtn").addEventListener("click", event => {
    if (!navigationControlsLocked()) {
      setSidebarCollapsed(true);
      if (event.detail === 0) {
        $("#sidebarRestoreBtn")?.focus({ preventScroll: true });
      }
    }
  });
  $("#sidebarRestoreBtn").addEventListener("click", event => {
    if (!navigationControlsLocked()) {
      setSidebarCollapsed(false);
      if (event.detail === 0) {
        $("#collapseBtn")?.focus({ preventScroll: true });
      }
    }
  });
  $("#navigationSettingsBtn")?.addEventListener("click", openNavigationSettings);
  document.querySelectorAll("[data-navigation-toggle]").forEach(button => {
    button.addEventListener("click", () => toggleNavigationSection(
      button.dataset.navigationToggle,
      button.dataset.navigationFocus,
    ));
  });
  $("#pageSearch").addEventListener("input", () => loadPages());
  $("#pageTitle").addEventListener("input", event => {
    if (State.pageData?.page) State.pageData.page.title = event.target.value;
    renderBreadcrumb(State.pageData?.page || null);
    markDirty();
  });
  $("#pageTitle").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      focusBlock(0, 0);
    } else if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
      event.preventDefault();
      applyEditorHistory(event.shiftKey ? 1 : -1, {
        offset: event.target.selectionStart,
        title: true,
      });
    }
  });
  $("#pageBacklinksBtn").addEventListener("click", openBacklinksDialog);
  $("#addIconBtn").addEventListener("click", openIconPicker);
  $("#pageIconBtn").addEventListener("click", openIconPicker);
  $("#addCoverBtn").addEventListener("click", openCoverPicker);
  $("#changeCoverBtn").addEventListener("click", openCoverPicker);
  $("#removeCoverBtn").addEventListener("click", () => updatePageAppearance({ cover: "" }));
  $("#fileInput").addEventListener("change", e => {
    const file = e.target.files?.[0];
    e.target.value = "";
    uploadSelectedFile(file);
  });
  $("#markdownInput").addEventListener("change", e => {
    const file = e.target.files?.[0];
    const parentId = e.target.dataset.parentId || "";
    e.target.value = "";
    e.target.dataset.parentId = "";
    importDataFile(file, parentId);
  });
  $("#shareDrawerBtn").addEventListener("focus", () => {
    setShareAccessTooltipVisible($("#inspector").hidden);
  });
  $("#shareDrawerBtn").addEventListener("blur", () => setShareAccessTooltipVisible(false));
  $("#shareDrawerBtn").addEventListener("click", () => openShareSurface("page-popover"));
  $("#workspaceInviteCancelBtn")?.addEventListener("click", () => closeDrawer(true));
  $("#workspaceMemberUser")?.addEventListener("change", syncWorkspaceInviteControls);
  $("#guestAccessBannerCloseBtn")?.addEventListener("click", () => {
    const banner = $("#guestAccessBanner");
    if (banner) banner.hidden = true;
  });
  $("#workspaceLandingBannerCloseBtn")?.addEventListener("click", () => {
    const banner = $("#workspaceLandingBanner");
    if (banner) banner.hidden = true;
  });
  $("#accessBoundaryRequestBtn")?.addEventListener("click", event => {
    event.currentTarget.textContent = "요청됨";
    event.currentTarget.disabled = true;
  });
  $("#commentDrawerBtn").addEventListener("click", () => { State.commentBlockId = ""; State.commentSelection = null; openDrawer("comments"); });
  $("#addCommentBtn").addEventListener("click", () => { State.commentBlockId = ""; State.commentSelection = null; openDrawer("comments"); });
  $("#pageCommentAffordanceBtn").addEventListener("click", () => { State.commentBlockId = ""; State.commentSelection = null; openDrawer("comments"); });
  $("#contextDrawerBtn").addEventListener("click", () => openDrawer("context"));
  $("#filesDrawerBtn").addEventListener("click", () => openDrawer("files"));
  $("#drawerCloseBtn").addEventListener("click", () => closeDrawer(true));
  $("#shareBtn").addEventListener("click", sharePage);
  $("#sharePrincipalType")?.addEventListener("change", renderSharePrincipalOptions);
  $("#pageInheritedShareUser")?.addEventListener("change", renderPageInheritedShareSurface);
  $("#pageInheritedShareInviteBtn")?.addEventListener("click", shareFromPageInheritedSurface);
  $("#pageInheritedShareCopyBtn")?.addEventListener("click", () => copyText(pageUrl(State.activePageId), "페이지 링크 복사됨"));
  $("#pageInheritedShareAdvancedBtn")?.addEventListener("click", event => {
    const details = $("#pageInheritedShareDetails");
    if (!details) return;
    details.hidden = !details.hidden;
    event.currentTarget.setAttribute("aria-expanded", String(!details.hidden));
  });
  $("#workspaceMemberBtn")?.addEventListener("click", inviteWorkspaceMember);
  $("#publicShareEnableBtn").addEventListener("click", enablePublicLink);
  $("#publicShareCopyBtn").addEventListener("click", () => copyText(State.publicLink?.url || "", "공개 링크 복사됨"));
  $("#publicShareDisableBtn").addEventListener("click", disablePublicLink);
  $("#publicShareAllowDuplicate").addEventListener("change", updatePublicDuplicatePermission);
  $("#sessionSelect").addEventListener("change", loadAttachedPages);
  $("#attachBtn").addEventListener("click", attachPage);
  $("#publishBtn").addEventListener("click", publishSecondBrain);
  $("#favoritePageBtn")?.addEventListener("click", async () => {
    const page = State.pageData?.page;
    if (page) await handlePageMenuAction("favorite", page);
  });
  $("#pageMoreBtn")?.addEventListener("click", e => {
    const page = State.pageData?.page;
    if (page) openPageMenu(page, e.currentTarget);
  });
  $("#pageTrashBtn").addEventListener("click", archiveCurrentPage);
  $("#sidePeekCloseBtn").addEventListener("click", closeSidePeek);
  $("#sidePeekPrevBtn").addEventListener("click", () => navigateSidePeekDatabase(-1));
  $("#sidePeekNextBtn").addEventListener("click", () => navigateSidePeekDatabase(1));
  $("#sidePeekFullBtn").addEventListener("click", async () => {
    await openSidePeekAsFullPage();
  });
  $("#sidePeekNewTabBtn").addEventListener("click", () => {
    const pageId = State.sidePeek.pageId;
    if (pageId) window.open(pageUrl(pageId), "_blank", "noopener");
  });
  $("#sidePeekMoreBtn").addEventListener("click", event => {
    const page = State.sidePeek.data?.page;
    if (page) openPageMenu(page, event.currentTarget);
  });
  $("#aiAskBtn").addEventListener("click", () => askNotionAi());
  $("#sidebarAiAskBtn").addEventListener("click", () => askNotionAi("#sidebarAiQuestion", "#sidebarAiAnswer"));
  document.querySelectorAll("[data-ai-result-action]").forEach(btn => {
    btn.addEventListener("click", () => applyAiResult(btn.dataset.aiResultAction));
  });
  $("#skipToContent").addEventListener("click", event => {
    event.preventDefault();
    $("#workspaceMain")?.focus({ preventScroll: false });
  });
  $("#commentSubmitBtn").addEventListener("click", submitComment);
  $("#commentReplyCancelBtn").addEventListener("click", () => setCommentReply(""));
  $("#commentFilterSelect").addEventListener("change", event => {
    State.commentFilter = event.target.value || "open";
    renderComments();
  });
  $("#commentPersonFilterSelect").addEventListener("change", event => {
    State.commentPersonFilter = event.target.value || "";
    renderComments();
  });
  $("#commentInput").addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      submitComment();
    }
  });
  $("#globalSearchInput").addEventListener("input", scheduleGlobalSearch);
  $("#globalSearchInput").addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!State.searchItems.length) return;
      const current = Math.max(0, State.searchItems.findIndex(item => item.id === State.activeSearchPageId));
      const next = (current + (e.key === "ArrowDown" ? 1 : -1) + State.searchItems.length) % State.searchItems.length;
      State.activeSearchPageId = State.searchItems[next].id;
      renderSearchResults();
      renderSearchPreview();
    } else if (e.key === "Enter" && State.activeSearchPageId) {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) window.open(pageUrl(State.activeSearchPageId), "_blank", "noopener");
      else {
        const pageId = State.activeSearchPageId;
        closeSearchOverlay();
        openPage(pageId);
      }
    } else if (e.key === "Escape") {
      e.preventDefault();
      closeSearchOverlay();
    }
  });
  $("#searchCloseBtn").addEventListener("click", closeSearchOverlay);
  $("#pageFindInput").addEventListener("input", () => refreshPageFind(true));
  $("#pageFindInput").addEventListener("keydown", event => {
    if (event.key === "Enter") {
      event.preventDefault();
      movePageFind(event.shiftKey ? -1 : 1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closePageFind();
    }
  });
  $("#pageFindPrev").addEventListener("click", () => movePageFind(-1));
  $("#pageFindNext").addEventListener("click", () => movePageFind(1));
  $("#pageFindClose").addEventListener("click", closePageFind);
  $("#searchTitleOnlyBtn").addEventListener("click", e => {
    State.searchTitleOnly = !State.searchTitleOnly;
    e.currentTarget.classList.toggle("active", State.searchTitleOnly);
    e.currentTarget.setAttribute("aria-pressed", State.searchTitleOnly ? "true" : "false");
    runGlobalSearch();
  });
  $("#searchCreatorSelect").addEventListener("change", runGlobalSearch);
  $("#searchScopeSelect").addEventListener("change", runGlobalSearch);
  $("#searchDateSelect").addEventListener("change", runGlobalSearch);
  $("#searchSortSelect").addEventListener("change", runGlobalSearch);
  $("#searchFilterBtn").addEventListener("click", () => $("#searchCreatorSelect").focus());
  $("#searchOverlay").addEventListener("click", e => {
    if (e.target.id === "searchOverlay") closeSearchOverlay();
  });
  $("#inboxCloseBtn").addEventListener("click", () => { $("#inboxOverlay").hidden = true; });
  $("#inboxFilter").addEventListener("change", renderInboxItems);
  $("#inboxReadAllBtn").addEventListener("click", markAllInboxItemsRead);
  $("#inboxOverlay").addEventListener("click", e => {
    if (e.target.id === "inboxOverlay") $("#inboxOverlay").hidden = true;
  });
  $("#trashBtn").addEventListener("click", openTrashOverlay);
  $("#trashSearchInput").addEventListener("input", () => {
    clearTimeout(State.searchTimer);
    State.searchTimer = setTimeout(loadTrash, 160);
  });
  $("#trashEditorFilter").addEventListener("change", loadTrash);
  $("#trashScopeFilter").addEventListener("change", loadTrash);
  $("#trashOverlay").addEventListener("click", e => {
    if (e.target.id === "trashOverlay") closeTrashOverlay();
  });
  $("#trashHelpBtn").addEventListener("click", openHelpOverlay);
  $("#helpBtn").addEventListener("click", openHelpOverlay);
  $("#helpCloseBtn").addEventListener("click", closeHelpOverlay);
  $("#helpOverlay").addEventListener("click", e => {
    if (e.target.id === "helpOverlay") closeHelpOverlay();
  });
  document.querySelectorAll("[data-help-action]").forEach(btn => btn.addEventListener("click", () => {
    const action = btn.dataset.helpAction;
    closeHelpOverlay();
    if (action === "search") openSearchOverlay();
    if (action === "blocks") openModal("블록 작업", "<p>페이지 왼쪽의 +는 새 블록을 삽입하고, ::는 전환, 색, 링크 복사, 복제, 이동, 삭제 작업을 실행합니다.</p>");
    if (action === "publish") openModal("Second Brain", "<p>상단 Second Brain 버튼을 누르면 업로드될 Markdown을 먼저 확인한 뒤 지식으로 보낼 수 있습니다.</p>");
  }));
  $("#modalClose").addEventListener("click", closeModal);
  $("#modalBg").addEventListener("click", e => {
    if (e.target.id === "modalBg") closeModal();
  });
  $("#logoutBtn").addEventListener("click", logout);
  document.addEventListener("click", e => {
    if (!e.target.closest("#workspaceName")) closeWorkspacePopover();
    const menu = $("#blockMenu");
    if (!menu.hidden && !menu.contains(e.target) && !e.target.closest(".block-control")) closeBlockMenu();
    const mentionMenu = $("#mentionMenu");
    if (!mentionMenu.hidden && !mentionMenu.contains(e.target) && !e.target.closest(".block-editor")) closeMentionMenu();
    const pageMenu = $("#pageMenu");
    if (!pageMenu.hidden && !pageMenu.contains(e.target) && !e.target.closest(".page-row-action, #pageMoreBtn")) closePageMenu();
    const newPageMenu = $("#newPageMenu");
    if (!newPageMenu.hidden && !newPageMenu.contains(e.target) && !e.target.closest("#sidebarNewBtn")) closeNewPageMenu();
    const movePageMenu = $("#movePageMenu");
    if (!movePageMenu.hidden && !movePageMenu.contains(e.target) && !e.target.closest("#pageMoreBtn, #pageMenu")) closeMovePageMenu();
    const pop = $("#linkPopover");
    if (!pop.hidden && !pop.contains(e.target) && !e.target.closest(".link-preview") && !e.target.closest("[data-link-block]")) closeLinkPopover();
    if (!e.target.closest(".block-row") && !e.target.closest("#blockMenu")) clearBlockSelection();
  });
  document.addEventListener("keydown", e => {
    if (trapVisibleModalFocus(e)) return;
    const command = e.ctrlKey || e.metaKey;
    const typing = Boolean(e.target.closest?.("input, textarea, select, [contenteditable='true']"));
    if (command && !e.shiftKey && e.key.toLowerCase() === "f" && State.pageData) {
      e.preventDefault();
      openPageFind();
      return;
    }
    if (e.ctrlKey && !e.altKey && !$("#sidePeek").hidden && ["j", "k"].includes(e.key.toLowerCase())) {
      e.preventDefault();
      navigateSidePeekDatabase(e.key.toLowerCase() === "k" ? -1 : 1);
      return;
    }
    if (command && e.shiftKey && e.key.toLowerCase() === "l") {
      e.preventDefault();
      toggleColorTheme();
      return;
    }
    if (command && !e.shiftKey && e.key.toLowerCase() === "n") {
      e.preventDefault();
      createPage();
      return;
    }
    if (command && e.shiftKey && e.key.toLowerCase() === "r" && State.pageData?.page?.can_edit) {
      e.preventDefault();
      renamePage(State.pageData.page);
      return;
    }
    if (command && !e.shiftKey && e.key.toLowerCase() === "d" && !typing && !State.selectedBlockIds.size && State.pageData?.page?.role === "owner") {
      e.preventDefault();
      duplicatePage(State.activePageId);
      return;
    }
    if (command && !e.shiftKey && (e.key === "[" || e.key === "]")) {
      e.preventDefault();
      if (e.key === "[") history.back?.();
      else history.forward?.();
      return;
    }
    if (State.selectedBlockIds.size && !typing) {
      if (e.key === "Backspace" || e.key === "Delete") {
        e.preventDefault();
        const indices = selectedBlockIndices();
        if (selectionContainsDatabase(indices)) return;
        State.pageData.blocks = EditorCore.removeBlocks(State.pageData.blocks, indices);
        clearBlockSelection();
        markDirty();
        captureEditorHistory(true);
        renderBlocks();
        return;
      }
      if (command && e.key.toLowerCase() === "c") {
        e.preventDefault();
        copySelectedBlocks(false);
        return;
      }
      if (command && e.key.toLowerCase() === "x") {
        e.preventDefault();
        copySelectedBlocks(true);
        return;
      }
      if (command && e.key.toLowerCase() === "d") {
        e.preventDefault();
        const indices = selectedBlockIndices();
        if (!databaseStructureMutationAllowed(indices)) return;
        State.pageData.blocks = EditorCore.duplicateBlocks(State.pageData.blocks, indices);
        clearBlockSelection();
        markDirty();
        captureEditorHistory(true);
        renderBlocks();
        return;
      }
      const selectedIndices = selectedBlockIndices();
      const activeIndex = Math.max(0, State.pageData?.blocks?.findIndex(block => block.id === State.activeBlockId) ?? -1);
      if (command && e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) {
        e.preventDefault();
        moveSelectedBlocks(e.key === "ArrowUp" ? -1 : 1);
        return;
      }
      if (e.key === "ArrowUp" || e.key === "ArrowDown") {
        e.preventDefault();
        const delta = e.key === "ArrowUp" ? -1 : 1;
        const next = Math.max(0, Math.min((State.pageData?.blocks?.length || 1) - 1, activeIndex + delta));
        selectBlockHandle(next, e.shiftKey);
        return;
      }
      if (command && e.key === "/") {
        e.preventDefault();
        const index = selectedIndices[0] ?? activeIndex;
        const anchor = document.querySelector(`.block-row[data-index="${index}"] [data-action="menu"]`);
        if (anchor) openBlockMenu(index, anchor, "actions");
        return;
      }
      if (command && e.key === "Enter") {
        e.preventDefault();
        modifyBlockAt(activeIndex);
        return;
      }
      if (command && e.shiftKey && e.key.toLowerCase() === "h" && applyLastBlockColor(activeIndex)) {
        e.preventDefault();
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const block = State.pageData?.blocks?.[activeIndex];
        if (block?.type === "child_page" && block.props?.page_id) openPage(block.props.page_id);
        else {
          clearBlockSelection();
          focusBlock(activeIndex, 0);
        }
        return;
      }
      if (e.key === " ") {
        const block = State.pageData?.blocks?.[activeIndex];
        if (openBlockPreview(block)) {
          e.preventDefault();
          return;
        }
      }
    }
    if (command && ["k", "p"].includes(e.key.toLowerCase()) && !e.target.closest?.(".block-editor")) {
      e.preventDefault();
      openSearchOverlay();
    }
    if (command && e.shiftKey && e.key.toLowerCase() === "u" && State.pageData?.page?.parent_id) {
      e.preventDefault();
      openPage(State.pageData.page.parent_id);
    }
    if (command && e.altKey && e.key.toLowerCase() === "t") {
      if (toggleAllBlocks()) e.preventDefault();
    }
    if (command && e.key === "\\") {
      e.preventDefault();
      if (!navigationControlsLocked()) {
        setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
      }
    }
    if (e.key === "Escape") {
      if (!$("#modalBg").hidden) {
        e.preventDefault();
        closeModal();
        return;
      }
      if (!$("#inspector").hidden) {
        e.preventDefault();
        closeDrawer(true);
        return;
      }
      if (!$("#pageFindBar").hidden) closePageFind();
      closeBlockMenu();
      closeMentionMenu();
      $("#inlineToolbar").hidden = true;
      closePageMenu();
      closeNewPageMenu();
      closeMovePageMenu();
      closeLinkPopover();
      if (!$("#searchOverlay").hidden) closeSearchOverlay();
      if (!$("#inboxOverlay").hidden) $("#inboxOverlay").hidden = true;
      if (!$("#trashOverlay").hidden) closeTrashOverlay();
      if (!$("#helpOverlay").hidden) closeHelpOverlay();
      if (State.selectedBlockIds.size && !typing) clearBlockSelection();
    }
  });
  window.addEventListener("popstate", e => {
    const pageId = e.state?.pageId || pageIdFromPath();
    if (pageId) openPage(pageId, false);
    else goHome(false);
  });
  window.addEventListener("beforeunload", e => {
    stopLiveCollaboration();
    if (!State.dirty && !State.sidePeek.dirty) return;
    e.preventDefault();
    e.returnValue = "";
  });
}

function pageIdFromPath() {
  const match = location.pathname.match(/^\/page\/(.+)$/);
  return match ? decodeURIComponent(match[1]) : "";
}

async function boot() {
  document.body.removeAttribute("data-notion-ready");
  try {
    loadColorTheme();
    loadSidebarWidth();
    loadSidebarCollapsed();
    setDynamicLinks();
    initEvents();
    document.querySelector(".notion-root")?.removeAttribute?.("inert");
    const startupPageLoadSequence = State.pageLoadSequence;
    if (!(await loadAuth())) return;
    await Promise.all([loadUsers(), loadOverview(), loadSessions(), loadInboxItems()]);
    await initializeWorkspaceNavigation();
    applyAdoptedSurfacePolicy();
    if (!State.navigation.authoritative) await loadPages();
    if (State.pageLoadSequence === startupPageLoadSequence) {
      const pageId = pageIdFromPath();
      if (pageId) await openPage(pageId, false, PageFocusPolicy.PRESERVE);
      else {
        const firstPageId = defaultPageId();
        if (firstPageId) await openPage(firstPageId, false, PageFocusPolicy.PRESERVE);
        else {
          renderEditor();
          setStatus("Ready", "ok");
        }
      }
    }
    document.body.dataset.notionReady = "true";
    window.setInterval(() => loadInboxItems(false), 60000);
  } catch (e) {
    document.body.removeAttribute("data-notion-ready");
    setStatus("Startup failed: " + e.message, "error");
  }
}

boot();
