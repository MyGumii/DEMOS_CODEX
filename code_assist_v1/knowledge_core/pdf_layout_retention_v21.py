"""Forward native x-category clarification; no changed numerical contract.

V20 remains immutable. Ordinary source/schema/options are identical; only the
native system prompt clarifies categories versus the image-view column. Its
actual extra tokens are admitted before execution. No repair or extra call.
"""
from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace

from . import pdf_layout_retention_v20 as previous
from . import pdf_layout_retention as legacy
from .pdf_layout_retention_v20 import (
    ALLOCATION_VERSION, CURVE_DOMAIN_VERSION, FINAL_GUIDANCE_VERSION,
    IMAGE_SCOPE_VERSION, LEDGER_VERSION, LITERAL_APPENDIX_VERSION, OUTPUT_CEILING,
    OUTPUT_FLOOR, OUTPUT_GUIDANCE, PROMPT, REQUEST_MODE_VERSION, ROI_INPUT_VERSION,
    STRUCTURED_PROMPT_VERSION, SUBJECT_BINDING_VERSION, SUPPLEMENTAL_BUDGET_VERSION,
    TERM_CONTEXT_VERSION, TEXT_CHUNK_BYTES, TEXT_OUTPUT_GUIDANCE,
    TEXT_PARTITION_VERSION, TEXT_PROMPT_VERSION, TEXT_RESPONSE_CONTRACT_VERSION,
    initialize, seal_admitted_plan, checked_admitted_plan, job_for, images, request,
    checked_subject_bindings, structured_prompt, structured_prompt_identity,
    text_prompt, text_prompt_identity, text_response_contract_identity,
    response_format, additional_schema_tokens, final_guidance, collect_term_contexts,
    decode, render_visual_record, project_named_group,
)
from .pdf_section_writing import CONTEXT_TOKENS, SAFETY_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError
from ..llm_images import prepare_image_messages
from ..llm_wire import chat_payload, encode_chat_payload

VERSION = 'wiki-pdf-layout-retention-v21'
DECODER_VERSION = 'pdf-layout-retention-v21'
NATIVE_PROMPT_VERSION = 'pdf-layout-native-categorical-contract-v2'
_OLD_CONDITIONS = 'conditions: one original label per column; '
_NEW_CONDITIONS = (
    'conditions: each distinct x tick/category of the owned panel, '
    'NOT axis title or view ordinal; '
)
if previous.native_prompt().count(_OLD_CONDITIONS) != 1:
    raise AuthoringIncompleteError('Exact frozen native condition instruction required.')
_NATIVE_PROMPT = previous.native_prompt().replace(_OLD_CONDITIONS, _NEW_CONDITIONS)


def native_prompt():
    return _NATIVE_PROMPT


def native_prompt_identity():
    return {'version':NATIVE_PROMPT_VERSION,'prompt_sha256':sha256(native_prompt().encode()).hexdigest()}


def request_messages(scope, job):
    messages = previous.request_messages(scope, job)
    if previous._native_scope(scope):
        if messages[0]['content'] != previous.native_prompt():
            raise AuthoringIncompleteError('Exact frozen native system required.')
        messages[0]['content'] = native_prompt()
    return messages


def baseline_reserved_tokens(scope, job):
    baseline = previous.baseline_reserved_tokens(scope, job)
    if previous._native_scope(scope):
        baseline += estimate_text_tokens(native_prompt())-estimate_text_tokens(previous.native_prompt())
    if baseline > CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete clarified native source/schema exceeds reservation.')
    return baseline


def _budget_view(plan):
    """Private pure-algorithm view, never a typed admission/publication capability."""
    if (plan.policy_version != VERSION
            or getattr(plan._layout_plan,'native_compiler_prompt',None) != NATIVE_PROMPT_VERSION):
        raise AuthoringIncompleteError('Exact clarified native policy and selector required.')
    return SimpleNamespace(policy_version=previous.VERSION,source=plan.source,
        model_policy=plan.model_policy,_layout_plan=plan._layout_plan,
        chunk_bytes=getattr(plan,'chunk_bytes',None),chunks=getattr(plan,'chunks',None))


def text_partition(plan):
    return previous.text_partition(_budget_view(plan))


