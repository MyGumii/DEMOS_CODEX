# V0B-P1 integration dependency: source-artifact contract repair proposal

Recorded: 2026-09-06, Asia/Seoul. State: **proposal only; ownership transfer not approved**.

## Observed blocker

The current read-only SBE contract audit has one error,
`OPENAPI_HUMAN_OPERATION_MISMATCH`. The human contract and implemented routes
include these two operations, but the machine-readable OpenAPI contract does not:

- `GET /api/knowledge/v1/spaces/{space_id}/items/{item_id}/source-artifacts`
- `GET /api/knowledge/v1/spaces/{space_id}/items/{item_id}/source-artifacts/{artifact_id}/download`

The implementation is in `knowledge_core/canonical_routes.py`, methods
`list_item_source_artifacts` and `download_item_source_artifact`. This is a
contract-registration correction, not a request to change runtime behavior.
Deleting the human declarations or suppressing the audit is not a valid repair.

Current machine totals: 70 paths, 78 operations, 25 GET operations.
Proposed totals after the exact additive correction: **72 paths, 80 operations,
27 GET operations**. Preserve 41 requirements, 20 goals, 284 scenarios and
31 stable errors. Preserve the frozen baseline 52 paths/56 operations and its
operation-ID hash. Preserve the historical SBE-0.1/SBE-6.2 18-path/22-operation
registration; explicitly record the SBE-8.12 addition of two paths/two operations.

Audit input SHA-256:
`32c2ff0b4274cfcbfb09bf9a93de5c4adf2b2f0c7c115e04a96a606f643f963d`.

## Exact proposed ownership and edit scope

All paths below are relative to `G:/AI_Agent/DEMOS_CODEX/code_assist_v1/`.
Only these **thirteen files** would transfer temporarily to `/root` for this repair.
No file in this table has been modified by this proposal.

| File | Proposed change |
| --- | --- |
| `docs/knowledge_spaces/contracts/openapi.json` | Register the two GET operations, UUID parameters, list response, binary download, response headers and existing common errors. Proposed operation IDs: `listItemSourceArtifacts`, `downloadItemSourceArtifact`. |
| `docs/knowledge_spaces/contracts/contract-manifest.json` | Add the exact two operations to the successor additions and update current expected/registered totals to 72/80 without changing baseline counts or hashes. |
| `docs/knowledge_spaces/contracts/schemas/knowledge.schema.json` | Add source-artifact list/row schemas matching the actual route response. |
| `docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md` | Preserve the historical 18/22 registration and document the SBE-8.12 +2/+2 addition and current 72/80 totals. |
| `docs/knowledge_spaces/API_UI_CONTRACT.md` | Correct the current 70/78 and stale current 56-operation narrative. Preserve the two existing routes and the historical/version-specific v2 18/22 table. |
| `tools/contract_audit.py` | Extend the exact successor operation registry, current totals and active narrative checks; retain all baseline and unregistered-addition rejection checks. |
| `tests/knowledge_spaces/unit/test_contract_audit.py` | Test 72/80 totals, frozen baseline, exact +2/+2 delta and failures for missing paths, IDs or registry entries and unregistered additions. |
| `tests/knowledge_spaces/unit/test_source_artifact_routes.py` | Validate actual list responses against the added schema and retain binary download/header/authorization tests. |
| `docs/knowledge_spaces/evidence/SBE-8.12.md` | Append new contract-repair evidence; preserve existing evidence and `IMPLEMENTED_NOT_GATED` state. |
| `docs/knowledge_spaces/README.md` | Update the active contract-audit totals to 72/80 with the additive amendment identified. |
| `docs/knowledge_spaces/STATUS.md` | Append the current correction/audit result and distinguish it from preserved historical 70/78 results. No unrelated status promotion. |
| `docs/knowledge_spaces/second_brain_evolution/EXECUTION_PLAN.md` | Update the active registered totals from 41/20/284/70/78 to 41/20/284/72/80, preserving all gate requirements. |
| `docs/knowledge_spaces/second_brain_evolution/ACCEPTANCE.md` | Extend the active SBEOPS-001 audit equation with the SBE-8.12 +2/+2 amendment while retaining baseline 52/56 and earlier 18/22. |

The final cross-document review includes both the nine machine/API/evidence
repair files and four active planning/status documents. Historical evidence
files for SBE-6.2, SBE-7.1, SBE-8.6 and SBE-8.7 retain their original 70/78
results. No new manifest shape or schema role is needed. `examples.json` need
not change if focused tests validate complete examples directly.

## Response details to preserve

The list response has `request_id`, `corpus_closure_sha256`, `item_id`,
`selected_view_space_id`, and `items`. Each item has exactly **eight fields**:
`artifact_id`, `created_at`, `download_url`, `media_type`, `origin_type`,
`original_filename`, `size_bytes`, and `source_revision_id`.
The list response does not return `content_sha256`.

The download response contains original bytes with the original media type.
Preserve `Content-Disposition`, `Cache-Control: private, no-store, max-age=0`,
`Pragma: no-cache`, `X-Content-Type-Options: nosniff`,
`X-Original-Content-SHA256`, `X-Request-ID`, and `Content-Length`.
Session identity, authorized selected-view closure, concealed denial, and
artifact-integrity checks remain server-owned and unchanged.

## Validation and stopping rules

After explicit transfer, recheck the thirteen file hashes and current ownership
before editing. If another owner has changed overlapping files, reconcile that
specific overlap before mutation; do not overwrite it.

Run the two focused test files sequentially using isolated temporary fixtures,
then a fresh read-only contract audit against the absolute knowledge-docs path.
Require zero audit errors, exact 72/80 active totals, and preserved baseline
52/56 and baseline hash. Do not weaken tests to accept drift.

No runtime routes/services, feature flags, production data, account permissions,
AGENTS.md, SCHEMA_V1.md, completed migrations or GOAL_CONTRACT.json are in scope.
If a correct repair requires another file, stop that expansion and report it.
No browser or live external-account operation is required for this correction.

This repair alone does not close SBE-8.12, V0B status integration, Q0, release,
production, external-provider/load/restore gates, or the full product goal.
After SBE audit recovery, separately reconcile the three current-checkpoint hash
bindings and twelve Notion verifier reports with authoritative regression
evidence. Then create the V0R/V0B generic wrapper and integrate status under an
exact predeclared Notion ownership checkpoint.

## Approval request

Suggested narrowly scoped user approval:

`SBE 원본파일 API 계약 교정 13개 파일 소유권 임시 이관 및 수정 승인`

Until that approval or an equivalent explicit transfer arrives, the thirteen files
remain read-only. This document is not an approval and does not grant ownership.
