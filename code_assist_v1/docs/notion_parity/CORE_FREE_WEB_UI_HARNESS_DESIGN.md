# Core Free Web UI evidence harness — xhigh implementation design

Status: authoritative H1 implementation supplement; local Packs A-D, all six UI1 local capture envelopes, H3 `UI-SHELL-001`/`UI-SHELL-003`/`UI-TREE-001` through `UI-TREE-003`, the full local H4 `UI-EDITOR-001` through `UI-EDITOR-006` set, H5 `UI-DB-001` through `UI-DB-003`, H6 `UI-SEARCH-001`/`UI-SEARCH-002`/`UI-COLLAB-001`/`UI-SHARE-001`, H7 `UI-SB-001` through `UI-SB-003`, and H8 `UI-RESP-001` are implemented and tested in isolated real Chrome. Synthetic Notion reference Pack E and first differential Pack F remain pending.  
Recorded: 2026-08-15 Asia/Seoul  
Parent plan: `docs/NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md`  
Reference procedure: `docs/notion_parity/CORE_FREE_WEB_UI_CHARACTERIZATION_RUNBOOK.md`

Current authoritative completion audit (superseding the older snapshot text
below): all 25 local Core contracts are covered, with 24 functional
real-Chrome results and one measurement-only performance result. The H9
isolated pilot/recovery foundation is green, but reference and differential
counts remain zero. See
`docs/notion_parity/core_free_web_ui/completion_matrix.v1.json`; verifier
success means truthful incompletion, never parity completion.

Current tested snapshot: `docs/notion_parity/checkpoints/ui-resp-001-local-viewport-green-reference-pending-20260815.json`, whose parents preserve the complete local H3 lifecycle chain, H4 editor/recovery set, H5 typed-row/view-state/multi-view set, H6 search/comment/share set, and H7 Second Brain set. The local real-Chrome suite repeats shell/editor/database/search/comments/share, canonical Second Brain cancel/approve/replay, workspace isolation, hierarchy/tree mutations, editor recovery, database reload/view switching, quick search, current-page find, page-link navigation, backlink return, comment lifecycle, share dismissal, and synthetic session attach/detach in independent roots. H8 `UI-RESP-001` additionally repeats one saved synthetic page at 1440×900, 1280×800, 980×800, and 720×800, proving zero global horizontal overflow and reachable navigation, editor, search, share, and dismissal focus paths without changing the already-conforming product CSS. The browser fixture excludes Chromium-rejected legacy service ports. The Chrome IME test drives browser composition/input events rather than a real operating-system candidate window, and all local results remain local rather than official-reference parity. The sealed evidence-packet writer/verifier and identifier-free C0 reference guard remain green. Signed-in `REF-FREE-A/B` reference capture still fails before browser or tab selection at host initialization, so no reference/differential promotion is claimed.

This document turns H1 into a bounded implementation contract. It does not authorize access to personal Notion content, live local data, plan changes, invitations, public publishing, operational migrations, feature-flag changes, or resolver cutover.

## 1. Outcome

H1 is complete only when one synthetic scenario can be executed in a real browser against the isolated local application, the corresponding Notion Free reference observation can be recorded with the same semantic action IDs, and both sides produce redacted, hash-bound evidence that the deterministic comparator can consume.

H1 is infrastructure, not a visual-parity claim. It must not modify production UI merely to make the first comparison pass. The first measured difference becomes the input to the next cohesive UI slice.

## 2. Fixed profile and safety boundary

| Axis | Fixed value |
|---|---|
| Reference | Notion Free public web UI |
| Browser | signed-in Chrome desktop session |
| Locale | `ko-KR` |
| Role | workspace owner |
| Reference data | synthetic content in `REF-FREE-A/B` only |
| Local data | unique trusted OS temporary root |
| Personal workspace | forbidden to open, search, or capture |
| Business/Enterprise/Archive | conditional; not an H1 gate |

Reference actions may use only the visible UI. Private selectors, private endpoints, raw network capture, cookie export, raw DOM dumps, workspace IDs, page IDs, user IDs, and non-synthetic text are not evidence.

