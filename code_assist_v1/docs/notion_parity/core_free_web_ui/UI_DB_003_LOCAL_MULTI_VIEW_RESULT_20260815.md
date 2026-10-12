# UI-DB-003 local multi-view preservation result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
Status: isolated local real-Chrome green; official Free reference and differential pending

## Outcome

`UI-DB-003` now drives one synthetic database through the normal local UI and
proves that table, board, list, gallery, and calendar are views over the same
canonical rows:

1. create a table and three synthetic row pages;
2. set exact synthetic title, status, and current-month date values;
3. add board, list, gallery, and calendar views through the visible view dialog;
4. verify all three row identities are rendered in every view;
5. switch through all five view tabs and require the newly rendered active tab
   to retain focus;
6. reload while calendar is active and verify that view and its rows return;
7. switch back to table and verify every title, status, and date is unchanged;
8. prove view work issued zero row-create or row-delete requests after fixture
   setup; and
9. prove Second Brain item and revision deltas remain zero.

The journey runs twice in independent OS-temporary roots and requires exact,
identifier-free report equality.

## Cohesive UI correction

- The database view strip now exposes a named `tablist` and each view exposes a
  selected-state `tab`.
- Switching views restores focus to the active tab after the database block is
  rerendered. The lookup is bound to the stable block ID and view ID rather than
  a stale DOM node or an interpolated selector.
- The add-view control has an explicit accessible label.
- Static Node probes lock the tab semantics and post-render focus restoration.

No route, canonical schema, migration, auth boundary, runtime flag, resolver,
consumer, or Second Brain production code changed.

## Redacted local evidence

- source UI build: `sha256:64bd8acf056c0c1aada8937134adbd013ee95b9adcce53ed0ca39ef9553e7ec8`
- `UI-DB-003-A01`: `sha256:d9ff63edbe163d01ca41815326dab28c72f0c08ebd70c8add43e60260632101e`
- canonical row preservation: pass
- persisted active calendar view after reload: pass
- final table title/status/date equality: pass
- rendered view count: `5`
- view-state save requests: `10`
- row create/delete requests after setup: `0`
- Second Brain item delta: `0`
- Second Brain revision delta: `0`

Repository evidence contains no page/row identifier, raw DOM, network log,
token, path, personal label, or personal data.

## Verification

- initial product red: active view tab lost focus after rerender;
  `1 failed, 14 deselected in 22.84s`;
- first focus correction red: lookup used a stale normalized block object;
  `1 failed, 14 deselected in 22.41s`;
- capture-contract red: the new `tablist` value was not yet allowlisted;
  `1 failed, 14 deselected in 22.72s`;
- focused repeated real-Chrome `UI-DB-003`:
  `1 passed, 14 deselected in 46.10s`;
- combined `UI-DB-001` through `UI-DB-003`:
  `3 passed, 12 deselected in 95.25s`;
- complete local real-Chrome suite: `15 passed in 327.28s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.13s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.78s`;
- plan, fixture, evidence, packet, evidence-policy, differential, and
  reference-guard contracts: `90 passed in 23.94s`;
- JavaScript/Python syntax and compact scenario verifier: pass.

## Claim boundary and next slice

This proves an isolated local implementation only. `UI-DB-003` and all H5
database scenarios remain `planned` in the Core denominator until matching
synthetic `REF-FREE-A/B` evidence and differentials exist.

If signed-in reference capture remains unavailable, the next cohesive local H6
slice is `UI-SEARCH-002`: in-page find plus synthetic page-link/backlink
navigation without workspace or Second Brain scope leakage. `UI-SEARCH-001`
quick-search behavior is already covered by the repeated local golden journey.
