"""Stable non-FTS trigger manifest for the physical SQLite v1 schema.

The schema contract deliberately uses only SQLite expressions, ``EXISTS``
queries, and ``RAISE(ABORT, ...)``.  No trigger depends on an application UDF or
on connection-local mutable state.  Trigger names are part of the reviewed
physical contract and therefore live beside their SQL in one deterministic
manifest.
"""

from __future__ import annotations


_DEFINITIONS: list[tuple[str, str]] = []


def _guard(
    name: str,
    timing: str,
    operation: str,
    table: str,
    invalid_when: str,
    error: str,
) -> None:
    _DEFINITIONS.append(
        (
            name,
            f"""CREATE TRIGGER {name}
{timing} {operation} ON {table}
FOR EACH ROW
WHEN {invalid_when}
BEGIN
  SELECT RAISE(ABORT, '{error}');
END""",
        )
    )


def _sql(name: str, statement: str) -> None:
    _DEFINITIONS.append((name, statement))


_ROW_VERSION_TABLES = (
    "app_user",
    "backup_run",
    "change_proposal",
    "conversation",
    "conversation_context_binding",
    "conversation_summary",
    "conversation_turn",
    "curation_finding",
    "item_relation",
    "knowledge_edge",
    "knowledge_item",
    "knowledge_space",
    "org_unit",
    "projection_state",
    "publication_preview",
)

for _table in _ROW_VERSION_TABLES:
    _guard(
        f"trg_{_table}_row_version_update",
        "BEFORE",
        "UPDATE",
        _table,
        "NEW.row_version <> OLD.row_version + 1",
        "ROW_VERSION_CONFLICT",
    )


# Organization hierarchy, current assignment, leadership, and space ownership.
_guard(
    "trg_org_unit_parent_insert",
    "BEFORE",
    "INSERT",
    "org_unit",
    """NOT (
  (NEW.kind='responsibility' AND NEW.parent_id IS NULL)
  OR (NEW.kind='team' AND EXISTS (
    SELECT 1 FROM org_unit p
    WHERE p.id=NEW.parent_id AND p.kind='responsibility' AND p.status='active'
  ))
  OR (NEW.kind='part' AND EXISTS (
    SELECT 1 FROM org_unit p
    WHERE p.id=NEW.parent_id AND p.kind='team' AND p.status='active'
  ))
)""",
    "ORG_PARENT_INVALID",
)

_sql(
    "trg_org_unit_parent_update",
    """CREATE TRIGGER trg_org_unit_parent_update
BEFORE UPDATE ON org_unit
FOR EACH ROW
BEGIN
  SELECT CASE WHEN NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind OR NOT (
    (NEW.kind='responsibility' AND NEW.parent_id IS NULL)
    OR (NEW.kind='team' AND EXISTS (
      SELECT 1 FROM org_unit p
      WHERE p.id=NEW.parent_id AND p.kind='responsibility' AND p.status='active'
    ))
    OR (NEW.kind='part' AND EXISTS (
      SELECT 1 FROM org_unit p
      WHERE p.id=NEW.parent_id AND p.kind='team' AND p.status='active'
    ))
  ) THEN RAISE(ABORT, 'ORG_PARENT_INVALID') END;
  SELECT CASE WHEN NEW.parent_id IS NOT OLD.parent_id AND EXISTS (
    WITH RECURSIVE ancestors(id,parent_id) AS (
      SELECT id,parent_id FROM org_unit WHERE id=NEW.parent_id
      UNION ALL
      SELECT p.id,p.parent_id FROM org_unit p JOIN ancestors a ON p.id=a.parent_id
    )
    SELECT 1 FROM ancestors WHERE id=NEW.id
  ) THEN RAISE(ABORT, 'ORG_CYCLE') END;
END""",
)

_guard(
    "trg_org_unit_inactivate_guard_update",
    "BEFORE",
    "UPDATE",
    "org_unit",
    """OLD.status='active' AND NEW.status='inactive' AND (
  (OLD.kind='part' AND (
    EXISTS (SELECT 1 FROM app_user u WHERE u.current_part_id=OLD.id)
    OR EXISTS (SELECT 1 FROM org_role_assignment r
               WHERE r.org_unit_id=OLD.id AND r.valid_to IS NULL)
    OR EXISTS (SELECT 1 FROM knowledge_space s
               WHERE s.owner_org_unit_id=OLD.id AND s.space_kind='part'
                 AND s.status='active')
  ))
  OR (OLD.kind IN ('responsibility','team') AND EXISTS (
    SELECT 1 FROM org_unit c WHERE c.parent_id=OLD.id AND c.status='active'
  ))
)""",
    "ORG_UNIT_IN_USE",
)

_guard(
    "trg_app_user_part_insert",
    "BEFORE",
    "INSERT",
    "app_user",
    """NEW.current_part_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM org_unit p
  WHERE p.id=NEW.current_part_id AND p.kind='part' AND p.status='active'
)""",
    "USER_PART_INVALID",
)

_sql(
    "trg_app_user_part_update",
    """CREATE TRIGGER trg_app_user_part_update
BEFORE UPDATE ON app_user
FOR EACH ROW
BEGIN
  SELECT CASE WHEN NEW.id IS NOT OLD.id OR (
    NEW.current_part_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM org_unit p
      WHERE p.id=NEW.current_part_id AND p.kind='part' AND p.status='active'
    )
  ) THEN RAISE(ABORT, 'USER_PART_INVALID') END;
  SELECT CASE WHEN (
    ((NEW.current_part_id IS NOT OLD.current_part_id)
      OR (NEW.account_status IS NOT OLD.account_status))
    AND NEW.scope_epoch <> OLD.scope_epoch + 1
  ) OR (
    NEW.current_part_id IS OLD.current_part_id
    AND NEW.account_status IS OLD.account_status
    AND NEW.scope_epoch <> OLD.scope_epoch
  ) THEN RAISE(ABORT, 'SCOPE_EPOCH_INVALID') END;
END""",
)

_guard(
    "trg_app_user_leader_guard_update",
    "BEFORE",
    "UPDATE",
    "app_user",
    """(NEW.current_part_id IS NOT OLD.current_part_id
      OR (OLD.account_status='active' AND NEW.account_status='disabled'))
AND EXISTS (
  SELECT 1 FROM org_role_assignment r
  WHERE r.user_id=OLD.id AND r.valid_to IS NULL
)""",
    "LEADER_WRITE_GUARD",
)

_guard(
    "trg_org_membership_validity_insert",
    "BEFORE",
    "INSERT",
    "org_membership",
    """NOT EXISTS (
  SELECT 1 FROM org_unit p
  WHERE p.id=NEW.part_id AND p.kind='part'
    AND (NEW.valid_to IS NOT NULL OR p.status='active')
)""",
    "MEMBERSHIP_INVALID",
)

_guard(
    "trg_org_membership_history_update",
    "BEFORE",
    "UPDATE",
    "org_membership",
    """OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
OR NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id
OR NEW.part_id IS NOT OLD.part_id OR NEW.valid_from IS NOT OLD.valid_from
OR NEW.assigned_by IS NOT OLD.assigned_by OR NEW.reason IS NOT OLD.reason""",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_org_membership_leader_guard_update",
    "BEFORE",
    "UPDATE",
    "org_membership",
    """OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL AND EXISTS (
  SELECT 1 FROM org_role_assignment r
  WHERE r.user_id=OLD.user_id AND r.org_unit_id=OLD.part_id
    AND r.valid_to IS NULL
)""",
    "LEADER_WRITE_GUARD",
)

_guard(
    "trg_org_membership_history_delete",
    "BEFORE",
    "DELETE",
    "org_membership",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_org_role_assignment_validity_insert",
    "BEFORE",
    "INSERT",
    "org_role_assignment",
    """NOT EXISTS (
  SELECT 1 FROM org_unit p
  WHERE p.id=NEW.org_unit_id AND p.kind='part' AND p.status='active'
) OR (NEW.valid_to IS NULL AND NOT EXISTS (
  SELECT 1 FROM app_user u
  WHERE u.id=NEW.user_id AND u.account_status='active'
    AND u.current_part_id=NEW.org_unit_id
))""",
    "MEMBERSHIP_INVALID",
)

_guard(
    "trg_org_role_assignment_history_update",
    "BEFORE",
    "UPDATE",
    "org_role_assignment",
    """OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL
OR NEW.id IS NOT OLD.id OR NEW.user_id IS NOT OLD.user_id
OR NEW.org_unit_id IS NOT OLD.org_unit_id OR NEW.role IS NOT OLD.role
OR NEW.valid_from IS NOT OLD.valid_from OR NEW.assigned_by IS NOT OLD.assigned_by
OR NEW.reason IS NOT OLD.reason""",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_org_role_assignment_leader_write_guard_update",
    "BEFORE",
    "UPDATE",
    "org_role_assignment",
    """OLD.valid_to IS NULL AND NEW.valid_to IS NOT NULL AND EXISTS (
  SELECT 1 FROM knowledge_space s
  WHERE s.space_kind='part' AND s.owner_org_unit_id=OLD.org_unit_id
    AND s.write_enabled=1
)""",
    "LEADER_WRITE_GUARD",
)

_guard(
    "trg_org_role_assignment_history_delete",
    "BEFORE",
    "DELETE",
    "org_role_assignment",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_knowledge_space_owner_insert",
    "BEFORE",
    "INSERT",
    "knowledge_space",
    """NOT (
  (NEW.space_kind='personal' AND NEW.owner_user_id IS NOT NULL
    AND NEW.owner_org_unit_id IS NULL)
  OR (NEW.space_kind IN ('part','team','responsibility')
    AND NEW.owner_user_id IS NULL AND EXISTS (
      SELECT 1 FROM org_unit o
      WHERE o.id=NEW.owner_org_unit_id AND o.kind=NEW.space_kind
    ))
) OR (NEW.write_enabled=1 AND (
  NEW.status<>'active' OR (NEW.space_kind='part' AND NOT EXISTS (
    SELECT 1 FROM org_role_assignment r
    WHERE r.org_unit_id=NEW.owner_org_unit_id AND r.role='part_leader'
      AND r.valid_to IS NULL
  ))
))""",
    "SPACE_OWNER_INVALID",
)

_guard(
    "trg_knowledge_space_owner_update",
    "BEFORE",
    "UPDATE",
    "knowledge_space",
    """NEW.id IS NOT OLD.id OR NEW.space_kind IS NOT OLD.space_kind
OR NEW.owner_user_id IS NOT OLD.owner_user_id
OR NEW.owner_org_unit_id IS NOT OLD.owner_org_unit_id
OR (NEW.write_enabled=1 AND (
  NEW.status<>'active' OR (NEW.space_kind='part' AND NOT EXISTS (
    SELECT 1 FROM org_role_assignment r
    WHERE r.org_unit_id=NEW.owner_org_unit_id AND r.role='part_leader'
      AND r.valid_to IS NULL
  ))
))""",
    "SPACE_OWNER_INVALID",
)

for _operation in ("INSERT", "UPDATE"):
    _guard(
        f"trg_knowledge_space_kind_disabled_{_operation.lower()}",
        "BEFORE",
        _operation,
        "knowledge_space",
        "NEW.space_kind IN ('team','responsibility') AND NEW.write_enabled=1",
        "SPACE_KIND_DISABLED",
    )


# Conversation identity, append-only message/event history, and bounded state.
_guard(
    "trg_conversation_scope_update",
    "BEFORE",
    "UPDATE",
    "conversation",
    """NEW.id IS NOT OLD.id OR NEW.owner_user_id IS NOT OLD.owner_user_id
OR NEW.space_id IS NOT OLD.space_id
OR NEW.captured_scope_epoch IS NOT OLD.captured_scope_epoch""",
    "CONVERSATION_SCOPE_IMMUTABLE",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_conversation_message_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "conversation_message",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_conversation_turn_validity_insert",
    "BEFORE",
    "INSERT",
    "conversation_turn",
    """NEW.state<>'accepted' OR NEW.row_version<>1
OR NEW.updated_at<>NEW.created_at
OR NOT EXISTS (
  SELECT 1 FROM conversation c
  WHERE c.id=NEW.conversation_id AND c.owner_user_id=NEW.actor_user_id
) OR NOT EXISTS (
  SELECT 1 FROM conversation_message m
  WHERE m.id=NEW.user_message_id AND m.conversation_id=NEW.conversation_id
    AND m.role='user'
) OR NOT EXISTS (
  SELECT 1 FROM operation_dedup d
  WHERE d.id=NEW.dedup_id AND d.actor_user_id=NEW.actor_user_id
    AND d.operation_type='conversation_turn'
    AND d.operation_id=NEW.operation_id AND d.request_sha256=NEW.request_sha256
    AND d.status='in_progress'
)""",
    "CONVERSATION_TURN_INVALID",
)

