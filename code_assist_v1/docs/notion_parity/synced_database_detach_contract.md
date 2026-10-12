# Scoped DB body synced detach

Status 2026-10-03: supported staged row/template destinations detach one explicit
reference into independent content. The complete UI/extension goal remains active.

The frozen request remains exact origin, current witnesses and caller operation.
Preparation resolves the original and instance across page, row and template
bodies. It pins physical locations, versions, epochs, source stamps, lock state
and destination write roles. Row bodies require edit_content; templates require
edit. Source view access is revalidated. Document-wide IDs prevent cross-body
collisions. Source files and databases use the existing independent-copy remap.

The DB writer replaces only the reference slot, retaining unrelated typed body
representation and source metadata. It reconstructs and validates the prepared
body before the transaction and again inside it. Current target version, body
hash, canonical document hash/revision, DB activity, audit, event, outbox and exact
receipt commit atomically. DB destinations do not delete main SQL blocks or append
Yjs updates. Main-page destinations retain their established prepared delta path.
No Node/network/file copying runs within the SQL transaction.

Receipt replay precedes source capture and never needs the old reference to still
exist. Current destination page and DB/body access gate disclosure. Archived rows
and templates on active parent pages use the existing workspace-owner/legacy-owner rule; other actors are
denied. A DB receipt adds storage=database_body, exact location, target_version and
body_sha256 to the unchanged commit receipt. POST is first201/replay200, with exact
GET result recovery and no-store. Wrong body results cannot confirm the journal.

The body UI uses physical-owner endpoints and actor/workspace/page/reference
journals. It flushes shared text, excludes dirty whole-form drafts, and blocks
overlapping writes during detach. Lost responses recover the existing operation;
late results cannot refresh closed or replaced mounts. Confirmed results can open
their exact body. Pending global recovery distinguishes body opening from retries.

After ordinary body/template saves, an explicit after-flight refresh invalidates
any earlier GET and waits for the new reference witness binding before releasing
editing. Post-binding availability is checked against the current slot. Normal
refresh deduplication, source denial, hide cancellation and scope fences remain.

Final browser8 plus affected regression8 cover native owned row/template bodies,
linked row/template physical owners, light1280/dark390, ordinary follow-up saves,
remaining shared references and lost-response recovery. Final server58 covers all
36 core detach cases, nine receipt boundaries and13 commit/HTTP regressions; an
earlier88-case regression supplies the other75 unchanged preparation/delta cases.
The combined server case-ID union is133, without double-counting the13 overlap.
Node68 and syntax/hash evidence accompany the packet.

The authenticated normal personal runtime loads current assets, preserves its
existing title and13 blocks, and selects six body blocks from outside the writing
area while excluding the title and focusing the editor. Fresh Chrome Notion
observation verified the reference toolbar and its Unsync menu; no Notion content
was changed and no actual Notion DB-body detach was executed. Local reference
action placement still differs. Staged DB gates are not normal runtime activation,
fresh native IME proof, or full operating-release completion.
Whole-DB/parent-page archive and restore receipts remain subject to the existing
lifecycle marker contracts and their separate review.
