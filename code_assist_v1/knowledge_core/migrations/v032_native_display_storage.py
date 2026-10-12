"""Forward-only display storage, with the complete historical v29 branch retained.

No default producer/worker/publication activation. Display text is reconstructed
from retained original bytes, not duplicated in immutable metadata. SQL pins
policy/type/relational identity; source authenticity and exact replay remain the
service's responsibility. All private bodies retain the existing lifecycle.
"""
from .v014_wiki_authoring import _sha
from .v029_native_authoring_storage import V029_STATEMENTS, METADATA_TYPES, _META_TYPES, _META_KEYS

DISPLAY_POLICY = "wiki-native-display-batch-authoring-v1"
DISPLAY_SOURCE_POLICY = "canonical-native-display-authoring-source-v1"
DISPLAY_SLOT_POLICY = "wiki-native-display-slot-authoring-v1"
DISPLAY_ASSEMBLY_POLICY = "wiki-native-display-slot-assembly-v1"
DISPLAY_PROMPT_SHA256 = "8d469928c6500cdc191915f0ce220fd783b7a79d4efe3c65118d46d99dd873be"
DISPLAY_VERSION = "pptx-source-display-v2"
DISPLAY_COVERAGE = "declared_slide_sp_cxnsp_group_direct_paragraphs_and_native_cells_no_mce_or_style_inheritance"
DISPLAY_PLAN_VERSION = "pptx-source-display-slots-v2"
DISPLAY_METADATA_TYPES = {**METADATA_TYPES,
    **dict.fromkeys(("display_version", "display_coverage", "display_slot_version", "display_sha256"), "text"),
    "display_size_bytes": "integer"}


def _old_guard(name):
    statement = next(s for s in V029_STATEMENTS if s.startswith("CREATE TRIGGER " + name + " "))
    return statement.split("WHEN ", 1)[1].split("BEGIN ", 1)[0].strip()


def _once(text, before, after, count=1):
    if text.count(before) != count:
        raise RuntimeError("Frozen native storage guard differs from its reviewed predecessor.")
    return text.replace(before, after)


_old_run = _old_guard("native_run_insert")
_display_run = _old_run
for _before, _after in (
    ("wiki-native-batch-authoring-v1", DISPLAY_POLICY),
    ("canonical-native-authoring-source-v1", DISPLAY_SOURCE_POLICY),
    ("wiki-native-slot-authoring-v1", DISPLAY_SLOT_POLICY),
    ("wiki-native-slot-assembly-v1", DISPLAY_ASSEMBLY_POLICY),
    (_META_TYPES, " AND ".join(f"json_type(NEW.metadata_json,'$.{key}')='{kind}'"
        for key, kind in DISPLAY_METADATA_TYPES.items())),
    (_META_KEYS, ",".join("'" + key + "'" for key in DISPLAY_METADATA_TYPES)),
):
    _display_run = _once(_display_run, _before, _after)
_display_run = _once(_display_run,
    f"json_each(NEW.metadata_json))<>{len(METADATA_TYPES)}",
    f"json_each(NEW.metadata_json))<>{len(DISPLAY_METADATA_TYPES)}", count=2)


def _display_metadata_valid(alias):
    types = " AND ".join(f"json_type({alias},'$.{key}')='{kind}'"
        for key, kind in DISPLAY_METADATA_TYPES.items())
    keys = ",".join("'" + key + "'" for key in DISPLAY_METADATA_TYPES)
    return f"""{types}
        AND (SELECT count(*) FROM json_each({alias}))={len(DISPLAY_METADATA_TYPES)}
        AND (SELECT count(DISTINCT key) FROM json_each({alias}))={len(DISPLAY_METADATA_TYPES)}
        AND NOT EXISTS(SELECT 1 FROM json_each({alias}) WHERE key NOT IN ({keys}))
        AND json_extract({alias},'$.schema_version')=1
        AND json_extract({alias},'$.policy_version')='{DISPLAY_POLICY}'
        AND json_extract({alias},'$.source_preparation_version')='{DISPLAY_SOURCE_POLICY}'
        AND json_extract({alias},'$.source_slot_version')='{DISPLAY_SLOT_POLICY}'
        AND json_extract({alias},'$.assembly_version')='{DISPLAY_ASSEMBLY_POLICY}'
        AND json_extract({alias},'$.table_rendering_version')='native-source-table-html-v1'
        AND json_extract({alias},'$.prompt_sha256')='{DISPLAY_PROMPT_SHA256}'
        AND json_extract({alias},'$.display_version')='{DISPLAY_VERSION}'
        AND json_extract({alias},'$.display_coverage')='{DISPLAY_COVERAGE}'
        AND json_extract({alias},'$.display_slot_version')='{DISPLAY_PLAN_VERSION}'
        AND {_sha("json_extract(" + alias + ",'$.display_sha256')")}
        AND json_extract({alias},'$.display_size_bytes') BETWEEN 1 AND 16777216"""


