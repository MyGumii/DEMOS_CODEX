# Linked databases: current source reads and copy identities

Status (2026-09-16): explicit source/local-view descriptors, current-authority GET,
copy remapping and a separate frontend render projection are implemented and
tested. Creation, local-view editing UI, source-authorized shared data writes,
live refresh and durable recovery are still required. The collaborative page-copy
capability remains disabled. This is not a completed linked-database UI feature.

## Direct Notion observations

Chrome computer-use was used with dedicated private QA pages on 2026-09-16.
No user document was used as a writable fixture.

1. Source page `3dd9e69c1dfb8022992bd0df6bb65d9a` owns database
   `3dd9e69c1dfb80bd8c43f191e7d356df`. A linked view was created through
   `/연결` → `데이터 소스 보기 연결 · 데이터베이스` on page
   `3dd9e69c1dfb808cbeb5eb6e7f1f0c58`.
2. A row created through the linked view appeared in the source. The linked
   view was filtered to A. Its page duplicate, `3dd9e69c1dfb80de869af0deaae155df`,
   initially retained A and still linked to the same source database.
3. Editing A through the duplicate changed A in both the original linked view
   and the source. Changing only the duplicate filter to B left the original
   linked view filtered to A; the source continued to show both rows.
4. That duplicate was moved under the source page. Duplicating the source page
   then produced parent `32a9e69c1dfb8357b7a2013d67d39223`, owned database
   `51e9e69c1dfb83c89b43811b445b0058`, and linked child
   `7d19e69c1dfb8285aa9a0197ba19444a`. The copied child's visible source link
   pointed to the new database. Editing B there changed B in the copied source
   only. The original source still displayed the unchanged B.

The duplicated words in the synthetic database's title came from the QA input
operation; they are not evidence about a product defect. These observations cover
external references and sources inside one copied subtree, not cross-workspace
linking or every relation/template shape.

## Stored model

A supported table/board/list/gallery/calendar/timeline block has empty `content`
and explicit props:

```json
{
  "linked_database": {"document_id": "source-page", "database_id": "source-db"},
  "views": [{"id": "local-view", "name": "Table", "type": "table", "config": {}}],
  "active_view_id": "local-view"
}
```

Layout metadata remains on the block. There must be 1–64 uniquely identified
local views and a matching active view type. Source row/property/template caches,
source title, rich content, children and flattened active-view caches are rejected.
An optional compatibility `database_id` must match the explicit source. Legacy
database ID/cache-only blocks are not inferred to be linked instances.

Local settings use the existing versioned block/CRDT storage. This packet adds
no schema migration and changes no live catalog or production flag.

## Current read boundary

`GET /api/notion/workspaces/<workspace>/pages/<page>/linked-databases/<block>`
calls `linked_databases.read_linked_database`. It materializes the instance's
durable Yjs body outside SQLite, then reauthorizes the active actor and both
documents in one read transaction. It checks the instance stamp and reads the
source database from that same final snapshot, including current relation and
computed-cell visibility. The source must be owned by the explicitly named
document in the same workspace. The combined preparation/response budget is
64 MiB. Source edits committed during instance replay are therefore reflected in
the response; an instance edit during replay produces a conflict.

The response contains separate `database`, local `views`, `active_view_id`,
instance/source identities and witnesses. It writes no content, grant, cache,
audit, receipt or legacy row. Responses are `no-store`, including errors.
Denied/missing objects return 404; instance races return 409; unsupported stored
shapes return 422 without private source details. Ordinary mode has no fallback.

## Copy and frontend boundaries

An external source reference remains unchanged. Its data is not fetched or copied,
and the new instance grants no access to it. When the source database is inside
the copied subtree, both its owner document and database identity are remapped.
Local view IDs are independently allocated per instance, and internal property
and relation-row references in view settings follow the copied schema. A source
document/database mismatch is rejected before commit. Copies of copies preserve
the same rules and current durable view settings.

`NotionDatabaseBridge.linkedToBlock` builds an ephemeral render model from an
instance-authorized response and requires an exact page/block/source match. It
combines source data with local views without modifying either input. This
projection must not be persisted as the linked block descriptor. Owned database
hydration/diff deliberately excludes explicit linked instances, preventing a
generic save from replacing source views or creating duplicate data. Dedicated
source-authorized writes remain required before full editing activation. The
creation and local-view UI described below persists only the reference descriptor.

