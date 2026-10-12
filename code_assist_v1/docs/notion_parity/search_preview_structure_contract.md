# Search preview callout structure

The 2026-09-23 restored Chrome connection allowed a direct, read-only comparison
with the existing Notion teamspace home. Its main callout had a yellow background;
the search preview presented the same callout with a neutral background and kept
the paragraph and three list children inside one container. Computed preview
metrics were 12px padding, 6px icon gap, 6px radius, 13px type and 18px line height.
Main-page text before and after inspection was identical. No external content,
icon, setting or clipboard change was performed in this packet.

Therefore the preview intentionally retains the existing neutral theme palette,
rather than applying the editor's stored block-level colors. Existing sanitized
inline rich text remains supported. Neutral presentation is not evidence that
stored colors were lost, and exact neutral RGB parity is not claimed.

`NotionDocumentPreview` first collects passive node descriptors using the shared
`blockOutline`, then serializes them. Each callout owns its visible descendants;
nested callouts retain their own container. Visual indentation is relative to
the owning callout, without adding the ancestry gap twice. Column/tab scopes and
closed ancestors retain their existing visibility rules. Headers and children
share the body beside an 18px icon. No new editable controls or data writes exist.

Verification:

- The new grouping regression failed before implementation. Six new tests cover
  ownership, neutral colors, nested visual offsets, hidden descendants/tabs,
  column isolation, escaping and passive empty callouts. Four Node files passed
  17 entries (older preview/search scripts contain internal assertions).
- Actual Chrome on the isolated resumed localhost:14581 fixture showed the
  previous separated rows and then one neutral group containing two children.
  Eight visible rows include an independent indented root; the closed child is
  absent and the image loads. Final assets use preview-structure1.
- Hide/show preserves the group; Escape returns focus to the search button;
  clicking the preview opens the correct document. Console warnings/errors were
  empty. This packet did not repeat narrow viewport, dark theme or all-role tests.
- Before/final/post-shutdown snapshots are exactly equal, including page bodies,
  source sequence 13, seven cells, three rows, two row bodies, memberships,
  bindings, five files and five upload sessions. Five tracked preview GETs
  returned 200 and no tracked database command occurred. Foreign-key checks pass.
- Own reference/app tabs closed. Clipboard, viewport and theme untouched. Named
  test server exited 0, with its process and ports 14581/3863 absent.

Remaining: complete search card/header/icon/spacing comparison, history/public/
export rendering, complex nested layout/action semantics, all roles/themes and
production rollout. This correction does not establish whole-app Notion parity
or complete the original R1–R11 and copy/synced scope.
