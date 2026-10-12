/* code_assist_v1/static/chat.js — 메시지 렌더 + SSE 스트리밍 */

const Chat = {
  appendMessage(role, content = "") {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;
    const av = document.createElement("div");
    av.className = "avatar";
    av.textContent = role === "user" ? "U" : "A";
    const body = document.createElement("div");
    body.className = "body";
    const r = document.createElement("div");
    r.className = "role";
    r.textContent = role === "user" ? "You" : "Second Brain Agent";
    const c = document.createElement("div");
    c.className = "content";
    const displayContent = role === "assistant" ? normalizeLatexSymbols(content) : content;
    c.dataset.raw = displayContent;
    if (role === "user") c.textContent = content;
    else c.innerHTML = renderAssistantHtml(displayContent);
    body.appendChild(r);
    body.appendChild(c);
    wrap.appendChild(av);
    wrap.appendChild(body);
    const messagesHost = $("#messages");
    installAssistantSourceDownloads(messagesHost);
    messagesHost.appendChild(wrap);
    Chat.scrollToBottom();
    return c;
  },

  clear() {
    window.PendingTurnUI?.clear();
    $("#messages").innerHTML = "";
  },

  scrollToBottom() {
    const el = $("#messages");
    if (!el) return;
    // 매 토큰마다 innerHTML 통째 재렌더링 + markdown 파싱 → single rAF로 layout 안정화 부족.
    // double rAF로 두 프레임 보장 + 마지막 element scrollIntoView 이중 보장.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        // 1차: 컨테이너 scrollTop 강제 끝으로
        el.scrollTop = el.scrollHeight + 99999;
        // 2차: 마지막 자식 element가 view 끝에 들어오도록 (이중 안전망)
        const last = el.lastElementChild;
        if (last && last.scrollIntoView) {
          try { last.scrollIntoView({ block: "end", inline: "nearest" }); } catch (_) {}
        }
      });
    });
  },

  ensureSessionId() {
    if (!State.session_id) State.session_id = Chat.newSessionId();
    return State.session_id;
  },

  newSessionId() {
    if (window.crypto?.getRandomValues) {
      const bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      return "s_" + Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
    }
    return "s_" + Date.now().toString(16) + Math.random().toString(16).slice(2, 10);
  },

  workspaceProjectId(sessionId = State.session_id) {
    const sid = String(sessionId || "").trim();
    if (!sid) return "";
    return "session-" + sid.replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 90);
  },

  cloneMessages(messages) {
    return (messages || []).map(m => ({
      role: m.role,
      content: cloneSessionValue(m.content),
    }));
  },

  isCurrentSession(sessionId) {
    return String(State.session_id || "") === String(sessionId || "");
  },

  setCurrentMessages(messages) {
    State.messages = Chat.cloneMessages(messages);
    if (State.session_id) {
      State.sessionCache.set(State.session_id, Chat.cloneMessages(State.messages));
    }
  },

  renderMessages(messages) {
    Chat.clear();
    (messages || []).forEach(m => {
      const node = Chat.appendMessage(m.role, messageDisplayText(m.content));
      if (m.role === "assistant") attachCopyButtons(node);
    });
  },

  async persistSession({
    sessionId = State.session_id,
    messages = State.messages,
    skillsUsed = [],
    metadata = {},
    title,
    updateCurrent = true,
  } = {}) {
    if (window.KnowledgeCoreBrowser?.active) {
      return { session_id: sessionId, status: "canonical" };
    }
    try {
      const targetCompact = State.sessionCompactCache.get(sessionId) || {};
      const isTargetCurrent = !sessionId || Chat.isCurrentSession(sessionId);
      const compactSummary = metadata.compact_summary !== undefined
        ? metadata.compact_summary
        : (isTargetCurrent ? (State.compactSummary || "") : (targetCompact.compact_summary || ""));
      const compactedMessageCount = metadata.compacted_message_count !== undefined
        ? Number(metadata.compacted_message_count || 0)
        : (isTargetCurrent ? Number(State.compactedMessageCount || 0) : Number(targetCompact.compacted_message_count || 0));
      const contextStatus = metadata.context_status !== undefined
        ? metadata.context_status
        : (isTargetCurrent ? (State.lastContextStatus || null) : (targetCompact.context_status || null));
      const mergedMetadata = {
        source: "code_assist_v1",
        model: State.model,
        ...metadata,
        compact_summary: compactSummary,
        compacted_message_count: compactedMessageCount,
        context_status: contextStatus,
      };
      const r = await authenticatedFetch("api/code/session/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId,
          messages,
          skills_used: skillsUsed,
          ...(title !== undefined ? { title } : {}),
          metadata: mergedMetadata,
        }),
      });
      if (!r.ok) return null;
      const j = await r.json();
      if (j?.session_id) {
        State.sessionCache.set(j.session_id, Chat.cloneMessages(messages));
        State.sessionCompactCache.set(j.session_id, {
          compact_summary: compactSummary,
          compacted_message_count: compactedMessageCount,
          context_status: contextStatus,
        });
        if (updateCurrent && (!sessionId || Chat.isCurrentSession(sessionId))) {
          State.session_id = j.session_id;
          State.messages = Chat.cloneMessages(messages);
        }
      }
      if (currentTab === "sessions") Sessions.render();
      return j;
    } catch (e) {
      console.warn("[chat] session save failed", e);
      return null;
    }
  },

  failureContent(title, message, partial = "") {
    const cleanMessage = String(message || "unknown error").trim();
    const cleanPartial = normalizeLatexSymbols(String(partial || "").trim());
    const note = `> ${title}: ${cleanMessage}`;
    return cleanPartial ? `${cleanPartial}\n\n${note}` : note;
  },

  statusContent(status = {}) {
    const message = String(status.message || "응답 준비 중").trim();
    const stage = String(status.stage || "").trim();
    const bits = [];
    if (status.loaded_n_ctx) bits.push(`ctx ${Number(status.loaded_n_ctx).toLocaleString()}`);
    if (status.max_tokens) bits.push(`max ${Number(status.max_tokens).toLocaleString()}`);
    return `
      <div class="stream-status">
        <span class="stream-spinner"></span>
        <span>${chatEscapeHtml(message)}</span>
        ${stage ? `<span class="stream-stage">${chatEscapeHtml(stage)}</span>` : ""}
        ${bits.length ? `<span class="stream-stage">${chatEscapeHtml(bits.join(" · "))}</span>` : ""}
      </div>
    `;
  },

  renderStreamingNode(node, buffer, status = null) {
    if (!node) return;
    const text = normalizeLatexSymbols(buffer || "");
    node.dataset.raw = text;
    const statusHtml = status && !text.trim() ? Chat.statusContent(status) : "";
    const inlineStatus = status && text.trim() ? `<div class="stream-status inline">${chatEscapeHtml(status.message || "생성 중")}</div>` : "";
    node.innerHTML = (text.trim() ? renderAssistantHtml(text) : statusHtml) + inlineStatus + '<span class="cursor"></span>';
  },

  truncationNotice(meta = {}) {
    if (!meta?.output_truncated && meta?.finish_reason !== "length") return "";
    const maxTokens = meta.gguf_actual_max_tokens || meta.max_tokens || "";
    const ctx = meta.gguf_loaded_n_ctx || "";
    const details = [
      maxTokens ? `max_tokens=${Number(maxTokens).toLocaleString()}` : "",
      ctx ? `loaded_ctx=${Number(ctx).toLocaleString()}` : "",
    ].filter(Boolean).join(", ");
    return `\n\n> 출력 한도에 도달해 답변이 중간에서 끊겼을 수 있습니다.${details ? ` (${details})` : ""}`;
  },

  async readErrorResponse(response) {
    try {
      const text = await response.clone().text();
      const match = text.match(/^data:\s*(\{.*\})\s*$/m);
      if (match) {
        try {
          const obj = JSON.parse(match[1]);
          if (obj.error) return obj.error;
        } catch {}
      }
      const compact = text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
      return compact ? compact.slice(0, 500) : "";
    } catch {
      return "";
    }
  },

  sessionCommandPayload(messages = State.messages) {
    let userId = null;
    try {
      const u = JSON.parse(sessionStorage.getItem("demos_user") || "null");
      if (u && u.id) userId = u.id;
    } catch {}
    return {
      model: State.model,
      messages: Chat.cloneMessages(messages || []),
      skills: Array.from(State.activeSkills),
      auto_skills: State.autoSkills,
      auto_skill_max: 3,
      effort: State.effort,
      enable_knowledge: State.enableKnowledge,
      enable_project_context: true,
      project_id: Chat.workspaceProjectId(State.session_id),
      enable_memory: State.enableMemory,
      session_id: State.session_id || "",
      workspace_files: State.workspaceFiles,
      pinned_thoughts: State.pinnedThoughts.map(t => t.id),
      system_prompt: State.systemPromptExtra || "",
      compact_summary: State.compactSummary || "",
      user_id: userId,
    };
  },

  formatContextStatus(status = {}) {
    const pct = Math.round(Number(status.used_ratio || 0) * 100);
    const remaining = Number(status.remaining_tokens_est || 0).toLocaleString();
    const input = Number(status.input_tokens_est || 0).toLocaleString();
    const output = Number(status.reserved_output_tokens || 0).toLocaleString();
    const windowSize = Number(status.context_window || 0).toLocaleString();
    const compactState = status.should_force_compact
      ? "compact 필요"
      : (status.should_auto_compact ? "auto compact 대상" : (status.should_warn ? "주의" : "정상"));
    return [
      "### Session Status",
      "",
      `- model: ${status.model || State.model || "-"}`,
      `- context: ${input} input + ${output} reserved / ${windowSize} tokens (${pct}%)`,
      `- remaining: ${remaining} tokens`,
      `- messages: ${status.raw_message_count ?? State.messages.length} raw / ${status.message_count ?? "-"} context`,
      `- compact: ${compactState}`,
      State.compactSummary ? `- compact summary: ${Number(status.compact_summary_tokens_est || 0).toLocaleString()} tokens` : "- compact summary: none",
      status.live_retrieval_note ? `- note: ${status.live_retrieval_note}` : "",
    ].filter(Boolean).join("\n");
  },

  async handleSessionCommand(text) {
    const raw = String(text || "").trim();
    const [cmd] = raw.split(/\s+/, 1);
    if (!["/status", "/clear", "/compact"].includes(cmd)) return false;
    if (State.streaming) {
      toast("스트리밍 중에는 세션 명령을 실행할 수 없습니다.", "error");
      return true;
    }
    if (cmd === "/clear") {
      if (window.KnowledgeCoreBrowser?.active) {
        await window.KnowledgeCoreBrowser.startFreshConversation();
        refreshMetaBar();
        $("#metaStatus").textContent = "새 canonical 대화";
        toast("같은 지식공간에서 새 대화를 시작했습니다.", "ok");
        return true;
      }
      State.session_id = null;
      State.messages = [];
      State.compactSummary = "";
      State.compactedMessageCount = 0;
      State.lastContextStatus = null;
      Chat.clear();
      refreshMetaBar();
      $("#metaStatus").textContent = "새 세션";
      toast("현재 대화 context를 비웠습니다.", "ok");
      return true;
    }
    if (!State.model && !window.KnowledgeCoreBrowser?.active) {
      toast("모델을 선택하세요.", "error");
      return true;
    }
    if (cmd === "/status") {
      if (window.KnowledgeCoreBrowser?.active) {
        const scope = window.KnowledgeCoreBrowser.state();
        const node = Chat.appendMessage("assistant", [
          "### Canonical Conversation Status",
          "",
          `- conversation: ${State.session_id || "none"}`,
          `- space: ${scope?.conversationBadge?.space_name || scope?.selectedSpaceId || "none"}`,
          `- messages: ${State.messages.length}`,
        ].join("\n"));
        attachCopyButtons(node);
        return true;
      }
      try {
        const j = await api("api/code/session/status", {
          method: "POST",
          body: Chat.sessionCommandPayload(),
        });
        State.lastContextStatus = j.status || null;
        const node = Chat.appendMessage("assistant", Chat.formatContextStatus(j.status || {}));
        attachCopyButtons(node);
      } catch (e) {
        toast("/status 실패: " + e.message, "error");
      }
      return true;
    }
    if (cmd === "/compact") {
      if (window.KnowledgeCoreBrowser?.active) {
        try {
          await window.KnowledgeCoreBrowser.compactSession(
            await window.KnowledgeCoreBrowser.ensureConversation()
          );
          const node = Chat.appendMessage("assistant", "### Session Compacted\n\nCanonical summary is ready.");
          attachCopyButtons(node);
        } catch (e) {
          toast("/compact 실패: " + e.message, "error");
        }
        return true;
      }
      try {
        const j = await api("api/code/session/compact", {
          method: "POST",
          body: Chat.sessionCommandPayload(),
        });
        State.compactSummary = j.compact_summary || State.compactSummary || "";
        State.compactedMessageCount = Number(j.compacted_message_count || State.compactedMessageCount || 0);
        State.lastContextStatus = j.status || null;
        await Chat.persistSession({
          metadata: { status: "compacted" },
          updateCurrent: false,
        });
        const count = Number(j.compacted_message_count || 0);
        const node = Chat.appendMessage(
          "assistant",
          `### Session Compacted\n\n- compacted messages: ${count}\n- recent messages kept: ${j.recent_message_count ?? "-"}\n- remaining: ${Number(j.status?.remaining_tokens_est || 0).toLocaleString()} tokens`
        );
        attachCopyButtons(node);
      } catch (e) {
        toast("/compact 실패: " + e.message, "error");
      }
      return true;
    }
    return false;
  },

  refreshChips() {
    const chips = $("#composerChips");
    chips.innerHTML = "";
    State.activeSkills.forEach(sid => {
      const c = document.createElement("span");
      c.className = "chip";
      c.innerHTML = `🛠 ${sid} <span class="x">✕</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.activeSkills.delete(sid);
        Chat.refreshChips();
        Skills.render();
        refreshMetaBar();
      });
      chips.appendChild(c);
    });
    if (State.autoSkills) {
      const c = document.createElement("span");
      c.className = "chip auto";
      c.innerHTML = `Auto Skill ON <span class="x">×</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.autoSkills = false;
        localStorage.setItem("code_assist_v1.autoSkills", "0");
        $("#btnAutoSkills")?.classList.remove("active");
        Chat.refreshChips();
        refreshMetaBar();
      });
      chips.appendChild(c);
    }
    if (State.enableKnowledge) {
      const c = document.createElement("span");
      c.className = "chip kb";
      c.innerHTML = `📚 Second Brain ON <span class="x">✕</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.enableKnowledge = false;
        localStorage.setItem("code_assist_v1.enableKnowledge", "0");
        $("#btnKB").classList.remove("active");
        Chat.refreshChips();
        refreshMetaBar();
      });
      chips.appendChild(c);
    }
    if (State.enableMemory) {
      const c = document.createElement("span");
      c.className = "chip";
      c.innerHTML = `장기기억 ON <span class="x">✕</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.enableMemory = false;
        localStorage.setItem("code_assist_v1.enableMemory", "0");
        $("#btnMemory")?.classList.remove("active");
        Chat.refreshChips();
        refreshMetaBar();
      });
      chips.appendChild(c);
    }
    State.workspaceFiles.forEach(f => {
      const c = document.createElement("span");
      c.className = "chip file";
      c.innerHTML = `📄 ${f.filename} <span class="x">✕</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.workspaceFiles = State.workspaceFiles.filter(x => x !== f);
        Chat.refreshChips();
        refreshMetaBar();
      });
      chips.appendChild(c);
    });
    State.pinnedThoughts.forEach(t => {
      const c = document.createElement("span");
      c.className = "chip thought";
      c.innerHTML = `지식 ${t.title || t.id} <span class="x">✕</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.pinnedThoughts = State.pinnedThoughts.filter(x => x.id !== t.id);
        Chat.refreshChips();
        refreshMetaBar();
      });
      chips.appendChild(c);
    });
    State.pastedImages.forEach(img => {
      const c = document.createElement("span");
      c.className = "chip image";
      c.innerHTML = `<img src="${img.dataUrl}" class="chip-thumb" alt=""> <span class="thumb-label">🖼 ${(img.size / 1024).toFixed(0)}K</span> <span class="x">✕</span>`;
      c.querySelector(".x").addEventListener("click", () => {
        State.pastedImages = State.pastedImages.filter(x => x !== img);
        Chat.refreshChips();
        refreshMetaBar();
      });
      // 썸네일 클릭 → 모달로 원본 보기
      c.querySelector(".chip-thumb").addEventListener("click", () => {
        const w = document.createElement("div");
        w.innerHTML = `<img src="${img.dataUrl}" style="max-width:100%;max-height:70vh;display:block;margin:0 auto;">`;
        Modal.open({ title: img.name, body: w, footButtons: [{ label: "닫기" }] });
      });
      chips.appendChild(c);
    });
  },

  async send() {
    if (window.KnowledgeCoreBrowser) await window.KnowledgeCoreBrowser.ready();
    let text = composerInput.value.trim();
    const hasImages = State.pastedImages.length > 0;
    if (!text && !hasImages) return;
    if (!hasImages && await Chat.handleSessionCommand(text)) {
      composerInput.value = "";
      composerInput.style.height = "auto";
      return;
    }
    const localSlash = Skills.parseSlashCommand?.(text);
    if (localSlash && Skills.isSkillCreatorAlias?.(localSlash.skill)) {
      composerInput.value = "";
      composerInput.style.height = "auto";
      Skills.openCreator?.({
        intent: localSlash.text || "",
        includeConversation: true,
        source: "slash",
      });
      return;
    }
    if (!State.model && !window.KnowledgeCoreBrowser?.active) {
      toast("모델을 선택하세요", "error");
      return;
    }

    const requestSkillIds = new Set(State.activeSkills);
    const slash = Skills.parseSlashCommand?.(text);
    if (slash) {
      try {
        const sid = await Skills.resolveAlias(slash.skill);
        requestSkillIds.add(sid);
        text = slash.text;
        if (!text && !hasImages) {
          toast(`/${slash.skill} 뒤에 요청 내용을 입력하세요.`, "error");
          return;
        }
        toast(`스킬 적용: ${sid}`, "ok");
      } catch (e) {
        toast(`스킬을 찾을 수 없습니다: /${slash.skill}`, "error");
        return;
      }
    }

    if (window.KnowledgeCoreBrowser?.active && hasImages) {
      toast("Canonical 대화에서는 아직 이미지 입력을 지원하지 않습니다.", "error");
      return;
    }

    composerInput.value = "";
    composerInput.style.height = "auto";

    if (window.KnowledgeCoreBrowser?.active) {
      await window.KnowledgeCoreBrowser.enterChat();
      await window.KnowledgeCoreBrowser.ensureConversation();
    }

    // 이미지가 있으면 OpenAI 호환 multimodal 형식으로 전송
    let userContent;
    if (hasImages) {
      const parts = [];
      if (text) parts.push({ type: "text", text });
      for (const img of State.pastedImages) {
        parts.push({ type: "image_url", image_url: { url: img.dataUrl } });
      }
      userContent = parts;
    } else {
      userContent = text;
    }

    const streamSessionId = Chat.ensureSessionId();
    const canonicalBridge = window.KnowledgeCoreBrowser?.active ? window.KnowledgeCoreBrowser : null;
    const canonicalOwnerId = State.currentUser?.id || "";
    const streamMessages = Chat.cloneMessages(State.messages);
    streamMessages.push({ role: "user", content: userContent });
    Chat.setCurrentMessages(streamMessages);
    State.sessionCache.set(streamSessionId, Chat.cloneMessages(streamMessages));

    // UI 표시: 텍스트 + 이미지 썸네일
    const userNode = Chat.appendMessage("user", text || "(이미지)");
    if (hasImages) {
      const gallery = document.createElement("div");
      gallery.className = "msg-image-gallery";
      State.pastedImages.forEach(img => {
        const im = document.createElement("img");
        im.src = img.dataUrl;
        im.alt = img.name;
        im.title = img.name;
        gallery.appendChild(im);
      });
      userNode.appendChild(gallery);
    }
    // 전송 후 보관 비움
    State.pastedImages = [];
    Chat.refreshChips();
    refreshMetaBar();
    await Chat.persistSession({
      sessionId: streamSessionId,
      messages: streamMessages,
      skillsUsed: Array.from(requestSkillIds),
      metadata: { status: "pending" },
      updateCurrent: false,
    });

    let assistantNode = Chat.isCurrentSession(streamSessionId)
      ? Chat.appendMessage("assistant", "")
      : document.createElement("div");
    let buffer = "";
    let streamStatus = { stage: "queued", message: "응답 준비 중" };
    Chat.renderStreamingNode(assistantNode, buffer, streamStatus);
    let streamErrorStatus = null;

    State.streaming = true;
    State.activeStream = { sessionId: streamSessionId };
    $("#btnSend").textContent = "중단";
    $("#btnSend").classList.add("stop");
    $("#metaStatus").textContent = "스트리밍 중…";

    const ac = new AbortController();
    State.abortController = ac;

    // demos_v1 로그인 user_id 추출 (지식베이스 user_id별 폴더 검색용)
    let _chatUid = null;
    try {
      const _u = JSON.parse(sessionStorage.getItem('demos_user') || 'null');
      if (_u && _u.id) _chatUid = _u.id;
    } catch {}

    // 사고 모드 체크박스 상태 (체크 시 모델이 reasoning 토큰 생성)
    const _thinkEl = document.getElementById('thinkToggle');
    const _thinkMode = !!(_thinkEl && _thinkEl.checked);

    const payload = {
      model: State.model,
      messages: streamMessages,
      skills: Array.from(requestSkillIds),
      auto_skills: State.autoSkills,
      auto_skill_max: 3,
      effort: State.effort,
      enable_knowledge: State.enableKnowledge,
      enable_project_context: true,
      project_id: Chat.workspaceProjectId(streamSessionId),
      enable_memory: State.enableMemory,
      session_id: streamSessionId,
      workspace_files: State.workspaceFiles,
      pinned_thoughts: State.pinnedThoughts.map(t => t.id),
      system_prompt: State.systemPromptExtra || "",
      compact_summary: State.compactSummary || "",
      // Preserve a manual first choice, then follow the server's frozen
      // company-API -> local-GGUF -> optional-Codex route.
      disable_fallback: false,
      user_id: _chatUid,
      think_mode: _thinkMode,
    };

    try {
      const r = canonicalBridge
        ? await canonicalBridge.chatStreamResponse({
            conversationId: streamSessionId,
            message: text,
            signal: ac.signal,
          })
        : await authenticatedFetch("api/code/chat/stream", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            signal: ac.signal,
          });
      if (canonicalBridge && (State.currentUser?.id || "") !== canonicalOwnerId) return;
      if (!r.ok || !r.body) {
        streamErrorStatus = r.status;
        const detail = await Chat.readErrorResponse(r);
        throw new Error(detail || `HTTP ${r.status}`);
      }

      const reader = r.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let pending = "";
      let meta = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        pending += decoder.decode(value, { stream: true });

        let nl;
        while ((nl = pending.indexOf("\n\n")) !== -1) {
          const event = pending.slice(0, nl);
          pending = pending.slice(nl + 2);
          if (!event.startsWith("data:")) continue;
          const json = event.slice(5).trim();
          if (!json) continue;
          let obj;
          try { obj = JSON.parse(json); }
          catch { continue; }

          if (obj.error) {
            streamErrorStatus = obj.status || null;
            throw new Error(obj.error);
          }
          if (obj.event === "status") {
            streamStatus = obj;
            if (Chat.isCurrentSession(streamSessionId) && document.contains(assistantNode)) {
              Chat.renderStreamingNode(assistantNode, buffer, streamStatus);
              Chat.scrollToBottom();
            }
            if (Chat.isCurrentSession(streamSessionId) && obj.message) {
              $("#metaStatus").textContent = obj.message;
            }
          }
          if (obj.delta) {
            buffer += obj.delta;
            if (Chat.isCurrentSession(streamSessionId) && document.contains(assistantNode)) {
              Chat.renderStreamingNode(assistantNode, buffer, streamStatus);
              Chat.scrollToBottom();
            }
          }
          if (obj.done) {
            meta = obj;
          }
        }
      }

      if (canonicalBridge && (State.currentUser?.id || "") !== canonicalOwnerId) return;
      // 종료
      const assistantContent = normalizeLatexSymbols(buffer + Chat.truncationNotice(meta || {}));
      streamMessages.push({ role: "assistant", content: assistantContent });
      State.sessionCache.set(streamSessionId, Chat.cloneMessages(streamMessages));
      if (Chat.isCurrentSession(streamSessionId)) {
        Chat.setCurrentMessages(streamMessages);
        if (document.contains(assistantNode)) {
          assistantNode.dataset.raw = assistantContent;
          assistantNode.innerHTML = renderAssistantHtml(assistantContent);
          attachCopyButtons(assistantNode);
        } else {
          Chat.renderMessages(State.messages);
        }
      }

      const completedMetadata = { status: "completed" };
      if (meta?.context_status) completedMetadata.context_status = meta.context_status;
      if (meta?.auto_compacted && meta.compact_summary) {
        completedMetadata.compact_summary = meta.compact_summary;
        completedMetadata.compacted_message_count = Number(meta.compacted_message_count || 0);
      }
      if (Chat.isCurrentSession(streamSessionId)) {
        if (meta?.context_status) State.lastContextStatus = meta.context_status;
        if (meta?.auto_compacted && meta.compact_summary) {
          State.compactSummary = meta.compact_summary;
          State.compactedMessageCount = Number(meta.compacted_message_count || State.compactedMessageCount || 0);
        }
      }

      await Chat.persistSession({
        sessionId: streamSessionId,
        messages: streamMessages,
        skillsUsed: Array.from(requestSkillIds),
        metadata: completedMetadata,
        updateCurrent: false,
      });

      if (meta) {
        const parts = [];
        if (meta.model_used) parts.push(`model=${meta.model_used}`);
        if (meta.elapsed_ms) parts.push(`${meta.elapsed_ms}ms`);
        if (meta.loaded_skills?.length) parts.push(`skills=${meta.loaded_skills.join(",")}`);
        if (meta.auto_skill_matches?.length) {
          if (Chat.isCurrentSession(streamSessionId)) State.lastAutoSkillMatches = meta.auto_skill_matches;
        }
        if (meta.knowledge_files?.length) parts.push(`kb=${meta.knowledge_files.length}`);
        if (meta.project_context_ids?.length) parts.push(`project=${meta.project_context_ids.length}`);
        if (meta.notion_pages?.length) parts.push(`notion=${meta.notion_pages.length}`);
        if (meta.memory_ids?.length) parts.push(`기억=${meta.memory_ids.length}`);
        if (meta.memory_written?.id) parts.push(`기억+${meta.memory_written.status}`);
        if (meta.harness_modes?.length) parts.push(`mode=${meta.harness_modes.join("+")}`);
        if (meta.auto_compacted) parts.push("compact=auto");
        if (meta.gguf_loaded_n_ctx) parts.push(`ctx=${Number(meta.gguf_loaded_n_ctx).toLocaleString()}`);
        if (meta.output_truncated || meta.finish_reason === "length") parts.push("출력한도");
        if (meta.context_status?.remaining_tokens_est !== undefined) {
          parts.push(`ctx≈${Number(meta.context_status.remaining_tokens_est || 0).toLocaleString()}`);
        }
        if (meta.pinned_thoughts?.length) parts.push(`고정=${meta.pinned_thoughts.length}`);
        if (Chat.isCurrentSession(streamSessionId)) $("#metaStatus").textContent = parts.join(" · ");
      } else {
        if (Chat.isCurrentSession(streamSessionId)) $("#metaStatus").textContent = "완료";
      }
      if (!Chat.isCurrentSession(streamSessionId)) toast("백그라운드 세션 응답이 완료되었습니다.", "ok");
    } catch (e) {
      if (canonicalBridge) {
        // A lost/pending stream is not a failed model answer. Do not invent an
        // assistant message or permanently cache optimistic duplicate retries.
        if ((State.currentUser?.id || "") === canonicalOwnerId) {
          const terminal = Boolean(e.terminal);
          const notice = e.name === "AbortError"
            ? "답변 수신을 중단했습니다. 서버 처리는 계속될 수 있으므로 대화 기록을 확인해 주세요."
            : terminal ? `응답 생성 실패: ${e.message}`
            : e.code === "INCOMPLETE_EVENT_STREAM"
              ? "요청의 완료를 아직 확인하지 못했습니다. 잠시 후 같은 질문을 다시 보내 결과를 확인할 수 있습니다."
              : `응답 확인 필요: ${e.message}`;
          toast(notice, terminal || (e.retryable === false && e.name !== "AbortError") ? "error" : "info");
          if (Chat.isCurrentSession(streamSessionId)) $("#metaStatus").textContent = notice;
        }
      } else if (e.name === "AbortError") {
        const errorContent = Chat.failureContent("중단됨", "사용자가 응답 생성을 중단했습니다.", buffer);
        streamMessages.push({ role: "assistant", content: errorContent });
        State.sessionCache.set(streamSessionId, Chat.cloneMessages(streamMessages));
        if (Chat.isCurrentSession(streamSessionId)) {
          Chat.setCurrentMessages(streamMessages);
          if (document.contains(assistantNode)) {
            assistantNode.dataset.raw = errorContent;
            assistantNode.innerHTML = renderAssistantHtml(errorContent);
            attachCopyButtons(assistantNode);
          } else {
            Chat.renderMessages(State.messages);
          }
        }
        await Chat.persistSession({
          sessionId: streamSessionId,
          messages: streamMessages,
          skillsUsed: Array.from(requestSkillIds),
          metadata: { status: "aborted" },
          updateCurrent: false,
        });
        if (Chat.isCurrentSession(streamSessionId)) $("#metaStatus").textContent = "중단됨";
      } else {
        const errorContent = Chat.failureContent("응답 생성 실패", e.message, buffer);
        streamMessages.push({ role: "assistant", content: errorContent });
        State.sessionCache.set(streamSessionId, Chat.cloneMessages(streamMessages));
        if (Chat.isCurrentSession(streamSessionId)) {
          Chat.setCurrentMessages(streamMessages);
          if (document.contains(assistantNode)) {
            assistantNode.dataset.raw = errorContent;
            assistantNode.innerHTML = renderAssistantHtml(errorContent);
            attachCopyButtons(assistantNode);
          } else {
            Chat.renderMessages(State.messages);
          }
        }
        await Chat.persistSession({
          sessionId: streamSessionId,
          messages: streamMessages,
          skillsUsed: Array.from(requestSkillIds),
          metadata: {
            status: "error",
            error_message: String(e.message || e),
            error_status: streamErrorStatus,
          },
          updateCurrent: false,
        });
        toast("스트리밍 오류: " + e.message, "error");
        if (Chat.isCurrentSession(streamSessionId)) $("#metaStatus").textContent = "오류";
      }
    } finally {
      if (canonicalBridge && (State.currentUser?.id || "") === canonicalOwnerId) {
        try {
          const saved = await canonicalBridge.readSessionMessages(streamSessionId);
          if ((State.currentUser?.id || "") === canonicalOwnerId) {
            State.sessionCache.set(streamSessionId, Chat.cloneMessages(saved));
            if (Chat.isCurrentSession(streamSessionId)) {
              Chat.setCurrentMessages(saved);
              Chat.renderMessages(saved);
            }
          }
        } catch (historyError) {
          if ((State.currentUser?.id || "") === canonicalOwnerId) {
            toast("서버 대화 기록을 확인하지 못했습니다. 연결 복구 후 세션을 다시 열어 주세요.", "error");
          }
        }
      }
      if (window.KnowledgeCoreBrowser?.active && currentTab === "sessions") {
        try { await Sessions.render(); }
        catch (sessionError) { console.warn("[chat] canonical session refresh failed", sessionError); }
      }
      State.streaming = false;
      State.abortController = null;
      if (State.activeStream?.sessionId === streamSessionId) State.activeStream = null;
      $("#btnSend").textContent = "전송";
      $("#btnSend").classList.remove("stop");
    }
  },

  stop() {
    if (State.abortController) {
      State.abortController.abort();
    }
  },
};

