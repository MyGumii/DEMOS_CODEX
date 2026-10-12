# Canonical architecture and domain contracts

> **Successor contract registered:** canonical-v7 remains the flags-off runtime
> baseline, while the multi-Wiki/higher-space/aggregate-graph/automatic-edge
> target in `second_brain_evolution/PRODUCT_CONTRACT.md` and `FORWARD_SCHEMA.md`
> is the authoritative forward architecture. SBE-0.1 completed the machine
> transition; SBE-1.1+ must implement it without rewriting completed v7 evidence.

## 1. Design principles

1. A knowledge space, not a filesystem directory or user ID, is the unit of retrieval and collaboration.
2. Canonical mutations are atomic database transactions. Files are generated views.
3. Content history is append-only; current state is a versioned pointer.
4. Permissions and membership are checked at commit time, not only when a screen opens.
5. Every retryable command is idempotent and every successful mutation emits audit and outbox records in the same commit.
6. Expensive or nondeterministic work is performed outside a write transaction and its input revision is revalidated before commit.
7. Scope correctness is enforced server-side even though cross-part information is not considered a confidentiality boundary.

## 2. Recommended module boundary

Introduce a new package rather than continuing to grow `second_brain_store.py`:

```text
code_assist_v1/knowledge_core/
  db.py                       connection factory and transaction helper
  migrations/                numbered, checksumed schema migrations
  domain.py                   enums, value objects, state validation
  errors.py                   stable domain/API error codes
  scope.py                    ScopeResolver and capability calculation
  repositories/
    users.py
    organization.py
    spaces.py
    conversations.py
    artifacts.py
    items.py
    graph.py
    leases.py
    curation.py
    audit.py
    outbox.py
    jobs.py
  services/
    organization.py
    spaces.py
    publication.py
    editing.py
    lifecycle.py
    retrieval.py
    curation.py
    conversations.py
  projection/
    materializer.py
    markdown.py
    graph.py
    activity.py
  worker.py
  legacy/
    inventory.py
    importer.py
    compatibility.py
  admin_routes.py             explicit injectable P1.5 administrator adapter
  routes.py
```

Rules:

- Repositories contain SQL and row mapping, not authorization decisions.
- Services own authorization, transactions, idempotency, audit, and outbox orchestration.
- Route handlers are thin adapters and never write a canonical table or projection directly.
- `admin_routes.py` is registered only on an empty dedicated Flask/Blueprint
  surface after semantic path, endpoint, callback, implicit-method, prefix,
  subdomain, and URL-default collision checks. It remains absent from default
  hosts until canonical traffic cutover.
- Only the materializer writes Markdown/index/log/graph projection paths.
- `app_code`, `app_notion`, and the optional Blueprint use the same composition root and service objects. The two Notion publish routes must call one publication service.
- Existing stores become read-only migration/compatibility adapters and are deleted only after cutover evidence is retained.

## 3. SQLite runtime contract

Database location: a configurable local NTFS SSD/NVMe path outside projection and upload directories. Do not place the live DB in SMB/NAS/OneDrive-synchronized storage.

Initialization, once per deployment:

```sql
PRAGMA journal_mode = WAL;
```

