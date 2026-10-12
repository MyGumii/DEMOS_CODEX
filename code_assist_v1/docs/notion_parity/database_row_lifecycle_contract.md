# Database item Trash — staged R5 contract (2026-09-26)

Direct signed-in Notion comparison on the disposable `Codex UI 비교 — 연결된
DB 원본 2026-09-16` page showed that selecting two table rows exposes a
floating count button, a direct Trash control, and an overflow menu with
icon/property edit, copy all links, duplicate, move, and move to Trash. The
current local selector implements the count/clear behavior and copy-all-links
for rows with stable links. A staged Trash icon and menu action appear only
for canonical rows when the independent capability is enabled.

The existing canonical `database.row.delete` command in
`notion_collaboration/database_features.py` deletes the row and cascades its
cells/body/appearance; it also retires row comments. It cannot implement a
recoverable Notion Trash action. The 2422 database-level Trash membership
retains all database rows and is not a per-row state.

The offline 2422→2423 forward migration adds
`workspace_collaboration_database_row_trash_state`. Membership points to the
original `(database_id,row_id)` through a restrictive foreign key. The row,
cells, body, appearance, and comment identities remain in canonical tables;
the membership records archive actor/time/operation and the row version plus
the workspace/document at archive time. Current ownership must be derived
from the database at read/restore time, because the database can later move.
The index supports database-scoped Trash listing. The application facade now
accepts the exact 2423 marker in isolation; no live catalog has been migrated.

The staged 2423 read helper now identifies archived row IDs only under the
exact 2423 marker. The canonical editing snapshot omits those rows from normal
database content while retaining every canonical row/cell on disk. Existing
row surface commands and typed cell writes reject an archived row before
mutating it; the destructive `database.row.delete` path cannot serve as Trash.
The 2422 whole-database Trash and edit-activity readers recognize 2423 so an
existing database-level Trash state is not accidentally displayed after an
offline upgrade. The whole-database Trash service now accepts exact 2423 under
its existing owner rule; an isolated nested lifecycle test archives a row,
archives and restores its database, then restores that same row and file.
The application facade can reopen exact 2423 while the row Trash capability
remains disabled by default.

A staged service now inserts/removes row membership in one SQLite transaction
under current `edit_content` permission, page unlock, document/database/row
version fences, and a caller operation ID. It increments the row and document
versions, recomputes the visible content hash, writes activity plus the audit,
outbox, and replay receipt, and retains the same row/cells/body/appearance.
Injected failures roll back the entire operation. Replay checks current access
and returns the original result. The service resolves the row's current
database/document binding rather than treating its archive location as owner.
It remains behind the disabled row Trash capability in normal HTTP traffic.
An explicit `edit_content` Trash reader returns the original row ID, current
version, cells, body, appearance, and archive metadata under its current
database/document grant. A read-only receipt lookup recovers the exact result
of a lost archive or restore response; the HTTP routes remain capability gated.

Normal relation cells and rollup counts exclude archived target rows. Row
comment threads and inbox notifications are hidden while the row is archived,
then visible again after restore; direct item-comment access is denied.
Search reminder indexing also excludes archived row date cells when rebuilding
the projection. The isolated 2423 service test now observes a date reminder
before archive, none immediately after archive, and the same reminder after
restore through the normal reminder reader. CSV export similarly omits the
archived row's date value and includes it again after restore.

The file visibility probe now includes archived-row cell/body/appearance
references. A file used only by an archived row is hidden from file reads and
listing, while an active block or page icon sharing it keeps access. Deletion
of a retained file is rejected even if an active block also references it.
Page-copy preparation removes files referenced only by row or whole-database
Trash after live block replay and before blob verification; shared active
files remain in the copy source. The page-copy service accepts exact 2423
under the existing owner rule while its UI capability remains disabled.
An isolated 2423 copy omits the archived row and its exclusive attachment
but retains files shared by active content; an isolated 2422 copy omits a
whole-database Trash and its exclusive files while keeping an active file.