The existing owned-view hydration also now clears flattened settings absent from
the newly active view, preventing a previous view's filter/sort from lingering.

## Validation

Python: 31 relevant linked read/copy/HTTP, remapping and synced-reference cases
passed across the completed runs. The first combined run had 30 passes and one
test-fixture failure (missing initial Yjs seed); that fixture was corrected and
the failed case passed with 10 existing copy-HTTP/database-feature regressions
in 87.07 seconds. The resulting union is 41 passing Python cases. Initial setup
also required creating the title property absent from the inherited fixture.
These were fixture defects, not ignored product failures.

JavaScript: 6 tests passed, covering shared source rows/local filters, input
immutability, exclusion from owned DB writes, response scope validation and stale
active-view filter removal. Real browser evidence above is from Notion; the new
application linked UI was not connected in that packet. The following successor
packet adds its own browser evidence without changing that earlier result.

## Creation and local-view UI successor (2026-09-16)

`GET /api/notion/workspaces/<workspace>/database-sources` lists only currently
accessible, active source documents in the exact workspace. Search is literal,
pagination cursors name visible databases only, and all responses are no-store.
This is a read-only discovery endpoint with no ordinary-mode fallback.

The collaborative editor exposes `/연결` → 데이터 소스 보기 연결. The picker
offers a new table view or a copy of an existing supported source view. Canceling
does not remove slash text. Source data is fetched for rendering only; the editor
stores the explicit source IDs, independent local views and layout metadata.
Normalization and ordinary dirty/save processing preserve this cache-free shape.

The linked toolbar supports local view selection, add, rename, duplicate, delete,
layout, filters, sorts, grouping and visible properties. Permission and current
page checks fence changes; a stale settings dialog cannot overwrite a changed
block. Current source data is reloaded on demand, focus/visibility and a 20-second
interval. Leaving the page aborts reads, and errors clear previously rendered
source data. This interval is unrelated to the meeting translation modes.

Isolated Chrome browser verification on localhost:14536 covers slash creation,
source selection, A-only and B-only local filters, view duplication, reload
persistence, independent original source settings and current source title
refresh, plus deleting only a duplicated local view. Source filters/sorts remain
empty and the original view name remains unchanged; the existing owned editor
does normalize additional empty default keys when saving a source cell, so this
is not a claim of byte-identical source view configuration. The source title
edit exposed an unnecessary legacy page PATCH; the
canonical single-cell path now omits it while the ordinary-page path is retained.
The prior test helper's missing linked-module dependency was also updated.

The isolated read-back verifies the durable Yjs projection against the linked
endpoint and confirms zero owned databases on the instance page. An existing
paragraph converted by slash keeps its base SQL block row until replay: the
editor's structural commands cover insert/delete/move, while type/props changes
are Yjs updates. A base `read_document().blocks` snapshot alone must therefore
not be interpreted as the current body. This packet does not add a second write
to that base snapshot. Final targeted JavaScript run: 18 passed. Discovery and
linked HTTP Python run: 6 passed in 38.83 seconds.

That creation packet left the grid read-only. The successor below adds table
cell writes and recovery. Row/schema writes, calendar/timeline navigation, full
six-layout interactions, role/theme/mobile coverage and operating rollout remain
incomplete. Page-copy activation remains disabled.

## Linked table cell writes and recovery successor (2026-09-16)

`POST /api/notion/workspaces/<workspace>/pages/<instance>/linked-databases/<block>/cells`
accepts exact `source`, `instance_witness`, `source_witness`, `cells` and
`operation_id` fields. Preparation replays the durable reference outside SQLite.
The existing atomic cell batch then rechecks current edit-content permission on
both documents, exact workspace/source ownership, prepared instance stamp,
caller epochs/generations, source sequence and every cell version in its writer
transaction. The instance identity and client witnesses join the idempotency
hash. A retry reauthorizes both documents before returning the prior result;
it does not write rows, audit or events twice. Instance body/views are untouched.

The linked table now supports native scalar inputs, rectangular TSV edits and
source-versioned undo/redo. Values are never assigned to the stored linked block.
Before sending, the request and prior values enter the existing AES-GCM browser
outbox under a separate `linked-cells:<instance>` page scope in the same
realm/subject. Account cleanup therefore covers these entries too. Recovery
resends the exact operation/body. Conflicts retain the pending input; the user
can retry or remove only that local pending request. Other-author edits are not
silently overwritten by undo. Changing back to the original value sends nothing.

