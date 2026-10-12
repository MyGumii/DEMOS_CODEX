# Canonical SQLite physical schema v1 contract

> This v1 physical contract and its completed migrations are immutable history.
> Do not edit them for the approved successor. Forward logical v8-v12 additions
> are logically registered in `second_brain_evolution/FORWARD_SCHEMA.md` by
> `SBE-0.1`; they become physical only through new checksumed SBE-1.x/SBE-3.2
> migrations. No v1-v7 migration source is rewritten.

This document is the authoritative physical contract for schema version 1 of the
shared Knowledge Agent. It is intentionally stricter than a logical entity list:
the numbered migration must implement every column, foreign key, check, index,
trigger, seed, and postcondition below. Names may not be changed without updating
the schema checksum and all repository SQL in the same change.

Schema v1 is frozen; it has not been rewritten. The canonical forward catalog is
currently at physical schema version 7: v2 adds account password-policy metadata,
v3 adds the reviewed `legacy_migration_map` locator-uniqueness and immutable-
history constraints, v4 adds the immutable asset-relationship checkpoint, v5
adds P2.4 effect checkpoints, v6 adds P2.5 conversation targets and the
immutable Notion session-link checkpoint, and v7 adds P5.6 blob-GC claim,
reference, and backup-race guards documented in sections 9.7-9.11. Existing
databases advance through v1-v7 in order; fresh databases execute that same
seven-version catalog.

This is a migration specification, not executable SQL. Every abbreviated domain
expression below is defined exactly in section 2 and must be expanded into ordinary
SQLite DDL. There are no phase-1 schema choices left to the implementation agent.

## 1. Runtime and connection gate

### 1.1 Only accepted SQLite build

Production, migration, backup, restore, worker, projector, and load-test processes
must all use the same SQLite library and pass both exact comparisons:

```text
sqlite_version()   == 3.53.4
sqlite_source_id() == 2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc
```

That pair is the complete v1 allowlist. A numerically newer, older, backported, or
same-version/different-source build is rejected until a reviewed migration changes
this document and the allowlist. The source ID is published in the official
[SQLite 3.53.4 release record](https://www.sqlite.org/releaselog/3_53_4.html).

Preflight must additionally prove:

- `sqlite_compileoption_used('ENABLE_FTS5') = 1`;
- a temporary FTS5 table can be created, queried, and dropped;
- the canonical DB resolves to a local NTFS volume, not UNC, mapped network,
  removable, ReFS, FAT, or a reparse point whose final target is non-local/non-NTFS;
- all processes report the same version and source ID;
- no failpoint configuration is present.

`SingleMigrator` must execute this gate itself for the exact configured canonical
database target; it may not accept a caller-supplied Boolean, report path, cached
JSON result, or other unverifiable attestation as a substitute. It reruns the P0.4
local fixed-volume NTFS/final-Win32-target checks for the database, data, blob, and
projection paths and reruns executable JSON and FTS5 probes with the migration
runtime. For an existing database it binds the accepted final path, volume
identity, and database file identity before open and verifies that same file
identity after open. For an empty bootstrap it binds the accepted final parent and
target path before creation, then captures the created database file identity and
reruns the DB-scoped gate against that identity before any schema DDL. Any path,
final-target, volume, file-identity, JSON, or FTS result that cannot be observed
locally and tied to that open database fails migration.

### 1.2 Per-connection settings

The connection factory applies and reads back these values before exposing a
connection:

```sql
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 10000;
PRAGMA synchronous = FULL;
PRAGMA trusted_schema = OFF;
PRAGMA recursive_triggers = ON;
PRAGMA temp_store = MEMORY;
```

The deployment migrator sets `PRAGMA journal_mode=WAL` once and verifies the
returned value is `wal`. Ordinary connections verify rather than change journal
mode. `wal_autocheckpoint` is `0`; one maintenance coordinator performs bounded
`PASSIVE` checkpoints. `TRUNCATE` is permitted only during a drained maintenance
or backup-finalization operation.

All connections use explicit transaction control. Canonical writes begin with
`BEGIN IMMEDIATE`; no network, LLM, OCR, embedding, artifact copy, projection file
write, or user interaction occurs before commit/rollback.

## 2. Common physical domains

All ordinary v1 tables are `STRICT` rowid tables. No ordinary v1 table uses
`WITHOUT ROWID`, including join/history tables with composite primary keys.
`search_document` keeps its explicit integer rowid contract, while the other
ordinary tables retain SQLite's normal rowid behavior. The FTS5 virtual table is
not an ordinary table. A later schema version may review `WITHOUT ROWID`; v1 does
not permit it. The migration never depends on implicit type coercion.

### 2.1 Types and reusable checks

- `UUID`: `TEXT NOT NULL` with the expanded check
  `length(x)=36 AND substr(x,9,1)='-' AND substr(x,14,1)='-' AND substr(x,19,1)='-' AND substr(x,24,1)='-' AND length(replace(x,'-',''))=32 AND replace(x,'-','') NOT GLOB '*[^0-9a-f]*' AND x=lower(x)`.
- `USER_ID`: `TEXT NOT NULL` with
  `length(x) BETWEEN 1 AND 64 AND x=lower(trim(x)) AND x NOT GLOB '*[^a-z0-9._-]*'`. Existing IDs such as
  `admin` and `user01` are valid.
- `SHA256`: `TEXT NOT NULL` with
  `length(x)=64 AND x=lower(x) AND x NOT GLOB '*[^0-9a-f]*'`.
- `UTC_MS`: `INTEGER NOT NULL` with `x >= 0`.
- `BOOL`: `INTEGER NOT NULL` with `x IN (0,1)`.
- `JSON_OBJECT`: `TEXT NOT NULL DEFAULT '{}'` with
  `json_valid(x) AND json_type(x)='object'`.
- `JSON_ARRAY`: `TEXT NOT NULL DEFAULT '[]'` with
  `json_valid(x) AND json_type(x)='array'`.
- Required human text uses `TEXT NOT NULL` plus `length(trim(x)) BETWEEN 1 AND N`.
- Optional text is nullable; an empty string is never a substitute for `NULL`.

When a table says `nullable UUID`, `nullable SHA256`, `nullable UTC_MS`, or
`nullable JSON_OBJECT/ARRAY`, only the domain's `NOT NULL` and default are removed;
all format, range, and JSON checks still apply when the value is non-null.

The migration expands these expressions at every named column; it does not create
application-defined SQL functions for them.

### 2.2 Database time

The sole production clock expression is:

```sql
CAST(unixepoch('subsec') * 1000 AS INTEGER)
```

Every service transaction selects it once as `db_now_ms` after `BEGIN IMMEDIATE`
and binds that value to all actor-visible times in the transaction. Timestamp
defaults use the same parenthesized expression only as a defensive fallback.
Client times never determine ordering, lease validity, purge eligibility, audit
time, job claims, or idempotency expiry. Purge eligibility is exactly
`deactivated_at + 7776000000` milliseconds, and the eligibility query compares it
to the clock expression in SQL.

### 2.3 Row versions and immutable identities

Mutable aggregate tables carry
`row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1)`. Their update trigger rejects a row unless
`NEW.row_version = OLD.row_version + 1`. Identity, owner, and immutable-scope fields listed later are rejected by
separate update triggers. Repositories use compare-and-swap predicates rather than
read-then-write updates.

## 3. Schema, identity, and organization

### 3.1 `schema_migration`

| Column | Physical contract |
|---|---|
| `version` | `INTEGER PRIMARY KEY CHECK(version >= 1)` |
| `name` | required text, max 120 |
| `checksum_sha256` | SHA256 |
| `applied_at` | UTC_MS, DB default |
| `app_version` | required text, max 80 |
| `sqlite_version` | `TEXT NOT NULL CHECK(sqlite_version='3.53.4')` |
| `sqlite_source_id` | `TEXT NOT NULL` checked against the exact allowlisted source ID |

The v1 migration inserts exactly one row with `version=1` and sets
`PRAGMA user_version=1`. Startup fails if either version, checksum, SQLite version,
or source ID differs.

### 3.2 `org_unit`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `kind` | `TEXT NOT NULL CHECK(kind IN ('responsibility','team','part'))` |
| `parent_id` | nullable UUID FK `org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `name` | required text, max 120 |
| `name_key` | required text, max 160; application-produced Unicode NFKC+casefold key for ordinary rows; the reviewed bootstrap-root exception is the exact frozen literal `AMHS담당` below |
| `status` | `TEXT NOT NULL CHECK(status IN ('active','inactive'))` |
| `created_at`, `updated_at` | UTC_MS, DB defaults |
| `created_by`, `updated_by` | nullable USER_ID FK `app_user(id) ON DELETE RESTRICT`; null only on rows created by the reviewed v1 bootstrap/import migration |
| `row_version` | versioned aggregate |

Indexes:

- unique `ux_org_unit_active_sibling` on
  `(kind, ifnull(parent_id,''), name_key) WHERE status='active'`;
- `ix_org_unit_parent` on `(parent_id, status, kind, name_key)`.

Triggers:

- `trg_org_unit_parent_insert` and `_update` require responsibility/null,
  team/active-responsibility, or part/active-team respectively.
- The update trigger rejects `id` or `kind` changes and uses a recursive CTE to
  reject any parent change that reaches `NEW.id`.
- `trg_org_unit_inactivate_guard_update` rejects inactivation of a part referenced by any
  `app_user.current_part_id`, any live part-leader assignment, or an active part
  space. It rejects team/responsibility inactivation while an active child exists.

The migration seeds exactly one active responsibility root with
`id='fb90d31e-9576-5823-84ef-241b7552f214'`, `name='AMHS담당'`, and
`name_key='AMHS담당'`. These exact deterministic values are frozen in
`knowledge_core/migrations/seed_manifest_v1.json` and are never generated or
inferred by name at migration time. `created_at` and `updated_at` are deliberately
not frozen seed literals: the INSERT uses their DB defaults, so they record the
actual migration-time database clock. Null bootstrap actors solve the initial
dependency cycle (organization before first user); ordinary repositories always
require non-null actors.

### 3.3 `app_user`

| Column | Physical contract |
|---|---|
| `id` | USER_ID primary key; immutable login ID |
| `display_name` | required text, max 120 |
| `password_hash` | required text, max 512 |
| `system_role` | `TEXT NOT NULL CHECK(system_role IN ('admin','user'))` |
| `account_status` | `TEXT NOT NULL CHECK(account_status IN ('active','disabled'))` |
| `current_part_id` | nullable UUID FK `org_unit(id) ON DELETE RESTRICT`; null is the intentional unassigned state |
| `scope_epoch` | `INTEGER NOT NULL DEFAULT 1 CHECK(scope_epoch >= 1)` |
| `created_at`, `updated_at` | UTC_MS, DB defaults |
| `disabled_at` | nullable UTC_MS |
| `row_version` | versioned aggregate |

Checks require `account_status='disabled'` exactly when `disabled_at IS NOT NULL`.
Triggers reject ID changes; validate a non-null `current_part_id` as an active
`part`; require a current-part change (including assign/unassign) or account-status
change to increment `scope_epoch` by exactly one; and reject `scope_epoch` changes
without one of those changes.

Indexes: `ix_app_user_part_status(current_part_id, account_status, id)` and
`ix_app_user_status(account_status,id)`.

This column is the authoritative current assignment and, together with the partial
membership index, guarantees at most one current part. A null value is valid and
grants personal-space capability only.

### 3.4 `org_membership`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `part_id` | UUID FK `org_unit(id) ON DELETE RESTRICT` |
| `valid_from` | UTC_MS |
| `valid_to` | nullable UTC_MS; check null or `valid_to > valid_from` |
| `assigned_by` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `reason` | required text, max 1000 |

Indexes:

- unique partial `ux_org_membership_current_user(user_id) WHERE valid_to IS NULL`;
- `ix_org_membership_part_period(part_id, valid_to, valid_from, user_id)`;
- `ix_org_membership_user_history(user_id, valid_from DESC, id)`.

Insert/update triggers validate that `part_id` is a part; a new open episode must
target an active part. Membership rows may only change from `valid_to NULL` to one
non-null value; every other column and a closed row are immutable. Delete is
always rejected.

The organization service is the only writer. Assign/move/unassign closes any open
old episode, optionally inserts one open new episode, updates
`app_user.current_part_id`, increments scope epoch, revokes old-part leases/jobs,
writes audit/outbox, then asserts before commit either zero open memberships plus a
null current part, or one open membership whose part equals `current_part_id`.
Creating an assigned account inserts its initial episode in the same transaction;
creating an intentionally unassigned account creates none. A failed postcondition
rolls back.

### 3.5 `org_role_assignment`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `org_unit_id` | UUID FK `org_unit(id) ON DELETE RESTRICT` |
| `role` | `TEXT NOT NULL CHECK(role='part_leader')` |
| `valid_from` | UTC_MS |
| `valid_to` | nullable UTC_MS, greater than `valid_from` when present |
| `assigned_by` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `reason` | required text, max 1000 |

Unique partial index `ux_org_role_active_unit_role` covers `(org_unit_id,role)`
where `valid_to IS NULL`; lookup index covers `(user_id,valid_to,org_unit_id)`.

Triggers require `org_unit_id` to be an active part and an open assignment's user
to be active with `current_part_id=org_unit_id`. Rows have the same close-only,
never-delete immutability as memberships.

An account disable, part move/unassign, or membership close is rejected while that
user has an open leader assignment. The organization command must first set every
owned part space's `write_enabled=0`, then close the old leader role. The role-close
trigger rejects while any such space remains write-enabled. The same transaction
must then do exactly one of:

- insert one replacement open leader who is active and a current member of that
  part, then optionally re-enable part writes; or
- leave zero open leaders and every part space write-disabled.

This disable-close-insert sequence is required because SQLite unique indexes are
not deferrable: it permits no committed two-leader or leaderless-write-enabled
state. The account/membership change, role close/replacement, space row-version
updates, scope-epoch/session/lease/job revocation, audit, and outbox all commit or
roll back together. Immediately before commit the service asserts for each affected
part exactly one active/current open leader, or zero open leaders plus zero
write-enabled spaces. Any other result rejects the whole command. This same
postcondition applies to leader account disable as well as move/unassign.

## 4. Knowledge spaces and conversations

### 4.1 `knowledge_space`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `space_kind` | `TEXT NOT NULL CHECK(space_kind IN ('personal','part','team','responsibility'))` |
| `owner_user_id` | nullable USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `owner_org_unit_id` | nullable UUID FK `org_unit(id) ON DELETE RESTRICT` |
| `status` | `TEXT NOT NULL CHECK(status IN ('active','frozen','inactive'))` |
| `write_enabled` | BOOL, default 0 |
| `generation` | `INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0)` |
| `created_at`, `updated_at` | UTC_MS, DB defaults |
| `row_version` | versioned aggregate |

Checks require exactly one owner and enforce the personal/organizational null
shape. Unique partial index `ux_space_personal_owner` covers `owner_user_id` where
`space_kind='personal'`; `ux_space_org_owner` covers `owner_org_unit_id` where
`space_kind<>'personal'`. Add `ix_space_kind_status(space_kind,status,id)`.

Insert/update owner triggers enforce:

- personal owner is `owner_user_id` and has no org owner;
- part/team/responsibility owner is an `org_unit` of the identical kind;
- owner fields, `space_kind`, and ID are immutable;
- `write_enabled=1` requires `status='active'`;
- a part space can enable writes only if its part has one open leader assignment;
- team and responsibility spaces must have `write_enabled=0` in schema v1.

The schema deliberately supports future independent team/responsibility owners.
Phase 1 seeds only personal and part spaces and exposes only those kinds. No higher
space is a union or query-time projection of child spaces.

### 4.2 `conversation`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `owner_user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `space_id` | UUID FK `knowledge_space(id) ON DELETE RESTRICT` |
| `captured_scope_epoch` | `INTEGER NOT NULL CHECK(captured_scope_epoch >= 1)` |
| `title` | `TEXT NOT NULL DEFAULT '' CHECK(length(title)<=200)` |
| `status` | `TEXT NOT NULL CHECK(status IN ('active','archived'))` |
| `metadata_json` | JSON_OBJECT |
| `created_at`, `updated_at` | UTC_MS, DB defaults |
| `row_version` | versioned aggregate |

