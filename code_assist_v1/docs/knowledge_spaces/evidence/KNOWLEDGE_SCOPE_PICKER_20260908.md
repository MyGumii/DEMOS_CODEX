# Knowledge scope picker position · 2026-09-08

The scope selector retains its existing select/options and controller. Scoped
CSS opts supporting browsers into the customizable picker, with an explicit
named anchor on `#knowledgeScopeSelect`. The picker opens 4 px below that field,
with its left edge aligned to the field, outside the toolbar's overflow clipping.
Its width accommodates scope names, its height is bounded, and the closed field
preserves its arrow and ellipsis. Other selectors are unchanged.

The picker CSS is in `static/workspace_features.css`. In supporting browsers,
`static/knowledge_scope.js` retains a native select button/selectedcontent node
when rebuilding options, so long labels truncate before the arrow. Selection
events, authorization and controller transitions are unchanged. `static/index.html`
requests CSS version `20260908b` and selector JS version `20260908a`. Implementation reference:
[Chrome customizable select documentation](https://developer.chrome.com/blog/a-customizable-select).
The CSS is feature-guarded: unsupported browsers retain their native selector.

Actual Edge verification used the existing HTML, stylesheet and scope controller
with isolated synthetic management adapters. Twenty open-menu checks combined
1920/1440/1280/900/768 px widths, light/dark themes, and first/third selections.
Every menu began 4 px below the field, all five options stayed within the viewport,
and the topbar remained 52 px high. Mouse/keyboard selection, Escape, outside-click
dismissal and disabled Team/Responsibility options passed. Screenshots were
visually checked. Existing `test_scope_selection_ui.py`: **9 passed in 3.85 s**.
The earlier 20-case topbar layout/scrolling check also passed with the final CSS.

10221 served the exact final CSS and updated index version; health remained
`ready`. This change made no backend, account or canonical knowledge mutations.
