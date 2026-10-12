# NP-2C canonical page tree and lifecycle design

Status: 1312 lifecycle guards, the NP2C-3b route-unregistered legacy-v3 rename source adapter, the frozen 1313 atomic rename catalog finalizer, and the NP2C-3d exact-sentinel end-to-end rename rehearsal are contract-tested against temporary owner shards/catalogs. NP2C-3e1 adds route-unregistered create/move/reorder source adapters, NP2C-3e2 freezes workspace 1314 structural effect/order/proof guards, and NP2C-3e3 freezes workspace 1315 structural finalization authority plus an exact-sentinel source↔catalog rehearsal. NP2C-4a freezes the duplicate/Trash/restore design, NP2C-4b adds the route-unregistered source-v3 adapter and deterministic file recovery, NP2C-4c freezes workspace 1316 lifecycle-subtree evidence and fail-closed raw-SQL guards, NP2C-4d freezes workspace 1317 atomic catalog finalization authority plus an exact-sentinel offline rehearsal, and NP2C-4e independently closes source-ledger recovery, rollback, exact-bound, redaction and deep-health evidence. NP2C-5 freezes only the official-reference Archive/unarchive design, candidate source-v5/workspace-1318 lineage and live-characterization requirements. Runtime source↔catalog orchestration, routes, UI, official Archive/unarchive implementation, destructive purge, live migration, watermark acknowledgement, and resolver cutover remain disabled.

As of: 2026-08-14

> **2026-08-14 scope note:** 이 문서의 1311~1317 lifecycle 계약과 운영 cutover 금지는 유지한다.
> Official Archive/NP2C-5a는 optional paid-feature lane이다. Core Free Web UI의 shell, page tree, Trash/restore와
> editor fixture 개발은 `../NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`에 따라 isolated/default-off로
> 진행할 수 있으며 Business/Enterprise Archive characterization을 기다리지 않는다.

Machine contract: `docs/notion_parity/np2c_page_tree_lifecycle_contract.json`

## 1. Purpose and boundary

NP2B-1/2/3 delivered a 1310 workspace navigation foundation and a default-off UI. They did not make the workspace catalog authoritative for the page tree. The current workspace page response still obtains `parent_id` and `position` from an exact legacy owner database, and the current move, duplicate, archive, restore, and permanent-delete routes still call the legacy store directly.

NP-2C establishes the contracts needed to make tree and lifecycle mutations durable and replayable without pretending that SQLite transactions across a workspace catalog and a legacy owner shard are atomic.

This document does not authorize any live schema migration, request/startup migration, continuous worker, destructive watermark acknowledgement, legacy directory-scan resolver, route replacement, UI enablement, or permanent deletion. The implemented NP2C-2a materializer may only transition an empty `unmaterialized` catalog tree to one exact `complete` projection under an explicit rehearsal sentinel and source-quiescence assertion.

### 1.1 Implemented NP2C-2a boundary

`notion_workspace_page_tree.py` now captures at most 1,000 active, currently attested bindings from exact owner shards. It opens each owner shard exactly twice for a new effect, performs the database-wide integrity check only on the first snapshot, reuses an exact file/schema/epoch attestation for the second snapshot, and derives parent-first nodes with a depth cap of 256 and dense visible sibling ordinals. A hidden/unbound parent, cycle, inactive/retired incarnation, changed receipt, changed binding set, or changed tree/workspace fence refuses the effect with zero catalog write.

`WorkspaceStore.materialize_page_tree_snapshot` publishes state, nodes, operation, audit, and outbox in one catalog transaction. Its replay path reauthorizes the current admin and validates the complete immutable state/node/result/event commitment. The offline wrapper probes that replay before sentinel or source-path access and returns identifier-free counters. It performs no source mutation, watermark acknowledgement, runtime registration, feature enablement, resolver cutover, or lifecycle command.

## 2. Current observed implementation

The checked-in source currently has the following behavior:

- `workspace_page_sources` identifies a page by `(workspace_id, source_owner_subject_id, source_page_id)` and stores placement, binding state, source proof, and binding generation. It has no canonical parent or sibling order.
- `workspace_page_source_projections` commits file/public locator projections for one binding generation. It has no tree structure.
- `notion_workspace_pages.list_workspace_pages` reads active bindings, opens only the exact known owner shards, obtains legacy summaries, blanks a parent that is not visible in the result, and sorts by legacy position. This is a compatibility read, not a canonical tree snapshot.
- legacy create is one source transaction, but has no caller operation ID;
- legacy rename uses `update_page` and explicitly rejects parent changes;
- legacy move is owner-only, detects cycles, renumbers destination siblings, and updates old/new parent child-page blocks in one source transaction, but has no durable replay record;
- legacy duplicate copies a subtree, blocks, files, and internal references, but file bytes and database state do not share one filesystem transaction and generated IDs are not replay-derived;
- legacy `archive_page` is called by the HTTP DELETE/Trash routes and therefore represents Trash compatibility, not the current official Archive feature; it marks the selected page and all active descendants archived in the legacy bit;
- legacy restore restores the selected trashed page, all descendants, and all ancestors, which is broader than an explicit restore target;
- legacy permanent delete removes archived database rows first, then deletes file bytes best-effort after commit. A process failure can therefore leave orphan bytes, and a retry has no durable command result.

These behaviors remain compatibility baselines. They are not evidence that NP-2C is complete.

## 3. Authority model

Until an explicit cutover gate is approved:

1. the legacy owner shard remains authoritative for page content, physical parent, source lifecycle, files, shares, and public-link state;
2. the workspace catalog may hold a durable, generation-fenced structural projection and command saga, but it must never invent a successful source mutation;
3. a catalog tree row is usable only when its exact page binding, source incarnation, and source receipt remain current;
4. structural reads may use the catalog without opening a source DB, while display hydration may open only the exact known owner shard and must have no directory-scan fallback;
5. catalog and source commits are converged as an at-least-once, state-idempotent saga. Exactly-once delivery is not claimed.

## 4. Canonical identity

Every page reference is the full locator:

`(workspace_id, source_owner_subject_id, source_page_id)`

A bare page ID is never sufficient across catalog boundaries. Mutation preconditions additionally bind:

- current binding generation;
- exact legacy incarnation ID and incarnation generation;
- exact source epoch, full-sweep generation, device, inode, and link count;
- current page change generation and mask;
- workspace tree version;
- actor and operation ID;
- source command request digest.

A deleted page ID recreated with a new legacy incarnation is a different identity. It cannot inherit the prior parent, placement, favorite eligibility, public projection, or restore authority without an explicit reviewed rebind.

## 5. 1311 schema decision

A new forward-only workspace schema is required. The candidate version is `2026081311`. Frozen 1310 SQL and hashes must remain independently importable by the 1310 migrator.

The minimum candidate schema consists of three concerns. Exact DDL is a later implementation slice and must match the machine contract.

### 5.1 Workspace tree state

One row per workspace records:

- monotonic `tree_version`;
- materialization status: `unmaterialized`, `partial`, or `complete`;
- bounded node counts and last complete source-fence set digest;
- trusted timestamps and the operation that last changed the tree;
- an explicit flag that resolver cutover is not enabled merely because the tree is complete.

Tree structure mutations increment `tree_version` once. Navigation preferences and favorites do not.

### 5.2 Tree nodes

One row per exact page locator records:

- binding generation and source incarnation evidence;
- nullable full parent locator, never a bare parent page ID;
- dense positive sibling ordinal within the exact parent scope;
- lifecycle state `active`, `archived`, or `deleted`;
- source change generation and a structural commitment digest;
- row version and operation correlation.

For an active node, the referenced binding must be active and current. An archived or deleted node is not visible in an active tree. A deleted tombstone is retained for replay and identity safety; it is not a resolver target.

