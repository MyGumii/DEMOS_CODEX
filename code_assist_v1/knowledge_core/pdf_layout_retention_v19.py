"""Forward complete-source packing; strict original responses remain authoritative.

Reuse the existing 8192-byte source partition and greedy section packer, not a
new extraction or review pipeline. Admission counts the compiled system, full
JSON schema, guidance, image labels, image reservation and output before any
provider request. Native tables, source pixels and numerical guards are retained.
"""
from dataclasses import replace
from hashlib import sha256
import json
import re
from types import SimpleNamespace

from . import pdf_layout_retention_v18 as previous
from . import pdf_layout_retention as legacy
from . import pdf_batch_markdown as prose
from .pdf_layout_math_format import normalize_markdown
from .pdf_layout_retention_v4 import _image_label_tokens
from .pdf_layout_term_context import collect_term_contexts as _source_term_contexts
from .pdf_section_writing import CONTEXT_TOKENS, SAFETY_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringOutputError, AuthoringWorkLimitError
from ..llm_images import prepare_image_messages
from ..llm_wire import chat_payload, encode_chat_payload

VERSION = 'wiki-pdf-layout-retention-v19'
DECODER_VERSION = 'pdf-layout-retention-v19'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v19-complete-packing-1'
TEXT_PARTITION_VERSION = 'pdf-layout-complete-source-packing-v1'
TEXT_CHUNK_BYTES = 8192
STRUCTURED_PROMPT_VERSION = previous.STRUCTURED_PROMPT_VERSION
TEXT_PROMPT_VERSION = 'pdf-layout-text-markdown-contract-v1'
TEXT_RESPONSE_CONTRACT_VERSION = 'raw-markdown-v1'
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
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
_OLD_TEXT_ENDING = 'source_sections 범위 끝까지 작성한 뒤 wiki_md 문자열과 JSON 객체를 완결하세요.'
if previous.TEXT_OUTPUT_GUIDANCE.count(_OLD_TEXT_ENDING) != 1:
    raise AuthoringIncompleteError('Exact frozen complete-source text guidance required.')
TEXT_OUTPUT_GUIDANCE = previous.TEXT_OUTPUT_GUIDANCE.replace(_OLD_TEXT_ENDING,
    'source_sections 범위 끝까지 작성한 뒤 Markdown 본문을 완결하세요.',1)
OUTPUT_GUIDANCE = previous.OUTPUT_GUIDANCE

initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
checked_subject_bindings = previous.checked_subject_bindings
project_named_group = previous.project_named_group
render_visual_record = previous.render_visual_record
structured_prompt = previous.structured_prompt
structured_prompt_identity = previous.structured_prompt_identity
native_prompt = previous.native_prompt
native_prompt_identity = previous.native_prompt_identity
_native_scope = previous._native_scope
_text_only = previous._text_only
_SOURCE_WIRE_KEYS = previous._SOURCE_WIRE_KEYS


def _raw_text_scope(scope):
    return (type(scope) is dict and scope.get('writing_mode') == 'knowledge'
        and scope.get('visual_units') == [] and scope.get('image_pages') == []
        and scope.get('text_response_contract') == TEXT_RESPONSE_CONTRACT_VERSION
        and scope.get('request_packing_policy') == TEXT_PARTITION_VERSION)


def text_prompt():
    """Frozen preservation prefix plus the existing natural-Markdown ending."""
    marker = '출력은 JSON 객체 하나입니다.'
    original = previous.PROMPT
    ending = '출력은 자연스러운 Markdown 본문만입니다.'
    if original.count(marker) != 1 or prose.PROMPT.count(ending) != 1:
        raise AuthoringIncompleteError('Exact frozen prose semantic prefix required.')
    return original.split(marker,1)[0] + ending + prose.PROMPT.split(ending,1)[1]


def text_prompt_identity():
    return {'version': TEXT_PROMPT_VERSION,'prompt_sha256': legacy._hash(text_prompt())}


def text_response_contract_identity():
    return {'version': TEXT_RESPONSE_CONTRACT_VERSION,'decoder_version': prose.DECODER_VERSION,
        'prompt_sha256': legacy._hash(text_prompt())}


def request_messages(scope, job):
    messages = previous.request_messages(scope,job)
    if _raw_text_scope(scope):
        if job.images != () or messages[0]['content'] != previous.text_prompt():
            raise AuthoringIncompleteError('Exact original text-only compiler system required.')
        messages[0]['content'] = text_prompt()
    return messages