Add `UNIQUE(id,space_id)`, index `ix_conversation_owner_time` on
`(owner_user_id,updated_at DESC,id)`, and `ix_conversation_space_time` on
`(space_id,updated_at DESC,id)`.
Triggers reject changes to `id`, `owner_user_id`, `space_id`, or
`captured_scope_epoch`. Creation service verifies personal ownership or current
part membership and copies the actor's current `scope_epoch`. Every part turn
revalidates both current part and epoch; personal conversations do not become
stale solely because the user later moves part.

### 4.3 `conversation_message`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `conversation_id` | UUID FK `conversation(id) ON DELETE RESTRICT` |
| `sequence_no` | `INTEGER NOT NULL CHECK(sequence_no>=1)` |
| `role` | `TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool'))` |
| `content_json` | JSON_OBJECT |
| `created_at` | UTC_MS, DB default |

Unique indexes `ux_message_id_conversation(id,conversation_id)` and
`ux_message_sequence(conversation_id,sequence_no)`, plus
`ix_message_conversation_time(conversation_id,created_at,id)`. Update and delete are rejected; redaction is a
new message/event, not mutation.

### 4.4 `conversation_turn`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `conversation_id` | UUID FK `conversation(id) ON DELETE RESTRICT` |
| `actor_user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `dedup_id` | UUID unique FK `operation_dedup(id) ON DELETE RESTRICT` |
| `operation_id` | caller UUID |
| `request_sha256` | SHA256 |
| `state` | `TEXT NOT NULL CHECK(state IN ('accepted','running','completed','failed','cancelled'))` |
| `user_message_id` | UUID NOT NULL; composite FK with conversation to message |
| `assistant_message_id` | nullable UUID; composite FK with conversation to message |
| `final_response_json`, `error_json` | nullable JSON_OBJECT |
| `created_at`, `updated_at`, `completed_at` | UTC_MS / UTC_MS / nullable UTC_MS |
| `row_version` | versioned aggregate |

Unique indexes `ux_turn_id_conversation(id,conversation_id)`,
`ux_turn_conversation_operation(conversation_id,operation_id)`, and
`ux_turn_dedup(dedup_id)`; indexes
`ix_turn_conversation_time(conversation_id,created_at DESC,id)` and
`ix_turn_state(state,updated_at,id)`. Triggers require the user message role to be
`user`, assistant message role to be `assistant`, actor to own the conversation,
and dedup actor/operation/hash/type (`conversation_turn`) to match.

State transitions are accepted→running/failed/cancelled,
running→completed/failed/cancelled; terminal states cannot transition. Completed
requires assistant message, final response, and completion time with no error.
Failed requires error/completion and no assistant message. Cancelled requires
completion and no final response. Identity, scope, operation, request, actor, and
user message are immutable. A new turn is inserted only in `accepted` state with
row version 1 and equal created/updated times; it cannot be born running or
terminal. The user message, accepted turn, and in-progress dedup
row are inserted atomically; completion inserts at most one assistant message and
commits the turn/dedup result atomically.

### 4.5 `conversation_turn_event`

| Column | Physical contract |
|---|---|
| `turn_id`, `conversation_id` | UUID composite FK to conversation turn |
| `sequence_no` | `INTEGER NOT NULL CHECK(sequence_no>=1)` |
| `event_type` | `TEXT NOT NULL CHECK(event_type IN ('accepted','delta','tool','completed','failed','cancelled'))` |
| `payload_json` | JSON_OBJECT |
| `created_at` | UTC_MS, DB default |

Primary key `(turn_id,sequence_no)` and index
`ix_turn_event_conversation(conversation_id,turn_id,sequence_no)`. Rows are
immutable. SSE/WebSocket reconnect resumes strictly after the caller's last
sequence number; the completed assistant message remains canonical.

### 4.6 `conversation_context_binding`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `conversation_id` | UUID FK conversation RESTRICT |
| `binding_type` | `TEXT NOT NULL CHECK(... IN ('workspace_file','project_context','notion_page','source_artifact'))` |
| `binding_mode` | `TEXT NOT NULL CHECK(... IN ('snapshot','live'))` |
| `external_key` | required text, max 1000 |
| `source_artifact_id` | nullable UUID FK source artifact RESTRICT |
| `snapshot_sha256` | nullable SHA256 |
| `display_metadata_json` | JSON_OBJECT |
| `status` | `TEXT NOT NULL CHECK(status IN ('active','unavailable','detached'))` |
| `status_reason` | nullable text, max 1000 |
| `created_by` | USER_ID FK app user RESTRICT |
| `created_at`, `status_changed_at`, `detached_at` | UTC_MS / UTC_MS / nullable UTC_MS |
| `row_version` | versioned aggregate |

Snapshot mode requires both `source_artifact_id` and `snapshot_sha256`; an insert
trigger verifies the artifact/snapshot content hash. It allows only active/detached
status. The immutable snapshot remains usable within this authorized conversation
after access to the original live resource is later revoked; it never becomes a
cross-conversation or cross-space grant. `binding_type='source_artifact'` requires
snapshot mode.

Live mode requires both artifact and snapshot columns null. Every turn reauthorizes
`external_key` through the type-specific adapter. Loss/unavailability records
status unavailable with a non-empty reason and DB time; the binding row and prior
conversation messages remain. Successful later authorization may change unavailable
back to active. Active or unavailable may change to detached; detached is terminal.
Detached time is non-null exactly for detached, unavailable requires reason, and
active requires null reason/detached time.

Create unique partial index
`ux_context_binding_attached(conversation_id,binding_type,binding_mode,external_key) WHERE status<>'detached'` and
`ix_context_binding_conversation(conversation_id,status,id)`. Conversation, type,
mode, key, artifact, snapshot, display metadata, creator, and creation time are
immutable; legal updates change only status shape, status time, row version, and the
live availability reason. Creation always revalidates actor/resource access.
Default private memory or project context is never implicitly inserted here.

### 4.7 `conversation_summary`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `conversation_id` | UUID FK conversation RESTRICT |
| `covered_through_sequence_no` | integer >=1 |
| `summary_md` | non-empty TEXT |
| `summary_sha256` | SHA256 |
| `model_id`, `prompt_version`, `summarizer_version` | required text, max 200/120/120 |
| `status` | `TEXT NOT NULL CHECK(status IN ('active','invalidated'))` |
| `created_by_user_id` | nullable USER_ID FK app user RESTRICT; null denotes system summarizer |
| `created_at`, `invalidated_at` | UTC_MS / nullable UTC_MS |
| `invalidation_reason` | nullable text, max 1000 |
| `row_version` | versioned aggregate |

Trigger requires the covered sequence to exist in the same conversation. Unique
partial index
`ux_conversation_summary_active(conversation_id) WHERE status='active'`; history index
`ix_conversation_summary_history(conversation_id,created_at DESC,id)`. Summary
identity/content/model/prompt/coverage/scope are immutable. The sole transition is
active→invalidated with time/reason; rebuild inserts a new row. Because messages are
immutable, ordinary appends after the covered prefix do not invalidate it.
A new summary is always inserted active at row version 1; it cannot be born
invalidated.

### 4.8 `conversation_pin`

| Column | Physical contract |
|---|---|
| `conversation_id`, `space_id`, `item_id` | UUID, composite primary key `(conversation_id,item_id)` |
| `revision_id` | nullable UUID |
| `created_by` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `created_at` | UTC_MS, DB default |

Composite FKs are exactly:

```text
(conversation_id,space_id) -> conversation(id,space_id) ON DELETE RESTRICT
(item_id,space_id)         -> knowledge_item(id,space_id) ON DELETE RESTRICT
(revision_id,item_id)      -> knowledge_revision(id,item_id) ON DELETE RESTRICT
```

The nullable revision FK is satisfied when `revision_id IS NULL`. Index
`ix_pin_item(item_id,conversation_id)` supports purge/reference checks.

## 5. Blobs, artifacts, extraction, and publication

### 5.1 `blob_object`

| Column | Physical contract |
|---|---|
| `sha256` | SHA256 primary key |
| `size_bytes` | `INTEGER NOT NULL CHECK(size_bytes>=0)` |
| `media_type` | required text, max 255 |
| `relative_path` | required text, max 500, unique; canonical `aa/bb/<sha256>` only |
| `verification_status` | `TEXT NOT NULL CHECK(... IN ('pending','verified','corrupt','missing'))` |
| `created_at`, `verified_at` | created UTC_MS; verified nullable UTC_MS |

`relative_path` must equal `substr(sha256,1,2)||'/'||substr(sha256,3,2)||'/'||sha256`.
Rows are never deleted by a route. A GC job may delete the physical blob and row
only after the DB proves zero canonical references, the 24-hour quarantine elapsed,
and no durable backup hold covers it.

### 5.2 `source_artifact`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `origin_type` | `TEXT NOT NULL CHECK(... IN ('upload','notion','chat','manual','legacy'))` |
| `origin_key` | nullable text, max 1000 |
| `origin_revision_key` | nullable text, max 500 |
| `raw_sha256` | SHA256 |
| `raw_size_bytes` | `INTEGER NOT NULL CHECK(raw_size_bytes>=0)` |
| `media_type` | required text, max 255 |
| `original_filename` | nullable text, max 500 |
| `body_retention_status` | `TEXT NOT NULL DEFAULT 'present' CHECK(... IN ('present','purged'))` |
| `body_retention_reason` | nullable `TEXT CHECK(... IN ('eligible_item_purge','orphan_ingestion_cleanup'))` |
| `body_purged_at` | nullable UTC_MS |
| `orphan_cleanup_eligible_at` | UTC_MS NOT NULL; exactly created time + 2592000000 |
| `publisher_user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `source_owner_snapshot_json`, `metadata_json` | JSON_OBJECT |
| `metadata_schema_version` | `INTEGER NOT NULL DEFAULT 1 CHECK(...>=1)` |
| `created_at` | UTC_MS, DB default |

Indexes: `(origin_type,origin_key,origin_revision_key,created_at DESC)` and
`(raw_sha256,created_at DESC)`, plus
`ix_artifact_retention(body_retention_status,orphan_cleanup_eligible_at,id)`.
Artifact rows are immutable and never deleted.
For manual/chat text without an original binary, the UTF-8 source bytes are still
stored through the body link below. Every new artifact is inserted as present;
the sole metadata update is guarded present-to-purged after all body links are
deleted. Present requires null purge reason/time; purged requires both, and is
terminal. Raw hash/size/type/name/origin/publisher metadata survive that transition.

### 5.2.1 `artifact_blob`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `artifact_id` | UUID FK `source_artifact(id) ON DELETE RESTRICT` |
| `content_sha256` | SHA256; retained metadata, deliberately not a live-blob FK |
| `size_bytes` | `INTEGER NOT NULL CHECK(size_bytes>=0)` |
| `role` | `TEXT NOT NULL CHECK(role IN ('raw','embedded_asset','attachment'))` |
| `logical_name` | required text, max 500 |
| `media_type` | required text, max 255 |
| `ordinal` | `INTEGER NOT NULL CHECK(ordinal>=0)` |
| `body_retention_status` | `TEXT NOT NULL DEFAULT 'present' CHECK(... IN ('present','purged'))` |
| `created_at` | UTC_MS, DB default |

Create unique `ux_artifact_blob_identity(artifact_id,role,ordinal)`, unique
`ux_artifact_blob_id_hash(id,content_sha256)`, partial unique
`ux_artifact_raw_role(artifact_id) WHERE role='raw'`, and
`ix_artifact_blob_content_hash(content_sha256,artifact_id)`. The raw role is exactly
one manifest row at ordinal 0 whose hash/size/media type equal the retained raw
metadata. Creation asserts that lower bound before commit. Rows are never deleted;
only body retention may change present-to-purged after its body link is gone.

### 5.2.2 `artifact_blob_body`

| Column | Physical contract |
|---|---|
| `artifact_blob_id` | UUID primary key FK `artifact_blob(id) ON DELETE RESTRICT` |
| `content_sha256` | SHA256 FK `blob_object(sha256) ON DELETE RESTRICT` |
| `created_at` | UTC_MS NOT NULL, DB default |

Composite FK `(artifact_blob_id,content_sha256)` references
`artifact_blob(id,content_sha256)`. Create
`ix_artifact_blob_body_hash(content_sha256,artifact_blob_id)`. UPDATE is always
rejected. Every present manifest has exactly one body link and every purged manifest
has none; creation and purge repositories assert this lower bound before commit.

### 5.3 `extraction_snapshot`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `artifact_id` | UUID FK `source_artifact(id) ON DELETE RESTRICT` |
| `status` | `TEXT NOT NULL CHECK(... IN ('ready','failed'))` |
| `extracted_sha256` | nullable SHA256; non-null exactly for ready |
| `normalized_content_sha256` | nullable SHA256; non-null exactly for ready |
| `extracted_size_bytes` | nullable INTEGER >=0; non-null exactly for ready |
| `normalizer_version` | nullable required text, max 80; non-null exactly for ready |
| `extractor_name`, `extractor_version` | required text, max 120 each |
| `warnings_json` | JSON_ARRAY |
| `body_retention_status` | `TEXT NOT NULL CHECK(... IN ('present','purged','none'))` |
| `created_at` | UTC_MS, DB default |

Create unique `ux_extraction_id_artifact(id,artifact_id)` and
`ix_extraction_artifact_time(artifact_id,created_at DESC,id)`. Rows are
immutable except guarded present-to-purged retention. Ready requires present or
purged retention and all hash/size/normalizer metadata; failed requires `none` and
all four fields null. Extracted Markdown is distinct from both raw artifact bytes
and editable knowledge revision bodies. `extracted_sha256` hashes the exact UTF-8
Markdown bytes. `normalized_content_sha256` hashes the deterministic normalized
bytes emitted by the allowlisted `normalizer_version`; it is candidate/top-K
evidence only and can never by itself cause automatic deduplication, provenance
attachment, merge, or replacement. Automatic exact handling requires verified
equality of exact stored UTF-8 body hashes/bytes and the applicable stable
source-identity rule. The extraction repository computes and verifies both before
insert, and integrity scans recompute them because v1 installs no SHA UDF.

### 5.3.1 `extraction_snapshot_body`

| Column | Physical contract |
|---|---|
| `snapshot_id`, `artifact_id` | UUID composite primary key and composite FK to `extraction_snapshot(id,artifact_id) ON DELETE RESTRICT` |
| `content_md` | nullable TEXT |
| `content_blob_hash` | nullable SHA256 FK `blob_object(sha256) ON DELETE RESTRICT` |
| `content_size_bytes` | `INTEGER NOT NULL CHECK(...>=0)` |
| `content_sha256` | SHA256 NOT NULL |
| `created_at` | UTC_MS NOT NULL, DB default |

Exactly one of inline content/blob hash is non-null. Insert requires a ready/present
snapshot and exact size/hash equality with its metadata. UPDATE is rejected. Every
ready/present snapshot has exactly one body and every ready/purged or failed snapshot
has none; repositories assert the lower bound before commit. Create partial index
`ix_extraction_body_blob(content_blob_hash,snapshot_id)` where non-null.

### 5.3.2 Artifact/extraction purge guard

