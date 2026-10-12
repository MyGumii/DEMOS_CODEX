# Canonical page settings

2026-09-15 · UI-PAGE-SETTINGS

## Problem and supported behavior

The topbar font, small-text, full-width and lock actions previously used the general page PATCH endpoint even in a collaborative workspace. Its full legacy payload was then assigned to the collaborative editor, potentially replacing the displayed body. Collaborative settings now use `PATCH /api/notion/workspaces/<workspace>/pages/<page>/settings` and apply metadata only.

- Allowed updates: font (`default`, `serif`, `mono`), boolean `small_text`, `full_width`, `show_outline`, `locked`. Unknown/empty fields and non-boolean substitutes are rejected. Existing unrelated settings are preserved.
- Current active IAM role must be `edit` or `full_access`, with exact workspace/document epochs and expected document sequence. `edit_content`, comment and view roles cannot change display settings. An archived document cannot be changed.
- A locked page accepts only `{locked:false}` as a new change. Exact receipt replay of the original lock is allowed after locking; replay still checks current identity/authority/epochs.
- The transaction updates settings, document sequence, row version, update time and the editing snapshot checksum. Body blocks, immutable content revisions, CRDT generations/frames and database/file state are not replaced.
- Operation ID/hash, audit, event and outbox use the existing authority transaction. `page.settings.updated` is replayable through the existing live gateway. No schema migration or legacy dual write is introduced.
- `can_change_settings` is carried in canonical page payloads and list metadata. Older backends that do not advertise this permission do not expose these collaborative settings actions in the updated UI.

## Client preservation and permissions

- Obtain current metadata before forming the CAS request; deduplicate a matching request and retain its exact request/operation after an uncertain failure. Read current metadata after a receipt, since replay may describe an older state.
- Merge only display/permission metadata. Never assign the returned body/title or clear the dirty flag, text-save retry, conflict or history. Preserve newer document metadata and a different active workspace/page.
- Update existing sidebar items directly; a display change must not reload a filtered list and accidentally clear the editor. Live display changes follow the same rule.
- A lock change updates editor editability; if an IME composition is active, defer rebuilding the block UI until composition end and discard that callback after navigation.
- Pending settings writes participate in the existing browser `beforeunload` protection. Immediate reload before saving completed was observed during verification; the guard is covered by runtime tests. A native confirmation-dialog timing scenario was not separately forced after the fix.
- Font/size/width actions require settings permission and an unlocked page. The unlock action remains available to the same authorized editor. Cover change/removal uses appearance permission.
- Page and row menus now omit rename/move when locked. Row shortcuts consume the unavailable edit action rather than falling through to a different open page; link copying and opening another tab remain usable. The move dialog also rechecks current client lock/move permission at entry, including drag/drop entry. Server authorization remains authoritative.
- Child/import and workspace lifecycle actions follow the current service's self-owned source/workspace-owner restriction. This corrects guest menu exposure; it does not establish Notion-equivalent guest creation/archive/copy capabilities. Canonical lifecycle behavior needs separate end-to-end verification.

## Evidence and limits

- Five new server tests cover metadata/body preservation, immutable replay, current roles, lock/unlock, revocation, validation, CAS, rollback at four commit stages, HTTP fields/permissions, and two independent processes committing one operation.
- Five existing appearance tests pass, including live replay and metadata behavior.
- `test_notion_page_settings.js` runs actual handlers for canonical/general preservation, uncertain retries, late responses/navigation, pending-write detection, IME deferral, and five role menu configurations. Related navigation, direct-link, menu, icon, side-peek and atomic-save tests pass.
- Real Chrome on the named 14534 fixture: owner width/size/font, save-completed reload, lock/unlock with title/body editability, comment and edit_content settings exclusion, edit guest width round-trip, guest trash/import removal, and live restore from another authenticated HTTP client without reload. Restored view guest opens the read-only side-peek iframe and returns to the full page.
- Final fixture view grant restored; default font/size/width and unlocked state restored explicitly. Two paragraphs, immutable revision, legacy body/settings/revision, and guest navigation preferences remain unchanged. Canonical document sequence is now 10 because metadata operations were committed; the earlier sequence-1 fixture check no longer applies.
- Remaining: all nested/DB/IME/native-dialog combinations, cross-workspace side-peek and lifecycle behavior, full Notion parity rollout. Production runtime/migrations/flags were not changed.

