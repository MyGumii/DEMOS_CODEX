"""Forward-only root-aware temporal ledger without changing historical rows.

The v12 ledger was unique only by logical generation. A restored database in a
new physical projection root could therefore not register the same canonical
generation with its new root identity. Rebuild only this child table; all old
rows, IDs, manifests and root digests are copied verbatim.
"""

from .v012_temporal_projection_footer import V012_STATEMENTS


def _statement(prefix: str) -> str:
    matches = tuple(sql for sql in V012_STATEMENTS if sql.startswith(prefix))
    assert len(matches) == 1, prefix
    return matches[0]


_old_table = _statement("CREATE TABLE projection_manifest_v2_ledger(")
_old_unique = (
    "UNIQUE(space_id,content_generation,graph_generation,temporal_generation)"
)
assert _old_table.count(_old_unique) == 1
_new_table = _old_table.replace(
    "CREATE TABLE projection_manifest_v2_ledger(",
    "CREATE TABLE projection_manifest_v2_ledger_v52(",
    1,
).replace(
    _old_unique,
    "UNIQUE(space_id,content_generation,graph_generation,"
    "temporal_generation,projection_root_identity_sha256)",
    1,
)

V052_STATEMENTS = (
    "DROP TRIGGER trg_projection_manifest_v2_validity_insert",
    "DROP TRIGGER trg_projection_manifest_v2_history_update",
    "DROP TRIGGER trg_projection_manifest_v2_history_delete",
    _new_table,
    """INSERT INTO projection_manifest_v2_ledger_v52
       SELECT * FROM projection_manifest_v2_ledger""",
    "DROP TABLE projection_manifest_v2_ledger",
    "ALTER TABLE projection_manifest_v2_ledger_v52 "
    "RENAME TO projection_manifest_v2_ledger",
    _statement("CREATE INDEX ix_projection_manifest_v2_space_generation\n"),
    _statement("CREATE TRIGGER trg_projection_manifest_v2_validity_insert\n"),
    _statement("CREATE TRIGGER trg_projection_manifest_v2_history_update\n"),
    _statement("CREATE TRIGGER trg_projection_manifest_v2_history_delete\n"),
)

V052_POSTCONDITION_QUERIES = (
    """SELECT 'temporal_ledger_root_identity_missing'
       FROM projection_manifest_v2_ledger
      WHERE projection_root_identity_sha256 IS NULL
      LIMIT 1""".strip(),
)

__all__ = ["V052_STATEMENTS", "V052_POSTCONDITION_QUERIES"]
