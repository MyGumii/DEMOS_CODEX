/* code_assist_v1/static/app.js — 전역 상태·토스트·모달·탭 */

function readDefaultOnToggle(key) {
  const value = localStorage.getItem(key);
  return value === null ? true : value !== "0";
}

const State = {
  session_id: null,              // 자동 세션 ID (하네스 저장 시 채워짐)
  model: "",
  effort: 2,
  activeSkills: new Set(),       // skill_id
  autoSkills: readDefaultOnToggle("code_assist_v1.autoSkills"),
  lastAutoSkillMatches: [],
  enableKnowledge: readDefaultOnToggle("code_assist_v1.enableKnowledge"), // 📚 토글
  enableProjectContext: true,
  projectId: localStorage.getItem("code_assist_v1.projectId") || "default",
  enableMemory: localStorage.getItem("code_assist_v1.enableMemory") !== "0",
  workspaceFiles: [],            // [{filename, content}] 첨부
  pinnedThoughts: [],            // [{id, title}] Second Brain nodes pinned into chat context
  pastedImages: [],              // [{name, mime, dataUrl}] — 다음 전송 시 user 메시지에 multimodal 로 첨부
  messages: [],                  // [{role, content}]
  compactSummary: "",
  compactedMessageCount: 0,
  lastContextStatus: null,
  sessionCache: new Map(),       // session_id -> messages snapshot
  sessionCompactCache: new Map(),
  activeStream: null,            // {sessionId}
  streaming: false,
  abortController: null,         // 스트리밍 중단용
  systemPromptExtra: localStorage.getItem("code_assist_v1.systemPromptExtra") || "",
  graphMode: false,
  currentUser: null,
  csrfToken: "",
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ── 토스트 ──
function toast(msg, kind = "") {
  const wrap = $("#toastWrap");
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

// ── 모달 ──
const Modal = {
  open({ title, body, footButtons = [], closeable = true }) {
    $("#modalTitle").textContent = title || "";
    $("#modalBody").innerHTML = "";
    if (typeof body === "string") $("#modalBody").innerHTML = body;
    else if (body instanceof Node) $("#modalBody").appendChild(body);
    const closeBtn = $("#modalClose");
    if (closeBtn) closeBtn.hidden = !closeable;

    const foot = $("#modalFoot");
    foot.innerHTML = "";
    footButtons.forEach(b => {
      const btn = document.createElement("button");
      btn.textContent = b.label;
      if (b.primary) btn.className = "primary";
      if (b.danger) btn.className = "danger";
      btn.onclick = async () => {
        let result = b.onClick?.();
        if (result && typeof result.then === "function") {
          btn.disabled = true;
          try {
            result = await result;
          } finally {
            btn.disabled = false;
          }
        }
        if (result !== false) Modal.close(true);
      };
      foot.appendChild(btn);
    });
    const bg = $("#modalBg");
    bg.dataset.open = "1";
    if (closeable) delete bg.dataset.locked;
    else bg.dataset.locked = "1";
    bg.classList.add("show");
  },
  close(force = false) {
    const bg = $("#modalBg");
    if (!force && bg.dataset.locked === "1") return;
    bg.classList.remove("show");
    delete bg.dataset.open;
    delete bg.dataset.locked;
    const closeBtn = $("#modalClose");
    if (closeBtn) closeBtn.hidden = false;
  },
};
$("#modalBg").addEventListener("click", e => { if (e.target.id === "modalBg") Modal.close(); });
$("#modalClose").addEventListener("click", () => Modal.close());

function resetModalOverlay() {
  const bg = $("#modalBg");
  if (!bg) return;
  bg.classList.remove("show");
  delete bg.dataset.open;
  delete bg.dataset.locked;
  const closeBtn = $("#modalClose");
  if (closeBtn) closeBtn.hidden = false;
}

// ── API 헬퍼 ──
function setNotionWorkspaceLink() {
  const link = $("#notionWorkspaceLink");
  if (!link) return;
  const host = window.location.hostname || "localhost";
  const notionPort = location.port === "10521" ? "10322" : location.port === "10111" ? "10112" : "10012";
  link.href = `http://${host}:${notionPort}`;
}

function notionWorkspacePageUrl(pageId) {
  const host = window.location.hostname || "localhost";
  const notionPort = location.port === "10521" ? "10322" : location.port === "10111" ? "10112" : "10012";
  return `http://${host}:${notionPort}/page/${encodeURIComponent(pageId || "")}`;
}

function chatContentText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content.map(part => {
      if (typeof part === "string") return part;
      if (part && typeof part === "object") return part.text || part.content || "";
      return "";
    }).join(" ");
  }
  if (content && typeof content === "object") return content.text || content.content || JSON.stringify(content);
  return String(content ?? "");
}

function defaultNotionChatTitle(messages) {
  const firstUser = (messages || []).find(m => m.role === "user");
  const text = chatContentText(firstUser?.content || "").replace(/\s+/g, " ").trim();
  if (text) return text.slice(0, 60);
  const now = new Date();
  const stamp = now.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  return `Chat ${stamp}`;
}

function notionParentOptions(pages) {
  const own = (pages || []).filter(page => page.scope === "own" && page.can_edit);
  if (!own.length) return "";
  const byParent = new Map();
  own.forEach(page => {
    const parent = page.parent_id || "";
    if (!byParent.has(parent)) byParent.set(parent, []);
    byParent.get(parent).push(page);
  });
  const rendered = new Set();
  const rows = [];
  const draw = (parentId, depth) => {
    const items = byParent.get(parentId) || [];
    items.sort((a, b) => (a.position || 0) - (b.position || 0) || String(a.title || "").localeCompare(String(b.title || "")));
    items.forEach(page => {
      rendered.add(page.id);
      const prefix = depth > 0 ? `${"· ".repeat(depth)}` : "";
      rows.push(`<option value="${accountEscapeHtml(page.id)}">${accountEscapeHtml(prefix + (page.title || "제목 없음"))}</option>`);
      draw(page.id, depth + 1);
    });
  };
  draw("", 0);
  own.filter(page => !rendered.has(page.id)).forEach(page => {
    rows.push(`<option value="${accountEscapeHtml(page.id)}">${accountEscapeHtml(page.title || "제목 없음")}</option>`);
  });
  return rows.join("");
}