## Additional locked-menu evidence (2026-09-16)

Real Chrome in the fresh synthetic localhost:14540 app exercised lock, row rename/move/duplicate shortcuts, reload while locked, and unlock. Rename/move and child/icon controls disappeared while locked, read actions and unlock remained available, and the editable controls returned after unlock. Read-only canonical evidence showed sequences 3 → 4 → 5, exactly two settings receipts, unchanged title/body/parent/archive state and workspace, and no new document. Ordinary/collaborative row and page menu behavior and five role configurations are covered by the client tests.

The separate ordinary-storage localhost:14541 app also completed the real Chrome lock/row-shortcut/reload/unlock flow. Its single page and block remained unchanged except for lock metadata; rename/move/duplicate were absent from the locked row menu, and the top menu retained unlock and read tools. This ordinary run does not depend on a collaborative feature flag.

In the existing Notion comparison page, locking removed move/duplicate/formatting from the top actions menu and disabled the title; unlocking restored editing. The temporary comparison lock was removed. This does not prove every role, live permission-change combination, or Notion service feature equivalent.

## Open-menu and lock-state presentation (2026-09-16)

Fresh Notion computer-use inspection showed a lock button next to page location,
and the open actions menu updated after locking without closing. The comparison
page was restored to unlocked. Its lock popover also distinguishes personal and
all-user unlock; this application retains its existing shared document lock and
only exposes the supported page-unlock action.

The application now displays the lock beside location, keeps top-menu settings
open while saving, and updates open page/row menus on current appearance/settings
metadata. Search text, caret, scroll and focus are retained. A replaced sidebar
row is found within the original list section, so Esc returns to its new button.
Older document-sequence responses cannot restore stale actions. During search
IME composition, the input remains mounted and actions are disabled until the
pending menu refresh can run. A pending setting cannot be dispatched twice, and
its late completion cannot reopen a closed or replaced menu.

The lock indicator is visible but disabled without settings permission. Unlock
uses the existing settings service and returns keyboard focus after its anchor
disappears. Locked titles retain normal contrast, empty locked blocks omit input
prompts, and block textboxes explicitly expose `aria-readonly`. A discovered
980px breakpoint bug placed locked text in an implicit second grid column; the
locked single-column layout now keeps text aligned with the page title.

Real Chrome on fresh synthetic port 14543 first reproduced the stale open menu
while the title was already locked. After the change, remote lock/unlock preserved
the search input and updated actions. A second Chrome tab unlocked a page whose
row menu remained open in the first tab; that menu stayed attached to the original
private-page row and Esc restored its focus. Local lock and topbar unlock passed.
At 390px, title/body starts both measured x=14 and body width 362 (formerly x=240,
width 136); at 900px both started x=284. Neither had horizontal overflow.

Read-only evidence found unchanged title/body and eight settings receipts, document
sequence 3 → 11, two existing documents and final unlocked state. Separate ordinary
port 14544 passed menu persistence, topbar unlock/focus and content preservation
with one page/one block. Both browser consoles had no observed errors; fixtures
and temporary tabs were stopped. Thirteen Node scripts passed (50 runner results,
579.3266ms), including nine new menu-state cases and existing settings/IME tests.
An actual guest-grant revocation with an open menu and native Windows IME input
inside this newly refreshed search field remain unverified.

## Page outline switch (2026-09-27)

Direct computer-use comparison found Notion's `목차` switch in the top-right page
menu, initially on. Turning it off kept the menu open and updated the page's edit
time; turning it back on restored the initial switch state. The comparison page
had no headings, so its outline visibility could not be checked there. The
reference page's original switch state was restored before leaving it.

