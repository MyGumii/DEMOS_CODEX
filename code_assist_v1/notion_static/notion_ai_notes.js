(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionAiNotes = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const sessions = new Map();
  let activeAudioSession = null;
  const escape = value => String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
  const sourceKey = props => JSON.stringify([props.transcript || "", props.notes || "", props.instructions || "", props.output_language || "",
    ...(props.comparison_enabled ? ["host_comparison", props.knowledge_space_id || "", props.knowledge_query || ""] : [])]);
  const audioActive = meeting => meeting && !meeting.disposed && !["idle", "stopped", "error"].includes(meeting.phase);
  // Fixed local recordings use twelve seconds, including old ten-second notes.
  // Speech-boundary mode remains available; reading never rewrites saved props.
  const captureSeconds = config => config?.local_only ? 12 : config?.chunk_seconds || 12;
  const captureMode = (config, props = {}) => config?.local_only && props.audio_chunk_mode !== "fixed" ? "speech" : "fixed";
  const summaryEditKey = props => JSON.stringify([props.result || null, props.generated_at || "", props.summary_edited_at || ""]);
  function editedSummary(props, text, at = new Date().toISOString()) {
    if (props.comparison_enabled || typeof props.result?.summary !== "string"
        || typeof text !== "string" || text.length > 12000 || text.includes("\0")) return null;
    return {...props, result: {...props.result, summary: text}, summary_manually_edited: true, summary_edited_at: at};
  }
  function canApplySummary(snapshot, props, provisional) {
    if (!provisional) return snapshot === sourceKey(props);
    const [transcript, notes, instructions, language = "", ...settings] = JSON.parse(snapshot);
    return String(props.transcript || "").startsWith(transcript) && (props.notes || "") === notes && (props.instructions || "") === instructions && (props.output_language || "") === language
      && JSON.stringify(settings) === JSON.stringify(JSON.parse(sourceKey(props)).slice(4));
  }

  function toMarkdown(props) {
    const result = props.result || {};
    const lines = ["## AI 노트"];
    if (result.summary) lines.push("### 요약", result.summary);
    if (result.decisions?.length) lines.push("### 결정 사항", ...result.decisions.map(item => `- ${item.text}`));
    if (result.action_items?.length) lines.push("### 할 일", ...result.action_items.map(item =>
      `- [${item.done ? "x" : " "}] ${item.task}${item.owner ? " · 담당: " + item.owner : ""}${item.due ? " · 기한: " + item.due : ""}`));
    if (props.notes) lines.push("### 메모", props.notes);
    if (props.transcript) lines.push("### 회의록 원문", props.transcript);
    if (props.translation_text) lines.push("### 원문 번역", props.translation_text);
    return lines.join("\n\n");
  }

  const exportLabels = Object.freeze({ all: "전체 노트", transcript: "원문", summary: "요약", notes: "메모", translation: "번역" });
  const translationFields = ["translation_text", "translation_source", "translation_language", "translation_model_name", "translation_config", "translation_scope_key", "translation_display_start"];
  const summaryFields = Object.freeze(["result", "summary_phase", "generated_source", "source_sha256", "model_name", "provider", "generated_at", "comparison_run_id", "comparison_state", "summary_manually_edited", "summary_edited_at"]);
  const languageOptions = [["", "지침에 따름 (기본 한국어)"], ["ko", "한국어"], ["en", "영어"], ["ja", "일본어"], ["zh", "중국어"]];
  const textValue = value => typeof value === "string" ? value : "";
  function hasClearContent(props, kind) {
    if (!Object.hasOwn(exportLabels, kind)) return false;
    if (kind === "transcript") return Boolean(textValue(props.transcript).length || props.audio_started_at);
    if (kind === "notes") return Boolean(textValue(props.notes).length);
    if (kind === "translation") return Boolean(props.translation_text);
    const summary = summaryFields.some(field => props[field] != null && props[field] !== "");
    return kind === "summary" ? summary : summary || hasClearContent(props, "transcript") || hasClearContent(props, "notes") || hasClearContent(props, "translation");
  }
  function clearedProps(props, kind) {
    if (!hasClearContent(props, kind)) return null;
    const next = { ...props };
    if (kind === "transcript" || kind === "all") {
      next.transcript = "";
      for (const field of ["audio_started_at", "audio_transcript_start", "audio_scope_invalid"]) delete next[field];
    }
    if (kind === "notes" || kind === "all") next.notes = "";
    if (kind === "summary" || kind === "all") for (const field of summaryFields) delete next[field];
    if (kind === "translation" || kind === "all") for (const field of translationFields) delete next[field];
    return next;
  }
  const resultItems = (props, key) => (Array.isArray(props.result?.[key]) ? props.result[key] : []).filter(item => item && typeof item === "object");
  function hasExportContent(props, kind) {
    if (!Object.hasOwn(exportLabels, kind)) return false;
    if (kind === "transcript" || kind === "notes") return Boolean(textValue(props[kind]).trim());
    if (kind === "translation") return Boolean(textValue(props.translation_text).trim());
    const summary = Boolean(textValue(props.result?.summary).trim()
      || resultItems(props, "decisions").some(item => textValue(item.text).trim())
      || resultItems(props, "action_items").some(item => textValue(item.task).trim()));
    return kind === "summary" ? summary : summary || hasExportContent(props, "transcript") || hasExportContent(props, "notes") || hasExportContent(props, "translation");
  }
  function exportFile(props, kind = "all", format = "md", date = new Date()) {
    if (!["txt", "md"].includes(format) || !hasExportContent(props, kind)) return null;
    // TXT source/note exports are exact, including whitespace and timestamps.
    // Escape raw HTML in Markdown without changing the canonical page content.
    const content = value => format === "md" ? textValue(value).replace(/[&<>]/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[ch])) : textValue(value);
    const heading = (title, level = 2) => format === "md" ? `${"#".repeat(level)} ${title}` : title;
    const lines = [];
    if (kind === "transcript" || kind === "notes" || kind === "translation") {
      if (format === "md") lines.push(heading(exportLabels[kind], 1));
      if (kind === "translation" && typeof globalThis.NotionNotesTranslation !== "undefined" && globalThis.NotionNotesTranslation.stale(props)) lines.push("[이전 번역] 원문 또는 언어가 변경되었습니다.");
      lines.push(content(kind === "translation" ? props.translation_text : props[kind]));
    } else {
      lines.push(heading(kind === "all" ? "AI 노트" : "AI 노트 요약", 1));
      if (hasExportContent(props, "summary")) {
        if (props.summary_manually_edited) lines.push("[직접 수정한 요약]");
        if (props.summary_phase === "provisional") lines.push("[잠정 요약] 회의 중 생성한 요약입니다.");
        if (props.generated_source && props.generated_source !== sourceKey(props)) lines.push("[이전 요약] 원문·메모·지침이 변경된 후 아직 다시 생성하지 않은 요약입니다.");
        if (textValue(props.result?.summary)) lines.push(heading("요약"), content(props.result.summary));
        const decisions = resultItems(props, "decisions").filter(item => textValue(item.text).trim());
        const actions = resultItems(props, "action_items").filter(item => textValue(item.task).trim());
        const quote = item => textValue(item.source_quote).trim() ? `\n  근거: ${content(item.source_quote).replace(/\n/g, "\n  ")}` : "";
        if (decisions.length) lines.push(heading("결정 사항"), decisions.map(item => `- ${content(item.text)}${quote(item)}`).join("\n"));
        if (actions.length) lines.push(heading("할 일"), actions.map(item =>
          `- [${item.done ? "x" : " "}] ${content(item.task)}${item.owner ? " · 담당: " + content(item.owner) : ""}${item.due ? " · 기한: " + content(item.due) : ""}${quote(item)}`).join("\n"));
      }
      if (kind === "all") {
        if (hasExportContent(props, "notes")) lines.push(heading("메모"), content(props.notes));
        if (hasExportContent(props, "transcript")) lines.push(heading("회의록 원문"), content(props.transcript));
        if (hasExportContent(props, "translation")) lines.push(heading("원문 번역"), content(props.translation_text));
      }
    }
    return { text: lines.join("\n\n"), mime: format === "md" ? "text/markdown;charset=utf-8" : "text/plain;charset=utf-8",
      filename: `AI노트_${exportLabels[kind].replaceAll(" ", "")}_${date.toISOString().replace(/[:.]/g, "-")}.${format}` };
  }

  function html(block, canEdit) {
    const props = block.props || {};
    const result = props.result || {};
    const list = (items, actions) => (Array.isArray(items) ? items : []).map((item, index) => `
      <li>${actions ? `<input type="checkbox" data-ai-done="${index}" aria-label="${escape(item.task)} 완료" ${item.done ? "checked" : ""} ${canEdit ? "" : "disabled"}>` : ""}
      <span>${escape(actions ? item.task : item.text)}${actions && (item.owner || item.due) ? `<small>${escape(item.owner || "담당 미지정")} · ${escape(item.due || "기한 미지정")}</small>` : ""}</span>
      <button type="button" class="ai-notes-source" data-ai-quote="${escape(item.source_quote)}" title="원문에서 근거 확인">근거</button></li>`).join("");
    return `<section class="ai-notes" aria-label="AI 회의 노트">
      <header><span class="ai-notes-symbol" aria-hidden="true">✧</span><strong>AI 노트</strong><span class="ai-notes-badge">${props.summary_phase === "provisional" ? "잠정 요약" : "회의 기록"}</span></header>
      <details class="ai-notes-download-settings"><summary>다운로드</summary><div class="ai-notes-export" role="group" aria-label="AI 노트 다운로드">
        <label>내용 <select data-ai-export-kind aria-label="다운로드 내용"><option value="all">전체 노트</option><option value="transcript">원문</option><option value="summary">요약·결정·할 일</option><option value="notes">메모</option><option value="translation">번역</option></select></label>
        <label>형식 <select data-ai-export-format aria-label="다운로드 형식"><option value="txt">텍스트 (.txt)</option><option value="md">Markdown (.md)</option></select></label>
        <button type="button" data-ai-export disabled>다운로드</button>
        <small data-ai-export-status role="status" aria-live="polite"></small>
      </div>
      </details>
      ${canEdit ? `<div class="ai-notes-audio">
        <div class="ai-notes-audio-buttons"><button type="button" data-ai-record disabled>소리 공유로 회의 시작</button>
          <button type="button" data-ai-pause hidden>일시정지</button><button type="button" data-ai-stop hidden>회의 종료</button>
          <button type="button" data-ai-retry-audio hidden>미전사 음성 재시도</button>
          <button type="button" data-ai-download-audio hidden>대기 음성 내려받기</button></div>
        <p data-ai-audio-status role="status" aria-live="polite">음성 API 설정 확인 중…</p>
        <p class="ai-notes-muted" data-ai-audio-latency hidden></p>
        <details class="ai-notes-audio-settings"><summary>음성 설정 · 파일 가져오기</summary>
        <label>소리 입력 <select data-ai-audio-source aria-label="소리 입력"><option value="system">시스템·탭 오디오</option><option value="mixed">시스템·탭 + 마이크</option><option value="microphone">마이크만</option></select></label>
        <label>음성 언어 <select data-ai-audio-language aria-label="음성 언어"><option value="">자동 감지</option><option value="ko">한국어</option><option value="en">영어</option><option value="ja">일본어</option><option value="zh">중국어</option></select></label>
        <label data-ai-audio-chunk-label hidden>음성 처리 간격 <select data-ai-audio-chunk aria-label="음성 처리 간격"><option value="speech">말끝 우선 · 10~20초 (기본)</option><option value="12">고정 · 12초</option></select></label>
        <label class="ai-notes-import">음성 파일 가져오기<input type="file" data-ai-audio-import accept=".wav,.mp3,.m4a,.mp4,.webm,.ogg,.flac,audio/*" disabled></label>
        <label class="ai-notes-consent"><input type="checkbox" data-ai-auto-summary checked><span data-ai-auto-summary-label>${props.comparison_enabled ? "회의 종료 후 비교 수집 (중간 요약 없음)" : "자동 요약 (회의 중 30초 간격·종료 후)"}</span></label>
        <p class="ai-notes-muted" data-ai-audio-provider></p>
        <p class="ai-notes-muted">Zoom/Webex 등 앱 종류와 관계없이 선택한 시스템·탭 소리를 수집합니다. 시작 시 브라우저 공유 창에서 ‘오디오 공유’를 선택하세요.</p>
        </details>
      </div>` : ""}
      <div class="ai-notes-tabs" role="tablist" aria-label="AI 노트 내용">
        <button type="button" role="tab" data-ai-tab="summary">요약</button>
        <button type="button" role="tab" data-ai-tab="notes">메모</button>
        <button type="button" role="tab" data-ai-tab="transcript">원문</button>
        <button type="button" role="tab" data-ai-tab="translation">번역</button>
      </div>
      ${globalThis.NotionNotesTranslation?.html(props, canEdit) || ""}
      <div data-ai-panel="summary" role="tabpanel" aria-label="요약">
        ${canEdit ? '<div class="ai-notes-panel-actions"><button type="button" class="ai-notes-clear" data-ai-clear="summary" disabled>요약 비우기</button></div>' : ""}
        ${props.comparison_enabled ? "<div class='ai-notes-empty'><strong>호스트 전용 비교 수집</strong><p>일반 요약과 지식 연동 요약은 이 화면이나 노트 본문에 저장하지 않습니다. 처리 상태만 표시합니다.</p></div>" : typeof result.summary === "string" ? `${canEdit
          ? `<textarea class="ai-notes-summary" data-ai-field="summary" aria-label="요약 직접 수정" maxlength="12000" placeholder="요약 내용을 직접 수정하세요.">${escape(result.summary)}</textarea>`
          : `<p class="ai-notes-summary">${escape(result.summary)}</p>`}
          <p class="ai-notes-muted" data-ai-summary-hint>${props.summary_manually_edited ? "직접 수정한 요약 · 자동 요약이 덮어쓰지 않습니다." : canEdit ? "직접 수정하면 자동 저장됩니다. 결정 사항·할 일·원문은 유지됩니다." : ""}</p>
          <h4>결정 사항</h4>${result.decisions?.length ? `<ul>${list(result.decisions, false)}</ul>` : "<p class='ai-notes-muted'>확인된 결정 사항 없음</p>"}
          <h4>할 일</h4>${result.action_items?.length ? `<ul>${list(result.action_items, true)}</ul>` : "<p class='ai-notes-muted'>확인된 할 일 없음</p>"}
          <p class="ai-notes-muted">${escape(props.model_name || "AI")} · ${escape(props.generated_at ? new Date(props.generated_at).toLocaleString() : "")} · AI가 작성한 초안입니다. 근거를 확인하세요.</p>` :
          "<div class='ai-notes-empty'><strong>회의의 핵심을 한곳에</strong><p>원문에 회의록을 붙여넣거나 메모를 작성한 뒤 요약을 생성하세요.</p></div>"}
        <p class="ai-notes-stale" data-ai-stale hidden>원문·메모가 변경되었습니다. 요약을 다시 생성하세요.</p>
      </div>
      <div data-ai-panel="notes" role="tabpanel" aria-label="메모" hidden>
        ${canEdit ? '<div class="ai-notes-panel-actions"><button type="button" class="ai-notes-clear" data-ai-clear="notes" disabled>메모 비우기</button></div>' : ""}
        <textarea data-ai-field="notes" aria-label="회의 메모" maxlength="12000" placeholder="안건, 배경, 직접 작성한 회의 메모" ${canEdit ? "" : "readonly"}>${escape(props.notes)}</textarea>
      </div>
      <div data-ai-panel="transcript" role="tabpanel" aria-label="원문" hidden>
        ${canEdit ? '<div class="ai-notes-panel-actions"><button type="button" class="ai-notes-clear" data-ai-clear="transcript" disabled>원문 비우기</button></div>' : ""}
        <textarea data-ai-field="transcript" aria-label="회의록 원문" maxlength="80000" placeholder="회의록 또는 전사된 텍스트를 붙여넣으세요. (최대 80,000자)" ${canEdit ? "" : "readonly"}>${escape(props.transcript)}</textarea>
        ${canEdit ? `<label class="ai-notes-import">텍스트 파일 가져오기<input type="file" data-ai-import accept=".txt,.md,.vtt,.srt,text/plain,text/markdown"></label>` : ""}
        <p class="ai-notes-muted">음성 전사 결과도 이 원문에 누적됩니다. 전사가 끝나면 직접 수정할 수 있습니다.</p>
      </div>
      ${canEdit ? `<details class="ai-notes-options"><summary>요약 설정</summary>
        <label class="ai-notes-consent"><input type="checkbox" data-ai-comparison ${props.comparison_enabled ? "checked" : ""} disabled> Second Brain 비교 수집 · 두 요약 모두 호스트 전용</label>
        <p class="ai-notes-muted" data-ai-comparison-availability>호스트 비교 수집 설정 확인 중…</p>
        <div data-ai-comparison-options ${props.comparison_enabled ? "" : "hidden"}>
          <label>참고 Wiki<select data-ai-knowledge-space aria-label="비교에 사용할 Wiki"><option value="">Wiki 선택</option>${props.knowledge_space_id ? `<option value="${escape(props.knowledge_space_id)}" selected>선택한 Wiki (확인 중)</option>` : ""}</select></label>
          <button type="button" data-ai-more-wikis hidden>Wiki 더 불러오기</button>
          <label>지식 검색어 (선택)<input type="text" data-ai-knowledge-query aria-label="비교 지식 검색어" maxlength="2000" placeholder="비우면 회의 전체 원문에서 검색어를 추출합니다" value="${escape(props.knowledge_query || "")}"></label>
          <p class="ai-notes-muted">원문·메모·지침·참고 지식과 두 요약을 품질 비교용으로 호스트에 암호화 보관합니다. 동일 모델로 두 번 이상 생성할 수 있습니다. 노트 비우기는 호스트 비교 기록을 삭제하지 않습니다.</p>
        </div>
        <label>모델<select data-ai-model aria-label="AI 노트 모델"><option value="">자동 · Gemma4 → Qwen3.6</option></select></label>
        <label>번역·요약 언어<select data-ai-output-language aria-label="번역·요약 언어">${languageOptions.map(([value, label]) => `<option value="${value}" ${(props.output_language || "") === value ? "selected" : ""}>${label}</option>`).join("")}<option value="custom" ${languageOptions.some(([value]) => value === (props.output_language || "")) ? "" : "selected"}>직접 입력 · 다른 언어/병기</option></select></label>
        <label data-ai-language-custom-label ${languageOptions.some(([value]) => value === (props.output_language || "")) ? "hidden" : ""}>원하는 언어<input type="text" data-ai-language-custom aria-label="직접 지정할 요약 언어" maxlength="80" placeholder="예: 프랑스어, 한국어와 영어 병기" value="${escape(languageOptions.some(([value]) => value === (props.output_language || "")) ? "" : props.output_language)}"></label>
        <p class="ai-notes-muted">원문은 보존하고 요약·결정·할 일을 선택한 언어로 정리합니다. 원문 전체 번역은 아닙니다.</p>
        <label>요약 지침<textarea data-ai-field="instructions" aria-label="AI 노트 요약 지침" maxlength="2000" placeholder="예: 결정 사항과 다음 주 할 일을 중심으로 정리">${escape(props.instructions)}</textarea></label>
        <p class="ai-notes-muted">${props.comparison_enabled ? "일반 요약은 원문·메모·지침을, 지식 연동 요약은 여기에 선택한 Wiki의 관련 지식을 추가로 전달합니다." : "선택한 모델에 이 노트의 원문·메모·요약 지침을 전달합니다."}</p></details>
        <footer><button type="button" class="ai-notes-generate" data-ai-generate>${props.comparison_enabled ? "비교 수집 시작" : result.summary ? "요약 다시 생성" : "요약 생성"}</button><button type="button" class="ai-notes-clear" data-ai-clear="all" disabled>전체 내용 비우기</button><span data-ai-status role="status" aria-live="polite"></span></footer>
        <p class="ai-notes-muted" data-ai-clear-hint>비우기 전에 필요한 내용은 다운로드하세요. 모델·요약 설정은 유지됩니다.</p>` : ""}
    </section>`;
  }

  function bind(container, adapter) {
    const key = `${adapter.scope.page_id}:${adapter.blockId}`;
    if (!sessions.has(key)) sessions.set(key, { tab: adapter.getBlock()?.props?.result ? "summary" : "transcript", busy: false, message: "", views: new Map() });
    const session = sessions.get(key);
    for (const node of session.views.keys()) if (!node.isConnected) session.views.delete(node);
    const alive = () => adapter.getBlock()?.type === "ai_notes";
    const editable = () => alive() && adapter.canEdit();
    const persist = () => {
      const pending = Promise.resolve(session.persisting).catch(() => false).then(() => adapter.save());
      session.persisting = pending;
      return pending;
    };
    const mutate = fn => {
      if (!editable()) return false;
      const block = adapter.getBlock();
      block.props ||= {};
      fn(block.props);
      block.content = toMarkdown(block.props);
      adapter.changed();
      return true;
    };
    const status = message => {
      session.message = message;
      const node = container.querySelector("[data-ai-status]");
      if (node) node.textContent = message;
      for (const view of session.views.keys()) {
        const target = view.querySelector("[data-ai-status]");
        if (target) target.textContent = message;
      }
    };
    const sync = () => {
      container.querySelectorAll("[data-ai-tab]").forEach(button => {
        const selected = button.dataset.aiTab === session.tab;
        button.setAttribute("aria-selected", String(selected));
        button.tabIndex = selected ? 0 : -1;
      });
      container.querySelectorAll("[data-ai-panel]").forEach(panel => { panel.hidden = panel.dataset.aiPanel !== session.tab; });
      const button = container.querySelector("[data-ai-generate]");
      if (button) {
        button.disabled = session.busy || session.clearing || session.comparisonPending || !editable();
        button.textContent = session.busy ? "처리 요청 중…" : session.comparisonPending ? "호스트 비교 처리 중…" : adapter.getBlock()?.props?.comparison_enabled ? "비교 수집 시작" : (adapter.getBlock()?.props?.result ? "요약 다시 생성" : "요약 생성");
      }
      const stale = container.querySelector("[data-ai-stale]");
      const props = adapter.getBlock()?.props || {};
      if (stale) stale.hidden = props.comparison_enabled || !props.result || props.generated_source === sourceKey(props);
      const comparison = container.querySelector("[data-ai-comparison]");
      if (comparison) {
        comparison.checked = Boolean(props.comparison_enabled);
        comparison.disabled = !session.comparisonAvailable || !editable() || clearBlocked();
      }
      container.querySelectorAll("[data-ai-done]").forEach(checkbox => { checkbox.disabled = session.busy || !editable(); });
      status(session.message);
      syncAudio();
      syncExport();
      syncClear();
      const summaryInput = container.querySelector('[data-ai-field="summary"]');
      if (summaryInput) {
        summaryInput.readOnly = !summaryEditable();
        const text = props.result?.summary || "";
        if (summaryInput.value !== text) summaryInput.value = text;
      }
      const summaryHint = container.querySelector("[data-ai-summary-hint]");
      if (summaryHint) summaryHint.textContent = !editable() ? (props.summary_manually_edited ? "직접 수정한 요약" : "") : props.summary_manually_edited
        ? "직접 수정한 요약 · 자동 요약이 덮어쓰지 않습니다. 다시 생성하려면 아래 버튼을 누르세요."
        : session.busy || session.finishingAudio ? "요약 처리 중입니다. 완료 후 직접 수정할 수 있습니다."
        : "직접 수정하면 자동 저장됩니다. 결정 사항·할 일·원문은 유지됩니다.";
      session.translationController?.update();
    };
    const repaintView = () => {
      if (!alive() || !container.isConnected) return;
      const focused = container.contains(document.activeElement) ? document.activeElement : null;
      const field = focused?.dataset?.aiField;
      const selection = field ? [focused.selectionStart, focused.selectionEnd] : null;
      container.querySelector(".ai-notes").outerHTML = html(adapter.getBlock(), adapter.canEdit());
      bind(container, adapter);
      if (field) { const input = container.querySelector(`[data-ai-field="${field}"]`); input?.focus({ preventScroll: true }); if (input && selection) input.setSelectionRange(...selection); }
    };
    session.views.set(container, { sync, repaint: repaintView, adapter });
    const repaint = () => {
      for (const [node, view] of [...session.views]) {
        if (node.isConnected) view.repaint();
        else session.views.delete(node);
      }
    };
    session.autoSummary ??= true;
    function syncAudio() {
      const meeting = session.meeting;
      const active = audioActive(meeting);
      const pending = Boolean(meeting?.queue.length);
      const ready = Boolean(session.audioConfig?.enabled && editable() && !session.startingAudio && !session.finishingAudio && !session.clearing && !session.importingText);
      const set = (selector, fn) => { const node = container.querySelector(selector); if (node) fn(node); };
      set("[data-ai-auto-summary]", node => { node.checked = session.autoSummary; });
      set("[data-ai-audio-source]", node => { node.value = session.audioSource || "system"; node.disabled = active || pending || session.startingAudio; });
      set("[data-ai-audio-language]", node => {
        const pairMode = globalThis.NotionNotesTranslation?.isPair(adapter.getBlock()?.props || {});
        node.value = pairMode ? "" : session.audioLanguage || "";
        node.disabled = pairMode || active || pending || session.startingAudio;
        node.title = pairMode ? "두 언어 통역은 음성 언어를 자동 감지합니다." : "";
      });
      set("[data-ai-audio-chunk-label]", node => { node.hidden = !session.audioConfig?.local_only; });
      set("[data-ai-audio-chunk]", node => {
        node.value = captureMode(session.audioConfig, adapter.getBlock()?.props) === "speech" ? "speech" : String(captureSeconds(session.audioConfig));
        node.disabled = !editable() || active || pending || session.startingAudio || session.finishingAudio;
      });
      set("[data-ai-audio-latency]", node => {
        node.hidden = !session.audioConfig?.local_only && !meeting;
        const seconds = meeting?.options.chunkSeconds || captureSeconds(session.audioConfig, adapter.getBlock()?.props);
        const mode = meeting?.options.boundaryMode || captureMode(session.audioConfig, adapter.getBlock()?.props);
        node.textContent = (mode === "speech" ? "말끝 우선 · 10초 이상 수집 후 0.8초 무음 · 최대 20초" : `음성 구간 ${seconds}초`)
          + (meeting?.lastChunkSeconds ? ` · 최근 구간 ${meeting.lastChunkSeconds.toFixed(1)}초` : "")
          + (meeting?.lastTranscribeMs != null ? ` · 최근 전사 요청 ${(meeting.lastTranscribeMs / 1000).toFixed(1)}초` : "")
          + (meeting?.lastSaveMs != null ? ` · 원문 저장 ${(meeting.lastSaveMs / 1000).toFixed(1)}초` : "")
          + (pending ? ` · 대기 ${meeting.queue.length}구간` : "")
          + (mode === "speech" ? " · 무음 기준이며 의미상 문장 완성을 보장하지 않습니다. 결과는 전사·번역 응답 후 표시됩니다." : " · 이 길이로 음성을 모아 처리합니다. 결과는 전사·번역 응답 후 표시됩니다.");
      });
      set("[data-ai-record]", node => { node.disabled = !ready || active || pending; });
      set("[data-ai-audio-import]", node => { node.disabled = !ready || active || pending; });
      set("[data-ai-import]", node => { node.disabled = active || pending || session.clearing || session.importingText; });
      set('[data-ai-field="transcript"]', node => { node.readOnly = !editable() || active || pending; });
      set("[data-ai-pause]", node => { node.hidden = !["recording", "paused"].includes(meeting?.phase); node.textContent = meeting?.phase === "paused" ? "녹음 재개" : "일시정지"; });
      set("[data-ai-stop]", node => { node.hidden = !active; node.disabled = meeting?.phase === "stopping"; });
      set("[data-ai-retry-audio]", node => { node.hidden = meeting?.phase !== "error" || !pending; });
      set("[data-ai-download-audio]", node => { node.hidden = meeting?.phase !== "error" || !pending; });
      set("[data-ai-audio-status]", node => {
        node.textContent = meeting?.message ? `${meeting.message}${pending ? ` · 대기 ${meeting.queue.length}구간` : ""}` :
          session.audioConfig?.enabled ? `${session.audioConfig.label || "음성 API 설정됨"} · 파일은 최대 20 MiB${session.audioConfig.max_audio_seconds ? ` · ${session.audioConfig.max_audio_seconds}초 이하` : ""}` : (session.audioConfig?.reason || "음성 API 설정 확인 중…");
      });
      set("[data-ai-audio-provider]", node => {
        node.textContent = session.audioConfig?.local_only ? "음성은 이 PC에서 전사합니다. AI 처리 시 전사 텍스트가 선택한 모델로 전송됩니다." : "";
      });
    }
    const syncViews = () => { for (const view of session.views.values()) view.sync(); };
    const clearBlocked = () => Boolean(session.busy || session.translationController?.busy || session.comparisonPending || session.clearing || session.importingText || session.startingAudio || session.finishingAudio || audioActive(session.meeting) || session.meeting?.queue.length);
    const summaryEditable = () => editable() && !adapter.getBlock()?.props?.comparison_enabled
      && !session.busy && !session.clearing && !session.comparisonPending && !session.finishingAudio;
    function syncClear() {
      const blocked = clearBlocked();
      const props = adapter.getBlock()?.props || {};
      container.querySelectorAll("[data-ai-clear]").forEach(button => {
        button.disabled = !editable() || blocked || !hasClearContent(props, button.dataset.aiClear);
      });
      const hint = container.querySelector("[data-ai-clear-hint]");
      if (hint) hint.textContent = blocked ? "녹음·전사·요약·파일 처리와 저장이 끝난 뒤 비울 수 있습니다. 미전사 음성이 있으면 먼저 재시도하세요." :
        "비우기 전에 필요한 내용은 다운로드하세요. 모델·요약 설정은 유지됩니다.";
    }
    container.querySelectorAll("[data-ai-clear]").forEach(button => button.addEventListener("click", async event => {
      event.stopPropagation();
      const kind = button.dataset.aiClear;
      if (!container.isConnected || !editable() || clearBlocked() || !hasClearContent(adapter.getBlock().props || {}, kind)) return;
      const details = {
        transcript: "원문을 비울까요? 메모와 기존 요약은 유지되며 요약은 이전 요약으로 표시됩니다.",
        notes: "메모를 비울까요? 원문과 기존 요약은 유지되며 요약은 이전 요약으로 표시됩니다.",
        summary: "요약·결정 사항·할 일을 비울까요? 원문과 메모는 유지됩니다.",
        all: "이 AI 노트의 원문·메모·요약·결정 사항·할 일·번역을 모두 비울까요?",
      };
      if (!window.confirm(`${details[kind]}\n모델·언어·요약 지침은 유지됩니다. 필요한 내용은 먼저 다운로드하세요.`)) return;
      // Recheck the current adapter after the confirmation, including permission and work state.
      if (!container.isConnected || !editable() || clearBlocked()) return;
      const next = clearedProps(adapter.getBlock().props || {}, kind);
      if (!next) return;
      session.clearing = true;
      if (kind === "all" || kind === "transcript") session.translationController?.stop();
      session.exportMessage = "";
      try {
        mutate(props => {
          for (const field of Object.keys(props)) if (!Object.hasOwn(next, field)) delete props[field];
          Object.assign(props, next);
        });
        status("내용 비우기 저장 중…"); repaint();
        status(await persist() ? "내용을 비우고 저장했습니다." : "화면의 내용은 비웠지만 저장하지 못했습니다. 새로고침하지 말고 페이지 저장 상태를 확인하세요.");
      } catch (_error) {
        status("비우기 변경을 저장하지 못했습니다. 새로고침하지 말고 페이지 저장 상태를 확인하세요.");
      } finally { session.clearing = false; syncViews(); }
    }));
    function syncExport() {
      session.exportKind ||= "all";
      session.exportFormat ||= "txt";
      const props = adapter.getBlock()?.props || {};
      const kindSelect = container.querySelector("[data-ai-export-kind]");
      const formatSelect = container.querySelector("[data-ai-export-format]");
      const button = container.querySelector("[data-ai-export]");
      const message = container.querySelector("[data-ai-export-status]");
      if (kindSelect) kindSelect.value = session.exportKind;
      if (formatSelect) formatSelect.value = session.exportFormat;
      const available = alive() && hasExportContent(props, session.exportKind);
      if (button) button.disabled = !available;
      if (message) message.textContent = !available ? "선택한 내용이 아직 없습니다." :
        (session.exportMessage || (audioActive(session.meeting) || session.meeting?.queue.length || session.busy
          ? "처리 중입니다. 지금까지 반영된 내용을 내려받습니다." : "현재 화면의 내용으로 파일을 만듭니다."));
    }
    container.querySelector("[data-ai-export-kind]")?.addEventListener("change", event => {
      session.exportKind = event.target.value; session.exportMessage = ""; syncViews();
    });
    container.querySelector("[data-ai-export-format]")?.addEventListener("change", event => {
      session.exportFormat = event.target.value; session.exportMessage = ""; syncViews();
    });
    container.querySelector("[data-ai-export]")?.addEventListener("click", event => {
      event.stopPropagation();
      if (!alive() || !container.isConnected) return;
      const file = exportFile(adapter.getBlock().props || {}, session.exportKind, session.exportFormat);
      if (!file) { syncExport(); return; }
      let url;
      const link = document.createElement("a");
      try {
        // Windows text editors reliably recognize Korean with an explicit UTF-8 BOM.
        url = URL.createObjectURL(new Blob(["\uFEFF", file.text], { type: file.mime }));
        link.href = url; link.download = file.filename; link.hidden = true;
        document.body.appendChild(link); link.click();
        session.exportMessage = "다운로드를 요청했습니다. 브라우저 다운로드 목록을 확인하세요.";
      } catch (_error) {
        session.exportMessage = "파일을 내려받지 못했습니다. 브라우저 다운로드 설정을 확인하세요.";
      } finally {
        link.remove();
        if (url) setTimeout(() => URL.revokeObjectURL(url), 30000);
        syncExport();
      }
    });
    const requestAutoSummary = () => {
      if (session.autoSummary && !adapter.getBlock()?.props?.comparison_enabled && editable() && audioActive(session.meeting) && ["recording", "paused"].includes(session.meeting.phase)) generate("provisional");
    };
    function newMeeting() {
      if (!editable() || session.clearing || session.importingText || !session.audioConfig?.enabled || !session.audioConsent) return null;
      if (activeAudioSession && activeAudioSession !== session && (activeAudioSession.startingAudio || activeAudioSession.finishingAudio || audioActive(activeAudioSession.meeting) || activeAudioSession.meeting?.queue.length)) {
        status("다른 AI 노트의 녹음·전사를 먼저 종료해 주세요."); return null;
      }
      if (session.startingAudio || session.finishingAudio || audioActive(session.meeting) || session.meeting?.queue.length) return null;
      session.startingAudio = true; activeAudioSession = session; syncViews();
      session.meeting?.dispose().catch(() => {});
      if (!editable()) { session.startingAudio = false; syncViews(); return null; }
      clearInterval(session.audioWatch); clearInterval(session.summaryTimer);
      activeAudioSession = session;
      let transcriptStarted = false;
      const beginTranscript = () => {
        if (transcriptStarted) return;
        mutate(value => {
          value.audio_started_at = meeting.startedAt || new Date().toISOString();
          value.audio_transcript_start = (value.transcript || "").length;
          delete value.audio_scope_invalid;
          value.translation_include_history = false;
        });
        transcriptStarted = true;
        // Cancel an older recording's pending translation; preserve the user's
        // auto switch, but do not send the old transcript as the new meeting.
        session.translationController?.sourceChanged();
      };
      const meeting = new window.NotionMeetingAudio.Meeting({
        valid: editable,
        captureFactory: window.NotionMeetingAudio.sourceFactory(session.audioSource || "system"),
        // Batch enough speech for sentence context, independently of translation
        // checkbox order. Existing short-interval settings normalize to 12 seconds.
        chunkSeconds: captureSeconds(session.audioConfig, adapter.getBlock()?.props),
        boundaryMode: captureMode(session.audioConfig, adapter.getBlock()?.props),
        onStarted: beginTranscript,
        transcribe: (item, signal) => adapter.uploadAudio(item, session.audioConsent, signal,
          globalThis.NotionNotesTranslation?.audioLanguage(adapter.getBlock()?.props || {}, session.audioLanguage || "") ?? session.audioLanguage ?? ""),
        onText: (text, item) => {
          if (!text.trim()) return;
          if (!editable()) throw new Error("페이지 편집 권한이 변경되었습니다.");
          beginTranscript(); // Imported audio has no capture start callback.
          const props = adapter.getBlock().props;
          const stamp = item.duration ? `[${Math.floor(item.offset / 60).toString().padStart(2, "0")}:${Math.floor(item.offset % 60).toString().padStart(2, "0")}]` : "[음성 파일]";
          const next = [props.transcript || "", `${stamp} ${text}`].filter(Boolean).join("\n");
          if (next.length > 80000) throw new Error("회의록이 80,000자에 도달했습니다. 새 AI 노트에서 회의를 이어가세요.");
          mutate(value => { value.transcript = next; });
          for (const view of session.views.keys()) {
            const input = view.querySelector('[data-ai-field="transcript"]');
            if (input) input.value = next;
          }
        },
        persist,
        onCommitted: item => { session.translationController?.sourceChanged(); if (item.result.text && meeting.received === 1) requestAutoSummary(); },
        onState: state => { if (state.phase === "error") clearInterval(session.summaryTimer); syncViews(); },
        onStopped: async () => {
          clearInterval(session.audioWatch); clearInterval(session.summaryTimer);
          if (!session.autoSummary) {
            meeting.notify("전사 완료");
            return;
          }
          session.finishingAudio = true; meeting.notify("전사 완료 · AI 처리 요청 중…");
          session.finishPromise = generate("final");
          try { await session.finishPromise; }
          finally { session.finishingAudio = false; session.finishPromise = null; meeting.notify(adapter.getBlock()?.props?.comparison_enabled ? "전사 완료 · 비교 처리 상태는 아래에서 확인하세요." : "전사 완료 · 요약 결과는 아래에서 확인하세요."); }
        },
      });
      session.meeting = meeting;
      session.startingAudio = false;
      session.summaryTimer = setInterval(requestAutoSummary, 30000);
      session.audioWatch = setInterval(() => {
        if (!editable()) {
          clearInterval(session.audioWatch); clearInterval(session.summaryTimer);
          meeting.notify("페이지 또는 편집 권한 변경으로 녹음을 중단했습니다.");
          meeting.dispose();
        }
      }, 1000);
      return meeting;
    }
    container.querySelector("[data-ai-auto-summary]")?.addEventListener("change", event => { session.autoSummary = event.target.checked; session.autoSummaryChosen = true; syncViews(); });
    container.querySelector("[data-ai-audio-source]")?.addEventListener("change", event => { session.audioSource = event.target.value; syncViews(); });
    container.querySelector("[data-ai-audio-language]")?.addEventListener("change", event => { session.audioLanguage = event.target.value; syncViews(); });
    container.querySelector("[data-ai-audio-chunk]")?.addEventListener("change", event => {
      if (!editable() || !session.audioConfig?.local_only || session.startingAudio || session.finishingAudio
          || audioActive(session.meeting) || session.meeting?.queue.length) { syncViews(); return; }
      const seconds = Number(event.target.value);
      if (event.target.value === "speech") mutate(props => { props.audio_chunk_mode = "speech"; });
      else if (seconds === 12) mutate(props => { props.audio_chunk_mode = "fixed"; props.audio_chunk_seconds = 12; });
      syncViews();
    });
    container.querySelector("[data-ai-record]")?.addEventListener("click", async () => {
      session.audioConsent = true; // Explicit input-start action; not an assertion of third-party consent.
      const meeting = newMeeting();
      if (meeting) { session.tab = "transcript"; syncViews(); await meeting.start(); }
    });
    container.querySelector("[data-ai-pause]")?.addEventListener("click", () => {
      if (session.meeting?.phase === "paused") session.meeting.resume(); else session.meeting?.pause();
    });
    container.querySelector("[data-ai-stop]")?.addEventListener("click", () => session.meeting?.stop());
    container.querySelector("[data-ai-retry-audio]")?.addEventListener("click", () => session.meeting?.retry());
    container.querySelector("[data-ai-download-audio]")?.addEventListener("click", () => {
      for (const item of session.meeting?.queue || []) {
        const url = URL.createObjectURL(item.blob);
        const link = document.createElement("a"); link.href = url;
        link.download = item.blob.name || `meeting-pending-${item.id}.wav`;
        link.click(); setTimeout(() => URL.revokeObjectURL(url), 30000);
      }
    });
    container.querySelector("[data-ai-audio-import]")?.addEventListener("change", async event => {
      const file = event.target.files?.[0]; event.target.value = "";
      if (!file) return;
      if (!file.size || file.size > (session.audioConfig?.max_audio_bytes || 20 * 1024 * 1024)) { status("20 MiB 이하의 오디오 파일을 선택하세요."); return; }
      session.audioConsent = true; // Explicit file-selection action authorizes this upload.
      const meeting = await newMeeting();
      if (meeting) { session.tab = "transcript"; meeting.enqueue(file); await meeting.stop(); }
    });
    container.querySelectorAll("[data-ai-tab]").forEach(button => {
      button.addEventListener("click", () => { session.tab = button.dataset.aiTab; sync(); });
      button.addEventListener("keydown", event => {
        const tabs = [...container.querySelectorAll("[data-ai-tab]")];
        const index = tabs.indexOf(button);
        const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
        if (!offset && !["Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = tabs[event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + offset + tabs.length) % tabs.length];
        next.click(); next.focus();
      });
    });
    container.querySelectorAll("[data-ai-field]").forEach(input => {
      input.addEventListener("input", () => {
        if (input.dataset.aiField === "summary") {
          if (!summaryEditable()) { sync(); return; }
          const next = editedSummary(adapter.getBlock().props, input.value);
          if (!next) { status("요약은 12,000자 이하로 입력하세요."); sync(); return; }
          mutate(props => { Object.assign(props, next); });
          status("요약 수정됨 · 자동 저장 대기 중…");
          clearTimeout(session.summarySaveTimer);
          session.summarySaveTimer = setTimeout(saveEditedSummary, 1000);
        } else mutate(props => {
          props[input.dataset.aiField] = input.value;
          if (input.dataset.aiField === "transcript") { delete props.audio_transcript_start; props.audio_scope_invalid = true; }
        });
        if (input.dataset.aiField === "transcript") session.translationController?.sourceChanged();
        sync();
      });
      input.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault();
          if (input.dataset.aiField === "summary") saveEditedSummary(); else adapter.save();
        }
      });
      if (input.dataset.aiField === "summary") input.addEventListener("blur", saveEditedSummary);
    });
    async function saveEditedSummary() {
      clearTimeout(session.summarySaveTimer);
      if (!editable() || !adapter.getBlock()?.props?.summary_manually_edited) return;
      const snapshot = summaryEditKey(adapter.getBlock().props);
      status("수정한 요약 저장 중…");
      const saved = await Promise.resolve().then(persist).catch(() => false);
      if (!editable() || session.busy || summaryEditKey(adapter.getBlock().props) !== snapshot) return;
      status(saved ? "수정한 요약 저장됨" : "요약 수정은 화면에 있지만 저장하지 못했습니다. 새로고침하지 말고 Ctrl+S로 다시 저장하세요.");
    }
    container.querySelectorAll("[data-ai-done]").forEach(checkbox => checkbox.addEventListener("change", () => {
      mutate(props => { props.result.action_items[Number(checkbox.dataset.aiDone)].done = checkbox.checked; });
    }));
    container.querySelectorAll("[data-ai-quote]").forEach(button => button.addEventListener("click", () => {
      const quote = button.dataset.aiQuote;
      const props = adapter.getBlock()?.props || {};
      session.tab = (props.transcript || "").includes(quote) ? "transcript" : "notes";
      sync();
      const input = container.querySelector(`[data-ai-field="${session.tab}"]`);
      if (input) { const start = input.value.indexOf(quote); input.focus(); input.setSelectionRange(Math.max(0, start), Math.max(0, start) + quote.length); }
    }));
    const select = container.querySelector("[data-ai-model]");
    const comparisonToggle = container.querySelector("[data-ai-comparison]");
    comparisonToggle?.addEventListener("change", event => {
      if (!editable() || clearBlocked()) { syncViews(); return; }
      mutate(props => { props.comparison_enabled = event.target.checked; });
      session.comparisonConsent = false;
      repaint();
    });
    container.querySelector("[data-ai-knowledge-space]")?.addEventListener("change", event => {
      mutate(props => { props.knowledge_space_id = event.target.value; }); syncViews();
    });
    container.querySelector("[data-ai-knowledge-query]")?.addEventListener("input", event => {
      mutate(props => { props.knowledge_query = event.target.value; }); syncViews();
    });
    async function loadComparisonScopes(cursor = "") {
      try {
        const data = await adapter.request("/api/notion/ai-notes/comparison-sources" + (cursor ? "?cursor=" + encodeURIComponent(cursor) : ""));
        const picker = container.querySelector("[data-ai-knowledge-space]");
        if (!picker?.isConnected || !alive()) return;
        const selected = adapter.getBlock().props.knowledge_space_id || "";
        if (!cursor) picker.replaceChildren(new Option("Wiki 선택", ""));
        for (const item of data.items || []) if (![...picker.options].some(option => option.value === item.space_id)) picker.add(new Option(item.display_name, item.space_id));
        if (selected && ![...picker.options].some(option => option.value === selected)) picker.add(new Option("선택한 Wiki (권한 확인 필요)", selected));
        picker.value = selected;
        const more = container.querySelector("[data-ai-more-wikis]");
        more.hidden = !data.next_cursor; more.dataset.cursor = data.next_cursor || "";
      } catch (_error) { status("참고 Wiki를 불러오지 못했습니다. Second Brain 연결과 권한을 확인하세요."); }
    }
    container.querySelector("[data-ai-more-wikis]")?.addEventListener("click", event => loadComparisonScopes(event.target.dataset.cursor));
    function pollComparison() {
      if (session.comparisonPolling) return;
      const id = adapter.getBlock()?.props?.comparison_run_id;
      if (!id || session.completedComparisonId === id || !editable()) return;
      session.comparisonPolling = true;
      session.comparisonPending = true;
      const tick = async () => {
        const view = [...session.views].find(([node, value]) => node.isConnected && value.adapter.getBlock()?.props?.comparison_run_id === id && value.adapter.canEdit());
        if (!view) { session.comparisonPolling = false; session.comparisonPending = false; return; }
        try {
          const result = await view[1].adapter.request("/api/notion/ai-notes/comparisons/" + encodeURIComponent(id));
          status(result.message || "호스트 비교 처리 중…");
          if (!["queued", "processing"].includes(result.state)) {
            session.completedComparisonId = id; session.comparisonPolling = false; session.comparisonPending = false; syncViews(); return;
          }
          syncViews(); setTimeout(tick, 2000);
        } catch (_error) {
          session.comparisonPolling = false; session.comparisonPending = false;
          status("호스트 비교 상태를 확인하지 못했습니다. 새로고침하면 다시 확인합니다."); syncViews();
        }
      };
      tick();
    }
    container.querySelector("[data-ai-output-language]")?.addEventListener("change", event => {
      const custom = event.target.value === "custom";
      container.querySelector("[data-ai-language-custom-label]").hidden = !custom;
      const input = container.querySelector("[data-ai-language-custom]");
      mutate(props => { props.output_language = custom ? input.value : event.target.value; }); sync();
      if (custom) input.focus();
    });
    container.querySelector("[data-ai-language-custom]")?.addEventListener("input", event => {
      mutate(props => { props.output_language = event.target.value; }); sync();
    });
    if (select) {
      // Do not re-enable a saved/offline provider before server capabilities arrive.
      select.value = "";
      select.disabled = true;
      select.addEventListener("change", () => mutate(props => { props.model_id = select.value; }));
      adapter.request("/api/notion/ai-notes/capabilities").then(data => {
        session.audioConfig = data.audio || { enabled: false, reason: "음성 API를 설정해 주세요." };
        session.translationController?.capabilities(data);
        session.comparisonAvailable = Boolean(data.comparison?.enabled);
        if (comparisonToggle?.isConnected) {
          comparisonToggle.disabled = !session.comparisonAvailable || !editable() || clearBlocked();
          container.querySelector("[data-ai-comparison-availability]").textContent = session.comparisonAvailable
            ? "비교 결과는 사용자 화면·다운로드에 포함되지 않습니다. 최종 원문으로 수집합니다." : "호스트 비교 수집 설정이 필요합니다.";
          if (session.comparisonAvailable && adapter.getBlock()?.props?.comparison_enabled) loadComparisonScopes();
        }
        if (!session.autoSummaryChosen && !session.meeting) session.autoSummary = session.audioConfig.auto_summary_default !== false;
        syncAudio();
        if (!select.isConnected) return;
        const models = globalThis.NotionNotesTranslation?.allowedModels(data) || (data.models || []).filter(model => model.provider !== "codex");
        session.textModels = models;
        select.replaceChildren(new Option(globalThis.NotionNotesTranslation?.autoLabel(models) || "자동 · Gemma4 → Qwen3.6", ""));
        for (const model of models) {
          select.add(new Option(model.name, model.id));
        }
        select.disabled = !models.length || !editable();
        const currentId = adapter.getBlock()?.props?.model_id || "";
        select.value = models.some(model => model.id === currentId) ? currentId : "";
      }).catch(() => {
        session.audioConfig = { enabled: false, reason: "음성 API 설정을 확인하지 못했습니다. 서버 연결 후 새로고침하세요." };
        syncAudio(); status("모델 목록을 불러오지 못했습니다. 서버 연결을 확인하세요.");
      });
    }
    container.querySelector("[data-ai-import]")?.addEventListener("change", async event => {
      const file = event.target.files?.[0];
      if (!file || !editable() || clearBlocked()) { event.target.value = ""; return; }
      const before = sourceKey(adapter.getBlock().props);
      session.importingText = true; syncViews();
      try {
        if (file.size > 320000) throw new Error("텍스트 파일이 너무 큽니다. 회의를 나누어 가져오세요.");
        const text = await file.text();
        if (text.length > 80000 || text.includes("\0")) throw new Error("80,000자 이하의 텍스트 파일을 선택하세요.");
        if (!alive() || sourceKey(adapter.getBlock().props) !== before) throw new Error("입력이 변경되어 파일을 반영하지 않았습니다.");
        if (adapter.getBlock().props.transcript && !window.confirm("현재 회의록 원문을 선택한 텍스트 파일로 바꿀까요?")) return;
        mutate(props => { props.transcript = text; delete props.audio_transcript_start; props.audio_scope_invalid = true; });
        session.translationController?.sourceChanged();
        repaint();
      } catch (error) { status(error.message); }
      finally { session.importingText = false; event.target.value = ""; syncViews(); }
    });
    async function generate(kind = "manual") {
      if (!editable() || session.clearing) return;
      if (kind === "manual" && (audioActive(session.meeting) || session.meeting?.queue.length)) { status("회의 종료·전사를 마친 뒤 최종 요약을 생성하세요."); return; }
      const original = adapter.getBlock()?.props || {};
      if (original.summary_manually_edited && !original.comparison_enabled) {
        if (kind !== "manual") { status("직접 수정한 요약을 보존했습니다. 자동 요약은 건너뜁니다."); return; }
        if (session.busy) return;
        if (!window.confirm("직접 수정한 요약을 AI 요약으로 교체할까요? 원문과 메모는 유지됩니다.")) return;
        if (!editable()) return;
      }
      if (container.querySelector("[data-ai-output-language]")?.value === "custom" && !(adapter.getBlock().props.output_language || "").trim()) {
        status("직접 지정할 요약 언어를 입력하세요."); return;
      }
      if (session.busy) {
        if (kind !== "manual") session.queuedSummary = kind === "final" ? "final" : (session.queuedSummary || kind);
        if (kind === "final") return new Promise(resolve => { (session.finalWaiters ||= []).push(resolve); });
        return;
      }
      const props = adapter.getBlock().props || {};
      if (!(props.transcript || "").trim() && !(props.notes || "").trim()) { status("회의록 원문 또는 메모를 입력하세요."); return; }
      const snapshot = sourceKey(props);
      const summarySnapshot = summaryEditKey(props);
      if (kind === "provisional" && props.generated_source === snapshot) return;
      const primary = session.textModels?.find(model => model.id === props.model_id);
      const body = { ...adapter.scope, transcript: props.transcript || "", notes: props.notes || "", instructions: props.instructions || "", output_language: props.output_language || "", model_id: primary?.id || "", summary_phase: kind === "provisional" ? "provisional" : "final" };
      if (props.comparison_enabled) {
        if (kind === "provisional") return;
        if (!session.comparisonAvailable) { status("호스트 비교 수집 설정이 필요합니다."); return; }
        if (!props.knowledge_space_id) { status("비교에 사용할 Wiki를 선택하세요."); return; }
        if (!session.comparisonConsent && !window.confirm("원문·메모·지침·참고 지식과 두 요약을 품질 비교용으로 호스트에 보관합니다. 두 요약은 화면에 표시하지 않으며, 같은 모델로 별도 호출합니다. 비교 수집을 시작할까요?")) return;
        session.comparisonConsent = true;
        const key = JSON.stringify([snapshot, body.model_id]);
        if (session.comparisonOperation?.key !== key) session.comparisonOperation = {key, id: crypto.randomUUID()};
        Object.assign(body, {knowledge_space_id: props.knowledge_space_id, knowledge_query: props.knowledge_query || "",
          comparison_consent: true, operation_id: session.comparisonOperation.id});
      }
      session.busy = true; status("원문 저장 중…");
      for (const view of session.views.values()) view.sync();
      try {
        if (!(await persist())) throw new Error("원문을 먼저 저장해야 합니다. 저장 상태를 확인하세요.");
        if (!editable() || !canApplySummary(snapshot, adapter.getBlock().props, kind === "provisional")) throw new Error("입력이 변경되었습니다. 다시 생성하세요.");
        if (props.comparison_enabled) {
          const comparison = await adapter.request("/api/notion/ai-notes/comparisons", {method: "POST", body});
          if (!editable() || sourceKey(adapter.getBlock().props) !== snapshot) throw new Error("비교는 호스트에 접수됐지만 현재 입력이 바뀌어 노트 상태에 반영하지 않았습니다.");
          mutate(value => { value.comparison_run_id = comparison.id; value.comparison_state = comparison.state; });
          session.comparisonOperation = null;
          status(await persist() ? comparison.message : "호스트에 비교가 접수됐지만 노트 상태는 저장하지 못했습니다.");
          pollComparison();
          return;
        }
        status("요약 생성 중…");
        const data = await adapter.request("/api/notion/ai-notes/summarize", { method: "POST", body });
        if (!editable() || !canApplySummary(snapshot, adapter.getBlock().props, kind === "provisional")) throw new Error("페이지 또는 원문이 변경되어 결과를 반영하지 않았습니다. 다시 생성하세요.");
        if (summaryEditKey(adapter.getBlock().props) !== summarySnapshot) throw new Error("요약이 변경되어 늦게 도착한 AI 결과를 반영하지 않았습니다.");
        mutate(value => {
          value.result = data.result;
          delete value.summary_manually_edited;
          delete value.summary_edited_at;
          value.summary_phase = body.summary_phase;
          value.generated_source = snapshot;
          value.source_sha256 = data.source_sha256;
          value.model_name = data.model_name;
          value.provider = data.provider;
          value.generated_at = new Date().toISOString();
        });
        if (kind !== "provisional") session.tab = "summary";
        status(await persist() ? "요약 저장됨" : "요약은 편집본에 있습니다. 저장 상태를 확인하세요.");
      } catch (error) { status(error.message || "AI 노트를 생성하지 못했습니다. 원문은 유지됩니다."); }
      finally {
        session.busy = false; repaint();
        const queued = session.queuedSummary; session.queuedSummary = null;
        if (queued && editable()) generate(queued).finally(() => { if (queued === "final") (session.finalWaiters || []).splice(0).forEach(resolve => resolve()); });
        else if (!editable()) (session.finalWaiters || []).splice(0).forEach(resolve => resolve());
      }
    }
    container.querySelector("[data-ai-generate]")?.addEventListener("click", () => generate());
    if (globalThis.NotionNotesTranslation) {
      session.translationController ||= new globalThis.NotionNotesTranslation.Controller();
      session.translationController.bind(container, adapter, mutate, persist,
        () => Boolean(session.clearing || session.importingText), () => { syncExport(); syncClear(); syncAudio(); },
        () => Boolean(session.startingAudio || session.finishingAudio || audioActive(session.meeting) || session.meeting?.queue.length));
    }
    sync();
    // Rows are bound before the editor attaches them to the document.
    setTimeout(pollComparison, 0);
  }

  async function beforeNavigate() {
    if (globalThis.NotionNotesTranslation && !(await globalThis.NotionNotesTranslation.beforeNavigate())) return false;
    const session = activeAudioSession;
    if (!session?.meeting) return !session?.startingAudio;
    if (session.finishingAudio) await session.finishPromise;
    if (!audioActive(session.meeting) && !session.meeting.queue.length) return true;
    if (!window.confirm("진행 중인 녹음을 종료하고 남은 전사·최종 요약을 처리한 뒤 이동할까요?")) return false;
    await session.meeting.stop();
    if (session.meeting.queue.length && !window.confirm("전사되지 않은 음성이 남아 있습니다. 이동하면 대기 음성이 사라집니다. 먼저 내려받거나 재시도하려면 취소하세요.")) return false;
    clearInterval(session.audioWatch); clearInterval(session.summaryTimer);
    await session.meeting.dispose(); activeAudioSession = null;
    return true;
  }
  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", event => {
      if (activeAudioSession?.finishingAudio || audioActive(activeAudioSession?.meeting) || activeAudioSession?.meeting?.queue.length) { event.preventDefault(); event.returnValue = ""; }
    });
    window.addEventListener("pagehide", () => { activeAudioSession?.meeting?.dispose(); });
  }
  return Object.freeze({ html, bind, sourceKey, toMarkdown, canApplySummary, beforeNavigate, hasExportContent, exportFile, hasClearContent, clearedProps, editedSummary, summaryEditKey, captureSeconds, captureMode });
});
