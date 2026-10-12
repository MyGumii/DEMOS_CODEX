# Ordered implementation backlog

All tasks are initially incomplete. Execute the first incomplete task whose dependencies are satisfied. Do not combine phases merely to reduce the number of changes.

## Execution protocol for the implementation agent

For each task:

1. Read the active goal and the linked design sections.
2. Inspect the exact current call sites; preserve unrelated user changes.
3. State task scope and tests before editing.
4. Implement one cohesive vertical or infrastructure slice.
5. Run targeted tests plus required regression.
6. Record command/output evidence and update `STATUS.md`.
7. Mark the task complete only when its acceptance statements pass.

If a task discovers an undocumented behavior that affects scope, data ownership, permissions, or destructive migration, stop that task and add a decision issue instead of guessing.

## P0 — Baseline and reproducibility

Part writes and real migration remain disabled throughout P0.

- [x] **P0.1 — Dependency and test baseline**
  - Add pinned runtime and test dependency manifests plus `pytest.ini`/markers. Production SQLite must be exactly the `SCHEMA_V1.md` version/source ID until deliberately upgraded.
  - Make one venv able to import Flask and run pytest.
  - Keep the verified 40 Python and 2 Node tests passing.
  - Record Python/SQLite/Node/package versions.
  - Files: dependency manifests, test config, developer run instructions.

- [x] **P0.2 — Isolated configuration/test data roots**
  - Introduce a test-safe configuration factory or explicit environment overrides before store modules import path constants.
  - Add a guard that refuses to use live `.runtime`, `knowledge`, or `user_data` paths in destructive/fault tests.
  - Provide temporary DB/blob/projection/Notion fixtures.

- [x] **P0.3 — Current-state characterization tests**
  - Add direct tests for auth, sessions, legacy knowledge, Second Brain, chat retrieval, Notion bridge publication, and job restart limitations.
  - Characterization tests document behavior to preserve or intentionally replace; do not make unsafe behavior the target contract.
  - Establish `REG-001..003` baselines; later phases rerun them against compatibility/cutover modes.

- [x] **P0.4 — Runtime preflight skeleton**
  - Report SQLite version/source ID/compile options, executable FTS5 check, effective pragmas, filesystem locality including UNC/reparse/volume type, writable data roots, free disk, feature flags, and dependency versions.
  - Current 3.50.4 must produce a production-gate failure, while development remains possible with an explicit warning.

- [x] **P0.5 — Read-only inventory command skeleton**
  - Implement manifest categories and hashes from `MIGRATION_ROLLOUT.md` without applying any migration.
  - Run only against sanitized/temp copies in automated tests.

- [x] **P0.6 — Machine-verifiable contracts**
  - Add OpenAPI plus JSON Schemas for requests, stable responses/errors, cursors, publication previews/results, proposal/merge previews, job state, projection manifests, and backup manifests.
  - Add a documentation audit that expands requirement/scenario ranges and fails on missing, duplicate, or untraced IDs.
  - Validate `GOAL_CONTRACT.json` against `TRACEABILITY.md` and `TEST_OPERATIONS.md`, including the exact mandatory-scenario count.

P0 gate:

- Existing regressions pass in one reproducible venv.
- Test isolation guard is proven.
- Preflight and inventory outputs are machine-readable and deterministic.
- OpenAPI/JSON Schema and requirement/scenario audits pass with missing/duplicate IDs equal to zero.
- No production/user data was mutated.

## P1 — Core database, identity, organization, and spaces

Dependencies: P0 gate.

- [x] **P1.1 — Package/composition scaffold**
  - Add `knowledge_core` module boundaries from `ARCHITECTURE.md`.
  - One composition root serves `app_code`, `app_notion` publication integration, and optional Blueprint host adapter.
  - No canonical route writes files directly.

- [x] **P1.2 — Connection factory and single migrator**
  - Implement per-connection pragmas, explicit transactions, schema version/checksum validation, and FTS check.
  - Migrations never run on ordinary read connections.
  - Add transaction rollback/foreign-key/schema checksum tests.

- [x] **P1.3 — Canonical schema v1**
  - Implement users, organization, membership/roles, spaces, conversations/pins, artifacts/publications, items/revisions, relations/edges, leases, findings, audit, idempotency, outbox/jobs, projection and migration-map tables.
  - Add triggers/indexes for hierarchy, immutable revision/audit, same-space edge/pin, unique active membership, and legal state constraints.
  - Implement exactly the columns, FK actions, checks, triggers, partial indexes, FTS tables, and query/claim indexes in `SCHEMA_V1.md`; schema tests cover every physical constraint.
  - Pass `SCHEMA-001..008`, including curation transitions and immutable revision/audit metadata.

- [x] **P1.4 — DB-backed user/audit repository**
  - Import existing password hashes/roles/status idempotently in fixtures.
  - Preserve Flask cookie-session login behavior through repository adapters.
  - Replace destructive user deletion with disable-first/reference-aware workflow.
  - Implement idempotent disable/re-enable commands that increment scope epoch, invalidate active sessions, revoke leases/jobs, and preserve knowledge/audit/membership history.
  - Audit writes cannot be swallowed and commit with the related command.
  - Pass `AUTH-001..004`, including leader replacement/write-disable policy and cookie-session CSRF/origin controls.

- [x] **P1.5 — Organization service and admin API**
  - Seed `AMHS담당`, implement create/rename/move/inactivate with stable IDs.
  - Require a short-lived impact-bound preview token and an exact per-member,
    leader, and optional-space resolution for inactivation; do not recursively
    inactivate active children.
  - Implement assign/move/unassign one active part and replace part leader.
  - Treat the current leader-assignment ID and part row version as CAS fences;
    every leader episode change increments the part aggregate version.
  - Membership move increments scope epoch and revokes old-part leases/jobs at commit checks.
  - A leader disable/move/unassign atomically replaces the leader or closes the role and disables part writes; reject every leaderless write-enabled result.

- [x] **P1.6 — Personal/part space lifecycle**
  - Create exactly one personal space per canonical user, including disabled
    users, and one part space per active part. A missing inactive-part space is
    deliberately not backfilled; an existing inactive-part space is retained.
  - Provision spaces for existing P1.5 parts reported as `not_created`; P1.5
    organization creation never creates a knowledge space implicitly.
  - Reconcile all owners in one `BEGIN IMMEDIATE` with actor-scoped
    idempotency, immutable audit, and one outbox event per created space.
  - Initialize new spaces as `active`, `write_enabled=0`, `generation=0`, and
    `row_version=1`; never reset an existing space's ID or lifecycle state.
  - Keep team/담당 space capability behind a disabled flag.

- [x] **P1.7 — ScopeResolver and capability tests**
  - Resolve actor, concrete space, current membership, roles, capabilities, and
    scope epoch from one caller-owned canonical transaction.
  - Treat the session user ID and captured epoch as the only trusted principal
    input; ignore/reject client actor, role, membership, and capability claims.
  - Expose personal/current-part only, conceal foreign/former/higher/inactive
    scopes, separate DB entitlement from rollout-effective capability, and
    never lazy-provision from a read.
  - Execute `SPACE-001..005` and the P1.7 portions of `ORG-003..006`, including
    a real two-process membership-move/old-epoch proof.

- [x] **P1.8 — Admin organization UI**
  - Extend the existing account-admin screen with tree, derived ancestors, membership, leader, validation previews, and reasoned disable/re-enable behavior.
  - No team/담당 publication control yet.

- [x] **P1.9 — Minimal durable work kernel**
  - Implement durable enqueue/claim/renew/retry/dead/cancel/result-idempotency primitives needed by publication preview, without yet cutting over every legacy producer.
  - Persist handler version, input reference/hash, stable effect key, actor/scope epoch/capability and expected aggregate versions; revalidate authorization immediately before domain commit.
  - P7 completes producer cutover, central inference scheduling, recovery operations, and production monitoring.

P1 gate:

- `ORG-001..006`, `SPACE-001..005`, `AUTH-001..004`, and `SCHEMA-001..008` pass.
- Two processes share one canonical organization/space view.
- User/account disable and membership move cannot leave a valid old-part write capability.
- Minimal durable-kernel restart/claim/idempotency tests pass before P4 preview work begins.

## P2 — Legacy inventory, migration, and reconciliation

Dependencies: P1 gate. Real production apply still requires explicit approval.

- [x] **P2.1 — Complete inventory scanner**
  - Cover every category in `CURRENT_SYSTEM_INVENTORY.md`, including hidden pages, root legacy, graph/candidates, sessions, assets, jobs, and Notion source references.
  - Read errors and unknowns fail strict inventory.
  - Bind output to operator-supplied frozen-copy/VSS metadata, the declared
    roots, and scan-time high-water markers, and validate final resolved Windows
    paths/reparse boundaries. The scanner does not create or authenticate the
    snapshot or prove writer quiescence/application consistency.

