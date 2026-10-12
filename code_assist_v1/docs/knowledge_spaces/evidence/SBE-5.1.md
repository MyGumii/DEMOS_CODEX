# SBE-5.1 evidence

Status: IMPLEMENTED_NOT_GATED

This record proves the local deterministic implementation gates for
`SBE-5.1 - bounded deterministic hybrid retrieval`. It does not claim the
held-out corpus quality gate in `RANK-014`, optional local cross-encoder or
temporal fusion owned by SBE-5.2, browser UI, 10111 activation, target-server
operation, or company-provider validation.

## 1. Result

- Started: 2026-08-25 Asia/Seoul.
- Local implementation checked: 2026-08-25 03:34 Asia/Seoul / 2026-08-24
  18:34 UTC.
- Prerequisites: SBE-2.2, SBE-2.3, SBE-3.1, and SBE-4.1 are VERIFIED.
- User-approved baseline: forward-only v11 higher authority and immutable v12
  temporal/footer behavior remain authoritative. This packet required no DDL.
- Gate conclusion: `RANK-001..013` and `REF-006` have deterministic local
  implementation evidence. `RANK-014` remains unexecuted because no approved
  Korean/English, per-scope held-out corpus and relevance/citation judgments
  were available.
- `KNOWLEDGE_CORE_HYBRID_RERANK` remains disabled by default.

## 2. Delivered behavior

- Retrieval accepts only one server-resolved immutable `CorpusClosure`; a
  personal selection searches exactly that Wiki, while Part, Team, and
  Responsibility views preserve the real source space and inherited/overlay
  origin. No client-provided space list can enlarge the closure.
- One authorized FTS query is capped at 40. One-hop work uses at most 12 seeds,
  four neighbors per seed, and 40 additions. Conditional two-hop work uses at
  most five anchors and 20 additions. The pre-rank union is capped at 40 and
  the final relevant unique result lane at 10 with no padding.
- Local Part edges and LCA aggregate edges enforce active/current endpoints.
  Responsibility retrieval includes authorized descendant Team-home edges;
  Part graphs remain isolated. Cycle revisits cannot inflate score or
  provenance, bounded hub degree is penalized, and hop decay is deterministic.
- Exact-content dedupe retains alternate item/revision/source identities.
  Aggregate fairness reserves relevant source representation without forcing
  weak or irrelevant documents. `contradicts` is labeled as conflicting
  evidence rather than supporting context.
- Deterministic ranking uses lexical rank, title/tag coverage, graph hop/type,
  confidence, scope affinity, bounded hub penalty, and stable identity ties.
  Korean and English FTS input, source-set order, repository restart, equal
  ordering inputs, and repeated calls are deterministic.
- The cache identity binds normalized query, closure hash, pipeline version,
  every authorized source content/row/organization/temporal generation, and
  every applicable local/aggregate graph generation and source vector. Drift
  invalidates before reuse; cached ranked output is identical.
- Agent prompts receive only the bounded ranked lane plus a separately labeled
  maximum-four mandatory explicit-context/pin/stable-reference lane. Combined
  excerpt text is at most 32 KiB, UTF-8 clipping is deterministic and disclosed,
  references are nonrecursive, and stored target revisions never silently
  retarget to a newer revision.
- The flag-enabled v1 search response preserves compatibility `items` and adds
  an explicit `ranked_documents[]` core containing rank, score, source/revision,
  graph provenance, relation semantics, alternate identities, and trust label.
- `POST /api/knowledge/v2/personal-wikis/search` is a separate owner-only,
  active-profile-only, maximum-ten management query with a signed
  owner/query/offset cursor. It returns real source/revision identity, never
  reports archived counts, and has no Agent-context interface or prompt path.
- Runtime composition shares one retrieval repository/cache between search and
  Agent context but keeps management-wide personal-Wiki search separate.
  Preflight fails closed unless canonical core and personal reads are enabled.

## 3. Boundary and no-go attestation

