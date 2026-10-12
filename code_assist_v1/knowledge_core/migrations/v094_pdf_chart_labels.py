"""Forward printed-label context policy; every historical SQL branch is frozen.

Retained definition spans and lossless wire images are input observations, never
semantic/publication authority. Fresh source, pixel and excerpt replay remain
producer/repository responsibilities; this migration only binds stored evidence.
"""
from . import v093_pdf_v10_chart_tables as previous
from .pdf_v10_chart_tables_catalog import PDF_V10_CHART_TABLES_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object
from .v014_wiki_authoring import _sha

POLICY_V35 = 'wiki-pdf-v10-chart-labels-v1'
PROMPT_V35_SHA256 = '688f96afb64765dc16ae69676357807b8e12eb33feef50d702a4ad2f495daa65'
POLICY_IDENTITIES = (*previous.POLICY_IDENTITIES, (POLICY_V35, PROMPT_V35_SHA256))
SOURCE_DEFINITIONS_VERSION = 'pdf-layout-source-definition-context-v2'


def _pair(policy, assembly, prompt):
    return '('+' OR '.join(f"({policy}='{v}' AND {assembly}='{v}' AND {prompt}='{h}')"
        for v,h in POLICY_IDENTITIES)+')'


def _v35(sql):
    return sql.replace(previous.POLICY_V34, POLICY_V35).replace(previous.PROMPT_V34_SHA256, PROMPT_V35_SHA256)


def _branch(statement, identity, guard):
    before, rest = statement.split(' WHEN ', 1); existing, after = rest.rsplit(' BEGIN ', 1)
    return f"{before} WHEN CASE WHEN {identity}='{POLICY_V35}' THEN ({guard}) ELSE ({existing}) END BEGIN {after}"


_profile = previous._profile


def _fixed_model(config):
    return f"""json_type({config},'$.model_policy.max_tokens')='integer'
        AND json_extract({config},'$.model_policy.max_tokens')=4096
        AND json_type({config},'$.model_policy.request_budget_bytes')='integer'
        AND json_extract({config},'$.model_policy.request_budget_bytes')=1114112
        AND json_type({config},'$.model_policy.max_attempts')='integer'
        AND json_extract({config},'$.model_policy.max_attempts')=1"""


def _chart_bundle_guard(bundle):
    config = f"json_extract({bundle},'$.configuration_json')"
    return previous._chart_bundle_guard(bundle)+f""" AND
        json_extract({config},'$.policy_version')='{POLICY_V35}'
        AND json_extract({config},'$.prompt_sha256')='{PROMPT_V35_SHA256}'
        AND ({_fixed_model(config)})"""


def _policy_guard(value):
    config = f"json_extract({value},'$.configuration')"
    return _v35(previous._policy_guard(value))+f" AND ({_fixed_model(config)})"


