"""Explicit ordinary original-range admission successor; historical SQL frozen.

Only registered V14 partition configurations enter this branch. Configured and
effective transport/canvas settings are both retained, never relabelled. Original
CAS, actor/scope, claims, receipts and literal-table guards remain unchanged.
Pixel replay belongs to the producer: stored wire relations grant no semantics.
"""
from . import v095_pdf_numeric_append as previous
from . import v094_pdf_chart_labels as wire
from .pdf_numeric_append_catalog import PDF_NUMERIC_APPEND_MIGRATION_CATALOG as prior
from .v032_native_display_storage import _once, _exact_object

PARTITION = 'pdf-original-range-request-partition-v14'
FORWARD = 'pdf-chart-admitted-owned-native-v13'
_CONFIG_FIELDS = {**previous.composition._CONFIG_FIELDS, 'chart_preparation':'object',
    'forward_source_policy':'object', 'request_partition_policy':'text'}
_RENDER_FIELDS = dict.fromkeys(('dpi','maximum_page_pixels','maximum_total_pixels',
    'maximum_page_bytes','maximum_total_bytes'), 'integer')


def _fresh(config):
    # Marker absence preserves the exact historical contract. A present but
    # malformed marker must fail the new guard, never fall into an old lane.
    return f"(json_type({config},'$.request_partition_policy') IS NOT NULL)"


def _branch(statement, predicate, guard):
    before, rest = statement.split(' WHEN ', 1)
    existing, after = rest.rsplit(' BEGIN ', 1)
    return f'{before} WHEN CASE WHEN {predicate} THEN ({guard}) ELSE ({existing}) END BEGIN {after}'


def _forward_guard(config, *, effective):
    forward = f"json_extract({config},'$.forward_source_policy')"
    configured_render = f"json_extract({forward},'$.configured_render_policy')"
    render = f"json_extract({config},'$.render_policy')"
    fields = dict(version='text', configured_render_policy='object',
        configured_request_budget_bytes='integer', effective_request_budget_bytes='integer',
        configured_logical_input_bytes='integer', effective_logical_input_bytes='integer',
        all_pages_retained='true', grid_fidelity_proven='false')
    caps = dict(maximum_page_pixels=16000000,maximum_total_pixels=256000000,
        maximum_page_bytes=16777216,maximum_total_bytes=67108864)
    configured_caps = ' AND '.join(f"json_extract({configured_render},'$.{key}') BETWEEN 1 AND {cap}" for key,cap in caps.items())
    return f"""json_extract({config},'$.policy_version')='{previous.POLICY_V36}'
        AND json_extract({config},'$.prompt_sha256')='{previous.PROMPT_SHA_V36}'
        AND json_type({config},'$.request_partition_policy')='text'
        AND json_extract({config},'$.request_partition_policy')='{PARTITION}'
        AND json_type({config},'$.protected_binding') IS NULL
        AND ({_exact_object(forward, fields)})
        AND json_extract({forward},'$.version')='{FORWARD}'
        AND json_extract({forward},'$.configured_request_budget_bytes')=1114112
        AND json_extract({forward},'$.effective_request_budget_bytes')=4194304
        AND json_extract({forward},'$.configured_logical_input_bytes')=67108864
        AND json_extract({forward},'$.effective_logical_input_bytes')=134217728
        AND ({_exact_object(configured_render, _RENDER_FIELDS)})
        AND ({_exact_object(render, _RENDER_FIELDS)})
        AND json_extract({configured_render},'$.dpi') BETWEEN 72 AND 144
        AND {configured_caps}
        AND json_extract({render},'$.dpi') BETWEEN 72 AND json_extract({configured_render},'$.dpi')
        AND json(json_set({render},'$.dpi',json_extract({configured_render},'$.dpi')))=json({configured_render})
        AND json_type({config},'$.model_policy.max_tokens')='integer'
        AND json_extract({config},'$.model_policy.max_tokens')=4096
        AND json_type({config},'$.model_policy.max_attempts')='integer'
        AND json_extract({config},'$.model_policy.max_attempts')=1
        AND json_type({config},'$.model_policy.request_budget_bytes')='integer'
        AND json_extract({config},'$.model_policy.request_budget_bytes')={4194304 if effective else 1114112}
        AND json_type({config},'$.work_policy.logical_input_bytes')='integer'
        AND json_extract({config},'$.work_policy.logical_input_bytes')={134217728 if effective else 67108864}
        {'' if effective else f'AND json({render})=json({configured_render})'}"""


