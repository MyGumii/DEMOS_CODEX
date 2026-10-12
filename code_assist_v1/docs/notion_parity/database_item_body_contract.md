# Canonical database item body foundation

This packet stores an item's block body with its existing canonical database row.
It does not create a second document, source binding, sidebar page, owner grant,
or title. The title remains the database's title cell. Full-page and side-peek
views address the same `(workspace, source document, database, row)` identity
and use the same body. The initial editor integration is described below.

## Schema and rollout

`notion_workspace_row_body_migration.py` is the forward-only 2417 → 2418 migration.
The single new table, `workspace_collaboration_database_row_bodies`, has one
optional JSON block array per row and a cascading row foreign key. It performs
no backfill. Existing rows, document hashes, revisions, memberships and source
bindings remain unchanged. Historical migrations are not modified.

Apply only through the existing offline plan/checksum/verified backup/quiesced
catalog protocol. Runtime code does not run DDL. This packet has been developed
against synthetic test catalogs; it does not authorize a production migration
or enable production flags.

Exact 2418 marker support is registered for the workspace facade, editing
transport, workspace image authority, member/guest navigation, native page
creation/archive and native team location. The latter must remain active so
creator identity cannot bypass a revoked team grant.

`database_row_content` on the workspace and `row_content` on a database expose
backend support. They do not claim the item-body UI is complete. Page copy stays
disabled and its service rejects 2418 until body remapping is implemented.

## Command and read contract

Use the existing authenticated database command endpoint:
`POST /api/notion/workspaces/{workspace}/pages/{source}/databases/commands`.

- `command_kind`: `database.row.content.set`.
- `database_id`: existing database belonging to the authorized source document.
- `target_id`: existing row belonging to that exact database.
- `payload`: exactly `{ "blocks": [...] }`; an empty array clears the body.
- Positive integer `expected_target_version`: the current row version.
- Existing expected document sequence, workspace/document epochs and caller
  operation ID remain required. A retry sends the original request unchanged.

The source document's current `edit_content` permission is required, including
for a previously committed retry. The command validates the row binding and
row/document versions, then writes the body, advances the row version and
document sequence, recomputes the document hash and commits the operation
receipt, event, audit and outbox in the same transaction. Cell values and cell
versions are not changed. Competing saves cannot silently overwrite each other.

The top-level block array uses the existing canonical block representation: unique IDs,
ordered positions, type, string content and JSON properties. Limit: 1000 blocks
at the top level and 1 MiB of normalized JSON, also subject to the existing 1 MiB command payload
limit. Invalid JSON numbers and non-text content are rejected. Block IDs are
local to the row body; they are not inserted into the source document's block
table. The storage contract alone does not resolve embedded file, page, database
or synced-block references or give access to their targets.

Existing authorized database reads return `row.body.blocks` after the first save.
Before the first save, `body` is absent and means an empty body. Keeping it absent
preserves old snapshot hashes across migration. The receipt returns the canonical
`body` and the next `target_version` and `document_sequence`.

## History and deletion boundary

Revision checkpoints include each saved row body. A body-only restore uses the
existing page restore transaction and advances current row versions; it never
rewinds versions. Restoring a checkpoint from before the first body save removes
the optional body record. Current authorization, document epochs and CRDT restore
generation handling remain in the existing restore contract.

Ordinary page saves on 2418 also retain the current canonical database and file
snapshot fields within the save transaction. Otherwise a title/body save would
omit the database item bodies from its new revision and content hash. This uses
a successor save-descriptor hook; earlier schemas retain their existing behavior.

The pre-existing page restorer does not restore database structures or cells.
On 2418, restore therefore requires the remaining canonical database snapshot to
match, ignoring only row body and row version. Changed rows, schema, cells, cell
versions, views or templates cause a history-integrity error and a complete
rollback. This intentionally prevents a success response for a partial restore;
full database history reconciliation remains required work.

Deleting a row removes its active body through the foreign key; immutable
checkpoint bodies remain readable. Restoring a deleted row through page history
is not implemented by this packet and is explicitly refused.

## Evidence and next integration

