# Migration, rollout, rollback, and reconciliation runbook

> The legacy-to-canonical migration evidence below remains unchanged. The
> approved canonical v7->v12 successor sequence and packet gates are in
> `second_brain_evolution/FORWARD_SCHEMA.md` and `EXECUTION_PLAN.md`. Target load,
> restore, and cutover are execution-deferred until SBE-7.2 rebaselines them.

The production migration is not authorized by this planning package. The implementation must first provide the dry-run CLI, test fixtures, backup tooling, and gates below. Real data is migrated only during an explicitly approved maintenance window.

## Registered successor feature-flag dependency contract

SBE-0.1 registers, but does not implement or enable, the following exact DAG.
The machine twin is `contracts/successor-feature-flags.json`; every listed flag
defaults false. Startup preflight must reject an effective child whose required
parent/service/schema is absent. Existing parent gates retain their current
meaning throughout rollout.

| Successor flag | Exact prerequisites |
|---|---|
| `KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA` | none; schema installation alone carries no traffic authority |
| `KNOWLEDGE_CORE_MULTI_WIKI_READS` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA`, `KNOWLEDGE_CORE_READS` |
| `KNOWLEDGE_CORE_MULTI_WIKI_WRITES` | `KNOWLEDGE_CORE_MULTI_WIKI_READS`, `KNOWLEDGE_CORE_PERSONAL_WRITES` |
| `KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING` | `KNOWLEDGE_CORE_MULTI_WIKI_READS` |
| `KNOWLEDGE_CORE_TEAM_WRITES` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_PART_READS`, `KNOWLEDGE_CORE_HIGHER_SPACES` |
| `KNOWLEDGE_CORE_RESPONSIBILITY_WRITES` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_PART_READS`, `KNOWLEDGE_CORE_HIGHER_SPACES` |
| `KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_PART_READS`, `KNOWLEDGE_CORE_HIGHER_SPACES` |
| `KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL` | `KNOWLEDGE_CORE_ENABLED`; concrete source also requires its current read/write gates |
| `KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL`, `KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS` |
| `KNOWLEDGE_CORE_HYBRID_RERANK` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_READS` |
| `KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER` | `KNOWLEDGE_CORE_HYBRID_RERANK` |
| `KNOWLEDGE_CORE_TEMPORAL_RANKING` | `KNOWLEDGE_CORE_HYBRID_RERANK` |
| `KNOWLEDGE_CORE_PROJECTION_FOOTER_V2` | `KNOWLEDGE_CORE_ENABLED`, `KNOWLEDGE_CORE_PROJECTIONS` |

The existing `KNOWLEDGE_CORE_PART_WRITES` remains an additional concrete-source
gate for Part publication and Part-local edge workers; Team/담당 native writes
instead require their exact role episode and matching higher write flag. Flag
registration changes no environment value, database, root, worker, route, or
server process.

## 1. Migration principles

- Existing user-owned knowledge and conversations migrate to that user's personal space only.
- No existing file, Notion share, chat, or apparent topic is automatically assigned to a part.
- Preserve the original snapshot and hash; never rewrite legacy content in place.
- Derived indexes/backlinks are rebuilt rather than treated as truth.
- Unknown ownership, missing assets, invalid edges, truncated data, and ambiguous duplicates are quarantined and reported, never silently skipped.
- Migration is idempotent by legacy identity/path/hash and can resume after a committed batch.
- Do not operate a long-lived legacy/new dual-write system.
- Full Notion page/block content remains in the Notion source system in phase 1. Knowledge publication captures an immutable source snapshot and provenance; it does not wholesale migrate Notion into the knowledge database.

## 2. Planned migration CLI contract

