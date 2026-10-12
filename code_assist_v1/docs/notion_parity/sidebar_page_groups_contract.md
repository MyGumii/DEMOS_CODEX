# Reachable sidebar groups and deep page rows

The private, shared and favorites sections previously sliced their authorized
page arrays before rendering. The first ten rows could end inside a branch and
there was no way to display the remaining pages. These sections now use a shared
page-group planner and an accessible more/less control. Tree limits count root
branches and retain their full authorized descendants. An active page's branch
is additionally retained in compact mode. Flat favorites keep their saved order.
Missing parents remain roots within the supplied authorized set; malformed
cycles terminate without fetching or inventing ancestors. An older shell without
the toggle renders the complete list instead of making pages unreachable.

Expansion is temporary UI state per account, workspace, owner filter and section.
It resets on reload, does not rewrite the stored item-limit preference and is
guarded against stale scope and locked navigation. More/less keeps keyboard focus
on its button and scrolls it into the nearest visible position after layout.
Collapsed sections hide both the tree and its more/less control.

Deep rows previously used a fixed 14px indentation per level. At 190px sidebar
width the tenth level had zero title width and its icon overlapped the actions.
Indentation now scales with the displayed tree depth and available row width,
up to the existing 14px per level. Space is reserved for the title, icon and
right-hand child/options actions, with additional reserves for scope/favorites
badges. ARIA tree levels, full-title labels and hierarchy remain unchanged.

Evidence (2026-09-23):

- Seven Node test files: 26 passed, zero failed/skipped, 278.4608ms, exit 0.
  New cases cover complete branches, unsorted children, inaccessible parents,
  active branch retention, favorite order, cycles, scope/lock isolation, current
  data rerendering, focus visibility and the old-shell fallback. Main/module
  syntax checks passed. These are scoped checks, not full-app/role coverage.
- In-app browser, named synthetic fixture only: 36 pages with a ten-level branch.
  Before: ten rows and no more control; at 190px deepest title width zero.
  After: first ten root branches render 19 rows, expansion renders all 36; the
  current last page is retained when compacted (20 rows). At 190px, the deepest
  title still has 38.015625px while its actions are shown; icon, title, + and …
  do not overlap. The row menu opens for the correct deep page and Esc returns
  to its current button. The child button opens the correct parent draft and
  cancelling an empty draft creates no document (catalog remains 37).
- At 390px, all 36 rows are reachable with document width 390. An initial focus
  check exposed the control moving below the viewport; merely calling focus()
  on the already focused element did not scroll it. Explicit nearest scrolling
  resolved this; the focused button is at y630–658 in an 844px viewport. Esc
  closes mobile navigation and focuses its opener. Section collapse hides both
  controls; sidebar width restored to its original 240px, viewport reset.
- After the sidebar change, a real drag from outside the writing column across
  the page title and body selects both body blocks only; release removes the
  marquee and Esc clears selection. The existing borderless blue marquee CSS
  is unchanged. A held-pointer intermediate frame is not newly verified.
- Exact initial snapshot verification initially failed on the first-opened last
  page. Diagnosis retained separately: its existing title/blocks/hash/metadata
  remain equal, while initial CRDT replay seeds the same content and increments
  sequence 2 to 3 plus edit time. This is the unchanged finishReplay seed path
  in notion_collaboration_editor.js, not a content edit. The original snapshot
  is retained; verification explicitly permits only this one identified page's
  exact initialization delta. All other 35 pages remain exactly equal. Final
  and stopped snapshots are identical, all 36 contents are preserved, foreign
  keys pass and no document was created by the cancelled draft.
- No console warnings/errors. Temporary tab closed; server session 61875/PID27492
  exited 0, ports 14582/3737 closed. No production data/auth/settings/migration/
  feature flags were touched. Synthetic navigation preferences were exercised.

Chrome remained absent from the tool inventory after the restoration reply;
there is no new direct Notion comparison in this packet. Root-branch limits and
adaptive indentation are the local product decisions described above, not a
claim of pixel-identical Notion behavior. All original R1–R11 and approved
copy/synced remaining gates stay open as tracked in the current-state matrix.

Direct UI comparison (2026-09-26, read-only actual Notion vs isolated local app):

