# Owner token redisplay — 2026-09-11

Scope: AIK-036. The user explicitly requested that signed-in account owners be
able to view previously issued agent tokens again. Existing active grants now
support this without rotation, reissue, migration or plaintext-token storage.

## Implementation

- Browser-only `POST /api/integrations/v1/connections/{connection_id}/token`
  accepts only a caller UUID `operation_id`. It is not a machine endpoint and
  never falls back from a bearer header to the browser account.
- The store uses the existing HMAC derivation under the grant DB write fence,
  checks the reconstructed value against the stored SHA-256, and revalidates
  owner, account activity/epoch, grant expiry/revocation and selected-Wiki access.
  Changed signing keys return `503 TOKEN_UNAVAILABLE`, not a substitute token.
- The idempotent receipt contains only the connection ID. The transactional
  `connection.token_viewed` audit event contains no token. Replayed requests
  perform current authorization and reconstruction checks again. Viewing does
  not change row version, token hash, grant scope or expiry.
- Responses use private/no-store, nosniff and no-referrer. Existing browser
  Host/session/CSRF checks apply. GET is not a reveal operation.
- `연결 설정 → 내 연결 → 토큰 보기` explicitly fetches and displays a token;
  `토큰 복사` copies it and `토큰 숨기기` clears/removes the displayed field.
  Navigating tabs/closing removes the view; an in-flight response is discarded
  if its view is no longer current. Inactive grants have no reveal button.
  The existing issue-screen message and user guides were updated.
- A capability flag hides the new UI action against older processes sharing
  the same static assets. The script cache version is now `20260911c`.

The separate extension inventory is 25 paths / 26 operations, with 13 machine
and 13 browser-only operations. Frozen SBE inventories/schema and all machine
MCP tools are unchanged. No extra password-confirmation flow was introduced.

## Verification

All writable test stores/accounts/tokens were isolated fixtures. No live token
was displayed, copied, reissued, revoked or submitted to a test tool.

From `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_token_reveal.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_surfaces.py -q -x -p no:cacheprovider
```

**12 passed in 16.23s**, no skips. Coverage includes reopening an existing ledger
and obtaining the exact issued token; unchanged grant/expiry; no raw secret in
the SQL dump, audit, replay receipt or ordinary list response; idempotency
conflicts and rejection of a revoked replay; foreign owner, expired, revoked,
changed epoch, archived Wiki, inactive account and changed-key failures.

The real auth middleware was exercised with authenticated owner and foreign
browser sessions, Host/Origin/CSRF failures, unauthenticated and bearer requests,
GET and extra authority-field rejection. The real Edge fixture UI verified
issue → list → reveal exact token → copy callback → hide → reveal again → tab
navigation → normal submission/review/publication → revoke. Clipboard delivery
was captured by a fixture callback rather than overwriting the user's clipboard.

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integrations.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_connections.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_ask.py -q -x -p no:cacheprovider
```

**41 passed in 43.69s**, no skips. Existing grant, operation, evidence, authoring,
queue, route inventory, token renewal, Notion root isolation, Outlook connection
and canonical/MCP Ask checks remain passing. Combined: **53 tests passed**.
Public client packaging `--check` also remains current.

## 10221 deployment

Read-only preflight found no integration runs. The lone canonical accepted turn
was the existing August 28 record (`updated_at=1787936773076`), not new work;
its data was left untouched. The exact 10221 process and its owned durable and
projector workers were verified before their shutdown. The existing preview
launcher restarted the same root with `--agent-worker`; no reseeding or
migration occurred. Composition reported 13 machine routes and the unchanged
marker `69f86898010bcca2b0f114fe7ec4f77e3026da9be14c12048fcc4b9fc7697fb5`.

The new process is PID 12448, with durable PID 19796 and projector PID 36956.
`GET /healthz` returned `ready`, both workers alive, provider mode
`codex-validation`. The existing host Notion/Outlook bindings were retained.
The 10322 and 10011 processes were not restarted or patched by this task.

The real in-app browser was refreshed after deployment and returned to the
login screen. The live account's new reveal button was therefore not clicked;
token reveal/copy/hide was verified in the isolated browser test above. The
user can log back in and use the existing connection's token-view button.
