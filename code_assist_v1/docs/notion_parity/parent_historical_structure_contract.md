# Certified older parent structure during link operations

This successor covers typed structural changes that predate the latest bound
current-generation Yjs frame. A later text-only frame can omit an earlier block
insert, deletion or move; native child creation, page/owned-DB lifecycle, linked
host retargeting, row/page placement and pure copy preparation must keep those
proven changes and the latest surviving text.

## Evidence and authority

The caller's existing authorized SQLite snapshot captures the earliest immutable
revision checkpoint in the exact current CRDT generation. Its revision payload
and checkpoint hash must agree with the recomputed canonical SHA-256. The anchor
body is bounded to 2 MiB/100,000 unique identities. At most 4096 subsequent typed
structural commands and 8 MiB of history payloads/receipts are captured. The
Captured candidates are certified after the reader closes. The complete
anchor-to-current replay must explain the current canonical IDs/order.
Unsupported/over-budget histories leave the previous current-tail path available;
an older repair then fails before a writer. Corrupt immutable anchors/receipts
are not accepted as repair evidence.

Owned DB Trash does not emit a typed block command. Its immutable
`database.trashed` event can be normalized to an ordered deletion only when its
scope, sequence, block/database identity and result agree with the exact immutable
`collaboration.database_trash` operation receipt, including event ID, operation
ID and request hash. Combined history shares the same command/byte budget.
Historical `database.restored` events still need a separate placement/content
contract; this does not infer their anchor. The current DB restore request keeps
database_trash's existing exact block snapshot and restore-anchor checks.

## Structural repair and content preservation

Independent Python and Node validators replay the anchor history, rather than
applying old move commands again to the latest projected order: move composition
is not idempotent. Only known IDs may be reconciled; an extra visible ID must have
a proven deletion, and untouched surviving IDs must retain their relative order.
A unique typed insertion that was never restored can use current canonical
values, just as in the current-tail contract. An existing/orphan Y.Map cannot be
overwritten as a missing insertion. Existing surviving maps and attributed
Y.Text instances are kept; order repair changes only the Y.Array entries.
Unknown CRDT-only ahead identities and explicit-empty compatibility are retained
when no certified discrepancy calls for repair. They cannot be guessed away
while repairing a conflicting structure.

Reconciliation uses the existing internal witness, never client-supplied history.
Node replay/preparation and independent projection replay run outside SQLite
readers/writers. Final scope, identity, generation, actor, lock, epoch and exact
source/document stamps still fence the existing atomic delta/domain/receipt/
audit/outbox transaction. Pure copy preparation changes no source rows/frames.
No generation ledger, frame or historical revision is rewritten.

## Remaining recovery scopes

Typed `restore_deleted` needs an exact rich-text recovery range and remains
guarded. Full saves or other domain changes not explained by the bounded anchor
and accepted command/event history are not inferred. Other linked/related DB
combinations, page-to-item bodies, nested legacy synced adoption, and DB item/
template synced identity/instantiation remain separate goal work. This packet
does not activate normal collaborative schema/flags or complete the full
R1–R11 plus C/D/E/F operating review.

Packet: `20261003-parent-historical-structure`. The primary chat integrator owns
the authoritative completion audit/status update after terminal gate receipts.
Exact evidence is written only to the chat workspace's
`outputs/parent-historical-structure-20261003/phase-evidence.json`.

The additive [current-tail rich restoration successor](parent_rich_restore_contract.md)
now owns restoration commands after the latest bound frame, with exact deletion
archives and independent attributed-text validation. Restorations before that
frame remain outside this historical packet's certified command replay.

The additive [historical rich restoration successor](parent_historical_rich_restore_contract.md)
extends the exact deletion/version certificate to typed restoration before the
latest frame. The original packet's evidence and remaining domain boundaries
remain historical records of their verified scope.
