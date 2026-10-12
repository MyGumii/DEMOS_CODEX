# SBE-8.9 selected-view UI and Wiki-body restoration evidence

Date: 2026-08-30 KST  
State: local release-candidate regression repaired and verified  
Preview: `http://127.0.0.1:10221`  
Data root: `D:\CodexTestTemp\SBE9-V13-20260827-v6`

## User-visible failures

Two independent failures were reproduced:

1. The top selector could show an additional personal Wiki, Part, Team, or
   Responsibility while the Knowledge sidebar still displayed the previously
   rendered personal document list. The list also waited for all seven
   management surfaces, so a slower Graph/Log request delayed the visible
   document switch.
2. A natural overview question that included the selected organization name,
   such as a renamed Part name, was not recognized as a corpus-overview query.
   The query-independent manifest therefore listed authorized titles while
   ranked body excerpts remained empty. The model then correctly refused to
   invent body details, but incorrectly treated the selected view name as a
   missing document title.

## Repair

- `knowledge_core_browser.js` now invalidates hidden stale requests, clears an
  old scope immediately, paints the selected scope's document list as soon as
  that list returns, and completes Index/Log/Graph/activity/findings/pins in
  the background. A visible Knowledge panel is refreshed after the chat-mode
  scope race as well as the normal management-mode switch.
- The same browser path covers the default personal Wiki, an exact additional
  personal Wiki, Part, Team, and Responsibility; no organization directories
  were reintroduced beneath the separately approved top selector.
- Hybrid retrieval reads the exact selected-view display name from the same
  authorized SQLite snapshot and uses it only to classify a generic corpus
  overview. Generic kind words and an exact current Wiki name are supported;
  topic-bearing questions still take the ordinary bounded BM25/graph/rerank
  path.
- The selected view's server-authorized ID, kind, display name, and organization
  path are now included in the Agent bundle. Prompt revision
  `canonical-second-brain-turn-v6-selected-view-aware` tells the model that the
  current view name is scope metadata, not an expected document title.
- Corpus-overview questions bypass unnecessary model-based multilingual query
  expansion. Normal multilingual topic queries retain expansion and fallback.
- No tenant name, test question, expected answer, document marker, or topic
  keyword was added to production code. The final reranked result bound remains
  ten documents; the manifest remains separately bounded at forty entries.

## Verification

Repository checks after the final change:

```text
Python py_compile: pass
node --check static/knowledge_core_browser.js: pass
Affected unit/integration aggregate: 118 passed in 105.88 s
Focused dynamic-name/UI/prompt selection: 9 passed
```

The DOM transport regression switches, in order, an additional personal Wiki,
Part, Team, Responsibility, and the default personal Wiki. Each switch must
replace the previous rows with only the selected scope's item. A separate
deferred-surface test proves that the document list paints before the slower
Graph/Log requests finish and that an older late response cannot overwrite it.

After restart, 10221 reported `ready`, `company_api_called=false`, and live
durable/projector workers. The retained validation account `sbv-p01-u01`
returned these actual selected-view results:

| View | Authorized manifest count | Body excerpts supplied |
|---|---:|---:|
| Default personal | 2 | 2 |
| Additional personal | 5 | 5 |
| AMHS설계파트 | 15 | 10 |
| AMHS지능화 Team | 40 | 10 |
| AMHS담당 Responsibility | 95 | 10 |

The 10-result body counts at higher levels are the approved retrieval bound,
not missing documents. The separate manifest preserves the authorized count.
All five management list routes returned distinct current titles and item
counts.

A real Codex Bridge turn was then created for the selected Part without a
document title or validation marker in the question. Conversation
`b1684394-edb6-5d4a-8a70-a7a773dc1df5` completed with prompt v6 and answered
from the stored AMHS OHT congestion body: its 70% open threshold, 55%/two-sample
release rule, dispatch freeze/reroute/telemetry sequence, and required audit
fields. It no longer claimed that a same-titled Part document was missing.

## Boundary

No schema, migration, feature flag, organization record, Wiki item, graph edge,
or uploaded validation document was deleted or rewritten. The company API was
not called. OpenAI-compatible company-model quality, target load, and the final
real-browser click pass remain external gates. The in-app Browser and Chrome
automation plugins on this workstation still reference mismatched cache
versions, so API, live canonical retrieval, and DOM-event evidence are not
misrepresented as a new visual click recording.
