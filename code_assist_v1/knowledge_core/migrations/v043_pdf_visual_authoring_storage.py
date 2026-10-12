"""Opt-in visual call ledger, instantiated from the frozen v29 protocol.

The archive is mandatory, not a caller-supplied image or geometry manifest.
This migration grants no model-call permission and registers no publication.
Provider execution, structural validation and audit belong to the future service.
"""
import re

from .v029_native_authoring_storage import V029_STATEMENTS, V029_POSTCONDITION_QUERIES, METADATA_TYPES, FAILURE_CODES
from .v032_native_display_storage import _once, _old_guard, _exact_object, _receipt_shape_valid

POLICY = "wiki-pdf-page-visual-writing-v1"
SOURCE_POLICY = "canonical-pdf-visual-authoring-source-v1"
SLOT_POLICY = "pdf-original-page-visual-source-v1"
TABLE_POLICY = "inferred_grid_exact_cells_no_header_inference_v1"
PROMPT_SHA256 = "d83f5b7cdea3f61b5e3f23545808cbad8a980856e8c85ffee8e860897df82d86"
OTHER_OWNERS = ("publication_authoring_run", "publication_preservation_run", "native_authoring_run",
    "pptx_role_authoring_run", "pdf_authoring_run", "publication_wiki_request",
    "publication_preservation_request", "native_workflow_request", "pptx_role_workflow_request", "pdf_workflow_request")


def _binding(body):
    # Classification of two captured records, NOT provider receipt/semantic proof.
    # An absent/empty transport attempt leaves any local-only evidence unbound.
    return f"""CASE WHEN json_type({body}.transport_json,'$.image_input_attempt') IS NULL THEN 'absent'
        WHEN json_type({body}.transport_json,'$.image_input_attempt')='object' THEN
          CASE WHEN json_extract({body}.transport_json,'$.image_input_attempt')='{{}}' THEN 'absent'
            WHEN json(json_extract({body}.transport_json,'$.image_input_attempt'))=json({body}.attempt_json) THEN 'matched'
            ELSE 'conflicting' END
        ELSE 'conflicting' END"""


def _namespace(sql):
    names = r"\b(?:native_authoring_[a-z_]+|native_wiki_snapshot[a-z_]*|native_(?:run|state|batch|attempt|outcome|snapshot)_[a-z_]+|native_pending_purge_guard)\b"
    return re.sub(names, lambda m: "pdf_visual_" + m[0][7:], sql).replace(
        "idx_native_validated_batch", "idx_pdf_visual_validated_batch").replace("NATIVE_", "PDF_VISUAL_")


_run = _namespace(_old_guard("native_run_insert"))
for before, after in (
    ("        JOIN extraction_native_table_structure d ON d.snapshot_id=e.id\n", ""),
    ("        JOIN extraction_native_table_structure_body n ON n.snapshot_id=d.snapshot_id\n", ""),
    ("          AND d.structure_sha256=n.structure_sha256 AND d.extracted_markdown_sha256=e.extracted_sha256\n", ""),
    ("deterministic-ingest-native-tables-v1", "p6.1-deterministic-ingest-v1"),
    ("e.extractor_version='rich-source-v4-native-tables'", "e.extractor_name='canonical-pypdf' AND e.extractor_version='rich-source-v3-bounded-tables'"),
    ("j.handler_version='v2'", "j.handler_version='v1'"),
    ("wiki-native-batch-authoring-v1", POLICY),
    ("canonical-native-authoring-source-v1", SOURCE_POLICY),
    ("wiki-native-slot-authoring-v1", SLOT_POLICY),
    ("wiki-native-slot-assembly-v1", POLICY),
    ("native-source-table-html-v1", TABLE_POLICY),
):
    _run = _once(_run, before, after)
_run += f"""
    OR NEW.attempt_units<>1 OR NEW.batch_count<1
    OR json_extract(NEW.metadata_json,'$.prompt_sha256')<>'{PROMPT_SHA256}'
    OR NOT EXISTS(SELECT 1 FROM pdf_visual_source a
        JOIN pdf_visual_source_ready ready ON ready.source_id=a.id
        JOIN pdf_visual_source_body body ON body.source_id=a.id
        WHERE a.id=NEW.source_archive_id AND a.preview_id=NEW.preview_id
          AND a.actor_user_id=NEW.actor_user_id AND a.artifact_id=NEW.artifact_id
          AND a.extraction_snapshot_id=NEW.extraction_snapshot_id AND a.parent_job_id=NEW.parent_job_id
          AND a.source_fence_sha256=NEW.source_fence_sha256 AND a.plan_sha256=NEW.source_plan_sha256
          AND a.created_at<=NEW.created_at
          AND json_array_length(body.bundle_json,'$.requests')=NEW.batch_count)
""" + "".join(f" OR EXISTS(SELECT 1 FROM {table} WHERE preview_id=NEW.preview_id)" for table in OTHER_OWNERS)

