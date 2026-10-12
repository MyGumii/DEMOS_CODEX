# Accepted decisions

These decisions are the implementation baseline. A later change requires an explicit decision update and user confirmation when it changes product behavior.

Successor contract state: **registered by SBE-0.1**. ADR-017 through ADR-024
and the exact supersession ledger in
`second_brain_evolution/EXECUTION_PLAN.md` are the authoritative target. Older
ADR clauses remain only as successor-flags-off compatibility behavior where the
ledger explicitly retains them; runtime activation still requires each packet.

## ADR-001 — Generic organization hierarchy

Status: accepted.

- Model stable organization units with kinds `responsibility`, `team`, and `part`.
- Valid parent chain is responsibility root → team → part.
- Seed `AMHS담당` as the initial responsibility root.
- Store one historical part-membership stream per user and enforce at most one active membership.
- An active account may intentionally have no active part membership; it then has personal-space capability only. This is a deliberate product rule, not an unresolved migration state.
- Derive a user's team and 담당 from the active part rather than storing three independently editable memberships.
- Store part leadership separately from system-administrator authority.

Reason: stable IDs survive rename/move operations and prevent inconsistent team/part assignments.

## ADR-002 — One canonical SQLite database

Status: accepted for the single-server, approximately 40-user deployment.

- One central database owns organization, users/audit, spaces, artifacts, publications, items, immutable revision metadata plus separately purgeable revision bodies, relations, edges, leases, findings, operation idempotency, durable jobs/outbox, and projection generations.
- Every space-owned canonical row includes or can be unambiguously joined to `space_id`.
- SQLite runs in WAL mode on local NTFS SSD/NVMe with the exact production runtime/source-ID pinned by `SCHEMA_V1.md`, foreign keys, busy timeout, and short transactions.
- Repository boundaries must allow a future PostgreSQL implementation without changing route or domain contracts.

Reason: a database per space cannot atomically publish to personal+part, and a lock database layered over mutable files cannot prevent cross-process lost updates.

## ADR-003 — Files are projections, not canonical state

Status: accepted.

- Markdown text is retained in immutable `knowledge_revision_body` rows referenced by immutable revision metadata. An eligible administrator purge may remove only the body row while preserving revision identity, hashes, provenance, and audit.
- Large/raw binary artifacts are immutable content-addressed blobs with database references.
- Artifact/extraction identity, hashes, and provenance remain immutable, but an eligible administrator purge may remove unreferenced body/link rows; a later GC deletes a physical blob only after rechecking every surviving item, preview, conversation snapshot, proposal, and backup hold.
- A terminal source preview that never became knowledge uses a separate 30-day zero-reference body cleanup; it preserves source metadata/hashes and never shortens the 90-day knowledge-purge policy.
- `sources/*.md`, `index.md`, `log.md`, graph/backlink JSON, search/vector caches, and exports are versioned projections.
- A projection can be deleted and rebuilt without losing canonical content, history, or graph state.
- Agent retrieval reads canonical DB/FTS data and never requires a projection to be current.

## ADR-004 — Independent dual publication

Status: accepted.

- A publication batch with target `both` creates or resolves one target outcome in the personal space and one in the current-part space in a single transaction.
- New items have different IDs and independent current revisions.
- They share source provenance and a lineage group only; `sync_policy=none` is permanent unless a future explicit feature changes it.
- Later personal-to-part change proposals use fork-base, personal-current, and part-current revisions for a three-way comparison.

## ADR-005 — Conversation scope is immutable

Status: accepted; extended by ADR-018 for multi-space aggregate views.

- Each Agent conversation stores one concrete `knowledge_space_id` and membership/scope epoch.
- The server resolves and authorizes the space; a client-supplied ID is never trusted by itself.
- Switching personal ↔ part creates a new conversation. Existing messages, compact summaries, pins, and retrieval caches remain bound to the original space.
- A membership move immediately invalidates old part sessions for retrieval and mutation.

## ADR-006 — Collaborative authority without approval bottleneck

Status: accepted.

- Every active part member may create, edit, deactivate, restore, and carry out reviewed curation in that part space.
- Part leaders and system administrators may force-release a lock; ordinary members may release only their own lock.
- Disabling/moving/unassigning the current leader must atomically appoint an eligible replacement or disable writes and close that leadership; it cannot leave a write-enabled leaderless part.
- System administrators alone manage the hierarchy and execute eligible permanent purge.
- Batch deactivate/merge is disabled initially to limit accidental damage.

