"""One forward V10 prose + literal-layout-table composition; no old guard edits.

Raw prose responses, page pixels, original slots and all authority checks stay
bound. The new manifest declares literal layout provenance, not visual or
semantic accuracy. Importing this policy does not activate or migrate a DB.
"""
from . import v091_pdf_native_tick_conditions as previous
from . import v069_pdf_batch_markdown as batch
from . import v071_pdf_layout_retention as layout
from .pdf_native_tick_conditions_catalog import PDF_NATIVE_TICK_CONDITIONS_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from .v014_wiki_authoring import _sha

POLICY_V33 = 'wiki-pdf-v10-literal-tables-v1'
PROMPT_V33_SHA256 = batch.PROMPT_V10_SHA256
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES,(POLICY_V33,PROMPT_V33_SHA256))


def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES)+')'


def _v33(sql):
    return sql.replace(batch.POLICY_V10,POLICY_V33)


def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1); existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V33}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


_table=_once(previous._table,previous._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run_guard=_v33(batch._v10(batch.previous._v9(batch.markdown._v8(batch.base._run_guard))))
_run=_branch(previous._run,"json_extract(NEW.metadata_json,'$.policy_version')",_run_guard)
_manifest_fields={**batch._manifest_fields,'literal_tables':'object'}
_snapshot_guard=_once(_v33(batch._snapshot_guard),
    _exact_object('NEW.manifest_json',batch._manifest_fields),
    _exact_object('NEW.manifest_json',_manifest_fields))
_details="json_extract(NEW.manifest_json,'$.literal_tables')"
_fields={'version':'text','source_identity':'object','table_count':'integer','cell_count':'integer',
    'tables':'array','output_byte_range':'array','rendered_sha256':'text'}
_snapshot_guard+=f""" OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id
    JOIN pdf_visual_source_body ab ON ab.source_id=r.source_archive_id WHERE s.id=NEW.snapshot_id
      AND ({_exact_object(_details,_fields)})
      AND json_extract({_details},'$.version')='pdf-layout-literal-table-supplement-v1'
      AND json(json_extract({_details},'$.source_identity'))=
        json(json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.layout_identity'))
      AND json(json_extract({_details},'$.source_identity.source'))=json(json_extract(ab.bundle_json,'$.source'))
      AND json_type({_details},'$.source_identity.native_figures') IS NULL
      AND ({_sha(f"json_extract({_details},'$.source_identity.catalog_sha256')")})
      AND ({_sha(f"json_extract({_details},'$.rendered_sha256')")})
      AND json_extract({_details},'$.table_count') BETWEEN 0 AND 4096
      AND json_extract({_details},'$.cell_count') BETWEEN 0 AND 200000
      AND json_extract({_details},'$.table_count')=json_array_length({_details},'$.tables')
      AND json_extract({_details},'$.cell_count')=(SELECT count(*)
        FROM json_each({_details},'$.tables') t,json_each(t.value,'$.rows') row,json_each(row.value,'$.cells'))
      AND (SELECT count(DISTINCT json_extract(value,'$.unit_id')) FROM json_each({_details},'$.tables'))=
        json_extract({_details},'$.table_count')
      AND NOT EXISTS(SELECT 1 FROM json_each({_details},'$.tables') t
        WHERE NOT coalesce((t.type='object' AND json_extract(t.value,'$.kind')='table'
          AND json_extract(t.value,'$.status')='extracted' AND json_type(t.value,'$.rows')='array'
          AND json_array_length(t.value,'$.rows')>0),0))
      AND json_array_length({_details},'$.output_byte_range')=2
      AND json_type({_details},'$.output_byte_range[0]')='integer'
      AND json_type({_details},'$.output_byte_range[1]')='integer'
      AND json_extract({_details},'$.output_byte_range[0]')>=0
      AND json_extract({_details},'$.output_byte_range[1]')=s.content_size_bytes
      AND CASE WHEN json_extract({_details},'$.table_count')=0
        THEN json_extract({_details},'$.output_byte_range[0]')=s.content_size_bytes
        ELSE json_extract({_details},'$.output_byte_range[1]')>json_extract({_details},'$.output_byte_range[0]') END)"""
_snapshot=_branch(previous._snapshot,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)
_batch=_branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    batch.base._new_batch_guard)
_old_versions=previous._new_versions
_new_versions=_old_versions[:-1]+",'wiki-pdf-v10-literal-tables-v1')"
_attempt=previous._attempt.replace(_old_versions,_new_versions)
_outcome=previous._outcome.replace(_old_versions,_new_versions)
_old_valid=previous._v32(previous._old_valid)
_policy=_once(previous._policy,f' OR {_old_valid}',f' OR {_old_valid} OR {_v33(batch._v10(batch._old_valid))}')
_layout_configuration_clause=layout._layout_configuration_clause
_family_guard=layout._forward_family_guard
_workflow_run_family=_branch(previous._workflow_run_family,"json_extract(NEW.metadata_json,'$.policy_version')",_family_guard)
_prior_view_clause=(f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V32}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.','r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run=_once(previous._registered_run,_prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V33}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.','r.')}) ELSE ({_prior_view_clause}) END")

V092_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in layout.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v91',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v91',
    'DROP TABLE pdf_visual_workflow_request_v91',*layout.writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert',_run,
    'DROP TRIGGER pdf_visual_snapshot_body_insert',_snapshot,
    'DROP TRIGGER pdf_visual_batch_insert',_batch,
    'DROP TRIGGER pdf_visual_attempt_insert',_attempt,
    'DROP TRIGGER pdf_visual_outcome_insert',_outcome,
    'DROP TRIGGER pdf_visual_workflow_policy_insert',_policy,
    'DROP TRIGGER pdf_visual_workflow_run_family',_workflow_run_family,
    'DROP VIEW pdf_visual_registered_run',_registered_run)


def _active(version,index):
    return next(row.query for row in prior.resolved_postconditions
        if (row.origin_version,row.origin_index)==(version,index))


_old_snapshot=_active(43,18)
_new_snapshot=(_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+
    _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1')
_old_policy=_active(44,5)
_new_policy=_once(_old_policy,f' OR {previous._v32(previous._p)}',
    f' OR {previous._v32(previous._p)} OR {_v33(batch._v10(batch._p))}')
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_run_fields=("json_extract(r.metadata_json,'$.policy_version')", "json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,previous._pair(*_run_fields),_pair(*_run_fields))
_old_pages=_active(43,7); _new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1); _new_absent=_old_absent.replace(_old_versions,_new_versions)
V092_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
