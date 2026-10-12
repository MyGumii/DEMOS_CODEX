# API, authorization, and UI contract

> Successor contract state: registered by SBE-0.1 and amended by the approved
> SBE-6.2 authority read contract and SBE-8.12 original-source contract.
> The current 72-path/80-operation
> machine surface and 31 stable public errors are now authoritative together
> with `second_brain_evolution/PRODUCT_CONTRACT.md`. Registration does not expose
> a route: all successor runtime flags remain false until their owning packets
> implement and gate the behavior.

## 1. General API rules

New shared-knowledge endpoints use the `/api/knowledge/v1` namespace and are registered from one shared composition root in both web applications when needed. Organization administration uses `/api/admin/v1`.

Common rules:

The machine-readable companion is `contracts/openapi.json`; shared request,
response, cursor, preview, job, projection, backup, and error shapes are under
`contracts/schemas/`. The current audit requires this document's 80 normalized
operations across 72 paths, including 27 GET operations, and the stable error
table to match those contracts exactly. The frozen v7 baseline is 52 paths/56
operations; the existing successor adds 18/22 and SBE-8.12 adds 2/2.

- Actor identity comes only from the authenticated server session.
- `user_id`, `space_id`, role, or part supplied by a client is never sufficient authorization.
- Every retryable mutation carries an opaque UUID `operation_id`. An `Idempotency-Key` header may be accepted as an alias, but if both are present they must match. Lease heartbeat is the sole phase-1 exception: token, fence, holder session, and lease identity make it a conditional renewal, and it writes metrics but no operation-dedup or audit row.
- State-changing item commands carry `expected_row_version`; content changes also carry `expected_revision_id`.
- Lease-protected writes carry lease token and fencing generation in headers, never query strings.
- Except for the item-scoped source-artifact list specified in §3, list endpoints return `{items, next_cursor, has_more}` with a bounded limit and an opaque cursor over the endpoint's documented immutable sort tuple (normally `created_at DESC, id DESC`). They default to excluding inactive/provisional/quarantined items.
- Every JSON response includes top-level `request_id`; every JSON mutation response includes resolved top-level `operation_id`. Binary attachments and streaming responses carry request correlation in the `X-Request-ID` header; original-source bytes are not wrapped in a JSON response.
- Server timestamps are UTC; the UI localizes them.
- Cookie-session mutations require a same-origin CSRF token. Production cookies are `Secure`, `HttpOnly`, and an explicitly tested `SameSite` value; POST/SSE initiation validates `Origin`/`Host` before streaming.

Standard error body:

```json
{
  "request_id": "...",
  "error": {
    "code": "REVISION_CONFLICT",
    "message": "The item changed after editing began.",
    "retryable": false,
    "details": {
      "current_row_version": 7,
      "current_revision_id": "..."
    }
  }
}
```

Stable errors:

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `VALIDATION_ERROR` | Missing/invalid input |
| 401 | `AUTHENTICATION_REQUIRED` | No valid login session |
| 403 | `FORBIDDEN` | Actor lacks capability |
| 404 | `NOT_FOUND` | Resource absent or intentionally undisclosed |
| 409 | `SCOPE_STALE` | Conversation/membership epoch is no longer valid |
| 409 | `STATE_CONFLICT` | Requested lifecycle transition is invalid |
| 409 | `REVISION_BODY_UNAVAILABLE` | A retained revision body cannot be read or does not match its canonical hash/size evidence |
| 409 | `IDEMPOTENCY_CONFLICT` | Operation ID was reused with different input |
| 409 | `REVISION_CONFLICT` | Expected revision or row version is stale |
| 410 | `PURGED_TOMBSTONE` | Identity remains but body was purged |
| 413 | `DIFF_TOO_LARGE` | The requested comparison exceeds the bounded server-side diff limits |
| 422 | `CURATION_BLOCKED` | Blocking lint/finding prevents publication/action |
| 423 | `LEASE_HELD` | Another valid editor holds the item |
| 423 | `LEASE_LOST` | Token/fence/session/expiry no longer matches |
| 428 | `OPERATION_ID_REQUIRED` | Retryable mutation lacks idempotency key |
| 428 | `LEASE_REQUIRED` | Edit/merge command lacks a valid lease |
| 500 | `INTERNAL_ERROR` | Unexpected internal failure; the response is correlated and discloses no implementation detail |
| 409 | `WIKI_NAME_CONFLICT` | Owner-local normalized Wiki name already exists |
| 409 | `WIKI_DEFAULT_REQUIRED` | Archive would remove the exact active default or last active Wiki |
| 409 | `WIKI_ARCHIVED_HISTORY_ONLY` | Archived Wiki/session permits history-only reads, not a new mutation/turn |
| 409 | `ROLE_EPISODE_STALE` | Submitted Team-leader/executive episode is no longer current |
| 403 | `NATIVE_WRITE_DISABLED` | Native higher-space writer gate is not effective |
| 409 | `CORPUS_CLOSURE_STALE` | Authorization/organization closure generation changed |
| 409 | `DISTRIBUTION_STATE_CONFLICT` | Distribution grant/revoke state or role fence changed |
| 409 | `EDGE_HOME_INVALID` | Requested aggregate edge is not owned by the supplied LCA/home |
| 409 | `EDGE_SUPPRESSED` | Suppression ledger prevents direct activation/reuse |
| 404 | `REFERENCE_TARGET_INVALID` | Reference target is absent, unauthorized, concealed, or invalid |
| 409 | `TEMPORAL_HEAD_CONFLICT` | Expected temporal/verification head is stale |
| 422 | `TEMPORAL_VALUE_INVALID` | Typed temporal value, precision, timezone, or evidence is invalid |
| 409 | `VERIFICATION_NONCURRENT` | Attestation no longer binds the current revision and temporal summary |
| 409 | `PROJECTION_MANIFEST_UNTRUSTED` | Footer stripping/re-import manifest lacks a trusted canonical anchor |

