# UI-PERF-001 local performance measurement result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Browser: Chrome `151.0.7922.110`, headless, 1440x900  
Status: isolated local measurement green; no budget or reference parity claim

## Measurement contract

Two independent trusted OS-temporary roots were used. Each root created one
synthetic page outside the timed sequence, then ran three independent Chrome
contexts through the same measured actions. The six samples for every metric
are bound to:

- scenario `UI-PERF-001`;
- fixture alias `UI-PARITY-RUN-EDITOR`;
- action-log SHA-256
  `sha256:822e31b9467911e276e0c3dbf0a014e1fb289b8b06825ca9cf007fb495b2a300`;
- source-build SHA-256
  `sha256:afb22017fe74799f243b960337b2bb30de949dd6a9434a940a60d01350ca8a60`;
- the exact Chrome version above; and
- successful restored-page, zero-external-request, synthetic-only, and
  zero-publication-delta states.

Nearest-rank percentiles are used. Values are measurements in milliseconds,
not release thresholds.

## Results

| Action | min | p50 | p95 | p99 | max | samples |
|---|---:|---:|---:|---:|---:|---:|
| Login through shell ready | 613.854 | 638.066 | 779.891 | 779.891 | 779.891 | 6 |
| Existing page ready | 38.068 | 78.676 | 179.463 | 179.463 | 179.463 | 6 |
| Editor input acknowledgement | 23.937 | 43.598 | 70.257 | 70.257 | 70.257 | 6 |
| Save ready | 1361.772 | 1380.734 | 1401.620 | 1401.620 | 1401.620 | 6 |
| Reload and restored content ready | 212.159 | 238.975 | 489.261 | 489.261 | 489.261 | 6 |
| Search overlay ready | 76.731 | 85.672 | 94.642 | 94.642 | 94.642 | 6 |
| Search result ready | 11.957 | 14.632 | 66.431 | 66.431 | 66.431 | 6 |
| Second Brain preview ready | 873.529 | 904.201 | 932.077 | 932.077 | 932.077 | 6 |

Identifier-free report SHA-256:
`sha256:e1aca063da1238534bc8ee5e7aff0a5b94512809b532b538e585a85bcc7d3a21`.

## State and safety result

- external browser requests: `0`;
- page content restored after reload: `true` for all six samples;
- Second Brain item delta: `0`;
- Second Brain revision delta: `0`;
- synthetic-only: `true`;
- personal Notion access: `false`;
- live local data access: `false`;
- trial, payment, invite, public posting, migration, flag, consumer, or
  resolver change: `false`.

No raw URL, page ID, user ID, session ID, data-root path, token, title,
paragraph, DOM, network log, or personal content is retained in the report.

## Probe corrections

The first focused run failed because the probe treated title focus as part of
page-ready after every new-page creation. The next extraction exposed that
repeated page creation was itself a noisy mutation and not the intended
page-open measurement. A second setup attempt also exposed an avoidable
title/block autosave race.

The final contract therefore creates one default synthetic page per isolated
root before timing, returns to Home for every session, and measures opening the
same existing page. This is a test-contract correction; no production code was
changed to improve the numbers.

## Verification

- final focused performance journey: `1 passed in 37.20s`;
- identifier-free evidence extraction: pass in `37.5s`;
- complete existing Chrome suite plus performance journey:
  `22 passed in 474.64s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.48s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.86s`;
- Python syntax and compact scenario verifier: pass with 25 required
  scenarios and zero promotions.

The 90-test evidence/reference-guard bundle had passed on the same authoritative
documents before the independent performance test file was added. Its two
post-performance executions then terminated before collection/output with the
same Windows host status `0xC0000409`. Per the harness rule, the exact retry was
not repeated again and this is recorded as a host-dependent gate, not a test
or product pass/failure. The compact verifier remains green.

## Claim boundary and next phase

`UI-PERF-001` remains `planned`. These six local values establish a repeatable
precalibration baseline only. They do not prove Notion parity, a service-level
objective, production capacity, p95/p99 statistical confidence, or H8
completion. A reviewed `REF-FREE-A/B` pair or an explicitly approved local
budget is required before a threshold can be introduced or the scenario can
be promoted.

The next implementation phase is H9: an isolated synthetic pilot, explicit
rollback/recovery rehearsal, and completion audit. Reference characterization
remains the larger cross-phase gate whenever safe signed-in browser control is
available.
