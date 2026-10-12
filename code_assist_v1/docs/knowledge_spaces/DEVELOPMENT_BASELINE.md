# P0 development and test baseline

This document is the developer execution contract established by P0.1 and
P0.2. Run every command
from the workspace root, the directory that contains `code_assist_v1/` and
`venv/`. These tasks do not authorize production startup, a live-data
migration, or part-space writes.

## Supported baseline

The dependency locks were resolved for Windows x86-64 and CPython 3.14.3.

| Component | P0.1 baseline |
|---|---|
| Python | CPython 3.14.3 |
| Runtime dependencies | `code_assist_v1/requirements-runtime.lock` |
| Test dependencies | `code_assist_v1/requirements-test.lock` (includes the runtime lock) |
| pytest | 9.0.3 |
| pytest configuration | `code_assist_v1/pytest.ini` |
| Verified Node | 24.14.1; both current tests use Node built-ins and require no npm install |
| Verified development SQLite | 3.50.4, source ID recorded below; development-only warning |
| Required production SQLite | Exact 3.53.4 version and exact approved source ID |

The lock files contain exact resolved package versions, including transitives.
They do not yet contain distribution archive hashes because an approved Windows
CPython 3.14 wheelhouse has not been downloaded and hashed. Do not add
`--require-hashes` or invent hashes from installed files. A hash-verified
wheelhouse is a later controlled artifact step.

Optional `llama-cpp-python`/CUDA, `pywin32`, and PyMuPDF remain outside this
baseline. P7.5 now freezes the production WSGI choice as Waitress `3.0.2` in
the runtime lock; development and test runners do not substitute it for the
explicit production-service entrypoint.

## Create exactly one clean virtual environment

Do not combine system Python packages with an existing partially populated
virtual environment and call that a reproducible result. Start with an absent or
otherwise freshly recreated `venv` directory, then run:

```powershell
# Start PowerShell in the workspace root that contains code_assist_v1/.

py -3.14 -c "import platform; assert platform.python_version() == '3.14.3', platform.python_version()"
py -3.14 -m venv venv

.\venv\Scripts\python.exe -m pip --version
.\venv\Scripts\python.exe -m pip install `
  -r code_assist_v1\requirements-test.lock
.\venv\Scripts\python.exe -m pip check
```

Install the test lock once; it includes the runtime lock. Do not separately
install into system Python. Do not run a floating `pip install -U` as part of
baseline evidence. If installation needs the public index or a future
wheelhouse, use the approved network/artifact procedure for that environment.

## Record runtime versions

The version diagnostic reads only interpreter/package metadata, an in-memory
SQLite connection, and `node --version`. It does not read environment-variable
values, secrets, application configuration, filesystem paths, `.runtime`,
`knowledge`, `user_data`, or Notion databases. It writes nothing and emits one
deterministically ordered JSON object to stdout.

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.runtime_versions `
  --mode development
```

Capture that stdout unchanged in CI or the phase evidence store. The report has
no timestamp or machine path by design; the evidence system supplies build and
capture metadata separately. It records:

- CPython implementation and exact `major.minor.micro` version;
- the in-process `sqlite_version()` and `sqlite_source_id()` values;
- the approved production pair and whether both values match exactly;
- Node availability/version without its executable path;
- every installed Python distribution name/version, sorted canonically;
- the mode, stable reason codes, gate status, and intended process exit code.

The development report on the verified 2026-08-11 interpreter is expected to
contain the following runtime facts:

```text
Python:           CPython 3.14.3
SQLite version:   3.50.4
SQLite source ID: 2025-07-30 19:33:53 4d8adfb30e03f9cf27f800a2c1ba3c48fb4ca1b08b0f5ed59a4d5ecbf45e20a3
Node:             24.14.1
Gate:             warning, exit 0
```

After the locked install, the same report must also include Flask 3.1.3 and
pytest 9.0.3. `pip check` must succeed.

## SQLite development warning versus production failure

`SCHEMA_V1.md` defines a two-value allowlist, not a minimum-version check:

```text
sqlite_version()   == 3.53.4
sqlite_source_id() == 2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc
```

A newer version, an older version, or 3.53.4 with any other source ID is a
mismatch.

- `--mode development`: emits `gate.status="warning"` with mismatch reason
  codes and exits 0. This permits P0 development and the existing regression
  suite only. It is not an exception to the production contract.
- `--mode production`: emits `gate.status="fail"` with the same facts and exits
  2 on any mismatch. That failure must block production, migration, backup,
  restore, worker, projector, and load-test processes.
- Either mode emits `gate.status="pass"` and exits 0 only when both values match
  exactly.

Exercise the failure behavior explicitly:

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.runtime_versions `
  --mode production
$LASTEXITCODE  # expected: 2 on the current SQLite 3.50.4 runtime
```

Python's `sqlite3` library comes from the selected Python runtime and is not
upgraded by either pip lock. Production therefore needs an approved interpreter
build that supplies the exact pair.

This tool is intentionally narrower than P0.4. A production `pass` here proves
only the SQLite version/source-ID pair. It does not prove compile options,
executable FTS5, connection pragmas, WAL configuration, local NTFS/reparse
requirements, writable data roots, free disk, feature flags, failpoint absence,
production WSGI, or cross-process agreement. The later full preflight must prove
all of those before readiness succeeds.

## Run the regression baseline

First prove pytest collection and execute all current Python tests in the same
venv:

```powershell
.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests --collect-only -q

.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests -q
```

Expected P0.1 baseline: 40 Python tests pass. The suite is unittest-compatible,
but pytest is the single runner for this baseline and all later scenario markers.

Run both Node tests from the same workspace-root shell:

```powershell
node code_assist_v1\tests\test_notion_editor_core.js
node code_assist_v1\tests\test_notion_ui_runtime.js
```

Expected P0.1 baseline: both scripts exit 0 and report success. There is no npm
dependency installation step.

For convenience, the equivalent Python commands from inside `code_assist_v1/`
are `..\venv\Scripts\python.exe -m pytest tests --collect-only -q` and
`..\venv\Scripts\python.exe -m pytest tests -q`. Do not mix working-directory
forms within one evidence record.

### Current root compatibility recheck

On 2026-08-17, the isolated root-level compatibility selection was collected
from `code_assist_v1/` with the official venv and
`TEMP=TMP=TMPDIR=D:\CodexTestTemp`:

```powershell
..\venv\Scripts\python.exe -E -m pytest -q --collect-only `
  -m "not fault and not load and not restore" `
  tests --ignore=tests\knowledge_spaces --ignore=tests\browser
```

The selection contained 943 tests in 89 files. Because a single invocation
exceeded the local command time limit, the exact collected files were executed
in smaller isolated pytest processes. Final unique-node coverage was 941 passed
and 2 intentional skips. The only failing expectation was a stale test that
stopped the explicit Notion workspace chain at frozen schema 1315 before asking
the current 1317 store for deep health. The test now applies the reviewed 1316
and 1317 migrations and then proves that older migrators fail closed while 1317
reports `already_current`. The repaired file passes 8/8 and compiles. This is a
local compatibility result, not P2.8, P7.8, P7.9, or production-cutover
evidence.

## P0.2 isolated test execution contract

Pytest is the only supported runner for tests that import application modules
or may write data. Direct `python -m unittest` execution is not accepted as
phase evidence because it bypasses `tests/conftest.py` and its fail-closed
bootstrap.

At the beginning of every pytest process, before `config`, either Flask app, or
any store module may be imported, the bootstrap:

- validates inherited `TEMP`, `TMP`, `TMPDIR`, and Python's cached temporary
  directory against protected repository and live-data paths;
- creates a unique session root below that trusted temporary directory;
- binds `CODE_ASSIST_TEST_DATA_ROOT` and `CODE_ASSIST_DATA_ROOT` to the same
  absolute session root;
- redirects runtime configuration, auth, token, sessions, workspace,
  knowledge, blob, projection, and Notion paths into that root;
- empties credential/API overrides and disables external Codex, GGUF,
  conversion, Outlook, Notion, and background-worker activity;
- gives subprocesses the repository root through `PYTHONPATH`; and
- rejects a pre-imported path-caching application module.

The autouse guard repeats the effective-path, environment, and loaded-module
checks before and after every test. It rejects relative paths, filesystem
roots, repository/package roots, live mutable roots, parent/child overlap with
those roots, and symlink or Windows-junction escapes. A fault test receives no
exception to these rules.

Standard fixtures are:

| Fixture | Scope and purpose |
|---|---|
| `isolated_data_root` | Session root shared only by the current pytest process |
| `isolated_knowledge_layout` | Fresh per-test runtime/knowledge/user/session/workspace/core/blob/projection/Notion layout |
| `isolated_sqlite_db` | Fresh per-test SQLite path whose connection is explicitly checked and closed |
| `isolated_blob_dir` | Fresh per-test blob directory |
| `isolated_projection_dir` | Fresh per-test projection directory |
| `isolated_notion_dir` | Fresh per-test Notion directory |

Do not set either test-root variable manually, reuse a root across pytest
processes, reload path-caching modules to switch roots, or pass an external
`--basetemp`. Start a fresh pytest process for a fresh root. An explicit
`--basetemp` is accepted only when it is a strict descendant of the generated
session root.

On teardown, pytest restores every environment variable and
`tempfile.tempdir` to its entry value and removes the session root. The P0.2
acceptance run proved this both through normal subprocess execution and a
same-interpreter `pytest.main()` call. The verified suite after P0.2 contains
64 Python tests plus five unittest subtests; 40 are the original regression
baseline and 24 directly exercise path resolution and test isolation. Both
Node scripts remain green.

## P0.3 current-state characterization selection

P0.3 adds a deliberately small compatibility suite for behavior that the
canonical cutover must either preserve or explicitly replace. Run it with:

```powershell
.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests -m regression --collect-only -q

.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests -m regression -q
```

The P0.3 selection contains eight executed cases:

| Baseline | Characterized behavior |
|---|---|
| `REG-001` | Remains a suite-level gate over the original 40 Python and two Node tests; P0.3 does not redefine that count |
| `REG-002` | Registry-backed personal cookie login plus both existing Notion-to-Second-Brain publication adapters |
| `REG-003` | Authenticated personal session, legacy knowledge, Second Brain isolation, and chat retrieval identity/metadata |
| Auxiliary limitation | Persisted legacy job metadata is pollable after reopening, but no durable callable/claim/recovery acceptance is claimed |

The tests use real Flask cookie login. Notion publication performs a real
isolated Markdown import/write while only LLM conversion and semantic analysis
are replaced with deterministic stubs. Chat streaming is mocked at the model
boundary, and both current retrieval adapters must receive the authenticated
user ID rather than a request-body ID.

The job limitation case is not `JOB-001` or any other durable-worker
acceptance scenario. It records an implementation gap that P7 intentionally
replaces. Likewise, these regressions do not bless unauthenticated global
fallbacks, physical file deletion, cross-user duplicate IDs, broad index/log
injection, missing CSRF controls, or automatic semantic graph writes.

After P0.3, the complete Python suite contains 72 tests plus five unittest
subtests: 40 original regressions, 24 P0.2 isolation tests, and eight P0.3
executed characterization cases. Both Node scripts remain separate required
commands.

## P0.4 deterministic runtime preflight

P0.4 adds the canonical executable preflight:

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.runtime_preflight `
  --mode development `
  --data-root C:\CodeAssist\data `
  --database C:\CodeAssist\data\core\knowledge-core.db `
  --blob-root C:\CodeAssist\data\blobs `
  --projection-root C:\CodeAssist\data\projections `
  --runtime-lock G:\AI_Agent\DEMOS_CODEX\code_assist_v1\requirements-runtime.lock `
  --part-write-rollout-record C:\CodeAssist\deployment\part-write-rollout.json
```

Every supplied database/directory must already exist. The command never creates
a database, schema, or storage directory. After proving fixed local NTFS and a
safe non-overlapping final layout, it performs only reversible create/fsync/delete
directory probes, opens the existing database with `mode=rw`, applies and reads
back connection-local pragmas, creates/queries/drops a TEMP FTS5 table, and runs
integrity/path checks. Output is one sorted, ASCII-safe JSON line; document bodies,
environment values, raw exception text, and executable paths are excluded.

The checked PowerShell adapter is:

```powershell
.\code_assist_v1\ops\preflight.ps1 `
  -Mode development `
  -DataRoot C:\CodeAssist\data `
  -Database C:\CodeAssist\data\core\knowledge-core.db `
  -BlobRoot C:\CodeAssist\data\blobs `
  -ProjectionRoot C:\CodeAssist\data\projections `
  -RuntimeLock G:\AI_Agent\DEMOS_CODEX\code_assist_v1\requirements-runtime.lock `
  -PartWriteRolloutRecord C:\CodeAssist\deployment\part-write-rollout.json
```

It pins the repository working directory and uses Python `-E`, so an inherited
`PYTHONPATH` cannot redirect the module import. On the current interpreter,
development exits 0 with only `sqlite_version_mismatch` and
`sqlite_source_id_mismatch`; production exits 2 with the same reasons. Any
unsafe path/layout, integrity failure, invalid flag, failpoint configuration, or
attempt to enable part writes without the future complete rollout verifier exits
2 in both modes.

Run the focused selection with:

```powershell
.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\knowledge_spaces\unit\test_runtime_preflight.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_windows_volume.py `
  code_assist_v1\tests\knowledge_spaces\operations\test_runtime_preflight_cli.py `
  -q
```

The verified P0.4 selection has 90 tests. The complete suite after P0.4 has 162
tests plus five unittest subtests; both Node scripts remain required. P0.4 is a
skeleton only: required schema indexes/triggers, cross-process build agreement,
production WSGI/service configuration, queue/projection/backup health, and target
server wrapper stability remain P1/P7 gates.

## P0.5 deterministic read-only inventory

P0.5 adds a standalone pre-migration inventory gate. Run it only against an
approved frozen copy or VSS snapshot when producing migration evidence:

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

`--knowledge-root` may be repeated for the standalone and integrated legacy
locations. `--allow-partial` permits a machine-readable result for corrupt or
unclassified records, but the report remains `complete=false`. It never relaxes
invalid roots, unsafe aliases/reparse entries, code-callsite coverage, source
drift, path collisions, or SQLite snapshot consistency. Output replacement
requires `--replace-output`.

The scanner hashes source bytes and hash-only logical children without importing
application config or stores. A live Notion DB is first copied with WAL/SHM/
journal into a scanner-owned temporary directory; only that copy is queried.
The source DB is never opened. The G-02 callsite ledger is sealed, so an added or
changed route, store call, UI integration, or job submitter fails until it is
explicitly mapped.

Run the focused selection with:

```powershell
.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\knowledge_spaces\unit\test_legacy_inventory.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_legacy_inventory_code.py `
  code_assist_v1\tests\knowledge_spaces\migration\test_legacy_inventory_cli.py `
  -q
```

The verified P0.5 selection has 52 tests. The complete suite after P0.5 has 214
tests plus five unittest subtests; both Node scripts remain required. P0.5 does
not authorize migration: owner-map approval, plan/apply, idempotency, and full
reconciliation remain P2 gates. Detailed proof is in `evidence/P0.5.md`.

## P2.1 complete inventory scanner

