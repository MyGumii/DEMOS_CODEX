"""Forward retained chart candidates plus unchanged V10 prose/literal tables.

Historical guards remain exact ELSE branches. Chart identities bind a verified
CAS blob, not semantic ownership, numerical accuracy or new publication authority.
"""
from . import v092_pdf_v10_literal_tables as previous
from . import v042_pdf_visual_source_storage as archive
from . import v044_pdf_visual_authoring_workflow as workflow
from .pdf_v10_literal_tables_catalog import PDF_V10_LITERAL_TABLES_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from .v014_wiki_authoring import _sha

POLICY_V34 = 'wiki-pdf-v10-chart-tables-v1'
PROMPT_V34_SHA256 = '876101a9fb5cb13ee3b7515963d48d5e4fbcb66807c1d924143ca63ba0b7b7ce'
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V34, PROMPT_V34_SHA256))
_CHART_FIELDS = dict(version='text', sha256='text', bytes='integer',
    source_geometry_sha256='text', source_raw_sha256='text', layout_catalog_sha256='text',
    figure_count='integer', publication_authority='false')
_CONFIG_FIELDS = dict(version='text', policy_version='text', prompt_sha256='text', chunk_bytes='integer',
    **dict.fromkeys(('model_policy','work_policy','execution_policy','geometry_limits','process_policy',
        'render_policy','renderer_identity','retained_asset_policy'), 'object'))


def _pair(policy, assembly, prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES)+')'


def _v34(sql):
    return sql.replace(previous.POLICY_V33, POLICY_V34).replace(previous.PROMPT_V33_SHA256, PROMPT_V34_SHA256)


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1); existing, after = rest.rsplit(' BEGIN ', 1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V34}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


def _profile(config):
    value = f"json_extract({config},'$.chart_preparation')"
    language = f"json_extract({value},'$.language')"
    segments = f"'['||replace(json_quote({language}),'-','\",\"')||']'"
    caps = dict(maximum_input_bytes=268435456,maximum_figures=4096,maximum_samples=8192,
        maximum_seconds=300,maximum_supplement_bytes=33554432,maximum_reading_bytes=4194304,
        maximum_pixel_tests=64000000,maximum_reading_seconds=60)
    groups = dict(native=dict(source_bytes=67108864,pages=1000,objects=4096,image_pixels=16000000,
        total_pixels=128000000,image_bytes=16777216,total_bytes=67108864,manifest_bytes=4194304,panels=64),
        ocr=dict(image_bytes=16777216,source_pixels=16000000,ocr_stdout_bytes=1048576,words=4096,
            panels=64,legend_scan_pixels=4000000,descriptor_bytes=65536,context_bytes=32768,timeout_seconds=60),
        rotated=dict(source_pixels=16000000,mosaic_pixels=16000000,png_bytes=16777216,panels=64,
            words=4096,ocr_stdout_bytes=1048576,metadata_bytes=4194304,timeout_seconds=30))
    limits = f"json_extract({value},'$.limits')"
    bounded = [_exact_object(limits,{**dict.fromkeys(caps,'integer'),**dict.fromkeys(groups,'object')})]
    bounded.extend(f"json_extract({limits},'$.{key}') BETWEEN 1 AND {cap}" for key,cap in caps.items())
    for group, fields in groups.items():
        nested = f"json_extract({limits},'$.{group}')"
        bounded.append(_exact_object(nested,dict.fromkeys(fields,'integer')))
        bounded.extend(f"json_extract({nested},'$.{key}') BETWEEN 1 AND {cap}" for key,cap in fields.items())
    return f"""({_exact_object(value, dict(version='text',language='text',limits='object'))})
        AND json_extract({value},'$.version')='pdf-chart-fresh-preparation-v1'
        AND {language} NOT GLOB '*[^A-Za-z0-9-]*'
        AND json_array_length({segments}) BETWEEN 1 AND 4
        AND NOT EXISTS(SELECT 1 FROM json_each({segments}) segment WHERE
          CASE WHEN segment.key=0 THEN length(segment.value) NOT BETWEEN 2 AND 8
            OR segment.value GLOB '*[^A-Za-z]*'
          ELSE length(segment.value) NOT BETWEEN 1 AND 8 OR segment.value GLOB '*[^A-Za-z0-9]*' END)
        AND {' AND '.join('('+term+')' for term in bounded)}"""


