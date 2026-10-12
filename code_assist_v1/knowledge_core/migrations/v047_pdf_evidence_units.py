"""Forward-only evidence-ID writing storage; old v1/v2 bytes stay valid.

SQL pins public identities, shape, bounds and validated response catalog echo.
It cannot hash/attest catalog content. Preparation and retained-source replay
rebuild the catalog from verified geometry/pixels; persisted JSON is not a
process capability. Failed raw responses retain their historical contract.
"""
import json

from . import v043_pdf_visual_authoring_storage as storage
from . import v044_pdf_visual_authoring_workflow as workflow
from . import v046_pdf_visual_writing_policy as previous
from .v014_wiki_authoring import _sha
from .v032_native_display_storage import _once, _exact_object

POLICY_V3 = "wiki-pdf-page-visual-writing-v3"
PROMPT_V3_SHA256 = "a22e165003b0d67c23c0ed7184da33017a6c481d1eb58dd420e0578ea38d92de"
EVIDENCE_VERSION = "pdf-source-evidence-units-v1"
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V3, PROMPT_V3_SHA256))
# Frozen values, not imported from a future mutable compiler implementation.
EVIDENCE_POLICY = dict(version=EVIDENCE_VERSION, schema_version=2, unit_codepoints=256,
    maximum_units=32768, maximum_source_bytes=8388608, maximum_catalog_bytes=16777216,
    maximum_references=4096, maximum_resolved_codepoints=8388608,
    per_claim_references=64, minimum_nonblank_owned_units=1,
    reference_policy="owned_plus_exact_allowed_context")
