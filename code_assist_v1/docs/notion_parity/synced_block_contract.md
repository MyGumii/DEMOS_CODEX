# Explicit synced block authority boundary

Status (2026-09-23): current-authority hierarchical reads and passive reference
display, new flat original creation and same-page reference creation are connected.
The hierarchical follow-up has browser evidence and a completed targeted
24-case server recheck (normal and diagnostic execution). Earlier native failure
causes remain unresolved; the recheck is not whole-application completion.
The text-write API and owned-leaf inline editing controller are connected.
Origin-specific draft recovery and guarded logout are described below.
This is not the completed shared-editing feature.

## HTTP

`GET /api/notion/workspaces/<workspace_id>/pages/<page_id>/synced-blocks/<block_id>`
reads an explicit original or reference. The URL workspace must be the current
instance workspace; a different workspace is rejected before any body replay.
An external original can belong to another workspace only if the current actor
can read both documents. No access is inherited from the instance.

The response contract is `code_assist.notion.synced_block.v1` with `instance`,
`origin`, the derived original `sync_id`, the current original `block`, and the
document/workspace sequence, epoch and generation `witnesses`. Original content
is reconstructed from durable Yjs frames outside SQLite transactions. Both
documents' current access and witnesses are rechecked before returning content.
The existing 64 MiB preparation bound applies to both sources together.

Every success and handled failure uses `Cache-Control: no-store, max-age=0`.
Missing/denied documents return 404; a concurrent source change returns 409
`synced_source_changed`; unresolved explicit shape returns 422
`synced_shape_unavailable`, without source details. Ordinary runtime requests
return 404 rather than entering a legacy store. This route never writes bodies,
grants, revisions, events or projections.

## Display

`collaborative_synced_read` advertises read capability on the page payload. The
main editor preserves it on normalized page metadata, including embedded main
editors. Explicit references render separate noneditable display elements. Their
content comes from the GET response and the existing rich-text sanitizer; fetched
text is never assigned to instance content/props, the draft or its CRDT.

The controller binds each mount to the user, workspace, page, navigation sequence
and editor data. It verifies response endpoints and the explicit original
marker, aborts obsolete requests, limits concurrent materializations to two and
deduplicates overlapping refreshes. Hiding the document clears display state.
Reload, editor render, returning visibility/focus and the refresh button fetch
current content. A refresh clears previous text before a possible denial/failure;
the original-open button is shown only after a valid response. It navigates via
the existing page-resolution path, which enforces destination access.

Originals and references with an explicit `synced_from` marker are excluded from
the legacy same-page peer-copy loop. Backspace/Delete at adjacent text boundaries
select a reference instead of merging a text block into its empty storage body.
The old sync-id-only behavior remains available to ordinary existing blocks.

## Creating originals and references

New collaborative synced blocks created through slash/conversion/insertion now
declare `synced_from: null`. Converting ordinary text with the sync-copy action
retains its current content, rich formatting and layout, and creates a separate
empty reference pointing to that document and block. The original's identity is
not inferred from a legacy sync ID. A block duplicate of an explicit original
creates a reference to that original; duplicating an instance preserves its
original endpoint rather than forming a reference chain. The keyboard duplicate
paths and tab-group duplication use the same transformation. Ordinary page-copy
still has the different, previously verified independent-original semantics.

Only layout and color metadata accompany a new reference. It contains no copied
text, rich HTML or children. Source/copy descriptors are prepared before editor
mutation; malformed explicit references do not partly convert the source. New
explicit blocks do not acquire a legacy sync ID during normalization. Ordinary
mode and existing sync-id-only groups keep their compatibility behavior without
silently electing an original. Explicit origins/references cannot be converted
through the generic type menu while the proper unsync operation remains absent.

Eight creation tests and the selected editor/slash/clipboard/view regressions
passed together (12 scripts, 36 runner results, 491.865ms). In a fresh synthetic
schema-2416 application at port 14539, real Chrome created an original with a
reference through the block menu, duplicated that original into a second
reference, and created a separate original through `/동기화`. Reload retained
both originals and both references. Page copy produced independent identities;
editing the copied original to C updated the copied references, while the source
retained A. Both pages were reloaded. Read-only current Yjs and authorized synced
reads confirmed empty stored reference content, exact endpoints, one independent
copied group and an unchanged source since the final pre-edit baseline.