## 2. Scope and capability API

### `GET /api/knowledge/v1/spaces`

Returns only spaces the current user can select in phase 1:

```json
{
  "items": [
    {
      "id": "...",
      "kind": "personal",
      "name": "내 지식",
      "capabilities": ["read", "publish", "edit", "deactivate", "restore"]
    },
    {
      "id": "...",
      "kind": "part",
      "name": "제어파트",
      "org_path": ["AMHS담당", "플랫폼팀", "제어파트"],
      "capabilities": ["read", "publish", "edit", "deactivate", "restore"]
    }
  ],
  "scope_epoch": 4
}
```

An unassigned user receives only the personal space. Team/담당 spaces are omitted until their feature flag is intentionally enabled.

### Conversation collection and metadata

| Method and path | Contract |
|---|---|
| `GET /api/knowledge/v1/conversations` | Cursor list for current owner, including immutable space badge and accessibility |
| `POST /api/knowledge/v1/conversations` | `operation_id`, `space_id`, optional title; verifies and captures scope epoch |
| `GET /api/knowledge/v1/conversations/{id}` | Messages/metadata/space/pins/bindings/summary status |
| `PATCH /api/knowledge/v1/conversations/{id}` | Title or archive status with expected row version; cannot accept `space_id` |
| `POST /api/knowledge/v1/conversations/{id}/compact` | Idempotent summary through a fixed message sequence; remains same-space |

There is no endpoint that changes an existing conversation's space.

When `KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING` is enabled, the immutable
`conversation.space_id` is the sole selected Wiki/view authority. Opening a
saved conversation selects that stored ID even if a browser cache or current
dropdown supplies another ID. A switch creates a new conversation; messages,
summaries, pins, citations, explicit contexts, and retrieval identities remain
with the old conversation. An archived personal-Wiki conversation remains
owner-readable as `history_only: true`, but every normal mutation and new turn
fails with `WIKI_ARCHIVED_HISTORY_ONLY` until that exact Wiki is restored.

If a user moved parts, the old conversation remains identified with the old space but part knowledge retrieval/mutation is refused with `SCOPE_STALE` or `FORBIDDEN`.

### `POST /api/knowledge/v1/conversations/{conversation_id}/turns/stream`

- Does not accept a knowledge `space_id` in the request.
- Requires `operation_id`; the same request hash returns/reconnects to the existing turn and never appends a duplicate user/final assistant message.
- Exposes a turn/event cursor or final stored response so a disconnected SSE client can resume safely.
- Resolves it from the conversation and checks membership on every turn.
- The accepted event captures the current actor/scope, exact selected view,
  role episode, personal-Wiki lifecycle generation, organization snapshot, and
  corpus-closure generation/hash as one canonical binding digest. Agent context
  composition and final commit re-resolve and compare that fence; mismatch
  fails with `CORPUS_CLOSURE_STALE` before retrieval or commit. Archive/restore
  therefore cannot pass through an ABA identity match.
- Retrieves only current active/published knowledge and valid pins in that space.
- Existing explicit workspace files, selected project context, and Notion attachments may remain conversation inputs, but must be visibly bound to that conversation and cannot silently change the knowledge space. A user's default private memory/project context is not automatically injected into a part conversation.
- A binding records whether it is an immutable snapshot or a live reference. Snapshot bindings remain usable after source access is revoked; live bindings are reauthorized on every turn and become unavailable without deleting prior conversation history.

Conversation context commands:

| Method and path | Contract |
|---|---|
| `POST /conversations/{c}/pins` | Add one same-space visible item/revision with operation ID |
| `DELETE /conversations/{c}/pins/{item_id}` | Remove own pin idempotently |
| `POST /conversations/{c}/context-bindings` | Explicitly bind an accessible project/workspace/Notion/source reference |
| `DELETE /conversations/{c}/context-bindings/{binding_id}` | Remove binding without deleting source data |

## 3. Knowledge read API

