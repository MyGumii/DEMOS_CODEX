"""Forward-only scoped-context policy; frozen v24 rows remain unchanged.

V5 changes the authoring prompt/semantics while deliberately retaining V4's
bounded-neighbor manifest and quote-location storage contract.  This migration
only admits the explicit successor into the existing one-inventory gates.
"""

from .v024_boundary_context_authoring import (
    V024_POSTCONDITION_QUERIES,
    V024_STATEMENTS,
)


_BOUNDARY = "wiki-information-authoring-v4-boundary-context"
_SUCCESSOR = "wiki-information-authoring-v5-scoped-context"
_SOURCE_CONTEXT_VERSION = "wiki-source-neighbors-v1"
_OLD_AUTHORING = (
    "r.policy_version IN ('wiki-information-authoring-v1',"
    "'wiki-information-authoring-v2-structure',"
    "'wiki-information-authoring-v3-full-labels',"
    f"'{_BOUNDARY}')"
)
_NEW_AUTHORING = _OLD_AUTHORING[:-1] + f",'{_SUCCESSOR}')"
_OLD_STRUCTURE = (
    "r.policy_version NOT IN ('wiki-information-authoring-v2-structure',"
    "'wiki-information-authoring-v3-full-labels',"
    f"'{_BOUNDARY}')"
)
_NEW_STRUCTURE = _OLD_STRUCTURE[:-1] + f",'{_SUCCESSOR}')"
_OLD_RANGE = f"r.policy_version<>'{_BOUNDARY}' AND NOT ("
_NEW_RANGE = (
    "r.policy_version NOT IN "
    f"('{_BOUNDARY}','{_SUCCESSOR}') AND NOT ("
)
_OLD_CONTEXT_GATE = f"r.policy_version<>'{_BOUNDARY}' OR ("
_NEW_CONTEXT_GATE = (
    "r.policy_version NOT IN "
    f"('{_BOUNDARY}','{_SUCCESSOR}') OR ("
)
_BOUNDARY_GROUP = (
    "r.policy_version NOT IN "
    f"('{_BOUNDARY}','{_SUCCESSOR}')"
)

if (
    sum(statement.count(_OLD_AUTHORING) for statement in V024_STATEMENTS) != 4
    or sum(statement.count(_OLD_STRUCTURE) for statement in V024_STATEMENTS) != 1
    or sum(statement.count(_OLD_RANGE) for statement in V024_STATEMENTS) != 1
    or sum(statement.count(_OLD_CONTEXT_GATE) for statement in V024_STATEMENTS)
    != 1
):
    raise RuntimeError("The frozen v24 scoped-context successor anchors changed.")


def _successor(statement: str) -> str:
    return (
        statement.replace(_OLD_AUTHORING, _NEW_AUTHORING)
        .replace(_OLD_STRUCTURE, _NEW_STRUCTURE)
        .replace(_OLD_RANGE, _NEW_RANGE)
        .replace(_OLD_CONTEXT_GATE, _NEW_CONTEXT_GATE)
    )


V025_STATEMENTS = tuple(_successor(statement) for statement in V024_STATEMENTS)

_ESCAPED_GROUP = _BOUNDARY_GROUP.replace("'", "''")
V025_POSTCONDITION_QUERIES = (
    *V024_POSTCONDITION_QUERIES,
    f"""SELECT 'scoped-context authoring successor gates missing' WHERE 4<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND instr(sql,'{_SUCCESSOR}')>0)""",
    f"""SELECT 'scoped-context boundary group is not exact' WHERE NOT EXISTS(
      SELECT 1 FROM sqlite_schema WHERE type='trigger'
        AND name='preserved_wiki_snapshot_body_binding'
        AND 2=(length(sql)-length(replace(sql,'{_ESCAPED_GROUP}','')))
              /length('{_ESCAPED_GROUP}')
        AND instr(sql,'{_SOURCE_CONTEXT_VERSION}')>0
        AND instr(sql,'source_chunk_id')>0)""",
    f"""SELECT 'scoped-context manifest boundary gate escaped snapshot body' WHERE 1<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND instr(sql,'{_SUCCESSOR}')>0
        AND instr(sql,'{_SOURCE_CONTEXT_VERSION}')>0
        AND instr(sql,'source_chunk_id')>0)""",
)


__all__ = ["V025_POSTCONDITION_QUERIES", "V025_STATEMENTS"]
