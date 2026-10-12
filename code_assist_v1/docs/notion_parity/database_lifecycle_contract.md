# Owned database move and Trash — R5

Status: the synthetic reference move/Trash/restore journey is observed. A
first, capability-gated canonical transfer service now commits one uncomplicated
owned database between two documents and retargets stored database favorites
and validated linked views. Owned database block content/properties, typed Files cells,
and item body/appearance resources receive destination-scoped file copies.
Owned-database comment threads retarget with current authorization, while
unsupported attachments and child pages are refused before commit. The complete lifecycle service and
production UI activation remain unfinished. Do
not expose a command that merely reorders a block as `옮기기` in the database
sidebar menu.

On 2026-09-25, the authenticated Notion database row menu displayed `옮기기`
(`Ctrl+⇧+P`) and `휴지통으로 이동` below rename. Selecting `옮기기` opened a
destination picker with search and recommended personal/teamspace pages. Escape
closed it and returned focus to the database row. The existing comparison
database was not moved or trashed.

A new empty `UI-PARITY-20260925-DB-LIFECYCLE` database and destination page
were created under REF-FREE-A HQ. Moving the database from the teamspace root
into the destination page removed its root sidebar row, inserted a database
link in the destination body, and placed it under that page in the expanded
sidebar. Its URL kept the same database identity. The move toast named the
destination and offered undo. Selecting `휴지통으로 이동` opened a confirmation
dialog saying all pages would be moved to Trash. After confirmation the body
link and sidebar child disappeared. Trash search showed the database with its
destination breadcrumb; `페이지 복원` returned to the same database URL and
parent. Finally the synthetic destination page and its restored child were
moved to recoverable Trash, and the original comparison page was reopened.
No pre-existing comparison data was edited or deleted.

A second synthetic database, `UI-PARITY-20260925-FAVORITE-MOVE`, was favorited
and moved from the same teamspace root into a new synthetic destination page.
Its URL and the Favorites sidebar entry survived the move and a full browser
reload; the source teamspace row disappeared, while the destination page body
linked to that same DB. The synthetic destination and child DB were then moved
to recoverable Trash. The favorite still appeared at that point, and opening
it showed the DB with a Trash banner; the lingering favorite was then removed
manually and the original comparison page reopened. This directly establishes
that moving a DB must retarget existing favorites rather than dropping them.
Trash does not silently remove the favorite; the current staged implementation
of this behavior is described at the end of this contract.

On 2026-09-26, a separate synthetic source database with one row
(`MOVE-PROBE-ROW`), a linked-view host page, and a destination page were created
under REF-FREE-A HQ. The host used the `/연결` command (`데이터 소스 보기 연결`)
to attach the source. Its source link and row survived a full reload before
the move. Moving the source database into the destination page removed its
teamspace-root row, kept the source database URL, and showed the same source
link and row in the host immediately and after another full reload. The host
page and the destination page with its child source database were then moved
to recoverable Trash; the original comparison page was reopened. This direct
observation confirms that cross-document linked views must continue resolving
the original database identity after an owned database move.

The earlier local `openSidebarDatabaseMenu` had no corresponding actions. The editor
block menu's `옮기기` only reorders blocks inside the same document. An owned
database is bound to `workspace_collaboration_databases.document_id`; activity
uses `(database_id, document_id)` as a foreign key, while files and item bodies
also carry document-scoped authority. Moving its visible block alone would
leave the canonical owner and file access behind.

The successor implementation must keep the database ID stable and atomically
update both documents' canonical block structure, the owned database's document
binding, activity binding, file provenance/access, and affected search/history
projections. It must revalidate source and destination access/epochs, preserve
linked database references, and commit audit, outbox, and an idempotent receipt
with the move. A stale source, denied destination, lock, lost reply, or injected
fault must not leave the database visible in two places or inaccessible in both.
Trash must be reversible and hide the owned database and its rows from ordinary
navigation without deleting their canonical content. Restore must recover the
same identity and destination context subject to current authorization.

The `page_move_delta.cjs` preparation accepts an owned database match. It
removes exactly the intended owned block from the source Yjs document, leaves
linked views and other databases intact, and inserts the same block identity
in the destination. Missing or duplicate source matches fail closed. The
capability-gated `database_move.py` service revalidates both owner documents and the
database version, then changes the canonical block/document owner, DB owner,
activity record, both Yjs histories and document epochs in one transaction
with an audit/outbox event and an idempotent receipt. It also retargets the
database block's personal favorite locators in member/guest navigation state,
preserving favorite order and incrementing their versions. Valid linked views
in the source, destination, or other workspace documents keep their block IDs
and local view settings while their source document locator changes in SQL
and Yjs. Owned block content/properties, typed Files-property cells, item body, and
item appearance references are remapped to destination-scoped file copies in
the same transaction. This includes file IDs, `nf:` icons, and local file URLs.
It refuses references in unsupported database fields and item child pages
because those still require dedicated transfer logic.
Owned-block and database-item comment threads change their document locator in
the same transaction; other source-page comments stay put. A staged `move` and `move-result` HTTP
pair uses a separate database path to avoid the page-move catch-all route;
`collaborative_owned_database_move` remains false by default. The result reader
verifies both owner-page scopes and returns the
stored receipt so a lost response need not issue a new move request.
The staged browser controller `notion_database_move.js` persists the exact
operation, source/destination IDs and both document fences before POST. A lost
reply is resolved through `move-result`; an unknown receipt retries the same
request, and an exclusive browser lock prevents duplicate cross-tab submits.
The server receipt carries `operation_id`, which the browser checks against
the persisted request on both immediate success and result lookup.
The browser now loads this controller and offers an `옮기기` command in the
database menu only when that capability is enabled. Its picker lists other
owned editable pages in the selected workspace, grouped as personal, shared,
and teamspace destinations; it does not yet offer root placement. A recovery
banner survives reload, and an isolated browser test confirmed one commit
after a deliberately lost response. A second browser test confirmed that a
Files-property cell and item body remain downloadable through their new
destination file ID after the move, while the exclusive source ID is retired.
Isolated storage tests also cover uploaded item and owned block icons, including
the live Yjs destination block. The production
flag stays off pending remaining attachment shapes, child-page, Trash,
notification lifecycle, and wider UI validation.

