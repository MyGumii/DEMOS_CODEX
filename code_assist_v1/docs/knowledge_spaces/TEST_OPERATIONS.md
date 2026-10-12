# Verification, performance, failure, backup, and operations contract

> Successor contract state: registered. The active machine inventory is the
> frozen 124-scenario table in this file plus the exact 160 successor scenarios
> in `second_brain_evolution/ACCEPTANCE.md`, for 284 unique mandatory scenarios.
> Runtime pass evidence remains packet-gated; registration is not implementation.

## 1. Verified current baseline

Verified on 2026-08-11 from `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -m unittest discover `
  -s code_assist_v1\tests `
  -p "test_*.py" `
  -v

node code_assist_v1\tests\test_notion_editor_core.js
node code_assist_v1\tests\test_notion_ui_runtime.js
```

Results:

- 40 existing Python tests passed in 6.838 seconds.
- Both existing Node UI test scripts passed.
- The project venv has Flask but no pytest; system Python has pytest but no Flask.
- Both Python runtimes report SQLite 3.50.4, which fails the production gate. Production v1 requires the exact SQLite 3.53.4 source ID pinned in `SCHEMA_V1.md`; a different runtime requires an explicit contract/allowlist revision and full rerun.
- There is no complete runtime/dev dependency manifest or pytest configuration.
- Existing tests cover Notion/Outlook primarily and use one process/temporary paths. They do not prove cross-process knowledge safety.

P0 must preserve these passes while creating a single reproducible test environment.

## 2. Required test/operations layout

Planned deliverables:

```text
code_assist_v1/
  requirements-runtime.lock
  requirements-test.lock
  pytest.ini
  tests/knowledge_spaces/
    fixtures/
    unit/
    integration/
    acceptance/
    concurrency/
    fault/
    migration/
    load/
    operations/
  ops/
    preflight.ps1
    backup.ps1
    restore-drill.ps1
    deploy.ps1
    service/
