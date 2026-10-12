"""Forward layout retention: frozen v10/v11 fallback guards remain exact.

SQL binds immutable source, raw provider records, range shapes and inventory
identities. Actual cell rendering/figure compilation and SHA256 checks are
replayed from the same leased original outside writer transactions. This is
not a claim that a model's visual reading is semantically correct.
"""
from . import v070_pdf_source_faithful_markdown as previous
from . import v069_pdf_batch_markdown as batch
from . import v063_pdf_section_writing as base
from . import v046_pdf_visual_writing_policy as writing
from . import v051_protected_pdf_visual_binding as protected
from .pdf_source_faithful_markdown_catalog import PDF_SOURCE_FAITHFUL_MARKDOWN_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from .v014_wiki_authoring import _sha

POLICY_V12='wiki-pdf-layout-retention-v1'
PROMPT_V12_SHA256='ff4f7f51e3988b968e76a013d099d4c176a0a6f1f4278d179a38fd2e28d3132b'
POLICY_IDENTITIES=(*previous.POLICY_IDENTITIES,(POLICY_V12,PROMPT_V12_SHA256))


def _pair(policy,assembly,prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES)+')'


def _v12(sql):
    return (sql.replace(previous.POLICY_V11,POLICY_V12)
        .replace(previous.PROMPT_V11_SHA256,PROMPT_V12_SHA256)
        .replace('pdf-batch-markdown-v2','pdf-layout-retention-v1'))


def _branch(statement,identity,guard):
    before,rest=statement.split(' WHEN ',1)
    existing,after=rest.rsplit(' BEGIN ',1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V12}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


def _range(value,snapshot='s'):
    return f"""json_type({value})='array' AND json_array_length({value})=2
      AND json_type({value},'$[0]')='integer' AND json_type({value},'$[1]')='integer'
      AND json_extract({value},'$[0]')>=0
      AND json_extract({value},'$[1]')>json_extract({value},'$[0]')
      AND json_extract({value},'$[1]')<={snapshot}.content_size_bytes"""


