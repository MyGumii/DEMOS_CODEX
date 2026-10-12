# Icon image input and follow-up item writes

The shared `NotionIcons` upload tab has a compact empty state, an image input,
image or public image URL clipboard paste, optional decoded preview, and explicit Cancel/Save.
Opening the tab or pasting does not write. Preview is hidden until decoding
succeeds. The emoji attribution remains available on the catalog tabs and is
hidden on upload. PNG/JPEG/WebP and the existing 2 MiB boundary remain unchanged.

Paste is handled only inside an upload-enabled picker on its upload tab.
Image data uses the same validation, preview, upload, appearance save and host
authorization as file selection. Plain HTTP/HTTPS image links use the optional
host `loadUrl` adapter, then the same file pipeline. Other text is ignored;
clipboard image bytes take precedence over a simultaneous text link.
A save in progress cannot accept a replacement paste. Selection
generations prevent old decodes from replacing a newer selection, a cancelled
selection or a disposed picker. Preview URLs are revoked. A cancelled native
chooser retains the existing selection; clearing its input value permits
reselecting the same file. A successful file upload is reused if only its
subsequent appearance save fails within the current picker.

## Public image links

`POST /api/notion/icon-image-preview` accepts exactly one `url` field in bounded,
strict JSON. Existing host authentication and CSRF protections apply, with
same-origin validation and an actor recheck before returning binary image bytes.
The response is private/no-store/nosniff. This endpoint creates no file, receipt,
document or appearance record. Only the picker's explicit Save uses existing
page, workspace or database-item mutation services and their authorization.

The importer permits HTTP/HTTPS without URL credentials. Every DNS result must
be public, including all redirect targets. It rejects private/link-local,
multicast/reserved and IPv6 translation addresses, connects only the checked
numeric address, and retains default TLS certificate verification and hostname
SNI. It forwards no user cookies, authorization or incoming headers. Redirects
are capped at three; actual bytes at 2 MiB; decoding at a single PNG/JPEG/WebP
frame and 16 megapixels. Compressed HTTP responses and MIME mismatches fail.
Four bounded workers and a 15-second caller deadline limit work. A resolver
that outlives the deadline retains its worker slot until it returns, rather
than admitting an unlimited queue. No database transaction spans network work.

The UI aborts an older request on replacement, Cancel or disposal and checks
selection generations before showing its result. Late successes and failures
cannot change the next selection or closed picker. Request cancellation does
not promise instantaneous cancellation of an already-running server resolver;
the server's deadline and bounded worker pool still apply.

## Upload receipt and database appearance

A collaborative file finalize advances document sequence without changing the
body. Previously `uploadPageIcon` returned only the file ID. Reloading database
rows did not refresh that sequence, so an immediate `database.row.appearance.set`
used the pre-upload sequence and received `document_sequence_conflict`.

`uploadPageIcon` now requires a matching file-authority contract, document,
file ID, active state and positive safe-integer sequence. For the same current
page-data object and actor/workspace scope, it retains a monotonic
`pageData.fileUploadSequence`. `currentDatabaseDocumentSequence` includes this
write witness. This does not advance the rendered page snapshot's sequence,
the live collaboration durable cursor, or the body; unread collaboration body
events must remain eligible for replay. Replacement page data owns a different
witness. Late responses cannot assign a witness to another page or workspace.
The server still checks the document/row versions and current authorization.
Existing pending appearance requests remain immutable and are not silently
rebased or discarded.

## Evidence and remaining coverage

2026-09-23 actual Chrome comparison observed Notion's image upload control,
image/link paste hint, Cancel and Save. No Notion icon or document was changed.
The independent synthetic app on port 14574 verified clipboard image preview,
Cancel without save, page/owned-row/workspace image storage, reload rendering,
and a 390px upload panel with no horizontal overflow. It first reproduced the
row sequence conflict (9 versus 10), then accepted the fixed request at 11→12.
The retained first failed attempt's uploaded file was not deleted. Canonical
body, cells, row bodies, memberships and source bindings were unchanged.

