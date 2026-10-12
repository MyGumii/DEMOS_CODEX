"""Opt-in PDF inferred geometry; no legacy extraction or publication activation.

Unlike OOXML native tables, a PDF grid is inferred from drawn lines. This layer
keeps original glyph ownership, page order and neighboring prose, but certifies
neither semantic reading order nor caption ownership. Images are not interpreted.
All I/O, authorization, subprocess deadlines and publication belong to callers.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
import io
import json
import math
from pathlib import PurePath
import re


PDF_GEOMETRY_VERSION = "pdf-inferred-grid-v1"
PDF_VISUAL_ONLY_GEOMETRY_VERSION = "pdf-page-raster-visual-only-v1"
PDF_AMBIGUOUS_GRID_GEOMETRY_VERSION = "pdf-original-glyphs-ambiguous-grid-v1"
_AMBIGUOUS_GRID_ORIGIN = "original_glyphs_ambiguous_grid_pages:"
PDF_ENGINE_VERSION = "0.11.9"
_EPS = 0.25  # PDF points; only tolerance for glyph/grid boundary comparisons.


class PdfGeometryError(ValueError):
    """Unsupported or ambiguous geometry; never permission to publish partial text."""


class PdfGridAmbiguityError(PdfGeometryError):
    """Grid inference ambiguity only; corruption and glyph fidelity stay fatal."""


def ambiguous_grid_pages(document):
    if document.version != PDF_AMBIGUOUS_GRID_GEOMETRY_VERSION:
        return ()
    if not document.structure_origin.startswith(_AMBIGUOUS_GRID_ORIGIN):
        raise PdfGeometryError("Invalid ambiguous-grid origin")
    raw = document.structure_origin[len(_AMBIGUOUS_GRID_ORIGIN):]
    if not re.fullmatch(r"[1-9]\d*(?:,[1-9]\d*)*", raw):
        raise PdfGeometryError("Invalid ambiguous-grid page inventory")
    pages = tuple(map(int, raw.split(",")))
    if tuple(sorted(set(pages))) != pages or pages[-1] > len(document.pages):
        raise PdfGeometryError("Ambiguous-grid page inventory differs")
    return pages


@dataclass(frozen=True, slots=True)
class PdfGeometryLimits:
    max_source_bytes: int = 64 * 1024 * 1024
    max_pages: int = 1000
    max_glyphs: int = 1_000_000
    max_tables: int = 4096
    max_cells: int = 200_000
    max_comparisons: int = 20_000_000
    max_text_bytes: int = 8 * 1024 * 1024
    max_manifest_bytes: int = 32 * 1024 * 1024
    max_render_bytes: int = 16 * 1024 * 1024

    def __post_init__(self):
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise PdfGeometryError("Geometry work limits must be positive integers")


@dataclass(frozen=True, slots=True)
class PdfTextBlock:
    block_id: str
    page: int
    text: str
    glyph_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PdfCell:
    row: int
    column: int
    bbox: tuple[float, float, float, float]
    text: str
    glyph_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PdfTableBlock:
    block_id: str
    page: int
    bbox: tuple[float, float, float, float]
    rows: int
    columns: int
    cells: tuple[PdfCell, ...]


@dataclass(frozen=True, slots=True)
class PdfGeometryPage:
    number: int
    width: float
    height: float
    glyph_count: int
    glyph_sha256: str
    image_count: int
    blocks: tuple[PdfTextBlock | PdfTableBlock, ...]


@dataclass(frozen=True, slots=True)
class PdfGeometryDocument:
    source_sha256: str
    pages: tuple[PdfGeometryPage, ...]
    version: str = PDF_GEOMETRY_VERSION
    engine_version: str = PDF_ENGINE_VERSION
    structure_origin: str = "inferred_line_grid"
    semantic_fidelity_proven: bool = False
    caption_ownership_proven: bool = False
    images_interpreted: bool = False

    def canonical_json(self, *, limits=PdfGeometryLimits()):
        validate_pdf_geometry(self, limits=limits)
        value = _json(asdict(self))
        if len(value.encode("utf-8")) > limits.max_manifest_bytes:
            raise PdfGeometryError("PDF geometry manifest exceeds its work budget")
        return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(",", ":"))


def _hash(value):
    return sha256(value.encode("utf-8")).hexdigest()


def _id(source, page, ordinal, kind):
    return "P_" + _hash(_json([PDF_GEOMETRY_VERSION, source, page, ordinal, kind]))


def _digest(value):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PdfGeometryError("Invalid geometry digest")


def _bbox(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PdfGeometryError("Invalid PDF rectangle")
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in value):
        raise PdfGeometryError("Non-finite PDF rectangle")
    result = tuple(float(n) for n in value)
    if result[0] > result[2] or result[1] > result[3]:
        raise PdfGeometryError("Inverted PDF rectangle")
    return result


def _inside(inner, outer):
    return (inner[0] >= outer[0] - _EPS and inner[1] >= outer[1] - _EPS
            and inner[2] <= outer[2] + _EPS and inner[3] <= outer[3] + _EPS)


def _symbols(text):
    return Counter(char for char in text if not char.isspace())


def _text(value):
    if type(value) is not str or "\x00" in value:
        raise PdfGeometryError("Invalid extracted PDF text")
    return value


def _grid(bbox, rows):
    """Accept only fully tiled rectangular cells; never guess missing spans."""
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise PdfGridAmbiguityError("Incomplete PDF cell grid")
    bottom = bbox[1]
    for row in rows:
        if any(cell is None for cell in row):
            raise PdfGridAmbiguityError("Spanning or missing PDF cells need a separate adapter")
        left, top = bbox[0], bottom
        bottom = row[0][3]
        for column, cell in enumerate(row):
            if (cell[0] >= cell[2] or cell[1] >= cell[3]
                    or any(abs(a - b) > _EPS for a, b in
                           ((cell[0], left), (cell[1], top), (cell[3], bottom)))):
                raise PdfGridAmbiguityError("PDF cells overlap or leave an ambiguous grid")
            if any(abs(cell[index] - rows[0][column][index]) > _EPS for index in (0, 2)):
                raise PdfGridAmbiguityError("PDF column boundaries differ between rows")
            left = cell[2]
        if abs(left - bbox[2]) > _EPS:
            raise PdfGridAmbiguityError("PDF row does not span its table")
    if abs(bottom - bbox[3]) > _EPS:
        raise PdfGridAmbiguityError("PDF column does not span its table")


class _Budget:
    def __init__(self, limits):
        if type(limits) is not PdfGeometryLimits:
            raise PdfGeometryError("An explicit geometry limits value is required")
        self.limits, self.used = limits, Counter()

    def add(self, kind, amount):
        self.used[kind] += amount
        if self.used[kind] > getattr(self.limits, "max_" + kind):
            raise PdfGeometryError(f"PDF geometry {kind} work budget exceeded")


def _page(page, source, budget, extract_text):
    if page.rotation != 0:
        raise PdfGeometryError("Rotated PDF pages need a separately verified reading order")
    chars = list(page.chars)
    budget.add("glyphs", len(chars))
    glyphs = [(_text(c["text"]), _bbox((c["x0"], c["top"], c["x1"], c["bottom"])))
              for c in chars]
    bounds = _bbox((0, 0, page.width, page.height))
    if any(not _inside(box, bounds) for _, box in glyphs):
        raise PdfGeometryError("Out-of-page PDF glyphs need explicit handling")
    tables = []
    for detected in page.find_tables():
        box = _bbox(detected.bbox)
        rows = [[None if c is None else _bbox(c) for c in row.cells]
                for row in detected.rows]
        budget.add("tables", 1)
        budget.add("cells", sum(len(row) for row in rows))
        _grid(box, rows)
        if not _inside(box, bounds):
            raise PdfGeometryError("Table outside PDF page")
        tables.append((box, rows))
    tables.sort(key=lambda pair: (pair[0][1], pair[0][0]))
    for previous, following in zip(tables, tables[1:]):
        if following[0][1] < previous[0][3]:
            raise PdfGridAmbiguityError("Overlapping or side-by-side PDF tables are ambiguous")
    cell_lists = [[[[] for _ in row] for row in rows] for _, rows in tables]
    prose = [[] for _ in range(len(tables) + 1)]
    budget.add("comparisons", len(chars) * (len(tables) + sum(
        len(row) for _, rows in tables for row in rows)))
    for index, (_, box) in enumerate(glyphs):
        assigned = False
        for table_index, (table_box, rows) in enumerate(tables):
            # A prose glyph beside a table would be displaced by vertical-band
            # reconstruction. Reject it instead of silently choosing a column.
            if box[3] > table_box[1] and box[1] < table_box[3]:
                matches = [(r, c) for r, row in enumerate(rows)
                           for c, cell in enumerate(row) if _inside(box, cell)]
                if len(matches) != 1:
                    raise PdfGridAmbiguityError("PDF glyph crosses a cell or lies beside a table")
                r, c = matches[0]
                cell_lists[table_index][r][c].append(index)
                assigned = True
                break
        if not assigned:
            band = sum(box[1] >= table_box[3] for table_box, _ in tables)
            prose[band].append(index)

    def materialize(indices):
        selected = [chars[i] for i in indices]
        value = _text(extract_text(selected, layout=False, expand_ligatures=False)) if selected else ""
        original = "".join(glyphs[i][0] for i in indices)
        if _symbols(value) != _symbols(original):
            raise PdfGeometryError("Text layout omitted or duplicated non-whitespace glyphs")
        budget.add("text_bytes", len(value.encode("utf-8")))
        return value

    blocks = []
    for band, indices in enumerate(prose):
        if indices:
            blocks.append(PdfTextBlock(_id(source, page.page_number, len(blocks), "text"),
                page.page_number, materialize(indices), tuple(indices)))
        if band < len(tables):
            box, rows = tables[band]
            cells = tuple(PdfCell(r, c, cell, materialize(cell_lists[band][r][c]),
                                 tuple(cell_lists[band][r][c]))
                          for r, row in enumerate(rows) for c, cell in enumerate(row))
            blocks.append(PdfTableBlock(_id(source, page.page_number, len(blocks), "table"),
                page.page_number, box, len(rows), len(rows[0]), cells))
    return PdfGeometryPage(page.page_number, float(page.width), float(page.height),
        len(chars), _hash(_json(glyphs)), len(page.images), tuple(blocks))


def _original_glyph_page(page, source, budget, extract_text):
    """Keep every original glyph, without asserting an inferred grid."""
    if page.rotation != 0:
        raise PdfGeometryError("Rotated PDF pages need a separately verified reading order")
    chars = list(page.chars)
    budget.add("glyphs", len(chars))
    glyphs = [(_text(c["text"]), _bbox((c["x0"],c["top"],c["x1"],c["bottom"])))
        for c in chars]
    bounds = _bbox((0,0,page.width,page.height))
    if any(not _inside(box,bounds) for _,box in glyphs):
        raise PdfGeometryError("Out-of-page PDF glyphs need explicit handling")
    value = _text(extract_text(chars, layout=False, expand_ligatures=False)) if chars else ""
    if _symbols(value) != _symbols("".join(text for text,_ in glyphs)):
        raise PdfGeometryError("Text layout omitted or duplicated non-whitespace glyphs")
    budget.add("text_bytes",len(value.encode("utf-8")))
    blocks = (PdfTextBlock(_id(source,page.page_number,0,"text"),
        page.page_number,value,tuple(range(len(chars)))),) if chars else ()
    return PdfGeometryPage(page.page_number,float(page.width),float(page.height),
        len(chars),_hash(_json(glyphs)),len(page.images),blocks)


def extract_pdf_geometry(filename: str, raw: bytes, *, limits=PdfGeometryLimits(),
                         allow_visual_only: bool = False, allow_ambiguous_grid: bool = False):
    """Build an original-bound sidecar. This does not modify extract_document."""
    if type(filename) is not str or PurePath(filename).suffix.lower() != ".pdf":
        raise PdfGeometryError("PDF geometry requires a PDF filename")
    if type(allow_visual_only) is not bool:
        raise PdfGeometryError("Invalid visual-only PDF extraction policy")
    if type(allow_ambiguous_grid) is not bool or (allow_ambiguous_grid and not allow_visual_only):
        raise PdfGeometryError("Ambiguous-grid extraction needs an explicit visual route")
    budget = _Budget(limits)
    if type(raw) is not bytes or not raw or len(raw) > limits.max_source_bytes:
        raise PdfGeometryError("Invalid or oversized PDF bytes")
    try:
        import pdfplumber
        from pdfplumber.utils import extract_text
        if pdfplumber.__version__ != PDF_ENGINE_VERSION:
            raise PdfGeometryError("PDF geometry engine version has not been verified")
        source = sha256(raw).hexdigest()
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            if pdf.doc.encryption is not None:
                raise PdfGeometryError("Encrypted PDF geometry is unsupported")
            if not 0 < len(pdf.pages) <= limits.max_pages:
                raise PdfGeometryError("PDF page work budget exceeded")
            pages, ambiguous_pages = [], []
            for page in pdf.pages:
                used = budget.used.copy()
                try:
                    prepared = _page(page, source, budget, extract_text)
                except PdfGridAmbiguityError:
                    if not allow_ambiguous_grid:
                        raise
                    # Only grid inference ambiguity is handled. Rotations,
                    # corrupt/out-of-page glyphs and text fidelity remain fatal.
                    budget.used = used
                    prepared = _original_glyph_page(page, source, budget, extract_text)
                    ambiguous_pages.append(page.page_number)
                pages.append(prepared)
            pages = tuple(pages)
        if not any(p.glyph_count for p in pages):
            if not allow_visual_only:
                raise PdfGeometryError("No recoverable PDF glyphs; images require a separate path")
            result = PdfGeometryDocument(source, pages,
                version=PDF_VISUAL_ONLY_GEOMETRY_VERSION,
                structure_origin="page_raster_no_glyphs")
        elif ambiguous_pages:
            result = PdfGeometryDocument(source,pages,version=PDF_AMBIGUOUS_GRID_GEOMETRY_VERSION,
                structure_origin=_AMBIGUOUS_GRID_ORIGIN + ",".join(map(str,ambiguous_pages)))
        else:
            result = PdfGeometryDocument(source, pages)
        result.canonical_json(limits=limits)
        return result
    except PdfGeometryError:
        raise
    except Exception as exc:
        raise PdfGeometryError("PDF geometry extraction failed") from exc


def validate_pdf_geometry(document, *, limits=PdfGeometryLimits()):
    budget = _Budget(limits)
    visual_only = (type(document) is PdfGeometryDocument
        and document.version == PDF_VISUAL_ONLY_GEOMETRY_VERSION
        and document.structure_origin == "page_raster_no_glyphs")
    ambiguous = (type(document) is PdfGeometryDocument
        and document.version == PDF_AMBIGUOUS_GRID_GEOMETRY_VERSION)
    if (type(document) is not PdfGeometryDocument
            or not (visual_only or ambiguous or (document.version == PDF_GEOMETRY_VERSION
                and document.structure_origin == "inferred_line_grid"))
            or document.engine_version != PDF_ENGINE_VERSION
            or document.semantic_fidelity_proven is not False
            or document.caption_ownership_proven is not False
            or document.images_interpreted is not False):
        raise PdfGeometryError("Invalid PDF geometry contract")
    _digest(document.source_sha256)
    ambiguous_pages = ambiguous_grid_pages(document) if ambiguous else ()
    if type(document.pages) is not tuple or not 0 < len(document.pages) <= limits.max_pages:
        raise PdfGeometryError("Invalid PDF geometry pages")
    total_glyphs = 0
    for page_no, page in enumerate(document.pages, 1):
        if (type(page) is not PdfGeometryPage or type(page.number) is not int
                or page.number != page_no or type(page.glyph_count) is not int
                or page.glyph_count < 0 or type(page.image_count) is not int or page.image_count < 0
                or type(page.blocks) is not tuple or len(page.blocks) > 2 * limits.max_tables + 1):
            raise PdfGeometryError("Invalid PDF geometry page")
        bounds = _bbox((0, 0, page.width, page.height))
        if page.width <= 0 or page.height <= 0:
            raise PdfGeometryError("Empty PDF page dimensions")
        _digest(page.glyph_sha256)
        budget.add("glyphs", page.glyph_count)
        total_glyphs += page.glyph_count
        assigned = []
        if page_no in ambiguous_pages and any(type(block) is PdfTableBlock for block in page.blocks):
            raise PdfGeometryError("Ambiguous page cannot claim verified grid cells")
        previous_bottom = 0.0
        for ordinal, block in enumerate(page.blocks):
            kind = "text" if type(block) is PdfTextBlock else "table"
            if (type(block) not in (PdfTextBlock, PdfTableBlock) or block.page != page_no
                    or type(block.page) is not int
                    or block.block_id != _id(document.source_sha256, page_no, ordinal, kind)):
                raise PdfGeometryError("Invalid PDF block identity")
            if kind == "text":
                content = [(block.text, block.glyph_indices)]
            else:
                if (type(block.rows) is not int or type(block.columns) is not int
                        or block.rows < 1 or block.columns < 1 or type(block.cells) is not tuple
                        or len(block.cells) != block.rows * block.columns):
                    raise PdfGeometryError("Invalid PDF table dimensions")
                budget.add("tables", 1)
                budget.add("cells", len(block.cells))
                box = _bbox(block.bbox)
                if not _inside(box, bounds) or box[1] < previous_bottom:
                    raise PdfGeometryError("Invalid PDF table order or page bounds")
                previous_bottom = box[3]
                rows, content = [[] for _ in range(block.rows)], []
                for i, cell in enumerate(block.cells):
                    if (type(cell) is not PdfCell or type(cell.row) is not int
                            or type(cell.column) is not int
                            or (cell.row, cell.column) != divmod(i, block.columns)):
                        raise PdfGeometryError("Invalid PDF cell position")
                    rows[cell.row].append(_bbox(cell.bbox))
                    content.append((cell.text, cell.glyph_indices))
                _grid(box, rows)
            for value, indices in content:
                budget.add("text_bytes", len(_text(value).encode("utf-8")))
                if (type(indices) is not tuple or len(assigned) + len(indices) > page.glyph_count
                        or any(type(i) is not int or
                        i < 0 or i >= page.glyph_count for i in indices)):
                    raise PdfGeometryError("Invalid PDF glyph ownership")
                if value.strip() and not indices:
                    raise PdfGeometryError("PDF text has no source glyphs")
                assigned.extend(indices)
        if sorted(assigned) != list(range(page.glyph_count)):
            raise PdfGeometryError("PDF glyph ownership omits or duplicates input")
    if visual_only and (total_glyphs != 0 or any(page.blocks for page in document.pages)):
        raise PdfGeometryError("Visual-only PDF geometry contains glyph claims")
    if total_glyphs == 0 and not visual_only:
        raise PdfGeometryError("No recoverable PDF glyphs")


def revalidate_pdf_geometry(document, filename, raw, *, limits=PdfGeometryLimits()):
    """Re-extract the retained original; a serialized claim is not proof of origin."""
    expected = document.canonical_json(limits=limits)
    actual = (extract_pdf_geometry(filename,raw,limits=limits,allow_visual_only=True,allow_ambiguous_grid=True)
        if document.version == PDF_AMBIGUOUS_GRID_GEOMETRY_VERSION else
        extract_pdf_geometry(filename, raw, limits=limits, allow_visual_only=True)
        if document.version == PDF_VISUAL_ONLY_GEOMETRY_VERSION
        else extract_pdf_geometry(filename, raw, limits=limits))
    if actual.canonical_json(limits=limits) != expected:
        raise PdfGeometryError("PDF geometry differs from the retained original")
    return actual


def render_pdf_table(document, table, *, limits=PdfGeometryLimits()):
    """Safe cell representation, not source attestation or publication authority.

    A storage/commit caller must first revalidate against the retained original.
    Shape checks alone cannot prove that a constructed object came from a PDF.
    """
    document.canonical_json(limits=limits)
    if not any(table is block for page in document.pages for block in page.blocks
               if type(block) is PdfTableBlock):
        raise PdfGeometryError("PDF table is not owned by this document")
    rows = ["<table>"]
    size = len("<table>\n</table>".encode())
    for r in range(table.rows):
        cells = table.cells[r * table.columns:(r + 1) * table.columns]
        rendered = []
        size += len("\n<tr></tr>".encode())
        for cell in cells:
            value = "<td>" + escape(cell.text).replace("\n", "<br>") + "</td>"
            size += len(value.encode("utf-8"))
            if size > limits.max_render_bytes:
                raise PdfGeometryError("PDF table rendering work budget exceeded")
            rendered.append(value)
        rows.append("<tr>" + "".join(rendered) + "</tr>")
    rows.append("</table>")
    return "\n".join(rows)


def adjacent_pdf_blocks(document, block_id, *, limits=PdfGeometryLimits()):
    """Same-original neighbors, including page boundaries; not semantic ownership."""
    document.canonical_json(limits=limits)
    blocks = [block for page in document.pages for block in page.blocks]
    for i, block in enumerate(blocks):
        if block.block_id == block_id:
            return tuple(blocks[j] for j in (i - 1, i + 1) if 0 <= j < len(blocks))
    raise PdfGeometryError("Unknown PDF block")


def pdf_page_context(document, block_id, *, limits=PdfGeometryLimits()):
    """Untrusted location context, not caption/qualification ownership.

    An immediately preceding caption can separate a table from a qualification
    on the previous page. Return complete current/neighboring pages, excluding
    the owner, so a later planner can budget them explicitly without hidden cuts.
    This is not a claim that all dependencies fit within those three pages.
    """
    document.canonical_json(limits=limits)
    owner = next((b for p in document.pages for b in p.blocks if b.block_id == block_id), None)
    if owner is None:
        raise PdfGeometryError("Unknown PDF block")
    return tuple(b for p in document.pages if abs(p.number - owner.page) <= 1
                 for b in p.blocks if b.block_id != block_id)