P2.1 upgrades the report to schema v2 and freezes it as an
`inventory_proposal`. Every record has one of three record kinds, one proposed
terminal classification, and either scanner or planner authority. The scanner
also proves record/relationship/category closure, validates canonical owner and
cross-source identities, and records unavailable inputs rather than silently
dropping them.

The four snapshot metadata options and `scratch-root` shown above are
mandatory. `scratch-root` must be a pre-existing absolute local fixed NTFS
directory outside every source root and without a reparse component. Notion
SQLite, WAL, SHM, and journal bytes are copied there before the copy is opened
read-only. The source is never opened through SQLite.

On Windows, scratch/output directories remain handle-pinned while children are
created relative to those handles. Writable scratch-copy and output temporary
files remain delete-pending until their bytes are flushed, closing path-rename
and hard-link escape windows; the scanner then performs the final rename
through the still-open handle. The focused suite permanently exercises these
races and late output-winner preservation.

`snapshot-attestation-sha256` is supplied by the operator's snapshot process.
The scanner format/time-checks it, binds it to the declared final root facts,
and checks scan-time drift; it does not authenticate the attestation, create the
snapshot, prove writer quiescence, or independently prove application-level
consistency.

Focused verification:

```powershell
$env:TEMP = 'D:\CodexTestTemp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_legacy_inventory.py `
  tests\knowledge_spaces\unit\test_legacy_inventory_code.py `
  tests\knowledge_spaces\migration\test_legacy_inventory_cli.py
```

The final focused selection contains 107 tests. Detailed proof and the explicit
no-live-scan/no-migration boundary are in `evidence/P2.1.md`. P2.2 owns the
reviewed plan; P2.6 owns observed reconciliation and rerun checksums.

## P2.2 deterministic migration-plan draft and review

P2.2 consumes only a bound schema-v2 inventory document and exact organization
mapping CSV. It performs no source scan or canonical database operation. The
planner creates a deterministic technical draft; `confirm-plan` separately
binds that exact draft to external review facts:

```powershell
$env:TEMP = 'D:\CodexTestTemp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_legacy_migration_plan_contract.py `
  tests\knowledge_spaces\unit\test_legacy_migration_planner.py `
  tests\knowledge_spaces\migration\test_legacy_migration_plan_cli.py
```

The focused selection contains 63 tests. It includes full P2.1 manifest
reconstruction, canonical JSON-plus-LF document binding, all 41 route decisions,
exact CSV/org closure, actual UTC chronology and run-identity validation, total
artifact validation, no-external-I/O planning, source-root
containment without a live source probe, and real eight-process exact/different
output-winner plus concurrent bound-input tests. `apply` and `verify` remain
absent; detailed proof is in `evidence/P2.2.md`.

## P0.6 machine-verifiable contracts

P0.6 adds a dependency-free, read-only contract gate over OpenAPI 3.1, JSON
Schema Draft 2020-12, `GOAL_CONTRACT.json`, `TRACEABILITY.md`,
`TEST_OPERATIONS.md`, `IMPLEMENTATION_BACKLOG.md`, and the human API contract:

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit `
  --docs-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1\docs\knowledge_spaces `
  --output -
```

The explicit docs root must be absolute. File output must also be absolute,
outside the source docs root, and absent unless `--replace-output` is supplied.
The writer uses an atomic same-directory commit; non-replace mode cannot race into
overwriting an existing report. Output and errors are one ASCII-safe canonical
JSON line.

The audit expands requirement/scenario ranges and fails on malformed or
duplicate-key JSON, escaping/missing references, schema syntax or example drift,
goal threshold drift, missing/duplicate/untraced IDs, human/OpenAPI operation
drift, stable-error drift, missing CSRF/idempotency contracts, and source changes
during the scan. It imports no application config or stores.

Run the focused selection with:

```powershell
.\venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\knowledge_spaces\unit\test_contract_audit.py -q
```

The verified P0.6 selection has 20 tests. The complete suite after P0.6 has 234
tests plus five unittest subtests and passes from both the workspace root and the
package directory. Both Node scripts and `pip check` also pass. P0.6 describes
target interfaces; it does not expose routes or enable writes. Detailed proof is
in `evidence/P0.6.md`.

## Evidence required to claim P0.1

Record all of the following together:

1. clean-venv creation command and CPython 3.14.3 assertion;
2. locked install command and successful `pip check` output;
3. unmodified development-mode runtime JSON;
4. production-mode runtime JSON plus its expected exit code;
5. pytest collection count and the 40-test pass result;
6. both Node command outputs and exit codes.

P0.1 is not complete if Flask and pytest come from different interpreters, a
test mutates live user data, the runtime JSON is edited by hand, or the current
SQLite warning is presented as production eligibility.

## P1.1 composition scaffold verification

P1.1 introduces the inert `knowledge_core` boundary and shared host
registration, without opening a database or enabling canonical traffic. Run its
focused proof from the package directory:

```powershell
..\venv\Scripts\python.exe -m pytest -q `
  tests\knowledge_spaces\unit\test_knowledge_core_composition.py
```

The verified selection has 37 tests. It covers exact package boundaries,
fresh-process import and registration isolation, all seven feature-flag states,
same-container identity, zero route registration, executable factory wiring for
`app_code`, `app_notion`, and the optional Blueprint, and fail-closed startup
before legacy registration. The complete suite after P1.1 has 271 tests plus
five unittest subtests and passes from both supported working directories.

P1.1 does not satisfy a database/schema scenario and does not authorize a live
DB, migration, worker, projection, traffic flag, or part write. Detailed proof
is in `evidence/P1.1.md`.

## P1.2 connection and migrator verification

P1.2 adds the SQLite infrastructure boundary but deliberately does not contain
the physical v1 schema. Run its focused proof from the package directory:

```powershell
..\venv\Scripts\python.exe -m pytest -q `
  tests\knowledge_spaces\unit\test_knowledge_core_db.py
```

The verified selection has 46 tests. It covers exact runtime/source gating,
per-connection pragmas and FTS, explicit transactions and rollback on every
`BaseException`, prepared-statement authorization isolation, SQLite-initiated
rollback reconciliation, an opaque connection/cursor boundary, path/file
identity and hard-link rejection, no-checkpoint-on-close, immutable migration
catalog/ledger history, reviewed physical-schema fingerprints, failpoint
rollback, idempotent rerun, and ordinary-open no-migration behavior.

The current workstation SQLite 3.50.4 is not production-eligible. Functional
development tests may use `ConnectionFactory._for_isolated_tests` only while
the P0.2 `CODE_ASSIST_TEST_MODE=1` guard is active and the target path resolves
inside its per-process isolated root. Production requires SQLite 3.53.4 with
the exact allowlisted source ID; no override is accepted there.

The complete suite after P1.2 has 318 tests plus five unittest subtests and
passes from both supported working directories. P1.2 creates and migrates only
synthetic fixture schemas under P0.2 temporary roots. It does not authorize a
live DB, the P1.3 schema, canonical routes, workers, projections, traffic flags,
or personal/part writes. Detailed proof is in `evidence/P1.2.md`.

## P1.3 canonical schema-v1 verification

P1.3 supplies the first executable canonical domain schema. Run its focused
proof from the workspace root:

```powershell
.\venv\Scripts\python.exe -m pytest -q `
  code_assist_v1\tests\knowledge_spaces\unit\test_knowledge_core_db.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_migration_storage_preflight.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_tables.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_triggers.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_catalog.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_fingerprint_tool.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_acceptance.py
```

The verified selection has 110 tests. It compiles all 46 ordinary STRICT rowid
tables, 120 explicit indexes, 131 non-FTS triggers, the FTS5 table and three
external-content triggers, the fixed root seed, and all 16 migration invariant
queries. It also executes `SCHEMA-001..008`, including a two-writer active-edge
race and curation/search/FTS commit-versus-rollback proof.

Reproduce the frozen physical fingerprint with an explicit approved CLI:

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.schema_v1_fingerprint `
  --sqlite-exe C:\approved-tools\sqlite3.exe
```

The CLI must report SQLite `3.53.4`, source ID ending in `59bcc`, FTS5 and JSON
available, object counts `120/51/134` for index/table/trigger, and matching
expected/observed SHA-256
`694fa29e2e184c8ab546e7fb603cf014bc1eb58ded639504548f9fa3f920761e`.
The downloaded official tools archive is review input only and must not be
committed or used to qualify a different Python SQLite binding.

The complete suite after P1.3 has 382 tests plus five unittest subtests. P1.3
creates and migrates only P0.2-controlled temporary databases. It does not
authorize a live DB, repository traffic, workers, projections, feature-flag
changes, or personal/part writes. Detailed proof is in `evidence/P1.3.md`.

## P1.4 user, audit, compatibility, and browser-security verification

P1.4 adds canonical user, audit, operation-dedup, outbox, account-lifecycle,
and compatibility primitives plus forward-only account-policy migration v2.
Run its focused proof from the package directory:

```powershell
..\venv\Scripts\python.exe -m pytest -q `
  tests\knowledge_spaces\unit\test_schema_v1_catalog.py `
  tests\knowledge_spaces\unit\test_migration_v2_account_policy.py `
  tests\knowledge_spaces\unit\test_user_audit_repositories.py `
  tests\knowledge_spaces\unit\test_account_service_and_compatibility.py `
  tests\knowledge_spaces\unit\test_auth_security.py `
  tests\knowledge_spaces\integration\test_auth_flask_security.py `
  tests\knowledge_spaces\unit\test_knowledge_core_composition.py
```

The verified selection has 139 tests. It covers idempotent legacy imports,
credential-policy preservation, redacted principals, revision-CAS account
transitions, leader/write protection, membership closure, scope-epoch
invalidation, lease fencing, old-epoch job invalidation, stable
`failed_final` replay, audit/outbox atomicity, hard-delete rejection, cookie
session compatibility, Host/Origin/CSRF/SSE enforcement, corrupt-registry
failure, production cookie gates, and concurrent secret-key bootstrap.

Run the browser helper and existing Notion JavaScript gates with:

```powershell
node tests\test_auth_csrf_frontend.js
node tests\test_notion_editor_core.js
node tests\test_notion_ui_runtime.js
```

The schema-v2 cumulative fingerprint must be
`d8c6a2d7e17893740280264f1c246c2bdabe97b0a14fd2fa0be4ccf726a777bf`
under the exact approved SQLite 3.53.4 runtime/source ID. P1.4 does not migrate
the live DB or cut canonical account traffic over. The legacy status and hard
delete endpoints fail closed; P1.5 supplies the versioned administrator API.
Detailed proof is in `evidence/P1.4.md`.

## P1.5 organization service and administrator API verification

P1.5 adds the canonical organization repository/service and an explicit,
dependency-injected Flask administrator adapter. Run its primary proof from the
package directory:

```powershell
..\venv\Scripts\python.exe -m pytest -q `
  tests\knowledge_spaces\unit\test_organization_repository.py `
  tests\knowledge_spaces\unit\test_organization_service.py `
  tests\knowledge_spaces\integration\test_admin_organization_api.py `
  tests\knowledge_spaces\integration\test_organization_multiprocess.py `
  tests\knowledge_spaces\integration\test_auth_flask_security.py `
  tests\knowledge_spaces\unit\test_contract_audit.py
```

The selection covers stable 담당→team→part identity, exact parent and sibling
rules, one active membership, leader-episode and part-row CAS, scope-epoch
invalidation, old-part lease/job revocation, impact-bound preview tokens,
exact member/leader/optional-space inactivation, populated-part moves that
retain IDs and scope, audit/outbox/dedup rollback at every transaction envelope,
and disabled future team-space freezing. Two fresh Python processes prove one
actor-scoped operation converges to one resource and competing membership
commands produce exactly one winner and one stable stale-version result.

The administrator adapter exposes exactly nine contracted routes only when
explicitly registered. It rejects body/header idempotency disagreement,
oversized or structurally unsafe JSON, forged actor facts, noncanonical IDs,
stale versions, and every semantic Flask/Blueprint shadow surface including
implicit HEAD/OPTIONS, final endpoint names, repeated named attachment, and
Blueprint-local callbacks. Authentication, authorization, CSRF, and unexpected
errors use the correlated no-store error envelope.

P1.5 creates and mutates only P0.2-controlled temporary databases. It does not
provision spaces for new parts, register these routes in `app_code` or
`app_notion`, migrate the live DB, enable canonical traffic, start workers or
projections, or enable personal/part writes. Detailed proof is in
`evidence/P1.5.md`; P1.6 owns personal/part space provisioning.

## P1.6 personal/part space lifecycle verification

P1.6 adds one canonical space repository and an unregistered administrator
reconciliation service. Run its primary proof from the package directory:

```powershell
..\venv\Scripts\python.exe -m pytest -q `
  tests\knowledge_spaces\unit\test_space_repository.py `
  tests\knowledge_spaces\unit\test_space_provisioning_service.py `
  tests\knowledge_spaces\integration\test_space_provisioning_multiprocess.py `
  tests\knowledge_spaces\unit\test_organization_repository.py `
  tests\knowledge_spaces\unit\test_organization_service.py `
  tests\knowledge_spaces\unit\test_account_service_and_compatibility.py `
  tests\knowledge_spaces\unit\test_knowledge_core_composition.py
```

The selection covers all canonical users, active versus inactive parts,
leaderless initial spaces, stable pre-existing IDs and lifecycle state,
`not_created -> write_disabled` tree state, actor epoch and idempotency,
audit/outbox cardinality, transaction failpoints, repository consolidation,
and default composition remaining inert. Two fresh Python processes exercise
40 users and four active parts for both exact operation replay and distinct
operation contention.

If the system temp drive is too small, point `TEMP`, `TMP`, and `TMPDIR` at one
existing absolute test-only directory on a non-workspace local drive before
starting pytest. P0.2 then creates and removes its own isolated child root and
continues to reject every protected workspace or live-data path.

P1.6 does not register a route or service in either live host, migrate a live
database, resolve client scope, expose team/responsibility spaces, lazy-create
from a read, or enable personal/part writes. Detailed proof is in
`evidence/P1.6.md`; P1.7 owns scope and capability resolution.

## P1.7 scope and capability resolution verification

P1.7 adds a framework-neutral, read-only `ScopeResolver`. Run its direct unit
and actual two-process proof from the package directory:

```powershell
..\venv\Scripts\python.exe -m pytest -q `
  tests\knowledge_spaces\unit\test_scope_resolver.py `
  tests\knowledge_spaces\integration\test_scope_resolver_multiprocess.py
```

The resolver accepts only a session user ID and captured scope epoch, requires
an active caller-owned canonical transaction, and rereads account,
membership, hierarchy, leader, and space state from that snapshot. The tests
cover unassigned personal-only users, ordinary part members, part leaders,
same-part administrators, stale epochs, disabled accounts, membership mirrors,
missing/frozen/inactive/foreign/former/higher spaces, forged IDs, all-off and
selective rollout snapshots, write-disabled rows, exact capability order, and
the absence of lazy provisioning or other canonical writes.

The integration test uses two fresh Python subprocesses and explicit barriers:
one process commits a membership move while the other proves the old epoch is
stale and the new epoch sees only personal plus the new part. It also proves
foreign/former/higher/missing spaces remain concealed, write-disabled spaces
remain non-writable, and knowledge-space/audit/outbox counts are unchanged by
resolution.

