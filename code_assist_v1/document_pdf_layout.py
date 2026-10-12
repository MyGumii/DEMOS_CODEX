"""Forward, bounded PDF layout evidence. Never publication or semantic authority.

The historical inferred-grid parser is deliberately untouched. This sidecar
retains native words with their coordinates, conservative aligned table rows,
and individual visual regions. Spatial neighbours are NOT caption/claim proof.
Raster-only chart values remain unknown until a model reads the actual pixels.
"""
from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import math
import re

VERSION = "pdf-layout-evidence-v1"
ENGINE = "0.11.9"
_CAPTION = re.compile(r"^(?P<kind>TABLE|Table|表|표|Fig\.?|Figure|FIGURE|그림)\s*(?P<label>[A-Za-z]?\d+(?:[.\-]\d+)*|[IVXLCDM]+)(?:[.:\s]|$)")


class PdfLayoutError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PdfLayoutLimits:
    source_bytes: int = 67_108_864
    pages: int = 1000
    glyphs: int = 1_000_000
    units: int = 4096
    cells: int = 200_000
    manifest_bytes: int = 32 * 1024 * 1024

    def __post_init__(self):
        ceilings = (67_108_864, 1000, 1_000_000, 4096, 200_000, 32 * 1024 * 1024)
        if any(type(v) is not int or not 1 <= v <= cap for v, cap in zip(asdict(self).values(), ceilings)):
            raise PdfLayoutError("Invalid layout work limits")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))


def _box(value):
    if (len(value) != 4 or any(type(n) not in (int, float) or not math.isfinite(n) for n in value)
            or value[0] >= value[2] or value[1] >= value[3]):
        raise PdfLayoutError("Invalid layout rectangle")
    return [float(n) for n in value]


def _rows(words):
    """Local baselines, not global two-column reading order certification."""
    groups = []
    for word in sorted(words, key=lambda w: ((w['top'] + w['bottom']) / 2, w['x0'])):
        middle = (word['top'] + word['bottom']) / 2
        if not groups or middle - groups[-1][0] > 3.5:
            groups.append([middle, []])
        groups[-1][1].append(word)
    return [sorted(group, key=lambda w: w['x0']) for _, group in groups]


def _segments(row, gap=8.0):
    groups = []
    for word in row:
        if not groups or word['x0'] - groups[-1][-1]['x1'] >= gap:
            groups.append([])
        groups[-1].append(word)
    return groups


def _text(words):
    return ' '.join(w['text'] for w in words)


def _bounds(words):
    return [min(w['x0'] for w in words), min(w['top'] for w in words),
            max(w['x1'] for w in words), max(w['bottom'] for w in words)]


def _captions(page, words):
    result = []
    # Separate a possible caption from neighbouring text in another column.
    for row in _rows(words):
        for offset, word in enumerate(row):
            if re.fullmatch(r'TABLE|Table|表|표|Fig\.?|Figure|FIGURE|그림', word['text']) is None:
                continue
            following = row[offset:]
            selected = [following[0]]
            for next_word in following[1:]:
                if (next_word['x0'] - selected[-1]['x1'] > 30
                        or next_word['bottom']-next_word['top'] > 1.15*(word['bottom']-word['top'])):
                    break
                selected.append(next_word)
            text = _text(selected)
            match = _CAPTION.match(text)
            if match is None:
                continue
            # Mid-sentence references are not caption inventories. A caption
            # starts its column, or is a short standalone table identifier.
            standalone = len(selected) <= 2
            if offset and not standalone and word['x0'] - row[offset-1]['x1'] < 10:
                continue
            if offset and standalone and word['x0'] - row[offset-1]['x1'] < 10:
                continue
            kind = 'table' if match['kind'] in ('TABLE', 'Table', '表', '표') else 'figure'
            if kind == 'table' and match['kind'] == 'Table' and len(selected) > 3:
                continue  # running-text references are not standalone captions
            result.append({'kind': kind, 'label': match['label'], 'text': text,
                           'bbox': _bounds(selected), 'association': 'spatial_only'})
    return result


