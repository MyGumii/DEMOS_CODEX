"""Forward Markdown authoring; raw provider receipts and old guards stay intact.

Only the v8 semantic-shape predicates read an explicitly marked decoded document.
Raw response equality/hashes, image provenance, rights, lease and source fences
are unchanged. Python replay recomputes this pure decoder from the raw response.
"""
from . import v063_pdf_section_writing as base
from . import v066_pdf_section_schema_output as predecessor
from . import v046_pdf_visual_writing_policy as writing
from .pdf_section_schema_output_catalog import PDF_SECTION_SCHEMA_OUTPUT_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V8 = "wiki-pdf-section-writing-v8"
PROMPT_V8_SHA256 = "24b9c6ae6e436be48a80edc21f938429f6bd3a7fe1762636a3d215b7733a3fd9"
POLICY_IDENTITIES = (*predecessor.POLICY_IDENTITIES, (POLICY_V8, PROMPT_V8_SHA256))

def _pair(policy, assembly, prompt):
    return '(' + ' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES) + ')'

def _v8(sql):
    return sql.replace(base.POLICY_V5, POLICY_V8).replace(base.PROMPT_V5_SHA256, PROMPT_V8_SHA256)

def _branch(statement, identity, guard):
    before,rest = statement.split(' WHEN ',1)
    existing,after = rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V8}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"

_table = _once(predecessor._table,
    predecessor._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run = _branch(predecessor._run, "json_extract(NEW.metadata_json,'$.policy_version')", _v8(base._run_guard))
_decoded = "json_extract(ob.transport_json,'$.markdown_decode.document')"
_snapshot_guard = _v8(base._snapshot_guard).replace(base.fenced._RESPONSE_JSON, _decoded)
_snapshot_guard += """ OR EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id
    JOIN pdf_visual_authoring_outcome o ON o.run_id=r.id AND o.status='validated'
    JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
    WHERE s.id=NEW.snapshot_id AND NOT coalesce((
      json_type(ob.transport_json,'$.markdown_decode')='object'
      AND (SELECT count(*) FROM json_each(ob.transport_json,'$.markdown_decode'))=3
      AND json_extract(ob.transport_json,'$.markdown_decode.decoder_version')='pdf-section-markdown-v1'
      AND json_extract(ob.transport_json,'$.markdown_decode.raw_sha256')=o.response_sha256
      AND json_type(ob.transport_json,'$.markdown_decode.document')='object'),0))"""
_snapshot = _branch(predecessor._snapshot,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)
_batch = _branch(predecessor._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    base._new_batch_guard)
_old_versions = "IN ('wiki-pdf-section-writing-v5','wiki-pdf-section-writing-v6','wiki-pdf-section-writing-v7')"
_new_versions = _old_versions[:-1] + ",'wiki-pdf-section-writing-v8')"
_attempt = predecessor._attempt.replace(_old_versions,_new_versions)
_outcome = predecessor._outcome.replace(_old_versions,_new_versions)
_policy = _once(predecessor._policy,
    f" OR {predecessor._v7(base._v5(base._old_policy_valid))}",
    f" OR {predecessor._v7(base._v5(base._old_policy_valid))} OR {_v8(base._v5(base._old_policy_valid))}")
V067_STATEMENTS = (
    *('DROP TRIGGER '+s.split()[2] for s in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v66',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v66',
    'DROP TABLE pdf_visual_workflow_request_v66',*writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    'DROP TRIGGER pdf_visual_outcome_insert',_outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
)

def _active(version,index):
    return next(r.query for r in prior.resolved_postconditions if (r.origin_version,r.origin_index)==(version,index))

_old_snapshot = _active(43,18)
_new_snapshot = _old_snapshot.split(' WHERE (',1)[0] + ' WHERE (' + _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.') + ') LIMIT 1'
_old_policy = _active(44,5)
_p = base.evidence._policy_valid('b.policy_json')
_new_policy = _once(_old_policy, f" OR {predecessor._v7(base._v5(_p))}",
    f" OR {predecessor._v7(base._v5(_p))} OR {_v8(base._v5(_p))}")
_old_pair = _active(46,1)
_new_pair = _once(_old_pair,predecessor._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run = _active(47,1)
_fields = ("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run = _once(_old_run,predecessor._pair(*_fields),_pair(*_fields))
_old_pages = _active(43,7)
_new_pages = _old_pages.replace(_old_versions,_new_versions)
_old_absent = _active(63,1)
_new_absent = _old_absent.replace(_old_versions,_new_versions)
V067_POSTCONDITION_REPLACEMENTS = ((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
