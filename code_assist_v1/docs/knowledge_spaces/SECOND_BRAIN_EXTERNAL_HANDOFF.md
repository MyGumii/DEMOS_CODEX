# Second Brain external validation handoff

Date: 2026-08-25 Asia/Seoul

This handoff separates locally verified Second Brain behavior from checks that
need company credentials, target-server authority, or operator control of an
existing port owner.

## 1. Locally verified release candidate

- Code root: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`
- Disposable data root: `D:\CodexTestTemp\SBE8-Gold-20260825-v1`
- Read-only organization source:
  `D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2`
- Marker SHA-256:
  `83c35d85ab9cdaba4359c33d9ccb2fa915c22d134220f99eb7a7327e70200ee1`
- Inspection URL while retained: `http://127.0.0.1:10211`
- Verified: 70 copied members, 140 personal Wikis, 505 meaningful gold pages,
  multi-Wiki exact targeting, personal Wiki ordering, independent personal+Part
  copies, Part/Team/Responsibility sharing and immutable sessions,
  Team/Responsibility native authority, exact distributions/references,
  Part-local and higher aggregate graph, unrelated-page detachment,
  automatic-edge provenance/correction, temporal read, legacy graph/index/log
  compatibility, restart-idempotent seed, and local Codex grounding.
- Company API calls during these checks: zero.

Detailed corpus/authority evidence is in `evidence/SBE-8.1.md`. Its integrated
UI completion claim is superseded by `evidence/SBE-8.2.md`, which records the
selector-shape, initial-workspace, and generic inventory-query repairs plus live
Codex conversation proof. `evidence/SBE-8.3.md` extends that proof to the real
admin phrasing `현재 위키 설명해줘` and records the exact post-restart Codex turn.
`evidence/SBE-8.4.md` records the canonical/legacy conversation-ID state repair
for new sessions and stale pre-restart session rows.
`evidence/SBE-8.5.md` replaces phrase-by-phrase Wiki inventory matching with a
bounded structural intent recognizer and proves the exact mixed `wiki` plus
Korean `정보` questions that failed in the real admin path.

## 2. Exact local launch/probe command

Run from `G:\AI_Agent\DEMOS_CODEX` after the requested port is free. Port
10211 is the retained inspection lane because 10111 and 10121 have independent
owners:

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.full_second_brain_preview `
  --data-root D:\CodexTestTemp\SBE8-Gold-20260825-v1 `
  --organization-source-root D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2 `
  --port 10211 --codex --probe --probe-codex `
  --password <preview-admin-password> --serve
```

Expected pre-serve JSON includes `status=ready`, both gate statuses `pass`,
`company_api_called=false`, at least one local/aggregate automatic edge, one
explicit reference, and one read-only distribution. `/healthz` must return the
same marker hash and schema version 2. The retained launcher now also starts
the durable and projector children; health must report both children as
`alive=true` and the requested provider mode.

Do not change the port to 10111 while another process owns it. Inspect first:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 10111 |
  Select-Object LocalAddress,LocalPort,OwningProcess
Get-CimInstance Win32_Process -Filter "ProcessId=<pid>" |
  Select-Object ProcessId,ParentProcessId,CreationDate,ExecutablePath,CommandLine
```

The current owner observed in this handoff is PID 29464,
`approved_dev_pair`, approval ID `AP-DEV-PAIR`. Stopping or replacing it is an
operator decision; this handoff does not authorize that action.

## 3. Company-model validation

Owner/authority required: company Codex or model-platform owner with approved
API credentials and permission to use the frozen held-out corpus.

Required work:

1. Bind the existing provider configuration through the approved secret lane;
   do not store credentials in this repository or preview root.
2. Run `AUTOEDGE-016` with frozen model, prompt, scoring, and corpus versions;
   record precision, false-positive suppression, failure fallback, latency, and
   cost.
3. Run `RANK-014` on the frozen Korean/English per-scope corpus; record leading-
   ten relevance, full-result/96-KiB-context behavior, unauthorized citation
   count (must be zero), latency, and exact
   model/reranker versions.
4. Exercise structured judge/reranker schema failure, timeout, retry, and
   deterministic fallback. A provider failure must remain enrichment status and
   must not turn a successful upload into failure.
5. Save only sanitized metrics/evidence. Never copy company prompts, secrets, or
   protected corpus content into the personal workspace.

## 4. Target Windows server and operations

Owner/authority required: target-server administrator, service-account owner,
storage/backup owner, and pilot owner.

Required work:

