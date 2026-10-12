# SBE-8.12 answer evidence and original-source download evidence

Date: 2026-09-01 (Asia/Seoul)

State: `IMPLEMENTED_NOT_GATED`

## Scope

This increment adds generic chapter-local Wiki citations, a mandatory final
`전체 요약`, and authorized original-source downloads in both the item popup
and Agent Markdown answers. It adds no phrase-, account-, organization-, or
topic-specific retrieval rule and does not change ranking or graph scoring.

## Authority and integrity

- Source provenance follows `item_source -> source_artifact -> raw
  artifact_blob -> artifact_blob_body -> verified blob_object`.
- The route accepts a selected view and resolves the item only through the
  current server-owned corpus closure or an exact distribution overlay.
- Overlay provenance is revision-bounded; native/inherited provenance is
  bounded to sources that existed at or before the answer revision.
- CAS bytes are read outside the transaction and verified by SHA-256 and size.
  Authority and immutable metadata are resolved again before response.
- Download responses are attachments with UTF-8 filenames, `private,
  no-store`, `nosniff`, and the verified digest header.

## Local automated evidence

Focused suites:

```text
75 passed in 75.34s
```

The set covers Agent execution/context, conversation behavior, canonical route
registration, browser transport/DOM wiring, response shape, attachment headers,
and the generic prompt contract.

## Isolated 10221 evidence

Retained root:

```text
D:\CodexTestTemp\SBE9-V13-20260827-v6
```

Observed with `sbv-p03-u02`:

- personal: 2 items; original PPTX list/download returned 200;
- Part: 10 items; original DOCX/PDF/PPTX provenance returned 200;
- Team: 40 items; inherited original download metadata returned 200;
- Responsibility: 95 items; inherited original download metadata returned 200;
- downloaded `02-related.pptx`: declared 2,152 bytes, received 2,152 bytes,
  response SHA-256 matched the received bytes;
- a foreign personal item owned by `sbv-p01-u01` returned concealed 404 when
  requested through `sbv-p03-u02`'s personal view.

One fresh Codex Bridge Part turn completed with ten supplied Wiki bodies. The
answer produced three evidence-bearing chapters, exact authorized PDF/PPTX/DOCX
links after each chapter, horizontal rules, and a final `전체 요약`.

Real Chrome verification on 10221 confirmed:

- the Wiki item modal displays `원본 다운로드 · 01-related.docx`;
- clicking the modal link emits a browser download;
- a fresh Agent answer rendered five headings including final `전체 요약`, four
  horizontal rules, and two `assistant-source-download` links;
- both chat links had exact selected-view/item/artifact URLs and the download
  attribute; clicking a chat evidence link emitted a browser download.

The page also logged a pre-existing optional curation-shadow 404 and Grammarly
extension stylesheet errors. Neither originated from this increment or blocked
the item/source/chat paths; they remain separate diagnostics.

## Remaining external gates

- Hynix/OpenAI-compatible provider formatting remains to be exercised inside
  the company environment. The provider receives the same generic policy and
  exact `source_documents` payload used by Codex Bridge.
- Production load/restore/canary acceptance remains owned by the existing
  operations handoff.

## 2026-09-06 approved original-source API contract correction

The user explicitly approved temporary ownership of the exact thirteen files
in the source-artifact contract repair proposal. The pre-edit authority and
hashes are preserved in the repository-relative checkpoint
`code_assist_v1/docs/notion_parity/checkpoints/ui-internal-collaboration-sbe-source-artifact-contract-approved-pre-edit-20260906.json`.
The proposal and all earlier evidence remain unchanged. This appendix records
a contract-registration correction, not new runtime behavior or a gate promotion.

The machine contract now registers exactly these existing GET operations:

- `listItemSourceArtifacts`: item source-artifact list with five required
  envelope fields and exactly eight required fields per artifact; canonical
  same-origin download URLs, non-null source revision, and no pagination.
- `downloadItemSourceArtifact`: HTTP 200 original bytes with dynamic media
  type, attachment disposition, no-store/no-cache, nosniff, verified SHA-256,
  request identity, and content length. Errors retain the common JSON envelope.

Both routes reject query parameters. The binary-response audit exception is
limited to the exact download GET; JSON request-ID checks remain enforced.
New rejection tests cover missing registration, incorrect operation IDs,
unregistered additions, response/header weakening, non-200 success declarations,
malformed UUIDs in each URL segment, and list/schema drift.

Sequential isolated validation using `venv/Scripts/python.exe -E`:

| Validation | Result | Repository-relative immutable output |
| --- | --- | --- |
| `tests/knowledge_spaces/unit/test_source_artifact_routes.py` | 26 passed in 6.63s; no failures or skips | `code_assist_v1/.runtime/sbe-source-artifact-routes-contract-20260906-r2-junit.xml` |
| `tests/knowledge_spaces/unit/test_contract_audit.py` | 51 passed in 147.70s; no failures or skips | `code_assist_v1/.runtime/sbe-source-artifact-contract-audit-20260906-r2-junit.xml` |
| Fresh read-only contract audit before this evidence appendix | exit 0, pass, zero errors | `code_assist_v1/.runtime/sbe-source-artifact-contract-pre-evidence-20260906.json` |

The audit registers 72 paths, 80 operations, and 27 GET operations:
frozen baseline 52/56 + historical successor 18/22 + SBE-8.12 2/2.
It preserves 41 requirements, 20 goals, 284 scenarios, and 31 stable errors.
The frozen baseline operation-ID SHA-256 remains
`75b7270c72c7275879ab43824c0988717390502b319f3c970aecceb944b200c1`.
The pre-appendix audit input SHA-256 is
`26aee3cbeb6e838fd627e7f5d24441c476e96e92a45b1a6212020e9ad4dc8626`.

Because active authority documents are audit inputs, the final post-appendix
audit is recorded separately at
`code_assist_v1/.runtime/sbe-source-artifact-contract-final-20260906.json`.
Its exact hash, final thirteen-file hashes, approval lineage, and ownership
return belong to the external Notion completion checkpoint, avoiding a
self-referential hash in this source document.

No runtime source, migration, feature flag, AGENTS.md, live database, account
permission, server, or external provider was changed. The two independent
review findings were corrected inside the same thirteen-file scope.
State remains `IMPLEMENTED_NOT_GATED`; company-provider, target load/restore,
canary, Notion integrated release, and full-product completion remain open.
