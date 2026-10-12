# SBE-0.1 evidence

Status: VERIFIED

## 1. Result

- Packet: `SBE-0.1 — contract transition and exact baseline`
- State: `VERIFIED`
- Outcome actually achieved: report-schema-v2 registered the approved successor
  machine contract as the exact baseline-plus-delta union: 41 requirements, 20
  goals, 284 scenarios, 69 OpenAPI paths, 75 operations, 31 stable errors, 12
  JSON Schema documents/examples, 13 disabled successor flags, and ten explicit
  supersessions.
- Gate conclusion: pass. Contract audit errors 0; focused tests pass from both
  supported working directories; frozen P evidence and v1-v7 migration hashes
  are unchanged.
- Started/completed: 2026-08-23 03:18-03:50 UTC / 12:18-12:50 Asia/Seoul.

## 2. Boundary and no-go attestation

- Owned scope: normative SBE/current contracts, OpenAPI/JSON Schema/examples,
  contract manifest, report-v2 audit tool/tests, traceability/backlog/status.
- Explicitly out of scope: runtime application behavior, v8-v11 migration
  implementations, runtime feature parsing, UI/server activation, model calls.
- Live `knowledge/`, `user_data/`, `.runtime`, Notion DB access/change: none.
- Production DB/migration/flags/services/ports change: none. No v1-v7 migration
  file changed; all 20 checked Knowledge Core environment flags were absent;
  neither 10011 nor 10111 was listening at the final read-only observation.
- Company API/network/model call: none. The audit is standard-library-only and
  imports no application/config/provider module.
- Unrelated user changes preserved: yes. Existing P evidence and migration
  sources were read/hash-checked only.

## 3. Prerequisites

| Prerequisite packet/evidence | State/hash checked | Result |
|---|---|---|
| User-approved SBE package | Five pinned sources under `second_brain_evolution/` | PASS |
| Canonical-v7 machine baseline | R/G/scenario/operation ID hashes pinned in manifest | PASS |
| P0-P7 evidence | 60 files, 407,773 bytes, inventory SHA-256 `f249eef78b587ee3453e27afab71de819963d5574c1852241dfeb2cbe3f1d084` before/after | UNCHANGED |
| v1-v7 migrations | 13 files, 311,799 bytes, inventory SHA-256 `f14a350f746493847fcaa40e9e458c516011e0ec8741f4c55d4ab95de3c8ce3e` before/after | UNCHANGED |

The inventory digest is SHA-256 over sorted UTF-8 rows
`relative_path NUL size NUL file_sha256 LF`.

## 4. Changed files

`STATUS.md` and this evidence file are intentionally excluded from the stable
implementation hash set to avoid a recursive hash. The 26-file stable set is
853,096 bytes with inventory SHA-256
`5f2a5e3be08634385f5b0e4aac1b8ef7abbedbd3f1c09e08470efaf0c3dd5ba6`.

