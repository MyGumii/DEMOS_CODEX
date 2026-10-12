# SBE-3.1 evidence

Status: VERIFIED

This record closes `SBE-3.1 - Canonical rich upload and explicit references`.
It does not authorize a live migration, successor feature-flag activation,
production traffic, company-provider calls, semantic-edge model execution, or
the v12 temporal/footer work.

## 1. Result

- Started: 2026-08-24 Asia/Seoul.
- Verified: 2026-08-24 Asia/Seoul.
- Prerequisites: SBE-1.3, SBE-2.3, and SBE-3.0 are VERIFIED.
- Acceptance result: `UPLOAD-005..012` and `REF-003..005` pass.
- Schema result: no migration was added. Canonical v11 remains the immutable
  physical schema head; the approved temporal change remains owned by v12.
- Runtime result: all successor traffic flags remain disabled by default.

## 2. Delivered boundary

The packet provides a complete local canonical upload/reference pipeline:

- Native rich extraction for PDF, DOCX, PPTX, XLSX, Markdown, and plain text.
- Deterministic canonical Markdown plus bounded embedded-asset children.
- Asset bytes are read and verified outside a DB transaction, then raw source,
  extraction snapshot, child artifact/blob links, and body references commit
  atomically through canonical repositories.
- The preview source and original filename are frozen before the publication
  transaction and hash-revalidated immediately before commit.
- Every newly created publication target queues one exact system curation job
  in the publication transaction. No-blocker jobs automatically publish the
  item, install FTS/search state, increment the space generation, emit ordinary
  audit/outbox records, and append handler-independent semantic intents in the
  same transaction. They create zero semantic-model jobs.
- Strong normalized near-duplicates remain provisional with a T2 human-review
  finding. Deterministic blockers quarantine the item. Neither state leaks into
  search/projection or creates semantic intents.
- `personal + Part` creates two independent items and revision histories and
  queues two independently fenced curation jobs in one publication transaction.
- An exact body re-upload with the same original filename adds provenance to
  the existing item. The same bytes under an ordinary changed filename create
  a new item. Later edits never synchronize independent copies.
- Production composition and the isolated full Second Brain preview both use
  the rich extractor and canonical curation worker. The preview no longer uses
  a fake human release shortcut.

## 3. Rich extraction and asset contract

The extractor performs all file/parser work outside the canonical write
transaction. It accepts only the frozen native format set and does not silently
invoke a configured external converter. It emits normalized Markdown and a
bounded asset manifest with these hard limits:

- at most 64 embedded assets;
- at most 16 MiB per asset;
- at most 64 MiB for the complete embedded-asset bundle.

DOCX/PPTX/XLSX relationship payloads and supported embedded images become
immutable child artifacts addressed as `asset://sha256/<digest>`. PDF text,
spreadsheet sheets/cells, slide text, Markdown, and plain text produce stable
canonical Markdown. Parser/converter errors are sanitized, temporary files are
cleaned, and source bytes or local paths are not disclosed in durable errors.

`ArtifactRepository.put_source_extraction` prepares every child before SQL,
stores its verified CAS/blob metadata and artifact-body link in the extraction
transaction, and verifies an exact replay rather than duplicating a child. A
failed extraction cannot carry embedded assets.

## 4. Curation, visibility, and projection flow

1. Preview extraction stores the raw artifact and immutable canonical Markdown
   snapshot without creating a knowledge item.
2. Publication freezes and verifies the preview/source evidence and resolves
   every requested physical target using current server authority.
3. The transaction creates the provisional item/revision/source provenance,
   audit/outbox state, and one system `curation.rescan/v1` job per new target.
4. The curation worker loads and verifies the body outside its decision
   transaction, then revalidates the exact revision/body/state fences inside it.
5. A blocker quarantines; a strong near-duplicate creates a pending T2 finding;
   otherwise the item becomes published with search/FTS, generation, audit,
   outbox, and bounded local/ancestor semantic-intent rows atomically.
6. The generic outbox projector accepts the new event types without an event
   allowlist. For a space-bound event it rebuilds from canonical DB truth, so a
   published item appears in the next immutable Markdown/index/log/graph
   generation while provisional/quarantined items remain absent.
7. Projection files remain read-only derivatives. No projection path imports or
   writes canonical item/reference state.

Crash, claim-expiry, response-loss, and retry tests prove exact terminal effect:
the publication/job kernels replay their stored result or resume a fenced
attempt and never duplicate an item, asset, reference, transition, intent,
audit, or outbox effect.

