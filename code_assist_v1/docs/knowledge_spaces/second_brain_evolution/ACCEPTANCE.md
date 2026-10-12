# Second Brain evolution acceptance contract

Status: registered mandatory successor matrix.

Every value below is active in the report-schema-v2 manifest and remains the
exact approved value, not an implementation-Agent choice. Registration does not
claim that its owning runtime packet has passed.

These 160 IDs are combined with the frozen 124-scenario v7 table to form the
active total of 284 across `TEST_OPERATIONS.md`, traceability, backlog, goal,
OpenAPI/JSON Schemas, and `contract-manifest.json`. Never remove or update only a
subset of that registered union.

The successor delta is exact and machine-audited:

| Family | Inclusive range | Count |
|---|---|---:|
| AGRAPH | `AGRAPH-001..017` | 17 |
| AUTOEDGE | `AUTOEDGE-001..019` | 19 |
| HSPACE | `HSPACE-001..018` | 18 |
| RANK | `RANK-001..021` | 21 |
| REF | `REF-001..008` | 8 |
| SBEOPS | `SBEOPS-001..018` | 18 |
| TIME | `TIME-001..022` | 22 |
| UPLOAD | `UPLOAD-001..015` | 15 |
| WIKI | `WIKI-001..022` | 22 |
| **Total** | — | **160** |

## 1. Evidence rules

- Every mandatory ID appears in at least one executed test or explicit
  target-operation gate and maps to one owning packet.
- Unit mocks cannot prove a cross-process, Windows handle, DB migration,
  browser, backup, or target-server requirement.
- Tests use disposable roots/DBs under the approved test directory. Live
  `knowledge`, `user_data`, `.runtime`, Notion DBs, production ports, and company
  API are outside local test authority.
- Every rejection proves no partial canonical rows, files in source roots,
  audit/outbox mismatch, or leaked concealed identifier.
- Total validators are tested against arbitrary JSON-shaped input and never
  raise an unhandled type/key/hash exception.

## 2. Personal Wiki and conversation scenarios

- **WIKI-001** — Sequential v7->v8 upgrade preserves every existing personal
  space/item/revision/conversation ID and body hash, and makes the existing
  personal space the exact active default with lifecycle generation 1.
- **WIKI-002** — Pristine v8 and upgraded v8 have identical physical
  fingerprint/postconditions; failed and repeated upgrades are rollback/no-op.
- **WIKI-003** — The old one-personal-space owner index is absent, the active-
  default uniqueness/indexes are exact, and there is no Wiki-count CHECK,
  trigger, service constant, or UI cap.
- **WIKI-004** — Invalid owner/profile/default/archive tuples, duplicate
  normalized names across active or archived profiles, lifecycle-generation
  skips/non-lifecycle changes, display/key drift, unknown schema objects, and
  tampered migration state fail closed.
- **WIKI-005** — One owner creates more than ten Wikis; IDs/default remain
  deterministic and independent, with exact audit/outbox/idempotent replay; the
  request cannot choose another owner.
- **WIKI-006** — Active and separate archived-management list/search use opaque
  owner/filter/lifecycle-bound cursors, default limit 50 and maximum 100, stable
  ordering, and no full-materialization endpoint; archived results/counts never
  enter active search or Agent retrieval.
- **WIKI-007** — Owner-only rename changes only display/key profile metadata and
  row version; foreign/admin-role-only attempts have zero effects and all item,
  revision, graph, conversation, and projection identity remains stable.
- **WIKI-008** — Owner-only set-default is one transaction with exactly one
  active default; stale CAS, foreign/admin-role-only caller, duplicate operation
  drift, and concurrent winners fail deterministically.
- **WIKI-009** — Explicit owner-only archive preserves data/IDs, removes the Wiki
  from active list/search/upload/edit/auto-job selection, and creates no delete/
  purge; administrator role alone and every foreign caller have zero effects.
- **WIKI-010** — Current default and last active Wiki archive are rejected with
  zero effects; archive succeeds after another active default exists.
- **WIKI-011** — Restore reuses the same Wiki/graph/conversation identity, advances
  exactly one Wiki lifecycle generation with a generation-bound canonical event,
  and does not make it default unless explicitly requested atomically.
- **WIKI-012** — No scheduler or age policy archives a Wiki; operational frozen/
  inactive state and item/conversation archive remain distinct.
- **WIKI-013** — Conversation creation binds the exact server-authorized Wiki or
  selected view; a forged/foreign/archived-normal-mode ID is concealed/rejected.
- **WIKI-014** — Opening a saved session auto-selects the stored Wiki even when
  browser cache/current dropdown differs; cache deletion changes no authority.
- **WIKI-015** — Switching Wiki/view creates a new conversation; messages,
  summaries, pins, citations, and retrieval cache of the old session never move.
- **WIKI-016** — Archived-Wiki sessions open history read-only, cannot accept a
  turn, and can continue after explicit restore with current authorization.