| File | Purpose | Final SHA-256 |
|---|---|---|
| `AGENTS.md` | Single-root successor routing/count instruction | `916383ca3674842d7dd2f9ef77af989b3b7dfe38e4c3f745072c6f9655d47549` |
| `tools/contract_audit.py` | Report schema v2, transition/authority/DAG/delta audits | `0c2949936c483fd7bd0d658933111b5f5c265c3d210ea028dc109b912b2e20dd` |
| `tests/knowledge_spaces/unit/test_contract_audit.py` | Positive and fail-closed v2 audit coverage | `0bb75ae47700449572116af5b5cd6e40ff83cad6c5ed3e3abd6f42a20c560312` |
| `docs/knowledge_spaces/README.md` | Registered successor entry point/counts | `090d886640ea2dc38e8d015a55530c17f4eaf9dd99c1ddf2ae57e5da358320ea` |
| `docs/knowledge_spaces/DECISIONS.md` | Registered ADR/supersession authority | `f764bec2b35730fc0b012415400059e61787d38f43b78914d46af9bca94ca7db` |
| `docs/knowledge_spaces/ARCHITECTURE.md` | Flags-off v7 vs forward-target boundary | `bb8552653b0f02128302d7bf1cb7d75f65000215f01e138398be8b81c62520aa` |
| `docs/knowledge_spaces/SCHEMA_V1.md` | Immutable v1-v7/forward logical boundary | `15de58b4ba6f7dd4c9f77087bed795ba3ab07cb9fbdada36a92e6d7164c91e21` |
| `docs/knowledge_spaces/API_UI_CONTRACT.md` | v2 operation/error/permission/UI contract | `731d920ccf1e72926ca3d5cfbb34e0ff2720bb57a73353ef89c50c32528d9411` |
| `docs/knowledge_spaces/MIGRATION_ROLLOUT.md` | Exact disabled flag DAG | `0008ded180852070f2d9d3a555052695b704651dd72a98c54979c10311354f0f` |
| `docs/knowledge_spaces/TEST_OPERATIONS.md` | 124+160 active scenario composition | `fa429c36bc5034818fbcd18a79fa66e36a8af863837afb263b4d1258a20fbc5b` |
| `docs/knowledge_spaces/IMPLEMENTATION_BACKLOG.md` | Exact SBE ownership/acceptance assignment | `beea7c61b7689d06a2dbea3e9c173057a42c17f575a83104b060fb9662651230` |
| `docs/knowledge_spaces/TRACEABILITY.md` | R-001..041 and G-01..20 trace union | `995fb1ecbe83a1e320e6ae2858a69b51c66fa53bbc6c0a9f72414d704ab73735` |
| `docs/knowledge_spaces/GOAL_CONTRACT.json` | Exact 20 quantitative goal families | `46ce12ad70e424dc1c381d496dfb71cc9f7955606fe9945a72bff1ccf2d2d71b` |
| `docs/knowledge_spaces/contracts/contract-manifest.json` | Closed registered transition ledger | `cfdca71c986db13dbd7c6e9ad58f678c302191fed83b65f9ef590d9e568eaad3` |
| `docs/knowledge_spaces/contracts/openapi.json` | Exact 69-path/75-operation surface | `7dae206ecdb98bfe9940aa55c77fb0a16fe453bc70b9074741a497ff94946894` |
| `docs/knowledge_spaces/contracts/examples.json` | 12 schema-role examples | `c8b65f70d4caf2b888d924ae0884348fd71061b582c1ae9532e0aab4b59ec1fd` |
| `docs/knowledge_spaces/contracts/successor-feature-flags.json` | Machine-readable 13-flag DAG | `86b7e37fcd7cc83938c9df4eebd20e0923eddef16cd528d843ba9eaae88a5e55` |
| `docs/knowledge_spaces/contracts/schemas/common.schema.json` | 31-code stable error enum | `086f4b8674d249e41f78286d705761ed1823813ab7d89cd90547e35fc00bc35e` |
| `docs/knowledge_spaces/contracts/schemas/goal-contract.schema.json` | Exact G-01..20 schema | `cb0535bad689433a4557785338c86ee942c5cdbb1979a95705dce07802d56976` |
| `docs/knowledge_spaces/contracts/schemas/second-brain.schema.json` | Registered successor DTO shapes | `9138f63265cf2c934f64a8be846075461604eaf4654dde5bbd3976dea308ddde` |
| `docs/knowledge_spaces/contracts/schemas/successor-feature-flags.schema.json` | Closed flag-contract schema | `d6d2d021b7e5a923bf64e2e3317c6ed995e3a3c74fde473c625b8151704114ce` |
| `docs/knowledge_spaces/second_brain_evolution/README.md` | Registered successor package state | `56fd256f6aed116eed9e7c3d1b4d03f8cd83146ea376874be32c25740c7ed34b` |
| `docs/knowledge_spaces/second_brain_evolution/PRODUCT_CONTRACT.md` | Machine-registered product authority state | `55fdc53dbf591d9974cf92d4042b6b4849d2e5a0c155bad4960f0a5acb33218c` |
| `docs/knowledge_spaces/second_brain_evolution/FORWARD_SCHEMA.md` | Frozen logical v8-v11 state | `861a95d471d9bef0e2f386571cc4f723cc33af626c2f85812c990a96bf928604` |
| `docs/knowledge_spaces/second_brain_evolution/EXECUTION_PLAN.md` | SBE-0.1 transition and next eligibility | `5883c6955430e04b4149cd69c0b96839fc4a5c7281748b001c4214197e46d15e` |
| `docs/knowledge_spaces/second_brain_evolution/ACCEPTANCE.md` | Active 160-ID successor matrix state | `bfd769be8757740f80e534fda6a9d50e7ae0652830e82b7b4e8472f809cb36bb` |

