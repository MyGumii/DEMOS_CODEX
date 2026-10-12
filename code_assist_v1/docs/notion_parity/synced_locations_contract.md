# Current synced locations and navigation

Status (2026-10-03): supported explicit source/reference mounts expose current
authorized location inventory. This supersedes only the single-original list
presentation in synced_reference_toolbar_contract.md. Frozen read, text, copy,
detach and authority contracts otherwise retain their scope.

GET /api/notion/workspaces/{workspace}/pages/{page}/synced-blocks/{block}/locations
returns code_assist.notion.synced_locations.v1, instance, real origin, entries,
other_count and complete=true. Each entry has the exact document/block endpoint,
workspace and page/row/template location, current page and body title, and an
original flag. The count measures other block instances; the visible list groups
the same document/body, preferring its original, then the current instance.
The current page is included. Row and template bodies remain distinct locations.

The requesting instance and real source must pass the existing owning-page
authority first. Inventory scans unarchived canonical pages in all currently
readable workspaces, not cached sync IDs or reference contents. Nested explicit
references and active row/template bodies participate. Current database authority
is also checked before disclosing DB body labels. Direct database grants do not
manufacture access to an otherwise denied owning page. Archived and denied
endpoints, titles and counts are omitted; malformed related or ambiguous IDs
fail instead of guessing a location.

Current descriptors, including CRDT title and body projection, are captured
outside SQLite transactions under one aggregate source-byte budget. There is no
document-count cutoff or partial count labeled complete. The full current visible
map and stamps are rechecked in a final read snapshot. New documents, changed
content/grants and actor deactivation invalidate preparation. Failures and success
use no-store HTTP headers. There are no canonical writes or migrations.

Opening a menu fetches fresh inventory. DOM labels use escaped text. A dismissed,
disposed, scope-obsolete or aborted request cannot recreate peer rows or restore
focus. Arrow/Home/End and Escape use enabled visible controls; placement fits the
viewport. The last successfully read count is a snapshot, not a continuously
watched all-workspace count. Reopening and choosing a peer refresh authority.

Choosing a listed peer flushes supported pending text, reads fresh inventory,
matches its exact workspace/body/endpoint, and reads that endpoint again against
the real source before navigating. Existing main and owned row/template hooks
open the precise current surface. Hidden-owner fallback remains an exact read-only
tree, not a full body editor. A revoked label cannot authorize navigation.
This gate does not add linked-template structure changes or DB partial copy.

Supported originals share the top-right location toolbar. Existing editable main
More uses the block action dialog; readonly and mounted DB scopes retain their
supported actions. The source copy button still performs the frozen immediate
same-page reference insertion. Actual Notion's copy button prepares clipboard
content for later paste; clipboard parity remains a separate following packet.

Evidence:37 server inventory/HTTP/current title/DB/race cases,26 browser cases
(8 new,18 affected),63 JS cases,4 JS and3 Python syntax checks on frozen sources.
Browser fixtures are isolated. Native pages are initialized before measuring
navigation; main peers use the actual active editor, DB bodies their service
commands. Failed and diagnostic runs are excluded. Real Notion internal/external
and original lists plus copy toast were inspected through computer use without
pasting or modifying a document. Normal app loads ui381/action-ui4 and keeps
its title and13 blocks; collaboration/SBE activation remains unchanged.

No full operating release, native Windows IME, large-workspace performance or
instant unknown-peer update claim is made by this packet.
