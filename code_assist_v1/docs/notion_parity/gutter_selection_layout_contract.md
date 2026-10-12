# Gutter selection layout (2026-09-23)

The main page and database item body share `bindGutterSelection`. A drag may
start outside the writing column; the existing blue fill has no border. Page
titles remain outside the selected body rows. Body headings, visible nested
blocks, tables and loaded image blocks remain eligible.

While a gesture is active, a ResizeObserver watches the canvas, surface and
current body contents. A late media load or row resize schedules a single
coalesced hit-test at the stationary pointer. Replaced rows are unobserved.
Completion, cancellation, navigation and disposal disconnect the observer;
callbacks belonging to a previous gesture cannot revive or alter a newer one.
Environments without ResizeObserver retain the existing pointer/scroll refresh.

The scroll edge is the visible intersection of the scroll container, selection
surface, viewport and toolbar. A partially offscreen scroll container therefore
scrolls at its visible edge. The edge zone remains at most 48px and cannot
overlap itself in a very short viewport. Mouse release evaluates final geometry
without another scroll step. Selection appearance and stored content are unchanged.

Four added regressions failed before the fix; the final five existing client test scripts
passed 19 test-runner checks. Actual Chrome on the named disposable fixture
selected ten mixed blocks including a decoded image, excluded the title and
collapsed child, selected eighteen lower paragraphs in reverse, and scrolled
1729px to 1721px with the pointer at y=70 below the toolbar. Escape cleared the
released selection. The 52-block body, document sequence and eight catalog
tables were unchanged.

Actual Notion was revisited with computer use: broad outside-column selection
excluded the page title and selected text, callout and column content. A brief
intermediate borderless rectangle appeared in the tool capture. This is not
proof of a prolonged held gesture. Stationary late-media layout and long-held
edge scrolling are automated evidence, not completed native mouse validation.
On 2026-09-26, another signed-in direct drag began in the left gutter and
ended with the rectangle overlapping only the leading edge of one body block.
Notion highlighted the full block even though the block was not entirely
inside the rectangle; Escape cleared it. This supports intersection-based
block selection rather than strict full containment. The comparison document
was not edited.
All roles, themes, mobile combinations and the original full R1–R11 scope remain
open. This packet does not authorize or perform a production flag/migration rollout.

2026-09-26 direct database-table comparison: On the signed-in Notion page
`Codex UI 비교 — 연결된 DB 원본 2026-09-16` at 1280×720, a drag from the page's
left body gutter (x≈324, y≈304) into the embedded table (x≈913, y≈452)
selected both database **rows**. The table showed `2개 선택됨`, checked the
header and both row checkboxes, and shaded each entire row. After clearing
the selection with the header checkbox, a narrower gutter drag ending at
x≈446 (overlapping only the left edge of the first table column) again
selected both entire rows. No page title was selected. The comparison was
read-only and the original Notion user tab was left untouched. The local
gutter selector currently hit-tests the containing document block as one
row; its grid selector starts only on a table cell and selects cell ranges.
Therefore this gutter-to-database row selection remains a verified parity
gap, not an implemented or regression-tested behavior.

Implementation update (2026-09-26): The local gutter hit-test now refines a
database table block into the data rows crossed by the rectangle. Those rows
retain the existing blue selection fill across every visible property cell;
the database block itself is excluded when data rows are hit. A row count,
header and per-row checkboxes, and selection clearing are provided without
moving the table during the gesture. Starting inside the table still uses the
existing cell-range selector. The isolated browser fixture selected both
database rows from a left-gutter drag whose right edge reached only the first
column, left the page-block selection empty, retained the row selection after
release, cleared it via the header checkbox, and then selected a 2×2 cell
range from an in-grid drag. A cancelled second drag restored the original
two-row selection, and Shift-drag added the second row to a one-row selection.
The full gutter suite passed 4 cases; the grid binding, range, interaction,
and UI runtime Node checks passed. This validates the
observed embedded table selection path. Database row batch actions, other
views, and the full R1–R11 parity gate remain open.

