"""Additive role-writer storage; legacy tables/policies stay byte-for-byte.

The fixed v29 state/attempt/retention contract is instantiated under distinct
names and exact role identities. No runtime flag, worker or publication is
registered. All raw/model/claim text remains in purgeable bodies.
"""
import re
from .v014_wiki_authoring import _sha
from .v029_native_authoring_storage import (
    V029_STATEMENTS, V029_POSTCONDITION_QUERIES, METADATA_TYPES as BASE_TYPES,
    _META_TYPES, _META_KEYS, FAILURE_CODES,
)
from .v032_native_display_storage import (
    _once, _exact_object, _receipt_shape_valid, _batch_shape_valid,
    DISPLAY_VERSION, DISPLAY_COVERAGE, DISPLAY_PLAN_VERSION,
)

POLICY = "wiki-pptx-role-writing-v2"
SOURCE_POLICY = "canonical-pptx-role-authoring-source-v1"
ROLE_VERSION = "pptx-source-role-context-v1"
ROLE_COVERAGE = "direct_shapes_groups_run_and_paragraph_defaults_not_rendered_or_inherited_styles"
ASSEMBLY = "wiki-pptx-role-assembly-v1"
LITERAL = "pptx-source-literal-h3-or-strong-p-v1"
PROMPT_SHA256 = "b5012c41496f6f9e8c8d4b753ec663b587c1796b687cff3399d81233032d00f8"
METADATA_TYPES = {
    **{("source_role_version" if k == "source_slot_version" else k): v for k, v in BASE_TYPES.items()},
    **dict.fromkeys(("literal_render_version", "role_context_sha256", "role_context_coverage",
        "display_version", "display_coverage", "display_slot_version", "display_sha256"), "text"),
    "role_context_size_bytes": "integer", "display_size_bytes": "integer",
}


def _namespace(sql):
    # Only identifiers owned by the v29 storage packet; extraction/source
    # contracts and policy strings are not renamed.
    identifiers = r"\b(?:native_authoring_[a-z_]+|native_wiki_snapshot[a-z_]*|native_(?:run|state|batch|attempt|outcome|snapshot)_[a-z_]+|native_pending_purge_guard)\b"
    sql = re.sub(identifiers, lambda m: "pptx_role_" + m[0][len("native_"):], sql)
    return sql.replace("idx_native_validated_batch", "idx_pptx_role_validated_batch").replace("NATIVE_", "PPTX_ROLE_")


def _metadata_valid(value):
    fixed = {
        "policy_version": POLICY, "source_preparation_version": SOURCE_POLICY,
        "source_role_version": ROLE_VERSION, "assembly_version": ASSEMBLY,
        "table_rendering_version": "native-source-table-html-v1", "literal_render_version": LITERAL,
        "prompt_sha256": PROMPT_SHA256, "role_context_coverage": ROLE_COVERAGE,
        "display_version": DISPLAY_VERSION, "display_coverage": DISPLAY_COVERAGE,
        "display_slot_version": DISPLAY_PLAN_VERSION,
    }
    return " AND ".join([
        _exact_object(value, METADATA_TYPES),
        *(f"json_extract({value},'$.{k}')='{v}'" for k, v in fixed.items()),
        *(_sha(f"json_extract({value},'$.{k}')") for k in ("role_context_sha256", "display_sha256")),
        f"json_extract({value},'$.role_context_size_bytes') BETWEEN 1 AND 16777216",
        f"json_extract({value},'$.display_size_bytes') BETWEEN 1 AND 16777216",
    ])


def _configuration_valid(value):
    return f"""{_exact_object(value, {'version': 'text', 'policy_version': 'text',
        'prompt_sha256': 'text', 'chunk_bytes': 'integer', 'context_bytes': 'integer',
        'model_policy': 'object', 'work_policy': 'object', 'execution_policy': 'object'})}
        AND json_extract({value},'$.version')='{SOURCE_POLICY}'
        AND json_extract({value},'$.policy_version')='{POLICY}'
        AND json_extract({value},'$.prompt_sha256')='{PROMPT_SHA256}'
        AND json_extract({value},'$.chunk_bytes') BETWEEN 4 AND 8192
        AND json_extract({value},'$.context_bytes') BETWEEN json_extract({value},'$.chunk_bytes') AND 8192"""


