(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  let host, visible = false, busy = false, serial = 0, listSerial = 0, timer = null;
  let cid = "", conversation = null, models = [], mode = "chat", scope = {page_ids: [], knowledge_space_id: ""};
  let historyCursor = null, wikiCursor = null, createOperation = "", pending = null;
  let historyVisible = false, historyKey = "", historySession = null, historyLoading = false;
  let notionConnected = false;
  let sourceOptions = {notion:true, web:false}, webAvailable = false, savePageAvailable = false;
  let streamingEnabled = false, streamController = null, streamDraft = null, streamPaint = null;
  let historySearchTimer = null, historyComposing = false, historyGroups = new Map(), historyIds = new Set();
  const modeHelp = {
    chat: "Enter로 전송 · Shift+Enter로 줄바꿈 · 내 Notion을 질문하면 관련 페이지를 자동으로 참고합니다.",
    slides: "HTML 슬라이드 · 최대 12장 · 생성 후 다운로드해 브라우저에서 열 수 있습니다.",
    spreadsheet: "CSV 표 · 최대 200행 · 다운로드 후 Excel 등에서 열 수 있습니다. XLSX·수식 실행은 지원하지 않습니다.",
    research: "조사 계획 · 원문 수집 · 추가 조사 · 장별 보고서 · 근거 검토. 화면을 닫아도 조사는 계속됩니다.",
    visualization: "숫자 자료로 막대차트를 만듭니다. 생성 후 SVG로 다운로드할 수 있습니다.",
  };
  const prompts = {slides: "다음 주제로 발표 자료를 만들어줘: ", spreadsheet: "다음 내용을 표로 정리해줘: ", research: "선택한 자료에서 다음 주제를 조사하고 출처와 함께 정리해줘: ", visualization: "다음 숫자 자료를 막대차트로 만들어줘: "};
  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16)); bytes[6] = bytes[6] & 15 | 64; bytes[8] = bytes[8] & 63 | 128;
    return [...bytes].map((b, i) => ([4, 6, 8, 10].includes(i) ? "-" : "") + b.toString(16).padStart(2, "0")).join("");
  }
  function node(tag, content, className) {
    const result = document.createElement(tag);
    if (content !== undefined) result.textContent = content;
    if (className) result.className = className;
    return result;
  }
  function safeSourceLink(title, url) {
    if (typeof url !== "string") return null;
    const internal = /^\/page\/[\w-]+$/.test(url);
    if (!internal) {
      try { const parsed = new URL(url); if (parsed.protocol !== "https:" || parsed.username || parsed.password) return null; }
      catch (_) { return null; }
    }
    const link = node("a", title); link.href = url;
    if (!internal) { link.target = "_blank"; link.rel = "noopener noreferrer"; }
    return link;
  }
  async function saveReport(message, button) {
    const token = serial, origin = host.key(), chatId = cid;
    button.disabled = true; button.textContent = "저장 중…";
    try {
      const result = await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(chatId)}/save-report`, {
        method:"POST", body:{message_operation_id:message.operation_id, operation_id:message.operation_id},
      });
      if (token !== serial || origin !== host.key() || !visible) return;
      button.textContent = "저장한 페이지 열기"; status("내 Notion에 보고서를 저장했습니다.");
      button.disabled = false;
      button.dataset.savedPageId = result.page_id;
      const link = safeSourceLink(result.title, result.url);
      if (link) button.parentElement.append(link);
    } catch (error) {
      if (token === serial && origin === host.key() && visible) { button.disabled = false; button.textContent = "페이지로 저장"; status(error.message, true); }
    }
  }
  function answerBody(text, citations = []) {
    if (window.NotionAiAnswer) {
      const body = window.NotionAiAnswer.render(text);
      function linkCitations(parent) {
        for (const child of [...parent.childNodes]) {
          if (child.nodeType === 3) {
            const parts = child.textContent.split(/(\[\d+\])/g);
            if (parts.length < 2) continue;
            const replacement = document.createDocumentFragment();
            for (const part of parts) {
              const match = /^\[(\d+)\]$/.exec(part), citation = match ? citations[Number(match[1])-1] : null;
              const link = citation ? safeSourceLink(part, citation.url) : null;
              if (link) { link.title = citation.title + (citation.location ? " · " + citation.location : ""); link.className = "ai-home-inline-citation"; replacement.append(link); }
              else replacement.append(document.createTextNode(part));
            }
            child.replaceWith(replacement);
          } else if (child.nodeType === 1 && !["A", "CODE", "PRE"].includes(child.tagName) && !child.classList.contains("katex")) linkCitations(child);
        }
      }
      if (citations.length) linkCitations(body);
      return body;
    }
    // Small DOM-only Markdown subset. Model output never becomes HTML or URLs.
    const body = node("div", undefined, "ai-home-message-text ai-home-markdown");
    function inline(target, value) {
      for (const part of value.split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g)) {
        if (part.startsWith("**") && part.endsWith("**")) target.append(node("strong", part.slice(2, -2)));
        else if (part.startsWith("`") && part.endsWith("`")) target.append(node("code", part.slice(1, -1)));
        else target.append(document.createTextNode(part));
      }
    }
    let list = null, code = null;
    for (const line of String(text || "").split("\n")) {
      if (line.trimStart().startsWith("```")) {
        list = null;
        if (code) code = null;
        else { const pre = node("pre"); code = node("code"); pre.append(code); body.append(pre); }
        continue;
      }
      if (code) { code.append(document.createTextNode(line + "\n")); continue; }
      const heading = line.match(/^(#{1,4})\s+(.+)$/), bullet = line.match(/^\s*([-*]|\d+[.)])\s+(.+)$/);
      if (bullet) {
        const tag = /^[\d]/.test(bullet[1]) ? "ol" : "ul";
        if (!list || list.tagName.toLowerCase() !== tag) { list = node(tag); body.append(list); }
        const item = node("li"); inline(item, bullet[2]); list.append(item); continue;
      }
      list = null;
      if (!line.trim()) continue;
      const part = node(heading ? "h" + Math.min(4, heading[1].length + 1) : "p");
      inline(part, heading ? heading[2] : line); body.append(part);
    }
    return body;
  }
  function status(text, error = false) { $("aiHomeStatus").textContent = text; $("aiHomeStatus").dataset.error = String(error); }
  function snapshotKey() { return host.key() + "/" + cid; }
  function controls() {
    const locked = busy || conversation?.pending || conversation?.archived;
    $("aiHomeSend").disabled = Boolean(locked || !models.length);
    $("aiHomeSend").textContent = busy || conversation?.pending ? "생성 중…" : "↑ 보내기";
    $("aiHomeInput").disabled = Boolean(locked);
    $("aiHomeModel").disabled = Boolean(locked);
    $("aiHomeWiki").disabled = Boolean(locked || $("aiHomeWiki").options.length < 2);
    $("aiHomePageChoices").querySelectorAll("input").forEach(input => { input.disabled = Boolean(locked); });
    for (const [id, key] of [["aiHomeWebSwitch", "web"], ["aiHomeNotionSwitch", "notion"]]) {
      const button = $(id); if (!button) continue;
      button.disabled = Boolean(locked);
      button.setAttribute("aria-checked", String(sourceOptions[key]));
    }
    document.querySelectorAll("[data-ai-home-mode]").forEach(button => { button.disabled = Boolean(locked); button.setAttribute("aria-pressed", String(button.dataset.aiHomeMode === mode)); });
    $("aiHomeModeHelp").textContent = mode === "chat" && !notionConnected
      ? "Enter로 전송 · Shift+Enter로 줄바꿈 · 참고할 페이지·Wiki를 선택할 수 있습니다." : modeHelp[mode];
    $("aiHomeTitle").textContent = conversation?.title || "새 채팅";
    const heroTitle = $("aiHomeHero").querySelector("h1");
    if (heroTitle) heroTitle.textContent = mode === "research" ? "데이터와 웹 전반에 걸쳐 리서치" : "무엇을 도와드릴까요?";
    const labels = scope.page_ids.map(id => host.pages().find(page => page.id === id)?.title || "선택한 페이지");
    if (scope.knowledge_space_id) labels.push($("aiHomeWiki").selectedOptions[0]?.textContent || "선택한 Wiki");
    const internal = sourceOptions.notion ? labels.length ? `참고: ${labels.join(" · ")}`
      : notionConnected ? "내 Notion 연결됨 · 질문에 맞는 페이지 자동 참고" : "참고할 페이지·Wiki를 선택하세요" : "내 자료 검색 꺼짐";
    $("aiHomeScope").textContent = `${internal}${sourceOptions.web ? " · 웹 검색 켜짐" : ""}`;
    if ($("aiHomeWebStatus")) $("aiHomeWebStatus").textContent = webAvailable ? "웹 검색에는 현재 질문만 전달합니다." : sourceOptions.web ? "웹 검색 서버 연결이 필요합니다. 연결 전에는 웹 검색을 끄고 이용하세요." : "웹 검색 서버가 아직 연결되지 않았습니다.";
  }
  function pageChoices() {
    const filter = $("aiHomePageFilter").value.trim().toLowerCase();
    const pages = host.pages().filter(page => !filter || String(page.title || "").toLowerCase().includes(filter));
    $("aiHomePageChoices").replaceChildren(...pages.map(page => {
      const label = node("label"), input = node("input"); input.type = "checkbox"; input.value = page.id;
      input.checked = scope.page_ids.includes(page.id); input.setAttribute("aria-label", `참고 페이지: ${page.title || "제목 없음"}`);
      input.addEventListener("change", () => {
        const ids = new Set(scope.page_ids);
        if (input.checked) ids.add(page.id); else ids.delete(page.id);
        if (ids.size > 4) { input.checked = false; return status("페이지는 최대 4개까지 선택하세요.", true); }
        changeScope({...scope, page_ids: [...ids].sort()});
      });
      label.append(input, node("span", page.title || "제목 없음")); return label;
    }));
    controls();
  }
  function changeScope(next) {
    if (busy || conversation?.pending) return;
    // A lost create response must not bind a changed scope to the old request ID.
    createOperation = ""; pending = null;
    if (cid) {
      serial += 1; clearTimeout(timer); cid = ""; conversation = null; pending = null; createOperation = "";
      host.route("", true); $("aiHomeMessages").replaceChildren(); $("aiHomeHero").hidden = false;
      status("참고 범위가 바뀌어 새 대화를 시작합니다. 이전 대화는 기록에 남아 있습니다.");
    }
    scope = next; controls();
  }
  function addWikis(data, reset = false) {
    const selector = $("aiHomeWiki");
    if (reset) selector.replaceChildren(new Option("참고하지 않음", ""));
    const existing = new Set([...selector.options].map(option => option.value));
    const kinds = {personal: "개인", part: "파트", team: "팀", responsibility: "담당"};
    for (const item of data.items || []) {
      const kind = item.kind || "personal", groupLabel = kinds[kind];
      if (!groupLabel || existing.has(item.space_id)) continue;
      let group = [...selector.children].find(child => child.tagName === "OPTGROUP" && child.label === groupLabel);
      if (!group) { group = node("optgroup"); group.label = groupLabel; selector.append(group); }
      const path = Array.isArray(item.org_path) ? item.org_path.join(" / ") : "";
      const label = path ? `${item.display_name} — ${path}` : item.display_name;
      group.append(new Option(label, item.space_id)); existing.add(item.space_id);
    }
    if (scope.knowledge_space_id && ![...selector.options].some(option => option.value === scope.knowledge_space_id)) selector.append(new Option("이 대화의 Wiki · 목록에서 확인되지 않음", scope.knowledge_space_id));
    selector.value = scope.knowledge_space_id;
    wikiCursor = data.next_cursor; $("aiHomeWikiMore").hidden = !wikiCursor;
  }
  async function capabilities(token) {
    try {
      const data = await host.api("/api/notion/ai-home/capabilities");
      if (serial !== token || !visible) return;
      models = Array.isArray(data.models) ? data.models : [];
      notionConnected = data.notion?.enabled === true;
      streamingEnabled = data.streaming === true;
      webAvailable = data.web_search === true;
      savePageAvailable = data.research?.save_page === true;
      const selected = $("aiHomeModel").value;
      $("aiHomeModel").replaceChildren(new Option("자동 · 사내 모델 우선", ""), ...models.map(model => new Option(model.name, model.id)));
      $("aiHomeModel").value = models.some(model => model.id === selected) ? selected : "";
      addWikis(data.knowledge || {}, true);
      $("aiHomeKnowledgeStatus").textContent = !data.knowledge?.enabled ? "현재 호스트에서 Wiki 연결을 사용할 수 없습니다."
        : data.knowledge.mode === "legacy-personal" ? "현재 호스트는 개인 파일 Wiki에 연결되어 있습니다. 조직 Wiki는 공용 지식 읽기 서비스가 연결된 호스트에서 표시됩니다."
        : "현재 읽기 권한이 있는 개인·파트·팀·담당 Wiki 중 하나를 선택하세요. 선택을 바꾸면 새 대화가 시작됩니다.";
      if (!models.length) status("연결된 모델이 없습니다. 호스트 설정 확인 후 이 화면을 다시 여세요.", true);
    } catch (error) { if (serial === token) { models = []; notionConnected = false; status("AI 홈 연결 확인 실패: " + error.message, true); } }
    if (serial === token) controls();
  }
  function historyDateGroup(seconds, now = new Date()) {
    const date = new Date(Number(seconds) * 1000);
    if (seconds == null || !Number.isFinite(date.getTime())) return {key:"unknown", label:"날짜 없음"};
    const day = value => Date.UTC(value.getFullYear(), value.getMonth(), value.getDate());
    const days = Math.round((day(now) - day(date)) / 86400000);
    if (days <= 0) return {key:"today", label:"오늘"};
    if (days === 1) return {key:"yesterday", label:"어제"};
    if (days < 7) return {key:"week", label:"지난 7일"};
    if (days < 30) return {key:"month", label:"지난 30일"};
    return {key:`${date.getFullYear()}-${date.getMonth()+1}`, label:`${date.getFullYear()}년 ${date.getMonth()+1}월`};
  }
  function invalidateHistory() {
    clearTimeout(historySearchTimer); historySearchTimer = null;
    historySession = null; listSerial += 1; historyLoading = false; historyCursor = null;
    historyGroups = new Map(); historyIds = new Set();
    $("aiHomeHistory").replaceChildren(); $("aiHomeHistoryMore").hidden = true;
    $("aiHomeHistoryStatus").textContent = "";
    $("aiHomeHistory").setAttribute("aria-busy", "false");
    $("aiHomeHistoryTitle").textContent = "오늘";
  }
  function searchHistory() {
    if (!historyVisible || historyKey !== host.key()) return;
    invalidateHistory();
    if (historyComposing) return;
    $("aiHomeHistoryStatus").textContent = "대화 기록을 검색하는 중…";
    historySearchTimer = setTimeout(() => { historySearchTimer = null; void history(); }, 200);
  }
  function setHistorySearch(opened, restore = true) {
    $("aiHomeHistorySearchBox").hidden = !opened;
    $("aiHomeHistoryHeading").hidden = opened;
    $("aiHomeHistorySearchBtn").setAttribute("aria-expanded", String(opened));
    $("aiHomeSidebar").classList.toggle("history-search-open", opened);
    if (opened) $("aiHomeHistorySearch").focus();
    else {
      const changed = Boolean($("aiHomeHistorySearch").value) || historySearchTimer !== null || !historySession;
      $("aiHomeHistorySearch").value = ""; historyComposing = false;
      clearTimeout(historySearchTimer); historySearchTimer = null;
      if (changed && historyVisible && historyKey === host.key()) void history();
      if (restore) $("aiHomeHistorySearchBtn").focus();
    }
  }
  async function history(more = false) {
    if (!historyVisible || historyComposing || historyKey !== host.key() || (more && (historyLoading || !historyCursor))) return;
    clearTimeout(historySearchTimer); historySearchTimer = null;
    if (!more) invalidateHistory();
    const requestId = ++listSerial, origin = host.key(), search = $("aiHomeHistorySearch").value.trim();
    const archived = $("aiHomeArchived").checked;
    if (!more) historySession = {};
    const session = historySession;
    const current = () => historyVisible && session === historySession && origin === host.key()
      && archived === $("aiHomeArchived").checked && search === $("aiHomeHistorySearch").value.trim() && !historyComposing;
    historyLoading = true; $("aiHomeHistoryMore").disabled = true;
    $("aiHomeHistory").setAttribute("aria-busy", "true");
    $("aiHomeHistoryStatus").textContent = "대화 기록을 불러오는 중…";
    const query = new URLSearchParams({workspace_id: host.workspaceId(), archived: archived ? "1" : "0"});
    if (search) query.set("q", search);
    if (more && historyCursor) query.set("cursor", historyCursor);
    try {
      const data = await host.api("/api/notion/ai-home/conversations?" + query);
      if (requestId !== listSerial || !current()) return;
      for (const item of data.items || []) {
        if (historyIds.has(item.id)) continue;
        historyIds.add(item.id);
        const date = historyDateGroup(item.updated_at);
        if (!historyGroups.has(date.key)) {
          const group = node("section", undefined, "ai-history-group"); group.setAttribute("aria-label", date.label);
          const heading = node("h3", date.label, "ai-history-date"), list = node("div"); list.setAttribute("role", "list");
          group.append(heading, list); $("aiHomeHistory").append(group); historyGroups.set(date.key, list);
          if (historyGroups.size === 1) $("aiHomeHistoryTitle").textContent = date.label;
        }
        const row = node("div", undefined, "ai-history-item"); row.dataset.active = String(visible && item.id === cid); row.setAttribute("role", "listitem");
        row.dataset.chatId = item.id;
        const openButton = node("button", item.title); openButton.type = "button"; openButton.title = item.title;
        openButton.addEventListener("click", () => { if (current()) void open(item.id); });
        const archiveButton = node("button", item.archived ? "↶" : "⋯"); archiveButton.type = "button";
        archiveButton.setAttribute("aria-label", `${item.title} ${item.archived ? "복원" : "보관"}`);
        let archiving = false;
        archiveButton.addEventListener("click", async () => {
          if (!current() || archiving) return;
          archiving = true; archiveButton.disabled = true;
          try {
            await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(item.id)}/archive`, {method: "POST", body: {operation_id: uuid(), archived: !item.archived}});
            if (!current()) return;
            if (visible && cid === item.id) await open(""); else await history();
          } catch (error) { if (current()) $("aiHomeHistoryStatus").textContent = "대화 기록 변경 실패: " + error.message; }
          finally { archiving = false; archiveButton.disabled = false; }
        });
        row.append(openButton, archiveButton); historyGroups.get(date.key).append(row);
      }
      historyCursor = data.next_cursor; $("aiHomeHistoryMore").hidden = !historyCursor;
      $("aiHomeHistoryStatus").textContent = historyIds.size ? "" : search ? "검색된 채팅이 없습니다." : archived ? "보관된 채팅이 없습니다." : "아직 채팅이 없습니다.";
    } catch (error) { if (requestId === listSerial && current()) $("aiHomeHistoryStatus").textContent = "기록을 불러오지 못했습니다: " + error.message; }
    finally { if (requestId === listSerial) { historyLoading = false; $("aiHomeHistoryMore").disabled = false; $("aiHomeHistory").setAttribute("aria-busy", "false"); } }
  }
  function showHistory() {
    if (!host || (historyVisible && historyKey === host.key())) return;
    if (historyKey !== host.key()) {
      historyVisible = false;
      $("aiHomeArchived").checked = false; $("aiHomeHistorySearch").value = "";
      setHistorySearch(false, false);
    }
    historyKey = host.key(); historyVisible = true;
    return history();
  }
  function hideHistory() {
    historyVisible = false; historyComposing = false; invalidateHistory();
  }
  async function download(message, kind) {
    const origin = snapshotKey();
    try {
      const response = await host.fetch(`/api/notion/ai-home/conversations/${encodeURIComponent(cid)}/messages/${encodeURIComponent(message.operation_id)}/download/${kind}`);
      if (!response.ok) throw new Error("다운로드 권한 또는 연결 상태를 확인하세요.");
      const blob = await response.blob();
      if (origin !== snapshotKey() || !visible) return;
      const url = URL.createObjectURL(blob), link = node("a"); link.href = url; link.download = `ai-${message.artifact?.kind || "answer"}.${kind}`;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 30000);
    } catch (error) { status(error.message, true); }
  }
  function artifactPreview(artifact) {
    function text(tag, value) {
      const element = node(tag, undefined, "ai-home-artifact-text");
      if (window.NotionAiAnswer?.renderText) window.NotionAiAnswer.renderText(element, value);
      else element.textContent = String(value ?? "");
      return element;
    }
    const box = node("div", undefined, "ai-home-artifact"); box.append(text("strong", artifact.title));
    if (artifact.kind === "slides") {
      artifact.slides.forEach((slide, index) => {
        const section = node("section", undefined, "ai-home-slide"), list = node("ul");
        section.append(node("small", `${index+1} / ${artifact.slides.length}`), text("h3", slide.title));
        slide.bullets.forEach(bullet => list.append(text("li", bullet))); section.append(list); box.append(section);
      });
    } else if (artifact.kind === "spreadsheet") {
      const table = node("table"), heading = node("tr"); artifact.columns.forEach(title => heading.append(text("th", title))); table.append(heading);
      artifact.rows.forEach(row => { const tr = node("tr"); row.forEach(value => tr.append(text("td", value))); table.append(tr); }); box.append(table);
    } else if (artifact.kind === "visualization") {
      const low = Math.min(0, ...artifact.values), high = Math.max(0, ...artifact.values), span = high-low || 1;
      const position = value => (value-low) / span * 100, zero = position(0);
      artifact.labels.forEach((label, index) => {
        const row = node("div", undefined, "ai-home-bar"), track = node("div", undefined, "ai-home-bar-track");
        const bar = node("div", undefined, "ai-home-bar-fill"), axis = node("div", undefined, "ai-home-bar-axis"), point = position(artifact.values[index]);
        bar.style.left = `${Math.min(point, zero)}%`; bar.style.width = `${Math.abs(point-zero)}%`; axis.style.left = `${zero}%`;
        track.append(bar, axis); track.setAttribute("aria-hidden", "true");
        row.append(text("span", label), track, node("span", `${artifact.values[index]} ${artifact.unit}`)); box.append(row);
      });
    }
    return box;
  }
  function reportContents(reportBody) {
    const toc = node("nav", undefined, "ai-home-report-toc"); toc.setAttribute("aria-label", "보고서 목차");
    // The answer renderer maps Markdown #/## to h2/h3.
    for (const heading of reportBody.querySelectorAll("h2, h3")) {
      const jump = node("button", heading.textContent); jump.type = "button";
      jump.addEventListener("click", () => heading.scrollIntoView({block:"start"})); toc.append(jump);
    }
    return toc;
  }
  function render() {
    const scroll = $("aiHomeScroll"), follow = scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight < 100, previousTop = scroll.scrollTop;
    const expanded = new Set([...$("aiHomeMessages").querySelectorAll("details[open]")].map(item => item.querySelector("summary")?.textContent));
    $("aiHomeView").dataset.empty = String(!conversation?.messages?.length);
    $("aiHomeHero").hidden = Boolean(conversation?.messages?.length);
    $("aiHomeMessages").replaceChildren(...(conversation?.messages || []).map(message => {
      const row = node("article", undefined, "ai-home-message"); row.dataset.role = message.role;
      row.append(node("small", message.role === "user" ? "나" : message.model_name || "AI"));
      if (message.role === "user") row.append(node("div", message.text, "ai-home-message-text"));
      else if (message.context_info?.research?.depth === "deep") {
        const full = node("details", undefined, "ai-home-full-report"); full.append(node("summary", "전체 심층 보고서 보기"));
        const reportBody = answerBody(message.text, message.citations);
        full.append(reportContents(reportBody), reportBody);
        const summary = message.text.match(/## \d+\. 요약과 결론\n([\s\S]*?)(?=\n## \d+\.|$)/)?.[1];
        row.append(answerBody(summary || message.text.slice(0, 1200), message.citations), full);
      } else row.append(answerBody(message.text, message.citations));
      if (message.role === "assistant") {
        if (message.artifact) row.append(artifactPreview(message.artifact));
        const actions = node("div", undefined, "ai-home-result-actions");
        const copy = node("button", "복사"); copy.type = "button"; copy.addEventListener("click", () => navigator.clipboard.writeText(message.text).then(() => status("답변을 복사했습니다.")).catch(() => status("복사 권한을 확인하세요.", true))); actions.append(copy);
        const formats = ["md"];
        if (message.artifact) formats.push({slides: "html", spreadsheet: "csv", visualization: "svg"}[message.artifact.kind]);
        formats.forEach(kind => { const button = node("button", `${kind.toUpperCase()} 다운로드`); button.type = "button"; button.addEventListener("click", () => void download(message, kind)); actions.append(button); });
        if (message.mode === "research" && savePageAvailable) {
          const save = node("button", "페이지로 저장"); save.type = "button";
          save.addEventListener("click", () => { if (save.dataset.savedPageId) void host.openPage(save.dataset.savedPageId); else void saveReport(message, save); }); actions.append(save);
        }
        row.append(actions);
        if (message.citations?.length) {
          const webIds = new Set((message.sources || []).filter(source => source.kind === "web").map(source => source.source_id));
          const citedWeb = new Set(message.citations.filter(citation => webIds.has(citation.source_id)).map(citation => citation.source_id)).size;
          const citedSources = new Map();
          message.citations.forEach(citation => {
            if (!citedSources.has(citation.source_id)) citedSources.set(citation.source_id, []);
            citedSources.get(citation.source_id).push(citation);
          });
          const details = node("details", undefined, "ai-home-citations"); details.append(node("summary", `출처 ${citedSources.size}개${citedWeb ? ` · 웹 ${citedWeb}개 인용` : ""}`));
          citedSources.forEach(citations => {
            const citation = citations[0];
            const title = safeSourceLink(citation.title, citation.url) || node("strong", citation.title);
            details.append(title);
            const quotes = new Set();
            citations.forEach(item => {
              if (!quotes.has(item.quote)) { details.append(node("blockquote", item.quote)); quotes.add(item.quote); }
              if (item.location) details.append(node("small", item.location));
            });
          }); row.append(details);
        }
        const research = message.context_info?.research;
        if (research) {
          const details = node("details", undefined, "ai-home-research-progress");
          details.append(node("summary", `리서치 과정 · ${research.steps.length}개 작업 · ${message.sources?.length || 0}개 출처`));
          research.steps.forEach(step => details.append(node("div", step.kind === "database_query"
            ? `DB · ${step.title}: 조건 일치 ${step.matched_rows}행 중 ${step.provided_rows}행 참고`
            : step.kind === "source_review" ? `원문 범위 확인 · ${step.title} · ${step.within_scope ? "포함" : "제외"} · ${step.reason}`
            : `${step.kind === "web_search" ? "웹" : "내 자료"} · ${step.query} · 출처 ${step.source_count}개`)));
          research.limitations.forEach(text => details.append(node("p", text))); row.append(details);
          if (research.coverage) {
            const coverage = node("details", undefined, "ai-home-research-progress"); coverage.append(node("summary", research.partial ? "조사 범위 · 추가 확인 필요" : "조사 범위 점검"));
            research.coverage.forEach(item => coverage.append(node("p", `${item.section_id} · ${{sufficient:"근거 확보",partial:"일부 확인",missing:"근거 미확보"}[item.status]} · ${item.reason}`)));
            row.append(coverage);
          }
        }
        const web = (message.sources || []).filter(source => source.kind === "web");
        const webSearch = message.context_info?.web_search_details;
        if (webSearch && !research) {
          const details = node("details", undefined, "ai-home-research-progress");
          const state = {completed:"완료",no_results:"관련 자료 없음",failed:"연결 실패",not_needed:"이번 요청은 검색 불필요"}[webSearch.status] || "확인 중";
          details.append(node("summary", `웹 검색 · ${state}`));
          (webSearch.steps || []).forEach(step => details.append(node("div", `${step.query} · 검색 ${step.result_count ?? step.source_count}개 · 관련 자료 ${step.source_count}개`)));
          (message.context_info?.limitations || []).forEach(text => details.append(node("p", text)));
          row.append(details);
          if (webSearch.status === "failed" || webSearch.status === "no_results") row.append(node("small", "이번 답변은 웹 자료로 검증하지 못했습니다."));
        }
        if (web.length) {
          const details = node("details", undefined, "ai-home-citations"); details.append(node("summary", `검색한 웹 자료 ${web.length}개`));
          web.forEach(source => { const link = safeSourceLink(source.title, source.url); if (link) details.append(link, node("small", ({document_text:source.source_format === "pdf" ? "PDF 텍스트 확보" : "웹 문서 본문 확보",partial_text:"원문 일부 확보",abstract_text:"초록·안내 페이지 참고",page_excerpt:"본문 발췌 참고"}[source.content_status] || "검색 발췌문 참고") + (source.total_chunks ? ` · ${source.total_chunks}개 구간 중 ${source.analyzed_chunks}개 분석` : ""))); });
          row.append(details);
        }
        const workspace = message.context_info?.notion_workspace;
        if (workspace) {
          row.append(node("small", `내 Notion: 페이지 ${workspace.page_count}개 중 목록 ${workspace.listed_page_count}개 · 본문 ${workspace.body_page_count}개 참고`));
          if (workspace.body_coverage_partial) row.append(node("small", "전체 페이지 본문을 모두 읽은 답변은 아닙니다. 필요한 페이지를 지정해 질문하세요."));
        }
        if (message.context_info?.truncated) row.append(node("small", "참고 자료가 길어 일부 생략되었습니다."));
        const pages = (message.sources || []).filter(source => source.kind === "page" && /^\/page\/[\w-]+$/.test(source.url || ""));
        if (pages.length) {
          const details = node("details", undefined, "ai-home-citations"); details.append(node("summary", `참고 페이지 ${pages.length}개`));
          pages.forEach(source => { const link = node("a", source.title); link.href = source.url; details.append(link, node("br")); });
          row.append(details);
        }
      }
      return row;
    }));
    renderResearchJob();
    for (const detail of $("aiHomeMessages").querySelectorAll("details")) if (expanded.has(detail.querySelector("summary")?.textContent)) detail.open = true;
    scroll.scrollTop = follow ? scroll.scrollHeight : previousTop;
    controls();
  }
  function renderResearchJob() {
    const job = conversation?.research_job;
    if (!job || job.state === "completed") return;
    $("aiHomeHero").hidden = true; $("aiHomeView").dataset.empty = "false";
    const user = node("article", undefined, "ai-home-message"); user.dataset.role = "user";
    user.append(node("small", "나"), node("div", job.request, "ai-home-message-text"));
    const row = node("article", undefined, "ai-home-message ai-home-research-job"); row.dataset.role = "assistant";
    row.append(node("small", "심층 리서치 · " + ({queued:"조사 대기",processing:"조사 중",paused:"일시 중단",failed:"단계 재시도 필요",cancelled:"취소됨"}[job.state] || job.state)));
    row.append(node("p", job.progress.label || "조사 중입니다."));
    if (job.progress.title) row.append(node('small', job.progress.title));
    const seconds = Math.max(0, Math.floor((Date.now()/1000-job.created_at)/60));
    row.append(node("small", `${seconds}분 경과 · 검색 ${job.progress.search_count || 0}회 · 자료 ${job.progress.source_count || 0}개 · 근거 ${job.progress.evidence_count || 0}개`));
    if (job.error) row.append(node("p", job.error));
    const actions = node("div", undefined, "ai-home-result-actions");
    for (const [action,label] of (job.state === "queued" || job.state === "processing" ? [["pause","조사 중단"],["cancel","취소"]] : job.state === "paused" || job.state === "failed" ? [["resume","이어 조사"],["cancel","취소"]] : [])) {
      const button = node("button", label); button.type = "button"; button.addEventListener("click", () => void researchControl(action, button)); actions.append(button);
    }
    row.append(actions);
    if (job.chapters.length || job.draft) {
      const partial = node("details", undefined, "ai-home-full-report"); partial.append(node("summary", "중간 보고서 · 최종 검토 전"));
      job.chapters.forEach(chapter => partial.append(node("h3", chapter.title),
        node('small', ({reviewed:'근거 검토 통과 · 최종 조합 전',partial:'일부 검토 미완료',missing:'근거 미확인'}[chapter.verification] || '검토 전')),
        answerBody(chapter.text)));
      if (job.draft) partial.append(node("h3", job.draft.title + " · 작성 중 초안"), answerBody(job.draft.text));
      row.append(partial);
    }
    $("aiHomeMessages").append(user, row);
  }
  async function researchControl(action, button) {
    const token = serial, origin = host.key(); button.disabled = true;
    try {
      const data = await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(cid)}/research`, {method:"POST",body:{action,operation_id:conversation.research_job.operation_id}});
      if (token !== serial || origin !== host.key() || !visible) return;
      conversation = data; render(); schedulePoll(); status(action === "resume" ? "저장된 조사 단계부터 이어갑니다." : "조사 상태를 저장했습니다.");
    } catch(error) { if (token === serial && origin === host.key()) { button.disabled = false; if ([401,403,404].includes(error.status)) { conversation = null; pending = null; clearTimeout(timer); render(); } status(error.message, true); } }
  }
  function clearStream() {
    streamController?.abort(); streamController = null;
    if (streamPaint !== null) cancelAnimationFrame(streamPaint);
    streamPaint = null; streamDraft = null;
  }
  function startDraft(question) {
    const user = node("article", undefined, "ai-home-message"), assistant = node("article", undefined, "ai-home-message");
    user.dataset.role = "user"; assistant.dataset.role = "assistant";
    user.append(node("small", "나"), node("div", question, "ai-home-message-text"));
    const label = node("small", "AI · 생성 중…"), body = answerBody("");
    assistant.append(label, body); assistant.setAttribute("aria-busy", "true");
    $("aiHomeMessages").append(user, assistant);
    $("aiHomeHero").hidden = true; $("aiHomeView").dataset.empty = "false";
    streamDraft = {answer: "", body, label};
    $("aiHomeScroll").scrollTop = $("aiHomeScroll").scrollHeight;
  }
  function paintDraft() {
    streamPaint = null;
    if (!streamDraft) return;
    const scroll = $("aiHomeScroll"), follow = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
    const next = answerBody(streamDraft.answer);
    streamDraft.body.replaceWith(next); streamDraft.body = next;
    if (follow) scroll.scrollTop = scroll.scrollHeight;
  }
  function draftEvent(event) {
    if (!streamDraft) return;
    if (event.type === "research_progress") {
      status(event.label); streamDraft.label.textContent = "AI · " + event.label;
      const line = node("div", event.title || event.query || event.label);
      if (!streamDraft.progress) {
        streamDraft.progress = node("details", undefined, "ai-home-research-progress"); streamDraft.progress.open = true;
        streamDraft.progress.append(node("summary", "리서치 진행 중")); streamDraft.label.after(streamDraft.progress);
      }
      streamDraft.progress.append(line);
    }
    if (event.type === "model") streamDraft.label.textContent = `${event.model_name || "AI"} · 생성 중…`;
    if (event.type === "reset") {
      streamDraft.answer = ""; status("응답을 다시 생성하고 있습니다…"); paintDraft();
    }
    if (event.type === "delta") {
      streamDraft.answer += event.text;
      if (streamPaint === null) streamPaint = requestAnimationFrame(paintDraft);
    }
  }
  function schedulePoll(failures = 0) {
    clearTimeout(timer);
    if (!conversation?.pending || !visible || !cid) return;
    const key = snapshotKey(), token = serial;
    timer = setTimeout(async () => {
      try {
        const data = await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(cid)}`);
        if (token !== serial || key !== snapshotKey() || !visible) return;
        conversation = data; render();
        if (!data.pending) { pending = null; status(["paused","failed"].includes(data.research_job?.state) ? "조사 단계를 저장했습니다. 이어 조사할 수 있습니다." : data.interrupted ? "이전 요청이 중단되었습니다. 다시 보내세요." : "응답을 불러왔습니다."); await history(); }
        schedulePoll();
      } catch (error) {
        if (token !== serial || !visible) return;
        status(error.message, true);
        if ([401, 403, 404].includes(error.status)) {
          conversation = null; cid = ""; pending = null; host.route("", false); render();
        } else if (failures < 3) schedulePoll(failures + 1);
        else status("응답 상태를 확인하지 못했습니다. 대화 기록에서 이 대화를 다시 여세요.", true);
      }
    }, 3000 * (2 ** failures));
  }
  function savedMode(data) {
    for (let index = (data.messages?.length || 0) - 1; index >= 0; index--) {
      const message = data.messages[index];
      if (message.role === "assistant") return Object.prototype.hasOwnProperty.call(modeHelp, message.mode) ? message.mode : "chat";
    }
    return data.research_job ? "research" : "chat";
  }
  async function open(id = "", push = true) {
    if (!(await host.enter())) return;
    clearStream();
    visible = true; const token = ++serial; clearTimeout(timer);
    cid = id; conversation = null; busy = true; pending = null; createOperation = ""; mode = "chat"; notionConnected = false; streamingEnabled = false;
    scope = {page_ids: [], knowledge_space_id: ""}; $("aiHomeInput").value = ""; $("aiHomePageFilter").value = "";
    sourceOptions = {notion:true, web:false}; webAvailable = false; savePageAvailable = false;
    if ($("aiHomeReferencePanel")) $("aiHomeReferencePanel").hidden = true;
    $("aiHomeReferenceOptions")?.setAttribute("aria-expanded", "false");
    $("aiHomeContextPicker").open = false;
    host.route(cid, push); status(id ? "대화 불러오는 중…" : ""); render(); pageChoices();
    if (id) {
      try {
        const data = await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(id)}`);
        if (token !== serial) return;
        if (data.scope.workspace_id !== host.workspaceId()) throw new Error("이 대화의 워크스페이스로 전환한 뒤 다시 여세요.");
        conversation = data; scope = data.scope; mode = savedMode(data); status(data.pending ? "호스트에서 이전 요청을 처리 중입니다." : data.archived ? "보관된 대화입니다. 보관함에서 복원한 뒤 이어갈 수 있습니다." : data.interrupted ? "이전 요청이 중단되었거나 실패했습니다. 다시 보내세요." : "");
        const last = [...(data.messages || [])].reverse().find(m => m.role === "assistant");
        if (last?.context_info?.source_options) sourceOptions = {...last.context_info.source_options};
        if (data.research_job && data.research_job.state !== "completed") { mode = "research"; sourceOptions = {...data.research_job.source_options}; }
      } catch (error) { if (token === serial) { cid = ""; host.route("", false); status(error.message, true); } }
    }
    if (token !== serial) return;
    await capabilities(token); if (token !== serial) return; busy = false;
    pageChoices(); render(); void history(); schedulePoll(); if (!conversation?.pending) $("aiHomeInput").focus();
  }
  async function send() {
    if (busy || conversation?.pending || conversation?.archived) return;
    const message = $("aiHomeInput").value.trim(); if (!message) return;
    if (!models.length) return status("먼저 모델을 연결하세요.", true);
    const token = ++serial, origin = host.key(); busy = true; controls(); status("AI가 응답을 생성하고 있습니다…");
    try {
      if (!cid) {
        createOperation ||= uuid();
        const created = await host.api("/api/notion/ai-home/conversations", {method: "POST", body: {...scope, workspace_id: host.workspaceId(), operation_id: createOperation}});
        if (token !== serial || origin !== host.key() || !visible) return;
        cid = created.id; conversation = created; host.route(cid, false);
      }
      const fields = {message, mode, model_id: $("aiHomeModel").value, expected_revision: conversation.revision, source_options:{...sourceOptions}};
      if (!pending || JSON.stringify(pending.fields) !== JSON.stringify(fields)) pending = {fields, operation_id: uuid()};
      const path = `/api/notion/ai-home/conversations/${encodeURIComponent(cid)}/messages`;
      const body = {...pending.fields, operation_id: pending.operation_id};
      let data;
      if (mode === "research") {
        data = await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(cid)}/research`, {method:"POST",body});
      } else if (streamingEnabled && host.fetch && window.NotionAiStream) {
        streamController = new AbortController(); startDraft(message);
        data = await window.NotionAiStream.send({fetch: host.fetch, path: path + "/stream", body,
          signal: streamController.signal, onEvent: event => {
            if (token === serial && origin === host.key() && visible) draftEvent(event);
          }});
      } else data = await host.api(path, {method: "POST", body});
      if (token !== serial || origin !== host.key() || !visible) return;
      clearStream();
      conversation = data; pending = null; $("aiHomeInput").value = ""; render();
      status(data.pending ? "서버에서 심층 리서치를 진행합니다. 화면을 닫아도 계속됩니다." : "응답을 저장했습니다."); schedulePoll();
      $("aiHomeScroll").scrollTop = $("aiHomeScroll").scrollHeight; void history();
    } catch (error) {
      if (token === serial && origin === host.key() && visible) {
        clearStream(); render();
        if (error.status && error.status !== 409) pending = null;
        status(error.message, true);
        if (error.status === 409 && cid) {
          const data = await host.api(`/api/notion/ai-home/conversations/${encodeURIComponent(cid)}`).catch(() => null);
          if (data && token === serial) { conversation = data; render(); schedulePoll(); if (!data.pending) pending = null; }
        }
      }
    } finally { if (token === serial) { busy = false; controls(); } }
  }
  function closeContextPicker(restoreFocus = false) {
    const picker = $("aiHomeContextPicker");
    if (!picker?.open) return;
    picker.open = false;
    if (restoreFocus) picker.querySelector("summary")?.focus({preventScroll: true});
  }
  function leave() { closeContextPicker(); visible = false; serial += 1; clearStream(); clearTimeout(timer); if (host) host.leave(); }
  window.NotionAiHome = {
    init(adapter) {
      host = adapter;
      const picker = $("aiHomeContextPicker");
      const placeContext = () => { if (picker.open) window.NotionUiInteraction.place(picker.querySelector(".ai-home-context-panel"), picker.querySelector("summary"), "below"); };
      picker.addEventListener("toggle", () => {
        if (picker.open) { placeContext(); $("aiHomeReferenceOptions")?.focus({preventScroll: true}); }
      });
      $("aiHomeReferenceOptions")?.addEventListener("click", () => {
        const panel = $("aiHomeReferencePanel"); panel.hidden = !panel.hidden;
        $("aiHomeReferenceOptions").setAttribute("aria-expanded", String(!panel.hidden));
        placeContext(); if (!panel.hidden) $("aiHomePageFilter").focus({preventScroll:true});
      });
      for (const [id, key] of [["aiHomeWebSwitch", "web"], ["aiHomeNotionSwitch", "notion"]]) {
        $(id)?.addEventListener("click", () => {
          if (busy || conversation?.pending) return;
          sourceOptions[key] = !sourceOptions[key]; controls();
        });
        $(id)?.closest(".ai-source-row")?.addEventListener("click", event => {
          if (!event.target.closest("button") && !$(id).disabled) $(id).click();
        });
      }
      picker.addEventListener("keydown", event => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        const items = [$("aiHomeReferenceOptions"), $("aiHomeWebSwitch"), $("aiHomeNotionSwitch")].filter(item => item && !item.disabled);
        const index = items.indexOf(event.target);
        if (index < 0) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length-1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[next]?.focus({preventScroll:true});
      });
      document.addEventListener("keydown", event => {
        if (event.key === "Escape" && picker.open) { event.preventDefault(); event.stopImmediatePropagation(); closeContextPicker(true); }
      }, true);
      document.addEventListener("click", event => { if (picker.open && !picker.contains(event.target)) closeContextPicker(); });
      window.addEventListener("resize", placeContext);
      document.addEventListener("scroll", placeContext, true);
      $("aiHomeComposer").addEventListener("submit", event => { event.preventDefault(); void send(); });
      $("aiHomeInput").addEventListener("keydown", event => { if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); void send(); } });
      $("aiHomeNewInline").addEventListener("click", () => void open(""));
      $("aiHomePageFilter").addEventListener("input", pageChoices);
      $("aiHomeWiki").addEventListener("change", event => changeScope({...scope, knowledge_space_id: event.target.value}));
      $("aiHomeWikiMore").addEventListener("click", async () => {
        const token = serial;
        try { const data = await host.api("/api/notion/ai-home/wikis?cursor=" + encodeURIComponent(wikiCursor)); if (token === serial) { addWikis(data); controls(); } }
        catch (error) { status(error.message, true); }
      });
      $("aiHomeArchived").addEventListener("change", () => void history());
      $("aiHomeHistorySearchBtn").addEventListener("click", () => setHistorySearch(true));
      $("aiHomeHistorySearchClose").addEventListener("click", () => setHistorySearch(false));
      $("aiHomeHistoryNewBtn").addEventListener("click", () => void open(""));
      $("aiHomeHistorySearch").addEventListener("input", searchHistory);
      $("aiHomeHistorySearch").addEventListener("compositionstart", () => { historyComposing = true; invalidateHistory(); });
      $("aiHomeHistorySearch").addEventListener("compositionend", () => { historyComposing = false; searchHistory(); });
      $("aiHomeHistorySearch").addEventListener("keydown", event => {
        if (event.isComposing || event.keyCode === 229) return;
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setHistorySearch(false); }
        else if (event.key === "Enter") { event.preventDefault(); void history(); }
      });
      $("aiHomeHistoryMore").addEventListener("click", () => void history(true));
      document.querySelectorAll("[data-ai-home-mode]").forEach(button => button.addEventListener("click", () => {
        mode = button.dataset.aiHomeMode; if (!$("aiHomeInput").value.trim()) $("aiHomeInput").value = prompts[mode] || ""; controls(); $("aiHomeInput").focus();
      }));
    }, open, leave, showHistory, hideHistory,
  };
})();