_guard(
    "trg_conversation_turn_validity_update",
    "BEFORE",
    "UPDATE",
    "conversation_turn",
    """NEW.id IS NOT OLD.id OR NEW.conversation_id IS NOT OLD.conversation_id
OR NEW.actor_user_id IS NOT OLD.actor_user_id OR NEW.dedup_id IS NOT OLD.dedup_id
OR NEW.operation_id IS NOT OLD.operation_id OR NEW.request_sha256 IS NOT OLD.request_sha256
OR NEW.user_message_id IS NOT OLD.user_message_id OR NOT (
  (OLD.state='accepted' AND NEW.state IN ('running','failed','cancelled'))
  OR (OLD.state='running' AND NEW.state IN ('completed','failed','cancelled'))
) OR (NEW.assistant_message_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM conversation_message m
  WHERE m.id=NEW.assistant_message_id AND m.conversation_id=NEW.conversation_id
    AND m.role='assistant'
))""",
    "CONVERSATION_TURN_INVALID",
)

_guard(
    "trg_conversation_turn_event_order_insert",
    "BEFORE",
    "INSERT",
    "conversation_turn_event",
    """NEW.sequence_no <> COALESCE((
  SELECT MAX(e.sequence_no)+1 FROM conversation_turn_event e
  WHERE e.turn_id=NEW.turn_id
),1)
OR (NEW.sequence_no=1 AND NEW.event_type<>'accepted')
OR (NEW.sequence_no>1 AND EXISTS (
  SELECT 1 FROM conversation_turn_event e
  WHERE e.turn_id=NEW.turn_id
    AND e.event_type IN ('completed','failed','cancelled')
)) OR NOT EXISTS (
  SELECT 1 FROM conversation_turn t
  WHERE t.id=NEW.turn_id AND t.conversation_id=NEW.conversation_id
    AND (
      (NEW.event_type='accepted' AND t.state='accepted')
      OR (NEW.event_type IN ('delta','tool') AND t.state='running')
      OR (NEW.event_type='completed' AND t.state='completed')
      OR (NEW.event_type='failed' AND t.state='failed')
      OR (NEW.event_type='cancelled' AND t.state='cancelled')
    )
)""",
    "CONVERSATION_TURN_INVALID",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_conversation_turn_event_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "conversation_turn_event",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_conversation_context_binding_validity_insert",
    "BEFORE",
    "INSERT",
    "conversation_context_binding",
    """(NEW.binding_mode='snapshot' AND (
  NEW.source_artifact_id IS NULL OR NEW.snapshot_sha256 IS NULL
  OR NEW.status NOT IN ('active','detached')
  OR NOT EXISTS (
    SELECT 1 FROM source_artifact a
    WHERE a.id=NEW.source_artifact_id AND a.raw_sha256=NEW.snapshot_sha256
    UNION ALL
    SELECT 1 FROM extraction_snapshot x
    WHERE x.artifact_id=NEW.source_artifact_id AND x.status='ready'
      AND (x.extracted_sha256=NEW.snapshot_sha256
        OR x.normalized_content_sha256=NEW.snapshot_sha256)
  )
)) OR (NEW.binding_mode='live' AND (
  NEW.source_artifact_id IS NOT NULL OR NEW.snapshot_sha256 IS NOT NULL
)) OR (NEW.binding_type='source_artifact' AND NEW.binding_mode<>'snapshot')""",
    "CONVERSATION_CONTEXT_INVALID",
)

_guard(
    "trg_conversation_context_binding_validity_update",
    "BEFORE",
    "UPDATE",
    "conversation_context_binding",
    """NEW.id IS NOT OLD.id OR NEW.conversation_id IS NOT OLD.conversation_id
OR NEW.binding_type IS NOT OLD.binding_type OR NEW.binding_mode IS NOT OLD.binding_mode
OR NEW.external_key IS NOT OLD.external_key
OR NEW.source_artifact_id IS NOT OLD.source_artifact_id
OR NEW.snapshot_sha256 IS NOT OLD.snapshot_sha256
OR NEW.display_metadata_json IS NOT OLD.display_metadata_json
OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
OR NOT (
  (OLD.status='active' AND NEW.status IN ('unavailable','detached'))
  OR (OLD.status='unavailable' AND NEW.status='unavailable'
    AND NEW.status_reason IS NOT OLD.status_reason)
  OR (OLD.status='unavailable' AND NEW.status IN ('active','detached'))
)""",
    "CONVERSATION_CONTEXT_INVALID",
)

_guard(
    "trg_conversation_context_binding_history_delete",
    "BEFORE",
    "DELETE",
    "conversation_context_binding",
    "1",
    "CONVERSATION_CONTEXT_INVALID",
)

_guard(
    "trg_conversation_summary_validity_insert",
    "BEFORE",
    "INSERT",
    "conversation_summary",
    """NEW.status<>'active' OR NEW.row_version<>1
OR NOT EXISTS (
  SELECT 1 FROM conversation_message m
  WHERE m.conversation_id=NEW.conversation_id
    AND m.sequence_no=NEW.covered_through_sequence_no
)""",
    "CONVERSATION_CONTEXT_INVALID",
)

_guard(
    "trg_conversation_summary_validity_update",
    "BEFORE",
    "UPDATE",
    "conversation_summary",
    """NEW.id IS NOT OLD.id OR NEW.conversation_id IS NOT OLD.conversation_id
OR NEW.covered_through_sequence_no IS NOT OLD.covered_through_sequence_no
OR NEW.summary_md IS NOT OLD.summary_md OR NEW.summary_sha256 IS NOT OLD.summary_sha256
OR NEW.model_id IS NOT OLD.model_id OR NEW.prompt_version IS NOT OLD.prompt_version
OR NEW.summarizer_version IS NOT OLD.summarizer_version
OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
OR NEW.created_at IS NOT OLD.created_at
OR NOT (OLD.status='active' AND NEW.status='invalidated')""",
    "CONVERSATION_CONTEXT_INVALID",
)

_guard(
    "trg_conversation_summary_history_delete",
    "BEFORE",
    "DELETE",
    "conversation_summary",
    "1",
    "CONVERSATION_CONTEXT_INVALID",
)


# Immutable artifacts and the two narrowly authorized body-retention paths.
_ARTIFACT_REFERENCE_BLOCKERS = """
  EXISTS (
    SELECT 1 FROM item_source s JOIN knowledge_item i ON i.id=s.item_id
    WHERE s.artifact_id={artifact} AND i.lifecycle_status<>'purged_tombstone'
  )
  OR EXISTS (
    SELECT 1 FROM lineage_group g JOIN lineage_member m ON m.lineage_group_id=g.id
      JOIN knowledge_item i ON i.id=m.item_id
    WHERE (g.initial_artifact_id={artifact} OR g.base_artifact_id={artifact})
      AND i.lifecycle_status<>'purged_tombstone'
  )
  OR EXISTS (
    SELECT 1 FROM publication_preview p
    WHERE p.artifact_id={artifact} AND p.status IN ('queued','extracting','ready')
  )
  OR EXISTS (
    SELECT 1 FROM conversation_context_binding b
    WHERE b.source_artifact_id={artifact} AND b.binding_mode='snapshot'
      AND b.status<>'detached'
  )
  OR EXISTS (
    SELECT 1 FROM change_proposal p
    WHERE p.source_artifact_id={artifact}
      AND p.state IN ('pending_review','approved','apply_failed')
  )
"""

_ARTIFACT_ALL_REFERENCED_ITEMS_PURGED = """
  NOT EXISTS (
    SELECT 1 FROM (
      SELECT s.item_id AS item_id FROM item_source s WHERE s.artifact_id={artifact}
      UNION
      SELECT m.item_id FROM lineage_group g
        JOIN lineage_member m ON m.lineage_group_id=g.id
      WHERE g.initial_artifact_id={artifact} OR g.base_artifact_id={artifact}
    ) refs
    JOIN knowledge_item i ON i.id=refs.item_id
    WHERE i.lifecycle_status<>'purged_tombstone'
       OR NOT EXISTS (
         SELECT 1 FROM item_deactivation_episode e
           JOIN app_user a ON a.id=e.purged_by
         WHERE e.id=i.latest_deactivation_episode_id AND e.item_id=i.id
           AND e.restored_at IS NULL AND e.purged_at IS NOT NULL
           AND e.purged_at>=e.purge_eligible_at
           AND e.purged_at<=CAST(unixepoch('subsec') * 1000 AS INTEGER)
           AND a.account_status='active' AND a.system_role='admin'
       )
  )
"""


def _artifact_retention_allowed(artifact: str) -> str:
    blockers = _ARTIFACT_REFERENCE_BLOCKERS.format(artifact=artifact)
    all_purged = _ARTIFACT_ALL_REFERENCED_ITEMS_PURGED.format(artifact=artifact)
    return f"""(
  NOT EXISTS (SELECT 1 FROM backup_run r WHERE r.state IN ('preparing','copying'))
  AND NOT ({blockers})
  AND (
    (
      EXISTS (
        SELECT 1 FROM source_artifact a
        WHERE a.id={artifact}
          AND CAST(unixepoch('subsec') * 1000 AS INTEGER)>=a.orphan_cleanup_eligible_at
      )
      AND NOT EXISTS (SELECT 1 FROM item_source s WHERE s.artifact_id={artifact})
      AND NOT EXISTS (
        SELECT 1 FROM lineage_group g
        WHERE g.initial_artifact_id={artifact} OR g.base_artifact_id={artifact}
      )
    )
    OR (
      EXISTS (
        SELECT 1 FROM item_source s WHERE s.artifact_id={artifact}
        UNION ALL
        SELECT 1 FROM lineage_group g
        WHERE g.initial_artifact_id={artifact} OR g.base_artifact_id={artifact}
      )
      AND {all_purged}
    )
  )
)"""


def _artifact_body_delete_allowed(artifact: str, blob_hash: str) -> str:
    return f"""(
  {_artifact_retention_allowed(artifact)}
  AND NOT EXISTS (
    SELECT 1 FROM backup_run r JOIN backup_blob b ON b.backup_run_id=r.id
    WHERE r.state IN ('preparing','copying') AND b.blob_sha256={blob_hash}
  )
)"""


