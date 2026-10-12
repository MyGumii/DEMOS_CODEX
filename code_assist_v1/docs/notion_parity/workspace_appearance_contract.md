# Workspace appearance v1

`PATCH /api/notion/workspaces/{workspace_id}/appearance` accepts exactly `icon`, `base_version`, and caller-supplied `operation_id`.

The service `notion_workspace_appearance.update_workspace_icon` uses the current runtime's workspace store and its existing transaction, authorization ledger, audit, and outbox contracts. Current owner or membership administrator authority and the exact workspace version are required. Replays revalidate current authority and return the original receipt. Reusing an operation ID with different input fails.

The mutation changes the workspace icon, row version, and update time. It does not change membership/security epochs, page content, file access, or other stores. General workspace catalogs and the collaborative runtime facade share this contract; an unconfigured legacy personal UI is not converted into a workspace catalog.

Supported icon values are empty (name-based default), existing text/emoji up to 64 characters, and catalog `ni:<id>:<color>` vectors. `nf:` and `nw:` references are rejected by this PATCH API: page-owned files do not establish workspace-wide image access, and workspace image references are assigned by the image service below. Page image upload is unchanged.

The authenticated workspace GET advertises `capabilities.workspace_appearance`. The frontend displays current workspace metadata after saving or replaying an old receipt, preserves document editing state, and ignores completed responses for a different workspace. In-memory uncertain requests retain their operation ID; this does not provide retry persistence across a browser reload.

Evidence: `test_notion_workspace_appearance.py` (UI-WS-ICON-001), `test_notion_workspace_icon.js` (UI-WS-ICON-002). Browser evidence is recorded separately in the UI development progress log.

The sidebar's small red workspace-avatar dot is a presentation of the current
workspace's unread inbox count, not part of the saved workspace icon. It uses
the same scoped unread calculation as the inbox badge and disappears when the
count reaches zero or the inbox scope changes. The 2026-09-28 authenticated
Notion comparison showed the avatar dot alongside an unread inbox badge; an
isolated Chrome fixture verified the 8px dot appears and clears with the badge.

## Workspace-owned images (additive schema 2411)

`notion_workspace_icon_image_migration` defines a guarded, checksummed, explicit 2026082410 → 2026082411 migration. It adds one `workspace_icon_images` row per workspace. Existing migration DDL remains frozen; application requests do not create or migrate schemas. The source and new table both remain in the workspace catalog. The existing general personal workspace is not silently converted into a catalog.

`POST /api/notion/workspaces/{workspace_id}/icon-image` accepts the raw image body and requires `Content-Type`, `X-Notion-Base-Version`, and `X-Notion-Operation-Id`. Standard authenticated requests require the existing CSRF header. Images are PNG/JPEG/WebP, at most 2 MiB, single-frame and at most 16,777,216 pixels. Pillow 12.3.0 is pinned in the runtime lock for content validation; decoding and hashing happen before the write transaction.

The service revalidates the active actor, current workspace owner/admin membership and exact workspace version in the transaction. Image bytes, `nw:wi_<40 hex>` reference, workspace version, receipt, audit and outbox commit together. The receipt/request ledger contains metadata and hashes, not image bytes. Replaying the same request revalidates current authority and returns its original receipt without restoring an older image. An operation ID reused for different bytes or metadata fails. The browser reads current workspace metadata after success or replay and preserves in-memory request identity after an uncertain response.

`GET /api/notion/workspace-images/{image_id}` permits only current workspace visibility under the same authority used by workspace search, including legitimate guests. Revoked/inactive actors and foreign workspaces cannot retrieve the image. It serves only the image currently referenced by the workspace. Removing the icon through the appearance PATCH makes the image URL unavailable; it does not purge the retained last image row. Replacing the image updates that single row. Reads are `private, no-store`, with the validated MIME and `nosniff`.

The exact new schema is recognized by the workspace facade and editing transport; `WorkspaceImageAuthority` inherits existing document, file, history and search behavior. Older schemas stay accepted under their original exact markers and do not expose upload capability. Only an explicitly migrated catalog advertises `workspace_image`. This is not approval to migrate an operating catalog or change rollout gates.

Evidence: `tests/notion_collaboration/test_workspace_images.py`, `tests/test_notion_workspace_image.js`, plus old appearance regression tests. Current real-browser evidence is limited to the fresh disposable 2411 fixture: upload tab, stored image rendering and reload. The Chrome file chooser is blocked by extension file URL access; a live HTTP upload/clear/restore is separately verified and is not claimed as a successful browser file chooser run. General personal settings and final runtime/backup rollout gates remain pending.
