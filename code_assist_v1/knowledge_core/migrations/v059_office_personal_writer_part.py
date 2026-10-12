"""Permit an authorized personal-only Office writer to retain a NULL Part.

The v56 writer-start trigger used SQL equality for two nullable Part IDs.
NULL = NULL is unknown, so a valid account with no Part could never start its
Office Wiki writer. Historical v56 SQL stays unchanged; this migration replaces
only that trigger with NULL-safe identity comparison.
"""

from .v056_office_hybrid_wiki import V056_STATEMENTS


_OLD = "j.authorization_part_id=parent.authorization_part_id"
_NEW = "j.authorization_part_id IS parent.authorization_part_id"
_TRIGGER = next(statement for statement in V056_STATEMENTS
    if statement.startswith("CREATE TRIGGER office_hybrid_wiki_run_insert\n"))
if _TRIGGER.count(_OLD) != 1:
    raise RuntimeError("The v56 Office writer Part condition changed unexpectedly.")


V059_STATEMENTS = (
    "DROP TRIGGER office_hybrid_wiki_run_insert",
    _TRIGGER.replace(_OLD, _NEW),
)

V059_POSTCONDITION_QUERIES = (
    """SELECT 'office_personal_writer_trigger_missing'
      WHERE NOT EXISTS(SELECT 1 FROM sqlite_schema
        WHERE type='trigger' AND name='office_hybrid_wiki_run_insert'
          AND instr(sql,'j.authorization_part_id IS parent.authorization_part_id')>0)""",
)


__all__ = ["V059_STATEMENTS", "V059_POSTCONDITION_QUERIES"]
