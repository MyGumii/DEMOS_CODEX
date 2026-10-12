"""Pure ordinary-table definitions for canonical schema version 1.

The deployment migration composes these statements with the independently
versioned index, trigger, FTS, seed, and postcondition manifests.  This module
does not open a database and deliberately contains no migration-runner logic.
"""

from __future__ import annotations


TABLE_DEFINITIONS: tuple[tuple[str, str], ...] = (
    (
        "schema_migration",
        """
CREATE TABLE schema_migration(
    version INTEGER PRIMARY KEY CHECK(version >= 1),
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    checksum_sha256 TEXT NOT NULL CHECK(
        length(checksum_sha256)=64
        AND checksum_sha256=lower(checksum_sha256)
        AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    applied_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(applied_at >= 0),
    app_version TEXT NOT NULL CHECK(length(trim(app_version)) BETWEEN 1 AND 80),
    sqlite_version TEXT NOT NULL CHECK(sqlite_version='3.53.4'),
    sqlite_source_id TEXT NOT NULL CHECK(
        sqlite_source_id='2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc'
    )
) STRICT
""".strip(),
    ),
    (
        "org_unit",
        """
CREATE TABLE org_unit(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    kind TEXT NOT NULL CHECK(kind IN ('responsibility','team','part')),
    parent_id TEXT CHECK(
        parent_id IS NULL OR (
            length(parent_id)=36 AND substr(parent_id,9,1)='-'
            AND substr(parent_id,14,1)='-' AND substr(parent_id,19,1)='-'
            AND substr(parent_id,24,1)='-'
            AND length(replace(parent_id,'-',''))=32
            AND replace(parent_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND parent_id=lower(parent_id)
        )
    ) REFERENCES org_unit(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    name_key TEXT NOT NULL CHECK(length(trim(name_key)) BETWEEN 1 AND 160),
    status TEXT NOT NULL CHECK(status IN ('active','inactive')),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    created_by TEXT CHECK(
        created_by IS NULL OR (
            length(created_by) BETWEEN 1 AND 64
            AND created_by=lower(trim(created_by))
            AND created_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    updated_by TEXT CHECK(
        updated_by IS NULL OR (
            length(updated_by) BETWEEN 1 AND 64
            AND updated_by=lower(trim(updated_by))
            AND updated_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1)
) STRICT
""".strip(),
    ),
    (
        "app_user",
        """
CREATE TABLE app_user(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id) BETWEEN 1 AND 64 AND id=lower(trim(id))
        AND id NOT GLOB '*[^a-z0-9._-]*'
    ),
    display_name TEXT NOT NULL CHECK(length(trim(display_name)) BETWEEN 1 AND 120),
    password_hash TEXT NOT NULL CHECK(length(trim(password_hash)) BETWEEN 1 AND 512),
    system_role TEXT NOT NULL CHECK(system_role IN ('admin','user')),
    account_status TEXT NOT NULL CHECK(account_status IN ('active','disabled')),
    current_part_id TEXT CHECK(
        current_part_id IS NULL OR (
            length(current_part_id)=36 AND substr(current_part_id,9,1)='-'
            AND substr(current_part_id,14,1)='-' AND substr(current_part_id,19,1)='-'
            AND substr(current_part_id,24,1)='-'
            AND length(replace(current_part_id,'-',''))=32
            AND replace(current_part_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND current_part_id=lower(current_part_id)
        )
    ) REFERENCES org_unit(id) ON DELETE RESTRICT,
    scope_epoch INTEGER NOT NULL DEFAULT 1 CHECK(scope_epoch >= 1),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    disabled_at INTEGER CHECK(disabled_at IS NULL OR disabled_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK((account_status='disabled') = (disabled_at IS NOT NULL))
) STRICT
""".strip(),
    ),
    (
        "org_membership",
        """
CREATE TABLE org_membership(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    user_id TEXT NOT NULL CHECK(
        length(user_id) BETWEEN 1 AND 64 AND user_id=lower(trim(user_id))
        AND user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    part_id TEXT NOT NULL CHECK(
        length(part_id)=36 AND substr(part_id,9,1)='-'
        AND substr(part_id,14,1)='-' AND substr(part_id,19,1)='-'
        AND substr(part_id,24,1)='-'
        AND length(replace(part_id,'-',''))=32
        AND replace(part_id,'-','') NOT GLOB '*[^0-9a-f]*' AND part_id=lower(part_id)
    ) REFERENCES org_unit(id) ON DELETE RESTRICT,
    valid_from INTEGER NOT NULL CHECK(valid_from >= 0),
    valid_to INTEGER CHECK(valid_to IS NULL OR valid_to > valid_from),
    assigned_by TEXT NOT NULL CHECK(
        length(assigned_by) BETWEEN 1 AND 64 AND assigned_by=lower(trim(assigned_by))
        AND assigned_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000)
) STRICT
""".strip(),
    ),
    (
        "org_role_assignment",
        """
CREATE TABLE org_role_assignment(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    user_id TEXT NOT NULL CHECK(
        length(user_id) BETWEEN 1 AND 64 AND user_id=lower(trim(user_id))
        AND user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    org_unit_id TEXT NOT NULL CHECK(
        length(org_unit_id)=36 AND substr(org_unit_id,9,1)='-'
        AND substr(org_unit_id,14,1)='-' AND substr(org_unit_id,19,1)='-'
        AND substr(org_unit_id,24,1)='-'
        AND length(replace(org_unit_id,'-',''))=32
        AND replace(org_unit_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND org_unit_id=lower(org_unit_id)
    ) REFERENCES org_unit(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK(role='part_leader'),
    valid_from INTEGER NOT NULL CHECK(valid_from >= 0),
    valid_to INTEGER CHECK(valid_to IS NULL OR valid_to > valid_from),
    assigned_by TEXT NOT NULL CHECK(
        length(assigned_by) BETWEEN 1 AND 64 AND assigned_by=lower(trim(assigned_by))
        AND assigned_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 1000)
) STRICT
""".strip(),
    ),
    (
        "knowledge_space",
        """
CREATE TABLE knowledge_space(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    space_kind TEXT NOT NULL CHECK(space_kind IN ('personal','part','team','responsibility')),
    owner_user_id TEXT CHECK(
        owner_user_id IS NULL OR (
            length(owner_user_id) BETWEEN 1 AND 64
            AND owner_user_id=lower(trim(owner_user_id))
            AND owner_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    owner_org_unit_id TEXT CHECK(
        owner_org_unit_id IS NULL OR (
            length(owner_org_unit_id)=36 AND substr(owner_org_unit_id,9,1)='-'
            AND substr(owner_org_unit_id,14,1)='-' AND substr(owner_org_unit_id,19,1)='-'
            AND substr(owner_org_unit_id,24,1)='-'
            AND length(replace(owner_org_unit_id,'-',''))=32
            AND replace(owner_org_unit_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND owner_org_unit_id=lower(owner_org_unit_id)
        )
    ) REFERENCES org_unit(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('active','frozen','inactive')),
    write_enabled INTEGER NOT NULL DEFAULT 0 CHECK(write_enabled IN (0,1)),
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK(
        (space_kind='personal' AND owner_user_id IS NOT NULL AND owner_org_unit_id IS NULL)
        OR
        (space_kind<>'personal' AND owner_user_id IS NULL AND owner_org_unit_id IS NOT NULL)
    ),
    CHECK(write_enabled=0 OR status='active'),
    CHECK(space_kind IN ('personal','part') OR write_enabled=0)
) STRICT
""".strip(),
    ),
    (
        "conversation",
        """
CREATE TABLE conversation(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    owner_user_id TEXT NOT NULL CHECK(
        length(owner_user_id) BETWEEN 1 AND 64
        AND owner_user_id=lower(trim(owner_user_id))
        AND owner_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    captured_scope_epoch INTEGER NOT NULL CHECK(captured_scope_epoch >= 1),
    title TEXT NOT NULL DEFAULT '' CHECK(length(title) <= 200),
    status TEXT NOT NULL CHECK(status IN ('active','archived')),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(metadata_json) AND json_type(metadata_json)='object'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1)
) STRICT
""".strip(),
    ),
    (
        "conversation_message",
        """
CREATE TABLE conversation_message(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    conversation_id TEXT NOT NULL CHECK(
        length(conversation_id)=36 AND substr(conversation_id,9,1)='-'
        AND substr(conversation_id,14,1)='-' AND substr(conversation_id,19,1)='-'
        AND substr(conversation_id,24,1)='-'
        AND length(replace(conversation_id,'-',''))=32
        AND replace(conversation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND conversation_id=lower(conversation_id)
    ) REFERENCES conversation(id) ON DELETE RESTRICT,
    sequence_no INTEGER NOT NULL CHECK(sequence_no >= 1),
    role TEXT NOT NULL CHECK(role IN ('system','user','assistant','tool')),
    content_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(content_json) AND json_type(content_json)='object'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0)
) STRICT
""".strip(),
    ),
    (
        "conversation_turn",
        """
CREATE TABLE conversation_turn(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    conversation_id TEXT NOT NULL CHECK(
        length(conversation_id)=36 AND substr(conversation_id,9,1)='-'
        AND substr(conversation_id,14,1)='-' AND substr(conversation_id,19,1)='-'
        AND substr(conversation_id,24,1)='-'
        AND length(replace(conversation_id,'-',''))=32
        AND replace(conversation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND conversation_id=lower(conversation_id)
    ) REFERENCES conversation(id) ON DELETE RESTRICT,
    actor_user_id TEXT NOT NULL CHECK(
        length(actor_user_id) BETWEEN 1 AND 64
        AND actor_user_id=lower(trim(actor_user_id))
        AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    dedup_id TEXT NOT NULL CHECK(
        length(dedup_id)=36 AND substr(dedup_id,9,1)='-'
        AND substr(dedup_id,14,1)='-' AND substr(dedup_id,19,1)='-'
        AND substr(dedup_id,24,1)='-'
        AND length(replace(dedup_id,'-',''))=32
        AND replace(dedup_id,'-','') NOT GLOB '*[^0-9a-f]*' AND dedup_id=lower(dedup_id)
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK(
        length(operation_id)=36 AND substr(operation_id,9,1)='-'
        AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-'
        AND substr(operation_id,24,1)='-'
        AND length(replace(operation_id,'-',''))=32
        AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND operation_id=lower(operation_id)
    ),
    request_sha256 TEXT NOT NULL CHECK(
        length(request_sha256)=64 AND request_sha256=lower(request_sha256)
        AND request_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK(state IN ('accepted','running','completed','failed','cancelled')),
    user_message_id TEXT NOT NULL CHECK(
        length(user_message_id)=36 AND substr(user_message_id,9,1)='-'
        AND substr(user_message_id,14,1)='-' AND substr(user_message_id,19,1)='-'
        AND substr(user_message_id,24,1)='-'
        AND length(replace(user_message_id,'-',''))=32
        AND replace(user_message_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND user_message_id=lower(user_message_id)
    ),
    assistant_message_id TEXT CHECK(
        assistant_message_id IS NULL OR (
            length(assistant_message_id)=36 AND substr(assistant_message_id,9,1)='-'
            AND substr(assistant_message_id,14,1)='-'
            AND substr(assistant_message_id,19,1)='-'
            AND substr(assistant_message_id,24,1)='-'
            AND length(replace(assistant_message_id,'-',''))=32
            AND replace(assistant_message_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND assistant_message_id=lower(assistant_message_id)
        )
    ),
    final_response_json TEXT CHECK(
        final_response_json IS NULL OR (
            json_valid(final_response_json) AND json_type(final_response_json)='object'
        )
    ),
    error_json TEXT CHECK(
        error_json IS NULL OR (json_valid(error_json) AND json_type(error_json)='object')
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(user_message_id,conversation_id)
        REFERENCES conversation_message(id,conversation_id) ON DELETE RESTRICT,
    FOREIGN KEY(assistant_message_id,conversation_id)
        REFERENCES conversation_message(id,conversation_id) ON DELETE RESTRICT,
    CHECK(
        (state IN ('accepted','running') AND assistant_message_id IS NULL
            AND final_response_json IS NULL AND error_json IS NULL AND completed_at IS NULL)
        OR
        (state='completed' AND assistant_message_id IS NOT NULL
            AND final_response_json IS NOT NULL AND error_json IS NULL AND completed_at IS NOT NULL)
        OR
        (state='failed' AND assistant_message_id IS NULL
            AND final_response_json IS NULL AND error_json IS NOT NULL AND completed_at IS NOT NULL)
        OR
        (state='cancelled' AND final_response_json IS NULL
            AND error_json IS NULL AND completed_at IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "conversation_turn_event",
        """
CREATE TABLE conversation_turn_event(
    turn_id TEXT NOT NULL CHECK(
        length(turn_id)=36 AND substr(turn_id,9,1)='-' AND substr(turn_id,14,1)='-'
        AND substr(turn_id,19,1)='-' AND substr(turn_id,24,1)='-'
        AND length(replace(turn_id,'-',''))=32
        AND replace(turn_id,'-','') NOT GLOB '*[^0-9a-f]*' AND turn_id=lower(turn_id)
    ),
    conversation_id TEXT NOT NULL CHECK(
        length(conversation_id)=36 AND substr(conversation_id,9,1)='-'
        AND substr(conversation_id,14,1)='-' AND substr(conversation_id,19,1)='-'
        AND substr(conversation_id,24,1)='-'
        AND length(replace(conversation_id,'-',''))=32
        AND replace(conversation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND conversation_id=lower(conversation_id)
    ),
    sequence_no INTEGER NOT NULL CHECK(sequence_no >= 1),
    event_type TEXT NOT NULL CHECK(event_type IN ('accepted','delta','tool','completed','failed','cancelled')),
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(payload_json) AND json_type(payload_json)='object'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    PRIMARY KEY(turn_id,sequence_no),
    FOREIGN KEY(turn_id,conversation_id)
        REFERENCES conversation_turn(id,conversation_id) ON DELETE RESTRICT
) STRICT
""".strip(),
    ),
    (
        "conversation_context_binding",
        """
CREATE TABLE conversation_context_binding(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    conversation_id TEXT NOT NULL CHECK(
        length(conversation_id)=36 AND substr(conversation_id,9,1)='-'
        AND substr(conversation_id,14,1)='-' AND substr(conversation_id,19,1)='-'
        AND substr(conversation_id,24,1)='-'
        AND length(replace(conversation_id,'-',''))=32
        AND replace(conversation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND conversation_id=lower(conversation_id)
    ) REFERENCES conversation(id) ON DELETE RESTRICT,
    binding_type TEXT NOT NULL CHECK(binding_type IN ('workspace_file','project_context','notion_page','source_artifact')),
    binding_mode TEXT NOT NULL CHECK(binding_mode IN ('snapshot','live')),
    external_key TEXT NOT NULL CHECK(length(trim(external_key)) BETWEEN 1 AND 1000),
    source_artifact_id TEXT CHECK(
        source_artifact_id IS NULL OR (
            length(source_artifact_id)=36 AND substr(source_artifact_id,9,1)='-'
            AND substr(source_artifact_id,14,1)='-' AND substr(source_artifact_id,19,1)='-'
            AND substr(source_artifact_id,24,1)='-'
            AND length(replace(source_artifact_id,'-',''))=32
            AND replace(source_artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND source_artifact_id=lower(source_artifact_id)
        )
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    snapshot_sha256 TEXT CHECK(
        snapshot_sha256 IS NULL OR (
            length(snapshot_sha256)=64 AND snapshot_sha256=lower(snapshot_sha256)
            AND snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    display_metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(display_metadata_json) AND json_type(display_metadata_json)='object'
    ),
    status TEXT NOT NULL CHECK(status IN ('active','unavailable','detached')),
    status_reason TEXT CHECK(
        status_reason IS NULL OR length(trim(status_reason)) BETWEEN 1 AND 1000
    ),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    status_changed_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(status_changed_at >= 0),
    detached_at INTEGER CHECK(detached_at IS NULL OR detached_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK(
        (binding_mode='snapshot' AND source_artifact_id IS NOT NULL AND snapshot_sha256 IS NOT NULL)
        OR
        (binding_mode='live' AND source_artifact_id IS NULL AND snapshot_sha256 IS NULL)
    ),
    CHECK(binding_type<>'source_artifact' OR binding_mode='snapshot'),
    CHECK(
        (status='active' AND status_reason IS NULL AND detached_at IS NULL)
        OR
        (status='unavailable' AND binding_mode='live' AND status_reason IS NOT NULL AND detached_at IS NULL)
        OR
        (status='detached' AND detached_at IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "conversation_summary",
        """
CREATE TABLE conversation_summary(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    conversation_id TEXT NOT NULL CHECK(
        length(conversation_id)=36 AND substr(conversation_id,9,1)='-'
        AND substr(conversation_id,14,1)='-' AND substr(conversation_id,19,1)='-'
        AND substr(conversation_id,24,1)='-'
        AND length(replace(conversation_id,'-',''))=32
        AND replace(conversation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND conversation_id=lower(conversation_id)
    ) REFERENCES conversation(id) ON DELETE RESTRICT,
    covered_through_sequence_no INTEGER NOT NULL CHECK(covered_through_sequence_no >= 1),
    summary_md TEXT NOT NULL CHECK(length(trim(summary_md)) >= 1),
    summary_sha256 TEXT NOT NULL CHECK(
        length(summary_sha256)=64 AND summary_sha256=lower(summary_sha256)
        AND summary_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    model_id TEXT NOT NULL CHECK(length(trim(model_id)) BETWEEN 1 AND 200),
    prompt_version TEXT NOT NULL CHECK(length(trim(prompt_version)) BETWEEN 1 AND 120),
    summarizer_version TEXT NOT NULL CHECK(length(trim(summarizer_version)) BETWEEN 1 AND 120),
    status TEXT NOT NULL CHECK(status IN ('active','invalidated')),
    created_by_user_id TEXT CHECK(
        created_by_user_id IS NULL OR (
            length(created_by_user_id) BETWEEN 1 AND 64
            AND created_by_user_id=lower(trim(created_by_user_id))
            AND created_by_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    invalidated_at INTEGER CHECK(invalidated_at IS NULL OR invalidated_at >= 0),
    invalidation_reason TEXT CHECK(
        invalidation_reason IS NULL OR length(trim(invalidation_reason)) BETWEEN 1 AND 1000
    ),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK(
        (status='active' AND invalidated_at IS NULL AND invalidation_reason IS NULL)
        OR
        (status='invalidated' AND invalidated_at IS NOT NULL AND invalidation_reason IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "conversation_pin",
        """
CREATE TABLE conversation_pin(
    conversation_id TEXT NOT NULL CHECK(
        length(conversation_id)=36 AND substr(conversation_id,9,1)='-'
        AND substr(conversation_id,14,1)='-' AND substr(conversation_id,19,1)='-'
        AND substr(conversation_id,24,1)='-'
        AND length(replace(conversation_id,'-',''))=32
        AND replace(conversation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND conversation_id=lower(conversation_id)
    ),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ),
    revision_id TEXT CHECK(
        revision_id IS NULL OR (
            length(revision_id)=36 AND substr(revision_id,9,1)='-'
            AND substr(revision_id,14,1)='-' AND substr(revision_id,19,1)='-'
            AND substr(revision_id,24,1)='-'
            AND length(replace(revision_id,'-',''))=32
            AND replace(revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND revision_id=lower(revision_id)
        )
    ),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    PRIMARY KEY(conversation_id,item_id),
    FOREIGN KEY(conversation_id,space_id)
        REFERENCES conversation(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT
) STRICT
""".strip(),
    ),
    (
        "blob_object",
        """
CREATE TABLE blob_object(
    sha256 TEXT PRIMARY KEY NOT NULL CHECK(
        length(sha256)=64 AND sha256=lower(sha256)
        AND sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    media_type TEXT NOT NULL CHECK(length(trim(media_type)) BETWEEN 1 AND 255),
    relative_path TEXT NOT NULL CHECK(
        length(trim(relative_path)) BETWEEN 1 AND 500
        AND relative_path=substr(sha256,1,2)||'/'||substr(sha256,3,2)||'/'||sha256
    ),
    verification_status TEXT NOT NULL CHECK(verification_status IN ('pending','verified','corrupt','missing')),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    verified_at INTEGER CHECK(verified_at IS NULL OR verified_at >= 0)
) STRICT
""".strip(),
    ),
    (
        "source_artifact",
        """
CREATE TABLE source_artifact(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    origin_type TEXT NOT NULL CHECK(origin_type IN ('upload','notion','chat','manual','legacy')),
    origin_key TEXT CHECK(origin_key IS NULL OR length(trim(origin_key)) BETWEEN 1 AND 1000),
    origin_revision_key TEXT CHECK(
        origin_revision_key IS NULL OR length(trim(origin_revision_key)) BETWEEN 1 AND 500
    ),
    raw_sha256 TEXT NOT NULL CHECK(
        length(raw_sha256)=64 AND raw_sha256=lower(raw_sha256)
        AND raw_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    raw_size_bytes INTEGER NOT NULL CHECK(raw_size_bytes >= 0),
    media_type TEXT NOT NULL CHECK(length(trim(media_type)) BETWEEN 1 AND 255),
    original_filename TEXT CHECK(
        original_filename IS NULL OR length(trim(original_filename)) BETWEEN 1 AND 500
    ),
    body_retention_status TEXT NOT NULL DEFAULT 'present'
        CHECK(body_retention_status IN ('present','purged')),
    body_retention_reason TEXT CHECK(
        body_retention_reason IS NULL
        OR body_retention_reason IN ('eligible_item_purge','orphan_ingestion_cleanup')
    ),
    body_purged_at INTEGER CHECK(body_purged_at IS NULL OR body_purged_at >= 0),
    orphan_cleanup_eligible_at INTEGER NOT NULL CHECK(orphan_cleanup_eligible_at >= 0),
    publisher_user_id TEXT NOT NULL CHECK(
        length(publisher_user_id) BETWEEN 1 AND 64
        AND publisher_user_id=lower(trim(publisher_user_id))
        AND publisher_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    source_owner_snapshot_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(source_owner_snapshot_json) AND json_type(source_owner_snapshot_json)='object'
    ),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(metadata_json) AND json_type(metadata_json)='object'
    ),
    metadata_schema_version INTEGER NOT NULL DEFAULT 1 CHECK(metadata_schema_version >= 1),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    CHECK(orphan_cleanup_eligible_at=created_at+2592000000),
    CHECK(
        (body_retention_status='present' AND body_retention_reason IS NULL AND body_purged_at IS NULL)
        OR
        (body_retention_status='purged' AND body_retention_reason IS NOT NULL AND body_purged_at IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "artifact_blob",
        """
CREATE TABLE artifact_blob(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    artifact_id TEXT NOT NULL CHECK(
        length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
        AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
        AND substr(artifact_id,24,1)='-'
        AND length(replace(artifact_id,'-',''))=32
        AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_id=lower(artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    content_sha256 TEXT NOT NULL CHECK(
        length(content_sha256)=64 AND content_sha256=lower(content_sha256)
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    role TEXT NOT NULL CHECK(role IN ('raw','embedded_asset','attachment')),
    logical_name TEXT NOT NULL CHECK(length(trim(logical_name)) BETWEEN 1 AND 500),
    media_type TEXT NOT NULL CHECK(length(trim(media_type)) BETWEEN 1 AND 255),
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
    body_retention_status TEXT NOT NULL DEFAULT 'present'
        CHECK(body_retention_status IN ('present','purged')),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    CHECK(role<>'raw' OR ordinal=0)
) STRICT
""".strip(),
    ),
    (
        "artifact_blob_body",
        """
CREATE TABLE artifact_blob_body(
    artifact_blob_id TEXT PRIMARY KEY NOT NULL CHECK(
        length(artifact_blob_id)=36 AND substr(artifact_blob_id,9,1)='-'
        AND substr(artifact_blob_id,14,1)='-' AND substr(artifact_blob_id,19,1)='-'
        AND substr(artifact_blob_id,24,1)='-'
        AND length(replace(artifact_blob_id,'-',''))=32
        AND replace(artifact_blob_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_blob_id=lower(artifact_blob_id)
    ) REFERENCES artifact_blob(id) ON DELETE RESTRICT,
    content_sha256 TEXT NOT NULL CHECK(
        length(content_sha256)=64 AND content_sha256=lower(content_sha256)
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ) REFERENCES blob_object(sha256) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(artifact_blob_id,content_sha256)
        REFERENCES artifact_blob(id,content_sha256) ON DELETE RESTRICT
) STRICT
""".strip(),
    ),
    (
        "extraction_snapshot",
        """
CREATE TABLE extraction_snapshot(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    artifact_id TEXT NOT NULL CHECK(
        length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
        AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
        AND substr(artifact_id,24,1)='-'
        AND length(replace(artifact_id,'-',''))=32
        AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_id=lower(artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('ready','failed')),
    extracted_sha256 TEXT CHECK(
        extracted_sha256 IS NULL OR (
            length(extracted_sha256)=64 AND extracted_sha256=lower(extracted_sha256)
            AND extracted_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    normalized_content_sha256 TEXT CHECK(
        normalized_content_sha256 IS NULL OR (
            length(normalized_content_sha256)=64
            AND normalized_content_sha256=lower(normalized_content_sha256)
            AND normalized_content_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    extracted_size_bytes INTEGER CHECK(
        extracted_size_bytes IS NULL OR extracted_size_bytes >= 0
    ),
    normalizer_version TEXT CHECK(
        normalizer_version IS NULL OR length(trim(normalizer_version)) BETWEEN 1 AND 80
    ),
    extractor_name TEXT NOT NULL CHECK(length(trim(extractor_name)) BETWEEN 1 AND 120),
    extractor_version TEXT NOT NULL CHECK(length(trim(extractor_version)) BETWEEN 1 AND 120),
    warnings_json TEXT NOT NULL DEFAULT '[]' CHECK(
        json_valid(warnings_json) AND json_type(warnings_json)='array'
    ),
    body_retention_status TEXT NOT NULL CHECK(body_retention_status IN ('present','purged','none')),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    CHECK(
        (status='ready' AND extracted_sha256 IS NOT NULL
            AND normalized_content_sha256 IS NOT NULL AND extracted_size_bytes IS NOT NULL
            AND normalizer_version IS NOT NULL AND body_retention_status IN ('present','purged'))
        OR
        (status='failed' AND extracted_sha256 IS NULL
            AND normalized_content_sha256 IS NULL AND extracted_size_bytes IS NULL
            AND normalizer_version IS NULL AND body_retention_status='none')
    )
) STRICT
""".strip(),
    ),
    (
        "extraction_snapshot_body",
        """
CREATE TABLE extraction_snapshot_body(
    snapshot_id TEXT NOT NULL CHECK(
        length(snapshot_id)=36 AND substr(snapshot_id,9,1)='-'
        AND substr(snapshot_id,14,1)='-' AND substr(snapshot_id,19,1)='-'
        AND substr(snapshot_id,24,1)='-'
        AND length(replace(snapshot_id,'-',''))=32
        AND replace(snapshot_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND snapshot_id=lower(snapshot_id)
    ),
    artifact_id TEXT NOT NULL CHECK(
        length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
        AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
        AND substr(artifact_id,24,1)='-'
        AND length(replace(artifact_id,'-',''))=32
        AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_id=lower(artifact_id)
    ),
    content_md TEXT,
    content_blob_hash TEXT CHECK(
        content_blob_hash IS NULL OR (
            length(content_blob_hash)=64 AND content_blob_hash=lower(content_blob_hash)
            AND content_blob_hash NOT GLOB '*[^0-9a-f]*'
        )
    ) REFERENCES blob_object(sha256) ON DELETE RESTRICT,
    content_size_bytes INTEGER NOT NULL CHECK(content_size_bytes >= 0),
    content_sha256 TEXT NOT NULL CHECK(
        length(content_sha256)=64 AND content_sha256=lower(content_sha256)
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    PRIMARY KEY(snapshot_id,artifact_id),
    FOREIGN KEY(snapshot_id,artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT,
    CHECK((content_md IS NOT NULL) <> (content_blob_hash IS NOT NULL))
) STRICT
""".strip(),
    ),
    (
        "publication_preview",
        """
CREATE TABLE publication_preview(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    actor_user_id TEXT NOT NULL CHECK(
        length(actor_user_id) BETWEEN 1 AND 64
        AND actor_user_id=lower(trim(actor_user_id))
        AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    artifact_id TEXT NOT NULL CHECK(
        length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
        AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
        AND substr(artifact_id,24,1)='-'
        AND length(replace(artifact_id,'-',''))=32
        AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_id=lower(artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    extraction_snapshot_id TEXT CHECK(
        extraction_snapshot_id IS NULL OR (
            length(extraction_snapshot_id)=36 AND substr(extraction_snapshot_id,9,1)='-'
            AND substr(extraction_snapshot_id,14,1)='-'
            AND substr(extraction_snapshot_id,19,1)='-'
            AND substr(extraction_snapshot_id,24,1)='-'
            AND length(replace(extraction_snapshot_id,'-',''))=32
            AND replace(extraction_snapshot_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND extraction_snapshot_id=lower(extraction_snapshot_id)
        )
    ),
    durable_job_id TEXT CHECK(
        durable_job_id IS NULL OR (
            length(durable_job_id)=36 AND substr(durable_job_id,9,1)='-'
            AND substr(durable_job_id,14,1)='-' AND substr(durable_job_id,19,1)='-'
            AND substr(durable_job_id,24,1)='-'
            AND length(replace(durable_job_id,'-',''))=32
            AND replace(durable_job_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND durable_job_id=lower(durable_job_id)
        )
    ) REFERENCES durable_job(id) ON DELETE RESTRICT,
    source_snapshot_sha256 TEXT CHECK(
        source_snapshot_sha256 IS NULL OR (
            length(source_snapshot_sha256)=64
            AND source_snapshot_sha256=lower(source_snapshot_sha256)
            AND source_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    captured_part_id TEXT CHECK(
        captured_part_id IS NULL OR (
            length(captured_part_id)=36 AND substr(captured_part_id,9,1)='-'
            AND substr(captured_part_id,14,1)='-' AND substr(captured_part_id,19,1)='-'
            AND substr(captured_part_id,24,1)='-'
            AND length(replace(captured_part_id,'-',''))=32
            AND replace(captured_part_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND captured_part_id=lower(captured_part_id)
        )
    ) REFERENCES org_unit(id) ON DELETE RESTRICT,
    captured_scope_epoch INTEGER NOT NULL CHECK(captured_scope_epoch >= 1),
    available_targets_json TEXT NOT NULL DEFAULT '[]' CHECK(
        json_valid(available_targets_json) AND json_type(available_targets_json)='array'
    ),
    rule_version TEXT NOT NULL CHECK(length(trim(rule_version)) BETWEEN 1 AND 120),
    deterministic_findings_json TEXT CHECK(
        deterministic_findings_json IS NULL OR (
            json_valid(deterministic_findings_json)
            AND json_type(deterministic_findings_json)='array'
        )
    ),
    ready_result_json TEXT CHECK(
        ready_result_json IS NULL OR (
            json_valid(ready_result_json) AND json_type(ready_result_json)='object'
        )
    ),
    result_schema_version INTEGER NOT NULL DEFAULT 1 CHECK(result_schema_version=1),
    status TEXT NOT NULL CHECK(status IN ('queued','extracting','ready','consumed','expired','cancelled','failed')),
    error_json TEXT CHECK(
        error_json IS NULL OR (json_valid(error_json) AND json_type(error_json)='object')
    ),
    expires_at INTEGER NOT NULL CHECK(expires_at >= 0),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(extraction_snapshot_id,artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT,
    CHECK(
        (status IN ('queued','extracting') AND durable_job_id IS NOT NULL
            AND extraction_snapshot_id IS NULL AND source_snapshot_sha256 IS NULL
            AND deterministic_findings_json IS NULL AND ready_result_json IS NULL
            AND error_json IS NULL AND completed_at IS NULL)
        OR
        (status IN ('ready','consumed','expired')
            AND extraction_snapshot_id IS NOT NULL AND source_snapshot_sha256 IS NOT NULL
            AND deterministic_findings_json IS NOT NULL AND ready_result_json IS NOT NULL
            AND error_json IS NULL AND completed_at IS NOT NULL)
        OR
        (status='failed' AND durable_job_id IS NOT NULL
            AND extraction_snapshot_id IS NULL AND source_snapshot_sha256 IS NULL
            AND deterministic_findings_json IS NULL AND ready_result_json IS NULL
            AND error_json IS NOT NULL AND completed_at IS NOT NULL)
        OR
        (status='cancelled' AND completed_at IS NOT NULL AND error_json IS NULL
            AND (
                (durable_job_id IS NOT NULL AND extraction_snapshot_id IS NULL
                    AND source_snapshot_sha256 IS NULL
                    AND deterministic_findings_json IS NULL AND ready_result_json IS NULL)
                OR
                (extraction_snapshot_id IS NOT NULL AND source_snapshot_sha256 IS NOT NULL
                    AND deterministic_findings_json IS NOT NULL AND ready_result_json IS NOT NULL)
            ))
    )
) STRICT
""".strip(),
    ),
    (
        "lineage_group",
        """
CREATE TABLE lineage_group(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    initial_artifact_id TEXT NOT NULL CHECK(
        length(initial_artifact_id)=36 AND substr(initial_artifact_id,9,1)='-'
        AND substr(initial_artifact_id,14,1)='-' AND substr(initial_artifact_id,19,1)='-'
        AND substr(initial_artifact_id,24,1)='-'
        AND length(replace(initial_artifact_id,'-',''))=32
        AND replace(initial_artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND initial_artifact_id=lower(initial_artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    base_artifact_id TEXT NOT NULL CHECK(
        length(base_artifact_id)=36 AND substr(base_artifact_id,9,1)='-'
        AND substr(base_artifact_id,14,1)='-' AND substr(base_artifact_id,19,1)='-'
        AND substr(base_artifact_id,24,1)='-'
        AND length(replace(base_artifact_id,'-',''))=32
        AND replace(base_artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND base_artifact_id=lower(base_artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    base_extraction_snapshot_id TEXT NOT NULL CHECK(
        length(base_extraction_snapshot_id)=36
        AND substr(base_extraction_snapshot_id,9,1)='-'
        AND substr(base_extraction_snapshot_id,14,1)='-'
        AND substr(base_extraction_snapshot_id,19,1)='-'
        AND substr(base_extraction_snapshot_id,24,1)='-'
        AND length(replace(base_extraction_snapshot_id,'-',''))=32
        AND replace(base_extraction_snapshot_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND base_extraction_snapshot_id=lower(base_extraction_snapshot_id)
    ),
    base_content_sha256 TEXT NOT NULL CHECK(
        length(base_content_sha256)=64 AND base_content_sha256=lower(base_content_sha256)
        AND base_content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    sync_policy TEXT NOT NULL DEFAULT 'none' CHECK(sync_policy='none'),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(base_extraction_snapshot_id,base_artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT
) STRICT
""".strip(),
    ),
    (
        "lineage_member",
        """
CREATE TABLE lineage_member(
    lineage_group_id TEXT NOT NULL CHECK(
        length(lineage_group_id)=36 AND substr(lineage_group_id,9,1)='-'
        AND substr(lineage_group_id,14,1)='-' AND substr(lineage_group_id,19,1)='-'
        AND substr(lineage_group_id,24,1)='-'
        AND length(replace(lineage_group_id,'-',''))=32
        AND replace(lineage_group_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND lineage_group_id=lower(lineage_group_id)
    ) REFERENCES lineage_group(id) ON DELETE RESTRICT,
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ) REFERENCES knowledge_item(id) ON DELETE RESTRICT,
    initial_revision_id TEXT NOT NULL CHECK(
        length(initial_revision_id)=36 AND substr(initial_revision_id,9,1)='-'
        AND substr(initial_revision_id,14,1)='-' AND substr(initial_revision_id,19,1)='-'
        AND substr(initial_revision_id,24,1)='-'
        AND length(replace(initial_revision_id,'-',''))=32
        AND replace(initial_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND initial_revision_id=lower(initial_revision_id)
    ),
    derived_from_item_id TEXT CHECK(
        derived_from_item_id IS NULL OR (
            length(derived_from_item_id)=36 AND substr(derived_from_item_id,9,1)='-'
            AND substr(derived_from_item_id,14,1)='-' AND substr(derived_from_item_id,19,1)='-'
            AND substr(derived_from_item_id,24,1)='-'
            AND length(replace(derived_from_item_id,'-',''))=32
            AND replace(derived_from_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND derived_from_item_id=lower(derived_from_item_id)
        )
    ),
    derived_from_revision_id TEXT CHECK(
        derived_from_revision_id IS NULL OR (
            length(derived_from_revision_id)=36 AND substr(derived_from_revision_id,9,1)='-'
            AND substr(derived_from_revision_id,14,1)='-'
            AND substr(derived_from_revision_id,19,1)='-'
            AND substr(derived_from_revision_id,24,1)='-'
            AND length(replace(derived_from_revision_id,'-',''))=32
            AND replace(derived_from_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND derived_from_revision_id=lower(derived_from_revision_id)
        )
    ),
    member_role TEXT NOT NULL CHECK(member_role IN ('origin','personal_copy','part_copy','promoted_copy','team_copy','responsibility_copy')),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    PRIMARY KEY(lineage_group_id,item_id),
    FOREIGN KEY(initial_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(derived_from_revision_id,derived_from_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK((derived_from_item_id IS NULL) = (derived_from_revision_id IS NULL)),
    CHECK((member_role='origin') = (derived_from_item_id IS NULL)),
    CHECK(derived_from_item_id IS NULL OR derived_from_item_id<>item_id)
) STRICT
""".strip(),
    ),
    (
        "operation_dedup",
        """
CREATE TABLE operation_dedup(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    actor_user_id TEXT NOT NULL CHECK(
        length(actor_user_id) BETWEEN 1 AND 64
        AND actor_user_id=lower(trim(actor_user_id))
        AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    operation_type TEXT NOT NULL CHECK(length(trim(operation_type)) BETWEEN 1 AND 80),
    operation_id TEXT NOT NULL CHECK(
        length(operation_id)=36 AND substr(operation_id,9,1)='-'
        AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-'
        AND substr(operation_id,24,1)='-'
        AND length(replace(operation_id,'-',''))=32
        AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND operation_id=lower(operation_id)
    ),
    request_sha256 TEXT NOT NULL CHECK(
        length(request_sha256)=64 AND request_sha256=lower(request_sha256)
        AND request_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK(status IN ('in_progress','committed','failed_final')),
    http_status INTEGER CHECK(http_status IS NULL OR http_status BETWEEN 100 AND 599),
    result_json TEXT CHECK(
        result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')
    ),
    error_json TEXT CHECK(
        error_json IS NULL OR (json_valid(error_json) AND json_type(error_json)='object')
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at >= 0),
    last_seen_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(last_seen_at >= 0),
    CHECK(
        (status='in_progress' AND http_status IS NULL AND result_json IS NULL
            AND error_json IS NULL AND completed_at IS NULL)
        OR
        (status='committed' AND http_status IS NOT NULL AND result_json IS NOT NULL
            AND error_json IS NULL AND completed_at IS NOT NULL)
        OR
        (status='failed_final' AND http_status IS NOT NULL AND result_json IS NULL
            AND error_json IS NOT NULL AND completed_at IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "publication_operation",
        """
CREATE TABLE publication_operation(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    dedup_id TEXT NOT NULL CHECK(
        length(dedup_id)=36 AND substr(dedup_id,9,1)='-'
        AND substr(dedup_id,14,1)='-' AND substr(dedup_id,19,1)='-'
        AND substr(dedup_id,24,1)='-'
        AND length(replace(dedup_id,'-',''))=32
        AND replace(dedup_id,'-','') NOT GLOB '*[^0-9a-f]*' AND dedup_id=lower(dedup_id)
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    actor_user_id TEXT NOT NULL CHECK(
        length(actor_user_id) BETWEEN 1 AND 64
        AND actor_user_id=lower(trim(actor_user_id))
        AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    operation_id TEXT NOT NULL CHECK(
        length(operation_id)=36 AND substr(operation_id,9,1)='-'
        AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-'
        AND substr(operation_id,24,1)='-'
        AND length(replace(operation_id,'-',''))=32
        AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND operation_id=lower(operation_id)
    ),
    request_sha256 TEXT NOT NULL CHECK(
        length(request_sha256)=64 AND request_sha256=lower(request_sha256)
        AND request_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    artifact_id TEXT NOT NULL CHECK(
        length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
        AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
        AND substr(artifact_id,24,1)='-'
        AND length(replace(artifact_id,'-',''))=32
        AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_id=lower(artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    extraction_snapshot_id TEXT NOT NULL CHECK(
        length(extraction_snapshot_id)=36 AND substr(extraction_snapshot_id,9,1)='-'
        AND substr(extraction_snapshot_id,14,1)='-'
        AND substr(extraction_snapshot_id,19,1)='-'
        AND substr(extraction_snapshot_id,24,1)='-'
        AND length(replace(extraction_snapshot_id,'-',''))=32
        AND replace(extraction_snapshot_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND extraction_snapshot_id=lower(extraction_snapshot_id)
    ),
    target_mode TEXT NOT NULL CHECK(target_mode IN ('personal','part','both')),
    lineage_group_id TEXT CHECK(
        lineage_group_id IS NULL OR (
            length(lineage_group_id)=36 AND substr(lineage_group_id,9,1)='-'
            AND substr(lineage_group_id,14,1)='-' AND substr(lineage_group_id,19,1)='-'
            AND substr(lineage_group_id,24,1)='-'
            AND length(replace(lineage_group_id,'-',''))=32
            AND replace(lineage_group_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND lineage_group_id=lower(lineage_group_id)
        )
    ) REFERENCES lineage_group(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(extraction_snapshot_id,artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT,
    CHECK((target_mode='both') = (lineage_group_id IS NOT NULL))
) STRICT
""".strip(),
    ),
    (
        "publication_target",
        """
CREATE TABLE publication_target(
    publication_operation_id TEXT NOT NULL CHECK(
        length(publication_operation_id)=36 AND substr(publication_operation_id,9,1)='-'
        AND substr(publication_operation_id,14,1)='-'
        AND substr(publication_operation_id,19,1)='-'
        AND substr(publication_operation_id,24,1)='-'
        AND length(replace(publication_operation_id,'-',''))=32
        AND replace(publication_operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND publication_operation_id=lower(publication_operation_id)
    ) REFERENCES publication_operation(id) ON DELETE RESTRICT,
    target_space_id TEXT NOT NULL CHECK(
        length(target_space_id)=36 AND substr(target_space_id,9,1)='-'
        AND substr(target_space_id,14,1)='-' AND substr(target_space_id,19,1)='-'
        AND substr(target_space_id,24,1)='-'
        AND length(replace(target_space_id,'-',''))=32
        AND replace(target_space_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_space_id=lower(target_space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    outcome TEXT NOT NULL CHECK(outcome IN ('created','provenance_added','update_proposed','no_op')),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ),
    resolved_revision_id TEXT NOT NULL CHECK(
        length(resolved_revision_id)=36 AND substr(resolved_revision_id,9,1)='-'
        AND substr(resolved_revision_id,14,1)='-' AND substr(resolved_revision_id,19,1)='-'
        AND substr(resolved_revision_id,24,1)='-'
        AND length(replace(resolved_revision_id,'-',''))=32
        AND replace(resolved_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND resolved_revision_id=lower(resolved_revision_id)
    ),
    created_revision_id TEXT CHECK(
        created_revision_id IS NULL OR (
            length(created_revision_id)=36 AND substr(created_revision_id,9,1)='-'
            AND substr(created_revision_id,14,1)='-' AND substr(created_revision_id,19,1)='-'
            AND substr(created_revision_id,24,1)='-'
            AND length(replace(created_revision_id,'-',''))=32
            AND replace(created_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND created_revision_id=lower(created_revision_id)
        )
    ),
    curation_finding_id TEXT CHECK(
        curation_finding_id IS NULL OR (
            length(curation_finding_id)=36 AND substr(curation_finding_id,9,1)='-'
            AND substr(curation_finding_id,14,1)='-' AND substr(curation_finding_id,19,1)='-'
            AND substr(curation_finding_id,24,1)='-'
            AND length(replace(curation_finding_id,'-',''))=32
            AND replace(curation_finding_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND curation_finding_id=lower(curation_finding_id)
        )
    ) REFERENCES curation_finding(id) ON DELETE RESTRICT,
    response_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(response_json) AND json_type(response_json)='object'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    PRIMARY KEY(publication_operation_id,target_space_id),
    FOREIGN KEY(item_id,target_space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(resolved_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(created_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(
        (outcome='created' AND created_revision_id=resolved_revision_id AND curation_finding_id IS NULL)
        OR
        (outcome IN ('provenance_added','no_op')
            AND created_revision_id IS NULL AND curation_finding_id IS NULL)
        OR
        (outcome='update_proposed' AND created_revision_id IS NULL AND curation_finding_id IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "knowledge_item",
        """
CREATE TABLE knowledge_item(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    lineage_group_id TEXT CHECK(
        lineage_group_id IS NULL OR (
            length(lineage_group_id)=36 AND substr(lineage_group_id,9,1)='-'
            AND substr(lineage_group_id,14,1)='-' AND substr(lineage_group_id,19,1)='-'
            AND substr(lineage_group_id,24,1)='-'
            AND length(replace(lineage_group_id,'-',''))=32
            AND replace(lineage_group_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND lineage_group_id=lower(lineage_group_id)
        )
    ) REFERENCES lineage_group(id) ON DELETE RESTRICT,
    lifecycle_status TEXT NOT NULL CHECK(lifecycle_status IN ('active','inactive','merged','purged_tombstone')),
    curation_status TEXT NOT NULL CHECK(curation_status IN ('published','provisional','quarantined')),
    current_revision_id TEXT CHECK(
        current_revision_id IS NULL OR (
            length(current_revision_id)=36 AND substr(current_revision_id,9,1)='-'
            AND substr(current_revision_id,14,1)='-' AND substr(current_revision_id,19,1)='-'
            AND substr(current_revision_id,24,1)='-'
            AND length(replace(current_revision_id,'-',''))=32
            AND replace(current_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND current_revision_id=lower(current_revision_id)
        )
    ),
    last_curation_transition_id TEXT CHECK(
        last_curation_transition_id IS NULL OR (
            length(last_curation_transition_id)=36
            AND substr(last_curation_transition_id,9,1)='-'
            AND substr(last_curation_transition_id,14,1)='-'
            AND substr(last_curation_transition_id,19,1)='-'
            AND substr(last_curation_transition_id,24,1)='-'
            AND length(replace(last_curation_transition_id,'-',''))=32
            AND replace(last_curation_transition_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND last_curation_transition_id=lower(last_curation_transition_id)
        )
    ),
    latest_deactivation_episode_id TEXT CHECK(
        latest_deactivation_episode_id IS NULL OR (
            length(latest_deactivation_episode_id)=36
            AND substr(latest_deactivation_episode_id,9,1)='-'
            AND substr(latest_deactivation_episode_id,14,1)='-'
            AND substr(latest_deactivation_episode_id,19,1)='-'
            AND substr(latest_deactivation_episode_id,24,1)='-'
            AND length(replace(latest_deactivation_episode_id,'-',''))=32
            AND replace(latest_deactivation_episode_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND latest_deactivation_episode_id=lower(latest_deactivation_episode_id)
        )
    ),
    redirect_item_id TEXT CHECK(
        redirect_item_id IS NULL OR (
            length(redirect_item_id)=36 AND substr(redirect_item_id,9,1)='-'
            AND substr(redirect_item_id,14,1)='-' AND substr(redirect_item_id,19,1)='-'
            AND substr(redirect_item_id,24,1)='-'
            AND length(replace(redirect_item_id,'-',''))=32
            AND replace(redirect_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND redirect_item_id=lower(redirect_item_id)
        )
    ),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    updated_by TEXT NOT NULL CHECK(
        length(updated_by) BETWEEN 1 AND 64 AND updated_by=lower(trim(updated_by))
        AND updated_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(current_revision_id,id)
        REFERENCES knowledge_revision(id,item_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(last_curation_transition_id,id)
        REFERENCES item_curation_transition(id,item_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(latest_deactivation_episode_id,id)
        REFERENCES item_deactivation_episode(id,item_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY(redirect_item_id,space_id)
        REFERENCES knowledge_item(id,space_id)
        ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
    UNIQUE(id,space_id),
    CHECK((lifecycle_status='purged_tombstone') = (current_revision_id IS NULL)),
    CHECK(lifecycle_status<>'merged' OR (redirect_item_id IS NOT NULL AND redirect_item_id<>id)),
    CHECK(lifecycle_status NOT IN ('active','inactive') OR redirect_item_id IS NULL)
) STRICT
""".strip(),
    ),
    (
        "item_curation_transition",
        """
CREATE TABLE item_curation_transition(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ),
    transition_kind TEXT NOT NULL CHECK(transition_kind IN ('finding_decision','revision_rescan')),
    from_status TEXT NOT NULL CHECK(from_status IN ('published','provisional','quarantined')),
    to_status TEXT NOT NULL CHECK(to_status IN ('published','provisional','quarantined')),
    base_revision_id TEXT NOT NULL CHECK(
        length(base_revision_id)=36 AND substr(base_revision_id,9,1)='-'
        AND substr(base_revision_id,14,1)='-' AND substr(base_revision_id,19,1)='-'
        AND substr(base_revision_id,24,1)='-'
        AND length(replace(base_revision_id,'-',''))=32
        AND replace(base_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND base_revision_id=lower(base_revision_id)
    ),
    result_revision_id TEXT NOT NULL CHECK(
        length(result_revision_id)=36 AND substr(result_revision_id,9,1)='-'
        AND substr(result_revision_id,14,1)='-' AND substr(result_revision_id,19,1)='-'
        AND substr(result_revision_id,24,1)='-'
        AND length(replace(result_revision_id,'-',''))=32
        AND replace(result_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND result_revision_id=lower(result_revision_id)
    ),
    finding_id TEXT CHECK(
        finding_id IS NULL OR (
            length(finding_id)=36 AND substr(finding_id,9,1)='-'
            AND substr(finding_id,14,1)='-' AND substr(finding_id,19,1)='-'
            AND substr(finding_id,24,1)='-'
            AND length(replace(finding_id,'-',''))=32
            AND replace(finding_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND finding_id=lower(finding_id)
        )
    ) REFERENCES curation_finding(id) ON DELETE RESTRICT,
    rescan_job_id TEXT CHECK(
        rescan_job_id IS NULL OR (
            length(rescan_job_id)=36 AND substr(rescan_job_id,9,1)='-'
            AND substr(rescan_job_id,14,1)='-' AND substr(rescan_job_id,19,1)='-'
            AND substr(rescan_job_id,24,1)='-'
            AND length(replace(rescan_job_id,'-',''))=32
            AND replace(rescan_job_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND rescan_job_id=lower(rescan_job_id)
        )
    ) REFERENCES durable_job(id) ON DELETE RESTRICT,
    decision_job_id TEXT CHECK(
        decision_job_id IS NULL OR (
            length(decision_job_id)=36 AND substr(decision_job_id,9,1)='-'
            AND substr(decision_job_id,14,1)='-' AND substr(decision_job_id,19,1)='-'
            AND substr(decision_job_id,24,1)='-'
            AND length(replace(decision_job_id,'-',''))=32
            AND replace(decision_job_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND decision_job_id=lower(decision_job_id)
        )
    ) REFERENCES durable_job(id) ON DELETE RESTRICT,
    dedup_id TEXT CHECK(
        dedup_id IS NULL OR (
            length(dedup_id)=36 AND substr(dedup_id,9,1)='-'
            AND substr(dedup_id,14,1)='-' AND substr(dedup_id,19,1)='-'
            AND substr(dedup_id,24,1)='-'
            AND length(replace(dedup_id,'-',''))=32
            AND replace(dedup_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND dedup_id=lower(dedup_id)
        )
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    operation_id TEXT CHECK(
        operation_id IS NULL OR (
            length(operation_id)=36 AND substr(operation_id,9,1)='-'
            AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-'
            AND substr(operation_id,24,1)='-'
            AND length(replace(operation_id,'-',''))=32
            AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND operation_id=lower(operation_id)
        )
    ),
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','system')),
    actor_user_id TEXT CHECK(
        actor_user_id IS NULL OR (
            length(actor_user_id) BETWEEN 1 AND 64
            AND actor_user_id=lower(trim(actor_user_id))
            AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    result_item_row_version INTEGER NOT NULL CHECK(result_item_row_version >= 2),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(base_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(result_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(
        (actor_kind='user' AND actor_user_id IS NOT NULL
            AND dedup_id IS NOT NULL AND operation_id IS NOT NULL AND decision_job_id IS NULL)
        OR
        (actor_kind='system' AND actor_user_id IS NULL
            AND dedup_id IS NULL AND operation_id IS NULL AND decision_job_id IS NOT NULL)
    ),
    CHECK(
        (transition_kind='finding_decision' AND from_status='provisional'
            AND to_status IN ('published','quarantined')
            AND base_revision_id=result_revision_id AND finding_id IS NOT NULL
            AND rescan_job_id IS NULL)
        OR
        (transition_kind='revision_rescan' AND to_status='provisional'
            AND finding_id IS NULL AND decision_job_id IS NULL AND rescan_job_id IS NOT NULL
            AND actor_kind='user' AND base_revision_id<>result_revision_id)
    )
) STRICT
""".strip(),
    ),
    (
        "knowledge_revision",
        """
CREATE TABLE knowledge_revision(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ) REFERENCES knowledge_item(id) ON DELETE RESTRICT,
    revision_no INTEGER NOT NULL CHECK(revision_no >= 1),
    parent_revision_id TEXT CHECK(
        parent_revision_id IS NULL OR (
            length(parent_revision_id)=36 AND substr(parent_revision_id,9,1)='-'
            AND substr(parent_revision_id,14,1)='-' AND substr(parent_revision_id,19,1)='-'
            AND substr(parent_revision_id,24,1)='-'
            AND length(replace(parent_revision_id,'-',''))=32
            AND replace(parent_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND parent_revision_id=lower(parent_revision_id)
        )
    ),
    base_revision_id TEXT CHECK(
        base_revision_id IS NULL OR (
            length(base_revision_id)=36 AND substr(base_revision_id,9,1)='-'
            AND substr(base_revision_id,14,1)='-' AND substr(base_revision_id,19,1)='-'
            AND substr(base_revision_id,24,1)='-'
            AND length(replace(base_revision_id,'-',''))=32
            AND replace(base_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND base_revision_id=lower(base_revision_id)
        )
    ),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 500),
    raw_content_sha256 TEXT NOT NULL CHECK(
        length(raw_content_sha256)=64 AND raw_content_sha256=lower(raw_content_sha256)
        AND raw_content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    normalized_content_sha256 TEXT NOT NULL CHECK(
        length(normalized_content_sha256)=64
        AND normalized_content_sha256=lower(normalized_content_sha256)
        AND normalized_content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    content_size_bytes INTEGER NOT NULL CHECK(content_size_bytes >= 0),
    normalizer_version TEXT NOT NULL CHECK(length(trim(normalizer_version)) BETWEEN 1 AND 80),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(metadata_json) AND json_type(metadata_json)='object'
    ),
    change_kind TEXT NOT NULL CHECK(change_kind IN ('initial','edit','merge','import','approved_update')),
    change_reason TEXT NOT NULL CHECK(length(trim(change_reason)) BETWEEN 1 AND 2000),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(parent_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(base_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(parent_revision_id IS NULL OR parent_revision_id<>id),
    CHECK(base_revision_id IS NULL OR base_revision_id<>id)
) STRICT
""".strip(),
    ),
    (
        "knowledge_revision_body",
        """
CREATE TABLE knowledge_revision_body(
    revision_id TEXT PRIMARY KEY NOT NULL CHECK(
        length(revision_id)=36 AND substr(revision_id,9,1)='-'
        AND substr(revision_id,14,1)='-' AND substr(revision_id,19,1)='-'
        AND substr(revision_id,24,1)='-'
        AND length(replace(revision_id,'-',''))=32
        AND replace(revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND revision_id=lower(revision_id)
    ),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ),
    content_md TEXT,
    content_blob_hash TEXT CHECK(
        content_blob_hash IS NULL OR (
            length(content_blob_hash)=64 AND content_blob_hash=lower(content_blob_hash)
            AND content_blob_hash NOT GLOB '*[^0-9a-f]*'
        )
    ) REFERENCES blob_object(sha256) ON DELETE RESTRICT,
    content_size_bytes INTEGER NOT NULL CHECK(content_size_bytes >= 0),
    content_sha256 TEXT NOT NULL CHECK(
        length(content_sha256)=64 AND content_sha256=lower(content_sha256)
        AND content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK((content_md IS NOT NULL) <> (content_blob_hash IS NOT NULL))
) STRICT
""".strip(),
    ),
    (
        "item_source",
        """
CREATE TABLE item_source(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ),
    revision_id TEXT NOT NULL CHECK(
        length(revision_id)=36 AND substr(revision_id,9,1)='-'
        AND substr(revision_id,14,1)='-' AND substr(revision_id,19,1)='-'
        AND substr(revision_id,24,1)='-'
        AND length(replace(revision_id,'-',''))=32
        AND replace(revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND revision_id=lower(revision_id)
    ),
    artifact_id TEXT NOT NULL CHECK(
        length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
        AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
        AND substr(artifact_id,24,1)='-'
        AND length(replace(artifact_id,'-',''))=32
        AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND artifact_id=lower(artifact_id)
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    extraction_snapshot_id TEXT NOT NULL CHECK(
        length(extraction_snapshot_id)=36 AND substr(extraction_snapshot_id,9,1)='-'
        AND substr(extraction_snapshot_id,14,1)='-'
        AND substr(extraction_snapshot_id,19,1)='-'
        AND substr(extraction_snapshot_id,24,1)='-'
        AND length(replace(extraction_snapshot_id,'-',''))=32
        AND replace(extraction_snapshot_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND extraction_snapshot_id=lower(extraction_snapshot_id)
    ),
    role TEXT NOT NULL CHECK(role IN ('initial','evidence','update','migrated')),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(extraction_snapshot_id,artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT
) STRICT
""".strip(),
    ),
    (
        "item_relation",
        """
CREATE TABLE item_relation(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    source_item_id TEXT NOT NULL CHECK(
        length(source_item_id)=36 AND substr(source_item_id,9,1)='-'
        AND substr(source_item_id,14,1)='-' AND substr(source_item_id,19,1)='-'
        AND substr(source_item_id,24,1)='-'
        AND length(replace(source_item_id,'-',''))=32
        AND replace(source_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_item_id=lower(source_item_id)
    ),
    source_revision_id TEXT NOT NULL CHECK(
        length(source_revision_id)=36 AND substr(source_revision_id,9,1)='-'
        AND substr(source_revision_id,14,1)='-' AND substr(source_revision_id,19,1)='-'
        AND substr(source_revision_id,24,1)='-'
        AND length(replace(source_revision_id,'-',''))=32
        AND replace(source_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_revision_id=lower(source_revision_id)
    ),
    target_item_id TEXT NOT NULL CHECK(
        length(target_item_id)=36 AND substr(target_item_id,9,1)='-'
        AND substr(target_item_id,14,1)='-' AND substr(target_item_id,19,1)='-'
        AND substr(target_item_id,24,1)='-'
        AND length(replace(target_item_id,'-',''))=32
        AND replace(target_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_item_id=lower(target_item_id)
    ),
    target_revision_id TEXT NOT NULL CHECK(
        length(target_revision_id)=36 AND substr(target_revision_id,9,1)='-'
        AND substr(target_revision_id,14,1)='-' AND substr(target_revision_id,19,1)='-'
        AND substr(target_revision_id,24,1)='-'
        AND length(replace(target_revision_id,'-',''))=32
        AND replace(target_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_revision_id=lower(target_revision_id)
    ),
    relation_type TEXT NOT NULL CHECK(relation_type IN ('forked_from','promoted_from','exact_duplicate_of','merged_into','supersedes')),
    relation_status TEXT NOT NULL CHECK(relation_status IN ('active','inactive')),
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(evidence_json) AND json_type(evidence_json)='object'
    ),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    updated_by TEXT NOT NULL CHECK(
        length(updated_by) BETWEEN 1 AND 64 AND updated_by=lower(trim(updated_by))
        AND updated_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(source_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(source_item_id<>target_item_id)
) STRICT
""".strip(),
    ),
    (
        "item_deactivation_episode",
        """
CREATE TABLE item_deactivation_episode(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ) REFERENCES knowledge_item(id) ON DELETE RESTRICT,
    episode_no INTEGER NOT NULL CHECK(episode_no >= 1),
    deactivated_by TEXT NOT NULL CHECK(
        length(deactivated_by) BETWEEN 1 AND 64
        AND deactivated_by=lower(trim(deactivated_by))
        AND deactivated_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    deactivated_at INTEGER NOT NULL CHECK(deactivated_at >= 0),
    reason TEXT NOT NULL CHECK(length(trim(reason)) BETWEEN 1 AND 2000),
    purge_eligible_at INTEGER NOT NULL CHECK(
        purge_eligible_at >= 0 AND purge_eligible_at=deactivated_at+7776000000
    ),
    restored_by TEXT CHECK(
        restored_by IS NULL OR (
            length(restored_by) BETWEEN 1 AND 64 AND restored_by=lower(trim(restored_by))
            AND restored_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    restored_at INTEGER CHECK(restored_at IS NULL OR restored_at >= deactivated_at),
    purged_by TEXT CHECK(
        purged_by IS NULL OR (
            length(purged_by) BETWEEN 1 AND 64 AND purged_by=lower(trim(purged_by))
            AND purged_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    purged_at INTEGER CHECK(purged_at IS NULL OR purged_at >= deactivated_at),
    CHECK((restored_by IS NULL) = (restored_at IS NULL)),
    CHECK((purged_by IS NULL) = (purged_at IS NULL)),
    CHECK(restored_at IS NULL OR purged_at IS NULL)
) STRICT
""".strip(),
    ),
    (
        "knowledge_edge",
        """
CREATE TABLE knowledge_edge(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK(
        length(source_item_id)=36 AND substr(source_item_id,9,1)='-'
        AND substr(source_item_id,14,1)='-' AND substr(source_item_id,19,1)='-'
        AND substr(source_item_id,24,1)='-'
        AND length(replace(source_item_id,'-',''))=32
        AND replace(source_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_item_id=lower(source_item_id)
    ),
    target_item_id TEXT NOT NULL CHECK(
        length(target_item_id)=36 AND substr(target_item_id,9,1)='-'
        AND substr(target_item_id,14,1)='-' AND substr(target_item_id,19,1)='-'
        AND substr(target_item_id,24,1)='-'
        AND length(replace(target_item_id,'-',''))=32
        AND replace(target_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_item_id=lower(target_item_id)
    ),
    edge_type TEXT NOT NULL CHECK(edge_type IN ('related','supports','contradicts','depends_on','extends','example_of')),
    edge_status TEXT NOT NULL CHECK(edge_status IN ('active','inactive')),
    origin TEXT NOT NULL CHECK(origin IN ('manual','semantic_approved')),
    source_evidence_revision_id TEXT CHECK(
        source_evidence_revision_id IS NULL OR (
            length(source_evidence_revision_id)=36
            AND substr(source_evidence_revision_id,9,1)='-'
            AND substr(source_evidence_revision_id,14,1)='-'
            AND substr(source_evidence_revision_id,19,1)='-'
            AND substr(source_evidence_revision_id,24,1)='-'
            AND length(replace(source_evidence_revision_id,'-',''))=32
            AND replace(source_evidence_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND source_evidence_revision_id=lower(source_evidence_revision_id)
        )
    ),
    target_evidence_revision_id TEXT CHECK(
        target_evidence_revision_id IS NULL OR (
            length(target_evidence_revision_id)=36
            AND substr(target_evidence_revision_id,9,1)='-'
            AND substr(target_evidence_revision_id,14,1)='-'
            AND substr(target_evidence_revision_id,19,1)='-'
            AND substr(target_evidence_revision_id,24,1)='-'
            AND length(replace(target_evidence_revision_id,'-',''))=32
            AND replace(target_evidence_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND target_evidence_revision_id=lower(target_evidence_revision_id)
        )
    ),
    approval_finding_id TEXT CHECK(
        approval_finding_id IS NULL OR (
            length(approval_finding_id)=36 AND substr(approval_finding_id,9,1)='-'
            AND substr(approval_finding_id,14,1)='-' AND substr(approval_finding_id,19,1)='-'
            AND substr(approval_finding_id,24,1)='-'
            AND length(replace(approval_finding_id,'-',''))=32
            AND replace(approval_finding_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND approval_finding_id=lower(approval_finding_id)
        )
    ) REFERENCES curation_finding(id) ON DELETE RESTRICT,
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(evidence_json) AND json_type(evidence_json)='object'
    ),
    needs_revalidation INTEGER NOT NULL DEFAULT 0 CHECK(needs_revalidation IN (0,1)),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    updated_by TEXT NOT NULL CHECK(
        length(updated_by) BETWEEN 1 AND 64 AND updated_by=lower(trim(updated_by))
        AND updated_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(source_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_evidence_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_evidence_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(source_item_id<>target_item_id),
    CHECK((source_evidence_revision_id IS NULL) = (target_evidence_revision_id IS NULL)),
    CHECK(
        (origin='manual' AND approval_finding_id IS NULL)
        OR
        (origin='semantic_approved' AND approval_finding_id IS NOT NULL
            AND source_evidence_revision_id IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "curation_finding",
        """
CREATE TABLE curation_finding(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    finding_kind TEXT NOT NULL CHECK(finding_kind IN ('lint','exact_repeat','curation_clear','weak_complementary','near_duplicate','update','potential_conflict','semantic_edge','replace','merge','curation_release','curation_quarantine','deactivate','purge')),
    risk_tier TEXT NOT NULL CHECK(risk_tier IN ('T0','T1','T2','T3')),
    severity TEXT NOT NULL CHECK(severity IN ('blocker','error','warning','info')),
    status TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','ignored','obsolete','applied','apply_failed')),
    decision_mode TEXT NOT NULL CHECK(decision_mode IN ('automatic','human')),
    required_reviewer TEXT NOT NULL CHECK(required_reviewer IN ('none','space_editor','part_leader','admin')),
    visibility_effect TEXT NOT NULL CHECK(visibility_effect IN ('none','badge','agent_hidden','publication_blocked')),
    proposed_action TEXT NOT NULL CHECK(proposed_action IN ('none','provenance','review_duplicate','update','review_conflict','relation','merge','replace','publish','quarantine','deactivate','purge')),
    source_item_id TEXT NOT NULL CHECK(
        length(source_item_id)=36 AND substr(source_item_id,9,1)='-'
        AND substr(source_item_id,14,1)='-' AND substr(source_item_id,19,1)='-'
        AND substr(source_item_id,24,1)='-'
        AND length(replace(source_item_id,'-',''))=32
        AND replace(source_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_item_id=lower(source_item_id)
    ),
    source_revision_id TEXT NOT NULL CHECK(
        length(source_revision_id)=36 AND substr(source_revision_id,9,1)='-'
        AND substr(source_revision_id,14,1)='-' AND substr(source_revision_id,19,1)='-'
        AND substr(source_revision_id,24,1)='-'
        AND length(replace(source_revision_id,'-',''))=32
        AND replace(source_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_revision_id=lower(source_revision_id)
    ),
    target_item_id TEXT CHECK(
        target_item_id IS NULL OR (
            length(target_item_id)=36 AND substr(target_item_id,9,1)='-'
            AND substr(target_item_id,14,1)='-' AND substr(target_item_id,19,1)='-'
            AND substr(target_item_id,24,1)='-'
            AND length(replace(target_item_id,'-',''))=32
            AND replace(target_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND target_item_id=lower(target_item_id)
        )
    ),
    target_revision_id TEXT CHECK(
        target_revision_id IS NULL OR (
            length(target_revision_id)=36 AND substr(target_revision_id,9,1)='-'
            AND substr(target_revision_id,14,1)='-' AND substr(target_revision_id,19,1)='-'
            AND substr(target_revision_id,24,1)='-'
            AND length(replace(target_revision_id,'-',''))=32
            AND replace(target_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND target_revision_id=lower(target_revision_id)
        )
    ),
    detector_name TEXT NOT NULL CHECK(length(trim(detector_name)) BETWEEN 1 AND 120),
    detector_version TEXT NOT NULL CHECK(length(trim(detector_version)) BETWEEN 1 AND 120),
    rule_version TEXT NOT NULL CHECK(length(trim(rule_version)) BETWEEN 1 AND 120),
    model_id TEXT CHECK(model_id IS NULL OR length(trim(model_id)) BETWEEN 1 AND 200),
    prompt_version TEXT CHECK(
        prompt_version IS NULL OR length(trim(prompt_version)) BETWEEN 1 AND 200
    ),
    confidence REAL CHECK(confidence IS NULL OR confidence BETWEEN 0.0 AND 1.0),
    evidence_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(evidence_json) AND json_type(evidence_json)='object'
    ),
    proposed_action_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(proposed_action_json) AND json_type(proposed_action_json)='object'
    ),
    reviewed_by TEXT CHECK(
        reviewed_by IS NULL OR (
            length(reviewed_by) BETWEEN 1 AND 64 AND reviewed_by=lower(trim(reviewed_by))
            AND reviewed_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    reviewed_at INTEGER CHECK(reviewed_at IS NULL OR reviewed_at >= 0),
    review_reason TEXT CHECK(
        review_reason IS NULL OR length(trim(review_reason)) BETWEEN 1 AND 2000
    ),
    applied_operation_id TEXT CHECK(
        applied_operation_id IS NULL OR (
            length(applied_operation_id)=36 AND substr(applied_operation_id,9,1)='-'
            AND substr(applied_operation_id,14,1)='-' AND substr(applied_operation_id,19,1)='-'
            AND substr(applied_operation_id,24,1)='-'
            AND length(replace(applied_operation_id,'-',''))=32
            AND replace(applied_operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND applied_operation_id=lower(applied_operation_id)
        )
    ),
    applied_dedup_id TEXT CHECK(
        applied_dedup_id IS NULL OR (
            length(applied_dedup_id)=36 AND substr(applied_dedup_id,9,1)='-'
            AND substr(applied_dedup_id,14,1)='-' AND substr(applied_dedup_id,19,1)='-'
            AND substr(applied_dedup_id,24,1)='-'
            AND length(replace(applied_dedup_id,'-',''))=32
            AND replace(applied_dedup_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND applied_dedup_id=lower(applied_dedup_id)
        )
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    applied_job_id TEXT CHECK(
        applied_job_id IS NULL OR (
            length(applied_job_id)=36 AND substr(applied_job_id,9,1)='-'
            AND substr(applied_job_id,14,1)='-' AND substr(applied_job_id,19,1)='-'
            AND substr(applied_job_id,24,1)='-'
            AND length(replace(applied_job_id,'-',''))=32
            AND replace(applied_job_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND applied_job_id=lower(applied_job_id)
        )
    ) REFERENCES durable_job(id) ON DELETE RESTRICT,
    applied_by TEXT CHECK(
        applied_by IS NULL OR (
            length(applied_by) BETWEEN 1 AND 64 AND applied_by=lower(trim(applied_by))
            AND applied_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    applied_at INTEGER CHECK(applied_at IS NULL OR applied_at >= 0),
    last_application_error_json TEXT CHECK(
        last_application_error_json IS NULL OR (
            json_valid(last_application_error_json)
            AND json_type(last_application_error_json)='object'
        )
    ),
    created_actor_kind TEXT NOT NULL CHECK(created_actor_kind IN ('user','system')),
    created_by TEXT CHECK(
        created_by IS NULL OR (
            length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
            AND created_by NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(source_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    CHECK((target_item_id IS NULL) = (target_revision_id IS NULL)),
    CHECK(target_item_id IS NULL OR target_item_id<>source_item_id),
    CHECK((model_id IS NULL) = (prompt_version IS NULL)),
    CHECK(
        (created_actor_kind='user' AND created_by IS NOT NULL)
        OR (created_actor_kind='system' AND created_by IS NULL)
    ),
    CHECK(
        (reviewed_by IS NULL AND reviewed_at IS NULL AND review_reason IS NULL)
        OR
        (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND review_reason IS NOT NULL)
    ),
    CHECK(
        (status='applied' AND applied_at IS NOT NULL
            AND last_application_error_json IS NULL
            AND (
                (applied_operation_id IS NOT NULL AND applied_dedup_id IS NOT NULL
                    AND applied_by IS NOT NULL AND applied_job_id IS NULL)
                OR
                (applied_operation_id IS NULL AND applied_dedup_id IS NULL
                    AND applied_by IS NULL AND applied_job_id IS NOT NULL)
            ))
        OR
        (status='apply_failed' AND applied_operation_id IS NOT NULL
            AND applied_dedup_id IS NOT NULL AND applied_by IS NOT NULL
            AND applied_at IS NOT NULL AND applied_job_id IS NULL
            AND last_application_error_json IS NOT NULL)
        OR
        (status NOT IN ('applied','apply_failed')
            AND applied_operation_id IS NULL AND applied_dedup_id IS NULL
            AND applied_job_id IS NULL AND applied_by IS NULL AND applied_at IS NULL
            AND last_application_error_json IS NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "change_proposal",
        """
CREATE TABLE change_proposal(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    finding_id TEXT NOT NULL CHECK(
        length(finding_id)=36 AND substr(finding_id,9,1)='-'
        AND substr(finding_id,14,1)='-' AND substr(finding_id,19,1)='-'
        AND substr(finding_id,24,1)='-'
        AND length(replace(finding_id,'-',''))=32
        AND replace(finding_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND finding_id=lower(finding_id)
    ) REFERENCES curation_finding(id) ON DELETE RESTRICT,
    finding_input_row_version INTEGER NOT NULL CHECK(finding_input_row_version >= 1),
    proposal_kind TEXT NOT NULL CHECK(proposal_kind IN ('stable_source_update','personal_to_part')),
    proposal_series_id TEXT NOT NULL CHECK(
        length(proposal_series_id)=36 AND substr(proposal_series_id,9,1)='-'
        AND substr(proposal_series_id,14,1)='-' AND substr(proposal_series_id,19,1)='-'
        AND substr(proposal_series_id,24,1)='-'
        AND length(replace(proposal_series_id,'-',''))=32
        AND replace(proposal_series_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND proposal_series_id=lower(proposal_series_id)
    ),
    proposal_version INTEGER NOT NULL CHECK(proposal_version >= 1),
    supersedes_proposal_id TEXT CHECK(
        supersedes_proposal_id IS NULL OR (
            length(supersedes_proposal_id)=36 AND substr(supersedes_proposal_id,9,1)='-'
            AND substr(supersedes_proposal_id,14,1)='-'
            AND substr(supersedes_proposal_id,19,1)='-'
            AND substr(supersedes_proposal_id,24,1)='-'
            AND length(replace(supersedes_proposal_id,'-',''))=32
            AND replace(supersedes_proposal_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND supersedes_proposal_id=lower(supersedes_proposal_id)
        )
    ) REFERENCES change_proposal(id) ON DELETE RESTRICT,
    lineage_group_id TEXT CHECK(
        lineage_group_id IS NULL OR (
            length(lineage_group_id)=36 AND substr(lineage_group_id,9,1)='-'
            AND substr(lineage_group_id,14,1)='-' AND substr(lineage_group_id,19,1)='-'
            AND substr(lineage_group_id,24,1)='-'
            AND length(replace(lineage_group_id,'-',''))=32
            AND replace(lineage_group_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND lineage_group_id=lower(lineage_group_id)
        )
    ) REFERENCES lineage_group(id) ON DELETE RESTRICT,
    base_item_id TEXT NOT NULL CHECK(
        length(base_item_id)=36 AND substr(base_item_id,9,1)='-'
        AND substr(base_item_id,14,1)='-' AND substr(base_item_id,19,1)='-'
        AND substr(base_item_id,24,1)='-'
        AND length(replace(base_item_id,'-',''))=32
        AND replace(base_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND base_item_id=lower(base_item_id)
    ),
    base_revision_id TEXT NOT NULL CHECK(
        length(base_revision_id)=36 AND substr(base_revision_id,9,1)='-'
        AND substr(base_revision_id,14,1)='-' AND substr(base_revision_id,19,1)='-'
        AND substr(base_revision_id,24,1)='-'
        AND length(replace(base_revision_id,'-',''))=32
        AND replace(base_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND base_revision_id=lower(base_revision_id)
    ),
    source_item_id TEXT CHECK(
        source_item_id IS NULL OR (
            length(source_item_id)=36 AND substr(source_item_id,9,1)='-'
            AND substr(source_item_id,14,1)='-' AND substr(source_item_id,19,1)='-'
            AND substr(source_item_id,24,1)='-'
            AND length(replace(source_item_id,'-',''))=32
            AND replace(source_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND source_item_id=lower(source_item_id)
        )
    ),
    source_revision_id TEXT CHECK(
        source_revision_id IS NULL OR (
            length(source_revision_id)=36 AND substr(source_revision_id,9,1)='-'
            AND substr(source_revision_id,14,1)='-' AND substr(source_revision_id,19,1)='-'
            AND substr(source_revision_id,24,1)='-'
            AND length(replace(source_revision_id,'-',''))=32
            AND replace(source_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND source_revision_id=lower(source_revision_id)
        )
    ),
    target_item_id TEXT NOT NULL CHECK(
        length(target_item_id)=36 AND substr(target_item_id,9,1)='-'
        AND substr(target_item_id,14,1)='-' AND substr(target_item_id,19,1)='-'
        AND substr(target_item_id,24,1)='-'
        AND length(replace(target_item_id,'-',''))=32
        AND replace(target_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_item_id=lower(target_item_id)
    ),
    target_revision_id TEXT NOT NULL CHECK(
        length(target_revision_id)=36 AND substr(target_revision_id,9,1)='-'
        AND substr(target_revision_id,14,1)='-' AND substr(target_revision_id,19,1)='-'
        AND substr(target_revision_id,24,1)='-'
        AND length(replace(target_revision_id,'-',''))=32
        AND replace(target_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_revision_id=lower(target_revision_id)
    ),
    source_artifact_id TEXT CHECK(
        source_artifact_id IS NULL OR (
            length(source_artifact_id)=36 AND substr(source_artifact_id,9,1)='-'
            AND substr(source_artifact_id,14,1)='-' AND substr(source_artifact_id,19,1)='-'
            AND substr(source_artifact_id,24,1)='-'
            AND length(replace(source_artifact_id,'-',''))=32
            AND replace(source_artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND source_artifact_id=lower(source_artifact_id)
        )
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    extraction_snapshot_id TEXT CHECK(
        extraction_snapshot_id IS NULL OR (
            length(extraction_snapshot_id)=36 AND substr(extraction_snapshot_id,9,1)='-'
            AND substr(extraction_snapshot_id,14,1)='-'
            AND substr(extraction_snapshot_id,19,1)='-'
            AND substr(extraction_snapshot_id,24,1)='-'
            AND length(replace(extraction_snapshot_id,'-',''))=32
            AND replace(extraction_snapshot_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND extraction_snapshot_id=lower(extraction_snapshot_id)
        )
    ),
    proposed_content_md TEXT,
    proposed_content_blob_hash TEXT CHECK(
        proposed_content_blob_hash IS NULL OR (
            length(proposed_content_blob_hash)=64
            AND proposed_content_blob_hash=lower(proposed_content_blob_hash)
            AND proposed_content_blob_hash NOT GLOB '*[^0-9a-f]*'
        )
    ) REFERENCES blob_object(sha256) ON DELETE RESTRICT,
    proposed_content_size_bytes INTEGER NOT NULL CHECK(proposed_content_size_bytes >= 0),
    proposed_content_sha256 TEXT NOT NULL CHECK(
        length(proposed_content_sha256)=64
        AND proposed_content_sha256=lower(proposed_content_sha256)
        AND proposed_content_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    preview_sha256 TEXT NOT NULL CHECK(
        length(preview_sha256)=64 AND preview_sha256=lower(preview_sha256)
        AND preview_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    state TEXT NOT NULL CHECK(state IN ('pending_review','approved','obsolete','applied','apply_failed')),
    applied_operation_id TEXT CHECK(
        applied_operation_id IS NULL OR (
            length(applied_operation_id)=36 AND substr(applied_operation_id,9,1)='-'
            AND substr(applied_operation_id,14,1)='-' AND substr(applied_operation_id,19,1)='-'
            AND substr(applied_operation_id,24,1)='-'
            AND length(replace(applied_operation_id,'-',''))=32
            AND replace(applied_operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND applied_operation_id=lower(applied_operation_id)
        )
    ),
    applied_dedup_id TEXT CHECK(
        applied_dedup_id IS NULL OR (
            length(applied_dedup_id)=36 AND substr(applied_dedup_id,9,1)='-'
            AND substr(applied_dedup_id,14,1)='-' AND substr(applied_dedup_id,19,1)='-'
            AND substr(applied_dedup_id,24,1)='-'
            AND length(replace(applied_dedup_id,'-',''))=32
            AND replace(applied_dedup_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND applied_dedup_id=lower(applied_dedup_id)
        )
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    last_application_error_json TEXT CHECK(
        last_application_error_json IS NULL OR (
            json_valid(last_application_error_json)
            AND json_type(last_application_error_json)='object'
        )
    ),
    created_by TEXT NOT NULL CHECK(
        length(created_by) BETWEEN 1 AND 64 AND created_by=lower(trim(created_by))
        AND created_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    updated_by TEXT NOT NULL CHECK(
        length(updated_by) BETWEEN 1 AND 64 AND updated_by=lower(trim(updated_by))
        AND updated_by NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    FOREIGN KEY(base_revision_id,base_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(extraction_snapshot_id,source_artifact_id)
        REFERENCES extraction_snapshot(id,artifact_id) ON DELETE RESTRICT,
    CHECK((source_item_id IS NULL) = (source_revision_id IS NULL)),
    CHECK((source_artifact_id IS NULL) = (extraction_snapshot_id IS NULL)),
    CHECK((proposed_content_md IS NOT NULL) <> (proposed_content_blob_hash IS NOT NULL)),
    CHECK(
        (proposal_version=1 AND proposal_series_id=id AND supersedes_proposal_id IS NULL)
        OR
        (proposal_version>1 AND supersedes_proposal_id IS NOT NULL)
    ),
    CHECK(
        (proposal_kind='stable_source_update' AND lineage_group_id IS NULL
            AND source_item_id IS NULL AND source_revision_id IS NULL
            AND source_artifact_id IS NOT NULL AND extraction_snapshot_id IS NOT NULL
            AND base_item_id=target_item_id AND base_revision_id=target_revision_id)
        OR
        (proposal_kind='personal_to_part' AND lineage_group_id IS NOT NULL
            AND source_item_id IS NOT NULL AND source_revision_id IS NOT NULL
            AND source_artifact_id IS NULL AND extraction_snapshot_id IS NULL
            AND source_item_id<>target_item_id)
    ),
    CHECK(
        (state='applied' AND applied_operation_id IS NOT NULL
            AND applied_dedup_id IS NOT NULL AND last_application_error_json IS NULL)
        OR
        (state='apply_failed' AND applied_operation_id IS NOT NULL
            AND applied_dedup_id IS NOT NULL AND last_application_error_json IS NOT NULL)
        OR
        (state NOT IN ('applied','apply_failed') AND applied_operation_id IS NULL
            AND applied_dedup_id IS NULL AND last_application_error_json IS NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "merge_event",
        """
CREATE TABLE merge_event(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    dedup_id TEXT NOT NULL CHECK(
        length(dedup_id)=36 AND substr(dedup_id,9,1)='-'
        AND substr(dedup_id,14,1)='-' AND substr(dedup_id,19,1)='-'
        AND substr(dedup_id,24,1)='-'
        AND length(replace(dedup_id,'-',''))=32
        AND replace(dedup_id,'-','') NOT GLOB '*[^0-9a-f]*' AND dedup_id=lower(dedup_id)
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK(
        length(source_item_id)=36 AND substr(source_item_id,9,1)='-'
        AND substr(source_item_id,14,1)='-' AND substr(source_item_id,19,1)='-'
        AND substr(source_item_id,24,1)='-'
        AND length(replace(source_item_id,'-',''))=32
        AND replace(source_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_item_id=lower(source_item_id)
    ),
    source_revision_id TEXT NOT NULL CHECK(
        length(source_revision_id)=36 AND substr(source_revision_id,9,1)='-'
        AND substr(source_revision_id,14,1)='-' AND substr(source_revision_id,19,1)='-'
        AND substr(source_revision_id,24,1)='-'
        AND length(replace(source_revision_id,'-',''))=32
        AND replace(source_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_revision_id=lower(source_revision_id)
    ),
    target_item_id TEXT NOT NULL CHECK(
        length(target_item_id)=36 AND substr(target_item_id,9,1)='-'
        AND substr(target_item_id,14,1)='-' AND substr(target_item_id,19,1)='-'
        AND substr(target_item_id,24,1)='-'
        AND length(replace(target_item_id,'-',''))=32
        AND replace(target_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_item_id=lower(target_item_id)
    ),
    target_revision_id TEXT NOT NULL CHECK(
        length(target_revision_id)=36 AND substr(target_revision_id,9,1)='-'
        AND substr(target_revision_id,14,1)='-' AND substr(target_revision_id,19,1)='-'
        AND substr(target_revision_id,24,1)='-'
        AND length(replace(target_revision_id,'-',''))=32
        AND replace(target_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_revision_id=lower(target_revision_id)
    ),
    result_revision_id TEXT NOT NULL CHECK(
        length(result_revision_id)=36 AND substr(result_revision_id,9,1)='-'
        AND substr(result_revision_id,14,1)='-' AND substr(result_revision_id,19,1)='-'
        AND substr(result_revision_id,24,1)='-'
        AND length(replace(result_revision_id,'-',''))=32
        AND replace(result_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND result_revision_id=lower(result_revision_id)
    ),
    preview_sha256 TEXT NOT NULL CHECK(
        length(preview_sha256)=64 AND preview_sha256=lower(preview_sha256)
        AND preview_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    relation_id TEXT NOT NULL CHECK(
        length(relation_id)=36 AND substr(relation_id,9,1)='-'
        AND substr(relation_id,14,1)='-' AND substr(relation_id,19,1)='-'
        AND substr(relation_id,24,1)='-'
        AND length(replace(relation_id,'-',''))=32
        AND replace(relation_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND relation_id=lower(relation_id)
    ) REFERENCES item_relation(id) ON DELETE RESTRICT,
    actor_user_id TEXT NOT NULL CHECK(
        length(actor_user_id) BETWEEN 1 AND 64
        AND actor_user_id=lower(trim(actor_user_id))
        AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    FOREIGN KEY(source_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_revision_id,source_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(target_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    FOREIGN KEY(result_revision_id,target_item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(source_item_id<>target_item_id)
) STRICT
""".strip(),
    ),
    (
        "item_lease",
        """
CREATE TABLE item_lease(
    item_id TEXT PRIMARY KEY NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ) REFERENCES knowledge_item(id) ON DELETE RESTRICT,
    holder_user_id TEXT CHECK(
        holder_user_id IS NULL OR (
            length(holder_user_id) BETWEEN 1 AND 64
            AND holder_user_id=lower(trim(holder_user_id))
            AND holder_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    editor_session_id TEXT CHECK(
        editor_session_id IS NULL OR (
            length(editor_session_id)=36 AND substr(editor_session_id,9,1)='-'
            AND substr(editor_session_id,14,1)='-' AND substr(editor_session_id,19,1)='-'
            AND substr(editor_session_id,24,1)='-'
            AND length(replace(editor_session_id,'-',''))=32
            AND replace(editor_session_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND editor_session_id=lower(editor_session_id)
        )
    ),
    token_hmac BLOB CHECK(token_hmac IS NULL OR length(token_hmac)=32),
    hmac_key_id TEXT CHECK(
        hmac_key_id IS NULL OR length(trim(hmac_key_id)) BETWEEN 1 AND 80
    ),
    fencing_generation INTEGER NOT NULL DEFAULT 0 CHECK(fencing_generation >= 0),
    base_item_row_version INTEGER CHECK(
        base_item_row_version IS NULL OR base_item_row_version >= 1
    ),
    base_revision_id TEXT CHECK(
        base_revision_id IS NULL OR (
            length(base_revision_id)=36 AND substr(base_revision_id,9,1)='-'
            AND substr(base_revision_id,14,1)='-' AND substr(base_revision_id,19,1)='-'
            AND substr(base_revision_id,24,1)='-'
            AND length(replace(base_revision_id,'-',''))=32
            AND replace(base_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND base_revision_id=lower(base_revision_id)
        )
    ),
    acquired_at INTEGER CHECK(acquired_at IS NULL OR acquired_at >= 0),
    heartbeat_at INTEGER CHECK(heartbeat_at IS NULL OR heartbeat_at >= 0),
    expires_at INTEGER CHECK(expires_at IS NULL OR expires_at >= 0),
    absolute_expires_at INTEGER CHECK(absolute_expires_at IS NULL OR absolute_expires_at >= 0),
    FOREIGN KEY(base_revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT,
    CHECK(
        (holder_user_id IS NULL AND editor_session_id IS NULL AND token_hmac IS NULL
            AND hmac_key_id IS NULL AND base_item_row_version IS NULL
            AND base_revision_id IS NULL AND acquired_at IS NULL AND heartbeat_at IS NULL
            AND expires_at IS NULL AND absolute_expires_at IS NULL)
        OR
        (holder_user_id IS NOT NULL AND editor_session_id IS NOT NULL AND token_hmac IS NOT NULL
            AND hmac_key_id IS NOT NULL AND base_item_row_version IS NOT NULL
            AND base_revision_id IS NOT NULL AND acquired_at IS NOT NULL
            AND heartbeat_at IS NOT NULL AND expires_at IS NOT NULL
            AND absolute_expires_at IS NOT NULL)
    ),
    CHECK(
        holder_user_id IS NULL OR (
            acquired_at<=heartbeat_at AND heartbeat_at<=expires_at
            AND expires_at<=absolute_expires_at
        )
    )
) STRICT
""".strip(),
    ),
    (
        "audit_event",
        """
CREATE TABLE audit_event(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uuid TEXT NOT NULL CHECK(
        length(event_uuid)=36 AND substr(event_uuid,9,1)='-'
        AND substr(event_uuid,14,1)='-' AND substr(event_uuid,19,1)='-'
        AND substr(event_uuid,24,1)='-'
        AND length(replace(event_uuid,'-',''))=32
        AND replace(event_uuid,'-','') NOT GLOB '*[^0-9a-f]*'
        AND event_uuid=lower(event_uuid)
    ),
    occurred_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(occurred_at >= 0),
    request_id TEXT NOT NULL CHECK(
        length(request_id)=36 AND substr(request_id,9,1)='-'
        AND substr(request_id,14,1)='-' AND substr(request_id,19,1)='-'
        AND substr(request_id,24,1)='-'
        AND length(replace(request_id,'-',''))=32
        AND replace(request_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND request_id=lower(request_id)
    ),
    operation_id TEXT CHECK(
        operation_id IS NULL OR (
            length(operation_id)=36 AND substr(operation_id,9,1)='-'
            AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-'
            AND substr(operation_id,24,1)='-'
            AND length(replace(operation_id,'-',''))=32
            AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND operation_id=lower(operation_id)
        )
    ),
    action TEXT NOT NULL CHECK(length(trim(action)) BETWEEN 1 AND 120),
    outcome TEXT NOT NULL CHECK(length(trim(outcome)) BETWEEN 1 AND 80),
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','system')),
    actor_user_id TEXT CHECK(
        actor_user_id IS NULL OR (
            length(actor_user_id) BETWEEN 1 AND 64
            AND actor_user_id=lower(trim(actor_user_id))
            AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    actor_display_name TEXT NOT NULL CHECK(length(trim(actor_display_name)) BETWEEN 1 AND 200),
    actor_role_snapshot TEXT NOT NULL CHECK(length(trim(actor_role_snapshot)) BETWEEN 1 AND 200),
    space_id TEXT CHECK(
        space_id IS NULL OR (
            length(space_id)=36 AND substr(space_id,9,1)='-'
            AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
            AND substr(space_id,24,1)='-'
            AND length(replace(space_id,'-',''))=32
            AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
        )
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    item_id TEXT CHECK(
        item_id IS NULL OR (
            length(item_id)=36 AND substr(item_id,9,1)='-'
            AND substr(item_id,14,1)='-' AND substr(item_id,19,1)='-'
            AND substr(item_id,24,1)='-'
            AND length(replace(item_id,'-',''))=32
            AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
        )
    ) REFERENCES knowledge_item(id) ON DELETE RESTRICT,
    base_revision_id TEXT CHECK(
        base_revision_id IS NULL OR (
            length(base_revision_id)=36 AND substr(base_revision_id,9,1)='-'
            AND substr(base_revision_id,14,1)='-' AND substr(base_revision_id,19,1)='-'
            AND substr(base_revision_id,24,1)='-'
            AND length(replace(base_revision_id,'-',''))=32
            AND replace(base_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND base_revision_id=lower(base_revision_id)
        )
    ) REFERENCES knowledge_revision(id) ON DELETE RESTRICT,
    new_revision_id TEXT CHECK(
        new_revision_id IS NULL OR (
            length(new_revision_id)=36 AND substr(new_revision_id,9,1)='-'
            AND substr(new_revision_id,14,1)='-' AND substr(new_revision_id,19,1)='-'
            AND substr(new_revision_id,24,1)='-'
            AND length(replace(new_revision_id,'-',''))=32
            AND replace(new_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND new_revision_id=lower(new_revision_id)
        )
    ) REFERENCES knowledge_revision(id) ON DELETE RESTRICT,
    reason TEXT CHECK(reason IS NULL OR length(trim(reason)) BETWEEN 1 AND 2000),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(details_json) AND json_type(details_json)='object'
    ),
    request_meta_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(request_meta_json) AND json_type(request_meta_json)='object'
    ),
    schema_version INTEGER NOT NULL DEFAULT 1 CHECK(schema_version=1),
    CHECK(
        (actor_kind='user' AND actor_user_id IS NOT NULL)
        OR (actor_kind='system' AND actor_user_id IS NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "outbox_event",
        """
CREATE TABLE outbox_event(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logical_key TEXT NOT NULL CHECK(length(trim(logical_key)) BETWEEN 1 AND 300),
    space_id TEXT CHECK(
        space_id IS NULL OR (
            length(space_id)=36 AND substr(space_id,9,1)='-'
            AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
            AND substr(space_id,24,1)='-'
            AND length(replace(space_id,'-',''))=32
            AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
        )
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    aggregate_type TEXT NOT NULL CHECK(length(trim(aggregate_type)) BETWEEN 1 AND 80),
    aggregate_id TEXT NOT NULL CHECK(length(trim(aggregate_id)) BETWEEN 1 AND 120),
    aggregate_version INTEGER NOT NULL CHECK(aggregate_version >= 0),
    event_type TEXT NOT NULL CHECK(length(trim(event_type)) BETWEEN 1 AND 120),
    payload_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(payload_json) AND json_type(payload_json)='object'
    ),
    payload_schema_version INTEGER NOT NULL DEFAULT 1 CHECK(payload_schema_version=1),
    state TEXT NOT NULL CHECK(state IN ('pending','processing','retry','applied','dead')),
    available_at INTEGER NOT NULL CHECK(available_at >= 0),
    claim_owner TEXT CHECK(
        claim_owner IS NULL OR length(trim(claim_owner)) BETWEEN 1 AND 200
    ),
    claim_expires_at INTEGER CHECK(claim_expires_at IS NULL OR claim_expires_at >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_error_json TEXT CHECK(
        last_error_json IS NULL OR (
            json_valid(last_error_json) AND json_type(last_error_json)='object'
        )
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at >= 0),
    CHECK((claim_owner IS NULL) = (claim_expires_at IS NULL)),
    CHECK((state='processing') = (claim_owner IS NOT NULL)),
    CHECK((state IN ('applied','dead')) = (completed_at IS NOT NULL))
) STRICT
""".strip(),
    ),
    (
        "durable_job",
        """
CREATE TABLE durable_job(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    dedup_id TEXT CHECK(
        dedup_id IS NULL OR (
            length(dedup_id)=36 AND substr(dedup_id,9,1)='-'
            AND substr(dedup_id,14,1)='-' AND substr(dedup_id,19,1)='-'
            AND substr(dedup_id,24,1)='-'
            AND length(replace(dedup_id,'-',''))=32
            AND replace(dedup_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND dedup_id=lower(dedup_id)
        )
    ) REFERENCES operation_dedup(id) ON DELETE RESTRICT,
    operation_id TEXT CHECK(
        operation_id IS NULL OR (
            length(operation_id)=36 AND substr(operation_id,9,1)='-'
            AND substr(operation_id,14,1)='-' AND substr(operation_id,19,1)='-'
            AND substr(operation_id,24,1)='-'
            AND length(replace(operation_id,'-',''))=32
            AND replace(operation_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND operation_id=lower(operation_id)
        )
    ),
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('user','system')),
    actor_user_id TEXT CHECK(
        actor_user_id IS NULL OR (
            length(actor_user_id) BETWEEN 1 AND 64
            AND actor_user_id=lower(trim(actor_user_id))
            AND actor_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    space_id TEXT CHECK(
        space_id IS NULL OR (
            length(space_id)=36 AND substr(space_id,9,1)='-'
            AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
            AND substr(space_id,24,1)='-'
            AND length(replace(space_id,'-',''))=32
            AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
        )
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    job_type TEXT NOT NULL CHECK(length(trim(job_type)) BETWEEN 1 AND 100),
    handler_version TEXT NOT NULL CHECK(length(trim(handler_version)) BETWEEN 1 AND 80),
    effect_key TEXT NOT NULL CHECK(length(trim(effect_key)) BETWEEN 1 AND 300),
    input_ref_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(input_ref_json) AND json_type(input_ref_json)='object'
    ),
    input_sha256 TEXT NOT NULL CHECK(
        length(input_sha256)=64 AND input_sha256=lower(input_sha256)
        AND input_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    expected_state_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(expected_state_json) AND json_type(expected_state_json)='object'
    ),
    authorization_snapshot_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(authorization_snapshot_json)
        AND json_type(authorization_snapshot_json)='object'
    ),
    authorization_snapshot_sha256 TEXT NOT NULL CHECK(
        length(authorization_snapshot_sha256)=64
        AND authorization_snapshot_sha256=lower(authorization_snapshot_sha256)
        AND authorization_snapshot_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    authorization_scope_epoch INTEGER CHECK(
        authorization_scope_epoch IS NULL OR authorization_scope_epoch >= 1
    ),
    authorization_part_id TEXT CHECK(
        authorization_part_id IS NULL OR (
            length(authorization_part_id)=36 AND substr(authorization_part_id,9,1)='-'
            AND substr(authorization_part_id,14,1)='-' AND substr(authorization_part_id,19,1)='-'
            AND substr(authorization_part_id,24,1)='-'
            AND length(replace(authorization_part_id,'-',''))=32
            AND replace(authorization_part_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND authorization_part_id=lower(authorization_part_id)
        )
    ) REFERENCES org_unit(id) ON DELETE RESTRICT,
    authorization_capabilities_json TEXT NOT NULL DEFAULT '[]' CHECK(
        json_valid(authorization_capabilities_json)
        AND json_type(authorization_capabilities_json)='array'
    ),
    state TEXT NOT NULL CHECK(state IN ('queued','claimed','running','retry','succeeded','failed','cancelled','dead')),
    claim_owner TEXT CHECK(
        claim_owner IS NULL OR length(trim(claim_owner)) BETWEEN 1 AND 200
    ),
    claim_expires_at INTEGER CHECK(claim_expires_at IS NULL OR claim_expires_at >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
    cancellable INTEGER NOT NULL DEFAULT 0 CHECK(cancellable IN (0,1)),
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
    progress_permille INTEGER NOT NULL DEFAULT 0 CHECK(progress_permille BETWEEN 0 AND 1000),
    result_json TEXT CHECK(
        result_json IS NULL OR (json_valid(result_json) AND json_type(result_json)='object')
    ),
    error_json TEXT CHECK(
        error_json IS NULL OR (json_valid(error_json) AND json_type(error_json)='object')
    ),
    available_at INTEGER NOT NULL CHECK(available_at >= 0),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at >= 0),
    CHECK(attempt_count <= max_attempts),
    CHECK((claim_owner IS NULL) = (claim_expires_at IS NULL)),
    CHECK((state IN ('claimed','running')) = (claim_owner IS NOT NULL)),
    CHECK(cancel_requested=0 OR cancellable=1),
    CHECK(state<>'cancelled' OR cancellable=1),
    CHECK(
        (state='succeeded' AND completed_at IS NOT NULL
            AND result_json IS NOT NULL AND error_json IS NULL)
        OR
        (state IN ('failed','dead') AND completed_at IS NOT NULL
            AND result_json IS NULL AND error_json IS NOT NULL)
        OR
        (state='cancelled' AND completed_at IS NOT NULL
            AND result_json IS NULL AND error_json IS NULL)
        OR
        (state NOT IN ('succeeded','failed','cancelled','dead')
            AND completed_at IS NULL AND result_json IS NULL)
    ),
    CHECK(
        (actor_kind='user' AND actor_user_id IS NOT NULL
            AND authorization_scope_epoch IS NOT NULL)
        OR
        (actor_kind='system' AND actor_user_id IS NULL
            AND authorization_scope_epoch IS NULL AND authorization_part_id IS NULL
            AND json(authorization_capabilities_json)=json('[]'))
    ),
    CHECK(
        (dedup_id IS NULL AND operation_id IS NULL)
        OR (dedup_id IS NOT NULL AND operation_id IS NOT NULL AND actor_user_id IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "projection_state",
        """
CREATE TABLE projection_state(
    projection_name TEXT NOT NULL CHECK(length(trim(projection_name)) BETWEEN 1 AND 120),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ) REFERENCES knowledge_space(id) ON DELETE RESTRICT,
    desired_generation INTEGER NOT NULL CHECK(desired_generation >= 0),
    built_generation INTEGER NOT NULL CHECK(built_generation >= 0),
    checksum_sha256 TEXT CHECK(
        checksum_sha256 IS NULL OR (
            length(checksum_sha256)=64 AND checksum_sha256=lower(checksum_sha256)
            AND checksum_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    status TEXT NOT NULL CHECK(status IN ('idle','building','ready','error')),
    last_error_json TEXT CHECK(
        last_error_json IS NULL OR (
            json_valid(last_error_json) AND json_type(last_error_json)='object'
        )
    ),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    built_at INTEGER CHECK(built_at IS NULL OR built_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    PRIMARY KEY(projection_name,space_id),
    CHECK(built_generation <= desired_generation)
) STRICT
""".strip(),
    ),
    (
        "backup_run",
        """
CREATE TABLE backup_run(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    state TEXT NOT NULL CHECK(state IN ('preparing','copying','verified','failed')),
    snapshot_db_relative_path TEXT CHECK(
        snapshot_db_relative_path IS NULL
        OR length(trim(snapshot_db_relative_path)) BETWEEN 1 AND 1000
    ),
    snapshot_db_sha256 TEXT CHECK(
        snapshot_db_sha256 IS NULL OR (
            length(snapshot_db_sha256)=64 AND snapshot_db_sha256=lower(snapshot_db_sha256)
            AND snapshot_db_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    manifest_sha256 TEXT CHECK(
        manifest_sha256 IS NULL OR (
            length(manifest_sha256)=64 AND manifest_sha256=lower(manifest_sha256)
            AND manifest_sha256 NOT GLOB '*[^0-9a-f]*'
        )
    ),
    snapshot_audit_high_water INTEGER NOT NULL DEFAULT 0
        CHECK(snapshot_audit_high_water >= 0),
    schema_version INTEGER NOT NULL CHECK(schema_version=1),
    sqlite_source_id TEXT NOT NULL CHECK(
        sqlite_source_id='2026-07-24 19:02:57 bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc'
    ),
    error_json TEXT CHECK(
        error_json IS NULL OR (json_valid(error_json) AND json_type(error_json)='object')
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    completed_at INTEGER CHECK(completed_at IS NULL OR completed_at >= 0),
    row_version INTEGER NOT NULL DEFAULT 1 CHECK(row_version >= 1),
    CHECK(
        (state='verified' AND snapshot_db_relative_path IS NOT NULL
            AND snapshot_db_sha256 IS NOT NULL AND manifest_sha256 IS NOT NULL
            AND completed_at IS NOT NULL AND error_json IS NULL)
        OR
        (state='failed' AND completed_at IS NOT NULL AND error_json IS NOT NULL)
        OR
        (state IN ('preparing','copying') AND completed_at IS NULL AND error_json IS NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "backup_blob",
        """
CREATE TABLE backup_blob(
    backup_run_id TEXT NOT NULL CHECK(
        length(backup_run_id)=36 AND substr(backup_run_id,9,1)='-'
        AND substr(backup_run_id,14,1)='-' AND substr(backup_run_id,19,1)='-'
        AND substr(backup_run_id,24,1)='-'
        AND length(replace(backup_run_id,'-',''))=32
        AND replace(backup_run_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND backup_run_id=lower(backup_run_id)
    ) REFERENCES backup_run(id) ON DELETE RESTRICT,
    blob_sha256 TEXT NOT NULL CHECK(
        length(blob_sha256)=64 AND blob_sha256=lower(blob_sha256)
        AND blob_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
    status TEXT NOT NULL CHECK(status IN ('pending','verified','failed')),
    copied_at INTEGER CHECK(copied_at IS NULL OR copied_at >= 0),
    verified_at INTEGER CHECK(verified_at IS NULL OR verified_at >= 0),
    error_json TEXT CHECK(
        error_json IS NULL OR (json_valid(error_json) AND json_type(error_json)='object')
    ),
    PRIMARY KEY(backup_run_id,blob_sha256),
    CHECK(
        (status='pending' AND verified_at IS NULL AND error_json IS NULL)
        OR
        (status='verified' AND copied_at IS NOT NULL AND verified_at IS NOT NULL
            AND error_json IS NULL)
        OR
        (status='failed' AND error_json IS NOT NULL)
    )
) STRICT
""".strip(),
    ),
    (
        "blob_gc_candidate",
        """
CREATE TABLE blob_gc_candidate(
    blob_sha256 TEXT PRIMARY KEY NOT NULL CHECK(
        length(blob_sha256)=64 AND blob_sha256=lower(blob_sha256)
        AND blob_sha256 NOT GLOB '*[^0-9a-f]*'
    ) REFERENCES blob_object(sha256) ON DELETE RESTRICT,
    unreferenced_since INTEGER NOT NULL CHECK(unreferenced_since >= 0),
    delete_after INTEGER NOT NULL CHECK(
        delete_after >= 0 AND delete_after>=unreferenced_since+86400000
    ),
    state TEXT NOT NULL CHECK(state IN ('quarantined','claimed','retry','dead')),
    claim_owner TEXT CHECK(
        claim_owner IS NULL OR length(trim(claim_owner)) BETWEEN 1 AND 200
    ),
    claim_expires_at INTEGER CHECK(claim_expires_at IS NULL OR claim_expires_at >= 0),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    last_error_json TEXT CHECK(
        last_error_json IS NULL OR (
            json_valid(last_error_json) AND json_type(last_error_json)='object'
        )
    ),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    CHECK((claim_owner IS NULL) = (claim_expires_at IS NULL)),
    CHECK((state='claimed') = (claim_owner IS NOT NULL))
) STRICT
""".strip(),
    ),
    (
        "legacy_migration_map",
        """
CREATE TABLE legacy_migration_map(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    migration_run_id TEXT NOT NULL CHECK(
        length(migration_run_id)=36 AND substr(migration_run_id,9,1)='-'
        AND substr(migration_run_id,14,1)='-' AND substr(migration_run_id,19,1)='-'
        AND substr(migration_run_id,24,1)='-'
        AND length(replace(migration_run_id,'-',''))=32
        AND replace(migration_run_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND migration_run_id=lower(migration_run_id)
    ),
    legacy_kind TEXT NOT NULL CHECK(length(trim(legacy_kind)) BETWEEN 1 AND 80),
    normalized_path TEXT NOT NULL CHECK(length(trim(normalized_path)) BETWEEN 1 AND 1000),
    source_sha256 TEXT NOT NULL CHECK(
        length(source_sha256)=64 AND source_sha256=lower(source_sha256)
        AND source_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    resolved_owner_user_id TEXT CHECK(
        resolved_owner_user_id IS NULL OR (
            length(resolved_owner_user_id) BETWEEN 1 AND 64
            AND resolved_owner_user_id=lower(trim(resolved_owner_user_id))
            AND resolved_owner_user_id NOT GLOB '*[^a-z0-9._-]*'
        )
    ) REFERENCES app_user(id) ON DELETE RESTRICT,
    artifact_id TEXT CHECK(
        artifact_id IS NULL OR (
            length(artifact_id)=36 AND substr(artifact_id,9,1)='-'
            AND substr(artifact_id,14,1)='-' AND substr(artifact_id,19,1)='-'
            AND substr(artifact_id,24,1)='-'
            AND length(replace(artifact_id,'-',''))=32
            AND replace(artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND artifact_id=lower(artifact_id)
        )
    ) REFERENCES source_artifact(id) ON DELETE RESTRICT,
    item_id TEXT CHECK(
        item_id IS NULL OR (
            length(item_id)=36 AND substr(item_id,9,1)='-'
            AND substr(item_id,14,1)='-' AND substr(item_id,19,1)='-'
            AND substr(item_id,24,1)='-'
            AND length(replace(item_id,'-',''))=32
            AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
        )
    ) REFERENCES knowledge_item(id) ON DELETE RESTRICT,
    revision_id TEXT CHECK(
        revision_id IS NULL OR (
            length(revision_id)=36 AND substr(revision_id,9,1)='-'
            AND substr(revision_id,14,1)='-' AND substr(revision_id,19,1)='-'
            AND substr(revision_id,24,1)='-'
            AND length(replace(revision_id,'-',''))=32
            AND replace(revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND revision_id=lower(revision_id)
        )
    ) REFERENCES knowledge_revision(id) ON DELETE RESTRICT,
    edge_id TEXT CHECK(
        edge_id IS NULL OR (
            length(edge_id)=36 AND substr(edge_id,9,1)='-'
            AND substr(edge_id,14,1)='-' AND substr(edge_id,19,1)='-'
            AND substr(edge_id,24,1)='-'
            AND length(replace(edge_id,'-',''))=32
            AND replace(edge_id,'-','') NOT GLOB '*[^0-9a-f]*' AND edge_id=lower(edge_id)
        )
    ) REFERENCES knowledge_edge(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status IN ('imported','quarantined','skipped_derived','failed')),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(details_json) AND json_type(details_json)='object'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at >= 0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0)
) STRICT
""".strip(),
    ),
    (
        "search_document",
        """
CREATE TABLE search_document(
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL CHECK(
        length(item_id)=36 AND substr(item_id,9,1)='-' AND substr(item_id,14,1)='-'
        AND substr(item_id,19,1)='-' AND substr(item_id,24,1)='-'
        AND length(replace(item_id,'-',''))=32
        AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*' AND item_id=lower(item_id)
    ),
    space_id TEXT NOT NULL CHECK(
        length(space_id)=36 AND substr(space_id,9,1)='-'
        AND substr(space_id,14,1)='-' AND substr(space_id,19,1)='-'
        AND substr(space_id,24,1)='-'
        AND length(replace(space_id,'-',''))=32
        AND replace(space_id,'-','') NOT GLOB '*[^0-9a-f]*' AND space_id=lower(space_id)
    ),
    revision_id TEXT NOT NULL CHECK(
        length(revision_id)=36 AND substr(revision_id,9,1)='-'
        AND substr(revision_id,14,1)='-' AND substr(revision_id,19,1)='-'
        AND substr(revision_id,24,1)='-'
        AND length(replace(revision_id,'-',''))=32
        AND replace(revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND revision_id=lower(revision_id)
    ),
    title TEXT NOT NULL,
    content_md TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at >= 0),
    FOREIGN KEY(item_id,space_id)
        REFERENCES knowledge_item(id,space_id) ON DELETE RESTRICT,
    FOREIGN KEY(revision_id,item_id)
        REFERENCES knowledge_revision(id,item_id) ON DELETE RESTRICT
) STRICT
""".strip(),
    ),
)


TABLE_NAMES: tuple[str, ...] = tuple(name for name, _sql in TABLE_DEFINITIONS)
TABLE_STATEMENTS: tuple[str, ...] = tuple(sql for _name, sql in TABLE_DEFINITIONS)


__all__ = ["TABLE_DEFINITIONS", "TABLE_NAMES", "TABLE_STATEMENTS"]
