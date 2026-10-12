# SBE-8.5 structured Wiki-overview intent evidence

Date: 2026-08-25 Asia/Seoul

Status: LOCAL_HTTP_AND_CODEX_PASS

## 1. Exact user-path evidence

Two real admin turns failed after the earlier phrase-specific repair:

| Conversation | Selected space | Exact question | Observed retrieval result |
|---|---|---|---:|
| `dcdbc52e-a3d9-596b-a933-2c0ef481e87b` | default personal | `현재 wiki에 어떤 정보들이 있지?` | 0 |
| `14c48731-433d-579b-8136-f30f2c774e2c` | `AMHS설계파트` | `현재 wiki에 어떤 정보가 있지?` | 0 |

Both turns reached `codex-local` and were bound to the correct space. Retrieval
intent classification failed because the previous recognizer relied on an
enumerated phrase list and did not cover the mixed English `wiki` plus Korean
`정보` form.

## 2. Structural repair

The inventory recognizer now tokenizes Korean/English input, removes bounded
Korean particles, and checks the structure of a Wiki-overview request:

- selected-space marker (`wiki`, `위키`, knowledge base);
- generic corpus nouns (information, knowledge, documents, items, contents);
- overview intent (what/which/list/show/describe, 어떤/무슨/설명/보유/포함);
- no remaining topic-specific tokens.

This makes common phrasing variations inventory queries without turning a
specific request such as `현재 wiki에서 scopealpha를 설명해줘` into a whole-Wiki
dump. Inventory mode remains authorization-bound, performs no graph expansion,
and returns at most ten documents.

## 3. Live proof after restart

Retained 10211 listener PID: 34096. Health is `ready` and
`company_api_called=false`.

Authenticated retrieval with the exact failed inputs returned:

- default personal: eight items;
- `AMHS설계파트`: ten items from fifteen direct authorized candidates.

Fresh real Codex conversations then completed with those same strings:

| View | Conversation ID | Answer evidence |
|---|---|---|
| default personal | `d949b51d-b9d4-50c7-981e-55c2dd75676f` | summarized three travel bookings and five research documents |
| Part | `728213fd-a46d-5611-95a6-454aa95c7465` | summarized five operations records, publication validation, recovery guidance, and an unrelated welfare memo |

Both durable turns record `model_id=codex-local`, `model_kind=codex`, and
`retrieval_source=canonical_fts`.

## 4. Regression proof

- focused structured-intent and retrieval tests: 20 passed in 25.20 seconds;
- complete relevant preview/browser/retrieval/Agent suite: 84 passed in 104.81
  seconds;
- Python compilation: pass.

Changed implementation SHA-256:

- `knowledge_core/repositories/hybrid_retrieval.py`:
  `f23a10992f579781491cf0bc6b4aa5ee331d50f4b6b470127802ed48d5d3c52f`.
