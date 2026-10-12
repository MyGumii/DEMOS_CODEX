# SBE-8.10 generalized body-evidence retrieval evidence

Date: 2026-08-31 KST  
State: local implementation verified; external provider/browser/load gates open  
Preview: `http://127.0.0.1:10221`  
Data root: `D:\CodexTestTemp\SBE9-V13-20260827-v6`

## Failure closed

The selected personal Wiki could answer some topic questions because lexical
FTS happened to return a body. Part, Team, and Responsibility overview
questions could instead leave the Agent with only a bounded title manifest.
SBE-8.9 repaired several visible phrases, but its selected-view-name and generic
word classifier was still a phrase-sensitive routing layer. A renamed view or
an unanticipated natural question could therefore reproduce the same
manifest-only result.

## Implementation

- Removed the production phrase/name overview classifier. No tenant name,
  document title, expected answer, test marker, Korean question phrase, or topic
  keyword selects the new path.
- The provider-compatible query planner now returns a closed, generic intent
  (`topic`, `overview`, `continuation`, or `exhaustive`) plus bounded lexical
  search terms. Invalid or unavailable model output degrades to `unknown` and
  the original query; it cannot broaden the authorized corpus.
- Query-ranked BM25/graph/rerank documents remain the final search lane and are
  never padded with irrelevant rows. A separate supplemental lane loads actual
  current Markdown bodies for representative corpus coverage or prior-turn
  continuation. Ranked plus supplemental output remains at most ten documents.
- Representative selection is one fixed-query-count, current-revision,
  published-only, closure/overlay-authorized, per-source round robin. Exact
  item/content duplicates are removed. Small corpora are covered completely up
  to ten; larger corpora disclose representative bounded coverage.
- Each completed Agent turn stores at most ten item/revision/source identities.
  A later continuation reuses them only after the current selected view and
  closure match and each identity independently passes current authorization,
  publication, revision, body, and index checks. Malformed/stale hints are
  ignored and cannot grant access.
- `wiki_manifest` is now explicitly navigation/count metadata. Actual
  supplemental Markdown is delivered separately as `wiki_body_evidence`, with
  `representative` or `continuation` selection reason. Prompt revision
  `canonical-second-brain-turn-v7-body-evidence-aware` forbids body claims from
  titles and requires a representative-coverage disclosure when applicable.
- Existing hard bounds remain: final ranked search results at most ten,
  combined Agent body contexts at most ten, mandatory contexts at most four,
  and total excerpts at most 32 KiB. No schema, migration, rollout flag, Wiki
  item, organization row, graph edge, or administrator source document changed.

## Automated verification

```text
Focused retrieval/rerank/executor regression: 41 passed in 43.07 s
Knowledge Spaces unit suite: 1971 passed, 5 subtests passed in 2320.87 s
Python compileall: pass
Production phrase/name overview classifier search: absent
Contract audit report schema v2: pass, 0 errors
```

The focused suite proves arbitrary renamed-view behavior, generic planner
schema/caching/fallback, real-body overview coverage, fixed SQL query count,
foreign/personal exclusion, combined byte/result bounds, prior-turn identity
reuse, current-publication reauthorization, prompt separation, and retrieval
trace persistence. The broader unit suite includes all canonical repository,
scope, graph, rerank, conversation, publication, projection, and worker units.

A wider `tests/knowledge_spaces` run reached the pre-existing REG-002 browser
security characterization after 122 passes, where the unauthenticated page
returned HTTP 403 instead of that environment-specific test's expected 200.
This failure precedes canonical retrieval and is not represented as a passing
full integration gate.

## Isolated release-candidate verification

Read-only retrieval against the retained v13 database returned non-empty actual
Markdown bodies for every selected-view class:

| Selected view | Authorized manifest count | Actual bounded bodies |
|---|---:|---:|
| Default personal Wiki | 5 | 5 |
| Additional personal Wiki | 5 | 5 |
| Part | 15 | 10 |
| Team | 40 | 10 |
| Responsibility | 95 | 10 |

The exact Korean Part overview question also succeeded with planner intent
`unknown`: ranked count 0, supplemental actual-body count 10, and every excerpt
non-empty. This proves the old failure does not depend on a recognized phrase
or a successful intent-model call. A topic plan for congestion returned ten
ranked bodies and included the stored 70-percent threshold.

After restart, `/healthz` reported `ready`, both durable and projector workers
alive, Codex-validation provider mode, and `company_api_called=false`. Real HTTP
Codex turns completed for default personal, additional personal, Part, Team,
and Responsibility views. They answered from body facts including HR-NAF,
AMHS 70/55-percent congestion control, 8-percent/420-ms servo control, and
195-degree/18-to-22-percent coffee roasting. Part conversation
`7178bbe9-4e15-58bb-b957-e4069ffb3ebf` then completed a second follow-up turn
from the preceding evidence. Both completed turns stored prompt v7, the same
selected Part/closure proof, and ten bounded retrieval identities.

## Boundary and handoff

The company OpenAI-compatible provider was not available and was not called.
Codex Bridge validates transport, body delivery, prompt assembly, and bounded
continuation behavior; it is not a substitute for company-model quality. The
final recorded real-browser click pass and target concurrency/load/restore
gates remain external. Port 10011 and its data were not touched.
