"""Local-only Word COM probe for a synthetic password-protected DOCX.

This is a desktop acceptance probe, not a web-worker or company DRM opener.
It accounts for each rendered page and rejects cross-page text/table spans or
visual/secondary stories rather than claiming incomplete content is a Wiki.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import os
from pathlib import Path
import shutil
import sys
import tempfile

from pypdf import PdfReader

from code_assist_v1.knowledge_core.protected_source_snapshot import (
    AuthorizedSourceLocation,
    AuthorizedSourceSnapshot,
)
from code_assist_v1.knowledge_core.protected_sources import ProtectedSourceKind
from code_assist_v1.knowledge_core.services.rich_publication import (
    CanonicalRichPublicationExtractor,
)


def _cell_text(cell: object) -> str:
    return str(cell.Range.Text).rstrip("\r\x07").strip()


def _table_markdown(table: object) -> str:
    rows = [[_cell_text(cell) for cell in row.Cells] for row in table.Rows]
    if not rows or not rows[0] or any(len(row) != len(rows[0]) for row in rows):
        raise ValueError("The Word table grid could not be fully recovered.")
    header = "| " + " | ".join(rows[0]) + " |"
    separator = "| " + " | ".join("---" for _ in rows[0]) + " |"
    data = ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join((header, separator, *data))


def _page_of(doc: object, position: int, page_count: int) -> int:
    number = int(doc.Range(position, position).Information(3))
    if not 1 <= number <= page_count:
        raise ValueError("Word returned a location outside the rendered pages.")
    return number


def _ordered_locations(doc: object, page_count: int) -> tuple[tuple[str, ...], int]:
    if type(page_count) is not int or not 1 <= page_count <= 500:
        raise ValueError("The Word PDF page inventory is invalid.")
    tables = tuple(doc.Tables)
    spans = tuple((int(table.Range.Start), int(table.Range.End)) for table in tables)
    units: list[list[tuple[int, str]]] = [[] for _ in range(page_count)]
    for paragraph in doc.Paragraphs:
        start = int(paragraph.Range.Start)
        if any(low <= start < high for low, high in spans):
            continue
        value = str(paragraph.Range.Text).strip("\r\x07 ")
        if value:
            end = int(paragraph.Range.End)
            first = _page_of(doc, start, page_count)
            last = _page_of(doc, end - 1, page_count)
            if first != last:
                raise ValueError("A paragraph crosses rendered Word pages.")
            units[first - 1].append((start, value))
    for table in tables:
        start, end = int(table.Range.Start), int(table.Range.End)
        first = _page_of(doc, start, page_count)
        last = _page_of(doc, end - 1, page_count)
        if first != last:
            raise ValueError("A table crosses rendered Word pages.")
        units[first - 1].append((start, _table_markdown(table)))
    return tuple("\n\n".join(value for _, value in sorted(page))
        for page in units), len(tables)


def _reject_unaccounted_word_content(doc: object) -> None:
    """Release secondary-story COM proxies before closing the document."""
    if int(doc.Shapes.Count) or int(doc.InlineShapes.Count):
        raise ValueError("Visual objects require examined visual evidence.")
    if int(doc.Footnotes.Count) or int(doc.Endnotes.Count):
        raise ValueError("Notes require separate source accounting.")
    if int(doc.Comments.Count) or int(doc.Revisions.Count):
        raise ValueError("Comments or revisions require separate source accounting.")
    for section in doc.Sections:
        for story in (*tuple(section.Headers), *tuple(section.Footers)):
            if (str(story.Range.Text).strip("\r\x07 ")
                    or int(story.Shapes.Count)):
                raise ValueError("Headers or footers require separate source accounting.")


class _FixtureWordOpener:
    def __init__(
        self, path: Path, password: str, render_pdf: Path | None = None,
        *, allowed_actor: str = "local-fixture-operator",
    ) -> None:
        self.path = path
        self.password = password
        self.render_pdf = render_pdf
        self.allowed_actor = allowed_actor
        self.table_count = 0
        self.page_count = 0

    def open_for_actor(
        self, filename: str, content: bytes, kind: ProtectedSourceKind,
        *, actor_user_id: str,
    ) -> AuthorizedSourceSnapshot:
        if (
            actor_user_id != self.allowed_actor
            or
            filename != self.path.name
            or content != self.path.read_bytes()
            or kind is not ProtectedSourceKind.OFFICE_ENCRYPTED_PACKAGE_CANDIDATE
        ):
            raise ValueError("The authorized source identity changed.")

        import pythoncom  # isolated validation environment only
        from win32com.client import DispatchEx

        app = None
        doc = None
        pythoncom.CoInitialize()
        try:
            app = DispatchEx("Word.Application")
            app.Visible = False
            app.DisplayAlerts = 0
            app.AutomationSecurity = 3
            doc = app.Documents.Open(
                FileName=str(self.path), ConfirmConversions=False, ReadOnly=True,
                AddToRecentFiles=False, PasswordDocument=self.password,
                Visible=False, OpenAndRepair=False, NoEncodingDialog=True,
            )
            _reject_unaccounted_word_content(doc)
            with tempfile.TemporaryDirectory(prefix="sb-protected-word-probe-") as folder:
                rendered = Path(folder) / "rendered.pdf"
                doc.ExportAsFixedFormat(str(rendered), 17)  # wdExportFormatPDF
                derivative = rendered.read_bytes()
                self.page_count = len(PdfReader(rendered, strict=True).pages)
                locations, self.table_count = _ordered_locations(doc, self.page_count)
                if not any(location.strip() for location in locations):
                    raise ValueError("Word returned no source content.")
                if self.render_pdf is not None:
                    if self.render_pdf.exists():
                        raise FileExistsError("The fixture render target already exists.")
                    self.render_pdf.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(rendered, self.render_pdf)
        finally:
            try:
                if doc is not None:
                    doc.Close(False)
            finally:
                doc = None
                try:
                    if app is not None:
                        app.Quit()
                finally:
                    app = None
                    pythoncom.CoUninitialize()

        return AuthorizedSourceSnapshot(
            original_sha256=sha256(content).hexdigest(),
            source_kind=kind,
            opening_method="local-word-com-password-fixture-v1",
            extraction_method="word-com-ordered-text-table-fixture-v1",
            location_kind="derived_page",
            expected_locations=self.page_count,
            locations=tuple(AuthorizedSourceLocation(
                ordinal, markdown, confirmed_blank=not markdown.strip(),
            ) for ordinal, markdown in enumerate(locations, 1)),
            derivative_sha256=sha256(derivative).hexdigest(),
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--password-env", default="SB_TEST_PROTECTED_WORD_PASSWORD")
    parser.add_argument("--render-pdf", type=Path)
    args = parser.parse_args()
    path = args.path.resolve(strict=True)
    password = os.environ.get(args.password_env, "")
    if not password:
        raise ValueError("The test password environment variable is empty.")

    opener = _FixtureWordOpener(
        path, password,
        args.render_pdf.resolve(strict=False) if args.render_pdf else None,
    )
    result = CanonicalRichPublicationExtractor(
        source_only=True, bounded_table_labels=True,
        protected_source_opener=opener,
    ).extract(
        path.read_bytes(),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        original_filename=path.name,
        actor_user_id="local-fixture-operator",
    )
    if result.failure_code is not None or result.markdown is None:
        print(f"extraction_failed={result.failure_code}", file=sys.stderr)
        return 1
    for marker in ("WORD-CANARY-10521", "14.2 A", "3.8 mm/s", "90 seconds"):
        if marker not in result.markdown:
            raise AssertionError(f"The fixture marker was lost: {marker}")
    if opener.table_count != 1 or opener.page_count != 1:
        raise AssertionError("The fixture page or table inventory changed.")
    provenance = result.protected_provenance
    if provenance is None or len(provenance.coverage) != 1:
        raise AssertionError("The protected extraction has no location provenance.")
    print(
        f"extraction=complete pages={opener.page_count} tables={opener.table_count} "
        f"source_chars={len(result.markdown)} source_sha256={provenance.original_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
