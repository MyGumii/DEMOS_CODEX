"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const ui = require(path.join(__dirname, "..", "static", "admin_organization.js"));

const ROOT = "fb90d31e-9576-5823-84ef-241b7552f214";
const TEAM = "11111111-1111-4111-8111-111111111111";
const PART_A = "22222222-2222-4222-8222-222222222222";
const PART_B = "33333333-3333-4333-8333-333333333333";
const MEMBERSHIP = "44444444-4444-4444-8444-444444444444";
const LEADER = "55555555-5555-4555-8555-555555555555";
const SPACE = "66666666-6666-4666-8666-666666666666";
const OP_A = "77777777-7777-4777-8777-777777777777";
const OP_B = "88888888-8888-4888-8888-888888888888";

function unit({ id, parent_id, kind, name, children = [], leader = null, member_count = 0, row_version = 1, status = "active", space_status = "write_disabled" }) {
  return { id, parent_id, kind, name, status, member_count, leader, space_status, row_version, children, warnings: [] };
}

function snapshot() {
  const partA = unit({ id: PART_A, parent_id: TEAM, kind: "part", name: "파트 <A>", member_count: 2, row_version: 9, leader: { assignment_id: LEADER, user_id: "leader", display_name: "Leader", valid_from: "2026-08-13T00:00:00Z" } });
  const partB = unit({ id: PART_B, parent_id: TEAM, kind: "part", name: "파트 B", row_version: 4, space_status: "not_created" });
  const team = unit({ id: TEAM, parent_id: ROOT, kind: "team", name: "팀", children: [partA, partB], member_count: 2, space_status: "future_only" });
  const root = unit({ id: ROOT, parent_id: null, kind: "responsibility", name: "AMHS담당", children: [team], member_count: 2, space_status: "future_only" });
  return {
    request_id: "99999999-9999-4999-8999-999999999999",
    root,
    users: [
      { id: "leader", display_name: "Leader", system_role: "user", account_status: "active", current_part_id: PART_A, current_membership_id: MEMBERSHIP, current_affiliation_id: MEMBERSHIP, home_org_unit_id: PART_A, home_org_unit_kind: "part", scope_epoch: 3, row_version: 7 },
      { id: "member", display_name: "<img src=x onerror=boom>", system_role: "admin", account_status: "active", current_part_id: PART_A, current_membership_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", current_affiliation_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", home_org_unit_id: PART_A, home_org_unit_kind: "part", scope_epoch: 2, row_version: 5 },
      { id: "unassigned", display_name: "Unassigned", system_role: "user", account_status: "active", current_part_id: null, current_membership_id: null, current_affiliation_id: null, home_org_unit_id: null, home_org_unit_kind: null, scope_epoch: 1, row_version: 1 },
    ],
  };
}

function testSnapshotAndEscaping() {
  const normalized = ui.normalizeSnapshot(snapshot());
  assert.equal(normalized.rows.length, 4);
  assert.equal(normalized.units.get(PART_A).path.join(" > "), "AMHS담당 > 팀 > 파트 <A>");
  assert.equal(normalized.users[1].id, "member");
  assert.equal(ui.escapeHtml(normalized.users[1].display_name), "&lt;img src=x onerror=boom&gt;");

  const unsorted = snapshot();
  unsorted.users.reverse();
  assert.throws(() => ui.normalizeSnapshot(unsorted), /정렬\/ID/);
  const torn = snapshot();
  torn.users[2].current_membership_id = MEMBERSHIP;
  assert.throws(() => ui.normalizeSnapshot(torn), /membership mirror/);
  const injected = snapshot();
  injected.extra = "forbidden";
  assert.throws(() => ui.normalizeSnapshot(injected), /top-level/);
  const wrongCount = snapshot();
  wrongCount.root.member_count = 999;
  assert.throws(() => ui.normalizeSnapshot(wrongCount), /member_count/);
  const opaqueRequest = snapshot();
  opaqueRequest.request_id = "request-from-host-01";
  assert.equal(ui.normalizeSnapshot(opaqueRequest).request_id, "request-from-host-01");
}