## 5. Commands and environment

- Working directories: `G:\AI_Agent\DEMOS_CODEX\code_assist_v1` and
  `G:\AI_Agent\DEMOS_CODEX`.
- Python 3.14.3; SQLite 3.50.4; Node v24.14.1. This packet does not satisfy the
  target production SQLite/runtime gate.
- Feature flags/config: none of the 20 inspected existing/successor
  `KNOWLEDGE_CORE_*` variables was set in the executing environment.
- Exact primary commands:

```text
python -m py_compile tools/contract_audit.py
python -m pytest tests/knowledge_spaces/unit/test_contract_audit.py -q
python -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_contract_audit.py -q
python tools/contract_audit.py audit --docs-root <absolute docs/knowledge_spaces> --output -
```

Read-only SHA-256 inventory scripts enumerated only `P*.md`, `v00[1-7]*`,
runtime `*.py`, Knowledge Spaces test `*.py`, and the explicit changed-file set.

## 6. Test results

| Selection | Passed | Failed | Skipped | Duration | Exit |
|---|---:|---:|---:|---:|---:|
| Focused audit tests, first full run | 25 | 2 | 0 | 79.11 s | 1 |
| Focused audit tests, corrected expectations | 27 | 0 | 0 | 77.72 s | 0 |
| Same tests from repository parent | 27 | 0 | 0 | 77.31 s | 0 |
| Final actual-contract + authority-drift smoke | 2 | 0 | 0 | 7.18 s | 0 |
| Deterministic contract audit | 1 audit | 0 errors | n/a | 2.3 s | 0 |

The first failure exposed no product/auditor defect: one test expected a family
gap after replacing the terminal ID with a duplicate, although the remaining
unique sequence was still contiguous; another removed only the first of several
`VERIFIED` markers. Fixtures were corrected to create a real duplicate+gap and
remove every marker, then both working-directory runs passed.

## 7. Acceptance mapping

| Acceptance ID | Test/evidence | Expected | Observed |
|---|---|---|---|
| `SBEOPS-001` | Report-v2 audit plus positive/negative unit tests | Exact source/authority/union/supersession/DAG/OpenAPI/error/schema closure | PASS, errors 0 |
| `SBEOPS-002` | Before/after P-evidence and v1-v7 migration inventories | No changed historical file/hash | PASS, both aggregate digests unchanged |
| `SBEOPS-004` | Explicit source/test/catalog inventories and no-go attestation | Baseline captured; no live/runtime/provider mutation | PASS |

## 8. Negative, security, and fault evidence

- Authorization/concealment canaries: runtime not applicable; manifest path
  traversal and repository/docs authority-root separation fail closed.
- Stale transition cases: registered sets with `state=proposed` fail; partial
  R/G/scenario/API unions fail.
- ID/DAG drift: duplicate/gapped successor scenario families, changed
  successor `operationId`, altered flag prerequisite, stale authority marker,
  missing/escaped source, and error/schema drift all fail the audit.
- Crash/restart/replay/failpoint cases: runtime not applicable to this contract-
  only packet; CLI output atomic/non-overwriting tests pass.
- Source-write/live-root diff: no live root was opened or written.
- Partial DB/file/audit/outbox effects: not applicable; no DB transaction or
  runtime service was used.
- Provider/model malformed/timeout fallback: not applicable; provider calls 0.

