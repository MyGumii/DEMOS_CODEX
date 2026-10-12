# AIK-038–044 — Personal and personal-plus-shared knowledge sets

Date: 2026-09-13. Authorized development target: existing 10221 preview.
This independent agent-integration extension does not change canonical schemas,
SBE contracts/flags, production data, or graph/index/log projection ownership.

## Behavior

- New grants explicitly opt into version 2 with exactly two retrieval modes:
  `personal` and `personal_shared`. Both require one or more owned personal Wikis.
  Shared mode adds one currently authorized Part/Team/Responsibility view. Old
  grants remain bound to their original personal read/write Wikis.
- Status returns selected Wiki/view metadata, not Wiki bodies. Search uses the
  canonical lexical/hybrid service for each selected personal Wiki plus one shared
  aggregate. Descendants are not queried individually. Results preserve physical
  source, item, revision and selected-view IDs; duplicate identities are removed.
  The hybrid query-expansion adapter reuses its query cache across these calls.
- One serialized JSON budget covers the set: 16 KiB default, 4–96 KiB configurable.
  Each excerpt is capped at 1200 UTF-8 bytes. Truncation/omitted counts are disclosed.
  Counts describe returned canonical candidates, not an exhaustive corpus count.
  Reads use the search result's selected view; inherited physical source IDs do
  not grant direct access or bypass personal-only selection.
- New Ask retrieves selected evidence and invokes the configured synthesis runner
  once. An existing hybrid query-expansion model call may precede synthesis. No-hit
  retrieval returns an honest deterministic response without synthesis. Long Ask
  input retains the complete model question while normalizing lexical input to
  500 NFKC characters / 32 terms and disclosing query truncation. Source IDs are format-validated;
  this is not independent semantic fact verification.
- Version-2 answers, citations and exact request scope use the auxiliary effect
  ledger (`conversation_id=null`). Replays return the saved result; changed
  question/selection under the same operation ID conflicts. Canonical Agent
  conversations remain bound to one selected Wiki/view. V1 Ask is preserved.
- Grants separately allow personal publication targets and optional own native
  organization/modes. Submission freezes read mode and target; follow-ups inherit
  the target unless explicitly changed within the grant. Publication uses existing
  canonical personal, Part, atomic personal+Part or higher-native services.
  Team/Responsibility authority is exact to its current role episode and does not
  invent lower membership. Administrator status does not provide ordinary writes.
- UI exposes two modes, multiple personal Wiki checkboxes, authorized shared view,
  permitted registration targets, and per-submission selection. Hermes adapter
  and on-demand skill describe the same contract. Narrowing the conversational
  scope also instructs Hermes to exclude previously retrieved out-of-scope context.
  Skill compliance remains model guidance, not a deterministic router.

## Verification

All fixtures are synthetic temporary canonical/auxiliary stores. No live Wiki,
Notion database, token or mailbox is used as a writable test fixture. Model output
in regression tests is deterministic fixture data; no live email or cron runs.

| IDs | Evidence |
|---|---|
| AIK-038 | Real personal/shared closure selection, owned Wiki subset, physical provenance in both lexical/hybrid lanes, one expansion per query, response-byte budget, inherited read and HTTP/legacy scope boundaries |
| AIK-039 | Real personal/Part/dual/Team/Responsibility publication receipts and idempotent replay; dual targets have distinct item histories |
| AIK-040 | Cited set Ask, exact replay, long question, no-hit behavior, grant isolation, revoked in-flight result rejection, invalid citations, frozen targets and follow-up inheritance |
| AIK-041 | Headless Microsoft Edge against real canonical services: exactly two modes, multiple Wikis, own organization/dual allowance, chosen submission mode/target, no browser errors or horizontal page overflow |
| AIK-042 | Installed Hermes MCP 2 runtime using an isolated copied adapter without server package: ten tools, status, narrowed search, inherited read, forbidden read, cited Ask/replay, target-bearing submission |
| AIK-043 | Windows spawn, two publishing processes, normal concurrency and simulated canonical-commit/auxiliary-receipt loss: exactly two total new items and matching recovered receipts |
| AIK-044 | Twenty synthetic combined-search samples on this Windows host; measured JSON bytes and SQLite busy errors |