The implementation creates a CLI under the new package. Names may be adjusted once, but every capability below is mandatory and stable before rehearsal:

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate inventory `
  --legacy-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1 `
  --output C:\CodeAssist\migration\inventory.json

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate plan `
  --inventory C:\CodeAssist\migration\inventory.json `
  --inventory-manifest-sha256 <INVENTORY_MANIFEST_SHA256> `
  --snapshot-attestation-sha256 <SNAPSHOT_ATTESTATION_SHA256> `
  --root-binding-sha256 <ROOT_BINDING_SHA256> `
  --org-map C:\CodeAssist\migration\org-map.csv `
  --org-map-sha256 <ORG_MAP_DOCUMENT_SHA256> `
  --output C:\CodeAssist\migration\plan-draft.json

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate confirm-plan `
  --draft C:\CodeAssist\migration\plan-draft.json `
  --draft-sha256 <EXACT_DRAFT_DOCUMENT_SHA256> `
  --review-attestation-sha256 <EXTERNAL_REVIEW_ATTESTATION_SHA256> `
  --reviewed-at-utc 2026-08-13T01:02:03.000000Z `
  --reviewer-id <REVIEWER_ID> `
  --output C:\CodeAssist\migration\reviewed-plan.json

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate apply `
  --plan C:\CodeAssist\migration\reviewed-plan.json `
  --plan-sha256 <EXACT_REVIEWED_PLAN_SHA256> `
  --data-root C:\CodeAssist\data `
  --database C:\CodeAssist\data\knowledge-core.db `
  --blob-root C:\CodeAssist\data\blobs `
  --projection-root C:\CodeAssist\data\projections `
  --batch-size 100 `
  --resume

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate verify `
  --plan C:\CodeAssist\migration\reviewed-plan.json `
  --plan-sha256 <EXACT_REVIEWED_PLAN_SHA256> `
  --data-root C:\CodeAssist\data `
  --database C:\CodeAssist\data\knowledge-core.db `
  --blob-root C:\CodeAssist\data\blobs `
  --projection-root C:\CodeAssist\data\projections `
  --strict `
  --report C:\CodeAssist\migration\verification.json

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate bootstrap-rehearsal `
  --data-root C:\CodeAssist\rehearsal-primary `
  --database C:\CodeAssist\rehearsal-primary\core\knowledge-core.db `
  --blob-root C:\CodeAssist\rehearsal-primary\blobs `
  --projection-root C:\CodeAssist\rehearsal-primary\projections

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate bootstrap-rehearsal `
  --data-root C:\CodeAssist\rehearsal-fresh `
  --database C:\CodeAssist\rehearsal-fresh\core\knowledge-core.db `
  --blob-root C:\CodeAssist\rehearsal-fresh\blobs `
  --projection-root C:\CodeAssist\rehearsal-fresh\projections

.\venv\Scripts\python.exe -m code_assist_v1.knowledge_core.cli migrate rehearse `
  --plan C:\CodeAssist\migration\reviewed-plan.json `
  --plan-sha256 <EXACT_REVIEWED_PLAN_SHA256> `
  --primary-data-root C:\CodeAssist\rehearsal-primary `
  --primary-database C:\CodeAssist\rehearsal-primary\core\knowledge-core.db `
  --primary-blob-root C:\CodeAssist\rehearsal-primary\blobs `
  --primary-projection-root C:\CodeAssist\rehearsal-primary\projections `
  --fresh-data-root C:\CodeAssist\rehearsal-fresh `
  --fresh-database C:\CodeAssist\rehearsal-fresh\core\knowledge-core.db `
  --fresh-blob-root C:\CodeAssist\rehearsal-fresh\blobs `
  --fresh-projection-root C:\CodeAssist\rehearsal-fresh\projections `
  --batch-size 100 `
  --report C:\CodeAssist\migration\rehearsal.json
```

