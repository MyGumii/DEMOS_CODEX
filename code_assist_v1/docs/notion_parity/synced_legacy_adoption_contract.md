# Explicit legacy synced-origin adoption

Status (2026-10-03): read-only preparation implemented and 12 isolated canonical
tests passed (162.76s).
An internal atomic writer is implemented and its final 13 tests passed (205.01s).
The explicitly selected flat-body review/request API and selection/recovery UI
are now implemented behind `collaborative_synced_legacy_adoption`, which defaults
to false. There is no automatic migration or operating flag change. Current
HTTP/process/controller/browser receipts are recorded separately in
`outputs/legacy-adoption-http-ui-20261002/phase-evidence.json` in the chat workspace.
The existing `origin_of` rejection of sync-ID-only blocks remains in place.
Explicitly selected omitted flat members can now join an original previously
designated by this contract, using its immutable server receipt as provenance.
This is a separate bounded extension; final join evidence is recorded in
`outputs/legacy-join-20261002/phase-evidence.json` in the chat workspace.

This is a local compatibility contract. The direct Notion synced-copy comparison
establishes explicit original/instance behavior; it cannot identify the original
of an older local group that never stored that identity. Matching content, block
order, ID spelling, access or the first member are not evidence of an original.

`prepare_legacy_adoption` requires the caller to name an original endpoint and
zero to 127 reference endpoints. Each endpoint is an exact document/block pair;
duplicates and selecting the original twice are rejected. The result covers
only this explicit selection. It does not discover a complete group, convert
unselected/archived/unreadable members or declare them already adopted.

Every selected document must belong to the current active owner under the
existing canonical copy ownership contract, be readable and editable, and be
unlocked. Durable Yjs frames are replayed outside SQLite transactions, under the
existing combined 64 MiB preparation budget. Current endpoint access, workspace
and document stamps, generation and exact frame digests are rechecked in a final
read transaction. A concurrent edit or identity revocation invalidates the plan.

This packet supports flat wrappers in the document body. Inline blocks, inferred
or explicit descendants, and database item/template scopes need their own
content/identity adoption contract. Explicit originals/instances cannot be
re-adopted as legacy blocks. A proven existing original can instead be retained
as the source of a later join under the contract below. Every selected legacy
wrapper must have the same legacy
group ID and exact content/shared props, including rich markup. Only surrounding
indent/column/tab placement is excluded from that equality comparison. Divergent
content is refused rather than overwritten, and resolving it is not automated.

The immutable plan retains each complete current body and before/after hashes.
Only selected wrappers change. The chosen original retains its content and rich
markup and gains `synced_from: null`; references gain the explicit original pair,
empty content and no cached rich markup. The new group ID derives from the chosen
original identity. Positions, surrounding placement, unselected body blocks,
documents, database/file descriptors and permission grants remain unchanged.
Copies of returned values cannot mutate the frozen preparation.

`revalidate_legacy_adoption` requires the future caller's transaction, reconstructs
the plan from its captured sources to reject modified scope/body/metadata, and
rechecks all current endpoint authority and frame stamps. It does no Node work
and provides no lease. Preparation alone retains `commit_ready: false`: the writer must persist
all selected document bodies, matching durable Yjs deltas, revisions, exact retry
receipts, audit and outbox atomically. Failure cannot convert only some endpoints.
Writer and bounded flat selection UI validation do not establish full legacy
group adoption. Other unselected, archived, nested and item/template scopes remain
required before treating the full extension as complete or activating copy.

The preparation tests verify the complete catalog dump is unchanged, exact
selected original/reference output, unselected divergent content preservation,
current durable text rather than stale SQL text, replay outside readers,
content/markup/group/explicit-origin/child-shape refusals, concurrent insertion,
identity revocation, owner and lock checks, modified preparations, explicit
selection validation and the replay budget. These are service preparation tests;
these preparation cases alone do not establish the browser workflow or operating adoption.

## Atomic backend writer

`prepare_adoption_delta` prepares all selected documents in one Node invocation
using the already verified body-delta engine. It rejects added/removed body
identities, bounds each durable frame to 1 MiB and the complete update set to
64 MiB before independently decoding old plus prepared frames. That independent
projection must exactly match every planned body. Preparation and projection
run outside SQLite transactions; the source selection is revalidated again
afterwards. The frozen delta metadata binds its frame hashes and current titles
to the immutable source preparation.

