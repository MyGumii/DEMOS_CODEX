# Native team page creation — UI R10

This successor extends the native page creation service with an explicit team root destination.
It does not change a legacy source binding into a location record.

- Exact predecessor: 2026082416 `notion_workspace_v2_file_copy_provenance`.
- Exact successor: 2026082417 `notion_workspace_v2_native_team_roots`.
- Migration module: `notion_workspace_native_team_migration.py`; plan checksum, verified backup and quiesced catalog are required. There is no request-time migration or production enablement in this packet.
- New table: `workspace_collaboration_native_team_roots`, one native root document per record, tied to the same workspace and an active teamspace. Descendants inherit their current root's placement. No existing document, source binding or access grant is backfilled.

`POST /api/notion/workspaces/{id}/pages` accepts optional nonempty `teamspace_id`, mutually exclusive with nonempty `parent_id`. The team destination participates in the request hash. Omitting it preserves existing private root creation semantics and existing request hashes. The new capability `collaborative_team_page_creation` is false on predecessor schemas; unsupported team requests fail without a private/legacy fallback.

An active joined team member with an ordinary workspace membership may create a team root. A workspace owner may also create one. Restricted members and guests may not create; membership administrators need actual team membership. Child creation additionally requires current editable, unlocked parent access. Authorization and version checks run again in the service transaction after Yjs preparation.

Document/checkpoint/revision, native registry, root placement, default `teamspace/full_access` inherited grant, optional parent link, audit, outbox and idempotency receipt commit atomically. Concurrent equal operations return the same page; reused operation IDs with changed team destinations conflict. Exact retries reauthorize current membership and visibility.

Creator IDs are provenance for documents currently under native team roots, and do not independently grant document-owner access. Current team membership and explicit grants determine access; workspace owner access remains. Thus a creator removed from the team loses access unless another current grant applies.

Moving a native root before/after another native team root uses that team's destination. Explicit empty parent moves to private roots. Moving under a parent follows that parent's hierarchy. Leaving a root placement deactivates its default team grant, removes its location record and invalidates affected access epochs. Independent subject/group/other-team grants remain. Legacy roots cannot be adopted through relative native-team root placement. Copying a team root preserves its team through a fresh default grant, but existing copy authorization and the disabled UI copy capability remain unchanged.

UI: team header `+` opens a scoped blank-page menu. Current capability, identity, workspace, navigation generation and team metadata gate submission. Existing creation retries retain immutable team/title/operation identity. The generated page opens with title focus; Escape returns to the current team `+` or the collapsed-sidebar trigger. Team child `+` follows editable team access. This does not implement team member administration, team archiving, templates or other external Notion services.

Verification lives in `test_native_team_creation.py`, `test_native_team_creation_http.py`, `test_notion_native_page_create_ui.js` and `test_notion_teamspace_create_menu.js`. Migration/service tests use isolated synthetic catalogs, including actual cross-process equal requests. Browser evidence is recorded separately; passing service tests alone is not a full UI-parity claim.
