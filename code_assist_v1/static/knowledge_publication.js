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
    if (preview.generation_worker?.status === "unavailable") return false;
    return preview.wiki_generation ? preview.wiki_generation.status === "pending"
      : ["queued", "extracting"].includes(preview.status);
  }

  function previewNotReadyError(preview) {
    if (preview.wiki_generation?.status === "pending" && preview.generation_worker?.status === "unavailable") {
      return new Error("문서 처리 작업자가 중단돼 더 진행할 수 없습니다. 원본과 완료된 작성 결과는 보존돼 있으며, 서버 작업자 복구가 필요합니다. (WORKER_UNAVAILABLE)");
    }
    if (preview.error?.code === "NATIVE_VISUAL_ANALYSIS_REQUIRED") {
      return new Error("문서의 그림·도형에 있는 정보를 현재 방식으로 확인할 수 없어 Wiki 생성을 중단했습니다. 시각 분석 경로가 필요합니다.");
    }
    const code = preview.generation_error?.code || preview.error?.code;
    const labels = {
      DOCUMENT_EXTRACTION_FAILED: "파일의 텍스트 또는 이미지를 추출하지 못했습니다. 원본은 보존되었으며 Wiki는 게시되지 않았습니다.",
      OFFICE_APPLICATION_UNAVAILABLE: "Word 또는 PowerPoint 설치와 COM 등록이 필요합니다. 원본은 보존됐으며 시각 정보를 처리하지 못해 Wiki는 게시되지 않았습니다.",
      UNSUPPORTED_MEDIA_TYPE: "지원하지 않는 파일 형식입니다.",
      PROTECTED_SOURCE_REQUIRES_AUTHORIZED_OPENER: "보호된 문서를 읽을 수 있는 승인된 연결이 필요합니다.",
      PROTECTED_SOURCE_PERMISSION_DENIED: "원본 문서를 읽을 권한이 없습니다.",
      WORK_LIMIT: "Wiki 입력 계획이 처리 한도를 초과했습니다. 원본은 보존됐으며 같은 설정으로 재업로드해도 해결되지 않을 수 있습니다.",
      MODEL_FAILURE: "Wiki 작성 모델 요청에 실패했습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      INVALID_OUTPUT: "모델의 작성 결과가 게시 조건을 만족하지 못했습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      TIMEOUT: "Wiki 처리 요청 시간이 초과됐습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      SOURCE_UNAVAILABLE: "Wiki 처리 중 오류가 발생했습니다. 원본 파일이 없다는 뜻은 아니며, 저장된 오류 기록으로 원인을 확인해야 합니다.",
      PROCESSING_FAILED: "Wiki 처리 중 오류가 발생했습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      EXTRACTION_FAILED: "문서 추출 작업이 실패했습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      AUTHORITY_UNAVAILABLE: "현재 계정 또는 게시 범위의 권한이 변경돼 Wiki 처리를 중단했습니다.",
      LEASE_LOST: "작업 소유권을 복구하지 못해 처리가 중단됐습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      EXPIRED: "Wiki 준비 기한이 만료됐습니다. 원본은 보존됐으며 게시하지 않았습니다.",
      CANCELLED: "Wiki 준비 작업이 취소됐습니다.",
    };
    const suffix = typeof code === "string" && /^[A-Z0-9_]{1,100}$/.test(code) ? ` (${code})` : "";
    return new Error((labels[code] || (preview.status === "expired"
      ? "Wiki 준비 기한이 만료되었습니다. 다시 업로드해 주세요."
      : "Wiki 준비에 실패하여 게시하지 않았습니다.")) + suffix);
  }

  function preparationLabel(preview) {
    const progress = preview?.generation_progress;
    if (preview?.generation_worker?.status === "recovering") {
      return "문서 처리 작업자 자동 복구 중 · 완료된 작성 결과는 유지됩니다 (추가 업로드하지 마세요)";
    }
    if (preview?.generation_worker?.status === "unavailable") return "문서 처리 작업자 중단 · 서버 복구 필요";
    const phases = {
      extraction_queued: "텍스트 추출 대기",
      extracting: "텍스트·표·이미지 추출 중",
      planning_queued: "텍스트 추출 완료 · Wiki 입력 계획 대기",
      planning: "텍스트 추출 완료 · Wiki 입력 계획 구성 중",
      writing_queued: "상세 Wiki 작성 대기",
      writing: "상세 Wiki 작성 중",
      assembling_queued: "Wiki 본문 작성 완료 · 최종 조립 대기",
      assembling: "Wiki 본문 작성 완료 · 최종 조립 중",
      recovery_pending: "작업 소유권 만료 · 자동 복구 대기 (추가 업로드하지 마세요)",
      ready: "Wiki 생성 완료 · 게시 준비",
      failed: "Wiki 준비 중단",
    };
    if (progress && Object.hasOwn(phases, progress.phase)) {
      const count = [progress.completed_batches, progress.total_batches].every(value => Number.isSafeInteger(value) && value >= 0)
        && progress.total_batches > 0 && progress.completed_batches <= progress.total_batches
        ? ` · 작성 ${progress.completed_batches}/${progress.total_batches}개 완료` : "";
      return phases[progress.phase] + count;
    }
    if (preview?.status === "ready" && ["information_authoring", "native_information_authoring", "pptx_role_information_authoring", "pdf_information_authoring", "pdf_visual_information_authoring", "office_hybrid_native_page_information_authoring", "pdf_source_continuation"].includes(preview.wiki_generation?.kind)
      && preview.wiki_generation.status === "pending") return "텍스트 추출 완료 · 상세 Wiki 작성 중";
    if (preview?.status === "ready" && preview.wiki_generation?.kind === "information_preservation"
      && preview.wiki_generation.status === "pending") return "텍스트 추출 완료 · Wiki 내용 보존·검수 중";
    if (preview?.status === "ready" && preview.wiki_generation?.status === "pending") return "텍스트 추출 완료 · Wiki 생성 중";
    return preview?.status === "extracting" ? "텍스트 추출 중" : "미리보기 준비 중";
  }

  function previewView(preview, spaces, preferredTargetMode = null, personalWiki = null, explicitTarget = null) {
    object(preview, "preview");
    if (preview.status !== "ready") throw new TypeError("A ready preview is required.");
    if (preview.wiki_generation && preview.publication_ready !== true) throw previewNotReadyError(preview);
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
        ...(preview.wiki_generation?.status === "ready" ? ["Wiki 생성 완료. 동일 본문인 경우 게시 단계에서 중복 항목을 만들지 않습니다."] : []),
        ...(preview.wiki_generation?.kind === "information_preservation" ? ["추출 원문과 작성 내용을 대조했습니다. AI 검수는 모든 정보의 의미적 보존이나 추출되지 않은 그림 내용을 보증하지 않습니다."] : []),
        ...(preview.wiki_generation?.kind === "information_authoring" ? ["전체 구간 작성과 원문 인용 일치를 기계적으로 확인했습니다. 별도 LLM 의미 검수는 수행하지 않았습니다."] : []),
        ...(["native_information_authoring", "pptx_role_information_authoring"].includes(preview.wiki_generation?.kind) ? ["전체 구간 작성과 원문 표 구조를 기계적으로 확인했습니다. 별도 LLM 의미 검수는 수행하지 않았습니다."] : []),
        ...(preview.wiki_generation?.status === "extraction_only" ? ["Wiki 생성은 완료되지 않았습니다. 보존된 추출 본문으로 게시합니다."] : []),
        ...(preview.wiki_generation?.kind === "pdf_information_authoring" ? ["PDF 전체 텍스트 구간 작성과 감지된 표의 셀 보존을 기계적으로 확인했습니다. 표 구조는 위치에서 추정하며 그림 해석이나 별도 LLM 의미 검수는 수행하지 않았습니다."] : []),
        ...(preview.wiki_generation?.kind === "pdf_visual_information_authoring" ? ["PDF 전체 텍스트 구간·감지된 표의 셀·페이지별 시각 관찰의 작성 기록을 기계적으로 확인했습니다. 표 구조와 그림 설명의 정확성을 보증하지 않으며 별도 LLM 의미 검수는 수행하지 않았습니다."] : []),
        ...(preview.wiki_generation?.kind === "pdf_source_continuation" ? ["완료된 작성 결과를 재사용하고 미완료 범위를 작성했습니다. 감지된 표 셀의 저장 기록을 확인했으며 별도 LLM 의미 검수는 수행하지 않았습니다."] : []),
        ...(preview.wiki_generation?.kind === "office_hybrid_native_page_information_authoring" ? ["Office 원문 텍스트와 페이지별 시각 관찰의 작성 기록을 대조했습니다. 추출되지 않은 내용이나 시각 해석의 의미 정확성을 보증하지 않으며 별도 LLM 의미 검수는 수행하지 않았습니다."] : []),
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
      publicationStatus: result.body_source === "preserved_wiki" ? "Wiki 게시 완료"
        : result.body_source === "authored_wiki" ? "Wiki 게시 완료"
        : result.body_source === "native_wiki" ? "Wiki 게시 완료"
        : result.body_source === "pptx_role_wiki" ? "Wiki 게시 완료"
        : ["pdf_wiki", "pdf_visual_wiki", "office_hybrid_wiki", "pdf_continuation_wiki"].includes(result.body_source) ? "Wiki 게시 완료"
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
      showPreparationError(error) {
        const body = element("div");
        body.className = "knowledge-publication-modal";
        body.setAttribute("aria-busy", "false");
        body.setAttribute("role", "alert");
        body.appendChild(element("p", error && error.message ? error.message : "Wiki 준비를 완료하지 못했습니다."));
        body.appendChild(element("p", "이번 업로드는 지식으로 게시되지 않았습니다. 내용을 확인한 뒤 다시 업로드할 수 있습니다."));
        modal.open({ title: "Wiki 준비 중단", body, closeable: true, footButtons: [{ label: "닫기" }] });
      },
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
        if (view.targets.some((target) => target.outcome === "created")) {
          body.appendChild(element("p", "원본과 Wiki가 저장되었습니다. 자동 등록 처리가 끝나면 문서 목록에 표시됩니다. 다시 업로드할 필요는 없습니다."));
        }
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
        body.appendChild(element("p", view.autoPublish
          ? `${view.succeededCount}개 게시 · ${view.failedCount}개 실패 · 전체 ${view.totalCount}개`
          : `${view.finishedCount} / ${view.totalCount}개 미리보기 준비`));
        if (view.autoPublish) body.appendChild(element("p", "파일별 Wiki 생성이 끝나면 선택한 위치에 자동 게시합니다."));
        if (view.duplicateCount) body.appendChild(element("p", `내용·파일명·게시 위치가 동일한 중복 선택 ${view.duplicateCount}개는 추가 업로드하지 않았습니다.`));
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
          title: `${view.autoPublish ? "파일 업로드·자동 게시" : "다중 파일 미리보기"} · ${view.totalCount}개`,
          body,
          closeable: false,
          footButtons: [{ label: "남은 작업 취소", onClick: async () => { await cancel(); return false; } }],
        });
      },
      showBatchPreview(view, commit, cancel) {
        const body = element("div");
        body.className = "knowledge-publication-batch";
        body.appendChild(element("p", `준비 ${view.readyCount}개 · 실패 ${view.failedCount}개`));
        if (view.duplicateCount) body.appendChild(element("p", `동일한 중복 선택 ${view.duplicateCount}개 제외`));
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
        if (view.duplicateCount) body.appendChild(element("p", `동일한 중복 선택 ${view.duplicateCount}개 제외 (추가 생성 요청 없음)`));
        if (view.cancellationUnconfirmedCount) body.appendChild(element("p", `취소 확인 실패 ${view.cancellationUnconfirmedCount}개: 서버 처리가 계속될 수 있습니다. 아래 버튼으로 취소를 다시 확인하며, 해당 파일은 이 창에서 자동 게시하지 않습니다.`));
        if (view.succeededCount) body.appendChild(element("p", "저장된 새 Wiki는 자동 등록 처리 후 목록에 표시됩니다. 이미 성공한 파일은 다시 업로드하지 마세요."));
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
            ...(view.failedCount ? [{ label: view.cancellationUnconfirmedCount ? "실패 재시도 / 취소 상태 다시 확인" : "실패 항목 다시 시도", onClick: async () => { await retry(); return false; } }] : []),
            { label: "닫기" },
          ],
        });
        notify(`다중 게시 완료: 성공 ${view.succeededCount}개, 실패 ${view.failedCount}개`, view.failedCount ? "error" : "ok");
      },
    });
  }

  async function deduplicateUploadSources(sources, cryptoRef = globalThis.crypto) {
    const unique = [], duplicates = [], identities = new Set();
    for (const source of sources) {
      const file = source?.file;
      let hash = null;
      if (source?.sourceType === "upload" && file && typeof file.arrayBuffer === "function" && cryptoRef?.subtle) {
        try {
          // Hash one file at a time; metadata alone must never discard a file.
          const digest = await cryptoRef.subtle.digest("SHA-256", await file.arrayBuffer());
          hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
        } catch (_) { /* Preserve the file if local hashing is unavailable. */ }
      }
      const target = source?.target;
      const destination = target ? [target.space_id, target.kind, target.capability,
        target.role_assignment_id, target.role_assignment_generation] : null;
      const key = hash === null ? null : JSON.stringify([hash, file.name, file.type || "", source.title || file.name,
        source.preferredTargetMode ?? null, source.personalSpaceId ?? null, destination]);
      if (key !== null && identities.has(key)) duplicates.push(String(file.name));
      else {
        if (key !== null) identities.add(key);
        unique.push(source);
      }
    }
    return { sources: unique, duplicateNames: duplicates };
  }

  function createBatchController({ transport, renderer, operationIdFactory, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), maxPolls = 900, preservationMaxPolls = 3600, concurrency = 3, cryptoRef = globalThis.crypto }) {
    if (!Number.isSafeInteger(preservationMaxPolls) || preservationMaxPolls < 1) throw new TypeError("Invalid preservation polling bound.");
    if (!transport || typeof transport.createPublicationPreview !== "function" || typeof transport.commitPublication !== "function" || typeof transport.getPublicationPreview !== "function" || typeof transport.cancelPublicationPreview !== "function") throw new TypeError("Canonical publication transport is required.");
    if (!renderer || ["showBatchProgress", "showBatchPreview", "showBatchCommitProgress", "showBatchResults"].some((name) => typeof renderer[name] !== "function")) throw new TypeError("Canonical batch publication renderer is required.");
    if (typeof operationIdFactory !== "function" || typeof sleep !== "function" || !Number.isSafeInteger(maxPolls) || maxPolls < 1 || !Number.isSafeInteger(concurrency) || concurrency < 1 || concurrency > 8) throw new TypeError("Batch publication controller options are invalid.");
    let current = null;
    let cancellationRequested = false;
    let running = false;

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
        statusLabel: entry.cancellationPending ? (entry.preview ? "취소 요청 확인 중" : "취소 요청 대기 · 파일 접수 확인 중")
          : entry.failureStage === "cancel" ? "취소 확인 실패"
          : entry.status === "preparing" ? preparationLabel(entry.preview)
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
        autoPublish: current.autoPublish,
        duplicateCount: current.duplicateNames.length,
        duplicateNames: Object.freeze(current.duplicateNames.slice()),
        cancelledCount: count("cancelled"),
        cancellationUnconfirmedCount: current.entries.filter(entry => entry.failureStage === "cancel").length,
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

    function cancellationUnconfirmed(entry) {
      entry.error = new Error("취소 확인 실패: 서버에서는 계속 처리될 수 있습니다. 새로 업로드하지 말고 취소 상태를 다시 확인하세요. (CANCELLATION_UNCONFIRMED)");
      entry.status = "failed";
      entry.failureStage = "cancel";
      entry.cancellationPending = false;
      return false;
    }

    function cancelEntry(entry, options = {}) {
      if (entry.cancellationPromise) return entry.cancellationPromise;
      // An unresolved create POST has no cancel API call to coalesce. Its
      // continuation must still be able to cancel when the preview arrives.
      if (!entry.preview && entry.status === "preparing" && !options.resolveUnknown) {
        return cancelEntryOnce(entry, options);
      }
      const pending = cancelEntryOnce(entry, options);
      entry.cancellationPromise = pending;
      return pending.finally(() => { if (entry.cancellationPromise === pending) entry.cancellationPromise = null; });
    }

    async function cancelEntryOnce(entry, { resolveUnknown = false } = {}) {
      // An in-flight commit may already have succeeded on the server. Do not
      // cancel its preview or mislabel it; wait for the idempotent receipt.
      if (entry.status === "publishing" || entry.status === "succeeded"
        || (entry.status === "failed" && entry.failureStage === "commit")) return false;
      if (entry.failureStage === "cancel" && !resolveUnknown) return false;
      if (!entry.preview) {
        if (entry.status === "waiting") {
          // No server request was started for this entry.
          entry.status = "cancelled";
          return true;
        }
        if (entry.status === "preparing" && !resolveUnknown) {
          // The create POST may still be in flight. Its continuation cancels
          // the exact returned preview; do not claim server cancellation yet.
          entry.cancellationPending = true;
          return false;
        }
        if (!resolveUnknown) return cancellationUnconfirmed(entry);
        try {
          // Resolve an ambiguous create using the SAME operation, never a new
          // source operation. Its only continuation is cancellation, not publish.
          entry.cancellationPending = true;
          entry.preview = await transport.createPublicationPreview({ ...entry.source, operationId: entry.previewOperationId });
        } catch (_) { return cancellationUnconfirmed(entry); }
      }
      if (entry.preview && ["queued", "extracting", "ready"].includes(entry.preview.status)) {
        entry.cancellationPending = true;
        try {
          const cancelled = await transport.cancelPublicationPreview({ operationId: entry.cancelOperationId || (entry.cancelOperationId = operationIdFactory()), previewId: entry.preview.preview_id });
          if (cancelled?.status !== "cancelled") return cancellationUnconfirmed(entry);
          entry.preview = { ...entry.preview, status: "cancelled" };
        } catch (_) { return cancellationUnconfirmed(entry); }
      }
      if (entry.preview.status !== "cancelled") {
        // A terminal failure or consumed preview is not a cancellation receipt.
        return cancellationUnconfirmed(entry);
      }
      entry.status = "cancelled";
      entry.error = null;
      entry.failureStage = null;
      entry.cancellationPending = false;
      return true;
    }

    async function cancelAll() {
      cancellationRequested = true;
      const cancelling = current.entries.map(entry => cancelEntry(entry));
      if (running) renderer.showBatchProgress(snapshot("cancellation_requested"), cancelAll);
      await Promise.all(cancelling);
      if (!running) renderer.showBatchResults(snapshot("cancelled"), retryFailed);
      else renderer.showBatchProgress(snapshot("cancellation_requested"), cancelAll);
      return Object.freeze({ cancelled: current.entries.every(entry => entry.status === "cancelled"), cancellationRequested: true });
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
        let preview = entry.reconnectPreview
          || await transport.createPublicationPreview({ ...entry.source, operationId: entry.previewOperationId });
        entry.reconnectPreview = null;
        entry.preview = preview;
        if (cancellationRequested) {
          await cancelEntry(entry);
          return;
        }
        for (let attempt = 0; isPreparing(preview); attempt += 1) {
          if (cancellationRequested) {
            await cancelEntry(entry);
            return;
          }
          const pollBound = ["information_preservation", "information_authoring", "native_information_authoring", "pptx_role_information_authoring", "pdf_information_authoring", "pdf_visual_information_authoring", "office_hybrid_native_page_information_authoring", "pdf_source_continuation"].includes(preview.wiki_generation?.kind) ? preservationMaxPolls : maxPolls;
          if (attempt >= pollBound) throw new Error("작업 상태 확인 대기시간이 초과됐습니다. 서버 작업은 계속될 수 있으므로 새로 업로드하지 말고 ‘실패 항목 다시 시도’로 기존 작업 상태를 확인하세요.");
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
        if (preview.status !== "ready" || preview.wiki_generation?.status === "unavailable") throw previewNotReadyError(preview);
        const personalWiki = entry.personalSpaceId === null ? null : Object.freeze({ displayName: entry.personalWikiDisplayName, spaceId: entry.personalSpaceId });
        entry.view = previewView(preview, spaces, entry.preferredTargetMode, personalWiki, entry.explicitTarget);
        entry.prepared = true;
        entry.status = "ready";
        entry.failureStage = null;
      } catch (error) {
        if (cancellationRequested) cancellationUnconfirmed(entry);
        else {
          entry.error = error;
          entry.status = "failed";
          entry.failureStage = "prepare";
        }
      } finally {
        renderer.showBatchProgress(snapshot("preparing"), cancelAll);
      }
    }

    async function commitEntry(entry, spaces) {
      if (cancellationRequested || entry.status !== "ready") return;
      entry.status = "publishing";
      entry.error = null;
      if (current.autoPublish) renderer.showBatchProgress(snapshot("publishing"), cancelAll);
      else renderer.showBatchCommitProgress(snapshot("publishing"));
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
        if (current.autoPublish) renderer.showBatchProgress(snapshot("publishing"), cancelAll);
        else renderer.showBatchCommitProgress(snapshot("publishing"));
      }
    }

    async function commitReady() {
      if (running) return snapshot("current");
      running = true;
      try {
      cancellationRequested = false;
      const ready = current.entries.filter((entry) => entry.status === "ready");
      await runPool(ready, (entry) => commitEntry(entry, current.spaces));
      renderer.showBatchResults(snapshot("completed"), retryFailed);
      return snapshot("completed");
      } finally { running = false; }
    }

    async function retryFailed() {
      if (running) return snapshot("current");
      running = true;
      try {
      cancellationRequested = false;
      const cancellationFailures = current.entries.filter(entry => entry.status === "failed" && entry.failureStage === "cancel");
      if (cancellationFailures.length) await runPool(cancellationFailures, entry => cancelEntry(entry, { resolveUnknown: true }));
      const failed = current.entries.filter((entry) => entry.status === "failed" && entry.failureStage !== "cancel");
      for (const entry of failed) {
        // Only an already retained PDF uses the new same-preview API. Office
        // and older hosts retain their existing preparation retry behavior.
        const pdf = ["pdf_information_authoring", "pdf_visual_information_authoring", "pdf_source_continuation"]
          .includes(entry.preview?.wiki_generation?.kind);
        if (entry.failureStage === "prepare" && pdf
            && typeof transport.probePdfPublicationResume === "function"
            && typeof transport.resumePdfPublicationPreview === "function") {
          try {
            const existing = await transport.getPublicationPreview(entry.preview.preview_id);
            entry.preview = existing;
            if (existing.status === "ready" && ["pending", "ready"].includes(existing.wiki_generation?.status)) {
              entry.resumeOperationId = null;
              entry.reconnectPreview = existing;
              entry.status = "waiting";
              continue;
            }
            if (await transport.probePdfPublicationResume(existing.preview_id)) {
              entry.resumeOperationId ||= operationIdFactory();
              entry.reconnectPreview = await transport.resumePdfPublicationPreview({
                previewId: existing.preview_id, operationId: entry.resumeOperationId,
              });
              // A confirmed response ends this explicit request intent. Only
              // an ambiguous failure keeps its ID; a later terminal retry is
              // a new finite request, never a reset of the earlier records.
              entry.resumeOperationId = null;
              entry.preview = entry.reconnectPreview;
              entry.status = "waiting";
              continue;
            }
          } catch (error) {
            // An authorization/state/network error is not permission to upload
            // again. Preserve the preview and operation for an explicit retry.
            entry.error = error;
            entry.status = "failed";
            continue;
          }
        }
        entry.status = entry.failureStage === "commit" ? "ready" : "waiting";
        // A terminal preparation failure cannot be retried by replaying its
        // completed operation. Ambiguous network errors retain the same ID.
        if (entry.failureStage === "prepare" && (["failed", "cancelled", "expired"].includes(entry.preview?.status)
          || ["failed", "cancelled", "stopped", "unavailable"].includes(entry.preview?.wiki_generation?.status))) {
          entry.previewOperationId = operationIdFactory();
          entry.preview = null;
          entry.prepared = false;
          entry.view = null;
        }
      }
      const prepareFailures = failed.filter((entry) => entry.failureStage === "prepare");
      if (current.autoPublish) {
        await runPool(failed, async (entry) => {
          if (entry.status === "waiting") await prepareEntry(entry, current.spaces);
          await commitEntry(entry, current.spaces);
        });
      } else {
        if (prepareFailures.length) await runPool(prepareFailures.filter(entry => entry.status === "waiting"), (entry) => prepareEntry(entry, current.spaces));
        await runPool(failed.filter(entry => entry.status === "ready"), entry => commitEntry(entry, current.spaces));
      }
      renderer.showBatchResults(snapshot(cancellationRequested ? "cancelled" : "completed"), retryFailed);
      return snapshot("completed");
      } finally { running = false; }
    }

    async function open(sources, { autoPublish = false } = {}) {
      if (typeof autoPublish !== "boolean") throw new TypeError("Invalid automatic publication option.");
      if (!Array.isArray(sources) || sources.length < (autoPublish ? 1 : 2)) throw new TypeError("Publication sources are required.");
      if (running) throw new Error("현재 파일 업로드가 진행 중입니다. 완료 후 다시 시도해 주세요.");
      running = true;
      try {
      cancellationRequested = false;
      const selected = await deduplicateUploadSources(sources, cryptoRef);
      const spacesPage = await transport.spaces();
      const spaces = (spacesPage.items || []).slice();
      const entries = selected.sources.map((raw, index) => {
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
          cancellationPromise: null,
          cancellationPending: false,
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
      current = { entries, spaces, targetLabel: null, autoPublish, duplicateNames: selected.duplicateNames };
      renderer.showBatchProgress(snapshot("preparing"), cancelAll);
      await runPool(entries, async (entry) => {
        await prepareEntry(entry, spaces);
        if (autoPublish) await commitEntry(entry, spaces);
      });
      if (cancellationRequested) {
        renderer.showBatchResults(snapshot("cancelled"), retryFailed);
        return Object.freeze({ ...snapshot("cancelled"), cancelled: current.entries.every(entry => entry.status === "cancelled"), cancellationRequested: true });
      }
      const firstReady = entries.find((entry) => entry.view);
      current.targetLabel = firstReady ? firstReady.view.options.find((option) => option.mode === firstReady.view.selectedMode)?.label || firstReady.view.selectedMode : null;
      if (autoPublish) {
        renderer.showBatchResults(snapshot("completed"), retryFailed);
        return snapshot("completed");
      }
      renderer.showBatchPreview(snapshot("preview"), commitReady, cancelAll);
      return Object.freeze({ cancel: cancelAll, commit: commitReady, state: snapshot("preview") });
      } finally { running = false; }
    }

    return Object.freeze({ open, state: () => current ? snapshot("current") : null });
  }

  function createController({ transport, renderer, operationIdFactory, sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)), maxPolls = 900, preservationMaxPolls = 3600 }) {
    if (!Number.isSafeInteger(preservationMaxPolls) || preservationMaxPolls < 1) throw new TypeError("Invalid preservation polling bound.");
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
        const pollBound = ["information_preservation", "information_authoring", "native_information_authoring", "pptx_role_information_authoring", "pdf_information_authoring", "pdf_visual_information_authoring", "office_hybrid_native_page_information_authoring", "pdf_source_continuation"].includes(preview.wiki_generation?.kind) ? preservationMaxPolls : maxPolls;
        if (attempt >= pollBound) throw new Error("작업 상태 확인 대기시간이 초과됐습니다. 서버 작업이 계속될 수 있으므로 새 업로드 전에 기존 작업 상태를 확인해야 합니다.");
        await sleep(1000);
        if (cancellationRequested) return null;
        preview = await transport.getPublicationPreview(preview.preview_id);
        if (isPreparing(preview) && renderer.showPending) renderer.showPending(preview, () => cancelPreview(preview));
      }
      if (preview.status === "cancelled") return null;
      if (preview.status !== "ready") throw previewNotReadyError(preview);
      if (preview.wiki_generation?.status === "unavailable") throw previewNotReadyError(preview);
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
        if (renderer.showPreparationError) renderer.showPreparationError(error);
        else if (renderer.showError) renderer.showError(error);
        throw error;
      }
    }

    return Object.freeze({ open, state: () => current });
  }

  return Object.freeze({ BOTH_WARNING, OUTCOME_LABELS, createBatchController, createController, createDomRenderer, deduplicateUploadSources, preparationLabel, previewNotReadyError, previewView, resultView });
});



