# SBE-7.1 evidence

Status: IMPLEMENTED_NOT_GATED

This record proves the local integrated release-candidate composition and the
checks actually executed. It deliberately leaves unexecuted gates open.

## 1. Locally integrated behavior

- A schema-v12 disposable database was created outside the workspace and seeded
  through the real v11 higher-authority service.
- The app used canonical scope, management, retrieval, publication, edit,
  lifecycle, personal-Wiki, higher-authority, temporal, and semantic-correction
  services. No preview-only hierarchy remapping adapter remains.
- Native Part, Team, and Responsibility items, a sibling Part, an exact Team to
  Part distribution, an ordered explicit reference, local automatic edges, and
  exact-LCA aggregate automatic edges were materialized.
- A deterministic local judge identified itself as
  `local-deterministic-preview/preview-overlap-v1`; the graph exposed model,
  confidence, detector generation, span hashes, and locations.
- Seed replay on the same root is now a tested no-op. Normal write-enabled Team
  and Responsibility roles are revalidated rather than rejected as drift.

## 2. Executed gates

| Gate | Result |
|---|---|
| Focused authority/graph/scope/browser/edit/temporal regression | PASS, 151 tests |
| Migration and contract regression | PASS, 36 tests |
| Full isolated HTTP integration and second-start replay | PASS, 1 test |
| Direct report-schema-v2 contract audit | PASS, zero errors, 70 paths/78 operations |
| Final contract-audit fail-closed suite after status transition | PASS, 27 tests in 80.00s |
| Marker-bound real-process functional probe | PASS: knowledge/graph/index/organization HTTP 200 |
| Local Codex bridge health | PASS: `codex-local`, company API called=false |
| Production flags/live roots/company API | zero changes/calls |

The real-process probe reported 7 local automatic edges, 6 aggregate automatic
edges, one explicit reference, one read-only distribution, three normal API
models, one Codex model, and two canonical users. Its marker SHA-256 is
`75b9976536d23bc353a776a2a7bc2ba8a3cd144318349383ba5e8628fab8a2c6`.
The final direct audit input SHA-256 after the status/backlog transition is
`0beefd78448cdd19767be0e0276fdbf6951ff417c39c135105e7754d755df494`.

## 3. Acceptance boundary

- `SBEOPS-003` and `SBEOPS-013` have local pass evidence: default production
  flags stay false, the preview flag set is marker-bound, and destructive/live/
  company effects are zero.
- Parts of `SBEOPS-005..009` and `SBEOPS-012` are covered by the focused suites,
  isolated HTTP flow, deterministic judge, and Codex bridge health.
- This packet is not VERIFIED because the complete root regression in both
  working-directory forms, the full named cross-process/fault/security matrix,
  1/100/1,000-Wiki measurements, 7-Part x 10-user gold corpus, successor backup/
  restore rehearsal, and real-browser matrix were not all run in this packet.
- Provider precision/recall and target-class latency/load are intentionally not
  inferred from the deterministic local judge.

## 4. Runtime limitation

The in-app Browser control runtime failed before navigation while writing its
local assets. Flask HTTP and Node DOM gates passed, but this is not a real-
browser pass.

## 5. Next owner

The verification owner must execute the remaining `SBEOPS-005..012` matrix on
approved isolated infrastructure. SBE-7.2 may expose the locally verified
preview, but production readiness remains prohibited.