- **WIKI-017** — Role/membership/lifecycle/closure generation changes invalidate
  stale turns and caches before retrieval or commit.
- **WIKI-018** — UI tree order is 내 지식, Part, 팀, 담당; personal Wikis are
  keyboard/screen-reader accessible expandable folders with lazy children.
- **WIKI-019** — UI supports owner create/rename/default/archive/restore, a
  separate archived list/search, and the active-only `모든 내 Wiki 검색`
  management action; opening its result starts/opens the exact Wiki-bound
  conversation, and stale optimistic state never becomes authority.
- **WIKI-020** — 1,000-Wiki UI list remains virtualized/paginated; initial DOM,
  requests, memory, and worker count do not scale to 1,000.
- **WIKI-021** — Session reopen highlights the exact active or history-only Wiki
  and displays a clear restore/new-conversation action when archived.
- **WIKI-022** — Legacy client `personal` compatibility resolves only the current
  default; new clients always send explicit `space_id` and never guess by name.

## 3. Higher-space authority and closure scenarios

- **HSPACE-001** — v9 pristine/upgrade schemas have exact higher-role episode,
  native-space, distribution, index, trigger, FK, and postcondition fingerprints,
  including exactly one native space for every active Part/Team/Responsibility
  after migration regardless of rollout flags.
- **HSPACE-002** — Existing user/org/Part/native IDs and v1 part-leader history
  are preserved; repeated/failed upgrade is no-op/rollback.
- **HSPACE-003** — Role kind must match org kind. Under canonical v13 the
  assignee is active, has an exact direct home at that Team/Responsibility, and
  has no simultaneous open Part membership; foreign-kind, differently homed,
  multiply affiliated, and inactive assignments fail with zero effects. The
  earlier descendant-Part wording is historical v9-v12 behavior only.
- **HSPACE-004** — At most one open Team leader and one open Responsibility
  executive per unit; each assignee has at most one open organization home, so
  Team leaders and Responsibility executives are distinct from Part members
  unless an explicit future reassignment first closes the current home.
- **HSPACE-005** — A write-enabled higher native space has exactly one matching
  open episode; disable/move/replacement is atomic and ABA generation changes.
- **HSPACE-006** — Personal closure is exact Wiki only; Part base closure is
  exact Part plus only explicitly granted higher-native item overlays; neither
  includes a whole higher space, another personal Wiki, or sibling/foreign Part.
- **HSPACE-007** — Team closure is Team native plus all and only active descendant
  Parts, independent of which descendant Part the actor belongs to.
- **HSPACE-008** — Responsibility closure is Responsibility native, descendant
  Team native spaces, and all descendant Parts, with no foreign branch.
- **HSPACE-009** — Ordinary active descendants read their ancestor closures but
  cannot publish/edit/deactivate/restore/curate/temporally attest higher-native
  items, mutate distributions, force-release higher leases, or correct higher-
  home edges.
- **HSPACE-010** — Resolver emits the closed named publish/edit/deactivate/
  restore/curate/temporal/local-edge/home-edge/distribution/force-release
  capabilities only for the exact matching current Team leader/executive episode;
  Part leader, endpoint editor, administrator role alone, or old role episode
  receives none. Each later owning service must revalidate its emitted capability
  immediately before effect and is gated in its own scenario family.
- **HSPACE-011** — Preview/lease/job created under an old episode fails after
  replacement/disable/move even if the same user is reassigned later.
- **HSPACE-012** — Every aggregate response preserves real source space, selected
  view, inherited/native/read-only capabilities, and conceals unauthorized IDs
  in data, count, error, cache, and timing-oriented fixtures.
- **HSPACE-013** — Downward distribution is explicit, read-only, revision-bound,
  auditable, ancestry-checked, and creates no descendant item/body copy; only the
  current source-native leader/executive can grant or ordinarily revoke, while
  an administrator can only perform a confirmed audited break-glass revoke.
- **HSPACE-014** — Org move invalidates closure/distribution/jobs/caches and the
  union of old/new ancestor projections before use, immediately conceals former
  ancestry, and rebuilds only those affected summaries.
- **HSPACE-015** — UI exposes each named native/edge/distribution/lease capability
  only to the current role episode and clearly marks inherited/overlay rows read-
  only with origin labels; an endpoint capability never enables a home-edge action.
- **HSPACE-016** — Open higher editor loses save capability immediately after
  role replacement; draft remains recoverable and no stale write commits.
- **HSPACE-017** — UI never offers personal+Team/Responsibility implicit copying;
  first release higher publication is an explicit single native target.
- **HSPACE-018** — Organization-admin role controls, break-glass force-release/
  organizational-edge suppression/distribution revoke/purge, and write-disable
  reason are understandable, confirmed, auditable, and cannot grant ordinary
  personal/higher authorship or leave a write-enabled leaderless unit.

## 4. Aggregate graph scenarios