_POLICY_JSON = json.dumps(EVIDENCE_POLICY, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _v3(sql):
    assert storage.POLICY in sql and storage.PROMPT_SHA256 in sql
    return sql.replace(storage.POLICY, POLICY_V3).replace(storage.PROMPT_SHA256, PROMPT_V3_SHA256)


def _pair(policy, assembly, prompt):
    return "(" + " OR ".join(
        f"({policy}='{version}' AND {assembly}='{version}' AND {prompt}='{digest}')"
        for version, digest in POLICY_IDENTITIES) + ")"


def _policy_valid(value):
    return f"(({previous._policy_valid(value)}) OR ({_v3(workflow._policy_valid(value))}))"


_run = f"""(coalesce(({previous._run}),1) AND coalesce(({_v3(storage._run)}),1))
    OR NOT EXISTS(SELECT 1 FROM pdf_visual_source_body ab
      WHERE ab.source_id=NEW.source_archive_id
        AND json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.policy_version')
          =json_extract(NEW.metadata_json,'$.policy_version')
        AND json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.prompt_sha256')
          =json_extract(NEW.metadata_json,'$.prompt_sha256'))"""

_CAT = "json_extract(ec.value,'$.catalog')"
_SOURCE = f"json_extract({_CAT},'$.source')"
_CAT_FIELDS = dict(schema_version="integer", policy_version="text", evidence_version="text",
    source="object", plan_sha256="text", batch_id="text", page="integer",
    evidence_policy="object", source_descriptors="array", units="array", tasks="object")
_SOURCE_FIELDS = dict.fromkeys(("snapshot_id", "raw_sha256", "geometry_sha256", "visual_source_sha256"), "text")

# CASE prevents decoded JSON scalars from being reparsed as JSON text. Every
# nullable shape/value predicate is false, not a bypass, when SQL yields NULL.
_catalogs_valid = f"""json_extract(NEW.manifest_json,'$.evidence_version')='{EVIDENCE_VERSION}'
    AND json_array_length(NEW.manifest_json,'$.evidence_catalogs')=r.batch_count
    AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.evidence_catalogs') ec
      WHERE NOT CASE WHEN ec.type='object' AND json_type(ec.value,'$.catalog')='object'
        THEN coalesce((EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b
          JOIN pdf_visual_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
          JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
          WHERE b.run_id=r.id AND b.ordinal=ec.key+1
            AND ({_exact_object('ec.value', {'sha256': 'text', 'catalog': 'object'})})
            AND ({_sha("json_extract(ec.value,'$.sha256')")})
            AND ({_exact_object(_CAT, _CAT_FIELDS)})
            AND json_extract({_CAT},'$.schema_version')=2
            AND json_extract({_CAT},'$.policy_version')='{POLICY_V3}'
            AND json_extract({_CAT},'$.evidence_version')='{EVIDENCE_VERSION}'
            AND json_extract({_CAT},'$.plan_sha256')=r.source_plan_sha256
            AND json_extract({_CAT},'$.batch_id')=b.batch_id
            AND json_extract({_CAT},'$.page')=b.page
            AND json_extract({_CAT},'$.evidence_policy')='{_POLICY_JSON}'
            AND json_array_length({_CAT},'$.units') BETWEEN 0 AND 32768
            AND json_array_length({_CAT},'$.source_descriptors') BETWEEN 0 AND 8208
            AND length(CAST({_CAT} AS BLOB)) BETWEEN 2 AND 16777216
            AND CASE WHEN json_type({_CAT},'$.source')='object' THEN coalesce((
              ({_exact_object(_SOURCE, _SOURCE_FIELDS)})
              AND json_extract({_SOURCE},'$.snapshot_id')=r.extraction_snapshot_id
              AND json_extract({_SOURCE},'$.raw_sha256')=json_extract(ab.bundle_json,'$.source.geometry.raw_sha256')
              AND json_extract({_SOURCE},'$.geometry_sha256')=a.geometry_sha256
              AND ({_sha(f"json_extract({_SOURCE},'$.visual_source_sha256')")})),0) ELSE 0 END
            AND (SELECT count(*) FROM json_each({_CAT},'$.tasks'))=json_array_length(b.members_json)
            AND (SELECT count(DISTINCT key) FROM json_each({_CAT},'$.tasks'))=json_array_length(b.members_json)
            AND NOT EXISTS(SELECT 1 FROM json_each({_CAT},'$.tasks') t
              WHERE t.key NOT IN (SELECT value FROM json_each(b.members_json))
                OR NOT CASE WHEN t.type='object' THEN coalesce((
                  ({_exact_object('t.value', {'owned': 'array', 'context': 'array'})})
                  AND json_array_length(t.value,'$.owned') BETWEEN 1 AND 32768
                  AND json_array_length(t.value,'$.context') BETWEEN 0 AND 32768),0) ELSE 0 END)
            AND CASE WHEN json_valid(ob.response) THEN coalesce((
              json_type(ob.response)='object' AND json_type(ob.response,'$.schema_version')='integer'
              AND json_extract(ob.response,'$.schema_version')=2
              AND json_extract(ob.response,'$.batch_id')=b.batch_id
              AND json_type(ob.response,'$.evidence_catalog_sha256')='text'
              AND json_extract(ob.response,'$.evidence_catalog_sha256')=json_extract(ec.value,'$.sha256')),0)
              ELSE 0 END)),0) ELSE 0 END)"""

_manifest_fields = {**storage._manifest_fields, "evidence_version": "text", "evidence_catalogs": "array"}
_v3_snapshot = _once(_v3(storage._snapshot),
    _exact_object('NEW.manifest_json', storage._manifest_fields),
    _exact_object('NEW.manifest_json', _manifest_fields))
_receipt_count = "AND json_array_length(NEW.manifest_json,'$.receipts')=r.batch_count"
_v3_snapshot = _once(_v3_snapshot, _receipt_count, _receipt_count + "\n AND (" + _catalogs_valid + ")")
_snapshot = f"""(({previous._snapshot}) AND ({_v3_snapshot}))
    OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
      JOIN pdf_visual_authoring_run r ON r.id=s.run_id WHERE s.id=NEW.snapshot_id
        AND json_extract(NEW.manifest_json,'$.version')=json_extract(r.metadata_json,'$.policy_version')
        AND json_extract(NEW.manifest_json,'$.prompt_sha256')=json_extract(r.metadata_json,'$.prompt_sha256'))"""

_table = _once(previous._table,
    previous._pair('batch_policy_version', 'assembly_version', 'prompt_sha256'),
    _pair('batch_policy_version', 'assembly_version', 'prompt_sha256'))
V047_STATEMENTS = (
    *("DROP TRIGGER " + s.split()[2] for s in previous._request_triggers),
    "ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v46",
    _table,
    "INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v46",
    "DROP TABLE pdf_visual_workflow_request_v46",
    *previous._request_triggers,
    "DROP TRIGGER pdf_visual_run_insert",
    f"CREATE TRIGGER pdf_visual_run_insert BEFORE INSERT ON pdf_visual_authoring_run WHEN {_run} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_RUN_SOURCE_INVALID'); END",
    "DROP TRIGGER pdf_visual_snapshot_body_insert",
    f"CREATE TRIGGER pdf_visual_snapshot_body_insert BEFORE INSERT ON pdf_visual_wiki_snapshot_body WHEN {_snapshot} BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_SNAPSHOT_BODY_INVALID'); END",
    "DROP TRIGGER pdf_visual_workflow_policy_insert",
    f"""CREATE TRIGGER pdf_visual_workflow_policy_insert BEFORE INSERT ON pdf_visual_workflow_policy_body
        WHEN NOT coalesce((EXISTS(SELECT 1 FROM pdf_visual_workflow_request r
            JOIN publication_preview p ON p.id=r.preview_id JOIN source_artifact a ON a.id=p.artifact_id
            WHERE r.preview_id=NEW.preview_id AND a.body_retention_status='present'
              AND r.policy_size_bytes=length(CAST(NEW.policy_json AS BLOB))
              AND ({previous._policy_binding('NEW.policy_json', 'r')}))
            AND ({_policy_valid('NEW.policy_json')})),0)
        BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_WORKFLOW_POLICY_INVALID'); END""",
)

_old_snapshot_check = previous.V046_POSTCONDITION_REPLACEMENTS[0][1]
_old_policy_check = previous.V046_POSTCONDITION_REPLACEMENTS[1][1]
_pair_check = previous.V046_POSTCONDITION_QUERIES[0]
V047_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot_check,
     f"SELECT 'visual snapshot identity invalid' FROM pdf_visual_wiki_snapshot_body retained WHERE ({_snapshot.replace('NEW.', 'retained.')}) LIMIT 1"),
    (_old_policy_check, _once(_old_policy_check, previous._policy_valid('b.policy_json'), _policy_valid('b.policy_json'))),
    (_pair_check, _once(_pair_check, previous._pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256'),
        _pair('q.batch_policy_version', 'q.assembly_version', 'q.prompt_sha256'))),
)
V047_POSTCONDITION_QUERIES = (
    "SELECT 'PDF retained run writing identity invalid' FROM pdf_visual_authoring_run r WHERE NOT coalesce((" + _pair(
        "json_extract(r.metadata_json,'$.policy_version')", "json_extract(r.metadata_json,'$.assembly_version')",
        "json_extract(r.metadata_json,'$.prompt_sha256')") + "),0) LIMIT 1",
)
