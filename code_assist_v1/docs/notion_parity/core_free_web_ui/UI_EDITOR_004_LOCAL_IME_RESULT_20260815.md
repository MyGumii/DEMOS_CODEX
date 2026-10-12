# UI-EDITOR-004 local Korean IME result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

The isolated main-page editor now treats Korean composition as one logical
edit. While composition is active it updates only the in-memory block text,
does not schedule autosave, does not apply markdown or mention/slash commands,
and ignores composition keydown (including the IME Enter/keyCode-229 class).
At `compositionend` it commits once through the ordinary editor pipeline,
saves once, and reloads the exact completed Korean text.

The same cohesive correction fixes an older data-loss bug exposed by the red
journey: after an autosave response, the application replaced the block objects
held by `State.pageData` while live contenteditable listeners retained the old
objects. A subsequent edit could remain visible in the DOM but save stale or
empty block data. Successful autosave now reconciles server-normalized blocks
into the existing objects in place, preserving listener identity and caret
without discarding the canonical response.

This is a local H4 interaction result. It does not claim official Notion IME
parity, OS candidate-window fidelity, signed-in reference characterization, or
H4 completion.

## Browser evidence

The composition journey ran twice in independent trusted temporary roots and
returned identical identifier-free reports. A separate extraction returned:

```json
{"block_count_during_composition":1,"committed_save_count":1,"content_restored":true,"focus_sequence":["EDITOR-BLOCK","PAGE-CANVAS"],"intermediate_save_count":0,"second_brain_item_delta":0,"second_brain_revision_delta":0,"synthetic_only":true}
```

Local capture SHA-256:

```text
UI-EDITOR-004 sha256:2554f1d4a22f64d7fa9dee2adfee03b2e1ac95170b3ea30d920aafc7793b4a70
```

UI source build SHA-256:

```text
sha256:3f46d39f0d0322df6c2df95801b0cdddc41c42abad20962d06e4ad977a85cc4f
```

The test executes the composition/input event path inside installed stable
Chrome. It does not invoke a real operating-system IME candidate window, and
that reference-level behavior remains a later manual/differential gate. No raw
DOM, screenshot, request payload, cookie, credential, filesystem path,
workspace/page identifier, or personal text is stored.

## Verification

- first red: intermediate Korean composition caused one premature save;
  `1 failed, 7 deselected in 8.86s`;
- second red after composition deferral: reload returned an empty block because
  autosave had replaced the listener-bound block object; `1 failed, 7
  deselected in 29.97s`;
- focused final UI-EDITOR-004 browser run: `1 passed, 7 deselected in 19.05s`;
- complete final local browser file: `8 passed in 165.27s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- atomic-save compatibility and publication adapter: `22 passed` across two
  serial runs;
- Second Brain publication/job restart and chat characterization: `5 passed`;
- JavaScript and Python syntax checks: pass.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- All pages, saves, and publication state existed only inside trusted operating
  system temporary roots.
- Second Brain item and revision deltas were both zero.
- No live feature flag, operational migration, payment, trial, invitation,
  public action, continuous consumer, resolver cutover, or permanent delete was
  performed.
- Signed-in Chrome C0 still fails before browser or tab selection because the
  host cannot initialize its browser-control assets; no Notion content was
  accessed by that attempt.

`REF-FREE-A/B` reference composition behavior and the sealed differential are
pending. The next cohesive local H4 slice is `UI-EDITOR-002` slash-menu block
transformation, followed by selection/clipboard/undo and conflict recovery;
reference capture takes priority whenever C0 becomes safely available.