- **AGRAPH-001** — v10 pristine/upgrade fingerprints and all aggregate endpoint,
  LCA, actor, evidence, uniqueness, suppression, and generation constraints pass.
- **AGRAPH-002** — Existing same-space `knowledge_edge` rows/FKs/visibility remain
  unchanged; v10 cannot weaken or reinterpret historical edges.
- **AGRAPH-003** — Invalid/noncanonical/personal/cross-branch/no-LCA endpoint rows,
  unknown relation types, mutable identities, and duplicate homes fail closed.
- **AGRAPH-004** — Failed/repeated v10 migration rolls back/no-ops without helper,
  ledger, trigger, or schema residue.
- **AGRAPH-005** — Same-Part endpoints create/read only a Part-local edge.
- **AGRAPH-006** — Cross-Part same-Team endpoints store one Team-home edge; neither
  Part graph/checksum/generation changes because of that edge.
- **AGRAPH-007** — Cross-Team same-Responsibility endpoints store one
  Responsibility-home edge and no Team/Part duplicate.
- **AGRAPH-008** — Native Team/Responsibility-to-descendant endpoints home at the
  exact higher LCA; unrelated/foreign pairs are rejected.
- **AGRAPH-009** — Team graph composes Team native/local Part clusters/Team-home
  edges only; Responsibility graph additionally composes descendant Team-home
  and Responsibility-home edges.
- **AGRAPH-010** — Graph responses preserve endpoint origin spaces and inherited
  state; layout/dedup never merges canonical identities.
- **AGRAPH-011** — Endpoint revision/deactivation/archive/org move makes affected
  edges invisible or needs-revalidation before the next read/Agent context.
- **AGRAPH-012** — Cycle, symmetric/directional duplicate, self-edge, high-degree
  hub, and repeated effect-key cases remain bounded/idempotent.
- **AGRAPH-013** — Ordinary descendant mutation updates its physical/current
  ancestors; org move updates the union of old/new ancestor generation targets;
  neither path scans/fans out to all spaces.
- **AGRAPH-014** — Projection rebuild from the same vector is byte-identical and
  does not copy descendant Markdown into a higher native directory.
- **AGRAPH-015** — Part UI never displays aggregate cross-Part/Team edges.
- **AGRAPH-016** — Team/Responsibility graph UI shows cluster/home/type/evidence/
  confidence/origin without leaking foreign branches.
- **AGRAPH-017** — Organization move/correction UI presents revalidation state and
  never silently relocates/duplicates an edge.

## 5. Automatic semantic-edge scenarios

- **AUTOEDGE-001** — Schema admits `semantic_auto` only with complete system-job,
  fixed-revision, model/prompt/scoring, evidence, confidence, and effect data.
- **AUTOEDGE-002** — Human/manual and historical approved edges remain readable
  and unchanged; no fabricated user actor is used for automatic work.
- **AUTOEDGE-003** — Insert-only semantic intents, closed seed-run provenance/
  cursor transitions, and decision/suppression ledgers have exact uniqueness/
  immutability/idempotency constraints and reject duplicate intent effect keys or
  incoherent tagged seed/terminal/candidate/activated/rejected/result tuples;
  stale/no-candidate jobs need no fabricated target or model evidence. Every
  nonterminal automatic job has zero decision rows; every terminal automatic job
  has exactly one terminal record, and candidate rows/effect/job terminal state
  commit atomically.
- **AUTOEDGE-004** — Only the post-curation transition of the current revision
  to active/published atomically appends bounded handler-independent local and
  eligible-ancestor intents plus the ordinary outbox; initial provisional
  publication appends none and SBE-3.1 creates zero semantic job rows. After the
  exact handler is registered and the matching flag is effective, bounded
  concurrent/restarted materializers create exactly one initially queued job for
  each eligible unjoined intent and converge to zero such intents, with at most
  one job per effect key. Projection-outbox acknowledgement neither consumes nor
  deletes an intent; stale/unauthorized/invalid input closes its one job with a
  deterministic pre-candidate terminal decision, claim/commit drift produces no
  edge, no intent is repeatedly skipped, and no DB transaction contains model/
  network work.
- **AUTOEDGE-005** — Candidate set is allowlisted/bounded; model cannot add an ID,
  cross authorization, self-link, or choose a free-form relation type.
- **AUTOEDGE-006** — Evidence spans anchor exactly to current source/target
  revisions; prompt injection, malformed JSON, unknown IDs/types/directions,
  and stale spans create no active edge.
- **AUTOEDGE-007** — Passing thresholds automatically activate every valid edge
  with no human queue; below-threshold/low-grade decisions do not activate.
- **AUTOEDGE-008** — Stricter frozen thresholds apply to `contradicts` and
  `depends_on`; relation-wise evaluation meets the precision gate.
- **AUTOEDGE-009** — Model timeout/OOM/unavailable/provider error leaves item
  curation status, search/FTS membership, and Agent visibility unchanged and
  creates zero semantic edges; deterministic provenance continues.
