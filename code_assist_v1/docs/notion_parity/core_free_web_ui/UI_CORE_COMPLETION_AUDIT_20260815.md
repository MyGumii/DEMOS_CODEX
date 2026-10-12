# Core Free Web UI requirement-level completion audit — 2026-08-15

Status: local evidence is complete enough to enumerate truthfully; official
reference characterization and differential parity remain incomplete.

Profile: Free workspace owner, Chrome desktop web, `ko-KR`, synthetic content
only.

## Audited result

The authoritative 25-scenario denominator currently closes as follows:

| Evidence state | Count |
|---|---:|
| Required Core scenarios | 25 |
| Local real-Chrome functional green | 24 |
| Local real-Chrome measurement green | 1 |
| Reference characterized | 0 |
| Differential passed | 0 |
| Promoted | 0 |

`UI-PERF-001` is the single measurement-only scenario. It has repeatable local
timing distributions but no approved product budget. The other 24 scenarios
have isolated local functional browser evidence. Neither category is an
official Notion parity claim.

## Machine boundary

`completion_matrix.v1.json` binds:

- the exact scenario-plan bytes and canonical digest;
- the latest H9 isolated pilot/recovery checkpoint;
- the ordered 25-scenario denominator and phase mapping;
- one current local evidence document and SHA-256 per scenario;
- all open reference, differential, H8, H9, and rollout gates; and
- explicit false completion claims.

`tools/verify_notion_core_free_ui_completion.py` independently recomputes the
counts and validates every bound path/hash. It rejects duplicate/non-finite
JSON, unknown fields, denominator/phase/manifest drift, unsafe paths, missing
or changed evidence, forged summaries, invented reference/differential
promotion, and false completion claims. Its successful terminal state is
`incomplete`, not `complete`.

## Verification

Run from `G:\AI_Agent\DEMOS_CODEX` with the project interpreter:

```powershell
venv\Scripts\python.exe -m pytest `
  code_assist_v1\tests\test_notion_ui_completion_matrix.py `
  code_assist_v1\tests\test_verify_notion_core_free_ui_plan.py -q
```

Observed result: `18 passed in 6.01s`.

```powershell
venv\Scripts\python.exe -m code_assist_v1.tools.verify_notion_core_free_ui_completion --compact
```

Observed result: `status=pass`, `completion_state=incomplete`, required 25,
local browser 24, local measurement 1, reference 0, differential 0, promotion
0.

The pre-existing complete local Chrome suite last passed with 22 tests after
the performance journey was added. The H9 pilot/recovery bundle separately
passed 8 tests. This audit does not replace those browser artifacts; it checks
their checked-in human-readable evidence hashes.

## Open gates

The product cannot be called complete until all of the following are proven:

1. a safe signed-in browser transport opens only `REF-FREE-A/B`;
2. all 25 reference scenarios are observed with redacted evidence;
3. every required scenario has a reviewed differential result;
4. the performance budget is explicitly approved after calibration;
5. formal accessibility and legal/brand reviews close;
6. production default-off readiness, kill switch, operational backup/restore,
   and live canary are explicitly authorized and tested.

The in-app browser currently fails during host path initialization before a
browser or tab can be selected. No Notion workspace was opened, including the
synthetic reference workspaces. Personal workspaces and content remain
forbidden.

## Safety and provenance

- No personal Notion, live `user_data`, `knowledge`, `.runtime`, or session
  data was accessed.
- No trial, payment, invitation, public posting, live migration, production
  flag, consumer, or resolver was enabled.
- Knowledge Core and Second Brain protected sources were not changed.
- The workspace is not recognized as a Git repository, so this audit makes no
  commit or landed-code claim.