async function openChatToNotionDialog() {
  const messages = Chat.cloneMessages(State.messages || []);
  if (!messages.length) {
    toast("Notion으로 보낼 현재 대화가 없습니다.", "error");
    return;
  }
  let pages = [];
  try {
    const data = await api("api/code/notion/pages");
    pages = data.items || [];
  } catch (e) {
    console.warn("[notion] page list failed", e);
  }
  const body = document.createElement("div");
  body.innerHTML = `
    <label>Notion 페이지 제목</label>
    <input id="notionChatTitle" type="text">
    <label>저장 방식</label>
    <select id="notionChatMode">
      <option value="summary" selected>핵심 요약 저장 (local GGUF → hynix API)</option>
      <option value="decisions">결정사항/할 일 중심 저장 (local GGUF → hynix API)</option>
      <option value="full">전체 대화 원문 저장 (LLM 없음)</option>
    </select>
    <label>저장 위치</label>
    <select id="notionChatParent">
      <option value="">최상위 페이지</option>
      ${notionParentOptions(pages)}
    </select>
    <p class="account-help">요약 저장은 서버에서 local GGUF를 먼저 사용하고, 실패하면 hynix API로 fallback합니다. 전체 저장은 LLM을 호출하지 않고 현재 세션 메시지 ${messages.length}개를 그대로 Markdown으로 저장합니다.</p>
  `;
  Modal.open({
    title: "현재 대화를 Notion으로 보내기",
    body,
    footButtons: [
      { label: "취소" },
      {
        label: "Notion에 저장",
        primary: true,
        onClick: async () => {
          const title = $("#notionChatTitle")?.value.trim() || defaultNotionChatTitle(messages);
          const mode = $("#notionChatMode")?.value || "summary";
          try {
            toast(mode === "full" ? "Notion에 전체 대화 저장 중..." : "대화를 요약해 Notion에 저장 중...");
            const data = await api("api/code/notion/save_chat_summary", {
              method: "POST",
              body: {
                title,
                mode,
                parent_id: $("#notionChatParent")?.value || "",
                session_id: State.session_id || "",
                messages,
              },
            });
            const pageId = data.page?.id || data.id || "";
            if (pageId) window.open(notionWorkspacePageUrl(pageId), "_blank", "noopener");
            const modelNote = data.model_used ? ` (${data.model_used})` : "";
            toast(`현재 대화를 Notion 페이지로 저장했습니다.${modelNote}`, "ok");
          } catch (e) {
            toast("Notion 저장 실패: " + e.message, "error");
            return false;
          }
        },
      },
    ],
  });
  $("#notionChatTitle").value = defaultNotionChatTitle(messages);
  $("#notionChatTitle").focus();
  $("#notionChatTitle").select();
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
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const init = { method: opts.method || "GET", headers };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const url = String(path || "").startsWith("/") ? path : "/" + path;
  const r = await authenticatedFetch(url, init);
  if (!r.ok) {
    let err = `HTTP ${r.status}`;
    try { const j = await r.json(); err = j.error || err; } catch {}
    throw new Error(err);
  }
  return r.json();
}

function accountEscapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function accountFormatTime(ts) {
  if (!ts) return "-";
  try {
    return new Date(Number(ts) * 1000).toLocaleString("ko-KR");
  } catch {
    return "-";
  }
}

function accountFormatBytes(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024 * 1024) return (n / (1024 * 1024 * 1024)).toFixed(1) + "GB";
  if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + "MB";
  if (n >= 1024) return (n / 1024).toFixed(1) + "KB";
  return n + "B";
}

// ── 모델 목록 로드 ──
async function loadModels() {
  try {
    const data = await api("api/code/models");
    const sel = $("#modelSelect");
    sel.innerHTML = "";

    if (data.api?.length) {
      const og = document.createElement("optgroup");
      og.label = "API 모델";
      data.api.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = `${m.name || m.id}`;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    }
    if (data.gguf?.length) {
      const og = document.createElement("optgroup");
      og.label = "GGUF (로컬)";
      data.gguf.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.id;
        const sizeLabel = m.size_gb ? ` (${m.size_gb}GB)` : (m.missing ? " (파일 없음)" : "");
        opt.textContent = `${m.name || m.id}${sizeLabel}`;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    }
    if (data.codex?.length) {
      const og = document.createElement("optgroup");
      og.label = "Codex";
      data.codex.forEach(m => {
        const opt = document.createElement("option");
        opt.value = m.id;
        opt.textContent = `${m.name || m.id}`;
        og.appendChild(opt);
      });
      sel.appendChild(og);
    }

    const def = data.default;
    if (def && [...sel.options].some(o => o.value === def)) {
      sel.value = def;
    }
    State.model = sel.value;
    refreshMetaBar();
  } catch (e) {
    toast("모델 목록 로드 실패: " + e.message, "error");
  }
}

// ── 메타바 ──
function refreshMetaBar() {
  $("#metaModel").textContent = "모델: " + (State.model || "-");
  $("#metaSkills").textContent = "스킬: " + State.activeSkills.size + (State.autoSkills ? " + Auto" : "");
  $("#metaKB").textContent = "지식: " + (State.enableKnowledge ? "ON" : "OFF")
    + (State.enableMemory ? " · 기억" : "")
    + (State.pinnedThoughts.length ? ` + 고정 ${State.pinnedThoughts.length}` : "");
  $("#metaWS").textContent = "파일: " + State.workspaceFiles.length
    + (State.pastedImages.length ? `+🖼${State.pastedImages.length}` : "");
}