One early browser input was sent before navigation to the copy completed; it
correctly edited the still-open source. It was restored through the UI and a new
baseline was captured before the successful independent-copy check. That attempt
is not counted as proof of source preservation across page-copy navigation.

## Automatic reference refresh

Visible full-page references now observe both the source and instance documents
through the existing authenticated WebSocket ticket and `live_replay`/`drain`
protocol. Observers are shared by document within a mount, including one observer
for same-page references. They only read version/epoch information; transport
payloads never supply displayed text, and observers send no edits or presence.
The dual-authority synced GET remains the only source of displayed content.

The observation interval is 650ms after a completed drain, not instantaneous
server push. A changed version or disconnected observer removes old display
content before reauthorization. A silent transport times out after 10 seconds;
ticket/socket reconnect uses backoff from 250ms to 10 seconds. Recovery fetches
current content. These bounds do not guarantee a matching Notion latency.

Responses must include exact source/instance witnesses. Slot revisions prevent
an older in-flight GET from rendering after a newer notification, and a returned
version behind an observed version retries after 650ms. Materializations remain
limited to two concurrent requests per mount. Hiding, disposal and remount cancel
requests, timers and observers; stale callbacks cannot update a new page.

Verification on 2026-09-16: 12 selected Node scripts passed with 42 runner results
(616.743ms), including 12 new live-observation scenarios. A fresh synthetic full
app on port 14542 and real Chrome displayed two references A → B without reload,
focus changes or refresh-button actions. A second client applied actual Yjs
updates through the test transport. Archiving the source through the fixture's
service command automatically cleared both references and original-open buttons;
restoring it recovered B, and another edit displayed C. Reload retained C.
The instance body, empty reference storage and endpoints were unchanged, with
instance document sequence fixed at 4. The original was restored; all three
fixture documents remained active. No browser console errors were observed.

The source's initial raw Yjs-only test snapshot had no frames. Its initial A was
verified through the authorized synced GET, which includes the canonical seed.
That raw snapshot is not evidence that the source initially had no blocks.

## Remaining behavior

- The current UI is a read view. Editing at the reference location remains.
  Automatic authorized refresh is now implemented as described above; exact
  Notion latency and an actual guest-grant revocation browser scenario are not
  claimed. Source/instance denial and recovery are covered by controller tests;
  the browser scenario above covers source archive/restore.
- Inline/flat hierarchy display is implemented in the follow-up below. Nested
  external synced references, live databases, complete media/embedded-resource
  fidelity and old nonembedded side-editor integration remain. The latter still
  directs references to the full-page editor.
- Cross-page reference creation/clipboard, wrapping arbitrary nested block groups,
  unsync conversion, complete block editing semantics, legacy-origin adoption
  and template instantiation still need their own integration. Do not advertise
  full synced creation/editing or enable canonical page-copy UI on this evidence.
- Initiating source trash/restore through the browser's native confirmation is
  still unverified. The automatic reference reaction to another client's service
  archive/restore was verified above; it does not verify that native dialog.

## Evidence

- `test_synced_block_http.py` (5) plus `test_synced_copy_references.py` (8):
  13 passed in 135.09 seconds. Existing synced tests cover real Yjs updates,
  copy/history, concurrent source changes and current grant revocation.
- `test_notion_synced_references.js` (8) plus existing page-activity, sidebar and
  document-preview scripts: Node reports 11 passing tests/scripts. The component
  checks late responses, scope changes, hidden views, endpoint mismatch, access
  denial, bounded concurrent reads and protection against peer-copy/merge writes.
- Fresh schema-2415 synthetic full app at port 14535: two references displayed
  current A; original-open navigated correctly; a browser edit to B was read from
  both references after returning; another tab edited C and the refresh button
  displayed C in both references. Read-only durable-body verification found C in
  the original and empty content/unchanged origin pointers in both instances.
- The source fixture at port 14534 was neither migrated nor restarted. Its
  three protected original documents and three native pages passed the existing
  read-only verifier. The earlier clock change was observed crossing from
  '2 hours ago' to '3 hours ago' without reloading the page.

