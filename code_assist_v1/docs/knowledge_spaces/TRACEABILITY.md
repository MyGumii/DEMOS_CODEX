# Requirements and goal traceability

This file is the coverage ledger. A requirement is complete only when its implementation and named acceptance evidence exist; a documentation link alone is not completion.

2026-09-08: SBE-8.13 reopens the local evidence/compression/multi-author validation
work described in [SBE-8.13](evidence/SBE-8.13.md). Its first packet extends
existing `RANK-009`, `RANK-011`, `RANK-012` evidence with whole Korean bodies,
exact source ranges and four-scope delayed materialization. No new machine
scenario IDs or production-readiness claim are introduced. The live model,
multi-pass, citation and authoring gates remain open.
The second packet adds existing `RANK-004..006`, `RANK-015` and `RANK-017`
coverage for expanded-query direct/graph grading inputs, plus verified blob
materialization and a real fenced revision edit. Its model-free 158-test run
and post-version two-test run do not close `RANK-021` large-source work bounds,
the multi-author live matrix, or the company-provider/target-operation gates.
Third-packet tests extend those same ranking scenarios with both-hop/alternate
dedupe before source reads, exact source/wire boundaries, all-ID fair allocation,
disabled-runtime preparation bypass and final-answer body preservation. The
Windows synthetic resource cases and final 199-test run are model-free evidence,
not a closed `RANK-021` target load gate. No new scenario IDs or live/full-goal
completion claim are added.
Fourth-packet tests extend `RANK-009..014` and `REF-006` evidence with body-only
server citation handles, actual selected-view original downloads, same-filename
multi-source isolation and immutable R1 reference provenance after an R2 edit.
Pure output tests do not prove `RANK-014` semantic citation precision. Live
browser/SSE/lifecycle, compatibility provenance and authoring gates remain open;
native host failures and an unobserved broad run are not counted as passes.
Subsequent retained terminal runs pass 276 disjoint model-free cases. The eight
four-scope cases also exercise `CONV-012` storage/reconnect behavior
through services and the SSE event encoder; this is not a browser/network
acceptance claim. See SBE-8.13 for exact files, XML artifacts and limitations.

2026-09-09 fifth-packet tests extend existing `UPLOAD-005` with exact extraction
identity, complete chunk accounting, bounded provider requests, separately
stored authored snapshots, and honest model-unavailable fallback. The optional
v14 test catalog is not a production schema transition. The final 176-test
regression is model-free; its 30-test synthesis v2 slice adds independent-review
counterexamples for context-only support, narrow exact boundary quotes,
atomic page windows and exactly-once input ownership. Neither card accounting nor a valid quote proves the
generated sentence's meaning. Real PDF model evidence stays ungraded until
source-to-Wiki omissions and compression are independently reviewed. No new
scenario IDs, company model claim or four-scope upload completion is added.
The subsequent v4 checkpoint extends `UPLOAD-005` with a verified map ledger,
ordered whole-wave identities and exact predecessor responses. Its 237-case
model-free regression and latest 66-case synthesis run (60 overlap) include
core reconstruction in fresh Python processes, not durable worker recovery.
Optional selection guards do not change extraction-only publication. The
real v3 probe remains failed: AMHS timed out and a completed food Wiki omitted
a non-gold condition. Semantic-compression and all-scope live gates remain open.

The later publication/reuse checkpoint extends `UPLOAD-005`, `UPLOAD-007..009`
and `UPLOAD-011`: actual internal authored-body publication, exact original/
extraction provenance, immutable selected-body metadata, DB-time expiry fences,
both-target rollback, normal curation/search release, and two Part authors'
mutual retrieval without a personal-source leak. UTF-8 upload/extraction-worker
fixtures now exercise personal/Part/both and native Team/Responsibility authored
reuploads with different fake model wording. First stable target-native bodies
are reused, and every fresh artifact/extraction provenance remains independent.
An eight-upload case checks one Python reuse summary rather than growing history
tuples; it is not large-DB/SQL-memory or concurrent-load acceptance. Existing
unchanged-publication lineage semantics are preserved. Optional v14 remains
isolated; public rich upload, actual provider answers and durable authoring
integration are not complete. No scenario IDs or completion gates are removed.

The next extraction-first checkpoint extends the same upload scenarios: an
exact-source ready A2 cannot duplicate an earlier extraction Wiki or relabel it
as authored. Immutable extraction-reuse receipts keep actual E1 separate from
the requested candidate and fresh E2 provenance. Four native scopes, departed
Part cowriters, CRLF, one-sided both, mixed-kind exact-body ordering, edit versus
same-body R2, rollback and lost-ack replay are covered by isolated tests and
independent review. The prior five-file authored checkpoint is terminal at
151 cases, not still running. New terminal counts/hashes are recorded separately
in SBE-8.13; no live/provider/compression acceptance follows from these fixtures.

## 1. Confirmed product and architecture requirements

The internal chunk-worker continuation extends `UPLOAD-005` and existing
`JOB-001..006` evidence: source/plan preparation outside writers, sealed
request-work reservations, deterministic per-ordinal jobs, actual fresh-process
claim and post-response recovery, at-most-one canonical effect, independent
pre-model/commit authority checks, direct Team/Responsibility role end, and
multiple Part authors continuing across another corpus publication. These are
UTF-8 uploads with fake providers, not rich-file semantic compression or actual
all-scope answers. The parent upload operation remains authoritative; chunk jobs
are internal successors rather than new user approval operations. Full source
and plan revalidation per chunk is not a proof of linear whole-document CPU/RSS.
No goal/scenario count or release gate is removed.

The following pure synthesis continuation extends UPLOAD-005: restart
reconstruction from complete maps and exact saved response/model/transport
receipts, ordered v4 metadata preservation, missing-only batch work, terminal
wave completeness, context-bearing single-map negatives and cumulative durable
retry-product admission. A fresh interpreter reproduces exact final Wiki and
manifest bytes without invoking a provider. The OpenAI-compatible fixture
validates exact wire receipts and never selects Codex as fallback. These are
193 focused model-free cases; they do not prove durable reduction job recovery,
actual company transport/model behavior, semantic compression or live scope
acceptance. All full-goal gates remain open as recorded in SBE-8.13.

