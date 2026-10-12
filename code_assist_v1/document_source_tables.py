"""Opt-in native table contract; no legacy extractor or authoring activation.

Read actual OOXML, not model output or HTML embedded in source prose. Logical
cells/captions are rendered by the server, never accepted as executable HTML.
Locations identify native XML elements, not inferred facts or layout geometry.
The caller must bind this document to the retained original artifact before
using it in a successor extraction/authoring receipt.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from html import escape
import io
import json
from pathlib import Path
import posixpath
import re
import zipfile

from . import document_extractors as native


NATIVE_TABLE_VERSION = "native-source-tables-v1"
MAX_DEPTH = 32
MAX_TABLES = 4096
MAX_GRID_CELLS = 200_000
MAX_TEXT_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_RENDER_BYTES = 16 * 1024 * 1024
_W, _A, _P = native._W, native._A, native._P
_DOCX_NONCONTENT = frozenset(_W + name for name in (
    "sectPr", "tcPr", "sdtPr", "sdtEndPr", "customXmlPr", "bookmarkStart",
    "bookmarkEnd", "proofErr", "permStart", "permEnd", "commentRangeStart", "commentRangeEnd",
))
_DOCX_TRACKED_REVISION = frozenset(_W + name for name in (
    "ins", "del", "moveFrom", "moveTo", "moveFromRangeStart", "moveFromRangeEnd",
    "moveToRangeStart", "moveToRangeEnd", "pPrChange", "rPrChange", "tblPrChange",
    "tblGridChange", "trPrChange", "tcPrChange", "sectPrChange", "numberingChange",
    "cellIns", "cellDel", "cellMerge",
    "customXmlInsRangeStart", "customXmlInsRangeEnd", "customXmlDelRangeStart", "customXmlDelRangeEnd",
    "customXmlMoveFromRangeStart", "customXmlMoveFromRangeEnd", "customXmlMoveToRangeStart", "customXmlMoveToRangeEnd",
)) | frozenset("{http://schemas.microsoft.com/office/word/2010/wordml}" + name for name in (
    "conflictIns", "conflictDel", "customXmlConflictInsertionRangeStart", "customXmlConflictInsertionRangeEnd",
    "customXmlConflictDeletionRangeStart", "customXmlConflictDeletionRangeEnd",
))


class SourceTableError(ValueError):
    """Reject an ambiguous/unsupported structure instead of losing source cells."""


@dataclass(frozen=True, slots=True)
class NativeLocation:
    part: str
    # Zero-based element-child path from the XML part's root (not an ordinal
    # obtained by searching for matching text). Generated labels stay internal.
    path: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NativeText:
    location: NativeLocation
    text: str
    role: str = "paragraph"
    level: int = 0


@dataclass(frozen=True, slots=True)
class NativeCell:
    location: NativeLocation
    row: int
    column: int
    rowspan: int
    colspan: int
    header: bool
    blocks: tuple[NativeText | NativeTable, ...]


@dataclass(frozen=True, slots=True)
class NativeTable:
    table_id: str
    location: NativeLocation
    row_count: int
    column_count: int
    cells: tuple[NativeCell, ...]
    caption: NativeText | None
    section_path: tuple[NativeText, ...]
    # OOXML tblCaption is accessibility metadata, not a visible Caption paragraph.
    accessibility_caption: NativeText | None = None
    # PresentationML firstRow requests styling; it does not declare a header.
    first_row_style: bool | None = None


@dataclass(frozen=True, slots=True)
class NativeTableDocument:
    source_sha256: str
    format: str
    blocks: tuple[NativeText | NativeTable, ...]
    version: str = NATIVE_TABLE_VERSION
    coverage: str = "main_story_or_declared_slide_native_tables"
    source_parts: tuple[str, ...] = ()

    def canonical_json(self) -> str:
        value = json.dumps(asdict(self), ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":"))
        if len(value.encode("utf-8")) > MAX_MANIFEST_BYTES:
            raise SourceTableError("Native table manifest exceeds its work budget")
        return value


def _integer(value, name, *, zero=False):
    if type(value) is not int or not (0 if zero else 1) <= value <= MAX_GRID_CELLS:
        raise SourceTableError(f"Invalid {name}")


def _table_id(digest, location):
    value = json.dumps([NATIVE_TABLE_VERSION, digest, location.part, location.path],
        separators=(",", ":"))
    return "T_" + sha256(value.encode()).hexdigest()


def _element_paths(root, part):
    result, pending = {}, [(root, ())]
    while pending:
        element, path = pending.pop()
        if len(path) > MAX_DEPTH:
            raise SourceTableError("Native XML nesting exceeds its work budget")
        result[id(element)] = NativeLocation(part, path)
        pending.extend((child, (*path, index)) for index, child in enumerate(element))
    return result


def _paragraph_role(paragraph):
    style = paragraph.find(f"./{_W}pPr/{_W}pStyle")
    value = "" if style is None else (style.get(_W + "val") or "").casefold()
    if value == "caption":
        return "caption", 0
    if value == "title":
        return "heading", 1
    match = re.fullmatch(r"heading([1-6])", value)
    return ("heading", int(match.group(1))) if match else ("paragraph", 0)


def _span(value, name):
    if value is None:
        return 1
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise SourceTableError(f"Invalid {name}")
    number = int(value)
    _integer(number, name)
    return number


def _merge_flag(value):
    if value is None:
        return False
    if value not in {"0", "1", "true", "false", "on", "off"}:
        raise SourceTableError("Invalid native merge flag")
    return value in {"1", "true", "on"}


def _property_enabled(element, attribute, *, missing_value=False):
    if element is None:
        return False
    value = element.get(attribute)
    return missing_value if value is None else _merge_flag(value)


def _assert_owned_structure(container, consumed, tag, table_tag):
    """A nested table owns its own rows/cells; unknown wrappers cannot hide any."""
    expected, pending = set(), list(container)
    while pending:
        child = pending.pop()
        if child.tag == table_tag:
            continue
        if child.tag == tag:
            expected.add(id(child))
        pending.extend(child)
    if expected != {id(element) for element in consumed}:
        raise SourceTableError("Unsupported native row/cell placement would omit source structure")


def _grid_width(grid, column_tag, fallback):
    if grid is None:
        return fallback
    if not len(grid) or any(child.tag != column_tag for child in grid):
        raise SourceTableError("Unsupported native table grid structure")
    return len(grid)


class _Parser:
    def __init__(self, digest):
        self.digest = digest
        self.table_count = 0
        self.grid_work = 0
        self.text_bytes = 0
        self.table_locations = set()

    def record_table(self, location):
        if location in self.table_locations:
            raise SourceTableError("A native table was consumed more than once")
        self.table_locations.add(location)

    def check_coverage(self, root, locations, tag):
        expected = {locations[id(element)] for element in root.iter(tag)}
        consumed = {location for location in self.table_locations if location.part == locations[id(root)].part}
        if expected != consumed:
            raise SourceTableError("Unsupported native table placement would omit or duplicate a table")

    def text(self, text, location, role="paragraph", level=0):
        self.text_bytes += len(text.encode("utf-8"))
        if self.text_bytes > MAX_TEXT_BYTES:
            raise SourceTableError("Native text exceeds its work budget")
        return NativeText(location, text, role, level)

    def admit_table(self, rows, columns):
        _integer(rows, "row count")
        _integer(columns, "column count")
        self.table_count += 1
        self.grid_work += rows * columns
        if self.table_count > MAX_TABLES or self.grid_work > MAX_GRID_CELLS:
            raise SourceTableError("Native table grid exceeds its work budget")

    def docx_blocks(self, container, locations, section=(), *, depth=0):
        if depth > MAX_DEPTH:
            raise SourceTableError("Native block nesting exceeds its work budget")
        blocks, headings = [], list(section)
        for child in container:
            if child.tag == _W + "p":
                role, level = _paragraph_role(child)
                text = native._docx_paragraph_text(child)
                block = self.text(text, locations[id(child)], role, level)
                # Keep empty paragraphs: they break explicit caption adjacency.
                blocks.append(block)
                if role == "heading" and text:
                    while headings and headings[-1].level >= level:
                        headings.pop()
                    headings.append(block)
            elif child.tag == _W + "tbl":
                blocks.append(self.docx_table(child, locations, tuple(headings), None, depth + 1))
            elif child.tag in native._DOCX_CONTENT_WRAPPERS:
                # Wrapper boundaries do not imply a caption-to-table relation.
                wrapped = self.docx_blocks(child, locations, tuple(headings), depth=depth + 1)
                blocks.extend(wrapped)
                for block in wrapped:
                    if isinstance(block, NativeText) and block.role == "heading" and block.text:
                        while headings and headings[-1].level >= block.level:
                            headings.pop()
                        headings.append(block)
            elif child.tag not in _DOCX_NONCONTENT:
                raise SourceTableError("Unsupported DOCX block content cannot be silently omitted")
        return _associate_captions(tuple(blocks))

    def docx_table(self, table, locations, section, caption, depth):
        self.record_table(locations[id(table)])
        rows = list(native._docx_table_rows(table))
        _assert_owned_structure(table, rows, _W + "tr", _W + "tbl")
        row_cells = [list(native._docx_row_cells(row)) for row in rows]
        widths = []
        for row, cells in zip(rows, row_cells):
            _assert_owned_structure(row, cells, _W + "tc", _W + "tbl")
            if not cells:
                raise SourceTableError("Native table has an empty row")
            if row.find(f"./{_W}trPr/{_W}gridBefore") is not None or row.find(f"./{_W}trPr/{_W}gridAfter") is not None:
                raise SourceTableError("DOCX omitted grid cells require an explicit successor contract")
            widths.append(sum(self.docx_colspan(cell) for cell in cells))
        grid = table.find(_W + "tblGrid")
        width = _grid_width(grid, _W + "gridCol", widths[0] if widths else 0)
        self.admit_table(len(rows), width)
        if any(value != width for value in widths):
            raise SourceTableError("Native table rows disagree with the column grid")
        cells, active = [], {}
        for row_index, (row, raw_cells) in enumerate(zip(rows, row_cells)):
            header = _property_enabled(row.find(f"./{_W}trPr/{_W}tblHeader"), _W + "val", missing_value=True)
            column, next_active = 0, {}
            for raw_cell in raw_cells:
                span = self.docx_colspan(raw_cell)
                if raw_cell.find(f"./{_W}tcPr/{_W}hMerge") is not None:
                    raise SourceTableError("Legacy DOCX hMerge requires explicit normalization")
                blocks = self.docx_blocks(raw_cell, locations, section, depth=depth)
                merge = raw_cell.find(f"./{_W}tcPr/{_W}vMerge")
                mode = None if merge is None else (merge.get(_W + "val") or "continue")
                key = (column, span)
                if mode == "continue":
                    if key not in active:
                        raise SourceTableError("DOCX vertical merge has no exact preceding anchor")
                    if any(isinstance(block, NativeTable) or block.text for block in blocks):
                        raise SourceTableError("DOCX merge continuation contains unowned content")
                    owner = active[key]
                    if cells[owner].header != header:
                        raise SourceTableError("DOCX vertical merge crosses a header boundary")
                    cells[owner] = replace(cells[owner], rowspan=cells[owner].rowspan + 1)
                    next_active[key] = owner
                elif mode in {None, "restart"}:
                    cells.append(NativeCell(locations[id(raw_cell)], row_index, column,
                        1, span, header, blocks))
                    if mode == "restart":
                        next_active[key] = len(cells) - 1
                else:
                    raise SourceTableError("Invalid DOCX vertical merge mode")
                column += span
            active = next_active
        # Native table properties are source-authored, unlike generated Table N.
        native_caption = table.find(f"./{_W}tblPr/{_W}tblCaption")
        accessibility_caption = None
        if native_caption is not None:
            value = native_caption.get(_W + "val") or ""
            if value:
                accessibility_caption = self.text(value, locations[id(native_caption)], "accessibility_caption")
        location = locations[id(table)]
        return NativeTable(_table_id(self.digest, location), location, len(rows), width,
            tuple(cells), caption, section, accessibility_caption)

    @staticmethod
    def docx_colspan(cell):
        node = cell.find(f"./{_W}tcPr/{_W}gridSpan")
        return _span(None if node is None else node.get(_W + "val"), "DOCX column span")

    def pptx_blocks(self, container, locations, *, depth=0):
        if depth > MAX_DEPTH:
            raise SourceTableError("Native shape nesting exceeds its work budget")
        blocks = []
        for child in container:
            if child.tag in {_P + "sp", _P + "cxnSp"}:
                body = child.find(_P + "txBody")
                if body is not None:
                    blocks.extend(self.text(native._pptx_paragraph_text(p), locations[id(p)])
                        for p in body.findall(_A + "p"))
            elif child.tag == _P + "graphicFrame":
                found = child.findall(f".//{_A}tbl")
                if len(found) > 1:
                    raise SourceTableError("A graphic frame contains multiple native tables")
                if found:
                    blocks.append(self.pptx_table(found[0], locations))
            elif child.tag == _P + "grpSp":
                blocks.extend(self.pptx_blocks(child, locations, depth=depth + 1))
        return tuple(blocks)

    def pptx_table(self, table, locations):
        self.record_table(locations[id(table)])
        rows = table.findall(_A + "tr")
        _assert_owned_structure(table, rows, _A + "tr", _A + "tbl")
        raw_rows = [row.findall(_A + "tc") for row in rows]
        for row, cells in zip(rows, raw_rows):
            _assert_owned_structure(row, cells, _A + "tc", _A + "tbl")
        grid = table.find(_A + "tblGrid")
        width = _grid_width(grid, _A + "gridCol", len(raw_rows[0]) if raw_rows else 0)
        self.admit_table(len(rows), width)
        if any(len(row) != width for row in raw_rows):
            raise SourceTableError("PPTX row cells disagree with native column grid")
        props = table.find(_A + "tblPr")
        first_row_style = (_property_enabled(props, "firstRow")
            if props is not None and props.get("firstRow") is not None else None)
        cells, occupied = [], {}
        for row_index, raw_cells in enumerate(raw_rows):
            for column, raw_cell in enumerate(raw_cells):
                body = raw_cell.find(_A + "txBody")
                blocks = (() if body is None else tuple(self.text(native._pptx_paragraph_text(p), locations[id(p)])
                    for p in body.findall(_A + "p")))
                hmerge, vmerge = _merge_flag(raw_cell.get("hMerge")), _merge_flag(raw_cell.get("vMerge"))
                colspan = _span(raw_cell.get("gridSpan"), "PPTX column span")
                rowspan = _span(raw_cell.get("rowSpan"), "PPTX row span")
                if hmerge or vmerge:
                    if colspan != 1 or rowspan != 1:
                        raise SourceTableError("PPTX merge continuation declares another span")
                    owner = occupied.get((row_index, column))
                    if owner is None or any(block.text for block in blocks):
                        raise SourceTableError("PPTX merge continuation has no empty covered position")
                    anchor = cells[owner]
                    if hmerge != (column > anchor.column) or vmerge != (row_index > anchor.row):
                        raise SourceTableError("PPTX merge flags disagree with their anchor")
                    continue
                if (row_index, column) in occupied or row_index + rowspan > len(rows) or column + colspan > width:
                    raise SourceTableError("PPTX merge overlaps or escapes native table")
                cell = NativeCell(locations[id(raw_cell)], row_index, column, rowspan,
                    colspan, False, blocks)
                for r in range(row_index, row_index + rowspan):
                    for c in range(column, column + colspan):
                        if (r, c) in occupied:
                            raise SourceTableError("PPTX table has overlapping merge anchors")
                        occupied[r, c] = len(cells)
                cells.append(cell)
        location = locations[id(table)]
        # Drawing shape order/proximity is not a caption or semantic heading.
        return NativeTable(_table_id(self.digest, location), location, len(rows), width,
            tuple(cells), None, (), None, first_row_style)


def _associate_captions(blocks):
    """Use only unambiguous adjacent visible Caption-style paragraphs.

    A caption between two tables, or two captions competing for one table,
    remains ordinary source content with role=caption and no invented owner.
    Empty paragraphs and native wrapper boundaries must not be skipped.
    """
    candidates = {}
    for index, block in enumerate(blocks):
        if type(block) is not NativeText or block.role != "caption" or not block.text:
            continue
        neighbors = [j for j in (index - 1, index + 1) if 0 <= j < len(blocks)
            and type(blocks[j]) is NativeTable and blocks[j].caption is None
            and blocks[j].location.path[:-1] == block.location.path[:-1]
            and abs(blocks[j].location.path[-1] - block.location.path[-1]) == 1]
        if len(neighbors) == 1:
            candidates.setdefault(neighbors[0], []).append(index)
    owners = {table: captions[0] for table, captions in candidates.items() if len(captions) == 1}
    consumed = set(owners.values())
    return tuple(replace(block, caption=blocks[owners[index]]) if index in owners else block
        for index, block in enumerate(blocks) if index not in consumed)


def _presentation_parts(archive):
    """Honor the presentation's authoritative slide relationship order."""
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    root = native._parse_ooxml_xml(archive, "ppt/presentation.xml")
    rels = native._parse_ooxml_xml(archive, "ppt/_rels/presentation.xml.rels")
    by_id = {}
    for rel in rels:
        if rel.tag != rel_ns + "Relationship":
            raise SourceTableError("Invalid presentation relationship element")
        key = rel.get("Id")
        if not key or key in by_id:
            raise SourceTableError("Invalid duplicate presentation relationship")
        by_id[key] = rel
    slide_list = root.find(_P + "sldIdLst")
    if slide_list is None or not len(slide_list):
        raise SourceTableError("PPTX presentation has no declared slides")
    parts, ids = [], set()
    for entry in slide_list:
        if entry.tag != _P + "sldId" or not entry.get("id") or entry.get("id") in ids:
            raise SourceTableError("Invalid presentation slide identity")
        ids.add(entry.get("id"))
        rel = by_id.get(entry.get(rid))
        if rel is None or rel.get("TargetMode", "Internal") != "Internal" or rel.get("Type") != "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide":
            raise SourceTableError("Invalid or external presentation slide relationship")
        target = rel.get("Target", "")
        # Resolve package relationships as POSIX part names, never filesystem
        # paths. No conventional slide filename/ordinal is an authority signal.
        # Percent-encoded part names need a separate URI canonicalization policy.
        if not target or any(char in target for char in "\\%?#:") or target.startswith("//") or any(ord(char) < 33 for char in target):
            raise SourceTableError("Unsupported presentation slide target")
        part = posixpath.normpath(target[1:] if target.startswith("/") else posixpath.join("ppt", target))
        if not _valid_part(part):
            raise SourceTableError("Unsupported presentation slide target")
        if part in parts:
            raise SourceTableError("Duplicate presentation slide target")
        parts.append(part)
    return tuple(parts)


