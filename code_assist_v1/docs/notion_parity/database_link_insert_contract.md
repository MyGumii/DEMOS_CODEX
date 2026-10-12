# Post-frame inserts during owned database lifecycle writes

This 2026-10-03 continuation applies the existing server insert witness to
ordinary owned-database copy, move, Trash and restore. It also retains host
inserts when a move retargets linked views in other documents. Row-to-page batch
links and their reverse path remain a separate packet. Full original Notion
goal completion is not established by this contract.

The authorized reader captures the canonical body, bound current-generation
frames and IDs with immutable typed insert commands newer than the latest
bound frame. These internal IDs are never accepted from the client. The existing
copy source witness, move source/destination/workspace reference witnesses and
Trash database/document/retained-state witnesses still fence the commit.

The common parent validator accepts an exact `owned_database` match in addition
to a child-page match. A current owned database is a supported database block
without `linked_database`, whose `database_id` (or fallback block ID) equals the
chosen database. The existing Node count witness and owning service identity
checks remain. Copy's server-generated sentinel deliberately matches no child
page and appends the independently remapped database block at its source anchor.

Before any writer, independent old-plus-delta replay verifies the complete
body/title and exact deletion/insertion/restore metadata. Only proven canonical
inserts and the chosen DB link change may differ. Current projected paragraphs,
rich markup, type, properties and title are preserved rather than replaced from
stale SQL. Unproven older gaps, ambiguous ordering and identity collisions remain
refused; this does not backfill old ledgers or frames or generalize to deletion
and reordering reconciliation.

For linked-view retargeting, each candidate host captures its own generation,
snapshot and post-frame insert witness under the existing workspace reference
fence. Its current complete projected body is rebased only through the same
proven-insert rule. Candidate references then bind their actual current props,
including local views, filters, sorts and active view. The pinned linked engine
reuses `page_move_delta.reconcileInserts` for hosts without a base update.
Source/destination base updates have already undergone independent verification
and retain their inserts, so they are not rebased a second time against an old
canonical seed whose DB location is intentionally about to change.

The existing linked engine's first-generation bootstrap also accepts title-only
streams without a competing projected body. Independent validation uses that
canonical initial body and retains an existing projected title, including empty
titles. Later-generation missing bodies still refuse. This is not an automatic
repair of an existing divergent collaborative body.

After linked preparation, a second independent replay includes any verified base
update and the linked delta. Every reference must keep its exact identity, type,
content and local props except for the intended source document locator; every
other block and title must remain equal. The writer still repeats current
scope, generations, DB versions, attachments/comments and workspace-wide linked
reference witnesses before committing metadata, structural links, CRDT frames,
exact receipt, audit and outbox atomically.

Testing uses synthetic catalogs and isolated app processes with test-only
capability overrides. No normal runtime restart, real schema migration or flag
activation belongs to this packet. Exact final validation receipts and any
intermediate failures will be recorded in the chat workspace's
`outputs/database-link-rebase-20261003/phase-evidence.json` when the gate passes.

## Verified packet receipt

Final server evidence covers81 distinct cases:21 new retention/safety cases and60 existing DB/page regressions in separate invocations. Four isolated menu/reload browser cases and seven existing Node cases pass; repeat runs are not additional unique coverage. Initial failures exposed an inserted-block normalization defect: SQL position metadata must not enter the four-field projected body. The old final server observations had no terminal receipt and are not counted; the replacement invocations persist exact terminal status. Existing Windows spawn-process DB move/page lifecycle regressions are included; no new DB process test was added. The final screenshots retain the Korean note and italic markup. They also expose existing red success-notice styling, left for a separate UI packet. This does not prove full-source/role combinations or operating rollout.

The subsequent `database_row_page_parent_insert_contract.md` packet opts row-to-page batch and reverse parent links into the same witness/base. This document's81/4/7 receipts remain its historical scope.

## Typed deletion/order successor (2026-10-03)

`parent_structure_rebase_contract.md` extends the proven parent tail to typed deletions and moves, and extends read-only page copy source preparation. Exact immutable history, independent full-body/title verification and existing writer fences remain. Older unexplained deletion/order is refused; rich-content typed restoration requires its own recovery contract. The insert-only counts above remain historical; current phase receipts are in the chat workspace at `outputs/parent-structure-rebase-20261003/phase-evidence.json`. No operating capability/schema activation is implied.
