# SBE-8.2 integrated selector and inventory-query repair evidence

> The generic inventory behavior in this packet remains valid. Natural-language
> coverage for `현재 위키 설명해줘` is extended and verified by `SBE-8.3.md`.

Date: 2026-08-25 Asia/Seoul

Status: LOCAL_HTTP_AND_CODEX_PASS_BROWSER_RUNTIME_BLOCKED

## 1. Why SBE-8.1 did not catch the user-visible failures

SBE-8.1 proved canonical data, authority, graph, projection, and keyword-grounded
Codex paths, but its UI statement exceeded its evidence. Three missing test
dimensions allowed the reported failures through:

- The JavaScript transport mock did not use the complete non-empty finding
  object returned by the retained preview. The server added inheritance,
  provenance, and capability fields while the browser still enforced the stale
  exact-key list. Selecting a space changed controller state, but the following
  management reload rejected the response with `finding summary shape is
  invalid`; the old Markdown list therefore remained visible.
- Codex grounding tests queried exact fixture terms. A generic inventory request
  such as `지금 어떤 지식이 들어있지?` had no lexical FTS match, so retrieval
  returned zero documents even in the default personal Wiki with eight items.
- The right workspace default was not asserted from a fresh page load. Missing
  local storage was interpreted as open even though the source comment said it
  should initially be collapsed.

The earlier statement that the integrated UI was complete is therefore
withdrawn. API/service gates are not a substitute for a selector-to-render
browser contract test.

## 2. Repairs

- `static/knowledge_core_transport.js` now validates the current finding shape,
  including selected/source-space binding, inheritance, origin, overlay grant,
  and capability fields. This removes the stale exact-shape exception on default
  personal, Part, Team, and Responsibility reloads.
- `static/index.html` and `static/app.js` start the right workspace collapsed
  before first paint. The existing top-right folder control remains the explicit
  opener. Static cache keys were advanced to `20260825d`.
- `knowledge_core/repositories/hybrid_retrieval.py` recognizes bounded Korean
  and English inventory/list intent and returns only authorized, active
  documents from the selected corpus closure. It still returns at most ten and
  performs no graph expansion for inventory mode.
- A distinct empty personal Wiki remains empty. `temp2` does not inherit the
  default personal Wiki because personal Wikis are independent by product
  contract.
- A preview started with `--codex` now composes the canonical conversation
  executor with provider order `codex` and model ID `codex-local` only. The
  company model catalog can remain visible for compatibility, but canonical
  turns in this validation lane cannot fall through to it.

## 3. Retained 10211 live HTTP proof

Retained URL: `http://127.0.0.1:10211`

- Listener PID: 45576
- Health: `ready`, schema version 2
- Marker SHA-256:
  `83c35d85ab9cdaba4359c33d9ccb2fa915c22d134220f99eb7a7327e70200ee1`
- `company_api_called=false`
- Initial root HTML contains `app-root right-collapsed` and cache key
  `20260825d`.

An authenticated admin request used the same Korean inventory question for all
selected spaces:

| Selected view | Findings | Retrieved items | Expected result |
|---|---:|---:|---|
| default personal `내 지식` | 8 | 8 | pass |
| personal `temp2` | 0 | 0 | pass; independently empty |
| Part `AMHS설계파트` | 15 | 10 | pass; bounded |
| Team `AMHS지능화` | 43 | 10 | pass; bounded aggregate |
| Responsibility `AMHS담당` | 50-page response | 10 | pass; bounded aggregate |

The returned titles differ by selected view, proving that routing is not reusing
the preceding Markdown list.

## 4. Real Codex conversation proof

Fresh canonical conversations were created through authenticated HTTP and the
same generic Korean inventory turn was streamed to completion:

| View | Conversation ID | Result |
|---|---|---|
| default personal | `3a22aa95-babe-5c22-b182-479ac261f653` | summarized eight travel/research documents |
| Part | `f6b967cd-91e7-5fed-a7ed-22d3d78ff07b` | summarized Part operations/incident documents |
| Team | `2de9a714-54ac-50ed-a99b-76220df7f862` | summarized Team aggregate incident knowledge |
| Responsibility | `002df51d-f045-5343-ae55-292e66b9cd05` | summarized Responsibility-level publications |

All four durable `conversation_turn` rows are `completed` with
`model_id=codex-local`, `model_kind=codex`, and
`retrieval_source=canonical_fts`. No company API call occurred.

## 5. Regression proof

Python compilation and JavaScript syntax checks passed. The focused integrated
suite passed 78/78 in 85.62 seconds and covers:

- full preview composition and Codex-only executor selection;
- current non-empty finding transport validation;
- default/additional personal Wiki and hierarchy scope selection;
- Korean inventory retrieval with authorization and final-ten bounds;
- canonical retrieval and canonical Agent execution.

## 6. Remaining browser limitation

The in-app Browser runtime still fails before navigation with
`failed to write kernel assets: path not found`. Therefore no claim is made that
this run produced a pixel-level click/screenshot recording. The replacement
evidence is stronger than SBE-8.1 at the HTTP, live-data, DOM-contract, and real
Codex layers, but the final human-visible login/click confirmation remains for
the repaired Browser runtime or the user at 10211.

## 7. Changed implementation hashes

| File | SHA-256 |
|---|---|
| `static/knowledge_core_transport.js` | `2a85858c8716b1ff6d6c2dad1a6347e29132c6f5a5c8d9aa2d6ce6e04768be3b` |
| `static/app.js` | `6bb635fd7893fdf3d6b80e23258ba8a54c16eb83ba62bba40f297351cf372890` |
| `static/index.html` | `97ada67e68c764a5191c16cecd6c5253b62d49cbdf27daa2b0c585bf19b7bfd2` |
| `knowledge_core/repositories/hybrid_retrieval.py` | `dff82ffc405c58ead2f2259a85665736044b60856f3bcafd6af7ed018bb858e6` |
| `knowledge_core/repositories/retrieval.py` | `af9b3c678ec88775e3142d1c78c402fcc786f7486e5bdc00d7731a2fa6752b87` |
| `tools/full_second_brain_preview.py` | `ae3b26482eb2a33f4cca61c6207788b0ef4f061ccce41801624965d05c20329b` |