| Method and path | Contract |
|---|---|
| `GET /spaces/{space_id}/items` | Filtered/cursor item list; validates current access |
| `GET /spaces/{space_id}/items/{item_id}` | Current metadata/content, state, capabilities, lock summary |
| `GET /spaces/{space_id}/items/{item_id}/source-artifacts` | Original retained source-file metadata and same-origin download URLs after selected-view authorization |
| `GET /spaces/{space_id}/items/{item_id}/source-artifacts/{artifact_id}/download` | Verified original bytes as an attachment; authorization is rechecked after CAS verification |
| `GET /spaces/{space_id}/items/{item_id}/revisions` | Immutable revision history |
| `GET /spaces/{space_id}/items/{item_id}/revisions/{revision_id}` | One historical revision |
| `GET /spaces/{space_id}/items/{item_id}/compare?from=&to=` | Server-side safe diff data |
| `POST /spaces/{space_id}/search` | DB/FTS search within exactly one space |
| `GET /spaces/{space_id}/graph` | Effective graph; optional authorized inactive-management view |
| `GET /spaces/{space_id}/activity` | Paginated audit projection, not raw append-only audit dump |
| `GET /spaces/{space_id}/projection-status` | Desired/built generation and lag/error |

Management search may request lifecycle/curation filters when the actor has read capability. Agent retrieval never includes inactive, provisional, quarantined, merged-source, or purged content by default.

The item-detail response is a current authorized snapshot. It includes the
current (or latest tombstone) revision, body when retained, item-specific
capabilities, a token-free live-lease summary, active/inactive edge and
deterministic-reference counts, conversation-pin count, content/update/review
times and ages, and the latest open deactivation episode. An inactive episode
projects the immutable actor display snapshot, actor ID, exact reason,
deactivation time, elapsed inactive duration, and manual-purge eligibility.
Content age and inactive age are distinct; lack of review is represented by a
null review timestamp/age rather than zero.

Original-source reads use the selected view as the authority input, never a
client-supplied physical source space. Native/inherited access must resolve
through the current `CorpusClosure`; overlay access is restricted to its exact
granted item/revision. Only retained `raw` artifacts whose artifact/blob hashes
and byte sizes agree and whose CAS object is verified are listed. Download
reads those bytes outside the database transaction, verifies the content
address, then re-resolves authority and immutable metadata before returning a
private, no-store attachment. Foreign item/artifact identities are concealed as
`NOT_FOUND`.

SBE-8.12 fixes the list operation ID as `listItemSourceArtifacts` and the
download operation ID as `downloadItemSourceArtifact`. Both reads reject query
parameters. The list returns the complete retained metadata for one authorized
item without cursor fields: `request_id`, `corpus_closure_sha256`, `item_id`,
`selected_view_space_id`, and `items`. Each row contains exactly `artifact_id`,
`created_at`, `download_url`, `media_type`, `origin_type`, `original_filename`,
`size_bytes`, and `source_revision_id`. The URL names the same selected view,
item, and artifact on the same origin; an empty `items` array represents an
authorized item with no retained original source.

A successful download returns the verified original bytes with their source
media type, exact `Content-Length`, and `Content-Disposition: attachment` with
an ASCII fallback and UTF-8 filename. It also carries `Cache-Control: private,
no-store, max-age=0`, `Pragma: no-cache`, `X-Content-Type-Options: nosniff`,
`X-Original-Content-SHA256`, and `X-Request-ID`. Failed reads use the existing
JSON error envelope and stable codes; a binary success has no top-level JSON
`request_id` or `operation_id`.

Revision history is ordered by immutable `(revision_no DESC, revision_id DESC)`
and uses an opaque endpoint-bound cursor. List rows expose no body. The single
revision endpoint returns retained Markdown or null for a purged tombstone and
always exposes editor ID/current canonical display label, reason, parent/base revision,
change kind, raw and normalized hashes, normalizer, size, and creation time.
Blob-backed bodies are loaded outside the database transaction, checked as
strict UTF-8 against exact byte size/SHA-256, and scope plus immutable database
evidence are re-read before response construction.

Compare accepts exactly one `from` and `to` revision ID from the same item. It
returns deterministic `unified-v1` structured hunks and a canonical SHA-256,
not HTML. Each input is limited to 2,000,000 characters and 20,000 lines; total
diff output is limited to 2,000,000 characters. The activity endpoint exposes
only the sanitized projection fields declared by the schema. Raw audit
`details_json`, request metadata, tokens, and other secrets are never returned.
The immutable actor display snapshot remains an activity/audit field; revision
metadata stores the creator ID and resolves its current canonical display label.

## 4. Publication API

### Preview/extraction

`POST /api/knowledge/v1/publication-previews`

Accepts one of:

- multipart upload;
- `{source_type: "notion", page_id}`;
- `{source_type: "chat", conversation_id, message_range}`;
- manual Markdown.

It creates a durable extraction job or a ready immutable source snapshot. A ready response includes a short-lived `preview_id`, `source_snapshot_sha256`, extraction warnings, title/size, available target modes, deterministic lint/dedup hints, and `ready_result_schema_version`; the same immutable ready-result body is returned after restart. Preview does not create knowledge. Team/담당 native publication supplies the exact `target` descriptor below at preview creation; the descriptor and its role episode are included in the idempotency input and returned as the single `available_targets` entry.

If extraction is asynchronous, the response is `202` with `preview_id`, `job_id`, and `status_url`. `GET /api/knowledge/v1/publication-previews/{preview_id}` returns the durable preview state and `DELETE .../{preview_id}` cancels only cancellable pre-commit work. Generic `GET /api/knowledge/v1/jobs/{job_id}` and `POST .../{job_id}/cancel` expose actor-authorized status/cancellation. Restart must not lose a preview; expiration is checked again at publication commit. Cancel/expiry never masquerades as knowledge deletion: an unpublished orphan body becomes cleanup-eligible after 30 days only if no item, binding, proposal, or backup hold references it, while metadata/hashes remain.

