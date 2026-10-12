# AIK-037 — On-demand Hermes knowledge skill

Date: 2026-09-12. Scope: Hermes client instructions, native skill, public client
packaging and installation guide. Server API/schema/tool inventory is unchanged.

## Delivered behavior instructions

- `integrations/hermes/second-brain/SKILL.md` activates for an explicit skill/Wiki
  request, related follow-ups or an authorized workflow that requests Wiki use.
- Related turns reuse sufficient evidence; additional/current evidence triggers
  retrieval. Exit requests, unrelated topics and new conversations do not inherit
  an always-query rule. No global memory/config modification is requested.
- Search misses, missing evidence, Ask failures and authentication/network failures
  remain distinct. Ask follow-ups send needed context explicitly: new operation IDs
  still create separate server conversations, and retry IDs retain exact payloads.
- Knowledge/Notion writes require the user's requested workflow scope. Existing
  evidence, execution attribution, server authoring and publication rules remain.
- Adapter initialization instructions and the common workflow prompt no longer
  tell Hermes to search before every user request.

## Verification

Command from the workspace root:

```powershell
& 'G:/AI_Agent/DEMOS_CODEX/venv/Scripts/python.exe' -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_portable_mcp.py -q
```

Result: **6 passed in 55.39s**.

- Four portable client cases cover copy/download, file/direct credentials, isolated
  stdlib checks and the installed MCP 2 runtime's ten tools/status/error recovery.
- Public artifacts are byte-identical to sources, served with existing Host/auth
  boundaries, and regenerated nested skill paths pass package drift checking.
- AIK-037 downloads the skill through the real static app route, verifies manifest
  hash/size, and installs into a synthetic temporary Hermes profile. The installed
  Hermes runtime validates frontmatter and lint, discovers `/second-brain`, loads
  the complete skill, and expands the slash invocation preserving the user question.
- `package_hermes_client --check` passed. Standalone Hermes skill lint was clean.

These are loader, packaging and MCP tests, not an LLM behavior evaluation or proof
that a model always follows the skill. Topic continuity is conversation guidance,
not a deterministic router. MCP startup and necessary API/model latency remain.

## Local installation and readiness

- Installed `D:/Hermes/skills/second-brain/SKILL.md`; native discovery in the actual
  profile resolves `/second-brain` to this path with clean lint.
- Updated `D:/Hermes/tools/second_brain/hermes_mcp.py` after matching its prior
  source hash. Backup: `hermes_mcp.pre-skill-20260912.bak` in the same directory.
  Existing YAML and token file were retained. Running Hermes was not interrupted;
  `/reload-skills` loads the new skill in the current CLI. Updated adapter startup
  instructions take effect on its next process start.
- 10221 was not listening during the final read-only download check. Started its
  existing development launcher with the marker-matching
  `D:/CodexTestTemp/SBE9-V13-20260827-v6` root and `--agent-worker`, without seeding
  or migration. Server PID 30928, durable worker 32244, projector 8548.
- Live public manifest/skill download hash, size and installed source parity passed.
  Health returned HTTP 200; the installed adapter authenticated using the existing
  token. No test submission, Ask generation, mail send or live cron was created.
  The successful bearer check can renew expiry under the existing opt-in policy.
- 10011 and 10322 processes/configuration were not changed. Future server packaging
  includes `static/integrations/hermes/second-brain/SKILL.md` and its manifest entry.