def _caption_context(caption, words, *, maximum_y=None):
    """Literal same-column continuation, explicitly only spatial context."""
    parts = [caption['text']]
    box = caption['bbox']
    bottom = box[3]
    size = box[3]-box[1]
    for row in _rows(words):
        selected = [w for w in row if w['x0'] >= box[0]-2 and w['x1'] <= box[2]+2
                    and w['top'] >= bottom-.5 and (maximum_y is None or w['bottom']<maximum_y)]
        if not selected:
            continue
        top = min(w['top'] for w in selected)
        # Table captions commonly use small caps, with several glyph heights
        # in the same literal title. A known table border bounds this context
        # before its headers, so those smaller caps need not be discarded.
        tolerance=.45 if maximum_y is not None else .18
        if top-bottom > size*2 or any(abs((w['bottom']-w['top'])-size) > size*tolerance for w in selected):
            break
        parts.append(_text(selected))
        bottom = max(w['bottom'] for w in selected)
    return '\n'.join(parts)


def _table_region(page, caption, captions):
    y = caption['bbox'][3]
    rules = [line for line in page.lines if abs(line['top'] - line['bottom']) <= .5
             and line['top'] >= y and line['top'] - y < 150
             and line['x0'] <= (caption['bbox'][0]+caption['bbox'][2])/2 <= line['x1']
             and line['x1']-line['x0'] >= 80]
    if rules:
        first = min(rules, key=lambda r: r['top'])
        ceiling = min((c['bbox'][1] for c in captions if c is not caption
            and c['bbox'][1] > first['top'] and c['bbox'][0] < first['x1']
            and c['bbox'][2] > first['x0']), default=page.height)
        aligned = [r for r in page.lines if abs(r['top']-r['bottom']) <= .5
            and abs(r['x0']-first['x0']) < 3 and abs(r['x1']-first['x1']) < 3
            and first['top'] <= r['top'] < ceiling]
        # Do not expand a table over intervening unrelated body text.
        ordered = sorted(aligned, key=lambda r: r['top'])
        run = [ordered[0]]
        for rule in ordered[1:]:
            if rule['top'] - run[-1]['top'] > 100:
                break
            run.append(rule)
        if len(run) >= 2 and run[-1]['top']-first['top'] > 12:
            return [first['x0'], first['top'], first['x1'], run[-1]['top']], 'horizontal_rules'
    # Unruled tables require a dense repeated aligned row pattern, not an
    # unrestricted text-table strategy over the entire page (which eats prose).
    rows = _rows(page.extract_words(x_tolerance=1, y_tolerance=3))
    candidates = [r for r in rows if min(w['top'] for w in r) >= y
                  and min(w['top'] for w in r)-y < 200]
    dense = []
    for row in candidates:
        if len(_segments(row)) >= 3 and sum(bool(re.search(r'\d', w['text'])) for w in row) >= 2:
            if dense and min(w['top'] for w in row)-max(w['bottom'] for w in dense[-1]) > 20:
                break
            dense.append(row)
        elif dense:
            break
    if len(dense) >= 3:
        box = _bounds([w for row in dense for w in row])
        box[1] = y
        return box, 'caption_aligned_rows'
    return None, 'unresolved_table_layout'


