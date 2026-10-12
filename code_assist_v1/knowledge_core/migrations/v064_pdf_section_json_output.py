"""Forward v6 section policy with constrained JSON and conservative vision budget."""
from . import v063_pdf_section_writing as old
from . import v046_pdf_visual_writing_policy as writing
from .pdf_section_writing_catalog import PDF_SECTION_WRITING_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V6 = "wiki-pdf-section-writing-v6"
PROMPT_V6_SHA256 = old.PROMPT_V5_SHA256
POLICY_IDENTITIES = (*old.POLICY_IDENTITIES,(POLICY_V6,PROMPT_V6_SHA256))

def _pair(policy,assembly,prompt):
    return "("+" OR ".join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')" for v,h in POLICY_IDENTITIES)+")"

def _v6(value): return value.replace(old.POLICY_V5,POLICY_V6)

_table = _once(old._table,old._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run = old._branch(old._run_insert,"json_extract(NEW.metadata_json,'$.policy_version')",_v6(old._run_guard))
_snapshot = old._branch(old._snapshot_insert,"json_extract(NEW.manifest_json,'$.version')",_v6(old._snapshot_guard))
_batch = old._branch(old._batch_insert,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",old._new_batch_guard)
_attempt = old._attempt_insert.replace(
    f"json_extract(r.metadata_json,'$.policy_version')='{old.POLICY_V5}'",
    f"json_extract(r.metadata_json,'$.policy_version') IN ('{old.POLICY_V5}','{POLICY_V6}')")
_outcome = old._outcome_insert.replace(
    f"json_extract(r.metadata_json,'$.policy_version')='{old.POLICY_V5}'",
    f"json_extract(r.metadata_json,'$.policy_version') IN ('{old.POLICY_V5}','{POLICY_V6}')")
_policy = _once(old._policy_insert,f" OR {old._v5(old._old_policy_valid)}",
    f" OR {old._v5(old._old_policy_valid)} OR {_v6(old._v5(old._old_policy_valid))}")
V064_STATEMENTS = (
    *('DROP TRIGGER '+value.split()[2] for value in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v63',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v63',
    'DROP TABLE pdf_visual_workflow_request_v63',*writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    _batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    _outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
)

def _active(version,index):
    return next(x.query for x in prior.resolved_postconditions if (x.origin_version,x.origin_index)==(version,index))

_snapshot_query = _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')
_old_snapshot_query = _active(43,18)
_new_snapshot_query = _old_snapshot_query.split(' WHERE (',1)[0]+' WHERE ('+_snapshot_query+') LIMIT 1'
_old_policy_query = _active(44,5)
_new_policy_query = _once(_old_policy_query,f" OR {old._v5(old.evidence._policy_valid('b.policy_json'))}",
    f" OR {old._v5(old.evidence._policy_valid('b.policy_json'))} OR {_v6(old._v5(old.evidence._policy_valid('b.policy_json')))}")
_old_pair_query = _active(46,1)
_new_pair_query = _once(_old_pair_query,old._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run_query = _active(47,1)
_new_run_query = _once(_old_run_query,old._pair("json_extract(r.metadata_json,'$.policy_version')",
    "json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')"),
    _pair("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')"))
_old_pages = _active(43,7)
_new_pages = _old_pages.replace(f"json_extract(r.metadata_json,'$.policy_version')<>'{old.POLICY_V5}'",
    f"json_extract(r.metadata_json,'$.policy_version') NOT IN ('{old.POLICY_V5}','{POLICY_V6}')")
_old_absent = _active(63,1)
_new_absent = _old_absent.replace(f"json_extract(r.metadata_json,'$.policy_version')='{old.POLICY_V5}'",
    f"json_extract(r.metadata_json,'$.policy_version') IN ('{old.POLICY_V5}','{POLICY_V6}')")
V064_POSTCONDITION_REPLACEMENTS = (
    (_old_snapshot_query,_new_snapshot_query),(_old_policy_query,_new_policy_query),
    (_old_pair_query,_new_pair_query),(_old_run_query,_new_run_query),
    (_old_pages,_new_pages),(_old_absent,_new_absent),
)
