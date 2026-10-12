# SBE-8.13 — Long-document evidence and iterative validation

Status: **in progress**, 2026-09-08. Sole product integrator: root agent.
Independent reviewer: `long_corpus_independent` (report only; no product edits).

## Approved scope and first implementation packet

The user approved development after reviewing the long-document baseline,
including personal, Part, Team and Responsibility spaces and multiple authors.
This supersedes SBE-8.11's **per-document 4 KiB prefix cutoff**, not its shared
96 KiB excerpt budget, authorization rules or disabled Team/Responsibility UI.
There is no migration, production flag change or live corpus cleanup in this
packet. Historical evidence and machine-contract scenario counts are unchanged.

Root owns these first-packet files:

- `knowledge_core/evidence_passages.py` (pure allocation and exact passages);
- `knowledge_core/repositories/context_bodies.py` (authorized lazy body reads);
- `knowledge_core/repositories/retrieval.py` (review follow-up: pin integrity
  checks in SQL without preloading every full pin body into Python);
- `knowledge_core/services/agent_context.py` (composition and scope fencing);
- `knowledge_core/agent_execution.py` (model-visible coverage metadata);
- `tests/knowledge_spaces/unit/test_evidence_passages.py`;
- `tests/knowledge_spaces/unit/test_context_bodies.py`;
- `tests/knowledge_spaces/unit/test_hybrid_retrieval.py` (approved expectation);
- this evidence, `STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, `TRACEABILITY.md`,
  `second_brain_evolution/PRODUCT_CONTRACT.md` and `ACCEPTANCE.md`.

## Contract amendment

1. Retrieval previews are not model-bound body evidence. After selection, use
   current, published canonical revision bodies inside a reauthorized selected
   closure. Never expand the scope using a model-supplied ID or filesystem path.
2. Supply whole documents when they fit the shared budget, including Korean
   documents of 12–32 KiB. No 4 KiB per-document maximum remains.
3. If the shared budget overflows, select exact source passages, record source
   size/hash and character ranges, and disclose partial/omitted coverage.
   Passage separators are not source text. Partial evidence is never described
   as having read the full document or the entire corpus.
4. Fetch metadata first and materialize one body at a time; do not put all full
   bodies in the query cache. The shared excerpt budget is not a claim that the
   entire prompt or Python process uses only 96 KiB.
5. Multi-pass overflow reading, server-resolved citation handles, and actual
   LLM upload authoring remain subsequent required work, not passed by this
   first packet. Failed/unavailable authoring must preserve raw extraction and
   must not be reported as successful LLM compression (ADR-023).

## Validation and remaining gates

Baseline: `workspace-feature-evidence/long-document-20260908/RESULTS.md` and
`workspace-feature-evidence/long-document-optimization-20260908/independent-baseline.md`.
The baseline retained extraction text but did **not** verify LLM compression,
multi-author synthesis or all-account live responses. Its four live turns
missed late facts. Previous completion labels are not current release evidence.

Initial integration runs: 115 focused tests passed in 114.20 seconds; after
pin/fixed-reference materialization changes, 69 context/retrieval/execution tests
passed in 94.28 seconds. The final packet rerun is pending below. Earlier fixture
failures were corrected using legal curation/lifecycle transitions and isolated
write flags; product authority was not weakened to satisfy a test.

The four-scope test uses actual bound conversations (`conversation_wiki_binding`
enabled), selected closures, direct Team/Responsibility affiliation and native
plus inherited long bodies. It is model-free input verification, not proof of
provider answer quality. Private/sibling/parent canaries are excluded as the
selected level requires. Whole large pins and fixed references are also covered.

Independent review follow-up removed eager full-body pin/reference loading,
omitted zero-byte UTF-8 fragments, and made offsets explicitly half-open Unicode
code points (not JS UTF-16 units or grapheme clusters).

Remaining: real revision-update races, complete live multi-author conversations,
fresh complex document uploads, multi-pass overflow, citation handles, LLM
authoring, and company-provider handoff. **Optional reranker inputs still use
cached prefix previews**; this packet changes model-bound answer evidence only.
An enabled reranker can therefore still demote a late-only match before byte
allocation. Query-centered reranker evidence and its held-out test are required
in the next retrieval packet; current tests do not close this risk.

Additional bounded-work gate: very many tiny bodies can fit the byte budget
while producing many body/artifact SELECTs. Measure query count, latency and
peak memory on thousands of 1–16-byte bodies; consider batched artifact reads
without reintroducing a fixed result-count cutoff. The current forty-document
test is a coverage/byte correctness test, not a capacity benchmark.

## Reproducible first-packet commands

Run from `G:\AI_Agent\DEMOS_CODEX` using the repository virtual environment.
Pytest creates its own disposable per-process root; never substitute the live
10221 root for these fixtures.

```powershell
$env:TEMP='D:\CodexTestTemp'
$env:TMP='D:\CodexTestTemp'
$env:TMPDIR='D:\CodexTestTemp'
.\venv\Scripts\python.exe -E -m pytest -q code_assist_v1/tests/knowledge_spaces/unit/test_evidence_passages.py code_assist_v1/tests/knowledge_spaces/unit/test_context_bodies.py code_assist_v1/tests/knowledge_spaces/unit/test_hybrid_retrieval.py code_assist_v1/tests/knowledge_spaces/unit/test_canonical_retrieval.py code_assist_v1/tests/knowledge_spaces/unit/test_canonical_agent_execution.py code_assist_v1/tests/knowledge_spaces/unit/test_source_artifact_routes.py code_assist_v1/tests/knowledge_spaces/unit/test_conversation_wiki_binding_service.py
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit --docs-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1\docs\knowledge_spaces --output -
```

Pre-final run: 117 passed in 110.91 seconds. Independent review then added a
mandatory UTF-8-remainder regression and one shared pre-allocation across lanes
so an overflow cannot silently reassign its leftover bytes to later documents.
Final first-packet rerun after duplicate-body sharing: **119 passed in 111.92
seconds** (`test_evidence_passages`, `test_context_bodies`, `test_hybrid_retrieval`,
`test_canonical_retrieval`, `test_canonical_agent_execution`,
`test_source_artifact_routes`, `test_conversation_wiki_binding_service`).
Contract audit: **pass, zero errors**, still 20 goals, 284 scenarios,
41 requirements, 72 paths/80 operations and 31 stable errors.

Pinned/fixed canonical identities now consume the body budget once. Matching
ranked/supplemental entries retain ranking and original-artifact provenance but
send `mandatory_context_identity` instead of another copy of `excerpt_md`.
The regression checks actual model-payload fields and unique delivered bytes,
not only a context object's body text.

This verifies the scoped **model-free inline-body delivery checkpoint**,
not SBE-8.13, the active goal, optional-reranker quality, or live validation.

Post-review compatibility finding, before activation: `items._prepare_body`
also supports a canonical revision with `content_md=NULL` and a retained
`content_blob_hash`. The new body repository currently requires inline text;
such a selected revision would fail closed even though the old retrieval path
could use its current `search_document` body. Current file uploads prepare inline
bodies (`services/publication.py`), so the 119 tests do not prove this alternate
storage path. **Resolve and test hash-fenced blob-backed body materialization
before restarting 10221 with this packet.** This is an implementation gate,
not a company-API dependency. Independent follow-up review is requested.

Pre-read status for the continuing root session: all 18 mandatory documents
have been read. Applicable agent instructions remain in AGENTS.md; this is a
progress record, not a replacement for them. Next order: blob-backed
compatibility; query-centered reranker/expanded-term overflow; iterative reading;
server-resolved citations; LLM upload authoring; fresh multi-author live matrix.

No listener was restarted and no live/user corpus was mutated by this packet.
No service-ready or full-goal completion claim is made here.

## Second packet — storage compatibility and retrieval evidence

In progress. Root remains sole integrator. Previous goal turn is classified as
progress (product edits and 119 passing regressions), not a verified live wait.
Additional owned files: `knowledge_core/agent_runtime.py`,
`tools/full_second_brain_preview.py`, and
`tests/knowledge_spaces/unit/test_context_blob_bodies.py`; existing first-packet
files remain owned by root. The existing CAS verifier is reused without changing
its filesystem authority. Body file I/O must occur outside SQLite transactions,
followed by a complete accepted-turn/closure/revision/reference revalidation
before a model can receive the materialized text. No new routes or migrations.

Root also owns `tests/knowledge_spaces/unit/test_canonical_retrieval.py` for
extending the existing isolated canonical fixture with the supported blob-backed
storage form. The production publication contract is unchanged. The intervening
user clarification turn changed no product state; this continuation resumes the
pending compatibility gate rather than claiming another completed iteration.

The second packet also owns `knowledge_core/repositories/hybrid_retrieval.py`,
`knowledge_core/services/hybrid_reranking.py` and
`tests/knowledge_spaces/unit/test_hybrid_reranking.py`. Direct lexical reranker
previews reuse the already-authorized, expanded-query FTS snippet instead of a
prefix. This is a ranking preview, not full answer evidence. Graph candidates
remain in the grading set and keep their existing scores/behavior until their
query-centered passage packet is verified; this direct-candidate change must
not be reported as completing all reranker evidence work. No new response
fields or route/schema changes. The model input version invalidates old grades.

Graph-preview follow-up now preserves every bounded one-/two-hop candidate and
all deterministic score/relation semantics. Non-direct neighbors are read from
their current authorized search revision one at a time using a cursor; raw hash,
byte size and blob identity are checked against canonical body metadata. Their
12 KiB ranking previews use exact query-guided passages (distributed passages
when no lexical overlap exists), not a prefix. Direct/graph intersections reuse
the direct FTS snippet without reloading their full body. This is still distinct
from 96 KiB final-answer evidence. Large graph-body transaction time/peak memory
and real model quality remain required performance/quality gates.

## Second-packet verified checkpoint and activation hold

This section supersedes the earlier *current* inline-only/prefix descriptions;
their test counts remain historical evidence. The root changed actual product
code and tests during this goal turn, so it is **progress**. Nothing was enabled
on a live listener and no existing account, document or source file was deleted.

Verified behavior:

- Both canonical body storage forms work at personal/Part/Team/Responsibility
  levels with genuine conversation bindings and native/inherited isolation.
  Pins and fixed references remain nonrecursive and deduplicated.
- CAS loading uses the existing Windows verifier outside context transactions,
  releases its lease, and verifies strict UTF-8/raw SHA/byte size/blob identity.
  One test stages real bytes and reads them back through runtime composition.
- Invalid loader/type/encoding/hash/size fail generically. Epoch/generation
  changes and a real `ImmutableItemEditService` R1-to-R2 save during body I/O
  prevent the old body from reaching a composed answer context. Budget-omitted
  blob bodies require metadata but invoke no file loader.
- The effective normalized/transformed/expanded lexical query is retained in
  the internal retrieval snapshot. Overflow selection uses it together with
  the original semantic query; the original model/user query is not rewritten.
- Direct reranker input contains the existing query-centered FTS snippet, not
  a prefix. Graph one-/two-hop candidates remain in the grader; exact extracted
  passages retain late inflected Korean facts and the fallback ranking remains
  equal to the deterministic baseline. Direct/graph intersections retain the
  FTS text even when graph score/relation metadata wins.

Verification on 2026-09-08, repository Python with isolated TEMP/TMP/TMPDIR:

- Intermediate storage/context/hybrid run: **55 passed in 84.68 s**.
- Intermediate eight-file run: **137 passed in 140.34 s**.
- Final nine-file functional run: **158 passed in 229.65 s**. Files are the
  original seven-file command above plus `test_context_blob_bodies.py` and
  `test_hybrid_reranking.py`.
- After changing only pipeline identity strings/DTO literals and the matching
  expectations, cache invalidation + search API envelope tests: **2 passed in
  4.95 s**. At that historical checkpoint, retrieval identity was
  `canonical-hybrid-rerank-v3-full-corpus-evidence`; ranking identity was
  `hybrid-rerank-v3-full-corpus-evidence`; grader input identity was
  `bounded-authorized-cross-encoder-input-v2-query-snippets`. The API has no
  new fields/routes or frozen v2 string enumeration to bypass.
- Independent targeted run: **18 passed in 24.98 s**, report-only reviewer;
  these are fake-provider input tests, not live LLM quality proof.
- Contract audit remains pass/zero errors with 20 goals, 284 scenarios,
  41 requirements, 72 paths/80 operations and 31 stable errors.

Two initial fixture mistakes (the CAS observation field name and lease-token
encoding) were corrected in tests; product validation was not relaxed.

**Do not activate yet.** Independent review found graph-preview work is only
row-bounded, not total-source-byte bounded. Repeated edges and second-hop visits
can rescan the same large revision before the merge rejects it. Also, individual
12 KiB previews can exhaust the existing 128 KiB serialized grader limit with
relatively few candidates. A source upload/embedded-asset limit is not proof of
a global canonical Markdown size limit. The next packet must deduplicate before
reading, account for total source work and whole serialized grader input, retain
every authorized candidate, and fall back deterministically when grading cannot
fit. Measure read-transaction duration, query count and peak memory. With no
lexical overlap, distributed passages are samples, not semantically located
evidence; do not claim otherwise.

After that: iterative overflow reading/coverage ledger, server-resolved citation
handles, real LLM Wiki authoring, then fresh rich-file multi-author uploads and
live conversations across all four levels. Codex Bridge remains validation-only;
company OpenAICompatible quality and target operations remain explicit handoff.

## Third packet — bounded graph work and shared grader input (in progress)

The intervening explanation turn changed no product state; this continuation
resumes implementation. Root additionally owns the isolated unit test files
`test_graph_preview_budget.py` and `test_reranker_payload_budget.py` under
`tests/knowledge_spaces/unit/`. The independent reviewer remains report-only.

Keep all authorized candidate identities, deterministic scores, provenance and
graph relation semantics. Resolve graph metadata before body work, reuse each
`(item_id, revision_id)` preview across edges and both hops, and admit at most
8 MiB of distinct canonical source bytes for graph preview construction per
retrieval. This is a measured-work starting bound, not a canonical document-size
limit, an FTS scan bound, or a limit on final answer bodies. Deferred graph
identities remain candidates. If a surviving grader candidate lacks evidence,
skip the entire optional grading call with the existing `input_bound` fallback.

Allocate the existing 128 KiB serialized grader payload across every candidate:
reserve complete identity/title/query metadata, then max-min fair allocation of
JSON-escaped excerpt bytes, redistributing the allowance unused by short texts.
Use query-guided exact passages and disclose that these are partial previews.
Do not cut candidate IDs or silently grade only a subset. Metadata overflow or
an allowance unable to carry nonempty evidence also uses whole-call fallback.
The expanded lexical query must survive this second passage allocation without
replacing the original semantic model query. These internal payload/diagnostic
changes add no route, schema migration or UI activation. Real large-corpus
quality and process-memory/transaction measurements remain required evidence.

### Third-packet implementation and measured checkpoint

The implemented source plan is all-or-none and runs **after both graph hops,
exact-content dedupe and relevance filtering**, not once per hop. A single
metadata-derived batch reads each surviving graph-only revision once; direct
intersections keep the FTS snippet. It compares planned bytes against the
remaining source-work allowance before the body query. Duplicate edge visits,
cycles and exact-content alternates cannot force repeated reads. Query count in
the single/multi-source fixture is now exactly 10 rather than 9: the one extra
statement is a per-retrieval body batch, never per space/edge/document fan-out.

Runtime composition now passes `flags.local_cross_encoder` into the versioned
`prepare_reranker_evidence` policy. Disabled local grading skips graph body
preparation and model-preview allocation (including temporal-only processing).
Standalone repositories retain explicit preview preparation by default for
compatibility. Preparation mode and the source budget enter the retrieval cache
identity; evidence completeness enters ranking cache identity. Disabled/missing
previews do not suppress final-answer canonical body reads. New inline/blob
accepted-turn tests verify both late facts remain in the actual model payload
with grading disabled or work-bound. No account/topic-specific prompt examples.

Current identities: `canonical-hybrid-rerank-v4-shared-preview-budget`,
`hybrid-rerank-v4-shared-preview-budget`,
`bounded-authorized-cross-encoder-input-v3-shared-preview-budget`, and
`local-multilingual-cross-encoder-v2-partial-previews`. API route/field counts and
historical migrations are unchanged. The input explicitly describes partial
previews; shortening never mutates the deterministic snapshot evidence.

Intermediate verification: the eleven-file run passed **191 tests in 191.06 s**.
After allocation-copy improvements, six-file context/ranking run passed **120
tests in 129.85 s**. These precede the final disabled-runtime regression rerun.
Independent initial new-file run passed **33 tests in 18.18 s** before the later
resource/disabled-runtime additions; it is not final-version approval.

Three real-size Windows diagnostic cases use synthetic isolated blob-backed
canonical revisions, actual SQLite retrieval and a 5-ms RSS sampler. Timing
includes `tracemalloc` instrumentation; it is neither production latency nor a
concurrency/p95 measurement. No external dependency was installed: the venv has
no psutil, so memory sampling uses Windows GetProcessMemoryInfo.

| Source body bytes | Admitted graph reads | Instrumented retrieval ms | Python peak allocated bytes | Sampled process RSS increase bytes |
| ---: | ---: | ---: | ---: | ---: |
| 1,048,576 | 1 | 354.775 | 2,291,460 | 7,127,040 |
| 8,388,608 | 1 | 2,147.929 | 17,064,236 | 69,160,960 |
| 8,388,609 | 0 | 197.915 | 1,687,030 | 60,547,072 |

All three cases passed. Avoiding repeated full UTF-8 encoding/raw retention and
removing the body-batch sort reduced the 8-MiB Python allocation peak from
33,856,566 to 17,064,236 bytes in these diagnostic runs. Total SQLite/process RSS
does **not** obey the graph raw-byte bound: even deferred retrieval increases
RSS. This is observed remaining work, not a memory-safety or service-ready
claim. The benchmark measures optional preparation enabled; runtime disabled
mode is separately asserted to execute zero graph-body queries/selectors.

Fixture corrections were the single batched SQL count, constructing a ranking
service with both successor features off (use the original snapshot instead),
and accounting for legitimate typed temporal enrichment in the disabled-model
comparison. No product authority or fact assertions were loosened.

Release/goal gates still open: full SQL/RSS and many-tiny-document workload;
grader output scalability (currently 2,048 tokens); iterative full-corpus reads;
server-resolved citations; real LLM Wiki authoring; fresh multi-author rich-file
uploads/answers at personal/Part/Team/Responsibility and revision/lifecycle
changes. The listener was not restarted and no live document/account changed.

### Final third-packet regression record (2026-09-08)

After the runtime-disable policy, temporal fixture and source-copy corrections,
the final eleven-file command passed **199 tests in 210.97 s**. It uses repository
`venv/Scripts/python.exe -E -m pytest -q`, isolated TEMP/TMP/TMPDIR, and these files
under `tests/knowledge_spaces/unit/`:

- `test_graph_preview_budget.py`, `test_reranker_payload_budget.py`;
- `test_evidence_passages.py`, `test_context_bodies.py`, `test_context_blob_bodies.py`;
- `test_hybrid_retrieval.py`, `test_hybrid_reranking.py`, `test_canonical_retrieval.py`;
- `test_canonical_agent_execution.py`, `test_source_artifact_routes.py`;
- `test_conversation_wiki_binding_service.py`.

The final two new files collect **41** cases. Independent verification passed
38 cases on the preceding snapshot, nine adjacent regression cases, then all
five latest runtime-disabled/final-answer cases after the final test expansion
(`5 passed in 10.01 s`). Those overlapping run counts are not a unique total.
The report-only reviewer found no P1/P2 defect in this bounded packet. No model
calls or live uploads occurred; passing 199 tests is not real-answer-quality or
full-goal proof. The 10221 listener check returned no listening row and it was
not started, stopped or deployed by this packet. Goal remains active.

## Fourth packet — server-resolved answer citations (in progress)

Root additionally owns `requirements-runtime.lock` for exact-pinned Markdown
syntax parsing (`markdown-it-py` and its `mdurl` dependency). Independent review
found that a hand-written line masker mishandled multiline HTML links and
multiline/indented code. Use a CommonMark token stream for citation validation;
do not maintain another ad-hoc Markdown parser or disable ordinary code answers.
This parser is not an HTML sanitizer and does not replace read-time authorization.

Previous goal turn made product progress and passed 199 model-free regressions.
Root additionally owns `knowledge_core/answer_citations.py`,
`tests/knowledge_spaces/unit/test_answer_citations.py`, and the already owned
executor/context tests. Independent reviewer remains report-only. Build citation
handles only from nonempty body evidence actually supplied to this accepted
turn (including delivered pins/references), never from the manifest alone.
Handles are turn- and immutable-item/revision-bound; filenames are display text,
not identity. The model writes a chapter-local marker, and the server alone
renders links from the supplied authorized artifact metadata. Raw model-written
knowledge API URLs and unknown/stale handles fail structural validation. General
knowledge, external tools and ordinary Markdown answers remain available.

This first citation packet proves identity/link rendering and records cited vs
merely supplied sources separately. It does not infer semantic support from a
valid handle, force every general-answer paragraph to cite Wiki, or declare
uncited answers verified. Claim-support/quote validation, live-model compliance
and download reauthorization remain required evidence before release. Existing
download endpoints retain their normal read-time authorization checks.

### Fourth-packet implementation checkpoint (2026-09-08)

This continuation changes product code and adds new isolated evidence, so it is
progress. It does not activate a listener or close the long-document goal.

- `canonical-second-brain-turn-v11-server-citations` now prepares a private
  body-only citation catalog before model invocation. Handles bind turn, selected
  view, physical source, item and immutable revision. The model gets handles,
  titles and filenames, but no original-download URL to copy. Actual body lanes
  include shared mandatory/ranked evidence and historical reference targets;
  manifest-only, grader-only, unavailable and body-omitted rows cannot mint a
  handle. General answers and external tools remain permitted.
- After output validation, only the server renders selected-view download URLs.
  Same filenames do not collapse different items/originals. The completion keeps
  supplied sources (`supplied_not_claim_verified`) separate from actually cited
  sources. Citation trace includes item/revision/source space, delivered-passage
  hashes, original artifact/source-revision IDs and original content digests.
  `claim_support=not_semantically_verified` is deliberate: valid identity does
  not prove the chapter is supported. A marker-free answer remains `uncited`.
- Current ranked/pin metadata and historical reference identity/body hash are
  revalidated in the provenance read transaction. Artifact lookup is keyed by
  item **and revision**, not item alone. An R1 reference cannot acquire an
  original first attached to R2. Model I/O remains outside transactions; existing
  download services reauthorize and verify bytes when a link is used.
- Exact-pinned `markdown-it-py==4.2.0` and `mdurl==0.1.2` were added to the runtime
  lock and installed in the repository venv. Nothing else was upgraded. The
  hand-written Markdown masker was replaced after independent probes found
  indented/multiline code and multiline/raw HTML mismatches. Tests now reject
  unknown/stale/escaped/entity/case markers, model-written canonical routes and
  active HTML link construction, while retaining external links, passive
  formatting and inert code examples. This is not a complete browser sanitizer.

Observed tests, all model-free and isolated under `D:\CodexTestTemp`:

- Root pure citation + executor files: **82 passed in 15.87 s**.
- Independent latest parser: **72 passed in 14.19 s**; executor selection:
  **8 passed, 2 deselected in 2.85 s**. These overlap the root run and are not an
  additional unique total. The reviewer found no remaining reproduced P1/P2 in
  the pure server transformation. See the report-only file
  `workspace-feature-evidence/long-document-optimization-20260908/independent-baseline.md`.
- Personal/Part/Team/Responsibility by inline/blob: **8 passed in 32.01 s**.
  Real isolated canonical users, role episodes, conversation bindings and
  closure resolution supply multi-source bodies; forbidden personal/sibling
  identities stay absent. All originals use one filename but different bytes.
  `WikiFilesService.attach` and `SourceArtifactService.download` preserve exact
  content and SHA through each selected view. The stager is an in-memory test
  adapter and the answer runner is controlled; this is not real PDF authoring,
  filesystem-CAS/browser clicking or LLM-quality evidence.
- Stable-reference current/historical by inline/blob: **4 passed in 10.98 s**.
  Historical cases perform real `ImmutableItemEditService` R1-to-R2 saves and
  attach a second original to R2. A normal reviewed publication transition is
  seeded for R2 (the edit correctly makes an item provisional). The delivered
  R1 body, rendered link and downloaded original remain R1; the R2-only original
  is absent. This does not claim that the curation worker ran.

Fixture corrections preserved authority rather than bypassing it: create an
active Part-leader episode before enabling writes, use the actual organization
API parameter name, admit pre-existing authorized graph evidence in the expected
supplied set, and seed the legal reviewed transition after an edit. The shared
publication fixture now increments the actual item row version instead of
hard-coding revision-one's row version. No product visibility rule was weakened.

### Host instability and unobserved-run record

During earlier runs in this packet, Python 3.14.3/SQLite 3.50.4 raised native
access/heap exceptions during migrations (including `0xc0000005` in
`C:\Python314\sqlite3.dll` and `0xc0000374` in ntdll). Windows Application event
1000 also recorded CLR/coreclr access violations in PowerShell and an unrelated
service. Read-only memory inventory did not show exhausted physical memory.
This is evidence of host/runtime instability, **not a proven root cause** or a
license to replace DLLs, restart Windows or change security settings.

`-E -B -X dev` diagnostic runs subsequently passed 35 context tests on an earlier
snapshot and the latest isolated cases above, but another diagnostic run also
crashed. Diagnostic flags are not a fix and these passes do not erase the
failures. A seven-file regression session (`17788`) produced partial progress;
after a continuation its handle was missing and process inventory showed no
matching pytest process. Its final outcome was not observed and is not counted
as a pass. A fresh run uses a JUnit artifact to retain a terminal result.

No live account, source, document or listener was changed. The latest read-only
10221 check returned no listening row. Keep activation held until host health
and the actual live integration gates are revalidated.

### Remaining citation and full-goal gates

- Pin/validate the browser Markdown renderer or sanitize its output and prove
  DOM behavior for normal/offline rendering, stored messages and SSE reconnect.
  A pinned server parser alone cannot prove equivalence to client renderers.
- Non-hybrid compatibility hits/neighbors/pins need equivalent original-file
  provenance evidence; absent payload metadata does not prove a missing original.
- Define bounded presentation for many originals without losing identity; the
  current all-original rendering can exceed the 32,000-character answer bound.
  No-original sources currently render a label, not a clickable revision route.
- Exercise inherited live downloads plus deactivate/restore, membership and
  artifact-retention changes during the model interval. Click-time authorization
  is retained but does not guarantee a pre-model link remains available later.
- Semantic support, real LLM file-to-Wiki authoring/compression, iterative
  overflow coverage, many-author large-corpus live turns, grader output scale
  and target SQL/RSS/load gates remain open. Keep company OpenAICompatible model
  validation and operations as explicit handoff, not a Codex-quality substitute.

### Retained final regression and persistence checkpoint

The fresh seven-file run ended normally: **195 passed in 191.51 s**. Command:
repository `venv/Scripts/python.exe -E -B -X dev -m pytest -q -x`, isolated
TEMP/TMP/TMPDIR, these files under `tests/knowledge_spaces/unit/`:
`test_answer_citations.py`, `test_canonical_agent_execution.py`,
`test_context_bodies.py`, `test_context_blob_bodies.py`,
`test_hybrid_retrieval.py`, `test_canonical_retrieval.py`, and
`test_source_artifact_routes.py`.
JUnit `D:\CodexTestTemp\sbe-8-13-citations-regression-20260908.xml` records
195 tests, zero errors/failures/skips, SHA-256
`af7b2fc387d013574d09292ad187529210c14bf315fa32a29886361a904a7eaf`.

The five adjacent files (`test_evidence_passages.py`,
`test_hybrid_reranking.py`, `test_graph_preview_budget.py`,
`test_reranker_payload_budget.py`, `test_conversation_wiki_binding_service.py`)
then ended normally: **81 passed in 76.71 s**, same interpreter/diagnostic flags,
JUnit `D:\CodexTestTemp\sbe-8-13-citations-adjacent-20260908.xml`.
Together these disjoint file sets cover **276 model-free cases**; they are not a
whole-repository or live-service certification.

After those runs, the existing eight four-scope/inline/blob cases were extended
through real `ConversationService.complete_turn`, stored-message reads and
`reconnect_turn`. The persisted assistant text contains rendered source links,
not citation markers; replay preserves the complete citation metadata. Cursor
replay through the canonical `_sse_chunks` encoder produces the exact remaining
event suffix. SSE here carries completion/message-ID signals; this test does
not pretend that the event payload carries the full answer text or that an
actual browser/network reconnect was exercised. All **8 passed in 35.52 s**;
JUnit `D:\CodexTestTemp\sbe-8-13-citations-persistence-20260908.xml`. These are
stronger reruns of eight cases above, not eight additional unique cases.

Product SHA-256 at this checkpoint:

- `answer_citations.py`: `f678269a57ad5f8f362d6f85705b839bbed6b37deebe08c9e2268315a4ccaf33`;
- `agent_execution.py`: `7447bf6c1bd03a8c50c6566d4f4ae27b4445ab7ad198e3c90897df1e7b334830`;
- `services/agent_context.py`: `5fb60d080289a6c9dd2085e2c023c96a559cb54d5e68758c5a84e81bd981efbd`.

Contract audit after the fourth-packet hot-document updates: pass/zero errors,
input SHA-256 `72c058f35878b95de6561e98031d70706ae0a27249f554ca357a1de346ec9e4c`,
with the unchanged 20 goals/284 scenarios/41 requirements/72 paths/80 operations/
31 stable errors. Later status-note edits require a fresh audit hash. No live
server was restarted; the native failure history remains an open activation
issue despite successful terminal reruns.

### Next authoring integration constraint established by code inspection

`CanonicalRichPublicationExtractor.extract` still returns extraction text, not
an LLM-authored Wiki. `PublicationPreviewExtractionHandler.prepare` passes that
text to `prepare_publication_artifact`; `commit_effect` persists its immutable
`extraction_snapshot` and makes a preview ready. Publication then freezes that
same snapshot (`_freeze_new_publication_source`) and checks it again through
`PublicationCommitRepository` before creating the initial revision body.

Do not simply replace the extractor's Markdown with an LLM summary: doing so
would relabel generated text as the original extraction and break independent
source-versus-Wiki compression/fidelity evidence. The next implementation must
retain raw source, exact extraction and authored Wiki as distinct immutable
artifacts, with a source-range/authoring manifest and provider/input/output
identity. It must preserve current preview authority, exact replay, atomic
personal+Part publication and the nonblocking provider-unavailable fallback in
ADR-023. Independent authoring review is report-only; no schema or publication
behavior was changed in this checkpoint.

The final persistence extension also retries the same canonical completion and
asserts exactly one user and one assistant message. These eight `CONV-012`
cases passed again in **34.67 s**; retained artifact:
`D:\CodexTestTemp\sbe-8-13-citations-persistence-idempotent-20260908.xml`.
After the final hot-document edits, contract audit again passed with zero errors
and unchanged registered totals; input SHA-256
`4f3e995b27e6a1e2cb0b130212217c62dae9aae688785676b7daf8dd1e4b1e5b`.

Independent authoring inspection confirms another integration requirement:
same-source/same-origin/exact-body deduplication currently assumes that the
extraction and initial Wiki body are the same. A future authored snapshot must
bind its policy/body identity separately, so a legitimate rewrite is not
silently treated as an identical extraction replay. Inspect meaningful-text
readiness as well: a format-generated heading for an empty/scanned source must
not be counted as successful source comprehension. This latter finding is a
code-inspection lead, not a newly reproduced PDF/OCR result in this packet.

## Fifth packet — source-preserving LLM Wiki authoring (in progress)

The preceding turn made progress in real persistence/identity tests and retained
276 model-free regressions. Root additionally owns `knowledge_core/wiki_authoring.py`
and `tests/knowledge_spaces/unit/test_wiki_authoring.py`, plus the existing hot
documents. The first implementation slice is the provider-neutral chunk authoring
and exact-source assembly boundary. It is not yet wired into publication and
does not change a schema or live listener. Independent reviewer remains report-only.

Plan every source code point with no prefix or document-count cutoff, using a
bounded request per chunk rather than one unbounded prompt. Preserve source
snapshot identity/hash and exact global quote ranges. Model text is generated
paraphrase, never relabeled extraction or semantically certified by quote
existence. All chunk results must be present, validated, and bound to the same
immutable extraction before assembly; failed/missing chunks cannot yield a
silently partial authored Wiki. Persist raw/extraction/authored artifacts in a
subsequent explicit forward storage/publication packet, including durable
per-chunk retries and exact idempotency. Do not put an entire long sequence of
model calls inside the existing extraction job's lease or transaction.

### Fifth-packet receipt and transport follow-up

The intervening user explanation turn changed no product state. Root revalidated
the active goal and the independent review before resuming. Additional ownership:
`internal_llm.py`, `tests/test_internal_llm_request_budget.py`, and the isolated
`workspace-feature-evidence/long-document-optimization-20260908/authoring_probe.py`
and its generated evidence directory. No live uploads or existing corpus cleanup
are authorized by this packet's tests. The shared API adapter must preserve its
existing unbounded-call behavior for other callers; authoring opts into exact
UTF-8 request-body measurement before transport. Logical input hashes and actual
transport hashes are different evidence and must not be conflated.

Before durable integration, bind assembly to a frozen provider/work policy,
recompute request receipts, reject forged metadata, and account for whole-document
work without silently truncating a source. ADR-023 still requires preserved
extraction-only availability on model failure; an authored result must not replace
or masquerade as the exact extraction. Production remains OpenAI-compatible-only;
explicit Codex validation is not proof of Gemma/Qwen quality.

Root's next owned storage slice is `knowledge_core/migrations/v014_wiki_authoring.py`,
`knowledge_core/migrations/authoring_catalog.py`,
`knowledge_core/repositories/wiki_authoring.py`, and
`tests/knowledge_spaces/unit/test_wiki_authoring_storage.py`. Its optional test
catalog is deliberately distinct from the active v13 catalog until service,
retention, worker and UI integration gates are met. Existing v1-v13 SQL and
checksums, preview source hash, and extraction-ready behavior remain unchanged.
The additive state separates source extraction, generated body/ledger, and
publication selection. No live migration or feature activation is performed.

### Fifth-packet storage and exact-evidence follow-up

The clarification-only turn made no product progress. Root revalidated the
actual probe session `79888`, which then returned exit 0. All three PDF model
runs completed, but their Wikis expanded by 5.69–16.25% in logical UTF-8 bytes.
Independent review found 24/24 held-out facts preserved yet two additional
boundary omissions/fragmentations and repeated qualifications. This is not a
compression or end-to-end upload pass; the next quality gate requires general
boundary-aware map/reduce, not topic-specific examples.

The independent reviewer also found Windows text-mode newline conversion in
the retained probe artifacts. Existing files are not rewritten. Their receipts
describe logical strings, not exact on-disk bytes. The new probe writer uses
raw UTF-8, verifies raw bytes on resume, and rejects legacy translated evidence.

Root additionally owns `llm_wire.py` and the already owned common-runner/core
tests, plus `tests/knowledge_spaces/unit/test_authoring_probe_artifacts.py`,
for a shared exact Chat-Completions serializer. Authoring must require a
nonempty API transport receipt bound to its exact messages, options and wire
model, while Codex keeps its separate bridge evidence. A shape-valid random hash
or empty API receipt must not be treated as a verified transport record.

The optional v14 storage tests found and corrected a migration postcondition
convention error and a non-idempotent completion path. Preparation now keeps
full-source/response validation outside the write transaction; a process-local
sealed preparation and immutable metadata fence prevent stale/tampered commits.
The seal is never a persisted token, client input or user-authorization grant.
A restarted worker must re-prepare from durable chunks. Current actor/role/job
authorization, publication selection, cancellation, retention and four-scope
upload integration remain required before enabling v14.

Root also owns `knowledge_core/wiki_synthesis.py` and
`tests/knowledge_spaces/unit/test_wiki_synthesis.py`. The next pure boundary is
source-bound claim cards and bounded reduction batches. It must account for
every input card, preserve transitive source ranges and original warnings, and
include exact adjacent/page-break source context. A small complete single-chunk
source without structural boundary flags requires no extra model call. Large
inputs are grouped by exact JSON bytes and reduced in bounded rounds, never by
dropping the tail or silently publishing a partial reduction. The driver is
initially isolated/offline only; production must schedule each batch durably,
not place a whole reduction tree in one extraction lease or transaction.

### Fifth-packet independent synthesis review, 2026-09-09

The nine-file model-free authoring/storage/adapter/extractor regression ended
normally: **160 passed in 91.80 s**, retained JUnit
`D:\CodexTestTemp\sbe-8-13-authoring-synthesis-regression-20260909.xml`.
These tests do not cover the independent review's newly identified P1 cases:
context-only factual support and transitive merging of adjacent boundary
windows. Root is correcting those cases in the already owned synthesis module
and tests. Context must not become an exact evidence range merely because a
model cites its ID. Any new fact recovered from boundary context needs a
separately verified exact quote; semantic validity still requires evaluation.
Keep atomic boundary windows bounded and colocate them with neighboring claims.

The exact-byte v3-map/synthesis-v1 Codex probe is still running in its own
process at this checkpoint; its evidence directory is
`authoring-model-20260908T150524213241Z` under the owned long-document evidence
root. Preserve it as v1 evidence rather than relabeling it after a prompt/schema
fix. No live state or company-provider call is part of this probe. Root owns
the forthcoming status/backlog/traceability/acceptance checkpoint; the optional
storage design is not yet an active forward-schema contract.

### Fifth-packet retained v2 checkpoint

Independent review reproduced duplicate leaf consumption across adjacent
reduction batches. The first v2 set-union test missed it. Root replaced helper
consumption with one owning batch per input and a separate immutable
`helper_refs` lane; a helper ID cannot be used as an owned `source_id`.
Counter-based tests now prove exactly-one membership at 7,000, 10,000 and
14,000 input-byte policies. Boundary evidence and reading context remain
separate. Newly recovered exact quote fragments retain absolute source ranges
and deterministic snapshot/range/hash IDs; overlapping windows yield the same
quote identity. Ambiguous matches, normalization drift and quotes outside the
named context fail, and a no-progress nonterminal wave stops before another
model wave. A terminal conflict-preserving one-for-one result is allowed.

The focused final slice passed **30 tests in 6.15 s**. The final nine-file
regression then ended normally: **176 passed in 105.43 s**, using isolated
TEMP/TMP/TMPDIR and `venv/Scripts/python.exe -E -B -m pytest -p no:cacheprovider`.
JUnit `D:\CodexTestTemp\sbe-authoring-synthesis-v2-owner-final-regression-20260909.xml`,
SHA-256 `10e4ecb35ada6806826571b300bc1afdcfff7abfc9e9a93900a4b22992638130`.
The earlier 160/169 and focused 23/24/30 counts are overlapping snapshots,
not additional unique tests. Final source hashes:

- `knowledge_core/wiki_synthesis.py`:
  `0b89fdbb9af4acfe085a729a77f6da6a64beab3c6b4d48de9c5db7ce77ac0b4c`;
- `tests/knowledge_spaces/unit/test_wiki_synthesis.py`:
  `c432a644a3209de739569c952cf55e4f19ac4f813cd184d4aac35c4a66b07ea1`.

The exact-byte v3-map/synthesis-v1 model probe subsequently finished with
`completed_ungraded`; the observed launcher PID 21128 no longer existed.
All seven chunk calls and three one-batch reductions completed. This is PDF
extraction plus explicit Codex model work, not browser upload/publication.
Raw extraction and Wiki file lengths/hashes match their plans/manifests:

| PDF fixture | Extraction UTF-8 bytes | Wiki UTF-8 bytes | Wiki/source |
|---|---:|---:|---:|
| LONG26-02-S-amhs.pdf | 12,479 | 11,090 | 0.8887 |
| LONG26-01-P-astronomy.pdf | 10,397 | 10,264 | 0.9872 |
| LONG26-01-S-food_beauty.pdf | 10,249 | 9,532 | 0.9300 |

All manifests retain semantic/compression verification as false. Initial
independent AMHS/astronomy review finds 16/16 held-out facts present but the
non-gold AMHS confirmation-time boundary rule still not reconstructed, plus
false partial-document warnings after all map chunks completed. Smaller output
alone does not pass the gate. The food/beauty independent semantic review is
now complete: its eight held-out facts and the previously missing cross-page
control-condition rule are preserved. It still retains a contradictory split
warning and a false partial-document warning. Total held-out recall is 24/24,
but the additional AMHS condition remains absent and all three have false
whole-document uncertainty. This does not pass semantic compression. Keep the
v1 outputs immutable and separate from the v2 code fixes.

The final independent v2 rerun passes **30/30 in 6.96 s** at the source hashes
above. It confirms exact-once owned membership for all 58 dense-fixture input
IDs at 7,000/10,000/14,000 bytes and zero owned/helper intersection. Remaining
mechanical limits include meaningful whole-wave progress rather than one-byte
prose shrinkage, cumulative planning/history accounting and durable ancestry.
Helper prose fidelity and resolution of warnings remain semantic gates, not
properties proved by a valid ID or exact quote alone.

Next quality slice must distinguish complete document-wide map coverage from
the current reducer batch's partial/full card set and selected original
passages; chunk-local lack of context must not become a false final-document
gap. Re-evaluate boundary recovery without injecting fixture topics, questions
or expected answers. Other open work includes structured format-neutral
boundaries, body/manifest-safe literal rendering, planning/output/CPU accounting,
durable map/reduce jobs, optional-schema terminal/selection constraints,
publication/retention/blob integration and actual four-scope uploads/turns.
The active catalog is still v13, live listeners/data are unchanged, and company
OpenAI-compatible quality plus target-runtime operations remain handoff gates.

After final status/backlog/traceability/acceptance updates, contract audit passed
with zero errors. Input SHA-256:
`c3e14a3126127b7ad64a665e5ecce28eedb1cf2613c3482f7082e3f6bbb54153`.
Registered totals remain 20 goals, 284 scenarios, 41 requirements, 72 paths,
80 operations and 31 stable errors. No active goal completion is claimed.

### Next fifth-packet slice — scope-aware synthesis and verified map reuse

The preceding goal turn was progress: v2 ownership/provenance fixes, 176
model-free cases and three complete real-PDF model outputs exposed concrete
semantic gaps. Root revalidated the active goal and current source before this
slice. Existing ownership remains `wiki_synthesis.py`, `wiki_authoring.py`, their
tests, `authoring_probe.py`, `test_authoring_probe_artifacts.py`, this evidence
file and the predeclared hot-document integration. The independent agent is
report-only. No live corpus, listener, migration or company API is changed.

Separate verified document-wide map completion from current-batch card and
original-passage coverage in the frozen reducer input. Give every boundary or
qualification one explicit disposition instead of forcing generated warnings
into factual claims. Preserve genuine retained warnings separately through
later waves; source absence is not automatically resolved by processing all
chunks. New boundary facts still require narrow exact source quotes. Reuse
only byte-verified, policy/source/request-bound completed v3 map responses in
a new versioned synthesis probe; do not overwrite or relabel old model files.
Code-level accounting and compression remain distinct from semantic acceptance.

### Current continuation — complete-map and whole-wave identity

The preceding user clarification turn was not implementation progress. The
root rechecked the actual probe: launcher 30956 and child 32424 are absent and
the retained v3 report is terminal `failed`. AMHS synthesis failed after
300.98 seconds; astronomy and food/beauty completed ungraded. Do not restart or
relabel these artifacts. Independent semantic/failure review is report-only.

Root retains ownership of `wiki_synthesis.py`, `test_wiki_synthesis.py`, the
probe harness/tests, optional authoring storage/tests, this evidence and the
predeclared status/backlog/traceability/acceptance integration. The next change
binds every batch to verified map completion, the exact ordered complete wave,
and complete predecessor batch responses. A caller-supplied count or hash is
not proof. Any process-local preparation seal is only a tamper/stale-state
fence: persisted workers must reconstruct it from committed maps and reduction
receipts, never accept a serialized seal as authority. Live publication,
accounts, listeners, migration catalogs and company API remain unchanged.

### Fifth-packet v4 verified checkpoint (not activation)

Synthesis v3 added context dispositions and separate whole-map/current-batch/
raw-passage coverage; plain-text-v2 rendering preserves apostrophes. Map reuse
now revalidates exact bytes, plan, provider descriptors/responses, validators,
both receipt layers and the origin's completed document row. Resume compares
frozen bytes, not JSON equivalence. Map-only preflight
`authoring-model-20260908T155012291131Z` reused seven maps with zero new calls.

The real v3 probe `authoring-model-20260908T155044461219Z` is terminal failed,
report SHA `2f61f13b306021ffb6ac67380f1b8d1ff76901bb8d4325ce353718ac3d833787`.
Its loaded harness preceded the origin-row/frozen-resume follow-up; it was a
fresh run with valid origin rows, not proof of those later harness changes.
Independent review found:

| Source | Source/Wiki UTF-8 bytes | Actual outcome |
|---|---:|---|
| AMHS | 12,479 / none | `CODEX_EXEC_TIMEOUT`, 300.98 s, before validated output |
| Astronomy | 10,397 / 10,079 | 8/8 gold, false partial warning removed; only 3.06% smaller |
| Food/beauty | 10,249 / 8,968 | 8/8 gold and cross-page control rule recovered; fixed-presentation-temperature condition omitted |

AMHS has no output/provider receipt/model identity; its two-attempt reservation
does not prove two actual attempts. Both completed sources resolved all context
cards with zero new Q receipts, so actual boundary-quote recovery is untested.
Food also retains repetition. These are not faithful-compression passes or v4
evidence. Expected answers remain outside product prompts. No new model call
was made during this continuation and no old probe artifact was rewritten.

V4 binds complete verified map receipts to `map_completion_id`, and the ordered
whole card set, policies and predecessor receipts to `wave_id`. Batches verify
owned/helper membership. Advancement revalidates the full batch plan and all
responses, model/transport records and dispositions. Unprepared descriptors
say `unverified` and cannot reach the model author. Process seals never go to
models or persisted jobs. `_prepare_wave` is a private signer, not a validating
constructor for future durable DTOs: only fully verified maps or predecessor
response chains may reach it.

Optional v14 guards require terminal timestamps, complete chunk/authored-body
readiness, frozen chunk-plan bindings and ready/present matching-source selection.
Optional cumulative schema SHA:
`e4760751074dec242ec512fff98b72f7eba15dbe8c60a24b4bfcc18a38c972aa`.
Active v1-v13 catalog/checksums are unchanged. Four new isolated storage tests
exercise the real publisher plus selection guards. They also prove the remaining
integration gap: publication still writes extraction; adding selection metadata
later cannot change that immutable body. This is not authored-publication or
rich-file upload implementation evidence.

Nine-file model-free regression: **237 passed in 137.51 s**; JUnit
`D:\CodexTestTemp\sbe-authoring-synthesis-v4-regression-20260909.xml`, SHA
`0cc242a47434dd2250af73e87a8866499f07bcceb874f319154825e13aa9baa2`.
Independent review passed the then-current 60 synthesis cases and confirmed the
count-only flaw is closed. Six later tests cover source/model/synthesis-policy
crossing and three real fresh-interpreter replays. Only source and fake responses
cross processes, never a preparation DTO/seal. Complete replay reproduces map/
wave IDs with a new seal; missing map or reduction responses fail. Latest
synthesis: **66/66 in 15.30 s**, JUnit
`D:\CodexTestTemp\sbe-synthesis-v4-process-final-20260909.xml` (60 overlap).
Initial new-test failures were incorrect observation-table/task names; correcting
them required no product changes. This is process-level core replay, not durable
DB/job/lease/authority recovery, real-provider or all-scope upload proof.

Final SHA-256:
- `wiki_synthesis.py`: `cc5c27c66ddc156618b2748a4d9cb4a12d3dadf30a86342497df43c0407c90c0`;
- `test_wiki_synthesis.py`: `f91996c34d7825fd9917b09c88a84c7ecbc1dd57d846a970864929d1cdbaafcd`;
- `test_wiki_authoring_storage.py`: `9cf2e931864394dbf8c5760394f3709dda0b82b56cc72cb492e546e3058885f8`.

Next: condition-level fidelity beyond ID accounting, meaningful nonterminal
factual compression/progress, run-level planning/history work and durable
source-preserving authoring/publication. Full-source validation for each saved
chunk must move outside the writer. Blob/retention/cancellation, 96-KiB overflow
reading, current authority and all four scopes' multi-author rich uploads,
Index/Log/Graph/answers remain open. No v14 activation, server restart, live-data
change or company API call occurred. Native host repair and company Gemma/Qwen/
target-operation gates still require their own evidence.

After the current status/backlog/traceability/acceptance updates, the contract
audit passed with zero errors, input SHA
`5a25051a7be86c79a3fa418c75cf29b3cbb9177262fe65c4197ad9ded35a6b04`.
Totals remain 20 goals, 284 scenarios, 41 requirements, 72 paths/80 operations
and 31 stable errors. This turn is concrete implementation/evidence progress;
the full active objective is not complete or blocked.

### Next continuation — prepare chunk results before the writer

The preceding turn is progress (v4 wave/receipt code, real-process core replay,
237 broad cases and independent model omissions). Root rechecked current source
and goal. Existing ownership covers `repositories/wiki_authoring.py`,
`test_wiki_authoring_storage.py`, this evidence and the hot-document integrator.
Move per-chunk full-source reconstruction, quote validation and transport
verification outside the writer; commit only a bounded prepared response after
fresh immutable source/chunk/preview/actor-epoch fencing. A process-local seal is
not authorization or a durable receipt. Initial run creation and complete
authoring/publication integration remain separate work, not implicitly fixed.
Independent reviewer is inspecting the real publisher integration boundaries
read-only. No live root, migration, flag, listener or company API is changed.

### Publication preparation ownership — source and selected body

The interrupted chunk-preparation suite is terminal: 55 passed in 73.65 s,
`D:\CodexTestTemp\sbe-authoring-chunk-preparation-final-20260909.xml`.
The intervening user clarification was read-only, not an implementation
checkpoint. Root resumes the unchanged four-scope, multi-author goal.

Root additionally owns `repositories/publication_commit.py`,
`services/publication.py`, and the isolated
`test_authored_publication.py` for source-preserving publication integration.
Existing optional authoring schema/catalog and storage-test ownership remains.
This is internal service development only: no route/UI contract exposure,
active migration, live listener/data, model call or feature-flag activation.
Keep exact extraction provenance and author-created references separate from
the selected canonical Wiki body. Model prose must not define stable source
references. Exact-source reupload with nondeterministic generated prose needs
stable published-body reuse before this path can be activated; it must not
silently duplicate or propose an update because the model rephrased a file.

### Resumed checkpoint — finish authored publication evidence

The intervening user explanation turn was read-only and did not implement the
goal. Root revalidated both exact prior process handles: the frozen authored/
storage run is terminal with 72 passed and two fixture authorization failures;
the final legacy route/higher/publication run is terminal with 61 passed in
222.44 s. The second author's personal space was created with writes disabled
while the fixture requested a default personal preview. Correct that isolated
fixture through the existing space repository; do not weaken product authority.
Root retains the predeclared publication/storage/test/hot-document ownership.
The independent reviewer resumes read-only stable-source reupload analysis.
No live data, listener, active catalog, production flags or provider calls change.

Raw-reupload regression now exercises real preview creation and the durable
UTF-8 extraction worker before two fake authorings of identical bytes/name.
All three personal/Part/both cases fail on selection of A2 instead of the first
published A1 (`sbe-authored-reupload-red-20260909.xml`, 13.15 s). Root implements
target-authorized stable-body reuse in the already-owned publisher/service and
optional v14 schema/catalog. Source bytes and each author's provenance remain
separate; no prior private preview/manifest/body is loaded. Any incompatible
target history or preparation race must not overwrite an existing revision.

### Internal authored publication and first-stable reuse checkpoint

The earlier chunk-preparation implementation now completes full-source/plan,
quote and response verification before entering the writer. Chunk save and
completion refresh DB time rather than trusting a caller's pre-work timestamp;
four crossing-expiry cases cover prepare/save/assemble/complete. A process seal
remains an internal integrity check, not authorization or a durable receipt.
Initial run creation still needs outside-writer planning/source integration.

The publication service accepts an internal optional authored snapshot identity.
It freezes the selected body before writing, rechecks current actor/preview/
target/source readiness, and writes revision, source links and authored selection
atomically. Curation then publishes/indexes that exact body. Author-created
explicit references come from extraction even when absent from the generated
prose; model-only reference markers do not acquire author-created edge effects.
The first real publisher tests failed on the historical extraction-only lineage
trigger. Optional v14 now supplies a forward successor branch; v1-v13 SQL and
the runtime catalog remain unchanged. No public route accepts the new identity.

Independent review caught selection-history deletion and stale-time gaps.
The selection DELETE trigger is immutable, selected revision/body metadata must
agree, and a pending authoring run selected as extraction-only is terminalized
in the same publication transaction. `extraction_selected` is a server
disposition, not a claim of model failure. Actual linked job/lease cancellation
is still required before runtime wiring.

Fresh ordinary reupload previews have new artifact/extraction IDs. Exact raw
SHA/size/media/name plus extracted/normalized SHA/size, normalizer, extractor
name/version and warnings identify source equivalence. A2, the new model output,
stays in the request hash; the actual first-stable A1 is selected from an
authorized native target, with a fresh source link for the new upload. Preparing
another author's shared body first checks the same personal/Part or exact
Team/Responsibility role/actor/organization fences as the writer. It reads only
the target's canonical body plus earlier immutable metadata, never the earlier
private preview/extraction/authoring body/manifest. The old author's departure
or missing private authoring body does not erase a still-visible shared Wiki.

Both targets use A1 when only one has a prior body. Different target histories
are not resolved by target order or by choosing A2. Edited/inactive/unavailable
source knowledge fails without restore/overwrite/new duplicate; a newer current
revision with the same A1 bytes and normalizer keeps its item and gains fresh
provenance, even when original links exist only on its historical revision.
The writer rechecks zero/nonzero reusable-candidate metadata to reject a
concurrent first publisher. Requested A2 and actual A1 replay are separate;
missing/mutated stored selection or a modified manifest fence is rejected.

The initial reuse query returned every matching provenance row. Independent
review found its Python fence grew with repeated uploads. Grouped/window SQL
now checks all matching histories for incompatible items/states/bodies while
returning one earliest stable summary per target (zero to two rows total).
An eight-upload regression retains eight source links and one item, with fence
sizes 0,1,1,1,1,1,1,1. This proves returned Python shape, NOT constant total SQL
work, SQLite temporary-memory usage, latency, or thousand-user capacity. The CTE
can still scan/sort matching history; that remains a measured performance gate.

The temporary concern about an unchanged `both` operation's memberless new
operation-level lineage group was withdrawn after checking the existing
cross-process test and API contract. Existing item lineages must not be silently
reparented. The equal-body/different-snapshot standalone-anchor test preserves
that policy and source selection rather than changing the old lineage contract.

Retained terminal evidence (overlapping intermediate runs are not additive):

| Evidence | Result | Meaning |
|---|---|---|
| `sbe-authored-publication-storage-frozen-final-20260909.xml` | 74 passed, 111.74 s | Initial 15 authored publication plus 59 storage cases; before stable-source reuse |
| `sbe-publication-final-compatibility-20260909.xml` | 61 passed, 222.44 s | Recovered original route/higher/publication compatibility run |
| `sbe-authored-reupload-red-20260909.xml` | 3 failed, 13.15 s | Real UTF-8 upload/extraction-worker reproduction: new A2 selected for unchanged source |
| `sbe-authored-reuse-first-20260909.xml` | 18 passed, 44.13 s | Initial reuse plus authored publication cases |
| `sbe-authored-reuse-four-scope-final-20260909.xml` | 29 passed, 96.21 s | Current reuse/authority/lifecycle/real extraction-worker paths, including Team/Responsibility |
| `sbe-authored-reuse-additional-20260909.xml` | 2 passed, 8.73 s | Equal dual anchors/unchanged lineage and altered reuse manifest rejection; disjoint additions |
| `sbe-authored-reuse-storage-compatibility-20260909.xml` | 120 passed, 319.43 s | Storage/route/higher/legacy publication; loaded before the final reuse normalizer/anchor follow-ups |
| Independent focused subset | 11 passed, 45.35 s | Actual current code hashes, 0 model calls; 18 deselected from loaded 29-case file, before the two disjoint additions |

All XML artifacts are under `D:\CodexTestTemp`. The final fully frozen five-file
regression is terminal: exact exec handle 21587 returned **151 passed in 405.38 s**,
exit code 0, recovered after the intervening read-only user clarification.
`sbe-authored-reuse-frozen-regression-20260909.xml` SHA-256 is
`79bd5a1fc940b09b1b27fe24efc4803c7bbf222421c306f294251f77e60f5baa`.
This is one disjoint 31-authored/59-storage/61-compatibility run on the hashes
below, not an additive sum of earlier overlapping runs. No observation timeout caused a
restart. Test-harness errors during development were corrected without weakening
product rules: personal writes must be enabled for the test author; disabling
an account requires closing its membership/leader role; lease tokens must be
canonical; the extraction harness must register existing curation handlers.

Current SHA-256 identities:
- `repositories/publication_commit.py`: `685728f1d90e2eb20b88441b47168dc6dccd798ad15e6591f5fb6b84cafef7a3`;
- `services/publication.py`: `c4317b14b8d52ab9edf5d67885360ad223aa98a67c8a298bfd970b541bccbee4`;
- `repositories/wiki_authoring.py`: `1446c0b3c0d3069dd30bc182822beb6e74662190bc73cf6285d7367f42dc2f8b`;
- `migrations/v014_wiki_authoring.py`: `866e4abe647c191a39eeff671e6fa4566f7dfcd62a000888ca41cc3daba1f86f`;
- `migrations/authoring_catalog.py`: `f6ef49938bf305b483457ec23abc60af38e7e4ee93a261caf71b413696dba1cf`;
- `test_authored_publication.py`: `93007c1d81c4e141f5069146ce76e27c28d1bd4de8a02ec5a0a5fb707a942401`;
- `test_wiki_authoring_storage.py`: `98a03c73155b5c0f9841b3ac7878f3735911d0fc0873e0794d8b0578d376f410`.

Optional v14 cumulative schema is
`55c68ecc78928c63bd822ed297f1d81ecfbcff28eb506fdc86647b35c698bd2d`.
Earlier e476/f89e/7f399/23a367 fingerprints are historical checkpoints, not the
current optional catalog. Runtime remains v13. Independent runner used
`C:\Python314\python.exe`; root used the project venv Python 3.14.3. Neither
is approved-production runtime evidence and neither repairs the native host hold.

Remaining implementation is material: extraction-only-first reupload handling,
early reuse before another model call, durable map/reduction jobs and recovery,
source/blob/retention/cancellation, final reduced-Wiki publication, public server-
selected upload wiring and 96-KiB overflow reading. The current tests use UTF-8
upload bytes and fake model prose. They do not prove PDF/PPTX/DOCX parsing,
faithful compression, semantic graph quality, live browser/answers or all-account
multi-author operation. More source/version/role/retention/actual-process races,
large-corpus SQL/RSS/latency and the company Gemma/Qwen handoff remain. The failed
real v3 probe above stays failed. No new model call, live-data mutation, listener
restart, company call or runtime schema activation occurred.

### Next continuation — extraction-only-first ordinary upload reuse

The preceding user clarification was read-only, not implementation progress.
The exact pending process is now verified terminal as recorded above. Root
continues the same full four-scope, multi-author goal using the existing
publication/service/optional-schema/storage/test and hot-document ownership.
The independent reviewer owns only its existing report and is inspecting the
legacy extraction-first boundary. A ready new model candidate must not relabel
the old extraction revision as authored, duplicate a byte-identical upload,
overwrite a current edited Wiki, or lose each uploader's original provenance.
Actual selected-body kind/identity and the requested candidate must remain
distinguishable and replayable. No public activation, model call, migration,
live-data mutation or listener change is authorized by this internal packet.

### Extraction-first reuse implementation and independent review

The first new real UTF-8 upload/extraction-worker regression failed all three
personal/Part/both cases by creating different items for the same source after
ready fake A2 authoring: `sbe-extraction-first-reupload-red-20260909.xml`, 3 failed,
10.03 s. This is an observed defect, not a hypothetical compatibility gate.

The reusable-source query now includes earlier extraction-only publications as
well as authored ones. It compares the original published revision and current
canonical body against the baseline raw/normalized hash, byte size and normalizer;
all source-equivalence fields and current target authorization remain intact.
The source query is named `_reusable_upload_sources`. Matching histories still
return one ordered summary per native target; total SQL scan/sort work is not
constant and remains an activation measurement.

An extraction-first result keeps `authored_snapshot_id=None` and carries the
internal `reused_extraction_snapshot_id`. Optional v14's separate
`publication_extraction_reuse` receipt binds the fresh operation, requested ready
A2, prior extraction E1 and source operation. It is UPDATE/DELETE immutable and
mutually exclusive with authored selection. The ready A2 stays ready; it was
not selected, not failed. Fresh item-source provenance still binds the new
artifact/extraction E2, not the prior author's private artifact. No previous
extraction/authoring body is read; only authorized canonical Wiki body and
immutable metadata are reused. New missing targets label their body as extraction.

Receipt insertion verifies source equivalence, exact ready candidate binding,
baseline operation/extraction, target overlap, all new target body identities and
lineage. Writer revalidation separately checks A2 even when the actual kind is
extraction. Exact replay verifies requested A2, E1/source-operation/equivalence,
immutable target overlap and revision hashes, without re-running the anchor's
current lifecycle. The no-op acknowledgement remains valid after later explicit
deactivation and removal of private candidate text; no restoration occurs.

One personal or Part E1 anchor supplies both targets atomically. Three subsequent
both reuploads retain two items and seven original-source links. CRLF and LF
source identity remain distinct/raw-exact. Mixed authored/extraction histories
with equal bytes use the earliest operation's actual kind, while different
contents conflict in either order. Same-body R2 gains new provenance; a different
body or inactive item is not overwritten, restored or duplicated. Team and
Responsibility tests use real upload-preview/extraction-worker and direct leader
authority, not a fabricated Part membership. The departed-cowriter test proves
existing shared-body retrieval without consulting the old private authoring body.

Terminal intermediate results (overlapping, not additive):
- 34 authored-publication cases passed in 110.23 s before replay's final immutable
  source-op/equivalence strengthening and before the expanded test-only matrix.
- 17 extraction-focused cases passed in 53.59 s (26 deselected), including CRLF,
  four scopes, departed Part author and lifecycle/R2; same pre-final replay code.
- Seven one-sided/mixed-kind/rollback cases passed in 39.38 s (43 deselected)
  on the final product.
- Independent current-product review found no P1/P2 defect. Thirteen extraction/
  both/mixed/receipt cases passed in 64.97 s, and one disjoint immutability/lost-ack
  case passed in 5.04 s. All use fake model prose or UTF-8 fixtures, zero models.

Final product SHA-256:
- `repositories/publication_commit.py`: `4b1ee83b408f728b5dd753888c6dc543520f4d3c93b3252afa9c3dc36835b96e`;
- `services/publication.py`: `13e698b6f2e27e99f799a9c2050c615227e1e79a7d6322378490384f0f8488e5`;
- `migrations/v014_wiki_authoring.py`: `bc097524492fccfaea8aab98e032e669e835d670fff130574c8f1a9485cde84c`;
- `migrations/authoring_catalog.py`: `44357598510861682e8c901a7538f6304a041db452b5bb4c2f98bf0869160314`;
- `test_authored_publication.py`: `00f9b7f063fac526f6a36f676315a5ca2ec163f85ea8b56d10a75c868b23a35a`.
Optional cumulative schema SHA:
`da3e5236c49f2a3c717d4116ad236b5a297a05f455b509901fc54329b4fcc898`.

Frozen root runs are both terminal with exit code 0:
- Exact handle 69159: **51 passed in 196.42 s**,
  `D:\CodexTestTemp\sbe-extraction-first-frozen-publication-20260909.xml`, SHA
  `1fdaf37a60cb8091f2ac238f447f5a9b6b1e2caa04c8678c475921bb3f957834`.
- Exact handle 52938: **120 passed in 341.29 s** (59 storage plus 61 route/higher/
  legacy cases), `D:\CodexTestTemp\sbe-extraction-first-storage-compat-20260909.xml`,
  SHA `921d411e2b5dc64a9e6fd904534331b9ec0ff9cf54b81c1cdf08f89e66f505ce`.
These are **171 disjoint model-free cases** on the final product hashes above.
They overlap the historical 151-case checkpoint and must not be summed with it.
All root test handles are terminal. No observation timeout triggered a restart.

Independent residual notes: earliest-operation choice is enforced by the ordered
repository/frozen writer fence, not as an independent SQL-trigger earliest-row
constraint. The prior extraction UUID is internal metadata; public adapter
disclosure policy is not implicitly approved. Root's prior read-only-clarification
closeout contract audit passed, input SHA
`210e949f99d95e137285afc64ed6cc777e884dc00372d23e6123df481fc93994`;
that is not the audit of the subsequent hot-document updates in this section.

Next implementation remains actual authoring integration: outside-writer initial
run/source planning, early first-stable reuse before an avoidable second model
call, durable per-chunk/reduction jobs/receipts/recovery, final reduced-Wiki
publication and server-selected public workflow. Real rich-file/long-document
fidelity, multi-author answers/graphs/index/log, all-account scope matrices,
96-KiB overflow reading, source-role/retention/process races and SQL/RSS/load
remain required. No model call, live data/listener mutation, active migration,
flag change or company API call occurred. The failed v3 semantic probe remains
failed; this packet is implementation progress, not full goal completion.

Final post-status/traceability/backlog/acceptance/contract audit passes with zero
errors over 33 sources, input SHA
`b3bec9eefd8677ffa159f9791056fa3e72b703188755b23ed8af4dc5d4fe040f`.
Counts remain 20 goals, 284 scenarios, 41 requirements, 72 API paths/80 operations
and 31 stable errors. No full-goal completion or blocked state is asserted.

### Next continuation — initial plan preparation and durable chunk workflow

The prior turn is implementation progress: extraction-first reuse is corrected,
171 frozen disjoint cases and independent 14-case review are terminal, with no
live process remaining. Root revalidated the active full goal and current files.
Existing ownership covers `repositories/wiki_authoring.py`, authoring storage
tests and authored-publication fixtures. Move initial source/plan verification
outside the writer and commit only prepared plan/chunk metadata under fresh
actor/preview/source fencing. Root additionally owns the prospective
`services/wiki_authoring.py` and `test_wiki_authoring_workflow.py` for the
versioned per-chunk producer/handler integration. The independent reviewer is
read-only/report-only, inspecting actual job/authorization/recovery contracts.
No live listener/data, company model call, active catalog or UI activation is
part of this internal packet. A source-preserving authoring worker, not a
whole-document model loop under one lease, remains the intended result.

2026-09-09 resumed implementation: the intervening clarification turn made no
product edit; it recovered the exact live handle 74090 to terminal **60 passed
in 79.46 s**, exit 0 (`sbe-authoring-initial-preparation-current-20260909.xml`).
That evidence closes the initial prepared-run fixture regression, not the full
goal. Root additionally owns the narrow public preparation/authority read-fence
methods in `knowledge_core/worker.py`, and sealed whole-plan work reservation
metadata in the authoring repository. Chunk workflow v1 must account for both
durable attempts and runner attempts, preserve completed receipts, and converge
queued/running cancellation. It remains internal and explicitly unactivated.

### Initial prepared run and upload-derived chunk worker v1 — 2026-09-09

Authoritative implementation now includes `PreparedAuthoringRun`: exact source,
source/actor metadata fences, immutable plan/chunk metadata and sealed model/
input/response work reservation, all prepared outside the writer. `create_run`
rehashes metadata and rechecks fresh DB-time/source/actor fencing, then creates
the run/chunks or verifies exact replay. It does not reload extraction or build
model requests inside the writer. Independent review caught the last `_plan_work`
scan after the final fence; it now runs before that fence. Preparation also
rechecks the upload parent's current authority after expensive source work.

`WikiAuthoringChunkWorkflow` is an internal adapter, not a registered production
route or runtime handler. It derives actor, space, epoch, Part and native higher-
role mutation authority from the actual completed extraction job retained by the
preview. It never accepts a caller-selected child-job space. Normal personal/
Part/both uploads retain their actor-owned extraction authority; Part/both final
publication still performs its independent target checks. Explicit Team and
Responsibility uploads retain the exact native leader/executive episode, without
fabricated Part membership. Another author's corpus-content update does not
invalidate an unchanged extraction's mutation authority.

One deterministic `publication_wiki.author_chunk/v1` job binds each run/ordinal/
chunk/request identity exactly once. Initial enqueue/run/bind/audit/outbox are
atomic. The handler reconstructs the plan outside the writer, rechecks claim,
cancel, source and current scope immediately before the one ordinal's provider
call, validates exact quotes/transport outside the writer and commits chunk
receipt, audit/outbox and durable success together. Kernel preparation checks
are opt-in and do not replace its independent final writer authorization fence.
Handler v1 fixes two durable attempts; admission multiplies the source plan's
runner-attempt/input/response reservation by two before enqueue. This preserves
the frozen v3 request format and does not silently enable a Codex fallback for
company-provider plans. Child jobs are internal upload-operation successors
with null new operation/dedup IDs and deterministic effect IDs, not new user
approval operations. Audit identifies the parent/run/ordinal as appropriate.

Queued and in-flight cancellation preserve raw/extraction and previously saved
chunks. Terminal run disposition is truthfully generic `authoring_incomplete`,
not a false model-unavailable diagnosis for cancellation/authority drift. First
terminal disposition wins. A bounded, metadata-only recovery/claim hook
reconciles one run per poll when another service directly cancels queued jobs,
a preview expires/terminates, or explicit extraction publication has already
closed the run. It cancels remaining work without changing the existing
`extraction_selected` reason. Audit distinguishes a selected anchor job from
the reconciliation reason. Existing handlers retain a no-op hook by default.

Tests use real isolated upload-preview/extraction services, optional v14 DBs,
job leases, audit/outbox and source-bound **fake** providers. They do not use
rich-file parsing or prove semantic quality. Cases cover:
- exact enqueue replay, one logical call/ordinal, one stored effect, atomic
  enqueue rollback and lost-ack replay without another model call;
- retry-product admission, provider exhaustion, queued/running cancellation,
  externally terminalized jobs and explicit extraction publication;
- pre-call actor revocation and direct Team/Responsibility role revocation
  after source preparation, with zero model calls and no Part affiliation;
- two Part authors processing different sources while a third source is
  published to the shared corpus;
- a genuinely new Python process after an expired claim, preserving the first
  successful chunk, and after a provider response that never committed. The
  latter legitimately repeats that exact request: external invocation is not
  exactly-once; only one canonical effect is committed;
- initial-run metadata tampering, stale actor/expiry, post-work epoch drift and
  re-preparation after the process-local key changes.

Intermediate red runs remain recorded, not counted as passes. The first
workflow run had five failures from an invalid optional dedup/operation pairing;
both are now null for internal successors. A higher-role fixture initially
ended a role before claiming: the existing authority service already cancelled
queued jobs, revealing the need for reconciliation, which was then implemented.
The model-preflight case now ends the actual role after source reading. Two
initial-status fixture iterations violated canonical account transition
invariants; the fixture now uses the real account-status repository transition.
An extraction-selected audit assertion incorrectly expected a second authoring
terminal event; it now checks that the earlier publication disposition is not
re-written or duplicated. No production invariant was weakened to pass tests.

Terminal results, all exit code 0:
- **102 passed, 174.88 s**, exact handle 18248, comprising 17 workflow, 72
  storage and 13 existing kernel cases;
  `D:\CodexTestTemp\sbe-authoring-chunk-frozen-core-20260909.xml`, SHA
  `893c286e7fb8cef1fe702abe24faa8470e10489d016b7f5606bc97408bd76c64`.
- **58 passed, 238.05 s**, exact handle 58311, comprising 51 authored-publication
  and seven actual multi-process job regressions;
  `D:\CodexTestTemp\sbe-authoring-publication-multiprocess-20260909.xml`, SHA
  `fee5ca4cf969a8ca050136ea375453ca2f27c0daf0d6e0e631d7cdf2e84e8aa5`.
- **2 passed, 16 deselected, 10.96 s**, exact handle 68670;
  `D:\CodexTestTemp\sbe-authoring-post-response-crash-20260909.xml`, SHA
  `c1c52259c881385b8626845108f244f6da7aeb0c45109c513b8b29026802d80a`.
  The last test-only refinement parameterized the earlier fresh-claim case
  into claim and post-response cases. One overlaps the core coverage; do not
  add all historical/overlapping counts. Product files were unchanged.
Earlier six/nine/intermediate 13/16 passing subsets and the three status-fixture
checks are overlapping, not additional final cases. All handles are terminal.

Final source SHA-256:
- repository `wiki_authoring.py`:
  `8b83faf5024041c642bf4d7b073203400c42cc6f965938b8b3a4112bbec3d812`;
- service `wiki_authoring.py`:
  `eef2a35e45017008f48dfd12fd0952d6e70ca0cbfc0239f158049fd0a09bff49`;
- `worker.py`:
  `bf900e314e83753107d9f643ffc7db738b3aa3b92c5736bac989eade52fbe474`;
- storage tests: `35cb66cbabc055df5800403f8a9084ba8fa8147e5d6a782556da96f5def6778c`;
- workflow tests after final parametrization:
  `a3f7377659244592b33c2b12c1265a8419640a1bc37105bb953b91f80fdd204c`;
- authored-publication fixture:
  `cdd09661edaed23f3f5817b8077f66b490877e217903ef6b86e71cb5d9b075a6`.

Independent review is report-only for this continuation and made no additional
live/model calls. Its initial default-whitespace explosion hypothesis was
retracted after a pure probe: default v3 already counts whitespace chunks in
the 512-record admission. A real pre-activation metadata-envelope gap remains:
trusted custom model/policy arrays and enlarged chunk policies need separately
frozen total plan bytes/records. Adding those fields changes request/manifest
identity, so use a version-correct v4 successor rather than silently changing
v3 recovery. Outbox consumer/aggregate-version semantics, precise remaining
source-retention/tamper races, SQLite/RSS and multi-author target load remain
unverified. Current per-chunk reconstruction still revisits the full source and
plan; this is not a linear-whole-document performance claim.

Next: durable model-free plan successor, last-chunk finalizer and per-reduction
jobs/receipts/recovery, final reduced-Wiki publication and server-selected public
upload workflow, early reuse before unnecessary generation, blob-source and
retention integration. Then repeat real long rich-file uploads, held-out
semantic compression, multi-author answers/graphs/index/log and the full scope
matrix. The failed actual synthesis-v3 probe is still failed, not superseded by
fake-provider tests. No live data/listener, active v13 catalog, UI flag, company
API, or host activation state changed. The full goal remains active/incomplete.

Post-integration contract audit: **pass, zero errors**, 33 sources, input SHA
`5c6bb5826aae1f00fd5c943c43236d61d905a015ed76ce85ce61e89b18ae13ea`;
report `D:\CodexTestTemp\sbe-authoring-chunk-contract-20260909.json`.
Counts remain 20 goals, 284 scenarios, 41 requirements, 72 paths/80 operations
and 31 stable errors. The audit checks contract consistency, not product quality.

### Resumable reduction and finalization ownership — 2026-09-09

The previous clarification turn inspected code and explained limits but made no
implementation progress; this continuation takes the next safe implementation
step. Root owns `knowledge_core/wiki_synthesis.py` and the new isolated
`tests/knowledge_spaces/unit/test_wiki_synthesis_resume.py`, retaining ownership
of the existing authoring repository/service and their tests. The immediate
boundary reconstructs complete maps and persisted reduction responses without
calling a provider, returns only genuinely missing current-wave work, and renders
a final Wiki only after a complete terminal wave. A partial or single completed
batch is not document completion. Durable attempt multipliers must reserve the
whole admitted wave before any provider call. No source/model request v3/v4
identity is silently changed. Durable schema/job scheduling and server-selected
public upload remain required subsequent integration, not implied by this pure
replay boundary. The independent agent reviews this boundary report-only.

Runtime-lock ownership handoff to the Notion parity integrator was requested
and acknowledged at this packet boundary. Rechecked SHA-256 of
`requirements-runtime.lock` is
`eb2c677bd3865d81b16dc5018babdd52d01bab39ba0f1e1aa6ad0f5645e19788`.
This synthesis packet does not own or change that file. A future SBE dependency
change requires renewed coordination. Notion parity files are untouched; the
handoff does not clear the host-runtime activation hold.

### Exact receipt replay and common terminal renderer — 2026-09-09

`ReductionReceipt` stores only round/batch/request identities, exact response
and digest, ordered model-pair JSON and canonical transport JSON. It never
persists trusted cards or a process-local preparation seal. `resume_synthesis`
rebuilds all validated map cards and complete predecessor waves, revalidates
each stored response against its exact request and provider policy, and returns
only missing batches in the first incomplete wave. Duplicate, unknown, changed,
future-spliced, malformed or extra-after-terminal receipts fail closed.

The pure stage generator is shared by replay and the existing offline driver;
there is no second final renderer with weaker completion rules. It reserves
whole-wave work before yielding any provider request and accounts separately
for map and reduction durable attempts times runner attempts. Replay recomputes
the same reservation; it does not double-charge saved completions. An execution
accounting object is included when durable multipliers differ from offline
one-attempt defaults; source/model request v3/v4 identities remain unchanged.
The final permitted round may produce its terminal result wave at max_round+1,
but cannot use it to schedule an additional round. Single-map shortcut requires
complete verified maps and no boundary/qualification cards; zero saved reduction
responses alone is never completion.

Independent review identified a metadata serialization trap before storage
integration: v4 ancestry hashes the original model-pair order. The receipt
format therefore deliberately persists an ordered pair-array, not a sorted
JSON object reloaded with `.items()`. This preserves existing v4 ancestry
without silently changing the policy version. A reported Unicode exception
gap was retracted: UnicodeError already subclasses ValueError. New tests
explicitly reject surrogate response/model/transport fields.

A fresh interpreter receives only original source, map responses, and reduction
response/model/transport receipts; no source preparation seal, fake reducer or
provider invocation is used during replay. Both final Markdown and complete
manifest are byte-identical to the offline result. Separate fake-compatible
transport tests recompute the full OpenAI-compatible wire receipt and reject a
missing transport record; no company endpoint is contacted or quality claimed.

Initial collection failed because the new test used a relative import in a
non-package pytest context. The import now follows existing fully qualified
test helper imports. No product invariant changed for that harness failure.
The first existing synthesis rerun passed 66 in 15.24 s (handle 55096, terminal).
The first new replay rerun passed 28 in 9.48 s (handle 75570, terminal). These
overlap the final run; they are not added as unique coverage.

Frozen four-file result: **193 passed, 49.91 s, exit 0**, handle 56018, terminal.
It comprises 30 replay, 66 existing synthesis, 73 authoring and 24 probe-artifact
cases. XML: `D:\CodexTestTemp\sbe-synthesis-resume-frozen-core-20260909.xml`,
SHA-256 `64e137143547dee93d0473ee50dd6f8cfef72777a454c526741bef29898ba8d3`.
Final product `knowledge_core/wiki_synthesis.py` SHA-256:
`76c6349d0714ff034a7f777b9a4766df5243391518c46782e10cc00d6553136d`.
New `test_wiki_synthesis_resume.py` SHA-256:
`cc53965401915825a8f60f40e52eee96a52447ebf1172a58313d791365367719`.
Independent review found no P1/P2 core defect and independently passed the first
28 replay cases in 9.21 s plus the two later context-shortcut negatives in
0.80 s. Those overlap the root run, not additional unique cases. Its retained
reports are `sbe-independent-synthesis-resume-20260909.xml` and
`sbe-independent-synthesis-shortcut-context-20260909.xml` in D:\CodexTestTemp.
Late final Markdown/manifest limits can still fail after model work is spent:
this preserves extraction but remains a cost/performance integration gate.

This is an implementation checkpoint for the common replay/finalization core,
not DB reduction storage or per-batch durable execution. Next: freeze/store
synthesis policy and wave/batch metadata, immutable response receipts and actual
job bindings; connect last-chunk/last-batch finalizers with current source,
retention and role/epoch fences; then server-select the final Wiki in public
upload and reuse. Do not call the offline whole-document driver in a writer or
one durable lease. Actual long rich-file ingestion, semantic compression,
multi-author answers/graphs/index/log, overflow reading and all runtime/host/
company gates are still required. The active goal remains incomplete. No live
listener/data/catalog/UI setting, company API or runtime dependency was changed.

Post-integration contract audit: pass, zero errors, 33 sources, input SHA-256
23775511a9d56f9c3f32ec81ca28499401cc8f430575b1a95b11aaa92dec9d96.
Report: D:\CodexTestTemp\sbe-synthesis-resume-contract-20260909.json.
Independent XML SHA-256, respectively:
94b5174e48590efab9d657fd6fe2d025de435fce6ac81bfe6778cc8c2723bb47 and
8c8e5c741d9e0da8270ad3f28badb2c67040b5a6b4a6a42c748b326ee083830e.
The product/test hashes were rechecked after review and are unchanged.

### Durable synthesis storage ownership — 2026-09-09

The previous continuation made verified implementation progress (193 focused
cases and independent replay review); no process remains running. Root now owns
the additive isolated v15 migration/catalog, repositories/wiki_synthesis.py,
tests/knowledge_spaces/unit/test_wiki_synthesis_storage.py, and narrow completed-
map loading/stage-observation hooks in existing authoring/synthesis modules.
Freeze configuration, exact wave/batch metadata, job bindings, separately
retained immutable responses and the final snapshot completion receipt. A
configured synthesis run must not become ready through map-only assembly.
Source/model calls and full replay remain outside writers; metadata/HMAC and
current authority/source fences guard commits. Existing v14 and active v13
catalogs are preserved. No live migration, runtime activation or original-file
cleanup is authorized by this isolated storage implementation.

### Durable storage continuation and publication checks — 2026-09-09

The intervening user clarification was read-only, not an implementation
checkpoint. Re-polled the existing test handle 73780 to its terminal exit 1:
16 setup errors all came from the new fixture retaining a timestamp before
`create_run`, not from a missing live process. The fixture now captures DB time
after creation, matching the existing storage fixture. No database invariant
was weakened. Handle 46569 then completed 16 cases in 29.25 s, exit 0;
`D:\CodexTestTemp\sbe-synthesis-storage-recheck-20260909.xml`.

Root also owns the new isolated
`tests/knowledge_spaces/unit/test_synthesized_publication.py` for selecting the
completed reduced snapshot in real canonical publication, indexing and
multi-author authorized retrieval across personal/Part/native Team/native
Responsibility and inherited views. Models are fake in this boundary check;
it does not prove semantic compression or browser/provider readiness. Current
audit input files and publication.py remain unchanged during the previously
acknowledged Notion integration handoff. Independent review is report-only.

### Frozen v15 ledger and reduced-publication checkpoint — 2026-09-09

Outcome: verified implementation progress, not full-goal completion. Optional
v15 persists exact synthesis config/input waves/batches, retained response
metadata plus purgeable bodies, and the final output-wave completion. Parent
ready requires the final receipt; map-only assembly cannot finalize a configured
reduction. Full replay/model work stays outside writers. A fresh interpreter
reconstructs saved responses and executes only missing batches.

Independent review found and root fixed two real completion gaps before this
checkpoint: job binding had been optional, then job success did not identify
its response. The repository now requires a sealed exact running ClaimFence,
bound actor/run/round/ordinal/batch/request/retry policy, unexpired uncancelled
attempt before/after preparation and at save. Canonical job result v1 has seven
fields and must exactly identify the saved response; SQL refuses missing,
foreign, extra-field or boolean-as-integer success records. Replay requires
the successful job and exact result. The intended caller must still commit
receipt, success and audit/outbox together; this repository is not that handler.

The real canonical publication regression selects reduced final snapshots,
not extraction or map drafts, in personal/Part/both and exact native higher
targets. Its combined matrix uses four authors (two Part members, a separate
direct Team leader and direct Responsibility executive with no Part home),
18 UTF-8 upload-service/extraction-job operations and 20 independent published
items, including 12 shared Part documents. FTS retrieval returns the exact
Part set, Team native+inherited set, Responsibility native+inherited set, and
each author's private set without personal canaries in organizational views.
Original/extraction identities and generated-body selection remain distinct.
This uses fake model prose and a fake blob stager: it is NOT rich-file parsing,
physical original-file download, HTTP/browser E2E, semantic graph/answer quality,
or concurrent/capacity evidence. Test `_synthesize` is explicitly a storage
integration loop, not the production durable orchestration implementation.

Terminal checks (all exit 0):
- **39 passed, 116.73 s**, handle 80768: 35 storage and four publication cases;
  `D:\CodexTestTemp\sbe-synthesis-ledger-frozen-20260909.xml`, SHA-256
  `03bf739543c6e5b7b66d5fab0c9acd05ba22abc92fa5be435d4427b48bd78b1b`.
- One additional migration rollback/upgrade/replay case passed in 2.53 s;
  `D:\CodexTestTemp\sbe-synthesis-ledger-upgrade-20260909.xml`, SHA-256
  `a15000a1a91aba77d576996ecd677ba90891333538b40e54a586c1d3aacf9449`.
  Only this test was added after the 39-case run; product hashes are unchanged.
- **168 passed, 124.26 s**, handle 31522: existing map storage, pure synthesis
  and receipt replay; `D:\CodexTestTemp\sbe-synthesis-ledger-regression-20260909.xml`,
  SHA-256 `2b93cd3e0cead39e0a536697f898510b0326048ab2035c4d9cdbe251d55e4a8c`.
- Independent frozen selections: 14 passed in 29.12 s and nine job-result
  negatives in 17.30 s. These overlap root coverage, not additional unique cases.
  Reports and exact SHA-256 are in `independent-baseline.md`; no additional
  P1/P2 pure/storage-core issue was found. Actual worker/authority gates remain.
Earlier 16/26/27-case runs and the one multi-author rerun overlap final evidence.
Fixture-only failures (pre-run timestamp, pre-upgrade invalid legacy organization
fixture, incorrect artifact column name, Part writes before leader assignment,
incomplete synthetic job authorization snapshot) were corrected by obeying real
schema/service prerequisites, never weakening production rules. All handles
are terminal. An independent run during schema changes was discarded.

Frozen SHA-256 (paths under code_assist_v1):
- `knowledge_core/migrations/v015_wiki_synthesis.py`:
  `d1865ffe49ace5321382c47bc4b5e3e26936ad41700fc543176ede47aa209a69`;
- `knowledge_core/migrations/synthesis_catalog.py`:
  `b7e0692a04a6837d9c3167ca99c4688fb73e44d4512c81bd5a4c1209dcb91254`;
- `knowledge_core/repositories/wiki_synthesis.py`:
  `670491dfab8d4cba0af1e5e53b6d17859ae284c6c2a3d9fb35d9f9d839e37b11`;
- `knowledge_core/repositories/wiki_authoring.py`:
  `3d7c1e0b33757f2dcd6a897fa3554f169e0e1b71098ce377e9c1873a58e63ba4`;
- `knowledge_core/wiki_synthesis.py`:
  `f672327dac2b6e6a286338bec82af33912318a0fef652db74ae758ef927a4d79`;
- `tests/knowledge_spaces/unit/test_wiki_synthesis_storage.py` (36 cases):
  `873985d9a4317178f6cf8bdbeb8ae5d7a4d35a68c266800557adeb3754be5306`;
- `tests/knowledge_spaces/unit/test_synthesized_publication.py` (four cases):
  `8b64451433a0a5f390d310b346325879d54d34ffe133080276f95c28cb48d60b`.
Reviewed isolated v15 schema fingerprint:
`653832489729cdba46dabb8522cef23167a689ad12ef0aff2c4b41019a1f9d7a`.

Contract audit is pass/zero errors, schema v2, 33 unchanged sources with input
SHA `23775511a9d56f9c3f32ec81ca28499401cc8f430575b1a95b11aaa92dec9d96`;
20 goals and 284 scenarios remain unchanged. Report:
`D:\CodexTestTemp\sbe-synthesis-ledger-contract-20260909.json`. Hot contracts,
publication.py and requirements-runtime.lock remain untouched under the Notion
handoff. Active runtime catalog stays v13; authoring catalog stays opt-in v14;
v15 is isolated only. No live data, listener, UI flag or company API changed.

Exact next packet: implement actual model-free planner/finalizer and one-batch
reduction handlers/producers using this ledger; inherit/revalidate the canonical
extraction job's current Part/Team/Responsibility authority before input loading,
immediately before provider calls and at commit; commit exact result with audit/
outbox; connect last map/batch and reconcile expiry/cancellation/purge to preserved
extraction. Add real cross-process competition, restart and role/source-change
tests. Then wire server-selected public upload and early source reuse. The
cost of repeated full predecessor replay, lifecycle/blob retention, actual
complex PDF/PPTX/DOCX uploads per account, compression/answer/graph quality,
multi-pass answer reading, browser and target load remain required. The last
real-model synthesis probe remains failed; these fake-model checks do not
supersede it. Host-runtime activation and company-model gates remain open.

### Durable workflow integration ownership — 2026-09-09

The intervening clarification changed no implementation state (no progress
toward this gate); the next safe action is the actual worker connection. Root
owns new services/wiki_synthesis.py, migrations/v016_synthesis_workflow.py,
migrations/synthesis_workflow_catalog.py, and unit/test_wiki_synthesis_workflow.py,
plus narrow opt-in hooks in services/wiki_authoring.py. v15 remains frozen.
The additive v16 intent freezes synthesis policy at upload enqueue, before map
jobs can run; otherwise a restart could silently change reduction policy.
Immutable planner bindings and worker reconciliation connect completed maps and
complete reduction waves to one model-free planner/finalizer. Each reducer job
executes one provider call. No full synthesis loop runs inside a claim or writer.
Existing independent reviewer remains report-only. Held shared contracts,
publication.py, runtime lock, active catalogs and live listeners are untouched.

Independent review expanded this packet to exact planner result/effect binding
and the shared deterministic snapshot-ID helper in repositories/wiki_synthesis.py.
Current-role tests exposed a real kernel cancellation gap between claim and
start: run_claim let the cancellation signal escape before its normal terminal
convergence. Root also owns the narrow worker.py start-cancellation branch and
its full existing kernel regression run. Cancellation retains exact lease/owner/
attempt checks; no authority is relaxed. Notion returned the 33 hot inputs and
publication.py after terminal S0A rebind; they remain unchanged in this packet.

### v16 actual-worker implementation and reviewed boundary — 2026-09-09

The optional workflow now freezes the synthesis policy and all three durable
attempt ceilings before any map job starts. Ordinary canonical-kernel polling
reconciles completed map jobs into the initial planner, and each whole completed
reduction wave into the next planner/finalizer. It performs no source-body replay
or model call inside the reconciliation writer. A planner reads/replays outside
transactions, then atomically commits either a whole wave plus all bound reducer
jobs or the final reduced snapshot. Each reducer claim calls exactly one batch;
receipt, exact job success, audit and outbox commit together. The combined
planner/finalizer uses completed input round 0/N, not final output round N+1.

v16 adds immutable intent and planner bindings without rewriting v15. An intent
blocks map-only ready even before v15 config exists; subsequent config must
match that intent. Planner success now identifies either the exact wave/hash/
batch count, or snapshot/content and manifest hashes+sizes/mode/last input round/
final output wave. SQL rejects absent/foreign/extra/mistyped effect results.
The shared snapshot-ID helper preserves v15's prior deterministic identity.

Independent review found and root fixed unbound-job cancellation poisoning and
cross-kernel authority composition. Reconciliation now considers bound planner/
batch jobs only, and map+synthesis workflows must share one actual kernel object.
New immutable synthesis events use one effect aggregate per event, rather than
pretending many changing run events are all version 1. Older map events are
unchanged; full projection/outbox consumption cost remains a runtime/load gate.

Current-role tests exposed a genuine kernel gap: cancellation between claim and
start escaped before domain convergence. run_claim now acknowledges that exact
still-live claimed attempt without reading bodies or calling the provider. An
expired/lost claim still cannot commit or cancel another worker's newer claim.

Terminal disjoint regression runs on the frozen product:
- Existing map-workflow + v15 storage + reduced publication: **58 passed in
  180.33 s**, `D:\CodexTestTemp\sbe-synthesis-workflow-regression-20260909.xml`,
  SHA `e2e567341aa1d6722b6844d01afd0d57c573fa9df686f987f82983310f55fd76`.
- Existing kernel + genuine multiprocess integration: **20 passed in 53.21 s**,
  `D:\CodexTestTemp\sbe-synthesis-kernel-regression-20260909.xml`, SHA
  `f08b4b064f37e38e44a1dd0502d14319fc6282f152fcfcb9c2cbdaf4f017ffba`.
- Independent targeted new workflow cases: **10 passed in 90.72 s** (overlapping
  root's new tests, not additional unique coverage), eight planner result
  mutations plus fresh-process continuation and two-process planner competition.
  `D:\CodexTestTemp\sbe-independent-v16-workflow-targeted-20260909.xml`, SHA
  `889c04f93c29df48c6585b60e3a263b4d09fb073a2b92f14987ddae69563be60`.
  No remaining P1/P2 was found within that exact independently reviewed boundary.
- An added unbound cancelled-job poison regression passed **1/1 in 10.20 s**,
  `D:\CodexTestTemp\sbe-synthesis-workflow-unbound-20260909.xml`, SHA
  `56793ee33223deac551ca534fcf039fbd322ff5a18bfff38c192d941e259730e`.

Pre-final test history is retained as failure evidence, not silently relabeled:
new postcondition initially returned a scalar instead of no violation rows;
the event writer used a duplicate schema_version keyword; both were fixed.
A stricter bound-only reconciliation query initially had an extra closing
parenthesis and was corrected. Fixture errors included missing account-change
timestamp, an illegal disable of a current Part leader (replaced by explicitly
labeled epoch-fence tests plus actual higher-role service revocations), the
publication helper's wrong call signature, and an incorrect expectation that a
60-second competing worker lease expires immediately after cancellation. The
competition fixture now acknowledges the exact winning attempt through the real
kernel. Earlier 9-pass/22-pass/9-pass subsets overlap the final tests and do not
constitute full workflow completion.

The four-author worker/publication matrix uses six UTF-8 upload-service jobs and
eight published items: two independent personal+Part uploads, two private higher
leader uploads, one native Team upload and one native Responsibility upload.
Part contains both authors' shared items, Team/Responsibility add only their
authorized native/inherited items, and four personal sets stay isolated. Exact
authored bodies are checked against canonical publication/search projections.
These are fake model responses and a fake blob stager; no rich-file parser,
physical original download, graph semantics, browser response quality, ten-file
per-account coverage, or capacity result is implied.

Unchanged boundaries: no public upload route selects this workflow yet; callers
still explicitly prepare/enqueue the source plan and select the resulting
authored snapshot. The current active catalog is v13, optional map catalog v14,
optional ledger v15, optional workflow v16. No production migration, listener
restart, model call, live corpus/account change, or Team/Responsibility UI flag
change occurred. Company API and host-runtime activation holds remain.

Next integration: add the durable model-free initial source planner after real
extraction, freeze policy before that job, and make public publication select
the final authored snapshot server-side (or explicit extraction-only failure),
with early exact-source reuse. Finish finalizer/preview-expiry and late-ready
failpoints, source/blob retention, metadata envelopes, replay/poll/outbox cost,
true simultaneous multi-author accumulation, then complex PDF/PPTX/DOCX and
real-model compression/answer/graph/browser validation. The last real model
probe remains failed. None of these fake-provider passes supersedes that result.

### Final worker checkpoint — 2026-09-09

Final new-file run is terminal exit 0: **39 passed in 321.72 s**, handle 15526,
`D:\CodexTestTemp\sbe-synthesis-workflow-final-20260909.xml`, SHA
`35d7dc603f4d3e55808a15cef6d7ec1468cd644d4f1fa2c009110b2f42fd5af3`.
The unbound-job test was added separately after that run collected: its one
passing case brings new coverage to **40 unique cases**, not 49. Together with
the 58+20 disjoint existing regressions, root verified **118 cases**. Independent
10-case replay overlaps these and is not added to that total. The final run
includes planner pre-body/after-replay/commit epoch fences, actual Team/담당
role-end service calls at four reducer boundaries, normal and zero-reduction
completion, purge/exhaustion convergence, exact partial-map enqueue replay,
atomic wave/ready result rollback, restart, competition and multi-author
publication/search. It is still not full-goal completion or activation evidence.

Frozen paths under code_assist_v1 and SHA-256:
- `knowledge_core/services/wiki_synthesis.py`:
  `6684eb9c67a7e9850e62612a3cb04ea49eb8749e42d3944e293c4c269296fe57`;
- `knowledge_core/services/wiki_authoring.py`:
  `908823c2e574a1a2531f9dc14d8ca600811d425bdbf61e9bc2a3701681f96f18`;
- `knowledge_core/repositories/wiki_synthesis.py`:
  `3181b3239a6fb9ccc9d0d2e2ef7f55351b1a03fec58672c1a1d34b0160498d7f`;
- `knowledge_core/migrations/v016_synthesis_workflow.py`:
  `ffbc840d8135a8f8331e2634a1d263e4e252c370f8c3f565dac2c95e92f8b9ef`;
- `knowledge_core/migrations/synthesis_workflow_catalog.py`:
  `c47df111d00b877f0f299f8308e7ec8ee0dfe70b508bf4ecdc833401035aba57`;
- `knowledge_core/worker.py`:
  `dd550ca0415c693075c0bdc094dd6a2bab69d218f3beb2cfae799c99483dad72`;
- `tests/knowledge_spaces/unit/test_wiki_synthesis_workflow.py` (40 cases):
  `4be2c92535eeef988c2816e75c2a4f42bcbaf35f464e33115b5164e9b5e2695d`.
Optional v16 schema SHA:
`4b40bac740d3c283472a2b71e18badf1b9a93a46bcaa2f5a9befcacbb4c1a322`.

Contract audit remains pass/zero errors, report schema v2, 20 goals/284 scenarios,
33 unchanged sources, input SHA
`23775511a9d56f9c3f32ec81ca28499401cc8f430575b1a95b11aaa92dec9d96`.
Report `D:\CodexTestTemp\sbe-synthesis-workflow-contract-20260909.json` SHA
`605184bafeafe4ad5b91049b81b191655b0fe6d74ecc9f4f325c462c940b9f8e`.
The publication and runtime-lock hashes remain the previously returned/held
identities. All root test and audit handles are now terminal, and the independent
reviewer is finished. This turn is verified implementation progress; the full
objective stays active with the public-entry/real-model/load gates above intact.

### Automatic upload entry packet — ownership, 2026-09-09

The intervening user clarification was read-only, not implementation progress.
Root now owns `services/publication.py` (returned by the Notion owner),
`services/wiki_authoring.py`, `services/wiki_synthesis.py`, new
`services/wiki_upload.py`, new `migrations/v017_wiki_upload.py`, new
`migrations/wiki_upload_catalog.py`, and new
`tests/knowledge_spaces/unit/test_wiki_upload_workflow.py`, plus this evidence.
Paths are relative to `knowledge_core/` unless prefixed with tests. The runtime
lock remains outside root ownership. The existing independent agent reviews
only its independent-baseline report; root integrates all product changes.

Implement atomic optional upload-policy registration inside the new queued
preview transaction; metadata-only source-planner scheduling after exact
extraction success; model-free source-plan preparation outside transactions;
same-writer run, synthesis intent, map jobs, source-plan outcome, audit/outbox
and job success. Freeze all model/work/chunk/synthesis policy before extraction
escapes. Legacy operation replay never backfills or adopts new deployment policy.
Retain a pre-run terminal outcome when extraction, authority or planning fails.
Public server-owned final publication selection, early equivalent-source reuse,
status/UI integration, activation and real-model/load evidence remain required
unless subsequently implemented and verified; this packet cannot waive them.

### Upload-entry implementation and independent review

The optional v17 catalog now retains an immutable preview/extraction request,
initial planner binding, and mutually exclusive planned/terminal source outcome.
The upload-preview hook executes after the newly queued preview and extraction
job, before the two operation commits. Either replay branch bypasses registration,
so deployment policy changes cannot backfill an old request. Registrar failure
rolls back all canonical preview/artifact/job/request/audit/outbox/op effects.
It does not pretend that staged but uncommitted CAS bytes have been published.

Ordinary kernel polling schedules one eligible initial planner after actual
extraction success. That planner prepares the complete immutable source outside
transactions and atomically registers the authoring run, every map job and v16
synthesis intent using v17's stored chunk/model/work/synthesis policy. Exact
source/actor/current-role/claim fences are checked before reads, after planning
and at commit. SQL requires the running bound planner and exact seven-field
successful effect result. Metadata-only reconciliation handles source/preview/
job failure even when no authoring run was ever created. Terminal dispositions
never fabricate an authored snapshot and do not delete the raw extraction lane.

Independent review found three draft defects: self-cancelling a terminalizing
planner (which lost failed/dead/error semantics); allowing a storage conflict
to escape kernel convergence; and freezing synthesis parameters but not every
downstream retry ceiling. Root fixed all three. The v1 request records source,
map, reduction and synthesis-planner durable attempts. A mismatching v1 worker
implementation fails closed; changing that implementation requires a new
request/handler version, never silently increased work on an old upload.
The new current-job exclusion retains failed/dead and its durable error, while
metadata reconciliation can cancel a distinct bound active planner.

The first draft test process loaded code before the terminal-self-cancel fix:
10 passed, one work-budget failure then failed kernel cancellation CAS, 46.87 s,
`D:\CodexTestTemp\sbe-upload-entry-first-20260909.xml`. This was a real draft
failure and is not counted as passed. After the fixes the earlier complete file
ran **28 passed in 119.33 s**, `sbe-upload-entry-second-20260909.xml`.
After adding explicit attempt ceilings, six added/focused cases passed in
69.99 s (`sbe-upload-entry-additional-20260909.xml`, 28 deselected). These
overlap the pending final full-file run and are not additive coverage.

That six-case checkpoint includes ten actual UTF-8 upload-service/extraction
jobs coexisting before source planning and LLM work. Member and peer each
publish personal, Part, and both; the native Team leader and Responsibility
executive each publish personal and explicit native. Twelve items are created:
four Part-shared, one Team-native, one Responsibility-native, six private.
Exact authored bodies are published and indexed. Authorized aggregate retrieval
contains precisely the expected shared descendants; private sets remain isolated.
This is fake model prose and a fake blob stager, not ten rich files per account,
real original-file download, semantic graph/compression/answer or load evidence.
Final publication still selects the snapshot through an internal command in
this test; the public server-owned selection/status gate remains open.

Independent bounded run: **5 passed, 29 deselected, 17.44 s**, valid prepared
policy substitution, registered-preview bypass, two attempt-drift checks and
retry exhaustion. JUnit `D:\CodexTestTemp\sbe-independent-v17-entry-bounded-20260909.xml`
SHA `4c2c12c76d9aedcffb100fd6a12dc21af13ee3408455e62f529dd43e8f20c639`.
It overlaps root cases. An earlier independent three-case run observed a
moving DDL/catalog checksum and failed setup, not the tested product behavior.
No remaining P1/P2 was found inside the independently inspected core boundary.

Important public integration gate: `extraction_only` here means no LLM Wiki and
preservation of the underlying extraction disposition. For a parent cancelled
before extraction there can be zero extracted bodies. Never render that receipt
alone as extraction success; status/source selection must check actual parent,
preview and retained body. Public final-source selection, early exact-source
reuse, preview/status/UI, rich files, real models, capacity, retention and target
runtime remain incomplete. No live migration or listener/account/corpus change.

Publication file was temporarily handed to Notion on its explicit short hash-
rebind request at SHA `8f4c0d27eff2e34e03cf9d64a5896fb992df551f49d10e54d7798abaa7853a1b`;
root will not edit that file until handback. Other owned v17 work continues.
No shared contract input changed: schema-v2 audit remains 20 goals/284 scenarios,
33 inputs, zero errors, input SHA
`23775511a9d56f9c3f32ec81ca28499401cc8f430575b1a95b11aaa92dec9d96`.

### Frozen upload-entry full-file result

The complete new file is terminal exit 0: **35 passed in 211.24 s**, handle
38538, `D:\CodexTestTemp\sbe-upload-entry-full-20260909.xml`, SHA
`4469ef7925217e91cea0cde8b8171d84d64ad0b68543c8134bb336c582c92434`.
This includes the actual two-process initial-planner competition: two fresh
worker processes reconstruct the optional registry, only one plans/commits,
one run/map set/results is retained, and frozen 512-byte chunk policy is restored
despite a new worker's default policy. It also includes all eight actual native
Team/Responsibility role-end boundaries (before binding, before source body,
after planning, at commit), current-job error preservation, partial map-bind
rollback, lost acknowledgement, exact replay, non-authoring legacy replay,
source work-budget rejection without prefix success, preview-service cancel,
all target modes and the ten-upload/twelve-item multi-author publication matrix.

Frozen product SHA-256 under code_assist_v1:
- `knowledge_core/services/publication.py`:
  `8f4c0d27eff2e34e03cf9d64a5896fb992df551f49d10e54d7798abaa7853a1b`;
- `knowledge_core/services/wiki_authoring.py`:
  `41be11b445308c02c19eb7fce20bf10a1e238dddece93785d472b4a97b64126d`;
- `knowledge_core/services/wiki_synthesis.py`:
  `800cb0e573a4a03bd2b806f78657d08d92b2fedcb0bff724f6cc5faa6916667d`;
- `knowledge_core/services/wiki_upload.py`:
  `839294ab4eec4596f98cf2372d1d3fa3d1957ed15b216ddc52f7c0fae9e32d9c`;
- `knowledge_core/migrations/v017_wiki_upload.py`:
  `5e770f8565612afb9f31b29b126c11622112adab01ed77e83b0fff4768cb1515`;
- `knowledge_core/migrations/wiki_upload_catalog.py`:
  `BCBBECE20FAD2428A3C1427AA68700A4A41695551C2CF78BB91CE3CF2DD5F0B0`;
- `tests/knowledge_spaces/unit/test_wiki_upload_workflow.py`:
  `1e7bc9b4783e8e0cf0ecc3d5c40290c422f980889778101e91bc5007e1b66ad2`.
Optional v17 physical schema SHA:
`433ef8621df188e108098be7804efd3c3cdb9fb20acdd74e50ed56e376afbf6f`.
Contract audit report `D:\CodexTestTemp\sbe-upload-entry-contract-20260909.json`
SHA `605184bafeafe4ad5b91049b81b191655b0fe6d74ecc9f4f325c462c940b9f8e`.
Existing preview/publication and map/synthesis regression handles remain live
at this checkpoint; their terminal results are required before handoff.

### Upload-entry regression completion

Both previously live root handles are now terminal exit 0:
- 23807: **96 passed in 346.35 s**, existing preview service, authored publication
  and reduced publication tests. JUnit
  `D:\CodexTestTemp\sbe-upload-entry-preview-regression-20260909.xml`, SHA
  `5f109748bdc7ed7608aa471e95ccad6079dcc23c71848ab98b8ecc20d1159456`.
- 93025: **58 passed in 440.77 s**, existing chunk and synthesis workflow tests,
  including prior exact-job, role, lost-ack and process behavior. JUnit
  `D:\CodexTestTemp\sbe-upload-entry-worker-regression-20260909.xml`, SHA
  `fc97579dcbbbd08e97df3d0432832be08f103ae56b37a502d7703e1732bac872`.

The final 35 new + 96 + 58 existing cases are **189 disjoint passing tests** on
this product checkpoint. Earlier subsets and the independent five are not
added again. All root test/audit processes have finished. The independent agent
is doing a report-only next-packet source-selection/status design review; it is
not an unfinished claimed product-verification pass for this checkpoint.

This goal turn made verified implementation progress. The full objective is
still incomplete: user-facing publication must select the final authored source
without a client snapshot ID, statuses must distinguish pending/failed extraction
from authored readiness, early exact-source reuse must avoid needless provider
work, and retention/expiry/load/rich-file/real-model/cross-space browser evidence
is still required. Publication remains frozen for the short Notion handback.
No production catalog, listener, account, live data or company model was changed.

### Public source-selection/status packet — ownership

Previous goal turn is verified progress (189 scoped tests plus v17 implementation).
Notion has returned publication.py at the recorded SHA after terminal single-file
rebind. Root now owns this next packet: `knowledge_core/services/publication.py`,
new `knowledge_core/services/wiki_publication.py`, `knowledge_core/canonical_routes.py`,
`static/knowledge_publication.js`, the canonical browser transport if required,
new `tests/knowledge_spaces/unit/test_wiki_publication_selection.py` and focused
browser/publication tests, `contracts/schemas/publication.schema.json`, associated
API/UI documentation, and this evidence. Contract paths are below
docs/knowledge_spaces. Any changed audit inputs invalidate the prior Notion
rebind until a new frozen checkpoint; the Notion runtime lock is still excluded.

Keep client publication intent/hash stable and resolve the authored body only
after committed-operation replay has returned. Pending must not consume a
publication operation ID. Ready/extraction-only/unavailable status is an overlay,
never a rewrite of stored extraction-ready results. A failed/cancelled extraction
with no body is not an extraction fallback. Exact replay verifies the actual
stored authored selection or extraction-reuse receipt, not current readiness.
Normal routes continue to reject client-authored snapshot IDs. Keep all legacy
behavior unchanged when the opt-in selector/request is absent.

Additional reported activation regressions to reproduce, not silently ignore:
test_production_service canonical dependency composition hits missing migration
storage path; three legacy inventory tests report two unclassified UI calls in
static/agent_integrations.js (reported lines 15/165, discovered 749). These are
reported by the Notion owner, not yet root-reproduced or attributed to this packet.

### Public source selection — implemented candidate, final verification running

The intervening user clarification turn was read-only (no product progress).
This continuation changed product code and produced new isolated evidence.
Root additionally owns `tests/knowledge_spaces/unit/test_wiki_publication_browser.py`.
No live listener, account, corpus, active catalog or company model was changed.

- Both preview and publication services now automatically recognize immutable
  v17 upload requests; omitting constructor injection cannot bypass generation.
  A table-aware lookup preserves the active pre-v17 legacy path without creating
  tables or retroactively registering old previews.
- Normal HTTP intent contains no authored snapshot UUID. After committed replay
  detection, the server selects exact completed A2; existing first-stable-source
  logic may select prior authorized A1/E1. Publication revalidates the actual
  frozen body and targets. Pending returns retryable 409 before any operation
  row, so the exact same intent/operation ID can succeed after generation.
- Preview status is a versioned, metadata-only overlay, not an update to stored
  extraction-ready JSON. Generation outcome and current publication eligibility
  are separate: a consumed completed Wiki stays `ready` with publication false.
  Actual failed/cancelled extraction is unavailable; terminal generation failure
  with a retained extraction is explicitly extraction-only. Completed but purged
  authored source cannot silently become a raw fallback.
- Public result source kind is persisted with the operation: authored_wiki or
  extraction, including a fresh fallback. Legacy non-opt-in results omit it.
  Replay binds its presence to the immutable request and checks exact authored,
  extraction-reuse, or absent selection receipts without current source reads.
- Single/batch controllers wait beyond extraction until generation completes,
  label fallback, stop for unavailable work, and keep exact retry identities.
  Transport and JSON schema validate the additive status/source-kind shapes.

Independent review identified and root corrected three candidate issues: an
inline-only authoring fence incorrectly rejected publication-compatible blob
extractions; consumed generation was incorrectly labeled unavailable; fresh
fallback results lacked a public source-kind field. Review then identified the
plain-service bypass and source-kind presence/replay binding, now also corrected.
A reported raw v17-table read in a default service was from a moving intermediate
snapshot; the final candidate uses table-aware `has_request`, confirmed on disk.

Verification so far (overlapping, not additive):
- Initial new file: 8 passed/1 failed in 55.40 s; deliberate planner failure used
  a helper asserting every drained job succeeded. Corrected the fixture to assert
  its expected failed job explicitly, preserving the failure/fallback behavior.
- Earlier route/controller run: 56 passed in 80.85 s; expanded source file then
  13 passed in 160.93 s; these predate final review changes.
- Initial browser-only run: 4 passed/3 failed because its batch fixture supplied
  one file to a two-file-minimum controller. Corrected to two distinct sources;
  all 7 passed in 2.26 s. No product constraint was weakened.
- Blob/fallback review cases: 2 passed in 11.84 s. The legal blob fixture selects
  that form before initial extraction insertion; immutable retention guards remain.
- Plain-service and exact source-kind presence: 3 passed in 21.05 s, JUnit
  `D:\CodexTestTemp\sbe-public-selection-no-bypass-20260909.xml`, SHA
  `c013adf9fbeb96f2f703767a4b58178d5619cf6b410c84aab4b99536cd61f7ad`.
- Earlier pre-final legacy/upload run: 127 passed in 458.31 s. It is not the
  final default-selector regression; fresh full-file runs are still live.
- Model-free public tests cover all five target modes, ten simultaneous ready
  extractions -> twelve independent/shared items, exact 4/5/6 Part/Team/담당
  visible sets, four actors' personal isolation, cross-author A1/E1 reuse and
  replay after purgeable candidate bodies are removed. HTTP tests include a
  fresh extraction fallback, UUID rejection, pending retry, schema and consumed
  status. Node execution is not a rendered live-browser proof.

Frozen candidate product SHA-256:
- publication.py: `f5fabdee88fcc216e3574d361543eabf993b55d5c41902ccac690a3042fa6d0f`
- wiki_publication.py: `2b94692d4fbfdb5314900cc2db247e098ce5ce994d007ac1dfb348cd55ccf40e`
- canonical_routes.py: `2f1894f7c9b8938e761154031b80896731b43fdfdd8048fdb8239fb13f81126e`
- knowledge_publication.js: `15b34418a7a800461961d0b218bb66b4a15e4f060759e539da7cb3b30ab599a1`
- knowledge_core_transport.js: `cdffbc99e5e95ba340df99277383953e64fdc4f706a3933e90169a8e8554f29c`
- test_wiki_publication_selection.py: `55e55c68b1dc3126953207240af295d6ca8c54aae7d956b458553bd1b3ce46e5`
- test_wiki_publication_browser.py: `bd7ee92346ea818673f079abae09e46de6ce9e83ba01b1e11bf26174488fb847`

Schema-v2 contract audit is pass/zero errors, still 20 goals/284 scenarios/33
inputs. New input SHA `4cc7a2f12c61eb7a6c16c30918f09ab236c30b477deef832f8c24ffe3ec99b92`.
Report `D:\CodexTestTemp\sbe-public-selection-contract-final-20260909.json`, SHA
`776aeb93b7a42e9ef17d47213c21b9454106fcb744b6b3102aa90e5b46ebc6c7`.
Notion has been told the former rebind is historical, not current.

Separate reported activation checks were reproduced: **4 failed/11 passed in
16.26 s**, `D:\CodexTestTemp\sbe-activation-regression-repro-20260909.xml`, SHA
`41897b617695808992e5c8f9f74c75bb95b7bc61f5340605f166fa17ca7a880e`.
The production composition unit uses a fictitious C:\CodeAssistData path and
now reaches the real stager path verifier; this is not proof of a live failure.
Three inventory checks see 749 calls instead of 747 and two unclassified
agent_integrations.js calls. Those files/gates remain unresolved, not silently
excluded from activation criteria. No guard or expected count was relaxed.

The original live provider probe remains failed, and real complex rich-file
compression, actual multi-author browser conversations, early source reuse,
retention/expiry races, worker leases under slow providers, large-corpus resource
measurements, multi-pass answer coverage and target/company runtime gates remain
required. The active goal and SBE-8.13 are not complete.

### Public-source frozen completion and activation-regression repair

On resumption the root collected the specific previously live handle 58211,
which was terminal exit 0: **130 passed in 468.03 s**. This completes the pending
legacy/v14/v17 regression, not the full goal. JUnit
`D:\CodexTestTemp\sbe-public-selection-frozen-regression-20260909.xml`, SHA
`bc22aadf3169af28d6dd79c6a5a92e7764414b426fd070dece9ef205f4511444`.
The last final-source/HTTP/Node run passed **71 in 210.88 s**, JUnit
`D:\CodexTestTemp\sbe-public-selection-frozen-20260909.xml`, SHA
`22731f3dd2ee32d5f0fc391ec8ade3081f55103e51360cc620c4c5d110c42410`.
The final Node-only wording regression passed **45 in 14.96 s**, JUnit
`D:\CodexTestTemp\sbe-public-source-browser-frozen-20260909.xml`, SHA
`be3756f2ab82cbb905a0188d5a9f368b069d96ba6694d7b4ea3110f834c4abd0`.
Parsing all three XML files confirms **202 distinct classname/name pairs**,
zero failure/error/skipped records; the 45 are not simply added to 71 and 130.

Final UI SHA is
`db6a82abf7094ba79ea102e8e42811853b2a83e6ec1901d7ea5a6c0f4eaf17a4`;
browser-test SHA is
`4cbfecd5e18f612749541364db93f9e64ca6acf58b042a5b072c39acd73a1755`.
Other product/source-test hashes in the preceding frozen list are unchanged.
The last wording fix says generation is ready without promising the new A2
will replace an existing target's stable A1/E1. Independent review reported
no remaining P1/P2 in this bounded snapshot, with six backend/HTTP/replay checks
and one final wording check passing separately; see independent-baseline.md.
No real-model compression or live-browser answer gate is inferred from this.

The intervening clarification turn otherwise made no product edit; collecting
the terminal process changed the next action from waiting to checkpointing.
This goal continuation repairs the four previously reproduced readiness
regressions. Root owns `tests/knowledge_spaces/unit/test_production_service.py`,
`tests/knowledge_spaces/unit/test_legacy_inventory_code.py`,
`docs/knowledge_spaces/CURRENT_SYSTEM_INVENTORY_MAP.json`, its human inventory
description, STATUS and this evidence. Production implementation, the scanner,
runtime lock, active catalog and live stores remain unchanged.

- Production composition now uses actual isolated storage rather than a
  fictitious C:\CodeAssistData. Its real filesystem stager is asserted, including
  the exact root/database binding. A second case proves a missing blob root
  rejects before creating the application/host and leaves dummy DB bytes intact.
  The fixture supplies an isolated secret instead of consulting ambient state.
  Full file: **20 passed in 6.93 s**, JUnit
  `D:\CodexTestTemp\sbe-activation-storage-fix-20260909.xml`, SHA
  `489bcaca2fca3250d60804a09fd83dbc65eaaa296bdaf0ac97f99c6cefb2d62a`.
- Inventory now explicitly maps the two integration UI sites to the canonical
  bridge (`replace`, not exclusion), after read-only inspection of the exact
  browser/route/bridge. Counts are 749 = 393 mapped + 356 retained/excluded;
  no unclassified calls or unmapped submitters. New tests bind both exact IDs
  and demonstrate that another mapped call in copied real JS still fails the
  callsite seal. Full file: **16 passed in 15.67 s**, JUnit
  `D:\CodexTestTemp\sbe-activation-inventory-fix-20260909.xml`, SHA
  `3dc1fe0daacf962a43d6e68cff140268d254b967439740796aa6f5a0dc7d6fdc`.
  The scanner's nested-package coverage limitation is documented, not concealed.

Fixture SHA `ec18377d3b2c0e7bb847318e96aa8c8530ad3572bb3bf808450c4f86ecc7682b`;
inventory-test SHA `8910581816306072b2ce4e6dc5d09762fc4a254ace6fefa28f161430e6335fe4`;
mapping SHA `5931d0f4531f32b0aab3812edafc6a45e077e9badc22690fd562569a9ec8f8aa`.
The already-read 33 contract inputs were rehashed before changes: zero drift
from the prior `6e15b441...` report. STATUS/inventory now change, so the next
audit/checkpoint must bind their current content. Adjacent integration tests
are running on exact handle 59455; no pass is claimed until its terminal result.

Handle 59455 is now terminal: **31 passed in 80.61 s**, JUnit
`D:\CodexTestTemp\sbe-activation-integration-regression-20260909.xml`, SHA
`03ec8b7f83a1b32e3aa9fd0060c03a7b820aa9343f53a484f05686656b6993b7`.
These include actual canonical search/read/ask/publication replay with a fake
model, two-process publication including lost receipt recovery, and the real
auth gate's machine/browser route separation. The storage 20, inventory 16 and
integration 31 files/cases are disjoint **67 passes**. Independent inventory
review passed two separately reported targeted cases and found no P1/P2 in this
bounded repair. It correctly notes the seal cannot detect a changed dynamic
target's meaning or a new relative api consumer represented by the same helper;
the human inventory now explicitly records those limits. No wider gate is waived.

### Many-small-body loading — measured bottleneck and local-handle repair

Next root-owned implementation: `knowledge_core/services/agent_context.py` and
new `tests/knowledge_spaces/unit/test_context_body_scale.py`. No schema, flags,
catalog, query relevance, corpus scope, body allocation or source-artifact query
policy changes. Measurements use actual isolated canonical DB composition,
synthetic tiny unique bodies, no model, and no live account or production data.

The first fixture accidentally repeated only five body values; retrieval
correctly collapsed those exact duplicates, so both expectation checks failed.
This was fixed in the fixture using unique `가{index}` bodies (4–6 UTF-8 bytes
at 128 documents), without changing product deduplication. Failed-run JUnit
`D:\CodexTestTemp\sbe-context-many-small-baseline-20260909.xml`, SHA
`c1933967310bec714f900b2e6d4595f47b6f517972f326989a3135ca4fad9309`.
The corrected pre-change baseline passed 2 cases in 13.15 s; JUnit
`D:\CodexTestTemp\sbe-context-many-small-baseline-unique-20260909.xml`, SHA
`4695d258eaa06d0bd004c55397b046b9a04a10ef6070a28faa637ad8ebffdbb5`.

| Additional / delivered documents | Baseline DB opens / SQL / compose seconds | First repaired DB opens / SQL / compose seconds |
|---|---|---|
| 8 / 10 | 15 / 261 / 0.850245 | 6 / 198 / 0.405793 |
| 128 / 130 | 135 / 1460 / 7.283499 | 6 / 557 / 0.685030 |

The problem was one factory open per inline body, repeating path/schema setup.
`_context_body_reader` now lazily retains one read-only handle local to a single
materialization call. Each inline read still has its own short DEFERRED
transaction; no read snapshot spans bodies or CAS I/O. It closes on all exits
before the final fresh metadata/authority check. There is no self/global handle,
cross-request sharing, full-corpus body cache or loss of per-body integrity.

For the 128-case comparison, exact excerpt bytes (718) and JSON payload bytes
(112309) are unchanged. Python tracemalloc peaks were 1932967 versus 1936856
bytes: this is an I/O/setup improvement, not a demonstrated memory reduction.
The measured seconds include instrumentation and are single local samples,
not p50/p95/p99 or company-model/target-server latency. The JSON is already
larger than the 96 KiB *body* budget because identities/provenance also cost
space; a full serialized-input/token bound and overflow iteration remain open.

First existing body/CAS/four-scope plus two-scale run: **37 passed in 75.43 s**,
`D:\CodexTestTemp\sbe-context-reader-first-20260909.xml`, SHA
`49c3d167bb8e45c071acba3977ae95dd2246c7bec123b5e446b2c162fa10bd0f`.
Expanded empty/all-CAS/mixed/inline-integrity/CAS-failure/interleaved-invocation
checks: **8 passed in 12.86 s** (overlapping),
`D:\CodexTestTemp\sbe-context-reader-scale-final-20260909.xml`, SHA
`c765bca9f54005d88b4d26100f1f01ad2088807c199b3d6302f4e5b353e6555e`.
New all-CAS-before-inline, consumer-exception and 1024-document cases are in
the fresh final run on handle 90791; broader retrieval/Agent regression is
running on handle 54693. The product is frozen at SHA
`dedc80e4418fd5d0f552227e6125dec77a9ef7347398bd3322d9209a3325d53c`.
Independent review is checking this exact local-handle boundary. No completed
capacity, actual multi-author rich-file upload, or real-provider quality claim.

### Local-reader frozen verification and next limiting factor

Both exact root handles are terminal exit 0:
- 90791: **46 passed in 102.15 s**, complete scale/lifetime/body/CAS/four-scope
  tests, `D:\CodexTestTemp\sbe-context-reader-complete-20260909.xml`, SHA
  `bb54c4a035f7bae9879c698f39cf52ecf7e46d21da0c89a5d254f427cdaaf737`.
- 54693: **62 passed in 80.82 s**, complete canonical retrieval, Agent executor
  and hybrid retrieval files, `D:\CodexTestTemp\sbe-context-reader-adjacent-20260909.xml`,
  SHA `84e85eebe6d53f0b4b5b842842c393b5c8fdc3e0a8181efad628410eecf71653`.
XML inspection finds **108 distinct classname/name pairs**, no failures,
errors or skips; preceding subsets are not added again.

The final instrumented measurements are:

| Additional / delivered docs | Opens | Body SELECTs / all measured statements | Compose seconds | Excerpt / complete payload bytes | Python traced peak bytes |
|---|---|---|---|---|---|
| 8 / 10 | 6 | 10 / 198 | 0.402610 | 92 / 13295 | 1592111 |
| 128 / 130 | 6 | 130 / 557 | 0.727008 | 718 / 112309 | 1936856 |
| 1024 / 1026 | 6 | 1026 / 3245 | 3.221379 | 6118 / 753953 | 4269653 |

The before/after 128-case has identical exact payload/body byte counts and
delivered identities, with connection count fixed instead of per body. This
does not remove the linear body/provenance SQL work or metadata footprint.
Python tracemalloc is not process RSS. Single runs under test instrumentation
are not latency percentiles or a supported concurrent-client capacity result.

Independent execution passed **9/9, 14 deselected, in 13.46 s**: all seven reader
modes, interleaved lifetimes and a real legal R1-to-R2 edit during CAS read that
must reject the old context. JUnit
`D:\CodexTestTemp\sbe-independent-context-body-reader-20260909.xml`, SHA
`e835ea3cb38dfc3e8d00b09a456cb8e3779b44c9e38af3a05d124de108ba7393`.
No P1/P2 remained in the bounded review. Frozen product SHA is the DEDC80E4...
value above; final scale-test SHA is
`e50842bf5799f9ff70ce729aa5099305f08e985f0047ee79f2f9bc4d757d3909`.

This turn made implementation/evidence progress; it did not redefine the goal.
The next answer-path task must measure/bound **complete serialized input**, not
only body bytes: 1026 tiny bodies already produce 753953 JSON bytes. Overflow
iteration must preserve requested coverage and exact source references without
silently reinstating a document-count cap. Long rich-file authoring quality,
multi-author actual uploads/conversations, early source reuse, expiry/retention,
slow-provider worker leases, target RSS/concurrency and company-model acceptance
remain open. No listener, live corpus/account, migration, feature flag, runtime
lock or company call changed, and the active canonical catalog remains v13.

Final schema-v2 audit: **pass, zero errors, 20 goals, 284 scenarios, 33 inputs**.
Report `D:\CodexTestTemp\sbe-small-body-reader-final-contract-20260909.json`,
input SHA `5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa`,
report SHA `933a72661117f7b4f973cfe2447a67db7131bc8eeb5d52b958f238957bee95c3`.
Human inventory SHA `3f805a116bd987ee9b0e45eb2c3abc2bbd40e8c4e0d9bd6e207a72334693608b`;
mapping retains the 5931D0F4... hash above. All root processes are terminal.
The frozen hashes/results and the repaired four-check evidence were handed to
the existing Notion owner for its next owned rebind. Root retains publication/
STATUS/contracts ownership; runtime lock remains with Notion. This handoff does
not authorize activation or mark the full shared-Knowledge-Agent goal complete.

## Answer-input provenance factoring and provider preservation — 2026-09-09

This is implementation/evidence progress toward the original multi-author,
four-scope goal, not a narrowed completion claim. The previous explanatory turn
made no implementation changes; the active goal was revalidated before the next
safe change. The 33 inputs of audit `5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa`
matched at entry. Those inputs and publication.py remain frozen for the Notion
owner's requested immutable checkpoint; this evidence is outside that 33-file
input set. No listener, live data/account, migration, flag or runtime lock changed.

### Implemented boundary

- `answer_input.py` factors only the model-safe `source_documents` metadata,
  **after** `prepare_citations` has validated the existing turn-bound catalog
  and removed download URLs. Body item/revision/source-space fields and the
  existing citation handle identify exact duplicates. Additional artifacts,
  differing labels, missing legacy source-space information and unknown future
  fields remain in `citation_sources[].source_metadata`; exact source order is
  preserved. The inverse must reproduce the complete post-citation payload,
  not a less-complete fixture. The original server payload/catalog and final
  source/download records remain unchanged.
- Literal body, query, history/summary, manifest entries/counts, coverage ranges,
  UTF-8 sizes, temporal fields, relation semantics and mandatory-shared bodies
  are unchanged. No document count, title list or per-document body cap is added.
  Prompt v12 describes the metadata join generically; it adds no topic/query
  examples and preserves the general-assistant and tool-availability policies.
- `model_input_trace` measures exact canonical UTF-8 payload JSON and the exact
  two-message logical array at `executor_messages_before_provider`, including
  the system prompt, history and JSON escaping. It records content-free hashes
  and sizes. It explicitly leaves token counts/provider limits unknown; a byte
  receipt is not a claim that the configured model's context window fits.
- The canonical answer opts into the common OpenAICompatible adapter's exact
  request receipt even without inventing a byte limit. `requests` receives the
  already-serialized UTF-8 bytes; the result records the actual HTTP body size,
  SHA and resolved wire-model alias. Existing non-opt-in API callers retain
  their `json=` transport behavior. Provider fallback continues to create a
  fresh per-attempt record instead of reusing another attempt's receipt.
- Independent inspection found a **different downstream loss**: the legacy
  Codex prompt builder trims long prompts to head/tail at 60,000 characters.
  Canonical answers now explicitly propagate `preserve_codex_input=True` through
  the shared runner and bridge, so that builder does not drop middle evidence
  or split its JSON. Ordinary bridge callers retain the old default policy.
  No shell/web/skill restriction was introduced. This guarantees preservation
  at our prompt-builder boundary, not that a remote model accepts or fully uses
  an arbitrarily large prompt. Multi-pass packing is still required.

### Root terminal verification

1. Initial input/executor/four-scope body slice: **46 passed, 58.47 s**. This
   intermediate run overlaps the final groups below and is not additive.
2. Final input, actual-adapter-boundary and legacy provider/bridge routing:
   **51 passed, 20.75 s**, no failures/errors/skips. JUnit
   `D:\CodexTestTemp\sbe-answer-transport-20260909.xml`, SHA
   `82dd819963e435a9c3b2869523a5d6e8771bbe19c53205aa6ddb0dc528c3296a`.
3. Final scale, canonical executor, four-scope full/partial/CAS body handling,
   hybrid retrieval and citation regression: **142 passed, 134.06 s**, no
   failures/errors/skips. JUnit
   `D:\CodexTestTemp\sbe-answer-input-regression-20260909.xml`, SHA
   `98663d370e49faa98ee42d28b4db050674d7ab9681a83f5854a00eddee83084f`.
4. Inventory seal, full preview integration and canonical retrieval:
   **43 passed, 76.58 s**, no failures/errors/skips. JUnit
   `D:\CodexTestTemp\sbe-answer-input-adjacent-20260909.xml`, SHA
   `6d4bf2175e17abfb3a6a2cd2e35ce58771c47a40ebaf1950ae91dd35d381c24d`.

The final groups cover **236 distinct root cases** (disjoint test files).
The transport tests execute the real executor/common adapter. The HTTP case
intercepts only `requests.Session.send` and inspects a real `PreparedRequest`,
including exact content length/hash and every middle/late Korean source byte.
The CLI case intercepts only the child process, captures the prompt written to
stdin through the real bridge, and proves >60,000 characters remain complete.
Neither calls a real external model or the company network. Codex text-pipe
newline translation/provider-internal token handling is not measured by the
logical-message receipt. These are transport and structural checks, not answer
quality, compression-quality or production-capacity proof.

### Same-boundary many-small-document measurements

Both columns use the same final system prompt. Before means **post-citation,
before factoring**; after means the payload actually passed to the runner.
Earlier 753,953 bytes measured pre-citation default-spacing JSON and must not
be used as this before/after baseline.

| Delivered docs | Exact body bytes | Model payload before / after bytes | Logical messages before / after bytes |
|---|---|---|---|
| 10 | 92 | 14,033 / 11,917 | 30,099 / 27,787 |
| 130 | 718 | 126,175 / 97,873 | 153,163 / 122,265 |
| 1026 | 6118 | 875,444 / 650,894 | 974,112 / 729,046 |

At 1026 docs the logical message size decreases about **25.16%**, with exact
round-trip provenance and unchanged evidence, and the six-DB-open property is
retained. The logical request is still large; this is not an input-fit bound,
multi-pass completion or permission to expose every corpus to a single call.
Composition samples remain instrumented single runs, not target p50/p95/RSS.

Frozen product hashes for the root terminal runs:

- answer_input.py `c457723746231e3a8fbeb3678d7e8ab3e516213342916ee012f2eb4832a96923`
- agent_execution.py `8fa69c2ae3b6b0d9395e3059cd79c2558bb0c42a6698a2727474382ece9136c5`
- internal_llm.py `3b6abebb9844e3a060035bffcb133c40a73611714462e5e6b849eaa197c294f4`
- codex_bridge.py `64c0ba7f74de07c65ed2998ecd446680c059c8fb7fbffadf57373c965dbddb8a`

Independent frozen review found no P1/P2 in this bounded slice. **8/8 targeted
cases passed in 4.33 s**; JUnit
`D:\CodexTestTemp\sbe-independent-answer-input-transport-20260909.xml`, SHA
`2ea6c35456e36b7e02090818e338aa1ffdef6251833845180da0f9c8cce5093e`.
The independent report preserves the legacy source-space override qualification,
separates logical bytes from actual provider input, and does not treat these
results as multi-pass or company quality proof.

Required work remains full model-specific input/output budgeting and multi-pass
source reading, actual rich-file upload/semantic compression and cross-author
conversations/graph checks, the earlier lifecycle/load gates, and company-model
acceptance. This packet does not opt authoring/synthesis or ordinary chat callers
into the new canonical-answer bridge policy. The active catalog stays v13 and
Team/Responsibility UI remains disabled. STATUS/audit input edits remain on the
requested coordination hold until the Notion owner finishes its checkpoint.

Final read-only contract audit for this held-input checkpoint passed with zero
errors, 20 goals, 284 scenarios and 33 unchanged inputs. Report
`D:\CodexTestTemp\sbe-answer-input-held-contract-20260909.json` has input SHA
`5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa` and report SHA
`933a72661117f7b4f973cfe2447a67db7131bc8eeb5d52b958f238957bee95c3`.
Case-sensitive `(classname,name)` comparison of the three final root JUnits
confirms 236 unique cases; case-insensitive PowerShell grouping would incorrectly
collapse the distinct `wiki` and `WIKI` malformed-citation parameters. All root
test handles are terminal. The Notion owner received the new source hashes and
the scope of the shared-runner change without lifting its requested hot-input
hold. The next goal turn must continue real multi-pass/model-budget work, not
repeat these completed structural tests as a substitute for end-to-end quality.

## Successor: invocation-local sequential source reading — 2026-09-09

This is concrete source-reader implementation after the read-only user
clarification. The clarification itself is not counted as implementation
progress. The full multi-author/upload/answer-quality goal remains active.

### Implemented boundary

- New `knowledge_core/answer_reading.py` provides a next-only in-process
  `EvidenceReadingCursor`. It accepts no client/model item, revision, offset,
  cursor token or scope. `AgentTurnContextService.reading_session(request)`
  constructs it in the same private compose invocation as the accepted turn's
  context, using the original actor epoch, turn, binding/closure and canonical
  metadata. A public context bundle cannot reopen or widen it.
- The source catalog includes all selected ranked/supplemental documents and
  canonical mandatory sources selected by the existing maximum-four policy,
  **before byte allocation**. A selected fixed reference or pin omitted by
  the initial 96 KiB allocation therefore remains readable. The fifth mandatory
  candidate is not promoted by the reader. Noncanonical explicit bindings
  remain separately in the original bundle; these pages do not claim to read
  those bindings or an unselected corpus.
- Every page revalidates the original turn/authority plus the complete selected
  current-body and historical-reference metadata before and after body and
  provenance I/O. `revalidate()` exposes the same fresh fence for the future
  executor immediately before provider invocation and after provider return.
  A changed body revision, inactive item, cancelled turn, actor epoch, closure
  generation or fixed-reference availability invalidates the invocation.
- Page fragments preserve exact contiguous half-open Unicode-code-point ranges,
  full-source byte count/hash and source identity. Reconstruction across pages
  is byte-for-byte exact, including whitespace, CRLF, Korean, combining marks,
  astral emoji and ZWJ sequences. The page bound covers **raw body text only**;
  it is not a serialized prompt or token budget. It adds no document-count cap.
- Reads materialize one source at a time. A large source is loaded once for its
  subsequent pages; only that source may remain cached. Cache ownership is
  transferred out of the persistent slot while a page advances, and a completed
  source is released before loading the next one. Close/error discards callbacks,
  source descriptors and cached body. All DB handles are closed before a page
  returns; no SQL snapshot crosses provider I/O.
- The full selection's metadata remains O(N), and fresh whole-selection
  metadata/authority checks repeat per page. This is not constant-memory or
  constant-work retrieval, and no target-host RSS/latency/capacity acceptance
  is inferred from the byte bound or the isolated correctness fixtures.
- Newly read pages obtain revision-bound original-file provenance. Historical
  R1 fragments cannot acquire originals first attached to R2. Shared pin/ranked
  identities are read once, while their selection roles are retained.
- Existing `compose(request)` callers retain their original bundle API and
  single retrieval. The new reading contextmanager is **not yet connected to
  the live answer executor/worker**; adding it is not a claim that multi-pass
  answering is now live on 10221.

### Verification scope and findings

The isolated fixtures exercise actual canonical DB reads, accepted bound
conversations, membership, native/inherited scope and source-artifact lookup.
They are not live uploads or model-quality evaluations.

- Pure paging covers six page sizes, invalid/no-progress budgets, no prefetch,
  process-local serialization refusal, empty selection, terminal failures,
  cache release before the next source, and exact reconstruction.
- Real service races cover five changes (epoch, generation, turn cancellation,
  legal revision edit, legal item deactivation) at three points (between pages,
  during CAS read, after a simulated provider interval). A separate check
  measures one lexical retrieval, no returned DB handle, and no repeated CAS
  load for successive fragments of the same document.
- Four-scope inline/CAS fixtures now reconstruct full native/inherited bodies
  and verify revision-bound originals through the reader, in addition to the
  existing deterministic answer/citation/persistence/replay checks.
- An 80-document isolated corpus has ten long documents for each listed
  author/space: personal, foreign personal, Part A member, Part A peer, sibling
  Part A2 member, other-Team Part B member, Team leader and Responsibility
  executive. Each selected scope exceeds 96 KiB. Personal reads only personal;
  Part A includes both authors; Team includes its Part sources/native space;
  Responsibility includes its authorized descendants/native space. Each scope
  reconstructs all expected texts and excludes the foreign identities. This is
  **not** evidence of two authors in every Part or of actual PDF/PPT/Word upload.
- The existing historical-reference fixture additionally places a >96 KiB pin
  ahead of the reference, proves the initially omitted R1 is fully read later
  without R2 originals, then changes only reference availability during a
  simulated provider interval and confirms the return fence rejects it.

The first race run exposed inconsistent service error normalization when scope
changed during body I/O; the service now returns its canonical state-conflict
error and closes the reader. Three failures were a test's incorrect principal
attribute name, corrected without changing authorization. The first overflow
fixture was 98,018 bytes, below 98,304; it was corrected to actually exceed the
threshold and now asserts its size explicitly. An ownership review found the
previous cached large body could remain referenced while the next body loaded;
the final cursor revision fixes that and adds a targeted regression.

### Coordination and remaining work

The Notion owner explicitly released its 33-input/publication hold after its
immutable checkpoint. Its checkpoint file's SHA was independently read as
`34ec423e0f330b0bf4d4a827a6f17a5a0a8da2c90ea9774d9fb64afd14c755fb`.
This reader is a later successor; it does not rewrite that evidence. The 33
audit inputs, publication.py, active migration catalog v13, runtime flags,
Team/Responsibility UI, live roots/accounts/listeners and provider configuration
remain unchanged by this slice.

Required next work: wire the reader's pre/post-provider fences into the real
executor; budget the **whole serialized model request** for the configured
provider; perform chunk analysis and final synthesis with complete coverage and
citation catalogs; retain/validate failures and retries; verify actual long
rich-file uploads and multi-author answers/semantic graphs; and run the same
quality matrix on company OpenAICompatible models when available. Original-file
provenance is refreshed on each read page, but a separate exact artifact-retention
receipt across provider I/O is not implemented. Existing lifecycle/capacity and
activation gates remain open. No real model/API request was made for this slice.

Terminal verification receipts for this successor are appended below after
the final source hash has completed its checks.

### Terminal receipts — sequential source reader

Final product hashes:

- answer_reading.py: `8f6f2649f2a444f34701f464b322f93a06a30d6a9b6b34c9413f7d5dea7da6fd`
- services/agent_context.py: `c9df51245b1d0e773e9343545bd97538b9cbbb2b6897f3d4aca094cff42fcb8b`

Final changed-path run: **113 passed in 197.97 s**, zero failures/errors/skips.
JUnit `D:\CodexTestTemp\sbe-reading-session-final-20260909.xml`, SHA
`6ffdfc8980077924b3696bb6ec164a943554ee5236a73f1bf4640830d53b11a2`.
Files: `test_answer_reading`, `test_answer_reading_service`,
`test_context_bodies`, `test_context_blob_bodies`, `test_context_body_scale`,
`test_hybrid_retrieval`. This reruns all changed reader consumers after the
cache-ownership fix, including the six-open legacy compose scale assertion.

Adjacent unchanged paths were separately checked before the final cursor-only
memory fix (agent_context.py and the caller paths have the same final hashes):

- Canonical retrieval/executor and actual adapter-boundary fixture:
  **47 passed in 47.15 s**. JUnit
  `D:\CodexTestTemp\sbe-reading-adjacent-20260909.xml`, SHA
  `971597bfa80f4fec530c83fe3ec01170f7f21f209836f368e73b58d47461f2cb`.
- Inventory callsite seal and isolated full preview integration:
  **17 passed in 38.65 s**. JUnit
  `D:\CodexTestTemp\sbe-reading-preview-seal-20260909.xml`, SHA
  `efc0a1dad50c307cf9c9b802b707d11af78f825ad2be22a436a3313c85842846`.

These three groups have **177 ordinal-distinct root cases**, zero duplicate
case identities and zero failures/errors/skips. The earlier 112-case run
(`sbe-reading-session-20260909.xml`, SHA
`6891673dd5c808a41b10eda53477a3b6a4024ef47f0af0b36a4bc4fbd465d282`)
is a superseded pre-memory-fix checkpoint, not an additional 112 tests. All
initial focused/intermediate runs overlap these groups and are not additive.

Independent final-hash review: **8 passed in 20.34 s**, no P1/P2 found in this
bounded source-reader slice. JUnit
`D:\CodexTestTemp\sbe-independent-answer-reading-memory-final-20260909.xml`, SHA
`eab8d9001a23424f2edad1cc08b0bfcb5a12e674eef262c4d84341c670cb7ec9`.
It covers cache release before the next source, maximum Unicode page,
epoch/provider-return and legal-edit/during-read races, DB/CAS/retrieval
lifetime, Part/Responsibility multi-author accumulation and historical R1 with
reference-availability invalidation. These are independent reruns, not eight
new root cases. Report: `workspace-feature-evidence/long-document-optimization-20260908/independent-baseline.md`.

Final test-source hashes:

- test_answer_reading.py: `a44b52e520649dee987a9516bc8ec364bf33cbef1dd5ee93c75aea625a94e813`
- test_answer_reading_service.py: `ed6d81c9317abcd51daef555b48da28a5c6b50e4c445dcb9eba0ebd7d9197a8f`
- test_context_bodies.py: `a77cb94da330fd2796e6b67082da8c4d3bcb080921040cab3ac5a71c646d1a0e`
- test_hybrid_retrieval.py: `4383069f737e017deacf933aa714c7ebd15fe4524eeb1dcaf50b09c77680eb5f`

Read-only contract audit passed, zero errors, 20 goals/284 scenarios, unchanged
33 inputs. `D:\CodexTestTemp\sbe-reading-session-contract-20260909.json` retains
input SHA `5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa`
and report SHA `933a72661117f7b4f973cfe2447a67db7131bc8eeb5d52b958f238957bee95c3`.
All root test handles are terminal. The full goal remains active; the next
implementation step is the real provider-budgeted multi-pass executor, not a
repeat of these completed model-free checks or a claim of live answer quality.

## Provider-attempt authority integration — 2026-09-09 (in progress)

This is a successor to the sequential source reader, not a release or a
replacement for the full personal/Part/Team/Responsibility goal. The preceding
user clarification turn inspected and explained current state; it did not
implement the remaining multi-pass executor. This turn changes authoritative
product code and exercises the real common adapter with isolated providers.

Root owns `internal_llm.py`, `knowledge_core/agent_execution.py`, the new
`tests/test_internal_llm_attempt_guard.py`, the previously owned
`tests/knowledge_spaces/unit/test_answer_reading_service.py`, and this evidence.
The independent reviewer remains report-only. No live roots/accounts/listeners,
company configuration, migration catalog, runtime flags or disabled higher-view
UI were changed. The Notion immutable checkpoint remains historical and intact.

Implemented:

- The common text runner accepts an optional invocation-local `attempt_guard`.
  It validates immediately before and after every provider-adapter attempt,
  across company-compatible HTTP, GGUF and Codex fallback. The post-check runs
  before output validation, including empty output, timeout, failure and abort.
  A guard exception is not swallowed by the provider-fallback handler. It
  escapes with its original type/code and prevents a subsequent attempt.
- `CanonicalAgentTurnExecutor.complete_reading(session)` consumes the original
  service-created reading session and passes its revalidation callback through
  the actual common runner. The executor also fences custom runners that ignore
  the callback. It latches a first guard failure so its enclosing cleanup cannot
  mask `CORPUS_CLOSURE_STALE` with a later "reader closed" exception; a custom
  runner that swallows the first callback failure cannot return an accepted
  completion. Existing `complete(context)` callers remain compatible.
- This entry point STILL answers from the initial composed evidence. It does
  not report unprocessed reader pages as analyzed, does not run chunk analysis
  or final synthesis, and has not been wired into the HTTP route. The fixture's
  having exhausted a cursor does not change model-visible omitted-body counts.
- The guard is a Python callback, not model payload, API JSON or persisted
  authorization. Provider message formatting, model selection/order, HTTP
  request-byte handling, output limits and Codex capabilities are unchanged.
  OpenAI Docs was consulted on the Chat Completions boundary; no OpenAI-specific
  token-field migration or model substitution was applied to company models.

Root verification:

1. Initial combined run: **75 passed, 4 failed in 67.66 s**. JUnit
   `D:\CodexTestTemp\sbe-provider-attempt-guard-20260909.xml`, SHA
   `01dd3ecb2063f9afa79a25ad91c405bf1a0cfbd9fd57d909c5f640471823aac8`.
   All four failures were the same new fixture expectation: changing a bound
   selected closure raises `AgentContextCorpusClosureStaleError`, not the more
   generic `AgentContextStateConflictError`. The product correctly rejected the
   result. No product constraint was relaxed to make this test pass.
2. After correcting that exact exception and asserting its stable
   `CORPUS_CLOSURE_STALE` code, the four-scope rerun was **4 passed, 16 deselected
   in 26.34 s**. JUnit
   `D:\CodexTestTemp\sbe-provider-attempt-four-scope-20260909.xml`, SHA
   `cc5383b5fd9a8240315be7c0871245fc595ffa4c9a40d342312e4e395adf910b`.
   The same product hashes were used for both runs. Together these establish
   79 distinct passing cases, not 83 and not a single all-green combined run.

The 27 new focused cases cover each provider lane's error/empty/invalid-output
fallback, guards before first send/after return/before retry, failures and abort,
invalid callback policy, and custom runners ignoring or swallowing guards. The
four real-service cases reuse the isolated 80-document corpus: only Part A has
two authors with ten documents each. They reconstruct the allowed corpus,
exercise `complete_reading -> common runner -> controlled API adapter`, then
change generation inside that adapter and prove the completion is rejected with
no fallback in personal, Part, Team and Responsibility. These are NOT real model
answers, rich-file uploads, compression quality or live browser acceptance.

Final product/test SHA-256:

- internal_llm.py: `b5cd5bbff51b83d704f220d6cc6eaab85148df21c331c3249bc15f8f08299c9f`
- agent_execution.py: `9a30d65c8ac5e8471c7395d05c80936c67ba0e5114919b68a10d28a755c8d810`
- test_internal_llm_attempt_guard.py: `882344becd1140325547be10b6ac3c1a963e9cb97585642cd5c300eaf2a90b10`
- test_answer_reading_service.py: `8fd668a4ed0977b17db82a726cf65f6a85545b23bfd39291d26d92e21add2816`

Contract audit: zero errors, unchanged 33 inputs, 20 goals/284 scenarios;
`D:\CodexTestTemp\sbe-provider-attempt-contract-20260909.json` retains input SHA
`5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa`.
Both root process handles are terminal. Independent review is pending below.

Remaining: wire the source reader into actual request-budgeted chunk analysis
and synthesis, then the HTTP/worker path; ensure consistent citations across
passes and exact original-file retention across provider I/O; evaluate long
PDF/PPT/Word uploads and multi-author answers/graphs, including target company
models when available. Adapter-boundary fencing does not by itself establish
an atomic authorization guarantee across provider preparation and network send,
nor can it revoke input already sent to a running model. Whole-request token
limits, latency/capacity and runtime activation gates remain open. Goal active.

### Terminal verification and independent activation findings

Root additionally owns the existing
`tests/knowledge_spaces/unit/test_answer_input_transport.py`. Two new cases
exercise real Requests serialization up to `PreparedRequest`, intercepting only
`Session.send`; both HTTP 200 and HTTP 500 followed by a scope change discard
the returned answer/error, propagate the original scope exception and send
nothing to the fallback candidate. The Python callback appears in neither the
HTTP envelope nor the model payload. **2 passed, 11 deselected in 2.18 s**,
`D:\CodexTestTemp\sbe-provider-attempt-http-20260909.xml`, SHA
`2424d1136ffa58844ab979afa068974db05563660bacba0de5c9f83223c25e53`.
Transport test source SHA:
`ef0b11236a59031e35f60a37c9d5e98a948d58d8e72faad5b01c1344241322f3`.
Root total is now **81 distinct passing cases across the recorded runs**.

Independent final-hash review passed **8/8 in 6.97 s**, no P1/P2 defect in the
unactivated adapter-attempt prerequisite. JUnit
`D:\CodexTestTemp\sbe-independent-attempt-guard-final-20260909.xml`, SHA
`658604c5e108ae4f2cacc1a942b1bf9151c0426b3ed4593c4536fd1ee7f74f5b`.
These are reruns of root cases, not additional distinct coverage. Its initial
test-expectation failure remains recorded in the independent report.

The independent review also reproduced two concrete activation concerns:

1. **Adapter preparation precedes transport.** A model-free probe revoked
   authority in API token resolution after the pre-adapter guard. The mock
   HTTP send still happened once, then the post-guard rejected the response
   (`guards=2`, `sends=1`). Root's 200/500 tests prove post-send discard only.
   Before public activation, move an additional practical last check after
   token/payload/request preparation and immediately before actual API send;
   apply the corresponding check after GGUF model/message preparation and
   before inference, and after Codex prompt/process preparation and before
   submission. Preserve original guard exceptions and no-fallback behavior
   through every nested callback. Do not claim these checks eliminate all
   TOCTOU or hold a database transaction across remote work.
2. **Session pairing is internal, not authenticated client input.** The frozen
   session dataclass can still be constructed by trusted Python code. A future
   route must acquire context and reader together from one service invocation,
   never deserialize a session or splice two sessions, and must not fall back
   to unguarded `complete` for a registered reading turn. Negative splice and
   bypass tests remain necessary before HTTP/worker activation.

Report: `workspace-feature-evidence/long-document-optimization-20260908/independent-baseline.md`.
Audit report SHA remains
`933a72661117f7b4f973cfe2447a67db7131bc8eeb5d52b958f238957bee95c3`.
All root handles and this independent review are terminal. Full goal remains
active. This turn is concrete implementation progress, not a blocked wait;
remaining safe work is transport/session activation hardening and actual
provider-budgeted multi-pass analysis/synthesis, followed by the unchanged
rich-file, multi-author, four-scope and company-model quality/capacity gates.

## 2026-09-09: practical provider-submission fences and terminal cleanup

This is an unactivated prerequisite, not a claim of complete multipass answers
or live multi-author acceptance. The preceding explanatory user turn made no
implementation progress; no external blocker prevented the next safe action.
This continuation verifies and records the current terminal implementation.

Root additionally owns `model_invocation.py`, `codex_bridge.py`, and
`tests/test_model_submission_guard.py`. The independent reviewer is report-only.
No live accounts/data/listeners, company configuration, catalog, higher-view UI,
STATUS, API_UI_CONTRACT or publication service were changed by this packet.

Changes and inspected behavior:

- `InvocationGuard` serializes callback checks, latches the first BaseException
  object, cannot be pickled, and preserves that failure across nested adapters
  and Codex writer/main threads. It is not a grant or a revocation lock.
- Guarded OpenAI-compatible requests fence each `Session.send` after auth/body/
  environment/PreparedRequest preparation, including redirects, and after
  return. The prior token-resolution revocation counterexample now sends zero
  requests. The unguarded convenience-request path is preserved.
- GGUF fences after slot acquisition, model loading and message preparation,
  immediately around inference. Preparation revocation prevents inference;
  return-time revocation rejects the result without provider fallback.
- Codex fences before spawn, before stdin write and after close/flush. Guarded
  events are buffered until final validation and cleanup succeed. An early
  private delta cannot escape before a later writer failure. The unguarded
  streaming path and existing tools/model/CLI arguments remain unchanged.
- Review found missing exceptional joins/reaping in the predecessor. The
  current guarded cleanup terminates the exact child tree, waits/reaps, joins
  stdin/stderr workers, closes pipes only after their owning workers stop, and
  fails closed if workers or process remain. It preserves the original stale
  exception or KeyboardInterrupt. Python cannot force-stop an arbitrary stuck
  callback thread; a bounded cleanup failure is not successful cleanup.
- The four-scope service fixture now calls the actual HTTP preparation code,
  intercepting only `requests.Session.send`. It verifies authorized bodies,
  exact stale closure rejection and no fallback. It still answers only from
  initial evidence, even when a fixture already exhausted the reader. The
  80-document corpus has two authors with ten documents each only in Part A;
  it is not all-accounts rich-file upload or semantic-quality evidence.

Root JUnit receipts were reopened and their cases deduplicated by classname/name:

| Run | Passed | JUnit suite seconds | SHA-256 |
| --- | ---: | ---: | --- |
| sbe-submission-guard-20260909.xml | 69 | 23.421 | 81788ba4a04622bd544918f16e0b6628194e215bd0ae0d31efdd40e262da6670 |
| sbe-submission-guard-service-20260909.xml | 30 | 52.645 | 0427959dde52cb64ad3a9868a0ae6355274668e3f10d25d85570ac96d372dcd4 |
| sbe-submission-guard-cleanup-20260909.xml | 31 | 12.216 | 27831a3b959ae6b75528e65878b902fb3caf09d03f1fd07932c261f335325560 |
| sbe-submission-guard-pipes-20260909.xml | 2 | 3.209 | 98b5c2d1d2b2c2f04bf3d37f4e322bd126cd39a9029c2b2b2db3e018a7ffc504 |

All receipts are under `D:\CodexTestTemp`. Total is **101 distinct passing
cases**, not 132. The first 69-case run predates the Codex cleanup refinement;
the overlapping 31-case rerun and two blocked-pipe cases cover the refined
bridge. The HTTP service/executor product paths did not change between runs.
These controlled tests use no real network, model or Codex process.

Independent final review: **14/14 passed**, 8.805 JUnit suite seconds (8.87 s
pytest summary), no P1/P2 correctness blocker in this bounded packet. Receipt
`D:\CodexTestTemp\sbe-independent-submission-guard-final-20260909.xml`, SHA
`2d8eb724ca68987050801a649dfdda1affb89951ade635ae6cc6ef31308ecc53`.
These selected reruns overlap root coverage and are not new distinct cases.
The independent report records the preparation counterexample, cleanup fix,
unguarded compatibility controls and remaining gates.

Final SHA-256 checkpoint:

- model_invocation.py: `046deb7dfe977a66e6729e88f6b088a7564dc2e8ba32fe6d2d894ebc1bc22388`
- internal_llm.py: `3f0602ed151036fe9fa25c23f9e571db62aa30e23930e64f8ea77b20532acf6b`
- codex_bridge.py: `3d95e01db8de56a3ac066c79bb000e0fb4267cd80ca9630a009015aec8cba6e3`
- agent_execution.py: `9a30d65c8ac5e8471c7395d05c80936c67ba0e5114919b68a10d28a755c8d810`
- test_model_submission_guard.py: `8c17b4361ea10023f90122f578391c7133804fe294ba11d4d7bfc99425af10c1`
- test_internal_llm_attempt_guard.py: `bbeaee6378b643f0bd2b7e4d11d30b0c2c0619723a6f8eba717e2b848927838c`
- test_answer_reading_service.py: `e0e54feae1169ecf4b9d94bc878e4c004462f3b0dc7a213bde2dc6f5face2aa0`

Contract audit remains zero errors across 33 unchanged inputs, 20 goals and
284 scenarios. `D:\CodexTestTemp\sbe-submission-guard-contract-20260909.json`
has input SHA `5a4d63e8501680129928649687aa3713a28e02550942569b5898056a9aee38aa`
and report SHA `933a72661117f7b4f973cfe2447a67db7131bc8eeb5d52b958f238957bee95c3`.
All root test handles and the independent review are terminal.

Remaining activation gates are explicit: guarded Codex has no enforced stdout
byte/event bound and temporarily duplicates buffered/reconstructed response
text; the caller's max_tokens is not a CLI output limit. Nested checks repeat
selected-corpus metadata work and require latency/load measurement. Source
session/context pairing and rejection of unguarded route bypass, full-request
provider budgets, actual page analysis/reduction/final citations, cancellation,
retention and commit fences remain open. Last-practical-point checks cannot
make remote send atomic with revocation or retract already transmitted input.
Long-file compression, actual uploads, multi-author answers and semantic graphs
across all four scopes remain full-goal requirements. Goal remains active.
