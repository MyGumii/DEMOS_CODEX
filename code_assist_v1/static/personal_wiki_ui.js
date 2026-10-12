/* Accessible, paginated SBE personal-Wiki tree. No legacy route is used. */
(function personalWikiUiModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.PersonalWikiUI = api;
})(typeof globalThis === "object" ? globalThis : this, function buildPersonalWikiUI() {
  "use strict";

  const PAGE_SIZE = 50;
  const GROUPS = Object.freeze([
    Object.freeze({ kind: "personal", label: "내 지식" }),
    Object.freeze({ kind: "part", label: "Part" }),
    Object.freeze({ kind: "team", label: "팀" }),
    Object.freeze({ kind: "responsibility", label: "담당" }),
  ]);

  function required(value, field) {
    if (typeof value !== "string" || !value) throw new TypeError(`${field} is required.`);
    return value;
  }

  function freezeState(state) {
    return Object.freeze({
      activeItems: Object.freeze(state.activeItems.slice()),
      activeLoaded: state.activeLoaded,
      activeNextCursor: state.activeNextCursor,
      archivedItems: Object.freeze(state.archivedItems.slice()),
      archivedLoaded: state.archivedLoaded,
      archivedNextCursor: state.archivedNextCursor,
      conversationSpaceId: state.conversationSpaceId,
      expanded: Object.freeze([...state.expanded]),
      historyOnly: state.historyOnly,
      loading: state.loading,
      orderIds: Object.freeze(state.orderIds.slice()),
      searchItems: Object.freeze(state.searchItems.slice()),
      searchNextCursor: state.searchNextCursor,
      selectedSpaceId: state.selectedSpaceId,
    });
  }

  function createController(options) {
    if (!options || typeof options !== "object") throw new TypeError("Personal Wiki options are required.");
    const transport = options.transport;
    const selectSpace = options.selectSpace;
    const openSearchResult = options.openSearchResult;
    const startFreshConversation = options.startFreshConversation;
    const operationIdFactory = options.operationIdFactory;
    const onChanged = options.onChanged || (() => {});
    const loadOrder = options.loadOrder || (() => []);
    const saveOrder = options.saveOrder || (() => {});
    if (
      !transport
      || typeof transport.listPersonalWikis !== "function"
      || typeof selectSpace !== "function"
      || typeof openSearchResult !== "function"
      || typeof startFreshConversation !== "function"
      || typeof operationIdFactory !== "function"
    ) throw new TypeError("Personal Wiki dependencies are required.");
    const listeners = new Set();
    let initialOrder = [];
    try {
      const loaded = loadOrder();
      if (Array.isArray(loaded)) {
        initialOrder = loaded.filter((value, index) => (
          typeof value === "string" && value && loaded.indexOf(value) === index
        ));
      }
    } catch (_) {
      initialOrder = [];
    }
    const state = {
      activeItems: [], activeLoaded: false, activeNextCursor: null,
      archivedItems: [], archivedLoaded: false, archivedNextCursor: null,
      conversationSpaceId: null, expanded: new Set(["personal"]), historyOnly: false,
      loading: false, orderIds: initialOrder,
      searchItems: [], searchNextCursor: null, selectedSpaceId: null,
    };

    function ordered(items) {
      const rank = new Map(state.orderIds.map((spaceId, index) => [spaceId, index]));
      return items
        .map((item, index) => ({ item, index }))
        .sort((left, right) => {
          const leftRank = rank.has(left.item.space_id) ? rank.get(left.item.space_id) : Number.MAX_SAFE_INTEGER;
          const rightRank = rank.has(right.item.space_id) ? rank.get(right.item.space_id) : Number.MAX_SAFE_INTEGER;
          return leftRank - rightRank || left.index - right.index;
        })
        .map((entry) => entry.item);
    }

    function persistLoadedOrder() {
      const loadedIds = state.activeItems.map((item) => item.space_id);
      const loaded = new Set(loadedIds);
      state.orderIds = loadedIds.concat(
        state.orderIds.filter((spaceId) => !loaded.has(spaceId)),
      );
      try { saveOrder(state.orderIds.slice()); } catch (_) { /* best-effort UI preference */ }
    }

    function emit() {
      const value = freezeState(state);
      for (const listener of listeners) listener(value);
      return value;
    }

    async function loadPage(lifecycleStatus, { append = false, query = "" } = {}) {
      const active = lifecycleStatus === "active";
      const cursor = append
        ? (active ? state.activeNextCursor : state.archivedNextCursor)
        : null;
      if (append && cursor === null) return emit();
      state.loading = true;
      emit();
      try {
        const page = await transport.listPersonalWikis({
          cursor, lifecycleStatus, limit: PAGE_SIZE, query,
        });
        const prior = append ? (active ? state.activeItems : state.archivedItems) : [];
        const ids = new Set(prior.map((item) => item.space_id));
        const merged = prior.concat(page.items.filter((item) => !ids.has(item.space_id)));
        if (active) {
          state.activeItems = ordered(merged);
          state.activeLoaded = true;
          state.activeNextCursor = page.next_cursor;
        } else {
          state.archivedItems = merged;
          state.archivedLoaded = true;
          state.archivedNextCursor = page.next_cursor;
        }
      } finally {
        state.loading = false;
      }
      return emit();
    }

    async function refresh() {
      await loadPage("active");
      if (state.archivedLoaded) await loadPage("archived");
      return emit();
    }

    async function mutate(action, profile, values = {}) {
      if (!profile || typeof profile !== "object") throw new TypeError("A personal Wiki profile is required.");
      const common = {
        expectedRowVersion: profile.row_version,
        operationId: operationIdFactory(),
        spaceId: profile.space_id,
      };
      if (action === "rename") await transport.renamePersonalWiki({ ...common, displayName: required(values.displayName, "displayName") });
      else if (action === "default") await transport.setDefaultPersonalWiki(common);
      else if (action === "archive") await transport.archivePersonalWiki({ ...common, reason: values.reason || null });
      else if (action === "restore") await transport.restorePersonalWiki({ ...common, makeDefault: !!values.makeDefault });
      else throw new TypeError("Unknown personal Wiki mutation.");
      return refresh();
    }

    const controller = {
      subscribe(listener) {
        if (typeof listener !== "function") throw new TypeError("listener is required.");
        listeners.add(listener);
        listener(freezeState(state));
        return () => listeners.delete(listener);
      },
      state: () => freezeState(state),
      async initialize(scopeState) {
        controller.syncScope(scopeState);
        if (!state.activeLoaded) await loadPage("active");
        if (state.historyOnly && !state.archivedLoaded) {
          state.expanded.add("archived");
          await loadPage("archived");
        }
        return emit();
      },
      syncScope(scopeState) {
        const badge = scopeState && scopeState.conversationBadge;
        state.selectedSpaceId = scopeState ? scopeState.selectedSpaceId : null;
        state.conversationSpaceId = badge ? badge.space_id : null;
        state.historyOnly = !!(badge && badge.history_only);
        return emit();
      },
      async toggle(group) {
        required(group, "group");
        if (state.expanded.has(group)) state.expanded.delete(group);
        else state.expanded.add(group);
        if (group === "personal" && !state.activeLoaded) await loadPage("active");
        return emit();
      },
      async showArchived() {
        state.expanded.add("archived");
        if (!state.archivedLoaded) await loadPage("archived");
        return emit();
      },
      loadMoreActive: () => loadPage("active", { append: true }),
      loadMoreArchived: () => loadPage("archived", { append: true }),
      refresh,
      async create(displayName) {
        if (!state.activeLoaded) await loadPage("active");
        const currentDefault = state.activeItems.find((item) => item.is_default);
        if (!currentDefault) throw new TypeError("The current default Wiki is unavailable.");
        await transport.createPersonalWiki({
          displayName: required(displayName, "displayName"),
          expectedRowVersion: currentDefault.row_version,
          operationId: operationIdFactory(),
        });
        await refresh();
        await onChanged();
        return emit();
      },
      async rename(profile, displayName) { const value = await mutate("rename", profile, { displayName }); await onChanged(); return value; },
      async setDefault(profile) { const value = await mutate("default", profile); await onChanged(); return value; },
      async archive(profile, reason = null) { const value = await mutate("archive", profile, { reason }); await onChanged(); return value; },
      async restore(profile, makeDefault = false) { const value = await mutate("restore", profile, { makeDefault }); await onChanged(); return value; },
      async restoreConversationWiki() {
        if (!state.historyOnly || !state.conversationSpaceId) return emit();
        if (!state.archivedLoaded) await loadPage("archived");
        const profile = state.archivedItems.find((item) => item.space_id === state.conversationSpaceId);
        if (!profile) throw new TypeError("The archived conversation Wiki is unavailable.");
        return controller.restore(profile, false);
      },
      async startFresh() {
        if (!state.activeLoaded) await loadPage("active");
        const profile = state.activeItems.find((item) => item.is_default) || state.activeItems[0];
        if (!profile) throw new TypeError("An active personal Wiki is required.");
        await startFreshConversation(profile.space_id);
        state.selectedSpaceId = profile.space_id;
        return emit();
      },
      async select(profile) {
        if (!profile || profile.lifecycle_status !== "active") throw new TypeError("Only an active Wiki can be selected.");
        await selectSpace(profile.space_id);
        state.selectedSpaceId = profile.space_id;
        return emit();
      },
      reorder(sourceSpaceId, targetSpaceId) {
        required(sourceSpaceId, "sourceSpaceId");
        required(targetSpaceId, "targetSpaceId");
        const sourceIndex = state.activeItems.findIndex((item) => item.space_id === sourceSpaceId);
        const targetIndex = state.activeItems.findIndex((item) => item.space_id === targetSpaceId);
        if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return emit();
        const next = state.activeItems.slice();
        const [moved] = next.splice(sourceIndex, 1);
        next.splice(targetIndex, 0, moved);
        state.activeItems = next;
        persistLoadedOrder();
        return emit();
      },
      move(spaceId, delta) {
        required(spaceId, "spaceId");
        if (delta !== -1 && delta !== 1) throw new TypeError("delta must be -1 or 1.");
        const index = state.activeItems.findIndex((item) => item.space_id === spaceId);
        const target = index + delta;
        if (index < 0 || target < 0 || target >= state.activeItems.length) return emit();
        return controller.reorder(spaceId, state.activeItems[target].space_id);
      },
      async search(query) {
        const value = required(query.trim(), "query");
        const page = await transport.searchPersonalWikis({ query: value, limit: 10 });
        state.searchItems = page.items.slice();
        state.searchNextCursor = page.next_cursor;
        return emit();
      },
      async openResult(item) {
        if (!item || typeof item !== "object") throw new TypeError("A search result is required.");
        return openSearchResult({
          itemId: required(item.item_id, "itemId"),
          revisionId: required(item.revision_id, "revisionId"),
          spaceId: required(item.source_space_id, "spaceId"),
        });
      },
    };
    return Object.freeze(controller);
  }

  function bindDom(controller, options) {
    if (!controller || typeof controller.subscribe !== "function") throw new TypeError("A Personal Wiki controller is required.");
    const host = options && options.host;
    const spaces = options && options.spaces;
    const promptText = options.promptText || (() => null);
    const confirmAction = options.confirmAction || (() => true);
    const onError = options.onError || (() => {});
    if (!host || typeof spaces !== "function") throw new TypeError("Personal Wiki DOM dependencies are required.");
    const documentRef = host.ownerDocument;
    let draggedSpaceId = null;

    function button(label, className, action) {
      const value = documentRef.createElement("button");
      value.type = "button";
      value.className = className;
      value.textContent = label;
      value.addEventListener("click", () => Promise.resolve(action()).catch(onError));
      return value;
    }

    function wikiRow(profile, state, archived = false) {
      const row = documentRef.createElement("div");
      row.className = "personal-wiki-row";
      row.setAttribute("role", "treeitem");
      row.dataset.spaceId = profile.space_id;
      row.classList.toggle("selected", state.selectedSpaceId === profile.space_id);
      row.classList.toggle("conversation-bound", state.conversationSpaceId === profile.space_id);
      if (!archived) {
        const handle = button("⋮⋮", "personal-wiki-drag-handle", () => {});
        handle.draggable = true;
        handle.setAttribute("aria-label", `${profile.display_name} Wiki 순서 이동`);
        handle.title = "드래그하거나 위/아래 방향키로 Wiki 순서 이동";
        handle.addEventListener("dragstart", (event) => {
          draggedSpaceId = profile.space_id;
          if (event.dataTransfer) {
            event.dataTransfer.effectAllowed = "move";
            event.dataTransfer.setData("text/plain", profile.space_id);
          }
          row.classList.add("dragging");
        });
        handle.addEventListener("dragend", () => {
          draggedSpaceId = null;
          row.classList.remove("dragging");
        });
        handle.addEventListener("keydown", (event) => {
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
          event.preventDefault();
          controller.move(profile.space_id, event.key === "ArrowUp" ? -1 : 1);
        });
        row.addEventListener("dragover", (event) => {
          if (!draggedSpaceId || draggedSpaceId === profile.space_id) return;
          event.preventDefault();
          if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
        });
        row.addEventListener("drop", (event) => {
          event.preventDefault();
          const source = draggedSpaceId || (event.dataTransfer && event.dataTransfer.getData("text/plain"));
          draggedSpaceId = null;
          if (source) controller.reorder(source, profile.space_id);
        });
        row.appendChild(handle);
      }
      const open = button(`${profile.display_name}${profile.is_default ? " · 기본" : ""}`, "personal-wiki-open", () => archived ? null : controller.select(profile));
      open.disabled = archived;
      row.appendChild(open);
      const actions = documentRef.createElement("span");
      actions.className = "personal-wiki-actions";
      if (archived) {
        actions.appendChild(button("복구", "ghost", async () => {
          if (confirmAction("restore", profile)) await controller.restore(profile, false);
        }));
      } else {
        actions.appendChild(button("이름", "ghost", async () => {
          const name = promptText("rename", profile);
          if (name) await controller.rename(profile, name);
        }));
        if (!profile.is_default) actions.appendChild(button("기본", "ghost", () => controller.setDefault(profile)));
        if (!profile.is_default) actions.appendChild(button("보관", "ghost danger", async () => {
          if (confirmAction("archive", profile)) await controller.archive(profile, "사용자 보관");
        }));
      }
      row.appendChild(actions);
      return row;
    }

    function render(state) {
      host.replaceChildren();
      host.className = "knowledge-wiki-navigation";
      host.setAttribute("role", "tree");
      host.setAttribute("aria-label", "지식공간 폴더");
      const controls = documentRef.createElement("div");
      controls.className = "personal-wiki-global-actions";
      controls.append(
        button("새 Wiki", "ghost", async () => {
          const name = promptText("create", null);
          if (name) await controller.create(name);
        }),
        button("모든 내 Wiki 검색", "ghost", async () => {
          const query = promptText("search", null);
          if (query) await controller.search(query);
        }),
        button("보관 Wiki", "ghost", () => controller.showArchived()),
      );
      host.appendChild(controls);

      if (state.historyOnly) {
        const warning = documentRef.createElement("div");
        warning.className = "personal-wiki-history-warning";
        const message = documentRef.createElement("span");
        message.textContent = "이 대화의 Wiki는 보관되어 기록 전용입니다.";
        warning.appendChild(message);
        if (state.archivedItems.some((item) => item.space_id === state.conversationSpaceId)) {
          warning.appendChild(button("이 Wiki 복구", "ghost", async () => {
            if (confirmAction("restore", { display_name: "이 대화의 Wiki" })) {
              await controller.restoreConversationWiki();
            }
          }));
        }
        warning.appendChild(button("활성 Wiki에서 새 대화", "ghost", () => controller.startFresh()));
        host.appendChild(warning);
      }

      // The top canonical scope selector owns Part/Team/Responsibility. This
      // tree is intentionally limited to the user's personal Wiki folders.
      for (const descriptor of GROUPS.filter((entry) => entry.kind === "personal")) {
        const group = documentRef.createElement("section");
        group.className = "knowledge-tree-group";
        const expanded = state.expanded.includes(descriptor.kind);
        const heading = button(`${expanded ? "▾" : "▸"} ${descriptor.label}`, "knowledge-tree-heading", () => controller.toggle(descriptor.kind));
        heading.setAttribute("role", "treeitem");
        heading.setAttribute("aria-expanded", expanded ? "true" : "false");
        heading.addEventListener("keydown", (event) => {
          if (["Enter", " ", "ArrowRight", "ArrowLeft"].includes(event.key)) {
            event.preventDefault();
            if ((event.key === "ArrowRight" && expanded) || (event.key === "ArrowLeft" && !expanded)) return;
            heading.click();
          }
        });
        group.appendChild(heading);
        if (expanded) {
          const children = documentRef.createElement("div");
          children.className = "knowledge-tree-children";
          children.setAttribute("role", "group");
          if (descriptor.kind === "personal") {
            for (const profile of state.activeItems) children.appendChild(wikiRow(profile, state));
            if (state.activeNextCursor) children.appendChild(button("Wiki 더 보기", "knowledge-tree-more", () => controller.loadMoreActive()));
          } else {
            const space = spaces().find((item) => item.kind === descriptor.kind);
            if (space) {
              const row = documentRef.createElement("button");
              row.type = "button";
              row.className = "knowledge-org-space";
              row.setAttribute("role", "treeitem");
              row.textContent = space.name;
              row.classList.toggle("selected", state.selectedSpaceId === space.id);
              row.addEventListener("click", () => Promise.resolve(options.selectOrgSpace(space)).catch(onError));
              children.appendChild(row);
            }
          }
          group.appendChild(children);
        }
        host.appendChild(group);
      }

      if (state.expanded.includes("archived")) {
        const archived = documentRef.createElement("section");
        archived.className = "knowledge-tree-group archived";
        const heading = documentRef.createElement("div");
        heading.className = "knowledge-tree-heading";
        heading.textContent = "보관 Wiki";
        archived.appendChild(heading);
        for (const profile of state.archivedItems) archived.appendChild(wikiRow(profile, state, true));
        if (state.archivedNextCursor) archived.appendChild(button("보관 Wiki 더 보기", "knowledge-tree-more", () => controller.loadMoreArchived()));
        host.appendChild(archived);
      }

      if (state.searchItems.length) {
        const results = documentRef.createElement("section");
        results.className = "personal-wiki-search-results";
        const title = documentRef.createElement("div");
        title.className = "knowledge-tree-heading";
        title.textContent = "모든 내 Wiki 검색 결과";
        results.appendChild(title);
        for (const item of state.searchItems) {
          results.appendChild(button(`${item.wiki_display_name} · ${item.title}`, "personal-wiki-search-result", () => controller.openResult(item)));
        }
        host.appendChild(results);
      }
      host.setAttribute("aria-busy", state.loading ? "true" : "false");
    }
    const unsubscribe = controller.subscribe(render);
    return Object.freeze({ destroy: unsubscribe, render: () => render(controller.state()) });
  }

  return Object.freeze({ GROUPS, PAGE_SIZE, bindDom, createController });
});
