# Agent knowledge-loop extension · 2026-09-07

Owner: this extension's implementation and evidence integrator. User-authorized
development target: the existing 10221 canonical-v13 preview. The hourly email
workflow is one example of the reusable Hermes ↔ Second Brain ↔ Notion loop.
This packet does not change the frozen SBE goal/scenario counts, historical
canonical migrations, or the outstanding production rollout gates.

## Contract

`agent-knowledge-loop-v1` adds a scoped machine API and browser review UI.
An authenticated canonical user issues a grant bound to their account, current
scope epoch, one personal read Wiki, one personal write Wiki, explicit scopes,
expiry, optional Notion location and optional attributed-source auto-publication.
Wiki identifiers are existing personal-Wiki identities, not filesystem paths.
Different read/write Wikis are supported. Existing one-Wiki-per-conversation
behavior remains intact. No company/Part aggregate scope is silently added.

The 2026-09-13 opt-in knowledge-set extension adds `knowledge_sets_version=2`
grants: `personal` (one or more selected owned personal Wikis) or `personal_shared`
(those Wikis plus one authorized Part/Team/Responsibility view). Shared-only is
invalid. Existing grants retain their original single-Wiki scope and tokens.
All selected sources are resolved by canonical authority; aggregates retain real
source identities. The extension's new retrieval response defaults to one 16 KiB
serialized JSON budget across the selected set (4096–98304 configurable).
Optional personal IDs narrow a grant; they never expand it. No whole-Wiki preload.

New grants also freeze permitted personal publication targets and optional exact
native organization/modes. A submission freezes its `read_mode` and
`publication_target`. Personal, organization, or personal+Part writes call the
existing canonical service. Team/Responsibility leaders can publish only to their
own native level; there is no synthetic descendant membership or admin bypass.
Dual publication creates two independent items atomically. Follow-ups inherit
their predecessor's target unless an explicitly allowed target is supplied.

Knowledge-set Ask searches the selected views then creates one cited synthesis;
the existing hybrid query-expansion cache avoids expansion per Wiki. Its answer,
source/revision/view IDs and exact request selection are stored in the auxiliary
effect ledger, with `conversation_id=null`. It does not create a canonical
conversation spanning multiple selected Wikis. V1 Ask keeps its canonical single
conversation behavior. Citation IDs are format-validated, not a semantic truth
proof. No-hit responses distinguish this search from corpus-wide absence.

Scopes: `knowledge:read`, `knowledge:ask`, `results:write`, `notion:write`,
`mail:read`. Revocation, expiry, account state, epoch and current Wiki authority
are checked on use. Before final Wiki publication, an auxiliary write fence
serializes grant revocation with the short canonical commit. Staging/model work
occurs outside this fence. Canonical account/space authority is independently
revalidated by the publication transaction.

Only explicitly registered endpoints accept delegated Bearer credentials.
Trusted Host validation runs first. Cookie or Origin plus Bearer is rejected;
invalid machine credentials never fall back to the browser's account. Browser
connection management, attestations and review keep existing session/CSRF checks.
Tokens cannot approve a candidate or impersonate a user's completion report.

## Sources, actions and authoring

Submission envelope: UUID `operation_id`, generic `kind`, `title`, 1–24 sources,
optional `result`, up to 32 actions, `external_key`, and same-grant `followup_of`.
Original source text and SHA-256, external identity/version, proposed result and
action completion conditions are immutable. Limits are byte-based: 100 KB per
source/result, 500 KB submission, 550 KB HTTP JSON. Duplicate JSON keys, authority
fields and unknown schema fields are rejected. Lookup responses disclose Wiki
item/revision identity and the exact 96 KiB excerpt budget.

Source kinds and all externally supplied execution reports remain untrusted
submitted references. Server evidence is written through a separate connector
path. Action projections distinguish approval, user-reported completion,
provider-observed reply, user-reported outcome and cancellation. Missing evidence
is unknown. Conflicting completion/cancellation evidence forces review. A sent
reply proves neither delivery nor the completion of every business action.

`authoring.py` reuses `CODING_SYSTEM_PROMPT`, `GENERAL_ASSISTANT_PERSONA` and
`ANTI_HALLUCINATION`, adds the versioned structured authoring policy, then applies
the existing `lint_markdown_structure` / `PUBLICATION_PREVIEW_RULE_VERSION`.
It follows the current canonical runtime's provider/model selection. Every claim
has exact source ID/quote citations. Unknown sources, nonmatching quotes and
unsupported execution claims block approval. Quote matching is not semantic
truth verification: generated synthesis and interpretations require a person to
compare the claim with its evidence. Existing canonical curation still applies.