For Notion, the server reads an accessible page snapshot through the Notion adapter. View access is sufficient under the confirmed cross-part sharing policy. Provenance records owner, page ID, page/source revision when available, publisher, and publisher's part at publication time. Later access revocation or page deletion does not silently remove the published snapshot.

### Commit publication

Opt-in upload Wiki generation (SBE-8.13, optional v17; not a live activation):
preview reads add `wiki_generation: {schema_version: 1, status}` and
`publication_ready` together, without rewriting the immutable extraction-ready
result. Generation status is `pending`, `ready`, `extraction_only`, or
`unavailable`. Extraction `status=ready` alone does not mean Wiki generation is
complete. The UI polls pending generation and labels extraction fallback plainly;
failed/cancelled extraction with no retained body is never a successful fallback.
Generation outcome is independent of current publishability: after publication a
retained completed Wiki remains `wiki_generation.status=ready` while preview
`status=consumed` and `publication_ready=false`. Expiry or missing retained source
bodies also prevent publication without rewriting a completed generation outcome.
No opt-in request means the legacy response remains unchanged.

Normal commit requests still cannot supply `authored_snapshot_id`. After exact
committed-operation replay detection, the server selects the completed,
source-bound Wiki. Pending generation returns HTTP 409 with
`PUBLICATION_PREVIEW_SERVICE_ERROR` and `retryable=true` before creating an
operation row; unavailable generation uses the same code with `retryable=false`.
The client retries the same intent and operation ID. The client extraction hash
remains source identity, not the server-selected Wiki hash. Replay verifies the
stored actual authored/extraction-reuse receipt, including prior-author reuse;
it never reselects the current run or requires purgeable source bodies.
Opt-in commits persist `body_source: authored_wiki|extraction` in the exact
operation result, including fresh extraction-only fallback; replay returns it
unchanged. The public response exposes that enum, never internal snapshot IDs
as download handles. Legacy non-opt-in result shapes remain unchanged.

`POST /api/knowledge/v1/publications`

```json
{
  "operation_id": "...",
  "preview_id": "...",
  "source_snapshot_sha256": "...",
  "target_mode": "personal|part|both|explicit"
}
```

For `target_mode: "explicit"`, both preview creation and commit carry exactly
one server-discovered descriptor (multipart upload encodes the same object as
the `target` form field):

```json
{
  "target": {
    "space_id": "...",
    "kind": "team|responsibility",
    "capability": "publish",
    "role_assignment_id": "...",
    "role_assignment_generation": 1
  }
}
```

- `part`/`both` is rejected for an unassigned user or stale preview membership.
- `both` resolves two target results atomically.
- `explicit` accepts exactly one Team or 담당 target and only the current exact
  `team_leader`/`responsibility_executive` role episode. Preview, durable job,
  commit, and replay bind the same target, closure, organization snapshot, actor
  row, role ID, and role generation; replacement, disable, move, or stale ABA
  evidence fails closed before knowledge is written.
- Response is an array because outcomes can differ by space:

```json
{
  "operation_id": "...",
  "request_id": "...",
  "results": [
    {
      "space_id": "...",
      "outcome": "provenance_added",
      "source_artifact_id": "...",
      "item_id": "...",
      "resolved_revision_id": "...",
      "created_revision_id": null,
      "finding_id": null
    },
    {
      "space_id": "...",
      "outcome": "created",
      "source_artifact_id": "...",
      "item_id": "...",
      "resolved_revision_id": "...",
      "created_revision_id": "...",
      "finding_id": null
    }
  ]
}
```

Every successful target has the same nullable response shape above. The first new `both` operation returns different item and revision IDs for its two targets. A retry of that same operation returns the stored result unchanged. A separate new operation for unchanged content may resolve to `no_op`/`provenance_added`.

Repeated unchanged Notion publication is a no-op/provenance event. Repeated changed publication creates an update proposal against the stable-origin item rather than a `-2` file or silent overwrite. The proposal fixes source snapshot, source/base revision, target revision, and finding version. It never automatically synchronizes personal and part copies.

## 5. Edit lease and revision API

| Method and path | Required input | Result |
|---|---|---|
| `POST /spaces/{s}/items/{i}/lease/acquire` | body: operation ID + editor-session ID; client-generated 256-bit token header | Fence, lease ID, expiry, base revision; raw token is never echoed/persisted |
| `POST /spaces/{s}/items/{i}/lease/heartbeat` | body: editor-session ID + lease ID; token + fence headers | New expiry if the complete holder/session/lease/token/fence tuple is still valid |
| `POST /spaces/{s}/items/{i}/lease/release` | body: operation ID + editor-session ID + lease ID; token + fence headers | Clears own lease, keeps persistent generation |
| `POST /spaces/{s}/items/{i}/lease/force-release` | body: operation ID, mandatory reason, exact `FORCE_RELEASE` confirmation | Part leader/admin only; increments fence and audits |
| `PUT /spaces/{s}/items/{i}` | body: operation ID, editor-session/lease IDs, expected row/revision, new Markdown, reason; token + fence headers | One new immutable revision or conflict |

