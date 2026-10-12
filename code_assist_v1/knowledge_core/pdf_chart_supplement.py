"""Complete retained chart evidence, without source or publication authority.

The producer owns fresh source/layout/native-appearance checks. This pure
aggregate checks consistency of their retained identities; hashes do not prove
semantic ownership or numerical accuracy. OCR, visible color bands and full
readings remain retained. Optional checked-layout rendering also preserves
literal captions and raw OCR lines, without certifying their ownership. No
source I/O, OCR, fitting, CAS, SQL or model is performed.
"""
from dataclasses import dataclass
from hashlib import sha256
from html import escape
import base64
import json
import math
import re

from ..document_pdf_process import PdfProcessError
from .pdf_chart_complete_reading import PdfCompleteChartReading, render_complete_source_chart
from .pdf_chart_model_context import render_chart_model_context
from .pdf_chart_retention_markdown import _cell


VERSION = 'pdf-chart-supplement-v1'
WRITER_INPUT_VERSION = 'pdf-chart-writer-source-observations-v1'
CONDITIONAL_WRITER_INPUT_VERSION = 'pdf-chart-writer-conditional-source-fits-v2'

LITERAL_CONTEXT_VERSION = 'pdf-chart-same-source-literal-definitions-v1'
LITERAL_CONTEXT_WRITER_VERSION = 'pdf-chart-same-source-literal-definitions-v2'
LITERAL_CONTEXT_TABLE_VERSION = 'pdf-chart-same-source-literal-definitions-v3'
LITERAL_CONTEXT_NATIVE_VERSION = 'pdf-chart-same-source-literal-definitions-v4'
LITERAL_CONTEXT_BOUNDED_OWNED_VERSION = 'pdf-chart-bounded-owned-native-v12'
LITERAL_CONTEXT_ADMITTED_OWNED_VERSION = 'pdf-chart-admitted-owned-native-v13'
LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION = 'pdf-chart-explicit-table-native-v11'
LITERAL_CONTEXT_RELATION_VERSION = 'pdf-chart-key-information-native-v8'
LITERAL_CONTEXT_BARE_VERSION = 'pdf-chart-key-information-native-v7'
LITERAL_CONTEXT_KEY_VERSION = 'pdf-chart-key-information-native-v5'
KEY_INFORMATION_REASON = 'key_information_via_original_page_images'
KEY_INFORMATION_SOURCE_VERSION = 'pdf-chart-key-information-source-v1'
NATIVE_TEXT_PRESENTATION_VERSION = 'pdf-native-word-flow-owned-block-v1'
NATIVE_TEXT_DERIVATION = 'WordExtractor(use_text_flow=True,expand_ligatures=False); space iff centerY delta<=3 and x0>=previous x0-3, otherwise newline'


def _literal_context_marker(record):
    marker = record.get('literal_context_marker')
    if 'literal_context_marker' not in record:
        return None
    _require(type(marker) is dict and set(marker) == ({'version','native_text_presentation'}
        if marker.get('version') in (LITERAL_CONTEXT_NATIVE_VERSION,LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION) else {'version'})
        and type(marker['version']) is str
        and marker['version'] in (LITERAL_CONTEXT_VERSION, LITERAL_CONTEXT_WRITER_VERSION, LITERAL_CONTEXT_TABLE_VERSION, LITERAL_CONTEXT_NATIVE_VERSION, LITERAL_CONTEXT_KEY_VERSION, LITERAL_CONTEXT_CLAUSE_VERSION, LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION),
        'Known typed literal context marker required')
    return marker['version']


def literal_context_version(supplement):
    # Metadata selection only, not an authority/validation API. The plan calls
    # this after _checked_chart_supplement; the collector still calls _load.
    _require(type(supplement) is PdfChartSupplement and type(supplement.metadata_json) is str
        and len(supplement.metadata_json.encode()) <= MAXIMUM_INPUT_BYTES,
        'Bounded typed literal context source required')
    if '"literal_context_marker":' not in supplement.metadata_json:
        return None  # Historical inputs incur no full parse/validation here.
    record = json.loads(supplement.metadata_json)
    _require(type(record) is dict, 'Typed literal context record required')
    return _literal_context_marker(record)



def _native_text_record(value, source, cancel_event=None):
    from collections import Counter
    _check(cancel_event)
    _require(type(value) is dict and set(value)=={'version','source_raw_sha256','source_geometry_sha256',
        'engine','derivation','semantic_reading_order_proven','pages'}
        and value['version']==NATIVE_TEXT_PRESENTATION_VERSION
        and value['source_raw_sha256']==source['geometry']['raw_sha256']
        and value['source_geometry_sha256']==source['geometry']['geometry_sha256']
        and value['engine']=='0.11.9' and value['derivation']==NATIVE_TEXT_DERIVATION
        and value['semantic_reading_order_proven'] is False,
        'Known source-bound native text presentation required')
    limits=source['geometry']['limits']
    from dataclasses import asdict
    from ..document_pdf_geometry import PdfGeometryLimits
    ceilings=asdict(PdfGeometryLimits())
    _require(type(limits) is dict and set(limits)==set(ceilings)
        and all(type(v) is int and 0<v<=ceilings[k] for k,v in limits.items()),
        'Existing bounded native geometry policy required')
    _require(len(_json(value).encode())<=min(MAXIMUM_INPUT_BYTES,limits['max_manifest_bytes'])
        and type(value['pages']) is list and len(value['pages'])==len(source['pages'])<=limits['max_pages'],
        'Complete bounded native page inventory required')
    ids=set();total_glyphs=total_text=0
    for ordinal,page in enumerate(value['pages'],1):
        _check(cancel_event)
        _require(type(page) is dict and set(page)=={'page','glyph_count','glyph_sha256','blocks'}
            and type(page['page']) is int and page['page']==ordinal
            and type(page['glyph_count']) is int and 0<=page['glyph_count']<=limits['max_glyphs']
            and _sha(page['glyph_sha256']) and type(page['blocks']) is list
            and len(page['blocks'])<=2*limits['max_tables']+1,'Typed original native page required')
        total_glyphs+=page['glyph_count']
        for row in page['blocks']:
            _check(cancel_event)
            _require(type(row) is dict and set(row)=={'block_id','source_text_sha256','glyph_indices',
                'nonwhitespace_glyph_indices','native_word_glyph_indices','projection_available',
                'projected_text','projected_text_sha256'} and type(row['block_id']) is str
                and 0<len(row['block_id'])<=256 and row['block_id'] not in ids
                and _sha(row['source_text_sha256']) and type(row['projected_text']) is str
                and '\x00' not in row['projected_text'] and row['projected_text_sha256']==_hash(row['projected_text'])
                and type(row['projection_available']) is bool,'Exact native block projection required')
            ids.add(row['block_id']);total_text+=len(row['projected_text'].encode())
            indices=row['glyph_indices']; nonwhite=row['nonwhitespace_glyph_indices'];words=row['native_word_glyph_indices']
            _require(type(indices) is list and len(indices)<=page['glyph_count']
                and all(type(i) is int and 0<=i<page['glyph_count'] for i in indices)
                and type(nonwhite) is list and len(nonwhite)<=len(indices)
                and type(words) is list and len(words)<=len(indices), 'Bounded native glyph map required')
            if row['projection_available']:
                owned_indices=set(indices)
                _require(len(indices)==len(set(indices)) and len(nonwhite)==len(set(nonwhite))
                    and all(type(i) is int and i in owned_indices for i in nonwhite)
                    and all(type(word) is list and len(word)<=len(indices)
                        and all(type(i) is int and 0<=i<page['glyph_count'] for i in word) for word in words)
                    and Counter(i for word in words for i in word)==Counter(nonwhite),
                    'Native word map omitted or duplicated source glyphs')
            else:
                _require(nonwhite==[] and words==[],'Unresolved native block cannot acquire a word map')
    _require(total_glyphs<=limits['max_glyphs'] and total_text<=limits['max_text_bytes'],
        'Native complete source work budget exceeded')
    return value


def native_owned_text_presentation(plan, retained):
    """Bind retained projection to unchanged geometry; never parse/OCR on restore."""
    from collections import Counter
    from ..document_pdf_geometry import PdfTextBlock
    value=_native_text_record(retained,plan.source.identity())
    originals=plan._doc.pages
    _require(len(value['pages'])==len(originals),'Native original page inventory changed')
    by_block={}
    for chunk in plan.chunks: by_block.setdefault(chunk.block_id,[]).append(chunk)
    ambiguous=(len({c.chunk_id for c in plan.chunks})!=len(plan.chunks)
        or len({b.block_id for p in originals for b in p.blocks})!=sum(len(p.blocks) for p in originals))
    selected=[]
    for page,stored in zip(originals,value['pages'],strict=True):
        _require((stored['page'],stored['glyph_count'],stored['glyph_sha256'])==
            (page.number,page.glyph_count,page.glyph_sha256),'Native original page glyph identity changed')
        blocks=[b for b in page.blocks if isinstance(b,PdfTextBlock)]
        inventory=[i for b in page.blocks for i in (b.glyph_indices if isinstance(b,PdfTextBlock)
            else [i for cell in b.cells for i in cell.glyph_indices])]
        page_ambiguous=(len(inventory)!=len(set(inventory))
            or any(type(i) is not int or not 0<=i<page.glyph_count for i in inventory))
        _require([r['block_id'] for r in stored['blocks']]==[b.block_id for b in blocks],
            'Complete original prose block inventory required')
        for block,row in zip(blocks,stored['blocks'],strict=True):
            _require(row['source_text_sha256']==_hash(block.text) and row['glyph_indices']==list(block.glyph_indices)
                and Counter(c for c in row['projected_text'] if not c.isspace())==Counter(c for c in block.text if not c.isspace()),
                'Native source block glyph content or ownership changed')
            owners=by_block.get(block.block_id,[])
            full=(not ambiguous and not page_ambiguous and len(owners)==1 and owners[0].start==0
                and owners[0].end==len(block.text) and owners[0].text==block.text)
            if full and row['projection_available']:
                selected.append((owners[0].chunk_id,block.block_id,block.page,row['source_text_sha256'],
                    row['projected_text'],row['projected_text_sha256']))
    return tuple(selected)


def _literal_ocr_lines(record):
    """Complete original OCR lines; page filtering never creates partial lines."""
    for figure, inventory in zip(record['figures'], record['figure_inventory'], strict=True):
        for kind, observed in (
                ('crop', _crop_ocr_context(figure)),
                ('native', _native_ocr_context(figure, inventory, record['source_identity']))):
            if observed is None:
                continue
            for ordinal, raw_line, words in observed['lines']:
                yield dict(page=inventory['page'], figure_id=inventory['unit_id'],
                    figure_label=inventory['label'], observation_kind=kind,
                    line_ordinal=ordinal, rotation_ccw=observed['rotation_ccw'],
                    text=raw_line if raw_line is not None else ' '.join(w[1] for w in words),
                    words=words, coordinate_frame=observed['coordinate_frame'],
                    dimensions=observed['dimensions'], source_id=observed['source_id'],
                    source_pdf_sha256=observed['source_pdf_sha256'],
                    source_png_sha256=observed['source_png_sha256'],
                    raw_ocr_stdout_sha256=observed['raw_ocr_stdout_sha256'])
        observed = _page_ocr_context(figure, inventory, record['source_identity'])
        if observed is None:
            continue
        page = record['source_identity']['pages'][inventory['page']-1]
        metadata = json.loads(figure['provenance']['page_label_ocr']['metadata_json'])
        bounds = observed['figure_pixel_bbox']
        for orientation in metadata['orientations']:
            word_ordinal = 0
            for ordinal, (line, boxes) in enumerate(zip(orientation['report']['lines'],
                    orientation['source_word_bbox_xywh'], strict=True)):
                words = [[word_ordinal+i, word['text'], box]
                    for i, (word, box) in enumerate(zip(line['words'], boxes, strict=True))]
                word_ordinal += len(words)
                # Every word of the exact line must belong to the same figure ROI.
                if not words or not all(b[0] >= bounds[0] and b[1] >= bounds[1]
                        and b[0]+b[2] <= bounds[2] and b[1]+b[3] <= bounds[3]
                        for _, _, b in words):
                    continue
                yield dict(page=inventory['page'], figure_id=inventory['unit_id'],
                    figure_label=inventory['label'], observation_kind='page',
                    line_ordinal=ordinal, rotation_ccw=orientation['rotation_degrees_ccw'],
                    text=line['text'], words=words, coordinate_frame='original-page',
                    dimensions=observed['dimensions'], source_id=page['source_id'],
                    source_pdf_sha256=record['source_identity']['geometry']['raw_sha256'],
                    source_png_sha256=page['sha256'],
                    raw_ocr_stdout_sha256=orientation['raw_ocr_stdout_sha256'])


def literal_definition_projection(rows):
    _require(type(rows) in (list, tuple) and all(type(r) is dict
        and type(r.get('page')) is int and r['page'] >= 1 and type(r.get('text')) is str
        and r.get('range_domain') in ('source_chunk_unicode_code_points',
            'retained_ocr_line_unicode_code_points')
        and r.get('definition_proven') is False and r.get('ownership_verified') is False
        and r.get('text_sha256') == _hash(r['text']) for r in rows),
        'Exact unverified literal projection required')
    return [{'page':r['page'], 'text':r['text'], 'source_kind':r['range_domain'],
        'definition_proven':False} for r in rows]


def _bounded_literal_groups(rows, maximum_bytes=1024):
    """Indexed whole groups; no quadratic scan over unrelated subjects."""
    _require(type(maximum_bytes) is int and 0 <= maximum_bytes <= 1024,
        'Bounded complete literal projection required')
    components, by_subject = [], {}
    def find(index):
        while components[index]['parent'] != index:
            components[index]['parent'] = components[components[index]['parent']]['parent']
            index = components[index]['parent']
        return index
    for row in rows:
        subjects = set(row['definition_terms'])
        roots = sorted({find(by_subject[term]) for term in subjects if term in by_subject},
            key=lambda index:components[index]['order'])
        eligible = 'subject_frequency' not in row or any(row['subject_frequency'].values())
        frequency = max((row.get('subject_frequency', {}).get(term, 0)
            for term in subjects), default=0)
        priority = row.get('selection_priority', 0)
        if not roots:
            root = len(components)
            components.append(dict(parent=root,order=root,node=row,count=1,
                eligible=eligible,frequency=frequency,priority=priority))
        else:
            # Original append-then-reverse-merge order, without copying rows
            # each time. Union by size and path compression bound lookup cost.
            node = [components[roots[0]]['node'],row,
                *(components[index]['node'] for index in reversed(roots[1:]))]
            root = max(roots,key=lambda index:components[index]['count'])
            merged = dict(parent=root,order=components[roots[0]]['order'],node=node,
                count=1+sum(components[index]['count'] for index in roots),
                eligible=eligible or any(components[index]['eligible'] for index in roots),
                frequency=max(frequency,*(components[index]['frequency'] for index in roots)),
                priority=min(priority,*(components[index]['priority'] for index in roots)))
            for index in roots:
                components[index]['parent'] = root
            components[root] = merged
        for term in subjects:
            by_subject[term] = root
    groups = sorted((item for index,item in enumerate(components)
        if item['parent']==index and item['eligible']),
        key=lambda item:(-item['frequency'],item['priority'],item['order']))
    selected = []
    for item in groups:
        if len(selected)+item['count'] > 32:
            continue  # Whole oversized group, not a truncated variant inventory.
        group, stack = [], [item['node']]
        while stack:
            node = stack.pop()
            if type(node) is list:
                stack.extend(reversed(node))
            else:
                group.append(node)
        projection = literal_definition_projection((*selected,*group))
        if len(_json(projection).encode()) <= maximum_bytes:
            selected.extend(group)
    return selected