## ADR-007 — Exclusive fenced editing

Status: accepted.

- An item has at most one live edit lease.
- Lease validation requires holder, session, secret token hash, monotonically increasing fencing generation, expiry, absolute expiry, and expected base row/revision.
- Save revalidates current membership and item state and uses revision/row-version compare-and-swap even when the lease is valid.
- A stale editor receives a conflict and retains a recoverable draft; stale writes never overwrite current content.

## ADR-008 — Reversible deactivation and stable graph identity

Status: accepted.

- Deactivate and restore are separate commands, never a toggle.
- Deactivation changes item lifecycle state and records actor, date, reason, and purge eligibility.
- It does not change stored edge state or delete graph nodes.
- Effective graph visibility is `edge active AND source item visible AND target item visible`.
- Restore preserves item, revision, relation, and edge IDs.
- Ninety days means administrator purge eligibility, not an automatic timer.
- Purge keeps a tombstone, audit identity, and redirect/provenance required by surviving records.

## ADR-009 — Conservative curation automation

Status: accepted for deterministic/destructive curation; its human-approval
requirement for semantic-edge activation is superseded by ADR-020 after the v10
automatic-edge gate is verified.

- Preserve each ingestion/source-artifact event even when logical knowledge is deduplicated.
- Same-origin same-revision requests are idempotent no-ops; exact same-space content adds provenance rather than another active item.
- An exact item may still exist independently in different spaces.
- A normalized hash is candidate evidence, not sufficient for automatic deduplication.
- Strong near-duplicate, update, or potential-conflict findings are provisional and excluded from Agent search until reviewed.
- `provisional`/Agent-hidden is a document-revision curation state. A pending,
  failed, rejected, or activated `semantic_auto` edge job never changes item
  curation status, FTS membership, or Agent visibility.
- Weak similarity/complementary documents may publish with a warning.
- Item curation status changes only through the matched finding/rescan transaction with audit, outbox, FTS, and visibility updates; no generic status patch is allowed.
- ADR-009 automatically commits only deterministic provenance and no-blocker
  document-curation effects. ADR-020 separately permits a validated semantic
  edge to activate without human approval. A finding that proposes a document
  update, replacement, merge, quarantine, or conflict winner remains T2 and
  requires an authenticated human reviewer.
- LLM never automatically merges, replaces, deactivates, or purges knowledge.
- Risk tiers are explicit and separate from severity: T0 structural/source
  checks and T1 deterministic provenance/no-blocker-release effects may be
  automatic within their narrow non-LLM contract; T2 document update/
  replacement/merge/conflict resolution requires a human reviewer, with
  validated semantic-edge activation as the narrowly bounded ADR-020 exception;
  T3 destructive, organization, and security-sensitive actions are never
  model-executed.

## ADR-010 — Provenance graph and semantic graph remain distinguishable

Status: accepted.

- Source, fork, promotion, exact-duplicate, merge, and redirect relations are provenance/curation relations.
- Domain meaning such as `supports`, `contradicts`, or `depends_on` is a semantic edge.
- Retrieval can traverse semantic edges without treating publication lineage as domain meaning.
- A significant revision marks affected semantic edges for revalidation rather than silently moving or deleting them.

## ADR-011 — Auth/user registry and audit move behind the core repository

Status: accepted implementation requirement before part writes.

- Migrate the JSON user registry and JSONL audit stream to DB-backed repositories while preserving cookie-session behavior and password hashes.
- Account deletion becomes disable-first. Canonical audit actors keep a display snapshot and do not cascade away.
- Membership moves, account disablement, lease revocation, and scope epoch changes can then commit consistently.
- Compatibility reads may exist during migration, but there is no long-lived JSON+DB dual-write mode.

## ADR-012 — Durable worker and single inference coordination

Status: accepted.

- Web processes enqueue durable jobs/outbox events and do not own authoritative in-memory job state.
- One worker service atomically claims work with a renewable lease and idempotent result commit.
- GPU/LLM execution is centrally coordinated so `app_code` and `app_notion` cannot independently overload the GPU.
- Restart recovery requeues expired work and resumes unfinished projections without duplicate commits.

## ADR-013 — Offline, report-driven legacy cutover

Status: accepted.