2026-09-26 selected-row toolbar comparison: In the same signed-in Notion
fixture, selecting both database rows exposed a compact floating bar. Clicking
its `2개 선택됨` count cleared selection. Its direct trash icon and overflow menu
were separate controls. The overflow menu contained icon edit, property edit,
copy all links, duplicate, move, and move to Trash. The local toolbar now
clears selection through the count button and offers `모든 링크 복사` in overflow
when every selected row has a stable page/item URL. Canonical owned rows use
the existing full-item route; legacy page-backed rows use their page URL.
The isolated browser copied two links in visible row order and withheld the
command when one row lacked a link. The full gutter browser suite passed five
cases; four adjacent Node checks passed. The local service's existing
`database.row.delete` physically deletes a row and retires comments, so it
cannot be wired to Notion's recoverable Trash control. Row Trash, duplicate,
move, icon and property batch edit still need their own safe service/UI work.
The real Notion fixture content was not edited.

2026-09-26 selected-row Trash follow-up: The selected-row control was already
wired to the gated canonical row Trash service. An isolated 2423 browser journey
now checks that a two-row selection archives both rows and restores both in
their original order and identities; the single-row lost-response receipt path
also passed (two browser cases). During a selected-row Trash request, the
toolbar now marks itself busy and disables its direct Trash and overflow
buttons. A focused browser case held the first request unresolved, attempted
a second click, and observed only one action; both controls returned after the
request settled. The adjacent two-row command probe and Node grid checks
passed. This prevents duplicate submissions from one toolbar while preserving
the existing per-row receipt recovery. The bulk operation remains a sequence
of individually committed rows, so an uncertain result stops the remaining
requests rather than pretending the batch was atomic. A transactional bulk
contract and the remaining Notion batch actions remain open.

2026-09-26 selected-row atomic Trash follow-up: The former multi-row loop is
replaced with one gated 2423 batch operation for 2–100 canonical rows. The
service checks all ownership, row versions, document sequence, and security
epochs before committing one transaction, one document-sequence increment,
and one replayable receipt. A forced failure on the second row rolled back
the first row and its receipt; retrying the same operation archived both.
The browser stores the exact batch request before sending it and can look up
or retry that request after an uncertain response. A different selection is
blocked until an unconfirmed request is resolved. The isolated browser
selected two rows and sent one batch POST; the existing one-row response-loss
and restore path also passed (two browser cases). Service and HTTP batch
cases, 17 adjacent Node recovery cases, and JavaScript syntax validation
passed. Batch restoration and Notion's remaining selected-row commands
(duplicate, move, icon/property editing) are still separate work. The
capability remains off by default; no live user data or catalog was changed.

2026-09-26 batch recovery and restore correction: The batch lifecycle-result
route now accepts and validates the direction flag stored by the browser; the
previous mismatch would have rejected a lost-response lookup. The same
2423 authority now restores all selected archived rows in one transaction,
with an independent restore operation/receipt and a matching batch `복원`
button in the status banner. A failure on the second row rolled back the
whole restore. The isolated browser passed normal two-row archive/restore,
single-row response-loss recovery, and two-row archive response-loss lookup
(three cases, 103.52 seconds). Focused service and HTTP cases passed, as did
20 adjacent Node recovery cases, including persisted 100-row requests.
The previous paragraph's missing batch restoration is resolved. Other
selected-row commands and full R1–R11 parity remain open; the capability is
still off by default and no live data was changed.

Evidence correction (2026-09-23): the prior command named a nonexistent `test_notion_database_item_editor.js`; Node ignored that path. The saved output proves five executed scripts and 19 checks, not six scripts or an item-editor test. New packet commands validate every input path before invoking Node.

2026-09-26 live selected-property comparison and restore-loss check: On the
signed-in Notion comparison table, selecting two existing rows showed the
floating count/Trash/overflow controls. Overflow showed `페이지`, then
`아이콘 편집`, `속성 편집`, `모든 링크 복사`, `복제`, `옮기기`, and
`휴지통으로 이동`. `속성 편집` opened a submenu with the current `이름`
and `날짜` properties. Choosing `날짜` opened the date picker; Escape closed
it and retained the two-row selection. The selection was cleared and the
original comparison page restored without changing any Notion row content.
The existing local `database.cells.set` authority can atomically edit up to
1,000 typed cells and the owned-cell browser controller journals the exact
request before sending it. These are reusable foundations for selected-row
property editing; the submenu and batch input UI are still absent. A new
isolated 2423 browser case deliberately discarded the successful two-row
restore response, then used the status banner's result lookup to confirm the
restore receipt without another mutation (one case passed, 39.80 seconds).

