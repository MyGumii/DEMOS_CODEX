# Feedback search, filters and pagination — verification

Scope: the approved follow-up to
[the workspace feature amendment](../WORKSPACE_FEATURES_20260907.md).
Verified 2026-09-07 KST. This report does not close historical company-provider
or production-load gates.

## Delivered

- Compact title/requester/status/date rows; long titles and requester labels
  truncate visually and expose their complete text in tooltips.
- Search across title, body, requester display name and account ID, with
  selectable title/body/requester-only search. Multiple search terms combine
  with AND across the selected fields; `%`, `_` and backslashes are literal.
- Combined type, processing-status and authenticated `내가 쓴 글만` filters.
- Previous/next replaces one 30-row page. The footer remains reachable while
  scrolling; no continually accumulating `더 보기` rows remain.
- Returning from a post restores the applied criteria, page, scroll and focus.
  Changed filters reset to page one. A newly created post opens the unfiltered
  first page. If the last page empties during a detail view, return falls back
  to the nearest remaining earlier page.
- No Wiki, retrieval, graph, LLM prompt or canonical-schema changes. Feedback
  schema v1 remains unchanged. Frontend asset version: `20260907c`.

## Tests

Final regression command from `G:\AI_Agent\DEMOS_CODEX`:

```powershell
$env:SB_FEEDBACK_EVIDENCE_DIR='G:\AI_Agent\DEMOS_CODEX\workspace-feature-evidence\board-search'
.\venv\Scripts\python.exe -E -X utf8 -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_workspace_features.py code_assist_v1/tests/knowledge_spaces/unit/test_source_artifact_routes.py code_assist_v1/tests/knowledge_spaces/unit/test_knowledge_core_browser_transport.py code_assist_v1/tests/knowledge_spaces/unit/test_feedback_board_search.py code_assist_v1/tests/knowledge_spaces/unit/test_feedback_board_browser.py -q --disable-warnings
```

**85 passed in 44.17 seconds.** JavaScript syntax check passed.

- WF-006: Korean requester-name substring and case-insensitive ASCII account-ID
  search; title-only/body-only behavior; cross-field multiple terms; literal
  wildcard characters; combined type/status/mine filters; deleted posts excluded;
  malformed criteria rejected with HTTP 400; HTTP route passes all filters.
- WF-006: 65 posts with tied creation timestamps produce disjoint pages of
  30/30/5 rows. Reusing a cursor returns the same next page after the anchor is
  logically deleted and a new post is inserted.
- WF-007: real Edge, production board JavaScript/CSS and Modal implementation,
  real HTTP and SQLite against an isolated temporary fixture. Sixty-five posts
  belong to two accounts. The test checks page replacement, previous/next,
  requester-name/ID and body search, filters, page-two return with exact row IDs,
  restored scroll/focus, empty-page fallback and another account's mine filter.

Fixture posts for the multi-page test were **not** added to the user's 10221
board. That test uses a minimal isolated shell, not production authentication;
the full-app verification below covers the actual login/CSRF boundary.

Final full-app runner on `http://127.0.0.1:10221`:

```powershell
.\venv\Scripts\python.exe -E -X utf8 -m code_assist_v1.tools.verify_workspace_features_browser --output G:\AI_Agent\DEMOS_CODEX\workspace-feature-evidence\board-search\full-app --password <validation-password>
```

**16 checks passed, zero uncaught page errors.** Two real login contexts verified
requester-ID search and mine-filter isolation in addition to the existing
downloads, pasted/dropped attachments, authorship restrictions, edits, deletion
and return-to-board checks. The labeled smoke post was logically deleted by
its author. Pre-existing posts and Wiki data were not test mutation targets.

The report retains two expected HTTP failures: unavailable optional shadow
metrics (handled 404) and an intentionally intercepted failed save (503) used to
verify draft retention. They are not uncaught application errors.

## Evidence and runtime

Evidence root: `G:\AI_Agent\DEMOS_CODEX\workspace-feature-evidence\board-search`.

- `search-pagination-report.json`
- `board-requester-search.png` — filters and compact rows, visually inspected
- `board-second-page.png` — restored list position
- `full-app/report.json` and `full-app/feedback-search.png`

Port 10221 was not listening after the interrupted run. It was started using
the existing marked `D:\CodexTestTemp\SBE9-V13-20260827-v6` data root. At final
verification the listener PID was 5408, durable worker 12408 and projector 38420
were alive, health returned HTTP 200, and startup's knowledge/index/graph checks
passed. Logs are `.runtime/preview-10221-feedback-search.stdout.log` and
`.stderr.log`; stderr was empty. Port 10011 was not changed.

## Deliberate boundaries

- Search/filter/page/scroll state is per account in the current browser page;
  a full browser reload resets it. Submitted posts remain persisted in SQLite.
- The server returns only a metadata page, and the browser renders at most 30
  rows. Search uses parameterized substring matching; it can scan stored text
  as volume grows. No constant-time search, indexed full-text search, total-page
  count, or production load-test claim is made.
- Ordering is by immutable creation time plus ID. This is a live board, not an
  immutable cross-request snapshot; concurrent edits can change search matches.