Deleting an `artifact_blob_body` or `extraction_snapshot_body` row is allowed only
by either the eligible administrator item-purge path in this section or the
non-knowledge orphan path in section 5.3.3. For item purge, after the requested item
is tombstoned, the DELETE trigger rejects if the artifact has any of:

- an `item_source` whose item is not `purged_tombstone`, including the independent
  sibling of a dual publication;
- a lineage member whose item is not purged;
- a publication preview in queued, extracting, or ready state;
- an attached snapshot-mode conversation context binding;
- a change proposal in pending_review, approved, or apply_failed state that names
  the artifact/snapshot; or
- a non-terminal backup hold.

It also requires every item that references the artifact through item source or
lineage to have a latest eligible purge episode completed by an active admin. After
deleting all allowed body/link rows, the same transaction changes only corresponding
artifact, manifest, and extraction retention states to purged; triggers require
zero remaining body rows and the same blocker query. Metadata, hashes, origin,
extractor/normalizer versions, lineage, item source, publication outcomes, and audit
remain. If any blocker exists, artifact/extraction cleanup is skipped and recorded
as `retained_due_reference`; the requested item's tombstone/revision-body purge may
still commit. Thus purging the first dual-published sibling preserves shared source
bodies, while purging the last eligible dependent item can remove them. Physical
blob GC runs later and independently rechecks all live references and backup holds.

### 5.3.3 Non-knowledge orphan-ingestion cleanup

The fixed v1 grace is 30 days: exactly 2,592,000,000 ms after artifact creation,
recorded in `orphan_cleanup_eligible_at`. This is independent of and never shortens
the 90-day inactive-knowledge purge window. After DB time reaches that value, a
durable `artifact.orphan_cleanup` job may remove artifact/extraction body links only
when one zero-row blocker query proves all of the following:

- no `item_source` references the artifact and no lineage group names it as initial
  or base artifact;
- every preview for the artifact is terminal (`consumed`, `expired`, `cancelled`, or
  `failed`); queued, extracting, or ready blocks cleanup;
- no attached snapshot context binding, pending_review/approved/apply_failed change
  proposal, or non-terminal backup hold depends on the artifact/snapshot.

Historical publication-operation/target rows retain artifact/snapshot IDs and
hashes but are metadata, not body holds. The cleanup transaction deletes eligible
body links, changes manifest/extraction retention to purged, and changes the
artifact aggregate to purged with reason `orphan_ingestion_cleanup` and DB purge
time. It atomically writes audit/outbox and succeeds the durable job's stable effect
key `artifact-orphan:<artifact-id>`; crash/retry cannot repeat the effect. The job
uses the registered handler version and system authorization snapshot, never a
model decision. A blocker produces a no-effect retry/defer result, not partial
cleanup. Physical blob deletion remains the later 24-hour quarantined GC step.

### 5.4 `publication_preview`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `actor_user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `artifact_id` | UUID FK source artifact RESTRICT |
| `extraction_snapshot_id` | nullable UUID; composite FK with artifact to extraction snapshot |
| `durable_job_id` | nullable UUID unique FK `durable_job(id) ON DELETE RESTRICT` |
| `source_snapshot_sha256` | nullable SHA256 |
| `captured_part_id` | nullable UUID FK `org_unit(id) ON DELETE RESTRICT`; null captures intentional unassigned state |
| `captured_scope_epoch` | integer >=1 |
| `available_targets_json` | JSON_ARRAY |
| `rule_version` | required text, max 120 |
| `deterministic_findings_json` | nullable JSON_ARRAY |
| `ready_result_json` | nullable JSON_OBJECT |
| `result_schema_version` | `INTEGER NOT NULL DEFAULT 1 CHECK(result_schema_version=1)` |
| `status` | `TEXT NOT NULL CHECK(... IN ('queued','extracting','ready','consumed','expired','cancelled','failed'))` |
| `error_json` | nullable JSON_OBJECT |
| `expires_at`, `created_at`, `updated_at`, `completed_at` | UTC_MS / UTC_MS / UTC_MS / nullable UTC_MS |
| `row_version` | versioned aggregate |

The extraction table therefore also has `UNIQUE(id,artifact_id)`. Add unique index
`ux_preview_durable_job(durable_job_id)` where non-null and index
`ix_preview_actor_status_expiry(actor_user_id,status,expires_at,id)`. Preview identity,
actor, artifact, durable job, captured part/epoch, expiry, available targets, rule
version, result schema version, and every non-null ready payload field are immutable.

The state/null shape is exact:

- queued or extracting: durable job non-null; snapshot, source hash, findings,
  result, error, and completed time all null;
- ready: snapshot/source hash/findings/result/completed time non-null, error null,
  and present artifact/extraction bodies;
- consumed or expired: retains the complete immutable ready shape; only status,
  updated time, and row version changed from ready;
- failed: durable job/error/completed time non-null and every ready payload field
  null;
- cancelled from queued/extracting: durable job/completed time non-null and ready
  payload/error null; cancelled from ready retains the full ready payload and its
  original completed time, with error null.

The extracting→ready transition repeats the ready snapshot/body/hash check and
requires the linked `publication_preview.extract` v1 job to be running with the
same actor, captured membership epoch/part, preview ID, artifact ID, and rule
version. A terminal-shaped or unverified ready payload cannot be introduced by an
UPDATE after a valid queued INSERT.

For asynchronous rows, the job type is `publication_preview.extract`; its
actor/scope/artifact/rule-version input and registered handler version must match.
A synchronous preview is inserted directly as ready with `durable_job_id` null,
the full ready payload present, and `completed_at=created_at`. `ready_result_json`
is the immutable schema-v1 response containing artifact/snapshot hashes, available
targets, deterministic decision inputs, and the same
`deterministic_findings_json`; application canonicalization/hash checks reject any
disagreement before INSERT/transition.

Legal transitions are queued→extracting/cancelled/failed,
extracting→ready/cancelled/failed, and ready→consumed/expired/cancelled; terminal
states do not transition. Publication changes ready→consumed inside its canonical
commit. Commit requires DB time before expiry, exact source hash, active actor, and
unchanged current part/epoch. Expiry uses DB time and compare-and-swap; it is not a
substitute for the commit-time expiry check.

The creation trigger requires `captured_part_id` to equal the actor's current part,
including null. A null captured part removes `part` and `both` from the
server-derived available targets; it never prevents personal preview/publication.

### 5.5 `lineage_group`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `initial_artifact_id` | UUID NOT NULL FK `source_artifact(id) ON DELETE RESTRICT` |
| `base_artifact_id` | UUID NOT NULL FK `source_artifact(id) ON DELETE RESTRICT` |
| `base_extraction_snapshot_id` | UUID NOT NULL; composite FK with base artifact to `extraction_snapshot(id,artifact_id) ON DELETE RESTRICT` |
| `base_content_sha256` | SHA256 NOT NULL |
| `sync_policy` | `TEXT NOT NULL DEFAULT 'none' CHECK(sync_policy='none')` |
| `created_by` | USER_ID NOT NULL FK `app_user(id) ON DELETE RESTRICT` |
| `created_at` | UTC_MS NOT NULL, DB default |

All columns are immutable and DELETE is rejected. An insert trigger requires the
base extraction snapshot to be ready and its `normalized_content_sha256` to equal
`base_content_sha256`; the publication-operation trigger requires the initial
operation to use that artifact/snapshot pair. A lineage group is provenance only and is never queried as a
synchronization instruction.

### 5.5.1 `lineage_member`

| Column | Physical contract |
|---|---|
| `lineage_group_id`, `item_id` | UUID NOT NULL composite primary key; FKs to lineage group/item RESTRICT |
| `initial_revision_id` | UUID NOT NULL; composite FK `(initial_revision_id,item_id)` to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `derived_from_item_id`, `derived_from_revision_id` | nullable UUID pair; composite FK `(derived_from_revision_id,derived_from_item_id)` to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `member_role` | `TEXT NOT NULL CHECK(... IN ('origin','personal_copy','part_copy','promoted_copy','team_copy','responsibility_copy'))` |
| `created_by` | USER_ID NOT NULL FK `app_user(id) ON DELETE RESTRICT` |
| `created_at` | UTC_MS NOT NULL, DB default |

The migration creates `UNIQUE(item_id)` as `ux_lineage_member_item` and
`ix_lineage_member_derived(derived_from_item_id,derived_from_revision_id,lineage_group_id)`.
The derived pair is either wholly null or wholly non-null. It is null exactly for
`member_role='origin'`; every other role requires it. The derived item differs from
the member item. Insert triggers require `knowledge_item.lineage_group_id` to equal
the member group and require a non-null derived item to be an already-existing
member of the same group. All columns are immutable and DELETE is rejected.

For initial `both`, the personal member is `origin`; the independent part member is
`part_copy` derived from the personal initial revision. A later personal-to-part
promotion uses `promoted_copy` with the fixed personal source revision. The
`initial_revision_id` and derived pair are the permanent fork base used by three-way
proposals. They never advance after edits and never authorize synchronization.

### 5.6 `operation_dedup`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `actor_user_id` | USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `operation_type` | required text, max 80 |
| `operation_id` | UUID supplied by caller |
| `request_sha256` | SHA256 |
| `status` | `TEXT NOT NULL CHECK(... IN ('in_progress','committed','failed_final'))` |
| `http_status` | nullable integer 100..599 |
| `result_json`, `error_json` | nullable JSON_OBJECT |
| `created_at`, `completed_at`, `last_seen_at` | UTC_MS; completion nullable |

Unique `(actor_user_id,operation_type,operation_id)` and cleanup index
`(status,completed_at,id)`. Checks require committed/result, failed_final/error,
and in_progress/neither. Result and error are mutually exclusive.

Legal transitions are in_progress→committed/failed_final; committed and
failed_final are terminal. `last_seen_at` may advance without changing status and
is the only update permitted on a terminal row.

The request hash is canonical JSON plus referenced snapshot hashes. Lease raw
tokens are never included; their HMAC digest is included. On uniqueness conflict,
the service compares request hash: equal+terminal returns the stored status/body,
equal+in-progress returns a retryable operation-in-progress response, and unequal
returns `IDEMPOTENCY_CONFLICT`. The domain mutation, terminal dedup row, audit, and
outbox commit together. Transient pre-commit failures roll back the in-progress row;
post-validation non-retryable failures may commit `failed_final`.

### 5.7 `publication_operation`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `dedup_id` | UUID unique FK `operation_dedup(id) ON DELETE RESTRICT` |
| `actor_user_id` | USER_ID FK app user RESTRICT |
| `operation_id` | caller UUID |
| `request_sha256` | SHA256 |
| `artifact_id`, `extraction_snapshot_id` | UUID FKs RESTRICT |
| `target_mode` | `TEXT NOT NULL CHECK(... IN ('personal','part','both'))` |
| `lineage_group_id` | nullable UUID FK lineage group RESTRICT; non-null exactly for `both` |
| `created_at` | UTC_MS |

Unique `ux_publication_actor_operation(actor_user_id,operation_id)` and
`ux_publication_dedup(dedup_id)`, plus the composite FK from snapshot/artifact as in
preview. Rows are immutable. Trigger verifies dedup actor, operation ID, operation
type `publication`, and request hash match. The service changes dedup to committed
only after target/audit/outbox rows exist, then verifies the terminal postcondition
immediately before commit.

### 5.8 `publication_target`

| Column | Physical contract |
|---|---|
| `publication_operation_id`, `target_space_id` | UUID composite primary key; FKs to operation/space RESTRICT |
| `outcome` | `TEXT NOT NULL CHECK(... IN ('created','provenance_added','update_proposed','no_op'))` |
| `item_id` | UUID NOT NULL; composite FK `(item_id,target_space_id)` to item |
| `resolved_revision_id` | UUID NOT NULL; composite FK to the same item |
| `created_revision_id` | nullable UUID; composite FK to same item |
| `curation_finding_id` | nullable UUID FK finding RESTRICT |
| `response_json` | JSON_OBJECT |
| `created_at` | UTC_MS |

Checks require:

- `created`: `created_revision_id=resolved_revision_id`, finding null;
- `provenance_added` or `no_op`: created revision and finding null;
- `update_proposed`: created revision null and finding non-null.

Add `ix_publication_target_item(item_id,publication_operation_id)` and partial index
`ix_publication_target_finding` on `curation_finding_id` where that column is not
null. A trigger verifies an update-proposal finding belongs to the target space,
source item, and resolved revision.

For an initial new `both`, the two target rows must have different item IDs and
different created revision IDs; the service asserts this before commit. A repeat of
the same operation returns these same rows. A later distinct exact-repeat operation
may resolve existing independent items without creating revisions. There is never
a sync pointer.

## 6. Knowledge, revisions, provenance, and graph

### 6.1 `knowledge_item`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `space_id` | UUID FK knowledge space RESTRICT |
| `lineage_group_id` | nullable UUID FK lineage group RESTRICT |
| `lifecycle_status` | `TEXT NOT NULL CHECK(... IN ('active','inactive','merged','purged_tombstone'))` |
| `curation_status` | `TEXT NOT NULL CHECK(... IN ('published','provisional','quarantined'))` |
| `current_revision_id` | nullable UUID |
| `last_curation_transition_id` | nullable UUID |
| `latest_deactivation_episode_id` | nullable UUID |
| `redirect_item_id` | nullable UUID |
| `created_by`, `updated_by` | USER_ID FKs app user RESTRICT |
| `created_at`, `updated_at` | UTC_MS |
| `row_version` | versioned aggregate |

Unique keys `(id,space_id)` and indexes:

- `ix_item_visible(space_id,lifecycle_status,curation_status,updated_at DESC,id)`;
- `ix_item_lineage(lineage_group_id,space_id,id)`;
- `ix_item_redirect(redirect_item_id)`;
- `ix_item_deactivation(latest_deactivation_episode_id)`;
- `ix_item_curation_transition(last_curation_transition_id,id)`.

Deferred composite FKs are:

```text
(current_revision_id,id)            -> knowledge_revision(id,item_id)
                                       DEFERRABLE INITIALLY DEFERRED
(last_curation_transition_id,id)    -> item_curation_transition(id,item_id)
                                       DEFERRABLE INITIALLY DEFERRED
(latest_deactivation_episode_id,id) -> item_deactivation_episode(id,item_id)
                                       DEFERRABLE INITIALLY DEFERRED
(redirect_item_id,space_id)         -> knowledge_item(id,space_id)
                                       DEFERRABLE INITIALLY DEFERRED
```

Checks require current revision null exactly for `purged_tombstone`; merged requires
a non-self redirect; active/inactive require redirect null. Purged tombstones may
retain a redirect. Identity, space, creator, and creation time are immutable.

Lifecycle trigger permits only active→inactive, inactive→active,
active→merged, and inactive→purged_tombstone. Purged is terminal. Phase 1 has no
merged reopen transition. For inactive-to-purged it additionally requires the
latest unrecovered episode's purge actor/time to be present, the actor to be an
active administrator, and DB time plus purge time to be at/after eligibility.
Reference/blocker checks and the revision-body lower-bound postcondition remain
mandatory service assertions in the same transaction.

The curation/current-revision update trigger rejects every such change without a new
`last_curation_transition_id` whose event exactly names OLD/NEW status, OLD/NEW
revision, and NEW item row version. Legal curation transitions are provisional to
published/quarantined through a finding decision, and
published/quarantined/provisional to provisional through a revision rescan. No
published-to-quarantined, quarantined-to-published, or direct status patch exists.

