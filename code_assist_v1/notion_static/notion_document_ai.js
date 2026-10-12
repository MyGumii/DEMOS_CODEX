/* Document AI keeps drafts in this tab only. All writes go through the editor. */
(function () {
  "use strict";
  let host, serial = 0, busy = false, models = [], target = null, result = null, history = [];
  const el = id => document.getElementById(id);
  const prompts = {
    summary: "현재 페이지의 핵심 내용과 결론을 간결하게 요약해줘. 없는 사실은 추가하지 마.",
    tasks: "현재 페이지에서 실제로 해야 할 일을 추출해줘. 담당자와 기한은 원문에 있을 때만 적어줘.",
    draft: "현재 페이지의 내용을 바탕으로 초안을 작성해줘. 아래에 원하는 형식과 목적을 추가할게:\n",
  };
  const edits = {
    improve: "의미와 사실을 유지하면서 더 명확하고 자연스럽게 다듬어줘. 결과 텍스트만 반환해줘.",
    spelling: "맞춤법과 문법만 수정하고 의미와 어조는 유지해줘. 결과 텍스트만 반환해줘.",
    shorter: "핵심 의미를 유지하면서 더 짧게 써줘. 결과 텍스트만 반환해줘.",
    longer: "원문에 없는 사실은 추가하지 말고 설명을 더 읽기 쉽게 풀어 써줘. 결과 텍스트만 반환해줘.",
    professional: "의미를 유지하면서 전문적인 업무 문체로 바꿔줘. 결과 텍스트만 반환해줘.",
  };
  function status(message, error = false) {
    el("documentAiStatus").textContent = message;
    el("documentAiStatus").dataset.error = String(error);
  }
  function paint() {
    const page = host.current();
    el("documentAiContext").textContent = page.pageId ? `${target ? "선택한 글" : "현재 페이지"} · ${page.title}` : "먼저 페이지를 열어 주세요.";
    el("documentAiSelection").hidden = !target;
    el("documentAiSource").textContent = target?.text || "";
    el("aiAskBtn").disabled = busy || !models.length || !page.pageId;
    el("aiAskBtn").textContent = busy ? "생성 중…" : "보내기";
    el("aiPanel").setAttribute("aria-busy", String(busy));
    el("documentAiModel").disabled = busy;
    document.querySelectorAll("[data-document-ai-prompt], [data-document-ai-edit]").forEach(button => {
      button.disabled = busy || !models.length || !page.pageId || (button.hasAttribute("data-document-ai-edit") && !target);
    });
    el("aiResultActions").hidden = !result;
    document.querySelectorAll("[data-ai-result-action]").forEach(button => {
      const action = button.dataset.aiResultAction;
      button.hidden = action === "replace" && !result?.target;
      button.disabled = busy || (["replace", "insert"].includes(action) && !host.canEdit());
      if (action === "insert") button.textContent = result?.target ? "아래에 삽입" : "페이지 끝에 삽입";
    });
    el("documentAiHistory").replaceChildren(...history.slice(0, -2).map(message => {
      const node = document.createElement("div");
      node.className = "document-ai-turn";
      node.dataset.role = message.role;
      node.textContent = `${message.role === "user" ? "나" : "AI"} · ${message.content}`;
      return node;
    }));
  }
  function invalidate() {
    serial += 1; busy = false; target = null; result = null; history = [];
    if (!host) return;
    el("aiAnswer").textContent = "";
    el("aiQuestion").value = "";
    status(""); paint();
  }
  let capabilitySerial = 0;
  async function capabilities() {
    const requestId = ++capabilitySerial;
    el("documentAiCapabilities").textContent = "모델 연결 확인 중…";
    try {
      const data = await host.api("/api/notion/document-ai/capabilities");
      if (requestId !== capabilitySerial) return;
      models = Array.isArray(data.models) ? data.models : [];
      const selector = el("documentAiModel"), selected = selector.value;
      selector.replaceChildren(new Option("자동 · 사내 모델 우선", ""), ...models.map(model => new Option(model.name, model.id)));
      selector.value = models.some(model => model.id === selected) ? selected : "";
      el("documentAiCapabilities").textContent = models.length
        ? `자동 선택 순서: ${models.map(model => model.name).join(" → ")}`
        : "연결된 모델이 없습니다. 호스트의 AI 연결 설정을 확인하세요.";
    } catch (error) {
      if (requestId !== capabilitySerial) return;
      models = [];
      el("documentAiModel").replaceChildren(new Option("자동 선택", ""));
      el("documentAiCapabilities").textContent = `연결 확인 실패: ${error.message}`;
    }
    paint();
  }
  async function ask() {
    if (busy) return;
    const question = el("aiQuestion").value.trim(), origin = host.current();
    if (!question || !origin.pageId) return status("페이지를 열고 질문을 입력하세요.", true);
    if (!models.length) return status("먼저 모델 연결을 확인하세요.", true);
    const requestId = ++serial, selection = target;
    busy = true; result = null; el("aiAnswer").textContent = ""; paint();
    status("최신 본문 확인 중…");
    try {
      if (selection && !host.selectionValid(selection)) throw new Error("선택한 원문이 변경되었습니다. 다시 선택하세요.");
      if (host.canEdit() && !(await host.flush(origin.pageId))) throw new Error("페이지 저장을 마친 뒤 다시 요청하세요.");
      if (requestId !== serial || host.current().key !== origin.key) return;
      const snapshot = host.current();
      status("AI 응답 생성 중… 원문은 변경하지 않습니다.");
      const data = await host.api("/api/notion/document-ai/ask", {method: "POST", body: {
        page_id: snapshot.pageId, workspace_id: snapshot.workspaceId,
        question, model_id: el("documentAiModel").value,
        ...(selection ? {block_id: selection.blockId, selected_text: selection.text} : {}),
        history: history.slice(-6),
      }});
      if (requestId !== serial || host.current().key !== origin.key) return;
      if (host.current().fingerprint !== snapshot.fingerprint) throw new Error("생성 중 원문이 변경되었습니다. 최신 내용으로 다시 요청하세요.");
      if (typeof data.answer !== "string" || !data.answer.trim()) throw new Error("AI 응답이 비어 있습니다.");
      result = {answer: data.answer, target: selection, snapshot, question};
      history.push({role: "user", content: question}, {role: "assistant", content: data.answer});
      history = history.slice(-6);
      el("aiAnswer").textContent = data.answer;
      status(`${data.model_name || "AI"} · ${data.source?.scope === "selection" ? "선택한 글" : "현재 페이지"} 참고 · 적용 전 내용을 확인하세요.`);
    } catch (error) {
      if (requestId === serial) status(error.message, true);
    } finally {
      if (requestId === serial) { busy = false; paint(); }
    }
  }
  function select(selection) {
    invalidate(); target = selection; paint();
    el("aiQuestion").value = edits.improve;
  }
  function run(action) {
    if (busy) return;
    el("aiQuestion").value = action === "translate"
      ? `${el("documentAiLanguage").value.trim() || "영어"}로 번역해줘. 번역문만 반환해줘.` : (edits[action] || edits.improve);
    void ask();
  }
  function apply(action) {
    if (action === "discard") { invalidate(); return; }
    if (busy || !result) return;
    if (action === "retry") { el("aiQuestion").value = result.question; void ask(); return; }
    try {
      if (!host.canEdit() || host.current().key !== result.snapshot.key || host.current().fingerprint !== result.snapshot.fingerprint)
        throw new Error("원문 또는 편집 권한이 변경되었습니다. 최신 내용으로 다시 요청하세요.");
      host.apply(result, action);
      result = null; target = null; history = []; paint(); status("AI 결과를 반영했습니다. 기존 자동 저장으로 저장됩니다.");
    } catch (error) { status(error.message, true); }
  }
  window.NotionDocumentAI = {
    init(adapter) {
      host = adapter;
      el("documentAiReconnect").addEventListener("click", capabilities);
      el("documentAiClear").addEventListener("click", invalidate);
      el("documentAiPageScope").addEventListener("click", invalidate);
      el("aiQuestion").addEventListener("keydown", event => {
        if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && !event.isComposing) { event.preventDefault(); void ask(); }
      });
      document.querySelectorAll("[data-document-ai-edit]").forEach(button => button.addEventListener("click", () => run(button.dataset.documentAiEdit)));
      document.querySelectorAll("[data-document-ai-prompt]").forEach(button => button.addEventListener("click", () => {
        if (busy) return;
        invalidate(); el("aiQuestion").value = prompts[button.dataset.documentAiPrompt];
        if (button.dataset.documentAiPrompt === "draft") el("aiQuestion").focus(); else void ask();
      }));
    },
    open() { paint(); void capabilities(); }, invalidate, ask, select, run, apply,
  };
})();