def _guard(name):
    statement = next(s for s in V029_STATEMENTS if s.startswith("CREATE TRIGGER " + name + " "))
    return statement.split("WHEN ", 1)[1].split("BEGIN ", 1)[0].strip()


_run = _guard("native_run_insert")
for before, after in (
    ("wiki-native-batch-authoring-v1", POLICY),
    ("canonical-native-authoring-source-v1", SOURCE_POLICY),
    ("wiki-native-slot-authoring-v1", ROLE_VERSION),
    ("wiki-native-slot-assembly-v1", ASSEMBLY),
    (_META_TYPES, " AND ".join(f"json_type(NEW.metadata_json,'$.{k}')='{v}'" for k, v in METADATA_TYPES.items())),
    (_META_KEYS, ",".join("'" + k + "'" for k in METADATA_TYPES)),
):
    _run = _once(_run, before, after)
_run = _once(_run, f"json_each(NEW.metadata_json))<>{len(BASE_TYPES)}",
    f"json_each(NEW.metadata_json))<>{len(METADATA_TYPES)}", count=2)
# The predecessor standalone check, after replacing its generated type block.
_run = _once(_run, "$.source_slot_version", "$.source_role_version")
_run += f"""
    OR NOT coalesce(({_metadata_valid('NEW.metadata_json')}),0)
    OR NOT EXISTS(SELECT 1 FROM extraction_native_table_structure d
        WHERE d.snapshot_id=NEW.extraction_snapshot_id AND d.source_format='pptx')
    OR EXISTS(SELECT 1 FROM native_authoring_run r WHERE r.preview_id=NEW.preview_id)
    OR EXISTS(SELECT 1 FROM native_workflow_request q WHERE q.preview_id=NEW.preview_id)"""

_snapshot = _guard("native_snapshot_body_insert")
_snapshot = _once(_snapshot, "wiki-native-batch-authoring-v1", POLICY)
_snapshot = _snapshot.replace("$.batch_lineage.receipts", "$.receipts").replace(
    "$.batch_lineage.batches", "$.batches").replace("$.batch_lineage.prompt_sha256", "$.prompt_sha256")