- The actual Notion sidebar placed the workspace header and quick tabs above
  `Private`, with Help/Trash near the bottom. The prior local shell showed
  Workspace Home, an expanded page filter, and Recents before private pages,
  moving the first page row far down. New/unconfigured workspaces now default
  to hidden Recents; Workspace Home and the page filter remain reachable from
  the workspace menu; Help/Trash sit at the bottom of the scrolling rail.
  Existing saved navigation visibility is preserved.
- The actual private page topbar used a lock and dropdown chevron. The local
  topbar used a person icon and no chevron; it now uses the lock and chevron.
  The right-side action order already aligned in the compared view. This is a
  direct visual comparison of those states, not a claim about every page type.
- The local page filter was opened from the workspace menu, focused its search
  field, and could be collapsed again. The workspace home menu action navigated
  to the local home. These operations were checked only in the isolated local
  app; no actual Notion content was changed.
- The live production app at `127.0.0.1:10322` was unavailable during this
  comparison. Remaining visual/behavioral scope includes icon quality,
  page-row +/… placement and commands, additional topbar/sidebar states,
  responsive views, and the R1–R11 matrix. The 2026-09-23 note above refers
  only to that earlier evidence packet.

Direct page-row action comparison (2026-09-26):

- In a 1280×720 viewport with a 270px sidebar, the actual Notion private page
  row occupied x=8..247, y=129..159. Its hovered `+` and `…` sat near x=207
  and x=228; clicking `…` opened a menu at x=227, y=143. The isolated local
  row was x=8..261, y=135..165, with `…` near x=242 and its menu near x=258.
  Collaborative sidebar page lists now reserve a 14px right gutter, and their
  section top spacing is 6px smaller. The row menu uses a scroll-aware anchor
  alignment. The isolated browser recheck measured row x=8..247, y=129..159
  and menu x=227, y=143. This is a match for the sampled row and viewport;
  it does not establish parity in every sidebar width or responsive state.
- The actual Notion row menu contained Favorite, Copy link, Duplicate, Rename,
  Move, Trash, Convert to wiki, New tab, Side peek, and editor/time. The local
  sampled 2414 fixture lacked Duplicate because its page-copy capability is
  disabled and it lacked `+` because page creation starts at 2415. The source
  gates those commands and does not present unsupported Convert to wiki.
  These are capability differences, not evidence that the buttons failed in
  a capable workspace.
- Clicking the actual Notion `+` immediately showed a `새 페이지` child row and
  opened a side peek with an empty title field. A later direct recheck found
  that the untouched child disappears when the peek closes, while a named child
  remains. The local `+` uses the same visible draft lifetime and commits after
  meaningful input; its canonical page URL appears only at commit. Disposable
  named reference pages used for these observations were moved to Trash; no
  permanent deletion ran.
- Focused Node checks for menu placement/sidebar shell passed (8 cases), and
  the local browser verified the final placement. Production 10322 remained
  unavailable, so no operating-profile UI claim is made.
- At a 390px viewport, the resized local sidebar kept its row and bottom
  tools visible without horizontal overflow. Resizing while its row menu was
  open initially left that menu stranded over the now-hidden sidebar; the
  sidebar accessibility sync now closes it. A new isolated browser session
  verified that the menu disappeared on the desktop-to-mobile transition.

Page-row child creation recheck (2026-09-26):

- The actual Notion title element was empty with a gray `새 페이지` placeholder.
  Both Esc and outside click removed an untouched child row. Typing a title
  made the child remain after closing. The local hover `+` now keeps a transient
  sidebar row and the same empty title/placeholder until meaningful input.
- A temporary immediate canonical-create change was reverted after that direct
  observation. The frozen creation operation and existing permission/parent
  retry behavior still apply once input triggers the POST. Title selection range
  now survives parent-to-embedded-editor focus handoff.
- The legacy row-menu tests and 2415 collaborative checks cover empty close,
  immediate and idle title entry, handoff, close/reload, and lock/membership
  retry: 9 browser cases passed together. Four related Node checks and the JS
  syntax check passed. This does not establish production or full R1–R11 parity.

Child creation presentation recheck (2026-09-26):

- Direct observation of the actual Notion row `+` at 1280×720 showed a centered,
  dimmed-background creation dialog, approximately x=153..1128 and y=72..648.
  The prior isolated local UI opened a right-hand side panel instead. The local
  creation draft now uses a 976×576 centered modal at x=152,y=72, with an
  outside-click backdrop, dialog semantics and inert background. Existing-page
  side peeks retain their previous presentation.
