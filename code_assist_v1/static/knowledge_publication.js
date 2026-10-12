/* Shared P4.5 publication modal for upload, chat promotion, and Notion. */
(function knowledgePublicationModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.KnowledgePublicationUI = api;
})(typeof globalThis === "object" ? globalThis : this, function buildKnowledgePublicationUI() {
  "use strict";

  const MODES = Object.freeze(["personal", "part", "both", "explicit"]);
  const OUTCOME_LABELS = Object.freeze({
    created: "새 지식 등록",
    provenance_added: "기존 지식에 출처 추가",
    update_proposed: "기존 지식 변경 제안",
    no_op: "변경 없음",
  });
  const BOTH_WARNING = "두 개의 독립 지식으로 등록되며 이후 자동 동기화되지 않습니다.";

  function object(value, field) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new TypeError(field + " must be an object.");
    return value;
  }

  function string(value, field) {
    if (typeof value !== "string" || !value) throw new TypeError(field + " must be a non-empty string.");
    return value;
  }

  function isPreparing(preview) {
    if (!["queued", "extracting", "ready"].includes(preview.status)) return false;
    return preview.wiki_generation ? preview.wiki_generation.status === "pending"
      : ["queued", "extracting"].includes(preview.status);
  }

  function preparationLabel(preview) {
    if (preview?.status === "ready" && preview.wiki_generation?.status === "pending") return "텍스트 추출 완료 · Wiki 생성 중";
    return preview?.status === "extracting" ? "텍스트 추출 중" : "미리보기 준비 중";
  }

  function previewView(preview, spaces, preferredTargetMode = null, personalWiki = null, explicitTarget = null) {
    object(preview, "preview");
    if (preview.status !== "ready") throw new TypeError("A ready preview is required.");
    if (preview.wiki_generation && preview.publication_ready !== true) throw new TypeError("Wiki 생성이 완료되지 않았거나 게시할 본문이 없습니다.");
    if (!Array.isArray(preview.available_target_modes) || !preview.available_target_modes.length) throw new TypeError("Preview target modes are invalid.");
    if (!Array.isArray(spaces)) throw new TypeError("spaces must be an array.");
    const part = spaces.find((space) => space && space.kind === "part") || null;
    const higher = explicitTarget
      ? spaces.find((space) => space && space.id === explicitTarget.space_id) || null
      : null;
    const available = new Set(preview.available_target_modes);
    const serverExplicitTarget = available.has("explicit")
      ? (Array.isArray(preview.available_targets) && preview.available_targets.length === 1
        ? preview.available_targets[0] : null)
      : null;
    if (available.has("explicit") && (
      !serverExplicitTarget
      || !explicitTarget
      || ["space_id", "kind", "capability", "role_assignment_id", "role_assignment_generation"]
        .some((key) => serverExplicitTarget[key] !== explicitTarget[key])
    )) {
      throw new TypeError("The explicit publication target changed before preview.");
    }
    const visibleModes = available.has("explicit") || explicitTarget
      ? MODES
      : MODES.filter((mode) => mode !== "explicit");
    const options = visibleModes.map((mode) => Object.freeze({
      disabled: !available.has(mode),
      label: mode === "personal" ? "개인" : mode === "part" ? (part ? part.name : "현재 파트") : `개인 + ${part ? part.name : "현재 파트"}`,
      mode,
    }));
    const renderedOptions = options.map((option) => option.mode !== "explicit"
      ? option
      : Object.freeze({
        ...option,
        label: `${higher && higher.kind === "responsibility" ? "담당" : "팀"} · ${higher ? higher.name : "상위 지식"}`,
      }));
    if (preferredTargetMode !== null && !MODES.includes(preferredTargetMode)) {
      throw new TypeError("Preferred target mode is invalid.");
    }
    const selected = preferredTargetMode === null
      ? renderedOptions.find((option) => !option.disabled)
      : renderedOptions.find((option) => option.mode === preferredTargetMode && !option.disabled);
    if (preferredTargetMode !== null && !selected) {
      throw new TypeError("처음 선택한 게시 대상은 현재 사용할 수 없습니다. 게시 위치를 다시 선택해 주세요.");
    }
    if (!selected) throw new TypeError("Preview has no selectable target mode.");
    return Object.freeze({
      bothWarning: BOTH_WARNING,
      explicitTarget: serverExplicitTarget,
      options: Object.freeze(renderedOptions),
      previewId: string(preview.preview_id, "preview_id"),
      personalWiki,
      selectedMode: selected.mode,
      sourceSnapshotSha256: string(preview.source_snapshot_sha256, "source_snapshot_sha256"),
      title: string(preview.title, "title"),
      warnings: Object.freeze([
        ...(Array.isArray(preview.warnings) ? preview.warnings.map((value) => string(value, "warning")) : []),
        ...(preview.wiki_generation?.status === "ready" ? ["Wiki 생성 완료. 같은 원본의 기존 지식이 있으면 게시 시 기존 본문을 사용할 수 있습니다."] : []),
        ...(preview.wiki_generation?.status === "extraction_only" ? ["Wiki 생성은 완료되지 않았습니다. 보존된 추출 본문으로 게시합니다."] : []),
      ]),
    });
  }

  function resultView(result, spaces) {
    object(result, "result");
    if (!Array.isArray(result.results) || !result.results.length) throw new TypeError("Publication results are invalid.");
    const names = new Map((spaces || []).map((space) => [space.id, space.name]));
    return Object.freeze({
      enrichmentStatus: "별도 처리",
      operationId: string(result.operation_id, "operation_id"),
      projectionStatus: "별도 처리",
      publicationStatus: result.body_source === "authored_wiki" ? "Wiki 게시 완료"
        : result.body_source === "extraction" ? "추출 본문 게시 완료" : "게시 완료",
      targets: Object.freeze(result.results.map((target) => {
        object(target, "target");
        const outcome = string(target.outcome, "target.outcome");
        if (!Object.hasOwn(OUTCOME_LABELS, outcome)) throw new TypeError("Publication outcome is invalid.");
        return Object.freeze({
          findingId: target.finding_id,
          itemId: string(target.item_id, "target.item_id"),
          label: OUTCOME_LABELS[outcome],
          outcome,
          spaceId: string(target.space_id, "target.space_id"),
          spaceName: names.get(target.space_id) || target.space_id,
        });
      })),
    });
  }

  function createDomRenderer({ documentRef, modal, toast, openTarget }) {
    if (!documentRef || !modal || typeof modal.open !== "function") throw new TypeError("DOM publication renderer dependencies are required.");
    const notify = typeof toast === "function" ? toast : () => {};
    const navigate = typeof openTarget === "function" ? openTarget : () => {};
    const element = (tag, text = "") => {
      const node = documentRef.createElement(tag);
      node.textContent = text;
      return node;
    };
    return Object.freeze({
      showPreparing() { notify("게시 미리보기를 준비하고 있습니다.", "ok"); },
      showError(error) { notify(`게시 실패: ${error && error.message ? error.message : "알 수 없는 오류"}`, "error"); },
      showPending(preview, cancel) {
        const body = element("div");
        body.className = "knowledge-publication-modal";
        body.setAttribute("aria-busy", "true");
        body.setAttribute("aria-live", "polite");
        body.appendChild(element("p", preparationLabel(preview)));
        modal.open({
          title: "게시 미리보기 준비",
          body,
          closeable: false,
          footButtons: [{ label: "취소", onClick: cancel }],
        });
      },
      showPreview(view, commit, cancel) {
        const body = element("div");
        body.className = "knowledge-publication-modal";
        const heading = element("h3", view.title);
        body.appendChild(heading);
        for (const warning of view.warnings) {
          const row = element("p", warning);
          row.className = "knowledge-publication-warning";
          body.appendChild(row);
        }
        const fieldset = element("fieldset");
        fieldset.setAttribute("aria-label", "게시 대상");
        let selected = view.selectedMode;
        const warning = element("p", view.bothWarning);
        warning.className = "knowledge-publication-both-warning";
        warning.hidden = selected !== "both";
        for (const option of view.options) {
          const label = element("label");
          const input = documentRef.createElement("input");
          input.type = "radio";
          input.name = `publication-target-${view.previewId}`;
          input.value = option.mode;
          input.checked = option.mode === selected;
          input.disabled = option.disabled;
          input.addEventListener("change", () => {
            if (input.checked) selected = option.mode;
            warning.hidden = selected !== "both";
          });
          label.append(input, documentRef.createTextNode(option.label));
          fieldset.appendChild(label);
        }
        body.append(fieldset, warning);
        if (view.personalWiki) {
          const selectedWiki = element("p", `개인 Wiki: ${view.personalWiki.displayName}`);
          selectedWiki.className = "knowledge-publication-personal-wiki";
          body.appendChild(selectedWiki);
        }
        modal.open({
          title: "지식 게시",
          body,
          closeable: false,
          footButtons: [
            { label: "취소", onClick: cancel },
            { label: "게시", primary: true, onClick: async () => { await commit(selected); return false; } },
          ],
        });
      },
      showResults(view) {
        const body = element("div");
        body.className = "knowledge-publication-results";
        body.setAttribute("aria-live", "polite");
        const status = element("p", `${view.publicationStatus} · Graph enrichment ${view.enrichmentStatus} · Projection ${view.projectionStatus}`);
        status.className = "knowledge-publication-status";
        body.appendChild(status);
        for (const target of view.targets) {
          const link = element("button", `${target.spaceName}: ${target.label}`);
          link.type = "button";
          link.dataset.itemId = target.itemId;
          link.dataset.spaceId = target.spaceId;
          link.addEventListener("click", () => {
            Promise.resolve(navigate(target)).catch((error) => notify(
              `게시 결과 이동 실패: ${error && error.message ? error.message : "알 수 없는 오류"}`,
              "error",
            ));
          });
          body.appendChild(link);
        }
        modal.open({ title: "게시 결과", body, footButtons: [{ label: "닫기" }] });
      },
      showBatchProgress(view, cancel) {
        const body = element("div");
        body.className = "knowledge-publication-batch";
        body.setAttribute("aria-busy", "true");
        body.setAttribute("aria-live", "polite");
        body.appendChild(element("p", `${view.finishedCount} / ${view.totalCount}개 미리보기 준비`));
        const list = element("ol");
        list.className = "knowledge-publication-batch-list";
        for (const item of view.items) {
          const row = element("li");
          row.dataset.status = item.status;
          row.append(
            element("span", item.name),
            element("strong", item.statusLabel),
          );
          if (item.error) row.appendChild(element("small", item.error));
          list.appendChild(row);
        }
        body.appendChild(list);
        modal.open({
          title: `다중 파일 미리보기 · ${view.totalCount}개`,
          body,
          closeable: false,
          footButtons: [{ label: "전체 취소", onClick: cancel }],
        });
      },
      showBatchPreview(view, commit, cancel) {
        const body = element("div");
        body.className = "knowledge-publication-batch";
        body.appendChild(element("p", `준비 ${view.readyCount}개 · 실패 ${view.failedCount}개`));
        if (view.targetLabel) body.appendChild(element("p", `게시 위치: ${view.targetLabel}`));
        const list = element("ol");
        list.className = "knowledge-publication-batch-list";
        for (const item of view.items) {
          const row = element("li");
          row.dataset.status = item.status;
          row.append(element("span", item.title || item.name), element("strong", item.statusLabel));
          for (const warning of item.warnings || []) row.appendChild(element("small", warning));
          if (item.error) row.appendChild(element("small", item.error));
          list.appendChild(row);
        }
        body.appendChild(list);
        modal.open({
          title: "다중 게시 미리보기",
          body,
          closeable: false,
          footButtons: [
            { label: "전체 취소", onClick: cancel },
            ...(view.readyCount ? [{ label: `${view.readyCount}개 게시`, primary: true, onClick: async () => { await commit(); return false; } }] : []),
          ],
        });
      },
      showBatchCommitProgress(view) {
        const body = element("div");
        body.className = "knowledge-publication-batch";
        body.setAttribute("aria-busy", "true");
        body.setAttribute("aria-live", "polite");
        body.appendChild(element("p", `${view.completedCount} / ${view.readyCount}개 게시 완료`));
        const list = element("ol");
        list.className = "knowledge-publication-batch-list";
        for (const item of view.items) {
          const row = element("li");
          row.dataset.status = item.status;
          row.append(element("span", item.title || item.name), element("strong", item.statusLabel));
          if (item.error) row.appendChild(element("small", item.error));
          list.appendChild(row);
        }
        body.appendChild(list);
        modal.open({ title: "다중 지식 게시", body, closeable: false, footButtons: [] });
      },
      showBatchResults(view, retry) {
        const body = element("div");
        body.className = "knowledge-publication-batch";
        body.setAttribute("aria-live", "polite");
        body.appendChild(element("p", `성공 ${view.succeededCount}개 · 실패 ${view.failedCount}개 · 취소 ${view.cancelledCount}개`));
        const list = element("ol");
        list.className = "knowledge-publication-batch-list";
        for (const item of view.items) {
          const row = element("li");
          row.dataset.status = item.status;
          row.append(element("span", item.title || item.name), element("strong", item.statusLabel));
          if (item.error) row.appendChild(element("small", item.error));
          for (const target of item.targets || []) {
            const link = element("button", `${target.spaceName}: ${target.label}`);
            link.type = "button";
            link.addEventListener("click", () => {
              Promise.resolve(navigate(target)).catch((error) => notify(
                `게시 결과 이동 실패: ${error && error.message ? error.message : "알 수 없는 오류"}`,
                "error",
              ));
            });
            row.appendChild(link);
          }
          list.appendChild(row);
        }
        body.appendChild(list);
        modal.open({
          title: "다중 게시 결과",
          body,
          footButtons: [
            ...(view.failedCount ? [{ label: "실패 항목 다시 시도", onClick: async () => { await retry(); return false; } }] : []),
            { label: "닫기" },
          ],
        });
        notify(`다중 게시 완료: 성공 ${view.succeededCount}개, 실패 ${view.failedCount}개`, view.failedCount ? "error" : "ok");
      },
    });
  }

  function createBatchController({ transport, renderer, operationIdFactory, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), maxPolls = 900, concurrency = 3 }) {
    if (!transport || typeof transport.createPublicationPreview !== "function" || typeof transport.commitPublication !== "function" || typeof transport.getPublicationPreview !== "function" || typeof transport.cancelPublicationPreview !== "function") throw new TypeError("Canonical publication transport is required.");
    if (!renderer || ["showBatchProgress", "showBatchPreview", "showBatchCommitProgress", "showBatchResults"].some((name) => typeof renderer[name] !== "function")) throw new TypeError("Canonical batch publication renderer is required.");
    if (typeof operationIdFactory !== "function" || typeof sleep !== "function" || !Number.isSafeInteger(maxPolls) || maxPolls < 1 || !Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 8) throw new TypeError("Batch publication controller options are invalid.");
    let current = null;
    let cancellationRequested = false;

    const statusLabels = Object.freeze({
      waiting: "대기",
      preparing: "미리보기 준비 중",
      ready: "게시 준비 완료",
      publishing: "게시 중",
      succeeded: "게시 완료",
      failed: "실패",
      cancelled: "취소",
    });

    function publicItem(entry) {
      return Object.freeze({
        error: entry.error ? String(entry.error.message || entry.error) : null,
        name: entry.name,
        status: entry.status,
        statusLabel: entry.status === "preparing" ? preparationLabel(entry.preview)
          : entry.status === "succeeded" && entry.resultView ? entry.resultView.publicationStatus : statusLabels[entry.status],
        targets: entry.resultView ? entry.resultView.targets : Object.freeze([]),
        title: entry.view ? entry.view.title : entry.name,
        warnings: entry.view ? entry.view.warnings : Object.freeze([]),
      });
    }

    function snapshot(phase) {
      const items = Object.freeze(current.entries.map(publicItem));
      const count = (status) => current.entries.filter((entry) => entry.status === status).length;
      return Object.freeze({
        cancelledCount: count("cancelled"),
        completedCount: current.entries.filter((entry) => entry.status === "succeeded" || (entry.status === "failed" && entry.failureStage === "commit")).length,
        failedCount: count("failed"),
        finishedCount: current.entries.filter((entry) => !["waiting", "preparing"].includes(entry.status)).length,
        items,
        phase,
        readyCount: current.entries.filter((entry) => entry.prepared && entry.status !== "cancelled").length,
        succeededCount: count("succeeded"),
        targetLabel: current.targetLabel,
        totalCount: current.entries.length,
      });
    }

    async function runPool(entries, worker) {
      let cursor = 0;
      const workers = Array.from({ length: Math.min(concurrency, entries.length) }, async () => {
        while (cursor < entries.length) {
          const entry = entries[cursor++];
          await worker(entry);
        }
      });
      await Promise.all(workers);
    }

    async function cancelEntry(entry) {
      if (entry.preview && ["queued", "extracting", "ready"].includes(entry.preview.status)) {
        try {
          await transport.cancelPublicationPreview({ operationId: entry.cancelOperationId || (entry.cancelOperationId = operationIdFactory()), previewId: entry.preview.preview_id });
        } catch (_) {}
      }
      if (!["succeeded", "failed"].includes(entry.status)) entry.status = "cancelled";
    }

    async function cancelAll() {
      cancellationRequested = true;
      await Promise.all(current.entries.map(cancelEntry));
      renderer.showBatchResults(snapshot("cancelled"), retryFailed);
      return Object.freeze({ cancelled: true });
    }

    async function prepareEntry(entry, spaces) {
      if (cancellationRequested) {
        entry.status = "cancelled";
        return;
      }
      entry.status = "preparing";
      entry.error = null;
      renderer.showBatchProgress(snapshot("preparing"), cancelAll);
      try {
        let preview = await transport.createPublicationPreview({ ...entry.source, operationId: entry.previewOperationId });
        entry.preview = preview;
        for (let attempt = 0; isPreparing(preview); attempt += 1) {
          if (cancellationRequested) {
            await cancelEntry(entry);
            return;
          }
          if (attempt >= maxPolls) throw new Error("게시 미리보기 준비 시간이 초과되었습니다.");
          await sleep(1000);
          if (cancellationRequested) {
            await cancelEntry(entry);
            return;
          }
          preview = await transport.getPublicationPreview(preview.preview_id);
          entry.preview = preview;
          renderer.showBatchProgress(snapshot("preparing"), cancelAll);
        }
        if (preview.status === "cancelled") {
          entry.status = "cancelled";
          return;
        }
        if (preview.status !== "ready") throw new Error("게시 가능한 미리보기가 아닙니다.");
        const personalWiki = entry.personalSpaceId === null ? null : Object.freeze({ displayName: entry.personalWikiDisplayName, spaceId: entry.personalSpaceId });
        entry.view = previewView(preview, spaces, entry.preferredTargetMode, personalWiki, entry.explicitTarget);
        entry.prepared = true;
        entry.status = "ready";
        entry.failureStage = null;
      } catch (error) {
        entry.error = error;
        entry.status = cancellationRequested ? "cancelled" : "failed";
        entry.failureStage = "prepare";
      } finally {
        renderer.showBatchProgress(snapshot("preparing"), cancelAll);
      }
    }

    async function commitEntry(entry, spaces) {
      if (cancellationRequested || entry.status !== "ready") return;
      entry.status = "publishing";
      entry.error = null;
      renderer.showBatchCommitProgress(snapshot("publishing"));
      try {
        const result = await transport.commitPublication({
          operationId: entry.commitOperationId,
          personalSpaceId: ["personal", "both"].includes(entry.view.selectedMode) ? (entry.view.personalWiki && entry.view.personalWiki.spaceId) || null : null,
          previewId: entry.view.previewId,
          sourceSnapshotSha256: entry.view.sourceSnapshotSha256,
          target: entry.view.selectedMode === "explicit" ? entry.view.explicitTarget : null,
          targetMode: entry.view.selectedMode,
        });
        entry.resultView = resultView(result, spaces);
        entry.status = "succeeded";
        entry.failureStage = null;
      } catch (error) {
        entry.error = error;
        entry.status = "failed";
        entry.failureStage = "commit";
      } finally {
        renderer.showBatchCommitProgress(snapshot("publishing"));
      }
    }

    async function commitReady() {
      cancellationRequested = false;
      const ready = current.entries.filter((entry) => entry.status === "ready");
      await runPool(ready, (entry) => commitEntry(entry, current.spaces));
      renderer.showBatchResults(snapshot("completed"), retryFailed);
      return snapshot("completed");
    }

    async function retryFailed() {
      cancellationRequested = false;
      const failed = current.entries.filter((entry) => entry.status === "failed");
      for (const entry of failed) entry.status = entry.failureStage === "commit" ? "ready" : "waiting";
      const prepareFailures = failed.filter((entry) => entry.failureStage === "prepare");
      if (prepareFailures.length) await runPool(prepareFailures, (entry) => prepareEntry(entry, current.spaces));
      await commitReady();
      return snapshot("completed");
    }

    async function open(sources) {
      if (!Array.isArray(sources) || sources.length < 2) throw new TypeError("At least two publication sources are required.");
      cancellationRequested = false;
      const spacesPage = await transport.spaces();
      const spaces = (spacesPage.items || []).slice();
      const entries = sources.map((raw, index) => {
        const request = { ...object(raw, `sources[${index}]`) };
        const preferredTargetMode = request.preferredTargetMode ?? null;
        const personalSpaceId = request.personalSpaceId ?? null;
        const personalWikiDisplayName = request.personalWikiDisplayName ?? null;
        const explicitTarget = request.target ?? null;
        const name = request.title || (request.file && request.file.name) || `파일 ${index + 1}`;
        delete request.preferredTargetMode;
        delete request.personalSpaceId;
        delete request.personalWikiDisplayName;
        return {
          cancelOperationId: null,
          commitOperationId: operationIdFactory(),
          error: null,
          explicitTarget,
          failureStage: null,
          name: String(name),
          personalSpaceId,
          personalWikiDisplayName,
          preferredTargetMode,
          preview: null,
          prepared: false,
          previewOperationId: request.operationId || operationIdFactory(),
          resultView: null,
          source: request,
          status: "waiting",
          view: null,
        };
      });
      for (const entry of entries) {
        if (entry.personalSpaceId !== null && !spaces.some((space) => space.id === entry.personalSpaceId)) {
          spaces.push({ id: entry.personalSpaceId, kind: "personal", name: entry.personalWikiDisplayName });
        }
      }
      current = { entries, spaces, targetLabel: null };
      renderer.showBatchProgress(snapshot("preparing"), cancelAll);
      await runPool(entries, (entry) => prepareEntry(entry, spaces));
      if (cancellationRequested) return Object.freeze({ cancelled: true });
      const firstReady = entries.find((entry) => entry.view);
      current.targetLabel = firstReady ? firstReady.view.options.find((option) => option.mode === firstReady.view.selectedMode)?.label || firstReady.view.selectedMode : null;
      renderer.showBatchPreview(snapshot("preview"), commitReady, cancelAll);
      return Object.freeze({ cancel: cancelAll, commit: commitReady, state: snapshot("preview") });
    }

    return Object.freeze({ open, state: () => current ? snapshot("current") : null });
  }

  function createController({ transport, renderer, operationIdFactory, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), maxPolls = 900 }) {
    if (!transport || typeof transport.createPublicationPreview !== "function" || typeof transport.commitPublication !== "function" || typeof transport.getPublicationPreview !== "function" || typeof transport.cancelPublicationPreview !== "function") throw new TypeError("Canonical publication transport is required.");
    if (!renderer || typeof renderer.showPreview !== "function" || typeof renderer.showResults !== "function") throw new TypeError("Publication renderer is required.");
    if (typeof operationIdFactory !== "function" || typeof sleep !== "function" || !Number.isSafeInteger(maxPolls) || maxPolls < 1) throw new TypeError("Publication controller options are invalid.");
    let current = null;
    let cancellationRequested = false;

    async function cancelPreview(preview) {
      cancellationRequested = true;
      try {
        const cancelled = await transport.cancelPublicationPreview({
          operationId: operationIdFactory(),
          previewId: string(preview.preview_id, "preview_id"),
        });
        current = Object.freeze({ preview, result: cancelled, view: null });
        return cancelled;
      } catch (error) {
        cancellationRequested = false;
        if (renderer.showError) renderer.showError(error);
        throw error;
      }
    }

    async function readyPreview(source) {
      if (renderer.showPreparing) renderer.showPreparing();
      let preview = await transport.createPublicationPreview({ ...source, operationId: source.operationId || operationIdFactory() });
      if (isPreparing(preview) && renderer.showPending) {
        renderer.showPending(preview, () => cancelPreview(preview));
      }
      for (let attempt = 0; isPreparing(preview); attempt += 1) {
        if (cancellationRequested) return null;
        if (attempt >= maxPolls) throw new Error("게시 미리보기 준비 시간이 초과되었습니다.");
        await sleep(1000);
        if (cancellationRequested) return null;
        preview = await transport.getPublicationPreview(preview.preview_id);
        if (isPreparing(preview) && renderer.showPending) renderer.showPending(preview, () => cancelPreview(preview));
      }
      if (preview.status === "cancelled") return null;
      if (preview.status !== "ready") throw new Error("게시 가능한 미리보기가 아닙니다.");
      return preview;
    }

    async function open(source) {
      try {
        cancellationRequested = false;
        const request = { ...object(source, "source") };
        const preferredTargetMode = request.preferredTargetMode ?? null;
        const personalSpaceId = request.personalSpaceId ?? null;
        const personalWikiDisplayName = request.personalWikiDisplayName ?? null;
        const explicitTarget = request.target ?? null;
        delete request.preferredTargetMode;
        delete request.personalSpaceId;
        delete request.personalWikiDisplayName;
        const preview = await readyPreview(request);
        if (preview === null) return Object.freeze({ cancelled: true });
        const spacesPage = await transport.spaces();
        const spaces = (spacesPage.items || []).slice();
        let personalWiki = null;
        if (personalSpaceId !== null) {
          string(personalSpaceId, "personalSpaceId");
          string(personalWikiDisplayName, "personalWikiDisplayName");
          personalWiki = Object.freeze({ displayName: personalWikiDisplayName, spaceId: personalSpaceId });
          if (!spaces.some((space) => space.id === personalSpaceId)) {
            spaces.push({ id: personalSpaceId, kind: "personal", name: personalWikiDisplayName });
          }
        }
        const view = previewView(preview, spaces, preferredTargetMode, personalWiki, explicitTarget);
        let commitOperationId = null;
        const commit = async (targetMode) => {
          try {
            if (!view.options.some((option) => option.mode === targetMode && !option.disabled)) throw new TypeError("선택할 수 없는 게시 대상입니다.");
            const result = await transport.commitPublication({
              operationId: commitOperationId || (commitOperationId = operationIdFactory()),
              previewId: view.previewId,
              sourceSnapshotSha256: view.sourceSnapshotSha256,
              targetMode,
              personalSpaceId: ["personal", "both"].includes(targetMode)
                ? (view.personalWiki && view.personalWiki.spaceId) || null : null,
              target: targetMode === "explicit" ? view.explicitTarget : null,
            });
            const rendered = resultView(result, spaces);
            renderer.showResults(rendered);
            current = Object.freeze({ preview, result, view: rendered });
            return result;
          } catch (error) {
            if (renderer.showError) renderer.showError(error);
            throw error;
          }
        };
        current = Object.freeze({ preview, result: null, view });
        const cancel = () => cancelPreview(preview);
        renderer.showPreview(view, commit, cancel);
        return Object.freeze({ cancel, commit, preview, view });
      } catch (error) {
        if (renderer.showError) renderer.showError(error);
        throw error;
      }
    }

    return Object.freeze({ open, state: () => current });
  }

  return Object.freeze({ BOTH_WARNING, OUTCOME_LABELS, createBatchController, createController, createDomRenderer, previewView, resultView });
});
