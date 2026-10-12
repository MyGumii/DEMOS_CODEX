# Agent connection controls — 2026-09-11

Scope: AIK-031–033, additive changes in the independent agent integration
extension. Frozen SBE inventories, canonical schema/migrations, existing Wiki
authoring/publication rules and the 10011 deployment are unchanged.

## Delivered behavior

- Opt-in sliding expiry on successful authenticated machine API responses.
  The same token remains usable until the renewed expiry. Browser reads,
  background processing and rejected requests do not extend it. Expired or
  revoked grants cannot be revived. Existing grants can change renewal mode
  through a versioned, idempotent browser operation with an atomic audit event.
- A separate legacy Notion user-data binding uses ContextVar isolation rather
  than global reassignment. Owned active pages include ancestor paths and depth;
  foreign pages stay excluded. Recording still uses existing Notion creation
  and atomic-save services and preserves operation replay.
- Browser-only Outlook account status/connect/callback/disconnect reuse the
  existing encrypted Microsoft OAuth storage and delegated Mail.Read flow.
  Bearer tokens cannot manage the account. Origin/callback matching is required;
  failed OAuth callbacks suppress provider details and use no-referrer. Account
  connection does not start mail synchronization, sending or scheduled jobs.
- The connection UI exposes renewal, hierarchical Notion path choices, optional
  server Outlook connection/setup instructions and clearer permission labels.

The extension now has 24 paths / 25 operations: 13 machine-enabled operations
and 12 browser-only operations. OAuth account lifecycle endpoints are ephemeral
account operations, not replayable task-ledger mutations.

## Isolated verification

All writable stores and browser/MCP test data were temporary fixtures. No live
user Wiki, Notion page, mail or token was used as a writable test target.

From `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integrations.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_ask.py -q -x -p no:cacheprovider
```

**34 passed in 42.41s.** Existing authorization, submission/evidence/authoring,
Ask delivery/replay and actual Hermes MCP adapter checks.

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/test_notion_store.py code_assist_v1/tests/test_notion_store_runtime_boundaries.py code_assist_v1/tests/test_notion_atomic_save_compat.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_core.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_surfaces.py -q -x -p no:cacheprovider
```

**39 passed in 45.62s.** Shared Notion compatibility, real canonical integration,
cross-process effects/recovery and isolated browser/MCP surfaces.

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_connections.py code_assist_v1/tests/test_outlook_graph.py code_assist_v1/tests/test_outlook_routes.py -q -x -p no:cacheprovider
```

**17 passed in 11.06s:** six new connection tests and eleven existing Outlook
tests. After adding a safe OAuth failure-response regression, the complete new
connection file was rerun:

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_connections.py -q -x -p no:cacheprovider
```

**7 passed in 7.07s.** Unique combined result: **91 tests passed** (the repeated
six are counted once). New coverage includes renewal success/failure/expiry and
revocation boundaries, mode-toggle replay/version conflicts and unchanged token
hash, separate Notion roots with three-level paths and 24 concurrent reads,
recording/replay under a child page, foreign-parent rejection, missing-root
fail-closed behavior, OAuth browser/bearer separation, redirect mismatch,
missing app settings and safe invalid-callback responses. These tests verify
integration contracts; Microsoft OAuth itself used fixtures, not a live tenant.

## Development host and live UI verification

The existing 10221 preview root remains
`D:\CodexTestTemp\SBE9-V13-20260827-v6`. Its operational host configuration is
`.runtime/agent-integration-host.json`, with the Notion connector pointed at
`G:/AI_Agent/DEMOS_CODEX/code_assist_v1/user_data`, the existing per-user store
used by 10322. The configured Notion origin is `http://127.0.0.1:10322` and the
optional Outlook feature flag is enabled. This binding uses the same local
Notion services/store; it is not an official Notion cloud connection or an HTTP
proxy through port 10322.

After checking for active new integration work, only the 10221 preview and its
owned worker children were restarted through the existing preview launcher.
There was no reseeding, migration or production patch. The marker remains
`69f86898010bcca2b0f114fe7ec4f77e3026da9be14c12048fcc4b9fc7697fb5`.
Read-only `GET /healthz` returned `status=ready`; durable and projector workers
were alive, with provider mode `codex-validation`.

After the user logged in, the real in-app browser at 10221 showed:

- The default 30-day duration and an unchecked active-use renewal option.
- Notion targets including `Second Brain Home / Resources` and an existing
  third-level child beneath Resources. The list displays complete paths;
  it is a native dropdown, not a collapsible tree.
- Clear missing Microsoft app configuration and the expected callback
  `http://127.0.0.1:10221/api/integrations/v1/outlook/callback`.
  Server Outlook permission was disabled while no account was connected.
- No existing connections or submitted tasks. Direct submission correctly
  requested a connection first. No connection/token or trial record was created
  during this read-only UI check. The Notion target stayed at `사용 안 함` until
  the user chooses a target for a new connection.

Actual Microsoft account connection remains pending server tenant/client/secret
and redirect settings plus user Microsoft login. No Outlook sync, mail send or
cron was started. The 10322 service was not restarted by this task. The 10011
deployment is unchanged; host-specific bindings and OAuth redirect settings
must be configured for that deployment when its patch is applied.

See [the Korean connection-controls guide](../../../integrations/hermes/connection_controls.md)
for expiry examples, Notion record semantics, exact-source-only automatic
publication limits and every tab/button action.
