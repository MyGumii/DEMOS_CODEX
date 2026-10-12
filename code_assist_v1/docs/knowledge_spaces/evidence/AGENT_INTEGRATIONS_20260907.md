# Agent knowledge-loop extension evidence · 2026-09-07

Scope: the independent `agent-knowledge-loop-v1` extension described in
[`../AGENT_INTEGRATIONS_20260907.md`](../AGENT_INTEGRATIONS_20260907.md).
This evidence does not complete the broader SBE goal or authorize the 10011
production rollout. Historical canonical migrations and the frozen SBE scenario
and API counts remain unchanged.

## Development deployment

The existing 10221 host was restarted with the common integration registration
and `CODE_ASSIST_AGENT_WORKER=1`. It continues to use
`D:\CodexTestTemp\SBE9-V13-20260827-v6`; no development corpus was reseeded or
replaced. Its marker SHA-256 is
`69f86898010bcca2b0f114fe7ec4f77e3026da9be14c12048fcc4b9fc7697fb5`.
The final health check reported `ready`, with the existing durable and projector
workers alive. The app process was PID 38476 at verification time.

Authenticated, read-only Edge verification on this host passed six checks:
integration status, background refinement availability, personal Wiki selection,
task review view, viewport overflow and page errors. The validation account had
two selectable personal Wikis. No grant or task was created in this live data root
for testing. Mutating acceptance tests used temporary, isolated stores.

The live capability response reported `notion_configured=false` and
`mail_connector_enabled=false`. Actual account grants, the user's Hermes profile,
Notion/mail account configuration and hourly cron activation are not installed by
this code deployment. No email was sent. 10011 was not patched.

## Data preservation

A SQLite online backup and read-only logical table fingerprints were captured
before restart. The same fingerprint operation after restart produced a
byte-identical report: SHA-256
`e4a2dc3e0ca72b40f9138846bef8c8ac80269ee91c54110720ab41548b30c41f`.

| Table | Rows before / after |
|---|---:|
| app_user | 76 / 76 |
| knowledge_space | 106 / 106 |
| personal_wiki_profile | 95 / 95 |
| knowledge_item | 228 / 228 |
| knowledge_revision | 229 / 229 |
| knowledge_revision_body | 229 / 229 |
| source_artifact | 190 / 190 |
| knowledge_edge | 146 / 146 |
| aggregate_knowledge_edge | 67 / 67 |
| knowledge_explicit_reference | 0 / 0 |

These checks cover the listed tables' contents, not every runtime/audit table.
Private backup data, credentials and local screenshots are excluded from source
distribution by `.gitignore`. The new auxiliary ledger is separate from the
canonical database and belongs in future coordinated backups.

## Verification

**PASS: 173 tests in 271.18 seconds**, exit code 0, on 2026-09-07 (Asia/Seoul).
The JUnit report records no failures, errors or skipped tests. This includes 33
extension cases and 140 existing preview, authentication, workspace-feature and
contract-audit cases. A [sanitized JUnit summary](AGENT_INTEGRATIONS_20260907.tests.json)
is included with this evidence. AIK-001 through AIK-029 cover the extension cases, including
parameterized cases, in three files:

- `unit/test_agent_integrations.py`: grant/owner boundaries, immutable operation
  receipts, expiry/revocation/epoch checks, schema/input rejection, authoring and
  evidence rules, queue/effect recovery, actual separate-process work claims,
  backup/restore and schema drift, follow-up lineage, measured ledger operations.
- `unit/test_agent_integration_core.py`: actual canonical search/read/ask and
  publication; two independent publisher processes; injected crash after the
  canonical commit followed by replay without a duplicate canonical item.
- `unit/test_agent_integration_surfaces.py`: Edge connection creation through
  review/publication/revocation, real Host/session/CSRF middleware, and a real
  stdio MCP session using the installed Hermes Python environment.

