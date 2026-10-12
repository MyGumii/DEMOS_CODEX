# UI-RESP-001 local viewport result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; reference and differential pending

## Outcome

`UI-RESP-001` now exercises one synthetic saved page at every adopted viewport:

- 1440×900;
- 1280×800;
- 980×800, including the exact desktop boundary; and
- 720×800, including the exact narrow-web boundary.

At each size, the real Chrome journey proves that:

1. primary sidebar navigation remains visible and reachable;
2. the page canvas and editor remain visible and horizontally contained;
3. the search overlay is wholly inside the viewport and Escape restores its trigger;
4. the share inspector and close control are wholly inside the viewport and Escape restores its trigger;
5. both `body` and `documentElement` have zero global horizontal overflow;
6. no external request is made; and
7. Second Brain publication item and revision deltas remain zero.

The complete journey runs twice in independent trusted OS-temporary roots and
requires exact identifier-free report equality.

## Implementation result

The pre-existing responsive CSS passed the new browser contract without a
product correction. The implementation gap was evidence coverage, not a
measured layout defect. The change is therefore limited to:

- a truthful viewport parameter in the normalized local capture helper;
- a repeated real-Chrome `UI-RESP-001` journey and exact overflow/focus/geometry assertions; and
- static Node guards for the adopted 980px, 820px, and 720px responsive rules.

`notion.css`, routes, schema, migrations, auth, runtime flags, resolver,
Knowledge Core, and Second Brain production services are unchanged.

## Redacted local evidence

- source UI build: `sha256:0c8231c2070aae86c6665e941e3b8efcd8b6f939e81e4979a2167e2b54cc6fa3`
- 1440×900 capture: `sha256:6362bbebb006dfcb7180234a7403790e1c0be9d6992698dcb34783a8a9ce0f64`
- 1280×800 capture: `sha256:9257a1f796d639ba7e1636aa3482bd3f15761293b0f30cab47223e3302bd5be3`
- 980×800 capture: `sha256:092993a64d9ffa25fbeba1039b8c0f4d003238594adbe4119ccb12733afa59bc`
- 720×800 capture: `sha256:df2a733ddf092e6dde6e88a7333517cc5f68f1ca231cda431148449c1698ab35`
- global horizontal overflow: `0` at all four viewports
- external request count: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

The report contains no page, workspace, session, or user identifier; no raw
DOM or network log; no token, filesystem path, personal label, or personal
content.

## Verification

- first focused real-Chrome coverage run: `1 passed, 19 deselected in 19.99s`;
- static responsive Node contract: pass;
- final complete local real-Chrome suite: `20 passed in 414.59s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 12.65s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.85s`;
- plan, fixture, capture, redaction, evidence packet, differential, and
  reference-guard bundle: `90 passed in 24.24s`;
- isolated identifier-free report extraction: pass;
- Python syntax and compact scenario verifier: pass with 25 required scenarios
  and zero promotions.

The in-app Chrome control runtime also failed twice before browser selection
with a host path initialization error. No tab or personal workspace was opened.
The repository-owned isolated Chrome harness remained available and is the
source of the successful local evidence above; the host failure is not counted
as a product result.

## Claim boundary and next slice

This proves only the local responsive contract. `UI-RESP-001` remains
`planned` in the shared denominator until a redacted `REF-FREE-A/B` observation
and reviewed differential exist. No visual threshold, reference parity, mobile
interaction, or Core completion claim is made.

The next cohesive local H8 slice is `UI-A11Y-001`: complete the full-surface
keyboard order, visible focus, role/name/state, focus-trap/return, reduced
motion, and forced-colors gate while retaining the current responsive and
Second Brain regressions.