def _definition_rows(value, manifest):
    fields = dict(terms='array',definition_terms='array',page='integer',chunk_id='text',
        range_domain='text',start='integer',end='integer',text='text',text_sha256='text',
        selection_reason='text',definition_proven='false')
    row = 'definition.value'
    return f"""json_type({value})='array' AND json_array_length({value}) BETWEEN 1 AND 32
        AND length(CAST((SELECT json_group_array(json_array(json_extract(d.value,'$.page'),
            json_extract(d.value,'$.text'))) FROM json_each({value}) d) AS BLOB))<=1024
        AND NOT EXISTS(SELECT 1 FROM json_each({value}) definition WHERE NOT coalesce((
          ({_exact_object(row, fields)})
          AND json_extract({row},'$.page')>=1
          AND length(json_extract({row},'$.chunk_id')) BETWEEN 1 AND 160
          AND json_extract({row},'$.range_domain')='source_chunk_unicode_code_points'
          AND json_extract({row},'$.selection_reason')='definition_like_literal_span'
          AND json_extract({row},'$.start')>=0
          AND json_extract({row},'$.end')>json_extract({row},'$.start')
          AND json_extract({row},'$.end')-json_extract({row},'$.start')=length(json_extract({row},'$.text'))
          AND ({_sha(f"json_extract({row},'$.text_sha256')")})
          AND json_array_length({row},'$.terms') BETWEEN 1 AND 32
          AND json_array_length({row},'$.definition_terms') BETWEEN 1 AND 32
          AND NOT EXISTS(SELECT 1 FROM json_each({row},'$.terms') term
            WHERE term.type<>'text' OR length(term.value) NOT BETWEEN 1 AND 32)
          AND NOT EXISTS(SELECT 1 FROM json_each({row},'$.definition_terms') term
            WHERE term.type<>'text' OR NOT EXISTS(SELECT 1 FROM json_each({row},'$.terms') allterm
              WHERE allterm.value=term.value))
          AND (SELECT count(DISTINCT value) FROM json_each({row},'$.terms'))=json_array_length({row},'$.terms')
          AND (SELECT count(DISTINCT value) FROM json_each({row},'$.definition_terms'))=json_array_length({row},'$.definition_terms')
          AND EXISTS(SELECT 1 FROM json_each({manifest},'$.chunks') chunk
            WHERE json_extract(chunk.value,'$.chunk_id')=json_extract({row},'$.chunk_id')
              AND json_extract(chunk.value,'$.page')=json_extract({row},'$.page')
              AND json_extract({row},'$.end')<=json_extract(chunk.value,'$.end')-json_extract(chunk.value,'$.start'))
        ),0))
        AND NOT EXISTS(SELECT 1 FROM json_each({value}) a JOIN json_each({value}) b ON a.key<b.key
          WHERE json_extract(a.value,'$.chunk_id')=json_extract(b.value,'$.chunk_id')
            AND json_extract(a.value,'$.start')=json_extract(b.value,'$.start')
            AND json_extract(a.value,'$.end')=json_extract(b.value,'$.end'))"""


def _definitions_guard(manifest):
    descriptor = f"json_extract({manifest},'$.source_definition_context')"
    base = dict(batch_id='text',response_sha256='text',source_pages='array',
        source_chunk_ids='array',output_byte_range='array')
    prose = 'prose.value'
    rows = f"json_extract({prose},'$.supplementary_source_contexts')"
    return f"""({_exact_object(descriptor, dict(version='text',semantic_definition_verification='false'))})
        AND json_extract({descriptor},'$.version')='{SOURCE_DEFINITIONS_VERSION}'
        AND NOT EXISTS(SELECT 1 FROM json_each({manifest},'$.batch_prose') prose
          WHERE NOT coalesce((CASE WHEN json_type({prose},'$.supplementary_source_contexts') IS NULL
            THEN ({_exact_object(prose, base)})
            ELSE ({_exact_object(prose, {**base,'supplementary_source_contexts':'array'})})
              AND ({_definition_rows(rows, manifest)}) END),0))"""


