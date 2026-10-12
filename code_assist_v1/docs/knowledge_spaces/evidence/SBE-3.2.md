# SBE-3.2 evidence

Status: VERIFIED

This record closes `SBE-3.2 - v12 temporal provenance and projection footer`.
It closes `TIME-001..019` only. `TIME-020..022` remain owned by SBE-5.2. It
does not authorize a production migration, successor flag activation, live-root
write, target traffic, company-provider call, or UI rollout.

## 1. Result

- Started: 2026-08-24 Asia/Seoul.
- Verified: 2026-08-25 00:31 Asia/Seoul / 2026-08-24 15:31 UTC.
- Prerequisites: SBE-1.3 and SBE-3.1 are VERIFIED.
- User-approved boundary: v1-v11 remain immutable; higher authority remains v11;
  temporal provenance and footer presentation begin only in forward migration
  v12.
- Gate result: `TIME-001..019` pass, the affected 300-test migration/runtime/
  projection/production aggregate passes, and report-schema-v2 contract audit
  reports zero errors.
- Rollout result: all successor flags, including
  `KNOWLEDGE_CORE_PROJECTION_FOOTER_V2`, remain disabled by default.

## 2. Delivered boundary

- Forward-only v12 adds `knowledge_space.temporal_generation`, append-only
  assertion and verification event ledgers, separate CAS heads, and an
  append-only projection-manifest-v2 trust ledger.
- `TemporalService` implements exact tagged instant/date validation, temporal
  assert/supersede/revoke, verification attest/revoke, exact replay, bounded
  history, current-revision fencing, same-transaction audit/outbox, ancestor
  invalidation, and independent temporal-generation advancement.
- Five exact v2 routes provide read, assert, assertion revoke, attest, and
  attestation revoke without trusting client scope or role claims.
- Footer v2 is a pure UTF-8/LF presentation transform with the exact two-LF
  boundary, three required rows, zero-to-three current evidence rows, explicit
  KST offset rendering, and no wall-clock-relative labels.
- `sources/*.md` remains footer-free canonical-body projection. New immutable
  `pages/*.md`, `index.md`, `log.md`, and `manifest-v2.json` are built under the
  content/graph/temporal generation tuple.
- Safe re-import strips a footer only when canonical ledger digest, projection
  root identity, manifest bytes, body prefix, deterministic suffix, length, and
  hashes all agree. Adjacent/self-declared/tampered manifests fail closed.
- The projector can settle v1 and v2 states together when the disabled footer-v2
  flag is later enabled; no existing v1 generation is mutated.

## 3. Boundary and no-go attestation

- Tests used only disposable `IsolatedTestLayout` databases, blob roots, and
  projection roots under pytest temporary directories.
- Live `knowledge/`, `user_data/`, `.runtime`, Notion databases, 10011/10111,
  production services, and target configuration were not accessed or changed.
- No production migrator was run. No feature flag or traffic was enabled.
- No company API, Codex bridge, GGUF, network provider, embedding, reranker, or
  semantic judge was invoked.
- Completed v1-v11 migration sources and historical evidence files were not
  edited. The v11 source SHA-256 remains
  `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`.

## 4. Schema and catalog evidence

- Starting schema: v11
  `1638cc6090a60fe1680b67fe6a6394d264b2cd596156bc4062b88948ec75b90a`.
- Ending schema: v12
  `b9dc73caa11ff955be43964d1322e115847d9f07b2cb7c54b4fc76edf2f1ff20`.
- v12 migration checksum:
  `35defab930534550dd622dfd18bac0edf11711e4fb57bb5ad799a97b0c1769e9`.
- Canonical v1-v12 catalog checksum:
  `9eac96306cbd7f9338184d051b25ec07161afd8e17a34cc663ebcf77b1078fe3`.
- Physical delta: one `knowledge_space` column, five tables, six indexes, and
  fifteen integrity/append-only triggers.
- Pristine v1-to-v12 creation equals sequential v1-to-v11 plus v12 upgrade.
  Re-running the migrator is a no-op. An injected failing v12 statement restores
  the exact v11 fingerprint with zero v12 residue.
- Gregorian calendar/leap-year, tagged-value null shape, actor/evidence, CAS
  head, append-only, manifest identity, FK, and canonical postconditions pass.

## 5. Stable implementation hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
from this stable implementation hash table to avoid recursive/document-state
coupling.