The sole exception is the lifecycle-only `inactive -> purged_tombstone` update.
That update must set `NEW.current_revision_id` to null, leave
`NEW.curation_status=OLD.curation_status`, and leave
`NEW.last_curation_transition_id` unchanged. It never inserts or points to an
`item_curation_transition`; purge is deliberately absent from that table's
`transition_kind` domain. The lifecycle trigger plus the latest purge episode and
same-transaction administrator audit/postcondition authorize this exception and
reject the same pointer shape for every other update. New items require a null
transition pointer; any other update that changes neither current revision nor
curation status must retain the old pointer unchanged.

### 6.1.1 `item_curation_transition`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `item_id`, `space_id` | UUID NOT NULL; composite FK `(item_id,space_id)` to `knowledge_item(id,space_id) ON DELETE RESTRICT` |
| `transition_kind` | `TEXT NOT NULL CHECK(... IN ('finding_decision','revision_rescan'))` |
| `from_status`, `to_status` | `TEXT NOT NULL CHECK(... IN ('published','provisional','quarantined'))` |
| `base_revision_id`, `result_revision_id` | UUID NOT NULL; each composite FK with item to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `finding_id` | nullable UUID FK `curation_finding(id) ON DELETE RESTRICT` |
| `rescan_job_id` | nullable UUID unique FK `durable_job(id) ON DELETE RESTRICT` |
| `decision_job_id` | nullable UUID unique FK `durable_job(id) ON DELETE RESTRICT` |
| `dedup_id` | nullable UUID unique FK `operation_dedup(id) ON DELETE RESTRICT` |
| `operation_id` | nullable caller UUID |
| `actor_kind` | `TEXT NOT NULL CHECK(... IN ('user','system'))` |
| `actor_user_id` | nullable USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `result_item_row_version` | `INTEGER NOT NULL CHECK(...>=2)` |
| `reason` | required text, max 2000 |
| `created_at` | UTC_MS NOT NULL, DB default |

Create unique indexes `ux_curation_transition_id_item(id,item_id)`,
`ux_curation_transition_dedup(dedup_id)`,
`ux_curation_transition_job(rescan_job_id)` where non-null, and
`ux_curation_transition_decision_job(decision_job_id)` where non-null,
`ux_curation_transition_item_version(item_id,result_item_row_version)`, plus
`ix_curation_transition_item(item_id,created_at DESC,id)`. Rows are append-only.
The insert trigger proves item space/current base revision/from status and
`result_item_row_version=item.row_version+1`. User rows require actor, dedup, and
operation ID; the dedup actor/request/type `item_curation` must match and the actor
must have target-space editor authority. System rows require null actor/dedup/
operation ID and non-null decision job with the system authorization shape.

Finding-decision shape is exactly provisional to published or quarantined,
base=result revision, finding non-null, and rescan job null. Human rows have no
decision job and require an approved, current, same-space/source/current-revision,
null-target finding: `curation_release/publish` for published or
`curation_quarantine/quarantine` for quarantined. Automatic rows require a running
`curation.rescan` decision job for that item/revision and a pending same-revision
finding: only `curation_clear/info/T1/automatic/none/none/publish` may publish, and only
the separately enumerated lint blocker/error T0 automatic tuples may quarantine.
The transition's `decision_job_id` must equal that finding's
`applied_job_id`; the finding and job application proof are committed in the same
transaction.
In the same transaction the item points to this event, finding becomes applied,
user dedup becomes committed or system job effect commits once, FTS is inserted
only for published, and audit/outbox/generation are written.

Revision-rescan shape is published, quarantined, or provisional to provisional;
finding and decision job are null, rescan job is non-null, actor kind is user,
base/result revisions differ, and result is
the newly inserted item revision. The job must be queued type `curation.rescan`,
match actor/space/item/result revision and handler version, and carry the current
authorization snapshot. Revision/body, item pointer/status/event, job, FTS deletion,
audit/outbox/generation, and dedup commit atomically. The item update trigger accepts
curation-status/current-revision changes only after this event exists; the service
then verifies the full event/item/finding-or-job/audit/outbox/FTS postcondition
immediately before commit.

### 6.2 `knowledge_revision`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `item_id` | UUID FK knowledge item RESTRICT |
| `revision_no` | integer >=1 |
| `parent_revision_id`, `base_revision_id` | nullable UUID |
| `title` | required text, max 500 |
| `raw_content_sha256`, `normalized_content_sha256` | SHA256 |
| `content_size_bytes` | `INTEGER NOT NULL CHECK(content_size_bytes>=0)` |
| `normalizer_version` | required text, max 80 |
| `metadata_json` | JSON_OBJECT |
| `change_kind` | `TEXT NOT NULL CHECK(... IN ('initial','edit','merge','import','approved_update'))` |
| `change_reason` | required text, max 2000 |
| `created_by` | USER_ID FK app user RESTRICT |
| `created_at` | UTC_MS |

Unique `(id,item_id)`, unique `(item_id,revision_no)`, and index
`(item_id,created_at DESC,id)`. Composite FKs `(parent_revision_id,item_id)` and
`(base_revision_id,item_id)` reference `(id,item_id)` and are RESTRICT. A check
rejects self parent/base. DELETE is always rejected. An UPDATE trigger rejects every
UPDATE without exception, so revision metadata is byte-for-byte immutable for its
entire lifetime. Revision numbering is allocated inside the item write transaction
as current max+1 under `BEGIN IMMEDIATE`.

### 6.3 `knowledge_revision_body`

| Column | Physical contract |
|---|---|
| `revision_id` | UUID primary key |
| `item_id` | UUID NOT NULL; composite FK `(revision_id,item_id)` to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `content_md` | nullable TEXT |
| `content_blob_hash` | nullable SHA256 FK `blob_object(sha256) ON DELETE RESTRICT` |
| `content_size_bytes` | `INTEGER NOT NULL CHECK(content_size_bytes>=0)` |
| `content_sha256` | SHA256 NOT NULL |
| `created_at` | UTC_MS NOT NULL, DB default |

`CHECK((content_md IS NOT NULL) <> (content_blob_hash IS NOT NULL))` requires exactly
one inline body or immutable blob reference. Inserts require a non-purged parent
item, and application insertion plus integrity scans verify body size/hash and
equality to the immutable revision metadata. Create
`ix_revision_body_blob(content_blob_hash,revision_id)` where the blob hash is
non-null. UPDATE always aborts.

The `trg_knowledge_revision_body_purge_delete` trigger permits DELETE only when all of
these facts already exist in the same transaction:

- the parent item is `purged_tombstone`, its current revision is null, and this is
  its latest deactivation episode;
- that episode is the latest unrecovered episode (`restored_by/restored_at` null),
  has non-null `purged_by/purged_at`,
  has `purged_at>=purge_eligible_at`, and `purged_at` is no later than DB UTC-ms now;
- `purged_by` references an active `app_user` whose `system_role='admin'`.

Body retention is derived, not stored on `knowledge_revision`: a body row means
present; absence is legal only when the parent item is a valid purged tombstone.
The purge transaction asserts zero body rows for every item revision. Every
non-purged item has exactly one body row per revision; revision creation asserts
that lower-bound postcondition before commit. No purge ever updates revision
metadata.

This deletion is part of the administrator's canonical purge transaction, not a
background body-GC operation. It preserves revision identity, hashes, actor, time,
lineage, provenance, audit, and tombstone metadata. A later durable GC job may
delete only blob objects that became unreferenced after the body row disappeared.

### 6.4 `item_source`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `item_id`, `revision_id` | UUID, composite FK to knowledge revision/item RESTRICT |
| `artifact_id`, `extraction_snapshot_id` | UUID, composite snapshot/artifact FK RESTRICT |
| `role` | `TEXT NOT NULL CHECK(... IN ('initial','evidence','update','migrated'))` |
| `created_by` | USER_ID FK app user RESTRICT |
| `created_at` | UTC_MS |

Unique `(item_id,revision_id,artifact_id,extraction_snapshot_id,role)`, indexes on
`(artifact_id,item_id)` and `(item_id,revision_id)`. Rows are immutable/deletion
rejected. `provenance_added` inserts a new evidence row against the resolved
revision; `no_op` still has a target result and audit but does not duplicate an
already identical item-source key.

### 6.5 `item_relation`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `source_item_id`, `source_revision_id` | UUID composite FK revision/item RESTRICT |
| `target_item_id`, `target_revision_id` | UUID composite FK revision/item RESTRICT |
| `relation_type` | `TEXT NOT NULL CHECK(... IN ('forked_from','promoted_from','exact_duplicate_of','merged_into','supersedes'))` |
| `relation_status` | `TEXT NOT NULL CHECK(... IN ('active','inactive'))` |
| `evidence_json` | JSON_OBJECT |
| `created_by`, `updated_by` | USER_ID FKs app user RESTRICT |
| `created_at`, `updated_at` | UTC_MS |
| `row_version` | versioned aggregate |

Check rejects the same source and target item. Cross-space relations are permitted.
Unique active identity index on `(source_item_id,target_item_id,relation_type)` where
`relation_status='active'`; reverse lookup index on
`(target_item_id,relation_status,relation_type)`. Identity, endpoints, type,
evidence, creator, and creation time are
immutable; the only update is active→inactive with row-version increment, updater,
and DB time. These are provenance/revision relations, never same-space semantic
retrieval edges.

### 6.6 `item_deactivation_episode`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `item_id` | UUID FK knowledge item RESTRICT |
| `episode_no` | integer >=1 |
| `deactivated_by` | USER_ID FK app user RESTRICT |
| `deactivated_at` | UTC_MS |
| `reason` | required text, max 2000 |
| `purge_eligible_at` | UTC_MS; exactly deactivated time + 7776000000 |
| `restored_by`, `restored_at` | nullable USER_ID/UTC_MS, both null or both non-null |
| `purged_by`, `purged_at` | nullable USER_ID/UTC_MS, both null or both non-null |

Unique `(id,item_id)`, unique `(item_id,episode_no)`, unique partial open episode on
`item_id WHERE restored_at IS NULL AND purged_at IS NULL`, plus eligibility index
`(purge_eligible_at,item_id) WHERE restored_at IS NULL AND purged_at IS NULL`.
Checks make restored and purged mutually exclusive and require event times not
before deactivation. Updates may only fill one restore pair or one purge pair once;
delete is rejected. Every episode is inserted open with all restore/purge fields
null; it cannot be born restored or purged.

### 6.7 `knowledge_edge`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `space_id` | UUID FK knowledge space RESTRICT |
| `source_item_id`, `target_item_id` | UUID; each composite-FK with space to knowledge item |
| `edge_type` | `TEXT NOT NULL CHECK(... IN ('related','supports','contradicts','depends_on','extends','example_of'))` |
| `edge_status` | `TEXT NOT NULL CHECK(... IN ('active','inactive'))` |
| `origin` | `TEXT NOT NULL CHECK(... IN ('manual','semantic_approved'))` |
| `source_evidence_revision_id`, `target_evidence_revision_id` | nullable UUID, composite FKs to corresponding item |
| `approval_finding_id` | nullable UUID FK `curation_finding(id) ON DELETE RESTRICT` |
| `evidence_json` | JSON_OBJECT |
| `needs_revalidation` | BOOL, default 0 |
| `created_by`, `updated_by` | USER_ID FKs app user RESTRICT |
| `created_at`, `updated_at` | UTC_MS |
| `row_version` | versioned aggregate |

Checks reject self-edge and require the two evidence revisions both null or both
non-null. Manual origin requires null approval finding. Semantic-approved origin
requires non-null evidence revisions and approval finding. Unique partial active identity index covers
`(space_id,source_item_id,target_item_id,edge_type)` where `edge_status='active'`;
reverse and revalidation indexes cover `(space_id,target_item_id,edge_status)` and
`(space_id,needs_revalidation,edge_status,id)`. Unique partial index
`ux_edge_approval_finding(approval_finding_id)` where non-null prevents one approval
from committing multiple edges.

The semantic-approved insert trigger requires the finding to be
`semantic_edge/T2/human/space_editor/relation`, status applied, and to have the same
space, source/target items, and evidence revisions. Thus semantic knowledge-edge
automatic commit count is structurally zero. Deterministic fork/promotion/exact
provenance is represented only by immutable/versioned `item_relation`, never here.

The two endpoint composite FKs guarantee same space. Deactivate/restore/purge item
services do not update this table. Effective visibility is always edge active plus
both endpoints active/published; stored edge status survives item lifecycle changes.
Identity, space, endpoints, type, origin, evidence, creator, and creation time are
immutable. An explicit reviewed edge command may change active↔inactive, and an
edit may set `needs_revalidation` from 0→1; approval against current evidence may
set it from 1→0. Every such update increments row version and writes audit/outbox.

## 7. Risk-tiered curation and merge

### 7.1 Fixed v1 risk policy

| Tier | Allowed automatic effect | Human requirement |
|---|---|---|
| `T0` | validation, exact request/source no-op, structural lint | none |
| `T1` | deterministic provenance/fork/promotion evidence or deterministic no-blocker release | none |
| `T2` | no automatic domain mutation | authenticated target-space editor reviews semantic edge, conflict, update, replace, merge, or curation decision |
| `T3` | none | explicit capability-checked human command; admin for purge |

Severity (`blocker|error|warning|info`) is impact, not authority. A model may produce
evidence/proposals but cannot lower a tier. Semantic relations, conflict conclusions,
replacement, and merge are always T2. Deactivate is T3; purge is T3/admin. No T2 or
T3 finding can use `decision_mode='automatic'`.

### 7.2 `curation_finding`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `space_id` | UUID FK knowledge space RESTRICT |
| `finding_kind` | `TEXT NOT NULL CHECK(... IN ('lint','exact_repeat','curation_clear','weak_complementary','near_duplicate','update','potential_conflict','semantic_edge','replace','merge','curation_release','curation_quarantine','deactivate','purge'))` |
| `risk_tier` | `TEXT NOT NULL CHECK(... IN ('T0','T1','T2','T3'))` |
| `severity` | `TEXT NOT NULL CHECK(... IN ('blocker','error','warning','info'))` |
| `status` | `TEXT NOT NULL CHECK(... IN ('pending','approved','rejected','ignored','obsolete','applied','apply_failed'))` |
| `decision_mode` | `TEXT NOT NULL CHECK(... IN ('automatic','human'))` |
| `required_reviewer` | `TEXT NOT NULL CHECK(... IN ('none','space_editor','part_leader','admin'))` |
| `visibility_effect` | `TEXT NOT NULL CHECK(... IN ('none','badge','agent_hidden','publication_blocked'))` |
| `proposed_action` | `TEXT NOT NULL CHECK(... IN ('none','provenance','review_duplicate','update','review_conflict','relation','merge','replace','publish','quarantine','deactivate','purge'))` |
| `source_item_id`, `source_revision_id` | UUID composite FK revision/item RESTRICT |
| `target_item_id`, `target_revision_id` | nullable UUID pair, composite FK revision/item RESTRICT |
| `detector_name`, `detector_version`, `rule_version` | required text, max 120 each |
| `model_id`, `prompt_version` | nullable text, max 200 |
| `confidence` | nullable REAL, check 0..1 |
| `evidence_json`, `proposed_action_json` | JSON_OBJECT |
| `reviewed_by`, `reviewed_at`, `review_reason` | nullable user/time/text triple |
| `applied_operation_id` | nullable UUID supplied by the applying client |
| `applied_dedup_id` | nullable UUID unique FK `operation_dedup(id) ON DELETE RESTRICT` |
| `applied_job_id` | nullable UUID FK `durable_job(id) ON DELETE RESTRICT` |
| `applied_by`, `applied_at` | nullable user/time pair |
| `last_application_error_json` | nullable JSON_OBJECT |
| `created_actor_kind` | `TEXT NOT NULL CHECK(... IN ('user','system'))` |
| `created_by` | nullable USER_ID FK `app_user(id) ON DELETE RESTRICT` |
| `created_at`, `updated_at` | UTC_MS |
| `row_version` | versioned aggregate |

