# Database-owned synced text writes

Status (2026-10-03): staged page-scoped text API and main-page reference editor
can update an exact visible row/template original. No new schema, runtime flag
or collaboration activation is introduced. DB-body detach, item/template editor
mounts and scoped original navigation remain separate work.

## Authority and preparation

Explicit endpoints remain exact document/block pairs; owning-body lookup must
resolve exactly one visible body. Source trees stop at unrelated siblings and
nested references. The caller's existing witnesses and operation hash are
unchanged. Both endpoint documents require current page write access and unlock:
row/page locations require edit_content; any template endpoint requires edit on
its page. On an installed sharing schema, the owning DB role and DB access epoch
are also revalidated. A direct database grant never grants page-scoped access.

Current materialization and pure body plans run outside SQLite readers/writers.
Reader and commit phases recheck workspace/document/sequence/generation stamps,
captured DB epochs and exact current authority. Lost authority blocks saved
receipt disclosure as well as a new write. Main-page originals retain their
existing Yjs delta writer and reject unexplained canonical/replayed structure
mismatches. A CRDT-only main reference can still edit a DB original because that
writer never changes the main body or frames.

## Canonical commit

The frozen plan contains the exact body location, positive row/template version,
full old canonical body JSON, resulting JSON and checksum. Independent plan
validation recomputes the rewrite without calling the public preparation hook.
Only requested owned text leaves change content/rich_text_html; unrelated block
metadata, positions, template names/defaults, cells and empty references survive.
Code/equation HTML is refused. Existing1000 root block,1 MiB body,100 leaf update,
256 KiB request and64 MiB source budgets remain in force.

The service transaction rechecks the active row/template and old body/version,
changes that one canonical body and advances its version and owning document
sequence once. It recomputes the canonical content hash and records DB activity
when installed. No main-page CRDT frame, seed or generation is manufactured.
The existing text response contract carries additive database_body/location/DB/
target-version/checksum fields. Its unchanged witnesses let the current inline
controller acknowledge the write and refresh the authorized original read.

Receipt, audit, event and outbox commit atomically; the event uses a derived
operation namespace to avoid colliding with an ordinary DB command using the
same caller operation ID. Current authorized identical retries return the saved
result without writes; changed requests conflict. Two spawned OS processes
preparing the same request produce one body version increment and one receipt.

## History, UI and limitations

Checkpoints include the changed canonical body. Row-body history can restore the
text with monotonically increasing versions. Template body changes still cross
the existing DB structure history boundary, so that restore refuses atomically;
this packet does not claim template restore support.

Light1280 and dark390 synthetic browser cases edit a main reference into its DB
row original, refresh other source edits, copy the whole page and preserve an
independent copy after source changes/reload. Existing view/comment topbar
permission/layout tests remain green. A metadata redraw between native pointer
down/up previously replaced the pressed SVG and cancelled More; static icon
nodes now survive unchanged-icon redraws, while role/title/visibility updates
still run. The held-pointer reproduction and ordinary menu click pass.

This is application evidence, not new direct Notion observation. Exact Notion
template instantiation and original navigation remain unobserved in this packet.
Partial DB copy/detach needs complete body witnesses; nested/archived legacy
groups, unsupported history, DB-body editor mounts and operating release remain.
