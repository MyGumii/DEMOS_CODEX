# Attributed text in a copied generation

This successor preserves raw Y.Text attributes during the staged native
full-page/subtree copy. The canonical descriptor still contains title and
four-field blocks; HTML rendering continues to use the existing content/props
contract. Raw attributes are not a new rendering or formatting UI.

Preparation captures an immutable optional text sidecar outside SQLite from
the same authorized current-generation frames and certified deletion ranges
as the existing page-copy source. Original-value restoration keeps archived
runs; explicit replacement uses the replacement text without inventing old
attributes. Current body, title, source stamps and recovery refusal rules remain
owned by the existing source/rebase services.

The established remapper allocates fresh page/block/file identities. Recognized
string `link`, `href`, `url` and `src` attributes use its existing local-origin,
page/item/fragment and file URL rules. Other attribute values remain opaque.
The optional sidecar includes only attributed fields and participates in the
64 MiB source/plan budget. Non-string text runs are not represented as a plain
text formatting sidecar.

For attributed documents, a new generation-1 body/title update is prepared
with the pinned Yjs runtime outside SQLite. A separate decoder checks the
whole body, title, order, props and every attributed run against the remapped
descriptor/sidecar. Each seed is at most 1 MiB, matching the existing durable
update limit. Metadata plus sidecar plus prepared seeds fit the preparation
budget; capacity headroom includes seeds.

The copy writer revalidates current identity, source/subtree/parent/workspace
stamps, epochs and locks before writing. It commits the copied SQL descriptor,
revision, fresh generation, initial checkpoint, initial update/binding, files,
native-copy registration and existing creation/copy receipt/audit/outbox in
one transaction. The seed is update sequence 1 at document sequence 1, part
of initial creation. The immutable initial checkpoint records high-water 1.
No browser ticket, session ACK or editing receipt is fabricated. Plain copies
retain their prior empty initial stream and high-water 0.

Exact copy retries return the original receipt. Concurrent processes may prepare
independently; the winning transaction owns the one subtree and seed set.
Late source formatting changes advance the durable source stamp and fence the
entire copy. A preparation/decoder/capacity/fault failure cannot leave a partial
copied stream. Existing source frames, checkpoints and tombstones are untouched.

This packet does not adopt synced item/template bodies, relax uncertified
history guards, instantiate unknown attribute schemas or migrate existing
copies. Collaborative operating flags/schema remain separately gated. The
personal atomic-save approval does not activate this staged capability.

Gate evidence is maintained by the scoped integrator at
`outputs/copy-rich-generation-20261003/phase-evidence.json` in the task
workspace. It records exact runs and source versions; it must not turn a
packet pass into full R1–R11/C/D/E/F/extension/operating completion.
