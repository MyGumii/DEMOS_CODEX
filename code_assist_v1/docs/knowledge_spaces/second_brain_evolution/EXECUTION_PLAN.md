# Second Brain evolution execution plan

Status: approved sequence. `SBE-0.1` is verified; `SBE-1.1` and independent
`SBE-3.0` are now dependency-eligible, subject to their ownership boundaries.

## 1. Execution rules for Agents

For every packet, the implementing Agent must perform this sequence:

1. Read the root `AGENTS.md`, this package, current `STATUS.md`, packet
   prerequisites, and their evidence files completely.
2. Inspect the current code/schema/call sites read-only and record any drift.
3. Declare packet scope, owned files, shared-file integrator, tests, flags, and
   explicit no-go boundary before editing.
4. Implement one dependency-closed slice. Do not opportunistically implement a
   later packet or rewrite unrelated user changes.
5. Run focused tests, negative/fault tests, adjacent regressions, and the
   documentation/contract audit required by the packet.
6. Write `evidence/<packet_id>.md` from `SBE_TEMPLATE.md`—for example,
   `evidence/SBE-0.1.md`—including exact commands, counts, durations, stable
   hashes, and live-boundary attestation.
7. Submit the evidence and transition facts to the named status integrator. Only
   that integrator updates `STATUS.md`/hot shared contracts in the final merge
   step; it may be the implementing Agent only when named before editing.
8. Mark `VERIFIED` only after all gates pass. Otherwise use
   `IMPLEMENTED_NOT_GATED`, `BLOCKED`, or `DEFERRED` honestly.

If code contradicts this approved contract, stop only the conflicting edit,
record the exact conflict, and continue independent safe work. Never silently
choose the old behavior or weaken the new invariant.

## 2. Status and evidence contract

Valid packet states:

```text
NOT_STARTED
IN_PROGRESS
IMPLEMENTED_NOT_GATED
VERIFIED
BLOCKED
DEFERRED
```

`STATUS.md` owns the state. Evidence documents prove a state but do not replace
it. A packet row contains:

```text
Packet | State | Prerequisites | Owner | Started | Evidence | Gate result | Next owner
```

The append-only transition log contains:

```text
Timestamp | Packet | From -> To | Actor | Reason | Evidence
```

Stable hash sets exclude `STATUS.md` and the packet evidence itself to avoid a
recursive hash. They include implementation, migration, test, and normative
contract files.

Every packet owns exactly its own `docs/knowledge_spaces/evidence/<packet_id>.md`
even when that path is omitted from the compact ownership table. Before work,
one named status integrator exclusively owns `STATUS.md` and the append-only
transition row for that packet; other Agents provide a handoff and do not edit
the status file.

## 3. Feature-flag dependency graph

All successor flags default false. Production preflight rejects an enabled
child flag whose prerequisite schema/service/gate is absent.

```text
KNOWLEDGE_CORE_ENABLED
  + KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA
  + KNOWLEDGE_CORE_READS
  -> KNOWLEDGE_CORE_MULTI_WIKI_READS

KNOWLEDGE_CORE_MULTI_WIKI_READS
  + KNOWLEDGE_CORE_PERSONAL_WRITES
  -> KNOWLEDGE_CORE_MULTI_WIKI_WRITES

KNOWLEDGE_CORE_MULTI_WIKI_READS
  -> KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING

KNOWLEDGE_CORE_ENABLED
  + KNOWLEDGE_CORE_PART_READS
  + KNOWLEDGE_CORE_HIGHER_SPACES
  -> KNOWLEDGE_CORE_TEAM_WRITES
  -> KNOWLEDGE_CORE_RESPONSIBILITY_WRITES
  -> KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS

KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL
  + KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS
  -> KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE

KNOWLEDGE_CORE_ENABLED
  + KNOWLEDGE_CORE_READS
  -> KNOWLEDGE_CORE_HYBRID_RERANK
       -> KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER
       -> KNOWLEDGE_CORE_TEMPORAL_RANKING

KNOWLEDGE_CORE_ENABLED
  + KNOWLEDGE_CORE_PROJECTIONS
  -> KNOWLEDGE_CORE_PROJECTION_FOOTER_V2
```

The names shown above are the exact environment names registered unchanged in
`SBE-0.1`; an implementing Agent has no naming discretion. Read and write flags must
remain separable so rollout can provision schema/spaces, enable read-only views,
then enable narrow writers. The three arrows sharing
`KNOWLEDGE_CORE_HIGHER_SPACES` are independent children, not a sequence.

The seven existing flags remain authoritative parent gates during the whole SBE
rollout; none is renamed, aliased, retired, or inferred from a successor flag:

