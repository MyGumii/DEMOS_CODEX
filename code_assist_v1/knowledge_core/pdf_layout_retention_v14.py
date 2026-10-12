"""Forward V25: named numeric bounds, unchanged strict source/curve guards.

Only fresh named-bound responses belong to this contract. A private syntax
projection preserves every numeric value and ordered condition/series array;
the frozen V7 decoder still checks their domains, support and source ownership.
Original provider text and named groups remain the receipt/manifest evidence.
No sorting, expansion, repair, historical-response fallback or extra call exists.
"""
from copy import deepcopy
from html import escape
import json

from . import pdf_layout_retention_v13 as previous
from . import pdf_layout_retention_v7 as historical
from . import pdf_layout_retention as legacy
from . import pdf_layout_wiki as writer
from .pdf_layout_retention_v4 import _image_label_tokens, image_scope_guidance
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import (AuthoredWiki, AuthoringIncompleteError,
    AuthoringOutputError, AuthoringWorkLimitError)

VERSION = 'wiki-pdf-layout-retention-v14'
DECODER_VERSION = 'pdf-layout-retention-v14'
CURVE_DOMAIN_VERSION = 'pdf-curve-domain-named-bounds-v1'
ALLOCATION_VERSION = previous.ALLOCATION_VERSION
OUTPUT_FLOOR = previous.OUTPUT_FLOOR
OUTPUT_CEILING = previous.OUTPUT_CEILING
REQUEST_MODE_VERSION = previous.REQUEST_MODE_VERSION
PROMPT = previous.PROMPT
LEDGER_VERSION = previous.LEDGER_VERSION
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = previous.IMAGE_SCOPE_VERSION
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v14-named-bounds-1'
FINAL_GUIDANCE_VERSION = 'pdf-curve-domain-final-guidance-v2'
TEXT_PARTITION_VERSION = previous.TEXT_PARTITION_VERSION
TEXT_CHUNK_BYTES = previous.TEXT_CHUNK_BYTES
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
TEXT_OUTPUT_GUIDANCE = previous.TEXT_OUTPUT_GUIDANCE
OUTPUT_GUIDANCE = (
    '\nPreserve facts/definitions/conditions/exceptions; undefined stays so. '
    'Nonplots: estimated_values=[]; relations/uncertainties; printed results->printed_values. '
    'Curves: conditions/condition_values/each values same N, paired order; descending OK. '
    'x_bounds/y_bounds/observed_x_bounds={min,max}: numeric NOT display order; '
    'axis min<max, support min<=max; linear/log10. '
    'Read drawn axes/support, not point extrema or tick=frame guesses. '
    'y requires x in axis+support and y in axis; no guessed bounds expansion. '
    'No guesses/units/extrapolation/fill/sort/clamp. Unreadable axis/support/y null; '
    'unknown x null; readable estimates stay unverified.\n')

# These helpers dispatch through the compiler's exact policy, or have no
# policy-sensitive globals. Version-sensitive allocation/assembly stays local.
initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
checked_subject_bindings = previous.checked_subject_bindings
collect_term_contexts = previous.collect_term_contexts
text_ranges = previous.text_ranges


def response_format(scope):
    original = historical.response_format(scope)
    if original is None:
        return None
    form = deepcopy(original)
    bounds = {'type': 'object', 'additionalProperties': False,
        'required': ['min', 'max'], 'properties': {
            'min': {'type': 'number'}, 'max': {'type': 'number'}}}
    records = form['json_schema']['schema']['properties']['figures']['properties']
    for alias, original_record in records.items():
        record = deepcopy(original_record)
        records[alias] = record
        group = record['properties']['estimated_values']['items']
        axis = group['properties']['axis_reading']['anyOf'][1]
        series = group['properties']['series']['items']
        if (set(axis['properties']) != {'x_range', 'y_range', 'x_scale', 'y_scale'}
                or axis['required'] != ['x_range', 'y_range', 'x_scale', 'y_scale']
                or set(series['properties']) != {'name', 'values', 'observed_x_range'}
                or series['required'] != ['name', 'values', 'observed_x_range']):
            raise AuthoringIncompleteError('The frozen curve-domain schema changed.')
        axis['required'] = ['x_bounds', 'y_bounds', 'x_scale', 'y_scale']
        axis['properties'] = {'x_bounds': deepcopy(bounds), 'y_bounds': deepcopy(bounds),
            'x_scale': axis['properties']['x_scale'], 'y_scale': axis['properties']['y_scale']}
        series['required'] = ['name', 'values', 'observed_x_bounds']
        del series['properties']['observed_x_range']
        series['properties']['observed_x_bounds'] = {
            'anyOf': [{'type': 'null'}, deepcopy(bounds)]}
    return form


