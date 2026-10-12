# Parent inserts during page link lifecycle operations

The 2026-10-03 lifecycle extension applies the native child-creation insert
witness to page move, ordinary page copy, archive and restore parent links.
It is a bounded continuation of `page_creation_parent_insert_contract.md`.
At this historical page-lifecycle gate, database and row-to-page batch writers had not opted in. The later `database_link_insert_contract.md` packet covers owned DB lifecycle and linked-view hosts; batch links remain separate.

Each owning service captures the authorized parent snapshot, current generation,
bound frames and canonical IDs with immutable `block.insert` commands newer than
the latest bound frame. This is an internal server witness; no client body or
proof parameter is accepted. Source collection remains in the existing reader.
All Node preparation and independent replay run after that reader closes.

The pinned link transformer adds only exactly proven missing canonical IDs at
their canonical positions. Existing projected content, type, properties, rich
markup and title are preserved. A gap requires the remaining canonical order to
equal the complete current projected order; unproven older gaps, identity
collisions and ambiguous ordering are refused. Old ledger rows/frames are not
rewritten. Typed deletion/reordering and older divergent streams are outside
this insertion contract.

`page_link_parent_rebase.validate_deltas` independently decodes old and
old-plus-delta streams in a bounded batch before any writer. It derives the
expected body from the same proven base and applies only the selected child-page
link change. The current projected type/`page_id`, rather than a stale SQL link
type, determines deletions. Unordered Yjs-map links may be cleaned up, but any
visible removed ID must be an exact current child-page match. Unrelated visible
content and the current title must remain byte-for-byte equal in the projection.

For a same-parent relative move, the first ordered matching link retains its
identity, text and properties, while its exact before/after position is checked.
Archive captures the surrounding visible nonmatching IDs and index. Restore
places the link at the surviving next anchor, previous anchor, or clamped index.
Copy appends only the new root link. The independent check also verifies placed
identity, inserted flag, structural after-anchor, restore-anchor, changed flag
and absence of batch placement metadata.

The owning writers still reauthorize exact current edit/owner scope and locks,
generation and document stamps. Page copy binds its parent witness through the
existing immutable `PreparedPageCopy.parent_stamp` and revalidation before both
link preparation and commit. Workspace/subtree fences and exact operation/hash
replay stay in their existing services. Parent structural changes, durable link
frames, metadata, receipt, audit and outbox commit together. An identical retry
returns the original receipt without rewriting its notes or links.

The new browser journey also reproduced an existing 390px UI defect: the fixed
desktop Trash dialog offset placed the restore button outside the viewport.
The <=720px rule now uses 12px left/right margins, a safe-area-aware bottom and
a viewport-bounded height. Desktop geometry is unchanged. The dark Trash footer
also uses the existing sidebar background rather than its previous white fill.
The CSS asset is `20261003-ui129-mobile-trash-dark-footer`; the main script
remains UI361.

This packet uses synthetic catalogs and isolated application processes. The
normal app's CSS reflection and unchanged title/13 blocks are a separate receipt.
No real collaborative migration, capability transition, lifecycle mutation or
normal backend restart is implied. The original R1-R11 and C/D/E/F goal remains
active, including DB/item/template/nested synced scopes and operating review.

Exact final counts, durations, screenshots and qualified intermediate failures
are recorded in the chat workspace at
`outputs/page-link-parent-rebase-20261003/phase-evidence.json`.

The subsequent row/page placement packet is documented in `database_row_page_parent_insert_contract.md`; it independently verifies batch metadata/anchor chains and reverse single-link deletion. Historical61/4 page packet counts remain unchanged.

## Typed deletion/order successor (2026-10-03)

`parent_structure_rebase_contract.md` extends the proven parent tail to typed deletions and moves, and extends read-only page copy source preparation. Exact immutable history, independent full-body/title verification and existing writer fences remain. Older unexplained deletion/order is refused; rich-content typed restoration requires its own recovery contract. The insert-only counts above remain historical; current phase receipts are in the chat workspace at `outputs/parent-structure-rebase-20261003/phase-evidence.json`. No operating capability/schema activation is implied.