def literal_definition_contexts(plan, owned, *, image_pages=()):
    """Literal input only. No OCR execution, label correction, or equation repair."""
    if literal_context_version(plan.chart_supplement) is None:
        return []
    from .pdf_layout_term_context import (
        _tokens, _ACRONYM, _definition_range, MAXIMUM_SCAN_CHARACTERS, MAXIMUM_SEED_CHARACTERS)
    chunks = tuple(plan.chunks)
    originals = tuple((c.chunk_id, c.page, c.text) for c in chunks)
    _require(len({c.chunk_id for c in chunks}) == len(chunks)
        and all(type(c.page) is int and c.page >= 1 and type(c.text) is str
            and type(c.chunk_id) is str and c.chunk_id for c in chunks),
        'Distinct original source chunks required')
    source_rows = {(c.page, c.text) for c in chunks}
    _require(type(image_pages) is tuple and all(type(p) is int and p >= 1 for p in image_pages),
        'Exact batch image pages required')
    _require(type(owned) is list and all(type(r) is dict and set(r) == {'page', 'text'}
        and type(r['page']) is int and type(r['text']) is str
        and (r['page'], r['text']) in source_rows for r in owned),
        'Exact owned source rows required for literal context')
    if (sum(len(r['text']) for r in owned) > MAXIMUM_SEED_CHARACTERS
            or sum(len(c.text) for c in chunks) > MAXIMUM_SCAN_CHARACTERS or not owned):
        return []  # Cannot select a complete conflict group from a partial scan.
    record = _load(plan.chart_supplement,
        expected_sha256=_hash(plan.chart_supplement.metadata_json))
    source_pdf = record['source_identity']['geometry']['raw_sha256']
    _require(plan.source.identity() == record['source_identity'], 'Literal context source changed')
    pages = {r['page'] for r in owned}; candidates = []; seen = set(); subjects_seen = set()
    for chunk in sorted(chunks, key=lambda c: (min(abs(c.page-p) for p in pages), c.page, c.chunk_id)):
        matches = _ACRONYM.finditer(chunk.text)
        for match in matches:
            span = _definition_range(chunk.text, match)
            if span is None:
                continue
            priority, start, end, subjects = span
            subjects_seen.update(subjects)
            if (chunk.page, chunk.text) in {(r['page'], r['text']) for r in owned}:
                continue
            subjects = sorted(set(subjects))
            key = (chunk.chunk_id, start, end)
            if not subjects or key in seen:
                continue
            seen.add(key); literal = chunk.text[start:end]
            candidates.append(dict(page=chunk.page, chunk_id=chunk.chunk_id,
                range_domain='source_chunk_unicode_code_points', start=start, end=end,
                text=literal, text_sha256=_hash(literal), source_pdf_sha256=source_pdf,
                definition_terms=subjects, selection_priority=priority,
                definition_proven=False, ownership_verified=False))
    for line in _literal_ocr_lines(record):
        words = line['words']
        if len(words) < 3 or words[1][1] != ':' or len(_tokens(words[0][1])) != 1:
            continue
        subjects_seen.update(_tokens(words[0][1]))
        literal = line['text']
        if line['page'] in image_pages or any(r['page'] == line['page']
                and literal in r['text'] for r in owned):
            continue  # Complete original image/exact text is already supplied.
        _require(type(line['page']) is int and type(line['line_ordinal']) is int
            and _sha(line['raw_ocr_stdout_sha256']) and _sha(line['source_png_sha256'])
            and line['source_pdf_sha256'] == source_pdf and type(literal) is str,
            'Typed exact original OCR definition line required')
        candidates.append(dict(line, range_domain='retained_ocr_line_unicode_code_points',
            start=0, end=len(literal), text_sha256=_hash(literal),
            literal_word_span_sha256=_hash(_json(words)), definition_terms=sorted(_tokens(words[0][1])),
            selection_priority=0, definition_proven=False, ownership_verified=False,
            source_labels_corrected=False))
    from collections import Counter
    # Literal uppercase-run boundaries tolerate missing PDF whitespace, but
    # never change text or certify a word/definition. Subject eligibility is
    # restricted to observed same-source definition hints, not every heading.
    seed_pattern = re.compile(_ACRONYM.pattern.replace('[A-Za-z0-9]', '[A-Z0-9]'))
    frequency = Counter(m.group().rstrip('.') for row in owned
        for m in seed_pattern.finditer(row['text']))
    relevant = set(frequency) & subjects_seen
    if not relevant:
        return []
    if any(sum(m.group().rstrip('.') in relevant for m in _ACRONYM.finditer(c.text)) > 64
            for c in chunks):
        return []
    selected_candidates = []
    for row in candidates:
        # Keep complete transitive groups even if a linked subject is not owned.
        subjects = sorted(set(row['definition_terms']))
        if subjects:
            selected_candidates.append(dict(row, definition_terms=subjects,
                subject_frequency={subject:frequency[subject] if subject in relevant else 0
                    for subject in subjects}))
    result = _bounded_literal_groups(selected_candidates)
    _require(tuple((c.chunk_id, c.page, c.text) for c in chunks) == originals,
        'Owned source changed during literal context selection')
    return result

CURVE_FITTING_NOT_REQUESTED = 'curve_fitting_not_requested'
MAXIMUM_INPUT_BYTES = 33_554_432
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_INVENTORY_FIELDS = {'unit_id', 'page', 'label', 'bbox', 'crop_sha256'}
_FIGURE_FIELDS = {'unit_id', 'status', 'reason', 'reading', 'provenance',
    'raw_ocr_stdout', 'raw_mosaic_ocr_stdout'}
_STORED_FIELDS = {'unit_id', 'status', 'reason', 'reading_json', 'reading_sha256', 'provenance',
    'raw_ocr_stdout_base64', 'raw_ocr_stdout_sha256',
    'raw_mosaic_ocr_stdout_base64', 'raw_mosaic_ocr_stdout_sha256'}


class PdfChartSupplementError(ValueError):
    pass


def _require(ok, message):
    if not ok:
        raise PdfChartSupplementError(message)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return sha256(value.encode('utf-8')).hexdigest()


def _writer_input_version(rows):
    """An explicit retained presentation mode, never inferred from current code.

    Absent markers preserve historical replay. A new preparation marks every
    figure, including unsupported ones; a mixed or unknown mode is not a
    successful subset. This marker conveys no source/numerical authority.
    """
    _require(type(rows) is list and all(type(row) is dict
        and type(row.get('provenance')) is dict for row in rows),
        'Complete retained writer presentation provenance required')
    marked = [row['provenance'] for row in rows if 'writer_input_version' in row['provenance']]
    if not marked:
        return None
    _require(len(marked) == len(rows) and all(type(item['writer_input_version']) is str
        and item['writer_input_version'] in (WRITER_INPUT_VERSION, CONDITIONAL_WRITER_INPUT_VERSION)
        for item in marked) and len({item['writer_input_version'] for item in marked}) == 1,
        'Unknown, malformed or mixed retained writer input version')
    return marked[0]['writer_input_version']



SOURCE_GEOMETRY_INPUT_VERSION = 'pdf-chart-source-thin-components-v1'


def _source_geometry_input_version(rows):
    """Optional all-figure producer provenance, not truth or writer authority."""
    _require(type(rows) is list and all(type(row) is dict
        and type(row.get('provenance')) is dict for row in rows),
        'Complete retained source geometry provenance required')
    marked = [row['provenance'] for row in rows if 'source_geometry_input_version' in row['provenance']]
    if not marked:
        return None
    _require(len(marked) == len(rows) and all(type(item['source_geometry_input_version']) is str
        and item['source_geometry_input_version'] == SOURCE_GEOMETRY_INPUT_VERSION
        and item.get('writer_input_version') == CONDITIONAL_WRITER_INPUT_VERSION for item in marked),
        'Unknown, malformed or mixed retained source geometry input version')
    return SOURCE_GEOMETRY_INPUT_VERSION


from .pdf_chart_ocr_bindings import VERSION as BINDINGS_VERSION



AXIS_FRAGMENT_RECIPE_VERSION = 'pdf-chart-secondary-source-x-fragments-v1'


def _axis_fragment_recipe_version(rows):
    marked = [row['provenance'] for row in rows if 'axis_fragment_recipe_version' in row['provenance']]
    if not marked:
        return None
    _require(len(marked) == len(rows) and all(type(p['axis_fragment_recipe_version']) is str
        and p['axis_fragment_recipe_version'] == AXIS_FRAGMENT_RECIPE_VERSION
        and p.get('source_geometry_input_version') == SOURCE_GEOMETRY_INPUT_VERSION
        and p.get('writer_input_version') == CONDITIONAL_WRITER_INPUT_VERSION for p in marked),
        'Unknown, malformed or mixed retained source-fragment recipe')
    return AXIS_FRAGMENT_RECIPE_VERSION


def _validate_effective_binding(provenance, record):
    recipe = provenance.get('axis_fragment_recipe_version')
    _require(('effective_bindings_metadata' in provenance) == (recipe is not None),
        'Effective secondary binding requires its retained recipe')
    if recipe is None:
        return
    _require(recipe == AXIS_FRAGMENT_RECIPE_VERSION, 'Known source-fragment recipe required')
    original, effective = provenance['bindings_metadata'], provenance['effective_bindings_metadata']
    rotated = provenance['rotated_ocr_metadata']
    primary_sha = sha256(_json(original).encode()).hexdigest()
    effective_sha = sha256(_json(effective).encode()).hexdigest()
    derived = effective.get('secondary_x_binding_provenance')
    if rotated.get('axis_fragment_input') is None:
        _require(rotated.get('axis_fragment_recipe_version') == recipe
            and rotated.get('axis_fragment_unresolved_reason') == 'no_source_y_strip'
            and rotated.get('ocr_calls') == 0 and rotated.get('mosaic') is None
            and rotated.get('tiles') == [] and rotated.get('observations') == []
            and rotated.get('raw_mosaic_ocr_stdout_sha256') == sha256(b'').hexdigest()
            and rotated.get('primary_bindings_identity') == {'version':BINDINGS_VERSION,'metadata_sha256':primary_sha}
            and effective == original and derived is None
            and record.get('bindings_identity') == {'version':BINDINGS_VERSION,'metadata_sha256':primary_sha},
            'No-secondary-input recipe must preserve original unknown bindings')
        return
    _require(type(derived) is dict and derived.get('diagnostic_version') == recipe
        and derived.get('original_bindings_sha256') == primary_sha
        and derived.get('raw_secondary_sha256') == rotated.get('raw_mosaic_ocr_stdout_sha256')
        and derived.get('combined_mosaic_sha256') == rotated.get('mosaic', {}).get('png_sha256')
        and rotated.get('axis_fragment_recipe_version') == recipe
        and rotated.get('primary_bindings_identity') == {'version':BINDINGS_VERSION,'metadata_sha256':primary_sha}
        and record.get('bindings_identity') == {'version':BINDINGS_VERSION,'metadata_sha256':effective_sha}
        and original.get('source') == effective.get('source')
        and all(derived.get(k) is False for k in ('original_primary_ocr_replaced',
            'raw_words_split_or_corrected','coordinates_inferred_from_strings',
            'label_accuracy_verified','publication_authority')),
        'Retained primary/effective binding or uncertainty provenance differs')


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _limit(value):
    _require(type(value) is int and 1 <= value <= MAXIMUM_INPUT_BYTES, 'Invalid complete input budget')


