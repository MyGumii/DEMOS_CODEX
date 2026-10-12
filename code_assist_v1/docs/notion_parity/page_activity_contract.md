# Current page edit time

The upper-right edit time must follow acknowledged collaborative body changes
and remote body updates. It must use the canonical server timestamp, preserve
local text/IME, and not treat an activity read as a newer body snapshot.

`GET /api/notion/workspaces/<workspace>/pages/<page>/activity` is a read-only
collaborative endpoint. It returns page/workspace IDs, document sequence,
workspace/document access epochs, `updated_at`, and nullable `last_edit` metadata.
A single read transaction
rechecks the active identity and current page/ancestor/workspace permissions.
Archived or unreadable pages, and mismatched workspaces, return the ordinary
404 response. Both success and error responses are `no-store`.

`last_edit` contains the editing actor subject ID, edit document sequence and
server timestamp from the newest recognized document-edit event. Body/typed
commands, database changes, appearance/settings, history restore, file changes
and lifecycle edits qualify. Comments, permissions, checkpoint/compaction and
unrelated activity do not replace the editing actor. The query reads only event
metadata, bounded by the authorized document sequence in the same snapshot.
No known edit event yields null, never a guess based on the document owner.

This read uses existing canonical document and event metadata. It does not load blocks,
replay Yjs, project workspace titles, write receipts or migrate a schema. The
existing ACK wire contract is unchanged.

The browser batches activity invalidations for 750 ms without resetting the
timer on each keystroke. At most one activity batch runs at a time; updates
arriving during a read schedule one later batch. An idle poll schedules none.
Only invalidation IDs cross the existing same-subject/workspace broadcast
channel; receivers reauthorize their reads and do not echo the notification.
The activity message has its own contract so older title-only clients ignore it
instead of loading the whole workspace on every body activity batch.
Body activity does not trigger the full workspace/title refresh path.

Returned IDs, navigation scope, document sequence and known access epochs gate
application. Activity reads and list refreshes share a timestamp high-water
mark. `activity_document_sequence` never advances the loaded body/DB snapshot's
`document_sequence`. Applying the result changes timestamps and their labels,
without rerendering editable blocks, replacing titles, or clearing dirty state.
Menus read activity when opened and, while visible and current, once 15 seconds
after the previous read settles. A slow read cannot accumulate concurrent menu
polls. Closing the menu stops its timer; hidden tabs do not issue menu reads.
The row and topbar footer show the known editor's display name (or exact subject
ID when a name is unavailable) and the paired edit timestamp. Ordinary pages or
older responses without author metadata show a neutral time label; they do not
claim the owner performed the last edit. This does not add missing historical
author data to the ordinary storage format.

Footer refresh preserves search text, selection, focus and menu position. A
sidebar redraw reconnects an open row menu to the replacement button, restoring
its expanded state and current placement. Esc returns focus to that button.

Read failure retains the last confirmed time; subsequent activity or a normal
focus metadata refresh can retry. No client-clock timestamp is fabricated.

Validation covers the real durable body ACK followed by read-only HTTP, current
guest/revoked/archived-ancestor access, continuous activity batching, remote
frames, scope/sequence/epoch races, and IME/dirty/outbox preservation. Actual
Chrome verification on 2026-09-23 covers row and topbar footers, a nonowner edit
followed by an owner comment, and real durable-protocol body changes from two
actors while menu search/focus remain intact. Sidebar width-save redraw at
190px reattaches the menu and preserves label/button separation. Full two-browser
editing and all ordinary/shared/role/theme combinations remain outside this
bounded follow-up.
