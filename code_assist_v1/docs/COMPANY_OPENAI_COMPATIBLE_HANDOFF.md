# Company OpenAI-compatible provider handoff

Status: executable handoff prepared; company endpoint execution is intentionally
not performed in the personal workspace.

This packet lets the company-side Codex validate the provider-specific portion
that cannot be exercised from the personal workspace. It does not authorize a
production cutover, live-data migration, feature-flag change, or secret export.

## 0. Personal-workspace validation boundary (2026-08-17)

The following provider-independent behavior has been exercised locally:

- the OpenAI-compatible mock probe passed all non-stream, SSE stream, JSON-only,
  report-seal, duplicate-config, and create-only-output tests;
- routing tests prove configured APIs precede GGUF and Codex, disabled models
  are omitted, and a manual model choice affects only the first attempt;
- a provider failure before any response content falls through, while a stream
  failure after partial content terminates without concatenating another
  model's answer or emitting a successful completion marker;
- `--codex` enables only the final Codex lane and removes the obsolete
  `CODE_ASSIST_DEFAULT_MODEL=codex-local` override;
- the bridge now checks CLI authentication before spawn and has a real
  wall-clock process timeout, including on Windows where the native Codex
  executable is used instead of an unkillable `.cmd` wrapper/child chain.
- an explicitly selected external `CODE_ASSIST_API_CONFIG` now fails startup
  with a stable sanitized error when the file is missing or malformed; it does
  not silently fall back to an unrelated registry.
- the canonical production composition now injects the real one-space Agent
  executor, canonical conversation history/summary materialization, and a
  server-derived principal resolver whenever the reviewed canonical read flags
  are enabled; the model call starts only after the database read closes;
- API-lane eligibility is determined by reviewed runtime metadata rather than
  a hostname substring, so an approved generic company gateway URL remains in
  the API-first lane; a permanent fallback-routing test covers this case.

The installed personal-workspace CLI is `codex-cli 0.147.0` and
`codex login status` now returns `Logged in using ChatGPT`. A live read-only,
ephemeral bridge execution completed in 9.2 seconds and emitted exactly
`BRIDGE_OK:EVIDENCE-CODEX-BRIDGE-20260817`; it inspected or modified no files.
The unauthenticated branch remains permanently tested and still fails before
spawn with sanitized status `401`. This proves local Codex auth/spawn/stream
completion, but not company-provider compatibility or answer quality. Do not
place company provider tokens into Codex CLI login or this repository.

A second live gate exercises Codex through the complete canonical Agent route
stack. The isolated runner created a fresh canonical database, authenticated a
local account, selected Part A, retrieved only that space's candidates, created
a conversation, streamed one turn, and read it back. The answer was exactly
`ORBIT-COPPER-7319`; the Part B marker `FOREIGN-CANARY-4821` and personal marker
`PERSONAL-CANARY-2640` were absent. Its token-free registry contained only the
Codex lane, so no company API could be called. Full evidence is in
`docs/knowledge_spaces/evidence/LOCAL_SECOND_BRAIN_CODEX_VALIDATION.md`.

Focused evidence commands use the repository venv and an isolated OS temp root:

```powershell
$env:TEMP = "$env:LOCALAPPDATA\Temp"
$env:TMP = $env:TEMP
$env:TMPDIR = $env:TEMP

venv\Scripts\python.exe -E -m pytest -q `
  code_assist_v1\tests\test_codex_bridge.py `
  code_assist_v1\tests\test_company_provider_registry_audit.py `
  code_assist_v1\tests\test_model_fallback_routing.py `
  code_assist_v1\tests\test_openai_compatible_probe.py `
  code_assist_v1\tests\test_notion_ai_routing.py `
  code_assist_v1\tests\test_outlook_integration.py `
  code_assist_v1\tests\knowledge_spaces\regression\test_second_brain_chat_characterization.py `
  -p no:cacheprovider
```

Latest local result for that set: `46 passed`. The model-runtime/UI boundary
set additionally passed `13` tests. These are local contract tests, not company
endpoint evidence.

## 1. Authoritative provider policy

The company deployment uses OpenAI-compatible `POST /v1/chat/completions`.
The automatic fallback order is API-first:

1. Gemma-4;
2. Qwen3.6;
3. Kimi-K3;
4. GLM-5.2;
5. local GGUF;
6. Codex bridge only when the application was started with `--codex`.

Codex is absent from the fallback list when `--codex` is absent. Local GGUF is
never tried before the approved company API candidates. A caller-selected model
may be the first attempt only when the product surface explicitly promises a
manual model override; the remaining fallbacks still follow the ordered policy
without cycling or retrying the same model.