_guard(
    "trg_source_artifact_validity_insert",
    "BEFORE",
    "INSERT",
    "source_artifact",
    """NEW.body_retention_status<>'present'
OR NEW.body_retention_reason IS NOT NULL OR NEW.body_purged_at IS NOT NULL""",
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_source_artifact_retention_update",
    "BEFORE",
    "UPDATE",
    "source_artifact",
    """NEW.id IS NOT OLD.id OR NEW.origin_type IS NOT OLD.origin_type
OR NEW.origin_key IS NOT OLD.origin_key
OR NEW.origin_revision_key IS NOT OLD.origin_revision_key
OR NEW.raw_sha256 IS NOT OLD.raw_sha256 OR NEW.raw_size_bytes IS NOT OLD.raw_size_bytes
OR NEW.media_type IS NOT OLD.media_type
OR NEW.original_filename IS NOT OLD.original_filename
OR NEW.publisher_user_id IS NOT OLD.publisher_user_id
OR NEW.source_owner_snapshot_json IS NOT OLD.source_owner_snapshot_json
OR NEW.metadata_json IS NOT OLD.metadata_json
OR NEW.metadata_schema_version IS NOT OLD.metadata_schema_version
OR NEW.orphan_cleanup_eligible_at IS NOT OLD.orphan_cleanup_eligible_at
OR NEW.created_at IS NOT OLD.created_at
OR OLD.body_retention_status<>'present' OR NEW.body_retention_status<>'purged'
OR NEW.body_retention_reason NOT IN ('eligible_item_purge','orphan_ingestion_cleanup')
OR NEW.body_purged_at IS NULL
OR EXISTS (
  SELECT 1 FROM artifact_blob b JOIN artifact_blob_body bb ON bb.artifact_blob_id=b.id
  WHERE b.artifact_id=OLD.id
)
OR EXISTS (
  SELECT 1 FROM extraction_snapshot x
    JOIN extraction_snapshot_body xb ON xb.snapshot_id=x.id AND xb.artifact_id=x.artifact_id
  WHERE x.artifact_id=OLD.id
)"""
    + f""" OR NOT {_artifact_retention_allowed('OLD.id')}
OR EXISTS (
  SELECT 1 FROM artifact_blob b
  WHERE b.artifact_id=OLD.id AND b.body_retention_status<>'purged'
)
OR EXISTS (
  SELECT 1 FROM extraction_snapshot x
  WHERE x.artifact_id=OLD.id AND x.status='ready'
    AND x.body_retention_status<>'purged'
)
OR (NEW.body_retention_reason='orphan_ingestion_cleanup' AND (
  EXISTS (SELECT 1 FROM item_source s WHERE s.artifact_id=OLD.id)
  OR EXISTS (
    SELECT 1 FROM lineage_group g
    WHERE g.initial_artifact_id=OLD.id OR g.base_artifact_id=OLD.id
  )
))
OR (NEW.body_retention_reason='eligible_item_purge' AND NOT (
  EXISTS (SELECT 1 FROM item_source s WHERE s.artifact_id=OLD.id)
  OR EXISTS (
    SELECT 1 FROM lineage_group g
    WHERE g.initial_artifact_id=OLD.id OR g.base_artifact_id=OLD.id
  )
))""",
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_source_artifact_history_delete",
    "BEFORE",
    "DELETE",
    "source_artifact",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_artifact_blob_validity_insert",
    "BEFORE",
    "INSERT",
    "artifact_blob",
    """NEW.body_retention_status<>'present' OR (
  NEW.role='raw' AND (
    NEW.ordinal<>0 OR NOT EXISTS (
      SELECT 1 FROM source_artifact a
      WHERE a.id=NEW.artifact_id AND a.body_retention_status='present'
        AND a.raw_sha256=NEW.content_sha256 AND a.raw_size_bytes=NEW.size_bytes
        AND a.media_type=NEW.media_type
    )
  )
)""",
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_artifact_blob_retention_update",
    "BEFORE",
    "UPDATE",
    "artifact_blob",
    """NEW.id IS NOT OLD.id OR NEW.artifact_id IS NOT OLD.artifact_id
OR NEW.content_sha256 IS NOT OLD.content_sha256 OR NEW.size_bytes IS NOT OLD.size_bytes
OR NEW.role IS NOT OLD.role OR NEW.logical_name IS NOT OLD.logical_name
OR NEW.media_type IS NOT OLD.media_type OR NEW.ordinal IS NOT OLD.ordinal
OR NEW.created_at IS NOT OLD.created_at
OR OLD.body_retention_status<>'present' OR NEW.body_retention_status<>'purged'
OR EXISTS (SELECT 1 FROM artifact_blob_body b WHERE b.artifact_blob_id=OLD.id)
OR NOT """
    + _artifact_retention_allowed("OLD.artifact_id"),
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_artifact_blob_history_delete",
    "BEFORE",
    "DELETE",
    "artifact_blob",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_artifact_blob_body_validity_insert",
    "BEFORE",
    "INSERT",
    "artifact_blob_body",
    """NOT EXISTS (
  SELECT 1 FROM artifact_blob b JOIN source_artifact a ON a.id=b.artifact_id
  WHERE b.id=NEW.artifact_blob_id AND b.content_sha256=NEW.content_sha256
    AND b.body_retention_status='present' AND a.body_retention_status='present'
)""",
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_artifact_blob_body_history_update",
    "BEFORE",
    "UPDATE",
    "artifact_blob_body",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_artifact_blob_body_retention_delete",
    "BEFORE",
    "DELETE",
    "artifact_blob_body",
    """NOT """
    + _artifact_body_delete_allowed(
        "(SELECT artifact_id FROM artifact_blob WHERE id=OLD.artifact_blob_id)",
        "OLD.content_sha256",
    ),
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_extraction_snapshot_validity_insert",
    "BEFORE",
    "INSERT",
    "extraction_snapshot",
    "NEW.status='ready' AND NEW.body_retention_status<>'present'",
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_extraction_snapshot_retention_update",
    "BEFORE",
    "UPDATE",
    "extraction_snapshot",
    """NEW.id IS NOT OLD.id OR NEW.artifact_id IS NOT OLD.artifact_id
OR NEW.status IS NOT OLD.status OR NEW.extracted_sha256 IS NOT OLD.extracted_sha256
OR NEW.normalized_content_sha256 IS NOT OLD.normalized_content_sha256
OR NEW.extracted_size_bytes IS NOT OLD.extracted_size_bytes
OR NEW.normalizer_version IS NOT OLD.normalizer_version
OR NEW.extractor_name IS NOT OLD.extractor_name
OR NEW.extractor_version IS NOT OLD.extractor_version
OR NEW.warnings_json IS NOT OLD.warnings_json OR NEW.created_at IS NOT OLD.created_at
OR OLD.body_retention_status<>'present' OR NEW.body_retention_status<>'purged'
OR EXISTS (
  SELECT 1 FROM extraction_snapshot_body b
  WHERE b.snapshot_id=OLD.id AND b.artifact_id=OLD.artifact_id
) OR NOT """
    + _artifact_retention_allowed("OLD.artifact_id"),
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_extraction_snapshot_history_delete",
    "BEFORE",
    "DELETE",
    "extraction_snapshot",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_extraction_snapshot_body_validity_insert",
    "BEFORE",
    "INSERT",
    "extraction_snapshot_body",
    """NOT EXISTS (
  SELECT 1 FROM extraction_snapshot x
  WHERE x.id=NEW.snapshot_id AND x.artifact_id=NEW.artifact_id
    AND x.status='ready' AND x.body_retention_status='present'
    AND x.extracted_size_bytes=NEW.content_size_bytes
    AND x.extracted_sha256=NEW.content_sha256
)""",
    "ARTIFACT_BODY_RETENTION_INVALID",
)

_guard(
    "trg_extraction_snapshot_body_history_update",
    "BEFORE",
    "UPDATE",
    "extraction_snapshot_body",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_extraction_snapshot_body_retention_delete",
    "BEFORE",
    "DELETE",
    "extraction_snapshot_body",
    """NOT """
    + _artifact_body_delete_allowed(
        "OLD.artifact_id", "COALESCE(OLD.content_blob_hash,OLD.content_sha256)"
    ),
    "ARTIFACT_BODY_RETENTION_INVALID",
)


# Durable publication preview and immutable publication/lineage history.
_guard(
    "trg_publication_preview_validity_insert",
    "BEFORE",
    "INSERT",
    "publication_preview",
    """NEW.row_version<>1 OR NEW.updated_at<>NEW.created_at
OR NEW.status NOT IN ('queued','ready')
OR NOT EXISTS (
  SELECT 1 FROM app_user u
  WHERE u.id=NEW.actor_user_id
    AND u.current_part_id IS NEW.captured_part_id
    AND u.scope_epoch=NEW.captured_scope_epoch
) OR NOT EXISTS (
  SELECT 1 FROM source_artifact a
  WHERE a.id=NEW.artifact_id AND a.body_retention_status='present'
) OR NOT EXISTS (
  SELECT 1 FROM json_each(NEW.available_targets_json) WHERE value='personal'
) OR EXISTS (
  SELECT value FROM json_each(NEW.available_targets_json)
  WHERE value NOT IN ('personal','part','both')
) OR EXISTS (
  SELECT value FROM json_each(NEW.available_targets_json)
  GROUP BY value HAVING count(*)<>1
) OR (NEW.captured_part_id IS NULL AND EXISTS (
  SELECT 1 FROM json_each(NEW.available_targets_json)
  WHERE value IN ('part','both')
)) OR (NEW.status='queued' AND (
  NEW.durable_job_id IS NULL OR NOT EXISTS (
  SELECT 1 FROM durable_job j JOIN app_user u ON u.id=j.actor_user_id
  WHERE j.id=NEW.durable_job_id AND j.job_type='publication_preview.extract'
    AND j.handler_version='v1'
    AND j.actor_kind='user' AND j.actor_user_id=NEW.actor_user_id
    AND j.authorization_scope_epoch=NEW.captured_scope_epoch
    AND j.authorization_part_id IS NEW.captured_part_id
    AND j.state='queued'
    AND (SELECT count(*) FROM json_each(j.input_ref_json))=3
    AND NOT EXISTS (
      SELECT 1 FROM json_each(j.input_ref_json)
      WHERE key NOT IN ('preview_id','artifact_id','rule_version')
    )
    AND json_extract(j.input_ref_json,'$.preview_id')=NEW.id
    AND json_extract(j.input_ref_json,'$.artifact_id')=NEW.artifact_id
    AND json_extract(j.input_ref_json,'$.rule_version')=NEW.rule_version
    AND (SELECT count(*) FROM json_each(j.authorization_snapshot_json))=9
    AND NOT EXISTS (
      SELECT 1 FROM json_each(j.authorization_snapshot_json)
      WHERE key NOT IN ('schema_version','actor_kind','actor_user_id',
        'account_status','system_role','current_part_id','scope_epoch','space_id',
        'capabilities')
    )
    AND json_extract(j.authorization_snapshot_json,'$.schema_version')=1
    AND json_extract(j.authorization_snapshot_json,'$.actor_kind')='user'
    AND json_extract(j.authorization_snapshot_json,'$.actor_user_id')=j.actor_user_id
    AND json_extract(j.authorization_snapshot_json,'$.account_status')=u.account_status
    AND json_extract(j.authorization_snapshot_json,'$.system_role')=u.system_role
    AND json_extract(j.authorization_snapshot_json,'$.current_part_id')
      IS j.authorization_part_id
    AND json_extract(j.authorization_snapshot_json,'$.scope_epoch')
      =j.authorization_scope_epoch
    AND json_extract(j.authorization_snapshot_json,'$.space_id') IS j.space_id
    AND json(json_extract(j.authorization_snapshot_json,'$.capabilities'))
      =json(j.authorization_capabilities_json)
  )
)) OR (NEW.status='ready' AND (
  NEW.durable_job_id IS NOT NULL OR NEW.completed_at<>NEW.created_at
  OR NOT EXISTS (
    SELECT 1 FROM extraction_snapshot x
      JOIN extraction_snapshot_body b
        ON b.snapshot_id=x.id AND b.artifact_id=x.artifact_id
    WHERE x.id=NEW.extraction_snapshot_id AND x.artifact_id=NEW.artifact_id
      AND x.status='ready' AND x.body_retention_status='present'
      AND x.extracted_sha256=NEW.source_snapshot_sha256
  )
))""",
    "PUBLICATION_PREVIEW_INVALID",
)

_guard(
    "trg_publication_preview_validity_update",
    "BEFORE",
    "UPDATE",
    "publication_preview",
    """NEW.id IS NOT OLD.id OR NEW.actor_user_id IS NOT OLD.actor_user_id
OR NEW.artifact_id IS NOT OLD.artifact_id
OR NEW.durable_job_id IS NOT OLD.durable_job_id
OR NEW.captured_part_id IS NOT OLD.captured_part_id
OR NEW.captured_scope_epoch IS NOT OLD.captured_scope_epoch
OR NEW.available_targets_json IS NOT OLD.available_targets_json
OR NEW.rule_version IS NOT OLD.rule_version
OR NEW.result_schema_version IS NOT OLD.result_schema_version
OR NEW.expires_at IS NOT OLD.expires_at OR NEW.created_at IS NOT OLD.created_at
OR NOT (
  (OLD.status='queued' AND NEW.status IN ('extracting','cancelled','failed'))
  OR (OLD.status='extracting' AND NEW.status IN ('ready','cancelled','failed'))
  OR (OLD.status='ready' AND NEW.status IN ('consumed','expired','cancelled'))
) OR (NEW.status='ready' AND NOT EXISTS (
  SELECT 1 FROM extraction_snapshot x
    JOIN extraction_snapshot_body b
      ON b.snapshot_id=x.id AND b.artifact_id=x.artifact_id
    JOIN durable_job j ON j.id=NEW.durable_job_id
  WHERE x.id=NEW.extraction_snapshot_id AND x.artifact_id=NEW.artifact_id
    AND x.status='ready' AND x.body_retention_status='present'
    AND x.extracted_sha256=NEW.source_snapshot_sha256
    AND j.job_type='publication_preview.extract' AND j.handler_version='v1'
    AND j.actor_kind='user' AND j.actor_user_id=NEW.actor_user_id
    AND j.authorization_scope_epoch=NEW.captured_scope_epoch
    AND j.authorization_part_id IS NEW.captured_part_id
    AND j.state='running'
    AND json_extract(j.input_ref_json,'$.preview_id')=NEW.id
    AND json_extract(j.input_ref_json,'$.artifact_id')=NEW.artifact_id
    AND json_extract(j.input_ref_json,'$.rule_version')=NEW.rule_version
)) OR (NEW.status='cancelled' AND OLD.status IN ('queued','extracting') AND (
  NEW.extraction_snapshot_id IS NOT NULL
  OR NEW.source_snapshot_sha256 IS NOT NULL
  OR NEW.deterministic_findings_json IS NOT NULL
  OR NEW.ready_result_json IS NOT NULL
)) OR (OLD.status='ready' AND (
  NEW.extraction_snapshot_id IS NOT OLD.extraction_snapshot_id
  OR NEW.source_snapshot_sha256 IS NOT OLD.source_snapshot_sha256
  OR NEW.deterministic_findings_json IS NOT OLD.deterministic_findings_json
  OR NEW.ready_result_json IS NOT OLD.ready_result_json
  OR NEW.completed_at IS NOT OLD.completed_at
))""",
    "PUBLICATION_PREVIEW_INVALID",
)

