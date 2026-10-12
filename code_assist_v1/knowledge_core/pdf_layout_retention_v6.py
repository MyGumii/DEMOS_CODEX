"""Forward V17: bounded PDF appearance ROIs, unchanged writing contract.

No additional semantic review call; exact source text/table retention remains
independent of the model's unverified figure readings.
"""
import json

from . import pdf_layout_retention_v5 as previous
from . import pdf_layout_retention as legacy
from .pdf_figure_roi_source import VERSION as ROI_INPUT_VERSION
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v6'
DECODER_VERSION = 'pdf-layout-retention-v6'
LEDGER_VERSION = previous.LEDGER_VERSION
PROMPT = previous.PROMPT
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = 'pdf-layout-image-roles-v7'
SUPPLEMENTAL_BUDGET_VERSION = previous.SUPPLEMENTAL_BUDGET_VERSION
LITERAL_APPENDIX_VERSION = previous.LITERAL_APPENDIX_VERSION

initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
collect_term_contexts = previous.collect_term_contexts


def _legacy_scope(scope):
    """Project only new private provenance; retain every original wire field.

    The whole typed plan seals the actual ROI capability and supplied pixels.
    Historical subject binding must still see its exact original projection,
    not a relaxed allowlist or authority inferred from public ROI metadata.
    """
    units = scope.get('visual_units') if type(scope) is dict else None
    if type(units) is not list or any(type(unit) is not dict for unit in units):
        raise AuthoringIncompleteError('Original forward visual scope changed.')
    projected = [{k:v for k,v in unit.items() if k != 'figure_roi'} for unit in units]
    expected = [{k:v for k,v in unit.items()
        if k not in ('unit_id','view_id','native_view_metadata')} for unit in projected]
    if 'visual_inventory' in scope and scope['visual_inventory'] != expected:
        raise AuthoringIncompleteError('Original forward visual wire inventory changed.')
    return {**scope,'visual_units':projected}


def checked_subject_bindings(scope, images=None):
    return previous.checked_subject_bindings(_legacy_scope(scope),images)


def response_format(scope):
    return previous.response_format(_legacy_scope(scope))


def additional_schema_tokens(scope):
    return previous.additional_schema_tokens(_legacy_scope(scope))


def decode(text, scope):
    return previous.decode(text,_legacy_scope(scope))


def supplemental_guidance(plan,scope,job,*,maximum_tokens=None):
    return previous.supplemental_guidance(plan,_legacy_scope(scope),job,
        maximum_tokens=maximum_tokens)


def assemble(plan, by_id):
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix
    from .pdf_figure_roi_source import checked_figure_roi

    if plan.policy_version != VERSION or appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact forward ROI/appendix policy required.')
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
    # Source capabilities are evidence of supplied pixels, not accurate curves
    # or caption ownership. Only jobs actually using an ROI count as delivered.
    supplied = {u['figure_roi']['unit_id'] for job in plan._layout_plan.jobs
        for u in json.loads(job.scope_json)['visual_units'] if 'figure_roi' in u}
    manifest['figure_roi_inputs'] = {'version': ROI_INPUT_VERSION,
        'sources': [checked_figure_roi(roi) for roi in plan.figure_rois],
        'delivered_unit_ids': sorted(supplied),
        'semantic_accuracy_verified': False}
    manifest['wiki_sha256'] = legacy._hash(markdown)
    manifest['wiki_bytes'] = len(markdown.encode('utf-8'))
    encoded = legacy._json(manifest)
    if len(encoded.encode('utf-8')) > plan.work_policy.manifest_bytes:
        raise AuthoringWorkLimitError('Complete forward ROI manifest exceeds reservation.')
    return AuthoredWiki(markdown, encoded)