No operating catalog, migration, membership or feature flag was changed.


## Hierarchical reference view — 2026-09-23 (progress, test gate incomplete)

The read response now adds `tree`, an owned subtree from the current original
body. Existing `block`, endpoint and witness fields retain their contracts.
`synced_tree.content_tree` follows flat indentation within layout/tab scopes,
inline child descriptors, explicit child IDs and tab-controller ownership.
Explicit child ordering wins; unrelated sibling/column blocks are excluded.
Duplicate identities, unresolved/shared explicit children, cycles and more than
64 nested levels fail without a partial hierarchy. Node props are copied and
inline children are moved to the tree's child list; source data is untouched.
Extraction occurs before the existing final dual-document authorization and
sequence/epoch/generation recheck, so a changing/denied source cannot leak an
already prepared subtree.

The dedicated passive renderer shows current rich text, headings, callouts and
their child bodies, native expandable toggles, lists/checks, simple tables, tabs,
columns, code, equations and authenticated file references. It uses the existing
rich-text sanitizer and color tokens. Tab clicks and arrow/Home/End navigation
are local view actions. The source hierarchy never becomes instance/editor CRDT
content or editable inputs. Local fold and tab choices survive authorized live
refresh per reference and clear on denial/disposal. Root position is independent
of its source-page indent; child indentation gaps and local visual offsets remain.
The old server response fallback retains its root text, without inventing children.

Actual IAB: two references display ten source blocks (heading, colored callout,
nested rich text, closed toggle/child, simple table and both tab panels). A source
sibling sentinel never appears. Opening a toggle and changing tabs works; arrow
navigation changes tab and focus. A real remote Yjs edit changes a hidden child
to B, then C. After the view-state fix, first-reference open/second-tab choices
survive C while the other reference remains closed/on its first tab. Archiving
the source through the named fixture's service clears both entire subtrees and
original-open actions; restore reauthorizes and displays them again. This is not
proof of initiating archive through the native browser confirmation. Reload and
390px view retain current C, document width 390 and body font 15px; captured tab
console warnings/errors are empty. The final pure indentation-gap adjustment
was made after this browser pass and checked by its Node test, not rerun in UI.

Named synthetic fixture only: localhost14583, three documents. The instance's
body and empty stored references/endpoints remain identical at sequence4; source
only changes the explicitly scripted child and ends active at sequence18. The
other source blocks remain equal, tree contains exactly ten expected IDs, SQLite
integrity and foreign keys pass. Server session97230/PID30652 exited0; ports
14583/6033 are closed. Production auth/data/migration/flags were untouched.

Browser IAB1 disconnected and reappeared as IAB2. Reacquired original test tab
crashed on reload, leaving a browser-generated data-URL error page. Browser URL
policy prevented inspecting/closing that error tab; no bypass was attempted.
A fresh allowed localhost tab completed the checks and was closed; viewport reset.
Chrome remained absent and no new actual Notion comparison is claimed here.

Validation must not be summarized as a fully green server suite:

- Final four Node files: 34 passed, zero failed/skipped, 231.6526ms, exit0;
  syntax checks pass. Rendering, escaping, inactive panels, authenticated file
  URLs, unresolved-reference isolation, local fold identity and indent gaps,
  existing creation/live/controller behavior are covered.
- Initial Python selection:22 passed/1 failed179.45s. The new HTTP fixture had
  inserted typed children after native child creation bootstrapped old CRDT;
  moving the complete seed before that transition fixed the harness. The new
  focused suite then passed10 tests33.09s with exit0 (before adding revocation).
- Final24-case attempts did not finish cleanly. One crashed during import/GC
  with Windows access violation (-1073741819). Another reported23 passed/1 setup
  error172.70s: temporary migration stage hash mismatch, then heap corruption
  exit (-1073740940). The subsequent isolated new-test process also ended with
  heap corruption. Cause is unresolved. No migration integrity check was relaxed,
  no historical migration edited and no crash treated as a pass. The final clean
  server suite remains an explicit gate, recorded in synced-tree-server-validation-pending.json.