`commit_prepared_adoption` first checks the exact operation receipt, then
rechecks it under the writer before validating all sources. Every selected body,
matching durable updates, revision, receipt, document event, audit and outbox
shares one transaction. The existing document/resource identities and positions
remain. Unchanged SQL block rows retain their creation time and row version;
current durable text is materialized where its SQL projection still lags.
No schema/table or permission grant is created. An outbox failure rolls the
entire selection back, including earlier document updates.

The request hash binds the actor, workspace, explicit original/reference
selection and preparation hash. Receipt replay occurs before stale source
preparation, so a committed operation can be recovered after later ordinary
edits. Current owner and view access to every selected endpoint is still required
to disclose that receipt. A new write also requires current edit access and
unlocked documents; a lock does not invalidate an already committed readable
result. Reusing the operation ID for another original selection is a conflict.

Review of the first writer found that revision capture used the SQL title even
when its durable title lane was newer. The first two added title cases failed
(38.77s), but their separate title stream was subsequently found to compete with
existing characters; those failures do not prove the exact intended title.
The fixture now renames the existing durable stream and asserts its current title
before adoption. The writer materializes the independently decoded current title
together with its body/revision. Only an absent title lane falls back to the captured canonical
title. Empty strings and intentional spacing are retained; no display placeholder
is written. This records the current logical title rather than performing an
additional title edit. The final writer suite passed after the corrected title
fixture, current-title materialization and pre-replay frame-budget check
(13 passed, 205.01s).

These modules are internal server-owned preparations, not serialized client
write instructions. No public API accepts their bodies or update bytes. The
review UI and exact request/result HTTP contract are implemented in the next
bounded packet below. Reattaching an omitted flat member is implemented in the
later join contract below. Restored/archived and nested legacy groups and
item/template identity contracts remain future work. Normal copy and
legacy-origin guards remain.

Final writer validation covers exact SQL/Yjs body and resource preservation,
unchanged unselected raw rows, copying the adopted group and later independence,
late-outbox rollback, modified updates, exact retry after later edits, operation
ID conflict, source/reference changes, identity revocation and locking,
singletons and multiple same-page endpoints, post-frame typed insertions,
current Korean/empty titles, pre-replay budget refusal, and two independent
Windows processes that both miss the receipt before entering the writer.
Preparation's 12 cases and this writer's 13 cases are 25 distinct final tests.
The earlier 10-case writer pass and the competing-title fixture failures are
retained separately; they are not additional final passes. Exact logs and source
hashes are in `outputs/legacy-adoption-20261002/phase-evidence.json` in the workspace
artifact directory. No browser, native Notion legacy adoption or operating
transition was performed by this backend packet.

## Reviewed request and recovery

The same-origin JSON API is independently gated and has no legacy write fallback:

- `POST /api/notion/workspaces/<workspace>/synced-legacy/candidates`: an explicit
  anchor and chosen document; current authorized flat candidates from that page,
  100 per page, with cursors bound to actor/workspace/source state. Content or
  rich markup differences and nested shapes are displayed as unavailable. It
  does not crawl every member or infer an original.
- `POST .../prepare`: explicit original and references, read-only review hash,
  current text preview and selected-document count.
- `POST .../intent`, `.../adopt`, `.../result`, `.../cancel`: exactly `origin`,
  `references`, `preparation_sha256`, `operation_id`. Clients cannot submit SQL,
  CRDT updates, prepared bodies or an intent-bypass option.
- `GET .../requests`: current exact owner/access, a single snapshot of pending
  and terminal records, stable pagination and an actor/workspace-bound cursor.
  This supports recovery when no local browser record exists.

Intent registration does not change document/workspace versions or body frames.
Only one request for the old sync group can remain pending. New writes recheck
the immutable review and registered intent inside the atomic writer. Commit and
cancel share one terminal operation fence, so concurrent processes return the
same outcome. Cancellation cannot undo a completed conversion; it prevents a
late registration/dispatch from applying an uncompleted request. Stale requests
remain visible until explicitly cancelled, rather than silently receiving new
operation IDs. Exact terminal replay precedes source-shape preparation, including
a second receipt check if another process finishes during outside-transaction
replay. Current endpoint access remains required for all receipt reads.

The owner UI opens from a legacy block's menu. Original radio buttons initially
have no selection. Page-by-page checkboxes select references explicitly; review
shows the preserved original and exact counts before confirmation. Only selected
flat endpoints are included. Dirty editors are flushed with existing composition
and input guards; late modal/workspace responses cannot dispatch a different
selection. The browser saves an immutable request before the first mutation,
coordinates same-operation tabs using Web Locks, and retains uncertain requests
for result/retry/cancel. It can accept a server-discovered request through the
workspace's recovery menu without inventing a replacement operation.

