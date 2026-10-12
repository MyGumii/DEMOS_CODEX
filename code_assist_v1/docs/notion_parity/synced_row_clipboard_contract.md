# Synced reference clipboard insertion in row bodies

Status (2026-10-03): verified isolated staged owned/linked row surfaces. This
supersedes only row structural paste rejection in synced_clipboard_contract.md.
Template and database-only structural paste remain rejected. Historical read,
text, detach, ordinary whole-form, schema and rollout contracts are unchanged.

The existing bounded clipboard descriptor names a real original and same-app
origin. It carries no body, descendants, credentials, grant or authority witness.
The row writer rereads the current original outside SQLite, validates its exact
document-wide unique endpoint and complete hierarchy, and refuses reference
chains, ambiguous identities and archived/hidden bodies. Source read permission
is sufficient; the target requires current owning-page edit_content plus current
database edit_content where independent database sharing is installed. A direct
database grant never supplies owning-page authority.

The new service contract is code_assist.notion.synced_row_clipboard.v1. It exposes
GET .../pages/{document}/databases/{database}/rows/{row}/synced-paste-target and
POST synced-paste / synced-paste-result under the same exact physical row path.
All responses are no-store. POST requires exactly source:{workspace_id,
document_id,block_id}, anchor_id (canonical ID or null), replace_empty (boolean),
expected_target_version, expected_workspace_epoch, expected_document_epoch,
expected_database_epoch (positive when installed, otherwise null), operation_id.
No caller-supplied body, subtree, permissions or template location is accepted.
The target GET returns only the current exact target version/access fence; it
does not issue a grant. The frozen database.row.content.set payload remains
exactly {blocks} and has no added authority fields.

Preparation resolves the active target row and real original from current
snapshots. Node replay runs outside readers/writers. Immediately before commit,
the service reauthorizes captured source/target stamps and database epochs,
validates the immutable deterministic splice and exact row body/version. It
inserts one operation-derived new empty synced_block with synced_from and sync_id,
replaces only an exact empty paragraph or inserts after the exact body anchor,
and preserves sibling bodies, original contents, cells, appearance, templates,
main page, files and other metadata. A null anchor is allowed only for an empty
stored row. Inline/flat descendants of existing synced blocks are not admitted
as structural insertion targets by this packet. Supported ordinary nested slots
retain their placement and independent body identity.

One transaction commits the row version, page sequence/hash, immutable revision,
workspace metadata version, activity, audit, outbox and operation receipt. Failure
at every finish stage rolls all effects back. Concurrent processes using one
operation return one insertion and one receipt. An ID with a changed request
fails. Exact receipt replay runs before source preparation and needs current
physical target read access; a subsequently revoked original cannot prevent
recovery of an already committed result. Archived targets remain fenced until
restored; broader archive receipt handling is a separate operating boundary.

Mounted owned/linked row editors bind the physical owner endpoint while retaining
the visible instance lifecycle. Pending ordinary input is flushed first; target
DOM, blocks, selection, actor, scope and input revision must remain current.
IME, concurrent input, target replacement and navigation cancel preparation
before any durable paste intent or mutation. The clipboard is decoded before
that flush, so malformed/foreign data cannot authorize a save. Independent DB
and template bodies retain their explicit paste rejection.

The browser stores only the immutable semantic request before sending, in a
realm/actor/physical page/database/row namespace guarded by a browser lock.
An uncertain request makes the body read-only and offers result confirmation /
retry. Reload retains the exact request. Confirmation looks up its receipt before
retrying the same operation, adopts only a fresh current canonical body and
acknowledges after observing its committed version. It never overwrites newer
body state with a cached reply. A confirmed stale request is cleared only after
a current unknown receipt; denied or malformed responses retain recovery data.

Final gates: 10 new browser cases plus 9 affected browser regressions; 33 service
cases plus 18 unchanged read regressions; 141 Node checks; 7 JS and 20 Python
syntax checks. These use isolated fixtures. They do not claim new actual Notion
DB paste observation, native Windows mounted-reference IME proof, or normal
collaboration/SBE activation. The previous authenticated normal-app drag/focus
proof remains separate. Whole authorized goal active; operating completion
unproven and remaining evidence boundaries retained.
