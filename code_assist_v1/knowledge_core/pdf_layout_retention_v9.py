"""Forward V20: request-scoped direct Qwen writing, unchanged source guards.

The local Qwen binding uses its documented per-request non-thinking mode.
Other providers/models and historical policies retain their original wire.
No global server setting, extra review, raw-response repair or source-specific
example is introduced. Direct mode is not a claim of semantic accuracy.
"""
import json

from . import pdf_layout_retention_v8 as previous
from . import pdf_layout_retention as legacy
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v9'
DECODER_VERSION = 'pdf-layout-retention-v9'
REQUEST_MODE_VERSION = 'pdf-authoring-request-mode-v1'
PROMPT = previous.PROMPT
CURVE_DOMAIN_VERSION = previous.CURVE_DOMAIN_VERSION
LEDGER_VERSION = previous.LEDGER_VERSION
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = previous.IMAGE_SCOPE_VERSION
SUPPLEMENTAL_BUDGET_VERSION = previous.SUPPLEMENTAL_BUDGET_VERSION
FINAL_GUIDANCE_VERSION = previous.FINAL_GUIDANCE_VERSION
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
OUTPUT_GUIDANCE = previous.OUTPUT_GUIDANCE

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
supplemental_guidance = previous.supplemental_guidance
decode = previous.decode
render_visual_record = previous.render_visual_record


def request_options(plan):
    if plan.policy_version != VERSION:
        raise AuthoringIncompleteError('The exact forward request-mode policy is required.')
    policy = plan.model_policy
    if policy.provider_order == ('hynix_api',) and policy.model_ids == ('qwen36-local',):
        return {'chat_template_kwargs': {'enable_thinking': False}}
    # Company gateways must not receive llama.cpp-specific parameters merely
    # because they also implement Chat Completions. Their modes need explicit
    # gateway acceptance; this branch preserves portable default requests.
    return {}


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact forward direct-writing policy required.')
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
        raise AuthoringWorkLimitError('Complete direct-writing manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