| ID | Requirement | Decision/design | Implementation tasks | Acceptance evidence |
|---|---|---|---|---|
| `R-001` | Organization hierarchy is 담당→team→part with stable rename/move identity | ADR-001; Architecture §5.1 | P1.3, P1.5, P1.8 | `ORG-001`, `ORG-002` |
| `R-002` | A user has at most one current organization home; Part users retain one active Part, while direct Team leaders/Responsibility executives have no Part membership | ADR-001; SBE product contract §5.2; forward schema v13 | P1.3, P1.5, P1.7; SBE-8.8 | `ORG-003`, `ORG-004`, `SPACE-001`, `PUB-008`, `HSPACE-003..005` |
| `R-003` | Admin UI creates/renames/moves units, assigns users and part leaders under AMHS담당 | ADR-001, ADR-006; API §8/§10.6 | P1.5, P1.8 | `ORG-001`, `ORG-005`, `ORG-006` |
| `R-004` | One selected view is resolved to a server-authorized `CorpusClosure`; successor flags off preserve personal/current-Part compatibility | ADR-014 as superseded by SBE product contract §6 | P1.6-P1.7, P3.6; SBE-2.3 | `SPACE-004`, `SPACE-005`, `HSPACE-006..014`, `RANK-001..003` |
| `R-005` | Publish targets are personal, part, or personal+part | ADR-004; API §4/§10.2 | P4.2, P4.3, P4.5 | `PUB-001..003`, `PUB-008` |
| `R-006` | Personal+part creates independent items/revisions and never auto-syncs | ADR-004 | P4.3, P6.6 | `PUB-003`, `PUB-009`, `PROP-002..005` |
| `R-007` | Agent retrieval uses exactly one concrete knowledge space; private memory/project context is not implicitly mixed into part scope | ADR-005; Architecture §8; API §2 | P3.1, P3.3, P3.4 | `CONV-001`, `CONV-004`, `CONV-007`, `CONV-010`, `SPACE-006` |
| `R-008` | Switching Agent personal↔part starts a new conversation | ADR-005; API §2/§10.1 | P3.1, P3.6 | `CONV-002`, `CONV-003` |
| `R-009` | All active part members may create/edit/deactivate/restore and reviewed-curate part knowledge | ADR-006; API §9 | P4.3, P5.1-P5.3, P6 | `SPACE-002`, `LIFE-001`, `CUR-*` |
| `R-010` | Part leader exists and can force-release; ordinary member cannot | ADR-001, ADR-006, ADR-007 | P1.5, P1.7, P5.1, P5.5 | `ORG-006`, `LEASE-007`, `LEASE-008` |
| `R-011` | Concurrent editing is exclusive until completion/cancel/expiry | ADR-007; Architecture §6.2/§7.3 | P5.1, P5.2, P5.5 | `LEASE-001..012` |
| `R-012` | Delete means reversible deactivation; restore keeps same knowledge identity | ADR-008; API §6 | P5.3, P5.4 | `LIFE-001..006` |
| `R-013` | Ninety days is purge eligibility, never automatic; administrator purges manually | ADR-008, ADR-015 | P5.6 | `LIFE-007..010`, `SCHEMA-003` |
| `R-014` | Actor/date/reason and all changes are auditable and revision/audit metadata is immutable | ADR-003, ADR-008, ADR-011 | P1.3-P1.4, P5.2-P5.4, P7.3 | `REV-001..002`, `SCHEMA-003`, `LIFE-001`, `PROJ-001` |
| `R-015` | Deactivate does not destroy graph; restore re-enables only originally active edges | ADR-008, ADR-010; Architecture §5.5 | P5.3 | `LIFE-004`, `LIFE-005` |
| `R-016` | Accessible Notion content can publish to selected target; cross-part sharing is allowed | ADR-004; API §4 | P4.2, P4.4, P4.5 | `PUB-008`, `PUB-010`, `PUB-011` |
| `R-017` | Native Team/담당 spaces support descendant aggregate reads and exact directly affiliated current leader/executive writes behind successor flags | ADR-014 as superseded by SBE product contract §5-§6; forward schema v13 | P1.3, P1.6; SBE-1.2, SBE-2.3, SBE-8.8 | `SPACE-005`, `SCHEMA-001`, `HSPACE-001..018` |
| `R-018` | Duplicate knowledge and lint are explicitly curated as corpus grows through legal state transitions | ADR-009; Architecture §6.3/§9 | P1.3, P6.1-P6.3, P6.7 | `CUR-001..004`, `CUR-007..011`, `SCHEMA-006` |
| `R-019` | Merge is reviewed, locked, revision-safe, audited, and recoverable | ADR-009, ADR-010 | P6.5 | `MERGE-001..004` |
| `R-020` | Target is approximately 40 users on one Windows GPU server | ADR-002, ADR-012, ADR-015 | P7.2, P7.5, P7.8 | `PERF-001`, `JOB-005`, `OPS-005` |
| `R-021` | Central DB is canonical; physical integrity/immutability/FTS constraints hold; Markdown/index/log/graph are replaceable projections | ADR-002, ADR-003; Schema | P1.2-P1.3, P7.3 | `SCHEMA-002..006`, `REV-001`, `PROJ-001..006`, `MIG-008` |
| `R-022` | Raw artifact, extracted snapshot, and editable revision remain distinct | ADR-009; Architecture §5.3/§5.4 | P4.1 | `PUB-007`, `PUB-011` |
| `R-023` | Web/process restart cannot lose or duplicate ingestion/projection/GPU work | ADR-012 | P1.9, P7.1-P7.3 | `PUB-012`, `JOB-001..006`, `PROJ-002` |
| `R-024` | Existing user knowledge migrates to personal only with no silent loss | ADR-013; Migration §4-§6 | P2.1-P2.8 | `MIG-001..008` |
| `R-025` | Production uses verified SQLite/WSGI/backup/restore, not development-only runtime | ADR-015 | P0.4, P7.4-P7.9 | `OPS-001..006`, `SCHEMA-008`, `PERF-001` |
| `R-026` | Scope and mutation authority are revalidated server-side at commit | ADR-005, ADR-007, ADR-011 | P1.7, P3.4, P4.3, P5.1-P5.3 | `SPACE-003..004`, `CONV-004..005`, `LEASE-010`, `SCHEMA-002` |
| `R-027` | A stable source update becomes a proposal, not numbered duplicate or overwrite | ADR-004, ADR-009; API §4/§7 | P4.4, P6.6 | `PUB-010`, `PROP-001`, `PROP-003..005` |
| `R-028` | Evidence-validated semantic edges may activate automatically; destructive/conflict document curation and all T3 effects remain human-controlled | ADR-009/ADR-010 with narrow SBE semantic-edge supersession | P6.3-P6.8; SBE-4.2 | `CUR-005`, `CUR-006`, `CUR-009..011`, `AUTOEDGE-004..019` |
| `R-029` | Personal→part update uses fork-base three-way comparison | ADR-004 | P6.6 | `PROP-002..005`, `MERGE-004` |
| `R-030` | Account/user removal cannot cascade away shared knowledge/audit | ADR-011; API §8 | P1.4, P1.8 | `AUTH-001..003`, `SCHEMA-007` |
| `R-031` | Cookie-session mutations and stream initiation enforce CSRF/origin policy and secure production-cookie attributes | API §1; Test §4 | P0.6, P1.4, P3.4 | `AUTH-004` |
| `R-032` | Owner-scoped personal Wikis have no product count cap, exactly one active default, stable identity, and reversible owner-only archive | SBE product contract §3; forward schema v8 | SBE-1.1, SBE-2.1, SBE-6.2 | `WIKI-001..022` |
| `R-033` | Every conversation is immutably bound to one selected Wiki/view; archived Wiki history is read-only and browser cache is never authority | SBE product contract §4 | SBE-2.2, SBE-6.2 | `WIKI-013..017`, `WIKI-020..022` |
| `R-034` | Native Team/담당 authority uses one direct organization home, fenced role episodes, descendant `CorpusClosure`, and explicit downward distribution | SBE product contract §5-§6; forward schema v9/v13 | SBE-1.2, SBE-2.3, SBE-6.2, SBE-8.8 | `HSPACE-001..018` |
| `R-035` | Aggregate semantic graph edges are stored once at endpoint LCA while Part graphs remain isolated and projections preserve origin | SBE product contract §7.1; forward schema v10 | SBE-1.3, SBE-4.1, SBE-6.2 | `AGRAPH-001..017` |
| `R-036` | Semantic edges auto-activate only after bounded evidence/model/deterministic validation, with exact correction authority and zero document-curation effects | SBE product contract §7.2-§7.3 | SBE-1.3, SBE-4.2, SBE-6.2 | `AUTOEDGE-001..019` |
| `R-037` | Authorized hybrid retrieval covers the selected closure without a ranked/body document-count cap, retains bounded graph/model work and an exact 96 KiB Agent excerpt budget, and has deterministic fallback | SBE product contract §9 | SBE-5.1, SBE-5.2, SBE-8.11 | `RANK-001..021` |
| `R-038` | Rich upload, embedded assets, canonical Markdown, independent personal+Part outcomes, and existing curation visibility form one durable pipeline | SBE product contract §5/§10 | SBE-3.0, SBE-3.1, SBE-6.1 | `UPLOAD-001..015` |
| `R-039` | Typed temporal provenance and deterministic footer/projection v2 stay outside canonical content/search hashes and support safe re-import | SBE product contract §11; forward schema v12 | SBE-3.2, SBE-5.1, SBE-6.1 | `TIME-001..022` |
| `R-040` | `item-ref-v1` is a separate authorization-bound stable-ID reference ledger/list with bounded mandatory context | SBE product contract §8 | SBE-1.3, SBE-3.1, SBE-5.1, SBE-6.1 | `REF-001..008` |
| `R-041` | Successor operations remain count-bounded, use isolated 10111 validation, and explicitly hand off company-model/target-server evidence | SBE execution plan §6-§9 | SBE-0.1, SBE-7.1, SBE-7.2 | `SBEOPS-001..018` |

Coverage status: all product and engineering requirements `R-001..R-041` have at least one decision/design location, implementation task, and acceptance target. The successor machine contract is registered; runtime requirements remain incomplete until their named SBE packet gates pass.

