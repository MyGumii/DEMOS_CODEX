"""Forward V27: exact non-native visual instructions, frozen strict decoder.

Only an obsolete compiler-owned curve paragraph is replaced for non-native
visual jobs. Text-only and current native messages remain byte-identical.
Source, aliases, images, JSON schema, output caps and raw decoding do not change.
The full system-message delta is reserved before every forward request.
No response repair, extra job/model call or semantic accuracy claim exists.
"""
import json

from . import pdf_layout_retention_v15 as previous
from . import pdf_layout_retention as legacy
from . import pdf_layout_wiki as writer
from .pdf_layout_retention_v4 import _image_label_tokens
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v16'
DECODER_VERSION = 'pdf-layout-retention-v16'
STRUCTURED_PROMPT_VERSION = 'pdf-layout-visual-named-contract-v1'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v16-visual-contract-1'
NATIVE_PROMPT_VERSION = previous.NATIVE_PROMPT_VERSION
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
OUTPUT_GUIDANCE = previous.OUTPUT_GUIDANCE

_OLD_STRUCTURED_RECORD = """estimated_values는 같은 대상·지표의 곡선을 묶은 객체 배열입니다. 각 객체는
subject(대상), metric(지표), unit(단위 또는 무차원), conditions(각 x축 조건의
문자열 배열), series(각 범례 계열의 name과 values 수치 배열), limit(판독 한계)를
포함합니다. values 순서는 conditions와 같아야 하고 읽을 수 없는 점은 null입니다.
이 수치는 모두 근삿값이며 서버가 이를 명시합니다. 같은 곡선의 각 점마다 대상과
단위를 반복하는 대신 한 그룹에 묶으세요. 어떤 계열·조건인지 모르거나 곡선이
겹쳐 식별 불가하면 값을 지어내지 말고 uncertainties에 한계를 남기세요."""
_NEW_STRUCTURED_RECORD = (
    "estimated_values: curve groups only, "
    "{subject,metric,unit,conditions,condition_values,axis_reading,series,limit}. "
    "conditions: ONE nonblank text label per x (no axis title/packed list); "
    "condition_values:numeric/null x. series={name,values,observed_x_bounds}; "
    "values:numeric/null y. All three arrays:same N/paired order. "
    "axis_reading:null or {x_bounds,y_bounds,x_scale,y_scale}; bounds={min,max}. "
    "Group/series strings nonblank; source-supported unit or not reported, never inferred. "
    "Unknown subject/metric/series/condition: no guessed group, use uncertainties. "
    "Empty categories use []; printed_values/relations/uncertainties entries are nonblank "
    "strings, never empty/whitespace strings or null. Nonplots: estimated_values=[]. "
    "Readable estimates stay unverified, not recovered raw data. "
    r"Valid JSON strings: write each literal LaTeX backslash as \\ (two backslashes), "
    r"never as \b or \f control escapes. Keep \n, \r, \t only for intended layout; "
    "no other decoded control characters."
)
_SOURCE_WIRE_KEYS = ('source_sections', 'image_pages', 'writing_mode', 'visual_context',
    'source_term_contexts', 'visual_inventory', 'table_inventory')

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
native_prompt = previous.native_prompt
native_prompt_identity = previous.native_prompt_identity
_native_scope = previous._native_scope
_text_only = previous._text_only
_mandatory_guidance = previous._mandatory_guidance


def structured_prompt():
    original = writer.STRUCTURED_PROMPT_V5
    if (type(original) is not str or original != PROMPT
            or original.count(_OLD_STRUCTURED_RECORD) != 1):
        raise AuthoringIncompleteError('The exact frozen structured curve paragraph is required.')
    return original.replace(_OLD_STRUCTURED_RECORD, _NEW_STRUCTURED_RECORD, 1)


def structured_prompt_identity():
    return {'version': STRUCTURED_PROMPT_VERSION,
        'prompt_sha256': legacy._hash(structured_prompt())}


def request_messages(scope, job):
    """Replace only the exact compiler system paragraph, never source content."""
    messages = previous.request_messages(scope, job)
    if _native_scope(scope) or not scope['visual_units']:
        return messages
    original = json.loads(job.scope_json)
    if (scope.get('output_contract') != writer.VERSION_V5
            or scope.get('writing_mode') not in ('knowledge', 'visual_only')
            or messages[0]['content'] != writer.STRUCTURED_PROMPT_V5
            or messages[0]['content'] != PROMPT
            or any(key not in original for key in _SOURCE_WIRE_KEYS)):
        raise AuthoringIncompleteError('Exact frozen non-native visual system and scope required.')
    try:
        source_wire = legacy._json({key: original[key] for key in _SOURCE_WIRE_KEYS})
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringIncompleteError('Original non-native visual source wire changed.') from error
    if messages[1]['content'] != source_wire:
        raise AuthoringIncompleteError('Original non-native visual source message changed.')
    messages[0]['content'] = structured_prompt()
    return messages


def baseline_reserved_tokens(scope, job):
    baseline = previous.baseline_reserved_tokens(scope, job)
    prior_messages = previous.request_messages(scope, job)
    transformed = request_messages(scope, job)
    baseline += (estimate_text_tokens(transformed[0]['content'])
        - estimate_text_tokens(prior_messages[0]['content']))
    if type(baseline) is not int or not 0 < baseline <= CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete visual instruction replacement exceeds context reservation.')
    return baseline


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
