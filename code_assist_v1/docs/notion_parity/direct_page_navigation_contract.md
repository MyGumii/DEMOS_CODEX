# Direct page navigation

Updated 2026-09-15. Scope: UI-DIRECT-LINK, canonical workspace resolution and safe page/workspace navigation.

## Authority and compatibility

- `GET /api/notion/pages/<page_id>/workspace` returns exactly `page_id` and `workspace_id` for the authenticated active human with current canonical document access. It denies unknown, archived, inactive-identity and inaccessible documents. It does not accept another actor or workspace from the client.
- Resolution uses one read transaction, current IAM role resolution, and an active-identity recheck. It does not read legacy page projections, scan all owners, write preferences, migrate schemas, or grant access. Responses are `no-store`.
- `page_workspace_resolution` is advertised by ready collaborative catalogs. Older running backends without this capability retain their existing same-workspace route; the UI does not call an unavailable resolver.
- Main-editor collaborative body loading uses the existing canonical collaboration payload directly. A missing legacy compatibility page is no longer a prerequisite failure. General-mode loading retains its legacy endpoint and atomic-save contract.
- Resolution is not an authorization token: destination navigation/page lists and the final body load each revalidate current access.

## Navigation behavior

- If a page is missing from the current sidebar list, resolve its actual workspace. For the same workspace, read the unfiltered authorized list. For another workspace, confirm current workspace visibility and stage its navigation state and unfiltered page list.
- Apply a staged workspace only after source preferences/drafts and side-peek closure have succeeded. Check main-editor edits again after destination reads. Destination read failure leaves the current workspace/editor intact.
- Keep the direct URL during an automatic workspace transition. Persist the selected workspace only after a successful transition. Manual workspace switching keeps the normal Home route/focus behavior.
- Allocate navigation order before asynchronous AI/draft preflights. A newer page, Home, or workspace navigation supersedes the earlier operation. Old successes, failures, capability reads and deferred focus callbacks cannot overwrite a newer destination.
- Keep the source editor during a body read; save changes made while loading before applying the destination. If that read targets the same page and predates a local edit, retain the newer local/saved content instead of applying the old payload.

## Evidence and remaining scope

- `test_page_workspace_resolution.py`: 3 server scenarios for read-only authority, revocation/archive, HTTP actor/runtime boundaries. `test_canonical_page_navigation.py` covers inherited listing without legacy grants.
- `test_notion_direct_links.js`: 14 runtime scenarios, including initial direct URL, filtered list, missing projection, failed saves/reads, superseded success/failure/preflight, late capability failures, typing during reads, and general-mode compatibility.
- Existing navigation/UI runtime, page-draft, side-peek and atomic-save JS tests pass.
- Real Chrome verification on the existing disposable 2412 fixture: guest personal workspace → direct shared-document URL → correct E1 workspace/body, reload, manual personal-workspace transition, direct re-entry. Two paragraphs and document sequence 1 are preserved; guest navigation preference/favorite versions remain 4/2.
- This packet does not establish completion of every role, database, mobile, history, side-peek or AI navigation combination. The full Notion parity plan remains active. Production schema migration, rollout and feature-flag transitions are separate and were not performed.
