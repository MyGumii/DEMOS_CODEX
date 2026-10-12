"""Bind exact ordinary Office structure to complete rendered PDF page images.

This is a process-local input boundary, not a Wiki writer or a DRM opener.
Native OOXML supplies tables; the PDF derivative supplies page appearance.
No inferred PDF grid is allowed to redefine an original Office table.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import hmac
from io import BytesIO
import json
from pathlib import Path
import secrets
import struct

from pypdf import PdfReader

from ..document_pdf_process import _run_worker, PdfProcessPolicy
from ..document_source_tables import NativeTableDocument, extract_native_table_document
from ..llm_images import ModelImageInput, validate_image_inputs
from .office_visual_source import AuthorizedOfficePdfRender, AuthorizedOfficePdfRenderer, OfficeVisualSourceError
from .pdf_visual_source import PdfPageRenderPolicy, PdfRenderedPage, renderer_identity


VERSION = "office-native-structure-rendered-pages-v1"
_KEY = secrets.token_bytes(32)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"))


def _page_source_id(render, policy, page, png_sha256):
    return "OV_" + sha256(_json([
        render.original_sha256, render.derivative_sha256, page,
        png_sha256, asdict(policy),
    ]).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class PreparedOfficeHybridSource:
    render: AuthorizedOfficePdfRender = field(repr=False)
    native: NativeTableDocument = field(repr=False)
    pages: tuple[PdfRenderedPage, ...] = field(repr=False)
    render_policy: PdfPageRenderPolicy
    seal: str = field(repr=False)

    def identity(self):
        return {
            "version": VERSION,
            "original_filename": self.render.original_filename,
            "original_sha256": self.render.original_sha256,
            "derivative_sha256": self.render.derivative_sha256,
            "derivative_pages": self.render.expected_pages,
            "render_method": self.render.render_method,
            "native_format": self.native.format,
            "native_structure_sha256": sha256(self.native.canonical_json().encode()).hexdigest(),
            "native_coverage": self.native.coverage,
            "render_identity": renderer_identity(),
            "render_policy": asdict(self.render_policy),
            "pages": [page.identity() for page in self.pages],
            "visual_interpretation_complete": False,
        }

    def checked(self, *, actor_user_id: str, original: bytes):
        self.check_seal()
        self.render.checked(actor_user_id=actor_user_id,
            original_filename=self.render.original_filename, original=original)
        if (self.native.source_sha256 != self.render.original_sha256
                or self.native.format != self.render.original_format
                or self.native.canonical_json() !=
                    extract_native_table_document(self.render.original_filename, original).canonical_json()):
            raise OfficeVisualSourceError("Office structure differs from the original.")
        return self

    def check_seal(self):
        """Only a process-local shape check; not a replacement for CAS re-read."""
        if (type(self.render) is not AuthorizedOfficePdfRender
                or type(self.native) is not NativeTableDocument
                or type(self.pages) is not tuple
                or type(self.render_policy) is not PdfPageRenderPolicy
                or type(self.seal) is not str):
            raise OfficeVisualSourceError("Office hybrid source has an invalid shape.")
        if (self.native.source_sha256 != self.render.original_sha256
                or self.native.format != self.render.original_format
                or len(self.pages) != self.render.expected_pages
                or any(type(page) is not PdfRenderedPage or page.page != ordinal
                    for ordinal, page in enumerate(self.pages, 1))):
            raise OfficeVisualSourceError("Office structure and rendered pages differ from the original.")
        metadata = validate_image_inputs(tuple(page.image for page in self.pages),
            self.render_policy.image_policy(len(self.pages)))
        if any((page.width, page.height) != (row["width"], row["height"])
                for page, row in zip(self.pages, metadata)):
            raise OfficeVisualSourceError("Office page image dimensions changed.")
        expected = hmac.digest(_KEY, _json(self.identity()).encode(), "sha256").hex()
        if not hmac.compare_digest(self.seal, expected):
            raise OfficeVisualSourceError("Office hybrid source seal changed.")
        return self


def prepare_office_hybrid_source(render: AuthorizedOfficePdfRender, *,
        actor_user_id: str, original: bytes, python_executable: str,
        render_policy=PdfPageRenderPolicy(), process_policy=PdfProcessPolicy(),
        cancel_event=None) -> PreparedOfficeHybridSource:
    """Prepare source-bound native tables and every page raster, without PDF grids."""
    if (type(render) is not AuthorizedOfficePdfRender
            or type(render_policy) is not PdfPageRenderPolicy
            or type(process_policy) is not PdfProcessPolicy):
        raise OfficeVisualSourceError("An approved Office render and bounded policies are required.")
    derivative = render.checked(actor_user_id=actor_user_id,
        original_filename=render.original_filename, original=original)
    native = extract_native_table_document(render.original_filename, original)
    if native.source_sha256 != render.original_sha256 or native.format != render.original_format:
        raise OfficeVisualSourceError("Native Office structure differs from the render origin.")
    try:
        reader = PdfReader(BytesIO(derivative), strict=True)
        if reader.is_encrypted or len(reader.pages) != render.expected_pages:
            raise ValueError("Office page inventory mismatch")
    except Exception as exc:
        raise OfficeVisualSourceError("Office derivative page inventory is invalid.") from exc

    header = _json({"version": 1, "raw_bytes": len(derivative),
        "raw_sha256": render.derivative_sha256, "pages": render.expected_pages,
        "policy": asdict(render_policy)}).encode()
    wire, _pid, _elapsed, _metrics = _run_worker(
        Path(python_executable), Path(__file__).resolve().parents[1] / "_document_pdf_visual_worker.py",
        (struct.pack("!I", len(header)), header, derivative), policy=process_policy,
        output_bytes=4 * ((render_policy.maximum_total_bytes + 2) // 3) + 1_048_576,
        cancel_event=cancel_event,
    )
    try:
        row = json.loads(wire)
        if (type(row) is not dict
                or set(row) != {"version", "raw_sha256", "renderer", "renderer_version",
                    "render_identity", "policy", "pages"}
                or row["version"] != 1
                or row["raw_sha256"] != render.derivative_sha256
                or row["renderer"] != "pypdfium2"
                or row["renderer_version"] != "5.13.0"
                or row["policy"] != asdict(render_policy)
                or _json(row["render_identity"]) != _json(renderer_identity())
                or type(row["pages"]) is not list
                or len(row["pages"]) != render.expected_pages):
            raise ValueError("Invalid Office derivative renderer identity")
        pages = []
        for ordinal, item in enumerate(row["pages"], 1):
            if (type(item) is not dict
                    or set(item) != {"page", "width", "height", "sha256", "png"}
                    or type(item["page"]) is not int or item["page"] != ordinal
                    or any(type(item[key]) is not int or item[key] <= 0
                        for key in ("width", "height"))
                    or type(item["png"]) is not str
                    or len(item["png"]) > 4 * ((render_policy.maximum_page_bytes + 2) // 3)):
                raise ValueError("Invalid Office derivative page record")
            image = base64.b64decode(item["png"], validate=True)
            if sha256(image).hexdigest() != item["sha256"] or not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Office derivative page bytes changed")
            source_id = _page_source_id(render, render_policy, ordinal,
                item["sha256"])
            pages.append(PdfRenderedPage(ordinal, item["width"], item["height"],
                ModelImageInput(source_id, "image/png", image)))
        prepared = PreparedOfficeHybridSource(render, native, tuple(pages), render_policy, "")
        seal = hmac.digest(_KEY, _json(prepared.identity()).encode(), "sha256").hex()
        return replace(prepared, seal=seal).checked(actor_user_id=actor_user_id, original=original)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError) as exc:
        raise OfficeVisualSourceError("Office derivative page renderer response is invalid.") from exc


def prepare_authorized_office_hybrid_source(original_filename: str, original: bytes, *,
        actor_user_id: str, renderer: AuthorizedOfficePdfRenderer, python_executable: str,
        render_policy=PdfPageRenderPolicy(), process_policy=PdfProcessPolicy(),
        cancel_event=None) -> PreparedOfficeHybridSource:
    """Let the approved renderer authorize before native source interpretation."""
    if not callable(getattr(renderer, "render_for_actor", None)):
        raise OfficeVisualSourceError("An approved Office renderer is required.")
    render = renderer.render_for_actor(original_filename, original,
        actor_user_id=actor_user_id)
    return prepare_office_hybrid_source(render, actor_user_id=actor_user_id,
        original=original, python_executable=python_executable,
        render_policy=render_policy, process_policy=process_policy,
        cancel_event=cancel_event)


def restore_office_hybrid_source(render: AuthorizedOfficePdfRender, *,
        actor_user_id: str, original: bytes, pages: tuple[PdfRenderedPage, ...],
        render_policy: PdfPageRenderPolicy) -> PreparedOfficeHybridSource:
    """Bind exact retained PNG bytes to their original without rerendering.

    The archive repository verifies every CAS digest, size, dimensions, page
    order and current export right before calling this function. Re-rendering a
    later process would substitute new pixels for the bytes the model is meant
    to see, and is unnecessary for checking the retained evidence.
    """
    if (type(render) is not AuthorizedOfficePdfRender
            or type(render_policy) is not PdfPageRenderPolicy
            or type(pages) is not tuple or len(pages) != render.expected_pages):
        raise OfficeVisualSourceError("Retained Office page inventory is invalid.")
    render.checked(actor_user_id=actor_user_id,
        original_filename=render.original_filename, original=original)
    native = extract_native_table_document(render.original_filename, original)
    if native.source_sha256 != render.original_sha256 or native.format != render.original_format:
        raise OfficeVisualSourceError("Retained Office native structure differs.")
    for ordinal, page in enumerate(pages, 1):
        if (type(page) is not PdfRenderedPage or page.page != ordinal
                or page.image.source_id != _page_source_id(render, render_policy,
                    ordinal, sha256(page.image.data).hexdigest())):
            raise OfficeVisualSourceError("Retained Office page origin differs.")
    prepared = PreparedOfficeHybridSource(render, native, pages, render_policy, "")
    seal = hmac.digest(_KEY, _json(prepared.identity()).encode(), "sha256").hex()
    return replace(prepared, seal=seal).checked(actor_user_id=actor_user_id,
        original=original)


__all__ = ["PreparedOfficeHybridSource", "prepare_office_hybrid_source",
    "prepare_authorized_office_hybrid_source", "restore_office_hybrid_source"]
