"""Forward-only bounded-neighbor authoring policy; frozen v23 rows unchanged.

V4 keeps the one-inventory-receipt authoring contract.  It widens quote
evidence only to the exact previous/next chunks recorded in the same run and
requires every fact to retain at least one quote from its owned core chunk.
"""

from .v023_full_label_authoring import V023_STATEMENTS


_SUCCESSOR = "wiki-information-authoring-v4-boundary-context"
_SOURCE_CONTEXT_VERSION = "wiki-source-neighbors-v1"
_OLD_AUTHORING = (
    "r.policy_version IN ('wiki-information-authoring-v1',"
    "'wiki-information-authoring-v2-structure',"
    "'wiki-information-authoring-v3-full-labels')"
)
_NEW_AUTHORING = _OLD_AUTHORING[:-1] + f",'{_SUCCESSOR}')"
_OLD_STRUCTURE = (
    "r.policy_version NOT IN ('wiki-information-authoring-v2-structure',"
    "'wiki-information-authoring-v3-full-labels')"
)
_NEW_STRUCTURE = _OLD_STRUCTURE[:-1] + f",'{_SUCCESSOR}')"
_CURRENT_RANGE = """OR NOT (json_extract(c.value,'$.source_range[0]')<=json_extract(sr.value,'$[0]')
                            AND json_extract(sr.value,'$[0]')<json_extract(sr.value,'$[1]')
                            AND json_extract(sr.value,'$[1]')<=json_extract(c.value,'$.source_range[1]'))"""
_VERSIONED_RANGE = f"""OR (r.policy_version<>'{_SUCCESSOR}' AND NOT (
                            json_extract(c.value,'$.source_range[0]')<=json_extract(sr.value,'$[0]')
                            AND json_extract(sr.value,'$[0]')<json_extract(sr.value,'$[1]')
                            AND json_extract(sr.value,'$[1]')<=json_extract(c.value,'$.source_range[1]')))"""
_MANIFEST_ANCHOR = (
    "AND json_extract(NEW.manifest_json,'$.structure_semantics')="
    "'exact_extracted_markdown_locations_not_fact_entailment'"
)

if (
    sum(statement.count(_OLD_AUTHORING) for statement in V023_STATEMENTS) != 4
    or sum(statement.count(_OLD_STRUCTURE) for statement in V023_STATEMENTS) != 1
    or sum(statement.count(_CURRENT_RANGE) for statement in V023_STATEMENTS) != 1
    or sum(statement.count(_MANIFEST_ANCHOR) for statement in V023_STATEMENTS) != 1
):
    raise RuntimeError("The frozen v23 boundary-authoring anchors changed.")


