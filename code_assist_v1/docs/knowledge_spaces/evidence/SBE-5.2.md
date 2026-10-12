# SBE-5.2 evidence

Status: IMPLEMENTED_NOT_GATED

This record proves the local deterministic implementation gates for
`SBE-5.2 - optional local cross-encoder and temporal ranking`. It does not
claim the prerequisite held-out retrieval-quality gate in `RANK-014`, browser
UI, 10111 activation, target-server operation, company-provider execution, or
production rollout.

## 1. Result

- Started and locally checked: 2026-08-25 Asia/Seoul.
- Final local check: 2026-08-25 04:05 Asia/Seoul / 2026-08-24 19:05 UTC.
- Prerequisites: SBE-3.2 is VERIFIED. SBE-5.1 runtime behavior is implemented,
  while its external held-out `RANK-014` quality gate remains open.
- User authorization: v11 authority and v12 temporal changes were approved.
  The existing immutable schemas already supplied every field and authority
  needed by this packet, so no DDL or migration edit was necessary.
- Gate conclusion: `RANK-015..021` and `TIME-020..022` have deterministic local
  implementation evidence. The packet remains `IMPLEMENTED_NOT_GATED` only
  because the prerequisite `RANK-014` corpus evaluation is not available.
- `KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER` and
  `KNOWLEDGE_CORE_TEMPORAL_RANKING` remain disabled by default.

## 2. Delivered behavior

- Two successor flags independently enable the optional local cross-encoder
  and temporal ranking, both under the existing hybrid-retrieval parent. The
  application parser, runtime composition, environment map, and operational
  preflight fail closed when the exact parent is absent.
- The cross-encoder receives one bounded payload containing no more than 40
  already-authorized candidate IDs. It can use only local GGUF or Codex bridge,
  receives no temporal/footer fields, cannot add IDs, and returns one discrete
  relevance grade from 0 through 4 for every supplied ID exactly once.
- Successful model work is cached by exact model, prompt, and input hashes.
  Timeout, OOM, unavailable model, malformed JSON, missing/duplicate grades,
  injected IDs, oversized input, or provider failure never fails an Agent turn
  and falls back to the deterministic ranked core with a typed status/reason.
- Applied grades use the frozen fusion `85% deterministic + 15% local grade`.
  The model only reorders authorized candidates; it cannot retrieve, write,
  merge, deactivate, or infer a conflict winner.
- A separate bounded v12 read loads temporal heads for the exact candidate
  item/revision/source triples in one SQL statement after the retrieval
  snapshot closes. It verifies the current temporal-summary digest and current
  verification attestation before exposing typed evidence.
- Temporal precedence is information-effective, source-modified,
  content-modified, then registered time. Current, scheduled, historical, and
  generic intent use deterministic bounded features; future facts are not
  treated as current, and current verification is a separate trust feature.
  Queries without frozen temporal intent preserve the SBE-5.1 item/rank/score
  core exactly.
- Explicit `as_of_ms` values bind the exact cache identity. Implicit requests
  bind a one-minute bucket, and every entry expires no later than the next known
  future effective boundary even when no write occurs.
- Exact-body dedupe remains content-based when provenance dates differ.
  Contradictory facts and their typed times remain available to the answer
  model; timestamp alone performs no merge, replacement, deactivation, purge,
  or winner selection.
- Search and Agent paths share the same post-transaction ranking service. No
  model call runs while a database transaction is open. Agent prompts receive
  temporal evidence only after final selection plus a bounded ranking envelope.
- Work remains fixed at 1, 100, and 1,000 personal Wikis: one bounded retrieval
  closure, one bounded temporal SQL read, at most one local model call, no
  per-Wiki query, model invocation, or worker, and at most ten final documents.

## 3. Boundary and no-go attestation

- All tests used disposable v12 SQLite databases and isolated roots.
- No live `knowledge/`, `user_data/`, `.runtime`, Notion DB, 10011/10111 port,
  production service, production flag, network, company API, or target provider
  was accessed or changed.
- Provider behavior was tested through strict in-process GGUF/Codex-compatible
  adapter doubles. Actual relevance quality remains part of the held-out
  `RANK-014`/integrated handoff rather than a correctness dependency.
- No migration source was edited. v10 remains
  `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`,
  v11 remains
  `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`,
  and v12 remains
  `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`.

## 4. Stable implementation hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
to avoid recursive document-state coupling.