- All tests used disposable v12 SQLite databases and isolated roots.
- No live `knowledge/`, `user_data/`, `.runtime`, Notion DB, 10011/10111 port,
  production service, production flag, provider, model, network, or company API
  was accessed or changed.
- No migration source was edited. v10 remains
  `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`,
  v11 remains
  `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`,
  and v12 remains
  `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`.
- There is no model work in SBE-5.1. Optional local reranking and temporal
  ranking remain owned by SBE-5.2; deterministic retrieval is correct without
  either and company API access is not a correctness prerequisite.

## 4. Stable implementation hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
to avoid recursive document-state coupling.

| File | SHA-256 |
|---|---|
| `knowledge_core/domain.py` | `cdf924ee4832536b1e151aaacef9fa49efc1336e9ddf3b65e4ebcd3797e4380e` |
| `knowledge_core/feature_flags.py` | `d9c4551ece6a774876adc322e481189078907471d30d0427531c3d396ec15870` |
| `knowledge_core/repositories/retrieval.py` | `9b9ea9991db1ea698ea6ade4d2de98617cf0e787f9c0ab47da549282322ce0fd` |
| `knowledge_core/repositories/hybrid_retrieval.py` | `6a77f6a62da31618ae3b6139e7d5fba3ada113ec6f75ef11869eb7086a54d994` |
| `knowledge_core/repositories/__init__.py` | `6d7ede71746e2494ea1b55b30700c18f123b2c485a8986df9cf0b5465ac1aae2` |
| `knowledge_core/services/retrieval.py` | `06320eaf443b9b997c90d444af52746fa91c84605f03205f4fd44f2454e9ca8d` |
| `knowledge_core/services/agent_context.py` | `de78eb4a870a64843c766aedaab377c2f2da08bdd9d10b515b658ee26fc76fec` |
| `knowledge_core/services/personal_wiki_search.py` | `b6cb28ab9071749685e2055ac23871430e64cef08fee0cd684bdd75389d6acb9` |
| `knowledge_core/services/__init__.py` | `0b3fe75ecbda7b74f263c5ef738b3426f33067e827bb232459476f405b5a8bd0` |
| `knowledge_core/agent_runtime.py` | `5c90ee78ae94437a5cd9c2f9a3de3b88b44cd089a716c73872ad1b2ad8fa7925` |
| `knowledge_core/agent_execution.py` | `cee9ea31394ee041dc0ca273979d93fc1fa3288ace7eaae493638c1161801f4b` |
| `knowledge_core/composition.py` | `22f1f745fcca8637d6ac598992b1bc1c931137b2c4d02c59311f6035bf415b5e` |
| `knowledge_core/canonical_routes.py` | `5600c4fdd3652004fe3985091a71134e21e8513588d81f8a8d2eac80bf7c2315` |
| `knowledge_core/personal_wiki_routes.py` | `cc1055b5efe7c68b5d95a601709c154fb437ff7ffe4f37711f4addd6068708d0` |
| `knowledge_core/routes.py` | `895ce81c6082558f8e000b8a5b7eb85b25acbb661a2e2eb7f9b98c568ae76659` |
| `tools/runtime_preflight.py` | `3c60a195a0b45dc7570cba5eca1bf0f07db43437e6815681a7e1c3aa2f2bc8b1` |
| `tests/knowledge_spaces/unit/test_hybrid_retrieval.py` | `0e729c411096b8ccd56f3ce97f5d4f6d7235e83cfea49891ebadd32881087065` |
| `tests/knowledge_spaces/unit/test_personal_wiki_routes.py` | `e86dd58363bbdad05ecf00be75e386ced97fa58c28ab69552a7dfb6c76d94fb9` |
| `tests/knowledge_spaces/unit/test_knowledge_core_composition.py` | `e673909ec85514b15e48b7deab00bad2fb8695ee514bd58a8383cd1c46383158` |
| `tests/knowledge_spaces/operations/test_runtime_preflight_cli.py` | `b5dddde365e4ad8a43b34cb7e645b33a4dcc491511417fd5f096972ce2fa7766` |

