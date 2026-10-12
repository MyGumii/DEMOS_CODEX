# Canonical page archive service

Status: service, offline migration, HTTP routes and browser intent runtime tests
passed. Routes and the capability-aware UI are connected. Only the named 14534
fixture has been migrated to 2413 and restarted. Actual browser archive/restore,
remote-client behavior and operating deployment remain unfinished gates.

## Reproduced mismatch

The existing collaborative workspace trash route calls the legacy page store.
A synthetic parent/child on the 2412 fixture returned success, but the canonical
body still returned 200 and the page remained in the workspace list. Duplication
also returned a legacy subtree whose canonical body returned 404. This packet
adds the archive/restore service and connects its routes and UI; duplication
remains unresolved. A native confirmation dialog blocked the actual browser
archive test before any archive request reached the fixture server.

## Mutation contract

`notion_collaboration.page_archive.set_page_archived` takes the current actor,
workspace and document plus `archived` (strict bool), caller operation ID, exact
document sequence, workspace security epoch and document access epoch.

- Current active human, current workspace owner and ownership of every affected
  document are required, including on exact receipt replay. A workspace role or
  source ownership alone is insufficient. This preserves the current lifecycle
  authorization scope; broader guest management has not been implemented.
- New requests require the expected current state and an unlocked affected
  subtree. A parent link change also requires an unlocked editable parent.
- A subtree is bounded at 128 documents. Cyclic hierarchies and inconsistent
  archive membership fail before any write.
- Archive records only currently active descendants, stopping at already
  archived branches. Restore follows the selected page's archive batch and adds
  its archived ancestor path, without reviving unrelated siblings or previously
  trashed children. The surviving parent and sibling position are retained.
- Parent-link deltas and current archived titles are decoded with the pinned Yjs
  runtime outside the write transaction. Before committing, the service repeats
  authorization and compares every affected document, parent and generation.
- The document flags, current archive membership, access fences, event sequences,
  parent structural command/Yjs delta, operation receipts, audit and outbox commit
  together. Injected failure at any finalization stage rolls them all back.
- Every affected document and the workspace receive a new access epoch. IAM
  visibility rejects an archived document or archived ancestor, even for an
  owner or a directly granted guest. Existing tickets must be revalidated by
  the established transport contract.
- Body revisions, history checkpoints, child body blocks and CRDT generations
  remain intact. The boundary parent's link is removed and reconstructed as a
  link-only delta. Stored previous/next block anchors recover its position; if
  both anchors disappear, its former bounded index is used.
- A repeated exact request returns its original receipt, even after a subsequent
  restore, and cannot archive the page again. Reusing an ID with different inputs
  fails. Response retries never mint a new operation automatically.

`list_archived_pages` is owner-only and reads canonical metadata plus current Yjs
titles, revalidating access/sequence after decoding. Returned entries carry the
epochs and sequence needed for restoration. Listing is capped at 1000 entries
and 64 MiB of replay frames; larger listings fail explicitly pending pagination.
The current archive and restore actions are reversible. There is no purge path.

## Schema and rollout

The new **unreleased 2026082413** successor adds
`workspace_collaboration_page_archive_state` with exact batch membership and an
optional boundary-link anchor. Existing archived flags become separate imported
memberships, preserving earlier independently archived children. The previous
2412 migration is unchanged.

- SQL checksum: `sha256:21457646577c8dcad3f23e2c834b173efae0d9d900447450464db356e344447f`
- Physical schema: `sha256:af6a280fab62623cb8762a189ea739c77d1d5e333e233d50be9be2cbbc39d6d7`

The explicit existing migration mechanism requires an exact dry-run plan, a
verified backup and a quiesced catalog. Requests and startup do not migrate data.
Exact 2413 markers retain guest navigation, image, search, history, file and
editing authority compatibility. The named 14534 fixture was stopped, backed up,
migrated with the exact plan, verified and restarted. Existing tables were
unchanged and its protected page remained at document sequence 10. Operating
10322 and the other fixture catalogs have not been migrated by this packet.

## HTTP and UI integration

- Collaborative POST page trash / trash restore accepts only
  `expected_document_sequence`, `expected_workspace_epoch`,
  `expected_document_epoch` and `operation_id`. GET workspace trash uses the
  canonical list. Legacy `base_version` remains an ordinary-mode contract.
  An older collaborative schema fails closed instead of writing the legacy store.
