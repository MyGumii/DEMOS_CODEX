/* P3.6 inert knowledge-space selector/new-conversation UX controller.
 *
 * The controller owns presentation state only.  Its injected adapters are
 * wired to canonical routes in P3.7; this file never mutates a conversation,
 * trusts a browser-supplied actor, or calls a legacy session endpoint.
 */
(function knowledgeScopeModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.KnowledgeScopeUI = api;
})(typeof globalThis === "object" ? globalThis : this, function buildKnowledgeScopeUI() {
  "use strict";

  const CHAT_MODE = "chat";
  const MANAGEMENT_MODE = "management";
  const NEW_CONVERSATION_LABEL = "새 지식공간으로 새 대화 시작";
  const MANAGEMENT_SURFACES = Object.freeze([
    "list",
    "search",
    "graph",
    "index_log",
    "activity",
    "candidates",
    "pins",
  ]);
  const SPACE_DISPLAY_ORDER = Object.freeze({
    personal: 0,
    part: 1,
    team: 2,
    responsibility: 3,
  });
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

  class KnowledgeScopeUIError extends Error {
    constructor(code, message) {
      super(message);
      this.name = "KnowledgeScopeUIError";
      this.code = code;
    }
  }

  function fail(code, message) {
    throw new KnowledgeScopeUIError(code, message);
  }

  function nonEmptyString(value, field) {
    if (typeof value !== "string" || value.length === 0) {
      fail("INVALID_SCOPE_SNAPSHOT", field + " must be a non-empty string.");
    }
    return value;
  }

  function exactBoolean(value, field) {
    if (typeof value !== "boolean") {
      fail("INVALID_SCOPE_SNAPSHOT", field + " must be a boolean.");
    }
    return value;
  }

  function normalizeNativeAuthority(value, spaceId, spaceKind) {
    if (value === undefined || value === null) return null;
    if (
      !value || typeof value !== "object" || Array.isArray(value)
      || !["team", "responsibility"].includes(spaceKind)
      || value.native_space_id !== spaceId
      || !UUID_RE.test(String(value.role_assignment_id || ""))
      || !Number.isSafeInteger(value.role_assignment_generation)
      || value.role_assignment_generation < 1
      || !Array.isArray(value.capabilities)
    ) fail("INVALID_SCOPE_SNAPSHOT", "space native authority is invalid.");
    const capabilities = Object.freeze(value.capabilities.map(
      item => nonEmptyString(item, "space.native_authority.capabilities"),
    ));
    if (new Set(capabilities).size !== capabilities.length) {
      fail("INVALID_SCOPE_SNAPSHOT", "space native authority capabilities are duplicated.");
    }
    let publicationTarget = null;
    if (value.publication_target !== null) {
      const target = value.publication_target;
      if (
        !target || typeof target !== "object" || Array.isArray(target)
        || target.capability !== "publish"
        || target.kind !== spaceKind
        || target.space_id !== spaceId
        || target.role_assignment_id !== value.role_assignment_id
        || target.role_assignment_generation !== value.role_assignment_generation
        || !capabilities.includes("publish")
      ) fail("INVALID_SCOPE_SNAPSHOT", "space publication authority is invalid.");
      publicationTarget = Object.freeze({
        capability: "publish",
        kind: target.kind,
        role_assignment_generation: target.role_assignment_generation,
        role_assignment_id: target.role_assignment_id,
        space_id: target.space_id,
      });
    }
    return Object.freeze({
      capabilities,
      native_space_id: spaceId,
      publication_target: publicationTarget,
      role_assignment_generation: value.role_assignment_generation,
      role_assignment_id: value.role_assignment_id,
    });
  }

  function normalizeSpace(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      fail("INVALID_SCOPE_SNAPSHOT", "space must be an object.");
    }
    const kind = nonEmptyString(value.kind, "space.kind");
    if (!["personal", "responsibility", "team", "part"].includes(kind)) {
      fail("INVALID_SCOPE_SNAPSHOT", "knowledge space kind is invalid.");
    }
    if (!Array.isArray(value.org_path) || !Array.isArray(value.capabilities)) {
      fail("INVALID_SCOPE_SNAPSHOT", "space arrays are invalid.");
    }
    const id = nonEmptyString(value.id, "space.id");
    return Object.freeze({
      id,
      kind,
      name: nonEmptyString(value.name, "space.name"),
      org_path: Object.freeze(value.org_path.map((item) => nonEmptyString(item, "space.org_path"))),
      capabilities: Object.freeze(value.capabilities.map((item) => nonEmptyString(item, "space.capabilities"))),
      history_only: value.history_only === undefined ? false : exactBoolean(value.history_only, "space.history_only"),
      native_authority: normalizeNativeAuthority(value.native_authority, id, kind),
      selected: exactBoolean(value.selected, "space.selected"),
    });
  }

  function normalizeBadge(value) {
    if (value === null) return null;
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      fail("INVALID_SCOPE_SNAPSHOT", "conversation_badge must be an object or null.");
    }
    if (value.immutable !== true) {
      fail("INVALID_SCOPE_SNAPSHOT", "conversation space badge must be immutable.");
    }
    const kind = nonEmptyString(value.space_kind, "conversation_badge.space_kind");
    if (!["personal", "responsibility", "team", "part"].includes(kind)) {
      fail("INVALID_SCOPE_SNAPSHOT", "conversation space kind is invalid.");
    }
    if (!Array.isArray(value.org_path)) {
      fail("INVALID_SCOPE_SNAPSHOT", "conversation badge path is invalid.");
    }
    if (
      value.wiki_lifecycle_generation !== undefined
      && value.wiki_lifecycle_generation !== null
      && (!Number.isSafeInteger(value.wiki_lifecycle_generation) || value.wiki_lifecycle_generation < 1)
    ) fail("INVALID_SCOPE_SNAPSHOT", "conversation badge Wiki generation is invalid.");
    if (!Number.isSafeInteger(value.conversation_row_version) || value.conversation_row_version < 1) {
      fail("INVALID_SCOPE_SNAPSHOT", "conversation badge row version is invalid.");
    }
    return Object.freeze({
      access_status: nonEmptyString(value.access_status, "conversation_badge.access_status"),
      accessible: exactBoolean(value.accessible, "conversation_badge.accessible"),
      conversation_id: nonEmptyString(value.conversation_id, "conversation_badge.conversation_id"),
      conversation_row_version: value.conversation_row_version,
      conversation_status: nonEmptyString(value.conversation_status, "conversation_badge.conversation_status"),
      conversation_title: nonEmptyString(value.conversation_title, "conversation_badge.conversation_title"),
      history_only: value.history_only === undefined ? false : exactBoolean(value.history_only, "conversation_badge.history_only"),
      immutable: true,
      org_path: Object.freeze(value.org_path.map((item) => nonEmptyString(item, "conversation_badge.org_path"))),
      space_id: nonEmptyString(value.space_id, "conversation_badge.space_id"),
      space_kind: kind,
      space_name: nonEmptyString(value.space_name, "conversation_badge.space_name"),
      wiki_lifecycle_generation: value.wiki_lifecycle_generation === undefined || value.wiki_lifecycle_generation === null
        ? null : value.wiki_lifecycle_generation,
    });
  }

  function normalizeSnapshot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      fail("INVALID_SCOPE_SNAPSHOT", "scope snapshot must be an object.");
    }
    if (!Number.isSafeInteger(value.scope_epoch) || value.scope_epoch < 1) {
      fail("INVALID_SCOPE_SNAPSHOT", "scope_epoch must be a positive integer.");
    }
    if (!Array.isArray(value.spaces) || value.spaces.length < 1) {
      fail("INVALID_SCOPE_SNAPSHOT", "selector must expose at least one space.");
    }
    const spaces = Object.freeze(value.spaces.map(normalizeSpace));
    const ids = new Set(spaces.map((space) => space.id));
    const selectedSpaceId = nonEmptyString(value.selected_space_id, "selected_space_id");
    if (ids.size !== spaces.length || !ids.has(selectedSpaceId)) {
      fail("INVALID_SCOPE_SNAPSHOT", "scope option identity is inconsistent.");
    }
    if (spaces.filter((space) => space.selected).length !== 1 || !spaces.find((space) => space.selected && space.id === selectedSpaceId)) {
      fail("INVALID_SCOPE_SNAPSHOT", "scope selection is inconsistent.");
    }
    const personalCount = spaces.filter((space) => space.kind === "personal").length;
    if (personalCount < 1) {
      fail("INVALID_SCOPE_SNAPSHOT", "at least one personal Wiki is required.");
    }
    const badge = normalizeBadge(value.conversation_badge);
    return Object.freeze({
      conversation_badge: badge,
      scope_epoch: value.scope_epoch,
      selected_space_id: selectedSpaceId,
      spaces,
    });
  }

  function spaceDisplayLabel(space) {
    if (!space || typeof space !== "object" || Array.isArray(space)) {
      throw new TypeError("A knowledge space is required.");
    }
    if (space.kind === "personal") {
      const name = nonEmptyString(space.name, "space.name");
      return name === "내 지식" ? "내 지식" : `내 지식 · ${name}`;
    }
    const label = {
      responsibility: "담당",
      team: "팀",
      part: "Part",
    }[space.kind];
    return `${label || "지식공간"} · ${nonEmptyString(space.name, "space.name")}`;
  }

  function spacesForDisplay(spaces) {
    if (!Array.isArray(spaces)) throw new TypeError("spaces must be an array.");
    return [...spaces].sort((left, right) => {
      const leftOrder = SPACE_DISPLAY_ORDER[left?.kind];
      const rightOrder = SPACE_DISPLAY_ORDER[right?.kind];
      if (!Number.isSafeInteger(leftOrder) || !Number.isSafeInteger(rightOrder)) {
        throw new TypeError("knowledge space kind is invalid.");
      }
      return leftOrder - rightOrder;
    });
  }

  function isBrowserSelectableSpace(space) {
    return !!space && space.history_only !== true;
  }

  function publicState(internal) {
    return Object.freeze({
      conversationBadge: internal.snapshot ? internal.snapshot.conversation_badge : null,
      currentConversationId: internal.currentConversationId,
      initialized: internal.snapshot !== null,
      mode: internal.mode,
      pendingSpaceId: internal.pendingSpaceId,
      primaryActionLabel: internal.pendingSpaceId ? NEW_CONVERSATION_LABEL : null,
      selectedSpaceId: internal.snapshot ? internal.snapshot.selected_space_id : null,
      spaces: internal.snapshot ? internal.snapshot.spaces : Object.freeze([]),
    });
  }

  function createController(options) {
    if (!options || typeof options !== "object") {
      throw new TypeError("KnowledgeScopeUI controller options are required.");
    }
    const readSnapshot = options.readSnapshot;
    const createConversation = options.createConversation;
    const reloadManagementViews = options.reloadManagementViews;
    const onConversationStarted = options.onConversationStarted || (() => {});
    const operationIdFactory = options.operationIdFactory || (() => {
      if (!globalThis.crypto || typeof globalThis.crypto.randomUUID !== "function") {
        throw new TypeError("operationIdFactory is required when crypto.randomUUID is unavailable.");
      }
      return globalThis.crypto.randomUUID();
    });
    for (const [name, value] of Object.entries({ readSnapshot, createConversation, reloadManagementViews, onConversationStarted, operationIdFactory })) {
      if (typeof value !== "function") throw new TypeError(name + " must be a function.");
    }

    const listeners = new Set();
    let transitionTail = Promise.resolve();
    const pendingTransitions = new Set();
    const settlementObservers = new Set();
    function transition(action) {
      // Scope reads/commits (including management rollback) are ordered.
      // Post-commit notifications may reenter; a failed read cannot poison
      // later transitions.
      const work = transitionTail.then(action);
      pendingTransitions.add(work);
      for (const observed of settlementObservers) observed.add(work);
      transitionTail = work.then(
        () => { pendingTransitions.delete(work); },
        () => { pendingTransitions.delete(work); },
      );
      return work;
    }
    const internal = {
      currentConversationId: null,
      mode: CHAT_MODE,
      pendingOperationId: null,
      pendingSpaceId: null,
      snapshot: null,
      startPromise: null,
    };

    function emit() {
      const value = publicState(internal);
      for (const listener of listeners) listener(value);
      return value;
    }

    async function fetchSnapshot(selectedSpaceId, conversationId) {
      return normalizeSnapshot(await readSnapshot({
        conversationId: conversationId || null,
        selectedSpaceId: selectedSpaceId || null,
      }));
    }

    function ensureAvailable(spaceId) {
      nonEmptyString(spaceId, "spaceId");
      if (!internal.snapshot || !internal.snapshot.spaces.some((space) => space.id === spaceId)) {
        // A newly-created Wiki can appear after the last snapshot. Canonical
        // UUIDs are revalidated by readSnapshot; arbitrary UI values fail here.
        if (!UUID_RE.test(spaceId)) {
          fail("SPACE_NOT_SELECTABLE", "The requested knowledge space is not selectable.");
        }
      }
    }

    const controller = {
      subscribe(listener) {
        if (typeof listener !== "function") throw new TypeError("listener must be a function.");
        listeners.add(listener);
        listener(publicState(internal));
        return () => listeners.delete(listener);
      },

      state() {
        return publicState(internal);
      },

      async whenSettled() {
        // Read-only barrier: observe pending work and any transitions queued
        // while waiting, without changing mode, IDs or requesting a snapshot.
        // The queue recovers after a failed read, but a caller waiting on that
        // failed selection must not silently use the preceding upload target.
        const observed = new Set(pendingTransitions);
        settlementObservers.add(observed);
        let failed = false;
        let failure;
        try {
          while (observed.size) {
            const batch = [...observed];
            const outcomes = await Promise.allSettled(batch);
            for (const work of batch) observed.delete(work);
            for (const outcome of outcomes) {
              if (outcome.status === "rejected" && !failed) {
                failed = true;
                failure = outcome.reason;
              }
            }
          }
          if (failed) throw failure;
          return publicState(internal);
        } finally {
          settlementObservers.delete(observed);
        }
      },

      async initialize({ mode = CHAT_MODE, conversationId = null, selectedSpaceId = null,
        preserveSelection = false, preserveMode = false } = {}) {
        if (mode !== CHAT_MODE && mode !== MANAGEMENT_MODE) {
          throw new TypeError("mode must be chat or management.");
        }
        if (typeof preserveSelection !== "boolean") throw new TypeError("preserveSelection must be a boolean.");
        if (typeof preserveMode !== "boolean") throw new TypeError("preserveMode must be a boolean.");
        return transition(async () => {
        // Tab/catalog refresh may have captured an old selection while a Wiki
        // change was still reading. Resolve these IDs only when its turn starts.
        if (preserveSelection && internal.snapshot) {
          selectedSpaceId = internal.snapshot.selected_space_id;
          conversationId = internal.currentConversationId;
        }
        if (preserveMode) mode = internal.mode;
        const validated = await fetchSnapshot(selectedSpaceId, conversationId);
        if (validated.conversation_badge && validated.conversation_badge.conversation_id !== conversationId) {
          fail("INVALID_SCOPE_SNAPSHOT", "conversation badge identity is inconsistent.");
        }
        const preservePending = preserveSelection && preserveMode && mode === CHAT_MODE
          && internal.pendingSpaceId === validated.selected_space_id;
        internal.mode = mode;
        internal.currentConversationId = conversationId;
        if (!preservePending) {
          internal.pendingOperationId = null;
          internal.pendingSpaceId = null;
        }
        if (!preserveSelection) internal.startPromise = null;
        internal.snapshot = validated;
        return emit();
        });
      },

      async openConversation(conversationId) {
        nonEmptyString(conversationId, "conversationId");
        return transition(async () => {
        const validated = await fetchSnapshot(null, conversationId);
        if (!validated.conversation_badge || validated.conversation_badge.conversation_id !== conversationId) {
          fail("INVALID_SCOPE_SNAPSHOT", "conversation badge is missing or inconsistent.");
        }
        internal.snapshot = validated;
        internal.currentConversationId = conversationId;
        internal.pendingOperationId = null;
        internal.pendingSpaceId = null;
        internal.startPromise = null;
        internal.mode = CHAT_MODE;
        return emit();
        });
      },

      async selectSpace(spaceId) {
        return transition(async () => {
        ensureAvailable(spaceId);
        const validated = await fetchSnapshot(spaceId, internal.currentConversationId);
        if (internal.mode === MANAGEMENT_MODE) {
          // Management renderers read controller.state() while they build the
          // selected-scope Index/Log/Graph surfaces.  Expose the validated
          // target snapshot during that render, but roll it back if any
          // dependent surface fails so a half-switched scope is never kept.
          const previousSnapshot = internal.snapshot;
          internal.snapshot = validated;
          try {
            await reloadManagementViews({
              spaceId,
              surfaces: MANAGEMENT_SURFACES.slice(),
            });
          } catch (error) {
            internal.snapshot = previousSnapshot;
            throw error;
          }
          internal.pendingOperationId = null;
          internal.pendingSpaceId = null;
          return emit();
        }
        internal.snapshot = validated;
        const badge = validated.conversation_badge;
        const previousTarget = internal.pendingSpaceId;
        internal.pendingSpaceId = !badge || badge.space_id !== spaceId ? spaceId : null;
        if (internal.pendingSpaceId === null) {
          internal.pendingOperationId = null;
        } else if (previousTarget !== internal.pendingSpaceId) {
          internal.pendingOperationId = nonEmptyString(operationIdFactory(), "operationId");
        }
        return emit();
        });
      },

      async cancelPendingSelection() {
        return transition(async () => {
        if (!internal.pendingSpaceId) return emit();
        const badge = internal.snapshot && internal.snapshot.conversation_badge;
        const fallback = badge && internal.snapshot.spaces.some((space) => space.id === badge.space_id)
          ? badge.space_id
          : internal.snapshot.spaces.find((space) => space.kind === "personal").id;
        internal.snapshot = await fetchSnapshot(fallback, internal.currentConversationId);
        internal.pendingOperationId = null;
        internal.pendingSpaceId = null;
        return emit();
        });
      },

      async startNewConversation() {
        if (internal.startPromise) return internal.startPromise;
        if (!internal.pendingSpaceId) {
          fail("NO_PENDING_SCOPE_CHANGE", "No chat scope change is pending.");
        }
        const work = transition(async () => {
          // A newer queued selection/cancel/tab change may have superseded the
          // pending target since the button callback requested this action.
          const targetSpaceId = internal.pendingSpaceId;
          if (!targetSpaceId) return { event: null };
          const operationId = internal.pendingOperationId || nonEmptyString(operationIdFactory(), "operationId");
          internal.pendingOperationId = operationId;
          const oldConversationId = internal.currentConversationId;
          const created = await createConversation({ operationId, spaceId: targetSpaceId });
          if (!created || typeof created !== "object" || Array.isArray(created)) {
            fail("INVALID_CONVERSATION_RESULT", "New conversation result is invalid.");
          }
          const newConversationId = nonEmptyString(created.id, "conversation.id");
          if (created.space_id !== targetSpaceId || newConversationId === oldConversationId) {
            fail("INVALID_CONVERSATION_RESULT", "New conversation scope is inconsistent.");
          }
          const validated = await fetchSnapshot(targetSpaceId, newConversationId);
          if (!validated.conversation_badge || validated.conversation_badge.conversation_id !== newConversationId || validated.conversation_badge.space_id !== targetSpaceId) {
            fail("INVALID_CONVERSATION_RESULT", "New conversation badge is inconsistent.");
          }
          internal.currentConversationId = newConversationId;
          internal.pendingOperationId = null;
          internal.pendingSpaceId = null;
          internal.snapshot = validated;
          emit();
          // Only the start transaction is deduplicated. A slow notification
          // must not prevent a later selection from starting its own session.
          if (internal.startPromise === work) internal.startPromise = null;
          return { event: { oldConversationId, newConversationId, spaceId: targetSpaceId } };
        }).then(async ({ event }) => {
          // The callback may refresh the selected Wiki and therefore reenter
          // initialize(). Never hold the scope queue while awaiting it, and
          // never return an old captured state over a later session/selection.
          if (event && internal.currentConversationId === event.newConversationId) await onConversationStarted(event);
          return publicState(internal);
        });
        internal.startPromise = work;
        try {
          return await work;
        } finally {
          if (internal.startPromise === work) internal.startPromise = null;
        }
      },
    };
    return Object.freeze(controller);
  }

  function bindDom(controller, elements, onError = () => {}) {
    if (!controller || typeof controller.subscribe !== "function") {
      throw new TypeError("A KnowledgeScopeUI controller is required.");
    }
    const required = ["root", "select"];
    for (const name of required) {
      if (!elements || !elements[name]) throw new TypeError("elements." + name + " is required.");
    }
    if (typeof onError !== "function") throw new TypeError("onError must be a function.");

    const { root: container, select, badge = null, primaryAction = null, cancelAction = null } = elements;
    const documentRef = select.ownerDocument;
    // Keep long selected labels bounded while the browser owns picker semantics.
    const supportsPicker = documentRef.defaultView?.CSS?.supports?.("appearance", "base-select");
    const selectedButton = supportsPicker ? documentRef.createElement("button") : null;
    if (selectedButton) {
      selectedButton.type = "button";
      selectedButton.appendChild(documentRef.createElement("selectedcontent"));
    }
    const unsubscribe = controller.subscribe((state) => {
      container.hidden = !state.initialized;
      select.disabled = !state.initialized;
      select.replaceChildren();
      if (selectedButton) select.appendChild(selectedButton);
      for (const space of spacesForDisplay(state.spaces)) {
        const option = documentRef.createElement("option");
        option.value = space.id;
        option.textContent = spaceDisplayLabel(space);
        option.selected = space.id === state.selectedSpaceId;
        option.disabled = !isBrowserSelectableSpace(space);
        if (option.disabled) {
          option.title = "보관된 Wiki는 새 대화에 선택할 수 없습니다.";
        }
        select.appendChild(option);
      }
      const conversationBadge = state.conversationBadge;
      if (badge) {
        badge.hidden = !conversationBadge;
        badge.textContent = conversationBadge ? "대화 공간: " + spaceDisplayLabel({
          kind: conversationBadge.space_kind,
          name: conversationBadge.space_name,
          org_path: conversationBadge.org_path,
        }) : "";
        badge.dataset.spaceId = conversationBadge ? conversationBadge.space_id : "";
        badge.dataset.immutable = conversationBadge ? "true" : "";
        badge.dataset.accessStatus = conversationBadge ? conversationBadge.access_status : "";
      }
      if (primaryAction) {
        primaryAction.hidden = !state.pendingSpaceId;
        primaryAction.textContent = state.primaryActionLabel || NEW_CONVERSATION_LABEL;
      }
      if (cancelAction) cancelAction.hidden = !state.pendingSpaceId;
    });

    async function run(action) {
      select.disabled = true;
      if (primaryAction) primaryAction.disabled = true;
      if (cancelAction) cancelAction.disabled = true;
      try {
        await action();
      } catch (error) {
        onError(error);
      } finally {
        select.disabled = false;
        if (primaryAction) primaryAction.disabled = false;
        if (cancelAction) cancelAction.disabled = false;
      }
    }
    select.addEventListener("change", () => run(async () => {
      const requested = controller.state().spaces.find((space) => space.id === select.value);
      if (!isBrowserSelectableSpace(requested)) {
        select.value = controller.state().selectedSpaceId;
        return;
      }
      await controller.selectSpace(select.value);
      const selected = controller.state();
      if (selected.mode === CHAT_MODE && selected.pendingSpaceId) {
        await controller.startNewConversation();
      }
    }));
    if (primaryAction) primaryAction.addEventListener("click", () => run(() => controller.startNewConversation()));
    if (cancelAction) cancelAction.addEventListener("click", () => run(() => controller.cancelPendingSelection()));
    return Object.freeze({ destroy: unsubscribe });
  }

  return Object.freeze({
    CHAT_MODE,
    MANAGEMENT_MODE,
    MANAGEMENT_SURFACES,
    NEW_CONVERSATION_LABEL,
    KnowledgeScopeUIError,
    bindDom,
    createController,
    isBrowserSelectableSpace,
    normalizeSnapshot,
    spaceDisplayLabel,
    spacesForDisplay,
  });
});