# The complete manifest is authenticated/replayed in the preparation layer.
# SQL independently pins its public identities, receipts, page ledger and bounds.
_manifest_fields = {**dict.fromkeys(("version", "plan_sha256", "prompt_sha256", "reservation_json",
    "output_range_unit", "wiki_sha256"), "text"), "schema_version":"integer", "wiki_bytes":"integer",
    "source":"object", **dict.fromkeys(("chunks", "tables", "visuals", "receipts"), "array"),
    **dict.fromkeys(("all_nonblank_ranges_authored", "all_detected_grid_cells_rendered", "all_pages_rendered",
        "all_visual_tasks_returned"), "true"),
    **dict.fromkeys(("semantic_review_performed", "semantic_completeness_proven", "visual_accuracy_verified",
        "caption_ownership_inferred", "canonical_publication_ready"), "false")}
_snapshot = f"""NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id
    JOIN extraction_snapshot_body extracted ON extracted.snapshot_id=r.extraction_snapshot_id
    JOIN pdf_visual_source a ON a.id=r.source_archive_id
    JOIN pdf_visual_source_body ab ON ab.source_id=a.id
    WHERE s.id=NEW.snapshot_id
      AND s.content_size_bytes=length(CAST(NEW.content_md AS BLOB))
      AND s.manifest_size_bytes=length(CAST(NEW.manifest_json AS BLOB))
      AND coalesce(({_exact_object('NEW.manifest_json', _manifest_fields)}),0)
      AND json_extract(NEW.manifest_json,'$.schema_version')=1
      AND json_extract(NEW.manifest_json,'$.version')='{POLICY}'
      AND json_extract(NEW.manifest_json,'$.prompt_sha256')='{PROMPT_SHA256}'
      AND json_extract(NEW.manifest_json,'$.wiki_sha256')=s.content_sha256
      AND json_extract(NEW.manifest_json,'$.wiki_bytes')=s.content_size_bytes
      AND json_extract(NEW.manifest_json,'$.plan_sha256')=r.source_plan_sha256
      AND json_extract(NEW.manifest_json,'$.output_range_unit')='utf8_bytes'
      AND json(json_extract(NEW.manifest_json,'$.source'))=json(json_extract(ab.bundle_json,'$.source'))
      AND json_array_length(NEW.manifest_json,'$.receipts')=r.batch_count
      AND json_array_length(NEW.manifest_json,'$.visuals')=a.page_count
      AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.receipts') rc
          WHERE NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b
              JOIN pdf_visual_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
              JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
              WHERE b.run_id=r.id AND b.ordinal=rc.key+1
                AND coalesce(({_receipt_shape_valid()}),0)
                AND json_extract(rc.value,'$.batch_id')=b.batch_id
                AND json_extract(rc.value,'$.request_sha256')=b.request_sha256
                AND json_extract(rc.value,'$.response_sha256')=o.response_sha256
                AND json_extract(rc.value,'$.response')=ob.response
                AND json_extract(rc.value,'$.transport_json')=ob.transport_json
                AND json_extract(rc.value,'$.validation_state')='validated'
                AND json_extract(rc.value,'$.policy_version')='{POLICY}'))
      AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.visuals') v
          WHERE NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b
              JOIN pdf_visual_page p ON p.source_id=a.id AND p.page=b.page
              WHERE b.run_id=r.id AND b.page=v.key+1 AND b.visual=1
                AND json_extract(v.value,'$.source_id')=p.image_id
                AND json_extract(v.value,'$.batch_id')=b.batch_id
                AND json(json_extract(v.value,'$.image'))=json(json_extract(ab.bundle_json,'$.source.pages['||v.key||']'))))
      AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b,
          json_each(b.ordinals_json) n JOIN json_each(b.members_json) member ON member.key=n.key
          WHERE b.run_id=r.id AND NOT coalesce((
              json_type(NEW.manifest_json,'$.chunks['||(n.value-1)||'].ordinal')='integer'
              AND json_extract(NEW.manifest_json,'$.chunks['||(n.value-1)||'].ordinal')=n.value
              AND json_extract(NEW.manifest_json,'$.chunks['||(n.value-1)||'].chunk_id')=member.value
              AND json_extract(NEW.manifest_json,'$.chunks['||(n.value-1)||'].batch_id')=b.batch_id),0)))"""

