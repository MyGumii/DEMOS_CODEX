/* Skill panel, metadata editor, and slash command helpers. */

const Skills = {
  cache: [],
  sectionOpen: {
    user: localStorage.getItem("code_assist_v1.skills.section.user") !== "0",
    shared: localStorage.getItem("code_assist_v1.skills.section.shared") !== "0",
  },

  scopeOf(skill = {}) {
    return skill.scope === "user" ? "user" : "shared";
  },

  scopeLabel(scope) {
    return scope === "user" ? "사용자" : "공유";
  },

  parseSlashCommand(text = "") {
    const m = String(text || "").match(/^\/([A-Za-z0-9][A-Za-z0-9_-]{1,63})(?:\s+([\s\S]*))?$/);
    if (!m) return null;
    return { skill: m[1], text: (m[2] || "").trim() };
  },

  isSkillCreatorAlias(name = "") {
    const key = String(name || "").trim().toLowerCase().replace(/_/g, "-");
    return ["skill-creator", "skill-creater", "skillcreator", "create-skill", "new-skill"].includes(key);
  },

  async resolveAlias(name) {
    if (Skills.isSkillCreatorAlias(name)) return "skill-creator";
    const params = new URLSearchParams({ q: name });
    const data = await api("api/code/skills/resolve?" + params.toString());
    return data.id;
  },

  async fetchList(query = "") {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    const data = await api("api/code/skills/list?" + params.toString());
    Skills.cache = data.items || [];
    return data;
  },

  async render(query = "") {
    if (currentTab !== "skills") return;
    const body = $("#sidebarBody");
    body.innerHTML = '<div style="padding:14px;color:var(--muted);font-size:12px;">스킬 로딩 중...</div>';
    try {
      const data = await Skills.fetchList(query);
      body.innerHTML = "";

      const header = document.createElement("div");
      header.className = "section-title";
      header.textContent = `Skills ${data.total} · Auto ${State.autoSkills ? "ON" : "OFF"}`;
      body.appendChild(header);

      const hint = document.createElement("div");
      hint.className = "sb-counts";
      hint.textContent = "클릭하면 수동 선택되고, /skill-id 형식으로 이번 요청에만 명시 적용할 수 있습니다.";
      body.appendChild(hint);

      const userSkills = Skills.cache.filter(s => Skills.scopeOf(s) === "user");
      const sharedSkills = Skills.cache.filter(s => Skills.scopeOf(s) === "shared");

      body.appendChild(Skills._section({
        key: "user",
        title: "사용자 스킬",
        description: "이 계정에서 만든 스킬과 공유 스킬을 수정해 만든 사용자 사본입니다.",
        items: userSkills,
        empty: query
          ? "검색어와 일치하는 사용자 스킬이 없습니다."
          : "아직 사용자 스킬이 없습니다. Skill+ 또는 + 버튼으로 만들면 여기에 표시됩니다.",
      }));
      body.appendChild(Skills._section({
        key: "shared",
        title: "공유 스킬",
        description: "모든 계정에서 읽을 수 있는 기본/공용 스킬입니다.",
        items: sharedSkills,
        empty: query
          ? "검색어와 일치하는 공유 스킬이 없습니다."
          : "공유 스킬이 없습니다.",
      }));

      if (!data.total) {
        const el = document.createElement("div");
        el.style.cssText = "padding:14px;color:var(--muted);font-size:12px;";
        el.textContent = "스킬이 없습니다. + 버튼 또는 Skill+로 새 사용자 스킬을 만들 수 있습니다.";
        body.appendChild(el);
      }
    } catch (e) {
      body.innerHTML = `<div style="padding:14px;color:var(--error);font-size:12px;">로드 실패: ${escapeHtml(e.message)}</div>`;
    }
  },

  _section({ key, title, description, items = [], empty = "" }) {
    const wrap = document.createElement("div");
    wrap.className = "skill-section";
    const open = Skills.sectionOpen[key] !== false;
    const activeCount = items.filter(s => State.activeSkills.has(s.id)).length;

    const head = document.createElement("button");
    head.type = "button";
    head.className = "skill-section-head" + (open ? " open" : "");
    head.innerHTML = `
      <span class="skill-section-caret">${open ? "▾" : "▸"}</span>
      <span class="skill-section-title">${escapeHtml(title)}</span>
      <span class="skill-section-count">${items.length}</span>
      ${activeCount ? `<span class="skill-section-active">선택 ${activeCount}</span>` : ""}
    `;
    head.addEventListener("click", () => {
      Skills.sectionOpen[key] = !open;
      localStorage.setItem(`code_assist_v1.skills.section.${key}`, Skills.sectionOpen[key] ? "1" : "0");
      Skills.render($("#sidebarSearch")?.value || "");
    });
    wrap.appendChild(head);

    if (description) {
      const desc = document.createElement("div");
      desc.className = "skill-section-desc";
      desc.hidden = !open;
      desc.textContent = description;
      wrap.appendChild(desc);
    }

    const list = document.createElement("div");
    list.className = "skill-section-body";
    list.hidden = !open;
    if (open) {
      if (items.length) {
        items.forEach(s => list.appendChild(Skills._row(s, State.activeSkills.has(s.id))));
      } else {
        const emptyEl = document.createElement("div");
        emptyEl.className = "skill-section-empty";
        emptyEl.textContent = empty;
        list.appendChild(emptyEl);
      }
    }
    wrap.appendChild(list);
    return wrap;
  },

  _row(s, active) {
    const row = document.createElement("div");
    const scope = Skills.scopeOf(s);
    row.className = `item skill-row ${scope === "user" ? "skill-user" : "skill-shared"}${active ? " selected" : ""}`;
    const tags = (s.tags || []).slice(0, 3).join(", ");
    const implicit = s.allow_implicit_invocation === false ? "명시" : "Auto";
    const desc = s.description || "";
    row.innerHTML = `
      <span class="name" title="${escapeAttr(desc)}" style="display:flex;flex-direction:column;line-height:1.25;">
        <span style="font-size:13px;font-weight:600;color:var(--text);">${escapeHtml(s.name || s.id)}</span>
        <span style="font-size:10px;color:var(--muted);font-family:monospace;">/${escapeHtml(s.id)}</span>
        ${tags ? `<span style="font-size:10px;color:var(--muted);">${escapeHtml(tags)}</span>` : ""}
      </span>
      <span class="badge scope ${scope}">${Skills.scopeLabel(scope)}</span>
      <span class="badge">${implicit}</span>
    `;
    row.addEventListener("click", () => {
      if (State.activeSkills.has(s.id)) State.activeSkills.delete(s.id);
      else State.activeSkills.add(s.id);
      Chat.refreshChips();
      refreshMetaBar();
      Skills.render($("#sidebarSearch").value);
    });
    row.addEventListener("contextmenu", e => {
      e.preventDefault();
      Skills.openView(s.id);
    });
    return row;
  },

  async openView(id) {
    try {
      const data = await api(`api/code/skills/${encodeURIComponent(id)}`);
      const meta = data.meta;

      const body = document.createElement("div");
      body.innerHTML = `
        <div style="font-size:12px;color:var(--muted);margin-bottom:6px;">/${escapeHtml(meta.id)}</div>
        <div style="font-size:13px;margin-bottom:8px;">${escapeHtml(meta.description || "")}</div>
        <div class="sb-detail-meta">
          tags: ${escapeHtml((meta.tags || []).join(", ") || "-")} ·
          auto: ${meta.allow_implicit_invocation === false ? "OFF" : "ON"}
        </div>
      `;
      const ta = document.createElement("textarea");
      ta.value = data.content || "";
      ta.style.minHeight = "300px";
      body.appendChild(ta);

      Modal.open({
        title: `스킬: ${id}`,
        body,
        footButtons: [
          { label: "삭제", danger: true, onClick: async () => {
            if (!confirm(`스킬 ${id}를 삭제할까요?`)) return false;
            try {
              await api("api/code/skills/delete", { method: "POST", body: { id } });
              State.activeSkills.delete(id);
              Chat.refreshChips();
              Skills.render($("#sidebarSearch").value);
              toast("삭제됨", "ok");
            } catch (e) { toast(e.message, "error"); return false; }
          }},
          { label: "저장", primary: true, onClick: async () => {
            try {
              await api("api/code/skills/update", {
                method: "POST",
                body: { id, content: ta.value },
              });
              toast("저장됨", "ok");
              Skills.render($("#sidebarSearch").value);
            } catch (e) { toast(e.message, "error"); return false; }
          }},
          { label: "닫기" },
        ],
      });
    } catch (e) {
      toast(e.message, "error");
    }
  },

  openCreator(options = {}) {
    const intent = String(options.intent || "").trim();
    const hasPinned = (State.pinnedThoughts || []).length > 0;
    const hasConversation = (State.messages || []).length > 0;
    const body = document.createElement("div");
    body.className = "skill-creator-modal";
    body.innerHTML = `
      <div class="skill-creator-intro">
        <strong>Skill Creator</strong>
        <span>반복 가능한 작업 절차를 재사용 가능한 skill로 만듭니다. Second Brain 고정 노드가 없으면 일반 skill-creator처럼 의도와 대화 내용만으로 시작합니다.</span>
      </div>
      <label>무엇을 skill로 만들까요?</label>
      <textarea id="scIntent" class="skill-creator-intent" placeholder="예: 반도체 물류 장애 로그를 분석해서 원인 후보와 확인 명령을 제안하는 skill"></textarea>
      <div class="skill-creator-sources">
        <label><input id="scUseConversation" type="checkbox" ${hasConversation ? "checked" : ""}> 현재 대화 내용 사용</label>
        <label><input id="scUsePinned" type="checkbox" ${hasPinned ? "checked" : ""} ${hasPinned ? "" : "disabled"}> 고정된 Second Brain 노드 사용 (${(State.pinnedThoughts || []).length})</label>
        <label><input id="scImplicit" type="checkbox" checked> Auto Skill 자동 선택 허용</label>
        <label><input id="scActivate" type="checkbox" checked> 생성 후 바로 활성화</label>
      </div>
      <div class="skill-creator-grid">
        <div>
          <label>Skill ID</label>
          <input id="scSkillId" type="text" placeholder="amhs-log-triage">
        </div>
        <div>
          <label>표시 이름</label>
          <input id="scSkillName" type="text" placeholder="AMHS Log Triage">
        </div>
      </div>
      <label>자동 선택 설명</label>
      <input id="scSkillDesc" type="text" placeholder="Use when the user asks to...">
      <div class="skill-creator-grid">
        <div>
          <label>태그</label>
          <input id="scSkillTags" type="text" placeholder="amhs, troubleshooting, logs">
        </div>
        <div>
          <label>필요 도구</label>
          <input id="scSkillTools" type="text" placeholder="python, shell, rg">
        </div>
      </div>
      <div class="skill-creator-grid">
        <div>
          <label>도메인</label>
          <input id="scSkillDomains" type="text" placeholder="semiconductor">
        </div>
        <div>
          <label>서비스</label>
          <input id="scSkillServices" type="text" placeholder="amhs, dispatch">
        </div>
      </div>
      <label>사용할 때</label>
      <input id="scWhenUse" type="text" placeholder="이 skill을 자동으로 선택해야 하는 상황">
      <label>사용하지 않을 때</label>
      <input id="scWhenNotUse" type="text" placeholder="범위 밖 상황">
      <label>SKILL.md 본문</label>
      <textarea id="scSkillBody" class="skill-creator-body" placeholder="# Workflow&#10;&#10;1. Inspect context.&#10;2. Follow the workflow.&#10;3. Verify the result."></textarea>
      <div class="skill-creator-note" id="scSourceNote"></div>
    `;
    Modal.open({
      title: "Skill Creator",
      body,
      footButtons: [
        {
          label: "초안 만들기",
          onClick: () => {
            Skills.populateCreatorDraft();
            return false;
          },
        },
        {
          label: "생성",
          primary: true,
          onClick: async () => {
            return await Skills.saveCreatorDraft();
          },
        },
        { label: "닫기" },
      ],
    });
    $("#scIntent").value = intent;
    Skills.populateCreatorDraft({ preserveBody: false });
  },

  async populateCreatorDraft(options = {}) {
    const intent = ($("#scIntent")?.value || "").trim();
    const useConversation = !!$("#scUseConversation")?.checked;
    const usePinned = !!$("#scUsePinned")?.checked;
    const source = await Skills.collectCreatorSource({ useConversation, usePinned });
    const draft = await Skills.buildSkillDraft({ intent, source });

    $("#scSkillId").value = draft.id;
    $("#scSkillName").value = draft.name;
    $("#scSkillDesc").value = draft.description;
    $("#scSkillTags").value = draft.tags.join(", ");
    $("#scSkillTools").value = draft.tools.join(", ");
    $("#scSkillDomains").value = draft.domains.join(", ");
    $("#scSkillServices").value = draft.services.join(", ");
    $("#scWhenUse").value = draft.whenToUse;
    $("#scWhenNotUse").value = draft.whenNotToUse;
    if (!options.preserveBody || !$("#scSkillBody").value.trim()) {
      $("#scSkillBody").value = draft.body;
    }
    const note = $("#scSourceNote");
    if (note) {
      note.textContent = `초안 재료: 대화 ${source.conversationCount}개 메시지, Second Brain ${source.pinned.length}개 노드`;
    }
  },

  async saveCreatorDraft() {
    const id = ($("#scSkillId")?.value || "").trim().toLowerCase();
    const name = ($("#scSkillName")?.value || "").trim() || id;
    const description = ($("#scSkillDesc")?.value || "").trim();
    const bodyText = ($("#scSkillBody")?.value || "").trim();
    if (!id) {
      toast("Skill ID를 입력하세요.", "error");
      return false;
    }
    if (!description) {
      toast("자동 선택 설명을 입력하세요.", "error");
      return false;
    }
    if (!bodyText) {
      toast("SKILL.md 본문을 입력하세요.", "error");
      return false;
    }
    try {
      await api("api/code/skills/create", {
        method: "POST",
        body: {
          id,
          name,
          description,
          tags: _splitCsv($("#scSkillTags")?.value || ""),
          when_to_use: ($("#scWhenUse")?.value || "").trim(),
          when_not_to_use: ($("#scWhenNotUse")?.value || "").trim(),
          domains: _splitCsv($("#scSkillDomains")?.value || ""),
          services: _splitCsv($("#scSkillServices")?.value || ""),
          tool_dependencies: _splitCsv($("#scSkillTools")?.value || ""),
          allow_implicit_invocation: !!$("#scImplicit")?.checked,
          body: bodyText,
        },
      });
      if ($("#scActivate")?.checked) {
        State.activeSkills.add(id);
        Chat.refreshChips();
        refreshMetaBar();
      }
      await Skills.render($("#sidebarSearch")?.value || "");
      toast(`skill 생성 완료: /${id}`, "ok");
      return true;
    } catch (e) {
      toast("skill 생성 실패: " + e.message, "error");
      return false;
    }
  },

  async collectCreatorSource({ useConversation = true, usePinned = true } = {}) {
    const conversation = useConversation ? Skills.conversationSourceText() : "";
    const pinned = usePinned ? await Skills.pinnedThoughtSource() : [];
    return {
      conversation,
      conversationCount: useConversation ? Math.min((State.messages || []).length, 12) : 0,
      pinned,
    };
  },

  conversationSourceText() {
    const rows = (State.messages || []).slice(-12).map(msg => {
      const role = msg.role || "message";
      const text = Skills.messageToPlainText(msg.content).slice(0, 1400);
      return `### ${role}\n${text}`;
    }).filter(Boolean);
    return rows.join("\n\n").slice(0, 7000);
  },

  messageToPlainText(content) {
    if (Array.isArray(content)) {
      return content.map(part => {
        if (part?.type === "text") return part.text || "";
        if (part?.type === "image_url") return "[image]";
        return "";
      }).filter(Boolean).join("\n");
    }
    if (content == null) return "";
    return String(content);
  },

  async pinnedThoughtSource() {
    const pinned = State.pinnedThoughts || [];
    const out = [];
    for (const item of pinned.slice(0, 8)) {
      try {
        const data = await api(`api/code/knowledge/thought/${encodeURIComponent(item.id)}${typeof _kbUidQs === "function" ? _kbUidQs() : ""}`);
        const meta = data.meta || {};
        out.push({
          id: item.id,
          title: meta.title || item.title || item.id,
          type: meta.memory_type || "",
          domain: meta.domain || "",
          service: meta.service || "",
          topics: Array.isArray(meta.topics) ? meta.topics : [],
          content: data.content || meta.content_preview || "",
        });
      } catch {
        out.push({ id: item.id, title: item.title || item.id, content: "" });
      }
    }
    return out;
  },

  async buildSkillDraft({ intent = "", source = {} } = {}) {
    const sourceText = [
      intent,
      source.conversation || "",
      ...(source.pinned || []).map(t => `${t.title}\n${t.content || ""}`),
    ].join("\n\n").trim();
    const title = Skills.titleFromText(intent || sourceText) || "Reusable Workflow";
    const id = await Skills.uniqueSkillId(Skills.slugify(title || "custom-skill"));
    const name = Skills.titleCaseFromId(id, title);
    const tags = Skills.extractTags(sourceText);
    const domains = Skills.extractKnownValues(source.pinned || [], "domain");
    const services = Skills.extractKnownValues(source.pinned || [], "service");
    const tools = Skills.extractTools(sourceText);
    const shortIntent = intent || title;
    const description = `Use when the user wants to ${Skills.lowerFirst(shortIntent)}. Trigger for requests that need this reusable workflow, its domain rules, or its verification steps.`;
    const whenToUse = `사용자가 "${title}" 유형의 반복 작업, 절차화된 분석, 산출물 작성, 검증 흐름을 요청할 때 사용합니다.`;
    const whenNotToUse = "단순 일반 질문, 일회성 설명, 또는 이 workflow의 입력/도메인과 무관한 작업에는 사용하지 않습니다.";
    const pinnedBlock = (source.pinned || []).length
      ? (source.pinned || []).map(t => `- ${t.title || t.id} (${t.id})${t.type ? ` / ${t.type}` : ""}`).join("\n")
      : "- No Second Brain nodes were pinned when this skill was drafted.";
    const conversationBlock = source.conversation
      ? Skills.indentBlock(source.conversation.slice(0, 5000))
      : "No conversation context was included. Ask the user for the goal, trigger conditions, inputs, and expected output before finalizing the workflow.";
    const body = `# ${name}

## Purpose

Use this skill to turn a recurring task into a reliable workflow. The current target is:

${shortIntent || "Define the user's recurring task and convert it into executable guidance."}

## When To Use

- Use when the user request matches the trigger description in the frontmatter.
- Use when the task needs repeatable steps, domain rules, validation, or a consistent output format.
- If the task depends on Second Brain content, inspect the pinned nodes or ask the user which contents should be authoritative.

## Inputs

- User request and constraints
- Relevant files or workspace context
- Current conversation context when the skill was invoked from a chat
- Second Brain nodes pinned into the current chat context

## Workflow

1. Clarify the target outcome only if it is missing or ambiguous.
2. Identify the required context: files, logs, specs, Second Brain nodes, or prior conversation.
3. Extract stable rules and reusable procedure from that context.
4. Apply the procedure step by step, keeping assumptions explicit.
5. Produce a concrete result the user can run, review, or apply.
6. Verify the result with the narrowest meaningful check available.

## Output Format

- Start with the direct answer or produced artifact.
- Include file paths, commands, or changed behavior when relevant.
- End with only the next required action or verification gap.

## Draft Source

### Second Brain Nodes

${pinnedBlock}

### Conversation Notes

${conversationBlock}
`;
    return {
      id,
      name,
      description,
      tags,
      tools,
      domains,
      services,
      whenToUse,
      whenNotToUse,
      body,
    };
  },

  titleFromText(text = "") {
    const clean = String(text || "")
      .replace(/^\/skill-creat[oe]r\s*/i, "")
      .split(/\n+/)
      .map(s => s.replace(/^[#*\-\d.\s]+/, "").trim())
      .find(Boolean) || "";
    return clean.slice(0, 80);
  },

  slugify(text = "") {
    let raw = String(text || "").toLowerCase();
    const koMap = [
      ["반도체", "semiconductor"],
      ["물류", "logistics"],
      ["장애", "troubleshooting"],
      ["로그", "log"],
      ["분석", "analysis"],
      ["스펙", "spec"],
      ["명세", "spec"],
      ["문서", "docs"],
      ["검토", "review"],
      ["코드", "code"],
      ["개발", "development"],
      ["테스트", "testing"],
      ["세컨브레인", "second-brain"],
      ["지식", "knowledge"],
      ["스킬", "skill"],
      ["생성", "create"],
      ["자동", "auto"],
    ];
    koMap.forEach(([ko, en]) => {
      raw = raw.replaceAll(ko, ` ${en} `);
    });
    const ascii = raw
      .normalize("NFKD")
      .replace(/[^\w\s-]/g, " ")
      .trim()
      .toLowerCase()
      .replace(/[_\s]+/g, "-")
      .replace(/-+/g, "-")
      .replace(/^-|-$/g, "");
    return ascii && /^[a-z0-9]/.test(ascii) ? ascii.slice(0, 50) : "custom-skill";
  },

  async uniqueSkillId(base) {
    const normalized = Skills.slugify(base || "custom-skill");
    try {
      if (!Skills.cache.length) await Skills.fetchList("");
    } catch {}
    const existing = new Set((Skills.cache || []).map(s => s.id));
    if (!existing.has(normalized)) return normalized;
    for (let i = 2; i < 100; i++) {
      const next = `${normalized}-${i}`;
      if (!existing.has(next)) return next;
    }
    return `${normalized}-${Date.now().toString(36)}`;
  },

  titleCaseFromId(id, fallback = "") {
    const fromFallback = String(fallback || "").trim();
    if (fromFallback && /[A-Za-z가-힣]/.test(fromFallback)) return fromFallback.slice(0, 60);
    return String(id || "custom-skill").split("-").map(s => s ? s[0].toUpperCase() + s.slice(1) : s).join(" ");
  },

  lowerFirst(text = "") {
    const s = String(text || "").trim();
    return s ? s[0].toLowerCase() + s.slice(1) : "create or apply a reusable workflow";
  },

  extractTags(text = "") {
    const lower = String(text || "").toLowerCase();
    const candidates = [
      "python", "javascript", "frontend", "backend", "fastapi", "flask", "react",
      "spec", "second-brain", "rag", "amhs", "semiconductor", "logistics",
      "troubleshooting", "testing", "docs", "workflow",
    ];
    const koHints = [
      ["반도체", "semiconductor"],
      ["물류", "logistics"],
      ["장애", "troubleshooting"],
      ["로그", "troubleshooting"],
      ["스펙", "spec"],
      ["명세", "spec"],
      ["문서", "docs"],
      ["세컨브레인", "second-brain"],
      ["지식", "second-brain"],
      ["테스트", "testing"],
    ].filter(([ko]) => lower.includes(ko)).map(([, tag]) => tag);
    const hits = candidates.filter(t => lower.includes(t.replace("-", " ")) || lower.includes(t)).concat(koHints);
    return [...new Set(hits.length ? hits : ["workflow"])].slice(0, 6);
  },

  extractTools(text = "") {
    const lower = String(text || "").toLowerCase();
    const tools = ["python", "shell", "rg", "node", "npm", "pytest", "playwright"].filter(t => lower.includes(t));
    return [...new Set(tools.length ? tools : ["shell"])].slice(0, 5);
  },

  extractKnownValues(items, key) {
    return [...new Set((items || []).map(it => String(it[key] || "").trim()).filter(Boolean))].slice(0, 6);
  },

  indentBlock(text = "") {
    return String(text || "").split("\n").map(line => line ? `> ${line}` : ">").join("\n");
  },

  openCreate() {
    const body = document.createElement("div");
    body.innerHTML = `
      <label>스킬 ID</label>
      <input id="newSkillId" type="text" placeholder="my-coding-skill">
      <label>이름</label>
      <input id="newSkillName" type="text" placeholder="My Coding Skill">
      <label>설명</label>
      <input id="newSkillDesc" type="text" placeholder="언제 이 스킬을 써야 하는지 명확히 적으세요.">
      <label>태그, 쉼표로 구분</label>
      <input id="newSkillTags" type="text" placeholder="python, fastapi, backend">
      <label>사용할 때</label>
      <input id="newSkillWhenUse" type="text" placeholder="FastAPI 라우터, Pydantic schema, backend API 작업">
      <label>사용하지 않을 때</label>
      <input id="newSkillWhenNotUse" type="text" placeholder="frontend-only UI 작업">
      <label>도메인 / 서비스, 쉼표로 구분</label>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
        <input id="newSkillDomains" type="text" placeholder="semiconductor, logistics">
        <input id="newSkillServices" type="text" placeholder="amhs, dispatch">
      </div>
      <label>필요 도구, 쉼표로 구분</label>
      <input id="newSkillTools" type="text" placeholder="python, shell, playwright">
      <label style="display:flex;align-items:center;gap:8px;margin-top:10px;">
        <input id="newSkillImplicit" type="checkbox" checked>
        자동 선택 허용
      </label>
      <label>본문</label>
      <textarea id="newSkillBody" placeholder="# Workflow&#10;&#10;1. Inspect context.&#10;2. Apply this skill's rules."></textarea>
    `;
    Modal.open({
      title: "새 스킬 만들기",
      body,
      footButtons: [
        { label: "취소" },
        { label: "생성", primary: true, onClick: async () => {
          const id = $("#newSkillId").value.trim().toLowerCase();
          const name = $("#newSkillName").value.trim() || id;
          const description = $("#newSkillDesc").value.trim();
          const tags = _splitCsv($("#newSkillTags").value);
          const bodyText = $("#newSkillBody").value;
          if (!id) { toast("ID 필수", "error"); return false; }
          try {
            await api("api/code/skills/create", {
              method: "POST",
              body: {
                id,
                name,
                description,
                tags,
                when_to_use: $("#newSkillWhenUse").value.trim(),
                when_not_to_use: $("#newSkillWhenNotUse").value.trim(),
                domains: _splitCsv($("#newSkillDomains").value),
                services: _splitCsv($("#newSkillServices").value),
                tool_dependencies: _splitCsv($("#newSkillTools").value),
                allow_implicit_invocation: $("#newSkillImplicit").checked,
                body: bodyText,
              },
            });
            toast("스킬 생성됨", "ok");
            Skills.render();
          } catch (e) { toast(e.message, "error"); return false; }
        }},
      ],
    });
  },
};

function _splitCsv(value) {
  return String(value || "").split(",").map(s => s.trim()).filter(Boolean);
}