Periodic identical reads do not rebuild the grid. An active input retains its
DOM while a refreshed source is still editable; denied reads clear displayed
rows. Hidden tabs pause reads without deleting the active input. Successful
saves restore grid focus only if the user has not moved to another control.

Chrome verification on synthetic localhost:14536 covers direct title edits,
source-page visibility, undo/redo, two-column paste and atomic undo/redo. The
synthetic server was stopped before a committed input attempt; its pending value
was visible, then restored from the encrypted browser journal after server
restart and page reload. Response-loss-after-commit is covered by the controller
and idempotent service tests, not claimed as that browser outage test. Final
read-back proves the instance body/sequence and source view settings unchanged,
row B unchanged, and one operation/event/audit/outbox record for each of nine
QA commands (six single-cell, three two-cell commands).

Validation: 15 Python cases passed in 155.50 seconds; final JavaScript set has
23 passes, including prior grid, collaboration editor and receipt regressions.
Two new JS test-helper defects (clone callback arguments and one missing brace)
were corrected before that final run. Live schema remains 2026082415 in the
named fixture; operating server 10322 was not restarted or migrated.

Remaining linked editing work includes relation choices, file operations, date
end/reminders, row/schema creation, editable card/item details in other layouts,
calendar/timeline navigation, conflict resolution beyond retained-input retry,
multi-tab simultaneous recovery, native IME/edit-navigation cases and complete
permission/theme/responsive verification. This packet does not complete the
full Notion parity goal or activate page-copy rollout.

## Linked item detail successor (2026-09-16)

Linked table row openers and board/list/gallery cards now open a right-hand
property panel. It edits the same source cells through the linked-cell endpoint
and encrypted outbox above. The panel does not create ordinary pages for row IDs
or invent item bodies. Its source action opens the authorized database document.
Current scalar properties are editable; unsupported relations/files/computed
fields remain read-only. Schema creation and full page-body editing are separate
remaining work.

The selected item is tracked by canonical row ID. Previous/next follows rendered
view order, including board groups, filters and sorting; duplicate timeline
openers count once. Edits retain the cell version present when typing began, so
a later source refresh cannot silently overwrite a concurrent author. Closing,
opening another item and source navigation wait for saves. Pending journal
requests keep retry/discard controls; successful recovery clears the matching
draft instead of issuing it again. Composition blocks navigation until committed.
Permission loss clears cached properties; a successful read of the same version
after a failed read restores the panel. The property panel and table return focus
only to the editing surface that initiated a save.

Browser comparison used the actual Notion source QA page and its A/B side peeks:
right-side title/property area, previous/next disabled boundaries, full-page
action and splitter. App verification used only the named localhost:14536
synthetic fixture: table/board/list/gallery open and save, source-page read-back,
undo, filtered boundaries, board group order, Ctrl+K navigation, keyboard resize,
and body width adjustment. A real server stop before request delivery retained
the entered title; after restart the panel's retry saved it once. This is not a
claim of a browser response-loss-after-commit or Windows native IME test.

The browser exposed two additional defects: the grid's capture listener consumed
row-opener clicks, and grid focus restoration stole focus from detail edits.
Both are corrected. A failed linked read now clears its comparison cache, so an
identical successful retry can replace the error view. Final JavaScript set:
34 passes. Read-only ledger verification: seven new single-cell commands, one
source event/audit/outbox each, 16 cumulative commands, source B including its
versions unchanged, original source views unchanged, no owned DB on the instance.
The temporary multi-layout QA view was removed: instance sequence is 16 and its
durable body/local A/B views equal the pre-test projection. Schema remains
2026082415. No backend schema or route change was needed in this successor.

Remaining: real calendar/timeline item interactions and date navigation, rich
relation/file/date-range widgets, item page bodies/comments, schema/row creation,
conflict rebase, simultaneous multi-tab recovery and comprehensive role/theme/
responsive/native-IME checks. Full Notion UI parity and operating rollout remain
in progress; page-copy activation is unchanged.


## Linked date-view successor (2026-09-16)

Calendar and timeline navigation now operate on the active instance view config.
Editors persist that config through the existing document editor; readers can
navigate and choose timeline scale within the current mounted view without a
write. Date-property selection requires instance editing permission. Source
schema, source view settings and row caches are never copied into the instance.