function testPreviewResolutionUsesOnlyPreviewFences() {
  const preview = {
    request_id: "99999999-9999-4999-8999-999999999999",
    unit_id: PART_A,
    allowed: true,
    blockers: [],
    member_impact: { members: [
      { user_id: "leader", expected_user_row_version: 17 },
      { user_id: "member", expected_user_row_version: 18 },
    ], member_ids: ["leader", "member"], current_member_count: 2, descendant_member_count: 0 },
    leader_impact: { leader_assignment_id: LEADER, leader_user_id: "leader", will_close: true },
    space_impact: { space_id: SPACE, row_version: 21, space_status: "active", write_enabled: false, knowledge_item_count: 3 },
    preview_token: "p".repeat(64),
    expires_at: "2026-08-13T00:05:00Z",
  };
  assert.equal(ui.normalizePreview(preview, PART_A), preview);
  assert.deepEqual(ui.buildInactivationResolution(preview, { leader: PART_B, member: null }), {
    member_resolutions: [
      { user_id: "leader", expected_user_row_version: 17, target_part_id: PART_B },
      { user_id: "member", expected_user_row_version: 18, target_part_id: null },
    ],
    expected_current_leader_assignment_id: LEADER,
    leader_action: "close",
    expected_space_row_version: 21,
    space_action: "freeze_if_present",
  });
  assert.throws(() => ui.buildInactivationResolution({ member_impact: {} }, {}), /member fence/);
  assert.throws(() => ui.normalizePreview({ ...preview, unit_id: PART_B }, PART_A), /correlation/);
  assert.throws(() => ui.normalizePreview({ ...preview, member_impact: { ...preview.member_impact, member_ids: ["member", "leader"] } }, PART_A), /member 목록/);

  const limitedMembers = Array.from({ length: 200 }, (_, index) => ({
    user_id: `user${String(index).padStart(3, "0")}`,
    expected_user_row_version: index + 1,
  }));
  const limited = {
    ...preview,
    allowed: false,
    blockers: [{ code: "MEMBER_RESOLUTION_LIMIT", message: "Too many members", resource_id: PART_A }],
    member_impact: {
      current_member_count: 201,
      descendant_member_count: 0,
      member_ids: limitedMembers.map(value => value.user_id),
      members: limitedMembers,
    },
  };
  assert.equal(ui.normalizePreview(limited, PART_A), limited);
  assert.throws(() => ui.normalizePreview({ ...preview, allowed: false }, PART_A), /allowed\/blocker/);
}

function testMembershipAndAccountFences() {
  const normalized = ui.normalizeSnapshot(snapshot());
  const leader = normalized.users[0];
  const membership = ui.membershipBody(normalized, leader, PART_B, "move", "member");
  assert.deepEqual(membership, {
    expected_user_row_version: 7,
    part_id: PART_B,
    expected_current_leader_assignment_id: LEADER,
    expected_part_row_version: 9,
    replacement_leader_user_id: "member",
    reason: "move",
  });
  const status = ui.accountStatusBody(normalized, leader, "disable", null);
  assert.equal(status.expected_current_leader_assignment_id, LEADER);
  assert.equal(status.expected_part_row_version, 9);
  assert.equal(status.expected_user_row_version, 7);
}

function testOperationIdSurvivesResponseLossAndHeaderMatchesBody() {
  const ids = [OP_A, OP_B];
  const store = ui.createOperationStore(() => ids.shift());
  const semantic = { expected_row_version: 3, name: "새 이름" };
  const first = ui.prepareMutation(store, "rename:unit", semantic);
  const responseLossRetry = ui.prepareMutation(store, "rename:unit", { name: "새 이름", expected_row_version: 3 });
  assert.equal(first.operationId, OP_A);
  assert.equal(responseLossRetry.operationId, OP_A);
  assert.equal(JSON.stringify(responseLossRetry.body), JSON.stringify(first.body), "exact retry body bytes are preserved");
  assert.equal(first.body.operation_id, first.headers["Idempotency-Key"]);
  assert.equal(responseLossRetry.body.operation_id, responseLossRetry.headers["Idempotency-Key"]);

  assert.throws(
    () => ui.prepareMutation(store, "rename:unit", { expected_row_version: 3, name: "다른 이름" }),
    /같은 내용으로 먼저 재시도/,
  );
  store.complete("rename:unit");
  assert.equal(store.peek("rename:unit"), null);
  const nextIntent = ui.prepareMutation(store, "rename:unit", { expected_row_version: 3, name: "다른 이름" });
  assert.equal(nextIntent.operationId, OP_B);

  const retryableConflict = new ui.AdminOrganizationError("in progress", {
    status: 409,
    code: "STATE_CONFLICT",
    retryable: true,
  });
  assert.equal(ui.shouldRefreshAfterError(retryableConflict), true);
  assert.equal(ui.isTerminalClientError(retryableConflict), false);
  const terminalConflict = new ui.AdminOrganizationError("stale", {
    status: 409,
    code: "REVISION_CONFLICT",
  });
  assert.equal(ui.shouldRefreshAfterError(terminalConflict), true);
  assert.equal(ui.isTerminalClientError(terminalConflict), true);

  const response = ui.normalizeMutationResponse({
    request_id: "99999999-9999-4999-8999-999999999999",
    operation_id: OP_B,
    status: "renamed",
    resource_id: PART_A,
    row_version: 4,
  }, OP_B);
  assert.equal(response.operation_id, OP_B);
  assert.throws(
    () => ui.normalizeMutationResponse({ ...response, operation_id: OP_A }, OP_B),
    /correlation/,
  );
  assert.throws(
    () => ui.normalizeMutationResponse({ ...response, injected: "ignored" }, OP_B),
    /correlation/,
  );
}

