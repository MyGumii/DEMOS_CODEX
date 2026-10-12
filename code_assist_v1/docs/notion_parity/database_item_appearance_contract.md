# Database item appearance contract

This increment provides canonical item appearance storage and its client picker,
cover positioning and uncertain-save recovery. The original R1–R11 and approved
copy/synced scope remain active; production rollout is not verified.

## Reference behavior

Actual Chrome inspection on 2026-09-22 opened the existing reference database
item at `https://app.notion.com/p/A-3dd9e69c1dfb80d8b3e6f18f7eeb9015`.
Its full-page header has add-icon, add-cover and customize-layout actions above
the title. Properties precede the comment area and body. Layout customization
opens a preview with an apply-to-all-pages action, compact/tab structures,
property-icon and full-width switches. No setting was applied or comment sent.
These DB-level layout choices must not be stored as per-item appearance.

## Canonical ownership and rollout

The offline `notion_workspace_row_appearance_migration.py` successor moves the
exact 2418 schema to 2419. It adds only
`workspace_collaboration_database_row_appearance(row_id, appearance_json)`.
The row ID references the original database row with cascade deletion. There is
no new page, grant, title, membership or independent authority for the item.
Existing rows have no appearance record; snapshots retain their old shape and
hash until an explicit appearance command changes them. Historical schema and
migration modules remain frozen.

Migration requires the exact reviewed plan, verified backup and quiescence.
Only fresh synthetic test catalogs are migrated by this development packet.
Production schema, feature flags and operating app 10322 remain unchanged.
The explicit successor gates preserve body editing, native team creation,
navigation, files, history and workspace images. Page-copy capability stays
false until its separate full-fidelity requirements are satisfied.

## Command and values

Use the existing source document database command endpoint and the existing
`code_assist.notion.collaborative_database_surface_command.v1` receipt:

```
command_kind: database.row.appearance.set
database_id: original database ID
target_id: original row ID
payload: nonempty subset of icon, cover, cover_position
expected_target_version: positive current row version
expected_document_sequence: current source document sequence
expected_workspace_epoch, expected_document_epoch: current access witnesses
operation_id: caller-supplied immutable retry identity
```

An item needs current `edit_content`, `edit` or `full_access` authority on the
source. Changing item appearance does not grant schema editing. A locked source
page rejects both new appearance commands and replay. Replay still requires
current read/edit authority and epochs; the same operation with changed input
is rejected. Source database and row binding is checked inside the transaction.

`icon` uses the shared emoji/vector/color/file representation and validation.
`cover` uses the existing image URL or gradient representation. `cover_position`
is a finite number from zero to one; booleans are rejected. Defaults are an
empty icon, empty cover and crop position 0.5. A new or cleared cover resets the
crop to 0.5 unless the command explicitly supplies another position. Partial
updates preserve the other fields. Arbitrary settings, title and body keys are
rejected.

Uploaded icon bytes are checked outside the write transaction for size, type,
checksum and ownership by the same source document. Inside the transaction,
the active file identity, content hash, size and MIME type are rechecked before
storing its reference. This does not authorize a file from another page.

The appearance record, row version, source sequence/content hash, event, audit,
outbox and immutable receipt commit together. Failures roll back all of them.
The receipt carries the complete normalized `appearance` and exact next row
version. A read returns appearance only when it actually exists, under the
source database's existing read authority and `row_appearance` capability.

## History and preservation

Checkpoints and ordinary page saves preserve item appearance with row bodies.
If database structure and property cells still match, a history restore can
restore body and appearance together while incrementing the row version once.
Restoring a pre-appearance checkpoint removes the appearance record. A deleted
row or changed database structure/cells still rejects the entire restore; it
must never report partial success. Full deleted-row history restoration remains
work. Item appearance does not alter property cells or the source page's own
icon, cover or body.

## Client integration and evidence

`notion_database_item_appearance.js` keeps one immutable request in an
actor/realm/workspace/document/database/row scoped journal before HTTP. Recovery
does not automatically submit it. Exact receipt identity, complete normalized
appearance and next versions are required before removing the request. A failed
storage write sends nothing; late completion cannot update another active page.
Malformed entries require explicit scoped discard. Permission and source checks
run again after asynchronous persistence. Body, property and appearance saves
are serialized, and the detail body is inert while appearance recovery is pending.

The view uses the shared emoji/vector/color/image picker, six gradient covers,
HTTP(S) image URLs, vertical range/drag positioning, cancellation and removal.
Side and full views share values. A full pane cover spans its scrollport; the
writing area keeps its usual width. Icons project into table/board/list/gallery/
calendar/timeline and the full item breadcrumb without becoming database cells.
Linked/read-only rows display appearance without exposing mutation actions.
Cover drag is excluded from body marquee selection. Disposed view listeners and
pointer capture are released. Uploads use the existing source-owned file route.

Chrome on synthetic port 14557 verified emoji/vector/color selection, gradient
and URL cover save, keyboard crop save/reload, pointer crop preview and Escape
restoration/focus, cover removal, table and board icon dimensions, full/side
views, exact after-commit response-loss recovery after reload, combined body/
status/icon persistence, row isolation and locked-page controls. A full-view
drag from outside the writing area across the title selected nine body blocks,
including headings, nested blocks and tables; it excluded the item title.

The final Node run passed 81 tests (12 new appearance cases included). The
synthetic catalog audit matched nine HTTP attempts to eight unique commits
(seven appearance, one body); the lost response replay had identical request
and receipt. Only the deliberately edited status cell and body paragraph
changed; other rows, memberships and source bindings matched baseline.

Follow-up Chrome verification on synthetic port 14559 opened the correct item
from list, gallery, calendar and timeline and verified both row icons. Gallery
image and empty covers now have equal 120px heights and aligned title starts;
the stored 70% image position is preserved. A 900px viewport had no document
horizontal overflow. Switching immediately after closing an unchanged item
now resolves the current block rather than a pre-refresh object. The regression
also covers a refresh after button rendering and a view removed before clicking.
The related Node run passed 52 tests. Twelve successful view updates left all
rows, cells, bodies, appearance, memberships and source bindings unchanged.

File chooser upload was attempted but Chrome denied setting a local file;
end-to-end upload remains unverified pending extension file-URL access. A held
marquee screenshot, prolonged edge scroll, other roles and full view/theme/
responsive combinations remain open. Focus preservation across later automatic
page saves also needs separate coverage. DB-wide layout, comments, embedded
resources, full copy/export/reference support and production migration/rollout
remain separate required parts of the full objective.
