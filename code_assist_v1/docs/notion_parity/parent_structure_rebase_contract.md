# Proven parent structural edits during link operations

This successor extends the insert-only parent packets. A typed block deletion or
move after the latest bound current-generation Yjs frame must survive later
native child creation, page lifecycle, owned database lifecycle, external linked
view retargeting, selected row-to-page placement and page-to-database return.
The complete body, title and link placement remain independently verified.

## Internal witness and replay

The existing authorized SQLite snapshot captures the exact document/generation,
latest bound frame document sequence, and subsequent immutable `block.insert`,
`block.delete`, `block.move` commands in sequence order. The tail is limited to
4096 commands. A deletion's recorded recovery generation must match the current
generation. This witness is internal service preparation data; clients cannot
submit a guessed order, deletion set or repair proof.

Outside every SQLite reader/writer, Node replays the complete frames and applies
the typed identity/anchor history to the old order. The resulting identities and
order must exactly explain the current canonical body. New surviving inserts use
their current canonical values. Existing surviving block maps and Y.Text objects
retain their content, formatting, type and props. A moved block only changes its
order entry. A proven deleted visible block leaves both the order and map. A
new transient insertion followed by deletion is not materialized in the output.
No original ledger entry or frame is rewritten.

Python independently replays old frames and old-plus-prepared delta, computes the
expected body using the captured history, verifies the structural change flag,
and checks the complete body/title and exact link identity/placement. Batch
links also retain their selected order and exact structural anchor chain.
Source/destination DB base deltas are verified once; external linked hosts use
their own witness before changing only the exact source locator, retaining local
view settings. The final writer still rechecks the existing actor, scope, locks,
epochs, generation, document/row stamps and receipt hash. Existing atomic
domain/delta/receipt/audit/outbox transactions and rollback behavior remain.

## Copy source preparation

The existing read-only page copy source preparation also captures this witness
for each already-authorized subtree document. Proven structural tails produce a
current copy descriptor without changing the original SQL rows, frames or
revisions. Existing copy scope, file provenance, content budgets, remapping,
authority/stamp revalidation and normal CRDT-only compatibility behavior remain.
The witness counts toward the source byte budget. The owning copy/move commit
does not treat successful preparation as cached authorization.

## Deliberate recovery boundaries

An unexplained older insertion gap is still refused. A projected identity known
to have been deleted but lacking a current-tail repair proof, or an unexplained
older typed order, cannot be carried into a new link/copy delta. These paths
require a separate historical reconciliation contract rather than an automatic
SQL body replacement. A current-tail `restore_deleted` command also requires its
own rich-content recovery contract; this packet does not guess restoration
content from an old map. Unsupported generation/body, ambiguous IDs, missing
anchors, duplicate/reused command sequences and unrelated mutations fail before
the writer. Page-to-item body transfer retains its separate existing guard.

This is a scoped development packet. It does not activate operating collaborative
schema/flags, complete nested/historical synced adoption or DB item/template
instantiation, or establish full R1–R11 plus C/D/E/F operating release completion.
Final gate receipts and qualifications are recorded in the chat workspace's
`outputs/parent-structure-rebase-20261003/phase-evidence.json` when the packet gate
finishes. Earlier insert-only receipts remain historical evidence of their scope.

The additive [historical structure successor](parent_historical_structure_contract.md)
now owns bounded checksumed anchor-to-current insert/delete/order certificates.
The recovery boundaries above describe this original current-tail packet;
historical successor evidence is recorded separately and does not rewrite it.

The additive [current-tail rich restoration successor](parent_rich_restore_contract.md)
now binds exact immutable deletion ranges and verifies attributed restored text.
Its scoped evidence does not change the limitations of this original packet.
