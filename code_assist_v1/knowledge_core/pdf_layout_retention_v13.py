"""Forward V24: lossless bounded text units with canonical visual context.

The V23 prompt/guidance, schema, images, output caps and strict decoder remain
unchanged. Every source code point is partitioned into unique writing chunks;
pure visual jobs retain the separately admitted original context partition.
No continuation, source clipping, body repair or semantic review is introduced.
"""
import json
import re

from . import pdf_layout_retention_v12 as previous
from . import pdf_layout_retention as legacy
from .pdf_layout_retention_v4 import _image_label_tokens
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v13'
DECODER_VERSION = 'pdf-layout-retention-v13'
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
SUPPLEMENTAL_BUDGET_VERSION = previous.SUPPLEMENTAL_BUDGET_VERSION
TEXT_PARTITION_VERSION = 'pdf-layout-text-partition-v1'
TEXT_CHUNK_BYTES = 2048
FINAL_GUIDANCE_VERSION = previous.FINAL_GUIDANCE_VERSION
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
OUTPUT_GUIDANCE = previous.OUTPUT_GUIDANCE
TEXT_OUTPUT_GUIDANCE = previous.TEXT_OUTPUT_GUIDANCE


initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
checked_subject_bindings = previous.checked_subject_bindings
response_format = previous.response_format
additional_schema_tokens = previous.additional_schema_tokens
final_guidance = previous.final_guidance
baseline_reserved_tokens = previous.baseline_reserved_tokens
decode = previous.decode
render_visual_record = previous.render_visual_record


def text_partition(plan):
    """Creation/replay identity of complete fine ownership and coarse context."""
    try:
        forward = plan._layout_plan
        context = forward.context_base
        configured = plan.chunk_bytes
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


def text_ranges(text, byte_limit):
    """Lossless safe boundaries; an oversized unbroken token fails closed."""
    if type(text) is not str or type(byte_limit) is not int or not 4 <= byte_limit <= TEXT_CHUNK_BYTES:
        raise AuthoringIncompleteError('Exact bounded text boundary inputs required.')
    start = 0
    while start < len(text):
        end, used = start, 0
        while end < len(text) and used + len(text[end].encode('utf-8')) <= byte_limit:
            used += len(text[end].encode('utf-8'))
            end += 1
        if end < len(text):
            window = text[start:end]
            families = [[start + m.end() for m in re.finditer(pattern, window)] for pattern in (
                r'\n[ \t]*\n', r'\r\n|\n|\r',
                r'[.!?](?:[ \t]+|(?=\r?\n))|[。！？]', r'\s+')]
            # At the byte ceiling, a boundary immediately before whitespace
            # is safe too; all whitespace remains in the next owned range.
            if text[end].isspace():
                families[-1].append(end)
            middle = start + (end - start) // 2
            preferred = [cuts[-1] for cuts in families if cuts and cuts[-1] >= middle]
            available = [cuts[-1] for cuts in families if cuts]
            if not available:
                raise AuthoringWorkLimitError('A complete source token exceeds the bounded text writing unit.')
            end = (preferred or available)[0]
        yield start, end
        start = end


def collect_term_contexts(plan, scope, job):
    # The original collector's exact seed/source guards remain untouched.
    # Coarse visual_context rows are canonical source chunks, not fine owners.
    forward = plan._layout_plan
    selected = forward.context_base if scope.get('writing_mode') == 'visual_only' else forward.base
    if selected is None or selected.source is not plan.source:
        raise AuthoringIncompleteError('Exact same-source terminology context required.')
    return previous.collect_term_contexts(selected, scope, job)


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
