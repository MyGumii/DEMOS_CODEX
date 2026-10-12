# Local canonical Second Brain + Codex validation

Date: 2026-08-17 (Asia/Seoul)

Decision: local functional gate passed. This is a personal-workspace,
loopback-only validation result; it is not P7.8, P7.9, P8, a company-provider
pass, or production authorization.

Operational note: this historical three-canary listener was superseded on the
same date by the full isolated 10111 preview documented in
`LOCAL_FULL_SECOND_BRAIN_PREVIEW.md`; its evidence remains reproducible but it
is no longer the active listener.

## Implemented boundary

The canonical Agent now has a concrete runtime composition and model adapter.
For every turn it:

1. accepts and persists the user message under one immutable conversation
   `space_id`;
2. resolves the authenticated user's current canonical `scope_epoch` rather
   than trusting cookie role/organization data;
3. retrieves deterministic FTS candidates, same-space semantic neighbors,
   server pins, explicit contexts, recent conversation messages, and the active
   compact summary;
4. closes the read snapshot before invoking a model;
5. asks the model to select relevant candidates from only that one-space,
   untrusted-reference bundle;
6. persists the assistant completion, model metadata, immutable turn event,
   operation result, audit, and outbox through the existing conversation
   service;
7. fails the turn with sanitized `AGENT_EXECUTION_FAILED` rather than silently
   changing provider or scope when no model returns an answer.

The default production composition delegates provider ordering to the existing
shared runner: all configured company API models in reviewed registry order,
then local GGUF, then Codex only when the public `--codex` launch policy enabled
it. The legacy internal lane name `hynix_api` now classifies API models by
reviewed runtime metadata rather than by hostname substring, so a reviewed
company gateway hostname is not accidentally skipped.

## Isolated local host

`tools/local_second_brain_validation.py` is a separate validation launcher. It
does not change production feature flags. It requires a P0.2-safe absolute data
root outside the repository, creates a fresh canonical database and auth
registry there, binds Codex to an empty read-only/ephemeral working directory,
and uses a token-free Codex-only model registry. Company API and GGUF attempts
are structurally absent from this host.

The final fixture is:

```text
root             D:\CodexTestTemp\SecondBrainLocalValidation-20260817
fixture SHA-256  5814c243e1d22b49000a5762071c860105d61094277c6fdefa7ee1b8bf3b975d
URL              http://127.0.0.1:10111/
account          local-admin
listener PID     50772 at final verification
```

The password is intentionally not recorded in repository evidence. It is
reported directly to the local operator when the fixture is created.

The fixture contains one personal space and one accessible part-A space for
`local-admin`, plus an inaccessible part-B space owned by `local-other`.
Part-A, part-B, and personal records contain distinct canaries.

## Real Codex functional result

The create/login/search/conversation/SSE/readback gate used the actual local
Codex CLI through the public `--codex` parser path. It forced
`provider_order=[codex]` and `model_ids=[codex-local]`. The result was:

```json
{
  "answer_sha256": "deed7b9a9aa1bd34f15babce787c3f5b7a6105971447879580e227bf10e2b693",
  "company_api_called": false,
  "foreign_marker_absent": true,
  "model_provider": "codex",
  "part_marker_present": true,
  "status": "pass"
}
```

The model returned the exact selected-part phrase. Neither the other part's
canary nor the personal-space canary appeared in deterministic retrieval, the
model prompt, or the answer. The command completed in 11.8 seconds. A subsequent
real HTTP login confirmed one accessible part space, one personal space, and
canonical `scope_epoch=2`. `/healthz` returned `status=ready`, the exact fixture
hash, `company_api_called=false`, and
`traffic_scope=localhost_single_user_validation`.

## Permanent tests and regressions

The permanent local validation test uses a fake model only to make the exact
scope/prompt/output assertions deterministic. The separate result above is the
real Codex call.

```text
new executor + local-host tests                         6 passed
provider + production composition + new tests         36 passed
Codex/routing/canonical/browser/conversation selection 120 passed
Python py_compile                                      pass
```

The 120-test selection covers the Codex bridge, provider ordering, canonical
turn executor, isolated local host, canonical retrieval, conversation
repository, route composition, and browser transport. The 36-test selection
adds the production-service dependency injection and reviewed-company-hostname
API classification.

## Frozen source hashes

```text
7fad5cbe4c1f8c13383dbe32eb84645d8eef96507577fbb805165f1be2fbcd40  knowledge_core/services/agent_context.py
f3c585046fdfe815b9983b471f996045c4010e8dc5ee4f6910531397c5b4bf11  knowledge_core/agent_execution.py
c067c8381525bdfa378eda0f333087887f621742f7f691e63081a326208a0470  knowledge_core/agent_runtime.py
286aba43593e4d474e0d8a44400a44f5bc73382b37a9591d51e8286e8cfe5f30  knowledge_core/production_service.py
7dee5223737c68461fdc454eec09776bc9a737925ee17ddbb171eb457c0aa9c1  internal_llm.py
f24d8b23117572523adef14647daaade0c137dc7c5505932b92c6c89ba5d41a3  tools/local_second_brain_validation.py
bf978340b621d45eaab453c67a0954160cbbeca01f8ac78c4707874b8405d300  tests/knowledge_spaces/unit/test_canonical_agent_execution.py
334f054acc76afb7fa4d818e46eeeb63a0dd6eccbd9a8dbfd1955f6fdb3d7da2  tests/knowledge_spaces/integration/test_local_second_brain_validation.py
926fdc2c9b44f53170c387df5286636026ee184b9600ed928b0c04b1bfc30466  tests/knowledge_spaces/unit/test_production_service.py
ebf6dc89cb274e18459c3e489e595547ed0448d79298c75d9311fb89fb16bf19  tests/test_model_fallback_routing.py
```

## Non-claims and company handoff

This workstation does not prove the company OpenAI-compatible endpoint,
production TLS/service-account deployment, target-server 40-client capacity,
or signed off-volume restore. Those are external operational gates, not missing
local Agent algorithms. The exact remaining actions and acceptance artifacts
are in `docs/COMPANY_OPENAI_COMPATIBLE_HANDOFF.md` and
`docs/COMPANY_TARGET_COMPLETION_HANDOFF.md`.