def _check(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError('CANCELLED')


def _page_ocr_context(row, original, source, cancel_event=None):
    """Raw geometry-filtered words; neither a corrected label nor an owner."""
    provenance = row['provenance']
    if 'page_label_ocr' not in provenance:
        _require(not any(key in provenance for key in ('page_label_source', 'page_label_figure_pixel_bbox')),
            'Incomplete full-page observation provenance')
        return None
    from . import pdf_native_chart_input as native
    from . import pdf_page_label_ocr as labels
    page = source['pages'][original['page']-1]
    record = provenance['page_label_ocr']
    _require(provenance.get('preparation_version') == 'pdf-chart-fresh-preparation-v1'
        and row['status'] == 'unsupported' and type(row['reason']) is str
        and row['reason'].startswith('native_appearance_unsupported:')
        and type(provenance.get('appearance')) is dict and provenance['appearance'].get('eligibility') is False
        and provenance.get('page_label_source') == page and type(record) is dict
        and set(record) == {'metadata_json', 'raw_ocr_stdout_base64'},
        'Same unsupported original page required for raw labels')
    limits = native.PdfNativeChartInputLimits(**provenance['generation_limits']['ocr'])
    _require(type(record['metadata_json']) is str and len(record['metadata_json'].encode('utf-8'))
        <= 3*(limits.ocr_stdout_bytes+limits.words*256+2048), 'Bounded complete page metadata required')
    metadata = json.loads(record['metadata_json'])
    size = [page['width'], page['height']]
    _require(metadata['version'] == labels.VERSION and metadata['source_png_sha256'] == page['sha256']
        and metadata['source_dimensions'] == size and _sha(metadata['source_pixel_sha256'])
        and all(metadata[key] is False for key in ('source_resized', 'source_clipped', 'labels_corrected',
            'relationship_ownership_verified', 'semantic_accuracy_verified', 'publication_authority')),
        'Raw full-page observations cannot grant semantic authority')
    orientations, transcripts = metadata['orientations'], record['raw_ocr_stdout_base64']
    _require(type(orientations) is list and type(transcripts) is list and len(transcripts) == 3
        and all(type(item['rotation_degrees_ccw']) is int for item in orientations)
        and [item['rotation_degrees_ccw'] for item in orientations] == [0, 90, 270],
        'All original and conflicting OCR orientations required')
    bounds = provenance['page_label_figure_pixel_bbox']
    _require(type(bounds) is list and len(bounds) == 4 and all(type(v) is int for v in bounds)
        and 0 <= bounds[0] < bounds[2] <= size[0] and 0 <= bounds[1] < bounds[3] <= size[1],
        'Original full-page figure pixel scope required')
    words = []
    for orientation, encoded in zip(orientations, transcripts, strict=True):
        _check(cancel_event)
        _require(type(encoded) is str and len(encoded) <= 4*((limits.ocr_stdout_bytes+2)//3),
            'Bounded complete raw page transcript required')
        raw = base64.b64decode(encoded, validate=True)
        _require(sha256(raw).hexdigest() == orientation['raw_ocr_stdout_sha256'],
            'Original full-page transcript bytes changed')
        rotation = orientation['rotation_degrees_ccw']
        dimensions = size if rotation == 0 else list(reversed(size))
        _require(orientation['derived_dimensions'] == dimensions and _sha(orientation['derived_pixel_sha256'])
            and _sha(orientation['derived_png_sha256'])
            and (rotation != 0 or orientation['derived_png_sha256'] == page['sha256']
                and orientation['derived_pixel_sha256'] == metadata['source_pixel_sha256']),
            'Full-page orientation source changed')
        report = native._report(raw, orientation['derived_png_sha256'], dimensions, metadata['language'], limits)
        _require(report == orientation['report'] and all(type(v) is int for v in report['source_dimensions']),
            'Original full-page OCR report changed')
        boxes = orientation['source_word_bbox_xywh']
        _require(type(boxes) is list and len(boxes) == len(report['lines']), 'Complete line geometry required')
        ordinal = 0
        for line, line_boxes in zip(report['lines'], boxes, strict=True):
            _require(type(line_boxes) is list and len(line_boxes) == len(line['words']),
                'Complete word geometry required')
            for word, box in zip(line['words'], line_boxes, strict=True):
                _check(cancel_event)
                expected = labels._source_box(labels._box(word['bbox'], dimensions), size, rotation)
                _require(type(word['text']) is str and labels._box(box, size) == expected,
                    'Raw word literal or original-frame geometry changed')
                if box[0] < bounds[2] and box[1] < bounds[3] and box[0]+box[2] > bounds[0] and box[1]+box[3] > bounds[1]:
                    words.append([rotation, ordinal, word['text'], box])
                ordinal += 1
    return dict(dimensions=size, figure_pixel_bbox=bounds, words=words)


@dataclass(frozen=True, slots=True)
class PdfChartSupplement:
    metadata_json: str

    def identity(self, *, cancel_event=None):
        record = _load(self, cancel_event=cancel_event)
        return dict(version=VERSION, sha256=_hash(self.metadata_json),
            bytes=len(self.metadata_json.encode('utf-8')),
            source_geometry_sha256=record['source_identity']['geometry']['geometry_sha256'],
            source_raw_sha256=record['source_identity']['geometry']['raw_sha256'],
            layout_catalog_sha256=record['layout_identity']['catalog_sha256'],
            figure_count=len(record['figure_inventory']), publication_authority=False)


def _source(source, layout, inventory, cancel_event=None):
    _check(cancel_event)
    _require(type(source) is dict and type(layout) is dict and layout.get('source') == source,
        'Same freshly checked source/layout identities required')
    geometry = source.get('geometry', {})
    _require(type(geometry) is dict and _sha(geometry.get('raw_sha256'))
        and _sha(geometry.get('geometry_sha256')) and type(source.get('pages')) is list
        and _sha(layout.get('catalog_sha256')) and type(layout.get('crops')) is list,
        'Complete source/layout identity required')
    _require(type(inventory) in (tuple, list) and len(inventory) <= 4096, 'Complete figure inventory required')
    crops = {crop['source_id']: crop for crop in layout['crops']}
    seen = set()
    for row in inventory:
        _check(cancel_event)
        _require(type(row) is dict and set(row) == _INVENTORY_FIELDS
            and type(row['unit_id']) is str and 0 < len(row['unit_id']) <= 160
            and row['unit_id'] not in seen and type(row['page']) is int
            and 1 <= row['page'] <= len(source['pages'])
            and (row['label'] is None or type(row['label']) is str and len(row['label']) <= 256)
            and _sha(row['crop_sha256']), 'Distinct complete layout figure rows required')
        box = row['bbox']
        _require(type(box) is list and len(box) == 4
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1_000_000 for n in box)
            and box[0] < box[2] and box[1] < box[3], 'Original figure bbox required')
        _require(row['unit_id'] in crops and crops[row['unit_id']]['sha256'] == row['crop_sha256'],
            'Figure crop differs from freshly checked layout')
        seen.add(row['unit_id'])


def _validate_roi_fallback(row):
    provenance = row['provenance']
    fallback = provenance.get('native_fallback_reason')
    _require(type(fallback) is str and 0 < len(fallback) <= 256
        and (fallback in {'no_unique_same_source_native_placement',
            'no_complete_rectangular_chart_panel_candidates'}
            or fallback.startswith('native_appearance_unsupported:'))
        and provenance.get('visual_scope') is None
        and ('appearance' not in provenance or type(provenance['appearance']) is dict
            and provenance['appearance'].get('eligibility') is False),
        'Direct ROI cannot claim eligible native appearance or scope')


def _validate_uninterpreted(row, inventory, source, cancel_event=None):
    """An explicit opt-in omission, not a failed geometry test or a reading."""
    from . import pdf_native_chart_input as native_input
    from .pdf_page_label_ocr import _box
    provenance = row['provenance']
    _require(provenance.get('writer_input_version') == WRITER_INPUT_VERSION
        and provenance.get('preparation_version') == 'pdf-chart-fresh-preparation-v1'
        and row['status'] == 'unsupported' and row['reading_json'] is row['reading_sha256'] is None
        and row['raw_mosaic_ocr_stdout_base64'] == ''
        and row['raw_mosaic_ocr_stdout_sha256'] == sha256(b'').hexdigest()
        and not any(key in provenance for key in ('crop_unsupported_reason', 'bindings_metadata',
            'atlas_metadata', 'rotated_ocr_metadata', 'literal_support_metadata',
            'literal_support_text_kind', 'page_label_ocr', 'page_appearance_roi_unsupported')),
        'Uninterpreted observations require explicit writer mode and no invented diagnostics')
    crop = provenance.get('page_appearance_crop')
    if crop is not None:
        png_sha, size = crop['crop_png_sha256'], crop['dimensions']
    else:
        native, appearance = provenance['native_metadata'], provenance['appearance']
        png_sha, size = native['png_sha256'], [native['width'], native['height']]
        _require(appearance['eligibility'] is True and appearance['complete_page_text_retained'] is True
            and appearance['semantic_accuracy_verified'] is False
            and appearance['raw_pdf_sha256'] == source['geometry']['raw_sha256']
            and appearance['png_sha256'] == png_sha and _sha(png_sha)
            and appearance['pixel_sha256'] == native['pixel_sha256'] and _sha(native['pixel_sha256'])
            and appearance['page'] == inventory['page']
            and appearance['object_ordinal'] == native['object_ordinal']
            and appearance['dimensions'] == size
            and all(abs(a-b) <= .01 for a, b in zip(native['page_bbox'], inventory['bbox'], strict=True)),
            'Uninterpreted native observations differ from original appearance')
        scope = provenance['visual_scope']
        _require(scope is None or type(scope) is dict and type(scope.get('visual_units')) is list
            and bool(scope['visual_units']) and all(unit['unit_id'] == inventory['unit_id']
                and unit['page'] == inventory['page'] and unit['label'] == inventory['label']
                and unit['native_view_metadata']['source_object_id'] == native['source_id']
                for unit in scope['visual_units']), 'Uninterpreted native scope belongs to another figure')
    raw = base64.b64decode(row['raw_ocr_stdout_base64'], validate=True)
    limits = native_input.PdfNativeChartInputLimits(**provenance['generation_limits']['ocr'])
    raw_record = native_input._load(raw)
    language = raw_record.get('language') if type(raw_record) is dict else None
    report = native_input._report(raw, png_sha, size, language, limits)
    descriptor = dict(raw_ocr_stdout_sha256=row['raw_ocr_stdout_sha256'],
        ocr=native_input._compact_report(report))
    if crop is not None:
        descriptor.update(source_kind=('direct_pdf_appearance_roi' if crop['version']
            == 'pdf-checked-direct-chart-roi-v1' else 'checked_page_appearance_crop'),
            allow_antialiased_axes=True)
    _require(provenance.get('ocr_descriptor') == descriptor
        and len(_json(descriptor).encode('utf-8')) <= limits.descriptor_bytes,
        'Uninterpreted exact OCR descriptor changed or exceeded budget')
    for line in report['lines']:
        for word in line['words']:
            _check(cancel_event)
            _require(type(word['text']) is str, 'Uninterpreted OCR words must remain literals')
            _box(word['bbox'], size)


def _validate_roi_unsupported(row, inventory, source, layout):
    provenance = row['provenance']; roi = provenance['page_appearance_roi_unsupported']
    _require(type(roi) is dict and set(roi) == {'version', 'dpi', 'unit_id', 'page', 'page_bbox',
        'unit_sha256', 'layout_catalog_sha256', 'source_pdf_sha256', 'reason'}
        and provenance.get('preparation_version') == 'pdf-chart-fresh-preparation-v1'
        and roi['version'] == 'pdf-direct-page-chart-roi-ineligible-v1'
        and type(roi['dpi']) is int and roi['dpi'] == 288
        and roi['unit_id'] == inventory['unit_id'] and type(roi['page']) is int
        and roi['page'] == inventory['page'] and roi['page_bbox'] == inventory['bbox']
        and _sha(roi['unit_sha256']) and roi['layout_catalog_sha256'] == layout['catalog_sha256']
        and roi['source_pdf_sha256'] == source['geometry']['raw_sha256']
        and roi['reason'] in {'unsupported_checked_caption_region', 'ambiguous_checked_figure_region',
            'unsupported_page_rotation', 'unsupported_page_coordinate_box'}
        and row['status'] == 'unsupported' and row['reading_json'] is None
        and 'page_appearance_crop' not in provenance
        and 'ocr_descriptor' not in provenance and 'crop_unsupported_reason' not in provenance
        and row['raw_ocr_stdout_base64'] == row['raw_mosaic_ocr_stdout_base64'] == '',
        'Explicit unsupported same-source ROI requires no invented reading or OCR')
    _validate_roi_fallback(row)


def _validate_direct_roi(row, inventory, source, layout, record):
    # Pure bounded geometry checks only. The producer/cold restore must also
    # validate the process-local capability against fresh original PDF pixels.
    from .pdf_figure_roi_source import PdfFigureRoiLimits, VERSION as ROI_VERSION, _placement, _image_id
    provenance = row['provenance']; crop = provenance['page_appearance_crop']
    fields = {'version', 'unit_id', 'page', 'page_source', 'page_bbox', 'page_pixel_bbox',
        'crop_png_sha256', 'crop_pixel_sha256', 'dimensions', 'source_pdf_sha256', 'coordinate_frame',
        'allow_antialiased_axes', 'native_resource', 'semantic_accuracy_verified', 'roi_source_id', 'roi_descriptor'}
    _require(set(crop) == fields and type(crop['roi_descriptor']) is dict,
        'Complete direct ROI provenance required')
    roi = crop['roi_descriptor']
    spec_fields = {'version', 'raw_bytes', 'raw_sha256', 'page_count', 'page', 'unit_id', 'unit_sha256',
        'layout_catalog_sha256', 'coordinate_domain', 'page_dimensions', 'bbox', 'dpi', 'limits', 'scale',
        'page_pixel_dimensions', 'page_pixel_bbox', 'width', 'height', 'crop_margins'}
    _require(set(roi) == spec_fields | {'encoded_sha256', 'encoded_bytes', 'pixel_sha256', 'pixel_mode',
        'appearance_relation', 'caption_ownership_proven', 'axes_calibrated', 'semantic_accuracy_verified'},
        'Exact source-owned direct ROI descriptor required')
    limits = PdfFigureRoiLimits(**roi['limits'])
    _require(type(roi['dpi']) is int and roi['dpi'] == 288
        and limits.dpi == 288 and type(roi['page_dimensions']) is list and len(roi['page_dimensions']) == 2
        and all(type(roi[k]) is int for k in ('width', 'height'))
        and all(type(roi[k]) is list and all(type(n) is int for n in roi[k])
            for k in ('page_pixel_bbox', 'page_pixel_dimensions'))
        and type(crop['page_pixel_bbox']) is list and all(type(n) is int for n in crop['page_pixel_bbox'])
        and type(crop['dimensions']) is list and all(type(n) is int for n in crop['dimensions']),
        'Direct ROI must retain its actual 288dpi coordinate frame')
    placement = _placement(*roi['page_dimensions'], inventory['bbox'], 288, limits)
    page = source['pages'][inventory['page']-1]
    _require(provenance.get('preparation_version') == 'pdf-chart-fresh-preparation-v1'
        and crop['version'] == 'pdf-checked-direct-chart-roi-v1'
        and crop['unit_id'] == roi['unit_id'] == inventory['unit_id']
        and type(crop['page']) is int and type(roi['page']) is int
        and crop['page'] == roi['page'] == inventory['page']
        and crop['page_source'] == page and crop['page_bbox'] == roi['bbox'] == inventory['bbox']
        and crop['source_pdf_sha256'] == roi['raw_sha256'] == source['geometry']['raw_sha256']
        and type(roi['raw_bytes']) is int and roi['raw_bytes'] == source['geometry']['raw_bytes']
        and type(roi['page_count']) is int and roi['page_count'] == len(source['pages'])
        and roi['version'] == ROI_VERSION and _sha(roi['unit_sha256'])
        and roi['layout_catalog_sha256'] == layout['catalog_sha256']
        and roi['coordinate_domain'] == 'zero-origin-uncropped-unrotated-page-canvas'
        and all(roi[k] == v for k, v in placement.items())
        and crop['page_pixel_bbox'] == roi['page_pixel_bbox']
        and crop['dimensions'] == [roi['width'], roi['height']]
        and crop['crop_png_sha256'] == roi['encoded_sha256'] and _sha(roi['encoded_sha256'])
        and crop['crop_pixel_sha256'] == roi['pixel_sha256'] and _sha(roi['pixel_sha256'])
        and type(roi['encoded_bytes']) is int and 0 < roi['encoded_bytes'] <= limits.image_bytes
        and roi['pixel_mode'] == 'RGB' and roi['appearance_relation'] == 'direct-pdf-static-figure-region-appearance'
        and all(roi[k] is False for k in ('caption_ownership_proven', 'axes_calibrated', 'semantic_accuracy_verified'))
        and crop['roi_source_id'] == _image_id({k: roi[k] for k in spec_fields},
            roi['pixel_sha256'], roi['encoded_sha256'])
        and crop['coordinate_frame'] == 'roi-local' and crop['allow_antialiased_axes'] is True
        and crop['native_resource'] is crop['semantic_accuracy_verified'] is False,
        'Direct ROI source/geometry/pixel/coordinate pins differ')
    _validate_roi_fallback(row)
    if row['status'] == 'unsupported':
        _require(row['reason'] == CURVE_FITTING_NOT_REQUESTED or
            provenance.get('crop_unsupported_reason') in {'no_complete_rectangular_panel_candidates',
            'unresolved_original_condition_anchors', 'unresolved_or_grayscale_legend_candidates'},
            'Explicit unsupported direct ROI chart geometry required')
    if record is not None:
        _require(type(provenance.get('ocr_descriptor')) is dict
            and 'crop_unsupported_reason' not in provenance
            and provenance['ocr_descriptor'].get('source_kind') == 'direct_pdf_appearance_roi'
            and provenance['ocr_descriptor'].get('allow_antialiased_axes') is True
            and record['source_png_sha256'] == crop['crop_png_sha256']
            and record['source_dimensions'] == crop['dimensions']
            and record.get('source_pixel_sha256') == crop['crop_pixel_sha256']
            and record['source_object_id'] is None and record['visual_scope_sha256'] is None
            and record['subject_inventory']['bindings'] == []
            and record['subject_inventory']['association_verified'] is False
            and all(panel['subject_binding'] is None
                and panel['metric_binding']['metric_ownership_verified'] is False for panel in record['panels']),
            'Direct ROI reading cannot acquire native or semantic ownership')


def _validate_page_crop(row, inventory, source, record=None, layout=None):
    provenance = row['provenance']
    crop = provenance.get('page_appearance_crop')
    if type(crop) is dict and crop.get('version') == 'pdf-checked-direct-chart-roi-v1':
        _validate_direct_roi(row, inventory, source, layout, record)
        return
    _require(type(crop) is dict and set(crop) == {'version', 'unit_id', 'page', 'page_source',
        'page_bbox', 'page_pixel_bbox', 'crop_png_sha256', 'crop_pixel_sha256', 'dimensions',
        'source_pdf_sha256', 'coordinate_frame', 'allow_antialiased_axes', 'native_resource',
        'semantic_accuracy_verified'}, 'Complete checked-page crop provenance required')
    page = source['pages'][inventory['page']-1]
    box, size = crop['page_pixel_bbox'], crop['dimensions']
    _require(provenance.get('preparation_version') == 'pdf-chart-fresh-preparation-v1'
        and crop['version'] == 'pdf-checked-page-chart-crop-v1'
        and crop['unit_id'] == inventory['unit_id'] and type(crop['page']) is int
        and crop['page'] == inventory['page']
        and _json(crop['page_source']) == _json(page) and _json(crop['page_bbox']) == _json(inventory['bbox'])
        and crop['source_pdf_sha256'] == source['geometry']['raw_sha256']
        and crop['crop_png_sha256'] == inventory['crop_sha256'] and _sha(crop['crop_pixel_sha256'])
        and crop['coordinate_frame'] == 'crop-local' and crop['allow_antialiased_axes'] is True
        and crop['native_resource'] is False and crop['semantic_accuracy_verified'] is False
        and type(box) is list and len(box) == 4 and all(type(n) is int for n in box)
        and 0 <= box[0] < box[2] <= page['width'] and 0 <= box[1] < box[3] <= page['height']
        and type(size) is list and size == [box[2]-box[0], box[3]-box[1]]
        and all(type(n) is int for n in size) and size[0]*size[1] <= 16_000_000,
        'Checked-page crop source/geometry/algorithm pins differ')
    fallback = provenance.get('native_fallback_reason')
    _require(type(fallback) is str and 0 < len(fallback) <= 256
        and (fallback in {'no_unique_same_source_native_placement',
            'no_complete_rectangular_chart_panel_candidates'}
            or fallback.startswith('native_appearance_unsupported:'))
        and provenance.get('visual_scope') is None
        and ('appearance' not in provenance or type(provenance['appearance']) is dict
            and provenance['appearance'].get('eligibility') is False),
        'A page crop cannot claim eligible native appearance or scope')
    if row['status'] == 'unsupported':
        _require(row['reason'] == CURVE_FITTING_NOT_REQUESTED or provenance.get('crop_unsupported_reason') in {
            'no_complete_rectangular_panel_candidates', 'unresolved_original_condition_anchors',
            'unresolved_or_grayscale_legend_candidates'}, 'Explicit unsupported crop geometry required')
    if record is not None:
        _require(type(provenance.get('ocr_descriptor')) is dict
            and 'crop_unsupported_reason' not in provenance
            and provenance['ocr_descriptor'].get('source_kind') == 'checked_page_appearance_crop'
            and provenance['ocr_descriptor'].get('allow_antialiased_axes') is True
            and record['source_png_sha256'] == crop['crop_png_sha256']
            and record['source_dimensions'] == size and record.get('source_pixel_sha256') == crop['crop_pixel_sha256']
            and record['source_object_id'] is None and record['visual_scope_sha256'] is None
            and record['subject_inventory']['bindings'] == []
            and record['subject_inventory']['association_verified'] is False
            and all(panel['subject_binding'] is None
                and panel['metric_binding']['metric_ownership_verified'] is False for panel in record['panels']),
            'Crop-local reading cannot acquire native or semantic ownership')


def _validate_read(row, inventory, source, cancel_event=None, layout=None):
    _check(cancel_event)
    reading = PdfCompleteChartReading(row['reading_json'])
    _require(_hash(reading.metadata_json) == row['reading_sha256'], 'Retained reading identity changed')
    try:
        render_complete_source_chart(reading, expected_reading_sha256=row['reading_sha256'],
            cancel_event=cancel_event)
    finally:
        _check(cancel_event)
    record = json.loads(reading.metadata_json)
    _require(record['source_pdf_sha256'] == source['geometry']['raw_sha256']
        and all(record[key] is False for key in ('semantic_accuracy_verified', 'series_ownership_verified',
            'numerical_accuracy_verified', 'source_text_corrected')), 'Reading is unverified same-source evidence only')
    provenance = row['provenance']
    _validate_effective_binding(provenance, record)
    _require(('source_geometry_input_version' in provenance) == ('source_geometry_input_version' in record)
        and record.get('source_geometry_input_version') == provenance.get('source_geometry_input_version'),
        'Complete reading and retained source geometry provenance differ')
    if 'page_appearance_crop' in provenance:
        _validate_page_crop(row, inventory, source, record, layout)
        _require(provenance['ocr_descriptor']['raw_ocr_stdout_sha256'] == row['raw_ocr_stdout_sha256']
            and record['raw_literal_support']['original_reading']['raw_mosaic_ocr_stdout_sha256']
                == row['raw_mosaic_ocr_stdout_sha256'], 'Exact crop-local raw OCR transcripts changed')
        return record
    appearance, native, scope = provenance['appearance'], provenance['native_metadata'], provenance['visual_scope']
    _require(type(appearance) is dict and type(native) is dict and (scope is None or type(scope) is dict),
        'Complete retained native appearance/scope provenance required')
    _require(appearance['eligibility'] is True and appearance['complete_page_text_retained'] is True
        and appearance['semantic_accuracy_verified'] is False
        and appearance['raw_pdf_sha256'] == source['geometry']['raw_sha256']
        and appearance['png_sha256'] == native['png_sha256'] == record['source_png_sha256']
        and appearance['pixel_sha256'] == native['pixel_sha256'] and _sha(native['pixel_sha256'])
        and appearance['page'] == inventory['page']
        and appearance['object_ordinal'] == native['object_ordinal']
        and native['source_id'] == record['source_object_id']
        and appearance['dimensions'] == record['source_dimensions'] == [native['width'], native['height']],
        'Original PDF/native appearance pins differ')
    box = native['page_bbox']
    _require(type(box) is list and len(box) == 4
        and all(type(n) in (int, float) and math.isfinite(n) for n in box)
        and all(abs(a-b) <= .01 for a, b in zip(box, inventory['bbox'], strict=True)),
        'Original native figure and layout geometry differ')
    _require(record['visual_scope_sha256'] == (_hash(_json(scope)) if scope is not None else None),
        'Retained complete visual scope changed')
    if scope is not None:
        units = scope['visual_units']
        _require(type(units) is list and bool(units), 'Complete original visual scope units required')
        for unit in units:
            _check(cancel_event)
            _require(type(unit) is dict and unit['unit_id'] == inventory['unit_id']
                and unit['page'] == inventory['page'] and unit['label'] == inventory['label']
                and unit['native_view_metadata']['source_object_id'] == record['source_object_id'],
                'Original visual scope belongs to another layout figure')
    if 'source_pixel_sha256' in record:
        _require(record['source_pixel_sha256'] == native['pixel_sha256'], 'Retained source pixels changed')
    if 'ocr_descriptor' in provenance:
        _require(provenance['ocr_descriptor']['raw_ocr_stdout_sha256'] == row['raw_ocr_stdout_sha256'],
            'Original raw OCR transcript changed')
    if 'raw_literal_support' in record:
        _require(record['raw_literal_support']['original_reading']['raw_mosaic_ocr_stdout_sha256']
            == row['raw_mosaic_ocr_stdout_sha256'], 'Rotated raw OCR transcript changed')
    return record



def _validate_key_information(row, inventory, source, layout):
    """Source-image input accounting, not observed values or figure accuracy."""
    from dataclasses import asdict
    from .pdf_chart_preparation import (PdfChartPreparationLimits,PdfNativeFigureLimits,
        PdfRotatedYOcrLimits,native_input)
    provenance = row['provenance']
    _require(type(provenance) is dict and set(provenance) ==
        {'preparation_version','generation_limits','key_information_source'},
        'Key-information cannot retain chart-fitting/OCR provenance')
    values = provenance['generation_limits']
    defaults = asdict(PdfChartPreparationLimits())
    _require(type(values) is dict and set(values) == set(defaults)
        and all(type(values[k]) is dict and set(values[k]) == set(defaults[k])
            for k in ('native','ocr','rotated')),
        'Exact existing bounded preparation limits required')
    PdfChartPreparationLimits(**{**values, 'native':PdfNativeFigureLimits(**values['native']),
        'ocr':native_input.PdfNativeChartInputLimits(**values['ocr']),
        'rotated':PdfRotatedYOcrLimits(**values['rotated'])})
    evidence = provenance['key_information_source']
    _require(type(evidence) is dict and set(evidence) ==
        {'version','source_pdf_sha256','layout_catalog_sha256','unit_id','page',
         'page_source','figure_inventory_sha256','exhaustive_point_recovery_requested',
         'semantic_accuracy_verified','numerical_accuracy_verified'}
        and provenance['preparation_version'] == 'pdf-chart-fresh-preparation-v1'
        and evidence['version'] == KEY_INFORMATION_SOURCE_VERSION
        and evidence['source_pdf_sha256'] == source['geometry']['raw_sha256']
        and evidence['layout_catalog_sha256'] == layout['catalog_sha256']
        and evidence['unit_id'] == row['unit_id'] == inventory['unit_id']
        and type(evidence['page']) is int and evidence['page'] == inventory['page']
        and _json(evidence['page_source']) == _json(source['pages'][inventory['page']-1])
        and evidence['figure_inventory_sha256'] == _hash(_json(inventory))
        and all(evidence[k] is False for k in ('exhaustive_point_recovery_requested',
            'semantic_accuracy_verified','numerical_accuracy_verified'))
        and row['status'] == 'source_images' and row['reason'] == KEY_INFORMATION_REASON
        and row['reading_json'] is row['reading_sha256'] is None,
        'Exact unchanged original page/image figure provenance required')
    for name in ('raw_ocr_stdout','raw_mosaic_ocr_stdout'):
        _require(row[name+'_base64'] == '' and row[name+'_sha256'] == sha256(b'').hexdigest(),
            'Key-information has no hidden OCR or recovered point values')


def _render_key_information(record, captions, maximum_markdown_bytes, cancel_event=None):
    if not record['figures']:
        return ''
    sections = ['## 원본 그림의 근거',
        '그림의 주요 정보는 원본 페이지 이미지와 본문·캡션을 함께 읽어 본문에 설명합니다. '
        '모든 곡선 점의 복원이나 별도 수치 정확성 검증을 수행한 것은 아닙니다.']
    for ordinal, inventory in enumerate(record['figure_inventory']):
        _check(cancel_event)
        label = _cell(inventory['label']) if inventory['label'] is not None else '표기 미확정'
        sections.append(f"### Figure {label} · p. {inventory['page']}")
        if captions is not None:
            sections.append(_literal_block(captions[ordinal]))
    markdown = '\n\n'.join(sections)+'\n'
    _require(len(markdown.encode()) <= maximum_markdown_bytes,
        'Complete figure provenance appendix exceeds its byte budget')
    _check(cancel_event)
    return markdown


def _load(supplement, expected_sha256=None, maximum_input_bytes=MAXIMUM_INPUT_BYTES, cancel_event=None):
    _check(cancel_event)
    _limit(maximum_input_bytes)
    _require(type(supplement) is PdfChartSupplement and type(supplement.metadata_json) is str
        and len(supplement.metadata_json.encode('utf-8')) <= maximum_input_bytes,
        'Bounded unchanged complete supplement required')
    if expected_sha256 is not None:
        _require(_sha(expected_sha256) and _hash(supplement.metadata_json) == expected_sha256,
            'Retained supplement identity changed')
    try:
        record = json.loads(supplement.metadata_json)
        marker = _literal_context_marker(record)
        _require(_json(record) == supplement.metadata_json and set(record)-({'literal_context_marker'} if marker is not None else set()) == {'version', 'source_identity',
            'layout_identity', 'figure_inventory', 'figures', 'all_figures_accounted_for',
            'semantic_accuracy_verified', 'numerical_accuracy_verified', 'publication_authority'},
            'Canonical complete supplement schema required')
        _require(record['version'] == VERSION and record['all_figures_accounted_for'] is True
            and all(record[key] is False for key in ('semantic_accuracy_verified',
                'numerical_accuracy_verified', 'publication_authority')), 'No chart publication or truth authority')
        _source(record['source_identity'], record['layout_identity'], record['figure_inventory'], cancel_event)
        if marker in (LITERAL_CONTEXT_NATIVE_VERSION,LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION):
            _native_text_record(record['literal_context_marker']['native_text_presentation'],record['source_identity'],cancel_event)
        rows = record['figures']; inventory = record['figure_inventory']
        _require(type(rows) is list and len(rows) == len(inventory)
            and [row['unit_id'] for row in rows] == [row['unit_id'] for row in inventory],
            'Every original figure must be accounted for exactly once and in order')
        _writer_input_version(rows)
        _source_geometry_input_version(rows)
        _axis_fragment_recipe_version(rows)
        for row, original in zip(rows, inventory, strict=True):
            _check(cancel_event)
            _require(type(row) is dict and set(row) == _STORED_FIELDS and type(row['provenance']) is dict,
                'Complete retained figure evidence required')
            if marker in (LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION):
                _validate_key_information(row,original,record['source_identity'],record['layout_identity'])
                continue
            _page_ocr_context(row, original, record['source_identity'], cancel_event)
            if 'page_appearance_roi_unsupported' in row['provenance']:
                _validate_roi_unsupported(row, original, record['source_identity'], record['layout_identity'])
            if 'page_appearance_crop' in row['provenance']:
                _validate_page_crop(row, original, record['source_identity'], layout=record['layout_identity'])
            for name in ('raw_ocr_stdout', 'raw_mosaic_ocr_stdout'):
                _check(cancel_event)
                raw = base64.b64decode(row[name+'_base64'], validate=True)
                _require(sha256(raw).hexdigest() == row[name+'_sha256'], 'Exact raw OCR bytes changed')
            if row['status'] == 'read':
                _require(row['reason'] is None and type(row['reading_json']) is str
                    and _sha(row['reading_sha256']), 'Complete reading or explicit unsupported status required')
                _validate_read(row, original, record['source_identity'], cancel_event, record['layout_identity'])
            else:
                _require(row['status'] == 'unsupported' and row['reading_json'] is None
                    and row['reading_sha256'] is None and type(row['reason']) is str
                    and 0 < len(row['reason']) <= 256, 'Unsupported figures cannot acquire invented readings')
            if row['reason'] == CURVE_FITTING_NOT_REQUESTED:
                _validate_uninterpreted(row, original, record['source_identity'], cancel_event)
            _crop_ocr_context(row, cancel_event)
        _check(cancel_event)
        return record
    except PdfProcessError:
        raise
    except PdfChartSupplementError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise PdfChartSupplementError('Complete unchanged same-source chart evidence required') from error


def prepare_chart_supplement(*, source_identity, layout_identity, figure_inventory, figures,
                             maximum_input_bytes=MAXIMUM_INPUT_BYTES, cancel_event=None,
                              literal_context_version=None, native_text_presentation=None):
    """Retain each fresh figure as a complete typed reading or explicit unknown."""
    _check(cancel_event); _limit(maximum_input_bytes)
    _require(literal_context_version is None or (type(literal_context_version) is str
        and literal_context_version in (LITERAL_CONTEXT_VERSION, LITERAL_CONTEXT_WRITER_VERSION, LITERAL_CONTEXT_TABLE_VERSION, LITERAL_CONTEXT_NATIVE_VERSION, LITERAL_CONTEXT_KEY_VERSION, LITERAL_CONTEXT_CLAUSE_VERSION, LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION)),
        'Known literal context selection required')
    _require((literal_context_version in (LITERAL_CONTEXT_NATIVE_VERSION,LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION)) == (native_text_presentation is not None),
        'Native presentation belongs to the fresh v4 marker only')
    try:
        _source(source_identity, layout_identity, figure_inventory, cancel_event)
        _require(type(figures) in (tuple, list) and len(figures) == len(figure_inventory),
            'Complete figure evidence required, never a passing prefix')
        rows = []
        for figure in figures:
            _check(cancel_event)
            _require(type(figure) is dict and set(figure) == _FIGURE_FIELDS,
                'Exact fresh figure evidence fields required')
            reading = figure['reading']
            _require(reading is None or type(reading) is PdfCompleteChartReading, 'Typed complete reading required')
            row = {key: figure[key] for key in ('unit_id', 'status', 'reason', 'provenance')}
            row.update(reading_json=reading.metadata_json if reading else None,
                reading_sha256=_hash(reading.metadata_json) if reading else None)
            for name in ('raw_ocr_stdout', 'raw_mosaic_ocr_stdout'):
                _check(cancel_event)
                raw = figure[name]
                _require(type(raw) is bytes and len(raw) <= maximum_input_bytes, 'Bounded exact raw OCR bytes required')
                row[name+'_base64'] = base64.b64encode(raw).decode('ascii')
                row[name+'_sha256'] = sha256(raw).hexdigest()
            rows.append(row)
        metadata = _json(dict(version=VERSION, source_identity=source_identity, layout_identity=layout_identity,
            figure_inventory=list(figure_inventory), figures=rows, all_figures_accounted_for=True,
            semantic_accuracy_verified=False, numerical_accuracy_verified=False, publication_authority=False,
            **({'literal_context_marker': {'version': literal_context_version,
                **({'native_text_presentation':native_text_presentation}
                   if literal_context_version in (LITERAL_CONTEXT_NATIVE_VERSION,LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION) else {})}}
                if literal_context_version is not None else {})))
        result = PdfChartSupplement(metadata)
        _load(result, maximum_input_bytes=maximum_input_bytes, cancel_event=cancel_event)
        return result
    except PdfProcessError:
        raise
    except PdfChartSupplementError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
        raise PdfChartSupplementError('Complete fresh chart evidence required') from error


def _load_bound_chart_supplement(supplement, *, source_identity, layout_identity, figure_inventory,
                                expected_sha256=None, maximum_input_bytes=MAXIMUM_INPUT_BYTES, cancel_event=None):
    """One complete load bound to current inputs; no caller-supplied cached record.

    The returned parsed record belongs only to the synchronous restoration call.
    Public APIs always perform this full load and never accept this record back.
    """
    record = _load(supplement, expected_sha256, maximum_input_bytes, cancel_event)
    _require(record['source_identity'] == source_identity and record['layout_identity'] == layout_identity
        and record['figure_inventory'] == list(figure_inventory), 'Fresh chart source/layout/inventory changed')
    identity = dict(version=VERSION, sha256=_hash(supplement.metadata_json),
        bytes=len(supplement.metadata_json.encode('utf-8')),
        source_geometry_sha256=record['source_identity']['geometry']['geometry_sha256'],
        source_raw_sha256=record['source_identity']['geometry']['raw_sha256'],
        layout_catalog_sha256=record['layout_identity']['catalog_sha256'],
        figure_count=len(record['figure_inventory']), publication_authority=False)
    return record, identity


def checked_chart_supplement(supplement, *, source_identity, layout_identity, figure_inventory,
                             expected_sha256=None, maximum_input_bytes=MAXIMUM_INPUT_BYTES, cancel_event=None):
    """Return the unchanged typed capability, bound to freshly checked inputs."""
    _load_bound_chart_supplement(supplement, source_identity=source_identity, layout_identity=layout_identity,
        figure_inventory=figure_inventory, expected_sha256=expected_sha256,
        maximum_input_bytes=maximum_input_bytes, cancel_event=cancel_event)
    return supplement


def _point_context(row, inventory, cancel_event=None):
    """Reuse the compact complete format; later request admission owns wire caps."""
    _check(cancel_event)
    context = json.loads(render_chart_model_context(PdfCompleteChartReading(row['reading_json']),
        expected_reading_sha256=row['reading_sha256'], maximum_context_bytes=MAXIMUM_INPUT_BYTES,
        cancel_event=cancel_event))
    context.update(figure_label=inventory['label'], page=inventory['page'])
    if row['provenance'].get('writer_input_version') == CONDITIONAL_WRITER_INPUT_VERSION:
        # Preserve the original series position and its observed swatch. This
        # is a candidate association, not an OCR correction or ownership proof.
        original = json.loads(row['reading_json'])
        context['series_paint_candidates'] = dict(
            columns=['source_word_id', 'raw_label', 'source_swatch_rgb'],
            rows=[[legend['source_word_id'], legend['label_literal'], legend['paint']['exact_rgb']]
                for legend in original['legend_readings']],
            matrix_row_order='same_as_raw_series', ownership_verified=False,
            source_labels_corrected=False, printed_measurements_verified=False)
    return context


def _crop_ocr_context(row, cancel_event=None):
    """Exact retained 288dpi OCR, not printed-value/label/semantic authority.

    Line text is reconstructed by a single-space join only when that is byte
    for byte its original value; otherwise retain the distinct raw line too.
    No words are split, joined into new words, corrected, clipped or filtered.
    """
    crop = row['provenance'].get('page_appearance_crop')
    if type(crop) is not dict or crop.get('version') != 'pdf-checked-direct-chart-roi-v1':
        return None
    from . import pdf_native_chart_input as native
    from .pdf_page_label_ocr import _box
    _check(cancel_event)
    raw = base64.b64decode(row['raw_ocr_stdout_base64'], validate=True)
    _require(sha256(raw).hexdigest() == row['raw_ocr_stdout_sha256'],
        'Exact retained direct ROI OCR bytes changed')
    raw_record = native._load(raw)
    language = raw_record.get('language') if type(raw_record) is dict else None
    limits = native.PdfNativeChartInputLimits(**row['provenance']['generation_limits']['ocr'])
    dimensions = crop['dimensions']
    report = native._report(raw, crop['crop_png_sha256'], dimensions, language, limits)
    lines, ordinal = [], 0
    for line_ordinal, line in enumerate(report['lines']):
        _check(cancel_event)
        words = []
        for word in line['words']:
            _check(cancel_event)
            _require(type(word['text']) is str, 'Direct ROI OCR word must remain its original literal')
            box = _box(word['bbox'], dimensions)
            words.append([ordinal, word['text'], box])
            ordinal += 1
        raw_line = None if line['text'] == ' '.join(word['text'] for word in line['words']) else line['text']
        lines.append([line_ordinal, raw_line, words])
    _require(ordinal == report['word_count'], 'Every original direct ROI OCR word required')
    return dict(dimensions=dimensions, coordinate_frame='roi-local', rotation_ccw=0,
        source_id=crop['roi_source_id'], source_png_sha256=crop['crop_png_sha256'],
        source_pixel_sha256=crop['crop_pixel_sha256'], source_pdf_sha256=crop['source_pdf_sha256'],
        raw_ocr_stdout_sha256=row['raw_ocr_stdout_sha256'], language=language,
        line_columns=['line_ordinal', 'raw_line_text_or_null', 'words'],
        word_columns=['word_ordinal', 'raw_text', 'bbox_xywh'],
        null_line_text='exact_single_space_join_of_raw_words', lines=lines,
        word_count=ordinal, source_resized=False, words_corrected=False,
        ownership_verified=False, printed_measurements_verified=False,
        numerical_accuracy_verified=False, semantic_accuracy_verified=False)


def _native_ocr_context(row, inventory, source, cancel_event=None):
    """Original native OCR words, never fitted series or printed measurements."""
    mode = row['provenance'].get('writer_input_version')
    if (mode not in (WRITER_INPUT_VERSION, CONDITIONAL_WRITER_INPUT_VERSION)
            or mode == WRITER_INPUT_VERSION and row['reason'] != CURVE_FITTING_NOT_REQUESTED):
        return None
    provenance = row['provenance']
    if 'page_appearance_crop' in provenance or not row['raw_ocr_stdout_base64']:
        return None
    from . import pdf_native_chart_input as native_input
    from .pdf_page_label_ocr import _box
    _check(cancel_event)
    native, appearance = provenance['native_metadata'], provenance['appearance']
    size = [native['width'], native['height']]
    _require(appearance['eligibility'] is True and appearance['complete_page_text_retained'] is True
        and appearance['semantic_accuracy_verified'] is False
        and appearance['raw_pdf_sha256'] == source['geometry']['raw_sha256']
        and appearance['png_sha256'] == native['png_sha256'] and _sha(native['png_sha256'])
        and appearance['pixel_sha256'] == native['pixel_sha256'] and _sha(native['pixel_sha256'])
        and appearance['page'] == inventory['page']
        and appearance['object_ordinal'] == native['object_ordinal'] and appearance['dimensions'] == size
        and all(abs(a-b) <= .01 for a, b in zip(native['page_bbox'], inventory['bbox'], strict=True)),
        'Native OCR source/appearance differs from its original figure')
    raw = base64.b64decode(row['raw_ocr_stdout_base64'], validate=True)
    _require(sha256(raw).hexdigest() == row['raw_ocr_stdout_sha256'], 'Exact native OCR bytes changed')
    limits = native_input.PdfNativeChartInputLimits(**provenance['generation_limits']['ocr'])
    raw_record = native_input._load(raw)
    language = raw_record.get('language') if type(raw_record) is dict else None
    report = native_input._report(raw, native['png_sha256'], size, language, limits)
    lines, ordinal = [], 0
    for line_ordinal, line in enumerate(report['lines']):
        _check(cancel_event)
        words = []
        for word in line['words']:
            _check(cancel_event)
            _require(type(word['text']) is str, 'Native OCR words must remain original literals')
            words.append([ordinal, word['text'], _box(word['bbox'], size)])
            ordinal += 1
        raw_line = None if line['text'] == ' '.join(word['text'] for word in line['words']) else line['text']
        lines.append([line_ordinal, raw_line, words])
    _require(ordinal == report['word_count'], 'Every original native OCR word required')
    return dict(dimensions=size, coordinate_frame='native-image-local', rotation_ccw=0,
        source_id=native['source_id'], source_png_sha256=native['png_sha256'],
        source_pixel_sha256=native['pixel_sha256'], source_pdf_sha256=source['geometry']['raw_sha256'],
        raw_ocr_stdout_sha256=row['raw_ocr_stdout_sha256'], language=report['language'],
        line_columns=['line_ordinal', 'raw_line_text_or_null', 'words'],
        word_columns=['word_ordinal', 'raw_text', 'bbox_xywh'],
        null_line_text='exact_single_space_join_of_raw_words', lines=lines,
        word_count=ordinal, source_resized=False, words_corrected=False,
        ownership_verified=False, printed_measurements_verified=False,
        numerical_accuracy_verified=False, semantic_accuracy_verified=False)


def contexts_by_page(supplement, *, expected_sha256, cancel_event=None):
    record = _load(supplement, expected_sha256, cancel_event=cancel_event); result = {}
    writer_version = _writer_input_version(record['figures'])
    for row, inventory in zip(record['figures'], record['figure_inventory'], strict=True):
        _check(cancel_event)
        if row['status'] == 'read':
            context = _point_context(row, inventory, cancel_event)
        else:
            context = dict(figure_label=inventory['label'], page=inventory['page'], status='unsupported',
                reason=row['reason'], point_values_unknown=True, source_kind='retained_source_not_semantic_truth')
            observations = _page_ocr_context(row, inventory, record['source_identity'], cancel_event)
            if observations is not None:
                context['page_ocr_observations'] = observations
        crop_observations = _crop_ocr_context(row, cancel_event)
        if crop_observations is not None:
            context['crop_ocr_observations'] = crop_observations
        if writer_version is not None:
            context['writer_input_version'] = writer_version
            native_observations = _native_ocr_context(row, inventory, record['source_identity'], cancel_event)
            if native_observations is not None:
                context['native_ocr_observations'] = native_observations
        result.setdefault(inventory['page'], []).append(context)
    return result


def _layout_captions(record, layout_source, cancel_event=None):
    """Bind literal captions by original unit identity, never repeated labels."""
    from .pdf_layout_source import checked_layout
    _check(cancel_event)
    try:
        catalog = checked_layout(layout_source)
        _require(layout_source.source.identity() == record['source_identity']
            and layout_source.identity() == record['layout_identity'],
            'Caption layout must own the same retained source and layout')
        figures, seen = [], set()
        for page in catalog['pages']:
            for unit in page['units']:
                _check(cancel_event)
                unit_id = unit['unit_id']
                _require(type(unit_id) is str and unit_id not in seen,
                    'Every original layout unit must occur exactly once')
                seen.add(unit_id)
                if unit['kind'] == 'figure':
                    _require(type(unit['page']) is int and unit['page'] == page['page'],
                        'Original caption page changed')
                    figures.append(unit)
        inventory = record['figure_inventory']
        _require(len(figures) == len(inventory), 'Complete original caption figure inventory required')
        captions = []
        for unit, original in zip(figures, inventory, strict=True):
            _check(cancel_event)
            _require(_json({key: unit[key] for key in _INVENTORY_FIELDS}) == _json(original),
                'Original caption unit, order, page, label or geometry changed')
            caption = unit.get('caption_context', unit.get('text'))
            _require(type(caption) is str, 'Literal original caption text required')
            captions.append(caption)
        _check(cancel_event)
        return captions
    except (PdfProcessError, PdfChartSupplementError):
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise PdfChartSupplementError('Same unchanged checked caption layout required') from error


def _literal_block(text):
    # Escaped data, not executable HTML/Markdown; preserve spaces and line ends.
    return '<pre style="white-space: pre-wrap">'+escape(text, quote=False)+'</pre>'


def _source_observations(row, caption, cancel_event=None, *, inventory=None, source=None):
    _check(cancel_event)
    sections = ['캡션 원문 — 공간 연결 후보이며 대상·조건·단위의 의미 소유권은 미확인입니다.',
        _literal_block(caption)]
    observations = _crop_ocr_context(row, cancel_event)
    if observations is None and inventory is not None:
        observations = _native_ocr_context(row, inventory, source, cancel_event)
    if observations is not None:
        lines = []
        for _, raw_line, words in observations['lines']:
            _check(cancel_event)
            text = ' '.join(word[1] for word in words) if raw_line is None else raw_line
            lines.append(_literal_block(text))
        sections.append('<details>\n<summary>그림 내부 원문 표기 (OCR·연결 미확인)</summary>\n\n'
            '아래는 보존된 OCR 원문 관찰입니다. 숫자가 축 눈금인지 측정값인지, 어떤 대상·조건·단위에 '
            '속하는지 확정하지 않았습니다.\n\n'+'\n'.join(lines)+'\n\n</details>')
    _check(cancel_event)
    return '\n\n'.join(sections)


def render_chart_supplement(supplement, *, expected_sha256, maximum_markdown_bytes=4_194_304,
                            cancel_event=None, layout_source=None):
    """Optionally retain checked captions/OCR as observations beside prose.

    Omitting ``layout_source`` keeps the historical appendix byte-identical.
    Original literal lines are not corrected, selected by number or converted
    into measurements or semantic relationships.
    """
    _check(cancel_event)
    _require(type(maximum_markdown_bytes) is int and 1 <= maximum_markdown_bytes <= MAXIMUM_INPUT_BYTES,
        'Invalid complete appendix budget')
    record = _load(supplement, expected_sha256, cancel_event=cancel_event)
    captions = _layout_captions(record, layout_source, cancel_event) if layout_source is not None else None
    if _literal_context_marker(record) in (LITERAL_CONTEXT_KEY_VERSION,LITERAL_CONTEXT_CLAUSE_VERSION,LITERAL_CONTEXT_BARE_VERSION,LITERAL_CONTEXT_RELATION_VERSION,LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION,LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION):
        return _render_key_information(record,captions,maximum_markdown_bytes,cancel_event)
    if not record['figures']:
        return ''
    sections = ['## 원본 그림의 조건별 판독',
        '표의 계열·조건·제목은 원문 OCR 및 연결 후보입니다. 공통 행·팔레트·선형 축을 가정한 값은 '
        '조건부 근삿값이며 인쇄된 측정값이나 정확도 보장이 아닙니다. 판독 미확정 값은 채우지 않았습니다. '
        '가시 색 띠는 점 측정값이 아니므로 수치 범위로 표시하지 않습니다.']
    if any(row['reason'] == CURVE_FITTING_NOT_REQUESTED for row in record['figures']):
        sections[1] = ('원문 관찰은 대상·조건·계열·단위의 의미 소유권이나 숫자 정확성을 보장하지 않습니다. '
            '곡선 수치 판독을 요청하지 않은 그림은 원문 관찰만 보존하고 값이나 조건을 새로 추정하지 않았습니다.')
    for figure_ordinal, (stored, inventory) in enumerate(zip(record['figures'], record['figure_inventory'], strict=True)):
        _check(cancel_event)
        figure = (_point_context(stored, inventory, cancel_event) if stored['status'] == 'read' else
            dict(figure_label=inventory['label'], status='unsupported', reason=stored['reason']))
        page = inventory['page']
        label = _cell(figure['figure_label']) if figure['figure_label'] is not None else '표기 미확정'
        sections.append(f'### Figure {label} · p. {page}')
        if captions is not None:
            sections.append(_source_observations(stored, captions[figure_ordinal], cancel_event,
                inventory=inventory, source=record['source_identity']))
        if figure.get('status') == 'unsupported':
            sections.append('곡선 수치 판독을 요청하지 않았습니다. 원문 관찰만 보존했으며 수치·연결은 미확정입니다.'
                if figure['reason'] == CURVE_FITTING_NOT_REQUESTED else
                '판독 미확정: '+_cell(figure['reason'])+'. 원문 그림의 값이나 조건을 새로 추정하지 않았습니다.')
            continue
        original_panels = json.loads(stored['reading_json'])['panels']
        for ordinal, (row, original) in enumerate(zip(figure['panels'], original_panels, strict=True), 1):
            _check(cancel_event)
            panel = dict(zip(figure['panel_columns'], row, strict=True))
            subject = panel['subject_candidate']
            title = (_cell(subject) if subject is not None else '행 '+str(original.get('row_ordinal', ordinal))
                +' · 열 '+str(original.get('column_ordinal') or '판독 미확정'))
            sections.append('#### '+title
                +' · '+_cell(panel['metric_candidate'])+' (연결 후보)')
            conditions = figure['condition_sets'][panel['condition_set']]
            if not figure['raw_series'] or not conditions:
                sections.append('계열 또는 조건 판독 미확정. 관측 조건: '+', '.join(_cell(c) for c in conditions))
                continue
            table = ['| 계열 후보 | '+' | '.join(_cell(c) for c in conditions)+' |',
                '| --- | '+' | '.join('---' for _ in conditions)+' |']
            for label, points in zip(figure['raw_series'], panel['points'], strict=True):
                _check(cancel_event)
                cells = ['판독 미확정' if point is None else '조건부 약 '+point.lstrip('~!')
                    + (' [눈금 지지 범위 밖]' if point.startswith('~!') else '') for point in points]
                table.append('| '+_cell(label)+' | '+' | '.join(cells)+' |')
            sections.append('\n'.join(table))
    markdown = '\n\n'.join(sections)+'\n'
    _require(len(markdown.encode('utf-8')) <= maximum_markdown_bytes,
        'Complete chart appendix exceeds its byte budget, never a prefix')
    _check(cancel_event)
    return markdown


KEY_REFERENCE_VERSION = 'pdf-key-literal-reference-packet-v1'
KEY_NATIVE_REFERENCE_DOMAIN = 'native_projected_block_unicode_code_points'


def _key_literal_definition_projection(rows):
    """Exact observed strings; neither normalization nor verified definitions."""
    _require(type(rows) in (list, tuple) and len(rows) <= 32,
        'Bounded key literal definition rows required')
    projected = []
    for row in rows:
        _require(type(row) is dict and type(row.get('page')) is int and row['page'] >= 1
            and type(row.get('text')) is str and type(row.get('definition_terms')) is list
            and all(type(term) is str and term for term in row['definition_terms'])
            and row.get('range_domain') in (KEY_NATIVE_REFERENCE_DOMAIN,
                'retained_ocr_line_unicode_code_points')
            and row.get('definition_proven') is False and row.get('ownership_verified') is False
            and row.get('text_sha256') == _hash(row['text'])
            and type(row.get('start')) is int and type(row.get('end')) is int
            and row['start'] >= 0 and row['end']-row['start'] == len(row['text']),
            'Exact unverified key definition projection required')
        projected.append(dict(page=row['page'], terms=row['definition_terms'], text=row['text'],
            source_kind=row['range_domain'], definition_proven=False))
    return projected


def key_literal_definition_contexts(plan, owned):
    """Read sealed retained native projections; no parser/OCR/repaired strings.

    A unavailable native projection does not fall back to interleaved original
    text as a clean definition. Whole owned source is preserved independently.
    """
    from .pdf_layout_term_context import (
        _tokens, _ACRONYM, _definition_range, MAXIMUM_SCAN_CHARACTERS, MAXIMUM_SEED_CHARACTERS)
    from collections import Counter
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page', 'text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str for row in owned),
        'Complete owned source rows required')
    chunks = tuple(plan.chunks)
    originals = tuple((chunk.chunk_id, chunk.page, chunk.text) for chunk in chunks)
    _require(len({chunk.chunk_id for chunk in chunks}) == len(chunks), 'Distinct source owners required')
    if (not owned or sum(len(row['text']) for row in owned) > MAXIMUM_SEED_CHARACTERS
            or sum(len(chunk.text) for chunk in chunks) > MAXIMUM_SCAN_CHARACTERS):
        return []
    record = _load(plan.chart_supplement, expected_sha256=_hash(plan.chart_supplement.metadata_json))
    _require(_literal_context_marker(record) == LITERAL_CONTEXT_KEY_VERSION
        and plan.source.identity() == record['source_identity'], 'Key reference source binding changed')
    retained = record['literal_context_marker']['native_text_presentation']
    retained_sha = _hash(_json(retained))
    _require(retained_sha == plan._native_text_presentation_sha256, 'Retained native reference changed')
    source_pdf = record['source_identity']['geometry']['raw_sha256']
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    mappings = tuple(plan._native_owned_text_inputs)
    _require(all(type(row) is tuple and len(row) == 6 for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings), 'Distinct native reference owners required')
    retained_blocks = {(page['page'], block['block_id']):block
        for page in retained['pages'] for block in page['blocks']}
    if sum(len(row[4]) for row in mappings) > MAXIMUM_SCAN_CHARACTERS:
        return []
    available = set((chunk.page, chunk.text) for chunk in chunks)
    for row in mappings:
        _require(type(row) is tuple and len(row) == 6 and row[0] in by_id,
            'Known retained native reference owner required')
        chunk = by_id[row[0]]
        block = retained_blocks.get((row[2], row[1]))
        _require(row[2] == chunk.page and row[3] == _hash(chunk.text)
            and type(row[4]) is str and row[5] == _hash(row[4])
            and block is not None and block['projection_available'] is True
            and block['source_text_sha256'] == row[3] and block['projected_text'] == row[4]
            and block['projected_text_sha256'] == row[5], 'Exact native reference mapping required')
        available.add((row[2], row[4]))
    _require(all((row['page'], row['text']) in available for row in owned),
        'Owned key reference seed changed')
    frequency = Counter(match.group().rstrip('.') for row in owned for match in _ACRONYM.finditer(row['text']))
    if not frequency:
        return []
    candidates, seen = [], set()
    pages = {row['page'] for row in owned}
    for row in sorted(mappings, key=lambda value:(min(abs(value[2]-page) for page in pages), value[2], value[0])):
        chunk_id, block_id, page, source_text_sha, text, projection_sha = row
        count = 0
        for match in _ACRONYM.finditer(text):
            count += 1
            if count > 4096:
                return []  # Never select a passing prefix of conflicting observations.
            selected = _definition_range(text, match)
            if selected is None:
                continue
            priority, start, end, subjects = selected
            subjects = sorted(set(subjects))
            identity = (chunk_id, start, end)
            if not subjects or identity in seen:
                continue
            seen.add(identity)
            literal = text[start:end]
            candidates.append(dict(page=page, chunk_id=chunk_id, block_id=block_id,
                range_domain=KEY_NATIVE_REFERENCE_DOMAIN, start=start, end=end, text=literal,
                text_sha256=_hash(literal), source_text_sha256=source_text_sha,
                native_projected_text_sha256=projection_sha, retained_projection_sha256=retained_sha,
                source_pdf_sha256=source_pdf, definition_terms=subjects, selection_priority=priority,
                subject_frequency={term:frequency[term] for term in subjects},
                definition_proven=False, ownership_verified=False))
    for line in _literal_ocr_lines(record):
        words = line['words']
        if len(words) < 3 or words[1][1] != ':' or len(_tokens(words[0][1])) != 1:
            continue
        subjects = sorted(_tokens(words[0][1])); text = line['text']
        _require(line['source_pdf_sha256'] == source_pdf and type(text) is str,
            'Retained same-source OCR definition required')
        candidates.append(dict(line, range_domain='retained_ocr_line_unicode_code_points',
            start=0, end=len(text), text_sha256=_hash(text), literal_word_span_sha256=_hash(_json(words)),
            definition_terms=subjects, selection_priority=0,
            subject_frequency={term:frequency[term] for term in subjects},
            definition_proven=False, ownership_verified=False, source_labels_corrected=False))
    selected = _key_bounded_literal_groups(candidates)
    _require(tuple((chunk.chunk_id, chunk.page, chunk.text) for chunk in chunks) == originals,
        'Complete source changed during key reference selection')
    return selected


