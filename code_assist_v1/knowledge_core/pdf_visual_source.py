"""Isolated geometry + complete page rasters, not canonical read authority.

Each renderer output is derived from the retained original, never a hash-only
caller image. Existing v1 geometry/source policy is unchanged. The two bounded
children run sequentially outside transactions; each has its own deadline.
"""
import base64
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
import json
import math
from pathlib import Path
import secrets
import struct

from ..document_pdf_process import _run_worker, PdfProcessPolicy, PdfProcessError
from ..document_pdf_geometry import PdfGeometryLimits
from ..llm_images import ModelImageInput, ModelImagePolicy, validate_image_inputs
from .pdf_geometry_authoring import PreparedPdfSource, prepare_pdf_source, _document
from .wiki_authoring import AuthoringSourceError, _json, _hash

PDF_VISUAL_SOURCE_VERSION = "pdf-original-page-visual-source-v1"
_KEY = secrets.token_bytes(32)


def renderer_identity():
    """Pinned static appearance, not all hidden layers/attachments/form states."""
    return {"pdfium_version": "153.0.7999.0", "png_encoder": "Pillow",
        "png_encoder_version": "12.3.0", "png_compress_level": 6, "png_optimize": False,
        "rotation": 0, "crop": [0, 0, 0, 0], "fill_color": [255, 255, 255, 255],
        "grayscale": False, "rev_byteorder": True, "draw_annots": True, "may_draw_forms": True,
        "coverage": "static-current-page-appearance", "scale": "dpi/72-canvas-units"}


@dataclass(frozen=True, slots=True)
class PdfPageRenderPolicy:
    dpi: int = 144
    maximum_page_pixels: int = 16_000_000
    maximum_total_pixels: int = 256_000_000
    maximum_page_bytes: int = 16_777_216
    maximum_total_bytes: int = 67_108_864

    def __post_init__(self):
        ceiling = (288, 16_000_000, 256_000_000, 16_777_216, 67_108_864)
        if (any(type(v) is not int or not 1 <= v <= maximum for v, maximum in zip(asdict(self).values(), ceiling))
                or self.dpi < 72):
            raise AuthoringSourceError("Invalid PDF page render policy.")

    def image_policy(self, count):
        return ModelImagePolicy(count, self.maximum_page_bytes, self.maximum_total_bytes,
                                self.maximum_page_pixels, self.maximum_total_pixels)


@dataclass(frozen=True, slots=True)
class PdfRenderedPage:
    page: int
    width: int
    height: int
    image: ModelImageInput = field(repr=False)

    def identity(self):
        return {"page": self.page, "width": self.width, "height": self.height,
                "source_id": self.image.source_id, "sha256": sha256(self.image.data).hexdigest(),
                "bytes": len(self.image.data), "media_type": self.image.media_type}


@dataclass(frozen=True, slots=True)
class PreparedPdfVisualSource:
    geometry: PreparedPdfSource
    pages: tuple[PdfRenderedPage, ...] = field(repr=False)
    render_policy: PdfPageRenderPolicy
    seal: str = field(repr=False)

    def identity(self):
        return {"version": PDF_VISUAL_SOURCE_VERSION, "geometry": self.geometry.identity(),
                "renderer": "pypdfium2", "renderer_version": "5.13.0",
                "render_identity": renderer_identity(),
                "render_policy": asdict(self.render_policy), "pages": [page.identity() for page in self.pages]}


def _seal(source):
    return hmac.digest(_KEY, _json(source.identity()).encode(), "sha256").hex()


def checked_visual_source(source):
    if (type(source) is not PreparedPdfVisualSource or type(source.render_policy) is not PdfPageRenderPolicy
            or type(source.pages) is not tuple or not source.pages or type(source.seal) is not str):
        raise AuthoringSourceError("A process-local PDF visual source is required.")
    doc = _document(source.geometry)
    if (len(source.pages) != len(doc.pages)
            or any(type(p) is not PdfRenderedPage or p.page != ordinal for ordinal, p in enumerate(source.pages, 1))
            or not hmac.compare_digest(source.seal, _seal(source))):
        raise AuthoringSourceError("PDF visual source changed.")
    metadata = validate_image_inputs(tuple(p.image for p in source.pages), source.render_policy.image_policy(len(source.pages)))
    if any((p.width, p.height) != (row["width"], row["height"]) for p, row in zip(source.pages, metadata)):
        raise AuthoringSourceError("PDF page pixels differ from their source.")
    scale = source.render_policy.dpi / 72
    if any((p.width, p.height) != (math.ceil(g.width*scale), math.ceil(g.height*scale))
            for p, g in zip(source.pages, doc.pages)):
        raise AuthoringSourceError("PDF geometry and raster canvases differ.")
    return doc


