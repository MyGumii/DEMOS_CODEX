# SBE-1.3 evidence

Status: VERIFIED

## Result

- Packet: `SBE-1.3 - v10 aggregate/automatic-edge/reference schema`
- Acceptance: `AGRAPH-001..004`, `AUTOEDGE-001..003`, `REF-001..002`
- State: `VERIFIED`
- Completed: 2026-08-23 22:55 Asia/Seoul.
- Outcome: canonical schema v10 now contains the closed physical contracts for
  cross-space aggregate edges, validated automatic local-edge provenance,
  handler-independent semantic intents and seed runs, immutable decision and
  suppression evidence, higher graph generation vectors, and explicit stable-ID
  references. No successor traffic flag was enabled.

## Implemented boundary

- Rebuilt `knowledge_edge` forward while preserving every historical local
  edge ID and the existing DTO fields. Human manual/approved rows retain their
  actor shape; `semantic_auto` rows require a real system job, fixed current
  endpoint revisions, detector/model/prompt/scoring identity, confidence,
  span/location hashes, audit, outbox, semantic key, and effect key.
- Added the immutable six-value `semantic-rel-v1` vocabulary. Direction policy
  is closed per relation and raw UPDATE/DELETE is rejected.
- Added `aggregate_knowledge_edge` with non-personal cross-space endpoints,
  exact current Team/Responsibility LCA home, direction-aware uniqueness,
  fixed revisions, endpoint row-version fences, actor/evidence union, audit,
  outbox, and correction-only lifecycle transitions.
- Added insert-only `semantic_edge_intent` and bounded
  `semantic_edge_intent_seed_run`. The database verifies current
  active/published source identity, body hash, source/target generations,
  organization ancestry, exact item/Wiki lifecycle vector, tagged publication
  or seed provenance, monotonic cursor/count/fence transitions, immutable
  effect keys, and deterministic generation identity.
- Added immutable `semantic_edge_decision` evidence and system-job terminal
  guards, plus explicit `semantic_edge_suppression` create/lift transitions.
  Candidate relation/direction, current candidate identity, activated edge/job
  binding, terminal count/ordinal, and outcome consistency fail closed.
- Added `aggregate_generation_vector` and its nondecreasing build/home/source
  generation fences for later higher Index/Log/Graph materialization.
- Added `knowledge_explicit_reference` as a revision/ordinal-bound, closure-
  proof-bound ledger. References never create local or aggregate semantic edges;
  availability changes preserve the original target revision.
- Added domain enums and `SemanticGraphContractRepository` DTOs/bounded reads,
  exact intent replay/conflict handling, and the one canonical
  `derive_semantic_lifecycle_vector` implementation. Later services do not need
  to reconstruct this schema with ad hoc SQL.
- Updated empty migration-rehearsal validation for the exact six deterministic
  v10 relation seed rows. Updated health/catalog fixtures to follow catalog v10,
  while keeping the historical v9 test catalog pinned to v9.
- No live knowledge/user/runtime root, production database, port, company API,
  provider, or model was opened or changed.

## Schema facts

- Catalog latest version: 10.
- Expected v10 schema SHA-256:
  `db8c6abd293b30892b7b8b09c1de31ebc4c9036b532bbecb78064df901e570b5`.
- v10 migration SHA-256:
  `b433ec346f55c5fd6634001f48a28a6cba1947cc2a6967b6b36fea25d6b92943`.
- Catalog SHA-256:
  `08350618024d87534d916bb7b33eb0bc65c772d4d0aac2a8de8ba424dc5054e7`.
- v10 migration body: 74 reviewed statements and 7 read-only postconditions.
- Canonical v10 objects: 65 tables, 175 explicit non-SQLite indexes, and
  188 triggers.
- Frozen v1-v9 migration checksums and the reviewed v9 schema fingerprint remain
  byte-identical.
- Pristine v1-to-v10 and populated v9-to-v10 schemas have the same fingerprint;
  rerun is a no-op and an injected v10 failure rolls back exactly to v9.

## Acceptance evidence

