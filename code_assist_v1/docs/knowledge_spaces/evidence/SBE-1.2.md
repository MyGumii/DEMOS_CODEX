# SBE-1.2 evidence

Status: VERIFIED

## Result

- Packet: `SBE-1.2 - v9 Team/Responsibility leadership and native-space schema`
- Acceptance: `HSPACE-001..005`
- State: `VERIFIED`
- Completed: 2026-08-23 16:36 Asia/Seoul.
- Outcome: canonical schema v9 now gives every active organization unit present
  at migration time one native space, introduces independently assignable Team
  leader and Responsibility executive episodes, and records revision-bound
  descendant distribution grants. Successor traffic and higher-level write
  flags remain disabled.

## Implemented boundary

- Added the checksummed `higher_native_spaces_v9` migration. It rebuilds
  `knowledge_space` without the v7 blanket ban on Team/Responsibility writes
  while preserving every existing space ID and all unrelated constraints.
- Added an explicit migration rebuild capability: only a reviewed migration may
  request foreign keys off plus SQLite legacy-alter mode, and the runner performs
  schema, postcondition, FTS, and foreign-key validation before commit and again
  after reopening with foreign keys on.
- Provisioned missing native Part, Team, and Responsibility spaces for active
  organization units. Existing native space IDs remain unchanged. The fixed
  system Responsibility root uses deterministic `created_at=updated_at=0`, so
  pristine rehearsal projections are reproducible across separate bootstraps.
- Added immutable `higher_scope_role_assignment` episodes with exact role/kind,
  active descendant membership, one-open-role, generation, close-only, user
  move/disable, organization move, and write-disable fences.
- Added immutable `knowledge_distribution_grant` rows bound to the current source
  revision, the exact open source role episode, a strict descendant target, audit
  and integer outbox IDs, plus explicit revoke/break-glass authority tuples.
- Added typed domain enums and fail-closed repository DTOs/read-write primitives
  for higher roles and distribution grants. Repository page sizes are bounded to
  100 per call; this is not a product count limit.
- Updated full preview bootstrap to use the canonical `SingleMigrator`, including
  existing-preview upgrades, and to reuse native spaces by organization owner.
  Updated local validation fixtures to create native Team spaces atomically with
  their direct fixture seed.
- Reconciled projection, restore, migration-rehearsal, scope, and phase-1
  provisioning fixtures with the v9 Responsibility bootstrap space.
- No live knowledge/user/runtime root, production database, port, company API,
  provider, or model was opened or changed.

## Schema facts

- Catalog latest version: 9.
- Expected v9 schema SHA-256:
  `f6659839db6be4f167a0946e863e8f27ff9d46c6e1bc74bae73d30dbffd658b2`.
- v9 migration SHA-256:
  `1ca3f06c06ad6155bc6a1cb860f36503d302dc3ea02cacc6b1f30a75d495291b`.
- Catalog SHA-256:
  `825c467c5656301e98d0bd9a92168713ac00185c930567d1fd3d3885f4a17890`.
- Canonical v9 objects: 57 tables, 152 explicit non-SQLite indexes, and
  165 triggers.
- Frozen v1-v8 migration checksums remain byte-identical.
- Pristine and v8-to-v9 upgraded schemas have the same expected fingerprint.

## Acceptance evidence

| ID | Proof | Result |
|---|---|---|
| `HSPACE-001` | Exact v9 schema/FKs plus real Team item, revision, audit/outbox, and descendant distribution repository round-trip | PASS |
| `HSPACE-002` | v8-to-v9 identity preservation, pristine equality, rerun no-op, and injected rebuild collision rollback exactly to v8 | PASS |
| `HSPACE-003` | Role kind, active organization, descendant membership, and exact Team/Responsibility role validation | PASS |
| `HSPACE-004` | One open episode per org role, independent multi-role user, close/reopen generation change, stale-generation rejection, and close-only history | PASS |
| `HSPACE-005` | Higher write requires the exact open role; disable/move guards and descendant/revision-bound grant constraints fail closed | PASS |

## Verification

