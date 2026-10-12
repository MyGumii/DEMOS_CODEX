# SBE-8.7 query-independent Wiki manifest restoration evidence

Date: 2026-08-25 KST  
State: locally verified isolated-preview repair  
Status integrator: none; `STATUS.md` was intentionally not edited

## Scope and correction

The phrase-driven Wiki inventory retrieval added by SBE-8.2, SBE-8.3,
SBE-8.5, and the inventory portion of SBE-8.6 is superseded by this repair.
The 10011 general-assistant prompt restoration from SBE-8.6 remains valid.

The canonical hybrid repository no longer classifies Korean or English Wiki
overview sentences and no longer inserts recent documents into
`ranked_documents[]` under an `inventory` provenance. Ordinary FTS, graph
expansion, relevance filtering, final-ten, and no-padding semantics now apply
to every query uniformly.

Every authorized retrieval snapshot instead carries a query-independent,
canonical-DB-backed `wiki_manifest`. It is the bounded Agent equivalent of the
legacy `index.md` navigation context:

- computed only from the server-resolved source/overlay closure;
- current, active, published revisions only;
- selected-view/source/origin provenance retained;
- total authorized published count plus at most 40 recent titles;
- no Markdown body, no graph expansion, and no ranked-search score;
- title clipping and truncation disclosure are explicit.

The answering model may use this metadata to list available documents and infer
only title-level topics. It must not invent document-body details from titles.
Thus a Wiki-overview question works without a phrase list, while topic search
continues to return only relevant documents.

## Regression evidence

Focused compile plus retrieval/Agent execution:

```text
19 passed in 20.68s
```

Adjacent canonical Agent, reranker, conversation binding/pins, production
composition, and full-preview integration:

```text
67 passed in 91.32s
```

The fixed SQL-work assertion is 8 statements for both one-source and
multi-source closures. The additional statement is the single bounded manifest
query; source count creates no query fan-out.

Contract audit report schema v2 passed with zero errors: 41 requirements,
20 goals, 284 scenarios, 70 OpenAPI paths, and 78 operations.

Tests no longer contain the reported Korean/English overview sentences or
import an inventory classifier. The replacement regression proves that two
unrelated, nonmatching queries receive identical authorized manifests while
`ranked_documents[]` stays empty. A 52-document fixture proves the manifest
lists exactly 40, reports the larger total, and marks itself truncated.

## Retained 10211 live Codex proof

The isolated preview was restarted with its existing approved inputs:

```text
data root                 D:\CodexTestTemp\SBE8-Gold-20260825-v1
organization source      D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2
listener                  127.0.0.1:10211
listener PID              45860
health                    ready, schema version 2
company_api_called        false
provider order            codex only
```

Two fresh admin conversations were bound to the default personal Wiki. The
ordinary `/search` endpoint returned zero ranked documents for both questions,
proving that the answers were not produced by inventory padding:

| Question | Ranked search | Conversation | Result |
|---|---:|---|---|
| `색인표를 보듯 이 공간에 보관된 항목들의 제목과 주제를 개괄해 줘.` | 0 | `fd82a7c4-6163-5a69-ad3f-e3f945ae4ed5` | listed all eight authorized titles and title-level topics; disclosed that bodies were absent |
| `지금 wiki에 뭐가있니?` | 0 | `1e30a2a3-f2c0-508b-a846-92228ce840d1` | listed all eight authorized titles; disclosed that bodies were absent |

Both durable turns are `completed` with `model_id=codex-local`,
`model_kind=codex`, prompt version
`canonical-second-brain-turn-v3-general-wiki-manifest`, and retrieval source
`canonical_fts`.

## Changed implementation hashes