| File | SHA-256 |
|---|---|
| `knowledge_core/domain.py` | `8e7ec72ee520800a2f23377cab8388816fdc37dcdee8318cac079a0557fc5adb` |
| `knowledge_core/feature_flags.py` | `425bbdaa4346d82cee889897be7186f5db7f8d8c4592645659c515d7743db36d` |
| `knowledge_core/repositories/retrieval.py` | `26a3891b5c1a4b8f27e02da6a1597e3bf468053c3aa7aa4ff82598bece8e7822` |
| `knowledge_core/repositories/hybrid_retrieval.py` | `809f018ed79a81e411fca821b72ebe4d26f8c7036a7d83183ceaf75341eb4304` |
| `knowledge_core/repositories/retrieval_temporal.py` | `f0bea6df514bc05d0c9e711eb607a005c5d341da4ec3f933cb83dd0c5fff7a0d` |
| `knowledge_core/repositories/__init__.py` | `5a78cd14d7a9c9ad78e6311a0c2f8ba7a000411cb856d4ff2c1443a48eadb7d9` |
| `knowledge_core/services/retrieval.py` | `f10e8ecdc8abd633051f3401ab78ec9d0f177cdaba563dabe8d4c23053baf573` |
| `knowledge_core/services/agent_context.py` | `490986790506569f9aa41023514c83feb4d40689e84e31ea488053a2bdaa8978` |
| `knowledge_core/services/hybrid_reranking.py` | `ea1b9cc80283df20eefcf131314db96e751a5439d3c54245c26a9f1ebab302ff` |
| `knowledge_core/services/__init__.py` | `7e1095176077b427ea9499f4bc1a0cc6ec16d6799534925066b9aea5f28d336f` |
| `knowledge_core/agent_runtime.py` | `ca2a9881b763a742c996ed1ccef8446290491c2ac580701d8695855180b17487` |
| `knowledge_core/agent_execution.py` | `0c501d752b5bf4ee2a72c2c56ebc867fb9eca5cea08203e604917b5a767afb35` |
| `knowledge_core/composition.py` | `a5c2059ca4b96ba01e4e3036b202004ea1557a11a6173df5609067563ba34c97` |
| `knowledge_core/canonical_routes.py` | `b9cfbb7115140c56494a9d946d1cd63dc95ff13e8ae15cbc0eee670f2f451401` |
| `tools/runtime_preflight.py` | `bedd07df7429ca7a486dc58942f6b5a02725a53c129358aafb73865ae7210d7a` |
| `tests/knowledge_spaces/unit/test_hybrid_reranking.py` | `1dc04eb072f9f3a97be9850159a8efd5cf6319b574e08f813b422914ddf18dd5` |
| `tests/knowledge_spaces/unit/test_knowledge_core_composition.py` | `d18007716c8ce637538162ef1f5c94b362b6b3a33bd5e3d00c98fd0e24a632f3` |
| `tests/knowledge_spaces/unit/test_runtime_preflight.py` | `3038055a7bbd1bdc363d3104d3c2d87a108c732b68445230799a0ff2015479a5` |
| `tests/knowledge_spaces/operations/test_runtime_preflight_cli.py` | `646d7a20dbbad2be6c9742d41d9e405adac21a0ec13b421fb629bad3ad6c0f12` |

## 5. Verification results

Environment: Windows, Python 3.14.3, SQLite 3.50.4. All pytest roots were
disposable.

```text
Focused SBE-5.2 reranker/temporal/Agent selection:
14 passed in 17.24s

Operational preflight selection:
94 passed in 20.74s

Final related retrieval/temporal/graph/Wiki/composition/preflight/WSGI selection:
270 passed in 168.72s

Python compileall for knowledge_core and focused tests:
PASS

Report-schema-v2 contract audit:
error_count=0; requirements=41; goals=20; scenarios=284;
OpenAPI paths=69; operations=75; schemas=12

Contract-audit regression suite:
27 passed in 82.39s
```

## 6. Acceptance mapping

| ID | Permanent local proof | Result |
|---|---|---|
| `RANK-015` | exact authorized ID set, max-40 payload, GGUF/Codex-only provider order, injected-ID rejection | PASS |
| `RANK-016` | timeout/OOM/unavailable/malformed/injected/provider failure byte-stable fallback and surviving Agent context | PASS |
| `RANK-017` | model/prompt/input digest cache and company-provider exclusion | PASS |
| `RANK-018` | frozen intent detection and exact non-temporal item/rank/score equality | PASS |
| `RANK-019` | typed precedence, future handling, verification trust, and bounded score features | PASS |
| `RANK-020` | both contradictory facts retained with typed evidence and no inferred winner | PASS |
| `RANK-021` | identical bounded SQL/model counts at 1, 100, and 1,000 Wikis | PASS |
| `TIME-020` | current/scheduled/historical fixtures, exact `as_of`, replay, and boundary expiry | PASS |
| `TIME-021` | same-body/different-date alternate identities remain one content duplicate | PASS |
| `TIME-022` | conflict corpus read leaves item/revision/edge/audit/outbox/dedup counts unchanged | PASS |

## 7. Residual ownership and handoff

- The prerequisite `RANK-014` held-out quality evaluation remains external and
  keeps SBE-5.1/SBE-5.2 at `IMPLEMENTED_NOT_GATED`. It requires an approved
  Korean/English corpus split by scope and the frozen recall, nDCG, MRR,
  citation-precision, and authorization thresholds.
- `SBE-6.1` may now consume the completed multi-Wiki lifecycle, upload,
  temporal, retrieval, and conversation-binding services for browser UI. It
  must preserve the selected Wiki/view and `as_of` identities.
- `SBE-6.2` owns higher native/inherited graph, write, reference, and correction
  UI. It cannot expose the local model as an authority or conflict-decider.
- `SBE-7.1/SBE-7.2` own integrated load/security evidence, actual held-out local
  provider quality, isolated 10111 activation, and target/company handoff.