Checks/triggers enforce:

- target item/revision are both null or both non-null;
- source and target items are in `space_id` and are different when target exists;

The insert/update trigger's CASE allowlist is exactly this table. No other
kind/severity/tier/mode/reviewer/visibility/action tuple is schema-valid:

| Finding kind | Severity | Tier | Mode | Reviewer | Visibility | Action |
|---|---|---|---|---|---|---|
| `lint` | `blocker` | `T0` | `automatic` | `none` | `publication_blocked` | `quarantine` |
| `lint` | `error` | `T0` | `automatic` | `none` | `publication_blocked` | `quarantine` |
| `lint` | `warning` | `T0` | `automatic` | `none` | `badge` | `none` |
| `lint` | `info` | `T0` | `automatic` | `none` | `none` | `none` |
| `exact_repeat` | `info` | `T0` | `automatic` | `none` | `none` | `none` |
| `exact_repeat` | `info` | `T1` | `automatic` | `none` | `none` | `provenance` |
| `curation_clear` | `info` | `T1` | `automatic` | `none` | `none` | `publish` |
| `weak_complementary` | `warning` | `T2` | `human` | `space_editor` | `badge` | `none` |
| `near_duplicate` | `warning` | `T2` | `human` | `space_editor` | `agent_hidden` | `review_duplicate` |
| `update` | `info` | `T2` | `human` | `space_editor` | `agent_hidden` | `update` |
| `potential_conflict` | `error` | `T2` | `human` | `space_editor` | `agent_hidden` | `review_conflict` |
| `semantic_edge` | `info` | `T2` | `human` | `space_editor` | `none` | `relation` |
| `replace` | `warning` | `T2` | `human` | `space_editor` | `agent_hidden` | `replace` |
| `merge` | `warning` | `T2` | `human` | `space_editor` | `agent_hidden` | `merge` |
| `curation_release` | `info` | `T2` | `human` | `space_editor` | `agent_hidden` | `publish` |
| `curation_quarantine` | `error` | `T2` | `human` | `space_editor` | `agent_hidden` | `quarantine` |
| `deactivate` | `warning` | `T3` | `human` | `space_editor` | `none` | `deactivate` |
| `purge` | `warning` | `T3` | `human` | `admin` | `none` | `purge` |

Additional checks/triggers enforce:

- created actor kind user requires creator ID and system requires null; detector
  findings emitted by workers, including curation_clear, use the system shape;
- `part_leader` is a reserved, schema-valid reviewer class for a versioned future
  rule, but no v1 tuple emits it; adding such a tuple requires a schema migration;
- `space_editor` means the active personal owner for a personal space or an active
  current member of the owning part for a part space; it is resolved from canonical
  ownership/membership, never accepted as a client role claim;
- exact_repeat T0/T1 requires verified equality of the exact UTF-8 content body hash
  and bytes (plus stable source identity where relevant); normalized hash equality
  alone must instead emit a non-automatic candidate finding;
- weak_complementary never changes item curation state, hides an item, or creates an
  edge/merge/replacement. Its pending, approved, or applied human T2 row is a
  non-hiding badge; rejected/ignored/obsolete rows remain history without an active
  badge. An already published item remains published while this review is pending;
  a provisional item can be published only by the separate deterministic
  curation_clear transition. Strong
  near_duplicate remains provisional plus agent-hidden pending human review;
- curation_clear is emitted only by a registered rescan handler after every required
  blocker detector for that exact revision/rule set completed with no blocker; its
  durable job/result hashes are verified by the transition command;
- no T2/T3 row is automatic. T3 findings can be reviewed but never execute a
  domain mutation: the separately authorized deactivate/purge command does that;
- approved/rejected/ignored and every `decision_mode='human'`
  applied/application-failed row require the complete review triple and a reviewer
  whose current DB capability satisfies `required_reviewer` inside the transition
  transaction. A caller-shaped T0/T1 automatic result from synchronous publication
  is not a human decision and does not fabricate a review triple;
- an application proof has exactly one of two shapes. The caller shape requires
  non-null `applied_operation_id`, `applied_dedup_id`, `applied_by`, and
  `applied_at`, with `applied_job_id` null. The system-job shape requires non-null
  `applied_job_id` and `applied_at`, with `applied_operation_id`,
  `applied_dedup_id`, and `applied_by` all null. A mixed or partial shape is
  invalid;
- `applied` requires exactly one complete application proof, no error, and either
  prior approval or an allowed T0/T1 automatic transition. The system-job shape is
  valid only for a T0/T1 automatic tuple and a matching system job. A synchronous
  user publication may apply an allowed T0/T1 automatic finding with the caller
  shape; using that shape does not turn the deterministic policy decision into a
  T2 human review. `apply_failed` requires the caller shape plus error JSON and a
  prior human approval; automatic job failures remain job retry/failure state and
  do not create a system-shaped `apply_failed` finding;
- legal transitions are pending to approved/rejected/ignored/obsolete or applied
  (T0/T1 automatic only), approved to applied/apply_failed/obsolete, and
  apply_failed to approved/rejected/obsolete. Re-approval clears the prior
  application tuple/error. Rejected, ignored, obsolete, and applied are terminal;
- detector/model/prompt/evidence/source/target/risk fields are immutable after insert.

The migration implements the fixed tuples above in insert/update triggers using an
explicit CASE expression; it does not trust API-supplied tier, reviewer, visibility,
or action values. `rule_version` is mandatory even when no model is involved.
Application operation/job fields are immutable except for the exact state
transitions above. A trigger proves a caller-shaped `applied_dedup_id` has the same
actor and operation ID and an operation type matching the domain command. For a
system-job shape it proves that `applied_job_id` is the registered T0/T1 automatic
job for the same space/source revision and that the job's system authorization,
handler version, input hash, and stable effect match. If an automatic
`item_curation_transition` exists for the finding, its `decision_job_id` is exactly
the same `applied_job_id`. Findings without application state require every
application field to be null; re-approval clears the prior caller tuple/error.

Indexes:

- review queue `(space_id,status,risk_tier,severity,created_at,id)`;
- source current `(source_item_id,source_revision_id,status)`;
- target current `(target_item_id,target_revision_id,status)`;
- ignored-pair lookup
  `(space_id,finding_kind,source_item_id,target_item_id,detector_version,status)`;
- unique `ux_finding_apply_dedup(applied_dedup_id)` where the dedup ID is non-null;
- `ix_finding_apply_job(applied_job_id)`.

When either compared current revision changes, the edit service marks pending or
approved findings obsolete in the same edit transaction and emits the appropriate
outbox event. Ignored-pair suppression keys include both revision hashes and the
detector/rule/model/prompt versions, so a change ends suppression.

### 7.3 `change_proposal`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `finding_id` | UUID NOT NULL unique FK `curation_finding(id) ON DELETE RESTRICT` |
| `finding_input_row_version` | `INTEGER NOT NULL CHECK(...>=1)`; fixed finding version used to create the proposal |
| `proposal_kind` | `TEXT NOT NULL CHECK(... IN ('stable_source_update','personal_to_part'))` |
| `proposal_series_id` | UUID NOT NULL; immutable logical rebase-chain identity |
| `proposal_version` | `INTEGER NOT NULL CHECK(proposal_version>=1)` |
| `supersedes_proposal_id` | nullable UUID unique FK `change_proposal(id) ON DELETE RESTRICT` |
| `lineage_group_id` | nullable UUID FK `lineage_group(id) ON DELETE RESTRICT` |
| `base_item_id`, `base_revision_id` | UUID NOT NULL composite FK to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `source_item_id`, `source_revision_id` | nullable UUID pair; composite FK to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `target_item_id`, `target_revision_id` | UUID NOT NULL composite FK to `knowledge_revision(id,item_id) ON DELETE RESTRICT` |
| `source_artifact_id`, `extraction_snapshot_id` | nullable UUID pair; composite FK `(extraction_snapshot_id,source_artifact_id)` to `extraction_snapshot(id,artifact_id) ON DELETE RESTRICT` |
| `proposed_content_md` | nullable TEXT |
| `proposed_content_blob_hash` | nullable SHA256 FK `blob_object(sha256) ON DELETE RESTRICT` |
| `proposed_content_size_bytes` | `INTEGER NOT NULL CHECK(...>=0)` |
| `proposed_content_sha256`, `preview_sha256` | SHA256 NOT NULL |
| `state` | `TEXT NOT NULL CHECK(... IN ('pending_review','approved','obsolete','applied','apply_failed'))` |
| `applied_operation_id` | nullable UUID |
| `applied_dedup_id` | nullable UUID unique FK `operation_dedup(id) ON DELETE RESTRICT` |
| `last_application_error_json` | nullable JSON_OBJECT |
| `created_by`, `updated_by` | USER_ID NOT NULL FKs `app_user(id) ON DELETE RESTRICT` |
| `created_at`, `updated_at` | UTC_MS NOT NULL, DB defaults |
| `row_version` | versioned aggregate |

`CHECK((proposed_content_md IS NOT NULL) <> (proposed_content_blob_hash IS NOT NULL))`
fixes exactly one proposed body. The proposal insert trigger requires its finding to
be a pending `update`, T2/human/space_editor finding with proposed action update;
every new or rebased proposal is inserted `pending_review` and cannot be born in a
reviewed or terminal state.
`finding_input_row_version` must equal the finding row version at proposal insert.
For both proposal kinds, the finding is scoped to the destination item: its source
pair equals the proposal target pair and its target pair is null. For
personal-to-part, the proposal's own source pair is the immutable cross-space
personal input while the finding remains a review record in the destination part
space. All items referenced directly by the finding therefore match the finding
space. The finding/proposal states advance together with this exact compatibility
map: pending/pending_review, approved/approved, obsolete/obsolete,
applied/applied, or apply_failed/apply_failed; a rejected or ignored finding maps
its no-longer-actionable proposal to obsolete. No repository may update one without
the other in the same transaction. Because SQLite has no deferred assertion
trigger, transition triggers enforce each local state machine and proposal updates
require the finding's already-updated compatible state; the service updates the
finding first, then proposal, and runs the compatibility query immediately before
commit. A mismatch aborts the transaction. Re-approving an apply-failed finding
moves its proposal back to approved; rejecting or obsoleting that finding moves the
proposal to obsolete.

Kind-specific checks are exact:

- `stable_source_update`: lineage and source item/revision are null; artifact and
  extraction snapshot are non-null; base item equals target item; the fixed base and
  target revisions are identical at proposal creation; the extraction snapshot is
  ready and immutable; and the proposed body size/hash are recomputed from the
  stored proposed body. `preview_sha256` is the SHA256 of RFC 8785 canonical JSON
containing the proposal kind/version, fixed base/source/target IDs, extraction
snapshot ID and hash, proposed body hash, finding input row version, and rule
version.
- `personal_to_part`: lineage and source item/revision are non-null; artifact and
  extraction snapshot are null; source is a personal-space item, target is a
  part-space item, both are immutable members of that lineage, and base item/revision
  equals the target member's fixed `derived_from_item_id/derived_from_revision_id`.
  The source revision is the fixed personal current and target revision is the fixed
  part current captured for the preview. Source and target are distinct. Its preview
  hash uses the same canonical shape with null artifact fields and the three fixed
  revision IDs.

Create unique index `ux_proposal_series_version` on
`(proposal_series_id,proposal_version)`, unique indexes
`ux_proposal_finding(finding_id)`,
`ux_proposal_superseded_once(supersedes_proposal_id)` where non-null, and
`ux_proposal_apply_dedup(applied_dedup_id)` where non-null. Also create
`ix_proposal_target_state(target_item_id,state,updated_at,id)`,
`ix_proposal_source(source_item_id,source_revision_id,state)`, and
`ix_proposal_supersedes(supersedes_proposal_id)`. All fixed input, body, hash,
finding, kind, lineage, series, version, supersedes, creator, and creation columns are
immutable. Only state/application fields, updater/time, and row version may change.
An initial proposal requires `proposal_series_id=id`, version 1, and null
supersedes. A rebased proposal retains the series, names the immediately previous
version as superseded, and has exactly previous version+1. Applied requires the
operation/dedup pair and null error; apply_failed requires that pair and non-null
error; every other state requires all application fields null.

Rebase is an atomic create-and-obsolete command. It requires the old proposal and
finding row versions, creates a new finding and new proposal with version old+1 and
`supersedes_proposal_id=old.id`, then marks the old pair obsolete. It never edits
old fixed inputs. Stable-source rebase fixes the same source snapshot against the
new target; personal-to-part rebase captures new personal/part currents while
retaining the lineage member's immutable fork base.

Apply requires proposal/finding approved, current target-space editor authority
(active owner for personal, current member for part), target lease token HMAC and
fence, expected item row version/current target revision, and
the exact preview hash. For personal-to-part, the fixed source revision must still
be the source current revision; for stable-source, the artifact/snapshot/hash must
still match. On success one `approved_update` target revision/body, queued rescan
job plus revision-rescan transition, target pointer/provisional state,
proposal/finding applied state, operation dedup result, FTS removal, audit, and outbox commit
together. Any stale/authorization/hash/lease mismatch rolls back without changing
proposal/finding or domain state. `apply_failed` records only a reviewed,
non-retryable application error for which no domain effect committed.

### 7.4 `merge_event`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `dedup_id` | UUID unique FK operation dedup RESTRICT |
| `space_id` | UUID FK space RESTRICT |
| `source_item_id`, `source_revision_id` | composite FK revision/item RESTRICT |
| `target_item_id`, `target_revision_id` | composite FK revision/item RESTRICT |
| `result_revision_id` | UUID composite FK with target item |
| `preview_sha256` | SHA256 |
| `relation_id` | UUID unique FK item relation RESTRICT |
| `actor_user_id` | USER_ID FK app user RESTRICT |
| `created_at` | UTC_MS |

Checks reject same source/target and triggers prove both items belong to `space_id`.
The migration names the two unique indexes `ux_merge_dedup(dedup_id)` and
`ux_merge_relation(relation_id)`.
Rows are immutable. Merge apply uses an atomic sorted dual-lease acquisition/check,
two expected revisions, and one transaction for target revision/body, queued rescan
job/revision-rescan transition, target current pointer/provisional state,
source merged/redirect, relation, event, audit, outbox, FTS removal, and dedup result. It never
rewires semantic edges automatically.
The preview is not stored in process memory or in this table. The apply request
repeats the exact confirmed result Markdown, and the service recomputes its
fixed-revision preview binding before entering the write transaction.

## 8. Lease and fencing

### 8.1 `item_lease`

One persistent row exists for every item and is inserted by an
`AFTER INSERT ON knowledge_item` trigger.

| Column | Physical contract |
|---|---|
| `item_id` | UUID primary key FK knowledge item RESTRICT |
| `holder_user_id` | nullable USER_ID FK app user RESTRICT |
| `editor_session_id` | nullable UUID |
| `token_hmac` | nullable `BLOB` with `length(token_hmac)=32` |
| `hmac_key_id` | nullable required-text form, max 80 |
| `fencing_generation` | `INTEGER NOT NULL DEFAULT 0 CHECK(...>=0)` |
| `base_item_row_version` | nullable integer >=1 |
| `base_revision_id` | nullable UUID, composite FK with item to revision |
| `acquired_at`, `heartbeat_at`, `expires_at`, `absolute_expires_at` | nullable UTC_MS |

One check requires all holder/token/base/time fields to be null together for free,
or all non-null for held. Held checks require acquired≤heartbeat≤expires≤absolute.
Indexes are `(expires_at,item_id) WHERE holder_user_id IS NOT NULL` and
`(holder_user_id,expires_at,item_id)`.

