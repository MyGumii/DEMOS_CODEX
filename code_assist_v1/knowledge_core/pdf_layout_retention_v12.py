"""Forward V23: finite first-writing guidance for text-only knowledge jobs.

The V22 prompt, complete owned jobs/source, images, schema, allocation and
strict raw-response checks remain unchanged. Only the text-only mandatory
suffix changes; visual guidance and optional context remain byte-identical.
No short summary, source clipping, continuation, repair or model review exists.
"""
import json

from . import pdf_layout_retention_v11 as previous
from . import pdf_layout_retention as legacy
from .pdf_layout_retention_v4 import _image_label_tokens
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v12'
DECODER_VERSION = 'pdf-layout-retention-v12'
ALLOCATION_VERSION = previous.ALLOCATION_VERSION
OUTPUT_FLOOR = previous.OUTPUT_FLOOR
OUTPUT_CEILING = previous.OUTPUT_CEILING
REQUEST_MODE_VERSION = previous.REQUEST_MODE_VERSION
PROMPT = previous.PROMPT
CURVE_DOMAIN_VERSION = previous.CURVE_DOMAIN_VERSION
LEDGER_VERSION = previous.LEDGER_VERSION
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = previous.IMAGE_SCOPE_VERSION
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v12-finite-text-1'
FINAL_GUIDANCE_VERSION = previous.FINAL_GUIDANCE_VERSION
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
OUTPUT_GUIDANCE = previous.OUTPUT_GUIDANCE
TEXT_OUTPUT_GUIDANCE = (
    '\n현재 source_sections를 끝까지 읽고 이 범위의 고유 사실·정의·수치·기호·방법·'
    '가정·조건·인과관계·논리·예외·한계를 각각 한 번씩 빠짐없이 보존하세요. '
    '보조 term/table context는 신규 본문 작성 범위가 아니므로 재전사하지 마세요. '
    '일반 배경을 덧붙이거나 이미 설명한 내용을 중복 재설명하거나 반복 요약하지 마세요. '
    '사실을 누락하는 요약이나 길이 잘라내기 없이 현재 출력 예산 안에서 '
    'source_sections 범위 끝까지 작성한 뒤 wiki_md 문자열과 JSON 객체를 완결하세요.\n')

initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
checked_subject_bindings = previous.checked_subject_bindings
collect_term_contexts = previous.collect_term_contexts
response_format = previous.response_format
additional_schema_tokens = previous.additional_schema_tokens
final_guidance = previous.final_guidance
baseline_reserved_tokens = previous.baseline_reserved_tokens
decode = previous.decode
render_visual_record = previous.render_visual_record


def _text_only(scope, job):
    return (scope.get('writing_mode') == 'knowledge'
        and scope.get('visual_units') == [] and job.images == ())


def _mandatory_guidance(scope, job):
    if _text_only(scope, job):
        return TEXT_OUTPUT_GUIDANCE
    return previous._mandatory_guidance(scope, job)


def _allocation_inputs(plan, scope, job):
    # Creation-only allocation runs before the new descriptor/seal exists.
    try:
        ceiling = plan.model_policy.max_tokens
        floor = plan._layout_plan.base.model_policy.max_tokens
        valid = (plan.policy_version == VERSION and type(ceiling) is int
            and ceiling == OUTPUT_CEILING and type(floor) is int and floor == OUTPUT_FLOOR
            and type(scope) is dict and scope.get('writing_mode') in ('knowledge', 'visual_only'))
    except AttributeError as error:
        raise AuthoringIncompleteError('Exact forward finite-text policy and planning floor required.') from error
    if not valid:
        raise AuthoringIncompleteError('Exact forward finite-text policy and planning floor required.')
    baseline = baseline_reserved_tokens(scope, job)
    mandatory = _mandatory_guidance(scope, job)
    schema_and_labels = additional_schema_tokens(scope) + _image_label_tokens(job.images)
    reserved = baseline + schema_and_labels + estimate_text_tokens(mandatory)
    if reserved > CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete mandatory input/schema exceeds context reservation.')
    return baseline, mandatory, schema_and_labels, reserved


def output_tokens(plan, scope, job):
    """Unchanged V22 allocation rule, admitted with the exact new suffix."""
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
        raise AuthoringIncompleteError('The exact forward finite-text policy is required.')
    policy = plan.model_policy
    if policy.provider_order == ('hynix_api',) and policy.model_ids == ('qwen36-local',):
        return {'chat_template_kwargs': {'enable_thinking': False}}
    return {}


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact forward finite-text policy required.')
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
        raise AuthoringWorkLimitError('Complete finite-text manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