// ── 사이드바 탭 ──
let currentTab = "skills";
function setTab(name) {
  currentTab = name;
  if (window.KnowledgeCoreBrowser?.active) {
    window.KnowledgeCoreBrowser.setWikiNavigationVisible(name === "knowledge");
  }
  if (name !== "knowledge" && window.KnowledgeCoreBrowser?.active) {
    window.KnowledgeCoreBrowser.enterChat().catch(error => {
      if (typeof toast === "function") toast(error.message, "error");
    });
  }
  $$(".sidebar .tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  if (name === "skills") Skills.render();
  else if (name === "knowledge") Knowledge.render();
  else if (name === "sessions") Sessions.render();
}
$$(".sidebar .tabs button").forEach(b => b.addEventListener("click", () => setTab(b.dataset.tab)));

// ── 사이드바 검색·추가 ──
$("#sidebarSearch").addEventListener("input", e => {
  if (currentTab === "skills") Skills.render(e.target.value);
  else if (currentTab === "knowledge") Knowledge.render(e.target.value);
});
$("#sidebarAddBtn").addEventListener("click", () => {
  if (currentTab === "skills") Skills.openCreator?.();
  else if (currentTab === "knowledge") {
    if (Knowledge.openAddMenu) Knowledge.openAddMenu();
    else Knowledge.openCreate();
  }
  else if (currentTab === "sessions") {
    startNewChatSession();
  }
});

// ── 모델 선택·effort ──
$("#modelSelect").addEventListener("change", e => {
  State.model = e.target.value;
  refreshMetaBar();
});
$("#effortSelect").addEventListener("change", e => {
  State.effort = parseInt(e.target.value, 10);
});
$("#chatToNotionBtn")?.addEventListener("click", openChatToNotionDialog);

async function startNewChatSession() {
  const previousProjectId = window.Chat?.workspaceProjectId?.(State.session_id);
  State.messages = [];
  State.session_id = null;
  State.compactSummary = "";
  State.compactedMessageCount = 0;
  State.lastContextStatus = null;
  State.workspaceFiles = [];
  State.pastedImages = [];
  State.pinnedThoughts = [];
  if (!State.streaming) State.activeStream = null;
  Chat.clear();
  Chat.refreshChips();
  refreshMetaBar();
  toast("새 세션 시작");
  if (previousProjectId) {
    try {
      await api("api/code/project_context/clear", {
        method: "POST",
        body: { project_id: previousProjectId },
      });
    } catch (e) {
      console.warn("[session] workspace index clear failed", e);
    }
  }
  if (window.Workspace?.clearAll) {
    Workspace.clearAll({ skipConfirm: true, quiet: true, clearIndex: false });
  }
  if (window.KnowledgeCoreBrowser?.active) {
    try {
      await window.KnowledgeCoreBrowser.startFreshConversation();
    } catch (error) {
      toast("새 canonical 세션 생성 실패: " + error.message, "error");
      return;
    }
  }
}

function setMainView(name) {
  const graphOn = name === "graph";
  State.graphMode = graphOn;
  const root = document.querySelector(".chat");
  root?.classList.toggle("graph-mode", graphOn);
  const graphView = $("#graphView");
  if (graphView) graphView.hidden = !graphOn;
  const btn = $("#graphToggleBtn");
  if (btn) btn.classList.toggle("active", graphOn);
  if (graphOn && window.SecondBrainGraph) {
    window.SecondBrainGraph.render();
  }
}
window.setMainView = setMainView;

$("#graphToggleBtn")?.addEventListener("click", async () => {
  if (window.KnowledgeCoreBrowser) await window.KnowledgeCoreBrowser.ready();
  if (window.KnowledgeCoreBrowser?.active) {
    if (
      document.documentElement.dataset.keepLegacyGraph === "true"
      && await window.KnowledgeCoreBrowser.openGraph()
    ) return;
    setTab("knowledge");
    return;
  }
  setMainView(State.graphMode ? "chat" : "graph");
});

// ── 시스템 프롬프트 모달 ──
async function openSystemPromptModal() {
  let info;
  try {
    info = await api("api/code/system_prompt");
  } catch (e) {
    toast("시스템 프롬프트 로드 실패: " + e.message, "error");
    return;
  }
  const body = document.createElement("div");
  body.innerHTML = `
    <label>기본 시스템 프롬프트 <span style="color:var(--muted);font-weight:400;">(읽기 전용)</span></label>
    <textarea id="spBase" readonly style="background:var(--code-bg);min-height:160px;font-size:12px;"></textarea>
    <label>반-환각 가드 <span style="color:var(--muted);font-weight:400;">(읽기 전용)</span></label>
    <textarea id="spAnti" readonly style="background:var(--code-bg);min-height:80px;font-size:12px;"></textarea>
    <label>사용자 추가 지시 <span style="color:var(--accent);font-weight:400;">(편집 가능, 자동 저장)</span></label>
    <textarea id="spExtra" placeholder="예: 답변은 항상 한국어로. 코드는 Python 3.11 문법으로." style="min-height:120px;"></textarea>
    <p style="color:var(--muted);font-size:11px;margin-top:8px;line-height:1.6;">
      💡 활성 스킬 본문 + (Second Brain ON 시) 승인된 지식/spec/source context + 워크스페이스 첨부 파일이 위 프롬프트에 더해져 LLM 에 전달됩니다.<br>
      💡 사용자 추가 지시는 브라우저에 저장돼 다음 세션에도 유지됩니다.
    </p>
  `;
  Modal.open({
    title: "시스템 프롬프트",
    body,
    footButtons: [
      { label: "초기화", danger: true, onClick: () => {
        const extra = $("#spExtra");
        if (extra) extra.value = "";
        State.systemPromptExtra = "";
        localStorage.removeItem("code_assist_v1.systemPromptExtra");
        toast("사용자 추가 지시 초기화됨", "ok");
        return false;  // 모달 유지 (사용자가 다시 입력하거나 닫기로 나감)
      }},
      { label: "저장", primary: true, onClick: () => {
        const extra = $("#spExtra");
        const val = extra ? extra.value : "";
        State.systemPromptExtra = val;
        localStorage.setItem("code_assist_v1.systemPromptExtra", val);
        toast(`저장됨 (${val.length}자)`, "ok");
        return false;  // 모달 유지
      }},
      { label: "닫기" },
    ],
  });
  $("#spBase").value = info.base || "";
  $("#spAnti").value = info.anti_hallucination || "";
  const extra = $("#spExtra");
  extra.value = State.systemPromptExtra;
  // 자동 저장도 유지 (편의)
  extra.addEventListener("input", () => {
    State.systemPromptExtra = extra.value;
    localStorage.setItem("code_assist_v1.systemPromptExtra", extra.value);
  });
}
$("#systemPromptBtn").addEventListener("click", openSystemPromptModal);

async function loadAuthUser() {
  try {
    const info = await api("api/code/auth/me");
    if (!info.authenticated) {
      window.location.href = "/login";
      return false;
    }
    State.currentUser = info.user || null;
    State.csrfToken = String(info.csrf_token || "");
    if (State.currentUser) {
      sessionStorage.setItem("demos_user", JSON.stringify(State.currentUser));
    }
    ensureAuthControls();
    return true;
  } catch {
    window.location.href = "/login";
    return false;
  }
}

function openOwnPasswordChangeModal({ required = false } = {}) {
  const body = document.createElement("div");
  body.innerHTML = `
    <p class="account-help">
      ${required
        ? "초기 비밀번호로 로그인했습니다. 계속 사용하려면 개인 비밀번호로 변경해야 합니다."
        : "현재 로그인한 계정의 비밀번호를 변경합니다."}
    </p>
    <label>현재 비밀번호</label>
    <input id="ownCurrentPassword" type="password" autocomplete="current-password" placeholder="${required ? "관리자가 안내한 임시 비밀번호" : "현재 비밀번호"}">
    <label>새 비밀번호</label>
    <input id="ownNewPassword" type="password" autocomplete="new-password" placeholder="8자 이상">
    <label>새 비밀번호 확인</label>
    <input id="ownNewPasswordConfirm" type="password" autocomplete="new-password" placeholder="한 번 더 입력">
    <p class="account-help">새 비밀번호는 8자 이상이어야 하며 현재 비밀번호와 달라야 합니다.</p>
  `;
  Modal.open({
    title: required ? "비밀번호 변경 필요" : "내 비밀번호 변경",
    body,
    closeable: !required,
    footButtons: [
      {
        label: "변경",
        primary: true,
        onClick: async () => {
          const current_password = $("#ownCurrentPassword").value;
          const new_password = $("#ownNewPassword").value;
          const confirm_password = $("#ownNewPasswordConfirm").value;
          if (!current_password || !new_password || !confirm_password) {
            toast("현재 비밀번호와 새 비밀번호를 모두 입력하세요.", "error");
            return false;
          }
          if (new_password.length < 8) {
            toast("새 비밀번호는 8자 이상이어야 합니다.", "error");
            return false;
          }
          if (new_password !== confirm_password) {
            toast("새 비밀번호 확인이 일치하지 않습니다.", "error");
            return false;
          }
          try {
            const data = await api("api/code/auth/password", {
              method: "POST",
              body: { current_password, new_password },
            });
            State.currentUser = data.user || State.currentUser;
            State.csrfToken = String(data.csrf_token || State.csrfToken || "");
            if (State.currentUser) {
              sessionStorage.setItem("demos_user", JSON.stringify(State.currentUser));
            }
            toast("비밀번호를 변경했습니다.", "ok");
            if (required) {
              location.reload();
              return false;
            }
          } catch (e) {
            toast("비밀번호 변경 실패: " + e.message, "error");
            return false;
          }
        },
      },
      ...(required ? [] : [{ label: "취소" }]),
    ],
  });
  setTimeout(() => $("#ownCurrentPassword")?.focus(), 0);
}

function ensureAuthControls() {
  let pill = $("#currentUserPill");
  if (!pill) {
    pill = document.createElement("span");
    pill.id = "currentUserPill";
    pill.className = "user-pill";
    $("#systemPromptBtn")?.insertAdjacentElement("afterend", pill);
  }
  pill.textContent = State.currentUser?.display_name || State.currentUser?.id || "-";

  let passwordBtn = $("#changeOwnPasswordBtn");
  if (!passwordBtn) {
    passwordBtn = document.createElement("button");
    passwordBtn.id = "changeOwnPasswordBtn";
    passwordBtn.className = "icon";
    passwordBtn.type = "button";
    passwordBtn.title = "내 비밀번호 변경";
    passwordBtn.textContent = "비밀번호";
    pill.insertAdjacentElement("afterend", passwordBtn);
  }
  passwordBtn.onclick = () => openOwnPasswordChangeModal();

  let manageBtn = $("#manageUsersBtn");
  if (State.currentUser?.is_admin) {
    if (!manageBtn) {
      manageBtn = document.createElement("button");
      manageBtn.id = "manageUsersBtn";
      manageBtn.className = "icon";
      manageBtn.type = "button";
      manageBtn.title = "계정 관리";
      manageBtn.textContent = "계정";
      passwordBtn.insertAdjacentElement("afterend", manageBtn);
    }
    manageBtn.onclick = () => openUserManagementModal();
  } else if (manageBtn) {
    manageBtn.remove();
  }

  let btn = $("#logoutBtn");
  if (!btn) {
    btn = document.createElement("button");
    btn.id = "logoutBtn";
    btn.className = "icon";
    btn.type = "button";
    btn.title = "Logout";
    btn.textContent = "Logout";
    (manageBtn || passwordBtn || pill).insertAdjacentElement("afterend", btn);
  }
  btn.onclick = async () => {
    try {
      await authenticatedFetch("api/code/auth/logout", { method: "POST" });
    } catch {}
    sessionStorage.removeItem("demos_user");
    window.location.href = "/login";
  };
}

async function openUserManagementModal() {
  if (!window.AdminOrganization) {
    toast("Canonical 관리자 UI 모듈을 불러오지 못했습니다.", "error");
    return;
  }
  try {
    await window.AdminOrganization.open({
      fetcher: authenticatedFetch,
      legacyApi: api,
      modal: Modal,
      toast,
    });
  } catch (e) {
    toast("조직·계정 관리 화면 초기화 실패: " + (e?.message || e), "error");
  }
}

function renderAccountList(root, items, reload) {
  if (!root) return;
  if (!items.length) {
    root.innerHTML = '<div class="account-empty">계정이 없습니다.</div>';
    return;
  }
  root.innerHTML = items.map(user => `
    <div class="account-row ${user.disabled ? "disabled" : ""}" data-user-id="${accountEscapeHtml(user.id)}">
      <span class="account-main">
        <strong>${accountEscapeHtml(user.display_name || user.id)}</strong>
        <span>${accountEscapeHtml(user.id)} · 생성 ${accountFormatTime(user.created_at)}</span>
      </span>
      <span class="account-role ${user.role === "admin" ? "admin" : ""}">${accountEscapeHtml(user.role || "user")}</span>
      ${user.disabled
        ? '<span class="account-status disabled">비활성</span>'
        : (user.must_change_password ? '<span class="account-status pending">비번 변경 필요</span>' : '<span class="account-status active">활성</span>')}
      <div class="account-summary">${accountSummaryLabel(user.data_summary || {})}</div>
      <div class="account-edit">
        <input class="account-display-input" value="${accountEscapeHtml(user.display_name || user.id)}" aria-label="레거시 표시 이름">
      </div>
      <div class="account-row-actions">
        <button type="button" data-action="save">레거시 표시 이름 저장</button>
        <button type="button" data-action="password">비밀번호</button>
        <button type="button" data-action="data">데이터</button>
        <button type="button" data-action="transfer">이관</button>
      </div>
    </div>
  `).join("");
  root.querySelectorAll(".account-row").forEach(row => {
    const userId = row.dataset.userId;
    const user = items.find(x => x.id === userId);
    row.querySelector('[data-action="save"]').onclick = async () => {
      try {
        await api(`api/code/auth/users/${encodeURIComponent(userId)}`, {
          method: "PATCH",
          body: {
            display_name: row.querySelector(".account-display-input").value.trim(),
          },
        });
        toast(`계정 저장 완료: ${userId}`, "ok");
        await reload?.();
      } catch (e) {
        toast("계정 저장 실패: " + e.message, "error");
      }
    };
    row.querySelector('[data-action="password"]').onclick = () => openPasswordResetModal(userId, reload);
    row.querySelector('[data-action="data"]').onclick = () => openAccountDataModal(userId);
    row.querySelector('[data-action="transfer"]').onclick = () => openTransferUserDataModal(userId, items, reload);
  });
}

function accountSummaryLabel(summary) {
  const parts = [
    `세션 ${Number(summary.sessions || 0)}`,
    `스킬 ${Number(summary.user_skills || 0)}`,
    `노드 ${Number(summary.thoughts || 0)}`,
    `파일 ${Number(summary.workspace_files || 0)}`,
  ];
  return accountEscapeHtml(parts.join(" · "));
}

function openPasswordResetModal(userId, reload) {
  const body = document.createElement("div");
  body.innerHTML = `
    <p class="account-help"><strong>${accountEscapeHtml(userId)}</strong> 계정의 새 비밀번호를 설정합니다.</p>
    <label>새 비밀번호</label>
    <input id="resetPasswordInput" type="password" autocomplete="new-password" placeholder="8자 이상">
    <p class="account-help">관리자가 설정한 비밀번호는 임시 비밀번호로 처리됩니다. 사용자는 다음 로그인 후 개인 비밀번호로 변경해야 합니다.</p>
  `;
  Modal.open({
    title: "비밀번호 초기화",
    body,
    footButtons: [
      {
        label: "변경",
        primary: true,
        onClick: async () => {
          const password = $("#resetPasswordInput").value;
          if (!password || password.length < 8) {
            toast("비밀번호는 8자 이상이어야 합니다.", "error");
            return false;
          }
          try {
            await api(`api/code/auth/users/${encodeURIComponent(userId)}/password`, {
              method: "POST",
              body: { password },
            });
            toast("비밀번호를 초기화했습니다. 사용자는 다음 로그인 후 변경해야 합니다.", "ok");
            await reload?.();
          } catch (e) {
            toast("비밀번호 변경 실패: " + e.message, "error");
            return false;
          }
        },
      },
      { label: "취소" },
    ],
  });
}

async function openAccountDataModal(userId) {
  let data;
  try {
    data = await api(`api/code/auth/users/${encodeURIComponent(userId)}/data`);
  } catch (e) {
    toast("데이터 요약 로드 실패: " + e.message, "error");
    return;
  }
  const s = data.summary || {};
  const body = document.createElement("div");
  body.innerHTML = `
    <div class="account-data-grid">
      <span>세션</span><strong>${Number(s.sessions || 0)}</strong>
      <span>워크스페이스 파일</span><strong>${Number(s.workspace_files || 0)}</strong>
      <span>임시 인덱스 파일</span><strong>${Number(s.project_context_files || 0)}</strong>
      <span>사용자 스킬</span><strong>${Number(s.user_skills || 0)}</strong>
      <span>장기기억 파일</span><strong>${Number(s.memory_files || 0)}</strong>
      <span>Second Brain 노드</span><strong>${Number(s.thoughts || 0)}</strong>
      <span>검토함 항목</span><strong>${Number(s.inbox_items || 0)}</strong>
      <span>원문/Raw chunks</span><strong>${Number(s.raw_chunks || 0)}</strong>
      <span>전체 파일</span><strong>${Number(s.total_files || 0)}</strong>
      <span>전체 크기</span><strong>${accountFormatBytes(s.total_bytes || 0)}</strong>
    </div>
    <p class="account-help">보안상 admin UI에서는 원문 내용을 직접 열람하지 않고 개수와 크기만 보여줍니다. 필요하면 이관 기능으로 대상 계정의 워크스페이스에 복사합니다.</p>
  `;
  Modal.open({
    title: `데이터 요약 · ${userId}`,
    body,
    footButtons: [{ label: "닫기" }],
  });
}

function openTransferUserDataModal(sourceUserId, users, reload) {
  const targets = users.filter(u => u.id !== sourceUserId && !u.disabled);
  if (!targets.length) {
    toast("이관할 활성 대상 계정이 없습니다.", "error");
    return;
  }
  const body = document.createElement("div");
  body.innerHTML = `
    <p class="account-help"><strong>${accountEscapeHtml(sourceUserId)}</strong> 데이터를 대상 계정 워크스페이스의 <code>admin_imports</code> 폴더로 복사합니다.</p>
    <label>대상 계정</label>
    <select id="transferTargetUser">
      ${targets.map(u => `<option value="${accountEscapeHtml(u.id)}">${accountEscapeHtml(u.display_name || u.id)} (${accountEscapeHtml(u.id)})</option>`).join("")}
    </select>
    <div class="account-transfer-options">
      <label><input type="checkbox" value="sessions" checked> 세션</label>
      <label><input type="checkbox" value="workspace" checked> 워크스페이스</label>
      <label><input type="checkbox" value="project_context" checked> 워크스페이스 임시 인덱스</label>
      <label><input type="checkbox" value="skills" checked> 사용자 스킬</label>
      <label><input type="checkbox" value="memory" checked> 장기기억</label>
      <label><input type="checkbox" value="knowledge" checked> Second Brain/지식</label>
    </div>
  `;
  Modal.open({
    title: "사용자 데이터 이관",
    body,
    footButtons: [
      {
        label: "이관",
        primary: true,
        onClick: async () => {
          const include = $$(".account-transfer-options input:checked", body).map(el => el.value);
          if (!include.length) {
            toast("이관할 항목을 하나 이상 선택하세요.", "error");
            return false;
          }
          try {
            const res = await api(`api/code/auth/users/${encodeURIComponent(sourceUserId)}/transfer`, {
              method: "POST",
              body: { target_user_id: $("#transferTargetUser").value, include },
            });
            toast(`이관 완료: ${res.target_user_id}`, "ok");
            await reload?.();
          } catch (e) {
            toast("데이터 이관 실패: " + e.message, "error");
            return false;
          }
        },
      },
      { label: "취소" },
    ],
  });
}

async function openAuditLogModal() {
  let data;
  try {
    data = await api("api/code/auth/audit?limit=100");
  } catch (e) {
    toast("감사 로그 로드 실패: " + e.message, "error");
    return;
  }
  const items = data.items || [];
  const body = document.createElement("div");
  body.innerHTML = items.length ? `
    <div class="audit-list">
      ${items.map(item => `
        <div class="audit-row">
          <strong>${accountEscapeHtml(item.action || "-")}</strong>
          <span>${accountEscapeHtml(item.actor_id || "-")} → ${accountEscapeHtml(item.target_id || "-")}</span>
          <span>${accountFormatTime(item.ts)}</span>
        </div>
      `).join("")}
    </div>
  ` : '<div class="account-empty">감사 로그가 없습니다.</div>';
  Modal.open({
    title: "감사 로그",
    body,
    footButtons: [{ label: "닫기" }],
  });
}

// ── 테마 토글 (다크/라이트) ──
function applyTheme(name) {
  document.documentElement.setAttribute("data-theme", name);
  $("#themeBtn").textContent = name === "light" ? "☀️" : "🌙";
  $("#themeBtn").title = name === "light" ? "라이트 → 다크 전환" : "다크 → 라이트 전환";
  // highlight.js 코드블록 스타일 동적 교체
  const linkId = "hljs-theme-link";
  let link = document.getElementById(linkId);
  if (!link) {
    link = document.createElement("link");
    link.id = linkId;
    link.rel = "stylesheet";
    document.head.appendChild(link);
  }
  link.href = name === "light"
    ? "/static/vendor/highlightjs-11.9.0/github.min.css"
    : "/static/vendor/highlightjs-11.9.0/github-dark.min.css";
}

const _initialTheme = localStorage.getItem("code_assist_v1.theme") || "light";
applyTheme(_initialTheme);

$("#themeBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme") || "dark";
  const next = cur === "dark" ? "light" : "dark";
  applyTheme(next);
  localStorage.setItem("code_assist_v1.theme", next);
  toast(next === "light" ? "☀️ 라이트 모드" : "🌙 다크 모드", "ok");
});

