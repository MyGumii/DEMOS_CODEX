# UI-SB-003 local session-context result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; local-only Second Brain hard journey

## Outcome

`UI-SB-003` now drives one complete synthetic page/session context lifecycle
through the normal local UI:

1. seed one synthetic session under the trusted OS-temporary fixture root;
2. create and save one synthetic page;
3. open the page's Second Brain session-context drawer;
4. attach the page to the selected session;
5. re-read the attached-page list through the current-access-filtering route;
6. focus the exact session-context region after its DOM refresh;
7. detach the page and focus the same stable region again;
8. require the exact mutation sequence `POST -> DELETE`;
9. prove external request count is zero; and
10. prove Knowledge publication item and revision deltas remain zero.

The journey runs twice in independent OS-temporary roots and requires exact,
identifier-free report equality. Variable bootstrap GET counts are reduced to a
stable `current_access_revalidated=true` assertion only after at least three
current-access-filtered list reads were observed.

## Cohesive UI correction

- The context panel now exposes a named, programmatically focusable `region`.
- Attach and detach both restore focus to that stable region after the attached
  list and overview have revalidated and rerendered.
- Static Node probes lock the region semantics and both mutation focus paths.
- The synthetic session seed contains no real conversation, user, workspace, or
  remote service data and exists only inside the isolated browser fixture.

No route, canonical schema, migration, auth boundary, runtime flag, resolver,
consumer, session authorization rule, Knowledge publication behavior, or
Second Brain production store changed.

## Redacted local evidence

- source UI build: `sha256:0c8231c2070aae86c6665e941e3b8efcd8b6f939e81e4979a2167e2b54cc6fa3`
- `UI-SB-003-A01`: `sha256:cd507bac693773362572138616090f076f4352659179214abb4e30a992f33af8`
- `UI-SB-003-A02`: `sha256:1f4b90ace23df89c2939057b5084177ba6651b15e4597ae8bf39eceb270a9669`
- attachment mutation sequence: `POST`, `DELETE`
- focus sequence: session context, session context
- current-access revalidated: `true`
- external request count: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

Repository evidence contains no page/session/user identifier, message content,
raw DOM, network log, token, filesystem path, personal label, or personal data.

## Verification

- initial product red: context panel lacked region semantics;
  `1 failed, 18 deselected in 12.80s`;
- first post-correction run proved the product flow but exposed nondeterministic
  bootstrap GET counts (`7` versus `6`) in the report; the internal lower bound
  remained exact while the external report was normalized to a boolean;
  `1 failed, 18 deselected in 17.68s`;
- focused repeated real-Chrome journey:
  `1 passed, 18 deselected in 14.69s`;
- first complete Chrome run: the new journey and 17 adjacent scenarios passed,
  while one pre-existing editor setup timed out waiting for title autosave;
  `1 failed, 18 passed in 400.02s`;
- exact editor selection retry: `1 passed in 18.90s`;
- final complete local real-Chrome suite:
  `19 passed in 387.83s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.63s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.76s`;
- the combined shell exited with the known Windows `0xC0000409` after those
  green sets and before the evidence process produced output;
- exact evidence-bundle run in a new shell:
  `90 passed in 24.09s`;
- JavaScript/Python syntax and compact scenario verifier: pass.

The setup timeout and host process exit are recorded rather than counted as
product passes or failures. The exact affected selections subsequently passed.

## Claim boundary and next slice

`UI-SB-003` is a local-only product journey because official Notion has no
Second Brain control to compare. It remains `planned` in the shared denominator
until the adopted evidence-status policy is advanced as a reviewed whole; no
reference or parity promotion is made here.

The next cohesive local H8 slice is `UI-RESP-001`: exercise the adopted desktop
and narrow web viewports across shell, editor, share/session drawers, and
overlays without horizontal overflow, hidden essential controls, or focus loss.