A shared date helper uses local calendar dates, rejects invalid dates and clamps
month-end moves (January 31 to February 28/29). Calendars render only the weeks
intersecting their month, show today, and support previous/next/today. Timeline
supports day, week, two weeks, month, quarter, year and five years. The current
bounded timeline is not yet Notion's continuous date track or its hour view.

Undated items remain reachable through a searchable menu. Opening an item does
not assign a date; clicking its assignment action writes local today into the
selected date property via the existing linked-cell journal/CAS endpoint. No new
backend mutation path was introduced. The menu closes on Escape/outside pointer
and detail open. Its auxiliary open buttons cannot reorder the main view's detail
navigation. The shared renderer also exposes undated rows for owned databases.

Actual Chrome comparison used the existing Notion QA source page's separate
`Codex 날짜 비교` view. Observed: calendar month navigation survives reload,
September/October 2026 use 35 cells, undated item click assigns today, date-property
selection exists, calendar default peek is centered and timeline default peek is
on the side. Timeline menu lists hour/day/week/two weeks/month/quarter/year/five
years. The observed persistence does not establish whether Notion stores month
navigation per user, browser or shared view. The QA source gained a date property
and item A was assigned 2026-09-16 during this controlled comparison.

App browser verification used only the newly named localhost:14537 synthetic
fixture. Calendar next month/reload/today, undated search/no-result and assignment,
start/due property switching, timeline next month/today/two-week/five-year (60
slots), item detail open without a date write and final date read-back passed.
An auxiliary-menu ordering defect found in the browser was fixed and retested:
item C stays 3/3 rather than moving to 1/3. Final JS suite: 44 passes in 591.4076 ms.
Read-only verification: instance sequence 13, schema 2026082415, zero owned DBs or
source caches on the instance, original source table view/version unchanged,
exactly two date cell changes, and one event/audit/outbox entry per operation.

Still remaining: hour precision, continuous timeline pan, drag-to-date/resizing,
calendar week/weekend controls, centered peek choice, date property creation,
date end/reminder/timezone widgets and complete role/theme/responsive/IME checks.
These limits and full Notion parity/operating rollout remain in progress.

## Scoped row creation successor (2026-09-30)

The staged, table-ID-only linked view can create a blank source row without
exposing its private parent page ID. The POST endpoint requires the current
instance witness and source access stamp. It checks both grants inside the
source transaction, including receipt replay, and never mutates the instance
page. The browser stores the exact operation and request before sending;
response loss retries the same operation and cannot create a second row.
The button appears only when the current instance and source are editable.
The creation cluster now offers the source database's default, an explicitly
blank item, and accessible named templates. An unavailable default disables
only the default action, leaving the blank choice available.

The focused source/linked HTTP regression suite passed 13 tests. Linked-view
controller, refresh, date and cell suites passed 24 tests; an isolated Chrome
DOM check passed for default, blank, named-template and permission behavior.
The scoped HTTP test also confirms that a named template's body is copied into
the source row and a blank item has no selected template. Full-app Chrome
verification on the staged 2429 catalog is still unavailable because the
selected collaboration gateway refuses that catalog's older transport schema.
The general runtime remains on its existing feature flags; no production
catalog was migrated or modified for this successor.