- The local empty `새 페이지` title starts at x=285 and aligns with the embedded
  editor title after first meaningful input; the handoff position was checked
  at 1280×800 within four pixels. The blue focus outline was removed in the
  ordinary color mode while forced-colors focus remains visible. At 390px the
  creation dialog fills the viewport. Empty outside/close dismissal does not
  create a canonical child.
- A further direct Notion check showed the draft header's share, copy-link,
  favorite and actions controls; the share popover opened even on an untouched
  draft, and closing the draft still removed its temporary child row. The
  actual bottom rail offered AI question, AI notes, database, form, template
  and more. The local draft now shows the parent icon/name breadcrumb and a
  bottom start rail instead of visible status, body placeholder and `+ block`
  guidance. Its two supported choices, AI notes and database, create those
  block types in the new child and persist them. Unsupported form/template
  choices and the draft share/link/favorite/action controls are still absent;
  these are open behavior and presentation gaps.
- The start rail disappears immediately after meaningful input, so a failed
  save exposes the existing retry control. An isolated browser verified that
  the selected AI-notes/table block survives canonical creation and reload;
  the lock and team-membership retry paths also kept the draft and recovered.
- The new starter and title-handoff browser cases passed together (3 cases),
  the empty-draft close case passed, the lock and membership retry cases passed
  (3 cases), the ordinary existing-page side-peek regression passed (5 cases),
  and six focused Node checks plus JavaScript syntax passed. No operating-
  profile page or user data was changed.

Creation-dialog toolbar recheck (2026-09-26):

- Direct Notion observation showed that copying the untouched draft's link
  reports a private link copied; closing that same untouched draft still
  removes its temporary child row. The local draft has no canonical page ID
  before meaningful input, so it cannot offer an equivalent provisional URL.
  Its share, link, favorite and actions buttons are now visible in Notion's
  order but explicitly disabled until the page exists.
- Once a child is saved, the local toolbar copies that child's canonical URL,
  toggles that child's workspace favorite, and opens that child's share surface
  via its full-page view. This last path navigates out of the modal, whereas
  Notion opens an inline share popover; the behavior is intentionally recorded
  as an unresolved fidelity gap. The existing-page side peek retains its new-
  tab and close controls. The create dialog at <=520px compacts the share label
  to an icon; a 390px browser check measured no horizontal overflow.
- Isolated browser checks showed favorite targeting the child, share opening
  with that child active, the clipboard URL ending in the child's ID, no
  canonical creation on empty dismissal, and five existing-page side-peek
  paths still passing. The actual reference page was not edited.

Creation-dialog inline sharing recheck (2026-09-26):

- Direct Notion observation found a compact `공유` popover below the page
  toolbar with invite controls, a people list, general access and link copy.
  Clicking the local saved child's `공유` now keeps the creation dialog and its
  parent page in place, opening an inline popover for the child's explicit
  workspace/page ID. Its GET, invite and revoke requests never derive their
  target from the still-active parent page.
- The local popover can add or remove direct subject, group and teamspace
  grants, choose roles and descendant inheritance, copy the child link, and
  open the existing full-page share settings. It binds asynchronous results
  to the child, workspace and signed-in actor, so closing or switching a page
  cannot display a stale share response. Esc closes only the popover.
- In isolated browser verification, a new child received and then lost a
  direct viewer grant while the parent remained active; the server share list
  and intercepted request URLs confirmed that the child alone was targeted.
  At this stage the local candidate and role selects, absence of Notion's
  publish tab, and disabled share/link controls before first save remained
  visible differences.
  The full sidebar-draft and existing-page peek browser bundle passed all 13
  cases; four related Node checks and JavaScript syntax also passed. No
  operating-profile page or user data was changed.

Default icon and topbar recheck (2026-09-26):

- Read-only direct Notion inspection of an icon-free private page and its
  icon-free child showed text-only page breadcrumbs. The first private-page
  title starts at x=288 in the 1280px viewport; the actual sidebar uses a
  rounded folded-page outline with two short lines. The workspace initial is
  a muted 20px tile rather than a large illustration.
- The local topbar previously inserted a default page icon before every crumb.
  It now inserts a crumb icon only for a page with an explicit icon value and
  aligns the first title at x=288. The default page icon has a softer rounded
  outline, fold and two lines. Existing explicit emoji, vector and uploaded
  image rendering is unchanged.