The synthetic migration test checks exact-plan refusal, injected schema
failure rollback, source row/cell preservation, empty initial Trash, target
schema fingerprint, foreign-key/integrity checks, orphan rejection, and
prevention of a direct delete while a row is a Trash member. It also verifies
normal snapshot visibility, restore visibility, and row/cell mutation denial
against a synthetic member. The focused row and existing database Trash tests
passed together (4 tests, 75.80 seconds). The new service test covers injected
archive/restore failure, retry/replay, operation-ID collision, content-hash and
body/appearance preservation, relation/rollup filtering, comments/inbox, and
restored visibility. It also confirms an `edit_content` actor can archive the
row and the owner can restore the same identity. Existing row-comment and
search-preview regressions passed (17 tests, 226.74 seconds), and the two
existing date-reminder tests passed (22.65 seconds). A separate broad regression still has one
older `test_database_row_content.py` capability expectation that contradicts
the existing `page_copy._owner` marker list; it is not caused by 2423 changes.
The staged row file test passed with exclusive, active-block-shared, and
page-icon-shared files (24.57 seconds); existing whole-database Trash file
tests passed again (3 tests, 59.04 seconds) and page-copy source/remap tests
passed (13 tests, 80.32 seconds).
The 2423 backup observer accepts the exact marker and checks an archived row's
event continuity. An isolated coordinated backup now restores a 2423 catalog
to a new root, passes the ordinary permission/search/file smoke probe,
reopens through the ordinary application facade, keeps the archived row and its
file hidden, and restores that same row with the original attachment bytes.
An exact row-title search finds the page before archive, stops finding it
while archived (including after backup restore), and finds it again after row
restore. This covers ordinary search projection invalidation across the
backup boundary.
The same isolated path checks database HTML and CSV export: the row title is
present before archive, absent during Trash, and present again after restore.
The passive search preview also returns no archived row.
The 2422 and 2423 coordinated database-Trash backup tests passed together
(2 tests, 52.84 seconds). A later runtime gate test and the strengthened
coordinated restore test reopen exact 2423 through the application facade.

Before enabling the capability, normal database and linked views, item detail,
other export forms, other file forms, and every row mutation must consistently
hide or reject archived rows. The HTTP transport remains disabled by its
independent capability, and UI controls remain to build. Cross-workspace
database move, concurrent tabs, lost
responses, and browser behavior need isolated verification.
The move file planner now reads archived rows directly from canonical tables
without adding them to the visible editing snapshot. It includes their Files
cells, body, and appearance in the source-to-destination file remap. For an
archived row with body or appearance attachments, the internal file remap
checks the Trash membership and current row version, then leaves that row
version unchanged so `archived_row_version + 1` remains a valid restore fence.
The move service accepts the exact 2423 marker under its existing workspace
owner rule, while the row Trash UI capability remains disabled. An isolated
transaction test verifies the copied file, remapped cell/body/icon, source
retirement, and unchanged restore fence before rolling back the trial copy.
A separate full-service 2423 test verifies owner-only access, rollback after
the domain phase, one move effect on replay, lost-response receipt, preserved
row and comment identities, hidden destination attachment and comment, and
restoration from the destination document with the remapped attachment bytes
and original comment visible again.
The source-file sharing scan also counts retained references in other archived
rows and whole-database Trash memberships on the source page. It excludes the
database being moved so that a file used only by that database can still be
retired. A synthetic two-database test moves the first database while the
second holds the same source file through either row Trash or whole-database
Trash. In both cases the shared original file stays active and its bytes are
readable after the remaining source item is restored.
Cross-workspace moves and production UI exposure remain unverified.
Production schema migration and feature switch activation remain out of scope
without explicit authorization.

The row lifecycle now has staged HTTP routes for archive, restore, exact
lost-response receipt, and authorized Trash listing. Mutations require the
document, database, and row fences plus a caller operation ID; unknown
receipt lookups return `state: unknown`. The independent
`collaborative_database_row_trash` capability remains false by default, and
the application runtime accepts the exact 2423 marker. A synthetic 2423
HTTP test covers the disabled gate, strict request shape, one archive/restore
effect under replay, receipt lookup, listing, outsider denial, and no-store
responses. It and the existing whole-database Trash HTTP tests passed
together (3 tests, 41.72 seconds). The UI capability remains disabled.
Relation-cell validation also rejects a new reference to an archived target
row. An isolated service test checks that the same row ID is rejected during
Trash and accepted again after restore; the row lifecycle and existing
database-feature suites passed together (12 tests, 150.04 seconds).
The ordinary application store now recognizes the exact 2423 marker and
retains the earlier page, database, and navigation capabilities without
activating row Trash. A synthetic reopen test archives a row, reopens the
store, observes it hidden in normal database reads and present in explicit
Trash listing, then restores the same row (1 test, 23.80 seconds). The
coordinated backup test now reopens both source and restored 2423 catalogs
through that store and verifies search, HTML/CSV export, file hiding, and
restore (1 test, 30.60 seconds). This does not activate the feature for live
user data or prove the remaining UI and browser behavior.

