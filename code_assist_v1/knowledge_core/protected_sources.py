"""Classify container-level protection before choosing an upload authoring lane.

This is a routing signal, not permission to decrypt a file.  In particular,
an OLE compound container under an OOXML extension is only a *candidate* for
Office encryption: an approved opener must establish the actual access and
coverage result.  Vendor DRM may use other wrappers and requires its own
authorized capability probe.
"""

from __future__ import annotations

from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Protocol


_OFFICE_OPEN_XML = frozenset({".docx", ".pptx", ".xlsx"})
_OLE_COMPOUND_HEADER = bytes.fromhex("d0cf11e0a1b11ae1")


class ProtectedSourceKind(str, Enum):
    PDF_STANDARD_ENCRYPTION = "pdf_standard_encryption"
    OFFICE_ENCRYPTED_PACKAGE_CANDIDATE = "office_encrypted_package_candidate"
    # This is never inferred from a filename or an ordinary PDF/OOXML parser.
    # Only an installed policy-aware provider may assert this source kind.
    COMPANY_POLICY_PROTECTED = "company_policy_protected"


class PolicyProtectedSourceClassifier(Protocol):
    """An installed provider's actor-bound protection signal, not authority."""

    def classify_for_actor(
        self, filename: str, content: bytes, *, actor_user_id: str,
    ) -> ProtectedSourceKind | None: ...


def classify_protected_for_actor(
    filename: str, content: bytes, *, actor_user_id: str,
    policy_classifier: PolicyProtectedSourceClassifier | None = None,
) -> ProtectedSourceKind | None:
    """Prefer an explicit policy signal; never infer policy DRM from bytes.

    A provider failure or malformed reply propagates. Callers must fail closed,
    not retry the ordinary parser as if the source were unprotected.
    """
    if not isinstance(actor_user_id, str) or not actor_user_id.strip():
        raise ValueError("Protected source classification requires an actor.")
    ordinary_signal = classify_protected_container(filename, content)
    if policy_classifier is None:
        return ordinary_signal
    classify = getattr(policy_classifier, "classify_for_actor", None)
    if not callable(classify):
        raise TypeError("The policy source classifier is invalid.")
    policy_signal = classify(filename, content, actor_user_id=actor_user_id)
    if policy_signal is None:
        return ordinary_signal
    if policy_signal is not ProtectedSourceKind.COMPANY_POLICY_PROTECTED:
        raise ValueError("The policy source classifier returned an invalid kind.")
    return policy_signal


def classify_protected_container(
    filename: str, content: bytes
) -> ProtectedSourceKind | None:
    """Return a narrow container signal without attempting to open content.

    A malformed source is left to the ordinary fail-closed extractor.  A
    positive result never asserts that an account can read, export, capture,
    or publish the document.
    """
    if not isinstance(filename, str) or not isinstance(content, bytes):
        raise TypeError("Protected source classification requires a filename and bytes.")
    extension = Path(filename).suffix.lower()
    if extension in _OFFICE_OPEN_XML:
        if content.startswith(_OLE_COMPOUND_HEADER):
            return ProtectedSourceKind.OFFICE_ENCRYPTED_PACKAGE_CANDIDATE
        return None
    if extension != ".pdf":
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(content), strict=True)
        return (
            ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
            if reader.is_encrypted else None
        )
    except Exception:
        # Classification must never turn malformed input into a success path.
        # The canonical extraction lane will report the actual parse failure.
        return None


__all__ = [
    "PolicyProtectedSourceClassifier", "ProtectedSourceKind",
    "classify_protected_container", "classify_protected_for_actor",
]