- [x] **P2.2 — Explicit migration planner**
  - Consume organization mapping CSV and inventory hash.
  - Produce deterministic import/quarantine/rebuild actions without inferring part ownership.
  - Require confirmation for plan drift.
  - Freeze a self-contained draft whose copied source ledgers reconstruct and
    pass the exact P2.1 manifest contract before an external review can bind it.

- [x] **P2.3 — Users/personal items/assets importer**
  - Import user-owned wiki sources as personal item/revision/artifact records.
  - Content-address assets and verify hashes.
  - Store durable legacy mapping per committed batch.

- [x] **P2.4 — Graph/finding/history importer**
  - Map only resolvable valid same-space edges.
  - Convert evidence-backed pending records; quarantine ambiguous/orphan records.
  - Preserve legacy logs/audit as labelled historical data, not current authority.

- [x] **P2.5 — Conversation and operational-state importer**
  - Import old sessions as personal-space conversations and reconcile every message, metadata field, compact summary, upload, and context binding by count/hash.
  - Rebind valid Notion session attachments through an adapter.
  - Classify unrecoverable queued/running legacy jobs as abandoned; do not execute stale closures.

- [x] **P2.6 — Strict verifier and full rebuild**
  - Implement count/hash/FK/space/edge/session/asset reconciliation and idempotent rerun checksum; every manifest record resolves to exactly one terminal classification.
  - Rebuild FTS/projections; never import old index/backlinks as truth.

- [x] **P2.7 — Dirty/large migration fault tests**
  - Pass `MIG-001..008`, crash/resume, Unicode/long path, missing asset, duplicate and quarantine scenarios.

- [x] **P2.8 — Rehearsal on a frozen sanitized/current copy**
  - Produce inventory, plan, verification, rerun, and rebuild evidence.
  - This phase runs migration/`MIG-*` gates only; the complete concurrency/load/backup/restore rehearsal is P8.1 after P1–P7.
  - Do not cut production over in this task.

P2 gate:

- Silent loss 0; all source records are imported or explicitly quarantined.
- Second identical run creates 0 duplicate logical rows/revisions.
- Existing data appears only in personal spaces.

## P3 — Scope-aware conversations, reads, pins, and compatibility

Dependencies: P2 core import/fixture gate. Part writes remain off.

- [x] **P3.1 — Conversation repository and immutable scope**
  - Replace JSON session ownership for new conversations with canonical conversation/message rows.
  - Enforce immutable space and scope epoch; implement list/load/rename/archive, idempotent turns/reconnect, and same-space compact-summary state.
  - Provide JSON-session compatibility/migration adapter.
  - Completion evidence (2026-08-14): the unregistered canonical
    repository/service and read-only canonical JSON-session projection now
    cover create/load/list/rename/archive, turn accept/completion/failure/
    cancellation/reconnect, sanitized terminal errors, compact-summary history,
    rollback, exact replay, and current-scope replay revalidation. Legacy Flask
    routes remain authoritative until the later P3.4/P3.6 cutovers. Evidence is
    frozen in `evidence/P3.1.md`; all broader P3/G-07 gate claims remain open.

- [x] **P3.2 — Server-persisted same-space pins**
  - Persist/load pins with conversation and item space constraints.
  - Persist explicit project/workspace/Notion context bindings and add/remove APIs; no implicit private default in part scope.
  - Remove browser state as the authority.
  - Completed 2026-08-14: canonical service APIs now persist fixed-current-
    revision pins and explicit workspace/project/Notion/source bindings with
    actor/current-scope replay validation, operation deduplication, atomic
    audit/outbox, and same-space enforcement. External context is authorized by
    an injected server resolver outside the DB transaction; browser-submitted
    artifact/hash evidence is not an authority input. Personal and part tests
    cover no implicit private default, exact replay, remove/re-attach, hash and
    FK enforcement, and full rollback. No HTTP route or traffic flag changed;
    evidence is frozen in `evidence/P3.2.md` and the broader P3/G-07 gate stays
    open.

- [x] **P3.3 — Canonical FTS retrieval**
  - Search current active/published revisions by one space ID.
  - Return relevant semantic neighborhood and selected recent item events only.
  - Add canonical fallback when projections lag.
  - Completed 2026-08-14: an unregistered canonical repository/service now
    resolves exactly one current authorized conversation or management space
    inside one read snapshot, executes literalized bounded FTS against only
    current active/published revisions, adds only same-space effective graph
    neighbors and audit events for returned items, and labels all Markdown as
    untrusted reference data. Missing/lagging file projections remain advisory;
    transactional FTS is the fail-closed source. Stale FTS bodies, old part
    scope, archived conversations, caller FTS operators, and personal/part
    canaries fail closed. No route, prompt assembly, traffic flag, or worker
    changed; evidence is frozen in `evidence/P3.3.md` and the broader P3/G-07
    gate remains open.

- [x] **P3.4 — Chat retrieval cutover**
  - Replace parallel legacy KB+SB calls with one conversation-derived retrieval call.
  - Validate pins and scope on every turn.
  - Do not inject private memory or a default private project context into part scope; keep selected project/workspace/Notion attachments explicit, conversation-bound, and labelled.
  - Completed 2026-08-14: the disabled Knowledge Core path now accepts only an
    already accepted canonical turn, derives both its search text and immutable
    space from canonical conversation rows, performs exactly one bounded FTS
    retrieval, and revalidates pins plus the complete explicit binding set on
    every turn. Live bindings are reauthorized outside SQLite and their
    available/unavailable transition commits with audit/outbox after a second
    scope/turn/binding check; immutable snapshot evidence cannot silently
    degrade to a live fallback. Returned Markdown is labelled untrusted and the
    service has no legacy KB, Second Brain, private-memory, default-project, or
    browser-state dependency. No HTTP/SSE route or feature flag changed. The
    old route remains authoritative until P3.7 traffic/canary cutover, so this
    task evidence is not the full P3/G-07 gate. See `evidence/P3.4.md`.

- [x] **P3.5 — Read-only knowledge/graph compatibility adapters**
  - Serve existing UI response shapes from the new repository.
  - Remove read-time refresh writes.
  - Legacy `uid` fields cannot select a different store.
  - Candidate/inbox reads use the canonical finding table imported/scaffolded in P1/P2; P6 adds semantic generation/review behavior rather than introducing storage late.

  Completed 2026-08-14 behind the disabled canonical traffic boundary. The
  adapter resolves only the authenticated actor's current personal space in a
  read-only `DEFERRED` snapshot and projects canonical items, FTS, effective
  edges, findings, and audit rows into the legacy list/view/search/thought/
  graph/inbox/system-document response keys. A legacy `uid` is accepted only
  as bounded compatibility text and is never compared, normalized, or used in
  scope SQL. There is no legacy-store, projection-file, refresh, or write-SQL
  dependency; requested limits truncate within independent hard safety bounds.
  Foreign personal/part canaries, stale scope, disabled reads, stale search
  evidence, malformed metadata, and invalid filters fail closed. Focused proof
  passes 7/7 and the related P3.1-P3.5/composition/schema regression passes
  148/148. No route, feature flag, worker, or live traffic changed. See
  `evidence/P3.5.md`; P3.6-P3.7 still own visible UI selection, route wiring,
  end-to-end canaries, and legacy traffic disablement.

- [x] **P3.6 — Scope selector and new-conversation UX**
  - Knowledge management may switch space immediately.
  - Chat switch always creates a new conversation and shows immutable badge.
  - Lists, search, graph, activity, candidate counts, and pins reload from the selected space.

  Completed 2026-08-14 behind the disabled canonical-traffic boundary. A
  read-only `ScopeSelectionService` returns exactly the authenticated actor's
  personal and current-part options and separately returns the immutable badge
  of an owner-bound conversation, including a fail-closed accessibility state
  after a part move. The inert browser controller validates that snapshot,
  reloads all seven management surfaces for an authorized selection, and, in
  chat, leaves the old badge/history untouched until the explicit
  `새 지식공간으로 새 대화 시작` action creates a different conversation. The
  create adapter receives one stable operation ID and concurrent clicks share
  one promise. The static component stays hidden until P3.7 binds canonical
  routes; no legacy session endpoint, live route, feature flag, worker, or
  traffic behavior changed. Focused proof passes 7/7 and the related
  conversation/pin/retrieval/scope/composition regression passes 136/136. See
  `evidence/P3.6.md`; P3.7 still owns route activation, API/Agent canaries,
  reconnect/session regression, and legacy parallel-read disablement.

- [x] **P3.7 — Leak and session regression gate**
  - Pass `SPACE-006` and `CONV-001..013` with canary fixtures through API and Agent context.

  Completed 2026-08-14 behind the disabled canonical-traffic boundary. The
  composition root requires the complete canonical service registry,
  server-principal resolver, turn executor, and summarizer before registering
  its initial strict 17-route group (extended to 21 by P5.4). Browser chat, session, SSE, scope, and management
  adapters now use only `/api/knowledge/v1/...`; actor/scope authority is never
  browser-supplied, retries keep one operation ID, malformed/reordered SSE
  fails closed, and default flag-off behavior is unchanged. The real localhost
  browser-to-Flask-to-Agent canary proves selected-part retrieval and foreign-
  part denial. Core mode imports/registers no legacy knowledge, Second Brain,
  memory, project-context, chat-stream, session, or agent-harness routes. The
  expanded regression passes 166/166 and covers `SPACE-006` plus
  `CONV-001..013`; see `evidence/P3.7.md`. This completes the tested P3/G-07
  implementation gate, not an operational traffic cutover.

