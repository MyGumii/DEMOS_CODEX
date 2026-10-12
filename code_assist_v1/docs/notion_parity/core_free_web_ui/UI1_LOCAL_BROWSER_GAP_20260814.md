# UI1 local real-browser gap — repeated Second Brain approval

Recorded: 2026-08-14T22:38:45+09:00  
Profile: `free-owner-chrome-desktop-ko-KR`  
Surface: isolated local Notion app in Stable Chrome, ephemeral profile  
Status: open H1 hard-gate gap; not reference evidence and not a parity claim

## Proven local behavior

`tests/browser/test_notion_ui1_golden_journey.py` ran against fresh trusted OS
temporary roots only.  The shell/search/editor subset reproduced exactly across
two runs:

- sidebar collapse and restore returned to the same geometry;
- search opened, dismissed, reopened, and found the synthetic page;
- the Korean synthetic title and paragraph saved and survived reload;
- no persistent user Chrome profile was supplied;
- no live `user_data`, `knowledge`, `.runtime`, session, or workspace database
was accessed.

The isolated Second Brain portion proved:

- preview cancel changed the isolated Markdown publication count by `0`;
- the first visible approval changed it by `1`;
- a second visible approval of the unchanged page changed it by another `1`.

The focused test therefore records
`UI1-BLOCKER-SB-LEGACY-NONIDEMPOTENT` as an expected failure.  UI1 remains
incomplete.

## Smallest observed cause boundary

The standalone Notion surface receives an authoritative absence result from
the canonical Knowledge publication probe and enters the preserved legacy
publication adapter.  That adapter derives the same source filename from the
page snapshot, while `second_brain_store` deliberately allocates a new unique
filename whenever the prior source exists.  The visible second approval is
therefore treated as a new import rather than a state-idempotent replay.

This is a product integration gap, not a browser fixture or redaction failure.
It does not authorize a silent edit to routes, the publication adapter,
`second_brain_store`, application registration, or Knowledge Core.

## Required decision before correction

The next implementation owner must choose and test one cohesive boundary:

1. make the isolated Notion surface use the existing canonical publication
   contract, including its operation/result replay behavior; or
2. add an explicit notion-snapshot idempotency contract to the legacy adapter,
   keyed by the immutable source snapshot identity rather than a filename.

Either choice must preserve preview-cancel mutation `0`, source-drift
fail-closed behavior, exactly one immutable publication, auth/CSRF/Origin
checks, and current app/Second Brain compatibility.  The expected failure is
removed only after the real-browser replay delta becomes `0` and adjacent
publication tests remain green.

## Separate unresolved reference gate

The signed-in Chrome Browser control connection still fails during host
initialization before selecting a tab.  No Notion tab, workspace, cookie,
profile, or content was inspected.  `REF-FREE-A/B` characterization and the
first reference/local differential therefore remain pending.

