# UI-SHELL-001 local workspace-isolation result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

The default-off canonical navigation fixture now proves, in an installed stable
Chrome browser, that switching between two isolated synthetic workspaces shows
only the active workspace's bound page.  Sidebar rows, global-search results,
the active page view, and direct page opening all use the same authoritative
workspace page snapshot.  The switch returns focus to the workspace switcher.

This is a local H3 safety and interaction result.  It is not a claim that the
official Notion reference has been characterized or that UI parity has passed.

## Reproduced defect

Before the correction, the browser test failed with two page rows where exactly
one was allowed.  The client loaded `/api/notion/pages` once at boot and retained
both legacy pages while `switchCanonicalWorkspace` changed only the navigation
preference snapshot.  The same global endpoint also backed search.

The red evidence was an actual browser assertion, not a source-regex or inert-DOM
test:

```text
expected #ownPages .page-title-label count: 1
actual count: 2
```

A second red assertion proved that the global legacy page-list request occurred
before the canonical capability boundary was known.

## Cohesive correction

1. `notion.js` validates the exact workspace-page response identity, count,
   unique page IDs, owner, placement, and binding generation.
2. Canonical navigation uses only
   `/api/notion/workspaces/<workspace>/pages`; it never falls back to the global
   page list after the server declares navigation authoritative.
3. Boot establishes the workspace capability before loading pages, closing the
   transient initial-render leak.
4. Switching clears the old page view before target I/O, loads navigation and
   page snapshots under the same sequence fence, restores a safe prior list on
   failure, and returns focus to the workspace switcher.
5. Global search uses the active workspace page endpoint in canonical mode.
   No `/api/notion/search` request is made in this scenario.
6. Direct page opening fails closed when the requested page is absent from the
   active authoritative workspace snapshot.
7. The legacy/default-off path remains unchanged when the canonical capability
   was never established.

The isolated fixture creates two synthetic workspaces and one exact bound and
projected synthetic page per workspace.  It enables the navigation feature only
inside that test subprocess.  No production flag or catalog was changed.

## Browser evidence

The complete A -> B -> A journey ran twice in independent isolated roots and
produced byte-identical identifier-free reports.

```json
{
  "active_workspace_restored": true,
  "cross_workspace_direct_open_rejected": true,
  "focus_restored": true,
  "global_search_requests": 0,
  "legacy_page_list_requests": 0,
  "scoped_page_request_count": 13,
  "synthetic_only": true,
  "workspace_page_counts": [1, 1, 1]
}
```

Local capture SHA-256:

```text
UI-SHELL-001 sha256:6fbf103060f265720ef68027a59eb49920325d23f0d83312f8fd10f9342ab615
```

UI source build SHA-256:

```text
sha256:cfd93be1f4c62e5bda0858c267b3797ba66002b1bb415fc14dd1a4c3a81babef
```

No raw request log, cookie, password, source path, workspace/page identifier, or
personal text is stored in this result.

## Verification

- new real-browser UI-SHELL-001 red/green, including a cross-workspace direct
  URL rejection: `1 passed in 23.70s`;
- complete local browser file, including the prior UI1 and Second Brain flows:
  `3 passed in 62.87s`;
- local fixture contracts: `7 passed in 4.72s`;
- workspace page/navigation/auth routes: `35 passed in 36.76s`;
- Notion routes, atomic-save compatibility, publication adapter:
  `25 passed in 11.31s`;
- Second Brain publication restart and chat characterization:
  `5 passed in 4.65s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- `node --check notion.js`: pass.

The first broad parallel PowerShell inspection in this run terminated with the
known host-dependent `0xC0000409` error.  Split, serial commands completed.  The
host failure is neither a product failure nor a pass.

## Safety boundary and remaining gate

- Personal Notion workspaces and content were not opened.
- Live local `user_data`, `knowledge`, `.runtime`, sessions, and workspace data
  were not accessed.
- No trial, payment, invitation, public posting, operational migration, live
  feature flag, continuous consumer, or resolver cutover occurred.
- Business, Enterprise, and Archive remain conditional.

`REF-FREE-A/B` C0, official synthetic reference capture, and the first sealed
differential remain pending because the signed-in browser-control transport has
not recovered.  Until that evidence exists, `UI-SHELL-001` is local-green only.
