# Current database content in collaborative HTML exports

The HTML export now prepares database models from the canonical database tables
for blocks that exist in the current durable page body. It does not read stale
row caches from block props, resurrect a removed block or modify the transfer
bundle. Current cell values, calculated values, authorized relation labels and
item bodies pass through the existing authority/materializer paths.

Linked database instances use their existing source/instance authorization and
current local view descriptors. Source denial emits a neutral unavailable label,
without cells, source titles or identifiers. The preparation tracks document,
workspace, access and generation stamps for the main page, linked sources and
authorized relation targets. Linked computed values and relation labels are
materialized in the same read transaction as their target witnesses. All sources
are reauthorized before and after rendering. A source edit/revocation discards the
prepared HTML and rebuilds the entire result; three changing attempts return a
retryable authority error. A main-page denial still denies the export.

The pure HTML renderer applies the existing filter operators, natural numeric
sort, visible-property settings and property order. Title properties remain
visible and numeric zero is retained. It has no fixed 100-row truncation. Static
table/grouped table, board, list, gallery, month calendar and date-range timeline
shapes are available. Calendar and timeline choose their own configured date
property. Item titles with bodies open native details containing the shared
hierarchical renderer; inputs are copied and escaped without mutation.

Evidence, 2026-09-23:

- Final four-file selection: 27 tests passed in 125.65s, process exit 0. This
  includes current durable HTML/icon/compaction/access tests, shared block tests,
  the export HTTP boundary and 15 database-export tests. Tests cover latest cells,
  formula 84, relation labels, unchanged transfer/SQL dump, absent current block,
  linked local filters, more than 100 rows, escaping, hidden properties, item
  hierarchy, distinct date properties and edits/revocations during rendering or
  between linked read and witness validation. The first run had three missing
  test-fixture setup errors; importing the existing fixture dependency chain
  resolved them. The intermediate suite passed 24 tests.
- Resumed the named synthetic hierarchy fixture at localhost:14581 without
  reseeding or migrating. The fixture-only export adapter invokes the actual
  authenticated export route and removes only download disposition. A second
  adapter projects the same authorized canonical data into six view types;
  those overrides are in-memory and do not change saved views.
- In-app browser: actual export has two live rows and three headers; expanding
  an item shows the colored heading/callout, nested text and closed toggle. Six
  view variants each contain two items; the month has 35 cells and timeline two
  lanes. At 390px, document width is 375px; calendar/timeline scroll internally.
  Console warnings/errors are empty. Screenshots and DOM observations retained.
  The final date-property and read/revocation fixes were made after this browser
  pass and validated in the final automated suite; no browser rerun is claimed.
- Whole before/final/stopped snapshots equal the preceding packet: source
  sequence 13, one source database block, nine destination blocks, seven cells,
  three stored rows, two item bodies, three memberships and five files/uploads.
  Foreign keys pass. Two tracked HTML GETs returned 200; no tracked database
  command ran. Server session 29074/PID19292 exited 0 and ports 14581/3085 closed.
- Chrome remained absent from the fresh tool inventory despite the user's
  restoration reply. No new actual Notion comparison or generated-file parity
  claim is made. A startup-before-ready navigation created an error tab; the
  browser URL policy blocked acquiring it for closure. No policy workaround was
  attempted. A fresh legitimate localhost tab succeeded once the server was
  ready; that verified tab was closed and temporary viewport reset. Clipboard,
  theme, production app/auth/data, migrations and feature flags were untouched.

Limits: these are static projections, not complete interactive/pixel parity with
Notion. Calendar events use their start day, timeline uses data extent rather than
saved anchor/scale, gallery covers/item icons and person display names are not yet
resolved. Locale-specific sort equivalence is unverified. Chart/dashboard/form/
feed/map use table fallback. Standalone media/file bundles, nested special blocks,
internal links, synced resolution, history/public-role integration and an actual
Notion-generated-file comparison remain open. No production rollout or full
R1–R11/copy/synced completion is claimed.

2026-09-25 addendum: A direct Notion download from the existing comparison database showed that HTML export is a ZIP containing one HTML and one CSV file; Markdown & CSV export is a ZIP containing current-view CSV and `_all.csv`. The earlier limit above that no Notion-generated file was compared was accurate for 2026-09-23 but is superseded for this database. The app now packages these two database export formats from one authorized snapshot while retaining the direct HTML and CSV API responses. Current-view filters, sort and property visibility apply to the view file; the all-row file ignores them. This does not verify PDF, child pages, comments, file exclusion, exact HTML styling or filtered Notion base-view semantics. Synthetic regressions: 23 collaborative export/search tests, 4 personal route tests, 4 browser sidebar/database tests, plus Node navigation and JavaScript syntax checks.