| Existing flag | Frozen successor relationship |
|---|---|
| `KNOWLEDGE_CORE_ENABLED` | Mandatory parent of every successor traffic or worker flag; schema-only migration may be installed while traffic stays off |
| `KNOWLEDGE_CORE_READS` | Parent of multi-Wiki/conversation and hybrid retrieval reads |
| `KNOWLEDGE_CORE_PERSONAL_WRITES` | Parent of personal-Wiki profile lifecycle and personal publication; a successor flag cannot reopen the current write gate |
| `KNOWLEDGE_CORE_PART_READS` | Parent of higher closure/native/aggregate read exposure; the existing rule that Part reads require personal reads remains |
| `KNOWLEDGE_CORE_PART_WRITES` | Parent for Part-target publication and Part-local automatic-edge workers only; it does not authorize Team/Responsibility native writes |
| `KNOWLEDGE_CORE_PROJECTIONS` | Parent of footer/manifest projection v2; `PROJECTION_FOOTER_V2` is a child, never an alias |
| `KNOWLEDGE_CORE_HIGHER_SPACES` | Master compatibility parent for Team/Responsibility targets and aggregate graph reads; its phase-1 fail-closed rejection is replaced only in the packet that supplies the v9 resolver and readiness proof |

Local automatic-edge and retrieval services additionally apply the old gate for
the concrete physical source: personal uses `READS`/`PERSONAL_WRITES`, Part uses
`PART_READS`/`PART_WRITES`, and higher native sources use `PART_READS`,
`HIGHER_SPACES`, and the matching Team/Responsibility write flag. Aggregate-edge
workers require both automatic-edge flags plus `AGGREGATE_GRAPH_READS`. Disabled
parents force every child ineffective even if its environment value is `1`;
startup/readiness reports the requested and effective values and rejects rather
than silently widening access. `SBE-0.1` must copy this exact DAG into
`MIGRATION_ROLLOUT.md`, a machine-readable successor flag contract/schema, and
contract-audit fixtures before `successor_transition.state` becomes
`registered`; it does not enable or implement a runtime flag. Each owning
implementation packet then adds its flags to the runtime parser, environment
example, readiness/preflight, and focused tests, while `SBE-7.1` proves the
complete effective DAG.

The v10 `semantic_edge_intent` producer is part of the source publication
transaction and follows that source's existing write gate; the two automatic-
edge flags govern intent-to-job materialization and execution, not intent
capture. While those flags are false, no semantic job is created and immutable
intents accumulate at only the frozen constant number of eligible scopes per
published revision. Before a flag becomes effective, its baseline seed run must
be completed and reconciled; enabling it drains only bounded indexed intent
batches. Later detector/org seed runs may execute while the old validated graph
remains visible, but invalidated old-scope edges never remain effectively visible.

## 4. Dependency overview

```text
SBE-0.1 -> SBE-1.1 -> SBE-1.2 -> SBE-1.3
SBE-0.1 -> SBE-3.0
SBE-1.1 -> SBE-2.1
SBE-1.2 -> SBE-2.3
SBE-2.1 + SBE-2.3 -> SBE-2.2
SBE-1.3 + SBE-2.3 + SBE-3.0 -> SBE-3.1
SBE-1.3 + SBE-3.1 -> SBE-3.2
SBE-1.3 + SBE-2.3 -> SBE-4.1
SBE-2.1 + SBE-3.1 + SBE-4.1 -> SBE-4.2
SBE-2.2 + SBE-2.3 + SBE-3.1 + SBE-4.1 -> SBE-5.1
SBE-3.2 + SBE-5.1 -> SBE-5.2
SBE-2.2 + SBE-3.1 + SBE-3.2 + SBE-5.1 -> SBE-6.1
SBE-2.3 + SBE-4.2 + SBE-5.1 -> SBE-6.2
all SBE-1.1..SBE-6.2 implementation packets, including SBE-3.0/SBE-5.2 -> SBE-7.1
SBE-7.1 -> SBE-7.2 -> rebaselined P7.8/P7.9/P8 (with target authority)
```

The packet-index prerequisite column below is authoritative and must match this
derived overview exactly; an audit fails on drift.

Schema packets are sequential at the catalog level: v8 before v9 before v10;
v11 is introduced by SBE-2.3 for explicit higher publication/curation authority,
then v12 is introduced with the footer/temporal packet after the earlier catalog
is stable. Service design/tests may be prepared in parallel against frozen DTOs,
but may not merge ahead of the required physical version.

## 5. Packet index

