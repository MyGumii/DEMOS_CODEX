# Database item comment authority

An item comment belongs to the exact `(source document, database, row)` identity.
It uses the existing canonical comment table and transaction, rather than making
the row a second document or storing a separate copy of the title or body.

## Availability and request contract

This is a runtime extension on the exact supported item-body schemas 2418 and
2419. There is no new migration, DDL, backfill, production flag transition, or
production data fixture. Workspace capability `database_row_comments` and
database capability `row_comments` advertise the server support. They do not
claim the client comment composer is integrated.

Use the existing authenticated comment endpoint for the source document:

`/api/notion/workspaces/{workspace}/pages/{source}/comments`

- Ordinary GET returns document/block comments and excludes item comments.
- Item GET requires both query fields `database_id` and `row_id`. The database
  must belong to that exact source and the row to that database. Missing one
  field or malformed identifiers fail validation; a wrong binding is denied.
  Successful item reads echo `item_target` so a client can validate the response
  against its current item, including when the list is empty.
- POST keeps the existing comment ID, content, operation ID, current epochs,
  optional parent and mention subject IDs. Its item anchor must be exactly
  `{"kind":"database_row","database_id":"…","row_id":"…"}`. IDs must be
  canonical strings. An item anchor cannot also use a source document block ID.
- Replies must use the same item target as their parent. A page thread cannot
  acquire an item reply, nor can a reply cross between two rows.
- Existing edit, delete, resolve/reopen and reaction endpoints address the
  immutable comment ID. The service revalidates its source permission and row
  binding; it does not trust a client-selected item or visual filter.

The source's current `view` role is required for reads and `comment` role for
creation, replies, resolution and reactions. Existing author/manager restrictions
still apply to edit and delete. Database edit permission is not a prerequisite
for commenting. Revoked access cannot be recovered by replaying an old request.

## Transactions, retry and deletion

Creation, edits, mentions, notifications, version increments, receipt, event,
audit and outbox use the existing service-layer transaction. Item comments do
not modify database cells, row body, appearance, row version or source bindings.
As with ordinary comments, a comment write advances the source document sequence.

Creation retries preserve the original operation ID and request. Comment edits
use the current comment row version; a stale edit fails instead of overwriting.
A successful soft deletion can be retried with the same request: authorization
can inspect the tombstone without allowing a second deletion command to edit it.

Database row deletion retires all active item threads in its existing transaction
and redacts their notifications. It increments comment versions and timestamps
while retaining stored comment content as a tombstone. Any fault before commit
rolls the entire row-and-comment change back. Recreating a row with the same ID
does not revive retired comments or accept a retry of their old creation request.
Restoring a deleted database row through page history remains unsupported; this
packet does not add a comment restoration path.

Inbox reads include the validated `item_target` for item notifications. Deleted
rows do not yield notifications or allow read acknowledgment of a redacted
notification. Ordinary notification response fields remain unchanged.
Comment and inbox reads pin one SQLite read snapshot before authorization and
target lookup. A concurrent row deletion cannot produce a list assembled partly
before and partly after the deletion; the next read observes the committed state.

## Reference and client integration work

Actual Chrome inspection of the Notion reference item on 2026-09-22 showed the
comment section between properties and body, with an avatar, empty composer,
attachment/mention controls and disabled send button. No comment was posted;
thread menus and existing-comment behavior were not verified on that reference.

The 2026-09-23 client integration adds the shared item composer and list between
properties and body. Creation, replies, author/manager edits and deletion,
resolution/reopening, reactions and user mention insertion use the existing
source document comment endpoints. Linked views address the real source from
their witnesses; the host document is not the comment destination. Capability
gating hides the new section on unsupported backends. The server's canonical
`owner` role represents full access and is explicitly supported by the composer.

Drafts and the exact pending request share one encrypted outbox record scoped by
realm, subject, source workspace/document, database and row. Acknowledgment
atomically clears the request and submitted draft together. A failed follow-up
read cannot resend an acknowledged write. Local discard explicitly clears only
that device's request/draft, leaving committed server comments intact. Web Locks
and a canonical fingerprint check prevent a stale tab from replacing another
tab's recorded draft/request. Storage failures retain the in-memory draft and
block departure until persistence succeeds. Drafts are never sent by navigation.

Editing drafts retain the original comment version across reload. Refreshing the
list does not silently rebase an edit over another author's change. A mismatch
keeps the draft and asks the user to review the current comment. All actions still
use the server's current authority and version checks. Deleted root comments do
not hide surviving replies. Existing body/property/appearance saves precede a
comment submission; unresolved comment requests block competing item departure
while the source remains available. When the source becomes unavailable, the
pane can close after pending local writes and the in-flight request settle.
This parks the exact request without resending or deleting it. An authorized
reopen restores it; changed authorization epochs still reject an old retry.
Explicit local discard remains available after reviewing the stored comment.

Comment composition and keyboard events do not reach body/item shortcuts. Enter
adds a newline, Ctrl/Cmd+Enter sends, and Esc dismisses local controls or blurs the
composer while preserving its draft. Native Windows IME in this new composer
has not been manually verified. The earlier slash IME fix is unchanged.

Inbox item targets now open the canonical row before acknowledging the alert.
Browser evidence covers owned items, side/full modes, draft/reload, request loss,
actions, remote-edit conflict, inbox routing and 390px width. Controller tests
cover comment-only/view roles and scope rejection; these are not a substitute
for actual browser verification of every role and linked-source combination.

The 2026-09-23 access follow-up exposes `row_comments` on linked databases only
when the source schema supports it, within the existing pinned read snapshot.
It does not advertise unsupported body or appearance editing. Source witness
changes refresh the comment list and role while preserving the draft. Explicit
item refresh also reads comments when the witness is unchanged. Refresh requests
during initialization or submission wait; a change during an active read queues
another read. Reads stop when the item is no longer current. This uses the linked
view's existing polling/focus/manual refresh, not a new live subscription.

Actual Chrome coverage now includes a view-only host with comment-only source,
comment-to-view-to-comment changes, author-specific menus, external source
comments, and full source revocation while a request is uncertain. The request
survives closure/reopen; an exact retry after epoch changes returns 409 and does
not duplicate the stored comment. Two-step local discard leaves that comment
intact. A fixture-specific session cookie keeps the ordinary app login separate.
This is one same-workspace linked-source fixture, not coverage of all roles.
File attachments, page/date mentions, full Notion thread menu parity, arbitrary
reaction selection and full theme/role validation remain. No production
migration, feature-flag transition or 10322 rollout is claimed.