Still open: reference-location editing and unsync transaction/permission/UI
integration, cross-page creation/clipboard, arbitrary group wrapping, legacy
adoption, nested external references, canonical live DB/resource completeness,
local fold-state persistence across whole-editor remount, focus during refresh,
all-role/theme/complex-layout parity and operating rollout. The overall original
R1–R11 + approved copy/synced goal remains active; this is not feature completion.


## Follow-up validation — 2026-09-23

Without product-code, dependency, migration or integrity-check changes, the final
three targeted server test files passed24/24 with exit0 in both diagnostic
(PYTHONMALLOC=debug, -X dev;220.87s) and normal (faulthandler only;183.39s)
runs. New subtree tests independently passed11/11 in49.99s. The interrupted
diagnostic run60338 has no verdict; its handle and processes disappeared on tool
refresh and the separate replacement14055 completed. Earlier native crash logs
remain authoritative failed attempts; their cause is neither established nor
fixed by these successes. This completes the final targeted clean-test gate,
not the entire server/app or the pending feature scope.

Actual IAB now loaded final renderer tree3/main2. Two independent ten-node
references show current child C; local open/second-tab versus closed/first-tab
choices are independent, display has zero editors, 390px has no overflow and
console warnings/errors are empty. Original page outside-column/title-crossing
drag selected five eligible visible blocks and excluded title/closed child;
release cleaned the rectangle and Escape cleared selection. Sidebar row menu
Escape restored focus. Held-drag frame and a dedicated new indent-gap fixture
remain unverified. Chrome absent, no new Notion comparison.

The exact existing synthetic catalog was resumed, never reseeded/migrated.
First strict whole-dump preservation failed after connection tickets were issued.
A read-only serialized dump excluding only newly issued ticket INSERT rows then
matched the original dump exactly. A first diagnostic DELETE against a disposable
memory copy was rejected by the immutable ledger; no trigger was altered.
Final resumed-run per-table comparison independently finds only the ticket table
changed, all document/body/permission/file/other tables identical, document
sequences4/18/1 unchanged, integrity ok/FK0. Server11476/session39074 exits0,
viewport reset and own tab2 closed. Prior interrupted server32676 was confirmed
absent; lost-browser error-tab cleanup is not claimed. All evidence is recorded
in the follow-up report and synced-recheck-files.json. Full R1-R11+copy/synced
scope and native-root-cause follow-up remain active.


## Text write boundary — 2026-09-23 (backend implemented, UI pending)

POST `/api/notion/workspaces/<workspace_id>/pages/<page_id>/synced-blocks/<block_id>/text`
accepts exactly `origin`, `witnesses`, `updates`, `operation_id`. Origin is the
explicit original document/block pair; witnesses are the exact one/two document
descriptors returned by GET. Updates contain exactly `id`, `content` and
`rich_text_html` (string or null). Null removes old rich HTML. The service caps
one to100 unique targets and256KiB of update JSON; the strict HTTP parser has
its existing64KiB whole-request limit. No arbitrary type, layout, child ownership,
file ID, source identity, checkbox/table/DB value or permission updates enter
this text boundary. It supports the root/owned text, heading, list, todo text,
toggle text, callout, quote, code and equation leaves. Code/equation require null
HTML. Nested external references require their own authority boundary.

Both pages require current edit_content access and must be unlocked. GET now
adds capabilities.edit_text based on this permission/lock check. It is an
authorization capability, not a claim that the current UI can edit every shape.
Source identity and the complete owned tree come from durable replay; clients
cannot use a sibling ID to edit outside that tree. Node prepares only text and
rich-HTML changes outside SQLite readers/writers. Flat Y.Text leaves and inline
child descriptors are supported. Initial empty generation1 uses the same
deterministic bootstrap as the real editor bridge. Incomplete or unsupported
replay cannot be committed. A1MiB encoded update bound remains enforced.

The writer rechecks both exact prepared stamps and all client witnesses before
appending one original-document CRDT update/binding, document sequence, immutable
receipt, audit, event and outbox in one transaction. The instance is never a
copied content cache. Receipt replay uses the exact request hash and fresh
permissions/locks, while accepting the already committed operation despite its
old version witnesses. Overlapping identical preparations commit once. A
namespaced durable update ID separates this service from browser transport
operation IDs. The same namespace is used in event/outbox records and their
payloads; the public receipt and audit still contain the caller's operation ID.
CanonicalAuthority._finish accepts an optional event_operation_id and preserves
the existing default for all other callers. An initial refinement missed the
event key and failed its collision regression (15 pass, 1 fail); that failure
is retained in synced-edit-python-namespace.log.
HTTP success is201 and every handled result/error is no-store; ordinary runtime
returns404 without entering legacy storage. Internal fault injection is not an
accepted HTTP field.

