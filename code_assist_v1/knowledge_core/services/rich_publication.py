"""Canonical bounded rich-document extraction for publication previews.

All parser I/O, temporary-file reads, hashing, and cleanup happen before the
worker opens its commit transaction.  Only sanitized failure/warning codes and
immutable bytes cross into the publication boundary.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
import re
import shutil
import tempfile

from ...document_extractors import extract_document
from .publication import (
    EmbeddedPublicationAsset,
    ExtractedPublicationContent,
    Utf8PublicationExtractor,
)


_RICH_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})
_TEXT_EXTENSIONS = frozenset({".md", ".markdown", ".txt", ".text"})
_IMAGE_MEDIA = {
    ".bmp": "image/bmp",
    ".emf": "image/emf",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
    ".wmf": "image/wmf",
}
_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z._() -]+", re.UNICODE)
_MAX_ASSETS = 64
_MAX_ASSET_BYTES = 16_777_216
_MAX_BUNDLE_BYTES = 67_108_864


def _safe_asset_name(value: object, *, ordinal: int) -> str:
    raw = Path(value).name if isinstance(value, str) and value else "asset"
    cleaned = _UNSAFE_NAME.sub("_", raw).strip(" ._") or "asset"
    return f"asset-{ordinal:03d}-{cleaned}"[:500]


def _safe_label(value: object, *, ordinal: int) -> str:
    label = value.strip() if isinstance(value, str) else ""
    if not label:
        label = f"Embedded asset {ordinal}"
    return label.replace("\\", "\\\\").replace("]", "\\]")[:200]


def _cleanup_roots(metadata: object) -> tuple[Path, ...]:
    if not isinstance(metadata, dict):
        return ()
    rows = metadata.get("cleanup_dirs")
    if not isinstance(rows, list):
        return ()
    temp_root = Path(tempfile.gettempdir()).resolve(strict=False)
    roots: list[Path] = []
    for raw in rows:
        if not isinstance(raw, str) or not raw:
            continue
        candidate = Path(raw).resolve(strict=False)
        if (
            candidate != temp_root
            and candidate.is_relative_to(temp_root)
            and candidate.name.startswith("sb-")
        ):
            roots.append(candidate)
    return tuple(sorted(set(roots), key=lambda value: str(value).casefold()))


def _cleanup(roots: tuple[Path, ...]) -> None:
    for root in reversed(roots):
        shutil.rmtree(root, ignore_errors=True)


def _read_assets(
    images: object, *, roots: tuple[Path, ...]
) -> tuple[EmbeddedPublicationAsset, ...]:
    if not isinstance(images, list) or len(images) > _MAX_ASSETS:
        raise ValueError("invalid_asset_manifest")
    assets: list[EmbeddedPublicationAsset] = []
    total = 0
    for ordinal, row in enumerate(images, start=1):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("invalid_asset_manifest")
        try:
            path = Path(row["path"]).resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("invalid_asset_path") from None
        if not roots or not any(path.is_relative_to(root) for root in roots):
            raise ValueError("invalid_asset_path")
        extension = path.suffix.lower()
        media_type = _IMAGE_MEDIA.get(extension)
        if media_type is None:
            guessed, _ = mimetypes.guess_type(path.name)
            if not isinstance(guessed, str) or not guessed.startswith("image/"):
                raise ValueError("unsupported_embedded_asset")
            media_type = guessed
        content = path.read_bytes()
        if not content or len(content) > _MAX_ASSET_BYTES:
            raise ValueError("embedded_asset_size_invalid")
        total += len(content)
        if total > _MAX_BUNDLE_BYTES:
            raise ValueError("embedded_asset_bundle_too_large")
        assets.append(
            EmbeddedPublicationAsset(
                content=content,
                logical_name=_safe_asset_name(
                    row.get("filename") or path.name, ordinal=ordinal
                ),
                media_type=media_type,
            )
        )
    return tuple(assets)


class CanonicalRichPublicationExtractor:
    """Native-only rich extractor frozen for the SBE-3.1 publication path."""

    __slots__ = ("_text",)

    def __init__(self) -> None:
        self._text = Utf8PublicationExtractor()

    def extract(
        self,
        content: bytes,
        *,
        media_type: str,
        original_filename: str | None,
    ) -> ExtractedPublicationContent:
        base_media = media_type.split(";", 1)[0].strip().lower()
        filename = Path(original_filename).name if original_filename else ""
        extension = Path(filename).suffix.lower()
        if extension in _TEXT_EXTENSIONS or (
            not extension
            and base_media in {"text/markdown", "text/plain", "application/json"}
        ):
            return self._text.extract(
                content,
                media_type=(
                    media_type
                    if base_media in {"text/markdown", "text/plain", "application/json"}
                    else "text/plain"
                ),
                original_filename=filename or None,
            )
        if extension not in _RICH_EXTENSIONS:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="unsupported_media_type",
                extractor_name="canonical-rich",
                extractor_version="rich-v1",
            )

        extracted = None
        roots: tuple[Path, ...] = ()
        try:
            extracted = extract_document(
                filename,
                content,
                allow_configured_converters=False,
            )
            roots = _cleanup_roots(extracted.metadata)
            if not isinstance(extracted.markdown, str) or not extracted.markdown.strip():
                raise ValueError("empty_extracted_document")
            assets = _read_assets(extracted.images, roots=roots)
            markdown = extracted.markdown.rstrip()
            if assets:
                references = ["## Embedded assets"]
                for ordinal, (asset, image) in enumerate(
                    zip(assets, extracted.images, strict=True), start=1
                ):
                    import hashlib

                    digest = hashlib.sha256(asset.content).hexdigest()
                    references.append(
                        f"![{_safe_label(image.get('label'), ordinal=ordinal)}]"
                        f"(asset://sha256/{digest})"
                    )
                markdown += "\n\n" + "\n\n".join(references) + "\n"
            warnings = (
                ("document_extraction_warning",) if extracted.warnings else ()
            )
            return ExtractedPublicationContent(
                markdown=markdown,
                failure_code=None,
                extractor_name=f"canonical-{extracted.extractor}"[:120],
                extractor_version="rich-v1",
                warnings=warnings,
                assets=assets,
            )
        except Exception:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="document_extraction_failed",
                extractor_name="canonical-rich",
                extractor_version="rich-v1",
            )
        finally:
            if extracted is not None and not roots:
                roots = _cleanup_roots(extracted.metadata)
            _cleanup(roots)


__all__ = ["CanonicalRichPublicationExtractor"]