P3 gate:

- Cross-space leak 0 across list/search/graph/pin/projection/candidate/chat.
- Conversation space changes 0.
- Legacy parallel retrieval is disabled in core mode.

## P4 — Source artifacts and personal/part publication

Dependencies: P3 gate. Part publication executes in tests; production flag remains off.

- [x] **P4.1 — Artifact/blob/extraction boundary**
  - Preserve raw artifact, extraction snapshot/version/warnings, and editable knowledge revision separately.
  - Run extractor/LLM outside write transactions.
  - Implement the minimal deterministic structural/blocker lint and source/exact-hash checks required before publication.

  Completed 2026-08-14 behind disabled traffic. The general artifact repository
  persists upload/Notion/chat/manual raw CAS evidence and exactly one immutable
  ready or failed extraction snapshot without creating an item/revision. A pure
  pre-transaction preparation boundary binds raw, extracted, LF-normalized,
  lint, and preparation hashes; the repository independently recomputes the
  sanitized evidence and rejects arbitrary nested metadata, unsafe warning
  payloads, hash forgery, partial rows, or replay drift. The closed T0 lint is
  limited to empty Markdown, unsafe controls, unterminated leading front matter,
  and unterminated code fences; it makes no semantic claim. Focused proof passes
  27/27 and related artifact/item/importer/schema regression passes 245/245.
  See `evidence/P4.1.md`. P4.2 owns durable preview/job state, authorization,
  operation idempotency, and target-space exact resolution; no `PUB-*` scenario
  pass or live publication is claimed here.

- [x] **P4.2 — Durable publication preview**
  - Support upload, manual Markdown, chat promotion, and Notion source snapshot.
  - Use the P1.9 durable kernel for asynchronous extraction and status/cancellation; bind preview to source hash, actor, membership epoch, expiry, and available target modes.
  - Implement the metadata-preserving 30-day orphan-body cleanup eligibility/effect for terminal never-published previews; it never applies to a knowledge item.
  - Persist immutable ready-result JSON/schema version, deterministic finding data/rule version, completion time, and exact state-shape checks for synchronous and asynchronous previews.

  Completed 2026-08-14 behind disabled traffic. Manual Markdown, canonical
  conversation ranges, and authorized Notion snapshots create synchronous
  ready previews; uploads stage an immutable raw artifact and use the exact
  P1.9 handler version for durable extraction. Actor/scope and available
  personal/current-part/both modes are resolved from canonical state, chat is
  revalidated at commit, and source/Notion identities are hash-bound. Durable
  polling reconstructs exact queued/failed/cancelled shapes and returns the
  immutable ready result after service/worker restart; cancellation, retry
  exhaustion, permanent failure, authorization drift, and expired-claim
  recovery converge the preview in the same transaction as the terminal job.
  SQLite-time expiry and the 30-day zero-reference cleanup preserve metadata
  and hashes while removing only eligible body links. Focused proof passes
  25/25, the repository/kernel/schema selection passes 125/125, compatibility
  regression passes 212/212, and process durability regression passes 9/9.
  See `evidence/P4.2.md`. P4.3-P4.6 still own item/revision outcomes,
  dual-target atomicity, stable Notion update proposals, routes/UI, exhaustive
  failpoints, 1,000 retries, and the complete `PUB-001..013` gate.

- [x] **P4.3 — Idempotent single/dual publication service**
  - Implement per-target exact/source identity outcomes and all-target atomic commit.
  - Create separate item/revision IDs for personal+part and permanent sync-none lineage.
  - Audit/outbox/operation result commit together.
  - Do not call the legacy LLM auto-edge path; publication commits only deterministic relations until P6 review flow exists.
  - New items remain provisional while the incremental risky-analysis gate is pending, so pre-review Agent exposure is zero.

  Completed 2026-08-14 behind disabled traffic. One service-owned immediate
  transaction binds the caller's expected preview snapshot, canonical actor
  and scope epoch, current personal/part targets, actor-scoped operation
  dedup, immutable publication operation/target rows, item/revision/source
  effects, preview consumption, target audit, operation/visibility outbox, and
  target generation. Personal, part, and both are exact; both resolves
  independent item/revision IDs under a permanent `sync_policy='none'`
  lineage, including the mixed provenance-plus-create case. Same stable-origin
  revision is `no_op`, different-origin exact content adds evidence
  provenance, and a changed stable origin fails closed for P4.4 instead of
  creating a numbered duplicate. New items are active/provisional and no
  relation/LLM path is called. Focused proof passes 10/10 and the combined
  P4.1-P4.3/schema regression passes 116/116. See `evidence/P4.3.md`.
  P4.4 still owns the actual changed-source finding/proposal and existing-route
  consolidation; P4.5 owns API/UI activation; P4.6 owns exhaustive SQL
  failpoints, process/restart races, 1,000 retries, and the complete
  `PUB-001..013` claim.

- [x] **P4.4 — Stable Notion provenance**
  - Consolidate both existing publish routes onto the same service.
  - Record owner/page/revision/publisher/part-at-publish.
  - Unchanged repeat is no-op; changed repeat creates a fixed source/base/target update proposal.
  - Create changed-source/update findings through the canonical finding repository already present from P1; P6 later enriches them semantically.

  Completed 2026-08-14 behind disabled canonical traffic. Authorized Notion
  previews now persist an exact body-free owner/page/revision/source-snapshot/
  publisher/captured-part provenance envelope. An unchanged stable source is
  `no_op`; a changed source preserves the current item/revision and atomically
  creates one deterministic T2 human-reviewed finding, one pending immutable
  `stable_source_update` proposal, one `update_proposed` publication target,
  audit, finding-queue outbox, preview consumption, and the stored replay
  result. Personal and part proposals stay independent and `both` never
  auto-synchronizes them. Late failure rolls the entire proposal path back.
  The two existing legacy Notion route wrappers are permanently proven to
  delegate to the same immutable snapshot/write boundary; P4.5 still owns the
  canonical publication endpoint and UI cutover. Focused P4.4/adapter proof
  passes 52/52, the P4.1-P4.4 core selection passes 82/82, and schema proof
  passes 38/38. See `evidence/P4.4.md`. P4.6 still owns exhaustive SQL
  failpoints, process/restart races, 1,000 retries, and the complete
  `PUB-001..013` acceptance claim.

- [x] **P4.5 — Publication UI**
  - Add personal/current-part/both modal to knowledge upload, chat promotion, and Notion.
  - Display independence warning and target-specific outcomes.

  Completed 2026-08-14 behind the still-disabled canonical write flag. One
  strict canonical route adapter now exposes manual/upload/chat/Notion preview,
  status, durable cancellation, commit, and concrete target discovery; the
  Notion-only host can bind that surface without conversation/retrieval
  dependencies. Browser capability is established by a non-mutating `OPTIONS`
  probe. Only an authoritative absent/404 result may use the legacy UI; probe
  errors or unknown canonical outcomes never trigger a second mutation.
  Knowledge upload, canonical chat promotion, and both Notion publish entry
  points use one body-safe shared modal with personal/current-part/both choices,
  the permanent no-sync warning, queued/ready cancellation, target-specific
  outcome labels, and exact-space/item navigation. Existing legacy behavior is
  retained only while the canonical surface is absent. Focused P4.5 proof
  passes 24/24, the combined P4.1-P4.5 core/adapter selection passes 158/158,
  the canonical route/conversation regression passes 57/57, and five existing
  Notion JavaScript runtime suites pass. See `evidence/P4.5.md`. No write flag,
  part traffic, live service, or `PUB-*` acceptance claim was enabled; P4.6
  still owns exhaustive SQL failpoints, real process/restart races, 1,000
  retries, and the complete `PUB-001..013` gate.

- [x] **P4.6 — Publication failure/idempotency tests**
  - Pass `PUB-001..013` including preview restart/cleanup, every SQL failpoint, and 1,000 retries.
  - Completed behind disabled canonical-write traffic on 2026-08-14. The
    acceptance suite binds every `PUB-001..013` ID to executable evidence,
    injects a rollback after each of 24 dual-create and 16 dual-Notion-update
    application DML boundaries, and observes zero partial target, preview,
    item/revision, proposal, audit, outbox, generation, or dedup state. One
    initial dual commit plus 1,000 exact service retries retains one operation,
    two items, and two revisions. Four-process same-operation contention,
    post-commit response loss followed by a fresh-process retry, and two-process
    distinct-source/equal-content contention converge without busy errors or
    duplicate revisions. Ready preview polling/cancellation survives real
    process reconstruction; synchronous/asynchronous worker restart, terminal
    cleanup, expired/consumed/actor/scope/snapshot rejection, stable Notion
    proposals, and exact source/artifact/extraction/revision/audit/outbox
    provenance all pass. Focused proof passes 71/71 and the combined P4.1-P4.6
    artifact/preview/kernel/publication/route/browser/composition/Notion
    selection passes 174/174. See `evidence/P4.6.md`. This verification changes
    no runtime code or feature flag and authorizes no live traffic by itself.