This application now stores `show_outline` in page settings, defaulting to on
when absent. It appears alongside small text and full width in the top page
menu, and the existing heading outline hides or returns without replacing body
content. Both ordinary and collaborative page settings accept the boolean; the
collaborative command retains its sequence, permission, receipt and event rules.
The isolated browser verified off, persistence after reload, on again, menu
continuity, heading navigation and responsive hiding. Its captured open-menu
view is `outputs/notion_page_outline_menu_toggle.png`. The focused canonical
settings check and the complete 11-test page-settings suite passed. Related
page-menu Node tests passed after their stale expectations and fixture stubs
were updated for the previously implemented top-menu layout and sidebar state.
Collaborative UI toggle in a second browser session and the rest of Notion's
top-menu features remain part of the full parity goal.

2026-09-28 direct topbar recheck: The signed-in reference ordinary-page `…`
menu puts the third font label as `모노` and exposes `페이지 사용자 지정` after the
small-text and full-width switches. Opening that command, without changing any
setting, displayed a 300×92 px dialog at the top right with page comments and
outline switches plus an inline-comments choice. Its inline-comments choices
are `기본` and `최소화`. The local third font label now reads `모노`; its existing
outline switch still acts directly in the top menu. Page-comment display and
inline-comment presentation are not in the ordinary page settings contract,
so the reference three-control dialog is a real remaining behavior gap rather
than a completed parity claim. The reference controls were only opened and
closed; the source page and its settings were not changed. JavaScript syntax
and the focused page-menu Node test passed after the label change.

2026-09-28 implementation follow-up: The ordinary-page top menu now opens a
`페이지 사용자 지정` panel with durable page-comment visibility and outline switches
and a `기본`/`최소화` inline-comment choice. The ordinary and collaborative settings
paths validate and save these fields without replacing the page body. Existing
page-level comments remain stored when their presentation is off; turning it on
restores the first open discussion preview for editors as well as comment-role
viewers. Open block comments now appear beside their block as a short preview in
default mode or a compact count in minimized mode, and the marker opens that
block's comments. Loading a page fetches its authorized comments for these
markers. The UI keeps the panel open during a settings write and ignores a late
completion after navigation or a new panel session. The isolated ordinary-page
browser verified both choices, off/on visibility, persistence after reload,
marker-to-thread navigation and a 390px panel without horizontal overflow;
the focused outline browser case passed. The collaborative settings suite (12
tests) and related menu/comment Node checks passed. Screenshots are under
`outputs/page-customize-20260928/`. The markers are block-level summaries,
not text-range-aligned Notion comment cards; collaborative multi-user UI and
all themes remain for the full parity review. The old comment-drawer browser
case still targets a hidden topbar button from a previous UI and was not used
as proof for the current comment entry points.

The follow-up browser audit updated that old suite to use the current
`댓글 추가` page action and inline discussion. Its long-comment case exposed a
600px overflow: the discussion's grid track grew to the unbroken comment's
intrinsic width. The page-head track now allows shrinking and the discussion,
thread and comment contents wrap inside the viewport. Showing existing page
comments to editors also exposed an unbounded top preview. It now shows at
most three lines of each comment, retains all text in the discussion, and
labels the opener `댓글 보기` when a discussion exists. The 600px screenshot is
`outputs/page-comments-20260928/comments-inline-600.png`. All four current
discussion browser cases plus the page-customization browser case pass; the
old suite's hidden-toolbar assumption is no longer a pending test gap.