- A fresh isolated 1280×720 browser screen showed no default icon in the
  breadcrumb, the title at x=288, an 18×18 default sidebar document icon and
  no horizontal overflow. A separate 390px browser check kept the crumb
  visible and document width at exactly 390px. Two relevant Node checks and
  JS syntax passed.
  This checks one default private-page state; other icon and responsive states
  still require direct comparison. No operating-profile data was changed.

Creation-dialog share search recheck (2026-09-26):

- Direct read-only inspection of the actual Notion share popover showed a
  focused name/email/group search field beside Invite, the owner's identity,
  a separate general-access section, and Advanced/Copy-link footer. The local
  popover previously exposed three selects and descendant settings on first
  open. It now starts with a search field and registered-principal suggestions;
  the role and descendant controls appear only after a result is chosen.
- Search covers the current actor's known users, groups and teamspaces; typed
  text is escaped by DOM text nodes. Arrow keys move through suggestions,
  Enter selects, and the first Escape dismisses the suggestions while leaving
  the share popover open. The existing child-ID-scoped invite/revoke request,
  actor/workspace guard and advanced full-page link remain in place.
- An isolated browser invited and revoked a direct viewer grant on the child
  through the new search UI while its parent remained active. The browser case
  also exercised Escape on the suggestion list and Arrow/Enter selection.
  Desktop checks
  saw the popover within the creation dialog and no horizontal overflow;
  the 390px check also had document width exactly 390px. The actual Notion
  publish tab and invitations to arbitrary external email addresses remain
  unsupported by this local authority model. The owner's avatar/email may
  differ when the local user directory has no profile image or email. One
  focused sharing browser case, five existing-page peek browser cases, four
  related Node checks and JS syntax passed. No operating-profile data changed.

Creation-dialog share response-loss retry (2026-09-26):

- A share or revoke request now freezes its principal, role, descendant flag,
  epoch witnesses and operation ID before sending. On a transport/server error,
  the dialog retries that exact request once; a later click on the same action
  also reuses it if both responses were uncertain. Validation and permission
  errors are not retried. Changing the requested action or recipient starts a
  new operation.
- The isolated browser test commits both a viewer grant and its revocation in
  the canonical authority while returning a synthetic 503 for each first
  response. The browser's retry used byte-identical request bodies, and the
  final grant list changed once in each direction. One focused browser case
  and JavaScript syntax passed. This closes a response-loss retry gap for the
  existing registered-principal UI; external-email invitations and the Notion
  publish tab remain outside the current authority model.

Page context and topbar menu comparison (2026-09-26):

- Direct read-only Notion inspection showed that right-clicking a private-page
  sidebar row opens a compact page menu: favorite; link, duplicate, rename,
  move and trash; wiki conversion; new-tab and side-peek opening; then editor,
  time and word/character metadata. Its topbar `…` instead starts with font
  choices, copy/duplicate/move/trash, text width and lock controls, and later
  history/import/export and product-specific actions. Rename, new-tab and
  side-peek entries are absent from that topbar menu.
- The local row menu keeps its working commands. The local topbar menu no longer
  repeats those three row-only entries; its supported entries now follow the
  observed grouping, and `페이지 기록` is labeled `버전 기록` there. Existing
  keyboard shortcuts and row-menu commands remain available. The Notion-only
  wiki conversion, page customization, translation, analytics, follow and
  desktop-app actions, and row word/character count remain unsupported.
- A focused isolated browser case verified the topbar order/absence and the
  row-menu commands. The broader menu/outline suite exposed old tests that
  still clicked a now-hidden close button in the centered child creation
  dialog and expected three outline buttons from four seeded heading blocks.
  Those tests now use the dialog backdrop and expect all four headings. The
  previously failing three cases passed when rerun (one focused, then two
  focused); JavaScript syntax passed. No operating-profile data changed.

Empty child creation and dismissal correction (2026-09-26):

- Direct comparison corrected an earlier interpretation in this document:
  actual Notion creates a canonical child ID as soon as the sidebar row `+`
  opens its blank dialog. The copied link resolves in another tab while the
  dialog is still open. Closing the untouched dialog removes the sidebar row,
  but reloading that direct link shows the page in Trash with a restore action.
  The disappearing row alone was not evidence that creation was deferred.