Local test runs must set both `CODE_ASSIST_TEST_DATA_ROOT` and `CODE_ASSIST_DATA_ROOT` to the same unique absolute trusted OS temporary directory and enable the existing explicit test mode. The repository, `user_data`, `knowledge`, `.runtime`, sessions, and workspace databases are never test roots.

## 3. Current reusable foundation

The xhigh implementer must reuse these read-only contracts before creating replacements:

- `tools/notion_parity/evidence.py`: duplicate-key rejection, canonical JSON SHA-256, relative-path validation, sensitive-value rejection, evidence-state progression;
- `tools/notion_parity/differential.py`: exact state/keyboard/a11y/semantic-visual/error-recovery comparison plus geometry and latency comparators;
- `docs/notion_parity/EVIDENCE_POLICY.md`;
- `docs/notion_parity/catalog/schema.json`;
- `docs/notion_parity/catalog/workspace_editor_core.json`;
- `docs/notion_parity/policies/default_differential.json`;
- `tests/test_notion_evidence_policy.py` and `tests/test_notion_differential.py`.

Known compatibility constraints:

1. Historical evidence accepts `NP*` scenario IDs only. The UI-first plan uses stable `UI-*` IDs. H1 must add a separate UI evidence envelope/verifier or a versioned extension; it must not silently rewrite historical evidence IDs.
2. `workspace_editor_core.json` mixes Free, paid, native, mobile, and multiple roles. It remains historical input. H1 adds a Free-owner desktop overlay instead of narrowing or mutating it.
3. `default_differential.json` contains `2px` geometry tolerance and `500ms` latency budget. Those values are not automatically adopted as Core UI product thresholds. H1 may reuse the comparator engine, but geometry and latency remain measurement-only until an approved synthetic reference/local pair records a reviewed policy.
4. Existing Node tests use inert DOM stubs and source probes. They remain contract regressions but never count as real-browser evidence.

## 4. H1 package and file plan

Prefer new files. Exact names may change only if a current-tree collision is found and recorded before editing.

### 4.1 Machine-readable scenario contract

- `docs/notion_parity/core_free_web_ui/scenario.schema.json`
- `docs/notion_parity/core_free_web_ui/scenarios.v1.json`
- `docs/notion_parity/core_free_web_ui/policies/precalibration.v1.json`
- `tools/verify_notion_core_free_ui_plan.py`
- `tests/test_verify_notion_core_free_ui_plan.py`

The overlay owns the Core Free denominator and references historical NP scenarios only as provenance. It does not edit `workspace_editor_core.json`.

### 4.2 Evidence/action implementation

- `tools/notion_parity/ui_actions.py`
- `tools/notion_parity/ui_capture.py`
- `tools/notion_parity/ui_evidence.py`
- `tools/notion_parity/ui_evidence_packet.py`
- `tools/notion_parity/ui_reference_guard.py`
- `tools/notion_parity/ui_redaction.py`
- `tests/test_notion_ui_evidence_harness.py`
- `tests/test_notion_ui_evidence_packet.py`
- `tests/test_notion_ui_reference_guard.py`

These modules must be import-side-effect free. Importing them must not import `app_code`, auth, configuration, Knowledge Core, Second Brain runtime, route registrars, data-path discovery, or browser drivers, and must not create files.

### 4.3 Isolated local browser proof

- `tests/browser/test_notion_ui1_golden_journey.py`, or a same-purpose new file under the repository's accepted browser-test location;
- a new fixture builder owned only by the browser test/harness;
- a sealed evidence-output directory contract under a validated isolated test root, with canonical JSON, exact file-set and digest verification, and no committed raw evidence.

The first H1 pass must not edit `notion_static/index.html`, `notion_static/notion.css`, or `notion_static/notion.js` to guess at Notion parity. Those files become eligible for Notion-familiar corrections only after the reference/local baseline is captured and a concrete UI difference is selected. A local-only Second Brain hard-gate failure may be corrected before reference capture because the reference has no corresponding control, but only after exact real-browser evidence identifies the failure and the change is bounded by the local scenario contract and adjacent regressions.