P1.7 does not register a route or service in either live host, read rollout
flags from the environment, create or migrate a live database, lazy-provision,
expose higher-level spaces, or enable personal/part reads or writes. Detailed
proof is in `evidence/P1.7.md`; P1.8 owns the administrator UI extension.

## P1.8 administrator organization UI verification

P1.8 adds a contract-strict browser module for the explicitly injected P1.5
administrator adapter. Run its Node controller test and backend/API/contract
selection from the package and repository roots respectively:

```powershell
node --check static\admin_organization.js
node tests\test_admin_organization_ui.js
node tests\test_auth_csrf_frontend.js

.\venv\Scripts\python.exe -m pytest -q `
  code_assist_v1\tests\knowledge_spaces\unit\test_organization_service.py `
  code_assist_v1\tests\knowledge_spaces\integration\test_admin_organization_api.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_contract_audit.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_legacy_inventory_code.py
```

The Node test executes the actual controller with a fake DOM/transport and
proves strict snapshot/preview/result validation, same-ID/body retry after
response loss, 409 refresh, last-request-wins loading, unavailable-state
blocking, explicit move/inactivation resolution, disabled-account membership
blocking, XSS escaping, and the absence of legacy status/role/delete fallback.

P1.8 does not register the administrator adapter in a live host, mutate or
migrate a live database, expose team/responsibility publishing, or enable any
canonical read/write rollout. Detailed proof is in `evidence/P1.8.md`; P1.9
owns the minimal durable work kernel.

## P1.9 minimal durable-work kernel verification

P1.9 fills the previously inert `repositories/jobs.py` and `worker.py`
boundaries without starting a worker loop or cutting over a producer. Run its
repository, orchestration, and real-process restart proof from the package
directory:

```powershell
$env:TEMP = 'D:\CodexTestTemp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_durable_job_repository.py `
  tests\knowledge_spaces\unit\test_durable_work_kernel.py `
  tests\knowledge_spaces\integration\test_durable_job_multiprocess.py
```

The final focused selection has 32 tests. It covers strict canonical JSON and
hash integrity, operation-dedup binding, exact enqueue replay/conflict,
handler-version and actor-kind registry coverage, claim/start/renew/progress,
attempt ABA fencing, expiry/retry/dead behavior, immediate/cooperative/expired
cancellation, current authorization and expected-state revalidation, atomic
canonical effect plus result commit, and stable response-loss replay. Fresh
Python processes prove enqueue durability after hard exit, expired-running
reclaim, stale-attempt rejection, one claim winner for the same job row,
effect/result atomicity after process death, account-disable rejection, and
terminal cancellation recovery. A unit acceptance also proves membership move
and scope-epoch advancement reject the old-part effect.

The related DB/schema/account/organization/space/scope/composition selection
passes 274 tests. The complete Python collection is recorded as four
non-overlapping P0.2-isolated runs because a single buffered run exceeded the
tool-output timeout: unit 518 plus five subtests, integration 34,
migration/operations/regression 36, and existing root compatibility 335 with
two skips. All six Node tests and `pip check` pass. Exact commands, durations,
hashes, and the independent adversarial review are in `evidence/P1.9.md`.

P1.9 is not a production worker. It supplies no daemon loop, heartbeat
scheduler, operational backoff, actual publication handler, job HTTP API,
legacy producer cutover, cross-job GPU serialization/fairness, projection
materializer, monitoring, or external-effect exactly-once guarantee. P4 and P7
own those gates. No route, live host, feature flag, live migration, personal/
part traffic, or part write is enabled.

## P2.3 legacy-mapping schema durability prerequisites

P2.3 adds forward-only physical schema migration v3 before importer commits may
rely on stable migration locators. Schema v1 and v2 definitions remain frozen.
Version 3 adds a unique `normalized_path` index and insert-only update/delete
guards for `legacy_migration_map`; the stable abort code is
`LEGACY_MIGRATION_MAP_IMMUTABLE`. Because no P2.3 importer existed before v3,
the upgrade requires an empty v2 map; any preexisting row (including duplicate
locators) is rejected without advancing its ledger or `user_version`.

Run the focused permanent proof from the workspace root:

```powershell
$env:TEMP = 'D:\CodexTestTemp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
.\venv\Scripts\python.exe -E -m pytest -q `
  code_assist_v1\tests\knowledge_spaces\unit\test_migration_v3_legacy_mapping_durability.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_catalog.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_migration_v2_account_policy.py
```

The focused selection contains 15 tests. The reviewed cumulative physical-schema
SHA-256 is
`9c7563cfff4ec6e17cef635b140df663c6924f5a285d6ce072e4a4b9e556057a`, generated
with the official project venv and `require_approved_runtime=False`; the frozen
v1/v2 prefix hashes reproduce unchanged. These schema tests are prerequisites,
not `MIG-*` acceptance evidence. The bounded P2.3 importer is now implemented;
live migration, the strict verifier, canonical traffic, and personal/part write
rollout remain disabled.

The later P2.3 asset-relationship slice advances the forward catalog to
physical schema v4 without rewriting v1-v3. Version 4 creates the STRICT,
append-only `legacy_migration_relationship_map` table for exact reviewed
`asset_reference` effects. Its unique relationship and embedded-manifest
identities, complete RESTRICT FK surface, constrained hash/record/type/status/
timestamp shape, and update/delete guards fail closed with
`LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE`. Unlike v3, a v3 database may
already contain mapping rows; the upgrade preserves them byte-for-byte. A
preexisting relationship table or named v4 object fails the upgrade atomically.

Run the complete forward-schema proof from the workspace root:

```powershell
$env:TEMP = 'D:\CodexTestTemp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
.\venv\Scripts\python.exe -E -m pytest -q `
  code_assist_v1\tests\knowledge_spaces\unit\test_migration_v4_legacy_relationship_map.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_migration_v3_legacy_mapping_durability.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_schema_v1_catalog.py `
  code_assist_v1\tests\knowledge_spaces\unit\test_migration_v2_account_policy.py
```

The selection contains 44 tests. It covers fresh v4 bootstrap/reopen, nonempty
v3-to-v4 preservation, exact schema objects, rerun idempotency, immutable
history, relationship and embedded-manifest uniqueness, every declared FK and
same-row check, and postcondition rollback. The cumulative physical-schema
SHA-256 is
`589e1cb328e98bb3d13c86dc117c10e0eee0fb13d2542d5923c2348e86f10fdd`;
the v4 migration checksum is
`1557507d1d86390bac2fa4267217850bd2459df58aa1b702938b3ea4fad9f242`,
and the four-version catalog checksum is
`01fa7e659195da9444101099c74ac5bf43df57931bb810369254184fe6d3c266`.
The v1-v3 cumulative prefix hashes reproduce unchanged. These schema tests do
not claim a live apply, observed reconciliation, or any `MIG-*` scenario.

## P2.3 bounded personal migration implementation

P2.3 now consumes the v3/v4 durability prerequisites through a production
`migrate apply` boundary. It validates one exact reviewed plan, root-bound
source bytes and high-water state, an approved current-schema storage layout,
and deterministic batches no larger than 100 effects or 256 MiB. Identity,
personal content, assets, valid asset references, immutable mappings, audit,
and outbox effects commit in service-owned transactions and exact replay is a
no-op. The implementation never enables part writes or migrates schema.

Run the isolated P2.3 proof with the official venv and
`TEMP=TMP=TMPDIR=D:\CodexTestTemp`. The five split selections documented in
`evidence/P2.3.md` contain 273 P2.3 tests; the updated P2.2 planner selection
contains 65 more. No production source or database is used. At that P2.3
freeze, P2.4 was next and P2.5-P2.8 retained conversation/state import, final
reconciliation, fault, and rehearsal authority; their later evidence follows.

## P2.4 graph, finding, and labelled-history implementation

P2.4 advances the forward physical catalog to schema version 5 and extends the
same explicit `migrate apply` boundary. The v5 schema adds immutable targets for
candidate findings and historical audit events plus a separate append-only
P2.4 relationship checkpoint. The importer accepts only reviewed valid
same-personal-space nodes, graph edges, candidate findings, exact-duplicate
provenance, wiki log/tombstone artifacts, and hash-only logical audit/tombstone
events. Unsupported P2.4 effects fail before execution. Raw global audit stays
in the frozen source snapshot; its canonical representation is labelled
historical and cannot act as current authority.

All source parsing/staging remains outside SQLite. Each bounded service-owned
transaction revalidates the reviewed account, role/status/part assertion and
personal space, then commits the graph/finding/history effect, immutable
checkpoint, audit, and outbox together. Source containers close only after the
exact reviewed child/relationship set exists. Exact replay inserts no mapping
or canonical effect, while failpoints after effect or checkpoint roll the whole
batch back.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the P2.4
focused contract/planner/apply/repository/v5-schema selection passed 135 tests
in 99.15 s. The complete Knowledge Space unit partition passed 911 tests plus
five subtests in 563.17 s; integration passed 34 tests in 32.11 s; and
migration/operations/regression passed 57 tests in 67.90 s. `py_compile` passed
for every touched production module. Exact commands, boundaries, hashes, and
the explicit non-claims are recorded in `evidence/P2.4.md`. No production
source/database was touched. At that recorded gate P2.5 was next and P2.6-P2.8
retained final verification, fault, and rehearsal authority.

## P2.5 conversation and operational-state migration implementation

P2.5 advances the forward physical catalog to schema version 6 and extends the
same explicit `migrate apply` boundary. Exact reviewed session bytes are parsed
with duplicate-key rejection and reconciled against all six child kinds: core
metadata, messages, metadata, compact summaries, uploads, and bindings.
Messages and summaries become canonical rows; every other child is retained in
the conversation's migration metadata and every logical child gets an exact
record checkpoint before the session parent dependency closes.

Terminal and nonterminal legacy job JSON is retained as content-addressed,
labelled historical evidence. Queued/running jobs are explicitly abandoned and
never submitted to `durable_job`. The Notion adapter copies the exact reviewed
main and sidecar bytes to migration-owned scratch outside SQLite, verifies the
full P2.1 schema/table/row ledger, then commits only resolved live page bindings
and immutable record/relationship checkpoints. The source Notion database stays
external and retained. Exact replay creates no new effect; failpoints roll back
binding/checkpoint/audit/outbox together.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the focused
P2.5 contract/planner/apply/repository/v6-schema selection passed 145 tests in
105.87 s. The complete Knowledge Space unit partition passed 979 tests plus five
subtests in 664.17 s; integration passed 34 tests in 33.16 s; and
migration/operations/regression passed 57 tests in 68.56 s. `py_compile` passed
for every touched production module. Exact commands, boundaries, hashes, and
explicit non-claims are recorded in `evidence/P2.5.md`. No production source or
database was touched. At that P2.5 freeze, P2.6-P2.8 retained observed
verification/rebuild, fault, and rehearsal authority.

## P2.6 strict observed verification and full rebuild

P2.6 adds `migrate verify` over one exact reviewed plan and an already-applied
canonical database. The verifier requires exactly one target-verified immutable
checkpoint for every reviewed import record and relationship, none for every
non-import outcome, and exact equality between planned and observed terminal
classifications. It reopens imported CAS objects no-follow, replays the
P2.3-P2.5 canonical-effect contracts, and checks schema, quick/FK, owner/space,
graph, asset, conversation, Notion-link, and job-history closure.

The same operation rebuilds transactional `search_document`/FTS and a complete
immutable file generation for every canonical space. Only active/published
current revisions appear. Sources use item IDs as names; index, labelled audit
log, effective graph, and backlinks are generated from the same read-consistent
snapshot. Windows directory handles pin every output parent, generation files
are checksum-verified and remain write/delete-pinned through manifest and
`projection_state` publication, only `manifest.json` is atomically replaced,
and `projection_state` advances only after canonical state is rechecked. Exact
replay is byte-identical and does not increment row versions.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the focused
projection/verifier selection passed 20 tests. The complete Knowledge Space
unit partition passed 999 tests plus five subtests in 768.29 s; integration
passed 34 tests in 40.85 s; and migration/operations/regression passed 57 tests
in 80.97 s. Exact commands, hashes, failure boundaries, and non-claims are in
`evidence/P2.6.md`. No production source/database was touched. P2.7 fault/large
execution is recorded below, while P2.8 retains frozen-rehearsal authority; no
`MIG-*` completion is claimed by P2.6 evidence alone.

## P2.7 dirty/large migration fault gate

P2.7 runs the complete named `MIG-001..008` matrix against isolated copied
fixtures through the real inventory, reviewed planner, bounded apply, strict
verifier, and rebuild classification. Empty and realistic sources, explicit
unowned quarantine, duplicate/orphan relationships, a missing asset,
quarantine near-misses, and an exact rerun all close with zero unresolved
records or relationships. A 206-item Unicode fixture includes a Windows source
path longer than 260 characters and commits in three batches, each containing
at most 100 content records.

The public fault boundaries are now uniform across the P2.3-P2.5 importers.
`migration.before_checkpoint` fires inside the service-owned transaction just
before each new immutable record or relationship checkpoint; an exception
rolls canonical effects and the checkpoint back together.
`migration.after_batch_commit` fires only after a batch transaction returns.
A real child process exits with code 73 at that boundary, leaving exactly the
committed identity prefix; an ordinary restart resumes from immutable mappings
and strict verification closes the plan without duplicates.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the focused
P2.7 gate passed 6 tests in 60.65 s. The affected importer selection passed 93
tests in 115.37 s, and the inventory contract selection passed 108 tests in
65.59 s. The complete Knowledge Space unit partition passed 999 tests plus five
subtests in 738.73 s; integration passed 34 tests in 34.20 s; and migration,
operations, and regression passed 63 tests in 127.93 s. `py_compile` passed for
every touched module. Exact commands, hashes, scenario bindings, and non-claims
are recorded in `evidence/P2.7.md`. No live source, production database,
service, or feature flag was touched. The later P2.8 evidence now closes final
G-02/G-03; production cutover is still not claimed.

## P2.8 frozen-copy rehearsal (gate verified)

The production `migrate rehearse` boundary now accepts one exact reviewed plan
and two distinct, already schema-migrated empty canonical targets. It executes
primary apply/strict verification, an exact same-target rerun with zero new
mapping checkpoints, and a fresh-target apply/strict verification. It emits a
 body-free canonical report binding the exact reviewed-plan document, reviewed
 inventory/plan/attestation hashes, both target database/storage identities,
 all three apply summaries, one complete strict verification artifact, and the
 reproducibility hashes. Rehearsal report schema v2 also records the exact
 bounded `1..100` batch size. An exact existing-report retry reruns strict
 verification on both bound targets and accepts only the archived result; it
 never applies a second time, and a retry that changes the batch size is
 rejected as a different execution. Both targets must be empty canonical
 bootstraps, and the report output is rejected before apply if it overlaps a
 source or target; the final atomic writer rebinds the live protected roots.

 The rehearsal selection now passes 6 tests in 31.44 s, including companion
 plan/target tamper rejection, strict-verification retry binding, fresh-only
 current canonical-v7 bootstrap, existing-target refusal, and a late competing
 database winner that remains byte-identical. The directly coupled rehearsal,
 fault-gate, and strict-verifier selection passes 26 tests in 132.55 s. The
 target artifact contract
 requires the exact current catalog checksum and version independently for
 both targets, rejecting even a matching pair of forged catalog claims. The
 current adjacent integration/migration/operations/regression selection passes
  125 tests in 266.98 s; `py_compile` passes for the touched production and test
  modules.
The refreshed read-only code inventory closes 692 callsites as 387 mapped plus
305 retained/excluded, with zero errors, zero unclassified sites, all eight
submitters mapped, and callsite-ID seal
`064087bcfff8d991b675d3dc27571636ffcd0de3b0644cff55bc93393b7df740`.
Those results established tooling readiness. The subsequent approved v3
frozen-copy execution is recorded in `evidence/P2.8.md`: 7,072 records and 36
relationships reconcile exactly on two distinct canonical-v7 targets, the
primary rerun inserts zero mappings, the fresh target produces the identical
strict verification SHA, and the archived report contract closes. This closes
P2.8, the full P2 gate, and final G-02/G-03. Neither rehearsal target is
production data; production migration and cutover remain open.

## P3.1 canonical conversation repository (task verified; P3 remains open)

The first inert P3.1 slice extends the existing schema-v6 conversation rows;
it adds no migration and registers no route. `ConversationService` owns short
transactions for create/rename/archive, one accepted user turn, one terminal
completed/failed/cancelled outcome, and compact-summary replacement. Failed
turns store only a stable code, retryable flag, and schema version; cancelled
turns store no error. Operation dedup, the
canonical effect, immutable audit, and outbox commit together. Reconnect reads
ordered turn events, and every committed replay rechecks the active account
and current immutable conversation-space access before returning its stored
result. A frozen/inactive part therefore blocks old response replay without
moving or deleting the conversation.

`LegacyJsonSessionAdapter` is a read-only compatibility projection over the
canonical database. It never imports or opens the legacy sessions directory,
never writes JSON, and never performs a parallel fallback. An old P2.5 session
reference resolves only through the SHA-256 evidence stored on its imported
canonical conversation. The reviewed offline P2.5 importer remains the only
legacy migration authority.

Run the focused implementation and the related P2.5/audit regression from the
package directory with the official venv and isolated temporary root:

```powershell
$env:TEMP = 'D:\CodexTestTemp'
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP
..\venv\Scripts\python.exe -E -m py_compile `
  knowledge_core\repositories\conversations.py `
  knowledge_core\services\conversations.py `
  knowledge_core\legacy\session_compatibility.py `
  tests\knowledge_spaces\unit\test_conversation_repository.py
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_conversation_repository.py
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_conversation_repository.py `
  tests\knowledge_spaces\unit\test_legacy_conversation_repository.py `
  tests\knowledge_spaces\unit\test_legacy_p25_mapping_repository.py `
  tests\knowledge_spaces\unit\test_legacy_p25_importer.py `
  tests\knowledge_spaces\unit\test_migration_v6_legacy_p25_effect_map.py `
  tests\knowledge_spaces\unit\test_user_audit_repositories.py `
  tests\knowledge_spaces\unit\test_schema_v1_catalog.py