Headers:

- `X-Knowledge-Lease-Token`
- `X-Knowledge-Fence`
- optionally `If-Match: "<revision-id>"`, which must exactly equal the canonical body `expected_revision_id` when present.

The raw lease token and fencing generation are mandatory headers for holder
operations and are never accepted from a body, query string, URL, response, or
error. The body is authoritative for editor-session, lease, and expected
revision/row-version identity; the `Idempotency-Key` header must equal the body
operation ID when supplied.

The browser creates the raw lease token with a cryptographically secure generator and retains it only for the editing session. The server logs redact this header and stores only a keyed hash. The server never accepts “last write wins.” A conflict response supplies current revision metadata but does not echo another user's draft.

## 6. Lifecycle API

### `POST /spaces/{s}/items/{i}/deactivate`

Requires operation ID, expected row version, and non-empty reason. A foreign live edit lease yields `LEASE_HELD`. Response includes `deactivated_at`, actor snapshot, `purge_eligible_at`, and affected graph/reference counts. Edge rows are unchanged.

### `POST /spaces/{s}/items/{i}/restore`

Requires operation ID and expected row version. Restores the same item/current revision and closes the latest deactivation episode. Response includes inactive duration and whether a fresh lint/review job was queued.

### `GET /spaces/{s}/items/{i}/purge-preview`

Administrator only. Returns eligibility, exact retained/deleted categories, revision/artifact/extraction/blob sizes, every active blocking reference, and confirmation text. Identity/hash/provenance/audit metadata is retained; a shared source/body/blob remains while any non-purged item/revision, ready preview, conversation snapshot binding, unapplied proposal, or backup hold needs it. Historical publication metadata alone retains hashes, not body bytes.

### `POST /spaces/{s}/items/{i}/purge`

Administrator only; operation ID, expected row version, preview token, and exact confirmation required. It is unavailable before the latest 90-day eligibility boundary and never runs from a timer.

## 7. Curation and merge API

| Method and path | Purpose |
|---|---|
| `GET /spaces/{s}/curation/findings` | Filtered/paginated review inbox |
| `GET /spaces/{s}/curation/findings/{id}` | Evidence spans, detector versions, affected revisions |
| `POST /spaces/{s}/curation/findings/{id}/review` | Approve/reject/ignore with reason and expected finding version |
| `GET /spaces/{s}/curation/shadow-metrics` | Bounded body-free model-proposal precision sample and zero-automatic-effect evidence |
| `POST /spaces/{s}/change-proposals/{id}/preview` | Fixed base/source-current/target-current three-way preview; no mutation |
| `POST /spaces/{s}/change-proposals/{id}/rebase` | Operation ID plus expected finding/target versions; creates a new proposal version and obsoletes the old one |
| `POST /spaces/{s}/change-proposals/{id}/apply` | Operation ID, finding version, target lease token/fence, expected target revision, and preview hash |
| `POST /spaces/{s}/merges/preview` | Fixed two-revision, three-pane preview and edge/reference impact |
| `POST /spaces/{s}/merges/apply` | Two leases/fences, two expected revisions, the exact confirmed result Markdown repeated from preview, preview hash, operation ID |
| `POST /spaces/{s}/edges/proposals/{id}/approve` | Commits a reviewed semantic edge with evidence |
| `POST /spaces/{s}/edges/{id}/deactivate` | Explicit edge-only state change |

Rules:

- A semantic proposal cannot be approved after either evidence revision changed; it becomes obsolete/stale.
- A changed-source proposal uses source snapshot/base/current-target. A personal-to-part proposal uses fork-base/personal-current/part-current. Apply creates one target revision only after the target lease/fence and all fixed revisions still match; otherwise it returns a stale response and requires explicit rebase.
- Merge preview is not a process-memory authority. Apply repeats the exact
  confirmed result Markdown; the server recomputes its fixed-revision preview
  binding before opening the write transaction. Merge result creation, source
  `merged` state, redirect relation, audit, and outbox commit together.
- Near-duplicate merge does not automatically rewire all semantic edges.
- No batch merge/deactivate endpoint is exposed in phase 1.

Review/application policy:

| Finding/effect | Tier | Review outcome and domain command |
|---|---:|---|
| Structural/source evidence | T0 | Detector may record/obsolete evidence only; blocker keeps item quarantined until an explicit corrected publication |
| Exact provenance/fork/promotion | T1 | Deterministic service may apply the versioned relation idempotently with audit/outbox |
| Weak complementary similarity | T2 | Item remains published with a warning badge; an authorized space editor may review it, but it proposes no domain mutation |
| Strong near duplicate/potential conflict/semantic edge | T2 | Item is Agent-hidden where specified; an authorized space editor invokes the current-version edge or proposal command; reject/ignore changes only finding state |
| Changed-source or personal-to-part proposal | T2 | Human approval alone does not edit content; lease-protected `apply` creates the target revision and atomically marks finding `applied` |
| Deactivate/purge/org/security suggestion | T3 | Review can acknowledge/link to a preview only; the model/finding service cannot execute the action |

