# Detached body materialization — 2026-09-23

`synced_detach_body` consumes immutable `PreparedDetachedPlacement` and returns
`PreparedDetachedBody`. It rebuilds the placement binding to validate its full
preparation chain, then substitutes the exact destination instance in the
captured destination body. Resources remain in the immutable content preparation.

Two deterministic ordinary paragraph layout containers retain destination slot
placement and source presentation separately. They contain explicit child ID
edges, carry no visible text, and are supported by the existing editor hierarchy.
Source outer column/tab membership is not transplanted. Independent inner
column/tab identities, styles, rich text, original child order and a focusable
reference-ID anchor survive. Local destination descendants stay outside source
presentation. Same-page originals retain their semantic tree; flattening changes
representation and global position fields.

The result rejects identity collisions, missing/duplicated blocks, ownership
changes, excessive indentation/depth/size and modified preparation. The reader
revalidates current source/destination authorities, source witnesses and resource
records through the existing placement chain. Preparation performs no writes.

`commit_ready` is always false. `required_integrations` retains
`atomic_destination_writer` and pending child-page handling. No route, UI menu,
permission grant, capability, schema migration, or operating behavior is enabled.
The future writer must atomically commit resources, canonical body, CRDT update,
receipt, audit and outbox, with exact revalidation and retry/draft coordination.

Validation: 16 new + 14 existing Python checks passed; cross-language checks run
the shipped editor core on materialized blocks. 26 existing editor regression
checks passed. No browser pixels, actual Notion comparison, native IME, actual
unsync action or operating rollout was certified in this packet.