def additional_schema_tokens(scope):
    original, changed = legacy.response_format(scope), response_format(scope)
    return max(0, estimate_text_tokens(legacy._json(changed)) -
        estimate_text_tokens(legacy._json(original))) if changed != original else 0


def final_guidance(scope):
    # The complete native system prompt already contains source subject/order,
    # axes/legend, Korean limits and overlap/contradiction instructions. Remove
    # only their repeated final copy; no source/context/image/schema is removed.
    original = historical.final_guidance(scope)  # Exact original alias guards.
    units = scope['visual_units']
    if not any('source_view' in unit for unit in units):
        return original
    writing = ('wiki_md empty' if scope.get('writing_mode') == 'visual_only'
        else 'wiki_md Korean knowledge prose')
    text = ('JSON only; ' + writing + '; figures=' +
        legacy._json([unit['alias'] for unit in units]) +
        '. printed_values=printed measurements; estimated_values=curves only; no guesses. '
        'Target=ALL panels of this column; context-only, no other ownership.')
    if not 0 < len(text.encode('utf-8')) <= 65536:
        raise AuthoringIncompleteError('Complete final instructions exceed their bounded format.')
    return text


def baseline_reserved_tokens(scope, job):
    if (type(scope) is not dict or type(getattr(job, 'scope_json', None)) is not str
            or type(getattr(job, 'estimated_tokens', None)) is not int
            or not 0 < job.estimated_tokens <= CONTEXT_TOKENS):
        raise AuthoringIncompleteError('Exact original final-instruction reservation required.')
    try:
        original = json.loads(job.scope_json)
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringIncompleteError('Original final-instruction scope changed.') from error
    keys = ('output_contract', 'writing_mode', 'visual_units')
    if type(original) is not dict or any(scope.get(key) != original.get(key) for key in keys):
        raise AuthoringIncompleteError('Final instructions differ from the original owned job.')
    old_text, new_text = writer._final_guidance(original), final_guidance(scope)
    if type(old_text) is not str or not 0 < len(old_text.encode('utf-8')) <= 65536:
        raise AuthoringIncompleteError('Original final instructions changed.')
    baseline = job.estimated_tokens - estimate_text_tokens(old_text) + estimate_text_tokens(new_text)
    if type(baseline) is not int or not 0 < baseline <= CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete replacement final instructions exceed context reservation.')
    return baseline


def _named_bounds(value, *, point=False):
    if type(value) is not dict or set(value) != {'min', 'max'}:
        raise AuthoringOutputError('Named curve bounds require exact min/max fields.')
    # Named meaning, not sorting: reversed values remain reversed and fail.
    return historical._range([value['min'], value['max']], point=point)


def project_named_group(group):
    """Private numeric syntax only; full decode still checks strings/ownership."""
    if type(group) is not dict or set(group) != {
            'subject', 'metric', 'unit', 'conditions', 'series', 'limit',
            'axis_reading', 'condition_values'}:
        raise AuthoringOutputError('Named curve observation fields are incomplete.')
    projected = deepcopy(group)
    axis = group['axis_reading']
    if axis is not None:
        if type(axis) is not dict or set(axis) != {'x_bounds', 'y_bounds', 'x_scale', 'y_scale'}:
            raise AuthoringOutputError('Named curve axes require exact bounds and scales.')
        projected['axis_reading'] = {
            'x_range': _named_bounds(axis['x_bounds']),
            'y_range': _named_bounds(axis['y_bounds']),
            'x_scale': axis['x_scale'], 'y_scale': axis['y_scale']}
    series = group['series']
    if type(series) is not list or not 1 <= len(series) <= 500:
        raise AuthoringOutputError('Named curve series require explicit values and support.')
    projected['series'] = []
    for row in series:
        if type(row) is not dict or set(row) != {'name', 'values', 'observed_x_bounds'}:
            raise AuthoringOutputError('Named curve series require exact support fields.')
        support = row['observed_x_bounds']
        projected['series'].append({'name': deepcopy(row['name']), 'values': deepcopy(row['values']),
            'observed_x_range': None if support is None else _named_bounds(support, point=True)})
    historical._validate_group(projected)
    return projected


