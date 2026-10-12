"""Forward V18: bounded, model-reported curve-domain self-consistency.

This is not axis calibration, pixel support or numerical accuracy proof. Raw
model responses are neither repaired nor clamped. The unchanged historical
decoder sees a private syntax projection; only a fresh V18 plan may use this
adapter. No additional model call or source truncation is introduced.
"""
from copy import deepcopy
from html import escape
import json
import math

from . import pdf_layout_retention_v6 as previous
from . import pdf_layout_retention as legacy
from . import pdf_layout_wiki as writer
from .pdf_layout_retention_v4 import _image_label_tokens, image_scope_guidance
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import (AuthoredWiki, AuthoringIncompleteError,
    AuthoringOutputError, AuthoringWorkLimitError)

VERSION = 'wiki-pdf-layout-retention-v7'
DECODER_VERSION = 'pdf-layout-retention-v7'
CURVE_DOMAIN_VERSION = 'pdf-curve-domain-self-consistency-v1'
LEDGER_VERSION = previous.LEDGER_VERSION
PROMPT = previous.PROMPT
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = 'pdf-layout-image-roles-v8'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v7'
FINAL_GUIDANCE_VERSION = 'pdf-curve-domain-final-guidance-v1'
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
OUTPUT_GUIDANCE = (
    '\nPreserve facts, symbol/method definitions, conditions and exceptions; '
    'undefined terms stay undefined. Group strings nonblank; never infer units. '
    'For numeric estimates report observed axis_reading x/y ranges and linear/log10 '
    'scales, condition_values numeric x, and each series observed_x_range. '
    'Each nonnull y requires x inside axis and that series drawn extent; y inside '
    'axis. Never extrapolate, fill endpoints or invent bounds. Unreadable axes: '
    'axis_reading null, numeric y null; unknown x null. Retain readable approximates '
    'inside observed support. These are unverified model readings.\n')

initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
checked_subject_bindings = previous.checked_subject_bindings
collect_term_contexts = previous.collect_term_contexts


def response_format(scope):
    original = previous.response_format(scope)
    if original is None:
        return None
    form = deepcopy(original)
    bounds = {'type': 'array', 'items': {'type': 'number'}, 'minItems': 2, 'maxItems': 2}
    axis = {'type': 'object', 'additionalProperties': False,
        'required': ['x_range', 'y_range', 'x_scale', 'y_scale'],
        'properties': {'x_range': deepcopy(bounds), 'y_range': deepcopy(bounds),
            'x_scale': {'type': 'string', 'enum': ['linear', 'log10']},
            'y_scale': {'type': 'string', 'enum': ['linear', 'log10']}}}
    records = form['json_schema']['schema']['properties']['figures']['properties']
    for alias, source_record in records.items():
        # Historical schemas intentionally reuse one unbound record object.
        # A whole-form deepcopy preserves that aliasing, so give each NEW
        # alias an independent record before extending its required fields.
        record = deepcopy(source_record)
        records[alias] = record
        group = record['properties']['estimated_values']['items']
        if group.get('type') != 'object' or set(group['properties']) != {
                'subject', 'metric', 'unit', 'conditions', 'series', 'limit'}:
            raise AuthoringIncompleteError('Original grouped curve contract changed.')
        group['required'].extend(['axis_reading', 'condition_values'])
        group['properties']['axis_reading'] = {'anyOf': [{'type': 'null'}, deepcopy(axis)]}
        group['properties']['condition_values'] = {'type': 'array',
            'items': {'type': ['number', 'null']}, 'minItems': 1, 'maxItems': 500}
        series = group['properties']['series']['items']
        if set(series['properties']) != {'name', 'values'}:
            raise AuthoringIncompleteError('Original grouped series contract changed.')
        series['required'].append('observed_x_range')
        series['properties']['observed_x_range'] = {'anyOf': [
            {'type': 'null'}, deepcopy(bounds)]}
    return form


def additional_schema_tokens(scope):
    original, changed = legacy.response_format(scope), response_format(scope)
    return max(0, estimate_text_tokens(legacy._json(changed)) -
        estimate_text_tokens(legacy._json(original))) if changed != original else 0


def final_guidance(scope):
    """Equivalent final instructions without repeating the frozen long prompt.

    This replaces only a compiler-owned final instruction, not owned document
    text, a caption, an image label, or either actual attachment. The complete
    historical system prompt and ordinary source message remain unchanged.
    """
    if type(scope) is not dict or scope.get('output_contract') != writer.VERSION_V5:
        raise AuthoringIncompleteError('Exact original structured scope required for final instructions.')
    units = scope.get('visual_units')
    if (type(units) is not list or len(units) > 4096
            or any(type(u) is not dict or type(u.get('alias')) is not str
                or not u['alias'] or len(u['alias'].encode('utf-8')) > 160 for u in units)
            or len({u['alias'] for u in units}) != len(units)):
        raise AuthoringIncompleteError('Original final-instruction visual aliases changed.')
    writing = ('wiki_md empty' if scope.get('writing_mode') == 'visual_only'
        else 'wiki_md Korean knowledge prose')
    text = ('JSON only; ' + writing + '; all figures ' +
        legacy._json([u['alias'] for u in units]) + '. printed_values=printed; '
        'estimated_values=curves ONLY; no guesses.')
    if any('source_view' in u for u in units):
        text += (' source_view owns column: ALL target panels. '
            'Context=position/reference only, no other columns/values/marks or assumed legend. '
            'Check captions, axes/legend, actual subject(not title); '
            'ordinal/count only explicit caption order. Unreadable:null+limits. '
            'Korean relations/limits. No strict global overlap ranks/own-value '
            'contradictions at same metric/condition.')
    if not 0 < len(text.encode('utf-8')) <= 65536:
        raise AuthoringIncompleteError('Complete final instructions exceed their bounded format.')
    return text


