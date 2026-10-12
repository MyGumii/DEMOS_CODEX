"""Bind an authorized Office PDF render to its retained original.

The PDF visual writer can analyze a permitted rendered derivative, but its
native source identity is that PDF. This opt-in adapter preserves the separate
Word/PowerPoint and actor identity until a publication workflow binds both.
It neither opens Office files nor grants export permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePath
from typing import Literal, Protocol

from ..document_pdf_geometry import PdfGeometryLimits
from .pdf_visual_source import (
    PreparedPdfVisualSource, PdfPageRenderPolicy, checked_visual_source,
    prepare_pdf_visual_source,
)


class OfficeVisualSourceError(ValueError):
    """An Office render is unauthorized, incomplete, or no longer source-bound."""


class AuthorizedOfficePdfRenderer(Protocol):
    """Composition-owned opener; it must enforce the actor's actual export rights.

    The original bytes are passed by the trusted worker, never by a caller's
    derivative-path claim. Its result is checked before any PDF parser/model I/O.
    """

    def render_for_actor(self, original_filename: str, original: bytes,
            *, actor_user_id: str) -> "AuthorizedOfficePdfRender": ...


@dataclass(frozen=True, slots=True)
class AuthorizedOfficePdfRender:
    actor_user_id: str
    original_filename: str
    original_sha256: str
    original_format: Literal["docx", "pptx"]
    derivative_pdf: bytes
    derivative_sha256: str
    expected_pages: int
    render_method: str
    export_permitted: bool

    def checked(self, *, actor_user_id: str, original_filename: str,
                original: bytes) -> bytes:
        if (type(actor_user_id) is not str or not actor_user_id
                or type(original_filename) is not str or not original_filename
                or PurePath(original_filename).name != original_filename
                or type(original) is not bytes
                or type(self.actor_user_id) is not str
                or self.actor_user_id != actor_user_id
                or type(self.original_filename) is not str
                or self.original_filename != original_filename
                or type(self.original_format) is not str
                or self.original_format not in {"docx", "pptx"}
                or PurePath(original_filename).suffix.lower() != "." + self.original_format
                or type(self.original_sha256) is not str
                or self.original_sha256 != sha256(original).hexdigest()
                or type(self.export_permitted) is not bool
                or not self.export_permitted
                or type(self.render_method) is not str
                or not 1 <= len(self.render_method) <= 120
                or type(self.expected_pages) is not int
                or not 1 <= self.expected_pages <= 1000
                or type(self.derivative_pdf) is not bytes
                or not self.derivative_pdf.startswith(b"%PDF-")
                or not 1 <= len(self.derivative_pdf) <= 64 * 1024 * 1024
                or type(self.derivative_sha256) is not str
                or self.derivative_sha256 != sha256(self.derivative_pdf).hexdigest()):
            raise OfficeVisualSourceError("The Office render identity, permission or coverage is invalid.")
        return self.derivative_pdf


@dataclass(frozen=True, slots=True)
class PreparedOfficeVisualSource:
    render: AuthorizedOfficePdfRender
    visual: PreparedPdfVisualSource

    def checked(self, *, actor_user_id: str, original_filename: str,
                original: bytes) -> PreparedPdfVisualSource:
        derivative = self.render.checked(actor_user_id=actor_user_id,
            original_filename=original_filename, original=original)
        checked_visual_source(self.visual)
        if (self.visual.geometry.raw_sha256 != sha256(derivative).hexdigest()
                or self.visual.geometry.raw_bytes != len(derivative)
                or len(self.visual.pages) != self.render.expected_pages):
            raise OfficeVisualSourceError("The rendered PDF pages differ from their Office origin.")
        return self.visual

    def identity(self) -> dict[str, object]:
        """Keep the original Office identity separate from the PDF derivative."""
        return {
            "version": "authorized-office-original-derivative-visual-v1",
            "actor_user_id": self.render.actor_user_id,
            "original_filename": self.render.original_filename,
            "original_format": self.render.original_format,
            "original_sha256": self.render.original_sha256,
            "derivative_sha256": self.render.derivative_sha256,
            "expected_pages": self.render.expected_pages,
            "render_method": self.render.render_method,
            "derivative_visual_source": self.visual.identity(),
        }


def prepare_office_visual_source(snapshot_id: str, render: AuthorizedOfficePdfRender,
        *, actor_user_id: str, original_filename: str, original: bytes,
        python_executable: str,
        render_policy: PdfPageRenderPolicy = PdfPageRenderPolicy()) -> PreparedOfficeVisualSource:
    if type(render) is not AuthorizedOfficePdfRender:
        raise OfficeVisualSourceError("An approved Office render is required.")
    derivative = render.checked(actor_user_id=actor_user_id,
        original_filename=original_filename, original=original)
    derivative_name = PurePath(original_filename).stem + ".rendered.pdf"
    visual = prepare_pdf_visual_source(snapshot_id, derivative_name, derivative,
        python_executable=python_executable, geometry_limits=PdfGeometryLimits(),
        render_policy=render_policy)
    prepared = PreparedOfficeVisualSource(render, visual)
    prepared.checked(actor_user_id=actor_user_id, original_filename=original_filename,
        original=original)
    return prepared


def prepare_authorized_office_visual_source(snapshot_id: str,
        *, actor_user_id: str, original_filename: str, original: bytes,
        renderer: AuthorizedOfficePdfRenderer, python_executable: str,
        render_policy: PdfPageRenderPolicy = PdfPageRenderPolicy()) -> PreparedOfficeVisualSource:
    """Invoke an injected authorized renderer, then bind its exact output.

    This is not a generic converter fallback: absent or malformed providers
    fail closed and cannot silently switch to text-only Wiki generation.
    """
    if (type(actor_user_id) is not str or not actor_user_id
            or type(original_filename) is not str or not original_filename
            or PurePath(original_filename).name != original_filename
            or PurePath(original_filename).suffix.lower() not in {".docx", ".pptx"}
            or type(original) is not bytes or not original
            or renderer is None
            or not callable(getattr(renderer, "render_for_actor", None))):
        raise OfficeVisualSourceError("An exact Office source and approved renderer are required.")
    try:
        render = renderer.render_for_actor(original_filename, original,
            actor_user_id=actor_user_id)
    except PermissionError as exc:
        raise OfficeVisualSourceError("The Office export is not authorized.") from exc
    except Exception as exc:
        raise OfficeVisualSourceError("The authorized Office render failed.") from exc
    if type(render) is not AuthorizedOfficePdfRender:
        raise OfficeVisualSourceError("The authorized renderer returned no exact PDF binding.")
    return prepare_office_visual_source(snapshot_id, render,
        actor_user_id=actor_user_id, original_filename=original_filename,
        original=original, python_executable=python_executable,
        render_policy=render_policy)


__all__ = ["AuthorizedOfficePdfRender", "OfficeVisualSourceError",
    "AuthorizedOfficePdfRenderer", "PreparedOfficeVisualSource",
    "prepare_office_visual_source", "prepare_authorized_office_visual_source"]