// ── KB 토글 ──
function ensureAutoSkillsButton() {
  let btn = $("#btnAutoSkills");
  if (!btn) {
    const kb = $("#btnKB");
    if (!kb) return null;
    btn = document.createElement("button");
    btn.className = "icon-btn";
    btn.id = "btnAutoSkills";
    btn.type = "button";
    btn.title = "Auto Skill routing";
    btn.textContent = "Auto";
    kb.insertAdjacentElement("afterend", btn);
  }
  btn.classList.toggle("active", State.autoSkills);
  return btn;
}

$("#btnKB").classList.toggle("active", State.enableKnowledge);
$("#btnKB").addEventListener("click", () => {
  State.enableKnowledge = !State.enableKnowledge;
  localStorage.setItem("code_assist_v1.enableKnowledge", State.enableKnowledge ? "1" : "0");
  $("#btnKB").classList.toggle("active", State.enableKnowledge);
  Chat.refreshChips();
  refreshMetaBar();
  toast(State.enableKnowledge ? "Second Brain 검색 ON" : "Second Brain 검색 OFF", "ok");
});

const memoryBtn = $("#btnMemory");
if (memoryBtn) {
  memoryBtn.classList.toggle("active", State.enableMemory);
  memoryBtn.addEventListener("click", () => {
    State.enableMemory = !State.enableMemory;
    localStorage.setItem("code_assist_v1.enableMemory", State.enableMemory ? "1" : "0");
    memoryBtn.classList.toggle("active", State.enableMemory);
    Chat.refreshChips();
    refreshMetaBar();
    toast(State.enableMemory ? "장기기억 참고 ON" : "장기기억 참고 OFF", "ok");
  });
}