_statements = []
for original in V029_STATEMENTS:
    sql = _namespace(original)
    if original.startswith("CREATE TABLE native_authoring_run("):
        sql = _once(sql, "        preview_id TEXT", "        source_archive_id TEXT NOT NULL UNIQUE REFERENCES pdf_visual_source(id) ON DELETE RESTRICT,\n        preview_id TEXT")
    elif original.startswith("CREATE TRIGGER native_run_insert "):
        sql = f"CREATE TRIGGER pdf_visual_run_insert BEFORE INSERT ON pdf_visual_authoring_run WHEN {_run} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_RUN_SOURCE_INVALID'); END"
    elif original.startswith("CREATE TRIGGER native_run_body_insert "):
        sql = _once(sql, "          AND json_extract", "          AND NEW.configuration_json=(SELECT json_extract(b.bundle_json,'$.configuration_json') FROM pdf_visual_source_body b WHERE b.source_id=r.source_archive_id)\n          AND json_extract")
    elif original.startswith("CREATE TABLE native_authoring_batch("):
        sql = _once(sql, "length(batch_id)=66 AND substr(batch_id,1,2)='B_'", "length(batch_id)=68 AND substr(batch_id,1,4)='PVB_'")
        sql = _once(sql, "substr(batch_id,3)", "substr(batch_id,5)")
        sql = _once(sql, "json_array_length(members_json) BETWEEN 1 AND 4", "json_array_length(members_json) BETWEEN 0 AND 16")
        sql = _once(sql, "        request_sha256 TEXT", """        ordinals_json TEXT NOT NULL CHECK(json_valid(ordinals_json) AND json_type(ordinals_json)='array'
            AND json_array_length(ordinals_json)=json_array_length(members_json)),
        page ANY NOT NULL CHECK(typeof(page)='integer' AND page BETWEEN 1 AND 1000),
        visual ANY NOT NULL CHECK(typeof(visual)='integer' AND visual IN (0,1)),
        wire_bytes ANY NOT NULL CHECK(typeof(wire_bytes)='integer' AND wire_bytes>0),
        image_bytes ANY NOT NULL CHECK(typeof(image_bytes)='integer' AND image_bytes BETWEEN 1 AND 16777216),
        request_sha256 TEXT""")
        sql = _once(sql, "input_size_bytes BETWEEN 1 AND 65536", "input_size_bytes=wire_bytes+image_bytes")
    elif original.startswith("CREATE TRIGGER native_batch_insert "):
        sql = _once(sql, "length(value)<>66 OR substr(value,1,2)<>'N_' OR substr(value,3)", "length(value)<>68 OR substr(value,1,4)<>'PVC_' OR substr(value,5)")
        sql = _once(sql, "    BEGIN SELECT RAISE", """      OR json_array_length(NEW.members_json)+NEW.visual NOT BETWEEN 1 AND 16
      OR EXISTS(SELECT 1 FROM json_each(NEW.ordinals_json) n WHERE n.type<>'integer' OR n.value NOT BETWEEN 1 AND 4096
          OR (n.key>0 AND n.value<=json_extract(NEW.ordinals_json,'$['||(n.key-1)||']')))
      OR EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b,json_each(b.ordinals_json) old,json_each(NEW.ordinals_json) n
          WHERE b.run_id=NEW.run_id AND old.value>=n.value)
      OR EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b WHERE b.run_id=NEW.run_id
          AND (b.page>NEW.page OR (b.page=NEW.page AND NEW.visual=1)))
      OR (NEW.visual=0 AND NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b
          WHERE b.run_id=NEW.run_id AND b.page=NEW.page AND b.visual=1))
      OR NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_run r
          JOIN pdf_visual_source_body a ON a.source_id=r.source_archive_id
          JOIN pdf_visual_page p ON p.source_id=r.source_archive_id AND p.page=NEW.page
          WHERE r.id=NEW.run_id AND p.size_bytes=NEW.image_bytes
            AND json_extract(a.bundle_json,'$.requests['||(NEW.ordinal-1)||']')=NEW.request_sha256)
    BEGIN SELECT RAISE""")
    elif original.startswith("CREATE TRIGGER native_attempt_insert "):
        sql = _once(sql, "          AND EXISTS(SELECT 1 FROM pdf_visual_authoring_run_body", "          AND (SELECT count(*) FROM pdf_visual_authoring_batch WHERE run_id=r.id AND visual=1)=(SELECT page_count FROM pdf_visual_source WHERE id=r.source_archive_id)\n          AND EXISTS(SELECT 1 FROM pdf_visual_authoring_run_body")
    elif original.startswith("CREATE TABLE native_authoring_outcome("):
        sql = _once(sql, "        completed_at ANY", """        evidence_sha256 TEXT NOT NULL CHECK(length(evidence_sha256)=64 AND evidence_sha256 NOT GLOB '*[^0-9a-f]*'),
        evidence_size_bytes ANY NOT NULL CHECK(typeof(evidence_size_bytes)='integer' AND evidence_size_bytes BETWEEN 2 AND 65536),
        evidence_binding TEXT NOT NULL CHECK(evidence_binding IN ('matched','absent','conflicting')
            AND (status<>'validated' OR evidence_binding='matched')),
        completed_at ANY""")
    elif original.startswith("CREATE TABLE native_authoring_outcome_body("):
        sql = _once(sql, "        response TEXT", """        attempt_json TEXT NOT NULL CHECK(json_valid(attempt_json) AND json_type(attempt_json)='object'
            AND length(CAST(attempt_json AS BLOB)) BETWEEN 2 AND 65536),
        response TEXT""")
    elif original.startswith("CREATE TRIGGER native_outcome_body_insert "):
        sql = _once(sql, "          AND o.model_size_bytes", "          AND o.evidence_size_bytes=length(CAST(NEW.attempt_json AS BLOB))\n          AND o.evidence_binding=("+_binding("NEW")+")\n          AND o.model_size_bytes")
    elif original.startswith("CREATE TRIGGER native_snapshot_body_insert "):
        sql = f"CREATE TRIGGER pdf_visual_snapshot_body_insert BEFORE INSERT ON pdf_visual_wiki_snapshot_body WHEN {_snapshot} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_SNAPSHOT_BODY_INVALID'); END"
    _statements.append(sql)