def key_literal_table_references(tables, owned, image_pages):
    """Whole same-source tables linked by observed caption/header identifiers.

    Never interpret a cell, select a correct answer, or silently fit by dropping
    primary text. Existing normal request/context budgets admit or reject the
    complete resulting packet. Source table appendix is unaffected.
    """
    from .pdf_layout_term_context import _tokens
    _require(type(tables) is list and type(owned) is list and type(image_pages) is tuple,
        'Exact table reference input required')
    pages = {row['page'] for row in owned} | set(image_pages)
    seed = set().union(*(_tokens(row['text']) for row in owned)) if owned else set()
    result = []
    for table in tables:
        if table['page'] in pages:
            continue  # The existing source_tables field already supplies it.
        identifiers = _tokens(table.get('caption_context', table['text']))
        # Column labels, not arbitrary body values, seed cross-batch selection.
        for row in table['rows']:
            if row['role'] not in ('positioned_header', 'header'):
                continue
            for cell in row['cells']:
                identifiers.update(_tokens(cell['text']))
        if identifiers & seed:
            result.append(table)
    return result


def key_literal_reference_packet(plan, owned, image_pages=()):
    from hashlib import sha256
    contexts = key_literal_definition_contexts(plan, owned)
    tables = json.loads(plan._literal_table_input_json)
    selected = key_literal_table_references(tables, owned, image_pages)
    compact = [{'page':table['page'], 'label':table['label'],
        'caption':table.get('caption_context',table['text']),
        'columns':len(table['column_bounds'])-1,
        'rows':[[[cell['start_column'],cell['span'],cell['text']] for cell in row['cells']]
            for row in table['rows']]} for table in selected]
    wire = dict(version=KEY_REFERENCE_VERSION, definitions=_key_literal_definition_projection(contexts),
        tables=compact)
    identity = dict(version=KEY_REFERENCE_VERSION, source_sha256=plan.source.geometry.raw_sha256,
        retained_projection_sha256=plan._native_text_presentation_sha256,
        reference_projection_sha256=_hash(_json(wire)), definitions=contexts,
        tables_sha256=sha256(plan._literal_table_input_json.encode()).hexdigest(),
        selected_table_ids=[table['unit_id'] for table in selected],
        selected_cell_count=sum(len(row['cells']) for table in selected for row in table['rows']),
        semantic_alignment_verified=False, definition_meanings_verified=False)
    return identity, wire