The 2420 file table has an immutable `document_id` update guard and a
composite `(source_file_id, workspace_id, source_document_id)` foreign key for
copied-file provenance. Merely changing an attached file's document binding
would violate the guard and can invalidate existing copies. The current Files-
property and item-content implementation creates destination-scoped copy rows,
changes typed references atomically, and retires an original source file only when no
other source reference uses it. A shared source file remains available to its
other references. Other attachment shapes still need reference migration and
authorization review. Comment notifications have an immutable historical
`document_id` payload guard. The move updates the comment's current document
while retaining the notification ledger; inbox listing and read authorization
derive the effective locator from that comment and project it into the response.
Read state and notification identity survive the move. Source-document purge
and restore interactions need a separate lifecycle review before rollout.

The move preflight scans the current Yjs frames of every document in the
workspace, outside the SQLite transaction. A linked view that exists only in
live collaborative state is promoted to a canonical source locator as it is
retargeted. The commit checks every scanned document's sequence, version,
epoch and generation again, so an intervening linked reference or other edit
invalidates the scan. If live and canonical block identities disagree, the
move fails closed. The pure linked-view delta is prepared after the base
database-block transfer update for source/destination documents; this avoids
two independent first-generation Yjs bootstraps colliding. The full-workspace
scan remains a bounded preparation approach and may reject a move when an
unrelated document changes. More scalable reference indexing and structural
reconciliation remain future work.

The preflight also inspects the live owned database block, not only its SQL
snapshot. If its identity or source has changed, or a file reference exists
only in Yjs, the move fails before commit. An isolated uploaded-file case
confirmed that the source database and file authority remain unchanged; the
database move commit and delta tests passed (18 cases, 2026-09-26). A later
Files-cell and item-content move tests confirmed destination readability,
exclusive-source retirement, shared-source preservation, replay, and rollback.
Live-only or changed owned database block attachments still fail closed.
The preceding move commit/HTTP suite passed 18 cases, and the isolated browser
move keeps both a Files cell and item-body attachment readable at the
destination (2026-09-26). Production capability remains disabled.

A later packet extends the destination file remap to a local file URL in the
owned database block's content. The SQL content and destination Yjs block
now carry the same destination-scoped file ID. An isolated storage test confirms
rollback keeps the original URL/file and a successful move retires the exclusive
source file. The combined storage/HTTP/browser regression suite passed 21 cases
(2026-09-26). This does not
permit live-only differences between the canonical and collaborative block.

Before production UI activation, implement and test remaining attachment shapes,
item child pages, source-purge effects on historical notifications, Trash/restore, concurrent
edits, permissions, and browser focus/reload. Same-page block reordering is a
different operation. Production migration and feature-flag changes remain
separate rollout gates.

Independent database Trash cannot reuse `page_archive.set_page_archived` as-is.
That service changes `workspace_collaboration_documents.archived` for the whole
document subtree and removes/restores a `child_page` link; an owned database is
still one block inside a live owner document. The current
`workspace_collaboration_databases` row has no archived state. A forward schema
must record the database's own reversible Trash membership and restoration
anchor, then normal DB reads, navigation, search, linked views, favorites, and
file/comment access must enforce that state without archiving unrelated blocks
in the owner document. The archive command must retain the database ID and
its rows, commit its block/Yjs removal and receipt atomically, and restore at
the recorded position under current authorization. A parent-page Trash is a
separate path whose database visibility follows the parent lifecycle.

The offline 2421→2422 migration now defines
`workspace_collaboration_database_trash_state` for that independent membership.
It binds a trashed database to its owner document/workspace, stores the exact
owned block snapshot, prior position/version/creation time, and optional restore
anchor, and records the archive operation and actor. A second table preserves
the IDs of block-anchored comments: deleting the SQL block otherwise invokes
its `ON DELETE SET NULL` comment foreign key, making their original anchor
impossible to reconstruct reliably on restore. The migration is forward-only and
does not mark any existing database as trashed. A synthetic catalog test
verified plan refusal, injected rollback, existing DB/block/comment preservation,
checksum, and foreign-key/JSON constraints. Do not apply 2422 to the running
catalog yet.

The isolated 2422 runtime now accepts the new marker for its existing document,
database, navigation, and editing services. An internal owned-database Trash
command removes the exact SQL/Yjs block while retaining the database ID, rows,
and block-comment identities. Restore reinserts that block at its saved anchor,
rebinds those comments, and verifies the same owner, version, permission,
document stamp, and live block state. Both directions write activity and an
idempotent operation receipt in one transaction. Canonical document and database
reads hide the trashed block and its comments; direct database reads and edits
reject a trashed database. Synthetic archive, restore, replay, injected rollback,
permission, migration, and read-gate tests passed (2 Trash tests, 2026-09-26).
Selected existing database activity and move regressions also passed (2 tests).

Gated `/workspaces/{workspace}/databases/{database}/trash`, `/restore`, and
`/lifecycle-result` HTTP routes now expose exact receipt recovery on isolated
2422 fixtures. The separate database path avoids the existing page Trash
catch-all route. Direct comment listing, inbox notification reads, comment
mutation authorization, and the linked-source picker now hide a trashed DB and
its block/item threads, including unanchored replies. The HTTP and storage
Trash tests passed together (3 tests); adjacent linked-source and item-comment
regressions passed (3 tests, 2026-09-26).

Search projection preparation filters the trashed owned block even if a live
frame still carries it, and database date-reminder projection excludes trashed
database rows. A synthetic search query for the owned DB title disappears on
Trash and reappears on restore; the existing search/backlink/reminder and linked
source regressions passed (2 tests, 2026-09-26).

