# R2/R3 approval-ready focus and accessibility patch map

Recorded: 2026-08-22 (Asia/Seoul)

Status: read-only source audit complete; owner approval pending; no product behavior change authorized.

This document converts the review queue's R2/R3 observations into an implementation-ready map. It does not adopt the reference observations, change the completion matrix, or authorize edits. The exact owner approval phrase in `evidence_review_gate.v1.json` remains the gate.

## Non-negotiable boundary

- Primary profile: `free-owner-chrome-desktop-ko-KR`.
- Reference workspaces: synthetic `REF-FREE-A` and `REF-FREE-B` only.
- Preserve Second Brain publication, attachment, and revision semantics.
- No route, schema, migration, authentication, Knowledge, or Second Brain source change in R2/R3.
- Do not weaken a currently keyboard-accessible focus target solely because a connected-page observation reported a generic `group`.
- Recapture the current local action immediately before any edit. If complete capture removes the apparent difference, stop without changing product code.

## Current immutable input snapshot

| Path | SHA-256 | Role |
|---|---|---|
| `notion_static/index.html` | `791a3a1663aad171a4661365b8b0ca9f56ef61ac1526bca5b782fee3026997ba` | roles, labels, focus anchors |
| `notion_static/notion.css` | `7f7b723aa10cbbb425e47227c6863c59efc0970d87e018a22850dc2e53bdad1d` | focus-visible presentation only if required |
| `notion_static/notion.js` | `112da8b087393b236499f7bc02e1a04c43ced7793fa59b1702fb3b98d7eaadc1` | focus transitions and entry context |
| `tests/browser/test_notion_ui1_golden_journey.py` | `297d5f164d63d8dfe4b153717e9f1f6b4661831b478856627d53607ad5f38e76` | real-browser contract |
| `tests/test_notion_ui_runtime.js` | `c9ccd1f49edd81c54af6e5f831312eed33cb1c47a3a307f93c3064dae0c100cc` | static runtime/role contract |
| `tests/test_notion_navigation_ui.js` | `84c3260fb1cb2f775356af6bbbf8e12c8f4e09b1fd0af0410c555830d8cb3945` | navigation contract |
| `tests/test_notion_publication_ui.js` | `d8602d926108869be2638a94a40c54871f35ba152d2e562bb1aba80ffc5de567` | Second Brain publication boundary |

Every implementation turn must rehash the owned files. Any drift stops the slice and requires a fresh audit.

## R2-1 — workspace-switch focus

Scenarios/actions: `UI-SHELL-001-A01`, `UI-SHELL-001-A02`.

Observed difference:

- Reference summary: workspace sidebar group receives the observed focus.
- Local: `#workspaceName`, a `role="button"` workspace switcher, receives focus.

Authoritative local source:

- `index.html:14` defines `#workspaceName` as the keyboard-operable switcher.
- `notion.js:2835` restores focus to `#workspaceName` after every workspace switch.
- `notion.js:10156-10167` implements click and Enter/Space activation.
- The real-browser journey currently requires `#workspaceName` focus after each switch.

Implementation decision after approval:

1. Re-run `test_ui_shell_001_workspace_switch_is_scoped_repeatable_and_focus_safe` and emit the complete A01/A02 captures.
2. Confirm the approved reference target is an independently focusable sidebar group, not a connected-page alias for the active switcher.
3. If the difference remains real, introduce one explicit sidebar focus anchor with an accessible name and deterministic focus-visible style. Do not remove keyboard access from the workspace switcher.
4. Change only the post-switch focus target; preserve workspace CAS, loading, failure rollback, page isolation, and Enter/Space activation.

No-go shortcut: replacing the switcher button with a non-operable group or dropping focus to `body`.

## R2-2 — page open and editor focus policy

Scenarios/actions: `UI-EDITOR-001-A03/A04`, `UI-SEARCH-001-A04`, `UI-EDITOR-002-A02`.

Observed differences:

- Reference editor actions report a page/editor group, while local actions focus either an editor textbox or `#pageView`.
- Reference reload/full-page observations do not prove that the page canvas itself is focused.
- Local slash transformation restores the transformed block editor.

Authoritative local source:

- `index.html:118` defines `#pageView` as `role="region"`, `aria-label="Page canvas"`, `tabindex="-1"`.
- `notion.js:3513` uses a boolean `focusContent` parameter for `openPage`.
- `notion.js:3557-3560` focuses `#pageView` after page render.
- `notion.js:3484-3489` opens a newly created page and then focuses/selects its title.
- `notion.js:4913-4926` is the shared `focusBlock` textbox/structured-control restoration primitive.
- `notion.js:8022-8051` transforms a block and calls `focusBlock(index, 0)`.
- `notion.js:8390-8477` focuses the slash menu search and exposes listbox/option semantics.
- `notion.js:8480-8491` supports Escape restoration to the source block.

Implementation decision after approval:

1. Do not globally flip `focusContent` or remove `focusBlock`.
2. First replace the ambiguous boolean in the proposed patch with an internal explicit focus policy (`page-canvas`, `page-editor-group`, `page-title`, `preserve`) while retaining current behavior at every call site.
3. Adopt a different policy only for the exact approved entry context. Search-result open, reload, new-page creation, history recovery, and side-peek-to-full-page are separate contexts.
4. If `page-editor-group` is adopted, add one real semantic focus anchor around the editor content; do not relabel the textbox or canvas solely to satisfy a string comparison.
5. Keep slash-menu combobox/listbox keyboard behavior, IME guards, dirty/save scheduling, undo/redo, and caret restoration unchanged.

No-go shortcuts:

- focusing `body` or an unlabelled wrapper;
- making the whole page `role="textbox"`;
- suppressing post-render focus without a deterministic replacement;
- changing slash transformation persistence to obtain a focus match.

## R3 — side peek and full-page entry context

Scenario/actions: `UI-EDITOR-005-A01`, `UI-EDITOR-005-A02`.

Observed difference:

- Reference side peek reports a title-oriented focus target and region semantics.
- Local side peek reports `SIDE-PEEK-CANVAS` and an outer dialog.
- Reference full-page observation does not establish local `#pageView` focus.

Authoritative local source:

- `index.html:219` defines `#sidePeek` as a non-modal `role="dialog"`.
- `index.html:224` defines `#sidePeekCrumb` as a non-focusable `strong` title.
- `index.html:229` defines `#sidePeekBody` as a focusable `role="region"`.
- `notion.js:3661-3687` opens the side peek and focuses `#sidePeekBody`.
- `notion.js:3693-3716` closes the side peek but does not itself restore the opener.
- `notion.js:4149-4157` closes the peek and calls `openPage`, which currently focuses `#pageView`.
- `test_notion_ui_runtime.js:250-251` statically locks the current dialog/region role pair.
- The real-browser `UI-EDITOR-005` journey locks side-body focus and full-page canvas focus.

Implementation decision after approval:

1. Confirm the adopted entry context is side peek, not center peek or full page.
2. If title focus is adopted, make `#sidePeekCrumb` a programmatic focus target with an accessible label and visible focus style; keep body editing controls reachable next.
3. Change the outer role only if the approved reference receipt unambiguously applies the role to the side-peek container. Update the Node static contract and real-browser semantics together.
4. Introduce an explicit opener reference so Escape/close returns focus deterministically; do not rely on removed-element browser fallback.
5. For side-peek-to-full-page, use the explicit page-open focus policy from R2-2 rather than another boolean.

No-go shortcut: changing only the captured alias while leaving DOM focus/role unchanged.

## Implementation order

The approved work must remain three serial cohesive slices:

1. `R2-1-WORKSPACE-FOCUS`
2. `R2-2-PAGE-EDITOR-FOCUS`
3. `R3-SIDE-PEEK-CONTEXT`

Do not combine them into a broad `notion.js` refactor. Each slice begins from a fresh hash snapshot and ends with its own evidence checkpoint before the next starts.

## Exact verification ladder

Run serially from the repository root without a custom `--basetemp`:

1. Exact changed scenario node(s) in `tests/browser/test_notion_ui1_golden_journey.py`.
2. Adjacent browser nodes:
   - workspace isolation and navigation for R2-1;
   - editor 001/002/003/004/005/006, search 001, atomic save, and IME for R2-2/R3.
3. `node code_assist_v1/tests/test_notion_ui_runtime.js`
4. `node code_assist_v1/tests/test_notion_navigation_ui.js`
5. `node code_assist_v1/tests/test_notion_publication_ui.js`
6. Python route/save/publication selections from `review_implementation_queue.v1.json`.
7. Second Brain publication/job-restart and chat characterization regressions.
8. Review-gate verifier and completion-matrix verifier.

Acceptance requires:

- complete approved reference and current local captures;
- zero exact state/keyboard/focus/a11y/semantic/error-recovery differences for the adopted actions;
- workspace leakage zero;
- Korean input and block round-trip exact;
- cancel mutation zero, approve exactly one immutable publication, replay mutation zero;
- no matrix change until the verifier accepts the sealed differential packet.

## Stop conditions

Stop without editing or roll back the in-slice transaction if any of these occurs:

- owner approval does not exactly cover the scenario set;
- owned-file hash drift;
- complete recapture shows the difference was an evidence alias/transport artifact;
- the change reduces keyboard operability or creates a focus dead end;
- a route/schema/authentication/Knowledge/Second Brain source change becomes necessary;
- any synthetic workspace, auth isolation, save/reload, publication, or Second Brain hard gate regresses;
- personal or live local data would be required.

## Current decision

The source audit makes the implementation mechanically ready, but it does not justify a behavior change. The current `group` observations may still be transport aliases for intentionally operable local targets. Owner approval plus complete capture comparison remains mandatory before the first product edit.