`tests/test_notion_icon_upload.js` covers clipboard extraction, explicit save,
decode ordering/cancel/failure, same-file reselection, pending/disposed scopes,
retry reuse, matched receipts and separate write witnesses. The combined
related Node run passed 47 checks across eight scripts. Final browser code
uses main/icon JS asset `20260923-ui4-icon-upload2`.

Native file-chooser completion remains unverified: this run's documented
chooser wait timed out before `setFiles`; it did not establish whether the
previous Chrome file-URL access limitation changed. Native Windows clipboard
interaction was not separately exercised; the real browser paste event was
driven using its documented clipboard API and keyboard input. The previous
clipboard and viewport were restored. At that point URL paste/import, all role/theme/size
combinations, the full icon catalog and final production rollout remain part
of the full R1–R11 goal. The shared picker is implemented for its existing
hosts, but this packet's live workspace coverage is collaborative, not every
legacy/personal settings path.

### Image-link follow-up, 2026-09-23

Actual Notion was given the public Python logo URL in its upload tab. It showed
two preview tiles, a workspace emoji-library option, Back and Save. Back/Cancel
left the existing house icon unchanged. Our shared picker now accepts the link
for its four hosts. Chrome on the named disposable port 14575 directly verified
decoded preview, Cancel without mutation, a private-address error and retry,
page/owned-row/collaborative-workspace saves, all three images after reload,
and a 390px preview without horizontal overflow. Actual file selection and
native Windows clipboard behavior were not exercised in this follow-up.

The first live fetch found a Windows closed-socket error after the final HTTP
Content-Length chunk. The importer now observes `HTTPResponse.isclosed()` before
adjusting the next read timeout; an actual socket-pair/HTTPResponse regression
test covers this. Final tests: 51 Python and 53 Node checks. Full-host requests
also rejected anonymous access (401), missing CSRF (403), foreign origin (403)
and private image destinations (400). Cancel/error left the entire baseline
snapshot unchanged; successful saves changed exactly two page file records and
one workspace image, with body/cells/row content/authority preserved.

At the end of the link-import packet, theme tiles, square crop/presentation and
optional shared emoji library remained visible differences. The
production app and its flags/migrations were not changed. This evidence closes
the link-import subtask, not the full R1–R11 verification matrix.

### Preview and saved image presentation follow-up, 2026-09-23

An additional actual Notion inspection measured two 80px previews: white and
black backgrounds, 6px padding, 8px corners, centered `object-fit: cover`.
Back returned to the upload input without applying the selection. On the
existing empty UI comparison page, a temporary public logo icon was saved to
measure its final rendering: a 140px button, 124.32px centered image, 4px corners,
and centered cover fit both in the page and sidebar. The icon was then removed;
the page's original icon-free view and visible body were restored. This test
did edit that comparison page's metadata/history; the teamspace home was not
saved or changed.

The shared picker now has separate input and preview stages, two background
samples, Back and a blue enabled Save button. Back releases the preview and
cached uploaded value, invalidates pending work, and returns keyboard focus to
the file input without saving or closing. Focus moves from input to Back when
decoding finishes, but not if the user has moved to another tab/control.
Errors remain visible; an ordinary successful preview adds no filename footer.

Uploaded images use centered cover fit across shared icon surfaces, including
workspace avatars, search and rename previews. Main page uploaded images use
the measured larger size; emoji/vector dimensions are unchanged. Authenticated,
public and embedded HTML icon renderers use the same raster fit and dimensions.
Only display styling changes: source image dimensions, transparency and pixels
are retained. Browser checks covered a transparent 240×80 test image, page/row/
workspace saves and reloads, keyboard Back/Esc, and a 390px dark preview with no
horizontal overflow. Other role/size combinations and the shared emoji-library
option remain unverified or unimplemented.

