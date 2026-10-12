"""Authorized owner-open adapter for standard encrypted PDFs.

This accepts a supplied *owner* credential for one source digest.  It is not a
password discovery tool, a DRM bypass, or a substitute for a company-approved
Office/Acrobat opener.  The clear derivative exists only in memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Callable

from pypdf import PdfReader, PdfWriter, PasswordType
from pypdf.generic import ContentStream

from .protected_sources import ProtectedSourceKind, classify_protected_container


class ProtectedPdfOpenError(Exception):
    """The source could not be opened with the approved owner credential."""


@dataclass(frozen=True, slots=True)
class AuthorizedPdfDerivative:
    original_sha256: str
    derivative_sha256: str
    derivative_pdf: bytes
    expected_pages: int
    observed_pages: int
    visual_pages: tuple[int, ...]
    opening_method: str = "standard_pdf_owner_password_v1"


_GRAPHIC_PAINT_OPERATORS = frozenset({
    b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*",
    b"sh", b"Do", b"INLINE IMAGE",
})


def _pages_with_unexamined_graphics(reader: PdfReader) -> tuple[int, ...]:
    """Flag raster, vector, forms and annotations missing from text extraction.

    This deliberately errs toward visual review. An operator in a page stream
    is not evidence that the renderer or a VLM has interpreted its meaning.
    """
    result: list[int] = []
    for number, page in enumerate(reader.pages, start=1):
        if page.get("/Annots"):
            result.append(number)
            continue
        contents = page.get_contents()
        if contents is None:
            continue
        operations = ContentStream(contents, reader).operations
        if any(operator in _GRAPHIC_PAINT_OPERATORS for _, operator in operations):
            result.append(number)
    return tuple(result)


class AuthorizedOwnerPdfOpener:
    """Resolve a document-specific owner secret from an operator-owned source."""

    def __init__(self, owner_password_for_sha256: Callable[[str], str | None]) -> None:
        if not callable(owner_password_for_sha256):
            raise TypeError("An owner password resolver is required.")
        self._password_for = owner_password_for_sha256

    def open(self, filename: str, content: bytes) -> AuthorizedPdfDerivative:
        if (
            not isinstance(filename, str)
            or Path(filename).suffix.lower() != ".pdf"
            or not isinstance(content, bytes)
            or classify_protected_container(filename, content)
            is not ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
        ):
            raise ProtectedPdfOpenError("The source is not a standard encrypted PDF.")
        original_sha256 = sha256(content).hexdigest()
        try:
            password = self._password_for(original_sha256)
            if not isinstance(password, str) or not password:
                raise ProtectedPdfOpenError("No approved owner credential is available.")
            reader = PdfReader(BytesIO(content), strict=True)
            if reader.decrypt(password) is not PasswordType.OWNER_PASSWORD:
                raise ProtectedPdfOpenError("The supplied credential is not the owner credential.")
            expected_pages = len(reader.pages)
            if expected_pages < 1 or expected_pages > 500:
                raise ProtectedPdfOpenError("The protected PDF page count is unsupported.")
            writer = PdfWriter()
            writer.append_pages_from_reader(reader)
            clear = BytesIO()
            writer.write(clear)
            derivative = clear.getvalue()
            observed = PdfReader(BytesIO(derivative), strict=True)
            if observed.is_encrypted or len(observed.pages) != expected_pages:
                raise ProtectedPdfOpenError("The authorized derivative lost pages.")
            visual_pages = _pages_with_unexamined_graphics(observed)
            return AuthorizedPdfDerivative(
                original_sha256=original_sha256,
                derivative_sha256=sha256(derivative).hexdigest(),
                derivative_pdf=derivative,
                expected_pages=expected_pages,
                observed_pages=len(observed.pages),
                visual_pages=visual_pages,
            )
        except ProtectedPdfOpenError:
            raise
        except Exception as exc:
            raise ProtectedPdfOpenError("The authorized PDF open failed.") from exc


__all__ = [
    "AuthorizedOwnerPdfOpener", "AuthorizedPdfDerivative", "ProtectedPdfOpenError",
]