- **AUTOEDGE-010** — Commit revalidates endpoint revisions, states, authority,
  closure, LCA, suppression, and effect key; any drift has zero edge effect.
- **AUTOEDGE-011** — Same request/job replay and restarted baseline/detector/org-
  retarget/lifecycle-reactivation seed batch are byte-stable/idempotent; a
  maximum-200-row keyset cursor resumes without gap/duplicate, publication/seed
  races converge, and changed payload under a reused operation/effect key
  conflicts.
- **AUTOEDGE-012** — Exact physical-space local authority or LCA-home aggregate
  authority suppresses/deactivates with reason/audit and prevents the same
  detector generation from recreating the semantic key; endpoint access, stale
  role, and foreign/ordinary admin routes have zero effects. Break-glass admin
  correction is organizational only.
- **AUTOEDGE-013** — Revised endpoints become needs-revalidation; a new passing
  decision creates/revalidates explicitly rather than mutating evidence silently.
  Archive/deactivate makes an old-generation queued job terminal no-edge; later
  item/Wiki restore advances the lifecycle vector, creates exactly one new-
  generation intent/job for only affected current published revisions, and never
  reopens/mutates the old terminal job or scans an unrelated Wiki.
- **AUTOEDGE-014** — Automatic merge, replace, conflict-winner, item deactivate,
  purge, distribution, document-curation, and FTS/Agent-visibility effects remain
  exactly zero under normal and hostile model output.
- **AUTOEDGE-015** — The pre-SBE published corpus, a detector-generation change,
  an org move to a new ancestor, and item/Wiki lifecycle reactivation each
  produce complete provenance-correct intents/jobs; first-enable preflight
  rejects an incomplete baseline seed and fake publication-outbox proof is
  rejected. Graph feedback-loop corpus, cycles, hubs, duplicates, and reversed
  upload order remain bounded and deterministic.
- **AUTOEDGE-016** — Golden corpus automatic-edge precision is at least 0.95
  overall and at least 0.97 for `contradicts|depends_on`, false active-edge rate
  at most 0.01, with no unauthorized edge; thresholds may only be changed in a
  reviewed contract update, never tuned on the held-out set.
- **AUTOEDGE-017** — UI shows automatically activated edges without an approval
  inbox and explains source/type/evidence/confidence/model generation.
- **AUTOEDGE-018** — Personal owner/Part editor/current Team leader/current
  executive see only their exact local/home correction actions; organizational
  break-glass admin is separately confirmed, suppression lift queues
  re-evaluation rather than direct activation, and ordinary readers cannot
  mutate graph state.
- **AUTOEDGE-019** — Provider/model failures are visible as enrichment status,
  not upload failure, and retry cannot duplicate or bypass suppression.

## 6. Explicit-reference scenarios

- **REF-001** — v10 explicit-reference table/index/constraint fingerprint is
  exact; source revision/ordinal and authorization-bound target identity close.
- **REF-002** — Reference rows are not local/aggregate semantic edges and do not
  change graph degree, semantic confidence, or edge generations.
- **REF-003** — Only the one frozen stable-item-ID syntax is recognized; title-
  only, malformed, duplicate-ordinal, control, and injected forms fail safely.
- **REF-004** — Publication accepts only a target in the server-resolved closure;
  forged/concealed/foreign targets reveal no ID/existence and commit zero rows.
- **REF-005** — Source/target revisions, ordinal, spaces, actor, operation/effect
  key, and exact replay are stored; target edit shows a newer revision without
  silent retarget, while unavailable target is explicit.
- **REF-006** — Authorized references enter bounded mandatory structured context
  separately from full-corpus scoring; they cannot expand authorization or trigger
  recursive/unbounded reference traversal, and combined pin/reference context
  respects the max-four mandatory and shared 96 KiB excerpt caps with disclosed
  deterministic truncation.
- **REF-007** — Page UI/projection shows an ordered `참조 문서` list distinct from
  semantic Graph, with real origin and unavailable/newer-revision state.
- **REF-008** — UI reference picker searches only current closure, writes the
  stable syntax/ID, survives rename, and never exposes concealed suggestions.

## 7. Retrieval and reranker scenarios

- **RANK-001** — Resolver supplies an immutable authorized closure; retrieval
  never trusts client space lists and conceals out-of-closure canaries.
- **RANK-002** — Personal conversation searches the exact bound Wiki. The
  separate `POST /api/knowledge/v2/personal-wikis/search` management action uses
  one active-owner-filtered bounded query, returns at most ten origin-preserving
  results, reports no archived hits, never enters Agent context, and opening a
  result starts/opens only that exact Wiki-bound conversation.
- **RANK-003** — Part/Team/Responsibility results match exact closure and preserve
  source-space provenance; no N-space query fan-out.
- **RANK-004** — One direct FTS query covers every lexical match in the selected
  authorized closure without candidate-count truncation, with stable BM25/rank
  normalization, Korean/English token handling, and deterministic ties.