def _valid_part(part):
    return (type(part) is str and part.endswith(".xml") and not part.startswith("/")
        and not any(char in part for char in "\\%?#:")
        and all(segment not in {"", ".", ".."} for segment in part.split("/"))
        and not any(ord(char) < 33 for char in part))


def _extract_native_table_document(filename: str, raw: bytes) -> NativeTableDocument:
    """No files, converters, network, image rendering or LLM calls are used."""
    if not isinstance(raw, bytes) or not raw or len(raw) > native._OOXML_MAX_UNCOMPRESSED_BYTES:
        raise SourceTableError("Invalid original OOXML bytes")
    kind = Path(filename).suffix.lower().removeprefix(".")
    if kind not in {"docx", "pptx"}:
        raise SourceTableError("This native table adapter requires DOCX or PPTX")
    digest = sha256(raw).hexdigest()
    parser = _Parser(digest)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        native._validate_ooxml_archive(archive)
        if kind == "docx":
            part = "word/document.xml"
            source_parts = (part,)
            root = native._parse_ooxml_xml(archive, part)
            if any(element.tag in _DOCX_TRACKED_REVISION for element in root.iter()):
                raise SourceTableError("DOCX tracked revisions require an explicit revision-view policy")
            body = root.find(_W + "body")
            if body is None:
                raise SourceTableError("DOCX document body is missing")
            locations = _element_paths(root, part)
            blocks = parser.docx_blocks(body, locations)
            parser.check_coverage(root, locations, _W + "tbl")
        else:
            parts = _presentation_parts(archive)
            source_parts = parts
            blocks = []
            for part in parts:
                root = native._parse_ooxml_xml(archive, part)
                tree = root.find(f"./{_P}cSld/{_P}spTree")
                if tree is None:
                    raise SourceTableError("PPTX shape tree is missing")
                locations = _element_paths(root, part)
                blocks.extend(parser.pptx_blocks(tree, locations))
                parser.check_coverage(root, locations, _A + "tbl")
            blocks = tuple(blocks)
    result = NativeTableDocument(digest, kind, blocks, source_parts=source_parts)
    validate_native_document(result)
    result.canonical_json()
    return result