P4 gate:

- Partial dual publication 0.
- Duplicate revision from retry 0.
- Every successful target result has source/artifact/audit/outbox provenance.

## P5 — Collaboration, revisions, deactivation, and purge

Dependencies: P4 gate.

- [x] **P5.1 — Persistent fenced lease service**
  - Acquire/heartbeat/release/force-release with token hashing, monotonic generation, expiry, expected base, and membership recheck.
  - Draft recovery behavior is explicit; raw tokens never persist in logs.
  - Completed behind disabled traffic with a transaction-owned repository/service,
    exact HMAC key rotation, monotonic fencing, SQLite-time expiry, commit-time
    scope revalidation, durable audit/outbox/idempotency, rollback failpoints,
    and a real 40-process one-holder proof. Evidence: `evidence/P5.1.md`.
    This closes the lease-only scenarios `LEASE-002`, `LEASE-004`,
    `LEASE-007..009`, and `LEASE-011`; the save-coupled lease scenarios are
    owned and now closed at the P5.2 task boundary below.

- [x] **P5.2 — Immutable item edit service**
  - Revision insert, item compare-and-swap, audit, outbox, semantic-edge revalidation, and lease release commit together.
  - Completed behind disabled traffic with the P5.1 fence revalidated inside
    the save transaction; one immutable revision/body, queued versioned rescan,
    revision-rescan transition, item CAS to provisional, stale finding/proposal
    obsolescence, semantic-edge revalidation, FTS removal, space generation,
    lease release, audit, outbox, and both operation results commit together.
    The required named edit failpoints plus all 16 observed SQL write boundaries
    roll back to the exact held-lease baseline. Evidence: `evidence/P5.2.md`.
    P5.7 still owns real web-process save races, 40-client lifecycle load, and
    the aggregate P5 acceptance gate.

- [x] **P5.3 — Deactivation/restore episodes**
  - Separate commands, reason/date/actor/eligibility, live-lease conflict, edge-row invariance, and same-ID restore.
  - Completed behind disabled canonical traffic with service-owned transactions,
    current-scope and expected-version revalidation, exact 90-day SQLite-time
    eligibility, actor-owned/expired lease fencing, foreign-live-lease conflict,
    immutable episode history, transactional search visibility, audit/outbox,
    exact replay, and unchanged revision/edge/reference identity. Named and
    all observed SQL-write failpoints roll back to the exact baseline. Evidence:
    `evidence/P5.3.md`. P5.7 still owns real web-process lifecycle contention,
    40-client load, and the aggregate P5 acceptance gate.

- [x] **P5.4 — Activity/version UI**
  - Show revision history, diff, editor, reason, content age, review age, inactive duration, and audit projection.
  - Completed behind disabled canonical traffic with a space-bound item/history
    repository and read service, four exact GET routes, closed response schemas,
    deterministic bounded diff, externally loaded and revalidated blob bodies,
    token-free lease/impact/deactivation projections, and text-only browser
    rendering. Permanent tests cover active/live-lease detail, immutable
    two-revision history, body reads, diff/hash stability, inactive actor/time/
    reason/eligibility, sanitized activity, cross-space denial, query/cursor
    fail-closed behavior, and browser transport/rendering. Evidence:
    `evidence/P5.4.md`. P5.5 still owns lease editor controls; P5.7 owns real
    web-process contention, 40-client load, and the aggregate P5 gate.

- [x] **P5.5 — Lease editor UI**
  - Read-only foreign lock, heartbeat/expiry, completion/cancel, conflict draft preservation, leader/admin force-release confirmation.
  - Verified behind disabled canonical-write traffic: the optional five-route
    mutation surface binds header-only token/fence evidence to explicit editor
    session/lease IDs; the browser owns one in-memory lease, heartbeats,
    releases on completion/cancel, preserves conflict drafts with the three
    required recovery actions, and requires reason plus exact force-release
    confirmation. Evidence: `evidence/P5.5.md`. P5.7 still owns the real
    two-web-process/40-client aggregate lease gate.

- [x] **P5.6 — Administrator purge preview/command**
  - Enforce latest 90-day episode, active-reference blockers, explicit confirmation, tombstone/hash/provenance retention, reference-free revision/artifact/extraction body-link removal, and asynchronous unreferenced blob GC.
  - Never schedule automatic purge.
  - Verified behind disabled canonical traffic: optional item preview/command and
    administrator queue routes bind the authenticated admin, current scope
    epoch, signed short-lived preview/cursor evidence, exact row version,
    operation ID, and literal item confirmation. One owned transaction retains
    item/revision/source/audit provenance, removes eligible body links and FTS,
    writes the tombstone/audit/outbox, and quarantines only fully unreferenced
    blobs. Forward schema v7 fences new references and active backups around a
    short claim/finalize state machine; the root-bound Windows provider verifies
    the exact single-link CAS object before handle-based deletion outside the DB
    transaction. No timer, scheduler, worker, flag, live database, or traffic was
    enabled. Evidence: `evidence/P5.6.md`. P5.7 below now supplies the actual
    two-web-process/40-client lifecycle load and aggregate P5 gate.

- [x] **P5.7 — Cross-process lifecycle/concurrency suite**
  - Pass `REV-001..002`, `LEASE-001..012`, `LIFE-001..010`, and save/deactivate/restore races with two actual web processes and 40 clients.
  - Verified behind disabled canonical traffic. Two independent OS processes
    host the real Flask WSGI route surface against one isolated SQLite database;
    40 simultaneous HTTP clients produce one lease holder and 39 `LEASE_HELD`
    results. Forced, moved-scope, and stale-revision saves commit zero effects;
    one of 40 valid-holder saves commits exactly one revision/audit/outbox;
    deactivate and restore races each commit exactly one state/audit/outbox
    effect; the edge/relation checksum is unchanged. The complete P5 selection
    passes 125/125 and adjacent regression passes 86/86. Evidence:
    `evidence/P5.7.md`. This is correctness evidence, not P7.5 production WSGI
    packaging or P7.8 target-server `PERF-001` load evidence.

P5 gate:

- Same-item simultaneous lease success count is exactly 1.
- Stale/moved/force-released save success count is 0.
- Successful mutations have 1:1 revision/state event, audit, and outbox evidence.
- Edge state checksum survives deactivation/restore.

Gate status: pass in `evidence/P5.7.md` behind disabled canonical traffic.

P1–P5 are the minimum data-safety prerequisites for any shared write. This goal's production pilot still waits for the P6 curation gate and P7 operations/recovery gate before `KNOWLEDGE_CORE_PART_WRITES=1`.

## P6 — Deduplication, lint, graph governance, and merge

Dependencies: P5 gate.

- [x] **P6.1 — Deterministic ingest lint/dedup**
  - Harden and version the minimal P4 source identity/raw hash/exact/structural lint rules; add corpus metrics and broader deterministic rules.
  - Exact provenance handling must not collapse items across spaces.
  - Verified as pure `p6.1-deterministic-ingest-v1` policy and current
    publication-preview rule behind disabled traffic. Same-source exact is
    no-op, same-space exact different-source adds provenance only,
    normalized-only/source-change/cross-space matches are no-effect candidates,
    and duplicate canonical matches fail closed. Bounded T0 lint warnings and
    body-free order-independent corpus metrics are hash-sealed; publication
    audit/outbox binds the rule version. Focused P6.1/P4 regression passes
    101/101 and P5.7/lifecycle/composition regression passes 102/102. Evidence:
    `evidence/P6.1.md`. Candidate retrieval/finding persistence remains P6.2/
    P6.3 and no semantic automation is claimed.

- [x] **P6.2 — Incremental candidate retrieval**
  - Changed-item-only top-K using FTS/BM25, MinHash and/or embedding.
  - No all-pairs nightly LLM scan.
  - Verified as one service-owned read snapshot over one exact current source
    revision and at most one same-space FTS5/BM25 query. Query terms are
    deterministic/NFKC-casefolded, literal, bounded to 32; SQL enforces current
    active/published targets, source exclusion, stable score/item ordering, and
    top-K<=20. Results are body-free/hash-sealed and independently validated;
    stale source/search evidence fails closed. Model calls, all-pairs scans, and
    SQL writes are structurally zero. Focused P6.2 passes 10/10 and P6.2 plus
    canonical retrieval/P6.1/P4 regression passes 137/137. Evidence:
    `evidence/P6.2.md`. Finding persistence/visibility/classification remains
    P6.3; no semantic effect is claimed.