The repository's current standalone file still names `GLM-5.1` and does not
contain authoritative Kimi-K3/GLM-5.2 server model identifiers. Do not guess
those identifiers. The company-side Codex must copy the exact values accepted
by the deployed endpoint and archive the sanitized configuration hash.

All four API lanes are part of the company baseline stated for this handoff.
If one is intentionally disabled, the runtime registry must contain
`"enabled": false`, the reviewer must record the separately approved policy
change, and the provider gate is `conditional` rather than an unconditional
pass. Registry insertion order and the per-model numeric `priority` field are
not substitutes for the exact `default_model_priority` array below.

Production services run with `CODE_ASSIST_STANDALONE=0`; without an explicit
override they would load the integrated repository-root `api_config.json`,
which is not this approved company policy. The protected production environment
must therefore set `CODE_ASSIST_API_CONFIG` to the reviewed external company
registry (the example uses `C:\CodeAssist\config\api_config.json`). Verify the
loaded-path/config hash in both service health documents before sending traffic.

## 2. Runtime wire contract to validate

Chat streaming sends:

```json
{
  "model": "<exact deployed model id>",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0,
  "max_tokens": 96,
  "stream": true,
  "tool_choice": "none"
}
```

This small file is for the bounded wire probe only. Do not install it directly
as `CODE_ASSIST_API_CONFIG`, because that environment variable replaces the
whole application registry and the probe file deliberately omits local GGUF.

For the application runtime registry, copy the reviewed token-free structure
of `code_assist_v1/api_config.json` to the protected external configuration
path, then make all of the following changes in one review:

1. replace the checked-in API entries with the four exact company entries
   above;
2. retain the approved enabled local GGUF profile, its top-level `gguf`
   settings, and its file-path environment-variable names;
3. remove the obsolete `GLM-5.1` entry rather than leaving two GLM lanes;
4. give each API entry `"api_key_env": "COMPANY_LLM_RUNTIME_TOKEN"` (or the
   exact approved secret-store environment name); and
5. set the exact array below:

```json
{
  "default_model_priority": [
    "gemma4-company",
    "qwen36-company",
    "kimi-k3-company",
    "glm52-company",
    "gguf-gemma4-31b-q4-k-xl"
  ]
}
```

The array is the authority for automatic model order. `codex-local` must not
appear in it: launch with `--codex` adds one optional Codex attempt after every
configured API and GGUF attempt. Without `--codex`, the registry must contain
zero runnable Codex candidates.

Before starting either web service, run the secret-free registry audit. It
does not resolve the host, read the token environment variable, or contact any
model:

```powershell
venv\Scripts\python.exe -E `
  -m code_assist_v1.tools.company_provider_registry_audit `
  --config C:\CodeAssist\config\api_config.json `
  --output D:\CompanyEvidence\company-provider-registry-audit.json
```

Exit `0` means the exact four API lanes, one approved GGUF lane, environment-
variable authentication, and absence of configured Codex/extra active lanes
match the frozen policy. Exit `2` means a create-only report was produced but
policy drift exists. Exit `3` means the input/output contract failed. Archive
the report hash next to the raw config hash. A passing registry audit proves
configuration only; it does not replace the live wire probe.

The existing consumer requires SSE `data:` records whose text is at
`choices[0].delta.content` and a terminal `data: [DONE]` record. Internal
classification/curation uses the same request with `stream=false` and requires
text at `choices[0].message.content`. JSON tasks require one parseable JSON
object without Markdown fences.

The compatibility probe deliberately exercises all three shapes. It records
only hashes, status classes, counts, model IDs, and the configuration hash. It
does not persist bearer tokens, prompts, response bodies, exception text, host
credentials, or chain-of-thought.

## 3. Prepare a company-only probe config

Create a repository-external UTF-8 JSON file. Use exact deployed model IDs and
endpoint URLs. The example IDs below are placeholders until replaced by the
company-side Codex:

```json
{
  "models": {
    "gemma4-company": {
      "runtime": "api",
      "model": "<exact-gemma-4-model-id>",
      "url": "https://<company-host>/v1/chat/completions"
    },
    "qwen36-company": {
      "runtime": "api",
      "model": "<exact-qwen-3.6-model-id>",
      "url": "https://<company-host>/v1/chat/completions"
    },
    "kimi-k3-company": {
      "runtime": "api",
      "model": "<exact-kimi-k3-model-id>",
      "url": "https://<company-host>/v1/chat/completions"
    },
    "glm52-company": {
      "runtime": "api",
      "model": "<exact-glm-5.2-model-id>",
      "url": "https://<company-host>/v1/chat/completions"
    }
  }
}
```

Do not put a token, username, password, tenant name, internal certificate, or
private prompt in this document. Put the token in a process-scoped environment
variable supplied by the company secret store.

## 4. Run the bounded compatibility probe

From the repository root, using the approved venv:

```powershell
$env:COMPANY_LLM_PROBE_TOKEN = '<injected by company secret store>'
$report = 'D:\CompanyEvidence\openai-compatible-provider-probe.json'

