# Timeline undated-item display — R5 (2026-09-26)

Read-only inspection of the signed-in Notion comparison page `Codex UI 비교 —
연결된 DB 원본 2026-09-16` at 1280×720 showed its `Codex 날짜 비교` timeline
view with one dated item in the visible timeline and one undated item under
`날짜 없음(1)`. The undated item did not occupy a second timeline row. The
comparison page was returned to its original table view, and the original
Notion tab URL was restored without editing page content.

The local timeline previously rendered every visible item as a timeline row,
including an undated item already listed in its dedicated menu. It now filters
undated items from timeline rows while retaining them in that menu. The menu
count spacing matches the observed Notion label. An isolated Chrome test
failed before the change with two rows, then passed with one dated row and
the undated item in the menu. The focused browser test passed (1 test, 15.94
seconds); 12 date/view Node checks and JavaScript syntax also passed.

The normal development address on port 10322 was unavailable during this
comparison, so the local result comes from an isolated application browser
fixture. Other timeline layouts, row drag selection, roles, responsive sizes,
and full R1–R11 parity remain open. No production feature flag or user data
was changed.

Follow-up, 2026-09-26: Opening the actual Notion `날짜 없음(1)` menu without
changing the comparison page showed the instruction `클릭하여 타임라인에 추가하세요.`,
the undated item's title, and a separate `열기` link. The local timeline menu
now uses the observed instruction. Clicking the real Notion item would change
its date, so the exact date/slot Notion assigns remains unverified. The local
owned-database action currently assigns today's local date; this is a local
behavior, not a confirmed Notion match.

The legacy local action was tested through save and reload: the item leaves
the undated menu and appears once in the timeline. A separate collaborative
browser test first showed that the same click only changed the block cache.
The handler now routes canonical rows through the versioned atomic cell write
and refuses a local-only change when that capability is absent. The isolated
browser checks cover the command dispatch and disabled-capability case. A
separate browser journey also passed against an isolated 2423 test store: the
menu click sent an authenticated cell command, the saved date was read back
from the canonical database, and the refreshed timeline showed the row once
with no remaining undated menu. The signed-in Notion page was returned to
table view and the user's original Notion URL was restored.

After the final menu wording and save-path changes, the focused browser suite
passed all three cases in 45.46 seconds. Sixteen date and linked-view Node
checks and the JavaScript syntax check also passed. The served script URL was
versioned as `20260926-ui58-timeline-date` so a resumed development server
loads the new handler.

The focused 2423 browser journey passed independently in 38.21 seconds, and
all four timeline-undated browser cases passed together in 79.15 seconds.

Follow-up, 2026-09-26: A fresh comparison used only two temporary items in
the signed-in Notion comparison database. In the month-scale timeline,
clicking an item in `날짜 없음(2)` assigned 2026-09-26 through 2026-09-30,
removed that item from the undated list, and kept the menu open as
`날짜 없음(1)`. In the week-scale timeline, the same action assigned only
2026-09-26 and also kept the menu open. Each temporary item was moved to
Trash after its date was checked; the original two rows, table view, and
month scale were restored. Other scales were not compared directly.

The local date assignment now matches the observed month and week behavior:
month assigns five local calendar days, while week assigns today. Calendar
continues to assign today. Owned and linked database actions preserve the
open undated menu after a successful refresh when items remain, including
the search term. The canonical path retains its versioned cell write. The
focused browser suite and Node checks verify date values, menu state, save
and reload, and the isolated 2423 database write.

Validation for this follow-up: all four pre-existing timeline browser cases
passed in 80.44 seconds after their month-range/menu expectations were
updated. A new week-scale browser case and a search-term preservation check
passed with the changed owned-database case (2 passed in 35.41 seconds).
Twenty-four focused Node tests and JavaScript/Python syntax checks passed.
A separate browser case for a linked timeline refreshed its canonical rows
after the date write and confirmed that the five-day value, the open menu,
the remaining count, and its search term survived the redraw (1 passed in
23.86 seconds). The linked case uses an isolated synthetic source; the 2423
owned-database case above exercises the authenticated persistence path.
