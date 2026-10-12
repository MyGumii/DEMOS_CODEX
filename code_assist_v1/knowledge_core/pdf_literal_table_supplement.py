"""Bounded literal tables from one checked same-source layout, not publication.

This pure component neither prepares PDFs nor authors/replaces prose. It reuses
the existing renderer and verifies its literal row/cell/span round-trip. Empty
cells stay empty; source inconsistencies are never corrected. Spatial headers
remain layout evidence, not semantic alignment certification. A future caller
must retain its own source/authorization/commit checks and whole-body budget.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import math

from ..document_pdf_layout import PdfLayoutError, canonical, render_table
from ..document_pdf_process import PdfProcessError
from .pdf_layout_source import PreparedPdfLayout, checked_layout


@dataclass(frozen=True, slots=True)
class LiteralTableSupplementLimits:
    tables: int = 4096
    cells: int = 200_000
    source_bytes: int = 33_554_432
    output_bytes: int = 1_048_576

    def __post_init__(self):
        ceilings = (4096, 200_000, 33_554_432, 16_777_216)
        if any(type(value) is not int or not 1 <= value <= maximum
                for value, maximum in zip(asdict(self).values(), ceilings)):
            raise PdfLayoutError('Invalid literal table supplement limits')


@dataclass(frozen=True, slots=True)
class LiteralTableSupplement:
    markdown: str
    tables_json: str
    table_count: int
    cell_count: int
    source_sha256: str
    layout_catalog_sha256: str


def _require(value, reason):
    if not value:
        raise PdfLayoutError(reason)


def _cancel(event):
    if event is not None and event.is_set():
        raise PdfProcessError('CANCELLED')


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _box_inside(box, outer, tolerance=0):
    return (type(box) is list and len(box) == 4 and all(_number(n) for n in box)
        and box[0] < box[2] and box[1] < box[3]
        and outer[0]-tolerance <= box[0] and outer[1]-tolerance <= box[1]
        and box[2] <= outer[2]+tolerance and box[3] <= outer[3]+tolerance)


def _checked_cells(table, page, remaining, event):
    _require(table.get('status') == 'extracted', 'Unresolved table must not be guessed or omitted')
    _require(type(table.get('label')) is str and table['label']
        and type(table.get('text')) is str and type(table.get('caption_context',table['text'])) is str
        and type(table.get('page')) is int and table['page'] == page['page'],
        'Literal table caption/page identity differs')
    box = table.get('bbox')
    _require(_box_inside(box,[0,0,page['width'],page['height']]), 'Table bounds differ from source page')
    bounds = table.get('column_bounds')
    _require(type(bounds) is list and len(bounds) >= 2 and all(_number(n) for n in bounds)
        and all(a < b for a,b in zip(bounds,bounds[1:])) and bounds[0] == box[0]
        and bounds[-1] == box[2], 'Complete ordered literal column bounds required')
    rows = table.get('rows'); result = []; count = 0
    _require(type(rows) is list and rows, 'Complete extracted table rows required')
    for row in rows:
        _cancel(event)
        _require(type(row) is dict and type(row.get('cells')) is list and row['cells'],
            'Complete literal table row required')
        cursor, literals = 0, []
        for cell in row['cells']:
            _cancel(event)
            count += 1
            _require(count <= remaining, 'Complete literal table cell inventory exceeds its bound')
            _require(type(cell) is dict and type(cell.get('start_column')) is int
                and type(cell.get('span')) is int and cell['start_column'] == cursor
                and cell['span'] >= 1 and cursor+cell['span'] <= len(bounds)-1
                and type(cell.get('text')) is str
                and _box_inside(cell.get('bbox'),box,.5),
                'Literal cell order/span/text/bounds differs')
            cursor += cell['span']
            literals.append((cell['text'],cell['span']))
        _require(cursor == len(bounds)-1, 'Literal row has an unowned column remainder')
        result.append(literals)
    return result, count


class _LiteralCells(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell, self.span = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            _require(self.row is None and self.cell is None, 'Nested literal table row')
            self.row = []
        elif tag == 'td':
            _require(self.row is not None and self.cell is None
                and len(attrs) == 1 and attrs[0][0] == 'colspan', 'Changed literal cell markup')
            self.cell, self.span = [], int(attrs[0][1])
        elif tag == 'br':
            _require(self.cell is not None and not attrs, 'Changed literal multiline markup')
            self.cell.append('\n')
        else:
            _require(tag == 'table' and not attrs, 'Unexpected literal table markup')

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'td':
            _require(self.row is not None and self.cell is not None, 'Unopened literal cell')
            self.row.append((''.join(self.cell),self.span)); self.cell = None
        elif tag == 'tr':
            _require(self.row is not None and self.cell is None, 'Unclosed literal table cell')
            self.rows.append(self.row); self.row = None
        else:
            _require(tag == 'table', 'Unexpected literal table markup')


def render_literal_table_supplement(source, layout, *, limits=LiteralTableSupplementLimits(), cancel_event=None):
    """Render all extracted tables or fail; never a selected/truncated prefix.

    The returned value is a fragment/data result, not a publication capability.
    It receives no authored body and therefore cannot rewrite that body.
    """
    _cancel(cancel_event)
    _require(type(limits) is LiteralTableSupplementLimits and type(layout) is PreparedPdfLayout
        and layout.source is source and type(layout.catalog_json) is str,
        'One checked layout and its exact original source required')
    _require(len(layout.catalog_json.encode()) <= limits.source_bytes,
        'Complete literal table source catalog exceeds its byte bound')
    catalog = checked_layout(layout)
    original_json = layout.catalog_json
    pages = catalog.get('pages')
    _require(type(pages) is list and len(pages) == len(source.pages), 'Complete source layout pages required')
    output, tables, seen, cells, output_bytes = [], [], set(), 0, 0
    for ordinal,page in enumerate(pages,1):
        _cancel(cancel_event)
        _require(type(page) is dict and type(page.get('page')) is int and page['page'] == ordinal
            and all(_number(page.get(key)) and page[key] > 0 for key in ('width','height'))
            and type(page.get('units')) is list, 'Ordered source page layout required')
        for table in page['units']:
            _cancel(cancel_event)
            _require(type(table) is dict, 'Source layout unit required')
            if table.get('kind') != 'table':
                continue
            _require(type(table.get('unit_id')) is str and table['unit_id'] and table['unit_id'] not in seen,
                'Unique literal table source owner required')
            seen.add(table['unit_id'])
            _require(len(seen) <= limits.tables, 'Complete literal table inventory exceeds its bound')
            expected, count = _checked_cells(table,page,limits.cells-cells,cancel_event)
            cells += count
            literal = render_table(table)
            _cancel(cancel_event)
            parts = ['<h3>원문 표 '+escape(table['label'])+' · p. '+str(table['page'])+'</h3>',
                '원문 셀을 그대로 보존했습니다. 머리글의 공간 배치는 좌표 기반이며 빈 셀을 0으로 해석하지 않습니다.',
                '<pre>'+escape(table.get('caption_context',table['text']))+'</pre>',literal]
            fragment = '\n\n'.join(parts)
            output_bytes += len(fragment.encode()) + (2 if output else 0)
            _require(output_bytes+1 <= limits.output_bytes, 'Complete literal table output exceeds its byte bound')
            parser = _LiteralCells()
            for start in range(0,len(literal),4096):
                _cancel(cancel_event); parser.feed(literal[start:start+4096])
            parser.close()
            _require(parser.cell is None and parser.row is None and parser.rows == expected,
                'Literal row/cell/text/span changed during rendering')
            output.append(fragment); tables.append(table)
    _cancel(cancel_event)
    _require(layout.source is source and layout.catalog_json == original_json
        and checked_layout(layout) == catalog, 'Same-source layout changed during literal rendering')
    _cancel(cancel_event)
    markdown = '\n\n'.join(output)+'\n' if output else ''
    return LiteralTableSupplement(markdown,canonical(tables),len(tables),cells,
        source.geometry.raw_sha256,sha256(original_json.encode()).hexdigest())