_table=_once(previous._table,previous._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_v11_run_guard=previous._v11(previous._v10_run_guard)
_run=_branch(previous._run,"json_extract(NEW.metadata_json,'$.policy_version')",_v12(_v11_run_guard))
_manifest_fields={**batch._manifest_fields,'layout_details':'object'}
_snapshot_guard=_v12(previous._snapshot_guard)
_snapshot_guard=_once(_snapshot_guard,_exact_object('NEW.manifest_json',batch._manifest_fields),
    _exact_object('NEW.manifest_json',_manifest_fields))
# The raw response remains JSON. Prose provenance attaches its exact decoded
# wiki_md, not the full JSON or a fabricated per-claim quote.
_snapshot_guard=_once(_snapshot_guard,"=replace(replace(ob.response,'<','&lt;'),'>','&gt;')",
    "=replace(replace(json_extract(ob.response,'$.wiki_md'),'<','&lt;'),'>','&gt;')")
_snapshot_guard=_once(_snapshot_guard,"markdown_decode.document.markdown')=ob.response",
    "markdown_decode.document.markdown')=json_extract(ob.response,'$.wiki_md')")
_snapshot_guard=_once(_snapshot_guard,
    "json_extract(prose.value,'$.output_byte_range[1]')>json_extract(prose.value,'$.output_byte_range[0]')",
    "(json_extract(prose.value,'$.output_byte_range[1]')>json_extract(prose.value,'$.output_byte_range[0]') OR "
    "(json_extract(prose.value,'$.output_byte_range[1]')=json_extract(prose.value,'$.output_byte_range[0]') "
    "AND json_array_length(b.members_json)=0 AND json_extract(ob.response,'$.wiki_md')=''))")

_old_visual=base._new_visual_check
_new_visual="""
          WHERE NOT coalesce((json_type(v.value,'$.page')='integer'
            AND json_extract(v.value,'$.page')=v.key+1
            AND json(json_extract(v.value,'$.image'))=json(json_extract(ab.bundle_json,'$.source.pages['||v.key||']'))
            AND json_extract(v.value,'$.analysis') IN ('text_sufficient','model_reading')
            AND json_type(v.value,'$.output_byte_ranges')='array'
            AND NOT EXISTS(SELECT 1 FROM json_each(v.value,'$.output_byte_ranges') output
              WHERE NOT coalesce((json_type(output.value)='array' AND json_array_length(output.value)=2
                AND json_type(output.value,'$[0]')='integer' AND json_type(output.value,'$[1]')='integer'
                AND json_extract(output.value,'$[0]')>=0
                AND json_extract(output.value,'$[1]')>=json_extract(output.value,'$[0]')
                AND json_extract(output.value,'$[1]')<=s.content_size_bytes),0))),0))
"""
_snapshot_guard=_once(_snapshot_guard,_old_visual,_new_visual)
_details="json_extract(NEW.manifest_json,'$.layout_details')"
_table_fields={'unit_id':'text','page':'integer','table_sha256':'text','cells':'integer','output_byte_range':'array'}
_figure_fields={'unit_id':'text','page':'integer','image_source_id':'text','image_sha256':'text','output_byte_ranges':'array',
    'analysis':'text','accuracy_verified':'false'}
_view_fields={'view_id':'text','image_source_id':'text','image_sha256':'text',
    'context_source_id':'text','context_sha256':'text','output_byte_ranges':'array'}
_view_check=f"""(json_type(f.value,'$.views') IS NULL OR (
      json_array_length(f.value,'$.views') BETWEEN 1 AND 4096
      AND (SELECT count(DISTINCT json_extract(value,'$.view_id')) FROM json_each(f.value,'$.views'))=
        json_array_length(f.value,'$.views')
      AND json_extract(f.value,'$.image_source_id')=json_extract(f.value,'$.views[0].image_source_id')
      AND json_extract(f.value,'$.image_sha256')=json_extract(f.value,'$.views[0].image_sha256')
      AND NOT EXISTS(SELECT 1 FROM json_each(f.value,'$.views') view
        WHERE NOT CASE WHEN view.type='object' THEN coalesce((
          ({_exact_object('view.value',_view_fields)})
          AND length(json_extract(view.value,'$.view_id')) BETWEEN 1 AND 160
          AND length(json_extract(view.value,'$.image_source_id')) BETWEEN 1 AND 160
          AND length(json_extract(view.value,'$.context_source_id')) BETWEEN 1 AND 160
          AND ({_sha("json_extract(view.value,'$.image_sha256')")})
          AND ({_sha("json_extract(view.value,'$.context_sha256')")})
          AND json_array_length(view.value,'$.output_byte_ranges') BETWEEN 1 AND 4096
          AND NOT EXISTS(SELECT 1 FROM json_each(view.value,'$.output_byte_ranges') output
            WHERE NOT coalesce(({_range('output.value')}),0)
              OR NOT EXISTS(SELECT 1 FROM json_each(f.value,'$.output_byte_ranges') parent
                WHERE json(parent.value)=json(output.value)))
          AND EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome outcome
            JOIN pdf_visual_authoring_outcome_body body ON body.attempt_id=outcome.attempt_id
            WHERE outcome.run_id=r.id AND outcome.status='validated'
              AND EXISTS(SELECT 1 FROM json_each(body.transport_json,'$.image_input_transport.images') target
                WHERE json_extract(target.value,'$.source_id')=json_extract(view.value,'$.image_source_id')
                  AND json_extract(target.value,'$.sha256')=json_extract(view.value,'$.image_sha256'))
              AND EXISTS(SELECT 1 FROM json_each(body.transport_json,'$.image_input_transport.images') context
                WHERE json_extract(context.value,'$.source_id')=json_extract(view.value,'$.context_source_id')
                  AND json_extract(context.value,'$.sha256')=json_extract(view.value,'$.context_sha256')))),0) ELSE 0 END)))"""
_snapshot_guard+=f""" OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id
    JOIN pdf_visual_source a ON a.id=r.source_archive_id
    JOIN pdf_visual_source_body ab ON ab.source_id=a.id WHERE s.id=NEW.snapshot_id
      AND ({_exact_object(_details,{'version':'text','source_identity':'object','tables':'array','figures':'array'})})
      AND json_extract({_details},'$.version')='pdf-layout-retention-ledger-v1'
      AND json(json_extract({_details},'$.source_identity'))=
        json(json_extract(json_extract(ab.bundle_json,'$.configuration_json'),'$.layout_identity'))
      AND json(json_extract({_details},'$.source_identity.source'))=json(json_extract(ab.bundle_json,'$.source'))
      AND ({_sha(f"json_extract({_details},'$.source_identity.catalog_sha256')")})
      AND json_array_length({_details},'$.tables') BETWEEN 0 AND 4096
      AND json_array_length({_details},'$.figures') BETWEEN 0 AND 4096
      AND json_array_length({_details},'$.figures')=json_array_length({_details},'$.source_identity.crops')
      AND (SELECT count(DISTINCT json_extract(value,'$.unit_id')) FROM json_each({_details},'$.tables'))=
        json_array_length({_details},'$.tables')
      AND (SELECT count(DISTINCT json_extract(value,'$.unit_id')) FROM json_each({_details},'$.figures'))=
        json_array_length({_details},'$.figures')
      AND (SELECT count(DISTINCT json_extract(view.value,'$.view_id'))
        FROM json_each({_details},'$.figures') figure,json_each(figure.value,'$.views') view)=
        (SELECT count(*) FROM json_each({_details},'$.figures') figure,json_each(figure.value,'$.views') view)
      AND NOT EXISTS(SELECT 1 FROM json_each({_details},'$.tables') t
        WHERE NOT CASE WHEN t.type='object' THEN coalesce((
          ({_exact_object('t.value',_table_fields)}) AND length(json_extract(t.value,'$.unit_id')) BETWEEN 1 AND 160
          AND json_extract(t.value,'$.page') BETWEEN 1 AND a.page_count
          AND json_extract(t.value,'$.cells') BETWEEN 1 AND 200000
          AND ({_sha("json_extract(t.value,'$.table_sha256')")})
          AND ({_range("json_extract(t.value,'$.output_byte_range')")})),0) ELSE 0 END)
      AND NOT EXISTS(SELECT 1 FROM json_each({_details},'$.figures') f
        WHERE NOT CASE WHEN f.type='object' THEN coalesce((
          (({_exact_object('f.value',_figure_fields)}) OR
            ({_exact_object('f.value',{**_figure_fields,'views':'array'})}))
          AND length(json_extract(f.value,'$.unit_id')) BETWEEN 1 AND 160
          AND json_extract(f.value,'$.page') BETWEEN 1 AND a.page_count
          AND ({_sha("json_extract(f.value,'$.image_sha256')")})
          AND json_extract(f.value,'$.analysis')='unreviewed_model_reading'
          AND json_array_length(f.value,'$.output_byte_ranges') BETWEEN 1 AND 4096
          AND NOT EXISTS(SELECT 1 FROM json_each(f.value,'$.output_byte_ranges') output
            WHERE NOT coalesce(({_range('output.value')}),0))
          AND EXISTS(SELECT 1 FROM json_each({_details},'$.source_identity.crops') crop
            WHERE json_extract(crop.value,'$.source_id')=json_extract(f.value,'$.unit_id'))
          AND {_view_check}
          AND EXISTS(SELECT 1 FROM pdf_visual_authoring_outcome outcome
            JOIN pdf_visual_authoring_outcome_body body ON body.attempt_id=outcome.attempt_id,
            json_each(body.transport_json,'$.image_input_transport.images') input
            WHERE outcome.run_id=r.id AND outcome.status='validated'
              AND json_extract(input.value,'$.source_id')=json_extract(f.value,'$.image_source_id')
              AND json_extract(input.value,'$.sha256')=json_extract(f.value,'$.image_sha256'))),0) ELSE 0 END))"""
_snapshot=_branch(previous._snapshot,"json_extract(NEW.manifest_json,'$.version')",_snapshot_guard)
_batch=_branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    base._new_batch_guard)
_old_versions=previous._new_versions
_new_versions=_old_versions[:-1]+",'wiki-pdf-layout-retention-v1')"
_attempt=previous._attempt.replace(_old_versions,_new_versions)
_outcome=previous._outcome.replace(_old_versions,_new_versions)
_old_valid=previous._v11(previous._old_valid)
_policy=_once(previous._policy,f' OR {_old_valid}',f' OR {_old_valid} OR {_v12(_old_valid)}')
# V12 preparation adds an original-bound layout identity AFTER the frozen
# settings request. Bind the remaining config to that exact retained settings
# object rather than pretending its full hash stayed the same. The historical
# ordinary/protected branches remain exact outside this one forward lane.
_config="json_extract(body.bundle_json,'$.configuration_json')"
_layout_configuration_clause=f"""EXISTS(SELECT 1 FROM pdf_visual_source archive
      JOIN pdf_visual_source_body body ON body.source_id=archive.id
      JOIN pdf_visual_workflow_policy_body policy ON policy.preview_id=archive.preview_id
      WHERE archive.id=NEW.source_archive_id AND archive.preview_id=q.preview_id
        AND json_type({_config},'$.layout_identity')='object'
        AND json(json_extract({_config},'$.layout_identity.source'))=json(json_extract(body.bundle_json,'$.source'))
        AND json(json_remove({_config},'$.layout_identity','$.protected_binding'))=
          json(json_extract(policy.policy_json,'$.configuration'))
        AND json_extract(NEW.metadata_json,'$.configuration_size_bytes')=length(CAST({_config} AS BLOB))
        AND CASE WHEN json_type({_config},'$.protected_binding') IS NULL THEN 1
          WHEN json_type({_config},'$.protected_binding')='object'
          THEN ({protected._protected_configuration_clause}) ELSE 0 END)"""