The P2.2 `plan` and `confirm-plan` commands only transform offline, bound
artifacts. The P2.3/P2.4/P2.5 `apply` command imports the reviewed identity,
personal-content, asset, asset-reference, same-personal-space graph/finding,
labelled historical-data, conversation, Notion-link, and job-history subsets in
bounded resumable transactions. It
refuses every unsupported P2.3/P2.4 effect, source/storage drift, and an
unmigrated database schema. The P2.6 `verify` command now consumes the exact
reviewed plan, independently replays imported target contracts, records one
observed outcome per reviewed record/relationship, reopens every imported CAS
object no-follow, validates schema/FK/space/edge/session/asset closure, and
rebuilds transactional `search_document`/FTS plus immutable per-space
Markdown/index/log/graph/backlink generations from canonical state. It verifies
every emitted checksum, atomically publishes only the small per-space manifest,
and advances `projection_state` after a canonical-drift recheck. P2.6 is closed
with isolated-fixture evidence in `evidence/P2.6.md`. P2.7 now closes the named
dirty/large `MIG-001..008` fault matrix in `evidence/P2.7.md`; P2.8 still owns
the frozen sanitized/current-copy rehearsal. The P2.8 `rehearse` command
requires two distinct, already schema-migrated empty canonical targets. It
runs apply/strict verification on the primary, repeats the same plan there and
requires zero new mappings plus an identical verification artifact, then
 applies to the fresh target and requires identical counts and verification.
 Its canonical hash-closed schema-v2 report binds the exact reviewed-plan
 document, the bounded `1..100` batch size used for all three executions, and
 both target database/storage identities and must be outside the source and
 both targets; that containment is checked before apply and rebound at atomic
 publication. Retrying an already published exact report does not apply again:
 it reopens both bound targets, reruns strict verification, and succeeds only
 when both results still equal the archived verification byte-for-byte and the
 requested batch size still equals the archived execution parameter.
 The command does not create or attest the source copy, so the operator must
still supply the application-consistent snapshot and reviewed organization
mapping required by P2.8.

`bootstrap-rehearsal` is the sole public preparation path for those two empty
targets. All four directories must already exist on an eligible local fixed
NTFS layout, while the database file itself must not exist. The command uses
the approved production SQLite runtime, creates the database with an exclusive
parent-relative handle, denies delete/rename through the migration, applies
the complete checksummed catalog once, and verifies that only the canonical
bootstrap root seed exists. It never opens, upgrades, replaces, or deletes an
existing database; an initial or late competing file is preserved and fails
`SCHEMA_BOOTSTRAP_TARGET_EXISTS`.

P2.1 provides the complete read-only schema-v2 inventory proposal:

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.legacy_inventory inventory `
  --legacy-root C:\CodeAssist\snapshot\code_assist_v1 `
  --knowledge-root C:\CodeAssist\snapshot\knowledge `
  --snapshot-id SNAPSHOT_OR_VSS_ID `
  --snapshot-kind frozen_copy `
  --snapshot-created-at-utc 2026-08-13T00:00:00.000000Z `
  --snapshot-attestation-sha256 <LOWERCASE_SHA256> `
  --runtime-root C:\CodeAssist\snapshot\code_assist_v1\.runtime `
  --user-data-root C:\CodeAssist\snapshot\code_assist_v1\user_data `
  --sessions-root C:\CodeAssist\snapshot\code_assist_v1\sessions `
  --workspace-root C:\CodeAssist\snapshot\code_assist_v1\workspace `
  --scratch-root C:\CodeAssist\migration\inventory-scratch `
  --output C:\CodeAssist\migration\inventory.json