def _wire_guard(manifest):
    wire = f"json_extract({manifest},'$.wire_image_derivation')"
    fields = dict(version='text',encoder='object',policy='object',images='array',
        source_replaced='false',source_resized='false',source_clipped='false',
        semantic_accuracy_verified='false',publication_authority='false')
    caps = dict(maximum_images=1000,maximum_image_bytes=16777216,maximum_total_bytes=67108864,
        maximum_image_pixels=16000000,maximum_total_pixels=256000000)
    image_fields = dict(ordinal='integer',source_id='text',media_type='text',original_png_sha256='text',
        original_bytes='integer',wire_png_sha256='text',wire_bytes='integer',pixel_sha256='text',
        mode='text',dimensions='array')
    image = 'relation.value'
    relation_shape = _exact_object(image, {**image_fields,'reencoded':'true'})
    relation_shape += ' OR '+_exact_object(image, {**image_fields,'reencoded':'false'})
    bounded = ' AND '.join(f"json_extract({wire},'$.policy.{key}') BETWEEN 1 AND {cap}" for key,cap in caps.items())
    return f"""CASE WHEN NOT EXISTS(SELECT 1 FROM json_each({manifest},'$.visuals') v
          WHERE json_extract(v.value,'$.analysis')='model_reading')
        THEN json_type({manifest},'$.wire_image_derivation') IS NULL
        ELSE ({_exact_object(wire, fields)})
          AND json_extract({wire},'$.version')='pdf-lossless-png-wire-v1'
          AND ({_exact_object(f"json_extract({wire},'$.encoder')",dict(name='text',version='text',compress_level='integer',optimize='false'))})
          AND json_extract({wire},'$.encoder.name')='Pillow'
          AND length(json_extract({wire},'$.encoder.version')) BETWEEN 1 AND 128
          AND json_extract({wire},'$.encoder.compress_level')=9
          AND ({_exact_object(f"json_extract({wire},'$.policy')",dict.fromkeys(caps,'integer'))})
          AND ({bounded})
          AND json_extract({wire},'$.policy.maximum_image_bytes')=json_extract({manifest},'$.source.render_policy.maximum_page_bytes')
          AND json_extract({wire},'$.policy.maximum_total_bytes')=json_extract({manifest},'$.source.render_policy.maximum_total_bytes')
          AND json_extract({wire},'$.policy.maximum_image_pixels')=json_extract({manifest},'$.source.render_policy.maximum_page_pixels')
          AND json_extract({wire},'$.policy.maximum_total_pixels')=json_extract({manifest},'$.source.render_policy.maximum_total_pixels')
          AND json_array_length({wire},'$.images')=(SELECT count(*) FROM json_each({manifest},'$.visuals') v
            WHERE json_extract(v.value,'$.analysis')='model_reading')
          AND json_array_length({wire},'$.images')=json_extract({wire},'$.policy.maximum_images')
          AND (SELECT count(DISTINCT json_extract(value,'$.source_id')) FROM json_each({wire},'$.images'))
            =json_array_length({wire},'$.images')
          AND NOT EXISTS(SELECT 1 FROM json_each({wire},'$.images') relation WHERE NOT coalesce((
            ({relation_shape})
            AND json_extract({image},'$.ordinal')=relation.key+1
            AND length(json_extract({image},'$.source_id')) BETWEEN 1 AND 160
            AND json_extract({image},'$.media_type')='image/png' AND json_extract({image},'$.mode')='RGB'
            AND ({_sha(f"json_extract({image},'$.original_png_sha256')")})
            AND ({_sha(f"json_extract({image},'$.wire_png_sha256')")})
            AND ({_sha(f"json_extract({image},'$.pixel_sha256')")})
            AND json_extract({image},'$.original_bytes') BETWEEN 1 AND json_extract({wire},'$.policy.maximum_image_bytes')
            AND json_extract({image},'$.wire_bytes') BETWEEN 1 AND json_extract({image},'$.original_bytes')
            AND json_array_length({image},'$.dimensions')=2
            AND json_type({image},'$.dimensions[0]')='integer' AND json_extract({image},'$.dimensions[0]')>0
            AND json_type({image},'$.dimensions[1]')='integer' AND json_extract({image},'$.dimensions[1]')>0
            AND json_extract({image},'$.dimensions[0]')*json_extract({image},'$.dimensions[1]')<=json_extract({wire},'$.policy.maximum_image_pixels')
            AND CASE WHEN json_extract({image},'$.reencoded')=0
              THEN json_extract({image},'$.wire_png_sha256')=json_extract({image},'$.original_png_sha256')
                AND json_extract({image},'$.wire_bytes')=json_extract({image},'$.original_bytes')
              ELSE json_extract({image},'$.wire_bytes')<json_extract({image},'$.original_bytes') END
            AND EXISTS(SELECT 1 FROM json_each({manifest},'$.visuals') v
              WHERE json_extract(v.value,'$.analysis')='model_reading'
                AND json_extract(v.value,'$.source_id')=json_extract({image},'$.source_id')
                AND json_extract(v.value,'$.image.sha256')=json_extract({image},'$.original_png_sha256')
                AND json_extract(v.value,'$.image.bytes')=json_extract({image},'$.original_bytes')
                AND json_extract(v.value,'$.image.width')=json_extract({image},'$.dimensions[0]')
                AND json_extract(v.value,'$.image.height')=json_extract({image},'$.dimensions[1]')
                AND json_extract(v.value,'$.image.media_type')='image/png')
          ),0))
          AND (SELECT sum(json_extract(value,'$.original_bytes')) FROM json_each({wire},'$.images'))<=json_extract({wire},'$.policy.maximum_total_bytes')
          AND (SELECT sum(json_extract(value,'$.dimensions[0]')*json_extract(value,'$.dimensions[1]')) FROM json_each({wire},'$.images'))<=json_extract({wire},'$.policy.maximum_total_pixels')
        END"""