The deeper test root also exposed an existing Windows chunk-upload failure:
the original operation ID repeated in the temporary filename made the full path
260 characters. The temporary leaf now uses a 24-hex SHA-256 prefix of that ID;
the complete operation ID remains in the database retry binding. Session/final
chunk paths and transaction/authorization contracts are unchanged. The failed
browser upload resumed the same session after the fix. A maximal 200-character
operation test verifies exact retry, stored binding, cleanup and final bytes;
the existing four file-authority integration tests also pass. This is not a
claim of arbitrary Windows long-path support.

Final validation for this follow-up: 57 related Node checks, 10 icon/HTML-export
Python checks and 5 file-authority Python checks. Read-only before/after audits
preserved all body/cell/authority fields and original image pixels. Production
rollout remains outside this verified isolated packet.

### Emoji picker density and recent selections, 2026-09-27

Direct comparison with the actual Notion emoji picker found a 408px popup,
12 emoji columns, and a recent section above the regular categories. The shared
picker now uses 12 columns at desktop width and keeps its 8-column narrow-screen
layout. An explicitly saved catalog emoji or vector appears first in a
user-scoped recent list across page, workspace, database item, and selected-row
pickers. Removing an icon, a failed save, and uploaded/private file icons do not
enter this list. The list is bounded to 12 values, and missing or invalid catalog
entries are dropped when read. Unavailable browser storage does not block saves.
Changing the vector color palette while the recent group is open recolors its
choices and keeps the active icon selectable.

An isolated Chrome page picker check verified 12 rendered columns, a saved emoji
appearing in the recent group after reopening, and a recent vector responding to
the color palette. The visible result was captured
as `outputs/notion_icon_picker_recent_12_columns.png`. Two selected-row Chrome
cases verified that a lost response does not mark a choice recent before its
successful retry. Three recent-list Node tests, nine keyboard geometry tests,
the existing icon-value and workspace-icon tests, and 21 upload tests passed.
The normal 10322 development server was unavailable, so this visual check used
the isolated app. The local catalog still has 80 emojis and 40 vectors; broader
Notion catalog and category parity remains part of the full UI goal.

### Observed face category, 2026-09-27

The signed-in Notion picker was inspected again on a teamspace home page, without
saving or removing its house icon. Its Korean `사람` category starts with 72
observed faces in a fixed order, from 😀 through 🧐. The local first category
previously had only 16 mixed emojis, so the next category appeared after two
rows despite both pickers using 12 columns. `scripts/sync_notion_emoji_faces.py`
records those 72 observed values and Korean labels, adds pinned Twemoji v14.0.2
SVG assets, and is idempotent. The other seven existing people-related choices
follow that observed run, keeping previous saved Unicode icon values valid. The
catalog now has 143 emojis and 40 original vector icons. The plain-text random
symbol was replaced with a shuffle SVG.

The isolated Chrome picker displayed the first 12 in observed order, 79 entries
in the `사람` group, all first-row images loaded, the existing recent group still
worked, and a vector's recent color still changed. Its full screenshot is
`outputs/notion_icon_picker_faces_12_columns.png`; the focused browser case and
icon asset/value Node check passed. The actual Notion vector tab had a much
longer alphabetical grid, beginning with `123`, Vitruvian man, activity,
plus-circle and airplane, while the local vector catalog still has 40 entries.
At that point, the local picker was left-aligned to the source page icon rather
than centered beneath it. Broader category/search parity remains open.
The actual Notion picker was closed and its comparison tab returned to the
original relation-source page; no Notion icon or body was changed.

### Page icon popup position, 2026-09-27