def _chart_bundle_guard(bundle):
    config = f"json_extract({bundle},'$.configuration_json')"
    value = f"json_extract({bundle},'$.chart_supplement')"
    return f"""({_exact_object(bundle, dict(version='text',source='object',plan_sha256='text',
        configuration_json='text',retained_assets_json='text',requests='array',chart_supplement='object'))})
        AND ({_exact_object(value, _CHART_FIELDS)})
        AND json_extract({value},'$.version')='pdf-chart-supplement-v1'
        AND ({_sha(f"json_extract({value},'$.sha256')")})
        AND json_extract({value},'$.bytes') BETWEEN 1 AND 33554432
        AND json_extract({value},'$.figure_count') BETWEEN 0 AND 4096
        AND json_extract({value},'$.bytes')<=json_extract({config},'$.chart_preparation.limits.maximum_supplement_bytes')
        AND json_extract({value},'$.figure_count')<=json_extract({config},'$.chart_preparation.limits.maximum_figures')
        AND json(json_extract({config},'$.chart_identity'))=json({value})
        AND json_extract({value},'$.source_raw_sha256')=json_extract({bundle},'$.source.geometry.raw_sha256')
        AND json_extract({value},'$.source_geometry_sha256')=json_extract({bundle},'$.source.geometry.geometry_sha256')
        AND json_extract({value},'$.layout_catalog_sha256')=json_extract({config},'$.layout_identity.catalog_sha256')
        AND ({_profile(config)})
        AND EXISTS(SELECT 1 FROM blob_object chart WHERE chart.sha256=json_extract({value},'$.sha256')
          AND chart.size_bytes=json_extract({value},'$.bytes') AND chart.verification_status='verified'
          AND chart.relative_path=substr(chart.sha256,1,2)||'/'||substr(chart.sha256,3,2)||'/'||chart.sha256)
        AND NOT EXISTS(SELECT 1 FROM blob_gc_candidate gc
          WHERE gc.blob_sha256=json_extract({value},'$.sha256') AND gc.state IN ('claimed','retry','dead'))"""


def _policy_guard(value):
    config = f"json_extract({value},'$.configuration')"
    sql = workflow._policy_valid(value).replace(workflow.POLICY, POLICY_V34).replace(
        workflow.PROMPT_SHA256, PROMPT_V34_SHA256)
    return _once(sql, _exact_object(config, _CONFIG_FIELDS),
        _exact_object(config, {**_CONFIG_FIELDS,'chart_preparation':'object'}))+f' AND ({_profile(config)})'


_table = _once(previous._table, previous._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run_guard = _v34(previous._run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _run_guard)
_manifest_fields = {**previous._manifest_fields,'chart_readings':'object'}
_snapshot_guard = _once(_v34(previous._snapshot_guard),
    _exact_object('NEW.manifest_json', previous._manifest_fields),
    _exact_object('NEW.manifest_json', _manifest_fields))
_details = "json_extract(NEW.manifest_json,'$.chart_readings')"
_snapshot_guard += f""" OR NOT EXISTS(SELECT 1 FROM pdf_visual_wiki_snapshot s
    JOIN pdf_visual_authoring_run r ON r.id=s.run_id
    JOIN pdf_visual_source_body ab ON ab.source_id=r.source_archive_id WHERE s.id=NEW.snapshot_id
      AND ({_exact_object(_details, dict(identity='object',output_byte_range='array',rendered_sha256='text',
        numerical_accuracy_verified='false',semantic_accuracy_verified='false',missing_values_filled='false'))})
      AND ({_chart_bundle_guard('ab.bundle_json')})
      AND json(json_extract({_details},'$.identity'))=json(json_extract(ab.bundle_json,'$.chart_supplement'))
      AND ({_sha(f"json_extract({_details},'$.rendered_sha256')")})
      AND json_array_length({_details},'$.output_byte_range')=2
      AND json_type({_details},'$.output_byte_range[0]')='integer'
      AND json_type({_details},'$.output_byte_range[1]')='integer'
      AND json_extract({_details},'$.output_byte_range[0]')>=0
      AND json_extract({_details},'$.output_byte_range[1]')>=json_extract({_details},'$.output_byte_range[0]')
      AND json_extract({_details},'$.output_byte_range[0]')>=(SELECT coalesce(max(json_extract(value,'$.output_byte_range[1]')),0)
        FROM json_each(NEW.manifest_json,'$.batch_prose'))
      AND json_extract({_details},'$.output_byte_range[1]')=
        json_extract(NEW.manifest_json,'$.literal_tables.output_byte_range[0]')-
        CASE WHEN json_extract(NEW.manifest_json,'$.literal_tables.table_count')>0 THEN 1 ELSE 0 END
      AND CASE WHEN json_extract({_details},'$.identity.figure_count')=0
        THEN json_extract({_details},'$.output_byte_range[0]')=json_extract({_details},'$.output_byte_range[1]')
        ELSE json_extract({_details},'$.output_byte_range[1]')>json_extract({_details},'$.output_byte_range[0]') END)"""
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    previous.batch.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1]+",'wiki-pdf-v10-chart-tables-v1')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_old_valid = previous._v33(previous.batch._v10(previous.batch._old_valid))
_new_valid = _policy_guard('NEW.policy_json')
_policy = _once(previous._policy, f' OR {_old_valid}', f' OR {_old_valid} OR {_new_valid}')
_config = "json_extract(body.bundle_json,'$.configuration_json')"
_layout_configuration_clause = _once(previous._layout_configuration_clause,
    f"json_remove({_config},'$.layout_identity','$.protected_binding')",
    f"json_remove({_config},'$.layout_identity','$.protected_binding','$.chart_identity')")