For a provisional item, an approved no-blocker publication finding may execute `provisional -> published`; a confirmed blocker executes `provisional -> quarantined`. Published/quarantined content may return to provisional only in a new-revision transaction that also enqueues its rescan and updates audit/outbox/FTS. There is no generic curation-status patch. Each visibility transition, finding state, domain mutation, audit row, outbox event, and FTS change commits atomically. `required_reviewer`, `risk_tier`, and `visibility_effect` are server-derived and cannot be elevated by client or model output.

## 8. Organization administration API

All endpoints require system administrator role.

| Method and path | Contract |
|---|---|
| `GET /api/admin/v1/org/tree` | Stable IDs, status, counts, leaders, validation warnings, plus ID-sorted canonical `users[]` containing only `id`, `display_name`, `system_role`, `account_status`, `current_part_id`, `current_membership_id`, `scope_epoch`, and `row_version` |
| `POST /api/admin/v1/org/units` | Create team under responsibility or part under team |
| `POST /api/admin/v1/org/units/{id}/change-preview` | Validate move/inactivation, blockers, member/leader/space impact, and issue short-lived preview token |
| `PATCH /api/admin/v1/org/units/{id}` | Rename or status update with expected row version; inactivation additionally requires the current preview token and exact member/leader/space resolution |
| `POST /api/admin/v1/org/units/{id}/move` | Operation ID, expected version, preview token, resolution; stable ID/space retained |
| `PUT /api/admin/v1/users/{user_id}/part-membership` | Assign/move/unassign with reason and expected user version |
| `PUT /api/admin/v1/org/parts/{part_id}/leader` | Replace active leader atomically |
| `POST /api/admin/v1/users/{user_id}/disable` | Operation ID, expected user version, reason; increments scope epoch and revokes sessions/leases/jobs while preserving references |
| `POST /api/admin/v1/users/{user_id}/enable` | Operation ID, expected user version, reason; restores login only, never silently restores stale leases/jobs |
| `GET /api/admin/v1/purge-eligible` | Paginated eligible inactive items with blockers |

Constraints:

- The organization tree and its canonical `users[]` are read from one
  `DEFERRED` database snapshot. A user's nullable `current_part_id` and open
  membership ID/part must agree; a mismatch fails closed rather than returning
  a torn admin read model. Password hashes, credential-policy fields, and
  account timestamps are never included in this response.
- A change preview returns each impacted member's `user_id` and
  `expected_user_row_version`, the nullable current leader-assignment ID, and
  the nullable owned-space row version from that same preview snapshot. The UI
  builds an inactivation resolution from those preview fences, never by mixing
  values from a separate tree request.
- Do not independently assign team and 담당 to a user.
- Inactivating/moving an organization with active members, leader, or knowledge requires a preview and explicit resolution.
- A move resolution explicitly retains memberships, the leader, and the owned
  space. An inactivation resolution enumerates every current member with its
  expected user version and destination part or intentional unassignment, and
  explicitly acknowledges leader closure and part-space freezing. The commit
  rechecks the complete impact hash bound into the short-lived preview token.
- Inactivating a part requires replacement/removal of its leader and an explicit member reassignment/unassignment plan; otherwise it is blocked. A part space with retained knowledge becomes `frozen`, not deleted.
- Disabling, moving, or unassigning the current leader must atomically install an eligible replacement or close the leadership and turn that part space's writes off; otherwise the command is rejected.
- User deletion is disable-first. Existing destructive account-data deletion must not bypass canonical reference checks.
- Membership move increments scope epoch and revokes old-part edit capability immediately.
- Replacing a part leader increments the part `org_unit.row_version`; therefore
  `expected_part_row_version` is an aggregate CAS fence, including account
  disable and membership-move paths that replace or close leadership.
- Membership, leader, and account-status commands also submit the current
  nullable leader-assignment ID. The commit compares the assignment episode,
  not only the user ID, so an intervening close/reopen (ABA) is stale.
- Membership and account-status commands additionally submit a nullable
  expected part row version. It must be non-null when the target is the current
  leader and that command will close or replace the leader episode.
- P1.5 may create a part before P1.6 creates its owned knowledge space. The tree
  reports `space_status=not_created`; no implicit space or higher-level
  publishing capability is created by the organization command.

## 9. Permission matrix

| Action | Personal owner | Same active Part member | Exact Team leader | Exact 담당 executive | System admin by status alone |
|---|---:|---:|---:|---:|---:|
| Read/search native scope or authorized aggregate view | Own Wiki | Part | Team closure | 담당 closure | No normal Agent-wide scope |
| Publish/edit/deactivate/restore native content | Own Wiki | Part | Team native only | 담당 native only | No ordinary authorship |
| Reviewed curation/temporal mutation | Own Wiki | Part | Team native only | 담당 native only | No ordinary authorship |
| Correct local/home semantic edge | Own Wiki local | Part local | Team-home | 담당-home | Suppress only through explicit organizational break-glass |
| Grant downward distribution | — | — | Own Team-native source | Own 담당-native source | No create; may break-glass revoke existing grant |
| Force-release another/stale lease | Own Wiki | Part leader only | Own Team | Own 담당 | Yes, exact confirmed break-glass |
| Manage organization and role episodes | No | No | No | No | Yes |
| Permanent purge after eligibility | No | No | No | No | Yes, exact confirmed break-glass |