Interpretation note: `R-002` means zero or one current organization home. An
unassigned active user remains personal-only. A `part` home is mirrored by the
existing membership/current-Part fields; a `team` or `responsibility` home must
not be represented by a synthetic descendant Part membership.

## 2. Active-goal quantitative metrics

| ID | Goal completion metric | Design/plan location | Required evidence |
|---|---|---|---|
| `G-01` | 100% requirement→design→API/UI→test trace; unlinked 0 | This file; all documents | Automated doc/link/ID audit plus table review |
| `G-02` | 100% current knowledge read/write/publish/pin/session/graph/index/log/curation paths inventoried and assigned | `CURRENT_SYSTEM_INVENTORY.md` | Inventory coverage report; no unexplained legacy route/storage call |
| `G-03` | Every legacy manifest category resolves exactly once; all source Markdown imports idempotently; silent loss 0; every orphan reported; rerun duplicates 0 | `MIGRATION_ROLLOUT.md`; P2 | `MIG-002..008`, complete terminal-classification and verification manifest/hash |
| `G-04` | Dual publication all-or-none/provenance outcome; partial 0; retry duplicate 0 | Architecture §7.2; API §4; P4 | `PUB-003..013`, transaction/restart failpoint report |
| `G-05` | Two processes/40 clients: exactly one lease holder; stale/forced/moved saves 0 | Architecture §6.2; P5 | `LEASE-002..010`, hot-item raw results |
| `G-06` | Successful save/deactivate/restore/merge/proposal has consistent revision/state, audit, outbox; edge preservation 100% | Architecture §7.3-§7.7; P5/P6 | `REV-002`, `LIFE-004..005`, `MERGE-002`, `PROP-004`, `PROJ-001` |
| `G-07` | Exposure outside the server-authorized closure/overlay is 0 across documents, pins, search, graph, candidates, chat, cache, and citations; existing conversation selection changes 0 | SBE product contract §6; P3/SBE-2.3 | `SPACE-006`, `CONV-001..013`, `HSPACE-006..014`, `RANK-001..003`, `SBEOPS-007` |
| `G-08` | Invalid/unauthorized automatic edge commits 0, automatic destructive effects 0, pre-review hidden exposure 0, T3 model execution 0 | Architecture §9; SBE product contract §7 | `CUR-001..011`, `AUTOEDGE-004..019`, `SBEOPS-006..008` |
| `G-09` | Target 40-client CRUD/FTS/projection/busy SLO remains required and is rebaselined on the exact successor corpus/query/profile after isolated preview | Test §6; P7.8; SBE-7.2 | `PERF-001`, `SBEOPS-009`, target-server handoff |
| `G-10` | Forced restart loses 0 durable jobs, duplicates 0 domain commits, leaves 0 projection mismatch | Architecture §6.4; P1.9/P7 | `PUB-012`, `JOB-001..006`, `PROJ-002..004`, crash logs |
| `G-11` | Exact approved SQLite WAL source, production WSGI, local NTFS, central worker/inference are deployment gates | Architecture §3; Schema; Test §7/§10 | P0 preflight, `SCHEMA-008`, `OPS-004`, `OPS-005`, service readiness |
| `G-12` | Online backup+blob manifest/GC hold; RPO≤1h, RTO≤4h; one successful restore; integrity errors 0 | Test §8-§9; P7.4/P7.9 | `OPS-001..003`, `OPS-006`, signed restore report |
| `G-13` | All 284 named baseline+successor unit/integration/concurrency/fault/migration/recovery scenarios pass; missing/duplicate IDs 0; legacy regression passes | Test §4; SBE acceptance; all phases | Generated scenario-ID report with total 284; `REG-001..003`; `SBEOPS-001..018`; failure/missing/duplicate counts 0 |
| `G-14` | Part shared writes stay off until every P1–P7 organization/space/migration/scope/publication/lease/lifecycle/curation/recovery gate passes | Backlog P1-P8; Migration §7 | Feature-gate record with prerequisite build/schema/deployment/evidence hashes and explicit rollout approval |
| `G-15` | Multi-Wiki identity/default/archive/conversation violations 0 with bounded behavior independent of Wiki count | SBE product contract §3-§4 | `WIKI-001..022`, `SBEOPS-009` |
| `G-16` | Higher-scope foreign exposure, stale-role commits, and invalid distribution effects 0 | SBE product contract §5-§6 | `HSPACE-001..018`, `SBEOPS-006..007` |
| `G-17` | Automatic-edge precision ≥0.95 overall and ≥0.97 for high-risk relations, false active-edge rate ≤0.01, unauthorized edges 0 | SBE product contract §7; Acceptance §5 | `AUTOEDGE-001..019`, held-out corpus report |
| `G-18` | Recall@10≥0.90, nDCG@10≥0.85, MRR≥0.85, citation precision≥0.95, unauthorized citations 0, authorized full-corpus result policy, Agent excerpts≤96 KiB, mandatory contexts≤4, bounded queries/models/workers | SBE product contract §9 | `RANK-001..021`, `SBEOPS-009` |
| `G-19` | Footer changes canonical hashes 0, identical state produces byte-identical projections, freshness/temporal semantic violations 0 | SBE product contract §11 | `TIME-001..022` |
| `G-20` | Isolated 10111 preview closes all local gates and separately records company-model and target-server evidence still requiring external authority | SBE execution plan SBE-7.2 | `SBEOPS-012..018`, final handoff |

## 3. Implementation artifacts expected per phase

| Phase | Required durable evidence |
|---|---|
| P0 | Locked dependency hashes, baseline and `REG-001..003` characterization reports, preflight output, isolated-path test, OpenAPI/JSON Schema and doc-ID audit |
| P1 | Schema checksum, migration tests, org/space/capability report, admin UI smoke, minimal durable-kernel restart/claim/idempotency report |
| P2 | Inventory/plan/reconciliation/quarantine manifests and idempotent rerun checksum |
| P3 | Scope canary matrix across API/Agent/projection and conversation-space immutability tests |
| P4 | Publication transaction failpoint matrix and 1,000-retry idempotency report |
| P5 | Two-process 40-client lease/race results, audit/outbox consistency, edge checksum |
| P6 | Curation shadow precision sample, semantic-auto count, merge/three-way evidence |
| P7 | Load raw samples, job/projector crash report, service preflight, backup/restore report |
| P8 | Cutover/pilot approval, feature-gate record, monitoring/rollback evidence, final goal audit |

Current P0 execution evidence is recorded in `evidence/P0.1.md` through
`evidence/P0.6.md`. P0.4 supplies the runtime-preflight portion of `R-025` and
`OPS-004`; P0.5 supplies the read-only storage and G-02 code-callsite inventory
baseline. P0.6 machine-verifies all 31 requirement rows, 14 goal rows, 124 named
scenario IDs, 55 API operations, schema roles/examples, and requirement/scenario
ranges. It supplies the planning-contract portions of G-01 and G-13, not their
future implementation/pass evidence. P0 does not claim the P1 schema inventory or
P7 production-service, cross-process readiness, backup, and restore portions of
`SCHEMA-008` or `OPS-001..006`.

