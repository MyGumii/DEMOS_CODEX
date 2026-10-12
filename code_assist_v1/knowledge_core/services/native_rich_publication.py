"""Opt-in OOXML extraction from typed source blocks, not legacy table labels.

No LLM, converter or slide-rendering call. Main-story/declared-slide text and
native tables are supported; this is not a visual/full-document fidelity claim.
The old rich adapter remains the exact fallback for non-OOXML formats.
"""

from dataclasses import dataclass
from hashlib import sha256
from html import escape
import io
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

from ... import document_extractors as legacy
from ...document_source_tables import (
    MAX_RENDER_BYTES, NativeTable, NativeText, NativeTableDocument, SourceTableError,
    extract_native_table_document, render_native_tables,
)
from ..office_hybrid_source import PreparedOfficeHybridSource
from ..repositories.native_table_artifacts import NATIVE_EXTRACTOR_VERSION
from ..native_wiki_authoring import NativeAssetReference
from .publication import ExtractedPublicationContent
from .rich_publication import (
    CanonicalRichPublicationExtractor, _cleanup, _cleanup_roots,
    _read_assets, _safe_label,
)


_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_PRESENTATION = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"


class NativeVisualAnalysisRequired(SourceTableError):
    """The active source has visual facts that native text/table writing cannot see."""


def _requires_visual_analysis(archive: zipfile.ZipFile, names: set[str], kind: str) -> bool:
    """Inspect active main-story/slide XML, not orphaned media package entries.

    This is intentionally a conservative publication gate, not image recognition.
    Embedded image bytes remain preserved by extraction but are never supplied to
    the native writing model, so publishing a complete Wiki would be misleading.
    """
    if kind == "docx":
        parts = ("word/document.xml",)
    else:
        parts = tuple(name for name in names
            if re.fullmatch(r"ppt/slides/slide[1-9][0-9]*\.xml", name))
    for part in parts:
        if part not in names:
            continue
        root = ET.fromstring(archive.read(part))
        for element in root.iter():
            if kind == "docx":
                if element.tag in {_WORD + "drawing", _WORD + "pict", _WORD + "object"}:
                    return True
            else:
                if element.tag in {_PRESENTATION + "pic", _PRESENTATION + "oleObj",
                        _PRESENTATION + "cxnSp", _DRAWING + "blip"}:
                    return True
                if element.tag == _PRESENTATION + "graphicFrame" and not any(
                        child.tag == _DRAWING + "tbl" for child in element.iter()):
                    return True
    return False


def native_source_markdown(document) -> str:
    """Preserve source order and emit each owned caption/table exactly once."""
    tables = dict(render_native_tables(document))
    owned_captions = {
        block.caption.location for block in document.blocks
        if type(block) is NativeTable and block.caption is not None
    }
    blocks = []
    for block in document.blocks:
        if type(block) is NativeTable:
            blocks.append(tables[block.table_id])
        elif type(block) is NativeText:
            if block.location in owned_captions or not block.text:
                continue
            # Source characters must not introduce HTML or Markdown headings.
            text = escape(block.text, quote=False)
            text = re.sub(r"([\\`*_{}\[\]#+.!|])", r"\\\1", text)
            if block.role == "heading":
                text = "#" * block.level + " " + text
            blocks.append(text)
        else:
            raise SourceTableError("Unsupported native source block")
    result = "\n\n".join(blocks) + "\n"
    if not result.strip() or len(result.encode("utf-8")) > MAX_RENDER_BYTES:
        raise SourceTableError("Native source rendering is empty or oversized")
    return result


@dataclass(frozen=True, slots=True)
class NativeSourceExtraction:
    """One raw reconstruction, including ordered labels before Markdown escaping.

    This is not authorization or a storage seal. Consumers must compare it with
    the canonical extraction, sidecar and complete retained asset inventory.
    """

    document: NativeTableDocument
    extraction: ExtractedPublicationContent
    asset_references: tuple[NativeAssetReference, ...]


def native_asset_label(kind: str, ordinal: int) -> str:
    """Frozen extractor-owned display label, distinct from its CAS logical name."""
    if kind not in {"docx", "pptx"} or type(ordinal) is not int or not 1 <= ordinal <= 64:
        raise SourceTableError("Invalid native asset label identity")
    prefix = "Document image" if kind == "docx" else "PPTX embedded image"
    return f"{prefix} {ordinal}"