def _exact_object(value, fields):
    keys = ",".join("'" + key + "'" for key in fields)
    types = " AND ".join(f"json_type({value},'$.{key}')='{kind}'" for key, kind in fields.items())
    return f"""json_type({value})='object' AND {types}
        AND (SELECT count(*) FROM json_each({value}))={len(fields)}
        AND (SELECT count(DISTINCT key) FROM json_each({value}))={len(fields)}
        AND NOT EXISTS(SELECT 1 FROM json_each({value}) WHERE key NOT IN ({keys}))"""


def _model_order(key):
    return f"CASE {key} WHEN 'model_kind' THEN 0 WHEN 'model_id' THEN 1 WHEN 'model_used' THEN 2 END"


def _receipt_shape_valid():
    # A receipt stores model fields as ordered pairs; the outcome stores an
    # object. Pin both directions, types, duplicate rejection and author order.
    # CASE protects json_each's decoded scalar values from reparsing as JSON.
    fields = dict.fromkeys(("batch_id", "request_sha256", "response", "response_sha256",
        "transport_json", "policy_version", "validation_state"), "text")
    fields["model"] = "array"
    return f"""CASE WHEN rc.type='object' THEN coalesce((
        {_exact_object('rc.value', fields)}
        AND (SELECT count(*) FROM json_each(ob.model_json))
            =(SELECT count(DISTINCT key) FROM json_each(ob.model_json))
        AND NOT EXISTS(SELECT 1 FROM json_each(ob.model_json)
            WHERE key NOT IN ('model_kind','model_id','model_used') OR type<>'text')
        AND json_array_length(rc.value,'$.model')=(SELECT count(*) FROM json_each(ob.model_json))
        AND NOT EXISTS(SELECT 1 FROM json_each(rc.value,'$.model') pair
            WHERE CASE WHEN pair.type='array' THEN NOT coalesce((
              json_array_length(pair.value)=2
              AND json_type(pair.value,'$[0]')='text' AND json_type(pair.value,'$[1]')='text'
              AND EXISTS(SELECT 1 FROM json_each(ob.model_json) field
                  WHERE field.key=json_extract(pair.value,'$[0]')
                    AND field.value=json_extract(pair.value,'$[1]')
                    AND pair.key=(SELECT count(*) FROM json_each(ob.model_json) preceding
                        WHERE {_model_order('preceding.key')}<{_model_order('field.key')}))
            ),0) ELSE 1 END)
        ),0) ELSE 0 END"""


def _batch_shape_valid():
    return f"""CASE WHEN entry.type='object' THEN coalesce((
        {_exact_object('entry.value', {'ordinal': 'integer', 'batch_id': 'text', 'chunk_ordinals': 'array'})}
        AND json_array_length(entry.value,'$.chunk_ordinals')=json_array_length(b.members_json)
        AND NOT EXISTS(SELECT 1 FROM json_each(entry.value,'$.chunk_ordinals') chunk
            WHERE chunk.type<>'integer' OR chunk.value NOT BETWEEN 1 AND 4096
              OR (chunk.key>0 AND chunk.value<=json_extract(entry.value,
                  '$.chunk_ordinals[' || (chunk.key-1) || ']')))
        ),0) ELSE 0 END"""


def _display_configuration_valid(value):
    return f"""{_exact_object(value, {'version': 'text', 'chunk_bytes': 'integer',
        'context_bytes': 'integer', 'model_policy': 'object', 'work_policy': 'object',
        'execution_policy': 'object'})}
        AND json_extract({value},'$.version')='{DISPLAY_SOURCE_POLICY}'
        AND json_extract({value},'$.chunk_bytes') BETWEEN 4 AND 8192
        AND json_extract({value},'$.context_bytes') BETWEEN json_extract({value},'$.chunk_bytes') AND 8192"""


_display_run += f"""
      OR NOT coalesce(({_display_metadata_valid('NEW.metadata_json')}),0)
      OR NOT EXISTS(SELECT 1 FROM extraction_native_table_structure d
          WHERE d.snapshot_id=NEW.extraction_snapshot_id AND d.source_format='pptx')
      OR EXISTS(SELECT 1 FROM native_workflow_request q WHERE q.preview_id=NEW.preview_id)"""