_guard(
    "trg_publication_preview_history_delete",
    "BEFORE",
    "DELETE",
    "publication_preview",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_lineage_group_validity_insert",
    "BEFORE",
    "INSERT",
    "lineage_group",
    """NOT EXISTS (
  SELECT 1 FROM extraction_snapshot x
  WHERE x.id=NEW.base_extraction_snapshot_id AND x.artifact_id=NEW.base_artifact_id
    AND x.status='ready' AND x.normalized_content_sha256=NEW.base_content_sha256
)""",
    "LINEAGE_MEMBER_INVALID",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_lineage_group_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "lineage_group",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_lineage_member_validity_insert",
    "BEFORE",
    "INSERT",
    "lineage_member",
    """NOT EXISTS (
  SELECT 1 FROM knowledge_item i
  WHERE i.id=NEW.item_id AND i.lineage_group_id=NEW.lineage_group_id
) OR NOT EXISTS (
  SELECT 1 FROM knowledge_revision r
  WHERE r.id=NEW.initial_revision_id AND r.item_id=NEW.item_id
) OR ((NEW.member_role='origin') <> (
  NEW.derived_from_item_id IS NULL AND NEW.derived_from_revision_id IS NULL
)) OR (NEW.derived_from_item_id IS NOT NULL AND (
  NEW.derived_from_item_id=NEW.item_id OR NOT EXISTS (
    SELECT 1 FROM lineage_member m
    WHERE m.lineage_group_id=NEW.lineage_group_id
      AND m.item_id=NEW.derived_from_item_id
  )
))""",
    "LINEAGE_MEMBER_INVALID",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_lineage_member_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "lineage_member",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_operation_dedup_validity_update",
    "BEFORE",
    "UPDATE",
    "operation_dedup",
    """NEW.id IS NOT OLD.id OR NEW.actor_user_id IS NOT OLD.actor_user_id
OR NEW.operation_type IS NOT OLD.operation_type
OR NEW.operation_id IS NOT OLD.operation_id
OR NEW.request_sha256 IS NOT OLD.request_sha256 OR NEW.created_at IS NOT OLD.created_at
OR NOT (
  (OLD.status='in_progress' AND NEW.status IN ('committed','failed_final'))
  OR (OLD.status=NEW.status AND OLD.status IN ('committed','failed_final')
      AND NEW.http_status IS OLD.http_status
      AND NEW.result_json IS OLD.result_json AND NEW.error_json IS OLD.error_json
      AND NEW.completed_at IS OLD.completed_at)
) OR NEW.last_seen_at<OLD.last_seen_at""",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_operation_dedup_history_delete",
    "BEFORE",
    "DELETE",
    "operation_dedup",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_publication_operation_validity_insert",
    "BEFORE",
    "INSERT",
    "publication_operation",
    """NOT EXISTS (
  SELECT 1 FROM operation_dedup d
  WHERE d.id=NEW.dedup_id AND d.actor_user_id=NEW.actor_user_id
    AND d.operation_type='publication' AND d.operation_id=NEW.operation_id
    AND d.request_sha256=NEW.request_sha256 AND d.status='in_progress'
) OR ((NEW.target_mode='both') <> (NEW.lineage_group_id IS NOT NULL))
OR (NEW.lineage_group_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM lineage_group g
  WHERE g.id=NEW.lineage_group_id AND g.initial_artifact_id=NEW.artifact_id
    AND g.base_artifact_id=NEW.artifact_id
    AND g.base_extraction_snapshot_id=NEW.extraction_snapshot_id
))""",
    "HISTORY_IMMUTABLE",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_publication_operation_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "publication_operation",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_publication_target_validity_insert",
    "BEFORE",
    "INSERT",
    "publication_target",
    """NEW.outcome='update_proposed' AND NOT EXISTS (
  SELECT 1 FROM curation_finding f
  WHERE f.id=NEW.curation_finding_id AND f.space_id=NEW.target_space_id
    AND f.source_item_id=NEW.item_id
    AND f.source_revision_id=NEW.resolved_revision_id
    AND f.target_item_id IS NULL AND f.target_revision_id IS NULL
    AND f.finding_kind='update' AND f.status='pending'
    AND f.risk_tier='T2' AND f.decision_mode='human'
    AND f.required_reviewer='space_editor' AND f.proposed_action='update'
)""",
    "HISTORY_IMMUTABLE",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_publication_target_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "publication_target",
        "1",
        "HISTORY_IMMUTABLE",
    )


# Item lifecycle and curation are event-backed; content history stays immutable.
_guard(
    "trg_knowledge_item_identity_update",
    "BEFORE",
    "UPDATE",
    "knowledge_item",
    """NEW.id IS NOT OLD.id OR NEW.space_id IS NOT OLD.space_id
OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at""",
    "ITEM_STATE_INVALID",
)

_guard(
    "trg_knowledge_item_lifecycle_update",
    "BEFORE",
    "UPDATE",
    "knowledge_item",
    """NEW.lifecycle_status IS NOT OLD.lifecycle_status AND (
  NOT (
    (OLD.lifecycle_status='active' AND NEW.lifecycle_status IN ('inactive','merged'))
    OR (OLD.lifecycle_status='inactive' AND NEW.lifecycle_status IN ('active','purged_tombstone'))
  )
  OR (NEW.lifecycle_status='purged_tombstone' AND (
    NEW.current_revision_id IS NOT NULL
    OR NEW.curation_status IS NOT OLD.curation_status
    OR NEW.last_curation_transition_id IS NOT OLD.last_curation_transition_id
    OR NEW.latest_deactivation_episode_id IS NOT OLD.latest_deactivation_episode_id
    OR NOT EXISTS (
      SELECT 1 FROM item_deactivation_episode e
        JOIN app_user a ON a.id=e.purged_by
      WHERE e.id=NEW.latest_deactivation_episode_id AND e.item_id=NEW.id
        AND e.restored_by IS NULL AND e.restored_at IS NULL
        AND e.purged_by IS NOT NULL AND e.purged_at IS NOT NULL
        AND e.purged_at>=e.purge_eligible_at
        AND e.purged_at<=CAST(unixepoch('subsec') * 1000 AS INTEGER)
        AND a.account_status='active' AND a.system_role='admin'
    )
  ))
)""",
    "ITEM_STATE_INVALID",
)

_guard(
    "trg_knowledge_item_curation_update",
    "BEFORE",
    "UPDATE",
    "knowledge_item",
    """(
  NEW.current_revision_id IS NOT OLD.current_revision_id
  OR NEW.curation_status IS NOT OLD.curation_status
) AND NOT (
  OLD.lifecycle_status='inactive' AND NEW.lifecycle_status='purged_tombstone'
  AND OLD.current_revision_id IS NOT NULL AND NEW.current_revision_id IS NULL
  AND NEW.curation_status=OLD.curation_status
  AND NEW.last_curation_transition_id IS OLD.last_curation_transition_id
) AND NOT EXISTS (
  SELECT 1 FROM item_curation_transition t
  WHERE t.id=NEW.last_curation_transition_id AND t.item_id=NEW.id
    AND t.space_id=NEW.space_id AND t.from_status=OLD.curation_status
    AND t.to_status=NEW.curation_status
    AND t.base_revision_id=OLD.current_revision_id
    AND t.result_revision_id=NEW.current_revision_id
    AND t.result_item_row_version=NEW.row_version
)""",
    "ITEM_CURATION_INVALID",
)

_guard(
    "trg_knowledge_item_transition_pointer_update",
    "BEFORE",
    "UPDATE",
    "knowledge_item",
    """NEW.current_revision_id IS OLD.current_revision_id
AND NEW.curation_status IS OLD.curation_status
AND NOT (
  OLD.lifecycle_status='inactive' AND NEW.lifecycle_status='purged_tombstone'
) AND NEW.last_curation_transition_id IS NOT OLD.last_curation_transition_id""",
    "ITEM_CURATION_INVALID",
)

_guard(
    "trg_knowledge_item_history_delete",
    "BEFORE",
    "DELETE",
    "knowledge_item",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_item_curation_transition_validity_insert",
    "BEFORE",
    "INSERT",
    "item_curation_transition",
    """NOT EXISTS (
  SELECT 1 FROM knowledge_item i
  WHERE i.id=NEW.item_id AND i.space_id=NEW.space_id
    AND i.curation_status=NEW.from_status
    AND i.current_revision_id=NEW.base_revision_id
    AND NEW.result_item_row_version=i.row_version+1
) OR (
  NEW.transition_kind='finding_decision' AND NOT (
    NEW.from_status='provisional'
    AND NEW.to_status IN ('published','quarantined')
    AND NEW.base_revision_id=NEW.result_revision_id
    AND NEW.finding_id IS NOT NULL AND NEW.rescan_job_id IS NULL
    AND (
      (NEW.actor_kind='user' AND NEW.actor_user_id IS NOT NULL
        AND NEW.dedup_id IS NOT NULL AND NEW.operation_id IS NOT NULL
        AND NEW.decision_job_id IS NULL
        AND EXISTS (
          SELECT 1 FROM curation_finding f
          WHERE f.id=NEW.finding_id AND f.space_id=NEW.space_id
            AND f.source_item_id=NEW.item_id
            AND f.source_revision_id=NEW.result_revision_id
            AND f.target_item_id IS NULL AND f.target_revision_id IS NULL
            AND f.status='applied' AND f.applied_job_id IS NULL
            AND f.applied_dedup_id=NEW.dedup_id
            AND f.applied_operation_id=NEW.operation_id
            AND ((NEW.to_status='published'
                  AND f.finding_kind='curation_release'
                  AND f.proposed_action='publish')
              OR (NEW.to_status='quarantined'
                  AND f.finding_kind='curation_quarantine'
                  AND f.proposed_action='quarantine'))
        ))
      OR (NEW.actor_kind='system' AND NEW.actor_user_id IS NULL
        AND NEW.dedup_id IS NULL AND NEW.operation_id IS NULL
        AND NEW.decision_job_id IS NOT NULL
        AND EXISTS (
          SELECT 1 FROM durable_job j JOIN curation_finding f
            ON f.id=NEW.finding_id
          WHERE j.id=NEW.decision_job_id AND j.job_type='curation.rescan'
            AND j.handler_version='v1'
            AND j.actor_kind='system' AND j.state='running'
            AND j.space_id=NEW.space_id
            AND json_extract(j.input_ref_json,'$.item_id')=NEW.item_id
            AND json_extract(j.input_ref_json,'$.revision_id')=NEW.result_revision_id
            AND json_extract(j.input_ref_json,'$.rule_version')=f.rule_version
            AND f.space_id=NEW.space_id AND f.source_item_id=NEW.item_id
            AND f.source_revision_id=NEW.result_revision_id
            AND f.status='applied' AND f.applied_job_id=NEW.decision_job_id
            AND f.decision_mode='automatic' AND f.required_reviewer='none'
            AND ((NEW.to_status='published'
                  AND f.finding_kind='curation_clear' AND f.severity='info'
                  AND f.risk_tier='T1' AND f.visibility_effect='none'
                  AND f.proposed_action='publish')
              OR (NEW.to_status='quarantined'
                  AND f.finding_kind='lint' AND f.severity IN ('blocker','error')
                  AND f.risk_tier='T0'
                  AND f.visibility_effect='publication_blocked'
                  AND f.proposed_action='quarantine'))
        ))
    )
  )
) OR (
  NEW.transition_kind='revision_rescan' AND NOT (
    NEW.from_status IN ('published','provisional','quarantined')
    AND NEW.to_status='provisional'
    AND NEW.base_revision_id<>NEW.result_revision_id
    AND NEW.finding_id IS NULL AND NEW.decision_job_id IS NULL
    AND NEW.rescan_job_id IS NOT NULL AND NEW.actor_kind='user'
    AND NEW.actor_user_id IS NOT NULL AND NEW.dedup_id IS NOT NULL
    AND NEW.operation_id IS NOT NULL
    AND EXISTS (
      SELECT 1 FROM durable_job j
      WHERE j.id=NEW.rescan_job_id AND j.job_type='curation.rescan'
        AND j.handler_version='v1'
        AND j.state='queued' AND j.actor_kind='user'
        AND j.actor_user_id=NEW.actor_user_id AND j.space_id=NEW.space_id
        AND json_extract(j.input_ref_json,'$.item_id')=NEW.item_id
        AND json_extract(j.input_ref_json,'$.revision_id')=NEW.result_revision_id
    )
  )
)""",
    "ITEM_CURATION_INVALID",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_item_curation_transition_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "item_curation_transition",
        "1",
        "HISTORY_IMMUTABLE",
    )

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_knowledge_revision_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "knowledge_revision",
        "1",
        "REVISION_BODY_IMMUTABLE",
    )