The MCP test initialized the installed MCP 2.0.0 server, listed all ten tools and
called authenticated status over an isolated HTTP host. It did not modify the
user's Hermes configuration. Notion tests exercised both real collaborative
IAM/document stores and the legacy atomic page-save implementation, using
isolated data, including authorization and idempotent replay. Outlook observations
and delta cursors were tested with controlled Graph responses; no live mailbox
integration is claimed.

Edge tests loaded the existing app stylesheet and the new dialog in both light
and dark themes. Text contrast passed the 4.5:1 threshold, with no viewport
overflow. Both final theme captures were visually inspected. A contrast issue
found in the first live-host capture was corrected before these final captures.
The CSS served by 10221 exactly matched the corrected local file. The host's
stderr log was empty at the final read.

The real 10221 Codex provider was also exercised separately with a synthetic
document through the existing runtime authoring path. It completed in **12.512 s**,
returned a cited source statement, an uncertain statement and a recommendation,
and preserved the missing-execution warning. There were no blockers or invented
action-result claims; `automatic_eligible` was false. Policy version was
`knowledge-return-authoring-v1`; existing ingest rule version was
`p6.1-deterministic-ingest-v1`. This check made no canonical writes and no company
API call. It is one functional model check, not a model reliability benchmark.

AIK-029 measured 100 authorization operations against an isolated SQLite ledger
on this Windows machine: **p50 9.160 ms / p95 10.656 ms / p99 13.975 ms**, with
**0 SQLite busy errors**. A queued item moved from 1 pending to 0, with a measured
queue wait of **1,011 ms**. Backup and restore took **0.0252 s**. This fixture uses
fake canonical/model adapters; these values do not measure production load or
LLM latency. Projection lag is not applicable to this auxiliary ledger, which
owns no projections. The existing canonical projector was separately confirmed
alive; no production projection-latency benchmark is claimed.

## Reproduction

From the repository root, using its Python environment:

```powershell
$env:AGENT_INTEGRATION_METRICS = "$PWD/.dev/agent-integrations-20260907/ledger-metrics.json"
$env:AGENT_INTEGRATION_UI_EVIDENCE = "$PWD/.dev/agent-integrations-20260907"
.\venv\Scripts\python.exe -m pytest `
  code_assist_v1/tests/knowledge_spaces/unit/test_agent_integrations.py `
  code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_core.py `
  code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_surfaces.py `
  code_assist_v1/tests/knowledge_spaces/unit/test_full_second_brain_preview_workers.py `
  code_assist_v1/tests/knowledge_spaces/integration/test_full_second_brain_preview.py `
  code_assist_v1/tests/knowledge_spaces/unit/test_auth_security.py `
  code_assist_v1/tests/knowledge_spaces/integration/test_auth_flask_security.py `
  code_assist_v1/tests/knowledge_spaces/unit/test_workspace_features.py `
  code_assist_v1/tests/knowledge_spaces/unit/test_contract_audit.py `
  -q -x -p no:cacheprovider `
  --junitxml=.dev/agent-integrations-20260907/regression.xml
```

The local evidence directory contains the JUnit report, test output, metric report,
theme captures, authenticated host smoke result, synthetic-model result and
before/after fingerprints. It is intentionally not a source-package dependency.
The [source manifest](AGENT_INTEGRATIONS_20260907.sources.json) lists 28 source
artifacts for reviewing the later common-code patch. Shared-file hashes identify
the current full files and are not a standalone diff. It contains no runtime data.

## Remaining environment-specific setup

Follow [`../../../integrations/hermes/README.md`](../../../integrations/hermes/README.md)
to issue a connection from the user's own account, choose read/write Wikis and
configure the Hermes MCP entry. Enable only the intended Notion store/mail
connector and configure the desired workflow schedule. The implementation does
not infer that an action was executed from approval, silence or an agent report.
Quote matching checks attribution, not external truth; synthesized claims require
human review. This boundary remains in effect when automation is enabled.