const autoSkillsBtn = ensureAutoSkillsButton();
autoSkillsBtn?.addEventListener("click", () => {
  State.autoSkills = !State.autoSkills;
  localStorage.setItem("code_assist_v1.autoSkills", State.autoSkills ? "1" : "0");
  autoSkillsBtn.classList.toggle("active", State.autoSkills);
  Chat.refreshChips();
  refreshMetaBar();
  toast(State.autoSkills ? "Auto Skill ON" : "Auto Skill OFF", "ok");
});

// ── 첨부 ──
$("#btnSkillCreator")?.addEventListener("click", () => {
  Skills.openCreator?.({
    includeConversation: true,
    source: "button",
  });
});

const composerAddMenu = $("#composerAddMenu");
const composerAddTrigger = $("#btnAttach");

function closeComposerAddMenu() {
  if (!composerAddMenu || !composerAddTrigger) return;
  composerAddMenu.hidden = true;
  composerAddTrigger.classList.remove("active");
  composerAddTrigger.setAttribute("aria-expanded", "false");
}

composerAddTrigger?.addEventListener("click", event => {
  event.stopPropagation();
  const opening = !!composerAddMenu?.hidden;
  if (!composerAddMenu) return;
  composerAddMenu.hidden = !opening;
  composerAddTrigger.classList.toggle("active", opening);
  composerAddTrigger.setAttribute("aria-expanded", opening ? "true" : "false");
  if (opening) composerAddMenu.querySelector("button")?.focus();
});

