# Current document search preview

UI-SEARCH-PREVIEW adds a passive document renderer to the search dialog. It does
not mount an editor or mutate the active page, database, revision, or CRDT state.

## Read contract

`GET /api/notion/workspaces/<workspace_id>/pages/<page_id>/search-preview`
returns `code_assist.notion.search_preview.v1` with page metadata, ordered blocks,
visible databases, authorized current page references, and source projection
sequence/generation. The existing collaborative runtime and page/workspace
binding gates apply. Responses use the existing no-store route helper.

The service reads the canonical snapshot and durable Yjs frames in a read
transaction, closes it before Node decoding, then opens another read transaction
to revalidate active identity, source sequence, workspace/document permissions,
and compaction generation. Changes retry up to three times through the existing
projection retry contract. Revocation denies the source or omits an inaccessible
reference on retry. There is no per-user content cache or search-index write.

An explicit empty CRDT block order means empty body. Updates with no body leaf
retain the structural body. Databases are materialized only when referenced by
the current body. Formula/rollup values reuse the database authority, and relation
titles require current permission on the target database's document.

## UI

The main renderer reuses sanitized rich text, icon, math and canonical database
adapters. Nested links are passive within the preview; clicking its document
surface or pressing Enter/Space opens the selected source page. Selecting text
does not trigger opening. Copy/open controls remain outside that surface.

Every query edit, close, preview hide, result change, and known navigation disable
invalidates the pending preview. Responses also check the request sequence,
active result, principal, workspace and capability scope before display. Failed
loads clear prior body content and offer a guarded retry. General mode uses its
existing authorized page GET rather than reading collaborative storage indirectly.

The preview is initially hidden below 900px. At widths up to 600px, showing it
temporarily replaces the results pane; its toggle returns to the results.
Filters and preview visibility have separate controls. Tables scroll locally.

## Evidence and remaining fidelity work

`tests/notion_collaboration/test_search_preview.py` covers current replay,
compaction, explicit empty body, concurrent update, revocation during decoding,
current reference titles/revocation race, computed/related database values,
title-only compatibility, HTTP scope, read-only access and no-store responses.
Logical database dumps before/after reads check absence of writes.

`tests/test_notion_document_preview.js` covers passive structure, escaping,
columns, tables, hidden toggle/tab content, computed/reference labels, source
immutability, async response order, close/scope changes, retry and open guards.
`tests/test_notion_search_ui.js` covers query and capability invalidation.

This packet does not establish parity for every block/view: database previews
currently use a table even for board/gallery/calendar/timeline views; media HTML
embeds are passive labels; covers and nested interactive toggles need further
comparison. These are unfinished fidelity work, not a redefinition of the target.
Authorized person/relation/computed values in preview do not imply the search
index can match all those dynamic labels. Live revocation after an already
completed read is not an instant push guarantee; this endpoint reauthorizes each
request. General-mode and all-role UI scenarios remain part of the broader audit.
