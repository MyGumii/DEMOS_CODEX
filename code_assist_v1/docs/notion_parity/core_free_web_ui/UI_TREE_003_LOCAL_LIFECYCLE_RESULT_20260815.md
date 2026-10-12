# UI-TREE-003 local page lifecycle result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

An isolated synthetic workspace now completes the H3 page lifecycle journey
through the normal UI: duplicate a root page and its child, move the duplicate
subtree to Trash, list the exact archived subtree, and restore it with the same
page identities and hierarchy. The active page, breadcrumb, tree row, and
keyboard focus remain coherent after every rerender.

The journey is workspace-scoped and default-off. It issues no global legacy
lifecycle request, no permanent-delete request, and no Second Brain mutation.
This is a local interaction result, not a Notion parity or resolver-cutover
claim.

## Reproduced gap and cohesive correction

The first strict contract run reproduced the integration gap after the product
slice landed: the registrar exposed 16 route rules and 24 operations, while the
machine contract still described 12 rules and 19 operations. The strict suite
failed rather than silently accepting the new surface.

The cohesive correction:

1. records workspace page create, subtree duplicate, Trash, Trash listing, and
   restore as required hard operations;
2. fixes the request, path, success-status, response-key, and error profiles for
   all five operations;
3. keeps the frozen legacy baseline at 59 rules, 61 operations, and 15 hard
   operations;
4. adds exact lifecycle semantics assertions, including replay status and the
   absence of a permanent-delete route;
5. preserves default-off routing, workspace authorization, exact operation
   replay, and bounded subtree behavior.

## Browser evidence

The complete lifecycle journey ran twice in independent trusted temporary
roots and returned identical identifier-free reports. A separate evidence
extraction returned:

```json
{"duplicate_subtree_count":2,"focus_sequence":["DUPLICATE-PAGE-ROW","TRASH","RESTORED-PAGE-ROW"],"legacy_lifecycle_requests":0,"permanent_delete_requests":0,"second_brain_item_delta":0,"second_brain_revision_delta":0,"synthetic_only":true,"workspace_archive_requests":1,"workspace_duplicate_requests":1,"workspace_restore_requests":1}
```

Local capture SHA-256:

```text
UI-TREE-003 sha256:73de771c93be474617fffb2b80f8be2b88cb13cf9da2e6440747189d77ee7522
```

UI source build SHA-256:

```text
sha256:a6738f744a6c64b2b2410b016cb8e0aa8d0a4337f2e4bb2160d8a68247c1b7d9
```

No raw DOM, screenshot, request log, cookie, credential, filesystem path,
workspace/page identifier, nonce, digest, or personal text is stored.

## Verification

- initial machine-contract red: `3 failed, 19 passed`; the failures were the
  expected 12/19 surface and four uncharacterized lifecycle rules;
- strict final workspace route contract: `15 passed in 6.00s`;
- page service, workspace routes, navigation routes, and auth isolation:
  `47 passed` across four serial runs;
- focused final UI-TREE-003 browser run: `1 passed, 6 deselected in 23.90s`;
- complete final local browser file: `7 passed in 149.09s`;
- Notion routes, atomic-save compatibility, and publication adapter:
  `25 passed` across three serial runs;
- Second Brain publication/job restart and chat characterization: `5 passed`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Core UI plan verifier and isolated fixture: `21 passed`;
- UI evidence, packet, redaction, and reference guard: `47 passed`;
- Python syntax, JSON parsing, and canonical module-mode verifier: pass.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- All local pages, workspaces, catalog state, and publication state existed only
  inside trusted operating-system temporary roots.
- The lifecycle capability remains default-off outside the isolated test
  environment.
- No permanent delete, live feature flag, operational migration, payment,
  trial, invitation, public action, continuous consumer, or resolver cutover
  was performed.
- Archive/restore currently changes the legacy source while the catalog
  resolver remains off; source-proof convergence is still a later healing and
  cutover gate.
- Business, Enterprise, and Notion Archive beta remain conditional.

`REF-FREE-A/B` C0, official synthetic reference capture, and the sealed
differential remain pending. `UI-TREE-003` is local-green only. With the local
H3 shell/tree slices implemented, the next local product slice is the first H4
editor interaction slice unless signed-in reference capture becomes available
first.