P1 implementation evidence is recorded in `evidence/P1.1.md` through
`evidence/P1.9.md`. P1.1 supplies the shared, side-effect-free package and
composition boundary used by all three hosts. P1.2 supplies the isolated
connection, explicit transaction, runtime/pragma/FTS, migration-ledger,
rollback, and reviewed physical-fingerprint infrastructure used by `R-021` and
the later `SCHEMA-008` gate. P1.3 supplies the frozen physical v1 schema,
official-runtime fingerprint, and executed `SCHEMA-001..008` acceptance proof.
P1.4 supplies DB-backed user/audit/dedup/outbox repositories, account lifecycle
atomicity, legacy-login compatibility, forward migration v2, and executed
`AUTH-001..004` proof. P1.5 supplies the organization repository/service,
impact-bound preview and exact resolution, membership/leader CAS and scope
invalidation, a collision-safe injectable nine-operation administrator
adapter, `ORG-001..006`, and real two-process idempotency/competition proof.
P1.6 supplies the canonical space row mapper/state owner, exact personal and
active-part lower-bound reconciliation, stable ID/state preservation,
transactional audit/outbox/dedup, and a real 40-user/four-part two-process
proof. P1.7 supplies transaction-scoped personal/current-part selection,
canonical epoch/membership/role revalidation, entitlement-versus-rollout
capability calculation, concealment of foreign/former/higher scopes, no-lazy-
write proof, and a real two-process membership-move/old-epoch test. P1.8
supplies the strict canonical tree/user read model, same-snapshot preview
fences, administrator hierarchy/membership/leader/account flows, exact
response-loss retry, unavailable-state fail-closed behavior, and explicit
legacy-login/data isolation. P1.9 supplies strict durable enqueue/claim/lease/
retry/dead/cancel/result primitives, an exact-version immutable handler
registry, actor/scope/capability and expected-state pre-commit revalidation,
same-transaction canonical effect/result commit, and real process-kill,
reclaim, ABA, replay, claim-race, account-disable, and membership-move proof.
This is only the P1 substrate for `R-023` and `G-10`: P4 still owns actual
publication handlers, while P7 owns all-producer cutover, cross-job GPU
coordination, randomized fault profiles, projection recovery, and production
operations. P1.1-P1.9 do not claim a live migration, canonical DB traffic
cutover, or enabled personal/part writes.

P2.1 execution evidence is recorded in `evidence/P2.1.md`. It supplies the
complete schema-v2 `inventory_proposal` prerequisite for `R-024`, `G-02`,
`G-03`, and `MIG-001..008`: fixed source categories, owner/source identities,
record and relationship closure, hash-only logical reconciliation, proposed
terminal classes, and frozen-root/high-water binding. It does **not** satisfy
the migration scenarios themselves. P2.2 must bind a reviewed plan to this
inventory and explicit organization map; P2.3-P2.5 import; P2.6 proves observed
one-of-five outcomes and idempotent checksums; P2.7 supplies the named fault
execution; and P2.8 supplies the later frozen rehearsal. Accordingly, no
`MIG-*`, final G-02, or final G-03 pass is claimed by P2.1 evidence alone.

P2.2 execution evidence is recorded in `evidence/P2.2.md`. It supplies the
reviewed-plan prerequisite for `R-024`, `G-02`, `G-03`, and `MIG-001..008`:
an exact P2.1 manifest reconstruction, explicit owner/organization decisions,
deterministic one-of-five plan classifications, no part knowledge target,
hash-bound technical and review stages, and cross-process no-clobber output.
It does **not** satisfy a migration scenario or an observed outcome. P2.3-P2.5
still own canonical imports and P2.6 still owns plan-versus-observed equality
and idempotent rerun checksums; therefore no `MIG-*`, final G-02, or final G-03
pass is claimed at P2.2.

P2.3 execution evidence is recorded in `evidence/P2.3.md`. It closes the
reviewed users/personal-items/assets implementation slice with root-bound
source reads, content-addressed storage, canonical personal effects, immutable
record/relationship mappings, audit/outbox emission, bounded transactions, and
exact crash/resume replay. This is still prerequisite evidence for `R-024`,
`G-02`, `G-03`, and `MIG-001..008`, not their final result: P2.4-P2.5 still own
the remaining imports, P2.6 owns observed one-of-five equality and rerun
checksums, P2.7 supplies the named fault execution, and P2.8 supplies the frozen
rehearsal. Accordingly no final G-02 or G-03 pass is claimed by P2.3 evidence.

P2.4 execution evidence is recorded in `evidence/P2.4.md`. It closes the
reviewed graph-node/edge, candidate-finding, exact-duplicate provenance, and
labelled legacy history implementation slice. Every imported endpoint is
revalidated against one canonical user and personal space; canonical effects,
immutable record/relationship checkpoints, audit, and outbox commit together;
legacy audit/log/tombstone data is explicitly non-authoritative. This remains
prerequisite evidence for `R-024`, `G-02`, `G-03`, and `MIG-001..008`, not their
final result: P2.5 owns conversations and operational state, P2.6 owns observed
one-of-five equality/rebuild/rerun checksums, P2.7 supplies named fault
execution, and P2.8 supplies the frozen rehearsal. Accordingly no final G-02 or
G-03 pass is claimed by P2.4 evidence.

P2.5 execution evidence is recorded in `evidence/P2.5.md`. It closes reviewed
personal conversations and every session child by exact count/hash, valid
Notion session-page rebinding against a copied frozen SQLite component set, and
terminal/abandoned legacy job history without creating a runnable durable job.
Canonical effects, immutable record/relationship checkpoints, audit, and
outbox commit together and exact replay is a no-op. This remains prerequisite
evidence for `R-024`, `G-02`, `G-03`, and `MIG-001..008`, not their final
result: P2.6 owns observed one-of-five equality, full rebuild, and rerun
checksums; P2.7 supplies the named fault execution; and P2.8 supplies the frozen
rehearsal. Accordingly no final G-02 or G-03 pass is claimed by P2.5 evidence.

P2.6 execution evidence is recorded in `evidence/P2.6.md`. The strict verifier consumes the
exact reviewed plan, validates every record and relationship checkpoint against
its phase-specific immutable target contract, reopens imported CAS bytes,
records observed one-of-five outcomes, binds report cardinality to the reviewed
plan ledgers, and rebuilds transactional `search_document`/FTS from canonical
active/published revisions. It also writes checksum-verified immutable
per-space Markdown/index/log/graph/backlink generations through pinned Windows
handles, atomically replaces each small manifest, rejects canonical drift, and
advances `projection_state` only after exact revalidation. Exact replay,
cross-space canaries, publish-failure recovery, hard-link rejection, and broad
partitions pass. This closes the P2.6 implementation gate. P2.7 supplies the
named `MIG-*` fault/large execution below and P2.8 supplies the later frozen
rehearsal; final G-02/G-03 are not claimed by P2.6 evidence alone.

P2.7 execution evidence is recorded in `evidence/P2.7.md`. The isolated
dirty/large matrix executes `MIG-001..008` end to end through inventory,
reviewed planning, bounded apply, strict verification, rerun, and rebuild
classification. It proves explicit quarantine/unavailable accounting, zero
unresolved records or relationships, personal-only imported knowledge,
rebuild-not-import treatment, batches of at most 100 across a 206-item Unicode
and Windows long-path fixture, pre-checkpoint rollback, and real process death
after a committed batch followed by immutable-checkpoint resume. This closes
the named P2.7 fault/large gate for `R-024` and supplies direct `MIG-001..008`
execution evidence. P2.8 has now repeated the sequence on the approved frozen
personal-workspace copy; `evidence/P2.8.md` closes final G-02/G-03 and the full
P2 gate. Production migration and traffic cutover remain unauthorized.

The P2.8 rehearsal execution boundary and frozen-copy phase gate are now
closed. `migrate rehearse` requires two distinct empty canonical targets,
 executes primary apply/verify, an exact zero-insertion rerun, and a fresh-target
 reproduction, then emits one hash-closed artifact binding the reviewed-plan
 document, exact bounded batch size, and both target identities. Report schema
 v2 rejects an out-of-range batch and an exact-report retry with a different
 batch size. Permanent isolated tests prove target
 emptiness/disjointness, companion-plan and target tamper rejection, output
 containment before mutation, same-target idempotency, archived strict-
 verification revalidation on retry, and fresh-target equality.
The separate fresh-only deployment command creates each current canonical-v7
target with
an exclusive parent-relative file handle, refuses initial and late file winners,
and proves the canonical database contains only its reviewed bootstrap seed.
The rehearsal validator now binds both targets to the exact current migration
catalog checksum as well as its version, so two equally forged catalogs cannot
self-validate.
The approved v3 frozen-copy sequence is archived in `evidence/P2.8.md`. It
binds the exact snapshot, inventory, reviewed plan/organization mapping, two
canonical-v7 target identities, zero-insertion rerun, observed terminal ledger,
and rebuild hashes. Together with P2.1-P2.7 it closes `R-024`, G-02/G-03, and
the full `MIG-001..008` migration gate without authorizing production cutover.
The current read-only code ledger closes 692 callsites as 387 mapped plus 305
retained/excluded, with zero unknown access classifications, zero unclassified
callsites, and all eight submitters mapped; historical phase evidence remains
unchanged.

