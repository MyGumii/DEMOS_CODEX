"""Forward-only durable targets for reviewed P2.4 record/relationship effects.

Versions 1 through 4 remain frozen.  Version 5 adds nullable finding/audit
targets to the existing immutable record ledger and a separate append-only
relationship ledger for graph-node, graph-edge, candidate, and exact-repeat
closure.  Existing P2.3 rows remain byte-for-byte unchanged.
"""

from __future__ import annotations


LEGACY_MIGRATION_P24_RELATIONSHIP_TABLE = "legacy_migration_p24_relationship_map"
LEGACY_MIGRATION_P24_RELATIONSHIP_IMMUTABLE_CODE = (
    "LEGACY_MIGRATION_P24_RELATIONSHIP_MAP_IMMUTABLE"
)


V005_STATEMENTS: tuple[str, ...] = (
    """
ALTER TABLE legacy_migration_map ADD COLUMN finding_id TEXT CHECK(
    finding_id IS NULL OR (
        length(finding_id)=36 AND substr(finding_id,9,1)='-'
        AND substr(finding_id,14,1)='-' AND substr(finding_id,19,1)='-'
        AND substr(finding_id,24,1)='-'
        AND length(replace(finding_id,'-',''))=32
        AND replace(finding_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND finding_id=lower(finding_id)
    )
) REFERENCES curation_finding(id) ON UPDATE RESTRICT ON DELETE RESTRICT
""".strip(),
    """
ALTER TABLE legacy_migration_map ADD COLUMN audit_event_id INTEGER CHECK(
    audit_event_id IS NULL OR audit_event_id>=1
) REFERENCES audit_event(id) ON UPDATE RESTRICT ON DELETE RESTRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_finding_target
ON legacy_migration_map(finding_id) WHERE finding_id IS NOT NULL
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_audit_event_target
ON legacy_migration_map(audit_event_id) WHERE audit_event_id IS NOT NULL
""".strip(),
    """
CREATE TABLE legacy_migration_p24_relationship_map(
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
        length(relationship_id)=28 AND substr(relationship_id,1,4)='rel_'
        AND substr(relationship_id,5) NOT GLOB '*[^0-9a-f]*'
        AND relationship_id=lower(relationship_id)
    ),
    relationship_sha256 TEXT NOT NULL CHECK(
        length(relationship_sha256)=64
        AND relationship_sha256=lower(relationship_sha256)
        AND relationship_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    relationship_type TEXT NOT NULL CHECK(relationship_type IN (
        'candidate_source','candidate_target','graph_node_source',
        'contradicts','extends','related','supports','exact_content_duplicate'
    )),
    source_mapping_id TEXT CHECK(
        source_mapping_id IS NULL OR (
            length(source_mapping_id)=36 AND substr(source_mapping_id,9,1)='-'
            AND substr(source_mapping_id,14,1)='-'
            AND substr(source_mapping_id,19,1)='-'
            AND substr(source_mapping_id,24,1)='-'
            AND length(replace(source_mapping_id,'-',''))=32
            AND replace(source_mapping_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND source_mapping_id=lower(source_mapping_id)
        )
    ) REFERENCES legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    from_mapping_id TEXT CHECK(
        from_mapping_id IS NULL OR (
            length(from_mapping_id)=36 AND substr(from_mapping_id,9,1)='-'
            AND substr(from_mapping_id,14,1)='-'
            AND substr(from_mapping_id,19,1)='-'
            AND substr(from_mapping_id,24,1)='-'
            AND length(replace(from_mapping_id,'-',''))=32
            AND replace(from_mapping_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND from_mapping_id=lower(from_mapping_id)
        )
    ) REFERENCES legacy_migration_map(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    to_mapping_id TEXT NOT NULL CHECK(
        length(to_mapping_id)=36 AND substr(to_mapping_id,9,1)='-'
        AND substr(to_mapping_id,14,1)='-' AND substr(to_mapping_id,19,1)='-'
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
        AND substr(target_space_id,14,1)='-' AND substr(target_space_id,19,1)='-'
        AND substr(target_space_id,24,1)='-'
        AND length(replace(target_space_id,'-',''))=32
        AND replace(target_space_id,'-','') NOT GLOB '*[^0-9a-f]*'
        AND target_space_id=lower(target_space_id)
    ) REFERENCES knowledge_space(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    effect_kind TEXT NOT NULL CHECK(
        effect_kind IN ('dependency','edge','finding','provenance')
    ),
    edge_id TEXT CHECK(
        edge_id IS NULL OR (
            length(edge_id)=36 AND substr(edge_id,9,1)='-'
            AND substr(edge_id,14,1)='-' AND substr(edge_id,19,1)='-'
            AND substr(edge_id,24,1)='-'
            AND length(replace(edge_id,'-',''))=32
            AND replace(edge_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND edge_id=lower(edge_id)
        )
    ) REFERENCES knowledge_edge(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    finding_id TEXT CHECK(
        finding_id IS NULL OR (
            length(finding_id)=36 AND substr(finding_id,9,1)='-'
            AND substr(finding_id,14,1)='-' AND substr(finding_id,19,1)='-'
            AND substr(finding_id,24,1)='-'
            AND length(replace(finding_id,'-',''))=32
            AND replace(finding_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND finding_id=lower(finding_id)
        )
    ) REFERENCES curation_finding(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    item_id TEXT CHECK(
        item_id IS NULL OR (
            length(item_id)=36 AND substr(item_id,9,1)='-'
            AND substr(item_id,14,1)='-' AND substr(item_id,19,1)='-'
            AND substr(item_id,24,1)='-'
            AND length(replace(item_id,'-',''))=32
            AND replace(item_id,'-','') NOT GLOB '*[^0-9a-f]*'
            AND item_id=lower(item_id)
        )
    ) REFERENCES knowledge_item(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK(status='imported'),
    details_json TEXT NOT NULL CHECK(
        json_valid(details_json) AND json_type(details_json)='object'
    ),
    created_at INTEGER NOT NULL CHECK(created_at>=0),
    updated_at INTEGER NOT NULL CHECK(updated_at>=0),
    CHECK(source_mapping_id IS NOT NULL OR from_mapping_id IS NOT NULL),
    CHECK(source_mapping_id IS NULL OR source_mapping_id<>to_mapping_id),
    CHECK(from_mapping_id IS NULL OR from_mapping_id<>to_mapping_id),
    CHECK(
        (effect_kind='dependency' AND edge_id IS NULL AND finding_id IS NULL AND item_id IS NULL)
        OR (effect_kind='edge' AND edge_id IS NOT NULL AND finding_id IS NULL AND item_id IS NULL)
        OR (effect_kind='finding' AND edge_id IS NULL AND finding_id IS NOT NULL AND item_id IS NULL)
        OR (effect_kind='provenance' AND edge_id IS NULL AND finding_id IS NULL AND item_id IS NOT NULL)
    ),
    CHECK(created_at=updated_at)
) STRICT
""".strip(),
    """
CREATE UNIQUE INDEX ux_migration_p24_relationship_identity
ON legacy_migration_p24_relationship_map(relationship_id)
""".strip(),
    """
CREATE INDEX ix_migration_p24_relationship_run
ON legacy_migration_p24_relationship_map(migration_run_id,status,id)
""".strip(),
    """
CREATE INDEX ix_migration_p24_relationship_endpoints
ON legacy_migration_p24_relationship_map(
    source_mapping_id,from_mapping_id,to_mapping_id,relationship_id
)
""".strip(),
    """
CREATE INDEX ix_migration_p24_relationship_effect
ON legacy_migration_p24_relationship_map(effect_kind,edge_id,finding_id,item_id)
""".strip(),
    """
CREATE TRIGGER trg_legacy_migration_p24_relationship_history_update
BEFORE UPDATE ON legacy_migration_p24_relationship_map
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'LEGACY_MIGRATION_P24_RELATIONSHIP_MAP_IMMUTABLE');
END
""".strip(),
    """
CREATE TRIGGER trg_legacy_migration_p24_relationship_history_delete
BEFORE DELETE ON legacy_migration_p24_relationship_map
FOR EACH ROW
BEGIN
  SELECT RAISE(ABORT, 'LEGACY_MIGRATION_P24_RELATIONSHIP_MAP_IMMUTABLE');
END
""".strip(),
)