class CanonicalNativePublicationExtractor:
    """The v2 preview's trusted source adapter; never used by the v1 worker."""

    def __init__(self):
        self._fallback = CanonicalRichPublicationExtractor(
            source_only=True, bounded_table_labels=True)

    def extract(self, content: bytes, *, media_type: str,
                original_filename: str | None) -> ExtractedPublicationContent:
        filename = Path(original_filename).name if original_filename else ""
        kind = Path(filename).suffix.lower().removeprefix(".")
        if kind not in {"docx", "pptx"}:
            return self._fallback.extract(content, media_type=media_type,
                original_filename=original_filename)
        try:
            return self.extract_native_source(content, media_type=media_type,
                original_filename=original_filename).extraction
        except NativeVisualAnalysisRequired:
            return ExtractedPublicationContent(
                markdown=None, failure_code="native_visual_analysis_required",
                extractor_name="canonical-native-" + kind,
                extractor_version=NATIVE_EXTRACTOR_VERSION,
            )
        except Exception:
            # No parser detail, file path or source prose enters the failure row.
            return ExtractedPublicationContent(
                markdown=None, failure_code="native_document_extraction_failed",
                extractor_name="canonical-native-" + kind,
                extractor_version=NATIVE_EXTRACTOR_VERSION,
            )

    def extract_native_source(self, content: bytes, *, media_type: str,
            original_filename: str, verified_visual_source: PreparedOfficeHybridSource | None = None,
            actor_user_id: str | None = None) -> NativeSourceExtraction:
        """Strict source preparation outside SQLite; no legacy/raw-text fallback."""
        filename = Path(original_filename).name if original_filename else ""
        kind = Path(filename).suffix.lower().removeprefix(".")
        if kind not in {"docx", "pptx"}:
            raise SourceTableError("Native authoring requires DOCX or PPTX")
        if verified_visual_source is not None:
            if (type(verified_visual_source) is not PreparedOfficeHybridSource
                    or type(actor_user_id) is not str or not actor_user_id
                    or verified_visual_source.render.original_filename != filename):
                raise SourceTableError("Exact authorized Office visual evidence is required")
            verified_visual_source.checked(actor_user_id=actor_user_id, original=content)
        roots = ()
        metadata = {}
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(legacy._validate_ooxml_archive(archive))
                if (_requires_visual_analysis(archive, names, kind)
                        and verified_visual_source is None):
                    raise NativeVisualAnalysisRequired(
                        "Active native visual content requires an examined visual source.")
            document = extract_native_table_document(filename, content)
            if (verified_visual_source is not None
                    and document.canonical_json() != verified_visual_source.native.canonical_json()):
                raise SourceTableError("Office native structure differs from its visual source")
            markdown = native_source_markdown(document).rstrip()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = legacy._validate_ooxml_archive(archive)
                prefix = "word/media/" if kind == "docx" else "ppt/media/"
                images, cleanup_dirs = legacy._extract_zip_media(
                    archive, names, prefix, kind + "_image", kind + "-image",
                    "Document image" if kind == "docx" else "PPTX embedded image",
                    bounded_ooxml=True,
                )
                metadata = {"cleanup_dirs": cleanup_dirs}
            roots = _cleanup_roots(metadata)
            assets = _read_assets(images, roots=roots)
            if any(row.get("label") != native_asset_label(kind, ordinal)
                    for ordinal, row in enumerate(images, 1)):
                raise SourceTableError("Native asset display label policy changed")
            asset_references = tuple(NativeAssetReference(
                sha256(asset.content).hexdigest(), native_asset_label(kind, ordinal))
                for ordinal, (asset, row) in enumerate(zip(assets, images, strict=True), 1))
            if assets:
                references = ["## Embedded assets"]
                for ordinal, (asset, row) in enumerate(zip(assets, images, strict=True), 1):
                    references.append(
                        f"![{_safe_label(row.get('label'), ordinal=ordinal)}]"
                        f"(asset://sha256/{sha256(asset.content).hexdigest()})")
                markdown += "\n\n" + "\n\n".join(references)
            if len(markdown.encode("utf-8")) > MAX_RENDER_BYTES:
                raise SourceTableError("Native source rendering is oversized")
            extraction = ExtractedPublicationContent(
                markdown=markdown + "\n", failure_code=None,
                extractor_name="canonical-native-" + kind,
                extractor_version=NATIVE_EXTRACTOR_VERSION,
                warnings=(("native_source_coverage_limited",)
                    if verified_visual_source is None else
                    ("native_visual_evidence_bound_unpublished",)), assets=assets,
            )
            return NativeSourceExtraction(document, extraction, asset_references)
        finally:
            _cleanup(roots or _cleanup_roots(metadata))


__all__ = ["CanonicalNativePublicationExtractor", "NativeSourceExtraction",
    "native_asset_label", "native_source_markdown"]