| File | SHA-256 |
|---|---|
| `knowledge_core/migrations/v012_temporal_projection_footer.py` | `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2` |
| `knowledge_core/migrations/canonical_catalog.py` | `519ce2a90c5b27d1a484b608f0852af19fbba7d4fda4c236f8acc8b31c3eb5a5` |
| `knowledge_core/migrations/__init__.py` | `8e8c63e0b3c44c65f33668668505e1cb1b6fc361b94380561919f5654bfaee64` |
| `knowledge_core/domain.py` | `fbd3ffbfe74884a270e12712d0720f264218fe734be26671dd4a74ca7c272707` |
| `knowledge_core/scope.py` | `2d2c4dd7f01bc704147841a0c333bd2cdde10fedd87049e4f4846d75e1352e98` |
| `knowledge_core/repositories/temporal.py` | `d34816a06e6738faee3412b34b16be477abdc075187012d3862277757ff8b54a` |
| `knowledge_core/repositories/outbox.py` | `4729f7e50c67b0ce23c88a8e0eb9d0d34f66b47eb2b0aeb81610b0b2d2b9d0ab` |
| `knowledge_core/repositories/__init__.py` | `bb1c863e124891759e4f6be26d97cf391d834d16893aee84f8ec592d41e9c9e1` |
| `knowledge_core/services/temporal.py` | `d60d40bdb5019623fb3960135c772d84b8995541b7ca3e7464cb8e7c6053987e` |
| `knowledge_core/services/__init__.py` | `d20e372f353bf0b69d95f586f4b01547d3259b167ef7220429e93256383d3ef1` |
| `knowledge_core/temporal_routes.py` | `f4e8ec38de3c22e0009b8cfe1b8aa7703063884248b5fa8489a4472c3fcd2ac9` |
| `knowledge_core/composition.py` | `bda2e698c539d455d7f4cf17db64546816e2d41c4399d76bcbade31e552e687a` |
| `knowledge_core/routes.py` | `9ec72e2f29ae1ebd301ee8bb0317fb088560a7c75c2f0a84ff05f98fdae09356` |
| `knowledge_core/agent_runtime.py` | `5f76cb6e81a88b8e704a0f8e76a980f215385cf6a90d34073c46823c2068d834` |
| `knowledge_core/feature_flags.py` | `d301177eb0a9efb33e84bca0434266b89bd6e48e711b4b6d05093e3e0f866b5d` |
| `knowledge_core/projection/temporal.py` | `fd0496ab6e2a58d5438c784258a027136bd8d17a59e499548eff2001eb5f74ca` |
| `knowledge_core/projection/worker.py` | `952d1fd2d8d531a8083489299c430efd1fff636d6dd836d4ffacb9065beb0ae9` |
| `knowledge_core/projection/__init__.py` | `5b3dab8069399d062b2f39299f69f05a20364381b9d7abe6a3e8032b68ac7d0d` |
| `knowledge_core/production_service.py` | `d85aed9314574b095cef97857708e39e97d83ad56222ff4c8e2dbfb2d26378e0` |
| `tools/runtime_preflight.py` | `9e28bd27a818fa205f09f547c434bcdfc3f89e756c16c9cfa19874a641784c64` |
| `tools/local_second_brain_validation.py` | `73aa076285451991855f087c8e2273cb7bc1bb8bc90f8f7c4f1379937a7b7b58` |
| `tools/full_second_brain_preview.py` | `6a38f12ec38d3b511a75092efed6a3c04079c489378f9e28ba36a7974fcbada7` |

Primary focused test hashes:

| File | SHA-256 |
|---|---|
| `tests/knowledge_spaces/unit/test_migration_v12_temporal_provenance.py` | `d5069c7ab525db2197c474c91cecbdfc7c450c011e26a356c4b6d287ac715155` |
| `tests/knowledge_spaces/unit/test_temporal_service.py` | `1da7fb00f129f4277ca64bd83cc9b1366013f65e9af613bba655280ffee66395` |
| `tests/knowledge_spaces/unit/test_temporal_routes.py` | `420068c03cf8f6dee2770c58034630e0d63f42cf7cccddb76dc0c39b41f878ef` |
| `tests/knowledge_spaces/unit/test_temporal_projection.py` | `122985fba190d955c259f3c0658cfd62a9513d9b65cccc800d0e1e5bfe311d36` |
| `tests/knowledge_spaces/unit/test_projection_worker.py` | `37db4d2647e440a30256b16082b5d26b86af039cdfb947c3bcba4a1db7de836e` |
| `tests/knowledge_spaces/unit/test_scope_resolver.py` | `b0e45e61eeda86f25775c8d2b97a7f7afbcfb6552020a4399d5ade6235e36301` |
| `tests/knowledge_spaces/integration/test_scope_resolver_multiprocess.py` | `3c61e7c146320028f898d84664b07bd25b06ba2fd64cd768708d4a2f57112a1e` |

