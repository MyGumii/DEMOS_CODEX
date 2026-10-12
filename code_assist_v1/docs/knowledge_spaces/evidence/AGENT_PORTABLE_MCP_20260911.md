# Standalone Hermes client — 2026-09-11

Scope: AIK-034, client packaging only. No server endpoint, token/authorization
contract, canonical data/schema, Wiki authoring, Notion behavior or host network
exposure was changed. Neither 10221 nor 10011 needs a server restart for this
adapter change; an already-running Hermes MCP process loads the new file on its
next restart.

## Cause and repair

The previous adapter imported only `uid` from the server's `contracts.py` and
inserted a repository-relative parent into `sys.path`. This made a remote client
require the `code_assist_v1` package layout for a standard-library UUID check.

Copying only the adapter into a temporary `client-only` directory reproduced
the failure before the repair: **1 failed in 2.34s**, with
`ModuleNotFoundError: No module named 'code_assist_v1'`. The reproduction used
Python `-I -S` to exclude the working directory, PYTHONPATH and site packages.

The adapter now validates canonical UUID strings locally with `uuid.UUID`,
without a server import or sys.path modification. Invalid values fail before a
tool can construct the HTTP request. The server continues to independently
validate every request and enforce authorization. The client uses ValueError
for local invalid input; no server exception class must be shipped to clients.

Clients copy only `agent_integrations/hermes_mcp.py` to any suitable local path.
They still need Python with MCP 2.x for stdio tool serving; `--check` and
`--drain` use only the standard library. Credentials may be supplied through
either the existing token-file variable or the direct-token variable, never
both. README and example YAML now describe client-local paths and single-file
deployment. Existing configurations pointing at the in-repository script path
remain valid.

## Verification

All account credentials and writable databases used here were synthetic,
isolated test fixtures. No live account token, Wiki, Notion page or mail was
read or mutated by these checks. No model was called against real user data.

From `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_portable_mcp.py -q -x -p no:cacheprovider
```

**2 passed in 10.11s**, covering token-file and direct-environment modes. Each
case copies just the adapter to an otherwise empty client directory, removes
PYTHONPATH/PYTHONHOME and verifies:

- Authenticated `--check` succeeds under `python -I -S` with no site packages.
- The installed Hermes Python/MCP 2 client cannot discover `code_assist_v1` and
  starts the copied adapter under isolated Python mode.
- MCP initialization discovers the exact existing set of ten tools.
- Status reaches the real authenticated integration HTTP routes and identifies
  the fixture grant.
- Path traversal, invalid and noncanonical uppercase UUID values are rejected
  without any HTTP request; a subsequent valid status call still succeeds.
- Neither client stdout nor stderr discloses the fixture token.

```powershell
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integrations.py::test_aik_020_mail_cursor_and_client_credentials_cannot_redirect code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_surfaces.py::test_aik_023_hermes_mcp_stdio_handshake_and_real_api code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_ask.py -q -x -p no:cacheprovider
```

**9 passed in 27.00s**, including existing URL/credential destination constraints,
the original in-repository MCP launch, and canonical Ask response/replay and
authorization checks with deterministic model output. Combined: **11 passed**,
no skips.

This verifies the client without the server package on the same Windows host;
it is not a claim of testing a second physical PC or a deployed HTTPS gateway.
The existing 10221 launcher remains loopback-bound. Remote HTTPS access still
requires host deployment/network configuration and a reachable trusted server
origin. No automatic download/installation endpoint was added.