Read-only comparison against the signed-in Notion linked QA page showed a
filter, sort, automation, AI autofill, search, full-page link, settings, and
split create control in that order. This implementation now exposes filter and
sort shortcuts into the existing local-view panels, plus a local search control
that filters rows without a source or instance write. The isolated Chrome check
verified both panels, search focus/Escape and the filtered result at desktop
and 390px width. A reader's focused search is also preserved across a
background refresh. The broader linked-view regression set passes 46 tests;
one stale upload fixture was updated to provide the current scoped response
contract. Notion's split-create menu
still offers “new template” when the source has no templates. The linked
view now exposes that action for an actor with source database `edit` and
instance-page `edit_content` or higher. It creates a named blank template on
the source via the scoped route, even without source-parent-page access; the
response omits that private parent identity. The exact request is saved before
sending, and replay rechecks both grants. The first-stage dialog edits the
name. Existing accessible templates now expose an edit action for the same
scoped actor. A versioned PATCH can change the name, supported text blocks,
and scalar property defaults; both the original and submitted body/defaults
must be free of private-page references. File IDs are constrained to the
database-visible set and relation defaults require target access. The edit
request is journaled before sending and the exact operation is retried after
response loss. The linked editor now uses the scoped database file route for
media blocks and file defaults, including upload, and offers relation choices
from the target database's access-checked projection. External file links,
file removal, and uploaded defaults are editable. A successful background
retry closes its matching edit dialog so the same draft cannot be submitted
as a new operation. Unsupported block kinds and inaccessible relation targets
are preserved without presenting an unsafe control.
An isolated collaborative app at the supported transport marker, with only
the staged linked HTTP read/write responses simulated, passed the full menu →
dialog → Enter → saved template menu flow and the existing-template edit
dialog's name, paragraph body, and title default flow. This verifies client integration
without exercising the staged server.
An additional isolated full-app test now uses the exact synthetic 2429 catalog,
the real linked read/create/update HTTP routes, and a test-process-only gateway
marker adapter. An actor who can edit the linked page and source database but
cannot read the source parent page creates and edits a template's name,
paragraph body, and title default. The saved version and values are read back
from the server, and the scoped HTTP response omits the private parent ID.
The same test now edits a same-database relation default, adds an external and
an uploaded file default, downloads that file through the scoped route, then
inserts an image URL and replaces it with an uploaded image in the template
body. Each step is read back from the real staged service. The separate
response-loss browser fixture still checks one exact retry after a page redraw.
An open template picker is retained through a forced read when source identity
and edit permission remain valid, and restored after the containing page is
redrawn. A full-app browser test explicitly redraws the page with the menu
open before creating and editing a template. Revoked edit permission repaints
the view instead of retaining the menu. The linked-view Node suite passes 65
tests after this change.
Automation and AI autofill also remain unimplemented here. The Notion page was
returned to its original source view without creating a template or changing
content.

The linked-view toolbar now opens the instance in the existing database
full-page presentation and returns to the inline position. Its breadcrumb
initially identifies a linked database, then shows the accessible source title
after the scoped read. Closing the view restores focus to its inline opener,
including when a personal-page linked view reloads after the transition.
That personal-page flow passed an isolated full-app browser regression for the
breadcrumb and restored focus.
Further read-only inspection of the signed-in Notion linked QA view confirmed
that its full-page control is a link to a separate view URL, with a breadcrumb
back to the containing page. This app now has a shareable
`/page/<instance>?linked_db=<block>` route. Clicking opens a browser history
entry, reload restores the full-page view, and Back returns to the inline
page. The opener is a real link so standard new-tab navigation also works.
The app route remains instance-scoped rather than using Notion's separate view
identity; the source database's private parent page is not disclosed. An
isolated Chrome DOM check passes the open/close control and verifies no
horizontal page overflow at 390px; a VM regression covers the route and focus
restoration. A personal-page full-app browser check covers click, reload,
Back, close, breadcrumb, and focus.
The full-page source title now uses a 26px heading treatment rather than the
inline 17–18px label; the isolated personal-page browser check reads the
computed size and confirms it is at least 24px.
The linked-view suite now passes 58 Node tests plus one isolated Chrome test.
The general user-app rollout and broader linked-view role/theme coverage remain
outstanding. No production catalog marker or feature flag was changed.

On 2026-09-30, read-only inspection of the signed-in Notion source table and
its linked view showed the same empty-template picker in both places: a
database title, a help link, a short explanation, one divider, and only
“새 템플릿”. The local source and linked pickers now use that empty state;
the “비어 있음” choice remains available when a named or default template
exists, so users can explicitly bypass it. The main “새로 만들기” button
continues to create a blank item when no template is selected.

Linked template updates now validate relation defaults against the current
target database and row grants inside the write transaction. An unpaired
cross-database relation requires target view access; a paired relation that
updates reciprocal cells requires target content-edit access. Missing or
archived target rows are rejected when saving a default, rather than leaving
a template that fails when a row is created. The isolated HTTP regression
covers denied and granted updates, a missing target row, successful linked
rows created from unpaired and paired templates (including the reciprocal
cell), paired edit requirements, grant revocation with no partial row creation,
and omission of the private source parent page ID. The focused
regression passed on 2026-09-30. An initial full-app browser rerun did not
reach the application because an isolated catalog migration rejected a
changed staged image. A later run through the required managed test root
completed the exact 2429 scoped-server browser flow (1 pass, 79.29 seconds).
The browser case was then extended with a second private target database.
With only target view access, its relation row was selected and saved as a
template default. Revoking that grant and reloading made the template choice
disabled, removed its edit control and redacted the target identity and
default in the scoped read (1 pass, 58.13 seconds). This remains an isolated
2429 app run, not a production rollout or general user-app validation.

