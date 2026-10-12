"""Additive inferred-grid PDF storage, not a worker or publication activation.

Instantiate only the frozen v29 state/attempt/retention protocol in its own
namespace. PDF provenance is legacy v1/pypdf plus a freshly rebound geometry
plan, never OOXML native tables. Old numbered schemas remain unchanged.
"""
import re

from .v014_wiki_authoring import _sha
from .v029_native_authoring_storage import (
    V029_STATEMENTS, V029_POSTCONDITION_QUERIES, METADATA_TYPES as BASE_TYPES,
    _META_TYPES, _META_KEYS, FAILURE_CODES,
)
from .v032_native_display_storage import _once, _exact_object, _receipt_shape_valid, _batch_shape_valid

POLICY = "wiki-pdf-inferred-grid-writing-v1"
SOURCE_POLICY = "canonical-pdf-authoring-source-v1"
GEOMETRY_VERSION = "pdf-inferred-grid-v1"
ENGINE_VERSION = "0.11.9"
ASSEMBLY = "wiki-pdf-inferred-grid-assembly-v1"
TABLE_POLICY = "inferred_grid_exact_cells_no_header_inference_v1"
PROMPT_SHA256 = "94f9513e2e2b98bb7410903cb60daa1da3ad5a84336c5f4283b4c77ee1ef6048"
METADATA_TYPES = {
    **{("geometry_version" if k == "source_slot_version" else k): v for k, v in BASE_TYPES.items()},
    "engine_version": "text", "geometry_sha256": "text", "geometry_size_bytes": "integer",
}
_OTHER_RUNS = ("publication_authoring_run", "publication_preservation_run",
    "native_authoring_run", "pptx_role_authoring_run")
_OTHER_WORKFLOWS = ("publication_wiki_request", "publication_preservation_request",
    "native_workflow_request", "pptx_role_workflow_request")


def _namespace(sql):
    names = r"\b(?:native_authoring_[a-z_]+|native_wiki_snapshot[a-z_]*|native_(?:run|state|batch|attempt|outcome|snapshot)_[a-z_]+|native_pending_purge_guard)\b"
    sql = re.sub(names, lambda m: "pdf_" + m[0][len("native_"):], sql)
    return sql.replace("idx_native_validated_batch", "idx_pdf_validated_batch").replace("NATIVE_", "PDF_")


def _metadata_valid(value):
    fixed = {"policy_version": POLICY, "source_preparation_version": SOURCE_POLICY,
        "geometry_version": GEOMETRY_VERSION, "engine_version": ENGINE_VERSION,
        "assembly_version": ASSEMBLY, "table_rendering_version": TABLE_POLICY,
        "prompt_sha256": PROMPT_SHA256}
    return " AND ".join([_exact_object(value, METADATA_TYPES),
        *(f"json_extract({value},'$.{k}')='{v}'" for k, v in fixed.items()),
        _sha(f"json_extract({value},'$.geometry_sha256')"),
        f"json_extract({value},'$.geometry_size_bytes') BETWEEN 1 AND 33554432"])


def _configuration_valid(value):
    fields = {"version": "text", "policy_version": "text", "prompt_sha256": "text", "chunk_bytes": "integer",
        "model_policy": "object", "work_policy": "object", "execution_policy": "object",
        "geometry_limits": "object", "process_policy": "object"}
    return f"""{_exact_object(value, fields)}
        AND json_extract({value},'$.version')='{SOURCE_POLICY}'
        AND json_extract({value},'$.policy_version')='{POLICY}'
        AND json_extract({value},'$.prompt_sha256')='{PROMPT_SHA256}'
        AND json_extract({value},'$.chunk_bytes') BETWEEN 4 AND 8192"""


def _guard(name):
    statement = next(s for s in V029_STATEMENTS if s.startswith("CREATE TRIGGER " + name + " "))
    return statement.split("WHEN ", 1)[1].split("BEGIN ", 1)[0].strip()