| Packet | Outcome | Prerequisites | Primary acceptance |
|---|---|---|---|
| `SBE-0.1` | Contract transition and exact baseline | approved package | `SBEOPS-001..002`, `SBEOPS-004` |
| `SBE-1.1` | v8 multi-Wiki physical schema | `SBE-0.1` | `WIKI-001..004` |
| `SBE-1.2` | v9 higher role/native-space schema | `SBE-1.1` | `HSPACE-001..005` |
| `SBE-1.3` | v10 aggregate/auto-edge/reference schema | `SBE-1.2` | `AGRAPH-001..004`, `AUTOEDGE-001..003`, `REF-001..002` |
| `SBE-2.1` | Personal Wiki lifecycle service/API | `SBE-1.1` | `WIKI-005..012` |
| `SBE-2.2` | Conversation binding/archive behavior | `SBE-2.1`, `SBE-2.3` | `WIKI-013..017` |
| `SBE-2.3` | CorpusClosure and higher authority | `SBE-1.2` | `HSPACE-006..014` |
| `SBE-3.0` | Windows stager least-privilege/race hotfix | `SBE-0.1` | `UPLOAD-001..004` |
| `SBE-3.1` | Canonical rich upload/reference pipeline | `SBE-1.3`, `SBE-2.3`, `SBE-3.0` | `UPLOAD-005..012`, `REF-003..005` |
| `SBE-3.2` | v12 temporal metadata/footer projection | `SBE-1.3`, `SBE-3.1` | `TIME-001..019` |
| `SBE-4.1` | LCA aggregate graph read/write/projection | `SBE-1.3`, `SBE-2.3` | `AGRAPH-005..014` |
| `SBE-4.2` | Automatic semantic edge jobs/correction | `SBE-2.1`, `SBE-3.1`, `SBE-4.1` | `AUTOEDGE-004..016` |
| `SBE-5.1` | Authorized deterministic hybrid retrieval | `SBE-2.2`, `SBE-2.3`, `SBE-3.1`, `SBE-4.1` | `RANK-001..014`, `REF-006` |
| `SBE-5.2` | Optional local cross-encoder/freshness | `SBE-3.2`, `SBE-5.1` | `RANK-015..021`, `TIME-020..022` |
| `SBE-6.1` | Multi-Wiki/archive/upload UI | `SBE-2.2`, `SBE-3.1`, `SBE-3.2`, `SBE-5.1` | `WIKI-018..022`, `UPLOAD-013..015` |
| `SBE-6.2` | Higher native write/aggregate graph/reference/correction UI | `SBE-2.3`, `SBE-4.2`, `SBE-5.1` | `HSPACE-015..018`, `AGRAPH-015..017`, `AUTOEDGE-017..019`, `REF-007..008` |
| `SBE-7.1` | Integrated isolation/security/performance gate | all implementation packets | `SBEOPS-003`, `SBEOPS-005..013` |
| `SBE-7.2` | Isolated 10111 staged activation and handoff | `SBE-7.1` | `SBEOPS-014..018` |

## 6. Packet specifications

### SBE-0.1 — contract transition and baseline

Outcome:

- synchronize `DECISIONS`, architecture, schema-forward contract, API/UI,
  migration rollout, test operations, traceability, goal contract, OpenAPI,
  JSON Schemas/examples, feature-flag dependency contract, backlog, and status;
- assign every new acceptance ID and update generated counts exactly once;
- register the approved v8-v12 DDL/API DTO names, migration filenames/
  checksums protocol,
  `CorpusClosure`, scoring versions, relation vocabulary, and error codes;
- capture current catalog/code/test hashes without editing live data.

No runtime application behavior, DB, live roots, flags, server, or model call
changes. This packet does own the contract manifest, audit tool/report schema,
and their tests because its gate cannot be proved by the current v1 auditor.
Gate: report-schema-v2 documentation audit pass, schema/API examples validate,
no contradictory active decision, and old P evidence hashes unchanged.

The transition is ID-explicit rather than a silent text rewrite:

| Existing ID | SBE-0.1 action |
|---|---|
| `SPACE-005` | Retain as the successor-flags-off compatibility test; enabled higher behavior is proved by the `HSPACE` scenario family |
| `CUR-005` | Retain for historical/legacy human-approval mode; enabled semantic-auto behavior is proved by the `AUTOEDGE` scenario family |
| `CUR-011` | Retain for destructive/conflict T2 and all T3 actions; explicitly exclude the narrow v10 validated edge effect from T2 |
| `R-004` | Rewrite from personal/current-Part-only to one selected view plus server-authorized `CorpusClosure` |
| `R-017` | Rewrite from future/no-union to native higher spaces, descendant aggregate reads, and exact leadership writes |
| `R-028` | Split validated automatic edge activation from the unchanged prohibition on automatic destructive curation |
| `G-07` | Define isolation as zero exposure outside the authorized closure/overlay, while preserving conversation-space immutability |
| `G-08` | Replace `max_semantic_edge_auto_commits=0` with zero invalid/unauthorized automatic commits, frozen precision/false-edge gates, and zero automatic destructive/T3 effects |
| `G-09` | Keep target SLO intent but rebaseline the exact successor corpus/query/profile after SBE-7.2 |
| `API_UI_CONTRACT.md#9:system-admin-Part-authorship` | Replace administrator-status ordinary Part publish/edit/curation authority with no ordinary authorship; preserve independently held membership authority and only the explicit audited break-glass matrix |

No historical scenario/evidence ID is deleted. The exact requirement additions
are:

