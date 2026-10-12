"""Forward decoder-v2 admission: natural next-heading/EOF Markdown boundaries."""
from . import v067_pdf_section_markdown_output as previous
from . import v063_pdf_section_writing as base
from . import v046_pdf_visual_writing_policy as writing
from .pdf_section_markdown_output_catalog import PDF_SECTION_MARKDOWN_OUTPUT_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V9='wiki-pdf-section-writing-v9'
PROMPT_V9_SHA256='835249baead1cb36c4a82546d4ae978ca50df4b5e1b760663380dc8fd14102b8'
POLICY_IDENTITIES=(*previous.POLICY_IDENTITIES,(POLICY_V9,PROMPT_V9_SHA256))
def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')" for v,h in POLICY_IDENTITIES)+')'
def _v9(sql):
    return sql.replace(previous.POLICY_V8,POLICY_V9).replace(previous.PROMPT_V8_SHA256,PROMPT_V9_SHA256).replace('pdf-section-markdown-v1','pdf-section-markdown-v2')
def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1);existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V9}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"
_table=_once(previous._table,previous._pair('batch_policy_version','assembly_version','prompt_sha256'),_pair('batch_policy_version','assembly_version','prompt_sha256'))
_run=_branch(previous._run,"json_extract(NEW.metadata_json,'$.policy_version')",_v9(previous._v8(base._run_guard)))
_snapshot=_branch(previous._snapshot,"json_extract(NEW.manifest_json,'$.version')",_v9(previous._snapshot_guard))
_batch=_branch(previous._batch,"(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",base._new_batch_guard)
_old_versions=previous._new_versions
_new_versions=_old_versions[:-1]+",'wiki-pdf-section-writing-v9')"
_attempt=previous._attempt.replace(_old_versions,_new_versions)
_outcome=previous._outcome.replace(_old_versions,_new_versions)
_old_valid=previous._v8(base._v5(base._old_policy_valid))
_policy=_once(previous._policy,f' OR {_old_valid}',f' OR {_old_valid} OR {_v9(_old_valid)}')
V068_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v67',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v67',
    'DROP TABLE pdf_visual_workflow_request_v67',*writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    'DROP TRIGGER pdf_visual_outcome_insert',_outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
)
def _active(version,index):
    return next(r.query for r in prior.resolved_postconditions if (r.origin_version,r.origin_index)==(version,index))
_old_snapshot=_active(43,18)
_new_snapshot=_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+_snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1'
_old_policy=_active(44,5)
_p=previous._v8(base._v5(base.evidence._policy_valid('b.policy_json')))
_new_policy=_once(_old_policy,f' OR {_p}',f' OR {_p} OR {_v9(_p)}')
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),_pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_fields=("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,previous._pair(*_fields),_pair(*_fields))
_old_pages=_active(43,7);_new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1);_new_absent=_old_absent.replace(_old_versions,_new_versions)
V068_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),(_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
