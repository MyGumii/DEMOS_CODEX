# UI-SHELL-003 local hierarchy result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

The isolated Chrome journey now presents a coherent transition from Home to one
active workspace page.  Home and page surfaces are explicit focusable regions,
route changes place focus on the newly active region, the breadcrumb exactly
matches the active page title, and the topbar contains no page title from the
other synthetic workspace.

This is an H3 local interaction and accessibility result.  Geometry is recorded
for later reference calibration; no geometry threshold or official Notion parity
is claimed.

## Reproduced defect and correction

The first browser run failed because clicking Home left focus on the sidebar
button instead of the newly displayed Home region.  Opening a page likewise left
focus on the sidebar row.

The cohesive correction:

1. marks `homeView` and `pageView` as labeled, programmatically focusable regions;
2. moves focus to `HOME-MAIN` after a successful Home route transition;
3. moves focus to `PAGE-CANVAS` after a successful page load;
4. preserves New Page behavior because its existing post-create callback still
   advances focus from the page canvas to the title input;
5. keeps the workspace switcher's later focus restoration as the final focus
   target during workspace changes.

## Browser evidence

The Home -> active page journey ran twice in independent isolated roots and
returned identical identifier-free reports:

```json
{
  "breadcrumb_matches_active_page": true,
  "focus_sequence": ["HOME-MAIN", "PAGE-CANVAS"],
  "hidden_workspace_metadata_count": 0,
  "synthetic_only": true
}
```

Local capture SHA-256:

```text
UI-SHELL-003 sha256:cf2724e661140ce7c9cbb739c4158d4d372ae662116d63b5bcb072fd1e1010b1
```

UI source build SHA-256:

```text
sha256:1a0d7af5c10afd2ecc962721dc22d6561d462211df0d7940bc8d8e07ced3e78b
```

No raw DOM, screenshot, request log, cookie, credential, path, workspace/page
identifier, or personal text is stored.

## Verification

- UI-SHELL-003 red: Home region was visible but not focused;
- UI-SHELL-003 green: `1 passed in 16.00s`;
- complete local browser file, including UI1, Second Brain, UI-SHELL-001, and
  UI-SHELL-003: `4 passed in 78.61s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- NP2B navigation contract verifier: `3 passed in 0.70s`;
- prior exact-state adjacent gates remain bound by the parent UI-SHELL-001
  checkpoint.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- The two workspaces and pages existed only inside trusted temporary roots.
- No live feature flag, migration, payment, trial, invite, or public action was
  performed.
- Business, Enterprise, and Archive remain conditional.

`REF-FREE-A/B` C0, official synthetic reference capture, and the first sealed
differential remain pending.  `UI-SHELL-003` is local-green only.
