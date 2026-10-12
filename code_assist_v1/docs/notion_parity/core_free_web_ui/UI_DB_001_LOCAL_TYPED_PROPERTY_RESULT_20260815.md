# UI-DB-001 local typed-property round-trip result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; official Free reference and differential pending

## Outcome

`UI-DB-001` now exercises one complete user-visible database path in an isolated
synthetic workspace:

1. create a page and insert a table through the slash menu;
2. place keyboard focus on the database table rather than its search field;
3. add a text property;
4. create a row page and edit text, status, and date properties in side peek;
5. preserve row focus while the parent table is saved and the open row is
   revalidated in the background;
6. close, reload, and prove the three typed values survived exactly;
7. reopen the row page and prove the same values are visible there; and
8. prove the Second Brain item and revision counts did not change.

The journey runs twice in independent OS-temporary roots and requires the two
identifier-free reports to be byte-equivalent.

## Cohesive UI correction

Only the existing client surface and its focused tests changed.

- Database table markup now has `role="table"`, `tabindex="0"`, and a stable
  accessible label.
- Structured-block focus explicitly prefers the table over the earlier search
  input in DOM order.
- Side-peek focus snapshots now include typed database-property controls.
- Background revalidation of an open row captures and restores side-peek focus
  around the DOM replacement.
- Node source-contract probes bind those focus and accessibility properties.

No route, schema, migration, runtime flag, resolver, consumer, auth, or
Second Brain production boundary changed.

## Redacted local evidence

- source UI build: `sha256:5a2a276d64835a75f2f1260a7a8aca4fe87b197e325a38cd41dca19f37d1f38d`
- `UI-DB-001-A01`: `sha256:1a186880aa5d9c7434c0d9f990ba0b4226ab2a1d22c00a5bd74b9fe33383e5cc`
- `UI-DB-001-A02`: `sha256:9199cfed5dd8936a756bce4579a2897357acdf31cb2d2b1dfe6d8bb05aeb1905`
- focus sequence: `DATABASE-GRID -> DATABASE-ROW`
- atomic parent-page save requests observed: `4`
- exact property reload: pass
- row-page reopen: pass
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

The repository contains hashes and normalized semantic evidence only. No raw
DOM, network log, cookie, token, filesystem path, personal workspace label, or
personal content was captured.

## Verification

- focused repeated real-Chrome `UI-DB-001`: `1 passed, 12 deselected in 21.93s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 12.22s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.90s`;
- plan, fixture, evidence, packet, differential, and reference-guard contracts:
  `90 passed in 25.34s`;
- compact Core Free plan verifier: pass, 25 required scenarios, zero promotion.

Two full 13-test Chrome attempts each finished with `12 passed` and one distinct
pre-existing autosave-response timeout: first `UI-EDITOR-006`, then
`UI-EDITOR-004`. Each timed-out journey passed its exact isolated retry
(`UI-EDITOR-006`: `1 passed in 19.80s`; `UI-EDITOR-004`: `1 passed in 18.93s`).
This is retained as a long-run Windows fixture-timing debt rather than reported
as a clean full-suite pass. The new database journey passed in both full runs.

One read-only discovery command also ended with Windows host exit
`0xC0000409`; its simpler exact retry succeeded. Neither event is classified as
a product pass or product failure.

## Claim boundary and next slice

This evidence proves an isolated local implementation, not official Notion
parity. `UI-DB-001` remains `planned` in the Core denominator until matching
synthetic `REF-FREE-A/B` evidence and a differential exist.

If safe signed-in reference capture remains unavailable, the next cohesive H5
slice is `UI-DB-002`: deterministic filter, sort, and group state with reload.