- [x] **P6.3 — Versioned finding service**
  - Persist severity separately from T0–T3 risk tier, required reviewer, evidence spans, compared revisions, detector/rule/model/prompt versions, review/apply/obsolete state, and visibility effect.
  - Enforce that T2 requires a human reviewer and T3 cannot execute a domain action through the finding/model path.
  - Distinguish `weak_complementary` (published+badge+no action) from strong `near_duplicate` (Agent-hidden+review), and route every curation-status transition through a matched finding/rescan transaction.

  Completed behind disabled canonical traffic. The repository derives all 18
  frozen v1 kind/severity/risk/mode/reviewer/visibility/action tuples rather
  than accepting policy fields from a detector. Persisted evidence is bounded,
  canonical, body-free, and bound to current same-space revisions, transient
  raw/LF-normalized Markdown, verified excerpt spans, and—where required—the
  complete independently validated P6.2 candidate snapshot and selected target.
  T2/T3 review re-resolves canonical `CURATE` authority and reviewer class in
  the write transaction; one operation result, finding transition, audit, and
  outbox commit together. Revision/lifecycle/curation drift becomes `obsolete`.
  Exact ignored input alone is suppressed; rule/model/revision input changes
  produce a distinct identity. Weak complementary remains published+badge with
  no action, strong near-duplicate/conflict remains pending human review, and
  T3 approval records no lifecycle/purge effect. Focused proof passes 38/38;
  P6.1-P6.3 plus immutable-edit obsoletion regression passes 77/77; schema
  finding-transition regression passes 76/76. Evidence: `evidence/P6.3.md`.
  P6.4-P6.8 still own semantic-edge application, merge/proposals, UI/shadow
  metrics, aggregate scenario closure, and the P6 gate.

- [x] **P6.4 — Reviewed semantic edge flow**
  - Deterministic provenance relations remain separate.
  - Semantic proposal requires current evidence and explicit approval; auto-commit count is zero.

  Completed behind disabled canonical traffic. The dedicated repository and
  transaction-owning service accept only a P6.3 `semantic_edge/T2/human/
  space_editor/relation` finding that is already explicitly approved by the
  applying editor. They bind the finding version, canonical evidence SHA,
  source/target current revisions, same writable space, and server-derived
  relation type before atomically recording `applied`, one
  `semantic_approved` edge, the space generation increment, audit, outbox, and
  operation result. Endpoint drift records `obsolete` and creates no edge;
  identity collision and changed request evidence fail without mutation.
  Focused proof passes 18/18; P6.1-P6.4 plus edit/revalidation regression
  passes 95/95; schema/trigger regression passes 56/56. Evidence:
  `evidence/P6.4.md`. No model, automatic semantic application, route, flag,
  live state, worker, or traffic was introduced. P6.5-P6.8 still own merge,
  proposal/UI/shadow metrics, aggregate scenario closure, and the P6 gate.

- [x] **P6.5 — Transactional merge**
  - Three-pane preview, two sorted locks/fences, two revision checks, one result revision, merged source/redirect, no blind edge rewiring.
  - A read-only preview binds the exact current source/target revisions, item
    row versions, materialized body hashes, result Markdown, and complete
    affected edge/reference ledgers. Apply repeats the exact confirmed Markdown
    and recomputes the preview before opening its write transaction; no
    process-memory preview cache is authoritative.
  - The transaction re-resolves current `CURATE` plus both item-specific edit
    fences in sorted item-ID order. It creates one target merge revision and
    rescan transition/job, makes the source `merged` with a canonical redirect,
    creates one immutable `merged_into` relation and `merge_event`, obsoletes
    fixed-revision findings/proposals, removes both FTS rows, increments one
    space generation, releases both leases, and commits audit/outbox/dedup.
    Existing semantic-edge identities/endpoints are never rewritten: affected
    rows become inactive and require revalidation.
  - Focused merge proof passes 16/16; P6.1-P6.5 plus immutable-edit regression
    passes 111/111; schema/table/trigger/catalog plus merge passes 60/60.
    Evidence: `evidence/P6.5.md`. No route, feature flag, worker, model, live
    state, or traffic was introduced. P6.6-P6.8 retain proposal/rebase/apply,
    review UI/shadow metrics, and the aggregate P6 gate.

- [x] **P6.6 — Three-way change proposal service**
  - Stable-source updates compare fixed source/base/target revisions; personal-to-part compares fork base, personal current, and part current.
  - Implement preview/rebase/apply API with finding version, target lease/fence, expected target revision and preview hash; stale/rebase behavior never overwrites target changes.
  - Completed behind disabled canonical traffic: the repository/service fixes the
    stable source snapshot or personal fork/source/target revisions, materializes
    blob bodies outside write transactions, rereads the complete snapshot, and
    exposes three-pane preview plus explicit versioned rebase/apply commands.
    Rebase creates one new pending pair and obsoletes the former pair atomically;
    apply requires the approving reviewer and exact target fence/CAS, creates one
    approved-update revision/rescan transition, and commits pair state, audit,
    outbox, generation, FTS removal, lease release, and idempotency together.
  - Exact committed apply and rebase replay is resolved before reopening blob
    storage; operation-ID rebinding, stale source/target, non-reviewer apply, and
    all 14 documented write failpoints fail closed. Focused proof passes 24/24;
    proposal/finding/publication/edit/merge regression passes 121/121; schema and
    trigger regression passes 44/44. Evidence: `evidence/P6.6.md`. No route, UI,
    flag, worker, model, live state, or traffic was introduced. P6.7-P6.8 retain
    route/UI/shadow metrics and the aggregate P6 gate.

- [x] **P6.7 — Curation UI and shadow metrics**
  - Review queue, evidence, warnings, visibility, precision sampling, obsolete/rejected/ignored lifecycle.
  - Start semantic automation in shadow/proposal-only mode.
  - Completed behind disabled canonical traffic: the existing management
    candidate surface now renders a body-free review queue and authorized
    finding detail, including detector/model warnings, fixed revision evidence,
    visibility, reviewer lifecycle, and explicit approve/reject/ignore actions.
    Proposal and merge preview/apply adapters reuse the P6.5-P6.6 services;
    lease tokens remain header-only. The route group is absent unless a host
    explicitly binds the curation facade and enables a write-capable rollout.
  - A read-only bounded metric derives model proposal/adjudication precision
    from immutable finding evidence and reports general, risky-kind, and T3
    automatic-execution counts. Its status passes only when all three counts
    are zero. Focused route/UI/metric proof passes 18/18; retrieval/composition/
    finding regression passes 113/113; proposal/merge/edge/browser regression
    passes 81/81. Evidence: `evidence/P6.7.md`. No feature flag, traffic, model,
    worker, service process, live root, or live database was enabled or used.
    P6.8 retains the aggregate scenario matrix and P6 gate.

- [x] **P6.8 — Curation/merge tests**
  - Pass `CUR-001..011`, `PROP-001..005`, and `MERGE-001..004`; LLM automatic destructive actions remain zero.
  - Completed behind disabled canonical traffic: all 20 scenario IDs have
    explicit permanent test owners. Deterministic ingest/publication passes
    32/32; candidate/finding/semantic-edge/shadow/retrieval passes 100/100;
    proposal/merge passes 40/40; the ownership matrix passes 1/1. The bounded
    shadow report records general, risky-kind, and T3 model automatic effects
    as zero and fails if any becomes nonzero. Evidence: `evidence/P6.8.md`.

P6 gate:

- [x] Exact automatic handling false positives 0 in fixtures/sampled corpus.
- [x] Strong risky findings are Agent-hidden before review.
- [x] Semantic edge, merge, replace, deactivate, and purge model auto-execution count is 0.

The P6 implementation/test gate is complete behind disabled traffic. It starts
no detector/model/worker, enables no flag or traffic, and makes no live-corpus
or deployment claim.

## P7 — Durable jobs, projections, deployment, and recovery

Dependencies: P5 gate; P6 may proceed in parallel only where it does not touch the same modules.

- [x] **P7.1 — Durable job/claim service**
  - Replace in-memory callable authority with durable input references, claim lease, retry/dead state, cancellation, and idempotent result commit.

  Completed 2026-08-15 behind the disabled worker/traffic boundary. The P1.9
  durable repository and exact-version kernel remain the sole canonical
  enqueue/claim/effect authority: restart-safe input references, attempt/lease
  fencing, cooperative cancellation, retry/dead convergence, current-scope
  revalidation, and one-transaction domain effect/result commit were rerun
  with real process-kill/claim competition proof. P7.1 adds the missing
  actor-authorized generic job read/cancel service, strict canonical routes,
  and browser transport. Users can observe or idempotently cancel only their
  own user jobs; foreign and system jobs are nondisclosing, stale sessions fail
  closed, and cancellation, audit, and operation result commit or roll back
  together. Routes remain absent until the service is explicitly injected,
  and cancellation additionally requires a write-capable disabled rollout.
  No worker loop, model/GPU owner, projection materializer, flag, traffic, or
  service process was started. Focused P7.1 proof passes 20/20; the unchanged
  durable repository/kernel/process proof passes 32/32. See
  `evidence/P7.1.md`; P7.2 owns central scheduling, fairness, heartbeat/polling,
  and production web-process model-owner removal.

