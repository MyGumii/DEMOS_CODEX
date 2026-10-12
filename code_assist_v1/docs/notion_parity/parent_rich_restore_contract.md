# Exact current-tail restoration during parent link operations

This additive successor covers `block.insert` with `restore_deleted: true` after
the latest bound current-generation Yjs frame. A deleted paragraph can be absent
from the current Y.Array and Y.Map; a later child/page/DB/row link operation must
restore its requested body and, where those fields match the deletion archive,
the original attributed Y.Text. It must not silently create an unformatted
replacement for a rich block whose recovery bytes are available.

## Immutable source and bounded capture

The authorized reader binds the exact document, generation, restore command and
latest preceding deletion command. The restore result must name that deletion;
the deleted version must agree with its recovery record and the restored version
must increment it exactly. Repeated delete/restore cycles use the latest deletion
receipt while keeping the first immutable tombstone unchanged.

The deletion receipt records the current generation and exact CRDT update high
water. Recovery chooses the latest bound snapshot at or before that high water,
then bound updates up to that same point. A newer snapshot containing the removed
body cannot replace the deletion archive. SHA-256, encoding, final high water,
scope and generation are checked. Snapshot/update lengths are checked before
loading oversized blobs. A per-range limit of 4096 updates plus one snapshot and
a per-document total of 8 MiB/256 restorations bound recovery work. Existing
current-document/copy-source budgets also include this preparation data.

The witness is internal service data. No client-provided archive, repair proof,
order or recovery range is accepted by the mutation endpoint. Replay, cloning and
independent attributed-text verification happen after the SQLite reader closes.
Existing actor, scope, generation, epochs, locks and exact source stamps are
revalidated by the service before the atomic delta/domain/receipt/audit/outbox
transaction. Late changes discard prepared recovery; rollback and exact retry
contracts remain.

## Restoration and independent validation

If an already-visible block has the requested type/content/props, keep its map,
Y.Text and later formatting. Otherwise, when the exact deletion archive's fields
match the restore payload, clone its original integrated map into a new map. This
preserves attributed text without changing or resurrecting the archived Yjs
identity. A certified restoration may replace an unordered orphan map; ordinary
insertion still cannot overwrite an unexplained map.

If the caller explicitly supplies different type/content/props, use exactly those
values with plain text. SQL-only deletion ranges with no frames have no archived
Y.Text formatting and also use those explicit values. A nonempty range whose
target is missing from the decoded visible body is refused. Later unexplained
canonical replacement of the restoration fields is refused before writing.

Python independently decodes the old body, deletion ranges and old-plus-delta.
Whole body, title, structure and exact link placement remain checked. The optional
rich search projection additionally exposes each visible Y.Text delta and the
title delta; it checks restored and surviving runs so an attribute-only mutation
is rejected even when plain content and props are unchanged. Default search
projection output and title-only/missing-body compatibility remain unchanged.

Native child creation, page move/copy/archive/restore, owned-DB lifecycle,
external linked-view retargeting, selected row-to-page placement and return reuse
the restoration witness. Pure copy source preparation returns exact restored
four-field descriptors and changes no original rows or frames. That descriptor
check does not establish attributed Y.Text fidelity for a new copied generation.

## Remaining scopes and evidence

A restoration that predates the latest frame is still outside this current-tail
contract. Historical restoration, historical DB restore placement/content, full
saves or other unexplained domain histories, nested/archived synced adoption,
DB item/template identity and instantiation, and the full R1–R11 plus C/D/E/F
operating review remain separate goal work. This packet does not activate normal
collaborative schema/flags or restart the normal app.

Packet: `20261003-parent-rich-restore`. The primary chat integrator records terminal
receipts, source hashes, counts, failed attempts and qualifications in the chat
workspace's `outputs/parent-rich-restore-20261003/phase-evidence.json`. Earlier
structural packet evidence remains historical and is not rewritten.

The additive [historical rich restoration successor](parent_historical_rich_restore_contract.md)
now owns certified restoration across later frames and preservation of newer
edits on proven restored maps. Its evidence is recorded separately.