2026-09-26 selected-row property edit implementation: The owned canonical
table's overflow menu now opens `속성 편집` and lists current editable
properties. A date opens the existing calendar; other supported types open
an anchored value editor. Escape closes either layer while preserving the
row selection. Before saving, the client reloads the canonical database and
checks that the selected row IDs, property type, role, and cell versions still
match. It sends one `database.cells.set` request for all selected rows via
the durable owned-cell journal and retries an older pending request before
issuing a new one. An isolated browser exercised two-row date and number
changes through the real 2423 HTTP/store path; each used one POST and saved
both values. The date picker and property submenu Escape paths passed. Two
browser cases, five linked-cell journal Node cases, grid Node checks, and
the lost-response batch restore browser regression passed. The production
capability remains disabled. Selected-row duplicate, move, and icon edit,
other views, and the full R1–R11 parity gate remain open.

2026-09-26 selected-row icon comparison and implementation: In the signed-in
Notion fixture, `아이콘 편집` on two selected rows opened a popover attached to
the selection bar, with `이모지` and `아이콘` tabs, search, colors, and a dense
icon grid. Escape closed it without clearing the two-row selection. The
original page and selection were restored; no comparison row was changed.
The local menu now includes `페이지` and `아이콘 편집`, and the shared icon picker
is shown beside the selected-row bar. It flips above the bar when there is
insufficient space below. A new `database.rows.icon.set` command validates
1–100 active rows and their versions before applying one icon and recording
one document sequence, audit/outbox event, and replay receipt in one
transaction. The endpoint and menu use a distinct
`collaborative_database_row_icon_batch` capability that stays false by
default; the DB also requires the exact 2423 schema. The browser journals
the exact request before sending and
replays it after an uncertain response. A forced rollback, stale second row,
same-operation replay, the disabled HTTP gate, and the pre-2423 rejection
passed three service cases. The
isolated browser saved two icons with one command and separately dropped the
first successful response, then recovered by retrying the same operation ID
without a second mutation (two cases). The earlier selected-row property
browser cases and two existing item-appearance service cases also passed.
Selected-row duplicate and move, other views, and the full R1–R11 parity
gate remain open. The production capability and user data were not changed.
The local shared picker still has an explicit upload tab and a ten-column
grid, whereas the observed Notion popover showed two top tabs and roughly
twelve emoji columns with custom items in its category strip; finer visual
and custom-image parity remain to be checked.

2026-09-26 selected-row move and duplicate follow-up: On the signed-in Notion
comparison table, selecting both existing rows and opening `… → 옮기기` showed
an anchored searchable destination tree headed `페이지 옮길 곳 선택`. Suggestions
included ordinary pages and the source database; expanding a separate
destination page revealed its child database. Escape closed the picker and
cleared the two-row selection. The original comparison page was restored and
no row was moved or edited. This is direct evidence that a destination picker
must support both pages and databases; the local selected-row move command
and picker remain open work.

The selected-row menu now exposes `복제` for 1–100 editable canonical rows when
the new `collaborative_database_row_duplicate_batch` capability is enabled.
The exact 2423 service checks current access, active source rows, versions,
document sequence and access epochs, then copies cells, item bodies, and
appearance into new row IDs immediately after each source. Nested block IDs,
tab/view IDs, layout groups, and relative links to copied blocks are remapped;
files remain attached to the same owning document. The transaction records
one document sequence, database activity, audit/outbox event, and operation
receipt. The browser journals the exact request before sending so a lost
response replays the same operation rather than creating more rows. Three
service/HTTP cases and two isolated browser cases passed; the latter include
one forced lost response and same-ID recovery. Two Node controller cases,
existing icon controller checks, grid checks, and Python/JS syntax checks also
passed. A subsequent same-item synced-block reference refinement passed its
focused service case. The production capability remains false. Full move parity, copying
unusual nested body constructs, other views, and the R1–R11 gate remain open.

