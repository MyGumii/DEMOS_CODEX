# SBE-3.0 evidence

Status: VERIFIED

## 1. Result

- Packet: `SBE-3.0 - Windows stager least-privilege and race safety`.
- State: `VERIFIED`.
- Acceptance: `UPLOAD-001..004`.
- Started/completed: 2026-08-24 19:40/20:29 Asia/Seoul
  (2026-08-24 10:40/11:29 UTC).
- Outcome: Windows CAS directory anchors no longer request unnecessary
  `FILE_DELETE_CHILD`; creation and existing-object reads walk exact retained
  parent handles with no-follow checks; independent process writers share only
  the directory-write access required for child operations while delete sharing
  remains denied; bounded winner handling and retained blob read leases close
  the observed process races. Publication preview and extraction boundaries copy
  and compare exact hash/size/media/path/time evidence before a write transaction.
- Gate conclusion: focused real-process, publication/artifact/projection,
  legacy-inventory, contract, and compilation gates pass. No schema, route,
  feature flag, extractor, UI, live root, service, or port changed.

## 2. Boundary and no-go attestation

- Owned scope: Windows directory/file anchors, CAS staging/verification evidence,
  and real-process race/security tests.
- Explicitly out of scope: publication targets/policy, rich extractors, reference
  parsing, schema, routes/UI, projections as a product feature, and live paths.
- Live `knowledge/`, `user_data/`, `.runtime`, and Notion DB access/change: none.
  Tests used only pytest-isolated roots under the configured temporary root.
- Production DB/migration/flags/services/ports change: none. Canonical catalog
  remains v10 and all successor traffic flags remain disabled.
- Company API/network/model call: none.
- Unrelated user changes: preserved. The workspace has no functional Git
  worktree, so the owned files were verified by explicit inventories and hashes.

## 3. Prerequisites

| Prerequisite | State/hash checked | Result |
|---|---|---|
| `SBE-0.1` registered contract | `evidence/SBE-0.1.md`, SHA-256 `db7d1ac7c6ca70846484053f4195ade208d2490637739d2ff506101e68bdb142`, VERIFIED | PASS |
| SBE execution/acceptance ownership | `EXECUTION_PLAN.md` SBE-3.0; `ACCEPTANCE.md` `UPLOAD-001..004` | PASS |
| Windows host/runtime | Windows 10.0.19045, CPython 3.14.3, SQLite 3.50.4 | PASS |

## 4. Changed files

`STATUS.md`, `IMPLEMENTATION_BACKLOG.md`, and this evidence file are excluded
from the stable implementation hash set to avoid recursive documentation hashes.

| File | Purpose | Final SHA-256 |
|---|---|---|
| `tools/legacy_inventory.py` | Least-privilege directory access mask and optional safe directory write sharing | `67148c6a4b79a3b07b3b5f9df7b5a8ee94b2fd714fa1ad1fc3a5c73b7552e1c3` |
| `knowledge_core/legacy/blob_stager.py` | Handle-relative shard/read traversal, reparse checks, bounded winner retry, concurrent directory anchors | `d5c53264a67f4429b303bc55a858db3ce6393037f295a3faa935fd18974999f1` |
| `knowledge_core/services/publication.py` | Exact frozen staged and existing-source evidence validation outside mutation transactions | `85518b0c77bc7e7d58a49932b3e9acbed5d597abc8688319d9945f424dcbb4dc` |
| `tests/knowledge_spaces/unit/test_sbe_windows_stager.py` | `UPLOAD-001..004` least-privilege/security/real-process proof | `2d8e2ce722852376899ef044d70041a813d8bb6462ad092611558ee37ab58141` |
| `tests/knowledge_spaces/unit/test_publication_preview_service.py` | Preview/loader evidence-drift and zero-effect proof | `f708d46881a1f9942da310354ac38412092750ec6e9a8b7abebe5acc8a275099` |

## 5. Commands and environment