Default states: `queued → processing → review → ready → publishing → published`.
`failed` can be retried; `rejected` is preserved. New execution evidence invalidates
the draft and its review and returns the task to `queued`. Publishing/published
versions cannot accept changed evidence; use an explicitly linked follow-up run.
Auto-publication, when enabled by the owner, renders only attributed literal
source quotations. It excludes generated summary/paraphrases, agent output and
execution claims, and never applies destructive curation. `published` is the
canonical registration receipt; downstream curation/public visibility is separate.

Publication Markdown retains the task link, source identities/hashes and any
parent-task link. Complete submitted originals and evidence remain in the
authenticated ledger API and must be retained in the backup set. The generated
Wiki must not be treated as independent corroboration of its own original source.

## Persistence and recovery

`core/agent_integrations.db` is an auxiliary application ledger, following the
existing separate feedback-store precedent. Canonical Wiki state remains in
`knowledge_core.db`; this module never writes Wiki projection files or canonical
tables directly. Its publication adapter calls the ordinary canonical services.
The ledger has a serialized, versioned, checksumed composition migration and
validates its actual physical schema. No migration runs per HTTP request.

Ledger tables: `integration_schema`, `agent_connection`, `integration_operation`,
`integration_run`, `integration_evidence`, `integration_event`, `integration_cursor`.
The cursor table is reserved; Outlook's opaque cursor is returned to its workflow
consumer, which must advance it only after durable work receipts. The event table
is the transactionally committed audit/outbox ledger; external notification
delivery is not automatically enabled by this extension.

Tokens are HMAC-derived from the server's signing key; only token hashes are
stored. Authenticated creation retries and an explicit owner-session reveal can
reproduce the same token. Reveal checks the reconstructed token hash against the
stored hash; missing/changed signing keys cannot return a substitute token.
Operations record request hashes and exact replay results. External effects first
reserve an immutable intent. Queue selection and Notion snapshot versions cannot
change on retry. A fenced work claim has a 15-minute lease and monotonic generation.
Newly submitted evidence must match the claim's captured version. Stale workers
cannot commit. Publication rendering mode is frozen before the first attempt;
the canonical operation IDs derive from the immutable run/draft/mode, so a crash
between canonical commit and ledger receipt does not create a second item.

`CODE_ASSIST_AGENT_WORKER=1` starts a consumer in the app process. It scans durable
work, recovers expired claims and resumes interrupted publication. Failed model
jobs await explicit retry; inaccessible grants cannot block later queue pages.
Multiple consumers use the same database fencing. `hermes_mcp.py --drain` is an
alternative grant-scoped consumer. Stopping the host preserves queued work.

## Mounted API

Prefix: `/api/integrations/v1`. As extended on 2026-09-11, this independent
extension has **25 paths / 26 operations**, including 13 machine-enabled
operations and 13 browser-only operations (originally 19/20 and 13/7).
It does not amend the frozen 72-path/80-operation SBE contract.

| Method / suffix | Authority | Purpose |
|---|---|---|
| GET `/status` | browser or grant | Capabilities/worker availability |
| GET `/wikis` | browser | Paginated personal Wiki selection |
| GET `/notion/targets` | browser | Current editable Notion locations |
| GET, POST `/connections` | browser | List / issue grant |
| POST `/connections/{connection_id}/revoke` | browser | Revoke grant |
| POST `/connections/{connection_id}/renewal` | browser | Versioned, idempotent sliding-expiration opt-in/out |
| POST `/connections/{connection_id}/token` | browser | Current-owner token reveal with no-secret audit/replay metadata |
| GET `/outlook/status` | browser | Account connection and safe configuration status |
| POST `/outlook/connect` | browser | Start a single-use Microsoft OAuth flow |
| GET `/outlook/callback` | browser | Complete the existing protected OAuth flow |
| POST `/outlook/disconnect` | browser | Disconnect this account's server Outlook login |
| POST `/knowledge/search` | knowledge:read | Bounded versioned excerpts |
| GET `/knowledge/items/{item_id}` | knowledge:read | Current authorized body |
| POST `/knowledge/ask` | knowledge:ask | Canonical one-Wiki conversation |
| POST `/connections/{connection_id}/runs` | results:write | Immutable submission/follow-up |
| GET `/runs` | results:write | Paginated history, current-grant filtered |
| GET `/runs/{run_id}` | results:write | Sources, evidence, draft and receipts |
| POST `/runs/{run_id}/process` | results:write | Refine / replay |
| POST `/connections/{connection_id}/process-next` | results:write | Stable queue selection / replay |
| POST `/runs/{run_id}/evidence` | browser | Attributed user attestation |
| POST `/runs/{run_id}/review` | browser | Exact version/hash approve/reject |
| POST `/runs/{run_id}/publish` | browser or results:write | Reviewed commit; grant lane allows literal-source mode only |
| POST `/runs/{run_id}/notion` | notion:write | Immutable version snapshot |
| POST `/mail/inbox` | mail:read | Optional read-only Outlook delta |
| POST `/runs/{run_id}/observe-outlook-reply` | mail:read + results:write | Independently observed reply, or unknown |