```

The focused selection passes 13 tests in 18.44 s; the related regression
selection passes 91 tests in 92.18 s, and `py_compile` passes. P3.1 evidence is
frozen in `evidence/P3.1.md`; the broader P3/G-07 gate remains
open until P3.2-P3.6 complete. The legacy Flask
session and chat routes, all Knowledge Core traffic flags, and part writes are
unchanged.

## P3.2 server-persisted pins and explicit context (task verified; P3 remains open)

P3.2 uses the existing schema-v6 `conversation_pin` and
`conversation_context_binding` rows; it adds no migration and registers no
route. A pin is accepted only when the conversation and item share the exact
space, the item is active and published, and the requested revision is its
fixed current revision. Add/remove, natural duplicate, detach/re-attach, and
operation replay are exact and server-owned.

Explicit workspace, project, Notion, and canonical-source context is resolved
by an injected trusted boundary outside the database transaction. The browser
command contains no artifact identifier or content hash. The write transaction
revalidates the active account, immutable conversation space and captured scope
facts and, when a snapshot is used, the canonical artifact/hash evidence. A
personal resolver result cannot attach to a part conversation, and a new part
conversation starts with zero implicit private pins or context.

Every mutation owns one short transaction containing the canonical change,
operation dedup record, audit event, and outbox event. Injected audit failure
rolls the entire mutation back. Exact committed replay rechecks current actor
and conversation access before returning the archived result. Detach preserves
immutable binding history; a later explicit re-attach receives a new binding
identity.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed. The focused P3.2 suite passed 9 tests. The final split regression passed
46 conversation tests in 68.89 s, 32 P2.5 mapping/importer tests in 57.11 s,
and 33 schema/trigger tests in 62.57 s: 111/111 in total. Exact behavior,
commands, hashes, and non-claims are frozen in `evidence/P3.2.md`.

This closes only the inert P3.2 storage/service task. The legacy Flask chat and
session routes remain authoritative; no HTTP/SSE route, retrieval pipeline,
worker, traffic flag, or part-write flag changed. P3.3 must build one-space
canonical retrieval, while P3.4-P3.7 still own per-turn revalidation, Agent/SSE
composition, diagnostics/canaries, compatibility regression, and cutover.

## P3.3 one-space canonical FTS retrieval (task verified; P3 remains open)

P3.3 adds an inert read-only repository/service boundary and no schema change.
Conversation retrieval derives the immutable `space_id` from the owned active
conversation; management retrieval accepts one requested space but resolves it
through the canonical `ScopeResolver`. Both paths revalidate the authenticated
scope epoch and read capability in the same `DEFERRED` snapshot as retrieval.
Administrative role never widens an ordinary Agent query to all spaces.

Caller text is NFKC-normalized, bounded, and compiled as literal FTS terms so
FTS operators, column selectors, prefixes, and parentheses have no authority.
The SQL joins `knowledge_fts` back to `search_document` and then rechecks exact
space, current revision, active lifecycle, published curation state, body
presence, title, and inline-body equality. It returns only bounded excerpts
labelled `untrusted_reference`; full projection files, index Markdown, raw audit
details, and another space are never opened or returned.

Only effective same-space graph rows touching a ranked hit can contribute a
bounded semantic neighbor. Recent events are selected solely for the returned
hit/neighbor item set and expose stable event metadata, not raw details. The
result also reports Markdown projection freshness. A missing, building, stale,
or errored file projection sets `canonical_fallback_used=true`; retrieval still
uses the transactional DB FTS index and never a stale file or vector cache.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed. The focused P3.3 suite passed 6 tests in 6.23 s. Final related splits
passed 28 scope/retrieval tests in 35.16 s, 46 conversation tests in 72.58 s,
33 schema/trigger tests in 71.99 s, and 14 P2.6 rebuild/canary tests in 64.37 s:
121/121 in total. Exact commands, hashes, behavior, and non-claims are frozen
in `evidence/P3.3.md`.

This closes only the P3.3 repository/service task. No HTTP route, Agent prompt,
SSE stream, compatibility adapter, worker, or rollout flag changed. P3.4 must
compose this result with per-turn pin/live-context validation and replace the
parallel legacy KB/SB retrieval; P3.5-P3.7 still own compatibility reads, UI
scope behavior, API/Agent canaries, and the full leak/session gate.

## P3.4 canonical turn-context composition (task verified; P3 remains open)

P3.4 adds an inert `AgentTurnContextService` and no schema, route, worker, or
feature-flag change. Its public request contains actor identity/scope, an
existing conversation ID, an accepted turn ID, and bounded result limits. It
contains neither `space_id` nor a search-text override: the service reads the
immutable accepted user message and conversation space from canonical rows.
This binds retrieval to the same turn that operation dedup and reconnect use.

The service opens a read snapshot to validate the active actor, immutable
conversation scope, accepted/running turn, canonical accepted event/message,
and at most 20 attached explicit bindings. External binding materialization and
live authorization then run with no SQLite transaction open. A final read or
write transaction repeats every authority check and compares the complete turn
and binding tuple before it performs exactly one canonical FTS retrieval and
loads current pins. A detached/replaced binding, part move, stale epoch, or turn
state change therefore discards any externally resolved bytes.

Live bindings may be available or carry one stable unavailable reason. A state
change uses row-version CAS and commits its audit/outbox in the same transaction
as the canonical binding update; an injected audit failure rolls everything
back. Snapshot bindings must remain available and reproduce their stored
SHA-256. Every explicit context is limited to 32 KiB, their combined content to
128 KiB, and all pin/context Markdown is structured and labelled
`untrusted_reference`. The service imports no legacy knowledge, Second Brain,
memory, project, workspace, or Notion store and never adds a default private
context.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed. The focused P3.3/P3.4 retrieval file passed 15 tests in 16.83 s. The
combined conversation, pin/context, retrieval, scope, composition, and schema
regression passed 140 tests in 118.83 s. Exact behavior, commands, hashes, and
non-claims are frozen in `evidence/P3.4.md`.

This closes the disabled canonical P3.4 implementation task, not live traffic
cutover or the P3/G-07 gate. The legacy Flask/SSE route remains authoritative;
P3.5-P3.7 still own read compatibility adapters, UI scope behavior, API/Agent
canaries, and final legacy-route disablement.

## P3.5 read-only legacy response compatibility (task verified; P3 remains open)

P3.5 adds an inert `LegacyKnowledgeReadAdapter` and no schema, route, worker,
or feature-flag change. Every public method resolves the authenticated actor's
current personal space with `ScopeResolver` and reads canonical state in one
read-only `DEFERRED` transaction. A supplied legacy `uid` is validated only as
bounded compatibility text and is never normalized, compared, or used to
select SQL rows.

The adapter preserves the existing knowledge list/view/search, Second Brain
thought/graph/inbox, and generated system-document keys. It reads current
active personal items and immutable revision bodies, management FTS rows,
effective canonical edges, `curation_finding`, and bounded audit metadata.
Canonical UUIDs remain opaque legacy filenames. Provisional and quarantined
items remain visible to management reads while Agent retrieval stays on the
separate published-only P3.3 boundary. Requested result limits truncate under
independent hard item/edge/finding/content/query bounds.

No legacy knowledge or Second Brain store, pending-diff file, projection file,
read-time refresh, or write SQL is imported or called. Search terms are
literalized and all item/edge/finding queries carry the resolved personal
`space_id`. Current revision, body hash/size, optional search-document, metadata
shape, endpoint state, scope epoch, and feature capability are rechecked;
foreign canaries and inconsistent evidence fail closed. Generated index/log
documents are bounded database views and do not create or refresh files.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed. The focused P3.5 selection passed 7 tests in 7.52 s, the complete
retrieval module passed 23 tests in 24.87 s, and the combined conversation,
pin/context, retrieval, scope, composition, and schema regression passed 148
tests in 126.81 s. Exact commands, hashes, behavior, and non-claims are frozen
in `evidence/P3.5.md`.

This closes only the disabled P3.5 adapter task. The existing Flask routes
remain authoritative and no live traffic changed. P3.6 still owns visible
management scope selection and immutable new-conversation UX; P3.7 owns route
wiring, API/Agent cross-space canaries, session regression, and final legacy
parallel-read disablement. P2.8 remains separately open for operator-supplied
rehearsal inputs.

## P3.6 scope selector and new-conversation UX (task verified; P3 remains open)

P3.6 adds a read-only `ScopeSelectionService`, a stable composition slot, and
an inert browser controller/component. The server model resolves the
authenticated actor and scope epoch through `ScopeResolver` in one read-only
`DEFERRED` snapshot. It returns exactly one personal option and at most the
current part option. A requested management selection must be in that set.
Conversation lookup is owner-bound, and its immutable space badge is returned
separately from the selected management space; an inaccessible former-part
conversation retains its badge with an explicit fail-closed access status.

The browser controller accepts only injected canonical adapters. It contains
no `fetch`, legacy session URL, actor selector, or conversation-update path.
Management selection validates a fresh snapshot and reloads list, search,
graph, index/log, activity, candidates, and pins. A different chat selection
does not modify or clear the old conversation. It exposes the exact
`새 지식공간으로 새 대화 시작` action, generates one stable operation ID,
coalesces concurrent clicks, requires a distinct returned conversation bound
to the selected space, then requires a matching immutable badge before it
switches presentation state. The top-bar component remains `hidden` until P3.7
binds trusted canonical routes, so legacy behavior is unchanged in this task.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, Python and
JavaScript syntax checks passed. The focused service/browser selection passed
7 tests in 5.96 s, the composition suite passed 37 tests in 10.58 s, and the
combined conversation, pin/context, retrieval, scope, composition, legacy
conversation, browser-controller, and multiprocess scope regression passed 136
tests in 108.86 s. Exact commands, hashes, behavior, and non-claims are frozen
in `evidence/P3.6.md`.

This closes only the disabled P3.6 implementation task. No HTTP/SSE route,
feature flag, worker, database schema, or live traffic changed. P3.7 still owns
canonical route activation, full `SPACE-006`/`CONV-001..013` API and Agent
canaries, reconnect/session compatibility, and final disablement of legacy
parallel retrieval. P2.8 remains separately open for operator-supplied
rehearsal inputs.

## P3.7 canonical browser/API/Agent isolation gate (task verified)

P3.7 adds a dependency-injected canonical Flask adapter, browser transport, and
one-space management read boundary. The adapter registers nothing on import.
With explicit read flags, the shared composition root requires canonical
scope, conversation, retrieval, management-read, and Agent-context services,
plus a server-principal resolver, turn executor, and summarizer. Missing or
incomplete bindings fail before any route is added. A successful composition
registers exactly `canonical_knowledge_v1`; the code host then does not import
or register the legacy knowledge, Second Brain, memory, project-context,
chat-stream, session, or agent-harness paths. Default production flags remain
off and the flag-off host behavior is unchanged.

The original P3.7 17-route canary (extended to 21 routes by P5.4) covers scope options, conversation list/create/get/update,
SSE turn and reconnect, compact, pins, explicit context, item list/search,
effective graph, projection state, activity, and finding inbox. Actor and scope epoch
come only from the injected server principal. Strict UUID/idempotency/body and
opaque cursor validation, immutable conversation space, terminal SSE replay,
same-space pin/context checks, part-move invalidation, and read-only management
queries fail closed. The Agent bundle performs one canonical retrieval and
contains no legacy KB/SB, private memory, or implicit project context.

The static browser bridge activates only after the canonical scope probe
succeeds. It binds the P3.6 selector to canonical chat, session, SSE, and seven
management surfaces without accepting a browser actor ID. Lazy conversation
creation and turn retry preserve their stable operation IDs; concurrent calls
coalesce; SSE sequence, event/body binding, terminal state, and post-terminal
data are validated. List/load/rename/archive/compact use canonical conversation
routes, and active canonical mode returns before any legacy knowledge/session/
chat transport. Default flag-off behavior still uses the unchanged legacy UI.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, Python and
JavaScript syntax checks passed; the real localhost browser/API/Agent canaries
passed 2/2 in 6.28 s; and the conversation/pin/retrieval/scope/browser/
composition/legacy-session/multiprocess/auth regression passed 166/166 in
155.16 s. Contract audit passed with zero errors, 31 requirements, 14 goals,
124 scenarios, 55 OpenAPI operations, and 10/10 schema examples. Exact hashes
and non-claims are frozen in `evidence/P3.7.md`.

This completes P3.7 and the tested P3/G-07 implementation gate behind disabled
traffic. It does not enable a live feature flag, start a worker, modify a live
database, or cut production traffic over. P2.8 remains separately open for
operator-supplied rehearsal inputs; P4.1 is the next safe implementation slice.

## P4.1 artifact/blob/extraction boundary (task verified)

P4.1 generalizes the existing migration-only artifact repository without
changing schema or a route. `SourceArtifactBundle` stores one upload, Notion,
chat, or manual source as a present raw CAS manifest and exactly one immutable
ready or failed extraction snapshot. Ready carries exact extracted Markdown,
its byte size/hash, the `markdown_lf_v1` evidence hash, extractor version, safe
warning codes, and an inline/blob XOR body. Failed carries extractor/version and
safe warning codes but no body or derived hash. Neither shape creates a
knowledge item or editable revision.

`prepare_publication_artifact()` is pure: it accepts already completed extractor
output, hashes the raw and extracted bytes, normalizes line endings for evidence,
and applies only the frozen empty/control/front-matter/code-fence structural
lint. It imports no DB, filesystem, config, network, or model boundary. The
bundle builder requires the physically staged raw observation to match the raw
bytes and emits exact body-free metadata. The repository then independently
validates owner/metadata key sets, warning-code vocabulary, publishability,
inline body bytes, normalized hash, and the complete preparation hash before
SQL. Exact replay succeeds; partial state or any drift fails closed.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed; the focused P4.1 suite passed 27 tests in 17.21 s; the P4.1 plus legacy
artifact selection passed 37 tests; and the related artifact/item/content-
importer/migration-map/schema regression passed 245 tests in 209.64 s. Exact
hashes and non-claims are frozen in `evidence/P4.1.md`.

This closes only P4.1 behind disabled traffic. P4.2 still owns the durable
preview and job, actor/scope authorization, operation idempotency, target-space
exact matching, and restart/cancel/expiry behavior. No publication route,
feature flag, item/revision, part write, worker, or `PUB-*` scenario is enabled
or claimed.

## P4.2 durable publication preview (task verified)

P4.2 composes the P4.1 artifact boundary with canonical authorization,
operation idempotency, immutable preview state, and the exact-version P1.9
kernel. Manual Markdown, an inclusive canonical conversation-message range,
and an authorized Notion page snapshot create synchronous ready previews.
Uploads preserve a verified raw artifact and enqueue one durable extraction
job. No preview creates a knowledge item or editable revision.

The service resolves actor, scope epoch, current part, and personal/part/both
target availability only from canonical state. It revalidates chat evidence in
the final write transaction and binds Notion owner/page/revision/content/asset
snapshot hashes without putting raw bodies in audit or job metadata. Polling
revalidates the current active actor and epoch but remains available when the
publication-write rollout flag is later disabled. It reconstructs exact
queued, extracting, failed, and pre-ready cancelled response shapes and returns
the immutable ready-result evidence after service/worker reconstruction.

The worker commits extraction and ready/failed preview state atomically. A
cooperative cancel, concurrent cancel during prepare, authorization drift,
permanent failure, retry exhaustion, or expired-claim recovery closes the job
and preview together and creates no knowledge. Ready-result schema/version,
source hash, rule version, findings, target modes, expiry, and completion time
are independently checked by the repository and canonical schema triggers.
SQLite time drives expiry. After the fixed 30-day grace, a terminal
never-published source with zero item, lineage, snapshot binding, proposal, or
backup reference may lose only its raw/extracted body links; artifact,
extraction, blob metadata, and hashes remain, and physical blob GC remains a
separate later operation.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed; the focused service suite passed 26/26; the artifact/preview/job/kernel/
schema selection passed 126/126; the artifact/item/importer/migration-map/schema
compatibility selection passed 212/212; and the existing real multiprocess
durable-job plus legacy Notion/job-restart regression passed 9/9. Exact hashes,
scope, and non-claims are frozen in `evidence/P4.2.md`.

This closes only P4.2 behind disabled traffic. It registers no route, changes
no feature flag, starts no worker, consumes no preview, and creates no item or
revision. P4.3-P4.6 still own single/dual publication, stable Notion update
proposals, API/UI activation, complete failpoint/1,000-retry proof, and all
`PUB-001..013` acceptance claims.

## P4.3 idempotent single/dual publication (task verified)

P4.3 adds the service-owned publication commit boundary over a ready P4.2
preview. The command requires the actor's current scope epoch and the exact
preview source-snapshot SHA. A new operation revalidates the active actor,
preview expiry/state, frozen current-part identity, and current personal/part
publish capabilities before it writes. An exact terminal operation replay
returns the first canonical response without re-consuming the preview or
repeating any domain effect; semantic input drift fails closed.

The same immediate transaction writes the immutable publication operation and
per-target rows, target effects, preview `consumed` transition, one target audit
event, operation/visibility outbox evidence, space generation for a created
item, and the committed operation result. New targets create active/provisional
items with an immutable initial revision/body and initial source link. A
different-origin exact body adds one evidence source to the existing current
item; an already represented stable-origin revision is no-op. A changed stable
origin is rejected for P4.4 rather than duplicated or overwritten.

`both` resolves personal then part as independent item/revision identities and
binds them to one immutable lineage group with `sync_policy='none'`; the part
member derives from the resolved personal revision. Mixed personal provenance
plus part creation is supported, but any later audit/outbox or target failure
rolls the whole command back, including preview consumption and idempotency
reservation. Publication writes no semantic relation and calls no LLM/model
path.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, `py_compile`
passed. The focused P4.3 suite passed 10/10, and the combined P4.1-P4.3,
artifact, preview repository/service, and schema table/trigger/postcondition
selection passed 116/116 in 119.07 s. Exact commands, hashes, and non-claims are
frozen in `evidence/P4.3.md`.

This closes only P4.3 behind disabled traffic. P4.4 owns the actual
stable-source update finding/proposal and existing Notion route consolidation;
P4.5 owns API/UI activation; P4.6 owns exhaustive SQL failpoints, process races,
1,000 retries, and the complete `PUB-001..013` acceptance claim. No route,
feature flag, live worker, production database, or traffic state changed.

## P4.4 stable Notion provenance and fixed update proposals (task verified)

P4.4 extends the disabled publication boundary with exact Notion provenance
and a canonical changed-source disposition. A ready Notion source artifact now
binds owner, page, page revision, source-snapshot SHA, asset-manifest SHA,
publisher, and part captured at preview time without persisting raw page content
in metadata, audit, or outbox payloads. Repository validation independently
binds this envelope to the stable Notion origin key and revision.

An unchanged stable source remains `no_op`. When the same Notion page has a new
source snapshot, the publication transaction preserves the current target item
and revision and inserts one deterministic T2 human-reviewed curation finding
and one pending `stable_source_update` proposal. The proposal fixes the source
artifact/extraction, source and proposed Markdown hashes/body reference, current
base item/revision, target space, finding row version, and preview hash. The
same transaction stores the `update_proposed` target and replay result, consumes
the preview, and appends target audit, finding-queue outbox, and operation
outbox evidence. It does not increment target generation because no visible
item/revision changed. A late outbox failure rolls all proposal/publication
effects back.

Personal and part targets create independent proposals; a repeated `both`
publication neither reassigns existing lineage nor synchronizes the two
copies. The two pre-existing legacy Notion route wrappers have permanent proof
that they delegate to the same immutable snapshot/write boundary, eliminating
duplicate wrapper-side write logic. This is not the canonical API/UI cutover:
P4.5 now owns and closes the new endpoint and target modal; the legacy delegate
remains a compatibility fallback rather than the canonical publication service.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the focused
P4.4 publication/artifact/legacy-adapter selection passed 52/52 in 43.92 s; the
P4.1-P4.4 artifact, preview repository/service, and publication core selection
passed 82/82 in 81.08 s; and the schema table/trigger/acceptance selection
passed 38/38 in 42.76 s. Contract audit tests passed 21/21 in 37.75 s and the
direct audit returned `status=pass`, `error_count=0`. Exact commands,
implementation hashes, and non-claims are frozen in `evidence/P4.4.md`.

This closes only P4.4 behind disabled canonical traffic. No canonical route,
UI, feature flag, live worker, production database, or traffic state changed.
No `PUB-*` or `PROP-*` scenario is claimed complete at this task boundary.
P4.5 closes route/UI activation; P4.6 owns exhaustive SQL failpoints,
process/restart races, 1,000 retries, and the complete `PUB-001..013` gate.

## P4.5 canonical publication route and shared target modal (task verified)

P4.5 adds one exact canonical Flask boundary for manual Markdown, upload,
canonical chat range, and authorized Notion preview creation, durable status and
cancellation, and publication commit. Actor ID and scope epoch come only from
the injected server principal. Request JSON/multipart is bounded, duplicate or
extra members are rejected, upload bytes/media are preserved, operation IDs are
bound to `Idempotency-Key`, and the public commit response exposes only the
contracted target rows. A standalone registrar supplies only target discovery
and publication routes for the Notion host; it cannot bind conversation,
retrieval, management, or Agent execution dependencies.

The browser transport first performs a non-mutating `OPTIONS` capability probe.
Only a normalized absent/404 result permits the compatibility UI. A probe
failure or any canonical preview/commit error is surfaced without invoking the
legacy mutation, preventing an unknown-outcome double write. Canonical read
mode remains available when only the publication probe fails. The production
write feature flag is still rejected by the rollout parser, so these adapters
remain disabled until the later gate explicitly enables them.

One body-safe shared modal serves knowledge upload, canonical chat promotion,
and both Notion publish entry points. It renders personal, concrete current-part,
and personal+part modes from canonical target discovery; disables unavailable
modes; freezes the preview ID/source SHA/allowed modes; shows the permanent
no-auto-sync warning; polls queued extraction; cancels queued or ready previews
through the durable API; and renders created/provenance/update-proposal/no-op
labels per target. Each result button selects the exact canonical space and
highlights the exact item. A Notion result opens the Code host with validated
space/item IDs, which the canonical browser consumes as the same target action.
The Notion compatibility path saves any dirty main/side-peek page first and is
entered only when the canonical route is authoritatively absent.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused route,
transport, modal, Notion bridge, durable-cancel, and exact-target proof passed
24/24 in 7.10 s. The combined P4.1-P4.5 artifact/preview/publication/route/
browser/composition/Notion-adapter selection passed 158/158 in 128.46 s.
Canonical P3 route/conversation/scope regression passed 57/57 in 73.78 s, and
the five existing Notion JavaScript publication/runtime/navigation/atomic-save/
editor suites passed. Python `py_compile` and JavaScript `node --check` passed.
Exact commands, hashes, invariants, and non-claims are frozen in
`evidence/P4.5.md`.

This closes only P4.5 behind disabled canonical write traffic. No feature flag,
live route/service/database/worker, personal write, or part write was enabled.
No `PUB-*` acceptance scenario is claimed complete here. P4.6 still owns every
SQL failpoint, real process/restart race, 1,000 exact retries, and the complete
`PUB-001..013` gate.

## P4.6 publication failure/idempotency acceptance gate (verified)

P4.6 adds no runtime behavior. It closes the publication implementation gate
by binding all `PUB-001..013` scenario IDs to permanent tests over the disabled
P4.1-P4.5 boundary. The test matrix dynamically observes application DML on a
new dual publication and on a dual changed-Notion update proposal, then raises
immediately after each observed write. The frozen paths contain 24 and 16 write
boundaries respectively; every injected failure restores the exact
ready-preview baseline and leaves zero partial operation, target, lineage,
item/revision/source, finding/proposal, audit, outbox, generation, or dedup
effect.

The idempotency gate performs one initial dual publication followed by 1,000
real service retries. It also uses independent Python processes for four-way
same-operation contention, a post-commit response-loss exit followed by a
fresh-process retry, and two distinct operations racing equal content. These
paths retain exactly one item/revision per target, add only the required
provenance, report no unhandled busy/locked result, and return the immutable
stored response. Separate processes reconstruct, poll, cancel, and repoll a
ready preview without creating knowledge. Existing durable-kernel tests cover
asynchronous extraction restart and cancellation; terminal tests cover expiry,
consumption, actor/scope/source drift, and 30-day zero-reference cleanup.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the final
`PUB` service/preview/repository/route selection passed 71/71 in 168.70 s. The
combined P4.1-P4.6 artifact, preview, durable-kernel, publication, route,
browser, composition, Notion-adapter, and restart selection passed 174/174 in
245.32 s. The explicit SQL matrix passed 2/2 in 8.12 s and reported 24 dual-
create plus 16 dual-update-proposal write boundaries. Contract audit tests
passed 21/21 in 37.93 s. Exact hashes, commands, scenario mapping, and
non-rollout limits are frozen in `evidence/P4.6.md`.

This closes the tested P4 prerequisite for P5. It does not enable a feature
flag, route, process, worker, personal/part write, production database, or live
traffic. P2.8 remains separately blocked on operator-approved rehearsal inputs.

## P5.1 persistent fenced lease service (task verified)

P5.1 replaces the lease repository/service stubs with a persistent canonical
fence over the existing `item_lease` row. Acquire claims only a free or expired
lease and increments generation exactly once; expiry reclaim and authorized
force release also increment generation, while an exact owner release clears
the holder without reusing or decrementing the fence. Heartbeat extends stale
expiry only up to the fixed absolute expiry. Every holder operation verifies
actor, session, strict token HMAC, generation, expiry, item row version, and
base revision as applicable.

Raw 43-character base64url tokens decode to exactly 32 bytes and are never
stored in SQLite, audit/outbox/dedup payloads, results, reprs, or logs. The
service accepts an externally protected active/old HMAC key ring, so an
existing lease remains usable across rotation without storing keys in SQLite.
Acquire, owner release, and reason-bound force release own short transactions,
revalidate current EDIT/FORCE_RELEASE authority, and commit the lease change,
operation result, audit, and outbox together. Heartbeat intentionally writes
only the lease time fields and has no audit/outbox/idempotency row. A reusable
same-transaction edit-fence proof is exposed for P5.2; it reports explicit
copy/reacquire/compare draft recovery and never permits forced overwrite.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, Python
`py_compile` passed and the final focused service suite passed 17/17 in
29.56 s. A direct 40-process barrier test passed 1/1 in 7.99 s and produced
one holder plus 39 `LEASE_HELD` results with no busy/locked result. The combined
lease/scope/organization/account/schema selection passed 123/123 in 135.68 s,
and the existing P4 publication selection passed 71/71 in 166.83 s. Exact
commands, implementation hashes, scenario ownership, and non-rollout limits
are frozen in `evidence/P5.1.md`.

This closes only P5.1 behind disabled traffic. It does not register a lease
route, enable a flag, open a live database, configure the deployment key
provider, or authorize personal/part writes. P5.2 subsequently closed the
immutable save transaction and save-coupled lease scenarios below. P2.8
remains separately blocked on operator-approved rehearsal inputs.

## P5.2 immutable item edit service (task verified)

P5.2 composes the P5.1 fence proof with one service-owned immutable save
transaction. It binds the caller operation to the exact lease token HMAC,
fencing generation, expected item row version/revision, normalized and raw
Markdown hashes, content size, and edit reason. A successful save enqueues one
`curation.rescan` v1 job, inserts one immutable revision/body and one
revision-rescan transition, compare-and-swaps the item to the new revision and
provisional state, obsoletes stale fixed-revision findings and update proposals,
marks affected semantic edges inactive and needing revalidation, removes the
search document, advances space generation, releases the lease, and appends one
audit/outbox plus committed edit/job idempotency results in the same transaction.

The required `edit.after_revision_insert`,
`edit.after_current_pointer_update`, `edit.after_audit`, and
`edit.after_outbox` boundaries are permanent tests. Additional rescan enqueue,
repository-effect, lease-release, and before-commit boundaries also roll back.
A dynamic matrix raises after every one of 16 observed application DML writes
and proves exact restoration of item, revision/body, transition, job, lease,
finding/proposal, edge, search, generation, audit/outbox, and dedup state.
Stored revision metadata/body reject UPDATE and DELETE; exact retries add no
effect; operation-ID reuse with changed content/base/reason is rejected; raw
tokens remain absent from repr, response, DB, WAL, and SHM bytes.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, Python
`py_compile` passed, the final P5.2 focused suite passed 19/19 in 29.40 s, the
lease/edit/job/scope/organization/account/schema selection passed 169/169 in
178.20 s, and the existing P4 publication selection passed 71/71 in 166.52 s.
Exact commands, hashes, scenario ownership, and non-rollout limits are frozen
in `evidence/P5.2.md`.

This closes only P5.2 behind disabled traffic. It does not register the edit
route/UI, enable a flag, start a worker, open a live database, or authorize
personal/part writes. P5.3-P5.6 own lifecycle, activity/editor UI, and purge;
P5.7 owns real web-process save races, 40-client lifecycle load, and the
aggregate P5 gate. P2.8 remains separately blocked on operator-approved
rehearsal inputs.

## P5.3 deactivation and restore episodes (task verified)

P5.3 adds separate unregistered deactivate and restore application services
over the canonical item/lease/episode/search boundary. Both commands bind an
operation ID and expected item row version, re-resolve the actor's current
capability inside a service-owned immediate transaction, and preserve the same
item ID, current revision ID, curation state, semantic edges, and deterministic
references. Deactivation requires a bounded non-empty reason, rejects a foreign
live lease, fences an actor-owned or expired lease, inserts one immutable
episode with SQLite-time eligibility exactly 7,776,000,000 ms later, marks the
item inactive, removes its transactional search row when published, advances
space generation, and appends one audit/outbox. Restore closes only the latest
open episode, marks the same item active, restores a published search row from
hash-verified current-revision bytes, advances generation, and appends one
audit/outbox. The frozen P5.3 policy does not enqueue a review job.

Inline Markdown is checked directly. Blob-backed restore accepts only an
injected externally protected content loader; it loads outside the write
transaction, verifies UTF-8 byte size and SHA-256, then revalidates the frozen
item/revision/body evidence inside the write transaction. P5.3 does not wire a
route or a live blob provider. Exact operation replay needs no second content
load and adds no effect.

Permanent tests cover `LIFE-001..006`, exact episode/audit/outbox payloads,
active/expired/actor-owned lease decisions, conditional search and Agent
visibility, same-ID restoration, byte-identical active/inactive edge/reference
rows, deterministic re-deactivation eligibility, stale/idempotency conflict,
secret exclusion, blob evidence, two service instances, seven deactivate and
six restore named failpoints, and every observed 9 deactivate/8 restore DML
boundary. Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`,
focused proof passed 45/45 in 64.95 s; combined lease/edit/lifecycle passed
81/81 in 120.38 s; job/scope/organization/account/schema integration passed
221/221 in 251.01 s; P4 publication passed 71/71 in 164.51 s; and canonical
retrieval/pin regression passed 35/35 in 43.40 s. Exact commands, hashes,
scenario ownership, and non-rollout limits are frozen in `evidence/P5.3.md`.

