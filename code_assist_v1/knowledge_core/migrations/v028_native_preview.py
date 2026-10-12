"""Opt-in v2 extraction jobs/previews; historical v1 behavior is retained.

Use the latest retained trigger definitions (including v11 higher authority),
not the original v1 versions. Authoring requests still reject a v2 parent until
their own successor is implemented. No startup imports this catalog.
"""

from .native_table_structure_catalog import NATIVE_TABLE_STRUCTURE_MIGRATION_CATALOG


RULE = "deterministic-ingest-native-tables-v1"


def _current_trigger(name):
    found = None
    for migration in NATIVE_TABLE_STRUCTURE_MIGRATION_CATALOG.migrations:
        for statement in migration.statements:
            if statement.startswith("CREATE TRIGGER " + name + "\n"):
                found = statement
    if found is None:
        raise RuntimeError("Retained preview trigger is missing: " + name)
    return found


def _once(statement, before, after):
    if statement.count(before) != 1:
        raise RuntimeError("Retained preview trigger contract changed")
    return statement.replace(before, after)


def _valid_v2(prefix):
    x = prefix
    return f"""json_extract({x}.input_ref_json,'$.rule_version')='{RULE}'
    AND (SELECT count(*) FROM json_each({x}.expected_state_json))=9
    AND NOT EXISTS (SELECT 1 FROM json_each({x}.expected_state_json)
        WHERE key NOT IN ('_authorization_fence','artifact_id','preview_id','media_type',
            'raw_sha256','raw_size_bytes','schema_version','title','handler_version'))
    AND json_type({x}.expected_state_json,'$.schema_version')='integer'
    AND json_extract({x}.expected_state_json,'$.schema_version')=2
    AND json_type({x}.expected_state_json,'$.handler_version')='text'
    AND json_extract({x}.expected_state_json,'$.handler_version')='v2'
    AND json_type({x}.expected_state_json,'$._authorization_fence') IN ('null','object')
    AND json_type({x}.expected_state_json,'$.artifact_id')='text'
    AND json_extract({x}.expected_state_json,'$.artifact_id')=json_extract({x}.input_ref_json,'$.artifact_id')
    AND json_type({x}.expected_state_json,'$.preview_id')='text'
    AND json_extract({x}.expected_state_json,'$.preview_id')=json_extract({x}.input_ref_json,'$.preview_id')
    AND json_type({x}.expected_state_json,'$.raw_sha256')='text'
    AND length(json_extract({x}.expected_state_json,'$.raw_sha256'))=64
    AND json_extract({x}.expected_state_json,'$.raw_sha256') NOT GLOB '*[^0-9a-f]*'
    AND json_type({x}.expected_state_json,'$.raw_size_bytes')='integer'
    AND json_extract({x}.expected_state_json,'$.raw_size_bytes')>=0
    AND json_type({x}.expected_state_json,'$.media_type')='text'
    AND length(trim(json_extract({x}.expected_state_json,'$.media_type'))) BETWEEN 1 AND 255
    AND json_type({x}.expected_state_json,'$.title')='text'
    AND length(trim(json_extract({x}.expected_state_json,'$.title'))) BETWEEN 1 AND 500"""


_statements = []
for _name in ("trg_publication_preview_validity_insert", "trg_publication_preview_validity_update"):
    _native_ready = ""
    if _name.endswith("_update"):
        _native_ready = """ AND EXISTS (
            SELECT 1 FROM source_artifact sa WHERE sa.id=x.artifact_id
            AND ((lower(substr(sa.original_filename,-5)) IN ('.docx','.pptx')
                AND x.extractor_version='rich-source-v4-native-tables'
                AND EXISTS (SELECT 1 FROM extraction_native_table_structure nd
                    JOIN extraction_native_table_structure_body nb
                    ON nb.snapshot_id=nd.snapshot_id AND nb.artifact_id=nd.artifact_id
                    WHERE nd.snapshot_id=x.id AND nd.artifact_id=x.artifact_id
                    AND nd.extracted_markdown_sha256=x.extracted_sha256))
              OR (coalesce(lower(substr(sa.original_filename,-5)),'') NOT IN ('.docx','.pptx')
                AND x.extractor_version<>'rich-source-v4-native-tables'))
        )"""
    _sql = _once(_current_trigger(_name), "j.handler_version='v1'",
        "(j.handler_version='v1' OR (j.handler_version='v2' AND coalesce(("
        + _valid_v2("j") + "),0)=1" + _native_ready + "))")
    _statements.extend(("DROP TRIGGER " + _name, _sql))

_name = "trg_durable_job_state_insert"
_sql = _once(_current_trigger(_name),
    "OR (NEW.job_type='publication_preview.extract' AND (\n  NEW.handler_version<>'v1'",
    "OR (NEW.job_type='publication_preview.extract' AND (\n  NEW.handler_version NOT IN ('v1','v2')"
    + " OR (NEW.handler_version='v2' AND coalesce((" + _valid_v2("NEW") + "),0)<>1)")
_statements.extend(("DROP TRIGGER " + _name, _sql))

V028_STATEMENTS = tuple(_statements)
V028_POSTCONDITION_QUERIES = (
    "SELECT 'invalid native preview input' FROM durable_job j "
    "WHERE j.job_type='publication_preview.extract' AND j.handler_version='v2' "
    "AND coalesce((" + _valid_v2("j") + "),0)<>1 LIMIT 1",
)