```

Test names or markers include the scenario IDs below. Tests never write to live `.runtime`, `knowledge`, `user_data`, or Notion databases.

## 3. Deterministic fixtures

### `org_40`

- 1 담당, 2 teams, 4 parts.
- 40 ordinary/pilot accounts, 10 per part.
- 4 active part leaders; 2 users also have system-admin role.
- 40 personal spaces and 4 active part spaces.
- Optional team/담당 space rows exist behind disabled feature flags.
- A separate unassigned user proves personal-only behavior.

### `space_canaries`

Each space receives an unpredictable unique token such as:

```text
PERSONAL_U07_CANARY_<random>
PART_P03_CANARY_<random>
```

Every API, FTS, Agent retrieval, graph, pin, projection, candidate, and session response is scanned. Any foreign canary is an immediate scope-leak failure.

### `knowledge_small`

- 400 active items, 40 inactive items.
- 3 revisions per item.
- 1,000 edges, including manual inactive edges.
- Korean/English text, formulas, Unicode filenames, duplicate titles, exact hashes, normalized-only similarities, and conflicting spans.

### `knowledge_load`

- 10,000 active items, 1,000 inactive items.
- 30,000 revisions and 20,000 edges.
- 2–8 KB Markdown bodies with deterministic Zipf-distributed search terms.
- Optional 1 GB immutable blob set for backup/restore timing.

### Lease/time fixture

- 40 authenticated clients wait at a barrier and acquire the same item.
- A fake database/service clock covers expiry and retry deterministically.
- The fake clock hook cannot be registered in a production connection factory; production eligibility compares SQLite UTC milliseconds within the mutation statement.
- Purge boundaries from deactivation timestamp `D0`: `D0 + 89d 23:59:59`, `D0 + 90d`, and `D0 + 90d + 1s`.
- Normal, expired, force-released, stale-fence, stale-revision, and moved-member combinations.

### Legacy migration fixtures

- `legacy_empty`.
- `legacy_realistic`, structurally matching the current user/wiki/graph/session/job layout with sanitized content.
- `legacy_dirty`: orphan/duplicate edges, hidden pages, corrupt/truncated JSONL, missing assets, stale temp files, duplicate filenames, Unicode/long paths, queued/running jobs.
- `legacy_large`, matching load scale.
- Each fixture includes `expected_manifest.json` mapping every source path/hash to expected import or quarantine outcome.

## 4. Mandatory acceptance scenarios

The following 124 named scenarios are mandatory frozen baseline scenarios. They may be parameterized, but each ID needs independent pass/fail evidence. Report-schema-v2 combines this table with the registered 160-ID successor inventory in `second_brain_evolution/ACCEPTANCE.md` and fails unless the active total is 284 with missing or duplicate IDs equal to zero.

### Organization and space

| ID | Scenario |
|---|---|
| `ORG-001` | Create a valid 담당→team→part hierarchy with stable unit IDs; a new part reports `space_status=not_created` until P1.6 provisions its space |
| `ORG-002` | Reject invalid parent type, cycle, and duplicate active sibling name |
| `ORG-003` | Enforce at most one active part membership per user |
| `ORG-004` | Allow intentionally unassigned personal-only user |
| `ORG-005` | Move membership atomically, increment scope epoch, revoke old leases |
| `ORG-006` | Require one valid active member as part leader before part-write enablement |
| `SPACE-001` | Personal item is visible/editable only in the owner's personal scope |
| `SPACE-002` | All current part members can read/edit/deactivate/restore part items |
| `SPACE-003` | Other-part and former-part users cannot use current-part endpoints |
| `SPACE-004` | Server rejects forged arbitrary `space_id` despite client UI state |
| `SPACE-005` | Team/담당 spaces remain hidden/unwritable in phase 1 while owner-kind triggers accept only the valid future unit kinds |
| `SPACE-006` | Personal/part canaries never cross list/search/graph/projection/API results |

### Account lifecycle and API security

| ID | Scenario |
|---|---|
| `AUTH-001` | Account disable atomically increments scope epoch, rejects active sessions, revokes leases/jobs, preserves history, and cannot leave a write-enabled part leaderless |
| `AUTH-002` | Re-enable restores login only and never revives stale session, lease, job, or former-part capability |
| `AUTH-003` | Legacy destructive account/data deletion is blocked while canonical references exist |
| `AUTH-004` | Cookie mutations/SSE initiation reject missing or invalid CSRF/origin and production cookie attributes pass |

### Physical schema invariants

| ID | Scenario |
|---|---|
| `SCHEMA-001` | Organization hierarchy, owner kind, active-membership, and active-leader triggers/indexes reject every invalid combination |
| `SCHEMA-002` | Composite foreign keys reject cross-space conversation pins, edges, current revisions, and evidence revisions |
| `SCHEMA-003` | Revision/audit metadata and present revision bodies are immutable; only eligible purge can remove a body |
| `SCHEMA-004` | Active-edge and open-deactivation partial uniqueness plus persistent one-row lease constraints hold under concurrency |
| `SCHEMA-005` | Item visibility/current revision and transactional FTS document change in the same commit/rollback |
| `SCHEMA-006` | Illegal lifecycle/curation/finding/proposal/job/outbox transition is rejected; curation visibility changes only through matched atomic finding/rescan commands |
| `SCHEMA-007` | Referenced actors cannot be hard-deleted and audit display/role snapshots survive disable/rename |
| `SCHEMA-008` | Approved SQLite source ID, required pragmas, FTS5, JSON checks, and required indexes pass schema preflight |

### Conversation, pin, and retrieval scope

| ID | Scenario |
|---|---|
| `CONV-001` | Conversation stores one authorized space at creation |
| `CONV-002` | Every attempt to update conversation space is rejected |
| `CONV-003` | UI switch creates a new conversation and preserves old badge/history |
| `CONV-004` | Chat request cannot override conversation space in its body |
| `CONV-005` | Part move invalidates old-part conversation retrieval immediately |
| `CONV-006` | Pins persist across reload and must reference the same conversation space |
| `CONV-007` | Private user memory is not implicitly injected into a part conversation |
| `CONV-008` | Legacy KB and SB are not queried in parallel after repository cutover |
| `CONV-009` | Inactive/provisional/quarantined items never enter default Agent context |
| `CONV-010` | Default private project context is absent from part scope unless explicitly bound |
| `CONV-011` | Conversation list/rename/archive preserves immutable space and expected row version |
| `CONV-012` | Repeated/reconnected turn operation creates one user message and at most one final assistant message |
| `CONV-013` | Compact summary, pins, and explicit context bindings cannot move across spaces |

### Publication and provenance

| ID | Scenario |
|---|---|
| `PUB-001` | Personal-only publication creates one item/revision/outcome |
| `PUB-002` | Current-part-only publication creates one part item/revision |
| `PUB-003` | `both` creates separate personal/part item and revision IDs with sync none |
| `PUB-004` | Every transaction failpoint yields all target outcomes or none, never partial |
| `PUB-005` | Same operation retry 1,000 times creates no duplicate item/revision |
| `PUB-006` | Same operation ID with different request hash is rejected |
| `PUB-007` | Preview/source snapshot drift is rejected before commit |
| `PUB-008` | Unassigned/stale-membership user cannot publish to part |
| `PUB-009` | Same-space exact content adds provenance/no-op; different spaces remain independent |
| `PUB-010` | Unchanged Notion source is no-op; changed source creates update proposal, not `-2` item |
| `PUB-011` | Publication preserves original artifact/extraction/revision provenance and hashes |
| `PUB-012` | Synchronous/asynchronous ready preview persists one immutable versioned result/findings payload, survives web/worker restart, and can be polled/cancelled without creating knowledge |
| `PUB-013` | Expired, consumed, actor-changed, scope-epoch-changed, or snapshot-drifted preview cannot commit; terminal never-published bodies clean only after the 30-day zero-reference gate |

### Revision and lease

| ID | Scenario |
|---|---|
| `REV-001` | Existing revision metadata rows cannot be updated/reordered; purge changes only the separate retention/body records |
| `REV-002` | Successful edit creates exactly one revision, audit event, and outbox effect |
| `LEASE-001` | Normal acquire/heartbeat/save/release succeeds |
| `LEASE-002` | A foreign user receives `LEASE_HELD` while a lease is live |
| `LEASE-003` | Expired lease cannot heartbeat or save |
| `LEASE-004` | Reacquire/force-release/expiry keeps fencing generation strictly increasing |
| `LEASE-005` | Old token/fence after force release can never save |
| `LEASE-006` | Correct token/fence with stale expected revision still fails |
| `LEASE-007` | Ordinary member cannot force-release another editor |
| `LEASE-008` | Same-part leader/admin can force-release with mandatory audit reason |
| `LEASE-009` | Forty simultaneous acquirers produce exactly one valid holder |
| `LEASE-010` | Membership move during edit rejects later heartbeat/save |
| `LEASE-011` | Client-generated raw lease token never appears in DB, audit, application logs, or server response |
| `LEASE-012` | Rejected save preserves/reports recoverable client draft workflow without overwrite |

### Lifecycle, graph, and merge

| ID | Scenario |
|---|---|
| `LIFE-001` | General part member can deactivate with actor/time/reason/eligibility |
| `LIFE-002` | Deactivate conflicts with another live editor rather than silently releasing it |
| `LIFE-003` | Inactive item is absent from default list/search/Agent retrieval |
| `LIFE-004` | Deactivation changes zero edge rows and preserves edge checksum |
| `LIFE-005` | Restore exposes only edges whose own stored state remains active |
| `LIFE-006` | Re-deactivation creates a new episode and new 90-day eligibility |
| `LIFE-007` | `89d 23:59:59` purge fails; scheduler never auto-purges |
| `LIFE-008` | At/after 90 days, non-admin purge still fails |
| `LIFE-009` | Eligible administrator purge keeps tombstone/hash/provenance/audit, atomically removes revision/eligible unreferenced artifact-extraction bodies and FTS rows, then GC removes only now-unreferenced blobs |
| `LIFE-010` | Active→purged direct transition and purged restore are rejected |
| `MERGE-001` | Merge requires two fixed current revisions and two valid locks/fences |
| `MERGE-002` | Result revision, merged source, redirect, audit, and outbox are atomic |
| `MERGE-003` | Near-duplicate merge does not automatically rewire semantic edges |
| `MERGE-004` | Stale fork-base/personal-current/part-current or merge preview cannot apply; explicit rebase preserves target changes and source history |

### Deduplication and curation

| ID | Scenario |
|---|---|
| `CUR-001` | Byte/source-identity exact handling has zero false automatic merge |
| `CUR-002` | Normalized hash alone creates a candidate and never auto-deduplicates |
| `CUR-003` | New content pending risky analysis and strong near-duplicate/update/potential conflict stay provisional with intermediate Agent exposure zero |
| `CUR-004` | Weak complementary similarity uses published+badge+no-action policy and is never treated as strong hidden near-duplicate |
| `CUR-005` | Semantic edge auto-commit count is zero; approval requires evidence |
| `CUR-006` | LLM cannot automatically merge/replace/deactivate/purge |
| `CUR-007` | A compared revision change makes the finding obsolete/stale |
| `CUR-008` | Ignored-pair suppression ends when content or rule/model version changes |
| `CUR-009` | Potential conflict stores evidence spans and is not asserted as fact automatically |
| `CUR-010` | T0/T1 automation cannot exceed structural/deterministic provenance or non-LLM no-blocker release effects and records versioned evidence/audit/outbox |
| `CUR-011` | T2 requires an authenticated human reviewer; T3 model/finding execution count is zero and server derives reviewer/visibility policy |

### Change proposals

| ID | Scenario |
|---|---|
| `PROP-001` | Changed stable source proposal fixes source snapshot, base revision, target revision, proposed body, and finding version |
| `PROP-002` | Personal-to-part proposal computes fork-base/personal-current/part-current three-way preview without auto-sync |
| `PROP-003` | Stale proposal/finding/target revision cannot apply; explicit rebase creates a new proposal and obsoletes the old one |
| `PROP-004` | Valid apply with target lease/fence creates exactly one target revision and atomically updates finding/audit/outbox/FTS |
| `PROP-005` | Proposal apply response-loss retry returns the stable prior result with no duplicate revision |

### Outbox, projection, and durable job

| ID | Scenario |
|---|---|
| `PROJ-001` | Canonical mutation and outbox event always commit/rollback together |
| `PROJ-002` | Worker crash after file replacement but before ack replays idempotently |
| `PROJ-003` | Projection filtering never mutates canonical edge/item state |
| `PROJ-004` | Full rebuild checksum equals settled incremental result |
| `PROJ-005` | Agent falls back to canonical retrieval when projection is behind |
| `PROJ-006` | Mixed-generation index/graph/log is never presented as one snapshot |
| `JOB-001` | Process kill immediately after enqueue loses zero jobs |
| `JOB-002` | Running-worker kill is reclaimed after claim lease expiry |
| `JOB-003` | Crash after domain commit before ack produces no duplicate domain effect |
| `JOB-004` | Same job/effect key returns the stable prior result |
| `JOB-005` | Two web processes cannot independently run conflicting GPU jobs |
| `JOB-006` | Account disable or membership move during a job causes pre-commit authorization recheck to reject its domain effect |

### Migration, operations, regression, and performance

| ID | Scenario |
|---|---|
| `MIG-001` | Empty fixture migration succeeds |
| `MIG-002` | Realistic fixture imports/rebuilds every planned category |
| `MIG-003` | Dirty fixture produces complete quarantine manifest with silent loss zero |
| `MIG-004` | Complete rerun changes zero logical rows/revisions |
| `MIG-005` | Crash after a committed batch resumes from durable mapping/checkpoint |
| `MIG-006` | Every manifest record and session child record resolves exactly once; counts/hashes/edges/sessions/assets reconcile 100% |
| `MIG-007` | Existing data creates personal items only; unowned root is never auto-assigned |
| `MIG-008` | Derived index/backlinks are rebuilt rather than imported as truth |
| `OPS-001` | Online backup during writes passes integrity/manifest verification |
| `OPS-002` | Isolated restore drill meets RPO ≤1h and RTO ≤4h |
| `OPS-003` | Restore rebuilds projections with zero canonical mismatch |
| `OPS-004` | Any SQLite version/source-ID mismatch or missing compile/pragmas/FTS/local-volume requirement fails preflight/readiness |
| `OPS-005` | Production starts with WSGI services, not Flask development server |
| `OPS-006` | Backup hold prevents blob GC race; manifest/finalization failure leaves no falsely completed backup |
| `REG-001` | Existing 40 Python and 2 Node tests remain green |
| `REG-002` | Existing personal login, Notion page/edit/share/export flow remains green |
| `REG-003` | Existing personal Agent/session behavior remains compatible after migration |
| `PERF-001` | Forty-client steady load meets all latency/busy/lag gates |

## 5. Fault injection

Implement test-only injectable boundaries. Production startup fails if a failpoint configuration is present.

Required failpoints:

```text
publication.after_operation
publication.after_first_target_item
publication.after_first_target_revision
publication.after_first_target_result
publication.after_second_target_item
publication.after_second_target_revision
publication.after_audit
publication.after_outbox
publication.before_commit
publication.after_commit_before_response

