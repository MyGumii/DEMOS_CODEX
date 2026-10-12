# SBE-4.2 evidence

Status: IMPLEMENTED_NOT_GATED

This record proves the local implementation and deterministic gates for
`SBE-4.2 - automatic semantic edges`. It does not claim the held-out provider
precision gate in `AUTOEDGE-016`, company-provider validation, browser UI,
10111 activation, or target-server operation.

## 1. Result

- Started: 2026-08-25 Asia/Seoul.
- Local implementation checked: 2026-08-25 02:52 Asia/Seoul / 2026-08-24
  17:52 UTC.
- Prerequisites: SBE-2.1, SBE-3.1, and SBE-4.1 are VERIFIED.
- User-approved baseline: forward-only v11 higher authority and immutable v12
  temporal/footer behavior remain authoritative.
- Gate conclusion: `AUTOEDGE-004..015` have deterministic local implementation
  evidence. `AUTOEDGE-016` remains unexecuted because no approved held-out
  provider corpus/run was available and the company API is explicitly deferred.
- Both automatic-edge flags remain disabled by default.

## 2. Delivered behavior

- Post-curation publication creates handler-independent semantic intents; a
  maximum-200 keyset baseline/detector/org-retarget/lifecycle-reactivation
  seeder and anti-join materializer converge to one known-handler durable job
  per eligible intent effect key.
- The worker registry contains exact local and aggregate handler versions before
  either rollout flag can become effective. First-enable preflight fails closed
  until the deterministic baseline is complete.
- One FTS candidate query is capped at 20, one-hop graph expansion is capped at
  20 from the top six lexical seeds, and deterministic pre-rank is capped at 12;
  there is no two-hop expansion or per-descendant query.
- The configured internal LLM provider chain runs only after the read snapshot
  closes. Its strict JSON adapter treats all document text as untrusted, retains
  the actual provider/model identity, and allows only candidate IDs, relation
  types, directions, grades, and evidence offsets supplied by the contract.
- Commit revalidates current source/target revisions, item/Wiki lifecycle,
  target authority generation, exact LCA, relation direction, evidence spans,
  duplicate identity, suppression, and effect key before any activation.
- Frozen normal and high-risk thresholds, audit/outbox coupling, graph
  invalidation, candidate/terminal decision records, optional edge effect, and
  durable terminal state are atomic. Retry exhaustion also writes its exact
  terminal decision before the job becomes dead.
- Exact local/home authority can suppress an edge. Organizational administrator
  correction is a separately confirmed, CAS-fenced break-glass path. Lifting a
  suppression queues durable re-evaluation and never directly reactivates.
- Automatic execution has no merge, replacement, conflict-winner, document
  curation, distribution, deactivation, purge, FTS, or Agent-visibility effect.

## 3. Boundary and no-go attestation

- All tests used disposable v12 SQLite databases and isolated roots.
- No live `knowledge/`, `user_data/`, `.runtime`, Notion DB, 10011/10111 port,
  production service, production flag, or company API was accessed or changed.
- No migration source was edited. v10 remains
  `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`,
  v11 remains
  `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`,
  and v12 remains
  `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`.
- The internal LLM adapter was verified with an injected runner carrying Codex
  route metadata. No real provider/network/model invocation is claimed here.

## 4. Stable implementation hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
to avoid recursive document-state coupling.

| File | SHA-256 |
|---|---|
| `knowledge_core/domain.py` | `aaafed87ae6b2ff52c0fe657f56a12fdf0a223179e2a387d91bd254f2018ed35` |
| `knowledge_core/feature_flags.py` | `638be8a78059832b53f00baebfb3fad6721c6eab562452eae5d246ad355d9217` |
| `knowledge_core/scope.py` | `b3fc13d40608eaa180ac7387a17240b6eb134d781ac6fdd9e84b6e891797345d` |
| `knowledge_core/worker.py` | `04542f50b98a66c4565e31a1b8a920d79efd969b371c8220b004d3041995ec27` |
| `knowledge_core/inference_worker.py` | `fb9e5e12358abf7bf3d4db260a1b354cec260c2196fb4408e6cc9e845d2cd0fa` |
| `knowledge_core/production_service.py` | `dcde523ddea3a41533651e49bb01500add97d2c8d495b92c352bdb21f1636d99` |
| `knowledge_core/semantic_edge_routes.py` | `19c9402903ed8dcda26b510b27ab06465138f406b076646e7ae062389fb2092e` |
| `knowledge_core/repositories/automatic_semantic_edges.py` | `708a498fcaf72e99defe2193754760535d048c344c117c89a1cd367ef85c58f2` |
| `knowledge_core/repositories/semantic_graph_contracts.py` | `00a10914a3fd591f84467802879ecad260f63ea45a58d4a44896cedca8617c92` |
| `knowledge_core/services/automatic_semantic_edges.py` | `e377d735cd8b42a9a996457b27539ba729b391d95c0a70a700246830691c74c4` |
| `knowledge_core/services/semantic_edge_seeding.py` | `8dd0e9cbbeedf5048b80e45000b4bd314aa543ab1a61042ec1c0ddd7b70dfef9` |
| `knowledge_core/services/semantic_edge_correction.py` | `9ad9c2622460aae60bba5c95e79bfbd53aaa3e632fe9d2dac899d1be2ca72461` |
| `knowledge_core/services/curation_worker.py` | `8ceade0d13a969b90d9b0dce0e665fd335983f004ccfe9e8afc147e34c538142` |
| `tools/runtime_preflight.py` | `09fad58313d1b80ed0baf99e90be43d37d2415c4903b0e44444f025f03b3e928` |
| `tests/knowledge_spaces/unit/test_automatic_semantic_edges.py` | `31c82a6e1d931ac4ccb91909379cb0105b2cf89dd973af93bd38c80bee9c6865` |
| `tests/knowledge_spaces/unit/test_semantic_edge_routes.py` | `6e00755e64d562be666542dc7a664414943407a6c3598cfd6d77ee8d14be5a5d` |
| `tests/knowledge_spaces/unit/test_publication_service.py` | `197d62380aed8a7b8dd405e9d4aed1b5d6c61c5af283c4ee6037232470a54a2b` |