Every connection:

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;
PRAGMA synchronous = FULL;
```

Operational rules:

- Use explicit, short `BEGIN IMMEDIATE` write transactions.
- Never hold a transaction while calling an LLM, OCR, extractor, filesystem copy, HTTP service, or streaming response.
- Read a required snapshot and close the read transaction before long-running inference.
- Run schema migrations through one deployment command, never implicitly on each request or connection.
- Production v1 pins SQLite `3.53.4` and its approved `sqlite_source_id()` (`2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc`). A different version or source ID fails readiness until this contract and allowlist are deliberately revised and regression-tested. The currently installed 3.50.4 fails the deployment gate; do not use a loose lower-bound comparison.
- Preflight verifies `sqlite_version()`, `sqlite_source_id()`, compile options, FTS5 creation/query, foreign keys, WAL, synchronous mode, busy timeout, and the actual live database volume/path before accepting traffic.
- Checkpoint from controlled worker/maintenance logic and monitor WAL size and checkpoint delay. Do not force a checkpoint on every request.
- Back up through SQLite's online backup facility and restore-test the DB together with the immutable-blob manifest.
- A backup run acquires a durable global GC hold before the online snapshot,
  binds every referenced immutable blob, verifies the exact SQLite/catalog/
  audit high-water and blob set, and exposes only an atomically renamed completed
  directory. Retention keeps the latest 96 half-hour, 30 UTC-daily, and 12
  UTC-monthly restore points; pruning re-verifies and handle-deletes only a
  quarantined backup-root child.
- An isolated restore pins every target directory across database/blob copy and
  projection rebuild, closes the captured backup hold in the restored database,
  then requires quick/FK/schema postconditions and zero projection mismatch.
  Phase 1 is roll-forward-only after its first canonical production write.

The transaction/service interface must not expose SQLite-specific behavior to routes so a later PostgreSQL repository remains possible.

## 4. Data conventions

- IDs are server-generated opaque UUID text. Clients must not derive IDs from names or paths.
- Timestamps are UTC integer milliseconds; UI renders Asia/Seoul time.
- Every mutable aggregate has integer `row_version`, incremented by each successful state mutation.
- JSON columns store UTF-8 canonical JSON and have a documented schema/version field.
- User and organization display-name snapshots are retained in audit records so later rename/disable does not erase history.
- Markdown line endings are normalized for hashing, but a normalized hash alone never authorizes automatic deduplication.
- Secrets such as raw lease tokens never appear in logs, audit details, job payloads, or persistent plaintext.

## 5. Canonical schema

The following is the logical schema contract. `SCHEMA_V1.md` is the authoritative physical type/null/default/FK/check/trigger/index/FTS contract; numbered migrations must implement it exactly and schema tests must prove every listed constraint.

### 5.1 Schema, users, and organization

`schema_migration`

- `version` primary key, `name`, `checksum`, `applied_at`, `app_version`.
- An existing version with a different checksum is a startup failure.

`app_user`

- `id` primary key; normalized immutable login ID.
- `display_name`, `password_hash`.
- `system_role`: `admin | user`.
- `account_status`: `active | disabled`.
- `scope_epoch` integer; increment on account disable or part membership change.
- `created_at`, `updated_at`, `disabled_at`, `row_version`.
- Do not hard-delete a user referenced by knowledge or audit.

`org_unit`

- `id` primary key, `kind`: `responsibility | team | part`.
- `parent_id` nullable FK with `ON DELETE RESTRICT`.
- `name`, normalized name, `status`: `active | inactive`.
- `created_at/by`, `updated_at/by`, `row_version`.
- Validate: responsibility has no parent; team parent is responsibility; part parent is team; cycles are impossible; active sibling names are unique case-insensitively.
- Seed one responsibility root named `AMHS담당`.

`org_membership`

- `id`, `user_id`, `part_id`, `valid_from`, `valid_to`, `assigned_by`, `reason`.
- A partial unique index permits at most one row per user where `valid_to IS NULL`.
- `part_id` must reference an active `part` at assignment time.
- An unassigned user is valid and has personal-space capability only.

`org_role_assignment`

- `id`, `user_id`, `org_unit_id`, `role`, `valid_from`, `valid_to`, `assigned_by`.
- Phase-1 role is `part_leader`; the user must have active membership in that part.
- A partial unique index on `(org_unit_id, role)` where `valid_to IS NULL` permits only one active part leader for a part.
- One active part leader per active part is required before that part's write feature is enabled.
- System `admin` remains a separate global role.

### 5.2 Spaces, conversations, and pins

`knowledge_space`

- `id`, nullable `owner_user_id` FK and nullable `org_unit_id` FK.
- `space_kind`: `personal | part | team | responsibility`.
- `status`: `active | frozen | inactive`.
- `generation` integer, incremented by a transaction that changes a visible projection.
- `created_at`, `updated_at`, `row_version`.
- A check constraint requires exactly one owner: personal uses `owner_user_id`; organizational kinds use `org_unit_id` whose unit kind matches the space kind.
- Partial unique indexes permit exactly one personal space per user and one organizational space per unit.
- Phase 1 exposes only personal and part spaces; higher spaces remain disabled.

P1.6 provisioning policy:

- A reconciliation command runs inside one short `BEGIN IMMEDIATE` and ensures
  one personal space for every canonical user, including disabled users, plus
  one part space for every part that is active in that transaction snapshot.
- A missing space for an inactive part is not backfilled. An existing inactive-
  part space is retained for history and is never reset by reconciliation.
- New phase-1 rows start as `active`, `write_enabled=0`, `generation=0`, and
  `row_version=1`. Existing rows keep their stable ID, status, write flag,
  generation, timestamps, and row version even if their ID predates the current
  deterministic provisioning namespace.
- Every created row has one immutable audit event and one outbox event in the
  same transaction. The command itself is actor-scoped and idempotent; exact
  replay returns the stored semantic result with the current request ID.
- Organization creation and reads never lazy-create a space. The explicit
  reconciliation command changes `not_created` to `write_disabled`; a part
  created after a completed reconciliation remains `not_created` until a later
  explicit command.
- Team and responsibility spaces are neither created nor exposed. Any existing
  higher-level row remains untouched and write-disabled by schema policy.
- Provisioning is not authorization. P1.7 alone resolves selectable spaces and
  capabilities from account, membership, role, space, epoch, and rollout state.

P1.7 scope-resolution policy:

- A caller supplies only a canonical session user ID and captured scope epoch,
  plus an immutable rollout snapshot. `ScopeResolver` requires an already-open
  caller-owned transaction and rereads the account status and epoch, current
  part and open-membership mirror, active organization path, active leader, and
  requested space from that one database snapshot.
- Normal Agent scope contains only the actor's personal space and current-part
  space. Foreign, former-part, frozen, inactive, schema-supported higher-level,
  and forged space claims are deliberately concealed as `NOT_FOUND`; system
  administrator status never widens normal Agent retrieval scope.
- A missing personal space for an active canonical user is an integrity error.
  A current part whose explicit provisioning has not yet run is omitted from
  the selector and remains unavailable; reads never provision or repair it.
- Database entitlement and rollout-effective capability are separate values.
  An active but write-disabled space has read entitlement only. Ordinary part
  members receive publish/edit/deactivate/restore/curate only when both the
  canonical row and rollout snapshot enable writes. A current-part leader or
  same-part system administrator additionally receives `force_release`.
- The default rollout snapshot is all-off, so no scope is selectable until the
  host explicitly supplies a reviewed read rollout. P1.7 does not read the
  environment, bind a route, start a service, or enable any feature flag.

P1.8 administrator UI policy:

- The browser consumes only the P1.5 canonical hierarchy/user snapshot and
  same-snapshot change-preview fences. It never combines legacy registry facts
  with canonical row versions, membership episodes, leader assignments, or
  space versions to authorize a command.
- Move and inactivation require visible impact plus explicit resolution.
  Membership/leader/account actions use canonical aggregate and episode CAS
  fences. A validated success or any 409 causes a full canonical refresh; the
  UI never applies an optimistic local authority update.
- Mutation response loss retains the original semantic body, transport, and
  operation UUID. Recovery resends that exact JSON body and matching
  `Idempotency-Key`; an unknown outcome cannot silently become a new intent.
- Missing, invalid, or out-of-order canonical snapshots fail closed. Legacy
  login/profile/password/data/transfer tools are visibly isolated and cannot
  change canonical membership, role, account status, or knowledge space. Hard
  delete and team/responsibility publication controls are absent.
- The UI module does not register the P1.5 server adapter or enable a feature
  flag. Live-host composition and all knowledge traffic remain unchanged.

`conversation`

- `id`, `owner_user_id`, immutable `space_id`, captured `scope_epoch`.
- `title`, `status`: `active | archived`, versioned `metadata_json`, `created_at`, `updated_at`, `row_version`.
- Reject every attempt to update `space_id`.
- A part conversation whose stored scope epoch or current membership is stale is viewable only according to policy and cannot retrieve/mutate old-part knowledge.

`conversation_message`

- `id`, `conversation_id`, monotonic `sequence_no`, `role`, `content_json`, `created_at`.
- Unique `(conversation_id, sequence_no)`.
- Existing JSON sessions migrate here; large tool payloads may use blob references.

`conversation_turn`

- `id`, `conversation_id`, actor, client `operation_id`, request hash.
- State: `accepted | running | completed | failed | cancelled`.
- User-message ID, optional assistant-message ID, durable final-response metadata/error, created/updated/completed times.
- Unique `(conversation_id, operation_id)` prevents retry/reconnect from appending the same user turn or final assistant message twice.
- Streaming transport may reconnect by turn/event cursor; canonical completion is the stored assistant message, not the socket lifetime.

`conversation_context_binding`

- `id`, `conversation_id`, binding type: `workspace_file | project_context | notion_page | source_artifact`.
- Binding mode: `snapshot | live`; external/resource identity, optional immutable source artifact/snapshot hash, display metadata, actor/time, status.
- It represents an explicit, visible context attachment; default private memory/project data is not silently converted into a binding.
- Snapshot mode remains usable from its immutable artifact after source access is revoked. Live mode is reauthorized on every turn and becomes unavailable on access loss without deleting prior conversation history.

`conversation_summary`

- `id`, `conversation_id`, covered through message sequence, summary content, model/prompt/version metadata, actor/time.
- A summary cannot move to another conversation/space and is invalidated/rebuilt if its covered message history changes.

`conversation_pin`

- `conversation_id`, `space_id`, `item_id`, optional pinned `revision_id`, `created_by/at`.
- Composite foreign keys guarantee that a pin belongs to the same space as the conversation and item.

### 5.3 Blobs, artifacts, and ingestion

`blob_object`

- `sha256` primary key, `size_bytes`, `media_type`, `relative_path`, `created_at`, `verification_status`.
- Blob paths are derived from hash only; original filenames live in metadata.
- Garbage collection deletes only a blob with no canonical reference after a grace period.

`source_artifact`

- `id`, `origin_type`: `upload | notion | chat | manual | legacy`.
- Stable `origin_key` when available, such as owner/page ID; `origin_revision_key` when available.
- `raw_sha256`, raw size, media type, original filename, and body-retention state; immutable metadata survives content purge.
- Publisher, source owner snapshot, metadata schema/version, `created_at`.
- Preserve every ingestion event's identity, hashes, and provenance even when logical knowledge is deduplicated or its eligible unreferenced body is later purged.

`artifact_blob`

- Artifact, immutable blob hash, role: `raw | embedded_asset | attachment`, logical name/media type/ordinal.
- The link is immutable while retained. An administrator purge may remove links/bodies only when no surviving non-purged item/revision, ready preview, conversation snapshot binding, unapplied proposal, or backup hold depends on them. Historical publication metadata retains IDs/hashes but does not by itself retain body bytes after all target knowledge is purged; physical blob deletion remains a later idempotent GC step.

`extraction_snapshot` and `extraction_snapshot_body`

- Immutable snapshot metadata stores artifact, extraction/raw/normalized hashes, normalizer and extractor versions, status/warnings, byte count, and body-retention state.
- The body table stores either extracted Markdown or a content blob reference. It is immutable while present and uses the same reference-aware administrator purge exception as artifact/revision bodies.
- A source that never became knowledge may have only its body links cleaned after the fixed 30-day grace when every preview is terminal and no item, lineage, snapshot binding, proposal, or backup hold references it. This durable system cleanup keeps artifact/snapshot metadata and hashes and is distinct from the 90-day knowledge purge.

`publication_preview`

- `id`, actor, captured scope epoch, source artifact/snapshot hash, requested source type, status: `queued | extracting | ready | consumed | failed | cancelled | expired`.
- Optional durable job ID, expiry, available target modes, versioned deterministic findings, immutable ready-result JSON, error, created/updated/completed times.
- Commit rechecks actor, scope epoch, target capability, expiry, and snapshot hash; a preview is never knowledge and cannot be reused after its successful publication operation.

`publication_operation`

- `id`, actor, `operation_id`, request hash, source artifact, requested target mode, created time.
- Unique `(actor_user_id, operation_id)`.
- `operation_id` reused with a different request hash is an error.

`publication_target`

- `publication_operation_id` FK to the server-generated `publication_operation.id`, target space, outcome: `created | provenance_added | update_proposed | no_op`.
- Resolved item/revision or curation-finding IDs, plus response metadata.
- Unique `(publication_operation_id, target_space_id)`. Do not key this table by the actor-scoped client operation string alone.
- All target rows and their canonical outcomes commit together.

### 5.4 Items, revisions, provenance, and lifecycle

`lineage_group`

- `id`, initial artifact, immutable `sync_policy=none`, base artifact/hash, `created_at`.
- Groups independently evolving items; it does not imply synchronization.

`lineage_member`

- Lineage group, item, initial revision, optional `derived_from_item_id` and `derived_from_revision_id`, member role, actor/time.
- Unique item membership and immutable initial/fork-base mapping make personal/part three-way proposals reproducible without a synchronization pointer.

`knowledge_item`

- `id`, `space_id`, optional lineage group.
- `lifecycle_status`: `active | inactive | merged | purged_tombstone`.
- `curation_status`: `published | provisional | quarantined`.
- `current_revision_id` nullable only for a purged tombstone.
- `row_version`, persistent `lease_generation`.
- Creator/time, updater/time, latest deactivation episode, optional redirect target.
- Unique `(id, space_id)` for composite edge/pin constraints.
- Default Agent visibility requires active lifecycle and published curation state.

`knowledge_revision`

- `id`, `item_id`, positive `revision_no`, optional parent/base revision.
- `title`, raw and normalized content hashes, byte count, and normalizer version.
- Structured metadata/tags, change kind/reason, actor and timestamp.
- Unique `(item_id, revision_no)` and immutable after insert.
- Item `current_revision_id` points to a revision of that same item.

`knowledge_revision_body`

- `revision_id` primary/composite FK to exactly one `knowledge_revision`; either inline `content_md` or an immutable `content_blob_hash`, never neither or both.
- A body row is immutable while present. It may be deleted only by the administrator purge transaction after all eligibility/reference checks pass.
- Retention is derived from body-row presence; purge never updates immutable `knowledge_revision` metadata.
- Deleting a body does not delete revision identity, hashes, actor, timestamp, lineage, audit, or tombstone metadata.

`item_source`

- Item/revision, source artifact, role: `initial | evidence | update | migrated`.
- Stores the exact source revision/snapshot that contributed to a knowledge revision.

`item_relation`

- Source item/revision, target item/revision, relation type: `forked_from | promoted_from | exact_duplicate_of | merged_into | supersedes`.
- Cross-space relations are allowed and are not semantic retrieval edges.
- Actor/time, state, evidence and provenance metadata.

`item_deactivation_episode`

- Item, episode number, deactivated actor/time/reason, `purge_eligible_at`, restored actor/time, purge actor/time.
- Re-deactivation creates a new episode and a new 90-day eligibility timestamp.

### 5.5 Graph and curation

`knowledge_edge`

- `id`, `space_id`, source item, target item, edge type.
- `edge_status`: `active | inactive` and `row_version`.
- Origin: `manual | semantic_approved`; automatic deterministic facts belong in `item_relation`, not the semantic edge table.
- Source/target evidence revision IDs, evidence spans/JSON, `needs_revalidation`.
- Both endpoints must belong to the same `space_id`; self-edge is rejected.
- Unique active edge identity per space/source/target/type.
- Item deactivate/restore never updates this table.

Effective edge visibility:

```text
edge.edge_status == active
AND source.lifecycle_status == active
AND source.curation_status == published
AND target.lifecycle_status == active
AND target.curation_status == published
```

`curation_finding`

- `id`, `space_id`, finding kind: `lint | exact_repeat | curation_clear | weak_complementary | near_duplicate | update | potential_conflict | semantic_edge | replace | merge | curation_release | curation_quarantine | deactivate | purge`; severity: `blocker | error | warning | info`.
- Status: `pending | approved | rejected | ignored | obsolete | applied | apply_failed` and integer `row_version`.
- Risk tier: `T0 | T1 | T2 | T3`, required reviewer, and visibility effect: `none | badge | agent_hidden | publication_blocked`.
- Source and optional target item/revision pair.
- Detector/rule/model/prompt version, explicit `rule_version`, confidence, evidence spans, proposed action, actor/time.
- Reviewer/time/reason, applied operation/actor/time, and last application error.
- A change to either compared revision makes the old finding obsolete.
- An ignored pair remains suppressed only until content or detector rule/version changes.

`change_proposal`

- `id`, finding ID, proposal kind: `stable_source_update | personal_to_part`, fixed base/source/target item and revision IDs, source artifact/snapshot, proposed Markdown hash/body reference, preview hash, state and row version.
- Rebase creates a new proposal version and marks the superseded proposal/finding obsolete; apply never mutates these fixed inputs.

`merge_event`

- `id`, operation ID, space, source item/revision, target item/revision, result revision.
- Actor/time, preview hash, state, and redirect relation.
- A merge is one reversible-history transaction; it does not promise automatic full undo after later target edits.

### 5.6 Leases and idempotency

`item_lease`

- One persistent row per item so fencing generation never resets.
- `item_id`, nullable holder/session/token hash.
- Monotonic `fencing_generation`, base item row version and base revision.
- Acquired/heartbeat/expiry/absolute-expiry times.
- Acquire increments generation; release clears holder/token but retains generation.
- The controlled client generates a 256-bit random lease token and sends it only in the lease header; the server stores a keyed HMAC digest and `key_id`. Active and previous verification keys live outside the database in the protected service-secret store so rotation can use a bounded overlap window. This makes a retried acquire idempotent without persisting or reproducing a raw secret.
- Recommended starting values: heartbeat 30 seconds, stale expiry 5 minutes, absolute lease 2 hours with explicit renewal/reconfirmation.

`operation_dedup`

- Actor, operation type, operation ID, request hash, status, serialized stable result/error, created/completed times.
- Unique `(actor, operation_type, operation_id)`.
- It protects response-loss retry and job retry from duplicate canonical commits.

### 5.7 Audit, outbox, jobs, and migration

`audit_event`

- Append-only event ID/time, request/operation ID, action/outcome.
- Actor ID plus display/role snapshots; optional space/item/base/new revision.
- Structured reason/details and remote/request metadata.
- Audit insertion occurs in the same transaction as the mutation. Heartbeats are metrics, not audit events.

`outbox_event`

- Monotonic ID, space/aggregate/version, event type/payload.
- State: `pending | processing | applied | retry | dead`.
- Availability, claimant/claim expiry, attempts, last error, created/completed times.
- Unique logical event key prevents duplicate projection work.
- Legal transitions: `pending -> processing`; `processing -> applied | retry`; expired `processing -> retry`; `retry -> processing | dead`. `applied` and `dead` are terminal. Cancellation is represented by an applied compensating event, never by deleting an event.

`projection_state`

- Projection name and space, desired generation, built generation, checksum, status/error, built time.

`durable_job`

- Job ID, operation ID, actor/space, captured scope epoch/capability, job type and handler version, stable effect key, input reference/hash, expected aggregate/revision versions.
- State: `queued | claimed | running | retry | succeeded | failed | cancelled | dead`.
- Claim owner/expiry, attempt count, progress/result/error, timestamps.
- Inputs needed after restart are durable references, not Python closures.
- Legal transitions: `queued -> claimed | cancelled`; `claimed -> running | retry | cancelled`; `running -> succeeded | retry | failed | cancelled`; expired `claimed/running -> retry`; `retry -> claimed | dead | cancelled`. `succeeded`, `failed`, `cancelled`, and `dead` are terminal. Only jobs explicitly marked cancellable accept cancellation.
- The P1.9 repository accepts mutations only in a caller-owned transaction,
  canonicalizes and hashes a strict RFC 8785/I-JSON subset, and validates every
  stored JSON/hash field before startup/claim. New user jobs bind only to an
  `in_progress` operation-dedup row; exact existing jobs remain replayable
  after the producer operation becomes terminal.
- Each process constructs one immutable registry keyed by exact
  `(job_type, handler_version)` and actor kind. Startup fails closed if any
  nonterminal job lacks that exact handler contract. Claim attempt count is the
  ABA fence; owner, attempt, live expiry, state, and cancellation are repeated
  in every worker compare-and-swap.
- Handler preparation runs outside SQLite transactions. Immediately before a
  canonical effect, the kernel rechecks the live claim, current `ScopeResolver`
  authorization and captured epoch/part, plus handler-specific expected state.
  The domain effect and `succeeded` result then commit in one IMMEDIATE
  transaction. This exactly-once boundary applies only to canonical SQLite
  effects, not file, network, or other nontransactional external effects.
- P1.9 exposes one-job primitives only. P7 owns daemon loops, heartbeat and
  backoff policy, full producer cutover, cross-job GPU serialization/fairness,
  monitoring, and production service packaging.
- P7.2 supplies the explicit central loop without starting it implicitly.
  Claim ordering rotates exact handler versions and actor lanes from durable
  attempt history, long preparation is protected by a renewable heartbeat, and
  only one job executes at a time. Production local GGUF calls require the
  exact worker process role while it owns one machine-wide Windows mutex; web
  roles cannot discover, resolve, load, or execute GGUF. The historical
  in-process callable executor and Outlook scheduler are development-only.
  P7.5 freezes the production service wrapper/account/ACL/start order behind
  disabled traffic. P7.6 adds a versioned, secret-free, read-only health
  provider for both web hosts with fixed-local-NTFS path revalidation,
  SQLite/schema/flag/queue/lease/backup/projection/capacity gates, and exact
  start-wrapper enforcement. P7.7 freezes the 54-name fault surface and proves
  transaction rollback, post-commit replay, real-process kill/lease recovery,
  projection filesystem recovery, backup-under-write, and a fixed-seed
  20-worker-termination profile without starting production services. P7.8
  retains target-class performance, and P7.9 retains signed target restore
  evidence.

`legacy_migration_map`

- Legacy kind/path, source hash, resolved owner, new artifact/item/revision/edge IDs, status/error, migration run and timestamp.
- Unique `(legacy_kind, normalized_path, source_hash)` provides repeatable import.

P2.1 inventory boundary:

- The schema-v2 inventory is a read-only `inventory_proposal` artifact, not a
  canonical database or migration outcome. It records only
  `physical_file | logical_record | unavailable` records and gives each one a
  proposed terminal class plus `scanner | planner_required` authority.
- Parent/child counts, relationship endpoints, owner claims, source identities,
  category aggregates, source-root facts, and the manifest hash must close
  before P2.2 may consume the artifact.
- Frozen-copy/VSS identity and attestation are operator-supplied inputs. The
  scanner format/time-checks and binds them to final root facts and scan-time
  high-water markers; it does not create or authenticate the snapshot or prove
  writer quiescence/application consistency.
- P2.2 owns reviewed owner/organization mapping and final plan decisions;
  P2.3-P2.5 own imports; P2.6 owns observed outcome reconciliation. Reads never
  lazy-import and the inventory report never becomes application authority.
- The P2.2 draft embeds P2.1 metadata plus original ledger order and copies
  each sanitized source row into its planned row. Independent validation
  reconstructs and validates the exact P2.1 manifest before applying the
  frozen category/relationship decision table. Review confirmation wraps the
  exact technical draft; it is not a database apply or observed outcome.
- P2.3/P2.4 apply only the exact reviewed import subsets. P2.4 resolves valid
  graph/candidate relationships only when every endpoint remains in one
  reviewed personal space, commits canonical effects with immutable migration
  checkpoints/audit/outbox, and stores legacy audit/log/tombstone evidence as
  explicitly historical rather than current authorization or publication
  state. Unsupported import effects fail the complete apply before execution.

## 6. State machines

### 6.1 Item lifecycle

```text
active --deactivate--> inactive --restore--> active
active --reviewed merge as source--> merged
inactive --admin purge after eligibility--> purged_tombstone
purged_tombstone --> terminal
```

- Active → purge is forbidden.
- Deactivate while another live edit lease exists returns a lock conflict. A leader/admin must use the separate force-unlock command first if necessary.
- Deactivate and restore do not create content revisions and do not mutate edge rows.
- Restore after a long inactive period may set a review-needed finding without hiding the restored document unless a blocker rule applies.
- Reopening a merged source is deferred beyond phase 1. Recovery in phase 1 creates an explicit new item/revision from retained history; no undocumented `merged -> active|inactive` transition exists.

### 6.1.1 Item curation visibility

```text
provisional --matched no-blocker finding command--> published
provisional --matched blocker finding command--> quarantined
published/quarantined --new revision requiring rescan--> provisional
```

- No generic repository update may change `curation_status`.
- `provisional -> published|quarantined` requires the current versioned finding/policy result; finding state, item row version, FTS, audit, outbox, and visibility generation commit together.
- `published|quarantined -> provisional` is allowed only in the content-revision transaction that also enqueues the versioned rescan job, removes default Agent visibility/FTS as required, and writes audit/outbox.
- A `weak_complementary` finding is T2/human-reviewable but has `badge` visibility and no proposed domain action, so the item may remain published. Strong `near_duplicate`, update, or conflict findings use `agent_hidden` until reviewed.

### 6.2 Lease

```text
free --acquire(+generation)--> held --heartbeat--> held
held --owner release--> free
held --expiry/revoke/force unlock(+generation)--> free
```

Every save simultaneously verifies actor membership, holder/session, token hash, fencing generation, expiry, item active state, base revision, and expected row version.

### 6.3 Curation finding

```text
pending --> approved | rejected | ignored | obsolete
approved --> applied (through a normal versioned command) | apply_failed | obsolete
apply_failed --> approved | rejected | obsolete
```

An approval is not itself an unrestricted database patch. It invokes the same edit/edge/merge service with current revision and permission checks.

### 6.4 Job/outbox

```text
job: queued --> claimed --> running --> succeeded
job: queued/claimed/running --> cancelled (only when cancellable)
job: running --> failed
job: claimed/running --claim timeout or retryable error--> retry --> claimed | dead
outbox: pending --> processing --> applied
outbox: processing --claim timeout or retryable error--> retry --> processing | dead
```

Result commit is idempotent; a worker crash after commit but before acknowledgement cannot duplicate a revision.

## 7. Transaction contracts

### 7.1 Organization membership move

In one short transaction:

1. Verify administrator and target part.
2. Close old membership and add the new membership, or leave unassigned.
3. Increment the user's `scope_epoch`.
4. Revoke/increment fences for leases held in the old part.
5. Append audit and relevant outbox/job invalidation events.

Every later save/job commit rechecks membership, so an old browser or queued job cannot write to the former part.

### 7.2 Single/dual publication

Extraction, hashing, source identity, exact hashing, structural lint, and cheap candidate retrieval happen before the write transaction. LLM/semantic classification never runs in the transaction. At commit:

1. Verify `operation_id` and request hash or return the stable previous response.
2. Revalidate actor, source snapshot, target capabilities, and current part.
3. Insert/preserve source artifact and one publication operation.
4. Resolve each target independently:
   - same origin and same origin revision/hash → `no_op`;
   - different origin with exact same-space content → `provenance_added`;
   - stable origin with changed content, including a re-published Notion page → `update_proposed` against the current item;
   - otherwise create a new item/revision. If incremental risky analysis is outstanding, create it `provisional`; only the later no-blocker promotion command can make it published.
5. For target `both`, use separate item/revision IDs and one lineage group; never create a synchronization pointer.
6. Insert audit, target results, and outbox events and commit all target outcomes together.

One target may attach provenance while the other creates an item, but the batch as a whole still commits or rolls back atomically.

Every successful target response includes `source_artifact_id`, `item_id`, and `resolved_revision_id`. It includes `created_revision_id` only when that operation inserted a revision. A first-time `both` publication returns two distinct item IDs and two distinct revision IDs. Retrying the same operation returns byte-equivalent stable target results; only a new operation may resolve an exact repeat as `no_op` or `provenance_added`.

Outcome side effects are fixed:

| Outcome | Item/revision | `item_source` | Audit | Outbox/generation |
|---|---|---|---|---|
| `created` | Create item and revision | Add exact source link | `publication.created` | Visibility event; increment target generation |
| `provenance_added` | Reuse current item/revision | Add missing idempotent source link | `publication.provenance_added` | Event/generation only if a visible provenance projection changes |
| `update_proposed` | Reuse current item/revision | Record proposed source link without replacing current | `publication.update_proposed` | Finding-queue event; no Agent-visible generation change |
| `no_op` | Reuse current item/revision | No duplicate row | `publication.no_op` | No projection event or generation change |

### 7.3 Lease acquire/save/release

- Acquire uses `BEGIN IMMEDIATE`, validates item and membership, validates/hashes the client-generated 256-bit token, treats an expired holder as revocable, and increments persistent fencing generation. A retry of the same operation/session/token returns the same lease metadata without another increment.
- Heartbeat is the sole phase-1 mutation that does not require an `operation_id`; it conditionally extends the same lease only when token, generation, holder, session, and non-expired lease match.
- Save inserts an immutable revision, compare-and-swaps the item pointer/version, marks affected semantic edges `needs_revalidation` when necessary, inserts audit/outbox, and releases the lease in one commit.
- A rejected save returns the current revision and keeps the client's draft; it never offers “force overwrite.”

### 7.4 Deactivate and restore

- Both commands require `operation_id` and expected row version.
- Deactivate requires a reason and no foreign live edit lease, adds a deactivation episode, sets inactive, and emits audit/outbox.
- Restore closes the latest episode and sets active; it preserves item/revision/edge IDs and emits audit/outbox.
- Neither command writes an edge row or content revision.

### 7.5 Merge

1. Build a three-pane preview outside the transaction using two fixed revision
   IDs. No process-memory preview cache is authoritative: apply repeats the
   exact confirmed result Markdown and the server recomputes the preview hash.
2. On apply, acquire/check both item leases in sorted item-ID order and validate both expected revisions.
3. Insert the result revision on the target, mark source `merged`, create `merged_into` relation/redirect, and append audit/outbox atomically.
4. Do not automatically move every semantic edge. Exact duplicates may use redirect projection; other edge moves become review findings.

### 7.6 Change proposal rebase/apply

- Stable-source update fixes the source snapshot/base target revision/new source body; personal-to-part fixes fork-base/personal-current/part-current.
- Preview and any LLM-assisted three-way draft run outside transactions against those immutable inputs.
- Rebase requires the old finding/proposal version and new target revision, creates a new proposal version, and obsoletes the former proposal/finding atomically.
- Apply rechecks human reviewer capability, proposal/finding version, target lease token/fence, expected target revision/row version, membership, and preview hash. It creates exactly one target revision and atomically marks the proposal/finding applied with audit/outbox/FTS; any mismatch changes no domain state.

### 7.7 Permanent purge

- Administrator only; inactive for at least 90 days measured by SQLite UTC milliseconds in the eligibility/compare-and-swap statement, never by client time; explicit preview and confirmation; expected row version required.
- Block item-revision purge while an active pin/binding/reference requires that revision body. A source artifact shared by another surviving item does not prevent this item's tombstone/revision-body purge; it causes artifact/extraction cleanup to be recorded `retained_due_reference` until the last dependent item is eligible.
- Retain item ID/tombstone, revision metadata/hashes, essential audit, redirect, and minimum provenance.
- In one canonical transaction, change the item to `purged_tombstone`, delete eligible `knowledge_revision_body` rows, remove corresponding transactional FTS rows, and append audit/outbox/GC work. A retryable GC job deletes only now-unreferenced blobs after its own reference recheck.

## 8. Retrieval and projection contract

Canonical retrieval:

- Resolve one authorized `space_id` from the conversation or management request.
- Query only current active/published revisions in that space through FTS/top-K ranking.
- Add only a relevant semantic graph neighborhood and relevant recent item events.
- Never inject an entire growing `index.md` or raw audit log into the Agent prompt.
- Treat retrieved knowledge as untrusted reference data and delimit it from system instructions.
- Pins are validated against the same conversation space and current visibility on every turn.

SQLite FTS is a rebuildable in-database query index, not a second source of truth. The row representing an item's current searchable revision is updated or removed in the same transaction as the visible item/revision state so read-after-write and scope filtering remain consistent. A full rebuild derives it entirely from canonical items/revisions. Optional embedding/vector caches may lag asynchronously; retrieval falls back to transactional FTS and never to another space or stale file projection.

Projection flow:

1. Canonical mutation increments space generation, inserts an outbox event, and
   advances `projection_state.desired_generation` in the same transaction.
2. The explicit projector recovers expired claims and claims the oldest available
   event with owner, attempt, and expiry fencing. Long builds renew below one
   third of the lease; import and construction start no loop.
3. It captures one read-consistent space snapshot and writes the complete
   generation to immutable staging: Markdown sources, index, file-visible
   activity log, graph, and backlinks. The log uses an exact allowlist of
   generation-changing canonical actions; lease, conversation, preview, and
   finding-only audits cannot mutate bytes of an existing numeric generation.
4. It verifies every content and manifest checksum, pins the completed generation,
   then atomically replaces only the small `manifest.json`.
5. It advances `projection_state.built_generation` idempotently and acknowledges
   the outbox event only when canonical, desired, and built generations still
   match and the checksum-bound state is `ready`. Canonical advance after
   manifest publication forces retry; a stale failure cannot poison a newer
   settled generation.
6. UI may show projection lag, but Agent retrieval falls back to canonical DB/FTS
   and never reads or combines files from mismatched generations. Full rebuild
   and settled incremental rebuild must produce the same manifest checksum.

Suggested projection layout:

```text
projections/<space-id>/
  manifest.json
  generations/<generation>/
    sources/<item-id>.md
    index.md
    log.md
    graph/graph.json
    graph/backlinks.json