venv\Scripts\python.exe -E -m code_assist_v1.tools.openai_compatible_probe `
  --config D:\CompanyEvidence\company-provider-probe.json `
  --model gemma4-company `
  --model qwen36-company `
  --model kimi-k3-company `
  --model glm52-company `
  --token-env COMPANY_LLM_PROBE_TOKEN `
  --timeout 60 `
  --output $report
```

The stated company baseline probes all four API models. If a separately
approved deployment intentionally disables one, keep the disabled entry in the
reviewed runtime registry, omit it from this wire invocation, and return a
`conditional` decision with the approval reference. Do not use `--insecure`
for HTTPS. It is available only for a separately approved isolated certificate
diagnostic; an insecure pass is not production evidence.

Expected process exit codes:

- `0`: every requested model passed non-stream, stream, and JSON-object checks;
- `2`: the report was produced but one or more compatibility checks failed;
- `3`: configuration/output contract failure; no model compatibility claim.

The output path is create-only. A repeat must use a new evidence path or first
compare and archive the existing report. The tool never overwrites evidence.

Independently verify the archived report without a token or network call. This
binds the report to the exact raw probe-config SHA-256, requires canonical JSON
plus LF, validates every model/check/summary row and its self-hash, and rejects
a fully rehashed model/config substitution:

```powershell
venv\Scripts\python.exe -E `
  -m code_assist_v1.tools.openai_compatible_probe `
  --config D:\CompanyEvidence\company-provider-probe.json `
  --verify-report D:\CompanyEvidence\openai-compatible-provider-probe.json
```

The verifier prints one body-free JSON result. Exit `0` is pass, `2` is a
contract/config-binding failure, and `3` is malformed input. The probe config
itself must contain only the `models` object and exact `runtime`, `model`, and
`url` fields; literal credentials or auth metadata are rejected.

## 5. Validate routing and failure convergence

The wire probe proves individual endpoints, not fallback behavior. Run the
application against an approved company mock/proxy capable of returning the
following controlled outcomes, without production user content:

| Case | Injected outcome | Required attempt order/result |
|---|---|---|
| R1 | Gemma succeeds | Gemma only |
| R2 | Gemma timeout, Qwen succeeds | Gemma, Qwen |
| R3 | Gemma 5xx, Qwen 429, Kimi fails, GLM succeeds | Gemma, Qwen, Kimi-K3, GLM-5.2 |
| R4 | All APIs fail, GGUF available | all configured APIs, then one GGUF attempt |
| R5 | APIs and GGUF fail, no `--codex` | terminal sanitized failure; Codex attempts = 0 |
| R6 | APIs and GGUF fail, `--codex` present | one final Codex attempt |
| R7 | separately approved Kimi/GLM lane disabled | disabled model attempts = 0 and gate decision is conditional |
| R8 | malformed SSE/JSON | fail closed; no partial answer committed as complete |

For every case capture a sanitized ordered attempt ledger containing only:

- registry ID and exact model ID;
- attempt ordinal;
- provider kind (`api`, `gguf`, `codex`);
- start/end UTC and elapsed milliseconds;
- HTTP/status class or bounded stable error code;
- success/failure and whether any response content was emitted;
- configuration/build hash and whether `--codex` was present.

Tokens, request messages, response bodies, raw exception text, DNS details, and
absolute credential paths are forbidden.

## 6. Knowledge/Agent acceptance matrix

Run the same sanitized evaluation corpus through Gemma-4, Qwen3.6, every
enabled big model, local GGUF, and Codex. Retrieval remains local and
deterministic; the provider receives only the already-authorized bounded
context.

Required assertions:

1. selected/cited record IDs are a subset of the supplied candidate IDs;
2. every cited record belongs to the conversation's one resolved space;
3. no hidden, foreign-owner, former-part, provisional, quarantined, or inactive
   record is used;
4. evidence-insufficient cases abstain rather than invent a record;
5. a provider answer never writes graph, item, revision, membership, or
   organization state;
