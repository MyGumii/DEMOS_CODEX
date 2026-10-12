# Teamspace settings

Contract `code_assist.notion.teamspace_settings.v1` adds GET/PATCH at
`/api/notion/workspaces/<workspace_id>/teamspaces/<teamspace_id>`.
No schema migration, operating feature flag, source binding or page placement changes.

GET requires an active workspace membership and either active membership of the
exact team or workspace administrator authority. Collaborative catalogs also
require the current active-human identity. It returns that team's metadata,
active team/workspace membership intersection (subject IDs and roles), current
workspace version, and a rename capability; responses are not cacheable.
An unjoined directory entry alone grants no roster access.

PATCH accepts exactly `name`, `base_version`, `operation_id`. Name uses the
catalog's 200-character normalization and case-insensitive active-team uniqueness.
Team owners and workspace administrators may rename. It revalidates current
authority, exact workspace/team and workspace version in the service transaction.
Team version and workspace version advance; security epoch, membership, grants,
page content and placement remain unchanged. Audit, outbox and operation receipt
commit together. Identical retries return the original receipt after current
authorization; changed requests conflict. A later rename is not undone by replay.

The sidebar's team `…` opens a menu, then settings for its stable team ID.
The dialog shows name, visibility and members; members without rename authority
have a read-only name. Uncertain requests retain their operation ID and payload
for retries in the current browser runtime, including closing/reopening the
dialog. A fresh GET supplies current display values after success/replay.
Workspace/actor/navigation guards reject obsolete responses. A definitive version
conflict preserves input and requires reopening before a new mutation.
Pending request storage does not survive a browser reload.

Opening a centered dialog releases the common popover placement observer so a
previous menu cannot reposition it later. Esc resolves the current team trigger
after sidebar redraw; when the sidebar is hidden it uses the sidebar-open button.

This is a partial R10 implementation: native team-root page creation requires a
forward placement contract with atomic authorization, not fabricated legacy
source provenance. Team visibility changes, membership editing, archival and
Notion's full settings/member/security tab set are not provided by this packet.
Tests: `test_notion_teamspace_settings.py` (including two-process identical retry)
and `test_notion_teamspace_settings.js`; isolated Chrome evidence is recorded in
the UI task's teamspace settings report, separately from operating rollout.
