/* Second Brain graph view for code_assist_v1. No build step required. */

(function () {
  const NS = "http://www.w3.org/2000/svg";

  const Graph = {
    built: false,
    data: null,
    selectedId: null,
    zoom: 1,
    pan: { x: 0, y: 0 },
    nodeMap: new Map(),
    positionCache: new Map(),
    draggingNode: null,
    draggingStage: false,
    lastPointer: null,
    provider: null,
    readOnly: false,
    scopeLabel: "",
    loadToken: 0,

    useProvider(provider, { readOnly = false, scopeLabel = "" } = {}) {
      if (provider !== null && typeof provider !== "function") {
        throw new TypeError("Graph provider must be a function or null.");
      }
      Graph.provider = provider;
      Graph.readOnly = Boolean(readOnly);
      Graph.scopeLabel = String(scopeLabel || "");
      Graph.data = null;
      Graph.selectedId = null;
      Graph.loadToken += 1;
      const svg = $("#graphSvg");
      if (svg) svg.innerHTML = "";
      const detail = $("#graphDetail");
      if (detail) detail.innerHTML = `<h2>${escapeHtml(Graph.scopeLabel || "지식")} 그래프 로딩 중</h2>`;
      Graph.updateModeControls();
    },

    updateModeControls() {
      const add = $("#graphAddBtn");
      const convert = $("#graphConvertBtn");
      if (add) add.hidden = Graph.readOnly;
      if (convert) convert.hidden = true;
    },

    render() {
      const root = $("#graphView");
      if (!root) return;
      if (!Graph.built) Graph.build(root);
      Graph.updateModeControls();
      if (!Graph.data) Graph.load();
      else Graph.draw();
    },

    build(root) {
      root.innerHTML = `
        <div class="graph-shell">
          <div class="graph-toolbar">
            <input class="graph-search" id="graphSearch" type="text" placeholder="markdown 파일명, 제목, 태그 검색">
            <select id="graphViewMode" title="그래프 보기 방식">
              <option value="wiki">Wiki Graph</option>
            </select>
            <select id="graphDepth" title="선택 노드 주변 확장 범위">
              <option value="all">전체</option>
              <option value="1">1-hop</option>
              <option value="2">2-hop</option>
            </select>
            <div class="graph-type-filter" id="graphMemoryPicker" hidden>
              <button type="button" class="graph-type-trigger" id="graphMemoryBtn" title="지식 유형">
                <span class="graph-type-dot all" id="graphMemoryDot"></span>
                <span id="graphMemoryLabel">전체 유형</span>
              </button>
              <div class="graph-type-menu" id="graphMemoryMenu" hidden>
                <button type="button" class="graph-type-option" data-value=""><span class="graph-type-dot all"></span>전체 유형</button>
                <button type="button" class="graph-type-option" data-value="semantic"><span class="graph-type-dot semantic"></span>사실/정의</button>
                <button type="button" class="graph-type-option" data-value="procedural"><span class="graph-type-dot procedural"></span>절차/규칙</button>
                <button type="button" class="graph-type-option" data-value="episodic"><span class="graph-type-dot episodic"></span>사건/사례</button>
                <button type="button" class="graph-type-option" data-value="reflective"><span class="graph-type-dot reflective"></span>회고/교훈</button>
                <button type="button" class="graph-type-option" data-value="thesis"><span class="graph-type-dot thesis"></span>핵심 주장</button>
                <button type="button" class="graph-type-option" data-value="topic"><span class="graph-type-dot topic"></span>주제</button>
              </div>
              <input id="graphMemory" type="hidden" value="">
            </div>
            <input id="graphService" type="text" placeholder="서비스" hidden>
            <input id="graphTopic" type="text" placeholder="태그">
            <select id="graphEdge" title="관계 유형">
              <option value="">전체 관계</option>
              <option value="references">참조</option>
              <option value="supports">근거</option>
              <option value="extends">확장</option>
              <option value="contradicts">충돌</option>
              <option value="requires">필요</option>
              <option value="source-of">출처</option>
              <option value="supersedes">대체</option>
              <option value="related">관련</option>
            </select>
            <button id="graphAddBtn">수동 추가</button>
            <button id="graphConvertBtn" title="현재 구조에서는 markdown 업로드가 곧 Wiki import입니다" hidden>Markdown 마이그레이션</button>
            <button id="graphRefreshBtn">새로고침</button>
            <button id="graphCloseBtn">채팅</button>
          </div>
          <div class="graph-main">
            <div class="graph-stage" id="graphStage">
              <svg class="graph-svg" id="graphSvg" role="img" aria-label="LLM Wiki 그래프"></svg>
              <div class="graph-legend">
                <span><i style="background:#58a6ff"></i>Wiki page</span>
                <span><i style="background:#6e7681"></i>Source markdown</span>
                <span><i style="background:#8b949e"></i>Graph edge</span>
              </div>
            </div>
            <aside class="graph-detail" id="graphDetail">
              <h2>LLM Wiki 그래프</h2>
              <p class="muted">노드는 markdown 파일이고, edge는 별도 graph cache에 저장된 연결 정보입니다.</p>
            </aside>
          </div>
        </div>
      `;

      const debouncedLoad = debounce(() => Graph.load(), 280);
      ["graphSearch", "graphService", "graphTopic"].forEach(id => {
        $("#" + id)?.addEventListener("input", debouncedLoad);
      });
      ["graphViewMode", "graphDepth", "graphMemory", "graphEdge"].forEach(id => {
        $("#" + id)?.addEventListener("change", () => Graph.load());
      });
      $("#graphMemoryBtn")?.addEventListener("click", e => {
        e.stopPropagation();
        const menu = $("#graphMemoryMenu");
        if (menu) menu.hidden = !menu.hidden;
      });
      $$(".graph-type-option", root).forEach(btn => {
        btn.addEventListener("click", e => {
          e.stopPropagation();
          const value = btn.dataset.value || "";
          const input = $("#graphMemory");
          if (input) input.value = value;
          Graph.updateMemoryPicker(value);
          const menu = $("#graphMemoryMenu");
          if (menu) menu.hidden = true;
          Graph.load();
        });
      });
      document.addEventListener("click", e => {
        const picker = $("#graphMemoryPicker");
        const menu = $("#graphMemoryMenu");
        if (picker && menu && !picker.contains(e.target)) menu.hidden = true;
      });
      $("#graphRefreshBtn")?.addEventListener("click", () => Graph.load());
      $("#graphCloseBtn")?.addEventListener("click", () => window.setMainView?.("chat"));
      $("#graphAddBtn")?.addEventListener("click", () => Graph.openCreateModal());
      $("#graphConvertBtn")?.addEventListener("click", () => Graph.convertDocs());

      const svg = $("#graphSvg");
      svg.addEventListener("wheel", e => {
        e.preventDefault();
        const oldZoom = Graph.zoom;
        Graph.zoom = clamp(Graph.zoom * (e.deltaY > 0 ? 0.9 : 1.1), 0.25, 3.5);
        const rect = svg.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        Graph.pan.x = mx - (mx - Graph.pan.x) * (Graph.zoom / oldZoom);
        Graph.pan.y = my - (my - Graph.pan.y) * (Graph.zoom / oldZoom);
        Graph.applyTransform();
      }, { passive: false });
      svg.addEventListener("mousedown", e => {
        if (e.target === svg) {
          Graph.draggingStage = true;
          Graph.lastPointer = { x: e.clientX, y: e.clientY };
          svg.classList.add("dragging");
        }
      });
      document.addEventListener("mousemove", Graph.onPointerMove);
      document.addEventListener("mouseup", Graph.onPointerUp);
      window.addEventListener("resize", debounce(() => Graph.draw(), 120));

      Graph.built = true;
      Graph.updateModeControls();
    },

    updateMemoryPicker(value) {
      const dot = $("#graphMemoryDot");
      const label = $("#graphMemoryLabel");
      if (dot) dot.className = "graph-type-dot " + (value || "all");
      if (label) label.textContent = memoryLabel(value || "");
    },

    async load() {
      const requestToken = ++Graph.loadToken;
      const requestProvider = Graph.provider;
      if (requestProvider) {
        try {
          const data = await requestProvider();
          if (requestToken !== Graph.loadToken || requestProvider !== Graph.provider) return;
          Graph.data = data;
          Graph.draw();
        } catch (e) {
          if (requestToken !== Graph.loadToken || requestProvider !== Graph.provider) return;
          $("#graphDetail").innerHTML = `<h2>그래프 로드 실패</h2><p class="muted">${escapeHtml(e.message)}</p>`;
        }
        return;
      }
      const params = new URLSearchParams();
      const uid = graphUid();
      if (uid) params.set("user_id", uid);
      const q = $("#graphSearch")?.value.trim();
      const memory = $("#graphMemory")?.value || "";
      const service = $("#graphService")?.value.trim();
      const topic = $("#graphTopic")?.value.trim();
      const edge = $("#graphEdge")?.value || "";
      const depth = $("#graphDepth")?.value || "all";
      const view = $("#graphViewMode")?.value || "wiki";
      if (q) params.set("q", q);
      if (view) params.set("view", view);
      if (memory) params.set("memory_type", memory);
      if (service) params.set("service", service);
      if (topic) params.set("topic", topic);
      if (edge) params.set("edge_type", edge);
      if (Graph.selectedId && depth !== "all") {
        params.set("focus", Graph.selectedId);
        params.set("depth", depth);
      }
      try {
        const data = await api("api/code/knowledge/graph?" + params.toString());
        if (requestToken !== Graph.loadToken || requestProvider !== Graph.provider) return;
        Graph.data = data;
        Graph.draw();
      } catch (e) {
        if (requestToken !== Graph.loadToken || requestProvider !== Graph.provider) return;
        $("#graphDetail").innerHTML = `<h2>그래프 로드 실패</h2><p class="muted">${escapeHtml(e.message)}</p>`;
      }
    },

    draw() {
      const svg = $("#graphSvg");
      const stage = $("#graphStage");
      if (!svg || !stage || !Graph.data) return;
      svg.innerHTML = "";
      Graph.nodeMap.clear();

      const nodes = (Graph.data.nodes || []).map(n => ({ ...n.data }));
      const edges = (Graph.data.edges || []).map(e => ({ ...e.data }));
      const w = Math.max(720, stage.clientWidth || 900);
      const h = Math.max(520, stage.clientHeight || 620);
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`);

      if (!nodes.length) {
        svg.innerHTML = "";
        if (Graph.readOnly) {
          $("#graphDetail").innerHTML = `
            <h2>${escapeHtml(Graph.scopeLabel || "조직 지식")} 그래프가 비어 있습니다</h2>
            <p class="muted">이 조직 지식공간에는 아직 게시된 지식이 없습니다.</p>
          `;
        } else {
          $("#graphDetail").innerHTML = `
            <h2>Wiki graph가 비어 있습니다</h2>
            <p class="muted">markdown/text 파일을 업로드하면 source 문서가 저장되고 graph edge가 생성됩니다.</p>
            <div class="actions">
              <button class="primary" id="graphEmptyAdd">Wiki page 추가</button>
            </div>
          `;
          $("#graphEmptyAdd")?.addEventListener("click", () => Graph.openCreateModal());
        }
        return;
      }

      seedPositions(nodes, edges, Graph.nodeMap, Graph.positionCache, w, h);
      layout(nodes, edges, Graph.nodeMap, w, h);
      syncPositionCache(nodes, Graph.nodeMap, Graph.positionCache);

      const g = document.createElementNS(NS, "g");
      g.setAttribute("id", "graphWorld");
      svg.appendChild(g);

      const edgeLayer = document.createElementNS(NS, "g");
      const nodeLayer = document.createElementNS(NS, "g");
      g.appendChild(edgeLayer);
      g.appendChild(nodeLayer);

      edges.forEach(edge => {
        const line = document.createElementNS(NS, "line");
        line.classList.add("graph-edge");
        line.dataset.source = edge.source;
        line.dataset.target = edge.target;
        line.dataset.edgeType = edge.edge_type || "related";
        line.dataset.id = edge.id || "";
        edgeLayer.appendChild(line);
      });

      nodes.forEach(node => {
        const pos = Graph.nodeMap.get(node.id);
        const group = document.createElementNS(NS, "g");
        group.classList.add("graph-node", node.memory_type || "semantic");
        group.dataset.id = node.id;
        group.setAttribute("tabindex", "0");

        const circle = document.createElementNS(NS, "circle");
        circle.setAttribute("r", String(9 + Math.min(12, Number(node.degree || 0) * 1.6)));
        group.appendChild(circle);

        const label = document.createElementNS(NS, "text");
        label.setAttribute("x", "17");
        label.setAttribute("y", "5");
        label.textContent = truncate(node.label || node.id, 34);
        group.appendChild(label);

        group.addEventListener("click", e => {
          e.stopPropagation();
          Graph.select(node.id);
        });
        group.addEventListener("mousedown", e => {
          e.stopPropagation();
          Graph.draggingNode = node.id;
          Graph.select(node.id);
        });
        group.addEventListener("keydown", e => {
          if (e.key === "Enter") Graph.select(node.id);
        });
        nodeLayer.appendChild(group);
        pos.el = group;
      });

      svg.addEventListener("click", e => {
        if (e.target === svg) Graph.select(null);
      }, { once: true });

      Graph.applyTransform();
      Graph.redrawPositions();
      Graph.updateClasses();
      if (Graph.selectedId) Graph.renderDetail(Graph.selectedId);
      else Graph.renderSummary();
    },

    redrawPositions() {
      const svg = $("#graphSvg");
      if (!svg) return;
      $$(".graph-edge", svg).forEach(line => {
        const a = Graph.nodeMap.get(line.dataset.source);
        const b = Graph.nodeMap.get(line.dataset.target);
        if (!a || !b) return;
        line.setAttribute("x1", a.x);
        line.setAttribute("y1", a.y);
        line.setAttribute("x2", b.x);
        line.setAttribute("y2", b.y);
      });
      Graph.nodeMap.forEach(pos => {
        if (pos.el) pos.el.setAttribute("transform", `translate(${pos.x},${pos.y})`);
      });
    },

    applyTransform() {
      const world = $("#graphWorld");
      if (world) world.setAttribute("transform", `translate(${Graph.pan.x},${Graph.pan.y}) scale(${Graph.zoom})`);
    },

    select(id) {
      Graph.selectedId = id;
      Graph.updateClasses();
      if (id) Graph.renderDetail(id);
      else Graph.renderSummary();
    },

    updateClasses() {
      const svg = $("#graphSvg");
      if (!svg) return;
      const selected = Graph.selectedId;
      const neighbors = new Set();
      $$(".graph-edge", svg).forEach(edge => {
        const hit = selected && (edge.dataset.source === selected || edge.dataset.target === selected);
        edge.classList.toggle("highlight", !!hit);
        if (hit) {
          neighbors.add(edge.dataset.source);
          neighbors.add(edge.dataset.target);
        }
      });
      const pinned = new Set((State.pinnedThoughts || []).map(t => t.id));
      $$(".graph-node", svg).forEach(node => {
        const id = node.dataset.id;
        node.classList.toggle("selected", id === selected);
        node.classList.toggle("pinned", pinned.has(id));
        node.classList.toggle("dim", !!selected && id !== selected && !neighbors.has(id));
      });
    },

    renderSummary() {
      const counts = Graph.data?.counts || { nodes: 0, edges: 0 };
      $("#graphDetail").innerHTML = `
        <h2>${escapeHtml(Graph.scopeLabel || "LLM Wiki")} 그래프</h2>
        <p class="muted">${counts.nodes}개 노드, ${counts.edges}개 관계</p>
        <p class="muted">노드는 markdown 파일이고, 관계는 graph cache에 저장됩니다. 노드 선택 후 1-hop 또는 2-hop으로 주변 Wiki만 좁혀볼 수 있습니다.</p>
      `;
    },

    renderSourceDetail(node) {
      const topics = Array.isArray(node.topics) ? node.topics : [];
      const edges = (Graph.data?.edges || [])
        .map(e => e.data)
        .filter(e => e.source === node.id || e.target === node.id);
      $("#graphDetail").innerHTML = `
        <h2>${escapeHtml(node.title || node.filename || node.id)}</h2>
        <div class="muted">${escapeHtml(node.id)} / ${escapeHtml(node.kind || node.view_type || "wiki")}</div>
        <div class="tag-row">
          ${topics.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("")}
        </div>
        <div class="actions">
          <button class="primary" id="graphPinBtn">채팅에 고정</button>
          <button id="graphHop1Btn">1-hop</button>
          <button id="graphHop2Btn">2-hop</button>
        </div>
        <h3 style="font-size:13px;margin-top:14px;">Markdown 미리보기</h3>
        <pre>${escapeHtml(node.content_preview || "미리보기 없음")}</pre>
        <h3 style="font-size:13px;margin-top:14px;">Graph 관계</h3>
        <pre>${escapeHtml(edges.length ? JSON.stringify(edges.map(e => ({
          source: e.source,
          target: e.target,
          edge_type: e.edge_type,
          reason: e.metadata?.reason || "",
        })), null, 2) : "관계 없음")}</pre>
      `;
      $("#graphPinBtn")?.addEventListener("click", () => Graph.pin(node));
      $("#graphHop1Btn")?.addEventListener("click", () => Graph.focusDepth(node.id, 1));
      $("#graphHop2Btn")?.addEventListener("click", () => Graph.focusDepth(node.id, 2));
    },

    async renderDetail(id) {
      const node = (Graph.data?.nodes || []).map(n => n.data).find(n => n.id === id);
      if (!node) return;
      if (node.view_type === "source" || String(node.id || "").startsWith("source:")) {
        Graph.renderSourceDetail(node);
        return;
      }
      let detail = { meta: node, content: node.content_preview || "" };
      if (!node.legacy) {
        try {
          detail = await api("api/code/knowledge/thought/" + encodeURIComponent(id) + uidQs());
        } catch {}
      }
      const meta = detail.meta || node;
      const topics = Array.isArray(meta.topics) ? meta.topics : [];
      const sources = Array.isArray(meta.sources) ? meta.sources : [];
      const canPin = !node.legacy;
      $("#graphDetail").innerHTML = `
        <h2>${escapeHtml(meta.title || id)}</h2>
        <div class="muted">${escapeHtml(id)} · ${escapeHtml(meta.memory_type || "semantic")}</div>
        <div class="tag-row">
          ${meta.domain ? `<span class="tag">도메인: ${escapeHtml(meta.domain)}</span>` : ""}
          ${meta.service ? `<span class="tag">서비스: ${escapeHtml(meta.service)}</span>` : ""}
          ${topics.map(t => `<span class="tag">${escapeHtml(t)}</span>`).join("")}
        </div>
        <div class="actions">
          ${canPin ? `<button class="primary" id="graphPinBtn">채팅에 고정</button>` : ""}
          <button id="graphHop1Btn">1-hop</button>
          <button id="graphHop2Btn">2-hop</button>
          ${canPin ? `<button id="graphAskBtn">이 Wiki로 질문</button>` : ""}
          ${canPin ? `<button id="graphEditBtn">수정</button>` : ""}
          ${canPin ? `<button class="danger" id="graphDeleteBtn">삭제</button>` : ""}
        </div>
        <pre>${escapeHtml(detail.content || meta.content_preview || "")}</pre>
        <h3 style="font-size:13px;margin-top:14px;">출처</h3>
        <pre>${escapeHtml(sources.length ? JSON.stringify(sources, null, 2) : "출처 없음")}</pre>
      `;
      const metaLine = $("#graphDetail .muted");
      if (metaLine) metaLine.textContent = `${id} / ${memoryLabel(meta.memory_type || "semantic")}`;
      $("#graphPinBtn")?.addEventListener("click", () => Graph.pin(meta));
      $("#graphHop1Btn")?.addEventListener("click", () => Graph.focusDepth(id, 1));
      $("#graphHop2Btn")?.addEventListener("click", () => Graph.focusDepth(id, 2));
      $("#graphAskBtn")?.addEventListener("click", () => Graph.askNode(meta));
      $("#graphEditBtn")?.addEventListener("click", () => Graph.openEditModal(meta, detail.content || ""));
      $("#graphDeleteBtn")?.addEventListener("click", () => Graph.deleteThought(meta));
    },

    pin(meta) {
      if (!meta?.id) return;
      if (!State.pinnedThoughts.some(t => t.id === meta.id)) {
        State.pinnedThoughts.push({ id: meta.id, title: meta.title || meta.id });
      }
      Chat.refreshChips();
      refreshMetaBar();
      Graph.updateClasses();
      toast("채팅 컨텍스트에 고정했습니다", "ok");
    },

    askNode(meta) {
      Graph.pin(meta);
      window.setMainView?.("chat");
      const input = $("#composerInput");
      if (input) {
        input.value = `고정한 LLM Wiki 문서 "${meta.title || meta.id}"를 근거로 적용 방법을 설명해줘.`;
        input.focus();
      }
    },

    focusDepth(id, depth) {
      Graph.selectedId = id;
      $("#graphDepth").value = String(depth);
      Graph.load();
    },

    async deleteThought(meta) {
      if (!meta?.id) return;
      const name = meta.title || meta.id;
      if (!confirm(`"${name}" Wiki 문서를 삭제할까요?\n해당 markdown 파일과 연결된 graph edge가 제거됩니다.`)) return;
      try {
        const body = { id: meta.id };
        const uid = graphUid();
        if (uid) body.user_id = uid;
        await api("api/code/knowledge/thought/delete", { method: "POST", body });
        State.pinnedThoughts = (State.pinnedThoughts || []).filter(t => t.id !== meta.id);
        Chat.refreshChips();
        refreshMetaBar();
        Graph.selectedId = null;
        Graph.data = null;
        await Graph.load();
        if (window.Knowledge && currentTab === "knowledge") {
          Knowledge.render($("#sidebarSearch")?.value || "");
        }
        toast("Wiki 문서를 삭제했습니다", "ok");
      } catch (e) {
        toast("삭제 실패: " + e.message, "error");
      }
    },

    async convertDocs() {
      if (!confirm("현재 구조에서는 markdown 업로드가 곧 LLM Wiki import입니다. index와 graph cache를 새로고침할까요?")) return;
      try {
        const body = {};
        const uid = graphUid();
        if (uid) body.user_id = uid;
        const result = await api("api/code/knowledge/convert", { method: "POST", body });
        toast("LLM Wiki index를 새로고침했습니다", "ok");
        Graph.data = null;
        Graph.load();
      } catch (e) {
        toast("Markdown 마이그레이션 실패: " + e.message, "error");
      }
    },

    openCreateModal() {
      const body = document.createElement("div");
      body.innerHTML = `
        <label>제목</label>
        <input id="thoughtTitle" type="text" placeholder="wiki-page.md 또는 문서 제목">
        <label>태그, 쉼표로 구분</label>
        <input id="thoughtTopics" type="text" placeholder="amhs, dispatch, stocker">
        <label>Markdown 본문</label>
        <textarea id="thoughtContent" placeholder="# Wiki Page&#10;&#10;본문을 작성하세요."></textarea>
      `;
      Modal.open({
        title: "LLM Wiki page 추가",
        body,
        footButtons: [
          { label: "취소" },
          { label: "생성", primary: true, onClick: async () => {
            const title = $("#thoughtTitle").value.trim();
            const content = $("#thoughtContent").value.trim();
            if (!title || !content) {
              toast("제목과 내용은 필수입니다", "error");
              return false;
            }
            try {
              const payload = Graph.formPayload();
              const uid = graphUid();
              if (uid) payload.user_id = uid;
              await api("api/code/knowledge/thought/create", { method: "POST", body: payload });
              toast("Wiki page를 생성했습니다", "ok");
              Graph.data = null;
              Graph.load();
            } catch (e) {
              toast(e.message, "error");
              return false;
            }
          }},
        ],
      });
    },

    openEditModal(meta, content) {
      const body = document.createElement("div");
      body.innerHTML = `
        <label>제목</label>
        <input id="thoughtTitle" type="text">
        <label>태그, 쉼표로 구분</label>
        <input id="thoughtTopics" type="text">
        <label>Markdown 본문</label>
        <textarea id="thoughtContent"></textarea>
      `;
      Modal.open({
        title: "Wiki page 수정",
        body,
        footButtons: [
          { label: "취소" },
          { label: "저장", primary: true, onClick: async () => {
            try {
              const payload = Graph.formPayload();
              payload.id = meta.id;
              const uid = graphUid();
              if (uid) payload.user_id = uid;
              await api("api/code/knowledge/thought/update", { method: "POST", body: payload });
              toast("Wiki page를 수정했습니다", "ok");
              Graph.data = null;
              Graph.load();
            } catch (e) {
              toast(e.message, "error");
              return false;
            }
          }},
        ],
      });
      $("#thoughtTitle").value = meta.title || meta.id;
      $("#thoughtTopics").value = Array.isArray(meta.topics) ? meta.topics.join(", ") : "";
      $("#thoughtContent").value = content || "";
    },

    formPayload() {
      return {
        title: $("#thoughtTitle").value.trim(),
        memory_type: "document",
        domain: "",
        service: "",
        topics: $("#thoughtTopics").value.split(",").map(s => s.trim()).filter(Boolean),
        content: $("#thoughtContent").value,
      };
    },

    onPointerMove(e) {
      const svg = $("#graphSvg");
      if (Graph.draggingNode) {
        const pt = toGraphPoint(e);
        const pos = Graph.nodeMap.get(Graph.draggingNode);
        if (pos) {
          pos.x = pt.x;
          pos.y = pt.y;
          Graph.redrawPositions();
        }
        return;
      }
      if (Graph.draggingStage && Graph.lastPointer && svg) {
        Graph.pan.x += e.clientX - Graph.lastPointer.x;
        Graph.pan.y += e.clientY - Graph.lastPointer.y;
        Graph.lastPointer = { x: e.clientX, y: e.clientY };
        Graph.applyTransform();
      }
    },

    onPointerUp() {
      if (Graph.draggingNode) {
        const pos = Graph.nodeMap.get(Graph.draggingNode);
        if (pos) {
          Graph.positionCache.set(Graph.draggingNode, {
            x: pos.x,
            y: pos.y,
            vx: 0,
            vy: 0,
          });
        }
      }
      Graph.draggingNode = null;
      Graph.draggingStage = false;
      Graph.lastPointer = null;
      $("#graphSvg")?.classList.remove("dragging");
    },
  };

  function seedPositions(nodes, edges, nodeMap, positionCache, w, h) {
    const margin = 48;
    const nodeIds = new Set(nodes.map(n => n.id));
    const adjacency = new Map();
    edges.forEach(edge => {
      if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
      if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
      if (!adjacency.has(edge.target)) adjacency.set(edge.target, []);
      adjacency.get(edge.source).push(edge.target);
      adjacency.get(edge.target).push(edge.source);
    });

    nodes.forEach((node, i) => {
      const old = positionCache.get(node.id);
      if (old) {
        nodeMap.set(node.id, {
          x: clamp(Number(old.x) || w / 2, margin, w - margin),
          y: clamp(Number(old.y) || h / 2, margin, h - margin),
          vx: 0,
          vy: 0,
        });
        return;
      }

      const anchors = (adjacency.get(node.id) || [])
        .map(id => positionCache.get(id) || nodeMap.get(id))
        .filter(Boolean);
      let x;
      let y;
      if (anchors.length) {
        const cx = anchors.reduce((sum, p) => sum + p.x, 0) / anchors.length;
        const cy = anchors.reduce((sum, p) => sum + p.y, 0) / anchors.length;
        const angle = stableRandom(node.id + ":anchor-angle") * Math.PI * 2;
        const radius = 58 + stableRandom(node.id + ":anchor-radius") * 140;
        x = cx + Math.cos(angle) * radius;
        y = cy + Math.sin(angle) * radius;
      } else {
        const angle = stableRandom(node.id + ":angle:" + i) * Math.PI * 2;
        const radius = Math.sqrt(stableRandom(node.id + ":radius:" + i)) * Math.min(w, h) * 0.44;
        const stretchX = 1.05 + stableRandom(node.id + ":stretch-x") * 0.38;
        const stretchY = 0.78 + stableRandom(node.id + ":stretch-y") * 0.32;
        x = w / 2 + Math.cos(angle) * radius * stretchX;
        y = h / 2 + Math.sin(angle) * radius * stretchY;
      }

      nodeMap.set(node.id, {
        x: clamp(x, margin, w - margin),
        y: clamp(y, margin, h - margin),
        vx: (stableRandom(node.id + ":vx") - 0.5) * 2.2,
        vy: (stableRandom(node.id + ":vy") - 0.5) * 2.2,
      });
    });
  }

  function layout(nodes, edges, nodeMap, w, h) {
    const ids = nodes.map(n => n.id);
    const ticks = nodes.length > 220 ? 80 : 130;
    for (let t = 0; t < ticks; t++) {
      const heat = 1 - t / ticks;
      for (let i = 0; i < ids.length; i++) {
        const a = nodeMap.get(ids[i]);
        for (let j = i + 1; j < ids.length; j++) {
          const b = nodeMap.get(ids[j]);
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 8) {
            dx = stableRandom(ids[i] + ":" + ids[j] + ":dx") - 0.5;
            dy = stableRandom(ids[i] + ":" + ids[j] + ":dy") - 0.5;
            d2 = 8;
          }
          const f = Math.min(2200 / d2, 3.2);
          a.vx += dx * f;
          a.vy += dy * f;
          b.vx -= dx * f;
          b.vy -= dy * f;
        }
      }
      edges.forEach(edge => {
        const a = nodeMap.get(edge.source);
        const b = nodeMap.get(edge.target);
        if (!a || !b) return;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const edgeKey = edge.id || `${edge.source}->${edge.target}:${edge.edge_type || "related"}`;
        const target = 96 + stableRandom(edgeKey + ":len") * 72;
        const f = (dist - target) * 0.018;
        const fx = (dx / dist) * f;
        const fy = (dy / dist) * f;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      });
      ids.forEach(id => {
        const p = nodeMap.get(id);
        p.vx += (w / 2 - p.x) * 0.0048;
        p.vy += (h / 2 - p.y) * 0.0048;
        p.vx += (stableRandom(id + ":jitter-x:" + t) - 0.5) * 0.34 * heat;
        p.vy += (stableRandom(id + ":jitter-y:" + t) - 0.5) * 0.34 * heat;
        p.vx *= 0.78;
        p.vy *= 0.78;
        p.x = clamp(p.x + p.vx, 24, w - 24);
        p.y = clamp(p.y + p.vy, 24, h - 24);
      });
    }
  }

  function syncPositionCache(nodes, nodeMap, positionCache) {
    nodes.forEach(node => {
      const p = nodeMap.get(node.id);
      if (!p) return;
      positionCache.set(node.id, {
        x: p.x,
        y: p.y,
        vx: 0,
        vy: 0,
      });
    });
    if (positionCache.size > 1600) {
      const visible = new Set(nodes.map(n => n.id));
      for (const id of positionCache.keys()) {
        if (!visible.has(id)) positionCache.delete(id);
        if (positionCache.size <= 1400) break;
      }
    }
  }

  function toGraphPoint(e) {
    const svg = $("#graphSvg");
    const rect = svg.getBoundingClientRect();
    return {
      x: (e.clientX - rect.left - Graph.pan.x) / Graph.zoom,
      y: (e.clientY - rect.top - Graph.pan.y) / Graph.zoom,
    };
  }

  function graphUid() {
    try {
      const u = JSON.parse(sessionStorage.getItem("demos_user") || "null");
      return u && u.id ? u.id : null;
    } catch {
      return null;
    }
  }

  function uidQs() {
    const uid = graphUid();
    return uid ? "?user_id=" + encodeURIComponent(uid) : "";
  }

  function debounce(fn, ms) {
    let timer = null;
    return (...args) => {
      clearTimeout(timer);
      timer = setTimeout(() => fn(...args), ms);
    };
  }

  function clamp(v, min, max) {
    return Math.max(min, Math.min(max, v));
  }

  function stableRandom(seed) {
    let h = 2166136261;
    const s = String(seed || "");
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    h += h << 13;
    h ^= h >>> 7;
    h += h << 3;
    h ^= h >>> 17;
    h += h << 5;
    return (h >>> 0) / 4294967296;
  }

  function truncate(s, n) {
    s = String(s || "");
    return s.length > n ? s.slice(0, n - 1) + "..." : s;
  }

  function memoryLabel(type) {
    return ({
      "": "전체 유형",
      semantic: "사실/정의",
      procedural: "절차/규칙",
      episodic: "사건/사례",
      reflective: "회고/교훈",
      thesis: "핵심 주장",
      topic: "주제",
      document: "레거시 문서",
    })[type] || type || "지식";
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.SecondBrainGraph = Graph;
})();
