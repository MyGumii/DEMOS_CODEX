# SBE-8.8 v13 clean document-matrix evidence

Date: 2026-08-29 KST  
State: local release candidate verified on an isolated preview  
External gates: company OpenAI-compatible models, target-class load/restore,
and a final real-Browser click run remain open

## Outcome

The marker-bound v13 preview implements and locally verifies the requested
Second Brain behavior without modifying the 10011 or 10111 roots:

- drag/drop and multi-file selection produce one independent extraction and
  Markdown publication result per source file;
- personal, Part, personal-and-Part, Team-leader native Team, and executive
  native Responsibility publication targets are exercised;
- Team leaders have a direct Team home and no Part membership, while the
  Responsibility executive has a direct Responsibility home and no Team or
  Part membership;
- personal Wiki, Part, Team, and Responsibility views expose their own
  canonical item/index/activity/graph state;
- Part graphs stay isolated from other Parts; Team and Responsibility graphs
  contain only their authorized descendant closure plus native higher-space
  content;
- canonical Agent turns bind to the selected space and receive stored Wiki
  bodies, rather than title-only inventory metadata.

## Isolated runtime

```text
origin                 http://127.0.0.1:10221
data root              D:\CodexTestTemp\SBE9-V13-20260827-v6
organization source    D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2
preview marker         69f86898010bcca2b0f114fe7ec4f77e3026da9be14c12048fcc4b9fc7697fb5
provider mode          codex-validation
company_api_called     false
worker topology        one durable + one projector
health                 ready; both retained workers alive
```

The final retained listener is the isolated 10221 process. The 10011 and 10111
listeners and roots were not stopped, replaced, or mutated.

## Clean rebuild and administrator preservation

The prior validation corpus was removed before this matrix was rebuilt. The
administrator's personal Wiki was compared against the pre-clean backup:

```text
old admin personal items       15
current admin personal items   15
matching item IDs               8
matching body SHA-256           8 / 8
old validation-only items       7 removed
new validation-only items       7 added
```

Thus all eight user-authored administrator items retain both canonical item ID
and exact body bytes. Only the seven old validation items were replaced.

## Real document upload matrix

The validator created actual DOCX, PPTX, and PDF containers, submitted them
through the public preview/publication routes, waited for extraction and
publication, and then checked canonical search and projection state. It did
not insert generated Markdown directly into the database.

| Measure | Result |
|---|---:|
| Accounts | 19 |
| Source files attempted / passed / failed | 190 / 190 / 0 |
| DOCX / PPTX / PDF | 76 / 57 / 57 |
| Source bytes / generated Markdown bytes | 337,312 / 210,601 |
| Independent publication results | 220 |
| Personal / both / Part / explicit higher files | 95 / 30 / 45 / 20 |
| Related / isolated-topic files | 148 / 42 |
| Mean / p50 / p95 / max file time | 5.451 / 5.378 / 7.767 / 9.113 seconds |

The 30 `both` inputs generated separate personal and Part items. Later edits to
one copy therefore do not silently mutate the other. The corpus mixes AMHS/OHT,
robot servo control, bearing vibration, seismic engineering, industrial CT,
astronomy, battery thermal control, greenhouse science, cold chain, coffee,
kimchi and Korean food, dashi and Japanese food, sushi HACCP, skincare,
sunscreen, makeup, salon hygiene, and hair bleaching. Related and unrelated
subjects are deliberately interleaved across formats and accounts.

## Direct organization homes

| Direct role/home | Accounts | Selectable scopes |
|---|---:|---|
| Part leader or member | 14 | personal, Part, Team, Responsibility |
| Team leader | 3 | personal, Team, Responsibility; no Part |
| Responsibility executive | 1 | personal, Responsibility; no Team/Part |
| Administrator | 1 | personal plus authorized organization scopes |

Native Team and Responsibility writes require the corresponding open
leadership role. Administrator status alone does not create native higher-level
authorship.

## Graph, index, log, and projection audit

All 19 account audits passed. Seven Part spaces remained mutually isolated.
Each Team graph used exactly its native Team source plus its descendant Parts.
The Responsibility graph used 11 sources: seven Parts, three Teams, and its
native Responsibility space. Every related validation document was incident
to its expected topic cluster, while every isolated-topic document had zero
edges into that cluster. API index/activity/graph responses and the projected
manifest and immutable graph file were checked for current generation, length,
and SHA-256.