_guard(
    "trg_knowledge_revision_body_validity_insert",
    "BEFORE",
    "INSERT",
    "knowledge_revision_body",
    """NOT EXISTS (
  SELECT 1 FROM knowledge_revision r JOIN knowledge_item i ON i.id=r.item_id
  WHERE r.id=NEW.revision_id AND r.item_id=NEW.item_id
    AND i.lifecycle_status<>'purged_tombstone'
    AND r.content_size_bytes=NEW.content_size_bytes
    AND r.raw_content_sha256=NEW.content_sha256
)""",
    "REVISION_BODY_IMMUTABLE",
)

_guard(
    "trg_knowledge_revision_body_history_update",
    "BEFORE",
    "UPDATE",
    "knowledge_revision_body",
    "1",
    "REVISION_BODY_IMMUTABLE",
)

_guard(
    "trg_knowledge_revision_body_purge_delete",
    "BEFORE",
    "DELETE",
    "knowledge_revision_body",
    """NOT EXISTS (
  SELECT 1 FROM knowledge_item i
    JOIN item_deactivation_episode e
      ON e.id=i.latest_deactivation_episode_id AND e.item_id=i.id
    JOIN app_user a ON a.id=e.purged_by
  WHERE i.id=OLD.item_id AND i.lifecycle_status='purged_tombstone'
    AND i.current_revision_id IS NULL
    AND e.restored_by IS NULL AND e.restored_at IS NULL
    AND e.purged_by IS NOT NULL AND e.purged_at IS NOT NULL
    AND e.purged_at>=e.purge_eligible_at
    AND e.purged_at<=CAST(unixepoch('subsec') * 1000 AS INTEGER)
    AND a.account_status='active' AND a.system_role='admin'
)""",
    "REVISION_BODY_IMMUTABLE",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_item_source_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "item_source",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_item_relation_validity_update",
    "BEFORE",
    "UPDATE",
    "item_relation",
    """NEW.id IS NOT OLD.id OR NEW.source_item_id IS NOT OLD.source_item_id
OR NEW.source_revision_id IS NOT OLD.source_revision_id
OR NEW.target_item_id IS NOT OLD.target_item_id
OR NEW.target_revision_id IS NOT OLD.target_revision_id
OR NEW.relation_type IS NOT OLD.relation_type
OR NEW.evidence_json IS NOT OLD.evidence_json OR NEW.created_by IS NOT OLD.created_by
OR NEW.created_at IS NOT OLD.created_at
OR NOT (OLD.relation_status='active' AND NEW.relation_status='inactive')""",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_item_relation_history_delete",
    "BEFORE",
    "DELETE",
    "item_relation",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_item_deactivation_episode_validity_insert",
    "BEFORE",
    "INSERT",
    "item_deactivation_episode",
    """NEW.restored_by IS NOT NULL OR NEW.restored_at IS NOT NULL
OR NEW.purged_by IS NOT NULL OR NEW.purged_at IS NOT NULL
OR NEW.purge_eligible_at<>NEW.deactivated_at+7776000000
OR NEW.episode_no<>COALESCE((
  SELECT MAX(e.episode_no)+1 FROM item_deactivation_episode e
  WHERE e.item_id=NEW.item_id
),1)""",
    "ITEM_STATE_INVALID",
)

_guard(
    "trg_item_deactivation_episode_validity_update",
    "BEFORE",
    "UPDATE",
    "item_deactivation_episode",
    """NEW.id IS NOT OLD.id OR NEW.item_id IS NOT OLD.item_id
OR NEW.episode_no IS NOT OLD.episode_no
OR NEW.deactivated_by IS NOT OLD.deactivated_by
OR NEW.deactivated_at IS NOT OLD.deactivated_at OR NEW.reason IS NOT OLD.reason
OR NEW.purge_eligible_at IS NOT OLD.purge_eligible_at
OR NOT (
  (OLD.restored_by IS NULL AND OLD.restored_at IS NULL
    AND OLD.purged_by IS NULL AND OLD.purged_at IS NULL
    AND (
      (NEW.restored_by IS NOT NULL AND NEW.restored_at IS NOT NULL
        AND NEW.purged_by IS NULL AND NEW.purged_at IS NULL)
      OR (NEW.restored_by IS NULL AND NEW.restored_at IS NULL
        AND NEW.purged_by IS NOT NULL AND NEW.purged_at IS NOT NULL
        AND NEW.purged_at>=OLD.purge_eligible_at
        AND NEW.purged_at<=CAST(unixepoch('subsec') * 1000 AS INTEGER)
        AND EXISTS (
          SELECT 1 FROM app_user a WHERE a.id=NEW.purged_by
            AND a.account_status='active' AND a.system_role='admin'
        ))
    )
  )
)""",
    "ITEM_STATE_INVALID",
)

_guard(
    "trg_item_deactivation_episode_history_delete",
    "BEFORE",
    "DELETE",
    "item_deactivation_episode",
    "1",
    "HISTORY_IMMUTABLE",
)


_guard(
    "trg_knowledge_edge_approval_insert",
    "BEFORE",
    "INSERT",
    "knowledge_edge",
    """NEW.needs_revalidation<>0
OR (NEW.origin='semantic_approved' AND NOT EXISTS (
  SELECT 1 FROM curation_finding f
  WHERE f.id=NEW.approval_finding_id AND f.finding_kind='semantic_edge'
    AND f.risk_tier='T2' AND f.decision_mode='human'
    AND f.required_reviewer='space_editor' AND f.proposed_action='relation'
    AND f.status='applied' AND f.space_id=NEW.space_id
    AND f.source_item_id=NEW.source_item_id
    AND f.source_revision_id=NEW.source_evidence_revision_id
    AND f.target_item_id=NEW.target_item_id
    AND f.target_revision_id=NEW.target_evidence_revision_id
))""",
    "CURATION_POLICY_INVALID",
)

_guard(
    "trg_knowledge_edge_validity_update",
    "BEFORE",
    "UPDATE",
    "knowledge_edge",
    """NEW.id IS NOT OLD.id OR NEW.space_id IS NOT OLD.space_id
OR NEW.source_item_id IS NOT OLD.source_item_id
OR NEW.target_item_id IS NOT OLD.target_item_id OR NEW.edge_type IS NOT OLD.edge_type
OR NEW.origin IS NOT OLD.origin
OR NEW.source_evidence_revision_id IS NOT OLD.source_evidence_revision_id
OR NEW.target_evidence_revision_id IS NOT OLD.target_evidence_revision_id
OR NEW.approval_finding_id IS NOT OLD.approval_finding_id
OR NEW.evidence_json IS NOT OLD.evidence_json OR NEW.created_by IS NOT OLD.created_by
OR NEW.created_at IS NOT OLD.created_at
OR (NEW.edge_status='active' AND NEW.needs_revalidation<>0)
OR NOT (
  (NEW.edge_status=OLD.edge_status
    OR (OLD.edge_status='active' AND NEW.edge_status='inactive')
    OR (OLD.edge_status='inactive' AND NEW.edge_status='active'))
  AND (NEW.needs_revalidation=OLD.needs_revalidation
    OR (OLD.needs_revalidation=0 AND NEW.needs_revalidation=1)
    OR (OLD.needs_revalidation=1 AND NEW.needs_revalidation=0
      AND (
        NEW.origin='manual'
        OR (NEW.approval_finding_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM curation_finding f
          JOIN knowledge_item s ON s.id=NEW.source_item_id
          JOIN knowledge_item t ON t.id=NEW.target_item_id
        WHERE f.id=NEW.approval_finding_id AND f.status='applied'
          AND f.source_item_id=NEW.source_item_id
          AND f.source_revision_id=NEW.source_evidence_revision_id
          AND f.target_item_id=NEW.target_item_id
          AND f.target_revision_id=NEW.target_evidence_revision_id
          AND s.current_revision_id=NEW.source_evidence_revision_id
          AND t.current_revision_id=NEW.target_evidence_revision_id
        ))
      )))
)""",
    "CURATION_POLICY_INVALID",
)

_guard(
    "trg_knowledge_edge_history_delete",
    "BEFORE",
    "DELETE",
    "knowledge_edge",
    "1",
    "HISTORY_IMMUTABLE",
)


_FINDING_POLICY_VALID = """CASE
  WHEN NEW.finding_kind='lint' AND NEW.severity='blocker' AND NEW.risk_tier='T0'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='publication_blocked' AND NEW.proposed_action='quarantine' THEN 1
  WHEN NEW.finding_kind='lint' AND NEW.severity='error' AND NEW.risk_tier='T0'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='publication_blocked' AND NEW.proposed_action='quarantine' THEN 1
  WHEN NEW.finding_kind='lint' AND NEW.severity='warning' AND NEW.risk_tier='T0'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='badge' AND NEW.proposed_action='none' THEN 1
  WHEN NEW.finding_kind='lint' AND NEW.severity='info' AND NEW.risk_tier='T0'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='none' THEN 1
  WHEN NEW.finding_kind='exact_repeat' AND NEW.severity='info' AND NEW.risk_tier='T0'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='none' THEN 1
  WHEN NEW.finding_kind='exact_repeat' AND NEW.severity='info' AND NEW.risk_tier='T1'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='provenance' THEN 1
  WHEN NEW.finding_kind='curation_clear' AND NEW.severity='info' AND NEW.risk_tier='T1'
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='publish' THEN 1
  WHEN NEW.finding_kind='weak_complementary' AND NEW.severity='warning' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='badge' AND NEW.proposed_action='none' THEN 1
  WHEN NEW.finding_kind='near_duplicate' AND NEW.severity='warning' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='review_duplicate' THEN 1
  WHEN NEW.finding_kind='update' AND NEW.severity='info' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='update' THEN 1
  WHEN NEW.finding_kind='potential_conflict' AND NEW.severity='error' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='review_conflict' THEN 1
  WHEN NEW.finding_kind='semantic_edge' AND NEW.severity='info' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='relation' THEN 1
  WHEN NEW.finding_kind='replace' AND NEW.severity='warning' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='replace' THEN 1
  WHEN NEW.finding_kind='merge' AND NEW.severity='warning' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='merge' THEN 1
  WHEN NEW.finding_kind='curation_release' AND NEW.severity='info' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='publish' THEN 1
  WHEN NEW.finding_kind='curation_quarantine' AND NEW.severity='error' AND NEW.risk_tier='T2'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='agent_hidden' AND NEW.proposed_action='quarantine' THEN 1
  WHEN NEW.finding_kind='deactivate' AND NEW.severity='warning' AND NEW.risk_tier='T3'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='space_editor'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='deactivate' THEN 1
  WHEN NEW.finding_kind='purge' AND NEW.severity='warning' AND NEW.risk_tier='T3'
    AND NEW.decision_mode='human' AND NEW.required_reviewer='admin'
    AND NEW.visibility_effect='none' AND NEW.proposed_action='purge' THEN 1
  ELSE 0 END"""

_FINDING_APPLICATION_SHAPE_INVALID = """NOT (
  (NEW.status='applied' AND NEW.last_application_error_json IS NULL AND (
    (NEW.applied_operation_id IS NOT NULL AND NEW.applied_dedup_id IS NOT NULL
      AND NEW.applied_by IS NOT NULL AND NEW.applied_at IS NOT NULL
      AND NEW.applied_job_id IS NULL)
    OR (NEW.applied_operation_id IS NULL AND NEW.applied_dedup_id IS NULL
      AND NEW.applied_by IS NULL AND NEW.applied_at IS NOT NULL
      AND NEW.applied_job_id IS NOT NULL AND NEW.risk_tier IN ('T0','T1')
      AND NEW.decision_mode='automatic')
  ))
  OR (NEW.status='apply_failed' AND NEW.applied_operation_id IS NOT NULL
    AND NEW.applied_dedup_id IS NOT NULL AND NEW.applied_by IS NOT NULL
    AND NEW.applied_at IS NOT NULL AND NEW.applied_job_id IS NULL
    AND NEW.last_application_error_json IS NOT NULL)
  OR (NEW.status NOT IN ('applied','apply_failed')
    AND NEW.applied_operation_id IS NULL AND NEW.applied_dedup_id IS NULL
    AND NEW.applied_job_id IS NULL AND NEW.applied_by IS NULL
    AND NEW.applied_at IS NULL AND NEW.last_application_error_json IS NULL)
)"""