The actual Notion teamspace home at a 1280px viewport placed its 408px picker
at x=250.5, exactly centered on its 78px icon button at x=415.5, and 4px below
the button's bottom edge. This was measured from the rendered DOM after opening
the existing house icon, then closing without saving and returning to the
original comparison page. The local shared placement utility now supports
`center-below`; the page icon picker requests it, while unrelated anchored
dialogs retain their placement. The viewport clamp and above/below fallback
remain active. The isolated Chrome page picker checked center alignment,
4px spacing, and containment at 390px, and the placement unit check includes
the actual Notion coordinates and edge cases. Both checks passed. The rendered
result is `outputs/notion_icon_picker_centered.png`. Other icon picker hosts
and the remaining catalog breadth were not made equivalent by this change.

### Search layout and remaining picker controls, 2026-09-27

On the signed-in Notion teamspace home, searching the emoji picker for `집`
showed a flat grid of 20 matching icons, with no section headings or footer
categories. A clear control inside the field restored the original recent and
category sections. A query with no matches showed left-aligned `결과 없음` and
an `이모지 추가` action. The emoji tab also exposed a palm button whose popup
listed six skin tones from default through dark. The inspection changed no
page icon or skin-tone preference; the picker was closed and the original
comparison page restored.

The local shared picker now gives the search field a magnifier, searches the
whole current tab even after a category was selected, shows matches in one
grid without section labels, hides the footer categories while searching,
keeps the popup height stable, and restores the catalog when the query clears.
The no-result copy is `결과 없음`; the browser's search-field clear control
triggers the same input path. The isolated Chrome page case checked a `집`
result, loaded image, flat grid, hidden footer, unchanged popup height,
no-result copy, query clear and the existing recent/color flow. The icon-value
Node check and nine keyboard checks also passed. The result screenshot is
`outputs/notion_icon_search_flat.png`.

The initial local catalog returned only the house emoji for `집`. Subsequent
work below added the complete observed set. Custom-emoji creation remains a
separate workspace feature rather than another page-icon upload.

### Skin-tone selector, 2026-09-27

The observed Notion palm control opens a horizontal six-choice menu: default
plus five skin tones. The local shared picker now offers those six choices,
with user-scoped preference storage. Changing the preference recolors the
five currently bundled tone-capable gestures (✋ 👋 💪 👍 🙏) in the catalog;
it does not edit the page icon until an icon is selected. Their 25 tone SVGs
come from the pinned Twemoji v14.0.2 source. Hidden variant entries remain
valid saved/recent icon values but do not clutter the base catalog. The
asset-sync script is idempotent (catalog SHA-256 unchanged on rerun).

The isolated Chrome page check exercised the six options, Escape and keyboard
reopening, preference persistence, a saved 👋🏽 icon after reload, recent-icon
history, and menu containment at 390px. It waited for the six menu images and
the first row of emoji images to load before capturing
`outputs/notion_icon_skin_tones.png`; the saved page icon also loaded before and
after reload. One browser case, 10 keyboard cases, the catalog Node check,
and seven Python icon checks passed. The catalog still has far fewer emojis
than Notion, so tone support applies only to its five bundled gestures. The
regular app on port 10322 was unavailable; the visual check used the isolated
test browser.

### Korean house search and custom-emoji form, 2026-09-27

The signed-in Notion picker returned these 20 emoji for `집`, in this order:
🏠 🛖 🏘️ 🪴 🪹 🏚️ 🦞 😣 🐈 🫳 🙃 👌 🤌 🤏 🐚 🍵 🍺 🏡 🏮 🇪🇬. The matches
include search aliases as well as literal house names. The local catalog now
bundles the 17 previously missing Twemoji assets and records this observed
Korean search ordering. Its picker showed all 20 in the same order and loaded
every image in the isolated Chrome check. The existing skin-tone and recent
flows also passed in that browser run (two cases, 35.42s); catalog Node,
keyboard Node (10 cases), Python export (seven cases), and JavaScript syntax
checks passed. The asset-sync script reproduced the same catalog SHA-256 on
rerun. The local screenshot is `outputs/notion_icon_search_flat.png`.