def response_format(scope):
    return None if _raw_text_scope(scope) else previous.response_format(scope)


def final_guidance(scope):
    if _raw_text_scope(scope):
        return ('현재 source_sections의 끝까지 상세한 한국어 지식 Markdown 본문만 작성하세요. '
            '## 실제 한국어 주제로 시작하고 조건·예외·한계를 보존하세요. '
            '표 값은 서버가 조립합니다. JSON·링크·HTML·코드 펜스는 쓰지 마세요.')
    return previous.final_guidance(scope)


def _mandatory_guidance(scope,job):
    return TEXT_OUTPUT_GUIDANCE if _raw_text_scope(scope) else previous._mandatory_guidance(scope,job)


def decode(text, scope):
    if not _raw_text_scope(scope):
        return previous.decode(text,scope)
    result = prose.decode(text,scope)
    # The raw producer removes serialization only, not the old restriction on
    # inventing model-owned source figure sections in knowledge prose.
    if re.search(r'^### (?:그림|시각자료) .+ · p\. \d+(?: · 열 \d+/\d+)?$',
            result['markdown'],re.M):
        raise AuthoringOutputError('Structured prose cannot introduce model-owned figure sections')
    result['markdown'] = normalize_markdown(result['markdown'])
    result['figures'] = {}
    return result


def additional_schema_tokens(scope):
    # Unlike historical delta accounting, the baseline below already reserves
    # the complete compiled named schema, not merely its historical delta.
    response_format(scope)
    return 0


def baseline_reserved_tokens(scope, job):
    try:
        original = json.loads(job.scope_json)
        valid = (type(scope) is dict and scope == original
            and original.get('request_packing_policy') == TEXT_PARTITION_VERSION
            and type(job.images) is tuple and len(job.images) <= 2)
    except (AttributeError, TypeError, ValueError, RecursionError) as error:
        raise AuthoringIncompleteError('Exact complete-source compiler scope required.') from error
    if not valid:
        raise AuthoringIncompleteError('Exact complete-source compiler scope required.')
    messages = request_messages(scope, job)
    form = response_format(scope)
    tokens = (sum(estimate_text_tokens(message['content']) for message in messages)
        + estimate_text_tokens(final_guidance(scope))
        + (estimate_text_tokens(legacy._json(form)) if form is not None else 0)
        + 4096 * len(job.images) + OUTPUT_FLOOR + SAFETY_TOKENS)
    if not 0 < tokens <= CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete compiled source/schema exceeds context reservation.')
    return tokens


def text_partition(plan):
    try:
        forward, configured = plan._layout_plan, plan.chunk_bytes
        base = forward.base
        valid = (plan.policy_version == VERSION and type(configured) is int
            and 4 <= configured <= TEXT_CHUNK_BYTES and base.chunk_bytes == configured
            and base.text_partition_policy is None and forward.context_base is None
            and forward.owned_chunk_limit is None
            and forward.packing_policy == TEXT_PARTITION_VERSION
            and base.source is plan.source
            and all(len(chunk.text.encode('utf-8')) <= configured for chunk in plan.chunks))
    except (AttributeError, TypeError) as error:
        raise AuthoringIncompleteError('Exact complete-source partition required.') from error
    if not valid:
        raise AuthoringIncompleteError('Exact complete-source partition required.')
    return {'version': TEXT_PARTITION_VERSION, 'maximum_chunk_bytes': TEXT_CHUNK_BYTES,
        'configured_chunk_bytes': configured, 'effective_chunk_bytes': configured,
        'ownership': 'all_original_block_code_points_exact_once',
        'packing': 'existing_greedy_complete_compiled_requests'}


def collect_term_contexts(plan, scope, job):
    forward = plan._layout_plan
    selected = forward.base
    if (forward.packing_policy != TEXT_PARTITION_VERSION or forward.context_base is not None
            or selected.source is not plan.source):
        raise AuthoringIncompleteError('Exact same-source complete terminology context required.')
    return _source_term_contexts(selected, scope, job)


