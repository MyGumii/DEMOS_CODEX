/* Second Brain Contents + Review Inbox management. */

function _kbUid() {
  try {
    const u = JSON.parse(sessionStorage.getItem("demos_user") || "null");
    return u && u.id ? u.id : null;
  } catch {
    return null;
  }
}

function _kbUidQs(extra = {}) {
  const params = new URLSearchParams();
  const uid = _kbUid();
  if (uid) params.set("user_id", uid);
  Object.entries(extra).forEach(([k, v]) => {
    if (v !== undefined && v !== null && String(v) !== "") params.set(k, v);
  });
  const qs = params.toString();
  return qs ? "?" + qs : "";
}

function _withUid(body = {}) {
  const uid = _kbUid();
  return uid ? { ...body, user_id: uid } : body;
}

const Knowledge = {
  mode: "contents",
  inboxStatus: "pending",
  selectedCandidates: new Set(),
  visibleCandidateIds: [],
  bulkDrag: { active: false, mode: true },
  bulkDragBound: false,
  _sourceImportFiles: [],
  _currentCandidate: null,
  _currentRecommendation: null,

  async fetchThoughts(query = "") {
    const data = await api("api/code/knowledge/thought/list" + _kbUidQs());
    let items = data.items || [];
    if (query) {
      const q = query.toLowerCase();
      items = items.filter(it => Knowledge._thoughtHay(it).includes(q));
    }
    return { ...data, items, count: items.length };
  },

  async fetchInbox(query = "") {
    return await api("api/code/knowledge/inbox/list" + _kbUidQs({
      status: Knowledge.inboxStatus === "all" ? "" : Knowledge.inboxStatus,
      q: query || "",
    }));
  },

  async render(query = "") {
    if (currentTab !== "knowledge") return;
    if (window.KnowledgeCoreBrowser) await window.KnowledgeCoreBrowser.ready();
    if (window.KnowledgeCoreBrowser?.active) {
      await window.KnowledgeCoreBrowser.renderWikiNavigation();
      // Every visible Wiki is canonical, including the default and additional
      // personal Wiki directories.  Falling back to the legacy filesystem
      // endpoints here made a populated canonical Wiki look empty and detached
      // the list/index/log/graph surfaces from the selected conversation scope.
      await window.KnowledgeCoreBrowser.renderManagement(query);
      return;
    }
    if (Knowledge.mode === "inbox" && !["pending", "rejected", "applied", "deleted"].includes(Knowledge.inboxStatus)) {
      Knowledge.inboxStatus = "pending";
    }
    const body = $("#sidebarBody");
    body.innerHTML = '<div style="padding:14px;color:var(--muted);font-size:12px;">Second Brain 로딩 중...</div>';
    try {
      const [thoughtsData, inboxData] = await Promise.all([
        Knowledge.fetchThoughts(query),
        Knowledge.fetchInbox(query),
      ]);
      body.innerHTML = "";
      body.appendChild(Knowledge._topControls(thoughtsData.count, inboxData.counts || {}));
      if (Knowledge.mode === "inbox") {
        if (Knowledge.inboxStatus !== "deleted") {
          body.appendChild(Knowledge._inboxFilters());
        }
        const items = inboxData.items || [];
        Knowledge.visibleCandidateIds = items.map(it => it.id);
        Knowledge._pruneSelection();
        body.appendChild(Knowledge._bulkToolbar(items));
        items.forEach(it => body.appendChild(Knowledge._candidateRow(it)));
        if (!items.length) {
          const emptyText = Knowledge.inboxStatus === "deleted"
            ? "삭제된 지식 점검 항목이 없습니다."
            : "이 필터에 해당하는 지식 점검 항목이 없습니다.";
          body.appendChild(Knowledge._empty(emptyText));
        }
      } else {
        const items = thoughtsData.items || [];
        body.appendChild(Knowledge._systemDocsBlock());
        items.forEach(it => body.appendChild(Knowledge._thoughtRow(it)));
        if (!items.length) body.appendChild(Knowledge._empty("아직 LLM Wiki 문서가 없습니다. markdown/text 파일을 업로드하면 source markdown과 graph 관계가 생성됩니다."));
      }

      body.querySelectorAll("[data-system-doc]").forEach(btn => {
        btn.addEventListener("click", e => Knowledge.openSystemDoc(e.currentTarget.dataset.systemDoc || ""));
      });
      $("#kbModeContents")?.addEventListener("click", () => {
        Knowledge.mode = "contents";
        Knowledge.render($("#sidebarSearch").value || "");
      });
      $("#kbModeInbox")?.addEventListener("click", () => {
        Knowledge.mode = "inbox";
        if (Knowledge.inboxStatus === "deleted") Knowledge.inboxStatus = "pending";
        Knowledge.render($("#sidebarSearch").value || "");
      });
      $("#kbModeTrash")?.addEventListener("click", () => {
        Knowledge.mode = "inbox";
        Knowledge.inboxStatus = "deleted";
        Knowledge.render($("#sidebarSearch").value || "");
      });
      body.querySelectorAll("[data-kb-status]").forEach(btn => {
        btn.addEventListener("click", e => {
          Knowledge.inboxStatus = e.currentTarget.dataset.kbStatus || "pending";
          Knowledge.render($("#sidebarSearch").value || "");
        });
      });
      Knowledge._ensureBulkDragListeners();
      Knowledge._refreshBulkToolbar();
    } catch (e) {
      body.innerHTML = `<div style="padding:14px;color:var(--error);font-size:12px;">${escapeHtml(e.message)}</div>`;
    }
  },

  _topControls(contentCount, inboxCounts) {
    const pending = inboxCounts.pending || 0;
    const rejected = inboxCounts.rejected || inboxCounts.skipped || 0;
    const applied = inboxCounts.applied || 0;
    const deleted = inboxCounts.deleted || 0;
    const wrap = document.createElement("div");
    wrap.className = "sb-controls";
    wrap.innerHTML = `
      <div class="section-title" style="display:flex;align-items:center;gap:6px;">
        <span style="flex:1;">LLM Wiki</span>
      </div>
      <div class="sb-mode-tabs sb-mode-tabs-three">
        <button id="kbModeContents" class="${Knowledge.mode === "contents" ? "active" : ""}">문서 ${contentCount}</button>
        <button id="kbModeInbox" class="${Knowledge.mode === "inbox" && Knowledge.inboxStatus !== "deleted" ? "active" : ""}">지식 점검 ${pending}</button>
        <button id="kbModeTrash" class="${Knowledge.mode === "inbox" && Knowledge.inboxStatus === "deleted" ? "active" : ""}">삭제됨 ${deleted}</button>
      </div>
      <div class="sb-counts"></div>
    `;
    const countsEl = wrap.querySelector(".sb-counts");
    if (countsEl) countsEl.textContent = `점검 ${pending} / 적용 ${applied} / 제외 ${rejected} / 삭제됨 ${deleted}`;
    return wrap;
  },

  _systemDocsBlock() {
    const wrap = document.createElement("div");
    wrap.className = "sb-system-docs";
    wrap.innerHTML = `
      <div class="section-title">관리 문서</div>
      <div class="sb-system-doc-grid">
        <button type="button" class="sb-system-doc" data-system-doc="index" title="Index 문서 보기">
          <span class="sb-system-doc-icon">#</span>
          <span class="sb-system-doc-text">
            <span class="sb-system-doc-label">Index</span>
            <small>검색 맵</small>
          </span>
        </button>
        <button type="button" class="sb-system-doc" data-system-doc="log" title="Log 문서 보기">
          <span class="sb-system-doc-icon">≡</span>
          <span class="sb-system-doc-text">
            <span class="sb-system-doc-label">Log</span>
            <small>변경 기록</small>
          </span>
        </button>
        <button type="button" class="sb-system-doc" data-system-doc="graph" title="Graph 상세 보기">
          <span class="sb-system-doc-icon">⌘</span>
          <span class="sb-system-doc-text">
            <span class="sb-system-doc-label">Graph</span>
            <small>연결 상세</small>
          </span>
        </button>
      </div>
    `;
    return wrap;
  },

  async openSystemDoc(name) {
    if (!name) return;
    try {
      const doc = await api(`api/code/knowledge/system_doc/${encodeURIComponent(name)}${_kbUidQs()}`);
      const body = document.createElement("div");
      body.innerHTML = `
        <div class="sb-detail-meta">${escapeHtml(doc.filename || name)} · ${doc.updated_at ? new Date(Number(doc.updated_at) * 1000).toLocaleString("ko-KR") : "-"}</div>
        <pre class="sb-source-excerpt">${escapeHtml(doc.content || "")}</pre>
      `;
      Modal.open({
        title: doc.title || name,
        body,
        footButtons: [{ label: "닫기" }],
      });
    } catch (e) {
      toast("관리 문서 로드 실패: " + e.message, "error");
    }
  },

  _inboxFilters() {
    const statuses = [
      ["pending", "대기"],
      ["applied", "적용됨"],
      ["rejected", "제외됨"],
    ];
    const row = document.createElement("div");
    row.className = "sb-filter-row sb-status-tabs";
    row.innerHTML = statuses.map(([value, label]) => `
      <button type="button" data-kb-status="${value}" class="${Knowledge.inboxStatus === value ? "active" : ""}">
        ${label}
      </button>
    `).join("");
    return row;
  },

  _bulkToolbar(items = []) {
    const visibleCount = items.length;
    const status = Knowledge.inboxStatus;
    const selectedCount = Knowledge._selectedVisibleIds().length;
    const wrap = document.createElement("div");
    wrap.className = "sb-bulk-toolbar";

    let actionButtons = "";
    if (status === "deleted") {
      actionButtons = `
        <button type="button" data-kb-bulk="restore" data-kb-scope="selected">선택 복구</button>
        <button type="button" data-kb-bulk="purge" data-kb-scope="selected" class="danger">선택 영구 삭제</button>
      `;
    } else if (status === "rejected" || status === "applied") {
      actionButtons = `
        <button type="button" data-kb-bulk="restore" data-kb-scope="selected">선택 복구</button>
        <button type="button" data-kb-bulk="delete" data-kb-scope="selected" class="danger">선택 삭제</button>
        <button type="button" data-kb-bulk="restore" data-kb-scope="visible">전체 복구</button>
      `;
    } else {
      actionButtons = `
        <button type="button" data-kb-bulk="approve" data-kb-scope="selected" class="primary">선택 점검 완료</button>
        <button type="button" data-kb-bulk="skip" data-kb-scope="selected">선택 제외</button>
        <button type="button" data-kb-bulk="delete" data-kb-scope="selected" class="danger">선택 삭제</button>
        <button type="button" data-kb-bulk="approve" data-kb-scope="visible">전체 점검 완료</button>
        <button type="button" data-kb-bulk="skip" data-kb-scope="visible">전체 제외</button>
        <button type="button" data-kb-bulk="delete" data-kb-scope="visible" class="danger">전체 삭제</button>
      `;
    }

    wrap.innerHTML = `
      <div class="sb-bulk-row">
        <span class="sb-bulk-count">선택 ${selectedCount} / ${visibleCount}</span>
        <button type="button" data-kb-select-visible>전체 선택</button>
        <button type="button" data-kb-clear-selection>해제</button>
      </div>
      <div class="sb-bulk-row sb-bulk-actions">${actionButtons}</div>
      <div class="sb-bulk-hint">지식 점검은 source markdown의 품질, lint, out-of-date 같은 유지보수 항목입니다. graph 후보는 여기에 표시하지 않습니다.</div>
    `;

    wrap.querySelector("[data-kb-select-visible]")?.addEventListener("click", () => {
      Knowledge.selectVisibleCandidates(true);
    });
    wrap.querySelector("[data-kb-clear-selection]")?.addEventListener("click", () => {
      Knowledge.clearCandidateSelection();
    });
    wrap.querySelectorAll("[data-kb-bulk]").forEach(btn => {
      btn.addEventListener("click", () => {
        Knowledge.bulkCandidateAction(btn.dataset.kbBulk, btn.dataset.kbScope || "selected");
      });
    });
    return wrap;
  },

  _selectedVisibleIds() {
    const visible = new Set(Knowledge.visibleCandidateIds || []);
    return [...Knowledge.selectedCandidates].filter(id => visible.has(id));
  },

  _pruneSelection() {
    const visible = new Set(Knowledge.visibleCandidateIds || []);
    Knowledge.selectedCandidates = new Set([...Knowledge.selectedCandidates].filter(id => visible.has(id)));
  },

  _ensureBulkDragListeners() {
    if (Knowledge.bulkDragBound) return;
    Knowledge.bulkDragBound = true;
    document.addEventListener("mouseup", () => {
      Knowledge.bulkDrag.active = false;
    });
  },

  _refreshBulkToolbar() {
    const selectedCount = Knowledge._selectedVisibleIds().length;
    const visibleCount = Knowledge.visibleCandidateIds.length;
    const count = document.querySelector(".sb-bulk-count");
    if (count) count.textContent = `선택 ${selectedCount} / ${visibleCount}`;
    document.querySelectorAll("[data-kb-bulk][data-kb-scope='selected']").forEach(btn => {
      btn.disabled = selectedCount === 0;
    });
    document.querySelectorAll("[data-kb-bulk][data-kb-scope='visible'], [data-kb-select-visible]").forEach(btn => {
      btn.disabled = visibleCount === 0;
    });
    document.querySelectorAll("[data-kb-clear-selection]").forEach(btn => {
      btn.disabled = selectedCount === 0;
    });
  },

  setCandidateSelected(id, selected) {
    if (!id) return;
    if (selected) Knowledge.selectedCandidates.add(id);
    else Knowledge.selectedCandidates.delete(id);
    const row = document.querySelector(`[data-candidate-id="${_kbCssEscape(id)}"]`);
    if (row) {
      row.classList.toggle("sb-selected", selected);
      const handle = row.querySelector(".sb-select-handle");
      if (handle) {
        handle.setAttribute("aria-pressed", selected ? "true" : "false");
        handle.textContent = selected ? "✓" : "";
      }
    }
    Knowledge._refreshBulkToolbar();
  },

  selectVisibleCandidates(selected = true) {
    (Knowledge.visibleCandidateIds || []).forEach(id => Knowledge.setCandidateSelected(id, selected));
  },

  clearCandidateSelection() {
    [...Knowledge.selectedCandidates].forEach(id => Knowledge.setCandidateSelected(id, false));
    Knowledge.selectedCandidates.clear();
    Knowledge._refreshBulkToolbar();
  },

  async bulkCandidateAction(action, scope = "selected") {
    const ids = scope === "visible" ? [...Knowledge.visibleCandidateIds] : Knowledge._selectedVisibleIds();
    if (!ids.length) {
      toast("처리할 지식 점검 항목이 없습니다.", "error");
      return false;
    }
    const actionLabel = _kbActionLabel(action);
    const scopeText = scope === "visible" ? "보이는 전체" : "선택한";
    const destructive = action === "delete" || action === "purge";
    const message = `"${scopeText}" 지식 점검 항목 ${ids.length}개를 ${actionLabel} 처리할까요?`
      + (destructive ? "\n삭제함 이동 또는 영구 삭제는 되돌리기 범위가 다릅니다." : "");
    if (!confirm(message)) return false;
    const body = _withUid({
      ids,
      action,
      skip_reason: action === "skip" ? "bulk action" : "",
    });
    try {
      const result = await api("api/code/knowledge/inbox/batch_action", {
        method: "POST",
        body,
      });
      Knowledge.clearCandidateSelection();
      Knowledge.render($("#sidebarSearch").value || "");
      if (window.SecondBrainGraph) {
        window.SecondBrainGraph.data = null;
      }
      const msg = `${actionLabel} 완료: ${result.succeeded || 0}개`
        + (result.failed ? ` / 실패 ${result.failed}개` : "");
      toast(msg, result.failed ? "error" : "ok");
      if (result.failed && result.errors?.length) {
        console.warn("Second Brain batch action errors", result.errors);
      }
    } catch (e) {
      toast(`${actionLabel} 실패: ${e.message}`, "error");
      return false;
    }
  },

  _empty(text) {
    const el = document.createElement("div");
    el.style.cssText = "padding:12px;color:var(--muted);font-size:12px;line-height:1.5;";
    el.textContent = text;
    return el;
  },

  _thoughtHay(it) {
    return [
      it.id,
      it.title,
      it.memory_type,
      it.domain,
      it.service,
      ...(it.topics || []),
      it.content_preview,
    ].join(" ").toLowerCase();
  },

  _thoughtRow(it) {
    const row = document.createElement("div");
    row.className = "item";
    row.innerHTML = `
      <span class="name" title="${escapeAttr(it.title || it.id)}">${escapeHtml(it.title || it.id)}</span>
      <span class="badge">${escapeHtml(_kbMemoryLabel(it.memory_type || "thought"))}</span>
    `;
    row.addEventListener("click", () => Knowledge.openThought(it.id));
    return row;
  },

  _candidateRow(it) {
    const row = document.createElement("div");
    const selected = Knowledge.selectedCandidates.has(it.id);
    row.className = "item sb-candidate" + (selected ? " sb-selected" : "");
    row.dataset.candidateId = it.id;
    const targetText = it.target_filename || it.target_id || "새 Wiki page";
    const actionText = it.candidate_kind === "source_lint" ? "lint" : "점검";
    row.innerHTML = `
      <button type="button" class="sb-select-handle" aria-label="변경안 선택" aria-pressed="${selected ? "true" : "false"}">${selected ? "✓" : ""}</button>
      <span class="name" title="${escapeAttr(it.title || it.id)}">${escapeHtml(it.title || it.id)}</span>
      <span class="badge">${escapeHtml(_kbStatusLabel(it.status || "pending"))}</span>
      <span class="badge">${escapeHtml(targetText)}</span>
      ${actionText ? `<span class="badge">${escapeHtml(actionText)}</span>` : ""}
    `;
    const handle = row.querySelector(".sb-select-handle");
    handle.addEventListener("mousedown", e => {
      if (e.button !== 0) return;
      e.preventDefault();
      e.stopPropagation();
      const next = !Knowledge.selectedCandidates.has(it.id);
      Knowledge.bulkDrag = { active: true, mode: next };
      Knowledge.setCandidateSelected(it.id, next);
    });
    handle.addEventListener("click", e => {
      e.preventDefault();
      e.stopPropagation();
    });
    row.addEventListener("mouseenter", () => {
      if (!Knowledge.bulkDrag.active) return;
      Knowledge.setCandidateSelected(it.id, Knowledge.bulkDrag.mode);
    });
    row.addEventListener("click", e => {
      if (e.target.closest(".sb-select-handle")) return;
      Knowledge.openCandidate(it.id);
    });
    return row;
  },

  _setSourceImportFiles(files) {
    Knowledge._sourceImportFiles = Array.from(files || []).filter(Boolean);
    const input = $("#sbSourceFile");
    if (input && window.DataTransfer) {
      try {
        const dt = new DataTransfer();
        Knowledge._sourceImportFiles.forEach(file => dt.items.add(file));
        input.files = dt.files;
      } catch {}
    }
    Knowledge._updateSourceDropLabel();
  },

  _updateSourceDropLabel() {
    const files = Knowledge._sourceImportFiles || [];
    const picked = $("#sbSourcePicked");
    const drop = $("#sbSourceDrop");
    if (!picked || !drop) return;
    if (!files.length) {
      picked.textContent = "선택된 파일 없음";
      drop.classList.remove("has-file");
      return;
    }
    drop.classList.add("has-file");
    const names = files.slice(0, 4).map(f => f.name).join(", ");
    picked.textContent = files.length > 4 ? `${names} 외 ${files.length - 4}개` : names;
  },

  _bindSourceDrop() {
    const drop = $("#sbSourceDrop");
    const input = $("#sbSourceFile");
    if (!drop || !input) return;
    input.addEventListener("change", () => Knowledge._setSourceImportFiles(input.files));
    drop.addEventListener("click", () => input.click());
    drop.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        input.click();
      }
    });
    ["dragenter", "dragover"].forEach(type => {
      drop.addEventListener(type, e => {
        e.preventDefault();
        e.stopPropagation();
        drop.classList.add("drag-over");
      });
    });
    ["dragleave", "drop"].forEach(type => {
      drop.addEventListener(type, e => {
        e.preventDefault();
        e.stopPropagation();
        if (type === "drop") {
          const files = Array.from(e.dataTransfer?.files || []);
          if (files.length) Knowledge._setSourceImportFiles(files);
        }
        drop.classList.remove("drag-over");
      });
    });
  },

  async _importSourceFiles(files) {
    let imported = 0;
    let failed = 0;
    let graphEdges = 0;
    let diffCount = 0;
    let converted = 0;
    let pending = 0;
    let assets = 0;
    files = Array.from(files || []).filter(Boolean);
    if (files.length) {
      return await Knowledge._importSourceFilesBatch(files);
    }
    for (let idx = 0; idx < files.length; idx++) {
      const file = files[idx];
      const fd = new FormData();
      fd.append("file", file);
      const uid = _kbUid();
      if (uid) fd.append("user_id", uid);
      fd.append("domain", "");
      fd.append("service", "");
      fd.append("auto_triage", "1");
      fd.append("async", "1");
      try {
        Knowledge._setImportProgress({
          fileIndex: idx,
          fileCount: files.length,
          fileName: file.name,
          progress: 0,
          message: "업로드 시작",
        });
        const r = await authenticatedFetch("api/code/knowledge/source/import", { method: "POST", body: fd });
        const j = await r.json();
        if (!r.ok) {
          throw new Error(j.error || `HTTP ${r.status}`);
        }
        const result = j.job_id
          ? await Knowledge._pollImportJob(j.job_id, { fileIndex: idx, fileCount: files.length, fileName: file.name })
          : j;
        imported++;
        graphEdges += Number(result.graph_edges_added || 0);
        diffCount += Number(result.candidate_count || 0);
        if (result.converted_to_wiki) converted++;
        assets += Number(result.asset_count || result.image_count || 0);
        const triage = result.auto_triage || {};
        pending += Number(triage.pending || result.pending_candidate_count || 0);
        Knowledge._setImportProgress({
          fileIndex: idx,
          fileCount: files.length,
          fileName: file.name,
          progress: 100,
          message: "완료",
        });
      } catch (e) {
        failed++;
        console.warn("[second-brain] source import failed", file.name, e);
        Knowledge._setImportProgress({
          fileIndex: idx,
          fileCount: files.length,
          fileName: file.name,
          progress: 100,
          message: "실패: " + e.message,
          error: true,
        });
      }
    }
    if (!imported) {
      throw new Error(`${failed}개 파일 업로드 실패`);
    }
    return { imported, failed, graphEdges, diffCount, converted, pending, assets };
  },

  async _importSourceFilesBatch(files) {
    files = Array.from(files || []).filter(Boolean);
    const fd = new FormData();
    files.forEach(file => fd.append("files", file));
    const uid = _kbUid();
    if (uid) fd.append("user_id", uid);
    fd.append("domain", "");
    fd.append("service", "");
    fd.append("auto_triage", "1");
    fd.append("async", "1");
    Knowledge._setImportProgress({
      fileIndex: 0,
      fileCount: 1,
      fileName: `${files.length} files`,
      progress: 0,
      message: "batch upload queued",
    });
    const r = await authenticatedFetch("api/code/knowledge/source/import_batch", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
    const result = j.job_id
      ? await Knowledge._pollImportJob(j.job_id, { fileIndex: 0, fileCount: 1, fileName: `${files.length} files` })
      : j;
    const items = result.items || [];
    const imported = Number(result.imported ?? items.filter(it => it.status === "imported").length);
    const failed = Number(result.failed ?? items.filter(it => it.status === "failed").length);
    if (!imported) {
      const firstError = items.find(it => it.error)?.error || `${failed || files.length} files failed`;
      throw new Error(firstError);
    }
    Knowledge._setImportProgress({
      fileIndex: 0,
      fileCount: 1,
      fileName: `${files.length} files`,
      progress: 100,
      message: failed ? `done with ${failed} failed` : "done",
      error: false,
    });
    return {
      imported,
      failed,
      graphEdges: Number(result.graph_edges_added || 0),
      diffCount: Number(result.candidate_count || 0),
      converted: Number(result.converted_count || items.filter(it => it.converted_to_wiki).length),
      pending: Number(result.pending_candidate_count || result.auto_triage?.pending || 0),
      assets: Number(result.asset_count || items.reduce((sum, it) => sum + Number(it.asset_count || it.image_count || 0), 0)),
    };
  },

  async _pollImportJob(jobId, { fileIndex = 0, fileCount = 1, fileName = "" } = {}) {
    while (true) {
      const job = await api(`api/code/jobs/${encodeURIComponent(jobId)}`);
      Knowledge._setImportProgress({
        fileIndex,
        fileCount,
        fileName,
        progress: Number(job.progress || 0),
        message: job.message || job.status || "처리 중",
      });
      if (job.status === "done") return job.result || {};
      if (job.status === "failed") throw new Error(job.error || job.message || "지식 처리 실패");
      if (job.status === "cancelled") throw new Error(job.message || "지식 처리가 취소되었습니다");
      await new Promise(resolve => setTimeout(resolve, 800));
    }
  },

  _setImportProgress({ fileIndex = 0, fileCount = 1, fileName = "", progress = 0, message = "", error = false } = {}) {
    const wrap = $("#sbImportProgress");
    if (!wrap) return;
    wrap.hidden = false;
    const safeCount = Math.max(1, Number(fileCount || 1));
    const filePct = Math.max(0, Math.min(100, Number(progress || 0)));
    const totalPct = Math.round(((Number(fileIndex || 0) + filePct / 100) / safeCount) * 100);
    const bar = $("#sbImportProgressBar");
    const text = $("#sbImportProgressText");
    const detail = $("#sbImportProgressDetail");
    if (bar) {
      bar.style.width = `${Math.max(0, Math.min(100, totalPct))}%`;
      bar.classList.toggle("error", !!error);
    }
    if (text) text.textContent = `처리 중 ${Math.min(fileIndex + 1, safeCount)} / ${safeCount} · ${totalPct}%`;
    if (detail) detail.textContent = `${fileName || "source"} · ${message || "처리 중"}`;
  },

  _openCanonicalAddDialog(initialTab = "upload") {
    const activeTab = initialTab === "chat" ? "chat" : "upload";
    const scopeState = window.KnowledgeCoreBrowser?.state?.() || null;
    const spaces = Array.isArray(scopeState?.spaces) ? scopeState.spaces : [];
    const currentSpace = spaces.find(space => space.id === scopeState?.selectedSpaceId) || null;
    const currentPart = spaces.find(space => space.kind === "part") || null;
    const higherTarget = currentSpace?.native_authority?.publication_target || null;
    const personalWikiState = window.KnowledgeCoreBrowser?.personalWikiState?.() || null;
    const personalWikis = Array.isArray(personalWikiState?.activeItems)
      ? personalWikiState.activeItems : [];
    const defaultPersonal = personalWikis.find(wiki => wiki.is_default) || null;
    let selectedPersonalSpaceId = (
      currentSpace?.kind === "personal"
      && personalWikis.some(wiki => wiki.space_id === currentSpace.id)
    ) ? currentSpace.id : defaultPersonal?.space_id || null;
    let selectedTarget = higherTarget
      ? "explicit"
      : currentPart && currentSpace?.kind !== "personal" ? "part" : "personal";
    let selectedSource = activeTab;

    const body = document.createElement("div");
    body.className = "knowledge-add-dialog";

    const sourceTabs = document.createElement("div");
    sourceTabs.className = "knowledge-add-source-tabs";
    sourceTabs.setAttribute("role", "tablist");
    sourceTabs.setAttribute("aria-label", "지식 추가 방식");

    const panels = document.createElement("div");
    panels.className = "knowledge-add-panels";
    const sourceButtons = new Map();
    const sourcePanels = new Map();

    const makeSourceTab = (mode, label) => {
      const button = document.createElement("button");
      button.type = "button";
      button.id = `knowledgeAdd${mode === "upload" ? "Upload" : "Chat"}Tab`;
      button.className = "knowledge-add-source-tab";
      button.textContent = label;
      button.setAttribute("role", "tab");
      button.setAttribute("aria-controls", `knowledgeAdd${mode === "upload" ? "Upload" : "Chat"}Panel`);
      sourceTabs.appendChild(button);
      sourceButtons.set(mode, button);
      return button;
    };

    const uploadTab = makeSourceTab("upload", "자료 업로드");
    const chatTab = makeSourceTab("chat", "대화 지식화");

    const uploadPanel = document.createElement("section");
    uploadPanel.id = "knowledgeAddUploadPanel";
    uploadPanel.className = "knowledge-add-panel";
    uploadPanel.setAttribute("role", "tabpanel");
    uploadPanel.setAttribute("aria-labelledby", uploadTab.id);
    const uploadTitle = document.createElement("h3");
    uploadTitle.textContent = "문서를 지식으로 발행";
    const uploadDescription = document.createElement("p");
    uploadDescription.className = "knowledge-add-description";
    uploadDescription.textContent = "Markdown·PDF·DOCX·PPTX·XLSX·텍스트·코드를 미리보기로 정규화한 뒤 선택한 공간에 발행합니다.";
    const fileInput = document.createElement("input");
    fileInput.id = "canonicalPublicationFile";
    fileInput.type = "file";
    fileInput.multiple = true;
    fileInput.hidden = true;
    fileInput.accept = ".docx,.pptx,.xlsx,.pdf,.md,.markdown,.txt,.text,.rst,.adoc,.json,.jsonl,.yaml,.yml,.toml,.xml,.html,.htm,.css,.scss,.sass,.less,.csv,.tsv,.sql,.log,.ipynb,.py,.js,.ts,.tsx,.jsx,.vue,.svelte,.java,.kt,.c,.cc,.cpp,.cxx,.h,.hpp,.cs,.go,.rs,.rb,.php,.sh,.bash,.zsh,.ps1,.bat,.r,.m,.scala,.swift,.lua,.pl";
    const fileDrop = document.createElement("div");
    fileDrop.id = "canonicalPublicationDrop";
    fileDrop.className = "knowledge-add-file-drop";
    fileDrop.tabIndex = 0;
    fileDrop.setAttribute("role", "button");
    fileDrop.setAttribute("aria-controls", fileInput.id);
    const fileDropTitle = document.createElement("strong");
    fileDropTitle.textContent = "파일을 여기에 드래그하거나 클릭해서 선택";
    const fileDropHint = document.createElement("span");
    fileDropHint.textContent = "여러 파일을 한 번에 선택할 수 있으며 파일별로 독립 처리됩니다.";
    const fileSummary = document.createElement("ol");
    fileSummary.id = "canonicalPublicationFiles";
    fileSummary.className = "knowledge-add-file-list";
    fileSummary.setAttribute("aria-live", "polite");
    let selectedFiles = [];
    const renderFiles = () => {
      fileSummary.replaceChildren();
      fileDrop.classList.toggle("has-files", selectedFiles.length > 0);
      fileDropHint.textContent = selectedFiles.length
        ? `${selectedFiles.length}개 선택 · 최대 3개씩 병렬로 미리보기/게시`
        : "여러 파일을 한 번에 선택할 수 있으며 파일별로 독립 처리됩니다.";
      for (const file of selectedFiles) {
        const row = document.createElement("li");
        const size = Number.isFinite(file.size) ? ` · ${(file.size / 1024).toFixed(file.size < 10240 ? 1 : 0)} KB` : "";
        row.textContent = `${file.name}${size}`;
        fileSummary.appendChild(row);
      }
    };
    const selectFiles = files => {
      selectedFiles = Array.from(files || []).filter(file => file && typeof file.name === "string");
      renderFiles();
    };
    fileInput.addEventListener("change", () => selectFiles(fileInput.files));
    fileDrop.addEventListener("click", () => fileInput.click());
    fileDrop.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        fileInput.click();
      }
    });
    for (const eventName of ["dragenter", "dragover"]) {
      fileDrop.addEventListener(eventName, event => {
        event.preventDefault();
        event.stopPropagation();
        if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
        fileDrop.classList.add("drag-over");
      });
    }
    for (const eventName of ["dragleave", "drop"]) {
      fileDrop.addEventListener(eventName, event => {
        event.preventDefault();
        event.stopPropagation();
        fileDrop.classList.remove("drag-over");
        if (eventName === "drop") selectFiles(event.dataTransfer?.files || []);
      });
    }
    const uploadNote = document.createElement("p");
    uploadNote.className = "knowledge-add-note";
    uploadNote.textContent = "원본은 보존되고 각 파일은 별도 Markdown·지식 항목으로 게시됩니다. 한 파일이 실패해도 다른 파일은 계속 처리되며, 실패 항목만 다시 시도할 수 있습니다.";
    uploadPanel.append(uploadTitle, uploadDescription, fileDrop, fileInput, fileSummary, uploadNote);
    sourcePanels.set("upload", uploadPanel);

    const chatPanel = document.createElement("section");
    chatPanel.id = "knowledgeAddChatPanel";
    chatPanel.className = "knowledge-add-panel";
    chatPanel.setAttribute("role", "tabpanel");
    chatPanel.setAttribute("aria-labelledby", chatTab.id);
    const chatTitle = document.createElement("h3");
    chatTitle.textContent = "현재 대화를 지식으로 발행";
    const chatDescription = document.createElement("p");
    chatDescription.className = "knowledge-add-description";
    const messageCount = (State.messages || []).filter(message => message && String(message.content || "").trim()).length;
    chatDescription.textContent = messageCount
      ? `현재 대화의 메시지 ${messageCount}개를 검토 가능한 지식 미리보기로 만듭니다.`
      : "현재 대화에 발행할 메시지가 있는지 확인한 뒤 지식 미리보기를 만듭니다.";
    const chatNote = document.createElement("p");
    chatNote.className = "knowledge-add-note";
    chatNote.textContent = "대화 원문을 바로 쓰지 않고, 현재 대화 범위를 고정한 immutable 미리보기를 먼저 확인합니다.";
    chatPanel.append(chatTitle, chatDescription, chatNote);
    sourcePanels.set("chat", chatPanel);
    panels.append(uploadPanel, chatPanel);

    const targetSection = document.createElement("section");
    targetSection.className = "knowledge-add-target-section";
    const targetTitle = document.createElement("h3");
    targetTitle.textContent = "게시 위치";
    const personalWikiLabel = document.createElement("label");
    personalWikiLabel.className = "knowledge-add-personal-wiki-label";
    personalWikiLabel.textContent = "개인 Wiki";
    const personalWikiSelect = document.createElement("select");
    personalWikiSelect.id = "knowledgeAddPersonalWiki";
    personalWikiSelect.setAttribute("aria-label", "게시할 개인 Wiki 선택");
    for (const wiki of personalWikis) {
      const option = document.createElement("option");
      option.value = wiki.space_id;
      option.textContent = `${wiki.display_name}${wiki.is_default ? " · 기본" : ""}`;
      option.selected = wiki.space_id === selectedPersonalSpaceId;
      personalWikiSelect.appendChild(option);
    }
    personalWikiSelect.disabled = !personalWikis.length;
    personalWikiSelect.addEventListener("change", () => {
      selectedPersonalSpaceId = personalWikiSelect.value || null;
    });
    personalWikiLabel.appendChild(personalWikiSelect);
    const targetDescription = document.createElement("p");
    targetDescription.className = "knowledge-add-description";
    targetDescription.textContent = "개인과 Part에 모두 발행하면 서로 독립된 두 지식이 만들어지고 이후 자동 동기화되지 않습니다.";
    const targetTabs = document.createElement("div");
    targetTabs.className = "knowledge-add-target-tabs";
    targetTabs.setAttribute("role", "tablist");
    targetTabs.setAttribute("aria-label", "게시 위치 선택");
    const targetButtons = new Map();
    const targetLabels = {
      personal: "개인",
      part: currentPart ? `Part · ${currentPart.name}` : "Part",
      both: "개인 + Part",
    };
    targetLabels.explicit = `${currentSpace?.kind === "responsibility" ? "담당" : "팀"} · ${currentSpace?.name || "상위 지식"}`;
    for (const mode of ["personal", "part", "both", "explicit"]) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "knowledge-add-target-tab";
      button.dataset.targetMode = mode;
      button.textContent = targetLabels[mode];
      button.setAttribute("role", "tab");
      button.disabled = mode === "explicit" ? !higherTarget : mode !== "personal" && !currentPart;
      button.addEventListener("click", () => {
        if (button.disabled) return;
        selectedTarget = mode;
        for (const [candidateMode, candidate] of targetButtons) {
          const selected = candidateMode === selectedTarget;
          candidate.classList.toggle("active", selected);
          candidate.setAttribute("aria-selected", selected ? "true" : "false");
          candidate.tabIndex = selected ? 0 : -1;
        }
      });
      targetTabs.appendChild(button);
      targetButtons.set(mode, button);
    }
    targetSection.append(targetTitle, personalWikiLabel, targetTabs, targetDescription);

    const selectSource = mode => {
      selectedSource = mode;
      for (const [candidateMode, button] of sourceButtons) {
        const selected = candidateMode === mode;
        button.classList.toggle("active", selected);
        button.setAttribute("aria-selected", selected ? "true" : "false");
        button.tabIndex = selected ? 0 : -1;
      }
      for (const [candidateMode, panel] of sourcePanels) panel.hidden = candidateMode !== mode;
    };
    uploadTab.addEventListener("click", () => selectSource("upload"));
    chatTab.addEventListener("click", () => selectSource("chat"));

    body.append(sourceTabs, panels, targetSection);
    selectSource(selectedSource);
    targetButtons.get(selectedTarget)?.click();

    Modal.open({
      title: "지식에 추가",
      body,
      footButtons: [
        { label: "취소" },
        {
          label: "다음: 미리보기",
          primary: true,
          onClick: async () => {
            try {
              if (selectedSource === "upload") {
                const files = selectedFiles.length ? selectedFiles.slice() : Array.from(fileInput.files || []);
                if (!files.length) {
                  toast("게시할 파일을 하나 이상 선택하세요.", "error");
                  return false;
                }
                const personalWikiDisplayName = personalWikis.find(wiki => wiki.space_id === selectedPersonalSpaceId)?.display_name || null;
                const requests = files.map(file => ({
                  file,
                  personalSpaceId: selectedPersonalSpaceId,
                  personalWikiDisplayName,
                  preferredTargetMode: selectedTarget,
                  sourceType: "upload",
                  target: selectedTarget === "explicit" ? higherTarget : null,
                  title: file.name,
                }));
                if (requests.length === 1) await window.KnowledgeCoreBrowser.publishSource(requests[0]);
                else await window.KnowledgeCoreBrowser.publishSources(requests);
              } else {
                await window.KnowledgeCoreBrowser.publishSource({
                  conversationId: State.session_id,
                  personalSpaceId: selectedPersonalSpaceId,
                  personalWikiDisplayName: personalWikis.find(wiki => wiki.space_id === selectedPersonalSpaceId)?.display_name || null,
                  preferredTargetMode: selectedTarget,
                  sourceType: "chat",
                  target: selectedTarget === "explicit" ? higherTarget : null,
                });
              }
            } catch (error) {
              toast(`${selectedSource === "upload" ? "자료 업로드" : "대화 지식화"} 실패: ${error.message}`, "error");
              return false;
            }
            return false;
          },
        },
      ],
    });
  },

  openAddMenu({ initialTab = "upload" } = {}) {
    if (window.KnowledgeCoreBrowser?.active && window.KnowledgeCoreBrowser.publicationAvailability === "error") {
      toast("지식 게시 기능의 사용 가능 여부를 확인하지 못했습니다.", "error");
      return;
    }
    if (window.KnowledgeCoreBrowser?.active && window.KnowledgeCoreBrowser.publicationAvailability === "available") {
      Knowledge._openCanonicalAddDialog(initialTab);
      return;
    }
    if (initialTab === "chat") Knowledge.openPromoteChat();
    else Knowledge.openImportSource();
  },

  openPromoteChat() {
    if (window.KnowledgeCoreBrowser?.active && window.KnowledgeCoreBrowser.publicationAvailability === "error") {
      toast("지식 게시 기능의 사용 가능 여부를 확인하지 못했습니다.", "error");
      return;
    }
    if (window.KnowledgeCoreBrowser?.active && window.KnowledgeCoreBrowser.publicationAvailability === "available") {
      Knowledge._openCanonicalAddDialog("chat");
      return;
    }
    const messages = (State.messages || []).filter(m => m && String(m.content || "").trim());
    if (!messages.length) {
      toast("지식화할 현재 대화가 없습니다.", "error");
      return;
    }
    const firstUser = messages.find(m => m.role === "user");
    const defaultTitle = String(firstUser?.content || "Conversation Knowledge").replace(/\s+/g, " ").slice(0, 80);
    const body = document.createElement("div");
    body.innerHTML = `
      <label>제목</label>
      <input id="sbChatPromoteTitle" type="text" value="${escapeHtml(defaultTitle)}" placeholder="예: Graph Attention Network 정리">
      <p style="color:var(--muted);font-size:12px;line-height:1.6;">
        현재 대화에서 재사용 가능한 정의, 수식, 절차, 결정, 주의점만 source markdown 후보로 정리합니다.
        생성된 내용은 source markdown으로 저장되고, source 품질/lint 문제가 있을 때만 지식 점검에 남습니다.
      </p>
    `;
    Modal.open({
      title: "대화 지식화",
      body,
      footButtons: [
        { label: "취소" },
        {
          label: "후보 생성",
          primary: true,
          onClick: async () => {
            try {
              const payload = _withUid({
                session_id: State.session_id || "",
                title: $("#sbChatPromoteTitle").value.trim() || defaultTitle,
                domain: "",
                service: "",
                messages: Chat?.cloneMessages ? Chat.cloneMessages(messages) : messages,
              });
              const r = await authenticatedFetch("api/code/knowledge/chat/promote", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
              });
              const j = await r.json();
              if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
              toast(`대화 source 생성: graph edge ${j.graph_edges_added || 0}개 / 점검 ${j.candidate_count || 0}개`, "ok");
              Knowledge.mode = "inbox";
              Knowledge.inboxStatus = "pending";
              Knowledge.render($("#sidebarSearch").value || "");
              if (window.SecondBrainGraph) window.SecondBrainGraph.data = null;
            } catch (e) {
              toast("대화 지식화 실패: " + e.message, "error");
              return false;
            }
          },
        },
      ],
    });
  },

  openImportSource() {
    if (window.KnowledgeCoreBrowser?.active && window.KnowledgeCoreBrowser.publicationAvailability === "error") {
      toast("지식 게시 기능의 사용 가능 여부를 확인하지 못했습니다.", "error");
      return;
    }
    if (window.KnowledgeCoreBrowser?.active && window.KnowledgeCoreBrowser.publicationAvailability === "available") {
      Knowledge._openCanonicalAddDialog("upload");
      return;
    }
    const body = document.createElement("div");
    body.innerHTML = `
      <label>지식 source 파일</label>
      <input id="sbSourceFile" type="file" multiple accept=".ppt,.pptx,.doc,.docx,.xls,.xlsx,.pdf,.md,.markdown,.txt,.text,.rst,.adoc,.json,.jsonl,.yaml,.yml,.toml,.xml,.html,.htm,.css,.scss,.sass,.less,.csv,.tsv,.sql,.log,.ipynb,.py,.js,.ts,.tsx,.jsx,.vue,.svelte,.java,.kt,.c,.cc,.cpp,.cxx,.h,.hpp,.cs,.go,.rs,.rb,.php,.sh,.bash,.zsh,.ps1,.bat,.r,.m,.scala,.swift,.lua,.pl">
      <div id="sbSourceDrop" class="sb-source-drop" tabindex="0">
        <div class="sb-source-drop-icon">＋</div>
        <div class="sb-source-drop-title">파일을 여기에 드래그하거나 클릭해서 선택</div>
        <div class="sb-source-drop-sub">md는 그대로 source가 되고, PDF/PPTX/DOCX/XLSX/text/code는 markdown으로 정규화됩니다. 추출된 이미지는 source asset으로 보관됩니다.</div>
        <div id="sbSourcePicked" class="sb-source-picked">선택된 파일 없음</div>
      </div>
      <p style="color:var(--muted);font-size:12px;line-height:1.6;">
        업로드한 파일은 source markdown으로 즉시 저장되고, index.md, log.md, graph cache가 바로 갱신됩니다.
        source 품질/lint 문제가 있을 때만 지식 점검에서 처리합니다. graph 후보는 점검함에 올리지 않습니다.
      </p>
      <div id="sbImportProgress" class="sb-import-progress" hidden>
        <div class="sb-import-progress-head">
          <span id="sbImportProgressText">처리 대기 중</span>
          <span id="sbImportProgressDetail"></span>
        </div>
        <div class="sb-import-progress-track">
          <div id="sbImportProgressBar" class="sb-import-progress-bar"></div>
        </div>
      </div>
    `;
    Modal.open({
      title: "자료 업로드",
      body,
      footButtons: [
        { label: "취소" },
        {
          label: "업로드",
          primary: true,
          onClick: async () => {
            const files = Knowledge._sourceImportFiles.length
              ? Knowledge._sourceImportFiles
              : Array.from($("#sbSourceFile").files || []);
            if (!files.length) {
              toast("원문 파일을 선택하거나 드롭하세요.", "error");
              return false;
            }
            try {
              const result = await Knowledge._importSourceFiles(files);
              toast(`Wiki ${result.imported}개 가져옴: graph edge ${result.graphEdges}개 / 점검 ${result.diffCount}개 / 변환 ${result.converted}개 / asset ${result.assets}개${result.failed ? ` / 실패 ${result.failed}개` : ""}`, result.failed ? "error" : "ok");
              Knowledge.mode = "inbox";
              Knowledge.inboxStatus = "pending";
              Knowledge._sourceImportFiles = [];
              Knowledge.render($("#sidebarSearch").value || "");
              if (window.SecondBrainGraph) {
                window.SecondBrainGraph.data = null;
              }
            } catch (e) {
              toast("업로드 실패: " + e.message, "error");
              return false;
            }
          },
        },
      ],
    });
    Knowledge._sourceImportFiles = [];
    Knowledge._bindSourceDrop();
    Knowledge._updateSourceDropLabel();
  },

  async openThought(id) {
    try {
      const data = await api(`api/code/knowledge/thought/${encodeURIComponent(id)}${_kbUidQs()}`);
      Knowledge.openThoughtViewer(data.meta || {}, data.content || "");
    } catch (e) {
      toast(e.message, "error");
    }
  },

  openThoughtViewer(meta, content) {
    const body = document.createElement("div");
    body.innerHTML = `
      <div class="sb-detail-meta">
        ${escapeHtml(_kbMemoryLabel(meta.memory_type || "document"))} · ${escapeHtml(meta.kind || meta.filename || "document")}
      </div>
      <pre class="sb-source-excerpt">${escapeHtml(content)}</pre>
      <label>출처</label>
      <pre class="sb-source-excerpt">${escapeHtml(JSON.stringify(meta.sources || [], null, 2))}</pre>
    `;
    Modal.open({
      title: meta.title || meta.id,
      body,
      footButtons: [
        {
          label: "채팅에 고정",
          primary: true,
          onClick: () => {
            Knowledge.pinThought(meta);
            return false;
          },
        },
        {
          label: "Notion으로 보내기",
          onClick: () => Knowledge.exportThoughtToNotion(meta, content),
        },
        {
          label: "수정",
          onClick: () => {
            Knowledge.openThoughtEditor(meta, content);
            return false;
          },
        },
        {
          label: "삭제",
          danger: true,
          onClick: () => Knowledge.deleteThought(meta),
        },
        { label: "닫기" },
      ],
    });
    const metaEl = $("#modalBody .sb-detail-meta");
    if (metaEl) {
      metaEl.textContent = `${_kbMemoryLabel(meta.memory_type || "document")} / ${meta.kind || meta.filename || "document"}`;
    }
  },

  openThoughtEditor(meta, content) {
    const body = document.createElement("div");
    body.innerHTML = `
      <label>제목</label>
      <input id="kbThoughtTitle" type="text">
      <label>유형</label>
      <select id="kbThoughtType">
        <option value="semantic">사실/정의</option>
        <option value="procedural">절차/규칙</option>
        <option value="episodic">사건/사례</option>
        <option value="reflective">회고/교훈</option>
        <option value="thesis">핵심 주장</option>
        <option value="topic">주제</option>
      </select>
      <label>주제, 쉼표로 구분</label>
      <input id="kbThoughtTopics" type="text">
      <label>내용</label>
      <textarea id="kbThoughtContent"></textarea>
    `;
    Modal.open({
      title: "Second Brain 콘텐츠 수정",
      body,
      footButtons: [
        { label: "취소" },
        {
          label: "저장",
          primary: true,
          onClick: async () => {
            const title = $("#kbThoughtTitle").value.trim();
            const nextContent = $("#kbThoughtContent").value.trim();
            if (!title || !nextContent) {
              toast("제목과 내용은 필수입니다.", "error");
              return false;
            }
            try {
              await api("api/code/knowledge/thought/update", {
                method: "POST",
                body: _withUid({
                  id: meta.id,
                  title,
                  memory_type: $("#kbThoughtType").value,
                  topics: $("#kbThoughtTopics").value.split(",").map(s => s.trim()).filter(Boolean),
                  sources: meta.sources || [],
                  meaning_version: meta.meaning_version || 1,
                  content: nextContent,
                }),
              });
              toast("콘텐츠를 수정했습니다", "ok");
              Knowledge.render($("#sidebarSearch")?.value || "");
              if (window.SecondBrainGraph) {
                window.SecondBrainGraph.data = null;
                if (State.graphMode) window.SecondBrainGraph.load();
              }
            } catch (e) {
              toast("수정 실패: " + e.message, "error");
              return false;
            }
          },
        },
      ],
    });
    $("#kbThoughtTitle").value = meta.title || meta.id || "";
    $("#kbThoughtType").value = meta.memory_type || "semantic";
    $("#kbThoughtTopics").value = Array.isArray(meta.topics) ? meta.topics.join(", ") : "";
    $("#kbThoughtContent").value = content || "";
  },

  async openCandidate(id) {
    try {
      const item = await api(`api/code/knowledge/inbox/${encodeURIComponent(id)}${_kbUidQs()}`);
      Knowledge.openCandidateEditor(item);
    } catch (e) {
      toast(e.message, "error");
    }
  },

  openCandidateEditor(item) {
    const source = item.source || {};
    const isDeleted = item.status === "deleted";
    const isGraphCandidate = item.candidate_kind === "graph_candidate";
    Knowledge._currentCandidate = item;
    Knowledge._currentRecommendation = item.llm_recommendation || null;
    const body = document.createElement("div");
    body.innerHTML = `
      <div class="sb-detail-meta">
        status: ${escapeHtml(item.status || "pending")} · source: ${escapeHtml(source.filename || source.raw_path || "-")}
      </div>
      <label>변경안 제목</label>
      <input id="candTitle" type="text" ${isDeleted ? "disabled" : ""}>
      <label>대상 Wiki 문서</label>
      <input id="candMergeTarget" type="text" value="${escapeAttr(item.target_filename || item.target_id || "")}" disabled>
      <label>태그</label>
      <input id="candTopics" type="text" ${isDeleted ? "disabled" : ""}>
      <label>적용될 본문</label>
      <textarea id="candContent" ${isDeleted ? "disabled" : ""}></textarea>
      <label>설명</label>
      <pre class="sb-source-excerpt">${escapeHtml(item.reason || "본문 diff는 사용자 승인 전까지 markdown 본문에 반영되지 않습니다. 본문 유지를 선택해도 source/index/log/graph 연결은 유지됩니다.")}</pre>
      <label>Diff 미리보기</label>
      <pre class="sb-source-excerpt">${escapeHtml(item.diff_text || item.content || "")}</pre>
      <label>검토 메모</label>
      <div class="sb-curation-panel">
        <div class="sb-curation-actions">
          <button type="button" id="candReviewBtn" ${isDeleted ? "disabled" : ""}>검토 메모 생성</button>
          <button type="button" id="candApplyRecommendationBtn" ${isDeleted ? "disabled" : ""}>제안 본문 입력창에 반영</button>
        </div>
        <pre class="sb-source-excerpt" id="candLlmRecommendation"></pre>
      </div>
      <label>Source</label>
      <pre class="sb-source-excerpt">${escapeHtml(Knowledge._sourceTraceText(item))}</pre>
    `;
    if (isGraphCandidate) {
      body.innerHTML = `
        <div class="sb-detail-meta">
          status: ${escapeHtml(item.status || "pending")} · source: ${escapeHtml(source.filename || source.raw_path || "-")}
        </div>
        <label>점검 제목</label>
        <input id="candTitle" type="text" ${isDeleted ? "disabled" : ""}>
        <label>대상 source</label>
        <input id="candMergeTarget" type="text" value="${escapeAttr(item.target_filename || item.target_id || "")}" disabled>
        <label>태그</label>
        <input id="candTopics" type="text" ${isDeleted ? "disabled" : ""}>
        <label>Graph 관계 후보</label>
        <pre class="sb-source-excerpt">${escapeHtml(Knowledge._graphCandidateText(item))}</pre>
        <label>검토 메모</label>
        <div class="sb-curation-panel">
          <div class="sb-curation-actions">
            <button type="button" id="candReviewBtn" ${isDeleted ? "disabled" : ""}>검토 메모 생성</button>
            <button type="button" id="candApplyRecommendationBtn" ${isDeleted ? "disabled" : ""}>추천 메모 반영</button>
          </div>
          <pre class="sb-source-excerpt" id="candLlmRecommendation"></pre>
        </div>
        <label>Source</label>
        <pre class="sb-source-excerpt">${escapeHtml(Knowledge._sourceTraceText(item))}</pre>
      `;
    }

    Modal.open({
      title: item.title || item.id,
      body,
      footButtons: Knowledge._candidateButtons(item),
    });

    const metaEl = $("#modalBody .sb-detail-meta");
    if (metaEl) {
      const deletedInfo = item.status === "deleted" && item.previous_status
        ? ` / 이전 상태: ${_kbStatusLabel(item.previous_status)}`
        : "";
      metaEl.textContent = `상태: ${_kbStatusLabel(item.status || "pending")} / 출처: ${source.filename || source.raw_path || "-"}${deletedInfo}`;
    }
    $("#candTitle").value = item.title || "";
    $("#candTopics").value = Array.isArray(item.topics) ? item.topics.join(", ") : "";
    if ($("#candContent")) $("#candContent").value = item.proposed_content || item.content || "";
    Knowledge._renderRecommendationPanel(Knowledge._currentRecommendation);
    $("#candReviewBtn")?.addEventListener("click", () => Knowledge.reviewCandidate(item, "review"));
    $("#candApplyRecommendationBtn")?.addEventListener("click", () => Knowledge.applyRecommendationToForm());
  },

  _renderRecommendationPanel(rec) {
    const el = $("#candLlmRecommendation");
    if (!el) return;
    if (!rec) {
      el.textContent = "아직 생성된 LLM 추천안이 없습니다. 추천안 생성 버튼을 누르면 local GGUF curator가 초안을 만듭니다.";
      return;
    }
    const lines = [
      `recommended_action: ${rec.recommended_action || "-"}`,
      `target_id: ${rec.target_id || "-"}`,
      `confidence: ${rec.confidence !== undefined ? Number(rec.confidence).toFixed(2) : "-"}`,
      `fallback: ${rec.fallback ? "yes" : "no"}`,
      `reason: ${rec.reason || "-"}`,
      "",
      "proposed_title:",
      rec.proposed_title || "-",
      "",
      "proposed_content:",
      rec.proposed_content || "-",
    ];
    if (Array.isArray(rec.risks) && rec.risks.length) {
      lines.push("", "risks:", ...rec.risks.map(x => `- ${x}`));
    }
    if (Array.isArray(rec.proposed_edges) && rec.proposed_edges.length) {
      lines.push("", "proposed_edges:", JSON.stringify(rec.proposed_edges, null, 2));
    }
    el.textContent = lines.join("\n");
  },

  async reviewCandidate(item, action = "review") {
    const target = $("#candMergeTarget")?.value.trim() || "";
    if ((action === "merge" || action === "replace") && !target) {
      toast("관계 검토에는 대상 source가 필요합니다.", "error");
      return false;
    }
    const panel = $("#candLlmRecommendation");
    if (panel) panel.textContent = "LLM 추천안을 생성하는 중입니다...";
    try {
      const result = await api("api/code/knowledge/inbox/review", {
        method: "POST",
        body: _withUid({
          id: item.id,
          action,
          target_id: target,
          persist: true,
        }),
      });
      Knowledge._currentRecommendation = result.recommendation || null;
      if (result.candidate) Knowledge._currentCandidate = result.candidate;
      Knowledge._renderRecommendationPanel(Knowledge._currentRecommendation);
      toast("LLM 추천안을 생성했습니다.", "ok");
    } catch (e) {
      if (panel) panel.textContent = `LLM 추천 생성 실패: ${e.message}`;
      toast("LLM 추천 생성 실패: " + e.message, "error");
    }
    return false;
  },

  applyRecommendationToForm() {
    const rec = Knowledge._currentRecommendation;
    if (!rec) {
      toast("적용할 LLM 추천안이 없습니다.", "error");
      return false;
    }
    if (rec.proposed_title && $("#candTitle")) $("#candTitle").value = rec.proposed_title;
    if (rec.proposed_content && $("#candContent")) $("#candContent").value = rec.proposed_content;
    if (rec.target_id && $("#candMergeTarget")) $("#candMergeTarget").value = rec.target_id;
    toast("추천안을 입력 폼에 반영했습니다. 최종 저장은 적용 버튼으로 확정하세요.", "ok");
    return false;
  },

  _graphCandidateText(item) {
    const edge = item.edge || {};
    const rows = [
      `source: ${edge.source || item.source?.source_id || "-"}`,
      `target: ${edge.target || item.target_id || "-"}`,
      `edge_type: ${edge.edge_type || item.action || "related"}`,
      `confidence: ${item.confidence !== undefined ? Number(item.confidence).toFixed(2) : "-"}`,
      `reason: ${item.reason || item.content || "-"}`,
      "",
      "적용하면 graph.json에 관계가 추가되고 검색 확장에 사용됩니다.",
      "제외하면 source markdown은 유지되지만 이 관계는 graph에 반영되지 않습니다.",
    ];
    return rows.join("\n");
  },

  _sourceTraceText(item) {
    const source = item.source || {};
    const rows = [
      `source file: ${source.filename || "-"}`,
      `source path: ${source.raw_path || "-"}`,
      `target wiki: ${item.target_filename || item.target_id || "-"}`,
      `diff action: ${item.action || "-"}`,
      `status: ${item.status || "pending"}`,
    ];
    rows.push(`candidate kind: ${item.candidate_kind || "wiki_diff"}`);
    return rows.join("\n");
  },

  _recommendationText(item) {
    const lines = [
      `action: ${item.action || "update"}`,
      `target: ${item.target_filename || item.target_id || "-"}`,
      `reason: ${item.reason || "-"}`,
    ];
    lines.push("점검 완료 또는 제외를 선택해도 source markdown은 유지됩니다.");
    return lines.join("\n");
  },

  _candidateButtons(item) {
    if (item.status === "deleted") {
      return [
        {
          label: "복구",
          primary: true,
          onClick: async () => {
            await Knowledge.candidateAction(item.id, "restore");
          },
        },
        {
          label: "영구 삭제",
          danger: true,
          onClick: () => Knowledge.purgeCandidate(item),
        },
        { label: "닫기" },
      ];
    }

    return [
      {
        label: item.status === "rejected" || item.status === "applied" ? "대기로 복구" : "제외",
        danger: item.status !== "rejected" && item.status !== "applied",
        onClick: async () => {
          if (item.status === "rejected" || item.status === "applied") {
            await Knowledge.candidateAction(item.id, "restore");
          } else {
            await Knowledge.candidateAction(item.id, "skip", {
              updates: Knowledge._candidateUpdates(),
              skip_reason: "graph candidate rejected",
            });
          }
        },
      },
      {
        label: item.candidate_kind === "source_lint" ? "점검 완료" : "처리",
        primary: true,
        onClick: async () => {
          await Knowledge.candidateAction(item.id, "approve", { updates: Knowledge._candidateUpdates() });
        },
      },
      {
        label: "삭제",
        danger: true,
        onClick: () => Knowledge.deleteCandidate(item),
      },
      { label: "닫기" },
    ];
  },

  _candidateUpdates() {
    const updates = {
      title: $("#candTitle")?.value.trim() || "",
      topics: ($("#candTopics")?.value || "").split(",").map(s => s.trim()).filter(Boolean),
      content: $("#candContent")?.value || "",
      proposed_content: $("#candContent")?.value || Knowledge._currentCandidate?.proposed_content || "",
    };
    if (Knowledge._currentRecommendation) {
      updates.llm_recommendation = Knowledge._currentRecommendation;
    }
    return updates;
  },

  async candidateAction(id, action, extra = {}) {
    try {
      await api("api/code/knowledge/inbox/action", {
        method: "POST",
        body: _withUid({ id, action, ...extra }),
      });
      toast(`지식 점검 항목 ${_kbActionLabel(action)} 완료`, "ok");
      Knowledge.render($("#sidebarSearch").value || "");
      if (window.SecondBrainGraph) {
        window.SecondBrainGraph.data = null;
      }
    } catch (e) {
      toast(`${_kbActionLabel(action)} 실패: ${e.message}`, "error");
      return false;
    }
  },

  async deleteCandidate(item) {
    const name = item.title || item.id;
    if (!confirm(`"${name}" 지식 점검 항목을 삭제됨 탭으로 이동할까요?\nsource markdown은 유지되고 이 점검 기록만 이동합니다.`)) {
      return false;
    }
    await Knowledge.candidateAction(item.id, "delete");
  },

  async purgeCandidate(item) {
    const name = item.title || item.id;
    if (!confirm(`"${name}" 지식 점검 항목을 영구 삭제할까요?\n점검 기록이 삭제됩니다.`)) {
      return false;
    }
    await Knowledge.candidateAction(item.id, "purge");
  },

  pinThought(meta) {
    if (!meta?.id) return;
    if (!State.pinnedThoughts.some(t => t.id === meta.id)) {
      State.pinnedThoughts.push({ id: meta.id, title: meta.title || meta.id });
    }
    Chat.refreshChips();
    refreshMetaBar();
    toast("채팅 컨텍스트에 고정했습니다", "ok");
  },

  async exportThoughtToNotion(meta, content) {
    if (!meta?.id && !content) return false;
    try {
      const data = await api("api/code/notion/import_thought", {
        method: "POST",
        body: {
          thought_id: meta.id || "",
          title: meta.title || meta.id || "Second Brain Note",
          content: content || "",
        },
      });
      const pageId = data.page?.id || "";
      if (pageId) {
        const host = window.location.hostname || "localhost";
        const notionPort = location.port === "10111" ? "10112" : "10012";
        window.open(`http://${host}:${notionPort}/page/${encodeURIComponent(pageId)}`, "_blank", "noopener");
      }
      toast("Notion 페이지로 보냈습니다", "ok");
    } catch (e) {
      toast("Notion 보내기 실패: " + e.message, "error");
      return false;
    }
  },

  async deleteThought(meta) {
    if (!meta?.id) return false;
    const name = meta.title || meta.id;
    if (!confirm(`"${name}" 콘텐츠를 삭제할까요?\n연결된 그래프 관계를 제거하고, 같은 원문 조각이 답변 검색에 다시 들어오지 않도록 삭제 기록을 남깁니다.\n원문 보존 파일과 검토 이력은 추적용으로 남습니다.`)) return false;
    try {
      await api("api/code/knowledge/thought/delete", {
        method: "POST",
        body: _withUid({ id: meta.id }),
      });
      State.pinnedThoughts = (State.pinnedThoughts || []).filter(t => t.id !== meta.id);
      Chat.refreshChips();
      refreshMetaBar();
      Knowledge.render($("#sidebarSearch")?.value || "");
      if (window.SecondBrainGraph) {
        window.SecondBrainGraph.selectedId = null;
        window.SecondBrainGraph.data = null;
        if (State.graphMode) window.SecondBrainGraph.load();
      }
      toast("콘텐츠를 삭제했습니다", "ok");
    } catch (e) {
      toast("삭제 실패: " + e.message, "error");
      return false;
    }
  },
};