- Dry-run inventory and reconciliation precede migration.
- A short maintenance window performs the final snapshot and idempotent import.
- Existing user wiki Markdown becomes personal knowledge only; nothing is auto-published to a part.
- Existing derived index/log/SQLite files are not imported as canonical data.
- Unowned legacy root data, orphan edges/assets, and hidden candidates are quarantined or reported for administrator resolution.
- Avoid long-lived legacy/new dual-write; switch through feature flags after reconciliation.

## ADR-014 — Team/담당 use read-only Part inheritance

Status: historical/current-v7 preview decision. Its current-Part-only alias and
no-native-write clauses are superseded by ADR-018 after the corresponding v9
packets are verified; production rollout remains gated.

- Personal and current-Part are the only direct publication targets.
- Selecting Team or 담당 resolves a read-only inherited view of the actor's
  current Part corpus. The selected higher-space ID is preserved in the API
  response, while item, activity, search, Index, Log, and Graph data are read
  from the authorized current-Part source.
- Inheritance is a live view, not a second editable copy. A Part revision,
  deactivation, or restoration is therefore reflected immediately at Team and
  담당 without a cross-space dual write.
- Each higher selection has its own UI navigation and selected-scope Index,
  Log, Graph, and projection-status response. It exposes read only; it cannot
  publish, edit, deactivate, restore, or curate.
- `personal + Part` remains two independent canonical items. Editing the
  personal copy never mutates the Part item or its inherited higher views.

## ADR-015 — Production and recovery gates

Status: accepted.

- Flask development servers are not a production deployment.
- Both web apps run behind production WSGI services; the worker/inference coordinator is a separate service.
- Live SQLite backup uses the online backup mechanism or an equivalent consistent snapshot and includes an attachment manifest.
- Backups are stored off-machine and proven by restore drill; the 4 TB server disk is not itself a backup.
- Part writes remain disabled until migration, scope, concurrency, audit, restore, and performance gates pass.

## ADR-016 — Roll-forward-only after the first canonical production write

Status: accepted for the phase-1 approval packet.

- No canonical-delta exporter is part of the phase-1 implementation.
- Before the first canonical production write, operators may still restore the
  verified pre-write backup and return to the frozen legacy source.
- After the first canonical production write, recovery is explicitly
  roll-forward-only from verified Knowledge Core backups. Legacy data is never
  replayed over newer canonical state.
- P8.1 must copy this decision and the exact P7.4 evidence hash into the signed
  deployment approval packet. Changing this decision requires a separately
  implemented, verified, and replay-tested canonical-delta exporter before
  personal writes are enabled.

## ADR-017 — Personal knowledge is an unbounded Wiki collection

Status: accepted target; activated only through SBE v8 gates.

- A user may own any number of personal Wikis; no product-level numeric count
  limit is added.
- Exactly one active personal Wiki is the default. The existing personal space
  keeps its ID and becomes the migration default.
- Rename is metadata-only. Archive is explicit, reversible, and distinct from
  item deactivation, conversation archive, operational freeze, and purge.
- The default and last active Wiki cannot be archived until another active
  default exists.
- Archived Wikis are excluded from ordinary search/write/jobs but remain
  history-readable and restorable with the same IDs.
- Ordinary create/rename/default/archive/restore is owner-only. Archived Wikis
  appear in a separate bounded management list/search that never feeds active
  Agent retrieval; administrator status alone is not a lifecycle override.
- Capacity is bounded by cursor pagination, one owner-filtered global search,
  storage/upload/job budgets, and lazy projections rather than a Wiki-count cap.

Reason: users need independent topic corpora and stable conversation context,
while arbitrary count fan-out would make an otherwise harmless metadata feature
an operational denial-of-service vector.

## ADR-018 — Native higher spaces, leadership episodes, and CorpusClosure

Status: accepted target; supersedes ADR-014 target behavior after v9/SBE gates.

- Every Team and 담당 has a native canonical space.
- Team view is Team native plus every descendant Part. 담당 view is 담당 native,
  every descendant Team native space, and every descendant Part.
- Personal Wikis are never implicitly included in organizational closure.
- All active descendants may read their current ancestor closures. Only the
  exact open Team-leader or 담당-executive episode may write that native higher
  space; one person may hold multiple independent roles.
- A selected Agent view remains immutable per conversation, but the server may
  resolve it to a versioned multi-space `CorpusClosure`.
