# Pending parent inserts during native child creation

Status (2026-10-03): final server coverage is 36 distinct cases (34 in the main
run, one existing team-role case and one new exact join-sequence case), pure delta
coverage is seven cases (five new and two existing script suites), and the final
light1280/dark390 browser run passed two cases. This bounded packet changes
native child-page creation, with no operating migration, flag transition or
write to actual user content.

The previous link-only transformer replayed durable frames and appended the new
child link. A canonical typed block inserted after the latest frame could then
be absent from the newer link frame. The canonical paragraph survived in SQL,
but strict synced-source preparation correctly refused that divergent body.
The join packet's original failing fixture and diagnostic IDs establish this
sequence; they do not justify relaxing the strict source guard.

Native creation captures the authorized parent snapshot, generation and frames
under the existing reader. It additionally captures current canonical block IDs
with an immutable `block.insert` command newer than the latest bound frame of
that generation. Deleted/noncurrent IDs are excluded. This proof is server-owned;
clients cannot submit a replacement body or the captured ID list.

In this original native-creation packet the optional delta input
`post_frame_insert_ids` was supplied only by native creation. The transformer
may add a missing canonical block only when it has
that proof and every existing projected block remains in exactly the same order
as the canonical body after those inserts are removed. Duplicate IDs, unknown
proof IDs, orphan map collisions, unproven missing blocks and ambiguous ordering
are refused. Existing projected text, title, properties and rich markup are
never replaced from a stale SQL seed. Proven inserts use their captured current
content/properties and canonical positions; the child link is then appended.

The service independently decodes both old frames and old-plus-prepared delta
outside SQLite. Its expected body includes only the proven inserts and the new
child link, retaining every old projected block and title exactly. Any unrelated
mutation or mismatched insert proof is refused before the writer. Replay budgets
and the durable one-MiB update limit remain. The existing writer rechecks parent
sequence/row version/generation, current creator/access/lock and workspace version
before atomically saving child metadata/history, parent link/delta, exact receipt,
audit and outbox. Same-operation retry still checks the receipt first.

Callers without the optional input retain their existing link-only behavior.
This does not establish rebase support for move, copy, archive or restore links,
typed deletion/reordering, older already-divergent streams, or nested/item/template
synced identity. Those remaining combinations keep their current guards and need
their own evidence. No old frame or immutable typed-command record is rewritten.

Evidence for this phase is recorded in
`outputs/parent-insert-rebase-20261003/phase-evidence.json` in the chat workspace.
The full original Notion development goal remains active.

The subsequent 2026-10-03 page link lifecycle packet opts move, ordinary copy,
archive and restore into the same insert witness/base. Its independent link
placement checks and separate final evidence are documented in
`page_link_parent_insert_contract.md`. The counts above remain the historical
native-creation packet counts; they are not cumulative lifecycle coverage.