Admin authority must not silently make normal Agent conversations search every
space and does not confer ordinary Part/Team/담당 authorship. An administrator
may still act through independently held ownership, active Part membership, or
current leader/executive episode. Every break-glass mutation follows the exact
reason/confirmation/CAS/audit/outbox matrix in the successor product contract.

## 10. UI contract

### 10.1 Global scope selector

- The web header renders authorized personal Wikis and the concrete current Part
  as enabled choices. Authorized Team/담당 descriptors may remain visible but
  are disabled in this browser control; the backend selection/read contract
  remains fully functional for future clients, administrative validation, and
  already-bound conversation history.
- In knowledge-management screens, switching reloads list/search/graph/index-log/activity/candidates/pins for the selected space.
- In Agent chat, selecting another space presents one primary action: `새 지식공간으로 새 대화 시작`.
- It does not mutate the current conversation. The old conversation keeps a visible immutable space badge.

### 10.2 Publication modal

- Radio options: `개인`, `<현재 파트명>`, `개인 + <현재 파트명>`.
- `part`/`both` disabled with explanation when unassigned.
- `both` displays: “두 개의 독립 지식으로 등록되며 이후 자동 동기화되지 않습니다.”
- Show extraction warnings, exact duplicate/update outcomes, and target-specific success links.
- A changed stable Notion source displays `기존 지식 변경 제안` rather than creating an ambiguous numbered copy.

### 10.3 Item editor

- The item popup displays `원본 자료` below the canonical Markdown. Each retained
  PDF/PPTX/DOCX or other original artifact has an explicit download link; a
  truly source-less manual/legacy item shows the empty state instead of a
  fabricated file.
- Clicking edit acquires a lease; other users see read-only content, holder name, and last heartbeat/expiry information.
- The browser keeps exactly one active edit session in memory, stores the raw token only in that closure, heartbeats every 30 seconds, stops heartbeat when the editor leaves, and attempts unload release only as a best effort.
- `편집 완료` saves a new revision and releases the lease. `취소` offers draft discard/copy and releases own lease.
- On lease/revision conflict, retain local draft and offer `최신본 보기`, `내 초안 복사`, and `새 잠금으로 비교`; no force overwrite.
- Part leader/admin sees audited force-release only after a mandatory reason and exact `FORCE_RELEASE` confirmation.

### 10.4 Deactivation and restoration

- Deactivate preview shows incoming/outgoing visible edges, pins/references, last update/review dates, and requires a reason.
- Inactive view shows who/when/why, elapsed inactive time, content age separately, and exact purge eligibility date.
- Restore uses the same item ID and reports that original eligible edges reappear.
- General users never see a permanent-delete control.

### 10.5 Curation

- Findings are grouped by blocker/error/warning/info and kind, and show a separate T0–T3 risk badge.
- Each shows compared revision IDs, evidence snippets, detector/rule/model/rule version, required reviewer, visibility effect, and why it is or is not Agent-visible.
- T2 screens require an authenticated human action; T3 screens expose only the ordinary authorized command preview and never an “AI apply” control.
- Merge uses source/current/target three-pane preview, reference/edge impact, two-lock status, and explicit result Markdown confirmation.
- Semantic edge suggestions require explicit approval; LLM confidence alone is never shown as an automatic action guarantee.

### 10.6 Administrator organization screen

- Tree view rooted at `AMHS담당`; create/rename/move/inactivate team and part nodes.
- User pane assigns one part or unassigned state; team/담당 are displayed as derived read-only ancestors.
- Part leader selection validates active membership.
- Warn and block unsafe moves/inactivation with counts and resolution links.
- A separate purge-eligible queue shows only administrator actions and retained tombstone consequences.

### 10.7 Registered successor Knowledge UI

The preceding controls are the flags-off compatibility UI. Once the owning SBE
packets are enabled, the left tree orders `내 지식 → Part → 팀 → 담당`.
`내 지식` expands a cursor-paginated, lazily rendered list of owner Wikis; each
Wiki independently expands documents, Index, Log, Graph, rename/default/archive
management, and an archived-only management view. Opening an old conversation
selects its server-bound Wiki; selecting another Wiki/view starts a new
conversation instead of moving the existing session.

The Knowledge `+` action opens one dialog with Chrome-like tabs: `자료 업로드`
first and `대화 지식화` second. Before submission it shows `개인`, `Part`, and
`개인+Part`; personal-bearing targets expose the exact active Wiki selector.
The two outcomes of `개인+Part` are independent canonical items. Team/담당
native write controls appear only for the exact current leader/executive and
never on inherited descendant items.

Team/담당 Index, Log, Graph, search, and Agent results preserve real
`source_space_id`, inherited state, and per-source capabilities. Part Graph
never shows cross-Part aggregate edges; Team/담당 Graph composes descendant
local clusters with authorized LCA-home edges. Automatically validated semantic
edges appear without an approval inbox but expose relation, evidence,
confidence, detector/model generation, and exact permitted correction actions.
Ordered `참조 문서` remains visually separate from semantic Graph. Page footers
render system-owned temporal metadata below canonical Markdown and never become
editable body, search text, or model excerpt.