Collaborative linked table relation cells now expose the same compact picker
treatment used by personal linked tables. A candidate list comes from the
target database's current scoped read, never from the private source parent
page. Missing target access keeps the control disabled; reciprocal relations
also require target content-edit access before the picker opens. Selection
commits through the existing versioned linked-cell write, whose transaction
rechecks target access and row identity. The picker closes on redraw and scope
teardown, and Escape restores focus. In an isolated 2429 full-app browser,
an editor with only target view access selected a cross-database row and the
source cell was read back with that row ID. Escape closed the picker, returned
focus to the cell and made no write. After the target grant was revoked,
the reloaded control was disabled with an access message. The final browser
case passed in 60.27 seconds; the 65 linked-view Node regressions and JS
syntax checks passed. This does not cover every relation role, theme or
concurrent-editing combination, nor the general user app at port 10322.
The same isolated browser case now also revokes target access while a picker
is open, applies a cleared draft and observes a denied write with the original
source cell intact. The failed request remains visibly recoverable; explicitly
discarding it clears the pending notice, and a reload shows the disabled
target control. The relation picker now also forces a scoped read after a
denied write, so the target control becomes disabled immediately without a
page reload. This extended browser case passed in 60.32 seconds, with 14
focused linked-cell/refresh Node regressions and JS syntax checks passing.
It does not establish push-driven revocation before the user attempts a write
or a general-user rollout.
The isolated full-app browser case now also enables a reciprocal relation
after an initial one-way cell write. A target view-only grant leaves its
source-cell picker disabled; promoting that grant to content-edit enables the
picker. Clearing and restoring the source relation updates the target's
inverse cell in the same canonical service path. The prior open-picker
revocation and discarded-pending checks still pass in this longer case
(1 browser pass, 68.06 seconds). This is not evidence for every target-page,
role or concurrent-editor combination.

Collaborative linked table file cells now use the compact file menu already
used by personal linked tables. Editors can add external links or upload to
the source database, then remove or reorder file chips (drag or Alt+arrow).
The upload path refreshes the scoped projection before it appends the new file
ID through a versioned source-cell write; later edits use the same stable row
ID and cell version. Consecutive file actions queue behind an in-flight action
so a second input is not silently dropped during redraw. The isolated 2429
full-app browser case reads back the external link, uploaded PNG bytes, changed
order, and removal from the canonical source row (1 pass, 73.13 seconds on
2026-10-01). The 65 linked-view Node tests and JS/Python syntax checks pass.
This is not evidence of a general user-app rollout or every role/theme case.
The same isolated browser flow additionally reordered the two file chips by
mouse drag and read the changed canonical order back (1 pass, 79.61 seconds).
The drag-start pending check now addresses the linked block ID, as do the
relation picker and file-upload refresh checks. The signed-in Notion comparison
database had title, relation, and date properties but no file property, so its
file-cell menu was not directly compared; its original calendar view was
restored after this read-only inspection.
The isolated browser flow also downgrades the source database grant to view:
the uploaded file remains visible, the add-file menu is absent, and the
canonical source row keeps the same file ID (1 pass, 85.46 seconds). The
comparison used a temporary synthetic catalog and did not change a live grant.

The previously personal-page-only full-page route now has a collaborative
2429 full-app check. An editor without access to the source parent opens the
linked instance at `/page/<instance>?linked_db=<block>`, sees the accessible
source title in the breadcrumb, reloads the full-page view, uses browser Back
to return inline, and closes the view with focus restored to its opener. The
same run continues through relation, template, file, and grant tests (1 pass,
88.33 seconds on 2026-10-01). During this longer flow, an identical forced
read could redraw and close an open relation picker. Forced reads now retain
open relation and file menus when the scoped response is unchanged; changed
source data or access still redraws them. The focused refresh test and all
66 linked-view Node tests pass. The general user app remains unverified.