function _kbMemoryLabel(type) {
  return ({
    semantic: "Wiki 문서",
    procedural: "Wiki 문서",
    episodic: "Wiki 문서",
    reflective: "Wiki 문서",
    thesis: "Wiki 문서",
    topic: "Wiki 문서",
    document: "Wiki 문서",
    thought: "Wiki 문서",
  })[type] || type || "지식";
}

function _kbStatusLabel(status) {
  return ({
    pending: "대기",
    approved: "적용됨",
    applied: "적용됨",
    merged: "적용됨",
    replaced: "적용됨",
    skipped: "제외됨",
    rejected: "제외됨",
    deleted: "삭제됨",
    purged: "영구 삭제",
  })[status] || status || "대기";
}

function _kbActionLabel(action) {
  return ({
    approve: "점검 완료",
    merge: "점검 완료",
    replace: "점검 완료",
    refine: "수정 저장",
    skip: "제외",
    delete: "삭제",
    restore: "복구",
    purge: "영구 삭제",
  })[action] || action || "처리";
}

function _kbRecommendedLabel(action) {
  return ({
    merge: "점검 완료 추천",
    replace: "점검 완료 추천",
    approve: "점검 완료 추천",
    skip: "제외 추천",
    review: "검토 필요",
    "new-linked": "새 Wiki 연결",
    new: "새 Wiki page",
  })[action] || "";
}

function _kbCssEscape(value) {
  if (window.CSS?.escape) return CSS.escape(String(value));
  return String(value).replace(/["\\]/g, "\\$&");
}

function escapeHtml(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function escapeAttr(s) {
  return escapeHtml(s).replace(/'/g, "&#39;");
}

document.addEventListener("second-brain:upload-source", () => {
  Knowledge.openAddMenu({ initialTab: "upload" });
});

document.addEventListener("second-brain:promote-chat", () => {
  Knowledge.openAddMenu({ initialTab: "chat" });
});
