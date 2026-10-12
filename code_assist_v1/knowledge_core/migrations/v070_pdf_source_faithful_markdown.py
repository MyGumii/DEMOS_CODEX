"""Forward source-faithful input/decoder identity, without changing v69.

The candidate still declares no semantic proof. Actor/source/lease, raw-response,
image transport and immutable snapshot bindings are inherited unchanged. This
module alone does not activate the policy or migrate a live database.
"""
from . import v069_pdf_batch_markdown as previous
from . import v063_pdf_section_writing as base
from . import v046_pdf_visual_writing_policy as writing
from .pdf_batch_markdown_catalog import PDF_BATCH_MARKDOWN_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V11='wiki-pdf-section-writing-v11'
PROMPT_V11_SHA256='6137e3756d917e67354e38800bead74faa38ba477c599e8dde299d285c99ade3'
POLICY_IDENTITIES=(*previous.POLICY_IDENTITIES,(POLICY_V11,PROMPT_V11_SHA256))


def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES)+')'


def _v11(sql):
    return (sql.replace(previous.POLICY_V10,POLICY_V11)
        .replace(previous.PROMPT_V10_SHA256,PROMPT_V11_SHA256)
        .replace('pdf-batch-markdown-v1','pdf-batch-markdown-v2'))


def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1)
    existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V11}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


_table=_once(previous._table,previous._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_v10_run_guard=previous._v10(previous.previous._v9(previous.markdown._v8(base._run_guard)))
_run=_branch(previous._run,"json_extract(NEW.metadata_json,'$.policy_version')",_v11(_v10_run_guard))
_snapshot_guard=_v11(previous._snapshot_guard)
_snapshot=_branch(previous._snapshot,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)
_batch=_branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    base._new_batch_guard)
_old_versions=previous._new_versions
_new_versions=_old_versions[:-1]+",'wiki-pdf-section-writing-v11')"
_attempt=previous._attempt.replace(_old_versions,_new_versions)
_outcome=previous._outcome.replace(_old_versions,_new_versions)
_old_valid=previous._v10(previous._old_valid)
_policy=_once(previous._policy,f' OR {_old_valid}',f' OR {_old_valid} OR {_v11(_old_valid)}')

V070_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v69',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v69',
    'DROP TABLE pdf_visual_workflow_request_v69',*writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    'DROP TRIGGER pdf_visual_outcome_insert',_outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
)


def _active(version,index):
    return next(r.query for r in prior.resolved_postconditions
        if (r.origin_version,r.origin_index)==(version,index))


_old_snapshot=_active(43,18)
_new_snapshot=(_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+
    _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1')
_old_policy=_active(44,5)
_p=previous._v10(previous._p)
_new_policy=_once(_old_policy,f' OR {_p}',f' OR {_p} OR {_v11(_p)}')
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_fields=("json_extract(r.metadata_json,'$.policy_version')",
    "json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,previous._pair(*_fields),_pair(*_fields))
_old_pages=_active(43,7)
_new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1)
_new_absent=_old_absent.replace(_old_versions,_new_versions)
V070_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