All effect mutations use `operation_id`. Evidence/review also bind expected row
version; review binds draft hash. No-store/nosniff responses and stable error
codes distinguish invalid input, forbidden access, revision/idempotency conflicts,
stale work, missing evidence, model failure and unavailable connectors.
The browser OAuth lifecycle uses the existing encrypted, single-use state flow;
it does not store replayable authorization URLs or start mail sync jobs.
Connection creation accepts optional Boolean `auto_renew` (absent means false).
Successful authenticated machine responses renew an opted-in unexpired grant to
now plus `expires_days`; browser inspection, background processing and failed
requests do not renew. The renewed expiry is returned in `X-Agent-Token-Expires-At`.
Renewal and its audit commit atomically after current authorization checks;
expiration, revocation and account/Wiki fences cannot be bypassed by renewal.

Token reveal accepts only `operation_id` and the owned connection path ID. It
uses existing browser Host/session/CSRF checks, is never enabled for bearer
tokens, and revalidates account epoch, selected-Wiki access and active grant
state on every call including replay. `connection.token_viewed` and the replay
receipt commit together without the token. Responses are private/no-store with
no-referrer. Viewing does not rotate or renew the token. Existing connections
work without migration or reissue. The UI fetches the token only on explicit
`토큰 보기`, supports copy/hide and discards late responses after navigation.

### Ask answer delivery (2026-09-11)

`POST /knowledge/ask` returns `conversation_id`, `state`, `answer` and `response`.
For a completed turn, `answer` is the Markdown text from that turn's committed
assistant message; `response` retains the existing canonical metadata unchanged.
The core stores these separately. The adapter resolves the exact
`assistant_message_id` through authorized conversation services on both first
delivery and replay, including turns completed before this fix. No schema change
or message rewrite is required. Missing completed answer text yields
`503 ANSWER_UNAVAILABLE`; failed/cancelled turns have a null answer, with the
canonical error included when present. Current grant and Wiki access are checked
again before answer delivery.

The caller's UUID is namespaced by connection. The same ID and question replay
the committed answer; a different question with the same ID conflicts. A new ID
starts a new conversation. Ask does not automatically continue prior questions
or certify the factual truth of model synthesis. The MCP tool passes this response
through unchanged and documents these fields in its tool description.

## Deployment, backup and boundaries

Code: `agent_integrations/`, `static/agent_integrations.{js,css}`,
`integrations/hermes/`; small shared registrations in `app_code.py`, `auth.py`,
`static/index.html`, and the explicit-route preview composition. Both ordinary
production composition and the preview call the same registration hook. The
entry button stays hidden on a running origin that has no integration API.

- `CODE_ASSIST_AGENT_INTEGRATIONS=0` disables the package at startup; canonical
  services/flags must otherwise be available. This does not open global SBE flags.
- `CODE_ASSIST_AGENT_WORKER=1` enables background authoring. Default is 0 for
  generic hosts; enabled explicitly on the 10221 development host.
- Notion/Outlook environment bindings and account login are documented in
  [`../../integrations/hermes/README.md`](../../integrations/hermes/README.md).
  Neither missing credentials nor an unavailable collaborative store causes a
  fallback to a different account/data root.
- Development restart: `tools/serve_existing_second_brain_preview.py`. Its
  allowed development ports are 10221/10223, and it opens only an existing,
  marker-matching root. It does not reseed, migrate or copy personal Wiki data.
- To patch 10011 later, ship the common code and target environment configuration.
  Initialize the target's auxiliary DB, retain its signing key, and issue target
  account grants. Do not overwrite production data with the development ledger.