P3.1 execution evidence is recorded in `evidence/P3.1.md`.
The current inert repository/service slice directly exercises the repository
portion of `R-007`, `R-008`, `R-026`, and `R-031`: immutable conversation-space
identity, current scope revalidation, expected row-version lifecycle changes,
exact turn replay/reconnect, one completed assistant message or sanitized
failed/cancelled terminal checkpoint, atomic terminal audit/outbox, and versioned compact
summaries. It also provides a canonical-only legacy JSON-session projection
whose imported-session resolution is bound to P2.5 hash evidence and has no
legacy-file fallback. Focused `CONV-011`/`CONV-012` behavior is implemented;
the compact-summary portion of `CONV-013` is covered, but `CONV-013` as a whole
remains open because pins and explicit context bindings belong to P3.2. No
P3 scenario or G-07 pass is claimed until the later API/Agent canary gate.

P3.2 execution evidence is recorded in `evidence/P3.2.md`. It advances the
repository/service portions of `R-007` and `R-026` and closes the storage
portion of `CONV-013`: pins are fixed to one current active/published revision
in the conversation's exact space, and explicit context bindings are resolved
by a trusted server boundary rather than browser-supplied artifact/hash state.
Personal and part fixtures prove direct cross-space rejection, and a part
conversation has no implicit private binding. Canonical change, operation
dedup, audit, and outbox commit together with exact replay and rollback proof.
This is substrate evidence for `G-07`, not its final result. P3.3-P3.6 still
own canonical retrieval, per-turn access-loss handling, API/Agent streaming,
diagnostics/canaries, compatibility regression, and traffic cutover, so no
full P3 scenario or G-07 pass is claimed here.

P3.3 execution evidence is recorded in `evidence/P3.3.md`. It advances the
retrieval portions of `R-007` and `R-026` and provides direct repository/service
evidence for `CONV-001`, `CONV-004`, `CONV-005`, `CONV-009`, and `SPACE-006`:
one conversation-derived or explicitly authorized management space is resolved
inside the same read snapshot as literalized canonical FTS, stale scope and
cross-space canaries fail closed, and only current active/published revisions
leave the repository. Returned graph neighbors are effective and same-space;
recent events are restricted to the returned item set; lagging file projections
fall back to transactional FTS. This remains substrate evidence for `G-07`, not
its final result. P3.4-P3.7 still own live turn composition, pin/context
revalidation, legacy parallel-read removal, compatibility routes/UI, and the
API/Agent canary gate.

P3.4 execution evidence is recorded in `evidence/P3.4.md`. It advances the
turn-composition portions of `R-007`, `R-026`, and `R-031` and directly covers
the inert service boundary for `CONV-004`, `CONV-007`, `CONV-008`, `CONV-009`,
and `CONV-010`: the request cannot select a space or alternate search text,
the service derives both from the accepted conversation turn, and the final DB
snapshot rechecks actor scope, turn identity/state, every pin, and the complete
explicit binding set after external live authorization. Only one canonical FTS
call is made, all Markdown remains labelled untrusted reference data, and no
legacy KB/SB, private-memory, default-project, or browser-state path exists in
the canonical service. Live availability transitions are atomic with audit and
outbox. This is still substrate evidence for `G-07`: P3.5-P3.7 own compatibility
routes, UI scope behavior, API/Agent canaries, and the actual legacy-traffic
disablement, so no full P3 scenario or G-07 pass is claimed.

P3.5 execution evidence is recorded in `evidence/P3.5.md`. It advances the
read-compatibility portions of `R-007`, `R-026`, and `R-031` and provides inert
service-level evidence for `SPACE-006` and `PROJ-006`: every legacy-shaped
knowledge list/view/search/thought, graph, finding, and generated index/log read
is derived from one authenticated personal-space `DEFERRED` snapshot. The
legacy `uid` cannot select another user, personal/part canaries do not cross the
adapter, FTS syntax is literalized, graph endpoints are current same-space
items, and findings come only from `curation_finding`. The implementation has no
legacy store, projection-file refresh, or write-SQL path, and invalid current
body/search/metadata evidence fails closed. This is still substrate evidence
for `G-07`: P3.6 owns visible scope selection and immutable new-conversation
UX, while P3.7 owns route wiring, API/Agent canaries, session regression, and
actual legacy-traffic disablement. No full `SPACE-006`, `PROJ-006`, P3, or G-07
pass is claimed here.

P3.6 execution evidence is recorded in `evidence/P3.6.md`. It advances `R-004`
and `R-008` and provides disabled-boundary proof for `SPACE-004`, `SPACE-005`,
`CONV-002`, and `CONV-003`: the selector service exposes only the authenticated
personal/current-part set, a requested management selection never rewrites an
existing conversation, and an owner-bound conversation always retains its
separate immutable badge even after access becomes stale. The inert browser
controller reloads the seven named management surfaces immediately, while a
different chat selection exposes only the explicit new-conversation action and
uses one stable idempotency key for concurrent/retried creation. Higher-level
or forged spaces, foreign conversations, stale epochs, mismatched create
results, and malformed snapshots fail closed. The component is present but
hidden until P3.7 supplies canonical route adapters. P3.7 still owns live route
activation, full API/Agent canaries, reconnect/session regression, legacy-read
disablement, and therefore the full P3/G-07 gate.

P3.7 execution evidence is recorded in `evidence/P3.7.md`. It closes the tested
P3/G-07 implementation gate for `SPACE-006` and `CONV-001..013`: canonical
browser chat/session/SSE and all seven management surfaces bind to the strict
Flask group; the server principal remains the sole actor/scope authority;
conversation space is immutable; retries/reconnect preserve operation and turn
bindings; foreign personal/part list, search, graph, pin, projection, candidate,
activity, and chat paths fail closed. A real localhost browser-to-Flask-to-Agent
canary proves one selected-part answer and zero legacy/private implicit context.
Injected core mode imports/registers no legacy knowledge/SB/memory/project/chat/
session/harness routes. This is a disabled-boundary verification result: live
feature flags and production traffic remain unchanged and operational cutover
is deliberately deferred.

P4.1 execution evidence is recorded in `evidence/P4.1.md`. It advances the
artifact/extraction provenance prerequisite for `R-009`, `R-012`, and
`PUB-011`: raw CAS evidence, immutable ready/failed extraction metadata/body,
exact extracted bytes, LF-normalized evidence, sanitized deterministic lint,
and a body-free preparation hash are distinct and independently rebound
before insert/replay. The boundary writes no knowledge item or revision and its
preparation code has no DB/filesystem/network/model dependency. This is not a
publication-scenario pass: at the P4.1 boundary P4.2-P4.6 still owned preview/
job durability, authorization/idempotency, personal/part target resolution,
atomic outcomes, provenance/lineage, and the complete `PUB-001..013` gate.

P4.2 execution evidence is recorded in `evidence/P4.2.md`. It advances the
preview portions of `R-005`, `R-009`, `R-012`, and `R-016`: upload, manual,
canonical chat-range, and authorized Notion sources are bound to immutable
artifacts/snapshots; canonical actor/scope/part state freezes available target
modes; the exact-version durable handler owns asynchronous extraction and
terminal convergence; actor-authorized polling reconstructs durable state; and
SQLite-time expiry plus the 30-day zero-reference gate preserve evidence while
removing only eligible body links. This task-level evidence intentionally does
not mark a `PUB-*` acceptance scenario complete. P4.3-P4.6 still own actual
item/revision outcomes, dual-target all-or-none publication, Notion update
proposals, route/UI activation, exhaustive failpoints/retries, and the complete
`PUB-001..013` matrix.

P4.3 execution evidence is recorded in `evidence/P4.3.md`. It advances the
implementation behind `R-005`, `R-006`, `R-009`, `R-026`, and `G-04`: one
source-snapshot-bound operation atomically resolves personal, current-part, or
both targets; created targets receive separate provisional item/revision IDs;
both targets are permanently sync-none; exact same-space content resolves to
provenance/no-op; and audit, outbox, target generation, preview consumption,
and the stored operation result share the target transaction. Stable-origin
content change is deliberately rejected for P4.4 rather than duplicated, and
no semantic edge/model path is invoked. This task-level evidence still does
not mark a `PUB-*` acceptance scenario complete. P4.4 owns the concrete update
finding/proposal, P4.5 the route/UI boundary, and P4.6 the exhaustive
failpoint/process/1,000-retry `PUB-001..013` gate.