composerAddMenu?.addEventListener("click", event => {
  const button = event.target.closest("[data-composer-add]");
  if (!button) return;
  const action = button.dataset.composerAdd;
  closeComposerAddMenu();
  if (action === "upload") {
    document.dispatchEvent(new CustomEvent("second-brain:upload-source"));
  } else if (action === "promote") {
    document.dispatchEvent(new CustomEvent("second-brain:promote-chat"));
  } else if (action === "attach") {
    $("#hiddenFileInput").click();
  }
});

document.addEventListener("click", event => {
  if (composerAddMenu?.hidden) return;
  if (composerAddMenu.contains(event.target) || composerAddTrigger?.contains(event.target)) return;
  closeComposerAddMenu();
});

document.addEventListener("keydown", event => {
  if (event.key === "Escape" && !composerAddMenu?.hidden) closeComposerAddMenu();
});

$("#hiddenFileInput").addEventListener("change", e => {
  Workspace.handleAttach(e.target.files);
  e.target.value = "";
});

// ── 입력 박스 ──
const SESSION_SLASH_COMMANDS = [
  {
    id: "status",
    name: "세션 상태",
    description: "현재 context window 사용량과 남은 토큰을 확인합니다.",
    kind: "session-command",
  },
  {
    id: "compact",
    name: "세션 압축",
    description: "오래된 대화를 요약해 context window를 확보합니다.",
    kind: "session-command",
  },
  {
    id: "clear",
    name: "세션 비우기",
    description: "현재 대화 context를 비우고 새 세션으로 시작합니다.",
    kind: "session-command",
  },
];

