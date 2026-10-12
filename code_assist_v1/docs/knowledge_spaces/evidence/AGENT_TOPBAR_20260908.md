# Topbar layout correction · 2026-09-08

The added agent connection button exposed the desktop toolbar's wrapping rule in
`static/workspace_features.css`. On the actual markup with synthetic admin
controls, the header grew from 52 px to 90 px at 1440 px viewport width.

`static/index.html` now groups the middle controls in a scrollable flex container;
both sidebar toggles stay outside it. The stylesheet keeps a single 52 px row,
lets the title and selectors shrink, and reduces spacing at narrower widths.
The stylesheet URL is versioned `20260908a` so refreshed pages load the change.

Read-only Edge verification used the real HTML, styles and account-control
construction with synthetic account/model/Wiki values. At 1920, 1600, 1440, 1366,
1280, 1120, 1024, 900, 860 and 768 px, in both light and dark themes, the toolbar
remained 52 px high, the Workspace button stayed visible, and the page did not
overflow horizontally. At 1440 px and above, all controls fit without scrolling;
below that, the middle container can scroll while the toggles stay visible.
Horizontal scrolling reveals the connection button in overflowing containers. Final
light/dark captures were visually checked. No account or knowledge data was
modified by verification.

10221 served the exact updated stylesheet and the new wrapper/versioned HTML;
its health response remained `ready`. This is a static layout correction, with
no backend changes, migrations or server restart.
