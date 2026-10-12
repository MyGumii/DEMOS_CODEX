# SBE-7.2 evidence

Status: STAGED_NOT_ACTIVATED

## 1. Staged release candidate

- Isolated root: `D:\CodexTestTemp\SBE7-Final-20260825-v2`
- Marker schema: `code-assist.full-second-brain-preview`, version 2
- Marker SHA-256:
  `75b9976536d23bc353a776a2a7bc2ba8a3cd144318349383ba5e8628fab8a2c6`
- Current inspection URL: `http://127.0.0.1:10121`
- Current process: hidden local preview; production flags and company calls are
  disabled/zero.

The same root passed functional and Codex bridge probes, then successfully
started a second time. `/healthz` reports `status=ready`, `schema_version=2`,
`company_api_called=false`, and the same marker hash.

## 2. Why 10111 is not activated

Port 10111 is owned by pre-existing PID 29464, command module
`code_assist_v1.notion_collaboration.approved_dev_pair`, approval ID
`AP-DEV-PAIR`, created at 2026-08-25 03:04 Asia/Seoul. It is not this preview.
It was not stopped, killed, or modified because replacing an independently
approved process requires explicit operator authority.

## 3. Acceptance status

| ID | State |
|---|---|
| `SBEOPS-014` | isolated roots and accidental-port protection pass; exact 10111 bind waits for the existing listener decision |
| `SBEOPS-015` | marker-bound health and postconditions pass; a separately recorded staged flag/rollback drill remains open |
| `SBEOPS-016` | open: the required meaningful 7-Part x 10-user gold corpus was not replaced by the smaller smoke fixture |
| `SBEOPS-017` | complete flow is inspectable on 10121; exact 10111 and real Browser proof remain open |
| `SBEOPS-018` | external/local boundary and commands are recorded in `SECOND_BRAIN_EXTERNAL_HANDOFF.md` |

## 4. No-go attestation

- No 10011/live mutable root was read as a write target.
- No company endpoint or credential was used.
- No target service, service account, firewall rule, or production flag changed.
- This evidence does not claim production readiness.
- The first workspace-local probe attempt left a stopped copied root at
  `G:\AI_Agent\DEMOS_CODEX\.tmp\second-brain-full-preview-10111-sbe7-v2`.
  Its marker was verified as this tool's schema-v2 copy, but recursive cleanup
  was denied by execution policy. It is not a listener and is not the release
  candidate root.