def _policy_guard(value):
    config = f"json_extract({value},'$.configuration')"
    base = _once(previous._policy_guard(value),
        _exact_object(config, {**previous.composition._CONFIG_FIELDS,'chart_preparation':'object'}),
        _exact_object(config, _CONFIG_FIELDS))
    return base+f' AND ({_forward_guard(config, effective=False)})'


def _chart_bundle_guard(bundle):
    config = f"json_extract({bundle},'$.configuration_json')"
    return previous.composition._chart_bundle_guard(bundle)+f""" AND
        ({_exact_object(config, {**_CONFIG_FIELDS,'layout_identity':'object','chart_identity':'object'})})
        AND json(json_extract({bundle},'$.source.render_policy'))=json(json_extract({config},'$.render_policy'))
        AND ({_forward_guard(config, effective=True)})"""


_source_config = "json_extract(NEW.bundle_json,'$.configuration_json')"
_source_chart_guard = f"CASE WHEN {_fresh(_source_config)} THEN coalesce(({_chart_bundle_guard('NEW.bundle_json')}),0) ELSE ({previous._source_chart_guard}) END"
_source_chart = f"""CREATE TRIGGER pdf_visual_chart_source_body_insert BEFORE INSERT ON pdf_visual_source_body
    WHEN NOT ({_source_chart_guard}) BEGIN SELECT RAISE(ABORT,'PDF_VISUAL_CHART_SOURCE_INVALID'); END"""
_ready_config = "(SELECT json_extract(bundle_json,'$.configuration_json') FROM pdf_visual_source_body WHERE source_id=NEW.source_id)"
_ready_chart = _branch(previous._ready_chart, _fresh(_ready_config),
    f"EXISTS(SELECT 1 FROM pdf_visual_source_body b WHERE b.source_id=NEW.source_id AND NOT coalesce(({_chart_bundle_guard('b.bundle_json')}),0))")

_config = "json_extract(body.bundle_json,'$.configuration_json')"
_normalized_config = f"json_set(json_remove({_config},'$.layout_identity','$.protected_binding','$.chart_identity'),'$.render_policy',json_extract({_config},'$.forward_source_policy.configured_render_policy'),'$.model_policy.request_budget_bytes',json_extract({_config},'$.forward_source_policy.configured_request_budget_bytes'),'$.work_policy.logical_input_bytes',json_extract({_config},'$.forward_source_policy.configured_logical_input_bytes'))"
_layout_configuration_clause = _once(previous._layout_configuration_clause,
    previous._chart_bundle_guard('body.bundle_json'), _chart_bundle_guard('body.bundle_json'))
_layout_configuration_clause = _once(_layout_configuration_clause,
    f"json_remove({_config},'$.layout_identity','$.protected_binding','$.chart_identity')", _normalized_config)
_family_guard = _once(previous._family_guard, previous._layout_configuration_clause, _layout_configuration_clause)
_run_config = "(SELECT json_extract(bundle_json,'$.configuration_json') FROM pdf_visual_source_body WHERE source_id=NEW.source_archive_id)"
_workflow_run_family = _branch(previous._workflow_run_family, _fresh(_run_config), _family_guard)
_registered_run = _once(previous._registered_run, previous._layout_configuration_clause.replace('NEW.','r.'),
    f"CASE WHEN {_fresh(_run_config.replace('NEW.','r.'))} THEN ({_layout_configuration_clause.replace('NEW.','r.')}) ELSE ({previous._layout_configuration_clause.replace('NEW.','r.')}) END")
_policy = _once(previous._policy, f' OR {previous._policy_guard("NEW.policy_json")}',
    f' OR {previous._policy_guard("NEW.policy_json")} OR {_policy_guard("NEW.policy_json")}')


