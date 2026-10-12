# Canonical database item detail

The row open buttons in owned canonical databases now open the shared item detail
panel by database ID and stable row ID. A row ID is not a page document ID and must
not be sent to the document side-peek route. Legacy document-backed items retain
their existing route.

## Editing and recovery

- Read the current database before opening. Page, actor, workspace, navigation
  generation, and competing item opens fence the asynchronous result.
- Use the existing authorized `database.cells.set` service contract. Keep the
  submitted document/epoch/cell versions and operation ID fixed across retries.
- Persist the request before sending, under `owned-cells:<page ID>` and
  `owned.cells.v1`. Linked instance journals remain separate and use their own
  authorization route.
- Remove a journal entry only after checking the response contract, operation,
  document, workspace, database, target, document sequence, and exact cell versions.
- After acknowledgement, retain the confirmed values if the follow-up read fails.
  Do not overwrite a newer cell version already received through collaboration.
  Permission-denied reads close the detail panel. A failed refresh must not
  resubmit an already acknowledged draft.
- Title and supported scalar property edits, undo/redo, pending-request recovery,
  composition guards, and close/navigation guards reuse the shared item controller.
- Previous/next and close focus follow rendered view order. Repeated calendar or
  timeline buttons refer to one row. An acknowledged edit requests the existing
  scoped activity refresh so the page's edit time comes from the server.

## Evidence and remaining parity

`test_notion_owned_database_items.js` exercises the actual application functions
with the real database bridge, journal controller, and item controller. Existing
linked-item/cell/refresh, navigation, side-peek, activity, and metadata tests cover
shared behavior. The backend atomic batch/replay/permission tests remain unchanged.

Actual Chrome checks used a synthetic database with two rows: table and board
open, title/status/date save, persisted reload, undo/redo, view order, Escape/focus,
and an after-commit lost response replay. The canonical receipt and cell versions
prove one committed change for the replayed request; unrelated rows and memberships
remain unchanged.

This resolves the owned-row document-route error and property editing. It does not
complete Notion row-page parity: item-specific rich body, comments, icon/cover,
full-page routing and viewing options still require their own storage and UI work.
No product schema, production migration, or rollout flag changes are part of this
change.

2026-09-28 date-picker follow-up: the live Notion date popup was measured at
248px wide; its 173px format menu opens immediately to the popup's left and
offers six choices. Its reminder control opens a corresponding five-choice
menu. The owned table and item picker now use separate anchored menus instead
of native selects, with checked choice, arrow-key navigation, Escape focus
return, outside-click dismissal, and viewport-aware placement. The all-day
reminder labels match the directly observed Korean menu. A browser journey
saved a format and reminder on an owned row, reloaded, and confirmed both
canonical values. The existing legacy peek test verified format selection and
390px placement; its legacy reminder path still uses its separate dialog.
The existing grid date redraw test also passed. Desktop and mobile screenshots
were inspected in the task report. This does not establish parity for all
roles, time-enabled reminders, or the unserved production 10322 instance.

2026-09-28 timed-date follow-up: the live Notion time-format control opens a
three-choice menu (`숨김`, `12시간`, `24시간`) to the left of the date popup. `숨김`
retains the timed value but omits the clock from the table cell. The timed
reminder menu calls its zero-minute choice `이벤트 정각`. The owned picker now
uses the same choice-menu interaction for time format, persists `hidden` as
part of the canonical date cell, and omits only the displayed clock. The
isolated service, date-model, and browser checks cover save/reload, retained
09:00 time, `minute_0` reminder, and the menu at desktop and 390px. The live
comparison row was returned to its original all-day date and four-row source
table after the read; no user content was left changed. The local 10322 host
remains stopped, so this is not an operating-app verification.

2026-09-28 inline time layout: direct Notion comparison with both end date and
time enabled showed two vertically stacked date rows, each with its time input
inside the right edge. The development picker previously put end dates side
by side and times below the calendar. It now uses the two inline rows and
retains focus on a changed time input after saving. In an isolated browser,
the end time changed from 09:00 to 10:30, saved canonically, survived reload,
and both rows stayed aligned at desktop and 390px. A focused follow-up caught
the grid's re-open path returning focus to the start date after an end-time
save; the re-opened picker now restores the end-time focus. The owned date
journey and legacy side-peek regression passed together (2 tests, 65.44s),
and the focus assertion passed after the correction (1 test, 47.54s). Desktop
and mobile screenshots were inspected. The comparison date was returned to
its original all-day value and the source table still had four named rows.
The local 10322 host remains unavailable for operating-app verification.
