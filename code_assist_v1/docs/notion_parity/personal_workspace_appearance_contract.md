# Personal workspace appearance

Contract: `code_assist.notion.personal_workspace_appearance.v1`.

This surface owns the appearance of an existing legacy personal Notion shard. It does not create a navigation catalog, a hidden page, a page attachment, or a second copy of canonical workspace metadata. Catalog/navigation and collaborative modes reject the personal endpoints. The existing canonical workspace appearance and workspace-image contracts remain separate.

## Storage and deployment

Forward version `2026091501`, name `notion_personal_workspace_appearance_v1`, checksum `sha256:1a07f0bf1d9db43dc79770b53e4ac91e2de3034c582c36bf10c79e9d651143e5` adds four owned tables to the user's existing `notion.db`: appearance, operation receipts, audit, and outbox. The appearance row holds the current icon, version and optional validated image bytes. Removing/replacing an image makes the old reference unavailable; removing also clears the bytes.

GET, PATCH, image requests, connections and application startup never migrate an existing shard. Existing shards require `notion_personal_workspace_schema.build_plan(owner)` followed by explicitly authorized offline `apply_plan(plan, backup_path=..., quiesced_confirmed=True)`. Stop writers first. Apply requires an exact source logical snapshot/checksum, creates a new SQLite backup outside the write transaction, rechecks the source under `BEGIN IMMEDIATE`, and commits the new schema and marker together. A stale plan, changed checksum, occupied backup path, missing prerequisite or ownership mismatch fails. Keep the backup; restore is an offline deployment action, not a UI operation.

The existing explicit `bootstrap_workspace` boundary installs the new tables only for a brand-new shard. Old shards report capability false until migrated. No production migration or feature flag transition is implied by the implementation or tests.

## Authorization and mutation

The current active personal owner alone can write. An administrator or page editor does not gain that right. A current active recipient of any active page share can read the owner's icon; revoking their last active share removes image/metadata access. Anonymous image requests are denied by the normal authentication layer. Image responses are private, no-store and nosniff.

PATCH and upload require an integer `base_version` and caller operation ID. The owner, current account status, schema and expected version are validated before commit. Image bytes/reference/version, receipt, audit and outbox commit in one SQLite transaction. A matching retry reauthorizes and returns its original receipt; a different request with the same ID or a stale version returns 409. Decoding PNG/JPEG/WebP (2 MiB, still image, 16 megapixels) occurs before the transaction using the shared validated-image routine. Clients cannot submit image references through the ordinary icon PATCH.

Stored `np:<owner>:pi_<40 hex>` references use the authenticated personal image route. Their ownership is never inferred from page files. The public appearance name currently follows the account display name; independent workspace renaming is not part of this contract.

## API and UI

- GET `/api/notion/personal-workspaces`: current visible personal workspaces and capabilities.
- GET/PATCH `/api/notion/personal-workspaces/{owner}/appearance`.
- POST `/api/notion/personal-workspaces/{owner}/icon-image`: raw bytes, Content-Type, X-Notion-Base-Version, X-Notion-Operation-Id, ordinary CSRF protection.
- GET `/api/notion/personal-workspaces/{owner}/images/{image_id}`.

The general workspace menu exposes Settings → General, the common icon picker, and this browser's existing theme preference. The sidebar header, workspace menu and settings use the same icon renderer. Guest metadata is read-only. The controller isolates account/workspace contexts, deduplicates in-flight writes, preserves an uncertain request for retry, reads current state after replay, and prevents older list responses from overwriting a committed icon. Closing a loading settings screen cancels its later popup; Esc restores the workspace trigger.

## Evidence and limits

`tests/test_notion_personal_workspace.py` covers no-write reads, fresh creation, explicit backup migration, ownership, shared/revoked image visibility, corruption/MIME/size rejection, atomic rollback, HTTP headers/mode boundary, and same-operation execution by two separate processes. `tests/test_notion_personal_workspace.js` covers UI request races, retries, image validation, context changes, cancellation and reference parsing. Existing atomic-save tests must continue passing.

Actual general owner UI verification includes emoji/vector apply/remove, reload persistence, stored-image rendering, narrow/dark settings and Esc focus. Chrome file chooser application remains unverified while extension file access is unavailable. Full deployment backup/restore rehearsal, all guest UI transitions and operating-app rollout are separate acceptance evidence; a unit/API pass does not assert they happened.
