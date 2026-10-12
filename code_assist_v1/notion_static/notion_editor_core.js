(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionEditorCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const MAX_INDENT = 6;

  const BLOCK_DEFINITIONS = [
    { type: "ai_notes", label: "AI 노트", icon: "✧", shortcut: "회의", group: "advanced", keywords: ["ai", "meeting", "notes", "회의", "회의록", "요약", "ai 노트"] },
    { type: "paragraph", label: "텍스트", icon: "T", shortcut: "", group: "basic", keywords: ["text", "plain", "텍스트", "본문"] },
    { type: "heading1", label: "제목 1", icon: "H1", shortcut: "#", group: "basic", keywords: ["h1", "heading", "제목"] },
    { type: "heading2", label: "제목 2", icon: "H2", shortcut: "##", group: "basic", keywords: ["h2", "heading", "제목"] },
    { type: "heading3", label: "제목 3", icon: "H3", shortcut: "###", group: "basic", keywords: ["h3", "heading", "제목"] },
    { type: "toggle_heading1", label: "제목 토글 1", icon: "▸H1", shortcut: "", group: "basic", keywords: ["toggle heading 1", "접이식 제목 1", "토글 제목 1"] },
    { type: "toggle_heading2", label: "제목 토글 2", icon: "▸H2", shortcut: "", group: "basic", keywords: ["toggle heading 2", "접이식 제목 2", "토글 제목 2"] },
    { type: "toggle_heading3", label: "제목 토글 3", icon: "▸H3", shortcut: "", group: "basic", keywords: ["toggle heading 3", "접이식 제목 3", "토글 제목 3"] },
    { type: "todo", label: "할 일 목록", icon: "☐", shortcut: "[]", group: "basic", keywords: ["todo", "checkbox", "task", "할일", "체크"] },
    { type: "bullet", label: "글머리 기호 목록", icon: "•", shortcut: "-", group: "basic", keywords: ["bullet", "list", "목록", "글머리"] },
    { type: "numbered", label: "번호 매기기 목록", icon: "1.", shortcut: "1.", group: "basic", keywords: ["number", "ordered", "list", "번호", "목록"] },
    { type: "toggle", label: "토글 목록", icon: "▸", shortcut: ">", group: "basic", keywords: ["toggle", "details", "토글", "접기"] },
    { type: "quote", label: "인용", icon: "❝", shortcut: "\"", group: "basic", keywords: ["quote", "인용"] },
    { type: "callout", label: "콜아웃", icon: "💡", shortcut: "", group: "basic", keywords: ["callout", "notice", "콜아웃", "강조"] },
    { type: "divider", label: "구분선", icon: "―", shortcut: "---", group: "basic", keywords: ["divider", "line", "구분선"] },
    { type: "child_page", label: "페이지", icon: "▱", shortcut: "", group: "basic", keywords: ["page", "subpage", "페이지", "하위"] },
    { type: "code", label: "코드", icon: "{}", shortcut: "```", group: "media", keywords: ["code", "snippet", "코드"] },
    { type: "equation", label: "수학 공식", icon: "∑", shortcut: "$$", group: "media", keywords: ["equation", "math", "latex", "수식", "공식"] },
    { type: "image", label: "이미지", icon: "▧", shortcut: "", group: "media", keywords: ["image", "photo", "이미지", "사진"] },
    { type: "video", label: "동영상", icon: "▶", shortcut: "", group: "media", keywords: ["video", "movie", "동영상", "비디오"] },
    { type: "audio", label: "오디오", icon: "♫", shortcut: "", group: "media", keywords: ["audio", "sound", "오디오", "음성"] },
    { type: "file", label: "파일", icon: "⌑", shortcut: "", group: "media", keywords: ["file", "attachment", "파일", "첨부"] },
    { type: "pdf", label: "PDF", icon: "PDF", shortcut: "", group: "media", keywords: ["pdf", "문서"] },
    { type: "bookmark", label: "웹 북마크", icon: "⌁", shortcut: "", group: "media", keywords: ["bookmark", "url", "link", "북마크", "링크"] },
    { type: "embed", label: "임베드", icon: "<>", shortcut: "", group: "media", keywords: ["embed", "iframe", "임베드"] },
    { type: "table", label: "표 보기", icon: "▦", shortcut: "", group: "database", keywords: ["table", "database", "표", "데이터베이스"] },
    { type: "board", label: "보드 보기", icon: "▤", shortcut: "", group: "database", keywords: ["board", "kanban", "보드", "칸반"] },
    { type: "list", label: "목록 보기", icon: "☷", shortcut: "", group: "database", keywords: ["list", "database", "목록", "데이터베이스"] },
    { type: "gallery", label: "갤러리 보기", icon: "▦", shortcut: "", group: "database", keywords: ["gallery", "cards", "갤러리", "카드"] },
    { type: "calendar", label: "캘린더 보기", icon: "▣", shortcut: "", group: "database", keywords: ["calendar", "date", "캘린더", "달력"] },
    { type: "timeline", label: "타임라인 보기", icon: "↔", shortcut: "", group: "database", keywords: ["timeline", "date range", "타임라인", "일정"] },
    { type: "chart", label: "차트 보기", icon: "▥", shortcut: "", group: "database", keywords: ["chart", "graph", "차트", "그래프"] },
    { type: "dashboard", label: "대시보드 보기", icon: "▦", shortcut: "", group: "database", keywords: ["dashboard", "widgets", "대시보드", "위젯"] },
    { type: "form", label: "폼 보기", icon: "▤", shortcut: "", group: "database", keywords: ["form", "survey", "폼", "양식", "설문"] },
    { type: "feed", label: "피드 보기", icon: "☷", shortcut: "", group: "database", keywords: ["feed", "activity", "피드", "활동"] },
    { type: "map", label: "지도 보기", icon: "⌖", shortcut: "", group: "database", keywords: ["map", "place", "location", "지도", "장소", "위치"] },
    { type: "table_of_contents", label: "목차", icon: "≡", shortcut: "", group: "advanced", keywords: ["contents", "toc", "목차"] },
    { type: "breadcrumb", label: "현재 페이지 경로", icon: "／", shortcut: "", group: "advanced", keywords: ["breadcrumb", "path", "경로"] },
    { type: "link_to_page", label: "페이지 링크", icon: "↗", shortcut: "", group: "advanced", keywords: ["link page", "페이지 링크", "링크"] },
    { type: "simple_table", label: "간단한 표", icon: "▦", shortcut: "", group: "advanced", keywords: ["simple table", "grid", "간단한 표"] },
    { type: "button", label: "버튼", icon: "▣", shortcut: "", group: "advanced", keywords: ["button", "action", "버튼", "액션"] },
    { type: "synced_block", label: "동기화 블록", icon: "↻", shortcut: "", group: "advanced", keywords: ["synced", "sync", "동기화", "공유 블록"] },
    { type: "tabs", label: "탭", icon: "▤", shortcut: "", group: "advanced", keywords: ["tabs", "tab", "sections", "탭", "섹션"] },
  ];

  const BLOCK_BY_TYPE = Object.fromEntries(BLOCK_DEFINITIONS.map(item => [item.type, item]));
  const TEXT_BLOCK_TYPES = new Set([
    "paragraph", "heading1", "heading2", "heading3", "toggle_heading1", "toggle_heading2",
    "toggle_heading3", "todo", "bullet", "numbered", "toggle", "quote", "callout", "code",
    "equation", "synced_block",
  ]);
  const LIST_BLOCK_TYPES = new Set(["todo", "bullet", "numbered", "toggle"]);

  function deepClone(value) {
    if (typeof structuredClone === "function") return structuredClone(value);
    return JSON.parse(JSON.stringify(value));
  }

  function blockId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return "tmp_" + crypto.randomUUID().replace(/-/g, "");
    }
    return "tmp_" + Date.now().toString(36) + Math.random().toString(36).slice(2);
  }

  function defaultProps(type) {
    if (type === "ai_notes") return { transcript: "", notes: "", instructions: "", model_id: "", ai_notes_version: 1 };
    if (["table", "board", "list", "gallery", "calendar", "timeline", "chart", "dashboard", "form", "feed", "map"].includes(type)) {
      const databaseProps = {
        view: type,
        rows: [],
        properties: [
          { id: "title", name: "이름", type: "title" },
          { id: "status", name: "상태", type: "status" },
          { id: "date", name: "날짜", type: "date" },
          { id: "tags", name: "태그", type: "multi_select" },
        ],
        filters: [],
        sorts: [],
        group_property: "",
      };
      if (type === "map") databaseProps.properties.push({ id: "place", name: "장소", type: "place" });
      if (type === "form") {
        databaseProps.form_title = "새 폼";
        databaseProps.form_submit_label = "제출";
        databaseProps.form_required_properties = [];
      }
      return databaseProps;
    }
    if (type === "todo") return { checked: false };
    if (["toggle", "toggle_heading1", "toggle_heading2", "toggle_heading3"].includes(type)) return { open: true, children: [] };
    if (type === "callout") return { icon: "💡" };
    if (["bookmark", "embed", "video", "audio", "pdf"].includes(type)) return { url: "" };
    if (type === "code") return { language: "plain text", caption: "" };
    if (type === "simple_table") return { rows: [["", ""], ["", ""]], header_row: false, header_column: false };
    if (type === "link_to_page") return { page_id: "" };
    if (type === "button") return { label: "버튼", action: "insert_block", url: "" };
    if (type === "tabs") {
      const first = "tab_" + blockId().replace(/^tmp_/, "");
      const second = "tab_" + blockId().replace(/^tmp_/, "");
      return {
        tabs: [{ id: first, name: "탭 1" }, { id: second, name: "탭 2" }],
        active_tab_id: first,
      };
    }
    return {};
  }

  function createBlock(type = "paragraph", overrides = {}) {
    const safeType = BLOCK_BY_TYPE[type] ? type : "paragraph";
    const id = overrides.id || blockId();
    const props = { ...defaultProps(safeType), ...(deepClone(overrides.props || {})) };
    if (safeType === "synced_block" && !props.sync_id) props.sync_id = "sync_" + String(id).replace(/^tmp_/, "");
    return {
      id,
      type: safeType,
      content: String(overrides.content || ""),
      props,
    };
  }

  function normalizeBlock(block) {
    const raw = block && typeof block === "object" ? block : {};
    return createBlock(BLOCK_BY_TYPE[raw.type] ? raw.type : "paragraph", raw);
  }

  function isTextBlock(type) {
    return TEXT_BLOCK_TYPES.has(type);
  }

  function filterBlockDefinitions(query = "") {
    const q = String(query || "").trim().toLowerCase().replace(/^\//, "");
    if (!q) return BLOCK_DEFINITIONS.slice();
    return BLOCK_DEFINITIONS.filter(item => {
      const haystack = [item.type, item.label, item.shortcut, ...(item.keywords || [])].join(" ").toLowerCase();
      return haystack.includes(q);
    });
  }

  function markdownShortcut(value) {
    const text = String(value || "");
    const exact = {
      "# ": "heading1",
      "## ": "heading2",
      "### ": "heading3",
      "- ": "bullet",
      "* ": "bullet",
      "+ ": "bullet",
      "1. ": "numbered",
      "a. ": "numbered",
      "i. ": "numbered",
      "[] ": "todo",
      "[ ] ": "todo",
      "> ": "toggle",
      "\" ": "quote",
      "``` ": "code",
      "$$ ": "equation",
    };
    if (exact[text]) return { type: exact[text], content: "" };
    if (text === "---" || text === "***") return { type: "divider", content: "" };
    return null;
  }

  function typeAfterEnter(block, offset) {
    const type = block.type || "paragraph";
    const content = String(block.content || "");
    if (LIST_BLOCK_TYPES.has(type)) return type;
    if (type === "paragraph") return "paragraph";
    if (["toggle_heading1", "toggle_heading2", "toggle_heading3"].includes(type)) {
      return offset < content.length ? type : "paragraph";
    }
    if (["heading1", "heading2", "heading3", "quote", "callout", "equation"].includes(type)) {
      return offset < content.length ? type : "paragraph";
    }
    return "paragraph";
  }

  function splitBlock(block, offset) {
    const current = normalizeBlock(block);
    const content = String(current.content || "");
    const point = Math.max(0, Math.min(content.length, Number(offset) || 0));
    const before = content.slice(0, point);
    const after = content.slice(point);
    const first = deepClone(current);
    first.content = before;
    if (first.props) delete first.props.rich_text_html;
    const nextType = typeAfterEnter(current, point);
    const second = createBlock(nextType, {
      content: after,
      props: {
        ...(LIST_BLOCK_TYPES.has(nextType) ? { indent: Number(current.props?.indent || 0) } : {}),
        ...(nextType === "todo" ? { checked: false } : {}),
        ...(current.props?.layout_group ? {
          layout_group: current.props.layout_group,
          layout_column: Number(current.props.layout_column || 0),
          layout_columns: Number(current.props.layout_columns || 2),
        } : {}),
        ...(current.props?.tab_group ? {
          tab_group: current.props.tab_group,
          tab_id: current.props.tab_id,
        } : {}),
      },
    });
    return [first, second];
  }

  function mergeBlocks(previous, current) {
    const left = normalizeBlock(previous);
    const right = normalizeBlock(current);
    const caret = String(left.content || "").length;
    left.content = String(left.content || "") + String(right.content || "");
    if (left.props) delete left.props.rich_text_html;
    return { block: left, caret };
  }

  function duplicateBlocks(blocks, indices) {
    const source = (blocks || []).map(normalizeBlock);
    const wanted = [...new Set((indices || []).map(Number).filter(i => i >= 0 && i < source.length))].sort((a, b) => a - b);
    if (!wanted.length) return source;
    const wantedSet = new Set(wanted);
    const out = [];
    source.forEach((block, index) => {
      out.push(block);
      if (wantedSet.has(index)) out.push(createBlock(block.type, { content: block.content, props: block.props }));
    });
    return out;
  }

  function removeBlocks(blocks, indices) {
    const remove = new Set((indices || []).map(Number));
    const out = (blocks || []).filter((_, index) => !remove.has(index)).map(normalizeBlock);
    return out.length ? out : [createBlock("paragraph")];
  }

  function moveBlock(blocks, fromIndex, toIndex) {
    const out = (blocks || []).map(normalizeBlock);
    const from = Number(fromIndex);
    let to = Number(toIndex);
    if (!Number.isInteger(from) || !Number.isInteger(to) || from < 0 || from >= out.length) return out;
    to = Math.max(0, Math.min(out.length - 1, to));
    if (from === to) return out;
    const [item] = out.splice(from, 1);
    out.splice(to, 0, item);
    return out;
  }

  function setIndent(block, delta) {
    const out = normalizeBlock(block);
    const current = Number(out.props?.indent || 0);
    out.props = out.props || {};
    out.props.indent = Math.max(0, Math.min(MAX_INDENT, current + Number(delta || 0)));
    return out;
  }

  function blockFromLine(line) {
    const text = String(line ?? "");
    const patterns = [
      [/^###\s+(.*)$/, "heading3"],
      [/^##\s+(.*)$/, "heading2"],
      [/^#\s+(.*)$/, "heading1"],
      [/^[-*+]\s+\[x\]\s+(.*)$/i, "todo", { checked: true }],
      [/^[-*+]\s+\[\s\]\s+(.*)$/, "todo", { checked: false }],
      [/^[-*+]\s+(.*)$/, "bullet"],
      [/^\d+[.)]\s+(.*)$/, "numbered"],
      [/^>\s+(.*)$/, "quote"],
    ];
    for (const [pattern, type, props] of patterns) {
      const match = text.match(pattern);
      if (match) return createBlock(type, { content: match[1] || "", props: props || {} });
    }
    if (/^(---|\*\*\*)$/.test(text.trim())) return createBlock("divider");
    return createBlock("paragraph", { content: text });
  }

  function blocksFromPlainText(text) {
    const lines = String(text ?? "").replace(/\r\n?/g, "\n").split("\n");
    return (lines.length ? lines : [""]).map(blockFromLine);
  }

  function pushHistory(history, snapshot, limit = 100) {
    const current = history && typeof history === "object" ? history : { entries: [], index: -1 };
    const entries = Array.isArray(current.entries) ? current.entries.slice(0, Number(current.index) + 1) : [];
    const serialized = JSON.stringify(snapshot);
    if (entries.length && entries[entries.length - 1] === serialized) return { entries, index: entries.length - 1 };
    entries.push(serialized);
    while (entries.length > Math.max(2, Number(limit) || 100)) entries.shift();
    return { entries, index: entries.length - 1 };
  }

  function historyStep(history, delta) {
    const entries = Array.isArray(history?.entries) ? history.entries : [];
    if (!entries.length) return { history: { entries: [], index: -1 }, snapshot: null };
    const index = Math.max(0, Math.min(entries.length - 1, Number(history.index || 0) + Number(delta || 0)));
    return { history: { entries: entries.slice(), index }, snapshot: deepClone(JSON.parse(entries[index])) };
  }

  const EDITOR_HOST_CONTRACT = "code_assist.notion.editor_host.v1";
  const EDITOR_HOST_CAPABILITIES = Object.freeze([
    "document-snapshot",
    "lifecycle-events",
    "local-mutation-events",
    "composition-boundary",
    "selection-preservation",
    "remote-projection",
  ]);

  function createEditorHostAdapter(bindings = {}) {
    if (typeof bindings.readDocument !== "function") {
      throw new TypeError("editor host requires readDocument()");
    }
    if (typeof bindings.applyProjection !== "function") {
      throw new TypeError("editor host requires applyProjection()");
    }

    const listeners = new Set();
    let sequence = 0;
    let compositionDepth = 0;
    let compositionBlockId = "";
    let pendingProjection = null;
    let disposed = false;

    function documentSnapshot() {
      const value = bindings.readDocument();
      if (!value || typeof value !== "object") return null;
      return Object.freeze({
        page_id: String(value.page_id || ""),
        title: String(value.title || ""),
        blocks: deepClone(Array.isArray(value.blocks) ? value.blocks : []),
        can_edit: Boolean(value.can_edit),
        server_revision: value.server_revision ?? null,
        edit_revision: Number.isInteger(value.edit_revision) ? value.edit_revision : 0,
      });
    }

    function publish(type, detail = {}) {
      if (disposed) return null;
      const snapshot = documentSnapshot();
      const safeDetail = detail && typeof detail === "object" ? deepClone(detail) : {};
      const event = Object.freeze({
        contract: EDITOR_HOST_CONTRACT,
        type,
        sequence: ++sequence,
        page_id: String(safeDetail.page_id || snapshot?.page_id || ""),
        edit_revision: snapshot?.edit_revision ?? 0,
        composing: compositionDepth > 0,
        detail: safeDetail,
      });
      for (const listener of [...listeners]) {
        try {
          listener(event);
        } catch (error) {
          bindings.onListenerError?.(error);
        }
      }
      return event;
    }

    function normalizeProjection(value) {
      if (!value || typeof value !== "object") throw new TypeError("remote projection must be an object");
      if (!Array.isArray(value.blocks)) throw new TypeError("remote projection blocks must be an array");
      return {
        page_id: String(value.page_id || ""),
        title: String(value.title || ""),
        blocks: deepClone(value.blocks),
        server_revision: value.server_revision ?? null,
      };
    }

    function applyProjectionNow(projection, options = {}) {
      const current = documentSnapshot();
      if (!current || !projection.page_id || current.page_id !== projection.page_id) {
        publish("remote-projection-rejected", {
          page_id: projection.page_id,
          reason: current ? "page-mismatch" : "no-active-document",
        });
        return Object.freeze({ applied: false, deferred: false, reason: current ? "page-mismatch" : "no-active-document" });
      }
      const preserveSelection = options.preserve_selection !== false;
      const selection = preserveSelection && typeof bindings.captureSelection === "function"
        ? deepClone(bindings.captureSelection())
        : null;
      const result = bindings.applyProjection(deepClone(projection), Object.freeze({
        origin: String(options.origin || "collaboration"),
        preserve_selection: preserveSelection,
      })) || {};
      const applied = result.applied !== false;
      if (applied && selection && typeof bindings.restoreSelection === "function") {
        bindings.restoreSelection(deepClone(selection));
      }
      publish(applied ? "remote-projection-applied" : "remote-projection-rejected", {
        page_id: projection.page_id,
        reason: applied ? "" : String(result.reason || "binding-rejected"),
      });
      return Object.freeze({
        applied,
        deferred: false,
        reason: applied ? "" : String(result.reason || "binding-rejected"),
      });
    }

    const adapter = {
      contract: EDITOR_HOST_CONTRACT,
      capabilities: EDITOR_HOST_CAPABILITIES,
      readDocument: documentSnapshot,
      subscribe(listener) {
        if (typeof listener !== "function") throw new TypeError("editor host listener must be a function");
        if (disposed) throw new Error("editor host is disposed");
        listeners.add(listener);
        return () => listeners.delete(listener);
      },
      notifyLifecycle(type, detail = {}) {
        if (!["document-loaded", "document-unloaded"].includes(type)) {
          throw new TypeError("unsupported editor lifecycle event");
        }
        return publish(type, detail);
      },
      notifyLocalMutation(kind = "document", detail = {}) {
        return publish("local-mutation", {
          ...deepClone(detail && typeof detail === "object" ? detail : {}),
          kind: String(kind || "document"),
        });
      },
      beginComposition(blockId = "") {
        compositionDepth += 1;
        if (blockId) compositionBlockId = String(blockId);
        return publish("composition-start", { block_id: compositionBlockId });
      },
      endComposition(blockId = "") {
        if (compositionDepth > 0) compositionDepth -= 1;
        const endedBlockId = String(blockId || compositionBlockId || "");
        const event = publish("composition-end", { block_id: endedBlockId });
        if (compositionDepth === 0) {
          compositionBlockId = "";
          const queued = pendingProjection;
          pendingProjection = null;
          if (queued) applyProjectionNow(queued.projection, queued.options);
        }
        return event;
      },
      isComposing() {
        return compositionDepth > 0;
      },
      applyRemoteProjection(value, options = {}) {
        if (disposed) return Object.freeze({ applied: false, deferred: false, reason: "disposed" });
        const projection = normalizeProjection(value);
        const safeOptions = deepClone(options && typeof options === "object" ? options : {});
        if (compositionDepth > 0) {
          pendingProjection = { projection, options: safeOptions };
          publish("remote-projection-deferred", { page_id: projection.page_id, reason: "composition-active" });
          return Object.freeze({ applied: false, deferred: true, reason: "composition-active" });
        }
        return applyProjectionNow(projection, safeOptions);
      },
      captureSelection() {
        if (typeof bindings.captureSelection !== "function") return null;
        return deepClone(bindings.captureSelection());
      },
      restoreSelection(selection) {
        if (typeof bindings.restoreSelection !== "function") return false;
        return Boolean(bindings.restoreSelection(deepClone(selection)));
      },
      focusBlock(blockId, offset = 0) {
        if (typeof bindings.focusBlock !== "function") return false;
        return Boolean(bindings.focusBlock(String(blockId || ""), Number(offset) || 0));
      },
      dispose() {
        disposed = true;
        listeners.clear();
        pendingProjection = null;
        compositionDepth = 0;
        compositionBlockId = "";
      },
    };
    return Object.freeze(adapter);
  }

  return {
    MAX_INDENT,
    BLOCK_DEFINITIONS,
    BLOCK_BY_TYPE,
    TEXT_BLOCK_TYPES,
    LIST_BLOCK_TYPES,
    createBlock,
    normalizeBlock,
    isTextBlock,
    filterBlockDefinitions,
    markdownShortcut,
    splitBlock,
    mergeBlocks,
    duplicateBlocks,
    removeBlocks,
    moveBlock,
    setIndent,
    blockFromLine,
    blocksFromPlainText,
    pushHistory,
    historyStep,
    EDITOR_HOST_CONTRACT,
    EDITOR_HOST_CAPABILITIES,
    createEditorHostAdapter,
    deepClone,
  };
});
