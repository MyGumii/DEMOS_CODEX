"""Forward-only layout math display normalization; old V12 guards stay exact.

Raw provider bytes, transport, source, image and retention bindings remain.
The new decoder rejects unknown lone control escapes outside recognized math
before publication replay. SQL binds its normalized display text, not new facts.
"""
from . import v071_pdf_layout_retention as previous
from .pdf_layout_retention_catalog import PDF_LAYOUT_RETENTION_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V13='wiki-pdf-layout-retention-v2'
PROMPT_V13_SHA256=previous.PROMPT_V12_SHA256
POLICY_IDENTITIES=(*previous.POLICY_IDENTITIES,(POLICY_V13,PROMPT_V13_SHA256))


def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES)+')'


def _v13(sql):
    return (sql.replace(previous.POLICY_V12,POLICY_V13)
        .replace('pdf-layout-retention-v1','pdf-layout-retention-v2'))


def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1)
    existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V13}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


_table=_once(previous._table,previous._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run=_branch(previous._run,"json_extract(NEW.metadata_json,'$.policy_version')",
    _v13(previous._v12(previous._v11_run_guard)))
_normalized="replace(replace(json_extract(ob.response,'$.wiki_md'),char(13)||char(10),char(10)),char(13),char(92)||'r')"
_snapshot_guard=_v13(previous._snapshot_guard)
_snapshot_guard=_once(_snapshot_guard,
    "=replace(replace(json_extract(ob.response,'$.wiki_md'),'<','&lt;'),'>','&gt;')",
    f"=replace(replace({_normalized},'<','&lt;'),'>','&gt;')")
_snapshot_guard=_once(_snapshot_guard,
    "markdown_decode.document.markdown')=json_extract(ob.response,'$.wiki_md')",
    "markdown_decode.document.markdown')="+_normalized)
_snapshot=_branch(previous._snapshot,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)
_batch=_branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    previous.base._new_batch_guard)
_old_versions=previous._new_versions
_new_versions=_old_versions[:-1]+",'wiki-pdf-layout-retention-v2')"
_attempt=previous._attempt.replace(_old_versions,_new_versions)
_outcome=previous._outcome.replace(_old_versions,_new_versions)
_old_valid=previous._v12(previous._old_valid)
_policy=_once(previous._policy,f' OR {_old_valid}',f' OR {_old_valid} OR {_v13(_old_valid)}')
_workflow_run_family=_branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')",_v13(previous._forward_family_guard))
_prior_view_clause=(f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V12}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.','r.')}) "
    f"ELSE ({previous._view_configuration_clause}) END")
_registered_run=_once(previous._registered_run,_prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V13}' "
    f"THEN ({_v13(previous._layout_configuration_clause).replace('NEW.','r.')}) ELSE ({_prior_view_clause}) END")

V072_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in previous.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v71',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v71',
    'DROP TABLE pdf_visual_workflow_request_v71',*previous.writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    'DROP TRIGGER pdf_visual_outcome_insert',_outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
    'DROP TRIGGER pdf_visual_workflow_run_family',_workflow_run_family,
    'DROP VIEW pdf_visual_registered_run',_registered_run,
)


def _active(version,index):
    return next(r.query for r in prior.resolved_postconditions if (r.origin_version,r.origin_index)==(version,index))


_old_snapshot=_active(43,18)
_new_snapshot=(_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+
    _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1')
_old_policy=_active(44,5)
_p=previous._v12(previous._p)
_new_policy=_once(_old_policy,f' OR {_p}',f' OR {_p} OR {_v13(_p)}')
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_fields=("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,previous._pair(*_fields),_pair(*_fields))
_old_pages=_active(43,7);_new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1);_new_absent=_old_absent.replace(_old_versions,_new_versions)
V072_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
