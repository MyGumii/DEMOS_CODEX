# Database row ↔ ordinary page placement

## Direct Notion evidence (2026-09-26)

In the signed-in comparison workspace, moving a database row to an ordinary
page removed it from its source table and created a child-page entry under the
chosen page. The moved page kept its URL/page ID, title, icon and body. A date
property set to 2026-09-15 was not shown on that ordinary page. Moving the
same page back to the original database displayed the original date value
again. The rich comparison row is back in the source database. The first
title-only comparison page remains under the ordinary destination. This
observation proves a reversible placement change and hidden property
retention for this path. A later direct comparison of a move into a second
database is recorded below. Date-reminder behavior was compared later as
recorded below; attachment behavior remains unobserved in the live comparison.

## Direct relation-property evidence (2026-09-27)

Dedicated test row `UI-PARITY-RELATION-SOURCE-20260927` in the source database
had a one-way relation to `UI-PARITY-RELATION-TARGET-20260927` in the separate
`UI-PARITY-DB-TARGET-20260927` database. Moving the source row to the ordinary
`Codex UI 비교 — 슬래시·메뉴 2026-09-16` page hid the relation; returning it to
the original database displayed the same related row. A direct move of that
source row into the target database hid the relation again, and moving it back
to the original database restored the same relation value. The source row is
back in its original database. This establishes that Notion retains source
relation metadata across both page and different-database placements even
when that metadata is not visible at the destination. The local service now
retains an unmatched one-way relation cell under its original property ID,
filters it from the destination database and Trash read models, and exposes
it again when the row returns. Isolated direct DB→DB→DB and
DB→ordinary page→different DB→original DB service tests passed. A browser
return with the relation-bearing row also passed through two real HTTP server
processes and lost-response receipt recovery: the destination read model hid
the relation while the canonical cell retained its related row ID.
The screenshot is `outputs/Notion_관계형_원본복귀_20260927.png` in the task
workspace.

The same dedicated source relation was temporarily made two-way with inverse
property `UI-PARITY-RELATION-BACK-20260927` on the target database. The target
row immediately showed the source row. Moving the source row into the target
database made the inverse value empty after a full reload; returning the
source row to its original database restored that inverse value. A separate
DB→ordinary page→original DB roundtrip showed the same empty-then-restored
inverse. The source row is back in the original database; the temporary
two-way setting was disabled and its inverse property disappeared, leaving
the original one-way relation intact. Screenshots are
`outputs/Notion_양방향관계_복귀_20260927.png`,
`outputs/Notion_양방향관계_일반페이지복귀_20260927.png` and
`outputs/Notion_양방향관계_비교후원복_20260927.png`.

The local move services now preserve inbound relation cells instead of
rejecting the move. Database read models show only related rows currently
placed in the configured target database, while canonical relation IDs remain
unchanged for restoration. Setting a new relation to a row currently placed
as an ordinary page is rejected. Isolated DB→DB→original and DB→ordinary
page→original inbound-relation tests passed, plus a two-process browser return
with both outbound and inbound cells and lost-response recovery. Existing
relation/formula/rollup and outbound move regressions passed. This covers
inbound values during moves; creating and editing reciprocal properties as a
fully synchronized two-way relation was still pending at this stage. The
later backend implementation is described in `database_relation_pair_contract.md`.

On 2026-09-27, a further direct Notion check enabled the inverse property on
the dedicated relation source/target rows. The target immediately displayed
the source row. Removing that target-side value made the source-side relation
empty after a full reload; reconnecting from the target restored the source
value after reload. Disabling two-way relation then removed the inverse
property while retaining the original source-side link. This establishes that
both sides must be editable and kept in sync, not merely rendered as a
read-only inverse. The comparison pages were restored to the original one-way
configuration; `outputs/Notion_양방향편집후원복_20260927.png` records the final
target page without the temporary inverse property.