| ID | Registered contract |
|---|---|
| `R-032` | Unlimited owner-scoped personal Wiki lifecycle with exactly one active default and reversible owner-only archive |
| `R-033` | Immutable conversation-to-Wiki/view binding, archived history-only behavior, and cache non-authority |
| `R-034` | Native Team/Responsibility authority, descendant `CorpusClosure`, role episodes, and distribution |
| `R-035` | LCA-owned aggregate graph with Part isolation and origin-preserving projections |
| `R-036` | Automatically activated, evidence-validated semantic edges with exact correction authority and zero document-curation/destructive effects |
| `R-037` | Authorized full-corpus hybrid retrieval with no ranked/body document-count cap, bounded graph/model-input work, an exact 96 KiB Agent excerpt budget, a separately max-four mandatory pin/reference lane, and deterministic fallback |
| `R-038` | Canonical rich upload/assets/reference pipeline with existing document-curation visibility |
| `R-039` | Typed temporal provenance, required/optional footer grammar, safe re-import, and freshness semantics |
| `R-040` | Exact `item-ref-v1` deterministic reference ledger/list, authorization, and bounded context |
| `R-041` | Count-independent operations, isolated 10111 validation, and explicit external-evidence handoff |

The exact goal additions are:

| ID | Registered metric family |
|---|---|
| `G-15` | Multi-Wiki identity/default/archive/conversation integrity |
| `G-16` | Higher-scope isolation, native leadership authority, and distribution integrity |
| `G-17` | Automatic-edge precision, false-edge/authorization zero-tolerance, and correction behavior |
| `G-18` | Recall/ranking/citation quality plus query/model/worker boundedness |
| `G-19` | Temporal/footer/projection byte integrity and freshness correctness |
| `G-20` | Isolated preview closure with company-model/target-server evidence explicitly handed off |

`contract-manifest.json` gains a closed `successor_transition.state` of
`proposed|registered` and the following exact active ledger. The approved
SBE-8.12 original-source amendment extends only the API rows; the prior
SBE-0.1/SBE-6.2 70-path/78-operation registration remains historical evidence.

| Measure | Baseline | Addition | Registered total |
|---|---:|---:|---:|
| Requirements | 31 | 10 (`R-032..041`) | 41 |
| Goals | 14 | 6 (`G-15..20`) | 20 |
| Mandatory scenarios | 124 | 160 (exact family table in `ACCEPTANCE.md`) | 284 |
| OpenAPI paths | 52 | 18 existing successor + 2 SBE-8.12 (exact tables in `FORWARD_SCHEMA.md`) | 72 |
| OpenAPI operations | 56 | 22 existing successor + 2 SBE-8.12 (exact operation IDs in `FORWARD_SCHEMA.md`) | 80 |
| Stable public error codes | 17 | 14 (exact list in `FORWARD_SCHEMA.md`) | 31 |

The manifest permanently pins baseline hashes: requirements
`47a5ce485b0f8bc791ea4534cdec1b86d662c300558e0db2c7473a0a0eebd0a6`, goals
`a9a2a6c1c308d1d3edd8e28f143d4ce37169b9eee90436cbd1ab97d4d63fcc9c`,
scenarios `c63fc22a5908393b73cef5fd1d9839c15af807417afa9f39ca31c8a6b6bcddf9`,
and OpenAPI operation IDs
`75b7270c72c7275879ab43824c0988717390502b319f3c970aecceb944b200c1`.
It also pins the five source paths `second_brain_evolution/README.md`,
`PRODUCT_CONTRACT.md`, `FORWARD_SCHEMA.md`, `EXECUTION_PLAN.md`, and
`ACCEPTANCE.md`, plus the ten-entry supersession set above.

Report schema v2 retains every current manifest source, reads/hashes all five
successor sources, and emits a `successor_transition` summary. It also has an
exact authority-synchronization ledger: repository-root `AGENTS.md`, plus
docs-root `README.md`, `DECISIONS.md`, `ARCHITECTURE.md`, `SCHEMA_V1.md`,
`API_UI_CONTRACT.md`, `MIGRATION_ROLLOUT.md`, `TEST_OPERATIONS.md`,
`IMPLEMENTATION_BACKLOG.md`, `TRACEABILITY.md`, `GOAL_CONTRACT.json`, and
`STATUS.md`. `AGENTS.md` is resolved only through a dedicated repository-root
allowlist; every docs source is resolved under the docs root, so arbitrary
parent traversal remains invalid. The audit checks the successor pointer/state,
the ten exact supersession selectors/actions, the feature-flag DAG, and required
SBE status/backlog rows in those authority sources rather than merely hashing
stale text. In `proposed`, active machine contracts must
remain exactly 31/14/124 and any partial new-ID registration fails. In
`registered`, active sets must equal baseline union additions and exact totals
41/20/284/72/80, including 27 GET operations. The API union is exactly the
frozen 52-path/56-operation baseline plus the existing 18-path/22-operation
successor and SBE-8.12's two v1 original-source GET paths/operations;
missing/path-escaped sources, duplicate/gapped family IDs,
R/G/supersession/narrative/error-code drift, premature state flips, and partial
unions all fail. The historical v1 audit pass proves only the baseline, never
`SBEOPS-001`; a fresh report-schema-v2 audit must prove the amended registered
contract. Prior audit evidence cannot prove the SBE-8.12 addition. Contract
registration alone does not complete runtime gates; SBE-8.12 remains
`IMPLEMENTED_NOT_GATED` while its external gates are open.

