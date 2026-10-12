# NP2C-5 official Archive/unarchive source-contract design

Status: official-reference design frozen; live characterization, source schema, catalog schema, adapters, finalizers, routes, UI, resolver changes, migrations and operational rollout are not implemented or authorized.

> **Optional paid-feature scope:** 이 문서는 official Archive를 제품 범위로 채택할 때만 적용되는 연구·설계
> 트랙이다. Business/Enterprise는 reference observation metadata이며 local billing/entitlement 로직을 복제하라는
> 요구가 아니다. 이 트랙의 live characterization이나 구현 부재는 Core Free Web UI와 Second Brain 개발을
> 차단하지 않는다. Core 실행 순서는 `../NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`를 따른다.

As of: 2026-08-14 Asia/Seoul

Machine contract: `docs/notion_parity/np2c_page_tree_lifecycle_contract.json` version 19

## 1. Outcome

Official Archive must be a lifecycle and visibility state distinct from the existing Trash compatibility path. The current `notion_store.archive_page` name and `pages.archived` bit continue to mean Delete/Trash compatibility. They must not be renamed, reinterpreted, or reused for official Archive.

This design reserves legacy source version `2026081305` as the first possible Archive schema, but only after the separately reserved H5e conditional-ack source version `2026081304` exists as an exact frozen predecessor. It also identifies workspace `2026081318` as the first possible catalog authority. Neither candidate schema is implemented by this slice.

Within this optional Archive track, the next gate is an approved live-characterization matrix, not DDL or runtime wiring.

## 2. Official reference snapshot

The design relies only on current first-party public documentation:

- [Archive pages](https://www.notion.com/help/archive-pages) says Archive is beta, is available on Business and Enterprise plans, keeps pages accessible, preserves links, hides archived content from default Search, filters archived database content from normal views, and lets users with edit permission archive or unarchive.
- The same reference says archiving a parent archives all subpages and exposes archived content through Search filters, Library, database settings, and Enterprise Content search.
- [Delete and restore content](https://www.notion.com/en-gb/help/duplicate-delete-and-restore-content) separately describes Delete as moving content to the Recycle Bin, where it cannot be edited until restored and is subject to retention.
- [The March 27, 2026 release note](https://www.notion.com/releases/2026-03-27) says archived pages are hidden from search, sidebar and database views by default while retaining their Archive banner and subtree behavior.
- [The current Page API object](https://developers.notion.com/reference/page) and [Update page](https://developers.notion.com/reference/patch-page) response schemas distinguish `is_archived` from `in_trash`. The documented update body describes Trash/restore through `in_trash`; this design does not infer a supported Archive mutation API from a response field.

These references are product observations, not live or differential evidence. The public API is not used to scrape, clone, mutate, or certify the product.

## 3. Confirmed semantics and non-inferences

Confirmed reference semantics:

1. Archive and Trash are different states.
2. Archived content remains addressable to an otherwise authorized user and existing workspace links continue to work.
3. Default navigation/search/database presentation hides archived content, while explicit archived-content views can include it.
4. Archiving a parent affects its subpages.
5. Archive/unarchive requires edit permission and a Business or Enterprise entitlement.
6. Trash retention, restore and permanent deletion rules do not define Archive.

The following are not established by the public references and remain mandatory live-characterization questions:

- whether unarchiving a parent recursively unarchives every child;
- how a child archived before its parent behaves when the parent is unarchived;
- whether an inherited archived child can be unarchived independently;
- what happens when content is created under, moved into, moved out of, duplicated from, trashed from, or restored into an archived subtree;
- whether archived pages remain editable beyond the explicit unarchive action;
- exact comment, mention, backlink, relation, rollup, synced-block and notification behavior;
- guest, teamspace, private-page and workspace-admin permission behavior;
- exact desktop, web and mobile sidebar/Library/Search behavior;
- public Site and published-link behavior;
- API or webhook mutation/event semantics for official Archive.

No implementation may turn an unresolved item into a silent assumption. Ambiguous cases refuse before source mutation.

## 4. Compatibility and action namespaces

Frozen compatibility names remain historical facts:

- `notion_store.archive_page` means Trash;
- HTTP DELETE and `/trash` routes keep their existing compatibility meaning;
- legacy `pages.archived=1` means the page is in the compatibility Trash state;
- frozen catalog command action `archive` maps to canonical lifecycle action `trash` in 1316/1317 evidence.

The future official capability uses separate internal namespaces:

- source actions: `legacy.page_lifecycle.content_archive` and `legacy.page_lifecycle.content_unarchive`;
- source receipt contracts: `np2c_legacy_page_content_archive_receipt_v1` and `np2c_legacy_page_content_unarchive_receipt_v1`;
- catalog storage actions: `archive_content` and `unarchive_content`;
- canonical product actions: `archive` and `unarchive`;
- catalog operation actions: `workspace.page_archived` and `workspace.page_unarchived`.

The legacy function name is never exposed as evidence that official Archive exists. Historical `action='archive'` Trash rows are never relabeled.

## 5. Source lineage decision

The exact current source chain is v1 `2026081301` → incarnation v2 `2026081302` → consumer v3 `2026081303`. H5e owns the candidate v4 number `2026081304` for conditional acknowledgement and remains `authorized=false`, `implemented=false`.

NP2C-5 reserves candidate source v5 `2026081305` for official Archive under these rules:

1. v5 may accept only an exact frozen v4 source; it may not skip v4.
2. NP2C-5 may not repurpose, rewrite or silently implement v4.
3. If H5e is rejected or its version changes, NP2C-5 stops for a new lineage review instead of reusing `2026081304`.
4. No request-time or startup migration is allowed.
5. A future v5 migration remains explicit, quiesced, exact-path, checksummed, backup-verified and copy-on-write with ambiguous outcome convergence.
6. Migration must preserve every v1/v2/v3/v4 object and byte of current page/content/file state and fabricate no Archive state, cohort or operation receipt.

Because v4 is not implemented, source-v5 implementation is currently blocked. Reserving a successor version is not implementation permission.

## 6. Candidate source state model

The future v5 schema needs a separate page-local Archive state. The proposed frozen-object review starts from `notion_page_archive_states`, keyed by exact page identity and containing:

- `archive_state=active|archived`;
- positive monotonic `archive_generation`;
- the exact page incarnation ID/generation;
- the Archive root page ID and immutable source operation ID that caused the current archived state;
- direct/inherited cohort provenance;
- archived-by subject and trusted archived timestamp;
- row version and trusted update timestamp.

The configured source writer must fail closed without exact Archive action/operation context. Direct INSERT/UPDATE/DELETE, `OR REPLACE`, generation regression, incarnation mismatch, cross-owner/root references and malformed identifiers are rejected. Existing rows are backfilled as active only; no page is fabricated as archived.

An archive source transaction must:

1. authorize the exact source actor and server-side plan entitlement;
2. capture the bounded exact physical subtree and each page incarnation/prestate;
3. distinguish pages whose effective Archive state changes from pages already archived by another cohort;
4. keep `pages.archived=0`, preserve page/content/block/file/version rows and preserve the incarnation;
5. update the separate Archive state and page proof/watermark exactly once per changed page;
6. commit the complete secret-free cohort receipt in the existing v3+ immutable operation ledger in the same SQLite transaction.

Unarchive may clear only a proven cohort under the final characterized nested-state rules. It may never broadly reactivate unrelated archived or trashed content. Until nested Archive/unarchive behavior is characterized, the adapter does not exist and every such mutation remains disabled.

## 7. Candidate catalog 1318 authority

Frozen workspace 1317 cannot represent official Archive safely:

- its physical `lifecycle_state='archived'` corresponds to a quarantined binding and Trash compatibility;
- its `canonical_lifecycle_state='trashed'` evidence is tied to the historical storage action `archive`;
- official archived content must remain addressable with an active binding and locator projection;
- current subtree manifest/action checks admit only duplicate, Trash and restore.

Candidate workspace `2026081318` is therefore required after source v5 is available. The design does not freeze DDL or hashes yet. The 1318 review must:

1. preserve frozen 1317 and every historical Trash row byte-for-byte;
2. add separate immutable Archive manifest/effect authority for `archive_content|unarchive_content`;
3. represent official archived pages with binding state `active`, complete locator projection retained, physical tree lifecycle `active`, and canonical lifecycle state `archived`;
4. add an explicit official-archived counter/readiness invariant rather than reusing the Trash/quarantine count;
5. allow only exact source-v5 cohort evidence, command lease/fence, incarnation, workspace/tree/binding and service-context guards;
6. retain direct resolver reachability subject to current IAM while default sidebar/search/database views exclude canonical archived nodes;
7. refresh active binding proof/projection atomically without deleting locator reachability;
8. commit tree/evidence/workspace/security/tree-version and operation/audit/outbox in one catalog transaction;
9. mark dependent-reference full-sweep debt without falsely changing a complete full-owner materialized tree to partial;
10. make deep health reject action aliases, missing cohort evidence, projection removal, quarantine, counter drift, noncanonical results and ledger mismatch.

No 1317 row is relabeled during migration. No operational catalog is migrated by this design.

## 8. Visibility and access contract

Archive is presentation state, not an authorization grant or revoke:

- direct locator resolution continues to require current workspace/source IAM;
- archived content does not become public, globally searchable or admin-readable merely because it is archived;
- default sidebar, normal Search and normal database views omit canonical archived rows;
- explicit Archive-aware Library/Search/database/admin views may include rows only after their route and capability contracts are separately reviewed;
- current links resolve if the caller remains authorized;
- Trash, deleted and quarantined bindings remain non-resolver targets;
- public/published link behavior is unknown and therefore the first offline adapter must refuse any cohort with public locator evidence before source mutation.

Client flags never decide plan entitlement or permission. Runtime Archive requires a server-owned Business/Enterprise capability plus current edit authority through NP2D's unified capability decision. The initial offline prototype, if separately approved, may narrow authority to the exact source owner who is also a current workspace admin; that is a safety restriction, not a parity claim.

## 9. Saga, replay and atomicity

Archive/unarchive follows the established cross-database pattern:

1. exact catalog replay probe before sentinel, path, quiescence or source I/O;
2. exact source operation replay probe before page/subtree reads;
3. new source effect under explicit quiescence and exact contained paths;
4. immutable source receipt commit with source state;
5. catalog acknowledgement of the exact receipt;
6. external source receipt revalidation;
7. one atomic catalog finalizer transaction;
8. response-loss retry through immutable source and catalog ledgers.

This is at-least-once/state-idempotent convergence, not cross-database exactly-once. Unknown source or filesystem outcome becomes `manual_audit`; it is not retried as a guessed new effect.

## 10. Bounds and redaction

The first offline contract remains deliberately bounded:

- synchronous subtree: at most 1,000 pages and depth 256;
- sibling scope: at most 10,000 entries per affected parent;
- source and catalog effect counts must be exactly equal;
- aggregate metadata/proof materialization: at most 256 MiB;
- one full source integrity attestation per exact owner and at most two immutable source snapshots per new effect;
- zero directory scans and zero source/file/network work while a catalog writer transaction is held;
- operation result and aggregate report contain no title, content, page/owner identifier, raw path, filename, public token, incarnation nonce, digest or lease token.

An oversized subtree is refused before source mutation. A future asynchronous large-subtree design is a separate durable-batching slice.

## 11. Required characterization and adversarial evidence

Before source-v5 DDL review, an approved Business/Enterprise test workspace must characterize at least:

- parent archive and unarchive with active, independently archived and nested archived descendants;
- direct and inherited banners, actor/timestamp and icon behavior;
- root, page-parented, database-page, inline-database and data-source cases;
- direct link, backlink, relation, rollup, mention, comment and synced-block behavior;
- default and explicit-filter Search, Library, sidebar and database visibility;
- create/move/duplicate/trash/restore interactions at and across an archived boundary;
- owner, editor, commenter, reader, guest, teamspace member and workspace-admin outcomes;
- public/published links;
- web, desktop and mobile behavior;
- response loss, concurrent permission/plan loss and subtree drift.

Evidence follows the Notion evidence policy and stores only redacted artifacts outside protected/live data paths. Public docs alone cannot promote this slice to live-characterized or differential-passed.

Future implementation tests must also reject source-version skipping, reuse of `pages.archived`, historical Trash relabeling, locator removal, binding quarantine, plan/client-flag spoofing, cohort broadening, same-ID new incarnation, nested-state ambiguity, public-link assumptions, raw SQL without service context, fault-phase partial commit, noncanonical ledger data and identifier leakage.

## 12. Ordered implementation gates

The only approved sequence is:

1. `NP2C-5` — this official-reference design and machine-contract freeze;
2. `NP2C-5a` — approved Business/Enterprise live-characterization matrix and reviewed semantic decisions;
3. H5e-1 source v4 schema/migrator only if separately authorized, still with destructive ack disabled;
4. `NP2C-5b` — exact v4→v5 Archive state schema/migrator and route-unregistered source adapter;
5. `NP2C-5c` — frozen 1317→1318 catalog guards and atomic Archive finalizer;
6. `NP2C-5d` — exact-sentinel offline response-loss/rollback rehearsal;
7. `NP2C-5e` — independent security, bounds, redaction and deep-health audit;
8. `NP2C-6` — protected routes/default-off UI only after NP2D capability review;
9. `NP2C-7` — retention and purge only after legal, security, backup/restore and operations approval.

Stop if H5e lineage changes, live characterization is unavailable, a protected boundary is required, a public-link behavior would be guessed, an exact subtree/cohort cannot be proven, or any step would enable runtime behavior or touch operational data.

### 12.1 NP2C-5a preparation harness

The repository now contains a separate v1 preparation matrix, strict read-only verifier, adversarial tests and execution runbook:

- `docs/notion_parity/np2c_archive_live_characterization_matrix.json`;
- `tools/verify_notion_np2c_archive_characterization.py`;
- `tests/test_verify_notion_np2c_archive_characterization.py`;
- `tools/verify_notion_np2c_archive_authorization.py`와 bounded preflight tests;
- `tools/verify_notion_np2c_archive_evidence_bundle.py`와 그 adversarial tests;
- `docs/notion_parity/NP2C_ARCHIVE_LIVE_CHARACTERIZATION_RUNBOOK.md`.

The matrix fixes 16 required scenarios, 11 Business/Enterprise or anonymous observation profiles, the evidence-policy manifest fields, no-inference rules and the exact source-v4/source-v5/workspace-1318 non-implementation boundary. A separate external-root inspection mode validates the redacted record structure and exposes its canonical authorization digest candidate while forcing `valid=false`, `authorized_to_begin_characterization=false` and `approval_digest_matched=false`; inspection is never approval. After the user explicitly approves that exact digest out of band, the current-time preflight validates a live, at-most-24-hour authorization window, synthetic Business and Enterprise tenants, independent operator/reviewer and the public-link/plan-transition risk approvals while permanently denying implementation and completion claims. A self-authored `approved=true` record cannot pass with a different digest, and any record mutation invalidates the approved digest. The acceptance verifier requires the same digest and binds a reviewed bundle to every exact scenario/profile run, distinct file paths with exact fixture/action/manifest/evidence bytes, result and redaction hashes, independent reviewer and a canonical semantic decision. Its status is `prepared_not_authorized_not_executed`; an actual user-approved digest, tenant availability, live evidence, implementation and completion claims are all absent. These preparation artifacts do not complete NP2C-5a and do not change machine contract v19. They only make the approved live run and its later acceptance deterministic and reviewable when the required tenant, operator, reviewer and external redacted evidence root exist.

## 13. Completion statement

NP2C-5 is complete when this design, machine contract v19, adversarial verifier cases, roadmap/handoff state and a hash-bound checkpoint agree that:

- official Archive is designed and distinct from Trash;
- candidate source v5 and workspace 1318 are required but unimplemented;
- H5e v4 remains reserved and unimplemented;
- live characterization remains pending;
- all runtime, routes/UI, resolver, live migration, destructive acknowledgement, public behavior assumptions and purge remain disabled.

Completing this design permits only NP2C-5a characterization planning. It does not authorize source or catalog implementation.