def _allocation_inputs(plan, scope, job):
    try:
        ceiling, floor = plan.model_policy.max_tokens, plan._layout_plan.base.model_policy.max_tokens
        valid = (plan.policy_version == VERSION and type(ceiling) is int and ceiling == OUTPUT_CEILING
            and type(floor) is int and floor == OUTPUT_FLOOR
            and plan._layout_plan.packing_policy == TEXT_PARTITION_VERSION
            and type(scope) is dict and scope.get('writing_mode') in ('knowledge', 'visual_only'))
    except AttributeError as error:
        raise AuthoringIncompleteError('Exact complete-source allocation required.') from error
    if not valid:
        raise AuthoringIncompleteError('Exact complete-source allocation required.')
    baseline = baseline_reserved_tokens(scope, job)
    mandatory = _mandatory_guidance(scope, job)
    labels = _image_label_tokens(job.images)
    reserved = baseline + labels + estimate_text_tokens(mandatory)
    if reserved > CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete mandatory input/schema exceeds context reservation.')
    return baseline, mandatory, labels, reserved


def output_tokens(plan, scope, job):
    _, _, _, reserved = _allocation_inputs(plan, scope, job)
    if job.images or scope['visual_units'] or scope['writing_mode'] == 'visual_only':
        return OUTPUT_FLOOR
    return min(OUTPUT_CEILING, OUTPUT_FLOOR + CONTEXT_TOKENS - reserved)


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    baseline, text, labels, _ = _allocation_inputs(plan, scope, job)
    cap = output_tokens(plan, scope, job)
    headroom = CONTEXT_TOKENS - baseline - (cap - OUTPUT_FLOOR)
    if maximum_tokens is not None and (type(maximum_tokens) is not int or maximum_tokens < 0):
        raise AuthoringIncompleteError('A bounded integer supplemental reservation is required.')
    maximum_tokens = headroom if maximum_tokens is None else min(maximum_tokens, headroom)
    maximum_tokens -= labels
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
        raise AuthoringIncompleteError('The exact complete-source policy is required.')
    if plan.model_policy.provider_order == ('hynix_api',) and plan.model_policy.model_ids == ('qwen36-local',):
        return {'chat_template_kwargs': {'enable_thinking': False}}
    return {}


def compiled_job_payload(base, job):
    """Creation-only, pure complete-wire admission for the existing greedy packer.

    No live plan, callback, model, retained state or publication authority is
    granted by this budget view. Runtime still freshly verifies its typed seal,
    scope, source and request under the ordinary canonical workflow.
    """
    if base.model_policy.max_tokens != OUTPUT_FLOOR:
        raise AuthoringIncompleteError('Exact source packing output floor required.')
    view = SimpleNamespace(policy_version=VERSION, source=base.source,
        model_policy=replace(base.model_policy, max_tokens=OUTPUT_CEILING),
        _layout_plan=SimpleNamespace(base=base, context_base=None,
            packing_policy=TEXT_PARTITION_VERSION))
    scope = json.loads(job.scope_json)
    extra = supplemental_guidance(view, scope, job)
    messages = request_messages(scope, job) + [{'role': 'user',
        'content': final_guidance(scope) + extra}]
    prepared = prepare_image_messages(messages, job.images, inline=True) if job.images else messages
    model = dict(view.model_policy.wire_models or {}).get((view.model_policy.model_ids or ('',))[0])
    if view.model_policy.provider_order != ('hynix_api',) or not model:
        raise AuthoringIncompleteError('Exact OpenAI-compatible packing model required.')
    cap = output_tokens(view, scope, job)
    form = response_format(scope)
    payload = chat_payload(model, prepared, max_tokens=cap, temperature=.1,
        response_format=form, **request_options(view))
    encoded = encode_chat_payload(payload)
    text_tokens = 0
    for message in prepared:
        content = message['content']
        if type(content) is str:
            text_tokens += estimate_text_tokens(content)
        else:
            text_tokens += sum(estimate_text_tokens(part['text']) for part in content
                if part['type'] == 'text')
    complete = (text_tokens + (estimate_text_tokens(legacy._json(form)) if form is not None else 0)
        + 4096 * len(job.images) + cap + SAFETY_TOKENS)
    if complete > CONTEXT_TOKENS or len(encoded) > view.model_policy.request_budget_bytes:
        raise AuthoringWorkLimitError('Complete compiled request exceeds its reservation.')
    return payload, encoded, {'complete_reserved_tokens': complete,
        'output_tokens': cap, 'wire_bytes': len(encoded),
        'wire_sha256': sha256(encoded).hexdigest()}


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact complete-source packing policy required.')
    text_partition(plan)
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
        raise AuthoringIncompleteError('Repeated complete-source ledger.')
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
        raise AuthoringWorkLimitError('Complete source manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