On 2026-10-01 the signed-in Notion comparison relation picker was inspected
without editing its source database, then its original calendar view was
restored. The actual picker showed a search row scoped to the target database,
a selected-count row, selected page rows with a separate minus affordance, and
no Apply footer. The no-Apply immediate-write behavior is an inference from
that UI, not a measured network or persistence trace on the real Notion DB.
Collaborative and personal linked relation pickers now use the same 560 px
search/count/selected-first layout and commit each add or removal without an
Apply step. The collaborative 2429 browser case passed once with direct
selection, reciprocal clearing/restoration, and denied-write recovery after
target access was revoked (1 pass, 102.07 seconds). Personal linked single-
and multi-relation browser cases passed (2 passes, 67.42 seconds), including
IME search, a same-data forced refresh with the picker open, responsive width,
and source/target revocation. All 66 linked-view Node tests and JS/Python
syntax checks passed. This does not establish all visual details, every role
or concurrent-editing combination, or rollout in the general user app.
The selected page label and its minus control are now separate elements in
both pickers, matching the visible Notion affordance. Clicking a selected
page label leaves the picker and source relation intact; its minus button
removes the link with the same versioned write. The exact 2429 full-app
browser flow passed (1 pass, 85.90 seconds), both personal relation browser
flows passed (2 passes, 67.45 seconds), and all 66 linked-view Node tests
passed. The general user app is still unavailable at port 10322.
Read-only inspection of the real Notion picker overflow on 2026-10-01
confirmed that it is a display-property menu, not a generic clear action.
It listed the target database's Name under visible properties and two Date
properties under hidden properties, each with an eye state and reorder handle.
Our linked relation pickers currently lack this target-property display
configuration and preview. Do not treat the visual/count/immediate-write
changes above as full picker parity. The comparison page was returned to its
original calendar view without changing relation data or property settings.
The display-property overflow is now connected to both collaborative and
personal linked relation pickers. It lists target properties in visible and
hidden groups, supports eye toggles and drag or Alt+arrow ordering, and shows
visible target-cell values below matching page names. Preferences contain
only property IDs and are stored locally per user, workspace, source database,
relation property, and target database; source relation cells are unchanged.
The isolated 2429 full-app browser check toggled a target date, verified its
preview and persistence after reload, then continued through permission
revocation (1 pass, 81.98 seconds). The personal browser check exercised
date preview persistence, a second property, Alt+arrow reordering, hiding,
IME search, and source/target revocation (1 pass, 45.11 seconds), with the
second personal no-target/revocation case passing earlier (68.09 seconds for
both). Visual inspection of the synthetic 1440 px screenshot found the menu
and relation popup within the viewport, with the property preview readable.
All 66 linked-view Node tests pass. This is a local per-user display
preference, not a server-synchronized Notion property setting; multi-device
sync and every property type remain unverified. The general user app at
10322 is still unavailable for live final comparison.
After the menu-position adjustment, the same isolated 2429 collaborative
browser flow passed again (1 pass, 85.76 seconds) and both personal browser
relation flows passed (2 passes, 67.16 seconds). JavaScript and Python syntax
checks also pass. Port 10322 still refused connections at this check.

The relation display-property submenu now moves focus to its first reorder
handle when opened, closes by itself when the user clicks elsewhere inside the
relation picker, and returns focus to its toggle on Escape. This leaves the
relation picker and its search text in place. The personal linked-view browser
case passed with these keyboard and outside-click paths (1 pass, 44.96
seconds); the collaborative full-app browser regression also passed (1 pass,
45.61 seconds), as did JavaScript syntax validation. Port 10322 remains
unavailable, so this
change has not been verified in the general user app.

Relation search results now use a target item's saved icon where the authorized
target response provides one. The collaborative picker reads each canonical
row's scoped appearance; the personal picker resolves the linked page icon
from its accessible page summary, falling back to the row value when present.
The generic page glyph remains for targets without a saved icon. An isolated
personal browser case showed an emoji icon from the linked target page and
completed the relation save/revoke flow (1 pass, 44.87 seconds). The exact
2429 collaborative browser case saved an icon on a target row, read it through
the view-only scoped response, rendered it in the picker, and completed the
existing relation/template/file/grant flow (1 pass, 83.64 seconds). All 66
linked-view Node checks and both JavaScript syntax checks passed. This does
not prove every image/vector icon, every role/theme, or the logged-in user app.