def _key_bounded_literal_groups(rows, maximum_bytes=1024):
    """Indexed whole groups; no quadratic scan over unrelated subjects."""
    _require(type(maximum_bytes) is int and 0 <= maximum_bytes <= 1024,
        'Bounded complete literal projection required')
    components, by_subject = [], {}
    def find(index):
        while components[index]['parent'] != index:
            components[index]['parent'] = components[components[index]['parent']]['parent']
            index = components[index]['parent']
        return index
    for row in rows:
        subjects = set(row['definition_terms'])
        roots = sorted({find(by_subject[term]) for term in subjects if term in by_subject},
            key=lambda index:components[index]['order'])
        eligible = 'subject_frequency' not in row or any(row['subject_frequency'].values())
        frequency = max((row.get('subject_frequency', {}).get(term, 0)
            for term in subjects), default=0)
        priority = row.get('selection_priority', 0)
        if not roots:
            root = len(components)
            components.append(dict(parent=root,order=root,node=row,count=1,
                eligible=eligible,frequency=frequency,priority=priority))
        else:
            # Original append-then-reverse-merge order, without copying rows
            # each time. Union by size and path compression bound lookup cost.
            node = [components[roots[0]]['node'],row,
                *(components[index]['node'] for index in reversed(roots[1:]))]
            root = max(roots,key=lambda index:components[index]['count'])
            merged = dict(parent=root,order=components[roots[0]]['order'],node=node,
                count=1+sum(components[index]['count'] for index in roots),
                eligible=eligible or any(components[index]['eligible'] for index in roots),
                frequency=max(frequency,*(components[index]['frequency'] for index in roots)),
                priority=min(priority,*(components[index]['priority'] for index in roots)))
            for index in roots:
                components[index]['parent'] = root
            components[root] = merged
        for term in subjects:
            by_subject[term] = root
    groups = sorted((item for index,item in enumerate(components)
        if item['parent']==index and item['eligible']),
        key=lambda item:(item['priority'],-item['frequency'],item['order']))
    selected = []
    for item in groups:
        if len(selected)+item['count'] > 32:
            continue  # Whole oversized group, not a truncated variant inventory.
        group, stack = [], [item['node']]
        while stack:
            node = stack.pop()
            if type(node) is list:
                stack.extend(reversed(node))
            else:
                group.append(node)
        projection = _key_literal_definition_projection((*selected,*group))
        if len(_json(projection).encode()) <= maximum_bytes:
            selected.extend(group)
    return selected



