# SBE-8.4 canonical conversation-ID state repair evidence

Date: 2026-08-25 Asia/Seoul

Status: LOCAL_HTTP_AND_DOM_CONTRACT_PASS

## 1. Reproduction and exclusion

The retained canonical database contains 163 conversations across the gold
organization. Every conversation ID matches the lowercase canonical UUID
contract, and no stored canonical message contains the reported
`conversationId must be a canonical UUID` text. The error was generated in the
browser before a turn reached the server; canonical data was not corrupt.

The retained admin compatibility API also exposes five historical JSON sessions
whose IDs use the legacy `s_<hex>` format. The active canonical admin API
exposes UUID conversations. A stale legacy row left in the DOM during a server
restart could therefore also be handed to the newly active canonical loader.

The deterministic code defect was the new-session path:

1. `startNewChatSession()` cleared global `State.session_id`.
2. The canonical scope controller retained its current conversation UUID.
3. `ensureConversation()` returned that existing UUID but did not copy it back
   to global state.
4. `Chat.ensureSessionId()` saw an empty global value and manufactured a legacy
   `s_...` ID.
5. The canonical transport rejected that ID before HTTP dispatch.

## 2. Repair

- The new-session button now calls the canonical bridge's
  `startFreshConversation()`, which creates a real server UUID conversation in
  the currently selected Wiki.
- `ensureConversation()` treats controller state as authoritative and always
  synchronizes `State.session_id` before returning an existing conversation.
- A stale non-UUID session row is no longer passed into the canonical loader;
  the session pane refreshes from the canonical list and asks the user to select
  again.
- Cache keys for `app.js`, `sessions.js`, and `knowledge_core_browser.js` were
  advanced to `20260825e`.

## 3. Verification

- JavaScript syntax checks: pass.
- focused browser/session/scope suite: 33 passed in 12.52 seconds.
- complete relevant preview/browser/retrieval/Agent suite: 80 passed in 96.85
  seconds.
- retained 10211 health: `ready` with `company_api_called=false`.
- authenticated root HTML serves all three `20260825e` cache keys.

The regression explicitly sets global state to `s_stale_legacy_session` while
the controller owns a canonical UUID, calls `ensureConversation()`, and asserts
that global state is repaired to the controller UUID before a turn can be sent.

## 4. Changed implementation hashes

| File | SHA-256 |
|---|---|
| `static/app.js` | `160af7cc2b9769a1d921c042f0c3f0353605731ce7cde78c250987d5ac75f898` |
| `static/sessions.js` | `1312211091fec239b6558f23e73a3aaa69f95155a20d115fce5bc14b02ed478d` |
| `static/knowledge_core_browser.js` | `81744f67af67591fee76ec18b2b9e6adf432ce0f64098014442c4cb7e9c15912` |
| `static/index.html` | `ea4fa32d20fcfa4e621a8b45bcbd7479ab21104e7f7e2be3eeca92d6a70a0b08` |

Pixel-level browser automation remains blocked by the separately documented
in-app Browser runtime bootstrap failure. This packet claims live API/static
delivery and executable DOM/state-contract proof, not a screenshot pass.