def extract_native_table_document(filename: str, raw: bytes) -> NativeTableDocument:
    """Versioned, stable failure boundary for pure original-byte extraction.

    This is not a complete-document extractor: headers/footers/notes, drawings,
    master/layout content, raster tables and other parts need separate adapters.
    The manifest declares the exact main-story/declared-slide table scope.
    DOCX tracked revisions fail until a separately specified revision view exists.
    """
    if type(filename) is not str or not filename:
        raise SourceTableError("Invalid native source filename")
    try:
        return _extract_native_table_document(filename, raw)
    except SourceTableError:
        raise
    except (zipfile.BadZipFile, ValueError, RuntimeError, KeyError, RecursionError, UnicodeError) as exc:
        raise SourceTableError("Invalid native source: " + str(exc)) from exc


def verify_native_source_binding(document: NativeTableDocument, *, filename: str, raw: bytes) -> None:
    """Exact retained-original replay for a commit/sidecar trust boundary.

    Shape validation alone cannot prove a dataclass's XML paths/cells occurred
    in an upload. A service must use its own authorized retained raw bytes here,
    never a caller-selected file or a self-declared sidecar source hash.
    """
    validate_native_document(document)
    actual = extract_native_table_document(filename, raw)
    if actual.canonical_json() != document.canonical_json():
        raise SourceTableError("Native table sidecar differs from the retained original")