P4.4 execution evidence is recorded in `evidence/P4.4.md`. It advances the
implementation behind `R-005`, `R-006`, `R-009`, `R-016`, and `R-027`: an
authorized Notion source carries exact body-free owner/page/revision/
source-snapshot/publisher/captured-part provenance; unchanged stable content is
no-op; and changed stable content preserves the current item/revision while one
deterministic T2 finding and one fixed pending update proposal commit with the
publication result, preview consumption, audit, and outbox. Personal and part
proposals remain independent and a `both` repeat performs no synchronization.
Both existing legacy Notion route wrappers are proven to share one immutable
snapshot/write delegate. P4.5 now closes the disabled canonical route/UI
boundary with exact preview/status/cancel/commit and target discovery, a shared
personal/current-part/both modal for upload/chat/Notion, a no-sync warning,
target-specific outcomes, and fail-closed capability fallback. Its standalone
Notion adapter excludes conversation/retrieval dependencies and its non-mutating
probe permits legacy fallback only when the canonical surface is authoritatively
absent, never after an unknown mutation outcome. Task-level evidence is in
`evidence/P4.5.md`. It intentionally does not mark a `PUB-*` or `PROP-*`
acceptance scenario complete; P4.6 owns the exhaustive failpoint/process/
restart/1,000-retry `PUB-001..013` gate.

P4.6 execution evidence is recorded in `evidence/P4.6.md`. It closes the tested
P4/G-04 implementation gate for `PUB-001..013`: personal, current-part, and
dual publication produce exact independent outcomes; every observed
application DML boundary on both dual-create and dual stable-Notion-update paths
rolls back to the same ready-preview state; 1,000 exact retries, concurrent
processes, and a post-commit response-loss retry create no duplicate revision;
and every successful target is joined back to its source artifact, extraction,
revision hashes, audit, and outbox. Durable synchronous/asynchronous previews
survive service, worker, and process reconstruction; cancellation, expiry,
consumption, authority/scope/source drift, and the 30-day zero-reference cleanup
remain fail-closed. This is acceptance evidence behind disabled traffic, not a
feature-flag or production rollout approval. The later P2.8 frozen-copy gate is
closed independently, and P5 may consume the closed P4 implementation
prerequisite.

P5.1 execution evidence is recorded in `evidence/P5.1.md`. It advances
`R-009`, `R-010`, `R-011`, `R-026`, and `G-05` with a persistent fenced lease
repository/service: exact HMAC token proof with active/old key overlap,
monotonic acquire/reclaim/force generations, SQLite-time stale and absolute
expiry, expected item base, commit-time membership/capability revalidation,
explicit local-draft recovery, atomic audit/outbox/idempotency for terminal
mutations, and zero raw-token disclosure. Real 40-process contention proves
exactly one holder and 39 stable `LEASE_HELD` outcomes without a busy result.
This directly closes the P5.1-owned scenarios `LEASE-002`, `LEASE-004`,
`LEASE-007`, `LEASE-008`, `LEASE-009`, and `LEASE-011`. At that task boundary it
did not claim the save-coupled portions of `LEASE-001`, `LEASE-003`,
`LEASE-005`, `LEASE-006`, `LEASE-010`, or `LEASE-012`; P5.2 now supplies the
separate same-transaction save proof below. No route, flag, database, worker,
or production traffic was enabled.

P5.2 execution evidence is recorded in `evidence/P5.2.md`. It advances
`R-011`, `R-014`, `R-021`, `R-026`, `G-05`, and `G-06` by consuming the P5.1
fence in the same transaction as one immutable revision/body, one queued
versioned rescan and revision-rescan transition, item current-revision/version
CAS, fixed-revision finding/proposal obsolescence, semantic-edge revalidation,
FTS removal, generation advance, lease release, one audit, one outbox, and exact
edit/job idempotency results. The save-coupled task evidence covers
`REV-001..002`, `LEASE-001`, `LEASE-003`, `LEASE-005..006`, `LEASE-010..012`;
P5.1 retains its lease-only coverage for `LEASE-002`, `LEASE-004`, and
`LEASE-007..009`. Every required named edit failpoint and all 16 observed DML
boundaries roll back to the exact held-lease baseline. P5.7 still owns the real
web-process/40-client lifecycle races and aggregate P5 acceptance claim. No
route, flag, worker, live database, or production traffic was enabled.

P5.3 execution evidence is recorded in `evidence/P5.3.md`. It closes the
P5.3-owned implementation proof for `R-009`, `R-012`, `R-014`, `R-015`,
`R-021`, `R-026`, and `G-06`: a general active part member can deactivate and
restore; actor/time/reason/exact 90-day eligibility and the latest episode are
durable; foreign live leases conflict while actor-owned/expired leases are
fenced; default management/search/Agent retrieval excludes inactive items;
same item/revision IDs return on restore; stored active/inactive edge and
reference rows remain byte-identical. Executable scenario ownership is
`LIFE-001..006`. Named failpoints, all 9 deactivate/8 restore DML boundaries,
and two service-instance contenders prove atomicity in one process. P5.7 still
owns actual two-web-process save/deactivate/restore contention, 40-client load,
and the aggregate P5 gate; P5.6 owns `LIFE-007..010` purge behavior. No route,
UI, flag, worker, live database, or production traffic was enabled.

P5.4 execution evidence is recorded in `evidence/P5.4.md`. It closes the
P5.4-owned read/UI proof for `R-012`, `R-014`, `R-021`, `G-05`, and `G-06`:
one authorized space yields exact current item metadata/body/state/capability,
token-free lease and edge/reference/pin impact, distinct content/review/inactive
ages, immutable actor/time/reason/purge eligibility, sanitized activity, and
immutable revision list/detail/compare. Revision bodies remain separate from
metadata and blob bytes are loaded outside the transaction then bound to a
second scope/evidence snapshot. Permanent tests exercise `REV-001..002`,
`LIFE-001`, `LIFE-003`, and `PROJ-001`, cross-space denial, cursor/query
rejection, deterministic diff hashing/bounds, exact browser transport, and
text-only rendering. P5.5 still owns lease editor mutation controls; P5.6 owns
purge; P5.7 owns actual two-web-process contention, 40-client load, and the
aggregate P5 gate. No feature flag, live database, worker, or production
traffic was enabled.

P5.5 execution evidence is recorded in `evidence/P5.5.md`. It advances
`R-009`, `R-010`, `R-011`, `R-014`, `R-026`, `R-031`, `G-05`, and `G-06` by
exposing the already-tested P5.1/P5.2 transaction services through an optional
five-route mutation surface and a canonical browser editor. Holder operations
use a header-only 256-bit token and fence plus explicit body session/lease
identity; save also binds expected revision/row version and optional matching
`If-Match`. The browser keeps one private token closure, heartbeats, releases
on completion/cancel/leave best effort, renders foreign locks read-only,
preserves conflict drafts, and offers only view/copy/reacquire-and-compare.
Force release requires current leader/admin authority in the service and a
reason plus exact UI/API confirmation. Permanent route/transport/browser tests
cover the P5.5-owned UI/API portions of `LEASE-001..008`, `LEASE-011..012`.
P5.7 still owns the actual two-web-process, forty-client aggregate lease and
lifecycle gate; no rollout flag or production traffic was enabled.

P5.6 execution evidence is recorded in `evidence/P5.6.md`. It closes the
task-owned implementation proof for `R-012`, `R-013`, `R-014`, `R-021`,
`R-026`, `R-030`, and the purge portion of `G-06`. Permanent scenarios
`LIFE-007..010` prove the strict 90-day DB-time boundary, zero automatic purge,
active-admin-only preview/execute, exact signed preview/confirmation, retained
tombstone/hash/provenance/audit metadata, atomic eligible body/FTS removal,
blocked/shared-body retention, edge immutability, idempotent replay, and
purged-restore rejection. Schema v7 and explicit Blob GC tests additionally
prove reference/backup claim fences, short DB transactions around external I/O,
exact root-bound Windows CAS deletion, missing/retry behavior, and zero deletion
for wrong-byte or multiply linked objects. P5.7 evidence below now supplies the
actual two-web-process, forty-client lifecycle/load result and aggregate P5
acceptance claim; P7 owns worker scheduling and production backup/recovery. No
feature flag, live root/database, scheduler, worker, or production traffic was
enabled.