## 5. Verification results

Environment: Windows, Python 3.14.3, SQLite 3.50.4. All pytest roots were
disposable.

```text
Focused SBE-5.1 retrieval/management/context selection:
13 passed in 15.88s

Hybrid plus canonical retrieval regression selection:
38 passed in 48.65s

Final related retrieval/graph/Wiki/composition/preflight/WSGI selection:
227 passed in 124.23s

Python compileall for knowledge_core and tools:
PASS

Report-schema-v2 contract audit:
error_count=0; requirements=41; goals=20; scenarios=284;
OpenAPI paths=69; operations=75; schemas=12

Contract-audit regression suite:
27 passed in 83.60s
```

The new functional tests exposed and closed two previously untested boundary
defects: personal Wiki cache vectors incorrectly required an organization row,
and nullable aggregate-edge confidence was rejected instead of receiving the
frozen deterministic default. The fixes are read-path-only and required no
schema or migration change.

## 6. Acceptance mapping

| ID | Permanent local proof | Result |
|---|---|---|
| `RANK-001` | immutable resolver closure, exact source/overlay filter, foreign/personal/provisional canaries | PASS |
| `RANK-002` | exact personal-Wiki retrieval plus separate owner-active bounded management search/cursor | PASS |
| `RANK-003` | Part/Team/Responsibility closure provenance, inherited Team-home expansion, fixed SQL count | PASS |
| `RANK-004` | direct cap 40, stable ties, quoted bounded FTS, explicit Korean and English cases | PASS |
| `RANK-005` | 12 seeds, four per seed, 40 additions, active/current edge checks, cycle/hub controls | PASS |
| `RANK-006` | sufficiency-gated two-hop, five anchors, decay, 20-addition cap | PASS |
| `RANK-007` | union/pre-rank cap 40 and exact-body collapse with alternate identities | PASS |
| `RANK-008` | relevant unique final-ten maximum with no padding and explicit ranked lane | PASS |
| `RANK-009` | separate mandatory inputs, max four, combined 32-KiB UTF-8-safe disclosed budget | PASS |
| `RANK-010` | contradiction preserved as relevant conflicting evidence in ranked/prompt metadata | PASS |
| `RANK-011` | stable source-set order, restart/replay, cycle handling, and identity tie-breaks | PASS |
| `RANK-012` | closure/content/row/org/graph/temporal/pipeline cache fence and invalidation | PASS |
| `RANK-013` | relevant multi-source aggregate fairness without irrelevant padding | PASS |
| `RANK-014` | held-out Recall@10/nDCG@10/MRR/citation/authorization report by language and scope | NOT RUN; external evaluation corpus gate |
| `REF-006` | bounded nonrecursive stable-reference context, exact stored revision, authorization and truncation | PASS |

## 7. Residual ownership and handoff

- `SBE-5.1` remains `IMPLEMENTED_NOT_GATED` until an approved held-out corpus
  proves Recall@10 at least 0.90, nDCG@10 at least 0.85, MRR at least 0.85,
  citation precision at least 0.95, and unauthorized citations exactly zero,
  split by Korean/English and every scope kind.
- `SBE-5.2` may build locally on this bounded authorized set. It owns the
  optional local cross-encoder adapter, exact deterministic fallback, temporal
  intent/fusion, and their cache/evaluation behavior; it must not require the
  company API for correctness.
- `SBE-6.1` owns browser multi-Wiki/archive/upload/reference surfaces, while
  `SBE-6.2` owns higher native/inherited graph/write/correction surfaces. Neither
  packet may weaken this packet's closure, prompt-budget, or origin contracts.
- `SBE-7.1/SBE-7.2` own integrated held-out/load/security evidence, isolated
  10111 activation, target-server operation, and company-provider handoff.