_run = _guard("native_run_insert")
for before, after in (
    ("        JOIN extraction_native_table_structure d ON d.snapshot_id=e.id\n", ""),
    ("        JOIN extraction_native_table_structure_body n ON n.snapshot_id=d.snapshot_id\n", ""),
    ("          AND d.structure_sha256=n.structure_sha256 AND d.extracted_markdown_sha256=e.extracted_sha256\n", ""),
    ("deterministic-ingest-native-tables-v1", "p6.1-deterministic-ingest-v1"),
    ("e.extractor_version='rich-source-v4-native-tables'", "e.extractor_name='canonical-pypdf' AND e.extractor_version='rich-source-v3-bounded-tables'"),
    ("j.handler_version='v2'", "j.handler_version='v1'"),
    ("wiki-native-batch-authoring-v1", POLICY),
    ("canonical-native-authoring-source-v1", SOURCE_POLICY),
    ("wiki-native-slot-authoring-v1", GEOMETRY_VERSION),
    ("wiki-native-slot-assembly-v1", ASSEMBLY),
    ("native-source-table-html-v1", TABLE_POLICY),
    (_META_TYPES, " AND ".join(f"json_type(NEW.metadata_json,'$.{k}')='{v}'" for k, v in METADATA_TYPES.items())),
    (_META_KEYS, ",".join("'" + k + "'" for k in METADATA_TYPES)),
):
    _run = _once(_run, before, after)
_run = _once(_run, f"json_each(NEW.metadata_json))<>{len(BASE_TYPES)}",
    f"json_each(NEW.metadata_json))<>{len(METADATA_TYPES)}", count=2)
_run = _once(_run, "$.source_slot_version", "$.geometry_version")
_run = _namespace(_run)
_run += f"""
    OR NOT coalesce(({_metadata_valid('NEW.metadata_json')}),0)
    OR NOT EXISTS(SELECT 1 FROM source_artifact a JOIN artifact_blob raw ON raw.artifact_id=a.id
        JOIN artifact_blob_body retained ON retained.artifact_blob_id=raw.id
        JOIN blob_object object ON object.sha256=retained.content_sha256
        WHERE a.id=NEW.artifact_id AND a.origin_type='upload' AND a.publisher_user_id=NEW.actor_user_id
          AND lower(substr(a.original_filename,-4))='.pdf'
          AND raw.role='raw' AND raw.ordinal=0 AND raw.body_retention_status='present'
          AND raw.content_sha256=retained.content_sha256 AND raw.content_sha256=a.raw_sha256
          AND raw.size_bytes=a.raw_size_bytes AND object.size_bytes=raw.size_bytes
          AND object.verification_status='verified'
          AND (SELECT count(*) FROM artifact_blob WHERE artifact_id=a.id)=1)"""
_run += "".join(f"\n    OR EXISTS(SELECT 1 FROM {table} other WHERE other.preview_id=NEW.preview_id)"
    for table in (*_OTHER_RUNS, *_OTHER_WORKFLOWS))

_snapshot = _namespace(_guard("native_snapshot_body_insert"))
_snapshot = _once(_snapshot,
    "          AND json_type(NEW.manifest_json,'$.authoring_receipt_count')='integer'\n"
    "          AND json_extract(NEW.manifest_json,'$.authoring_receipt_count')=r.batch_count\n", "")
_snapshot = _snapshot.replace("$.batch_lineage.receipts", "$.receipts").replace(
    "$.batch_lineage.batches", "$.batches").replace("$.batch_lineage.prompt_sha256", "$.prompt_sha256")