This closes only the tested P5.3 implementation prerequisite behind disabled
traffic. It does not register lifecycle routes/UI, enable any flag, open a live
database, configure a production blob loader, start a worker, or authorize
personal/part writes. P5.4-P5.6 own activity/editor UI and purge. P5.7 owns
actual web-process save/deactivate/restore contention, 40-client lifecycle
load, and the aggregate P5 gate. P2.8 remains separately blocked on
operator-approved rehearsal inputs.

## P5.4 activity and immutable version reads (task verified)

P5.4 extends the disabled canonical read surface with item detail, revision
list, revision detail, and structured comparison. `ItemHistoryRepository`
reads one space-bound item snapshot, its immutable revisions, retained body
references, live lease summary without a token/session ID, edge/reference/pin
impact, latest human review, and the latest open deactivation episode joined to
its immutable audit actor display snapshot. `CanonicalManagementReadService`
re-resolves scope for every request, calculates content/review/inactive ages
from database time, limits capabilities by item state, loads blob bytes outside
the transaction, verifies strict UTF-8/size/SHA-256, then re-reads scope and
immutable evidence. The deterministic `unified-v1` comparison is limited to
2,000,000 characters, 20,000 lines per input, and 2,000,000 output characters.

The JSON Schema now closes item timeline/lifecycle/deactivation, full revision,
diff hunk, and sanitized activity shapes. The canonical transport exposes the
four reads with strict UUID/limit/query binding. The browser renders via
`textContent` only and shows content/review/inactive age, actor/date/reason,
purge eligibility, edge/reference/pin impact, live editor summary, revision
editor/reason/history, historical body and structured diff. It never reads raw
audit JSON or displays lease credentials.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P5.4
proof passed 7/7 in 10.63 s; complete edit/lifecycle files passed 67/67 in
98.45 s; retrieval/browser/publication files passed 52/52 in 38.17 s; canonical
route registration passed 19/19 in 28.42 s; and the documentation/contract
audit passed 21/21 in 37.75 s; direct audit passed with zero errors, 55 OpenAPI
operations, 17 stable errors, and 10/10 schema examples. Exact commands,
hashes, and non-rollout limits are frozen
in `evidence/P5.4.md`.

