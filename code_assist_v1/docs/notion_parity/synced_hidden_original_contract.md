# Hidden database original navigation

Status (2026-10-03): supported staged original navigation opens an exact read-only
original tree when its physical owner page has no visible owning database block.
Existing visible row/template editing and read-only paths remain unchanged.

Owned-template original navigation awaits the current ordinary save without a
close-after job. It verifies the same editor still owns the modal before closing
and reopening the source. Save completion cannot silently drop this navigation
or close its replacement. A save rechecks its mounted scope after awaiting shared
text flush. Manual linked-template draft dismissal behavior is unchanged.

Navigation opens the authorized physical page and waits for its initial replay.
The fallback creates no synthetic owning block and writes no body or schema.
It reads the original document/block endpoint afresh with no-store, validates both
returned body locations against the requested row/template location, and uses the
existing explicit-original/endpoint/witness controller. It renders the original
tree and rich children with the existing sanitized content renderer, focuses that
exact block, and labels the view read-only. Files use the exact database scope.

Current actor, workspace, page-load/navigation and page-data scope fence the
surface. Source change events refresh it; loading, denial, scope loss, hide and
dispose clear visible content. Closing aborts requests and removes observers;
late replies cannot reopen or populate a replaced modal. No write controller,
local source body, database/page grant or runtime capability is invented.

Final new browser7 covers cross-page row/template navigation in light1280 legacy
and dark390 staged modern sharing, no display block, exact focus, rich children,
no navigation body/DB mutation after ordinary initialization, current source
refresh, page grant revoke, wrong body location, delayed replies after close,
and deterministic held after-save reads during original navigation.
Affected browser10 retains owned row/template and linked physical owner paths.
Five existing server read/replay/archive/authority gates and28 existing JS
contracts pass. The authenticated normal app loads ui376 with title and13 body
blocks preserved; its staged collaboration feature is not enabled by this packet.

Fresh computer-use observation of actual Notion found the reference toolbar's
sync-location list, the original/current-page link, and same-page anchor navigation.
No Notion content changed. Hidden DB-body navigation was not compared in Notion.
The fallback is an app-specific read-only surface, not full visual parity, full
body editing, nested reference resolution or hidden pending-detach recovery.
Other copy/history/IME/action-placement and operating-release gates remain.