## 5. Scenario overlay contract

The overlay root must reject duplicate keys and unknown fields and contain:

- `schema_version`;
- `contract`;
- `primary_profile`;
- `required_scenarios`;
- `extension_scenarios`;
- `conditional_scenarios`;
- `excluded_capabilities`;
- `artifact_contract`;
- `policy_refs`;
- `source_refs`;
- `generated_counts`.

Each required scenario contains:

- stable `scenario_id` matching `^UI-[A-Z0-9]+(?:-[A-Z0-9]+)*-[0-9]{3}$`;
- phase and user-visible outcome;
- exact profile `free / workspace_owner / desktop_web / ko-KR`;
- synthetic fixture aliases;
- ordered semantic actions;
- exact assertions;
- tolerance assertions, if any;
- negative and recovery assertions;
- Second Brain hard-gate IDs where relevant;
- current evidence state;
- explicit non-goals.

The verifier recomputes all counts. Hand-written counts that do not match are a failure. No required scenario may name Business, Enterprise, native, mobile, a real workspace, a real page, or a personal identifier.

The initial required denominator is the 25 Core IDs fixed in the parent plan. H1 immediately executes only this bounded subset:

- `UI-SHELL-002` — sidebar collapse/restore;
- `UI-SEARCH-001` — search open/Escape/focus restoration;
- `UI-EDITOR-001` — Korean title and paragraph save/reload;
- `UI-SB-001` — preview cancel produces zero Knowledge mutation;
- `UI-SB-002` — approval creates exactly one isolated immutable publication;
- `UI-A11Y-001` — keyboard focus and accessible control identity.

The composite run ID is `UI1-FREE-DESKTOP-GOLDEN-JOURNEY`; it is not a replacement for the individual scenario states.

## 6. Semantic action grammar

Actions are product-level observations, not source selectors. Each action contains exactly:

- `action_id` unique within the scenario;
- `scenario_id`;
- `surface`;
- `verb`;
- `target_alias`;
- `input_alias` or `null`;
- `expected_state_id` or `null`;
- `expected_focus_alias` or `null`;
- `capture_after` boolean;
- `mutation_class`;
- `recovery_action_id` or `null`.

Allowed initial verbs:

- `open_surface`;
- `click`;
- `press`;
- `type_synthetic`;
- `wait_visible`;
- `assert_visible`;
- `assert_hidden`;
- `assert_focus`;
- `assert_synthetic_text`;
- `capture_region`;
- `reload`;
- `switch_workspace_alias`.

Reference execution resolves `target_alias` through visible role/name/position and human-observable state. It never records a Notion internal selector. Local execution prefers accessible role/name and product-owned `data-testid` only where needed for a stable test contract.

Unknown actions, extra fields, duplicated action IDs, forward references, cycles in recovery actions, raw selectors, absolute paths, URLs with query strings, emails, tokens, or non-synthetic free text fail validation.

## 7. Capture envelope

One capture contains only normalized, redacted product facts:

```json
{
  "capture_schema_version": 1,
  "scenario_id": "UI-EDITOR-001",
  "side": "reference|local",
  "profile": "free-owner-chrome-desktop-ko-KR",
  "viewport": {"width": 1440, "height": 900, "scale": 1},
  "state": {},
  "keyboard": {},
  "focus": {},
  "a11y": {},
  "semantic_visual": {},
  "error_recovery": {},
  "geometry": {},
  "latency_ms": 0,
  "action_log_sha256": "sha256:<64 lowercase hex>",
  "screenshot_sha256": "sha256:<64 lowercase hex>|null",
  "source_build_sha256": "sha256:<64 lowercase hex>",
  "redaction": {"status": "pass", "rule_set": "core-free-ui-v1"}
}
```

The exact schemas must reject extra keys, duplicate keys, non-finite numbers, booleans used as numeric fields, absolute paths, unsafe relative paths, and unredacted values.