_FINDING_INSERT_STATE_INVALID = """NOT (
  (NEW.status='pending'
    AND NEW.reviewed_by IS NULL AND NEW.reviewed_at IS NULL
    AND NEW.review_reason IS NULL)
  OR
  (NEW.status='applied' AND NEW.risk_tier IN ('T0','T1')
    AND NEW.decision_mode='automatic' AND NEW.required_reviewer='none'
    AND NEW.reviewed_by IS NULL AND NEW.reviewed_at IS NULL
    AND NEW.review_reason IS NULL
    AND (
      (NEW.applied_operation_id IS NOT NULL
        AND NEW.applied_dedup_id IS NOT NULL
        AND NEW.applied_by IS NOT NULL AND NEW.applied_job_id IS NULL
        AND NEW.created_actor_kind='user' AND NEW.created_by=NEW.applied_by
        AND EXISTS (
          SELECT 1 FROM operation_dedup d
          WHERE d.id=NEW.applied_dedup_id AND d.actor_user_id=NEW.applied_by
            AND d.operation_id=NEW.applied_operation_id
            AND d.status IN ('in_progress','committed')
            AND d.operation_type=CASE
              WHEN NEW.finding_kind IN ('curation_release','curation_quarantine')
                THEN 'item_curation'
              WHEN NEW.finding_kind IN ('exact_repeat','curation_clear','lint')
                THEN 'publication'
              ELSE 'curation_finding'
            END
        )
        AND EXISTS (
          SELECT 1 FROM app_user u JOIN knowledge_space s ON s.id=NEW.space_id
          WHERE u.id=NEW.applied_by AND u.account_status='active'
            AND ((s.space_kind='personal' AND s.owner_user_id=u.id)
              OR (s.space_kind='part' AND s.owner_org_unit_id=u.current_part_id))
        ))
      OR
      (NEW.applied_operation_id IS NULL AND NEW.applied_dedup_id IS NULL
        AND NEW.applied_by IS NULL AND NEW.applied_job_id IS NOT NULL
        AND NEW.created_actor_kind='system' AND NEW.created_by IS NULL
        AND EXISTS (
          SELECT 1 FROM durable_job j
          WHERE j.id=NEW.applied_job_id AND j.actor_kind='system'
            AND j.job_type='curation.rescan' AND j.state='running'
            AND j.space_id=NEW.space_id
            AND j.handler_version='v1'
            AND (SELECT count(*) FROM json_each(j.input_ref_json))=3
            AND NOT EXISTS (
              SELECT 1 FROM json_each(j.input_ref_json)
              WHERE key NOT IN ('item_id','revision_id','rule_version')
            )
            AND json_extract(j.input_ref_json,'$.item_id')=NEW.source_item_id
            AND json_extract(j.input_ref_json,'$.revision_id')=NEW.source_revision_id
            AND json_extract(j.input_ref_json,'$.rule_version')=NEW.rule_version
            AND (SELECT count(*) FROM json_each(j.authorization_snapshot_json))=2
            AND NOT EXISTS (
              SELECT 1 FROM json_each(j.authorization_snapshot_json)
              WHERE key NOT IN ('schema_version','actor_kind')
            )
            AND json_extract(j.authorization_snapshot_json,'$.schema_version')=1
            AND json_extract(j.authorization_snapshot_json,'$.actor_kind')='system'
            AND json(j.authorization_capabilities_json)=json('[]')
        ))
    )
  )
)"""


def _finding_cross_table_invalid() -> str:
    return """NOT EXISTS (
  SELECT 1 FROM knowledge_item i JOIN knowledge_revision r ON r.item_id=i.id
  WHERE i.id=NEW.source_item_id AND i.space_id=NEW.space_id
    AND r.id=NEW.source_revision_id
) OR (NEW.target_item_id IS NOT NULL AND (
  NEW.target_item_id=NEW.source_item_id OR NOT EXISTS (
    SELECT 1 FROM knowledge_item i JOIN knowledge_revision r ON r.item_id=i.id
    WHERE i.id=NEW.target_item_id AND i.space_id=NEW.space_id
      AND r.id=NEW.target_revision_id
  )
))"""


_guard(
    "trg_curation_finding_policy_insert",
    "BEFORE",
    "INSERT",
    "curation_finding",
    f"""({_FINDING_POLICY_VALID})<>1 OR {_finding_cross_table_invalid()}
OR ((NEW.created_actor_kind='user') <> (NEW.created_by IS NOT NULL))
OR NEW.row_version<>1 OR NEW.updated_at<>NEW.created_at
OR {_FINDING_APPLICATION_SHAPE_INVALID}
OR {_FINDING_INSERT_STATE_INVALID}""",
    "CURATION_POLICY_INVALID",
)

_guard(
    "trg_curation_finding_policy_update",
    "BEFORE",
    "UPDATE",
    "curation_finding",
    f"""({_FINDING_POLICY_VALID})<>1
OR NEW.id IS NOT OLD.id OR NEW.space_id IS NOT OLD.space_id
OR NEW.finding_kind IS NOT OLD.finding_kind OR NEW.risk_tier IS NOT OLD.risk_tier
OR NEW.severity IS NOT OLD.severity OR NEW.decision_mode IS NOT OLD.decision_mode
OR NEW.required_reviewer IS NOT OLD.required_reviewer
OR NEW.visibility_effect IS NOT OLD.visibility_effect
OR NEW.proposed_action IS NOT OLD.proposed_action
OR NEW.source_item_id IS NOT OLD.source_item_id
OR NEW.source_revision_id IS NOT OLD.source_revision_id
OR NEW.target_item_id IS NOT OLD.target_item_id
OR NEW.target_revision_id IS NOT OLD.target_revision_id
OR NEW.detector_name IS NOT OLD.detector_name
OR NEW.detector_version IS NOT OLD.detector_version
OR NEW.rule_version IS NOT OLD.rule_version OR NEW.model_id IS NOT OLD.model_id
OR NEW.prompt_version IS NOT OLD.prompt_version OR NEW.confidence IS NOT OLD.confidence
OR NEW.evidence_json IS NOT OLD.evidence_json
OR NEW.proposed_action_json IS NOT OLD.proposed_action_json
OR NEW.created_actor_kind IS NOT OLD.created_actor_kind
OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
OR {_FINDING_APPLICATION_SHAPE_INVALID}""",
    "CURATION_POLICY_INVALID",
)

_guard(
    "trg_curation_finding_state_update",
    "BEFORE",
    "UPDATE",
    "curation_finding",
    """NOT (
  (OLD.status='pending' AND NEW.status IN
    ('approved','rejected','ignored','obsolete','applied'))
  OR (OLD.status='approved' AND NEW.status IN ('applied','apply_failed','obsolete'))
  OR (OLD.status='apply_failed' AND NEW.status IN ('approved','rejected','obsolete'))
) OR (OLD.status='pending' AND NEW.status='applied'
  AND NOT (NEW.risk_tier IN ('T0','T1') AND NEW.decision_mode='automatic'))
OR (NEW.status IN ('approved','rejected','ignored','apply_failed')
  AND (NEW.reviewed_by IS NULL OR NEW.reviewed_at IS NULL
    OR NEW.review_reason IS NULL))
OR (OLD.status='apply_failed' AND NEW.status='approved' AND (
  NEW.applied_operation_id IS NOT NULL OR NEW.applied_dedup_id IS NOT NULL
  OR NEW.applied_job_id IS NOT NULL OR NEW.applied_by IS NOT NULL
  OR NEW.applied_at IS NOT NULL OR NEW.last_application_error_json IS NOT NULL
)) OR (NEW.applied_dedup_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM operation_dedup d
  WHERE d.id=NEW.applied_dedup_id AND d.actor_user_id=NEW.applied_by
    AND d.operation_id=NEW.applied_operation_id
    AND d.operation_type=CASE
      WHEN NEW.finding_kind IN ('curation_release','curation_quarantine')
        THEN 'item_curation'
      WHEN NEW.finding_kind='semantic_edge' THEN 'semantic_edge'
      WHEN NEW.finding_kind='update' THEN 'proposal_apply'
      WHEN NEW.finding_kind='merge' THEN 'merge'
      WHEN NEW.finding_kind IN ('exact_repeat','curation_clear','lint')
        THEN 'publication'
      ELSE 'curation_finding'
    END
)) OR (NEW.applied_job_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM durable_job j
  WHERE j.id=NEW.applied_job_id AND j.actor_kind='system'
    AND j.job_type='curation.rescan' AND j.handler_version='v1'
    AND j.state='running' AND j.space_id=NEW.space_id
    AND (SELECT count(*) FROM json_each(j.input_ref_json))=3
    AND NOT EXISTS (
      SELECT 1 FROM json_each(j.input_ref_json)
      WHERE key NOT IN ('item_id','revision_id','rule_version')
    )
    AND json_extract(j.input_ref_json,'$.item_id')=NEW.source_item_id
    AND json_extract(j.input_ref_json,'$.revision_id')=NEW.source_revision_id
    AND json_extract(j.input_ref_json,'$.rule_version')=NEW.rule_version
    AND (SELECT count(*) FROM json_each(j.authorization_snapshot_json))=2
    AND NOT EXISTS (
      SELECT 1 FROM json_each(j.authorization_snapshot_json)
      WHERE key NOT IN ('schema_version','actor_kind')
    )
    AND json_extract(j.authorization_snapshot_json,'$.schema_version')=1
    AND json_extract(j.authorization_snapshot_json,'$.actor_kind')='system'
    AND json(j.authorization_capabilities_json)=json('[]')
)) OR (NEW.reviewed_by IS NOT NULL AND NOT (
  (NEW.required_reviewer='admin' AND EXISTS (
    SELECT 1 FROM app_user u WHERE u.id=NEW.reviewed_by
      AND u.account_status='active' AND u.system_role='admin'
  ))
  OR (NEW.required_reviewer='space_editor' AND EXISTS (
    SELECT 1 FROM app_user u JOIN knowledge_space s ON s.id=NEW.space_id
    WHERE u.id=NEW.reviewed_by AND u.account_status='active'
      AND ((s.space_kind='personal' AND s.owner_user_id=u.id)
        OR (s.space_kind='part' AND s.owner_org_unit_id=u.current_part_id))
  ))
  OR (NEW.required_reviewer='part_leader' AND EXISTS (
    SELECT 1 FROM app_user u JOIN knowledge_space s ON s.id=NEW.space_id
      JOIN org_role_assignment r
        ON r.org_unit_id=s.owner_org_unit_id AND r.user_id=u.id
    WHERE u.id=NEW.reviewed_by AND u.account_status='active'
      AND s.space_kind='part' AND r.valid_to IS NULL
  ))
)) OR (NEW.decision_mode='human' AND NEW.status IN ('applied','apply_failed')
  AND NEW.applied_by IS NOT NEW.reviewed_by)""",
    "CURATION_POLICY_INVALID",
)

_guard(
    "trg_curation_finding_history_delete",
    "BEFORE",
    "DELETE",
    "curation_finding",
    "1",
    "HISTORY_IMMUTABLE",
)


