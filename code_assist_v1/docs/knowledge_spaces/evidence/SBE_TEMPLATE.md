# <packet_id> evidence

Status: template only. Copy to `<packet_id>.md`—for example,
`SBE-0.1.md`; do not add another `SBE-` prefix and do not overwrite completed
P-phase evidence.

## 1. Result

- Packet:
- State:
- Outcome actually achieved:
- Gate conclusion:
- Started/completed (UTC and Asia/Seoul):

## 2. Boundary and no-go attestation

- Owned scope:
- Explicitly out of scope:
- Live `knowledge/`, `user_data/`, `.runtime`, Notion DB access/change:
- Production DB/migration/flags/services/ports change:
- Company API/network/model call:
- Unrelated user changes preserved:

## 3. Prerequisites

| Prerequisite packet/evidence | State/hash checked | Result |
|---|---|---|
| | | |

## 4. Changed files

| File | Purpose | Final SHA-256 |
|---|---|---|
| | | |

`STATUS.md` and this evidence file are excluded from the stable implementation
hash set to avoid a recursive hash.

## 5. Commands and environment

- Working directory:
- Python/SQLite/Node/runtime source ID:
- TEMP/TMP/TMPDIR:
- Feature flags/config:
- Exact commands (verbatim):

```text

```

## 6. Test results

| Selection | Passed | Failed | Skipped | Duration | Exit |
|---|---:|---:|---:|---:|---:|
| | | | | | |

Include raw failure/retry facts; do not report only the final green run when a
preceding failure exposed a product defect.

## 7. Acceptance mapping

| Acceptance ID | Test/evidence | Expected | Observed |
|---|---|---|---|
| | | | |

## 8. Negative, security, and fault evidence

- Authorization/concealment canaries:
- Stale CAS/role/closure/revision cases:
- Crash/restart/replay/failpoint cases:
- Source-write/live-root diff:
- Partial DB/file/audit/outbox effects:
- Provider/model malformed/timeout fallback:

## 9. Schema/data/projection evidence

- Starting physical schema/catalog hash:
- Ending physical schema/catalog hash:
- Pristine vs sequential upgrade:
- Failed-upgrade rollback:
- FK/integrity/postconditions:
- Before/after canonical row/body hashes:
- Projection manifest/generation hashes:

Use `not applicable` with a reason for a non-schema/non-projection packet; do
not omit the section.

## 10. Performance and bounded-work evidence

- Corpus/Wiki/user/Part sizes:
- SQL/query/model/worker counts:
- p50/p95/p99 and busy/queue/projection lag:
- Memory/token/input caps:
- Target-class or workstation-only qualification:

## 11. Contract audit

- Input hash:
- Requirement/goal/scenario/OpenAPI/schema counts:
- Errors:
- Exit:

## 12. Residual ownership

| Residual/risk | Severity | Owner/packet | Why not closed here |
|---|---|---|---|
| | | | |

## 13. Handoff

- Exact next eligible packet:
- Required reading/evidence:
- Frozen DTO/schema/API names:
- Hot files that must not be edited concurrently:
- Commands the next Agent should run first:
- External authority/state still required:
