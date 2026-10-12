# UI1 C0 reference guard result

Recorded: 2026-08-14 23:43 KST  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: C0 contract-tested; external Chrome transport pending

## Outcome

`tools/notion_parity/ui_reference_guard.py` turns the runbook's C0 safety checklist into a deterministic, identifier-free state gate.

A passing C0 decision now proves all of the following before any reference capture:

- the browser connection, expected Chrome session, and signed-in state are visibly confirmed;
- the workspace switcher was observed without opening a personal workspace or page;
- both `REF-FREE-A` and `REF-FREE-B` were identified through synthetic aliases;
- no screenshot, raw artifact, mutation, invite, public action, or other external side effect occurred;
- visible non-synthetic workspace labels were not persisted or captured.

The decision stores only booleans, fixed aliases, synthetic run IDs, canonical failure codes, and a canonical SHA-256. A contradictory manually constructed pass decision is rejected.

After C0, each reference action is authorized separately. The action guard:

- accepts only fixed UI scenario/action IDs and requires their prefix relationship;
- accepts only `REF-FREE-A`, `REF-FREE-B`, `PERSONAL-FORBIDDEN`, or `UNRECOGNIZED` aliases, never a raw workspace name;
- allows read-only actions only in a synthetic workspace;
- allows synthetic mutation only after the fixture is reconfirmed;
- always denies reference-side Second Brain mutation, personal workspace entry, and external side effects.

## Verification

- focused C0 guard tests: `25 passed in 5.44s`;
- combined H1 guard/evidence/packet/plan/local-fixture tests: `65 passed in 18.63s`;
- production and test module compile: pass;
- clean-process import produced no forbidden app, route, auth, data-path, workspace, Knowledge Core, or Second Brain imports and no filesystem mutation.

No Browser or Computer Use application/window was selected during this slice. Personal Notion content and live local data were not accessed.

## Remaining gate

This is a contract-tested safety gate, not a live C0 result. The shared Browser/Computer Use JavaScript kernel still fails before application listing with `kernel-assets-path-not-found-os-error-3`. After that runtime is restarted, the harness must obtain a real passing C0 decision in signed-in Chrome before entering either synthetic workspace or producing reference evidence.