Parent and child must be in the same workspace. Cross-owner parentage is rejected for the current legacy shard model. A non-null parent must identify an extant node of the same active lifecycle set when a tree is complete. Partial materialization may retain an explicit unresolved-parent state internally, but public reads flatten it without exposing the hidden locator.

Sibling order is dense and unique. Root is a distinct parent scope with both parent locator components null.

### 5.3 Lifecycle command saga

One durable row per `(actor_subject_id, operation_id)` records an immutable request plus fenced progress:

- action allowlist;
- exact target and optional parent/relative sibling locator;
- expected workspace, tree, binding, and incarnation versions;
- canonical request SHA-256;
- phase `prepared`, `source_applied`, `finalized`, `failed`, or `manual_audit`;
- source-effect receipt digest and identifier-free counters;
- lease token digest, fencing generation, row version, and bounded failure code;
- final operation/audit/outbox correlation.

The command row is not a substitute for source evidence. `source_applied` requires an exact immutable source receipt, and `finalized` requires a catalog transaction that rechecks the current actor, source receipt, binding/incarnation fence, and tree CAS.

Raw SQL must not be able to forge lifecycle transitions. Service-context UDFs, exact transition triggers, immutable identities, trusted-clock checks, and deep-health correlation are required.

## 6. Command protocol

The protocol is deliberately split across durable phases.

### Phase A: authorize and prepare

1. Strictly validate the request before path or source access.
2. Reauthorize the current workspace actor and source authority.
3. Read the current tree/binding/incarnation fence.
4. Insert an immutable prepared command in one catalog transaction.
5. Return or continue with no source I/O inside that transaction.

### Phase B: apply or recognize the source effect

1. Acquire a bounded lease/fence for the prepared command.
2. Open only the exact owner shard under the approved sentinel/root and quiescence policy.
3. Recheck the source incarnation and change receipt.
4. Apply a source mutation with deterministic identities or recognize the exact already-applied post-state.
5. Persist an immutable, secret-free source receipt before reporting the phase complete.

Create and duplicate must use replay-stable page/file mappings or a source-side immutable receipt. Move, rename, archive, and restore must compare both exact pre-state and exact post-state. Permanent delete additionally requires a retained incarnation tombstone and durable file-cleanup work.

### Phase C: finalize the catalog

1. Reauthorize the current actor.
2. Revalidate the exact source receipt outside the catalog writer transaction.
3. Begin the catalog writer transaction.
4. Recheck command lease/fence, tree version, binding version, incarnation, and source receipt.
5. Atomically replace the affected tree rows and relevant binding/projection/lifecycle state.
6. Increment the workspace tree version once and the workspace/security versions only when required by the security semantics.
7. Commit operation, audit, and outbox records with a secret-free canonical payload.
8. Mark the command finalized in the same transaction.

Crash after the source effect and before catalog finalization must converge by recognizing the exact source post-state. A changed or ambiguous source state goes to `manual_audit`; it is never guessed as success.

## 7. Action semantics

### Create

- parent is either the root scope or an exact active parent locator;
- source owner follows the approved shard/parent authority;
- generated identity is replay-stable;
- a created node becomes visible only after source receipt and catalog finalization;
- failure must not leave a visible catalog node without a source page.

### Rename

- title/content never enters public command, audit, or outbox payloads;
- expected source revision/change generation is required;
- the tree row changes only its structural commitment/version, not its locator or ordinal;
- stale edit and operation-ID reuse fail closed.

### Move and reorder

- target parent and before/after sibling are exact locators;
- `before` and `after` are mutually exclusive;
- relative sibling must belong to the resulting parent scope;
- self-parenting, descendant cycles, cross-workspace moves, and current-model cross-owner parentage are rejected;
- all affected source siblings and parent child-page references change in one source transaction;
- all affected catalog sibling ordinals change in one catalog finalization transaction;
- ordinals are dense after success;
- a stale drag response cannot overwrite a newer tree version.

### Duplicate

- the entire selected active subtree is bounded before mutation;
- page, block, and file mappings are deterministic or durably receipted;
- copied internal references target copied identities;
- shares, guests, public links, comments, notifications, and external integrations are not copied unless a separately characterized policy says so;
- copied files require size/hash verification and durable cleanup on failure;
- the duplicate is published to the tree only after the complete source effect is proven.

### Trash compatibility (legacy name: Archive)

- the selected node and its exact active descendant set form one fenced subtree;
- active resolver projections and favorites are removed or hidden atomically with catalog lifecycle finalization;
- original parent/order/placement/incarnation evidence is retained for restore;
- open editors and cached capabilities must be invalidated before a future runtime enablement.

### Restore

- restore requires the same legacy incarnation; a recreated ID is not a restore;
- the original parent is used only if it is still active, visible, and the exact expected incarnation;
- otherwise the command requires an explicit reviewed root/fallback placement or returns a conflict;
- ancestors are never silently restored merely because a descendant was selected;
- file and public projections are republished only after exact source revalidation.

### Official Archive and unarchive

- Archive is distinct from Trash and keeps content addressable under a plan/capability-specific visibility policy;
- the current legacy `pages.archived` bit cannot represent both states safely;
- no action may alias official Archive to `archive_page`;
- a separate source lifecycle schema/lineage review is required, while H5e source version 1304 remains reserved;
- Archive/unarchive remains disabled after NP2C-4a.

### Permanent delete and purge

- disabled in this slice;
- requires a separately approved retention and legal policy, current workspace admin authorization, explicit confirmation, backup/restore evidence, and a source tombstone;
- DB deletion and file cleanup use durable work with retry and orphan accounting;
- successful purge retains the minimum immutable command/audit/incarnation tombstone needed for replay and compliance;
- request handlers never run an unbounded purge.

## 8. Authorization and zero-leak rules

Until NP-2D supplies one unified capability resolver, runtime lifecycle routes remain disabled. Offline/test commands use the narrowest existing authority and revalidate it at every phase.

Required rules:

- active workspace membership; guests cannot mutate;
- private pages require the source owner unless a separately reviewed admin rebind path applies;
- teamspace placement requires current membership and the required source edit authority;
- permission loss between phases prevents source mutation or catalog finalization;
- existence, hidden-parent, hidden-sibling, subtree size, title, path, filename, raw public token, nonce, and lease token are not exposed in denial payloads;
- operation replay reauthorizes the current actor before returning a result;
- all public reports are identifier-free counters except the authorized target/result locator required by the direct command response.

## 9. Bounds

- request JSON: at most 65,536 bytes;
- one synchronous command subtree: at most 1,000 nodes and depth 256;
- one sibling scope: at most 10,000 nodes;
- operation ID: existing workspace bound;
- one command worker lease at a time;
- source snapshots: at most two bounded exact snapshots per new effect;
- no directory enumeration;
- no source or network I/O while a catalog write transaction is held;
- larger trees require an explicit offline batched command, not a relaxed request bound.

## 10. Read and UI gates

The current default-off navigation UI continues to use compatibility page data. A future canonical tree endpoint must:

- return one consistent `tree_version`;
- omit archived/deleted/invisible nodes without leaking counts or ordinal gaps;
- return dense visible ordinals;
- flatten a hidden or unresolved parent without returning its locator;
- use no legacy source DB for structural state;
- allow exact-owner display hydration only through the existing no-directory-scan boundary;
- include a server capability that defaults off and is subordinate to a kill switch.

Drag, keyboard reorder, create, duplicate, archive, and restore controls stay on legacy routes until their canonical command route is separately contract-tested. Enabling the NP2B UI does not enable NP-2C commands.

## 11. Migration requirements

The 1310→1311 migration must be explicit, forward-only, and offline/quiesced. It must:

