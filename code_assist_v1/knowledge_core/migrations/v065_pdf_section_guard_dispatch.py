"""Correct the explicit v6 guard dispatch, preserving v1-v5 guards exactly."""
from . import v063_pdf_section_writing as old
from . import v064_pdf_section_json_output as previous
from .pdf_section_json_output_catalog import PDF_SECTION_JSON_OUTPUT_MIGRATION_CATALOG as prior

def _branch(base,identity,guard):
    before,rest=base.split(' WHEN ',1)
    existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{previous.POLICY_V6}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"

_run = _branch(old._run_insert,"json_extract(NEW.metadata_json,'$.policy_version')",previous._v6(old._run_guard))
_snapshot = _branch(old._snapshot_insert,"json_extract(NEW.manifest_json,'$.version')",previous._v6(old._snapshot_guard))
_batch = _branch(old._batch_insert,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",old._new_batch_guard)
V065_STATEMENTS=(
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
)
_old_query=next(r.query for r in prior.resolved_postconditions if (r.origin_version,r.origin_index)==(43,18))
_new_query=_old_query.split(' WHERE (',1)[0]+' WHERE ('+_snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1'
V065_POSTCONDITION_REPLACEMENTS=((_old_query,_new_query),)