- [x] **P7.2 — Central inference coordination**
  - Web apps enqueue; one durable worker/service controls GPU model concurrency and fairness.
  - Disable production web-process GGUF autoload/model ownership while preserving development-mode compatibility.

  Completed 2026-08-15 behind the disabled worker/traffic boundary. A new
  explicit `DurableInferenceWorker` owns polling, expired-claim recovery,
  renewable heartbeat, bounded stop-aware backoff, and one-job execution. Its
  claim order derives handler-version and actor-lane recency from durable job
  history, so reconstructing the service does not reset fairness. Production
  local inference additionally requires the exact `worker` process role and a
  machine-wide Windows mutex; a second deployment or database configuration is
  rejected, while an owner crash is reclaimable. The `web` role cannot discover,
  resolve, load, or execute GGUF, and the legacy in-process job executor and
  Outlook scheduler are development-only. Existing canonical publication web
  producers continue to enqueue durable references and are processed through
  the exact-version registry. No worker/model/service was started and no flag,
  traffic, live root, or live database was enabled or accessed. Focused P7.2
  proof passes 13/13; durable repository/kernel/process proof remains 32/32.
  See `evidence/P7.2.md`. P7.3 is closed below; P7.4-P7.9 and the aggregate P7
  gate remain open.

- [x] **P7.3 — Outbox materializer**
  - Generate Markdown/index/log/graph/backlinks by space generation through staging/checksum/atomic manifest.
  - Full rebuild and incremental settled checksums match.

  Completed 2026-08-15 behind the disabled worker/traffic boundary. Space-bound
  outbox append now advances the matching Markdown projection desired generation
  in the same canonical transaction. An explicit `OutboxProjectionWorker`
  performs expired-lease recovery, fenced claim/heartbeat/retry/dead transitions,
  complete per-space immutable generation materialization, checksum verification,
  atomic manifest replacement, and current-generation acknowledgement. Full and
  incremental rebuilds produce the same manifest checksum; canonical retrieval
  remains authoritative while projection lags. Real Windows process kill and
  two-process claim tests prove exact replay and one claimant. No worker/service,
  route traffic, feature flag, live root, or live database was started or used.
  Focused projection proof passes 48/48 and affected canonical producer regression
  passes 187/187. See `evidence/P7.3.md`. P7.4-P7.9 and the aggregate P7 gate
  remain open.

- [x] **P7.4 — Backup/preflight/restore tooling**
  - Online SQLite snapshot, durable GC/backup hold, blob manifest/hash, same-volume staging/final rename, retention, quick/FK checks, isolated restore and projection rebuild.
  - Manifest records app/schema/SQLite source ID, audit high-water, blob count/hashes, configuration and key-set versions.
  - Before any personal production write, either implement and replay-test a verified canonical-delta exporter or record an explicit roll-forward-only deployment decision in the approval packet.
  - Verified behind the P0.2-isolated, disabled-traffic boundary. The durable
    run/blob repository, online snapshot, pinned filesystem storage, strict
    manifest, 96/30/12 retention, operator CLI/wrappers, isolated restore, and
    full projection rebuild are implemented. Real-process write concurrency and
    all four backup crash boundaries prove no false completion and resumable
    exact output. Focused backup/recovery proof passes 45/45; affected blob-GC,
    import staging, and projection regression passes 32/32. Accepted decision
    ADR 016 freezes the
    roll-forward-only decision for the future P8.1 approval packet. See
    `evidence/P7.4.md`. P7.5-P7.6 are closed below; P7.7-P7.9 and the aggregate
    P7 gate remain open.

- [x] **P7.5 — Production WSGI/Windows service packaging**
  - Pin the exact production server version, threads/connection limits, service wrapper, service account/NTFS ACL, local non-reparse working/data paths, ports, protected environment file, logs, graceful drain and migrator→web→worker/projector start order.
  - Keep development runner separate.
  - One checkpoint coordinator uses periodic `PASSIVE`; `TRUNCATE` is maintenance/drain-only.
  - Verified behind the disabled-service boundary: Waitress `3.0.2` and WinSW
    `2.12.0`/x64 asset SHA are exact; the self-hashed deployment manifest owns
    network, process, ACL, path, wrapper, drain, and start-order policy. Five
    explicit service entrypoints include a fresh-bootstrap-safe one-shot
    migrator, two actual Waitress hosts, the durable worker, and the projector
    with the sole machine-wide periodic `PASSIVE` coordinator. Protected env
    authority, active-wrapper hash, local NTFS/non-reparse path, ambient-test,
    package-drift, named-stop, in-flight drain, and cross-process checkpoint
    ownership proofs pass. Install/start/stop scripts apply the reviewed ACL,
    avoid password/XML leakage, roll back partial install, health-gate both web
    hosts, and drain in reverse order. See `evidence/P7.5.md`. No service was
    installed or started, so target SCM/account/ACL observation and aggregate
    `OPS-005`/`G-11` target deployment evidence remain later operational gates;
    P7.6 health/metrics is closed below.

- [x] **P7.6 — Health/metrics**
  - Build/schema/SQLite/pragmas/flags, DB/WAL/disk, busy/retry, queue/outbox lag, leases, backup age, projection generation.
  - Verified 2026-08-15 behind the disabled-service/traffic boundary. Both
    production Flask hosts receive one cached, read-only, secret-free health
    provider. The schema-v1 snapshot binds application/config/catalog/current
    migration, exact SQLite source/runtime/compile options/pragmas/FTS/integrity,
    effective flags and failpoint absence, DB execute/busy/locked counters,
    durable job/outbox attempts/retries/oldest age/dead states, active/stale
    leases, projection generation/lag/errors, latest verified backup age, and
    DB/WAL/referenced-blob size plus the three-times free-space gate. Database,
    database-parent, and blob paths are re-probed as fixed local NTFS,
    non-reparse, final-path-identical roots before any DB/file observation.
    Start scripts require the exact health schema and a ready/empty-reason gate.
    Focused proof passes 101/101 and the adjacent backup/worker/projector/
    multiprocess/auth/runtime selection passes 291/291. See `evidence/P7.6.md`.
    No target service, traffic, worker, flag, live root/DB, or monitoring sink
    was started or changed; P7.7 is closed below, while P7.8-P7.9 and the
    aggregate P7 gate remain open.

- [x] **P7.7 — Fault/restart suite**
  - Pass `PROJ-001..006`, `JOB-001..006`, `OPS-001..006`, backup-under-write, failpoints, and 20 randomized worker-kill profile.
  - Verified 2026-08-16 in the isolated Windows test boundary. All 54 required
    failpoint names are frozen, unique, present in production code, and
    exercised by permanent tests. Transaction-internal failures restore the
    exact baseline; post-commit response loss and hard process termination
    converge through exact replay. Projection tests cover disk-full,
    permission, atomic-replace failure, stale staging cleanup, and a process
    kill after replacement before acknowledgement. The fixed-seed durable-job
    profile performs exactly 20 child-process terminations and converges with
    lost jobs and duplicate effects both zero. `PROJ-001..006`,
    `JOB-001..006`, `OPS-001..006`, and backup-under-write all pass. See
    `evidence/P7.7.md`. No production service, worker, traffic, flag, live
    root/database, or target host was used; P7.8-P7.9 and aggregate P7 remain
    open.

- [ ] **P7.8 — Forty-client performance gate**
  - Execution is deferred until SBE-7.2. The sealed runner remains valid
    preparation, but the target profile and acceptance binding must be
    rebaselined against the successor release candidate before traffic runs.
  - Run `PERF-001` steady/hot/saturation/burst/leak profiles on the target-class Windows server and archive raw results.
  - The sealed runner, independent JSON/CSV verifier, and five-profile gate
    binder are implemented in `tools/performance_load.py` with entry points in
    `tests/knowledge_spaces/load/`. They require explicit traffic confirmation,
    both production WSGI health endpoints, exact fixed durations/seed, one
    40-account/four-part fixture, a 40x39 cross-space canary matrix, and pinned
    local-NTFS evidence I/O. This is preparation only: no target service or
    traffic was used, so `PERF-001` and P7.8 remain unchecked until the five
    target reports and their passing gate artifact are archived.

- [ ] **P7.9 — Restore drill**
  - Execution is deferred until SBE-7.2 so the drill includes multi-Wiki,
    higher-role, aggregate-edge, suppression, temporal, and projection-v2 state.
  - Complete at least one isolated restore with RPO ≤1h, RTO ≤4h, and zero integrity/projection mismatch.

P7 gate:

- Lost durable jobs and duplicate domain commits: 0.
- CRUD/search/projection/busy targets pass.
- Patched SQLite, local NTFS, production WSGI, verified backup, and restore evidence pass.

## SBE — Approved Second Brain evolution (execute before P7.8/P7.9/P8)

