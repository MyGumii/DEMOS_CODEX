/* Optional canonical browser bridge. It activates only when the canonical API probe succeeds. */
(function knowledgeCoreBrowserModule(root, factory) {
  const api = factory(root);
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.KnowledgeCoreBrowser = api;
})(typeof globalThis === "object" ? globalThis : this, function buildBrowserModule(root) {
  "use strict";

  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  let bridge = null;
  let bootPromise = null;
  let bootResolved = false;
  let bootResult = false;

  function messageText(value, transportModule) {
    return transportModule.messageText(value);
  }

  function createBridge(options) {
    if (!options || typeof options !== "object") throw new TypeError("Browser bridge options are required.");
    const transport = options.transport;
    const scopeUI = options.scopeUI;
    const state = options.state;
    const chat = options.chat;
    if (!transport || !scopeUI || !state || !chat) throw new TypeError("Canonical browser dependencies are required.");
    let controller = null;
    let binding = null;
    let ensurePromise = null;
    let publicationBatchController = null;
    let publicationController = null;
    let publicationAvailability = "not_probed";
    let publicationProbeError = null;
    let personalWikiBinding = null;
    let personalWikiController = null;
    let personalWikiScopeSubscription = null;
    let pendingCreateOperationId = null;
    let activeEditor = null;
    let leaveHandler = null;
    let managementRequestGeneration = 0;
    let managementRenderGeneration = 0;
    let itemOpenGeneration = 0;
    let systemDocRequestGeneration = 0;
    let systemDocScopeGeneration = 0;
    let systemDocScopeSubscription = null;
    let renderedManagementSpaceId = null;
    let lastManagementQuery = "";
    let publicationRefresh = null;
    let externalRefreshHandle = null;
    let externalRefreshPromise = null;
    let externalFocusHandler = null;
    let externalVisibilityHandler = null;
    let destroyed = false;
    const managementClosureBySpace = new Map();
    const publicationRefreshSleep = options.publicationRefreshSleep
      || ((ms) => new Promise((resolve) => root.setTimeout(resolve, ms)));
    const pendingTurnOperations = new Map();
    const pendingTurnPromises = new Map();
    const serverPendingTurns = new Map();
    let pendingReadGeneration = 0;
    const recentEdgeSuppressions = new Map();
    const scheduleHeartbeat = options.setIntervalImpl || ((callback, delay) => root.setInterval(callback, delay));
    const cancelHeartbeat = options.clearIntervalImpl || ((handle) => root.clearInterval(handle));
    const heartbeatIntervalMs = options.heartbeatIntervalMs === undefined ? 30000 : options.heartbeatIntervalMs;
    const externalRefreshIntervalMs = options.externalRefreshIntervalMs === undefined
      ? 30000 : options.externalRefreshIntervalMs;
    const leaseTokenFactory = options.leaseTokenFactory || (
      options.transportModule && options.transportModule.createLeaseToken
    );
    if (
      typeof scheduleHeartbeat !== "function"
      || typeof cancelHeartbeat !== "function"
      || !Number.isSafeInteger(heartbeatIntervalMs)
      || heartbeatIntervalMs < 1000
    ) {
      throw new TypeError("heartbeatIntervalMs is invalid.");
    }
    if (options.enableExternalRefresh === true && (
      !Number.isSafeInteger(externalRefreshIntervalMs)
      || externalRefreshIntervalMs < 5000
      || externalRefreshIntervalMs > 300000
    )) throw new TypeError("externalRefreshIntervalMs is invalid.");

    function ageLabel(seconds) {
      if (seconds === null || seconds === undefined) return "not reviewed";
      if (!Number.isSafeInteger(seconds) || seconds < 0) return "unknown";
      if (seconds < 60) return `${seconds}s`;
      if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
      if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
      return `${Math.floor(seconds / 86400)}d`;
    }

    function appendManagementLine(documentRef, text, className = "item", target = options.managementRoot) {
      const row = documentRef.createElement("div");
      row.className = className;
      row.textContent = text;
      target.appendChild(row);
      return row;
    }

    function appendButton(documentRef, text, className, callback, target = options.managementRoot) {
      const button = documentRef.createElement("button");
      button.type = "button";
      button.className = className;
      button.textContent = text;
      if (typeof button.addEventListener === "function") {
        button.addEventListener("click", () => Promise.resolve(callback()).catch(options.onError || (() => {})));
      }
      target.appendChild(button);
      return button;
    }

    function appendItemPager(documentRef, spaceId, results, target = options.managementRoot) {
      const page = results && results.list;
      if (!page || page.has_more !== true || typeof page.next_cursor !== "string" || !page.next_cursor) {
        return null;
      }
      let loading = false;
      const requestGeneration = managementRequestGeneration;
      const renderGeneration = managementRenderGeneration;
      const scopeGeneration = systemDocScopeGeneration;
      const isCurrent = () => controller && managementIsVisible()
        && controller.state().selectedSpaceId === spaceId
        && requestGeneration === managementRequestGeneration
        && renderGeneration === managementRenderGeneration
        && scopeGeneration === systemDocScopeGeneration;
      const button = appendButton(
        documentRef,
        `문서 더 보기 (현재 ${Array.isArray(page.items) ? page.items.length : 0}개)`,
        "knowledge-tree-more canonical-items-more",
        async () => {
          if (loading || !isCurrent()) return false;
          loading = true;
          button.disabled = true;
          try {
            let next;
            try {
              next = await transport.management(spaceId, "list", {
                cursor: page.next_cursor,
                limit: 50,
              });
            } catch (error) {
              if (isCurrent()) throw error;
              return false;
            }
            // A manual page may finish after automatic publication has painted
            // a newer complete view. Never restore its captured partial state.
            if (!isCurrent()) return false;
            if (
              typeof page.corpus_closure_sha256 === "string"
              && typeof next.corpus_closure_sha256 === "string"
              && page.corpus_closure_sha256 !== next.corpus_closure_sha256
            ) {
              await renderManagement();
              return false;
            }
            const items = [];
            const seen = new Set();
            for (const item of [
              ...(Array.isArray(page.items) ? page.items : []),
              ...(Array.isArray(next.items) ? next.items : []),
            ]) {
              if (!item || typeof item.id !== "string" || seen.has(item.id)) continue;
              seen.add(item.id);
              items.push(item);
            }
            if (
              next.has_more === true
              && (
                typeof next.next_cursor !== "string"
                || !next.next_cursor
                || next.next_cursor === page.next_cursor
              )
            ) {
              throw new TypeError("The item page cursor did not advance.");
            }
            results.list = Object.assign({}, next, { items });
            renderManagementResults(spaceId, results);
            return true;
          } finally {
            loading = false;
            if (isCurrent()) button.disabled = false;
          }
        },
        target,
      );
      return button;
    }

    function editorMatches(spaceId, itemId) {
      return !!activeEditor && activeEditor.spaceId === spaceId && activeEditor.itemId === itemId;
    }

    function stopHeartbeat(editor = activeEditor) {
      if (editor && editor.heartbeatHandle !== null) {
        cancelHeartbeat(editor.heartbeatHandle);
        editor.heartbeatHandle = null;
      }
    }

    function editingState() {
      if (!activeEditor) return null;
      return Object.freeze({
        draft_preserved: typeof activeEditor.draft === "string",
        expires_at: activeEditor.expiresAt,
        fencing_generation: activeEditor.fencingGeneration,
        item_id: activeEditor.itemId,
        lease_id: activeEditor.leaseId,
        space_id: activeEditor.spaceId,
        status: activeEditor.status,
      });
    }

    async function copyAndReleaseEditorDraft() {
      if (!activeEditor) return false;
      const editor = activeEditor;
      if (typeof options.copyDraft === "function") {
        await options.copyDraft(editor.draft);
      } else if (root.navigator && root.navigator.clipboard && typeof root.navigator.clipboard.writeText === "function") {
        await root.navigator.clipboard.writeText(editor.draft);
      } else {
        throw new TypeError("Draft copy is unavailable.");
      }
      return releaseEditor(editor);
    }

    async function releaseEditor(
      editor = activeEditor,
      { reload = true, resumeOnFailure = true } = {}
    ) {
      if (!editor || editor !== activeEditor) return false;
      stopHeartbeat(editor);
      try {
        await transport.releaseItemLease({
          editorSessionId: editor.editorSessionId,
          fencingGeneration: editor.fencingGeneration,
          itemId: editor.itemId,
          leaseId: editor.leaseId,
          operationId: options.operationIdFactory(),
          rawToken: editor.rawToken,
          spaceId: editor.spaceId,
        });
      } catch (error) {
        if (resumeOnFailure && activeEditor === editor && editor.status === "held") {
          beginHeartbeat(editor);
        }
        throw error;
      }
      if (activeEditor === editor) activeEditor = null;
      if (reload) await openItem(editor.spaceId, editor.itemId);
      return true;
    }

    async function destroy() {
      destroyed = true;
      publicationRefresh = null;
      if (externalRefreshHandle !== null) {
        cancelHeartbeat(externalRefreshHandle);
        externalRefreshHandle = null;
      }
      if (externalFocusHandler && root && typeof root.removeEventListener === "function") {
        root.removeEventListener("focus", externalFocusHandler);
        externalFocusHandler = null;
      }
      if (externalVisibilityHandler && root && root.document
          && typeof root.document.removeEventListener === "function") {
        root.document.removeEventListener("visibilitychange", externalVisibilityHandler);
        externalVisibilityHandler = null;
      }
      systemDocRequestGeneration += 1;
      if (typeof systemDocScopeSubscription === "function") systemDocScopeSubscription();
      const editor = activeEditor;
      stopHeartbeat(editor);
      if (leaveHandler && root && typeof root.removeEventListener === "function") {
        root.removeEventListener("pagehide", leaveHandler);
        leaveHandler = null;
      }
      if (editor) {
        try {
          await releaseEditor(editor, { reload: false, resumeOnFailure: false });
        } catch (_) {
          stopHeartbeat(editor);
          activeEditor = null;
        }
      }
      if (binding && typeof binding.destroy === "function") binding.destroy();
      if (personalWikiBinding && typeof personalWikiBinding.destroy === "function") personalWikiBinding.destroy();
      if (typeof personalWikiScopeSubscription === "function") personalWikiScopeSubscription();
    }

    async function heartbeatEditor(editor) {
      if (!editor || editor !== activeEditor || editor.status !== "held") return;
      try {
        const result = await transport.heartbeatItemLease({
          editorSessionId: editor.editorSessionId,
          fencingGeneration: editor.fencingGeneration,
          itemId: editor.itemId,
          leaseId: editor.leaseId,
          rawToken: editor.rawToken,
          spaceId: editor.spaceId,
        });
        if (activeEditor === editor) editor.expiresAt = result.expires_at;
      } catch (error) {
        if (activeEditor === editor) {
          editor.status = "conflict";
          editor.conflict = error;
          stopHeartbeat(editor);
          renderItemDetail(
            editor.spaceId,
            editor.itemId,
            editor.detail,
            editor.history,
            editor.distribution,
            editor.graph,
          );
          if (options.dispatchEvent) options.dispatchEvent("knowledge:edit-conflict", {
            code: error && error.code,
            draftPreserved: true,
            itemId: editor.itemId,
            spaceId: editor.spaceId,
          });
        }
      }
    }

    function beginHeartbeat(editor) {
      stopHeartbeat(editor);
      editor.heartbeatHandle = scheduleHeartbeat(() => heartbeatEditor(editor), heartbeatIntervalMs);
    }

    async function beginEdit(spaceId, itemId, preservedDraft = null) {
      if (editorMatches(spaceId, itemId)) return editingState();
      if (activeEditor && !editorMatches(spaceId, itemId)) {
        throw new TypeError("Finish or cancel the current edit first.");
      }
      const preflightDetail = await transport.getItem(spaceId, itemId);
      if (!Array.isArray(preflightDetail.capabilities) || !preflightDetail.capabilities.includes("edit")) {
        throw new TypeError("This item is read-only.");
      }
      if (typeof leaseTokenFactory !== "function") {
        throw new TypeError("A secure lease-token factory is unavailable.");
      }
      const editorSessionId = options.operationIdFactory();
      const rawToken = leaseTokenFactory(options.cryptoImpl || root.crypto);
      const lease = await transport.acquireItemLease({
        editorSessionId,
        itemId,
        operationId: options.operationIdFactory(),
        rawToken,
        spaceId,
      });
      let detail;
      let history;
      let graph;
      try {
        const graphRequest = typeof transport.management === "function"
          ? transport.management(spaceId, "graph").catch(() => null)
          : Promise.resolve(null);
        [detail, history, graph] = await Promise.all([
          transport.getItem(spaceId, itemId),
          transport.listRevisions(spaceId, itemId, { limit: 100 }),
          graphRequest,
        ]);
        if (
          !detail.item
          || detail.item.current_revision_id !== lease.base_revision_id
          || !Number.isSafeInteger(detail.item.row_version)
          || detail.item.row_version < 1
        ) {
          throw new TypeError("The acquired lease base is not bound to the current item.");
        }
      } catch (error) {
        let releaseError = null;
        try {
          await transport.releaseItemLease({
            editorSessionId,
            fencingGeneration: lease.fencing_generation,
            itemId,
            leaseId: lease.lease_id,
            operationId: options.operationIdFactory(),
            rawToken,
            spaceId,
          });
        } catch (caught) {
          releaseError = caught;
        }
        if (releaseError) {
          activeEditor = {
            cancelPending: false,
            conflict: error,
            detail: preflightDetail,
            draft: preservedDraft === null ? preflightDetail.markdown : preservedDraft,
            editorSessionId,
            expiresAt: lease.expires_at,
            expectedRevisionId: lease.base_revision_id,
            expectedRowVersion: preflightDetail.item.row_version,
            fencingGeneration: lease.fencing_generation,
            graph: null,
            heartbeatHandle: null,
            history: { items: [] },
            itemId,
            leaseId: lease.lease_id,
            rawToken,
            spaceId,
            status: "conflict",
          };
          renderItemDetail(
            spaceId,
            itemId,
            preflightDetail,
            activeEditor.history,
            null,
            null,
          );
        }
        throw error;
      }
      activeEditor = {
        cancelPending: false,
        conflict: null,
        detail,
        draft: preservedDraft === null ? detail.markdown : preservedDraft,
        editorSessionId,
        expiresAt: lease.expires_at,
        expectedRevisionId: lease.base_revision_id,
        expectedRowVersion: detail.item.row_version,
        fencingGeneration: lease.fencing_generation,
        graph,
        heartbeatHandle: null,
        history,
        itemId,
        leaseId: lease.lease_id,
        rawToken,
        spaceId,
        status: "held",
      };
      beginHeartbeat(activeEditor);
      renderItemDetail(spaceId, itemId, detail, history, null, graph);
      if (options.dispatchEvent) options.dispatchEvent("knowledge:edit-started", {
        itemId, spaceId,
      });
      return editingState();
    }

    async function saveEditor(editor, markdown, reason) {
      if (!editor || editor !== activeEditor || editor.status !== "held") {
        throw new TypeError("There is no active editable lease.");
      }
      editor.draft = markdown;
      try {
        const result = await transport.updateItem({
          editorSessionId: editor.editorSessionId,
          expectedRevisionId: editor.expectedRevisionId,
          expectedRowVersion: editor.expectedRowVersion,
          fencingGeneration: editor.fencingGeneration,
          itemId: editor.itemId,
          leaseId: editor.leaseId,
          markdown: editor.draft,
          operationId: options.operationIdFactory(),
          rawToken: editor.rawToken,
          reason,
          spaceId: editor.spaceId,
        });
        stopHeartbeat(editor);
        if (activeEditor === editor) activeEditor = null;
        await openItem(editor.spaceId, editor.itemId);
        return result;
      } catch (error) {
        editor.status = "conflict";
        editor.conflict = error;
        stopHeartbeat(editor);
        renderItemDetail(
          editor.spaceId,
          editor.itemId,
          editor.detail,
          editor.history,
          editor.distribution,
          editor.graph,
        );
        if (options.dispatchEvent) options.dispatchEvent("knowledge:edit-conflict", {
          code: error && error.code,
          draftPreserved: true,
          itemId: editor.itemId,
          spaceId: editor.spaceId,
        });
        throw error;
      }
    }

    async function reacquireAndCompare() {
      if (!activeEditor) return false;
      const old = activeEditor;
      const draft = old.draft;
      const oldRevisionId = old.expectedRevisionId;
      try {
        await releaseEditor(old);
      } catch (error) {
        if (!error || error.code !== "LEASE_LOST") throw error;
        stopHeartbeat(old);
        if (activeEditor === old) activeEditor = null;
      }
      await beginEdit(old.spaceId, old.itemId, draft);
      const editor = activeEditor;
      if (editor && oldRevisionId !== editor.expectedRevisionId) {
        try {
          editor.comparison = await transport.compareRevisions(
            editor.spaceId, editor.itemId, oldRevisionId, editor.expectedRevisionId
          );
        } catch (_) { editor.comparison = null; }
        renderItemDetail(
          editor.spaceId,
          editor.itemId,
          editor.detail,
          editor.history,
          editor.distribution,
          editor.graph,
        );
      }
      return true;
    }

    async function reloadManagementViews({ spaceId, surfaces, query = "", listResult }) {
      const requestGeneration = ++managementRequestGeneration;
      renderManagementLoading(spaceId);
      async function loadSurface(surface) {
        if (surface === "list" && listResult !== undefined) return listResult;
        if (surface === "search") {
          return query ? transport.management(spaceId, "search", { query }) : null;
        } else if (surface === "pins") {
          const current = controller && controller.state();
          const badge = current && current.conversationBadge;
          if (badge && badge.space_id === spaceId) {
            const detail = await transport.getConversation(badge.conversation_id);
            return {
              has_more: false,
              items: detail.pins.filter((pin) => pin.space_id === spaceId),
              next_cursor: null,
              space_id: spaceId,
            };
          }
          return { has_more: false, items: [], next_cursor: null, space_id: spaceId };
        } else if (surface === "candidates") {
          try {
            return await transport.management(spaceId, surface);
          } catch (error) {
            if (!error || ![403, 404].includes(error.status)) throw error;
            return {
              has_more: false,
              items: [],
              next_cursor: null,
              space_id: spaceId,
              unavailable: true,
            };
          }
        }
        return transport.management(spaceId, surface);
      }

      // Attach rejection handlers immediately: optional diagnostics may reject
      // before the slower document/graph requests finish. Delay propagation of
      // real failures, not registration of the rejection handler.
      const shadowMetricsPromise = (typeof transport.getCurationShadowMetrics === "function"
        ? transport.getCurationShadowMetrics(spaceId, { sampleSize: 25 })
        : Promise.resolve(null)).then(
        (value) => ({ value }), (error) => ({ error }),
      );
      const surfaceLoads = surfaces.map((surface) => ({
        promise: loadSurface(surface),
        surface,
      }));
      // Start every surface in parallel, but let the document list paint as
      // soon as it is ready instead of waiting for Graph/Log diagnostics.
      const entriesPromise = Promise.all(
        surfaceLoads.map(async ({ surface, promise }) => [surface, await promise]),
      ).then((value) => ({ value }), (error) => ({ error }));
      const listLoad = surfaceLoads.find(({ surface }) => surface === "list");
      if (listLoad) {
        const list = await listLoad.promise;
        const current = controller && controller.state();
        if (
          requestGeneration === managementRequestGeneration
          && current
          && current.selectedSpaceId === spaceId
        ) {
          renderManagementResults(spaceId, { list, partial_loading: true });
        }
      }
      const entries = await entriesPromise;
      if (entries.error) throw entries.error;
      const results = Object.fromEntries(entries.value);
      const shadowMetrics = await shadowMetricsPromise;
      if (shadowMetrics.error && ![403, 404].includes(shadowMetrics.error.status)) {
        throw shadowMetrics.error;
      }
      results.shadow_metrics = shadowMetrics.value || null;
      const current = controller && controller.state();
      if (
        requestGeneration !== managementRequestGeneration
        || !current
        || current.selectedSpaceId !== spaceId
      ) {
        return results;
      }
      renderManagementResults(spaceId, results);
      if (results.list && typeof results.list.corpus_closure_sha256 === "string") {
        managementClosureBySpace.set(spaceId, results.list.corpus_closure_sha256);
      }
      if (
        typeof State === "object" && State.graphMode
        && root && typeof root.SecondBrainGraph === "object"
      ) {
        const selected = current.spaces.find((space) => space.id === spaceId);
        if (selected) {
          configureGraph(selected);
          root.SecondBrainGraph.render();
        }
      }
      if (options.dispatchEvent) {
        options.dispatchEvent("knowledge:management-reload", {
          results,
          spaceId,
          surfaces,
        });
      }
      return results;
    }

    function canonicalGraphPayload(value) {
      const nodes = (value && value.nodes || []).map((node) => ({
        data: {
          capabilities: Array.isArray(node.capabilities) ? [...node.capabilities] : ["read"],
          content_preview: "",
          current_revision_id: node.current_revision_id,
          id: node.id,
          inherited: node.inherited === true,
          kind: "canonical",
          label: node.title,
          legacy: true,
          memory_type: "semantic",
          origin: node.origin,
          overlay_grant_id: node.overlay_grant_id,
          row_version: node.row_version,
          selected_view_space_id: node.selected_view_space_id,
          source_space_id: node.source_space_id,
          state: node.state,
          title: node.title,
          topics: [],
          view_type: "canonical",
        },
      }));
      const edges = (value && value.edges || []).map((edge) => ({
        data: {
          capabilities: Array.isArray(edge.capabilities) ? [...edge.capabilities] : ["read"],
          confidence_ppm: edge.confidence_ppm,
          detector_generation: edge.detector_generation,
          direction: edge.direction,
          edge_scope: edge.edge_scope,
          edge_status: edge.edge_status,
          edge_type: edge.edge_type,
          home_space_id: edge.home_space_id,
          id: edge.id,
          inherited: edge.inherited === true,
          metadata: {
            confidence_ppm: edge.confidence_ppm,
            detector_generation: edge.detector_generation,
            model_name: edge.model_name,
            model_provider: edge.model_provider,
            origin: edge.origin,
            prompt_schema_version: edge.prompt_schema_version,
            scoring_version: edge.scoring_version,
            semantic_evidence_sha256: edge.semantic_evidence_sha256,
            source_evidence_location: edge.source_evidence_location,
            source_evidence_revision_id: edge.source_evidence_revision_id,
            source_evidence_span_sha256: edge.source_evidence_span_sha256,
            target_evidence_location: edge.target_evidence_location,
            target_evidence_revision_id: edge.target_evidence_revision_id,
            target_evidence_span_sha256: edge.target_evidence_span_sha256,
          },
          model_name: edge.model_name,
          model_provider: edge.model_provider,
          origin: edge.origin,
          prompt_schema_version: edge.prompt_schema_version,
          row_version: edge.row_version,
          scoring_version: edge.scoring_version,
          selected_view_space_id: edge.selected_view_space_id,
          semantic_evidence_sha256: edge.semantic_evidence_sha256,
          source: edge.source_item_id,
          source_space_id: edge.source_space_id,
          target: edge.target_item_id,
          target_space_id: edge.target_space_id,
        },
      }));
      return {
        counts: { edges: edges.length, nodes: nodes.length },
        edges,
        generation: value && value.generation,
        nodes,
        space_id: value && value.space_id,
      };
    }

    function configureGraph(selected) {
      root.SecondBrainGraph.useProvider(
        async () => canonicalGraphPayload(
          await transport.management(selected.id, "graph"),
        ),
        {
          // Canonical publication/edit actions live in the knowledge panel.
          // The legacy graph mutations write to a different filesystem store,
          // so the canonical graph is deliberately exploration-only here.
          readOnly: true,
          scopeLabel: scopeUI.spaceDisplayLabel(selected),
          openDocument: (node) => openItem(selected.id, node.id),
        },
      );
    }

    async function openGraph() {
      if (
        !controller
        || !root
        || !root.document
        || root.document.documentElement.dataset.keepLegacyGraph !== "true"
        || typeof root.SecondBrainGraph !== "object"
        || typeof root.setMainView !== "function"
      ) return false;
      if (typeof State === "object" && State.graphMode) {
        root.setMainView("chat");
        return true;
      }
      const current = controller.state();
      const selected = current.spaces.find((space) => space.id === current.selectedSpaceId);
      if (!selected) return false;
      configureGraph(selected);
      root.setMainView("graph");
      return true;
    }

    async function openCanonicalSystemDoc(name, selected, results) {
      const modal = options.modal || (typeof Modal === "object" ? Modal : null);
      if (!root || !root.document || !modal) return;
      const requestGeneration = ++systemDocRequestGeneration;
      const managementGeneration = managementRequestGeneration;
      const scopeGeneration = systemDocScopeGeneration;
      const body = root.document.createElement("div");
      const metadata = root.document.createElement("div");
      metadata.className = "sb-detail-meta";
      metadata.textContent = `${name}.md · 전체 목록을 불러오는 중…`;
      const source = root.document.createElement("pre");
      source.className = "sb-source-excerpt";
      body.append(metadata, source);
      modal.open({
        title: name === "index" ? "Index" : "Log",
        body,
        footButtons: [{ label: "닫기" }],
      });
      function ownsModal() {
        const background = root.document.getElementById("modalBg");
        const host = root.document.getElementById("modalBody");
        return requestGeneration === systemDocRequestGeneration
          && background && background.dataset.open === "1" && host && host.contains(body);
      }
      function currentScope() {
        const current = controller && controller.state();
        return managementGeneration === managementRequestGeneration
          && scopeGeneration === systemDocScopeGeneration
          && current && current.selectedSpaceId === selected.id;
      }
      try {
        const value = await transport.getSystemDocument(selected.id, name);
        // Closing/replacing the modal, switching A→B→A, or a new management
        // refresh must not let an older request paint over the current view.
        if (!ownsModal()) return;
        if (!currentScope()) {
          metadata.textContent = "지식 선택 또는 목록이 변경되었습니다. 닫고 다시 열어 주세요.";
          return;
        }
        const closure = results.list && results.list.corpus_closure_sha256;
        if (closure && value.corpus_closure_sha256 !== closure) {
          metadata.textContent = "문서가 변경되어 목록을 새로 고쳤습니다. 닫고 다시 열어 주세요.";
          await renderManagement();
          return;
        }
        metadata.textContent = `${name}.md · 전체 ${value.entry_count}개 · 조회 시점의 기록`;
        source.textContent = value.content;
      } catch (error) {
        if (!ownsModal() || !currentScope()) return;
        metadata.textContent = "전체 목록을 불러오지 못했습니다. 닫고 다시 시도해 주세요.";
        throw error;
      }
    }

    function originLabel(origin) {
      return ({
        inherited: "상속 읽기",
        native: "현재 공간 원본",
        overlay: "배포 overlay",
      })[origin] || "출처 확인 필요";
    }

    function managementOriginLabel(origin, selected) {
      if (origin === "native") return "현재 공간 문서";
      if (origin === "overlay") return "공유 문서";
      if (origin === "inherited") {
        if (selected && selected.kind === "team") return "Part에서 상속";
        if (selected && selected.kind === "responsibility") return "하위 조직에서 상속";
        return "상속 문서";
      }
      return "읽기 전용";
    }

    function correctionReason(edge, action) {
      const message = action === "suppress"
        ? `이 edge(${edge.edge_type})를 숨기는 감사 사유를 입력하세요.`
        : `이 edge(${edge.edge_type})의 억제를 해제하고 재평가할 사유를 입력하세요.`;
      const value = typeof options.promptText === "function"
        ? options.promptText(`semantic_edge_${action}`, edge)
        : root && typeof root.prompt === "function" ? root.prompt(message, "") : null;
      const reason = typeof value === "string" ? value.trim() : "";
      if (!reason || reason.length > 1000) throw new TypeError("1~1000자의 감사 사유가 필요합니다.");
      return reason;
    }

    function renderGraphGovernance(documentRef, spaceId, selected, graph, target) {
      if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) return;
      appendManagementLine(
        documentRef,
        `Graph · generation ${graph.generation} · nodes ${graph.nodes.length} · edges ${graph.edges.length}`,
        "section-title canonical-graph-governance-title",
        target,
      );
      const authority = selected && selected.native_authority;
      const canEditNative = Boolean(
        selected
        && Array.isArray(selected.capabilities)
        && selected.capabilities.includes("edit")
      );
      appendManagementLine(
        documentRef,
        canEditNative && authority
          ? `Native authority · role episode ${authority.role_assignment_generation} · ${(authority.capabilities || []).join(", ") || "read"}`
          : canEditNative
          ? "현재 공간의 원본 문서를 작성·수정할 수 있습니다. 상속·배포 문서는 읽기 전용입니다."
          : "이 범위는 읽기 전용입니다. 상속·overlay 문서와 foreign-home edge는 변경할 수 없습니다.",
        "item canonical-native-authority",
        target,
      );

      const visibleEdges = selected && selected.kind === "part"
        ? graph.edges.filter(edge => edge.edge_scope !== "aggregate")
        : graph.edges;
      if (selected && selected.kind === "part" && visibleEdges.length !== graph.edges.length) {
        const error = new TypeError("Part Graph 응답에 aggregate edge가 포함되어 표시를 차단했습니다.");
        if (typeof options.onError === "function") options.onError(error);
      }
      const clusters = new Map();
      const nodeTitles = new Map(graph.nodes.map(node => [
        node.id,
        typeof node.title === "string" && node.title.trim()
          ? node.title.trim() : "이름을 확인할 수 없는 문서",
      ]));
      for (const node of graph.nodes) {
        const key = node.source_space_id || "unknown";
        const value = clusters.get(key) || { count: 0, origins: new Set() };
        value.count += 1;
        value.origins.add(node.origin || "unknown");
        clusters.set(key, value);
      }
      if (selected && ["team", "responsibility"].includes(selected.kind)) {
        let clusterNumber = 0;
        for (const cluster of clusters.values()) {
          clusterNumber += 1;
          appendManagementLine(
            documentRef,
            `지식 출처 ${clusterNumber} · 문서 ${cluster.count}개 · ${[...cluster.origins].map(originLabel).join(", ")}`,
            "item canonical-graph-cluster",
            target,
          );
        }
      }

      for (const edge of visibleEdges) {
        const confidence = Number.isSafeInteger(edge.confidence_ppm)
          ? `${(edge.confidence_ppm / 10000).toFixed(2)}%`
          : "n/a";
        const detector = edge.detector_generation === null || edge.detector_generation === undefined
          ? "detector n/a"
          : `detector g${edge.detector_generation}`;
        const model = edge.model_name
          ? `${edge.model_provider || "model"}/${edge.model_name}`
          : "deterministic";
        const relation = ({
          related: "관련",
          extends: "확장",
          contradicts: "상충",
          references: "참조",
        })[edge.edge_type] || edge.edge_type || "관계";
        const status = ({
          active: "연결됨",
          needs_revalidation: "재검토 필요",
          suppressed: "숨김",
        })[edge.edge_status] || edge.edge_status || "상태 미확인";
        const row = appendManagementLine(
          documentRef,
          `${nodeTitles.get(edge.source_item_id) || "목록에 없는 문서"} → ${nodeTitles.get(edge.target_item_id) || "목록에 없는 문서"} · ${relation} · ${confidence} · ${status}`,
          `item canonical-graph-edge canonical-edge-${edge.edge_status || "unknown"}`,
          target,
        );
        row.dataset.canonicalEdgeId = edge.id;
        const disclosure = documentRef.createElement("details");
        disclosure.className = "canonical-graph-edge-diagnostics";
        const summary = documentRef.createElement("summary");
        summary.textContent = "연결 근거와 기술 정보";
        disclosure.appendChild(summary);
        const evidence = documentRef.createElement("pre");
        evidence.className = "canonical-graph-edge-evidence";
        evidence.textContent = JSON.stringify({
          edge_id: edge.id,
          edge_scope: edge.edge_scope,
          edge_type: edge.edge_type,
          edge_status: edge.edge_status,
          home_space_id: edge.home_space_id,
          confidence,
          origin: originLabel(edge.origin),
          detector,
          model,
          semantic_evidence_sha256: edge.semantic_evidence_sha256,
          source: {
            item_id: edge.source_item_id,
            location: edge.source_evidence_location,
            revision_id: edge.source_evidence_revision_id,
            span_sha256: edge.source_evidence_span_sha256,
          },
          target: {
            item_id: edge.target_item_id,
            location: edge.target_evidence_location,
            revision_id: edge.target_evidence_revision_id,
            span_sha256: edge.target_evidence_span_sha256,
          },
        }, null, 2);
        disclosure.appendChild(evidence);
        target.appendChild(disclosure);
        const requiredCapability = edge.edge_scope === "aggregate"
          ? "aggregate_edge_correct" : "local_edge_correct";
        if (
          edge.home_space_id === spaceId
          && Array.isArray(edge.capabilities)
          && edge.capabilities.includes(requiredCapability)
          && ["active", "needs_revalidation"].includes(edge.edge_status)
          && typeof transport.suppressSemanticEdge === "function"
        ) {
          appendButton(
            documentRef,
            "이 edge만 억제",
            "canonical-semantic-edge-suppress",
            async () => {
              const result = await transport.suppressSemanticEdge({
                edgeId: edge.id,
                expectedRowVersion: edge.row_version,
                operationId: options.operationIdFactory(),
                reason: correctionReason(edge, "suppress"),
                spaceId,
              });
              recentEdgeSuppressions.set(`${spaceId}:${edge.id}`, { edge, result });
              await renderManagement();
              return result;
            },
            target,
          );
        }
      }

      for (const [key, value] of recentEdgeSuppressions) {
        if (!key.startsWith(`${spaceId}:`)) continue;
        appendManagementLine(
          documentRef,
          `Suppressed · ${value.edge.edge_type} · reevaluation ${value.result.reevaluation_queued ? "queued" : "not queued"}`,
          "item canonical-semantic-edge-suppressed",
          target,
        );
        if (typeof transport.liftSemanticEdgeSuppression === "function") {
          appendButton(
            documentRef,
            "억제 해제 후 재평가",
            "canonical-semantic-edge-lift",
            async () => {
              const result = await transport.liftSemanticEdgeSuppression({
                edgeId: value.edge.id,
                expectedRowVersion: value.result.row_version,
                operationId: options.operationIdFactory(),
                reason: correctionReason(value.edge, "lift"),
                spaceId,
              });
              recentEdgeSuppressions.delete(key);
              await renderManagement();
              return result;
            },
            target,
          );
        }
      }
    }

    function openGraphGovernance(selected, graph) {
      if (!selected || !graph || !options.modal || typeof options.modal.open !== "function") {
        return false;
      }
      const documentRef = options.managementRoot.ownerDocument;
      const body = documentRef.createElement("div");
      body.className = "canonical-graph-governance-modal";
      renderGraphGovernance(documentRef, selected.id, selected, graph, body);
      options.modal.open({
        title: `${scopeUI.spaceDisplayLabel(selected)} Graph 상세`,
        body,
        footButtons: [{ label: "닫기" }],
      });
      return true;
    }

    async function openGraphDetails() {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      const current = controller.state();
      const selected = current.spaces.find(
        (space) => space.id === current.selectedSpaceId,
      );
      if (!selected) return false;
      const graph = await transport.management(selected.id, "graph");
      return openGraphGovernance(selected, graph);
    }

    function renderManagementLoading(spaceId) {
      if (!options.managementRoot || renderedManagementSpaceId === spaceId) return;
      const documentRef = options.managementRoot.ownerDocument;
      const current = controller && controller.state();
      const selected = current && current.spaces.find((item) => item.id === spaceId);
      options.managementRoot.replaceChildren();
      const row = documentRef.createElement("div");
      row.className = "item canonical-management-loading";
      const label = selected && typeof scopeUI.spaceDisplayLabel === "function"
        ? scopeUI.spaceDisplayLabel(selected)
        : spaceId;
      row.textContent = `${label} 문서를 불러오는 중…`;
      options.managementRoot.appendChild(row);
    }

    function renderManagementResults(spaceId, results) {
      if (!options.managementRoot) return;
      managementRenderGeneration += 1;
      renderedManagementSpaceId = spaceId;
      const documentRef = options.managementRoot.ownerDocument;
      const selected = controller && controller.state().spaces.find((item) => item.id === spaceId);
      options.managementRoot.replaceChildren();
      const heading = documentRef.createElement("div");
      heading.className = "section-title";
      const selectedLabel = selected && typeof scopeUI.spaceDisplayLabel === "function"
        ? scopeUI.spaceDisplayLabel(selected)
        : (selected ? selected.name : spaceId);
      heading.textContent = `Canonical Knowledge · ${selectedLabel}`;
      const useLegacyManagementSurface = Boolean(
        selected
        && typeof Knowledge === "object"
        && typeof Knowledge._topControls === "function"
        && typeof Knowledge._systemDocsBlock === "function"
      );
      if (!useLegacyManagementSurface) options.managementRoot.appendChild(heading);
      if (useLegacyManagementSurface) {
        const items = (results.list && results.list.items) || [];
        const findings = (results.candidates && results.candidates.items) || [];
        const counts = {
          applied: findings.filter((item) => item.status === "applied").length,
          deleted: 0,
          pending: findings.filter((item) => item.status === "pending").length,
          rejected: findings.filter((item) => item.status === "rejected").length,
        };
        const controls = Knowledge._topControls(
          results.list && results.list.has_more === true ? `${items.length}+` : items.length,
          counts,
        );
        const title = controls.querySelector(".section-title span");
        if (title) title.textContent = `${scopeUI.spaceDisplayLabel(selected)} Wiki`;
        if (root.WorkspaceFeatures && title) {
          title.parentElement.appendChild(root.WorkspaceFeatures.headerControls(spaceId, openGraphDetails));
        }
        options.managementRoot.appendChild(controls);
        if (selected.kind === "team" || selected.kind === "responsibility") {
          const inheritanceNote = documentRef.createElement("div");
          inheritanceNote.className = "canonical-inheritance-note";
          inheritanceNote.textContent = "현재 Part 지식을 읽기 전용으로 상속합니다. 이 범위의 Index · Log · Graph가 함께 갱신됩니다.";
          options.managementRoot.appendChild(inheritanceNote);
        }
        const systemDocs = Knowledge._systemDocsBlock();
        if (root.WorkspaceFeatures) systemDocs.querySelector('[data-system-doc="graph"]')?.remove();
        options.managementRoot.appendChild(systemDocs);
        systemDocs.querySelectorAll("[data-system-doc]").forEach((button) => {
          button.disabled = results.partial_loading === true;
          button.addEventListener("click", () => Promise.resolve().then(() => {
            const name = button.dataset.systemDoc || "";
            if (name === "graph") return openGraphGovernance(selected, results.graph);
            return openCanonicalSystemDoc(name, selected, results);
          }).catch(options.onError || (() => {})));
        });
        for (const item of items) {
          const row = documentRef.createElement("div");
          row.className = "item";
          row.dataset.canonicalItemId = item.id;
          const name = documentRef.createElement("span");
          name.className = "name";
          name.title = item.title;
          name.textContent = item.title;
          row.append(name);
          if (root.WorkspaceFeatures) {
            row.append(root.WorkspaceFeatures.itemControls(spaceId, item, renderManagement));
          }
          row.addEventListener("click", () => openItem(spaceId, item.id).catch(options.onError || (() => {})));
          options.managementRoot.appendChild(row);
        }
        appendItemPager(documentRef, spaceId, results);
        if (results.partial_loading === true) {
          appendManagementLine(
            documentRef,
            "문서 목록을 먼저 표시했습니다. Index · Log · Graph를 동기화하는 중…",
            "item canonical-management-loading",
          );
        }
        if (!items.length && typeof Knowledge._empty === "function") {
          options.managementRoot.appendChild(Knowledge._empty("아직 이 지식공간에 문서가 없습니다."));
        }
        return;
      }
      const projection = results.index_log;
      if (projection) {
        const status = documentRef.createElement("div");
        status.className = "item";
        status.textContent = `Projection ${projection.status} · generation ${projection.built_generation}/${projection.desired_generation}`;
        options.managementRoot.appendChild(status);
      }
      const metrics = results.shadow_metrics;
      if (metrics) {
        const precision = metrics.precision_denominator === 0
          ? "not yet sampled"
          : `${Math.floor(metrics.precision_ppm / 10000)}% (${metrics.precision_numerator}/${metrics.precision_denominator})`;
        appendManagementLine(
          documentRef,
          `Shadow precision ${precision} · automatic effects ${metrics.model_automatic_execution_count} · T3 automatic effects ${metrics.t3_model_automatic_execution_count} · ${metrics.report_status}`,
          `item canonical-shadow-metrics canonical-shadow-${metrics.report_status}`,
        );
      }
      for (const item of (results.list && results.list.items) || []) {
        const row = documentRef.createElement("div");
        row.className = "item";
        row.dataset.canonicalItemId = item.id;
        row.textContent = `${item.title} · ${managementOriginLabel(item.origin, selected)}`;
        if (typeof row.addEventListener === "function") {
          row.addEventListener("click", () => openItem(spaceId, item.id).catch(options.onError || (() => {})));
        }
        options.managementRoot.appendChild(row);
      }
      appendItemPager(documentRef, spaceId, results);
      for (const event of (results.activity && results.activity.items) || []) {
        const row = documentRef.createElement("div");
        row.className = "item";
        row.dataset.canonicalActivityId = event.event_uuid;
        const actor = event.actor && event.actor.display_name ? event.actor.display_name : "Unknown actor";
        const reason = event.reason ? ` · ${event.reason}` : "";
        row.textContent = `${event.action} · ${event.outcome} · ${actor} · ${event.occurred_at}${reason}`;
        options.managementRoot.appendChild(row);
      }
      for (const finding of (results.candidates && results.candidates.items) || []) {
        const row = documentRef.createElement("div");
        row.className = "item";
        row.dataset.canonicalFindingId = finding.id;
        row.textContent = `${finding.finding_kind} · ${finding.status} · ${finding.risk_tier}/${finding.severity} · ${finding.visibility_effect} · ${finding.required_reviewer}`;
        if (typeof row.addEventListener === "function" && typeof transport.getFinding === "function") {
          row.addEventListener("click", () => openFinding(spaceId, finding.id).catch(options.onError || (() => {})));
        }
        options.managementRoot.appendChild(row);
      }
    }

    async function openFinding(spaceId, findingId) {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      if (
        typeof spaceId !== "string" || !UUID_RE.test(spaceId)
        || typeof findingId !== "string" || !UUID_RE.test(findingId)
      ) {
        throw new TypeError("Finding identifiers are invalid.");
      }
      if (controller.state().selectedSpaceId !== spaceId) {
        throw new TypeError("The finding space is not selected.");
      }
      if (typeof transport.getFinding !== "function") {
        throw new TypeError("Canonical curation transport is unavailable.");
      }
      const detail = await transport.getFinding(spaceId, findingId);
      if (!detail.finding || detail.finding.id !== findingId || detail.finding.space_id !== spaceId) {
        throw new TypeError("Canonical finding binding is invalid.");
      }
      renderFindingDetail(spaceId, detail.finding);
      if (options.dispatchEvent) {
        options.dispatchEvent("knowledge:finding-opened", { findingId, spaceId });
      }
      return detail;
    }

    function renderFindingDetail(spaceId, finding) {
      if (!options.managementRoot) return;
      const documentRef = options.managementRoot.ownerDocument;
      options.managementRoot.replaceChildren();
      appendManagementLine(
        documentRef,
        `${finding.finding_kind} · ${finding.status}`,
        "section-title canonical-finding-title",
      );
      appendManagementLine(
        documentRef,
        `Risk ${finding.risk_tier} · severity ${finding.severity} · reviewer ${finding.required_reviewer}`,
      );
      appendManagementLine(
        documentRef,
        `Visibility ${finding.visibility_effect} · action ${finding.proposed_action} · version ${finding.row_version}`,
      );
      appendManagementLine(
        documentRef,
        `Detector ${finding.detector_name}@${finding.detector_version} · rule ${finding.rule_version}`,
      );
      if (finding.model_id !== null) {
        appendManagementLine(
          documentRef,
          `Shadow model ${finding.model_id} · prompt ${finding.prompt_version} · confidence ${finding.confidence}`,
          "item canonical-finding-model-warning",
        );
      }
      appendManagementLine(
        documentRef,
        `Source ${finding.source_item_id}@${finding.source_revision_id} · target ${finding.target_item_id || "none"}@${finding.target_revision_id || "none"}`,
      );
      if (finding.reviewed_by !== null) {
        appendManagementLine(
          documentRef,
          `Reviewed ${finding.reviewed_at} by ${finding.reviewed_by} · ${finding.review_reason}`,
          "item canonical-finding-review-lifecycle",
        );
      }
      const evidence = documentRef.createElement("pre");
      evidence.className = "item canonical-finding-evidence";
      evidence.textContent = JSON.stringify({
        evidence: finding.evidence,
        proposed_action_evidence: finding.proposed_action_evidence,
      }, null, 2);
      options.managementRoot.appendChild(evidence);

      const actionEvidence = finding.proposed_action_evidence || {};
      if (
        typeof actionEvidence.proposal_id === "string"
        && UUID_RE.test(actionEvidence.proposal_id)
        && typeof transport.previewChangeProposal === "function"
      ) {
        appendButton(
          documentRef,
          "Preview proposal",
          "canonical-finding-proposal-preview",
          async () => {
            const preview = await transport.previewChangeProposal({
              proposalId: actionEvidence.proposal_id,
              spaceId,
            });
            const pane = documentRef.createElement("pre");
            pane.className = "item canonical-proposal-preview";
            pane.textContent = (preview.panes || [])
              .map((value) => `## ${value.label}\n${value.markdown}`)
              .join("\n\n");
            options.managementRoot.appendChild(pane);
            return preview;
          },
        );
      }

      if (
        finding.status === "pending"
        && finding.required_reviewer !== "none"
        && typeof transport.reviewFinding === "function"
      ) {
        const reason = documentRef.createElement("textarea");
        reason.className = "canonical-finding-review-reason";
        reason.placeholder = "Required review reason";
        options.managementRoot.appendChild(reason);
        for (const [decision, label] of [["approve", "Approve"], ["reject", "Reject"], ["ignore", "Ignore"]]) {
          appendButton(
            documentRef,
            label,
            `canonical-finding-review canonical-finding-${decision}`,
            async () => {
              const value = typeof reason.value === "string" ? reason.value.trim() : "";
              if (!value) throw new TypeError("A review reason is required.");
              const result = await transport.reviewFinding({
                decision,
                expectedFindingRowVersion: finding.row_version,
                findingId: finding.id,
                operationId: options.operationIdFactory(),
                reason: value,
                spaceId,
              });
              await openFinding(spaceId, finding.id);
              return result;
            },
          );
        }
      }
    }

    function renderAutomaticRelations(documentRef, spaceId, itemId, graph, target) {
      appendManagementLine(
        documentRef,
        "\uC790\uB3D9 \uC5F0\uAD00 \uBB38\uC11C",
        "section-title canonical-automatic-relation-title",
        target,
      );
      if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
        appendManagementLine(
          documentRef,
          "\uC790\uB3D9 \uC5F0\uAD00 \uADF8\uB798\uD504\uB97C \uBD88\uB7EC\uC624\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.",
          "item canonical-automatic-relation-empty",
          target,
        );
        return;
      }
      const nodes = new Map(graph.nodes.map(node => [node.id, node]));
      const relations = graph.edges
        .filter(edge => edge.source_item_id === itemId || edge.target_item_id === itemId)
        .filter(edge => !["suppressed", "inactive"].includes(edge.edge_status))
        .map(edge => ({
          edge,
          relatedItemId: edge.source_item_id === itemId ? edge.target_item_id : edge.source_item_id,
        }))
        .filter(value => typeof value.relatedItemId === "string");
      if (!relations.length) {
        appendManagementLine(
          documentRef,
          "\uC790\uB3D9\uC73C\uB85C \uC5F0\uACB0\uB41C \uAD00\uB828 \uBB38\uC11C\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.",
          "item canonical-automatic-relation-empty",
          target,
        );
        return;
      }
      relations.sort((left, right) => {
        const confidenceDelta = (right.edge.confidence_ppm || 0) - (left.edge.confidence_ppm || 0);
        return confidenceDelta || left.relatedItemId.localeCompare(right.relatedItemId);
      });
      for (const { edge, relatedItemId } of relations) {
        const node = nodes.get(relatedItemId);
        const confidence = Number.isSafeInteger(edge.confidence_ppm)
          ? `${(edge.confidence_ppm / 10000).toFixed(2)}%`
          : "n/a";
        const title = node && node.title ? node.title : relatedItemId;
        const row = appendManagementLine(
          documentRef,
          `${title} \u00B7 ${edge.edge_type || "related"} \u00B7 ${confidence} \u00B7 ${edge.edge_scope || "local"} \u00B7 ${edge.edge_status || "active"}`,
          "item canonical-automatic-relation",
          target,
        );
        row.dataset.canonicalEdgeId = edge.id;
        row.dataset.canonicalRelatedItemId = relatedItemId;
        if (relatedItemId !== itemId && nodes.has(relatedItemId) && typeof row.addEventListener === "function") {
          row.addEventListener("click", () => openItem(spaceId, relatedItemId).catch(options.onError || (() => {})));
        }
      }
    }

    function renderDirectReferences(documentRef, spaceId, itemId, detail, target) {
      appendManagementLine(
        documentRef,
        "\uC9C1\uC811 \uCC38\uC870 \uBB38\uC11C",
        "section-title canonical-reference-title",
        target,
      );
      const references = Array.isArray(detail.references) ? [...detail.references] : [];
      references.sort((left, right) => left.ordinal - right.ordinal);
      if (!references.length) {
        appendManagementLine(
          documentRef,
          "\uBCF8\uBB38\uC5D0 \uC0AC\uC6A9\uC790\uAC00 \uC9C1\uC811 \uC9C0\uC815\uD55C \uCC38\uC870 \uBB38\uC11C\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.",
          "item canonical-reference-empty",
          target,
        );
        return;
      }
      for (const reference of references) {
        if (reference.availability_status !== "available" || !reference.target) {
          const unavailable = appendManagementLine(
            documentRef,
            `${reference.ordinal}. \uCC38\uC870\uD55C \uBB38\uC11C\uB97C \uD604\uC7AC \uC5F4 \uC218 \uC5C6\uC2B5\uB2C8\uB2E4.`,
            "item canonical-reference-unavailable",
            target,
          );
          unavailable.dataset.canonicalReferenceId = reference.id;
          continue;
        }
        const referenceTarget = reference.target;
        const newer = referenceTarget.newer_revision_available ? " \u00B7 \uCD5C\uC2E0 revision \uC788\uC74C" : "";
        const row = appendManagementLine(
          documentRef,
          `${reference.ordinal}. ${referenceTarget.title} \u00B7 ${originLabel(referenceTarget.origin)} \u00B7 ${referenceTarget.source_space_id}${newer}`,
          "item canonical-reference-available",
          target,
        );
        row.dataset.canonicalReferenceId = reference.id;
        row.dataset.canonicalReferenceItemId = referenceTarget.item_id;
        if (referenceTarget.item_id !== itemId && typeof row.addEventListener === "function") {
          row.addEventListener("click", () => openItem(spaceId, referenceTarget.item_id).catch(options.onError || (() => {})));
        }
      }
    }

    function renderExplicitReferences(documentRef, spaceId, itemId, detail, target) {
      appendManagementLine(documentRef, "참조 문서", "section-title canonical-reference-title", target);
      const references = Array.isArray(detail.references) ? [...detail.references] : [];
      references.sort((left, right) => left.ordinal - right.ordinal);
      if (!references.length) {
        appendManagementLine(documentRef, "등록된 명시적 참조가 없습니다.", "item canonical-reference-empty", target);
        return;
      }
      for (const reference of references) {
        if (reference.availability_status !== "available" || !reference.target) {
          const unavailable = appendManagementLine(
            documentRef,
            `${reference.ordinal}. 참조할 수 없는 문서 · 권한 변경 또는 문서 비활성`,
            "item canonical-reference-unavailable",
            target,
          );
          unavailable.dataset.canonicalReferenceId = reference.id;
          continue;
        }
        const referenceTarget = reference.target;
        const newer = referenceTarget.newer_revision_available ? " · 최신 revision 있음" : "";
        const row = appendManagementLine(
          documentRef,
          `${reference.ordinal}. ${referenceTarget.title} · ${originLabel(referenceTarget.origin)} · ${referenceTarget.source_space_id}${newer}`,
          "item canonical-reference-available",
          target,
        );
        row.dataset.canonicalReferenceId = reference.id;
        row.dataset.canonicalReferenceItemId = referenceTarget.item_id;
        if (referenceTarget.item_id !== itemId && typeof row.addEventListener === "function") {
          row.addEventListener("click", () => openItem(spaceId, referenceTarget.item_id).catch(options.onError || (() => {})));
        }
      }
    }

    function renderReferencePicker(documentRef, spaceId, itemId, editor, draft, target) {
      if (editor.status !== "held" || typeof transport.management !== "function") return;
      appendManagementLine(documentRef, "참조 문서 추가", "section-title canonical-reference-picker-title", target);
      const query = documentRef.createElement("input");
      query.type = "search";
      query.className = "canonical-reference-picker-query";
      query.placeholder = "현재 지식 범위에서 문서 검색";
      target.appendChild(query);
      const resultsHost = documentRef.createElement("div");
      resultsHost.className = "canonical-reference-picker-results";
      target.appendChild(resultsHost);
      appendButton(documentRef, "검색", "canonical-reference-picker-search", async () => {
        const text = typeof query.value === "string" ? query.value.trim() : "";
        if (!text) throw new TypeError("참조 문서 검색어가 필요합니다.");
        const result = await transport.management(spaceId, "search", { query: text });
        if (typeof resultsHost.replaceChildren === "function") resultsHost.replaceChildren();
        else resultsHost.textContent = "";
        for (const hit of (result.items || []).filter(value => value.item_id !== itemId)) {
          const button = documentRef.createElement("button");
          button.type = "button";
          button.className = "canonical-reference-picker-result";
          button.textContent = `${hit.title} · ${originLabel(hit.origin)} · ${hit.source_space_id}`;
          button.dataset.canonicalReferenceTargetId = hit.item_id;
          button.addEventListener("click", () => {
            const token = `[[item:${hit.item_id}]]`;
            const start = Number.isSafeInteger(draft.selectionStart) ? draft.selectionStart : draft.value.length;
            const end = Number.isSafeInteger(draft.selectionEnd) ? draft.selectionEnd : start;
            draft.value = `${draft.value.slice(0, start)}${token}${draft.value.slice(end)}`;
            editor.draft = draft.value;
            if (typeof draft.setSelectionRange === "function") {
              draft.setSelectionRange(start + token.length, start + token.length);
            }
            if (typeof draft.focus === "function") draft.focus();
          });
          resultsHost.appendChild(button);
        }
        if (!(result.items || []).some(value => value.item_id !== itemId)) {
          const empty = documentRef.createElement("div");
          empty.className = "canonical-reference-picker-empty";
          empty.textContent = "현재 권한 범위에서 참조할 문서를 찾지 못했습니다.";
          resultsHost.appendChild(empty);
        }
        return result;
      }, target);
    }

    function distributionReason(promptText) {
      const value = typeof options.promptText === "function"
        ? options.promptText("distribution_reason", { prompt: promptText })
        : root && typeof root.prompt === "function" ? root.prompt(promptText, "") : null;
      const reason = typeof value === "string" ? value.trim() : "";
      if (!reason || reason.length > 1000) throw new TypeError("1~1000자의 배포 감사 사유가 필요합니다.");
      return reason;
    }

    function renderDistributionControls(documentRef, spaceId, itemId, detail, distribution, target) {
      if (!distribution) return;
      appendManagementLine(documentRef, "하위 조직 읽기 배포", "section-title canonical-distribution-title", target);
      appendManagementLine(
        documentRef,
        "원본은 현재 Team/담당 native 공간에 한 번만 남고, 대상에는 읽기 전용 overlay가 생성됩니다.",
        "item canonical-distribution-help",
        target,
      );
      const targets = distribution.targets && distribution.targets.items || [];
      if ((detail.capabilities || []).includes("distribution_grant") && targets.length) {
        const select = documentRef.createElement("select");
        select.className = "canonical-distribution-target";
        for (const target of targets) {
          const option = documentRef.createElement("option");
          option.value = target.org_unit_id;
          option.textContent = `${target.org_path.join(" > ")} · ${target.kind}`;
          select.appendChild(option);
        }
        target.appendChild(select);
        appendButton(documentRef, "선택 조직에 읽기 배포", "canonical-distribution-create", async () => {
          const target = targets.find(value => value.org_unit_id === select.value);
          if (!target) throw new TypeError("서버가 허용한 배포 대상을 선택해야 합니다.");
          const result = await transport.createDistribution({
            itemId,
            operationId: options.operationIdFactory(),
            reason: distributionReason("이 revision을 하위 조직에 배포하는 사유를 입력하세요."),
            revisionId: detail.item.current_revision_id,
            spaceId,
            target,
          });
          await openItem(spaceId, itemId);
          return result;
        }, target);
      } else if (!targets.length) {
        appendManagementLine(documentRef, "현재 역할에서 배포할 수 있는 하위 조직이 없습니다.", "item canonical-distribution-empty", target);
      }
      for (const grant of distribution.grants && distribution.grants.items || []) {
        const active = grant.revoked_at === null;
        const row = appendManagementLine(
          documentRef,
          `${active ? "활성" : "회수됨"} · ${grant.target_scope_kind}:${grant.target_scope_id} · revision ${grant.source_revision_id} · ${grant.reason}`,
          `item canonical-distribution-grant ${active ? "active" : "revoked"}`,
          target,
        );
        row.dataset.canonicalDistributionGrantId = grant.grant_id;
        if (active && (detail.capabilities || []).includes("distribution_revoke")) {
          appendButton(documentRef, "이 배포만 회수", "canonical-distribution-revoke", async () => {
            const result = await transport.revokeDistribution({
              breakGlass: false,
              expectedRowVersion: grant.row_version,
              grantId: grant.grant_id,
              operationId: options.operationIdFactory(),
              reason: distributionReason("이 읽기 배포를 회수하는 사유를 입력하세요."),
              spaceId,
            });
            await openItem(spaceId, itemId);
            return result;
          }, target);
        }
      }
    }

    function renderItemDetail(spaceId, itemId, detail, history, distribution = null, graph = null, sourceArtifacts = null) {
      if (!options.managementRoot) return;
      const documentRef = options.managementRoot.ownerDocument;
      const useModal = options.modal && typeof options.modal.open === "function";
      const detailRoot = useModal ? documentRef.createElement("div") : options.managementRoot;
      if (useModal) detailRoot.className = "canonical-item-modal";
      else options.managementRoot.replaceChildren();
      if (!useModal) appendManagementLine(documentRef, detail.item.title, "section-title", detailRoot);
      const selected = controller && controller.state().spaces.find((item) => item.id === spaceId);
      const selectedLabel = selected && typeof scopeUI.spaceDisplayLabel === "function"
        ? scopeUI.spaceDisplayLabel(selected)
        : "현재 지식공간";
      appendManagementLine(
        documentRef,
        `${selectedLabel} · ${managementOriginLabel(detail.origin, selected)}`,
        "item canonical-item-origin",
        detailRoot,
      );
      appendManagementLine(documentRef, `State ${detail.item.state} · curation ${detail.item.curation_state} · capabilities ${(detail.capabilities || []).join(", ") || "read only"}`, "item", detailRoot);
      appendManagementLine(documentRef, `Content age ${ageLabel(detail.timeline.content_age_seconds)} · review age ${ageLabel(detail.timeline.review_age_seconds)} · updated ${detail.timeline.last_updated_at}`, "item", detailRoot);
      const edges = detail.lifecycle.edge_counts;
      const references = detail.lifecycle.reference_counts;
      appendManagementLine(documentRef, `Impact ${edges.total} edges · ${references.total} references · ${references.conversation_pins} conversation pins`, "item", detailRoot);
      if (detail.lifecycle.deactivation) {
        const inactive = detail.lifecycle.deactivation;
        appendManagementLine(documentRef, `Inactive ${ageLabel(inactive.inactive_duration_seconds)} · ${inactive.deactivated_by.display_name} · ${inactive.deactivated_at} · ${inactive.reason} · purge eligible ${inactive.purge_eligible_at}`, "item", detailRoot);
      }
      if (detail.lease) {
        appendManagementLine(documentRef, `Editing: ${detail.lease.holder_display} · heartbeat ${detail.lease.heartbeat_at} · expires ${detail.lease.expires_at}`, "item", detailRoot);
      }
      let body;
      if (root.CanonicalMarkdown && typeof root.CanonicalMarkdown.render === "function") {
        body = root.CanonicalMarkdown.render(documentRef, detail.markdown);
      } else {
        body = documentRef.createElement("pre");
        body.className = "item canonical-item-markdown";
        body.textContent = detail.markdown === null ? "Body unavailable" : detail.markdown;
      }
      detailRoot.appendChild(body);
      renderAutomaticRelations(documentRef, spaceId, itemId, graph, detailRoot);
      renderDirectReferences(documentRef, spaceId, itemId, detail, detailRoot);
      renderDistributionControls(documentRef, spaceId, itemId, detail, distribution, detailRoot);
      const editor = editorMatches(spaceId, itemId) ? activeEditor : null;
      if (editor) {
        appendManagementLine(documentRef, editor.status === "held"
          ? `Lease held until ${editor.expiresAt}`
          : "Edit conflict: your local draft is preserved.", "item canonical-editor-status", detailRoot);
        const draft = documentRef.createElement("textarea");
        draft.className = "canonical-item-editor";
        draft.value = editor.draft;
        draft.readOnly = editor.status !== "held";
        if (typeof draft.addEventListener === "function") {
          draft.addEventListener("input", () => { editor.draft = draft.value; });
        }
        detailRoot.appendChild(draft);
        renderReferencePicker(documentRef, spaceId, itemId, editor, draft, detailRoot);
        if (editor.status === "held") {
          const reason = documentRef.createElement("input");
          reason.className = "canonical-edit-reason";
          reason.type = "text";
          reason.value = "Edit completed";
          detailRoot.appendChild(reason);
          appendButton(documentRef, "Complete edit", "canonical-edit-complete", () => saveEditor(editor, draft.value, reason.value), detailRoot);
          appendButton(documentRef, "Cancel", "canonical-edit-cancel", () => {
            editor.draft = draft.value;
            editor.cancelPending = true;
            renderItemDetail(spaceId, itemId, detail, history, distribution, graph, sourceArtifacts);
          }, detailRoot);
          if (editor.cancelPending) {
            appendButton(documentRef, "Copy draft and release", "canonical-edit-copy-release", copyAndReleaseEditorDraft, detailRoot);
            appendButton(documentRef, "Discard draft and release", "canonical-edit-discard-release", () => releaseEditor(editor), detailRoot);
          }
        } else {
          appendButton(documentRef, "View current", "canonical-edit-view-current", async () => {
            const current = await transport.getItem(spaceId, itemId);
            editor.detail = current;
            renderItemDetail(spaceId, itemId, current, history, distribution, graph, sourceArtifacts);
          }, detailRoot);
          appendButton(documentRef, "Copy my draft", "canonical-edit-copy", async () => {
            if (typeof options.copyDraft === "function") return options.copyDraft(editor.draft);
            if (root.navigator && root.navigator.clipboard && root.navigator.clipboard.writeText) return root.navigator.clipboard.writeText(editor.draft);
            throw new TypeError("Draft copy is unavailable.");
          }, detailRoot);
          appendButton(documentRef, "Reacquire and compare", "canonical-edit-reacquire", reacquireAndCompare, detailRoot);
        }
        if (editor.comparison && Array.isArray(editor.comparison.hunks)) {
          appendManagementLine(documentRef, "Canonical changes since your original lease", "section-title", detailRoot);
          for (const hunk of editor.comparison.hunks) appendManagementLine(documentRef, hunk.lines.join(""), "item canonical-diff-hunk", detailRoot);
        }
      } else if (detail.item.state === "active" && (detail.capabilities || []).includes("edit") && !detail.lease && typeof transport.acquireItemLease === "function") {
        appendButton(documentRef, "Edit", "canonical-edit-start", () => beginEdit(spaceId, itemId), detailRoot);
      }
      if (!editor && detail.lease && (detail.capabilities || []).includes("force_release") && typeof transport.forceReleaseItemLease === "function") {
        const reason = documentRef.createElement("input");
        reason.className = "canonical-force-release-reason";
        reason.type = "text";
        reason.placeholder = "Mandatory audit reason";
        detailRoot.appendChild(reason);
        const confirmation = documentRef.createElement("input");
        confirmation.className = "canonical-force-release-confirmation";
        confirmation.type = "text";
        confirmation.placeholder = "Type FORCE_RELEASE";
        detailRoot.appendChild(confirmation);
        appendButton(documentRef, "Force release", "canonical-force-release", async () => {
          if (confirmation.value !== "FORCE_RELEASE" || !reason.value) throw new TypeError("Force-release confirmation and reason are required.");
          await transport.forceReleaseItemLease({
            confirmation: confirmation.value,
            itemId,
            operationId: options.operationIdFactory(),
            reason: reason.value,
            spaceId,
          });
          await openItem(spaceId, itemId);
        }, detailRoot);
      }
      appendManagementLine(documentRef, "Revision history", "section-title", detailRoot);
      for (const revision of history.items || []) {
        const row = appendManagementLine(documentRef, `Revision ${revision.revision_no} · ${revision.created_by_display} · ${revision.created_at} · ${revision.reason}`, "item", detailRoot);
        row.dataset.canonicalRevisionId = revision.id;
        if (typeof row.addEventListener === "function") {
          row.addEventListener("click", async () => {
            try {
              const selected = await transport.getRevision(spaceId, itemId, revision.id);
              appendManagementLine(documentRef, selected.revision.markdown === null ? "Revision body unavailable" : selected.revision.markdown, "item canonical-revision-body", detailRoot);
            } catch (error) {
              (options.onError || (() => {}))(error);
            }
          });
        }
      }
      if ((history.items || []).length >= 2) {
        const newer = history.items[0];
        const older = history.items[1];
        const compare = appendManagementLine(documentRef, `Compare revision ${older.revision_no} → ${newer.revision_no}`, "item canonical-revision-compare", detailRoot);
        if (typeof compare.addEventListener === "function") {
          compare.addEventListener("click", async () => {
            try {
              const result = await transport.compareRevisions(spaceId, itemId, older.id, newer.id);
              for (const hunk of result.hunks) appendManagementLine(documentRef, hunk.lines.join(""), "item canonical-diff-hunk", detailRoot);
            } catch (error) {
              (options.onError || (() => {}))(error);
            }
          });
        }
      }
      if (useModal) {
        options.modal.open({
          title: detail.item.title,
          body: detailRoot,
          closeable: !editor,
          footButtons: editor ? [] : [{ label: "닫기" }],
        });
      }
    }

    async function activate() {
      const available = await transport.probe();
      if (!available) return false;
      try {
        publicationAvailability = typeof transport.probePublication === "function" && await transport.probePublication()
          ? "available" : "absent";
      } catch (error) {
        publicationAvailability = "error";
        publicationProbeError = error;
      }
      controller = scopeUI.createController({
        createConversation: (request) => transport.createConversation(request),
        onConversationStarted: async (event) => {
          state.session_id = event.newConversationId;
          state.messages = [];
          state.compactSummary = "";
          state.compactedMessageCount = 0;
          state.lastContextStatus = null;
          if (state.sessionCache) state.sessionCache.set(event.newConversationId, []);
          chat.renderMessages([]);
          await refreshVisibleManagement();
          if (options.onConversationStarted) await options.onConversationStarted(event);
        },
        operationIdFactory: options.operationIdFactory,
        readSnapshot: (request) => transport.readSnapshot(request),
        reloadManagementViews,
      });
      let lastSystemDocSpace = null;
      systemDocScopeSubscription = controller.subscribe((value) => {
        if (value.selectedSpaceId !== lastSystemDocSpace) {
          systemDocScopeGeneration += 1;
          lastSystemDocSpace = value.selectedSpaceId;
        }
      });
      if (root && typeof root.addEventListener === "function") {
        leaveHandler = () => {
          const editor = activeEditor;
          stopHeartbeat(editor);
          if (editor) releaseEditor(
            editor, { reload: false, resumeOnFailure: false }
          ).catch(() => {
            stopHeartbeat(editor);
            if (activeEditor === editor) activeEditor = null;
          });
        };
        root.addEventListener("pagehide", leaveHandler);
      }
      if (publicationAvailability === "available" && options.publicationUI && options.publicationRenderer) {
        const publicationRenderer = { ...options.publicationRenderer };
        for (const method of ["showResults", "showBatchResults"]) {
          if (typeof options.publicationRenderer[method] !== "function") continue;
          publicationRenderer[method] = (...args) => {
            const result = options.publicationRenderer[method](...args);
            if (method === "showResults" || args[0]?.succeededCount > 0) {
              const targets = method === "showResults" ? args[0].targets
                : args[0].items.flatMap((item) => item.targets || []);
              // Commit creates provisional items. Normal deterministic worker
              // processing makes them visible later; never retry publication.
              refreshPublishedManagement(targets).catch(() => {});
            }
            return result;
          };
        }
        publicationController = options.publicationUI.createController({
          operationIdFactory: options.operationIdFactory,
          renderer: publicationRenderer,
          transport,
        });
        if (typeof options.publicationUI.createBatchController === "function") {
          publicationBatchController = options.publicationUI.createBatchController({
            concurrency: 3,
            operationIdFactory: options.operationIdFactory,
            renderer: publicationRenderer,
            transport,
          });
        }
      }
      const canonicalConversation = typeof state.session_id === "string" && UUID_RE.test(state.session_id)
        ? state.session_id : null;
      if (!canonicalConversation) state.session_id = null;
      await controller.initialize({ mode: scopeUI.CHAT_MODE, conversationId: canonicalConversation });
      if (options.elements) {
        binding = scopeUI.bindDom(controller, options.elements, options.onError || (() => {}));
      }
      if (options.personalWikiUI && options.personalWikiRoot) {
        const personalWikiOrderKey = (() => {
          try {
            const raw = root.sessionStorage && root.sessionStorage.getItem("demos_user");
            const user = raw ? JSON.parse(raw) : null;
            const userId = user && typeof user.id === "string" ? user.id : "anonymous";
            return `second-brain.personal-wiki-order.v1.${userId}`;
          } catch (_) {
            return "second-brain.personal-wiki-order.v1.anonymous";
          }
        })();
        personalWikiController = options.personalWikiUI.createController({
          loadOrder: () => {
            try {
              const raw = root.localStorage && root.localStorage.getItem(personalWikiOrderKey);
              return raw ? JSON.parse(raw) : [];
            } catch (_) {
              return [];
            }
          },
          onChanged: async () => {
            const current = controller.state();
            await controller.initialize({
              conversationId: current.currentConversationId,
              mode: current.mode,
              selectedSpaceId: current.selectedSpaceId,
              preserveSelection: true,
              preserveMode: true,
            });
          },
          openSearchResult: async ({ itemId, spaceId }) => {
            const current = controller.state();
            let conversationId = current.currentConversationId;
            if (!current.conversationBadge || current.conversationBadge.space_id !== spaceId || !current.conversationBadge.accessible) {
              const created = await transport.createConversation({
                operationId: options.operationIdFactory(),
                spaceId,
              });
              conversationId = created.id;
              await controller.openConversation(conversationId);
              state.session_id = conversationId;
              state.messages = [];
              if (state.sessionCache) state.sessionCache.set(conversationId, []);
              chat.renderMessages([]);
            }
            await openItem(spaceId, itemId);
            if (options.dispatchEvent) options.dispatchEvent("knowledge:personal-wiki-search-opened", {
              conversationId, itemId, spaceId,
            });
            return conversationId;
          },
          operationIdFactory: options.operationIdFactory,
          saveOrder: (spaceIds) => {
            if (root.localStorage) root.localStorage.setItem(personalWikiOrderKey, JSON.stringify(spaceIds));
          },
          selectSpace: (spaceId) => selectSpaceAndStart(spaceId),
          startFreshConversation: (spaceId) => createFreshConversationForSpace(spaceId),
          transport,
        });
        personalWikiBinding = options.personalWikiUI.bindDom(personalWikiController, {
          confirmAction: (action, profile) => root.confirm(
            action === "archive"
              ? `"${profile.display_name}" Wiki를 보관할까요? 기존 대화는 기록 전용으로 남습니다.`
              : `"${profile.display_name}" Wiki를 복구할까요?`,
          ),
          host: options.personalWikiRoot,
          onError: options.onError || (() => {}),
          promptText: (action, profile) => root.prompt(
            action === "create" ? "새 Wiki 이름"
              : action === "rename" ? "Wiki 이름 변경"
              : "모든 내 Wiki 검색어",
            action === "rename" && profile ? profile.display_name : "",
          ),
          selectOrgSpace: (space) => selectSpaceAndStart(space.id),
          spaces: () => controller.state().spaces,
        });
        personalWikiScopeSubscription = controller.subscribe((value) => {
          if (personalWikiController) personalWikiController.syncScope(value);
        });
        await personalWikiController.initialize(controller.state());
      }
      if (options.enableExternalRefresh === true) {
        externalRefreshHandle = scheduleHeartbeat(
          () => refreshIfCorpusChanged(), externalRefreshIntervalMs,
        );
        if (root && typeof root.addEventListener === "function") {
          externalFocusHandler = () => { refreshIfCorpusChanged(); };
          root.addEventListener("focus", externalFocusHandler);
        }
        if (root && root.document && typeof root.document.addEventListener === "function") {
          externalVisibilityHandler = () => {
            if (!root.document.hidden) refreshIfCorpusChanged();
          };
          root.document.addEventListener("visibilitychange", externalVisibilityHandler);
        }
      }
      return true;
    }

    async function renderWikiNavigation() {
      if (!personalWikiController || !options.personalWikiRoot) return false;
      options.personalWikiRoot.hidden = false;
      personalWikiController.syncScope(controller.state());
      return true;
    }

    function setWikiNavigationVisible(visible) {
      if (options.personalWikiRoot) options.personalWikiRoot.hidden = !visible;
      if (!visible) {
        managementRequestGeneration += 1;
        publicationRefresh = null;
      }
    }

    async function ensureConversation() {
      const current = controller && controller.state().currentConversationId;
      if (current) {
        // The canonical controller is authoritative. A legacy/stale global
        // session ID can survive a server restart or an old session row; never
        // let Chat manufacture an `s_...` ID while a canonical conversation is
        // already selected.
        state.session_id = current;
        return current;
      }
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      if (ensurePromise) return ensurePromise;
      pendingCreateOperationId = pendingCreateOperationId || options.operationIdFactory();
      ensurePromise = (async () => {
        const selected = controller.state().selectedSpaceId;
        const created = await transport.createConversation({
          operationId: pendingCreateOperationId,
          spaceId: selected,
        });
        await controller.openConversation(created.id);
        state.session_id = created.id;
        state.messages = [];
        if (state.sessionCache) state.sessionCache.set(created.id, []);
        pendingCreateOperationId = null;
        return created.id;
      })();
      try { return await ensurePromise; }
      finally { ensurePromise = null; }
    }

    async function enterChat() {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      const current = controller.state();
      await controller.initialize({
        conversationId: current.currentConversationId,
        mode: scopeUI.CHAT_MODE,
        selectedSpaceId: current.selectedSpaceId,
        preserveSelection: true,
      });
      return controller.state();
    }

    async function whenScopeSettled() {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      return controller.whenSettled();
    }

    async function enterManagement() {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      const current = controller.state();
      await controller.initialize({
        conversationId: current.currentConversationId,
        mode: scopeUI.MANAGEMENT_MODE,
        selectedSpaceId: current.selectedSpaceId,
        preserveSelection: true,
      });
      return controller.state();
    }

    async function renderManagement(query = "") {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      lastManagementQuery = typeof query === "string" ? query : "";
      await enterManagement();
      const spaceId = controller.state().selectedSpaceId;
      const results = await reloadManagementViews({
        query: lastManagementQuery,
        spaceId,
        surfaces: scopeUI.MANAGEMENT_SURFACES,
      });
      return results;
    }

    function managementIsVisible() {
      return !options.personalWikiRoot || options.personalWikiRoot.hidden === false;
    }

    async function refreshPublishedManagement(targets) {
      if (!controller || !managementIsVisible()) return false;
      const spaceId = controller.state().selectedSpaceId;
      const pending = new Set(publicationRefresh?.spaceId === spaceId
        ? publicationRefresh.pending : []);
      for (const target of targets || []) {
        if (target.spaceId === spaceId && target.outcome === "created") pending.add(target.itemId);
      }
      if (!pending.size) return refreshVisibleManagement();
      const task = { pending, spaceId };
      publicationRefresh = task;
      const current = () => publicationRefresh === task && controller
        && managementIsVisible() && controller.state().selectedSpaceId === spaceId;
      // Only the list is polled. Do not repeatedly request Graph/Log, use a
      // fixed delay as proof of visibility, or display a provisional body.
      const delays = [250, 500, 1000, 2000, 4000, 8000, 8000, 8000];
      // Bound background status-read work across all polls/pages. This is not
      // a Wiki/upload limit: unconfirmed targets remain delayed, never success.
      // The delay schedule is backoff, not a second poll-count deadline. Large
      // batches can still be publishing when the schedule first reaches 8s.
      let remainingListRequests = 64;
      try {
        for (let attempt = 0; current() && remainingListRequests > 0; attempt += 1) {
          const generation = ++managementRequestGeneration;
          remainingListRequests -= 1;
          const list = await transport.management(spaceId, "list");
          if (!current() || generation !== managementRequestGeneration) return false;
          // Confirmation may need later pages, but the visible list keeps its
          // first page and manual pager. Retain target IDs, not an entire Wiki.
          const found = new Set();
          const cursors = new Set();
          let page = list;
          let closureChanged = false;
          while (current()) {
            for (const item of page.items || []) {
              if (pending.has(item.id)) found.add(item.id);
            }
            if (found.size === pending.size || page.has_more !== true) break;
            if (remainingListRequests === 0) break;
            const cursor = page.next_cursor;
            if (typeof cursor !== "string" || !cursor || cursors.has(cursor)) {
              throw new TypeError("The item page cursor did not advance.");
            }
            cursors.add(cursor);
            remainingListRequests -= 1;
            const next = await transport.management(spaceId, "list", { cursor, limit: 50 });
            if (!current() || generation !== managementRequestGeneration) return false;
            if (
              (typeof list.corpus_closure_sha256 === "string"
                || typeof next.corpus_closure_sha256 === "string")
              && list.corpus_closure_sha256 !== next.corpus_closure_sha256
            ) {
              // Do not combine different corpus snapshots as visibility proof.
              // The next bounded poll starts again at the current first page.
              closureChanged = true;
              break;
            }
            page = next;
          }
          if (!closureChanged) for (const id of found) pending.delete(id);
          if (!pending.size) {
            await reloadManagementViews({ spaceId, query: lastManagementQuery,
              surfaces: scopeUI.MANAGEMENT_SURFACES, listResult: list });
            return true;
          }
          const finished = remainingListRequests === 0;
          // A detected generation change also invalidates this first page for
          // display. Wait for a fresh poll instead of flashing known-stale rows.
          if (!closureChanged) renderManagementResults(spaceId, { list, partial_loading: true });
          if (options.managementRoot && (!closureChanged || finished)) appendManagementLine(options.managementRoot.ownerDocument,
            finished
              ? "등록 처리 확인이 지연되고 있습니다. 다시 업로드하지 말고 잠시 후 문서 목록을 확인해 주세요."
              : "새 문서 자동 등록 처리 중… 완료되면 이 목록에 반영됩니다.",
            "item canonical-publication-pending");
          if (!finished) await publicationRefreshSleep(delays[Math.min(attempt, delays.length - 1)]);
        }
        return false;
      } catch (error) {
        if (current()) (options.onError || (() => {}))(error);
        return false;
      } finally {
        if (publicationRefresh === task) publicationRefresh = null;
      }
    }

    async function refreshVisibleManagement() {
      if (!controller || !managementIsVisible()) return false;
      try {
        await reloadManagementViews({
          query: lastManagementQuery,
          spaceId: controller.state().selectedSpaceId,
          surfaces: scopeUI.MANAGEMENT_SURFACES,
        });
        return true;
      } catch (error) {
        (options.onError || (() => {}))(error);
        return false;
      }
    }

    function refreshIfCorpusChanged() {
      if (externalRefreshPromise) return externalRefreshPromise;
      if (destroyed || !controller || !managementIsVisible() || activeEditor || publicationRefresh
          || (root && root.document && root.document.hidden)) return Promise.resolve(false);
      const current = controller.state();
      const spaceId = current.selectedSpaceId;
      const before = managementClosureBySpace.get(spaceId);
      if (!before) return Promise.resolve(false);
      const generation = managementRequestGeneration;
      const task = (async () => {
        try {
          const list = await transport.management(spaceId, "list");
          if (destroyed || !controller || !managementIsVisible() || activeEditor || publicationRefresh
              || (root && root.document && root.document.hidden)
              || controller.state().selectedSpaceId !== spaceId
              || generation !== managementRequestGeneration) return false;
          const after = list && list.corpus_closure_sha256;
          if (typeof after !== "string" || after === before) return false;
          await reloadManagementViews({
            spaceId, query: lastManagementQuery,
            surfaces: scopeUI.MANAGEMENT_SURFACES, listResult: list,
          });
          return true;
        } catch (error) {
          if (error && [401, 403, 404].includes(error.status)
              && controller && controller.state().selectedSpaceId === spaceId) {
            // A revoked scope must not leave its last successful document list
            // visible just because this read runs in the background.
            managementRequestGeneration += 1;
            managementClosureBySpace.delete(spaceId);
            if (options.managementRoot) {
              options.managementRoot.replaceChildren();
              appendManagementLine(options.managementRoot.ownerDocument,
                "지식 접근 권한이 변경되었습니다. 지식 경로를 다시 선택해 주세요.");
            }
          }
          // A transient network failure must not produce a repeated toast.
          // An explicit user refresh still reports errors.
          return false;
        }
      })();
      externalRefreshPromise = task;
      task.finally(() => {
        if (externalRefreshPromise === task) externalRefreshPromise = null;
      });
      return task;
    }

    async function openItem(spaceId, itemId) {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      if (typeof spaceId !== "string" || !UUID_RE.test(spaceId) || typeof itemId !== "string" || !UUID_RE.test(itemId)) {
        throw new TypeError("Item identifiers are invalid.");
      }
      if (typeof transport.getItem !== "function" || typeof transport.listRevisions !== "function") {
        throw new TypeError("Canonical item history transport is unavailable.");
      }
      if (controller.state().selectedSpaceId !== spaceId) {
        throw new TypeError("The item space is not selected.");
      }
      const requestGeneration = ++itemOpenGeneration;
      const graphRequest = typeof transport.management === "function"
        ? transport.management(spaceId, "graph").catch(() => null)
        : Promise.resolve(null);
      const sourceRequest = typeof transport.listItemSourceArtifacts === "function"
        ? transport.listItemSourceArtifacts(spaceId, itemId)
        : Promise.resolve({ items: [] });
      const [detail, history, graphResult, sourceArtifacts] = await Promise.all([
        transport.getItem(spaceId, itemId),
        transport.listRevisions(spaceId, itemId, { limit: 100 }),
        graphRequest,
        sourceRequest,
      ]);
      const graph = (
        graphResult
        && graphResult.space_id === spaceId
        && Array.isArray(graphResult.nodes)
        && Array.isArray(graphResult.edges)
      ) ? graphResult : null;
      if (
        !detail.item
        || detail.item.id !== itemId
        || detail.selected_view_space_id !== spaceId
        || (history.items || []).some((revision) => revision.item_id !== itemId)
      ) {
        throw new TypeError("Canonical item history binding is invalid.");
      }
      let distribution = null;
      if (
        detail.origin === "native"
        && (detail.capabilities || []).includes("distribution_grant")
        && typeof transport.listDistributionTargets === "function"
        && typeof transport.listDistributions === "function"
      ) {
        const [targets, grants] = await Promise.all([
          transport.listDistributionTargets({ spaceId }),
          transport.listDistributions({ limit: 100, spaceId }),
        ]);
        distribution = Object.freeze({ grants, targets });
      }
      // A document opened from the graph or list may finish after the user
      // selects another Wiki/document. Do not reopen that stale modal.
      if (requestGeneration !== itemOpenGeneration || controller.state().selectedSpaceId !== spaceId) {
        return { detail, graph, history, sourceArtifacts };
      }
      renderItemDetail(spaceId, itemId, detail, history, distribution, graph, sourceArtifacts);
      if (options.dispatchEvent) options.dispatchEvent("knowledge:item-opened", { itemId, spaceId });
      return { detail, graph, history, sourceArtifacts };
    }

    async function selectSpaceAndStart(spaceId) {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      await controller.selectSpace(spaceId);
      const selected = controller.state();
      if (selected.mode === scopeUI.CHAT_MODE && selected.pendingSpaceId) {
        return controller.startNewConversation();
      }
      return selected;
    }

    async function createFreshConversationForSpace(spaceId) {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      const oldConversationId = controller.state().currentConversationId;
      const created = await transport.createConversation({
        operationId: options.operationIdFactory(),
        spaceId,
      });
      await controller.openConversation(created.id);
      state.session_id = created.id;
      state.messages = [];
      state.compactSummary = "";
      state.compactedMessageCount = 0;
      state.lastContextStatus = null;
      if (state.sessionCache) state.sessionCache.set(created.id, []);
      pendingCreateOperationId = null;
      chat.renderMessages([]);
      await refreshVisibleManagement();
      if (options.onConversationStarted) {
        await options.onConversationStarted({
          oldConversationId,
          newConversationId: created.id,
          spaceId,
        });
      }
      return created.id;
    }

    async function startFreshConversation() {
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      return createFreshConversationForSpace(controller.state().selectedSpaceId);
    }

    async function chatStreamResponse({ conversationId, message, signal }) {
      const ownerId = (state.currentUser && state.currentUser.id) || "";
      const turnKey = `${ownerId}\n${conversationId}\n${message}`;
      if (pendingTurnPromises.has(turnKey)) return pendingTurnPromises.get(turnKey);
      const journal = options.getRetryJournal ? options.getRetryJournal() : null;
      const work = (async () => {
        const operationId = pendingTurnOperations.get(turnKey) || (journal
          ? await journal.reserve({ conversationId, message, operationIdFactory: options.operationIdFactory })
          : options.operationIdFactory());
        pendingTurnOperations.set(turnKey, operationId);
        async function settle() {
          if (journal) await journal.settle({ conversationId, message, operationId });
          pendingTurnOperations.delete(turnKey);
        }
        let result;
        if (((state.currentUser && state.currentUser.id) || "") !== ownerId) {
          throw new Error("로그인 계정이 변경되어 이전 계정의 요청 전송을 중단했습니다.");
        }
        try {
          const retryDeadline = Date.now() + 90_000;
          while (true) {
            try {
              result = await transport.streamTurn({
                conversationId,
                message,
                operationId,
                signal,
              });
              break;
            } catch (error) {
              // A publication/graph update can make a new turn temporarily
              // unavailable. The server has not accepted it yet, so replay
              // only this retryable conflict with the same operation ID.
              if (error?.code !== "STATE_CONFLICT" || error.status !== 409
                  || error.retryable !== true || error.terminal === true
                  || Date.now() >= retryDeadline || signal?.aborted) throw error;
              await new Promise((resolve, reject) => {
                const abort = () => {
                  clearTimeout(timer);
                  signal?.removeEventListener("abort", abort);
                  const aborted = new Error("The request was cancelled.");
                  aborted.name = "AbortError";
                  reject(aborted);
                };
                const timer = setTimeout(() => {
                  signal?.removeEventListener("abort", abort);
                  resolve();
                }, 1000);
                if (signal) signal.addEventListener("abort", abort, { once: true });
                if (signal?.aborted) abort();
              });
            }
          }
        } catch (error) {
          if (error && error.terminal) {
            try { await settle(); }
            catch (storageError) { storageError.cause = error; throw storageError; }
          }
          throw error;
        }
        try { await settle(); }
        catch (storageError) {
          storageError.message = "답변은 서버에 저장됐지만 브라우저의 요청 복구 상태를 정리하지 못했습니다. 대화 기록을 확인해 주세요.";
          throw storageError;
        }
        if (((state.currentUser && state.currentUser.id) || "") !== ownerId) {
          throw new Error("로그인 계정이 변경되어 이전 계정의 답변 표시를 중단했습니다.");
        }
        const payloads = [
          { event: "status", message: "Canonical Agent turn accepted", stage: "accepted" },
          { delta: result.assistantText },
          { done: true, event_cursor: result.terminalSequence, model_used: "canonical" },
        ];
        const encoded = payloads.map((value) => `data: ${JSON.stringify(value)}\n\n`).join("");
        return new Response(encoded, {
          headers: { "Content-Type": "text/event-stream; charset=utf-8" },
          status: 200,
        });
      })();
      pendingTurnPromises.set(turnKey, work);
      try { return await work; }
      finally { pendingTurnPromises.delete(turnKey); }
    }

    async function readPendingTurns(conversationId, pageOptions = {}) {
      const generation = ++pendingReadGeneration;
      const owner = state.currentUser && state.currentUser.id;
      if (!owner) throw new Error("로그인이 필요합니다.");
      const page = await transport.listPendingTurns(conversationId, pageOptions);
      if ((state.currentUser && state.currentUser.id) !== owner) throw new Error("로그인 계정이 변경되었습니다.");
      if (generation !== pendingReadGeneration) throw new Error("더 최근의 미완료 요청 목록이 선택되었습니다.");
      const prefix = `${owner}\n${conversationId}\n`;
      // Keep only the current page's issued handles, not a growing history cache.
      serverPendingTurns.clear();
      for (const item of page.items) serverPendingTurns.set(prefix + item.operation_id, item);
      return page;
    }

    async function resumePendingTurn(conversationId, operationId) {
      const owner = state.currentUser && state.currentUser.id;
      const key = `${owner}\n${conversationId}\n${operationId}`;
      const item = serverPendingTurns.get(key);
      if (!owner || !item || !item.can_resume) throw new Error("서버의 미완료 요청 목록을 다시 확인해 주세요.");
      if (state.session_id !== conversationId) throw new Error("현재 선택된 대화가 변경되었습니다.");
      if (pendingTurnPromises.has(key)) return pendingTurnPromises.get(key);
      const work = (async () => {
        let terminal = false;
        try {
          await transport.streamTurn({ conversationId, operationId, message: item.message });
          terminal = true;
        } catch (error) {
          terminal = !!(error && error.terminal);
          throw error;
        } finally {
          if (terminal) {
            serverPendingTurns.delete(key);
            const questionKey = `${owner}\n${conversationId}\n${item.message}`;
            if (pendingTurnOperations.get(questionKey) === operationId) pendingTurnOperations.delete(questionKey);
            // Existing server operation is recoverable without tab storage.
            // Settlement is compare-and-delete and cannot clear a newer request.
            if ((state.currentUser && state.currentUser.id) === owner && options.getRetryJournal) {
              try {
                await options.getRetryJournal().settle({ conversationId, message: item.message, operationId });
              } catch (_) {
                if (typeof toast === "function") toast("처리 결과는 서버에 저장됐지만 탭의 복구 기록을 정리하지 못했습니다.", "info");
              }
            }
          }
        }
        if ((state.currentUser && state.currentUser.id) !== owner) throw new Error("로그인 계정이 변경되었습니다.");
      })();
      pendingTurnPromises.set(key, work);
      try { await work; } finally { pendingTurnPromises.delete(key); }
    }

    async function listSessions() {
      const page = await transport.listConversations({ limit: 100 });
      return {
        sessions: page.items.map((item) => ({
          archived: item.archived,
          completed_turn_count: item.completed_turn_count,
          first_message: item.title,
          latest_turn_state: item.latest_turn_state,
          message_count: item.turn_count,
          row_version: item.row_version,
          session_id: item.id,
          space: item.space,
          timestamp: Math.floor(Date.parse(item.updated_at) / 1000),
          title: item.title,
          turn_count: item.turn_count,
        })),
      };
    }

    async function loadSession(conversationId) {
      const owner = state.currentUser && state.currentUser.id;
      const detail = await transport.getConversation(conversationId);
      if ((state.currentUser && state.currentUser.id) !== owner) throw new Error("로그인 계정이 변경되었습니다.");
      await controller.openConversation(conversationId);
      if ((state.currentUser && state.currentUser.id) !== owner) throw new Error("로그인 계정이 변경되었습니다.");
      state.session_id = conversationId;
      const messages = detail.messages.map((item) => ({
        content: messageText(item, options.transportModule),
        role: item.role,
      }));
      state.messages = chat.cloneMessages(messages);
      if (state.sessionCache) state.sessionCache.set(conversationId, chat.cloneMessages(messages));
      return {
        conversation: detail.conversation,
        messages,
        metadata: { summary_status: detail.summary_status },
        session_id: conversationId,
        title: detail.conversation.title,
      };
    }

    async function readSessionMessages(conversationId) {
      if (typeof conversationId !== "string" || !UUID_RE.test(conversationId)) {
        throw new TypeError("A canonical conversation is required.");
      }
      const detail = await transport.getConversation(conversationId);
      if (detail.conversation.id !== conversationId) {
        throw new TypeError("Conversation history binding is invalid.");
      }
      // Read-only reconciliation must not select another Wiki or conversation.
      return detail.messages.map((item) => ({
        content: messageText(item, options.transportModule), role: item.role,
      }));
    }

    async function updateSession(conversationId, changes) {
      const detail = await transport.getConversation(conversationId, { includeAllMessages: false });
      return transport.updateConversation({
        archived: changes.archived,
        conversationId,
        expectedRowVersion: detail.conversation.row_version,
        operationId: options.operationIdFactory(),
        title: changes.title,
      });
    }

    async function compactSession(conversationId) {
      const detail = await transport.getConversation(conversationId);
      if (!detail.messages.length) throw new TypeError("An empty conversation cannot be compacted.");
      return transport.compactConversation({
        conversationId,
        operationId: options.operationIdFactory(),
        throughMessageSequence: detail.messages[detail.messages.length - 1].sequence_no,
      });
    }

    async function publishSource(source) {
      if (publicationAvailability === "error") throw publicationProbeError;
      if (!publicationController) throw new TypeError("Canonical publication UI is unavailable.");
      const request = { ...source };
      if (request.sourceType === "chat") {
        const conversationId = request.conversationId || state.session_id;
        if (typeof conversationId !== "string" || !UUID_RE.test(conversationId)) throw new TypeError("A canonical conversation is required.");
        const detail = await transport.getConversation(conversationId);
        const sequences = (detail.messages || []).map((message) => message.sequence_no).filter((value) => Number.isSafeInteger(value) && value > 0);
        if (!sequences.length) throw new TypeError("An empty conversation cannot be published.");
        request.conversationId = conversationId;
        request.fromSequence = Math.min(...sequences);
        request.toSequence = Math.max(...sequences);
      }
      return publicationController.open(request);
    }

    async function publishSources(sources, { autoPublish = false } = {}) {
      if (publicationAvailability === "error") throw publicationProbeError;
      if (!publicationBatchController) throw new TypeError("Canonical batch publication UI is unavailable.");
      if (!Array.isArray(sources) || sources.length < (autoPublish ? 1 : 2)) throw new TypeError("Upload sources are required.");
      const requests = sources.map((source, index) => {
        if (!source || typeof source !== "object" || source.sourceType !== "upload" || !source.file) {
          throw new TypeError(`Upload source ${index + 1} is invalid.`);
        }
        return { ...source };
      });
      return publicationBatchController.open(requests, { autoPublish });
    }

    async function openPublishedTarget(target) {
      if (!target || typeof target !== "object") throw new TypeError("A publication target is required.");
      const spaceId = target.spaceId;
      const itemId = target.itemId;
      if (typeof spaceId !== "string" || !UUID_RE.test(spaceId) || typeof itemId !== "string" || !UUID_RE.test(itemId)) {
        throw new TypeError("Publication target identifiers are invalid.");
      }
      if (!controller) throw new TypeError("Canonical browser bridge is not active.");
      const current = controller.state();
      await controller.initialize({
        conversationId: current.currentConversationId,
        mode: scopeUI.MANAGEMENT_MODE,
        selectedSpaceId: spaceId,
      });
      await reloadManagementViews({ spaceId, surfaces: scopeUI.MANAGEMENT_SURFACES });
      let row = null;
      if (options.managementRoot && typeof options.managementRoot.querySelector === "function") {
        const escaped = root && root.CSS && typeof root.CSS.escape === "function" ? root.CSS.escape(itemId) : itemId;
        row = options.managementRoot.querySelector(`[data-canonical-item-id="${escaped}"]`);
        if (row) {
          row.classList.add("canonical-publication-target");
          row.tabIndex = -1;
          if (typeof row.focus === "function") row.focus();
          if (typeof row.scrollIntoView === "function") row.scrollIntoView({ block: "center" });
        }
      }
      if (options.dispatchEvent) options.dispatchEvent("knowledge:publication-target", { itemId, spaceId });
      return Boolean(row);
    }

    return Object.freeze({
      activate, chatStreamResponse, compactSession, destroy,
      beginEdit, editingState, ensureConversation, enterChat, enterManagement, listSessions, loadSession, reloadManagementViews,
      openFinding, openGraph, openGraphDetails, openItem, openPublishedTarget,
      personalWikiState: () => personalWikiController && personalWikiController.state(),
      publicationAvailability: () => publicationAvailability,
      publishSource, publishSources, renderManagement, renderWikiNavigation, setWikiNavigationVisible,
      startFreshConversation, state: () => controller && controller.state(),
      whenScopeSettled,
      updateSession, readSessionMessages, readPendingTurns, resumePendingTurn,
    });
  }

  async function bootstrap() {
    if (bridge) return true;
    if (bootResolved) return bootResult;
    if (bootPromise) return bootPromise;
    bootPromise = (async () => {
      if (!root || !root.document || !root.KnowledgeCoreTransport || !root.KnowledgeScopeUI) return false;
      const state = typeof State === "object" ? State : null;
      const chat = typeof Chat === "object" ? Chat : null;
      if (!state || !chat) return false;
      const operationIdFactory = () => root.crypto.randomUUID();
      const transport = root.KnowledgeCoreTransport.createTransport({
        csrfToken: () => state.csrfToken || "",
        operationIdFactory,
      });
      let candidate = null;
      let retryJournal = null;
      let retryJournalOwner = null;
      const publicationRenderer = root.KnowledgePublicationUI && typeof Modal === "object"
        ? root.KnowledgePublicationUI.createDomRenderer({
            documentRef: root.document,
            modal: Modal,
            openTarget: (target) => candidate && candidate.openPublishedTarget(target),
            toast: (message, kind) => typeof toast === "function" && toast(message, kind),
          })
        : null;
      candidate = createBridge({
        chat,
        getRetryJournal: () => {
          // Authentication loads asynchronously during page startup. Resolve
          // the actual current account lazily before sending, not from cached
          // demos_user or an anonymous shared namespace during bootstrap.
          const owner = state.currentUser && state.currentUser.id;
          if (!owner || !root.KnowledgeTurnRetry) {
            throw new Error("로그인 및 요청 복구 기능 초기화를 확인한 뒤 다시 시도해 주세요.");
          }
          if (!retryJournal || retryJournalOwner !== owner) {
            retryJournal = root.KnowledgeTurnRetry.createJournal({ storage: root.sessionStorage, ownerId: owner });
            retryJournalOwner = owner;
          }
          return retryJournal;
        },
        dispatchEvent: (name, detail) => root.dispatchEvent(new CustomEvent(name, { detail })),
        elements: {
          root: root.document.getElementById("knowledgeScopeControl"),
          select: root.document.getElementById("knowledgeScopeSelect"),
        },
        modal: typeof Modal === "object" ? Modal : null,
        onConversationStarted: async () => {
          if (typeof Sessions === "object" && typeof Sessions.render === "function") await Sessions.render();
        },
        onError: (error) => typeof toast === "function" && toast(error.message, "error"),
        operationIdFactory,
        personalWikiRoot: root.document.getElementById("knowledgeWikiNavigation"),
        personalWikiUI: root.PersonalWikiUI || null,
        publicationRenderer,
        publicationUI: root.KnowledgePublicationUI || null,
        managementRoot: root.document.getElementById("sidebarBody"),
        enableExternalRefresh: true,
        scopeUI: root.KnowledgeScopeUI,
        state,
        transport,
        transportModule: root.KnowledgeCoreTransport,
      });
      if (!await candidate.activate()) return false;
      bridge = candidate;
      root.document.documentElement.dataset.knowledgeCoreMode = "canonical";
      const legacyGraph = root.document.getElementById("graphToggleBtn");
      if (
        legacyGraph
        && root.document.documentElement.dataset.keepLegacyGraph !== "true"
      ) legacyGraph.hidden = true;
      try {
        const params = new URL(root.location.href).searchParams;
        const spaceId = params.get("canonical_space_id");
        const itemId = params.get("canonical_item_id");
        if (spaceId && itemId) await candidate.openPublishedTarget({ spaceId, itemId });
      } catch (error) {
        if (typeof toast === "function") toast(error.message, "error");
      }
      return true;
    })();
    try {
      bootResult = await bootPromise;
      bootResolved = true;
      return bootResult;
    } finally { bootPromise = null; }
  }

  const api = {
    get active() { return bridge !== null; },
    get publicationAvailability() { return bridge ? bridge.publicationAvailability() : "not_probed"; },
    bootstrap,
    chatStreamResponse: (...args) => bridge.chatStreamResponse(...args),
    readSessionMessages: (...args) => bridge.readSessionMessages(...args),
    readPendingTurns: (...args) => bridge.readPendingTurns(...args),
    resumePendingTurn: (...args) => bridge.resumePendingTurn(...args),
    compactSession: (...args) => bridge.compactSession(...args),
    createBridge,
    ensureConversation: (...args) => bridge.ensureConversation(...args),
    enterChat: (...args) => bridge.enterChat(...args),
    enterManagement: (...args) => bridge.enterManagement(...args),
    listSessions: (...args) => bridge.listSessions(...args),
    loadSession: (...args) => bridge.loadSession(...args),
    openItem: (...args) => bridge.openItem(...args),
    openGraph: (...args) => bridge.openGraph(...args),
    openGraphDetails: (...args) => bridge.openGraphDetails(...args),
    beginEdit: (...args) => bridge.beginEdit(...args),
    editingState: () => bridge && bridge.editingState(),
    openPublishedTarget: (...args) => bridge.openPublishedTarget(...args),
    personalWikiState: () => bridge && bridge.personalWikiState(),
    publishSource: (...args) => bridge.publishSource(...args),
    publishSources: (...args) => bridge.publishSources(...args),
    ready: bootstrap,
    renderManagement: (...args) => bridge.renderManagement(...args),
    renderWikiNavigation: (...args) => bridge.renderWikiNavigation(...args),
    setWikiNavigationVisible: (...args) => bridge.setWikiNavigationVisible(...args),
    startFreshConversation: (...args) => bridge.startFreshConversation(...args),
    state: () => bridge && bridge.state(),
    whenScopeSettled: (...args) => bridge.whenScopeSettled(...args),
    updateSession: (...args) => bridge.updateSession(...args),
  };

  if (root && root.document) {
    const start = () => bootstrap().catch((error) => {
      if (root.console) root.console.warn("[knowledge-core] canonical browser activation failed", error);
    });
    if (root.document.readyState === "loading") root.document.addEventListener("DOMContentLoaded", start, { once: true });
    else Promise.resolve().then(start);
  }
  return api;
});