_old_snapshot = _old_guard("native_snapshot_body_insert")
_display_snapshot = _once(_old_snapshot, "wiki-native-batch-authoring-v1", DISPLAY_POLICY)
_display_snapshot += f"""
      OR NOT EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
          JOIN source_artifact a ON a.id=r.artifact_id
          JOIN extraction_native_table_structure d ON d.snapshot_id=r.extraction_snapshot_id
          WHERE s.id=NEW.snapshot_id
            AND json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
            AND json_extract(NEW.manifest_json,'$.source_slot_policy_version')='{DISPLAY_SLOT_POLICY}'
            AND json_extract(NEW.manifest_json,'$.assembly_version')='{DISPLAY_ASSEMBLY_POLICY}'
            AND json_extract(NEW.manifest_json,'$.table_rendering_version')='native-source-table-html-v1'
            AND json_extract(NEW.manifest_json,'$.batch_lineage.policy_version')='{DISPLAY_POLICY}'
            AND json_extract(NEW.manifest_json,'$.source.format')='pptx'
            AND json_extract(NEW.manifest_json,'$.source.raw_sha256')=a.raw_sha256
            AND json_extract(NEW.manifest_json,'$.source.structure_sha256')=d.structure_sha256
            AND json_extract(NEW.manifest_json,'$.source.display.version')=json_extract(r.metadata_json,'$.display_version')
            AND json_extract(NEW.manifest_json,'$.source.display.coverage')=json_extract(r.metadata_json,'$.display_coverage')
            AND json_extract(NEW.manifest_json,'$.source.display.slot_version')=json_extract(r.metadata_json,'$.display_slot_version')
            AND json_extract(NEW.manifest_json,'$.source.display.sha256')=json_extract(r.metadata_json,'$.display_sha256')
            AND json_type(NEW.manifest_json,'$.source.display.bytes')='integer'
            AND json_extract(NEW.manifest_json,'$.source.display.bytes')=json_extract(r.metadata_json,'$.display_size_bytes')
            AND json_type(NEW.manifest_json,'$.source.display')='object'
            AND (SELECT count(*) FROM json_each(NEW.manifest_json,'$.source.display'))=5
            AND (SELECT count(DISTINCT key) FROM json_each(NEW.manifest_json,'$.source.display'))=5
            AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.source.display')
                WHERE key NOT IN ('version','coverage','slot_version','sha256','bytes'))
            AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.batch_lineage.receipts') rc
                WHERE NOT EXISTS(SELECT 1 FROM native_authoring_batch b
                    JOIN native_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
                    JOIN native_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
                    WHERE b.run_id=r.id AND b.ordinal=rc.key+1
                      AND json_extract(rc.value,'$.batch_id')=b.batch_id
                      AND json_extract(rc.value,'$.request_sha256')=b.request_sha256
                      AND json_extract(rc.value,'$.policy_version')='{DISPLAY_POLICY}'
                      AND json_extract(rc.value,'$.validation_state')='validated'
                      AND json_extract(rc.value,'$.response_sha256')=o.response_sha256
                      AND json_extract(rc.value,'$.response')=ob.response
                      AND json_extract(rc.value,'$.transport_json')=ob.transport_json
                      AND ({_receipt_shape_valid()})))
            AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.batch_lineage.batches') entry
                WHERE NOT EXISTS(SELECT 1 FROM native_authoring_batch b
                    WHERE b.run_id=r.id AND b.ordinal=entry.key+1
                      AND json_type(entry.value,'$.ordinal')='integer'
                      AND json_extract(entry.value,'$.ordinal')=b.ordinal
                      AND json_extract(entry.value,'$.batch_id')=b.batch_id
                      AND ({_batch_shape_valid()}))))"""