# Fresh-v6-only selection. Merely adding these functions does not select them.
LITERAL_CONTEXT_CLAUSE_VERSION = 'pdf-chart-key-information-native-v6'


def _key_clause_seed_frequency(owned):
    """Lexical interest only; uppercase runs in joined prose are not definitions.

    Uppercase/digit boundaries prevent a short term from matching a longer
    uppercase term. Lowercase neighbours may be PDF word-spacing loss. Never
    split, repair or expand the original text, and never use this lexer to find
    definition candidates themselves.
    """
    from collections import Counter
    lexical = re.compile(
        r'(?<![A-Z0-9])(?:[A-Z](?:\.[A-Z]){1,5}\.?|[A-Z][A-Z0-9-]{1,11})(?![A-Z0-9])')
    return Counter(match.group().rstrip('.') for row in owned
        for match in lexical.finditer(row['text']))


def _key_clause_parenthesis_range(text, match):
    """An exact bounded clause around '(TERM)', not a verified expansion.

    Newlines are NOT clause boundaries: a PDF may wrap the full name. No
    arbitrary word/character prefix is selected when a safe boundary is absent.
    Retain the complete containing sentence, including preceding correction
    labels and trailing negation/exception clauses. Commas, colons and
    semicolons are NOT boundaries. All offsets index the unchanged input.
    """
    from .pdf_layout_term_context import (
        _ACRONYM, _LITERAL_DOTTED, _definition_sentence_end)
    opening = match.start()-1
    while opening >= 0 and text[opening].isspace():
        opening -= 1
    closing = match.end()
    while closing < len(text) and text[closing].isspace():
        closing += 1
    if opening < 0 or text[opening] != '(' or closing >= len(text) or text[closing] != ')':
        return None
    floor = max(0, opening-240)
    protected = (tuple(_ACRONYM.finditer(text, max(0, floor-16), opening))
        + tuple(_LITERAL_DOTTED.finditer(text, max(0, floor-16), opening)))
    start = 0 if floor == 0 else None
    depth = 0
    for position in range(floor, opening):
        character = text[position]
        if character in '([{':
            depth += 1
        elif character in ')]}':
            depth = max(0, depth-1)
        if depth or any(word.start() <= position < word.end() for word in protected):
            continue
        following = text[position+1:position+2]
        if (character in '!?' or (character == '.'
                and (not following or following.isspace() or 'A' <= following <= 'Z'))):
            start = position+1
    if start is None:
        return None
    while start < opening and text[start].isspace():
        start += 1
    if start == opening or not re.search(r'[a-z]', text[start:opening]):
        return None
    end = _definition_sentence_end(text, closing+1, min(len(text), closing+241))
    if end is None:
        return None  # An incomplete sentence is not a safe compact reference.
    return 1, start, end, [match.group().rstrip('.')]


def _key_clause_definition_range(text, match):
    from .pdf_layout_term_context import _definition_range
    parenthetical = _key_clause_parenthesis_range(text, match)
    if parenthetical is not None:
        return parenthetical
    old = _definition_range(text, match)
    # Never restore the old arbitrary 120/240-character parenthesis window.
    return old if old is not None and old[0] == 0 else None