The client generates exactly 32 cryptographically random bytes. The
`X-Knowledge-Lease-Token` header is their unpadded base64url encoding: exactly 43
ASCII characters, decoded by a strict no-whitespace/no-padding parser. The server
stores:

```text
HMAC-SHA-256(key[hmac_key_id],
  UTF8("knowledge-lease-v1\0") || UTF8(item_id) || 0x00 || raw_token)
```

Keys are 32 random bytes stored outside SQLite in a DPAPI machine-scope encrypted
key-set file whose NTFS ACL grants only the common service account and deployment
administrators. Every web/worker process reads the identical file. The active key
ID is stored with a held lease; old keys remain readable until every lease using
them has expired/released. Raw tokens are never persisted,
echoed, audited, logged, or put in idempotency request JSON. Comparison is constant
time.

Acquire, release, expiry reclaim, membership revoke, and force release use
compare-and-swap SQL under `BEGIN IMMEDIATE`. A new holder, expiry reclaim, revoke,
or force release increments generation exactly once. Ordinary owner release clears
holder fields without decreasing/resetting generation. Heartbeat changes only
heartbeat/expiry and is the sole retryable-mutation exception that does not need an
operation ID. Save validates actor/current part, token HMAC/key, generation,
session, DB expiry, absolute expiry, base revision, expected revision, and expected
item row version in one transaction; success releases the lease. Part leader/admin
force release requires operation dedup and audited reason.

A retried acquire with the same actor, operation ID, item, editor session, and token
HMAC returns the original fence/expiry without increment. Reuse with any different
field is `IDEMPOTENCY_CONFLICT`.

## 9. Audit, outbox, jobs, projections, backup, and migration

### 9.1 `audit_event`

| Column | Physical contract |
|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `event_uuid` | UUID unique |
| `occurred_at` | UTC_MS, DB default |
| `request_id` | UUID |
| `operation_id` | nullable UUID; null only for non-retryable read-derived/system events |
| `action`, `outcome` | required text, max 120/80 |
| `actor_kind` | `TEXT NOT NULL CHECK(... IN ('user','system'))` |
| `actor_user_id` | nullable USER_ID FK app user RESTRICT |
| `actor_display_name`, `actor_role_snapshot` | required text, max 200 |
| `space_id`, `item_id`, `base_revision_id`, `new_revision_id` | nullable UUID FKs RESTRICT; revision/item consistency is service-asserted |
| `reason` | nullable text, max 2000 |
| `details_json`, `request_meta_json` | JSON_OBJECT |
| `schema_version` | `INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1)` |

Checks require user actor/user ID and system actor/null user ID. Indexes:
`(space_id,occurred_at DESC,id)`, `(item_id,occurred_at DESC,id)`,
`(operation_id,occurred_at,id)`. UPDATE and DELETE always abort. Heartbeats are
metrics, not audit events.

### 9.2 `outbox_event`

| Column | Physical contract |
|---|---|
| `id` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `logical_key` | required text max 300, unique |
| `space_id` | nullable UUID FK space RESTRICT |
| `aggregate_type`, `aggregate_id` | required text max 80 / required text max 120 |
| `aggregate_version` | integer >=0 |
| `event_type` | required text max 120 |
| `payload_json` | JSON_OBJECT |
| `payload_schema_version` | `INTEGER NOT NULL DEFAULT 1 CHECK(...=1)` |
| `state` | `TEXT NOT NULL CHECK(... IN ('pending','processing','retry','applied','dead'))` |
| `available_at` | UTC_MS |
| `claim_owner`, `claim_expires_at` | nullable text/UTC_MS pair |
| `attempt_count` | integer >=0, default 0 |
| `last_error_json` | nullable JSON_OBJECT |
| `created_at`, `completed_at` | UTC_MS / nullable UTC_MS |

Checks require claim fields exactly in processing, completed time exactly in applied
or dead, and no completed time otherwise. Legal transitions are pending to
processing, processing to applied/retry, and retry to processing/dead; applied/dead
are terminal. Processing to retry is also the claim-timeout recovery path. Each
claim increments attempt count. Indexes: `(state,available_at,id)`,
`(state,claim_expires_at,id)`, and `(space_id,id)`.

### 9.3 `durable_job`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `dedup_id` | nullable UUID unique FK operation dedup RESTRICT |
| `operation_id` | nullable caller UUID |
| `actor_kind` | `TEXT NOT NULL CHECK(... IN ('user','system'))` |
| `actor_user_id` | nullable USER_ID FK app user RESTRICT |
| `space_id` | nullable UUID FK space RESTRICT |
| `job_type` | required text, max 100 |
| `handler_version` | required text, max 80 |
| `effect_key` | required text, max 300, unique |
| `input_ref_json` | JSON_OBJECT |
| `input_sha256` | SHA256 |
| `expected_state_json` | JSON_OBJECT |
| `authorization_snapshot_json` | JSON_OBJECT |
| `authorization_snapshot_sha256` | SHA256 |
| `authorization_scope_epoch` | nullable `INTEGER CHECK(...>=1)` |
| `authorization_part_id` | nullable UUID FK `org_unit(id) ON DELETE RESTRICT` |
| `authorization_capabilities_json` | JSON_ARRAY |
| `state` | `TEXT NOT NULL CHECK(... IN ('queued','claimed','running','retry','succeeded','failed','cancelled','dead'))` |
| `claim_owner`, `claim_expires_at` | nullable text/UTC_MS pair |
| `attempt_count`, `max_attempts` | integers with `0<=attempt_count<=max_attempts` and `max_attempts>=1` |
| `cancellable` | BOOL NOT NULL, default 0 |
| `cancel_requested` | BOOL default 0 |
| `progress_permille` | integer 0..1000 |
| `result_json`, `error_json` | nullable JSON_OBJECT |
| `available_at`, `created_at`, `updated_at`, `completed_at` | UTC_MS; completion nullable |

Claim fields are present exactly for claimed/running. Terminal states are succeeded,
failed, cancelled, dead and require completion; succeeded requires result/no error;
failed/dead require error/no result. `cancel_requested=1` and a transition to
cancelled are legal only when `cancellable=1`. Legal transitions:

```text
queued  -> claimed | cancelled
claimed -> running | retry | cancelled
running -> succeeded | failed | retry | cancelled
retry   -> claimed | cancelled | dead
```

Terminal states cannot transition. Retryable execution failures use retry, not
failed; only an exhausted retry row may transition to dead. Indexes:
`(state,available_at,id)`, `(state,claim_expires_at,id)`,
`(actor_user_id,created_at DESC,id)`, `(space_id,state,created_at,id)`, and
`ix_job_handler_state(job_type,handler_version,state,available_at,id)`. Inputs are
only durable IDs/hashes/blob references; Python closures are forbidden. The
initial row is exactly queued with zero attempts, no claim/result/error/completion,
`cancel_requested=0`, `progress_permille=0`, and `updated_at=created_at`. The
deployment handler registry is keyed by the exact `(job_type,handler_version)`;
enqueue and claim reject an unknown pair, and deployment retains every version
referenced by a non-terminal job. Job type, handler version, effect key, input,
expected state, authorization snapshot, actor/scope, creation identity, and
cancellability are immutable. Domain effect commit uses `effect_key` plus operation
dedup so crash-after-commit cannot duplicate a revision or other canonical effect.

For a user job, `actor_kind='user'` requires actor, captured scope epoch, and a
canonical authorization snapshot containing account status, system role, current
part (nullable for an intentionally unassigned personal-only actor), scope epoch,
space, and sorted capability strings. `authorization_part_id` equals that captured
part and `authorization_capabilities_json` equals the same sorted array. For a
system job, actor, part, and scope epoch are null, capabilities are empty, and the
snapshot is exactly the versioned system-actor shape. The SHA256 is computed over
the RFC 8785 canonical JSON bytes. SQLite has no v1 SHA UDF: the enqueue repository
canonicalizes, hashes, compares, and binds both values before INSERT, while startup
and integrity scans independently rehash every row. DB checks still enforce JSON
shape and SHA format. The snapshot is immutable audit evidence, never authority.

Immediately before any canonical effect, the worker opens the domain transaction
and revalidates the current account status, scope epoch, current membership/part,
space access, and required capability from the database. It also compare-and-swaps
every aggregate/revision in `expected_state_json`. Disable, part move/unassign, or
capability loss therefore prevents the effect even if execution already started;
the job records an authorization failure only after the domain transaction rolls
back. This is the schema/service contract exercised by `JOB-006`.

Create unique `ux_job_dedup(dedup_id)` where non-null. When `dedup_id` is non-null,
a trigger requires non-null actor/operation ID and an
operation-dedup row with the same actor, operation ID, request hash equal to
`input_sha256`, and `operation_type='job:'||job_type`. Claim/retry SQL uses state,
claim owner, claim expiry, and attempt count in its compare-and-swap predicate.

The two schema-linked v1 handlers are frozen so aggregate triggers can verify
their durable identity without consulting a process-local registry:

| Job type | Handler | Exact `input_ref_json` keys | Trigger-bound values |
|---|---|---|---|
| `publication_preview.extract` | `v1` | `preview_id`, `artifact_id`, `rule_version` | preview ID, artifact ID, and rule version |
| `curation.rescan` | `v1` | `item_id`, `revision_id`, `rule_version` | transition item/result revision; a system finding decision also matches the finding rule version |

These objects contain exactly the listed keys; IDs and rule versions use their
normal physical domains. Other registered job types remain service-registry
entries and are not accepted by a schema trigger as either of these two handlers.
Changing either linked handler version or input shape requires a schema migration.

The v1 authorization snapshot semantic shapes are also exact. A system snapshot
has exactly `schema_version=1` and `actor_kind='system'`. A user snapshot has
exactly `schema_version`, `actor_kind`, `actor_user_id`, `account_status`,
`system_role`, `current_part_id`, `scope_epoch`, `space_id`, and `capabilities`;
its scalar values equal the durable job columns/captured user facts and its
capability array equals `authorization_capabilities_json` as a duplicate-free,
lexically sorted string array. The system capability array is empty. Triggers
validate these semantic shapes and linked values. The repository still owns RFC
8785 serialization and SHA256 binding, and startup/integrity scans independently
rehash them because v1 deliberately has no SQL SHA function.

### 9.4 `projection_state`

| Column | Physical contract |
|---|---|
| `projection_name`, `space_id` | required text/UUID composite primary key; space FK RESTRICT |
| `desired_generation`, `built_generation` | integers >=0 with built≤desired |
| `checksum_sha256` | nullable SHA256 |
| `status` | `TEXT NOT NULL CHECK(... IN ('idle','building','ready','error'))` |
| `last_error_json` | nullable JSON_OBJECT |
| `updated_at`, `built_at` | UTC_MS / nullable UTC_MS |
| `row_version` | versioned aggregate |

Index `(status,updated_at,space_id)`. Canonical visible mutation increments
`knowledge_space.generation`, sets desired generation, and inserts outbox in the
same transaction. Materializers never update domain rows.

### 9.5 `backup_run` and `backup_blob`

`backup_run`:

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `state` | `TEXT NOT NULL CHECK(... IN ('preparing','copying','verified','failed'))` |
| `snapshot_db_relative_path` | nullable text, max 1000 |
| `snapshot_db_sha256`, `manifest_sha256` | nullable SHA256 |
| `snapshot_audit_high_water` | `INTEGER NOT NULL DEFAULT 0 CHECK(...>=0)` |
| `schema_version` | `INTEGER NOT NULL CHECK(schema_version=1)` |
| `sqlite_source_id` | required text exactly equal to the v1 allowlisted source ID |
| `error_json` | nullable JSON_OBJECT |
| `created_at`, `completed_at` | UTC_MS / nullable UTC_MS |
| `row_version` | versioned aggregate |

Verified requires both paths/hashes and completion with no error; failed requires
completion/error; preparing/copying require no completion. Legal transitions are
preparing→copying/failed and copying→verified/failed; verified/failed are terminal.
Index `ix_backup_state_time(state,created_at DESC,id)`.

`backup_blob`:

| Column | Physical contract |
|---|---|
| `backup_run_id` | UUID FK backup run RESTRICT |
| `blob_sha256` | SHA256; deliberately not an FK to the live blob row |
| `size_bytes` | integer >=0 |
| `status` | `TEXT NOT NULL CHECK(... IN ('pending','verified','failed'))` |
| `copied_at`, `verified_at` | nullable UTC_MS |
| `error_json` | nullable JSON_OBJECT |

Primary key `(backup_run_id,blob_sha256)` and reverse index
`ix_backup_blob_hash(blob_sha256,backup_run_id)`. Verified requires both times/no
error; failed requires error; pending has neither verified time nor error. While a
backup run is non-terminal, these hashes are a durable live-blob GC hold. Omitting
the live FK is intentional: a completed off-machine backup must not prevent later
production blob GC. The manifest is derived from the completed SQLite snapshot,
not the live DB.

Backup-blob transitions are pending→verified/failed and terminal thereafter.
Preparing/copying backup runs globally pause GC; after all hold rows are populated,
the copying state and per-hash holds remain until verified/failed finalization.

### 9.6 `blob_gc_candidate`

| Column | Physical contract |
|---|---|
| `blob_sha256` | SHA256 primary key FK blob object RESTRICT |
| `unreferenced_since` | UTC_MS |
| `delete_after` | UTC_MS, check `delete_after>=unreferenced_since+86400000` |
| `state` | `TEXT NOT NULL CHECK(... IN ('quarantined','claimed','retry','dead'))` |
| `claim_owner`, `claim_expires_at` | nullable text/UTC_MS pair |
| `attempt_count` | integer >=0, default 0 |
| `last_error_json` | nullable JSON_OBJECT |
| `updated_at` | UTC_MS |

Claim fields exist exactly in claimed. Legal transitions are quarantined→claimed,
claimed→retry/dead, retry→claimed/dead. Successful physical deletion is finalized
by deleting this candidate row first and then `blob_object` in one short DB
transaction; there is no persisted deleted state. Finalization is allowed only if
all canonical reference queries still return zero and no non-terminal backup hold
exists. A missing physical blob is idempotent success only after its expected
hash/path was verified.

Add `ix_blob_gc_available(state,delete_after,blob_sha256)` and
`ix_blob_gc_claim_expiry(state,claim_expires_at,blob_sha256)`. A reference query is
exactly the union of `artifact_blob_body.content_sha256`,
`extraction_snapshot_body.content_blob_hash`,
`knowledge_revision_body.content_blob_hash`, and
`change_proposal.proposed_content_blob_hash`, excluding nulls. Future migrations
adding a blob FK must extend this union in the same migration. GC performs no
finalization while any backup run is preparing/copying, which closes the interval
before its `backup_blob` hold rows are populated.

### 9.7 `legacy_migration_map`

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `migration_run_id` | UUID |
| `legacy_kind` | required text max 80 |
| `normalized_path` | required text max 1000 |
| `source_sha256` | SHA256 |
| `resolved_owner_user_id` | nullable USER_ID FK app user RESTRICT |
| `artifact_id`, `item_id`, `revision_id`, `edge_id` | nullable UUID FKs RESTRICT |
| `status` | `TEXT NOT NULL CHECK(... IN ('imported','quarantined','skipped_derived','failed'))` |
| `details_json` | JSON_OBJECT |
| `created_at`, `updated_at` | UTC_MS |

Unique `(legacy_kind,normalized_path,source_sha256)` and index
`(migration_run_id,status,id)`. A blank user→part mapping creates the explicit
unassigned personal-only state; an absent/invalid mapping record is quarantined and
is never inferred from path, content, or Notion ownership.