function cloneSessionValue(value) {
  if (value == null || typeof value !== "object") return value;
  try {
    if (typeof structuredClone === "function") return structuredClone(value);
  } catch {}
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return value;
  }
}

function messageDisplayText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const text = content
      .filter(p => p && typeof p === "object" && p.type === "text")
      .map(p => p.text || "")
      .join("\n")
      .trim();
    const images = content.filter(p => p && typeof p === "object" && p.type === "image_url").length;
    if (text && images) return `${text}\n\n[이미지 ${images}개]`;
    if (text) return text;
    if (images) return `[이미지 ${images}개]`;
  }
  return String(content ?? "");
}

// 마크다운 렌더. HTML 정화 자산이 없으면 평문으로 안전하게 강등한다.
function renderMd(text, options = {}) {
  if (!text) return "";
  if (!options.preserveMath) text = normalizeLatexSymbols(text);
  let html = "";
  if (window.marked) {
    try { html = marked.parse(text); }
    catch { /* fallthrough */ }
  }
  if (!html) html = miniRenderMd(text);
  if (!window.DOMPurify || typeof DOMPurify.sanitize !== "function") {
    return chatEscapeHtml(text).replace(/\n/g, "<br>");
  }
  const safeHtml = DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["audio", "video", "source", "picture", "track", "style", "link"],
    FORBID_ATTR: ["style", "srcset", "sizes", "autoplay", "poster"],
  });
  // Markdown supplied by a document or model must not initiate cross-origin
  // image requests merely because a user opens the answer. Local images remain.
  const template = document.createElement("template");
  template.innerHTML = safeHtml;
  template.content.querySelectorAll("img").forEach(image => {
    const source = image.getAttribute("src") || "";
    let local = false;
    try {
      const url = new URL(source, window.location.href);
      local = (url.origin === window.location.origin
        && (url.protocol === "http:" || url.protocol === "https:"));
    } catch { /* Invalid or empty URL: show alt text below. */ }
    if (!local) image.replaceWith(document.createTextNode(image.getAttribute("alt") || "[이미지]"));
  });
  return template.innerHTML;
}

function chatEscapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderAssistantHtml(text) {
  const host = document.createElement("div");
  host.innerHTML = renderMd(text);
  const canonicalSourceDownload = /^\/api\/knowledge\/v1\/spaces\/[0-9a-f-]{36}\/items\/[0-9a-f-]{36}\/source-artifacts\/[0-9a-f-]{36}\/download$/;
  host.querySelectorAll("a[href]").forEach(anchor => {
    const href = anchor.getAttribute("href") || "";
    if (canonicalSourceDownload.test(href)) {
      anchor.classList.add("assistant-source-download");
      anchor.setAttribute("download", "");
      anchor.setAttribute("title", "참조 원본 파일 다운로드");
    }
  });
  highlightUnicodeMath(host);
  return host.innerHTML;
}

const assistantSourceDownloadHosts = new WeakSet();
const assistantSourceDownloadPath = /^\/api\/knowledge\/v1\/spaces\/[0-9a-f-]{36}\/items\/[0-9a-f-]{36}\/source-artifacts\/[0-9a-f-]{36}\/download$/;

function installAssistantSourceDownloads(host) {
  if (!host || typeof host.addEventListener !== "function" || assistantSourceDownloadHosts.has(host)) return;
  assistantSourceDownloadHosts.add(host);
  host.addEventListener("click", async event => {
    const anchor = event.target?.closest?.("a.assistant-source-download");
    if (!anchor || !host.contains(anchor)) return;
    const href = anchor.getAttribute("href") || "";
    if (!assistantSourceDownloadPath.test(href)) return;
    event.preventDefault();
    if (anchor.dataset.downloadPending === "true") return;
    anchor.dataset.downloadPending = "true";
    try {
      const response = await authenticatedFetch(href);
      if (!response.ok) throw new Error(`원본 다운로드 실패 (${response.status})`);
      const disposition = response.headers.get("Content-Disposition") || "";
      const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
      const plain = /filename="([^"]+)"/.exec(disposition);
      const filename = encoded ? decodeURIComponent(encoded[1]) : (plain?.[1] || "download");
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(objectUrl), 30000);
    } catch (error) {
      toast(error?.message || "원본 다운로드에 실패했습니다.", "error");
    } finally {
      delete anchor.dataset.downloadPending;
    }
  });
}