## 5. Explicit-reference contract

- Only exact `[[item:<lowercase-canonical-uuid>]]` syntax version
  `item-ref-v1` is recognized. Title-only, uppercase UUID, malformed,
  unterminated, control, injected, or duplicate-ordinal forms fail safely.
- Parsed references are resolved from frozen Markdown before the write
  transaction and are accepted only when the target is active, published, and
  in the server-resolved source view/closure. A forged, concealed, sibling, or
  foreign target returns a generic error and rolls back all publication rows.
- The curation release transaction re-resolves the actor and closure, rechecks
  source/target revisions and row versions, and stores source/target spaces,
  observed target revision, ordinal, closure proof, actor, operation/effect
  key, and release audit/outbox linkage in `knowledge_explicit_reference`.
- Explicit references do not create local or aggregate semantic graph edges and
  do not change graph degree or semantic confidence.
- A later target edit leaves the stored target revision unchanged while readers
  can compare it with the newer current revision; no silent retarget occurs.
- Target deactivation atomically changes every currently available incoming
  reference to `unavailable/target_inactive` in the same lifecycle transaction.
  A fault immediately after the reference transition rolls back both the item
  deactivation and reference state. Exact lifecycle replay does not increment
  the reference row again.

Archive and permission-loss producers can use the same frozen v10 unavailable
reason vocabulary in their owning later packets. UI ordered reference lists and
bounded Agent reference context remain assigned to REF-006..008 in SBE-5.1 and
SBE-6.1.

## 6. Acceptance proof

| ID | Verified outcome |
|---|---|
| `UPLOAD-005` | PDF, DOCX, PPTX, XLSX, Markdown, and text extract to canonical Markdown through native local adapters. |
| `UPLOAD-006` | Embedded assets are bounded, hash-addressed child artifacts and commit/replay atomically with extraction. |
| `UPLOAD-007` | No-blocker curation publishes with FTS/search, audit/outbox, generation, projection visibility, and semantic intents; zero semantic jobs are created. |
| `UPLOAD-008` | Strong near-duplicate and blocker paths remain hidden as provisional/pending-review or quarantined with exact durable findings/events. |
| `UPLOAD-009` | Personal, Part, and atomic personal+Part publication preserve exact independent target behavior. |
| `UPLOAD-010` | Same filename/body adds provenance; an ordinary changed filename creates a new item; source identity and revision history remain exact. |
| `UPLOAD-011` | Transaction failpoints, worker crash/reclaim, response loss, and retry converge with one effect and no partial visibility. |
| `UPLOAD-012` | SQLite canonical state is authoritative; projection is derived, rebuildable, and never a reverse-write source. |
| `REF-003` | Only frozen stable-ID syntax is parsed; malformed/title/control/injected input fails safely. |
| `REF-004` | Reference targets are closure-authorized twice and concealed/foreign targets commit zero domain rows. |
| `REF-005` | Full immutable provenance/replay is stored; edits expose revision drift without retarget; target deactivation is explicitly unavailable and atomic. |

## 7. Verification gates

Focused reference-lifecycle proof after the final availability integration:

```text
..\venv\Scripts\python.exe -m pytest \
  tests\knowledge_spaces\unit\test_item_lifecycle_service.py \
  -q -x -p no:cacheprovider \
  -k "stable_reference_unavailable or reference_unavailability_and_target_deactivation"
2 passed, 63 deselected in 4.92s

..\venv\Scripts\python.exe -m pytest \
  tests\knowledge_spaces\unit\test_item_lifecycle_service.py \
  -q -x -p no:cacheprovider
65 passed in 105.58s
```

Projection/outbox compatibility and canonical-read-only regression:

```text
..\venv\Scripts\python.exe -m pytest \
  tests\knowledge_spaces\unit\test_projection_materializer.py \
  tests\knowledge_spaces\unit\test_projection_worker.py \
  tests\knowledge_spaces\unit\test_publication_routes.py \
  tests\knowledge_spaces\unit\test_publication_preview_repository.py \
  tests\knowledge_spaces\unit\test_blob_gc_service.py \
  -q -x -p no:cacheprovider
59 passed in 96.51s
```

The final affected rich-upload, publication, job, edit/lifecycle, reference,
v10/v11 migration, projection, production-composition, isolated-preview, and
contract-audit aggregate passed after `py_compile`:

```text
..\venv\Scripts\python.exe -m pytest \
  <20 affected unit/integration modules> \
  -q -x -p no:cacheprovider
363 passed in 676.48s
```