- The local row `+` now submits the existing idempotent create operation on
  open with the canonical initial title `새 페이지`, while the dialog title input
  remains empty and displays `새 페이지` as its placeholder. Once the server
  returns the child ID, the child appears in navigation and the Share, Copy
  link, Favorite, and More controls become available. The child link resolves
  in a separate browser tab before any text is entered.
- Closing an empty new child waits for an in-flight create or retries an
  uncertain create response using the same operation ID. It then archives the
  canonical child without a confirmation dialog. The archive path flushes
  other open edits, skips the empty child editor, uses the existing navigation
  or collaborative lifecycle receipt, and leaves the dialog open if archive
  completion cannot be confirmed. Collaborative archive compares the child's
  document sequence with its creation sequence before deleting an untouched
  page, so a concurrent edit does not get silently discarded.
- In isolated browser verification, the new child was readable by direct URL,
  Copy link and Share worked while its title was empty, and dismissal removed
  the active child but kept it in the workspace Trash list. A second open
  delayed the create response and returned synthetic HTTP 503 after the server
  had committed it: dismissal replayed the exact creation operation once,
  then archived the single child. The creation-dialog browser set's eight
  distinct cases and four sidebar-row/menu cases passed across focused runs;
  JS and test syntax checks also passed. No operating-profile page was used.
- The parent tab does not yet mirror actual Notion's child query URL while
  the creation dialog is open. Its archived direct-page screen also needs a
  direct visual comparison with Notion's Trash notice. These remain parity
  work, along with the broader UI and database gates.

Archived direct-page view (2026-09-26):

- A direct `/page/<id>` read of an archived page previously ended with a
  generic load error. It now resolves the ID only through authenticated,
  authorized Trash listings for accessible workspaces. A match shows a
  dedicated page-level Trash notice with the saved title and a Restore button;
  an unknown or inaccessible ID keeps the ordinary load failure. The ordinary
  page editor and action buttons remain hidden until restore succeeds.
- Restore uses the existing lifecycle service and receipt path, then opens the
  recovered page without adding a duplicate history entry for the same URL.
  An isolated collaborative browser kept the child open in another tab during
  dismissal, reloaded that tab into the notice, restored it and read the
  original child URL again. A legacy-page browser case also opened a dismissed
  child's URL and saw the notice. All four related row/menu browser cases and
  the focused collaborative creation case passed; JS and Python syntax passed.
- Opening an untouched canonical child in a second tab can advance its document
  sequence even without visible edits. The dismiss path now checks whether its
  current canonical title, appearance and blocks are still blank, and retries
  a document-sequence conflict against fresh witnesses a bounded number of
  times. In the same isolated browser, a genuinely changed title in the other
  tab prevented the original blank dialog from archiving that page.
- The notice uses a generic message; Notion's actor/time wording and exact
  layout remain to be matched. The parent tab still does not change to the
  child query URL while its dialog is open.

Archived notice visual comparison (2026-09-26):

- Read-only computer-use inspection of the actual archived child URL showed a
  full-width red banner immediately below the 44px topbar, with the archive
  actor/time and Restore/Delete buttons. The archived page title remains as
  faint text at x≈424 in a 1280×720 viewport. The sidebar and breadcrumb are
  still visible, and the topbar retains Share, Copy link and More.
- The local notice now uses the red top banner and faint title at the same
  measured horizontal position. It shows the authorized archive actor and
  relative time when available, keeps the parent breadcrumb and Copy link,
  and restores through the existing lifecycle service. The archive service
  projects the already-stored `archived_by_subject_id` only in its authorized
  Trash listing; it does not infer an actor from the page owner. A focused
  collaborative browser case checked banner size/color/title placement,
  actor projection, Copy link, Restore and concurrent editing. Eight page
  archive service cases and the focused browser case passed.
- Actual Notion shows a permanent-delete command and a 30-day deletion
  statement. The local collaborative archive contract does not implement that
  deletion schedule or permanent-delete action, so the notice does not make
  either claim.

Reserved child URL comparison (2026-09-26):

- In a separate temporary, logged-in Notion tab, clicking a page-row `+`
  immediately changed the parent URL to `?p=<child-id>&showMoveTo=true`, while
  the new child appeared in the sidebar. Reload kept the blank child dialog and
  the same child ID. Escape removed `p`, kept `showMoveTo=true`, and moved the
  untouched child to recoverable Trash. The comparison tab was closed; the
  original user tab was left in place.
