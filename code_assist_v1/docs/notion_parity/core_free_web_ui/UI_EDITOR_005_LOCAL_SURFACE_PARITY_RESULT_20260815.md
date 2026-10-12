# UI-EDITOR-005 local editor-surface parity result

Recorded: 2026-08-15 Asia/Seoul  
Profile: `free-owner-chrome-desktop-ko-KR`  
State: local real-browser green; `REF-FREE-A/B` reference and differential pending

## Outcome

The isolated editor now carries one explicit persistence and focus contract
across the main page, side peek, and the full-page transition from that peek.
Opening side peek moves focus to its named page region. Side-peek title and
block controls accept the same `Ctrl+S` save command as the main editor, and a
successful save reconstructs the view without losing the focused control or
caret.

Choosing `전체 페이지` first flushes a dirty side-peek draft, closes and clears
the overlay, opens the same page in the main canvas, and restores page-canvas
focus. A subsequent main-editor save and reload preserves the final Korean
synthetic content. The correction also applies to child-page full-page actions
inside a peek.

This is a local H4 interaction result. It does not claim official Notion
surface parity, signed-in reference characterization, center-peek support, or
H4 completion.

## Browser evidence

The journey ran twice in independent trusted operating-system temporary roots
and returned identical identifier-free reports. A separate isolated extraction
returned:

```json
{"content_restored":true,"focus_sequence":["SIDE-PEEK-CANVAS","PAGE-CANVAS"],"save_count":2,"second_brain_item_delta":0,"second_brain_revision_delta":0,"side_peek_closed_on_full_page":true,"synthetic_only":true}
```

Local capture SHA-256 values:

```text
UI-EDITOR-005-A01 sha256:285760f540c0ac1066257bdecd5f2c916deb85c659d2e424fcbd0af4249e2bb8
UI-EDITOR-005-A02 sha256:c17e86d5da50990b9456898283286dd4e4c1273b62a0e009cff131f75074fa95
```

UI source build SHA-256:

```text
sha256:d7a57fd4ce6e2255ecf503abd19102f35c3217e2bd303785347fd14a4d809d21
```

No raw DOM, screenshot, request payload, cookie, credential, filesystem path,
workspace/page identifier, or synthetic page text is stored in the evidence.

## Verification

- first red: side peek became visible but focus remained outside its page
  region; `1 failed in 13.79s`;
- focused final UI-EDITOR-005 browser run: `1 passed in 18.11s`;
- complete final local browser file: `11 passed in 216.96s`;
- six Node editor/UI/navigation/publication/atomic-save/CSRF contracts: pass;
- Notion route, atomic-save compatibility, and publication adapter:
  `25 passed in 12.10s`;
- Second Brain publication/job restart and chat characterization:
  `5 passed in 5.11s`;
- JavaScript and Python syntax checks: pass.

## Safety boundary and remaining gate

- Personal Notion workspaces/content and live local data were not accessed.
- All pages, saves, and publication state existed only inside trusted operating
  system temporary roots.
- Second Brain item and revision deltas were both zero.
- No live feature flag, operational migration, payment, trial, invitation,
  public action, continuous consumer, resolver cutover, or permanent delete was
  performed.
- Signed-in Chrome C0 remains unavailable because the browser-control host
  fails before browser or tab selection; no Notion content was accessed by that
  attempt.

`REF-FREE-A/B` reference behavior and the sealed differential remain pending.
The next cohesive local H4 slice is `UI-EDITOR-006` conflict, retry, and draft
recovery. Reference capture takes priority whenever C0 becomes safely
available.