- import frozen 1310 SQL and literal hashes, not mutable runtime aliases;
- refuse partial, drifted, future, or unsupported source schemas;
- create an exact backup and verify source/backup path, identity, sidecars, security metadata, and logical fingerprint;
- preserve every existing workspace, binding, attestation, projection, navigation preference, and favorite byte-for-byte in logical content;
- fabricate no complete tree and no lifecycle receipt;
- initialize existing workspaces as `unmaterialized` with zero tree nodes;
- produce fresh and migrated 1311 schemas with identical reviewed objects/hashes;
- converge ambiguous commit/replace outcomes without deleting a live target;
- never run during request handling or startup.

## 12. Verification ladder

The implementation slice must add, in order:

1. machine-contract verifier and negative weakening tests;
2. frozen 1310→1311 migration tests, fresh/migrated equality, backup and fault injection;
3. raw SQL lifecycle/lineage/transition/deep-health tests;
4. tree materialization tests for root/child/order/hidden parent/incomplete source;
5. command tests for exact replay, request conflict, CAS, permission loss, source drift, crash after each phase, and operation/audit/outbox failure;
6. move cycle/cross-workspace/cross-owner/relative sibling/stale-drag tests;
7. duplicate subtree/file/reference/rollback bounds tests;
8. archive/restore identity, subtree, favorites/projection, and editor invalidation tests;
9. permanent-delete disabled tests;
10. NP2A/NP2B migrations, healing, incarnation, navigation, routes, UI, auth, Second Brain, and Shared Knowledge hard regressions.

No phase is promoted to differential-passed without the evidence policy's live reference corpus and reviewer fields.

## 13. Stop conditions

Stop immediately when:

- file ownership or an input hash drifts;
- a protected file or live data path is required;
- frozen 1310 SQL/hash changes;
- a source schema version conflicts with the reserved H5e conditional-ack lineage;
- an unbounded source scan, directory scan, or catalog writer I/O is required;
- a command cannot distinguish pre-state, exact post-state, and ambiguous state;
- a hidden locator/count/title/path/token could leak;
- operation, audit, and outbox cannot be committed atomically with catalog finalization;
- destructive purge, continuous scheduling, resolver cutover, feature enablement, or live migration becomes necessary;
- route, auth, Second Brain, or Shared Knowledge regression fails.

## 14. Implementation sequence

1. NP2C-0: this design and strict machine contract.
2. NP2C-1: frozen 1311 tree state/node/command schema and read-only deep-health/readiness; all workspaces start unmaterialized.
3. NP2C-2a: offline exact-source initial tree materializer with full replay/event commitment; implemented and contract-tested with temporary sources/catalogs only.
4. NP2C-2b: replay-safe lifecycle command kernel with fake/source-temp effects; no route.
5. NP2C-3a: freeze the rename source request/receipt and catalog finalization contract; implemented as design only.
6. NP2C-3b: add the v3-ledger-backed rename source adapter against temporary exact owner shards only; implemented and contract-tested, with no runtime registration.
7. NP2C-3c: add a frozen 1312→1313 catalog guard/finalizer for atomic proof, attestation, locator, and tree-node refresh.
8. NP2C-3d: prove source-response-loss and catalog-finalize-response-loss convergence in an offline rehearsal.
9. NP2C-3e: freeze the create/move/reorder source-receipt and candidate-1314 catalog contracts, then implement only the route-unregistered legacy-v3 source adapter as NP2C-3e1.
10. NP2C-4a: duplicate/Trash/restore terminology, identity, file, cohort, placement, replay and candidate-1316 design.
11. NP2C-4b: route-unregistered same-owner duplicate and Trash/restore source-v3 adapters in temporary shards.
12. NP2C-4c: separately reviewed frozen 1315→1316 lifecycle subtree schema/guards.
13. NP2C-4d: atomic catalog finalizers and exact-sentinel rehearsal.
14. NP2C-4e: source-ledger-first recovery plus independent rollback, exact-bound, redaction and deep-health audit.
15. NP2C-5: official Archive/Trash separation, exact source-v4→candidate-v5/workspace-1318 lineage and unknown live-semantics design; completed without implementation.
16. NP2C-5a: approved Business/Enterprise live characterization; this is the current next gate.
17. NP2C-5b/5c/5d/5e: only after characterization, separately review source-v5, workspace-1318 finalization, exact-sentinel rehearsal and independent audit.
18. NP2C-6: protected routes/default-off UI only after the preceding contract gates.
19. NP2C-7: retention/purge design and approval; purge remains disabled until then.
20. NP-2D capability resolver and later resolver cutover are separate gates.

### 14.1 NP2C-2b schema gate implemented as frozen 1312

The frozen 1311 command table is intentionally not service-ready. Its transition trigger did not prove exact lease duration/expiry, claim versus reclaim fencing increments, live-lease requirements for `source_applied` and terminal transitions, or mutual exclusion between a prepared command's `(actor_subject_id, operation_id)` and `workspace_operations`. A Python-only implementation would have left a weaker raw/service-context invariant than the rest of the substrate.

1311 remains byte-for-byte frozen. The reviewed 1312 forward migration replaces the lifecycle transition trigger, bounds leases to 300 seconds, distinguishes claim/reclaim from same-token heartbeat fencing, requires a live lease for source-applied and terminal transitions, and adds two-way operation namespace guards. Deep health mirrors these constraints. Because 1311 had no authorized command service, the migration rejects every pre-kernel lifecycle command instead of blessing raw rows. Only now may prepare/claim/record-source/finalize APIs and fake source effects be added. This does not change the runtime-disabled status and authorizes no live migration. Frozen 1312 checksum is `sha256:effcfb238406228b79ad780e39c0577de4ac851f881af520c92b627b98b529f6`; actual schema hash is `sha256:49e388386f2a90ea23fe8861554c50274b127de2ad7aa171e78bb4e9ded074e1` with 142 objects.

### 14.2 NP2C-2b disabled lifecycle kernel implemented

The 1312 store now exposes an internal, route-unregistered kernel for the `rename` protocol shape only: exact prepare replay, lease claim/reclaim/write-free same-token acknowledgement, post-source receipt recording, source receipt revalidation, atomic finalize, deterministic failure, and manual-audit termination. Finalization reauthorizes the current workspace admin and rechecks the materialized tree version, active binding generation, source incarnation generation, live lease/fence, and exact stored source receipt. The finalized operation, audit row, outbox row, command row, and canonical identifier-free payload commit in one catalog transaction. Deep health and every direct replay path validate the full immutable commitment.

`notion_workspace_page_lifecycle.py` is a disabled orchestration harness around an injected replay-safe source effect. Exact finalized or terminal replay is resolved before quiescence and before source callbacks. Every new effect requires explicit source quiescence. A fake temporary source ledger proves source-commit/response-loss convergence, while a catalog outbox fault proves that retry finalizes without applying the source effect twice. Lease expiry during either callback, catalog generation drift, malformed ledger data, permanent source refusal, and ambiguous source outcome are fail-closed and transaction-tested.

This slice does **not** rename a real legacy page. It carries no title, source revision/change-generation pre-state, or production source path; it does not update the catalog tree node, binding proof, or locator projection after a content change. Those are NP2C-3 adapter responsibilities and may require a separately frozen forward schema/receipt contract. No protected route, startup import, worker, feature flag, resolver, live migration, watermark acknowledgement, or purge behavior is enabled.

### 14.3 NP2C-3a rename source adapter and receipt contract frozen

NP2C-3a is a design-only gate. It authorizes no source write and no catalog migration. Its purpose is to remove three ambiguities before implementation: which source ledger is replay authority, which exact pre/post facts distinguish success from an unknown outcome, and which catalog objects must change atomically after the source commit.

#### Source schema decision

The legacy atomic-save ledger is **not** the lifecycle replay authority. `page_save_operations` stores a full page result including blocks, is deleted by `ON DELETE CASCADE`, has no immutable UPDATE/DELETE guards, is not part of the frozen legacy change-tracking object contract, and is absent on legacy shards that never received the optional atomic-save migration. Reusing it would make rename recovery depend on a mutable, content-bearing record.

