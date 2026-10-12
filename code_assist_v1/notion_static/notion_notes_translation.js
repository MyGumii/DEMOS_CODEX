(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.NotionNotesTranslation = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const controllers = new Set();
  const fields = ["translation_text", "translation_source", "translation_language", "translation_model_name", "translation_config", "translation_scope_key", "translation_display_start"];
  const languages = [["ko", "한국어"], ["en", "영어"], ["ja", "일본어"]];
  const isPair = props => props.translation_mode === "pair";
  function pair(props) {
    const value = props.translation_pair ?? ["ko", "en"];
    return Array.isArray(value) && value.length === 2 && value[0] !== value[1]
      && value.every(code => languages.some(([id]) => id === code))
      ? value.slice() : [];
  }
  const canonicalPair = props => languages.map(([id]) => id).filter(id => pair(props).includes(id));
  const singleTarget = props => String(props.translation_target ?? "한국어").trim();
  const configKey = props => JSON.stringify(isPair(props) ? ["pair", canonicalPair(props)] : ["single", singleTarget(props)]);
  const audioLanguage = (props, preferred = "") => isPair(props) ? "" : preferred;
  const esc = value => String(value || "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const target = props => isPair(props) ? canonicalPair(props).map(code => languages.find(([id]) => id === code)[1]).join(" ↔ ") : singleTarget(props);
  function recordingStart(props) {
    const source = props.transcript || "";
    if (props.audio_scope_invalid || !props.audio_started_at) return 0;
    if (Number.isInteger(props.audio_transcript_start) && props.audio_transcript_start >= 0 && props.audio_transcript_start <= source.length) return props.audio_transcript_start;
    // Upgrade old recordings without rewriting their text. Existing captures
    // reset the timestamp to 00:00 whenever a new meeting starts.
    const starts = [...source.matchAll(/(^|\n)\[00:00\] /g)];
    const last = starts.at(-1);
    return last ? last.index + last[1].length : 0;
  }
  function sourceScope(props) {
    const start = props.translation_include_history ? 0 : recordingStart(props);
    const recording = !props.translation_include_history && !props.audio_scope_invalid && props.audio_started_at
      && (start > 0 || Number.isInteger(props.audio_transcript_start));
    return {start, key: recording ? `recording:${props.audio_started_at}:${start}` : "all"};
  }
  const sameScope = props => (props.translation_scope_key || "all") === sourceScope(props).key;
  const sourcePrefix = props => sameScope(props) ? (props.translation_source || "") : (props.transcript || "").slice(0, sourceScope(props).start);
  function displayedTranslation(props) {
    const text = props.translation_text || "";
    const offset = sameScope(props) ? Math.max(0, Math.min(text.length, Number(props.translation_display_start) || 0)) : text.length;
    return {current: text.slice(offset), previous: text.slice(0, offset).trimEnd()};
  }
  const stale = props => Boolean(sameScope(props) && props.translation_source &&
    (!(props.transcript || "").startsWith(props.translation_source) || props.translation_language !== target(props)
      || (props.translation_config ? props.translation_config !== configKey(props) : isPair(props))));
  function allowedModels(data) {
    const codex = data.codex_bridge?.enabled === true && data.codex_bridge?.connected === true;
    return (data.models || []).filter(model => model.provider !== "codex" || codex);
  }
  const autoLabel = models => "자동 · Gemma4 → Qwen3.6" + (models.some(model => model.provider === "codex") ? " → Codex fallback" : "");
  async function requestTranslation(adapter, body, signal) {
    const path = "/api/notion/ai-notes/translate";
    if (typeof globalThis.authenticatedFetch !== "function") return adapter.request(path, {method: "POST", body, signal});
    // Reuse the existing CSRF/auth boundary, preserving AbortSignal. The generic
    // page JSON adapter does not forward signals; do not change that shared API.
    const response = await globalThis.authenticatedFetch(path, {method: "POST", signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || result.error || "번역 요청 실패");
    return result;
  }
  function chunk(text, limit = 2000) {
    if (text.length <= limit) return text;
    const head = text.slice(0, limit);
    const boundary = Math.max(head.lastIndexOf("\n"), head.lastIndexOf(". "), head.lastIndexOf("。"), head.lastIndexOf("? "), head.lastIndexOf("! "));
    let end = boundary >= limit / 2 ? boundary + 1 : limit;
    if (/[\uD800-\uDBFF]/.test(head[end - 1])) end--;
    return text.slice(0, end);
  }
  function html(props, canEdit) {
    return `<div data-ai-panel="translation" role="tabpanel" aria-label="번역" hidden>
      ${canEdit ? `<div class="ai-notes-translation-controls">
        <label>번역 방식<select data-ai-translate-mode aria-label="번역 방식"><option value="single" ${!isPair(props) ? "selected" : ""}>단일 언어 번역</option><option value="pair" ${isPair(props) ? "selected" : ""}>두 언어 통역 · 양방향 자막</option></select></label>
        <label data-ai-translate-single ${isPair(props) ? "hidden" : ""}>번역할 언어<input data-ai-translate-language type="text" maxlength="80" aria-label="원문 번역 언어" value="${esc(singleTarget(props))}" placeholder="한국어, English, 日本語…"></label>
        <div data-ai-translate-pair-panel ${!isPair(props) ? "hidden" : ""}>
          <div class="ai-notes-interpret-pair">${[0, 1].map(index => `<label>통역 언어 ${index + 1}<select data-ai-translate-pair="${index}" aria-label="통역 언어 ${index + 1}">${languages.map(([id, label]) => `<option value="${id}" ${pair(props)[index] === id ? "selected" : ""}>${label}</option>`).join("")}</select></label>`).join('<span aria-hidden="true">↔</span>')}</div>
          <p class="ai-notes-muted">문장별 언어를 감지해 상대 언어로 한 번만 번역합니다. 음성 언어는 자동 감지를 사용합니다. 선택 외 언어·불명확한 구간은 원문과 함께 표시합니다.</p>
        </div>
        <label data-ai-translate-scope-label hidden>번역 원문 범위<select data-ai-translate-scope aria-label="번역 원문 범위"><option value="current">이번 녹음만 (기본)</option><option value="all">이전 녹음을 포함한 전체 원문</option></select></label>
        <p class="ai-notes-muted" data-ai-translation-scope-note hidden></p>
        <label>번역 모델<select data-ai-translate-model aria-label="원문 번역 모델"><option value="">자동 · Gemma4 → Qwen3.6</option></select></label>
        <label class="ai-notes-consent"><input type="checkbox" data-ai-translate-auto> 새 전사 자동 번역 (이 페이지를 열어둔 동안)</label>
        <div class="ai-notes-audio-buttons"><button type="button" data-ai-translate-start disabled>미번역 원문 번역</button><button type="button" data-ai-translate-stop hidden>번역 중지</button><button type="button" data-ai-translate-clear>번역 비우기</button></div>
        <p class="ai-notes-muted">전사 텍스트와 직전 문맥을 선택한 모델로 보내 번역합니다. <span data-ai-translate-codex-notice hidden>Codex를 직접 선택하면 해당 모델을 사용하고, 자동 선택에서는 마지막 fallback으로 사용합니다. Codex 사용 시 외부 전송과 사용량이 발생합니다. </span>로컬 Whisper는 ‘말끝 우선’과 ‘고정 · 12초’를 선택할 수 있습니다. 기본 말끝 우선은 10초 이상 모은 뒤 0.8초 무음에서 처리하며, 소리가 계속되면 최대 20초에서 나눕니다. 의미상 문장 끝을 보장하지 않으며 말끝 감지에 별도 LLM은 쓰지 않습니다. 회의 종료·일시정지 때 남은 짧은 구간도 처리합니다. 전사·번역 시간은 별도로 추가됩니다.</p>
      </div>` : ""}
      <p data-ai-translation-status role="status" aria-live="polite"></p>
      <p class="ai-notes-muted" data-ai-translation-latency hidden></p>
      <p data-ai-translation-stale class="ai-notes-stale" ${stale(props) ? "" : "hidden"}>원문 또는 언어가 바뀌었습니다. 기존 번역을 비운 뒤 다시 번역하세요.</p>
      <pre data-ai-translation-text class="ai-notes-translation-text">${esc(displayedTranslation(props).current)}</pre>
      <details data-ai-translation-history ${displayedTranslation(props).previous ? "" : "hidden"}><summary>이전 번역 기록 (보존됨)</summary><pre data-ai-translation-history-text class="ai-notes-translation-text">${esc(displayedTranslation(props).previous)}</pre></details>
      <p class="ai-notes-muted">원문 전체를 구간별로 번역한 AI 초안입니다. 요약·호스트 비교 수집과 별개이며, 음성 출력은 하지 않습니다.</p>
    </div>`;
  }
  class Controller {
    constructor() {
      this.auto = false; this.busy = false; this.message = ""; this.views = new Map(); this.generation = 0;
      controllers.add(this);
    }
    activeModel(props) {
      const id = props.translation_model_id || "";
      return this.models?.some(model => model.id === id) ? id : "";
    }
    view() {
      for (const [node, view] of this.views) {
        if (node.isConnected && view.adapter.getBlock()?.type === "ai_notes" && view.adapter.canEdit()) return view;
      }
      return null;
    }
    update() {
      for (const [node, view] of this.views) {
        if (!node.isConnected) { if (view.wasConnected) this.views.delete(node); continue; }
        view.wasConnected = true;
        view.sync();
      }
    }
    stop(message = "번역을 중지했습니다. 완료된 번역과 원문은 유지됩니다.") {
      this.generation++; this.auto = false; clearTimeout(this.timer);
      clearInterval(this.progressTimer); this.requestStartedAt = null;
      this.abort?.abort(); this.message = message; this.update();
    }
    sourceChanged() {
      const props = this.view()?.adapter.getBlock()?.props || {};
      if (this.busy && this.runningScopeKey !== sourceScope(props).key) {
        const auto = this.auto;
        this.stop("이번 녹음의 새 구간을 기다립니다. 이전 원문·번역은 보존됩니다.");
        this.auto = auto;
      }
      if (!this.auto) {
        if (!sameScope(props)) { this.message = "이번 녹음의 새 묶음을 번역할 준비가 되었습니다. 이전 기록은 보존됩니다."; this.lastRequestMs = null; }
        this.update(); return;
      }
      clearTimeout(this.timer);
      // Committed STT segments can start immediately; retain a short debounce
      // for direct text edits, without a one-second delay on every utterance.
      this.timer = setTimeout(() => this.run(), 100);
    }
    async run() {
      if (this.busy) return;
      const view = this.view();
      if (!view) { this.stop("페이지 또는 편집 권한이 바뀌어 번역을 중지했습니다."); return; }
      const props = view.adapter.getBlock().props || {};
      if (stale(props)) { this.stop("기존 번역을 비운 뒤 다시 번역하세요."); return; }
      const language = target(props);
      const settings = configKey(props);
      if (!language) { this.stop("번역할 언어를 입력하세요."); return; }
      const source = props.transcript || "";
      if (source.length > 80000) { this.stop("원문은 80,000자 이하여야 합니다."); return; }
      const manualEnd = source.length;
      const scope = sourceScope(props);
      if (!sameScope(props)) this.lastRequestMs = null;
      const generation = this.generation;
      this.runningScopeKey = scope.key;
      this.busy = true; this.abort = new AbortController();
      try {
        // A previous save may have failed after a successful model response.
        // Persist that local draft even when there is no new text to translate.
        this.message = "원문·번역 저장 상태 확인 중…"; this.update();
        if (!(await view.persist())) throw new Error("원문·번역을 먼저 저장해야 합니다. 페이지 저장 상태를 확인하세요.");
        while (generation === this.generation) {
          const current = this.view();
          if (!current || current !== view || current.blocked()) throw new Error("페이지 또는 편집 상태가 바뀌어 번역을 중지했습니다.");
          const before = current.adapter.getBlock().props || {};
          if (stale(before) || configKey(before) !== settings || sourceScope(before).key !== scope.key) throw new Error("원문 또는 언어가 바뀌어 결과를 반영하지 않았습니다.");
          const storedPrefix = before.translation_source || "";
          const prefix = sourcePrefix(before);
          const full = before.transcript || "";
          const remaining = full.slice(prefix.length, this.auto ? undefined : manualEnd);
          if (!remaining) break;
          // A committed 12-second capture is one request, not per timestamp/line.
          // If the model falls behind, coalesce the queued text up to the API limit.
          const part = chunk(remaining, 2000);
          const snapshot = prefix + part;
          const model = this.activeModel(before);
          this.message = `번역 중… ${Math.max(0, prefix.length-scope.start).toLocaleString()} / ${(full.length-scope.start).toLocaleString()}자 · 전사는 계속됩니다.`; this.update();
          if (!(await current.persist())) throw new Error("원문을 먼저 저장해야 합니다. 저장 상태를 확인하세요.");
          if (generation !== this.generation || !current.adapter.canEdit() || !current.adapter.getBlock()?.props?.transcript?.startsWith(snapshot)) throw new Error("입력이 바뀌어 번역을 중지했습니다.");
          let result;
          if (part.trim()) {
            this.requestStartedAt = Date.now();
            this.progressTimer = setInterval(() => this.update(), 1000);
            this.update();
            try {
              result = await requestTranslation(current.adapter,
                {...current.adapter.scope, text: part, context: prefix.slice(Math.max(scope.start, prefix.length - 1200)), target_language: language, model_id: model,
                  ...(isPair(before) ? {translation_mode: "pair", language_pair: canonicalPair(before)} : {})}, this.abort.signal);
              if (generation === this.generation) this.lastRequestMs = Math.max(0, Date.now() - this.requestStartedAt);
            } finally { clearInterval(this.progressTimer); this.requestStartedAt = null; }
          } else result = {text: part, translation: "", target_language: language};
          if (generation !== this.generation) break;
          const live = current.adapter.getBlock()?.props;
          if (this.view() !== current || !current.adapter.canEdit() || !live || !live.transcript?.startsWith(snapshot)
              || (live.translation_source || "") !== storedPrefix || configKey(live) !== settings || sourceScope(live).key !== scope.key
              || this.activeModel(live) !== model) throw new Error("원문·모델·권한이 바뀌어 결과를 반영하지 않았습니다.");
          if (result.text !== part || result.target_language !== language || typeof result.translation !== "string" || (part.trim() && !result.translation.trim())) throw new Error("번역 응답 구간이 일치하지 않습니다. 다시 시도하세요.");
          if (part.trim() && isPair(live) && (result.translation_mode !== "pair" || JSON.stringify(result.language_pair) !== JSON.stringify(canonicalPair(live)))) throw new Error("통역 언어 쌍이 일치하지 않습니다. 다시 시도하세요.");
          const translated = [live.translation_text || "", result.translation].filter(Boolean).join("\n\n");
          if (translated.length > 240000) throw new Error("번역이 240,000자에 도달했습니다. 새 AI 노트에서 이어가세요.");
          current.mutate(value => { value.translation_source = snapshot; value.translation_text = translated;
            if (!sameScope(live)) value.translation_display_start = result.translation ? translated.length - result.translation.length : translated.length;
            value.translation_scope_key = scope.key;
            value.translation_language = language; value.translation_config = settings; value.translation_model_name = result.model_name || value.translation_model_name || ""; });
          // Paint the validated result before waiting for the shared save queue.
          // Recording/autosave may keep persistence pending; a checkbox toggle
          // must never be required to reveal an already received translation.
          this.message = "번역 수신됨 · 화면에 반영됨 · 저장 중…";
          this.update();
          const saved = await Promise.resolve().then(() => current.persist()).catch(() => false);
          if (!saved) { this.stop("번역은 화면에 있지만 저장하지 못했습니다. 페이지 저장 상태를 확인하세요."); break; }
          if (generation !== this.generation) break;
          this.message = `번역 저장됨 · ${(snapshot.length-scope.start).toLocaleString()} / ${((current.adapter.getBlock().props.transcript || "").length-scope.start).toLocaleString()}자`;
          this.update();
        }
        if (generation === this.generation) {
          const latest = view.adapter.getBlock()?.props || {};
          this.message = this.auto ? "새 전사를 기다리는 중 · 자동 번역 켜짐" :
            (latest.translation_source || "").length < (latest.transcript || "").length
              ? "요청한 구간 번역 완료 · 추가 원문은 다시 번역하세요." : "현재 원문 번역 완료 · 저장됨";
        }
      } catch (error) {
        if (generation === this.generation) this.stop(error.message || "번역하지 못했습니다. 원문은 유지됩니다.");
      } finally {
        this.busy = false; this.abort = null; this.runningScopeKey = null; this.update();
        if (generation !== this.generation && this.auto) this.sourceChanged();
      }
    }
    bind(container, adapter, mutate, persist, blocked, changed, settingsLocked = () => false) {
      const view = {adapter, mutate, persist, blocked, sync: () => {
        const props = adapter.getBlock()?.props || {};
        const editable = adapter.canEdit() && !blocked();
        const set = (selector, fn) => { const node = container.querySelector(selector); if (node) fn(node); };
        const shown = displayedTranslation(props), scope = sourceScope(props), hasPreviousRecording = recordingStart(props) > 0;
        set("[data-ai-translation-text]", node => { node.textContent = shown.current; });
        set("[data-ai-translation-history]", node => { node.hidden = !shown.previous; });
        set("[data-ai-translation-history-text]", node => { node.textContent = shown.previous; });
        set("[data-ai-translate-scope-label]", node => { node.hidden = !hasPreviousRecording; });
        set("[data-ai-translate-scope]", node => { node.value = props.translation_include_history ? "all" : "current"; node.disabled = !editable || this.busy || this.auto || settingsLocked(); });
        set("[data-ai-translation-scope-note]", node => { node.hidden = !hasPreviousRecording; node.textContent = scope.start ? "이번 녹음의 묶음만 번역합니다. 이전 미번역 원문은 자동으로 처리하지 않으며 기존 기록은 보존됩니다." : "이전 녹음을 포함한 전체 원문을 번역합니다. 범위를 바꾸면 그 범위를 처음부터 번역합니다."; });
        set("[data-ai-translation-stale]", node => { node.hidden = !stale(props); });
        set("[data-ai-translation-status]", node => { node.textContent = this.message || (shown.current ? `번역 저장됨 · ${props.translation_model_name || "AI"}` : "언어·모델을 선택하고 원문을 번역하세요."); });
        set("[data-ai-translation-latency]", node => {
          node.hidden = this.requestStartedAt == null && this.lastRequestMs == null;
          const pending = Math.max(0, (props.transcript || "").length - sourcePrefix(props).length);
          node.textContent = (this.requestStartedAt != null ? `번역 응답 대기 ${((Date.now() - this.requestStartedAt) / 1000).toFixed(1)}초` : "")
            + (this.lastRequestMs != null ? `${this.requestStartedAt != null ? " · " : ""}최근 번역 요청 ${(this.lastRequestMs / 1000).toFixed(1)}초` : "")
            + ` · 미번역 ${pending.toLocaleString()}자 · 요청 시간은 통신·서버 대기·모델 처리를 포함합니다.`;
        });
        set("[data-ai-translate-start]", node => { node.textContent = scope.start ? "이번 녹음 미번역 구간 번역" : "미번역 원문 번역"; node.disabled = !editable || !this.enabled || this.busy || !target(props) || !(props.transcript || "").slice(scope.start).trim() || stale(props); });
        set("[data-ai-translate-auto]", node => { node.checked = this.auto; node.disabled = !editable || !this.enabled || !target(props) || stale(props); });
        set("[data-ai-translate-stop]", node => { node.hidden = !this.busy && !this.auto; });
        set("[data-ai-translate-clear]", node => { node.disabled = !editable || this.busy || !fields.some(field => props[field]); });
        set("[data-ai-translate-language]", node => { node.disabled = !editable || this.busy || this.auto; });
        set("[data-ai-translate-mode]", node => { node.value = isPair(props) ? "pair" : "single"; node.disabled = !editable || this.busy || this.auto || settingsLocked(); });
        set("[data-ai-translate-single]", node => { node.hidden = isPair(props); });
        set("[data-ai-translate-pair-panel]", node => { node.hidden = !isPair(props); });
        container.querySelectorAll("[data-ai-translate-pair]").forEach(node => {
          const index = Number(node.dataset.aiTranslatePair), selected = pair(props);
          node.value = selected[index] || ""; node.disabled = !editable || this.busy || this.auto || settingsLocked();
          for (const option of node.options) option.disabled = option.value === selected[1 - index];
        });
        set("[data-ai-translate-model]", node => { node.disabled = !editable || this.busy || this.auto; });
        changed();
      }};
      // Replace the view for a re-render, but a running call retains its authority
      // adapter. Stable view identity avoids discarding harmless editor repaints.
      const previous = this.views.get(container);
      if (previous && previous.adapter.scope.page_id === adapter.scope.page_id) Object.assign(previous, view);
      else this.views.set(container, view);
      container.querySelector("[data-ai-translate-mode]")?.addEventListener("change", event => {
        if (!adapter.canEdit() || this.busy || this.auto || blocked() || settingsLocked()) return;
        if (!["single", "pair"].includes(event.target.value)) return;
        mutate(props => { props.translation_mode = event.target.value; }); this.update();
      });
      container.querySelectorAll("[data-ai-translate-pair]").forEach(node => node.addEventListener("change", event => {
        if (!adapter.canEdit() || this.busy || this.auto || blocked() || settingsLocked()) return;
        const selected = pair(adapter.getBlock().props || {});
        selected[Number(node.dataset.aiTranslatePair)] = event.target.value;
        if (!pair({translation_pair: selected}).length) return;
        mutate(props => { props.translation_pair = selected; }); this.update();
      }));
      container.querySelector("[data-ai-translate-language]")?.addEventListener("input", event => {
        if (!adapter.canEdit() || this.busy || this.auto || blocked()) return;
        mutate(props => { props.translation_target = event.target.value; }); this.update();
      });
      container.querySelector("[data-ai-translate-model]")?.addEventListener("change", event => {
        if (!adapter.canEdit() || this.busy || this.auto || blocked()) return;
        if (event.target.value && !this.models?.some(model => model.id === event.target.value)) return;
        mutate(props => { props.translation_model_id = event.target.value; });
      });
      container.querySelector("[data-ai-translate-scope]")?.addEventListener("change", event => {
        if (!adapter.canEdit() || this.busy || this.auto || blocked() || settingsLocked()) return;
        mutate(props => { props.translation_include_history = event.target.value === "all"; });
        this.message = "번역 범위를 변경했습니다. 원문·이전 번역 기록은 보존됩니다."; this.update();
      });
      container.querySelector("[data-ai-translate-start]")?.addEventListener("click", () => { if (!blocked() && this.enabled) this.run(); });
      container.querySelector("[data-ai-translate-auto]")?.addEventListener("change", event => {
        if (blocked() || !adapter.canEdit() || !this.enabled) { this.update(); return; }
        if (!event.target.checked) { this.stop(); return; }
        this.auto = true; this.message = "자동 번역 켜짐 · 전사 텍스트를 선택한 모델로 전송합니다."; this.update(); this.run();
      });
      container.querySelector("[data-ai-translate-stop]")?.addEventListener("click", () => this.stop());
      container.querySelector("[data-ai-translate-clear]")?.addEventListener("click", async () => {
        if (!adapter.canEdit() || this.busy || blocked() || !window.confirm("번역만 비울까요? 원문·메모·요약은 유지됩니다.")) return;
        if (!adapter.canEdit() || this.busy || blocked()) return;
        this.stop(); mutate(props => { for (const field of fields) delete props[field]; });
        this.message = await persist() ? "번역을 비우고 저장했습니다." : "번역 비우기를 저장하지 못했습니다. 페이지 저장 상태를 확인하세요."; this.update();
      });
      if (this.models) this.capabilities({models: this.models, codex_bridge: this.bridge, translation: {enabled: this.enabled}});
      setTimeout(() => this.update(), 0);
    }
    capabilities(data) {
      this.bridge = data.codex_bridge || {};
      this.models = allowedModels(data);
      this.enabled = data.translation?.enabled !== false && Boolean(this.models.length);
      for (const [node, view] of this.views) {
        const select = node.querySelector("[data-ai-translate-model]");
        if (!select) continue;
        const value = this.activeModel(view.adapter.getBlock()?.props || {});
        select.replaceChildren(new Option(autoLabel(this.models), ""));
        for (const model of this.models) {
          select.add(new Option(model.name, model.id));
        }
        select.value = value;
        const notice = node.querySelector("[data-ai-translate-codex-notice]");
        if (notice) notice.hidden = !this.models.some(model => model.provider === "codex");
      }
      this.update();
    }
  }
  async function beforeNavigate() {
    const active = [...controllers].filter(controller => controller.busy || controller.auto);
    if (active.length && !window.confirm("자동 번역을 중지하고 이동할까요? 저장된 번역·원문은 유지됩니다.")) return false;
    active.forEach(controller => controller.stop()); return true;
  }
  if (typeof window !== "undefined") {
    window.addEventListener("beforeunload", event => { if ([...controllers].some(c => c.busy)) { event.preventDefault(); event.returnValue = ""; } });
    window.addEventListener("pagehide", () => controllers.forEach(c => c.stop()));
  }
  return {html, Controller, beforeNavigate, chunk, stale, fields, allowedModels, autoLabel, isPair, pair, configKey, audioLanguage, recordingStart, sourceScope, sourcePrefix, displayedTranslation};
});
