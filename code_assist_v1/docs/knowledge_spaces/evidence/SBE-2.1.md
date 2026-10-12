# SBE-2.1 evidence

Status: VERIFIED

## 1. Result

- Packet: `SBE-2.1 - Wiki lifecycle repository/service/API`.
- State: `VERIFIED`.
- Acceptance: `WIKI-005..012`.
- Started: 2026-08-23 Asia/Seoul; completed: 2026-08-24 08:23
  Asia/Seoul.
- Outcome: an authenticated owner can list bounded active or separately archived
  personal Wiki profiles, create any number of stable Wikis, rename, choose the
  exact default, archive, and restore through repository/service/API boundaries.
  Every mutation uses row-version CAS, operation idempotency, audit, and outbox
  in one `BEGIN IMMEDIATE` transaction.
- Gate conclusion: focused, cross-boundary, full-unit sharded, integration,
  operations, regression, compile, migration, and contract gates pass. Both new
  runtime flags remain disabled.

## 2. Boundary and no-go attestation

- Owned scope: personal-Wiki profile repository/service/routes/tests; lifecycle
  generation and canonical outbox payload; multi-Wiki read/write flags,
  composition, runtime/preflight, and exact API schemas.
- Explicitly out of scope: cross-Wiki content search, conversation-Wiki binding,
  higher-scope authority, upload/edit consumer changes, semantic restore-event
  consumption/seeding, UI, and projections.
- Live `knowledge/`, `user_data/`, `.runtime`, and Notion DB access/change: none.
- Production DB/migration/flags/services/ports change: none. No migration was
  added; canonical schema remains v10. No service or port was started.
- Company API/network/model call: none.
- Unrelated user changes: preserved. This workspace has no functional Git
  worktree, so verification used explicit owned-file inventories and hashes.

## 3. Prerequisites

| Prerequisite | State checked | Result |
|---|---|---|
| `SBE-0.1` contract transition | `evidence/SBE-0.1.md`, VERIFIED | PASS |
| `SBE-1.1` v8 multi-Wiki schema/profile/default compatibility | `evidence/SBE-1.1.md`, VERIFIED | PASS |
| Canonical catalog v10 | expected schema SHA-256 `db8c6abd293b30892b7b8b09c1de31ebc4c9036b532bbecb78064df901e570b5` | PASS |

## 4. Implemented contract

- Stable Wiki IDs are server-derived UUID5 identities from owner and operation
  ID. A request cannot provide or redirect an owner/space identity.
- Active list order is `(name_key, space_id)`; archived management order is
  `(archived_at DESC, space_id)`. Both use one `limit+1` query with limit 50 by
  default and 100 maximum. Optional profile-name search is normalized prefix
  search, not content search.
- Opaque HMAC-SHA256 cursors bind version, owner, lifecycle, normalized filter,
  ordering anchor, and anchor row version. Tampering, owner/filter/lifecycle
  reuse, or a changed anchor fails closed as `VALIDATION_ERROR`.
- Rename changes profile display/key metadata and row version only. Set-default
  clears and selects inside one transaction and verifies exactly one active
  default. Current-default/last-active archive is rejected.
- Archive is reversible profile state, never delete/purge. Restore reuses the
  same space/item/revision/conversation/edge identity, increments exactly one
  dedicated lifecycle generation, and remains non-default unless
  `make_default=true` is applied atomically.
- Canonical outbox events are `personal_wiki.created`, `.renamed`,
  `.default_changed`, `.archived`, and `.restored`. Restore payload includes the
  audit ID, owner/space IDs, profile row version, and lifecycle generation for
  the later `SBE-4.2` consumer.
- `GET/POST /api/knowledge/v2/personal-wikis`, `PATCH /{space_id}`,
  `PUT /{space_id}/default`, `POST /{space_id}/archive`, and
  `POST /{space_id}/restore` are registered behind exact flags.
  `/personal-wikis/search` remains unregistered for `SBE-5.1`.
- `SpaceRepository.get_personal()` remains the documented legacy compatibility
  boundary and resolves only the exact active default under v8+.

## 5. Changed files and stable hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
from the stable implementation inventory to avoid recursive documentation
hashes. The 22-file inventory is 419,253 bytes. Its sorted
`path NUL size NUL sha256 LF` SHA-256 is
`b8d06a73d0c7cc6f35645c4dc365ef9bf2e66dba149b882c341e94267a048d22`.

