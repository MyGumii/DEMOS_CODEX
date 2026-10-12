# Current collaborative search text

Contracts: `code_assist.notion.visible_search_text.v1` and
`code_assist.notion.search_projection.v1`. This extends current title search with
body extraction; it does not change revision/history/transfer semantics.

- Search collects only explicit content fields, rich-text display text, simple
  table cells, canonical database names and literal cell values, media names and
  captions, tab/button labels, and AI-note transcript/translation/summary text.
  It never recursively indexes block IDs, property IDs, configuration, versions,
  layout, filters, unused options, or template bodies. Literal plain-text angle
  brackets, code, numbers, the word `paragraph`, and UUIDs remain searchable.
- A canonical database descriptor replaces its stale editor-property copy for
  extraction and is included only where the current block list references it.
- Person/relation IDs are excluded. Current authorized labels and computed
  formula/rollup values are a separate remaining integration; workspace-wide
  indexing must not copy labels or values from an inaccessible target page.
- Index preparation captures the current source rows, security epoch, search
  generation, CRDT generations, checksum-validated snapshots and update tails in
  one read transaction. The pinned Yjs 13.6.32 runtime decodes after that
  transaction closes. Replay uses the editor's block-order/map representation,
  preserves explicit empty bodies, and rejects missing dependencies. Title-only
  historical frames preserve the structural body fallback.
- Before replacing the projection, the service rechecks the active caller,
  workspace visibility and exact source stamp in the write transaction. Changed
  preparation is retried at most three times. Decode failure, permission loss or
  commit failure cannot leave a partially replaced index.
- Decode batches are limited to 32 documents or 8 MiB of input, with a 64 MiB
  per-document replay limit, 15-second process timeout and 256 MiB Node heap.
  This is a limit per batch/document, not a total workspace latency guarantee.
- The existing projection checksum includes both extractor/replay contracts, so
  a prior index is rebuilt on the next authorized request without editing source
  documents or performing a database migration. Existing generation triggers
  invalidate the index after durable collaborative edits.
- Search result permissions, title revalidation, filtering and pagination remain
  in place. Backlink/reminder context uses the same visible block extraction;
  reminders support the current rich-text field as well as historical content.
- Only replaceable search/backlink/reminder tables are written. Canonical body,
  receipts, revision hashes and the legacy store are unchanged by indexing.

Validation: `test_visible_search.py`, `test_search_authority.py`,
`test_title_projection.py`, and `test_notion_search_projection.js`. These establish
service/decoder behavior, not completed browser parity or production rollout.

Remaining: general-mode search, authorized reference labels/computed database
values, live browser search verification and large-workspace cold rebuild timing.
