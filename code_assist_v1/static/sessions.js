/* code_assist_v1/static/sessions.js — 세션 패널 (삭제 + 날짜/시간 표시) */

const CANONICAL_SESSION_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

const Sessions = {
  async fetchList() {
    try {
      if (window.KnowledgeCoreBrowser) await window.KnowledgeCoreBrowser.ready();
      if (window.KnowledgeCoreBrowser?.active) {
        return await window.KnowledgeCoreBrowser.listSessions();
      }
      return await api("api/code/session/list");
    } catch {
      return { sessions: [] };
    }
  },

  async render() {
    if (currentTab !== "sessions") return;
    const body = $("#sidebarBody");
    body.innerHTML = '<div style="padding:14px;color:var(--muted);font-size:12px;">로드 중…</div>';
    const data = await Sessions.fetchList();
    body.innerHTML = "";
    const header = document.createElement("div");
    header.className = "section-title";
    header.textContent = `세션 ${data.sessions?.length || 0}개`;
    body.appendChild(header);

    if (!data.sessions?.length) {
      const el = document.createElement("div");
      el.style.cssText = "padding:14px;color:var(--muted);font-size:12px;";
      el.textContent = "저장된 세션이 없습니다. 채팅 후 자동 저장됩니다.";
      body.appendChild(el);
      return;
    }

    data.sessions.forEach(s => body.appendChild(Sessions._row(s)));
  },

  _row(s) {
    const row = document.createElement("div");
    row.className = "item";
    row.style.flexDirection = "column";
    row.style.alignItems = "stretch";
    row.style.gap = "2px";

    const ts = s.timestamp ? new Date(s.timestamp * 1000) : null;
    const dateStr = ts ? Sessions._fmt(ts) : "—";
    const title = (s.title || s.first_message || s.session_id || "").slice(0, 60);
    const defaultTitle = (s.first_message || s.session_id || "").slice(0, 60);
    const isCurrent = State.session_id === s.session_id;
    const safeTitle = Sessions._escape(title || "(빈 세션)");
    const turnCount = Number.isSafeInteger(s.turn_count)
      ? s.turn_count
      : (Number.isSafeInteger(s.message_count) ? s.message_count : 0);
    const turnState = s.latest_turn_state === "failed" ? " · 실패" : "";
    const wikiPath = Sessions._spacePath(s.space);
    const safeWikiPath = Sessions._escape(wikiPath);

    row.innerHTML = `
      <div style="display:flex;align-items:center;gap:6px;">
        <span class="name" style="flex:1;font-size:13px;${isCurrent ? "color:var(--accent);font-weight:600;" : ""}"
              title="${safeTitle}">${safeTitle}</span>
        <span class="badge">${turnCount}턴${turnState}</span>
        <button class="ghost session-rename" title="이름 변경" style="padding:2px 6px;font-size:11px;">이름</button>
        <button class="ghost session-del" title="삭제" style="padding:2px 6px;font-size:11px;">✕</button>
      </div>
      <div style="font-size:10.5px;color:var(--muted);font-family:var(--mono);">
        ${dateStr}  ·  <span style="opacity:.7;">${(s.session_id || "").slice(0,8)}</span>
      </div>
      <div class="session-wiki-path" title="${safeWikiPath}">참조 Wiki · ${safeWikiPath}</div>
    `;

    // 본문 클릭 → 세션 로드
    row.addEventListener("click", e => {
      if (e.target.classList.contains("session-del") || e.target.classList.contains("session-rename")) return;
      Sessions.load(s.session_id);
    });

    row.querySelector(".session-rename").addEventListener("click", e => {
      e.stopPropagation();
      Sessions.rename(s.session_id, title, defaultTitle);
    });

    // 삭제 버튼
    row.querySelector(".session-del").addEventListener("click", async e => {
      e.stopPropagation();
      if (!confirm(`이 세션을 삭제할까요?\n  ${title || s.session_id}`)) return;
      try {
        if (window.KnowledgeCoreBrowser?.active) {
          await window.KnowledgeCoreBrowser.updateSession(s.session_id, { archived: true });
        } else {
          const r = await authenticatedFetch(`api/code/session/delete/${encodeURIComponent(s.session_id)}`, {
            method: "DELETE",
          });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
        }
        if (State.session_id === s.session_id) {
          State.session_id = null;
          State.messages = [];
          State.compactSummary = "";
          State.compactedMessageCount = 0;
          State.lastContextStatus = null;
          State.sessionCache.delete(s.session_id);
          State.sessionCompactCache.delete(s.session_id);
          Chat.clear();
        }
        toast("세션 삭제됨", "ok");
        Sessions.render();
      } catch (e) {
        toast("삭제 실패: " + e.message, "error");
      }
    });

    return row;
  },

  async load(sid) {
    try {
      if (window.KnowledgeCoreBrowser?.active && !CANONICAL_SESSION_ID_RE.test(String(sid || ""))) {
        State.session_id = null;
        await Sessions.render();
        toast("이전 형식 세션 목록을 canonical 목록으로 갱신했습니다. 다시 선택해 주세요.", "error");
        return;
      }
      const data = window.KnowledgeCoreBrowser?.active
        ? await window.KnowledgeCoreBrowser.loadSession(sid)
        : await api(`api/code/session/load/${encodeURIComponent(sid)}`);
      State.session_id = sid;
      State.messages = Chat.cloneMessages(data.messages || []);
      State.compactSummary = data.metadata?.compact_summary || "";
      State.compactedMessageCount = Number(data.metadata?.compacted_message_count || 0);
      State.lastContextStatus = data.metadata?.context_status || null;
      State.sessionCache.set(sid, Chat.cloneMessages(State.messages));
      State.sessionCompactCache.set(sid, {
        compact_summary: State.compactSummary,
        compacted_message_count: State.compactedMessageCount,
        context_status: State.lastContextStatus,
      });
      Chat.renderMessages(State.messages);
      toast(`세션 로드: ${(data.title || data.first_message || sid).slice(0,24)}`, "ok");
      Sessions.render();
    } catch (e) {
      toast(e.message, "error");
    }
  },

  rename(sid, currentTitle = "", defaultTitle = "") {
    const body = document.createElement("div");
    body.innerHTML = `
      <p class="account-help">세션 목록에 표시할 이름을 변경합니다. 빈 값으로 저장하면 첫 메시지 제목을 다시 사용합니다.</p>
      <label>세션 이름</label>
      <input id="sessionRenameInput" type="text" maxlength="80" autocomplete="off">
      ${defaultTitle ? `<p class="account-help">기본 제목: ${Sessions._escape(defaultTitle)}</p>` : ""}
    `;
    Modal.open({
      title: "세션 이름 변경",
      body,
      footButtons: [
        { label: "취소" },
        {
          label: "저장",
          primary: true,
          onClick: async () => {
            const title = $("#sessionRenameInput").value.trim();
            try {
              if (window.KnowledgeCoreBrowser?.active) {
                if (!title) throw new Error("Canonical 대화 제목은 비울 수 없습니다.");
                await window.KnowledgeCoreBrowser.updateSession(sid, { title });
              } else {
                await api(`api/code/session/rename/${encodeURIComponent(sid)}`, {
                  method: "POST",
                  body: { title },
                });
              }
              toast(title ? "세션 이름을 변경했습니다." : "세션 이름을 기본값으로 되돌렸습니다.", "ok");
              await Sessions.render();
            } catch (e) {
              toast("세션 이름 변경 실패: " + e.message, "error");
              return false;
            }
          },
        },
      ],
    });
    const input = $("#sessionRenameInput");
    if (input) {
      input.value = currentTitle || "";
      input.focus();
      input.select();
    }
  },

  _fmt(d) {
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
  },

  _spacePath(space) {
    if (!space || typeof space !== "object") return "경로 확인 필요";
    const name = String(space.name || "").trim();
    if (space.kind === "personal") {
      return name === "내 지식" || !name ? "내 지식 · 기본" : `내 지식 · ${name}`;
    }
    const kind = ({ part: "Part", team: "팀", responsibility: "담당" })[space.kind]
      || "지식공간";
    return name ? `${kind} · ${name}` : kind;
  },

  _escape(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  },
};

if (typeof module === "object" && module.exports) module.exports = Sessions;