def output_tokens(plan, scope, job):
    view = _budget_view(plan)
    cap = previous.output_tokens(view, scope, job)
    baseline, _, _, reserved = previous._allocation_inputs(view, scope, job)
    if reserved+baseline_reserved_tokens(scope,job)-baseline > CONTEXT_TOKENS:
        raise AuthoringWorkLimitError('Complete clarified mandatory input exceeds reservation.')
    return cap


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    # Preserve every old optional-context choice. If the longer native system
    # no longer fits, reject the complete payload instead of clipping source or
    # changing an ordinary request to make it fit.
    output_tokens(plan,scope,job)
    return previous.supplemental_guidance(_budget_view(plan),scope,job,maximum_tokens=maximum_tokens)


def request_options(plan):
    return previous.request_options(_budget_view(plan))


def compiled_job_payload(base, job):
    """Creation-only exact wire preflight; normal runtime still checks its seal."""
    if base.model_policy.max_tokens != OUTPUT_FLOOR:
        raise AuthoringIncompleteError('Exact source packing output floor required.')
    view = SimpleNamespace(policy_version=VERSION,source=base.source,
        model_policy=replace(base.model_policy,max_tokens=OUTPUT_CEILING),
        _layout_plan=SimpleNamespace(base=base,context_base=None,packing_policy=TEXT_PARTITION_VERSION,
            native_compiler_prompt=NATIVE_PROMPT_VERSION))
    scope = json.loads(job.scope_json)
    messages = request_messages(scope,job)+[{'role':'user',
        'content':final_guidance(scope)+supplemental_guidance(view,scope,job)}]
    prepared = prepare_image_messages(messages,job.images,inline=True) if job.images else messages
    model = dict(view.model_policy.wire_models or {}).get((view.model_policy.model_ids or ('',))[0])
    if view.model_policy.provider_order != ('hynix_api',) or not model:
        raise AuthoringIncompleteError('Exact OpenAI-compatible packing model required.')
    cap,form = output_tokens(view,scope,job),response_format(scope)
    payload = chat_payload(model,prepared,max_tokens=cap,temperature=.1,response_format=form,**request_options(view))
    encoded = encode_chat_payload(payload)
    text_tokens = sum(estimate_text_tokens(message['content']) if type(message['content']) is str else
        sum(estimate_text_tokens(part['text']) for part in message['content'] if part['type']=='text') for message in prepared)
    complete = text_tokens+(estimate_text_tokens(legacy._json(form)) if form is not None else 0)+4096*len(job.images)+cap+SAFETY_TOKENS
    if complete > CONTEXT_TOKENS or len(encoded) > view.model_policy.request_budget_bytes:
        raise AuthoringWorkLimitError('Complete clarified request exceeds its reservation.')
    return payload,encoded,{'complete_reserved_tokens':complete,'output_tokens':cap,
        'wire_bytes':len(encoded),'wire_sha256':sha256(encoded).hexdigest()}


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact clarified complete-source policy required.')
    text_partition(plan)
    result = legacy.assemble(plan,by_id)
    records = collect_literal_records(plan)
    body = render_literal_appendix(records)
    if type(body) is not str:
        raise AuthoringIncompleteError('Exact source appendix renderer changed.')
    separator = '\n' if body else ''
    start = len((result.markdown+separator).encode())
    markdown = result.markdown+separator+body
    if len(markdown.encode()) > plan.work_policy.markdown_bytes:
        raise AuthoringWorkLimitError('Complete source appendix exceeds Markdown reservation.')
    manifest = json.loads(result.manifest_json)
    if 'literal_appendix' in manifest or 'figure_roi_inputs' in manifest:
        raise AuthoringIncompleteError('Repeated complete-source ledger.')
    manifest['literal_appendix'] = {'version':appendix_version,'records':records,
        'records_sha256':legacy._hash(legacy._json(records)),'appendix_sha256':legacy._hash(body),
        'output_byte_range':[start,start+len(body.encode())],'semantic_accuracy_verified':False}
    supplied = {unit['figure_roi']['unit_id'] for job in plan._layout_plan.jobs
        for unit in json.loads(job.scope_json)['visual_units'] if 'figure_roi' in unit}
    manifest['figure_roi_inputs'] = {'version':ROI_INPUT_VERSION,
        'sources':[checked_figure_roi(roi) for roi in plan.figure_rois],
        'delivered_unit_ids':sorted(supplied),'semantic_accuracy_verified':False}
    manifest['wiki_sha256'],manifest['wiki_bytes'] = legacy._hash(markdown),len(markdown.encode())
    encoded = legacy._json(manifest)
    if len(encoded.encode()) > plan.work_policy.manifest_bytes:
        raise AuthoringWorkLimitError('Complete source manifest exceeds reservation.')
    return AuthoredWiki(markdown,encoded)
