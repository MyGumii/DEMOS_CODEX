# UI-EDITOR-006 local draft-recovery result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

The isolated main editor now preserves a dirty atomic-save draft in tab-scoped
session storage when a network save fails. Reloading the same user, workspace,
and page restores the draft without treating it as acknowledged server state.
The first explicit retry reuses the exact original request body and operation
ID, so response-loss recovery remains compatible with the server idempotency
ledger.

If the acknowledged server revision changes before recovery, the draft is
restored in conflict state with automatic saving disabled. `Ctrl+S` opens the
explicit recovery dialog; choosing discard loads the latest server revision
and removes the session draft. A normal successful save also removes it.
Startup no longer overwrites recovery or conflict status with a generic
`Ready` message, and a manual save cancels its older pending autosave timer.

Draft keys are scoped by authenticated user, workspace, and page. Logout clears
the current user's tab-scoped drafts. No draft is written to durable local
storage, the backend, publication state, audit state, or outbox state.

This completes the local H4 editor scenario set. It does not claim official
Notion behavior, signed-in reference characterization, collaborative realtime
merge semantics, or differential parity.

## Browser evidence

The journey ran twice in independent trusted operating-system temporary roots
and returned identical identifier-free reports. A separate isolated extraction
returned:

```json
{"conflict_autosave_attempt_delta":0,"conflict_draft_restored":true,"discard_loaded_server_revision":true,"focus_sequence":["PAGE-CANVAS","PAGE-CANVAS"],"network_draft_restored":true,"retry_operation_reused":true,"second_brain_item_delta":0,"second_brain_revision_delta":0,"synthetic_only":true}
```

Local capture SHA-256 values:

```text
UI-EDITOR-006-A01 sha256:32c7886579b1947ce42dab063bcb3b4beded23b21a350b54026bd42f47a77f7c
UI-EDITOR-006-A02 sha256:98920b5aa2f471e7784b240b75781b69d6780ec994fd12f97f7e864ba5b028c9
```

UI source build SHA-256:

```text
sha256:bdbaef171e6e8682adcd921e98827891ca5f7c0196bb27bebf39d3e3df4a20db
```

No raw DOM, screenshot, request payload, cookie, credential, browser-storage
value, filesystem path, workspace/page identifier, or synthetic page text is
stored in the evidence.

## Verification

- first red: a failed manual save was followed by an older pending autosave,
  so reload observed a false success; `1 failed in 29.21s`;
- recovery-status red: startup replaced the recovered status with `Ready`;
- idempotency red: block normalization changed the retry body and operation ID;
- fixture red: the external-conflict setup omitted CSRF and was rejected with
  HTTP 403;
- focused final UI-EDITOR-006 browser run: `1 passed in 21.32s`;
- complete final local browser file: `12 passed in 240.93s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 11.47s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 4.65s`;
- plan, local fixture, evidence harness, packet, and reference guard:
  `68 passed in 19.70s`;
- JavaScript and Python syntax checks: pass.

One read-only `rg` discovery command ended with Windows host exit
`0xC0000409`; the equivalent simpler query succeeded. This was recorded as a
host-tool failure, not a product or test failure.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- All pages, saves, conflicts, and publication state existed only inside
  trusted operating-system temporary roots.
- Second Brain item and revision deltas were both zero.
- No live feature flag, operational migration, payment, trial, invitation,
  public action, continuous consumer, resolver cutover, or permanent delete was
  performed.
- Signed-in Chrome C0 remains unavailable in this execution environment because
  the browser-control host fails before browser or tab selection; no Notion
  content was accessed by that attempt.

`REF-FREE-A/B` reference behavior and sealed differentials for H3/H4 remain
pending. If C0 remains unavailable, the next cohesive local phase is H5,
starting with `UI-DB-001` typed table-row/property round-trip.