_table = _once(previous._table, previous._pair('batch_policy_version','assembly_version','prompt_sha256'),
    _pair('batch_policy_version','assembly_version','prompt_sha256'))
_run_guard = _v35(previous._run_guard)
_run = _branch(previous._run, "json_extract(NEW.metadata_json,'$.policy_version')", _run_guard)
_manifest_fields = {**previous._manifest_fields,'source_definition_context':'object'}
_manifest_shape = f"CASE WHEN json_type(NEW.manifest_json,'$.wire_image_derivation') IS NULL THEN ({_exact_object('NEW.manifest_json', _manifest_fields)}) ELSE ({_exact_object('NEW.manifest_json', {**_manifest_fields,'wire_image_derivation':'object'})}) END"
_snapshot_guard = _once(_v35(previous._snapshot_guard),
    _exact_object('NEW.manifest_json', previous._manifest_fields), _manifest_shape)
_old_image_pin = "AND json_extract(image.value,'$.sha256')=json_extract(v.value,'$.image.sha256')"
_new_image_pin = """AND EXISTS(SELECT 1 FROM json_each(NEW.manifest_json,'$.wire_image_derivation.images') relation
        WHERE json_extract(relation.value,'$.source_id')=json_extract(v.value,'$.source_id')
          AND json_extract(relation.value,'$.original_png_sha256')=json_extract(v.value,'$.image.sha256')
          AND json_extract(relation.value,'$.wire_png_sha256')=json_extract(image.value,'$.sha256')
          AND json_extract(relation.value,'$.wire_bytes')=json_extract(image.value,'$.raw_bytes')
          AND json_extract(image.value,'$.media_type')='image/png'
          AND json_extract(relation.value,'$.dimensions[0]')=json_extract(image.value,'$.width')
          AND json_extract(relation.value,'$.dimensions[1]')=json_extract(image.value,'$.height'))"""
_snapshot_guard = _once(_snapshot_guard, _old_image_pin, _new_image_pin)
_snapshot_guard += f" OR NOT coalesce(({_definitions_guard('NEW.manifest_json')}) AND ({_wire_guard('NEW.manifest_json')}),0)"
_snapshot = _branch(previous._snapshot, "json_extract(NEW.manifest_json,'$.version')", _snapshot_guard)
_batch = _branch(previous._batch,
    "(SELECT json_extract(metadata_json,'$.policy_version') FROM pdf_visual_authoring_run WHERE id=NEW.run_id)",
    previous.previous.batch.base._new_batch_guard)
_old_versions = previous._new_versions
_new_versions = _old_versions[:-1]+",'wiki-pdf-v10-chart-labels-v1')"
_attempt = previous._attempt.replace(_old_versions, _new_versions)
_outcome = previous._outcome.replace(_old_versions, _new_versions)
_policy = _once(previous._policy, f' OR {previous._new_valid}', f' OR {previous._new_valid} OR {_policy_guard("NEW.policy_json")}')
_layout_configuration_clause = _once(previous._layout_configuration_clause,
    previous._chart_bundle_guard('body.bundle_json'), _chart_bundle_guard('body.bundle_json'))
_family_guard = _once(previous._family_guard, previous._layout_configuration_clause, _layout_configuration_clause)
_workflow_run_family = _branch(previous._workflow_run_family,
    "json_extract(NEW.metadata_json,'$.policy_version')", _family_guard)
_old_view_clause = (f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{previous.POLICY_V34}' "
    f"THEN ({previous._layout_configuration_clause.replace('NEW.','r.')}) ELSE ({previous._prior_view_clause}) END")
_registered_run = _once(previous._registered_run, _old_view_clause,
    f"CASE WHEN json_extract(r.metadata_json,'$.policy_version')='{POLICY_V35}' "
    f"THEN ({_layout_configuration_clause.replace('NEW.','r.')}) ELSE ({_old_view_clause}) END")
_source_chart_guard = f"""CASE WHEN json_extract(json_extract(NEW.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V35}'
    THEN coalesce(({_chart_bundle_guard('NEW.bundle_json')}),0) ELSE ({previous._source_chart_guard}) END"""
