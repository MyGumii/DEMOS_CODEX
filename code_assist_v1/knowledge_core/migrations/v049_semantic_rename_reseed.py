"""Forward-only rename reseed provenance over the frozen v10/v48 graph schema.

Both parent tables have closed CHECK constraints.  SQLite cannot widen those
in place, so the deployment migrator rebuilds them with foreign keys disabled
inside its exclusive transaction, checks every foreign key, and restores the
ordinary connection mode before any application connection is opened.
"""

from .v010_semantic_graph_contracts import V010_STATEMENTS
from .v048_native_semantic_targets import V048_STATEMENTS


def _statement(prefix: str) -> str:
    matches = tuple(sql for sql in V010_STATEMENTS if sql.startswith(prefix))
    assert len(matches) == 1, prefix
    return matches[0]


def _replace_once(sql: str, old: str, new: str) -> str:
    assert sql.count(old) == 1, old
    return sql.replace(old, new)


_seed_table = _statement("CREATE TABLE semantic_edge_intent_seed_run(")
_seed_table = _replace_once(
    _seed_table,
    "'baseline_backfill','org_retarget','detector_reseed','lifecycle_reactivate'",
    "'baseline_backfill','org_retarget','org_rename_reseed','detector_reseed','lifecycle_reactivate'",
)
_seed_table = _replace_once(
    _seed_table,
    "      (cause_kind='detector_reseed' AND scope_kind='global'",
    """      (cause_kind='org_rename_reseed' AND scope_kind='org_unit'
       AND organization_event_audit_id IS NOT NULL
       AND old_ancestor_space_id IS NULL AND new_ancestor_space_id IS NULL
       AND previous_detector_generation IS NULL
       AND detector_rollout_audit_id IS NULL
       AND lifecycle_restore_audit_id IS NULL)
      OR
      (cause_kind='detector_reseed' AND scope_kind='global'""",
)
_seed_table = _replace_once(
    _seed_table,
    "CREATE TABLE semantic_edge_intent_seed_run(",
    "CREATE TABLE semantic_edge_intent_seed_run_v49(",
)

_seed_insert = _statement("CREATE TRIGGER trg_semantic_seed_state_insert\n")
_seed_insert = _replace_once(
    _seed_insert,
    "SELECT 1 FROM org_unit o WHERE o.id=NEW.scope_id AND o.status='active'",
    """SELECT 1 FROM org_unit o WHERE o.id=NEW.scope_id
       AND (o.status='active' OR NEW.cause_kind='org_rename_reseed')""",
)
_seed_insert = _replace_once(
    _seed_insert,
    "BEGIN SELECT RAISE(ABORT,'SEMANTIC_SEED_TRANSITION_INVALID'); END",
    """ OR (NEW.cause_kind='org_rename_reseed' AND NOT EXISTS (
   SELECT 1 FROM audit_event a JOIN org_unit o ON o.id=NEW.scope_id
   WHERE a.id=NEW.organization_event_audit_id
     AND a.action='organization.unit.rename' AND a.outcome='renamed'
     AND json_extract(a.details_json,'$.resource_id')=NEW.scope_id
     AND json_type(a.details_json,'$.row_version')='integer'
     AND json_extract(a.details_json,'$.row_version') BETWEEN 1 AND o.row_version
     AND o.kind IN ('part','team')
 ))
BEGIN SELECT RAISE(ABORT,'SEMANTIC_SEED_TRANSITION_INVALID'); END""",
)

_intent_table = _statement("CREATE TABLE semantic_edge_intent(")
_intent_table = _replace_once(
    _intent_table,
    "'publication','baseline_backfill','org_retarget','detector_reseed',",
    "'publication','baseline_backfill','org_retarget','org_rename_reseed','detector_reseed',",
)
_intent_table = _replace_once(
    _intent_table,
    "         OR (provenance_kind='detector_reseed'",
    """         OR (provenance_kind='org_rename_reseed'
             AND organization_event_audit_id IS NOT NULL
             AND detector_rollout_audit_id IS NULL
             AND lifecycle_restore_audit_id IS NULL)
         OR (provenance_kind='detector_reseed'""",
)
_intent_table = _replace_once(
    _intent_table,
    "CREATE TABLE semantic_edge_intent(",
    "CREATE TABLE semantic_edge_intent_v49(",
)