def _parse_project(text):
    if type(text) is not str or not 0 < len(text.encode('utf-8')) <= 65536:
        raise AuthoringOutputError('Invalid named curve-domain response.')
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError('Repeated field')
            value[key] = child
        return value
    def nonfinite(_):
        raise ValueError('Nonfinite constant')
    try:
        raw = json.loads(text, object_pairs_hook=unique, parse_constant=nonfinite)
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringOutputError('Invalid named curve-domain JSON.') from error
    if (type(raw) is not dict or set(raw) != {'wiki_md', 'figures'}
            or type(raw['figures']) is not dict):
        raise AuthoringOutputError('Named curve response requires exact original root fields.')
    projected = deepcopy(raw)
    for alias, record in raw['figures'].items():
        if (type(record) is not dict or set(record) != {
                'printed_values', 'estimated_values', 'relations', 'uncertainties'}
                or type(record['estimated_values']) is not list or len(record['estimated_values']) > 500):
            raise AuthoringOutputError('Invalid named curve visual record.')
        projected['figures'][alias]['estimated_values'] = [
            project_named_group(group) for group in record['estimated_values']]
    return raw, projected


def decode(text, scope):
    raw, projected = _parse_project(text)
    document = historical.decode(legacy._json(projected), scope)
    document['figures'] = raw['figures']
    return document


def render_visual_record(text, scope):
    raw, projected = _parse_project(text)
    _, original_fields = historical._parse_project(legacy._json(projected))
    body = writer._decode_structured(legacy._json(original_fields), scope)
    ledgers = []
    for alias, record in raw['figures'].items():
        for group in record['estimated_values']:
            reading = {key: group[key] for key in ('subject', 'metric', 'unit',
                'axis_reading', 'condition_values')}
            reading['series_support'] = [{'name': row['name'],
                'observed_x_bounds': row['observed_x_bounds']} for row in group['series']]
            ledgers.append('<pre>' + escape('모델 보고 축·관찰 구간(미검증) · ' + alias + '\n'
                '자기 일관성 검사만 수행했으며 축·범례·수치의 원문 정확성은 검증하지 않았습니다.\n'
                + legacy._json(reading)) + '</pre>')
    return body + ('\n\n' + '\n\n'.join(ledgers) if ledgers else '')


def text_partition(plan):
    try:
        forward, configured = plan._layout_plan, plan.chunk_bytes
        context = forward.context_base
        effective = min(configured, TEXT_CHUNK_BYTES)
        valid = (plan.policy_version == VERSION and type(configured) is int
            and 4 <= configured <= 8192 and forward.base.chunk_bytes == effective
            and forward.base.text_partition_policy == TEXT_PARTITION_VERSION
            and context is not None and context.chunk_bytes == configured
            and context.text_partition_policy is None
            and context.source is plan.source and forward.base.source is plan.source
            and context.model_policy == forward.base.model_policy
            and context.work_policy == forward.base.work_policy
            and context.execution_policy == forward.base.execution_policy
            and all(len(chunk.text.encode('utf-8')) <= effective for chunk in plan.chunks))
    except (AttributeError, TypeError) as error:
        raise AuthoringIncompleteError('Exact bounded text partition and canonical context required.') from error
    if not valid:
        raise AuthoringIncompleteError('Exact bounded text partition and canonical context required.')
    return {'version': TEXT_PARTITION_VERSION, 'maximum_chunk_bytes': TEXT_CHUNK_BYTES,
        'configured_chunk_bytes': configured, 'effective_chunk_bytes': effective,
        'context_base_plan_sha256': context.plan_sha256}


def _text_only(scope, job):
    return (scope.get('writing_mode') == 'knowledge'
        and scope.get('visual_units') == [] and job.images == ())


def _mandatory_guidance(scope, job):
    if _text_only(scope, job):
        return TEXT_OUTPUT_GUIDANCE
    bindings = checked_subject_bindings(scope, job.images)
    text = OUTPUT_GUIDANCE + image_scope_guidance(scope, job.images)
    if bindings:
        literals = [{'alias': row['figure_alias'], 'subject': row['selected_subject']} for row in bindings]
        text += ('\nExact caption-order subjects (source data): ' + legacy._json(literals) +
            '. No other/aggregate subjects; legends/values unverified.\n')
    return text