_snapshot += f"""
    OR NOT EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        JOIN source_artifact a ON a.id=r.artifact_id JOIN extraction_snapshot e ON e.id=r.extraction_snapshot_id
        JOIN extraction_native_table_structure d ON d.snapshot_id=e.id
        WHERE s.id=NEW.snapshot_id
          AND json_extract(NEW.manifest_json,'$.assembly_version')='{ASSEMBLY}'
          AND json_extract(NEW.manifest_json,'$.literal_render_version')='{LITERAL}'
          AND json_extract(NEW.manifest_json,'$.table_render_version')='native-source-table-html-v1'
          AND json_type(NEW.manifest_json,'$.all_nonblank_chunks_authored')='true'
          AND json_type(NEW.manifest_json,'$.all_role_candidates_assigned_once')='true'
          AND json_type(NEW.manifest_json,'$.all_native_tables_preserved')='true'
          AND json_type(NEW.manifest_json,'$.semantic_review_performed')='false'
          AND json_type(NEW.manifest_json,'$.semantic_completeness_proven')='false'
          AND json_type(NEW.manifest_json,'$.caption_ownership_inferred')='false'
          AND json_extract(NEW.manifest_json,'$.output_range_unit')='utf8_bytes'
          AND json_extract(NEW.manifest_json,'$.source.format')='pptx'
          AND json_extract(NEW.manifest_json,'$.source.raw_sha256')=a.raw_sha256
          AND json_type(NEW.manifest_json,'$.source.raw_bytes')='integer'
          AND json_extract(NEW.manifest_json,'$.source.raw_bytes')=a.raw_size_bytes
          AND json_extract(NEW.manifest_json,'$.source.extraction_sha256')=e.extracted_sha256
          AND json_extract(NEW.manifest_json,'$.source.structure_sha256')=d.structure_sha256
          AND {_exact_object("json_extract(NEW.manifest_json,'$.source.role_context')",
              dict.fromkeys(('version','coverage','sha256'), 'text') | {'bytes':'integer'})}
          AND json_extract(NEW.manifest_json,'$.source.role_context.version')='{ROLE_VERSION}'
          AND json_extract(NEW.manifest_json,'$.source.role_context.coverage')='{ROLE_COVERAGE}'
          AND json_extract(NEW.manifest_json,'$.source.role_context.sha256')=json_extract(r.metadata_json,'$.role_context_sha256')
          AND json_extract(NEW.manifest_json,'$.source.role_context.bytes')=json_extract(r.metadata_json,'$.role_context_size_bytes')
          AND {_exact_object("json_extract(NEW.manifest_json,'$.source.display')",
              dict.fromkeys(('version','coverage','slot_version','sha256'), 'text') | {'bytes':'integer'})}
          AND json_extract(NEW.manifest_json,'$.source.display.version')='{DISPLAY_VERSION}'
          AND json_extract(NEW.manifest_json,'$.source.display.coverage')='{DISPLAY_COVERAGE}'
          AND json_extract(NEW.manifest_json,'$.source.display.slot_version')='{DISPLAY_PLAN_VERSION}'
          AND json_extract(NEW.manifest_json,'$.source.display.sha256')=json_extract(r.metadata_json,'$.display_sha256')
          AND json_extract(NEW.manifest_json,'$.source.display.bytes')=json_extract(r.metadata_json,'$.display_size_bytes')
          AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.receipts') rc
              WHERE NOT EXISTS(SELECT 1 FROM native_authoring_batch b
                  JOIN native_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
                  JOIN native_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
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
              WHERE NOT EXISTS(SELECT 1 FROM native_authoring_batch b
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
    statement = original
    if original.startswith("CREATE TRIGGER native_run_insert "):
        statement = f"""CREATE TRIGGER native_run_insert BEFORE INSERT ON native_authoring_run
            WHEN {_run} BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_SOURCE_INVALID'); END"""
        # Keep the cross-family exclusivity check's historical table name.
        statement = _namespace(statement)
        statement = _once(statement,
            "OR EXISTS(SELECT 1 FROM pptx_role_authoring_run r WHERE r.preview_id=NEW.preview_id)",
            "OR EXISTS(SELECT 1 FROM native_authoring_run r WHERE r.preview_id=NEW.preview_id)")
    elif original.startswith("CREATE TRIGGER native_snapshot_body_insert "):
        statement = _namespace(f"""CREATE TRIGGER native_snapshot_body_insert BEFORE INSERT ON native_wiki_snapshot_body
            WHEN {_snapshot} BEGIN SELECT RAISE(ABORT,'NATIVE_SNAPSHOT_BODY_INVALID'); END""")
    else:
        statement = _namespace(statement)
    if original.startswith("CREATE TABLE native_authoring_batch("):
        statement = _once(statement, "length(batch_id)=66 AND substr(batch_id,1,2)='B_'",
            "length(batch_id)=67 AND substr(batch_id,1,3)='RB_'")
        statement = _once(statement, "substr(batch_id,3)", "substr(batch_id,4)")
        statement = _once(statement, "json_array_length(members_json) BETWEEN 1 AND 4",
            "json_array_length(members_json) BETWEEN 1 AND 16")
        statement = _once(statement, "        request_sha256 TEXT", """        ordinals_json TEXT NOT NULL CHECK(json_valid(ordinals_json)
            AND json_type(ordinals_json)='array'
            AND json_array_length(ordinals_json)=json_array_length(members_json)),
        request_sha256 TEXT""")
    if original.startswith("CREATE TRIGGER native_batch_insert "):
        statement = _once(statement, "length(value)<>66 OR substr(value,1,2)<>'N_' OR substr(value,3)",
            "length(value)<>67 OR substr(value,1,3)<>'RC_' OR substr(value,4)")
        statement = _once(statement, "    BEGIN SELECT RAISE", """      OR EXISTS(SELECT 1 FROM json_each(NEW.ordinals_json) position
          WHERE position.type<>'integer' OR position.value NOT BETWEEN 1 AND 4096
            OR (position.key>0 AND position.value<=json_extract(NEW.ordinals_json,
                '$[' || (position.key-1) || ']')))
      OR EXISTS(SELECT 1 FROM pptx_role_authoring_batch b,
          json_each(b.ordinals_json) prior,json_each(NEW.ordinals_json) current
          WHERE b.run_id=NEW.run_id AND prior.value>=current.value)
    BEGIN SELECT RAISE""")
    _statements.append(statement)

