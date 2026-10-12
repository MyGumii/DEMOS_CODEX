"""Forward-only source-structure authoring policy; retained v21 rows unchanged."""
from .v021_authoring_only import V021_STATEMENTS

_PRIOR = "r.policy_version='wiki-information-authoring-v1'"
_SUCCESSOR = "wiki-information-authoring-v2-structure"
_AUTHORING = "r.policy_version IN ('wiki-information-authoring-v1','" + _SUCCESSOR + "')"
if sum(statement.count(_PRIOR) for statement in V021_STATEMENTS) != 4:
    raise RuntimeError("The frozen v21 authoring gates changed.")


def _successor(statement):
    result = statement.replace(_PRIOR, _AUTHORING)
    if result.startswith("CREATE TRIGGER preserved_wiki_snapshot_body_binding"):
        anchor = "AND json_extract(NEW.manifest_json,'$.policy_version')=r.policy_version"
        if result.count(anchor) != 1:
            raise RuntimeError("The frozen v21 manifest gate changed.")
        result = result.replace(anchor, anchor + f"""
        AND (r.policy_version<>'{_SUCCESSOR}' OR (
          json_extract(NEW.manifest_json,'$.source_structure_version')='wiki-source-structure-v1'
          AND json_extract(NEW.manifest_json,'$.source_snapshot_id')=r.extraction_snapshot_id
          AND json_extract(NEW.manifest_json,'$.source_sha256')=r.source_sha256
          AND json_extract(NEW.manifest_json,'$.source_bytes')=r.source_size_bytes
          AND json_type(NEW.manifest_json,'$.source_outline')='array'
          AND json_extract(NEW.manifest_json,'$.structure_semantics')='exact_extracted_markdown_locations_not_fact_entailment'
          AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.source_outline') h
            WHERE json_type(h.value) IS NOT 'object'
              OR json_type(h.value,'$.heading_id') IS NOT 'text'
              OR length(json_extract(h.value,'$.heading_id')) IS NOT 26
              OR json_type(h.value,'$.label') IS NOT 'text'
              OR json_type(h.value,'$.start') IS NOT 'integer'
              OR json_type(h.value,'$.label_end') IS NOT 'integer'
              OR json_type(h.value,'$.scope_end') IS NOT 'integer'
              OR json_type(h.value,'$.level') IS NOT 'integer'
              OR json_extract(h.value,'$.level') NOT BETWEEN 1 AND 6
              OR NOT (0<=json_extract(h.value,'$.start')
                AND json_extract(h.value,'$.start')<json_extract(h.value,'$.label_end')
                AND json_extract(h.value,'$.label_end')<=json_extract(h.value,'$.scope_end')
                AND json_extract(h.value,'$.scope_end')<=length(x.content_md))
              OR substr(x.content_md,json_extract(h.value,'$.start')+1,
                json_extract(h.value,'$.label_end')-json_extract(h.value,'$.start'))
                    IS NOT json_extract(h.value,'$.label')
              OR (SELECT count(*) FROM json_each(NEW.manifest_json,'$.source_outline') other
                WHERE json_extract(other.value,'$.heading_id')=json_extract(h.value,'$.heading_id'))<>1
              OR (json_type(h.value,'$.parent_id') IS NOT 'null' AND NOT EXISTS(
                SELECT 1 FROM json_each(NEW.manifest_json,'$.source_outline') parent
                WHERE json_extract(parent.value,'$.heading_id')=json_extract(h.value,'$.parent_id')
                  AND json_extract(parent.value,'$.start')<json_extract(h.value,'$.start')
                  AND json_extract(parent.value,'$.scope_end')>=json_extract(h.value,'$.scope_end')
                  AND json_extract(parent.value,'$.level')<json_extract(h.value,'$.level'))))
          AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.chunks') c
            WHERE json_type(c.value,'$.source_locations') IS NOT 'array'
              OR json_type(c.value,'$.source_range') IS NOT 'array'
              OR json_array_length(c.value,'$.source_range') IS NOT 2
              OR NOT EXISTS(SELECT 1 FROM publication_preservation_chunk stored
                JOIN publication_preservation_receipt receipt ON receipt.run_id=stored.run_id
                  AND receipt.ordinal=stored.ordinal AND receipt.sequence=1
                JOIN publication_preservation_receipt_body rb ON rb.run_id=receipt.run_id
                  AND rb.ordinal=receipt.ordinal AND rb.sequence=receipt.sequence
                WHERE stored.run_id=r.id AND stored.ordinal=c.key+1
                  AND stored.chunk_id=json_extract(c.value,'$.chunk_id')
                  AND stored.source_start=json_extract(c.value,'$.source_range[0]')
                  AND stored.source_end=json_extract(c.value,'$.source_range[1]')
                  AND receipt.request_sha256=json_extract(c.value,'$.receipts[0].request_sha256')
                  AND receipt.response_sha256=json_extract(c.value,'$.receipts[0].response_sha256')
                  AND rb.response=json_extract(c.value,'$.receipts[0].response'))
              OR json_array_length(c.value,'$.source_locations') IS NOT json_array_length(c.value,'$.facts')
              OR EXISTS(SELECT 1 FROM json_each(c.value,'$.source_locations') l
                WHERE json_type(l.value) IS NOT 'object'
                  OR json_type(l.value,'$.fact_id') IS NOT 'text'
                  OR json_extract(l.value,'$.fact_id') IS NOT json_extract(c.value,'$.facts['||l.key||'].fact_id')
                  OR json_type(l.value,'$.heading_paths') IS NOT 'array'
                  OR EXISTS(SELECT 1 FROM json_each(l.value,'$.heading_paths') path
                    WHERE json_type(path.value) IS NOT 'array' OR json_array_length(path.value)=0
                      OR EXISTS(SELECT 1 FROM json_each(path.value) id WHERE id.type<>'text'
                        OR NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.source_outline') h
                          WHERE json_extract(h.value,'$.heading_id')=id.value)))
                  OR json_type(l.value,'$.quotes') IS NOT 'array'
                  OR json_array_length(l.value,'$.quotes') IS NOT json_array_length(c.value,'$.facts['||l.key||'].quotes')
                  OR json_array_length(l.value,'$.quotes')=0
                  OR EXISTS(SELECT 1 FROM json_each(l.value,'$.quotes') quote
                    WHERE json_type(quote.value) IS NOT 'object'
                      OR json_type(quote.value,'$.quote_sha256') IS NOT 'text'
                      OR length(json_extract(quote.value,'$.quote_sha256')) IS NOT 64
                      OR json_type(quote.value,'$.heading_paths') IS NOT 'array'
                      OR json_type(quote.value,'$.source_ranges') IS NOT 'array'
                      OR json_array_length(quote.value,'$.source_ranges')=0
                      OR EXISTS(SELECT 1 FROM json_each(quote.value,'$.source_ranges') sr
                        WHERE json_type(sr.value) IS NOT 'array' OR json_array_length(sr.value) IS NOT 2
                          OR json_type(sr.value,'$[0]') IS NOT 'integer' OR json_type(sr.value,'$[1]') IS NOT 'integer'
                          OR NOT (json_extract(c.value,'$.source_range[0]')<=json_extract(sr.value,'$[0]')
                            AND json_extract(sr.value,'$[0]')<json_extract(sr.value,'$[1]')
                            AND json_extract(sr.value,'$[1]')<=json_extract(c.value,'$.source_range[1]'))
                          OR NOT EXISTS(SELECT 1 FROM json_each(c.value,'$.facts['||l.key||'].source_ranges') fr
                            WHERE fr.value=sr.value)))))))""")
    return result


V022_STATEMENTS = tuple(_successor(statement) for statement in V021_STATEMENTS)
V022_POSTCONDITION_QUERIES = (
    """SELECT 'structured authoring successor gates missing' WHERE 4<>(SELECT count(*)
      FROM sqlite_schema WHERE type='trigger' AND instr(sql,'wiki-information-authoring-v2-structure')>0)""",
)