No new legacy source schema is needed for the first rename adapter. Frozen source v3 `2026081303` already contains `notion_legacy_change_consumer_operations`, whose rows are service-context-gated and immutable. NP2C reserves the fixed namespace `consumer='np2c.lifecycle'`, action `legacy.page_lifecycle.rename`, and result contract `np2c_legacy_page_rename_receipt_v1`. A new side-effect-free lifecycle contract must authenticate the complete v1→v2→v3 source chain first and then strictly validate only this action's canonical request/result. It must not weaken or rewrite the frozen v3 inspector.

This choice leaves H5e source v4 `2026081304` reserved for conditional watermark acknowledgement. NP2C must stop if implementation would require changing v3 DDL or taking that version number.

The optional atomic-save **revision capability** is still required for rename because the command contract requires a monotonic page revision. That is a capability precondition, not the replay ledger. A source lacking `pages.revision` is refused without mutation and must receive its already-existing explicit atomic-save migration before any lifecycle rehearsal.

#### Canonical source request and privacy boundary

The source mutation actor and the catalog proof actor are separate principals. The initiating actor must be a current workspace admin and must independently hold current legacy edit permission; the existing binding's proof actor remains the actor used to capture the post-mutation proof. Historical `bound_by_subject_id` is provenance, not automatic mutation authority.

The normalized title follows the current legacy rule: trim/collapse whitespace, default to `Untitled`, and cap at 160 characters. Raw title is permitted only in the caller request and the legacy source transaction. It is forbidden from catalog command rows, workspace operation results, audit/outbox payloads, checkpoints, public reports, and logs. The source operation request SHA-256 commits the normalized title and exact pre-state. The catalog command request commits only that opaque source request digest, not title bytes.

The exact pre-state is:

- source file device, inode, and link count;
- source epoch and full-sweep generation;
- exact owner/page identity;
- active, non-retired incarnation ID and generation;
- page revision and updated timestamp;
- watermark generation, mask, physical-exists, and active state;
- canonical incarnation receipt SHA-256.

The source request also carries the catalog command ID and caller operation ID in its domain-separated digest. Reusing the same source operation ID with any different title or pre-state is a deterministic conflict, not a retry.

#### Source transaction and post-state recognition

A new effect performs one full authenticated v3 integrity gate, then opens the exact owner shard for a bounded write. The writer rechecks source identity, active page/incarnation, current edit role, page lock, revision, watermark, and complete pre-state under `BEGIN IMMEDIATE`. Page/version snapshot row and byte bounds are checked before materializing a page-version snapshot.

For a changed title, one transaction updates the page title/timestamp, increments revision once, records the legacy page version, lets frozen v1 triggers increment page watermark, source epoch, and full-sweep generation once, validates the resulting row, and inserts the immutable lifecycle operation result. The post-state must have the same source file identity and incarnation, revision `pre+1`, watermark generation `pre+1`, change mask `pre|CHANGE_PAGE`, source epoch `pre+1`, full-sweep generation `pre+1`, and active/existing state. Any other result rolls back.

For an exact normalized-title no-op, page, revision, watermark, epoch, and full-sweep generation remain byte-for-byte unchanged. The immutable source operation is still inserted with `changed=false`; this is the durable response-loss receipt. It prevents an acknowledged no-op from being reinterpreted after a later unrelated source mutation.

The operation result contains identifiers and source generations only inside the owner shard, never title/content/path/token bytes. Its domain-separated SHA-256 is the only source-effect receipt stored in the catalog command. On response loss, the source adapter probes and validates this exact immutable row before reading the page or attempting a write. A committed row with matching request returns the prior result; mismatched request conflicts; a state that matches neither exact pre-state nor the committed result is `manual_audit`.

#### Catalog 1313 decision

Changed rename cannot be finalized safely on frozen 1312. The current `workspace_page_sources` active→active proof transition is authorized only by a live locator-healing run. Reusing that context for a lifecycle command would forge the wrong authority. A frozen 1312→1313 migration is therefore required before catalog integration. It changes no source schema and must refuse any existing lifecycle command row because 1312 commands do not carry the real adapter contract.

1313 must add a lifecycle-specific, fail-closed service context and a narrowly constrained active→active binding refresh branch. It must not broaden ordinary binding or healing transitions. The finalize implementation revalidates the exact source candidate outside the catalog writer, then in one `BEGIN IMMEDIATE` transaction:

1. reauthorizes the current workspace admin and live command lease/fence/version;
2. rechecks the unchanged tree version, target binding generation, incarnation, and source-operation receipt;
3. deletes the old generation locator projection before updating its parent binding;
4. updates the active binding proof and binding generation exactly once;
5. inserts the complete summary/file/public locator projection for the new generation;
6. inserts a current incarnation attestation correlated to the same lifecycle operation;
7. refreshes the target tree node's binding generation, source change generation, receipt, and structure commitment;
8. updates tree-state evidence and bumps its row version while keeping `tree_version` unchanged for rename;
9. bumps workspace row version/security epoch exactly once for a changed effect;
10. finalizes the command and commits one canonical raw-title-free operation/audit/outbox trio.

Every injected failure restores the old binding, attestation, locator projection, tree node/state, workspace versions, command phase, and ledger trio. An exact no-op skips binding/projection/tree/workspace changes and only finalizes the command ledger.

#### Reference dependency and full-sweep obligation

The current page proof hydrates linked-page title/icon/archive metadata. Renaming one page can therefore change proofs of other pages without incrementing those pages' revisions or individual watermarks. Frozen source v1 intentionally increments `full_sweep_generation` for every page metadata update; NP2C must preserve that signal.

A changed rename may refresh only its exact target in the synchronous catalog transaction. It must set the workspace tree materialization state to `partial`, record an identifier-free `full_sweep_required=true` commitment, and keep resolver cutover unavailable until an exact owner-wide no-ack reconciliation proves the new full-sweep generation and republishes all affected bindings/tree evidence. It must not acknowledge or delete a watermark. A no-op does not create this obligation.

#### Implementation and stop gates

NP2C-3b implements only a new route-unregistered source contract/adapter with temporary-shard tests. It does not modify `notion_store.py`: the new modules reproduce only the bounded permission, lock, revision, and page-version semantics needed inside the single source transaction and deliberately avoid importing app/auth/config/data-path code. NP2C-3b stops before catalog DDL or integration.

NP2C-3c separately freezes 1313 and the atomic catalog finalizer. NP2C-3d then runs a sentinel-bound offline rehearsal proving source commit/response loss, catalog finalize rollback, exact replay, no-op, title redaction, and full-sweep debt. Routes, UI, runtime workers, resolver cutover, H5e acknowledgement, purge, live migration, and live data access remain prohibited.

### 14.4 NP2C-3b legacy-v3 rename source adapter implemented

`notion_legacy_page_lifecycle_contract.py` now owns the strict source-only envelope. It fixes the `np2c.lifecycle` consumer/action namespace, derives the source operation ID from catalog command and caller operation IDs, commits the normalized title and complete expected pre-state only through a domain-separated SHA-256, and validates an exact raw-title-free response-loss receipt. The persisted field names remain the frozen cross-database names `source_owner_subject_id` and `source_page_id`; the existing legacy incarnation digest receives those values through its source-side owner/page mapping.

`notion_legacy_page_lifecycle.py` is a caller-owned-connection adapter. Construction requires one idle, exact file-backed SQLite connection and performs one full frozen v1→v2→v3 integrity inspection. A new effect requires explicit source quiescence, but an exact immutable operation replay is probed first and returns without page, permission, block, or title reads. Reusing the deterministic operation ID with a changed title, actor, command, caller operation, or pre-state is a conflict.