The local 2424 cell command now rejects writes to a row while that row is
placed as an ordinary page, whether sent as one typed cell command or through
an atomic cell batch. Its canonical cells remain intact for database return.
The inbound-relation page-return regression and the existing relation/formula
round trip passed in the isolated catalog. The later paired relation backend
work is recorded in `database_relation_pair_contract.md`; its user-facing UI
and browser validation remain unfinished.

## Direct date-reminder evidence (2026-09-27)

The same dedicated source row received an all-day date of 2027-01-15 and a
"1 day before" reminder. Moving it directly to the separate database reused
the compatible `날짜 1` date column; its date picker still displayed the same
reminder. A full browser reload preserved both values. Moving the row back to
the original database displayed the date and reminder in its original `날짜`
column, alongside the retained relation value. The test date and reminder
were then removed, leaving the source row in its original database with an
empty date. This verifies visible reminder metadata across DB→DB moves.
The reminder was then added again and the same row moved to an ordinary page.
After a full reload, that page displayed no database properties. Moving it
back to the original database restored the date and the same "1 day before"
reminder in its date picker. The test date and reminder were removed again;
the original row's date is empty and its relation is intact. These probes do
not establish whether a notification fires while the row is an ordinary page.
Screenshots are saved as `outputs/Notion_날짜알림_비교후복원_20260927.png`
and `outputs/Notion_날짜알림_일반페이지복귀_20260927.png` in the task workspace.

## Local target contract

1. The row/page ID is one stable identity before, during and after a move.
   Existing links must resolve to the current placement after permission
   checks. A move cannot create a new unrelated page ID.
2. An ordinary-page placement removes the row from every source database
   view, linked view, search result and export, and adds one child-page entry
   at the chosen destination. A database placement does the inverse. These
   projections must derive from one canonical placement state so stale block
   render caches cannot resurrect the old location.
3. All typed property cells, their schema identity and the row version remain
   canonical while properties are hidden in an ordinary-page view. On return
   to the original database, values appear unchanged. A move into a different
   database requires explicit property mapping; unknown values cannot be
   silently dropped. The visible page title is the title cell while attached
   to a database and stays synchronized with any ordinary-page title edit.
4. Body blocks, icon, cover, comments and attachments remain available on
   the same page. Cross-document files need destination-owned file identities
   that preserve bytes and provenance, as in the existing DB-to-DB move.
   Comment anchors must follow the page and still be valid after return.
5. A selected-row move checks source and destination edit rights, active
   state, document sequences, security epochs and row versions. All selected
   rows, source/destination page changes, activity, durable events and one
   replayable receipt commit atomically. An uncertain response replays the
   same operation ID from the encrypted browser journal.
6. The user-facing `옮기기` picker may select the ordinary page itself as well
   as a database nested below it. The ordinary-page route opens without
   database property rows; returning to a database restores them. The
   capability remains disabled by default until isolated service, HTTP,
   browser and migration gates pass.

## Implementation dependency map

The current 2423 schema has separate `workspace_collaboration_database_rows`
and `workspace_collaboration_documents` tables. Moving the row by deleting it
would cascade its cells/body/appearance; merely creating a native page would
lose the date observed above. The forward storage change therefore needs an
explicit placement record keyed by row ID, with the row and cells retained as
canonical data. Database readers must exclude ordinary-page placements, and
the page/navigation reader must project that same row ID under its chosen
parent. A page edit must write to the canonical row body/appearance/title or
perform an explicitly reversible transfer; two independently editable copies
would diverge. The existing DB-to-DB move service supplies its permission,
fence, attachment, comment, event and replay patterns. The page creation/move
services supply child-link/Yjs projection patterns. The old-URL resolver must
return either a current database location or an ordinary-page location.

The implementation must cover these checks with isolated 2423/forward-schema
fixtures, never a live catalog:

- A date-bearing row moves to an ordinary page and back with the same ID,
  date, body, icon and comments; source/child navigation and every read model
  agree on the placement.
- Multiple selected rows move in one transaction. A forced failure leaves all
  rows, pages, links, files and receipts unchanged; replay returns one result.
- A stale row, source/destination page, security epoch or inaccessible/trashed
  destination cannot commit. Another user cannot discover the relocated page
  through the old URL.