_snapshot = _once(_snapshot, "wiki-native-batch-authoring-v1", POLICY)
_snapshot += f"""
    OR NOT EXISTS(SELECT 1 FROM pdf_wiki_snapshot s JOIN pdf_authoring_run r ON r.id=s.run_id
        JOIN source_artifact a ON a.id=r.artifact_id WHERE s.id=NEW.snapshot_id
          AND json_extract(NEW.manifest_json,'$.assembly_version')='{ASSEMBLY}'
          AND json_extract(NEW.manifest_json,'$.structure_origin')='inferred_line_grid'
          AND json_type(NEW.manifest_json,'$.all_nonblank_ranges_authored')='true'
          AND json_type(NEW.manifest_json,'$.all_detected_grid_cells_rendered')='true'
          AND json_type(NEW.manifest_json,'$.semantic_review_performed')='false'
          AND json_type(NEW.manifest_json,'$.semantic_completeness_proven')='false'
          AND json_type(NEW.manifest_json,'$.caption_ownership_inferred')='false'
          AND json_type(NEW.manifest_json,'$.images_interpreted')='false'
          AND json_extract(NEW.manifest_json,'$.output_range_unit')='utf8_bytes'
          AND json_type(NEW.manifest_json,'$.chunks')='array'
          AND json_type(NEW.manifest_json,'$.tables')='array'
          AND json_extract(NEW.manifest_json,'$.source.raw_sha256')=a.raw_sha256
          AND json_type(NEW.manifest_json,'$.source.raw_bytes')='integer'
          AND json_extract(NEW.manifest_json,'$.source.raw_bytes')=a.raw_size_bytes
          AND json_extract(NEW.manifest_json,'$.source.filename')=a.original_filename
          AND json_extract(NEW.manifest_json,'$.source.geometry_version')='{GEOMETRY_VERSION}'
          AND json_extract(NEW.manifest_json,'$.source.engine_version')='{ENGINE_VERSION}'
          AND json_extract(NEW.manifest_json,'$.source.geometry_sha256')=json_extract(r.metadata_json,'$.geometry_sha256')
          AND json_type(NEW.manifest_json,'$.source.geometry_bytes')='integer'
          AND json_extract(NEW.manifest_json,'$.source.geometry_bytes')=json_extract(r.metadata_json,'$.geometry_size_bytes')
          AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.receipts') rc
              WHERE NOT EXISTS(SELECT 1 FROM pdf_authoring_batch b
                  JOIN pdf_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
                  JOIN pdf_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
                  WHERE b.run_id=r.id AND b.ordinal=rc.key+1
                    AND json_extract(rc.value,'$.batch_id')=b.batch_id
                    AND json_extract(rc.value,'$.request_sha256')=b.request_sha256
                    AND json_extract(rc.value,'$.policy_version')='{POLICY}'
                    AND json_extract(rc.value,'$.validation_state')='validated'
                    AND json_extract(rc.value,'$.response_sha256')=o.response_sha256
                    AND json_extract(rc.value,'$.response')=ob.response
                    AND json_extract(rc.value,'$.transport_json')=ob.transport_json
                    AND ({_receipt_shape_valid()})))
          AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.batches') entry
              WHERE NOT EXISTS(SELECT 1 FROM pdf_authoring_batch b
                  WHERE b.run_id=r.id AND b.ordinal=entry.key+1
                    AND json_type(entry.value,'$.ordinal')='integer'
                    AND json_extract(entry.value,'$.ordinal')=b.ordinal
                    AND json_extract(entry.value,'$.batch_id')=b.batch_id
                    AND json(json_extract(entry.value,'$.chunk_ordinals'))=json(b.ordinals_json)
                    AND NOT EXISTS(SELECT 1 FROM json_each(b.ordinals_json) position
                        JOIN json_each(b.members_json) member ON member.key=position.key
                        WHERE NOT coalesce((
                            json_type(NEW.manifest_json,'$.chunks[' || (position.value-1) || '].ordinal')='integer'
                            AND json_extract(NEW.manifest_json,'$.chunks[' || (position.value-1) || '].ordinal')=position.value
                            AND json_extract(NEW.manifest_json,'$.chunks[' || (position.value-1) || '].chunk_id')=member.value
                        ),0))
                    AND ({_batch_shape_valid()}))))"""

