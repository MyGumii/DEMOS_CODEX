# Database search input and view-dialog refresh

Database search is temporary presentation state scoped to the current account,
workspace, page load, block instance and database source. It is not written into
document props, shared through document projections, or restored from historical
`props.query`. Clearing a search stays empty even if a projection contains an old
query. Changing views retains the local query; a fresh page load starts empty.

Search debounce updates database results while retaining the actual header and
toolbar DOM. It must not replace/refocus the search input: doing so terminates
native input targets and destroys composition/undo state. Result event binding
is separate from header binding, so new results remain interactive without
duplicating toolbar listeners. Pending updates include every changed database;
typing in a second database cannot cancel the first database's result update.

An unrelated main-block render can wait while an owned search/cell is focused.
The guard is bound to the original page scope, block and database identity.
Removed/replaced databases and changed page scopes cannot keep old editors
mounted. A delayed search result also waits if a cell has since gained editing
focus or a pointer action has not yet completed. Existing deferred-render release
on focusout, pointer release/cancel and window blur remains in use.

Database view dialogs resolve the current block/view immediately before create,
rename, copy and delete. Refresh while a modal is open must not leave mutations
on a detached object. Actions recheck scope, database identity, edit/schema
permission, removed views, maximum count and last-view deletion. Shortcuts resolve
the same block's current index. Other database property/filter/sort/group dialog
commit paths are not certified by this packet.

## Evidence

In an isolated browser fixture, per-character Korean search originally replaced
the input between characters and stopped at `두 번`. The fixed search accepted
`두 번째` with only one focusin and no intervening change/focusout. Native undo
and redo retained the input. A second connection changed the document title;
the first connection retained its query/focus and the second query stayed empty.
Table, board, list, gallery, calendar and timeline result refreshes were checked,
including date-less item counts. Refreshed result cells and item-open buttons
worked. A view creation lost during a pending cell save was reproduced, covered
by initially failing tests, fixed, and verified through UI and a fresh connection.

Thirty-six checks in eight JavaScript test files passed. Four syntax checks also
passed. Native Windows IME, all permission roles, all structure/concurrent editing
cases and operating-app rollout remain broader verification requirements.

## Automation distinction

The fixture-only event observer saw nonempty browser `fill` emit an untrusted
input event, but no change event when leaving the cell. Empty `fill` selected the
existing text without clearing it or emitting deletion input. Actual Backspace
emitted trusted beforeinput/input with `deleteContentBackward` and cleared the
field. This resolves the earlier fill-specific observations for the tested
connection; the application was not modified to disguise those tool semantics.
Dialog inputs read directly on Save may still use nonempty fill.

The earlier unspecified Korean fragments were not reproduced in this session.
Their historical cause remains unproven; this is not a claim that every IME or
external-input case has been fixed. The event observer and its panel exist only
in the isolated fixture, not in product assets.