function highlightUnicodeMath(root) {
  if (!root) return;
  promoteMathParagraphs(root);
  const blocked = new Set(["CODE", "PRE", "A", "SCRIPT", "STYLE", "TEXTAREA", "INPUT", "BUTTON", "KBD", "SAMP"]);
  const mathProbe = new RegExp("[" + UNICODE_MATH_CHARS + "]");
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const value = node.nodeValue || "";
      if (!mathProbe.test(value)) {
        return NodeFilter.FILTER_REJECT;
      }
      let el = node.parentElement;
      while (el && el !== root) {
        if (blocked.has(el.tagName) || el.classList?.contains("math-inline") || el.classList?.contains("math-display")) {
          return NodeFilter.FILTER_REJECT;
        }
        el = el.parentElement;
      }
      return NodeFilter.FILTER_ACCEPT;
    }
  });

  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  nodes.forEach(wrapUnicodeMathTextNode);
}

function promoteMathParagraphs(root) {
  $$("p", root).forEach(p => {
    if (p.querySelector("code, pre, a, img, button, .math-inline, .math-display")) return;
    const text = (p.textContent || "").trim();
    if (isDisplayMathParagraph(text)) {
      p.replaceWith(createMathDisplay(text));
      return;
    }
    if (p.querySelector("br")) promoteMathLinesInsideParagraph(p);
  });
}