def baseline_reserved_tokens(scope, job):
    """Replace the exact old final-instruction reservation; never alter a job."""
    if (type(scope) is not dict or type(getattr(job, 'scope_json', None)) is not str
            or type(getattr(job, 'estimated_tokens', None)) is not int
            or not 0 < job.estimated_tokens <= CONTEXT_TOKENS):
        raise AuthoringIncompleteError('Exact original final-instruction reservation required.')
    try:
        original = json.loads(job.scope_json)
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringIncompleteError('Original final-instruction scope changed.') from error
    keys = ('output_contract', 'writing_mode', 'visual_units')
    if type(original) is not dict or any(scope.get(k) != original.get(k) for k in keys):
        raise AuthoringIncompleteError('Final instructions differ from the original owned job.')
    old_text, new_text = writer._final_guidance(original), final_guidance(scope)
    if type(old_text) is not str or not 0 < len(old_text.encode('utf-8')) <= 65536:
        raise AuthoringIncompleteError('Original final instructions changed.')
    baseline = job.estimated_tokens - estimate_text_tokens(old_text) + estimate_text_tokens(new_text)
    if type(baseline) is not int or not 0 < baseline <= CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete replacement final instructions exceed context reservation.')
    return baseline


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    headroom = CONTEXT_TOKENS - baseline_reserved_tokens(scope, job)
    maximum_tokens = headroom if maximum_tokens is None else min(maximum_tokens, headroom)
    maximum_tokens -= additional_schema_tokens(scope) + _image_label_tokens(job.images)
    bindings = checked_subject_bindings(scope, job.images)
    text = OUTPUT_GUIDANCE + image_scope_guidance(scope, job.images)
    if bindings:
        literals = [{'alias': row['figure_alias'], 'subject': row['selected_subject']} for row in bindings]
        text += ('\nExact caption-order subjects (source data): ' + legacy._json(literals) +
            '. No other/aggregate subjects; legends/values unverified.\n')
    if estimate_text_tokens(text) > maximum_tokens:
        raise AuthoringWorkLimitError('Complete curve-domain input/schema exceeds context reservation.')
    intro = '\nSame-source literal context, not verified definitions or extra ownership:\n'
    selected = []
    for row in collect_term_contexts(plan, scope, job):
        if estimate_text_tokens(text + intro + legacy._json(selected + [row])) <= maximum_tokens:
            selected.append(row)
    if selected:
        text += intro + legacy._json(selected)
    return text


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _range(value, *, point=False):
    if (type(value) is not list or len(value) != 2 or not all(_number(v) for v in value)
            or (value[0] > value[1] if point else value[0] >= value[1])):
        raise AuthoringOutputError('Curve-domain ranges require finite ordered bounds.')
    return value