_intent_insert = V048_STATEMENTS[1]
assert _intent_insert.startswith("CREATE TRIGGER trg_semantic_intent_validity_insert\n")
_intent_insert = _replace_once(
    _intent_insert,
    "      OR (sr.scope_kind='org_unit' AND NEW.provenance_kind='org_retarget')",
    """      OR (sr.scope_kind='org_unit' AND NEW.provenance_kind='org_retarget')
      OR (sr.scope_kind='org_unit' AND NEW.provenance_kind='org_rename_reseed'
          AND sr.scope_id=NEW.target_org_unit_id
          AND EXISTS (
            SELECT 1 FROM audit_event a
            WHERE a.id=NEW.organization_event_audit_id
              AND a.action='organization.unit.rename' AND a.outcome='renamed'
              AND json_extract(a.details_json,'$.resource_id')=sr.scope_id
              AND json_extract(a.details_json,'$.row_version')=NEW.organization_generation
          ))""",
)


V049_STATEMENTS = (
    """CREATE TABLE semantic_rename_reseed_boundary(
         id INTEGER PRIMARY KEY CHECK(id=1),
         legacy_audit_high_water INTEGER NOT NULL CHECK(legacy_audit_high_water>=0)
       ) STRICT""",
    """INSERT INTO semantic_rename_reseed_boundary(id,legacy_audit_high_water)
       SELECT 1,coalesce(max(id),0) FROM audit_event""",
    """CREATE TRIGGER trg_semantic_rename_boundary_update
       BEFORE UPDATE ON semantic_rename_reseed_boundary
       BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    """CREATE TRIGGER trg_semantic_rename_boundary_delete
       BEFORE DELETE ON semantic_rename_reseed_boundary
       BEGIN SELECT RAISE(ABORT,'HISTORY_IMMUTABLE'); END""",
    "DROP TRIGGER trg_semantic_intent_validity_insert",
    "DROP TRIGGER trg_semantic_intent_history_update",
    "DROP TRIGGER trg_semantic_intent_history_delete",
    "DROP TRIGGER trg_semantic_seed_state_insert",
    "DROP TRIGGER trg_semantic_seed_transition_update",
    "DROP TRIGGER trg_semantic_seed_history_delete",
    _seed_table,
    "INSERT INTO semantic_edge_intent_seed_run_v49 SELECT * FROM semantic_edge_intent_seed_run",
    "DROP TABLE semantic_edge_intent_seed_run",
    "ALTER TABLE semantic_edge_intent_seed_run_v49 RENAME TO semantic_edge_intent_seed_run",
    _statement("CREATE INDEX ix_semantic_seed_state_cursor\n"),
    _statement("CREATE INDEX ix_semantic_seed_scope_history\n"),
    _seed_insert,
    _statement("CREATE TRIGGER trg_semantic_seed_transition_update\n"),
    _statement("CREATE TRIGGER trg_semantic_seed_history_delete\n"),
    _intent_table,
    "INSERT INTO semantic_edge_intent_v49 SELECT * FROM semantic_edge_intent",
    "DROP TABLE semantic_edge_intent",
    "ALTER TABLE semantic_edge_intent_v49 RENAME TO semantic_edge_intent",
    _statement("CREATE UNIQUE INDEX ux_semantic_intent_generation_identity\n"),
    _statement("CREATE INDEX ix_semantic_intent_materialize\n"),
    _statement("CREATE INDEX ix_semantic_intent_source\n"),
    _intent_insert,
    _statement("CREATE TRIGGER trg_semantic_intent_history_update\n"),
    _statement("CREATE TRIGGER trg_semantic_intent_history_delete\n"),
)

V049_POSTCONDITION_QUERIES = (
    """SELECT 'rename_boundary_invalid'
       WHERE (SELECT count(*) FROM semantic_rename_reseed_boundary)<>1
          OR (SELECT id FROM semantic_rename_reseed_boundary)<>1
          OR (SELECT legacy_audit_high_water FROM semantic_rename_reseed_boundary)<0""".strip(),
    """SELECT 'rename_seed_proof_invalid'
       FROM semantic_edge_intent_seed_run sr
       LEFT JOIN audit_event a ON a.id=sr.organization_event_audit_id
      WHERE sr.cause_kind='org_rename_reseed'
        AND (sr.scope_kind<>'org_unit' OR a.action<>'organization.unit.rename'
             OR a.outcome<>'renamed'
             OR json_extract(a.details_json,'$.resource_id')<>sr.scope_id)
      LIMIT 1""".strip(),
    """SELECT 'rename_intent_target_invalid'
       FROM semantic_edge_intent i
       JOIN semantic_edge_intent_seed_run sr ON sr.id=i.seed_run_id
      WHERE i.provenance_kind='org_rename_reseed'
        AND (sr.cause_kind<>'org_rename_reseed'
             OR sr.scope_id<>i.target_org_unit_id
             OR i.organization_event_audit_id<>sr.organization_event_audit_id)
      LIMIT 1""".strip(),
)
