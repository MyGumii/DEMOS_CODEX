# Synced originals in database bodies

Status (2026-10-03 successor): exact body reads, whole-page copy and existing
template/item instantiation are followed by the page-scoped canonical text writer
in [Database-owned synced text writes](synced_database_text_contract.md).
DB-body detach, partial copying, item/template reference mounts and scoped
original navigation remain incomplete. Ordinary runtime flags and physical
schemas are unchanged. Whole-goal completion requires remaining packets/review.

## Reference identity and visibility

The persisted explicit endpoint remains exactly `{document_id, block_id}`.
An original explicitly stores `synced_from: null`. References carry no original
content, rich HTML or children; legacy sync-ID-only blocks require adoption.

The authorized editing snapshot contains independent page, row and template
bodies. Synced lookup indexes visible bodies separately and requires exactly one
matching body for a public endpoint. It never guesses from ID collisions, even
when another matching block is ordinary text. Repeated unrelated ordinary IDs
remain valid in distinct bodies. Trashed databases/rows and rows promoted to pages
are excluded by the existing snapshot; archived templates are explicitly skipped.
The original hierarchy is built from its owning body, excluding other bodies and
unrelated siblings. It does not flatten all blocks into a fictitious page body.

The existing current-authority GET returns additive `locations.instance` and
`locations.origin` objects: `kind: page`, or `kind: row` with `database_id/row_id`,
or `kind: template` with `database_id/template_id`. Endpoint objects and witnesses
are unchanged. This metadata is not a new grant and is not a locator write API.

Both documents require current page access before preparation and are rechecked
with the existing sequence, workspace/document epoch and generation stamps after
preparation. A direct database grant alone does not authorize this page-scoped
GET. Existing database-only projection/instantiation guards still withhold bodies
containing synced/page references. No content is cached into instance storage.
Replay remains outside SQLite readers/writers and within the combined 64 MiB bound.

The identity-only packet initially advertised `edit_text: false` for DB-owned
endpoints. Its text-write successor now advertises current authorized edit
capability and commits to the original's exact canonical body. Template endpoints
require template-edit authority. Main-page originals retain the verified Yjs
writer. DB-body detach still uses the strict guard; no mutation manufactures a
main-page Yjs copy of a database-owned original. See the linked text contract.

## Whole-page copy

Block allocation still uses the owning body's namespace. Public synced endpoints
are checked against all copied visible body locations. Internal originals and
references can cross page/row/template boundaries: each original receives an
independent block identity and each included reference follows that exact copied
original and document. Nested children retain formatting while receiving fresh
identities. Derived group IDs use the exact resulting original pair.

References outside the copied subtree retain their complete original pair and
contain no fetched external content. Copies of copies use the same rules.
Ambiguous endpoint IDs, missing included originals, reference chains and stale
reference content fail during preparation. The existing canonical writer owns
authority fences, file budgets, receipts, immutable history, audit and outbox;
this packet adds no direct SQL write or migration path.

Partial database copy/detach plans containing row/template synced blocks remain
guarded until a complete original-location witness is carried by those plans.
An existing partial main-body plan may preserve an original outside its selected
blocks. It retains the entire external pair. A selected plain block or reference
cannot be accepted as a missing external original.

## Historical template and item instantiation

The existing independent-original behavior is preserved: originals included in
the source body receive new IDs, internal references reconnect to those originals,
and external references keep their endpoints. Every explicit synced shape is now
validated before rewriting. Local references must name an included explicit
original, and all group IDs are recomputed from the resulting endpoint, including
external groups with stale cached group metadata. Invalid or legacy shapes fail
without creating a row, receipt, audit event or document-version change.

This documents the application's current instantiation semantics. It is not a
claim of newly observed Notion template semantics. Official Notion documentation
establishes shared-content propagation and original-page access requirements,
but does not specify every original/reference template-instantiation case.
Those exact UI behaviors still require direct comparison. Primary references:
[Synced blocks](https://www.notion.com/help/synced-blocks) and
[Database templates](https://www.notion.com/help/database-templates?slug=database-templates).

## Template-reference successor (2026-10-03)

A fresh public-UI comparison used one disposable synthetic database, a template
with a synced original, and two created items. The original remained in the
template. Both items were references to that original; editing one updated the
template and the other. After unsyncing only the second item, a later template
edit updated the first item while the second retained its detached paragraph.
The temporary database was moved to recoverable Trash. Unknown account role,
plan and exact browser version prevent formal catalog live/differential promotion.

This successor supersedes only template instantiation in the preceding historical
section. Explicit, default and empty-item template application allocate fresh item
block identities while retaining every synced endpoint in its original body.
Included originals become empty references to their template pair; included
references retain their exact pair. Original content, rich HTML and children are
not stored in the item. Inline, flat, indentation and tab-owned descendants stay
in the template, and links to those retained descendants keep their existing
targets instead of naming nonexistent copies. Ordinary blocks remain independent.

Validation still precedes rewriting, including malformed synced descendants that
will not be copied. Existing item duplication and whole-page copy retain their
separately tested independent-original behavior. No existing item, receipt,
physical schema or runtime flag is rewritten. Existing source/template authority,
current read fences, database-only reference guards and detach contracts remain
authoritative; creating an item grants no template-edit permission.

Personal compatibility database item creation currently creates an ordinary
child page and has no canonical template instantiation adapter. This successor
does not silently activate that staged adapter in the operating personal app.