- **RANK-005** — One-hop expansion uses at most 12 seeds, four neighbors/seed,
  40 unique neighbors, active/current edges, cycle guard, and hub penalty.
- **RANK-006** — Two-hop expansion runs only when the frozen sufficiency gate
  fails, from at most five anchors, with decay and at most 20 additions.
- **RANK-007** — Candidate union/dedupe/pre-rank covers the complete authorized
  lexical/graph candidate set; exact-body prompt collapse retains alternate
  provenance identities and there is no per-Wiki query/model fan-out.
- **RANK-008** — Final ranked and authorized body-evidence lanes have no fixed
  document-count cap, preserve relevant unique documents, and do not pad the
  ranked lane with weak/irrelevant results; the separately bounded mandatory
  pin/reference lane is not counted as a ranking result.
- **RANK-009** — Explicit pins/contexts remain mandatory separate inputs and
  cannot be displaced by ranker/model output; combined mandatory plus reranked
  and supplemental evidence stays within the exact shared 96 KiB byte budget.
  SBE-8.13 removes the per-document 4 KiB cutoff: full bodies are supplied when
  they fit; overflow passages retain source ranges and disclose omissions.
- **RANK-010** — `contradicts` means relevant conflicting evidence, not trusted
  support; prompt/citations preserve relation semantics.
- **RANK-011** — Input/set order, repeated runs, restart, cycles, equal scores,
  and reversed ingestion order yield byte-stable deterministic ordering.
- **RANK-012** — Cache key binds closure/content/graph/temporal/ranker versions;
  any authorization/revision/generation drift invalidates before use.
- **RANK-013** — Aggregate ordering preserves source provenance and stable ties
  without forcing an irrelevant Part result or imposing a final count cap.
- **RANK-014** — Held-out corpus achieves Recall@10 >= 0.90, nDCG@10 >= 0.85,
  MRR >= 0.85, citation precision >= 0.95, and unauthorized citation count 0,
  split by Korean/English and each scope kind.
  SBE-8.13 separately proves body-only turn-bound citation admission, exact
  server-generated selected-view original URLs and immutable revision/artifact
  provenance. A structurally valid marker under an unrelated claim must remain
  explicitly not semantically verified; model-free link tests do not close the
  held-out citation-precision threshold. Include actual browser/persistence and
  read-time lifecycle checks before activation.
- **RANK-015** — Local cross-encoder receives only authorized IDs and may only
  assign/reorder those IDs; oversized serialized input triggers the deterministic
  input-bound fallback rather than truncating authorization semantics.
- **RANK-016** — Timeout/OOM/missing model/malformed/injected output returns an
  exact byte-stable deterministic `ranked_documents[]` core for identical input
  and does not fail the Agent turn; the bounded fallback status/reason envelope
  may differ and is tested separately.
- **RANK-017** — Cross-encoder model/prompt/input revisions are hashed/cached;
  company API is not required for correctness.
- **RANK-018** — Temporal boost activates only for frozen temporal-intent cases;
  non-temporal golden order does not regress beyond the approved tolerance.
- **RANK-019** — Effective/source/content/registered time precedence, unknown
  neutrality, future-effective handling, and verification-trust feature are exact.
- **RANK-020** — Newer conflicting content is never auto-selected as winner; both
  relevant sources and typed times remain available to the answering model.
- **RANK-021** — Query/SQL/model-call count remains bounded at 1, 100, and 1,000
  Wikis with no per-Wiki query, model call, or worker; model-bound excerpt bytes
  remain capped even though retrieval result count is not.

## 8. Upload and publication scenarios

- **UPLOAD-001** — Standard NTFS service-account `Modify` can pin/create CAS
  shards; implementation does not request unnecessary `FILE_DELETE_CHILD`.
- **UPLOAD-002** — Parent/reparse/identity swap, symlink/junction, hardlink,
  rename, ADS/reserved name, remote/non-NTFS, and source-overlap attacks create
  zero source writes and stable public errors.
- **UPLOAD-003** — Concurrent same bytes are idempotent success; different late
  winners return drift; no false write failure from bounded handle races.
- **UPLOAD-004** — Raw hash/size/media/path evidence is streamed no-follow outside
  DB transaction and compared exactly inside the service transaction.