function promoteMathLinesInsideParagraph(p) {
  const rawLines = [];
  let current = [];
  Array.from(p.childNodes).forEach(node => {
    if (node.nodeName === "BR") {
      rawLines.push(current);
      current = [];
    } else {
      current.push(node);
    }
  });
  rawLines.push(current);

  const lines = rawLines.map(nodes => ({
    nodes,
    text: nodes.map(node => node.textContent || "").join("").trim(),
  }));
  if (!lines.some(line => isDisplayMathParagraph(line.text))) return;

  const frag = document.createDocumentFragment();
  let mathBuffer = [];
  const flushMath = () => {
    if (!mathBuffer.length) return;
    frag.appendChild(createMathDisplay(mathBuffer.join("\n")));
    mathBuffer = [];
  };

  lines.forEach(line => {
    if (!line.text) return;
    if (isDisplayMathParagraph(line.text)) {
      mathBuffer.push(line.text);
      return;
    }
    flushMath();
    const para = document.createElement("p");
    line.nodes.forEach(node => para.appendChild(node.cloneNode(true)));
    frag.appendChild(para);
  });
  flushMath();
  p.replaceWith(frag);
}

function createMathDisplay(text) {
  const div = document.createElement("div");
  div.className = "math-display";
  div.textContent = formatDisplayEquationText(text);
  return div;
}

function formatDisplayEquationText(text) {
  const lines = String(text || "")
    .split(/\n+/)
    .map(line => line.trim())
    .filter(Boolean);
  return lines.map(formatDisplayEquationLine).join("\n");
}

function formatDisplayEquationLine(line) {
  const s = String(line || "").trim();
  if (!s) return "";
  const parts = splitEquationByTopLevelEquals(s);
  if (parts.length === 2) {
    const left = parts[0].trim();
    const right = parts[1].trim();
    if (shouldBreakSingleEquation(left, right)) return `${left}\n= ${right}`;
    return s;
  }
  if (parts.length <= 1) return s;
  const left = parts[0].trim();
  const firstRight = parts[1].trim();
  const out = shouldBreakSingleEquation(left, firstRight)
    ? [left, "= " + firstRight]
    : [left + " = " + firstRight];
  for (let i = 2; i < parts.length; i++) {
    out.push("= " + parts[i].trim());
  }
  return out.join("\n");
}

function shouldBreakSingleEquation(left, right) {
  const l = String(left || "").trim();
  const r = String(right || "").trim();
  if (!l || !r) return false;
  if (/LeakyReLU|softmax|exp|σ|Σ|∑|∏|∫/.test(r)) return true;
  if ((l.length + r.length) < 42 && !/[Σ∑∏∫]/.test(r)) return false;
  return /[()[\]{}Σ∑∏∫]|softmax|LeakyReLU|exp|σ/.test(r);
}

function splitEquationByTopLevelEquals(text) {
  const s = String(text || "");
  const parts = [];
  let depth = 0;
  let start = 0;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if ("([{".includes(ch)) depth++;
    else if (")]}".includes(ch)) depth = Math.max(0, depth - 1);
    else if (ch === "=" && depth === 0 && s[i - 1] !== "≤" && s[i - 1] !== "≥" && s[i - 1] !== "≠") {
      parts.push(s.slice(start, i));
      start = i + 1;
    }
  }
  parts.push(s.slice(start));
  return parts.map(x => x.trim()).filter(Boolean);
}

function isDisplayMathParagraph(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 600) return false;
  if (/[가-힣]{4,}/.test(s)) return false;
  if (/^(model|mode|skills?|kb|project|file|파일|스킬|지식)\s*=/.test(s)) return false;
  if (!/[=≤≥≈≠∈∉→⇒∑∏∫√∞α-ωΑ-Ω₀-₉ᵢⱼₖₙ⁰-⁹]/.test(s)) return false;
  const mathChars = (s.match(/[=≤≥≈≠∈∉→⇒∑∏∫√∞α-ωΑ-Ω₀-₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ⁰-⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ′×⁄|‖∥{}\[\](),.+\-*/]/g) || []).length;
  return mathChars / Math.max(1, s.length) >= 0.22;
}

function wrapUnicodeMathTextNode(node) {
  const text = node.nodeValue || "";
  const parts = splitUnicodeMathText(text);
  if (parts.length <= 1 && !parts[0]?.math) return;
  const frag = document.createDocumentFragment();
  parts.forEach(part => {
    if (!part.text) return;
    if (part.math) {
      const span = document.createElement("span");
      span.className = "math-inline";
      span.textContent = part.text;
      frag.appendChild(span);
    } else {
      frag.appendChild(document.createTextNode(part.text));
    }
  });
  node.parentNode?.replaceChild(frag, node);
}

function splitUnicodeMathText(text) {
  const s = String(text || "");
  const out = [];
  const re = /([A-Za-zΑ-Ωα-ω∑∏∫√∞][A-Za-zΑ-Ωα-ω0-9₀-₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ⁰-⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ′{}\[\](),.\s+\-*/⁄×|‖∥⟨⟩=≤≥≈≠∈∉→⇒<>]*?(?:=|≤|≥|≈|≠|∈|∉|→|⇒|<|>)[A-Za-zΑ-Ωα-ω0-9₀-₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ⁰-⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ′{}\[\](),.\s+\-*/⁄×|‖∥⟨⟩=≤≥≈≠∈∉→⇒<>]+|[A-Za-zΑ-Ωα-ω][A-Za-zΑ-Ωα-ω0-9₀-₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ⁰-⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ′]*(?:₍[^₎]{1,24}₎|[₀-₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ]{1,8}|[⁰-⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ′]{1,8})|(?:∑|∏|∫|√|∞)[^ㄱ-힣,.;:!?，。؛\n]{0,100})/gu;
  let last = 0;
  let match;
  while ((match = re.exec(s)) !== null) {
    const value = match[0];
    if (!isUnicodeMathHighlightCandidate(value)) continue;
    if (match.index > last) out.push({ text: s.slice(last, match.index), math: false });
    out.push({ text: value, math: true });
    last = match.index + value.length;
  }
  if (last < s.length) out.push({ text: s.slice(last), math: false });
  return out.length ? out : [{ text: s, math: false }];
}

