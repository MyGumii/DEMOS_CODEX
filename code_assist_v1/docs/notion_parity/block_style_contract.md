# Block style presentation across page and item editors

Status: item color editing and page/item callout grouping implemented. Full R1–R11,
copy/synced fidelity, and production rollout remain in progress.

`notion_block_style.js` accepts only the existing ten named choices for
`text_color` and `background_color`. Both editors render these names through
shared theme variables; no stored CSS is inserted. Reapplying a default or
invalid value removes stale color classes and variables. Existing light colors
remain unchanged; dark variables use a separate readable palette. This is not
a claim of exact current Notion dark-theme color matching.

The item block action menu now has a color submenu with separate text/background
groups and current-selection checkmarks. Mixed selections do not falsely show a
single selected color. Changes use the existing item draft/save path, apply only
to explicitly selected IDs, and preserve descendants and all other properties.
Default resets only the chosen field. The normal history supports undo/redo.
ArrowRight opens the submenu; ArrowLeft/Escape returns to its trigger; Escape
from the main action menu restores the block handle. Menus close on loss of edit
authority, pending recovery, composition start, remote body replacement, and
disposal. No extra service, migration, or permission path was introduced.

Item callout text and children share one background container. An uncolored
child inherits the callout's text color; an explicitly colored child retains its
own color. Existing item indentation stays 24px per step. Block selection uses
the existing borderless blue overlay over the stored background. Other item
resource, text and table rendering is preserved.

Actual Notion's callout action and color submenus were inspected read-only on
2026-09-23. Text/background groups, checked yellow background, swatches, and a
callout background enclosing child paragraphs/lists were observed. Main text
was equal before/after; no reference-page content was edited.

Actual Chrome on the named localhost:14580 fixture verified initial colors,
callout children, explicit child colors, 48px indentation, single/multiple
selection color changes, undo/redo, default/undo, Escape focus, full-item reload,
and light/dark colors. The final stored item differs from its seed only in three
color properties, with IDs, content, rich text, nesting, collapsed state and file
references unchanged. Three body writes committed; the new fixture's initial
database/title materialization accounts for one additional document sequence.
Other rows, cells, authority and five files stayed unchanged. After item edits,
later theme/default-undo/read operations left the source snapshot unchanged.

The seven existing test scripts in `item-style-node-final.log` passed 49 Node
test entries (the older interaction script is one entry containing its own
assertions). Three product scripts passed syntax checks. Functional browser
checks used style1. Final style2 changes only the menu height variable/focus CSS
and asset identifiers. The follow-up hierarchy packet verified that final menu
CSS in the in-app browser, including actual 390×844 bounds and Escape focus.
Chrome remained absent after the user reported reconnection.

Limitations and next work:

- The earlier Chrome viewport attempts did not change the actual 1920px width.
  The separate in-app-browser 390px check is documented in the hierarchy packet.
- The previously observed main callout grouping and independent-indent/toggle
  failures are corrected and browser-verified in `block_hierarchy_contract.md`.
  Search-preview grouping is verified in `search_preview_structure_contract.md`;
  broader nested layout/action parity remains open.
- Recent-color shortcuts, callout icon editing, full table/header presentation,
  nested main-page semantics, all roles/themes and external import are not
  completed by this packet.
- Chrome tab/theme cleanup and original clipboard restoration were interrupted
  by the tool reset. The temporary viewport was reset before that reset. Do not
  claim restoration of the clipboard from the lost in-memory capture.


## Neutral search previews, 2026-09-23

After Chrome reconnection, direct Notion comparison showed a yellow main callout
rendered neutrally in search while retaining all children inside one background.
Our preview now matches that grouping and measured compact spacing, retaining
its existing neutral palette. Stored block colors remain unchanged. See
`search_preview_structure_contract.md` for the actual comparison and limitations.