_historical_configuration_clause=(f"({protected._configuration_clause} OR "
    f"{protected._protected_configuration_clause})")
_old_family=protected._workflow_run_family
_forward_family_guard=_once(_old_family.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0],
    _historical_configuration_clause,_layout_configuration_clause)
_workflow_run_family=_branch(_old_family,"json_extract(NEW.metadata_json,'$.policy_version')",
    _forward_family_guard)
_old_view=protected._registered_run
_view_configuration_clause=_historical_configuration_clause.replace('NEW.','r.')
_registered_run=_once(_old_view,_view_configuration_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V12}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.','r.')}) ELSE ({_view_configuration_clause}) END")
V071_STATEMENTS=(
    *('DROP TRIGGER '+s.split()[2] for s in writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v70',
    _table,'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v70',
    'DROP TABLE pdf_visual_workflow_request_v70',*writing._request_triggers,
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
_p=previous._v11(previous._p)
_new_policy=_once(_old_policy,f' OR {_p}',f' OR {_p} OR {_v12(_p)}')
_old_pair=_active(46,1)
_new_pair=_once(_old_pair,previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run=_active(47,1)
_fields=("json_extract(r.metadata_json,'$.policy_version')","json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run=_once(_old_run,previous._pair(*_fields),_pair(*_fields))
_old_pages=_active(43,7);_new_pages=_old_pages.replace(_old_versions,_new_versions)
_old_absent=_active(63,1);_new_absent=_old_absent.replace(_old_versions,_new_versions)
V071_POSTCONDITION_REPLACEMENTS=((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
