# Page/item hierarchy and callout presentation

The page editor stores a flat ordered block list. `props.indent` continues to
express its existing structural hierarchy. An item editor instead uses explicit
`props.children`, while its `props.indent` is local visual spacing. Converting
item spacing directly to page ancestry hid independent siblings beneath closed
toggles and included them in parent deletion/movement.

The shared clipboard now converts actual item depth to page `indent` and local
spacing to `visual_indent` (bounded to 0–8). Reverse conversion restores local
spacing and explicit children. Existing flat payloads retain their subtree
meaning and relative gaps; selected subtrees still rebase missing ancestors.
No existing documents are rewritten. Ordinary insertion/type conversion retains
the new spacing property. Enter within an indented paragraph retains ancestry.

`blockOutline` describes ancestry, accumulated visual spacing, nearest callout
owner and closed ancestors. Rendering and search preview use it, with separate
column/tab scopes. Main-page callouts enclose actual descendants in a shared
background. Each cell remains its own `.block-row`, avoiding ancestor row event
handlers and oversized marquee hit rectangles. Uncolored child text inherits
the callout color; explicit child colors override it. Full-page database view
bypasses callout wrappers. The existing blue selection uses a borderless
gradient so explicit background colors remain visible beneath it.

Verification on 2026-09-23:

- The independent-root regression failed before the correction. Nine new
  hierarchy tests cover conversion, parent removal/movement, bounded spacing,
  legacy gaps, nested Enter, preview and column/tab visibility isolation.
- Eight Node scripts passed 49 test entries; four modified scripts passed
  syntax checks. Older core/preview scripts each count as one test entry with
  several internal assertions.
- Actual in-app-browser interaction on isolated localhost:14581 copied six
  item roots/nine nodes to a page and back to another item. Callout grouping,
  inherited/explicit colors, independent 48px indentation, toggle deletion and
  undo, individual child selection, outside-column marquee results, nested
  Enter and reload were observed. Body selection included eight visible rows
  and excluded the page title and closed child. Sustained held-pointer frames
  were not verified in this packet.
- The previous style2 item color menu was checked at actual 390×844: 20 choices,
  current checkmarks, scrollable height, screen bounds and Escape focus return.
- Read-only catalog audit confirmed exact source item preservation, equivalent
  round-trip content/properties/children/file references with independent IDs,
  unchanged cells/authority/files, one target-item write, and unchanged final
  state after Enter/undo and server shutdown. The fresh source document's
  initial database/title materialization accounts for the other sequence step.

Chrome remained absent from the tool inventory after the user's reconnection
notice. This packet does not claim a new live Notion comparison. The earlier
read-only Notion callout/color observations remain reference evidence. Existing
Chrome tab/theme/clipboard cleanup from the earlier tool reset is unresolved;
this packet's own tab, viewport, clipboard capture and server were cleaned up.

Remaining: full nested layout/action semantics, callout-specific Enter/reparent
parity beyond the tested nested paragraph, full search card presentation,
history and remaining special public/export blocks, canonical database/synced clipboard semantics,
independent files/internal links/external Notion import, all role/theme
combinations and production rollout. Full R1–R11/copy/synced scope remains active.


## Search-preview follow-up, 2026-09-23

Chrome became available in the follow-up packet. Direct Notion search inspection
confirmed neutral callout previews with grouped children. The missing grouping
and compact spacing are now implemented and browser-verified; see
`search_preview_structure_contract.md`. All test content remained unchanged.
The earlier Chrome outage above describes the hierarchy packet only.


## Read-only HTML follow-up, 2026-09-23

`html_block_structure_contract.md` records the shared renderer for collaborative
exports and ordinary public/authenticated HTML. Basic block hierarchy, styles,
native toggle descendants, table headers and nested tab switching are verified
in tests and the in-app browser. The complete specialized-block/resource scope
and published-role integration remain open; this does not claim whole export
parity or production availability.