_layout_configuration_clause = _once(_layout_configuration_clause,
    f"json_type({_config},'$.layout_identity')='object'",
    f"json_type({_config},'$.layout_identity')='object' AND ({_chart_bundle_guard('body.bundle_json')})")
_family_guard = _once(previous._family_guard, previous._layout_configuration_clause, _layout_configuration_clause)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _family_guard)
_prior_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V33}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.','r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _prior_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V34}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.','r.')}) ELSE ({_prior_view_clause}) END")
_source_chart_guard = f"""CASE WHEN json_extract(json_extract(NEW.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V34}'
    THEN coalesce(({_chart_bundle_guard('NEW.bundle_json')}),0)
    ELSE json_type(NEW.bundle_json,'$.chart_supplement') IS NULL END"""
_source_chart = f"""CREATE TRIGGER pdf_visual_chart_source_body_insert BEFORE INSERT ON pdf_visual_source_body
    WHEN NOT ({_source_chart_guard}) BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_CHART_SOURCE_INVALID'); END"""
_ready_chart = f"""CREATE TRIGGER pdf_visual_chart_ready_insert BEFORE INSERT ON pdf_visual_source_ready
    WHEN EXISTS(SELECT 1 FROM pdf_visual_source_body b WHERE b.source_id=NEW.source_id
      AND json_extract(json_extract(b.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V34}'
      AND NOT coalesce(({_chart_bundle_guard('b.bundle_json')}),0))
    BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_CHART_SOURCE_INCOMPLETE'); END"""
_retained = next(sql for sql in archive.V042_STATEMENTS if sql.startswith('CREATE VIEW pdf_visual_retained_blob'))
_retained += f""" UNION SELECT json_extract(b.bundle_json,'$.chart_supplement.sha256'),s.artifact_id
    FROM pdf_visual_source_body b JOIN pdf_visual_source s ON s.id=b.source_id
    WHERE json_extract(json_extract(b.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V34}'
      AND ({_chart_bundle_guard('b.bundle_json')})"""

V093_STATEMENTS = (
    *('DROP TRIGGER '+s.split()[2] for s in previous.layout.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v92',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v92',
    'DROP TABLE pdf_visual_workflow_request_v92', *previous.layout.writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert', _run, 'DROP TRIGGER pdf_visual_snapshot_body_insert', _snapshot,
    'DROP TRIGGER pdf_visual_batch_insert', _batch, 'DROP TRIGGER pdf_visual_attempt_insert', _attempt,
    'DROP TRIGGER pdf_visual_outcome_insert', _outcome, 'DROP TRIGGER pdf_visual_workflow_policy_insert', _policy,
    'DROP TRIGGER pdf_visual_workflow_run_family', _workflow_run_family,
    'DROP VIEW pdf_visual_registered_run', _registered_run,
    _source_chart, _ready_chart, 'DROP VIEW pdf_visual_retained_blob', _retained)


def _active(version, index):
    return next(row.query for row in prior.resolved_postconditions
        if (row.origin_version,row.origin_index)==(version,index))


_old_snapshot = _active(43,18)
_new_snapshot = (_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+
    _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1')
_old_policy = _active(44,5)
_old_p = previous._v33(previous.batch._v10(previous.batch._p))
_new_policy = _once(_old_policy, f' OR {_old_p}', f' OR {_old_p} OR {_policy_guard("b.policy_json")}')
_old_pair = _active(46,1)
_new_pair = _once(_old_pair, previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run = _active(47,1)
_run_fields = ("json_extract(r.metadata_json,'$.policy_version')", "json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run = _once(_old_run, previous._pair(*_run_fields), _pair(*_run_fields))
_old_pages = _active(43,7); _new_pages = _old_pages.replace(_old_versions,_new_versions)
_old_absent = _active(63,1); _new_absent = _old_absent.replace(_old_versions,_new_versions)
V093_POSTCONDITION_REPLACEMENTS = ((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent))
V093_POSTCONDITION_QUERIES = (f"SELECT 'chart source identity invalid' FROM pdf_visual_source_body retained "
    f"WHERE NOT ({_source_chart_guard.replace('NEW.','retained.')}) LIMIT 1",)
