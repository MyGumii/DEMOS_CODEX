# Database settings during document refresh

Property visibility, property creation/edit/copy/delete, filter, sort and group
dialogs resolve their current block by identity immediately before acting. A cell
save or document projection may replace the object captured when the dialog
opened. An action must mutate the current model, preserving its current rows and
unrelated settings, rather than a detached object.

The target is scoped to account, workspace, page load, block, database and active
view. A removed/replaced block, linked reference, removed/switched view or loss of
edit/schema permission rejects the action without closing the draft. Property
edit/copy/delete also rechecks property existence and original type; create/copy
rechecks the 500-property limit. Rules recheck selected property existence and
group settings recheck group eligibility. These guards do not implement a general
merge policy for simultaneous edits to the same setting.

Visibility saves only toggles changed in this dialog and merges them into current
visibility. New properties and unrelated remote visibility changes survive. Title
properties remain visible. Adding/removing filter/sort draft rows resolves the
same block's current index, so document reordering cannot retarget the dialog.

## Evidence, 2026-09-23

The actual isolated app lost a filter when opened immediately after editing a
cell: applying a title filter still displayed all three rows. With the fix, the
same sequence displayed only the matching row. Cell-save overlap with sort/group,
remote page-title refresh with visibility/property creation, property edit and a
fresh third connection were checked through browser UI. Original row contents
and the page title were restored; one test property and intended view settings
remain in the disposable fixture. Korean sequential search retained its value and
focus and returned the correct row. This was not a new native Windows IME test.

Eight new regression cases failed before the fix and passed afterward. The final
focused run passed 44 checks across nine JavaScript files, including search,
view dialogs, cell input, database bridge, native item creation, range history and
UI runtime. Two JavaScript syntax checks passed. Account/source/permission/view
changes and property copy/delete were tested automatically, not certified across
every actual browser role and concurrent editing combination. No new comparison
against actual Notion or operating-app rollout is claimed by this packet.