Forward-version note: schema v3 additionally creates unique
`ux_migration_normalized_path(normalized_path)`, so a stable normalized locator
cannot be reinterpreted under a different legacy kind or source hash. It also adds
`BEFORE UPDATE` and `BEFORE DELETE` triggers that abort with the stable code
`LEGACY_MIGRATION_MAP_IMMUTABLE`; mapping history is insert-only. Because no P2.3
importer was deployed before this forward migration, v3 requires an empty v2 map
and fails atomically on any preexisting row; duplicate normalized locators also
fail at unique-index creation. Its postconditions prove the index, both triggers,
and zero duplicate locators.

### 9.8 `legacy_migration_relationship_map` (forward schema v4)

Physical schema v4 adds a separate append-only checkpoint for a reviewed P2.3
`asset_reference` effect. It does not reinterpret `legacy_migration_map` and its
upgrade explicitly allows committed v3 mapping rows. The v4 preflight instead
requires the relationship table and all of its named schema objects to be absent;
a collision fails the migration atomically.

| Column | Physical contract |
|---|---|
| `id` | UUID primary key |
| `migration_run_id` | UUID |
| `relationship_id` | exactly `rel_` plus 24 lowercase hexadecimal characters |
| `relationship_sha256` | SHA256 of the exact reviewed inventory relationship |
| `relationship_type` | `TEXT NOT NULL CHECK(...='asset_reference')` |
| `from_inventory_record_id`, `to_inventory_record_id` | distinct IDs, each exactly `rec_` plus 24 lowercase hexadecimal characters |
| `from_mapping_id`, `to_mapping_id` | distinct UUID FKs to `legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `resolved_owner_user_id` | USER_ID FK `app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `target_space_id` | UUID FK `knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `source_artifact_id` | UUID FK `source_artifact(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `source_item_id` | UUID FK `knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `source_revision_id` | UUID FK `knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `target_asset_artifact_id` | UUID FK `source_artifact(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `target_asset_blob_id`, `embedded_asset_blob_id` | distinct UUID FKs `artifact_blob(id) ON UPDATE RESTRICT ON DELETE RESTRICT` |
| `embedded_ordinal` | integer greater than or equal to zero |
| `embedded_logical_name_sha256` | SHA256 of the exact embedded manifest logical name |
| `status` | `TEXT NOT NULL CHECK(...='imported')` |
| `details_json` | JSON_OBJECT containing the canonical reviewed relationship/effect binding |
| `created_at`, `updated_at` | UTC_MS, required equal on insert |

All listed columns are non-null. Unique index
`ux_migration_relationship_identity(relationship_id)` prevents a reviewed
relationship from acquiring a second effect. Unique index
`ux_migration_relationship_embedded_blob(embedded_asset_blob_id)` prevents one
embedded manifest row from serving as two checkpoints. Lookup indexes are
`ix_migration_relationship_run(migration_run_id,status,id)` and
`ix_migration_relationship_endpoints(from_mapping_id,to_mapping_id,relationship_id)`.

Triggers `trg_legacy_migration_relationship_map_history_update` and
`trg_legacy_migration_relationship_map_history_delete` reject every mutation
with `LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE`. The importing repository
must additionally prove, in the same transaction and before this immutable row
is inserted, exact reviewed endpoint-map identity, one owner/personal space,
source artifact/item/revision closure, target raw-asset manifest identity, and
the newly created embedded-asset manifest/blob/hash/name/ordinal binding. Those
cross-row semantic checks are deliberately repository postconditions rather
than duplicated JSON-extraction rules in v4 DDL.

This schema prerequisite and its synthetic fixture tests are not an importer,
live apply, reconciliation result, or `MIG-*` acceptance claim.

### 9.9 P2.4 migration effect checkpoints (forward schema v5)

Physical schema v5 preserves every v1-v4 object and committed row. It adds
nullable `finding_id` and `audit_event_id` targets to
`legacy_migration_map`, each with a RESTRICT foreign key and partial unique
index (`ux_migration_finding_target`, `ux_migration_audit_event_target`). A
record checkpoint can therefore bind one reviewed candidate finding or one
labelled historical audit event without reinterpreting existing P2.3 mappings.

The new STRICT `legacy_migration_p24_relationship_map` is the append-only
checkpoint for reviewed P2.4 relationships. It stores the exact plan/run,
relationship ID/hash/type, source/from/to mapping IDs, resolved owner and
personal space, effect kind and its optional canonical edge/finding/item ID,
status, canonical details JSON, and equal create/update timestamps. Frozen
relationship types are `candidate_source`, `candidate_target`,
`graph_node_source`, `contradicts`, `extends`, `related`, `supports`, and
`exact_content_duplicate`; effect kind is exactly `dependency`, `edge`,
`finding`, or `provenance`, with the corresponding target-column shape.

Unique index `ux_migration_p24_relationship_identity(relationship_id)` closes
exact-once identity. Run, endpoint, and effect indexes support bounded replay
verification. Update/delete triggers abort with
`LEGACY_MIGRATION_P24_RELATIONSHIP_MAP_IMMUTABLE`. The repository additionally
revalidates exact reviewed endpoint mappings, one owner/personal space, source
binding, effect semantics, and dependency-closed parent sets before inserting
an immutable checkpoint.

The reviewed cumulative v5 schema SHA-256 is
`05831cad4338a49c16905cfc5daabd7e85973dbf33ed833706c4f172b0a6eb96`;
the v5 migration checksum is
`feb79ac03f3882a5f78de1355c7c4ad2202e2c13da5260f4a6a1457995b86832`;
the five-version catalog checksum is
`6cbd6dce13d246423e3f57161355274442d1e5b03de3ac28c5b803be557354bd`.
This checkpoint schema is not observed migration reconciliation or a `MIG-*`
acceptance claim.

### 9.10 P2.5 conversation and Notion-link checkpoints (forward schema v6)

Physical schema v6 preserves every v1-v5 object and committed row. It adds
nullable `conversation_id`, `message_id`, `summary_id`, and
`context_binding_id` targets to `legacy_migration_map`, each with a RESTRICT
foreign key. Message, summary, and context-binding targets have partial unique
indexes, while the conversation index permits one imported session parent and
its reconciled logical children to bind the same canonical conversation.

The new STRICT `legacy_migration_p25_relationship_map` is the append-only
checkpoint for a reviewed valid `notion_session_page` relationship. It stores
the exact plan/run and relationship identity, the logical Notion-link and
session-parent mapping endpoints, canonical conversation and context-binding
targets, one resolved user/personal space, page-identity and source-row hashes,
canonical details JSON, imported status, and equal creation/update timestamps.
Unique indexes close relationship and context-binding identity; run and
endpoint indexes support bounded replay. Update/delete triggers abort with
`LEGACY_MIGRATION_P25_RELATIONSHIP_MAP_IMMUTABLE`.

Repository postconditions additionally prove that both endpoint mappings come
from the same reviewed plan, inventory manifest, root binding, owner, and
personal space; the origin is the exact imported session container; the source
is the exact logical Notion link; and the canonical live Notion-page binding
matches the reviewed page and row hashes. The retained external Notion database
is not inserted into `legacy_migration_map` and remains authoritative source
state outside Knowledge Core.

The reviewed cumulative v6 schema SHA-256 is
`32d38a5942119766bc96f28b51a302b4c005f484bf37bc23f2f23cfdf5b138c2`;
the v6 migration checksum is
`1b5790ac762ac81cd120907d367fb791e0f8d3f0d58000fa4d4fe7d9bbf1e60c`;
the six-version catalog checksum is
`f59d208c00a0fbf460983c7e272b0d1fd53e4ec20c66b5780e563b06c6695da0`.
This checkpoint schema is not observed migration reconciliation or a `MIG-*`
acceptance claim.

### 9.11 P5.6 blob-GC claim safety (forward schema v7)

Physical schema v7 preserves every v1-v6 object and committed row and adds no
table or index. It adds eight triggers that close the asynchronous physical
deletion race around `blob_gc_candidate`:

- inserts into `artifact_blob_body`, `extraction_snapshot_body`,
  `knowledge_revision_body`, and the nullable proposal blob reference are
  rejected with `BLOB_GC_REFERENCE_FENCED` while the candidate is `claimed`,
  `retry`, or `dead`;
- a candidate cannot be inserted while any canonical body/proposal reference
  exists (`BLOB_GC_REFERENCE_PRESENT`);
- transition to `claimed` rechecks DB time, zero canonical references, verified
  blob metadata, and absence of a `preparing`/`copying` backup
  (`BLOB_GC_CLAIM_INELIGIBLE`);
- deleting a candidate is legal only to cancel a newly referenced quarantined
  row or to finalize an unreferenced claimed row while no backup is active
  (`BLOB_GC_FINALIZE_INELIGIBLE`); and
- a backup cannot enter `preparing` or `copying` while a blob is claimed
  (`BLOB_GC_BACKUP_RACE`).

The service performs only short claim and finalize transactions. Exact CAS
path/size/SHA-256/single-link verification and handle-bound deletion occur
between them, outside SQLite. Importing or constructing the service starts no
timer, thread, scheduler, or worker; a later P7 worker may call one explicit
`run_once`. Permanent item purge remains a human administrator command and
only enqueues an unreferenced blob candidate after an additional 24-hour
quarantine delay.

The cumulative v7 trigger inventory is 152. The reviewed cumulative v7 schema
SHA-256 is
`5ca2cb78e1433ac8ce6976263d578df6f3a625b73bce5f8556b93d395db585a4`;
the v7 migration checksum is
`4f2b5f54b89bf0e3faf73aeb1096609c802a0543498b2e69619f071f9d86c440`;
the seven-version catalog checksum is
`a6c3c4d5d8397f3b8c57470c2391221f823af42a37483c9746b4ae9494f77bf3`.
This forward hardening is P5.6 implementation evidence, not a scheduler,
backup-completion, worker, performance, or rollout claim.

## 10. Transactional FTS5

### 10.1 `search_document`

This is a transactional, rebuildable DB projection of the current Agent-visible
revision, not a second source of truth.

| Column | Physical contract |
|---|---|
| `rowid` | `INTEGER PRIMARY KEY AUTOINCREMENT` |
| `item_id` | UUID unique |
| `space_id` | UUID |
| `revision_id` | UUID |
| `title`, `content_md` | `TEXT NOT NULL` |
| `updated_at` | UTC_MS |

Composite FKs `(item_id,space_id)` to item and `(revision_id,item_id)` to revision,
both RESTRICT. Index `(space_id,item_id)`.

### 10.2 `knowledge_fts`

The migration creates exactly:

```sql
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
  title,
  content_md,
  content='search_document',
  content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
```

Three standard external-content triggers on `search_document` perform FTS insert,
delete command, and delete+insert update. Search always executes the FTS match then
joins `search_document` by rowid and applies `search_document.space_id=:authorized`
before returning rows.

Initial publication, restore, or a finding-decision release upserts one search row
only when the item is active and published and its body exists. A blob-backed body is hash-verified and decoded by
the repository before the same transaction writes `search_document`; FTS never
reads the filesystem itself. Every edit/merge/proposal-created revision first moves
the target to provisional and removes FTS until curation release. Deactivate,
merge-source, quarantine/provisional, and purge also delete it. These changes occur
in the same canonical transaction. A full
rebuild deletes/repopulates `search_document` from item/current revision/body and
then runs the FTS5 rebuild command; settled incremental and full checksums must
match.

## 11. Required trigger set

The v1 migration creates and schema-tests every trigger below. Trigger error text is
the stable uppercase name shown here so tests can assert it.

| Trigger family | Required effect/error |
|---|---|
| row version | Mutable aggregate update must be old+1: `ROW_VERSION_CONFLICT` |
| org parent/kind/cycle | Section 3.2: `ORG_PARENT_INVALID`, `ORG_CYCLE` |
| org inactivation | Prevent live references: `ORG_UNIT_IN_USE` |
| user part | Active part only; epoch coupling: `USER_PART_INVALID`, `SCOPE_EPOCH_INVALID` |
| membership/role | Part/membership validity, leader close/write-disable ordering, and close-only history: `MEMBERSHIP_INVALID`, `LEADER_WRITE_GUARD`, `HISTORY_IMMUTABLE` |
| space owner | Exactly matching owner kind and future-write guard: `SPACE_OWNER_INVALID`, `SPACE_KIND_DISABLED` |
| immutable scope | Conversation identity/space/epoch: `CONVERSATION_SCOPE_IMMUTABLE` |
| conversation turn | Message roles, dedup identity, state/result shape, and event order: `CONVERSATION_TURN_INVALID` |
| context/summary | Binding detach-only and summary invalidate-only state/scope: `CONVERSATION_CONTEXT_INVALID` |
| immutable history | Message, lineage group/member, operation, source, merge, audit, and all non-retention artifact/extraction metadata: `HISTORY_IMMUTABLE` |
| artifact/extraction body | Eligible-admin last-reference or 30-day orphan delete, blocker query, and present-to-purged retention only: `ARTIFACT_BODY_RETENTION_INVALID` |
| publication preview | Async job/sync ready identity, exact state/result/null shape, and immutable ready payload: `PUBLICATION_PREVIEW_INVALID` |
| lineage member | Same-group item/revision and fixed derived fork base: `LINEAGE_MEMBER_INVALID` |
| item pointer/state | Same-item deferred FKs plus legal lifecycle, including the lifecycle-only inactive-to-purged pointer exception: `ITEM_STATE_INVALID` |
| item curation | Event-backed curation transition, exact automatic finding/job identity, and FTS visibility: `ITEM_CURATION_INVALID` |
| revision metadata/body | Revision metadata UPDATE/DELETE always rejected; body delete only in eligible admin tombstone transaction: `REVISION_BODY_IMMUTABLE` |
| finding policy | Exact enumerated kind/severity/tier/mode/reviewer/visibility/action tuple, legal transition, and exactly one caller-or-system-job application proof: `CURATION_POLICY_INVALID` |
| change proposal | Exact kind shape, finding/state coupling, immutable inputs, rebase chain, and apply dedup: `CHANGE_PROPOSAL_INVALID` |
| lease seed | Insert one free persistent lease row after item insert |
| outbox state | Section 9.2 transition/claim shape: `OUTBOX_STATE_INVALID` |
| job state/auth | Transition/claim/result shape, handler version, snapshot/hash/actor shape: `JOB_STATE_INVALID` |
| backup/GC state | Sections 9.5-9.6 transition/claim shape: `MAINTENANCE_STATE_INVALID` |
| FTS external content | Insert/delete/update synchronization from `search_document` |

The exact reviewed manifest contains 134 triggers: 131 non-FTS triggers and the
three FTS external-content triggers. Every name has the stable form
`trg_<table>_<purpose>_<operation>`, where the final component is exactly
`insert`, `update`, or `delete`. Migration verification compares `sqlite_schema`
against this complete ordered name set; an omitted, renamed, duplicate, or extra
trigger fails the schema gate.

```text
trg_app_user_row_version_update
trg_backup_run_row_version_update
trg_change_proposal_row_version_update
trg_conversation_row_version_update
trg_conversation_context_binding_row_version_update
trg_conversation_summary_row_version_update
trg_conversation_turn_row_version_update
trg_curation_finding_row_version_update
trg_item_relation_row_version_update
trg_knowledge_edge_row_version_update
trg_knowledge_item_row_version_update
trg_knowledge_space_row_version_update
trg_org_unit_row_version_update
trg_projection_state_row_version_update
trg_publication_preview_row_version_update
trg_org_unit_parent_insert
trg_org_unit_parent_update
trg_org_unit_inactivate_guard_update
trg_app_user_part_insert
trg_app_user_part_update
trg_app_user_leader_guard_update
trg_org_membership_validity_insert
trg_org_membership_history_update
trg_org_membership_leader_guard_update
trg_org_membership_history_delete
trg_org_role_assignment_validity_insert
trg_org_role_assignment_history_update
trg_org_role_assignment_leader_write_guard_update
trg_org_role_assignment_history_delete
trg_knowledge_space_owner_insert
trg_knowledge_space_owner_update
trg_knowledge_space_kind_disabled_insert
trg_knowledge_space_kind_disabled_update
trg_conversation_scope_update
trg_conversation_message_history_update
trg_conversation_message_history_delete
trg_conversation_turn_validity_insert
trg_conversation_turn_validity_update
trg_conversation_turn_event_order_insert
trg_conversation_turn_event_history_update
trg_conversation_turn_event_history_delete
trg_conversation_context_binding_validity_insert
trg_conversation_context_binding_validity_update
trg_conversation_context_binding_history_delete
trg_conversation_summary_validity_insert
trg_conversation_summary_validity_update
trg_conversation_summary_history_delete
trg_source_artifact_validity_insert
trg_source_artifact_retention_update
trg_source_artifact_history_delete
trg_artifact_blob_validity_insert
trg_artifact_blob_retention_update
trg_artifact_blob_history_delete
trg_artifact_blob_body_validity_insert
trg_artifact_blob_body_history_update
trg_artifact_blob_body_retention_delete
trg_extraction_snapshot_validity_insert
trg_extraction_snapshot_retention_update
trg_extraction_snapshot_history_delete
trg_extraction_snapshot_body_validity_insert
trg_extraction_snapshot_body_history_update
trg_extraction_snapshot_body_retention_delete
trg_publication_preview_validity_insert
trg_publication_preview_validity_update
trg_publication_preview_history_delete
trg_lineage_group_validity_insert
trg_lineage_group_history_update
trg_lineage_group_history_delete
trg_lineage_member_validity_insert
trg_lineage_member_history_update
trg_lineage_member_history_delete
trg_operation_dedup_validity_update
trg_operation_dedup_history_delete
trg_publication_operation_validity_insert
trg_publication_operation_history_update
trg_publication_operation_history_delete
trg_publication_target_validity_insert
trg_publication_target_history_update
trg_publication_target_history_delete
trg_knowledge_item_identity_update
trg_knowledge_item_lifecycle_update
trg_knowledge_item_curation_update
trg_knowledge_item_transition_pointer_update
trg_knowledge_item_history_delete
trg_item_curation_transition_validity_insert
trg_item_curation_transition_history_update
trg_item_curation_transition_history_delete
trg_knowledge_revision_history_update
trg_knowledge_revision_history_delete
trg_knowledge_revision_body_validity_insert
trg_knowledge_revision_body_history_update
trg_knowledge_revision_body_purge_delete
trg_item_source_history_update
trg_item_source_history_delete
trg_item_relation_validity_update
trg_item_relation_history_delete
trg_item_deactivation_episode_validity_insert
trg_item_deactivation_episode_validity_update
trg_item_deactivation_episode_history_delete
trg_knowledge_edge_approval_insert
trg_knowledge_edge_validity_update
trg_knowledge_edge_history_delete
trg_curation_finding_policy_insert
trg_curation_finding_policy_update
trg_curation_finding_state_update
trg_curation_finding_history_delete
trg_change_proposal_validity_insert
trg_change_proposal_state_update
trg_change_proposal_history_delete
trg_merge_event_validity_insert
trg_merge_event_history_update
trg_merge_event_history_delete
trg_knowledge_item_lease_seed_insert
trg_item_lease_identity_update
trg_item_lease_history_delete
trg_audit_event_history_update
trg_audit_event_history_delete
trg_outbox_event_state_insert
trg_outbox_event_state_update
trg_outbox_event_history_delete
trg_durable_job_state_insert
trg_durable_job_state_update
trg_durable_job_history_delete
trg_backup_run_state_insert
trg_backup_run_state_update
trg_backup_run_history_delete
trg_backup_blob_state_insert
trg_backup_blob_state_update
trg_backup_blob_history_delete
trg_blob_gc_candidate_state_insert
trg_blob_gc_candidate_state_update
trg_search_document_fts_insert
trg_search_document_fts_delete
trg_search_document_fts_update
```

No trigger calls an application-defined function. Cross-table rules use `EXISTS`
queries and `RAISE(ABORT,...)`; same-row enum/null-shape rules use `CHECK`.

## 12. Required index inventory

Migration verification compares `sqlite_schema` against this complete inventory of
exactly 120 explicitly named indexes:

```text
ux_org_unit_active_sibling
ix_org_unit_parent
ix_app_user_part_status
ix_app_user_status
ux_org_membership_current_user
ix_org_membership_part_period
ix_org_membership_user_history
ux_org_role_active_unit_role
ix_org_role_user
ux_space_personal_owner
ux_space_org_owner
ix_space_kind_status
ux_conversation_id_space
ix_conversation_owner_time
ix_conversation_space_time
ux_message_id_conversation
ux_message_sequence
ix_message_conversation_time
ux_turn_id_conversation
ux_turn_conversation_operation
ux_turn_dedup
ix_turn_conversation_time
ix_turn_state
ix_turn_event_conversation
ux_context_binding_attached
ix_context_binding_conversation
ux_conversation_summary_active
ix_conversation_summary_history
ix_pin_item
ux_blob_relative_path
ix_artifact_origin
ix_artifact_raw_hash
ix_artifact_retention
ux_artifact_blob_identity
ux_artifact_blob_id_hash
ux_artifact_raw_role
ix_artifact_blob_content_hash
ix_artifact_blob_body_hash
ux_extraction_id_artifact
ix_extraction_artifact_time
ix_extraction_body_blob
ix_preview_actor_status_expiry
ux_preview_durable_job
ux_lineage_member_item
ix_lineage_member_derived
ux_operation_dedup_actor_type_id
ix_operation_dedup_cleanup
ux_publication_actor_operation
ux_publication_dedup
ix_publication_target_item
ix_publication_target_finding
ix_item_visible
ix_item_lineage
ix_item_redirect
ix_item_deactivation
ix_item_curation_transition
ux_curation_transition_id_item
ux_curation_transition_dedup
ux_curation_transition_job
ux_curation_transition_decision_job
ux_curation_transition_item_version
ix_curation_transition_item
ux_revision_id_item
ux_revision_number
ix_revision_item_time
ix_revision_body_blob
ux_item_source_identity
ix_item_source_artifact
ix_item_source_revision
ux_item_relation_active
ix_item_relation_target
ux_deactivation_id_item
ux_deactivation_episode
ux_deactivation_open
ix_deactivation_eligibility
ux_edge_active_identity
ux_edge_approval_finding
ix_edge_reverse
ix_edge_revalidation
ix_finding_review_queue
ix_finding_source_revision
ix_finding_target_revision
ix_finding_ignored_pair
ux_finding_apply_dedup
ix_finding_apply_job
ux_proposal_series_version
ux_proposal_finding
ux_proposal_superseded_once
ux_proposal_apply_dedup
ix_proposal_target_state
ix_proposal_source
ix_proposal_supersedes
ux_merge_dedup
ux_merge_relation
ix_lease_expiry
ix_lease_holder
ix_audit_space_time
ix_audit_item_time
ix_audit_operation
ux_audit_event_uuid
ux_outbox_logical_key
ix_outbox_available
ix_outbox_claim_expiry
ix_outbox_space
ux_job_effect_key
ix_job_available
ix_job_claim_expiry
ix_job_actor_time
ix_job_space_state
ix_job_handler_state
ux_job_dedup
ix_projection_status
ix_backup_state_time
ix_backup_blob_hash
ix_blob_gc_available
ix_blob_gc_claim_expiry
ix_migration_run_status
ux_migration_source_identity
ux_search_document_item
ix_search_document_space
```

Primary-key autoindexes are not renamed or counted as substitutes. The migration
uses these exact explicit names, including explicit unique indexes described in the
table sections.

## 13. Command transaction postconditions

DB checks cannot express every lower-bound or authorization rule. Each service
executes the named postconditions immediately before commit; failure raises and
rolls back.

| Command | Mandatory single-transaction postcondition |
|---|---|
| account create/assign/move/unassign/disable | current part is null with zero open memberships or matches the sole open membership; affected active-leader role is atomically replaced or closed with every owned part space write-disabled; epoch/session/lease/job revocation and audit/outbox consistent |
| preview finalize | exact status/null shape, immutable rule/findings/ready-result schema, durable job match for async or complete direct-ready shape for sync; no knowledge row created |
| publication | dedup terminal result, operation, all requested targets, item/source/lineage-member/audit/outbox atomic; every lineage item has exactly one member; new `both` IDs/revisions differ; no target outside personal/current part |
| conversation turn accept/complete | operation dedup, one user message, one turn, ordered turn events, and at most one canonical assistant message; reconnect/retry returns the stored turn |
| edit save/rescan | one immutable revision metadata+body, queued versioned rescan job and revision-rescan transition, item CAS to provisional, finding obsolescence, FTS removal, audit/outbox, lease release atomic |
| curation finding decision | exact current finding and transition event; provisional to published/quarantined only through allowed human or system tuple; exactly one caller/job application proof, with automatic transition `decision_job_id=finding.applied_job_id`; finding/dedup-or-job effect, FTS, audit/outbox/generation atomic |
| deactivate | one open episode, inactive item, FTS removal, audit/outbox; zero knowledge-edge writes |
| restore | latest episode closed, same item/current revision, conditional FTS row, audit/outbox; zero knowledge-edge writes |
| merge | sorted dual lease/fence and two revision checks; target revision/body plus queued rescan/transition to provisional, source merged/redirect, relation/event, FTS removal, audit/outbox/dedup atomic |
| proposal rebase | old proposal/finding expected versions; one new fixed-input version in the same series and old pair obsolete; no item/revision mutation |
| proposal apply | approved pair, current source/target checks, lease HMAC/fence, preview hash; exactly one target revision/body plus queued rescan/transition to provisional, pair state, FTS removal, audit/outbox/dedup atomic |
| purge | admin, DB time at/after latest eligibility, inactive item and no item-purge blockers; tombstone/current pointer null with unchanged curation status/transition pointer and no curation-transition row, episode, FTS deletion, every revision body deletion with immutable revision metadata untouched, conditional last-reference artifact/extraction body cleanup or `retained_due_reference`, audit/outbox/one blob-GC job atomic; edge rows unchanged |
| orphan ingestion cleanup | 30-day DB eligibility, zero knowledge/live-preview/binding/proposal/backup references; artifact/extraction links and retention reason/time plus audit/outbox/job effect atomic; metadata retained |
| job result | one effect key has at most one domain commit; terminal job/result or retry transition is consistent with dedup |

All authorization checks are repeated inside the write transaction. Repository
methods expose no “skip validation”, last-write-wins, raw SQL patch, or projection
write path.

## 14. Migration order and verification

The numbered v1 migration performs these steps in this exact order:

1. `SingleMigrator` locally reruns the P0.4 fixed-local-NTFS/final-target/runtime/
   JSON/FTS gate for the explicit data, database, blob, and projection paths; binds
   it to the exact existing database file identity or, for bootstrap, to the final
   parent/target and then the newly created file identity; and accepts no external
   attestation in place of an observable result.
2. `BEGIN EXCLUSIVE`; create ordinary `STRICT` rowid tables in dependency order,
   with no `WITHOUT ROWID` clause. SQLite permits declarations that reference a
   table created later in the same schema.
3. Create explicit unique and lookup indexes from section 12.
4. Create all 131 non-FTS triggers from section 11.
5. Create `knowledge_fts` and its three external-content triggers, bringing the
   exact required trigger total to 134.
6. Load `knowledge_core/migrations/seed_manifest_v1.json` and insert the frozen
   responsibility root UUID/name/name-key from section 3.2, leaving its timestamp
   columns to migration-time DB defaults. Import each user only with an explicit
   org-map disposition: a valid part creates the matching current part and one open
   membership; an explicit blank creates an unassigned personal-only user with null
   `current_part_id` and zero open memberships; missing/invalid dispositions are
   quarantined, never inferred.
7. Insert `schema_migration(version=1,...)`, set `user_version=1`.
8. Run `PRAGMA foreign_key_check`, schema invariant queries, and an FTS insert/query/
   delete probe inside the transaction; any row/result mismatch aborts.
9. Commit, reopen through the ordinary connection factory, and repeat source-ID,
   pragma, checksum, `quick_check`, `foreign_key_check`, and the exact 120-index/
   134-trigger inventories.

Mandatory invariant queries return zero rows for: user/current-membership mismatch
(including null current part with any open membership);
leaderless write-enabled part or inactive/nonmember open leader; invalid owner kind;
item/current-revision/last-curation-transition mismatch outside the explicit
inactive-to-purged lifecycle exception; non-purged revision missing
a body or purged revision retaining one; lineage item/member mismatch; cross-space
pin/edge; evidence revision/item mismatch; illegal/non-enumerated finding tuple or
zero/mixed/partial application proof;
invalid lease null shape; invalid outbox/job claim shape; visible item/search row
mismatch; invalid preview result/null shape; dangling active relation/reference; and
terminal state with mutable claim.

Schema v1 is implementation-ready only when migration-up, empty-DB bootstrap,
rollback-on-failpoint, schema checksum, all constraint probes, and a second-process
open all pass against the exact allowlisted SQLite build.

## 15. Acceptance-schema closure

Migration CI parses backticked acceptance IDs in `TEST_OPERATIONS.md` and requires
exactly 124 occurrences and 124 unique IDs with this frozen group cardinality:

```text
ORG 6   SPACE 6   AUTH 4   SCHEMA 8   CONV 13   PUB 13   REV 2
LEASE 12   LIFE 10   MERGE 4   CUR 11   PROP 5   PROJ 6   JOB 6
MIG 8   OPS 6   REG 3   PERF 1
```

Missing, duplicate, renamed, or additional IDs fail the schema-readiness gate until
this contract and traceability matrix are reviewed together. The eight direct
physical-schema probes map without gaps:

| Acceptance ID | Required v1 probe |
|---|---|
| `SCHEMA-001` | Reject invalid organization parent/kind/cycle, owner-kind shape, current membership, and active leader combinations. |
| `SCHEMA-002` | Reject cross-space conversation pins/turn scope, item current revisions, semantic edges/evidence revisions, and proposal item/revision pairs through composite FKs/triggers. |
| `SCHEMA-003` | Reject revision/audit mutation and body update/delete; allow body deletion only through the eligible administrator purge sequence in section 6.3, whose lifecycle-only pointer change leaves curation transition state untouched. |
| `SCHEMA-004` | Race active-edge/open-deactivation partial unique indexes and the persistent one-row lease/fence invariant. |
| `SCHEMA-005` | At every failpoint, item visibility/current revision and `search_document`/FTS commit or roll back together. |
| `SCHEMA-006` | Probe every illegal lifecycle/curation-event, fully enumerated finding tuple/application-proof, proposal, job, outbox, preview-ready payload, turn, backup, and GC state/null-shape transition. |
| `SCHEMA-007` | Prove referenced actors cannot be deleted and immutable audit display/role snapshots survive account disable and rename. |
| `SCHEMA-008` | Reject any non-allowlisted SQLite source, wrong pragma, non-local/non-NTFS DB or final-target identity, absent FTS5/JSON, or missing/changed required 120-index/134-trigger inventory. |

The schema-linked non-`SCHEMA` closure is also mandatory: `CONV-012..013` exercise
turn idempotency/context/summary; `PUB-003..004,012..013` exercise sync-none dual
publication and durable preview; `LIFE-004..005,009` exercise edge preservation and
body purge; `PROP-001..005` exercise fixed proposal/rebase/apply; `JOB-001..006`
exercise durable execution/effect idempotency/current authorization; `ORG-006` and
`AUTH-001` exercise leader/write gating during account and membership change.
Passing only the eight direct probes is not release acceptance.