def _key_clause_native_contexts(mappings, owned, retained_sha, source_pdf,
        *, owned_chunk_ids=()):
    """Pure native recipe; exact external conflict groups are all-or-zero.

    Already-owned native blocks need no duplicate supplementary rows. Other
    same-subject source variants remain separate exact observations, even when
    their strings agree. The existing 1-KiB group gate is reused unchanged.
    """
    from .pdf_layout_term_context import (
        _ACRONYM, MAXIMUM_SCAN_CHARACTERS, MAXIMUM_SEED_CHARACTERS)
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Exact owned native rows required')
    _require(type(mappings) is tuple and all(type(row) is tuple and len(row) == 6
        and type(row[0]) is str and row[0] and type(row[1]) is str and row[1]
        and type(row[2]) is int and row[2] >= 1 and type(row[3]) is str
        and type(row[4]) is str and row[5] == _hash(row[4]) for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Exact distinct native candidate mappings required')
    _require(type(owned_chunk_ids) in (tuple,set,frozenset)
        and all(type(value) is str and value for value in owned_chunk_ids)
        and set(owned_chunk_ids) <= {row[0] for row in mappings},
        'Known owned native chunk identities required')
    if (not owned or sum(len(row['text']) for row in owned) > MAXIMUM_SEED_CHARACTERS
            or sum(len(row[4]) for row in mappings) > MAXIMUM_SCAN_CHARACTERS):
        return []
    frequency = _key_clause_seed_frequency(owned)
    if not frequency:
        return []
    owned_pairs = {(row['page'],row['text']) for row in owned}
    already_owned = set(owned_chunk_ids) | {row[0] for row in mappings
        if (row[2],row[4]) in owned_pairs}
    pages = {row['page'] for row in owned}
    candidates, seen = [], {}
    for row in sorted(mappings, key=lambda value:(min(abs(value[2]-page) for page in pages),
            value[2],value[0])):
        chunk_id, block_id, page, source_text_sha, text, projection_sha = row
        matches = tuple(_ACRONYM.finditer(text))
        if len(matches) > 4096:
            return []  # Do not admit a passing prefix of external variants.
        if chunk_id in already_owned:
            continue
        for match in matches:
            selected = _key_clause_definition_range(text, match)
            if selected is None:
                continue
            priority, start, end, subjects = selected
            subjects = sorted(set(subjects))
            identity = (chunk_id,start,end)
            if not subjects:
                continue
            if identity in seen:
                prior = seen[identity]
                prior['definition_terms'] = sorted(set(prior['definition_terms']) | set(subjects))
                prior['subject_frequency'].update({term:frequency[term] for term in subjects})
                prior['selection_priority'] = min(prior['selection_priority'],priority)
                continue
            literal = text[start:end]
            candidate = dict(page=page,chunk_id=chunk_id,block_id=block_id,
                range_domain=KEY_NATIVE_REFERENCE_DOMAIN,start=start,end=end,text=literal,
                text_sha256=_hash(literal),source_text_sha256=source_text_sha,
                native_projected_text_sha256=projection_sha,retained_projection_sha256=retained_sha,
                source_pdf_sha256=source_pdf,definition_terms=subjects,selection_priority=priority,
                subject_frequency={term:frequency[term] for term in subjects},
                definition_proven=False,ownership_verified=False)
            candidates.append(candidate)
            seen[identity] = candidate
    return _key_bounded_literal_groups(candidates)


def key_literal_definition_contexts_v6(plan, owned):
    """Unwired fresh-v6 entry point, requiring a separately admitted v6 plan.

    Source/retained projection binding is checked exactly as for v5. No PDF,
    native worker, OCR, model, glossary or repaired strings are consulted.
    """
    from .pdf_layout_term_context import MAXIMUM_SCAN_CHARACTERS
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Complete owned source rows required')
    chunks = tuple(plan.chunks)
    originals = tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks)
    _require(len({chunk.chunk_id for chunk in chunks}) == len(chunks),
        'Distinct source owners required')
    if sum(len(chunk.text) for chunk in chunks) > MAXIMUM_SCAN_CHARACTERS:
        return []
    record = _load(plan.chart_supplement,expected_sha256=_hash(plan.chart_supplement.metadata_json))
    marker = record.get('literal_context_marker')
    _require(type(marker) is dict and set(marker) == {'version','native_text_presentation'}
        and marker['version'] == LITERAL_CONTEXT_CLAUSE_VERSION
        and plan.source.identity() == record['source_identity'],
        'Separately admitted fresh-v6 source binding required')
    retained = marker['native_text_presentation']
    retained_sha = _hash(_json(retained))
    _require(retained_sha == plan._native_text_presentation_sha256,
        'Retained native reference changed')
    by_id = {chunk.chunk_id:chunk for chunk in chunks}
    mappings = tuple(plan._native_owned_text_inputs)
    _require(all(type(row) is tuple and len(row) == 6 for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Distinct native reference owners required')
    retained_blocks = {(page['page'],block['block_id']):block
        for page in retained['pages'] for block in page['blocks']}
    available = {(chunk.page,chunk.text) for chunk in chunks}
    for row in mappings:
        _require(row[0] in by_id,'Known native reference owner required')
        chunk = by_id[row[0]]
        block = retained_blocks.get((row[2],row[1]))
        _require(row[2] == chunk.page and row[3] == _hash(chunk.text)
            and type(row[4]) is str and row[5] == _hash(row[4])
            and block is not None and block['projection_available'] is True
            and block['source_text_sha256'] == row[3] and block['projected_text'] == row[4]
            and block['projected_text_sha256'] == row[5],
            'Exact native reference mapping required')
        available.add((row[2],row[4]))
    _require(all((row['page'],row['text']) in available for row in owned),
        'Owned key reference seed changed')
    native_ids = {row[0] for row in mappings}
    original_owned = tuple(chunk.chunk_id for chunk in chunks
        if chunk.chunk_id in native_ids and (chunk.page,chunk.text) in
            {(row['page'],row['text']) for row in owned})
    selected = _key_clause_native_contexts(mappings,owned,retained_sha,
        record['source_identity']['geometry']['raw_sha256'],owned_chunk_ids=original_owned)
    _require(tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks) == originals,
        'Complete source changed during clause reference selection')
    return selected


def key_literal_reference_packet_v6(plan, owned, image_pages=()):
    from hashlib import sha256
    contexts = key_literal_definition_contexts_v6(plan, owned)
    tables = json.loads(plan._literal_table_input_json)
    selected = key_literal_table_references(tables, owned, image_pages)
    compact = [{'page':table['page'], 'label':table['label'],
        'caption':table.get('caption_context',table['text']),
        'columns':len(table['column_bounds'])-1,
        'rows':[[[cell['start_column'],cell['span'],cell['text']] for cell in row['cells']]
            for row in table['rows']]} for table in selected]
    wire = dict(version=KEY_REFERENCE_VERSION, definitions=_key_literal_definition_projection(contexts),
        tables=compact)
    identity = dict(version=KEY_REFERENCE_VERSION, source_sha256=plan.source.geometry.raw_sha256,
        retained_projection_sha256=plan._native_text_presentation_sha256,
        reference_projection_sha256=_hash(_json(wire)), definitions=contexts,
        tables_sha256=sha256(plan._literal_table_input_json.encode()).hexdigest(),
        selected_table_ids=[table['unit_id'] for table in selected],
        selected_cell_count=sum(len(row['cells']) for table in selected for row in table['rows']),
        semantic_alignment_verified=False, definition_meanings_verified=False)
    return identity, wire



def _key_bounded_literal_groups_v7(rows, maximum_bytes=2048):
    """Indexed whole groups; no quadratic scan over unrelated subjects."""
    _require(type(maximum_bytes) is int and 0 <= maximum_bytes <= 2048,
        'Bounded complete literal projection required')
    components, by_subject = [], {}
    def find(index):
        while components[index]['parent'] != index:
            components[index]['parent'] = components[components[index]['parent']]['parent']
            index = components[index]['parent']
        return index
    for row in rows:
        subjects = set(row['definition_terms'])
        roots = sorted({find(by_subject[term]) for term in subjects if term in by_subject},
            key=lambda index:components[index]['order'])
        eligible = 'subject_frequency' not in row or any(row['subject_frequency'].values())
        frequency = max((row.get('subject_frequency', {}).get(term, 0)
            for term in subjects), default=0)
        priority = row.get('selection_priority', 0)
        if not roots:
            root = len(components)
            components.append(dict(parent=root,order=root,node=row,count=1,
                eligible=eligible,frequency=frequency,priority=priority))
        else:
            # Original append-then-reverse-merge order, without copying rows
            # each time. Union by size and path compression bound lookup cost.
            node = [components[roots[0]]['node'],row,
                *(components[index]['node'] for index in reversed(roots[1:]))]
            root = max(roots,key=lambda index:components[index]['count'])
            merged = dict(parent=root,order=components[roots[0]]['order'],node=node,
                count=1+sum(components[index]['count'] for index in roots),
                eligible=eligible or any(components[index]['eligible'] for index in roots),
                frequency=max(frequency,*(components[index]['frequency'] for index in roots)),
                priority=min(priority,*(components[index]['priority'] for index in roots)))
            for index in roots:
                components[index]['parent'] = root
            components[root] = merged
        for term in subjects:
            by_subject[term] = root
    groups = sorted((item for index,item in enumerate(components)
        if item['parent']==index and item['eligible']),
        key=lambda item:(item['priority'],-item['frequency'],item['order']))
    selected = []
    for item in groups:
        if len(selected)+item['count'] > 32:
            continue  # Whole oversized group, not a truncated variant inventory.
        group, stack = [], [item['node']]
        while stack:
            node = stack.pop()
            if type(node) is list:
                stack.extend(reversed(node))
            else:
                group.append(node)
        projection = _key_literal_definition_projection((*selected,*group))
        if len(_json(projection).encode()) <= maximum_bytes:
            selected.extend(group)
    return selected


def _key_clause_native_contexts_v7(mappings, owned, retained_sha, source_pdf,
        *, owned_chunk_ids=()):
    """Pure native recipe; exact external conflict groups are all-or-zero.

    Already-owned native blocks need no duplicate supplementary rows. Other
    same-subject source variants remain separate exact observations, even when
    their strings agree. The fresh-v7 2-KiB complete-group gate is used without sentence truncation.
    """
    from .pdf_layout_term_context import (
        _ACRONYM, MAXIMUM_SCAN_CHARACTERS, MAXIMUM_SEED_CHARACTERS)
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Exact owned native rows required')
    _require(type(mappings) is tuple and all(type(row) is tuple and len(row) == 6
        and type(row[0]) is str and row[0] and type(row[1]) is str and row[1]
        and type(row[2]) is int and row[2] >= 1 and type(row[3]) is str
        and type(row[4]) is str and row[5] == _hash(row[4]) for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Exact distinct native candidate mappings required')
    _require(type(owned_chunk_ids) in (tuple,set,frozenset)
        and all(type(value) is str and value for value in owned_chunk_ids)
        and set(owned_chunk_ids) <= {row[0] for row in mappings},
        'Known owned native chunk identities required')
    if (not owned or sum(len(row['text']) for row in owned) > MAXIMUM_SEED_CHARACTERS
            or sum(len(row[4]) for row in mappings) > MAXIMUM_SCAN_CHARACTERS):
        return []
    frequency = _key_clause_seed_frequency(owned)
    if not frequency:
        return []
    owned_pairs = {(row['page'],row['text']) for row in owned}
    already_owned = set(owned_chunk_ids) | {row[0] for row in mappings
        if (row[2],row[4]) in owned_pairs}
    pages = {row['page'] for row in owned}
    candidates, seen = [], {}
    for row in sorted(mappings, key=lambda value:(min(abs(value[2]-page) for page in pages),
            value[2],value[0])):
        chunk_id, block_id, page, source_text_sha, text, projection_sha = row
        matches = tuple(_ACRONYM.finditer(text))
        if len(matches) > 4096:
            return []  # Do not admit a passing prefix of external variants.
        if chunk_id in already_owned:
            continue
        for match in matches:
            selected = _key_clause_definition_range(text, match)
            if selected is None:
                continue
            priority, start, end, subjects = selected
            subjects = sorted(set(subjects))
            identity = (chunk_id,start,end)
            if not subjects:
                continue
            if identity in seen:
                prior = seen[identity]
                prior['definition_terms'] = sorted(set(prior['definition_terms']) | set(subjects))
                prior['subject_frequency'].update({term:frequency[term] for term in subjects})
                prior['selection_priority'] = min(prior['selection_priority'],priority)
                continue
            literal = text[start:end]
            candidate = dict(page=page,chunk_id=chunk_id,block_id=block_id,
                range_domain=KEY_NATIVE_REFERENCE_DOMAIN,start=start,end=end,text=literal,
                text_sha256=_hash(literal),source_text_sha256=source_text_sha,
                native_projected_text_sha256=projection_sha,retained_projection_sha256=retained_sha,
                source_pdf_sha256=source_pdf,definition_terms=subjects,selection_priority=priority,
                subject_frequency={term:frequency[term] for term in subjects},
                definition_proven=False,ownership_verified=False)
            candidates.append(candidate)
            seen[identity] = candidate
    return _key_bounded_literal_groups_v7(candidates)


def key_literal_definition_contexts_v7(plan, owned):
    """Unwired fresh-v7 entry point, requiring a separately admitted v6 plan.

    Source/retained projection binding is checked exactly as for v5. No PDF,
    native worker, OCR, model, glossary or repaired strings are consulted.
    """
    from .pdf_layout_term_context import MAXIMUM_SCAN_CHARACTERS
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Complete owned source rows required')
    chunks = tuple(plan.chunks)
    originals = tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks)
    _require(len({chunk.chunk_id for chunk in chunks}) == len(chunks),
        'Distinct source owners required')
    if sum(len(chunk.text) for chunk in chunks) > MAXIMUM_SCAN_CHARACTERS:
        return []
    record = _load(plan.chart_supplement,expected_sha256=_hash(plan.chart_supplement.metadata_json))
    marker = record.get('literal_context_marker')
    _require(type(marker) is dict and set(marker) == {'version','native_text_presentation'}
        and marker['version'] == LITERAL_CONTEXT_BARE_VERSION
        and plan.source.identity() == record['source_identity'],
        'Separately admitted fresh-v7 source binding required')
    retained = marker['native_text_presentation']
    retained_sha = _hash(_json(retained))
    _require(retained_sha == plan._native_text_presentation_sha256,
        'Retained native reference changed')
    by_id = {chunk.chunk_id:chunk for chunk in chunks}
    mappings = tuple(plan._native_owned_text_inputs)
    _require(all(type(row) is tuple and len(row) == 6 for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Distinct native reference owners required')
    retained_blocks = {(page['page'],block['block_id']):block
        for page in retained['pages'] for block in page['blocks']}
    available = {(chunk.page,chunk.text) for chunk in chunks}
    for row in mappings:
        _require(row[0] in by_id,'Known native reference owner required')
        chunk = by_id[row[0]]
        block = retained_blocks.get((row[2],row[1]))
        _require(row[2] == chunk.page and row[3] == _hash(chunk.text)
            and type(row[4]) is str and row[5] == _hash(row[4])
            and block is not None and block['projection_available'] is True
            and block['source_text_sha256'] == row[3] and block['projected_text'] == row[4]
            and block['projected_text_sha256'] == row[5],
            'Exact native reference mapping required')
        available.add((row[2],row[4]))
    _require(all((row['page'],row['text']) in available for row in owned),
        'Owned key reference seed changed')
    native_ids = {row[0] for row in mappings}
    original_owned = tuple(chunk.chunk_id for chunk in chunks
        if chunk.chunk_id in native_ids and (chunk.page,chunk.text) in
            {(row['page'],row['text']) for row in owned})
    selected = _key_clause_native_contexts_v7(mappings,owned,retained_sha,
        record['source_identity']['geometry']['raw_sha256'],owned_chunk_ids=original_owned)
    _require(tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks) == originals,
        'Complete source changed during clause reference selection')
    return selected


def key_literal_reference_packet_v7(plan, owned, image_pages=()):
    from hashlib import sha256
    contexts = key_literal_definition_contexts_v7(plan, owned)
    tables = json.loads(plan._literal_table_input_json)
    selected = key_literal_table_references(tables, owned, image_pages)
    compact = [{'page':table['page'], 'label':table['label'],
        'caption':table.get('caption_context',table['text']),
        'columns':len(table['column_bounds'])-1,
        'rows':[[[cell['start_column'],cell['span'],cell['text']] for cell in row['cells']]
            for row in table['rows']]} for table in selected]
    wire = dict(version=KEY_REFERENCE_VERSION, definitions=_key_literal_definition_projection(contexts),
        tables=compact)
    identity = dict(version=KEY_REFERENCE_VERSION, source_sha256=plan.source.geometry.raw_sha256,
        retained_projection_sha256=plan._native_text_presentation_sha256,
        reference_projection_sha256=_hash(_json(wire)), definitions=contexts,
        tables_sha256=sha256(plan._literal_table_input_json.encode()).hexdigest(),
        selected_table_ids=[table['unit_id'] for table in selected],
        selected_cell_count=sum(len(row['cells']) for table in selected for row in table['rows']),
        semantic_alignment_verified=False, definition_meanings_verified=False)
    return identity, wire



def key_literal_definition_contexts_v8(plan, owned):
    """Unwired fresh-v8 entry point, requiring a separately admitted v6 plan.

    Source/retained projection binding is checked exactly as for v5. No PDF,
    native worker, OCR, model, glossary or repaired strings are consulted.
    """
    from .pdf_layout_term_context import MAXIMUM_SCAN_CHARACTERS
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Complete owned source rows required')
    chunks = tuple(plan.chunks)
    originals = tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks)
    _require(len({chunk.chunk_id for chunk in chunks}) == len(chunks),
        'Distinct source owners required')
    if sum(len(chunk.text) for chunk in chunks) > MAXIMUM_SCAN_CHARACTERS:
        return []
    record = _load(plan.chart_supplement,expected_sha256=_hash(plan.chart_supplement.metadata_json))
    marker = record.get('literal_context_marker')
    _require(type(marker) is dict and set(marker) == {'version','native_text_presentation'}
        and marker['version'] == LITERAL_CONTEXT_RELATION_VERSION
        and plan.source.identity() == record['source_identity'],
        'Separately admitted fresh-v8 source binding required')
    retained = marker['native_text_presentation']
    retained_sha = _hash(_json(retained))
    _require(retained_sha == plan._native_text_presentation_sha256,
        'Retained native reference changed')
    by_id = {chunk.chunk_id:chunk for chunk in chunks}
    mappings = tuple(plan._native_owned_text_inputs)
    _require(all(type(row) is tuple and len(row) == 6 for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Distinct native reference owners required')
    retained_blocks = {(page['page'],block['block_id']):block
        for page in retained['pages'] for block in page['blocks']}
    available = {(chunk.page,chunk.text) for chunk in chunks}
    for row in mappings:
        _require(row[0] in by_id,'Known native reference owner required')
        chunk = by_id[row[0]]
        block = retained_blocks.get((row[2],row[1]))
        _require(row[2] == chunk.page and row[3] == _hash(chunk.text)
            and type(row[4]) is str and row[5] == _hash(row[4])
            and block is not None and block['projection_available'] is True
            and block['source_text_sha256'] == row[3] and block['projected_text'] == row[4]
            and block['projected_text_sha256'] == row[5],
            'Exact native reference mapping required')
        available.add((row[2],row[4]))
    _require(all((row['page'],row['text']) in available for row in owned),
        'Owned key reference seed changed')
    native_ids = {row[0] for row in mappings}
    original_owned = tuple(chunk.chunk_id for chunk in chunks
        if chunk.chunk_id in native_ids and (chunk.page,chunk.text) in
            {(row['page'],row['text']) for row in owned})
    selected = _key_clause_native_contexts_v7(mappings,owned,retained_sha,
        record['source_identity']['geometry']['raw_sha256'],owned_chunk_ids=original_owned)
    _require(tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks) == originals,
        'Complete source changed during clause reference selection')
    return selected


def key_literal_reference_packet_v8(plan, owned, image_pages=()):
    from hashlib import sha256
    contexts = key_literal_definition_contexts_v8(plan, owned)
    tables = json.loads(plan._literal_table_input_json)
    selected = key_literal_table_references(tables, owned, image_pages)
    compact = [{'page':table['page'], 'label':table['label'],
        'caption':table.get('caption_context',table['text']),
        'columns':len(table['column_bounds'])-1,
        'rows':[[[cell['start_column'],cell['span'],cell['text']] for cell in row['cells']]
            for row in table['rows']]} for table in selected]
    wire = dict(version=KEY_REFERENCE_VERSION, definitions=_key_literal_definition_projection(contexts),
        tables=compact)
    identity = dict(version=KEY_REFERENCE_VERSION, source_sha256=plan.source.geometry.raw_sha256,
        retained_projection_sha256=plan._native_text_presentation_sha256,
        reference_projection_sha256=_hash(_json(wire)), definitions=contexts,
        tables_sha256=sha256(plan._literal_table_input_json.encode()).hexdigest(),
        selected_table_ids=[table['unit_id'] for table in selected],
        selected_cell_count=sum(len(row['cells']) for table in selected for row in table['rows']),
        semantic_alignment_verified=False, definition_meanings_verified=False)
    return identity, wire



_explicit_table_PREFIX_v11 = re.compile(r"(?<!\w)(?:Tables?\s+|표\s*)", re.IGNORECASE)

_explicit_table_TOKEN_v11 = re.compile(r"(?:[IVXLCDM]+|[0-9]+)")

_explicit_table_ROMAN_v11 = re.compile(r"M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})\Z")

_explicit_table_SEPARATOR_v11 = re.compile(r"(?:,\s*(?:and\s+)?|and\b\s*|&\s*|및\s*|와\s*|과\s*)", re.IGNORECASE)

_explicit_table_RANGE_v11 = re.compile(r"(?:[-–—]|to\b)", re.IGNORECASE)

_explicit_table_KOREAN_PARTICLE_v11 = re.compile(
    r"(?:에서는|에서도|으로는|으로도|에서|에게|으로|까지|부터|은|는|이|가|을|를|의|에|로|도|만)"
    r"(?=\s|$|[.,;:!?()\[\]{}])")

def _explicit_table_label_valid_v11(label):
    if label.isascii() and label.isdigit():
        return label[0] != "0"
    return bool(label and _explicit_table_ROMAN_v11.fullmatch(label))

def _explicit_table_label_at_v11(text, offset):
    match = _explicit_table_TOKEN_v11.match(text, offset)
    if match is None or not _explicit_table_label_valid_v11(match.group()):
        return None
    end = match.end()
    if end < len(text) and (text[end].isalnum() or text[end] == "_"):
        # Declared conjunctions preserve their existing group parsing. Common
        # complete Korean particles are a boundary only, not inferred labels.
        if (not text.startswith(("및", "와", "과"), end)
                and _explicit_table_KOREAN_PARTICLE_v11.match(text, end) is None):
            return None
    if end + 1 < len(text) and text[end] == "." and text[end + 1].isdigit():
        return None
    return match.group(), end

def _explicit_table_reference_group_v11(text, offset):
    """Reject an unsupported compound instead of selecting its passing prefix."""
    item = _explicit_table_label_at_v11(text, offset)
    if item is None:
        return ()
    labels = []
    while item is not None:
        label, end = item
        labels.append(label)
        position = end
        while position < len(text) and text[position].isspace():
            position += 1
        if _explicit_table_RANGE_v11.match(text, position):
            return ()
        separator = _explicit_table_SEPARATOR_v11.match(text, position)
        if separator is None:
            return tuple(labels)
        next_position = separator.end()
        while next_position < len(text) and text[next_position].isspace():
            next_position += 1
        # Separate repeated explicit prefixes are independently parsed.
        if _explicit_table_PREFIX_v11.match(text, next_position):
            return tuple(labels)
        item = _explicit_table_label_at_v11(text, next_position)
        if item is None:
            # A comma can begin prose, not necessarily another label. A short
            # unsupported alphanumeric item is ambiguous, so reject the group.
            following = re.match(r"[A-Za-z0-9]+", text[next_position:])
            comma_only = separator.group().strip() == ","
            if comma_only and (following is None or len(following.group()) > 3):
                return tuple(labels)
            return ()
    return ()

def _explicit_table_labels_v11(text):
    """Lexical labels only, with no topic words, acronym scoring or case facts."""
    if type(text) is not str:
        raise ValueError("Exact native source text required")
    labels = set()
    for prefix in _explicit_table_PREFIX_v11.finditer(text):
        labels.update(_explicit_table_reference_group_v11(text, prefix.end()))
    return frozenset(labels)

def key_explicit_table_references_v11(tables, owned, image_pages=()):
    """Return original complete dictionaries in catalog order, without mutation.

    Exact repeated catalog entries are returned at most once. Two distinct
    tables bearing the same label are ambiguous even if one is on an owned
    page; the off-owner table is not guessed. Conflicting repeated unit IDs are
    malformed input, not an opportunity to choose one of their contents.
    """
    if (type(tables) is not list or type(owned) is not list
            or type(image_pages) is not tuple
            or any(type(page) is not int or page < 1 for page in image_pages)):
        raise ValueError("Exact tables, owned sections and image pages required")
    if any(type(row) is not dict or set(row) != {"page", "text"}
            or type(row["page"]) is not int or row["page"] < 1
            or type(row["text"]) is not str for row in owned):
        raise ValueError("Complete exact owned source rows required")
    referenced = set().union(*(_explicit_table_labels_v11(row["text"]) for row in owned)) if owned else set()
    pages = {row["page"] for row in owned} | set(image_pages)
    unique, by_id, by_label = [], {}, {}
    for table in tables:
        if (type(table) is not dict or type(table.get("page")) is not int
                or table["page"] < 1 or type(table.get("label")) is not str
                or type(table.get("unit_id")) is not str or not table["unit_id"]):
            raise ValueError("Exact table identity required")
        key = table["unit_id"]
        if key in by_id:
            if table != by_id[key]:
                raise ValueError("Repeated table identity changed")
            continue
        by_id[key] = table
        unique.append(table)
        by_label.setdefault(table["label"], []).append(table)
    return [table for table in unique if table["page"] not in pages
        and table["label"] in referenced and _explicit_table_label_valid_v11(table["label"])
        and len(by_label[table["label"]]) == 1]

def key_literal_definition_contexts_v11(plan, owned):
    """Unwired fresh-v11 entry point, requiring a separately admitted v11 plan.

    Source/retained projection binding is checked exactly as for v5. No PDF,
    native worker, OCR, model, glossary or repaired strings are consulted.
    """
    from .pdf_layout_term_context import MAXIMUM_SCAN_CHARACTERS
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Complete owned source rows required')
    chunks = tuple(plan.chunks)
    originals = tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks)
    _require(len({chunk.chunk_id for chunk in chunks}) == len(chunks),
        'Distinct source owners required')
    if sum(len(chunk.text) for chunk in chunks) > MAXIMUM_SCAN_CHARACTERS:
        return []
    record = _load(plan.chart_supplement,expected_sha256=_hash(plan.chart_supplement.metadata_json))
    marker = record.get('literal_context_marker')
    _require(type(marker) is dict and set(marker) == {'version','native_text_presentation'}
        and marker['version'] == LITERAL_CONTEXT_EXPLICIT_TABLE_VERSION
        and plan.source.identity() == record['source_identity'],
        'Separately admitted fresh-v11 source binding required')
    retained = marker['native_text_presentation']
    retained_sha = _hash(_json(retained))
    _require(retained_sha == plan._native_text_presentation_sha256,
        'Retained native reference changed')
    by_id = {chunk.chunk_id:chunk for chunk in chunks}
    mappings = tuple(plan._native_owned_text_inputs)
    _require(all(type(row) is tuple and len(row) == 6 for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Distinct native reference owners required')
    retained_blocks = {(page['page'],block['block_id']):block
        for page in retained['pages'] for block in page['blocks']}
    available = {(chunk.page,chunk.text) for chunk in chunks}
    for row in mappings:
        _require(row[0] in by_id,'Known native reference owner required')
        chunk = by_id[row[0]]
        block = retained_blocks.get((row[2],row[1]))
        _require(row[2] == chunk.page and row[3] == _hash(chunk.text)
            and type(row[4]) is str and row[5] == _hash(row[4])
            and block is not None and block['projection_available'] is True
            and block['source_text_sha256'] == row[3] and block['projected_text'] == row[4]
            and block['projected_text_sha256'] == row[5],
            'Exact native reference mapping required')
        available.add((row[2],row[4]))
    _require(all((row['page'],row['text']) in available for row in owned),
        'Owned key reference seed changed')
    native_ids = {row[0] for row in mappings}
    original_owned = tuple(chunk.chunk_id for chunk in chunks
        if chunk.chunk_id in native_ids and (chunk.page,chunk.text) in
            {(row['page'],row['text']) for row in owned})
    selected = _key_clause_native_contexts_v7(mappings,owned,retained_sha,
        record['source_identity']['geometry']['raw_sha256'],owned_chunk_ids=original_owned)
    _require(tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks) == originals,
        'Complete source changed during clause reference selection')
    return selected


def key_literal_reference_packet_v11(plan, owned, image_pages=()):
    from hashlib import sha256
    contexts = key_literal_definition_contexts_v11(plan, owned)
    tables = json.loads(plan._literal_table_input_json)
    selected = key_explicit_table_references_v11(tables, owned, image_pages)
    compact = [{'page':table['page'], 'label':table['label'],
        'caption':table.get('caption_context',table['text']),
        'columns':len(table['column_bounds'])-1,
        'rows':[[[cell['start_column'],cell['span'],cell['text']] for cell in row['cells']]
            for row in table['rows']]} for table in selected]
    wire = dict(version=KEY_REFERENCE_VERSION, definitions=_key_literal_definition_projection(contexts),
        tables=compact)
    identity = dict(version=KEY_REFERENCE_VERSION, source_sha256=plan.source.geometry.raw_sha256,
        retained_projection_sha256=plan._native_text_presentation_sha256,
        reference_projection_sha256=_hash(_json(wire)), definitions=contexts,
        tables_sha256=sha256(plan._literal_table_input_json.encode()).hexdigest(),
        selected_table_ids=[table['unit_id'] for table in selected],
        selected_cell_count=sum(len(row['cells']) for table in selected for row in table['rows']),
        semantic_alignment_verified=False, definition_meanings_verified=False)
    return identity, wire



def key_literal_definition_contexts_v12(plan, owned):
    """Unwired fresh-v12 entry point, requiring a separately admitted v12 plan.

    Source/retained projection binding is checked exactly as for v5. No PDF,
    native worker, OCR, model, glossary or repaired strings are consulted.
    """
    from .pdf_layout_term_context import MAXIMUM_SCAN_CHARACTERS
    _require(type(owned) is list and all(type(row) is dict and set(row) == {'page','text'}
        and type(row['page']) is int and row['page'] >= 1 and type(row['text']) is str
        for row in owned), 'Complete owned source rows required')
    chunks = tuple(plan.chunks)
    originals = tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks)
    _require(len({chunk.chunk_id for chunk in chunks}) == len(chunks),
        'Distinct source owners required')
    if sum(len(chunk.text) for chunk in chunks) > MAXIMUM_SCAN_CHARACTERS:
        return []
    record = _load(plan.chart_supplement,expected_sha256=_hash(plan.chart_supplement.metadata_json))
    marker = record.get('literal_context_marker')
    _require(type(marker) is dict and set(marker) == {'version','native_text_presentation'}
        and marker['version'] in (LITERAL_CONTEXT_BOUNDED_OWNED_VERSION,LITERAL_CONTEXT_ADMITTED_OWNED_VERSION)
        and plan.source.identity() == record['source_identity'],
        'Separately admitted fresh-v12 source binding required')
    retained = marker['native_text_presentation']
    retained_sha = _hash(_json(retained))
    _require(retained_sha == plan._native_text_presentation_sha256,
        'Retained native reference changed')
    by_id = {chunk.chunk_id:chunk for chunk in chunks}
    mappings = tuple(plan._native_owned_text_inputs)
    _require(all(type(row) is tuple and len(row) == 6 for row in mappings)
        and len({row[0] for row in mappings}) == len(mappings),
        'Distinct native reference owners required')
    retained_blocks = {(page['page'],block['block_id']):block
        for page in retained['pages'] for block in page['blocks']}
    available = {(chunk.page,chunk.text) for chunk in chunks}
    for row in mappings:
        _require(row[0] in by_id,'Known native reference owner required')
        chunk = by_id[row[0]]
        block = retained_blocks.get((row[2],row[1]))
        _require(row[2] == chunk.page and row[3] == _hash(chunk.text)
            and type(row[4]) is str and row[5] == _hash(row[4])
            and block is not None and block['projection_available'] is True
            and block['source_text_sha256'] == row[3] and block['projected_text'] == row[4]
            and block['projected_text_sha256'] == row[5],
            'Exact native reference mapping required')
        available.add((row[2],row[4]))
    _require(all((row['page'],row['text']) in available for row in owned),
        'Owned key reference seed changed')
    native_ids = {row[0] for row in mappings}
    original_owned = tuple(chunk.chunk_id for chunk in chunks
        if chunk.chunk_id in native_ids and (chunk.page,chunk.text) in
            {(row['page'],row['text']) for row in owned})
    selected = _key_clause_native_contexts_v7(mappings,owned,retained_sha,
        record['source_identity']['geometry']['raw_sha256'],owned_chunk_ids=original_owned)
    _require(tuple((chunk.chunk_id,chunk.page,chunk.text) for chunk in chunks) == originals,
        'Complete source changed during clause reference selection')
    return selected


def key_literal_reference_packet_v12(plan, owned, image_pages=()):
    from hashlib import sha256
    contexts = key_literal_definition_contexts_v12(plan, owned)
    tables = json.loads(plan._literal_table_input_json)
    selected = key_explicit_table_references_v11(tables, owned, image_pages)
    compact = [{'page':table['page'], 'label':table['label'],
        'caption':table.get('caption_context',table['text']),
        'columns':len(table['column_bounds'])-1,
        'rows':[[[cell['start_column'],cell['span'],cell['text']] for cell in row['cells']]
            for row in table['rows']]} for table in selected]
    wire = dict(version=KEY_REFERENCE_VERSION, definitions=_key_literal_definition_projection(contexts),
        tables=compact)
    identity = dict(version=KEY_REFERENCE_VERSION, source_sha256=plan.source.geometry.raw_sha256,
        retained_projection_sha256=plan._native_text_presentation_sha256,
        reference_projection_sha256=_hash(_json(wire)), definitions=contexts,
        tables_sha256=sha256(plan._literal_table_input_json.encode()).hexdigest(),
        selected_table_ids=[table['unit_id'] for table in selected],
        selected_cell_count=sum(len(row['cells']) for table in selected for row in table['rows']),
        semantic_alignment_verified=False, definition_meanings_verified=False)
    return identity, wire