Dependencies: the registered package in `second_brain_evolution/`. Existing P0-
P7.7 evidence remains historical and unchanged. The exact packet definitions,
file ownership, feature-flag dependencies, gates, and active acceptance IDs are
in `second_brain_evolution/EXECUTION_PLAN.md` and `ACCEPTANCE.md`.

- [x] **SBE-0.1 — Contract transition and exact baseline** — `SBEOPS-001..002`, `SBEOPS-004`
- [x] **SBE-1.1 — v8 multiple-personal-Wiki schema** — `WIKI-001..004`; verified in `evidence/SBE-1.1.md`
- [x] **SBE-1.2 — v9 Team/담당 leadership and native-space schema** — `HSPACE-001..005`; verified in `evidence/SBE-1.2.md`
- [x] **SBE-1.3 — v10 aggregate/automatic-edge schema** — `AGRAPH-001..004`, `AUTOEDGE-001..003`, `REF-001..002`; verified in `evidence/SBE-1.3.md`
- [x] **SBE-2.1 — Wiki lifecycle repository/service/API** — `WIKI-005..012`; verified in `evidence/SBE-2.1.md`
- [x] **SBE-2.2 — Conversation-Wiki binding and archived history** — `WIKI-013..017`; verified in `evidence/SBE-2.2.md`
  - Conversation creation/reopen/turns bind the exact server-authorized
    Wiki/view and closure digest. Browser state cannot rebind a session;
    archived sessions remain readable history and reject normal mutations until
    exact restore. Lifecycle/role/organization closure changes fence stale
    retrieval and commit. The successor flag remains disabled.
- [x] **SBE-2.3 — CorpusClosure and higher authority** — `HSPACE-006..014`;
  verified in `evidence/SBE-2.3.md`
  - User-approved forward-only v11 adds immutable exact Team/Responsibility
    publication authority and exact current-role curation while preserving
    v1-v10 byte-for-byte. The temporal/footer migration remains assigned to
    v12 and is not implemented by this packet. All successor traffic flags
    remain disabled.
- [x] **SBE-3.0 — Windows stager least-privilege/race hotfix** — `UPLOAD-001..004`; verified in `evidence/SBE-3.0.md`
  - Completed 2026-08-24 without schema, publication-policy, extractor, UI,
    flag, or live-root changes. Directory anchors no longer request
    `FILE_DELETE_CHILD`; shard traversal and existing-object reads remain
    handle-relative/no-follow; directory write sharing supports independent
    contenders while delete sharing remains denied; bounded winner handling,
    retained read leases, and exact frozen pre-transaction evidence are covered
    by real Windows process and drift tests.
- [x] **SBE-3.1 — Canonical rich upload and explicit references** — `UPLOAD-005..012`, `REF-003..005`; verified in `evidence/SBE-3.1.md`
  - Native PDF/DOCX/PPTX/XLSX/Markdown/text extraction, bounded atomic child
    assets, frozen source/filename provenance, automatic curation release or
    hidden review/quarantine, independent personal+Part outcomes, projection
    convergence, exact stable-ID reference authorization/replay, revision-drift
    visibility, and atomic target-inactive availability are verified. No
    successor traffic, company provider, semantic model job, or live root was
    enabled or used.
- [x] **SBE-3.2 — v12 temporal provenance and projection footer** — `TIME-001..019`; verified in `evidence/SBE-3.2.md`
  - User-approved forward-only v12 adds append-only temporal assertion and
    verification streams, CAS-protected heads, an independent temporal
    generation, exact v2 temporal routes, deterministic footer-v2 presentation,
    a canonical manifest trust ledger, and safe ledger-bound re-import. Canonical
    bodies, content/FTS/search hashes, and v1 source projections remain unchanged.
    `KNOWLEDGE_CORE_PROJECTION_FOOTER_V2` remains disabled by default and no live
    migration, root, port, provider, or company API was used.
- [x] **SBE-4.1 — LCA aggregate graph** — `AGRAPH-005..014`; verified in `evidence/SBE-4.1.md`
  - Exact local/Team/Responsibility LCA placement, current endpoint fences,
    origin-preserving effective reads, ancestor-only generation invalidation,
    topology-aware vectors, and graph-only immutable projection are complete.
    Part graphs remain isolated; Team-home changes propagate only to their
    strict higher ancestors. `KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS` remains
    disabled by default and no live root, port, provider, or company API was used.
- [ ] **SBE-4.2 — Validated automatic semantic edges and correction** — `AUTOEDGE-004..016`
  - Runtime implementation and deterministic local gates `AUTOEDGE-004..015`
    are complete in `evidence/SBE-4.2.md`; both rollout flags remain disabled.
    The task stays unchecked/`IMPLEMENTED_NOT_GATED` until the approved held-out
    provider run closes `AUTOEDGE-016` without using the deferred company API.
- [ ] **SBE-5.1 — Authorized deterministic retrieval** — `RANK-001..014`, `REF-006`
  - Runtime implementation and deterministic local gates `RANK-001..013` plus
    `REF-006` are complete in `evidence/SBE-5.1.md`; the rollout flag remains
    disabled. The task stays unchecked/`IMPLEMENTED_NOT_GATED` until an approved
    Korean/English, per-scope held-out corpus closes the exact `RANK-014` quality
    and unauthorized-citation thresholds.
- [ ] **SBE-5.2 — Optional local reranker and temporal ranking** — `RANK-015..021`, `TIME-020..022`
  - Runtime implementation and deterministic local gates `RANK-015..021` plus
    `TIME-020..022` are complete in `evidence/SBE-5.2.md`; both rollout flags
    remain disabled. The task stays unchecked/`IMPLEMENTED_NOT_GATED` because
    its SBE-5.1 prerequisite retains the external held-out `RANK-014` quality
    gate. No company API is required for runtime correctness.
- [ ] **SBE-6.1 — Multi-Wiki/archive/upload UI** — `WIKI-018..022`, `UPLOAD-013..015`
  - Runtime/UI implementation and automated local gates are complete in
    `evidence/SBE-6.1.md`. The packet stays unchecked/`IMPLEMENTED_NOT_GATED`
    until the in-app Browser runtime can execute the real click/accessibility
    flow and prerequisite `RANK-014` is closed.
- [ ] **SBE-6.2 — Higher native write/aggregate graph/correction UI** — `HSPACE-015..018`, `AGRAPH-015..017`, `AUTOEDGE-017..019`, `REF-007..008`
  - Runtime/UI implementation and automated local gates are complete in
    `evidence/SBE-6.2.md`. The packet remains unchecked/`IMPLEMENTED_NOT_GATED`
    until the real Browser flow and prerequisite provider/evaluation gates close.
- [ ] **SBE-7.1 — Integrated security, correctness, fault, and scale gate** — `SBEOPS-003`, `SBEOPS-005..013`
  - The canonical isolated composition, focused regressions, migration/contract
    audit, restart replay, HTTP flow, and local Codex bridge pass are recorded in
    `evidence/SBE-7.1.md`. The complete cross-process/security/fault/scale/
    backup/browser matrix remains open.
- [ ] **SBE-7.2 — Isolated 10111 activation and external handoff** — `SBEOPS-014..018`
  - Marker-v2 candidate and handoff are recorded in `evidence/SBE-7.2.md` and
    `SECOND_BRAIN_EXTERNAL_HANDOFF.md`. It is healthy on isolated port 10121;
    exact 10111 activation and the 7-Part x 10-user corpus remain open.
- [x] **SBE-8.11 — Full-corpus path parity and web higher-scope gating** — `RANK-003..009`, `RANK-013`, `RANK-015`, `RANK-021`
  - Personal Wiki, Part, Team, and 담당 backend retrieval share the no-fixed-
    document-count policy and exact 96 KiB Agent excerpt budget. The web scope
    selector keeps personal Wiki/Part enabled and disables Team/담당 choices;
    controller/backend access stays available for future clients and bound
    history. Local proof is recorded in `evidence/SBE-8.11.md`.

SBE gate:

- Every packet is `VERIFIED` with `evidence/<packet_id>.md` (for example,
  `evidence/SBE-0.1.md`).
- Current-v7 compatibility stays green while every successor flag defaults off.
- Local deterministic and Codex-bridge validation is separated from unexecuted
  company-provider and target-server evidence.
- P7.8/P7.9/P8 may resume only after SBE-7.2 records the new release-candidate
  hashes and rebaselined target procedures.

## P8 — Controlled pilot and part-write enablement

Dependencies: every P1–P7 gate, SBE-7.2, rebaselined P7.8/P7.9 evidence, and
explicit user authorization for target-server deployment/migration.

- [ ] **P8.1 — Production migration rehearsal and approval packet**
  - Final inventory/plan/reconciliation, backups, rollback decision tree, org mapping, service configuration, evidence hashes.

- [ ] **P8.2 — Maintenance cutover**
  - Follow `MIGRATION_ROLLOUT.md`; personal reads/writes first, part writes off.

- [ ] **P8.3 — Personal canary and soak**
  - Login, Notion, chat, personal knowledge, idempotency, backup, queue/projection observation.