Earlier development aggregates exposed four non-product or expected-contract
drifts, which were corrected and rerun rather than hidden:

- a parser test used uppercase generated UUID text while testing a valid form;
  the fixture was corrected to the frozen lowercase syntax;
- publication SQL-boundary expectation moved from 26 to 28 because atomic
  `personal + Part` now inserts two required curation jobs;
- an edit test selected the older initial-publication curation job; it now
  selects the job bound to the exact edited revision;
- a rich-worker assertion used obsolete extraction/body column names; it now
  verifies the canonical `snapshot_id` and CAS link.

The initial large rich/publication aggregate was 115 passed plus one expected
boundary-count failure, whose focused rerun passed. The worker/job/edit
aggregate was 60 passed plus one stale job-selection assertion, whose focused
rerun passed. No unresolved product test failure remains.

## 8. Schema and source integrity

No schema source changed in this packet. The verified v11 facts remain:

- expected schema SHA-256:
  `1638cc6090a60fe1680b67fe6a6394d264b2cd596156bc4062b88948ec75b90a`;
- v11 migration checksum:
  `d584077e9e774129998b154ddc0b056c2db621ee07ea418185352b1d4b494238`;
- canonical v1-v11 catalog checksum:
  `280d1f03ce6e1127d8771cf489c5df393fbcb73bf73bc59498e9fce818c04f82`;
- current v11 Python-file SHA-256:
  `8282a7ea066db27f267be36a88fdf81fc98b4aed84d4fabf91d160024d27479b`.

Selected final implementation SHA-256 values:

| File | SHA-256 |
|---|---|
| `document_extractors.py` | `ed38db534a7da0c1238dbfe7a745b40f4e9e618f277cfddb1d92e366805bd843` |
| `knowledge_core/repositories/artifacts.py` | `def45620cace4cd6ed03b3807ad9677fc8a9f499afa28f04f33093728cf8e149` |
| `knowledge_core/repositories/publication_commit.py` | `fea0652e0bc0721a32c3257c41b4ec3a7780dc159122b15411b83f0bffd9564f` |
| `knowledge_core/repositories/semantic_graph_contracts.py` | `037c3608668d547cbb2b68190a555e2ed9c09f8eb3d222ac33283d7ea5331aca` |
| `knowledge_core/services/explicit_references.py` | `ddddcdc7292e981949fe436c75c8e4a7dbc0990777de2d9bfce4ac4c2ce15e4e` |
| `knowledge_core/services/rich_publication.py` | `1183ba3f3d59adcce1b2d63c39ec60757bae75c87e1db4ae2ca8858c63cfc925` |
| `knowledge_core/services/publication.py` | `0963ad803aeab4b0c0aa81c1a3964b932ae9289a1285d1a49290816c368417f4` |
| `knowledge_core/services/curation_worker.py` | `8ceade0d13a969b90d9b0dce0e665fd335983f004ccfe9e8afc147e34c538142` |
| `knowledge_core/services/lifecycle.py` | `f639bb832939c7fb4ac1498bb9d4b00457dd3d11e22ae93d79e4892175b7aff3` |
| `knowledge_core/worker.py` | `3fd749d9ebfa0f3210ec7fd09f8c6aef5194bc4cc8fe49b22772a3ccf5f1cc4a` |
| `knowledge_core/production_service.py` | `6f00dcbb96d91c3878021648d921ccbad0f04a06985aad1d1c5eeb0208e569e4` |
| `tools/full_second_brain_preview.py` | `49e8e7c4dc319cd7c9d6bcf5862c64c5e337f83857428feb4c2f49d0124c365e` |

## 9. Contract audit and boundary attestation

The final direct report-schema-v2 audit after the VERIFIED status/backlog
transition passed with zero errors:

```text
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit \
  --docs-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1\docs\knowledge_spaces \
  --output -
exit 0; status pass; error_count 0
input_sha256 5f70d21ebe20317f0325a81e06d7b90f79fb0c6f73ddea2d6a1a970b79a46726
41 requirements; 20 goals; 284 scenarios; 69 paths; 75 operations;
31 stable errors; 12 schemas; 12 valid examples
```

- No live database/root, production service, target port, feature flag,
  company API, provider, or model was accessed or changed.
- No personal, Part, Team, or Responsibility traffic was enabled.
- No completed v1-v11 migration or historical packet evidence was modified.
- No semantic-edge model handler or semantic job was activated.
- SBE-3.2 is the next runnable packet and owns the user-approved forward-only
  v12 temporal assertion/verification/footer work.
