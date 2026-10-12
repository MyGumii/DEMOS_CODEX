"""Forward V16 source-faithful first writing; no additional model review.

Historical writer bytes, figure schema and strict decoder remain unchanged.
Guidance is generic source-reading advice, not proof of semantic accuracy.
"""
import json

from . import pdf_layout_retention_v4 as previous
from . import pdf_layout_retention as legacy
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoredWiki, AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v5'
DECODER_VERSION = 'pdf-layout-retention-v5'
LEDGER_VERSION = previous.LEDGER_VERSION
PROMPT = previous.PROMPT
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = 'pdf-layout-image-roles-v6'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v6'
LITERAL_APPENDIX_VERSION = 'pdf-source-literal-appendix-v1'
OUTPUT_GUIDANCE = ('\nOwn facts once: preserve numbered equations, symbol/method definitions, '
    'optimizer settings, conditions and exceptions. Expand terms only from source definitions; '
    'otherwise undefined. Context owns no facts. Nonblank units: source-supported or not reported, '
    'never inferred.\n')
CURVE_GUIDANCE = ('\nCurves: null beyond each drawn series or unreadable; no interpolation, '
    'extrapolation or endpoint fill. Compare only same metric/condition; no ranks contradicting '
    'readings. Relations are unverified.\n')

initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
response_format = previous.response_format
additional_schema_tokens = previous.additional_schema_tokens
checked_subject_bindings = previous.checked_subject_bindings
collect_term_contexts = previous.collect_term_contexts
decode = previous.decode
_image_label_tokens = previous._image_label_tokens


def image_scope_guidance(scope, images):
    """Keep all existing attachment roles, without introducing source facts."""
    if not scope['visual_units']:
        return ''
    roles = []
    for unit in scope['visual_units']:
        view = unit.get('source_view')
        if view is None:
            continue
        for name in ('target_image_index', 'context_image_index'):
            index = view.get(name)
            if type(index) is not int or not 0 <= index < len(images):
                raise AuthoringIncompleteError('Original image role index changed.')
        roles.append({'alias': unit['alias'], 'target': view['target_image_index'] + 1,
            'context': view['context_image_index'] + 1})
    text = CURVE_GUIDANCE
    if roles:
        text += ('1-based roles: ' + legacy._json(roles) +
            '. Read all target panels; context grants no extra subjects.\n')
    return text


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    """Reserve the unchanged full schema and actual labels before context.

    Required source, pixels, output and safety reserves are never reduced.
    Optional exact source excerpts may be omitted, never clipped or rewritten.
    """
    headroom = CONTEXT_TOKENS - job.estimated_tokens
    maximum_tokens = headroom if maximum_tokens is None else min(maximum_tokens, headroom)
    maximum_tokens -= additional_schema_tokens(scope) + _image_label_tokens(job.images)
    bindings = checked_subject_bindings(scope, job.images)
    text = OUTPUT_GUIDANCE + image_scope_guidance(scope, job.images)
    if bindings:
        literals = [{'alias': row['figure_alias'], 'subject': row['selected_subject']} for row in bindings]
        text += ('\nExact caption-order subjects (source data): ' + legacy._json(literals) +
            '. No other/aggregate subjects. Curves/legends remain unverified.\n')
    if estimate_text_tokens(text) > maximum_tokens:
        raise AuthoringWorkLimitError('Complete source-faithful input/schema exceeds context reservation.')
    intro = '\nSame-source literal context, not verified definitions or extra ownership:\n'
    selected = []
    for row in collect_term_contexts(plan, scope, job):
        if estimate_text_tokens(text + intro + legacy._json(selected + [row])) <= maximum_tokens:
            selected.append(row)
    if selected:
        text += intro + legacy._json(selected)
    return text


def assemble(plan, by_id):
    """Keep unchanged receipt replay, then append exact checked source text.

    No model answer, figure record, numerical reading or raw receipt is changed.
    The appendix is an extraction ledger, not corrected mathematics or a
    semantic reviewer. Source and rendered output identities are both bound.
    """
    from .pdf_source_literal_appendix import VERSION as appendix_version
    from .pdf_source_literal_appendix import collect_literal_records, render_literal_appendix

    if plan.policy_version != VERSION:
        raise AuthoringIncompleteError('Exact source appendix requires the forward policy.')
    if appendix_version != LITERAL_APPENDIX_VERSION:
        raise AuthoringIncompleteError('Exact source appendix version changed.')
    result = legacy.assemble(plan, by_id)
    records = collect_literal_records(plan)
    body = render_literal_appendix(records)
    if type(body) is not str:
        raise AuthoringIncompleteError('Exact source appendix renderer changed.')
    separator = '\n' if body else ''
    start = len((result.markdown + separator).encode('utf-8'))
    end = start + len(body.encode('utf-8'))
    markdown = result.markdown + separator + body
    if len(markdown.encode('utf-8')) > plan.work_policy.markdown_bytes:
        raise AuthoringWorkLimitError('Complete source appendix exceeds Markdown reservation.')
    manifest = json.loads(result.manifest_json)
    if 'literal_appendix' in manifest:
        raise AuthoringIncompleteError('Repeated source appendix ledger.')
    manifest['literal_appendix'] = {'version': appendix_version, 'records': records,
        'records_sha256': legacy._hash(legacy._json(records)),
        'appendix_sha256': legacy._hash(body), 'output_byte_range': [start, end],
        'semantic_accuracy_verified': False}
    manifest['wiki_sha256'] = legacy._hash(markdown)
    manifest['wiki_bytes'] = len(markdown.encode('utf-8'))
    encoded = legacy._json(manifest)
    if len(encoded.encode('utf-8')) > plan.work_policy.manifest_bytes:
        raise AuthoringWorkLimitError('Complete source appendix exceeds manifest reservation.')
    return AuthoredWiki(markdown, encoded)