| File | Purpose | SHA-256 |
|---|---|---|
| `knowledge_core/repositories/personal_wikis.py` | Bounded profile reads and lifecycle CAS primitives | `f8a2be0c38deae0c0660d8e0e3c3a48117e33f1b78f87c5f1dd6f280ab3d151a` |
| `knowledge_core/services/personal_wikis.py` | Owner authority, signed cursors, transactions, replay, audit/outbox | `12896ef58e357e7d93b3838d65b910347ffb1c99a1791815ae335203c36f2858` |
| `knowledge_core/services/__init__.py` | Service package export | `f1b3bea6459a51df885c9a8d29aa624e7d40a6a5256578cc684aafd394db5425` |
| `knowledge_core/personal_wiki_routes.py` | Strict v2 HTTP adapter | `d7f7676388e7111856d973c94f9dcf865a4f85efb017fc0ebd89956ed3b25292` |
| `knowledge_core/composition.py` | Personal-Wiki service registry slot | `ac3de0f175f92232ef3b05aa3597641970ffb63bacf2e5dd9c00df9f1555274c` |
| `knowledge_core/routes.py` | Flag-gated v2 route composition | `4f01784d3ac9aefb33391b18492d4ef7a65ad084fafaae0f397898f3efe39712` |
| `knowledge_core/domain.py` | Multi-Wiki feature snapshot fields | `42c7c1e8e0d1ffcc3ad6dd30528ded27329b8bbdee2efdc19a4c011c4840b0f5` |
| `knowledge_core/feature_flags.py` | Exact parser/prerequisite DAG | `f25f95a18f1ccab67d1146f5eda8a023ff894abf9c848fc55654cc861d9b8d1b` |
| `knowledge_core/agent_runtime.py` | Side-effect-free lifecycle service binding | `aaaf58136497197b4ee2f9f54d1ad0f1799329dce592eda169068048e10ae653` |
| `knowledge_core/production_service.py` | Domain-separated cursor-key derivation | `1c72bcc33afcbfd0c602e9c95563342834b44203b9bb01fd6449611650a3d753` |
| `tools/runtime_preflight.py` | Requested/effective flag reporting | `5729ce934637710075a3300bbe77d4bc27036b42544be96024cd21756e52afce` |
| `ops/production.env.example` | Disabled flag defaults | `5f4af7fe5504a91d0efcff93dc5be69f2f6e7cc7dbb2a79477b71287b853cd25` |
| `docs/knowledge_spaces/contracts/schemas/second-brain.schema.json` | Exact profile/request/page schemas | `c8f3eeffcb4228fbabf32d40863a71fcd7a19505d0d5a7fbb20839d4820f6d0b` |
| `docs/knowledge_spaces/contracts/openapi.json` | Exact five-write/list API contract | `08ba6efacaf2cdb8c3bd693624d4d53b872619ec5e8ea7c1929b91c801b835b8` |
| `tests/knowledge_spaces/unit/test_knowledge_core_composition.py` | Flag DAG and route composition regression | `49fd12735f83d58a39f9a43a2b97bc43a8aefda07304a1123c0af5c9f63f0bed` |
| `tests/knowledge_spaces/unit/test_runtime_preflight.py` | Preflight flag inventory | `1b3fae6ab01e305803dcd435ec87deb1c2667090410956f88725634c0c6ee369` |
| `tests/knowledge_spaces/unit/test_production_operational_scripts.py` | Disabled production example contract | `f82ad1ba6f36497af57cb634ff943fa927530a66f9cff866b6198306559bfe86` |
| `tests/knowledge_spaces/unit/test_production_service.py` | Production cursor secret/runtime binding | `714940d65c9258da504de3840b5d8ba98eb62a6d9d9f299a97eecc36caac6a4a` |
| `tests/knowledge_spaces/unit/test_personal_wiki_lifecycle_service.py` | WIKI-005..012 service/fault/identity proof | `0d5ca2c55e8bfa6a270816389b660029a6d8ecd3a9a3a121aae93657ba5260f1` |
| `tests/knowledge_spaces/unit/test_personal_wiki_routes.py` | Strict route and flag proof | `eb3020064b75430d535f485dc6c4db77b8a401c62a6d1240948012f24d079bb6` |
| `tests/knowledge_spaces/integration/test_personal_wiki_lifecycle_multiprocess.py` | Real Windows CAS races | `422de581de3fafaee6665b59822cb6316996f1993cda66d88a9d3b2c25867f01` |
| `tests/knowledge_spaces/integration/test_personal_wiki_http_integration.py` | Real Flask-service-SQLite lifecycle flow | `e878654f1a6a85bbd0a30376a372a03569334ad41e00186593470c362a647283` |

## 6. Commands, environment, and results

