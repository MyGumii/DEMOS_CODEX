# SBE-4.1 evidence

Status: VERIFIED

This record closes `SBE-4.1 - LCA aggregate graph` and `AGRAPH-005..014`.
Automatic model judging/correction (`SBE-4.2`), ranking, browser UI, integrated
load/security gates, 10111 activation, and company-provider validation remain
later packets.

## 1. Result

- Started: 2026-08-25 Asia/Seoul.
- Verified: 2026-08-25 01:32 Asia/Seoul / 2026-08-24 16:32 UTC.
- Prerequisites: SBE-1.3 and SBE-2.3 are VERIFIED.
- User-approved boundary: immutable v11 higher authority and immutable v12
  temporal/footer behavior remain the authority/schema baseline.
- Gate result: exact LCA placement, Part isolation, Team/Responsibility
  composition, current-endpoint fencing, ancestor-only invalidation,
  topology-aware generation identity, and graph-only projection pass.
- Rollout result: `KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS` is implemented but
  remains disabled by default in production and both preview launchers.

## 2. Delivered behavior

- `AggregateGraphRepository` computes the current exact Team/Responsibility LCA,
  rejects personal/inactive/no-LCA endpoints, fences both current published
  revisions and item row versions, and stores one canonical edge at its home.
- `AggregateGraphService` owns authentication, `AGGREGATE_EDGE_CORRECT`
  authorization, exact operation replay, audit, outbox, edge effect, home
  generation, and strict-ancestor invalidation in one transaction. Same-space
  requests are sent back to the existing local-edge ledger.
- Effective graph reads preserve every node/edge physical source space,
  native/inherited state, direction, home, status, confidence, and evidence.
  Part reads never include aggregate edges. Team reads include only their own
  Team-home edges; Responsibility reads additionally include descendant
  Team-home and Responsibility-home edges.
- Every aggregate read recomputes the stored edge home against the current
  organization tree. Revision, lifecycle, curation, row-version, or org-home
  drift hides the edge before the next read or Agent context.
- The source vector binds sorted physical source IDs plus content, local/child
  graph, temporal, and per-source organization row versions. A descendant Team
  home-edge generation contributes to its Responsibility vector while the
  selected space's own home-edge counter remains independently fenced.
- Ordinary mutation latency walks only the current ancestor chain. It never
  captures a descendant vector. Exact outbox replay does not advance an
  aggregate generation twice. Org move uses the exact old/new target union.
- When the aggregate worker flag is enabled, a maximum-64 higher-target page is
  bootstrapped and rebuilt. `space_id=NULL` organization events also settle
  pending aggregate targets.
- The Windows graph projector publishes immutable `graph.json`, `index.json`,
  `log.json`, `backlinks.json`, and `manifest.json` generations. It copies no
  descendant Markdown and repeated rebuild from the same vector is byte-identical.
- The existing canonical graph route gains additive aggregate metadata only
  under the new flag. The canonical runtime binds the aggregate service only
  when that flag is enabled.

## 3. Boundary and no-go attestation

- Tests used disposable v12 SQLite databases and isolated blob/projection roots.
- No migration source was edited. v10 remains
  `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`,
  v11 remains
  `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`,
  and v12 remains
  `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`.
- Live `knowledge/`, `user_data/`, `.runtime`, 10011/10111, target services,
  and company APIs were not accessed or changed.
- No feature flag was enabled outside isolated test composition. No LLM,
  Codex bridge, GGUF, network provider, embedding, or reranker was invoked.

## 4. Stable implementation hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
to avoid recursive document-state coupling.