The selected-row toolbar now has a Trash icon beside its count and an overflow
Trash item. Both require every selected row to be a current canonical row and
the application-level row Trash capability. The UI submits each selected row
through its exact HTTP route with fresh document/database/row fences, and the
shared durable browser controller stores each row operation under a distinct
key before submitting it. Lost responses use the exact receipt route; another
tab cannot submit the same row operation concurrently. A recovery banner can
confirm, retry, or restore a row. Restore preparation filters the authorized
Trash listing by the exact row ID so it is not limited to the first page of
archived rows. The 2423 capability remains false by default. Controller and
selection-browser tests pass, but a full application browser journey with an
opted-in synthetic 2423 catalog is still required before activation.
An isolated browser test also exercises the actual application selection
callback with a controlled HTTP transport: two selected canonical rows submit
distinct operation IDs and current document fences in order, and the recovery
banner restores one exact row with its updated row version. It does not stand
in for a real server-backed browser journey.
The durable receipt also retains a bounded plain-text row title so recovery
entries remain distinguishable after reload. The browser test selects the
restore control by that title rather than relying on local-storage key order.
The browser action has now also been exercised through the real gated Flask
routes and isolated 2423 SQLite service. With the test-only capability
override, two selected rows archive in order and one restores while the other
remains hidden. A second journey commits an archive, loses only its HTTP
response, and confirms the committed receipt without a second archive POST
before restoring. Both journeys passed (2 tests, 76.06 seconds). The fixture
forwards browser requests to the in-process route handler; it does not enable
the production capability or replace a deployed multi-process browser check.

The exact 2423 runtime has also passed a two-process collision check. Two
workers replaying the same archive operation produce one committed effect and
one original receipt replay. Two distinct restore operations with the same
fences produce one restore and one document-sequence conflict; the Trash member
and operation tables remain unique and foreign keys are valid (1 test, 38.90
seconds). The full runtime file passed (3 tests, 72.60 seconds).

A saved canonical database block may retain a prior render cache in its
`props.rows`. Before this guard, the ordinary page reader and transfer bundle
could return a cached archived row despite the canonical database reader hiding
it. The shared page snapshot now masks archived row IDs from an owned or linked
block cache without mutating stored props. The isolated check verifies the
archived cache row is absent from page read, transfer bundle, and the embedded
Markdown bundle; an active row remains and restore reveals the original cache
row again. Search authority (8 tests) and HTML database export (15 tests)
regressions passed alongside the 3 runtime checks. Other read paths and the
full R1–R11 gate remain open; the production capability stays disabled.

An exact-2423 linked-view journey exposed an unrelated runtime marker gap:
ordinary native page creation rejected the new schema even though the app
advertised that existing capability. The page-creation marker now accepts
the exact row-Trash migration. A synthetic page with an authorized linked
database instance reads the source row before archive, hides it while the row
is in Trash, and shows the same row ID after restore. The 2423 runtime suite
passed 4 tests (98.60 seconds); existing native page creation passed 10 tests
(128.75 seconds) and linked database copy/read passed 7 tests (106.02 seconds).
This verifies the isolated read path, not production activation or all linked
view interactions.

A capability-to-runtime audit of exact 2423 found two more additive marker
gaps: page archive/restore and native team placement were advertised but their
service readers rejected that schema. Both now admit only the exact 2423
fingerprint in addition to their earlier accepted markers. In an isolated
catalog, archiving and restoring the containing page leaves its separately
archived database row in Trash; the row reappears only after its own restore.
The same catalog can create a team-root page and resolve its placement. The
expanded 2423 runtime file passed 6 tests (131.48 seconds), and the prior
page archive and native team suites passed 8 (104.07 seconds) and 9 (139.35
seconds) tests respectively. Database copy remains independently gated and
is not promoted by these marker changes.

The separately gated owned-database copy service now accepts the exact 2423
schema as well. An isolated source with one active and one archived row copies
only the active row; the source Trash membership stays with the original
database, and later source-row restore does not add that row to the copy.
The new test also confirms the copied database has no row-Trash membership.
The expanded 2423 runtime suite passed 7 tests (145.06 seconds), the existing
owned-database copy suite passed 8 tests (132.23 seconds), and its HTTP suite
passed 3 tests (51.33 seconds). The independent copy UI/API capability remains
false; this is service compatibility and isolation evidence, not rollout.