Inside one `BEGIN IMMEDIATE`, the adapter rechecks file identity, active owner/page and incarnation, atomic revision capability, current legacy edit role, page lock, watermark, and every expected pre-state field. A changed rename uses one `UPDATE pages` statement that changes title/timestamp and increments revision together. This is intentional: the frozen v1 trigger observes one page update and therefore increments the page watermark, source epoch, and full-sweep generation exactly once. It then writes a bounded compatible page-version snapshot and the immutable lifecycle operation row before commit. The page-version boundary is 10,000 blocks, 16 MiB of UTF-8 material, and 256 permission-ancestry hops. An exact normalized-title no-op writes only the immutable `changed=false` operation receipt.

Focused temporary-shard evidence covers changed rename, exact generation deltas, page-version creation, post-commit response loss, replay before page read and before quiescence, operation-ID conflict, no-op state preservation, rollback after the page-version write, permission and lock refusal, stale pre-state, missing revision capability, bounded snapshot refusal, malformed ledger refusal, title redaction, and import/FS isolation. The current focused result is `6 passed`; adjacent v3 consumer, v3 migration, v2 incarnation migration, and disabled lifecycle kernel lanes also pass. No real user shard, catalog 1313, protected route, feature flag, worker, resolver, watermark acknowledgement, or purge path was touched.

### 14.5 NP2C-3c frozen 1313 atomic catalog finalizer implemented

Workspace runtime `2026081313` adds only the lifecycle-specific catalog authority required by the frozen rename protocol. Frozen 1312 remains independently importable. The exact 1312→1313 migrator refuses any pre-finalizer lifecycle command row and installs fail-closed command/receipt service-context functions, command workspace/security/tree-state CAS columns, a lifecycle-only active binding proof refresh branch, and a v2 operation result namespace. Unconfigured raw writers and the legacy v1 finalizer cannot exercise this authority.

`WorkspaceStore.finalize_page_lifecycle_rename_catalog` resolves exact replay before the external revalidator. A changed effect deletes the old projection, advances the active binding generation with fresh owner-canonical proof, inserts the matching incarnation attestation and complete summary/file/public projection, updates the target tree node, marks the tree `partial`, increments workspace row/security versions exactly once, and finalizes command/operation/audit/outbox in one transaction. A no-op finalizes only the immutable command/ledger commitment. Catalog write faults roll back the whole effect, lease or workspace/tree CAS drift is write-free, and stored result/audit/outbox omit the raw title. Deep health recomputes the active tree-node structure commitment and validates the historical finalizer commitment without requiring mutable later workspace state to remain unchanged.

Frozen 1313 checksum is `sha256:6be5b47275cf0dbe0b6b244c3f71672662f8ce648d1efa446208a94488da1637`; actual schema hash is `sha256:e971256344e262c59990901b3a6816ecac7be760a31c286f68c5e03d4d491595` with 144 objects. Focused temporary-catalog evidence covers six finalizer cases plus frozen migration, raw SQL and legacy-v1 rejection. No live catalog or source shard was used.

NP2C-3d is now complete under the exact-sentinel, explicitly quiesced offline boundary. The source adapter and finalizer remain route-unregistered.

### 14.6 NP2C-3d exact-sentinel end-to-end rename rehearsal implemented

`notion_workspace_page_lifecycle_rehearsal.py` combines only the frozen legacy-v3 rename adapter and workspace-1313 catalog finalizer. A new effect requires source quiescence, the exact rehearsal sentinel, root-contained source/catalog paths, current catalog admin authority, an active owner-canonical source proof, a current incarnation attestation and a complete tree prestate. Source v3 must be installed before catalog attestation captures the source file identity.

The orchestration preserves two independent immutable replay authorities. A source commit followed by response loss is recognized from the source operation ledger and does not create a second page version or generation bump. A catalog finalize commit followed by response loss is recognized from the catalog operation commitment before quiescence, sentinel, filesystem, or source access; the requested title and original source prestate are still recomputed into the exact source request/receipt locally, so a changed title under the same operation ID conflicts. If catalog finalization rolls back after the source commit, the command remains `source_applied` and the same operation later converges without a second source mutation.

Temporary sentinel evidence covers five focused cases: changed convergence and source-free catalog replay, exact-title no-op, source response loss, catalog response loss plus catalog outbox rollback, and quiescence/import isolation. Changed rename advances source revision/watermark/source epoch/full-sweep and catalog binding/workspace/security exactly once, refreshes the target projection/node, and leaves the tree `partial`; no-op preserves domain versions and writes only the two immutable receipts. The aggregate report contains no raw title, page/owner ID, path, digest, token, or lease secret. Existing source adapter/kernel/finalizer tests pass 19, migration/tree tests pass 43, and locator/projection/healing/outbox tests pass 90 on the current tree.

NP2C-3e begins with the design below. It treats create, move, and reorder as separate bounded source receipt/catalog-finalizer contracts and does not generalize rename by assumption. Duplicate/archive/restore remain NP2C-4. Runtime routes, UI, continuous worker, resolver cutover, H5e acknowledgement, purge, live migration, and live data access remain prohibited.

### 14.7 NP2C-3e create, move, and reorder contracts; 3e1 source adapters implemented

NP2C-3e began as a design-only gate. The isolated `NP2C-3e1` source slice added route-unregistered structural source contracts/adapters; subsequent `NP2C-3e2` machine contract v12 added workspace 1314 structural guards and an explicit offline migrator. `NP2C-3e3` machine contract v13 adds the separately frozen 1315 finalization authority, atomic catalog finalizer, and exact-sentinel orchestration without adding a source schema or route. The observed legacy behavior is more expansive than the rename protocol and is not represented by widening the rename allowlist.

#### Observed compatibility baseline

Legacy `create_page` normalizes the title, resolves an optional parent with current edit permission, rejects a locked parent, allocates a page ID before the source transaction, appends the page after the active sibling maximum, inserts blocks and a page-version snapshot, and optionally rewrites the complete parent block list to add one child-page reference. The ordinary route defaults `add_to_parent_blocks` to false. Root creation uses the initiating user's shard; child creation uses the exact parent owner's shard. Outlook and other internal callers also call this public mutator directly, so a route-only wrapper would not be complete.

Legacy `move_page` is owner-only. `before_id` and `after_id` are mutually exclusive; a relative target determines the resulting parent. The write transaction rejects self-parenting, cycles, missing targets, and locked old/new parents. It orders destination siblings by `(position, updated_at DESC)`, inserts the target at the requested slot or the end, rewrites every destination sibling to a dense positive position, changes the target parent, rewrites old/new parent child-page blocks when applicable, and bumps revisions for the target, every destination sibling, and any parent whose block list changed. Moving away does not renumber the old source positions, so a catalog snapshot must derive a dense rank for both old and new scopes rather than copying numeric gaps.

The same endpoint is classified as `move` only when the resulting parent differs from the current parent. It is `reorder` when the resulting parent is unchanged, including an append-to-end request. The compatibility implementation still performs source page/revision/watermark writes when the requested structural order is already equal; an adapter must not silently optimize that behavior before differential characterization. Its receipt therefore records `structure_changed` separately from `source_changed`.

Frozen v1 triggers make simple delta assumptions unsafe. A create increments the new page watermark/epoch for the page insert, every block insert, and the page update performed by block replacement. A move may trigger the target page update, subtree parent-change watermarking, destination position rewrites, parent block delete/insert work, and the final revision bumps. The exact post-state must be captured from the committed transaction; it must not be inferred from a fixed `+1` rule.

#### Legacy source contract: reuse v3, preserve the v4 reservation

No new legacy schema is required. The authenticated v3 `notion_legacy_change_consumer_operations` table remains the immutable source replay authority under `consumer='np2c.lifecycle'`, with three fixed actions and result contracts:

- `legacy.page_lifecycle.create` / `np2c_legacy_page_create_receipt_v1`;
- `legacy.page_lifecycle.move` / `np2c_legacy_page_move_receipt_v1`;
- `legacy.page_lifecycle.reorder` / `np2c_legacy_page_reorder_receipt_v1`.

