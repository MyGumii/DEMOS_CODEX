"""Canonical bounded rich-document extraction for publication previews.

All parser I/O, temporary-file reads, hashing, and cleanup happen before the
worker opens its commit transaction.  Only sanitized failure/warning codes and
immutable bytes cross into the publication boundary.
"""

from __future__ import annotations

import mimetypes
from hashlib import sha256
from pathlib import Path
import re
import shutil
import tempfile

from ...document_extractors import extract_document
from ..protected_pdf_open import AuthorizedOwnerPdfOpener
from ..protected_source_snapshot import (
    AuthorizedProtectedSourceOpener, AuthorizedSourceSnapshot,
    ProtectedExtractionProvenance, ProtectedLocationCoverage,
    ProtectedSourceSnapshotError,
)
from ..protected_sources import (
    PolicyProtectedSourceClassifier, ProtectedSourceKind,
    classify_protected_container, classify_protected_for_actor,
)
from .publication import (
    EmbeddedPublicationAsset,
    ExtractedPublicationContent,
    Utf8PublicationExtractor,
)


_RICH_EXTENSIONS = frozenset({".pdf", ".docx", ".pptx", ".xlsx"})
_PROTECTED_LOCATION_KINDS = {
    ".pdf": frozenset({"page"}),
    ".docx": frozenset({"page", "derived_page"}),
    ".pptx": frozenset({"slide"}),
    ".xlsx": frozenset({"sheet"}),
}
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

    __slots__ = ("_text", "_source_only", "_version", "_bounded_table_labels",
                 "_protected_pdf_opener", "_protected_source_opener",
                 "_policy_protection_classifier")

    def __init__(self, *, source_only: bool = False, bounded_table_labels: bool = False,
                 protected_pdf_opener: AuthorizedOwnerPdfOpener | None = None,
                 protected_source_opener: AuthorizedProtectedSourceOpener | None = None,
                 policy_protection_classifier: PolicyProtectedSourceClassifier | None = None) -> None:
        if type(source_only) is not bool:
            raise TypeError("source_only must be boolean")
        if type(bounded_table_labels) is not bool:
            raise TypeError("bounded_table_labels must be boolean")
        if bounded_table_labels and not source_only:
            raise ValueError("Bounded table labels require source-only extraction")
        if (protected_pdf_opener is not None
                and not isinstance(protected_pdf_opener, AuthorizedOwnerPdfOpener)):
            raise TypeError("The protected PDF opener must use the authorized owner contract.")
        if (protected_source_opener is not None
                and not callable(getattr(protected_source_opener, "open_for_actor", None))):
            raise TypeError("The protected source opener must supply open_for_actor().")
        if (policy_protection_classifier is not None
                and not callable(getattr(policy_protection_classifier, "classify_for_actor", None))):
            raise TypeError("The policy classifier must supply classify_for_actor().")
        self._text = Utf8PublicationExtractor()
        self._source_only = source_only
        self._bounded_table_labels = bounded_table_labels
        self._protected_pdf_opener = protected_pdf_opener
        self._protected_source_opener = protected_source_opener
        self._policy_protection_classifier = policy_protection_classifier
        self._version = ("rich-source-v3-bounded-tables" if bounded_table_labels else
            "rich-source-v2" if source_only else "rich-v1")

    def extract(
        self,
        content: bytes,
        *,
        media_type: str,
        original_filename: str | None,
        allow_unexamined_protected_pdf_visual: bool = False,
        actor_user_id: str | None = None,
        expected_protected_kind: ProtectedSourceKind | None = None,
    ) -> ExtractedPublicationContent:
        if type(allow_unexamined_protected_pdf_visual) is not bool:
            raise TypeError("Protected PDF visual mode must be boolean.")
        if (expected_protected_kind is not None
                and expected_protected_kind is not ProtectedSourceKind.COMPANY_POLICY_PROTECTED):
            raise TypeError("An unsupported durable protected source claim was supplied.")
        base_media = media_type.split(";", 1)[0].strip().lower()
        filename = Path(original_filename).name if original_filename else ""
        extension = Path(filename).suffix.lower()
        try:
            protected_kind = (
                classify_protected_for_actor(
                    filename, content, actor_user_id=actor_user_id,
                    policy_classifier=self._policy_protection_classifier,
                ) if self._policy_protection_classifier is not None else
                classify_protected_container(filename, content)
            )
        except Exception:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="protected_source_classification_failed",
                extractor_name="canonical-protected-source",
                extractor_version=self._version,
            )
        if ((protected_kind is ProtectedSourceKind.COMPANY_POLICY_PROTECTED)
                != (expected_protected_kind is ProtectedSourceKind.COMPANY_POLICY_PROTECTED)):
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="protected_source_classification_changed",
                extractor_name="canonical-protected-source",
                extractor_version=self._version,
            )
        if protected_kind is ProtectedSourceKind.COMPANY_POLICY_PROTECTED and extension not in _RICH_EXTENSIONS:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="protected_source_unsupported_format",
                extractor_name="canonical-protected-source",
                extractor_version=self._version,
            )
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
                extractor_version=self._version,
            )
        use_owner_pdf = (protected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
                         and self._protected_pdf_opener is not None)
        if (protected_kind is not None and self._protected_source_opener is None
                and (protected_kind is not ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
                     or self._protected_pdf_opener is None)):
            return ExtractedPublicationContent(
                markdown=None,
                failure_code="protected_source_requires_authorized_opener",
                extractor_name="canonical-protected-source",
                extractor_version=self._version,
            )

        if (protected_kind is not None and self._protected_source_opener is not None
                and not use_owner_pdf):
            if not isinstance(actor_user_id, str) or not actor_user_id.strip():
                return ExtractedPublicationContent(
                    markdown=None,
                    failure_code="protected_source_actor_required",
                    extractor_name="canonical-protected-source",
                    extractor_version="protected-snapshot-source-v1",
                )
            try:
                snapshot = self._protected_source_opener.open_for_actor(
                    filename, content, protected_kind,
                    actor_user_id=actor_user_id,
                )
                if type(snapshot) is not AuthorizedSourceSnapshot:
                    raise ValueError("The approved opener returned an invalid source snapshot.")
                if (snapshot.export_permitted is False
                        and snapshot.original_sha256 == sha256(content).hexdigest()
                        and snapshot.source_kind is protected_kind):
                    raise PermissionError("The approved opener disallowed source export.")
                if snapshot.location_kind not in _PROTECTED_LOCATION_KINDS[extension]:
                    raise ProtectedSourceSnapshotError(
                        "The authorized source locations do not match the source format."
                    )
                markdown = snapshot.checked_markdown(
                    original=content, expected_kind=protected_kind,
                )
                provenance = snapshot.provenance()
                warnings = ("authorized_protected_open", "provider_coverage_reported")
                if provenance.visual_coverage == "not_proven":
                    warnings += ("visual_coverage_not_proven",)
                return ExtractedPublicationContent(
                    markdown=markdown,
                    failure_code=None,
                    extractor_name="canonical-authorized-protected-snapshot",
                    extractor_version="protected-snapshot-source-v1",
                    warnings=warnings,
                    protected_provenance=provenance,
                )
            except PermissionError:
                return ExtractedPublicationContent(
                    markdown=None,
                    failure_code="protected_source_permission_denied",
                    extractor_name="canonical-protected-source",
                    extractor_version="protected-snapshot-source-v1",
                )
            except Exception:
                return ExtractedPublicationContent(
                    markdown=None,
                    failure_code="protected_source_open_or_extraction_failed",
                    extractor_name="canonical-protected-source",
                    extractor_version="protected-snapshot-source-v1",
                )

        extracted = None
        roots: tuple[Path, ...] = ()
        try:
            source_bytes = content
            if protected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION:
                assert self._protected_pdf_opener is not None
                opened = self._protected_pdf_opener.open(filename, content)
                if (opened.original_sha256 != sha256(content).hexdigest()
                        or opened.expected_pages != opened.observed_pages):
                    raise ValueError("The authorized source identity or coverage changed.")
                source_bytes = opened.derivative_pdf
            extracted = extract_document(
                filename,
                source_bytes,
                allow_configured_converters=False,
                include_format_label=not self._source_only,
                **({"bounded_table_labels": True} if self._bounded_table_labels else {}),
                # Only the durable, actor-bound full-page visual preview opts
                # in. Ordinary/text previews retain their embedded asset path.
                **({"defer_pdf_embedded_images": True} if extension == ".pdf"
                   and self._source_only and allow_unexamined_protected_pdf_visual
                   and protected_kind is None else {}),
            )
            roots = _cleanup_roots(extracted.metadata)
            markdown = extracted.markdown
            if not isinstance(markdown, str):
                raise ValueError("empty_extracted_document")
            if (extension == ".pdf" and protected_kind is None
                    and allow_unexamined_protected_pdf_visual and not markdown.strip()):
                page_count = extracted.metadata.get("pdf_page_count")
                if (type(page_count) is not int or page_count < 1
                        or extracted.metadata.get("pdf_text_pages") != []
                        or (not extracted.images and extracted.metadata.get("pdf_images_deferred") is not True)):
                    raise ValueError("The image-only PDF lacks a visual source inventory.")
                # The visual workflow renders every page again from the retained
                # original. This is metadata, not surrogate source prose.
                markdown = "[Visual-only PDF: no extractable text; page images require authoring.]\n"
            if protected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION:
                text_pages = extracted.metadata.get("pdf_text_pages")
                all_pages = set(range(1, opened.expected_pages + 1))
                if (extracted.metadata.get("pdf_page_count") != opened.expected_pages
                        or type(text_pages) is not list
                        or any(type(page) is not int for page in text_pages)
                        or text_pages != sorted(set(text_pages))
                        or not set(text_pages) <= all_pages):
                    raise ValueError("The authorized PDF extraction omitted a page.")
                pages_without_text = all_pages - set(text_pages)
                if pages_without_text and (not allow_unexamined_protected_pdf_visual
                        or not pages_without_text <= set(opened.visual_pages)):
                    raise ValueError("A protected PDF page lacks text and visual coverage.")
                if not markdown.strip() and pages_without_text == all_pages:
                    # Metadata only, never source prose. The registered visual
                    # authoring job must examine every page before publication.
                    markdown = "[Visual-only PDF: no extractable text; page images require authoring.]\n"
            if not markdown.strip():
                raise ValueError("empty_extracted_document")
            if protected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION:
                # Embedded images are not the full visual inventory: a vector
                # drawing or annotation can encode the only direction of a
                # relationship while pypdf still extracts every text label.
                # This text-only lane cannot claim such a source is complete.
                if ((opened.visual_pages or extracted.images)
                        and not allow_unexamined_protected_pdf_visual):
                    return ExtractedPublicationContent(
                        markdown=None,
                        failure_code="protected_source_visual_analysis_required",
                        extractor_name="canonical-protected-source",
                        extractor_version="protected-pdf-source-v1",
                    )
                if extracted.images and not opened.visual_pages:
                    raise ValueError("The protected PDF visual inventory is incomplete.")
            assets = _read_assets(extracted.images, roots=roots)
            markdown = markdown.rstrip()
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
            if extracted.metadata.get("xlsx_chart_visual_coverage") == "metadata_only":
                warnings += ("chart_visual_coverage_not_proven",)
            if extracted.metadata.get("pdf_images_deferred") is True:
                warnings += ("pdf_embedded_assets_deferred_to_page_visual",)
            if protected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION:
                warnings += ("authorized_owner_open", "visual_coverage_not_proven")
                if not text_pages:
                    warnings += ("source_text_absent_visual_only",)
            elif extension == ".pdf" and not extracted.metadata.get("pdf_text_pages"):
                warnings += ("source_text_absent_visual_only", "visual_coverage_not_proven")
            provenance = None
            if protected_kind is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION:
                provenance = ProtectedExtractionProvenance(
                    original_sha256=opened.original_sha256,
                    source_kind=protected_kind,
                    opening_method=opened.opening_method,
                    extraction_method="pypdf-source-text-v1",
                    location_kind="page",
                    coverage=tuple(
                        ProtectedLocationCoverage(
                            page, "complete", page in opened.visual_pages, False
                        )
                        for page in range(1, opened.expected_pages + 1)
                    ),
                    visual_coverage="not_proven",
                    derivative_sha256=opened.derivative_sha256,
                )
            return ExtractedPublicationContent(
                markdown=markdown,
                failure_code=None,
                extractor_name=("canonical-approved-owner-pdf" if protected_kind
                                is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
                                else f"canonical-{extracted.extractor}"[:120]),
                extractor_version=("protected-pdf-source-v1" if protected_kind
                                   is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
                                   else self._version),
                warnings=warnings,
                assets=assets,
                protected_provenance=provenance,
            )
        except Exception:
            return ExtractedPublicationContent(
                markdown=None,
                failure_code=("protected_source_open_or_extraction_failed" if protected_kind
                              is not None else "document_extraction_failed"),
                extractor_name=("canonical-protected-source" if protected_kind
                                is not None else "canonical-rich"),
                extractor_version=("protected-pdf-source-v1" if protected_kind
                                   is ProtectedSourceKind.PDF_STANDARD_ENCRYPTION
                                   else self._version),
            )
        finally:
            if extracted is not None and not roots:
                roots = _cleanup_roots(extracted.metadata)
            _cleanup(roots)

    def extract_for_actor(
        self, content: bytes, *, media_type: str,
        original_filename: str | None, actor_user_id: str,
        expected_protected_kind: ProtectedSourceKind | None = None,
    ) -> ExtractedPublicationContent:
        """Bind an approved protected opener to the durable job's actor."""
        claim = ({"expected_protected_kind": expected_protected_kind}
                 if expected_protected_kind is not None else {})
        return self.extract(
            content, media_type=media_type,
            original_filename=original_filename,
            actor_user_id=actor_user_id,
            **claim,
        )


__all__ = ["CanonicalRichPublicationExtractor"]
