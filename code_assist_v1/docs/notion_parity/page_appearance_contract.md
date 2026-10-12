# Page appearance — UI-ICON-005/006

`PATCH /api/notion/workspaces/{workspace}/pages/{page}/appearance` uses the active
collaborative editing authority. It is available only in the existing enabled
collaborative runtime; it does not enable a rollout flag or migrate a schema.

Request fields are exactly `updates`, `expected_document_sequence`,
`expected_workspace_epoch`, `expected_document_epoch`, and `operation_id`.
`updates` contains at least one of `icon` (at most 64 characters) and `cover`
(at most 2048 characters). Empty strings explicitly remove the corresponding
appearance. Supported vector IDs and colors come from the local icon catalog;
existing emoji values remain compatible. Covers accept HTTP(S) image URLs or
linear gradients supported by the picker.

The active subject requires the page's `edit` or `full_access` role and an
unlocked page. Workspace, document, access epochs and sequence are checked in
the transaction. Reusing a request returns its immutable receipt after current
authorization; changing a request under the same operation ID is rejected.
A sequence conflict is not silently overwritten. The client preserves an
uncertain request and retries it exactly, then re-reads current metadata.

`nf:` icons must reference an active file belonging to this document. PNG, JPEG
and WebP are limited to 2 MiB. File bytes, signature, MIME, size and checksum are
checked outside the write transaction; file identity and active state are
rechecked inside it. No foreign-page file is accepted even if the caller can
read that other page.

The transaction updates icon/cover, sequence, row version, content checksum and
update time, and atomically writes its receipt, audit, event and outbox. It does
not replace blocks, CRDT generations, database rows or immutable revisions.
`page.appearance.updated` is replayed through the existing authorized live event
path. Clients refresh appearance without replacing a locally edited body.

The collaboration payload advertises `collaborative_appearance`; its page
includes `can_change_appearance`. Workspace lists retain their authorized bound
page set and placement while taking display metadata from the collaborative
source. Missing or denied canonical pages are omitted. Filtering uses the
resulting display title. This is a read projection, not a legacy dual-write.

Verification: `tests/notion_collaboration/test_page_appearance.py`,
`tests/test_notion_collaborative_appearance.js`, and the existing icon/appearance,
navigation and save tests. Live fixture evidence is recorded separately from
unit/API verification. Full UI parity remains governed by the additional UI plan.