### SBE-1.1 — v8 multiple personal Wikis

Implement the forward migration, catalog entry, pristine/upgrade paths,
repository DTOs, and schema tests for `personal_wiki_profile` and removal of the
one-personal-space owner constraint. Preserve existing personal space IDs and
backfill them as exact defaults. Prove exact-one active default, no count cap,
archive/default/lifecycle-generation constraints, pagination plans, failed-
upgrade rollback, and v7-disabled compatibility.

### SBE-1.2 — v9 higher roles/native spaces

Implement the higher-role episode ledger, Team/Responsibility native-space
provisioning, organization-kind constraints, write-enable closure, and explicit
distribution-grant schema. Prove role-kind/ancestry, one open episode, ABA
generation fencing, disable/move/replacement atomicity, and no Part/personal
regression.

### SBE-1.3 — v10 aggregate/automatic edge schema

Implement aggregate-edge, automatic-decision, suppression, explicit-reference,
handler-independent semantic-intent/seed-run, actor/evidence, and generation-
vector physical contracts. Keep local same-space edge FKs intact. Prove exact LCA,
endpoint revisions, one-home uniqueness, no personal aggregate edge,
deterministic reference separation, insert-only intent/effect-key uniqueness,
item/Wiki source-lifecycle vector and increment constraints, closed seed
provenance/cursor transitions, system actor integrity, immutable evidence,
suppression, and schema rollback/idempotency.

### SBE-2.1 — Wiki lifecycle service and API

Implement cursor profile list/filter, create, rename, set-default, archive,
restore, and active/archived management listing through repositories/services
only. The cross-Wiki content-search operation is owned by SBE-5.1, not this
packet. Every mutation has
operation ID, CAS row version, audit, outbox, and exact replay. Bound list
limits; reject control characters/collisions, stale cursors, foreign owners,
default/last-active archive, and unbounded calls. Compatibility maps a legacy
personal target to the current default only at a documented boundary. Archive/
restore increments only the dedicated Wiki lifecycle generation, and the
canonical restore outbox payload binds that generation for SBE-4.2's later
reactivation-event consumer.

### SBE-2.2 — conversation binding and archived history

Make conversation creation require explicit resolved view/Wiki identity. On
session open, return/select the stored server binding; treat local cache as an
optimization. Archived Wiki conversations are readable but cannot accept a new
turn until restore. Switching selection creates a new conversation. Bind all
summaries, pins, retrieval, and caches to view/closure generations; test stale
membership and tampered IDs.

### SBE-2.3 — CorpusClosure and higher authority

Replace single-space capability assumptions with typed `AccessPlan` and
`CorpusClosure`. Implement descendant reads, native higher write targets,
role-assignment/replace/end-and-disable services and exact admin v2 routes,
role-episode fencing, origin-preserving responses, explicit distribution
services/routes, concealment, and invalidation on org/role change. Centralize
resolution so browser, Agent turns, publication, graph, jobs, and projection use
the same closure rather than preview-only wrappers. Integrate the resolved
higher-space `AccessPlan` into every existing publish, edit, deactivate, restore,
reviewed-curation, lease acquire/renew/release/force-release, preview, commit,
and queued-job entry point. Each entry point rejects inherited/overlay mutation,
revalidates the exact open role episode and generation immediately before commit,
and fences stale previews, leases, and jobs after role replacement or disable.
Later temporal and semantic-edge entry-point integration remains owned by
`SBE-3.2` and `SBE-4.2`, respectively.

### SBE-3.0 — Windows stager least-privilege and race safety

Fix the directory-anchor access mask so standard NTFS `Modify` can stage CAS
content without unnecessary `FILE_DELETE_CHILD`. Preserve handle-bound,
no-follow, anti-hardlink/rename, atomic commit, and source-no-write guarantees.
Freeze observed evidence DTOs outside DB transactions and close real-process
late-winner/read races. This packet changes no publication target, extractor,
reference, schema, UI, or live root and can land before the later feature work.

### SBE-3.1 — canonical rich upload and explicit references

Unify rich extractors and embedded assets with the canonical preview/publication
worker on top of the verified stager. Keep reads/hashes outside transactions,
revalidate evidence before commit, parse the frozen stable-ID reference syntax
into the separate reference ledger, run the versioned document-curation gate,
make only its automatic no-blocker `published` transition searchable, and append
bounded handler-independent `semantic_edge_intent` rows plus ordinary
publication/projection outbox records in that same transaction. This packet
creates zero semantic `durable_job` rows and registers no semantic handler;
generic outbox acknowledgement never consumes an intent. Strong document
findings stay provisional; later edge jobs never write document curation/FTS
visibility.

### SBE-3.2 — temporal assertions and footer

