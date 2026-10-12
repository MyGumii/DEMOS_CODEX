"""Bind an authorized clear PDF derivative to its encrypted original.

This is an in-memory preparation boundary, not a DRM opener or a publication
shortcut.  Existing PDF geometry and page pixels remain bound to the clear
derivative; the encrypted original keeps its own distinct identity.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from hashlib import sha256
import hmac
import secrets

from ..document_pdf_geometry import PdfGeometryLimits
from ..document_pdf_process import PdfProcessPolicy
from .pdf_visual_source import (
    PdfPageRenderPolicy, PreparedPdfVisualSource, checked_visual_source,
    prepare_pdf_visual_source,
)
from .protected_pdf_open import AuthorizedOwnerPdfOpener
from .wiki_authoring import _json


VERSION = "authorized-pdf-original-derivative-visual-v1"
_KEY = secrets.token_bytes(32)


class ProtectedPdfVisualBindingError(ValueError):
    """An authorized PDF visual preparation lost its source binding."""


@dataclass(frozen=True, slots=True)
class PreparedAuthorizedPdfVisualSource:
    original_sha256: str
    derivative_sha256: str
    original_filename: str
    opening_method: str
    expected_pages: int
    visual_pages: tuple[int, ...]
    source: PreparedPdfVisualSource
    derivative_pdf: bytes = field(repr=False, compare=False)
    seal: str = field(repr=False, compare=False)

    def identity(self) -> dict[str, object]:
        return {
            "version": VERSION,
            "original_sha256": self.original_sha256,
            "derivative_sha256": self.derivative_sha256,
            "original_filename": self.original_filename,
            "opening_method": self.opening_method,
            "expected_pages": self.expected_pages,
            "visual_pages": list(self.visual_pages),
            "derivative_visual_source": self.source.identity(),
        }

    def check(self, *, original: bytes) -> PreparedPdfVisualSource:
        if type(original) is not bytes or type(self.derivative_pdf) is not bytes:
            raise ProtectedPdfVisualBindingError("PDF source bytes are unavailable.")
        try:
            checked_visual_source(self.source)
            geometry = self.source.geometry
            valid = (
                type(self.original_sha256) is str
                and self.original_sha256 == sha256(original).hexdigest()
                and type(self.derivative_sha256) is str
                and self.derivative_sha256 == sha256(self.derivative_pdf).hexdigest()
                and self.original_sha256 != self.derivative_sha256
                and type(self.original_filename) is str
                and self.original_filename.lower().endswith(".pdf")
                and type(self.opening_method) is str
                and self.opening_method == "standard_pdf_owner_password_v1"
                and type(self.expected_pages) is int
                and 1 <= self.expected_pages <= 500
                and type(self.visual_pages) is tuple
                and all(type(page) is int and 1 <= page <= self.expected_pages
                        for page in self.visual_pages)
                and tuple(sorted(set(self.visual_pages))) == self.visual_pages
                and len(self.source.pages) == self.expected_pages
                and geometry.raw_sha256 == self.derivative_sha256
                and geometry.raw_bytes == len(self.derivative_pdf)
                and geometry.filename == self.original_filename
                and type(self.seal) is str
                and hmac.compare_digest(
                    self.seal,
                    hmac.digest(_KEY, _json(self.identity()).encode(), "sha256").hex(),
                )
            )
        except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError) as exc:
            raise ProtectedPdfVisualBindingError("PDF visual source binding is invalid.") from exc
        if not valid:
            raise ProtectedPdfVisualBindingError("PDF visual source binding is invalid.")
        return self.source


def prepare_authorized_pdf_visual_source(
    snapshot_id: str,
    filename: str,
    original: bytes,
    *,
    owner_opener: AuthorizedOwnerPdfOpener,
    python_executable: str,
    geometry_limits: PdfGeometryLimits = PdfGeometryLimits(),
    render_policy: PdfPageRenderPolicy = PdfPageRenderPolicy(),
    process_policy: PdfProcessPolicy = PdfProcessPolicy(),
    cancel_event=None,
) -> PreparedAuthorizedPdfVisualSource:
    """Open only through the approved owner adapter, then render its derivative."""
    if type(owner_opener) is not AuthorizedOwnerPdfOpener:
        raise TypeError("An authorized PDF owner opener is required.")
    opened = owner_opener.open(filename, original)
    source = prepare_pdf_visual_source(
        snapshot_id, filename, opened.derivative_pdf,
        python_executable=python_executable,
        geometry_limits=geometry_limits,
        render_policy=render_policy,
        process_policy=process_policy,
        cancel_event=cancel_event,
        allow_visual_only=True,
    )
    prepared = PreparedAuthorizedPdfVisualSource(
        opened.original_sha256, opened.derivative_sha256, filename,
        opened.opening_method, opened.expected_pages, opened.visual_pages,
        source, opened.derivative_pdf, "",
    )
    prepared = replace(prepared, seal=hmac.digest(
        _KEY, _json(prepared.identity()).encode(), "sha256"
    ).hex())
    prepared.check(original=original)
    return prepared


__all__ = [
    "PreparedAuthorizedPdfVisualSource", "ProtectedPdfVisualBindingError",
    "prepare_authorized_pdf_visual_source",
]