- **UPLOAD-005** — PDF/DOCX/PPTX/XLSX/Markdown/text fixtures produce versioned
  canonical Markdown or explicit sanitized failure without raw exception leakage.
  SBE-8.13 additionally keeps raw source, exact extraction and generated Wiki
  distinct. Require complete chunk/batch accounting, exact source-bound quotes,
  independent omission/compression review and explicit extraction-only fallback.
  Context/qualification ranges are not factual evidence; a new fact recovered
  at a boundary requires its own exact quote receipt. Model-free synthesis and
  optional storage tests do not replace real file-upload/publication acceptance.
  Whole-map completion and complete-wave coverage require the verified map
  ledger, exact ordered card identities and every predecessor batch response;
  a caller-supplied count/hash is not proof. Reconstruct preparation after
  restart from committed results, never from a serialized process-local seal.
  A valid authored-selection metadata row alone does not prove that publication
  used the authored body: initial revision, dedup, FTS and provenance must all
  select the same body atomically. Fact-ID accounting must also be checked for
  semantic condition/exception loss beyond the fixed development gold list.
  Chunk preparation, assembly and publication must refresh DB time after slow
  work and in the writer; stale caller time cannot extend preview eligibility.
  The generated-body selection is part of the publication transaction, not
  a later mutable/deletable label. Source-created explicit references are parsed
  from exact extraction; new model markers do not become user-authored edges.
  Extraction-only publication closes a pending run in the same transaction;
  durable job/lease cancellation and ready-body server selection also require
  end-to-end validation before automatic authoring activation.
  Internal chunk-worker checks must cover lost acknowledgement after commit,
  a fresh process after claim and after an uncommitted provider response,
  retry-product admission, source/actor/role drift before model and commit,
  outside-service queued cancellation reconciliation, and explicit extraction
  selection cancelling unneeded work. A chunk result plus a successful job is
  one atomic effect; a second provider call after a lost uncommitted response
  is possible and must be reported. Multiple authors changing corpus content
  must not invalidate an unchanged extraction's mutation-authority fence.
  These checks do not replace final reduced-synthesis/public workflow tests.
- **UPLOAD-006** — Supported embedded images/assets become canonical children
  with exact content-addressed linkage, same-owner checks, and rendered references.
- **UPLOAD-007** — A no-blocker revision has zero pre-release Agent exposure,
  automatically reaches `published`, and becomes FTS/Agent-searchable atomically
  without human review. That same transaction writes exact eligible
  `semantic_edge_intent` rows and ordinary outbox but no semantic job; a strong
  document-curation finding remains provisional/hidden with zero intent. Later
  semantic/projection pending or failure never hides an already published
  revision.
- **UPLOAD-008** — `개인` requires exact active Wiki, `Part` exact current Part,
  and `개인+Part` creates two independent atomic outcomes with no sync.
- **UPLOAD-009** — Duplicate/reupload rules preserve source provenance: exact
  same-space body adds provenance/no duplicate item; cross-space remains independent.
  For authored ordinary reuploads, identical raw hash/size/media/name and exact
  extraction hash/size/normalizer/extractor/warnings identify an equivalent source;
  another model paraphrase cannot create a duplicate or update proposal by itself.
  Reuse the first stable body only from a currently authorized native publication
  target, not another author's private preparation/manifest. One existing target
  can supply the common body for both; incompatible target histories fail without
  mutation. A byte-identical newer current revision keeps its item and gains fresh
  source provenance even when prior source links exist only on an older revision.
  Requested candidate identity remains in the request hash while exact replay
  verifies the actual selected snapshot. An extraction-first native anchor must
  keep its exact raw/normalized body identity (including CRLF), add fresh source
  provenance and create no false authored selection. A separate immutable reuse
  receipt binds requested A2, baseline E1 and its source publication; the ready
  A2 run is not relabeled as an extraction failure. Receipt and both-target effects
  commit or roll back together. Equal-byte mixed-kind anchors use the earliest
  operation's kind, and different bodies conflict in either order. Receipt replay
  remains exact after later deactivation or private candidate-body retention loss.
  Existing unchanged-operation lineage
  history is not reparented. Empty/nonempty reuse preparation is revalidated in
  the writer, and target-summary bounds must not conceal total DB history work.
- **UPLOAD-010** — Changed ordinary filename upload creates a new item unless an
  adapter provides a stable, hash-bound source identity and explicit update flow.
- **UPLOAD-011** — Worker crash/retry at every stage yields one canonical effect,
  exact audit/outbox, no partial bundle, and recoverable staged cleanup.
- **UPLOAD-012** — Canonical DB is the only editable truth; projection files are
  browse/download read-only and external edits cannot mutate canonical state.
- **UPLOAD-013** — UI `+` dialog exposes upload first, conversation promotion
  second, then personal/Part/both with visible personal-Wiki selector.
- **UPLOAD-014** — Loading/extraction/published/enrichment/projection/error states
  are accessible, retry-safe, and distinguish upload success from edge enrichment.
- **UPLOAD-015** — Browser E2E verifies rich formats/assets, independent both
  copies, exact Wiki target, no legacy 404, and no foreign-scope data.

## 9. Temporal/footer scenarios

- **TIME-001** — v12 pristine/upgrade fingerprints, append-only temporal/
  verification ledgers, indexes, constraints, and failure rollback pass.
- **TIME-002** — `생성` equals item creation, `마지막 내용 수정` equals current
  revision creation, and `Revision` equals current positive `revision_no`; item
  aggregate `updated_at` is never used.
- **TIME-003** — A content edit advances content-modified and `revision_no`
  together; archive/restore/curation/edge/projection/no-op changes neither.