Screenshots stored in the repository are redacted derivatives only. Raw screenshots remain in the approved temporary evidence root, are never required for a source checkout, and have an explicit deletion state. Full DOM, full accessibility tree, browser storage, and network logs are not artifacts.

## 8. Redaction contract

The UI redactor is allowlist-based.

Allowed visible strings:

- scenario and action IDs;
- `REF-FREE-A`, `REF-FREE-B`;
- `UI-PARITY-<opaque-run-id>-<kind>` fixture labels;
- fixed Korean synthetic strings declared by the scenario;
- reviewed generic UI role/state vocabulary.

It must reject or replace:

- email-like strings;
- bearer/cookie/authorization material;
- absolute Windows or POSIX paths;
- query parameters named token, key, secret, signature, sig, or code;
- unknown workspace/page/user labels;
- UUIDs or platform identifiers not declared as synthetic aliases;
- any string outside the scenario allowlist in screenshot OCR or normalized captures.

A redaction failure invalidates the artifact. The harness must not downgrade it to an unknown product result.

## 9. Comparator policy

During `precalibration`:

- state, keyboard, focus, a11y, semantic-visible state, error recovery, workspace isolation, and Second Brain mutation counts are exact;
- geometry and latency are collected but cannot independently produce a `differential-passed` claim;
- pixel identity, Notion branding, logos, illustrations, exact colors, and proprietary copy are forbidden comparison targets.

After the first approved synthetic pair:

1. record reference and candidate hashes;
2. identify semantic regions used by the journey;
3. measure repeat-run variance on the same side;
4. propose geometry and latency thresholds larger than measured harness noise and no larger than the reviewed product tolerance;
5. create a versioned policy file;
6. bind its hash in evidence and checkpoint;
7. rerun the scenario before promotion.

The historical `2px/500ms` policy remains usable by its historical scenarios but is not evidence that the Core UI policy has been calibrated.

## 10. Browser mechanism decision

The initial audit found no persistent Playwright, Puppeteer, Selenium, or WebDriver runner. The accepted `docs/notion_parity/adr/UI_BROWSER_RUNNER_ADR_20260814.md` now pins a test-only Python Playwright runner for isolated local Chrome and explicitly forbids using it with the signed-in Notion profile. No browser binary download or persistent browser profile is part of that contract.

H1 uses two stages:

1. **Connection proof:** use the available in-app Browser against the already signed-in Chrome context to perform C0 and confirm `REF-FREE-A/B` without opening personal content. This produces no mutation and no unredacted repository artifact.
2. **Repeatable local run:** first attempt the same browser control against the isolated local app. If it cannot produce a deterministic action log plus focus/a11y/geometry capture that can be rerun without model interpretation, H1 must stop before claiming repeatability and write an ADR for a persistent browser runner.

The ADR must compare at least:

- compatibility with the installed Chrome/Chromium and Windows host;
- deterministic IME, focus, pointer, clipboard, screenshot, and a11y support;
- package/version/license and lockfile impact;
- installation and removal commands;
- CI and offline-cache behavior;
- raw artifact and credential boundaries;
- exact files to own.

The recommended default, if a dependency is approved after that check, is a browser-test-only Playwright runner. This is a recommendation, not authority to install a package during the Ultra planning turn.

## 11. UI0A baseline prerequisite

The current adjacent workspace/auth selection has one reproducible baseline failure:

`test_notion_workspace_auth_isolation.py::test_app_code_factory_import_graph_excludes_workspace_overlay_modules`

The subprocess test stubs `knowledge_core.register_knowledge_core` with a two-argument lambda while the current `app_code.create_app()` passes the supported keyword arguments including `environ`. This is classified as concurrent test-signature drift, not a demonstrated product regression.

Rules for xhigh:

1. rehash `AGENTS.md`, `app_code.py`, `tests/test_notion_workspace_auth_isolation.py`, and the imported Knowledge registration boundary;
2. rerun only the failing test once;
3. if the same mismatch remains and no owner is editing the test, own only the test file and update the subprocess stub to accept the current documented keyword contract;
4. do not edit `app_code.py`, `knowledge_core/**`, or Knowledge documentation from this slice;
5. rerun the single test and the full L3 selection;
6. if ownership or behavior differs, record a gap and continue only with new H1 files; do not claim L3 green.

This prerequisite blocks a full adjacent-boundary-green claim. It does not require personal data and does not block writing new, import-isolated H1 contract files.

## 12. UI1 golden journey transactions

The local journey is one user flow but contains separately asserted effects:

1. shell opened;
2. sidebar collapse and restore preserve the expected width/state;
3. search opens, Escape dismisses it, and focus returns to the originating control;
4. a synthetic page is created;
5. a Korean title and paragraph are committed;
6. reload reproduces the exact content;
7. dirty state is flushed before Second Brain preview;
8. preview cancel changes neither Knowledge canonical state nor publication count;
9. approval creates exactly one immutable synthetic publication with the matching source revision/hash;
10. response-loss replay or repeated UI activation does not create a second publication.

Any local API gap discovered during the journey is reported with the exact request, response class, state before/after, and smallest required ownership expansion. H1 does not silently edit routes, stores, schemas, migrations, auth, app registration, or Knowledge Core.

## 13. Verification ladder

Run serially from `G:\AI_Agent\DEMOS_CODEX`. Do not supply a workspace-local `--basetemp`.

### H1-L0 — contracts and import isolation

- validate every new JSON with duplicate-key rejection;
- run the new scenario verifier tests;
- import each new harness module in a clean process;
- assert forbidden app/auth/config/data-path/Knowledge/Second Brain modules were not imported;
- assert filesystem state did not change on import.

### H1-L1 — existing comparator/evidence regressions

```powershell
venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\test_notion_evidence_policy.py `
  code_assist_v1\tests\test_notion_differential.py -q
```

### H1-L2 — existing Node UI contracts

Run the six Node scripts listed in the parent plan individually and serially.

### H1-L3 — route/publication and adjacent boundary

Run L2 and L3 from the parent plan. Record the known UI0A failure separately until it is owner-resolved.

### H1-L4 — local real-browser repeatability

- run the same isolated fixture twice;
- require identical action ordering and exact state/focus/SB results;
- allow only reviewed volatile fields to differ;
- require no live-path access and no raw artifact in the repository.

### H1-L5 — reference observation

- perform C0 first;
- operate only on `REF-FREE-A/B` with synthetic content;
- bind reference evidence to the same action IDs;
- stop on personal content, unknown label capture, plan/invite/public mutation, or redaction failure.

### H1-L6 — comparison and promotion

- compare exact sections;
- retain geometry/latency as precalibration measurements;
- record product differences without patching them during the same evidence run;
- promote only the evidence state actually proved.

## 14. xhigh work packets

### Packet A — UI0A baseline ownership

Outcome: the known adjacent test is either green by a test-only compatibility fix or explicitly owner-routed.  
Production changes: none.  
Stop: shared Knowledge or app ownership is required.

### Packet B — scenario overlay and verifier

Outcome: the full Core denominator and the six UI1 scenarios are machine-readable and fail-closed.  
Production changes: none.  
Gate: generated counts, profile restriction, ID uniqueness, cross-reference integrity, and non-goals tests pass.

### Packet C — evidence/action/redaction library

Outcome: deterministic in-memory action/capture manifests validate, redact, hash, and compare.  
Production changes: none.  
Gate: adversarial secrets/paths/extra keys/duplicate keys/cycles/non-synthetic strings all fail.

### Packet D — isolated local browser smoke

Outcome: real browser executes the local shell/search/edit/reload subset twice.  
Production changes: none unless a measured blocker is separately accepted.  
Gate: trusted OS temp only, repeatable exact result, no browser dependency drift without ADR.

### Packet E — synthetic Notion reference observation

Outcome: the same common action IDs are observed in `REF-FREE-A/B`.  
Production changes: none.  
Gate: personal workspace never opened, artifact redaction passed, official build/profile/date recorded.

### Packet F — first differential and UI correction selection

Outcome: exact diff ledger plus one next cohesive UI correction, not a broad refactor.  
Production changes: none in the evidence run.  
Gate: reference/candidate/policy hashes and reviewer decision recorded.

## 15. Stop and handoff

Stop immediately on:

- owned-file hash drift;
- personal or production content exposure;
- missing isolated data root;
- raw DOM/network/cookie/token capture;
- unredactable artifact;
- need for trial, purchase, invite, public posting, migration, live flag, continuous consumer, or resolver cutover;
- need to edit protected files without explicit ownership;
- auth, CSRF, origin, zero-leak, or Second Brain regression;
- repeated host-dependent process failure.

At every packet boundary, write a unique checkpoint with source hashes, exact command and exit code, evidence hashes, live-mutation booleans, known failure classification, and the next exact command. Since the workspace is not a valid Git repository, do not claim a commit, branch, or landed state.

## 16. Model handoff

Ultra owns scope, dependency order, risk boundaries, evidence requirements, and this design. xhigh owns one work packet at a time and must not redesign H0–H9 unless current evidence proves a contract conflict.

This split follows the official OpenAI model guidance to use high/xhigh where additional reasoning shows measured benefit and to provide stable domain context, hard constraints, approval boundaries, and success criteria for complex multi-turn work: `https://developers.openai.com/api/docs/guides/latest-model`.