account.after_status
account.after_scope_epoch
account.after_session_revoke
membership.after_old_close
membership.after_new_insert
membership.after_scope_epoch
membership.after_lease_revoke

lease.after_generation_increment
lease.after_holder_write
lease.force_release_after_generation

edit.after_revision_insert
edit.after_current_pointer_update
edit.after_audit
edit.after_outbox
lifecycle.after_item_state
lifecycle.after_episode
lifecycle.purge_after_tombstone
lifecycle.purge_after_fts_remove
lifecycle.purge_after_body_delete

merge.after_dual_acquire
merge.after_result_revision
merge.after_source_state
merge.after_redirect

curation.after_review_state
curation.after_visibility_state
curation.after_domain_effect
proposal.after_rebase_insert
proposal.after_apply_revision

projector.after_claim
projector.after_staging_write
projector.after_markdown_replace
projector.after_index_replace
projector.after_graph_replace
projector.before_outbox_ack

job.after_enqueue
job.after_claim
job.after_domain_commit_before_ack
job.after_ack

migration.after_batch_commit
migration.before_checkpoint
backup.after_database_snapshot
backup.after_blob_manifest
backup.after_hold_acquire
backup.before_finalize_rename
```

Methods:

- Transaction-internal failpoint raises an exception and asserts rollback.
- Post-commit boundary runs in a child process and uses hard termination to simulate crash.
- Real SQLite contention uses separate processes/connections, one holding `BEGIN IMMEDIATE`.
- Client disconnects immediately after commit and retries the same operation ID.
- Projection filesystem adapter injects disk-full, permission, and atomic-replace failure in a temporary root.
- Every failure test asserts canonical invariants and then recovery convergence.

## 6. Forty-client load profile

LLM/OCR/extraction latency is excluded from database/API gates and measured separately.

Steady profile:

- Start two real production-WSGI processes (`app_code` and `app_notion`) against the same database plus the durable worker/projector; an in-process Flask test client is not valid load evidence.
- 2-minute ramp-up, 40 distinct authenticated accounts.
- 20-minute steady interval, 1-minute cooldown.
- Think time uniformly 250–1,250 ms.
- Fixed load fixture and random seed are recorded.

| Mix | Operation |
|---:|---|
| 35% | FTS search |
| 20% | item/list/revision/graph read |
| 10% | conversation create/list without LLM |
| 8% | item/manual publication |
| 8% | acquire→edit→save |
| 5% | personal publication |
| 4% | dual publication |
| 4% | deactivate/restore pair |
| 4% | acquire/heartbeat/release without edit |
| 2% | projection/job status |

Additional profiles:

- `hot-item`: 10 clients per part contest one item for 5 minutes.
- `write-saturation`: 40 clients mutate different items with no think time for 10 minutes.
- `projection-burst`: 1,000 canonical mutations in 60 seconds.
- `job-crash`: 100 durable jobs with 20 randomized worker terminations.
- `leak-scan`: every account queries every foreign canary through API/FTS/Agent/graph/projection.

Pass criteria:

- Cross-space canary exposure: 0.
- Stale saves producing revisions: 0.
- Dual-publication partial outcomes: 0.
- Successful non-LLM CRUD end-to-end p95: ≤500 ms.
- DB/FTS search p95: ≤1.5 seconds.
- Outbox commit to applied projection p95: ≤5 seconds.
- Final unhandled `SQLITE_BUSY` failure rate: <0.1%; record raw busy/retry counts separately.
- Submitted durable job set minus terminal/recoverable set: 0.
- Domain result commits per job/effect key: at most 1.

The busy-rate denominator is every database-backed request in the fixed steady profile; its numerator is a response that still ends in `SQLITE_BUSY` or `SQLITE_LOCKED` after the bounded retry policy. Compute latency targets both per endpoint class and over the documented aggregate mix; every class with at least 100 samples must pass. Saturation/burst profiles use recorded throughput, error rate, queue peak, and eventual convergence targets rather than replacing the steady-profile SLO.

Persist raw JSON/CSV samples, p50/p95/p99, error codes, server build/schema/SQLite source ID, hardware, run seed, and test profile so percentiles can be independently recomputed.

## 7. Windows production services

Recommended service split:

- `CodeAssist-Migrator` — manual, one-shot deployment migrator that must stop
  successfully before the web services are started.
- `CodeAssist-Web` — production WSGI for `app_code`.
- `CodeAssist-Notion` — production WSGI for `app_notion`.
- `CodeAssist-DurableWorker` — ingestion/curation/maintenance jobs and central GPU/inference scheduling.
- `CodeAssist-Projector` — may share worker code, but uses a separate job type/claim policy; one active claimant is sufficient.

P7.5 must commit one exact Windows-supported production WSGI package/version and a checked deployment manifest containing thread/connection limits, ports, service wrapper, working directory, protected environment file, service account, NTFS ACL, graceful drain timeout, and migrator→web→worker/projector order. No floating package or implicit default is accepted. `run_standalone.ps1` and `app.run()` remain development-only.

Readiness resolves the final database/data paths, rejects UNC or disallowed reparse/junction targets, and verifies the live volume is local NTFS. One coordinator performs periodic `PRAGMA wal_checkpoint(PASSIVE)`; `TRUNCATE` is allowed only during an explicit drained maintenance operation.

Readiness exposes without secrets:

- app/build and schema migration versions;
- SQLite version/source ID, compile options, required pragmas, and executable FTS5 check;
- DB/FTS open check and foreign-key status;
- effective feature flags;
- oldest outbox/job age, dead counts, active/stale lease counts;
- projection desired/built generation and lag;
- latest successful backup age;
- DB/WAL/blob disk free space;
- failpoint absence.

## 8. Backup contract

- Schedule a consistent knowledge-core backup every 30 minutes so RPO ≤1 hour has margin.
- Alert and block deployment when the latest verified backup is older than 45 minutes.
- Retain, initially: 96 half-hour snapshots, 30 daily snapshots, and 12 monthly snapshots; tune after observing storage.
- Use `sqlite3.Connection.backup()` or equivalent online backup API. Never copy only the live `.db` while WAL is active.
- Generate the blob manifest from the completed snapshot DB, copy referenced immutable blobs, and verify SHA-256.
- Acquire a durable backup hold before manifest extraction so blob GC cannot delete a referenced object; release it only after final success/failure is durably recorded.
- Backups live off the production machine/volume with restricted NTFS ACL and appropriate encryption.
- Projections are not required backup data and rebuild after restore.
- Zero-reference blob GC uses at least a 24-hour quarantine and never races an active snapshot manifest.

Backup steps:

1. Create a timestamped `.partial` staging directory on the same volume as its final backup directory so final rename is atomic.
2. Produce the online SQLite snapshot.
3. Extract referenced-blob manifest from that snapshot.
4. Copy/verify blobs.
5. Run `PRAGMA quick_check` and `foreign_key_check` on the snapshot.
6. Record app/build, schema/checksum, SQLite source ID, audit high-water mark, blob counts/hashes, configuration version, and lease-HMAC key-set version without secret material.
7. Atomically rename staging to completed.
8. Record success in durable health state/Windows event log.

Notion source databases/files remain a separate application backup set in phase 1; knowledge backups include every immutable Notion snapshot already published into the knowledge core.

## 9. Restore drill

Run at least monthly and before a schema-changing release:

1. Select a completed backup and verify its manifest/hashes.
2. Restore into a different data root and isolated ports.
3. Run `integrity_check`, `foreign_key_check`, schema check, and canonical reconciliation.
4. Rebuild all projections from canonical state.
5. Start worker/projector and both web apps on isolated ports.
6. Execute canary authentication, personal/part read, edit, dual-publication retry, search, graph, and projection tests.
7. Calculate RPO from backup audit high-water mark to assumed incident time.
8. Calculate RTO from drill start to successful smoke completion.
9. Archive logs and result manifest.

Pass: RPO ≤1 hour, RTO ≤4 hours, zero checksum/FK/projection mismatch.

## 10. Deployment no-go gate

Production/pilot advancement is blocked if any of these is true:

- Any mandatory acceptance/regression test fails.
- Cross-space leak, stale save, lost job, duplicate result commit, or dual-publication partial occurs.
- Migration strict reconciliation is not 100% or a source was silently skipped.
- SQLite version/source ID differs from the approved pin, DB is not verified local NTFS, or required compile options/pragmas/FTS fail.
- Any CRUD/search/projection/SQLite-busy target fails. After a capacity correction, the same fixed profile must be rerun and pass before the no-go condition can clear.
- Latest verified backup age exceeds 45 minutes.
- Latest isolated restore drill exceeds RPO/RTO or has integrity mismatch.
- Dead outbox/unrecoverable job count is nonzero.
- Production still uses Flask development server or process-local job/GPU correctness.
- Free target-server disk is below three times current DB+referenced-blob size during migration/deployment.

Part writes require a separately recorded gate containing schema/build versions and evidence hashes.

## 11. Planned verification commands

After P0 supplies locked dependencies and test runners:

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit `
  --docs-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1\docs\knowledge_spaces `
  --output -

.\venv\Scripts\python.exe -m pip check

.\venv\Scripts\python.exe -m pytest code_assist_v1\tests `
  -m "not fault and not load and not restore" -q