The gated `/workspaces/{workspace}/database-trash` listing returns only active
owner-page database Trash entries. It includes title, parent page, archive time,
and current document/database fences for restore, without exposing rows or
block content. Disabled capability, wrong role, query validation, one archived
entry, and empty-after-restore behavior passed the isolated HTTP test.

The sidebar and full-page database menus now offer `휴지통으로 이동` only when
the staged capability and owner/edit/lock checks allow it. The shared Trash
dialog includes database entries and restores the exact owned block. A browser
intent is saved before POST, serialized across tabs, checked against the exact
receipt after reload, and retried only with the original operation and fences.
After a confirmed archive, restore can start directly without an extra
acknowledgment click; an unconfirmed opposite operation remains blocked.
Six Node recovery checks and an isolated Chrome journey with a deliberately
lost archive response, reload, receipt lookup, and restore passed (2026-09-26).
The collaborative Trash dialog hides legacy-only filters and does not claim an
unimplemented automatic 30-day purge; the browser journey checks both details.

The coordinated backup observer now accepts the exact 2422 schema marker. An
isolated 2411→2422 catalog test archived an owned database, created and verified
a coordinated backup generation, restored it to a new root, reopened the
restored catalog, checked the database stayed hidden in Trash, and restored it.
The 2422 test now includes a real uploaded attachment used by the owned DB
icon. Its file is inaccessible while the database is trashed, remains hidden
after restoring the backup to a new root, and returns its original bytes after
database restore. This test and the existing 2421 restore regression passed
(2026-09-26). No production catalog was migrated or restored.

File reads now omit or deny files referenced exclusively by a trashed database
while preserving access to a file also referenced by an active page. File
deletion rejects a file held by a trashed database so restore cannot revive a
broken reference. The scoped file scan includes saved owned-block content and
appearance, database properties, views, templates, rows, cells, and row bodies.
An isolated test covered an owned icon, local block URL, exclusive Files cell,
shared active-page reference, and restoration; the selected existing file
authority regression also passed (2026-09-26).

The file read gate now also replays current Yjs blocks outside the SQLite read
transaction. It removes trashed owned blocks from that projection, combines
remaining live blocks with canonical active database rows, and rechecks the
document, Trash membership, and exact CRDT frames under a second read before
returning a file or file list. A live-only active block reference keeps its
shared file accessible; removing that reference hides it again. Archiving
rejects an owned database block whose live file references differ from its
canonical saved block, avoiding an attachment missing from the Trash snapshot.
This shape conflict is returned as HTTP 422 with a synchronization/retry
message, so the browser does not treat a known rejection as an uncertain save.
The three focused Trash-file tests and the existing file-authority regression
passed (2026-09-26). The Trash HTTP/file group passed five tests after the
error mapping. The isolated browser archive/reload/restore journey also passed
after narrowing the archive guard to attachment differences.

This is not the completed feature. The new capability remains false by default.
Unrecognized attachment shapes, other reader surfaces, concurrency breadth,
broader attachment/backup combinations, and wider browser/Notion parity
verification remain. The production migration and feature switches remain
untouched.

On 2026-09-27, a direct Notion comparison showed that an archived database
remains in Favorites and opens an archived database page with a Trash banner.
The staged local implementation now keeps an exact saved favorite in member
and guest navigation while its owned database block is archived, returns only
its title/type/ID as archive display metadata, and permits removing that
favorite without allowing a new archived favorite. The sidebar shows the
archived favorite after navigation refresh. A direct Notion recheck found a
red Trash banner with restore, database title, saved table view, and an
empty-results state for the synthetic database. The local owner activation
now opens a read-only archived database page at its stable sidebar DB URL,
with the red banner, title, saved properties/rows, and restore. The selected
view's basic column visibility, filters, and sorts are projected read-only.
Reload retains
that route; restore returns to the active full-page database. The dedicated
GET is owner-only, checks exact retained block identity, leaves ordinary DB
read denied, and uses no-store responses. Guest activation still shows an
owner-restore message; guest archived-page behavior in Notion is not yet
established. An immediate Favorite→Trash sequence exposed a race: the
optimistic favorite row could precede its server commit. Archive now waits
for that navigation write. The isolated lost-response browser journey,
owner/role HTTP checks passed; a current-state screenshot was inspected. Per-view layouts
beyond a read-only grid, archived DB toolbar actions, attachments, retention
wording, broader role concurrency, and exact visual parity remain. The
capability is still off by default and was not enabled in the production
catalog.

On 2026-09-27, the archived Notion database topbar was checked again. It keeps
Share, Copy link, and More in that order; Share opens a read-only permission
popover with disabled invite/permission controls while archived. The local
archived database now shows the same three controls. Its Share popover reads
the authorized parent page grant snapshot and labels the inherited access
without offering permission mutations. More offers Copy link, Trash view, and
Restore; the existing red-banner Restore remains available. The isolated
lost-response browser journey verified those controls, the grant load, the
Trash view, reload, and restore. Its visual captures and viewport assertions
confirmed the Share popover remains fully visible at 1280 and 390 pixels.
The archived database's filter/sort/search/settings toolbar, non-table view
layouts, Notion's exact More menu entries, and guest comparison still require
work. The production feature flag and catalog were not changed.

A read-only Notion recheck of that archived toolbar found that Filter and Sort
both open a property picker (the synthetic database offered Name), Search
expands an inline text field and closes on Escape, and Settings opens a
right-side view panel. The archived state disables automation creation and
several settings controls, but keeps the filter/sort entry points visible.
The comparison page was returned to its original tab after inspection; no
database value or view setting was changed.

