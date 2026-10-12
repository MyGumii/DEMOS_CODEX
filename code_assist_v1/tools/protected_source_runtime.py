"""10521 protected-source provider selection, not a DRM implementation.

The local PDF surrogate uses a digest-keyed owner credential. A company
deployment can select one installed entry-point factory returning an
actor-bound opener and current publication authorizer. It may also expose
``authorize_original_download(evidence)``; without this optional method,
protected original downloads are denied. Neither mechanism accepts a
document credential from an upload request or stores one in SQLite.
"""

from __future__ import annotations

import json
from importlib import metadata
import os
import re

from code_assist_v1.knowledge_core.protected_pdf_open import AuthorizedOwnerPdfOpener


_KEY = "CODE_ASSIST_10521_PDF_OWNER_CREDENTIALS_JSON"
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_APPROVED_KEY = "CODE_ASSIST_10521_APPROVED_PROTECTED_PROVIDER"
_APPROVED_GROUP = "code_assist_v1.protected_source_v1"
_APPROVED_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


def approved_protected_provider_for_10521_runtime():
    """Load one explicitly installed company adapter, never a user module path.

    The installed entry-point factory returns an object implementing both
    actor-bound ``open_for_actor`` and current ``authorize_publication``.
    Protected original byte transfer additionally needs the optional
    ``authorize_original_download`` method; its absence denies downloads.
    This code neither grants rights nor opens protected files by itself.
    """
    selected = os.environ.get(_APPROVED_KEY)
    if selected is None:
        return None
    if _APPROVED_NAME.fullmatch(selected) is None or _KEY in os.environ:
        raise ValueError("The approved protected provider configuration is invalid.")
    try:
        matches = [entry for entry in metadata.entry_points(group=_APPROVED_GROUP)
                   if entry.name == selected]
        if len(matches) != 1:
            raise ValueError("The approved protected provider is unavailable or ambiguous.")
        factory = matches[0].load()
        if not callable(factory):
            raise TypeError("The approved protected provider factory is invalid.")
        provider = factory()
    except Exception:
        raise RuntimeError("The approved protected provider could not be loaded.") from None
    if (not callable(getattr(provider, "open_for_actor", None))
            or not callable(getattr(provider, "authorize_publication", None))):
        raise TypeError("The approved protected provider contract is invalid.")
    return provider


def owner_pdf_opener_for_10521_runtime() -> AuthorizedOwnerPdfOpener | None:
    raw = os.environ.get(_KEY)
    if raw is None:
        return None
    try:
        values = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("The 10521 PDF owner credential mapping is invalid.") from exc
    if (
        type(values) is not dict or not 1 <= len(values) <= 128
        or any(
            type(digest) is not str or _SHA.fullmatch(digest) is None
            or type(password) is not str or not 1 <= len(password) <= 256
            for digest, password in values.items()
        )
    ):
        raise ValueError("The 10521 PDF owner credential mapping is invalid.")
    return AuthorizedOwnerPdfOpener(values.get)


def owner_pdf_opener_for_10521_worker() -> AuthorizedOwnerPdfOpener | None:
    """Compatibility alias for the original worker-only v50 validation path."""
    return owner_pdf_opener_for_10521_runtime()


__all__ = [
    "approved_protected_provider_for_10521_runtime",
    "owner_pdf_opener_for_10521_runtime", "owner_pdf_opener_for_10521_worker",
]
