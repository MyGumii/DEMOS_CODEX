"""Forward V28: source-complete text instructions and strict unchanged decode.

Only text-only compiler instructions change. Original text/source ownership,
visual/native messages, images, JSON schema and strict decoding stay exact.
No response repair, new writing job or semantic-review model call is added.
Complete text-message changes are reserved before each forward request.
"""
import json

from . import pdf_layout_retention_v16 as previous
from . import pdf_layout_retention as legacy
from . import pdf_layout_wiki as writer
from .pdf_layout_retention_v4 import _image_label_tokens
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v17'
DECODER_VERSION = 'pdf-layout-retention-v17'
STRUCTURED_PROMPT_VERSION = previous.STRUCTURED_PROMPT_VERSION
TEXT_PROMPT_VERSION = 'pdf-layout-text-json-contract-v1'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v17-text-contract-1'
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


structured_prompt = previous.structured_prompt
structured_prompt_identity = previous.structured_prompt_identity


def text_prompt():
    """Preserve the frozen semantic guide; omit unrelated visual-output work."""
    original = writer.STRUCTURED_PROMPT_V5
    marker = '출력은 JSON 객체 하나입니다.'
    if type(original) is not str or original != PROMPT or original.count(marker) != 1:
        raise AuthoringIncompleteError('The exact frozen text semantic prefix is required.')
    prefix = original.split(marker, 1)[0]
    return prefix + (
        "출력은 wiki_md와 figures 두 필드만 가진 JSON 객체 하나입니다. "
        "이번 입력에는 직접 판독할 그림이 없으므로 figures는 빈 객체입니다. "
        "wiki_md는 '## 실제 한국어 주제'로 시작하는 상세한 한국어 지식 Markdown입니다. "
        "현재 source_sections의 끝까지 읽고 핵심 사실·조건·수치·변수 정의·인과관계·"
        "예외·한계를 보존하세요. 표 셀과 별도 그림 판독은 서버가 조립하므로 중복하지 "
        "않고 본문에 명시된 결과와 의미를 설명하세요. source_term_contexts는 같은 "
        "원문의 해석용 문맥이지 새 본문 작성 범위나 검증된 정의가 아닙니다. "
        "수식은 일반 LaTeX로 작성하세요. "
        r"Represent each literal LaTeX backslash in JSON as \\ (two backslashes). "
        "줄바꿈은 JSON의 \\n만 사용하고 \\r·\\b·\\f 등 다른 제어문자를 "
        "생성하지 마세요. 링크·HTML·코드 펜스·내부 ID·작업 설명은 쓰지 마세요. "
        "외부 지식으로 누락을 채우거나 원문의 논리 강도를 높이지 마세요."
    )


def text_prompt_identity():
    return {'version': TEXT_PROMPT_VERSION,
        'prompt_sha256': legacy._hash(text_prompt())}


def request_messages(scope, job):
    """Change only a source-complete text job's compiler-owned system message."""
    messages = previous.request_messages(scope, job)
    if _native_scope(scope) or not _text_only(scope, job):
        return messages
    original = json.loads(job.scope_json)
    if (scope.get('output_contract') != writer.VERSION_V5
            or scope.get('writing_mode') != 'knowledge'
            or messages[0]['content'] != writer.STRUCTURED_PROMPT_V5
            or messages[0]['content'] != PROMPT
            or any(key not in original for key in _SOURCE_WIRE_KEYS)):
        raise AuthoringIncompleteError('Exact frozen text-only system and scope required.')
    try:
        source_wire = legacy._json({key: original[key] for key in _SOURCE_WIRE_KEYS})
    except (ValueError, TypeError, RecursionError) as error:
        raise AuthoringIncompleteError('Original text-only source wire changed.') from error
    if messages[1]['content'] != source_wire:
        raise AuthoringIncompleteError('Original text-only source message changed.')
    messages[0]['content'] = text_prompt()
    return messages


def baseline_reserved_tokens(scope, job):
    baseline = previous.baseline_reserved_tokens(scope, job)
    prior_messages = previous.request_messages(scope, job)
    transformed = request_messages(scope, job)
    baseline += (estimate_text_tokens(transformed[0]['content'])
        - estimate_text_tokens(prior_messages[0]['content']))
    if type(baseline) is not int or not 0 < baseline <= CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete text instruction replacement exceeds context reservation.')
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
