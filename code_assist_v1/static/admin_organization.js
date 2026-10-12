/* Canonical P1.8 organization/account administration UI.
 *
 * This module is deliberately independent from the legacy account registry.
 * Every organization, membership, leader, and account-status mutation is
 * built from one canonical tree snapshot or one change-preview snapshot.
 */
(function (global) {
  "use strict";

  const ADMIN_PREFIX = "/api/admin/v1";
  const HIGHER_ADMIN_PREFIX = "/api/admin/v2/org";
  const KNOWLEDGE_V2_SPACE_PREFIX = "/api/knowledge/v2/spaces";
  const RESPONSIBILITY_ROOT_ID = "fb90d31e-9576-5823-84ef-241b7552f214";
  const RESPONSIBILITY_ROOT_NAME = "AMHS담당";
  const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
  const USER_ID_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/;
  const MAX_VERSION = Number.MAX_SAFE_INTEGER;

  class AdminOrganizationError extends Error {
    constructor(message, { status = 0, code = "NETWORK_ERROR", requestId = "", details = null, retryable = false } = {}) {
      super(String(message || "관리자 요청을 완료하지 못했습니다."));
      this.name = "AdminOrganizationError";
      this.status = Number(status || 0);
      this.code = String(code || "INTERNAL_ERROR");
      this.requestId = String(requestId || "");
      this.details = details;
      this.retryable = retryable === true;
    }
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function stableValue(value) {
    if (Array.isArray(value)) return value.map(stableValue);
    if (value && typeof value === "object") {
      const result = {};
      Object.keys(value).sort().forEach(key => { result[key] = stableValue(value[key]); });
      return result;
    }
    return value;
  }

  function cloneJson(value) {
    if (Array.isArray(value)) return value.map(cloneJson);
    if (value && typeof value === "object") {
      const result = {};
      Object.keys(value).forEach(key => { result[key] = cloneJson(value[key]); });
      return result;
    }
    return value;
  }

  function freezeJson(value) {
    if (Array.isArray(value)) value.forEach(freezeJson);
    else if (value && typeof value === "object") Object.values(value).forEach(freezeJson);
    return Object.freeze(value);
  }

  function stableStringify(value) {
    return JSON.stringify(stableValue(value));
  }

  function positiveVersion(value) {
    return Number.isInteger(value) && value >= 1 && value <= MAX_VERSION;
  }

  function nullableUuid(value) {
    return value === null || (typeof value === "string" && UUID_RE.test(value));
  }

  function validRequestId(value) {
    return typeof value === "string" && value.length >= 1 && value.length <= 128;
  }

  function normalizeSnapshot(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new AdminOrganizationError("조직 응답이 올바른 객체가 아닙니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
    }
    if (stableStringify(Object.keys(value).sort()) !== stableStringify(["request_id", "root", "users"])) {
      throw new AdminOrganizationError("조직 응답에 허용되지 않은 top-level 필드가 있습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
    }
    if (!validRequestId(value.request_id) || !value.root || !Array.isArray(value.users)) {
      throw new AdminOrganizationError("조직 응답에 root/users가 없습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
    }
    const units = new Map();
    const rows = [];
    const visit = (unit, ancestors, seen) => {
      if (!unit || typeof unit !== "object" || Array.isArray(unit)) throw new Error("unit");
      const unitKeys = ["children", "id", "kind", "leader", "member_count", "name", "parent_id", "row_version", "space_status", "status", "warnings"];
      if (stableStringify(Object.keys(unit).sort()) !== stableStringify(unitKeys)) throw new Error("unit_fields");
      if (!UUID_RE.test(String(unit.id || "")) || units.has(unit.id)) throw new Error("unit_id");
      if (!nullableUuid(unit.parent_id)) throw new Error("parent_id");
      if (!["responsibility", "team", "part"].includes(unit.kind)) throw new Error("kind");
      if (!["active", "inactive"].includes(unit.status) || !positiveVersion(unit.row_version)) throw new Error("state");
      if (typeof unit.name !== "string" || !unit.name.trim() || unit.name.length > 120 || !Array.isArray(unit.children)) throw new Error("name");
      if (!Number.isSafeInteger(unit.member_count) || unit.member_count < 0 || !Array.isArray(unit.warnings)) throw new Error("counts");
      if (!["not_created", "active", "write_disabled", "frozen", "future_only"].includes(unit.space_status) || unit.warnings.some(value => typeof value !== "string")) throw new Error("space");
      if (unit.leader !== null) {
        const leaderKeys = ["assignment_id", "display_name", "user_id", "valid_from"];
        if (unit.kind !== "part" || !unit.leader
            || stableStringify(Object.keys(unit.leader).sort()) !== stableStringify(leaderKeys)
            || !UUID_RE.test(String(unit.leader.assignment_id || ""))
            || !USER_ID_RE.test(String(unit.leader.user_id || ""))
            || typeof unit.leader.display_name !== "string"
            || !unit.leader.display_name.trim()
            || unit.leader.display_name.length > 120
            || typeof unit.leader.valid_from !== "string"
            || Number.isNaN(Date.parse(unit.leader.valid_from))) throw new Error("leader");
      }
      if (seen.has(unit.id)) throw new Error("cycle");
      const nextSeen = new Set(seen);
      nextSeen.add(unit.id);
      const row = { unit, depth: ancestors.length, path: [...ancestors.map(item => item.name), unit.name] };
      units.set(unit.id, row);
      rows.push(row);
      unit.children.forEach(child => {
        if (child.parent_id !== unit.id) throw new Error("child_parent");
        if ((unit.kind === "responsibility" && child.kind !== "team") || (unit.kind === "team" && child.kind !== "part") || unit.kind === "part") throw new Error("child_kind");
        visit(child, [...ancestors, unit], nextSeen);
      });
    };
    try {
      visit(value.root, [], new Set());
      if (value.root.id !== RESPONSIBILITY_ROOT_ID || value.root.name !== RESPONSIBILITY_ROOT_NAME || value.root.kind !== "responsibility" || value.root.parent_id !== null) throw new Error("root");
    } catch (error) {
      throw new AdminOrganizationError("조직 계층 응답이 canonical 계약과 다릅니다.", {
        code: "INVALID_CANONICAL_SNAPSHOT",
        details: String(error?.message || error),
      });
    }

    const users = [];
    let previousId = "";
    const seenUsers = new Set();
    const seenMemberships = new Set();
    for (const user of value.users) {
      if (!user || typeof user !== "object" || Array.isArray(user)) throw new AdminOrganizationError("사용자 응답이 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      const id = String(user.id || "");
      const allowedUserKeys = ["account_status", "current_affiliation_id", "current_membership_id", "current_part_id", "display_name", "home_org_unit_id", "home_org_unit_kind", "id", "row_version", "scope_epoch", "system_role"];
      if (stableStringify(Object.keys(user).sort()) !== stableStringify(allowedUserKeys)) throw new AdminOrganizationError("사용자 응답에 허용되지 않은 필드가 있습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (!USER_ID_RE.test(id) || seenUsers.has(id) || (previousId && id < previousId)) throw new AdminOrganizationError("사용자 정렬/ID가 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (!["admin", "user"].includes(user.system_role) || !["active", "disabled"].includes(user.account_status)) throw new AdminOrganizationError("사용자 상태가 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (typeof user.display_name !== "string" || !user.display_name.trim() || user.display_name.length > 120) throw new AdminOrganizationError("사용자 표시 이름이 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (!positiveVersion(user.scope_epoch) || !positiveVersion(user.row_version)) throw new AdminOrganizationError("사용자 version이 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (!nullableUuid(user.current_part_id) || !nullableUuid(user.current_membership_id) || !nullableUuid(user.current_affiliation_id) || !nullableUuid(user.home_org_unit_id)) throw new AdminOrganizationError("사용자 조직 fence가 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (![null, "part", "team", "responsibility"].includes(user.home_org_unit_kind)) throw new AdminOrganizationError("사용자 직속 소속 종류가 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if ((user.current_part_id === null) !== (user.current_membership_id === null)) throw new AdminOrganizationError("사용자 membership mirror가 일치하지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if ((user.current_affiliation_id === null) !== (user.home_org_unit_id === null) || (user.home_org_unit_id === null) !== (user.home_org_unit_kind === null)) throw new AdminOrganizationError("사용자 직속 소속 mirror가 일치하지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (user.current_part_id !== null && (user.current_affiliation_id !== user.current_membership_id || user.home_org_unit_id !== user.current_part_id || user.home_org_unit_kind !== "part")) throw new AdminOrganizationError("사용자 Part 소속 mirror가 일치하지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (user.current_part_id === null && user.home_org_unit_kind === "part") throw new AdminOrganizationError("Part 직속 소속에 membership이 없습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (user.current_membership_id !== null && seenMemberships.has(user.current_membership_id)) throw new AdminOrganizationError("사용자 membership episode가 중복되었습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (user.account_status === "disabled" && user.home_org_unit_id !== null) throw new AdminOrganizationError("비활성 사용자가 조직 권한을 보유합니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      if (user.current_part_id !== null) {
        const part = units.get(user.current_part_id)?.unit;
        if (!part || part.kind !== "part" || part.status !== "active") throw new AdminOrganizationError("사용자의 현재 파트가 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      }
      if (user.home_org_unit_id !== null) {
        const home = units.get(user.home_org_unit_id)?.unit;
        if (!home || home.kind !== user.home_org_unit_kind || home.status !== "active") throw new AdminOrganizationError("사용자의 직속 조직이 올바르지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      }
      seenUsers.add(id);
      if (user.current_membership_id !== null) seenMemberships.add(user.current_membership_id);
      previousId = id;
      users.push(Object.freeze({ ...user }));
    }
    for (const row of rows) {
      const leader = row.unit.leader;
      if (!leader) continue;
      const user = users.find(value => value.id === leader.user_id);
      if (!user || user.account_status !== "active" || user.current_part_id !== row.unit.id || user.display_name !== leader.display_name) {
        throw new AdminOrganizationError("파트장과 canonical 사용자 snapshot이 일치하지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      }
    }
    const expectedPartCounts = new Map();
    users.forEach(user => {
      if (user.current_part_id !== null) expectedPartCounts.set(user.current_part_id, (expectedPartCounts.get(user.current_part_id) || 0) + 1);
    });
    const validateAggregateCount = unit => {
      const expected = unit.kind === "part"
        ? (expectedPartCounts.get(unit.id) || 0)
        : unit.children.reduce((total, child) => total + validateAggregateCount(child), 0);
      if (unit.member_count !== expected) throw new AdminOrganizationError("조직 member_count가 canonical 사용자 snapshot과 일치하지 않습니다.", { code: "INVALID_CANONICAL_SNAPSHOT" });
      return expected;
    };
    validateAggregateCount(value.root);
    return Object.freeze({ request_id: String(value.request_id || ""), root: value.root, users: Object.freeze(users), units, rows: Object.freeze(rows) });
  }

  function normalizePreview(value, expectedUnitId) {
    if (!value || typeof value !== "object" || Array.isArray(value) || !validRequestId(value.request_id) || value.unit_id !== expectedUnitId) throw new AdminOrganizationError("변경 preview correlation이 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    const previewKeys = ["allowed", "blockers", "expires_at", "leader_impact", "member_impact", "preview_token", "request_id", "space_impact", "unit_id"];
    if (stableStringify(Object.keys(value).sort()) !== stableStringify(previewKeys)
        || typeof value.allowed !== "boolean" || !Array.isArray(value.blockers)
        || value.blockers.some(blocker => !blocker || typeof blocker !== "object" || Array.isArray(blocker)
          || stableStringify(Object.keys(blocker).sort()) !== stableStringify(["code", "message", "resource_id"])
          || typeof blocker.code !== "string" || !blocker.code || blocker.code.length > 80
          || typeof blocker.message !== "string" || !blocker.message.trim() || blocker.message.length > 1000
          || !(blocker.resource_id === null || (typeof blocker.resource_id === "string" && blocker.resource_id.length <= 120)))
        || typeof value.preview_token !== "string" || value.preview_token.length < 32 || value.preview_token.length > 4096
        || typeof value.expires_at !== "string" || Number.isNaN(Date.parse(value.expires_at))) throw new AdminOrganizationError("변경 preview 상태가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    if (value.allowed !== (value.blockers.length === 0)) throw new AdminOrganizationError("변경 preview allowed/blocker 상태가 일치하지 않습니다.", { code: "INVALID_PREVIEW" });
    const impact = value.member_impact;
    if (!impact || stableStringify(Object.keys(impact).sort()) !== stableStringify(["current_member_count", "descendant_member_count", "member_ids", "members"])
        || !Array.isArray(impact.member_ids) || !Array.isArray(impact.members)
        || !Number.isSafeInteger(impact.current_member_count) || impact.current_member_count < 0
        || !Number.isSafeInteger(impact.descendant_member_count) || impact.descendant_member_count < 0
        || impact.members.length > 200) throw new AdminOrganizationError("변경 preview member impact가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    const totalMemberCount = impact.current_member_count + impact.descendant_member_count;
    const expectedListedCount = Math.min(totalMemberCount, 200);
    if (impact.members.length !== expectedListedCount || impact.member_ids.length !== expectedListedCount
        || (totalMemberCount > 200 && (value.allowed || !value.blockers.some(blocker => blocker.code === "MEMBER_RESOLUTION_LIMIT")))) {
      throw new AdminOrganizationError("변경 preview member count가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    }
    const seen = new Set();
    impact.members.forEach(member => {
      if (!member || stableStringify(Object.keys(member).sort()) !== stableStringify(["expected_user_row_version", "user_id"]) || !USER_ID_RE.test(String(member.user_id || "")) || !positiveVersion(member.expected_user_row_version) || seen.has(member.user_id)) throw new AdminOrganizationError("변경 preview member fence가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
      seen.add(member.user_id);
    });
    if (stableStringify([...seen]) !== stableStringify(impact.member_ids)) throw new AdminOrganizationError("변경 preview member 목록이 일치하지 않습니다.", { code: "INVALID_PREVIEW" });
    const leader = value.leader_impact;
    if (!leader || stableStringify(Object.keys(leader).sort()) !== stableStringify(["leader_assignment_id", "leader_user_id", "will_close"]) || !nullableUuid(leader.leader_assignment_id) || !(leader.leader_user_id === null || USER_ID_RE.test(String(leader.leader_user_id))) || typeof leader.will_close !== "boolean" || ((leader.leader_assignment_id === null) !== (leader.leader_user_id === null))) throw new AdminOrganizationError("변경 preview leader fence가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    const space = value.space_impact;
    if (!space || stableStringify(Object.keys(space).sort()) !== stableStringify(["knowledge_item_count", "row_version", "space_id", "space_status", "write_enabled"]) || !nullableUuid(space.space_id) || !(space.row_version === null || positiveVersion(space.row_version)) || ((space.space_id === null) !== (space.row_version === null)) || !["not_created", "active", "write_disabled", "frozen", "future_only"].includes(space.space_status) || typeof space.write_enabled !== "boolean" || !Number.isSafeInteger(space.knowledge_item_count) || space.knowledge_item_count < 0) throw new AdminOrganizationError("변경 preview space fence가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    return value;
  }

  function normalizeMutationResponse(value, expectedOperationId) {
    const allowed = ["operation_id", "request_id", "resource_id", "row_version", "status"];
    if (!value || typeof value !== "object" || Array.isArray(value)
        || stableStringify(Object.keys(value).sort()) !== stableStringify(allowed)
        || !validRequestId(value.request_id)
        || value.operation_id !== expectedOperationId
        || typeof value.status !== "string"
        || !value.status
        || value.status.length > 80
        || !nullableUuid(value.resource_id)
        || !(value.row_version === null || positiveVersion(value.row_version))) {
      throw new AdminOrganizationError(
        "변경 응답의 correlation 또는 결과 형식이 올바르지 않습니다.",
        { code: "INVALID_RESPONSE" },
      );
    }
    return Object.freeze({ ...value });
  }

  function normalizeHigherRoleStatus(value, expectedUnitId, expectedRole) {
    const keys = [
      "assignee_display_name", "assignee_user_id", "assignment_generation",
      "assignment_id", "assignment_row_version", "org_unit_id", "request_id",
      "role", "space_generation", "space_id", "space_row_version",
      "write_enabled",
    ];
    const assignmentFields = [
      value?.assignment_id,
      value?.assignment_generation,
      value?.assignment_row_version,
      value?.assignee_user_id,
      value?.assignee_display_name,
    ];
    const assignmentEmpty = assignmentFields.every(field => field === null);
    const assignmentPresent = UUID_RE.test(String(value?.assignment_id || ""))
      && positiveVersion(value?.assignment_generation)
      && positiveVersion(value?.assignment_row_version)
      && USER_ID_RE.test(String(value?.assignee_user_id || ""))
      && typeof value?.assignee_display_name === "string"
      && value.assignee_display_name.trim().length >= 1
      && value.assignee_display_name.length <= 120;
    if (
      !value || typeof value !== "object" || Array.isArray(value)
      || stableStringify(Object.keys(value).sort()) !== stableStringify(keys)
      || !validRequestId(value.request_id)
      || value.org_unit_id !== expectedUnitId
      || value.role !== expectedRole
      || !UUID_RE.test(String(value.space_id || ""))
      || !positiveVersion(value.space_row_version)
      || !Number.isSafeInteger(value.space_generation)
      || value.space_generation < 0
      || typeof value.write_enabled !== "boolean"
      || (!assignmentEmpty && !assignmentPresent)
      || (assignmentEmpty && value.write_enabled)
    ) {
      throw new AdminOrganizationError("상위 지식 역할 상태 응답이 canonical 계약과 다릅니다.", {
        code: "INVALID_HIGHER_ROLE_STATUS",
      });
    }
    return Object.freeze({ ...value });
  }

  function normalizeHigherRoleMutationResponse(value, expectedOperationId) {
    const keys = [
      "assignment_generation", "assignment_id", "assignment_row_version",
      "operation_id", "org_unit_id", "request_id", "space_generation",
      "space_id", "space_row_version", "status", "write_enabled",
    ];
    const empty = value?.assignment_id === null
      && value?.assignment_generation === null
      && value?.assignment_row_version === null;
    const present = UUID_RE.test(String(value?.assignment_id || ""))
      && positiveVersion(value?.assignment_generation)
      && positiveVersion(value?.assignment_row_version);
    if (
      !value || typeof value !== "object" || Array.isArray(value)
      || stableStringify(Object.keys(value).sort()) !== stableStringify(keys)
      || value.operation_id !== expectedOperationId
      || !validRequestId(value.request_id)
      || !["assigned", "replaced", "updated", "ended"].includes(value.status)
      || !UUID_RE.test(String(value.org_unit_id || ""))
      || !UUID_RE.test(String(value.space_id || ""))
      || !positiveVersion(value.space_row_version)
      || !Number.isSafeInteger(value.space_generation)
      || value.space_generation < 0
      || typeof value.write_enabled !== "boolean"
      || (!empty && !present)
      || (value.status === "ended" && (!empty || value.write_enabled))
    ) {
      throw new AdminOrganizationError("상위 지식 역할 변경 응답이 canonical 계약과 다릅니다.", {
        code: "INVALID_HIGHER_ROLE_MUTATION",
      });
    }
    return Object.freeze({ ...value });
  }

  function normalizeDistributionMutationResponse(value, expectedOperationId) {
    const keys = [
      "grant_id", "operation_id", "request_id", "revoked", "row_version",
      "source_space_id", "status",
    ];
    if (
      !value || typeof value !== "object" || Array.isArray(value)
      || stableStringify(Object.keys(value).sort()) !== stableStringify(keys)
      || value.operation_id !== expectedOperationId
      || !validRequestId(value.request_id)
      || !UUID_RE.test(String(value.grant_id || ""))
      || !UUID_RE.test(String(value.source_space_id || ""))
      || !positiveVersion(value.row_version)
      || !["created", "revoked"].includes(value.status)
      || value.revoked !== (value.status === "revoked")
    ) throw new AdminOrganizationError("배포 변경 응답이 canonical 계약과 다릅니다.", { code: "INVALID_DISTRIBUTION_MUTATION" });
    return Object.freeze({ ...value });
  }

  function normalizeSemanticCorrectionResponse(value, expectedOperationId) {
    const keys = [
      "edge_id", "edge_kind", "edge_status", "operation_id",
      "reevaluation_queued", "request_id", "row_version", "scope_space_id",
      "suppression_id", "suppression_status",
    ];
    if (
      !value || typeof value !== "object" || Array.isArray(value)
      || stableStringify(Object.keys(value).sort()) !== stableStringify(keys)
      || value.operation_id !== expectedOperationId
      || !validRequestId(value.request_id)
      || !UUID_RE.test(String(value.edge_id || ""))
      || !UUID_RE.test(String(value.scope_space_id || ""))
      || !UUID_RE.test(String(value.suppression_id || ""))
      || value.edge_kind !== "auto"
      || typeof value.reevaluation_queued !== "boolean"
      || !positiveVersion(value.row_version)
    ) throw new AdminOrganizationError("semantic edge 변경 응답이 canonical 계약과 다릅니다.", { code: "INVALID_SEMANTIC_CORRECTION" });
    return Object.freeze({ ...value });
  }

  function createOperationStore(uuidFactory) {
    if (typeof uuidFactory !== "function") throw new TypeError("uuidFactory is required");
    const pending = new Map();
    return Object.freeze({
      prepare(intentKey, semanticBody) {
        const fingerprint = stableStringify(semanticBody);
        const existing = pending.get(intentKey);
        if (existing && existing.fingerprint === fingerprint) return existing.operationId;
        if (existing) {
          throw new AdminOrganizationError(
            "이전 요청의 응답을 확인하지 못했습니다. 같은 내용으로 먼저 재시도하세요.",
            { code: "UNKNOWN_OPERATION_OUTCOME" },
          );
        }
        const operationId = String(uuidFactory());
        if (!UUID_RE.test(operationId)) throw new Error("crypto.randomUUID() returned a non-canonical UUID");
        pending.set(intentKey, {
          fingerprint,
          operationId,
          semanticBody: freezeJson(cloneJson(semanticBody)),
          transport: null,
        });
        return operationId;
      },
      attach(intentKey, transport) {
        const existing = pending.get(intentKey);
        if (!existing) throw new Error("pending operation does not exist");
        existing.transport = Object.freeze({ ...transport });
      },
      complete(intentKey) { pending.delete(intentKey); },
      entries() {
        return Object.freeze([...pending.entries()].map(([intentKey, value]) => Object.freeze({
          intentKey,
          operationId: value.operationId,
          semanticBody: value.semanticBody,
          transport: value.transport,
        })));
      },
      get(intentKey) {
        const value = pending.get(intentKey);
        return value ? Object.freeze({
          intentKey,
          operationId: value.operationId,
          semanticBody: value.semanticBody,
          transport: value.transport,
        }) : null;
      },
      peek(intentKey) { return pending.get(intentKey)?.operationId || null; },
    });
  }

  function prepareMutation(operationStore, intentKey, semanticBody) {
    const operationId = operationStore.prepare(intentKey, semanticBody);
    const storedBody = operationStore.get(intentKey)?.semanticBody;
    if (!storedBody) throw new Error("pending operation body is missing");
    return Object.freeze({
      body: Object.freeze({ operation_id: operationId, ...storedBody }),
      headers: Object.freeze({ "Idempotency-Key": operationId }),
      operationId,
    });
  }

  function buildInactivationResolution(preview, destinations) {
    const members = preview?.member_impact?.members;
    if (!Array.isArray(members)) throw new AdminOrganizationError("preview member fence가 없습니다.", { code: "INVALID_PREVIEW" });
    const destinationMap = destinations || {};
    const leaderAssignmentId = preview?.leader_impact?.leader_assignment_id ?? null;
    const spaceRowVersion = preview?.space_impact?.row_version ?? null;
    if (!nullableUuid(leaderAssignmentId)) throw new AdminOrganizationError("preview leader fence가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    if (spaceRowVersion !== null && !positiveVersion(spaceRowVersion)) throw new AdminOrganizationError("preview space fence가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
    return {
      member_resolutions: members.map(member => {
        if (!USER_ID_RE.test(String(member.user_id || "")) || !positiveVersion(member.expected_user_row_version)) throw new AdminOrganizationError("preview member fence가 올바르지 않습니다.", { code: "INVALID_PREVIEW" });
        const target = Object.prototype.hasOwnProperty.call(destinationMap, member.user_id) ? destinationMap[member.user_id] : null;
        if (!nullableUuid(target)) throw new AdminOrganizationError("구성원 대상 파트가 올바르지 않습니다.", { code: "VALIDATION_ERROR" });
        return { user_id: member.user_id, expected_user_row_version: member.expected_user_row_version, target_part_id: target };
      }),
      expected_current_leader_assignment_id: leaderAssignmentId,
      leader_action: "close",
      expected_space_row_version: spaceRowVersion,
      space_action: "freeze_if_present",
    };
  }

  function membershipBody(snapshot, user, destinationPartId, reason, replacementLeaderUserId = null) {
    const currentPart = user.current_part_id === null ? null : snapshot.units.get(user.current_part_id)?.unit;
    return {
      expected_user_row_version: user.row_version,
      part_id: destinationPartId,
      expected_current_leader_assignment_id: currentPart?.leader?.assignment_id ?? null,
      expected_part_row_version: currentPart?.row_version ?? null,
      replacement_leader_user_id: replacementLeaderUserId,
      reason,
    };
  }

  function accountStatusBody(snapshot, user, reason, replacementLeaderUserId = null) {
    const currentPart = user.current_part_id === null ? null : snapshot.units.get(user.current_part_id)?.unit;
    return {
      expected_user_row_version: user.row_version,
      expected_current_leader_assignment_id: currentPart?.leader?.assignment_id ?? null,
      expected_part_row_version: currentPart?.row_version ?? null,
      replacement_leader_user_id: replacementLeaderUserId,
      reason,
    };
  }

  function errorFromResponse(response, payload) {
    const error = payload?.error;
    return new AdminOrganizationError(
      error?.message || payload?.message || `HTTP ${response.status}`,
      {
        status: response.status,
        code: error?.code || payload?.code || "INTERNAL_ERROR",
        requestId: payload?.request_id || response.headers?.get?.("X-Request-ID") || "",
        details: error?.details || null,
        retryable: error?.retryable === true,
      },
    );
  }

  function shouldRefreshAfterError(error) {
    return error instanceof AdminOrganizationError && error.status === 409;
  }

  function isTerminalClientError(error) {
    return error instanceof AdminOrganizationError
      && error.status >= 400
      && error.status < 500
      && !error.retryable;
  }

  async function jsonRequest(fetcher, path, { method = "GET", body, headers = {} } = {}) {
    const init = { method, headers: { Accept: "application/json", ...headers } };
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetcher(path, init);
    } catch (error) {
      throw new AdminOrganizationError(error?.message || "네트워크 응답을 받지 못했습니다.");
    }
    let payload = null;
    try { payload = await response.json(); } catch { payload = null; }
    if (!response.ok) throw errorFromResponse(response, payload);
    if (!payload || typeof payload !== "object") throw new AdminOrganizationError("서버 JSON 응답이 올바르지 않습니다.", { status: response.status, code: "INVALID_RESPONSE" });
    return payload;
  }

  function statusLabel(value) {
    return ({ active: "활성", disabled: "비활성", inactive: "비활성", frozen: "동결", not_created: "미생성", write_disabled: "쓰기 꺼짐", future_only: "향후" })[value] || value;
  }

  function reasonValue(form, maximum) {
    const reason = String(form.querySelector('[name="reason"]')?.value || "").trim();
    if (!reason || reason.length > maximum) throw new AdminOrganizationError(`사유는 1~${maximum}자여야 합니다.`, { code: "VALIDATION_ERROR" });
    return reason;
  }

  function option(value, label, selected = false) {
    return `<option value="${escapeHtml(value ?? "")}"${selected ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }

  function unitPath(snapshot, unitId) {
    return snapshot.units.get(unitId)?.path.join(" > ") || "미배정";
  }

  function activeParts(snapshot, excludedIds = new Set()) {
    return snapshot.rows.map(row => row.unit).filter(unit => unit.kind === "part" && unit.status === "active" && !excludedIds.has(unit.id));
  }

  function higherRoleDescriptor(unit) {
    if (unit?.kind === "team") {
      return Object.freeze({
        label: "팀 지식 책임자",
        role: "team_leader",
        url: `${HIGHER_ADMIN_PREFIX}/teams/${encodeURIComponent(unit.id)}/leader`,
      });
    }
    if (unit?.kind === "responsibility") {
      return Object.freeze({
        label: "담당 지식 임원",
        role: "responsibility_executive",
        url: `${HIGHER_ADMIN_PREFIX}/responsibilities/${encodeURIComponent(unit.id)}/executive`,
      });
    }
    return null;
  }

  let defaultOperationStore = null;

  async function open(deps) {
    if (!deps || typeof deps.fetcher !== "function" || !deps.modal || typeof deps.toast !== "function") throw new TypeError("AdminOrganization.open dependencies are incomplete");
    const uuidFactory = deps.uuidFactory || global.crypto?.randomUUID?.bind(global.crypto);
    const operations = deps.operationStore || (
      defaultOperationStore || (defaultOperationStore = createOperationStore(uuidFactory))
    );
    const state = {
      snapshot: null,
      legacyUsers: [],
      unavailable: "",
      loadGeneration: 0,
      higherRoles: new Map(),
      higherRoleErrors: new Map(),
    };
    const body = global.document.createElement("div");
    body.className = "admin-org-shell";

    const showError = error => {
      const request = error?.requestId ? ` · request ${error.requestId}` : "";
      deps.toast(`${error?.code || "ERROR"}: ${error?.message || error}${request}`, "error");
    };

    const load = async () => {
      const generation = ++state.loadGeneration;
      const [canonical, legacy] = await Promise.allSettled([
        jsonRequest(deps.fetcher, `${ADMIN_PREFIX}/org/tree`),
        typeof deps.legacyApi === "function" ? deps.legacyApi("api/code/auth/users") : Promise.resolve({ items: [] }),
      ]);
      if (generation !== state.loadGeneration) return;
      state.legacyUsers = legacy.status === "fulfilled" && Array.isArray(legacy.value?.items) ? legacy.value.items : [];
      if (canonical.status === "fulfilled") {
        try {
          state.snapshot = normalizeSnapshot(canonical.value);
          state.unavailable = "";
          const higherUnits = state.snapshot.rows
            .map(row => row.unit)
            .filter(unit => unit.status === "active" && higherRoleDescriptor(unit));
          const roleReads = await Promise.allSettled(higherUnits.map(async unit => {
            const descriptor = higherRoleDescriptor(unit);
            return normalizeHigherRoleStatus(
              await jsonRequest(deps.fetcher, descriptor.url),
              unit.id,
              descriptor.role,
            );
          }));
          if (generation !== state.loadGeneration) return;
          state.higherRoles = new Map();
          state.higherRoleErrors = new Map();
          higherUnits.forEach((unit, index) => {
            const outcome = roleReads[index];
            if (outcome.status === "fulfilled") state.higherRoles.set(unit.id, outcome.value);
            else state.higherRoleErrors.set(
              unit.id,
              outcome.reason?.message || "상위 지식 역할을 조회하지 못했습니다.",
            );
          });
        } catch (error) {
          state.snapshot = null;
          state.unavailable = error.message;
          state.higherRoles = new Map();
          state.higherRoleErrors = new Map();
        }
      } else {
        state.snapshot = null;
        state.unavailable = canonical.reason?.message || "canonical 관리자 API가 등록되지 않았습니다.";
        state.higherRoles = new Map();
        state.higherRoleErrors = new Map();
      }
      render();
    };

    const refreshAfterConflict = async error => {
      if (shouldRefreshAfterError(error)) await load();
    };

    const mutate = async (intentKey, path, method, semanticBody, {
      success = "변경을 적용했습니다.",
      responseKind = "canonical",
    } = {}) => {
      const prepared = prepareMutation(operations, intentKey, semanticBody);
      operations.attach(intentKey, { method, path, responseKind, success });
      try {
        const payload = await jsonRequest(deps.fetcher, path, {
          method,
          body: prepared.body,
          headers: prepared.headers,
        });
        const result = responseKind === "higher_role"
          ? normalizeHigherRoleMutationResponse(payload, prepared.operationId)
          : responseKind === "distribution"
            ? normalizeDistributionMutationResponse(payload, prepared.operationId)
            : responseKind === "semantic_correction"
              ? normalizeSemanticCorrectionResponse(payload, prepared.operationId)
              : normalizeMutationResponse(payload, prepared.operationId);
        operations.complete(intentKey);
        deps.toast(success, "ok");
        await load();
        return result;
      } catch (error) {
        // A response-less or explicitly retryable failure keeps the exact
        // operation ID. Only a known terminal client response clears it.
        if (isTerminalClientError(error)) operations.complete(intentKey);
        await refreshAfterConflict(error);
        if (!isTerminalClientError(error)) render();
        throw error;
      }
    };

    const pendingRecovery = () => {
      const entries = operations.entries().filter(value => value.transport);
      if (!entries.length) return "";
      return `<section class="admin-org-pending"><strong>응답 미확인 요청 ${entries.length}건</strong><p>${state.snapshot ? "새 operation을 만들지 않고 원래 본문과 ID를 그대로 다시 보냅니다." : "Canonical snapshot을 다시 검증할 때까지 재시도도 차단됩니다."}</p>${entries.map(value => `<button data-action="retry-pending" data-intent="${escapeHtml(value.intentKey)}"${state.snapshot ? "" : " disabled"}>재시도 · ${escapeHtml(value.intentKey)}</button>`).join("")}</section>`;
    };

    const showForm = (title, html, submit) => {
      const panel = body.querySelector("#adminOrgActionPanel");
      panel.innerHTML = `<div class="admin-org-action-head"><strong>${escapeHtml(title)}</strong><button type="button" data-action="close-panel">닫기</button></div><form class="admin-org-action-form">${html}<div class="admin-org-form-actions"><button type="submit" class="primary">확인</button></div></form>`;
      const form = panel.querySelector("form");
      form.addEventListener("submit", async event => {
        event.preventDefault();
        const button = form.querySelector('[type="submit"]');
        button.disabled = true;
        try { await submit(form); } catch (error) { showError(error); } finally { button.disabled = false; }
      });
      panel.scrollIntoView({ block: "nearest" });
    };

    const renderTree = snapshot => snapshot.rows.map(({ unit, depth, path }) => {
      const actions = [];
      if (unit.status === "active" && unit.kind === "responsibility") actions.push(`<button data-action="create-unit" data-id="${unit.id}">팀 추가</button>`);
      if (unit.status === "active" && unit.kind === "team") actions.push(`<button data-action="create-unit" data-id="${unit.id}">파트 추가</button>`);
      if (unit.kind !== "responsibility") {
        actions.push(`<button data-action="rename-unit" data-id="${unit.id}">이름</button>`);
        if (unit.status === "active") actions.push(`<button data-action="move-unit" data-id="${unit.id}">이동</button><button class="danger" data-action="inactivate-unit" data-id="${unit.id}">비활성화</button>`);
      }
      if (unit.kind === "part" && unit.status === "active") actions.push(`<button data-action="set-leader" data-id="${unit.id}">파트장</button>`);
      const higherDescriptor = higherRoleDescriptor(unit);
      const higherRole = state.higherRoles.get(unit.id);
      const higherRoleError = state.higherRoleErrors.get(unit.id);
      if (higherDescriptor && unit.status === "active") {
        actions.push(`<button data-action="set-higher-role" data-id="${unit.id}"${higherRole ? "" : " disabled"}>${escapeHtml(higherDescriptor.label)}</button>`);
        actions.push(`<button data-action="manage-distributions" data-id="${unit.id}"${higherRole ? "" : " disabled"}>배포 회수</button>`);
        actions.push(`<button class="danger" data-action="break-glass-edge" data-id="${unit.id}"${higherRole ? "" : " disabled"}>긴급 edge 억제</button>`);
      }
      const higherMeta = higherRole
        ? ` · ${escapeHtml(higherDescriptor.label)} ${escapeHtml(higherRole.assignee_display_name || "미지정")} · native 쓰기 ${higherRole.write_enabled ? "활성" : "비활성"}`
        : higherRoleError
          ? ` · 역할 조회 실패: ${escapeHtml(higherRoleError)}`
          : "";
      return `<div class="admin-org-node kind-${unit.kind} status-${unit.status}" style="--org-depth:${depth}">
        <div class="admin-org-node-main"><strong>${escapeHtml(unit.name)}</strong><span>${escapeHtml(path.join(" > "))}</span></div>
        <span class="account-status ${unit.status}">${escapeHtml(statusLabel(unit.status))}</span>
        <span class="admin-org-meta">구성원 ${Number(unit.member_count || 0)} · ${escapeHtml(statusLabel(unit.space_status))} · v${unit.row_version}${higherMeta}</span>
        <div class="admin-org-actions">${actions.join("")}</div>
      </div>`;
    }).join("");

    const renderUsers = snapshot => {
      const legacyById = new Map(state.legacyUsers.map(user => [String(user.id || ""), user]));
      return snapshot.users.map(user => {
        const legacy = legacyById.get(user.id);
        const statusAction = user.account_status === "active" ? "disable-account" : "enable-account";
        return `<div class="account-row canonical-account-row" data-user-id="${escapeHtml(user.id)}">
          <span class="account-main"><strong>${escapeHtml(user.display_name)}</strong><span>${escapeHtml(user.id)} · ${escapeHtml(unitPath(snapshot, user.home_org_unit_id))}</span></span>
          <span class="account-role ${user.system_role === "admin" ? "admin" : ""}">${escapeHtml(user.system_role)}</span>
          <span class="account-status ${user.account_status}">${escapeHtml(statusLabel(user.account_status))}</span>
          <div class="account-summary">scope epoch ${user.scope_epoch} · user v${user.row_version}${user.home_org_unit_kind ? ` · 직속 ${escapeHtml(user.home_org_unit_kind)}` : ""}${user.current_affiliation_id ? ` · affiliation ${escapeHtml(user.current_affiliation_id)}` : ""}</div>
          <div class="account-row-actions">
            <button data-action="set-membership" data-user="${escapeHtml(user.id)}"${user.account_status === "active" && !["team", "responsibility"].includes(user.home_org_unit_kind) ? "" : " disabled"}>파트</button>
            <button data-action="${statusAction}" data-user="${escapeHtml(user.id)}">${user.account_status === "active" ? "비활성화" : "활성화"}</button>
            ${legacy ? `<button data-action="legacy-profile" data-user="${escapeHtml(user.id)}">레거시 표시 이름</button><button data-action="legacy-password" data-user="${escapeHtml(user.id)}">비밀번호</button><button data-action="legacy-data" data-user="${escapeHtml(user.id)}">데이터</button><button data-action="legacy-transfer" data-user="${escapeHtml(user.id)}">이관</button>` : ""}
          </div>
        </div>`;
      }).join("");
    };

    const renderLegacyRows = users => users.map(user => `<div class="account-row"><span class="account-main"><strong>${escapeHtml(user.display_name || user.id)}</strong><span>${escapeHtml(user.id)}</span></span><span class="account-role">legacy</span><div class="account-row-actions"><button data-action="legacy-profile" data-user="${escapeHtml(user.id)}">레거시 표시 이름</button><button data-action="legacy-password" data-user="${escapeHtml(user.id)}">레거시 비밀번호</button><button data-action="legacy-data" data-user="${escapeHtml(user.id)}">레거시 데이터</button><button data-action="legacy-transfer" data-user="${escapeHtml(user.id)}">레거시 이관</button></div></div>`).join("");

    const renderLegacyOnly = snapshot => {
      const canonicalIds = new Set(snapshot.users.map(user => user.id));
      const users = state.legacyUsers.filter(user => !canonicalIds.has(String(user.id || "")));
      if (!users.length) return "";
      return `<section class="admin-org-section legacy-compat-section"><h3>Canonical 미이관 레거시 로그인 계정</h3><p class="account-help">아래 도구는 레거시 로그인·데이터 호환 전용이며 canonical 조직, 공간, 권한을 변경하지 않습니다.</p><div class="account-list">${renderLegacyRows(users)}</div></section>`;
    };

    const renderUnavailableLegacy = () => {
      const rows = renderLegacyRows(state.legacyUsers);
      return `<div class="admin-org-unavailable"><strong>Canonical 관리 기능 사용 불가</strong><p>${escapeHtml(state.unavailable)}</p><p>조직, 파트, 파트장, 계정 상태 변경은 안전하게 차단되었습니다. 레거시 status/role 변경이나 삭제로 우회하지 않습니다.</p></div><div class="account-admin-actions"><button data-action="legacy-create">레거시 로그인 계정 생성</button></div><p class="account-help">이 생성은 기존 로그인을 보존하기 위한 호환 기능이며 canonical 조직/공간을 만들거나 권한을 부여하지 않습니다.</p><div class="account-list">${rows || '<div class="account-empty">레거시 계정도 불러오지 못했습니다.</div>'}</div>`;
    };

    const render = () => {
      body.innerHTML = `<div class="account-admin-head"><div><strong>조직·계정 관리</strong><span>Canonical snapshot, preview, row-version CAS</span></div><div class="account-admin-actions"><button data-action="refresh">새로고침</button><button class="danger" data-action="break-glass-edge">조직 edge 긴급 억제</button><button data-action="legacy-audit">레거시 감사 로그</button></div></div>${pendingRecovery()}${state.snapshot ? `<section class="admin-org-section"><h3>담당 &gt; 팀 &gt; 파트</h3><div class="admin-org-tree">${renderTree(state.snapshot)}</div><p class="account-help">팀 지식 책임자와 담당 지식 임원은 각 native 지식 공간에만 게시·수정할 수 있습니다. 역할 종료는 native 쓰기를 함께 비활성화하며, 기존 문서와 하위 상속 읽기는 보존됩니다.</p></section><section class="admin-org-section"><h3>Canonical 사용자</h3><div class="account-list">${renderUsers(state.snapshot)}</div><p class="account-help">조직 지식 역할은 계정의 admin/user system role과 독립적으로 지정됩니다. ‘레거시’ 도구는 canonical 조직/공간/권한을 변경하지 않으며 영구 삭제는 제공하지 않습니다.</p></section>${renderLegacyOnly(state.snapshot)}` : renderUnavailableLegacy()}<div id="adminOrgActionPanel" class="admin-org-action-panel"></div>`;
    };

    const previewChange = async (unit, change, newParentId = null) => normalizePreview(
      await jsonRequest(deps.fetcher, `${ADMIN_PREFIX}/org/units/${encodeURIComponent(unit.id)}/change-preview`, { method: "POST", body: { expected_row_version: unit.row_version, change, new_parent_id: newParentId } }),
      unit.id,
    );

    const actionCreate = unit => {
      const kind = unit.kind === "responsibility" ? "team" : "part";
      showForm(`${unit.name} 아래 ${kind === "team" ? "팀" : "파트"} 추가`, `<label>이름<input name="name" maxlength="120" required></label>`, async form => {
        const name = String(form.elements.name.value || "").trim();
        if (!name || name.length > 120) throw new AdminOrganizationError("이름은 1~120자여야 합니다.", { code: "VALIDATION_ERROR" });
        await mutate(`create:${unit.id}:${kind}`, `${ADMIN_PREFIX}/org/units`, "POST", { parent_id: unit.id, kind, name }, { success: `${name} 생성 완료` });
      });
    };

    const showLegacyPassword = userId => showForm(
      `비밀번호 초기화 · ${userId}`,
      '<label>새 비밀번호<input name="password" type="password" minlength="8" required></label><p class="account-help">임시 비밀번호로 설정되며 다음 로그인 후 사용자가 변경해야 합니다.</p>',
      async form => {
        const password = String(form.elements.password.value || "");
        if (password.length < 8) throw new AdminOrganizationError("비밀번호는 8자 이상이어야 합니다.", { code: "VALIDATION_ERROR" });
        await deps.legacyApi(`api/code/auth/users/${encodeURIComponent(userId)}/password`, { method: "POST", body: { password } });
        deps.toast("비밀번호를 초기화했습니다.", "ok");
        await load();
      },
    );

    const showLegacyData = async userId => {
      const data = await deps.legacyApi(`api/code/auth/users/${encodeURIComponent(userId)}/data`);
      const summary = data?.summary || {};
      const fields = [
        ["세션", "sessions"], ["워크스페이스 파일", "workspace_files"],
        ["임시 인덱스 파일", "project_context_files"], ["사용자 스킬", "user_skills"],
        ["장기기억 파일", "memory_files"], ["Second Brain 노드", "thoughts"],
        ["검토함 항목", "inbox_items"], ["원문/Raw chunks", "raw_chunks"],
        ["전체 파일", "total_files"], ["전체 bytes", "total_bytes"],
      ];
      const panel = body.querySelector("#adminOrgActionPanel");
      panel.innerHTML = `<div class="admin-org-action-head"><strong>데이터 요약 · ${escapeHtml(userId)}</strong><button type="button" data-action="close-panel">닫기</button></div><div class="account-data-grid">${fields.map(([label, key]) => `<span>${escapeHtml(label)}</span><strong>${Number.isFinite(Number(summary[key])) && Number(summary[key]) >= 0 ? Math.trunc(Number(summary[key])) : 0}</strong>`).join("")}</div><p class="account-help">원문은 표시하지 않습니다.</p>`;
    };

    const showLegacyTransfer = sourceUserId => {
      const targets = state.legacyUsers.filter(user => String(user.id) !== sourceUserId && !user.disabled);
      if (!targets.length) throw new AdminOrganizationError("이관할 활성 대상 계정이 없습니다.", { code: "STATE_CONFLICT" });
      showForm(
        `사용자 데이터 이관 · ${sourceUserId}`,
        `<label>대상 계정<select name="target">${targets.map(user => option(String(user.id), `${user.display_name || user.id} (${user.id})`)).join("")}</select></label><div class="account-transfer-options">${["sessions", "workspace", "project_context", "skills", "memory", "knowledge"].map(value => `<label><input type="checkbox" name="include" value="${value}" checked>${value}</label>`).join("")}</div>`,
        async form => {
          const include = [...form.querySelectorAll('input[name="include"]:checked')].map(value => value.value);
          if (!include.length) throw new AdminOrganizationError("이관할 항목을 하나 이상 선택하세요.", { code: "VALIDATION_ERROR" });
          await deps.legacyApi(`api/code/auth/users/${encodeURIComponent(sourceUserId)}/transfer`, { method: "POST", body: { target_user_id: form.elements.target.value, include } });
          deps.toast("데이터 이관을 완료했습니다.", "ok");
          await load();
        },
      );
    };

    const showLegacyAudit = async () => {
      const data = await deps.legacyApi("api/code/auth/audit?limit=100");
      const items = Array.isArray(data?.items) ? data.items : [];
      const panel = body.querySelector("#adminOrgActionPanel");
      panel.innerHTML = `<div class="admin-org-action-head"><strong>감사 로그</strong><button type="button" data-action="close-panel">닫기</button></div><div class="audit-list">${items.length ? items.map(item => `<div class="audit-row"><strong>${escapeHtml(item.action || "-")}</strong><span>${escapeHtml(item.actor_id || "-")} → ${escapeHtml(item.target_id || "-")}</span><span>${escapeHtml(item.ts || "-")}</span></div>`).join("") : '<div class="account-empty">감사 로그가 없습니다.</div>'}</div>`;
    };

    const actionRename = unit => showForm("조직 이름 변경", `<label>이름<input name="name" maxlength="120" value="${escapeHtml(unit.name)}" required></label>`, async form => {
      const name = String(form.elements.name.value || "").trim();
      if (!name || name.length > 120) throw new AdminOrganizationError("이름은 1~120자여야 합니다.", { code: "VALIDATION_ERROR" });
      await mutate(`rename:${unit.id}`, `${ADMIN_PREFIX}/org/units/${encodeURIComponent(unit.id)}`, "PATCH", { expected_row_version: unit.row_version, name }, { success: "조직 이름을 변경했습니다." });
    });

    const actionMove = unit => {
      const parents = state.snapshot.rows.map(row => row.unit).filter(candidate => candidate.status === "active" && candidate.kind === (unit.kind === "team" ? "responsibility" : "team") && candidate.id !== unit.parent_id);
      if (!parents.length) { showError(new AdminOrganizationError("이동할 수 있는 다른 상위 조직이 없습니다.", { code: "STATE_CONFLICT" })); return; }
      showForm("조직 이동 preview", `<label>새 상위 조직<select name="parent">${parents.map(parent => option(parent.id, unitPath(state.snapshot, parent.id))).join("")}</select></label><p class="account-help">ID, 구성원, 파트장, 공간은 그대로 유지됩니다.</p>`, async form => {
        const parentId = form.elements.parent.value;
        const preview = await previewChange(unit, "move", parentId);
        if (!preview.allowed) throw new AdminOrganizationError(preview.blockers?.map(value => value.message).join(" / ") || "이동이 차단되었습니다.", { code: "STATE_CONFLICT", requestId: preview.request_id });
        showForm("조직 이동 확인", `<div class="admin-org-impact">현재 구성원 ${preview.member_impact.current_member_count}명 · 하위 구성원 ${preview.member_impact.descendant_member_count}명 · 지식 ${preview.space_impact.knowledge_item_count}건 · 만료 ${escapeHtml(preview.expires_at)}</div><label class="admin-org-check"><input type="checkbox" name="retain_memberships" required> membership를 유지합니다.</label><label class="admin-org-check"><input type="checkbox" name="retain_leader" required> 파트장 assignment를 유지합니다.</label><label class="admin-org-check"><input type="checkbox" name="retain_space" required> 동일 space ID를 유지합니다.</label>`, async confirmForm => {
          if (!["retain_memberships", "retain_leader", "retain_space"].every(name => confirmForm.elements[name].checked)) throw new AdminOrganizationError("모든 유지 resolution을 명시적으로 확인하세요.", { code: "VALIDATION_ERROR" });
          await mutate(`move:${unit.id}`, `${ADMIN_PREFIX}/org/units/${encodeURIComponent(unit.id)}/move`, "POST", { expected_row_version: unit.row_version, preview_token: preview.preview_token, new_parent_id: parentId, resolution: { retain_memberships: true, retain_leader: true, retain_space: true } }, { success: "조직을 이동했습니다." });
        });
      });
    };

    const descendantPartIds = unit => {
      const result = new Set();
      const walk = current => {
        if (current.kind === "part") result.add(current.id);
        (current.children || []).forEach(walk);
      };
      walk(unit);
      return result;
    };

    const actionHigherRole = unit => {
      const descriptor = higherRoleDescriptor(unit);
      const current = state.higherRoles.get(unit.id);
      if (!descriptor || !current) {
        showError(new AdminOrganizationError("상위 지식 역할 상태를 먼저 새로고침해야 합니다.", {
          code: "STATE_CONFLICT",
        }));
        return;
      }
      const candidates = state.snapshot.users.filter(user =>
        user.account_status === "active"
        && (user.home_org_unit_id === null || user.home_org_unit_id === unit.id)
      );
      const currentVisible = current.assignee_user_id === null
        || candidates.some(user => user.id === current.assignee_user_id);
      if (!currentVisible) {
        showError(new AdminOrganizationError(
          "현재 역할 보유자의 직속 소속이 해당 Team/담당과 일치하지 않습니다. 역할을 종료하거나 canonical 데이터를 먼저 복구해야 합니다.",
          { code: "STATE_CONFLICT" },
        ));
        return;
      }
      const candidateOptions = [
        option("", "역할 종료 + native 쓰기 비활성", current.assignee_user_id === null),
        ...candidates.map(user => option(
          user.id,
          `${user.display_name} (${user.id}) · ${unitPath(state.snapshot, user.home_org_unit_id)}`,
          user.id === current.assignee_user_id,
        )),
      ].join("");
      showForm(
        `${descriptor.label} · ${unit.name}`,
        `<p class="account-help">선택한 계정은 하위 Part 소속이 아니라 이 ${unit.kind === "team" ? "팀" : "담당"}에 직속 소속됩니다. 개인 Wiki는 유지되고, native 지식에만 직접 게시·수정하며 admin/user system role은 바뀌지 않습니다.</p><label>역할 보유자<select name="assignee">${candidateOptions}</select></label><label class="admin-org-check"><input type="checkbox" name="enable_writes"${current.write_enabled ? " checked" : ""}> 이 역할 episode의 native 쓰기를 활성화합니다.</label><label class="admin-org-check"><input type="checkbox" name="end_ack"> 역할 종료 시 직속 소속과 native 쓰기가 함께 종료되고 기존 세션 scope가 무효화됨을 확인합니다.</label><label>사유<textarea name="reason" maxlength="1000" required></textarea></label>`,
        async form => {
          const assignee = String(form.elements.assignee.value || "") || null;
          const ending = assignee === null;
          if (ending && !form.elements.end_ack.checked) {
            throw new AdminOrganizationError("역할 종료와 쓰기 비활성화 영향을 확인해 주세요.", {
              code: "VALIDATION_ERROR",
            });
          }
          const enableWrites = ending ? false : form.elements.enable_writes.checked;
          await mutate(
            `higher-role:${descriptor.role}:${unit.id}`,
            descriptor.url,
            "PUT",
            {
              assignee_user_id: assignee,
              enable_writes: enableWrites,
              expected_current_assignment_generation: current.assignment_generation,
              expected_current_assignment_id: current.assignment_id,
              expected_current_assignment_row_version: current.assignment_row_version,
              expected_space_row_version: current.space_row_version,
              reason: reasonValue(form, 1000),
            },
            {
              responseKind: "higher_role",
              success: ending
                ? `${descriptor.label} 역할을 종료하고 native 쓰기를 비활성화했습니다.`
                : `${descriptor.label} 역할과 native 쓰기 상태를 적용했습니다.`,
            },
          );
        },
      );
    };

    const actionManageDistributions = async unit => {
      const current = state.higherRoles.get(unit.id);
      if (!current) throw new AdminOrganizationError("상위 native 공간 상태를 먼저 조회해야 합니다.", { code: "STATE_CONFLICT" });
      const page = await jsonRequest(
        deps.fetcher,
        `${KNOWLEDGE_V2_SPACE_PREFIX}/${encodeURIComponent(current.space_id)}/distributions?limit=100`,
      );
      if (!Array.isArray(page.items)) throw new AdminOrganizationError("배포 목록 응답이 올바르지 않습니다.", { code: "INVALID_RESPONSE" });
      const active = page.items.filter(grant => grant && grant.revoked_at === null
        && UUID_RE.test(String(grant.grant_id || ""))
        && positiveVersion(grant.row_version)
        && grant.source_space_id === current.space_id);
      if (!active.length) {
        showForm(
          `배포 회수 · ${unit.name}`,
          '<p class="account-help">현재 회수할 활성 배포가 없습니다. 과거 회수 기록은 감사 로그에 유지됩니다.</p>',
          async () => { body.querySelector("#adminOrgActionPanel").innerHTML = ""; },
        );
        return;
      }
      showForm(
        `관리자 break-glass 배포 회수 · ${unit.name}`,
        `<p class="account-help">역할 보유자가 아닌 관리자가 정확한 한 배포를 긴급 회수합니다. 원본 문서는 삭제되지 않고 대상 overlay 읽기만 즉시 종료되며 모든 값이 감사됩니다.</p><label>활성 배포<select name="grant">${active.map(grant => option(grant.grant_id, `${grant.target_scope_kind}:${grant.target_scope_id} · item ${grant.source_item_id} · v${grant.row_version}`)).join("")}</select></label><label>확인 문구<input name="confirmation" required></label><p class="account-help">선택 후 정확히 REVOKE DISTRIBUTION &lt;grant UUID&gt; 를 입력하세요.</p><label>사유<textarea name="reason" maxlength="1000" required></textarea></label>`,
        async form => {
          const grant = active.find(value => value.grant_id === form.elements.grant.value);
          if (!grant) throw new AdminOrganizationError("활성 배포를 다시 선택해 주세요.", { code: "VALIDATION_ERROR" });
          const confirmation = `REVOKE DISTRIBUTION ${grant.grant_id}`;
          if (String(form.elements.confirmation.value || "") !== confirmation) {
            throw new AdminOrganizationError(`확인 문구는 '${confirmation}' 이어야 합니다.`, { code: "VALIDATION_ERROR" });
          }
          await mutate(
            `distribution-break-glass:${grant.grant_id}`,
            `${KNOWLEDGE_V2_SPACE_PREFIX}/${encodeURIComponent(current.space_id)}/distributions/${encodeURIComponent(grant.grant_id)}/revoke`,
            "POST",
            {
              break_glass: true,
              confirmation,
              expected_row_version: grant.row_version,
              reason: reasonValue(form, 1000),
            },
            {
              responseKind: "distribution",
              success: "선택한 읽기 배포만 관리자 break-glass로 회수했습니다.",
            },
          );
        },
      );
    };

    const actionBreakGlassEdge = unit => {
      const knownSpaceId = unit ? state.higherRoles.get(unit.id)?.space_id || null : null;
      showForm(
        `관리자 break-glass semantic edge 억제${unit ? ` · ${unit.name}` : ""}`,
        `<p class="account-help">자동 생성된 조직 semantic edge 한 개만 억제합니다. 문서 게시 성공 여부에는 영향을 주지 않으며, edge의 home 공간과 ID·row version이 정확히 일치해야 합니다. Part/Team/담당의 조직 공간에만 사용할 수 있습니다.</p>${knownSpaceId ? `<input type="hidden" name="space_id" value="${escapeHtml(knownSpaceId)}"><p class="account-help">home space: ${escapeHtml(knownSpaceId)}</p>` : '<label>edge home 공간 UUID<input name="space_id" required></label>'}<label>edge UUID<input name="edge_id" required></label><label>edge row version<input name="row_version" type="number" min="1" step="1" required></label><label>확인 문구<input name="confirmation" required></label><p class="account-help">정확히 SUPPRESS ORGANIZATIONAL SEMANTIC EDGE 를 입력하세요.</p><label>사유<textarea name="reason" maxlength="1000" required></textarea></label>`,
        async form => {
          const spaceId = String(form.elements.space_id.value || "").trim();
          const edgeId = String(form.elements.edge_id.value || "").trim();
          const rowVersion = Number(form.elements.row_version.value);
          const confirmation = "SUPPRESS ORGANIZATIONAL SEMANTIC EDGE";
          if (!UUID_RE.test(spaceId) || !UUID_RE.test(edgeId) || !positiveVersion(rowVersion)) {
            throw new AdminOrganizationError("공간·edge UUID와 현재 row version을 정확히 입력해 주세요.", { code: "VALIDATION_ERROR" });
          }
          if (String(form.elements.confirmation.value || "") !== confirmation) {
            throw new AdminOrganizationError(`확인 문구는 '${confirmation}' 이어야 합니다.`, { code: "VALIDATION_ERROR" });
          }
          await mutate(
            `semantic-edge-break-glass:${spaceId}:${edgeId}`,
            `${KNOWLEDGE_V2_SPACE_PREFIX}/${encodeURIComponent(spaceId)}/edges/${encodeURIComponent(edgeId)}/suppress`,
            "POST",
            {
              break_glass: true,
              confirmation,
              expected_row_version: rowVersion,
              reason: reasonValue(form, 1000),
            },
            {
              responseKind: "semantic_correction",
              success: "정확한 조직 semantic edge 한 개를 관리자 break-glass로 억제했습니다.",
            },
          );
        },
      );
    };

    const actionInactivate = async unit => {
      try {
        const preview = await previewChange(unit, "inactivate", null);
        if (!preview.allowed) throw new AdminOrganizationError(preview.blockers?.map(value => value.message).join(" / ") || "비활성화가 차단되었습니다.", { code: "STATE_CONFLICT", requestId: preview.request_id });
        const excluded = descendantPartIds(unit);
        const destinations = activeParts(state.snapshot, excluded);
        const members = preview.member_impact?.members || [];
        const memberRows = members.map(member => `<label>${escapeHtml(member.user_id)} 대상<select name="member:${escapeHtml(member.user_id)}" required><option value="" selected disabled>대상을 명시적으로 선택하세요</option>${option("__unassign__", "미배정 (개인만)")}${destinations.map(part => option(part.id, unitPath(state.snapshot, part.id))).join("")}</select></label>`).join("");
        const leaderAck = preview.leader_impact?.leader_assignment_id ? '<label class="admin-org-check"><input type="checkbox" name="leader_ack"> 현재 파트장 assignment를 닫습니다.</label>' : "";
        const spaceAck = preview.space_impact?.space_id ? '<label class="admin-org-check"><input type="checkbox" name="space_ack"> 보유 공간을 삭제하지 않고 동결합니다.</label>' : "";
        showForm("조직 비활성화 확인", `<div class="admin-org-impact">구성원 ${members.length}명 · 지식 ${Number(preview.space_impact?.knowledge_item_count || 0)}건 · 만료 ${escapeHtml(preview.expires_at || "-")}</div>${memberRows}${leaderAck}${spaceAck}<label>사유<textarea name="reason" maxlength="1000" required></textarea></label>`, async form => {
          if (preview.leader_impact?.leader_assignment_id && !form.elements.leader_ack.checked) throw new AdminOrganizationError("파트장 종료를 명시적으로 확인하세요.", { code: "VALIDATION_ERROR" });
          if (preview.space_impact?.space_id && !form.elements.space_ack.checked) throw new AdminOrganizationError("공간 동결을 명시적으로 확인하세요.", { code: "VALIDATION_ERROR" });
          const targets = {};
          members.forEach(member => {
            const selected = form.elements[`member:${member.user_id}`].value;
            if (!selected) throw new AdminOrganizationError(`${member.user_id}의 대상 또는 미배정을 선택하세요.`, { code: "VALIDATION_ERROR" });
            targets[member.user_id] = selected === "__unassign__" ? null : selected;
          });
          const resolution = buildInactivationResolution(preview, targets);
          await mutate(`inactivate:${unit.id}`, `${ADMIN_PREFIX}/org/units/${encodeURIComponent(unit.id)}`, "PATCH", { expected_row_version: unit.row_version, status: "inactive", reason: reasonValue(form, 1000), preview_token: preview.preview_token, resolution }, { success: "조직을 비활성화했습니다." });
        });
      } catch (error) { showError(error); }
    };

    const actionMembership = user => {
      const parts = activeParts(state.snapshot);
      const currentPart = user.current_part_id ? state.snapshot.units.get(user.current_part_id)?.unit : null;
      const isLeader = currentPart?.leader?.user_id === user.id;
      const replacements = isLeader ? state.snapshot.users.filter(candidate => candidate.id !== user.id && candidate.account_status === "active" && candidate.current_part_id === user.current_part_id) : [];
      showForm(`파트 배정 · ${user.id}`, `<label>대상 파트<select name="part">${option("", "미배정 (개인만)", user.current_part_id === null)}${parts.map(part => option(part.id, unitPath(state.snapshot, part.id), part.id === user.current_part_id)).join("")}</select></label>${isLeader ? `<label>파트장 처리<select name="replacement">${option("", "파트장 종료 + 쓰기 비활성")}${replacements.map(candidate => option(candidate.id, `${candidate.display_name} (${candidate.id})`)).join("")}</select></label>` : ""}<label>사유<textarea name="reason" maxlength="1000" required></textarea></label>`, async form => {
        const destination = form.elements.part.value || null;
        const replacement = isLeader && destination !== user.current_part_id ? (form.elements.replacement.value || null) : null;
        await mutate(`membership:${user.id}`, `${ADMIN_PREFIX}/users/${encodeURIComponent(user.id)}/part-membership`, "PUT", membershipBody(state.snapshot, user, destination, reasonValue(form, 1000), replacement), { success: "파트 배정을 변경했습니다." });
      });
    };

    const actionLeader = part => {
      const candidates = state.snapshot.users.filter(user => user.account_status === "active" && user.current_part_id === part.id);
      if (!candidates.length) { showError(new AdminOrganizationError("이 파트에 활성 구성원이 없습니다.", { code: "STATE_CONFLICT" })); return; }
      showForm(`파트장 지정 · ${part.name}`, `<label>파트장<select name="leader">${candidates.map(user => option(user.id, `${user.display_name} (${user.id})`, part.leader?.user_id === user.id)).join("")}</select></label><label>사유<textarea name="reason" maxlength="1000" required></textarea></label>`, async form => {
        await mutate(`leader:${part.id}`, `${ADMIN_PREFIX}/org/parts/${encodeURIComponent(part.id)}/leader`, "PUT", { leader_user_id: form.elements.leader.value, expected_part_row_version: part.row_version, expected_current_leader_assignment_id: part.leader?.assignment_id ?? null, reason: reasonValue(form, 1000) }, { success: "파트장을 변경했습니다." });
      });
    };

    const actionAccount = (user, target) => {
      const part = user.current_part_id ? state.snapshot.units.get(user.current_part_id)?.unit : null;
      const isLeader = target === "disabled" && part?.leader?.user_id === user.id;
      const replacements = isLeader ? state.snapshot.users.filter(candidate => candidate.id !== user.id && candidate.account_status === "active" && candidate.current_part_id === user.current_part_id) : [];
      showForm(`${user.id} 계정 ${target === "disabled" ? "비활성화" : "활성화"}`, `${isLeader ? `<label>파트장 처리<select name="replacement">${option("", "파트장 종료 + 쓰기 비활성")}${replacements.map(candidate => option(candidate.id, `${candidate.display_name} (${candidate.id})`)).join("")}</select></label>` : ""}<label>사유<textarea name="reason" maxlength="2000" required></textarea></label><p class="account-help">비활성화는 epoch를 증가시키고 기존 membership/lease/job 권한을 복구하지 않습니다.</p>`, async form => {
        const replacement = isLeader ? (form.elements.replacement.value || null) : null;
        const endpoint = target === "disabled" ? "disable" : "enable";
        await mutate(`account:${user.id}:${target}`, `${ADMIN_PREFIX}/users/${encodeURIComponent(user.id)}/${endpoint}`, "POST", accountStatusBody(state.snapshot, user, reasonValue(form, 2000), replacement), { success: `계정을 ${target === "disabled" ? "비활성화" : "활성화"}했습니다.` });
      });
    };

    body.addEventListener("click", async event => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      const action = button.dataset.action;
      if (action === "refresh") { await load(); return; }
      if (action === "close-panel") { body.querySelector("#adminOrgActionPanel").innerHTML = ""; return; }
      if (action === "legacy-audit") { try { await showLegacyAudit(); } catch (error) { showError(error); } return; }
      if (action === "break-glass-edge" && !button.dataset.id) { actionBreakGlassEdge(null); return; }
      if (action === "retry-pending") {
        if (!state.snapshot) { showError(new AdminOrganizationError("Canonical snapshot이 없어 재시도를 차단했습니다.", { code: "CANONICAL_ADMIN_REQUIRED" })); return; }
        const pending = operations.get(button.dataset.intent);
        if (!pending?.transport) { showError(new AdminOrganizationError("재시도할 요청이 없습니다.", { code: "STATE_CONFLICT" })); return; }
        if (button.disabled) return;
        button.disabled = true;
        try {
          await mutate(
            pending.intentKey,
            pending.transport.path,
            pending.transport.method,
            pending.semanticBody,
            {
              responseKind: pending.transport.responseKind || "canonical",
              success: pending.transport.success,
            },
          );
        } catch (error) { showError(error); } finally { button.disabled = false; }
        return;
      }
      if (action === "legacy-create") {
        showForm("레거시 로그인 계정 생성", `<p class="account-help">Canonical 조직/공간/권한과 분리된 임시 표준 사용자 로그인입니다. 관리자 역할은 이 호환 경로에서 부여할 수 없습니다.</p><label>계정 ID<input name="user_id" maxlength="64" required></label><label>표시 이름<input name="display_name" maxlength="120" required></label><label>초기 비밀번호<input name="password" type="password" minlength="8" required></label>`, async form => {
          const userId = String(form.elements.user_id.value || "").trim();
          const displayName = String(form.elements.display_name.value || "").trim();
          const password = String(form.elements.password.value || "");
          if (!USER_ID_RE.test(userId) || !displayName || displayName.length > 120 || password.length < 8) throw new AdminOrganizationError("레거시 계정 입력이 올바르지 않습니다.", { code: "VALIDATION_ERROR" });
          await deps.legacyApi("api/code/auth/users", { method: "POST", body: { user_id: userId, display_name: displayName, password, role: "user" } });
          deps.toast("레거시 로그인 계정을 만들었습니다. Canonical import가 별도로 필요합니다.", "ok");
          await load();
        });
        return;
      }
      const legacyUser = state.legacyUsers.find(user => String(user.id) === button.dataset.user);
      if (action === "legacy-profile" && legacyUser) {
        showForm(`레거시 표시 이름 · ${legacyUser.id}`, `<label>표시 이름<input name="display_name" maxlength="120" value="${escapeHtml(legacyUser.display_name || legacyUser.id)}" required></label><p class="account-help">Canonical 조직 사용자 이름이나 역할은 변경하지 않습니다.</p>`, async form => {
          const displayName = String(form.elements.display_name.value || "").trim();
          if (!displayName || displayName.length > 120) throw new AdminOrganizationError("표시 이름은 1~120자여야 합니다.", { code: "VALIDATION_ERROR" });
          await deps.legacyApi(`api/code/auth/users/${encodeURIComponent(legacyUser.id)}`, { method: "PATCH", body: { display_name: displayName } });
          deps.toast("레거시 표시 이름을 저장했습니다.", "ok");
          await load();
        });
        return;
      }
      if (action === "legacy-password") { showLegacyPassword(button.dataset.user); return; }
      if (action === "legacy-data") { try { await showLegacyData(button.dataset.user); } catch (error) { showError(error); } return; }
      if (action === "legacy-transfer") { try { showLegacyTransfer(button.dataset.user); } catch (error) { showError(error); } return; }
      if (!state.snapshot) { showError(new AdminOrganizationError("Canonical 관리자 API를 사용할 수 없어 변경을 차단했습니다.", { code: "CANONICAL_ADMIN_REQUIRED" })); return; }
      const unit = button.dataset.id ? state.snapshot.units.get(button.dataset.id)?.unit : null;
      const user = button.dataset.user ? state.snapshot.users.find(value => value.id === button.dataset.user) : null;
      if (action === "set-membership" && user?.account_status !== "active") { showError(new AdminOrganizationError("비활성 계정은 재활성화 전까지 파트 membership을 변경할 수 없습니다.", { code: "STATE_CONFLICT" })); return; }
      if (action === "create-unit" && unit) actionCreate(unit);
      else if (action === "rename-unit" && unit) actionRename(unit);
      else if (action === "move-unit" && unit) actionMove(unit);
      else if (action === "inactivate-unit" && unit) await actionInactivate(unit);
      else if (action === "set-leader" && unit) actionLeader(unit);
      else if (action === "set-higher-role" && unit) actionHigherRole(unit);
      else if (action === "manage-distributions" && unit) {
        try { await actionManageDistributions(unit); } catch (error) { showError(error); }
      }
      else if (action === "break-glass-edge" && unit) actionBreakGlassEdge(unit);
      else if (action === "set-membership" && user) actionMembership(user);
      else if (action === "disable-account" && user) actionAccount(user, "disabled");
      else if (action === "enable-account" && user) actionAccount(user, "active");
      void legacyUser;
    });

    deps.modal.open({ title: "조직·계정 관리", body, footButtons: [{ label: "닫기" }] });
    await load();
  }

  const api = Object.freeze({
    AdminOrganizationError,
    accountStatusBody,
    buildInactivationResolution,
    createOperationStore,
    escapeHtml,
    isTerminalClientError,
    membershipBody,
    normalizeDistributionMutationResponse,
    normalizeHigherRoleMutationResponse,
    normalizeHigherRoleStatus,
    normalizeMutationResponse,
    normalizePreview,
    normalizeSemanticCorrectionResponse,
    normalizeSnapshot,
    open,
    prepareMutation,
    shouldRefreshAfterError,
    stableStringify,
  });
  global.AdminOrganization = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