- Quiesce writers before coordinated backups. Include canonical DB/blobs and all
  ordinary data plus `agent_integrations.db`, its committed SQLite WAL state,
  the server signing key and the configured Notion store. Use SQLite online
  backup or a quiesced consistent copy, never copy only an active main DB file.
  Restore the whole environment before consumers resume. Restoring grants needs
  the matching signing key. Replays reconcile the canonical/auxiliary commit gap.
- Rolling code back can disable the feature flag; retain the ledger and
  published canonical records. No automatic deletion or reverse schema migration.

No external email is sent; no live hourly cron is activated as part of the code
installation. A user's account/Wiki/Notion/mail selection and actual Hermes
profile setup complete the individual connection. Non-Outlook workflows can
use Hermes's own source connectors through the same submission contract.

## Acceptance

| IDs | Coverage |
|---|---|
| AIK-001–005 | Owner/grant boundaries, token storage/revocation, epoch/archive fences, retries, malformed authority fields |
| AIK-006–011 | Human review, exact quotations, literal-source automation, evidence invalidation, stale work, invalid model output |
| AIK-012–014 | HTTP injection/auth separation, non-email task, real process claim exclusion |
| AIK-015–017 | Real canonical search/read/ask/publication, frozen queue receipts and publication mode |
| AIK-018–020 | Real collaborative Notion ACL/replay, Outlook sent proof/unknown, cursor/credential destination validation |
| AIK-021–023 | Real Edge browser including themes, real existing Host/CSRF middleware, installed Hermes MCP 2 interoperability |
| AIK-024–026 | Real concurrent canonical publication, commit-gap recovery, worker restart, auxiliary backup/schema drift |
| AIK-027–028 | Same-grant follow-up lineage, real legacy Notion ACL/atomic save and collaborative-mode guard |
| AIK-029 | Independent route inventory, measured Windows ledger latency/queue/backup profile |
| AIK-030 | Ask body/metadata, durable and legacy replay, exact message selection, failed/cancelled/missing body, grant isolation/revocation, installed Hermes MCP ask |
| AIK-031–033 | Active-use renewal and opt-in changes, independent Notion root/hierarchical targets, browser-only Outlook connection lifecycle |
| AIK-034 | Single copied MCP client file without server packages, isolated stdlib check, both credential modes, installed MCP discovery/status and invalid UUID handling |
| AIK-035 | Public client download/guide/hash parity, stale-package detection, unchanged static Host/API auth boundaries, downloaded isolated MCP in both credential modes |
| AIK-036 | Existing token owner reveal, current authority/key/replay checks, no-secret audit, unchanged expiry, real browser CSRF and reveal/copy/hide UI |
| AIK-037 | Public skill hash/download, isolated native Hermes validation, discovery, full loading and slash invocation |
| AIK-038–040 | Real personal/shared scope and budgets, lexical/hybrid provenance, exact native/dual publication, set Ask, replay/revocation, frozen targets and malformed inputs |
| AIK-041–042 | Real Edge two-mode/multi-Wiki/target UI; standalone installed MCP 2 search/read/Ask/submission through the real canonical services |
| AIK-043–044 | Two-process dual publication and commit-receipt recovery; Windows synthetic combined-search latency and response bytes |

Evidence is recorded in
[`evidence/AGENT_INTEGRATIONS_20260907.md`](evidence/AGENT_INTEGRATIONS_20260907.md).
Ask delivery regression evidence is recorded separately in
[`evidence/AGENT_ASK_20260911.md`](evidence/AGENT_ASK_20260911.md).
Connection control and Notion host binding evidence is in
[`evidence/AGENT_CONNECTIONS_20260911.md`](evidence/AGENT_CONNECTIONS_20260911.md).
Standalone client evidence is in
[`evidence/AGENT_PORTABLE_MCP_20260911.md`](evidence/AGENT_PORTABLE_MCP_20260911.md).
Client download/installation evidence is in
[`evidence/AGENT_CLIENT_DOWNLOAD_20260911.md`](evidence/AGENT_CLIENT_DOWNLOAD_20260911.md).
Owner token-reveal evidence is in
[`evidence/AGENT_TOKEN_REVEAL_20260911.md`](evidence/AGENT_TOKEN_REVEAL_20260911.md).
On-demand Hermes skill installation evidence is in
[`evidence/AGENT_HERMES_SKILL_20260912.md`](evidence/AGENT_HERMES_SKILL_20260912.md).
Knowledge-set implementation and verification are recorded in
[`evidence/AGENT_KNOWLEDGE_SETS_20260913.md`](evidence/AGENT_KNOWLEDGE_SETS_20260913.md).
