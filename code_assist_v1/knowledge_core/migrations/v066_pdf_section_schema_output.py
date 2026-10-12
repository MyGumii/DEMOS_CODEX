"""Admit v7 per-input output schema; authority and evidence resolvers are unchanged."""
from . import v063_pdf_section_writing as base
from . import v064_pdf_section_json_output as prior_policy
from . import v065_pdf_section_guard_dispatch as dispatch
from . import v046_pdf_visual_writing_policy as writing
from .pdf_section_guard_dispatch_catalog import PDF_SECTION_GUARD_DISPATCH_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V7='wiki-pdf-section-writing-v7'
PROMPT_V7_SHA256=base.PROMPT_V5_SHA256
POLICY_IDENTITIES=(*prior_policy.POLICY_IDENTITIES,(POLICY_V7,PROMPT_V7_SHA256))
def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')" for v,h in POLICY_IDENTITIES)+')'
def _v7(sql):return sql.replace(base.POLICY_V5,POLICY_V7)
def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1);existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V7}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"
_table=_once(prior_policy._table,prior_policy._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run=_branch(dispatch._run,"json_extract(NEW.metadata_json,'$.policy_version')",_v7(base._run_guard))
_snapshot=_branch(dispatch._snapshot,"json_extract(NEW.manifest_json,'$.version')",_v7(base._snapshot_guard))
_batch=_branch(dispatch._batch,"(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",base._new_batch_guard)
_old_versions=f"IN ('{base.POLICY_V5}','{prior_policy.POLICY_V6}')"
_new_versions=f"IN ('{base.POLICY_V5}','{prior_policy.POLICY_V6}','{POLICY_V7}')"
_attempt=prior_policy._attempt.replace(_old_versions,_new_versions)
_outcome=prior_policy._outcome.replace(_old_versions,_new_versions)
_policy=_once(prior_policy._policy,f" OR {prior_policy._v6(base._v5(base._old_policy_valid))}",
    f" OR {prior_policy._v6(base._v5(base._old_policy_valid))} OR {_v7(base._v5(base._old_policy_valid))}")
V066_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v65',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v65',
    'DROP TABLE pdf_visual_workflow_request_v65',*writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    'DROP TRIGGER pdf_visual_outcome_insert',_outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
)
def _active(version,index):return next(r.query for r in prior.resolved_postconditions if (r.origin_version,r.origin_index)==(version,index))
_old_snapshot=_active(43,18)
_new_snapshot=_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+_snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1'
_old_policy=_active(44,5)
_p=base.evidence._policy_valid('b.policy_json')
_new_policy=_once(_old_policy,f" OR {prior_policy._v6(base._v5(_p))}",f" OR {prior_policy._v6(base._v5(_p))} OR {_v7(base._v5(_p))}")
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,prior_policy._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),_pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_fields=("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,prior_policy._pair(*_fields),_pair(*_fields))
_old_pages=_active(43,7);_new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1);_new_absent=_old_absent.replace(_old_versions,_new_versions)
V066_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