- [ ] **P8.4 — Pilot part reads**
  - Enable one configured part; canary leak scan and performance observation.

- [ ] **P8.5 — Pilot part writes**
  - Record prerequisite gate version, enable explicitly, monitor lease/audit/outbox/backup.

- [ ] **P8.6 — Broader 40-user rollout**
  - Expand by part only after pilot evidence and rollback readiness.

- [ ] **P8.7 — Goal completion audit**
  - After SBE-0.1 registration, provide evidence for every `G-01..G-20`
    traceability row and all mandatory acceptance scenarios; before registration,
    the active baseline remains exactly `G-01..G-14`.
  - Rerun `REG-001..003` in the final cutover-compatible mode and require all to pass.
  - The successor Team/담당/multi-Wiki/automatic-edge flags follow the exact
    SBE rollout packet and cannot be silently omitted from the final goal audit.

## Next implementation task

2026-09-08 active local packet: **SBE-8.13**, approved long-document and
multi-author improvements. See [owned packet](evidence/SBE-8.13.md).

- [ ] Lazy authorized full-body context, no per-document 4 KiB cutoff, honest
  shared-budget range/omission disclosure; regression and independent review.
- [ ] Iterative overflow reading and full-corpus coverage accounting.
- [ ] Finish reranker evidence work bounds: deduplicate neighbor revisions,
  budget total source scanning and serialized input without dropping authorized
  candidates, and prove deterministic fallback plus latency/peak memory.
- [ ] Server-resolved citation handles rather than model-retyped UUID URLs.
  - Fourth-packet server transform and actual-body provenance are implemented;
    overlapping 82 pure/executor, eight four-scope and four historical-reference
    model-free cases pass. Keep this gate open for browser/SSE, compatibility
    provenance, many-original rendering, lifecycle and live-model evidence.
- [ ] Real LLM file-to-Wiki authoring, retaining raw/extracted provenance and
  explicitly labeled provider-unavailable fallback.
- [ ] Fresh multi-author uploads and actual conversations at all four levels,
  with withheld questions, isolation, updates/conflicts and source traceability.

These are new gates; historical short-document/live-turn counts below do not
close them. Company model quality and target-class operations remain handoff.

SBE-8.13 second-packet progress: 158 model-free focused tests, two post-version
cache/API tests, and 18 independent targeted tests pass. Verified CAS bodies,
real revision-edit fencing and expanded-query passage delivery are implemented.
The new graph-preview resource gates above block activation, not further work.
Third-packet implementation now includes post-dedupe source preflight/batching,
JSON-aware fair preview allocation, policy-bound cache identities and disabled
runtime fast paths. Synthetic Windows probes demonstrate reduced Python copies
but still substantial SQLite/process RSS growth; source-byte bounds are not
whole-process memory proof. Final-version 199-test regression and independent
targeted review pass. The remaining work/output-scale and goal-level
authoring/citation/iterative/live gates are still required.
Fourth-packet citation trace distinguishes supplied bodies from actually cited
identities and explicitly does not verify semantic claim support. Historical
references use as-of-revision artifact metadata. Independent parser/executor
review passes, but the browser and lifecycle gates above remain. Native host
access/heap exceptions during tests are recorded in SBE-8.13; no listener was
restarted and no host-runtime repair was attempted.
The retained final fourth-packet focused/adjacent runs total 276 disjoint
model-free cases. Eight existing four-scope cases additionally pass canonical
completion/storage and cursor-event replay; browser/network and live-model
gates remain open. Authoring must not overwrite or relabel the immutable
extraction snapshot: retain extraction and generated Wiki separately before
integrating generated content into the frozen preview/publication contract.

2026-09-09 fifth-packet progress: provider-neutral source/chunk authoring v3,
exact opt-in API wire receipts, and optional v14 authored-body storage are
implemented only as isolated boundaries. The final 176-case model-free regression
includes 30 synthesis v2 cases, including exact-once owner batches and read-only
helper references. Synthesis v1 real-PDF results remain separate, not relabeled
as v2: all three are smaller but the initial independent review still finds
boundary and false whole-document uncertainty defects. The later real v3 probe
removes false partial warnings in two completed sources but fails overall:
AMHS timed out and food/beauty loses a non-gold condition. Current v4 adds
verified whole-map/ordered-wave identity and predecessor replay, with 237
model-free regressions plus a latest 66-case synthesis file (60 overlap).
Fresh-process fake-response reconstruction is not durable worker or live
four-scope evidence. Before activation:

- [ ] Finish public upload/authoring integration. Internal publication now binds
  authored body, current authority, original/extraction identity, FTS release and
  exact replay. Source-equivalent uploaded bytes/name reuse the first authorized
  native body despite a new model paraphrase, preserving new source provenance.
  The public adapter must server-select/carry the ready body and cannot silently
  omit it. Generation-before-reuse still wastes an unnecessary model call;
  integrate early reusable-body selection in the authoring workflow.
  Extraction-only-first preservation is now implemented internally: exact body
  identity remains extraction, a separate immutable receipt binds new candidate
  and prior extraction/operation, and fresh upload provenance accumulates without
  duplicate items. Public response disclosure for the internal prior-extraction
  identity must be decided when wiring the adapter, not exposed accidentally.
- [ ] Persist per-chunk and per-reduction jobs/receipts and recovery fences;
  no whole-document model loop inside a write transaction or one worker lease.
  Initial source/plan verification now occurs before the writer. The internal
  upload-derived `author_chunk/v1` producer, exact results, recovery and
  pre-model/commit fencing exist with model-free tests. Next: model-free durable
  plan successor, per-reduction jobs/receipts, last-chunk finalizer and final
  reduced-Wiki server selection. Full-source/plan reconstruction per chunk still
  needs total work/RSS optimization; per-job bounds do not prove linear scaling.
  The shared pure synthesis stage/replay boundary now restores exact persisted
  response/model/transport inputs with no model call, returns only missing
  current-wave batches, and rejects gaps, spliced future responses and terminal
  extras. Ordered model pairs preserve v4 request ancestry after restart.
  Complete-wave retry-product accounting and final rendering pass 193 focused
  model-free regressions. Next integration must persist the frozen synthesis
  policy, wave/batch identities and immutable receipts, bind each to its actual
  durable job, and reauthorize source/role/epoch before provider and commit.
  Reuse the replay boundary; do not implement another whole-document model loop
  in a job or treat map assembly as reduced-Wiki completion.
- [ ] Finish remaining optional schema/service invariants, blob-source loading,
  retention and cancellation. Terminal timestamps, complete ready-body/chunk
  binding and metadata selection guards now exist in the isolated v14 catalog;
  source-preserving internal publication now selects authored content. Optional
  v14 also guards selected revision/body identity and immutable selection history;
  pending extraction fallback is atomic. The internal chunk registry now
  converges queued/running/external cancellation, preview eligibility and
  extraction selection. Finalizer/reduction/blob-retention integration and
  outbox consumer semantics remain open. The runtime catalog/public uploader
  remain unchanged. Before activation, freeze total plan/policy metadata and
  chunk-record admission under a version-correct successor; v3 defaults already
  bound whitespace chunks, but arbitrary trusted custom model-policy arrays
  are not covered by the current source-message work reservation.
- [ ] Finish reupload lifecycle/retention and target-race matrix: edited/inactive
  source histories cannot be silently replaced/restored/duplicated; byte-identical
  newer current revisions retain their item and receive fresh provenance. Preserve
  original-only references and existing unchanged-operation lineage policy.
  Bound total SQL history work, not merely the now-bounded Python target summaries;
  test source fingerprint/version changes, role/membership moves, current revisions,
  purge/retention, cross-process winners, large repeated-source corpora and replay.
- [ ] Independently evaluate key facts beyond the development gold list,
  page/chunk continuation, duplicates, negation and conflicting conditions.
- [ ] Prove whole-document planning/quote/output work and actual memory/latency;
  conservative byte allowances and structural tests are not load evidence.
- [ ] Exercise actual multi-author rich-file uploads, publication and follow-up
  questions in personal, Part, Team and Responsibility views before any claim
  that the current service is using LLM-authored rather than extracted Markdown.

P2.8, P7.1-P7.7, and SBE-0.1 through SBE-4.1 remain closed by their archived
evidence. SBE-4.2 through SBE-6.2 runtime implementations and the local SBE-7.1
composition are complete while held-out provider, real Browser, and complete
integrated gates remain open. The marker-v2 SBE-7.2 candidate is healthy on
10121. The next local work is the remaining SBEOPS matrix, especially the
meaningful 7-Part x 10-user corpus; exact 10111 activation waits for operator
authority over the existing approved listener. No successor traffic flag is
enabled outside the marker-bound disposable preview. The user authorized
v11/v12 changes if required; no DDL change was needed and frozen migration
hashes remain unchanged.

P7.8/P7.9 and P8 remain open but execution-deferred until SBE-7.2. Nothing in
the planning package authorizes a production migration, target traffic, feature
flag, live-root write, or company API call.