| File | SHA-256 |
|---|---|
| `knowledge_core/repositories/retrieval.py` | `524bad0eabeafdb83ef519915ee3a3d118ce68675167acd8277028a3686725bf` |
| `knowledge_core/repositories/hybrid_retrieval.py` | `9d5af16ab1b1c03c407be86697676c328ec9d792db761f6f70c7ec9fb305ad21` |
| `knowledge_core/services/retrieval.py` | `a141a4f2d1f63cc7ceeee5d2ac50b2eaedcba8642afb7c03b604aaeef63d29ee` |
| `knowledge_core/agent_execution.py` | `9245c6cb5aaa3f1b64cc91114baa184128b1b6ce679da59e8899b5d5281241e5` |
| `tests/knowledge_spaces/unit/test_hybrid_retrieval.py` | `cfed4b285462f2dec3afcfd74c0f405e9ad00dd8a477311ff8306692504774bb` |
| `tests/knowledge_spaces/unit/test_canonical_agent_execution.py` | `e88ccd8e337e02a8f90ec2317cc49fa14565c64bf847ec2e6db0a6f3f63a48ca` |

## Boundaries

- No company API was called.
- No production flag, migration, schema, live 10011 root, organization row,
  Wiki body, graph edge, or authorization policy changed.
- Ports 10111 and 10121 were not restarted or mutated.
- The retained preview is local functional evidence, not target-server load or
  company-provider evidence.

## 2026-08-25 grounding, turn-count, and failure-diagnostic repair

An admin conversation exposed three post-gate defects: canonical session rows
always rendered `0턴`, a detailed HR-NAF answer incorrectly said that no Wiki
body had been returned before claiming an external paper cross-check, and the
following turn collapsed a provider failure to undifferentiated
`AGENT_EXECUTION_FAILED`.

The repair keeps the query-independent manifest and ordinary bounded retrieval
semantics, while making these corrections:

- conversation list rows now return total accepted turn count, completed turn
  count, and latest turn state from canonical `conversation_turn` rows;
- the canonical browser maps and renders those fields and refreshes the session
  sidebar after both successful and failed terminal turns;
- direct ranked candidates carry up to 4,096 characters of authorized Markdown
  body instead of using the short FTS display snippet as Agent evidence;
- the existing combined 32 KiB Agent excerpt ceiling remains unchanged;
- the prompt explicitly distinguishes body excerpts from title-only manifest
  metadata and forbids a false "no body" claim when candidates exist;
- canonical turns invoke Codex in evidence-only mode with user config ignored,
  web search disabled, shell and subagents disabled, read-only sandbox retained,
  and no external-verification claim allowed;
- stable non-secret Codex failure categories survive through the durable turn
  error and browser message, while raw provider detail remains undisclosed.

Focused and adjacent regression sets passed:

```text
69 passed in 99.37s
80 passed in 204.54s
37 passed in 17.47s  # focused rerun after nested SSE diagnostic assertion
```

One real restricted Codex marker call returned exactly
`SBE-EVIDENCE-ONLY-READY`. A second non-mutating call used all three real
HR-NAF Markdown bodies and produced a 6,014-character answer with no
`본문이 반환되지`, external-cross-check, or ScienceDirect claim.

The retained 10211 preview was restarted. Read-only authenticated verification
reported the affected conversation as `turn_count=4`,
`completed_turn_count=3`, `latest_turn_state=failed`. The same HR-NAF query
returned three body excerpts of 2,955, 3,379, and 3,861 characters, each
including its Core Claim section. No company API was called and no new live
conversation or Wiki mutation was created for this verification.

Prompt version is now
`canonical-second-brain-turn-v4-grounded-no-external-tools`.

### Repair implementation hashes

| File | SHA-256 |
|---|---|
| `knowledge_core/repositories/conversations.py` | `c3ad65dff4784f9ef0d9a6cc32aaee30d722efe4fcdeab2e4faf347fc47e1799` |
| `knowledge_core/canonical_routes.py` | `5b94a38125b7684b334fe0493e9098357d9d94b2af22f138af8f60a69adce4d5` |
| `knowledge_core/repositories/hybrid_retrieval.py` | `7dc65c12bb4a60b147a66d13333f8e4ded74592baa58b2aefaa4a726094b9b35` |
| `knowledge_core/services/agent_context.py` | `8a8a94f6aa3d78c5fc1ee173303514021210092d9f7c9eca6c20e4d685e67ffd` |
| `knowledge_core/agent_execution.py` | `b22a43af50432de90966e453bc671c65f98c88a30ef6b82d94a5dc85abdaf9ea` |
| `internal_llm.py` | `db3949246e57df163a5acde35b71c703e0586cd04396f038a99b7c98c13668b8` |
| `codex_bridge.py` | `51417731d0901607d651a5cdd7eb9250111f530d1d72f95277bd8240dc72eba3` |
| `static/knowledge_core_browser.js` | `1bbfa146f85af54a4f082acf4f22526eefa88698cbbfda60776640fd6aeb0d53` |
| `static/knowledge_core_transport.js` | `42b96bead1888dcdb405f3bd62143724bff36570f63f40ed8357471071b049ee` |
| `static/sessions.js` | `58fed529c4c8a8094dd2c10ec0cf6c654fdb4d5ae3b304dedba2e96684f42598` |
| `static/chat.js` | `72e1ba42fdd4f2a93dab47eb5f68baa9e4823a54e31bcd316059e0d750cc456e` |

