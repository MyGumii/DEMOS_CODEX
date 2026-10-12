# Detached Yjs delta preparation — 2026-09-23

`synced_detach_delta.py` consumes a verified `PreparedDetachedBody`. It rereads
the captured destination's current generation and checked frame bytes, validates
the whole detach authority/resource chain, and invokes the pinned Yjs 13.6.32
runtime outside any SQLite transaction. After Node returns, it revalidates the
same current authority, generation and frame digest. No writer is called.

The Node transform verifies the replayed block graph against the captured body.
It changes only replaced block properties/content and the necessary order span;
unchanged Y.Text objects retain their identity. Generation-one bodies with no
structural frames are bootstrapped using the existing deterministic client ID.
Title-only frames retain their title. Long text is inserted in UTF-16 chunks
without splitting surrogate pairs. Emitted Yjs events are merged into durable
frames of at most 1 MiB each. A second replay must reproduce the exact target
block graph before the result is accepted.

The immutable `PreparedDetachedDelta` binds update frame hashes/lengths, source
frame digest, document/generation/operation and body preparation hash. Its
revalidator checks the current source/destination authority chain and exact
frame digest inside a future writer transaction. The result is preparation
only: `commit_ready=false`. It neither creates a receipt nor changes canonical
resources, body, revision, CRDT history, audit, or outbox.

Validation: six Node cases cover unchanged text identity, normal and initial
generation replay, large update splitting, a deliberately split emoji boundary,
and stale/malformed data. One isolated real-catalog Python integration proves
read-only preparation, exact replay, changed-source refusal and tamper refusal.
No browser unsync, actual Notion comparison or operating rollout was certified.
