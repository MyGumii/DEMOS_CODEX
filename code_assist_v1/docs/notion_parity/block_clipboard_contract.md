# Structured clipboard between page and item editors

Status: mixed basic/resource block transfer is implemented and verified on the
isolated full app. This does not complete the original R1–R11/copy/synced scope.

The shared `notion_block_clipboard.js` writes version 2 of the existing custom
clipboard MIME, with an explicit flat/page or tree/item shape. Standard HTML and
plain text accompany it; the escaped HTML wrapper carries the same bounded
payload when custom clipboard types are dropped. Version 1 main-page payloads
remain readable. Clipboard data is untrusted: supported types, unique input IDs,
recursive counts/depth, UTF-8 size and prototype-related keys are checked before
mutation. Nested rich HTML passes the host sanitizer. Unsafe resource URLs and
foreign-service local file-ID collisions are rejected. A malformed structured
payload does not silently replace the selection with plain text.

Each pasted block, including closed descendants, receives a fresh ID distinct
from source and destination IDs. Layout groups and tab-controller references
are rekeyed together. Flat indentation becomes item children; item children
become visible flat page rows with relative indentation. Clipboard transport
does not rewrite ordinary strings or confer authority through source metadata.
This packet retains existing file references, whose reads still authorize the
source file. It creates no independent copied file records or new grants.

Item copy/cut includes a selected parent's subtree once. Destination insertion
respects the current nested list and replaces only a truly empty paragraph.
Caption/ordinary text paste remains native to its existing text path when no
structured data is present. Cut never removes blocks after a clipboard failure;
readonly and pending/IME guards still apply. Item disposal removes all old root
listeners so a prior row cannot react to the next row's clipboard events.
The main editor's second Ctrl+A now clears native text selection and focuses the
page editor group, allowing the following Ctrl+C to copy the selected blocks.
Simple-table plain text quotes tabs/newlines/quotes for a reversible TSV export.

Actual Notion was inspected read-only: selected text/list copy produced its
custom block MIME plus HTML and plain text. No Notion paste/cut/content change
was performed, and source text was equal before/after. Do not claim that this
proves all Notion paste/reparent/cross-workspace behavior.

Actual Chrome on localhost:14579 copied five item roots/seven total nodes into
another item and a separate main page, then copied the main page back to the
item through HTML-only transport. Closed children, rich HTML, multiline/tabbed
table cells and a loaded image survived. Cut/undo and direct Ctrl+A twice/Ctrl+C
were verified. Reload and read-only catalog inspection showed independent IDs,
equivalent content/properties/hierarchy, unchanged source item/page, other rows,
cells, grants and five existing files. The main model made its existing false
table-header defaults explicit on round-trip; comparison accounts only for
those defaults. An unsupported URL paste was rejected; the complete final
catalog snapshot stayed equal to the pre-rejection snapshot. Final automated
evidence: eight existing scripts, 59 passing checks, four syntax checks.

Remaining scope includes canonical DB structures and synced special cases,
independent attachment copying and provenance, internal rich-link/block-anchor
remapping, external Notion-format import, selection replacement/caret split
parity, all role/theme/size combinations and full rendering of retained style
properties. The shared format does not claim a full independent page copy or
completed clipboard fidelity for these shapes. Production rollout is separate.


## Hierarchy correction, 2026-09-23

The follow-up `block_hierarchy_contract.md` separates actual flat ancestry from
item-local visual indentation. Independent indented siblings no longer join a
preceding toggle during copy, hide, removal or movement. Main-page callouts now
enclose actual children and preserve nested paragraph Enter. Actual in-app-
browser round-trip and stored-preservation checks passed; 49 Node entries cover
the corrected paths. This supersedes the earlier rendering limitation only for
the verified shapes; special clipboard semantics and rollout remain open.