During wire-format tests, replacing an emoji sharing a UTF-16 surrogate prefix
reproduced replacement characters. commonTextEdges in the existing collaborative
editor now moves both prefix/suffix boundaries outside surrogate pairs. The new
server delta uses that same helper. Main script asset version is unicode-text1.
Real editor-bridge sender/receiver tests cover shared prefix/suffix replacement,
and the service bootstrap is merged with the actual editor bridge's seed update.

Validation: final Node40pass0fail422.9194ms; new Python initial11pass142.91s.
The combined old/new39-case server regression passed before the final request-ID
namespace refinement; the final service and shared helper were then checked
with all16 new write cases plus9 existing durable transport cases and4 disposable
authority baseline cases:29 passed.
Times and exit codes are recorded in synced-edit-results.json.
Coverage includes HTTP origin-only mutation, empty instance, exact retry/hash
conflict, same-page/one witness, flat child versus sibling, one-sided grants,
read-only/locked pages, permission revocation and source update during prepare,
overlapping identical requests, every transaction fault stage, and collision
with an existing browser-transport operation ID. These are scoped tests, not
all-role/cross-workspace/browser/production completion.

Still pending: actual reference inline editing controller, IME/draft/focus/live
refresh/conflict/undo behavior and browser verification, structural edits and
special types, unsync, cross-page creation/clipboard, all other original gates.
No new actual Notion comparison or product-browser proof is claimed for this
packet. Prior native-memory failure root cause remains open, even when current
tests pass; no runtime/dependency/migration/integrity changes were made.


## Inline text UI — 2026-09-23 (implemented, broader editing pending)

The previous backend-only/read-only status is superseded for owned textual
leaves by notion_synced_text.js and the main reference mount. A reference now
uses the authorized text POST without adding body content to its stored block.
Encrypted actor/workspace/page/instance drafts and exact pending requests are
serialized before send. Automatic save waits650ms, composition prevents send,
and lost responses keep their operation ID. Input arriving during a write is
sent afterward against its acknowledged base. A fresh read can safely advance
witnesses when the edited leaves did not change; same-leaf conflicts retain the
draft and expose latest/draft text with explicit rebase/discard actions. An
uncertain request cannot be discarded before its receipt is resolved.

Focused text DOM/native undo survives ordinary reference refresh; a focused
reference defers unrelated whole-block rendering until blur while its scope and
endpoint remain current. Known denial clears displayed source; inaccessible
drafts can be durably parked before navigation. Navigation, logout and unload
are connected to pending text state. Rendered content is sanitized using the
existing rich text path; code/equation requests remain plain text. Default
read-only renderer behavior remains available when the editing host is absent.

Seven Node files53pass0fail457.5347ms, including13 new state cases. Actual IAB
tested five successful writes, two references, native undo/redo, focus, rich
child text, source archive/restore, reload and390px. Source diff is exactly
root+heading+nested text; reference body and all other source blocks unchanged.
SQL integrity ok/FK0. Scripts inline4/CSS inline6 loaded and visually checked.
No new Chrome/Notion comparison, no actual Windows IME proof or broad browser
fault matrix. Structural editing/Enter semantics, unsync, clipboard, special
types and all original R1–R11/copy/operating gates remain pending. Native crash
root cause remains unresolved. See the Korean inline report and
synced-inline-preservation.json for scoped evidence, not full feature completion.


## Origin recovery and logout — 2026-09-23

Each actor/workspace/page/reference journal holds one encrypted entry per
original endpoint, using an origin-hashed entry ID. Legacy operation_id=draft
records remain readable without eager deletion. Only the current authorized
endpoint can render or send. Detached edits and exact uncertain requests remain
parked and restore when the reference returns. Stale input/menu origin arguments
and navigation generations fail closed. A parked notice exposes no old text.

