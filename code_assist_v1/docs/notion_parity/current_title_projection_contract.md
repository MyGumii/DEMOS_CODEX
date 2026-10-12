# Current collaborative title projection

Contract: `code_assist.notion.title_projection.v1`  
UI regression: `UI-META-003`

The collaborative editor stores its current title in the Yjs `page-title` text.
The immutable canonical revision and the document descriptor's original title
are not updated by an opaque text delta. Current page/list/search UI must read
the durable text state instead of displaying that original title indefinitely.

## Read contract

- `title_projection.read_current_titles` receives only page IDs already bound by
  its caller. It checks the current human identity and page access before loading
  any payload. It never discovers or authorizes additional pages.
- One SQLite read snapshot captures each page's current generation, latest
  generation-bound CRDT snapshot, and subsequent generation-bound updates.
  Stored SHA-256 hashes and payload encoding are checked.
- Node uses the same pinned Yjs 13.6.32 bundle as the browser. No new dependency,
  remote request, migration, CRDT engine, or schema-marker change is introduced.
  Decoding runs after the database read transaction has closed.
- A missing `page-title` leaf uses the descriptor title. An explicitly empty leaf
  returns the empty string; the UI can display its ordinary untitled placeholder.
  Invalid or incomplete Yjs state is an unavailable result, never a successful
  fallback to stale metadata.
- Current identity, access, document sequence, access epochs, row version and
  generation are checked again after decoding, including cache hits. Revoked or
  archived pages are omitted. A changed binding retries the entire caller read
  up to three times, then returns the existing authority-unavailable error.
- Only pure decoding results are cached, bounded to 256 small entries. Permissions
  and caller page lists are not cached. Decoder batches contain at most 32 pages
  or about 8 MiB; this does not limit the number of authorized candidates scanned.
  A single replay over 64 MiB requires compaction and fails explicitly. The Node
  process has a 15-second timeout and a 256 MiB heap budget. Payload content is
  excluded from diagnostic output.

## UI integrations

- The current collaboration page payload overlays only its display title and
  adds `page.title_projection` with the projection contract, generation and
  document sequence. Body, revision, checksum and appearance receipts retain
  their existing authority and meaning.
- Authorized workspace page metadata uses the same title, including local title
  filtering and parent link rendering based on that list.
- Search derives titles before term matching, ranking, title sorting and limiting
  results. The old index's empty-body title fallback is not treated as body text.
  Existing indexed body search is otherwise unchanged.
- Meeting lists use the current title before title filtering and pagination.
- Local title input and remote editor projections update existing sidebar labels
  and page-reference buttons without replacing editor content or focusable rows.
  Explicitly empty titles display the ordinary untitled label rather than a
  reference block's old title.
- After a durable CRDT acknowledgement, same-origin tabs receive a small scoped
  invalidation (subject, workspace and page IDs; no title/body). A receiving tab
  reloads current authorized metadata. Duplicate body-only acknowledgements are
  coalesced by their unchanged title. Channel failures do not block ACK removal.
  Returning focus/visibility also refreshes metadata. Workspace changes and older
  overlapping responses are discarded, pending page IDs are retained, and active
  unacknowledged/IME text is preserved. This is not cross-device push delivery;
  background pages in other browsers remain a separate verification item.
- Public/export/history title behavior and other metadata surfaces still require
  their own verification; this packet does not claim they are completed.

## Preserved mutation contracts

This is a read projection. It does not rewrite canonical revisions or receipts,
write the legacy page store, add a second title-save request, or alter CRDT
generation transitions. Search may converge its existing replaceable index using
the existing transaction contract; Yjs decoding never runs in that transaction.

Evidence is recorded in the phase-4 progress document after tests and live UI
verification. Unit/service checks alone do not establish visual Notion parity.