def native_document_from_json(serialized: str) -> NativeTableDocument:
    """Decode only this version's canonical sidecar, never grant authenticity.

    Publication still requires verify_native_source_binding against the original
    loaded through the service's authorization and immutable artifact boundary.
    """
    if type(serialized) is not str or len(serialized.encode("utf-8")) > MAX_MANIFEST_BYTES:
        raise SourceTableError("Invalid native sidecar bytes")

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise SourceTableError("Duplicate native sidecar field")
            result[key] = value
        return result

    def fields(row, expected):
        if type(row) is not dict or set(row) != set(expected.split()):
            raise SourceTableError("Invalid native sidecar field set")

    def sequence(value):
        if type(value) is not list:
            raise SourceTableError("Invalid native sidecar array")
        return value

    def location(row):
        fields(row, "part path")
        return NativeLocation(row["part"], tuple(sequence(row["path"])))

    def text(row):
        fields(row, "location text role level")
        return NativeText(location(row["location"]), row["text"], row["role"], row["level"])

    def blocks(values, depth=0):
        if depth > MAX_DEPTH:
            raise SourceTableError("Native sidecar nesting exceeds its work budget")
        result = []
        for row in sequence(values):
            if type(row) is not dict:
                raise SourceTableError("Invalid native sidecar block")
            if "table_id" not in row:
                result.append(text(row))
                continue
            fields(row, "table_id location row_count column_count cells caption section_path accessibility_caption first_row_style")
            cells = []
            for cell in sequence(row["cells"]):
                fields(cell, "location row column rowspan colspan header blocks")
                cells.append(NativeCell(location(cell["location"]), cell["row"], cell["column"],
                    cell["rowspan"], cell["colspan"], cell["header"], blocks(cell["blocks"], depth + 1)))
            result.append(NativeTable(row["table_id"], location(row["location"]), row["row_count"],
                row["column_count"], tuple(cells), None if row["caption"] is None else text(row["caption"]),
                tuple(text(heading) for heading in sequence(row["section_path"])),
                None if row["accessibility_caption"] is None else text(row["accessibility_caption"]), row["first_row_style"]))
        return tuple(result)

    try:
        data = json.loads(serialized, object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(SourceTableError("Invalid JSON number")))
        fields(data, "source_sha256 format blocks version coverage source_parts")
        document = NativeTableDocument(data["source_sha256"], data["format"], blocks(data["blocks"]),
            data["version"], data["coverage"], tuple(sequence(data["source_parts"])))
        validate_native_document(document)
        if document.canonical_json() != serialized:
            raise SourceTableError("Native sidecar JSON is not canonical")
        return document
    except SourceTableError:
        raise
    except (TypeError, ValueError, RecursionError, UnicodeError) as exc:
        raise SourceTableError("Invalid native sidecar") from exc