_guard(
    "trg_change_proposal_validity_insert",
    "BEFORE",
    "INSERT",
    "change_proposal",
    """NOT EXISTS (
  SELECT 1 FROM curation_finding f
  WHERE f.id=NEW.finding_id AND f.finding_kind='update'
    AND f.risk_tier='T2' AND f.decision_mode='human'
    AND f.required_reviewer='space_editor' AND f.proposed_action='update'
    AND f.row_version=NEW.finding_input_row_version
    AND f.source_item_id=NEW.target_item_id
    AND f.source_revision_id=NEW.target_revision_id
    AND f.target_item_id IS NULL AND f.target_revision_id IS NULL
    AND f.status='pending' AND NEW.state='pending_review'
) OR (NEW.proposal_version=1 AND (
  NEW.proposal_series_id<>NEW.id OR NEW.supersedes_proposal_id IS NOT NULL
)) OR (NEW.proposal_version>1 AND NOT EXISTS (
  SELECT 1 FROM change_proposal p
  WHERE p.id=NEW.supersedes_proposal_id
    AND p.proposal_series_id=NEW.proposal_series_id
    AND p.proposal_version=NEW.proposal_version-1
)) OR (NEW.proposal_kind='stable_source_update' AND NOT (
  NEW.lineage_group_id IS NULL AND NEW.source_item_id IS NULL
  AND NEW.source_revision_id IS NULL AND NEW.source_artifact_id IS NOT NULL
  AND NEW.extraction_snapshot_id IS NOT NULL
  AND NEW.base_item_id=NEW.target_item_id
  AND NEW.base_revision_id=NEW.target_revision_id
  AND EXISTS (
    SELECT 1 FROM extraction_snapshot x
    WHERE x.id=NEW.extraction_snapshot_id AND x.artifact_id=NEW.source_artifact_id
      AND x.status='ready'
  )
)) OR (NEW.proposal_kind='personal_to_part' AND NOT (
  NEW.lineage_group_id IS NOT NULL AND NEW.source_item_id IS NOT NULL
  AND NEW.source_revision_id IS NOT NULL AND NEW.source_artifact_id IS NULL
  AND NEW.extraction_snapshot_id IS NULL
  AND NEW.source_item_id<>NEW.target_item_id
  AND EXISTS (
    SELECT 1 FROM lineage_member target_member
      JOIN lineage_member source_member
        ON source_member.lineage_group_id=target_member.lineage_group_id
      JOIN knowledge_item si ON si.id=source_member.item_id
      JOIN knowledge_space ss ON ss.id=si.space_id
      JOIN knowledge_item ti ON ti.id=target_member.item_id
      JOIN knowledge_space ts ON ts.id=ti.space_id
    WHERE target_member.lineage_group_id=NEW.lineage_group_id
      AND target_member.item_id=NEW.target_item_id
      AND source_member.item_id=NEW.source_item_id
      AND target_member.derived_from_item_id=NEW.base_item_id
      AND target_member.derived_from_revision_id=NEW.base_revision_id
      AND ss.space_kind='personal' AND ts.space_kind='part'
  )
))""",
    "CHANGE_PROPOSAL_INVALID",
)

_guard(
    "trg_change_proposal_state_update",
    "BEFORE",
    "UPDATE",
    "change_proposal",
    """NEW.id IS NOT OLD.id OR NEW.finding_id IS NOT OLD.finding_id
OR NEW.finding_input_row_version IS NOT OLD.finding_input_row_version
OR NEW.proposal_kind IS NOT OLD.proposal_kind
OR NEW.proposal_series_id IS NOT OLD.proposal_series_id
OR NEW.proposal_version IS NOT OLD.proposal_version
OR NEW.supersedes_proposal_id IS NOT OLD.supersedes_proposal_id
OR NEW.lineage_group_id IS NOT OLD.lineage_group_id
OR NEW.base_item_id IS NOT OLD.base_item_id
OR NEW.base_revision_id IS NOT OLD.base_revision_id
OR NEW.source_item_id IS NOT OLD.source_item_id
OR NEW.source_revision_id IS NOT OLD.source_revision_id
OR NEW.target_item_id IS NOT OLD.target_item_id
OR NEW.target_revision_id IS NOT OLD.target_revision_id
OR NEW.source_artifact_id IS NOT OLD.source_artifact_id
OR NEW.extraction_snapshot_id IS NOT OLD.extraction_snapshot_id
OR NEW.proposed_content_md IS NOT OLD.proposed_content_md
OR NEW.proposed_content_blob_hash IS NOT OLD.proposed_content_blob_hash
OR NEW.proposed_content_size_bytes IS NOT OLD.proposed_content_size_bytes
OR NEW.proposed_content_sha256 IS NOT OLD.proposed_content_sha256
OR NEW.preview_sha256 IS NOT OLD.preview_sha256
OR NEW.created_by IS NOT OLD.created_by OR NEW.created_at IS NOT OLD.created_at
OR NOT (
  (OLD.state='pending_review' AND NEW.state IN ('approved','obsolete'))
  OR (OLD.state='approved' AND NEW.state IN ('applied','apply_failed','obsolete'))
  OR (OLD.state='apply_failed' AND NEW.state IN ('approved','obsolete'))
) OR NOT EXISTS (
  SELECT 1 FROM curation_finding f
  WHERE f.id=NEW.finding_id AND (
    f.status=NEW.state
    OR (NEW.state='pending_review' AND f.status='pending')
    OR (NEW.state='obsolete' AND f.status IN ('rejected','ignored','obsolete'))
  )
) OR (NEW.state='applied' AND (
  NEW.applied_operation_id IS NULL OR NEW.applied_dedup_id IS NULL
  OR NEW.last_application_error_json IS NOT NULL
)) OR (NEW.state='apply_failed' AND (
  NEW.applied_operation_id IS NULL OR NEW.applied_dedup_id IS NULL
  OR NEW.last_application_error_json IS NULL
)) OR (NEW.state NOT IN ('applied','apply_failed') AND (
  NEW.applied_operation_id IS NOT NULL OR NEW.applied_dedup_id IS NOT NULL
  OR NEW.last_application_error_json IS NOT NULL
)) OR (NEW.applied_dedup_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM operation_dedup d
  WHERE d.id=NEW.applied_dedup_id AND d.operation_id=NEW.applied_operation_id
))""",
    "CHANGE_PROPOSAL_INVALID",
)

_guard(
    "trg_change_proposal_history_delete",
    "BEFORE",
    "DELETE",
    "change_proposal",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_merge_event_validity_insert",
    "BEFORE",
    "INSERT",
    "merge_event",
    """NEW.source_item_id=NEW.target_item_id OR NOT EXISTS (
  SELECT 1 FROM knowledge_item s JOIN knowledge_item t
  WHERE s.id=NEW.source_item_id AND t.id=NEW.target_item_id
    AND s.space_id=NEW.space_id AND t.space_id=NEW.space_id
) OR NOT EXISTS (
  SELECT 1 FROM operation_dedup d
  WHERE d.id=NEW.dedup_id AND d.actor_user_id=NEW.actor_user_id
) OR NOT EXISTS (
  SELECT 1 FROM item_relation r
  WHERE r.id=NEW.relation_id AND r.source_item_id=NEW.source_item_id
    AND r.source_revision_id=NEW.source_revision_id
    AND r.target_item_id=NEW.target_item_id
    AND r.target_revision_id=NEW.result_revision_id
    AND r.relation_type='merged_into'
)""",
    "CURATION_POLICY_INVALID",
)

for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_merge_event_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "merge_event",
        "1",
        "HISTORY_IMMUTABLE",
    )


# Every item owns one never-reset fencing row.
_sql(
    "trg_knowledge_item_lease_seed_insert",
    """CREATE TRIGGER trg_knowledge_item_lease_seed_insert
AFTER INSERT ON knowledge_item
FOR EACH ROW
BEGIN
  SELECT CASE WHEN NEW.lifecycle_status<>'active'
    OR NEW.curation_status<>'provisional'
    OR NEW.current_revision_id IS NULL
    OR NEW.last_curation_transition_id IS NOT NULL
    OR NEW.latest_deactivation_episode_id IS NOT NULL
    OR NEW.redirect_item_id IS NOT NULL
    OR NEW.row_version<>1
    OR NEW.created_by<>NEW.updated_by
    OR NEW.created_at<>NEW.updated_at
    THEN RAISE(ABORT, 'ITEM_STATE_INVALID') END;
  INSERT INTO item_lease(item_id, fencing_generation)
  VALUES (NEW.id, 0);
END""",
)

_guard(
    "trg_item_lease_identity_update",
    "BEFORE",
    "UPDATE",
    "item_lease",
    "NEW.item_id IS NOT OLD.item_id OR NEW.fencing_generation<OLD.fencing_generation",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_item_lease_history_delete",
    "BEFORE",
    "DELETE",
    "item_lease",
    "1",
    "HISTORY_IMMUTABLE",
)


# Audit, outbox, durable jobs, backup, and blob-GC state machines.
for _operation in ("UPDATE", "DELETE"):
    _guard(
        f"trg_audit_event_history_{_operation.lower()}",
        "BEFORE",
        _operation,
        "audit_event",
        "1",
        "HISTORY_IMMUTABLE",
    )

_guard(
    "trg_outbox_event_state_insert",
    "BEFORE",
    "INSERT",
    "outbox_event",
    """NEW.state<>'pending' OR NEW.claim_owner IS NOT NULL
OR NEW.claim_expires_at IS NOT NULL OR NEW.attempt_count<>0
OR NEW.last_error_json IS NOT NULL OR NEW.completed_at IS NOT NULL""",
    "OUTBOX_STATE_INVALID",
)

_guard(
    "trg_outbox_event_state_update",
    "BEFORE",
    "UPDATE",
    "outbox_event",
    """NEW.id IS NOT OLD.id OR NEW.logical_key IS NOT OLD.logical_key
OR NEW.space_id IS NOT OLD.space_id OR NEW.aggregate_type IS NOT OLD.aggregate_type
OR NEW.aggregate_id IS NOT OLD.aggregate_id
OR NEW.aggregate_version IS NOT OLD.aggregate_version
OR NEW.event_type IS NOT OLD.event_type OR NEW.payload_json IS NOT OLD.payload_json
OR NEW.payload_schema_version IS NOT OLD.payload_schema_version
OR NEW.created_at IS NOT OLD.created_at OR NOT (
  (OLD.state='pending' AND NEW.state='processing')
  OR (OLD.state='processing' AND NEW.state='processing')
  OR (OLD.state='processing' AND NEW.state IN ('applied','retry'))
  OR (OLD.state='retry' AND NEW.state IN ('processing','dead'))
) OR (NEW.state='processing' AND OLD.state<>'processing'
  AND NEW.attempt_count<>OLD.attempt_count+1)
OR ((NEW.state<>'processing' OR OLD.state='processing')
  AND NEW.attempt_count<>OLD.attempt_count)
OR (OLD.state='processing' AND NEW.state='processing'
  AND NEW.claim_owner IS NOT OLD.claim_owner)""",
    "OUTBOX_STATE_INVALID",
)