V043_STATEMENTS = (
    "CREATE VIEW pdf_visual_registered_run AS SELECT CAST(NULL AS TEXT) AS run_id WHERE 0",
    "CREATE VIEW pdf_visual_registered_publication AS SELECT CAST(NULL AS TEXT) AS snapshot_id,CAST(NULL AS TEXT) AS publication_operation_id WHERE 0",
    *_statements,
    *(f"CREATE TRIGGER pdf_visual_excludes_{table} BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM pdf_visual_authoring_run WHERE preview_id=NEW.preview_id) BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_PREVIEW_ALREADY_OWNED'); END" for table in OTHER_OWNERS),
)
V043_POSTCONDITION_QUERIES = (
    *(_namespace(q) for q in V029_POSTCONDITION_QUERIES),
    f"""SELECT 'visual failure evidence binding invalid' FROM pdf_visual_authoring_outcome o
        JOIN pdf_visual_authoring_outcome_body b ON b.attempt_id=o.attempt_id
        WHERE o.evidence_binding<>({_binding('b')}) LIMIT 1""",
    """SELECT 'visual pages not assigned exactly once' FROM pdf_visual_authoring_run r
        WHERE (SELECT count(*) FROM pdf_visual_authoring_batch WHERE run_id=r.id AND visual=1)
          <>(SELECT page_count FROM pdf_visual_source WHERE id=r.source_archive_id) LIMIT 1""",
    *(f"SELECT 'visual preview shared with {table}' FROM pdf_visual_authoring_run r WHERE EXISTS(SELECT 1 FROM {table} WHERE preview_id=r.preview_id) LIMIT 1" for table in OTHER_OWNERS),
    f"SELECT 'visual snapshot identity invalid' FROM pdf_visual_wiki_snapshot_body retained WHERE ({_snapshot.replace('NEW.', 'retained.')}) LIMIT 1",
)
