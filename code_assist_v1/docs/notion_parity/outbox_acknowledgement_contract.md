# Queued edit acknowledgements

An edit can be queued while preceding structural commands are still being
committed. Its creation-time acknowledgement can therefore precede the last
acknowledgement accepted for the same client session.

New browser envelopes have `submission_started: false`. After their structural
commands finish, the browser advances their acknowledgement to its current
durable cursor, persists `submission_started: true`, and only then transmits.
Subsequent retries preserve the exact acknowledgement and operation identity.
Legacy records without this marker are treated as possibly submitted; they are
first retransmitted unchanged.

Only an `AcknowledgementConflict` raised by the durable submit authority yields
the WebSocket response `submit_rejected` with error
`ACKNOWLEDGEMENT_CONFLICT`, operation ID, client sequence and attempted
acknowledgement. The rejected transaction has not committed. The connection
remains open for replay and corrected submission. Authorization checks, sequence
validation, immutable operation receipts and transaction rollback are unchanged.
Other failures retain the existing error/close behavior.

The browser accepts this permission only for an exact matching in-flight request
in its current client/session. When its durable cursor has advanced, it persists
the unsent state and retries the same content and operation with the newer
acknowledgement. Without a forward cursor it preserves the draft in recovery
mode. Unknown responses, stale scopes, lost acknowledgements and generic errors
never authorize rewriting an uncertain request.

Evidence: `test_notion_outbox_acknowledgement.js`,
`test_gateway_acknowledgement_recovery.py`, existing sequence/replay and lifecycle
tests, and the isolated column-compaction browser fixture. The browser recovered
three pre-existing queued edits, performed further block moves and Korean text
entry, and retained the result after reload. This is not a production rollout or
a claim of full editor/Notion parity.
