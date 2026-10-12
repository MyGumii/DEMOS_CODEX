# Native page creation — API and editor integration

Status: the explicit 2413→2414 migration, native registry/read selector and
atomic creation service have passed isolated tests. Native page list discovery
is connected; the 2414→2415 member navigation cutover supports both page origins
with preserved personal settings and receipts. The collaborative POST now calls
the native service directly, with no legacy fallback, on the exact 2415 runtime.
Main and side-peek creation preserve the original request and accepted result.
Same-browser navigation invalidation is connected. Actual UI checks have shown
root creation, saved title/body after reload, child draft handoff and another
tab's list refresh. Cross-device creation discovery remains an integration gate.

The sidebar page-row hover `+` now starts canonical child creation immediately
with initial title `새 페이지`. The dialog keeps an empty title input with that
placeholder. A second direct 2026-09-26 Notion recheck established that the
reference's disappearing untouched row was a Trash transition: its URL could
be opened in another tab before any input, and after the original dialog was
closed that URL displayed a Trash notice. The app now archives its newly
created child when the dialog is dismissed untouched. Isolated legacy and
2415 collaborative browser checks cover direct child URLs, empty close,
create-response loss, immediate/idle title input, editor handoff, persistence
after reload, and permission/retry cases. The parent tab now carries the
reference's child query URL while the dialog is open, and a same-tab reload
restores the reserved child without creating it again. Production migration,
feature activation and cross-device discovery are separate gates.

## Required result

A workspace owner creates a page directly in canonical storage. Creating the
page, its initial paragraph/revision/CRDT generation/checkpoint, native registry
entry, parent child-page link and audit/outbox/operation result must commit
together. No per-user legacy page, fake legacy source proof or fabricated
upload/session record is created.

The current creation authorization is the existing workspace-owner scope.
The actor must remain an active identity and workspace owner. A parent must be
a current, readable, editable, unlocked page owned by that actor in the same
workspace. A canonical parent need not have a legacy source binding; authorizing
that parent does not register or expose unrelated unbound legacy pages.

The request carries `title`, `parent_id`, `base_version`, and `operation_id`.
Titles retain the current 1–160-character creation contract. Later editor
updates may clear the title. The ID is deterministic for the actor, workspace,
operation and native creation contract. A reused ID with different input fails.
An exact replay rechecks current authority and returns the committed result,
including the original workspace version; only the transport `replayed` flag
differs. A deleted or inaccessible created page cannot be recovered through an
ordinary create replay.

Parent links are prepared from the current canonical structure and Yjs body
outside database transactions. The write transaction checks the workspace
version and parent row/document/access/generation stamp again. A concurrent
parent change fails without a partial new page. The existing typed structural
ledger and service-authored Yjs delta remain in agreement; current parent text
and title survive unchanged. Adding a page advances the workspace navigation
version without changing existing access grants or invalidating existing tickets.

## Native registration and compatibility boundary

2414 adds `workspace_collaboration_native_pages` as explicit provenance.
Existing data rows and legacy source bindings are preserved; existing unbound
documents are not automatically adopted. The migration is offline only and
requires an exact plan, verified backup and quiescent catalog. No request or
startup applies it. The previous physical migrations remain unchanged.

`native_page_items()` returns only authorized active registry entries. Placement
is derived from the current ancestor chain and existing ancestor placement,
with a private default for a native root. This avoids a stale duplicate placement
after moving the page. `list_workspace_pages()` merges these entries with current
authorized source bindings and applies the existing metadata/title projection,
deduplication, inaccessible-parent removal, filtering and ordering. The `copy`
provenance value is reserved; canonical duplication is
not implemented by this service.

Historical member favorites reference `workspace_page_sources` by foreign key.
The exact 2415 runtime uses `workspace_member_navigation_states` for all member
settings and favorites. Its offline migration preserves existing versions,
ordering and settings without modifying historical tables or manufacturing
legacy bindings. See [member navigation contract](member_navigation_contract.md)
for current permission filtering, atomic receipts and pre-cutover replay.

## Remaining integration gates

- Native discovery and member favorites have isolated API/service coverage.
  Verify their placement, move behavior and creation flow in the actual UI.
- The exact 2415 bootstrap advertises `collaborative_page_creation`. Older
  collaborative runtimes refuse creation with 503; ordinary creation retains
  its existing route/service contract.
- In the 2026-09-26 direct Notion comparison, a sidebar row `+` opened a
  centered new-page dialog over a dimmed page. The isolated local draft now
  uses that presentation, restores the background on dismissal, and preserves
  the title's screen position when the canonical embedded editor takes over.
  Empty dismissal archives the newly created canonical child; input transfers
  to the editor against that existing ID. Other
  side-peek entry points retain their existing panel behavior. Header commands
  and starter choices in the actual Notion dialog remain a visual/behavioral
  gap.
- The local empty draft now offers AI-notes and database starters at the bottom
  of that dialog. Choosing either replaces its one empty paragraph with the
  selected supported block and saves it against the already-created page.
  The starter rail hides as soon as meaningful title/body input arrives, leaving
  save errors and the retry control visible. Actual Notion also offers an AI
  question, form, template and more, which are not claimed by this local flow.
- Main creation freezes the first actor/workspace/parent/title/operation intent,
  deduplicates in-flight clicks and retains an accepted page ID across later
  navigation failures. These intents are in memory, not reload-durable storage.
  Workspace responses cannot regress a newer version or erase access/teamspaces.
- Side-peek honors `parent_link_saved`; it does not insert a duplicate link or
  open a hidden parent editor for an already committed link.
- Navigation refresh applies only within its captured actor/workspace/query and
  preserves the active editor even when the returned list omits that page.
- Verify subsequent draft body/appearance saves, IME, close/retry and navigation
  races. This service's initial page contains one empty paragraph; it does not
  claim to atomically commit the later draft body.
- Same-browser notifications contain only contract/actor/workspace, and prompt
  an authorized list read; they carry no private page identity or content.
  Window focus/visibility also refreshes the list. Different browsers/devices
  still need workspace-scoped creation invalidation; adding `page.created` to
  a document-only event stream does not implement that contract.
- Real browser testing exposed that periodic live-domain reads omitted service
  CRDT updates. Polling now reads durable updates before domain events, using a
  separate cursor advanced only after draining the replay batch. The server
  supplies the exact batch cursor; newer ACKs/broadcasts cannot skip earlier
  changes, and a full queue drain is continued before advancing the cursor.
- Run the remaining real browser/Notion comparisons before rollout. The named
  disposable 14534 fixture was upgraded offline from 2413 through 2415 using
  exact plans and verified per-step backups, with all existing rows preserved.
  Operating data has not been migrated.

Validation covers exact migration prerequisites and preservation, current Yjs
parent content, nested/root creation, strict retries, three transaction fault
points, parent CAS, lock/identity/marker rejection, native archive/restore, and
two independent Windows processes submitting the same creation operation.

## Typed deletion/order successor (2026-10-03)

`parent_structure_rebase_contract.md` extends the proven parent tail to typed deletions and moves, and extends read-only page copy source preparation. Exact immutable history, independent full-body/title verification and existing writer fences remain. Older unexplained deletion/order is refused; rich-content typed restoration requires its own recovery contract. The insert-only counts above remain historical; current phase receipts are in the chat workspace at `outputs/parent-structure-rebase-20261003/phase-evidence.json`. No operating capability/schema activation is implied.