The final browser packet covers light 1280px and dark 390px, explicit origin and
reference selection, disabled divergent candidates, read-only review, a lost
committed response or lost intent response, cleared local operation records,
server-history recovery, exact result/cancellation, unchanged omitted content,
and opening/reloading the adopted reference. All screenshots are visually
reviewed; the dark preview override was corrected after finding insufficient
contrast. The final preview contrast exceeds 4.5 in both themes.

This UI/API packet does not convert actual user data, activate its operating
capability, discover a complete historical group, or remove remaining copy
guards. The normal logged-in page's current asset reflection and preserved
13-block content are recorded separately from the isolated adoption workflow.

## Joining a previously designated original

The caller still chooses the exact original and reference endpoints. For a
later join, at least one legacy reference must be selected. The original must
currently be a flat explicit original with the group ID derived from its own
document/block identity. A current marker or equal text alone is insufficient:
the server must find the current actor/workspace's immutable committed initial
designation receipt for that exact endpoint and old legacy group. Join receipts
are excluded from this initial proof, so adding another member does not replace
or alter the proof used by other pending selections.

New initial receipts retain the old legacy group ID. For receipts from the
earlier public API packet that lack this field, the server can derive it only
from the exact matching committed registration intent, including the original,
references, operation and preparation hash. Immutable ledger rows are never
backfilled. An older internal receipt without that field or a matching intent,
an unrelated explicit original, or an ambiguous historical group is refused.
The frozen plan binds the exact receipt/intent proof and revalidates it together
with current owner, edit access, lock, document state and frame hashes before
commit. Forging or rehashing captured proof cannot enlarge the selection.

Each chosen legacy reference must have the proven old group ID and exactly the
original's current text and shared rich properties. Divergent text or markup is
unavailable for selection and cannot be overwritten. Existing references are
not legacy candidates. A review that becomes stale is refused; a concurrent
terminal result is checked again before reporting that refusal. Normal exact
retry, request history and cancellation retain their original operation IDs.

Only the selected references are rewritten. If the original is on a different
page from every selected reference, its complete raw page metadata, SQL block
rows, durable frames and revisions remain unchanged. No no-op delta or page
save event is generated for that original. Its receipt entry reports
`unchanged: true` and `update_frames: 0` with the captured current witness; the
join's primary terminal event is workspace-scoped, avoiding reuse of an
unchanged document sequence. All reference writes, audit, receipt and outbox
still commit in one transaction. A same-page join changes that page once and
retains the original block's raw row.

Delta preparation runs the existing body engine only for changed documents.
Independent replay still validates all selected documents. An original with
already validated canonical-only insertions after its durable projection is
retained without materializing that read-only page during the join. This does
not permit any deletion, reordering or mutation of its projected blocks.
If a canonical insertion precedes a newer server-authored frame that omits it,
the existing strict source contract does not classify it as a post-frame insert.
Joining is refused and all canonical content is retained. Reconciliation of
that broader typed-command/server-frame combination remains separate work.
The separately verified native child-creation parent-insert contract now
prevents new instances caused by creating a child after a proven post-frame
insertion. It does not automatically repair older divergent streams.
The subsequent page-link lifecycle packet applies that same bounded prevention
to ordinary move/copy/archive/restore parent links, with independent body/title
and placement verification. At that historical page gate, database/batch link writers had not opted in. The later `database_link_insert_contract.md` and `database_row_page_parent_insert_contract.md` packets cover those writers;
the older divergent-stream guard above is unchanged. See
`page_link_parent_insert_contract.md` for its separate evidence.

The page-by-page candidate UI labels a proven source as `기존 원본 사용`,
disables its reference checkbox, and requires a selected legacy reference before
review. The preview explains original preservation and the confirmation reads
`기존 원본에 연결`. The existing immutable request controller recovers a lost
committed response after reload by checking the exact server result. Current
authorization and endpoint access remain necessary for receipt disclosure.

This extension covers explicitly selected flat document-body members only.
It neither discovers a complete historical group nor adopts archived, nested,
database item or template scopes. The capability remains false by default and
normal copy guards remain. Synthetic route/UI verification and normal app
asset reflection are recorded separately; operating data and flags are not
changed by this packet.