- Responses retain actual source space IDs and inherited/read-only capability;
  a higher view never rewrites ownership or copies descendant bodies.
- Role replacement, disable, or organization move fences stale previews,
  leases, jobs, and commits by episode/generation.
- Higher-native downward exposure is an explicit audited read-only distribution
  grant, not implicit copying.

Reason: leadership needs a writable knowledge layer at its actual organization
level, while descendants need a coherent aggregate without sacrificing source
ownership or isolation.

## ADR-019 — Aggregate graph edges live at the lowest common ancestor

Status: accepted target; activated only through v10/SBE graph gates.

- Existing `knowledge_edge` remains same-space and Part graphs remain isolated.
- Cross-Part/same-Team edges live once at Team. Cross-Team/same-담당 edges live
  once at 담당. Native-higher/descendant edges live at that higher ancestor.
- Team/담당 graphs compose descendant local clusters and applicable home edges;
  they do not copy child Markdown or duplicate one edge at every ancestor.
- Endpoint revision, visibility, closure, and organization ancestry are
  revalidated. A move/edit/deactivation hides or marks affected edges for
  revalidation before use.
- Aggregate projection identity binds a sorted child generation vector and home
  edge generation.

Reason: cross-organizational relationships are useful at the level where both
branches meet, but must not leak into or mutate a Part's independent graph.

## ADR-020 — Validated semantic edges activate automatically

Status: accepted target; supersedes only ADR-009's human-click requirement for
semantic-edge activation after the automatic-edge gate is verified.

- Only the transition of the current revision to active/published after the
  versioned document-curation gate appends bounded, handler-independent local and
  ancestor `semantic_edge_intent` rows plus the ordinary publication outbox in
  the same transaction. Initial provisional publication appends no intent. It
  creates zero semantic `durable_job` rows until the exact handler is registered
  and its feature flag is effective. The later bounded materializer maps each
  flag-eligible unjoined intent to exactly one queued known-handler durable job,
  with at most one job per immutable effect key; it never repeatedly skips a
  stale intent. Workers close stale/invalid input with a deterministic no-edge
  terminal record and perform model/job execution only after commit, outside the
  publication transaction. A generic projection-outbox acknowledgement never
  consumes or deletes the intent.
- Before automatic-edge flags become effective, a bounded, restartable seed run
  covers every pre-existing current active/published revision. The same runner
  creates new scoped intents for an organization retarget and a new detector
  generation. Item/Wiki restore increments its source-lifecycle vector and feeds
  an affected-item/Wiki reactivation seed, so a terminal decision from the old
  archived/inactive generation cannot suppress reevaluation. Intent provenance
  is a closed
  `publication|baseline_backfill|org_retarget|detector_reseed|lifecycle_reactivate`
  union; a seed-run proof is never presented as a same-transaction publication
  outbox proof.
- An LLM judge may choose only allowlisted candidates and a frozen relation
  vocabulary, and must return evidence bound to fixed revisions.
- A deterministic validator rechecks evidence, revisions, authority, LCA,
  thresholds, duplication, suppression, and effect-key idempotency immediately
  before commit.
- Every passing edge activates automatically; malformed, stale, suppressed, or
  low-confidence output activates nothing.
- Model/prompt/scoring versions, evidence and decision outcomes are durable.
  Users can correct/suppress an edge, and suppression prevents automatic
  recreation by the same detector generation.
- Model execution never merges, replaces, chooses a conflict winner,
  deactivates, purges, or changes organization/permissions.

Reason: mandatory approval of every relationship makes the graph unusable at
normal ingestion volume, while bounded candidates plus deterministic commit
validation keep model authority narrow and reversible.

## ADR-021 — Retrieval is authorized-full-corpus, origin-preserving, and byte-bounded

Status: accepted target.

- Retrieval consumes a server-authorized `CorpusClosure`; a client or reranker
  cannot add scopes or candidate IDs.
- One direct FTS query scans every lexical match in the selected authorized
  personal/Part/Team/Responsibility closure without candidate-count truncation.
  Graph expansion stays bounded one-hop with conditional bounded two-hop only
  when stage one is insufficient. The final `ranked_documents[]` and authorized
  body-evidence lanes have no fixed document-count cap and never add irrelevant
  padding.
- Separately labeled authorized pins/stable references may add at most four
  mandatory documents. Mandatory plus ranked/supplemental excerpts share an
  exact 96 KiB model-context budget, with deterministic per-document clipping
  and coverage/truncation disclosure.
