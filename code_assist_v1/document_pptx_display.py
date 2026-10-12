"""Successor source-display planning for native PPTX, not v1 activation.

The old table descriptor stays byte-identical. This companion reads explicit
slide text properties from the same original; bold is emphasis, never evidence
of a heading hierarchy or a table-caption owner. Unknown/inherited formatting
is not guessed from font size, coordinates, text, or an evaluation fixture.

This pure module performs no I/O, model calls, storage or publication. A future
versioned authoring service must bind its result to retained original bytes and
persist source text only in purgeable bodies. Old v1 jobs cannot consume it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from html import escape
import io
import json
import re
import zipfile

from . import document_source_tables as tables


DISPLAY_VERSION = "pptx-source-display-v2"
DISPLAY_PLAN_VERSION = "pptx-source-display-slots-v2"
DISPLAY_COVERAGE = "declared_slide_sp_cxnsp_group_direct_paragraphs_and_native_cells_no_mce_or_style_inheritance"
MAX_BLOCKS = 16_384
MAX_DISPLAY_BYTES = 16 * 1024 * 1024
_A, _P = tables._A, tables._P
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


class PptxDisplayError(ValueError):
    """Unbound, malformed or oversized source display data."""


def _require(condition, message="PPTX display source differs."):
    if not condition:
        raise PptxDisplayError(message)


def _json(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    _require(len(text.encode()) <= MAX_DISPLAY_BYTES, "PPTX display data exceeds its budget.")
    return text


@dataclass(frozen=True, slots=True)
class DisplayParagraph:
    block_index: int
    location: tables.NativeLocation
    text: str
    placeholder_type: str | None
    # None means some nonempty run's bold value is unresolved. False means
    # at least one run is explicitly not bold; neither becomes source emphasis.
    explicit_bold: bool | None

    @property
    def role(self):
        if self.placeholder_type in {"title", "ctrTitle"}:
            return "slide_title"
        return "source_emphasis" if self.explicit_bold is True else "prose"


@dataclass(frozen=True, slots=True)
class PptxDisplaySource:
    source_sha256: str
    native_structure_sha256: str
    source_parts: tuple[str, ...]
    paragraphs: tuple[DisplayParagraph, ...]
    version: str = DISPLAY_VERSION
    coverage: str = DISPLAY_COVERAGE

    def canonical_json(self):
        return _json(asdict(self))


@dataclass(frozen=True, slots=True)
class PptxDisplaySlot:
    ordinal: int
    kind: str
    part: str
    block_indices: tuple[int, ...]
    text: str
    table_id: str | None = None


@dataclass(frozen=True, slots=True)
class PptxDisplayPlan:
    source_sha256: str
    native_structure_sha256: str
    display_sha256: str
    slots: tuple[PptxDisplaySlot, ...]
    version: str = DISPLAY_PLAN_VERSION

    def canonical_json(self):
        return _json(asdict(self))


def _bold(properties):
    if properties is None or "b" not in properties.attrib:
        return None
    value = properties.get("b")
    _require(value in {"0", "1", "true", "false", "on", "off"},
        "PPTX explicit bold property is invalid.")
    return value in {"1", "true", "on"}


def _only(parent, tag):
    nodes = [] if parent is None else parent.findall(tag)
    _require(len(nodes) <= 1, "PPTX has ambiguous duplicate source owners.")
    return None if not nodes else nodes[0]


def _paragraph_bold(paragraph):
    default = _bold(_only(_only(paragraph, _A + "pPr"), _A + "defRPr"))
    states, consumed = [], set()
    for child in paragraph:
        if child.tag not in {_A + "r", _A + "fld"}:
            continue
        declared = _bold(_only(child, _A + "rPr"))
        text = "".join(node.text or "" for node in child.findall(_A + "t"))
        if not text.strip():
            continue
        consumed.update(id(node) for node in child.findall(_A + "t") if (node.text or "").strip())
        states.append(default if declared is None else declared)
    visible = {id(node) for node in paragraph.iter(_A + "t") if (node.text or "").strip()}
    if not states or visible != consumed:
        return None
    if False in states:
        return False
    return True if all(value is True for value in states) else None


def _shape_paragraphs(tree, owned_bodies):
    # Match v1's supported source shapes, without traversing table cells or
    # layout/master/notes content and manufacturing a second owner for them.
    for child in tree:
        if child.tag in {_P + "sp", _P + "cxnSp"}:
            body = _only(child, _P + "txBody")
            if body is None:
                continue
            owned_bodies.add(id(body))
            tag = "nvSpPr" if child.tag == _P + "sp" else "nvCxnSpPr"
            ph = _only(_only(_only(child, _P + tag), _P + "nvPr"), _P + "ph")
            placeholder = None if ph is None else ph.get("type", "obj")
            if placeholder is not None:
                _require(bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", placeholder)),
                    "PPTX placeholder type is invalid.")
            for paragraph in body.findall(_A + "p"):
                yield paragraph, placeholder
        elif child.tag == _P + "grpSp":
            yield from _shape_paragraphs(child, owned_bodies)


def _audit_slide_text_owners(root, locations, native_tables, paragraphs, owned_bodies):
    """Audit the whole slide, not only the frozen parser's limited traversal.

    Table continuation cells may have empty text bodies not retained as native
    cell anchors; audit their XML owners too. This is textual coverage only:
    raster/chart/layout/notes interpretation remains outside this companion.
    """
    for table in root.iter(_A + "tbl"):
        _require(locations[id(table)] in native_tables, "PPTX table has no native owner.")
        for row in table.findall(_A + "tr"):
            for cell in row.findall(_A + "tc"):
                body = _only(cell, _A + "txBody")
                if body is not None:
                    owned_bodies.add(id(body))
                    paragraphs.extend(body.findall(_A + "p"))
    actual_bodies = {id(node) for node in root.iter()
        if node.tag in {_A + "txBody", _P + "txBody"}}
    _require(actual_bodies == owned_bodies, "PPTX contains unsupported text body owners.")
    _require({id(node) for node in root.iter(_A + "p")} == {id(node) for node in paragraphs},
        "PPTX contains unsupported paragraph owners.")
    controls = {_A + "t", _A + "br", _A + "tab"}
    _require({id(node) for node in root.iter() if node.tag in controls} ==
        {id(node) for paragraph in paragraphs for node in paragraph.iter() if node.tag in controls},
        "PPTX contains unsupported text owners.")


def extract_pptx_display_source(filename: str, raw: bytes):
    """Return the unchanged native descriptor and its source-bound companion.

    Direct run bold overrides paragraph default bold. Unknown inherited styles
    stay unknown. There are deliberately no font-size or caption-name rules.
    """
    try:
        return _extract_pptx_display_source(filename, raw)
    except tables.SourceTableError as exc:
        raise PptxDisplayError("Invalid PPTX native source: " + str(exc)) from exc


def _extract_pptx_display_source(filename, raw):
    document = tables.extract_native_table_document(filename, raw)
    _require(document.format == "pptx", "PPTX display extraction requires PPTX.")
    _require(len(document.blocks) <= MAX_BLOCKS, "PPTX display source has too many blocks.")
    expected = {block.location: (index, block) for index, block in enumerate(document.blocks)
        if type(block) is tables.NativeText}
    _require(len(expected) == sum(type(block) is tables.NativeText for block in document.blocks),
        "PPTX text locations are not unique.")
    found = {}
    native_tables = {block.location for block in document.blocks if type(block) is tables.NativeTable}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        # The v1 parser already validates the exact same immutable archive.
        for part in document.source_parts:
            root = tables.native._parse_ooxml_xml(archive, part)
            _require(not any(node.tag.startswith(_MC) or any(key.startswith(_MC) for key in node.attrib)
                for node in root.iter()),
                "PPTX markup compatibility requires an explicit selection policy.")
            locations = tables._element_paths(root, part)
            tree = _only(_only(root, _P + "cSld"), _P + "spTree")
            _require(tree is not None, "PPTX shape tree is missing.")
            owned_bodies, paragraphs = set(), []
            for paragraph, placeholder in _shape_paragraphs(tree, owned_bodies):
                paragraphs.append(paragraph)
                location = locations[id(paragraph)]
                _require(location in expected and location not in found,
                    "PPTX display text coverage differs from native extraction.")
                index, block = expected[location]
                # Use v1-normalized text, never a second incompatible text view.
                _require(tables.native._pptx_paragraph_text(paragraph) == block.text,
                    "PPTX display text differs from the native source block.")
                found[location] = DisplayParagraph(index, location, block.text,
                    placeholder, _paragraph_bold(paragraph))
            _audit_slide_text_owners(root, locations, native_tables, paragraphs, owned_bodies)
    _require(set(found) == set(expected), "PPTX display source omits native text.")
    result = PptxDisplaySource(document.source_sha256,
        sha256(document.canonical_json().encode()).hexdigest(), document.source_parts,
        tuple(sorted(found.values(), key=lambda item: item.block_index)))
    validate_pptx_display_source(document, result)
    result.canonical_json()
    return document, result


def validate_pptx_display_source(document, source):
    """Exact shape/coverage binding, not proof of source formatting authenticity."""
    try:
        tables.validate_native_document(document)
    except tables.SourceTableError as exc:
        raise PptxDisplayError("Invalid PPTX native source: " + str(exc)) from exc
    _require(document.format == "pptx" and type(source) is PptxDisplaySource
        and source.version == DISPLAY_VERSION and source.coverage == DISPLAY_COVERAGE
        and source.source_sha256 == document.source_sha256
        and source.native_structure_sha256 == sha256(document.canonical_json().encode()).hexdigest()
        and type(source.source_parts) is tuple and source.source_parts == document.source_parts
        and type(source.paragraphs) is tuple and len(document.blocks) <= MAX_BLOCKS,
        "PPTX display source identity differs.")
    expected = [(index, block) for index, block in enumerate(document.blocks)
        if type(block) is tables.NativeText]
    _require(len(expected) == len(source.paragraphs), "PPTX display source text coverage differs.")
    for (index, block), item in zip(expected, source.paragraphs, strict=True):
        _require(type(item) is DisplayParagraph and type(item.block_index) is int
            and item.block_index == index and type(item.location) is tables.NativeLocation
            and type(item.location.part) is str and type(item.location.path) is tuple
            and all(type(value) is int for value in item.location.path)
            and item.location == block.location and type(item.text) is str and item.text == block.text
            and (item.explicit_bold is None or type(item.explicit_bold) is bool)
            and (item.placeholder_type is None or (type(item.placeholder_type) is str
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,63}", item.placeholder_type))),
            "PPTX display paragraph is invalid or unbound.")
    source.canonical_json()


def verify_pptx_display_binding(document, source, *, filename, raw):
    """Service-owned retained-original replay, required before trusting labels."""
    validate_pptx_display_source(document, source)
    actual_document, actual_source = extract_pptx_display_source(filename, raw)
    _require(document.canonical_json() == actual_document.canonical_json()
        and source.canonical_json() == actual_source.canonical_json(),
        "PPTX display source differs from the retained original.")


def pptx_display_source_from_json(document, serialized):
    """Strict purgeable-body decoder; decoding is never original-byte authority."""
    _require(type(serialized) is str, "PPTX display body must be canonical JSON.")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "PPTX display JSON has duplicate fields.")
            result[key] = value
        return result

    def shape(value, keys):
        _require(type(value) is dict and set(value) == set(keys), "PPTX display JSON shape differs.")
        return value

    try:
        _require(len(serialized.encode()) <= MAX_DISPLAY_BYTES, "PPTX display JSON exceeds its budget.")
        row = shape(json.loads(serialized, object_pairs_hook=pairs), (
            "source_sha256", "native_structure_sha256", "source_parts", "paragraphs", "version", "coverage"))
        _require(type(row["paragraphs"]) is list and len(row["paragraphs"]) <= MAX_BLOCKS
            and type(row["source_parts"]) is list, "PPTX display arrays differ.")
        paragraphs = []
        for value in row["paragraphs"]:
            item = shape(value, ("block_index", "location", "text", "placeholder_type", "explicit_bold"))
            location = shape(item["location"], ("part", "path"))
            _require(type(location["path"]) is list, "PPTX display location differs.")
            paragraphs.append(DisplayParagraph(item["block_index"],
                tables.NativeLocation(location["part"], tuple(location["path"])),
                item["text"], item["placeholder_type"], item["explicit_bold"]))
        source = PptxDisplaySource(row["source_sha256"], row["native_structure_sha256"],
            tuple(row["source_parts"]), tuple(paragraphs), row["version"], row["coverage"])
        validate_pptx_display_source(document, source)
        _require(source.canonical_json() == serialized, "PPTX display JSON is not canonical.")
        return source
    except (TypeError, ValueError, RecursionError) as exc:
        if isinstance(exc, PptxDisplayError):
            raise
        raise PptxDisplayError("Invalid PPTX display JSON.") from exc


def plan_pptx_display_slots(document, source):
    """Preserve labels at their original slot; table ownership remains unchanged.

    This is a successor plan, NOT a NativeWikiPlan/v1 receipt. Slide boundaries
    are system navigation metadata, not source titles. Prose slots still require
    versioned model authoring/receipts before publication; they are not raw Wiki.
    """
    validate_pptx_display_source(document, source)
    by_index = {item.block_index: item for item in source.paragraphs}
    rendered = dict(tables.render_native_tables(document))
    blocks_by_part = {part: [] for part in document.source_parts}
    for index, block in enumerate(document.blocks):
        blocks_by_part[block.location.part].append((index, block))
    slots, pending = [], []
    current_part = None

    def add(kind, indices, text, table_id=None):
        slots.append(PptxDisplaySlot(len(slots) + 1, kind, current_part,
            tuple(indices), text, table_id))

    def flush():
        if pending:
            add("prose", [index for index, _ in pending],
                "\n\n".join(block.text for _, block in pending))
            pending.clear()

    for ordinal, current_part in enumerate(document.source_parts, 1):
        # Every declared slide, even an empty/image-only one, has a boundary.
        # Navigation is not a claim that raster/chart content has been read.
        add("slide_boundary", (), str(ordinal))
        for index, block in blocks_by_part[current_part]:
            if type(block) is tables.NativeTable:
                flush()
                add("table", (index,), rendered[block.table_id], block.table_id)
            elif by_index[index].role != "prose" and block.text.strip():
                flush()
                add(by_index[index].role, (index,), block.text)
            else:
                pending.append((index, block))
        flush()
    _require([index for slot in slots for index in slot.block_indices] == list(range(len(document.blocks))),
        "PPTX display plan duplicates or omits source blocks.")
    plan = PptxDisplayPlan(document.source_sha256, source.native_structure_sha256,
        sha256(source.canonical_json().encode()).hexdigest(), tuple(slots))
    plan.canonical_json()
    return plan


def render_pptx_source_display(slot):
    """Server-owned source display only, not an authoring completion API."""
    _require(type(slot) is PptxDisplaySlot and type(slot.text) is str,
        "Invalid PPTX display slot.")
    _require(len(slot.text.encode()) <= MAX_DISPLAY_BYTES, "PPTX rendered display exceeds its budget.")
    if slot.kind == "slide_boundary":
        _require(re.fullmatch(r"[1-9][0-9]*", slot.text) is not None)
        result = "## 원문 슬라이드 " + slot.text
        _require(len(result.encode()) <= MAX_DISPLAY_BYTES, "PPTX rendered display exceeds its budget.")
        return result
    _require(slot.kind in {"slide_title", "source_emphasis"},
        "Only source label slots can use the source-display renderer.")
    tag = "h3" if slot.kind == "slide_title" else "p"
    # Admit exact escaped UTF-8 size before allocating an expanded string.
    size = len(f"<{tag}><strong></strong></{tag}>".encode()) + len(slot.text.encode())
    size += sum(slot.text.count(char) * extra for char, extra in
        (("&", 4), ("<", 3), (">", 3), ('"', 5), ("'", 5), ("\n", 3)))
    _require(size <= MAX_DISPLAY_BYTES, "PPTX rendered display exceeds its budget.")
    text = escape(slot.text, quote=True).replace("\n", "<br>")
    return f"<{tag}><strong>{text}</strong></{tag}>"
