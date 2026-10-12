# UI-SEARCH-002 local find/link/backlink result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; official Free reference and differential pending

## Outcome

`UI-SEARCH-002` now drives a synthetic parent/child page journey through the
normal local UI:

1. select isolated Synthetic Workspace A;
2. open its synthetic root page;
3. add one synthetic in-page marker and one child-page mention as fixture setup;
4. prove the mention picker omits Synthetic Workspace B content;
5. open current-page find with `Ctrl+F`, find exactly one marker, retain input
   focus while the block is highlighted, and return focus to the invoking
   editor with Escape;
6. follow the child-page link;
7. open the child's backlink dialog and prove it contains the synthetic root
   but no Workspace B title;
8. follow the backlink to the exact root block and restore page-canvas focus;
9. prove the read-only find/link/backlink phase performs zero page saves,
   creates, or deletes; and
10. prove Second Brain item and revision deltas remain zero.

The journey runs twice in independent OS-temporary roots and requires exact,
identifier-free report equality.

## Cohesive UI correction

- Current-page find now remembers the control that opened it.
- Highlighting a title or block no longer steals focus from the find query.
- Escape or the close control returns focus to the still-connected invoking
  editor/control with scroll preservation.
- Static Node probes lock both query-focus retention and close-focus recovery.

No route, canonical schema, migration, auth boundary, runtime flag, resolver,
consumer, or Second Brain production code changed.

## Redacted local evidence

- source UI build: `sha256:7379c993b6dc86747b256dac94eb76abbbb2ba09fcaecfd87304a52d2ac8841f`
- `UI-SEARCH-002-A01`: `sha256:f005abe4900c1b715b7558b6ba40c929bc0726b2ff581bfc07b2ac70e525f4b3`
- `UI-SEARCH-002-A02`: `sha256:a05a03b61f913e280210382f44923f381dd1982a0a34be20c8f62c1e23a83e06`
- exact in-page match count: `1`
- focus sequence: find input -> invoking editor -> page canvas
- other-workspace mention/backlink title count: `0`
- page mutation count after fixture setup: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

Repository evidence contains no workspace/page/block identifier, raw DOM,
network log, token, path, personal label, or personal data.

## Verification

- initial product red: in-page highlighting stole query focus;
  `1 failed, 15 deselected in 15.53s`;
- focused repeated real-Chrome `UI-SEARCH-002`:
  `1 passed, 15 deselected in 20.79s`;
- combined local quick-search and find/link/backlink journeys:
  `2 passed, 14 deselected in 36.68s`;
- complete local real-Chrome suite: `16 passed in 343.63s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.51s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.92s`;
- plan, fixture, evidence, packet, evidence-policy, differential, and
  reference-guard contracts: `90 passed in 24.16s`;
- JavaScript/Python syntax and compact scenario verifier: pass.

## Claim boundary and next slice

This proves an isolated local implementation only. `UI-SEARCH-001` and
`UI-SEARCH-002` remain `planned` in the Core denominator until matching
synthetic `REF-FREE-A/B` evidence and differentials exist.

If signed-in reference capture remains unavailable, the next cohesive local H6
slice is `UI-COLLAB-001`: create, resolve, and reopen a synthetic comment
thread through the Free-owner surface without workspace or Second Brain scope
leakage.