- An explicit all-personal-Wiki management search uses one active-owner-filtered
  query, never one query/model call per Wiki. It does not enter Agent context;
  opening a result starts/opens that exact Wiki-bound conversation, and every
  normal personal conversation searches only its bound Wiki.
- Deterministic ranking and fallback are always available. An optional local
  multilingual cross-encoder may reorder only the authorized set; input-bound
  overflow falls back without dropping authorization or failing the Agent turn.
- Freshness materially affects temporal questions only. Newer content does not
  automatically win a conflict.

Reason: final context quality benefits from graph evidence and local reranking,
but correctness, authorization, and capacity cannot depend on an external model
or the number of Wikis.

## ADR-022 — Temporal information is metadata, not Markdown body

Status: accepted target.

- Every page footer renders `생성` from item creation, `마지막 내용 수정` from
  the current revision creation, and `Revision` from the current positive
  `revision_no`; item aggregate update or projection time is never substituted.
- `원문 수정`, `정보 기준`, and `마지막 검증` are optional rows rendered only
  from a current evidence-backed assertion or attestation. An absent, revoked,
  superseded, or non-current value omits its row instead of rendering `미지정`
  or `미검증`.
- Source-modified/effective-time assertions and exact-revision verification are
  typed, evidence-bound, append-only, supersedable/revocable metadata. They are
  not guessed from upload time or file mtime.
- A verification attestation also binds the current temporal-summary hash;
  changing a temporal head makes it non-current. Head changes use CAS, and a
  revoke never silently falls back to a superseded value.
- UI and projection render the footer; canonical body, hashes, FTS, dedupe,
  embeddings, semantic judgment, reranker text, and Agent excerpts exclude it.
- Projection manifest binds the exact LF-normalized UTF-8 prefix boundary/hash
  separately from rendered footer/file hash. Re-import strips a footer only with
  a canonical-ledger/root-bound manifest or an approved signed export envelope,
  never a self-declared adjacent file.
- UTC is canonical and UI uses explicit Asia/Seoul rendering. A new revision
  does not inherit the prior revision's verification.
- Every rendered footer value is exact metadata rather than a wall-clock-
  relative status; omitted optional rows represent absence without fabricating
  a value. Temporal-only events advance a separate generation and immutable
  projection.

Reason: visible dates aid freshness comparisons, but putting generated dates in
content would create false revisions, search noise, dedup drift, and model bias.

## ADR-023 — Canonical upload parity precedes semantic enrichment

Status: accepted target.

- The canonical flow is raw CAS, versioned rich extraction and assets,
  immutable preview, explicit target canonical publication, versioned document-
  curation gate, published-only FTS/node visibility, asynchronous semantic jobs,
  then projection.
- Content becomes Agent-searchable in the same transaction in which its
  versioned document-curation gate reaches `published`. The deterministic no-
  blocker release needs no human reviewer; a strong document-curation finding
  remains provisional and Agent-hidden under ADR-009.
- Automatic semantic-edge enrichment starts only from active/published
  revisions. Its pending or failed state never changes item curation visibility,
  and a model failure is not an upload failure.
- New writes target canonical DB state. Markdown/Index/Log/Graph files are
  browse/download projections, not an editable second truth.
- Windows staging works under documented NTFS service-account `Modify` rights
  without unnecessary delete-child permission while preserving no-follow,
  identity, anti-race, and no-source-write guarantees.

Reason: a database transition must preserve the user's simple upload-to-
knowledge experience without reviving unsafe dual writes or making model
availability a publication prerequisite.

## ADR-024 — Explicit references are deterministic and separate from Graph

Status: accepted target.

- Author-created stable item references use exact syntax
  `[[item:<lowercase-uuid>]]` (`item-ref-v1`); title-only resolution is forbidden.
- They are stored and rendered as an ordered `참조 문서` list, not as local or
  aggregate semantic edges, and do not change graph degree/confidence.
- Publication binds source revision/ordinal and the target visible in the
  server-resolved closure. A forged or concealed target commits nothing and
  discloses nothing.
- Retrieval may add an authorized reference as bounded structured context, but
  it cannot recursively traverse without a separate bound or enlarge scope.

Reason: explicit author intent should be durable and easy to navigate without
being confused with model-inferred semantic relationships or creating a new
cross-scope authorization path.
