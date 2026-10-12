"""Original-bound PPTX layout observations for successor one-stage authoring.

The frozen native/display descriptors and their renderers remain unchanged.
This companion supplies observations, NOT heading or caption decisions. Direct
run properties override paragraph defaults; layout/master/theme inheritance and
rendered text geometry are deliberately unresolved. Group coordinates retain
their frame chain rather than masquerading as slide coordinates.

No model call, storage, publication, or inferred fact/heading ownership occurs
here. A future registered authoring policy must bind this context to the retained
original and include role selection in its writing calls, not a review pass.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import io
import json
import re
import zipfile

from . import document_pptx_display as display
from . import document_source_tables as tables


ROLE_CONTEXT_VERSION = "pptx-source-role-context-v1"
ROLE_PACKET_VERSION = "pptx-source-role-part-packet-v1"
ROLE_CONTEXT_COVERAGE = "direct_shapes_groups_run_and_paragraph_defaults_not_rendered_or_inherited_styles"
MAX_CONTEXT_BYTES = 16 * 1024 * 1024
MAX_SHAPES = 32_768
MAX_RUNS = 65_536
_A, _P = tables._A, tables._P


class PptxRoleContextError(ValueError):
    """Unsupported, malformed, oversized or unbound role context."""


def _require(ok, message):
    if not ok:
        raise PptxRoleContextError(message)


def _json(value):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False)
    _require(len(text.encode()) <= MAX_CONTEXT_BYTES, "PPTX role context exceeds its byte budget.")
    return text


def _only(parent, tag):
    nodes = [] if parent is None else parent.findall(tag)
    _require(len(nodes) <= 1, "PPTX role context has duplicate property owners.")
    return nodes[0] if nodes else None


def _int(node, name, *, minimum=-(2**63), maximum=2**63 - 1):
    if node is None or name not in node.attrib:
        return None
    value = node.attrib[name]
    _require(bool(re.fullmatch(r"[+-]?[0-9]{1,20}", value)), "Invalid PPTX integer property.")
    result = int(value)
    _require(minimum <= result <= maximum, "PPTX integer property is out of range.")
    return result


def _bool(node, name):
    if node is None or name not in node.attrib:
        return None
    value = node.attrib[name]
    _require(value in {"0", "1", "true", "false", "on", "off"}, "Invalid PPTX boolean property.")
    return value in {"1", "true", "on"}


@dataclass(frozen=True, slots=True)
class DeclaredTransform:
    # Coordinates are EMU in the immediate parent frame. None is undeclared,
    # not zero. Rotation is the original OOXML unit (1/60000 degree).
    x: int | None
    y: int | None
    cx: int | None
    cy: int | None
    child_x: int | None
    child_y: int | None
    child_cx: int | None
    child_cy: int | None
    rotation: int | None
    flip_h: bool | None
    flip_v: bool | None


@dataclass(frozen=True, slots=True)
class RoleShape:
    location: tables.NativeLocation
    kind: str
    group_chain: tuple[tables.NativeLocation, ...]
    transform: DeclaredTransform | None
    block_indices: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class RoleRun:
    location: tables.NativeLocation
    kind: str
    # Raw run text is separate from the native-normalized paragraph domain.
    text: str
    font_size_centipoints: int | None
    font_size_origin: str
    bold: bool | None
    bold_origin: str


@dataclass(frozen=True, slots=True)
class RoleParagraph:
    block_index: int
    location: tables.NativeLocation
    shape_location: tables.NativeLocation
    text: str
    explicit_placeholder: str | None
    paragraph_level: int | None
    runs: tuple[RoleRun, ...]
    direct_text_run_coverage_complete: bool


@dataclass(frozen=True, slots=True)
class PptxRoleContext:
    source_sha256: str
    native_structure_sha256: str
    display_sha256: str
    source_parts: tuple[str, ...]
    slide_cx: int | None
    slide_cy: int | None
    shapes: tuple[RoleShape, ...]
    paragraphs: tuple[RoleParagraph, ...]
    version: str = ROLE_CONTEXT_VERSION
    coverage: str = ROLE_CONTEXT_COVERAGE

    def canonical_json(self):
        return _json(asdict(self))


@dataclass(frozen=True, slots=True)
class PptxRoleCandidate:
    candidate_id: str
    block_index: int
    location: tables.NativeLocation
    shape_location: tables.NativeLocation
    text_sha256: str


@dataclass(frozen=True, slots=True)
class PptxRolePartPacket:
    source_part_ordinal: int
    part: str
    candidate_ids: tuple[str, ...]
    payload_json: str

    @property
    def sha256(self):
        return sha256(self.payload_json.encode()).hexdigest()


def pptx_role_candidates(context):
    """Every nonempty native text owner, never a bold/size/keyword shortlist.

    This pure selection function grants no source or publication authority.
    The caller must have verified the context against retained original bytes.
    Same text at different source locations remains separate. Candidate IDs
    include the whole context hash, so a layout change invalidates old roles.
    """
    _require(type(context) is PptxRoleContext, "PPTX role context type differs.")
    digest = sha256(context.canonical_json().encode()).hexdigest()
    candidates = []
    for paragraph in context.paragraphs:
        if not paragraph.text.strip():
            continue
        text_sha256 = sha256(paragraph.text.encode()).hexdigest()
        identity = _json([ROLE_CONTEXT_VERSION, digest, paragraph.block_index,
            asdict(paragraph.location), asdict(paragraph.shape_location), text_sha256])
        candidates.append(PptxRoleCandidate("PR_" + sha256(identity.encode()).hexdigest(),
            paragraph.block_index, paragraph.location, paragraph.shape_location, text_sha256))
    return tuple(candidates)


def pptx_role_part_packets(context, *, byte_budget=65_536):
    """Complete same-slide observations with exactly-one candidate ownership.

    No prose-presence filter: title-only/table+label slides still have a packet.
    These are source preparation packets, not model calls or validated roles.
    The successor author must include all packets under full prompt/output/work
    budgets and either combine them with writing or schedule necessary writing
    work. This function cannot claim that current prose batches cover them.
    """
    _require(type(byte_budget) is int and 1 <= byte_budget <= MAX_CONTEXT_BYTES,
        "Invalid PPTX role packet byte budget.")
    candidates = pptx_role_candidates(context)
    digest = sha256(context.canonical_json().encode()).hexdigest()
    by_part = {part: {"candidates": [], "shapes": [], "paragraphs": []} for part in context.source_parts}
    for candidate in candidates:
        by_part[candidate.location.part]["candidates"].append(candidate)
    for shape in context.shapes:
        by_part[shape.location.part]["shapes"].append(shape)
    for paragraph in context.paragraphs:
        by_part[paragraph.location.part]["paragraphs"].append(paragraph)
    packets = []
    for ordinal, part in enumerate(context.source_parts, 1):
        owned = by_part[part]
        if not owned["candidates"]:
            continue
        payload = _json({"version": ROLE_PACKET_VERSION,
            "role_context_version": context.version, "role_context_sha256": digest,
            "source_sha256": context.source_sha256, "coverage": context.coverage,
            "native_structure_sha256": context.native_structure_sha256,
            "display_sha256": context.display_sha256, "source_part": part,
            "source_part_ordinal": ordinal, "source_part_count": len(context.source_parts),
            "slide_size_emu": [context.slide_cx, context.slide_cy],
            "coordinate_domain": "immediate_parent_frame_with_ordered_group_chain",
            "role_assignment": "not_performed", "table_cells_supplied": False,
            "visual_content_interpreted": False,
            **{key: [asdict(value) for value in owned[key]] for key in owned}})
        _require(len(payload.encode()) <= byte_budget,
            "Complete PPTX role part packet exceeds its byte budget; no truncated context.")
        packets.append(PptxRolePartPacket(ordinal, part,
            tuple(c.candidate_id for c in owned["candidates"]), payload))
    _require(tuple(c.candidate_id for c in candidates) ==
        tuple(candidate_id for packet in packets for candidate_id in packet.candidate_ids),
        "PPTX role part packets omit, duplicate or reorder candidates.")
    return tuple(packets)


def _transform(shape):
    if shape.tag == _P + "grpSp":
        parent, tag = _only(shape, _P + "grpSpPr"), _A + "xfrm"
    elif shape.tag == _P + "graphicFrame":
        parent, tag = shape, _P + "xfrm"
    else:
        parent, tag = _only(shape, _P + "spPr"), _A + "xfrm"
    node = _only(parent, tag)
    if node is None:
        return None
    off, ext = _only(node, _A + "off"), _only(node, _A + "ext")
    child_off, child_ext = _only(node, _A + "chOff"), _only(node, _A + "chExt")
    return DeclaredTransform(_int(off, "x"), _int(off, "y"),
        _int(ext, "cx", minimum=0), _int(ext, "cy", minimum=0),
        _int(child_off, "x"), _int(child_off, "y"),
        _int(child_ext, "cx", minimum=0), _int(child_ext, "cy", minimum=0),
        _int(node, "rot", minimum=-(2**31), maximum=2**31 - 1),
        _bool(node, "flipH"), _bool(node, "flipV"))


def _run_property(run, default, name, parser):
    value = parser(run, name)
    if value is not None:
        return value, "run"
    value = parser(default, name)
    return (value, "paragraph_default") if value is not None else (None, "unresolved")


def _runs(paragraph, paths, remaining):
    default = _only(_only(paragraph, _A + "pPr"), _A + "defRPr")
    rows, consumed = [], set()
    for child in paragraph:
        if child.tag not in {_A + "r", _A + "fld"}:
            continue
        _require(len(rows) < remaining, "PPTX role context has too many text runs.")
        properties = _only(child, _A + "rPr")
        size, size_origin = _run_property(properties, default, "sz",
            lambda node, name: _int(node, name, minimum=100, maximum=400_000))
        bold, bold_origin = _run_property(properties, default, "b", _bool)
        nodes = child.findall(_A + "t")
        consumed.update(id(node) for node in nodes)
        rows.append(RoleRun(paths[id(child)], child.tag.removeprefix(_A),
            "".join(node.text or "" for node in nodes), size, size_origin, bold, bold_origin))
    # Unknown wrappers keep their native paragraph text but are not mislabeled
    # as known-font text. Tabs/breaks have no font-bearing text of their own.
    complete = consumed == {id(node) for node in paragraph.iter(_A + "t")}
    return tuple(rows), complete


def extract_pptx_role_context(filename: str, raw: bytes):
    """Return unchanged native/display descriptors plus layout observations.

    Raw PPTX is the only input. Evaluation text, guessed roles or a caller's
    rewritten display companion cannot influence these observations.
    """
    try:
        return _extract(filename, raw)
    except (display.PptxDisplayError, tables.SourceTableError) as exc:
        raise PptxRoleContextError("Invalid PPTX role-context source: " + str(exc)) from exc


def _extract(filename, raw):
    document, companion = display.extract_pptx_display_source(filename, raw)
    by_location = {item.location: item for item in companion.paragraphs}
    tables_by_location = {block.location: index for index, block in enumerate(document.blocks)
        if type(block) is tables.NativeTable}
    shapes, paragraphs, run_count = [], [], 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        presentation = tables.native._parse_ooxml_xml(archive, "ppt/presentation.xml")
        dimensions = _only(presentation, _P + "sldSz")
        slide_cx = _int(dimensions, "cx", minimum=1)
        slide_cy = _int(dimensions, "cy", minimum=1)
        for part in document.source_parts:
            root = tables.native._parse_ooxml_xml(archive, part)
            paths = tables._element_paths(root, part)
            tree = _only(_only(root, _P + "cSld"), _P + "spTree")

            def visit(parent, groups):
                nonlocal run_count
                for shape in parent:
                    if shape.tag in {_P + "nvGrpSpPr", _P + "grpSpPr", _P + "extLst"}:
                        continue
                    _require(len(shapes) < MAX_SHAPES, "PPTX role context has too many shapes.")
                    location = paths[id(shape)]
                    indices = []
                    if shape.tag in {_P + "sp", _P + "cxnSp"}:
                        body = _only(shape, _P + "txBody")
                        for paragraph in [] if body is None else body.findall(_A + "p"):
                            source = by_location[paths[id(paragraph)]]
                            runs, complete = _runs(paragraph, paths, MAX_RUNS - run_count)
                            run_count += len(runs)
                            indices.append(source.block_index)
                            paragraphs.append(RoleParagraph(source.block_index, source.location,
                                location, source.text, source.placeholder_type,
                                _int(_only(paragraph, _A + "pPr"), "lvl", minimum=0, maximum=8),
                                runs, complete))
                    elif shape.tag == _P + "graphicFrame":
                        indices.extend(tables_by_location[paths[id(table)]] for table in shape.iter(_A + "tbl"))
                    kind = shape.tag.removeprefix(_P)
                    shapes.append(RoleShape(location, kind, groups, _transform(shape), tuple(indices)))
                    if shape.tag == _P + "grpSp":
                        visit(shape, (*groups, location))

            visit(tree, ())
    _require([p.block_index for p in paragraphs] == [p.block_index for p in companion.paragraphs],
        "PPTX role context has incomplete or reordered paragraph coverage.")
    _require([i for shape in shapes for i in shape.block_indices] == list(range(len(document.blocks))),
        "PPTX role context has incomplete or reordered native block coverage.")
    context = PptxRoleContext(document.source_sha256, companion.native_structure_sha256,
        sha256(companion.canonical_json().encode()).hexdigest(), document.source_parts,
        slide_cx, slide_cy, tuple(shapes), tuple(paragraphs))
    context.canonical_json()
    return document, companion, context


def verify_pptx_role_context_binding(document, companion, context, *, filename, raw):
    """Re-extract retained bytes, not merely compare caller-supplied digests."""
    _require(type(context) is PptxRoleContext, "PPTX role context type differs.")
    actual_document, actual_companion, actual_context = extract_pptx_role_context(filename, raw)
    _require(type(document) is type(actual_document) and type(companion) is type(actual_companion),
        "PPTX role context descriptor type differs.")
    _require(document.canonical_json() == actual_document.canonical_json()
        and companion.canonical_json() == actual_companion.canonical_json()
        and context.canonical_json() == actual_context.canonical_json(),
        "PPTX role context differs from retained original bytes.")


def pptx_role_context_from_json(serialized, *, filename, raw):
    """Decode only by reproducing the original, not by trusting saved fields.

    Unlike a metadata-only deserializer this intentionally requires the retained
    original. Exact canonical bytes reject duplicate/unknown keys and numeric
    aliases, even when Python equality would equate True/1 or 1/1.0.
    """
    _require(type(serialized) is str and len(serialized.encode()) <= MAX_CONTEXT_BYTES,
        "PPTX role context JSON is missing or exceeds its byte budget.")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "PPTX role context JSON has duplicate fields.")
            result[key] = value
        return result

    def reject_constant(value):
        raise PptxRoleContextError("PPTX role context JSON has a non-finite number.")

    try:
        decoded = json.loads(serialized, object_pairs_hook=pairs, parse_constant=reject_constant)
        _require(_json(decoded) == serialized, "PPTX role context JSON is not canonical.")
        document, companion, context = extract_pptx_role_context(filename, raw)
        _require(context.canonical_json() == serialized,
            "PPTX role context JSON differs from retained original bytes.")
        return document, companion, context
    except (ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, PptxRoleContextError):
            raise
        raise PptxRoleContextError("Invalid PPTX role context JSON.") from exc