2026-09-26 direct selected-row move evidence: A newly created comparison item
`UI-PARITY-ROW-MOVE-20260926` had a title, a date, an emoji icon and a unique
body paragraph. Its single-row `… → 옮기기` picker offered ordinary pages and
databases nested below pages. Choosing `UI-PARITY-20260925-DB-LIFECYCLE`
removed the item from the source table, and the resulting Notion page kept the
same page ID/URL. Search still found its emoji and body paragraph at the new
database path. That destination database had already been moved to Trash; the
item therefore appeared as trashed after the move. A restore dialog warned
that restoration would place the item in personal pages because its parent
was deleted, changing its prior permissions. Restoration was cancelled. The
new comparison item remains under that trashed test database; the two original
comparison rows were not moved or edited. This observation proves identity,
icon and body retention for this route, but does not prove date/property
mapping, attachment ownership, comment behavior, or an active-destination
move. The local destination contract must inspect the current active/archived
state; selecting a trashed database cannot silently appear to succeed as an
active move. The full selected-row move service, picker, and regression gate
remain open.

2026-09-26 same-document move foundation: `database.rows.move` now has an exact
2423 canonical transaction path for 1–100 selected active rows between two
owned databases in the same document. It checks both edit permissions, active
database state, document/row versions and access epochs, and matching property
schemas. It preserves row IDs, mapped cells, body and appearance, retargets
row-comment anchors, and reindexes both databases with one document sequence,
audit/outbox event and replay receipt. Inbound relations and date reminders
currently cause a shape rejection rather than silently losing data. A
cross-document destination also returns a shape rejection while its file
ownership and page transfer contract is unfinished. The distinct capability
`collaborative_database_row_move_batch` is false by default, and no selected-row
move picker is connected to this command yet. Four isolated service/HTTP cases
passed, including rollback after domain writes, stale row rejection, request
replay, schema mismatch and inbound-relation refusal. Following a previously
copied item URL after a same-document move now resolves the same row ID in its
current owned database and rewrites the route through the normal full-item
open flow; a navigation regression case and JS syntax check passed. The URL
fallback is limited to databases on the loaded document. The comparison
Notion rows, production capability and user data were not changed.

2026-09-26 selected-row same-document move UI: The selected canonical-row
`…` menu now has `옮기기` when the independently gated move capability is
available. Its anchored destination dialog searches owned databases on the
current document, keeps row selection when Escape closes it, and disables a
destination whose properties cannot retain all source cells. Choosing a
compatible database first refreshes both canonical surfaces, then journals
one exact `database.rows.move` request and confirms the single receipt before
refreshing both views. A lost successful HTTP response can be recovered by
selecting the destination again; the stored operation ID is reused. The
isolated browser committed two rows with their IDs and mapped cells in both
normal and lost-response cases (two cases); the Escape/selection behavior
also passed. The existing duplicate/property-edit browser regressions (four
cases), move service/HTTP cases (four), relevant Node cases, and JS/Python
syntax checks passed. The backend capability stays false in production and
no real user data was edited. This is still a same-document destination
subset: ordinary pages and other documents are not yet selectable, and a
stored uncertain request has no reload-time recovery control. The full
Notion destination tree, cross-document files/comments/permissions, other
views, and R1–R11 parity remain open.

2026-09-26 selected-row move recovery follow-up: The previous paragraph's
reload-time recovery gap is now addressed by a page-level result banner. It
reads encrypted pending `owned.row.move.v1` requests for the current realm,
subject, workspace and document, validates their exact bodies, and offers
`결과 확인` after page editing becomes available. That action resends each exact
stored operation ID; it neither reconstructs row selection nor creates a new
move. A new controller instance recovered a lost-response journal entry in
Node, and the isolated browser showed the banner after a committed response
was replaced with HTTP 503, then confirmed the receipt without a second
mutation. The normal move and Escape cases still passed (browser two cases,
Node seven cases, JS syntax checks). The lost-response browser case now also
reloads the app, restores the isolated canonical fixture, and confirms that
the stored request still surfaces in the banner and replays once under the
new JavaScript runtime. On capability or access loss the request remains
stored and the banner reports why it cannot be confirmed. Cross-document DB
row transfer is still blocked: its current storage contracts bind files to a
document, comments to a document and DB anchor, and document sequences to
both pages. Ordinary-page destinations additionally require a row-to-page
identity and body contract. These paths must be implemented and verified
before the full Notion picker can expose them.