The local archived page now presents the selected view chip and Filter, Sort,
Search, and Settings controls in the inspected order. The property picker,
criterion editor, search field, and settings panel work on a temporary
projection of the retained rows. This projection is read-only: it never
writes a saved database view or its rows, and temporary conditions disappear
after reload. Automation and AI autofill remain disabled. An isolated Chrome
journey created two canonical rows (`Alpha` and `Zeta`) before archiving,
then confirmed one-row filter/search results, descending sort order,
condition clearing, Escape, reload, and restore. A 390-pixel capture and
toolbar bounds check covered the compact layout. The Notion comparison also
confirmed that selecting a Filter property without entering a value did not
leave a saved condition after reload; that transient selection was cleared
and the original page reopened. Persisted nonempty filter values in archived
Notion views, non-table archived layouts, exact More menu items, guest access,
attachments, and production behavior remain unverified. The feature flag
stays off by default; no production catalog migration was applied.

The retained snapshot now drives all six local database layouts: table,
board, list, gallery, calendar, and timeline. Archived view tabs switch a
client-only projection; the saved active view is restored on reload. Rows,
view configuration, appearance, saved filters, and saved sorts remain
read-only. A two-row isolated Chrome journey created five additional saved
views before Trash, then checked the rendered row/card counts, calendar event,
timeline bar, filter/search/sort behavior, narrow toolbar bounds, and restore.
It passed in 55.55 seconds. The narrow screenshot exposed a duplicate
recovery banner above the Trash banner; confirmed archive recovery is now
hidden while its matching archived database page is open, without discarding
the recovery record. The current direct Notion comparison database has only
a table view, so exact archived board/gallery/calendar/timeline appearance
has not been proven against Notion. Screenshots of the local board, calendar,
and 390-pixel table were inspected. Production remains untouched.
After a read-only hour-scale projection adjustment, the full isolated browser
journey passed again in 53.15 seconds, including a reload assertion that
restores the saved table view after a temporary board selection.
The recovery banner now remains in its in-progress state until navigation
refresh finishes, so opening the archived favorite after a confirmed lost
reply does not race the refresh or leave a stale error toast. The same
end-to-end journey, including the no-toast assertion, passed in 69.18 seconds;
the final 390-pixel capture shows the single red Trash banner.

A 390-pixel timeline follow-up found that changing to the last view rebuilt
the tab strip at scroll position zero, leaving the selected tab outside the
visible strip. The archived preview now scrolls its selected tab into the
strip after rendering. The isolated end-to-end browser journey checks the
selected tab and filter popover bounds, page width, reload, and restore; it
passed in 55.86 seconds. The narrow timeline screenshot was inspected. The
current Notion comparison database still has only its table view, so this is
a local usability fix, not a claim of exact Notion timeline parity.

The archived database More menu was finally opened in the signed-in Notion
comparison tab. Its visible order is Export, Updates & analytics, Open in
Windows app, last editor/time, and database help. Copy link is a separate
topbar control and Restore is on the Trash banner. The local archived topbar
menu now follows that order, has action search, and removes its redundant
Copy link, Trash view, and Restore actions. Updates & analytics and desktop
app integration are shown disabled because those services are unavailable.
The owner-only archived export reuses the retained canonical snapshot to
produce Markdown & CSV or HTML ZIP bundles without restoring the database;
the service checks archive ownership again before returning rendered data.
An isolated HTTP test covers the feature gate, unauthorized actor, invalid
view, both bundle formats, and post-restore denial (2 tests passed). The
isolated Chrome journey verifies the menu, search, export dialog, ZIP download
containing both saved rows, reload, and Restore (1 test passed in 56.69s).
The local 1280-pixel menu capture was inspected against the Notion menu.
At that point Notion's export confirmation had not been submitted, so exact
ZIP contents and the disabled service items were unverified. Production
switches and schema were not changed.

A later read-only export of the Notion comparison database confirmed the ZIP
shape. Its empty archived database produced an empty ZIP. An active comparison
database with one row produced two UTF-8-BOM CSV entries (current view and
`_all`) with CRLF lines; selecting HTML produced an HTML entry and one
UTF-8-BOM CSV entry. Entry names include the database identity after the
title. The shared local database bundle writer now adds that identity and
the CSV BOM while preserving HTML without a BOM. Isolated route/archived
HTTP checks passed (6 tests) and the active collaborative database browser
journey passed (2 tests, 98.04 seconds). The Notion export dialog also offers
PDF, a content choice (all content or excluding files/images), child-page
options, and an HTML comments switch. Those controls and attachment payload
parity remain unfinished. The archived comparison database had no visible
rows, so its empty ZIP does not establish what Notion exports from a populated
archived database. The three downloaded comparison ZIPs remain in Downloads
because automatic cleanup was blocked by execution policy.

On 2026-09-27 the live active comparison database's HTML export dialog was
reopened. Its content selector offers `모든 콘텐츠` and `파일 및 이미지 제외`;
the child-page switch controls an initially disabled child-folder switch, and
the HTML dialog includes a comments switch. PDF additionally exposes page
size and scale. These are observed UI controls, not local implemented export
features. The archived empty Markdown & CSV download has now been matched:
the local owner-authorized export returns a valid ZIP with zero entries when
the retained database has zero rows, instead of two header-only CSV files.
An isolated route test deletes the fixture row, archives its database, and
checks the downloaded ZIP entry list (three HTTP tests passed in 44.74s).
Populated exports still use the verified CSV/HTML bundle paths.
With `하위 페이지 포함` enabled in the live active database's HTML dialog,
Notion enabled `하위 페이지용 폴더 생성` and submission displayed an export-in-
progress message promising an email download link. No immediate ZIP appeared
in Downloads during this observation. This is evidence of the UI state only;
the eventual bundle shape and email delivery have not been verified.

The active database topbar More menu has now been aligned with the directly
observed Notion ordering and action search. The local menu exposes supported
link, duplicate, move, Trash, export, and help actions while showing unsupported
layout, lock, import, analytics, history, notifications, connections, and
desktop-app integrations as disabled. An isolated collaborative browser journey
checked the order, search, Escape focus, a disabled item, export, and 390-pixel
bounds (one test passed); the desktop and narrow captures were inspected.
The adjacent side-peek access-revocation journey passed on retry but had one
intermittent loss of an unsaved database title after a failed command. Local
database diffs now count as pending after the command timer ends, so remote
editor projections and live canonical refreshes preserve the draft. The
isolated side-peek journey passed with an explicit refresh between the failed
save and grant revocation, and the two-test browser file passed together
(95.38 seconds). This does not establish complete role, network, or production
parity; the feature gate and production schema remain unchanged.