The final semantic contract is
`automatic-edge-judge-v4-relation-policy`. It supplies a generic relation and
direction policy: `related` and `contradicts` are symmetric; `supports`,
`depends_on`, `extends`, and `example_of` are directed. The model returns short
verbatim quotes from both bodies; the canonical service locates those quotes
and stores exact Unicode code-point spans and hashes. Repeated substantive
claims, constraints, or procedures may form `related` edges, while filenames,
formats, headings, accounts, timestamps, upload metadata, and validation
markers alone cannot. No topic-specific example, retrieval phrase, or
query-specific production few-shot was added.

Generation 4 completed 393 of 393 semantic jobs. Its terminal outcomes were:

```text
aggregate  activated 22; no_candidate 97; provider_failure 3;
           below-threshold 4; duplicate 39
local      activated 33; no_candidate 45; provider_failure 14;
           duplicate 136
```

Provider/schema failures fail closed and do not manufacture edges or turn a
successful upload into failure. The final database has 146 active local
semantic edges and 67 active aggregate semantic edges. Overall durable state
is clean: 1,982 durable jobs are `succeeded`, 1,647 outbox events are `applied`,
and no nonterminal, failed, dead, or unapplied record remains.

## Codex Bridge query matrix

The final query stage passed 62 of 62 real canonical Agent turns:

- 19 default-personal Wiki queries;
- 19 additional-personal Wiki queries;
- 15 cross-account Part queries;
- three Team-native and three Team inherited-Part queries;
- one Responsibility-native, one inherited-Team, and one inherited-Part query.

Every attempt uses fresh canonical conversation and turn operation UUIDs. The
validator asks a bounded natural question and requests an exact Wiki-rule
quotation in English. It does not inject the expected answer values, document
title, validation marker, or traceability token into the Agent prompt. All
questions contain 15 through 25 searchable terms, canonical SSE is decoded as
UTF-8, and the body-derived expected phrases are asserted only after the turn
has completed. This proves selected-space routing and body delivery through
Codex Bridge; it is not evidence for company-model answer quality.

The create-only report is:

```text
D:\CodexTestTemp\SBE9-V13-20260827-v6\.runtime\v13-document-matrix-OPEN-READY-20260828-V1.json
SHA-256 fe53abdfb9b2c5b79a07a015ad65e6e61b1b59a1f7513fbbec65cfec939794e3
status pass; upload pass; graph pass; query 62/62 pass; unresolved failures 0
```

Eight superseded diagnostics remain in `resolved_diagnostics` rather than
being erased: three earlier graph assertions and five earlier query attempts.
The complete bounded-query re-audit and strict graph re-audit supersede them.

## Regression and UI evidence

Final repository tests after stopping the retained server were:

```text
1966 passed, 5 subtests passed in 2230.45s
31 focused integration/unit tests passed in 51.50s
Python py_compile: pass
node --check for the four Knowledge UI scripts: pass
```

The focused run includes multiprocess inference ownership, full preview
startup, semantic-edge lifecycle/policy, v13 source generation, and Codex
Bridge. Earlier real-browser checks on this same isolated lane observed the
initially closed workspace, scope ordering, personal/additional-Wiki switching,
document body popup, Part/Team/Responsibility lists, and non-404 Index/Log
popups. The final post-regression Browser rerun could not connect because the
installed Browser skill and its internal service referenced different plugin
cache versions. The plugin cache was not modified and no standalone browser
runner was substituted. Therefore the final real-click pass remains an
explicit gate; API, projection, DOM transport, and earlier click evidence are
not represented as a new final click run.

## Boundaries and handoff

- No company API was called. Company model IDs, credentials, TLS, streaming and
  error behavior, rate limits, latency, and held-out semantic/retrieval quality
  remain the OpenAI-compatible handoff.
- This 19-account functional matrix is not the target-class 40-client load
  profile, signed off-volume backup/restore rehearsal, or production cutover.
- The isolated candidate is ready for user inspection at 10221. It is a local
  release candidate, not final company production sign-off.