2026-09-26 cross-document database destination service follow-up: The exact
2423 selected-row move transaction now accepts two owned databases in
different documents of the same workspace. It validates edit access and
sequence/epoch fences on both documents, preserves selected row IDs, maps
compatible property cells, moves row comments to the destination document
and database anchor, and commits each document sequence/activity and a
destination live event/outbox alongside the source receipt. Selected-row
attachment references in cells, body, appearance and comments receive new
destination document file identities with provenance, while source file
metadata remains active so shared references cannot be broken. Child pages,
inbound relations and date reminders still reject a transfer whose
integrity is not defined. Six isolated service/HTTP cases passed, including
cross-document rollback, idempotent replay, comment retargeting, dual-page
events and attachment bytes/provenance; the production capability remains
false. The current browser controller and picker still accept only databases
in the open document, and old item URLs cannot yet locate a row moved to
another document. The destination tree, cross-document UI recovery and
ordinary-page destinations therefore remain unfinished.

2026-09-26 cross-document selected-row picker follow-up: The preceding
same-document browser limitation is now superseded for canonical database
destinations. The `옮기기` picker lists editable pages in the current workspace
as a nested page list, loads a page's databases when expanded, and disables a
database whose property schema cannot preserve the selected rows. It obtains
fresh source and destination document witnesses before committing one
`database.rows.move` request. The encrypted journal stores both document IDs
and independent destination fences, so a lost response is confirmed with the
same operation ID after a full browser reload. The current page refreshes
its source database and the destination page receives its own live event and
activity refresh. Four isolated browser combinations passed (same/cross
document, normal/lost response), as did four controller Node cases and JavaScript
syntax checks. The browser destination list is lazy: a database title in an
unexpanded page is not globally searchable until that page has been loaded.
Ordinary-page destinations, moved-item URLs that still point at an old page,
and the broader R1–R11 visual/interaction parity remain open. The production
capability remains disabled and no real user data was changed.

2026-09-26 moved-item URL follow-up: A read-only 2423 row-location endpoint
now resolves an active row ID to its current document/database only after
checking the requesting user's current access to the destination document.
Wrong workspaces, absent rows, archived rows/databases, and denied users do
not disclose a location. When an old full-item URL no longer finds the row
on its source page, the browser resolves that ID, replaces the URL with the
new page/database while preserving other query parameters, and opens the
same row in full mode. One isolated service/HTTP case passed for before/after
location, wrong workspace and denied user. A real HTTP browser case moved
two rows across documents, then followed the old source URL and observed
the destination URL plus full-item view for the original row ID. Existing
same-page item navigation Node cases and JavaScript/Python syntax checks
passed. This lookup handles canonical database destinations; ordinary-page
destinations and the full R1–R11 parity remain open.

2026-09-26 direct ordinary-page destination comparison: In the signed-in
Notion comparison database, a newly created row was moved to an ordinary page.
It disappeared from the source table and appeared as a child page of the
chosen destination. A second test row was given a date (2026-09-15), emoji
icon, and unique body paragraph before the same move. Its resulting child-page
URL retained the original row page ID
`3e79e69c1dfb8062b4b3ec99aa43ea8b`; the icon, title and paragraph were
visible. The date property was absent from the ordinary-page interface. Moving
that same page back into its source database restored the date property with
the original 2026-09-15 value, while retaining the same page ID, icon and
body. Thus the ordinary-page move must preserve database property data even
when those properties are hidden in the page view. The rich comparison row is
back in the source database; the first, title-only comparison page remains
under the ordinary destination. The local implementation still has only
database-to-database row movement. Its future ordinary-page path needs a
reversible row/page identity and hidden-property contract; simply discarding
cells or creating an unrelated page would contradict this direct observation.

