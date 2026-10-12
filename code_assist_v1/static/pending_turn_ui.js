/* Explicit server-owned recovery; never submit a guessed replacement question. */
(function attach(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (!root || !root.document) return;
  let controller;
  const get = () => controller || (controller = api.createController({
    document: root.document, state: State, chat: Chat,
    bridge: () => root.KnowledgeCoreBrowser,
    notice: (text, kind) => { if (typeof toast === "function") toast(text, kind); },
  }));
  root.PendingTurnUI = { clear: () => controller && controller.clear(), refresh: (...args) => get().refresh(...args) };
})(typeof globalThis === "object" ? globalThis : this, function build() {
  "use strict";
  function createController({ document, state, chat, bridge, notice }) {
    let generation = 0;
    const panel = document.getElementById("pendingTurnPanel");
    const owner = () => state.currentUser && state.currentUser.id;
    function clear() { generation++; panel.replaceChildren(); panel.hidden = true; }
    function element(tag, text) { const node = document.createElement(tag); node.textContent = text; return node; }
    function button(text, callback) {
      const node = element("button", text); node.type = "button"; node.className = "ghost compact";
      node.addEventListener("click", callback); return node;
    }
    async function refresh(cid, afterSequenceNo = 0) {
      clear();
      const selectedOwner = owner(), ticket = generation, api = bridge();
      const current = () => generation === ticket && owner() === selectedOwner && state.session_id === cid;
      if (!api || !api.active || !selectedOwner || !cid || !current()) return;
      let page;
      try { page = await api.readPendingTurns(cid, { afterSequenceNo, limit: 20 }); }
      catch (_) {
        if (current()) {
          panel.hidden = false;
          panel.append(element("p", "미완료 요청 목록을 확인하지 못했습니다."), button("다시 확인", () => refresh(cid)));
        }
        return;
      }
      if (!current() || !page.items.length && !afterSequenceNo) return;
      panel.hidden = false;
      panel.append(element("strong", "서버에 남아 있는 미완료 요청"));
      for (const item of page.items) {
        const row = element("div", ""); row.style.marginTop = "8px";
        const text = element("span", `질문 ${item.sequence_no}: ${item.message.slice(0, 180)}`);
        text.title = item.message; row.append(text);
        if (item.can_resume) {
          const resume = button("결과 확인·이어서 처리", async () => {
            if (!current() || state.streaming || resume.disabled) return;
            resume.disabled = true;
            row.append(element("span", " 처리 상태를 확인하고 있습니다…"));
            let resultNotice = "처리 결과를 대화 기록에서 확인했습니다.", kind = "ok";
            try { await api.resumePendingTurn(cid, item.operation_id); }
            catch (error) {
              resultNotice = error.code === "INCOMPLETE_EVENT_STREAM"
                ? "아직 처리 중입니다. 잠시 후 다시 확인해 주세요. 새 질문은 만들지 않았습니다."
                : error.message;
              kind = error.terminal ? "error" : "info";
            }
            if (!current()) return;
            try {
              const messages = await api.readSessionMessages(cid);
              if (!current()) return;
              state.messages = chat.cloneMessages(messages);
              if (state.sessionCache) state.sessionCache.set(cid, chat.cloneMessages(messages));
              chat.renderMessages(state.messages);
            } catch (_) {
              if (!current()) return;
              resultNotice += " 대화 기록 갱신에 실패했습니다. 다시 확인해 주세요.";
              kind = "error";
            }
            notice(resultNotice, kind);
            await refresh(cid);
          });
          row.append(resume);
        } else {
          const explanations = {
            LEGACY_EXECUTION_PROTOCOL: "이전 버전 요청입니다. 운영자 확인이 필요합니다.",
            CONVERSATION_ARCHIVED: "보관된 대화이므로 이어서 처리할 수 없습니다.",
            WIKI_ARCHIVED_HISTORY_ONLY: "보관된 Wiki이므로 읽기만 가능합니다.",
            CORPUS_CLOSURE_STALE: "Wiki 범위가 변경되어 이 요청을 그대로 재개할 수 없습니다.",
            REQUEST_SCOPE_CHANGED: "접근 범위가 변경되어 이 요청을 그대로 재개할 수 없습니다.",
          };
          row.append(element("small", " · " + (explanations[item.reason] || "현재 상태에서는 재개할 수 없습니다. 운영자에게 문의해 주세요.")));
        }
        panel.append(row);
      }
      panel.append(button("목록 새로고침", () => refresh(cid)));
      if (page.has_more) panel.append(button("다음 미완료 요청", () => refresh(cid, page.next_after_sequence_no)));
      if (afterSequenceNo) panel.append(button("처음으로", () => refresh(cid)));
    }
    return Object.freeze({ clear, refresh });
  }
  return Object.freeze({ createController });
});
