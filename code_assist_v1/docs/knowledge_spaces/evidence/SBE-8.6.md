# SBE-8.6 general-intelligence plus Wiki prompt restoration evidence

Date: 2026-08-25 KST  
State: locally verified isolated-preview repair  
Status integrator: none; `STATUS.md` was intentionally not edited by this repair

## Scope

This repair restores the 10011 answer policy on the canonical Agent path without
weakening the server-owned `CorpusClosure`, conversation-to-space binding, or
private-data authorization boundary.

The canonical executor now reuses the existing `CODING_SYSTEM_PROMPT`,
`GENERAL_ASSISTANT_PERSONA`, and `ANTI_HALLUCINATION` constants from
`prompts.py`. Its canonical addendum treats authorized Wiki excerpts as
high-priority supplemental evidence while allowing ordinary general knowledge.
When Wiki evidence is absent it distinguishes that absence and continues with a
general answer when possible; it still refuses to invent exact private/Wiki
facts.

The selected-Wiki inventory classifier also recognizes Korean interrogative and
existence predicates written as one eojeol, including the exact observed query
`지금 wiki에 뭐가있니?`. This selects the existing bounded authorized inventory
query rather than free-text FTS.

## Owned files and SHA-256

- `knowledge_core/agent_execution.py` — `f7811207943f44cccc6a4e3afad5f60d1efe7de40489f64d906162a987c8d93c`
- `knowledge_core/repositories/hybrid_retrieval.py` — `f0299264a9c77062f1c7d9ceeea015a7ea3b99a549fa03558bb8589bcde7737c`
- `tests/knowledge_spaces/unit/test_canonical_agent_execution.py` — `4ec2dd535a026ee12c87b130674e090bee7fe8d8bd01e18fa3a87a4c782f2334`
- `tests/knowledge_spaces/unit/test_hybrid_retrieval.py` — `706b42dd15f36d795d6d8e7b7a8e18c18392d400595116f6b61211e66e1adb2a`

## Commands and results

From `G:\AI_Agent\DEMOS_CODEX\code_assist_v1`:

```powershell
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_canonical_agent_execution.py `
  tests\knowledge_spaces\unit\test_hybrid_retrieval.py
```

Result: `27 passed in 25.57s`.

```powershell
..\venv\Scripts\python.exe -E -m pytest -q `
  tests\knowledge_spaces\unit\test_canonical_agent_execution.py `
  tests\knowledge_spaces\unit\test_hybrid_retrieval.py `
  tests\knowledge_spaces\unit\test_hybrid_reranking.py `
  tests\knowledge_spaces\unit\test_conversation_wiki_binding_service.py `
  tests\knowledge_spaces\unit\test_conversation_pins_contexts.py `
  tests\knowledge_spaces\unit\test_production_service.py `
  tests\knowledge_spaces\integration\test_full_second_brain_preview.py
```

Result: `75 passed in 99.57s`.

```powershell
.\venv\Scripts\python.exe -E -m code_assist_v1.tools.contract_audit audit `
  --docs-root G:\AI_Agent\DEMOS_CODEX\code_assist_v1\docs\knowledge_spaces `
  --output -
```

Result: report schema v2 `status=pass`, `error_count=0`, 41 requirements,
20 goals, 284 scenarios, 70 OpenAPI paths, and 78 operations.

## Live isolated preview evidence

The existing isolated 10211 preview was restarted in place with its exact prior
roots and `--codex` option:

- data root: `D:\CodexTestTemp\SBE8-Gold-20260825-v1`
- read-only organization source: `D:\CodexTestTemp\SecondBrainFullPreview-20260817-v2`
- listener: `127.0.0.1:10211`
- `/healthz`: `status=ready`, `company_api_called=false`
- marker SHA-256:
  `83c35d85ab9cdaba4359c33d9ccb2fa915c22d134220f99eb7a7327e70200ee1`

Admin live conversation `ae021865-5d39-5348-9c51-53b039ad9b8f` used the exact
UTF-8 query `지금 wiki에 뭐가있니?`. The answer enumerated the eight authorized
documents in the selected personal Wiki, including the five research/paper and
three travel/reservation pages. No foreign scope was included.

Admin live conversation `8134f896-dbba-59a2-905a-d5f2f1741acf` used a query
with no relevant Wiki evidence: `파이썬에서 리스트와 튜플 차이 설명해줘`.
The Agent returned a normal general-knowledge explanation with examples instead
of refusing merely because retrieval was empty.

One earlier PowerShell probe encoded Korean as question marks before HTTP and is
not application evidence. It was superseded by the exact Unicode-escaped UTF-8
probe above.

## Boundaries

- No company API was called.
- No production flag, production port, live 10011 root, schema, migration,
  organization row, Wiki body, graph edge, or authorization policy changed.
- 10121 and the independently approved 10111 dev-pair listener were not changed.
- This is local functional evidence, not target-server load, company-model, or
  production-readiness evidence.