_source_chart = f"""CREATE TRIGGER pdf_visual_chart_source_body_insert BEFORE INSERT ON pdf_visual_source_body
    WHEN NOT ({_source_chart_guard}) BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_CHART_SOURCE_INVALID'); END"""
_ready_chart = _branch(previous._ready_chart,
    "(SELECT json_extract(json_extract(bundle_json,'$.configuration_json'),'$.policy_version') FROM pdf_visual_source_body WHERE source_id=NEW.source_id)",
    f"EXISTS(SELECT 1 FROM pdf_visual_source_body b WHERE b.source_id=NEW.source_id AND NOT coalesce(({_chart_bundle_guard('b.bundle_json')}),0))")
_retained = previous._retained+f""" UNION SELECT json_extract(b.bundle_json,'$.chart_supplement.sha256'),s.artifact_id
    FROM pdf_visual_source_body b JOIN pdf_visual_source s ON s.id=b.source_id
    WHERE json_extract(json_extract(b.bundle_json,'$.configuration_json'),'$.policy_version')='{POLICY_V35}'
      AND ({_chart_bundle_guard('b.bundle_json')})"""

V094_STATEMENTS = (
    *('DROP TRIGGER '+s.split()[2] for s in previous.previous.layout.writing._request_triggers),
    'ALTER TABLE pdf_visual_workflow_request RENAME TO pdf_visual_workflow_request_v93',
    _table, 'INSERT INTO pdf_visual_workflow_request SELECT * FROM pdf_visual_workflow_request_v93',
    'DROP TABLE pdf_visual_workflow_request_v93', *previous.previous.layout.writing._request_triggers,
    'DROP TRIGGER pdf_visual_run_insert', _run, 'DROP TRIGGER pdf_visual_snapshot_body_insert', _snapshot,
    'DROP TRIGGER pdf_visual_batch_insert', _batch, 'DROP TRIGGER pdf_visual_attempt_insert', _attempt,
    'DROP TRIGGER pdf_visual_outcome_insert', _outcome, 'DROP TRIGGER pdf_visual_workflow_policy_insert', _policy,
    'DROP TRIGGER pdf_visual_workflow_run_family', _workflow_run_family,
    'DROP VIEW pdf_visual_registered_run', _registered_run,
    'DROP TRIGGER pdf_visual_chart_source_body_insert', _source_chart,
    'DROP TRIGGER pdf_visual_chart_ready_insert', _ready_chart,
    'DROP VIEW pdf_visual_retained_blob', _retained)


def _active(version, index):
    return next(row.query for row in prior.resolved_postconditions
        if (row.origin_version,row.origin_index)==(version,index))


_old_snapshot = _active(43,18)
_new_snapshot = (_old_snapshot.split(' WHERE (',1)[0]+' WHERE ('+
    _snapshot.split(' WHEN ',1)[1].rsplit(' BEGIN ',1)[0].replace('NEW.','retained.')+') LIMIT 1')
_old_policy = _active(44,5)
_new_policy = _once(_old_policy, f' OR {previous._policy_guard("b.policy_json")}',
    f' OR {previous._policy_guard("b.policy_json")} OR {_policy_guard("b.policy_json")}')
_old_pair = _active(46,1)
_new_pair = _once(_old_pair, previous._pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'),
    _pair('q.batch_policy_version','q.assembly_version','q.prompt_sha256'))
_old_run = _active(47,1)
_run_fields = ("json_extract(r.metadata_json,'$.policy_version')", "json_extract(r.metadata_json,'$.assembly_version')",
    "json_extract(r.metadata_json,'$.prompt_sha256')")
_new_run = _once(_old_run, previous._pair(*_run_fields), _pair(*_run_fields))
_old_pages = _active(43,7); _new_pages = _old_pages.replace(_old_versions,_new_versions)
_old_absent = _active(63,1); _new_absent = _old_absent.replace(_old_versions,_new_versions)
_old_source = _active(93,1)
_new_source = f"SELECT 'chart source identity invalid' FROM pdf_visual_source_body retained WHERE NOT ({_source_chart_guard.replace('NEW.','retained.')}) LIMIT 1"
V094_POSTCONDITION_REPLACEMENTS = ((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),
    (_old_pair,_new_pair),(_old_run,_new_run),(_old_pages,_new_pages),(_old_absent,_new_absent),(_old_source,_new_source))