## 6. Verification commands and results

Environment: Windows, Python 3.14.3, SQLite 3.50.4; working directory
`G:\AI_Agent\DEMOS_CODEX\code_assist_v1`; pytest roots were disposable.

Focused final temporal selections:

```text
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_temporal_service.py -q -x
7 passed in 11.28s

..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_temporal_routes.py -q -x
3 passed in 4.37s

..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_temporal_projection.py -q -x
12 passed in 10.64s

..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_scope_resolver.py -q -x
23 passed in 20.68s
```

Final affected migration/runtime/projection/production aggregate:

```text
..\venv\Scripts\python.exe -m pytest \
  tests\knowledge_spaces\unit\test_migration_v12_temporal_provenance.py \
  tests\knowledge_spaces\unit\test_schema_v1_catalog.py \
  tests\knowledge_spaces\unit\test_temporal_service.py \
  tests\knowledge_spaces\unit\test_temporal_routes.py \
  tests\knowledge_spaces\unit\test_temporal_projection.py \
  tests\knowledge_spaces\unit\test_scope_resolver.py \
  tests\knowledge_spaces\unit\test_projection_materializer.py \
  tests\knowledge_spaces\unit\test_projection_worker.py \
  tests\knowledge_spaces\unit\test_knowledge_core_composition.py \
  tests\knowledge_spaces\unit\test_runtime_preflight.py \
  tests\knowledge_spaces\unit\test_production_config.py \
  tests\knowledge_spaces\unit\test_production_health.py \
  tests\knowledge_spaces\unit\test_production_service.py \
  tests\knowledge_spaces\unit\test_production_wsgi.py \
  tests\knowledge_spaces\unit\test_production_operational_scripts.py \
  tests\knowledge_spaces\integration\test_scope_resolver_multiprocess.py \
  tests\knowledge_spaces\integration\test_projection_worker_multiprocess.py \
  tests\knowledge_spaces\integration\test_production_service_multiprocess.py \
  -q -x -p no:cacheprovider
300 passed in 244.02s
```

Final TIME-003/route/projection marker-and-invariant selection after the
documentation-only scenario ownership additions:

```text
<temporal routes/projection plus lifecycle deactivate/restore, v1 projection,
and reviewed-edge exact node selection>
19 passed in 25.22s
```

Contract regression:

```text
..\venv\Scripts\python.exe -m pytest \
  tests\knowledge_spaces\unit\test_contract_audit.py -q -x -p no:cacheprovider
27 passed in 84.94s
```

One initial aggregate run reported `292 passed, 1 failed in 209.60s`. The
failure was an old v11-predecessor multiprocess scope fixture that asserted
`higher_spaces=True` must still hide all higher views. It was corrected to the
approved v11 policy: only the actor's real Responsibility ancestor is visible,
the foreign Team remains concealed, and writes remain disabled. Its isolated
rerun passed 1/1, followed by the clean 300/300 aggregate above.

## 7. Acceptance mapping