- **TIME-004** — Source-modified/effective assertions preserve precision/timezone/
  evidence and validate tagged instant/date values; mtime/upload/Notion-updated
  never becomes effective time by inference.
- **TIME-005** — Verification binds exact revision and temporal-summary hash;
  the canonical summary JSON contains exactly `schema_version`, `revision_id`,
  `source_modified_head`, and `information_effective_head`, represents each head
  by event ID/state/tagged value or `null`, and excludes verification, audit, and
  footer data. Golden fixtures prove a head change changes the summary hash while
  a verification-only change does not; either a new revision or a temporal-head
  change omits the `마지막 검증` row while history remains.
- **TIME-006** — Concurrent assert/revoke/attest uses expected-head CAS and exact
  replay; revoke leaves no active value/attestation rather than falling back to
  a superseded event, and no temporal event creates a content revision.
- **TIME-007** — Footer enabled/disabled yields identical canonical body/raw/
  normalized hashes, FTS, dedupe, embedding, semantic, and reranker text.
- **TIME-008** — Agent excerpts contain no footer string; typed temporal metadata
  is system-owned and prompt-injection resistant.
- **TIME-009** — Projection manifest v2 binds canonical prefix length/hash,
  where the prefix is explicitly LF-normalized UTF-8 with named normalizer; it
  also binds footer schema, rendered length/hash, revision ID/`revision_no`, and
  the canonical audit/domain high-water event ID used as `snapshot_as_of`.
  `built_at` is absent from the immutable manifest and rendered bytes.
- **TIME-010** — Rebuilding identical canonical state is byte-identical and keeps
  the same manifest/rendered hashes even when an out-of-band telemetry `built_at`
  differs; projector wall clock/relative scheduled-current label never appears
  as content modified, fact verification, or nondeterministic projection bytes.
- **TIME-011** — Trusted manifest re-import removes exact footer and produces a
  normalized-body no-op only when its digest is anchored in the canonical
  projection ledger/root or a reviewed signing key; adjacent/self-declared
  manifests and forged marker/length/hash/suffix are rejected or kept as text.
- **TIME-012** — Unicode, CRLF, blob-backed body, and byte-boundary fixtures do not
  truncate user text or confuse raw source, LF-normalized projected prefix, and
  rendered hashes; re-import does not claim to recreate raw CRLF bytes.
- **TIME-013** — UTC/KST round-trip, `+09:00`, date-only precision, invalid
  timezone/unit/calendar, tagged instant/date union, and future-effective value
  display are correct without fabricated midnight/relative file labels.
- **TIME-014** — The three required footer rows always render; absent, revoked,
  superseded, or non-current source/effective/verification metadata omits its
  optional row with no placeholder. Page Log remains the complete immutable
  history with exact-once assert/supersede/revoke/attest/verification-revoke
  audit ordering rather than duplicated footer state.
- **TIME-015** — Personal owner/Part editor/exact Team leader/exact 담당 executive
  can mutate only native current revisions; inherited readers, stale episodes,
  foreign users, system jobs, and LLM checks cannot create user-facing verification.
- **TIME-016** — Footer v2 golden bytes freeze UTF-8/LF, two-LF separator,
  markers, required `생성`/`마지막 내용 수정`/`Revision` rows, all eight legal
  optional-row presence combinations in fixed order, escaping, date/instant
  rendering, and final LF for inline/blob/empty/CRLF/Unicode bodies.
- **TIME-017** — The real 10-digit Notion-seconds fixture converts through one
  versioned adapter to milliseconds/source-modified evidence while preserving
  the immutable v1 snapshot/raw hash; unit/calendar ambiguity fails closed.
- **TIME-018** — Legacy reviewed mtime may backfill source-modified only with
  exact evidence; migration/import time remains canonical `생성`/revision event
  time and never becomes source-modified/effective evidence automatically.
- **TIME-019** — Temporal-only event increments one temporal generation,
  transactionally appends outbox, invalidates current ancestors and
  temporal/reranker/footer caches, but not content/FTS/embedding generation.
- **TIME-020** — Temporal-intent fixtures rank current/scheduled/historical facts
  correctly while unknown dates remain neutral; cache binds request `as_of` and
  expires by the next known effective boundary even without a write.
- **TIME-021** — Same body with different provenance dates remains a content
  duplicate and does not create semantic difference by footer.
- **TIME-022** — Conflict corpus retains both facts; timestamp alone performs no
  merge, replace, deactivate, purge, or conflict conclusion.

## 10. Integrated operations scenarios

