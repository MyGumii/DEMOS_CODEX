"""Forward-only immutable checkpoints for reviewed legacy asset relationships.

Schema versions 1 through 3 remain frozen.  Version 4 adds a separate,
append-only relationship ledger for P2.3 ``asset_reference`` effects.  The
existing record mapping ledger may already contain committed rows when this
migration is applied.
"""

from __future__ import annotations


LEGACY_MIGRATION_RELATIONSHIP_MAP_TABLE = (
    "legacy_migration_relationship_map"
)
LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_CODE = (
    "LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE"
)
LEGACY_MIGRATION_RELATIONSHIP_MAP_INDEXES: tuple[str, ...] = (
    "ux_migration_relationship_identity",
    "ux_migration_relationship_embedded_blob",
    "ix_migration_relationship_run",
    "ix_migration_relationship_endpoints",
)
LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_TRIGGERS: tuple[str, str] = (
    "trg_legacy_migration_relationship_map_history_update",
    "trg_legacy_migration_relationship_map_history_delete",
)


V004_STATEMENTS: tuple[str, ...] = (
    """
CREATE TABLE legacy_migration_relationship_map_v4_preflight(
  violation INTEGER NOT NULL,
  CONSTRAINT LEGACY_MIGRATION_RELATIONSHIP_MAP_ABSENT_BEFORE_V4
    CHECK(violation=0)
) STRICT
""".strip(),
    """
INSERT INTO legacy_migration_relationship_map_v4_preflight(violation)
SELECT 1 FROM sqlite_schema
WHERE name IN (
  'legacy_migration_relationship_map',
  'ux_migration_relationship_identity',
  'ux_migration_relationship_embedded_blob',
  'ix_migration_relationship_run',
  'ix_migration_relationship_endpoints',
  'trg_legacy_migration_relationship_map_history_update',
  'trg_legacy_migration_relationship_map_history_delete'
)
LIMIT 1
""".strip(),
    """
DROP TABLE legacy_migration_relationship_map_v4_preflight
""".strip(),
    """
CREATE TABLE legacy_migration_relationship_map(
    id TEXT PRIMARY KEY NOT NULL CHECK(
        length(id)=36 AND substr(id,9,1)='-' AND substr(id,14,1)='-'
        AND substr(id,19,1)='-' AND substr(id,24,1)='-'
        AND length(replace(id,'-',''))=32
        AND replace(id,'-','') NOT GLOB '*[^0-9a-f]*' AND id=lower(id)
    ),
    migration_run_id TEXT NOT NULL CHECK(
        length(migration_run_id)=36 AND substr(migration_run_id,9,1)='-'
        AND substr(migration_run_id,14,1)='-'
        AND substr(migration_run_id,19,1)='-'
        AND substr(migration_run_id,24,1)='-'
        AND length(replace(migration_run_id,'-',''))=32
        AND replace(migration_run_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND migration_run_id=lower(migration_run_id)
    ),
    relationship_id TEXT NOT NULL CHECK(
        length(relationship_id)=28
        AND substr(relationship_id,1,4)='rel_'
        AND substr(relationship_id,5) NOT GLOB '*[^0-9a-f]*'
        AND relationship_id=lower(relationship_id)
    ),
    relationship_sha256 TEXT NOT NULL CHECK(
        length(relationship_sha256)=64
        AND relationship_sha256=lower(relationship_sha256)
        AND relationship_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    relationship_type TEXT NOT NULL CHECK(relationship_type='asset_reference'),
    from_inventory_record_id TEXT NOT NULL CHECK(
        length(from_inventory_record_id)=28
        AND substr(from_inventory_record_id,1,4)='rec_'
        AND substr(from_inventory_record_id,5) NOT GLOB '*[^0-9a-f]*'
        AND from_inventory_record_id=lower(from_inventory_record_id)
    ),
    to_inventory_record_id TEXT NOT NULL CHECK(
        length(to_inventory_record_id)=28
        AND substr(to_inventory_record_id,1,4)='rec_'
        AND substr(to_inventory_record_id,5) NOT GLOB '*[^0-9a-f]*'
        AND to_inventory_record_id=lower(to_inventory_record_id)
    ),
    from_mapping_id TEXT NOT NULL CHECK(
        length(from_mapping_id)=36 AND substr(from_mapping_id,9,1)='-'
        AND substr(from_mapping_id,14,1)='-'
        AND substr(from_mapping_id,19,1)='-'
        AND substr(from_mapping_id,24,1)='-'
        AND length(replace(from_mapping_id,'-',''))=32
        AND replace(from_mapping_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND from_mapping_id=lower(from_mapping_id)
    ) REFERENCES legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    to_mapping_id TEXT NOT NULL CHECK(
        length(to_mapping_id)=36 AND substr(to_mapping_id,9,1)='-'
        AND substr(to_mapping_id,14,1)='-'
        AND substr(to_mapping_id,19,1)='-'
        AND substr(to_mapping_id,24,1)='-'
        AND length(replace(to_mapping_id,'-',''))=32
        AND replace(to_mapping_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND to_mapping_id=lower(to_mapping_id)
    ) REFERENCES legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    resolved_owner_user_id TEXT NOT NULL CHECK(
        length(resolved_owner_user_id) BETWEEN 1 AND 64
        AND resolved_owner_user_id=lower(trim(resolved_owner_user_id))
        AND resolved_owner_user_id NOT GLOB '*[^a-z0-9._-]*'
    ) REFERENCES app_user(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_space_id TEXT NOT NULL CHECK(
        length(target_space_id)=36 AND substr(target_space_id,9,1)='-'
        AND substr(target_space_id,14,1)='-'
        AND substr(target_space_id,19,1)='-'
        AND substr(target_space_id,24,1)='-'
        AND length(replace(target_space_id,'-',''))=32
        AND replace(target_space_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_space_id=lower(target_space_id)
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_artifact_id TEXT NOT NULL CHECK(
        length(source_artifact_id)=36 AND substr(source_artifact_id,9,1)='-'
        AND substr(source_artifact_id,14,1)='-'
        AND substr(source_artifact_id,19,1)='-'
        AND substr(source_artifact_id,24,1)='-'
        AND length(replace(source_artifact_id,'-',''))=32
        AND replace(source_artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_artifact_id=lower(source_artifact_id)
    ) REFERENCES source_artifact(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_item_id TEXT NOT NULL CHECK(
        length(source_item_id)=36 AND substr(source_item_id,9,1)='-'
        AND substr(source_item_id,14,1)='-'
        AND substr(source_item_id,19,1)='-'
        AND substr(source_item_id,24,1)='-'
        AND length(replace(source_item_id,'-',''))=32
        AND replace(source_item_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_item_id=lower(source_item_id)
    ) REFERENCES knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    source_revision_id TEXT NOT NULL CHECK(
        length(source_revision_id)=36 AND substr(source_revision_id,9,1)='-'
        AND substr(source_revision_id,14,1)='-'
        AND substr(source_revision_id,19,1)='-'
        AND substr(source_revision_id,24,1)='-'
        AND length(replace(source_revision_id,'-',''))=32
        AND replace(source_revision_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND source_revision_id=lower(source_revision_id)
    ) REFERENCES knowledge_revision(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_asset_artifact_id TEXT NOT NULL CHECK(
        length(target_asset_artifact_id)=36
        AND substr(target_asset_artifact_id,9,1)='-'
        AND substr(target_asset_artifact_id,14,1)='-'
        AND substr(target_asset_artifact_id,19,1)='-'
        AND substr(target_asset_artifact_id,24,1)='-'
        AND length(replace(target_asset_artifact_id,'-',''))=32
        AND replace(target_asset_artifact_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_asset_artifact_id=lower(target_asset_artifact_id)
    ) REFERENCES source_artifact(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    target_asset_blob_id TEXT NOT NULL CHECK(
        length(target_asset_blob_id)=36
        AND substr(target_asset_blob_id,9,1)='-'
        AND substr(target_asset_blob_id,14,1)='-'
        AND substr(target_asset_blob_id,19,1)='-'
        AND substr(target_asset_blob_id,24,1)='-'
        AND length(replace(target_asset_blob_id,'-',''))=32
        AND replace(target_asset_blob_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_asset_blob_id=lower(target_asset_blob_id)
    ) REFERENCES artifact_blob(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    embedded_asset_blob_id TEXT NOT NULL CHECK(
        length(embedded_asset_blob_id)=36
        AND substr(embedded_asset_blob_id,9,1)='-'
        AND substr(embedded_asset_blob_id,14,1)='-'
        AND substr(embedded_asset_blob_id,19,1)='-'
        AND substr(embedded_asset_blob_id,24,1)='-'
        AND length(replace(embedded_asset_blob_id,'-',''))=32
        AND replace(embedded_asset_blob_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND embedded_asset_blob_id=lower(embedded_asset_blob_id)
    ) REFERENCES artifact_blob(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    embedded_ordinal INTEGER NOT NULL CHECK(embedded_ordinal>=0),
    embedded_logical_name_sha256 TEXT NOT NULL CHECK(
        length(embedded_logical_name_sha256)=64
        AND embedded_logical_name_sha256=lower(embedded_logical_name_sha256)
        AND embedded_logical_name_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    status TEXT NOT NULL CHECK(status='imported'),
    details_json TEXT NOT NULL DEFAULT '{}' CHECK(
        json_valid(details_json) AND json_type(details_json)='object'
    ),
    created_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(created_at>=0),
    updated_at INTEGER NOT NULL DEFAULT (
        CAST(unixepoch('subsec') * 1000 AS INTEGER)
    ) CHECK(updated_at>=0),
    CHECK(from_inventory_record_id<>to_inventory_record_id),
    CHECK(from_mapping_id<>to_mapping_id),
    CHECK(target_asset_blob_id<>embedded_asset_blob_id),
    CHECK(created_at=updated_at)
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_relationship_identity
ON legacy_migration_relationship_map(relationship_id)
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_relationship_embedded_blob
ON legacy_migration_relationship_map(embedded_asset_blob_id)
""".strip(),
    """
CREATE INDEX ix_migration_relationship_run
ON legacy_migration_relationship_map(migration_run_id,status,id)
""".strip(),
    """
CREATE INDEX ix_migration_relationship_endpoints
ON legacy_migration_relationship_map(from_mapping_id,to_mapping_id,relationship_id)
""".strip(),
    """
CREATE TRIGGER trg_legacy_migration_relationship_map_history_update
BEFORE UPDATE ON legacy_migration_relationship_map
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_legacy_migration_relationship_map_history_delete
BEFORE DELETE ON legacy_migration_relationship_map
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE');
END
""".strip(),
)


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