This closes only P5.4 behind disabled traffic. It does not add edit/deactivate/
restore routes, configure a production blob provider, enable flags, open a live
database, start a worker, or authorize personal/part writes. P5.5 owns the
lease editor UI, P5.6 owns purge, and P5.7 owns real web-process contention,
40-client lifecycle load, and the aggregate P5 gate. P2.8 remains separately
blocked on operator-approved rehearsal inputs.

## P5.5 lease-aware browser editor (task verified)

P5.5 adds an optional five-route mutation surface to the existing disabled
canonical registrar. It is absent unless an `item_edit_service` exposing
acquire, heartbeat, release, force release, and immutable save is explicitly
injected; read-only registration therefore remains exactly 21 routes. The
composition root binds this service only under an explicitly constructed write
configuration, while production feature-flag parsing continues to reject write
enablement. Acquire accepts an operation/editor-session pair and a header-only
43-character token. Heartbeat and release additionally require exact lease ID
and fence; save binds the same tuple to expected row/revision, Markdown and
reason, and an optional `If-Match` must equal the body revision. Force release
requires a mandatory reason and exact `FORCE_RELEASE` confirmation before the
service revalidates leader/administrator authority and audits the result.

The browser generates 32 bytes with `crypto.getRandomValues`, keeps the raw
token only in one active-editor closure, and exposes only a sanitized editor
summary. It heartbeats every 30 seconds, displays foreign holder/heartbeat/
expiry as read-only, saves and releases on completion, offers copy-or-discard
before cancel/release, stops heartbeat on conflict/leave, and preserves the
local draft with `view current`, `copy draft`, and `reacquire and compare`.
Neither browser events nor error responses include draft content or token, and
there is no force-overwrite path. Page unload release is deliberately best
effort; the persistent expiry/fence is the authority.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the focused
route/browser proof passed 12/12; the complete lease/edit/route/browser
selection passed 67/67; existing browser regression passed 18/18; and contract
audit passed 21/21. Exact commands, hashes, and non-rollout limits are frozen
in `evidence/P5.5.md`.

This closes only P5.5 behind disabled traffic. It does not enable a feature
flag, configure a production HMAC key provider, open a live database, start a
worker, or authorize personal/part writes. P5.6 owns purge; P5.7 owns real
two-web-process/40-client lifecycle contention and the aggregate P5 gate. P2.8
remains separately blocked on operator-approved rehearsal inputs.

## P5.6 administrator purge and blob GC (task verified)

P5.6 adds optional canonical item purge-preview/command routes and an optional
administrator purge-eligible queue. Registration remains inert unless the
complete service is explicitly injected. The service accepts no client role,
actor, or eligibility assertion: it re-reads the canonical active administrator
and scope epoch, uses database time, and binds an exact item/space/row/state
snapshot into a short-lived HMAC preview. Execution requires an operation ID,
the matching preview, and literal `PURGE <item_id>` confirmation.

One owned transaction repeats the latest open 90-day episode and blocker
checks, writes the purged tombstone and episode, removes FTS and every revision
body, conditionally removes only last-reference artifact/extraction bodies,
retains immutable revision/item/source/lineage/hash/provenance/audit metadata,
advances space generation, appends one audit and outbox event, and enqueues only
fully unreferenced verified blobs after a further 24-hour quarantine. Named
failpoints and DML-boundary tests prove rollback and exact idempotent replay.

Forward schema v7 preserves v1-v6 and adds eight insertion/claim/finalize/
backup-race guards. `BlobGcService.run_once` claims and finalizes in separate
short transactions and performs no file I/O while SQLite is held. The Windows
provider revalidates the approved storage layout, pins the CAS directory chain,
opens the exact non-reparse file without following, requires one hard link,
streams and verifies exact size/SHA-256, and marks that handle delete-pending;
path-based unlink is never used. Missing, retry, backup-held, newly referenced,
wrong-byte, hardlink, and replay outcomes fail closed. The service contains no
timer, scheduler, thread, worker registration, or import-time execution.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the complete
P5.6/lifecycle/admin/schema selection passes 104/104 in 124.67 s; adjacent
composition/publication/retrieval regression passes 86/86 in 165.35 s; and
production/test `py_compile` passes. Exact commands, reviewed schema/catalog
hashes, source hashes, scenario ownership, and non-rollout limits are frozen in
`evidence/P5.6.md`.

This closes only P5.6 behind disabled traffic. It does not enable a flag, open a
live database or root, provision a production preview key, start GC, create a
backup, register a worker/scheduler, or authorize personal/part writes. P5.7
owns actual two-web-process/40-client lifecycle contention and the aggregate P5
gate. P2.8 remains separately blocked on operator-approved rehearsal inputs.

## P5.7 cross-process lifecycle/concurrency gate (phase verified)

P5.7 adds the optional canonical deactivate/restore routes to the already
dependency-injected write surface and verifies the aggregate P5 contract using
two independent OS processes, one isolated canonical SQLite database, and 40
simultaneous real HTTP clients. The test-only servers use Werkzeug as a bounded
WSGI correctness fixture; they do not represent the production WSGI/service
choice owned by P7.5.

The raw result is exactly one successful same-item lease acquisition and 39
stable `LEASE_HELD` results. Force-released, membership-moved, and
stale-revision save success counts are zero. Forty same-fence valid-base saves
produce one immutable revision plus one audit and outbox effect. Forty
deactivations and forty restores each produce one state transition plus one
audit and outbox effect. The deterministic edge/relation checksum is
`d3859858f558ca59be59ac16a37626dc6c8ff5f7591d30e71713364f0fab6b51`
before and after the lifecycle races.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the standalone
raw gate passes 1/1 in 16.72 s; the aggregate lease/edit/lifecycle/browser/
purge/GC selection passes 125/125 in 179.87 s; and adjacent composition/
publication/retrieval regression passes 86/86 in 165.63 s. Exact commands,
hashes, scenario aggregation, and non-rollout limits are frozen in
`evidence/P5.7.md`.

This closes P5.1-P5.7 as isolated implementation/correctness evidence behind
disabled traffic. It does not enable flags, open live state, configure
production keys, start services/workers, authorize personal/part writes, or
claim P7.5 production WSGI or P7.8 target-server `PERF-001`. P6 owns curation,
graph governance, and merge; P7 owns durable operations/recovery; P8 plus
explicit approval owns activation. P2.8 remains separately blocked on
operator-approved rehearsal inputs.

## P6.1 deterministic ingest lint/dedup (task verified)

P6.1 replaces the curation stub with a pure, versioned deterministic policy.
`p6.1-deterministic-ingest-v1` binds the source identity, target space, raw
SHA-256, LF-normalized SHA-256, size, and normalizer version without retaining
body text. The current publication preview, audit, and outbox payloads bind the
same rule and `markdown_lf_v1` normalizer versions.

The exact decision boundary is space-scoped and fail-closed: same-source exact
content is a no-op; a same-space exact repeat from another source may add only
provenance; changed bytes under the same source identity, normalized-only
matches, and cross-space exact matches remain no-effect candidates; and
multiple source/exact canonical targets are rejected. Production publication
tests prove that normalized-only content creates a distinct item and exact
relations are not created across personal spaces.

The structural lint policy preserves the P4 empty/control/unclosed-frontmatter/
unclosed-fence blockers and adds bounded warning-only duplicate-H1, heading
jump, and long-line findings while ignoring frontmatter and fenced-code heading
text. Corpus and lint metrics are order-independent, body-free, hash-sealed,
and keep automatic merge count structurally zero.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the P6.1/P4
policy-publication selection passes 101/101 in 198.81 s. The changed publication
rule also passes the P5.7 web/lifecycle/composition regression selection at
102/102 in 127.79 s. Exact commands, source hashes, scenario ownership, and the
bounded host-retry note are frozen in `evidence/P6.1.md`.

This closes only P6.1 behind disabled traffic. It does not persist curation
findings, retrieve top-K candidates, execute semantic or merge effects, invoke
a model, enable a flag, open live state, or authorize personal/part traffic.
P6.2-P6.8 and the aggregate P6 gate remain open; P2.8 still requires
operator-approved rehearsal inputs.

## P6.2 incremental candidate retrieval (task verified)

P6.2 adds a dedicated read-only candidate repository and service without
changing schema, routes, jobs, or feature flags. The service accepts exactly
one changed current revision and body, opens one canonical deferred snapshot,
and binds item/revision/space, active lifecycle, provisional-or-published source
state, raw/LF-normalized SHA-256, size, normalizer, and stored body evidence.

The `p6.2-fts-bm25-v1` query selects at most 32 deterministic NFKC-casefolded
Unicode-alphanumeric literal terms. It executes zero queries when no terms
exist and otherwise exactly one FTS5 `MATCH` restricted to the concrete space,
excluding the source and requiring current active/published targets. BM25 plus
item ID provides stable ordering and SQL applies top-K<=20. Returned target
title/body bytes are rechecked against canonical revision/body metadata before
only IDs, ranks, finite scores, hashes, sizes, and exact/normalized/lexical kind
are emitted.

The body-free result validator independently closes version/mode/counters,
UUIDs/hashes/integers, source exclusion, target item/revision uniqueness,
contiguous ranks, score ordering, derived match kind, top-K, and evidence hash.
The code has no file/network/model/write surface; all-pairs, model-call, and
automatic-effect counters are structurally zero. Stale source identity/body,
foreign space, stale/tampered search body/title, invalid limits, forged ranks,
scores, kinds, counters, and hashes fail closed.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P6.2
passes 10/10 in 11.11 s. P6.2 plus canonical retrieval and P6.1/P4 publication
regression passes 137/137 in 239.40 s. Production/test `py_compile` passes.
Exact commands, source hashes, scenario ownership, and non-effect limits are
frozen in `evidence/P6.2.md`.