V032_STATEMENTS = (
    # Stable, schema-hashed extension points, not writable activation flags.
    # This catalog registers no workflow/publication. A reviewed successor can
    # replace these empty views with exact lineage eligibility without changing
    # this catalog's historical checksum or skipping its permanent invariants.
    "CREATE VIEW native_display_registered_run AS SELECT CAST(NULL AS TEXT) AS run_id WHERE 0",
    """CREATE VIEW native_display_registered_publication AS
        SELECT CAST(NULL AS TEXT) AS snapshot_id,CAST(NULL AS TEXT) AS publication_operation_id WHERE 0""",
    "DROP TRIGGER native_run_insert",
    f"""CREATE TRIGGER native_run_insert BEFORE INSERT ON native_authoring_run
    WHEN CASE WHEN json_extract(NEW.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
      THEN ({_display_run}) ELSE ({_old_run}) END
    BEGIN SELECT RAISE(ABORT,'NATIVE_RUN_SOURCE_INVALID'); END""",
    f"""CREATE TRIGGER native_display_configuration_insert BEFORE INSERT ON native_authoring_run_body
    WHEN EXISTS(SELECT 1 FROM native_authoring_run r WHERE r.id=NEW.run_id
        AND json_extract(r.metadata_json,'$.policy_version')='wiki-native-display-batch-authoring-v1')
      AND NOT coalesce(({_display_configuration_valid('NEW.configuration_json')}),0)
    BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_CONFIGURATION_INVALID'); END""",
    "DROP TRIGGER native_snapshot_body_insert",
    f"""CREATE TRIGGER native_snapshot_body_insert BEFORE INSERT ON native_wiki_snapshot_body
    WHEN CASE WHEN EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        WHERE s.id=NEW.snapshot_id AND json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}')
      THEN ({_display_snapshot}) ELSE ({_old_snapshot}) END
    BEGIN SELECT RAISE(ABORT,'NATIVE_SNAPSHOT_BODY_INVALID'); END""",
    f"""CREATE TRIGGER native_display_preview_consume_guard BEFORE UPDATE OF status ON publication_preview
    WHEN NEW.status='consumed' AND EXISTS(SELECT 1 FROM native_authoring_run r
        WHERE r.preview_id=NEW.id AND json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}')
    BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_PUBLICATION_NOT_REGISTERED'); END""",
    f"""CREATE TRIGGER native_display_selection_guard BEFORE INSERT ON publication_native_selection
    WHEN EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        WHERE s.id IN (NEW.native_snapshot_id,NEW.requested_snapshot_id)
          AND json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}')
    BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_PUBLICATION_NOT_REGISTERED'); END""",
    f"""CREATE TRIGGER native_display_body_selection_guard BEFORE INSERT ON publication_body_selection
    WHEN EXISTS(SELECT 1 FROM native_wiki_snapshot s JOIN native_authoring_run r ON r.id=s.run_id
        WHERE s.id IN (NEW.requested_native_wiki_id,NEW.selected_native_wiki_id)
          AND json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}')
    BEGIN SELECT RAISE(ABORT,'NATIVE_DISPLAY_PUBLICATION_NOT_REGISTERED'); END""",
)

V032_POSTCONDITION_QUERIES = (
    f"""SELECT 'native display workflow not registered' FROM native_authoring_run r
        JOIN native_workflow_request q ON q.preview_id=r.preview_id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND NOT EXISTS(SELECT 1 FROM native_display_registered_run registered WHERE registered.run_id=r.id) LIMIT 1""",
    f"""SELECT 'native display metadata invalid' FROM native_authoring_run r
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND NOT coalesce(({_display_metadata_valid('r.metadata_json')}),0) LIMIT 1""",
    f"""SELECT 'native display source format invalid' FROM native_authoring_run r
        JOIN extraction_native_table_structure d ON d.snapshot_id=r.extraction_snapshot_id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}' AND d.source_format<>'pptx' LIMIT 1""",
    f"""SELECT 'native display configuration policy invalid' FROM native_authoring_run r
        JOIN native_authoring_run_body b ON b.run_id=r.id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND NOT coalesce(({_display_configuration_valid('b.configuration_json')}),0) LIMIT 1""",
    f"""SELECT 'native display snapshot identity invalid' FROM native_wiki_snapshot s
        JOIN native_authoring_run r ON r.id=s.run_id JOIN native_wiki_snapshot_body retained ON retained.snapshot_id=s.id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND ({_display_snapshot.replace('NEW.', 'retained.')}) LIMIT 1""",
    f"""SELECT 'native display preview consumed before activation' FROM native_authoring_run r
        JOIN publication_preview p ON p.id=r.preview_id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND p.status='consumed' AND NOT EXISTS(SELECT 1 FROM native_wiki_snapshot s
            JOIN native_display_registered_publication registered ON registered.snapshot_id=s.id
            WHERE s.run_id=r.id) LIMIT 1""",
    f"""SELECT 'native display selected before activation' FROM native_wiki_snapshot s
        JOIN native_authoring_run r ON r.id=s.run_id
        WHERE json_extract(r.metadata_json,'$.policy_version')='{DISPLAY_POLICY}'
          AND (EXISTS(SELECT 1 FROM publication_native_selection selected
                WHERE s.id IN (selected.native_snapshot_id,selected.requested_snapshot_id)
                  AND NOT EXISTS(SELECT 1 FROM native_display_registered_publication registered
                    WHERE registered.snapshot_id=s.id AND registered.publication_operation_id=selected.publication_operation_id))
            OR EXISTS(SELECT 1 FROM publication_body_selection selected
                WHERE s.id IN (selected.selected_native_wiki_id,selected.requested_native_wiki_id)
                  AND NOT EXISTS(SELECT 1 FROM native_display_registered_publication registered
                    WHERE registered.snapshot_id=s.id AND registered.publication_operation_id=selected.publication_operation_id))) LIMIT 1""",
)