_RELATIONSHIP_TABLE_SQL = _sql_literal(V004_STATEMENTS[3])
_RELATIONSHIP_INDEX_SQL = tuple(
    _sql_literal(statement) for statement in V004_STATEMENTS[4:8]
)


V004_POSTCONDITION_QUERIES: tuple[str, ...] = (
    f"""
SELECT 'legacy_migration_relationship_table_missing_or_changed'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table'
    AND name='legacy_migration_relationship_map'
    AND tbl_name='legacy_migration_relationship_map'
    AND sql={_RELATIONSHIP_TABLE_SQL}
)
""".strip(),
    f"""
SELECT 'legacy_migration_relationship_indexes_missing' WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND tbl_name='legacy_migration_relationship_map' AND (
    (name='ux_migration_relationship_identity'
      AND sql={_RELATIONSHIP_INDEX_SQL[0]})
    OR (name='ux_migration_relationship_embedded_blob'
      AND sql={_RELATIONSHIP_INDEX_SQL[1]})
    OR (name='ix_migration_relationship_run'
      AND sql={_RELATIONSHIP_INDEX_SQL[2]})
    OR (name='ix_migration_relationship_endpoints'
      AND sql={_RELATIONSHIP_INDEX_SQL[3]})
  )
) <> 4
""".strip(),
    """
SELECT 'legacy_migration_relationship_immutable_triggers_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_relationship_map_history_update'
    AND tbl_name='legacy_migration_relationship_map'
    AND upper(sql) LIKE '%BEFORE UPDATE ON LEGACY_MIGRATION_RELATIONSHIP_MAP%'
    AND instr(
      sql,
      'RAISE(ABORT, ''LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE'')'
    ) > 0
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_relationship_map_history_delete'
    AND tbl_name='legacy_migration_relationship_map'
    AND upper(sql) LIKE '%BEFORE DELETE ON LEGACY_MIGRATION_RELATIONSHIP_MAP%'
    AND instr(
      sql,
      'RAISE(ABORT, ''LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE'')'
    ) > 0
)
""".strip(),
    """
SELECT relationship_id FROM legacy_migration_relationship_map
GROUP BY relationship_id
HAVING count(*) > 1
LIMIT 1
""".strip(),
    """
SELECT embedded_asset_blob_id FROM legacy_migration_relationship_map
GROUP BY embedded_asset_blob_id
HAVING count(*) > 1
LIMIT 1
""".strip(),
)


__all__ = [
    "LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_CODE",
    "LEGACY_MIGRATION_RELATIONSHIP_MAP_IMMUTABLE_TRIGGERS",
    "LEGACY_MIGRATION_RELATIONSHIP_MAP_INDEXES",
    "LEGACY_MIGRATION_RELATIONSHIP_MAP_TABLE",
    "V004_POSTCONDITION_QUERIES",
    "V004_STATEMENTS",
]