`tests/notion_collaboration/test_database_row_content.py` exercises forward
migration preservation/refusal/rollback, body round trip and restart, exact retry,
current grant revalidation, malformed input, CAS, transaction fault points,
body-only history restore, deleted-row refusal, HTTP routing, successor gates,
revoked native team creators and two competing Windows processes.

Actual Chrome inspection on 2026-09-22 confirmed the Notion reference item's
side-peek property/comment/body arrangement and its full-page transition. The
reference content was not modified. This is reference evidence, not evidence
that a new body editor is visible in the development app.

## Initial client integration (2026-09-22)

`notion_database_item_body.js` owns body state independently of source document
blocks and property cell drafts. It journals drafts and frozen requests through
the existing encrypted IndexedDB outbox before any HTTP write. The namespace
uses the original origin/actor/workspace and a SHA-256 of source/DB/row, bounded
below the outbox identifier limit. Logout cleanup therefore keeps its existing
actor/realm boundary. The journal is not a new canonical store.

A recovered request stays frozen until an explicit retry. Receipt validation
checks contract, status, operation, source workspace/document/database/row,
command, exact next row/document versions and normalized content. A failed
receipt retains the exact request. Edits made while a request is in flight
become a following draft/version. A stale base is never silently rebased onto a
remote body. Storage failures prevent unjournaled HTTP writes; retry and local
discard handle storage errors without losing the in-memory draft implicitly.

`notion_database_item_editor.js` reuses the main block definitions, split rules
and rich HTML sanitizer. It provides text/headings/lists/todos/toggles, code,
quotes/callouts, dividers and simple tables, nested toggle children, local
undo/redo, slash selection, Korean slash Escape preservation and scoped
shortcuts/clipboard handling. Other blocks are preserved but displayed as
non-editable placeholders. Their embedded resources are not resolved by this
initial editor. The following selection packet adds range drag and reordering
to this body surface through the main page's shared gesture implementation.

The shared item panel saves property drafts before body drafts and waits before
row changes or closing. Side/full layout switching preserves the same mounted
editor without forcing a save; even an uncertain recovered draft can change
layout. Full mode uses `/page/{source}?item_db={db}&item_row={row}&item_view=full`,
restored on reload and browser back/forward. It does not manufacture a row page
or grant. The underlying source canvas becomes inert while full mode is open.
Linked-instance bodies remain read-only; opening their full page navigates to
the authorized source item instead of bypassing linked-instance write checks.

Actual Chrome tests used two fresh synthetic 2418 catalogs: ordinary rich body
editing and a separate after-commit response-loss run. The latter retained a
pending draft across full-mode reload and resolved one identical operation after
retry. Canonical read-only comparison confirmed unchanged cells, other rows,
memberships and source bindings. Client regression results and browser evidence
are in the `item-body-ui-*` packet. Production migration/flags were not changed.

Remaining implementation: body-specific comments, icon/cover and view options,
embedded resource fidelity; full database history recovery, body-aware copy,
search/export/publication and embedded reference handling; final role/theme/
responsive testing and separately authorized production rollout. The original
R1–R11 and approved copy/synced-block scope remain active.

## Body selection and block movement (2026-09-22)

The full item pane and side pane accept a drag starting outside the writing
column. The existing blue marquee has no border; only body rows intersecting
the rectangle are selected. The item title and property controls are excluded.
Body headings, nested text, simple tables and dividers participate. Text and
control starts retain their normal behavior. Shift adds to the selection;
Escape restores the previous held selection, and a subsequent Escape clears
the released selection before the enclosing pane may close.

The gutter's six-dot handle opens block actions on click and moves the selected
group on drag. Pointer capture, movement threshold, current body/permission
checks, invalid descendant targets and pane-local scrolling prevent accidental
or stale moves. Release samples the final position without another scroll
frame. Escape, pointer cancellation, focus loss, disposal and document changes
remove gesture state and drop markers. Shared page selection now exposes host
geometry and disposal hooks while preserving its original default behavior.