function testStaticCutoverBoundary() {
  const app = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
  const moduleSource = fs.readFileSync(path.join(__dirname, "..", "static", "admin_organization.js"), "utf8");
  const html = fs.readFileSync(path.join(__dirname, "..", "static", "index.html"), "utf8");
  assert.ok(app.includes("window.AdminOrganization.open"));
  assert.ok(moduleSource.includes('"Idempotency-Key"'));
  assert.ok(moduleSource.includes("expected_user_row_version"));
  assert.ok(moduleSource.includes("leader_assignment_id"));
  assert.ok(moduleSource.includes("space_impact?.row_version"));
  assert.ok(moduleSource.includes("CANONICAL_ADMIN_REQUIRED"));
  assert.ok(moduleSource.includes("레거시 status/role 변경이나 삭제로 우회하지 않습니다"));
  assert.equal(moduleSource.includes('name="legacy_role"'), false);
  assert.ok(moduleSource.includes('role: "user"'));
  assert.ok(moduleSource.includes('value="" selected disabled'));
  assert.ok(moduleSource.includes('option("__unassign__"'));
  assert.ok(moduleSource.includes('name="retain_memberships" required'));
  assert.ok(moduleSource.includes("Canonical 미이관 레거시 로그인 계정"));
  assert.ok(moduleSource.includes('user?.account_status !== "active"'));
  assert.equal(app.includes('data-action="delete"'), false);
  assert.equal(app.includes('data-action="toggle"'), false);
  assert.equal(app.includes("account-role-select"), false);
  assert.equal(app.includes("openUserManagementModalLegacy"), false);
  assert.equal(app.includes("newAccountRole"), false);
  assert.equal(/body\s*:\s*\{\s*disabled\s*:/.test(app), false);
  assert.equal(/api\/code\/auth\/users\/\$\{[^}]+\}`,[\s\S]{0,100}method:\s*"DELETE"/.test(app), false);
  assert.equal(/publication|publish/i.test(moduleSource), false, "P1.8 must not expose higher-space publication controls");
  assert.ok(html.indexOf("static/admin_organization.js") < html.indexOf("static/app.js"));
}

function response(status, payload) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    async json() { return payload; },
  };
}

async function testOpenExecutesExactPendingRetryAndRefreshes409() {
  const originalDocument = global.document;
  try {
    const body = {
      className: "",
      innerHTML: "",
      listeners: {},
      addEventListener(name, listener) { this.listeners[name] = listener; },
      querySelector() { return { innerHTML: "" }; },
    };
    global.document = { createElement: () => body };

    const store = ui.createOperationStore(() => OP_A);
    const semantic = { expected_row_version: 9, name: "재시도 이름" };
    store.prepare("rename:unit", semantic);
    store.attach("rename:unit", { method: "PATCH", path: `/api/admin/v1/org/units/${PART_A}`, success: "renamed" });
    const calls = [];
    const fetcher = async (url, init) => {
      calls.push({ url, init });
      if (url.endsWith("/org/tree")) return response(200, snapshot());
      return response(200, {
        request_id: "opaque-mutation-request",
        operation_id: OP_A,
        status: "renamed",
        resource_id: PART_A,
        row_version: 10,
      });
    };
    await ui.open({
      fetcher,
      legacyApi: async () => ({ items: [] }),
      modal: { open: () => {} },
      toast: () => {},
      operationStore: store,
    });
    await body.listeners.click({ target: { closest: () => ({ dataset: { action: "retry-pending", intent: "rename:unit" } }) } });
    const mutation = calls.find(call => call.init?.method === "PATCH");
    assert.ok(mutation);
    assert.equal(mutation.init.headers["Idempotency-Key"], OP_A);
    assert.deepEqual(JSON.parse(mutation.init.body), { operation_id: OP_A, ...semantic });
    assert.equal(store.peek("rename:unit"), null);
    assert.equal(calls.filter(call => call.url.endsWith("/org/tree")).length, 2);

    const retryStore = ui.createOperationStore(() => OP_B);
    retryStore.prepare("rename:unit", semantic);
    retryStore.attach("rename:unit", { method: "PATCH", path: `/api/admin/v1/org/units/${PART_A}`, success: "renamed" });
    let treeReads = 0;
    const errors = [];
    await ui.open({
      fetcher: async (url) => {
        if (url.endsWith("/org/tree")) { treeReads += 1; return response(200, snapshot()); }
        return response(409, { request_id: "conflict-request", error: { code: "STATE_CONFLICT", message: "running", retryable: true, details: { state: "operation_in_progress" } } });
      },
      legacyApi: async () => ({ items: [] }),
      modal: { open: () => {} },
      toast: message => errors.push(message),
      operationStore: retryStore,
    });
    await body.listeners.click({ target: { closest: () => ({ dataset: { action: "retry-pending", intent: "rename:unit" } }) } });
    assert.equal(treeReads, 2, "every 409 refreshes the canonical snapshot");
    assert.equal(retryStore.peek("rename:unit"), OP_B, "retryable 409 keeps the exact operation identity");
    assert.ok(errors.some(value => value.includes("STATE_CONFLICT")));

    const unavailableStore = ui.createOperationStore(() => OP_A);
    unavailableStore.prepare("rename:unit", semantic);
    unavailableStore.attach("rename:unit", { method: "PATCH", path: `/api/admin/v1/org/units/${PART_A}`, success: "renamed" });
    let unsafeMutationCalls = 0;
    const unavailableErrors = [];
    await ui.open({
      fetcher: async (url) => {
        if (url.endsWith("/org/tree")) return response(404, { request_id: "missing", error: { code: "NOT_FOUND", message: "not registered", retryable: false, details: null } });
        unsafeMutationCalls += 1;
        return response(500, {});
      },
      legacyApi: async () => ({ items: [] }),
      modal: { open: () => {} },
      toast: message => unavailableErrors.push(message),
      operationStore: unavailableStore,
    });
    assert.ok(body.innerHTML.includes("disabled"));
    await body.listeners.click({ target: { closest: () => ({ disabled: false, dataset: { action: "retry-pending", intent: "rename:unit" } }) } });
    assert.equal(unsafeMutationCalls, 0, "canonical unavailable must block even a forged enabled retry button");
    assert.ok(unavailableErrors.some(value => value.includes("CANONICAL_ADMIN_REQUIRED")));

    const disabledView = snapshot();
    disabledView.users[2].account_status = "disabled";
    await ui.open({
      fetcher: async () => response(200, disabledView),
      legacyApi: async () => ({ items: [{ id: "legacy-only", display_name: "Legacy Only", disabled: false }] }),
      modal: { open: () => {} },
      toast: () => {},
      operationStore: ui.createOperationStore(() => OP_A),
    });
    assert.ok(body.innerHTML.includes('data-action="set-membership" data-user="unassigned" disabled'));
    assert.ok(body.innerHTML.includes("Canonical 미이관 레거시 로그인 계정"));

    let treeCall = 0;
    let resolveOld;
    let resolveNew;
    const oldResponse = new Promise(resolve => { resolveOld = resolve; });
    const newResponse = new Promise(resolve => { resolveNew = resolve; });
    const initial = snapshot();
    const older = snapshot();
    older.root.children[0].children[0].name = "OLDER PART";
    const newer = snapshot();
    newer.root.children[0].children[0].name = "NEWEST PART";
    await ui.open({
      fetcher: async () => {
        treeCall += 1;
        if (treeCall === 1) return response(200, initial);
        if (treeCall === 2) return oldResponse;
        return newResponse;
      },
      legacyApi: async () => ({ items: [] }),
      modal: { open: () => {} },
      toast: () => {},
      operationStore: ui.createOperationStore(() => OP_A),
    });
    const refreshButton = () => ({ disabled: false, dataset: { action: "refresh" } });
    const oldLoad = body.listeners.click({ target: { closest: refreshButton } });
    const newLoad = body.listeners.click({ target: { closest: refreshButton } });
    resolveNew(response(200, newer));
    await newLoad;
    resolveOld(response(200, older));
    await oldLoad;
    assert.ok(body.innerHTML.includes("NEWEST PART"));
    assert.equal(body.innerHTML.includes("OLDER PART"), false, "an older refresh must not overwrite the latest snapshot");
  } finally {
    global.document = originalDocument;
  }
}

(async () => {
  testSnapshotAndEscaping();
  testPreviewResolutionUsesOnlyPreviewFences();
  testMembershipAndAccountFences();
  testOperationIdSurvivesResponseLossAndHeaderMatchesBody();
  testStaticCutoverBoundary();
  await testOpenExecutesExactPendingRetryAndRefreshes409();
  console.log("admin_organization_ui: ok");
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