```

The item ID, not a mutable title, is the projection filename identity. A title may be included in front matter and UI metadata.

## 9. Curation pipeline

Synchronous before publish:

- source identity and raw hash;
- empty/corrupt/unsupported input;
- deterministic Markdown/front-matter checks;
- exact content hash within the target space;
- safe structural lint.

Asynchronous after canonical ingest:

1. Candidate retrieval by source identity, FTS/BM25, MinHash, and/or embedding top-K.
2. Evidence extraction for only the top candidates.
3. Optional budgeted LLM classification outside DB transactions.
4. Persist versioned finding with evidence; revalidate revision IDs before commit.

Activation policy:

- Exact repeat: no new logical item; add provenance/no-op.
- Blocker/corrupt/empty: quarantined.
- Strong near-duplicate, update, or potential conflict: provisional and excluded from Agent retrieval.
- Weak similarity/complementary content: published with warning.
- Low similarity: published.

Execution risk tiers are independent of severity:

| Tier | Examples | Automation boundary |
|---|---|---|
| `T0` | Source identity, hashes, parser/Markdown structure, empty/corrupt checks | May run automatically; may only emit evidence, no semantic relation or destructive effect |
| `T1` | Deterministic `forked_from`, `promoted_from`, exact provenance/duplicate relation, or deterministic no-blocker publication release | May apply automatically only with versioned non-LLM rule, exact evidence, and idempotent audit/outbox |
| `T2` | Semantic edge, potential conflict, replacement/update resolution, merge | Human authorized space-editor review is mandatory (personal owner or active part member); model may propose but never approve/apply |
| `T3` | Deactivate, purge, organization/permission/security-sensitive action | Never model-executed; only an explicit authenticated capability/admin command can initiate it |

`required_reviewer` and `visibility_effect` are derived from the tier and finding kind by a versioned policy table, not freely supplied by a detector. Approval of a T2 finding invokes the ordinary current-version command and atomically updates finding status, domain state, audit, outbox, and visibility. T3 findings may link to a manual command preview but approval itself has no domain effect.

No nightly all-pairs semantic scan. Process changed items incrementally; run integrity/reindex checks on a schedule and full semantic scans only manually or within an explicit idle-time budget.