def validate_native_document(document: NativeTableDocument) -> None:
    """Shape/work validation, not source authenticity or semantic validation."""
    if type(document) is not NativeTableDocument or document.version != NATIVE_TABLE_VERSION or type(document.format) is not str or document.format not in {"docx", "pptx"} or document.coverage != "main_story_or_declared_slide_native_tables":
        raise SourceTableError("Invalid native document version or type")
    if not isinstance(document.source_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", document.source_sha256):
        raise SourceTableError("Invalid original source identity")
    if (type(document.source_parts) is not tuple or not document.source_parts
            or any(not _valid_part(part) for part in document.source_parts)
            or len(set(document.source_parts)) != len(document.source_parts)
            or (document.format == "docx" and document.source_parts != ("word/document.xml",))):
        raise SourceTableError("Invalid declared native source parts")
    allowed_parts = set(document.source_parts)
    seen, work = set(), [0, 0, 0]

    def location(value):
        if type(value) is not NativeLocation or not _valid_part(value.part):
            raise SourceTableError("Invalid native XML part")
        if type(value.path) is not tuple or len(value.path) > MAX_DEPTH or any(type(i) is not int or i < 0 for i in value.path):
            raise SourceTableError("Invalid native XML location")
        if value.part not in allowed_parts:
            raise SourceTableError("Native location differs from the declared source parts or document format")

    def text(value):
        if type(value) is not NativeText or type(value.text) is not str or type(value.role) is not str or value.role not in {"paragraph", "heading", "caption", "accessibility_caption"}:
            raise SourceTableError("Invalid native text block")
        if type(value.level) is not int or (value.role == "heading" and not 1 <= value.level <= 6) or (value.role != "heading" and value.level != 0):
            raise SourceTableError("Invalid native heading level")
        location(value.location)
        work[2] += len(value.text.encode("utf-8"))
        if work[2] > MAX_TEXT_BYTES:
            raise SourceTableError("Native text exceeds its work budget")

    def blocks(values, depth):
        if type(values) is not tuple or depth > MAX_DEPTH:
            raise SourceTableError("Invalid native block container")
        for value in values:
            if type(value) is NativeText:
                text(value)
                continue
            if type(value) is not NativeTable:
                raise SourceTableError("Invalid native table block")
            location(value.location)
            if value.table_id != _table_id(document.source_sha256, value.location) or value.table_id in seen:
                raise SourceTableError("Duplicate or mismatched native table identity")
            seen.add(value.table_id)
            _integer(value.row_count, "row count")
            _integer(value.column_count, "column count")
            work[0] += 1
            work[1] += value.row_count * value.column_count
            if work[0] > MAX_TABLES or work[1] > MAX_GRID_CELLS:
                raise SourceTableError("Native table grid exceeds its work budget")
            if value.caption is not None:
                text(value.caption)
                if value.caption.role != "caption":
                    raise SourceTableError("Invalid source-authored caption")
            if value.accessibility_caption is not None:
                text(value.accessibility_caption)
                if value.accessibility_caption.role != "accessibility_caption":
                    raise SourceTableError("Invalid accessibility caption")
            if value.first_row_style is not None and (document.format != "pptx" or type(value.first_row_style) is not bool):
                raise SourceTableError("Invalid native first-row style metadata")
            if type(value.section_path) is not tuple:
                raise SourceTableError("Invalid native section path")
            for heading in value.section_path:
                text(heading)
                if heading.role != "heading":
                    raise SourceTableError("Invalid native section heading")
            occupied, previous = set(), (-1, -1)
            if type(value.cells) is not tuple:
                raise SourceTableError("Invalid native cells")
            for cell in value.cells:
                if type(cell) is not NativeCell or type(cell.header) is not bool:
                    raise SourceTableError("Invalid native cell")
                location(cell.location)
                for number in (cell.row, cell.column):
                    _integer(number, "cell position", zero=True)
                for number in (cell.rowspan, cell.colspan):
                    _integer(number, "cell span")
                if (cell.row, cell.column) <= previous or cell.row + cell.rowspan > value.row_count or cell.column + cell.colspan > value.column_count:
                    raise SourceTableError("Native cells are unordered or out of bounds")
                previous = (cell.row, cell.column)
                for r in range(cell.row, cell.row + cell.rowspan):
                    for c in range(cell.column, cell.column + cell.colspan):
                        if (r, c) in occupied:
                            raise SourceTableError("Native cell spans overlap")
                        occupied.add((r, c))
                blocks(cell.blocks, depth + 1)
            if len(occupied) != value.row_count * value.column_count:
                raise SourceTableError("Native table grid has uncovered cells")
    blocks(document.blocks, 0)


def render_native_table(table: NativeTable, *, document: NativeTableDocument) -> str:
    """Render a bound typed table, never raw/model HTML. No generated captions."""
    validate_native_document(document)
    found = []
    def collect(blocks):
        for block in blocks:
            if type(block) is NativeTable:
                found.append(block)
                for cell in block.cells:
                    collect(cell.blocks)
    collect(document.blocks)
    if not any(value is table for value in found):
        raise SourceTableError("Table does not belong to the validated document")
    value = _render(table)
    if len(value.encode("utf-8")) > MAX_RENDER_BYTES:
        raise SourceTableError("Native table rendering exceeds its work budget")
    return value


def render_native_tables(document: NativeTableDocument) -> tuple[tuple[str, str], ...]:
    """One validation and one source-order traversal for complete table output.

    Each top-level table gets one result. Nested tables occur only inside the
    containing cell's markup, not as duplicate standalone output. Ordinary
    prose/section placement is the responsibility of the successor assembler.
    """
    validate_native_document(document)
    result, total = [], 0
    for block in document.blocks:
        if type(block) is NativeTable:
            markup = _render(block)
            total += len(markup.encode("utf-8"))
            if total > MAX_RENDER_BYTES:
                raise SourceTableError("Native table rendering exceeds its work budget")
            result.append((block.table_id, markup))
    return tuple(result)


def _render(table):
    accessibility = ("" if table.accessibility_caption is None else
        ' aria-label="' + escape(table.accessibility_caption.text, quote=True) + '"')
    lines = ["<table" + accessibility + ">"]
    if table.caption is not None:
        lines.append("<caption>" + escape(table.caption.text).replace("\n", "<br>") + "</caption>")
    by_row = {}
    for cell in table.cells:
        by_row.setdefault(cell.row, []).append(cell)
    for row in range(table.row_count):
        style = (f' data-source-first-row-style="{str(table.first_row_style).lower()}"'
            if row == 0 and table.first_row_style is not None else "")
        lines.append("<tr" + style + ">")
        for cell in by_row.get(row, ()):
            tag = "th" if cell.header else "td"
            attrs = (f' rowspan="{cell.rowspan}"' if cell.rowspan > 1 else "")
            attrs += (f' colspan="{cell.colspan}"' if cell.colspan > 1 else "")
            content = "<br>".join(_render(block) if type(block) is NativeTable else
                escape(block.text).replace("\n", "<br>") for block in cell.blocks)
            lines.append(f"<{tag}{attrs}>{content}</{tag}>")
        lines.append("</tr>")
    lines.append("</table>")
    return "\n".join(lines)