This closes only P6.2 behind disabled traffic. It does not persist findings,
derive weak/strong visibility policy, review or apply semantic effects, run a
model/job, enable a flag, open live state, or authorize personal/part traffic.
P6.3-P6.8 and the aggregate P6 gate remain open; P2.8 still requires
operator-approved rehearsal inputs.

## P6.3 versioned finding service (task verified)

P6.3 adds a canonical finding repository and one transaction-owning recording/
review service without adding a route, flag, worker, or schema migration. The
repository derives the complete fixed v1 kind/severity/risk/mode/reviewer/
visibility/action policy and rejects any unsupported combination. A detector
can supply evidence, never policy authority.

Persisted evidence is canonical, bounded, and body-free. Transient Markdown is
rebound to the current revision's raw hash, LF-normalized hash, size, and
normalizer before every 1-based/end-exclusive excerpt span hash is verified.
Candidate-backed semantic findings additionally require the complete P6.2
snapshot; its independent validator, source identity/hashes, selected target
identity/title/content hashes, and evidence SHA must all agree. Same-space,
current lifecycle/curation, pair shape, model/prompt pairing, confidence,
evidence-code ordering, exact-repeat hashes, and semantic relation vocabulary
fail closed.

Human review re-resolves the authenticated principal's current `CURATE`
capability and reviewer class inside one short write transaction. Expected
finding row version and endpoint currency are checked immediately before the
transition; drift records `obsolete`. A successful decision, immutable
operation result, audit row, and outbox row commit together. Exact retries
return the prior result; a changed command under the same operation ID fails.
Weak complementary remains published with badge/no action. Strong near-
duplicate and potential-conflict rows remain pending human review and
Agent-hidden. T3 approval is only a reviewed finding state: it does not call
deactivate/purge or any other effect service. Automatic findings reject human
review, and the modules expose no model/network/domain-action execution path.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P6.3
passes 38/38 in 28.92 s. P6.1-P6.3 plus immutable-edit obsoletion regression
passes 77/77 in 71.60 s. The finding/schema transition selection passes 76/76
in 71.70 s, and production/test `py_compile` passes. Exact commands, hashes,
scenario ownership, and non-rollout limits are frozen in `evidence/P6.3.md`.

This closes only P6.3 behind disabled traffic. P6.4-P6.8 still own reviewed
semantic-edge effects, merge/proposal application, UI/shadow precision, the
complete curation acceptance matrix, and the aggregate P6 gate. P2.8 still
requires operator-approved rehearsal inputs.

## P6.4 reviewed semantic edge flow (task verified)

P6.4 adds a dedicated repository and transaction-owning application service
without changing schema, routes, jobs, feature flags, or deployment state. The
service accepts only a canonical session principal with current `CURATE`
authority and an explicitly approved `semantic_edge/T2/human/space_editor/
relation` finding reviewed by that editor. The request binds expected finding
row version, exact canonical finding-evidence SHA-256, fixed source/target
revision IDs, reason, and actor-scoped operation identity.

The repository revalidates the immutable finding policy/action, derives the
edge type from its closed six-value relation vocabulary, verifies operation
reservation, and requires both endpoints to remain current, active, published,
and in the same active writable space. Success changes the approved finding to
`applied` with operation proof, inserts exactly one active
`semantic_approved` edge with exact revision evidence, increments the space
generation, and commits audit, outbox, and the stable operation result in the
same transaction. Changed endpoint state records `obsolete` and no edge;
changed evidence/version, cross-scope authority, or an occupied active identity
rolls back. Deterministic provenance remains outside `knowledge_edge`.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P6.4
passes 18/18 in 26.27 s. P6.1-P6.4 plus immutable-edit/revalidation regression
passes 95/95 in 98.54 s. The edge/finding schema and trigger selection passes
56/56 in 68.95 s, and production/test `py_compile` passes. Exact commands,
hashes, scenario ownership, failpoints, confidentiality proof, and non-rollout
limits are frozen in `evidence/P6.4.md`.

This closes only P6.4 behind disabled traffic. P6.5-P6.8 still own merge,
change proposals, review UI/shadow precision, the complete curation acceptance
matrix, and the aggregate P6 gate. P2.8 still requires operator-approved
rehearsal inputs.

## P6.5 transactional merge (task verified)

P6.5 adds a read-only fixed-revision preview and a dedicated merge repository/
transaction-owning service without changing schema, routes, feature flags, or
deployment state. Preview re-resolves `CURATE` authority, materializes two
immutable current revision bodies outside any write transaction, revalidates
the snapshot, and binds the exact proposed result Markdown plus complete
affected semantic-edge and item-relation ledgers. Apply repeats the Markdown
and recomputes the preview instead of trusting process memory.

The mutation transaction validates both held edit leases and expected current
revisions in sorted item-ID order. It inserts one target `merge` revision/body,
queues its curation rescan and provisional transition, marks the source
`merged` with a target redirect, inserts one immutable `merged_into` relation
and `merge_event`, obsoletes fixed-revision findings/proposals, removes both
search rows, increments one space generation, releases both leases, and commits
audit, outbox, job result, and merge result atomically. It does not move or
recreate semantic edges: their semantic identities and evidence endpoints are
postcondition-checked unchanged while affected rows become inactive and
`needs_revalidation=1`.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P6.5
passes 16/16 in 35.03 s. P6.1-P6.5 plus immutable-edit/revalidation regression
passes 111/111 in 130.41 s. Schema table/trigger/acceptance/catalog plus merge
passes 60/60 in 82.03 s, and production/test `py_compile` passes. Exact
commands, hashes, `MERGE-001..004` ownership, nine failpoints, confidentiality,
and non-rollout limits are frozen in `evidence/P6.5.md`.

This closes only P6.5 behind disabled traffic. P6.6-P6.8 still own three-way
proposal/rebase/apply, review UI/shadow precision, the complete curation
acceptance matrix, and the aggregate P6 gate. P2.8 still requires
operator-approved rehearsal inputs.

## P6.6 three-way proposal service (task verified)

P6.6 adds a dedicated proposal repository and transaction-owning service
without changing schema, routes, feature flags, or deployment state. Preview
re-resolves current `CURATE` authority and fixes the finding/proposal versions,
base/source/target revision bodies, personal lineage or stable extraction
snapshot, proposed body, and current item row versions. Blob materialization is
outside any write transaction and a second authorized read must be identical
before a preview is returned.

Personal-to-part preview exposes fork-base, personal-current, part-current, and
the proposed result; stable-source preview preserves the exact extraction body.
Explicit rebase creates one deterministic next finding/proposal version and
obsoletes the old pair in one transaction. Apply is restricted to the recorded
reviewer and checks the fixed preview, expected source/target revisions, target
row version, lease token HMAC, editor session, and fence before creating one
`approved_update` revision/body, rescan transition/job, provisional current
pointer, applied pair, generation increment, semantic-edge revalidation, FTS
removal, lease release, audit, outbox, and operation result atomically.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P6.6
passes 24/24 in 49.31 s. Proposal/finding/publication/edit/merge regression
passes 121/121 in 273.75 s. Schema table/trigger/acceptance/catalog regression
passes 44/44 in 56.35 s, and production/test `py_compile` passes. Exact
commands, hashes, `PROP-001..005` ownership, 14 failpoints, blob-independent
terminal replay, confidentiality, and non-rollout limits are frozen in
`evidence/P6.6.md`.

This closes only P6.6 behind disabled traffic. P6.7 owns the review queue/UI
adapter and shadow precision; P6.8 owns the complete `CUR-*`, `PROP-*`, and
`MERGE-*` acceptance matrix and aggregate P6 gate. P2.8 still requires
operator-approved rehearsal inputs.

## P6.7 curation review surface and shadow metrics (task verified)

P6.7 connects the disabled canonical curation surface without transferring
authority into the browser or route layer. The management candidate page shows
a body-free queue and an authorized detail view with detector/model warnings,
fixed revision evidence, visibility, proposed-action evidence, and reviewed,
rejected, ignored, or obsolete lifecycle state. Approve/reject/ignore,
proposal, and merge commands delegate to the existing transaction-owning
services. Source/target lease tokens remain named headers and never enter JSON.

The route group exists only when the host explicitly binds the curation facade
and enables a write-capable rollout; the read-only rollout and default flags
register no curation routes. A read-only `CURATE`-authorized metric derives a
bounded, deterministic, body-free sample of human-adjudicated model proposals.
It reports precision together with general, risky-kind, and T3 automatic-effect
counts and returns `pass` only when every automatic-effect count is zero. This
is fixture evidence in shadow/proposal-only mode, not a live-corpus claim.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P6.7
route/UI/metric proof passes 18/18. Retrieval, candidate, finding, and
composition regression passes 113/113; proposal, merge, semantic-edge, and
browser regression passes 81/81; contract audit passes 21/21. Direct contract
audit passes 31/31 requirements, 14/14 goals, 124/124 scenarios, 52 OpenAPI
paths/56 operations, and 10/10 schema examples. Exact commands and hashes are
frozen in `evidence/P6.7.md`.

This closes only P6.7 behind disabled traffic. P6.8 still owns the complete
`CUR-*`, `PROP-*`, and `MERGE-*` reconciliation and aggregate P6 gate. No flag,
traffic, model, worker, service process, live root, or live database was enabled
or used. P2.8 remains separately dependent on operator-approved rehearsal
inputs.

## P6.8 aggregate curation acceptance gate (verified)

P6.8 reconciles the complete curation/proposal/merge acceptance matrix without
adding a new mutation surface. Permanent scenario markers now cover exactly
`CUR-001..011`, `PROP-001..005`, and `MERGE-001..004`; a dedicated ownership
test fails if any of these 20 IDs loses its test owner.

The aggregate proof combines deterministic byte/source handling, normalized-
only candidate behavior, provisional/Agent-hidden risky content, weak badge
policy, reviewed semantic edges, ignored/stale lifecycle, versioned evidence,
three-way proposals, and dual-fenced merges. The bounded shadow report closes
the observational invariant: general model automatic effects, risky semantic/
merge/replace/deactivate/purge effects, and T3 effects are all zero, and report
status fails if any count becomes nonzero.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, deterministic
ingest/publication passes 32/32 in 134.78 s; candidate/finding/semantic-edge/
shadow/retrieval passes 100/100 in 105.77 s; proposal/merge passes 40/40 in
88.14 s; and scenario ownership passes 1/1. Exact commands, scenario mapping,
and hashes are frozen in `evidence/P6.8.md`.

P6.1-P6.8 and the P6 implementation/test gate are now complete behind disabled
traffic. This does not start a detector/model/worker, enable a feature flag or
traffic, inspect a live corpus, or claim deployment. P2.8 remains dependent on
operator-approved rehearsal inputs.

## P7.1 durable job/claim service (task verified)

P7.1 reuses rather than replaces the P1.9 canonical job authority. Producers
persist exact-version handler identity, durable input/expected-state and
authorization references, captured scope, stable effect key, and operation
binding. The repository and kernel retain attempt-count ABA fencing, claim
lease expiry/recovery, cooperative cancellation, retry/dead convergence,
strict JSON/hash integrity, current-scope/expected-state revalidation, and a
single transaction for canonical domain effect plus stable job result. The
real-process enqueue-kill, reclaim, stale-fence, claim-race, commit/ack crash,
account-disable, membership-move, and cancellation proofs remain green.

The P7.1 addition is the previously missing public service boundary. Read-only
status and mutation-owning cancellation require a current canonical session and
resolve only `actor_kind='user'` rows owned by that actor. Foreign, system, and
missing jobs share a nondisclosing `NOT_FOUND`; stale/disabled principals fail
with `SCOPE_STALE`. Queued/retry work cancels immediately, while claimed/running
work records a cooperative request for the fenced worker. One actor-scoped
operation ID binds the exact job and captured epoch. Job state, sanitized audit,
and committed operation result are atomic; exact response-loss replay returns
the original outcome with the current request ID, and changed input conflicts.

Canonical GET/cancel routes are registered only when `ServiceRegistry.jobs` is
explicitly bound. GET may be exposed under a disabled read-capable composition;
cancel additionally requires the write-capable disabled rollout. The browser
transport validates the exact job state/null shape, identity, status URL,
progress/attempts, terminal result/error shape, and cancel operation/resource
binding before returning data. It uses only `/api/knowledge/v1/jobs/...` and
never sends actor identity from the browser.

With the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the new service,
route, and transport proof passes 20/20 in 14.81 s. The unchanged durable
repository, kernel, and multiprocess selections pass 19/19, 7/7, and 6/6.
Related route/composition regression passes 63/63 and browser transport
regression passes 22/22. Contract audit passes 21/21. Exact commands, artifact
hashes, boundaries, and scenario reuse are frozen in `evidence/P7.1.md`.

This closes P7.1 only. It starts no polling/heartbeat loop, production worker,
model process, GPU scheduler, projection materializer, feature flag, route
traffic, or service process and touches no live root/database. The P7.2 section
below now owns and closes central worker GPU concurrency/fairness and production
web-process model ownership; P7.3 is closed below, while P7.4-P7.9 and the
aggregate P7 gate remain open.

## P7.2 central inference coordination (task verified)

P7.2 adds an explicit `DurableInferenceWorker`; import and construction start
nothing. `run_once()` and `serve()` first acquire the server-wide Windows model
owner, verify exact handler coverage, recover expired claims, and then claim at
most one durable row. `serve()` uses stop-aware exponential idle backoff. A
separate heartbeat renews a long preparation at less than one third of the
lease, while the repository's owner/attempt/expiry fence remains authoritative
for terminal commit. Exact handler versions and actor lanes rotate according to
durable prior attempts, including after reconstructing the worker object.

`CODE_ASSIST_PROCESS_ROLE` is exact and defaults to `development` solely for
compatibility. `web` hosts omit GGUF catalog/default entries and fail before
file discovery, path resolution, model load, or execution. They also cannot
submit through the historical callable `JobManager` or start the Outlook
scheduler. `worker` cannot compose Flask hosts and may call local inference only
inside one machine-wide `Global\\CodeAssistV1InferenceOwnerV1` mutex. The name
does not vary by DB path, so another deployment cannot create a second GPU lane;
Windows process death releases/abandons the owner and a restarted service takes
it over without a mutable file lease. Development retains the prior in-process
paths and regression behavior.

With the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, focused P7.2
model/worker/process proof passes 13/13. The durable repository, kernel, and
multiprocess partitions pass 19/19, 7/7, and 6/6. Actual publication durable
enqueue/handler proof passes 34/34; host composition passes 59/59; legacy
development job/Outlook regression passes 7/7; and curation/chat regression
passes 9/9. Exact commands, hashes, and safety limits are frozen in
`evidence/P7.2.md`.

This closes P7.2 only behind disabled traffic. It does not start a production
worker/model/service, enable a feature flag or route traffic, inspect a live
root/database, package the Windows services, prove target-GPU capacity, run the
randomized kill profile, materialize projections, back up, or restore. P7.3 is
closed below; P7.4-P7.9 and the aggregate P7 gate remain open.

