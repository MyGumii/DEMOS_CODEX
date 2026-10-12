# UI-DB-002 local filter/sort/group view-state result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; official Free reference and differential pending

## Outcome

`UI-DB-002` now drives one complete Notion-familiar table-view configuration
through the normal local UI:

1. create a table and three synthetic row pages;
2. set exact synthetic title and status values;
3. apply a title filter;
4. apply descending title sort;
5. group the table by status;
6. verify the grouped visible order and all source row values;
7. reload the page; and
8. verify the same active controls, groups, order, values, and table focus.

The journey runs twice in independent OS-temporary roots and requires exact
identifier-free report equality.

## Cohesive UI correction

- Database blocks now have a persisted `group_property` view-state field.
- The toolbar exposes an active-state `그룹` control alongside filter and sort.
- The group dialog permits an explicit property selection or `그룹 없음`.
- Table rows render under deterministic non-empty group headings after the
  existing filter and sort pipeline.
- Removing a property clears any matching active or saved-view group state.
- The core model and Node runtime probes assert the default state, no row loss,
  deterministic ordering, group count, and persisted toolbar contract.

No route, canonical schema, migration, auth boundary, runtime flag, resolver,
consumer, or Second Brain production code changed.

## Redacted local evidence

- source UI build: `sha256:65dec8b7c88b296e1429b12bd285b5403bc63191aba8d9d30d0e9ed1469a5bc4`
- `UI-DB-002-A01`: `sha256:3da04aa1af2c2df6b7e8f6030e77fddbabe40a2de82714e2ec8d98c4056448d6`
- `UI-DB-002-A02`: `sha256:3da04aa1af2c2df6b7e8f6030e77fddbabe40a2de82714e2ec8d98c4056448d6`
- view-state save requests after fixture setup: `3`
- filter/sort/group reload: pass
- source rows unchanged: pass
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

The equal A01/A02 hash is intentional: normalized state, focus, accessibility,
semantic region, geometry, action hash, source build, and redaction envelope are
identical before and after reload. Repository evidence contains no row/page
identifier, raw DOM, network log, token, path, personal label, or personal data.

## Verification

- initial product red: group control absent; `1 failed in 45.47s`;
- test-contract correction red: installed Playwright lacks
  `Locator.all_input_values`; `1 failed in 15.32s`;
- focused repeated real-Chrome `UI-DB-002`: `1 passed in 30.89s`;
- combined `UI-DB-001` and `UI-DB-002`: `2 passed in 50.16s`;
- complete local real-Chrome suite: `14 passed in 280.64s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.11s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.62s`;
- plan, fixture, evidence, packet, differential, and reference-guard contracts:
  `90 passed in 25.44s` after one recorded Windows host-process failure;
- JavaScript/Python syntax and JSON/verifier checks: pass.

The combined regression command ended with Windows `0xC0000409` while starting
the final harness subprocess, after Node, Notion, and Second Brain selections had
already passed. The exact harness-only retry passed; the host exit is not counted
as either a product pass or product failure.

## Claim boundary and next slice

This proves an isolated local implementation only. `UI-DB-002` remains
`planned` in the Core denominator until matching synthetic `REF-FREE-A/B`
evidence and a differential exist.

If signed-in reference capture remains unavailable, the next cohesive H5 slice
is `UI-DB-003`: table/board/list/gallery/calendar view switching without row
creation, deletion, or canonical value drift.