V035_STATEMENTS = (
    # Schema-hashed registration seams. Only a reviewed forward migration may
    # replace these empty views with exact permanent workflow/publication
    # lineage. They are not writable feature flags or present authorization.
    "CREATE VIEW pptx_role_registered_run AS SELECT CAST(NULL AS TEXT) AS run_id WHERE 0",
    """CREATE VIEW pptx_role_registered_publication AS
        SELECT CAST(NULL AS TEXT) AS snapshot_id,CAST(NULL AS TEXT) AS publication_operation_id WHERE 0""",
    *_statements,
    f"""CREATE TRIGGER pptx_role_configuration_insert BEFORE INSERT ON pptx_role_authoring_run_body
        WHEN NOT coalesce(({_configuration_valid('NEW.configuration_json')}),0)
        BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_CONFIGURATION_INVALID'); END""",
    """CREATE TRIGGER pptx_role_excludes_legacy_run BEFORE INSERT ON native_authoring_run
        WHEN EXISTS(SELECT 1 FROM pptx_role_authoring_run r WHERE r.preview_id=NEW.preview_id)
        BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_PREVIEW_ALREADY_OWNED'); END""",
    """CREATE TRIGGER pptx_role_workflow_not_registered BEFORE INSERT ON native_workflow_request
        WHEN EXISTS(SELECT 1 FROM pptx_role_authoring_run r WHERE r.preview_id=NEW.preview_id)
        BEGIN SELECT RAISE(ABORT,'PPTX_ROLE_WORKFLOW_NOT_REGISTERED'); END""",
)
# Retention checks remain valid after bodies are legitimately purged. Do not
# reevaluate original live TTL/account conditions as permanent integrity facts.
V035_POSTCONDITION_QUERIES = (
    *(_namespace(q) for q in V029_POSTCONDITION_QUERIES),
    f"""SELECT 'role metadata invalid' FROM pptx_role_authoring_run r
        WHERE json_extract(r.metadata_json,'$.policy_version')='{POLICY}'
          AND NOT coalesce(({_metadata_valid('r.metadata_json')}),0) LIMIT 1""",
    f"""SELECT 'role configuration invalid' FROM pptx_role_authoring_run_body b
        JOIN pptx_role_authoring_run r ON r.id=b.run_id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{POLICY}'
          AND NOT coalesce(({_configuration_valid('b.configuration_json')}),0) LIMIT 1""",
    """SELECT 'role preview shared with legacy or unregistered workflow' FROM pptx_role_authoring_run r
        WHERE EXISTS(SELECT 1 FROM native_authoring_run old WHERE old.preview_id=r.preview_id)
          OR (EXISTS(SELECT 1 FROM native_workflow_request q WHERE q.preview_id=r.preview_id)
            AND NOT EXISTS(SELECT 1 FROM pptx_role_registered_run registered WHERE registered.run_id=r.id)) LIMIT 1""",
    """SELECT 'role preview consumed before publication registration' FROM pptx_role_authoring_run r
        JOIN publication_preview p ON p.id=r.preview_id WHERE p.status='consumed'
          AND NOT EXISTS(SELECT 1 FROM pptx_role_wiki_snapshot s
            JOIN pptx_role_registered_publication registered ON registered.snapshot_id=s.id
            WHERE s.run_id=r.id) LIMIT 1""",
    _namespace(f"""SELECT 'role snapshot identity invalid' FROM native_wiki_snapshot s
        JOIN native_wiki_snapshot_body retained ON retained.snapshot_id=s.id
        JOIN native_authoring_run origin ON origin.id=s.run_id
        WHERE json_extract(origin.metadata_json,'$.policy_version')='{POLICY}'
          AND ({_snapshot.replace('NEW.', 'retained.')}) LIMIT 1"""),
)