This is an action-specific use of the generic immutable ledger, not a change to its frozen DDL. H5e source version `2026081304` remains reserved for conditional watermark acknowledgement. If any source-side table, trigger, or v3 object must change, NP2C-3e implementation stops for a new lineage review instead of taking v4.

Every action uses a domain-separated deterministic source operation ID, probes its exact immutable replay before page/path/quiescence work, and rejects reuse with a changed request digest. The source request commits the complete canonical pre-state and all user content only by digest. Source operation results may contain exact owner/page identities and generations inside the owner shard, but no raw title, markdown, block content, settings, icon, cover, path, filename, public token, or lease token. Catalog commands, audit, outbox, checkpoints, and aggregate reports retain only canonical digests, bounded counts, and the authorized response locator.

Create derives a proposed `p_` plus 32-lowercase-hex page ID from a domain-separated digest of the workspace, exact source owner, actor, and caller operation ID before catalog prepare. The proposed ID is part of the catalog request and source request. An extant page, incarnation-history collision, or operation with a different proposed ID is a conflict; random retry or ID substitution is forbidden. The source transaction rechecks parent authority/lock and the exact sibling tail, inserts the proposed page, lets the configured v2 incarnation trigger allocate the new nonce/generation, inserts bounded blocks/version state, optionally rewrites a bounded parent block list, and inserts the immutable v3 operation result in the same commit. Response-loss replay returns the prior created identity and exact post-state without a second insert.

Move and reorder require the current source owner as mutation actor until NP-2D establishes a separately reviewed capability. Their request digests commit the current target/incarnation, old and resulting parent locators, relative sibling locator/side, current subtree membership, ordered old/new sibling scope digests, parent block digests, source file identity/fence, and catalog tree/binding preconditions. The adapter performs a pre-write bounded scope audit, applies the compatibility transaction, then captures an exact canonical post-manifest before inserting the source operation result. The post-manifest commits:

- target pre/post parent and dense derived ordinal;
- ordered old and new sibling scopes after the source commit;
- the complete moved subtree and derived post-move depths;
- every page whose page row, proof, watermark, or parent block list changed;
- exact per-page incarnation, watermark receipt, revision/timestamp, source proof, and locator digests;
- exact source epoch/full-sweep/file-identity fence;
- `source_changed`, `structure_changed`, affected-page/subtree/scope counts, and one canonical manifest SHA-256.

The immutable source operation need not duplicate the complete manifest JSON. It commits its canonical digest and bounded summary; the source candidate is recaptured from the quiesced exact shard and must match that digest before catalog finalization. If a later source write makes the committed post-manifest unreconstructable, the command is `manual_audit`, not guessed success.

#### Bounded work and pre-write refusal

All bounds are checked before the source transaction mutates a page:

- moved subtree: at most 1,000 nodes and depth 256;
- each old/new sibling scope: at most 10,000 active nodes;
- union of target, subtree, both sibling scopes, and old/new parents: at most 21,002 exact page receipts;
- page proof: existing 10,000-block, 5,000-file, and 16 MiB per-page bounds;
- parent child-reference rewrite: at most 5,000 blocks per parent;
- aggregate materialized source bytes for one structural command: at most 256 MiB;
- canonical source operation result JSON: at most 16 MiB;
- one full v1/v2/v3 integrity gate per exact owner and at most two exact source snapshots for a new effect;
- zero directory scans and zero source/network I/O while a catalog writer transaction is held.

A request exceeding any bound refuses without a source write. The runtime route remains on the legacy compatibility path until an offline/batched policy exists; the bounds are not relaxed to claim parity.

#### NP2C-3e1 temporary-shard source evidence

The source adapter accepts one caller-owned, exact file-backed v3 connection. Its constructor authenticates the complete v1/v2/v3 contract once, requires the configured v2 incarnation UDFs and atomic page revision/version capability, rejects WAL/sidecars and multi-link source files, and installs only the connection-scoped v3 operation context. New effects require explicit quiescence; exact immutable operation replay is the first source query and is allowed before a renewed quiescence assertion.

Create uses the replay-derived proposed ID, rechecks absence in both `pages` and incarnation history, revalidates parent edit/lock state and the complete bounded pre-manifest inside `BEGIN IMMEDIATE`, then commits page, blocks, page version, optional parent child-reference rewrite, fresh incarnation/watermark evidence, and the canonical v3 receipt together. Move/reorder revalidates the exact target incarnation, source fence, subtree, old/new sibling scopes and parent commitments before the first write; applies owner-only cycle/lock/relative-sibling rules; preserves the compatibility distinction between source change and equal resulting structure; and commits the exact post-manifest receipt in the same transaction. No fixed generation delta is inferred.

Six focused temporary-shard tests cover deterministic create and secret-free response-loss replay before page read, rollback/quiescence/root authority, cross-parent dense move and child-reference parity, same-parent reorder plus structure-equal append receipt, stale/cycle/corrupt-ledger refusal, and import/filesystem isolation. Adjacent frozen rename/lifecycle, v3 consumer migration, and v2 incarnation suites pass 24. No real shard, workspace catalog, route, flag, worker, resolver, watermark acknowledgement, or live migration was accessed.

#### Why frozen 1313 is insufficient

Frozen 1313 accepts only `rename` in `_normalize_page_lifecycle_request`, requires an already-active target with binding/incarnation versions at least one, stores only one aggregate receipt capped at 1,000 nodes, and authorizes only a single-target rename proof refresh. It cannot represent a not-yet-existing create target or the per-page evidence for two 10,000-node sibling scopes.

There is also a database-level ordering obstacle. `workspace_page_tree_nodes` has immediate unique indexes on positive sibling ordinals and no spare value when a 10,000-node scope is full. A same-scope permutation cannot safely swap ordinals through the existing schema without transient uniqueness failure. Dropping the index, deleting immutable nodes, using `OR REPLACE`, or relying on Python update order is forbidden.

Therefore a separate frozen workspace `2026081314` contract is required before catalog finalizer implementation. NP2C-3e2 now implements that guard contract while the 1313 SQL, checksum, actual-schema hash, rename finalizer, and migration remain independently importable and byte-for-byte frozen.

#### Frozen 1314 minimum schema and service authority

The reviewed 1314 implementation adds, without fabricating source success:

1. immutable action-specific command scope/effect evidence keyed by command and exact page locator, capable of storing old/new parent roles, old/new sibling scope order, subtree membership, pre/post binding/incarnation/change generations, proof/locator commitments, and the source manifest digest;
2. an order-generation field on tree nodes and rebuilt unique sibling indexes that include that generation, so all rows in one affected scope can move from the old generation to the new tree generation without transient ordinal collision; committed deep health requires one generation and dense `1..N` ordinals per active scope;
3. exact action-shape triggers for create, move, and reorder, including create's expected binding/incarnation version zero and move/reorder's existing active target;
4. lifecycle-only service contexts for one create binding/attestation/projection publication and bounded multi-page active proof refresh; healing authority and ordinary bind APIs are not reused;
5. a structural finalization result namespace distinct from the rename v2 result, with complete command/effect/operation/audit/outbox correlation;
6. deep-health checks for scope cardinality, role uniqueness, manifest equality, source receipts, order generation, dense ordinals, binding/attestation/projection evidence, and exact canonical ledger payloads.

The 1313→1314 migrator is explicit, offline, quiesced, backup-verified, and forward-only. It preserves every finalized rename command and all existing tree/binding/projection/history rows. It refuses any live `prepared` or `source_applied` lifecycle command, partial/drifted/future schema, and any tree that is not healthy before migration. Existing nodes receive their current scope order generation without changing logical order. No create/move/reorder command or source receipt is backfilled.

#### Atomic catalog finalization

