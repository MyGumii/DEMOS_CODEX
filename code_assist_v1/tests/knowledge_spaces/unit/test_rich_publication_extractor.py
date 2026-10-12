from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from code_assist_v1.knowledge_core.services.rich_publication import (
    CanonicalRichPublicationExtractor,
)


pytestmark = pytest.mark.unit


def _docx_with_image() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p><w:r><w:t>Canonical rich body</w:t></w:r></w:p></w:body>
</w:document>""",
        )
        archive.writestr("word/media/diagram.png", b"\x89PNG\r\n\x1a\nfixture")
    return output.getvalue()


def _pptx_fixture() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "ppt/slides/slide1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody>
    <a:p><a:r><a:t>Canonical slide</a:t></a:r></a:p>
    <a:p><a:r><a:t>Bounded body</a:t></a:r></a:p>
  </p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>""",
        )
    return output.getvalue()


def _xlsx_fixture() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <sheetData><row r="1"><c r="A1"><v>42</v></c><c r="B1"><v>84</v></c></row></sheetData>
</worksheet>""",
        )
    return output.getvalue()


def _pdf_fixture() -> bytes:
    from pypdf import PdfWriter

    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


def test_docx_becomes_markdown_with_content_addressed_embedded_child() -> None:
    outcome = CanonicalRichPublicationExtractor().extract(
        _docx_with_image(),
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "wordprocessingml.document"
        ),
        original_filename="source.docx",
    )
    assert outcome.failure_code is None
    assert outcome.markdown is not None
    assert "Canonical rich body" in outcome.markdown
    assert len(outcome.assets) == 1
    asset = outcome.assets[0]
    assert asset.logical_name == "asset-001-docx_image_001.png"
    assert asset.media_type == "image/png"
    digest = hashlib.sha256(asset.content).hexdigest()
    assert f"(asset://sha256/{digest})" in outcome.markdown
    assert "embedded_assets_not_published" not in outcome.warnings


def test_rich_extraction_failures_are_sanitized() -> None:
    outcome = CanonicalRichPublicationExtractor().extract(
        b"not a zip",
        media_type="application/octet-stream",
        original_filename="private-customer-name.docx",
    )
    assert outcome.markdown is None
    assert outcome.assets == ()
    assert outcome.failure_code == "document_extraction_failed"
    assert "private-customer-name" not in repr(outcome)


def test_markdown_keeps_the_frozen_utf8_path() -> None:
    outcome = CanonicalRichPublicationExtractor().extract(
        b"# Existing markdown\n",
        media_type="application/octet-stream",
        original_filename="notes.md",
    )
    assert outcome.failure_code is None
    assert outcome.markdown == "# Existing markdown\n"
    assert outcome.extractor_name == "utf8-text"
    assert outcome.assets == ()


def test_plain_text_keeps_the_frozen_utf8_path() -> None:
    outcome = CanonicalRichPublicationExtractor().extract(
        "일반 텍스트\nSecond line\n".encode("utf-8"),
        media_type="text/plain",
        original_filename="notes.txt",
    )
    assert outcome.failure_code is None
    assert outcome.markdown == "일반 텍스트\nSecond line\n"
    assert outcome.extractor_name == "utf8-text"
    assert outcome.assets == ()


@pytest.mark.parametrize(
    ("filename", "media_type", "content_factory", "expected_text", "extractor"),
    (
        (
            "slides.pptx",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            _pptx_fixture,
            "Canonical slide",
            "canonical-pptx-xml",
        ),
        (
            "workbook.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            _xlsx_fixture,
            "| 42 | 84 |",
            "canonical-xlsx-xml",
        ),
        (
            "source.pdf",
            "application/pdf",
            _pdf_fixture,
            "# Imported PDF",
            "canonical-pypdf",
        ),
    ),
)
def test_native_rich_formats_produce_versioned_canonical_markdown(
    monkeypatch,
    filename: str,
    media_type: str,
    content_factory,
    expected_text: str,
    extractor: str,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_RENDER_PPTX_SLIDES", "false")
    monkeypatch.setenv("CODE_ASSIST_RENDER_PDF_PAGES", "false")
    outcome = CanonicalRichPublicationExtractor().extract(
        content_factory(),
        media_type=media_type,
        original_filename=filename,
    )
    assert outcome.failure_code is None
    assert outcome.markdown is not None
    assert expected_text in outcome.markdown
    assert outcome.extractor_name == extractor
    assert outcome.extractor_version == "rich-v1"