P5.7 execution evidence is recorded in `evidence/P5.7.md`. It combines the
previously frozen `REV-001..002`, `LEASE-001..012`, and `LIFE-001..010`
matrices with an actual two-OS-process WSGI correctness gate and forty
simultaneous HTTP clients. The raw result is exactly one holder/39
`LEASE_HELD`, zero forced/moved/stale save successes, one valid-holder save
revision/audit/outbox commit, one deactivate state/audit/outbox commit, one
restore state/audit/outbox commit, and identical pre/post edge+relation
checksum. The aggregate P5 selection passes 125/125. This closes the P5
implementation/correctness contribution to `G-05` and `G-06` behind disabled
traffic; it does not claim P7.5 production WSGI packaging, P7.8 target-server
`PERF-001`, or any rollout/operations gate.

P6.1 execution evidence is recorded in `evidence/P6.1.md`. It advances the
deterministic portions of `R-018`, `R-021`, `G-08`, and `G-13`: source/exact/
normalized fingerprints and target-space identity are frozen under
`p6.1-deterministic-ingest-v1`; same-space byte-exact repeat may add provenance
only; normalized-only/source-change/cross-space matches remain no-effect
candidates; multiple canonical targets fail closed; and structural/corpus
metrics are bounded, order-independent, hash-sealed, and body-free. Permanent
tests exercise the P6.1-owned portions of `CUR-001`, `CUR-002`, and `CUR-010`.
This does not claim the aggregate P6 scenarios or gate: P6.2-P6.8 retain
candidate retrieval, persisted findings, visibility, human review, semantic
edges, merge/proposals, shadow precision, and zero-model-execution evidence.

P6.2 execution evidence is recorded in `evidence/P6.2.md`. It advances the
candidate-retrieval portion of `R-018`, `G-08`, and `G-13`: exactly one changed
current revision and concrete space bind one bounded literal FTS5/BM25 query;
only current active/published same-space targets can enter deterministic top-K;
source/cross-space/provisional/stale rows cannot; and output is body-free,
hash-sealed, independently validated evidence. Permanent tests exercise the
retrieval-owned portion of `CUR-002` and prove model calls, all-pairs scans,
semantic effects, and writes are structurally zero. This does not claim
`CUR-003`/`CUR-004` policy or the aggregate P6 gate: P6.3 still owns finding
persistence, severity/risk/reviewer/visibility derivation, and lifecycle.

P6.3 execution evidence is recorded in `evidence/P6.3.md`. It advances the
finding-lifecycle portions of `R-018`, `R-026`, `R-028`, `G-06`, and `G-08`:
all 18 v1 policy tuples are server-derived; durable evidence binds current
same-space revisions, verified Markdown excerpts, detector/rule/model/prompt
versions, and the exact validated P6.2 candidate snapshot; and T2/T3 review
re-resolves authenticated canonical authority in the mutation transaction.
Weak complementary remains a published badge with no action, strong risky
findings remain pending human review/Agent-hidden, revision drift becomes
obsolete, and ignored suppression is exact-input/version scoped. Finding,
idempotency result, audit, and outbox are atomic. T3 approval records no domain
effect and neither the repository nor service can invoke a model or semantic/
destructive action. Permanent tests exercise P6.3-owned portions of
`CUR-003..004` and `CUR-006..011`; P5.2 retains the real edit-coupled
obsoletion proof. This does not claim semantic-edge application, merge,
proposal/UI/shadow precision, the complete `CUR-*` matrix, or the aggregate P6
gate; P6.4-P6.8 retain those responsibilities.

P6.4 execution evidence is recorded in `evidence/P6.4.md`. It advances the
semantic-edge application portions of `R-015`, `R-018`, `R-026`, `R-028`,
`G-06`, and `G-08`: an explicitly approved P6.3 semantic finding is rebound to
its expected row version, exact canonical evidence hash, current source/target
revisions, active/published same-space endpoints, current editor capability,
and writable space generation. The immutable finding action, not a client
field, derives the six-value edge type. Finding application proof, one
`semantic_approved` edge, generation, operation result, audit, and outbox are
atomic; endpoint drift instead records `obsolete` with no edge. Permanent tests
exercise P6.4-owned portions of `CUR-005`, `CUR-007`, and `CUR-011`, including
identity collision, changed evidence, idempotency, secret-free evidence, and
all write failpoints. Review/detector/model paths commit zero semantic edges and
deterministic provenance remains `item_relation`. This does not claim merge,
proposal/UI/shadow precision, the complete `CUR-*` matrix, or the aggregate P6
gate; P6.5-P6.8 retain those responsibilities.

P6.5 execution evidence is recorded in `evidence/P6.5.md`. It advances the
transactional-merge portions of `R-009`, `R-011`, `R-014`, `R-018`, `R-019`,
`R-026`, `R-028`, `G-06`, and `G-08`: preview binds two exact current
revisions, row versions, bodies, result Markdown, and complete affected
edge/reference ledgers outside the write transaction. Apply repeats the exact
Markdown, recomputes the preview, and validates both current edit fences in
sorted item-ID order. Target revision/rescan/provisional state, source merged
redirect, immutable `merged_into` relation and merge event, stale finding/
proposal obsoletion, FTS removal, one generation increment, dual lease release,
audit, outbox, and idempotency commit atomically. Existing semantic-edge IDs,
types, endpoints, and evidence revisions are preserved; affected rows only
become inactive and require review/revalidation. Permanent tests exercise
`MERGE-001..004`, exact terminal replay, changed-command rejection, token
confidentiality, and every documented merge write failpoint. This does not
claim proposal/rebase/apply, UI/shadow precision, the complete `CUR-*` matrix,
or the aggregate P6 gate; P6.6-P6.8 retain those responsibilities.

P6.6 execution evidence is recorded in `evidence/P6.6.md`. It advances the
proposal portions of `R-006`, `R-009`, `R-011`, `R-014`, `R-018`, `R-027`,
`R-029`, `R-031`, `G-06`, and `G-08`: stable-source preview remains bound to
one immutable extraction snapshot, while personal-to-part preview fixes the
fork base, personal current revision, and part current revision. Rebase is an
idempotent create-new/obsolete-old transaction and never overwrites a changed
target. Apply requires the recorded reviewer, exact finding/proposal versions,
fixed preview, current source/target evidence, and the target lease token/fence
before one approved-update revision/rescan and the pair/audit/outbox/search/
generation effects commit. Permanent tests exercise `PROP-001..005`, personal
source and target drift, non-reviewer rejection, exact response-loss replay
without blob reopening, request rebinding, confidentiality, and every proposal
apply/rebase write failpoint. This does not claim route/UI activation, shadow
precision, the complete `CUR-*` matrix, or the aggregate P6 gate; P6.7-P6.8
retain those responsibilities.

P6.7 execution evidence is recorded in `evidence/P6.7.md`. It advances the
review-surface and observational portions of `R-018` and `G-08`: authorized
curators receive body-free queue/detail evidence, warnings, visibility, and
review lifecycle controls; proposal/merge commands remain explicit reviewed
service calls; and one bounded read-only report derives model precision plus
general, risky-kind, and T3 automatic-execution counts. The report passes only
when all three execution counts are zero. Routes remain absent without an
explicit curation service and write-capable rollout, and no model, worker,
feature flag, service process, live root/database, or traffic was activated.
This does not claim the complete `CUR-*`, `PROP-*`, or `MERGE-*` acceptance
matrix or the aggregate P6 gate; P6.8 retains that responsibility.

