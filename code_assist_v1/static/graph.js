/* Second Brain graph view for code_assist_v1. No build step required. */

(function () {
  const NS = "http://www.w3.org/2000/svg";

  const Graph = {
    built: false,
    data: null,
    visibleData: null,
    selectedId: null,
    zoom: 1,
    viewportWidth: 900,
    viewportHeight: 620,
    pan: { x: 0, y: 0 },
    nodeMap: new Map(),
    positionCache: new Map(),
    layoutScope: null,
    hoveredId: null,
    focusedId: null,
    draggingNode: null,
    draggingStage: false,
    lastPointer: null,
    provider: null,
    openDocument: null,
    readOnly: false,
    scopeLabel: "",
    loadToken: 0,

    useProvider(provider, { readOnly = false, scopeLabel = "", openDocument = null } = {}) {
      if (provider !== null && typeof provider !== "function") {
        throw new TypeError("Graph provider must be a function or null.");
      }
      if (openDocument !== null && typeof openDocument !== "function") {
        throw new TypeError("Graph document opener must be a function or null.");
      }
      Graph.provider = provider;
      Graph.openDocument = openDocument;
      Graph.readOnly = Boolean(readOnly);
      Graph.scopeLabel = String(scopeLabel || "");
      Graph.data = null;
      Graph.visibleData = null;
      Graph.selectedId = null;
      Graph.hoveredId = null;
      Graph.focusedId = null;
      if ($("#graphNodeTitle")) $("#graphNodeTitle").hidden = true;
      // A new authorized space must not inherit the previous space's filters.
      ["graphSearch", "graphTopic", "graphService", "graphMemory", "graphEdge"].forEach(id => {
        const control = $("#" + id);
        if (control) control.value = "";
      });
      if ($("#graphDepth")) $("#graphDepth").value = "all";
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
      const canonical = Boolean(Graph.provider);
      const topic = $("#graphTopic");
      if (topic) topic.hidden = canonical; // Canonical graph metadata has no tags.
      const search = $("#graphSearch");
      if (search) search.placeholder = canonical ? "Wiki 제목, 문서 ID 검색" : "markdown 파일명, 제목, 태그 검색";
      const canonicalTypes = new Set(["", "related", "supports", "contradicts", "depends_on", "extends", "example_of"]);
      for (const option of $("#graphEdge")?.options || []) {
        option.hidden = canonical && !canonicalTypes.has(option.value);
        option.disabled = option.hidden;
      }
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
              <option value="depends_on">의존</option>
              <option value="example_of">적용 사례</option>
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
              <svg class="graph-svg" id="graphSvg" role="group" aria-label="LLM Wiki 그래프"></svg>
              <div class="graph-node-title" id="graphNodeTitle" role="tooltip" hidden></div>
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

      const debouncedLoad = debounce(() => Graph.refreshView(), 280);
      ["graphSearch", "graphService", "graphTopic"].forEach(id => {
        $("#" + id)?.addEventListener("input", debouncedLoad);
      });
      ["graphViewMode", "graphDepth", "graphMemory", "graphEdge"].forEach(id => {
        $("#" + id)?.addEventListener("change", () => Graph.refreshView());
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
          Graph.refreshView();
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
      svg.setAttribute("role", "group");
      svg.addEventListener("wheel", e => {
        e.preventDefault();
        const oldZoom = Graph.zoom;
        Graph.zoom = clamp(Graph.zoom * (e.deltaY > 0 ? 0.9 : 1.1), 0.25, 3.5);
        const { x: mx, y: my } = viewportPoint(e, svg);
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

    refreshView() {
      // Display filters operate only on the already authorized graph. They
      // never query another space or load document bodies; Refresh still fetches.
      if (Graph.provider && Graph.data) Graph.draw();
      else return Graph.load();
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
      Graph.hoveredId = null;
      Graph.focusedId = null;
      if ($("#graphNodeTitle")) $("#graphNodeTitle").hidden = true;

      Graph.visibleData = Graph.provider ? filteredGraph(Graph.data, {
        query: $("#graphSearch")?.value || "",
        topic: $("#graphTopic")?.value || "",
        memory: $("#graphMemory")?.value || "",
        service: $("#graphService")?.value || "",
        edgeType: $("#graphEdge")?.value || "",
        depth: $("#graphDepth")?.value || "all",
        focus: Graph.selectedId,
      }) : Graph.data;
      const nodes = (Graph.visibleData.nodes || []).map(n => ({ ...n.data }));
      const edges = (Graph.visibleData.edges || []).map(e => ({ ...e.data }));
      const w = Math.max(720, stage.clientWidth || 900);
      const h = Math.max(520, stage.clientHeight || 620);
      Graph.viewportWidth = w;
      Graph.viewportHeight = h;
      // Provider functions are recreated on refresh. Only the stable selected
      // view identity, never a function reference, owns manual layout state.
      const layoutScope = Graph.provider
        ? "canonical:" + String(Graph.data.space_id || Graph.scopeLabel)
        : "legacy:" + String(graphUid() || "");
      if (layoutScope !== Graph.layoutScope) {
        Graph.positionCache.clear();
        Graph.layoutScope = layoutScope;
        Graph.pan = { x: 0, y: 0 };
        Graph.zoom = 1;
      }
      svg.setAttribute("viewBox", `0 0 ${w} ${h}`);

      if (!nodes.length) {
        svg.innerHTML = "";
        if (Graph.provider && (Graph.data.nodes || []).length) {
          $("#graphDetail").innerHTML = `<h2>조건에 맞는 문서가 없습니다</h2>
            <p class="muted">검색어나 관계 유형, 주변 범위를 변경해 주세요. 문서가 삭제된 것은 아닙니다.</p>`;
        } else if (Graph.readOnly) {
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

      layoutComponents(nodes, edges, Graph.nodeMap, Graph.positionCache, w, h);
      syncPositionCache(nodes, Graph.nodeMap, Graph.positionCache, w, h);

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
        group.setAttribute("role", "button");
        group.setAttribute("aria-label", node.title || node.label || node.id);

        const circle = document.createElementNS(NS, "circle");
        circle.setAttribute("r", String(9 + Math.min(12, Number(node.degree || 0) * 1.6)));
        group.appendChild(circle);

        const label = document.createElementNS(NS, "text");
        label.setAttribute("x", "17");
        label.setAttribute("y", "5");
        label.textContent = truncate(node.label || node.id, 34);
        group.appendChild(label);
        const title = document.createElementNS(NS, "title");
        title.textContent = node.title || node.label || node.id;
        group.appendChild(title);

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
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            Graph.select(node.id);
          }
        });
        group.addEventListener("mouseenter", () => { Graph.hoveredId = node.id; Graph.positionLabels(); });
        group.addEventListener("mouseleave", () => { Graph.hoveredId = null; Graph.positionLabels(); });
        group.addEventListener("focus", () => { Graph.focusedId = node.id; Graph.positionLabels(); });
        group.addEventListener("blur", () => { Graph.focusedId = null; Graph.positionLabels(); });
        nodeLayer.appendChild(group);
        pos.el = group;
        pos.label = label;
        pos.title = node.title || node.label || node.id;
        pos.labelText = node.label || node.title || node.id;
        pos.labelCache = new Map();
      });

      svg.addEventListener("click", e => {
        if (e.target === svg) Graph.select(null);
      }, { once: true });

      Graph.applyTransform();
      Graph.redrawPositions();
      Graph.updateClasses();
      if (Graph.selectedId && Graph.nodeMap.has(Graph.selectedId)) Graph.renderDetail(Graph.selectedId);
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
      Graph.positionLabels();
    },

    positionLabels() {
      positionVisibleLabels(Graph);
    },

    applyTransform() {
      const world = $("#graphWorld");
      if (world) world.setAttribute("transform", `translate(${Graph.pan.x},${Graph.pan.y}) scale(${Graph.zoom})`);
      Graph.positionLabels();
    },

    select(id) {
      Graph.selectedId = id;
      Graph.updateClasses();
      Graph.positionLabels();
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
        node.setAttribute("aria-pressed", String(id === selected));
        node.classList.toggle("pinned", pinned.has(id));
        node.classList.toggle("dim", !!selected && id !== selected && !neighbors.has(id));
      });
    },

    renderSummary() {
      const counts = Graph.visibleData?.counts || Graph.data?.counts || { nodes: 0, edges: 0 };
      $("#graphDetail").innerHTML = `
        <h2>${escapeHtml(Graph.scopeLabel || "LLM Wiki")} 그래프</h2>
        <p class="muted">${counts.nodes}개 노드, ${counts.edges}개 관계</p>
        <p class="muted">${Graph.provider
          ? "현재 지식공간에서 읽을 수 있는 Wiki와 저장된 관계입니다. 노드를 선택하면 문서를 열거나 1-hop·2-hop 주변 연결을 볼 수 있습니다."
          : "노드는 markdown 파일이고, 관계는 graph cache에 저장됩니다. 노드 선택 후 1-hop 또는 2-hop으로 주변 Wiki만 좁혀볼 수 있습니다."}</p>
        <p class="muted">겹치는 제목은 줄여 표시합니다. 노드에 마우스를 올리거나 키보드로 이동·선택하면 전체 제목을 확인할 수 있습니다.</p>
      `;
    },

    renderCanonicalDetail(node) {
      const provider = Graph.provider;
      const token = Graph.loadToken;
      const opener = Graph.openDocument;
      const nodes = new Map((Graph.data.nodes || []).map(value => [value.data.id, value.data]));
      const relations = (Graph.visibleData?.edges || Graph.data.edges || []).map(value => value.data)
        .filter(edge => edge.source === node.id || edge.target === node.id);
      const relationRows = relations.map(edge => {
        const reversed = edge.direction === "target_to_source";
        const source = nodes.get(reversed ? edge.target : edge.source);
        const target = nodes.get(reversed ? edge.source : edge.target);
        const symmetric = edge.direction === "symmetric" || ["related", "contradicts"].includes(edge.edge_type);
        return `<li>${escapeHtml(source?.title || source?.label || "문서")} ${symmetric ? "↔" : "→"} ${escapeHtml(target?.title || target?.label || "문서")} · ${escapeHtml(edge.edge_type || "related")}</li>`;
      }).join("");
      $("#graphDetail").innerHTML = `
        <h2>${escapeHtml(node.title || node.label || "Wiki 문서")}</h2>
        <p class="muted">본문·자동 연관 문서·수정 이력은 Wiki 문서에서 확인할 수 있습니다.</p>
        <div class="actions">
          ${opener ? '<button class="primary" id="graphOpenDocumentBtn">Wiki 문서 열기</button>' : ""}
          <button id="graphHop1Btn">1-hop</button>
          <button id="graphHop2Btn">2-hop</button>
        </div>
        <h3 style="font-size:13px;margin-top:14px;">표시 중인 연결</h3>
        ${relations.length ? `<ul>${relationRows}</ul>` : '<p class="muted">표시 중인 연결이 없습니다.</p>'}
        <p class="muted" id="graphDocumentError" role="status"></p>
      `;
      $("#graphOpenDocumentBtn")?.addEventListener("click", async () => {
        if (token !== Graph.loadToken || provider !== Graph.provider || Graph.selectedId !== node.id) return;
        try { await opener(node); }
        catch (error) {
          if (token === Graph.loadToken && provider === Graph.provider && Graph.selectedId === node.id) {
            const notice = $("#graphDocumentError");
            if (notice) notice.textContent = `문서를 열지 못했습니다: ${error.message || error}`;
          }
        }
      });
      $("#graphHop1Btn")?.addEventListener("click", () => Graph.focusDepth(node.id, 1));
      $("#graphHop2Btn")?.addEventListener("click", () => Graph.focusDepth(node.id, 2));
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
      if (Graph.provider && node.view_type === "canonical") {
        Graph.renderCanonicalDetail(node);
        return;
      }
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
      Graph.refreshView();
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
          pos.x = clamp(pt.x, 36, Graph.viewportWidth - 36);
          pos.y = clamp(pt.y, 36, Graph.viewportHeight - 54);
          Graph.redrawPositions();
        }
        return;
      }
      if (Graph.draggingStage && Graph.lastPointer && svg) {
        const current = viewportPoint(e, svg);
        const previous = viewportPoint({ clientX: Graph.lastPointer.x, clientY: Graph.lastPointer.y }, svg);
        Graph.pan.x += current.x - previous.x;
        Graph.pan.y += current.y - previous.y;
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
            userDragged: true,
            width: Graph.viewportWidth,
            height: Graph.viewportHeight,
          });
        }
      }
      Graph.draggingNode = null;
      Graph.draggingStage = false;
      Graph.lastPointer = null;
      $("#graphSvg")?.classList.remove("dragging");
    },
  };

  function filteredGraph(data, filters) {
    const fold = value => String(value || "").normalize("NFKC").toLowerCase();
    const allNodes = data.nodes || [];
    const ids = new Set(allNodes.map(node => node.data.id));
    let edges = (data.edges || []).filter(({ data: edge }) => ids.has(edge.source) && ids.has(edge.target)
      && (!filters.edgeType || edge.edge_type === filters.edgeType));
    // With an explicit relation filter, display only its endpoints. Without
    // that filter, isolated documents remain first-class visible nodes.
    let allowed = filters.edgeType
      ? new Set(edges.flatMap(({ data: edge }) => [edge.source, edge.target])) : ids;
    const depth = Number(filters.depth);
    if (filters.focus && [1, 2].includes(depth)) {
      allowed = new Set(allowed.has(filters.focus) ? [filters.focus] : []);
      const neighbors = new Map();
      for (const { data: edge } of edges) {
        if (!neighbors.has(edge.source)) neighbors.set(edge.source, new Set());
        if (!neighbors.has(edge.target)) neighbors.set(edge.target, new Set());
        neighbors.get(edge.source).add(edge.target);
        neighbors.get(edge.target).add(edge.source);
      }
      let frontier = [...allowed];
      for (let hop = 0; hop < depth; hop += 1) {
        const next = [];
        for (const id of frontier) for (const neighbor of neighbors.get(id) || []) {
          if (!allowed.has(neighbor)) { allowed.add(neighbor); next.push(neighbor); }
        }
        frontier = next;
      }
    }
    const query = fold(filters.query).trim();
    const topic = fold(filters.topic).trim();
    const service = fold(filters.service).trim();
    const nodes = allNodes.filter(({ data: node }) => {
      const topics = Array.isArray(node.topics) ? node.topics : [];
      return allowed.has(node.id)
        && (!query || fold([node.title, node.label, node.filename, node.id, ...topics].join(" ")).includes(query))
        && (!topic || topics.some(value => fold(value).includes(topic)))
        && (!service || fold(node.service).includes(service))
        && (!filters.memory || node.memory_type === filters.memory);
    });
    const visible = new Set(nodes.map(node => node.data.id));
    edges = edges.filter(({ data: edge }) => visible.has(edge.source) && visible.has(edge.target));
    return { ...data, nodes, edges, counts: { nodes: nodes.length, edges: edges.length } };
  }

  function compareIds(a, b) { return a < b ? -1 : a > b ? 1 : 0; }

  function layoutComponents(nodes, edges, nodeMap, positionCache, w, h) {
    // O(V+E) connectivity, deterministic weighted rectangle partition, and
    // O(ticks * sum(component_size^2)) local forces. Isolates need no force.
    // Rendered/API edges are untouched; this is a separate layout projection.
    const ordered = [...nodes].sort((a, b) => compareIds(a.id, b.id));
    const adjacency = new Map(ordered.map(node => [node.id, new Set()]));
    for (const edge of edges) {
      if (!adjacency.has(edge.source) || !adjacency.has(edge.target)) continue;
      adjacency.get(edge.source).add(edge.target);
      adjacency.get(edge.target).add(edge.source);
    }
    const byId = new Map(ordered.map(node => [node.id, node]));
    const membership = new Map();
    const components = [];
    for (const node of ordered) {
      if (membership.has(node.id)) continue;
      const component = { nodes: [], edges: [] };
      const pending = [node.id];
      membership.set(node.id, component);
      for (let i = 0; i < pending.length; i++) {
        const id = pending[i];
        component.nodes.push(byId.get(id));
        for (const next of [...adjacency.get(id)].sort(compareIds)) if (!membership.has(next)) {
          membership.set(next, component); pending.push(next);
        }
      }
      component.nodes.sort((a, b) => compareIds(a.id, b.id));
      component.visitOrder = pending;
      components.push(component);
    }
    for (const edge of edges) {
      const component = membership.get(edge.source);
      if (component && component === membership.get(edge.target)) component.edges.push(edge);
    }
    const edgeKey = edge => JSON.stringify([edge.source, edge.target, edge.edge_type || "", edge.id || ""]);
    for (const component of components) component.edges.sort((a, b) => compareIds(edgeKey(a), edgeKey(b)));
    components.sort((a, b) => b.nodes.length - a.nodes.length || compareIds(a.nodes[0].id, b.nodes[0].id));
    function partition(parts, box) {
      if (parts.length === 1) { parts[0].box = box; return; }
      const total = parts.reduce((sum, part) => sum + part.nodes.length, 0);
      let split = 1, weight = parts[0].nodes.length;
      while (split < parts.length - 1 && weight + parts[split].nodes.length / 2 < total / 2) {
        weight += parts[split++].nodes.length;
      }
      const extent = Math.max(box.w, box.h);
      const minimum = Math.min(72, extent / 2);
      const ratio = clamp(weight / total, minimum / extent, 1 - minimum / extent);
      if (box.w >= box.h) {
        partition(parts.slice(0, split), { ...box, w: box.w * ratio });
        partition(parts.slice(split), { ...box, x: box.x + box.w * ratio, w: box.w * (1 - ratio) });
      } else {
        partition(parts.slice(0, split), { ...box, h: box.h * ratio });
        partition(parts.slice(split), { ...box, y: box.y + box.h * ratio, h: box.h * (1 - ratio) });
      }
    }
    if (components.length) partition(components, { x: 36, y: 36, w: w - 72, h: h - 108 });
    for (const component of components) {
      const box = component.box;
      const local = new Map();
      if (component.nodes.length === 1) local.set(component.nodes[0].id, { x: 0, y: 0 });
      else {
        seedPositions(component.nodes, component.edges, local, new Map(), 900, 620);
        layout(component.nodes, component.edges, local, 900, 620);
      }
      const points = [...local.values()];
      const minX = points.reduce((v,p) => Math.min(v,p.x), Infinity), maxX = points.reduce((v,p) => Math.max(v,p.x), -Infinity);
      const minY = points.reduce((v,p) => Math.min(v,p.y), Infinity), maxY = points.reduce((v,p) => Math.max(v,p.y), -Infinity);
      const padding = Math.min(28, box.w * .2, box.h * .2);
      const fit = (value, min, max, size) => max === min ? size / 2 : padding + (value - min) / (max - min) * (size - 2 * padding);
      const fitted = new Map(component.nodes.map(node => [node.id, {
        x: fit(local.get(node.id).x, minX, maxX, box.w),
        y: fit(local.get(node.id).y, minY, maxY, box.h),
      }]));
      // A local force fit can still stack nodes on its own clamp boundaries.
      // Only when centers collide, use a topology-ordered serpentine grid.
      // No links are added/removed. BFS orders placement, not edge distance;
      // arbitrary branching/cyclic graphs can still have crossing lines.
      const cells = new Map();
      let crowded = false;
      for (const p of fitted.values()) {
        const cx = Math.floor(p.x / 44), cy = Math.floor(p.y / 44);
        for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++)
          if ((cells.get((cx + dx) + ":" + (cy + dy)) || []).some(q => Math.hypot(p.x - q.x, p.y - q.y) < 44)) crowded = true;
        if (crowded) break;
        const key = cx + ":" + cy;
        if (!cells.has(key)) cells.set(key, []);
        cells.get(key).push(p);
      }
      let gridWidth = null;
      if (crowded) {
        const count = component.nodes.length, width = box.w - padding * 2, height = box.h - padding * 2;
        let columns = 1, best = 0;
        for (let candidate = 1; candidate <= count; candidate++) {
          const gap = Math.min(width / candidate, height / Math.ceil(count / candidate));
          if (gap > best) { best = gap; columns = candidate; }
        }
        const rows = Math.ceil(count / columns);
        gridWidth = width / columns;
        component.visitOrder.forEach((id, index) => {
          const row = Math.floor(index / columns), column = row % 2 ? columns - 1 - index % columns : index % columns;
          fitted.set(id, { x: padding + (column + .5) * gridWidth, y: padding + (row + .5) * height / rows });
        });
        // A one-row/column star makes overlapping spokes look like a chain.
        // If the component box has room, separate its sole hub from a fan of
        // leaves. Detect topology only, never document names or model scores.
        const countNeighbors = node => adjacency.get(node.id).size;
        const hub = component.nodes.length > 2
          ? component.nodes.find(node => countNeighbors(node) === count - 1
            && component.nodes.every(other => other.id === node.id || countNeighbors(other) === 1)) : null;
        const horizontal = width >= height;
        const longSide = horizontal ? width : height, shortSide = horizontal ? height : width;
        const maxRadius = component.nodes.reduce((radius, node) => Math.max(radius, 9 + Math.min(12, Number(node.degree || 0) * 1.6)), 9);
        const fanGap = Math.max(44, 2 * maxRadius + 4);
        if (hub && longSide / (count - 2) >= fanGap && shortSide >= fanGap) {
          const point = (along, across) => horizontal ? { x: padding + along, y: padding + across }
            : { x: padding + across, y: padding + along };
          fitted.set(hub.id, point(longSide / 2, 0));
          component.nodes.filter(node => node.id !== hub.id).forEach((node, index) => {
            fitted.set(node.id, point(index * longSide / (count - 2), shortSide));
          });
          gridWidth = horizontal ? longSide / (count - 2) : shortSide;
        }
      }
      for (const node of component.nodes) {
        const point = fitted.get(node.id), old = positionCache.get(node.id);
        const userDragged = Boolean(old?.userDragged);
        nodeMap.set(node.id, {
          x: userDragged ? clamp(old.x * w / (old.width || w), 36, w - 36) : box.x + point.x,
          y: userDragged ? clamp(old.y * h / (old.height || h), 36, h - 54) : box.y + point.y,
          vx: 0, vy: 0, userDragged,
          labelWidth: gridWidth ? gridWidth - 12 : component.nodes.length === 1 ? box.w - 12 : Math.sqrt(box.w * box.h / component.nodes.length),
        });
      }
    }
  }

  function positionVisibleLabels(graph) {
    const active = graph.focusedId || graph.hoveredId || graph.selectedId;
    const title = $("#graphNodeTitle");
    const activeNode = graph.nodeMap.get(active);
    if (title) { title.hidden = !activeNode; title.textContent = activeNode?.title || ""; }
    const dense = graph.nodeMap.size > 24;
    // Screen-space occupancy buckets avoid a quadratic all-label scan on every
    // mouse move. Text measurements are cached until the label budget changes.
    const occupied = new Map(), size = 64;
    const keys = box => {
      const result = [];
      for (let x = Math.floor(box.left / size); x <= Math.floor(box.right / size); x++)
        for (let y = Math.floor(box.top / size); y <= Math.floor(box.bottom / size); y++) result.push(x + ":" + y);
      return result;
    };
    const overlaps = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const entries = [...graph.nodeMap].sort(([a], [b]) => Number(b === active) - Number(a === active) || compareIds(a, b));
    for (const [id, pos] of entries) {
      if (!pos.label) continue;
      const x = pos.x * graph.zoom + graph.pan.x, y = pos.y * graph.zoom + graph.pan.y;
      const faceLeft = x > graph.viewportWidth / 2;
      const available = dense ? Math.min(pos.labelWidth || 100, 180) : 340;
      const budget = Math.max(24, Math.min(available, (graph.viewportWidth - 24) / graph.zoom));
      const cacheKey = Math.floor(budget);
      let fitted = pos.labelCache?.get(cacheKey);
      if (!fitted) {
        const chars = [...String(pos.labelText || pos.title || "")];
        let limit = Math.min(34, chars.length), text, width;
        const measure = value => {
          pos.label.textContent = value;
          return typeof pos.label.getComputedTextLength === "function" ? pos.label.getComputedTextLength()
            : [...value].reduce((sum, char) => sum + (char.charCodeAt(0) > 255 ? 13 : 7), 0);
        };
        do {
          text = chars.slice(0, limit).join("") + (limit < chars.length ? "…" : "");
          width = measure(text);
          if (width <= budget || limit <= 1) break;
          limit -= 1;
        } while (true);
        fitted = { text, width };
        if (pos.labelCache) { if (pos.labelCache.size > 16) pos.labelCache.clear(); pos.labelCache.set(cacheKey, fitted); }
      }
      pos.label.textContent = fitted.text;
      const offsetX = dense ? 0 : faceLeft ? -17 : 17;
      const offsetY = dense ? 28 : 5;
      pos.label.setAttribute("x", String(offsetX));
      pos.label.setAttribute("y", String(offsetY));
      pos.label.setAttribute("text-anchor", dense ? "middle" : faceLeft ? "end" : "start");
      const anchorX = x + offsetX * graph.zoom;
      const labelWidth = (fitted.width + 8) * graph.zoom;
      const left = dense ? anchorX - labelWidth / 2 : faceLeft ? anchorX - labelWidth : anchorX;
      const box = { left, right: left + labelWidth, top: y + (offsetY - 14) * graph.zoom, bottom: y + (offsetY + 4) * graph.zoom };
      const bucketKeys = keys(box);
      const onScreen = box.left >= 0 && box.right <= graph.viewportWidth && box.top >= 0 && box.bottom <= graph.viewportHeight - 42;
      const collision = bucketKeys.some(key => (occupied.get(key) || []).some(other => overlaps(box, other)));
      const visible = id === active || (onScreen && !collision);
      pos.label.setAttribute("visibility", visible ? "visible" : "hidden");
      pos.labelBox = visible ? box : null;
      if (visible) for (const key of bucketKeys) {
        if (!occupied.has(key)) occupied.set(key, []);
        occupied.get(key).push(box);
      }
    }
  }

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

  function syncPositionCache(nodes, nodeMap, positionCache, w, h) {
    nodes.forEach(node => {
      const p = nodeMap.get(node.id);
      if (!p) return;
      positionCache.set(node.id, {
        x: p.x,
        y: p.y,
        vx: 0,
        vy: 0,
        userDragged: Boolean(p.userDragged),
        width: w,
        height: h,
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

  function viewportPoint(e, svg) {
    const rect = svg.getBoundingClientRect();
    const width = rect.width || Graph.viewportWidth, height = rect.height || Graph.viewportHeight;
    // The SVG uses the default xMidYMid meet aspect ratio: account for both
    // uniform scale and letterboxing when the CSS viewport is narrower/shorter.
    const scale = Math.min(width / Graph.viewportWidth, height / Graph.viewportHeight);
    return { x: (e.clientX - rect.left - (width - Graph.viewportWidth * scale) / 2) / scale,
      y: (e.clientY - rect.top - (height - Graph.viewportHeight * scale) / 2) / scale };
  }

  function toGraphPoint(e) {
    const point = viewportPoint(e, $("#graphSvg"));
    return {
      x: (point.x - Graph.pan.x) / Graph.zoom,
      y: (point.y - Graph.pan.y) / Graph.zoom,
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
