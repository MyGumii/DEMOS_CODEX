# Shared Knowledge Agent implementation instructions

These instructions apply to every change under `code_assist_v1/`.

Preferred implementation/review model when model selection is available: `gpt-5.6-sol` with `xhigh` reasoning, as explicitly requested by the user.

## Start here

Before implementing the shared-knowledge goal, read these files in order:

1. `docs/knowledge_spaces/README.md`
2. `docs/knowledge_spaces/second_brain_evolution/README.md`
3. `docs/knowledge_spaces/second_brain_evolution/PRODUCT_CONTRACT.md`
4. `docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md`
5. `docs/knowledge_spaces/second_brain_evolution/EXECUTION_PLAN.md`
6. `docs/knowledge_spaces/second_brain_evolution/ACCEPTANCE.md`
7. `docs/knowledge_spaces/DECISIONS.md`
8. `docs/knowledge_spaces/ARCHITECTURE.md`
9. `docs/knowledge_spaces/SCHEMA_V1.md`
10. `docs/knowledge_spaces/API_UI_CONTRACT.md`
11. `docs/knowledge_spaces/CURRENT_SYSTEM_INVENTORY.md`
12. `docs/knowledge_spaces/MIGRATION_ROLLOUT.md`
13. `docs/knowledge_spaces/TEST_OPERATIONS.md`
14. `docs/knowledge_spaces/DEVELOPMENT_BASELINE.md`
15. `docs/knowledge_spaces/IMPLEMENTATION_BACKLOG.md`
16. `docs/knowledge_spaces/TRACEABILITY.md`
17. `docs/knowledge_spaces/GOAL_CONTRACT.json`
18. `docs/knowledge_spaces/STATUS.md`

If goal tooling is available, inspect the active goal before planning a change. Do not replace or mark that goal complete until every completion gate in `TEST_OPERATIONS.md` and `TRACEABILITY.md` is satisfied.

## Non-negotiable product decisions

- Organization hierarchy is `담당 > 팀 > 파트`; a user has at most one active part membership.
- Canonical v7 remains the current disabled implementation baseline until the
  successor packet that owns a behavior is verified. The approved target is the
  `second_brain_evolution/` package; do not implement its features by weakening
  v7 constraints in place.
- A user may own any number of personal Wikis and has exactly one active default.
  Existing personal space identity is preserved. Wiki archive is explicit,
  reversible, and distinct from item/conversation archive and purge.
- `personal + Part` publication creates two independent items and revision
  histories in one transaction. Later edits never synchronize automatically.
- One Agent conversation is bound to exactly one selected Wiki/view. Changing
  selection starts a new conversation. A higher selected view may resolve to a
  server-owned multi-space `CorpusClosure`; cache/client input never enlarges it.
- Every active Part member may create, edit, deactivate, restore, and perform
  reviewed curation while Part writes are enabled. Force-release authority is
  exact to the personal owner, Part leader, Team leader, or 담당 executive for
  that native space; a system administrator uses only the separately audited
  break-glass path. Administrator status alone grants no ordinary authorship.
- Team and 담당 have native canonical spaces. All active descendants may read
  the authorized aggregate view; only the exact current Team leader or 담당
  executive may write that level's native documents. Inherited documents remain
  read-only and retain their real source-space IDs.
- Deletion is reversible deactivation. Permanent purge is administrator-only, never automatic, and is only eligible 90 days after the latest deactivation.
- Item deactivation never changes the stored state of graph edges. Effective visibility is computed from edge state and endpoint item state.
- Part graphs remain isolated. Cross-Part/cross-Team semantic edges are stored
  once at their lowest common Team/담당 ancestor and are visible only in the
  appropriate aggregate views.
- Every semantic edge that passes the frozen bounded-candidate, LLM-judge, and
  deterministic evidence/revision/authorization/LCA validation activates
  automatically. Model output never automatically merges, replaces,
  deactivates, purges, or declares a conflict winner.
- Retrieval executes one authorized lexical plan over the complete selected
  personal/Part/Team/Responsibility closure. Direct and final body lanes have no
  fixed document-count cap and never pad irrelevant results; graph expansion
  remains hop/work bounded. Separately labeled authorized pins/stable references
  may add at most four mandatory documents. Mandatory plus ranked/supplemental
  excerpts share an exact 96 KiB model-context budget with deterministic
  truncation disclosure. Optional local reranking may reorder only an authorized
  candidate set and always has a deterministic fallback.
