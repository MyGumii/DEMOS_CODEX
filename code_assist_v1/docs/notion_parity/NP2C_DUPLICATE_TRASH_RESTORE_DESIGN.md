# NP2C-4 duplicate, Trash/restore, and Archive terminology contract

Status: design frozen; route-unregistered source-v3 adapters/file recovery, the separately reviewed workspace 1316 lifecycle-subtree schema/guards, the frozen workspace 1317 atomic catalog finalizer/exact-sentinel offline rehearsal, and the NP2C-4e recovery/adversarial audit are contract-tested. NP2C-5 separately freezes the reference-only official Archive design, but no Archive source/catalog implementation, route, UI, worker, resolver, destructive acknowledgement, purge, live migration, or live-data execution is authorized by this document.

As of: 2026-08-14 Asia/Seoul

Machine contract: `docs/notion_parity/np2c_page_tree_lifecycle_contract.json` version 19

## 1. Objective

NP2C-3e3 finished the isolated, quiesced create/move/reorder source-to-catalog saga through frozen workspace schema 1315. NP2C-4 defines the next lifecycle boundary without widening that implementation by analogy.

This slice must answer five questions before code is added:

1. which product action is actually being modeled;
2. how a subtree identity survives Trash and restore without accepting a recreated page ID;
3. how duplicate page, block, and file identities remain replay-stable;
4. how file bytes and SQLite state converge after process loss without claiming a cross-filesystem transaction;
5. which new source and catalog authority is genuinely required.

The output of this slice is a reviewed design, machine-verifiable invariants, adversarial weakening tests, and a checkpoint. It is not an implementation permission.

## 2. Reference snapshot and terminology correction

The current official product distinguishes Archive from Delete/Recycle Bin:

- [Archive pages](https://www.notion.com/help/archive-pages) describes Archive as a separate, currently beta capability. An archived page remains accessible, parent Archive applies to subpages, and users with edit permission may archive or unarchive.
- [Delete and restore content](https://www.notion.com/en-gb/help/duplicate-delete-and-restore-content) describes Delete as moving a page into the Recycle Bin, where it cannot be edited until restore. The default Bin retention is 30 days, with enterprise policy variation.
- [Custom data retention settings](https://www.notion.com/help/custom-data-retention-settings) allows enterprise owners to restore to the original location or Private pages and to configure retention.
- [Duplicate public pages](https://www.notion.com/en-gb/help/duplicate-public-pages) includes contained subpages, is gated by the publisher's duplicate-as-template setting, and may surface inaccessible referenced content as unavailable.

The existing compatibility implementation does not have these distinct states:

- HTTP `DELETE /api/notion/pages/<id>` calls `notion_store.archive_page`;
- `/api/notion/trash` lists those rows;
- `/api/notion/trash/<id>/restore` calls `restore_page`;
- the legacy source stores one `pages.archived` bit.

Therefore the existing `archive_page` name denotes **Trash compatibility**, not the current official Archive feature. NP2C uses the following canonical vocabulary from this checkpoint onward:

- `trash`: active content becomes unavailable and enters recoverable Trash;
- `restore`: the exact trashed incarnation is returned to an explicitly valid placement;
- `archive` / `unarchive`: a future distinct capability in which content remains addressable under Archive visibility policy;
- `purge`: irreversible data/file removal after retention and legal approval.

Machine contracts and future APIs must not call Trash `archive`. Frozen legacy function and database column names remain compatibility implementation details.

## 3. Current implementation inventory

### 3.1 Duplicate

`notion_store.duplicate_page` currently:

- requires the source owner;
- recursively collects only active subtree rows without an explicit bound;
- allocates random page, block, and file IDs;
- copies file bytes before the SQLite transaction commits;
- remaps page, block, and file references inside copied content;
- copies settings, icon, cover, blocks, page versions, and file rows;
- does not copy shares or public links;
- removes copied file bytes best-effort when a Python exception is observed.

A process exit after copying bytes and before operation evidence may leave unreferenced bytes. A response-loss retry creates a second subtree because there is no immutable operation receipt.

`duplicate_public_page` is a separate cross-owner/public capability. It copies a published subtree into the caller's private root and redacts inaccessible references. It remains NP2E public-subtree scope and is not authorized by NP2C-4.

### 3.2 Trash compatibility

`notion_store.archive_page` updates the selected active page and every currently active descendant to `archived=1` in one source transaction and bumps their revisions. It does not delete file bytes. It has no operation ID or immutable cohort receipt.

The active descendant set matters: a child already in Trash before its parent is trashed is not part of the parent's transition cohort.

### 3.3 Restore compatibility

`notion_store.restore_page` restores the selected row, every descendant including rows previously trashed by a different action, and every ancestor. This can silently reactivate unrelated content and permissions. The NP2C contract forbids that behavior.

### 3.4 Permanent delete compatibility

`delete_page_permanent` deletes archived page/file rows in SQLite, commits, and only then removes file bytes best-effort. It has no durable cleanup receipt. It remains disabled and is not renamed into NP2C-4.

## 4. Authority and action split

The legacy owner shard remains the content, physical tree, file, and lifecycle authority before cutover. The workspace catalog remains a generation-fenced projection and saga authority.

The canonical command action set for the future lifecycle service is:

- `duplicate`;
- `trash`;
- `restore`;
- `archive`;
- `unarchive`;
- `purge`.

Only `duplicate`, `trash`, and `restore` are designed for the next implementation sequence. `archive`, `unarchive`, and `purge` remain unimplemented gates.

The first offline adapters require one subject to be both the current source owner and current workspace administrator. This deliberately narrow authority avoids a confused-deputy path before NP2D supplies a unified capability resolver. A future delegated editor/admin flow is a separate reviewed contract.

## 5. Identity and immutable receipts

Every command binds the exact:

- workspace and full owner/page locator;
- current workspace, security, tree, binding, and tree-node versions;
- source database device, inode, link count, source epoch, and full-sweep generation;
- page incarnation ID and incarnation generation;
- page change generation/mask and source receipt;
- source request digest and caller operation ID;
- action-specific subtree, placement, mapping, and file manifest digests.

A deleted-and-recreated page ID is never a restore candidate. Restore requires the same incarnation ID and generation recorded by the Trash receipt.

The generic frozen source-v3 table `notion_legacy_change_consumer_operations`, under `consumer='np2c.lifecycle'`, is sufficient for immutable source success receipts. The reserved H5e source-v4 version is not reused. Proposed source actions/results are:

- `legacy.page_lifecycle.duplicate` / `np2c_legacy_page_duplicate_receipt_v1`;
- `legacy.page_lifecycle.trash` / `np2c_legacy_page_trash_receipt_v1`;
- `legacy.page_lifecycle.restore` / `np2c_legacy_page_restore_receipt_v1`.

Each new effect checks exact operation replay before renewed quiescence, path access, source page reads, or file work. Same ID plus a different canonical request is a conflict.

## 6. Duplicate contract

### 6.1 Scope and placement

Duplicate is same-owner and same-workspace only in NP2C-4. The exact selected active subtree is captured parent-first before mutation. The new root is inserted into the same parent scope immediately after the source root unless an explicitly reviewed exact active parent/relative placement is part of a later request version.

The complete destination sibling scope is fenced. Internal copied parentage and relative order are preserved. Shares, guests, public links, comments, notifications, integrations, edit locks, and publication settings are not copied.

### 6.2 Replay-stable mappings

Page, block, and file IDs are derived with separate domain-separated digests over at least:

- workspace;
- source owner;
- actor;
- caller operation ID;
- source root identity and incarnation;
- source object type and source object ID.

Page IDs use `p_<32-lowercase-hex>`. Block/file IDs use their existing validated prefixes and lengths. The complete source→destination mapping digest is committed by the source request before file or database mutation. Any existing destination page, incarnation-history row, block, file row, or nonmatching byte path is a conflict; random substitution is forbidden.

Every new page receives a fresh source incarnation from the configured v2 trigger. No original incarnation, share, public token, or catalog binding generation is inherited.

### 6.3 Content and reference policy

The source transaction copies bounded page rows, blocks, versions, file rows, and internal references. A reference to a copied page/block/file is rewritten to the deterministic destination identity. An external reference is preserved only when the current exact-owner authorization and later NP2D policy explicitly allow it; the first NP2C adapter fails closed instead of leaking or guessing.

Raw title, content, settings, icon, cover, filename, path, public token, nonce, or file hash is not persisted in the catalog command, audit, outbox, aggregate report, or checkpoint. The source-local immutable receipt may commit sensitive manifests by digest and return full identifiers only to the already-authorized caller.

### 6.4 File-byte convergence

SQLite and file bytes are not presented as one transaction. The safe ordering is:

1. the prepared catalog command already exists;
2. derive the exact operation-scoped destination mapping;
3. open every source file by an exact contained no-reparse path and verify size plus SHA-256;
4. copy into an operation-scoped, contained staging area with exclusive creation;
5. flush and verify each staged byte image;
6. publish each deterministic final byte path using an atomic no-overwrite rename, accepting an existing path only when the byte hash is exact;
7. in one source `BEGIN IMMEDIATE`, revalidate the entire source subtree/fence, insert all page/block/file/version rows and the immutable source-v3 result, then commit.

Before source commit, final byte paths are unreferenced and therefore not resolver-visible. A crash before source commit leaves a prepared catalog command and deterministic byte paths; exact replay verifies and reuses them or performs guarded cleanup. A crash after source commit has both rows and bytes and is recognized from the immutable source receipt.

Automatic cleanup is allowed only when the operation mapping is exact, no source file row references the destination, path containment/file identity still matches, and the command outcome is positively known to be pre-commit failure. Unknown/ambiguous state preserves bytes for manual audit. No path is reconstructed from untrusted stored relative text without exact root/handle validation.

### 6.5 Catalog finalization

One catalog transaction must create every new binding, incarnation attestation, locator projection, and tree node; update the destination sibling scope through a new order generation; update workspace/security/tree versions once; mark the tree `partial` with full-sweep debt; insert action-specific manifest/effect evidence; finalize the command; and commit one canonical operation/audit/outbox trio.

The subtree is invisible until the complete source effect is proven. A half-created catalog subtree is forbidden.

## 7. Trash contract

### 7.1 Exact cohort

Trash captures the selected active node and exactly the active descendants transitioned by this command. Already trashed, archived, deleted, hidden, or different-incarnation descendants are not silently added.

The immutable pre-manifest records for every cohort node:

- exact incarnation and source receipt;
- active parent/depth/order and order generation;
- binding/projection commitment;
- whether it was the command root;
- the source/canonical archive-cohort digest.

The source transaction changes only this cohort, coalesces internal page/revision statements to one logical watermark generation per affected page, preserves file bytes and page identities, and commits the exact post-receipts with the source-v3 Trash operation.

### 7.2 Catalog effect

One catalog transaction:

- quarantines every affected active binding under the existing source-inactive compatibility authority;
- deletes every active locator/file/public projection before binding generation changes;
- marks exactly the cohort tree nodes `trashed` while retaining original placement/incarnation/cohort evidence;
- compacts only the remaining active sibling scopes through a new order generation;
- updates active/trashed counters and tree/workspace/security versions once;
- invalidates favorites and future editor/capability caches through canonical operation evidence;
- records manifest/effects plus operation/audit/outbox and finalizes the command.

File bytes, content rows, versions, shares, comments, and public-link source rows remain retained. Public resolution is disabled because the active catalog projection is removed.

The tree becomes `partial` with full-sweep debt because linked-page proof hydration and lifecycle visibility may affect pages outside the cohort.

## 8. Restore contract

### 8.1 Cohort and identity

Restore references one exact prior Trash command/receipt. It restores only nodes transitioned by that cohort and still carrying the same incarnation. A descendant trashed by a different operation remains trashed.

Missing rows, retired incarnations, delete→same-ID recreation, changed cohort membership, or a source/database rollback is a conflict or `manual_audit`; it is never interpreted as a successful restore.

### 8.2 Placement

Ancestors are never silently restored. The request uses one of two explicit modes:

- `original`: the prior external parent is still active, visible, same-incarnation, and matches the expected parent/sibling scope; or
- `reviewed_fallback`: the current admin and source owner explicitly choose root or one exact active same-owner parent plus an optional exact relative sibling.

Without a valid mode, restore refuses before source mutation. Internal parentage/order inside the restored cohort is preserved. The root is inserted into a revalidated active sibling scope with dense derived order; no old numeric position is trusted without current scope evidence.

### 8.3 Source and catalog effect

The source transaction activates only the exact cohort, applies any reviewed root reparenting and bounded parent child-reference updates, coalesces each changed page to one logical watermark generation, and commits an immutable restore receipt.

One catalog transaction reactivates the exact quarantined bindings under lifecycle-only authority, inserts current incarnation attestations and complete locator projections, restores cohort tree nodes and active sibling order, updates counters/versions once, leaves unrelated trashed nodes unchanged, marks full-sweep debt, and commits the command plus operation/audit/outbox.

## 9. Official Archive/unarchive gate

Current legacy `pages.archived` cannot represent both Archive and Trash. Official Archive keeps a page accessible, whereas the current source bit removes it from the active compatibility path and drives Trash routes.

Therefore `archive` and `unarchive` require a distinct future legacy lifecycle state and exact source migration. NP2C-4a assigned no source version because H5e reserves `2026081304` for conditional acknowledgement. The later NP2C-5 design now reserves only exact successor `2026081305` for Archive, requires a frozen v4 predecessor, and forbids skipping or repurposing v4. Neither source version is implemented by this document.

The frozen reference contract in `NP2C_OFFICIAL_ARCHIVE_DESIGN.md` keeps Archive distinct from Trash in source receipt, binding visibility, tree state, search policy, public link behavior, and plan/capability checks. It does not alias Archive to the current `archive_page` function and does not authorize implementation.

## 10. Candidate workspace 1316 decision

The current frozen 1315 schema cannot safely express NP2C-4:

- command normalization and DDL action guards allow only rename/create/move/reorder;
- the structural manifest models one create target and requires `subtree_count=1` for create;
- structural effects require a non-null active after-proof/projection and cannot express active→trashed or trashed→active;
- tree lifecycle states do not distinguish official Archive from Trash;
- no immutable Trash cohort/original placement or duplicate mapping/file-work commitment exists;
- no lifecycle-only multi-create or bounded multi-page quarantine/reactivation authority exists.

After this design review, candidate workspace `2026081316` is required. Frozen 1315 SQL/checksum/actual schema and the 1314→1315 migrator remain byte-for-byte independent.

The minimum 1316 concerns are:

1. action-specific immutable duplicate and Trash/restore manifests/effects;
2. before/after lifecycle state, archive-cohort, original placement, duplicate mapping, and file-work commitments;
3. a tree lifecycle representation that distinguishes `active`, `archived`, `trashed`, and retained `deleted` tombstones;
4. lifecycle-only multi-create, active→trashed, and exact trashed→active binding/tree authority;
5. generation-scoped dense active order updates for removal/insertion;
6. exact result/operation/audit/outbox namespaces and deep-health correlation;
7. fail-closed service-context UDFs and raw-SQL guards.

The 1315→1316 migration must refuse live commands and must not silently reinterpret historical `archived` nodes. If an existing non-active node cannot be proven to be Trash from exact frozen source/catalog evidence, migration stops for explicit reconciliation. It fabricates no duplicate, cohort, source receipt, operation, or successful lifecycle state.

No DDL or migration module was implemented in NP2C-4a itself. The separately authorized NP2C-4c slice later implemented the frozen 1315→1316 schema/guard boundary, and NP2C-4d independently implemented the frozen 1316→1317 finalization authority and exact-sentinel offline rehearsal.

### 10.1 Frozen workspace 1317 finalization authority

The real source receipt includes both nodes whose lifecycle changed and parent/sibling nodes whose lifecycle did not change but whose proof, placement, order, or source generation participates in the exact command post-state. Frozen 1316 intentionally admitted transitioned effects only, so it could not safely execute the complete receipt. Workspace `2026081317` preserves 1316 and authorizes up to 11,004 effects with `transitioned=0|1`, including narrowly scoped active→active proof refresh.

The 1317 catalog finalizer commits duplicate/Trash/restore bindings, projections, incarnation attestations, tree nodes/order, immutable subtree manifest/effects/mappings, workspace/security/tree versions, and the operation/audit/outbox trio in one catalog transaction. Source lineage, receipt, command lease/fence, workspace/tree/binding state and all action-specific cohorts are checked again before commit. Exact replay precedes renewed filesystem or source work.

The route-unregistered offline orchestration combines the frozen source-v3 adapter and 1317 finalizer only beneath an exact rehearsal sentinel, contained paths and explicit source quiescence. Its report is identifier-free. This evidence does not enable runtime routes/UI, resolver cutover, live migration, watermark acknowledgement, public duplicate, official Archive/unarchive or purge.

### 10.2 NP2C-4e recovery and adversarial audit

The 4e audit found that a committed source Trash followed by a lost catalog acknowledgement or finalizer rollback could not be resumed safely because retry attempted to recapture the now-inactive root precondition. The source adapter now exposes an exact immutable lifecycle-operation replay probe. Offline orchestration performs that probe before precondition recapture, reuses the committed source receipt, and converges the catalog command without a second source mutation.

Independent tests cover source-receipt acknowledgement loss, catalog-finalizer response loss, atomic rollback after each catalog phase (`after_evidence`, `after_bindings`, `after_tree`), exact source/catalog parity at 11,004 effects, 11,005 refusal before effect parsing, canonical deep-health validation, and identifier/path/digest/lease-secret redaction. A complete owner receipt keeps the finalized tree `complete`; `full_sweep_required` records remaining watermark/cutover debt. Machine contract v18 closes NP2C-4e without enabling runtime behavior.

## 11. Bounds

All bounds are checked before the first source/file mutation:

- request JSON: 64 KiB;
- subtree nodes: 1,000;
- depth: 256;
- active sibling scope: 10,000;
- total duplicate blocks: 50,000;
- total duplicate files: 5,000;
- aggregate duplicate file bytes: 256 MiB;
- aggregate source metadata/materialization: 256 MiB;
- parent child-reference blocks: 5,000 per rewritten parent;
- source operation result JSON: 16 MiB;
- one full v1/v2/v3 integrity gate per exact owner and at most two exact snapshots per new effect;
- zero directory scans and zero source/file/network work while a catalog writer transaction is held.

Inputs above these bounds are refused, not silently truncated or converted into synchronous unbounded work. A future asynchronous large-duplicate path needs a separate durable batching design.

## 12. Replay, rollback, and fault matrix

Required negative evidence includes:

- operation-ID reuse with changed root, cohort, placement, mapping, or source digest;
- deterministic page/block/file ID collision;
- inaccessible external reference;
- file source path escape, reparse/hard-link change, size/hash drift, existing mismatched destination, partial copy, fsync/rename failure, and process loss before source commit;
- duplicate source commit response loss and catalog commit response loss;
- database failure after all bytes are staged/published with guarded cleanup or recoverable prepared debt;
- trash cohort drift, independently trashed descendant, permission/lock loss, and active scope drift;
- restore of a new incarnation, missing archive cohort, silently restored ancestor, invalid original parent, invalid fallback, and already-active unrelated descendant;
- lease expiry, workspace/tree/binding/security drift, source fence drift, full-sweep drift, and current IAM loss between every phase;
- binding/projection/attestation/tree/manifest/effect/operation/audit/outbox failure with complete catalog rollback;
- secret/identifier-free denial and aggregate reports;
- permanent delete, official Archive, public duplicate, runtime registration, and live migration remaining disabled.

## 13. Implementation sequence and stop gates

The only approved order after this design checkpoint is:

1. `NP2C-4a` — this contract and machine verifier;
2. `NP2C-4b` — route-unregistered same-owner duplicate and Trash/restore source-v3 adapters in temporary exact shards, including deterministic file staging/recovery;
3. `NP2C-4c` — frozen 1315→1316 workspace schema/guards after separate DDL audit;
4. `NP2C-4d` — atomic catalog finalizers and exact-sentinel end-to-end rehearsal;
5. `NP2C-4e` — independent rollback/bounds/redaction/deep-health audit and evidence closure without runtime enablement;
6. `NP2C-5` official Archive/unarchive reference/source-lineage design, now complete without implementation;
7. `NP2C-5a` approved Business/Enterprise live characterization;
8. protected routes/default-off UI only after NP2D capability review;
9. retention/purge only after legal, security, backup/restore, and operations approval.

Stop immediately if implementation would require taking H5e source version 1304, mutating frozen 1315, accessing live data, writing a directory-scanned resolver, enabling routes/UI/workers, acknowledging watermarks, deleting bytes without durable proof, or conflating Archive with Trash.

## 14. Completion gate for this design slice

NP2C-4a is complete only when:

- the terminology split is present in human and machine contracts;
- current 1315 insufficiency and candidate 1316 concerns are explicit;
- duplicate file convergence and restore cohort/placement semantics are complete;
- public duplicate, official Archive/unarchive, purge, runtime, and live migration remain false;
- the verifier rejects weakened identity, ancestor restore, file bounds, replay order, Archive/Trash aliasing, premature schema implementation, and runtime enablement;
- docs, exact hashes, goal status, test results, and host-dependent failures are stored in a new checkpoint.

Passing this gate permits review of NP2C-4b. It does not authorize 4b implementation automatically.

## 14. NP2C-4b source-v3 adapter checkpoint

NP2C-4b is now implemented only as a route-unregistered, caller-owned exact-shard adapter. The new side-effect-free contract and adapter modules are `notion_legacy_page_duplicate_trash_restore_contract.py` and `notion_legacy_page_duplicate_trash_restore.py`. They reuse the frozen authenticated source-v3 operation ledger and do not consume the H5e-reserved source-v4 lineage.

The temporary-shard evidence fixes the following behavior:

- duplicate IDs for pages, blocks, files, versions, and adjacent parent references are domain-separated and replay-derived;
- duplicate captures a bounded active subtree, refuses external references, lock loss, destination/history collisions, unsafe or hard-linked paths, and source-byte drift;
- file bytes use contained operation staging, exclusive creation, flush/hash verification, atomic no-overwrite publication, exact-existing recovery, precommit cleanup, and manual-audit preservation for indeterminate outcomes;
- Trash records only the exact active cohort and leaves an independently trashed descendant unchanged;
- restore requires the exact prior Trash receipt and same incarnation, restores no ancestor implicitly, and accepts only exact original placement or an explicit active unlocked fallback;
- the source mutation and immutable source-v3 receipt commit in one SQLite transaction, with response-loss replay before renewed quiescence, page, path, or file I/O;
- imports do not load app, route, auth, config, Knowledge, Second Brain, or `notion_store` runtime modules and create no filesystem artifact.

Focused evidence is 18 passing adversarial cases, with 15 existing rename/create/move/reorder cases and 32 source-v3/incarnation migration/consumer cases passing unchanged. This is temporary, offline, quiesced evidence only; no live source or catalog was accessed.

At the NP2C-4b checkpoint workspace 1315 remained current and 1316 was still pending. NP2C-4c later added the frozen 1316 guard schema, NP2C-4d added the frozen 1317 atomic finalizer/rehearsal, and NP2C-4e closed independent recovery/bounds/redaction/deep-health evidence. NP2C-5 subsequently froze the reference-only Archive design and source-v5/workspace-1318 candidate lineage. Runtime routes/UI/workers, resolver cutover, official Archive/unarchive implementation, public duplicate, destructive watermark acknowledgement, purge, and live migration remain unimplemented and unauthorized. The next cohesive slice is approved `NP2C-5a` live characterization.

## 15. NP2C-4c workspace 1316 guard checkpoint

Workspace `2026081316` adds canonical lifecycle action/state fields, immutable bounded subtree manifest/effect/object-mapping evidence, and fail-closed configured UDF guards for duplicate pending binding creation and exact Trash/restore binding/tree transitions. The base `archive` command remains a frozen implementation detail; canonical evidence records `trash` and never aliases official Archive.

The explicit `notion_workspace_lifecycle_subtree_migration.py` and dry-run-default CLI accept exact frozen 1315 only. They refuse live or historical duplicate/Trash/restore commands and ambiguous non-active tree history, fabricate no evidence or success, and were not run against an operational catalog. Frozen 1316 checksum is `sha256:04db7799402ea563228c6be4b016907c0f51ae4ba376fb176bb2a15ca588d81c`; actual schema hash is `sha256:cb375ac9e36d42fb3896eabf11f32d6b35a131f5075c1512a51add3da6d32981` with 179 objects.

This historical guard foundation did not itself mutate a source shard or finalize a catalog command. NP2C-4d subsequently supplied the atomic finalizer/rehearsal and NP2C-4e independently audited its recovery and adversarial boundaries; neither authorizes runtime use.