Logout retains clear-subject semantics but asks before removing remaining
offline records. Cancellation preserves records/key. After confirmation, one
transaction compares the same encrypted snapshot and removes records/key.
Key tokens (added without rotating legacy keys) fence the final outbox write,
so encryption begun before logout cannot save using a deleted key.

Final Node60 pass0fail803.788ms; real browser IndexedDB/Web Crypto11 checks,
including a reproduced/fixed late-encryption failure. Synthetic full app tested
A→B→A draft retention, logout Esc/cancel, rejection/version conflict, and
committed-response loss recovered from a fresh tab. Exactly two new source
receipts, final source sequence25; retry creates no new write. Reference body
restored. Fixture B added and initially blank source CRDT title populated on
startup; both are explicit in the preservation record. Browser assets recovery2;
final obsolete-navigation guard in text recovery3 is additionally model-tested.
No new actual Notion comparison, native IME/full-role/offline matrix, unsync,
structural editing or entire parity/operating gate completion is claimed.


## Detach preparation — 2026-09-23 (internal, not an unsync endpoint)

synced_detach_plan prepares an immutable, hashed independent replacement forest
from the authorized owned subtree. It performs no content write, permission grant,
HTTP exposure or UI action. Every plan says commit_ready=false. A future writer
must resolve all resources and placement and revalidate in its transaction.

The reference ID survives on the converted root. For an empty wrapper with
children, the first surviving child instead keeps this ID. The removed root and
promoted child intentionally share one source-to-destination mapping; the returned
forest itself has unique IDs. Internal anchors to both resolve to a surviving
block. Whole-page and unrelated links stay unchanged. Nonempty/rich wrappers and
empty leaves remain paragraph blocks. Removed wrapper properties are explicitly
retained for inherited presentation; source and destination placement are separate.
Block/layout/tab IDs are deterministic per actor/workspace/target/operation, and
tab IDs are scoped to their controller. Nested owned synced originals/internal
references are remapped; references to the outer original remain references.

Files, canonical databases, child pages, linked databases and nested references
are explicit dependencies, never silently omitted. Linked source identity and
view settings remain while local view IDs change. Cached database rows are not
canonical copy data. Full resource duplication, flattened/tab/column placement,
CRDT writes, receipts/audit/outbox, retries, draft coordination and UI remain open.
An original endpoint is rejected here; unsync-all requires a separate contract.

Preparation and revalidation need source read and destination edit_content plus
an unlocked destination. Source lock/read-only access permits preparation. The
existing current dual-authority source stamps and exact witnesses fence changes,
revocation and same-page references. No permission result is a cached grant.

Initial16 tests passed, followed by31 including shared remapper regression.
After the forest/anchor refinement, all22 detach cases passed. These overlapping
runs are not summed. SQL-dump comparisons cover read-only preparation and failed
revalidation. No browser unsync or new actual Notion comparison is claimed.
Official synced-block documentation informed expected independent content/link
retention; the Chrome connection was still absent in the available inventory.


## Shared database item copy dependency — 2026-09-23

Before wiring detach resources, page_copy_remap now scans and maps each canonical
row body in its own document/database/row scope. Tabs are controller-scoped in
page, template and row bodies. Row rich links, file IDs, image URLs and appearance
are remapped. Exact item_db/item_row links map page/database/row/body anchors
together; unrelated query bytes and external/ambiguous item targets are retained.
File URL query fields are not mistaken for navigation to an item.

page_copy validates complete row body/appearance metadata and the existing size
limits before insertion. Its shared _insert_database helper requires a transaction,
preflights row schema support, and inserts bodies/appearance at initial version1.
It does not invoke ordinary edit commands that would advance the row version.
The full page-copy service's schema/capability gates are unchanged. This helper
does not itself provide authority, receipts, audit or detach writes; those remain
the caller's service contract.

Seven pure failures and one real temporary canonical insertion mismatch were
reproduced before their fixes. Combined copy/remap/commit36 cases then passed;
after a file-query boundary refinement, the final15 related cases passed. The
temporary transaction roundtrip matches the prepared descriptor and FK checks,
and rollback restores the full database dump. There is no new browser/Notion
proof or complete unsync claim. Resource selection/revalidation, child pages,
placement and atomic detach writer/UI still follow; synced blocks inside row
or template bodies remain explicitly unsupported pending their identity contract.