Implement v12 append-only temporal/verification ledgers, the exact temporal GET/
assert/revoke/attest routes and typed API fields, projection manifest v2,
deterministic footer rendering, and safe re-import.
Required `생성`/`마지막 내용 수정`/positive `Revision` values come from item/
current revision; the three evidence-backed temporal rows are omitted unless
current. Footer remains outside hashes/search/dedup/model text. Preserve v1 source projections during
transition. Freeze head CAS/no-fallback, attestation temporal-summary binding,
native mutation permissions, temporal generation/outbox/Log ordering, exact
footer bytes and trusted manifest/signing roots. Fix/normalize source timestamp
units at adapter boundaries without rewriting immutable legacy evidence,
including the real 10-digit Notion-seconds path.

### SBE-4.1 — LCA aggregate graph

Implement exact LCA calculation, local/aggregate repositories, effective
visibility, descendant composition, origin labels, generation-vector
invalidation, projection, and org-move/revision revalidation. Part graphs remain
bitwise unaffected by sibling/ancestor edges. Team and Responsibility views
compose the correct descendant clusters and home edges without body/edge copies.

### SBE-4.2 — automatic semantic edges

Register the exact automatic-edge handler pair before enabling either edge flag.
Register and reconcile a maximum-200-row keyset seed-run service for the
pre-existing current active/published corpus, detector-generation reseed, and
organization-retarget plus item/Wiki lifecycle-reactivation events, with exact
high-water/cursor/cause/lifecycle-vector proof and no fabricated publication
outbox. Its independent restore-event cursor consumes immutable canonical event
history regardless of projection-outbox acknowledgement and seeds only the
affected current active/published item or Wiki. Implement a bounded monotonic/
anti-join intent
materializer that records current fences and creates exactly one queued known-
handler job for every flag-eligible unjoined effect key, with at most one job per
key. Concurrent/restarted runs converge to zero eligible unjoined intents; stale
input is never repeatedly skipped and its worker closes one deterministic
pre-candidate no-edge terminal record. Generic outbox state is not consumption
authority. Then
implement bounded local and ancestor candidate jobs, versioned LLM judge schema,
deterministic evidence/threshold validation, idempotent activation, repeated
claim/commit endpoint/authority/closure/LCA/suppression revalidation, evaluation
outcomes, correction/suppression, and rollback by detector generation.
Nonterminal jobs have zero decision rows; candidate decisions, the exact one
terminal decision, any edge effect, and job terminal state finalize in one
transaction, including retry exhaustion. Every valid edge auto-activates; there
is no approval queue. Model failure creates no semantic edge. Automatic
destructive effects remain exactly zero.

### SBE-5.1 — deterministic hybrid retrieval

Implement one authorized full-closure FTS plan, bounded one-hop expansion,
conditional bounded two-hop expansion, cycle/hub control, dedupe, scoring,
aggregate-view ordering, document-count-unbounded ranked/body evidence without
irrelevant padding, citations, and generation-bound caches. The Agent composer
enforces the exact 96 KiB combined excerpt budget and discloses truncation.
Authorized explicit references and pins are a separate combined max-four
structured-context lane within that shared byte budget, not scored graph hops.
Global active-
personal-Wiki search is one owner-filtered
management query outside conversations/Agent context, never N fan-out; implement
its exact `personalWikisSearch` route here. Opening a result starts or opens its
exact Wiki-bound conversation. Provide stable explain evidence without leaking
concealed IDs or archived hit counts.

### SBE-5.2 — optional local reranker and temporal ranking

Add an optional local multilingual cross-encoder over the frozen bounded set.
It may reorder only. Strict timeout/OOM/malformed/unavailable fallback returns
the byte-stable deterministic `ranked_documents[]` core while the bounded status
envelope may differ. Add temporal-intent detection and typed
freshness/trust features; general queries remain freshness-neutral. Do not
embed footer strings or typed time fields in cross-encoder text, and do not
treat newer as an automatic conflict winner. Deterministic fusion owns temporal
features; cache keys bind request `as_of` and expire at the next known effective
boundary.

### SBE-6.1 — multi-Wiki/archive/upload UI

Implement the `내 지식 -> Part -> 팀 -> 담당` tree, cursor virtualization,
expand/collapse, rename, default/archive/restore/history states, exact Wiki
selection on session load, separate archived management search, active-only
`모든 내 Wiki 검색`, and the `자료 업로드`/`대화 지식화` plus target tabs.
Show native document/footer data from canonical APIs; projection files are
browse/download only. Test keyboard, screen-reader, Korean text, large lists,
stale tabs, and concurrent lifecycle updates.

### SBE-6.2 — higher write/aggregate graph/correction UI

Show native versus inherited origin/read-only state. Only the exact current
Team leader/Responsibility executive sees native authoring controls. Show Part
clusters isolated, aggregate edge homes, relation types/evidence/confidence,
the separate ordered References list, exact distribution grant/revoke controls,
lease force-release, role end/write-disable, and authorized correction/
suppression including separately confirmed admin break-glass actions. Revalidate
at commit so an open editor cannot survive role replacement. Never expose
concealed branches in counts, errors, suggestions, or graph layout.

### SBE-7.1 — integrated gate

