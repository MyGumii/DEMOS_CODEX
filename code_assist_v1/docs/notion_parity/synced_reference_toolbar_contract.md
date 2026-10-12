# Synced reference toolbar and original popup

Status (2026-10-03): supported main and mounted database references use one
top-right toolbar. This supersedes the older bottom inline action presentation,
without changing the frozen synced read/text/detach or authority contracts.

Original location opens an anchored menu with the original entry. Only a valid
current synced read enables it. The exact source endpoint and typed body location
are still reauthorized immediately before navigation through existing hooks.
Same-page title comes from the visible current page; unknown other-page titles
stay generic. DOM text labels are escaped by textContent. No peer count or full
location inventory is inferred from cached pages or sync IDs.

Main editable references open the existing complete block action/search dialog
from More. Readonly main references expose only refresh in a local menu. Mounted
DB references expose refresh and the previously authorized detach/retry action
inside More. Unsupported body mounts have disabled controls. No DB copy action,
grants, capability, schema, runtime feature activation or writer is invented.

Toolbar visibility follows hover, keyboard focus and its open menu. A transparent
bridge covers the small toolbar-to-body gap. DB wrappers fill the body lane.
Menus use the browser top layer when supported and fit within the viewport.
Arrow/Home/End move among enabled visible actions; Escape returns to the visible
trigger. Shared-text Escape and reference receipt navigation focus a visible
toolbar trigger rather than an action inside a hidden menu.

A render which removes focused toolbar markup can transfer a five-second focus
intent only to the same realm/actor/workspace/page/navigation/body/reference/
original identity. The replacement must satisfy its current scope and available
read before restoring the original trigger. User pointer or focus elsewhere
cancels the intent. No read/write authority or displayed source is transferred.

One active local popup closes on outside pointer/focus, enclosing scroll, dispose,
or source unavailability, including a click back into the same reference body.
Bindings remove observers/listeners on replacement.
Existing main block menu retains its own current target and return focus. These
presentation hooks perform no network or canonical mutation by themselves.

Final32 distinct browser cases cover editor/viewer light1280/dark390 keyboard and
pointer popup paths, no navigation body mutation, disconnected DOM cleanup,
owned/linked row/template edits, save/read overlap, original storage navigation,
hidden owner read-only originals, revocation, closed replies and DB detach/retry.
Existing49 JS renderer/reference/owner/copy/tab/column cases and4 syntax checks
pass on recorded frozen sources. These are isolated application fixtures.

Actual Notion was inspected by computer use read-only: top-right location/copy/
More toolbar, source link list, and block action dialog with unsync. The app's
current reference popup lists one original, not the full Notion peer inventory;
the app's sync-copy semantics and all DB actions are not full parity. Normal app
loads ui379/action-ui3 and preserves title plus13 blocks; collaboration is not
activated. Native Windows IME and full release readiness are not asserted.