The test file is `tests/knowledge_spaces/unit/test_agent_knowledge_sets.py`.
Regression commands also include the existing integration, Ask, core, browser,
connection-control, token-reveal and portable-client test modules.

Final broad regression: **84 passed in 222.98s**, using:

```powershell
& 'G:/AI_Agent/DEMOS_CODEX/venv/Scripts/python.exe' -m pytest code_assist_v1/tests/knowledge_spaces/unit/test_agent_knowledge_sets.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integrations.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_ask.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_core.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_surfaces.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_connections.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_token_reveal.py code_assist_v1/tests/knowledge_spaces/unit/test_agent_integration_portable_mcp.py -q
```

Two final targeted corrections followed that run:

- Long/multiline Ask input must satisfy both canonical character and term limits.
  `test_agent_knowledge_sets.py -k '040 or 042'`: **4 passed in 35.79s** after
  normalization/bounding, including unchanged complete model input and real MCP.
- Flask's host-specific ASCII escaping/pretty printing must not expand the
  budgeted response. V2 search now returns exactly its measured UTF-8 JSON bytes;
  v1 serialization is unchanged. `-k 'http_modes or 042'`: **2 passed in 25.95s**
  after this correction, including exact HTTP byte parity and real MCP decoding.

`package_hermes_client --check` passed. Native Hermes skill validation/loading and
public manifest parity are included in the 84-test regression. Edge screenshots
were visually inspected at `D:/CodexTestTemp/AIK-KnowledgeSets-20260913/`.

AIK-044 Windows search measurement (20 runs, lexical fixture, no model/network):

| Measurement | Value |
|---|---:|
| p50 | 945.41 ms |
| p95 | 982.54 ms |
| p99 | 995.30 ms |
| SQLite busy/locked failures | 0 |
| Combined JSON response | 5,178 bytes |
| Selected views / returned documents | 3 / 7 |

Fixture: two personal Wikis plus one Responsibility view containing two Teams and
two Parts. These are small-fixture service timings on this Windows host, not a
large-corpus/LLM latency guarantee. Queue/projection lag and restore timing were
not remeasured for this read-only benchmark; the existing worker/recovery/backup
regressions passed in the broad suite. JSON measurements are preserved in the
same evidence directory as `search-metrics.json`.

## Deployment contract

No new routes or canonical migrations: existing 25 paths / 26 operations remain.
Version-2 fields are opt-in extensions to the existing endpoints. Public client
artifacts retain manifest schema 1 and add knowledge-set capability version 2.
Old browser/API clients and old single-Wiki grants keep their authority.

10221 uses the existing marker-matching development root and launch command.
10011 receives the common code/public assets only when separately patched; this
packet does not copy the development ledger or credentials into production.
The local Hermes adapter/skill can be updated while retaining YAML and token.
An existing version-1 token must be replaced by a newly selected version-2 grant
to use additional Wiki/organization access. Running Hermes loads the adapter on
its next start; ongoing conversations are not forcibly terminated.

## Local completion

- Installed adapter `D:/Hermes/tools/second_brain/hermes_mcp.py` and skill
  `D:/Hermes/skills/second-brain/SKILL.md` match the public/source SHA-256 bytes.
  Existing files were hash-checked before update, then preserved as
  `hermes_mcp.pre-sets-20260913.bak` / `SKILL.pre-sets-20260913.bak` respectively.
  YAML and the existing token were retained; running Hermes was not interrupted.
- Started the existing `D:/CodexTestTemp/SBE9-V13-20260827-v6` development root
  through `serve_existing_second_brain_preview --port 10221 --agent-worker`,
  without reseeding or canonical migration. Final server PID 34224, supervisor
  worker launcher PIDs 5576 / 33204. Prior development workers were stopped via
  their supervisor stop file before the final server restart.
- Final health HTTP 200, active background refinement, advertised set version 2,
  authenticated existing grant, and public/installed client hashes all passed.
  Existing Hermes token still resolves to `admin` and version-1 single-personal
  scope. No raw credential was printed and no broader token was auto-issued.
- Live readiness inspection read status/downloads only, with no Ask generation,
  source submission, publication, mailbox send or cron. Successful bearer status
  can renew the existing expiry under the user's existing opt-in setting.
- Existing 10322 Notion binding remains configured. 10011 was not restarted or
  patched by this packet; future common-code deployment includes these assets.