```

`--knowledge-root` is repeatable. `--snapshot-kind` is `frozen_copy` or `vss`;
the timestamp is a real canonical UTC instant and cannot be after scan start;
scan completion cannot precede scan start, and the run ID is derived from the
attestation, snapshot identity/kind, and start instant. The attestation
is a lowercase SHA-256 supplied by the operator's snapshot process. The scanner
binds it to final root facts and scan-time high-water marks but does not
authenticate it, create the snapshot, or prove writer quiescence. Scratch must
be a pre-existing absolute local fixed NTFS root, reparse-free and outside all
sources. This command applies no migration and imports no legacy application or
store module.

The organization map is explicit:

```csv
mapping_schema_version,source_owner_claim,source_record_id,decision,user_id,system_role,account_status,responsibility_code,responsibility_id,team_code,team_id,part_code,part_id,is_part_leader
1,EXAMPLE_USER_LEADER,,personal,example_user_leader,admin,active,AMHS,11111111-1111-4111-8111-111111111111,TEAM_A,22222222-2222-4222-8222-222222222222,PART_A,33333333-3333-4333-8333-333333333333,true
1,,rec_0123456789abcdef01234567,quarantine,,,,,,,,,,false
```

The header and order above are exact schema v1. Exactly one selector
(`source_owner_claim` or `source_record_id`) is present. `decision=personal`
requires the canonical user/role/status; all six organization code/UUID fields
are either present as one exact hierarchy assertion or all blank for explicit
unassignment. `decision=quarantine` has no target fields. UUIDs, not the human
codes, are authoritative because the canonical v1 organization tables do not
store external codes. The planner requires at least one active administrator,
allows at most one leader per represented part, and never creates part/team/
responsibility knowledge. It never infers ownership from a path or Notion
share. A record selector is required to import or explicitly quarantine an
otherwise unowned/global conversation record.

The inventory input must be the scanner's exact canonical JSON plus one LF.
This lets the plan validator reconstruct the sanitized inventory and bind it
directly to the operator-approved document SHA rather than trusting an
artifact-internal recomputed digest. The first command emits a deterministic
`migration_plan_draft`; it is not an
approval. The second emits a `reviewed_migration_plan` whose final hash covers
the exact draft and external review facts. That attestation is supplied by the
operator and hash-bound but is not authenticated by this tool. An existing
byte-identical output is an idempotent success; a different existing artifact
fails `PLAN_DRIFT` and is never overwritten. Both commands are source/DB inert.
The draft carries the P2.1 metadata and original ledger order while each plan
row carries its exact sanitized source record or relationship. The independent
plan validator reconstructs the complete P2.1 report, verifies its manifest and
all P2.1 closure rules, then verifies every planner decision and aggregate. A
missing frozen source root is still treated as a declared containment boundary;
neither command probes that source path. Concurrent identical writers and
readers converge without weakening output-parent identity protections.

## 3. Inventory manifest

The schema-v2 `inventory` command is read-only and emits:

- tool/schema version, run ID, source snapshot/VSS ID, per-source high-water marker, start/end UTC time;
- resolved absolute roots and exclusions;
- SQLite/Python/app version and filesystem information;
- user/account count and per-user source paths;
- per category: file/row count, bytes, SHA-256, read errors, and planned source classification;
- Markdown sources and hidden pages separately;
- graph nodes/edges with valid, orphan, duplicate, and legacy-auto-edge counts;
- pending candidate/finding counts and resolvable revision evidence;
- referenced, missing, and unreferenced assets;
- sessions and Notion session-page links;
- legacy memory/job states;
- root/unowned legacy data;
- `.tmp`, backup DB, and recovery-candidate files;
- a deterministic overall manifest hash.

It fixes 41 categories and the record kinds
`physical_file | logical_record | unavailable`. Every record receives one
**proposed** terminal class (`imported | rebuilt | retained_external |
quarantined | unavailable`) and one authority (`scanner | planner_required`).
The report stage is `inventory_proposal`; parent/child counts, relationship
endpoints, category aggregates, owner facts, source identities, and summary
totals must close under the independent contract validator.

The command exits non-zero in strict mode for unreadable inputs or an
unaccounted category. `--allow-partial` may return a report for record-level
corruption, unknowns, or missing references, but `complete` remains false. It
never relaxes invalid roots, code-callsite coverage, source drift, path
collision/reparse safety, report reconciliation, or SQLite snapshot
consistency. It never displays or uploads document content in the report.
`complete=true` means only that the declared source inventory satisfies this
proposal contract; it does not mean any record has been migrated. P2.2
plan/apply consumers must refuse a different inventory hash, snapshot binding,
or final resolved root from the approved inputs.

## 4. Source classification and action

| Source | Ownership | Migration action |
|---|---|---|
| `.runtime/users.json` | Global | Import identity, password hash, role/status; create personal space |
| `.runtime/audit.jsonl` | Global | Import as labelled legacy audit events where parseable; retain raw snapshot |
| `knowledge/<uid>/wiki/sources/*.md` | User | Import as personal item revision with source/path/hash provenance |
| Valid edges in per-user `graph.json` | User space | Map endpoints to new item IDs; import only explicit/accepted relationships |
| `pending_diffs.json` | User space | Convert resolvable records to versioned findings; otherwise quarantine |
| `wiki/log.md` | User | Preserve as legacy-history artifact/event evidence, not canonical current log |
| `wiki/pages/*.md` | User but currently invisible | Compare with sources, then import or quarantine explicitly |
| `index.md`, `wiki_index.sqlite`, backlinks | Derived | Exclude and rebuild |
| `knowledge/<uid>/{raw,thoughts,inbox,raw_chunks,...}` | User legacy | Dedicated adapter; never assume current UI covered it |
| Root `knowledge/{raw,thoughts,inbox,...}` | Unknown | Quarantine pending explicit owner/space mapping |
| Existing session JSON | User | Import as personal-space conversations because old system had personal knowledge only |
| Browser pins | Not persisted | Cannot migrate; report as unavailable |
| Notion live DB/files | External Notion source | Keep in place; validate adapter, page identity, backup, and publication snapshot flow |
| Notion backup DBs | Recovery candidate | Hash/report; never silently prefer over live DB |
| Legacy job JSON | User operational state | Succeeded/failed history may import; queued/running is retained as migration-history classification `abandoned_legacy`, not a runnable `durable_job` state, and is never auto-replayed |
| Memory JSONL | User private context | Preserve behind memory adapter; do not convert to part knowledge |

Repeated publication of the same stable Notion page after cutover is resolved by source identity. Unchanged snapshot is no-op/provenance; changed snapshot is an update finding, never a numbered file copy.

## 5. Idempotent import algorithm

For each planned record:

1. Normalize and validate the Windows path by case-folded final resolved path under an allowed root; reject UNC roots and any reparse/junction escape.
2. Read and hash from the frozen source snapshot.
3. Look up `legacy_migration_map(legacy_kind, normalized_path, source_hash)`.
4. If a completed mapping exists, verify the target and return it unchanged.
5. In one bounded transaction, create/resolve the artifact, item/revision/relation/edge, migration mapping, audit event, and outbox event.
6. Commit at most the configured batch size and checkpoint progress.
7. On retry, resume after the last committed mapping; never rely only on an external line number.

Migration may share identical immutable blobs by hash. Logical deduplication remains per space and follows the same exact-hash rules as normal publication.

Recommended batch size begins at 100 items and is tuned during rehearsal. Do not wrap the entire migration or any LLM/extraction work in one transaction.

## 6. Reconciliation contract

Strict verification passes only when:

```text
legacy users = imported users + explicitly rejected users
legacy Markdown = imported revision sources + quarantined Markdown
legacy graph edges = imported edges + duplicate edges + quarantined edges
legacy candidates = imported findings + quarantined candidates
legacy sessions = imported conversations + quarantined sessions
legacy assets = referenced/imported assets + missing records + quarantined/unreferenced assets
```

P2.1 assigns every inventory manifest record, including audit lines, memory,
jobs, project context, Notion references, session messages/metadata/summaries/
uploads/bindings, and temporary/recovery candidates, exactly one **proposed**
terminal classification: `imported`, `rebuilt`, `retained_external`,
`quarantined`, or `unavailable`. Missing and multiply classified proposals are
failures. `proposed_terminal_classification="imported"` does not mean import has
occurred. P2.2 freezes the reviewed final plan, and P2.6 records one observed
outcome per record and proves equality with that plan. Conversation
reconciliation compares child-record counts and hashes, not only conversation
counts.

Additional checks:

- Every imported content/blob SHA-256 equals its source manifest.
- Every current revision belongs to its item; every item belongs to one space.
- Every graph endpoint belongs to the edge's space.
- Personal migration creates no part/team/담당 item.
- Re-running the complete migration produces zero new item/revision/edge rows and an identical logical checksum.
- Full projection rebuild yields the same active/published item set as canonical retrieval.
- FTS returns every deterministic canary from its own space and none from another.
- Unknowns and errors are present in the report with a reason and source hash.

## 7. Feature flags and safe exposure

Use explicit deployment flags with safe defaults:

| Flag | Default | Meaning |
|---|---:|---|
| `KNOWLEDGE_CORE_ENABLED` | `0` | Core schema/repository can start but serves no production traffic |
| `KNOWLEDGE_CORE_READS` | `0` | Personal reads use the core repository |
| `KNOWLEDGE_CORE_PERSONAL_WRITES` | `0` | Personal mutations use core commands |
| `KNOWLEDGE_CORE_PART_READS` | `0` | Part management/retrieval visible to pilot users |
| `KNOWLEDGE_CORE_PART_WRITES` | `0` | Shared part mutation enabled; final gate only |
| `KNOWLEDGE_CORE_PROJECTIONS` | `0` | Materializer produces compatibility/export views |
| `KNOWLEDGE_CORE_HIGHER_SPACES` | `0` | Team/담당 targets; remains off in phase 1 |

Readiness reports effective flags. No UI control can override them. `KNOWLEDGE_CORE_PART_WRITES=1` is refused at startup unless a canonical rollout record binds the exact build, schema checksum, data-root/deployment identity, P1–P7 evidence hashes, and explicit external rollout approval.

## 8. Rehearsal sequence

1. Make runtime/test dependencies reproducible and pass existing tests.
2. Quiesce every legacy writer and create one application-consistent source snapshot. If host policy uses VSS, cover every mutable source volume and record the VSS snapshot ID; SQLite online backup alone does not freeze surrounding JSON/Markdown/Notion databases.
3. Run inventory on the backup copy, not on mutable production data.
4. Complete explicit organization mapping and resolve unowned/quarantine policy.
5. Apply to an empty rehearsal database and blob root.
6. Run strict reconciliation, full projection rebuild, and content/hash comparison.
7. In P2, run migration-tool and `MIG-*` tests only. In the final P8.1 rehearsal after P1–P7 gates, run the full regression, acceptance, concurrency, fault, load, backup, and restore suite.
8. Repeat the same migration into the same database and prove zero logical change.
9. Destroy and recreate the rehearsal target from the same plan to prove reproducibility.
10. Archive signed/checksummed inventory, plan, verification, and test evidence.

No production cutover is scheduled until rehearsal has zero silent loss and all mandatory gates pass.

## 9. Production cutover

1. Announce and enter maintenance/read-only mode.
2. Stop web writers, legacy job submitters, worker, and projection processes.
3. Verify no active canonical/legacy write and record process/service state.
4. Create and verify the final consistent backup and immutable-source manifest.
5. Re-run inventory and compare it with the approved plan; resolve drift.
6. Apply schema migrations through the single migrator.
7. Import users, organization/spaces, personal knowledge, conversations, valid relations/findings, and legacy audit history.
8. Run strict verification and full projection build.
9. Start worker/projector with part writes still disabled; verify readiness.
10. Start web services with core personal reads only; perform canary login/list/search/session tests.
11. Enable personal writes and perform create/edit/deactivate/restore/idempotency smoke tests.
12. Enable part reads for the pilot part; run canary leakage scan.
13. Observe metrics and backup completion for the defined soak period.
14. Enable part writes only after the P8 gate and explicit rollout approval.

Existing source directories become read-only archived inputs after successful cutover. New compatibility Markdown is produced only by the materializer.

## 10. Rollback rules

Before any new canonical production write:

- Stop new services.
- Restore the verified pre-cutover snapshot/data root.
- Start the previous binaries and validate personal login/knowledge/Notion/chat.

After canonical writes have been accepted:

- Immediately disable personal/part writes and preserve the incident DB/blob state.
- Do not simply point old binaries at legacy data; that would discard new revisions/audit.
- Diagnose and roll forward. A backward replay is permitted only if the canonical-delta exporter, verifier, and replay test were implemented and passed before personal production writes were enabled; otherwise the deployment is explicitly roll-forward-only after its first canonical write.
- A backward-incompatible schema migration is never rolled back by binary-only downgrade.

The absence of dual-write is intentional: it avoids two competing truths. It makes pre-write canary gates and verified backups mandatory.

## 11. Data retention after cutover

- Keep the final legacy snapshot read-only for at least the agreed operational retention period and until two successful restore drills after cutover.
- Store migration manifests, reconciliation reports, schema versions, and checksums with backups.
- Do not run blob GC during cutover. Begin only after reference reconciliation and backup retention cover the oldest eligible deletion.
- Preserve tombstones, essential audit, stable IDs, and redirects even after administrator purge.