`notion_database_item_blocks.js` implements immutable tree operations. Selecting
both a parent and its children duplicates, removes or moves each subtree once.
New duplicate IDs cover every descendant. Moves preserve subtree contents and
reject cycles. Keyboard movement stays among siblings. Group delete, duplicate,
undo/redo and plain-text copy/cut are scoped to this item. Structured clipboard
exchange with other pages and complete embedded-resource editing remain work.

The `item-body-selection-*` packet records 75 passing client checks and actual
Chrome full/side selection, single/group handle movement, menu click, saved
order after reload, delete/undo and Escape behavior. Runtime style records show
border 0px and the existing rgba(35,131,226,.1) background. The screenshot shows
released block selection; a held marquee screenshot and prolonged edge-scroll
capture remain unverified. A read-only canonical comparison confirms the same
nine block IDs/content/hierarchy, changed root order only, unchanged properties,
other rows, memberships and source bindings. No production rollout occurred.


## Embedded item resources (2026-09-23)

Owned item bodies now display and add image, file, audio, video, PDF, bookmark
and embed blocks through the slash menu. A 540px centered, viewport-clamped
picker exposes upload/link tabs, file paste, error/retry/cancel and Escape focus
return. Notion's Unsplash/GIPHY services are outside this local feature. Captions
remain independently editable without splitting the resource on ordinary edits.
Linked/read-only bodies retain playback/open links without mutation controls.

Uploads reuse the authorized file service and existing upload sequence witness;
the main document cursor is not advanced by a file-only result. Item properties
flush first, current row data refreshes after upload, then the body draft binds
the result only if scope/permission/block source still match. Late, cancelled,
replaced or disposed jobs cannot attach to another target. Caption edits during
upload survive. Existing files are not deleted by source replacement or cancel.

PDF preview verifies a successful application/pdf response, a 64 MiB byte bound
and %PDF- signature before creating an application/pdf object URL. This avoids
Chrome's blocked sandboxed plugin while preventing an arbitrary HTML response
from entering an unsandboxed frame. URLs are revoked on source removal/disposal.
External PDFs additionally depend on CORS; failure leaves a visible reason and
an ordinary open link. Arbitrary embed frames retain their sandbox and do not
receive allow-same-origin. No server policy or upload authority was weakened.

Actual Chrome on the named isolated fixture verified seven displays, caption
edit/save, bookmark replacement, PNG paste upload, slash image/link insertion,
reload preservation, 390px no-overflow and Escape focus. PDF contents were
visually confirmed, and audio/video metadata showed a one-second duration.
This is not a native file-dialog or actual audio playback proof. Automated
checks: seven existing test files, 70 checks passed, including resource safety,
late replies/cancellation and failed-picker retry visibility. Canonical audit:
source body, property cells, other rows, memberships and original five files
unchanged; exact expected item edits and one new PNG only, sequence 12 to 20.

Remaining: rich bookmark metadata, resource resize/layout controls, all remote
embed providers, native chooser, full role/theme matrices, structured clipboard,
item body copy/search/export/publication fidelity and original R1-R11/copy/synced
scope. Production migrations/flags/rollout remain separate and unperformed.


## Structured clipboard transfer (2026-09-23)

Basic text/headings/lists, closed nested blocks, simple tables, callouts and
resource references now transfer between item and main-page editors. A shared
bounded format retains rich HTML and descendants with fresh identities and
converts the editors' flat/tree representations. Actual item→item, item→main and
main→item/HTML-only fallback, cut/undo, reload and canonical preservation were
verified. See `block_clipboard_contract.md` for exact evidence and limits. This
closes the former plain-text-only item path, not the remaining DB/synced/file
independence, internal reference, external import and full styling scope.


## Named block colors and grouped callouts (2026-09-23)

Item blocks now render retained text/background colors through the same palette
as main-page blocks. A color submenu applies/reset colors to selected item IDs
through the existing draft/save/history path. Callout descendants share their
parent background while explicit child colors remain independent. Actual
light/dark, selection, undo/redo and reload checks plus canonical preservation
are documented in `block_style_contract.md`. Final menu CSS visual checking,
narrow viewport, all roles, and flat main-page nested semantics remain open.
