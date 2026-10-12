"""Forward-only durability constraints for legacy migration mappings.

Schema versions 1 and 2 are frozen.  Version 3 strengthens the P2.3 mapping
ledger so one stable normalized source locator can produce at most one
canonical effect, regardless of later category or content-hash drift.  Mapping
rows are append-only once inserted.
"""

from __future__ import annotations


LEGACY_MIGRATION_MAP_IMMUTABLE_CODE = "LEGACY_MIGRATION_MAP_IMMUTABLE"
LEGACY_MIGRATION_MAP_LOCATOR_INDEX = "ux_migration_normalized_path"
LEGACY_MIGRATION_MAP_IMMUTABLE_TRIGGERS: tuple[str, str] = (
    "trg_legacy_migration_map_history_update",
    "trg_legacy_migration_map_history_delete",
)


V003_STATEMENTS: tuple[str, ...] = (
    """
CREATE UNIQUE INDEX ux_migration_normalized_path
ON legacy_migration_map(normalized_path)
""".strip(),
    """
CREATE TABLE legacy_migration_map_v3_preflight(
  violation INTEGER NOT NULL,
  CONSTRAINT LEGACY_MIGRATION_MAP_EMPTY_BEFORE_V3 CHECK(violation=0)
) STRICT
""".strip(),
    """
INSERT INTO legacy_migration_map_v3_preflight(violation)
SELECT 1 FROM legacy_migration_map LIMIT 1
""".strip(),
    """
DROP TABLE legacy_migration_map_v3_preflight
""".strip(),
    """
CREATE TRIGGER trg_legacy_migration_map_history_update
BEFORE UPDATE ON legacy_migration_map
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'LEGACY_MIGRATION_MAP_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_legacy_migration_map_history_delete
BEFORE DELETE ON legacy_migration_map
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'LEGACY_MIGRATION_MAP_IMMUTABLE');
END
""".strip(),
)


V003_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'legacy_migration_locator_index_missing' WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='index'
    AND name='ux_migration_normalized_path'
    AND tbl_name='legacy_migration_map'
    AND sql='CREATE UNIQUE INDEX ux_migration_normalized_path\nON legacy_migration_map(normalized_path)'
)
""".strip(),
    """
SELECT 'legacy_migration_immutable_triggers_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_map_history_update'
    AND tbl_name='legacy_migration_map'
    AND upper(sql) LIKE '%BEFORE UPDATE ON LEGACY_MIGRATION_MAP%'
    AND instr(sql, 'RAISE(ABORT, ''LEGACY_MIGRATION_MAP_IMMUTABLE'')') > 0
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_map_history_delete'
    AND tbl_name='legacy_migration_map'
    AND upper(sql) LIKE '%BEFORE DELETE ON LEGACY_MIGRATION_MAP%'
    AND instr(sql, 'RAISE(ABORT, ''LEGACY_MIGRATION_MAP_IMMUTABLE'')') > 0
)
""".strip(),
    """
SELECT normalized_path FROM legacy_migration_map
GROUP BY normalized_path
HAVING count(*) > 1
LIMIT 1
""".strip(),
)


__all__ = [
    "LEGACY_MIGRATION_MAP_IMMUTABLE_CODE",
    "LEGACY_MIGRATION_MAP_IMMUTABLE_TRIGGERS",
    "LEGACY_MIGRATION_MAP_LOCATOR_INDEX",
    "V003_POSTCONDITION_QUERIES",
    "V003_STATEMENTS",
]
