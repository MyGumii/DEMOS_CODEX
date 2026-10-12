# UI-TREE-001 local expand/collapse result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

An isolated synthetic workspace now exposes a nested page as an actual
accessibility tree. Clicking the root disclosure expands the child, ArrowLeft
collapses it, the root remains the focused `treeitem` after each rerender, and a
collapsed descendant is absent from the DOM and keyboard order.

This is an H3 local interaction result. It does not claim official Notion
parity, visual calibration, create/move lifecycle completion, or resolver
cutover.

## Reproduced defect and correction

The first real-Chrome run reproduced a concrete rendering defect: ArrowLeft
changed the root disclosure icon and persisted the collapsed ID, but the child
remained visible. The renderer treated every unrendered descendant below a
collapsed node as an orphan and appended it again at depth zero. The same
rerender also discarded the focused row.

The cohesive correction:

1. marks every descendant of a collapsed node as intentionally hidden instead
   of treating it as an orphan;
2. preserves true orphan/cycle fallback behavior for unrelated malformed trees;
3. exposes the private and shared page collections as labeled `tree` regions;
4. exposes nested rows as `treeitem` nodes with exact `aria-level` and
   `aria-expanded` state;
5. restores focus to the same row inside the originating page-tree root after
   disclosure or ArrowLeft/ArrowRight rerender;
6. keeps the existing flat Favorites, Recents, and breadcrumb lists outside the
   tree role.

## Browser evidence

The expand/collapse journey ran twice in independent isolated roots and
returned identical identifier-free reports. A final evidence extraction from
the same final UI build returned:

```json
{"collapsed_descendant_count":0,"focus_sequence":["TREE-ROOT","TREE-ROOT"],"root_aria_expanded":"false","synthetic_only":true,"tree_role":"tree"}
```

Local capture SHA-256:

```text
UI-TREE-001 sha256:d1a9a91ac341a299f9fb1b90f3b7b3ad8bede6fa9cbec3f1b2922ec5e4968d71
```

UI source build SHA-256:

```text
sha256:f6c89f965d16232946eae15971f33fb494890331907da7421bd15d9d6a914845
```

No raw DOM, screenshot, request log, cookie, credential, path, workspace/page
identifier, or personal text is stored.

## Verification

- UI-TREE-001 red: after ArrowLeft the persisted root was collapsed but the
  child count remained `1`;
- focused UI-TREE-001 green: `1 passed, 4 deselected in 17.26s`;
- complete final local browser file: `5 passed in 98.78s`;
- isolated fixture/navigation/pages/auth/verifier regressions: `61 passed in
  73.64s`;
- Notion routes, atomic-save compatibility, and publication adapter: `25 passed
  in 11.75s`;
- Second Brain hard regressions: `5 passed in 4.80s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- final Python and JavaScript syntax checks: pass.

The first combined browser run had one capture-timing mismatch in the existing
editor reload journey. The scenario already requires `PAGE-CANVAS` focus after
reload, so the test now waits for that exact focus before capturing. The focused
retry and the complete final browser run are green.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- The nested source page, workspaces, catalog, and publication state existed
  only inside trusted temporary roots.
- No live feature flag, migration, payment, trial, invitation, public action,
  continuous consumer, or resolver cutover was performed.
- Business, Enterprise, and Archive remain conditional.

`REF-FREE-A/B` C0, official synthetic reference capture, and the sealed
differential remain pending. `UI-TREE-001` is local-green only. The next local H3
slice is `UI-TREE-002` (create/rename/nest/reorder/move), unless the signed-in
reference browser transport becomes available first.
