# Part-shared Knowledge Agent implementation package

Status: implementation in progress. P0, P1, and the complete P2 legacy
migration gate are verified. P2.8 repeated the exact reviewed migration on two
distinct canonical-v7 targets from an approved frozen personal-workspace copy,
proved a zero-insertion rerun and strict checksum equality, and archived the
hash-bound artifacts in `evidence/P2.8.md`. Production startup, live migration,
traffic cutover, company-API execution, and personal/part writes remain
disabled; P7.8-P7.9 and P8 remain open.

This directory is the single planning baseline for converting the current personal Second Brain into a part-shared Knowledge Agent for approximately 40 users on one Windows GPU server. It preserves the decisions confirmed with the user and turns them into implementation contracts, migration gates, and measurable acceptance criteria.

## Approved Second Brain successor lane

Successor contract state: **registered** by SBE-0.1. The exact active target is
`second_brain_evolution/PRODUCT_CONTRACT.md`; report-schema-v2 audits 41
requirements, 20 goals, 284 scenarios, 72 OpenAPI paths, 80 operations (27 GET),
and 31 stable public errors. The frozen v7 baseline is 52 paths/56 operations;
the existing SBE-0.1/SBE-6.2 successor adds 18/22, and the approved SBE-8.12
original-source amendment adds 2/2. The earlier 70/78 registration remains
historical evidence. The 2026-09-06 amendment passed a fresh zero-error contract
audit and 77 focused tests; evidence is appended to `evidence/SBE-8.12.md`.
SBE-8.12 remains `IMPLEMENTED_NOT_GATED`. Registration changes no runtime flag,
DB, root, worker, server, or provider call. SBE-1.1 is the first runtime
prerequisite packet.

On 2026-08-23 the user approved a successor product contract covering multiple
personal Wikis without a numeric product cap, native Team/담당 writing by their
exact leaders, descendant aggregate views, LCA-scoped aggregate graphs,
validated automatic semantic edges, full-corpus authorized retrieval with a
96-KiB model-excerpt budget, rich canonical uploads, and projection-only
temporal footers.

The authoritative planning package is
[`second_brain_evolution/README.md`](second_brain_evolution/README.md). It uses
the `SBE` packet lane and forward canonical v8-v12 migrations. The existing P-phase
text and evidence below describe the verified canonical-v7 baseline and remain
historically valid; they do not prove successor behavior. `SBE-0.1` has
synchronized the full human/machine contract, so implementation starts at
SBE-1.1 while every successor runtime flag remains false.

## Target outcome

The first production release provides:

- administrator-managed `AMHS담당 > 팀 > 파트` hierarchy;
- at most one active part membership per user (unassigned is personal-only) and one designated leader per write-enabled part;
- personal, current-part, and personal+part publication targets;
- logically independent personal and part copies with common provenance but no automatic synchronization;
- exactly one knowledge space per Agent conversation;
- part-wide collaborative editing with exclusive leases, immutable revisions, audit history, reversible deactivation, and restoration;
- space-isolated search, graph, pins, index/log views, curation candidates, and Agent context;
- deterministic duplicate handling and a human-reviewed semantic curation workflow;
- durable jobs, reproducible projections, tested backup/restore, and a Windows production deployment.

Team and 담당 publishing remain schema-supported but disabled in phase 1.

## Architecture in one view

```text
upload / Notion / chat / legacy import
                  |
          source artifact + extraction
                  |
           publication batch
             /           \
    personal item       part item
    independent revs    independent revs
             \           /
        central canonical SQLite
     org | space | item | revision | edge
     lease | audit | finding | outbox | FTS
                  |
        idempotent materializer/worker
                  |
 Markdown | index/log | graph/backlinks | caches
             replaceable projections
```

An immutable initial content/blob may be shared internally by hash. That is storage deduplication, not a shared mutable document: the first edit creates a new revision for only the edited item.

## Document map