def _table(page, caption, captions, words):
    box, method = _table_region(page, caption, captions)
    if box is None:
        return {'bbox': caption['bbox'], 'rows': [], 'column_bounds': [],
                'status': 'unresolved', 'reason': method}
    selected = [w for w in words if w['x0'] >= box[0]-.5 and w['x1'] <= box[2]+.5
                and w['top'] >= box[1]-.5 and w['bottom'] <= box[3]+.5]
    rows = _rows(selected)
    counts = Counter(len(_segments(row)) for row in rows)
    repeated = [count for count, frequency in counts.items() if count >= 3 and frequency >= 3]
    if not repeated:
        return {'bbox': box, 'rows': [], 'column_bounds': [], 'status': 'unresolved',
                'reason': 'no_repeated_column_pattern'}
    count = max(repeated)
    body = [_segments(row) for row in rows if len(_segments(row)) == count]
    if any(max(group[col][0]['x0'] for group in body) > min(group[col][-1]['x1'] for group in body)+2
           for col in range(count)):
        return {'bbox':box,'rows':[],'column_bounds':[],'status':'unresolved',
                'reason':'inconsistent_column_alignment'}
    # A boundary must lie inside a shared whitespace interval in every body
    # row. Never pick a global x cut through a number or its uncertainty sign.
    bounds = [box[0]]
    for col in range(count-1):
        left = max(group[col][-1]['x1'] for group in body)
        right = min(group[col+1][0]['x0'] for group in body)
        if right-left < 2:
            return {'bbox': box, 'rows': [], 'column_bounds': [], 'status': 'unresolved',
                    'reason': 'inconsistent_column_whitespace'}
        bounds.append((left+right)/2)
    bounds.append(box[2])
    first_dense = min(_bounds([w for group in row for w in group])[1] for row in body)
    dividing_rules = [line['top'] for line in page.lines if abs(line['top']-line['bottom']) < .5
        and abs(line['x0']-box[0]) < 3 and abs(line['x1']-box[2]) < 3
        and box[1]+3 < line['top'] < first_dense]
    header_bottom = max(dividing_rules, default=first_dense)
    result = []
    for row in rows:
        groups = _segments(row)
        if min(w['top'] for w in row) >= header_bottom-.5:
            slots = [[] for _ in range(count)]
            for word in row:
                owned = [c for c in range(count) if word['x0'] >= bounds[c]-.5 and word['x1'] <= bounds[c+1]+.5]
                if len(owned) != 1:
                    return {'bbox':box,'rows':[],'column_bounds':[],'status':'unresolved',
                            'reason':'body_word_crosses_column'}
                slots[owned[0]].append(word)
            cells = [{'start_column': i, 'span': 1, 'text': _text(group),
                      'bbox':_bounds(group) if group else [bounds[i],_bounds(row)[1],bounds[i+1],_bounds(row)[3]]}
                     for i, group in enumerate(slots)]
            role = 'aligned_row'
        else:
            # Keep multilevel headings as literal position-bound spans. This
            # does not invent semantic labels for empty cells or certify units.
            cells = []
            centers = [(g[0]['x0']+g[-1]['x1'])/2 for g in groups]
            leading = sum(bounds[c+1] <= groups[0][0]['x0']+2 for c in range(count))
            cuts = [bounds[leading], *[(a+b)/2 for a,b in zip(centers, centers[1:])], box[2]]
            if leading:
                cells.append({'start_column':0,'span':leading,'text':'',
                              'bbox':[box[0],_bounds(row)[1],bounds[leading],_bounds(row)[3]]})
            for i, group in enumerate(groups):
                owned = [c for c in range(count) if cuts[i] <= (bounds[c]+bounds[c+1])/2 < cuts[i+1]]
                if not owned:
                    return {'bbox': box, 'rows': [], 'column_bounds': [], 'status': 'unresolved',
                            'reason': 'ambiguous_header_span'}
                cells.append({'start_column': owned[0], 'span': len(owned),
                              'text': _text(group), 'bbox': _bounds(group)})
            role = 'positioned_header'
        result.append({'role': role, 'cells': cells})
    native_chars = [c['text'] for c in page.chars if c['x0'] >= box[0]-.5 and c['x1'] <= box[2]+.5
                    and c['top'] >= box[1]-.5 and c['bottom'] <= box[3]+.5]
    def symbols(text):
        return Counter(c for c in text if not c.isspace())
    if symbols(''.join(native_chars)) != symbols(''.join(c['text'] for r in result for c in r['cells'])):
        return {'bbox': box, 'rows': [], 'column_bounds': [], 'status': 'unresolved',
                'reason': 'native_glyph_coverage_mismatch'}
    return {'bbox': box, 'rows': result, 'column_bounds': bounds, 'status': 'extracted',
            'reason': method, 'semantic_alignment_proven': False}


