# Full-page database sharing: observed gap

On 2026-09-28, the authenticated Notion comparison database's top-right
`공유` control opened a database-specific page sharing popover. It contained
`공유` and `게시` tabs, an invite field and button, the current owner with an
access role, a `일반 사용 권한` selector, `고급`, and `링크 복사`. No sharing
setting was changed. The edit-time control separately opened an `업데이트`
side panel.

The development application's full-page database is currently an owned block
inside a parent page. Its canonical access checks resolve against that parent
document. The present top-right popover accurately names the inherited
access, summarizes the parent's grants, copies the database link, and leads
to **parent** sharing settings. It is not equivalent to Notion's independent
database share scope. Do not relabel parent grants as database grants.

[Notion's sharing guide](https://www.notion.com/help/sharing-and-permissions)
states that subpages inherit parent permissions by default, can have their own
permissions changed, and use the broadest applicable access. It also reserves
`Can edit content` for database pages: the grantee may edit database items and
property values without changing database structure, views, sorts, or filters.
This makes separate database access a functional requirement, not only a visual
difference.

The read-only development popover now follows the observed tab strip,
principal rows with avatars/roles, general-access section, and link action.
It explicitly says that the displayed access comes from the parent page;
`게시` is disabled because database publishing is not implemented. The parent
share settings action is available only for an active database. Isolated
authenticated Chrome checks passed for the active 390px and archived 1280px
surfaces, and both captures were inspected for viewport clipping. These
presentation changes do not create independent database grants.

Closing this gap requires a separate canonical database principal/grant scope,
authorization for database reads and writes plus item pages, search, files,
linked views and real-time tickets, atomic permission epochs/revocation, and a
forward-only migration. The remaining direct-comparison boundary is the exact
behavior of a database grant beneath a private parent and how linked views
inherit access; those cases need direct comparison before finalizing the role
model. Use isolated catalogs and accounts for development. Do not migrate a user catalog
or change live sharing while characterizing this behavior.

## 2026-09-29 implementation boundary

The read-only Notion comparison was repeated on the dedicated source table.
Its database URL opened a `공유`/`게시` popover with an invite field, current
owner role, general access and link copy. No permission was changed. The
[official permission guide](https://www.notion.com/help/sharing-and-permissions)
documents `Can edit content` and `Can create` as database-only levels and says
the broadest applicable grant wins. These are product facts; whether a direct
database grant beneath a private parent reveals the parent's other blocks is
an inference and must be verified before making that route available.

The frozen 2427→2428 offline migration now defines empty
`workspace_collaboration_database_access_state` and
`workspace_collaboration_database_access_grants` tables. The grant table has
no redundant workspace ID: the workspace must be derived through the
database's owning document, preventing a mismatched workspace identity in a
stored grant. An absent state row means epoch 1 and no separate grants.
The isolated image authority now accepts 2428 for transaction tests. The fresh
workspace facade, HTTP capability and endpoints still reject 2428, so this
schema is **staged only** and no user's catalog is migrated by this change.
The staged `database_access` resolver combines the existing parent role with
active database grants, checks archived ancestors and database Trash, and
returns separate workspace/document/database epochs. An isolated 2428 test
proved that a direct database viewer still cannot read the private parent,
while the owner keeps full access and archiving the parent suppresses the
direct grant. The fresh workspace facade still rejects a 2428 catalog; the
resolver and mutation service are exercised only through an existing isolated
authority.

The staged mutation service now sets and revokes direct database grants with
workspace, document and database epoch checks, one operation receipt, audit
and outbox event. Replaying the same operation returns its receipt; a stale
epoch, unauthorized actor or injected transaction failure leaves no partial
grant. Direct subject grants are revoked in the same transaction as workspace
membership removal. Page-grant removal keeps a guest active while they still
hold a database grant, and removing their last database grant retires that
guest. These boundaries passed isolated tests. No application route calls the
mutation service yet.

A staged single-database projection now resolves that database before reading
and returns only its database descriptor, role and access epochs. It does not
return the owning page or sibling database descriptors. Relation values use
the target database's own permission decision in the same SQLite snapshot;
an inaccessible target's relation config is masked. Isolated tests cover a
direct viewer beneath a private parent, an inaccessible sibling, archived rows
remaining hidden, and the relation config appearing after the sibling receives
a separate grant. The
projection has no HTTP route yet. Nested view/template configuration, item
files, editing, linked views and live tickets still need their own disclosure
and authorization audits before this can be activated.

The projection now also redacts settings derived from an inaccessible relation
target: the dependent rollup configuration, relation/rollup template defaults,
and filters, nested filter groups, sorts or grouping on those properties in
owned views. It leaves unrelated view settings intact. The relation cells are
empty and dependent rollup values are null while target access is absent.
Isolated tests exercise a private sibling's database, property and row IDs,
then grant and revoke target access to confirm that settings and values follow
the current decision without changing stored data. Other user-authored links
in row bodies/templates and the broader linked-view surface still need a
separate disclosure audit.

The staged write guard now checks the effective database role and current
workspace, owner-document and database access epochs in the same transaction.
An isolated database-only `edit_content` grant passes the content-write gate,
cannot pass the schema-write gate, and fails after any one of the three epochs
changes or its grant is revoked. Existing page-scoped cell commands still use
parent-page authorization; they must not be exposed to direct database
grantees.

A staged `change_cells` service now executes ordinary cell batches for a
database-only `edit_content` grantee. It derives the owner document internally,
checks all three access epochs and the document sequence in one transaction,
reuses the canonical cell validator, and commits the document hash, activity,
event, audit, outbox and replay receipt together. Its service receipt contains
the database ID, changed cell versions and new sequence, but no owner-document
ID or full-page content hash. Isolated tests cover a two-cell commit, injected
rollback, invalid second cell rolling back the first, exact replay, changed
retry rejection, private-parent non-disclosure and revoke-before-replay.
Unpaired relation edits require target database view access in the same
transaction. Paired edits additionally require target database content-edit
access before inverse cells can change; a denied inverse edit rolls back the
source cell as well. The service receipt omits reciprocal document IDs, while
internal effects still record them for authorized owner-document listeners.
The staged file-reference decision now accepts only active files referenced by
the database's live item cells, body, appearance or active templates. It does
not treat every file on the owner page as a database attachment. A database-only
editor may retain or reuse one of those files in a Files cell and may add a
validated external link; an unreferenced same-page file is denied. Archiving
the last referencing row removes the file from this database decision. Isolated
tests cover these cases. A staged file-content resolver applies this same
active-reference decision in one SQLite read snapshot before resolving the
blob path beneath the configured storage root. A database-only viewer can read
a referenced file's original bytes without receiving the owner-document ID;
an unreferenced same-page file and a file whose sole referring row is archived
are denied in isolated tests. This internal resolver is not an HTTP download:
a streaming endpoint must recheck authorization immediately before transfer,
including after asynchronous work or permission changes.

The frozen 2428→2429 offline migration adds an empty, immutable upload-session
to database binding with the database access epoch. Existing page sessions and
file metadata are unchanged. An isolated `begin_upload` service records the
session and binding in one transaction after database content-edit, three epoch,
document sequence, lock and capacity checks. Chunk, status, finalize and cancel
reuse the existing bounded file machinery while rechecking the bound database
at each step. Before attachment, a committed database-bound file is visible
only to its uploader through the bound database; once a live database item
references it, other database viewers may read it. An ordinary page upload
continues to require a live database reference. A staged deletion service only
accepts files originally uploaded into this database and refuses deletion while
any current page or database content still references the file. It updates the
file row and owner-document sequence atomically; shared content blobs remain
until no active file uses them. Isolated tests cover original bytes, same-page
sibling isolation, attach, cancel, deletion and replay, revocation before chunk
or finalize, and existing page uploads on 2429. This is still an internal
service, not an HTTP route. The owner-only database move now refuses pending
uploads and active, unreferenced database-bound files before making changes;
after attachment it copies and remaps the file with the database. An isolated
move test confirms the direct database grantee can read the remapped bytes at
the destination without gaining its parent page. This still leaves the UI for
resolving pending/unreferenced files, owning-block icon/cover references,
streaming rechecks, backup/restore and full live-event scoping before
activation. The fresh workspace facade still rejects 2428 and 2429; no user
catalog was migrated.

The owner-only same-page database copy accepts the isolated 2428/2429
successors. It creates a new database identity without copying direct grants:
a grantee can still open the original but cannot open the copy, while the
workspace owner can open both. This is tested at the staged service layer;
copy UI, cross-page variants and all-file combinations remain separate gates.

A staged linked-view read now checks the instance page and the referenced
source database independently in one SQLite snapshot after instance replay.
It rejects a mismatched stored source owner, returns only the database-scoped
projection and local views, and omits the source owner page identity. An
isolated integration test covers no source grant, independent source grant,
instance-page revocation and restoration, source grant revocation, and the
absence of parent-page content in the response. The same redaction used for
database views also removes inaccessible relation-derived property visibility,
property order and group order from local linked-view settings. The linked
view HTTP read now uses this service on 2428/2429 while older schemas keep
their parent-page reader. A grant-only reader can open either a legacy or
database-ID-only linked instance without receiving the hidden parent ID.
The in-page renderer accepts that response and does not offer a parent-page
open button when the parent is inaccessible. The linked cell HTTP endpoint
now accepts the scoped source access and instance witness on 2428/2429,
while older schemas keep their legacy request/response shape. The browser's
durable cell journal builds the corresponding request and verifies the
content-free scoped receipt before clearing it. Item property edits share
this cell path; item comments and remaining row actions still require their
own scope checks before direct grants are user-visible.

A staged linked-view cell writer now binds the current durable instance to the
database-scoped batch in the same write transaction. Its idempotency hash
includes the instance identity and witness; exact retries recheck both the
instance page's content-edit role and the source database's content-edit role
and access epochs. The direct database batch keeps its original request hash.
An isolated integration test confirms a grant-only source editor can save and
replay a linked cell without owner-page access, cannot start a new write when
the instance page is downgraded to view, and cannot replay after the instance
page grant is revoked. The scoped HTTP connection now covers a fresh write
through a database-ID-only block and an exact retry through a legacy block;
the browser journal also tests response-loss recovery and undo without a
parent-page ID. Row/comment/file surfaces and live events still need the
corresponding scope before database grants can be exposed.

The staged source picker now lists and resolves database IDs through the same
database grant. Direct database grantees can search the database title but
receive neither the inaccessible parent document ID nor its title. A parent
page title is searchable and returned only when that page is independently
visible. A revoked database grant disappears from both the list and exact
lookup, while a revoked linked-instance page grant does not hide an otherwise
authorized source from the picker. The HTTP picker and exact source lookup
now select this grant-scoped reader when the 2428 schema marker is installed;
older schemas keep their previous response shape. Isolated HTTP tests confirm
a DB-only grantee can search by database title without receiving the
inaccessible parent ID or title, cannot search by that parent title, and
loses list/lookup access on revoke. The new-page picker now passes only the
database ID when the parent is inaccessible. The 2428/2429 page creation
service resolves that source and checks its grant inside the page creation
transaction, stores a database-ID-only reference, and rechecks the grant on
exact replay. A database-only team member can create and read the new page
without owner-page access; after grant revocation, replay and linked read are
denied. Older schemas still require the source document ID. The in-page
linked block picker can now fetch the selected database's projection by its
ID without needing the parent page. For a database-only source it posts the
selected view to the scoped insert endpoint, keeps the original slash text or
blank until the committed block reaches the live editor, and reuses the same
operation after a lost response. An isolated Node picker test covers the
lost-response retry without a duplicate block. An actual browser journey for
this gated schema is still pending. Runtime capability activation remains
gated by the other privacy surfaces.

The staged insertion service now accepts a database ID, resolves its current
owner server-side, and inserts a linked block whose stored reference contains
only that database ID. The source grant and all three access epochs are checked
again in the destination page's typed-command transaction and on exact replay;
the destination page separately requires content-edit access. Staged linked
read and cell write accept this database-only reference and resolve its owner
at use time, while legacy two-ID references retain their existing behavior.
An isolated test confirms the stored block omits the parent ID, exact replay,
read through the new block, refusal to insert without a source grant, and
denial after source revocation. The staged linked HTTP reader, cell writer,
new-page template and generic renderer now understand this reference. The
scoped HTTP insert endpoint accepts a database ID plus the selected local
view settings and layout, verifies the destination page and source grant,
and returns the same receipt on retry; an isolated test confirms the stored
reference has no parent ID and source revocation denies replay. The in-page
picker's final commit has an isolated code-level test; actual browser
verification, item surfaces and realtime transport remain activation gates.
Legacy linked blocks still store two IDs in ordinary page snapshots. Those
snapshots must redact or replace the owner-document field for database-only
readers before staged grants become user-visible; the new insertion format
alone does not make older instance pages private-parent safe.
A staged instance-page reader now replays the current page outside SQLite and
checks its stamp, page role and every linked source grant in one read snapshot.
For an authorized source it projects either legacy or new links as a
database-ID-only reference, and removes inaccessible relation-derived local
view settings. For a denied source it replaces the linked block with an empty
unavailable placeholder. The stored legacy block remains unchanged. An
isolated test covers no source grant, direct source grant, source revocation,
both stored reference formats and the absence of the parent ID in all staged
page responses. Historical exports, other derived readers and raw CRDT
transports still need equivalent decisions before grants become visible.
The direct `read_document` path now applies the same linked-block projection
inside its own SQLite read transaction on 2428/2429. An inaccessible source
becomes an unavailable placeholder; a database-only grant receives a
database-ID-only reference; a reader authorized on the source page retains the
legacy pair so existing linked views keep working. The inaccessible parent
page ID is removed from document metadata as well. The collaborative page
payload additionally replays current blocks and refuses a page sequence
change between metadata and projected blocks. Isolated tests cover direct
document and full payload responses before/after source grant and revoke,
with the owner-visible legacy pair and parent metadata preserved. Earlier
schema versions retain their prior behavior; title, lock-generation and
linked-view regressions pass. History/search exports and raw collaborative
updates still need their own privacy decisions before HTTP activation.

An isolated database-update summary reader now authorizes through the direct
database grant in one SQLite snapshot rather than requiring the owner page.
It keeps the existing content-free event filtering and pagination but omits
the owner-document ID from its response. A direct grantee can see summaries
for the shared database, not a private sibling, and loses this access on
revoke; tests also confirm cell text never enters the summary. The existing
HTTP update route now selects this scoped reader when the 2428 schema marker
is installed, while older schemas keep the parent-page reader and legacy
response shape. HTTP tests cover a grant-only reader without the parent ID
or cell text, revocation, and the older-schema response. Raw Yjs
snapshots/updates, tickets and outbox delivery remain the larger realtime
activation gate.

The staged database-only file download endpoint now resolves an active file
through a live reference in the authorized database and sends bytes without
requiring the owner page. The in-page linked table and linked item property/body
renderers use this endpoint for database-ID-only sources. An isolated HTTP test
covers the attached bytes, an unrelated file, missing schema, legacy page-file
denial and grant revocation; Node tests cover generated table and item-body
URLs. Database-scoped upload HTTP now accepts the four-part source witness and
uses the immutable 2429 upload-session binding for status, chunks, finalize and
cancel. The linked item file picker reads a fresh database projection, uploads
through that route, refreshes the linked view and then saves the file cell.
Isolated HTTP tests cover upload replay, bytes, cancel and revoke; Node tests
cover database-scoped upload parameters and the linked item sequence. The
direct full-page database and body-upload flows have mocked browser coverage
but not a live 2428/2429 application run. Comments, exports and other privacy
surfaces still need their own authority checks before activation.

The activation gate is not just grant CRUD. It requires all of the following
to use one database-scoped authorization decision and its current epochs:

The current workspace page search checks each indexed page's page-level role
before returning it, so a database-only grant does not expose its private
parent in that page result. The staged database search and sidebar use separate
database identities and direct grants; they do not represent the owner page as
accessible. Keep the 2428/2429 runtime gate closed until the remaining
surfaces below are safe.

A staged `database-search` HTTP route now returns database-shaped title hits
using the current database grant in one read snapshot. Results contain the
database ID, title, role and edit time, but no owner page ID/title. A direct
grantee finds the shared database by its title, cannot find it by the private
parent title, and loses the hit immediately after revocation. Nonparticipants
get the same denial for an existing or nonexistent workspace. A `direct_only`
filter returns databases whose parent page the actor cannot read, so the
sidebar can show them under shared items without duplicating visible page
trees. The route does not yet activate in the user runtime. The search dialog
now has a capability-gated database result branch with a database-only preview
and direct URL. A
separate full-page screen reads the database projection without opening the
owner page, supports view switching, title search and a read-only item summary,
and returns home when a direct URL read is denied. An isolated browser with
mocked database responses covers search, preview, direct view, reload, back
navigation and denial. A separate exact-2429 synthetic-catalog browser check
uses a test-only adapter; normal 2428/2429 startup remains gated. The
direct full-page screen now offers basic typed cell editing when its database
projection grants content editing. Table cells edit in place, matching the
observed Notion title-cell interaction; item-summary properties use a dialog.
It writes through the database-ID-only cell
route, retains the exact request in the encrypted browser outbox before sending,
and retries that request after a lost response and reload. A confirmed write is
removed from the outbox before refreshing the screen, so a refresh failure is
reported as a display failure rather than retried as another write. The HTTP
authorization/revoke test and a mocked browser edit/reload/replay test pass.
The direct screen now ignores any stale page-level schema role when rendering
the shared database header and grid. It does not present owner-page-only
create/property controls without database-scoped handlers. Calendar and
timeline navigation and date-property selection use local view state, so a
database viewer can explore these layouts without writing the source view.
The staged browser covers this role separation, local date navigation and the
existing cell save/replay journey.
The direct item summary displays the already-authorized row body with the
existing block renderer. File blocks use the database-scoped file route, and
closing the item or leaving the database disposes that renderer. The mocked
browser covers body text, viewer read-only controls, database-scoped image URLs
and navigation cleanup.
A staged database-only body write service and HTTP route now check the direct
content-edit grant and all three access epochs in the save transaction. They
write one row body and its document sequence/hash/audit/receipt atomically,
return no parent document ID, and replay the identical request only while the
grant remains current. A file referenced from body blocks must already be
visible through this database or be the actor's pending database upload; a
private file elsewhere on the owner page is denied. Child-page creation is
not accepted through this narrow route. The isolated HTTP test covers view
denial, edit, replay, conflicting operation ID, fault rollback, unrelated-file
denial, cross-database denial and revocation. The direct item UI now enables
body editing for content editors and keeps it read-only for viewers. It uses
the existing encrypted draft journal, preserves the exact database-scoped
request across a lost response and reload, and retries with the same operation
ID and database epoch. Closing the item or navigating away first flushes a
pending draft; a failed confirmation keeps the item and database open with an
error. The item resource picker uploads through the database-only resumable
file route after confirming the current body save, refreshes the document
sequence, and attaches the file to the same body block. Its gutter is kept
inside the modal so the add-block control remains clickable. The isolated
HTTP test confirms a pending upload is private to its uploader until the body
references it; another database viewer can read the bytes only after that
attachment. A mocked browser covers edit, lost response, reload, exact replay,
file selection and attachment, updated body and viewer downgrade. Schema
editing and comments in this screen now have scoped implementations. The
sidebar entry and an exact-2429 synthetic-catalog browser journey have also
passed. Other privacy surfaces below remain activation gates. In particular,
the normal app startup rejects a
2428/2429 catalog until its successor runtime compatibility gate is reviewed;
the test-adapter browser result must not be represented as a normal-runtime
integration test.

| Surface | Current parent-page dependency | Required gate |
|---|---|---|
| Full-page database URL and UI | `openSidebarDatabase` opens the parent page document | Grant-only users receive a database projection without the parent's title, body, sibling blocks or page-level share controls |
| Source reads and schema/row commands | `database_features.read_database`, `read_databases`, `editing_authority` resolve the owner document | Role-specific database read, content write and schema write; stale role/epoch denied in the same transaction |
| Item pages, comments, files and exports | Row and file authorities resolve the owner document | Item and attachment access follows the database grant and any narrower item rules without leaking other rows |
| Linked views and relation/formula targets | `linked_databases` and computed-cell visibility resolve source-document access | Instance access and source database access are checked independently; a link never confers source access |
| Search, inbox, sidebar and link previews | Page resolution and canonical search assume a visible owner document | Grant-only database entries are discoverable without revealing inaccessible parent or sibling content |
| Realtime tickets, changes and revocation | Live authority issues page-scoped tickets and uses page epochs | Database-scoped tickets stop writes after revoke; old offline envelopes cannot be replayed under a new grant |
| Copy, move, Trash and backup/restore | Database identity changes and snapshot marker lists end at 2427 | Grants never accidentally copy to a new database; move keeps identity and access; Trash suppresses access; exact backup marker is recognized |

Do not enable a database sharing control until these gates and private-parent,
mixed-role, linked-view, revoke, retry and cross-workspace isolation tests pass.
The existing popover must continue to describe inherited parent access meanwhile.

The staged independent item screen now reads and creates row comments through
the database grant. Existing page-authorized file links in stored comments are
rewritten only when the file is live and referenced by this database; otherwise
the whole attachment label and link are redacted. Unknown page-shaped links
and private owner-page identifiers suppress the comment text. The read and
create receipts omit the owner document ID, and create/replay checks the live
grant plus workspace, document, and database epochs. Direct reply, edit,
delete, resolve/reopen, and reaction commands now use the same scoped authority,
including row-version checks and replay fences. The independent item screen
uses the existing encrypted comment draft/outbox and controls with database-only
request and receipt contracts. Mention IDs are omitted from the direct read.
Direct replies and added reactions now create notifications for the original
comment author through the existing transactional notification store. A
separate inbox reader checks the database grant, current row placement, and
active comment, then returns a database/row target without the private owner
document ID. Read acknowledgments require the workspace, document, database,
and notification epochs; revoked or stale grants cannot acknowledge old items.
The inbox UI opens the independent database row and its comments before
acknowledging the notification. The ordinary page-shaped inbox remains for
recipients who can read the owner page. Direct comment create/edit requests
also accept mention IDs, require each active recipient to have current access
to the same database, and insert mention notifications atomically. The
independent item comment composer exposes the mention picker and journals the
exact mention list with its retryable request. The picker checks each directory
candidate against the current database grant before displaying it, using
bounded batches for large directories. The read-only candidate route and
subsequent comment write both revalidate access. Direct comments now accept exact
database-scoped markdown file links after checking that the actor can read
the active file through this database. A database upload remains private to
its uploader until a live row comment references it, then database viewers
can read it; removing the comment removes that reference. The independent
comment composer can upload and link a file for content editors and displays
the authorized link in the thread. Comment-only users cannot upload through
the current database file contract. Isolated service and mocked browser checks
passed for filtered mention suggestions; the successor runtime is still gated.
Comment attachment behavior across database move/copy and backup, the remaining
notification cases, and a successor-runtime browser run without the test
adapter remain activation gates.

## 2026-09-30 exact-catalog browser check

An isolated app now opens a synthetic 2429 catalog through a test-process
compatibility adapter. A database-only commenter could follow the original
database URL through login, open the row, see only the database-scoped
projection, choose an eligible mention, save a comment, and read it again after
reload. The same user received 404 for the private owner page. The exact-catalog
browser check also found the database by title search and displayed a direct
database row in the shared sidebar without the private parent identifier.
Revoking the direct grant then made the database and search APIs return 404
and hid both the database and its sidebar row on reload. A stale sidebar row
is also removed after a denied open without reloading. The full browser
check and the existing mocked search UI check passed; 23
authentication contract checks also passed. Login redirects now preserve an
internal page/database/workspace/AI destination instead of losing it at home.
The direct sidebar row retains keyboard focus when the list rerenders.
The independent database screen now shows its own top-right share and link
controls. The read-only share popover rechecks the database grant before
showing the actor's role, and its copied URL contains only the workspace and
database IDs. It does not show the private parent or offer parent-page grant
editing. The exact-2429 synthetic browser check covered the role, link,
mobile-width popover bounds, and visible database content after closing it;
both 390px captures were inspected. Invitation and database grant editing
remain behind the broader activation gate.
The direct screen also shows the database's last-edit control when the scoped
projection has activity. Its updates drawer reads the database-scoped,
content-free feed, keeps pagination bound to the current database, and opens
an authorized row without first opening the owner page. The exact-2429
synthetic browser check created a real cell event, opened its row from the
drawer, and confirmed the API response omits the inaccessible parent ID.
The scoped update reader now extracts one visible row ID from direct cell/body
events without returning cell values; multiple-row events have no row link.
Existing service checks also cover denied sibling databases, and the older
owned-database updates test still passes.
The direct feed now labels a cell commit as a property edit and an item-body
commit as a body edit. It derives those labels from the event shape without
returning changed cell values or body text. The exact-2429 synthetic browser
check confirmed the property-edit label and row navigation; scoped service
checks confirmed both event kinds and excluded private parent identifiers.
The older owned-database updates test and JavaScript/Python syntax checks pass.
Historical typed cell updates are now attributed through their original
property's database, not the row's current database. A real same-page row move
previously made its earlier cell edit disappear from the source feed and appear
in the destination feed. The isolated row-move regression now keeps that edit
in the source feed, excludes it from the destination feed, and returns no cell
value or link to the moved row. Both existing owned-update and scoped sharing
checks pass.
Direct database content editors can now create an empty item through a
database-only service and HTTP route. The row and its empty title cell commit
atomically under the workspace/document/database epochs; the receipt omits the
private owner document. A browser journal stores the exact creation request
before sending it, retries after a lost response or reload, and prevents a
second row from being created by that retry. Isolated service/HTTP tests cover
view-role denial, injected rollback, replay, changed-operation rejection,
revoke-before-replay and private-parent denial; a mocked browser verifies
response loss, reload, one row, and the hidden create control for view role.
The exact-2429 synthetic full-app browser also created one row through the
real HTTP route and verified its appearance in the independent database.
Direct creation now applies the database's selected default template, including
its title/default cells and ordinary body blocks. An empty request title keeps
the template title; a nonempty request title overrides it. Relation defaults
are checked through the target database's own read grant, and paired inverse
edits require its content-edit grant. Cross-page/cross-table embeds in a
template body are redacted from a grant-only projection and rejected before
direct creation; template-body file references must belong to active files on
the owning document. Isolated tests cover default body cloning, title
override, denied relation rollback, subsequent target grant, and private
template-body redaction/rejection. When the selected default is unavailable,
the independent screen explains why its create button is disabled and offers
an explicit blank-item action. That action records the choice in its durable
retry request; the HTTP route accepts the choice while preserving retries from
the earlier request shape. The independent picker now lists the blank choice
and active named templates. It disables templates whose body or defaults were
redacted by the scoped projection; a selected template ID is stored in the
same retry journal, bound to the server operation digest, and checked against
the response. Scoped HTTP/service and mocked-browser checks cover a named
choice, replay, and a different-template operation conflict. A complete audit
of all embedded link forms remains open.
For a paired relation default, the projection also marks the template
unavailable when the actor can view but cannot edit the inverse target. An
isolated case verifies that view permission denies creation, while granting
target content-edit permission allows the row and inverse cell to commit.
The source database's last-edit summary now advances with direct item creation.
The logged-in Notion comparison table's create-arrow menu was inspected again
without creating a row or template. At 1280px it opens a roughly 360px panel
under the arrow, with a database-named heading, short explanation, separator,
and template actions. The independent picker now uses that width and hierarchy;
its named/blank choices remain governed by the scoped role. The main create
button omits the extra plus glyph seen in the earlier synthetic screen. A
1280px synthetic-browser screenshot of the revised menu was visually checked.
The direct create cluster was also one row too high: the independent renderer
left it beside the database title, while the Notion comparison places it on
the view-tab toolbar. The cluster now moves into that toolbar, matching the
existing owned full-page behavior. A browser geometry check confirms the
create button and view tab share a row; the captured menu now opens directly
under the toolbar arrow at the same approximate coordinates as Notion.
The logged-in Notion database's top-right overflow was inspected read-only.
The directly shared database now shows an overflow button beside Share and
Copy link. Its searchable menu offers the scoped database URL, a new tab,
updates, and database help; the edit-time footer uses the database projection.
The exact-2429 full-app browser check opens this menu, verifies it omits the
private parent identifier, and follows Updates through the scoped route.
Notion also shows a favorite control and broader actions such as duplicate,
move, trash, export, lock, and connections. Duplicate, move, trash, export,
lock, and connections are not shown on the direct-grant surface until each has
a database-scoped permission and storage contract; the existing page routes
require the private parent page.
Read-only inspection of the existing navigation favorites confirmed that both
member and guest entries are keyed by the source owner, parent page, and block.
A 2430 forward migration adds subject-scoped canonical direct-database favorite
state containing database IDs, order, and a version, without parent page IDs.
The scoped service checks the current database grant and workspace, document,
and database access epochs in its transaction. It commits favorite state,
receipt, audit, event, and outbox together; same-operation replay is allowed
only while access epochs remain current. Its read path filters every stored
favorite through current database authorization, and a revoked proof database
returns 404. The exact-2430 synthetic full-app browser check covers the topbar
star, sidebar entry, reload persistence, remove/re-add, revoke, and absence of
the private parent ID. The content-region focus outline seen in that captured
screen was removed while child control focus remains visible. This is staged
behind a separate capability and does not activate the normal runtime or
migrate user data.
An additional same-session two-tab check reproduced a stale star after one tab
changed the favorite. A scope-checked browser invalidation now makes the other
tab reload its authorized list after both add and remove. Focus/visibility
refresh is a fallback for changes made outside that channel; neither message
nor local storage contains a private parent ID or favorite data.
For both exact-2429 and exact-2430 synthetic runs, returning focus to an open
direct-grant database now rechecks its database-scoped read. A revoked grant's
404 closes an idle table, removes its sidebar entry, and replaces the stale
database URL with Home without revealing the private parent page. A table with
an unconfirmed local edit keeps the input until its recovery path is resolved.
For an idle direct table, the same read now applies changed grant epochs and
role to the open projection, refreshing edit controls without a reload. The
exact-2429 full-app browser promoted a comment grant to content-edit and then
downgraded it to comment; cell edit controls appeared and disappeared on focus.
If an editor or retry journal has pending input, the client preserves that UI
and reports the changed permission rather than replacing its DOM.
The 2429 browser now also holds a raw cell value without committing it, revokes
the grant, and returns focus to the tab. The value remains visible with a
persistent access notice; the stale topbar actions and sidebar entry disappear.
Clicking the table close control cannot discard that value. Escape cancels the
cell edit, after which another access check can close the table. The navigation
guard also detects pending cell and row journals. A separate exact-2429 browser
check now revokes access with a stored comment draft and body draft in turn,
shows only the local drafts in a recovery modal while hiding the revoked table,
then verifies each draft is restored after access is granted again. The body
controller waits for its local
journal before showing that modal and never starts a new remote save on access
loss. An uncommitted value in the item property editor instead appears in a
copyable recovery field clearly marked as not stored; leaving explicitly
discards that raw value. Composition or failed local storage keeps the original
input visible and blocks automatic replacement. Pending grid cell/row journals,
rich body-copy fidelity, and live user-app behavior still need more review.
These are synthetic browser checks, not a live user-app rollout.
The direct-table recovery path also distinguishes a raw grid value from the
same value already durably queued in the encrypted cell journal. On a revoked
grant, a queued cell save or row create with no additional raw input can be
parked behind a recovery notice and the inaccessible table can be left. An
exact-2429 browser check interrupted each HTTP request, revoked the grant,
left Home, then restored the grant and confirmed the old-epoch request still
needed explicit resolution; neither cell nor row was silently committed.
An unsaved grid value different from the queued request continues to block
replacement so it can be copied or cancelled. The exact-2429 browser also
checked active-role reductions: a raw grid input remains copyable until Escape,
when controls update to the new role; an open item modal parks body/comment
drafts or shows an unjournaled property value for copying before applying
the lower role; and a pending row-create request remains parked. Synthetic
composition events now cover a comment-role reduction, a body-role reduction,
and complete access revocation while a comment is being composed. The original
editor remains mounted until composition ends; any final text that did not
reach the durable draft is shown separately as copyable, unstored input. A
read-only body view no longer treats an earlier editable body draft as new
unstored text when comment access is later revoked. These isolated Chrome
checks do not replace a manual Windows IME check. An exact-2429 browser check
now pauses a cell response after the server commits it, revokes access, then
delivers the acknowledgment: the already saved value is not treated as a raw
unsaved edit, and the inaccessible table closes. A cell request denied after
revocation and a row-create request denied after role reduction instead retain
their local journals and automatically open the recovery notice when the
in-flight requests settle. Reopening with new access does not silently commit
either old-epoch request. An additional exact-2429 browser check pauses an
item-body save during an edit-to-comment downgrade and a comment submission
during complete access revocation. After the responses are denied, the
respective drafts and requests remain locally recoverable and the server has
neither change. A previously queued body request no longer blocks a new
comment when the current role permits comments but cannot edit the body. A
further exact-2429 browser check holds the response after a body save commits,
reduces the role, and then delivers that response; it also holds a committed
comment response across full access revocation. Both writes remain on the
server, their old requests are cleared locally, and no false recovery draft
appears. The interim role-change notice describes an unconfirmed result
without claiming the write failed. The full role/schema matrix and real
user-app behavior still require review.
One isolated migration setup initially reported a target-image checksum
mismatch; later runs passed. Its cause remains unverified, so migration
reliability is not cleared by this browser test.

A further staged template check found that a private page could be referenced
inside ordinary paragraph markup without a `page_id` field. The direct-grant
projection now withholds a template body containing a page URL, including
Markdown/HTML links and HTML-entity or percent-encoded paths; direct row
creation rejects the same template before writing. An isolated end-to-end
template test and focused link-form checks pass. This is a conservative
template boundary, not a complete audit of user-authored links in existing
row bodies or every embedded link form. The normal runtime remains gated.
The same known-reference check now applies to an existing item body on a
grant-only database read: blocks carrying a page URL or page embed are omitted
from that response while unrelated blocks remain visible. A flag explains the
partial body in the item modal and disables body editing so a grant-only save
cannot erase hidden blocks. The scoped write transaction also rejects a new
page reference and refuses to replace a body that already contains one; the
owner's original body remains intact. The isolated HTTP integration and mocked
browser checks pass. This does not establish full fidelity for every link
format or the exact Notion behavior of inaccessible references; those require
further comparison before the normal runtime can be enabled.

The logged-in Notion full-page table was compared visually with the staged
direct-database screen. Notion places a compact property-type icon before each
column name and uses a white header background. The staged table now renders
type-specific icons in that position, including text, number, and date, and
uses the page background for the header. The focused direct-database and
personal linked-view browser checks pass; a synthetic staged screenshot was
also reviewed. The reference account owns its table whereas the synthetic
direct-grant account has limited permissions, so the toolbar controls cannot
be treated as a role-equivalent comparison. The ordinary app on port 10322
was unavailable during this check, and the staged schema remains gated.

The owner toolbar in the reference table has Filter, Sort, Automation, AI,
Search, Settings, and Create controls in that order. The staged full-page
toolbar already orders Filter, Sort, Search, Settings, and Create accordingly;
Automation and AI services remain outside this implementation. Notion's
published permission guide says a `Can edit content` user cannot change the
original database's views, filters, or sorts, while a separately linked view
may have its own settings. The direct-grant screen therefore must not expose
the owner's original-table controls merely to match the screenshot. The
reference table's relation property uses a diagonal arrow rather than a chain
symbol; the staged table header and view-setting property lists now use that
relation icon. Reference: https://www.notion.com/help/intro-to-databases .

The adapter lives under tests and supplies the reviewed capability set to
synthetic runs. It does not make the normal runtime accept 2428/2429/2430 or
activate user-data migration or database sharing controls. Production facade,
remaining privacy surfaces, and role combinations still need their own gates.