6. deterministic T0/T1 effects remain algorithmic; semantic T2 output is only a
   proposal requiring human review; T3 is never model-executed;
7. stream disconnect/retry never creates a second canonical user or assistant
   message for the same operation;
8. model output and provider failure never occur inside a canonical SQLite
   write transaction.

Do not compare complete natural-language strings. Compare structured IDs,
citations, terminal status, state deltas, and invariant counters. Run each
nondeterministic semantic case at least three times and report the worst result.

## 7. Provider-specific checks

The company-side Codex must explicitly record whether each model supports or
requires adaptation for:

- `tool_choice: "none"`;
- `max_tokens` versus a provider-specific output-token field;
- system-role messages;
- SSE `[DONE]` termination;
- `choices[0].delta.content` and `choices[0].message.content`;
- reasoning fields such as `reasoning_content` that must not be surfaced as the
  final answer;
- exact JSON-only output;
- context length and server-side truncation behavior;
- Unicode/Korean text, NFKC identifiers, Markdown, and long citations;
- 400/401/403/404/408/429/5xx mapping;
- connect/read timeout and retry-after behavior;
- request cancellation after client disconnect;
- TLS/certificate validation and bearer-token handling.

Any adapter exception must be model/endpoint-version scoped and covered by a
fixture. Do not silently weaken the common contract for every model.

## 8. Handoff return packet

Return the following to this repository through the approved company transfer
path. Keep secrets and raw private content outside the repository.

1. exact sanitized probe config bytes and SHA-256;
2. exact external runtime-registry SHA-256 plus the passing create-only
   registry-audit report and `report_sha256`;
3. create-only live probe report and `report_sha256`;
4. ordered fallback cases R1-R8 with build/config hashes;
5. model matrix with exact server model IDs and enabled/disabled status;
6. latency p50/p95/p99 and failure counts for the sanitized corpus;
7. a list of provider-specific adapter changes, each with tests;
8. reviewer identity/UTC and a clear `accepted`, `rejected`, or `conditional`
   decision;
9. confirmation that token/prompt/response/chain-of-thought leakage count is 0.

The company provider gate is complete only when the independent report
validator passes, every enabled model passes its required checks, fallback
order R1-R8 is exact, and the reviewed packet is bound to the same application
build/config used for the test. A Codex-bridge pass in the personal workspace
does not substitute for this packet.

## 9. Local handoff integrity

Verify these transfer hashes before running the company-side commands:

```text
d63da1544ab6df0b9a9b584b84a6c8fcbdaafcfc974a92eb4541629b9d5bde9a  code_assist_v1/tools/company_provider_registry_audit.py
9afb4ddde6574b9279ef4ce93ff7050b4d339eed3afb6230de6fa50dfe9a0853  code_assist_v1/tests/test_company_provider_registry_audit.py
28b99360ea38eb0dafbcd35149cd8ec9f62b7a073c8b9a1d670d739d40eebeb8  code_assist_v1/tools/openai_compatible_probe.py
6cc1457d5c58038e5f96d8c8693687b678d0328eb9c900467e50dedc0eb5b818  code_assist_v1/tests/test_openai_compatible_probe.py
d84411458188b9021475cf76210c2e6198173448f628cc12b0b3153c87e732a1  code_assist_v1/tests/test_model_fallback_routing.py
```

The current combined local provider/routing/bridge/retrieval selection is
`46 passed in 22.53s`; `py_compile` passes. These hashes and counts cover local
policy tooling only and remain distinct from company endpoint evidence.

## 10. 2026-08-26 managed Second Brain worker addendum

The isolated full-preview launcher now supervises two explicit child
processes in addition to the Waitress web process:

- the durable worker owns extraction/curation/automatic-semantic handler
  registry coverage and continuously consumes semantic seed/materialization
  work;
- the projector consumes the canonical outbox and builds Markdown,
  `index.md`, `log.md`, local/aggregate graph, and v12 temporal-footer
  projections;
- `/healthz` reports both child kinds, PIDs, liveness, and provider mode and
  returns `503 degraded` if either retained child is unavailable.

Provider selection is intentionally split. `--codex` is a local validation
mode and restricts the semantic judge to `provider_order=("codex",)` and
`model_ids=("codex-local",)`. Without `--codex`, the semantic worker uses only
the provider-neutral `hynix_api` lane, whose name is the frozen compatibility
identifier for configured OpenAI-compatible HTTP models; it does not silently
fall through to GGUF or Codex. The ordinary application Agent retains its
reviewed provider policy.

