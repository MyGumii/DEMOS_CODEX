"""Forward V21: explicit curve-array and source-bound range writing guidance.

The existing source jobs, schema, decoder and raw-response checks are retained.
Only first-writing guidance changes; no guessed bounds, sorting, repair, extra
model review or source-specific example is introduced.
"""
import json

from . import pdf_layout_retention_v9 as previous
from . import pdf_layout_retention as legacy
from .pdf_layout_retention_v4 import _image_label_tokens, image_scope_guidance
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v10'
DECODER_VERSION = 'pdf-layout-retention-v10'
REQUEST_MODE_VERSION = previous.REQUEST_MODE_VERSION
PROMPT = previous.PROMPT
CURVE_DOMAIN_VERSION = previous.CURVE_DOMAIN_VERSION
LEDGER_VERSION = previous.LEDGER_VERSION
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = previous.IMAGE_SCOPE_VERSION
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v10-compact-1'
FINAL_GUIDANCE_VERSION = previous.FINAL_GUIDANCE_VERSION
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION
ROI_INPUT_VERSION = previous.ROI_INPUT_VERSION
OUTPUT_GUIDANCE = (
    '\nKeep facts/definitions/conditions/exceptions; undefined stays so. '
    'Nonplots estimated_values=[]; relations/uncertainties; '
    'printed numbers->printed_values. Per curve: one conditions label per x; '
    'labels/condition_values/each series.values same N, paired order; '
    'descending x OK. Source-read axis x/y [min,max]: min<max, linear/log10; '
    'observed_x_range drawn [min,max]: min<=max, any plot order, not point extrema. '
    'y needs x in axis+extent; y in axis. No guessed bounds/units, '
    'extrapolation/fill/sort/clamp. Unreadable axis/extent/y null; unknown x null. '
    'Keep readable estimates; unverified.\n')

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


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    # The new mandatory guidance consumes its own reservation first. Optional
    # same-source context may use only the remaining space; owned text/images,
    # schema, output allowance and safety allowance are never clipped.
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
        raise AuthoringWorkLimitError('Complete curve-array input/schema exceeds context reservation.')
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
        raise AuthoringIncompleteError('The exact forward curve-array policy is required.')
    policy = plan.model_policy
    if policy.provider_order == ('hynix_api',) and policy.model_ids == ('qwen36-local',):
        return {'chat_template_kwargs': {'enable_thinking': False}}
    return {}


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi
    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact forward curve-array policy required.')
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
        raise AuthoringWorkLimitError('Complete curve-array manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
