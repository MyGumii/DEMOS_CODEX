# Two-way database relation (R5)

Actual Notion comparison on 2026-09-27 showed that enabling a two-way
relation creates an inverse property with existing links. Removing or adding
a link from that inverse property changes the original relation after reload.
Disabling two-way relation from the original property removes the inverse
property but keeps the original links. A related row moved out of its database
is hidden in the inverse value and reappears on return. The dedicated
comparison rows and property were restored to their original one-way state.

The local 2424 backend now has an atomic pair writer in
`notion_collaboration/database_relation_pair.py`. The enable operation creates
the inverse property, backfills existing inverse cells, advances both affected
documents, and emits durable events. The disable operation deletes the inverse
property while retaining the selected property's links. Both operations
revalidate edit rights, locks, document sequences, epochs, property versions,
and replay IDs. `database.cell.set` and `database.cells.set` update inverse
cells from either side in the same transaction and advance every affected
document. Same-document pairs advance that document only once. A failed
operation rolls back both sides. Generic schema updates may rename a paired
property but cannot forge, retarget, or detach the pair; generic deletion is
rejected until the pair is disabled. The browser bridge preserves reciprocal
metadata during an unrelated edit.
Moving a paired row to an ordinary page or another database retains its
original relation cell and hides the inverse value until the row returns.
The DB-to-DB mapper does not remap a paired relation to a superficially
similar destination column, because its inverse property names the original
property ID.
Copy remapping preserves a pair only when both owned databases are included
in the copied page subtree: both inverse property IDs and row IDs then point
to the copies. Copying one owned database detaches its copied relation from
the external inverse while retaining its links to the existing target DB.
It leaves the original pair and inverse cells untouched. Malformed inverse
metadata blocks the two-database copy rather than producing a broken pair.

The HTTP database command endpoint accepts `database.relation_pair.enable`
and `database.relation_pair.disable` behind
`collaborative_database_relation_pair`, which remains **false** by default.
Enable targets the existing source relation property and supplies the inverse
database/document, a new inverse property ID/name, both document versions,
and the source property version. Disable targets the property to retain and
supplies the inverse document/property versions. The property editor now
exposes pair enable/disable only when that capability is active. It reads
both documents' current versions before sending one exact command body with
a stable operation ID. The exact body is written to a per-user/workspace/page/
property browser journal before the POST. A pending result is shown in a
recovery banner after a full browser restart, and retry resubmits that same
body under a browser lock rather than creating a second inverse property.
Storage failure prevents submission. It prevents direct deletion, copy, or target changes
of a paired property. A permission-filtered database-source picker allows a
relation to target another document, and the cell editor loads that target's
actual rows. A direct source lookup endpoint resolves the target document
without relying on a paginated name search. No live user catalog or
production feature switch was changed.

The isolated tests cover enable with an existing link, disable preserving the
original link, edits in both directions, atomic cell batches, idempotent
replays, injected rollback, same-document ordering, HTTP gating, and a paired
row's ordinary-page and different-database round trips. Direct source lookup
has scoped-read and HTTP tests. Source-only edit rights, stale target document
sequence, stale property version, and identical requests from two processes
have isolated 2424 checks. The UI command flow has a focused enable, disable,
and lost-response retry test; remote target row loading has a focused test.
Two independent processes submitting opposite-side edits from the same
starting document versions yield one committed typed-command receipt and one
stale-version rejection. The source and inverse cells agree afterward, and
each affected document advances once.
Separate-client grant revocation is serialized with pair enable in two
scheduled process orders. If target edit permission is revoked first, enable
does not create an inverse property or receipt. If enable commits first, the
pair remains complete after the grant is revoked, and that actor can no
longer read the target. Both orders use a common process barrier and the
same initial permission/version snapshot.
An isolated authenticated Chrome run passed cross-page target search, actual
target row selection, relation pair enable with an aborted first response,
browser close/relaunch with restored cookies and local storage, exact journal retry,
inverse backfill, and disable while retaining the source link. A Node journal
check covers restart replay, conflicting new requests, and storage failure.
JavaScript syntax, bridge, and UI runtime tests also pass. The normal app at
port 10322 remained unavailable, and its real user catalog was not changed.
Pure remap checks cover copied two-sided pairs, malformed inverse metadata,
and single-DB external/internal pairs. A committed isolated 2424 owned DB
copy confirms that an external inverse is detached only on the copy, its
source links are retained, and retry returns the original receipt. The
focused copy/remap suite passed 28 checks, and the new committed pair-copy
check passed separately.
The outstanding work is more varied concurrent edits and broader R1–R11
validation. These checks do not prove complete Notion parity or readiness to
enable the feature in production.
