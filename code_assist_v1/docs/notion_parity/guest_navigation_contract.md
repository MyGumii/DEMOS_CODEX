# Guest navigation contract

The 2412 schema adds durable sidebar preferences and favorites for current workspace guests. A guest never receives a membership row or permission to change another person's settings. Existing member navigation continues through its original service and tables.

## Storage and migration

`notion_workspace_guest_navigation_migration.py` is the explicit, forward-only 2411 → 2412 migration. It adds `workspace_guest_navigation_states`, keyed by workspace and subject with a foreign key to `workspace_guests`. Historical schemas and member navigation tables are unchanged. The migration uses the existing guarded plan, exact checksum, quiescence and SQLite backup workflow. Application requests do not install the schema.

The new exact marker retains editing, transport, database, files, history, search and workspace images. `guest_navigation` is advertised only for 2412. Older catalogs keep their previous behavior until explicitly migrated.

## Authority and mutations

The runtime facade dispatches a current guest to `notion_guest_navigation`; ordinary members continue through `WorkspaceStore`. The service rechecks an active human identity, current workspace visibility and an unrevoked guest row within the transaction. It rejects active members on the guest-only path. Revocation cannot be bypassed by replaying a previous operation.

Preferences use an expected preference version; favorites use a separate expected favorite version. Sidebar widths and section fields use the existing validation limits. Favorites use exact source-owner/page identities and current canonical page access, including archive and owner checks. Reads filter inaccessible favorites and assign dense visible positions.

Writes, the collaboration operation receipt, event, audit and outbox commit together. Exact retries return the original receipt; the UI then reloads the current navigation snapshot. Reusing an operation ID with different input fails. Workspace events contain only the actor and navigation version numbers, never private settings or the saved page list. No page body, page sequence, workspace membership or another actor's settings are changed.

Guest preferences remain guest-scoped. The existing member store remains authoritative after promotion to membership; preference transfer between guest and member stores is not implemented by this packet.

## UI behavior

Viewing and commenting roles no longer hide the sidebar through role-specific CSS. Both the initial navigation snapshot and subsequent page rendering respect the saved collapsed state. The ordinary mobile sidebar and explicit collapse/restore controls remain responsible for visibility.

The existing navigation/preferences and navigation/favorites HTTP endpoints are reused. UI settings, collapse/expand and favorite actions keep their existing optimistic rendering, fresh snapshot after success, and error/retry flow.

## Evidence and remaining work

`tests/notion_collaboration/test_guest_navigation.py` covers no-write reads, schema/capability compatibility, member isolation, exact retry and conflict, current access filtering, revoked/inactive identity, HTTP actor boundaries, atomic outbox failure and two independent processes committing one operation. Existing workspace-image tests remain required because the new marker retains that authority. The UI navigation test checks guest collapse/expand snapshots.

Actual UI verification runs only in the named `work/notion-ui-phase4/guest-navigation-fixture` test environment. Operating migration and deployment require their existing explicit approval and backup/restore workflow. Cross-workspace direct page-link resolution is a separately identified, still-open navigation defect.