## 5. Verification results

Environment: Windows, Python 3.14.3, SQLite 3.50.4. All pytest roots were
disposable.

```text
Focused automatic semantic-edge/service/route/schema selection:
15 passed in 30.29s

Durable kernel/job/inference/production selection:
63 passed in 67.36s

Runtime preflight/composition/operations selection:
153 passed in 48.57s

Publication plus v10/v11/v12 migration selection:
90 passed in 274.44s

Authority/lifecycle/organization regression selection:
134 passed in 184.79s

Publication v12 SQL-boundary corrections:
2 passed in 14.93s

Python compileall for knowledge_core and tools:
PASS

Report-schema-v2 contract audit:
error_count=0; requirements=41; goals=20; scenarios=284;
OpenAPI paths=69; operations=75; schemas=12

Contract-audit regression suite:
27 passed in 84.75s
```

The first combined publication/migration run exposed two stale failpoint count
assertions: v12 adds one `markdown_v2` desired-generation write per affected
target. Every newly observed SQL boundary already rolled back atomically. The
test comments/counts were corrected from 28/18 to 30/20; no runtime behavior was
changed by that correction.

## 6. Acceptance mapping

| ID | Permanent local proof | Result |
|---|---|---|
| `AUTOEDGE-004` | post-curation intent, flag-gated materializer, exact handlers, terminal-before-dead | PASS |
| `AUTOEDGE-005` | bounded allowlisted retrieval and strict candidate/vocabulary response parser | PASS |
| `AUTOEDGE-006` | exact evidence offsets/hash validation and malformed/hostile response rejection | PASS |
| `AUTOEDGE-007` | passing deterministic decision activates with no approval queue; replay converges | PASS |
| `AUTOEDGE-008` | immutable normal/high-risk thresholds and grade floors in the commit validator | PASS (local deterministic contract) |
| `AUTOEDGE-009` | provider/schema failure creates one no-edge terminal and leaves curation/search unchanged | PASS |
| `AUTOEDGE-010` | current endpoint/lifecycle/authority/LCA/suppression/effect revalidation | PASS |
| `AUTOEDGE-011` | maximum-200 seed restart/replay, anti-join materialization, detector/org race convergence | PASS |
| `AUTOEDGE-012` | exact Part correction, ordinary-admin denial, confirmed organizational break-glass, lift queue | PASS |
| `AUTOEDGE-013` | old job remains terminal across item deactivate/restore; new lifecycle vector creates one job | PASS |
| `AUTOEDGE-014` | hostile/model paths produce zero destructive/document-curation/visibility effects | PASS |
| `AUTOEDGE-015` | baseline, detector rollout, real org move, lifecycle event cursor, first-enable preflight | PASS |
| `AUTOEDGE-016` | held-out real-provider precision/false-active/authorization report | NOT RUN; external model corpus gate |

## 7. Residual ownership and handoff

- `SBE-4.2` remains `IMPLEMENTED_NOT_GATED` until `AUTOEDGE-016` supplies an
  approved held-out report with overall precision at least 0.95, high-risk
  precision at least 0.97, false-active rate at most 0.01, and unauthorized
  edges exactly zero. A Codex-bridge run may validate the provider interface;
  company-provider evidence remains a separate external handoff.
- `SBE-5.1` is independently dependency-eligible and owns authorized bounded
  hybrid retrieval, graph expansion, final-ten/no-padding, references/pins,
  citations, cache fencing, and management-wide personal-Wiki search.
- `SBE-6.2` must not claim its complete gate until SBE-4.2 precision is closed;
  it owns correction/provider-status browser surfaces, not backend authority.
- `SBE-7.1/SBE-7.2` own integrated load/security/evaluation evidence, isolated
  10111 activation, target-server operation, and company-provider handoff.