- A cross-document attachment still opens by its destination-owned file ID,
  and source references remain valid. Relational and reminder data cannot be
  orphaned or discarded.
- A real browser move, full reload, lost-response recovery, ordinary-page
  edit and return to the original DB reproduce the directly observed Notion
  behavior before the production capability can be considered for rollout.

The source→ordinary-page service, ordinary-page old-link resolver and
ordinary-page destination picker are implemented behind a default-off
capability. The original-database return service and return picker also exist
behind a separate default-off capability. The browser has exercised a normal
return and a lost-response return recovered after a full navigation, preserving
the same row ID and date. A second service-level roundtrip now reactivates the
same page identity with a fresh CRDT generation. An isolated HTTP browser test
also repeats the selection-menu move and original-database return, then checks
the same page/row ID and preserved date. On 2026-09-27 the signed-in Notion
comparison row `UI-PARITY-ROW-TO-PAGE-RICH-20260926` was moved from its source
database to the ordinary `Codex UI 비교 — 슬래시·메뉴 2026-09-16` page, then to the
separate `UI-PARITY-DB-TARGET-20260927` database. The same page ID, body and
2026-09-15 date survived; the existing destination `날짜 1` date column was
reused while its text `날짜` column stayed empty. Moving the row back to its
original database restored the value in the original `날짜` column. The page
move menu offered both databases directly. The local return service and picker
now support a different editable owned database: they preview compatible
property mappings, create missing safe columns atomically, rehome owned files,
preserve the row/page ID and body, and support receipt replay. An isolated
service roundtrip and actual HTTP browser picker flow passed. The production
capability flags remain off.

The isolated return flow also passed a committed-but-lost-response browser
case for a different database: a reload recovered the same operation receipt
without a second mutation, then opened the same row and preserved its date.
With explicit inherited page access, a content editor can preview and return
to that database once its matching columns exist; the same actor cannot select
or commit the destination when new columns would require schema edit access.
A spawned two-process replay of the same original-database return committed one
receipt and one row placement while preserving foreign-key integrity. This
was also verified through two separate HTTP server processes sharing an
isolated 2424 catalog: the second server replayed the first server's committed
response without adding another operation. A browser picker exercised both
normal and lost-response return into a second database in another document,
and into a second database in the source document. The browser fixture routes
HTTP into an in-process test handler for those cases. A further isolated
browser test now routes a committed first POST into one HTTP server process,
delivers a synthetic lost response to the browser, reloads, and routes the
replay POST into a second server process. Both original-DB and different-DB
destinations pass with one stored operation and preserved date. The full
production host, gateway and multi-user topology remain open.

## Storage foundation added 2026-09-26

An offline 2423→2424 forward migration now adds an exact-fingerprint placement
table keyed by the same database-row/page-document ID. Its foreign keys prevent
an active placement from outliving either canonical identity. The existing row
and typed cells remain in place, so changing `placement` from `page` back to
`database` exposes the original date value without recreating it. The
database snapshot and stale `props.rows` render cache omit page-placed rows;
normal row commands reject them. The 2424 runtime recognizes the inherited
2423 capabilities, including row Trash hiding, while the new move capability
remains off. An isolated migration/runtime test passed for same ID, date
preservation, stale-cache filtering, Trash separation and re-opened reads;
two existing DB-to-DB move regressions passed.

The migration has not been applied to a user catalog. The test's direct
placement insert is a synthetic storage probe, not a user-facing move.

## Source row → ordinary page service added 2026-09-26

The default-off `database.rows.move_to_page` command accepts 1–100 selected
owned rows, verifies source and parent edit access and exact sequence/epoch/row
fences, and commits one replayable transaction. It keeps typed cells under the
same row ID, creates same-ID native child pages with body/icon/cover, rehomes
row comments and notifications, copies document-owned attachments when needed,
and writes the parent child links and live document update. Hidden rows are
excluded from source DB reads. Tests verify two-row rollback/replay, a retained
2026-09-15 date, comments and body, cross-parent files and sequences, HTTP
capability gating/replay, and old-link resolution to the ordinary page. The
JavaScript route test verifies redirect without database query parameters.