The no-result `이모지 추가` action in Notion opens a nested form headed
`사용자 지정 이모지 추가`, which says anyone in the workspace may use the emoji.
It has an image-upload control, an emoji-name field, Cancel, and a disabled
Save button until valid input. We cancelled without uploading, naming, or
saving and returned to the original comparison page. At this comparison stage,
the local picker lacked a workspace-scoped emoji registry and form; the later
2425 implementation below adds them. The overall emoji and vector catalogs
are still smaller than Notion's, and the full R1–R11 parity gate remains open.

### Empty upload tab, 2026-09-27

The signed-in Notion teamspace page showed an approximately 408×191px upload
popup. Its empty state has a single image-upload control, one line explaining
Ctrl+V for images or links, then Cancel and disabled Save. The local empty
state had an additional always-visible file-format line, making the popup
about 16px taller. The local popup now keeps the PNG/JPEG/WebP and 2 MiB
limits in the upload control's title and validation, uses the observed hint
wording, and renders at 188–196px in the isolated Chrome layout check.
`outputs/notion_icon_upload_empty.png` records that state. The same browser
case and all 21 upload/clipboard/retry Node cases passed; the older upload
test harness was updated to include the already-existing skin-menu state.
No file was uploaded to Notion; Cancel closed its picker and the original
comparison page was restored.

The current `workspace_icon_images` table stores one current avatar image per
workspace and replaces it on update. A reusable custom-emoji library cannot
be stored there without changing its identity, visibility, and lifecycle
contract. It needs a separate workspace-scoped registry and authorized image
serving before the observed `이모지 추가` form can be made functional locally.

### Custom-emoji registry foundation, 2026-09-27

An explicit, guarded 2424→2425 migration now adds `workspace_custom_emojis`
without changing the single-avatar `workspace_icon_images` table or any
existing page icon. The registry has workspace/name uniqueness, an exact raster
type and 2 MiB bound, a content digest, uploader identity, and a workspace
foreign key. Existing authorities accept the exact new marker and retain their
older capabilities. An isolated migration/reopen test passed; no live catalog
was migrated.

The new service creates a reusable emoji with an operation receipt, exact
workspace membership, name normalization, bounded image validation, and a
250-item per-workspace limit. List and image reads require workspace
visibility. The multipart upload/list and authenticated image routes are
available only on the exact 2425 schema. `ne:we_…` page icon values are
checked against that page's workspace inside the appearance transaction;
the browser icon renderer recognizes the reference. An isolated service/HTTP
test verified replay, duplicate-name refusal, access denial, preserving the
workspace avatar, using the emoji on a page, and authorized image delivery.

The page icon picker now loads the workspace library, offers `이모지 추가` for an
empty emoji search, and opens a nested name/image form. On creation it shows
the new reusable emoji in the workspace group for explicit selection. The
existing icon keyboard/upload suite and a focused create/cancel form test pass.
An isolated Chrome journey passed the full no-result → add → select → reload
path on the 2425 schema, with no page script errors. Same-workspace page copy
retains the shared reference, and authorized HTML export embeds the raster
bytes rather than an authenticated URL. Cross-workspace page transfer is not
offered by the current page-copy/move services. Other page-icon hosts and the
overall R1–R11 parity gate remain open.

The database-row appearance path now enforces the same-workspace registry
check for `ne:we_…`, including the multi-row icon command. A valid shared
emoji works for the row command; a missing or foreign ID is refused in the
same transaction. The page, owned database-item, and selected-row pickers
now share the workspace custom-emoji catalog and creation host. An isolated
Chrome journey applied one custom emoji to a real owned database item, and a
separate selected-row browser journey applied it to two rows atomically.

The adjacent legacy row-appearance/icon-batch run initially had 12 passes and
one stale gate assertion: the 2419 fixture expected internal
`page_copy._owner` to reject, even though that staged service checks ownership
at 2419 while the public page-copy capability remains disabled. The assertion
now checks the actual internal ownership boundary and both the owning test and
its row-appearance successor test pass. This does not enable the public copy
feature.