const SkillAutocomplete = {
  el: null,
  items: [],
  activeIndex: 0,
  requestId: 0,

  init() {
    if (this.el) return;
    const wrap = document.querySelector(".composer");
    if (!wrap) return;
    this.el = document.createElement("div");
    this.el.className = "skill-suggest";
    this.el.hidden = true;
    const inputWrap = wrap.querySelector(".input-wrap");
    wrap.insertBefore(this.el, inputWrap);
    document.addEventListener("click", e => {
      if (this.el?.hidden) return;
      if (this.el.contains(e.target) || e.target === composerInput) return;
      this.hide();
    });
  },

  getState() {
    const value = composerInput.value || "";
    const pos = composerInput.selectionStart ?? value.length;
    const before = value.slice(0, pos);
    const m = before.match(/^\/([A-Za-z0-9_-]{0,63})$/);
    if (!m) return null;
    return { prefix: m[1] || "", pos };
  },

  async update() {
    if (!this.el) return;
    const state = this.getState();
    if (!state) {
      this.hide();
      return;
    }
    const rid = ++this.requestId;
    try {
      if (!Skills.cache.length) {
        try {
          await Skills.fetchList("");
        } catch {
          // Session slash commands are local and should still work if skill loading fails.
        }
      }
      if (rid !== this.requestId) return;
      this.items = this.filter(state.prefix);
      this.activeIndex = 0;
      this.render(state.prefix);
    } catch {
      this.hide();
    }
  },

  filter(prefix) {
    const q = String(prefix || "").toLowerCase();
    const commandRows = SESSION_SLASH_COMMANDS.map(command => {
      const id = String(command.id || "");
      const name = String(command.name || "");
      const hay = [id, name, command.description || ""].join(" ").toLowerCase();
      let score = 0;
      if (!q) score = 120;
      else if (id.toLowerCase().startsWith(q)) score = 160;
      else if (name.toLowerCase().startsWith(q)) score = 140;
      else if (id.toLowerCase().includes(q)) score = 110;
      else if (name.toLowerCase().includes(q)) score = 100;
      else if (hay.includes(q)) score = 80;
      return { item: command, score, rank: 0 };
    }).filter(x => x.score > 0);
    const skillRows = (Skills.cache || []).map(s => {
      const id = String(s.id || "");
      const name = String(s.name || "");
      const hay = [
        id,
        name,
        s.description || "",
        ...(s.tags || []),
        ...(s.domains || []),
        ...(s.services || []),
      ].join(" ").toLowerCase();
      let score = 0;
      if (!q) score = State.activeSkills.has(id) ? 20 : 1;
      else if (id.toLowerCase().startsWith(q)) score = 100;
      else if (name.toLowerCase().startsWith(q)) score = 90;
      else if (id.toLowerCase().includes(q)) score = 70;
      else if (name.toLowerCase().includes(q)) score = 60;
      else if (hay.includes(q)) score = 30;
      return { item: s, score, rank: 1 };
    }).filter(x => x.score > 0);
    const rows = commandRows.concat(skillRows);
    rows.sort((a, b) => b.score - a.score || a.rank - b.rank || String(a.item.id).localeCompare(String(b.item.id)));
    return rows.slice(0, 8).map(x => x.item);
  },

  render(prefix) {
    if (!this.el || !this.items.length) {
      this.hide();
      return;
    }
    const q = prefix ? `/${prefix}` : "/";
    this.el.hidden = false;
    this.el.innerHTML = `
      <div class="skill-suggest-head">
        <span>${skillSuggestEscape(q)}</span>
        <kbd>Tab</kbd><span>완성</span>
        <kbd>↑↓</kbd><span>선택</span>
      </div>
      <div class="skill-suggest-list"></div>
    `;
    const list = this.el.querySelector(".skill-suggest-list");
    this.items.forEach((s, idx) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "skill-suggest-row" + (s.kind === "session-command" ? " session-command" : "") + (idx === this.activeIndex ? " active" : "");
      row.title = s.description || "";
      const meta = s.kind === "session-command"
        ? "세션 명령"
        : (s.allow_implicit_invocation === false ? "명시 전용" : "자동 가능");
      row.innerHTML = `
        <span class="skill-suggest-main">
          <span class="skill-suggest-id">/${skillSuggestEscape(s.id)}</span>
          <span class="skill-suggest-name">${skillSuggestEscape(s.name || s.id)}</span>
        </span>
        <span class="skill-suggest-meta">${skillSuggestEscape(meta)}</span>
      `;
      row.addEventListener("mousemove", () => {
        this.activeIndex = idx;
        this.paintActive();
      });
      row.addEventListener("mousedown", e => {
        e.preventDefault();
        this.accept(idx);
      });
      list.appendChild(row);
    });
  },

  paintActive() {
    if (!this.el) return;
    this.el.querySelectorAll(".skill-suggest-row").forEach((row, idx) => {
      row.classList.toggle("active", idx === this.activeIndex);
    });
  },

  handleKeydown(e) {
    if (!this.el || this.el.hidden) return false;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      this.activeIndex = Math.min(this.items.length - 1, this.activeIndex + 1);
      this.paintActive();
      return true;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      this.activeIndex = Math.max(0, this.activeIndex - 1);
      this.paintActive();
      return true;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      this.accept(this.activeIndex);
      return true;
    }
    if (e.key === "Enter" && !e.shiftKey) {
      const state = this.getState();
      const item = this.items[this.activeIndex];
      if (state?.prefix && item?.kind === "session-command" && item.id.toLowerCase() === state.prefix.toLowerCase()) {
        this.hide();
        return false;
      }
      e.preventDefault();
      this.accept(this.activeIndex);
      return true;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      this.hide();
      return true;
    }
    return false;
  },

  accept(index = this.activeIndex) {
    const state = this.getState();
    const skill = this.items[index];
    if (!state || !skill) {
      this.hide();
      return;
    }
    const value = composerInput.value || "";
    const rest = value.slice(state.pos).replace(/^\s+/, "");
    const spacer = skill.kind === "session-command" && !rest ? "" : " ";
    const next = `/${skill.id}${spacer}${rest}`;
    composerInput.value = next;
    const cursor = (`/${skill.id}${spacer}`).length;
    composerInput.setSelectionRange(cursor, cursor);
    composerInput.dispatchEvent(new Event("input", { bubbles: true }));
    this.hide();
  },

  hide() {
    if (!this.el) return;
    this.el.hidden = true;
    this.items = [];
  },
};

function skillSuggestEscape(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const composerInput = $("#composerInput");
composerInput.addEventListener("input", () => {
  composerInput.style.height = "auto";
  composerInput.style.height = Math.min(composerInput.scrollHeight, 200) + "px";
  SkillAutocomplete.update();
});
composerInput.addEventListener("keydown", e => {
  if (SkillAutocomplete.handleKeydown(e)) return;
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    Chat.send();
  } else if (e.key === "Escape" && State.streaming) {
    Chat.stop();
  }
});

// ── 클립보드 이미지 붙여넣기 (Ctrl/Cmd+V) → VLM multimodal 첨부 ──
const MAX_PASTED_IMAGE_MB = 20;

function _readImageItem(item) {
  const blob = item.getAsFile();
  if (!blob) return Promise.reject(new Error("blob 없음"));
  return _readImageFile(blob);
}

function _readImageFile(file) {
  return new Promise((resolve, reject) => {
    if (!file) { reject(new Error("file 없음")); return; }
    if (file.size > MAX_PASTED_IMAGE_MB * 1024 * 1024) {
      reject(new Error(`이미지가 ${MAX_PASTED_IMAGE_MB}MB 초과`));
      return;
    }
    const reader = new FileReader();
    reader.onload = () => resolve({
      name: file.name || `drop-${Date.now()}.${((file.type || "").split("/")[1] || "png")}`,
      mime: file.type || "image/png",
      dataUrl: reader.result,
      size: file.size,
    });
    reader.onerror = () => reject(reader.error || new Error("read 실패"));
    reader.readAsDataURL(file);
  });
}

