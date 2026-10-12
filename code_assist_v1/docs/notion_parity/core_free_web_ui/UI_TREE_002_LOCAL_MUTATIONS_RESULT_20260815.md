# UI-TREE-002 local page-tree mutation result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

An isolated synthetic workspace now completes a persisted page-tree mutation
journey through the normal UI: create a page, enter and save its title, nest it
under an existing page through the move dialog, reorder it before a sibling by
dragging, and refuse an ancestor-into-descendant drag without issuing a move
request. The active row, breadcrumb, and keyboard focus remain coherent after
each rerender.

This is an H3 local interaction result. It does not claim official Notion
parity, visual calibration, complete page lifecycle parity, destructive
watermark consumption, or resolver cutover.

## Reproduced gaps and cohesive correction

The first route-level red test reproduced the central persistence gap: the
canonical workspace page collection was read through an explicit workspace
binding, while the New Page control still posted to the global legacy endpoint.
A newly created page therefore had no workspace binding and could disappear
from the canonical list after reload.

The cohesive correction:

1. adds a deterministic, self-owned legacy page create primitive keyed by the
   workspace and operation ID;
2. adds a default-off, owner-only workspace create service that performs exact
   replay, stages and activates one source binding, and never directory-scans;
3. adds strict request-field, stale workspace-version, changed-replay, and
   disabled-gate refusal tests with zero extra source pages;
4. makes the navigation UI use the workspace-scoped command and retain the same
   operation intent across response-loss retry;
5. restores the focused tree row after move-dialog and drag rerenders;
6. carries the originating tree root through the drag handler, avoiding a
   stale/undefined root reference;
7. extends the frozen UI action vocabulary with explicit synthetic selection
   and drag actions used by this scenario.

The new command deliberately does not project locators or enable the resolver.
Later healing/runtime cutover gates remain separate.

## Browser evidence

The full mutation journey ran twice in independent isolated roots and returned
identical identifier-free reports. A final evidence extraction returned:

```json
{"active_path_coherent":true,"failed_move_request_delta":0,"focus_sequence":["PAGE-TITLE","TREE-CHILD-B","TREE-CHILD-B","TREE-ROOT"],"legacy_create_requests":0,"nested_child_count":2,"synthetic_only":true,"workspace_create_requests":1}
```

Local capture SHA-256:

```text
UI-TREE-002 sha256:c9fb4ae16772b3a8d3afb765fe78e367f2899f384a0512f0c290b9a221d4a85b
```

UI source build SHA-256:

```text
sha256:1b0111f829f137ba5c616f10f8e06edae453d83333533159e52ecf47f36e40f9
```

No raw DOM, screenshot, request log, cookie, credential, filesystem path,
workspace/page identifier, nonce, digest, or personal text is stored.

## Verification

- initial route red: workspace page POST was absent and returned `405`;
- first browser red: evidence action validator rejected the newly declared
  `select_synthetic` verb after the product journey reached capture;
- focused final UI-TREE-002 browser run: `1 passed, 5 deselected in 22.37s`;
- complete final local browser file: `6 passed in 122.25s`;
- workspace page service and route tests: `5 passed in 10.61s`;
- workspace routes, navigation, and auth isolation: `41 passed in 43.22s`;
- Notion routes, atomic-save compatibility, and publication adapter: `25 passed
  in 12.23s`;
- Second Brain publication/job restart and chat characterization: `5 passed in
  6.90s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- final Python and JavaScript syntax checks: pass.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- All source pages, workspaces, catalog state, and publication state existed
  only inside trusted temporary roots.
- The navigation mutation capability remains default-off outside the explicit
  isolated test environment.
- No live feature flag, migration, payment, trial, invitation, public action,
  continuous consumer, or resolver cutover was performed.
- Business, Enterprise, and Archive remain conditional.

`REF-FREE-A/B` C0, official synthetic reference capture, and the sealed
differential remain pending. `UI-TREE-002` is local-green only. The next local
H3 slice is `UI-TREE-003` (duplicate, Delete/Trash, restore), unless the
signed-in reference browser transport becomes available first.