2026-09-27 direct different-database property comparison: In the signed-in
Notion test area, the dedicated title-only
`UI-PARITY-ROW-TO-PAGE-20260926` test page itself was converted into an empty
full-page database (same page ID `3e79e69c1dfb80aab151e98faee54afd`) and
renamed `UI-PARITY-DB-TARGET-20260927`. It was not created as a child database
under that page. Moving the rich test row (same page ID
`3e79e69c1dfb8062b4b3ec99aa43ea8b`) from the source database into this
destination added a `날짜` date column that did not previously exist and retained
the value 2026-09-15, emoji and body. The row then returned to the source.
For a second probe, the empty destination's `날짜` column was changed to text.
Moving the row into it again left that text column empty and created a
separate date column (displayed as `날짜 1` after name disambiguation) with the
original date value. The row was returned to its original database and still
showed 2026-09-15. This directly contradicts the local picker/server rule
that rejected every missing or mismatched destination property. The new local
implementation creates safe missing property types and suffixes a name when
the existing same-name property has the wrong type; relation, formula, rollup
and system-computed property creation still require separate fidelity work.
An unmatched one-way relation value can now remain hidden on the moved row
and reappear under its original property after a return; inbound and two-way
relation fidelity is still open. The
test destination database remains as the converted full-page database under
the Notion comparison page for further verification; it has no rows and retains
the two probe columns. The visible database `작업` menu did not offer a direct
page-conversion action, so the original title-only page was not restored.
One more move of the same returned row to that destination reused `날짜 1`
instead of adding a third date column. The row was returned again to the
original database with its date intact. Repeated cross-database property
identity matching must not rely only on a new operation-specific property ID.
The original database table still showed only its original name and date
columns after the return, so the empty destination text `날짜` property did
not get copied back merely because it existed in that database.

The local 2423 move service now maps only properties with cells on the
selected rows. It first uses an unambiguous exact name/type/config match;
if absent, it can reuse one compatible numeric-suffixed name such as `Date 1`
for `Date`. Otherwise it atomically creates a new property for supported
stored-value types, using a unique name and preserving its type/config.
Creating properties requires database schema edit permission and respects
Notion's [500-property limit](https://www.notion.com/help/database-properties)
and [database-settings edit permission](https://www.notion.com/help/customize-your-database).
Ambiguous suffix matches and computed/relation property creation remain
closed rather than risking a value change. Unmatched stored relation values
stay attached to their source schema instead of being dropped. The
picker applies the same eligibility rules. An isolated service test covers
wrong-type collision, first move, return, and second move without new columns;
normal and lost-response browser tests cover first-time creation. Full
deployed behavior and all property types remain unverified.

2026-09-27 direct gutter selection comparison: At 1280 px in the signed-in
Notion comparison document, dragging from the left page gutter across the body
selected a paragraph, an empty block, and a linked database block as full-width
rows. The page title stayed unselected. Escape cleared the selection. The
isolated local Chrome fixture selected the corresponding heading, empty
paragraph, paragraph, and database block across 1440/1280/900 px viewports,
with a borderless marquee and without including the page title. The local
selected rows spanned approximately x=415..1135 at 1280 px; the observed
Notion rows spanned approximately x=417..1133. These are close visual
measurements, not pixel-exact parity. One visible discrepancy was that the
local empty paragraph still displayed its input hint through the selected
fill, whereas Notion showed only the blue row. The hint is now hidden while
that block is selected and returns after Escape. The selected local screenshot
is `outputs/notion_gutter_selection_1280.png`. The gutter browser suite passed
7 selection cases. Its eighth case used obsolete per-row trash expectations;
it now checks the current single fenced batch archive/restore request and
passed independently. The normal port 10322 app was unavailable, so local
visual validation used the isolated Chrome fixture. Broader R1–R11 parity
remains open.

2026-09-27 right-gutter follow-up: In the signed-in Notion comparison page
`Codex UI 비교 — 연결된 DB 원본 2026-09-16`, a drag beginning in the empty space
to the right of the embedded table selected four database rows while leaving
the page title unselected. Escape cleared those rows. The isolated local
browser test now drags from the same side across the first two table rows,
checks that the borderless marquee appears, and confirms that two database
rows are selected without selecting the database container block. This test
exposed a focus-dependent Escape bug: table-local key handling cleared rows
only while focus was inside the grid. The page-level Escape path now clears
gutter-selected database rows after closing higher-priority popups. The
focused right-gutter case passed in the isolated browser fixture. The normal
port 10322 app was unavailable, so a separate live local-app UI pass remains
open.

2026-09-28 paragraph spacing follow-up: the compared Notion paragraph rows
were 40 px high, while isolated local rows were 28 px. The local paragraph
content now has 6 px of vertical hit area above and below its 28 px editor;
the first text baseline stays aligned. The existing gutter suite passed at
1440/1280/900 px after this change, and the real-viewer fixture passed again
with two paragraphs, dark theme, borderless blue marquee, copy without cut,
390 px overflow check, permission revocation, and unchanged server document.
Port 10322 was restarted and reports healthy, but the browser security policy
blocked direct inspection of its logged-in UI; that gate remains open.