A real child-process test used a loopback OpenAI-compatible server through the
copied external registry. Five queued semantic jobs all reached `succeeded`.
The observed request contract was:

```text
model         semantic-stub-v1
roles         system,user
stream        false
tool_choice   none
temperature   0.0
max_tokens    768
```

The response used `choices[0].message.content` with the exact semantic-judge
JSON schema. The temporary registry was restored byte-for-byte after the run.
This proves the application adapter and durable-worker integration against an
OpenAI-compatible implementation, but still does not prove company DNS/TLS,
credentials, exact deployed model IDs, rate limits, latency, or model quality.
Sections 2 through 8 remain the required company return gate.

Related local regression result: `24 passed in 40.11s`, including the full
preview, provider fallback, OpenAI-compatible probe, and real-process projector
replay. A post-health focused rerun passed `11` tests. `py_compile` passes; the
repository venv does not currently contain `ruff`.

```text
db2c63730bb12d4ec477e2b64d6e786a4135a7228f4c3212e5f416234204eeed  code_assist_v1/tools/full_second_brain_preview.py
0669cc1981e72f6a65f531798eec5f6e0332b3f9831802566adede13854e3f58  code_assist_v1/tools/full_second_brain_preview_workers.py
78d4f8a397dc796b8a4727e97778ba4e8bf31f88d6c597f1f4b029e4bab6c0d1  code_assist_v1/tests/knowledge_spaces/unit/test_full_second_brain_preview_workers.py
```

## 11. v13 substantive semantic-edge provider gate

The automatic semantic-edge prompt schema is now
`automatic-edge-judge-v4-relation-policy`.  It is query- and topic-
independent.  A model may select an edge only from substantive domain claims,
entities, procedures, constraints, or evidence present in both candidate
documents.  Matching filenames, formats, headings, account/project/batch
labels, timestamps, provenance text, ingestion instructions, or validation
markers are non-semantic metadata and cannot justify an edge.  Every selected
relationship must include short verbatim evidence quotes from both bodies;
metadata-only overlap must return an empty `selections` array. The canonical
service resolves those quotes to Unicode-codepoint spans and hashes against the
immutable revisions. Company models must not calculate offsets or paraphrase
the evidence text. The request also supplies `relation_direction_policy`; the
model must choose a listed relation/direction combination. Repetition of the
same substantive claims, constraints, or procedures is eligible for the
`related` relation even when the documents otherwise duplicate one another.

Company validation must exercise the same non-streaming JSON contract against
every enabled OpenAI-compatible semantic model.  Include at least:

1. two documents with a shared operational rule expressed in different file
   formats, which must yield a supported relationship with valid body spans;
2. two documents that share only upload/template/account metadata but discuss
   unrelated domains, which must yield no relationship;
3. a mixed candidate set proving that a valid pair can be selected without
   connecting its unrelated neighbor; and
4. three repeated runs per nondeterministic case, recording the worst precision
   result rather than selecting a favorable response.

The personal validation root calls only Codex when launched with `--codex` and
records `company_api_called=false`.  That local evidence checks application
flow and the JSON contract; it cannot establish company-model precision,
latency, rate-limit behavior, or endpoint compatibility.  Return those results
with the provider packet described in sections 6 through 8.

## 12. SBE-8.8 provider-neutral functional baseline

The final isolated v13 matrix in
`docs/knowledge_spaces/evidence/SBE-8.8.md` passed 62 of 62 canonical Agent
turns through Codex Bridge after 190 real DOCX/PPTX/PDF uploads and 220
independent publications. Questions used only bounded natural wording plus a
generic request to quote the exact Wiki rule; they contained no document
title, expected value, evidence marker, or traceability token. The selected
personal/Part/Team/Responsibility body therefore had to supply the asserted
values. Semantic decisions used
`automatic-edge-judge-v4-relation-policy`, with canonical Unicode evidence
locations derived from verbatim body quotes and a generic relation/direction
policy. Metadata-only overlap is not graph evidence. The report records
`company_api_called=false`, unresolved failures zero, and SHA-256
`fe53abdfb9b2c5b79a07a015ad65e6e61b1b59a1f7513fbbec65cfec939794e3`.

Re-run the same provider-neutral assertions against every enabled company
model. Do not copy Codex natural-language answers as golden strings. Bind
selected record IDs, authorized source spaces, expected phrases read from the
stored body, terminal state, citations, semantic evidence spans, and
state-delta counters. Keep expected phrases out of the model prompt. The local
pass does not waive the exact model-ID, authentication, TLS, streaming,
timeout, rate-limit, latency, or repeated worst-case quality gates required
elsewhere in this handoff.