## Scoped detach resources — 2026-09-23 (read-only capture)

synced_detach_resources accepts the frozen detach preparation and revalidates
both endpoints before collecting its original's owned subtree. The plan now
records application_origin to bind relative/absolute local file recognition to
the same trusted origin used during identity planning. Canonical databases form
an owned-block closure through item/template bodies; relations and external
linked sources never enlarge it. Repeated/cyclic database references terminate.
Replaceable block caches and archived templates/views are excluded from resource
discovery. Invalid linked-instance caches still fail the existing source contract.

Only referenced active files in the original document/workspace are captured.
The shared immutable blob verifier runs outside all SQLite transactions. After
verification, dual endpoint authority/stamps and exact full file metadata are
rechecked. The result is immutable hashed JSON and supports transaction-time
revalidation. It remains commit_ready=false and contains original resource IDs:
resource remapping, child pages, placement, atomic writer and UI are still pending.
References are recorded without fetching external bodies or granting access.

Final33 tests passed (11 resource +22 preparation). The first run had3 failures
from invalid fixture shape and seeding the parent CRDT before resource blocks;
these were corrected in fixtures without weakening source validation. Tests cover
read-only capture, exact selected resources, source-view/target-edit access,
transaction-free blob verification, source edit/revocation/file deletion after
verification, corrupt temporary bytes and unchanged SQL dumps. No new browser,
actual Notion, operating deployment or completed unsync claim is made.


## Detached resource mapping — 2026-09-23 (no writer or UI)

synced_detach_remap consumes the immutable resource capture and produces a
hashed replacement forest plus independent canonical databases and file records.
Primary block/DB/file IDs agree with detach preparation; row/property/view/body
IDs are bound to the destination, instance and operation. It reuses canonical
copy validation, row-body limits, formula aliases, provenance and initial versions.
Replaceable DB caches are removed. Source text and ordinary page links remain;
owned block anchors and item links follow their copied IDs, including same-page
detach. Wrapper metadata is rewritten from the source once to avoid alias remaps.

The shared private _Remapper accepts explicit partial_documents=False by default.
Only detach opts in: a missing original/database elsewhere on the same source
page can remain external, but mismatched ownership of a selected database still
fails. Full-page copy's existing strict validation remains the default. Explicit
same-page mappings no longer return before remapping nested anchors and rows.

Owned linked DBs point to the copied source and remapped view configuration;
uncopied sources and their filter identities stay unchanged. Nested main-body
synced endpoints retain the owned-tree planner's identity rules. Reference plans
record internal/preserved/pending_child_page states. Child pages are not silently
duplicated or discarded. Synced blocks inside row/template bodies still require
their pending original-identity contract.

Initial tests exposed fixture validation problems; the next run reproduced two
real issues (same-page URL early return and active_view_id overwritten by generic
metadata). Both were fixed. Final37 passed:10 detach cases +12 copy remap +15 row
resource cases. A real temporary source exercised capture/map/read-only SQL and
changed-source rejection. No actual browser/Notion or full write completion is
claimed. Target placement/colors, child pages, atomic CRDT/receipt/audit/outbox,
retry/draft coordination, UI and all original parity/operating gates remain.

### Child-page correction after direct Notion comparison — 2026-09-23

The earlier `pending_child_page` status above was a conservative preparation
placeholder, not observed Notion behavior. In a test page with an original and
one synced copy, creating a child page inside the original showed the same page
URL in both. Unsyncing the copy left the original `child_page` in place and
changed the detached side to a page-link arrow with the **same page ID**. The
official synced-block help also says page links continue to work after unsync.
The implementation now maps copied `child_page` blocks to `link_to_page`,
including those in copied database row/template bodies; it never creates a
second page document or claims a second parent. Reference plans record
`linked_existing_page`. This supersedes the pending-child-copy rule in the
earlier preparation section; the public route and UI still require separate
validation.


## Detached placement contexts — 2026-09-23 (preparation only)

synced_tree.content_hierarchy exposes the existing inline/ID/indent/tab ownership
index and ordered ancestors. content_tree delegates to the same unchanged tree
builder; no new persisted representation is introduced. Destination ancestry is
cycle/depth checked before binding a replacement slot.

