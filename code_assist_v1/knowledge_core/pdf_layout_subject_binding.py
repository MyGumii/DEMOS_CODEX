"""Pure forward candidate: bind an explicit caption order to owned columns.

The caller must freshly check its sealed original scope. This helper grants no
source capability, proves no caption/image association, and verifies no numeric
or legend meaning. It reads no files, model output, other Wiki or external data.
Only a deliberately narrow literal left-to-right grammar is admitted. Names
outside that grammar, vertical/mixed directions and ambiguous lists abstain.
"""
from hashlib import sha256
import json
import re

VERSION = 'pdf-layout-caption-subject-binding-v1'
MAXIMUM_CAPTION_BYTES = 8192
MAXIMUM_BINDING_BYTES = 4096
MAXIMUM_RESULT_BYTES = 16384
MAXIMUM_UNITS = 64
MAXIMUM_SUBJECTS = 16

# Atomic source labels only: multi-word/quoted names are intentionally unsupported
# rather than guessing whether a comma belongs inside a name. Digits can follow
# a letter, but a numeric/measurement list is never a subject list.
_NAME = r'[^\W\d_][\w-]{0,47}'
_LIST = _NAME + r'(?:\s*,\s*' + _NAME + r'){0,14}\s*,?\s+(?:and|&)\s+' + _NAME
_ENUMERATION = re.compile(r'(?<![\w-])' + _LIST + r'(?![\w-])', re.UNICODE)
_DIRECTION = r'(?:from\s+)?left(?:\s+|-)to(?:\s+|-)right'
_ANY_DIRECTION = re.compile(
    r'\b(?:left(?:\s+|-)to(?:\s+|-)right|right(?:\s+|-)to(?:\s+|-)left|'
    r'top(?:\s+|-)to(?:\s+|-)bottom|bottom(?:\s+|-)to(?:\s+|-)top)\b', re.I)
_MIXED_AXIS = re.compile(r'\b(?:rows?|row-wise|vertically|vertical|top|bottom|above|below)\b', re.I)
_FIGURE = re.compile(r'\b(?:Fig\.|Figure)\s+(?P<label>\d+[A-Za-z]?)\.', re.I)
_CANDIDATE = re.compile(
    r'(?:\b(?:of|for|between|among)\s+|(?:^|[.!?])\s*)'
    r'(?P<subjects>' + _LIST + r')\s+'
    r'(?:datasets?|products?|samples?|groups?|subjects?|cases?|cohorts?|categories|models?|methods?)'
    r'(?:\s+are\s+(?:displayed|shown|arranged))?\s+'
    r'(?P<direction>' + _DIRECTION + r')\b', re.I | re.UNICODE)
_LABEL = re.compile(r'\d+[A-Za-z]?\Z')
_ID = re.compile(r'[A-Za-z0-9_-]{1,160}\Z')
_ALIAS = re.compile(r'V[1-9]\d{0,3}\Z')
_HASH = re.compile(r'[0-9a-f]{64}\Z')
_UNSUPPORTED = re.compile(r'[\"\'“”‘’`\[\]{};<>]')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(text):
    return sha256(text.encode('utf-8')).hexdigest()


def _literal(caption, start, end):
    text = caption[start:end]
    return {'text': text, 'start': start, 'end': end, 'text_sha256': _sha(text)}


def _checked_column(unit):
    if (type(unit) is not dict or unit.get('kind') != 'figure'
            or unit.get('image_mode') != 'native_source_column_with_context'
            or type(unit.get('page')) is not int or not 1 <= unit['page'] <= 4096
            or type(unit.get('alias')) is not str or not _ALIAS.fullmatch(unit['alias'])
            or type(unit.get('label')) is not str or not _LABEL.fullmatch(unit['label'])
            or any(type(unit.get(key)) is not str or not _ID.fullmatch(unit[key])
                   for key in ('unit_id', 'view_id'))):
        return None
    view = unit.get('source_view')
    if (type(view) is not dict or type(view.get('count')) is not int
            or not 2 <= view['count'] <= MAXIMUM_SUBJECTS
            or type(view.get('ordinal')) is not int or not 1 <= view['ordinal'] <= view['count']
            or view.get('target_image_index') != 0 or type(view.get('target_image_index')) is not int
            or view.get('context_image_index') != 1 or type(view.get('context_image_index')) is not int
            or view.get('context_only') is not True or view.get('axes_calibrated') is not False
            or view.get('legend_ownership_proven') is not False):
        return None
    dimensions, box = view.get('source_dimensions'), view.get('pixel_bbox')
    if (type(dimensions) is not list or len(dimensions) != 2
            or any(type(n) is not int or not 1 <= n <= 16384 for n in dimensions)
            or dimensions[0] * dimensions[1] > 16_000_000
            or type(box) is not list or len(box) != 4 or any(type(n) is not int for n in box)
            or not 0 <= box[0] < box[2] <= dimensions[0] or box[1] != 0 or box[3] != dimensions[1]
            or (view['ordinal'] == 1 and box[0] != 0)
            or (view['ordinal'] == view['count'] and box[2] != dimensions[0])):
        return None
    native = unit.get('native_view_metadata')
    if (type(native) is not dict or any(
            native.get(key) != expected for key, expected in (
                ('view_id', unit['view_id']), ('page', unit['page']), ('pixel_bbox', box),
                ('ordinal', view['ordinal']), ('count', view['count']),
                ('width', box[2] - box[0]), ('height', dimensions[1])))
            or native.get('axes_calibrated') is not False
            or native.get('semantic_ownership_proven') is not False
            or type(native.get('source_object_id')) is not str or not _ID.fullmatch(native['source_object_id'])
            or any(type(native.get(key)) is not str or not _HASH.fullmatch(native[key])
                   for key in ('encoded_sha256', 'pixel_sha256'))):
        return None
    context = native.get('context')
    if (type(context) is not dict or context.get('context_only') is not True
            or context.get('legend_ownership_proven') is not False
            or type(context.get('source_id')) is not str or not _ID.fullmatch(context['source_id'])
            or any(type(context.get(key)) is not str or not _HASH.fullmatch(context[key])
                   for key in ('encoded_sha256', 'overview_pixel_sha256', 'reference_pixel_sha256'))):
        return None
    return view