- A follow-up direct Notion comparison opened that exact `p` URL in a separate
  logged-in tab. It showed the same centered blank-child dialog without any
  tab-local creation history. Escape in the second tab archived the child;
  the original tab immediately displayed its Trash notice. This supersedes
  the earlier assumption that only the creating tab may archive it. The
  disposable child remains recoverable in Notion Trash.
- The local row `+` now pushes the child query URL after canonical creation.
  A same-tab reload or a fresh tab reopens the same reserved child in the
  centered dialog from an authorized canonical read; neither creates another
  child. Escape, backdrop and browser Back close the dialog, archive a still
  empty child through the existing lifecycle contract, and remove `p`.
  A query pointing to an edited or inaccessible child cannot classify it as
  a blank draft. The local flow retains `showMoveTo=true` after close,
  matching the observed Notion URL.
- A focused isolated collaborative browser case covers direct URL, reload,
  browser Back, one canonical child, Trash notice/restore, response-loss retry
  and concurrent editing. A separate case covers a fresh tab's `p` URL and
  its archive effect. Notion's broader `p` routes and truly separate devices
  remain parity checks.

Archived child while parent query remains open (2026-09-26):

- The actual Notion original tab received the other tab's archive live and
  retained its `p` URL and centered dialog. A red banner began directly below
  the dialog header at y≈116 in a 1280×720 viewport; the faint child title
  started around x≈287. The same archived dialog appeared after a fresh
  navigation to that query URL. Its 30-day message and permanent-delete button
  are outside the local collaboration lifecycle contract.
- The local dialog now resolves a missing `p` child through the authorized
  workspace Trash listing. Metadata invalidation from another tab changes a
  still-clean dialog into this notice without dropping the parent URL. Direct
  reload of an already archived `p` URL shows the same notice. The banner,
  ghost title, link-copy action and Restore button use the existing archive
  permissions and lifecycle service. A direct Notion restore check confirmed
  that Restore leaves the centered dialog and opens the child as a full page,
  matching the local behavior. The comparison child was then returned to
  recoverable Trash. Unsaved local edits are preserved instead of
  being replaced by a remote archive notice.
- A focused legacy-page browser case also opened an archived child's parent
  `p` URL in a fresh tab and found the centered Trash notice. The collaborative
  browser case checked desktop placement/color, 390px width, reload, live
  cross-tab invalidation and Restore with no browser error.

Sidebar page hover controls (direct comparison, 2026-09-27):

- At 1280 px, the signed-in Notion sidebar measured 270 px wide. Its visible
  scrollbar occupied 15 px, leaving a 255 px content pane. The private page
  row began at x=8 and was 239 px wide; the 20 px child `+` and `…` controls
  began at x=197 and x=219. Opening `…` placed the 268 px menu around x=226,
  and the row hover controls disappeared while that menu was open. The menu
  contained the working page actions plus Notion's wiki conversion action.
- The isolated local Chrome used an overlay scrollbar, so the same 270 px
  sidebar left a 269 px content pane and a 253 px page row. Its controls were
  14 px further right for that reason, but their offsets from the row's right
  edge and the menu's offset from `…` matched the observed Notion layout.
  Avoid hard-coding the 14 px difference into page-row spacing; native
  scrollbar width varies by browser and operating system.
- The actual discrepancy was that the local `+` and `…` stayed visible behind
  the open menu. The row menu now hides them while expanded; Escape returns
  focus and restores them. Isolated screenshots are
  `outputs/notion_sidebar_hover_1280.png` and
  `outputs/notion_sidebar_menu_1280.png`. The focused hover case plus four
  existing mouse/keyboard page-menu cases passed. The normal port 10322 app
  remains unavailable for an additional deployed-screen comparison.

Workspace switcher menu (direct comparison, 2026-09-27):

- In the signed-in Notion browser at 873×836, the switcher opened a compact
  284 px popover with the workspace icon/name and plan/member subtitle first.
  Upgrade, Settings, Invite and Add account were grouped above a divider;
  account email, workspace choices and New workspace followed; Logout was
  separated at the bottom. The page/sidebar remained in place while the menu
  was open.