Create finalization reauthorizes the current catalog admin, revalidates the source candidate outside the writer, and then in one `BEGIN IMMEDIATE` rechecks command lease/fence, workspace/tree versions, exact parent/sibling scope, proposed identity absence, and source receipt. It creates the active binding at generation one through lifecycle-only authority, inserts incarnation attestation and complete projection, inserts the tree node at the source-derived dense ordinal, refreshes the optional changed parent binding/projection/node, advances the affected order generation, bumps tree/workspace/security versions once, and commits command/effect rows plus one operation/audit/outbox commitment. No visible catalog node may exist without the exact source page.

Move/reorder finalization rechecks all affected catalog rows and the exact source post-manifest. It deletes old locator projections before any binding-generation update, refreshes every source-mutated binding/attestation/projection, updates moved-subtree parent/depth evidence, rewrites both affected sibling scopes using the new order generation, and advances tree version exactly once when `structure_changed=true`. If the compatibility source effect changed proofs but the final structure is identical, it refreshes the affected proofs and evidence without incrementing tree version. Workspace row/security epoch increase exactly once for a changed source effect. Any failure rolls back all bindings, projections, attestations, nodes, order generations, state/version counters, command effects, and the ledger trio.

Because page metadata, positions, revisions, and parent block rewrites advance the frozen full-sweep fence and linked-page hydration can change other proofs, every changed create/move/reorder finalization leaves the tree `partial` with `full_sweep_required=true`. Resolver cutover stays unavailable until the existing exact owner-wide no-ack reconciliation proves the post-effect full-sweep generation. The command never acknowledges or deletes a watermark.

#### Replay, fault, and security matrix

Required implementation evidence includes:

- create ID collision, parent permission/lock loss, block/version bound refusal, source operation insert rollback, source commit response loss, exact replay before source read, and catalog insert/attestation/projection/tree/ledger rollback;
- move/reorder action-class mismatch, before+after, wrong-parent relative sibling, cycle, cross-owner/workspace, source scope drift, subtree/depth/sibling/aggregate-byte bounds, full 10,000-sibling permutation, and exact old/new dense catalog order;
- source commit followed by catalog crash, catalog CAS loss, lease expiry during revalidation, per-page binding/incarnation drift, response loss after catalog commit, and malformed source/catalog ledger commitment;
- every operation/audit/outbox/fault point restoring the pre-finalize catalog byte-for-byte in logical state;
- denial/report redaction for owner/page counts outside authorized results, title/content/settings/path/filename/public token/incarnation nonce/digest/lease secret;
- current route/auth/Outlook/Second Brain/NP2A/NP2B/rename/migration-chain regressions and import isolation.

`NP2C-3e1` action-specific legacy-v3 source contracts/adapters and `NP2C-3e2` frozen 1313→1314 migration plus raw-SQL/deep-health guards are complete under temporary-shard/catalog evidence. Frozen 1314 checksum is `sha256:c131d83534326ab1dfa8bf4d007412e1fb9e08c628c04e36de55287b79835b82`; actual schema hash is `sha256:2aad855cd6b9775850706f3c23c6ae3a494b908b4bce188791c0533a0e0acf14` with 159 objects. The migration refuses live commands and invalid materialized trees, preserves 1313 history, and fabricates no source receipt or command. Legacy-v1 orchestration is disabled before source/catalog I/O on 1313+.

### 14.8 NP2C-3e3 atomic structural finalizer and exact-sentinel rehearsal implemented

Workspace `2026081315` preserves frozen 1314 and replaces only the reviewed structural-finalization authority objects. It makes root parent equality null-safe, authorizes complete old/new sibling and subtree effects, and keeps structurally unchanged source-mutated parents on their existing order generation. Frozen 1315 checksum is `sha256:a57f73b41a834c42edcdfbacbbf2aa1fc30dd34366d98d7c40a42337b4f0e932`; actual schema hash is `sha256:33eaed5b16671104d53443c7cd4b09b3471eabbce805161608a114e4528f5d82` with 159 objects. The explicit 1314→1315 migrator refuses live lifecycle commands and fabricates no source receipt, effect, node, or operation.

`notion_legacy_page_structural.py` uses the frozen source-v3 operation ledger and no new source DDL. A new create/move/reorder effect requires explicit quiescence and an exact caller-owned file-backed source. Multiple internal page/block statements are coalesced inside the same source transaction to one logical watermark generation per affected page; intermediate generations are never externally visible. The immutable receipt commits the exact post-manifest, source fence, complete bounded effect set, source-changing roles and source operation digest. Response-loss replay is checked before renewed quiescence and performs no second source mutation.

`WorkspaceStore.finalize_page_lifecycle_structural_catalog` reauthorizes the current admin, checks the command lease/fence and all workspace/tree/binding/incarnation/source fences before and after external source revalidation, and commits the manifest/effects, binding/projection/attestation/tree changes, workspace/security/tree versions, command, operation, audit and outbox in one catalog transaction. `notion_workspace_page_structural_rehearsal.py` composes that finalizer with the source adapter only beneath an exact rehearsal sentinel. A finalized catalog replay is validated before sentinel, path or source access and returns only identifier-free counters.

Current temporary-fixture evidence includes create, reorder and cross-parent move convergence; page-level generation coalescing; source and catalog replay; late rollback; lease-expiry refusal before source callback; root null-parent authority; import/filesystem isolation; and the full frozen workspace migration chain through 1315. NP2C-4b additionally covers replay-derived duplicate identities, exact file staging/recovery, source-byte and path drift refusal, exact Trash cohort, same-incarnation restore, reviewed fallback placement, response-loss replay, and rollback. This is offline/quiesced contract evidence, not a live or differential pass. Runtime route/UI/worker registration, resolver cutover, live migration, destructive watermark acknowledgement and purge remain unauthorized.

### 14.9 NP2C-4a duplicate/Trash/restore design frozen

The detailed contract is [NP2C_DUPLICATE_TRASH_RESTORE_DESIGN.md](NP2C_DUPLICATE_TRASH_RESTORE_DESIGN.md). It corrects a compatibility naming error: the existing HTTP DELETE route calls `archive_page` and lists the result in `/trash`, while the current official Archive feature is distinct from Delete/Recycle Bin. Future canonical actions therefore use `trash` and `restore`; `archive` and `unarchive` remain a separate source-lifecycle gate.

The design keeps same-owner duplicate and Trash/restore on the generic frozen v3 source operation ledger, with distinct immutable action/result namespaces. Duplicate uses domain-separated deterministic page/block/file mappings and an exact contained, exclusive, hash-verified file staging/publish protocol before the source SQLite commit. Trash records only the exact active transition cohort. Restore references that cohort, requires the same incarnation, never silently restores ancestors, and requires either a still-valid original placement or an explicit reviewed fallback.

Frozen 1315 cannot represent multi-page duplicate creation, active→trashed/trashed→active effects, cohort/original-placement evidence, or a distinct Archive/Trash tree state. Candidate workspace 1316 was therefore required by the design audit; NP2C-4c implements the separately frozen schema/guard slice while preserving frozen 1315 byte-for-byte. At that historical 4c checkpoint catalog finalization and rehearsal remained separate; NP2C-4d subsequently implemented them on frozen 1317.

NP2C-4b temporary-shard source adapters and file recovery, the separately authorized 4c DDL audit plus frozen 1315→1316 schema/guards, and the 4d frozen 1316→1317 atomic finalizer/exact-sentinel rehearsal are contract-tested as independent checkpoints. Public duplicate stays NP2E; official Archive/unarchive waits for a source-lineage decision that does not consume H5e-reserved source version 1304; purge waits for retention/legal/backup approval. Runtime routes/UI/workers, resolver cutover, live migration, watermark acknowledgement and live data access remain unauthorized.

### 14.10 NP2C-4b source-v3 lifecycle adapters