Commands used the repository virtual environment. General tests used isolated
roots under `G:\AI_Agent\codex-sbe-test-temp-019fec6f`; the Windows
directory-pinning rehearsal ran on the local C: NTFS temporary root because the
external G: volume does not provide the required immediate staging visibility.

| Selection | Result |
|---|---|
| Focused v9 migration plus catalog | 11 passed |
| Projection materializer owner file | 6 passed |
| Unit suite in ordered fail-fast/resume splits | All 1,684 collected nodes covered; final owner selections green |
| Integration + migration + operations in fail-fast/resume splits | All 120 collected nodes covered; final owner selections green |
| Full preview focused integration | 1 passed |
| Local validation initialization/reopen | 2 passed |
| Backup/restore service and CLI focused | 5 passed |
| Strict migration rehearsal owner file | 6 passed |
| Operations runtime preflight | 4 passed |
| Regression directory in a fresh process | 8 passed |
| Current source-inventory seal | 14 passed |
| Python compile check for all owned production/test modules | PASS |
| Report-schema-v2 contract audit | PASS, 0 errors, 33 sources; final input SHA `284d70c452fa1b742212d12286d5d3c7da9b7e9a2014d9b29163df2a805c9cf0` |

The first long unit run exhausted the system C: temp volume. Re-running the same
440-test tail with `TEMP/TMP/TMPDIR` on the goal-owned G: isolation root passed
with 440 tests and 5 subtests. The external G: volume then reproduced a Windows
projection staging visibility failure twice; the owning strict rehearsal passed
on local C: NTFS after the deterministic v9 root timestamp fix. No product
assertion was suppressed and no live/user data was deleted.

## Stable implementation inventory

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded to
avoid recursive documentation hashes. The 26-file stable set is 531,386 bytes;
its sorted `path NUL size NUL sha256 LF` inventory SHA-256 is
`5726e070504a1a3a9df0fa66a50415e2fe42830e8ec35dee526811d7afb18b44`.

Principal implementation hashes:

| File | SHA-256 |
|---|---|
| `knowledge_core/migrations/v009_higher_native_spaces.py` | `323acfc199e0f4251fc4617cbebf3f6f95590e29611d257efb0d72a96bbd3a5c` |
| `knowledge_core/migrations/canonical_catalog.py` | `c7730985d1401da2d2c1a356526fe71e25d2f3e7085a786d87542fdadbdf2faa` |
| `knowledge_core/migrations/runner.py` | `7e5927c37e845c9e7500474b832b38e8fcc7763d9b9e70d362a7a7a8a7d5c54f` |
| `knowledge_core/repositories/higher_spaces.py` | `1b870424fa230bf28d147ec572a3e2c4ca0a9902acec15656d9bd316343a1653` |
| `knowledge_core/legacy/rehearsal.py` | `211fe87de4f5847bcfb9b4d13e9b8d7cc5a3daa295c978a105e0ceeb66a5e9e1` |
| `tools/full_second_brain_preview.py` | `59ca1f20040ed53f7164e0021df5288db0ef5a824af4ce67b81a1bb42b048141` |
| `tools/local_second_brain_validation.py` | `98e2272d71006ddb78e8e8c1895c8842275deb108892af254cf59d526c689e10` |
| `tests/knowledge_spaces/unit/test_migration_v9_higher_native_spaces.py` | `95a71c3e36584a5e6624b7535455d7ed13456da8ef1ed7a90360f8fa1167f177` |

## Residual ownership and handoff

- `SBE-1.3` owns v10 aggregate graph, automatic-edge, and explicit-reference
  physical schema.
- `SBE-2.3` owns runtime organization-create/native-space atomicity, Team leader
  and Responsibility executive management services/APIs, CorpusClosure reads,
  distribution commands, audit/outbox composition, and rollout flags.
- `SBE-4.1` owns aggregate graph construction; this packet stores authority and
  distribution facts but does not expose aggregate graph traffic.
- `SBE-3.0` independently owns the Windows stager least-privilege/race hotfix.
- No successor traffic has been activated. Production migration, 10111 release-
  candidate activation, company-model validation, and target-server evidence
  remain later gates.
