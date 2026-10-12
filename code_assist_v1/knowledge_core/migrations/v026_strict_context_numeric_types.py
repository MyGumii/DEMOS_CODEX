"""Forward-only strict numeric types for structured chunk source ranges.

V26 narrows V2-V5 structured manifest storage without changing V1. Frozen
V1-V25 migrations, valid receipts, and retained rows remain byte-for-byte unchanged.
"""

from .v025_scoped_context_authoring import (
    V025_POSTCONDITION_QUERIES,
    V025_STATEMENTS,
)


_BOUNDARY = "wiki-information-authoring-v4-boundary-context"
_SCOPED = "wiki-information-authoring-v5-scoped-context"
_STRUCTURED = "wiki-information-authoring-v2-structure"
_CAPTION = "wiki-information-authoring-v3-full-labels"
_OLD_RANGE_SHAPE = """OR json_type(c.value,'$.source_range') IS NOT 'array'
              OR json_array_length(c.value,'$.source_range') IS NOT 2"""
_STRICT_RANGE_SHAPE = _OLD_RANGE_SHAPE + """
              OR json_type(c.value,'$.source_range[0]') IS NOT 'integer'
              OR json_type(c.value,'$.source_range[1]') IS NOT 'integer'"""
_START_TYPE = "json_type(c.value,'$.source_range[0]') IS NOT 'integer'"
_END_TYPE = "json_type(c.value,'$.source_range[1]') IS NOT 'integer'"
_STRUCTURED_POLICIES = (
    f"'{_STRUCTURED}','{_CAPTION}','{_BOUNDARY}','{_SCOPED}'"
)

if (
    sum(statement.count(_OLD_RANGE_SHAPE) for statement in V025_STATEMENTS) != 1
    or sum(statement.count(_START_TYPE) for statement in V025_STATEMENTS) != 0
    or sum(statement.count(_END_TYPE) for statement in V025_STATEMENTS) != 0
):
    raise RuntimeError("The frozen v25 strict numeric-type anchor changed.")


V026_STATEMENTS = tuple(
    statement.replace(_OLD_RANGE_SHAPE, _STRICT_RANGE_SHAPE)
    for statement in V025_STATEMENTS
)

_ESCAPED_START_TYPE = _START_TYPE.replace("'", "''")
_ESCAPED_END_TYPE = _END_TYPE.replace("'", "''")
V026_POSTCONDITION_QUERIES = (
    *V025_POSTCONDITION_QUERIES,
    f"""SELECT 'strict context numeric-type gate missing' WHERE NOT EXISTS(
      SELECT 1 FROM sqlite_schema WHERE type='trigger'
        AND name='preserved_wiki_snapshot_body_binding'
        AND 1=(length(sql)-length(replace(sql,'{_ESCAPED_START_TYPE}','')))
              /length('{_ESCAPED_START_TYPE}')
        AND 1=(length(sql)-length(replace(sql,'{_ESCAPED_END_TYPE}','')))
              /length('{_ESCAPED_END_TYPE}'))""",
    f"""SELECT 'strict context numeric-type gate escaped snapshot body' WHERE 1<>(
      SELECT count(*) FROM sqlite_schema WHERE type='trigger'
        AND instr(sql,'{_ESCAPED_START_TYPE}')>0
        AND instr(sql,'{_ESCAPED_END_TYPE}')>0)""",
    f"""SELECT 'retained structured chunk source range has non-integer endpoint'
      WHERE EXISTS(
        SELECT 1 FROM preserved_wiki_snapshot_body body
        JOIN preserved_wiki_snapshot snapshot ON snapshot.id=body.snapshot_id
        JOIN publication_preservation_run r ON r.id=snapshot.run_id,
          json_each(body.manifest_json,'$.chunks') c
        WHERE r.policy_version IN ({_STRUCTURED_POLICIES})
          AND (json_type(c.value,'$.source_range[0]') IS NOT 'integer'
            OR json_type(c.value,'$.source_range[1]') IS NOT 'integer'))""",
)


__all__ = ["V026_POSTCONDITION_QUERIES", "V026_STATEMENTS"]