The live Notion `레이아웃 사용자 지정` menu action was opened directly. It enters a
database item-page layout editor with a sample item, property/content/comment
modules, compact/tab structure choices, and Cancel/save controls. It is not
equivalent to the existing local database view-name/layout dialog, so the
topbar item remains disabled until the item-page layout contract is implemented.
Cancel returned to the database without saving, and the comparison tab was
restored to its original item page.

A second direct inspection of the same editor clarified its scope and draft
behavior. The top bar offered a sample-item preview selector, `취소`, a primary
`모든 페이지에 적용` action, and a secondary option to reset the original page
layout without deleting properties. The right panel offered `간결한 구조` and
`탭 구조`, the inline-comment position selector, and switches for `페이지`,
`속성 아이콘`, `전체 너비`, and `데이터베이스 템플릿`. In the compact draft the
preview contained title, property group, page/comment module, and content
module, plus an optional side panel. Switching to tab structure introduced a
`콘텐츠` tab in the item preview. The property-group menu exposed a section
add action, per-property visibility toggles, and `패널로 옮기기`; the page and
panel add controls offered existing properties and new property types. The
layout changes were only draft changes: `취소` prompted to discard them, and
discarding returned to the database. Browser back restored the original item
page. This confirms that the target is a database-wide item-page layout with
module placement and display options, requiring storage separate from view
config and distinct read/write authorization. No live Notion layout was saved.
The inline-comments selector was reopened on the original item page and offers
`기본` and `최소화`. It was dismissed without selecting either option.
Turning off the `페이지` switch in an unsaved draft removed the preview's
page/comment module while leaving the title, property group, and content module
visible. The draft was discarded and the original item page was unchanged.

The first local item-page-layout packet is now code-only. Forward migration
2026082426 adds one layout JSON/version row per database without populating
existing databases or changing view config. The canonical surface command
`database.item_layout.set` requires the source document's schema-edit role,
current access epochs, document sequence, an expected layout version, and an
operation ID. It validates module structure, property placement, and the
observed display options; a saved layout enters authorized database reads and
the document content hash. Database copy remaps referenced property IDs into
the copied layout. Isolated migration/service/copy regression and legacy custom
emoji/copy checks passed. The topbar layout item remains disabled: a faithful
editor and application of saved layouts in full/side item pages, role/remote
browser checks, and rollout review are still required. The 2426 migration was
applied only to disposable test catalogs, never to the user catalog.

The active Notion HTML export dialog was directly rechecked: format, database
view, page content (`모든 콘텐츠` / `파일 및 이미지 제외`), include subpages,
create a child-page folder, and export comments appear in that order. The
local active and archived database dialogs now display that order, including
explicitly disabled PDF, subpage, folder, and comment controls. Selecting
`파일 및 이미지 제외` sends a validated page-content option through the personal,
collaborative, and archived routes. HTML rendering omits image, file, video,
audio, and PDF blocks from database item bodies, including nested bodies,
while preserving text and CSV data. An isolated canonical row-body export
test, an archived retained-snapshot ZIP test, personal route test, the
16-test HTML database suite, and active/archived browser journeys passed.
The active 390-pixel dialog capture was inspected; all fields and actions fit
within the viewport. Binary attachments are still not bundled even under
`모든 콘텐츠`; file properties and actual Notion ZIP contents for subpages and
comments have not been established. The disabled options are not represented
as completed export behavior. Production feature flags and schema remain
unchanged.
The format selector was checked once more in Notion: `댓글 내보내기` disappears
for Markdown & CSV and reappears for HTML. Both local database dialogs now
follow that visibility rule; their browser regression checks were updated.

On 2026-09-27 the active and archived collaborative database HTML ZIP paths
started packaging the original bytes of files actually referenced by visible
database rows: image/file/video/audio/PDF body blocks and visible `files`
properties. The HTML uses relative `assets/` links; duplicate references to
one file produce one ZIP entry. The writer streams file contents into a
spooled ZIP and checks each byte stream against the canonical size and SHA-256.
Active exports use the normal permission-aware file read before and after
packaging, plus source witnesses. Archived exports use the existing owner-only
retained-snapshot access rule and a narrow document-local file lookup, then
recheck the archived snapshot and file metadata. The normal file endpoint
continues to hide archived-only files. `파일 및 이미지 제외` produces no asset
entries. Isolated active service/HTTP, archived service and Trash HTTP tests
passed; no production feature flag or schema changed. Personal database file
bundles, Markdown & CSV attachments, subpages, comments, PDF export, precise
Notion ZIP media naming, and production 10322 behavior remain unverified or
unfinished.
The combined isolated server regression set passed 24 tests in 200.62s, and
the active/archived browser download journeys passed 3 tests in 148.53s.
The user-facing 127.0.0.1:10322 endpoint still refused connections at this
check, so these results do not prove deployment there.

The first item-page layout display packet now reads the saved database-wide
layout in owned/linked item pages. It orders the page properties, places panel
properties in a side group, hides explicitly hidden properties, orders the
property/comment/content modules, and renders the content tab for the tab
structure. The same descriptor drives side and full item modes. An isolated
2426 browser journey saved a layout, opened a row, and changed
to full mode with the property placement intact. Existing item-icon and side
peek checks passed; one unrelated Playwright driver disconnect in a combined
run passed on an isolated rerun. The topbar editor remains disabled, and inline
comment minimization, template visibility, role/browser and production checks
remain unfinished.