V005_POSTCONDITION_QUERIES: tuple[str, ...] = (
    """
SELECT 'legacy_migration_p24_record_targets_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='index' AND name='ux_migration_finding_target'
    AND tbl_name='legacy_migration_map'
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='index' AND name='ux_migration_audit_event_target'
    AND tbl_name='legacy_migration_map'
)
""".strip(),
    """
SELECT 'legacy_migration_p24_relationship_table_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='table' AND name='legacy_migration_p24_relationship_map'
    AND tbl_name='legacy_migration_p24_relationship_map'
)
""".strip(),
    """
SELECT 'legacy_migration_p24_relationship_indexes_missing'
WHERE (
  SELECT count(*) FROM sqlite_schema
  WHERE type='index' AND tbl_name='legacy_migration_p24_relationship_map'
    AND name IN (
      'ux_migration_p24_relationship_identity',
      'ix_migration_p24_relationship_run',
      'ix_migration_p24_relationship_endpoints',
      'ix_migration_p24_relationship_effect'
    )
)<>4
""".strip(),
    """
SELECT 'legacy_migration_p24_relationship_triggers_missing'
WHERE NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_p24_relationship_history_update'
    AND tbl_name='legacy_migration_p24_relationship_map'
    AND instr(sql,'LEGACY_MIGRATION_P24_RELATIONSHIP_MAP_IMMUTABLE')>0
)
OR NOT EXISTS (
  SELECT 1 FROM sqlite_schema
  WHERE type='trigger'
    AND name='trg_legacy_migration_p24_relationship_history_delete'
    AND tbl_name='legacy_migration_p24_relationship_map'
    AND instr(sql,'LEGACY_MIGRATION_P24_RELATIONSHIP_MAP_IMMUTABLE')>0
)
""".strip(),
    """
SELECT 'legacy_migration_p24_target_columns_unusable'
FROM legacy_migration_map
WHERE finding_id IS NOT NULL AND audit_event_id IS NOT NULL
LIMIT 1
""".strip(),
)


__all__ = [
    "LEGACY_MIGRATION_P24_RELATIONSHIP_IMMUTABLE_CODE",
    "LEGACY_MIGRATION_P24_RELATIONSHIP_TABLE",
    "V005_POSTCONDITION_QUERIES",
    "V005_STATEMENTS",
]
