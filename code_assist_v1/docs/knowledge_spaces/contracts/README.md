# Machine-verifiable Knowledge Space contracts

This directory is the phase-P0.6 machine-readable companion to
`API_UI_CONTRACT.md`, `SCHEMA_V1.md`, and `TEST_OPERATIONS.md`.

- `contract-manifest.json` identifies every required contract role and the human
  documents audited with it.
- `openapi.json` is the OpenAPI 3.1 interface contract. It describes the target
  `/api/knowledge/v1` and `/api/admin/v1` APIs; it does not claim those routes are
  implemented before their backlog phase.
- `schemas/*.schema.json` are JSON Schema Draft 2020-12 contracts shared by the
  API, workers, materializers, backup tooling, and goal audit.

Run the deterministic, read-only audit from the workspace root:

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit `
  --docs-root .\code_assist_v1\docs\knowledge_spaces `
  --output -
```

The audit fails closed on malformed or duplicate-key JSON, missing or escaping
local references, duplicate OpenAPI operation IDs, unknown requirement IDs,
unresolved scenario ranges, missing/duplicate/untraced requirement, goal, or
scenario IDs, and any count that differs from `GOAL_CONTRACT.json`.

All examples and API timestamps use UTC RFC 3339 strings. The canonical database
continues to store the integer UTC millisecond representation specified by
`SCHEMA_V1.md`; serialization is an API boundary concern.