The new route-unregistered source contract/adapter pair reuses the authenticated v3 operation table with the `np2c.lifecycle` consumer. Duplicate, Trash, and restore have distinct canonical request/result contracts. New effects require exact owner authority and quiescence; exact replay precedes renewed quiescence and page/path/file access. Duplicate derives page/block/file/version identities, copies only a bounded same-owner active subtree, refuses external references, and converges file bytes through contained exclusive staging and no-overwrite publication. Trash records only nodes actually transitioned from active. Restore binds the exact prior Trash receipt and same incarnation, never restores ancestors implicitly, and requires original or reviewed fallback placement.

Eighteen focused adversarial source-adapter cases plus the adjacent legacy-v3 regression lanes are green. That historical 4b checkpoint added no workspace schema or catalog finalizer. NP2C-4c subsequently added only the frozen 1316 guard schema/migration described below, and NP2C-4d later added the separately frozen 1317 finalizer/rehearsal. Route, UI, worker, flag, live migration, live data, destructive acknowledgement, official Archive, public duplicate and purge remain absent.

### 14.11 NP2C-4c frozen 1316 lifecycle-subtree guards implemented

Workspace `2026081316` preserves frozen 1315 and adds canonical lifecycle metadata plus immutable bounded subtree evidence. The explicit 1315→1316 migrator/CLI is offline and dry-run by default; it refuses live prepared/source-applied commands, any historical duplicate/Trash/restore command, and ambiguous non-active tree history. It fabricates no subtree manifest, effect, mapping, source receipt, command, operation, or successful lifecycle state.

The additive schema records canonical command actions `duplicate|trash|restore` without rewriting the frozen base `archive` compatibility action. It records canonical tree states `active|archived|trashed|deleted`, immutable command-scoped subtree manifests/effects and object mappings, bounded page and aggregate file commitments, and fail-closed configured command/source-receipt/source-generation contexts. Raw SQL cannot create a duplicate pending binding or perform Trash/restore binding/tree transitions without exact immutable command evidence, lease/fence and source receipt correlation. Deep health mirrors operation/audit/outbox/result and subtree evidence closure.

Frozen 1316 checksum is `sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c`; actual schema hash is `sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981` with 179 objects. Focused evidence is nine migration/raw-guard cases, with adjacent 1314/1315/1316 migrations and page-tree/locator/outbox regression lanes passing. This is the historical offline `contract-tested` 4c guard checkpoint; the 4d finalizer/rehearsal is the separate 1317 section below.

### 14.12 NP2C-4d frozen 1317 atomic lifecycle-subtree finalizer and rehearsal implemented

Workspace `2026081317` preserves frozen 1316 byte-for-byte and replaces only the reviewed lifecycle-subtree finalization authority. The explicit 1316→1317 migrator/CLI is offline, quiesced and dry-run by default. It refuses a catalog containing live lifecycle commands or subtree evidence and fabricates no source receipt, manifest, effect, mapping, operation or successful state. Frozen 1317 checksum is `sha256:6cb890ccc026435715a1c9171b4d1fd0f74ae6896f63d3cdf66a27b6eec5b61b`; actual schema hash is `sha256:650a031ff1f7d1794bde06d9f9479794d0c89fa2faba8e07762badfe3c500aad` with 179 objects.

The 1317 authority accepts the complete source receipt: transitioned duplicate/Trash/restore nodes plus unchanged-lifecycle parent and sibling effects. It raises the exact effect bound to 11,004 and permits `transitioned=0|1` only under the configured command/source receipt/source generation context. The atomic finalizer reauthorizes the current admin, rechecks command lease/fence and source lineage, then commits binding/projection/incarnation/tree/evidence/mapping/workspace/security/tree-version and operation/audit/outbox state in one catalog transaction. Duplicate, Trash and restore use action-specific transition rules; any fault rolls the catalog transaction back.

`notion_workspace_page_lifecycle_subtree_orchestration.py` remains route-unregistered. For a new effect it requires explicit source quiescence, an exact rehearsal sentinel and contained catalog/user-data/source paths. It performs one full source integrity gate, captures complete bounded receipts and locators, replays the source adapter idempotently, revalidates the candidate, and calls the catalog finalizer. A finalized catalog replay is checked before sentinel, path, quiescence, source clock or source I/O. Its aggregate report contains counters only, not owner/page IDs, paths, hashes, nonces, filenames, public tokens or lease secrets.

This is offline/quiesced contract evidence, not live or differential parity. Official Archive/unarchive remains a later source-contract design; public duplicate, runtime routes/UI/workers, resolver cutover, live migration, destructive watermark acknowledgement and purge remain unauthorized.

### 14.13 NP2C-4e independent source-replay and adversarial audit completed

The independent audit reproduced a real recovery fault: after the legacy source transaction committed, a lost catalog acknowledgement or a catalog-finalizer failure caused retry to recapture the already-trashed root precondition and stop. The source adapter now exposes a validated immutable lifecycle-operation replay probe, and offline orchestration consults it before any precondition recapture. A retry therefore reuses the committed source receipt, records or replays the catalog source-applied acknowledgement, and proceeds without applying the source mutation again.

Focused evidence covers response loss at both the source-receipt acknowledgement and catalog-finalizer boundary; atomic catalog rollback after `after_evidence`, `after_bindings`, and `after_tree`; exact source/catalog effect-bound equality at 11,004; refusal of 11,005 effects before effect parsing; canonical result/deep-health validation; and aggregate-report exclusion of identifiers, sentinel/source/catalog paths, digests and lease secrets. Because the finalizer consumes a complete owner receipt and rebuilds the exact owner fence set, a changed lifecycle effect leaves the materialized tree `complete`; `full_sweep_required=true` remains a watermark/cutover obligation rather than a claim that the tree is partial.

Machine contract v18 records this as offline/default-off `contract-tested` evidence only. It does not authorize DDL, runtime registration, routes/UI, resolver cutover, live migration, destructive acknowledgement or purge.

### 14.14 NP2C-5 official Archive source-contract design frozen

Machine contract v19 and `NP2C_OFFICIAL_ARCHIVE_DESIGN.md` now distinguish official Archive from both the legacy `pages.archived` Trash bit and the historical catalog storage action `archive` whose canonical meaning is Trash. Official archived content keeps an active binding and complete locator projection, remains subject to current IAM, and is hidden only by default navigation/search/database visibility policy. Frozen 1317 cannot authorize this representation without confusing physical Trash quarantine with addressable Archive, so candidate workspace 1318 is required but unimplemented.

H5e retains candidate source v4 `2026081304`. Official Archive reserves only its exact successor v5 `2026081305`; it may not skip, repurpose or silently implement v4. Because v4 and the approved Business/Enterprise live-characterization evidence set are both absent, source v5, workspace 1318, adapters, finalizers and runtime remain blocked. The next exact gate is NP2C-5a live characterization of nested archive/unarchive, visibility, permissions, cross-lifecycle operations and public-link behavior.

The NP2C-5a preparation harness is now present as a separate 16-scenario/11-profile matrix, strict verifier/adversarial tests, execution runbook, external authorization inspection/preflight and evidence-bundle acceptance verifier. Inspection validates record structure/redaction and emits the canonical digest candidate while forcing all authorization and completion booleans false. Only after the user approves that exact digest out of band may preflight validate an at-most-24-hour window, synthetic Business and Enterprise tenants, independent review and explicit public/plan-transition risk approval; any record mutation invalidates that approval. The completion verifier requires the same digest, reads only distinct explicitly listed files below an approved repository-external root and binds every exact matrix pair to fixture/action/result/redaction hashes, reviewer authorization and a canonical semantic decision. The gate remains `prepared_not_authorized_not_executed`: there is no actual user-approved digest, approved tenant session, operator/reviewer evidence set, accepted bundle, source/catalog mutation authority or live observation. It does not alter page-tree machine contract v19 or complete NP2C-5a.
