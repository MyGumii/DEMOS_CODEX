# Native page discovery and member navigation

Status: implemented and verified on temporary catalogs. The native creation
packet subsequently upgraded the named disposable 14534 fixture to 2415 and
verified creation, list discovery and favorites in Chrome. Operating catalogs
have not been migrated. See [native creation](native_page_creation_contract.md)
for its API/UI integration and remaining cross-device notification gate.

## Required result

An authorized native page appears in the page list alongside existing bound
pages. A member can favorite either origin using one ordered personal list and
one favorite version. Sidebar settings retain their independent version. Guest
settings remain in the existing guest service and table.

## Storage and offline migration

The forward 2414→2415 migration adds `workspace_member_navigation_states` with
one row per workspace/member. It copies existing sidebar width/collapse,
preference/favorite versions, ordered sections, ordered favorites and update
time. The default five sections are materialized only when the prior state has
no section rows. Hidden favorites remain stored for permission restoration.

The migration requires the exact source schema and plan, verified backup and
quiescent catalog. No startup or request executes it. Existing navigation,
favorite, binding, operation, audit and outbox tables remain historical and are
not dual-written. SQL and actual schema fingerprints are frozen in the migration
module. Failure before commit preserves the original catalog.

The runtime accepts the exact 2415 marker, keeps prior schema behavior and uses
the existing image/editing/archive authorities. A member branch is selected
before the guest branch, since an identity can have an active membership and a
historical guest row. Revocation does not convert member settings to guest data.

## Read and mutation contract

Reads are side-effect-free and require an active identity and current membership.
Absent rows return defaults. Native favorites require explicit native registry
provenance; legacy favorites require an active matching owner/page binding.
Both require an active canonical document and current effective page permission.
Existing unbound canonical documents are never automatically adopted.

Invisible favorites are omitted and returned positions are dense. Their stored
order remains unchanged so archiving/restoring a page preserves its place.
Native list discovery also passes through the current permission and metadata
projection, with inaccessible parents removed from the returned hierarchy.

Mutations validate complete settings, bounded versions, owner/page identities
and expected preference/favorite version. A favorite add/remove no-op preserves
the favorite version. The existing 100-favorite bound includes temporarily
invisible stored entries. Preference writes do not change the favorite version.

Current authority is checked before replay and again before commit. State,
canonical receipt, event, audit and outbox commit atomically. Workspace event
payloads contain only the actor and the two navigation versions, never private
settings or saved page IDs. Exact retries return the original receipt without
touching state, even after later navigation writes.

An operation completed before cutover is replayed through the original immutable
operation/audit/outbox validator with the original action/request hash. It is not
reapplied against the new versions. Reusing an operation across navigation
actions or with different input fails. Current membership and target access are
still required for an old or new receipt.

## Evidence and remaining UI gate

Isolated tests cover migration preservation and prerequisites, fault rollback,
old receipts, mixed page origins and order, native hierarchy discovery, archive
and restore, guest/member separation and revocation, exact retries, CAS, no-op,
inactive identity and owner mismatch, private event payloads, actual navigation
HTTP routes and two spawned Windows processes committing one favorite.

The browser's actual Notion tab was accessible on 2026-09-15. The existing 14534
verification tab still timed out during state/dialog inspection. This is not
browser completion evidence for the new creation flow. The existing named
2413 fixture was read-only checked against its original body/history hashes.
