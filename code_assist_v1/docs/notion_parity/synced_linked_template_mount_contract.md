# Editable linked-template synced references

Status (2026-10-03): supported staged linked templates mount current synced
references, use physical-owner text APIs, retain source data on ordinary saves,
and refuse stale whole-form drafts. The whole goal remains active.

Owner resolution joins one authorized lookup. Clear, obsolete scope and replaced
records fence its awaited result; cancellation never creates a fresh lookup.
The physical owner supplies the template location and encrypted text journal.
The visible linked page still controls modal lifecycle and editability.

The whole-form editor pins its base template version. Its controller rejects
invalid or changed explicit versions before allocating an operation or journal.
Only a clean form with identical name/default/property metadata adopts source
body/version refreshes. Dirty input keeps its old baseline; it cannot overwrite
remote source text using a newer projection version. Reference writes are paused
during ordinary drafts, composition and full-form jobs. Source text is edited
through references; protected source subtree text fields are read-only here.
Closing flushes shared text before handling ordinary draft dismissal.

Scoped server saves retain every existing synced subtree at its exact path and
full canonical value. Ordinary fields pass the existing unscoped-reference guard;
defaults, file and relation checks remain. Parent read authority is revalidated
inside the existing transaction, including exact receipt replay. No new page or
source grant is inferred. New, removed, moved or edited protected subtrees and
ordinary page-reference injections are rejected. Template/document versions,
current DB/instance grants, locks, operation hash, atomic audit and outbox remain.
No SQL-time external work, schema or runtime activation is introduced.

Resource uploads retain their mounted job/picker until application of the result.
Controls stop writes while busy; an exact pending whole-form request can still
be retried. Loss of source access clears name/defaults/body, disables Save and
clears reference views. The redacted modal closes without a discard prompt.

Final browser5 covers light1280/dark390 clean source plus ordinary saves and
source navigation, independent two-session stale drafts without a PATCH,
and source-parent revocation without mutations. Affected browser6 covers linked
template defaults/files/relations/lost-response retry and owned row/template
mounts. Server24 includes14 new cases, three atomic fault stages, current revoke,
one real two-process exact replay and10 existing page-reference/linked grant
checks. Node92 and syntax checks retain journal/scope/conflict contracts.
Normal personal runtime loads ui371 with its existing title and13 blocks.

Hidden owner navigation, structural synced edits, detach/partial copy, unsupported
legacy/history, broader overlap and the operating release review remain guarded.
These synthetic gates do not establish fresh direct Notion or native IME parity.