.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\knowledge_spaces\acceptance `
  -m acceptance -q

.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\knowledge_spaces\concurrency `
  code_assist_v1\tests\knowledge_spaces\fault `
  -m "concurrency or fault" -q

.\venv\Scripts\python.exe `
  code_assist_v1\tests\knowledge_spaces\load\run_load.py `
  --base-url https://target.example:10011 `
  --notion-base-url https://target.example:10012 `
  --fixture D:\Perf001\fixture.json `
  --credentials D:\Perf001Secrets\credentials.json `
  --hardware D:\Perf001\hardware.json `
  --clients 40 --ramp-seconds 120 --duration-seconds 1200 `
  --cooldown-seconds 60 --seed 0x5077 --profile steady `
  --output D:\Perf001Evidence\steady.json `
  --confirm-target-load PERF-001_TARGET_LOAD

.\venv\Scripts\python.exe `
  code_assist_v1\tests\knowledge_spaces\load\verify_load.py `
  --fixture D:\Perf001\fixture.json `
  --report D:\Perf001Evidence\steady.json `
  --csv D:\Perf001Evidence\steady.csv

# After all five exact profiles, bind them with verify_gate.py as documented
# in tests\knowledge_spaces\load\README.md.

powershell -NoProfile -ExecutionPolicy Bypass `
  -File code_assist_v1\ops\restore-drill.ps1 `
  -DataRoot C:\CodeAssistData `
  -Database C:\CodeAssistData\database\knowledge-core.sqlite `
  -BlobRoot C:\CodeAssistData\blobs `
  -ProjectionRoot C:\CodeAssistData\projections `
  -BackupRoot E:\CodeAssistBackups `
  -BackupRunId 00000000-0000-4000-8000-000000000000 `
  -IsolatedDataRoot C:\CodeAssistRestoreDrill `
  -IsolatedDatabase C:\CodeAssistRestoreDrill\database\knowledge-core.sqlite `
  -IsolatedBlobRoot C:\CodeAssistRestoreDrill\blobs `
  -IsolatedProjectionRoot C:\CodeAssistRestoreDrill\projections `
  -MaxRpoMinutes 60 -MaxRtoMinutes 240
```

The contract-audit and baseline pytest commands are available after P0. The
isolated restore interface is available after P7.4; its target-server signed
execution remains P7.9. Acceptance/concurrency/load commands remain required
future interfaces until their owning backlog phases create them.