_BOUNDARY_CONTEXT_GATE = f"""
          AND (r.policy_version<>'{_SUCCESSOR}' OR (
            json_extract(NEW.manifest_json,'$.source_context_version')='{_SOURCE_CONTEXT_VERSION}'
            AND NOT EXISTS(
              SELECT 1 FROM json_each(NEW.manifest_json,'$.chunks') c
              JOIN publication_preservation_chunk core
                ON core.run_id=r.id AND core.ordinal=c.key+1
              WHERE json_type(c.value,'$.source_context') IS NOT 'array'
                OR json_array_length(c.value,'$.source_context') IS NOT
                  ((core.ordinal>1)+(core.ordinal<r.chunk_count))
                OR EXISTS(
                  SELECT 1 FROM json_each(c.value,'$.source_context') context
                  WHERE context.type IS NOT 'object'
                    OR CASE WHEN context.type IS NOT 'object' THEN 0 ELSE (
                      (SELECT count(*) FROM json_each(context.value)) IS NOT 4
                      OR EXISTS(SELECT 1 FROM json_each(context.value) member
                        WHERE member.key NOT IN ('chunk_id','ordinal','source_range','source_sha256'))
                      OR json_type(context.value,'$.chunk_id') IS NOT 'text'
                      OR json_type(context.value,'$.ordinal') IS NOT 'integer'
                      OR json_type(context.value,'$.source_range') IS NOT 'array'
                      OR json_array_length(context.value,'$.source_range') IS NOT 2
                      OR json_type(context.value,'$.source_range[0]') IS NOT 'integer'
                      OR json_type(context.value,'$.source_range[1]') IS NOT 'integer'
                      OR json_type(context.value,'$.source_sha256') IS NOT 'text'
                      OR length(json_extract(context.value,'$.source_sha256')) IS NOT 64
                      OR json_extract(context.value,'$.source_sha256') GLOB '*[^0-9a-f]*'
                      OR NOT EXISTS(
                        SELECT 1 FROM publication_preservation_chunk neighbor
                        WHERE neighbor.run_id=r.id
                          AND neighbor.ordinal=CASE
                            WHEN core.ordinal>1 THEN core.ordinal-1+(2*context.key)
                            ELSE core.ordinal+1 END
                          AND neighbor.ordinal=json_extract(context.value,'$.ordinal')
                          AND neighbor.chunk_id=json_extract(context.value,'$.chunk_id')
                          AND neighbor.source_start=json_extract(context.value,'$.source_range[0]')
                          AND neighbor.source_end=json_extract(context.value,'$.source_range[1]')
                          AND neighbor.source_sha256=json_extract(context.value,'$.source_sha256'))
                    ) END)
                OR EXISTS(
                  SELECT 1 FROM json_each(c.value,'$.source_locations') location
                  WHERE location.type IS NOT 'object'
                    OR CASE WHEN location.type IS NOT 'object' THEN 0 ELSE (
                      (SELECT count(*) FROM json_each(location.value)) IS NOT 3
                      OR EXISTS(SELECT 1 FROM json_each(location.value) member
                        WHERE member.key NOT IN ('fact_id','heading_paths','quotes'))
                      OR json_array_length(c.value,
                           '$.facts['||location.key||'].quotes') IS NOT
                         json_array_length(c.value,
                           '$.facts['||location.key||'].source_ranges')
                      OR EXISTS(
                        SELECT 1 FROM json_each(location.value,'$.quotes') quote
                        WHERE quote.type IS NOT 'object'
                          OR CASE WHEN quote.type IS NOT 'object' THEN 0 ELSE (
                            (SELECT count(*) FROM json_each(quote.value)) IS NOT 4
                            OR EXISTS(SELECT 1 FROM json_each(quote.value) member
                              WHERE member.key NOT IN
                                ('quote_sha256','heading_paths','source_ranges','source_chunk_id'))
                            OR json_type(quote.value,'$.source_chunk_id') IS NOT 'text'
                            OR json_type(quote.value,'$.quote_sha256') IS NOT 'text'
                            OR length(json_extract(quote.value,'$.quote_sha256')) IS NOT 64
                            OR json_extract(quote.value,'$.quote_sha256') GLOB '*[^0-9a-f]*'
                            OR json_type(quote.value,'$.source_ranges') IS NOT 'array'
                            OR json_array_length(quote.value,'$.source_ranges') IS NOT 1
                            OR json_type(quote.value,'$.source_ranges[0]') IS NOT 'array'
                            OR json_array_length(quote.value,'$.source_ranges[0]') IS NOT 2
                            OR json_type(quote.value,'$.source_ranges[0][0]') IS NOT 'integer'
                            OR json_type(quote.value,'$.source_ranges[0][1]') IS NOT 'integer'
                            OR json_type(c.value,
                                 '$.facts['||location.key||'].quotes['||quote.key||']') IS NOT 'text'
                            OR json_type(c.value,
                                 '$.facts['||location.key||'].source_ranges['||quote.key||']') IS NOT 'array'
                            OR json_array_length(c.value,
                                 '$.facts['||location.key||'].source_ranges['||quote.key||']') IS NOT 2
                            OR json_extract(c.value,
                                 '$.facts['||location.key||'].source_ranges['||quote.key||'][0]')
                               IS NOT json_extract(quote.value,'$.source_ranges[0][0]')
                            OR json_extract(c.value,
                                 '$.facts['||location.key||'].source_ranges['||quote.key||'][1]')
                               IS NOT json_extract(quote.value,'$.source_ranges[0][1]')
                            OR NOT EXISTS(
                              SELECT 1 FROM publication_preservation_chunk evidence
                              WHERE evidence.run_id=r.id
                                AND evidence.chunk_id=json_extract(quote.value,'$.source_chunk_id')
                                AND (evidence.ordinal=core.ordinal OR EXISTS(
                                  SELECT 1 FROM json_each(c.value,'$.source_context') context
                                  WHERE context.type='object'
                                    AND json_extract(context.value,'$.chunk_id')=evidence.chunk_id
                                    AND json_extract(context.value,'$.ordinal')=evidence.ordinal))
                                AND evidence.source_start<=json_extract(quote.value,'$.source_ranges[0][0]')
                                AND json_extract(quote.value,'$.source_ranges[0][0]')
                                  <json_extract(quote.value,'$.source_ranges[0][1]')
                                AND json_extract(quote.value,'$.source_ranges[0][1]')<=evidence.source_end
                                AND substr(x.content_md,
                                      json_extract(quote.value,'$.source_ranges[0][0]')+1,
                                      json_extract(quote.value,'$.source_ranges[0][1]')
                                        -json_extract(quote.value,'$.source_ranges[0][0]'))
                                  =json_extract(c.value,
                                      '$.facts['||location.key||'].quotes['||quote.key||']'))
                          ) END)
                      OR NOT EXISTS(
                        SELECT 1 FROM json_each(location.value,'$.quotes') core_quote
                        WHERE core_quote.type='object'
                          AND json_extract(core_quote.value,'$.source_chunk_id')=core.chunk_id
                          AND core.source_start<=json_extract(core_quote.value,'$.source_ranges[0][0]')
                          AND json_extract(core_quote.value,'$.source_ranges[0][0]')
                            <json_extract(core_quote.value,'$.source_ranges[0][1]')
                          AND json_extract(core_quote.value,'$.source_ranges[0][1]')<=core.source_end)
                    ) END)
            )
          ))"""


def _successor(statement: str) -> str:
    result = statement.replace(_OLD_AUTHORING, _NEW_AUTHORING).replace(
        _OLD_STRUCTURE, _NEW_STRUCTURE
    )
    if result.startswith("CREATE TRIGGER preserved_wiki_snapshot_body_binding"):
        if result.count(_CURRENT_RANGE) != 1 or result.count(_MANIFEST_ANCHOR) != 1:
            raise RuntimeError("The frozen v23 snapshot-body gate changed.")
        result = result.replace(_CURRENT_RANGE, _VERSIONED_RANGE)
        result = result.replace(
            _MANIFEST_ANCHOR, _MANIFEST_ANCHOR + _BOUNDARY_CONTEXT_GATE
        )
    return result


V024_STATEMENTS = tuple(_successor(statement) for statement in V023_STATEMENTS)
V024_POSTCONDITION_QUERIES = (
    f"""SELECT 'boundary-context authoring successor gates missing' WHERE 4<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND instr(sql,'{_SUCCESSOR}')>0)""",
    f"""SELECT 'boundary-context manifest gate missing' WHERE 1<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND name='preserved_wiki_snapshot_body_binding'
        AND instr(sql,'{_SOURCE_CONTEXT_VERSION}')>0
        AND instr(sql,'source_chunk_id')>0)""",
)


__all__ = ["V024_POSTCONDITION_QUERIES", "V024_STATEMENTS"]