- Markdown page timestamps are projection/UI metadata. They never enter the
  canonical body, content hash, FTS, deduplication, semantic analysis, reranker
  text, or Agent excerpt.

## Engineering invariants

- The central SQLite database is canonical for organization, spaces, items, immutable revision metadata and separately purgeable revision bodies, relations, edges, audit, leases, idempotency, curation findings, and the durable outbox.
- Markdown, `index.md`, `log.md`, graph JSON, backlinks, and search/vector caches are replaceable projections. Do not make a projection the only copy of state.
- All mutations run through service-layer transaction contracts. Route handlers and background jobs must not write canonical tables or projection files directly.
- A valid edit save requires current owner, active membership, or exact open
  role-episode authority revalidation as applicable to the physical source
  space, plus lease token, monotonically increasing fencing generation, and
  expected item revision/row version.
- LLM, OCR, file copying, embedding, and network work must run outside database transactions. Revalidate inputs immediately before commit.
- Every retryable mutation has a caller-supplied `operation_id`; lease heartbeat is the sole phase-1 exception because it is a conditional renewal of the same lease. The same mutation ID and request hash must return the original result, while an ID reused with a different request must fail.
- Treat the existing curation risk tiers as an execution boundary: T0/T1 may run
  only their documented deterministic effects, existing T2 destructive/conflict
  workflows still require a human reviewer, and T3 is never model-executed. The
  v10 `semantic_auto` edge effect is a separate, narrowly validated successor
  contract; it must not be used to bypass merge/replacement/deactivation/purge
  review.
- Audit and outbox records commit in the same transaction as the mutation they describe.
- Do not dual-write legacy files and the new canonical database. Use the documented offline cutover and generated compatibility projections.
- Do not enable successor reads/writes until the owning SBE packet and rollout
  dependency gates in `IMPLEMENTATION_BACKLOG.md` are satisfied.

## Safe development rules

- Keep this file as the single instruction file for `code_assist_v1/`. Do not
  create a nested or case-variant `AGENTS.md`/`agents.md` for SBE work.
- Never use live `knowledge/`, `user_data/`, `.runtime/`, or per-user Notion databases as test fixtures or writable test targets. Copy sanitized fixtures into a temporary directory.
- Never run the real migration, purge, membership reassignment, or production feature-flag transition without explicit user authorization. Dry-run and read-only inventory are safe.
- Preserve existing personal knowledge, Notion, chat, and authentication behavior behind compatibility adapters until their replacement phase passes regression tests.
- Keep migrations forward-only, checksumed, idempotent where applicable, and executable by one deployment migrator rather than on every request/connection.
- Make small phase-scoped changes. Each packet writes only its exact evidence
  file; the predeclared status/contract integrator updates `STATUS.md`, relevant
  traceability rows, and other hot shared contracts for the phase gate.
- Treat `SCHEMA_V1.md` and completed v1-v7 migrations as historical physical
  contracts. Never edit them for successor behavior. Implement v8-v13 only with
  forward migrations and update the catalog checksum, API contract, tests,
  traceability, and SBE evidence in the same reviewed packet.
- Keep `GOAL_CONTRACT.json`, traceability goal rows, and the generated mandatory-
  scenario count synchronized. SBE-0.1 registered the exact active union of 20
  goals and 284 scenarios (the frozen 14/124 baseline plus 6/160 additions).
  Any partial update, count/ID drift, or report-schema-v2 audit failure blocks
  later packet completion.
- If implementation reveals a conflict with an accepted decision, stop that
  conflicting change and record it. For target behaviors explicitly listed in
  the SBE supersession ledger, follow the active SBE packet; preserve all
  unrelated v7 decisions.

## Validation discipline

- Use the test IDs in `TEST_OPERATIONS.md` in test names or markers so evidence remains traceable.
- Test real cross-process behavior for locking, publication, job claiming, backup, and recovery; thread-only tests are insufficient.
- Record measured p50/p95/p99 latency, SQLite busy counts, queue/projection lag, and restore timing on the target Windows server.
- A phase is complete only when its documented gate passes. “Implemented” without gate evidence is still in progress.
