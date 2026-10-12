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
  const sourceKey = props => JSON.stringify([props.transcript || "", props.notes || "", props.instructions || "", props.output_language || ""]);
  const audioActive = meeting => meeting && !meeting.disposed && !["idle", "stopped", "error"].includes(meeting.phase);
  function canApplySummary(snapshot, props, provisional) {
    if (!provisional) return snapshot === sourceKey(props);
    const [transcript, notes, instructions, language = ""] = JSON.parse(snapshot);
    return String(props.transcript || "").startsWith(transcript) && (props.notes || "") === notes && (props.instructions || "") === instructions && (props.output_language || "") === language;
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
    return lines.join("\n\n");
  }

  const exportLabels = Object.freeze({ all: "전체 노트", transcript: "원문", summary: "요약", notes: "메모" });
  const textValue = value => typeof value === "string" ? value : "";
  const resultItems = (props, key) => (Array.isArray(props.result?.[key]) ? props.result[key] : []).filter(item => item && typeof item === "object");
  function hasExportContent(props, kind) {
    if (!Object.hasOwn(exportLabels, kind)) return false;
    if (kind === "transcript" || kind === "notes") return Boolean(textValue(props[kind]).trim());
    const summary = Boolean(textValue(props.result?.summary).trim()
      || resultItems(props, "decisions").some(item => textValue(item.text).trim())
      || resultItems(props, "action_items").some(item => textValue(item.task).trim()));
    return kind === "summary" ? summary : summary || hasExportContent(props, "transcript") || hasExportContent(props, "notes");
  }
  function exportFile(props, kind = "all", format = "md", date = new Date()) {
    if (!["txt", "md"].includes(format) || !hasExportContent(props, kind)) return null;
    // TXT source/note exports are exact, including whitespace and timestamps.
    // Escape raw HTML in Markdown without changing the canonical page content.
    const content = value => format === "md" ? textValue(value).replace(/[&<>]/g, ch => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[ch])) : textValue(value);
    const heading = (title, level = 2) => format === "md" ? `${"#".repeat(level)} ${title}` : title;
    const lines = [];
    if (kind === "transcript" || kind === "notes") {
      if (format === "md") lines.push(heading(exportLabels[kind], 1));
      lines.push(content(props[kind]));
    } else {
      lines.push(heading(kind === "all" ? "AI 노트" : "AI 노트 요약", 1));
      if (hasExportContent(props, "summary")) {
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
      <div class="ai-notes-export" role="group" aria-label="AI 노트 다운로드">
        <label>내용 <select data-ai-export-kind aria-label="다운로드 내용"><option value="all">전체 노트</option><option value="transcript">원문</option><option value="summary">요약·결정·할 일</option><option value="notes">메모</option></select></label>
        <label>형식 <select data-ai-export-format aria-label="다운로드 형식"><option value="txt">텍스트 (.txt)</option><option value="md">Markdown (.md)</option></select></label>
        <button type="button" data-ai-export disabled>다운로드</button>
        <small data-ai-export-status role="status" aria-live="polite"></small>
      </div>
      ${canEdit ? `<div class="ai-notes-audio">
        <label class="ai-notes-consent"><input type="checkbox" data-ai-audio-consent> 참석자의 녹음·전사 동의를 확인했습니다</label>
        <label>소리 입력 <select data-ai-audio-source aria-label="소리 입력"><option value="system">시스템·탭 오디오</option><option value="mixed">시스템·탭 + 마이크</option><option value="microphone">마이크만</option></select></label>
        <label>음성 언어 <select data-ai-audio-language aria-label="음성 언어"><option value="">자동 감지</option><option value="ko">한국어</option><option value="en">영어</option><option value="ja">일본어</option><option value="zh">중국어</option></select></label>
        <div class="ai-notes-audio-buttons"><button type="button" data-ai-record disabled>소리 공유로 회의 시작</button>
          <button type="button" data-ai-pause hidden>일시정지</button><button type="button" data-ai-stop hidden>회의 종료</button>
          <button type="button" data-ai-retry-audio hidden>미전사 음성 재시도</button>
          <button type="button" data-ai-download-audio hidden>대기 음성 내려받기</button></div>
        <label class="ai-notes-import">음성 파일 가져오기<input type="file" data-ai-audio-import accept=".wav,.mp3,.m4a,.mp4,.webm,.ogg,.flac,audio/*" disabled></label>
        <label class="ai-notes-consent"><input type="checkbox" data-ai-auto-summary checked> 자동 요약 (회의 중 30초 간격·종료 후)</label>
        <p class="ai-notes-muted" data-ai-audio-provider></p>
        <p data-ai-audio-status role="status" aria-live="polite">음성 API 설정 확인 중…</p>
        <p class="ai-notes-muted">Zoom/Webex 등 앱 종류와 관계없이 선택한 시스템·탭 소리를 수집합니다. 시작 시 브라우저 공유 창에서 ‘오디오 공유’를 선택하세요. 브라우저·OS에 따라 지원 범위가 다릅니다. 선택한 소리는 알림·다른 앱 소리도 포함할 수 있습니다. 화면 영상은 전송·저장하지 않으며 오디오는 설정된 전사 API로 전송됩니다. 이 앱 서버는 오디오를 영구 보관하지 않습니다.</p>
      </div>` : ""}
      <div class="ai-notes-tabs" role="tablist" aria-label="AI 노트 내용">
        <button type="button" role="tab" data-ai-tab="summary">요약</button>
        <button type="button" role="tab" data-ai-tab="notes">메모</button>
        <button type="button" role="tab" data-ai-tab="transcript">원문</button>
      </div>
      <div data-ai-panel="summary" role="tabpanel" aria-label="요약">
        ${result.summary ? `<p class="ai-notes-summary">${escape(result.summary)}</p>
          <h4>결정 사항</h4>${result.decisions?.length ? `<ul>${list(result.decisions, false)}</ul>` : "<p class='ai-notes-muted'>확인된 결정 사항 없음</p>"}
          <h4>할 일</h4>${result.action_items?.length ? `<ul>${list(result.action_items, true)}</ul>` : "<p class='ai-notes-muted'>확인된 할 일 없음</p>"}
          <p class="ai-notes-muted">${escape(props.model_name || "AI")} · ${escape(props.generated_at ? new Date(props.generated_at).toLocaleString() : "")} · AI가 작성한 초안입니다. 근거를 확인하세요.</p>` :
          "<div class='ai-notes-empty'><strong>회의의 핵심을 한곳에</strong><p>원문에 회의록을 붙여넣거나 메모를 작성한 뒤 요약을 생성하세요.</p></div>"}
        <p class="ai-notes-stale" data-ai-stale hidden>원문·메모가 변경되었습니다. 요약을 다시 생성하세요.</p>
      </div>
      <div data-ai-panel="notes" role="tabpanel" aria-label="메모" hidden>
        <textarea data-ai-field="notes" aria-label="회의 메모" maxlength="12000" placeholder="안건, 배경, 직접 작성한 회의 메모" ${canEdit ? "" : "readonly"}>${escape(props.notes)}</textarea>
      </div>
      <div data-ai-panel="transcript" role="tabpanel" aria-label="원문" hidden>
        <textarea data-ai-field="transcript" aria-label="회의록 원문" maxlength="80000" placeholder="회의록 또는 전사된 텍스트를 붙여넣으세요. (최대 80,000자)" ${canEdit ? "" : "readonly"}>${escape(props.transcript)}</textarea>
        ${canEdit ? `<label class="ai-notes-import">텍스트 파일 가져오기<input type="file" data-ai-import accept=".txt,.md,.vtt,.srt,text/plain,text/markdown"></label>` : ""}
        <p class="ai-notes-muted">음성 전사 결과도 이 원문에 누적됩니다. 전사가 끝나면 직접 수정할 수 있습니다.</p>
      </div>
      ${canEdit ? `<details class="ai-notes-options"><summary>요약 설정</summary>
        <label>모델<select data-ai-model aria-label="AI 노트 모델"><option value="">자동 · Gemma4 → Qwen3.6 → 연결된 Codex</option></select></label>
        <label>번역·요약 언어<select data-ai-output-language aria-label="번역·요약 언어">${[["", "지침에 따름 (기본 한국어)"], ["ko", "한국어"], ["en", "영어"], ["ja", "일본어"], ["zh", "중국어"]].map(([value, label]) => `<option value="${value}" ${(props.output_language || "") === value ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <p class="ai-notes-muted">원문은 보존하고 요약·결정·할 일을 선택한 언어로 정리합니다. 원문 전체 번역은 아닙니다.</p>
        <label>요약 지침<textarea data-ai-field="instructions" aria-label="AI 노트 요약 지침" maxlength="2000" placeholder="예: 결정 사항과 다음 주 할 일을 중심으로 정리">${escape(props.instructions)}</textarea></label>
        <p class="ai-notes-muted">선택한 모델에 이 노트의 원문과 메모만 전송됩니다.</p></details>
        <footer><button type="button" class="ai-notes-generate" data-ai-generate>${result.summary ? "요약 다시 생성" : "요약 생성"}</button><span data-ai-status role="status" aria-live="polite"></span></footer>` : ""}
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
        button.disabled = session.busy || !editable();
        button.textContent = session.busy ? "요약 생성 중…" : (adapter.getBlock()?.props?.result ? "요약 다시 생성" : "요약 생성");
      }
      const stale = container.querySelector("[data-ai-stale]");
      const props = adapter.getBlock()?.props || {};
      if (stale) stale.hidden = !props.result || props.generated_source === sourceKey(props);
      container.querySelectorAll("[data-ai-done]").forEach(checkbox => { checkbox.disabled = session.busy || !editable(); });
      status(session.message);
      syncAudio();
      syncExport();
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
    session.views.set(container, { sync, repaint: repaintView });
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
      const ready = Boolean(session.audioConfig?.enabled && session.audioConsent && editable() && !session.startingAudio && !session.finishingAudio);
      const set = (selector, fn) => { const node = container.querySelector(selector); if (node) fn(node); };
      set("[data-ai-audio-consent]", node => { node.checked = Boolean(session.audioConsent); node.disabled = active || pending; });
      set("[data-ai-auto-summary]", node => { node.checked = session.autoSummary; });
      set("[data-ai-audio-source]", node => { node.value = session.audioSource || "system"; node.disabled = active || pending || session.startingAudio; });
      set("[data-ai-audio-language]", node => { node.value = session.audioLanguage || ""; node.disabled = active || pending || session.startingAudio; });
      set("[data-ai-record]", node => { node.disabled = !ready || active || pending; });
      set("[data-ai-audio-import]", node => { node.disabled = !ready || active || pending; });
      set("[data-ai-import]", node => { node.disabled = active || pending; });
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
        node.textContent = session.audioConfig?.local_only ? "음성은 이 PC에서 전사합니다. 요약 실행 시 전사 텍스트가 선택한 AI 모델로 전송됩니다. 첫 소리 테스트는 자동 요약을 끄고 원문을 확인하세요. 노래·반주는 부정확하게 인식될 수 있습니다." : "";
      });
    }
    const syncViews = () => { for (const view of session.views.values()) view.sync(); };
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
      if (session.autoSummary && editable() && audioActive(session.meeting) && ["recording", "paused"].includes(session.meeting.phase)) generate("provisional");
    };
    function newMeeting() {
      if (!editable() || !session.audioConfig?.enabled || !session.audioConsent) return null;
      if (activeAudioSession && activeAudioSession !== session && (activeAudioSession.startingAudio || activeAudioSession.finishingAudio || audioActive(activeAudioSession.meeting) || activeAudioSession.meeting?.queue.length)) {
        status("다른 AI 노트의 녹음·전사를 먼저 종료해 주세요."); return null;
      }
      if (session.startingAudio || session.finishingAudio || audioActive(session.meeting) || session.meeting?.queue.length) return null;
      session.startingAudio = true; activeAudioSession = session; syncViews();
      session.meeting?.dispose().catch(() => {});
      if (!editable()) { session.startingAudio = false; syncViews(); return null; }
      clearInterval(session.audioWatch); clearInterval(session.summaryTimer);
      activeAudioSession = session;
      const meeting = new window.NotionMeetingAudio.Meeting({
        valid: editable,
        captureFactory: window.NotionMeetingAudio.sourceFactory(session.audioSource || "system"),
        chunkSeconds: session.audioConfig.chunk_seconds || 12,
        transcribe: (item, signal) => adapter.uploadAudio(item, session.audioConsent, signal, session.audioLanguage || ""),
        onText: (text, item) => {
          if (!text.trim()) return;
          if (!editable()) throw new Error("페이지 편집 권한이 변경되었습니다.");
          const props = adapter.getBlock().props;
          const stamp = item.duration ? `[${Math.floor(item.offset / 60).toString().padStart(2, "0")}:${Math.floor(item.offset % 60).toString().padStart(2, "0")}]` : "[음성 파일]";
          const next = [props.transcript || "", `${stamp} ${text}`].filter(Boolean).join("\n");
          if (next.length > 80000) throw new Error("회의록이 80,000자에 도달했습니다. 새 AI 노트에서 회의를 이어가세요.");
          mutate(value => { value.transcript = next; value.audio_started_at = meeting.startedAt; });
          for (const view of session.views.keys()) {
            const input = view.querySelector('[data-ai-field="transcript"]');
            if (input) input.value = next;
          }
        },
        persist,
        onCommitted: item => { if (item.result.text && meeting.received === 1) requestAutoSummary(); },
        onState: state => { if (state.phase === "error") clearInterval(session.summaryTimer); syncViews(); },
        onStopped: async () => {
          clearInterval(session.audioWatch); clearInterval(session.summaryTimer);
          if (!session.autoSummary) {
            meeting.notify("전사 완료 · 원문을 확인한 뒤 필요하면 ‘요약 생성’을 누르세요.");
            return;
          }
          session.finishingAudio = true; meeting.notify("전사 완료 · 최종 요약 처리 중…");
          session.finishPromise = generate("final");
          try { await session.finishPromise; }
          finally { session.finishingAudio = false; session.finishPromise = null; meeting.notify("전사 완료 · 요약 결과는 아래에서 확인하세요."); }
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
    container.querySelector("[data-ai-audio-consent]")?.addEventListener("change", event => { session.audioConsent = event.target.checked; syncViews(); });
    container.querySelector("[data-ai-auto-summary]")?.addEventListener("change", event => { session.autoSummary = event.target.checked; session.autoSummaryChosen = true; syncViews(); });
    container.querySelector("[data-ai-audio-source]")?.addEventListener("change", event => { session.audioSource = event.target.value; syncViews(); });
    container.querySelector("[data-ai-audio-language]")?.addEventListener("change", event => { session.audioLanguage = event.target.value; syncViews(); });
    container.querySelector("[data-ai-record]")?.addEventListener("click", async () => {
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
        mutate(props => { props[input.dataset.aiField] = input.value; });
        sync();
      });
      input.addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
          event.preventDefault(); adapter.save();
        }
      });
    });
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
    container.querySelector("[data-ai-output-language]")?.addEventListener("change", event => { mutate(props => { props.output_language = event.target.value; }); sync(); });
    if (select) {
      const selectedId = adapter.getBlock()?.props?.model_id || "";
      if (selectedId) select.add(new Option(selectedId, selectedId));
      select.value = selectedId;
      select.addEventListener("change", () => mutate(props => { props.model_id = select.value; }));
      adapter.request("/api/notion/ai-notes/capabilities").then(data => {
        session.audioConfig = data.audio || { enabled: false, reason: "음성 API를 설정해 주세요." };
        if (!session.autoSummaryChosen && !session.meeting) session.autoSummary = session.audioConfig.auto_summary_default !== false;
        syncAudio();
        if (!select.isConnected) return;
        select.replaceChildren(new Option("자동 · Gemma4 → Qwen3.6 → 연결된 Codex", ""));
        for (const model of data.models || []) select.add(new Option(model.name, model.id));
        if (selectedId && ![...select.options].some(option => option.value === selectedId)) select.add(new Option(`${selectedId} (설정 확인 필요)`, selectedId));
        select.value = adapter.getBlock()?.props?.model_id || "";
      }).catch(() => {
        session.audioConfig = { enabled: false, reason: "음성 API 설정을 확인하지 못했습니다. 서버 연결 후 새로고침하세요." };
        syncAudio(); status("모델 목록을 불러오지 못했습니다. 서버 연결을 확인하세요.");
      });
    }
    container.querySelector("[data-ai-import]")?.addEventListener("change", async event => {
      const file = event.target.files?.[0];
      if (!file || !editable()) return;
      const before = sourceKey(adapter.getBlock().props);
      try {
        if (file.size > 320000) throw new Error("텍스트 파일이 너무 큽니다. 회의를 나누어 가져오세요.");
        const text = await file.text();
        if (text.length > 80000 || text.includes("\0")) throw new Error("80,000자 이하의 텍스트 파일을 선택하세요.");
        if (!alive() || sourceKey(adapter.getBlock().props) !== before) throw new Error("입력이 변경되어 파일을 반영하지 않았습니다.");
        if (adapter.getBlock().props.transcript && !window.confirm("현재 회의록 원문을 선택한 텍스트 파일로 바꿀까요?")) return;
        mutate(props => { props.transcript = text; });
        repaint();
      } catch (error) { status(error.message); }
      event.target.value = "";
    });
    async function generate(kind = "manual") {
      if (!editable()) return;
      if (kind === "manual" && (audioActive(session.meeting) || session.meeting?.queue.length)) { status("회의 종료·전사를 마친 뒤 최종 요약을 생성하세요."); return; }
      if (session.busy) {
        if (kind !== "manual") session.queuedSummary = kind === "final" ? "final" : (session.queuedSummary || kind);
        if (kind === "final") return new Promise(resolve => { (session.finalWaiters ||= []).push(resolve); });
        return;
      }
      const props = adapter.getBlock().props || {};
      if (!(props.transcript || "").trim() && !(props.notes || "").trim()) { status("회의록 원문 또는 메모를 입력하세요."); return; }
      const snapshot = sourceKey(props);
      if (kind === "provisional" && props.generated_source === snapshot) return;
      const body = { ...adapter.scope, transcript: props.transcript || "", notes: props.notes || "", instructions: props.instructions || "", output_language: props.output_language || "", model_id: props.model_id || "", summary_phase: kind === "provisional" ? "provisional" : "final" };
      session.busy = true; status("원문 저장 중…");
      for (const view of session.views.values()) view.sync();
      try {
        if (!(await persist())) throw new Error("원문을 먼저 저장해야 합니다. 저장 상태를 확인하세요.");
        if (!editable() || !canApplySummary(snapshot, adapter.getBlock().props, kind === "provisional")) throw new Error("입력이 변경되었습니다. 다시 생성하세요.");
        status("요약 생성 중…");
        const data = await adapter.request("/api/notion/ai-notes/summarize", { method: "POST", body });
        if (!editable() || !canApplySummary(snapshot, adapter.getBlock().props, kind === "provisional")) throw new Error("페이지 또는 원문이 변경되어 결과를 반영하지 않았습니다. 다시 생성하세요.");
        mutate(value => {
          value.result = data.result;
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
    sync();
  }

  async function beforeNavigate() {
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
  return Object.freeze({ html, bind, sourceKey, toMarkdown, canApplySummary, beforeNavigate, hasExportContent, exportFile });
});