composerInput.addEventListener("paste", async e => {
  const items = e.clipboardData?.items || [];
  const imageItems = [...items].filter(it => it.kind === "file" && it.type.startsWith("image/"));
  if (!imageItems.length) return;  // 텍스트 paste 는 기본 동작
  e.preventDefault();
  for (const it of imageItems) {
    try {
      const img = await _readImageItem(it);
      State.pastedImages.push(img);
      toast(`🖼 이미지 첨부 (${(img.size / 1024).toFixed(0)}K)`, "ok");
    } catch (err) {
      toast("이미지 붙여넣기 실패: " + err.message, "error");
    }
  }
  Chat.refreshChips();
  refreshMetaBar();
});

async function handleComposerDroppedFiles(files) {
  files = Array.from(files || []).filter(Boolean);
  if (!files.length) return;
  const images = files.filter(file => (file.type || "").startsWith("image/"));
  const others = files.filter(file => !(file.type || "").startsWith("image/"));
  let imageOk = 0;
  let imageFail = 0;
  for (const file of images) {
    try {
      State.pastedImages.push(await _readImageFile(file));
      imageOk++;
    } catch (e) {
      imageFail++;
      console.warn("[composer] image drop failed", file.name, e);
    }
  }
  if (imageOk) toast(`이미지 ${imageOk}개 첨부`, "ok");
  if (imageFail) toast(`이미지 ${imageFail}개 첨부 실패`, "error");
  if (others.length) {
    await Workspace.handleAttach(others);
  } else {
    Chat.refreshChips();
    refreshMetaBar();
  }
}

function setupComposerFileDrop() {
  const composer = document.querySelector(".composer");
  if (!composer) return;
  let dragDepth = 0;
  const hasFiles = e => Array.from(e.dataTransfer?.types || []).includes("Files");
  ["dragenter", "dragover"].forEach(type => {
    composer.addEventListener(type, e => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      e.stopPropagation();
      if (type === "dragenter") dragDepth++;
      composer.classList.add("drag-over");
    });
  });
  composer.addEventListener("dragleave", e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) composer.classList.remove("drag-over");
  });
  composer.addEventListener("drop", e => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    dragDepth = 0;
    composer.classList.remove("drag-over");
    handleComposerDroppedFiles(e.dataTransfer?.files || []);
  });
}

$("#btnSend").addEventListener("click", () => {
  if (State.streaming) Chat.stop();
  else Chat.send();
});

// 마크다운 렌더 옵션. 코드블록 강조는 chat.js의 highlightElement가 적용한다.
if (window.marked) {
  marked.setOptions({
    breaks: true,
    gfm: true,
  });
}

// ── 사이드바 접기/펼치기 ──
const SidebarToggle = {
  applyLeft(collapsed, persist = true) {
    document.querySelector(".app-root").classList.toggle("left-collapsed", collapsed);
    if (persist) localStorage.setItem("ws.left.collapsed", collapsed ? "1" : "0");
    const btn = $("#btnToggleLeft");
    if (btn) {
      btn.title = collapsed ? "왼쪽 사이드바 펼치기" : "왼쪽 사이드바 접기";
      btn.setAttribute("aria-expanded", String(!collapsed));
    }
  },
  applyRight(collapsed, persist = true) {
    document.querySelector(".app-root").classList.toggle("right-collapsed", collapsed);
    if (persist) localStorage.setItem("ws.right.collapsed", collapsed ? "1" : "0");
    const btn = $("#btnToggleRight");
    if (btn) {
      btn.title = collapsed ? "워크스페이스 펼치기" : "워크스페이스 접기";
      btn.setAttribute("aria-expanded", String(!collapsed));
    }
  },
  toggleLeft() {
    const cur = document.querySelector(".app-root").classList.contains("left-collapsed");
    if (cur && window.matchMedia("(max-width: 860px)").matches) SidebarToggle.applyRight(true, false);
    SidebarToggle.applyLeft(!cur);
  },
  toggleRight() {
    const cur = document.querySelector(".app-root").classList.contains("right-collapsed");
    if (cur && window.matchMedia("(max-width: 860px)").matches) SidebarToggle.applyLeft(true, false);
    SidebarToggle.applyRight(!cur);
  },
  init() {
    // The workspace always starts closed and opens only after the folder button.
    const left = localStorage.getItem("ws.left.collapsed");
    SidebarToggle.applyLeft(left === "1", false);
    SidebarToggle.applyRight(true, false);
    window.matchMedia("(max-width: 860px)").addEventListener("change", ({ matches }) => {
      if (matches && !document.querySelector(".app-root").classList.contains("left-collapsed")) {
        // Keep one drawer accessible after shrinking a two-panel desktop layout.
        SidebarToggle.applyRight(true, false);
      }
    });
  },
};
$("#btnToggleLeft").addEventListener("click", () => SidebarToggle.toggleLeft());
$("#btnToggleRight").addEventListener("click", () => SidebarToggle.toggleRight());

// ── 부트 ──
function showBootError(error) {
  console.error("[boot] initialization failed", error);
  $("#modalBg")?.classList.remove("show");
  const status = $("#metaStatus");
  if (status) status.textContent = "초기화 실패";
  const messages = $("#messages");
  if (messages) {
    const row = document.createElement("div");
    row.className = "msg assistant";
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "!";
    const body = document.createElement("div");
    body.className = "body";
    const role = document.createElement("div");
    role.className = "role";
    role.textContent = "초기화 오류";
    const content = document.createElement("div");
    content.className = "content";
    content.textContent = `로그인 후 앱 초기화가 실패했습니다: ${error?.message || error}`;
    body.appendChild(role);
    body.appendChild(content);
    row.appendChild(avatar);
    row.appendChild(body);
    messages.appendChild(row);
  }
  toast("앱 초기화 실패: " + (error?.message || error), "error");
}

window.addEventListener("DOMContentLoaded", async () => {
  try {
    resetModalOverlay();
    setNotionWorkspaceLink();
    SidebarToggle.init();
    if (!(await loadAuthUser())) return;
    if (State.currentUser?.must_change_password) {
      openOwnPasswordChangeModal({ required: true });
      return;
    }
    await loadModels();
    setTab("skills");
    ensureAutoSkillsButton();
    SkillAutocomplete.init();
    setupComposerFileDrop();
    Chat.refreshChips();
    Workspace.refresh();
  } catch (e) {
    showBootError(e);
  }
});

window.addEventListener("pageshow", () => {
  resetModalOverlay();
});