function isUnicodeMathHighlightCandidate(value) {
  const s = String(value || "").trim();
  if (!s || s.length > 180) return false;
  const hasMathGlyph = new RegExp("[" + UNICODE_MATH_CHARS + "]").test(s);
  const isTinyEquation = /^[A-Za-zΑ-Ωα-ω]\s*(?:=|≤|≥|≈|≠|∈|∉|→|⇒|<|>)\s*[A-Za-zΑ-Ωα-ω0-9({\[]/.test(s);
  if (!hasMathGlyph && !isTinyEquation) return false;
  if (/^(model|mode|skills?|kb|project|file|파일|스킬|지식)\s*=/.test(s)) return false;
  if (/^[A-Za-z0-9_-]+\.(py|js|css|html|json|md)$/i.test(s)) return false;
  return true;
}

const UNICODE_MATH_CHARS = "₀-₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ⁰-⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᵀ′α-ωΑ-Ω∑∏∫√∞≤≥≠≈∈∉⊂⊆⊃⊇→⇒×⁄‖∥";

function normalizeLatexSymbols(text) {
  if (!text) return "";
  const fencedParts = String(text).split(/(```[\s\S]*?```)/g);
  return fencedParts.map(part => {
    if (part.startsWith("```")) return normalizeFencedCodeIfMath(part);
    return part.split(/(`[^`\n]*`)/g).map(piece => {
      if (piece.startsWith("`") && piece.endsWith("`")) return normalizeInlineCodeIfMath(piece);
      return normalizeLatexSymbolSegment(piece);
    }).join("");
  }).join("");
}

function normalizeLatexSymbolSegment(text) {
  let out = String(text || "");
  const mathDisplays = [];
  const markdownLiterals = [];
  const stashDisplay = value => {
    const token = `@@MATHDISPLAY${mathDisplays.length}@@`;
    mathDisplays.push(formatMathDisplay(value));
    return token;
  };

  out = convertLatexEnvironments(out);
  out = out.replace(/\$\$([\s\S]*?)\$\$/g, (_, inner) => stashDisplay(convertLatexMath(inner)));
  out = out.replace(/\\\[([\s\S]*?)\\\]/g, (_, inner) => stashDisplay(convertLatexMath(inner)));
  out = out.replace(/\$([^$\n]{1,800})\$/g, (_, inner) => escapeMathHtml(convertLatexMath(inner)));
  out = out.replace(/\\\(([^()\n]{1,800})\\\)/g, (_, inner) => escapeMathHtml(convertLatexMath(inner)));

  // Escaped Markdown punctuation outside math is literal (e.g. rob\_fair.pdf),
  // not a bare LaTeX script. Keep its escape until the Markdown parser runs.
  out = out.replace(/\\([_*])/g, (_, literal) => {
    const token = `@@MDLITERAL${markdownLiterals.length}@@`;
    markdownLiterals.push("\\" + literal);
    return token;
  });

  out = convertLatexStructuredCommands(out);
  out = convertLatexCommands(out);
  out = convertScripts(out);
  out = out.replace(/@@MATHDISPLAY(\d+)@@/g, (_, idx) => mathDisplays[Number(idx)] || "");
  out = out.replace(/@@MDLITERAL(\d+)@@/g, (_, idx) => markdownLiterals[Number(idx)] || "");
  return out;
}

function normalizeFencedCodeIfMath(fence) {
  const match = String(fence || "").match(/^```([^\n`]*)\n?([\s\S]*?)```$/);
  if (!match) return fence;
  const info = (match[1] || "").trim().toLowerCase();
  const body = String(match[2] || "").replace(/\s+$/, "");
  if (!isLikelyMathBlock(body, info)) return fence;
  return formatMathDisplay(convertMathLikePlainText(body));
}

function normalizeInlineCodeIfMath(piece) {
  const raw = String(piece || "");
  const inner = raw.slice(1, -1).trim();
  if (!isLikelyMathInline(inner)) return piece;
  return formatMathInline(convertMathLikePlainText(inner));
}

function convertMathLikePlainText(text) {
  return String(text || "")
    .split(/\n/)
    .map(line => convertLatexMath(line.trim()))
    .join("\n")
    .trim();
}

function isLikelyMathBlock(text, info = "") {
  const lang = String(info || "").trim().toLowerCase();
  if (lang && !["math", "latex", "tex", "equation"].includes(lang)) return false;
  const body = String(text || "").trim();
  if (!body || body.length > 3000) return false;
  if (looksLikeProgrammingCode(body)) return false;
  const lines = body.split(/\n/).map(x => x.trim()).filter(Boolean);
  if (!lines.length || lines.length > 20) return false;
  const mathLines = lines.filter(isLikelyMathExpression).length;
  return mathLines > 0 && mathLines / lines.length >= 0.55;
}

function isLikelyMathInline(text) {
  const s = String(text || "").trim();
  if (!s || s.length > 500 || /\n/.test(s)) return false;
  if (looksLikeProgrammingCode(s)) return false;
  return isLikelyMathExpression(s);
}

function isLikelyMathExpression(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  if (/\\(?:frac|dfrac|tfrac|sqrt|sum|prod|int|alpha|beta|gamma|delta|theta|lambda|mu|sigma|omega|mathbb|mathcal|mathbf|mathrm|operatorname|in|notin|leq|geq|neq|times|cdot|vert|Vert|lvert|rvert|langle|rangle|top)(?![A-Za-z])/.test(s)) return true;
  if (/[∑∏∫√∞≤≥≠≈∈∉⊂⊆⊃⊇α-ωΑ-Ω]/.test(s)) return true;
  if (/[A-Za-z0-9)\]}]\s*(=|≤|≥|≈|≠|∈|∉|→|⇒|<|>)\s*[A-Za-z0-9\\({\[]/.test(s)) return true;
  if (/^[A-Za-zΑ-Ωα-ω]{1,2}_\{?[A-Za-z0-9+\-]+\}?$/.test(s)) return true;
  if (/^[A-Za-zΑ-Ωα-ω]{1,2}\^\{?[^{}]{1,12}\}?$/.test(s)) return true;
  if (/[A-Za-zΑ-Ωα-ω]_\{?[A-Za-z0-9+\-]+\}?/.test(s) && /(?:=|\\in|∈|\\to|→|\^|\\times)/.test(s)) return true;
  if (/(?:^|[^A-Za-z0-9_])R\^\{?[^{}]+/.test(s)) return true;
  if (/^\s*=\s*[-+A-Za-z0-9\\()[\]{}_^.,\s|]+$/.test(s) && /[\\_^=]/.test(s)) return true;
  return false;
}

function looksLikeProgrammingCode(text) {
  const s = String(text || "").trim();
  if (!s) return false;
  if (/^\s*(import|from|def|class|function|const|let|var|return|if|else|for|while|try|catch|SELECT|WITH|UPDATE|INSERT|DELETE)\b/im.test(s)) return true;
  if (/(=>|;\s*$|console\.|print\(|#include|<\/?[A-Za-z][^>]*>|{\s*[A-Za-z_$][\w$]*\s*:)/m.test(s)) return true;
  if (/^\s*(pip|npm|pnpm|yarn|python|node|git|cd|dir|ls|copy|move|set)\s+/im.test(s)) return true;
  return false;
}

function formatMathDisplay(value) {
  const text = formatDisplayEquationText(value);
  if (!text) return "";
  return `\n<div class="math-display">${escapeMathHtml(text).replace(/\n/g, "<br>")}</div>\n`;
}

function formatMathInline(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return `<span class="math-inline">${escapeMathHtml(text)}</span>`;
}

function escapeMathHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function convertLatexEnvironments(text) {
  let out = String(text || "");
  for (let i = 0; i < 5; i++) {
    const before = out;
    out = out.replace(/\\begin\{([A-Za-z]+\*?)\}([\s\S]*?)\\end\{\1\}/g, (_, env, body) => {
      return convertLatexEnvironment(env, body);
    });
    if (out === before) break;
  }
  return out;
}

function convertLatexEnvironment(env, body) {
  const name = String(env || "").replace(/\*$/, "");
  const clean = cleanLatexEnvironmentBody(body);

  if (["equation", "displaymath", "math", "multline"].includes(name)) {
    return "\n" + convertLatexMath(clean) + "\n";
  }
  if (["align", "aligned", "gather", "gathered", "split", "flalign", "alignat", "eqnarray"].includes(name)) {
    return "\n" + convertAlignedEnvironment(clean, name) + "\n";
  }
  if (name === "cases") {
    return convertCasesEnvironment(clean);
  }
  if (["matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix", "array"].includes(name)) {
    return convertMatrixEnvironment(clean, name);
  }
  if (["itemize", "enumerate", "description"].includes(name)) {
    return "\n" + convertListEnvironment(clean, name) + "\n";
  }
  if (["tabular", "tabularx", "longtable"].includes(name)) {
    return "\n" + convertTabularEnvironment(clean) + "\n";
  }
  if (["table", "figure", "center", "quote", "quotation", "proof", "theorem", "lemma", "definition", "remark"].includes(name)) {
    return "\n" + normalizeLatexSymbolSegment(clean) + "\n";
  }
  return "\n" + convertLatexMath(clean) + "\n";
}

function cleanLatexEnvironmentBody(body) {
  return String(body || "")
    .replace(/%.*$/gm, "")
    .replace(/\\label\s*\{[^{}]*\}/g, "")
    .replace(/\\tag\s*\{[^{}]*\}/g, "")
    .replace(/\\(?:notag|nonumber)\b/g, "")
    .trim();
}

function convertAlignedEnvironment(body, name) {
  let clean = body;
  if (name === "alignat") clean = clean.replace(/^\s*\{[^{}]*\}\s*/, "");
  const rows = splitLatexRows(clean)
    .map(row => row.replace(/&\s*/g, "").trim())
    .filter(Boolean)
    .map(row => convertLatexMath(row));
  return rows.join("\n");
}

function convertCasesEnvironment(body) {
  const rows = splitLatexRows(body)
    .map(row => row.split("&").map(part => part.trim()).filter(Boolean))
    .filter(parts => parts.length);
  const converted = rows.map(parts => {
    const value = convertLatexMath(parts[0] || "");
    const condition = convertLatexMath(parts.slice(1).join(" ")).replace(/^if\s+/i, "");
    return condition ? `${value} (${condition})` : value;
  });
  return `{ ${converted.join("; ")} }`;
}

function convertMatrixEnvironment(body, name) {
  let clean = body;
  if (name === "array") clean = clean.replace(/^\s*\{[^{}]*\}\s*/, "");
  const rows = splitLatexRows(clean).map(row => {
    return row
      .split("&")
      .map(cell => convertLatexMath(cell.trim()))
      .filter(Boolean)
      .join(", ");
  }).filter(Boolean);
  const inner = rows.join("; ");
  if (name === "pmatrix") return `(${inner})`;
  if (name === "Bmatrix") return `{${inner}}`;
  if (name === "vmatrix") return `|${inner}|`;
  if (name === "Vmatrix") return `‖${inner}‖`;
  return `[${inner}]`;
}

function convertListEnvironment(body, name) {
  const parts = String(body || "")
    .split(/\\item(?:\s*\[[^\]]*\])?\s*/g)
    .map(item => item.trim())
    .filter(Boolean);
  return parts.map((item, i) => {
    const text = normalizeLatexSymbolSegment(item).replace(/\s+/g, " ").trim();
    return name === "enumerate" ? `${i + 1}. ${text}` : `- ${text}`;
  }).join("\n");
}

function convertTabularEnvironment(body) {
  const clean = String(body || "")
    .replace(/^\s*\{[^{}]*\}\s*/, "")
    .replace(/\\(?:hline|toprule|midrule|bottomrule)\b/g, "")
    .replace(/\\cline\s*\{[^{}]*\}/g, "");
  const rows = splitLatexRows(clean)
    .map(row => row.split("&").map(cell => normalizeLatexSymbolSegment(cell.trim())).filter(Boolean).join(" | "))
    .filter(Boolean);
  return rows.join("\n");
}

function splitLatexRows(body) {
  return String(body || "")
    .split(/\\\\(?:\s*\[[^\]]*\])?/g)
    .map(row => row.trim())
    .filter(Boolean);
}

function convertLatexStructuredCommands(text) {
  let out = String(text || "");
  for (let i = 0; i < 8; i++) {
    const next = convertLatexStructuredCommandPass(out);
    if (next === out) break;
    out = next;
  }
  return out;
}

function convertLatexStructuredCommandPass(text) {
  const s = String(text || "");
  let out = "";
  let i = 0;
  let changed = false;

  while (i < s.length) {
    if (s[i] !== "\\") {
      out += s[i++];
      continue;
    }

    const match = s.slice(i).match(/^\\([A-Za-z]+)(?![A-Za-z])/);
    if (!match) {
      out += s[i++];
      continue;
    }

    const cmd = match[1];
    let pos = i + match[0].length;

    if (["frac", "dfrac", "tfrac"].includes(cmd)) {
      const first = readLatexArgument(s, pos);
      const second = first ? readLatexArgument(s, first.end) : null;
      if (first && second) {
        out += formatFraction(convertLatexMath(first.value), convertLatexMath(second.value));
        i = second.end;
        changed = true;
        continue;
      }
    }

    if (cmd === "binom") {
      const first = readLatexArgument(s, pos);
      const second = first ? readLatexArgument(s, first.end) : null;
      if (first && second) {
        out += `C(${convertLatexMath(first.value)}, ${convertLatexMath(second.value)})`;
        i = second.end;
        changed = true;
        continue;
      }
    }

    if (cmd === "sqrt") {
      const optional = readLatexOptionalArgument(s, pos);
      if (optional) pos = optional.end;
      const value = readLatexArgument(s, pos);
      if (value) {
        out += formatRoot(optional ? convertLatexMath(optional.value) : "", convertLatexMath(value.value));
        i = value.end;
        changed = true;
        continue;
      }
    }

    out += s[i++];
  }

  return changed ? out : text;
}

function readLatexArgument(text, pos) {
  let i = skipLatexSpaces(text, pos);
  if (i >= text.length) return null;
  if (text[i] === "{") return readBalancedLatexGroup(text, i, "{", "}");
  if (text[i] === "\\") {
    const m = text.slice(i).match(/^\\[A-Za-z]+(?![A-Za-z])/);
    if (m) return { value: m[0], end: i + m[0].length };
    if (i + 1 < text.length) return { value: text.slice(i, i + 2), end: i + 2 };
  }
  if (text[i] === "(" || text[i] === "[") {
    const close = text[i] === "(" ? ")" : "]";
    const group = readBalancedLatexGroup(text, i, text[i], close);
    if (group) return group;
  }
  return { value: text[i], end: i + 1 };
}

function readLatexOptionalArgument(text, pos) {
  const i = skipLatexSpaces(text, pos);
  if (text[i] !== "[") return null;
  return readBalancedLatexGroup(text, i, "[", "]");
}

function readBalancedLatexGroup(text, pos, open, close) {
  if (text[pos] !== open) return null;
  let depth = 0;
  for (let i = pos; i < text.length; i++) {
    const ch = text[i];
    if (ch === "\\" && i + 1 < text.length) {
      i++;
      continue;
    }
    if (ch === open) depth++;
    else if (ch === close) {
      depth--;
      if (depth === 0) {
        return { value: text.slice(pos + 1, i), end: i + 1 };
      }
    }
  }
  return null;
}

function skipLatexSpaces(text, pos) {
  let i = pos;
  while (i < text.length && /\s/.test(text[i])) i++;
  return i;
}

function formatRoot(root, inner) {
  const value = String(inner || "").trim();
  const wrapped = value.length === 1 ? value : `(${value})`;
  const r = String(root || "").trim();
  if (!r) return `√${wrapped}`;
  if (r === "3") return `∛${wrapped}`;
  if (r === "4") return `∜${wrapped}`;
  return `√[${r}]${wrapped}`;
}

function convertLatexMath(input) {
  // Stored Markdown may safely encode literal comparisons. Decode these only
  // within explicit math; HTML is escaped again at the display boundary.
  let out = String(input || "").trim()
    .replace(/&lt;|&#0*60;|&#x0*3c;/gi, "<")
    .replace(/&gt;|&#0*62;|&#x0*3e;/gi, ">");
  if (!out) return "";

  out = convertLatexEnvironments(out);
  out = out.replace(/\^\s*\\(?:top|intercal)\b/g, "^{T}");
  out = out.replace(/\\prime\b/g, "′");
  out = out.replace(/([A-Za-z0-9)\]}])'/g, "$1′");
  out = out.replace(/^\s*\/w\s*$/i, "ω");
  out = out.replace(/\/w\b/g, "\\omega");
  out = out.replace(/(^|[^\\])\/([A-Za-z]+)\b/g, (_, pre, cmd) => pre + "\\" + cmd);
  out = out.replace(/\\\|/g, "\\Vert");
  out = out.replace(/\\(?:left|right|big|Big|bigg|Bigg)\b/g, "");
  out = out.replace(/\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle|limits|nolimits)\b/g, "");
  out = unwrapLatexTextCommands(out);

  out = convertLatexStructuredCommands(out);
  out = convertLatexCommands(out);
  out = convertScripts(out);
  out = out.replace(/\\begin\{[^{}]+\}|\\end\{[^{}]+\}/g, "");
  out = out.replace(/[{}]/g, "");
  out = out.replace(/\\,/g, " ").replace(/\\;/g, " ").replace(/\\:/g, " ").replace(/\\!/g, "");
  out = out.replace(/~+/g, " ");
  out = out.replace(/\s+/g, " ").trim();
  return out;
}

function unwrapLatexTextCommands(text) {
  let out = text;
  const names = [
    "text", "mathrm", "mathbf", "mathit", "mathsf", "mathtt", "operatorname",
    "mbox", "textrm", "textbf", "textit", "overline", "underline",
    "mathcal", "mathbb", "mathfrak", "boldsymbol", "pmod", "mod",
  ];
  for (let i = 0; i < 4; i++) {
    names.forEach(name => {
      const re = new RegExp("\\\\" + name + "\\s*\\{([^{}]*)\\}", "g");
      out = out.replace(re, "$1");
    });
    out = out.replace(/\\(?:hat|bar|vec|dot)\s*\{([^{}])\}/g, (_, v) => accentMath(v, RegExp.lastMatch));
  }
  out = out.replace(/\\hat\s+([A-Za-z])/g, "$1̂");
  out = out.replace(/\\bar\s+([A-Za-z])/g, "$1̄");
  out = out.replace(/\\vec\s+([A-Za-z])/g, "$1⃗");
  out = out.replace(/\\dot\s+([A-Za-z])/g, "$1̇");
  return out;
}

function accentMath(value, match) {
  if (match.startsWith("\\hat")) return value + "̂";
  if (match.startsWith("\\bar")) return value + "̄";
  if (match.startsWith("\\vec")) return value + "⃗";
  if (match.startsWith("\\dot")) return value + "̇";
  return value;
}

function convertLatexCommands(text) {
  const map = {
    rightarror: "→", rightarrow: "→", to: "→", longrightarrow: "⟶",
    leftarrow: "←", longleftarrow: "⟵", leftrightarrow: "↔", longleftrightarrow: "⟷",
    Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔",
    mapsto: "↦", uparrow: "↑", downarrow: "↓",
    le: "≤", leq: "≤", ge: "≥", geq: "≥", neq: "≠", ne: "≠",
    approx: "≈", sim: "∼", simeq: "≃", equiv: "≡", propto: "∝",
    times: "×", cdot: "·", bullet: "•", div: "÷", pm: "±", mp: "∓",
    infty: "∞", partial: "∂", nabla: "∇", sum: "∑", prod: "∏", int: "∫",
    in: "∈", notin: "∉", ni: "∋", subseteq: "⊆", subset: "⊂",
    supseteq: "⊇", supset: "⊃", cup: "∪", cap: "∩", setminus: "∖",
    emptyset: "∅", varnothing: "∅", land: "∧", wedge: "∧", lor: "∨", vee: "∨",
    neg: "¬", lnot: "¬", forall: "∀", exists: "∃", therefore: "∴", because: "∵",
    top: "⊤", bot: "⊥", angle: "∠", degree: "°", circ: "∘",
    vert: "|", lvert: "|", rvert: "|", Vert: "‖", lVert: "‖", rVert: "‖",
    mid: "|", lfloor: "⌊", rfloor: "⌋", lceil: "⌈", rceil: "⌉",
    langle: "⟨", rangle: "⟩",
    quad: " ", qquad: " ", thinspace: " ", medspace: " ", thickspace: " ",
    exp: "exp", log: "log", ln: "ln", sin: "sin", cos: "cos", tan: "tan",
    sinh: "sinh", cosh: "cosh", tanh: "tanh", max: "max", min: "min",
    argmax: "argmax", argmin: "argmin", softmax: "softmax",
    alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε",
    zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
    lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", omicron: "ο", pi: "π", varpi: "ϖ",
    rho: "ρ", varrho: "ϱ", sigma: "σ", varsigma: "ς", tau: "τ", upsilon: "υ",
    phi: "φ", varphi: "φ", chi: "χ", psi: "ψ", omega: "ω",
    Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
    Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
    ell: "ℓ", hbar: "ℏ", Re: "ℜ", Im: "ℑ", w: "ω",
  };

  let out = text;
  out = out.replace(/\\([A-Za-z]+)(?![A-Za-z])/g, (m, cmd) => map[cmd] ?? m);
  out = out.replace(/\/([A-Za-z]+)(?![A-Za-z])/g, (m, cmd) => map[cmd] ?? m);
  out = out.replace(/\\([#$%&_{}])/g, "$1");
  return out;
}

function convertScripts(text) {
  let out = text;
  const base = "([A-Za-z0-9α-ωΑ-Ω∑∏∫∞∂∇√∛∜≤≥≠≈+\\-*/⁄)\\]\\}])";
  for (let i = 0; i < 3; i++) {
    out = out.replace(new RegExp(base + "\\^\\{([^{}]+)\\}", "g"), (_, b, script) => b + toSuperscript(script));
    out = out.replace(new RegExp(base + "_\\{([^{}]+)\\}", "g"), (_, b, script) => b + toSubscript(script));
  }
  out = out.replace(new RegExp(base + "\\^([A-Za-z0-9+\\-=()])", "g"), (_, b, script) => b + toSuperscript(script));
  out = out.replace(new RegExp(base + "_([A-Za-z0-9+\\-=()])", "g"), (_, b, script) => b + toSubscript(script));
  return out;
}

function formatFraction(a, b) {
  const key = `${a}/${b}`;
  const common = {
    "1/2": "½", "1/3": "⅓", "2/3": "⅔", "1/4": "¼", "3/4": "¾",
    "1/5": "⅕", "2/5": "⅖", "3/5": "⅗", "4/5": "⅘",
    "1/6": "⅙", "5/6": "⅚", "1/8": "⅛", "3/8": "⅜", "5/8": "⅝", "7/8": "⅞",
  };
  if (common[key]) return common[key];
  if (/^[A-Za-z0-9α-ωΑ-Ω]+$/.test(a) && /^[A-Za-z0-9α-ωΑ-Ω]+$/.test(b)) return `${a}⁄${b}`;
  return `(${a})⁄(${b})`;
}

function toSuperscript(value) {
  const map = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    a: "ᵃ", b: "ᵇ", c: "ᶜ", d: "ᵈ", e: "ᵉ", f: "ᶠ", g: "ᵍ", h: "ʰ",
    i: "ⁱ", j: "ʲ", k: "ᵏ", l: "ˡ", m: "ᵐ", n: "ⁿ", o: "ᵒ", p: "ᵖ",
    r: "ʳ", s: "ˢ", t: "ᵗ", u: "ᵘ", v: "ᵛ", w: "ʷ", x: "ˣ", y: "ʸ", z: "ᶻ",
  };
  return mapChars(value, map, "^");
}

function toSubscript(value) {
  const map = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    a: "ₐ", e: "ₑ", h: "ₕ", i: "ᵢ", j: "ⱼ", k: "ₖ", l: "ₗ", m: "ₘ",
    n: "ₙ", o: "ₒ", p: "ₚ", r: "ᵣ", s: "ₛ", t: "ₜ", u: "ᵤ", v: "ᵥ", x: "ₓ",
  };
  return mapChars(value, map, "_");
}

function mapChars(value, map, fallbackPrefix) {
  const s = String(value || "").trim();
  let out = "";
  for (const ch of s) {
    if (!map[ch]) return fallbackPrefix === "_" ? "₍" + s + "₎" : "⁽" + s + "⁾";
    out += map[ch];
  }
  return out;
}

// marked.js 로드 실패 시 폴백. 이 결과도 renderMd에서 정화한다.
function miniRenderMd(t) {
  const esc = s => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  // 코드블록
  t = t.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) =>
    `<pre><code class="language-${lang}">${esc(code)}</code></pre>`);
  // 인라인 코드
  t = t.replace(/`([^`\n]+)`/g, (m, c) => `<code>${esc(c)}</code>`);
  // 헤딩
  t = t.replace(/^### (.+)$/gm, "<h3>$1</h3>")
       .replace(/^## (.+)$/gm, "<h2>$1</h2>")
       .replace(/^# (.+)$/gm, "<h1>$1</h1>");
  // 굵게/기울임
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
       .replace(/(?<!\*)\*([^*\n]+)\*(?!\*)/g, "<em>$1</em>");
  // 리스트 (간단)
  t = t.replace(/(?:^|\n)(- .+(?:\n- .+)*)/g, m => {
    const items = m.trim().split("\n").map(l => `<li>${l.replace(/^- /, "")}</li>`).join("");
    return `\n<ul>${items}</ul>`;
  });
  // 단락
  t = t.split(/\n{2,}/).map(p =>
    /^<(h\d|ul|ol|pre|blockquote)/.test(p.trim()) ? p : `<p>${p.replace(/\n/g, "<br>")}</p>`
  ).join("\n");
  return t;
}

// 코드블록에 복사 버튼 부착
function attachCopyButtons(root) {
  $$("pre", root).forEach(pre => {
    if (pre.querySelector(".copy-btn")) return;
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "복사";
    btn.onclick = () => {
      const code = pre.querySelector("code")?.textContent || pre.textContent;
      navigator.clipboard.writeText(code).then(
        () => { btn.textContent = "복사됨"; setTimeout(() => btn.textContent = "복사", 1500); },
        () => toast("복사 실패", "error")
      );
    };
    pre.appendChild(btn);
  });
  // highlight.js 적용
  if (window.hljs) {
    $$("pre code", root).forEach(c => { try { hljs.highlightElement(c); } catch {} });
  }
}