| Document | Purpose |
|---|---|
| `DECISIONS.md` | Accepted architecture and product decisions; implementation may not silently reopen them |
| `ARCHITECTURE.md` | Domain model, schema, constraints, state machines, transaction and module boundaries |
| `SCHEMA_V1.md` | Physical SQLite v1 tables, types, keys, checks, triggers, indexes, FTS, and transition constraints |
| `API_UI_CONTRACT.md` | Server API, authorization, errors, and user-interface behavior |
| `CURRENT_SYSTEM_INVENTORY.md` | Existing read/write routes, files, stores, and required replacement mapping |
| `MIGRATION_ROLLOUT.md` | Dry-run migration, reconciliation, cutover, feature flags, rollback, and legacy treatment |
| `TEST_OPERATIONS.md` | Acceptance scenarios, performance profile, failure injection, backup, deployment, monitoring |
| `DEVELOPMENT_BASELINE.md` | Reproducible venv, locked install, runtime version gate, isolated test contract, and regression commands |
| `IMPLEMENTATION_BACKLOG.md` | Ordered, dependency-aware implementation tasks and phase gates |
| `TRACEABILITY.md` | Requirement and goal-metric mapping to decisions, implementation, and tests |
| `GOAL_CONTRACT.json` | Machine-readable `G-01..G-20` thresholds and rollout completion policy |
| `STATUS.md` | Current execution state, evidence links, blockers, and next task |
| `contracts/` | OpenAPI 3.1, Draft 2020-12 schemas/examples, and deterministic contract manifest |
| `second_brain_evolution/` | Approved successor product contract, forward schema, dependency-ordered Agent runbook, and acceptance matrix |

Completed task evidence is recorded under `evidence/` and is not a substitute for the phase gate.

## Delivery sequence

1. Establish a reproducible test/runtime baseline and backup inventory.
2. Add the central database, migrations, organization/space model, and DB-backed user/audit repository.
3. Dry-run and then idempotently migrate existing personal knowledge.
4. Replace every legacy knowledge read/write path with `ScopeResolver` and the canonical repository.
5. Bind conversations, pins, retrieval, graph, and system views to one immutable space.
6. Add personal/part/both publication and stable source provenance.
7. Add edit leases, revisions, deactivation/restoration, activity, and administrator purge eligibility.
8. Add incremental deduplication, lint findings, reviewed semantic edges, and transactional merge.
9. Add the durable worker, materialized projections, production WSGI services, monitoring, backup, and restore automation.
10. Pass cross-process, 40-client, failure, migration, and restore gates before enabling part writes.

The detailed task IDs and gates are in `IMPLEMENTATION_BACKLOG.md`.

The successor sequence is maintained separately in
`second_brain_evolution/EXECUTION_PLAN.md`. It begins with contract transition,
which SBE-0.1 has completed, then v8-v12 schema/services, upload/graph/retrieval/UI, integrated gates, and an
isolated 10111 preview. Existing P7.8/P7.9/P8 target evidence is deferred until
that behavior stabilizes so target load/restore/cutover is measured against the
actual release candidate.

## Scope boundaries

Included in phase 1:

- personal and part knowledge spaces;
- direct publication from upload, chat promotion, and an accessible Notion page snapshot;
- organization administration and part-leader assignment;
- complete version/audit/deactivation lifecycle;
- existing personal-data migration and compatibility until cutover.

Not included in phase 1:

- simultaneous real-time co-editing;
- automatic synchronization between personal and part copies;
- automatic semantic merge, contradiction resolution, or edge approval;
- query-time union of personal, part, team, or 담당 directories;
- automatic permanent deletion;
- active team/담당 publishing UI;
- multi-server deployment.

These exclusions describe the original canonical-v7 phase. The SBE successor
explicitly changes the multi-Wiki, higher-scope, automatic-edge, and aggregate-
retrieval exclusions while retaining no-auto-sync, no-auto-destructive-action,
single-server, and explicit-production-approval boundaries.

## Definition of done

The current machine goal is complete only when goal rows `G-01` through `G-20`
in `TRACEABILITY.md` have evidence and all 284 active mandatory scenarios across
`TEST_OPERATIONS.md` and `second_brain_evolution/ACCEPTANCE.md` pass. SBE-0.1
registered those metrics and scenarios; documentation completion alone does not
complete the implementation goal.

## Additive workspace features

The approved 2026-09-07 [Wiki files and shared feedback amendment](WORKSPACE_FEATURES_20260907.md)
and its [verification evidence](evidence/WORKSPACE_FEATURES_20260907.md) cover
original linking, selected-Wiki downloads and the shared request board. This
application extension does not change the historical canonical schema or mark
the broader development/production gates complete.

The [agent knowledge-loop extension](AGENT_INTEGRATIONS_20260907.md) adds scoped
Hermes/API access, selected-Wiki authoring, execution evidence and Notion records.
Its independent acceptance/evidence packet preserves the frozen SBE contracts.