def prepare_pdf_visual_source(snapshot_id, filename, raw, *, python_executable,
        geometry_limits=PdfGeometryLimits(), render_policy=PdfPageRenderPolicy(),
        process_policy=PdfProcessPolicy(), cancel_event=None, allow_visual_only=False,
        allow_ambiguous_grid=False):
    if type(render_policy) is not PdfPageRenderPolicy:
        raise AuthoringSourceError("A page render policy is required.")
    source = prepare_pdf_source(snapshot_id, filename, raw, python_executable=python_executable,
        limits=geometry_limits, process_policy=process_policy, cancel_event=cancel_event,
        allow_visual_only=allow_visual_only, allow_ambiguous_grid=allow_ambiguous_grid)
    doc = _document(source)
    if allow_ambiguous_grid:
        # Explicit fresh ordinary route only. A PDF slide can use 1920x1080
        # points: rendering it at 144dpi would quadruple its native canvas.
        # Choose a bounded whole-page DPI before allocating any PNG. Every
        # page survives; original bytes/glyphs are never resized or discarded.
        render_policy = fitted_page_render_policy(doc,render_policy)
    header = _json({"version": 1, "raw_bytes": len(raw), "raw_sha256": source.raw_sha256,
                    "pages": len(doc.pages), "policy": asdict(render_policy)}).encode()
    wire, pid, _elapsed, _metrics = _run_worker(Path(python_executable),
        Path(__file__).resolve().parents[1] / "_document_pdf_visual_worker.py",
        (struct.pack("!I", len(header)), header, raw), policy=process_policy,
        output_bytes=4 * ((render_policy.maximum_total_bytes + 2) // 3) + 1_048_576,
        cancel_event=cancel_event)
    try:
        row = json.loads(wire)
        if (type(row) is not dict or set(row) != {"version", "raw_sha256", "renderer", "renderer_version", "render_identity", "policy", "pages"}
                or type(row["version"]) is not int or row["version"] != 1
                or row["raw_sha256"] != source.raw_sha256 or row["renderer"] != "pypdfium2"
                or row["renderer_version"] != "5.13.0" or row["policy"] != asdict(render_policy)
                or _json(row["render_identity"]) != _json(renderer_identity())
                or type(row["pages"]) is not list or len(row["pages"]) != len(doc.pages)):
            raise ValueError("Invalid visual worker identity")
        pages = []
        for number, item in enumerate(row["pages"], 1):
            if (type(item) is not dict or set(item) != {"page", "width", "height", "sha256", "png"}
                    or type(item["page"]) is not int or item["page"] != number
                    or any(type(item[k]) is not int or item[k] <= 0 for k in ("width", "height"))
                    or type(item["png"]) is not str
                    or len(item["png"]) > 4 * ((render_policy.maximum_page_bytes + 2) // 3)):
                raise ValueError("Invalid visual page record")
            data = base64.b64decode(item["png"], validate=True)
            if sha256(data).hexdigest() != item["sha256"] or not data.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Invalid page image bytes")
            image_id = "PV_" + _hash(_json([source.raw_sha256, number, item["sha256"], asdict(render_policy)]))
            pages.append(PdfRenderedPage(number, item["width"], item["height"], ModelImageInput(image_id, "image/png", data)))
        result = PreparedPdfVisualSource(source, tuple(pages), render_policy, "")
        result = replace(result, seal=_seal(result))
        checked_visual_source(result)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        raise PdfProcessError("VISUAL_PROTOCOL_ERROR", pid=pid) from exc
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError("CANCELLED", pid=pid)
    return result


def fitted_page_render_policy(document, configured):
    """Deterministic complete-document canvas admission, no prefix rendering."""
    if type(configured) is not PdfPageRenderPolicy:
        raise AuthoringSourceError("A configured PDF render policy is required.")
    for dpi in range(configured.dpi,71,-1):
        scale=dpi/72
        pixels=[math.ceil(page.width*scale)*math.ceil(page.height*scale)
            for page in document.pages]
        if max(pixels)<=configured.maximum_page_pixels and sum(pixels)<=configured.maximum_total_pixels:
            return replace(configured,dpi=dpi)
    raise AuthoringSourceError("Complete PDF page canvases exceed even the minimum render budget.")
