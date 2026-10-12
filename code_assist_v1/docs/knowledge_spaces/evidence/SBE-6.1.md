# SBE-6.1 evidence

Status: IMPLEMENTED_NOT_GATED

This record proves the local implementation and automated integration gates for
`SBE-6.1 - multi-Wiki/archive/upload UI`. It does not claim the prerequisite
held-out `RANK-014` quality gate, a successful real-browser run, target-server
load, company-provider execution, production rollout, or final 10111 activation.

## 1. Result

- Started and locally checked: 2026-08-25 Asia/Seoul.
- Final local check: 2026-08-25 04:37 Asia/Seoul / 2026-08-24 19:37 UTC.
- User authorization: v11 authority and v12 temporal changes were approved.
- Gate conclusion: `WIKI-018..022` and `UPLOAD-013..015` have local service,
  transport, controller, DOM-contract, Flask HTTP, rich-document, exact-target,
  concurrency, and isolation evidence. The packet remains
  `IMPLEMENTED_NOT_GATED` because SBE-5.1 still retains external `RANK-014` and
  the in-app Browser runtime failed before it could execute a real click flow.
- Production successor flags remain disabled. The marker-bound disposable
  full-preview builder enables the required flags only inside its isolated app.

## 2. Delivered behavior

- The Knowledge tree is ordered `내 지식 -> Part -> 팀 -> 담당`. Personal Wiki
  folders are independently expandable and paginated in pages of 50. Initial
  request/DOM work is constant at 1, 100, and 1,000 Wiki scale.
- Owners can create, rename, set default, archive, restore, and separately load
  archived Wikis using server-returned row versions. Stale optimistic browser
  state is never accepted as authority.
- A history-only conversation automatically loads and highlights its archived
  exact Wiki. The warning exposes real `이 Wiki 복구` and
  `활성 Wiki에서 새 대화` actions; a fresh action creates a new exact-Wiki-bound
  conversation instead of reusing the old session.
- Active-only all-Wiki search is one owner-filtered v2 request. Opening a result
  creates or opens an exact Wiki-bound conversation before opening the exact
  item. No per-Wiki fan-out or legacy route is used.
- The Knowledge `+` dialog opens `자료 업로드` first and `대화 지식화` second,
  with visible personal-Wiki selection plus `개인`, `Part`, and
  `개인 + Part` target tabs.
- Publication accepts an optional additive `personal_space_id`. The server
  reauthorizes that exact owner Wiki with `publish` capability inside the
  transaction. Legacy clients that omit it still target the current default.
  Supplying it for Part-only publication is rejected.
- `both` creates independent personal-Wiki and Part items. A full isolated-app
  test publishes to a non-default Wiki, proves the default Wiki was unchanged,
  and proves Part inheritance and graph/activity identity remain isolated.
- New personal Wiki write enablement is explicit and inherits only the
  `multi_wiki_writes` flag. The repository/service default remains read-safe.
- Upload UI distinguishes publication completion from later enrichment and
  projection status. Commit retry freezes and reuses one operation ID after an
  unknown outcome.
- Static asset versions were advanced so a restarted preview cannot retain the
  old tree/dialog scripts or CSS from browser cache.

## 3. Boundary and no-go attestation

- Tests used disposable SQLite databases, Flask test clients, and isolated
  roots. No company API or target provider was called.
- No migration source was edited. Frozen hashes remain:
  - v10 `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c`
  - v11 `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`
  - v12 `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2`
- Existing 10111 belongs to PID 29464 (`approved_dev_pair`) and was not stopped
  or modified. A disposable preview briefly listened on 127.0.0.1:10121, then
  was stopped. Its isolated root is
  `D:\CodexTestTemp\SBE6-1-20260825-10121`; automated cleanup was denied by the
  execution policy, so it remains stopped and recoverable for manual cleanup.
- The `browser:control-in-app-browser` skill was selected for the required real
  browser proof. Its JavaScript control runtime failed even for an empty probe
  with `failed to write kernel assets: path not found`; standalone browser
  automation was not substituted. This is an environment gate, not a UI pass.

## 4. Stable implementation hashes

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
to avoid recursive document-state coupling.