The inline marker follow-up now creates one marker for each open root comment
and places it beside the line selected when that comment was created. Markers
are ordered by their measured line position even if the comments were written
in the opposite order. Activating one opens and focuses that exact thread.
An isolated browser case verified the first and third lines of a multiline
paragraph with comments created in reverse order; the existing four comment
cases and customization case still pass. When text is inserted before an
existing quote, the marker now finds that exact quote again, updates while
typing and remains aligned after save and reload. Ordinary inputs without
comments do not trigger the marker redraw. An identical quote repeated in the
same block remains ambiguous without richer saved context. Multi-user UI,
themes and direct user-app comparison are also still open. In the reference
Notion editor, selecting text and opening the comment composer highlighted the
quote and showed a temporary one-comment marker; Escape removed both without
saving a comment. This observation does not establish how Notion rebases a
saved comment after later text edits.

Canceling a selected-text comment with Escape now clears its pending selection
anchor. A browser case canceled one selected-text composer, then created a new
ordinary block comment and confirmed that the second comment had no inherited
quote. Switching the drawer to another mode also clears the abandoned anchor.
The selected-text composer now opens as a compact popover beside the quoted
line. A CSS text highlight and provisional one-comment marker appear before
sending, then disappear on Escape, outside click, or a successful send. The
saved comment keeps its exact quote. Switching to an existing comment also
clears the abandoned selection so a later comment cannot inherit it. The
ordinary page-level discussion and saved-thread drawer remain separate.
Browser checks covered the toolbar action, cancellation, send, thread switch,
390px positioning, dark theme and horizontal overflow; the existing comment,
customization and slash-menu suite passed. The reference Notion composer was
observed in its unsent state only. Saved-comment rebasing after edits and all
role combinations remain open.

New selected-text comments now store up to 48 characters immediately before
and after the quote in addition to the existing offsets and quote text. When
the same quote occurs more than once, marker placement scores the surrounding
text first and uses distance from the original offset only as a tie breaker.
The personal store preserves these bounded fields and replies inherit their
root comment's anchor; the collaboration store already stores the anchor JSON.
An isolated browser case attached a comment to the second of two identical
quotes, prepended enough text for its old numeric offset to point at the first,
and verified that the marker stayed on the second quote before and after
reload. The personal store test confirmed context and reply persistence. Old
comments without saved context retain nearest-offset placement. Identical
quotes with identical surrounding context cannot be distinguished from the
current anchor data; actual Notion's saved-comment edit behavior remains to
be compared.

The unsent reference composer was measured at roughly 472×46 px in a
1280×720 viewport, horizontally centered below the selected text. It has a
single horizontal row with an avatar, `댓글 추가` placeholder, paperclip, mention
button and circular send arrow, without a heading. The selected-text composer
now follows that arrangement and centers below the selection. An empty draft
disables the send button. Focused browser checks passed for desktop and
390×700 light/dark screens, including popup bounds and no horizontal overflow;
the nine related browser cases and JavaScript syntax check passed. This
measurement covers one reference document and the unsent state, not every role
and viewport. A longer unsent reference draft kept the same 472px width while
the composer grew to about 111px high for three lines and moved its paperclip,
mention and send controls to the lower right. The local composer now grows with
wrapped text, keeps those controls in the reference order, and returns to its
compact row when the draft shrinks. Its position is recalculated as it grows
so the popup stays in the viewport. The focused browser case passed at desktop
and 390px widths, with light and dark screenshots; the reference draft was
cleared before leaving the comparison document.
Six adjacent comment-drawer, cancellation and page-customization browser
cases passed after this layout change, in addition to the focused case.

For an unsent selected-text comment with nonempty text, the reference Notion
composer closes on Escape but keeps its one-comment marker; clicking the marker
reopens the same draft. Navigating to another page prompts with a small dialog
to return to the draft or delete it. The local selected-text composer now keeps
its quote, anchor and text after Escape or an outside click, restores the
composer from the pending marker, and prompts before changing page or opening
the page-level discussion. Explicit deletion clears the draft and selection;
an empty composer still closes without leaving a marker. The local navigation
dialog uses the reference's centered compact layout and stacked actions. Ten
related isolated browser cases passed, including page and home navigation,
discussion switching, desktop/mobile dialog bounds and existing comment
flows. Browser hard navigation also warns on a nonempty selected draft. The
reference's behavior for switching to every other comment surface, browser
history and multi-user role changes remains to be compared.
When a user selects an existing saved thread while a different selected-text
draft is pending, the local app defers the target-block change until the
discard choice. Cancel keeps the original quote and draft; discarding opens
the requested thread with an empty composer. A focused browser case verified
this transition. The reference Notion behavior for this particular switch has
not yet been observed.

