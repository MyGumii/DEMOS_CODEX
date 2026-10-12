# Database activity in the sidebar menu — R5

Status: direct comparison, the offline 2421 schema, commit-time activity
recording, authorized reads, and the sidebar menu footer are implemented in
isolated development. The collaborative database copy feature remains disabled
in production. No user catalog has been migrated to 2421.

## Direct comparison

On 2026-09-25, the authenticated Notion comparison database's sidebar `…` menu
showed an editor name followed by `최종 편집`, then a full local date and time.
The same menu contained the existing favorite, link, duplicate, rename, move,
trash, open, and help actions. The menu was closed and the original comparison
page restored without changing Notion data. Earlier menu observations are in
`outputs/Notion_UI_사이드바_인라인DB_20260925.md`.

## Pre-2421 evidence

`workspace_collaboration_databases.updated_at` advances on title changes, but
does not represent cell, row, property, view, template, or item-body edits.
Their own canonical rows have separate timestamps. The audit/event tables store
actors and document sequences, but typed cell/property events do not carry a
stable `database_id`; resolving a historical event through a row/property that
was subsequently deleted is insufficient. Page `updated_at` includes unrelated
edits. None is a truthful DB-level last-editor projection for the menu.

## Required contract

- Add a forward-only, checksumed 2421 database-activity schema. Store one row
  per owned canonical database with editor subject, edit time, and document
  sequence. Historical rows with unverifiable editor identity remain unknown;
  never substitute the parent page actor/time.
- Update this row in the same transaction as every DB-affecting mutation:
  schema, cell/batch, row body/appearance, copy/create, restore, and any import
  or move that changes the DB's content. Replayed operation IDs must return the
  original receipt without advancing activity. Unrelated page edits must leave
  DB activity unchanged.
- Preserve actor identity and monotonic document-sequence ordering under
  concurrent writes. Ensure rollback, audit, and outbox are atomic with the
  activity update. Copy initializes the copied DB's own activity; it does not
  inherit the source's editor as the latest editor.
- Expose authorized activity with the canonical DB read response. The sidebar
  outline may carry only the minimal editor/time metadata. The menu renders the
  actor and localized time only when the record is known; old/unknown records
  remain unlabelled rather than displaying a false value. Role or membership
  changes must not expose an inaccessible DB's activity.
- Verify title, cell, body, appearance, schema, copy, response replay, restore,
  rollback, concurrent edits, unrelated page edits, read-only roles, reload,
  and a representative real browser menu. Keep legacy personal storage behind
  its existing adapter until its independent activity contract is built.

Do not run a user catalog migration or enable production feature flags during
development. Use only isolated synthetic catalogs for the 2421 migration and
runtime validation.

`notion_workspace_database_activity_migration.py` adds an empty historical
activity table and transaction-local event triggers for schema, surface,
typed-cell, property-rename, and owned-database-copy commits. Template-created
databases, page-copy, and history-restore paths record their proven DB changes
within the same transaction. Runtime marker gates accept 2421, and authorized DB responses
expose `last_edit` only for that schema. The sidebar menu displays the editor
and local time when a verified record exists; it does not use the parent page
timestamp as a fallback.

On 2026-09-25, synthetic migration/runtime tests passed, including
wrong-plan/fault rollback, historical unknown state, schema, cell/batch,
row-body/appearance, template creation, create/copy, restore, replay,
unrelated page edits, permission revocation, and two competing Windows
processes that preserved the winning editor. One isolated authenticated Chrome
test passed for the menu during live remote refresh, after reload, and after
an unrelated paragraph edit while the menu remained open. No live catalog or
production flag was changed. Existing owned-DB copy browser modes (2) and
collaborative DB side peek browser behavior (1) passed after the live refresh
change. The current canonical runtime writer inventory
found no other import or move path that changes DB content; legacy cutover
imports precede 2421 and retain unknown history. The full 2421 regression
set and rollout gates remain pending. Do not deploy this schema or enable its
feature flag on this evidence alone.

The isolated coordinated backup/restore path now accepts the exact 2421 marker
as well as its prior 2410 baseline. A synthetic 2410→2421 catalog with a real
database activity row completed generation, verification, new-root restore,
canonical digest comparison, and foreign-key checks. A corrupted 2421 marker
was rejected. This closes the immediate 2421 restore refusal, not the complete
target-host backup/recovery or production rollout gate.
The focused 2421 restore test, eight direct search-authority tests, and the
existing 2410 new-root restore test passed. The frozen F0-P4 manifest verifier
still fails on route-contract and source-hash drift in the evolving worktree;
its gate is not claimed as passed by these functional checks.

On 2026-09-28, the full-page database top bar began showing only verified
`last_edit` activity from its canonical database snapshot. An older database
without an activity record stays unlabelled; it never borrows its parent page's
edit time. The activity refreshes after a remote schema change, and an unrelated
parent paragraph edit does not change the displayed database editor. The label
does not open the parent page history drawer. One isolated authenticated Chrome test
covered the known editor, reload, remote change, and 390-pixel layout. This
does not implement independent database sharing or full database history.

Later on 2026-09-28, the authenticated comparison database's edit-time control
was opened directly. It showed a right-side `업데이트` panel with an
`애널리틱스` tab, actor/time, database item creation/deletion, and linked-view
edit entries. It is an update feed, not the parent page's version-restore
drawer. The development UI now opens a separate database update panel from the
edit-time control. An authorized, content-free read endpoint selects the
current owned database's direct canonical events, including current-row cell
edits and property renames, without mixing unrelated parent-page edits. It
denies archived databases and paginates by document sequence. One isolated
authority test and one 390px authenticated Chrome journey passed; the ordinary
page history browser regression also passed. Historical events whose database
identity can no longer be resolved, linked-view updates on another document,
analytics, and version viewing are not yet represented by this feed.
The archived-database HTTP lifecycle regression also confirmed that the feed
is readable before archive, denied while archived, and readable again after
restore.
Events with a surviving row identity now carry only that row ID in addition to
their content-free summary. The client resolves its current title from the
authorized database projection and opens that exact row in the side view;
missing/deleted rows remain unlinked. A same-parent second database edit is
excluded by the focused authority regression, and the 390px Chrome journey
opened an item through the update link without changing the source document.
The 2423 row-trash regression confirms that an archived row's activity entry
remains visible as a summary without a row ID, while restoration makes that
row linkable again.

On 2026-09-28, the comparison database's top-right `작업` menu was also opened
directly. Its `업데이트와 애널리틱스` action opens the same right-side panel as
the edit-time control. The development menu now enables that action for the
active collaborative full-page database and opens the existing authorized
update feed. The panel uses the observed 384px desktop width, begins below the
45px top bar, and omits an extra heading and help line that Notion does not
show. An isolated 390px Chrome journey verified the menu route, feed content,
viewport bounds, Escape close, and focus return; the same journey rechecked
the edit-time route. Notion's analytics tab showed a 28-day view-count chart,
viewer list, and a personal view-history setting. The development app does not
collect equivalent page-view data, so that tab remains disabled. This work
does not imply analytics, per-event version viewing, or independent database
sharing.