| File | SHA-256 |
|---|---|
| `knowledge_core/domain.py` | `cf167ed65edc9cc7766de1f9fee364bc9b6a2a3f48844437149c71459beb473d` |
| `knowledge_core/feature_flags.py` | `3c0af17e0820c3933f988dd32ea6626d1769d16a55b852bd14387c5827dacfc7` |
| `knowledge_core/scope.py` | `4c0cef5cc534579e3f7cca67d816df9f8aa21f744222462facc0e4fdd0fc6e27` |
| `knowledge_core/composition.py` | `f73931aaeb3f2ef9b650ab7d719459c3400c72cdc1d1d8860b19a084fc593cb2` |
| `knowledge_core/agent_runtime.py` | `eaec81e8bd3467295553762ced87addbaae0dc06a3815a2a11bd25856c8eadfc` |
| `knowledge_core/repositories/aggregate_graph.py` | `2a8cef7dfab237a2dda7abcba41b093e8855d980dcf422e4c39897e49def67a5` |
| `knowledge_core/repositories/outbox.py` | `15bab2b807532468774e1a27a85b6c67a15e353b455c6fe590c9981de8846815` |
| `knowledge_core/repositories/temporal.py` | `c18640d35788dbe5240a99f8d3ab52ecc422b4f09fd95b89d19cd3dc28404f45` |
| `knowledge_core/repositories/management_reads.py` | `a81ce6c304007394f28aaa915196766f109a19ecde0e44abc781a6421a4bba73` |
| `knowledge_core/services/aggregate_graph.py` | `ec9bad0656306f5d6f8b803b68e7da562c6316692d3eb2f430d8d97e019d4041` |
| `knowledge_core/services/management_reads.py` | `1d617ce0edb3f3d89d885e2e25abbec1c5ef6e6441ec7974d0d5ca98f07456af` |
| `knowledge_core/canonical_routes.py` | `18ca7c2ae3d225db5c76fce8deb10eddcc5b4d25ac008ce16a37556eb19b2b60` |
| `knowledge_core/projection/graph.py` | `57b9e7b5ef70f05c6ea4403795e870ba44a39d0cc6acdaa36f557255d98d96f1` |
| `knowledge_core/projection/worker.py` | `a712d069730a05801e9904b087b68af6a8fb1ad21c595dc4bac3f05477a23156` |
| `knowledge_core/production_service.py` | `6766895a975349935d28a1fd4823cd7b42f27a668411f7ea019d28033b9d08a4` |
| `tools/runtime_preflight.py` | `cf4a863fc056a10942c490f08d8dd5f41270ea5c730b00bc751d739366713fed` |
| `tools/local_second_brain_validation.py` | `17650e8e5dfe99de0d6b585d0ec2a179fcdf08799bd157056c5f7c86908ea305` |
| `tools/full_second_brain_preview.py` | `08da880b9b0b80f549ba8c39f6159e5416ea4bf0cab389337311eb79f96d74c5` |
| `ops/production.env.example` | `9b9ab5893494b7c4b0b21eef02ea8ec19faa72d73c91c2b8afc9f68657d8c399` |
| `tests/knowledge_spaces/unit/test_aggregate_graph_service.py` | `7153fd7822702300c67577bcdde003838dd5d4a4555fd9cd13681a4e2229a2f0` |

## 5. Verification results

Environment: Windows, Python 3.14.3, SQLite 3.50.4. Every pytest root was
disposable.

```text
Focused final AGRAPH-005..014 service/read/projection/worker selection:
10 passed in 20.25s

Final aggregate/runtime/composition selection:
69 passed in 38.95s

Final authority/edit/lifecycle/proposal clean shard:
135 passed in 231.86s

Merge/semantic/temporal/projection/flag/preflight/production clean shard:
284 passed in 202.77s

Windows projection-worker and production-service multiprocess selection:
7 passed in 18.66s

Python compileall for knowledge_core and tools:
PASS

Report-schema-v2 contract audit after status transition:
error_count=0, 284/284 scenarios, 13 disabled successor flags

Contract-audit regression suite:
27 passed in 82.04s
```

Two predecessor test assertions were corrected while running the broad gate:
the v12 lifecycle SQL boundary now counts both v1 and footer-v2 projection
markers, and the merge lock-failure fixture compares its job count with the
pre-existing SBE-3.1 publication-job baseline instead of incorrectly requiring
the global job table to be empty. Neither correction changes runtime behavior.

## 6. Acceptance mapping

| ID | Permanent proof | Result |
|---|---|---|
| `AGRAPH-005` | same-space placement plus local-ledger-only/aggregate rejection and unchanged Part read | PASS |
| `AGRAPH-006` | cross-Part Team-home insert, Part generation/edge isolation, disabled-flag concealment | PASS |
| `AGRAPH-007` | cross-Team Responsibility-home insert with no Team/Part duplicate | PASS |
| `AGRAPH-008` | higher-native placement, exact current LCA, physical closure authority, invalid pair rejection | PASS |
| `AGRAPH-009` | Team versus Responsibility composition, runtime service binding, enabled-worker bootstrap | PASS |
| `AGRAPH-010` | source-space identities and native/inherited labels preserved without canonical dedup | PASS |
| `AGRAPH-011` | revision/row-version and org-home drift hide stale edges before read | PASS |
| `AGRAPH-012` | self-edge rejection, symmetric duplicate conflict, directional cycle, exact replay and response bounds | PASS |
| `AGRAPH-013` | ancestor-only mutation, replay no-op, strict-ancestor home propagation, old/new move targets, no descendant capture | PASS |
| `AGRAPH-014` | same-vector byte identity, topology-aware new generation, graph-only files, zero copied Markdown | PASS |

## 7. Residual ownership and handoff

- `SBE-4.2` owns automatic candidate/detector/model judging, validation,
  correction/suppression, job materialization, and automatic-edge flags. It
  must reuse this packet's LCA, endpoint, generation, and projection contracts.
- `SBE-5.1` owns bounded retrieval/graph expansion and final-ten selection.
- `SBE-6.2` owns browser visualization and correction controls; the backend
  graph response already carries the required origin/home/evidence fields.
- `SBE-7.1/SBE-7.2` own integrated load/security/browser evidence, isolated
  10111 activation, target-server operation, and company-provider handoff.

No later packet may copy descendant Markdown into a higher native directory,
show aggregate edges in a Part graph, store an edge anywhere except its current
exact LCA, or run descendant source-vector capture on an ordinary mutation.
