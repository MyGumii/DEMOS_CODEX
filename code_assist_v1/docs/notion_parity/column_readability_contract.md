# Nested column readability

Column groups respond to their local writing area's inline size, rather than
only to the browser viewport. The main editor, database-item body renderer and
synced-content renderer expose the saved column count as `data-layout-columns`.

The presentation reserves 96px per column and up to 28px between columns. Groups
stack below 220px (two columns), 344px (three), 468px (four), or 592px (five).
The existing narrow-viewport stacking rules still apply. Named CSS containers
cover the actual text regions inside columns, callouts, branches, item children
and synced children/tab panels. A container query changes only grid tracks and
spacing; it does not rewrite saved column groups, indices, counts or ownership.
Widening the writing area restores the saved arrangement automatically.

Verified locally: a nested two/three/two-column page previously produced two
zero-width inner tracks and overlapping Korean glyphs. After the change its
three-column middle group stacks while the inner pair has 112.5px per track.
Full-width display restores the middle three columns and stacks only the inner
pair that still lacks room. The final saved body retains every original column
property after a real drag, undo, redo and undo. Input, reload, margin selection
and a 390px viewport were also checked.

The production item/synced renderers were additionally rendered in a disposable
preview at 16 count/width boundaries. The expected tracks and glyph containment
were checked in the browser, including widening and restoring each case. This
preview verifies rendering, not database-item/synced persistence or all roles.
No new direct Notion comparison was possible in this packet because only the
in-app browser was available; full product parity and rollout remain separate
unfinished gates.
