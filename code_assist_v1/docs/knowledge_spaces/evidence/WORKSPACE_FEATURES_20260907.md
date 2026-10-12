# Wiki download / original link / feedback board evidence

## Follow-up: visible return-to-board navigation

The 2026-09-07 follow-up adds `← 게시판으로` above the detail content and in
the modal footer for every reader. The former small bottom `목록` button is
replaced. Footer navigation explicitly keeps the modal open when returning to
the list. Frontend JS/CSS cache versions are updated to `20260907b`; no backend
restart, schema or existing post changes are needed.

Verification: browser transport regressions **37 passed in 15.11 seconds**;
the expanded isolated Edge runner **15 checks passed, zero uncaught page errors**.
The author used the top button and another account used the footer button;
both returned to the visible board and reopened the same post successfully.
Evidence: `G:\AI_Agent\DEMOS_CODEX\workspace-feature-evidence\board-navigation\report.json`
and `feedback-other-user.png`. The labeled test post was logically deleted by
the author after verification; pre-existing posts were not modified.

Verified on 2026-09-07 KST against the isolated preview at
`http://127.0.0.1:10221`. This report covers only the approved
[workspace feature amendment](../WORKSPACE_FEATURES_20260907.md), not the whole
historical development goal or company-provider/production readiness gates.

## Delivered behavior

- Each Wiki row has `MD ↓` and `원본 ↓`. Native editors also have the `⋯`
  original-link dialog. The misleading direct-writing badge and the original
  download section inside the Markdown dialog are removed. Existing answer
  citation/source-download links are unchanged.
- The selected Wiki heading contains `Graph` and `전체 MD ↓`; the former Graph
  management-document card is removed. ZIP export includes all active authorized
  documents, not just the visible page, plus Index/Log, provenance and retained
  canonical images. Team/Responsibility frontend selection stays disabled;
  their authorized backend exports remain functional.
- The duplicate top new-session button is replaced with `버그·개선 요청`.
  The sidebar plus remains. Narrow desktop headers wrap within the viewport.
- The board supports shared list/detail, create/edit, raster image clipboard
  paste, multiple selected/dropped files, preview/removal and downloads.
  Only authors edit/delete content. Administrators can change status, but cannot
  edit/delete another author's post. Failed submissions retain the open draft.
- Original linking does not republish a Wiki or change its revision or graph.
  Existing source bytes are verified using the normal CAS storage boundary.

## Automated results

Command, from `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -E -X utf8 -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_workspace_features.py code_assist_v1/tests/knowledge_spaces/unit/test_source_artifact_routes.py code_assist_v1/tests/knowledge_spaces/unit/test_knowledge_core_browser_transport.py -q --disable-warnings
```

Final result: **72 passed in 35.27 seconds**, no skipped tests.
JavaScript syntax checks also passed for `workspace_features.js` and
`knowledge_core_browser.js`.

| Scenario | Evidence |
|---|---|
| WF-001 | Multipart original linking and replay; retained-source count increases only once; exact raw byte download through real Windows CAS; existing revision/row version/graph unchanged; foreign account denied |
| WF-002 | 113 same-title documents cross the 100-row page boundary without loss or filename collision; foreign Wiki excluded; concurrent inventory changes reject the export instead of returning a partial successful ZIP |
| WF-003 | Actual Edge browser individual MD, complete ZIP, original download and original-link dialog; no horizontal page overflow; Graph card absent and header control present |
| WF-004 | Two browser accounts read the same post; author edits/deletes; other account lacks controls and receives HTTP 403 for attempted deletion; removed attachments and deleted posts return HTTP 404 |
| WF-005 | Reopened SQLite store and browser reload preserve content; two separate Python processes competing with the same version produce exactly one commit and one 409; retries do not duplicate; simulated failed submission retains title and both attachments; anonymous access and missing CSRF denied |

The tests also assert the exact 8-path / 11-operation additive route set and
reject non-object JSON bodies with HTTP 400. Existing transport tests cover an
optional metrics request failing before slower document requests finish. Its
rejection handler is now installed immediately; non-403/404 errors still propagate.

## Browser results