- Rebaseline and run the complete Knowledge Spaces plus adjacent root regression
  in both supported working-directory forms.
- Execute the named cross-process authority, publication, lifecycle, edge, job,
  projection, and temporal races; record one-winner/replay/no-partial-effect
  results and bounded busy behavior.
- Re-run the completed 7-Part x 10-user gold gate on the target image, then run
  1/100/1,000-Wiki query-plan measurements and the target-class 40-client load
  profile.
- Rehearse signed backup/restore of DB, blobs, profiles, roles, conversations,
  local/aggregate edges, suppressions, temporal records, and projections.
- Repair/enable the approved real-browser runner and execute admin/member/Part
  leader/Team leader/executive/stale-role/archived-Wiki/large-list/keyboard/
  Korean UI flows.
- Validate service-account ACLs, startup/restart, health, logs, monitoring,
  rollback, and roll-forward. Then execute rebaselined P7.8/P7.9 and the P8 pilot
  only with explicit deployment/migration approval.

## 5. Frozen migration boundary

The release candidate did not alter migration sources. Expected SHA-256:

| Migration | SHA-256 |
|---|---|
| v10 | `1b7f161d393f758083ea32f04e0b6afec5b7132de3b0485899d8000fe8faa15c` |
| v11 | `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b` |
| v12 | `ead553153d5f3b2366dfe4c9fa665cf087087fb3410ad3519f27597cf3b7e9d2` |

Any mismatch stops the handoff; do not regenerate or edit a released migration.

## 6. Release-candidate implementation hashes

| File | SHA-256 |
|---|---|
| `knowledge_core/services/editing.py` | `3d43d57b1dd4905012353c4aaabfec98d2ecdb9d46d7fc022c60174005eee04e` |
| `knowledge_core/worker.py` | `680adae6ebfb3cd475cee735c70b00cc8319c671a058af2daa302a6a5bbf0f7f` |
| `knowledge_core/repositories/management_reads.py` | `1fe50808cf003d013e7f25ab995f6addea7ecca3b548a607f384e1afc607b921` |
| `knowledge_core/repositories/higher_spaces.py` | `fd87cac640dc47ca8a2ff3219bf946ba3957cf9675da37c3f2b616753b37a094` |
| `knowledge_core/canonical_routes.py` | `1052fbbe736de41602508f05f1894503192d85fd30b8e13e2c78359fd51bcea7` |
| `static/knowledge_scope.js` | `834203aac6210965e439eb17c49816cfebf49ebbee2f27be8f2509de552dd559` |
| `static/knowledge_core_transport.js` | `4188f43168b0547024c0b76386ba8f29a59663df62c05dc69a636c2967953ecb` |
| `static/personal_wiki_ui.js` | `ff0c2e44fb228d978d030eab2081f2eb883adc3a1680e28818090920fb3ec9ba` |
| `static/knowledge_core_browser.js` | `2db37eb2b21301032cecd5c4ca48c7068d1c6dc58847235169fa4afa2681ac96` |
| `static/admin_organization.js` | `65f36e170a46190080536774bc8be41703121a73b46a8830c573998ec1a52eb5` |
| `tools/full_second_brain_preview.py` | `5c7eea4ee12caf40867dad5e707fbc49425e5630f1dca21ea1bbfd94e57dca37` |
| `tests/knowledge_spaces/unit/test_knowledge_core_browser_transport.py` | `79a404cc014a24e9b5347e80dfd6019fc79b61fffd863970629d19256249e873` |
| `tests/knowledge_spaces/integration/test_full_second_brain_preview.py` | `4d42b926d789af3e2f15efc61cf803d3da7c855ec87f7eb445b3572256ad7680` |

## 7. 2026-08-26 operational-worker verification

The retained `10211` inspection lane was restarted in place without changing
the independent `10111` approved-dev-pair listener. Its current health contract
is:

```text
status                 ready
marker_sha256          83c35d85ab9cdaba4359c33d9ccb2fa915c22d134220f99eb7a7327e70200ee1
worker kinds           durable, projector
worker liveness        true, true
provider mode          codex-validation
company_api_called     false
```

The worker validation used only disposable roots. In the Codex lane, 18
unjoined semantic intents became durable jobs and automatic decisions/edges
continued committing outside HTTP requests. A separate OpenAI-compatible
loopback run completed five queued semantic jobs and verified the exact
non-stream `chat/completions` request/JSON-response contract. The temporary API
registry was restored byte-for-byte.

Authenticated live HTTP checks on `10211` returned `200` for personal, Part,
Team, and Responsibility items/graph/activity plus the legacy graph,
`index.md`, and `log.md` compatibility endpoints. A fresh Part-bound canonical
conversation completed through SSE and persisted a Codex answer containing
the authorized Part Wiki markers `PART-SBV-P01-U04-ANSWER` through
`PART-SBV-P01-U10-ANSWER`.

The initial 505-document fixture creates a deliberately large outbox. The
projector drains it durably in the background rather than bulk-acknowledging
events without manifest verification. During the retained run, applied outbox
events increased from `6` to `90`, ready projection states from `12` to `161`,
and physical projection files from `138` to `1,488`; all durable jobs were
`succeeded`. Canonical APIs remain usable while this replaceable projection
backlog converges.

Local regression evidence:

```text
24 passed in 40.11s
11 passed in 25.62s  # focused post-health rerun
py_compile: pass
ruff: unavailable in the repository venv
```

Updated implementation hashes:

| File | SHA-256 |
|---|---|
| `tools/full_second_brain_preview.py` | `db2c63730bb12d4ec477e2b64d6e786a4135a7228f4c3212e5f416234204eeed` |
| `tools/full_second_brain_preview_workers.py` | `0669cc1981e72f6a65f531798eec5f6e0332b3f9831802566adede13854e3f58` |
| `tests/knowledge_spaces/unit/test_full_second_brain_preview_workers.py` | `78d4f8a397dc796b8a4727e97778ba4e8bf31f88d6c597f1f4b029e4bab6c0d1` |
| `tests/knowledge_spaces/integration/test_full_second_brain_preview.py` | `02bf8313982ea413a3359f45739bb6ce6c2d3e97c6ef9a2edd09e5043deeb808` |

Company-side OpenAI-compatible endpoint/model execution remains the only
provider-specific handoff. Follow `docs/COMPANY_OPENAI_COMPATIBLE_HANDOFF.md`;
do not treat the Codex or loopback pass as company endpoint evidence.

## 8. 2026-08-29 clean v13 release-candidate baseline

The current successor baseline supersedes the retained 10211 corpus above for
functional handoff purposes:

```text
inspection URL          http://127.0.0.1:10221
data root               D:\CodexTestTemp\SBE9-V13-20260827-v6
marker SHA-256          69f86898010bcca2b0f114fe7ec4f77e3026da9be14c12048fcc4b9fc7697fb5
provider mode           codex-validation
company_api_called      false
health                  ready
workers                 one durable + one projector, both alive
```

The clean matrix preserved the administrator's eight original items byte for
byte, uploaded 190 real DOCX/PPTX/PDF files through the public routes, produced
220 publications, passed all 19 strict graph/index/log/projection audits, and
completed 62/62 fresh canonical Codex Agent turns. The report is
`D:\CodexTestTemp\SBE9-V13-20260827-v6\.runtime\v13-document-matrix-OPEN-READY-20260828-V1.json`
with SHA-256
`fe53abdfb9b2c5b79a07a015ad65e6e61b1b59a1f7513fbbec65cfec939794e3`.
Unresolved failures are zero. The final repository regression is 1,966 tests
plus five subtests and 31 focused tests, all passing.

The provider-neutral semantic prompt is now
`automatic-edge-judge-v4-relation-policy`. Company validation must preserve
its generic relation/direction policy, exact canonical evidence-location
validation, fail-closed provider behavior, and absence of topic-specific
few-shot examples. Re-run both the semantic quality corpus and all 62 routing
cases with expected body values kept out of model prompts.

SBE-8.11 closes the real-Chrome scope-selector sub-gate on 10221: personal and
Part are enabled, Team/Responsibility are disabled only in the web control,
the backend returns all four authorized descriptors and 200 selection/read
responses, and browser console errors are zero. Drag/drop multi-upload, body
popup, Index, Log, local/aggregate Graph, and other end-to-end browser journeys
remain external.

The current retrieval contract is no longer final-ten. All four backend view
kinds preserve authorized full-corpus ranked/body evidence without a fixed
document-count cap, while mandatory plus ranked/supplemental model excerpts are
exactly capped at 96 KiB and mandatory pins/references remain max four. Local
regression is 1,977 unique Knowledge Spaces tests plus five subtests and 109
focused/contract tests, all passing. See `evidence/SBE-8.11.md`.

Remaining production gates are unchanged: exact company model/auth/TLS/
streaming/error/rate behavior, held-out semantic and retrieval quality,
target-class 40-client load, signed off-volume backup/restore, service-account
operations, and approved cutover. The 10011 and 10111 roots were not modified
by this baseline.
