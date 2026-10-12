"""Forward V15 input prevention for blank grouped-curve text fields.

Historical V14 schema, prompts and decoding remain unchanged. This adapter
does not repair raw output, infer units, pad points, omit source information,
or add a semantic reviewer. The ordinary caller supplies a fresh sealed job.
"""
from copy import deepcopy

from ..llm_images import prepare_image_messages
from . import pdf_layout_retention_v3 as previous
from . import pdf_layout_retention as legacy
from .pdf_section_writing import CONTEXT_TOKENS, estimate_text_tokens
from .wiki_authoring import AuthoringIncompleteError, AuthoringWorkLimitError

VERSION = 'wiki-pdf-layout-retention-v4'
DECODER_VERSION = 'pdf-layout-retention-v4'
LEDGER_VERSION = previous.LEDGER_VERSION
PROMPT = previous.PROMPT
SUBJECT_BINDING_VERSION = previous.SUBJECT_BINDING_VERSION
TERM_CONTEXT_VERSION = previous.TERM_CONTEXT_VERSION
IMAGE_SCOPE_VERSION = 'pdf-layout-image-roles-v5'
SUPPLEMENTAL_BUDGET_VERSION = 'pdf-layout-supplemental-budget-v5'
OUTPUT_GUIDANCE = ('\nPreserve owned facts, symbol/method definitions, values, conditions and exceptions. '
    'Undefined abbreviations stay undefined; context grants no prose ownership. '
    'Group strings must be nonblank. Units: dimensionless only if source-supported; '
    'if not stated, say not reported. Never infer units.\n')
initialize = previous.initialize
seal_admitted_plan = previous.seal_admitted_plan
checked_admitted_plan = previous.checked_admitted_plan
job_for = previous.job_for
images = previous.images
request = previous.request
assemble = previous.assemble
checked_subject_bindings = previous.checked_subject_bindings
collect_term_contexts = previous.collect_term_contexts
decode = previous.decode


def response_format(scope):
    """Constrain only the four already-required grouped string fields."""
    original = previous.response_format(scope)
    if original is None:
        return None
    form = deepcopy(original)
    figures = form['json_schema']['schema']['properties']['figures']['properties']
    for record in figures.values():
        group = record['properties']['estimated_values']['items']
        if group.get('type') != 'object' or type(group.get('properties')) is not dict:
            raise AuthoringIncompleteError('Nonblank curve input requires the existing grouped contract.')
        for name in ('subject', 'metric', 'unit', 'limit'):
            field = group['properties'].get(name)
            if type(field) is not dict or field.get('type') != 'string':
                raise AuthoringIncompleteError('Nonblank curve input field shape changed.')
            field['minLength'] = 1
    return form


def additional_schema_tokens(scope):
    """Reserve full V14 enums plus V15 constraints against the original job."""
    original = legacy.response_format(scope)
    changed = response_format(scope)
    if changed == original:
        return 0
    return max(0, estimate_text_tokens(legacy._json(changed)) -
        estimate_text_tokens(legacy._json(original)))


def image_scope_guidance(scope, images):
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
    if not roles:
        return ''
    return ('\n1-based image roles: ' + legacy._json(roles) +
        '. Read all target panels; context grants no other subjects. '
        'Keep legend/condition correspondence; unclear points null, no invented ranks.\n')


def _image_label_tokens(images):
    """Count the normal adapter's separate wire text parts, not image pixels."""
    if not images:
        return 0
    prepared = prepare_image_messages([{'role': 'user', 'content': ''}], images, inline=True)
    return sum(estimate_text_tokens(part['text']) for part in prepared[-1]['content']
        if part['type'] == 'text')


def supplemental_guidance(plan, scope, job, *, maximum_tokens=None):
    """Reserve schema and actual labels before optional same-source context.

    ``maximum_tokens`` bounds all additions to the legacy job estimate, not
    just the returned guidance. A caller may reduce but never enlarge the
    actual remaining context. Owned input/output/image reserves are unchanged.
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
        raise AuthoringWorkLimitError('Complete nonblank curve input/schema exceeds context reservation.')
    intro = '\nSame-source literal context, not verified definitions or extra ownership:\n'
    selected = []
    for row in collect_term_contexts(plan, scope, job):
        if estimate_text_tokens(text + intro + legacy._json(selected + [row])) <= maximum_tokens:
            selected.append(row)
    if selected:
        text += intro + legacy._json(selected)
    return text