def bind_subject(unit):
    """Return literal position metadata or None, without guessing source facts.

    The grammar accepts atomic letter-led labels, a final explicit conjunction,
    a generic subject-class noun and one left-to-right phrase. Quotes, comma-
    bearing/multi-word names, multiple lists/directions, row references, another
    figure label and count mismatch deliberately abstain. Source offsets are
    Python Unicode code points, NOT bytes or page-global offsets.
    """
    view = _checked_column(unit)
    if view is None:
        return None
    caption = unit.get('caption')
    if (type(caption) is not str or not caption or len(caption.encode('utf-8')) > MAXIMUM_CAPTION_BYTES
            or any(ord(c) < 32 and c not in '\n\r\t' for c in caption)
            or _UNSUPPORTED.search(caption) or _MIXED_AXIS.search(caption)):
        return None
    figures = list(_FIGURE.finditer(caption))
    if (len(figures) != 1 or figures[0].start() != 0
            or figures[0]['label'] != unit['label']):
        return None
    directions = list(_ANY_DIRECTION.finditer(caption))
    candidates = list(_CANDIDATE.finditer(caption))
    enumerations = list(_ENUMERATION.finditer(caption))
    if len(directions) != 1 or len(candidates) != 1 or len(enumerations) != 1:
        return None
    match = candidates[0]
    if not (match.start('direction') <= directions[0].start() < directions[0].end() <= match.end('direction')
            and match.span('subjects') == enumerations[0].span()):
        return None
    subject_region = match['subjects']
    # Split punctuation/connector only within the already admitted grammar.
    names = list(re.finditer(_NAME, subject_region, re.UNICODE))
    names = [name for name in names if name.group().casefold() != 'and']
    if len(names) != view['count'] or len({name.group().casefold() for name in names}) != len(names):
        return None
    subjects = [_literal(caption, match.start('subjects') + name.start(),
        match.start('subjects') + name.end()) for name in names]
    selected = subjects[view['ordinal'] - 1]
    native = unit['native_view_metadata']; context = native['context']
    result = {'version': VERSION, 'figure_alias': unit['alias'],
        'source_unit_id': unit['unit_id'], 'source_view_id': unit['view_id'],
        'page': unit['page'], 'figure_label': unit['label'],
        'caption_sha256': _sha(caption), 'caption_character_count': len(caption),
        'range_domain': 'source_caption_unicode_code_points',
        'direction': 'left_to_right',
        'direction_literal': _literal(caption, match.start('direction'), match.end('direction')),
        'ordered_subjects': subjects, 'ordinal': view['ordinal'], 'count': view['count'],
        'selected_subject': selected['text'], 'selected_subject_start': selected['start'],
        'selected_subject_end': selected['end'],
        'source_object_id': native['source_object_id'],
        'target_source_id': native['view_id'], 'target_encoded_sha256': native['encoded_sha256'],
        'target_pixel_sha256': native['pixel_sha256'],
        'context_source_id': context['source_id'], 'context_encoded_sha256': context['encoded_sha256'],
        'context_overview_pixel_sha256': context['overview_pixel_sha256'],
        'context_reference_pixel_sha256': context['reference_pixel_sha256'], 'context_only': True,
        'selection_reason': 'single_explicit_caption_order_matches_owned_native_column_count',
        'binding_basis': 'source_caption_order_only',
        'caption_association': unit.get('caption_association'),
        'semantic_ownership_verified': False,
        'caption_image_association_proven': False, 'legend_ownership_proven': False,
        'numeric_accuracy_verified': False}
    result['binding_id'] = 'PSB_' + _sha(canonical(result))
    if len(canonical(result).encode('utf-8')) > MAXIMUM_BINDING_BYTES:
        return None
    return result


def collect_subject_bindings(scope):
    """Read only freshly checked original visual units; no partial budget prefix.

    Wire/source mismatch is a changed-input error, not an opportunity to bind a
    different caption. Genuine grammar ambiguity simply returns no binding.
    """
    if type(scope) is not dict or type(scope.get('visual_units')) is not list:
        raise ValueError('An original source-owned visual scope is required.')
    units = scope['visual_units']
    if len(units) > MAXIMUM_UNITS:
        return []
    if 'visual_inventory' in scope:
        inventory = scope['visual_inventory']
        if (type(inventory) is not list or any(type(unit) is not dict for unit in units)
                or inventory != [{k: v for k, v in unit.items()
                    if k not in ('unit_id', 'view_id', 'native_view_metadata')} for unit in units]):
            raise ValueError('Original source caption and wire inventory differ.')
    identities = [(unit.get('unit_id'), unit.get('view_id')) for unit in units if type(unit) is dict]
    if len(identities) != len(set(identities)):
        raise ValueError('Original source view identity is ambiguous.')
    result = [binding for unit in units if (binding := bind_subject(unit)) is not None]
    return result if len(canonical(result).encode('utf-8')) <= MAXIMUM_RESULT_BYTES else []