def _allocation_inputs(plan, scope, job):
    try:
        ceiling, floor = plan.model_policy.max_tokens, plan._layout_plan.base.model_policy.max_tokens
        valid = (plan.policy_version == VERSION and type(ceiling) is int and ceiling == OUTPUT_CEILING
            and type(floor) is int and floor == OUTPUT_FLOOR and type(scope) is dict
            and scope.get('writing_mode') in ('knowledge', 'visual_only'))
    except AttributeError as error:
        raise AuthoringIncompleteError('Exact named-bound policy and planning floor required.') from error
    if not valid:
        raise AuthoringIncompleteError('Exact named-bound policy and planning floor required.')
    baseline = baseline_reserved_tokens(scope, job)
    mandatory = _mandatory_guidance(scope, job)
    schema_and_labels = additional_schema_tokens(scope) + _image_label_tokens(job.images)
    reserved = baseline + schema_and_labels + estimate_text_tokens(mandatory)
    if reserved > CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete mandatory input/schema exceeds context reservation.')
    return baseline, mandatory, schema_and_labels, reserved


def output_tokens(plan, scope, job):
    _, _, _, reserved = _allocation_inputs(plan, scope, job)
    if job.images or scope['visual_units'] or scope['writing_mode'] == 'visual_only':
        return OUTPUT_FLOOR
    return min(OUTPUT_CEILING, OUTPUT_FLOOR + CONTEXT_TOKENS - reserved)


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    baseline, text, schema_and_labels, _ = _allocation_inputs(plan, scope, job)
    cap = output_tokens(plan, scope, job)
    headroom = CONTEXT_TOKENS - baseline - (cap - OUTPUT_FLOOR)
    if maximum_tokens is not None and (type(maximum_tokens) is not int or maximum_tokens < 0):
        raise AuthoringIncompleteError('A bounded integer supplemental reservation is required.')
    maximum_tokens = headroom if maximum_tokens is None else min(maximum_tokens, headroom)
    maximum_tokens -= schema_and_labels
    if estimate_text_tokens(text) > maximum_tokens:
        raise AuthoringWorkLimitError('Complete mandatory input/schema/output exceeds context reservation.')
    intro = '\nSame-source literal context, not verified definitions or extra ownership:\n'
    selected = []
    for row in collect_term_contexts(plan, scope, job):
        if estimate_text_tokens(text + intro + legacy._json(selected + [row])) <= maximum_tokens:
            selected.append(row)
    if selected:
        text += intro + legacy._json(selected)
    return text


def request_options(plan):
    if plan.policy_version != VERSION:
        raise AuthoringIncompleteError('The exact forward named-bound policy is required.')
    policy = plan.model_policy
    if policy.provider_order == ('hynix_api',) and policy.model_ids == ('qwen36-local',):
        return {'chat_template_kwargs': {'enable_thinking': False}}
    return {}


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact forward named-bound policy required.')
    result = legacy.assemble(plan, by_id)
    records = collect_literal_records(plan)
    body = render_literal_appendix(records)
    if type(body) is not str:
        raise AuthoringIncompleteError('Exact source appendix renderer changed.')
    separator = '\n' if body else ''
    start = len((result.markdown + separator).encode('utf-8'))
    markdown = result.markdown + separator + body
    if len(markdown.encode('utf-8')) > plan.work_policy.markdown_bytes:
        raise AuthoringWorkLimitError('Complete source appendix exceeds Markdown reservation.')
    manifest = json.loads(result.manifest_json)
    if 'literal_appendix' in manifest or 'figure_roi_inputs' in manifest:
        raise AuthoringIncompleteError('Repeated forward source ledger.')
    manifest['literal_appendix'] = {'version': appendix_version, 'records': records,
        'records_sha256': legacy._hash(legacy._json(records)), 'appendix_sha256': legacy._hash(body),
        'output_byte_range': [start, start + len(body.encode('utf-8'))], 'semantic_accuracy_verified': False}
    supplied = {unit['figure_roi']['unit_id'] for job in plan._layout_plan.jobs
        for unit in json.loads(job.scope_json)['visual_units'] if 'figure_roi' in unit}
    manifest['figure_roi_inputs'] = {'version': ROI_INPUT_VERSION,
        'sources': [checked_figure_roi(roi) for roi in plan.figure_rois],
        'delivered_unit_ids': sorted(supplied), 'semantic_accuracy_verified': False}
    manifest['wiki_sha256'] = legacy._hash(markdown)
    manifest['wiki_bytes'] = len(markdown.encode('utf-8'))
    encoded = legacy._json(manifest)
    if len(encoded.encode('utf-8')) > plan.work_policy.manifest_bytes:
        raise AuthoringWorkLimitError('Complete named-bound manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