The personal (legacy) database HTML ZIP now uses the same relative asset
links and streaming ZIP writer. Its file manifest resolves only references in
visible rows and properties through the existing per-file view authorization;
an accessible cross-page file reference is allowed, while a reader who can
see the DB page but not the referenced file is denied. The personal page and
file metadata are re-read after ZIP creation, and the file bytes are checked
against their metadata size and pre-packaging SHA-256. The isolated personal
route suite passed 5 tests. Direct inspection of the signed-in Notion
comparison database showed no attached files, so the exact Notion ZIP naming
and Markdown & CSV behavior with attachments remain unverified. The tab was
returned to its original item page without data edits.

The item-page layout editor is now available from the active database's topbar
only when the canonical database reports schema 2426 layout support and the
current document role can edit database structure. It works on a local draft:
the sample row, compact/tab structure, module order, page/panel/hidden property
placement, page visibility, property icons, full width, and inline-comment
position update the preview without server writes. Cancel/Escape with changes
offers discard confirmation; Apply sends the version-checked canonical command,
reloads the database, and updates already-open item pages. Target-version
conflicts leave the draft open and never overwrite the remote layout. The
template switch is displayed disabled because template display/application in
item pages is not implemented. A fresh direct inspection of Notion's editor
showed its full-screen title/property/page/content modules, persistent middle
panel, structure cards, and right-side options; the local editor was reshaped
to match these major regions. The Notion draft was canceled without saving.
An isolated 2426 Chrome flow covered save, reopen, discard, minimized comments,
side/full item display, remote conflict, role denial, and 390px layout; two
legacy browser regressions passed. The actual product at 127.0.0.1:10322
remains unreachable and was not migrated or switched on. Exact Notion module
menus, new-property creation from the editor, DB template handling, and wider
R1-R11 role/visual parity remain unfinished.

The property-group menu was directly rechecked in Notion. Each property offers
three visibility states (always, hide when empty, always hide), and the whole
group can move into the center panel. The comparison draft was discarded. The
local editor now exposes those states and the group move in its preview menu.
The canonical JSON layout accepts optional `empty_hidden_properties` and
`property_group_placement` while older saved layouts remain readable; copy
remaps the additional property IDs. The side and full item pages hide empty
values conditionally and display a grouped panel heading. The isolated 2426
service migration/copy test and Chrome editor/save/item journey passed. The
1440px menu and moved-panel captures were inspected. At that stage, section
creation, new property creation, templates, exact Notion menu styling, live
10322, and the full R1-R11 audit remained unfinished. No production schema or
feature flag was
changed.

Notion's `+ 섹션 추가` was inspected directly: the first section groups the
existing properties, a later section starts empty, and its menu offers rename
and delete without deleting the properties. `Add a property` opens a name field
and a type picker. The comparison layout draft was discarded. The local layout
JSON now stores optional ordered sections with property membership; new
properties can be staged in the editor and committed atomically with the
layout through the existing `database.item_layout.set` transaction. Cancel
leaves no new property. Supported simple property types use the existing
canonical property-create validator; relation, formula, and rollup still need
their configuration flow and remain disabled in this picker. Section data is
remapped on database copy and cleaned when a property is deleted. Isolated
2426 service checks covered save/replay, copy, stale-version rejection,
multi-property rollback, and deletion cleanup. The Chrome journey covered
section add/rename/reassignment/delete-discard, property creation and discard,
side/full item display, and a 390px menu boundary. The picker capture was
inspected. Production 10322 and the wider R1-R11 audit remain outstanding.
Property and type searches filter their existing menu rows without replacing
the focused input, reducing the risk of Korean IME composition loss; a direct
Windows IME check of these new fields remains outstanding.

On 2026-09-27 Notion's `패널에 추가` menu was directly checked in the item-page
layout editor. It combines existing properties with quick new-property types
(`파일과 미디어`, `숫자`, `텍스트`, `사람`, `선택`); `더 보기` expands the remaining types,
including formula, relation, and rollup. Selecting `숫자` immediately added a
draft property to the preview and opened its name/type/format settings. The
draft was discarded and the comparison page returned to its original state.
The local editor now uses a combined panel menu for existing-property moves
and simple new-property types. A new type opens the existing draft name/type
picker, so it currently takes an extra confirmation step compared with Notion.
The isolated layout browser journey passed, including the panel menu's search,
existing-property move, and new number-property discard. Advanced property
configuration, exact settings pane, direct Windows IME verification, and
production 10322 remain outstanding.

The same Notion layout editor's `페이지 모듈 추가` menu was checked directly. It
uses the same existing-property / quick new-property / `더 보기` grouping as
the panel menu. The local page and panel additions now share one picker and
one draft-placement path. Existing properties move to the selected region;
simple new types enter the name/type draft picker and commit only on Apply.
The isolated browser journey covered both menus, search, moving a property
back to the page, discarding a panel-created property, and popup bounds at
390px (54.08s, one test passed). The Notion draft was closed without saving.
The local extra confirmation step and advanced type configuration remain
differences.

Further direct comparison of `더 보기` showed that Notion creates a formula
draft immediately and opens a separate formula editor with property/function
suggestions. A relation draft prompts for a target data source. A rollup draft
requires an existing relation, then a target property and a calculation; the
calculation menu includes original/unique values and count/percentage groups.
No comparison draft was saved. The local canonical service has narrower formula
syntax and a bounded set of rollup aggregations, so these types remain
unavailable in the layout picker until their configuration and validation
contracts are connected.

For simple types, the local page/panel menu now creates a draft property as
soon as its type is clicked and opens a right-side name/type details view. Name
edits update the draft and preview; Apply still commits the property and
layout atomically, and Discard leaves no property. The isolated browser
journey passed (59.11s) and its details screenshot was visually inspected at
`outputs/item_layout_property_details_20260927.png`. This is closer to
Notion's immediate draft flow, while type-specific settings are still absent.
An additional isolated browser save reopened the editor after a remote layout
version change, created a page-placed text draft, renamed it in the details
pane, applied it, and verified both the canonical property name and saved
page placement. A new property added to the page while the property group is
in the panel no longer acquires a panel-section membership. The final isolated
browser check passed in 58.23s, with JavaScript syntax valid.