## 2026-08-26 correction: Wiki-first does not mean tool-disabled

The prior same-day repair incorrectly interpreted Wiki grounding as a reason to
disable Codex web, shell, skills, connectors, and user configuration. That
restriction did not match the product intent and is superseded by this
correction.

The canonical Agent now uses this ordering instead:

1. select genuinely relevant candidates from the existing bounded hybrid
   retrieval pipeline;
2. answer a Wiki-covered general or detailed question from the supplied Wiki
   body first;
3. keep Codex web, shell, files, skills, connectors, memory, project, and
   workspace tools available;
4. use external tools when evidence is absent, stale, materially incomplete, or
   the user requests current/external verification;
5. distinguish Wiki-grounded facts from external evidence and never claim a
   tool/source was used without a supporting tool result.

No topic-specific branch, HR-NAF literal, paper title, DOI, retrieval few-shot,
or question matcher exists in the implementation. The existing server-authorized
FTS/graph/rerank pipeline and final-ten contract remain unchanged. The earlier
2,000-to-4,096-character body excerpt correction is retained because it fixes
the generic body-delivery defect rather than special-casing a query.

The `evidence_only` bridge/API mode and all injected
`web_search="disabled"`, `tools.web_search=false`, `features.shell_tool=false`,
`agents.enabled=false`, and `--ignore-user-config` arguments were removed.
Prompt version is now
`canonical-second-brain-turn-v5-wiki-first-tool-assisted`.

Validation completed with company calls disabled:

- 51 focused bridge/Agent/retrieval/reranker tests passed;
- a real Codex Wiki marker call answered `ORBIT-COPPER-7319` from the supplied
  Wiki body without external-source markers;
- a real v5 Codex call for the exact question
  `HR-NAF에 대해서 자세히 설명해봐.` used the three supplied Wiki bodies and
  returned a 5,442-character answer with no URL, external-material, web-search,
  or cross-check marker;
- a real explicit web request used web search plus the `openai-docs` skill and
  returned the official Codex configuration-reference URL;
- a real explicit shell request returned the isolated preview
  `codex-context` working directory;
- a forced 60-second shell wait under a 3-second bridge limit returned
  `CODEX_EXEC_TIMEOUT` in 4.69 seconds after exact Windows process-tree
  termination, preventing plugin/MCP descendants from retaining stdout and
  making a timed-out turn appear permanently stuck.

### Correction implementation hashes

| File | SHA-256 |
|---|---|
| `codex_bridge.py` | `de2e0201be1cea47ce9c3baa653d82d9ede9ef34f47974cbcbd08d7cf56e8d61` |
| `internal_llm.py` | `4e1f2971c2731714464eade4741c0ce07ea71931cdde2fb63bfc99e763fd0994` |
| `knowledge_core/agent_execution.py` | `a7cdb6548d4a451dba423765ec12089facc324f3d5bebb4de3a7cd1d3826e940` |
| `tests/test_codex_bridge.py` | `9582b07beeab274fccacbd487d022794cd67ceebebe450f9bd7c26740468fecb` |
| `tests/knowledge_spaces/unit/test_canonical_agent_execution.py` | `758e9433610bd8bee9f144ea13f807a1fe8f39d0fb4090496298dd09812b89b9` |