- The local menu previously inserted a generic title, put account and
  workspace choices before Settings, and mixed navigation preferences with
  workspace creation. It now starts with workspace identity, exposes Settings
  and members plus the existing member-invite surface for owner/admin roles
  before the account list, keeps New workspace immediately after the choices,
  and groups local utilities below a divider. The popover and row sizing are
  closer to the observed menu. A focused isolated collaborative browser check
  verifies this ordering, viewport fit, invite entry and Escape focus return.
- Notion's billing, add-account and account-switch actions do not correspond
  to implemented local services. The local menu keeps its own
  workspace home, page filter, icon, sidebar and theme actions in a distinct
  group rather than implying those unavailable actions exist.

Medium-width sidebar recheck (2026-09-27):

- Read-only DOM measurement on the signed-in active private page at 873×836
  reported a 270 px sidebar, topbar origin at x=270 and page-title button at
  x=282. The local 873 px
  browser previously used a 236 px sidebar because a 980 px media rule
  replaced the resizable width, shifting the breadcrumb and every page row
  icon/control 34 px left. The rule is removed; desktop widths above the
  720 px mobile boundary keep the configured width, defaulting to 270 px.
  A first visual estimate mixed the local title text and button edges;
  measured against the actual button edge, the existing topbar padding
  already places the local title button at x=282. The isolated browser check
  covers the 873 px sidebar and breadcrumb; existing mobile focus checks
  cover the narrower overlay mode.

Left tab and switcher icon recheck (2026-09-27):

- At the same signed-in 873 px viewport, the actual Home, Chat, Meetings and
  Inbox tab boxes began at x=8, 75, 109 and 143; Search began at x=226.
  The isolated local boxes began at x=8, 72, 108, 144 and 229. Differences
  are at most 3 px, so no fixed scrollbar compensation was added to the tab
  strip. The reference menu's Settings and Invite rows use a gear and an
  envelope, while the local rows used sliders and people. Those two menu
  icons now use a compact gear/envelope pair with a 16 px stroke drawing and
  tighter text spacing. The existing invite service and permission gate are
  unchanged. A browser check verifies the rendered icons and the invite
  surface.

Page-row menu text metadata follow-up (2026-09-27):

- The signed-in Notion private-page row menu ends with the last editor/time
  and a word/character count (`8개의 단어, 31자` on the comparison page). The
  local row menu currently ends after the editor/time. A second existing
  team-page row reported `2개의 단어, 7자` in Notion.
- The initial pages had mixed title, body, and embedded content, so their
  totals alone did not establish which text Notion counted. The menus were
  closed and the original comparison tab returned to its archived page
  without changing page content.
- A controlled child `Parity count probe` with body `one two` was created under
  the comparison page, reloaded, and moved to recoverable Trash. The title is
  three space-separated words and 18 characters; the body is two words and
  seven characters. Its row menu first showed `3개의 단어, 18자`, then after reload
  briefly showed the parent's `8개의 단어, 31자`, then showed `3개의 단어, 18자` again.
  The footer can lag or carry a previous page's count; its stable value in
  this probe excludes the body. The comparison page's 31-character title is
  eight word-like segments when punctuation is split, matching its displayed
  `8개의 단어, 31자`. The local row/favorite menu now shows a title-only count
  using word segmentation and Unicode code points, keeping the current title
  in sync while it is being edited. An isolated browser case changed the
  body without changing the count, then changed the title and observed the
  expected new count. The page-row menu and activity browser suites passed
  all six cases (130.48s), and JavaScript syntax passed. Other Notion page types and
  its short-lived count cache remain unverified. The user-owned Notion tab was
  returned to its original archived URL and the parent page content was not
  edited.

2026-09-28 page-row menu semantic follow-up: The signed-in Notion comparison
page's sidebar `…` menu measured 265 px wide and exposed favorite, copy link,
duplicate, rename, move, trash, convert to wiki, new tab, and side peek as
menu items. The local row menu already uses the same width and all of those
actions except wiki conversion, for which this product has no corresponding
capability. The local page-row and database-row menu actions now expose the
`menuitem` role inside their existing `menu` surface; checkbox actions retain
`menuitemcheckbox`. Focus, ArrowDown, Escape, and the database menu flow
passed the two focused isolated browser cases, and JavaScript syntax passed.
The reference menu was closed without executing an action, leaving the source
DB unchanged. This does not establish live user-app or all-role parity.
