# Atomic detached destination commit — 2026-09-23

`synced_detach_commit.commit_prepared_detach` consumes an immutable
`PreparedDetachedDelta`. It first looks for an exact operation receipt, then
checks capacity outside the writer. Its single SQLite write transaction replays
the request or revalidates current source/destination authority, resource
records, captured CRDT generation and exact frame digest before any change.

The writer replaces the target's canonical blocks, inserts independently mapped
canonical databases (including rows, cells, item bodies/appearance, views and
templates), and inserts file metadata with verified source provenance. It checks
the resulting canonical descriptor against the captured destination metadata,
prepared body and expected existing/new DB/file identities. Source documents
remain untouched. It appends each prepared Yjs frame with its own durable
sequence, binding, event, audit and outbox record. A final separate sequence
stores the revised canonical snapshot and exact-operation receipt. One SQLite
rollback covers all records, including fault injection after outbox insertion.

The operation ID and request hash bind actor, workspace, destination, reference,
original endpoint, exact witnesses and trusted application origin. Replay checks
current destination view authority and never recopies resources. A child-page
block inside the detached content becomes a normal link to the existing page,
without claiming page ownership or cloning its document. This matches the
direct Notion comparison on 2026-09-23: the original retained its child page;
the unsynced reference showed a page-link arrow to the same page ID.

This internal service is not a public unsync feature. No route, menu, capability,
schema migration or operating setting is enabled. Browser collaboration,
response-loss lookup, draft coordination, cross-workspace
permission matrix and full parity still need validation. Three isolated real
catalog tests passed: source/target preservation plus replay, late-outbox fault
rollback plus retry, and stale-source refusal. No fresh actual Notion or UI
inspection was performed in this packet.