_statements = []
for original in V029_STATEMENTS:
    statement = _namespace(original)
    if original.startswith("CREATE TRIGGER native_run_insert "):
        statement = f"""CREATE TRIGGER pdf_run_insert BEFORE INSERT ON pdf_authoring_run
            WHEN {_run} BEGIN SELECT RAISE(ABORT,'PDF_RUN_SOURCE_INVALID'); END"""
    elif original.startswith("CREATE TRIGGER native_snapshot_body_insert "):
        statement = f"""CREATE TRIGGER pdf_snapshot_body_insert BEFORE INSERT ON pdf_wiki_snapshot_body
            WHEN {_snapshot} BEGIN SELECT RAISE(ABORT,'PDF_SNAPSHOT_BODY_INVALID'); END"""
    elif original.startswith("CREATE TABLE native_authoring_batch("):
        statement = _once(statement, "length(batch_id)=66 AND substr(batch_id,1,2)='B_'",
            "length(batch_id)=67 AND substr(batch_id,1,3)='PB_'")
        statement = _once(statement, "substr(batch_id,3)", "substr(batch_id,4)")
        statement = _once(statement, "json_array_length(members_json) BETWEEN 1 AND 4",
            "json_array_length(members_json) BETWEEN 1 AND 16")
        statement = _once(statement, "        request_sha256 TEXT", """        ordinals_json TEXT NOT NULL CHECK(json_valid(ordinals_json)
            AND json_type(ordinals_json)='array'
            AND json_array_length(ordinals_json)=json_array_length(members_json)),
        request_sha256 TEXT""")
    elif original.startswith("CREATE TRIGGER native_batch_insert "):
        statement = _once(statement, "length(value)<>66 OR substr(value,1,2)<>'N_' OR substr(value,3)",
            "length(value)<>67 OR substr(value,1,3)<>'PC_' OR substr(value,4)")
        statement = _once(statement, "    BEGIN SELECT RAISE", """      OR EXISTS(SELECT 1 FROM json_each(NEW.ordinals_json) position
          WHERE position.type<>'integer' OR position.value NOT BETWEEN 1 AND 4096
            OR (position.key>0 AND position.value<=json_extract(NEW.ordinals_json,
                '$[' || (position.key-1) || ']')))
      OR EXISTS(SELECT 1 FROM pdf_authoring_batch b,
          json_each(b.ordinals_json) prior,json_each(NEW.ordinals_json) current
          WHERE b.run_id=NEW.run_id AND prior.value>=current.value)
    BEGIN SELECT RAISE""")
    _statements.append(statement)

V039_STATEMENTS = (
    "CREATE VIEW pdf_registered_run AS SELECT CAST(NULL AS TEXT) AS run_id WHERE 0",
    """CREATE VIEW pdf_registered_publication AS SELECT CAST(NULL AS TEXT) AS snapshot_id,
        CAST(NULL AS TEXT) AS publication_operation_id WHERE 0""",
    *_statements,
    f"""CREATE TRIGGER pdf_configuration_insert BEFORE INSERT ON pdf_authoring_run_body
        WHEN NOT coalesce(({_configuration_valid('NEW.configuration_json')}),0)
        BEGIN SELECT RAISE(ABORT,'PDF_CONFIGURATION_INVALID'); END""",
    *(f"""CREATE TRIGGER pdf_excludes_{table} BEFORE INSERT ON {table}
        WHEN EXISTS(SELECT 1 FROM pdf_authoring_run r WHERE r.preview_id=NEW.preview_id)
        BEGIN SELECT RAISE(ABORT,'PDF_PREVIEW_ALREADY_OWNED'); END"""
        for table in (*_OTHER_RUNS, *_OTHER_WORKFLOWS)),
)
V039_POSTCONDITION_QUERIES = (
    *(_namespace(q) for q in V029_POSTCONDITION_QUERIES),
    f"""SELECT 'PDF metadata invalid' FROM pdf_authoring_run r
        WHERE NOT coalesce(({_metadata_valid('r.metadata_json')}),0) LIMIT 1""",
    f"""SELECT 'PDF configuration invalid' FROM pdf_authoring_run_body b
        WHERE NOT coalesce(({_configuration_valid('b.configuration_json')}),0) LIMIT 1""",
    *(f"""SELECT 'PDF preview shared with {table}' FROM pdf_authoring_run r
        WHERE EXISTS(SELECT 1 FROM {table} other WHERE other.preview_id=r.preview_id) LIMIT 1"""
        for table in (*_OTHER_RUNS, *_OTHER_WORKFLOWS)),
    """SELECT 'PDF preview consumed without registered publication' FROM pdf_authoring_run r
        JOIN publication_preview p ON p.id=r.preview_id WHERE p.status='consumed'
          AND NOT EXISTS(SELECT 1 FROM pdf_wiki_snapshot s
            JOIN pdf_registered_publication registered ON registered.snapshot_id=s.id
            WHERE s.run_id=r.id) LIMIT 1""",
    f"""SELECT 'PDF snapshot identity invalid' FROM pdf_wiki_snapshot s
        JOIN pdf_wiki_snapshot_body retained ON retained.snapshot_id=s.id
        WHERE ({_snapshot.replace('NEW.', 'retained.')}) LIMIT 1""",
)
