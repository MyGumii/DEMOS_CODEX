# Hermes client download and prompt installation — 2026-09-11

Scope: AIK-035. A client can obtain the already-tested standalone adapter from
its Second Brain host instead of carrying project files between PCs. This is
an installation guide and a public code artifact, not a background installer or
a new machine API. Existing integration inventory remains 24 paths / 25
operations. Authentication rules, server network bindings and canonical data
are unchanged.

## Public assets and maintenance

The existing `/static/` route serves three new assets:

- `/static/integrations/hermes/install.md`: generated from the Korean
  `integrations/hermes/client_install.md` guide.
- `/static/integrations/hermes/manifest.json`: filename, byte size, SHA-256,
  Python/MCP requirements and integration contract version.
- `/static/integrations/hermes/hermes_mcp.py`: an exact byte copy of the
  standalone client source. No user account, credential, runtime configuration,
  database or server package is included.

`tools/package_hermes_client.py` packages only these fixed source assets.
`--check` and a regression test fail if the published files differ from their
source. Production/shared stores are never packaging inputs. Existing Host
validation runs before public static delivery; the knowledge API still requires
account authentication or a valid scoped bearer token.

The install prompt directs Hermes to discover its actual local profile/Python,
back up and merge only the relevant MCP settings, obtain the adapter from the
user-designated server origin, check size/SHA-256, use a local token file, and
verify API authentication plus MCP tool discovery/status. Directly supplied
tokens can be saved to that local file. Existing local adapter paths can skip
the download. The guide preserves explicit tool restrictions and tells the
user when the running Hermes needs a restart rather than killing that session.

The manifest hash checks file consistency and is not an independent publisher
signature. Remote clients must use reachable trusted HTTPS. The current 10221
launcher remains loopback-bound; serving these assets does not expose the host
to another PC automatically.

## Verification

From `G:\AI_Agent\DEMOS_CODEX`:

```powershell
.\venv\Scripts\python.exe -m code_assist_v1.tools.package_hermes_client --check
.\venv\Scripts\python.exe -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_portable_mcp.py -q -x -p no:cacheprovider
```

Packaging reported **current**. **5 passed in 19.25s**, no skips:

- Four independent client cases: copied/downloaded adapter crossed with token
  file/direct environment. HTTP downloads use no bearer credentials. Each runs
  the one client file without the server package, checks API authentication
  under Python `-I -S`, and uses installed Hermes MCP 2 under isolated Python
  for ten-tool discovery, authenticated status, invalid UUID rejection before
  HTTP and recovery. Synthetic tokens stay out of stdout/stderr.
- Published asset/source byte parity, stale-package detection, existing real
  Flask/auth middleware permitting public downloads at the trusted origin and
  rejecting hostile Host values. Unauthenticated integration status remains
  HTTP 401 while fixture bearer status succeeds.

All writable test databases/tokens were temporary synthetic fixtures. The
installed Hermes distribution was inspected read-only: MCP 2.0.0, Python
requirement >=3.10.

## Existing development server check

Without restarting 10221, an anonymous standard-library HTTP client downloaded
all three assets from `http://127.0.0.1:10221/static/integrations/hermes/`.
Responses contained the expected guide and source bytes. The script is 9,503
bytes with SHA-256:

`40c1bf334f6f3ca72f9ecb8432cb3edd9f24708b047e42e21ec79bc3eb6d2f5a`

No real account token or cookie was used, and no live user data was read or
written. The user's Hermes configuration was not changed in this turn.
End-to-end downloaded adapter execution was tested against isolated HTTP
fixtures on this Windows host, not a second physical PC or a remote HTTPS
gateway. The natural-language installer prompt remains a procedure for Hermes
to execute; it has not been run against the user's live profile here.