def extract_layout(raw, *, limits=PdfLayoutLimits()):
    if type(raw) is not bytes or not raw or len(raw) > limits.source_bytes:
        raise PdfLayoutError('Invalid or oversized layout source')
    import pdfplumber
    if pdfplumber.__version__ != ENGINE:
        raise PdfLayoutError('Unverified layout engine version')
    source = sha256(raw).hexdigest()
    pages, units, glyphs, cells = [], 0, 0, 0
    with pdfplumber.open(BytesIO(raw)) as pdf:
        if pdf.doc.encryption is not None or not 0 < len(pdf.pages) <= limits.pages:
            raise PdfLayoutError('Encrypted source or page budget exceeded')
        for page in pdf.pages:
            if page.rotation != 0:
                raise PdfLayoutError('Rotated layout needs an explicit adapter')
            glyphs += len(page.chars)
            if glyphs > limits.glyphs:
                raise PdfLayoutError('Layout glyph budget exceeded')
            words = page.extract_words(x_tolerance=1, y_tolerance=3, expand_ligatures=False)
            captions = _captions(page, words)
            entries = []
            used_images = set()
            for caption in captions:
                entry = dict(caption)
                entry['caption_context'] = _caption_context(caption,words)
                if caption['kind'] == 'table':
                    entry.update(_table(page, caption, captions, words))
                    if entry['status']=='extracted':
                        wider_caption = dict(caption,bbox=[entry['bbox'][0],caption['bbox'][1],
                            entry['bbox'][2],caption['bbox'][3]])
                        entry['caption_context'] = _caption_context(wider_caption,words,maximum_y=entry['bbox'][1])
                else:
                    centre = (caption['bbox'][0]+caption['bbox'][2])/2
                    candidates = [(index, image) for index, image in enumerate(page.images)
                        if image['bottom'] <= caption['bbox'][1]+1
                        and 0 <= caption['bbox'][1]-image['bottom'] < 100
                        and image['x0'] <= centre <= image['x1']]
                    if candidates:
                        index, image = min(candidates, key=lambda pair: caption['bbox'][1]-pair[1]['bottom'])
                        used_images.add(index)
                        entry.update(bbox=_box([image['x0'], image['top'], image['x1'], image['bottom']]),
                            status='region', reason='preceding_native_image', association='spatial_only')
                    else:
                        entry.update(bbox=[0., 0., float(page.width), float(page.height)],
                            status='page_context', reason='vector_or_unresolved_figure_region')
                entries.append(entry)
            for index, image in enumerate(page.images):
                if index not in used_images:
                    entries.append({'kind':'visual', 'label':str(index+1), 'text':'',
                        'bbox':_box([image['x0'],image['top'],image['x1'],image['bottom']]),
                        'status':'region', 'reason':'uncaptioned_native_image', 'association':'unproven'})
            for index, entry in enumerate(entries):
                entry['unit_id'] = 'PL_' + sha256(canonical([VERSION,source,page.page_number,index,entry]).encode()).hexdigest()
                entry['page'] = page.page_number
                cells += sum(len(r['cells']) for r in entry.get('rows', []))
            units += len(entries)
            if units > limits.units or cells > limits.cells:
                raise PdfLayoutError('Layout unit/cell budget exceeded')
            pages.append({'page':page.page_number, 'width':float(page.width), 'height':float(page.height),
                          'units':entries})
    result = {'version':VERSION,'engine':ENGINE,'raw_sha256':source,'pages':pages,
              'semantic_fidelity_proven':False,'caption_ownership_proven':False}
    if len(canonical(result).encode()) > limits.manifest_bytes:
        raise PdfLayoutError('Complete layout manifest exceeds its budget')
    return result


def render_table(unit):
    if unit['kind'] != 'table' or unit['status'] != 'extracted':
        raise PdfLayoutError('Only extracted table rows can be assembled')
    def safe(text):
        return escape(text).replace('\n','<br>')
    lines = ['<table>']
    for row in unit['rows']:
        lines.append('<tr>' + ''.join('<td colspan="'+str(c['span'])+'">'+safe(c['text'])+'</td>'
                                     for c in row['cells']) + '</tr>')
    lines.append('</table>')
    return '\n'.join(lines)
