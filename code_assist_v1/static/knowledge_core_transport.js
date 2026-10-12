/* Canonical P3.7-P7.1 browser transport. No legacy route is used. */
(function knowledgeCoreTransportModule(root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.KnowledgeCoreTransport = api;
})(typeof globalThis === "object" ? globalThis : this, function buildTransportModule() {
  "use strict";

  const PREFIX = "/api/knowledge/v1";
  const PERSONAL_WIKI_PREFIX = "/api/knowledge/v2/personal-wikis";
  const SPACE_V2_PREFIX = "/api/knowledge/v2/spaces";
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const SHA256_RE = /^[0-9a-f]{64}$/;
  const LEASE_TOKEN_RE = /^[A-Za-z0-9_-]{43}$/;
  const EVENT_TYPES = new Set(["accepted", "delta", "metadata", "completed", "failed"]);
  const JOB_STATES = new Set(["queued", "claimed", "running", "retry", "succeeded", "failed", "cancelled", "dead"]);
  const JOB_CANCEL_STATUSES = new Set([...JOB_STATES, "cancel_requested"]);

  class CanonicalTransportError extends Error {
    constructor(code, message, status = 0, retryable = false, terminal = false, details = {}) {
      super(message);
      this.name = "CanonicalTransportError";
      this.code = code;
      this.status = status;
      this.retryable = retryable;
      this.terminal = terminal;
      this.details = details && typeof details === "object" && !Array.isArray(details)
        ? Object.freeze({ ...details }) : Object.freeze({});
    }
  }

  function fail(code, message, status = 0, retryable = false, terminal = false) {
    throw new CanonicalTransportError(code, message, status, retryable, terminal);
  }

  function object(value, field) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      fail("INVALID_CANONICAL_RESPONSE", field + " must be an object.");
    }
    return value;
  }

  function string(value, field) {
    if (typeof value !== "string" || value.length === 0) {
      fail("INVALID_CANONICAL_RESPONSE", field + " must be a non-empty string.");
    }
    return value;
  }

  function boundedText(value, field, maximum) {
    string(value, field);
    if (
      value.length > maximum
      || [...value].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
    ) {
      throw new TypeError(field + " is invalid.");
    }
    return value;
  }

  function markdownText(value, field) {
    string(value, field);
    if (
      value.length > 2000000
      || [...value].some((character) => {
        const code = character.charCodeAt(0);
        return (code < 32 && ![9, 10, 13].includes(code)) || code === 127;
      })
    ) {
      throw new TypeError(field + " is invalid.");
    }
    return value;
  }

  function uuid(value, field) {
    if (typeof value !== "string" || !UUID_RE.test(value)) {
      fail("INVALID_CANONICAL_RESPONSE", field + " must be a canonical UUID.");
    }
    return value;
  }

  function positive(value, field) {
    if (!Number.isSafeInteger(value) || value < 1) {
      fail("INVALID_CANONICAL_RESPONSE", field + " must be a positive integer.");
    }
    return value;
  }

  function operationId(factory, supplied) {
    return uuid(supplied || factory(), "operation_id");
  }

  function encodedId(value, field) {
    return encodeURIComponent(uuid(value, field));
  }

  function higherPublicationTarget(value, field = "target") {
    object(value, field);
    exactKeys(value, ["capability", "kind", "role_assignment_generation", "role_assignment_id", "space_id"], field);
    if (!['team', 'responsibility'].includes(value.kind) || value.capability !== 'publish') {
      throw new TypeError(field + " is invalid.");
    }
    return Object.freeze({
      capability: "publish",
      kind: value.kind,
      role_assignment_generation: positive(value.role_assignment_generation, field + ".role_assignment_generation"),
      role_assignment_id: uuid(value.role_assignment_id, field + ".role_assignment_id"),
      space_id: uuid(value.space_id, field + ".space_id"),
    });
  }

  function exactKeys(value, expected, field) {
    object(value, field);
    const actual = Object.keys(value).sort().join(",");
    const wanted = [...expected].sort().join(",");
    if (actual !== wanted) fail("INVALID_CANONICAL_RESPONSE", field + " shape is invalid.");
    return value;
  }

  function leaseToken(value) {
    if (typeof value !== "string" || !LEASE_TOKEN_RE.test(value)) {
      throw new TypeError("lease token is invalid.");
    }
    return value;
  }

  function createLeaseToken(cryptoImpl = globalThis.crypto) {
    if (!cryptoImpl || typeof cryptoImpl.getRandomValues !== "function") {
      throw new TypeError("A cryptographically secure random generator is required.");
    }
    const bytes = new Uint8Array(32);
    cryptoImpl.getRandomValues(bytes);
    const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let encoded = "";
    for (let offset = 0; offset < bytes.length; offset += 3) {
      const a = bytes[offset];
      const hasB = offset + 1 < bytes.length;
      const hasC = offset + 2 < bytes.length;
      const b = hasB ? bytes[offset + 1] : 0;
      const c = hasC ? bytes[offset + 2] : 0;
      encoded += alphabet[a >> 2];
      encoded += alphabet[((a & 3) << 4) | (b >> 4)];
      if (hasB) encoded += alphabet[((b & 15) << 2) | (c >> 6)];
      if (hasC) encoded += alphabet[c & 63];
    }
    return encoded.replace(/\+/g, "-").replace(/\//g, "_");
  }

  async function errorFromResponse(response) {
    let payload = null;
    try { payload = await response.json(); } catch (_) {}
    const error = payload && payload.error && typeof payload.error === "object"
      ? payload.error : null;
    return new CanonicalTransportError(
      error && typeof error.code === "string" ? error.code : "HTTP_ERROR",
      error && typeof error.message === "string" ? error.message : `HTTP ${response.status}`,
      response.status,
      !!(error && error.retryable),
      false,
      error && error.details,
    );
  }

  function decodeSse(text, expectedOperationId) {
    if (typeof text !== "string") fail("INVALID_EVENT_STREAM", "The event stream is invalid.");
    const events = [];
    let previous = 0;
    let terminal = false;
    for (const block of text.replace(/\r\n/g, "\n").split("\n\n")) {
      if (!block.trim()) continue;
      let id = null;
      let namedType = null;
      const data = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "id") id = value;
        else if (field === "event") namedType = value;
        else if (field === "data") data.push(value);
      }
      if (!id || !namedType || data.length < 1) {
        fail("INVALID_EVENT_STREAM", "A canonical event is incomplete.");
      }
      let value;
      try { value = JSON.parse(data.join("\n")); } catch (_) {
        fail("INVALID_EVENT_STREAM", "A canonical event payload is invalid.");
      }
      object(value, "event");
      const sequence = positive(value.event_sequence, "event_sequence");
      if (String(sequence) !== id || sequence <= previous) {
        fail("INVALID_EVENT_STREAM", "Canonical event sequence is not monotonic.");
      }
      if (!EVENT_TYPES.has(namedType) || value.event_type !== namedType) {
        fail("INVALID_EVENT_STREAM", "Canonical event type is invalid.");
      }
      if (uuid(value.operation_id, "event.operation_id") !== expectedOperationId) {
        fail("INVALID_EVENT_STREAM", "Canonical event operation binding is invalid.");
      }
      uuid(value.request_id, "event.request_id");
      uuid(value.turn_id, "event.turn_id");
      object(value.payload, "event.payload");
      if (typeof value.terminal !== "boolean" || value.terminal !== ["completed", "failed"].includes(namedType)) {
        fail("INVALID_EVENT_STREAM", "Canonical terminal marker is invalid.");
      }
      if (terminal) fail("INVALID_EVENT_STREAM", "Canonical stream contains data after terminal.");
      previous = sequence;
      terminal = value.terminal;
      events.push(Object.freeze(value));
    }
    if (!terminal || events.length < 1) {
      fail("INCOMPLETE_EVENT_STREAM", "Canonical stream did not reach a terminal event.", 0, true);
    }
    return Object.freeze(events);
  }

  function messageText(message) {
    let value = object(message, "message").content;
    if (value && typeof value === "object" && !Array.isArray(value) && value.content !== undefined) {
      value = value.content;
    }
    if (value && typeof value === "object" && !Array.isArray(value) && typeof value.text === "string") {
      value = value.text;
    }
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function createTransport(options = {}) {
    const fetchImpl = options.fetchImpl || (typeof fetch === "function" ? fetch.bind(globalThis) : null);
    const csrfToken = typeof options.csrfToken === "function" ? options.csrfToken : () => "";
    const idFactory = options.operationIdFactory || (() => globalThis.crypto.randomUUID());
    if (typeof fetchImpl !== "function" || typeof idFactory !== "function") {
      throw new TypeError("fetchImpl and operationIdFactory are required.");
    }

    async function request(path, requestOptions = {}) {
      const canonicalPath = typeof path === "string" && (
        path.startsWith(PREFIX + "/") || path === PREFIX + "/spaces"
      );
      const personalWikiPath = typeof path === "string" && (
        path === PERSONAL_WIKI_PREFIX
        || path.startsWith(PERSONAL_WIKI_PREFIX + "/")
        || path.startsWith(PERSONAL_WIKI_PREFIX + "?")
      );
      const successorSpacePath = typeof path === "string"
        && path.startsWith(SPACE_V2_PREFIX + "/");
      if (!canonicalPath && !personalWikiPath && !successorSpacePath) {
        throw new TypeError("Canonical path is invalid.");
      }
      const method = String(requestOptions.method || "GET").toUpperCase();
      const headers = { Accept: requestOptions.accept || "application/json" };
      let body;
      if (requestOptions.formData !== undefined) {
        body = requestOptions.formData;
      } else if (requestOptions.body !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(requestOptions.body);
      }
      if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
        const token = csrfToken();
        if (token) headers["X-CSRF-Token"] = token;
      }
      if (requestOptions.operationId) headers["Idempotency-Key"] = requestOptions.operationId;
      if (requestOptions.leaseToken !== undefined) {
        headers["X-Knowledge-Lease-Token"] = leaseToken(requestOptions.leaseToken);
      }
      if (requestOptions.fencingGeneration !== undefined) {
        headers["X-Knowledge-Fence"] = String(positive(requestOptions.fencingGeneration, "fencing_generation"));
      }
      if (requestOptions.sourceLeaseToken !== undefined) {
        headers["X-Knowledge-Source-Lease-Token"] = leaseToken(requestOptions.sourceLeaseToken);
      }
      if (requestOptions.sourceFencingGeneration !== undefined) {
        headers["X-Knowledge-Source-Fence"] = String(positive(requestOptions.sourceFencingGeneration, "source_fencing_generation"));
      }
      if (requestOptions.targetLeaseToken !== undefined) {
        headers["X-Knowledge-Target-Lease-Token"] = leaseToken(requestOptions.targetLeaseToken);
      }
      if (requestOptions.targetFencingGeneration !== undefined) {
        headers["X-Knowledge-Target-Fence"] = String(positive(requestOptions.targetFencingGeneration, "target_fencing_generation"));
      }
      if (requestOptions.ifMatch !== undefined) {
        headers["If-Match"] = `"${uuid(requestOptions.ifMatch, "if_match")}"`;
      }
      const response = await fetchImpl(path, {
        body,
        credentials: "same-origin",
        headers,
        method,
        signal: requestOptions.signal,
      });
      if (!response || typeof response.ok !== "boolean") {
        fail("TRANSPORT_FAILURE", "Canonical fetch returned no response.", 0, true);
      }
      if (!response.ok) throw await errorFromResponse(response);
      return response;
    }

    async function json(path, requestOptions) {
      const response = await request(path, requestOptions);
      let value;
      try { value = await response.json(); } catch (_) {
        fail("INVALID_CANONICAL_RESPONSE", "Canonical JSON response is invalid.");
      }
      object(value, "response");
      uuid(value.request_id, "request_id");
      return value;
    }

    function personalWikiProfile(value) {
      exactKeys(value, [
        "archive_reason", "archived_at", "created_at", "display_name",
        "is_default", "lifecycle_generation", "lifecycle_status",
        "row_version", "space_id", "updated_at",
      ], "personal Wiki profile");
      uuid(value.space_id, "personal_wiki.space_id");
      boundedText(value.display_name, "personal_wiki.display_name", 160);
      if (
        !["active", "archived"].includes(value.lifecycle_status)
        || typeof value.is_default !== "boolean"
        || !Number.isSafeInteger(value.lifecycle_generation)
        || value.lifecycle_generation < 1
      ) fail("INVALID_CANONICAL_RESPONSE", "Personal Wiki profile state is invalid.");
      positive(value.row_version, "personal_wiki.row_version");
      return Object.freeze(value);
    }

    async function listPersonalWikis({ lifecycleStatus = "active", query = "", cursor = null, limit = 50 } = {}) {
      if (!["active", "archived"].includes(lifecycleStatus)) throw new TypeError("lifecycleStatus is invalid.");
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new TypeError("limit is invalid.");
      if (typeof query !== "string" || query.length > 160) throw new TypeError("query is invalid.");
      if (cursor !== null && (typeof cursor !== "string" || !cursor || cursor.length > 4096)) throw new TypeError("cursor is invalid.");
      const params = new URLSearchParams({ lifecycle_status: lifecycleStatus, limit: String(limit) });
      if (query) params.set("query", query);
      if (cursor !== null) params.set("cursor", cursor);
      const value = await json(`${PERSONAL_WIKI_PREFIX}?${params.toString()}`);
      exactKeys(value, ["items", "next_cursor", "request_id"], "personal Wiki page");
      if (!Array.isArray(value.items) || value.items.length > limit || (value.next_cursor !== null && typeof value.next_cursor !== "string")) {
        fail("INVALID_CANONICAL_RESPONSE", "Personal Wiki page is invalid.");
      }
      return Object.freeze({ ...value, items: Object.freeze(value.items.map(personalWikiProfile)) });
    }

    async function searchPersonalWikis({ query, cursor = null, limit = 10 }) {
      if (typeof query !== "string" || !query.trim() || query.length > 500) throw new TypeError("query is invalid.");
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 10) throw new TypeError("limit is invalid.");
      if (cursor !== null && (typeof cursor !== "string" || !cursor || cursor.length > 4096)) throw new TypeError("cursor is invalid.");
      const body = { query };
      if (cursor !== null) body.cursor = cursor;
      if (limit !== 10) body.limit = limit;
      const value = await json(`${PERSONAL_WIKI_PREFIX}/search`, { body, method: "POST" });
      exactKeys(value, ["items", "next_cursor", "request_id"], "personal Wiki search");
      if (!Array.isArray(value.items) || value.items.length > limit || (value.next_cursor !== null && typeof value.next_cursor !== "string")) {
        fail("INVALID_CANONICAL_RESPONSE", "Personal Wiki search is invalid.");
      }
      const items = value.items.map((item) => {
        exactKeys(item, ["item_id", "provenance", "revision_id", "score", "snippet", "source_space_id", "title", "wiki_display_name"], "personal Wiki search item");
        uuid(item.item_id, "search.item_id");
        uuid(item.revision_id, "search.revision_id");
        uuid(item.source_space_id, "search.source_space_id");
        if (!Array.isArray(item.provenance) || typeof item.score !== "number") fail("INVALID_CANONICAL_RESPONSE", "Personal Wiki search item is invalid.");
        return Object.freeze(item);
      });
      return Object.freeze({ ...value, items: Object.freeze(items) });
    }

    async function personalWikiMutation(path, method, body, supplied) {
      const op = operationId(idFactory, supplied);
      const value = await json(path, {
        body: { ...body, operation_id: op }, method, operationId: op,
      });
      exactKeys(value, ["operation_id", "request_id", "resource_id", "row_version", "status"], "personal Wiki mutation");
      if (uuid(value.operation_id, "mutation.operation_id") !== op) fail("INVALID_CANONICAL_RESPONSE", "Personal Wiki mutation binding is invalid.");
      uuid(value.resource_id, "mutation.resource_id");
      positive(value.row_version, "mutation.row_version");
      string(value.status, "mutation.status");
      return Object.freeze(value);
    }

    async function createPersonalWiki({ displayName, expectedRowVersion, operationId: supplied }) {
      return personalWikiMutation(PERSONAL_WIKI_PREFIX, "POST", {
        display_name: boundedText(displayName, "displayName", 160),
        expected_row_version: positive(expectedRowVersion, "expectedRowVersion"),
      }, supplied);
    }

    async function renamePersonalWiki({ spaceId, displayName, expectedRowVersion, operationId: supplied }) {
      return personalWikiMutation(`${PERSONAL_WIKI_PREFIX}/${encodedId(spaceId, "space_id")}`, "PATCH", {
        display_name: boundedText(displayName, "displayName", 160),
        expected_row_version: positive(expectedRowVersion, "expectedRowVersion"),
      }, supplied);
    }

    async function setDefaultPersonalWiki({ spaceId, expectedRowVersion, operationId: supplied }) {
      return personalWikiMutation(`${PERSONAL_WIKI_PREFIX}/${encodedId(spaceId, "space_id")}/default`, "PUT", {
        expected_row_version: positive(expectedRowVersion, "expectedRowVersion"),
      }, supplied);
    }

    async function archivePersonalWiki({ spaceId, expectedRowVersion, reason = null, operationId: supplied }) {
      const body = { expected_row_version: positive(expectedRowVersion, "expectedRowVersion") };
      if (reason !== null) body.reason = boundedText(reason, "reason", 1000);
      return personalWikiMutation(`${PERSONAL_WIKI_PREFIX}/${encodedId(spaceId, "space_id")}/archive`, "POST", body, supplied);
    }

    async function restorePersonalWiki({ spaceId, expectedRowVersion, makeDefault = false, operationId: supplied }) {
      if (typeof makeDefault !== "boolean") throw new TypeError("makeDefault is invalid.");
      return personalWikiMutation(`${PERSONAL_WIKI_PREFIX}/${encodedId(spaceId, "space_id")}/restore`, "POST", {
        expected_row_version: positive(expectedRowVersion, "expectedRowVersion"),
        make_default: makeDefault,
      }, supplied);
    }

    function distributionTarget(value, field = "distribution target") {
      exactKeys(value, [
        "kind", "name", "org_path", "org_unit_id", "target_scope_id",
        "target_scope_kind",
      ], field);
      if (!["team", "part"].includes(value.kind) || value.target_scope_kind !== "org_unit") {
        fail("INVALID_CANONICAL_RESPONSE", field + " kind is invalid.");
      }
      uuid(value.org_unit_id, field + ".org_unit_id");
      if (uuid(value.target_scope_id, field + ".target_scope_id") !== value.org_unit_id) {
        fail("INVALID_CANONICAL_RESPONSE", field + " identity is invalid.");
      }
      boundedText(value.name, field + ".name", 160);
      if (!Array.isArray(value.org_path) || !value.org_path.length || value.org_path.length > 64) {
        fail("INVALID_CANONICAL_RESPONSE", field + " path is invalid.");
      }
      value.org_path.forEach((name, index) => boundedText(name, `${field}.org_path[${index}]`, 160));
      return Object.freeze(value);
    }

    async function listDistributionTargets({ spaceId }) {
      const id = uuid(spaceId, "space_id");
      const value = await json(`${SPACE_V2_PREFIX}/${encodedId(id, "space_id")}/distribution-targets`);
      exactKeys(value, ["items", "request_id", "source_space_id"], "distribution target list");
      if (uuid(value.source_space_id, "source_space_id") !== id || !Array.isArray(value.items) || value.items.length > 4096) {
        fail("INVALID_CANONICAL_RESPONSE", "Distribution target list binding is invalid.");
      }
      return Object.freeze({ ...value, items: Object.freeze(value.items.map(distributionTarget)) });
    }

    function distributionGrant(value) {
      exactKeys(value, [
        "created_at", "created_by_user_id", "grant_id", "grant_mode", "reason",
        "revoke_mode", "revoke_reason", "revoked_at", "revoked_by_user_id",
        "row_version", "source_item_id", "source_revision_id",
        "source_role_assignment_id", "source_space_id", "target_org_unit_id",
        "target_scope_id", "target_scope_kind", "updated_at",
      ], "distribution grant");
      for (const field of [
        "grant_id", "source_item_id", "source_revision_id", "source_role_assignment_id",
        "source_space_id", "target_org_unit_id", "target_scope_id",
      ]) uuid(value[field], `distribution.${field}`);
      if (value.grant_mode !== "read_only" || !["org_unit", "native_space"].includes(value.target_scope_kind)) {
        fail("INVALID_CANONICAL_RESPONSE", "Distribution grant state is invalid.");
      }
      positive(value.row_version, "distribution.row_version");
      boundedText(value.reason, "distribution.reason", 1000);
      return Object.freeze(value);
    }

    async function listDistributions({ spaceId, cursor = null, limit = 50 }) {
      const id = uuid(spaceId, "space_id");
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new TypeError("limit is invalid.");
      if (cursor !== null && (typeof cursor !== "string" || !cursor || cursor.length > 4096)) throw new TypeError("cursor is invalid.");
      const query = new URLSearchParams({ limit: String(limit) });
      if (cursor !== null) query.set("cursor", cursor);
      const value = await json(`${SPACE_V2_PREFIX}/${encodedId(id, "space_id")}/distributions?${query.toString()}`);
      exactKeys(value, ["items", "next_cursor", "request_id"], "distribution page");
      if (!Array.isArray(value.items) || value.items.length > limit || (value.next_cursor !== null && typeof value.next_cursor !== "string")) {
        fail("INVALID_CANONICAL_RESPONSE", "Distribution page is invalid.");
      }
      const items = value.items.map(distributionGrant);
      if (items.some((item) => item.source_space_id !== id)) {
        fail("INVALID_CANONICAL_RESPONSE", "Distribution source binding is invalid.");
      }
      return Object.freeze({ ...value, items: Object.freeze(items) });
    }

    function distributionMutation(value, operation, sourceSpaceId, expectedStatus) {
      exactKeys(value, [
        "grant_id", "operation_id", "request_id", "revoked", "row_version",
        "source_space_id", "status",
      ], "distribution mutation");
      if (
        uuid(value.operation_id, "mutation.operation_id") !== operation
        || uuid(value.source_space_id, "mutation.source_space_id") !== sourceSpaceId
        || value.status !== expectedStatus
        || value.revoked !== (expectedStatus === "revoked")
      ) fail("INVALID_CANONICAL_RESPONSE", "Distribution mutation binding is invalid.");
      uuid(value.grant_id, "mutation.grant_id");
      positive(value.row_version, "mutation.row_version");
      return Object.freeze(value);
    }

    async function createDistribution({ spaceId, itemId, revisionId, target, reason, operationId: supplied }) {
      const sourceSpaceId = uuid(spaceId, "space_id");
      const selectedTarget = distributionTarget(target, "target");
      const op = operationId(idFactory, supplied);
      const value = await json(`${SPACE_V2_PREFIX}/${encodedId(sourceSpaceId, "space_id")}/distributions`, {
        body: {
          operation_id: op,
          reason: boundedText(reason, "reason", 1000),
          source_item_id: uuid(itemId, "item_id"),
          source_revision_id: uuid(revisionId, "revision_id"),
          target_org_unit_id: selectedTarget.org_unit_id,
          target_scope_id: selectedTarget.target_scope_id,
          target_scope_kind: selectedTarget.target_scope_kind,
        },
        method: "POST",
        operationId: op,
      });
      return distributionMutation(value, op, sourceSpaceId, "created");
    }

    async function revokeDistribution({ spaceId, grantId, expectedRowVersion, reason, breakGlass = false, confirmation = null, operationId: supplied }) {
      const sourceSpaceId = uuid(spaceId, "space_id");
      if (typeof breakGlass !== "boolean" || (!breakGlass && confirmation !== null)) throw new TypeError("breakGlass is invalid.");
      const op = operationId(idFactory, supplied);
      const body = {
        break_glass: breakGlass,
        expected_row_version: positive(expectedRowVersion, "expected_row_version"),
        operation_id: op,
        reason: boundedText(reason, "reason", 1000),
      };
      if (confirmation !== null) body.confirmation = boundedText(confirmation, "confirmation", 256);
      const value = await json(`${SPACE_V2_PREFIX}/${encodedId(sourceSpaceId, "space_id")}/distributions/${encodedId(grantId, "grant_id")}/revoke`, {
        body, method: "POST", operationId: op,
      });
      return distributionMutation(value, op, sourceSpaceId, "revoked");
    }

    function semanticCorrection(value, operation, spaceId, edgeId) {
      exactKeys(value, [
        "edge_id", "edge_kind", "edge_status", "operation_id",
        "reevaluation_queued", "request_id", "row_version", "scope_space_id",
        "suppression_id", "suppression_status",
      ], "semantic correction");
      if (
        uuid(value.operation_id, "correction.operation_id") !== operation
        || uuid(value.scope_space_id, "correction.scope_space_id") !== spaceId
        || uuid(value.edge_id, "correction.edge_id") !== edgeId
        || value.edge_kind !== "auto"
        || typeof value.reevaluation_queued !== "boolean"
      ) fail("INVALID_CANONICAL_RESPONSE", "Semantic correction binding is invalid.");
      uuid(value.suppression_id, "correction.suppression_id");
      positive(value.row_version, "correction.row_version");
      return Object.freeze(value);
    }

    async function suppressSemanticEdge({ spaceId, edgeId, reason, expectedRowVersion = null, breakGlass = false, confirmation = null, operationId: supplied }) {
      const selectedSpaceId = uuid(spaceId, "space_id");
      const selectedEdgeId = uuid(edgeId, "edge_id");
      if (typeof breakGlass !== "boolean" || (!breakGlass && confirmation !== null)) throw new TypeError("breakGlass is invalid.");
      const op = operationId(idFactory, supplied);
      const body = { break_glass: breakGlass, operation_id: op, reason: boundedText(reason, "reason", 1000) };
      if (expectedRowVersion !== null) body.expected_row_version = positive(expectedRowVersion, "expected_row_version");
      if (confirmation !== null) body.confirmation = boundedText(confirmation, "confirmation", 256);
      const value = await json(`${SPACE_V2_PREFIX}/${encodedId(selectedSpaceId, "space_id")}/edges/${encodedId(selectedEdgeId, "edge_id")}/suppress`, {
        body, method: "POST", operationId: op,
      });
      return semanticCorrection(value, op, selectedSpaceId, selectedEdgeId);
    }

    async function liftSemanticEdgeSuppression({ spaceId, edgeId, reason, expectedRowVersion = null, operationId: supplied }) {
      const selectedSpaceId = uuid(spaceId, "space_id");
      const selectedEdgeId = uuid(edgeId, "edge_id");
      const op = operationId(idFactory, supplied);
      const body = { operation_id: op, reason: boundedText(reason, "reason", 1000) };
      if (expectedRowVersion !== null) body.expected_row_version = positive(expectedRowVersion, "expected_row_version");
      const value = await json(`${SPACE_V2_PREFIX}/${encodedId(selectedSpaceId, "space_id")}/edges/${encodedId(selectedEdgeId, "edge_id")}/suppression/lift`, {
        body, method: "POST", operationId: op,
      });
      return semanticCorrection(value, op, selectedSpaceId, selectedEdgeId);
    }

    function previewDocument(value) {
      object(value, "publication_preview");
      uuid(value.request_id, "preview.request_id");
      uuid(value.operation_id, "preview.operation_id");
      uuid(value.preview_id, "preview.preview_id");
      const status = string(value.status, "preview.status");
      if (value.wiki_generation !== undefined || value.publication_ready !== undefined) {
        const generation = object(value.wiki_generation, "preview.wiki_generation");
        const preserving = generation.schema_version === 2;
        const nativeAuthoring = generation.schema_version === 4;
        const roleAuthoring = generation.schema_version === 5;
        const pdfAuthoring = generation.schema_version === 6;
        const pdfVisualAuthoring = generation.schema_version === 7;
        const officeHybridAuthoring = generation.schema_version === 8;
        const pdfContinuation = generation.schema_version === 9;
        const authoringOnly = generation.schema_version === 3 || nativeAuthoring || roleAuthoring || pdfAuthoring || pdfVisualAuthoring || officeHybridAuthoring || pdfContinuation;
        const validShape = authoringOnly
          ? Object.keys(generation).sort().join(",") === "kind,schema_version,semantic_completeness_proven,semantic_review_performed,status"
            && generation.kind === (pdfContinuation ? "pdf_source_continuation" : officeHybridAuthoring ? "office_hybrid_native_page_information_authoring" : pdfVisualAuthoring ? "pdf_visual_information_authoring" : pdfAuthoring ? "pdf_information_authoring" : roleAuthoring ? "pptx_role_information_authoring"
              : nativeAuthoring ? "native_information_authoring" : "information_authoring")
            && generation.semantic_completeness_proven === false
            && generation.semantic_review_performed === false && generation.status !== "extraction_only"
          : preserving
          ? Object.keys(generation).sort().join(",") === "kind,schema_version,semantic_completeness_proven,status"
            && generation.kind === "information_preservation" && generation.semantic_completeness_proven === false
            && generation.status !== "extraction_only"
          : Object.keys(generation).sort().join(",") === "schema_version,status" && generation.schema_version === 1;
        const canBeReady = status === "ready" && ["ready", "extraction_only"].includes(generation.status);
        if (!validShape || !["pending", "ready", "extraction_only", "unavailable"].includes(generation.status)
          || typeof value.publication_ready !== "boolean"
          || (value.publication_ready && !canBeReady)
          || (generation.status === "pending" && !["queued", "extracting", "ready"].includes(status))
          || (["queued", "extracting", "failed"].includes(status) && ["ready", "extraction_only"].includes(generation.status))) {
          fail("INVALID_CANONICAL_RESPONSE", "preview Wiki readiness is invalid.");
        }
      }
      if (value.generation_progress !== undefined) {
        const progress = object(value.generation_progress, "preview.generation_progress");
        const phases = ["extraction_queued", "extracting", "planning_queued", "planning", "writing_queued",
          "writing", "assembling_queued", "assembling", "recovery_pending", "ready", "failed"];
        const counts = ["completed_batches", "total_batches", "queued_batches", "running_batches"];
        if (![7, 8, 9].includes(value.wiki_generation?.schema_version)
          || Object.keys(progress).sort().join(",") !== "completed_batches,phase,queued_batches,recovery_pending,running_batches,total_batches"
          || !phases.includes(progress.phase)
          || counts.some(key => !Number.isSafeInteger(progress[key]) || progress[key] < 0 || progress[key] > 100000)
          || progress.completed_batches + progress.queued_batches + progress.running_batches > progress.total_batches
          || typeof progress.recovery_pending !== "boolean"
          || progress.recovery_pending !== (progress.phase === "recovery_pending")) {
          fail("INVALID_CANONICAL_RESPONSE", "preview generation progress is invalid.");
        }
      }
      if (value.generation_worker !== undefined) {
        const worker = object(value.generation_worker, "preview.generation_worker");
        if (!value.wiki_generation || Object.keys(worker).sort().join(",") !== "automatic_recovery,status"
          || !["healthy", "recovering", "unavailable"].includes(worker.status)
          || typeof worker.automatic_recovery !== "boolean"
          || (worker.status === "recovering" && !worker.automatic_recovery)) {
          fail("INVALID_CANONICAL_RESPONSE", "preview generation worker status is invalid.");
        }
      }
      if (value.generation_error !== undefined) {
        const failure = object(value.generation_error, "preview.generation_error");
        const codes = ["MODEL_FAILURE", "INVALID_OUTPUT", "TIMEOUT", "WORK_LIMIT", "SOURCE_UNAVAILABLE",
          "AUTHORITY_UNAVAILABLE", "CANCELLED", "LEASE_LOST", "EXPIRED", "EXTRACTION_FAILED", "PROCESSING_FAILED"];
        if (![7, 8, 9].includes(value.wiki_generation?.schema_version) || value.wiki_generation.status !== "unavailable"
          || Object.keys(failure).sort().join(",") !== "code,retryable" || !codes.includes(failure.code)
          || typeof failure.retryable !== "boolean") {
          fail("INVALID_CANONICAL_RESPONSE", "preview generation failure is invalid.");
        }
      }
      if (["queued", "extracting"].includes(status)) {
        uuid(value.job_id, "preview.job_id");
        string(value.status_url, "preview.status_url");
      } else if (["ready", "consumed", "expired", "cancelled"].includes(status) && value.source_snapshot_sha256 !== undefined) {
        if (!SHA256_RE.test(value.source_snapshot_sha256)) fail("INVALID_CANONICAL_RESPONSE", "preview source hash is invalid.");
        if (!Array.isArray(value.available_target_modes) || !value.available_target_modes.length) {
          fail("INVALID_CANONICAL_RESPONSE", "preview target modes are invalid.");
        }
        const modes = new Set(value.available_target_modes);
        if (modes.size !== value.available_target_modes.length || [...modes].some((mode) => !["personal", "part", "both", "explicit"].includes(mode))) {
          fail("INVALID_CANONICAL_RESPONSE", "preview target modes are invalid.");
        }
        if (modes.has("explicit")) {
          if (!Array.isArray(value.available_targets) || value.available_targets.length !== 1) {
            fail("INVALID_CANONICAL_RESPONSE", "preview explicit target is invalid.");
          }
          higherPublicationTarget(value.available_targets[0], "preview.available_targets[0]");
        } else if (value.available_targets !== undefined) {
          fail("INVALID_CANONICAL_RESPONSE", "preview target descriptors are unexpected.");
        }
      } else if (!["failed", "cancelled"].includes(status)) {
        fail("INVALID_CANONICAL_RESPONSE", "preview status is invalid.");
      }
      return Object.freeze(value);
    }

    async function createPublicationPreview(source) {
      object(source, "source");
      const op = operationId(idFactory, source.operationId);
      const sourceType = string(source.sourceType, "sourceType");
      const target = source.target === undefined || source.target === null
        ? null : higherPublicationTarget(source.target, "source.target");
      let value;
      if (sourceType === "upload") {
        if (typeof FormData !== "function" || !source.file) throw new TypeError("An upload file is required.");
        const form = new FormData();
        form.append("operation_id", op);
        form.append("source_type", "upload");
        form.append("file", source.file);
        if (source.title) form.append("title", string(source.title, "title"));
        if (target !== null) form.append("target", JSON.stringify(target));
        value = await json(PREFIX + "/publication-previews", { method: "POST", formData: form, operationId: op });
      } else {
        const body = { operation_id: op, source_type: sourceType };
        if (target !== null) body.target = target;
        if (sourceType === "manual") {
          body.markdown = string(source.markdown, "markdown");
          if (source.title) body.title = string(source.title, "title");
        } else if (sourceType === "notion") {
          body.page_id = string(source.pageId, "pageId");
        } else if (sourceType === "chat") {
          body.conversation_id = uuid(source.conversationId, "conversationId");
          body.message_range = {
            from_sequence: positive(source.fromSequence, "fromSequence"),
            to_sequence: positive(source.toSequence, "toSequence"),
          };
          if (body.message_range.from_sequence > body.message_range.to_sequence) throw new TypeError("message range is invalid.");
        } else {
          throw new TypeError("sourceType is invalid.");
        }
        value = await json(PREFIX + "/publication-previews", { method: "POST", body, operationId: op });
      }
      return previewDocument(value);
    }

    async function probePublication() {
      try {
        await request(PREFIX + "/publication-previews", { method: "OPTIONS" });
        return true;
      } catch (error) {
        if (error instanceof CanonicalTransportError && error.status === 404) return false;
        throw error;
      }
    }

    async function getPublicationPreview(previewId) {
      return previewDocument(await json(PREFIX + "/publication-previews/" + encodedId(previewId, "preview_id")));
    }

    async function probePdfPublicationResume(previewId) {
      try {
        await request(PREFIX + "/publication-previews/" + encodedId(previewId, "preview_id") + "/resume", { method: "OPTIONS" });
        return true;
      } catch (error) {
        if (error instanceof CanonicalTransportError && error.status === 404) return false;
        throw error;
      }
    }

    async function resumePdfPublicationPreview({ previewId, operationId: supplied }) {
      const op = operationId(idFactory, supplied);
      const value = previewDocument(await json(PREFIX + "/publication-previews/" + encodedId(previewId, "preview_id") + "/resume", {
        method: "POST", body: { operation_id: op }, operationId: op,
      }));
      if (value.preview_id !== previewId) fail("INVALID_CANONICAL_RESPONSE", "PDF resume preview binding is invalid.");
      return value;
    }

    async function cancelPublicationPreview({ previewId, operationId: supplied }) {
      const op = operationId(idFactory, supplied);
      const value = await json(PREFIX + "/publication-previews/" + encodedId(previewId, "preview_id"), {
        method: "DELETE", body: { operation_id: op }, operationId: op,
      });
      uuid(value.resource_id, "resource_id");
      positive(value.row_version, "row_version");
      string(value.status, "status");
      if (value.operation_id !== op || value.resource_id !== previewId || Object.keys(value).sort().join(",") !== "operation_id,request_id,resource_id,row_version,status") {
        fail("INVALID_CANONICAL_RESPONSE", "preview cancellation binding is invalid.");
      }
      return Object.freeze(value);
    }

    function jobDocument(value, expectedJobId) {
      exactKeys(value, [
        "attempt_count", "cancel_requested", "cancellable", "completed_at",
        "created_at", "error", "handler_version", "job_id", "job_type",
        "max_attempts", "progress_permille", "request_id", "result", "state",
        "status_url", "updated_at",
      ], "durable job");
      if (uuid(value.job_id, "job.job_id") !== expectedJobId) {
        fail("INVALID_CANONICAL_RESPONSE", "Durable job identity is invalid.");
      }
      string(value.job_type, "job.job_type");
      string(value.handler_version, "job.handler_version");
      if (!JOB_STATES.has(value.state)) {
        fail("INVALID_CANONICAL_RESPONSE", "Durable job state is invalid.");
      }
      if (!Number.isSafeInteger(value.attempt_count) || value.attempt_count < 0) {
        fail("INVALID_CANONICAL_RESPONSE", "Durable job attempt_count is invalid.");
      }
      positive(value.max_attempts, "job.max_attempts");
      if (
        typeof value.cancellable !== "boolean"
        || typeof value.cancel_requested !== "boolean"
        || !Number.isSafeInteger(value.progress_permille)
        || value.progress_permille < 0
        || value.progress_permille > 1000
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Durable job progress shape is invalid.");
      }
      string(value.created_at, "job.created_at");
      string(value.updated_at, "job.updated_at");
      const expectedStatusUrl = `${PREFIX}/jobs/${expectedJobId}`;
      if (value.status_url !== expectedStatusUrl) {
        fail("INVALID_CANONICAL_RESPONSE", "Durable job status URL is invalid.");
      }
      if (["queued", "claimed", "running", "retry"].includes(value.state)) {
        if (value.completed_at !== null || value.result !== null || value.error !== null) {
          fail("INVALID_CANONICAL_RESPONSE", "Nonterminal durable job shape is invalid.");
        }
      } else {
        string(value.completed_at, "job.completed_at");
        if (value.state === "succeeded") {
          object(value.result, "job.result");
          if (value.error !== null) fail("INVALID_CANONICAL_RESPONSE", "Succeeded durable job error is invalid.");
        } else if (["failed", "dead"].includes(value.state)) {
          object(value.error, "job.error");
          if (value.result !== null) fail("INVALID_CANONICAL_RESPONSE", "Failed durable job result is invalid.");
        } else if (value.result !== null || value.error !== null) {
          fail("INVALID_CANONICAL_RESPONSE", "Cancelled durable job result is invalid.");
        }
      }
      return Object.freeze(value);
    }

    async function getJob(jobId) {
      const id = uuid(jobId, "job_id");
      return jobDocument(
        await json(`${PREFIX}/jobs/${encodedId(id, "job_id")}`),
        id,
      );
    }

    async function cancelJob({ jobId, operationId: supplied }) {
      const id = uuid(jobId, "job_id");
      const op = operationId(idFactory, supplied);
      const value = await json(`${PREFIX}/jobs/${encodedId(id, "job_id")}/cancel`, {
        body: { operation_id: op }, method: "POST", operationId: op,
      });
      exactKeys(value, ["operation_id", "request_id", "resource_id", "row_version", "status"], "durable job cancellation");
      if (
        uuid(value.operation_id, "cancel.operation_id") !== op
        || uuid(value.resource_id, "cancel.resource_id") !== id
        || value.row_version !== null
        || !JOB_CANCEL_STATUSES.has(value.status)
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Durable job cancellation binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function commitPublication({ previewId, sourceSnapshotSha256, targetMode, personalSpaceId = null, target = null, operationId: supplied }) {
      const op = operationId(idFactory, supplied);
      if (!SHA256_RE.test(sourceSnapshotSha256)) throw new TypeError("sourceSnapshotSha256 is invalid.");
      if (!["personal", "part", "both", "explicit"].includes(targetMode)) throw new TypeError("targetMode is invalid.");
      if (personalSpaceId !== null && !["personal", "both"].includes(targetMode)) throw new TypeError("personalSpaceId is invalid for this target mode.");
      if ((targetMode === "explicit") !== (target !== null)) throw new TypeError("target is required exactly for explicit publication.");
      const body = {
        operation_id: op,
        preview_id: uuid(previewId, "previewId"),
        source_snapshot_sha256: sourceSnapshotSha256,
        target_mode: targetMode,
      };
      if (personalSpaceId !== null) body.personal_space_id = uuid(personalSpaceId, "personalSpaceId");
      if (target !== null) body.target = higherPublicationTarget(target, "target");
      const value = await json(PREFIX + "/publications", {
        method: "POST",
        operationId: op,
        body,
      });
      if (!Array.isArray(value.results) || !value.results.length || value.results.length > 2) {
        fail("INVALID_CANONICAL_RESPONSE", "publication results are invalid.");
      }
      const resultKeys = value.body_source === undefined ? "operation_id,request_id,results" : "body_source,operation_id,request_id,results";
      if (value.operation_id !== op || Object.keys(value).sort().join(",") !== resultKeys
        || (value.body_source !== undefined && !["authored_wiki", "extraction", "preserved_wiki", "native_wiki", "pptx_role_wiki", "pdf_wiki", "pdf_visual_wiki", "office_hybrid_wiki", "pdf_continuation_wiki"].includes(value.body_source))) {
        fail("INVALID_CANONICAL_RESPONSE", "publication operation binding is invalid.");
      }
      const spaces = new Set();
      for (const result of value.results) {
        object(result, "publication_result");
        if (Object.keys(result).sort().join(",") !== "created_revision_id,finding_id,item_id,outcome,resolved_revision_id,source_artifact_id,space_id") {
          fail("INVALID_CANONICAL_RESPONSE", "publication target shape is invalid.");
        }
        const spaceId = uuid(result.space_id, "result.space_id");
        if (spaces.has(spaceId)) fail("INVALID_CANONICAL_RESPONSE", "publication target spaces are duplicated.");
        spaces.add(spaceId);
        uuid(result.source_artifact_id, "result.source_artifact_id");
        uuid(result.item_id, "result.item_id");
        uuid(result.resolved_revision_id, "result.resolved_revision_id");
        if (!["created", "provenance_added", "update_proposed", "no_op"].includes(result.outcome)) {
          fail("INVALID_CANONICAL_RESPONSE", "publication outcome is invalid.");
        }
        if (result.outcome === "created") {
          uuid(result.created_revision_id, "result.created_revision_id");
          if (result.finding_id !== null) fail("INVALID_CANONICAL_RESPONSE", "created target finding is invalid.");
        } else if (result.outcome === "update_proposed") {
          if (result.created_revision_id !== null) fail("INVALID_CANONICAL_RESPONSE", "proposal revision is invalid.");
          uuid(result.finding_id, "result.finding_id");
        } else if (result.created_revision_id !== null || result.finding_id !== null) {
          fail("INVALID_CANONICAL_RESPONSE", "non-creating target fields are invalid.");
        }
      }
      return Object.freeze(value);
    }

    async function spaces({ selectedSpaceId = null, conversationId = null } = {}) {
      if (selectedSpaceId !== null && conversationId !== null) {
        throw new TypeError("Select either a requested space or an existing conversation.");
      }
      const query = new URLSearchParams();
      if (selectedSpaceId !== null) query.set("selected_space_id", uuid(selectedSpaceId, "selectedSpaceId"));
      if (conversationId !== null) query.set("conversation_id", uuid(conversationId, "conversationId"));
      const suffix = query.toString();
      const value = await json(PREFIX + "/spaces" + (suffix ? "?" + suffix : ""));
      // The server may return any number of owned active personal Wikis along
      // with the authorized Part, Team and Responsibility views.
      if (!Array.isArray(value.items) || value.items.length < 1) {
        fail("INVALID_CANONICAL_RESPONSE", "Selectable spaces are invalid.");
      }
      positive(value.scope_epoch, "scope_epoch");
      return value;
    }

    function validateHistoryMessage(value) {
      exactKeys(value, ["id", "sequence_no", "role", "content", "created_at"], "history message");
      uuid(value.id, "message.id"); positive(value.sequence_no, "message.sequence_no");
      object(value.content, "message.content");
      if (!["system", "user", "assistant", "tool"].includes(value.role)
          || typeof value.created_at !== "string" || !Number.isFinite(Date.parse(value.created_at))) {
        fail("INVALID_CANONICAL_RESPONSE", "History message is invalid.");
      }
      return value;
    }

    async function getMessagePage(conversationId, { afterSequenceNo = 0, throughSequenceNo = null, limit = 100 } = {}) {
      if (!Number.isSafeInteger(afterSequenceNo) || afterSequenceNo < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100
          || throughSequenceNo !== null && (!Number.isSafeInteger(throughSequenceNo) || throughSequenceNo < afterSequenceNo)) {
        throw new TypeError("Invalid history page bounds.");
      }
      const params = new URLSearchParams({ after_sequence_no: String(afterSequenceNo), limit: String(limit) });
      if (throughSequenceNo !== null) params.set("through_sequence_no", String(throughSequenceNo));
      const page = await json(`${PREFIX}/conversations/${encodedId(conversationId, "conversation_id")}/messages?${params}`);
      exactKeys(page, ["request_id", "schema_version", "conversation_id", "items", "through_sequence_no", "has_more", "next_after_sequence_no"], "history page");
      if (page.schema_version !== 1 || page.conversation_id !== conversationId || !Array.isArray(page.items) || page.items.length > limit
          || !Number.isSafeInteger(page.through_sequence_no) || page.through_sequence_no < afterSequenceNo
          || throughSequenceNo !== null && page.through_sequence_no !== throughSequenceNo || typeof page.has_more !== "boolean") {
        fail("INVALID_CANONICAL_RESPONSE", "History page binding is invalid.");
      }
      let last = afterSequenceNo;
      const ids = new Set();
      for (const value of page.items) {
        validateHistoryMessage(value);
        if (value.sequence_no <= last || value.sequence_no > page.through_sequence_no || ids.has(value.id)) {
          fail("INVALID_CANONICAL_RESPONSE", "History message order is invalid.");
        }
        last = value.sequence_no; ids.add(value.id);
      }
      if (page.has_more ? !page.items.length || page.next_after_sequence_no !== last || last >= page.through_sequence_no
        : page.next_after_sequence_no !== null || last !== page.through_sequence_no) {
        fail("INVALID_CANONICAL_RESPONSE", "History page cursor is invalid.");
      }
      return page;
    }

    async function getTurnAnswer(conversationId, operation, expectedTurnId, expectedMessageId) {
      uuid(expectedTurnId, "turn_id"); uuid(expectedMessageId, "assistant_message_id");
      const answer = await json(`${PREFIX}/conversations/${encodedId(conversationId, "conversation_id")}/turns/${encodedId(operation, "operation_id")}/answer`);
      exactKeys(answer, ["request_id", "schema_version", "conversation_id", "operation_id", "turn_id", "message"], "turn answer");
      const message = validateHistoryMessage(answer.message);
      if (answer.schema_version !== 1 || answer.conversation_id !== conversationId || answer.operation_id !== operation
          || answer.turn_id !== expectedTurnId || message.id !== expectedMessageId || message.role !== "assistant") {
        fail("INVALID_CANONICAL_RESPONSE", "Completed answer binding is invalid.");
      }
      return message;
    }

    async function getConversation(conversationId, { includeAllMessages = true } = {}) {
      const value = await json(PREFIX + "/conversations/" + encodedId(conversationId, "conversation_id"));
      object(value.conversation, "conversation");
      if (value.conversation.id !== conversationId || !Array.isArray(value.messages) || !Array.isArray(value.pins) || !Array.isArray(value.context_bindings)) {
        fail("INVALID_CANONICAL_RESPONSE", "Conversation collections are invalid.");
      }
      if (includeAllMessages && value.messages.length === 100) {
        let last = 0, through = null;
        const messages = [...value.messages], ids = new Set();
        for (const message of messages) {
          validateHistoryMessage(message);
          if (message.sequence_no <= last || ids.has(message.id)) fail("INVALID_CANONICAL_RESPONSE", "History message order is invalid.");
          last = message.sequence_no; ids.add(message.id);
        }
        while (true) {
          const page = await getMessagePage(conversationId, { afterSequenceNo: last, throughSequenceNo: through });
          through = page.through_sequence_no;
          for (const message of page.items) {
            if (ids.has(message.id)) fail("INVALID_CANONICAL_RESPONSE", "Duplicate history message identity.");
            ids.add(message.id); messages.push(message);
          }
          if (!page.has_more) break;
          last = page.next_after_sequence_no;
        }
        return { ...value, messages };
      }
      return value;
    }

    async function listPendingTurns(conversationId, { afterSequenceNo = 0, limit = 20 } = {}) {
      const cid = encodedId(conversationId, "conversation_id");
      if (!Number.isSafeInteger(afterSequenceNo) || afterSequenceNo < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
        throw new TypeError("Invalid pending-turn page bounds.");
      }
      const value = await json(`${PREFIX}/conversations/${cid}/pending-turns?after_sequence_no=${afterSequenceNo}&limit=${limit}`);
      exactKeys(value, ["request_id", "schema_version", "conversation_id", "items", "has_more", "next_after_sequence_no"], "pending turns");
      if (value.schema_version !== 1 || value.conversation_id !== conversationId || !Array.isArray(value.items)
          || value.items.length > limit || typeof value.has_more !== "boolean") {
        fail("INVALID_CANONICAL_RESPONSE", "Pending-turn page binding is invalid.");
      }
      let previous = afterSequenceNo;
      const operations = new Set();
      for (const item of value.items) {
        exactKeys(item, ["operation_id", "user_message_id", "sequence_no", "state", "message", "can_resume", "reason"], "pending turn");
        uuid(item.operation_id, "operation_id"); uuid(item.user_message_id, "user_message_id");
        positive(item.sequence_no, "sequence_no");
        if (item.sequence_no <= previous || operations.has(item.operation_id)
            || !["accepted", "running"].includes(item.state) || typeof item.message !== "string"
            || typeof item.can_resume !== "boolean" || (item.can_resume ? item.reason !== null || !item.message
              : typeof item.reason !== "string" || !/^[A-Z][A-Z0-9_]+$/.test(item.reason))) {
          fail("INVALID_CANONICAL_RESPONSE", "Pending-turn binding is invalid.");
        }
        operations.add(item.operation_id); previous = item.sequence_no;
        Object.freeze(item);
      }
      if (value.has_more ? !value.items.length || value.next_after_sequence_no !== previous : value.next_after_sequence_no !== null) {
        fail("INVALID_CANONICAL_RESPONSE", "Pending-turn cursor is invalid.");
      }
      Object.freeze(value.items);
      return Object.freeze(value);
    }

    async function readSnapshot({ selectedSpaceId = null, conversationId = null } = {}) {
      const detail = conversationId ? await getConversation(conversationId, { includeAllMessages: false }) : null;
      const conversation = detail ? object(detail.conversation, "conversation") : null;
      const badgeSpace = conversation ? object(conversation.space, "conversation.space") : null;
      const spaceList = await spaces(
        selectedSpaceId !== null
          ? { selectedSpaceId }
          : badgeSpace && badgeSpace.id
            ? { conversationId }
            : {},
      );
      const ids = new Set(spaceList.items.map((item) => uuid(item.id, "space.id")));
      let selected = selectedSpaceId;
      if (selected !== null && !ids.has(selected)) fail("SPACE_NOT_SELECTABLE", "The requested space is not selectable.");
      if (!selected && badgeSpace && ids.has(badgeSpace.id)) selected = badgeSpace.id;
      if (!selected) selected = spaceList.items.find((item) => item.kind === "personal").id;
      return {
        conversation_badge: conversation ? {
          access_status: conversation.accessible ? "accessible" : "revoked",
          accessible: !!conversation.accessible,
          conversation_id: uuid(conversation.id, "conversation.id"),
          conversation_row_version: positive(conversation.row_version, "conversation.row_version"),
          conversation_status: conversation.archived ? "archived" : "active",
          conversation_title: string(conversation.title, "conversation.title"),
          immutable: true,
          org_path: badgeSpace.org_path,
          space_id: badgeSpace.id,
          space_kind: badgeSpace.kind,
          space_name: badgeSpace.name,
        } : null,
        scope_epoch: spaceList.scope_epoch,
        selected_space_id: selected,
        spaces: spaceList.items.map((item) => ({ ...item, selected: item.id === selected })),
      };
    }

    async function createConversation({ operationId: supplied, spaceId, title = "" }) {
      const op = operationId(idFactory, supplied);
      const body = { operation_id: op, space_id: uuid(spaceId, "space_id") };
      if (title) body.title = string(title, "title");
      const value = await json(PREFIX + "/conversations", { method: "POST", body, operationId: op });
      return { id: uuid(value.resource_id, "resource_id"), operation_id: op, row_version: positive(value.row_version, "row_version"), space_id: spaceId };
    }

    async function listConversations({ cursor = null, limit = 100 } = {}) {
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) throw new TypeError("limit is invalid.");
      const query = new URLSearchParams({ limit: String(limit) });
      if (cursor) query.set("cursor", string(cursor, "cursor"));
      return json(PREFIX + "/conversations?" + query.toString());
    }

    async function updateConversation({ conversationId, expectedRowVersion, title, archived, operationId: supplied }) {
      const op = operationId(idFactory, supplied);
      const body = { operation_id: op, expected_row_version: positive(expectedRowVersion, "expected_row_version") };
      if (title !== undefined) body.title = string(title, "title");
      if (archived !== undefined) body.archived = !!archived;
      return json(PREFIX + "/conversations/" + encodedId(conversationId, "conversation_id"), { method: "PATCH", body, operationId: op });
    }

    async function compactConversation({ conversationId, throughMessageSequence, operationId: supplied }) {
      const op = operationId(idFactory, supplied);
      const body = { operation_id: op, through_message_sequence: positive(throughMessageSequence, "through_message_sequence") };
      return json(PREFIX + "/conversations/" + encodedId(conversationId, "conversation_id") + "/compact", { method: "POST", body, operationId: op });
    }

    async function streamTurn({ conversationId, message, operationId: supplied, resumeAfterEventSequence = 0, signal = undefined, onEvent = null }) {
      const op = operationId(idFactory, supplied);
      if (typeof message !== "string" || !message) throw new TypeError("message is required.");
      if (!Number.isSafeInteger(resumeAfterEventSequence) || resumeAfterEventSequence < 0) throw new TypeError("resumeAfterEventSequence is invalid.");
      const body = { message, operation_id: op };
      if (resumeAfterEventSequence) body.resume_after_event_sequence = resumeAfterEventSequence;
      const response = await request(PREFIX + "/conversations/" + encodedId(conversationId, "conversation_id") + "/turns/stream", {
        accept: "text/event-stream", body, method: "POST", operationId: op, signal,
      });
      const events = decodeSse(await response.text(), op);
      if (typeof onEvent === "function") for (const event of events) await onEvent(event);
      const terminal = events[events.length - 1];
      if (terminal.event_type === "failed") {
        const payload = terminal.payload || {};
        const error = payload.error && typeof payload.error === "object" ? payload.error : {};
        const code = typeof error.code === "string" ? error.code : "AGENT_EXECUTION_FAILED";
        const messages = {
          CODEX_CLI_NOT_FOUND: "Codex CLI를 찾을 수 없습니다.",
          CODEX_NOT_AUTHENTICATED: "Codex CLI 로그인이 필요합니다.",
          CODEX_EXEC_TIMEOUT: "Codex 응답 시간이 초과되었습니다. 다시 시도해 주세요.",
          CODEX_EXEC_FAILED: "Codex 실행이 실패했습니다. 다시 시도해 주세요.",
          CODEX_EMPTY_OUTPUT: "Codex가 빈 응답을 반환했습니다. 다시 시도해 주세요.",
          CODEX_OUTPUT_INVALID: "Codex 응답이 안전한 출력 조건을 충족하지 못했습니다.",
          MODEL_RESPONSE_HTTP_STATUS: "모델 API 요청이 거절되었거나 서버 오류가 발생했습니다. 연결 설정과 서버 상태를 확인해 주세요.",
          MODEL_API_AUTH_FAILED: "모델 API 인증 또는 접근 권한을 확인할 수 없습니다. API 키와 모델 접근 권한을 확인해 주세요.",
          MODEL_API_INPUT_TOO_LARGE: "모델 API가 입력 크기를 허용하지 않았습니다. 요청 범위나 서버 입력 한도를 확인해 주세요.",
          MODEL_API_RATE_LIMITED: "모델 API 호출 한도에 도달했습니다. 잠시 후 다시 시도해 주세요.",
          MODEL_API_SERVER_ERROR: "모델 API 서버에서 오류가 발생했습니다. 잠시 후 다시 시도하고, 반복되면 서버 상태를 확인해 주세요.",
          MODEL_RESPONSE_ENCODING_UNSUPPORTED: "모델 API가 지원하지 않는 응답 인코딩을 반환했습니다. API 연결 설정을 확인해 주세요.",
          MODEL_RESPONSE_TOO_LARGE: "모델 API 응답이 처리 한도를 초과했습니다. 불완전한 답변은 저장하지 않았습니다.",
          MODEL_RESPONSE_INVALID: "모델 API 응답 형식이 올바르지 않습니다. API 호환성을 확인해 주세요.",
          MODEL_RESPONSE_INCOMPLETE: "모델 API가 답변을 끝까지 생성하지 못했습니다. 출력 한도 또는 종료 상태를 확인해 주세요.",
          MODEL_EMPTY_OUTPUT: "모델 API가 빈 답변을 반환했습니다. 다시 시도해 주세요.",
          MODEL_EXEC_TIMEOUT: "모델 API 응답 시간이 초과되었습니다. 다시 시도해 주세요.",
          MODEL_EXEC_FAILED: "모델 API 연결 또는 실행에 실패했습니다. 연결 상태를 확인하고 다시 시도해 주세요.",
        };
        fail(code, messages[code] || "The canonical Agent turn failed.", 409, !!error.retryable, true);
      }
      const assistant = await getTurnAnswer(conversationId, op, terminal.turn_id, terminal.payload.assistant_message_id);
      const detail = await getConversation(conversationId, { includeAllMessages: false });
      return Object.freeze({
        assistantText: messageText(assistant),
        conversation: detail,
        events,
        operationId: op,
        terminalSequence: terminal.event_sequence,
      });
    }

    async function getItem(spaceId, itemId) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}`);
      object(value.item, "item");
      object(value.timeline, "timeline");
      object(value.lifecycle, "lifecycle");
      const references = value.references === undefined ? [] : value.references;
      if (!Array.isArray(references) || references.length > 1000) {
        fail("INVALID_CANONICAL_RESPONSE", "Item references are invalid.");
      }
      for (const reference of references) {
        object(reference, "reference");
        uuid(reference.id, "reference.id");
        positive(reference.ordinal, "reference.ordinal");
        if (reference.syntax_version !== "item-ref-v1" || !["available", "unavailable"].includes(reference.availability_status)) {
          fail("INVALID_CANONICAL_RESPONSE", "Item reference state is invalid.");
        }
        if (reference.target !== null) {
          object(reference.target, "reference.target");
          uuid(reference.target.item_id, "reference.target.item_id");
          uuid(reference.target.observed_revision_id, "reference.target.observed_revision_id");
          uuid(reference.target.current_revision_id, "reference.target.current_revision_id");
          uuid(reference.target.source_space_id, "reference.target.source_space_id");
          if (reference.availability_status !== "available" || typeof reference.target.newer_revision_available !== "boolean") {
            fail("INVALID_CANONICAL_RESPONSE", "Item reference target is invalid.");
          }
        } else if (reference.availability_status !== "unavailable") {
          fail("INVALID_CANONICAL_RESPONSE", "Available item reference target is missing.");
        }
      }
      if (
        uuid(value.item.id, "item.id") !== itemId
        || uuid(value.selected_view_space_id, "selected_view_space_id") !== spaceId
        || uuid(value.item.selected_view_space_id, "item.selected_view_space_id") !== spaceId
        || uuid(value.item.source_space_id, "item.source_space_id") !== uuid(value.item.space_id, "item.space_id")
        || value.item.source_space_id !== value.source_space_id
        || value.item.inherited !== value.inherited
        || value.item.origin !== value.origin
        || !Array.isArray(value.capabilities)
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Item capabilities are invalid.");
      }
      return Object.freeze({ ...value, references: Object.freeze(references) });
    }

    async function listItemSourceArtifacts(spaceId, itemId) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/source-artifacts`);
      exactKeys(value, [
        "corpus_closure_sha256", "item_id", "items", "request_id",
        "selected_view_space_id",
      ], "source artifact page");
      if (
        uuid(value.item_id, "source_artifacts.item_id") !== itemId
        || uuid(value.selected_view_space_id, "source_artifacts.selected_view_space_id") !== spaceId
        || !SHA256_RE.test(value.corpus_closure_sha256)
        || !Array.isArray(value.items)
        || value.items.length > 1000
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Source artifact page binding is invalid.");
      }
      const seen = new Set();
      for (const artifact of value.items) {
        const hasPermission = Object.prototype.hasOwnProperty.call(artifact, "download_permitted");
        const keys = [
          "artifact_id", "created_at", "download_url", "media_type",
          "origin_type", "original_filename", "size_bytes", "source_revision_id",
        ];
        if (hasPermission) keys.push("download_permitted");
        exactKeys(artifact, keys, "source artifact");
        const artifactId = uuid(artifact.artifact_id, "source_artifact.artifact_id");
        if (
          seen.has(artifactId)
          || !["upload", "notion", "chat", "manual", "legacy"].includes(artifact.origin_type)
          || typeof artifact.original_filename !== "string"
          || !artifact.original_filename
          || artifact.original_filename.length > 240
          || typeof artifact.media_type !== "string"
          || !artifact.media_type
          || artifact.media_type.length > 255
          || !Number.isSafeInteger(artifact.size_bytes)
          || artifact.size_bytes < 0
          || (hasPermission && artifact.download_permitted !== null
              && typeof artifact.download_permitted !== "boolean")
        ) {
          fail("INVALID_CANONICAL_RESPONSE", "Source artifact metadata is invalid.");
        }
        uuid(artifact.source_revision_id, "source_artifact.source_revision_id");
        string(artifact.created_at, "source_artifact.created_at");
        const expectedUrl = `${PREFIX}/spaces/${sid}/items/${iid}/source-artifacts/${artifactId}/download`;
        if (artifact.download_url !== expectedUrl) {
          fail("INVALID_CANONICAL_RESPONSE", "Source artifact download binding is invalid.");
        }
        seen.add(artifactId);
      }
      return Object.freeze({
        ...value,
        items: Object.freeze(value.items.map((artifact) => Object.freeze({ ...artifact }))),
      });
    }

    async function acquireItemLease({ spaceId, itemId, editorSessionId, rawToken, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const op = operationId(idFactory, supplied);
      const body = {
        editor_session_id: uuid(editorSessionId, "editor_session_id"),
        operation_id: op,
      };
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/lease/acquire`, {
        body, leaseToken: rawToken, method: "POST", operationId: op,
      });
      exactKeys(value, ["absolute_expires_at", "base_revision_id", "expires_at", "fencing_generation", "lease_id", "operation_id", "request_id"], "lease");
      if (uuid(value.operation_id, "lease.operation_id") !== op) fail("INVALID_CANONICAL_RESPONSE", "Lease operation binding is invalid.");
      uuid(value.request_id, "lease.request_id");
      uuid(value.lease_id, "lease.lease_id");
      uuid(value.base_revision_id, "lease.base_revision_id");
      positive(value.fencing_generation, "lease.fencing_generation");
      string(value.expires_at, "lease.expires_at");
      string(value.absolute_expires_at, "lease.absolute_expires_at");
      return Object.freeze(value);
    }

    async function heartbeatItemLease({ spaceId, itemId, editorSessionId, leaseId, fencingGeneration, rawToken }) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const expectedLeaseId = uuid(leaseId, "lease_id");
      const expectedFence = positive(fencingGeneration, "fencing_generation");
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/lease/heartbeat`, {
        body: {
          editor_session_id: uuid(editorSessionId, "editor_session_id"),
          lease_id: expectedLeaseId,
        },
        fencingGeneration: expectedFence,
        leaseToken: rawToken,
        method: "POST",
      });
      exactKeys(value, ["expires_at", "fencing_generation", "lease_id", "request_id"], "heartbeat");
      if (uuid(value.lease_id, "heartbeat.lease_id") !== expectedLeaseId || positive(value.fencing_generation, "heartbeat.fencing_generation") !== expectedFence) {
        fail("INVALID_CANONICAL_RESPONSE", "Heartbeat binding is invalid.");
      }
      uuid(value.request_id, "heartbeat.request_id");
      string(value.expires_at, "heartbeat.expires_at");
      return Object.freeze(value);
    }

    async function releaseItemLease({ spaceId, itemId, editorSessionId, leaseId, fencingGeneration, rawToken, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const op = operationId(idFactory, supplied);
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/lease/release`, {
        body: {
          editor_session_id: uuid(editorSessionId, "editor_session_id"),
          lease_id: uuid(leaseId, "lease_id"),
          operation_id: op,
        },
        fencingGeneration,
        leaseToken: rawToken,
        method: "POST",
        operationId: op,
      });
      exactKeys(value, ["operation_id", "request_id", "resource_id", "row_version", "status"], "lease release");
      if (value.operation_id !== op || value.resource_id !== itemId || value.status !== "released") fail("INVALID_CANONICAL_RESPONSE", "Lease release binding is invalid.");
      uuid(value.request_id, "release.request_id");
      positive(value.row_version, "release.row_version");
      return Object.freeze(value);
    }

    async function forceReleaseItemLease({ spaceId, itemId, reason, confirmation, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const op = operationId(idFactory, supplied);
      if (confirmation !== "FORCE_RELEASE") throw new TypeError("force-release confirmation is invalid.");
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/lease/force-release`, {
        body: { confirmation, operation_id: op, reason: boundedText(reason, "reason", 2000) },
        method: "POST",
        operationId: op,
      });
      exactKeys(value, ["operation_id", "request_id", "resource_id", "row_version", "status"], "force release");
      if (value.operation_id !== op || value.resource_id !== itemId || value.status !== "force_released") fail("INVALID_CANONICAL_RESPONSE", "Force-release binding is invalid.");
      uuid(value.request_id, "force_release.request_id");
      positive(value.row_version, "force_release.row_version");
      return Object.freeze(value);
    }

    async function updateItem({ spaceId, itemId, editorSessionId, leaseId, fencingGeneration, expectedRowVersion, expectedRevisionId, markdown, reason, rawToken, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const op = operationId(idFactory, supplied);
      if (typeof markdown !== "string" || markdown.length < 1 || markdown.length > 2000000) throw new TypeError("markdown is invalid.");
      const expectedRevision = uuid(expectedRevisionId, "expected_revision_id");
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}`, {
        body: {
          editor_session_id: uuid(editorSessionId, "editor_session_id"),
          expected_revision_id: expectedRevision,
          expected_row_version: positive(expectedRowVersion, "expected_row_version"),
          lease_id: uuid(leaseId, "lease_id"),
          markdown,
          operation_id: op,
          reason: boundedText(reason, "reason", 2000),
        },
        fencingGeneration,
        ifMatch: expectedRevision,
        leaseToken: rawToken,
        method: "PUT",
        operationId: op,
      });
      exactKeys(value, ["item_id", "operation_id", "request_id", "revision_id", "row_version", "status"], "item update");
      if (value.operation_id !== op || value.item_id !== itemId || value.status !== "updated") fail("INVALID_CANONICAL_RESPONSE", "Item update binding is invalid.");
      uuid(value.request_id, "update.request_id");
      uuid(value.revision_id, "update.revision_id");
      positive(value.row_version, "update.row_version");
      return Object.freeze(value);
    }

    async function listRevisions(spaceId, itemId, options = {}) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const limit = options.limit === undefined ? 50 : options.limit;
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
        throw new TypeError("limit is invalid.");
      }
      const query = new URLSearchParams({ limit: String(limit) });
      if (options.cursor !== undefined && options.cursor !== null) {
        query.set("cursor", string(options.cursor, "cursor"));
      }
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/revisions?${query.toString()}`);
      if (!Array.isArray(value.items) || typeof value.has_more !== "boolean") {
        fail("INVALID_CANONICAL_RESPONSE", "Revision history is invalid.");
      }
      const seen = new Set();
      for (const revision of value.items) {
        object(revision, "revision");
        const revisionId = uuid(revision.id, "revision.id");
        if (seen.has(revisionId) || uuid(revision.item_id, "revision.item_id") !== itemId) {
          fail("INVALID_CANONICAL_RESPONSE", "Revision history binding is invalid.");
        }
        seen.add(revisionId);
      }
      return Object.freeze(value);
    }

    async function getRevision(spaceId, itemId, revisionId) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const rid = encodedId(revisionId, "revision_id");
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/revisions/${rid}`);
      object(value.revision, "revision");
      if (uuid(value.revision.id, "revision.id") !== revisionId || uuid(value.revision.item_id, "revision.item_id") !== itemId) {
        fail("INVALID_CANONICAL_RESPONSE", "Revision binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function compareRevisions(spaceId, itemId, fromRevisionId, toRevisionId) {
      const sid = encodedId(spaceId, "space_id");
      const iid = encodedId(itemId, "item_id");
      const fromId = uuid(fromRevisionId, "from_revision_id");
      const toId = uuid(toRevisionId, "to_revision_id");
      const query = new URLSearchParams({ from: fromId, to: toId });
      const value = await json(`${PREFIX}/spaces/${sid}/items/${iid}/compare?${query.toString()}`);
      if (value.from_revision_id !== fromId || value.to_revision_id !== toId || value.diff_format !== "unified-v1" || !Array.isArray(value.hunks) || !SHA256_RE.test(value.diff_sha256)) {
        fail("INVALID_CANONICAL_RESPONSE", "Revision comparison is invalid.");
      }
      return Object.freeze(value);
    }

    async function getFinding(spaceId, findingId) {
      const sid = encodedId(spaceId, "space_id");
      const fid = encodedId(findingId, "finding_id");
      const value = await json(`${PREFIX}/spaces/${sid}/curation/findings/${fid}`);
      exactKeys(value, ["finding", "request_id"], "finding response");
      exactKeys(value.finding, [
        "confidence", "created_at", "decision_mode", "detector_name", "detector_version",
        "evidence", "finding_kind", "id", "model_id", "prompt_version",
        "proposed_action", "proposed_action_evidence", "required_reviewer",
        "review_reason", "reviewed_at", "reviewed_by", "risk_tier", "row_version",
        "rule_version", "severity", "source_item_id", "source_revision_id", "space_id",
        "status", "target_item_id", "target_revision_id", "updated_at", "visibility_effect",
      ], "finding");
      if (
        uuid(value.finding.id, "finding.id") !== findingId
        || uuid(value.finding.space_id, "finding.space_id") !== spaceId
        || !Number.isSafeInteger(value.finding.row_version)
        || value.finding.row_version < 1
        || !["pending", "approved", "rejected", "ignored", "obsolete", "applied", "apply_failed"].includes(value.finding.status)
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Finding binding is invalid.");
      }
      object(value.finding.evidence, "finding.evidence");
      object(value.finding.proposed_action_evidence, "finding.proposed_action_evidence");
      return Object.freeze(value);
    }

    async function reviewFinding({ spaceId, findingId, expectedFindingRowVersion, decision, reason, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const fid = encodedId(findingId, "finding_id");
      const op = operationId(idFactory, supplied);
      if (!["approve", "reject", "ignore"].includes(decision)) {
        throw new TypeError("decision is invalid.");
      }
      const value = await json(`${PREFIX}/spaces/${sid}/curation/findings/${fid}/review`, {
        body: {
          decision,
          expected_finding_row_version: positive(expectedFindingRowVersion, "expected_finding_row_version"),
          operation_id: op,
          reason: boundedText(reason, "reason", 2000),
        },
        method: "POST",
        operationId: op,
      });
      exactKeys(value, ["finding_id", "operation_id", "outcome", "request_id", "row_version"], "finding review");
      if (
        uuid(value.finding_id, "review.finding_id") !== findingId
        || uuid(value.operation_id, "review.operation_id") !== op
        || !["approved", "rejected", "ignored", "obsolete"].includes(value.outcome)
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Finding review binding is invalid.");
      }
      positive(value.row_version, "review.row_version");
      return Object.freeze(value);
    }

    async function getCurationShadowMetrics(spaceId, options = {}) {
      const sid = encodedId(spaceId, "space_id");
      const sampleSize = options.sampleSize === undefined ? 25 : options.sampleSize;
      if (!Number.isSafeInteger(sampleSize) || sampleSize < 1 || sampleSize > 100) {
        throw new TypeError("sample_size is invalid.");
      }
      const value = await json(`${PREFIX}/spaces/${sid}/curation/shadow-metrics?sample_size=${sampleSize}`);
      exactKeys(value, [
        "finding_count", "human_reviewed_count", "mode", "model_accepted_count",
        "model_adjudicated_count", "model_automatic_execution_count", "model_ignored_count",
        "model_obsolete_count", "model_proposal_count", "model_rejected_count",
        "pending_count", "precision_denominator", "precision_numerator", "precision_ppm",
        "report_status", "request_id", "risky_model_automatic_execution_count", "sample",
        "schema_version", "space_id", "t3_model_automatic_execution_count",
      ], "curation shadow metrics");
      if (
        uuid(value.space_id, "metrics.space_id") !== spaceId
        || value.mode !== "shadow_proposal_only"
        || value.schema_version !== 1
        || !["pass", "fail"].includes(value.report_status)
        || !Array.isArray(value.sample)
      ) {
        fail("INVALID_CANONICAL_RESPONSE", "Curation shadow metrics are invalid.");
      }
      for (const field of [
        "finding_count", "human_reviewed_count", "model_accepted_count",
        "model_adjudicated_count", "model_automatic_execution_count", "model_ignored_count",
        "model_obsolete_count", "model_proposal_count", "model_rejected_count",
        "pending_count", "precision_denominator", "precision_numerator",
        "risky_model_automatic_execution_count", "t3_model_automatic_execution_count",
      ]) {
        if (!Number.isSafeInteger(value[field]) || value[field] < 0) {
          fail("INVALID_CANONICAL_RESPONSE", `metrics.${field} is invalid.`);
        }
      }
      if (value.precision_ppm !== null && (!Number.isSafeInteger(value.precision_ppm) || value.precision_ppm < 0 || value.precision_ppm > 1000000)) {
        fail("INVALID_CANONICAL_RESPONSE", "metrics.precision_ppm is invalid.");
      }
      return Object.freeze(value);
    }

    async function previewChangeProposal({ spaceId, proposalId }) {
      const sid = encodedId(spaceId, "space_id");
      const pid = encodedId(proposalId, "proposal_id");
      const value = await json(`${PREFIX}/spaces/${sid}/change-proposals/${pid}/preview`, {
        body: {}, method: "POST",
      });
      exactKeys(value, [
        "current_source_revision_id", "current_target_revision_id", "current_target_row_version",
        "finding_id", "finding_row_version", "panes", "preview_sha256", "proposal_id",
        "proposal_kind", "proposal_row_version", "proposal_version", "proposed_result",
        "request_id", "stale", "state",
      ], "proposal preview");
      if (uuid(value.proposal_id, "preview.proposal_id") !== proposalId || typeof value.stale !== "boolean" || !Array.isArray(value.panes) || value.panes.length !== 3 || !SHA256_RE.test(value.preview_sha256)) {
        fail("INVALID_CANONICAL_RESPONSE", "Proposal preview binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function rebaseChangeProposal({ spaceId, proposalId, expectedProposalRowVersion, expectedFindingRowVersion, expectedSourceRevisionId, expectedTargetRevisionId, proposedMarkdown = null, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const pid = encodedId(proposalId, "proposal_id");
      const op = operationId(idFactory, supplied);
      const body = {
        expected_finding_row_version: positive(expectedFindingRowVersion, "expected_finding_row_version"),
        expected_proposal_row_version: positive(expectedProposalRowVersion, "expected_proposal_row_version"),
        expected_source_revision_id: expectedSourceRevisionId === null ? null : uuid(expectedSourceRevisionId, "expected_source_revision_id"),
        expected_target_revision_id: uuid(expectedTargetRevisionId, "expected_target_revision_id"),
        operation_id: op,
      };
      if (proposedMarkdown !== null) body.proposed_markdown = markdownText(proposedMarkdown, "proposed_markdown");
      const value = await json(`${PREFIX}/spaces/${sid}/change-proposals/${pid}/rebase`, {
        body, method: "POST", operationId: op,
      });
      exactKeys(value, [
        "finding_id", "finding_row_version", "old_proposal_id", "operation_id",
        "preview_sha256", "proposal_id", "proposal_row_version", "proposal_series_id",
        "proposal_version", "request_id", "source_revision_id", "status", "target_revision_id",
      ], "proposal rebase");
      if (uuid(value.old_proposal_id, "rebase.old_proposal_id") !== proposalId || uuid(value.operation_id, "rebase.operation_id") !== op || value.status !== "pending_review") {
        fail("INVALID_CANONICAL_RESPONSE", "Proposal rebase binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function applyChangeProposal({ spaceId, proposalId, expectedProposalRowVersion, expectedFindingRowVersion, expectedTargetItemRowVersion, expectedTargetRevisionId, expectedSourceRevisionId, previewSha256, editorSessionId, leaseId, fencingGeneration, rawToken, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const pid = encodedId(proposalId, "proposal_id");
      const op = operationId(idFactory, supplied);
      const value = await json(`${PREFIX}/spaces/${sid}/change-proposals/${pid}/apply`, {
        body: {
          editor_session_id: uuid(editorSessionId, "editor_session_id"),
          expected_finding_row_version: positive(expectedFindingRowVersion, "expected_finding_row_version"),
          expected_proposal_row_version: positive(expectedProposalRowVersion, "expected_proposal_row_version"),
          expected_source_revision_id: expectedSourceRevisionId === null ? null : uuid(expectedSourceRevisionId, "expected_source_revision_id"),
          expected_target_item_row_version: positive(expectedTargetItemRowVersion, "expected_target_item_row_version"),
          expected_target_revision_id: uuid(expectedTargetRevisionId, "expected_target_revision_id"),
          lease_id: uuid(leaseId, "lease_id"),
          operation_id: op,
          preview_sha256: SHA256_RE.test(previewSha256) ? previewSha256 : fail("INVALID_CANONICAL_RESPONSE", "preview_sha256 is invalid."),
        },
        fencingGeneration, leaseToken: rawToken, method: "POST", operationId: op,
      });
      exactKeys(value, [
        "finding_id", "finding_row_version", "item_row_version", "operation_id",
        "proposal_id", "proposal_row_version", "request_id", "rescan_job_id",
        "result_revision_id", "status", "target_item_id",
      ], "proposal apply");
      if (uuid(value.proposal_id, "apply.proposal_id") !== proposalId || uuid(value.operation_id, "apply.operation_id") !== op || value.status !== "applied") {
        fail("INVALID_CANONICAL_RESPONSE", "Proposal apply binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function previewMerge({ spaceId, sourceItemId, sourceRevisionId, targetItemId, targetRevisionId, resultMarkdown }) {
      const sid = encodedId(spaceId, "space_id");
      const value = await json(`${PREFIX}/spaces/${sid}/merges/preview`, {
        body: {
          result_markdown: markdownText(resultMarkdown, "result_markdown"),
          source_item_id: uuid(sourceItemId, "source_item_id"),
          source_revision_id: uuid(sourceRevisionId, "source_revision_id"),
          target_item_id: uuid(targetItemId, "target_item_id"),
          target_revision_id: uuid(targetRevisionId, "target_revision_id"),
        },
        method: "POST",
      });
      exactKeys(value, ["edge_impact", "panes", "preview_sha256", "reference_impact", "request_id", "source_revision_id", "stale", "target_revision_id"], "merge preview");
      if (uuid(value.source_revision_id, "merge.source_revision_id") !== sourceRevisionId || uuid(value.target_revision_id, "merge.target_revision_id") !== targetRevisionId || value.stale !== false || !Array.isArray(value.panes) || value.panes.length !== 3 || !SHA256_RE.test(value.preview_sha256)) {
        fail("INVALID_CANONICAL_RESPONSE", "Merge preview binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function applyMerge({ spaceId, sourceItemId, sourceRevisionId, targetItemId, targetRevisionId, resultMarkdown, previewSha256, sourceEditorSessionId, sourceLeaseId, sourceFencingGeneration, sourceRawToken, targetEditorSessionId, targetLeaseId, targetFencingGeneration, targetRawToken, operationId: supplied }) {
      const sid = encodedId(spaceId, "space_id");
      const op = operationId(idFactory, supplied);
      if (!SHA256_RE.test(previewSha256)) throw new TypeError("preview_sha256 is invalid.");
      const value = await json(`${PREFIX}/spaces/${sid}/merges/apply`, {
        body: {
          operation_id: op,
          preview_sha256: previewSha256,
          result_markdown: markdownText(resultMarkdown, "result_markdown"),
          source_editor_session_id: uuid(sourceEditorSessionId, "source_editor_session_id"),
          source_item_id: uuid(sourceItemId, "source_item_id"),
          source_lease_id: uuid(sourceLeaseId, "source_lease_id"),
          source_revision_id: uuid(sourceRevisionId, "source_revision_id"),
          target_editor_session_id: uuid(targetEditorSessionId, "target_editor_session_id"),
          target_item_id: uuid(targetItemId, "target_item_id"),
          target_lease_id: uuid(targetLeaseId, "target_lease_id"),
          target_revision_id: uuid(targetRevisionId, "target_revision_id"),
        },
        method: "POST", operationId: op,
        sourceFencingGeneration, sourceLeaseToken: sourceRawToken,
        targetFencingGeneration, targetLeaseToken: targetRawToken,
      });
      exactKeys(value, ["merge_event_id", "operation_id", "redirect_relation_id", "request_id", "result_revision_id", "source_item_id", "status", "target_item_id"], "merge apply");
      if (uuid(value.operation_id, "merge.operation_id") !== op || uuid(value.source_item_id, "merge.source_item_id") !== sourceItemId || uuid(value.target_item_id, "merge.target_item_id") !== targetItemId || value.status !== "applied") {
        fail("INVALID_CANONICAL_RESPONSE", "Merge apply binding is invalid.");
      }
      return Object.freeze(value);
    }

    async function getSystemDocument(spaceId, documentName) {
      const sid = encodedId(spaceId, "space_id");
      if (!["index", "log"].includes(documentName)) throw new TypeError("Unknown system document.");
      const value = await json(`${SPACE_V2_PREFIX}/${sid}/system-docs/${documentName}`);
      exactKeys(value, ["request_id", "schema_version", "space_id", "document_name", "corpus_closure_sha256", "entry_count", "content"], "system document");
      if (value.schema_version !== 1 || uuid(value.space_id, "system document.space_id") !== spaceId
        || value.document_name !== documentName || !SHA256_RE.test(value.corpus_closure_sha256)
        || !Number.isSafeInteger(value.entry_count) || value.entry_count < 0
        || typeof value.content !== "string" || !value.content.trim()) {
        fail("INVALID_CANONICAL_RESPONSE", "System document binding is invalid.");
      }
      // A complete metadata inventory has no fixed document-count/body-text cap.
      // It is rendered as text, not interpreted as HTML or an editable Wiki body.
      return Object.freeze(value);
    }

    async function management(spaceId, surface, options = {}) {
      const sid = encodedId(spaceId, "space_id");
      if (surface === "list") {
        const hasLimit = options.limit !== undefined;
        const hasCursor = options.cursor !== undefined && options.cursor !== null;
        if (!hasLimit && !hasCursor) return json(`${PREFIX}/spaces/${sid}/items`);
        const limit = hasLimit ? options.limit : 50;
        if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
          throw new TypeError("limit is invalid.");
        }
        const query = new URLSearchParams({ limit: String(limit) });
        if (hasCursor) query.set("cursor", string(options.cursor, "cursor"));
        return json(`${PREFIX}/spaces/${sid}/items?${query.toString()}`);
      }
      if (surface === "graph") return json(`${PREFIX}/spaces/${sid}/graph`);
      if (surface === "index_log") return json(`${PREFIX}/spaces/${sid}/projection-status`);
      if (surface === "activity") return json(`${PREFIX}/spaces/${sid}/activity`);
      if (surface === "candidates") {
        const value = await json(`${PREFIX}/spaces/${sid}/curation/findings`);
        exactKeys(value, ["corpus_closure_sha256", "has_more", "items", "next_cursor", "request_id"], "finding page");
        if (
          !Array.isArray(value.items)
          || typeof value.has_more !== "boolean"
          || !SHA256_RE.test(value.corpus_closure_sha256)
        ) {
          fail("INVALID_CANONICAL_RESPONSE", "Finding page is invalid.");
        }
        const seen = new Set();
        for (const finding of value.items) {
          exactKeys(finding, [
            "capabilities", "confidence", "created_at", "detector_name", "detector_version",
            "evidence", "finding_kind", "id", "inherited", "model_id", "origin",
            "overlay_grant_id", "prompt_version", "proposed_action", "required_reviewer",
            "risk_tier", "row_version", "rule_version", "selected_view_space_id", "severity",
            "source_item_id", "source_revision_id", "source_space_id", "space_id", "status",
            "target_item_id", "target_revision_id", "updated_at", "visibility_effect",
          ], "finding summary");
          const findingId = uuid(finding.id, "finding.id");
          if (
            seen.has(findingId)
            || uuid(finding.selected_view_space_id, "finding.selected_view_space_id") !== spaceId
            || uuid(finding.space_id, "finding.space_id") !== uuid(finding.source_space_id, "finding.source_space_id")
            || typeof finding.inherited !== "boolean"
            || !["native", "inherited", "overlay"].includes(finding.origin)
            || !Array.isArray(finding.capabilities)
          ) {
            fail("INVALID_CANONICAL_RESPONSE", "Finding page binding is invalid.");
          }
          seen.add(findingId);
          object(finding.evidence, "finding.evidence");
          positive(finding.row_version, "finding.row_version");
        }
        return Object.freeze(value);
      }
      if (surface === "search") {
        const query = typeof options.query === "string" && options.query ? options.query : "*";
        return json(`${PREFIX}/spaces/${sid}/search`, { method: "POST", body: { query } });
      }
      if (surface === "pins") return { has_more: false, items: [], next_cursor: null, space_id: spaceId };
      throw new TypeError("Unknown management surface.");
    }

    async function probe() {
      try { return await spaces(); }
      catch (error) {
        if (error instanceof CanonicalTransportError && error.status === 404) return null;
        throw error;
      }
    }

    return Object.freeze({
      acquireItemLease, applyChangeProposal, applyMerge, archivePersonalWiki, cancelJob, cancelPublicationPreview, commitPublication, compactConversation,
      createConversation, createDistribution, createPersonalWiki, createPublicationPreview, getConversation, getMessagePage, getTurnAnswer, listPendingTurns,
      compareRevisions, forceReleaseItemLease, getCurationShadowMetrics, getFinding, getItem, getJob, getPublicationPreview, getRevision,
      getSystemDocument, heartbeatItemLease,
      liftSemanticEdgeSuppression, listConversations, listDistributions, listDistributionTargets, listItemSourceArtifacts, listPersonalWikis, listRevisions, management, probe, readSnapshot,
      previewChangeProposal, previewMerge, probePublication, probePdfPublicationResume, rebaseChangeProposal, releaseItemLease, resumePdfPublicationPreview,
      renamePersonalWiki, restorePersonalWiki, revokeDistribution, reviewFinding, searchPersonalWikis, setDefaultPersonalWiki, spaces, streamTurn, suppressSemanticEdge, updateConversation,
      updateItem,
    });
  }

  return Object.freeze({ CanonicalTransportError, PERSONAL_WIKI_PREFIX, PREFIX, SPACE_V2_PREFIX, createLeaseToken, createTransport, decodeSse, messageText });
});