## 9. Schema/data/projection evidence

- Starting and ending physical schema/catalog: unchanged canonical v7; the 13
  v1-v7 migration files retain inventory SHA-256
  `f14a350f746493847fcaa40e9e458c516011e0ec8741f4c55d4ab95de3c8ce3e`.
- Pristine vs sequential upgrade / failed-upgrade rollback / FK integrity:
  not applicable because SBE-0.1 creates no migration or DB.
- Before/after canonical row/body hashes: not applicable; canonical DB unopened.
- Projection manifests/generations: unchanged; projection services unopened.

## 10. Performance and bounded-work evidence

- Runtime corpus/Wiki/user/Part sizes and latency: not applicable at contract
  registration; SBE-7.1/SBE-7.2 own execution.
- Machine contracts freeze cursor/page/query/model/worker bounds, final ten
  reranked documents, four mandatory pin/reference contexts, and 32 KiB combined
  context; this packet does not claim their runtime performance.
- Captured current runtime-source snapshot: 132 files, 4,108,111 bytes,
  inventory SHA-256 `0b7a2723fdf354e67771320e4ba814e37f1f82b46219615f1497bfecb258fcfa`.
- Captured Knowledge Spaces test snapshot: 123 files, 2,829,665 bytes,
  inventory SHA-256 `4dcd732ee5e97763adcde0db9c264d6e1b845975e82052bc9e0871351bef0f03`.

## 11. Contract audit

- Input SHA-256:
  `2cf68b6f2dd01acbc27fec533aab4a13aa205729a120796c95db4a6a7448b797`.
- Source files: 33, including the five successor sources and dedicated
  `repository:AGENTS.md` allowlist source.
- Requirements/goals/scenarios: 41/20/284; all traceability/backlog coverage
  exact, duplicates 0.
- OpenAPI: 69 paths, 75 operations, 31 stable errors; baseline operation hash
  `75b7270c72c7275879ab43824c0988717390502b319f3c970aecceb944b200c1`.
- Schemas/examples: 12/12; 1,494/1,494 local references resolved.
- Errors: 0. Exit: 0.

## 12. Residual ownership

| Residual/risk | Severity | Owner/packet | Why not closed here |
|---|---|---|---|
| v8-v11 physical migrations and repositories do not exist | Required next work | SBE-1.1..SBE-3.2 | Explicit SBE-0.1 no-go boundary |
| Successor services/routes/UI/flags are registered but not runtime-exposed | Required next work | SBE-2.x..SBE-6.x | Registration is not implementation |
| Local integrated/10111 preview not gated | Required later | SBE-7.1/SBE-7.2 | Depends on all implementation packets |
| Company model and target 40-client/service/backup/pilot evidence absent | External gate | Post-SBE handoff/P7.8/P7.9/P8 | Requires company/target authority and state |

## 13. Handoff

- Exact next eligible packets: SBE-1.1 (v8 multi-Wiki schema) and independent
  SBE-3.0 (Windows stager hotfix), each using its own ownership boundary.
- Required reading: `second_brain_evolution/README.md`, `PRODUCT_CONTRACT.md`,
  `FORWARD_SCHEMA.md`, `EXECUTION_PLAN.md`, `ACCEPTANCE.md`, then this evidence.
- Frozen names: v8-v11 filenames/tables/DTOs, 17 v2 paths/19 operation IDs, 14
  successor error codes, 13 flag names/DAG, `CorpusClosure`, `automatic-edge-v1`,
  `item-ref-v1`, relation/scoring/temporal/footer versions.
- Hot files: manifest, OpenAPI, common/goal/successor schemas, traceability,
  backlog, and STATUS must remain integrator-owned during packet transitions.
- First commands: rerun report-v2 audit and focused contract tests, then inspect
  the exact packet-owned migration/catalog/test surface before editing.
- External authority/state still required: company API/model compatibility and
  quality/latency, target Windows 40-client/service-account/backup/restore/pilot,
  production flag enablement, and live migration/cutover approval.