- Working directory: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`.
- Runtime: CPython 3.14.3, SQLite 3.50.4, Windows 10.0.19045 AMD64.
- Effective test temp: `TEMP=TMP=TMPDIR=D:\CodexTestTemp`.
- Feature flags: all production example values remain `0`; no runtime flag was
  enabled.

| Selection | Result | Duration |
|---|---:|---:|
| Final focused lifecycle + route units | 9 passed, later lifecycle-only expansion 7 passed | 18.57 s + 18.25 s |
| Real HTTP lifecycle + eight-process CAS races | 2 passed | 11.58 s |
| Default compatibility/scope/retrieval/agent/production/v8-v10 cross-regression | 303 passed | 236.82 s |
| Contract/schema/v8-v10 focused gate | 49 passed | 100.32 s |
| Production/flag/preflight focused gate | 144 passed; added production binding case 1 passed | 44.88 s + 1.35 s |
| Full 103-file unit directory, three exact name-index shards | 1,708 passed, 5 subtests passed | 637.06 s + 508.49 s + 963.80 s |
| Full integration + operations, fresh process | 65 passed | 156.43 s |
| Full regression, fresh process | 8 passed | 12.09 s |
| Owned Python module compilation | PASS | 0.97 s |

Exact final full-unit shard form was:

```text
$sbe21AllFiles=@(Get-ChildItem tests\knowledge_spaces\unit -Filter test_*.py -File | Sort-Object Name)
$sbe21ShardFiles=@(for($sbe21Index=0;...){if(($sbe21Index % 3) -eq <0|1|2>){...}})
..\venv\Scripts\python.exe -E -m pytest -q -x -p no:cacheprovider @sbe21ShardFiles
```

Raw retry facts:

- The former G: test-temp child stopped inside Windows `TemporaryDirectory`
  creation. No product test ran there; exact test processes were stopped and the
  unchanged code passed under the pre-existing verified D: test root. That G:
  directory was not deleted or modified.
- The first HTTP integration assertion expected 404 for the intentionally
  unregistered `/search`; Flask correctly returned 405 because `/<space_id>` is
  a method-mismatched dynamic candidate. The test was corrected to assert no
  exact route plus the actual 405; the full lifecycle path had already passed.
- A full-unit attempt with `-p no:debugging` reached 1,593 passes, then an
  existing unittest safety case could not read pytest's removed `trace` option.
  No test/product code was changed for this invocation error.
- The official long single-process retry reached 29% and the workstation raised
  a native Windows access violation in SQLite fetch. The exact stopped test then
  passed alone. Three official-option shards covering all 103 files completed
  green; this matches the already documented workstation native-module risk.

## 7. Acceptance mapping

| ID | Test/evidence | Observed |
|---|---|---|
| `WIKI-005` | `test_wiki_005_more_than_ten_create_with_exact_replay_and_no_owner_input`; all-mutation replay test; HTTP integration | 11 new Wikis plus default, deterministic independent IDs, exact audit/outbox, replay no effects, drift rejected |
| `WIKI-006` | signed cursor/list service and route tests | owner/filter/lifecycle/anchor bound, stale/tampered rejection, stable active/archived ordering, default 50/max 100, no content-search route |
| `WIKI-007` | owner/CAS/identity test | foreign and admin-role-only attempts have zero effects; real item/revision/conversation/edge tuples remain byte-value identical across rename |
| `WIKI-008` | owner/CAS test and eight-process race | one exact default; stale/foreign/admin/drift rejected; one concurrent winner and one audit/outbox |
| `WIKI-009` | identity and HTTP active/archived tests | explicit owner archive preserves IDs/content and leaves active list; no delete/purge path |
| `WIKI-010` | owner lifecycle test | current default/valid last active archive rejected; non-default archive succeeds after another default exists |
| `WIKI-011` | identity, event, replay, HTTP, and process-race tests | same identity, generation increments 1 per archive/restore, generation-bound event, optional atomic default only |
| `WIKI-012` | scheduler/age-surface static test | no scheduler or age archive API; Wiki lifecycle remains distinct from item/conversation state |

## 8. Negative, security, and fault evidence

- Principal identity comes only from the resolver; request bodies have no owner
  field. Foreign and administrator-role-only rename/default/archive/restore are
  concealed as `NOT_FOUND` with zero domain, dedup, audit, or outbox effects.
- Stale account epoch, row-version CAS, normalized name collisions, controls,
  duplicate JSON members, oversized/unknown shapes, cursor tamper/reuse/staleness,
  current-default archive, last-active archive, and operation drift fail closed.
- Failpoints after domain, audit, outbox, and before commit roll back space,
  profile, dedup, audit, and outbox together; the same operation can then retry
  exactly once.
- Eight real Windows spawn contenders produce one set-default, one archive, and
  one restore winner. Each phase records exactly one canonical audit/outbox.
- Request metadata is allowlisted and rejects secret/token/cookie/CSRF fragments.
  JSON is 16 KiB bounded; cursor is 4,096 characters bounded.
- Source-write/live-root diff: not applicable to this DB-only packet; no live
  root was opened. Company/provider/model malformed/timeout fallback is out of
  scope because this packet performs no model or network work.

## 9. Schema/data/projection evidence

- Starting and ending physical schema: canonical v10; expected SHA-256
  `db8c6abd293b30892b7b8b09c1de31ebc4c9036b532bbecb78064df901e570b5`.
- No DDL/catalog change. Pristine/sequential/failure/no-op schema behavior remains
  covered by the 49-test v8-v10 focused gate and the full unit set.
- Archive/restore test fixtures contain two items, two revision-1 rows/bodies,
  one conversation binding, and one local edge. Exact IDs, revision numbers,
  binding fields, endpoints, and row versions are unchanged after restore.
- Projections: not implemented or touched; `SBE-3.2`, `SBE-4.1`, and UI packets
  own later materialization.

## 10. Performance and bounded-work evidence

- Owner fixture: more than ten active Wikis plus separate archived pages; no
  product-level count cap exists. The 100/1,000-Wiki load gate remains `SBE-7.1`.
- List uses one indexed `limit+1` query, plus one indexed anchor lookup only when
  continuing a cursor. There is no Wiki-per-query loop or full-materialization
  endpoint. Page maximum is 100.
- Mutations use a constant transaction/query shape independent of owner Wiki
  count. Concurrency proof used eight real processes against one target.
- Bounds: display 120 characters, normalized key 160, archive reason 1,000,
  JSON 16 KiB, cursor 4,096, page 100. Model/token counts are zero.
- Qualification: workstation/local NTFS deterministic correctness only; not
  target-server 40-client performance evidence.

## 11. Contract audit

- Final input hash: `e42924c7d5d305dad8b483b76909eb86398c720626e90c99f37e60f682d49bc4`.
- Expected registered counts: 41 requirements, 20 goals, 284 scenarios, 69
  OpenAPI paths, 75 operations, 31 stable errors, and 13 successor flags.
- Source files: 33.
- Errors: 0; report status `pass` under report schema v2.
- Exit: 0.

## 12. Residual ownership

| Residual/risk | Severity | Owner/packet | Reason |
|---|---|---|---|
| Conversation creation/session history must bind exact Wiki | High | `SBE-2.2` | Explicitly outside lifecycle packet |
| Higher `CorpusClosure` and Team/Responsibility authority | High | `SBE-2.3` | Required before conversation/retrieval consumers |
| Upload/edit/auto-job active-Wiki enforcement | High | `SBE-3.1`, `SBE-4.2` | Consumers do not yet exist in successor form |
| Restore-event semantic reactivation consumer | Medium | `SBE-4.2` | This packet emits only the generation-bound event |
| Cross-Wiki content search and Agent retrieval | High | `SBE-5.1` | `/search` deliberately unregistered |
| Multi-Wiki/archive UI and 10111 activation | Medium | `SBE-6.1`, `SBE-7.2` | Backend packet starts no preview |
| 1,000-Wiki/40-client/target-server scale | High | `SBE-7.1`, rebaselined P7/P8 | External later gate |
| Long single-process native Windows instability | Medium | workstation operations / target gate | Exact case and complete three-shard suite are green; not a product assertion failure |

## 13. Handoff

- Exact next critical packet: `SBE-2.3` (`CorpusClosure` and higher authority).
  Independent `SBE-3.0` is also prerequisite-eligible.
- Required reading: `second_brain_evolution/EXECUTION_PLAN.md`,
  `ACCEPTANCE.md`, `FORWARD_SCHEMA.md`, `evidence/SBE-1.2.md`, and this file.
- Frozen interfaces: `PersonalWikiProfileRecord`, the five command DTOs,
  `ListPersonalWikisQuery`, `PersonalWikiMutationResult`, exact v2 paths, exact
  event types, and lifecycle-generation payload fields.
- Hot shared files: `domain.py`, `feature_flags.py`, `composition.py`,
  `routes.py`, OpenAPI, and second-brain schemas must remain single-writer.
- Next Agent first command: run focused SBE-2.3 v9/authority tests under an
  isolated root, then the report-schema-v2 contract audit.
- External state still required later: company-model compatibility/latency,
  target-server service account/40-client load, backup/restore, pilot, and
  production authorization.