The editor now ignores Enter/Escape keydown events while an IME composition is
active (including keyCode 229). An isolated browser regression dispatched
composing Enter in section rename and composing Escape in the new-property
name field; both remained open, and the full item-layout browser journey
passed in 55.55s. This is synthetic event coverage, not a direct Windows
Korean IME test. The local user-facing 10322 port had no listener at this
check, so production behavior remains unverified.

Direct comparison of a disposable Notion layout draft showed that
`레이아웃에 추가` on a section property creates an independent page property
module. Moving that module to the panel and back to the page keeps it
independent; only `속성 그룹에 추가` returns it to its prior section. The draft
was discarded without changing the comparison database. The local layout
now stores `standalone_properties` separately from page/panel placement and
section membership, so moving a standalone module preserves its section
membership for later restoration. Rendering filters those IDs out of the
group and displays them as separate page/panel cards. Canonical validation,
database copy remapping, and property-deletion cleanup cover the new field.
The isolated storage/copy/delete test passed (36.75s), and the browser
journey passed (64.31s), including page/panel round trip, group restoration,
save, side/full item display, and 390px editor bounds. The panel screenshot
was inspected at `outputs/item_layout_standalone_panel_20260927.png`.
Advanced type settings, template application, broader roles/concurrency,
direct Windows IME, and the unserved 10322 runtime remain open.

Direct Notion template comparison used the disposable relation test database. A
blank item offers template creation; this immediately opens a page-shaped
editor. The database New menu lists saved templates, an Empty default, and New
template; each saved template has a menu for setting it as default, editing,
duplicating, and deleting it. The comparison-created blank rows and templates
were removed after explicit user authorization, and the original four named
rows remained. The local implementation now offers saved-template creation,
plain-paragraph body editing, simple default-value editing, duplication, and
archive through an owned-database modal; the New menu can apply a template
atomically to a new row. An empty existing item can select an active template:
its nonempty cells (including its title) are preserved, empty cells are filled,
and the body is copied with new block IDs in one version-checked transaction.
The item layout show_templates option controls that chooser. Isolated browser
and authority tests cover this flow, including a narrow viewport and rollback
on invalid defaults. The local modal still differs from Notion's full-page
block editor; rich template bodies remain preserved but cannot be edited in
that modal. Default-template selection, relation/file defaults, broader role
and concurrency coverage, and the unserved 10322 runtime remain open.

The local template editor now uses the owned-item block editor in a full-height
right-side panel. Supported text and structural blocks, including headings,
lists, toggles, simple tables, and tabs, retain their type when saved to a
template and applied to a later row. Closing with unsaved changes asks whether
to discard them. A remote version change after the editor opens rejects the
stale save, and a document redraw restores an open template menu. The isolated
browser regression covered those flows and the existing item layout (three
passes, 140.21s); the item-body JavaScript suite passed twelve cases. The
screen was visually inspected at
`outputs/notion_template_block_editor_20260927.png`. At that stage, media
blocks were preserved without editing controls; later paragraphs record the
resource editor and automatic-save additions.

The forward-only 2427 schema adds one versioned default-template selection per
owned database. No row means the historical Empty default. A schema editor can
select an active template or explicitly restore Empty through the database
surface command; content-only editors cannot change this setting. Ordinary
row creation applies the saved default atomically, while an explicit Empty
choice sends a null template ID and bypasses it. Archiving the selected
template clears the choice in the same transaction, and independent database
copy remaps the selection to the copied template. The isolated migration and
service checks passed twice in total, including stale-version and role denial;
the new/default/Empty/new-button browser flow and the existing template and
item-layout browser regressions passed. The local picker now places default
selection, edit, duplicate, and delete under each template's more-options
button, matching the directly observed Notion grouping. It retains the same
menu in databases without the 2427 default-template schema, omitting only the
default action there. A 390px browser check showed no horizontal overflow;
desktop screenshots of the choice and action menus were inspected. Schema
2427 has not been applied to live data.

The template's page-shaped body editor now admits the same resource blocks as
an owned database item: image, file, video, audio, PDF, bookmark, and embed.
It uses the existing link and page-file upload picker; an upload checks the
current page and schema-edit scope before and after transfer, then remains in
the template draft until Save. Existing resource blocks can be opened and
edited instead of forcing a preserve-only screen. An isolated browser flow
inserted a bookmark link and uploaded a PNG, saved them to an existing
template, and verified that a new item receives both resource references.
The same flow forced a document redraw while the template's action menu was
open and verified that the submenu stays usable. The 1440px editor screenshot
was inspected at `outputs/notion_template_resources_20260927.png`.
The local New Template action now commits an empty `새 페이지` template before
opening its editor, matching the directly observed Notion behavior: closing
without typing leaves a saved template. Existing-template title, supported
property defaults, and body changes save after a short idle period; closing
flushes remaining changes. The current target version advances only after
canonical reload, and a remote version conflict leaves the draft visible
instead of overwriting it. Title/default-property IME composition pauses
autosave until composition ends. A live browser journey verified immediate
creation, empty close/reopen, autosaved title, close-time property save, and
resource-bearing body preservation. A synthetic composition event verified
that a partly composed Korean name was not saved, then the completed name
was. The existing selected/default-template browser checks also passed.
`outputs/notion_template_autosave_20260927.png` was visually inspected.
The template editor has no footer during normal autosave; the header Close
flushes changes, and a footer Retry Save action appears after a recoverable save error.
Its supported property defaults now use the same compact icon/name/value row
shape as an item page, without a separate form heading or visible "body" heading.
Rich bookmark previews and size controls, file property defaults,
broader concurrent editing and role coverage, cross-page media copy,
and the unserved 10322 runtime remain open.