- Working directory: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`.
- Runtime: CPython 3.14.3, SQLite 3.50.4, Windows 10.0.19045 AMD64.
- Effective test roots: pytest-created isolated roots; no live root was passed.
- Feature flags/config: unchanged and not enabled.

Exact primary commands:

```text
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_sbe_windows_stager.py -q -x -p no:cacheprovider
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_publication_preview_service.py -q -x -p no:cacheprovider
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_legacy_content_importer.py tests\knowledge_spaces\unit\test_legacy_inventory.py::test_directory_anchor_denies_reparse_write_handles -q -x -p no:cacheprovider
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_legacy_inventory.py tests\knowledge_spaces\unit\test_legacy_inventory_code.py -q -x -p no:cacheprovider
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_sbe_windows_stager.py tests\knowledge_spaces\unit\test_publication_preview_service.py tests\knowledge_spaces\unit\test_publication_preview_repository.py tests\knowledge_spaces\unit\test_publication_routes.py tests\knowledge_spaces\unit\test_publication_service.py tests\knowledge_spaces\unit\test_artifact_boundary.py tests\knowledge_spaces\unit\test_legacy_artifact_repository.py tests\knowledge_spaces\unit\test_legacy_content_importer.py tests\knowledge_spaces\unit\test_blob_gc_service.py tests\knowledge_spaces\unit\test_projection_materializer.py -q -x -p no:cacheprovider
..\venv\Scripts\python.exe -m pytest tests\knowledge_spaces\unit\test_contract_audit.py -q -x -p no:cacheprovider
..\venv\Scripts\python.exe -m py_compile tools\legacy_inventory.py knowledge_core\legacy\blob_stager.py knowledge_core\services\publication.py tests\knowledge_spaces\unit\test_sbe_windows_stager.py tests\knowledge_spaces\unit\test_publication_preview_service.py
```

## 6. Test results

| Selection | Passed | Failed | Skipped | Duration | Exit |
|---|---:|---:|---:|---:|---:|
| Final SBE-3.0 scenario suite plus retained-handle regression | 8 | 0 | 0 | 10.20 s | 0 |
| Final publication preview/source-loader suite | 39 | 0 | 0 | 41.23 s | 0 |
| Final upload/artifact/publication/blob-GC/projection aggregate | 169 | 0 | 0 | 239.55 s | 0 |
| Full legacy inventory and code-boundary regression | 72 | 0 | 0 | 30.49 s | 0 |
| Contract audit unit gate | 27 | 0 | 0 | 77.50 s | 0 |
| Owned Python compilation | PASS | 0 | 0 | 0.47 s | 0 |

Raw failure/retry fact: the first eight-process same-byte test produced seven
successes and one `[WinError 32]` sharing failure (2 earlier tests passed, 1
failed in 5.34 s). This exposed that retained directory anchors denied another
legitimate writer. The implementation was corrected to allow directory write
sharing while continuing to deny delete sharing and to keep all traversal
relative to retained handles. The exact eight-process test and all aggregate
reruns then passed; the initial failure is not omitted from this evidence.

## 7. Acceptance mapping

| Acceptance ID | Test/evidence | Expected | Observed |
|---|---|---|---|
| `UPLOAD-001` | `test_upload_001_directory_masks_exclude_file_delete_child_and_stage` | Standard create rights without `FILE_DELETE_CHILD` | Read/write/created-shard masks have bit `0x0040` clear; `FILE_ADD_FILE`, `FILE_ADD_SUBDIRECTORY`, and child-handle `DELETE` remain exact; real staging succeeds |
| `UPLOAD-002` | hardlink canary; shared-root identity-swap/reparse/reserved/ADS component test; legacy inventory/source-overlap regressions | Unsafe path/link/root forms have zero source writes and stable errors | Hardlink count and source bytes/mtime remain unchanged; root rename is denied; reparse shard and unsafe names are rejected; inventory/source tests pass |
| `UPLOAD-003` | eight spawned writers; paused real-process different-winner test; bounded synthetic sharing retry | Same bytes converge; different winner reports drift; bounded races do not false-fail | All eight return the same canonical path; wrong late winner returns `LEGACY_BLOB_STAGING_FAILED` with exact drift text and no temp residue; three sharing failures retry then succeed |
| `UPLOAD-004` | retained-read process test; six-field preview drift and six-field extraction-loader drift tests | No-follow evidence is observed outside and exactly bound before DB effect | Retained handle blocks write and unlink; hash/size/media/path/created/verified drift closes leases and commits zero preview/job/audit/outbox effects; valid loader returns exact immutable bytes |

## 8. Negative, security, and fault evidence

- Directory access never includes `FILE_DELETE_CHILD`; file `DELETE` is held only
  on temporary/created handles that require disposition and atomic rename.
- Root/shard traversal uses simple-component validation, `OBJ_CASE_INSENSITIVE`,
  `FILE_OPEN_REPARSE_POINT`, retained parent handles, exact final-path checks,
  directory/reparse attribute checks, and delete-share denial.
- A source-backed hardlink is rejected before any write. Source bytes, mtime, and
  link content remain unchanged; no source path is ever used as a CAS target.
- Delete-pending temporary files remain un-linkable during writes. Atomic rename
  uses the pinned parent; a different late winner is opened no-follow and exact
  bytes are checked rather than overwritten.
- Invalid physical DTOs close their retained lease before any write transaction.
  For preview creation, canonical artifact, preview, job, dedup, audit, and
  outbox counts all remain zero.
- Crash/restart and model/provider fallback are not part of this filesystem-only
  packet; no worker/model/network path was invoked.

## 9. Schema/data/projection evidence

- Starting/ending physical schema: canonical v10, expected schema SHA-256
  `db8c6abd293b30892b7b8b09c1de31ebc4c9036b532bbecb78064df901e570b5`.
- Schema/catalog migration, pristine/sequential upgrade, and rollback: not
  applicable because this packet contains no DDL or migration change.
- Canonical data: only isolated fixtures. Drift failures produce no partial
  artifact/preview/job/dedup/audit/outbox rows.
- Projection product behavior and manifests: unchanged. Existing materializer
  regression is included in the 169-test aggregate only because it consumes the
  shared verifier.

## 10. Performance and bounded-work evidence

- Real contention uses eight spawned Windows processes against one isolated CAS
  root and one digest; all converge without a false failure.
- Sharing/not-found recovery is bounded to 64 attempts with a 2 ms pause
  (approximately 128 ms maximum scheduled pause per bounded open site).
- Reads/hashes use a retained file handle and 1 MiB chunks. No database
  transaction is held during filesystem streaming.
- SQL/model/worker/token counts for staging are zero; publication commits retain
  their existing constant transaction shape.
- Qualification: local Windows/NTFS correctness and race evidence only, not the
  later target-server 40-client performance gate.

## 11. Contract audit

- Input SHA-256: `5a87387e4c731b9a71ae7d5181c77ad62f7c50cec4dd1d0220a7f220e0435cda`.
- Counts: 41 requirements, 20 goals, 284 scenarios, 69 OpenAPI paths, 75
  operations, 31 stable errors, 13 successor flags, 12 schema documents, and
  1,599 resolved schema references.
- Errors: 0. Status: `pass`. Unit gate: 27 passed. Exit: 0.

## 12. Residual ownership

| Residual/risk | Severity | Owner/packet | Why not closed here |
|---|---|---|---|
| Rich PDF/DOCX/PPTX/XLSX extraction and embedded assets | planned | `SBE-3.1` | SBE-3.0 changes only the verified stager |
| Explicit reference ledger and publication curation/visibility transition | planned | `SBE-3.1` | Requires the already frozen v10 ledgers and higher closure authority |
| Target-server 40-client upload/load qualification | external/deferred | `SBE-7.1`, rebaselined P7.8 | Workstation process evidence cannot be labeled target evidence |
| Live-root ACL/service-account verification | external/deferred | `SBE-7.2` handoff | This packet intentionally used no live root or production identity |

## 13. Handoff

- Exact next eligible work: continue active `SBE-2.3`. After it is verified,
  `SBE-3.1` has all three prerequisites (`SBE-1.3`, `SBE-2.3`, `SBE-3.0`).
- Required reading: successor `PRODUCT_CONTRACT.md`, `EXECUTION_PLAN.md`,
  `ACCEPTANCE.md`, then `evidence/SBE-1.3.md`, this evidence, and the eventual
  `SBE-2.3.md`.
- Frozen boundary names: `FilesystemLegacyBlobStager`,
  `StagedVerifiedBlob`, `CanonicalPublicationSourceLoader`, and stable
  `PUBLICATION_SOURCE_STAGING_FAILED`/`LEGACY_BLOB_STAGING_FAILED` errors.
- Hot files: `tools/legacy_inventory.py`,
  `knowledge_core/legacy/blob_stager.py`, and
  `knowledge_core/services/publication.py`.
- First commands: rerun `test_sbe_windows_stager.py`,
  `test_publication_preview_service.py`, and contract audit before altering the
  stager or upload worker.
- External authority still required later: company provider validation,
  target-class service account/ACL, 40-client load, production migration, and
  10111 activation. None is implied by this verification.