| ID | Permanent proof | Observed result |
|---|---|---|
| `TIME-001` | `test_migration_v12_temporal_provenance.py`, schema catalog tests | Pristine/upgrade/no-op/rollback and append-only physical constraints pass. |
| `TIME-002` | temporal service/routes | Required timestamps and positive revision come only from item/current revision. |
| `TIME-003` | temporal revision, lifecycle, v1 projection, reviewed-edge tests | Content edit alone advances revision; non-content lifecycle/projection/edge effects create no revision or body change. |
| `TIME-004` | tagged parser and reviewed-legacy tests | Precision/timezone/evidence are exact; no mtime/upload/effective inference occurs. |
| `TIME-005` | four golden summary hashes and attest tests | Summary shape is exact; verification is revision+summary-bound and excluded from the summary. |
| `TIME-006` | replay, stale/sequential and concurrent CAS, no-fallback tests | One concurrent winner, exact replay, full rollback, revoke leaves no older active head. |
| `TIME-007` | content/search invariant and projection tests | Footer changes no canonical body/revision/search identity; v1 source remains footer-free. |
| `TIME-008` | canonical retrieval after footer materialization | Agent/retrieval excerpts contain no footer marker or presentation row. |
| `TIME-009` | manifest-v2 materializer tests | LF prefix size/hash, rendered size/hash, revision/generation/high-water binding are exact; `built_at` absent. |
| `TIME-010` | repeated rebuild test | Identical canonical snapshot produces identical result, manifest, and page bytes. |
| `TIME-011` | trusted importer tamper suite | Only canonical-ledger/root-bound exact bytes strip; forged/adjacent/tampered input fails closed. |
| `TIME-012` | Unicode/CRLF/empty/verified blob fixtures | No truncation or raw-vs-normalized hash confusion; import returns normalized prefix only. |
| `TIME-013` | parser/footer golden tests | UTC/KST/+09:00/date/calendar/unit/future-date behavior is exact without fabricated midnight. |
| `TIME-014` | eight combinations and projection Log sequence | Required rows always render; optional rows omit wholly; every transition logs once in audit order. |
| `TIME-015` | scope resolver, v2 routes, service in-transaction resolution | Owner/Part editor/exact current higher role gets native mutation only; inherited/foreign/admin-only paths fail closed. |
| `TIME-016` | footer-v2 golden bytes | UTF-8/LF, markers, row order, two-LF boundary, escaping and final LF are frozen. |
| `TIME-017` | Notion adapter fixture | Exact 10-digit seconds converts once to milliseconds; ambiguous units fail closed. |
| `TIME-018` | reviewed legacy origin test | Exact evidence can assert source-modified only; system import cannot fabricate effective time. |
| `TIME-019` | temporal service/projection/worker tests | Each event increments only temporal generation, appends audit/outbox, invalidates current ancestors, and leaves content/FTS unchanged. |

## 8. Negative, security, and fault evidence

- Invalid calendar, timezone/unit, tagged union, UUID, JSON, authentication,
  idempotency, current-revision, summary, and expected-head shapes fail closed.
- Same operation ID with a changed request fails; exact replay returns the
  original result with no duplicate event.
- Concurrent head-zero assertions commit exactly one assertion/audit/outbox/
  generation and one loser receives a head conflict.
- Revision changes inherit neither assertion nor verification heads. Historical
  rows remain attached to the old revision.
- Manifest/root/prefix/suffix/rendered tampering is never treated as a trusted
  body boundary.
- No partial DB/file/audit/outbox effect remains after migration or service
  failure paths exercised by the packet.

## 9. Performance and bounded-work evidence

- Temporal GET history is cursor-bounded to 1..100 events.
- Head lookup, audit ordering, projection-state settlement, and manifest trust
  use indexed exact identities; no work scales with total Wiki count.
- A temporal event creates no model, OCR, embedding, reranker, or semantic job.
- The 300-test aggregate completed in 244.02 seconds on the workstation with no
  unresolved SQLite busy/locked failure. This is packet correctness evidence,
  not the deferred target-class 40-client performance gate.

## 10. Contract audit

- Final input hash:
  `a15b797832e01ca87189abde6d4108536c9429beee12c7ea4eedaec3409fb092`.
- Report schema: 2.
- Counts: 41 requirements, 20 goals, 284 scenarios, 69 OpenAPI paths, 75
  operations, 31 stable errors, 12 schemas, and 12 valid examples.
- Errors: 0.
- Exit: 0.

## 11. Residual ownership and handoff

| Residual | Owner | Reason |
|---|---|---|
| Temporal-intent ranking, `as_of` expiry, duplicate/conflict ranking semantics (`TIME-020..022`) | SBE-5.2 | Requires final-ten retrieval and local reranker boundary. |
| Nonzero aggregate graph generation/vector in manifest v2 | SBE-4.1 | Aggregate graph is the next independent packet. |
| Footer/browser styling and upload UI | SBE-6.1 | Backend/projection bytes are complete; UI packet remains gated on retrieval. |
| Full fault/security/load/browser gate and flag activation | SBE-7.1/SBE-7.2 | Must measure the complete successor release candidate. |
| Company model/target-server evidence | SBE-7.2 handoff | External authority and environment remain unavailable here. |

The next eligible packet is **SBE-4.1**. It must read this evidence, preserve
the v12 source/catalog hashes above, keep graph generation independently fenced,
and leave every successor flag disabled until its own gate is verified.