Relation template defaults can now select target rows by title in the template
editor. Applying a template to a new or existing empty row writes the relation
and any configured reciprocal cells in one transaction; other affected source
documents get their normal refresh events. Invalid or inaccessible targets roll
back the entire row operation. The isolated cross-document relation test covers
create, apply, idempotent replay, missing target, and locked inverse target.
The isolated browser journey covers cross-page target loading by row title,
selection, save, and application.

File property defaults now accept up to 20 files uploaded to the template's
source page. Template create/update canonicalizes file names, MIME types, and
sizes from active same-document file records; missing, deleted, or foreign
file IDs are rejected. New-item creation and application to an empty item
recheck every file in the same transaction, so a stale file reference cannot
leave a partially created item. The editor shows uploaded files, supports
removing them, and saves the file list after upload. Closing while an upload
is in progress stays on the editor. An isolated browser journey verified PNG
upload, autosave, and inheritance on a new item; the isolated service test
verified canonical metadata, apply, replay, missing-file rejection, and
rollback after a file was deleted. Follow-up isolated copy tests confirmed
that both same-page database copy and cross-page copy rewrite template file
defaults to independent copied file IDs. The cross-page copy's template also
applied successfully after the original file and page were removed. Notion's
Files & media help also documents pasting a link into a file property; the
template editor now accepts HTTP(S) file links with an optional display name.
The server rejects malformed URLs, credentials, and non-web schemes, while
copying preserves external links. An isolated browser journey verified one
uploaded file and one external link together in a template and its new item.
The same journey checked the populated file row at a 390px viewport: both
entries remained visible and the editor had no horizontal overflow. A cross-
page copy test also verified that the uploaded file points to the independent
copied file while the external link survives unchanged, including when the
copied template is applied after the source page and file are removed.
The created item's table row renders both defaults as links to their respective
local file and external URL.
The ordinary owned table Files cell now offers `링크 추가` beside upload, with
HTTP(S) validation shared in intent with the template editor. Canonical cell
writes validate external links on the server as well. The owned item peek shows
file links as clickable chips and can add or remove them. Both editors retain
their in-progress link form across unrelated document redraws. An isolated
Chrome journey rejected a non-web URL, added table and item-peek links, checked
canonical saves, reloaded the page, and found the links in both places again.
The focused server check rejected invalid URL schemes, credentials, and
backslashes without altering the cell sequence. Batch row-property editing
still excludes Files because its text editor does not support file arrays.
The owned item peek also accepts a file upload in that same property row. It
uploads to the owning document, refreshes the canonical database witness,
and saves the uploaded file ID into the selected row; a browser journey
reloaded the page and found the new file in both table and item views. The
item-close and item-navigation controls wait while that upload is in flight.
Direct cell writes now reject missing, deleted, or foreign-document file IDs before
mutation. The focused server check covered valid local upload, missing and
foreign IDs, while existing file Trash and HTML export regressions passed.
The normal table link editor retains its URL/name draft across a full document
redraw even after validation rejects a URL; the browser journey forced that
redraw and then saved the corrected link. Production 10322 remains unserved.
An isolated two-tab Chrome check kept the same template editor open in both
tabs: the first tab autosaved a name change, the stale second tab received a
conflict error instead of overwriting it, and discarding/reopening displayed
the first tab's saved name. This verifies one concurrent edit path; different
users, simultaneous body/property changes, and lost responses remain open.
A focused service role check also confirms that a content editor can apply a
template to rows but cannot create or rename template definitions; rejected
schema writes leave the document sequence unchanged.
The source behavior is documented at
https://www.notion.com/en-gb/help/database-properties.

The template editor's title now reads as a page title without a separate
visible form label, and its property columns and spacing are closer to the
observed Notion item page at desktop and 390px widths. This is a visual
comparison to the item page, not a claim that the live Notion template editor
was directly inspected in this pass. A body-resource upload now pauses template
autosave and waits for an already-running save before starting its file session;
the same wait applies to a template file-property upload. This keeps the
document sequence from changing between upload begin and finalize due to this
editor's own autosave. The isolated browser journey passed after the change,
including image upload, file and link defaults, autosave, row application, and
the 390px overflow check. Other tabs or users may still change the document
during an upload, in which case the server's source fence remains authoritative.
When that fence rejects a session, the browser now discards its stored session
handle so Retry begins against a fresh document witness; transient server or
network failures still retain the resumable handle. The UI shows a Korean
retry message for this particular conflict. A focused Node test covers a 409
followed by a successful new session and the icon-upload suite covers the
user-facing message. The focused browser journey passed again after the title
focus outline was adjusted to match the item-page title.

Notion's current database-template help describes a database-location notice at
the top of the template editor. The local editor now shows the owning database
title in a neutral, compact notice above its page title. The isolated template
browser suite passed (4 tests, 203.48s), and its full editor journey passed
again with a 1440px screenshot and 390px overflow check (1 test, 72.67s).
The screenshot is `outputs/notion_template_location_dev_20260928.png`. The
live Notion comparison database still showed only its four named rows after a
refresh; the actual template editor remains unverified because activating the
menu entry through the connected browser did not open it reliably.

Notion's database-property help also documents reordering multiple files with
the left-side drag handle. The local Files property now supports this in the
owned table cell, owned item peek, and template default editor. Drag-and-drop
and Alt+arrow-key moves persist the array order through the canonical cell or
template save path. The table's existing upload and remove actions now also
write owned database cells canonically. An isolated browser journey applied a
reordered template to a new row, dragged a table file to the front, moved a
peek file with the keyboard, uploaded and removed a table file, and verified
the saved order after reload (1 test, 76.55s). The complete four-test template
browser suite then passed in 205.28s. Its first attempt found an unrelated
test timing race between the initial table sync and a direct template command;
the test now waits for the table sync before reading its expected sequence.
A 390px capture is
`outputs/notion_template_files_reorder_mobile_20260928.png`. The live Notion
database was not edited for this comparison; the observed behavior comes from
the official help at https://www.notion.com/en-gb/help/database-properties.
