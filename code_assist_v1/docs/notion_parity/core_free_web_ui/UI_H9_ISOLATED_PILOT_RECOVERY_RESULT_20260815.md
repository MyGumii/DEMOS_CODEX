# H9 isolated pilot and recovery result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated synthetic rehearsal green; live pilot and Core release pending

## Outcome

The H9 offline pilot foundation now proves one complete copy-on-write recovery
journey in real Chrome:

1. default-off authorization requires explicit synthetic opt-in and exact
   readiness;
2. an engaged kill switch denies the pilot;
3. one baseline page is saved and survives reload;
4. baseline Second Brain preview cancellation creates zero items/revisions;
5. a quiesced 15-file synthetic tree is copied and fingerprint-verified;
6. the canary edit saves and exactly one publication creates one item and one
   revision;
7. recovery creates a separate new root and preserves the changed canary;
8. the restored page contains the baseline body; and
9. restored publication counts return exactly to zero.

No production code, route, startup path, runtime flag, migration, consumer, or
resolver is registered or enabled.

## Identifier-free report

- contract: `code_assist.notion.core_free_ui.isolated_pilot_report.v1`;
- report SHA-256:
  `sha256:eda0f45d754124385c461732d093c19bcf86517736c2d0cd583a5d0878cee997`;
- backup file count: `15`;
- baseline publication: `0` items / `0` revisions;
- canary publication: `1` item / `1` revision;
- restored publication: `0` items / `0` revisions;
- copy-on-write backup verified: `true`;
- changed canary root preserved: `true`;
- new restored root verified: `true`;
- kill-switch denial exercised: `true`;
- Second Brain hard gate: `true`;
- live local data accessed: `false`;
- synthetic-only: `true`.

The raw tree fingerprint, path, credential, session, page/user/workspace ID,
title, body, DOM, token, and network log are not persisted.

## Verification

- default-off, kill-switch, exact-readiness, copy-on-write recovery,
  identifier-free report, and import-boundary unit contracts plus the real
  Chrome pilot: `8 passed in 22.43s`;
- independent identifier-free real-Chrome report extraction: pass in `21.4s`;
- production UI files: unchanged from the sealed H8 accessibility state;
- Python syntax: pass;
- compact Core scenario verifier: pass with 25 required scenarios and zero
  promotions.

The prior H8 combined browser suite is `22 passed in 474.64s`, Node contracts
are green, Notion route/storage/publication is 25 green, and the Second Brain
regression set is 5 green. The evidence/reference pytest bundle remains a
host-dependent gate after two identical `0xC0000409` terminations; it is not
represented as a product failure or a fresh pass.

## Claim boundary

This completes only the isolated H9 pilot/recovery foundation. It does not
complete H9 or the active goal because the following remain unproved or
unauthorized:

- `REF-FREE-A/B` reference characterization and differentials;
- promotion of all 25 Core scenarios;
- an approved performance budget;
- formal accessibility, legal, and brand review;
- a production default-off flag, readiness endpoint, and kill switch;
- operational backup/restore and live canary approval; and
- final support, rollback, and release sign-off.

The operational procedure and stop conditions are recorded in
`CORE_FREE_WEB_UI_PILOT_RUNBOOK.md`.