Runner: `tools/verify_workspace_features_browser.py`, restricted by its CLI to
port 10221. Final result: **13 passed checks, zero uncaught page errors**.
The runner uses Edge at 1440×1000 with two authenticated contexts:
`sbv-p03-u02` and `sbv-p03-u01`.

- Clipboard: real browser Clipboard API image followed by keyboard `Control+V`.
- Drop: file-bearing browser DragEvent dispatched to the real drop zone.
- Failed submission: one intentional intercepted HTTP 503, followed by a
  successful retry with the preserved draft and attachments.
- Test posts are clearly labeled and logically deleted by their author after
  the check. Retained audit/history bytes are not physically purged.
- Existing Wiki content, sources and organization were read-only browser targets.

The final backend exports matched the complete authorized item lists and
contained nonempty bodies and Index/Log:

| Selected view | Documents | One-run export/check duration |
|---|---:|---:|
| Personal default | 2 | Not separately measured |
| Part | 10 | 0.541 s |
| Team | 40 | 1.609 s |
| Responsibility | 95 | 3.799 s |

These are individual functional measurements, not p95/p99 or load-test claims.

Browser evidence is in `G:\AI_Agent\DEMOS_CODEX\workspace-feature-evidence\browser`:
`report.json`, `knowledge-downloads.png`, `feedback-compose.png`,
`feedback-other-user.png`, and the downloaded files.
The report's HTTP errors are expected: the preview does not mount the optional
curation shadow-metrics endpoint (404, handled), and the intentional save-failure
simulation returns 503. Neither is an uncaught page error or a failed feature check.

## Existing-data preservation and original-file limitation

The following complete table snapshots had identical SHA-256 values before
restart and after browser verification:

| Table | Rows | SHA-256 |
|---|---:|---|
| knowledge_item | 228 | 5ab37e0d41022e3c0eb66fce9b2cbbfe2d77d0f7ade9159599b13e610202e409 |
| knowledge_revision | 229 | 4a510a46f8a9924dc4e5fa88022245bf89284aa3562a726cf88dfe46e7388591 |
| knowledge_edge | 146 | 7d46b1b6ecb4c05a82e238e9a66708a264982eb953bdfaa36318ee11ec533989 |
| item_source | 220 | 2ee99ecda9983eed35532868d40939735ba7418a6e49b48b0be277633493e004 |

Read-only inventory: the current root has 20 admin-created items, 12 with source
links and 8 without. The preserved organization/source root
`D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2` has 9 admin-created items,
none with source links, and zero source-artifact records. The inspected legacy
import path preserved Markdown/extracted assets, not a recoverable original-file
registry. No original was guessed from a similar title or attached to the user's
existing items during this task.

Therefore changing/updating port 10011 cannot reconstruct absent PDF/PPT/Word
bytes. If the user has the original, open `⋯` next to the matching native Wiki,
choose **원본 연결**, and attach it. Download becomes available without generating
a replacement Wiki. Historical files may be linked automatically only if actual
upload/digest evidence establishes identity; absence from these inspected roots
is not a claim that no other backup exists.

## Runtime and handoff

- Preview root: `D:\CodexTestTemp\SBE9-V13-20260827-v6`.
- Final listener PID at verification: 8480; durable worker 38796 and projector
  36540 alive. Startup functional gate returned knowledge/index/graph HTTP 200.
- Runtime logs: `.runtime/preview-10221-workspace-features-final.stdout.log` and
  `.stderr.log` in that root; stderr empty at final check.
- Feedback persists in `core/feedback.db`, independently of Wiki publication.
  Include this auxiliary SQLite database in deployment backup/restore and
  data-root transfers. Use SQLite-consistent backup or stop the process before
  copying; do not copy only a live main DB while ignoring its WAL.
- Transfer original links using the canonical DB **and** its verified blobs;
  copying Markdown alone does not transfer originals.
- Drafts survive a failed request and modal navigation in the current page,
  not a browser reload. Submitted posts survive page/process reopening.
- New features perform no LLM/API calls. Prompts, retrieval, reranking and graph
  scoring are unchanged. No Hynix access, company integration test or production
  load/restore gate is claimed by this report. Port 10011 was not restarted or
  migrated in this task.
