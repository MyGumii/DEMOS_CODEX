# UI-COLLAB-001 local comment-thread result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; official Free reference and differential pending

## Outcome

`UI-COLLAB-001` now drives one complete synthetic comment thread through the
normal local Free-owner surface:

1. create and save a synthetic page;
2. open its comment drawer and post one synthetic comment;
3. focus the exact newly rendered thread;
4. keep the thread visible with the `전체` filter, resolve it, and focus the
   exact rerendered resolved thread;
5. reopen it and focus the same thread again;
6. reload the page and prove the reopened text, state, and page association;
7. require the exact mutation sequence `POST -> PATCH -> PATCH`;
8. prove external request count is zero; and
9. prove Second Brain item and revision deltas remain zero.

The journey runs twice in independent OS-temporary roots and requires exact,
identifier-free report equality.

## Cohesive UI correction

- Each root comment thread now exposes a named, programmatically focusable
  `group` without adding it to normal Tab order.
- Comment creation uses the returned root comment identity to focus the exact
  thread after list revalidation.
- Resolve and reopen use the same stable identity to restore focus after their
  DOM replacement.
- Replies resolve to their root thread before focus restoration.
- Static Node probes lock the semantic target and all mutation focus paths.

No route, canonical schema, migration, auth boundary, runtime flag, resolver,
consumer, notification, invite, or Second Brain production code changed.

## Redacted local evidence

- source UI build: `sha256:143f164d6854783abdede39454a2672c1d30e4ecfd0331dde2066ac0c02c7f95`
- `UI-COLLAB-001-A01`: `sha256:c826b3e1ead95c90daa65b896593cefb3bf684d695431c539d1c2117c928fbce`
- `UI-COLLAB-001-A02`: `sha256:0d776195593c2747e52a4cff82a005132e8147aed61404d8410d25a64eb16cd2`
- comment mutation sequence: `POST`, `PATCH`, `PATCH`
- focus sequence: thread, thread, thread
- reload round-trip: pass
- external request count: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

Repository evidence contains no comment/page/workspace identifier, raw DOM,
network log, token, path, personal label, or personal data.

## Verification

- initial product red: posting rerendered the list without focusing the thread;
  `1 failed, 16 deselected in 15.40s`;
- test-contract correction: resolved state belongs to the inner comment article,
  followed by one unrelated title-autosave setup timeout;
  `1 failed, 16 deselected in 33.47s`;
- exact retry of the focused repeated journey:
  `1 passed, 16 deselected in 14.99s`;
- combined local H6 quick-search/find-link/comment journeys:
  `3 passed, 14 deselected in 50.97s`;
- first complete Chrome run: new/comment and 15 adjacent scenarios passed, while
  one pre-existing sidebar-collapse click was not observed;
  `1 failed, 16 passed in 361.59s`;
- exact sidebar test retry: `1 passed in 16.36s`;
- final complete local real-Chrome suite: `17 passed in 358.73s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.66s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.78s`;
- first evidence bundle run had one isolated fixture startup timeout with the
  other 89 tests passing; its exact retry passed in `3.01s`;
- final plan, fixture, evidence, packet, evidence-policy, differential, and
  reference-guard bundle: `90 passed in 24.28s`;
- JavaScript/Python syntax and compact scenario verifier: pass.

The two setup/host timing failures are recorded rather than counted as product
passes or product failures; both exact selections and the final full bundles
subsequently passed.

## Claim boundary and next slice

This proves an isolated local implementation only. `UI-COLLAB-001` remains
`planned` in the Core denominator until matching synthetic `REF-FREE-A/B`
evidence and a differential exist.

If signed-in reference capture remains unavailable, the next cohesive local H6
slice is `UI-SHARE-001`: inspect and close Free-visible sharing controls without
inviting, publishing, or changing access.