P6.8 execution evidence is recorded in `evidence/P6.8.md` and closes the P6
implementation/test gate behind disabled traffic. Explicit permanent test
owners cover `CUR-001..011`, `PROP-001..005`, and `MERGE-001..004` without
gaps. Fixture/corpus metrics prove zero false automatic merge; provisional and
strong risky findings remain outside Agent retrieval and use server-derived
`agent_hidden` policy before review; semantic edge, merge, replace, deactivate,
purge, and T3 model automatic-effect counts remain zero. Proposal and merge
apply still require the recorded human review plus exact revision and lease/
fence evidence. This is not a rollout or live-corpus claim: no flag, traffic,
detector, model, worker, service process, live root, or live database was
enabled or used.

P7.1 execution evidence is recorded in `evidence/P7.1.md` and advances the
durable-work portions of `R-023`, `G-10`, and `G-13`. The P1.9 durable
repository/kernel remains the authority for exact-version restart-safe input,
lease/attempt fencing, retry/dead/cancel convergence, current authorization and
expected-state revalidation, and same-transaction canonical effect/result.
P7.1 closes the generic public actor boundary: a current user can read or
idempotently cancel only their own user job; foreign/system rows are
nondisclosing, mutation/audit/operation result are atomic, and strict routes and
browser transport preserve the frozen job schema. Permanent real-process tests
continue to own `JOB-001..006`; P7.1 reruns those owners rather than duplicating
scenario markers. This does not close `G-10` or the P7 gate: P7.2 evidence
below closes central model concurrency/fairness and the worker loop, P7.3
evidence closes projection materialization, and P7.4-P7.9 own production
preflight, faults, performance,
backup, and restore. No worker, flag, traffic, service process, live root, or
live database was enabled or used.

P7.2 execution evidence is recorded in `evidence/P7.2.md` and advances the
central-inference portions of `R-020`, `R-023`, `G-10`, `G-11`, and `JOB-005`.
Canonical producers persist durable references; the explicit service performs
expired-claim recovery, renewable heartbeat, bounded polling, and one-job
execution. Handler-version and actor-lane ordering comes from durable attempt
history rather than a restart-reset process cursor. Production web roles cannot
discover, resolve, load, or execute GGUF, and legacy callable jobs/scheduling
remain development-only. The sole production worker role must hold a
machine-wide Windows mutex, rejects another database/deployment owner, and can
reclaim after owner-process death. This is not the final `G-10`, `G-11`, or P7
gate: P7.3 evidence below closes projection materialization, P7.4-P7.6 close
backup/restore, production packaging, and runtime health below, and P7.7-P7.9
own randomized faults, target-class load, and signed restore. No worker, model, traffic,
service process, flag, live root, or live database was enabled or used.

P7.3 execution evidence is recorded in `evidence/P7.3.md` and closes the
disabled-implementation portions of `R-014`, `R-021`, `R-023`, `G-06`, and
`PROJ-001..006`. Canonical generation, durable outbox, and desired projection
state commit or roll back together. The explicit leased worker emits one
checksum-bound immutable per-space generation and atomically publishes only its
manifest; full and settled incremental results match. Real process death before
ack replays without duplicate generation effects, concurrent claim is fenced,
canonical advance prevents stale ack, and canonical DB/FTS remains the retrieval
fallback. This does not close aggregate `G-10`, `G-11`, or P7: P7.4-P7.6 are
closed below while P7.7-P7.9 still own target fault/load and operational
recovery. No worker/service, traffic, flag, live root, or live
database was started or accessed.

P7.4 execution evidence is recorded in `evidence/P7.4.md` and closes the
disabled implementation portions of `R-025`, `G-12`, `OPS-001..003`, and
`OPS-006`. The online snapshot, exact blob manifest, durable GC hold, atomic
completed-directory publication, 96/30/12 UTC retention, isolated restore,
canonical integrity gate, and full projection rebuild are executable through
the operator CLI/wrappers. ADR-016 explicitly selects roll-forward-only recovery
after the first canonical production write; P8.1 must bind that decision into
the signed approval packet. This does not close aggregate `G-12` or P7: P7.5
and P7.6 close production packaging and health below, P7.7-P7.8 own target
fault/load evidence, and P7.9 owns the target-server signed restore report. No
live root, external backup volume, service, traffic, worker, or flag was used.

P7.5 execution evidence is recorded in `evidence/P7.5.md` and closes the
disabled implementation portions of `R-020`, `R-025`, `G-11`, `OPS-004`, and
`OPS-005`. The exact Waitress and WinSW versions/hashes, WSGI limits, five
process roles, dedicated account/ACL, local non-reparse path policy, protected
environment, logs, fresh-bootstrap migrator, graceful reverse drain, and
machine-wide periodic `PASSIVE` checkpoint owner are self-hashed or hard-coded
and independently tested. Actual child processes prove both Flask apps use
Waitress, not Werkzeug, complete an in-flight request after named stop, and
enforce cross-process checkpoint exclusion. This is not target deployment
evidence: no SCM service/account/ACL, production port, worker, live root/DB,
flag, or traffic was used. P7.6 closes health/metrics below; P7.7-P7.9 still own
randomized fault/restart, target-class load, and signed target restore/service
observation, so aggregate `G-10`, `G-11`, `G-12`, and P7 remain open.

P7.6 execution evidence is recorded in `evidence/P7.6.md` and closes the
disabled implementation portions of `R-020`, `R-025`, `G-10`, `G-11`,
`G-12`, `OPS-004`, and `OPS-005` that belong to health and metric exposure.
The versioned, secret-free provider uses validated read-only SQLite boundaries
and one read snapshot for persisted aggregates; it reports exact build/schema/
SQLite/pragmas/flags, DB activity, queue/outbox attempts/retries/age/dead state,
leases, backup age, projection generation/lag/error, and DB/WAL/blob capacity.
Database, parent, and blob roots are revalidated as fixed local NTFS,
non-reparse, final-path-identical before observation, and the start wrapper
requires the exact report schema plus a ready gate with zero reasons. This is
not target monitoring or deployment evidence: no service, traffic, worker,
live root/DB, flag, alert sink, or target performance run was used. P7.7 closes
the isolated randomized fault/restart boundary below; P7.8-P7.9 still own
target-class load and signed target restore/service observation. Aggregate
`G-10`, `G-11`, `G-12`, and P7 remain open.

P7.7 execution evidence is recorded in `evidence/P7.7.md` and closes the
disabled implementation portions of `R-020`, `R-024`, `R-025`, `G-10`, and
`G-11` assigned to `PROJ-001..006`, `JOB-001..006`, and `OPS-001..006`. The
54 required failpoint names are exact, unique, implemented, and permanently
exercised. Transaction failures preserve the prior canonical state;
post-commit response loss and real process kills converge through stable replay
with lost jobs and duplicate effects both zero. Projection disk-full,
permission, atomic-replace, abandoned-staging, and replacement-before-ack
paths recover; a fixed-seed profile performs exactly 20 child-process worker
terminations; online backup under concurrent writes verifies successfully.
This is isolated workstation evidence, not target deployment or load proof. No
service, traffic, worker, live root/database, feature flag, or target host was
used. P7.8-P7.9 still own `PERF-001` target-class load and the signed target
restore/deployment evidence; aggregate `G-10`, `G-11`, `G-12`, and P7 remain
open.

P7.8 now has a sealed runner and independent evidence-verification boundary,
but no target result. The runner fixes the five profile durations, 40-client
identity, steady mix, projection count, health sampling, nearest-rank
percentiles, raw busy/error/canary observations, two-production-WSGI binding,
and a complete 40x39 leak matrix. A five-profile gate binds report/CSV hashes
to one fixture, seed, target, hardware, application/config/migration, and
SQLite identity. These tests establish tooling coverage for `R-020`, `R-025`,
`G-09`, and `PERF-001`; they are not execution evidence. Those rows remain
open until the target-class reports pass and are archived.

## 4. Deliberately deferred decisions

These do not block phase-1 implementation and must not be guessed into enabled behavior:

- Team/담당 publisher/editor/leader permission details beyond schema support.
- Whether higher-level publication is direct authoring, reviewed promotion, or both. Recommended default remains independent promoted snapshot.
- Multi-server/PostgreSQL transition timing; repository boundaries preserve the option.
- Target-server drive letters, service account, TLS/reverse-proxy topology, and off-machine backup destination.

Their feature flags remain off until separately confirmed.
