# Local full Second Brain preview

Date: 2026-08-18 (Asia/Seoul)

Decision: the original 10011-era Second Brain knowledge experience and the
canonical organization hierarchy are available together on the loopback-only,
marker-bound preview at `http://127.0.0.1:10111/`. This is a local functional
preview, not production or target-server authorization.

## Isolation and process

```text
preview root       D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2
marker SHA-256     9c5547f1ac88a290315a90f1b4c6954fd1ff1905a0b0115ed6ef0429e9aaf712
listener           127.0.0.1:10111
listener PID       46428 at final verification
health             HTTP 200
company API called false
```

`tools/full_second_brain_preview.py` copies the legacy knowledge and user data
into the isolated preview root. It does not copy a company token. The preview
injects a read-only higher-hierarchy resolver, enables canonical personal/Part
publication only inside this marker-bound localhost app, and preserves the
legacy knowledge/session/chat compatibility routes.
Production defaults remain fail-closed and continue to expose only the
production-authorized scope surface.

## Visible behavior verified by real browser actions

The browser logged in as the existing `admin` account and clicked the actual
controls. It verified:

- the knowledge-space selector has four separate choices, not one breadcrumb:
  `내 지식`, `담당 · AMHS담당`, `팀 · Second Brain Preview Team`, and
  `Part · Second Brain Preview Part`; its display order is intentionally
  `내 지식` → `Part` → `팀` → `담당`, while canonical space IDs and hierarchy
  semantics remain unchanged;
- the old top-level `대화 지식화` and `자료 업로드` controls are removed;
  the Knowledge sidebar `+` now opens one `지식에 추가` dialog with Chrome-like
  `자료 업로드` and `대화 지식화` tabs. The bottom composer menu routes both
  actions into the same dialog while retaining `대화에 파일 첨부` as a distinct
  action;
- the unified dialog exposes `개인`, `Part`, and `개인 + Part` as immediate
  segmented tabs before preview creation. That selection is carried into the
  immutable preview and rejected rather than silently changed if the target
  became unavailable. Personal and Part remain independent items and later
  edits are not auto-synchronized;
- clicking the personal Index and Log opens their document modals; neither
  request returns 404;
- the personal graph renders eight named Markdown nodes and ten relationships;
- Responsibility and Team each render a separately selected read-only inherited
  view of the current Part corpus. Each selection keeps its own Index, Log,
  Graph, and projection-status response identity; no higher-space write control
  is exposed;
- Part renders the independent `Part 공유 지식 공간` item and a graph with one
  named node and zero relationships;
- switching Team -> Part while the graph is open refreshes the graph provider
  to the selected scope;
- switching Part -> personal restores both the original personal Wiki panel and
  the eight-node/ten-relationship personal graph;
- a late response from the previous scope cannot overwrite the newly selected
  graph. The permanent regression resolves Part first and personal last, and
  proves that only the Part result is drawn.

Responsibility and Team are not independent editable copies. They inherit the
authorized current-Part items live, so Part revision/deactivation/restoration
is visible immediately without a three-way dual write. Part contains one
explicit preview guide item, and an integration upload proves that a new Part
Markdown publication appears in all three selected views and their Graph/Log.
The same integration run publishes a second Markdown preview with `both`,
proves that it creates two different item IDs in personal and Part spaces, and
therefore freezes the later-edit independence rule.
The isolated canonical upload picker uses the existing deterministic document
parsers for Markdown, PDF, DOCX, PPTX, XLSX, text/code, notebook, and structured
text inputs. The immutable raw artifact is preserved. Extracted embedded images
remain in that raw source and produce an explicit preview warning because this
preview does not yet publish them as separate canonical asset children. A
minimal real DOCX is normalized and published to Part in the integration gate.

## HTTP and functional probe

The authenticated live checks returned HTTP 200 for:

```text
/api/code/knowledge/graph
/api/code/knowledge/system_doc/index
/api/code/knowledge/system_doc/log
/api/knowledge/v1/spaces
```

The final non-browser probe returned:

```json
{
  "functional_gate": {
    "api_model_count": 3,
    "codex_model_count": 1,
    "graph_http": 200,
    "index_http": 200,
    "knowledge_http": 200,
    "organization_user_count": 2,
    "status": "pass"
  },
  "knowledge_projection": {
    "part": 1,
    "personal": 8,
    "responsibility": 1,
    "team": 1
  },
  "status": "ready"
}
```

## Regression evidence

```text
scope transition + full preview publication/inheritance
7 passed in 10.35s
canonical browser transport + curation + lease editor UI
26 passed in 11.54s
publication route + publication service
41 passed in 145.91s
total unique focused regressions
74 passed
JavaScript syntax checks (changed modules)
pass
real browser: Knowledge + unified tabs/targets, bottom + routing, Team/Responsibility inheritance, Index/Log/Graph
pass
live functional probe
pass
knowledge-space contract audit
pass, 0 errors, input SHA-256 d35bd7cf77375b42af837ccd568d74f28001e4bd80e48a2c2086732656645aa8
```

Frozen implementation hashes:

```text
57a43f53cc0be93468197272f90aaf1c235d8694608ed395b34c94aa75d71993  tools/full_second_brain_preview.py
2464482eb58ecd4587f026ce292ed520214857b5989c1a1cb7c064384686cd9c  static/index.html
339e9e50742067ec21214bc455dc3dc07bf4a08b1e98adb9eef6e203d84d6430  static/app.css
f77e1fc335edf9e320f8fc5d1730a9350b9b266b5fed8f64c03aae9ef4a5bc2a  static/app.js
979f4d85fe0db12a5335b6b96823b148d43cd93e748fa867a8fea4eabd09e53c  static/knowledge.js
684984c213b8756a05320d68a5e090f3448636e2b83451ce6921052dcb1351eb  static/knowledge_publication.js
6c2ffbdfd0c77ff04fd7cb99cba985593c2b77766987a9b1eaefc9ccd5047fb3  static/knowledge_scope.js
ffa3de093c4ef1df8134342ae89a13d6751d2a157873750edb215ceb17a7fb20  static/knowledge_core_browser.js
ea7357c2c1f995aca564af14bd7aec84b840e9d891b418ffe0bcbf3f1fef5af3  tests/knowledge_spaces/integration/test_full_second_brain_preview.py
30f054f697096403e6dbd2a705f8ff76c34b343c8925963b1892091925b42e64  tests/knowledge_spaces/unit/test_knowledge_core_browser_transport.py
f229954c2163ee88d09c78ed4396f9981999605121a7703c883f383f1486ea77  tests/knowledge_spaces/unit/test_scope_selection_ui.py
6fa22d33085cb5cf4ead7eda7d0fdeddd971be633ab2f74de9b71667ff4e0572  AGENTS.md
e9696e9a4323b48b94c2fd4fcbc876faf5b427767b738f7a13f65b2717d40fed  docs/knowledge_spaces/DECISIONS.md
```

## Non-claims and company handoff

This preview does not prove company OpenAI-compatible endpoint model IDs,
tokens, wire compatibility, service-account/TLS deployment, target-class
multi-client load, or off-volume restore. Those remain documented in
`docs/COMPANY_OPENAI_COMPATIBLE_HANDOFF.md` and
`docs/COMPANY_TARGET_COMPLETION_HANDOFF.md` for the company Codex/operator.