The current xhigh continuation command is not a production edit. It is:

```powershell
Get-FileHash -Algorithm SHA256 `
  code_assist_v1\AGENTS.md, `
  code_assist_v1\docs\NOTION_UI_SECOND_BRAIN_XHIGH_HANDOFF_PLAN.md, `
  code_assist_v1\docs\notion_parity\CORE_FREE_WEB_UI_HARNESS_DESIGN.md, `
  code_assist_v1\docs\notion_parity\checkpoints\ui-resp-001-local-viewport-green-reference-pending-20260815.json
```

Packets A-D and the current local Chrome ladder through H8 responsive coverage are already green
and must not be rebuilt. If signed-in C0 is safely available, prioritize
synthetic `REF-FREE-A/B` characterization; otherwise continue with
H8 `UI-A11Y-001` full-surface keyboard/focus/role/name/state coverage, then
measurement-only `UI-PERF-001`,
preserving the existing reference-pending claim boundary.

## 17. H8 accessibility packet result and performance handoff

`UI-A11Y-001` is locally green in the isolated real-Chrome harness. Its
machine contract now requires:

- one deterministic enumeration of all visible native/composite tab targets;
- exact actual-Tab traversal equality with that enumeration;
- accessible name/role/state and a visible focus indicator for every target;
- forward and reverse focus containment for search and publication dialogs;
- exact trigger restoration after search/share/unlocked-modal dismissal and
  explicit publication cancellation;
- reduced-motion and forced-colors media behavior;
- external request count zero and Second Brain item/revision delta zero; and
- exact identifier-free report equality across two independent OS-temporary
  roots.

The sealed local report records 36 accessible targets, 36 visible focus
indicators, two focus traps, no external requests, and no Second Brain
mutation. The full isolated Chrome suite is 21 green; the Node, route,
publication, Second Brain, redaction, evidence, and reference-guard ladders are
also green. No scenario status is promoted without a reference observation.

The next implementation packet is `UI-PERF-001`. It must measure, rather than
guess, at least shell-ready, page-ready, editor-input acknowledgement, search
open/result, save/reload, and Second Brain preview readiness. Run it twice in
independent isolated roots, retain only identifier-free timing summaries, and
classify values as measurement-only until a reviewed reference differential
or explicit local service-level budget supplies a threshold. A timing result
alone must not change scenario status.

## 18. H8 performance packet result and H9 handoff

`UI-PERF-001` now uses a dedicated real-Chrome measurement contract. One
synthetic page is prepared outside the timed sequence per trusted temporary
root, and three fresh contexts per root measure the adopted action sequence.
The report binds every distribution to the scenario/action log, fixture alias,
source build, profile, exact Chrome version, and successful semantic state.

