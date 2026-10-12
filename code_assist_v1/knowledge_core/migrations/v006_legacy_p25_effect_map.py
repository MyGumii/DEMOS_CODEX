"""Forward-only durable targets for reviewed P2.5 conversation/state effects.

Versions 1 through 5 remain frozen. Version 6 adds nullable conversation-child
targets to the existing insert-only record ledger and a separate immutable
checkpoint for a valid reviewed Notion session-page relationship. Existing
P2.3/P2.4 rows remain byte-for-byte unchanged.
"""

from __future__ import annotations


LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE = "legacy_migration_p25_relationship_map"
LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE = (
    "LEGACY_MIGRATION_P25_RELATIONSHIP_MAP_IMMUTABLE"
)


_UUID_CHECK = """
length({name})=36 AND substr({name},9,1)='-'
AND substr({name},14,1)='-' AND substr({name},19,1)='-'
AND substr({name},24,1)='-' AND length(replace({name},'-',''))=32
AND replace({name},'-','') NOT GLOB '*[^0-9a-f]*' AND {name}=lower({name})
""".strip()


def _nullable_uuid_column(name: str, target: str) -> str:
    return (
        f"ALTER TABLE legacy_migration_map ADD COLUMN {name} TEXT CHECK(\n"
        f"    {name} IS NULL OR ({_UUID_CHECK.format(name=name)})\n"
        f") REFERENCES {target}(id) ON UPDATE RESTRICT ON DELETE RESTRICT"
    )


V006_STATEMENTS: tuple[str, ...] = (
    _nullable_uuid_column("conversation_id", "conversation"),
    _nullable_uuid_column("message_id", "conversation_message"),
    _nullable_uuid_column("summary_id", "conversation_summary"),
    _nullable_uuid_column("context_binding_id", "conversation_context_binding"),
    """
CREATE INDEX ix_migration_conversation_target
ON legacy_migration_map(conversation_id,id) WHERE conversation_id IS NOT NULL
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_message_target
ON legacy_migration_map(message_id) WHERE message_id IS NOT NULL
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_summary_target
ON legacy_migration_map(summary_id) WHERE summary_id IS NOT NULL
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_context_binding_target
ON legacy_migration_map(context_binding_id) WHERE context_binding_id IS NOT NULL
""".strip(),
    f"""
CREATE TABLE {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}(
    id TEXT PRIMARY KEY NOT NULL CHECK({_UUID_CHECK.format(name='id')}),
    migration_run_id TEXT NOT NULL CHECK(
        {_UUID_CHECK.format(name='migration_run_id')}
    ),
    relationship_id TEXT NOT NULL CHECK(
        length(relationship_id)=28 AND substr(relationship_id,1,4)='rel_'
        AND substr(relationship_id,5) NOT GLOB '*[^0-9a-f]*'
        AND relationship_id=lower(relationship_id)
    ),
    relationship_sha256 TEXT NOT NULL CHECK(
        length(relationship_sha256)=64
        AND relationship_sha256=lower(relationship_sha256)
        AND relationship_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    relationship_type TEXT NOT NULL CHECK(
        relationship_type='notion_session_page'
    ),
    source_mapping_id TEXT NOT NULL CHECK(
        {_UUID_CHECK.format(name='source_mapping_id')}
    ) REFERENCES legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    from_mapping_id TEXT NOT NULL CHECK(
        {_UUID_CHECK.format(name='from_mapping_id')}
    ) REFERENCES legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    conversation_id TEXT NOT NULL CHECK(
        {_UUID_CHECK.format(name='conversation_id')}
    ) REFERENCES conversation(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    context_binding_id TEXT NOT NULL CHECK(
        {_UUID_CHECK.format(name='context_binding_id')}
    ) REFERENCES conversation_context_binding(id)
      ON UPDATE RESTRICT ON DELETE RESTRICT,
    resolved_owner_user_id TEXT NOT NULL CHECK(
        length(resolved_owner_user_id) BETWEEN 1 AND 64
        AND resolved_owner_user_id=lower(trim(resolved_owner_user_id))
        AND resolved_owner_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_space_id TEXT NOT NULL CHECK(
        {_UUID_CHECK.format(name='target_space_id')}
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    page_identity_sha256 TEXT NOT NULL CHECK(
        length(page_identity_sha256)=64
        AND page_identity_sha256=lower(page_identity_sha256)
        AND page_identity_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    notion_row_sha256 TEXT NOT NULL CHECK(
        length(notion_row_sha256)=64
        AND notion_row_sha256=lower(notion_row_sha256)
        AND notion_row_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK(status='imported'),
    details_json TEXT NOT NULL CHECK(
        json_valid(details_json) AND json_type(details_json)='object'
    ),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=0),
    CHECK(source_mapping_id<>from_mapping_id),
    CHECK(created_at=updated_at)
) STRICT
""".strip(),
    f"""
CREATE UNIQUE INDEX ux_migration_p25_relationship_identity
ON {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}(relationship_id)
""".strip(),
    f"""
CREATE UNIQUE INDEX ux_migration_p25_context_binding
ON {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}(context_binding_id)
""".strip(),
    f"""
CREATE INDEX ix_migration_p25_relationship_run
ON {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}(migration_run_id,status,id)
""".strip(),
    f"""
CREATE INDEX ix_migration_p25_relationship_endpoints
ON {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}(
    source_mapping_id,from_mapping_id,relationship_id
)
""".strip(),
    f"""
CREATE TRIGGER trg_legacy_migration_p25_relationship_history_update
BEFORE UPDATE ON {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, '{LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE}');
END
""".strip(),
    f"""
CREATE TRIGGER trg_legacy_migration_p25_relationship_history_delete
BEFORE DELETE ON {LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, '{LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE}');
END
""".strip(),
)


V006_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'legacy_migration_p25_record_target_indexes_missing'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND tbl_name='legacy_migration_map'
    AND name IN (
      'ix_migration_conversation_target','ux_migration_message_target',
      'ux_migration_summary_target','ux_migration_context_binding_target'
    )
)<>4
""".strip(),
    f"""
SELECT 'legacy_migration_p25_relationship_table_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table' AND name='{LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}'
    AND tbl_name='{LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}'
)
""".strip(),
    f"""
SELECT 'legacy_migration_p25_relationship_indexes_missing'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND tbl_name='{LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}'
    AND name IN (
      'ux_migration_p25_relationship_identity',
      'ux_migration_p25_context_binding',
      'ix_migration_p25_relationship_run',
      'ix_migration_p25_relationship_endpoints'
    )
)<>4
""".strip(),
    f"""
SELECT 'legacy_migration_p25_relationship_triggers_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_p25_relationship_history_update'
    AND tbl_name='{LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}'
    AND instr(sql,'{LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE}')>0
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_p25_relationship_history_delete'
    AND tbl_name='{LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE}'
    AND instr(sql,'{LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE}')>0
)
""".strip(),
)


__all__ = [
    "LEGACY_MIGRATION_P25_RELATIONSHIP_IMMUTABLE_CODE",
    "LEGACY_MIGRATION_P25_RELATIONSHIP_TABLE",
    "V006_POSTCONDITION_QUERIES",
    "V006_STATEMENTS",
]