| ID | Proof | Result |
|---|---|---|
| `AGRAPH-001` | Exact v10 pristine/upgrade fingerprint; aggregate actor/evidence, suppression, generation, repository mapping, index and trigger contracts | PASS |
| `AGRAPH-002` | Populated v9 manual edge preserved byte-for-byte at its historical DTO fields and remains a user actor after forward rebuild | PASS |
| `AGRAPH-003` | Exact LCA storage accepts cross-Part same-Responsibility endpoints while rejecting wrong home, personal endpoint, duplicate home, unknown/mismatched relation direction, and mutable identity | PASS |
| `AGRAPH-004` | Repeated migration is a no-op; injected statement failure leaves v9 fingerprint/version and zero v10 residue | PASS |
| `AUTOEDGE-001` | Local automatic edge requires system job plus complete revision/model/scoring/confidence/span/location/audit/outbox evidence | PASS |
| `AUTOEDGE-002` | Historical human rows remain unchanged and automatic rows use `actor_kind=system` with no fabricated user | PASS |
| `AUTOEDGE-003` | Intent exact replay/conflict, item/Wiki lifecycle vector, seed provenance/cursor/fence, relation immutability, decision terminal counts, suppression create/lift, and immutable ledgers fail closed | PASS |
| `REF-001` | Stable syntax, fixed source/target revisions, unique ordinal/effect identity, availability transition, DTO mapping, and closure-shaped target checks | PASS |
| `REF-002` | Reference insert/update leaves both local and aggregate graph row counts/identities unchanged | PASS |

## Verification

Commands used the repository virtual environment. General tests ran under the
goal-owned `G:\AI_Agent\codex-sbe-test-temp-019fec6f` isolation root. Strict
Windows projection pinning ran under local NTFS `D:\CodexTestTemp`.

| Selection | Result |
|---|---|
| Focused catalog, v9 history, v10 migration, and repository contracts | 19 passed |
| Existing semantic edge, legacy graph, backup, and production-health owners | 62 passed |
| Full unit directory in one fail-fast G: process | 1,692 passed, 5 subtests passed in 2,813.27 s |
| Integration + operations before isolated regression process | 63 passed |
| Regression directory in a fresh process | 8 passed |
| Inventory/fault/plan migration files | 51 passed |
| Strict migration rehearsal on local fixed NTFS | 6 passed |
| Python compile check for all owned production/test modules | PASS |
| Report-schema-v2 contract audit after final status close | PASS, 0 errors, 33 sources; input SHA `38e5679c8ae87aebf387177967b183833d4ec665cb6a723deebf62678c021205` |

The first full-unit attempt reached 1,566 passing tests before the C: temporary
volume returned `database or disk is full`. The same complete selection was
then rerun once under the isolated G: root and all 1,692 tests completed. A
combined integration/regression process left global security state that made
the first regression request return 403; the regression owner directory passed
all eight tests in the required fresh process. No product assertion was
suppressed, no test was weakened, and no live/user data was deleted.

## Stable implementation inventory

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded to
avoid recursive documentation hashes. The 12-file stable set is 274,808 bytes;
its sorted `path NUL size NUL sha256 LF` inventory SHA-256 is
`3d86e3e30dbf823f07532b4ef7ab0213ff34f5cbcd64fa96edfe921a21fcdd47`.

Principal implementation hashes:

| File | SHA-256 |
|---|---|
| `knowledge_core/migrations/v010_semantic_graph_contracts.py` | `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c` |
| `knowledge_core/migrations/canonical_catalog.py` | `4738f736eb027cc229f5446350200d8b1b0bb5f7a7225b65b3a47ccb372963c7` |
| `knowledge_core/domain.py` | `af996fa9f78e4b94fa087a2108b8ceb4af2819f9056e55503f38c1bcb4236ed8` |
| `knowledge_core/repositories/semantic_graph_contracts.py` | `0423e7a68f95518f4bf1713daa9e9723eb06a50430afbd82b08c54ed32b2bd7e` |
| `knowledge_core/legacy/rehearsal.py` | `38ffa8143864cd66a9c44a378761e2554f6572b4d13ceb767e60fee9fc788dd4` |
| `tests/knowledge_spaces/unit/test_migration_v10_semantic_graph_contracts.py` | `5f59082bc5cad87cd4c12b3df0d8faa1f7df47715736065dfc05e7fe8018c98d` |
| `tests/knowledge_spaces/unit/test_semantic_graph_contract_repository.py` | `d1e4c1904dbe756c7e29ffe90c5cdb681c425df354d3d66e592211296048197e` |

## Residual ownership and handoff

- `SBE-2.1` owns personal Wiki lifecycle repository/service/API behavior; v10
  only consumes its lifecycle generation proof.
- `SBE-2.3` owns `CorpusClosure`, higher authority, and runtime mutation fencing.
- `SBE-3.0` owns the Windows stager least-privilege/race hotfix.
- `SBE-3.1` owns canonical upload publication and explicit-reference parsing/
  commit; it consumes this packet's reference and intent ledgers.
- `SBE-4.1` owns aggregate LCA read/write/composition/projection behavior; it
  consumes this packet's aggregate edge and generation-vector tables.
- `SBE-4.2` owns seed/materializer/job/model/validator/correction services and
  automatic-edge feature flags; v10 registers no handler and creates no job by
  itself.
- No successor traffic has been activated. Production migration, 10111 release-
  candidate activation, company-model validation, and target-server evidence
  remain later gates.
