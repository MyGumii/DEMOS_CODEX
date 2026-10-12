# Synced clipboard copy and main editor paste

Status (2026-10-03): verified isolated staged paths. This supersedes only the
immediate same-page insertion of the shared supported explicit-original toolbar
in synced_locations_contract.md. Legacy group/block-menu/duplicate behavior and
all frozen read, detach, mutation authority and rollout contracts remain scoped.

The shared original/reference toolbar reads the selected current instance after
flushing supported pending text. Copy serializes the real original endpoint,
owning workspace and canonical application origin. It neither adds a source-page
block nor writes the source body. Viewer copy is allowed only by the existing
current read service. The payload is exact version 1 of
code_assist.notion.synced_clipboard.v1, max 16 KiB UTF-8 JSON, with fields version,
contract, application_origin, source:{workspace_id,document_id,block_id}. It
contains no body, descendants, witnesses, credentials or grants.

Native copy writes application/x-code-assist-synced-reference+json, an escaped
HTML data-notion-synced-reference marker and a plain original-block URL. A capture
listener prevents the ordinary selected-block handler from adding a second MIME
with cached content. The listener is removed on every path. Navigator clipboard
fallback writes bounded HTML/plain formats. Copy failure and obsolete/composing
mounts cannot announce success. The current success notice uses the existing
status region; exact Notion toast styling is still outside this gate.

Automatic redraw of an unchanged main body must not cancel a copy read already
initiated by its current toolbar. Its continuation requires the same realm,
actor, workspace, page, navigation sequence, one explicit block, unchanged real
original and a current visible main toolbar. Removed/hidden mounts, changed
endpoints and composition invalidate it. DB copy retains its mounted host guard.
Paste still requires its exact unchanged target DOM and body snapshot. Only an
identical projection redraw is deferred while the source read is pending. Changes
to body, selection, page identity, composition or write readiness immediately
release this hold; actual DOM replacement cancels paste. Deferred redraw is
flushed when the request settles, without restoring a departed page.

Main body editable-field or selected-block paste recognizes the descriptor before
ordinary block/text paste. It requires the same application origin, exact fields
and canonical IDs, a current editable supported page, completed initial replay
and current write authorization, then rereads the real original through the
existing no-store service. Original identity and workspace must still match.
Clipboard content cannot enlarge read or edit authority.

Paste inserts only a newly identified synced_block with empty content and exact
synced_from:{document_id,block_id}; it carries no cached subtree and cannot chain
through a reference. It replaces a solitary empty paragraph or inserts after the
target using the existing block structure/history/editor bridge. This adds no
HTTP creation route or migration. Transient socket disconnect alone does not
discard the existing authorized durable outbox. Initial replay or lost write
authorization still prevents paste. The canonical writer retains its independent
service validation; earlier service-preparation/editor insertion races remain
part of the broader operating gate.

Scope, target DOM, all body blocks and selection must remain unchanged while
reading. Concurrent typing, navigation, replacement, IME composition, malformed
or foreign payload, inaccessible original and late results cannot insert a block
or revive an old target. DB row/template toolbars can prepare the same descriptor,
but body structural paste is explicitly rejected with guidance to paste in a
page. Shared reference-text fields also reject structural synced paste.

Final evidence: 4 new and 11 affected browser cases, 18 unchanged server read
regressions, 89 Node checks including ordinary clipboard and detach compatibility,
6 JS syntax and 8 Python syntax checks. Normal ui384, actions ui6, clipboard ui1
and DB-editor ui8 are loaded; the logged-in page retains its title and 13 rows.
Actual gutter drag selects 7 body rows, excludes the title, focuses the page
editor and Escape clears selection. Fixtures alone perform content writes.

Actual Notion copy completion and no immediate insertion were directly observed
in the sealed previous locations packet. Those observations are reused, not
represented as new Notion paste or DB edits. Full hidden-body editing, DB partial
copy, legacy adoption, unsupported template structures/history/archive boundaries,
native mounted-reference IME/navigation overlap, performance and complete
operating release/feature activation remain independent gates. Whole goal active.