The stable metric set is:

- `shell_ready_ms`;
- `page_ready_ms`;
- `editor_input_ack_ms`;
- `save_ready_ms`;
- `reload_ready_ms`;
- `search_open_ms`;
- `search_result_ms`; and
- `second_brain_preview_ready_ms`.

Every distribution contains min, nearest-rank p50/p95/p99, max, and sample
count. The test requires finite nonnegative ordering, six samples, two roots,
zero external requests, exact reload restoration, and zero Second Brain
mutation. It deliberately contains no product latency threshold.

The current local measurement result is green and the combined browser suite
is 22 green. No production UI correction was made for performance. H9 must now
exercise an isolated synthetic pilot and an explicit rollback/recovery path,
then audit every Core requirement against current evidence. H9 cannot promote
the 25 Core scenarios while `REF-FREE-A/B` reference/differential evidence is
missing, and it must not silently convert measurement-only latency values into
a release budget.

## 19. H9 isolated pilot/recovery foundation

The repository now has an offline-only `ui_pilot` contract. It is import-side
effect free and unregistered. Authorization checks exact opt-in, kill-switch,
synthetic-only, and six readiness booleans before resolving any path. It then
accepts only a descendant of the trusted OS test root with the frozen synthetic
sentinel.

Recovery is deliberately non-destructive: a quiesced source is copied into a
new snapshot root, exact tree contents are verified internally, and restore
copies that snapshot into another new root. Links/junctions, existing targets,
sentinel drift, fingerprint drift, missing quiescence, or an engaged kill
switch fail closed. Raw tree hashes remain temporary and are not repository
evidence.

The real-Chrome H9 rehearsal proves baseline save/reload, zero-delta preview
cancel, kill-switch denial, one canary edit and one Second Brain publication,
and restoration of the baseline content and zero publication state. The
changed canary remains intact. The identifier-free aggregate and support
procedure are sealed in the H9 result and pilot runbook.

No production feature flag exists. Consequently this is a pilot/recovery
foundation, not H9 completion. Reference/differential, approved latency budget,
formal human review, operational readiness/kill switch, and live canary gates
remain open.

## 20. Completion-matrix contract

The checked-in completion matrix is the handoff boundary between local proof
and official-reference parity. It binds, by SHA-256, the scenario plan, latest
H9 checkpoint, and one local evidence document for every required scenario.
The corresponding read-only verifier recomputes the denominator and state
counts, validates every bound path and hash, and fails closed on any attempt to
invent reference, differential, promotion, H8, H9, Core, or overall completion.

The current exact state is 25 required, 24 functional local browser green, one
local performance measurement green, and zero reference, differential, or
promotion results. This is the maximum truthful local claim. Pack E remains
the next product-relevant packet whenever the signed-in browser transport can
be initialized safely; Pack F and any cohesive UI correction require that
reference evidence first.

## 21. Manual Pack E transport

Pack E may now receive a human-observed submission when automatic signed-in
Chrome control is unavailable. The manual transport is not a relaxed path. It
requires the same identifier-free passing C0, verified scenario actions,
ordered `capture_after` denominator, capture/action/source-build hashes,
allowlist-first redaction, and synthetic-only operator attestation.

The draft is deliberately invalid until every observed field is filled and a
human marks the redaction review complete. File-reading CLI commands require
an explicit synthetic-only flag before opening the supplied path. An accepted
receipt contains hashes and counts only and always reports
`promotion_allowed=false`; sealed reference evidence and a reviewed
differential remain separate later steps.

The first manual packet is `UI-SHELL-002`, which is read-only and exercises
sidebar collapse/restore. Do not use the manual transport to execute local
Second Brain actions in Notion. The exact operator procedure is
`docs/notion_parity/core_free_web_ui/CORE_FREE_WEB_UI_MANUAL_REFERENCE_INTAKE_RUNBOOK.md`.
