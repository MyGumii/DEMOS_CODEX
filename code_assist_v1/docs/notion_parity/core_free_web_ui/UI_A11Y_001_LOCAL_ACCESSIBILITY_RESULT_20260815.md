# UI-A11Y-001 local accessibility result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; reference and differential pending

## Outcome

`UI-A11Y-001` now exercises the adopted shell, navigation, editor, search,
share inspector, icon picker, and canonical Second Brain publication preview
as one keyboard-first journey in real Chrome.

The repeated journey proves that:

1. all 36 visible native and composite tab targets have an accessible name,
   role, and state;
2. actual `Tab` traversal equals the enumerated DOM tab order;
3. every adopted target receives a visible focus indicator;
4. the search dialog and Second Brain preview each trap forward and reverse
   keyboard focus;
5. Escape closes search, share, and an unlocked icon-picker surface and
   restores focus to the exact trigger;
6. explicit keyboard Cancel closes the intentionally locked publication
   preview and restores the publication trigger;
7. `prefers-reduced-motion: reduce` disables smooth scrolling and collapses
   animation and transition duration;
8. forced-colors mode retains a visible focus outline;
9. no external request is made; and
10. the cancellation-only journey changes zero Second Brain items and zero
    Second Brain revisions.

The complete journey runs twice in independent trusted OS-temporary roots and
requires exact identifier-free report equality.

## Product corrections

The first real-browser run exposed missing accessible names on the title and
editor surfaces. The cohesive correction adds:

- stable Korean `aria-label` values for sidebar open/close, page title,
  editor blocks, and drawer close controls;
- a visible `:focus-visible` outline across adopted native and composite
  controls;
- one visible-modal `Tab`/`Shift+Tab` focus trap;
- focus-restoring Escape behavior for unlocked common modals;
- reduced-motion rules for scrolling, transitions, and animations; and
- a forced-colors focus treatment.

The locked Second Brain preview intentionally continues to ignore generic
Escape/close behavior. Its explicit Cancel action is keyboard reachable and
is the tested focus-restoring exit.

Several subsequent red runs corrected the browser test contract rather than
the product: ARIA tree-item text naming, dynamic search-dialog endpoints, and
dynamic publication-modal endpoints. Those corrections are retained so the
test follows the actual accessible surface instead of assuming a fixed button
order.

Routes, schema, migrations, auth, runtime flags, resolver, Knowledge Core, and
Second Brain production services are unchanged.

## Redacted local evidence

- identifier-free report SHA-256:
  `sha256:7fe4776405495725eec8fb59b4e55ab767a461c446c872297247c27df00554e2`
- normalized UI capture SHA-256:
  `sha256:d4d5978642158fc616477674f3bb0c11b3ad9f519d449b330ce8764a9cfc225c`
- accessible tab targets: `36`
- visible focus indicators: `36`
- modal focus traps: `2`
- unlocked-modal Escape focus restoration: `true`
- external requests: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`
- synthetic-only: `true`

The report contains no page, workspace, session, or user identifier; no raw
DOM or network log; no token, filesystem path, personal label, or personal
content.

## Verification

- first product red: missing page-title accessible name,
  `1 failed, 20 deselected in 14.42s`;
- three browser-contract corrections for semantic naming and dynamic dialog
  endpoints, followed by the explicit locked-modal Cancel contract;
- final focused repeated real-Chrome journey:
  `1 passed, 20 deselected in 19.42s`;
- final complete local real-Chrome suite: `21 passed in 428.19s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.33s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.62s`;
- plan, fixture, capture, redaction, evidence packet, differential, and
  reference-guard bundle: `90 passed in 24.49s`;
- isolated identifier-free report extraction: pass;
- Python syntax and compact scenario verifier: pass with 25 required scenarios
  and zero status promotions.

The in-app Chrome control runtime remains unavailable because its host setup
fails before browser or tab selection. No reference or personal workspace was
opened. The repository-owned isolated Chrome harness is the source of the
successful local evidence above; the host failure is not counted as a product
failure.

## Claim boundary and next slice

This proves only the isolated local accessibility contract. `UI-A11Y-001`
remains `planned` in the shared denominator until a redacted `REF-FREE-A/B`
observation and reviewed differential exist. No reference parity, WCAG
conformance certification, mobile parity, or Core completion claim is made.

The next cohesive H8 slice is `UI-PERF-001`: measure repeatable real-Chrome
shell load, page open, editor input, search, save/reload, and Second Brain
preview timings. Values remain measurement-only until a reference capture or
explicitly reviewed local budget establishes promotion thresholds.