def _manifest_shape(manifest):
    fields = previous._manifest_fields
    image_present = f"json_type({manifest},'$.wire_image_derivation') IS NOT NULL"
    grid_present = f"json_type({manifest},'$.ambiguous_grid_source') IS NOT NULL"
    return f"""CASE WHEN {image_present} THEN CASE WHEN {grid_present}
        THEN ({_exact_object(manifest, {**fields,'wire_image_derivation':'object','ambiguous_grid_source':'object'})})
        ELSE ({_exact_object(manifest, {**fields,'wire_image_derivation':'object'})}) END
        ELSE CASE WHEN {grid_present}
        THEN ({_exact_object(manifest, {**fields,'ambiguous_grid_source':'object'})})
        ELSE ({_exact_object(manifest, fields)}) END END"""


def _ambiguous_guard(manifest):
    value = f"json_extract({manifest},'$.ambiguous_grid_source')"
    fields = dict(version='text',pages='array',all_original_glyphs_retained='true',
        original_page_images_retained='true',cell_associations_verified='false',semantic_accuracy_verified='false')
    return f"""CASE WHEN json_type({manifest},'$.ambiguous_grid_source') IS NULL THEN 1 ELSE
        ({_exact_object(value, fields)})
        AND json_extract({value},'$.version')='pdf-original-glyphs-ambiguous-grid-v1'
        AND json_array_length({value},'$.pages') BETWEEN 1 AND json_array_length({manifest},'$.source.pages')
        AND (SELECT count(DISTINCT value) FROM json_each({value},'$.pages'))=json_array_length({value},'$.pages')
        AND NOT EXISTS(SELECT 1 FROM json_each({value},'$.pages') page WHERE page.type<>'integer'
          OR page.value NOT BETWEEN 1 AND json_array_length({manifest},'$.source.pages')
          OR page.key<>(SELECT count(*) FROM json_each({value},'$.pages') preceding WHERE preceding.value<page.value))
        END"""


_snapshot_guard = _once(previous._snapshot_guard,
    _exact_object('NEW.manifest_json',previous._manifest_fields), _manifest_shape('NEW.manifest_json'))
_snapshot_guard = _once(_snapshot_guard, previous._chart_bundle_guard('ab.bundle_json'), _chart_bundle_guard('ab.bundle_json'))
_snapshot_guard = _once(_snapshot_guard, wire._old_image_pin, wire._new_image_pin)
_snapshot_guard += f" OR NOT coalesce(({wire._wire_guard('NEW.manifest_json')}) AND ({_ambiguous_guard('NEW.manifest_json')}),0)"
_snapshot_config = "(SELECT json_extract(b.bundle_json,'$.configuration_json') FROM pdf_visual_wiki_snapshot s JOIN pdf_visual_authoring_run r ON r.id=s.run_id JOIN pdf_visual_source_body b ON b.source_id=r.source_archive_id WHERE s.id=NEW.snapshot_id)"
_snapshot = _branch(previous._snapshot, _fresh(_snapshot_config), _snapshot_guard)
_retained = previous._retained+f""" UNION SELECT json_extract(b.bundle_json,'$.chart_supplement.sha256'),s.artifact_id
    FROM pdf_visual_source_body b JOIN pdf_visual_source s ON s.id=b.source_id
    WHERE {_fresh("json_extract(b.bundle_json,'$.configuration_json')")}
      AND ({_chart_bundle_guard('b.bundle_json')})"""

V096_STATEMENTS = (
    'DROP TRIGGER pdf_visual_workflow_policy_insert', _policy,
    'DROP TRIGGER pdf_visual_workflow_run_family', _workflow_run_family,
    'DROP VIEW pdf_visual_registered_run', _registered_run,
    'DROP TRIGGER pdf_visual_chart_source_body_insert', _source_chart,
    'DROP TRIGGER pdf_visual_chart_ready_insert', _ready_chart,
    'DROP TRIGGER pdf_visual_snapshot_body_insert', _snapshot,
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
_old_source = _active(93,1)
_new_source = f"SELECT 'chart source identity invalid' FROM pdf_visual_source_body retained WHERE NOT ({_source_chart_guard.replace('NEW.','retained.')}) LIMIT 1"
V096_POSTCONDITION_REPLACEMENTS = ((_old_snapshot,_new_snapshot),(_old_policy,_new_policy),(_old_source,_new_source))