Browser history is a different exit path from the sidebar. In the reference
Notion editor, an unsent selected-text comment with text did not show the
draft-discard dialog when browser Back was used, whether the composer was open
or had been closed with Escape: the previous page opened, and browser Forward
returned to the document without the pending marker or draft.
The local `popstate` handler now discards the selected-comment draft on a
cross-page history move before loading the destination, while sidebar page
navigation still prompts. Isolated browser tests cover Back with the composer
open or closed, Forward without draft restoration, and the separate sidebar
confirmation behavior. This observation was made on one personal-page route;
same-page history states and all role combinations remain unverified.

An isolated two-account collaboration check found that a remote page-role
change did not update an already-open comment composer. The server exposed the
new `view` role, but the client retained the old `edit_content` role and left
the draft's input and send button enabled. A new realtime ticket already had
the newer access epoch; the reconnect path failed to reread the page metadata.
The client now refreshes visible page metadata after a ticket's access epoch
differs from the open page, following CRDT replay when applicable. An access
change also rerenders comment actions. The browser check downgraded
`edit_content` to `view`, confirmed both input and send disabled with the
unsent text preserved, then upgraded to `comment` and confirmed they reenabled
without losing the draft. The adjacent current-tab unlock/remote-relock browser
case and JavaScript syntax check passed. This is a synthetic collaboration
fixture; direct reference Notion behavior for concurrent role changes and the
live user app remain unverified.

A second isolated collaboration case revoked the page grant entirely while a
selected-text comment draft was open. The document correctly entered its
recovery-only surface and kept the draft text, but the composer initially
remained writable because it used the stale page role. Comment input, send,
reaction, reply, edit, resolve and delete affordances now respect recovery-only
mode, and their handlers refuse a mutation while access is uncertain. Rerendering
the document had also detached the pending quote range and moved its composer
to the top of the viewport. The client now reconnects that range to the current
editor using the saved quote and surrounding context before positioning the
composer. The browser case confirmed the preserved draft, disabled input/send,
visible pending marker, connected quote range, and popup beside the marker;
four adjacent quote/marker browser cases and both collaboration role/revocation
cases passed. Regranting access without reloading was checked subsequently;
the send behavior after the selected quote is removed still needs comparison.

The quote-removal case was subsequently exercised with two isolated browser
accounts: one kept an unsent selected-text comment open while the other
replaced the paragraph with text that no longer contained the quote. The draft
and provisional marker remained, the obsolete highlight was removed, and the
composer stayed beside the marker instead of jumping to the top of the page.
The composer now falls back to the provisional marker's position when its
saved quote has no visible range. The focused collaboration case and three
adjacent selected-comment/repeated-quote cases passed. Whether Notion permits
sending a draft after its quoted text disappears, and how this product should
store that orphaned anchor, remain to be compared before changing send behavior.

After a full page-grant revocation, regranting access to `comment` did not
recover an already-open selected-comment draft. Once the page disappeared
from navigation, focus refreshes queried only listed page IDs, so the open
editor retained its old role and recovery-only state. Focus refresh now also
queries the active page when it belongs to the selected workspace. A returned
grant starts a new collaboration session only when there are no unsynced body
operations or offline-policy blocks; the comment draft and provisional marker
remain in place. The isolated browser check confirmed that input and send
reenable after regrant without a reload. A concurrent role-change check passed
when rerun alone after one intermittent timeout in a paired run. Direct Notion
multiuser comparison and the live user app are still unverified.