- The workspace bootstrap advertises `collaborative_page_archive` only at exact
  2413. Mutation receipts include the workspace ID and row version committed in
  the same transaction. The individual workspace-detail capability object is
  intentionally different from the bootstrap capability object.
- The UI flushes open editors and holds their inputs before reading current
  stamps. Duplicate activation shares a pending promise. An uncertain HTTP
  response retains the exact request for retry; a received receipt followed by
  UI refresh failure retains the result without issuing another mutation.
- User/workspace/navigation guards reject obsolete work. The server's affected
  IDs determine which open views close. Pending and uncertain lifecycle writes
  participate in the existing before-unload protection.
- Archive/restore events invalidate hierarchy metadata. An inaccessible live
  page loses its sidebar locator while the current recovery editor keeps its
  unsaved body. Trash filter responses are scoped and ordered; stale responses
  do not overwrite the current workspace or query.
- Collaborative trash omits the unsupported purge action and fictional ordinary
  30-day countdown. Ordinary-mode UI behavior remains intact.

Four HTTP tests and sixteen browser-function runtime scenario groups passed,
along with the existing navigation/settings/editor/link/save/peek/move/metadata
checks. These runtime tests are not actual browser archive/restore evidence.
Before-unload protection is not a claim that lifecycle intents survive a full
browser restart.

### Response recovery after an archive access fence

POST `/pages/<page_id>/lifecycle-result` is a read-only result lookup. It accepts
the exact original four request fields plus a strict `archived` bool, matches
the original request digest, and revalidates the current active actor, workspace
owner and ownership of every affected document. It does not require ordinary
read access to the archived body and cannot repeat the mutation. No response is
cacheable. `unknown` means no receipt was visible in that read snapshot; it is
not proof that a concurrent request cannot commit. `committed` includes the
original receipt and separately observed current document/workspace state.

The client now looks up a retained request before trying to flush an archived
editor. A committed receipt skips the impossible flush. If the receipt remains
unknown, a resend still requires a successful flush and uses the original ID.
A recovery panel outside the editor keeps the retry action reachable when the
ordinary page menu disappears. If the editor contains local changes or is in
recovery mode, applying the result preserves the main and side-peek editors and
their pending updates instead of closing them. The panel can restore the page;
the ordinary trash overlay also avoids reopening a preserved editor. Restoring
does not silently reapply edits authored under an older access epoch.

A superseded archive receipt cannot make a page restored by another action
appear archived again. The client checks the current receipt-lookup state and
the subsequent unfiltered page list. Runtime tests cover loss plus the actual
embedded-editor blocked-flush contract, the rendered panel's click handlers,
dirty main/peek preservation, restoration, overlay reopening prevention and
unknown/superseded outcomes. Native clicks, two real clients, browser restart
recovery and partial-restore sibling UI behavior are still not proven.

## Remaining integration gates

1. Recover the browser's pending native confirmation, then verify top/row archive,
   list removal, direct-link denial, restore, reload and body preservation through
   actual clicks. Do not repeat the uncertain click before resolving the dialog.
2. Verify the implemented response-loss/recovery paths with a second real client,
   side peek, navigation and fresh input; complete any resulting fixes.
3. Compare real Notion partial restore/locked subtree behavior before treating
   these server policy choices as proven UI parity. Check remaining archived
   child links when only an ancestor path is restored.
4. Exercise archive/restore from the actual row and top menus, refresh, direct
   links, side peek, grants and a second connected client in a named fixture.
5. Complete canonical create/duplicate, including current Yjs, internal document
   references, databases and files. The reproduced copy mismatch remains open.
6. Prepare rollout/restore evidence and seek the explicit operating migration
   authorization required by AGENTS.md only after the result is reviewable.

The user-specified drag contract remains unchanged: start in page margins outside
the writing column; include all body blocks, including heading/table/DB blocks;
exclude the page title; keep the existing fill and omit rectangle borders.
## 2026-10-03 parent insert retention extension

Page archive and restore now capture proven canonical inserts newer than the
parent's last bound current-generation frame and independently validate the
resulting parent body/title and exact link anchors before writing. The existing
subtree scope, owner/lock/stamp checks and atomic archive/restore receipts remain.
This is limited to insert retention; older divergent streams are not repaired.
See `page_link_parent_insert_contract.md` and its separate phase evidence.