synced_detach_placement binds immutable remapped content to its exact target
parent/sibling slot and body hash. Referenced context layers preserve destination
ancestors, instance placement, the removed source wrapper and each replacement
node separately. Source positions remain explicit so outer columns/tabs are not
overwritten by inner scopes. The empty wrapper's presentation is retained once;
its source location is not mistaken for a new destination column. Existing local
descendants of a flat reference are kept separately outside source presentation.
The replacement forest, resources and ID aliases are unchanged. Collisions,
changed endpoints/placement and tampered preparation fail. Transaction-time
revalidation delegates to the existing full authority/stamp/file chain.

This is a writer/renderer input, not a flattened editable body, storage format,
permission grant or public feature. commit_ready remains false. Renderer/structure
integration, child pages, atomic writes/receipts/audit/outbox, draft/retry and UI
remain open. Final35 tests passed:14 new placement +11 tree +10 remapping. A real
temporary capture remains SQL-read-only and expires after source edits. Fresh
browser inventory exposed only IAB, no Chrome; no current Notion/UI comparison
or full parity/operating completion is claimed.


## Native nested layout prerequisite — 2026-09-23

EditorCore.blockOutline now resolves flat ID ownership and tab controllers in
addition to scope-local indentation, computing inherited collapse/tab visibility,
visual offsets and ordered child indices. The native renderBlocks delegates DOM
placement to NotionBlockLayout: containers are parent-local, inherited columns
are reused, nested grids are scoped to their tab, and each direct column gets one
add control. Full-page DB viewing bypasses hidden ancestors/layout containers.
Selection follows actual descendants instead of numeric indentation across scopes.
Column-add and split/merge paths maintain explicit sibling edges; core clone and
remove paths remap/clean edges. Cross-parent/column text merging is not performed.

This is a flat native-editor prerequisite, not direct consumption of detached
placement contexts or a complete structural-edit contract. Inline child objects,
all move/indent/paste/AI/upload insertion paths and transient removed-wrapper
presentation still need integration. Detach remains commit_ready=false; no unsync
writer/UI is exposed. Existing permissions/schema/operating gates are unchanged.

Final Node37 passed (12 new cases). An isolated full app verified nested callout
columns, outer collapse/tab visibility, add+reload, Enter+Backspace, 7-body-block
outside-margin selection/title exclusion/Esc and 390px no horizontal overflow.
Typed seed versus final CRDT preserved all11 original texts/types, only expected
child-edge and expanded-toggle properties changed. Initial CRDT snapshot was
empty before materialization and was not used as preservation evidence. Temporary
tab/viewport/server/gateway were cleaned up. No fresh actual Notion comparison,
native IME or full R1–R11/operating completion is claimed.


## Native structural editor prerequisite — 2026-09-23

NotionBlockStructure now operates on the outline ownership graph for relative
move, same-scope sibling reorder, indent/outdent and forest insertion. Complete
descendants move together; destination outer placement is rebased while nested
independent column/tab identities survive. Serialization writes existing flat
children ID edges and validates ownership without adding a storage schema.
Native drag/drop, keyboard indent/reorder, structured/multiline paste and ordinary
block/tab insertion use this prerequisite. Rejected multiline paste is atomic
with respect to the original text/formatting. Clipboard flat input is encoded as
an ownership tree; flat decode adds explicit edges only where inference differs.

Final Node53 passed including 15 new structural/integration cases. The isolated
full app verified keyboard reparent/reorder, five-node collapsed copy into a
column, independent copied tabs, two-level outdent, native top-level drag reorder,
reload and outside selection of12 body rows/title exclusion/Esc. Actual native
cross-column drag did not visibly move and remains unverified; no Notion recheck
or mid-drag rectangle capture is claimed. Seed12 texts/types remain, final18
includes5 copied nodes and one app blank. Temporary app/gateway/tab are closed.

This is not a complete structural-edit/placement contract. Root multi-column
forests pasted inside an uncarried outer column are explicitly rejected pending
a representable placement wrapper. Inline child objects, remaining special
insertion/conversion paths, detached context consumption, child pages and atomic
unsync writer/menu remain. commit_ready=false and the complete original scope
and operating gates remain unchanged.
