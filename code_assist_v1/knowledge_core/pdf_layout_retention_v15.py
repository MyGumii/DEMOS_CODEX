"""Forward V26: exact native named-curve instructions, frozen strict decoder.

Only the compiler-owned obsolete native record paragraph is replaced. Source
messages, images, order, ownership and named JSON remain unchanged. Complete
system/final replacements are accounted before every forward request admission.
No response repair, extra job, model call or numerical accuracy claim exists.
"""
import json

from . import pdf_layout_retention_v14 as previous
from . import pdf_layout_retention as legacy
from . import pdf_layout_wiki as writer
from .pdf_layout_retention_v4 import _image_label_tokens, image_scope_guidance
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v15'
DECODER_VERSION = 'pdf-layout-retention-v15'
NATIVE_PROMPT_VERSION = 'pdf-layout-native-named-contract-v1'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v15-native-contract-1'
CURVE_DOMAIN_VERSION = previous.CURVE_DOMAIN_VERSION
ALLOCATION_VERSION = previous.ALLOCATION_VERSION
OUTPUT_FLOOR = previous.OUTPUT_FLOOR
OUTPUT_CEILING = previous.OUTPUT_CEILING
REQUEST_MODE_VERSION = previous.REQUEST_MODE_VERSION
PROMPT = previous.PROMPT
LEDGER_VERSION = previous.LEDGER_VERSION
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = previous.IMAGE_SCOPE_VERSION
FINAL_GUIDANCE_VERSION = previous.FINAL_GUIDANCE_VERSION
TEXT_PARTITION_VERSION = previous.TEXT_PARTITION_VERSION
TEXT_CHUNK_BYTES = previous.TEXT_CHUNK_BYTES
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
TEXT_OUTPUT_GUIDANCE = previous.TEXT_OUTPUT_GUIDANCE
OUTPUT_GUIDANCE = (
    "\nPreserve facts/definitions/conditions/exceptions; undefined stays. "
    "Nonplots: estimated_values=[]; relations/uncertainties; printed results->printed_values. "
    "Read each panel/group's drawn axes/support; frame may exceed outer printed ticks, never point extrema. "
    "Bounds numeric NOT display order; axis min<max, support min<=max; linear/log10; descending OK. "
    "y only for x in axis+support and y in axis. "
    "No bounds expansion/guesses/units/extrapolation/fill/sort/clamp. "
    "Unreadable axes/support/y null; unknown x null; readable estimates unverified.\n"
)

_OLD_NATIVE_RECORD = """estimated_values의
각 객체는 subject, metric, unit, conditions, series, limit입니다. conditions는
x축 조건의 순서 배열이고 series의 각 {name,values}는 한 범례와 같은 순서의
숫자/null 배열입니다. 수치는 근삿값이며 원시 데이터가 복원된 것처럼 쓰면 안 됩니다."""
_NEW_NATIVE_RECORD = (
    "estimated_values: {subject,metric,unit,conditions,condition_values,axis_reading,series,limit}. "
    "conditions: ONE text label per x (no axis title/packed list); condition_values:numeric/null x. "
    "series={name,values,observed_x_bounds}; values:numeric/null y. "
    "All three arrays:same N/paired order. "
    "axis_reading:null or {x_bounds,y_bounds,x_scale,y_scale}; bounds={min,max}. "
    "Unverified approximates, not recovered raw data."
)

initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
checked_subject_bindings = previous.checked_subject_bindings
collect_term_contexts = previous.collect_term_contexts
text_ranges = previous.text_ranges
response_format = previous.response_format
additional_schema_tokens = previous.additional_schema_tokens
final_guidance = previous.final_guidance
project_named_group = previous.project_named_group
decode = previous.decode
render_visual_record = previous.render_visual_record
_text_only = previous._text_only


def native_prompt():
    original = writer.NATIVE_VIEW_PROMPT
    if type(original) is not str or original.count(_OLD_NATIVE_RECORD) != 1:
        raise AuthoringIncompleteError('The exact frozen native record paragraph is required.')
    return original.replace(_OLD_NATIVE_RECORD, _NEW_NATIVE_RECORD, 1)


def native_prompt_identity():
    return {'version': NATIVE_PROMPT_VERSION, 'prompt_sha256': legacy._hash(native_prompt())}


def _native_scope(scope):
    return any('source_view' in unit for unit in scope['visual_units'])


def request_messages(scope, job):
    """Private compiler instruction replacement; never search source content."""
    if (type(scope) is not dict or type(getattr(job, 'scope_json', None)) is not str
            or type(getattr(job, 'messages_json', None)) is not str):
        raise AuthoringIncompleteError('Exact original native message and scope required.')
    try:
        original = json.loads(job.scope_json)
        messages = json.loads(job.messages_json)
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringIncompleteError('Original native messages or scope changed.') from error
    keys = ('source_sections', 'image_pages', 'writing_mode', 'visual_context',
        'source_term_contexts', 'visual_inventory', 'table_inventory', 'visual_units',
        'tables', 'output_contract')
    if (type(original) is not dict or any(scope.get(key) != original.get(key) for key in keys)
            or type(scope.get('visual_units')) is not list
            or any(type(unit) is not dict for unit in scope['visual_units'])
            or type(messages) is not list or len(messages) != 2
            or any(type(message) is not dict or set(message) != {'role', 'content'}
                or type(message['content']) is not str for message in messages)
            or [message['role'] for message in messages] != ['system', 'user']):
        raise AuthoringIncompleteError('Native instructions differ from the original owned job.')
    if not _native_scope(scope):
        if messages[0]['content'] == writer.NATIVE_VIEW_PROMPT:
            raise AuthoringIncompleteError('A native instruction requires its original owned column.')
        return messages
    units = scope['visual_units']
    view = units[0].get('source_view')
    if (scope.get('output_contract') != writer.VERSION_V5
            or scope.get('writing_mode') != 'visual_only' or len(units) != 1
            or type(view) is not dict or type(view.get('target_image_index')) is not int
            or view['target_image_index'] != 0 or type(view.get('context_image_index')) is not int
            or view['context_image_index'] != 1 or view.get('context_only') is not True
            or type(getattr(job, 'images', None)) is not tuple or len(job.images) != 2
            or messages[0]['content'] != writer.NATIVE_VIEW_PROMPT):
        raise AuthoringIncompleteError('Exact frozen native system and two source images required.')
    messages[0]['content'] = native_prompt()
    return messages


def baseline_reserved_tokens(scope, job):
    baseline = previous.baseline_reserved_tokens(scope, job)
    transformed = request_messages(scope, job)
    original = json.loads(job.messages_json)
    baseline += (estimate_text_tokens(transformed[0]['content'])
        - estimate_text_tokens(original[0]['content']))
    if type(baseline) is not int or not 0 < baseline <= CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete native instruction replacement exceeds context reservation.')
    return baseline


def _mandatory_guidance(scope, job):
    if _text_only(scope, job):
        return TEXT_OUTPUT_GUIDANCE
    if not _native_scope(scope):
        return previous._mandatory_guidance(scope, job)
    bindings = checked_subject_bindings(scope, job.images)
    text = OUTPUT_GUIDANCE + image_scope_guidance(scope, job.images)
    if bindings:
        literals = [{'alias': row['figure_alias'], 'subject': row['selected_subject']} for row in bindings]
        text += ('\nExact caption-order subjects (source data): ' + legacy._json(literals) +
            '. No other/aggregate subjects; legends/values unverified.\n')
    return text


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
