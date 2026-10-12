# SBE-1.1 evidence

Status: VERIFIED

## Result

- Packet: `SBE-1.1 — v8 multiple-personal-Wiki schema`
- Acceptance: `WIKI-001..004`
- State: `VERIFIED`
- Completed: 2026-08-23 14:36 Asia/Seoul.
- Outcome: canonical schema v8 introduces owner-scoped personal Wiki profiles,
  removes the former one-personal-space-per-owner index, preserves all existing
  space/content identities, and retains the v7 default-space behavior while the
  successor traffic flags remain disabled.

## Implemented boundary

- Added checksumed `v008_personal_wiki_profiles` migration and canonical catalog
  entry with exact schema, index, trigger, ledger, and postcondition hashes.
- Added immutable personal-Wiki profile DTO/repository primitives with NFKC plus
  casefold name keys, owner/default/lifecycle checks, and bounded cursor reads.
- Backfilled each v7 personal space as its owner's active default named `내 지식`
  without changing the existing `knowledge_space.id` or dependent IDs/hashes.
- Updated the personal-space compatibility repository so legacy callers resolve
  only the exact active default under v8 and retain the old query under v7.
- Registered `KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA`, default false, in runtime
  parsing, operational preflight, and the production environment example. This
  schema-only flag neither exposes traffic nor enables writes.
- Reconciled v8-aware raw SQL fixtures and the current source-inventory seal.
- No live knowledge/user/runtime root, production database, port, company API,
  provider, or model was opened or changed.

## Schema facts

- Catalog latest version: 8.
- Expected v8 schema SHA-256:
  `db47b16c5ffdbfb682ee7459682cbff2876d80d5dc95e6e46617c6deef9ba55a`.
- v8 migration SHA-256:
  `3f88b2a47c5fceb375535ac41de2332fc394b673a0dea88b63f145d1f0310ccb`.
- Catalog SHA-256:
  `5ff23d6d33562c5d05714b2bdf7ec69c7512189e9cc48c30601c6a4f1e1591a9`.
- Canonical v8 objects: 55 tables, 143 explicit non-SQLite indexes, and 156
  triggers.
- Personal-Wiki indexes are exactly the active-name, active-default,
  active-name-cursor, active-recent-cursor, and archived-cursor contracts; the
  old `ux_space_personal_owner` index is absent.
- No schema, repository, service, or UI count cap was introduced. Repository
  page size is bounded to 100 solely as a per-request work bound.

## Acceptance evidence

| ID | Proof | Result |
|---|---|---|
| `WIKI-001` | Sequential v7→v8 fixture compares space, item, revision, conversation, body ID and body hashes; existing space becomes lifecycle generation 1 default | PASS |
| `WIKI-002` | Pristine and upgraded fingerprints match; rerun is a no-op; invalid upgrade rolls back completely to v7 | PASS |
| `WIKI-003` | More than ten Wikis are inserted/listed; old unique owner index is absent; exact indexes and bounded cursor query plan are asserted | PASS |
| `WIKI-004` | Owner/default/lifecycle/delete/display-key/name conflicts, unknown objects, and tampered ledger cases fail closed | PASS |

## Verification

All commands used the repository virtual environment and isolated test roots
under `G:\AI_Agent\codex-sbe-test-temp-019fec6f`.

| Selection | Result |
|---|---|
| Focused v8 tests | 5 passed |
| Catalog/v7/space/flags/preflight/operations focused regression | 162 passed |
| Unit suite, split only because the system C: temp volume filled | 1,705 distinct tests passed |
| Integration + migration + operations | 120 passed |
| Regression directory | 8 passed |
| Backup/restore service and CLI focused rerun | 5 passed |
| Python compile check for all owned production/test modules | PASS |
| Report-schema-v2 contract audit | PASS, 0 errors, 33 sources |

One projection-worker timing canary failed once in a long aggregate run with a
600 ms lease and 450 ms sleep and passed immediately in isolation. One combined
multi-directory run inherited a prior test's process-global host policy and
returned 403 from the first regression `/me`; the entire regression directory
passed in a fresh process. Neither failure reproduced in its owning selection,
and neither touches the v8 implementation. They remain suite-harness flake
observations rather than suppressed product failures.

## Stable implementation inventory

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded to
avoid recursive documentation hashes. The 27-file stable set is 558,619 bytes;
its sorted `path NUL size NUL sha256 LF` inventory SHA-256 is
`a97bfb7bd162ae6a05640dab86b9ef032f64abb20751413cc27c29c13d6fd658`.

Principal implementation hashes:

| File | SHA-256 |
|---|---|
| `knowledge_core/migrations/v008_personal_wiki_profiles.py` | `e1d23745d137753387bcd4d92bc5e4d0ead491505a4bdf457867e0d8f07f0d4a` |
| `knowledge_core/migrations/canonical_catalog.py` | `2da6c2677e671c5a358ced108aeb691dfa7a01065ce1d4267fe36151fbe75f95` |
| `knowledge_core/repositories/personal_wikis.py` | `6f3c6da80f656299912f5d190f9b9a0360b89a260a7878f417ac41f1cf250bec` |
| `knowledge_core/repositories/spaces.py` | `dc045576f44f5597b3931c5b93e238c0ff8b42a1768d8e9150c9f41426b953e6` |
| `knowledge_core/feature_flags.py` | `46ef0cdb76f21c4cf9932633b4fa222a0ec393b0267b3d89539fff7541d8597b` |
| `tools/runtime_preflight.py` | `96447bb3e50537471b0264ccc5a8a5014c3147ddabe6a9876e34ec359671c884` |
| `tests/knowledge_spaces/unit/test_migration_v8_personal_wiki_profiles.py` | `368a817d00a5497530b3557b54e40339dbc68fc529031de6b35283e5d9263006` |
| `docs/knowledge_spaces/CURRENT_SYSTEM_INVENTORY_MAP.json` | `0a4dbf0d793339bf96ac59b5ff8d88b42293d04cd60fbff09ba88327c64be402` |

The current source scanner classifies 693 callsites as 387 mapped plus 306
retained/excluded, with source fingerprint
`a03c98223da9279bddc2e4c7ea8889e376c418913f19732d55bd9f554412bf65`.
The one-callsite delta is an existing retained Notion UI-pilot readiness route,
not an SBE runtime dependency.

## Residual ownership and handoff

- `SBE-1.2` owns v9 higher-role episodes, native Team/Responsibility spaces,
  distribution grants, and their physical constraints.
- `SBE-2.1` owns personal-Wiki create/rename/default/archive/restore services,
  authorization, audit/outbox, idempotency, routes, and write/read flags.
- `SBE-3.0` independently owns the Windows staging least-privilege/race hotfix.
- No successor traffic has been activated. Production migration, 10111 preview,
  company-model validation, and target-server evidence remain later gates.