| File | SHA-256 |
|---|---|
| `knowledge_core/services/publication.py` | `0b77e848de455a34426bae95f89e118487485040005fc1c3763af8b71bf4357b` |
| `knowledge_core/canonical_routes.py` | `e5334815a22e27a36a34a358fae2f3ee72fb51c0e195cb3ee72f825f941f3f90` |
| `knowledge_core/repositories/personal_wikis.py` | `45e4195f1b91f855cff49b1f94e34f5bd508a8d95842010f94436a5513a9b610` |
| `knowledge_core/services/personal_wikis.py` | `3e88a960ce157ebac1733d63a79d31cbf29b5d893f38be63551d503842c239ba` |
| `knowledge_core/agent_runtime.py` | `d581231abe6b0affee679e76392b9879e7d2e2e6e434b023bb8761fda28c4807` |
| `static/personal_wiki_ui.js` | `680618e9c19dbe9ab643e305d1cc6cadeecf98bec135eff390e8e0e99b63c272` |
| `static/knowledge_core_transport.js` | `b0d73faa36306887bbe053d607c5817f83f2bb48da55cf7bfbc8244ab42aa003` |
| `static/knowledge_scope.js` | `0494a70c11dacc913796b9633902cb9e5f412a8b04548937b35eda01555b0de2` |
| `static/knowledge_publication.js` | `d0a655bbdf254ee4f14481b90064aa7e2e20625cfcc44d5bc6a951da6637d4a9` |
| `static/knowledge_core_browser.js` | `26cf51ab89550f093f492472d8993f05692c1c7c185f359faf89856a1473a0bf` |
| `static/knowledge.js` | `61f3b09f2abc77b1b3071114640b585c2fa7aca9e453d091478ebb0336b6ee2d` |
| `static/app.css` | `84c18885280871c37c49dcaf230cbe41f8dc8e48ffbd22b4f29384c51e2928d4` |
| `static/index.html` | `e2c2150dc9cd183a819363994310052455cb0e0a244da09ba4e98964513e7e75` |
| `tools/full_second_brain_preview.py` | `43eec65f67f6014c659841285626e1ec3789315e0a59283adbc6cf40df76b9ed` |
| `docs/knowledge_spaces/contracts/schemas/publication.schema.json` | `51110efea8e05257d061bbc8fd1d2478d5fc0ad9278fbc31c447d8d5a56b22d5` |
| `tests/knowledge_spaces/unit/test_personal_wiki_ui.py` | `155cfbef3fb54d84ec6bc1f1e817c90431490151f1598221fe550e1adff37ba1` |
| `tests/knowledge_spaces/integration/test_full_second_brain_preview.py` | `60205279c4ce9965fe9ff3d1c4ca170b07612fd983903f131ff978df1ead3f32` |

## 5. Verification results

```text
JavaScript syntax checks for all changed browser modules: PASS
Python compileall/py_compile for changed backend and tests: PASS

Focused publication/Wiki/browser/scope/composition regression:
160 passed in 225.33s

New exact-target, 1,000-Wiki, history-only, CAS, and retry tests:
all focused selections pass

Full isolated Second Brain preview with Markdown, DOCX, independent both,
non-default exact Wiki, Part inheritance, Graph/Activity, and legacy 404 canary:
1 passed in 11.15s

Real-process lifecycle CAS against current v12 catalog:
1 passed in 7.21s

Legacy Notion publication adapters, isolated rerun:
2 passed in 3.88s

Contract-audit regression suite:
27 passed in 78.73s

Final direct report-schema-v2 audit:
status=pass; error_count=0; requirements=41; goals=20; scenarios=284;
OpenAPI paths=69; operations=75; schemas=12
```

## 6. Acceptance mapping

| ID | Permanent local proof | Result |
|---|---|---|
| `WIKI-018` | ordered accessible tree, keyboard roles, lazy page-50 controller, Korean labels | AUTOMATED PASS |
| `WIKI-019` | exact owner lifecycle CAS, archived list, active-only search, exact result conversation | AUTOMATED PASS |
| `WIKI-020` | 1,000-Wiki fixture with one initial request and 50 initial rows | AUTOMATED PASS |
| `WIKI-021` | archived exact session highlight, restore action, genuinely new exact Wiki conversation | AUTOMATED PASS |
| `WIKI-022` | omitted target preserves default; new clients send explicit Wiki/space ID | AUTOMATED PASS |
| `UPLOAD-013` | upload-first/chat-second dialog, visible Wiki selector, personal/Part/both tabs | AUTOMATED PASS |
| `UPLOAD-014` | accessible pending/result state, separate publication/enrichment/projection, retry-safe commit | AUTOMATED PASS |
| `UPLOAD-015` | isolated HTTP rich upload, independent both, exact non-default target, legacy 404 and foreign-data canaries | AUTOMATED PASS; REAL BROWSER BLOCKED |

## 7. Residual ownership and handoff

- Repair or reinstall the in-app Browser Node control runtime, then rerun the
  exact click/screen-reader/stale-tab flow before changing this packet to
  `VERIFIED`.
- The prerequisite SBE-5.1 `RANK-014` held-out Korean/English quality corpus
  remains external and prevents the dependent packet chain from fully gating.
- SBE-6.2 owns higher native/inherited authoring, aggregate graph, ordered
  References, distribution/lease/role controls, and edge correction UI.
- SBE-7.1/SBE-7.2 own full security/load/provider gates, resolving the existing
  10111 listener, isolated final activation, and company/target handoff.