def _validate_group(group):
    if type(group) is not dict or set(group) != {
            'subject', 'metric', 'unit', 'conditions', 'series', 'limit',
            'axis_reading', 'condition_values'}:
        raise AuthoringOutputError('Curve-domain observation fields are incomplete.')
    conditions, xs, series, axis = (group['conditions'], group['condition_values'],
        group['series'], group['axis_reading'])
    if (type(conditions) is not list or not 1 <= len(conditions) <= 500
            or type(xs) is not list or len(xs) != len(conditions)
            or any(x is not None and not _number(x) for x in xs)
            or type(series) is not list or not 1 <= len(series) <= 500):
        raise AuthoringOutputError('Numeric curve conditions must align with their labels.')
    numeric_x = [x for x in xs if x is not None]
    if len(numeric_x) > 1 and not (all(a < b for a, b in zip(numeric_x, numeric_x[1:]))
            or all(a > b for a, b in zip(numeric_x, numeric_x[1:]))):
        raise AuthoringOutputError('Numeric curve conditions must have a consistent strict order.')
    if axis is not None:
        if (type(axis) is not dict or set(axis) != {'x_range', 'y_range', 'x_scale', 'y_scale'}
                or axis['x_scale'] not in ('linear', 'log10')
                or axis['y_scale'] not in ('linear', 'log10')):
            raise AuthoringOutputError('Curve axes require exact reported ranges and scales.')
        x_bounds, y_bounds = _range(axis['x_range']), _range(axis['y_range'])
        if ((axis['x_scale'] == 'log10' and x_bounds[0] <= 0)
                or (axis['y_scale'] == 'log10' and y_bounds[0] <= 0)
                or any(not x_bounds[0] <= x <= x_bounds[1] for x in numeric_x)):
            raise AuthoringOutputError('Reported curve conditions lie outside their axis domain.')
    for row in series:
        if (type(row) is not dict or set(row) != {'name', 'values', 'observed_x_range'}
                or type(row['values']) is not list or not 1 <= len(row['values']) <= 500
                or any(y is not None and not _number(y) for y in row['values'])):
            raise AuthoringOutputError('Curve series require unchanged values and observed support.')
        ys, extent = row['values'], row['observed_x_range']
        has_y = any(y is not None for y in ys)
        if has_y and (axis is None or extent is None or len(ys) != len(xs)):
            raise AuthoringOutputError('Numeric estimates require axes and observed series support.')
        if extent is not None:
            _range(extent, point=True)
            if axis is None or not x_bounds[0] <= extent[0] <= extent[1] <= x_bounds[1]:
                raise AuthoringOutputError('Observed series support lies outside the reported axis.')
        if has_y:
            for x, y in zip(xs, ys, strict=True):
                if y is None:
                    continue
                if (x is None or not extent[0] <= x <= extent[1]
                        or not y_bounds[0] <= y <= y_bounds[1]):
                    raise AuthoringOutputError('Numeric estimate lies outside axis or observed series support.')
    # The historical strict decoder also checks all strings, unique series,
    # safe markup, subject ownership and all-null malformed-array diagnostics.
    return {k: deepcopy(v) for k, v in group.items()
        if k not in ('axis_reading', 'condition_values') and k != 'series'} | {
        'series': [{k: deepcopy(v) for k, v in row.items() if k != 'observed_x_range'} for row in series]}


def _parse_project(text):
    if type(text) is not str or not 0 < len(text.encode('utf-8')) <= 65536:
        raise AuthoringOutputError('Invalid structured curve-domain response.')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError('Repeated field')
            out[key] = value
        return out
    def invalid(_):
        raise ValueError('Nonfinite constant')
    try:
        raw = json.loads(text, object_pairs_hook=unique, parse_constant=invalid)
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringOutputError('Invalid structured curve-domain JSON.') from error
    if (type(raw) is not dict or set(raw) != {'wiki_md', 'figures'}
            or type(raw['figures']) is not dict):
        raise AuthoringOutputError('Curve-domain response requires exact original root fields.')
    projected = deepcopy(raw)
    for alias, record in raw['figures'].items():
        if (type(record) is not dict or set(record) != {
                'printed_values', 'estimated_values', 'relations', 'uncertainties'}
                or type(record['estimated_values']) is not list or len(record['estimated_values']) > 500):
            raise AuthoringOutputError('Invalid curve-domain visual record.')
        projected['figures'][alias]['estimated_values'] = [
            _validate_group(group) for group in record['estimated_values']]
    return raw, projected


def decode(text, scope):
    raw, projected = _parse_project(text)
    document = previous.decode(legacy._json(projected), scope)
    # Original fields remain byte-source values. Projection neither changes
    # the saved raw receipt nor substitutes guessed ranges, points or units.
    document['figures'] = raw['figures']
    return document


def render_visual_record(text, scope):
    raw, projected = _parse_project(text)
    body = writer._decode_structured(legacy._json(projected), scope)
    ledgers = []
    for alias, record in raw['figures'].items():
        for group in record['estimated_values']:
            reading = {k: group[k] for k in ('subject', 'metric', 'unit',
                'axis_reading', 'condition_values')}
            reading['series_support'] = [{'name': row['name'],
                'observed_x_range': row['observed_x_range']} for row in group['series']]
            ledgers.append('<pre>' + escape('모델 보고 축·관찰 구간(미검증) · ' + alias + '\n'
                + '자기 일관성 검사만 수행했으며 축·범례·수치의 원문 정확성은 검증하지 않았습니다.\n'
                + legacy._json(reading)) + '</pre>')
    return body + ('\n\n' + '\n\n'.join(ledgers) if ledgers else '')


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact forward curve-domain policy required.')
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
        'records_sha256': legacy._hash(legacy._json(records)),
        'appendix_sha256': legacy._hash(body),
        'output_byte_range': [start, start + len(body.encode('utf-8'))],
        'semantic_accuracy_verified': False}
    supplied = {u['figure_roi']['unit_id'] for job in plan._layout_plan.jobs
        for u in json.loads(job.scope_json)['visual_units'] if 'figure_roi' in u}
    manifest['figure_roi_inputs'] = {'version': ROI_INPUT_VERSION,
        'sources': [checked_figure_roi(roi) for roi in plan.figure_rois],
        'delivered_unit_ids': sorted(supplied), 'semantic_accuracy_verified': False}
    manifest['wiki_sha256'] = legacy._hash(markdown)
    manifest['wiki_bytes'] = len(markdown.encode('utf-8'))
    encoded = legacy._json(manifest)
    if len(encoded.encode('utf-8')) > plan.work_policy.manifest_bytes:
        raise AuthoringWorkLimitError('Complete curve-domain manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