- **SBEOPS-001** — Contract-audit report schema v2 retains all current manifest
  sources; reads/hashes the five exact successor documents and the exact
  repository/docs authority-synchronization ledger in `EXECUTION_PLAN.md`;
  validates their required successor pointers, transition state,
  baseline/addition/total ID sets, ten-entry supersession ledger, feature-flag
  DAG, human narrative counts, schema examples, and the exact OpenAPI union:
  frozen 52 paths/56 operations + the existing approved 18-path/22-operation
  successor + SBE-8.12's two v1 original-source GET paths/operations = current
  72 paths/80 operations, including 27 GET operations. It preserves baseline
  operation-ID hashes and the historical 70/78 registration, validates the
  17+14=31 stable public-error ledger and local references with zero errors,
  and rejects a stale authority document, missing/renamed source-artifact
  operation, unregistered addition, or proposed/registered partial mix. Fresh
  audit evidence must cover the amendment; it does not close SBE-8.12's
  remaining external runtime gates.
- **SBEOPS-002** — Existing P0-P7 evidence/hashes and v1-v7 migrations remain
  unchanged; current-v7 behavior is explicit until successor flags enable.
- **SBEOPS-003** — Every successor flag defaults false, dependency-invalid
  combinations fail preflight, and import alone opens no DB/routes/workers.
- **SBEOPS-004** — Baseline captures exact code/catalog/tests/contracts and no
  live roots/DB/flags/ports/provider calls changed.
- **SBEOPS-005** — Complete Knowledge Spaces plus root adjacent regressions pass
  in both supported working directories with strict markers.
- **SBEOPS-006** — Cross-process lifecycle/publication/role/edge/job/projection
  races across every named native/aggregate/distribution/lease/temporal
  capability satisfy one winner, exact replay, no partial effects, and bounded
  busy; emitted authority is revalidated immediately before each effect.
- **SBEOPS-007** — Security canaries outside CorpusClosure have zero data/count/
  ID/error/cache/citation/graph leakage or mutation across every role,
  break-glass action, stale episode, inherited/overlay target, and lifecycle state.
- **SBEOPS-008** — Fault injection at DB/disk/hash/extractor/model/outbox/worker/
  projector boundaries recovers idempotently without canonical corruption.
- **SBEOPS-009** — 1/100/1,000-Wiki and synthetic multi-Part workstation tests
  prove SQL/model/worker counts remain bounded and record comparable local
  latency/queue regressions. They do not close target-class `G-09`, `PERF-001`,
  the 40-client gate, or the post-SBE rebaseline.
- **SBEOPS-010** — Backup/restore of disposable v11 state reproduces exact DB,
  blobs, profiles, roles, conversations, local/aggregate edges, suppressions,
  temporal records, and projections.
- **SBEOPS-011** — Browser E2E covers admin, member, Part leader, Team leader,
  executive, stale-role, archived-Wiki, large-list, keyboard, and Korean UI.
- **SBEOPS-012** — Codex bridge/GGUF/provider-schema tests prove structured judge/
  reranker success and deterministic failure fallback with company calls disabled.
- **SBEOPS-013** — Automatic destructive effects, live-root mutations,
  production-flag changes, and company API calls are all zero.
- **SBEOPS-014** — 10111 uses isolated DB/blob/projection/config roots and cannot
  read/write 10011 mutable roots or bind its port by accident.
- **SBEOPS-015** — Flags activate in dependency order; each stage has health,
  rollback, and data/postcondition checks before the next stage.
- **SBEOPS-016** — The meaningful 7-Part x 10-user corpus proves personal/Part/
  Team/Responsibility Index, Log, Graph, retrieval, unrelated isolation,
  duplicate/provenance, contradiction, explicit reference, and auto-edge behavior.
- **SBEOPS-017** — User can inspect the complete isolated flow on 10111; known
  provider/target-operation limits are displayed rather than hidden.
- **SBEOPS-018** — Final handoff separates verified local behavior from company-
  model and target-server 40-client/service/backup/pilot gates, with exact
  commands, expected results, hashes, owners, and required authority.

## 11. Evaluation corpus

The integrated corpus contains at least seven Parts with ten users each and,
per Part, documents for:

- a unique canary policy;
- an explicit dependency/reference to that policy;
- a byte-exact duplicate under another filename;
- a normalized-only variant;
- a concrete contradiction with evidence;
- an unrelated domain document containing misleading shared words;
- a weak complementary document;
- an explicit stable-ID reference.

Every user also receives a private personal canary and neutral document. At
least one user per Part publishes one `개인+Part` item to prove independent
copies. Test both forward and reverse ingestion order. Gold labels distinguish
local edge, Team-home aggregate edge, Responsibility-home aggregate edge,
relevant-no-edge, duplicate provenance, contradiction, and no relation.

Held-out labels are frozen before tuning. Provider-specific evaluation records
model/prompt versions separately; deterministic/security gates do not depend on
provider quality.

## 12. Final release boundary

Passing this matrix locally permits the isolated `SBE-7.2` preview. It does not
authorize production. Production still requires rebaselined P7.8/P7.9/P8:
target Windows runtime/storage/service-account evidence, 40-client profile,
company-model compatibility if selected, signed backup/restore, maintenance
window, pilot, monitoring, rollback/roll-forward decision, and explicit user/
operator approval.