Run complete Knowledge Spaces and root regressions, schema upgrades, cross-
process concurrency, fault injection, security canaries, deterministic corpus
evaluation, 1/100/1,000-Wiki query-plan/load tests, multi-Part graph validation,
projection/re-import checks, worker restart/idempotency, backup/restore rehearsal
on disposable state, browser E2E, and provider-failure fallback. All flags stay
disabled outside the isolated fixture. Prove every successor flag defaults
false, dependency-invalid combinations fail preflight, and import alone opens
no DB, route, worker, or service; this is where `SBEOPS-003` becomes provable.

### SBE-7.2 — isolated 10111 activation and handoff

Build a new isolated DB/root from fixtures, enable flags in dependency order,
and expose the complete dev preview on 127.0.0.1:10111 without sharing mutable
10011 roots. Validate local deterministic behavior and Codex bridge model-schema
paths. Record the remaining company-model quality/latency, target-server 40-
client, service-account, backup/restore, and operational pilot checks as a
separate handoff. Do not claim production readiness or start company calls.

## 7. Packet ownership matrix

Every packet must restate and narrow this matrix in its opening commentary. A
path not listed as owned is read-only unless the integrator explicitly assigns
it before the edit.

Common exception: each packet exclusively owns its exact
`docs/knowledge_spaces/evidence/<packet_id>.md`; this implicit ownership applies
to every row below. Only the predeclared status integrator may edit `STATUS.md`.

| Packet | Owned implementation area | Shared/integrator files | Explicitly out of scope |
|---|---|---|---|
| SBE-0.1 | SBE/current normative docs, OpenAPI/JSON Schemas/examples, contract manifest, audit tool/report-v2/tests | `AGENTS.md`, contracts, backlog/status/traceability/goal | Runtime application behavior, migrations, flags, live state |
| SBE-1.1 | v8 migration/catalog/tests; personal-Wiki schema DTO/repository primitives including lifecycle generation; `KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA` integration/tests | migrations catalog, domain/feature-flag exports | Lifecycle routes/UI, higher roles, content changes |
| SBE-1.2 | v9 migration/catalog/tests; higher-role/native-space schema primitives | migrations catalog, org/domain exports | Scope resolver, publication/UI, graph behavior |
| SBE-1.3 | v10 migration/catalog/tests; derived item/Wiki semantic-lifecycle vector proof; aggregate/auto/semantic-intent/seed-run/reference schema primitives and insert-only intent/effect-key plus closed provenance/cursor constraints | migrations catalog, graph/domain exports | Intent producer/seeder/materializer, candidate/model jobs, retrieval, UI |
| SBE-2.1 | Wiki profile repository/service/API/unit/integration tests; archive/restore lifecycle generation and canonical event payload; `KNOWLEDGE_CORE_MULTI_WIKI_READS` and `KNOWLEDGE_CORE_MULTI_WIKI_WRITES` integration/tests | domain, feature flags, composition, canonical routes, schemas | Semantic restore-event consumer/seeding, conversations, higher scopes, UI, projections |
| SBE-2.2 | Conversation repository/service/API/cache binding tests; `KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING` integration/tests | domain, feature flags, composition, canonical routes, schemas | Retrieval scoring, higher graph, UI rendering |
| SBE-2.3 | Scope/organization capability resolver; Team/executive role-assignment and distribution services/APIs/routes/tests; higher `AccessPlan` enforcement in existing publication/edit/lifecycle/curation/lease/preview/commit/job entry points; stale-role fencing; existing `KNOWLEDGE_CORE_HIGHER_SPACES` plus `KNOWLEDGE_CORE_TEAM_WRITES` and `KNOWLEDGE_CORE_RESPONSIBILITY_WRITES` integration/tests | domain, feature flags, composition/routes | Temporal and semantic-edge entry-point integration (`SBE-3.2`/`SBE-4.2`), graph candidate/model logic, UI |
| SBE-3.0 | Windows directory/file anchor, CAS staging evidence and real-process race tests | shared stager primitive if used by output/projection | Publication policy, extractors, schema, UI, live paths |
| SBE-3.1 | Blob/artifact/publication/extractor/reference worker tests; active/published same-transaction `semantic_edge_intent` plus ordinary-outbox producer with zero semantic job rows | feature flags, composition/routes, publication schemas | Semantic handler/registry/materializer/scoring, temporal footer, UI |
| SBE-3.2 | v12 migration, temporal repository/service/API/routes, manifest-v2 materializer/re-import/tests; `KNOWLEDGE_CORE_PROJECTION_FOOTER_V2` integration/tests | migrations catalog, projection/API schemas, feature flags, composition/routes | Retrieval model, UI styling, live legacy rewrite |
| SBE-4.1 | Aggregate graph repository/service/LCA/generation/projection/tests; `KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS` integration/tests | domain, feature flags, composition/routes, graph schema | LLM judging, ranking, UI |
| SBE-4.2 | Exact handler registry; bounded baseline/detector/org-retarget/lifecycle-reactivation intent seeder, item/Wiki restore-event integration, and intent-to-job materializer; candidate/judge/validator/job/suppression/correction services/tests; `KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL` and `KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE` integration/tests | domain, feature flags, lifecycle/outbox consumer, worker registry/routes/schemas | Merge/replacement/deactivation/purge, UI |
| SBE-5.1 | Retrieval repository, deterministic rank/expansion/cache/context, exact `personalWikisSearch` API/route/tests; `KNOWLEDGE_CORE_HYBRID_RERANK` integration/tests | domain, feature flags, agent runtime/routes/schemas | Cross-encoder model, UI, edge writes |
| SBE-5.2 | Local reranker adapter, temporal intent/fusion/cache/eval tests; `KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER` and `KNOWLEDGE_CORE_TEMPORAL_RANKING` integration/tests | feature flags, model runtime/agent context/schemas | Provider rollout, edge writing, UI |
| SBE-6.1 | Multi-Wiki/archive/upload/footer browser JS/CSS/E2E | shared browser bundle, routes/schemas | Higher graph/correction UI, backend policy changes |
| SBE-6.2 | Higher native/inherited/reference/aggregate graph/correction/distribution/lease/role/admin-break-glass browser E2E | shared browser bundle, routes/schemas | Backend authority changes, purge execution |
| SBE-7.1 | Integrated/fault/security/load/eval/browser tools and evidence | test config, status/contracts only for proven results | Production traffic, live roots, company API |
| SBE-7.2 | Isolated preview composition/config/fixture/operator handoff/evidence | feature flags, preview tool, status | 10011/live roots, production service, company calls |