The ordinary-page destination appears under an expanded page in the selected
row move picker. Its encrypted request journal validates the exact operation
ID, page/row identities and both document fences, and can recover a lost
response after reload. Actual browser tests covered normal placement, reload
recovery and old-link navigation; server, controller and URL tests also passed.

## Ordinary page → original database service added 2026-09-27

The default-off `return-to-database` service checks page, parent, original DB
and row permissions, sequences, epochs and version, then prepares current page
text and the parent-link removal outside its transaction. It updates the
hidden title cell and row body/appearance from page edits, copies page-owned
attachments to the original DB document, returns comments and notifications
to the row, reveals the original typed cells, removes the child-page link and
archives the page shell in one replayable transaction. Isolated tests passed
for an edited title/body with retained date, forced-failure rollback, replay,
cross-parent attachment/comment return and the default-off HTTP gate.

The return service initially accepted only the original database; the later
different-database extension is described above. The page shell remains as an
archived same-ID record required by the placement table.
The next row→page move reuses that shell, replaces its body with the current
canonical row body, advances revision/sequence/access epoch, and retires its
old CRDT generation before opening a new one. A two-roundtrip isolated test
passes with a database-side title/body edit between trips, a live title from
the first generation, forced-failure rollback, retained date and same ID.
An isolated browser test performs the second move and return through the UI,
checks that the ordinary page is reachable under its original ID, and verifies
the retained date after the second return. It uses a synthetic workspace and
does not establish a deployed multi-process browser result. The generic page
mover now updates
the placement-parent record when an ordinary row-page changes parent; an
isolated move-then-return test passed. A page moved to the workspace root can
also return without a parent-link delta: the target contract uses zero parent
fences, the service checks page and original DB rights, and the return clears
any root team-location grant. Isolated HTTP tests cover this path. The browser
test now clicks `워크스페이스 최상위` in the actual move picker, verifies one
canonical move request and a root parent, then clicks the original database
return target. Normal and lost-response return both pass. The isolated fixture
must inject its collaborative navigation scope and restore the seeded source
page to the browser's page list because the fixture's `/pages` projection lists
only the newly placed row-pages. This verifies the screen-to-service path but
does not establish a deployed multi-process browser result. The page move
picker exposes the original database after a
fresh target read. The encrypted request journal supports both ordinary return
and lost-response replay after navigating to another page; the latter can be
confirmed even if that other page is not editable. Browser tests cover both
paths with isolated 2424 storage, while full deployed multi-process validation
is still open. No user catalog migration, production feature switch or live
user data was changed.

## Date-reminder transfer implementation (2026-09-27)

The direct DB→DB row mover and ordinary-page move/return now retain date
reminder metadata. The search reminder projection follows the active
placement: it uses the database document while the row is in a database and
the row's page document while it is an ordinary page. It rebuilds from the
canonical date cell and checks current document access when listing reminders,
so a move does not leave an inbox entry pointing at the old location. The
ordinary-page routing choice is a local inference; the direct Notion probe
proved metadata retention after return but did not observe whether a future
notification actually fires during ordinary-page placement.

Isolated 2424 tests passed for DB→ordinary page→original DB and DB→ordinary
page→different DB, checking identical stored date/reminder data and exactly
one inbox entry at each placement. Two browser cases clicked the selected-row
ordinary-page move and other-database return, including a lost return response
recovered after reload, then checked the same reminder route and retained
date. Five existing page-move, DB-move and search-reminder
regressions passed. These tests use synthetic catalogs; the runtime capability
switches remain off by default, and no production catalog was changed.

## Parent link insert retention continuation (2026-10-03)

`database_row_page_parent_insert_contract.md` adds proven post-frame parent inserts and independent complete body/title/selected-link validation for batch placement and return. Existing row/page identity, hidden cells, attachment/comment/relation and atomic receipt contracts remain. Exact final evidence is in `outputs/row-page-parent-rebase-20261003/phase-evidence.json` in the chat workspace.