_guard(
    "trg_outbox_event_history_delete",
    "BEFORE",
    "DELETE",
    "outbox_event",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_durable_job_state_insert",
    "BEFORE",
    "INSERT",
    "durable_job",
    """NEW.state<>'queued' OR NEW.claim_owner IS NOT NULL
OR NEW.claim_expires_at IS NOT NULL OR NEW.attempt_count<>0
OR NEW.cancel_requested<>0 OR NEW.progress_permille<>0
OR NEW.result_json IS NOT NULL OR NEW.error_json IS NOT NULL
OR NEW.completed_at IS NOT NULL OR NEW.updated_at<>NEW.created_at
OR EXISTS (
  SELECT 1 FROM json_each(NEW.authorization_capabilities_json)
  WHERE type<>'text'
)
OR EXISTS (
  SELECT 1
  FROM json_each(NEW.authorization_capabilities_json) a
  JOIN json_each(NEW.authorization_capabilities_json) b
    ON CAST(a.key AS INTEGER)<CAST(b.key AS INTEGER)
  WHERE a.value>=b.value
)
OR (NEW.actor_kind='user' AND NOT EXISTS (
  SELECT 1 FROM app_user u
  WHERE u.id=NEW.actor_user_id
    AND u.account_status='active'
    AND u.scope_epoch=NEW.authorization_scope_epoch
    AND u.current_part_id IS NEW.authorization_part_id
    AND (SELECT count(*) FROM json_each(NEW.authorization_snapshot_json))=9
    AND NOT EXISTS (
      SELECT 1 FROM json_each(NEW.authorization_snapshot_json)
      WHERE key NOT IN ('schema_version','actor_kind','actor_user_id',
        'account_status','system_role','current_part_id','scope_epoch','space_id',
        'capabilities')
    )
    AND json_extract(NEW.authorization_snapshot_json,'$.schema_version')=1
    AND json_extract(NEW.authorization_snapshot_json,'$.actor_kind')='user'
    AND json_extract(NEW.authorization_snapshot_json,'$.actor_user_id')=u.id
    AND json_extract(NEW.authorization_snapshot_json,'$.account_status')=u.account_status
    AND json_extract(NEW.authorization_snapshot_json,'$.system_role')=u.system_role
    AND json_extract(NEW.authorization_snapshot_json,'$.current_part_id')
      IS NEW.authorization_part_id
    AND json_extract(NEW.authorization_snapshot_json,'$.scope_epoch')
      =NEW.authorization_scope_epoch
    AND json_extract(NEW.authorization_snapshot_json,'$.space_id') IS NEW.space_id
    AND json(json_extract(NEW.authorization_snapshot_json,'$.capabilities'))
      =json(NEW.authorization_capabilities_json)
)) OR (NEW.actor_kind='system' AND (
  NEW.actor_user_id IS NOT NULL OR NEW.authorization_scope_epoch IS NOT NULL
  OR NEW.authorization_part_id IS NOT NULL
  OR json_array_length(NEW.authorization_capabilities_json)<>0
  OR (SELECT count(*) FROM json_each(NEW.authorization_snapshot_json))<>2
  OR EXISTS (
    SELECT 1 FROM json_each(NEW.authorization_snapshot_json)
    WHERE key NOT IN ('schema_version','actor_kind')
  )
  OR json_type(NEW.authorization_snapshot_json,'$.schema_version') IS NOT 'integer'
  OR json_extract(NEW.authorization_snapshot_json,'$.schema_version') IS NOT 1
  OR json_type(NEW.authorization_snapshot_json,'$.actor_kind') IS NOT 'text'
  OR json_extract(NEW.authorization_snapshot_json,'$.actor_kind') IS NOT 'system'
))
OR (NEW.job_type='publication_preview.extract' AND (
  NEW.handler_version<>'v1'
  OR (SELECT count(*) FROM json_each(NEW.input_ref_json))<>3
  OR EXISTS (
    SELECT 1 FROM json_each(NEW.input_ref_json)
    WHERE key NOT IN ('preview_id','artifact_id','rule_version')
  )
  OR json_type(NEW.input_ref_json,'$.preview_id') IS NOT 'text'
  OR length(json_extract(NEW.input_ref_json,'$.preview_id'))<>36
  OR substr(json_extract(NEW.input_ref_json,'$.preview_id'),9,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.preview_id'),14,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.preview_id'),19,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.preview_id'),24,1)<>'-'
  OR length(replace(json_extract(NEW.input_ref_json,'$.preview_id'),'-',''))<>32
  OR replace(json_extract(NEW.input_ref_json,'$.preview_id'),'-','') GLOB '*[^0-9a-f]*'
  OR json_extract(NEW.input_ref_json,'$.preview_id')
    <>lower(json_extract(NEW.input_ref_json,'$.preview_id'))
  OR json_type(NEW.input_ref_json,'$.artifact_id') IS NOT 'text'
  OR length(json_extract(NEW.input_ref_json,'$.artifact_id'))<>36
  OR substr(json_extract(NEW.input_ref_json,'$.artifact_id'),9,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.artifact_id'),14,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.artifact_id'),19,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.artifact_id'),24,1)<>'-'
  OR length(replace(json_extract(NEW.input_ref_json,'$.artifact_id'),'-',''))<>32
  OR replace(json_extract(NEW.input_ref_json,'$.artifact_id'),'-','') GLOB '*[^0-9a-f]*'
  OR json_extract(NEW.input_ref_json,'$.artifact_id')
    <>lower(json_extract(NEW.input_ref_json,'$.artifact_id'))
  OR json_type(NEW.input_ref_json,'$.rule_version') IS NOT 'text'
  OR length(trim(json_extract(NEW.input_ref_json,'$.rule_version'))) NOT BETWEEN 1 AND 120
))
OR (NEW.job_type='curation.rescan' AND (
  NEW.handler_version<>'v1'
  OR (SELECT count(*) FROM json_each(NEW.input_ref_json))<>3
  OR EXISTS (
    SELECT 1 FROM json_each(NEW.input_ref_json)
    WHERE key NOT IN ('item_id','revision_id','rule_version')
  )
  OR json_type(NEW.input_ref_json,'$.item_id') IS NOT 'text'
  OR length(json_extract(NEW.input_ref_json,'$.item_id'))<>36
  OR substr(json_extract(NEW.input_ref_json,'$.item_id'),9,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.item_id'),14,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.item_id'),19,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.item_id'),24,1)<>'-'
  OR length(replace(json_extract(NEW.input_ref_json,'$.item_id'),'-',''))<>32
  OR replace(json_extract(NEW.input_ref_json,'$.item_id'),'-','') GLOB '*[^0-9a-f]*'
  OR json_extract(NEW.input_ref_json,'$.item_id')
    <>lower(json_extract(NEW.input_ref_json,'$.item_id'))
  OR json_type(NEW.input_ref_json,'$.revision_id') IS NOT 'text'
  OR length(json_extract(NEW.input_ref_json,'$.revision_id'))<>36
  OR substr(json_extract(NEW.input_ref_json,'$.revision_id'),9,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.revision_id'),14,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.revision_id'),19,1)<>'-'
  OR substr(json_extract(NEW.input_ref_json,'$.revision_id'),24,1)<>'-'
  OR length(replace(json_extract(NEW.input_ref_json,'$.revision_id'),'-',''))<>32
  OR replace(json_extract(NEW.input_ref_json,'$.revision_id'),'-','') GLOB '*[^0-9a-f]*'
  OR json_extract(NEW.input_ref_json,'$.revision_id')
    <>lower(json_extract(NEW.input_ref_json,'$.revision_id'))
  OR json_type(NEW.input_ref_json,'$.rule_version') IS NOT 'text'
  OR length(trim(json_extract(NEW.input_ref_json,'$.rule_version'))) NOT BETWEEN 1 AND 120
))
OR (NEW.dedup_id IS NOT NULL AND NOT EXISTS (
  SELECT 1 FROM operation_dedup d
  WHERE d.id=NEW.dedup_id AND d.actor_user_id=NEW.actor_user_id
    AND d.operation_id=NEW.operation_id
    AND d.request_sha256=NEW.input_sha256
    AND d.operation_type='job:'||NEW.job_type
))""",
    "JOB_STATE_INVALID",
)

_guard(
    "trg_durable_job_state_update",
    "BEFORE",
    "UPDATE",
    "durable_job",
    """NEW.id IS NOT OLD.id OR NEW.dedup_id IS NOT OLD.dedup_id
OR NEW.operation_id IS NOT OLD.operation_id OR NEW.actor_kind IS NOT OLD.actor_kind
OR NEW.actor_user_id IS NOT OLD.actor_user_id OR NEW.space_id IS NOT OLD.space_id
OR NEW.job_type IS NOT OLD.job_type OR NEW.handler_version IS NOT OLD.handler_version
OR NEW.effect_key IS NOT OLD.effect_key OR NEW.input_ref_json IS NOT OLD.input_ref_json
OR NEW.input_sha256 IS NOT OLD.input_sha256
OR NEW.expected_state_json IS NOT OLD.expected_state_json
OR NEW.authorization_snapshot_json IS NOT OLD.authorization_snapshot_json
OR NEW.authorization_snapshot_sha256 IS NOT OLD.authorization_snapshot_sha256
OR NEW.authorization_scope_epoch IS NOT OLD.authorization_scope_epoch
OR NEW.authorization_part_id IS NOT OLD.authorization_part_id
OR NEW.authorization_capabilities_json IS NOT OLD.authorization_capabilities_json
OR NEW.max_attempts IS NOT OLD.max_attempts OR NEW.cancellable IS NOT OLD.cancellable
OR NEW.created_at IS NOT OLD.created_at OR NOT (
  (OLD.state=NEW.state AND NEW.state IN ('queued','claimed','running','retry'))
  OR (OLD.state='queued' AND NEW.state IN ('claimed','cancelled'))
  OR (OLD.state='claimed' AND NEW.state IN ('running','retry','cancelled'))
  OR (OLD.state='running' AND NEW.state IN ('succeeded','failed','retry','cancelled'))
  OR (OLD.state='retry' AND NEW.state IN ('claimed','cancelled','dead'))
) OR (NEW.state='claimed' AND OLD.state<>'claimed'
  AND NEW.attempt_count<>OLD.attempt_count+1)
OR ((NEW.state<>'claimed' OR OLD.state='claimed')
  AND NEW.attempt_count<>OLD.attempt_count)
OR (OLD.state IN ('claimed','running') AND NEW.state IN ('claimed','running')
  AND NEW.claim_owner IS NOT OLD.claim_owner)
OR (OLD.state='retry' AND NEW.state='dead'
  AND OLD.attempt_count<>OLD.max_attempts)
OR (NEW.state='cancelled' AND NEW.cancellable<>1)""",
    "JOB_STATE_INVALID",
)

_guard(
    "trg_durable_job_history_delete",
    "BEFORE",
    "DELETE",
    "durable_job",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_backup_run_state_insert",
    "BEFORE",
    "INSERT",
    "backup_run",
    """NEW.state<>'preparing' OR NEW.snapshot_db_relative_path IS NOT NULL
OR NEW.snapshot_db_sha256 IS NOT NULL OR NEW.manifest_sha256 IS NOT NULL
OR NEW.error_json IS NOT NULL OR NEW.completed_at IS NOT NULL""",
    "MAINTENANCE_STATE_INVALID",
)

_guard(
    "trg_backup_run_state_update",
    "BEFORE",
    "UPDATE",
    "backup_run",
    """NEW.id IS NOT OLD.id OR NEW.schema_version IS NOT OLD.schema_version
OR NEW.sqlite_source_id IS NOT OLD.sqlite_source_id
OR NEW.created_at IS NOT OLD.created_at OR NOT (
  (OLD.state='preparing' AND NEW.state IN ('copying','failed'))
  OR (OLD.state='copying' AND NEW.state IN ('verified','failed'))
)""",
    "MAINTENANCE_STATE_INVALID",
)

_guard(
    "trg_backup_run_history_delete",
    "BEFORE",
    "DELETE",
    "backup_run",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_backup_blob_state_insert",
    "BEFORE",
    "INSERT",
    "backup_blob",
    """NEW.status<>'pending' OR NEW.copied_at IS NOT NULL
OR NEW.verified_at IS NOT NULL OR NEW.error_json IS NOT NULL""",
    "MAINTENANCE_STATE_INVALID",
)

_guard(
    "trg_backup_blob_state_update",
    "BEFORE",
    "UPDATE",
    "backup_blob",
    """NEW.backup_run_id IS NOT OLD.backup_run_id
OR NEW.blob_sha256 IS NOT OLD.blob_sha256 OR NEW.size_bytes IS NOT OLD.size_bytes
OR OLD.status<>'pending' OR NEW.status NOT IN ('verified','failed')""",
    "MAINTENANCE_STATE_INVALID",
)

_guard(
    "trg_backup_blob_history_delete",
    "BEFORE",
    "DELETE",
    "backup_blob",
    "1",
    "HISTORY_IMMUTABLE",
)

_guard(
    "trg_blob_gc_candidate_state_insert",
    "BEFORE",
    "INSERT",
    "blob_gc_candidate",
    """NEW.state<>'quarantined' OR NEW.claim_owner IS NOT NULL
OR NEW.claim_expires_at IS NOT NULL OR NEW.attempt_count<>0
OR NEW.last_error_json IS NOT NULL""",
    "MAINTENANCE_STATE_INVALID",
)

_guard(
    "trg_blob_gc_candidate_state_update",
    "BEFORE",
    "UPDATE",
    "blob_gc_candidate",
    """NEW.blob_sha256 IS NOT OLD.blob_sha256
OR NEW.unreferenced_since IS NOT OLD.unreferenced_since
OR NEW.delete_after IS NOT OLD.delete_after OR NOT (
  (OLD.state='quarantined' AND NEW.state='claimed')
  OR (OLD.state='claimed' AND NEW.state='claimed')
  OR (OLD.state='claimed' AND NEW.state IN ('retry','dead'))
  OR (OLD.state='retry' AND NEW.state IN ('claimed','dead'))
) OR (NEW.state='claimed' AND OLD.state<>'claimed'
  AND NEW.attempt_count<>OLD.attempt_count+1)
OR ((NEW.state<>'claimed' OR OLD.state='claimed')
  AND NEW.attempt_count<>OLD.attempt_count)
OR (OLD.state='claimed' AND NEW.state='claimed'
  AND NEW.claim_owner IS NOT OLD.claim_owner)""",
    "MAINTENANCE_STATE_INVALID",
)


TRIGGER_DEFINITIONS: tuple[tuple[str, str], ...] = tuple(_DEFINITIONS)
TRIGGER_NAMES = tuple(name for name, _statement in TRIGGER_DEFINITIONS)
TRIGGER_STATEMENTS = tuple(statement for _name, statement in TRIGGER_DEFINITIONS)

if len(TRIGGER_NAMES) != len(set(TRIGGER_NAMES)):  # pragma: no cover - import guard
    raise RuntimeError("The physical v1 trigger manifest contains a duplicate name.")


__all__ = [
    "TRIGGER_DEFINITIONS",
    "TRIGGER_NAMES",
    "TRIGGER_STATEMENTS",
]