## 8. File ownership and concurrency

Hot shared files may have only one active writer/integrator packet:

```text
AGENTS.md
knowledge_core/domain.py
knowledge_core/feature_flags.py
knowledge_core/composition.py
knowledge_core/canonical_routes.py
knowledge_core/migrations/*
static/knowledge_core_browser.js
docs/knowledge_spaces/STATUS.md
docs/knowledge_spaces/contracts/*
```

Rules:

- Each packet declares `Owned files`, `Shared files requiring integrator`, and
  `Explicitly out of scope`.
- A sub-agent may write independent repositories/tests only after DTOs are
  frozen. It does not edit hot files or interpret skill/AGENTS instructions for
  the parent.
- Existing uncommitted/user changes are preserved. No reset/checkout/cleanup of
  unrelated work.
- Contract and OpenAPI changes merge as one integrator-owned atomic packet.

## 9. Forbidden implementations

- New nested `AGENTS.md` or reuse of P8/P9 IDs for this lane.
- Editing v1-v7 migration files or old completion evidence.
- A numeric Wiki-count constraint, Wiki-per-query loop, or Wiki-per-worker.
- Automatic/archive-on-age behavior or destructive archive implementation.
- Rebinding an existing conversation to another Wiki.
- Trusting browser cache as authority.
- Rewriting inherited item `source_space_id` to the selected ancestor.
- Copying descendant bodies/edges into higher native spaces.
- Putting cross-Part edges in a Part graph or duplicating one edge at each
  ancestor.
- Letting an LLM add candidate IDs, expand authorization, or directly write DB.
- Padding final results to ten with irrelevant documents.
- Putting footer bytes into canonical body/hash/FTS/dedupe/embedding/model input.
- LLM/OCR/network/filesystem work inside a DB transaction.
- Automatic merge, replacement, conflict winner, deactivation, or purge.
- Enabling production flags, touching live roots, running live migration, or
  calling the company API without the later explicit gate/authority.

## 10. Required validation commands

Use the official venv and an isolated approved temporary root. Do not mix the
two working-directory forms.

From workspace root `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -E -m pytest -q code_assist_v1\<focused-selection>
.\venv\Scripts\python.exe -E -m pytest -q code_assist_v1\<adjacent-regression>
$docsRoot = (Resolve-Path -LiteralPath 'code_assist_v1\docs\knowledge_spaces').Path
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit `
  --docs-root $docsRoot `
  --output -
```

From project root `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`, use only project-
relative pytest selections; run contract audit from workspace root as above:

```powershell
..\venv\Scripts\python.exe -E -m pytest -q tests\<focused-selection>
..\venv\Scripts\python.exe -E -m pytest -q tests\<adjacent-regression>
```

Migration packets also run pristine-create, every supported upgrade path,
failed-upgrade rollback, repeat no-op, fingerprint, FK, and integrity tests. UI
packets run `node --check` for every changed JavaScript file plus browser E2E.
Concurrency/atomic-output behavior requires real processes on Windows; thread-
only simulations are insufficient.

## 11. Handoff format

Every packet ends with:

```text
Packet/state:
Outcome actually achieved:
Owned files and SHA-256:
Commands/results/durations:
Acceptance IDs proven:
Negative/fault evidence:
Feature flags and live-boundary attestation:
Residual risks/blockers:
Exact next eligible packet:
Files the next Agent must not edit concurrently:
```

The final handoff after `SBE-7.2` explicitly separates:

- locally verified deterministic behavior;
- locally verified Codex bridge/provider schema behavior;
- unverified company-model compatibility/quality/latency;
- unverified target-server 40-client, service-account, backup/restore, and pilot
  operations that require external authority/state.