## P7.3 outbox materializer (task verified)

P7.3 turns the existing deterministic full-rebuild primitive into an explicit
durable per-space projector without starting it at import or construction.
`OutboxRepository.append()` now inserts or advances the Markdown
`projection_state.desired_generation` using the current canonical space
generation in the producer's transaction. `OutboxProjectionWorker.run_once()`
recovers expired work, claims one oldest available event, renews long work below
one third of the lease, rebuilds only the selected space, and acknowledges only
when canonical, desired, and built generations are equal and checksum-bound
state is ready. Retry/dead errors are stable and sanitized. An older failing
event cannot replace a newer complete state with a false projection error.

Each generation contains `sources/<item-id>.md`, `index.md`, `log.md`,
`graph/graph.json`, and `graph/backlinks.json` from one read-consistent canonical
snapshot. Every file and manifest entry is hashed before the pinned completed
generation is exposed by atomic manifest replacement. `log.md` uses the exact
file-visible action set associated with the seven live generation-writer
repositories; non-visible lease/conversation/preview/finding audits do not
change an immutable numeric generation. Canonical drift during staging or after
manifest replacement prevents stale acknowledgement and retries at the latest
generation. Events without a space are explicit projection no-ops.

With the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the projector,
materializer, canonical-fallback, and real-process proof passes 48/48 in
89.77 s. A standalone two-process partition passes 2/2 in 8.51 s. Affected
account/space/edit/lifecycle/publication/semantic-edge/merge/proposal producer
regression passes 187/187 in 416.80 s, and migration full-rebuild verification
passes 14/14 in 42.76 s. Exact commands, hashes, safety limits, and scenario
ownership are frozen in `evidence/P7.3.md`.

This closes P7.3 only behind disabled traffic. It starts no projector or Windows
service, enables no feature flag or route, and accesses no live root/database.
P7.4-P7.9 still own backup/restore tooling, production service packaging,
health/metrics, randomized target faults, target-class load, and operational
recovery; the aggregate P7, `G-10`, and `G-11` gates remain open.

## P7.4 backup and isolated restore tooling (task verified)

P7.4 adds an explicit operator-only backup path. A durable preparing run blocks
GC before SQLite's online backup API captures a standalone snapshot; the copying
transition installs the exact snapshot-derived blob hold set. The self-hashed
manifest binds the current seven-version schema/catalog/physical checksum,
approved SQLite version/source ID, audit high-water, every referenced blob,
application build, configuration, and lease-HMAC key-set versions. Staging and
completion use pinned local-NTFS handles and one same-volume atomic directory
rename; production requires the backup root on another volume.

All four named crash boundaries resume exactly without false completion. A real
writer process commits during backup while the completed snapshot remains
integral. The deterministic UTC retention policy preserves 96 recent
half-hour, 30 daily, and 12 monthly restore points and re-verifies a candidate
before quarantined handle-relative deletion. Isolated restore pins every target
directory through database/blob copy, WAL activation, captured-hold closure,
canonical integrity checks, and full projection rebuild. Manifest or snapshot
tamper fails before target creation. The explicit CLI and three PowerShell
wrappers start no process on import.

Focused backup/restore proof passes 45/45 in 55.19 s. Affected blob-GC,
content-staging, and projection regression passes 32/32 in 47.19 s; all three
operator wrappers parse successfully. Exact hashes and commands are frozen in
`evidence/P7.4.md`. ADR-016 records the phase-1 roll-forward-only recovery
decision for incorporation into the future signed P8.1 approval packet.

This closes only P7.4's disabled implementation boundary. No live source,
canonical production database, external backup target, production service,
traffic, worker, flag, or migration was accessed or enabled. P7.5-P7.6 are
closed below; P7.7-P7.9 still own broad randomized faults, target-server load,
and the operational signed restore drill; aggregate P7, `G-10`, `G-11`, and
`G-12` remain open.

## P7.5 production WSGI and Windows service packaging (task verified)

P7.5 freezes Waitress `3.0.2`, sixteen threads, 128 connections/backlog, a
30-second application drain, and the official WinSW `2.12.0` x64 asset with
SHA-256 `05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da`.
The self-hashed production manifest owns both ports, the five exact process
roles, protected paths, dedicated account, explicit ACL policy, wrapper,
logging, checkpoint interval/mode, and startup order. Development `app.run()`
remains a separate entrypoint and production imports start no listener or loop.

The protected environment parser accepts canonical UTF-8/LF without
interpolation and rejects process/path/test/failpoint authority. Application
namespace values inherited outside the reviewed file are removed. Both local
health origins must match the manifest ports before application composition.
The package renderer rejects non-fixed/non-NTFS, reparse, final-path, kind, and
hard-link failures before writing; runtime preflight additionally binds the
current account, cwd, Python executable, source and active WinSW hashes, exact
runtime lock, SQLite, storage, and feature gates. Only the one-shot migrator
may begin with an absent database, in which case its safe parent is checked and
`SingleMigrator` retains SQLite/storage/schema authority.

The services are `CodeAssist-Migrator`, `CodeAssist-Web`,
`CodeAssist-Notion`, `CodeAssist-DurableWorker`, and `CodeAssist-Projector`.
Installation applies inheritance-removed service read/execute to the immutable
working tree, service modify to data/log/backup roots, service read to the
config/environment files, and full control to SYSTEM/Administrators. The
operator must provision the dedicated account and its service-logon policy;
any SCM credential failure rolls the partial install back. Start runs the
migrator to successful stop, health-gates both Waitress apps, then starts worker
and projector. Stop signals named events and drains in reverse order.

One machine-wide checkpoint mutex belongs to the projector. Its coordinator
uses only periodic `PRAGMA wal_checkpoint(PASSIVE)`; the existing restore-only
isolated activation remains the explicit drained `TRUNCATE` boundary. Real
independent processes prove both Flask apps are served by Waitress rather than
Werkzeug, named-stop delivery, completion of an in-flight response before exit,
and exclusive checkpoint ownership across processes.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the final
focused P7.5 manifest/service/WSGI/checkpoint/role/preflight/process selection
passes 74 tests, and the adjacent database/worker/projector/process/browser-
security selection passes 88 tests. All touched Python files pass `py_compile`;
all three operator wrappers pass the PowerShell AST parser. Exact commands,
hashes, and non-claims are frozen in `evidence/P7.5.md`.

This closes only the disabled P7.5 implementation boundary. No Windows service
or account was installed, no target ACL was applied, no production port or
worker was started, no live database/root was accessed, and no feature flag or
traffic was enabled. P7.6 is closed below; P7.7-P7.9 still own randomized
restart/fault proof, target-class forty-client load, and the signed target
restore/deployment evidence; aggregate `G-10`, `G-11`, `G-12`, and P7 remain
open.

## P7.6 production health and metrics (task verified)

P7.6 replaces the shallow production health response with one versioned,
secret-free snapshot shared by both web hosts. The provider uses a five-second
single-flight cache, catches observation failures into a stable minimal 503,
and never returns filesystem paths, identifiers, source claims, payloads,
stored errors, account names, or secret-bearing environment values.

Before any SQLite, file-size, or free-space observation, the database file,
database parent, and blob root are re-probed as existing fixed local NTFS,
non-reparse, final-path-identical paths. An unsafe role stops collection and
returns only role eligibility booleans and stable reason codes. Normal
collection opens validated read-only connections; all persisted aggregates are
read in one deferred/query-only snapshot. The result binds application and
config revision, migration catalog and current migration, exact SQLite
version/source/compile options/pragmas/executable FTS/quick/FK checks, effective
feature flags, and failpoint absence.

Operational fields include process DB execute/busy/locked counts, durable job
and outbox states/attempts/retries/oldest nonterminal age, active/stale/released
leases, projection desired/built generation and lag/error counts, latest
verified backup age, referenced blob counts/bytes/verification, DB/WAL bytes,
and free capacity for both DB and blob volumes. Readiness is false for an
unapproved SQLite/runtime/schema, invalid flags/failpoints, a missing or older
than 45-minute verified backup, dead outbox, failed/dead jobs, projection
errors, unverified referenced blobs, unsafe storage, or free space below three
times DB plus referenced-blob bytes. The honest application retry count is zero
because no Python DB retry loop exists; durable job/outbox retries are reported
from canonical state.

`start-services.ps1` now accepts only schema
`code-assist.production-health` version 1 with `gate.status=ready` and an empty
reason list before continuing from the two web services to worker/projector.
Fallback development health no longer exposes token source or token file.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the final
DB/health/service/WSGI/checkpoint/script selection passes 101/101 in 59.61 s.
The broader adjacent backup/restore, durable worker, projector, real-process,
browser-security, and runtime-preflight selection passes 291/291 in 227.71 s.
All touched Python files pass `py_compile`, and the start wrapper passes the
PowerShell AST parser. Exact commands, hashes, and non-claims are frozen in
`evidence/P7.6.md`.

This closes only the disabled P7.6 implementation boundary. No target service,
listener, worker, monitoring sink, traffic, flag, live root/database, or
operator state was started or changed. The official target SQLite remains a
deployment gate rather than a workstation claim. P7.7 is closed below;
P7.8-P7.9 still own target-class forty-client load and signed target
restore/service observation. Aggregate `G-10`, `G-11`, `G-12`, and P7 remain
open.

## P7.7 fault and restart suite (task verified)

P7.7 freezes all 54 required failpoint names as one exact, unique vocabulary.
A permanent source/test contract proves every name occurs in production code
and in an executable test. Publication, account, membership, lease, edit,
lifecycle, merge, curation, proposal, migration, backup, projection, and job
boundaries fail inside their owning transaction or at an explicit post-commit
seam. Pre-commit failure restores the exact baseline. Response loss after
commit and hard process termination recover through the same operation/effect
identity and produce no duplicate canonical effect.

Projection failure coverage includes disk-full, permission, and atomic
generation-replace errors. An interrupted writer can leave only an exact
`.generation-<32 lowercase hex>` staging directory; the next pinned generation
writer removes that abandoned staging tree before rebuilding. Arbitrary names,
links, or reparse entries are never treated as cleanup candidates. A real
projector process killed after replacement and before acknowledgement replays
to one settled generation. Durable jobs recover after enqueue/claim/domain-
commit/ack boundaries, including a fixed seed with exactly 20 child-process
worker terminations and zero lost jobs or duplicate effects. Separate-process
SQLite contention, client retry, two-process GPU exclusion, online backup
during writes, backup holds, restore/rebuild, production WSGI, and graceful
drain remain part of the executed scenario set.

Using the official venv and `TEMP=TMP=TMPDIR=D:\CodexTestTemp`, the changed
failpoint selection passes 45/45; full projection and projector-process proof
passes 25/25; durable-job unit/process proof including the 20-kill profile
passes 18/18; publication proof including child-process response loss passes
33/33; the remaining required account/membership/lease/edit/lifecycle/merge/
migration/backup selections pass 50/50; the complete operations selection
passes 92/92; and the exact six projection acceptance nodes pass 6/6. All
touched Python files compile. Exact commands, artifact hashes, scenario
mapping, and non-claims are frozen in `evidence/P7.7.md`.

This closes only the P0.2-isolated, disabled worker/service/traffic P7.7
boundary. No target server, SCM service, production listener, worker, feature
flag, live root/database, real traffic, or operator state was accessed,
started, or changed. P7.8 still requires the documented forty-client profiles
on the target-class Windows server, and P7.9 still requires the signed target
restore drill. Aggregate `G-10`, `G-11`, `G-12`, and P7 remain open.

## P7.8 target load harness (implemented, target gate open)

`tools/performance_load.py` supplies an import-side-effect-free runner,
per-profile report validator, and five-profile gate binder. The three entry
points are `tests/knowledge_spaces/load/run_load.py`, `verify_load.py`, and
`verify_gate.py`; their exact input and command contract is documented in that
directory's `README.md`. The runner cannot provision accounts, start services,
change flags, or infer authorization. Non-validation execution requires the
literal `PERF-001_TARGET_LOAD` before the secret-bearing credential file is
opened.

Every profile binds a canonical reviewed fixture, a separate unreported
credential document, a reconstructable hardware document, a fixed seed, and
distinct Web/Notion HTTPS origins. Both production WSGI health endpoints must
be ready and agree on application/config/catalog/migration/SQLite identity
before authentication; Web health is sampled throughout the run, and both
services are checked after cooldown. Reports bind raw canonical samples to a
Windows-safe CSV, recompute nearest-rank p50/p95/p99 and all criteria, and use
fixed local-NTFS identity-bound input/output handling. The fixture requires 40
accounts in four groups of ten, one hot item per part, distinct saturation
items, and every directed pair in the 40x39 personal-canary matrix. The final
reports also require explicit raw invariant samples for stale-save revision
stability, dual-publication completeness, job terminal/recoverable state, and
at-most-one domain effect. The final
gate accepts exactly one independently valid report for each steady, hot-item,
write-saturation, projection-burst, and leak-scan profile and rejects target,
fixture, seed, hardware, build, schema, or SQLite drift.

The workstation unit suite validates only this harness contract; it does not
create valid performance evidence. No target service, account set, credential,
traffic, worker, feature flag, live root, or database was used. P7.8 remains
open until all five exact target runs pass and their gate artifact is archived.

## Supplemental canonical Agent and Codex acceptance boundary

The canonical route layer now has a concrete model-execution composition rather
than a test-only protocol stub. A turn materializes exactly one authorized
space, one deterministic retrieval result, bounded recent canonical messages,
and the active compact summary. The database snapshot closes before the shared
internal model router is called. The model is instructed to select only from
the deterministic candidates; it receives no cross-space candidates, no tool
authority, and no implicit legacy/private path. Production WSGI composition
injects the canonical runtime only when the existing reviewed canonical read
flags are enabled, and this change does not enable them.

The isolated validation runner creates a fresh canonical database and token-free
Codex-only registry outside the repository, enters through the public
`--codex` parser path, and exercises real login, scope listing, scoped FTS,
conversation creation, SSE turn completion, and readback. The live Codex result
contained the selected Part A marker and contained neither the Part B marker nor
the personal-space marker. The fixture reports `company_api_called=false`.
Executor and isolated-host proof passes 6/6; the provider/production/runtime
selection passes 36/36; and the broader Codex, fallback, retrieval,
conversation, composition, and browser-transport selection passes 120/120.
Exact source hashes and the real result hash are recorded in
`evidence/LOCAL_SECOND_BRAIN_CODEX_VALIDATION.md`.

This is a local functional acceptance boundary, not a target operations gate.
Company endpoint/model-ID compatibility, target-class forty-client load,
off-volume signed restore, service-account/TLS deployment, and P8 pilot/cutover
remain executable company-side handoff work. No company token, endpoint,
production database, live root, feature flag, worker, or production listener
was accessed or changed.

The supplemental full-preview launcher is
`tools/full_second_brain_preview.py`. It binds a non-live copied data root,
preserves the normal API -> configured GGUF -> optional Codex model order, and
registers canonical organization/Part administration alongside the original
knowledge/Index/Log/graph UI. The current loopback preview is documented in
`evidence/LOCAL_FULL_SECOND_BRAIN_PREVIEW.md`; it is not a production-service
or company-provider gate.
