# UI-SHARE-001 local share-dialog result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; official Free reference and differential pending

## Outcome

`UI-SHARE-001` now exercises the Free-visible share surface as an explicitly
read-only synthetic journey:

1. create and save one synthetic page;
2. invoke sharing from the page topbar;
3. expose the inspector as a named, programmatically focusable non-modal
   dialog;
4. read the current internal-share and public-link states without selecting an
   invitee or publication action;
5. keep focus on the share dialog after it opens;
6. dismiss it with Escape and restore focus to the exact share trigger;
7. prove share, permission, public-link, invite, and external mutation count is
   zero; and
8. prove Second Brain item and revision deltas remain zero.

The journey runs twice in independent OS-temporary roots and requires exact,
identifier-free report equality.

## Cohesive UI and harness correction

- The shared inspector surface now exposes `role="dialog"`, its visible title
  through `aria-labelledby`, and a programmatic focus target without claiming
  modality.
- Opening a drawer captures the invoking control only at the closed-to-open
  boundary and focuses the rendered inspector.
- Escape and the explicit close control dismiss the drawer and restore the
  invoking control; navigation-driven closure deliberately does not restore a
  stale control.
- Static Node probes lock the semantic dialog, return-focus, and Escape paths.
- The isolated real-browser fixture now selects an available loopback port from
  a Chrome-safe range instead of accepting an OS-selected legacy service port.

No route, canonical schema, migration, auth boundary, runtime flag, resolver,
consumer, access rule, public-link behavior, invite behavior, or Second Brain
production code changed.

## Redacted local evidence

- source UI build: `sha256:cf2bfc112fb04fca523576901a38f24cb502a61e7abf364fcd1cb4f5861de194`
- `UI-SHARE-001-A01`: `sha256:d58c3d2655f3cb3b1731af786453aef53bc4c21cc2637785d65a796f5d2f9888`
- `UI-SHARE-001-A02`: `sha256:1d9684056ff48e610521981b6a5123f9fd56513bb183ae011659aa8fa350689d`
- focus sequence: share dialog, share trigger
- access/public-link mutation count: `0`
- external request count: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

Repository evidence contains no page/workspace/user identifier, public token,
raw DOM, network log, cookie, filesystem path, personal label, or personal data.

## Verification

- initial product red: the inspector exposed only complementary/aside semantics;
  `1 failed, 17 deselected in 13.33s`;
- first static assertion after implementation was over-constrained around the
  timer callback punctuation and was corrected without changing behavior;
- focused JavaScript/Python syntax plus repeated real-Chrome journey:
  `1 passed, 17 deselected in 14.62s`;
- H6 combined run: the new share and search cases passed while the existing
  comment case timed out before its feature path waiting for title autosave;
  `1 failed, 2 passed, 15 deselected in 61.15s`;
- exact comment retry: `1 passed in 14.47s`;
- first complete Chrome run: `17 passed`; one editor-recovery run received the
  Chromium-rejected loopback port `3659` before application navigation;
  `1 failed, 17 passed in 356.81s`;
- the first defensive port filter proved incompatible with this host's low
  dynamic range and failed before navigation; it was replaced with bounded
  explicit safe-range allocation;
- exact editor-recovery retry after the final allocator:
  `1 passed in 20.61s`;
- final complete local real-Chrome suite:
  `18 passed in 372.91s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.52s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.87s`;
- plan, fixture, evidence, packet, evidence-policy, differential, and
  reference-guard bundle: `90 passed in 24.86s`;
- JavaScript/Python syntax and compact scenario verifier: pass.

The setup and port failures are recorded rather than counted as product passes
or product failures. Their exact affected selections and the final complete
bundles subsequently passed.

## Claim boundary and next slice

This proves an isolated local implementation only. `UI-SHARE-001` remains
`planned` in the Core denominator until matching synthetic `REF-FREE-A/B`
evidence and a differential exist.

If signed-in reference capture remains unavailable, the next cohesive local H7
slice is `UI-SB-003`: attach and detach one synthetic page from one synthetic
session with current-access revalidation, exact focus/error recovery, and no
publication mutation.