When an Agent answer uses Wiki body evidence, independent findings render as
chapters. Each chapter is immediately followed by `근거 문서` containing only
supporting original filenames linked to the exact authorized download route,
then a Markdown horizontal rule. The last chapter is always `전체 요약` and
summarizes the Wiki material actually read. This rule is generic and contains
no account, organization, topic, document-title, or query-phrase exception.
External/web citations remain distinct and can never masquerade as a Wiki
original.

## 11. Registered successor v2 operation inventory

The following exact delta is registered behind disabled successor flags. These
18 paths contain 22 operations and are additive to the frozen 52-path/
56-operation v1 baseline. The OpenAPI operation IDs and JSON Schema request/
response shapes are normative; later packets implement the routes without
renaming or splitting them.

This v2 inventory remains the existing 18-path/22-operation subset. SBE-8.12
adds the two v1 original-source operations specified in §3; together they form
the exact successor delta of 20 paths/24 operations and the current
72-path/80-operation total. The original 52-path/56-operation baseline and its
operation-ID hash remain unchanged. SBE-8.12 remains `IMPLEMENTED_NOT_GATED`
until its remaining external gates have evidence.

| Method and route | `operationId` | Contract owner |
|---|---|---|
| `GET /api/knowledge/v2/personal-wikis` | `personalWikisList` | SBE-2.1 |
| `POST /api/knowledge/v2/personal-wikis` | `personalWikisCreate` | SBE-2.1 |
| `POST /api/knowledge/v2/personal-wikis/search` | `personalWikisSearch` | SBE-5.1 |
| `PATCH /api/knowledge/v2/personal-wikis/{space_id}` | `personalWikiRename` | SBE-2.1 |
| `PUT /api/knowledge/v2/personal-wikis/{space_id}/default` | `personalWikiSetDefault` | SBE-2.1 |
| `POST /api/knowledge/v2/personal-wikis/{space_id}/archive` | `personalWikiArchive` | SBE-2.1 |
| `POST /api/knowledge/v2/personal-wikis/{space_id}/restore` | `personalWikiRestore` | SBE-2.1 |
| `GET /api/admin/v2/org/teams/{team_id}/leader` | `teamKnowledgeLeaderGet` | SBE-6.2 |
| `PUT /api/admin/v2/org/teams/{team_id}/leader` | `teamKnowledgeLeaderPut` | SBE-2.3 |
| `GET /api/admin/v2/org/responsibilities/{responsibility_id}/executive` | `responsibilityKnowledgeExecutiveGet` | SBE-6.2 |
| `PUT /api/admin/v2/org/responsibilities/{responsibility_id}/executive` | `responsibilityKnowledgeExecutivePut` | SBE-2.3 |
| `GET /api/knowledge/v2/spaces/{space_id}/distribution-targets` | `distributionTargetsList` | SBE-6.2 |
| `GET /api/knowledge/v2/spaces/{space_id}/distributions` | `distributionsList` | SBE-2.3 |
| `POST /api/knowledge/v2/spaces/{space_id}/distributions` | `distributionsCreate` | SBE-2.3 |
| `POST /api/knowledge/v2/spaces/{space_id}/distributions/{grant_id}/revoke` | `distributionRevoke` | SBE-2.3 |
| `POST /api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppress` | `semanticEdgeSuppress` | SBE-4.2 |
| `POST /api/knowledge/v2/spaces/{space_id}/edges/{edge_id}/suppression/lift` | `semanticEdgeSuppressionLift` | SBE-4.2 |
| `GET /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal` | `itemTemporalGet` | SBE-3.2 |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal-assertions` | `temporalAssertionCreate` | SBE-3.2 |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/temporal-assertions/{assertion_id}/revoke` | `temporalAssertionRevoke` | SBE-3.2 |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/verification-attestations` | `verificationAttestationCreate` | SBE-3.2 |
| `POST /api/knowledge/v2/spaces/{space_id}/items/{item_id}/verification-attestations/{attestation_id}/revoke` | `verificationAttestationRevoke` | SBE-3.2 |

Additive successor fields include exact selected-view and real source-space
identity, per-source capability, history-only state, `CorpusClosure` generation,
origin-preserving aggregate graph/search results, automatic-edge evidence and
suppression, ordered explicit references, a document-count-unbounded authorized
`ranked_documents[]`/body-evidence lane governed by an exact 96 KiB combined
excerpt budget and separate max-four mandatory context, typed temporal metadata, and
projection-manifest v2 hashes. Clients never enlarge closure by submitting IDs.

## 12. Compatibility transition

- Existing `/api/code/knowledge/*` and Second Brain responses may be served temporarily by compatibility adapters backed by the new repository.
- Legacy `uid` query/body fields never select another user's store; non-admin compatibility calls resolve only the authenticated personal space.
- Preserve old response keys such as `thoughts`/`pinned_thoughts` long enough for the existing UI to transition, but canonical IDs are opaque item IDs.
- Remove read-time refresh side effects. Explicit reindex endpoints enqueue a projection/rebuild job.
- Both current Notion publish routes call one common preview/publication service; duplicated write logic is removed before part publication is enabled.
