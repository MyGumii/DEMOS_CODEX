# HTML block structure and styling

The shared `notion_html_blocks` renderer replaces flattened text in collaborative
HTML exports and the detached descendants in the ordinary public/HTML renderer.
Page blocks remain an immutable flat source: structural `indent` and local
`visual_indent` are distinct. Callouts own their descendants; native details own
toggle descendants, retaining closed state while allowing read-only expansion.
Independent indented roots remain outside a previous toggle.

Headings, numbered/bullet lists, checked items, quotes, code/captions, dividers and
simple-table headers now retain semantic HTML. Table zero values and escaped
markup remain visible. Named block colors use the editor's light palette, with
callout inheritance and explicit child overrides. Inline markup passes a strict
allowlist; event handlers, executable elements, arbitrary CSS and unsafe URLs
are not emitted. HTML input is balanced before it enters a block wrapper. No
resource lookup or canonical mutation is performed by this pure module.

Columns have isolated ancestry/numbering. Tabs retain all panels and active state;
nested controllers include their members. The shared click handler changes only
the nearest tab group, preserving outer selections. Browser controls affect the
generated document, not saved page content. The public renderer retains its
special database/form/media callbacks and existing authorization paths. An
authenticated file_ media reference now uses the collaboration file route.

The collaborative export still gets its current durable body and icon from
`_html_export_view`, including the existing access/epoch recheck. Its transfer
bundle is unchanged. Export resources and canonical database data are not
silently fetched through the new renderer: standalone media bundles, complete DB
views, internal links, synced resolution and other special types remain work.

Evidence on 2026-09-23:

- Chrome directly inspected the existing Notion teamspace home callout and its
  nested paragraph/list background. Padding was 12px; original body text was
  identical afterward. Export settings expose PDF, HTML and Markdown/CSV. The
  dialog was canceled. No Notion file was downloaded, so no generated-file or
  pixel-exact export parity is claimed.
- Eight new Python tests plus related current-body/icon/export, public/form/tab,
  and HTTP boundary regressions passed: four files, 60 tests, 75.69 seconds. The
  initial seven-test variant and existing suite passed 59 tests. The later
  additions cover nested-tab ownership and collaborative image URL routing.
- A tool refresh removed Chrome from inventory. Verification continued in the
  in-app browser on the resumed named synthetic fixture localhost:14581. A
  fixture-only adapter displayed the actual authorized export-route response
  inline; its HTML bytes were unchanged, only download disposition was removed.
  A second adapter rendered the same authorized snapshot through the public
  renderer with no public link or permission change. A third exercised a static
  nested-tab/table shape using that same renderer. No production route was added.
- Observed: yellow/purple callout with two children and red override, independent
  48px gap, closed toggle then visible child after click, real file image loaded
  through its authorized route, outer/inner tab switching, and 390px layout
  without document overflow. Native viewport was restored and own tab closed.
  Browser console warnings/errors were empty. Dark/all-role coverage was not
  repeated here.
- Entire before/final/stopped snapshots match the prior packet: source sequence
  13, source DB block, nine destination blocks, seven cells, three rows, two row
  bodies, memberships, source bindings, five files and five upload sessions.
  Foreign-key checks passed; three tracked fixture HTML GETs returned 200 and no
  tracked database command occurred. Server session 26364/PID22508 exited 0 and
  ports 14581/2127 were absent. Clipboard and theme were untouched.

Full original R1–R11 and copy/synced scope remains active. Remaining HTML work
includes complete specialized blocks/DB/attachments, historical rendering,
published-role integration and actual Notion-export comparison. No production
migration, flag transition, public exposure or rollout occurred.


## Canonical database follow-up, 2026-09-23

`html_database_export_contract.md` now records current owned/linked database
content, local filters/sorts/properties, six static view shapes and item bodies.
It includes final source/permission rechecks and read-only browser evidence.
Remaining specialized media, complete view fidelity and public/history scope
above are still open.
