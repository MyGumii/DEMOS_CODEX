"""Forward natural prose policy; input provenance is not claim-level evidence.

Historical policies keep their exact guards. This lane explicitly declares no
claim-completeness or visual acknowledgement proof, while retaining all actor,
source, lease, raw response, image transport and immutable snapshot bindings.
"""
from . import v068_pdf_section_markdown_delimiters as previous
from . import v067_pdf_section_markdown_output as markdown
from . import v063_pdf_section_writing as base
from . import v046_pdf_visual_writing_policy as writing
from .pdf_section_markdown_delimiters_catalog import PDF_SECTION_MARKDOWN_DELIMITERS_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once

POLICY_V10 = 'wiki-pdf-section-writing-v10'
PROMPT_V10_SHA256 = '4e5f3cadda01fcc8a313d4336abc97a9aabd2328c7c368fc8420bbe56c4f8b6c'
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES,(POLICY_V10,PROMPT_V10_SHA256))

def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')" for v,h in POLICY_IDENTITIES)+')'

def _v10(sql):
    return sql.replace(previous.POLICY_V9,POLICY_V10).replace(previous.PROMPT_V9_SHA256,PROMPT_V10_SHA256).replace('pdf-section-markdown-v2','pdf-batch-markdown-v1')

def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1);existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V10}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"

_table=_once(previous._table,previous._pair('batch_policy_version','assembly_version','prompt_sha256'),_pair('batch_policy_version','assembly_version','prompt_sha256'))
_run=_branch(previous._run,"json_extract(NEW.metadata_json,'$.policy_version')",_v10(previous._v9(markdown._v8(base._run_guard))))
_manifest_fields={**base.evidence._manifest_fields,'all_nonblank_ranges_authored':'false',
    'all_visual_tasks_returned':'false','all_nonblank_ranges_delivered':'true',
    'provenance_level':'text','batch_prose':'array'}
_snapshot_guard=_v10(previous._v9(markdown._snapshot_guard))
_snapshot_guard=_once(_snapshot_guard,
    base.evidence._exact_object('NEW.manifest_json',base.evidence._manifest_fields),
    base.evidence._exact_object('NEW.manifest_json',_manifest_fields))
_snapshot_guard += """ OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id WHERE s.id=NEW.snapshot_id
      AND json_extract(NEW.manifest_json,'$.provenance_level')='batch_input_context'
      AND json_array_length(NEW.manifest_json,'$.batch_prose')=r.batch_count
      AND NOT EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.batch_prose') prose
        WHERE NOT EXISTS(SELECT 1 FROM pdf_visual_authoring_batch b
          JOIN pdf_visual_authoring_outcome o ON o.run_id=b.run_id AND o.ordinal=b.ordinal AND o.status='validated'
          JOIN pdf_visual_authoring_outcome_body ob ON ob.attempt_id=o.attempt_id
          WHERE b.run_id=r.id AND b.ordinal=prose.key+1
            AND json_extract(prose.value,'$.batch_id')=b.batch_id
            AND json_extract(prose.value,'$.response_sha256')=o.response_sha256
            AND json(json_extract(prose.value,'$.source_chunk_ids'))=json(b.members_json)
            AND json_type(prose.value,'$.source_pages')='array'
            AND json_type(prose.value,'$.output_byte_range')='array'
            AND json_array_length(prose.value,'$.output_byte_range')=2
            AND json_type(prose.value,'$.output_byte_range[0]')='integer'
            AND json_type(prose.value,'$.output_byte_range[1]')='integer'
            AND json_extract(prose.value,'$.output_byte_range[0]')>=0
            AND json_extract(prose.value,'$.output_byte_range[1]')>json_extract(prose.value,'$.output_byte_range[0]')
            AND json_extract(prose.value,'$.output_byte_range[1]')<=s.content_size_bytes
            AND CAST(substr(CAST(NEW.content_md AS BLOB),
                json_extract(prose.value,'$.output_byte_range[0]')+1,
                json_extract(prose.value,'$.output_byte_range[1]')-json_extract(prose.value,'$.output_byte_range[0]')) AS TEXT)
                =replace(replace(ob.response,'<','&lt;'),'>','&gt;')
            AND json_extract(ob.transport_json,'$.markdown_decode.document.provenance_level')='batch_input_context'
            AND json_extract(ob.transport_json,'$.markdown_decode.document.markdown')=ob.response)))"""
_snapshot=_branch(previous._snapshot,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)
_batch=_branch(previous._batch,"(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",base._new_batch_guard)
_old_versions=previous._new_versions
_new_versions=_old_versions[:-1]+",'wiki-pdf-section-writing-v10')"
_attempt=previous._attempt.replace(_old_versions,_new_versions)
_outcome=previous._outcome.replace(_old_versions,_new_versions)
_old_valid=previous._v9(markdown._v8(base._v5(base._old_policy_valid)))
_policy=_once(previous._policy,f' OR {_old_valid}',f' OR {_old_valid} OR {_v10(_old_valid)}')
V069_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v68',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v68',
    'DROP TABLE pdf_visual_workflow_request_v68',*writing._request_triggers,
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
_p=previous._v9(markdown._v8(base._v5(base.evidence._policy_valid('b.policy_json'))))
_new_policy=_once(_old_policy,f' OR {_p}',f' OR {_p} OR {_v10(_p)}')
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),_pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_fields=("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')","json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,previous._pair(*_fields),_pair(*_fields))
_old_pages=_active(43,7);_new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1);_new_absent=_old_absent.replace(_old_versions,_new_versions)
V069_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),(_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
