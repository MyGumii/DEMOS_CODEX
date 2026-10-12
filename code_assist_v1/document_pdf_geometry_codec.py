"""Strict saved-sidecar loading, with mandatory retained-PDF revalidation.

Not connected to storage or publication. Invoke inside the future isolated PDF
worker: byte/depth/work checks here are not OS memory or execution-time limits.
No metadata flag or caller-provided hash substitutes for re-extracting the PDF.
"""
from dataclasses import fields
from hashlib import sha256
import json
import math
from pathlib import PurePath

from . import document_pdf_geometry as geometry


def _reject(message):
    raise geometry.PdfGeometryError(message)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("Duplicate PDF sidecar JSON key")
        result[key] = value
    return result


def _integer(value):
    # Before conversion, bound unusually long JSON integer literals as well.
    if len(value) > 20:
        _reject("PDF sidecar integer is outside its wire range")
    result = int(value)
    if not -(2 ** 63) <= result < 2 ** 63:
        _reject("PDF sidecar integer is outside its wire range")
    return result


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        _reject("Non-finite PDF sidecar number")
    return result


def _depth(raw):
    # Check nesting before json.loads can recurse. Delimiters inside strings
    # (including escaped quotes/backslashes) have no structural significance.
    quoted = escaped = False
    depth = 0
    for byte in raw:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte == 34:
            quoted = True
        elif byte in (91, 123):
            depth += 1
            if depth > 12:
                _reject("PDF sidecar JSON is too deeply nested")
        elif byte in (93, 125):
            depth -= 1
    # Syntax, balanced delimiters and terminated strings are checked by JSON.


def _keys(value, cls):
    if type(value) is not dict or set(value) != {field.name for field in fields(cls)}:
        _reject("PDF sidecar fields do not match its versioned schema")
    return value


def _array(value, maximum):
    if type(value) is not list or len(value) > maximum:
        _reject("Invalid or oversized PDF sidecar array")
    return value


def _count(value):
    if type(value) is not int or value < 0:
        _reject("Invalid PDF sidecar count")
    return value


def _string(value):
    if type(value) is not str or "\x00" in value:
        _reject("Invalid PDF sidecar string")
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise geometry.PdfGeometryError("Invalid PDF sidecar Unicode") from exc
    return value


def _rectangle(value):
    return geometry._bbox(_array(value, 4))


def _decode(raw, limits):
    """Internal shape decoder only; must never serve as a trusted load API."""
    budget = geometry._Budget(limits)
    if type(raw) is not bytes or not 0 < len(raw) <= limits.max_manifest_bytes:
        _reject("Invalid or oversized saved PDF sidecar")
    _depth(raw)
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_object, parse_int=_integer, parse_float=_float,
            parse_constant=lambda _: _reject("Non-finite PDF sidecar constant"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, geometry.PdfGeometryError):
            raise
        raise geometry.PdfGeometryError("Invalid saved PDF sidecar JSON") from exc
    top = _keys(value, geometry.PdfGeometryDocument)
    pages = []
    for page in _array(top["pages"], limits.max_pages):
        page = _keys(page, geometry.PdfGeometryPage)
        glyph_count = _count(page["glyph_count"])
        budget.add("glyphs", glyph_count)
        ownership = 0

        def content(part):
            nonlocal ownership
            text = _string(part["text"])
            budget.add("text_bytes", len(text.encode("utf-8")))
            indices = _array(part["glyph_indices"], glyph_count - ownership)
            ownership += len(indices)
            return text, tuple(indices)

        blocks = []
        for block in _array(page["blocks"], 2 * limits.max_tables + 1):
            if type(block) is not dict:
                _reject("Invalid PDF sidecar block")
            cls = geometry.PdfTableBlock if "cells" in block else geometry.PdfTextBlock
            block = _keys(block, cls)
            identity = (_string(block["block_id"]), _count(block["page"]))
            if cls is geometry.PdfTextBlock:
                blocks.append(cls(*identity, *content(block)))
                continue
            budget.add("tables", 1)
            rows, columns = _count(block["rows"]), _count(block["columns"])
            cells = _array(block["cells"], limits.max_cells)
            if rows < 1 or columns < 1 or rows * columns != len(cells):
                _reject("Invalid PDF sidecar table dimensions")
            budget.add("cells", len(cells))
            decoded_cells = []
            for cell in cells:
                cell = _keys(cell, geometry.PdfCell)
                decoded_cells.append(geometry.PdfCell(_count(cell["row"]),
                    _count(cell["column"]), _rectangle(cell["bbox"]), *content(cell)))
            blocks.append(cls(*identity, _rectangle(block["bbox"]), rows, columns,
                              tuple(decoded_cells)))
        # Geometry validator checks number/coordinate/index types and bounds,
        # exact block IDs, total ownership, ordering and supported contract flags.
        pages.append(geometry.PdfGeometryPage(page["number"], page["width"],
            page["height"], glyph_count, _string(page["glyph_sha256"]),
            _count(page["image_count"]), tuple(blocks)))
    document = geometry.PdfGeometryDocument(**{**top, "pages": tuple(pages)})
    document.canonical_json(limits=limits)
    return document


def load_pdf_geometry(manifest: bytes, filename: str, retained_pdf: bytes, *,
                      limits=geometry.PdfGeometryLimits()):
    """Return re-extracted geometry only after strict decode and exact rebind.

    Requires retained original bytes; there is deliberately no trust-hash-only
    or skip-revalidation option. Neither this function nor rebind grants write
    authority or proves semantic reading order/caption ownership.
    """
    geometry._Budget(limits)
    if type(filename) is not str or PurePath(filename).suffix.lower() != ".pdf":
        _reject("PDF sidecar loading requires a PDF filename")
    if (type(retained_pdf) is not bytes or not retained_pdf
            or len(retained_pdf) > limits.max_source_bytes):
        _reject("Invalid or oversized retained PDF bytes")
    document = _decode(manifest, limits)
    if document.source_sha256 != sha256(retained_pdf).hexdigest():
        _reject("PDF sidecar does not match the retained original digest")
    return geometry.revalidate_pdf_geometry(document, filename, retained_pdf, limits=limits)
