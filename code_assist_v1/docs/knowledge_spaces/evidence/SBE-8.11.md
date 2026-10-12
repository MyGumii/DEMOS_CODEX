# SBE-8.11 full-corpus path parity and web higher-scope gating evidence

Date: 2026-08-31 KST  
State: local implementation verified; company-provider/target-scale gates open  
Preview: `http://127.0.0.1:10221`  
Data root: `D:\CodexTestTemp\SBE9-V13-20260827-v6`

## Product decision implemented

- Personal Wiki, Part, Team, and Responsibility use the same authorized
  full-corpus retrieval policy in the backend. Direct lexical candidates,
  pre-rank candidates, final ranked documents, and supplemental current bodies
  have no fixed document-count cap.
- Graph discovery remains work bounded. Mandatory pins/references remain max
  four. Individual model excerpts remain max 4 KiB and mandatory plus ranked/
  supplemental excerpts share an exact 96 KiB budget with deterministic
  clipping/coverage disclosure.
- The existing all-personal-Wiki management search remains a separate max-ten
  management operation and does not enter Agent context.
- The web scope selector enables personal Wikis and Part. Team and
  Responsibility are rendered disabled. Their server descriptors, selection
  resolution, read APIs, CorpusClosure, retrieval, and already-bound history
  remain functional for future clients and administrative validation.

No schema migration, company API call, production-root mutation, topic/name
few-shot, expected answer, or tenant-specific retrieval branch was added.

## Runtime changes

- `canonical-hybrid-rerank-v2-full-corpus` removes SQL direct-candidate and
  final-result slicing for all four selected-view kinds and carries null cap
  values in retrieval explain metadata.
- `hybrid-rerank-v2-full-corpus` retains the complete eligible authorized order.
  If the optional cross-encoder exceeds its own serialized input bound, the
  deterministic ranking remains available without failing the Agent turn.
- `canonical-second-brain-turn-v8-full-corpus-aware` receives contexts composed
  under the shared 96 KiB budget.
- The Hybrid repository accepts the canonical search route's legacy `limit`/
  `top_k` hint through 100. The hint does not slice full-corpus results. This
  closes a live mismatch where the HTTP route accepted 100 but the repository
  rejected values above 20 and the route surfaced an internal error.
- Browser scope options use a single kind-based gate. The controller/backend
  `selectSpace` behavior was not removed. The static asset query version was
  advanced so an old selector script is not reused after restart.

## Automated verification

```text
Python compileall: pass
Focused retrieval/rerank/UI/browser/contract: 109 passed in 134.41 s
Final retrieval/rerank rerun after limit-range alignment: 37 passed in 52.30 s
Knowledge Spaces collection: 1,977 unique tests
First full-suite segment: 1,853 passed before a command-induced pytest
  `-p no:debugging`/nested-trace option conflict
Standard-plugin rerun from that file through suite end:
  124 passed, 5 subtests passed in 163.22 s
Unique suite coverage: all 1,977 tests passed; 5 subtests passed
Contract audit: pass as part of focused and full unit coverage
```

The interrupted full-suite process did not expose a product failure. Removing
pytest's built-in debugging plugin removed the `trace` option that
`test_test_safety.py` intentionally expects in an inner pytest run. The exact
test and every remaining collected test passed with the standard plugin set.

Focused tests prove more than ten ranked/current bodies for Part and Agent
context, more than ten backend results for personal/Team/Responsibility, exact
96 KiB disclosure, deterministic ordering, reranker parity, Team/Responsibility
disabled web options, forced disabled-option selection refusal, and unchanged
personal/Part selection behavior.

## Isolated HTTP and real-browser verification

The retained clean v13 preview restarted with marker
`69f86898010bcca2b0f114fe7ec4f77e3026da9be14c12048fcc4b9fc7697fb5`.
`/healthz` returned `ready`, `company_api_called=false`, and one live durable
plus one live projector worker in `codex-validation` mode.

Authenticated read-only HTTP checks returned:

| Kind | View | Backend selection | Visible items |
|---|---|---:|---:|
| personal | 내 지식 | 200 | 10 |
| part | AMHS설계파트 | 200 | 15 |
| team | AMHS지능화 | 200 | 40 |
| responsibility | AMHS담당 | 200 | 95 |

Thus Team/Responsibility remained server-selectable/readable even though the web
choice is disabled. A real Chrome DOM pass on the same authenticated preview
showed exactly:

| Web option | State |
|---|---|
| 내 지식 | enabled, selected |
| Part · AMHS설계파트 | enabled |
| 팀 · AMHS지능화 | disabled |
| 담당 · AMHS담당 | disabled |

The selector itself was visible/enabled and browser console errors were zero.
Port 10011 and its data were not touched.

A final live `field note` search used the public maximum `limit=100` and proved
the deployed full-corpus pipeline beyond ten results:

| Kind | Direct | Pre-rank | Relevant unique final |
|---|---:|---:|---:|
| part | 15 | 15 | 15 |
| team | 40 | 40 | 36 |
| responsibility | 95 | 95 | 37 |

All three responses used `canonical-hybrid-rerank-v2-full-corpus`. The smaller
final counts are relevance/exact-content deduplication, not a result cap; no
irrelevant padding or fixed-ten slicing occurred.

## Remaining external gates

Codex-validation proves the local transport/composition surface but is not
company OpenAI-compatible provider quality evidence. Held-out retrieval quality,
target-class concurrency/load, backup/restore, service-account operation, and
the remaining end-to-end browser upload/editor/graph journeys stay in the
external handoff.
