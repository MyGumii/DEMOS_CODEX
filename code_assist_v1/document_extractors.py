"""
Document extraction framework for Second Brain imports.

Supported sources are normalized into markdown plus optional image artifacts.
Native extractors are used first. Optional local converters can be configured
for richer Office rendering or custom import pipelines.
"""
from __future__ import annotations

import base64
import html
import io
import json
import os
import platform
import posixpath
import re
import shutil
import shlex
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".rst", ".adoc",
    ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".toml",
    ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".ini", ".cfg", ".conf", ".log", ".sql",
}
CODE_TEXT_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".vue", ".svelte",
    ".java", ".kt", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".hh",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bash", ".zsh", ".ps1", ".bat",
    ".r", ".m", ".scala", ".swift", ".lua", ".pl",
}
NOTEBOOK_EXTENSIONS = {".ipynb"}
OFFICE_EXTENSIONS = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
LEGACY_OFFICE_EXTENSIONS = {".doc", ".ppt", ".xls"}
SUPPORTED_SOURCE_EXTENSIONS = TEXT_EXTENSIONS | CODE_TEXT_EXTENSIONS | NOTEBOOK_EXTENSIONS | OFFICE_EXTENSIONS | {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".emf", ".wmf"}

# Native OOXML parsing is intentionally bounded.  These are rejection limits,
# never truncation limits: a document over a limit fails extraction rather than
# producing a plausible-looking prefix Wiki.
_OOXML_MAX_ENTRIES = 4_096
_OOXML_MAX_PART_BYTES = 32 * 1024 * 1024
_OOXML_MAX_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
_EXCEL_MAX_SOURCE_MARKDOWN_BYTES = 2_000_000
_XLSX_DENSE_GRID_MAX_COLUMNS = 256
_XLSX_DENSE_GRID_MAX_CELLS = 200_000
_XLSX_CELL_REFERENCE = re.compile(r"([A-Z]{1,3})([1-9][0-9]*)\Z")

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_W = f"{{{_WORD_NS}}}"
_A = f"{{{_DRAWING_NS}}}"
_P = f"{{{_PRESENTATION_NS}}}"
_OOXML_FALSE = frozenset({"0", "false", "off", "no"})
_DOCX_CONTENT_WRAPPERS = frozenset({
    _W + "customXml", _W + "sdt", _W + "sdtContent", _W + "ins",
    _W + "moveFrom", _W + "moveTo",
})


@dataclass
class ExtractionResult:
    markdown: str
    extractor: str
    filename: str
    warnings: list[str] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def extract_document(
    filename: str,
    raw: bytes,
    *,
    allow_configured_converters: bool = True,
    include_format_label: bool = True,
    bounded_table_labels: bool = False,
    defer_pdf_embedded_images: bool = False,
) -> ExtractionResult:
    """Extract a source document into markdown plus optional image artifacts."""
    if type(include_format_label) is not bool:
        raise TypeError("include_format_label must be boolean")
    if type(bounded_table_labels) is not bool:
        raise TypeError("bounded_table_labels must be boolean")
    if type(defer_pdf_embedded_images) is not bool:
        raise TypeError("defer_pdf_embedded_images must be boolean")
    if defer_pdf_embedded_images and (allow_configured_converters or include_format_label
                                    or Path(filename).suffix.lower() != ".pdf"):
        raise ValueError("Deferred PDF images require native source-only PDF extraction")
    if bounded_table_labels and (include_format_label or allow_configured_converters):
        raise ValueError("Bounded table labels require native source-only extraction")
    if not include_format_label and allow_configured_converters:
        raise ValueError("Source-only extraction requires native-only conversion")
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_SOURCE_EXTENSIONS:
        raise ValueError(f"Unsupported source file extension: {ext}")

    try:
        return _extract_native(filename, raw, include_format_label=include_format_label,
            bounded_table_labels=bounded_table_labels,
            **({"defer_pdf_embedded_images": True} if defer_pdf_embedded_images else {}))
    except Exception as native_error:
        configured = _configured_converters() if allow_configured_converters else []
        if configured:
            try:
                return _extract_with_converter_chain(
                    filename,
                    raw,
                    initial_warning=f"Native extractor failed: {native_error}",
                )
            except Exception:
                pass
        raise RuntimeError(f"Document extraction failed: {native_error}") from native_error


def _extract_native(filename: str, raw: bytes, *, include_format_label: bool = True,
                    bounded_table_labels: bool = False,
                    defer_pdf_embedded_images: bool = False) -> ExtractionResult:
    ext = Path(filename).suffix.lower()
    if ext in NOTEBOOK_EXTENSIONS:
        markdown, images, metadata = _extract_ipynb_markdown(filename, raw)
        return ExtractionResult(markdown, "ipynb-json", filename, images=images, metadata=metadata)
    if ext in CODE_TEXT_EXTENSIONS:
        text = _decode_text(raw)
        return ExtractionResult(_source_text_to_markdown(filename, text, ext), "source-text", filename)
    if ext in TEXT_EXTENSIONS:
        text = _decode_text(raw)
        return ExtractionResult(text, "text", filename)
    if ext in LEGACY_OFFICE_EXTENSIONS:
        raise RuntimeError(
            f"{ext} is a legacy Office format. Configure CODE_ASSIST_DOCUMENT_CONVERTERS=office_com "
            "or convert the file to docx/pptx/xlsx first."
        )
    if ext == ".docx":
        markdown, images, metadata = _extract_docx_xml(raw, include_format_label=include_format_label,
            bounded_table_labels=bounded_table_labels)
        return ExtractionResult(markdown, "docx-xml", filename, images=images, metadata=metadata)
    if ext == ".pptx":
        markdown, images, metadata, warnings = _extract_pptx_xml(filename, raw, include_format_label=include_format_label,
            bounded_table_labels=bounded_table_labels)
        return ExtractionResult(markdown, "pptx-xml", filename, warnings=warnings, images=images, metadata=metadata)
    if ext == ".xlsx":
        markdown, images, metadata = _extract_xlsx_xml(raw, include_format_label=include_format_label)
        warnings = (["XLSX chart metadata was extracted, but visual layout was not analyzed"]
                    if metadata.get("xlsx_chart_count") else [])
        return ExtractionResult(markdown, "xlsx-xml", filename, warnings=warnings,
                                images=images, metadata=metadata)
    if ext == ".pdf":
        markdown, images, metadata, warnings = _extract_pdf_pypdf(raw, include_format_label=include_format_label,
            **({"defer_embedded_images": True} if defer_pdf_embedded_images else {}))
        return ExtractionResult(markdown, "pypdf", filename, warnings=warnings, images=images, metadata=metadata)
    raise ValueError(f"Unsupported source file extension: {ext}")


def _extract_with_converter_chain(filename: str, raw: bytes, initial_warning: str = "") -> ExtractionResult:
    configured = _configured_converters()
    warnings = [initial_warning] if initial_warning else []
    errors: list[str] = []
    for name in configured:
        try:
            if name == "external":
                result = _extract_external_command(filename, raw)
            elif name == "office_com":
                result = _extract_office_com(filename, raw)
            elif name == "native":
                result = _extract_native(filename, raw)
            else:
                continue
            result.warnings = warnings + result.warnings
            return result
        except Exception as e:
            errors.append(f"{name}: {e}")
    raise RuntimeError("; ".join(errors) or "No document converter succeeded")


def _configured_converters() -> list[str]:
    raw = os.environ.get("CODE_ASSIST_DOCUMENT_CONVERTERS", "")
    if not raw.strip():
        return []
    names = [x.strip().lower().replace("-", "_") for x in re.split(r"[,;\s]+", raw) if x.strip()]
    allowed = {"external", "office_com", "native"}
    return [n for n in names if n in allowed]


def _extract_external_command(filename: str, raw: bytes) -> ExtractionResult:
    cmd_template = os.environ.get("CODE_ASSIST_DOCUMENT_EXTRACTOR_CMD", "").strip()
    if not cmd_template:
        raise RuntimeError("CODE_ASSIST_DOCUMENT_EXTRACTOR_CMD is not configured")

    timeout = int(os.environ.get("CODE_ASSIST_DOCUMENT_EXTRACTOR_TIMEOUT", "300") or "300")
    tmp_path = Path(tempfile.mkdtemp(prefix="sb-extract-"))
    try:
        input_path = tmp_path / _safe_filename(filename)
        output_dir = tmp_path / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(raw)

        args = _build_external_args(cmd_template, input_path=input_path, output_dir=output_dir, filename=filename)
        proc = subprocess.run(
            args,
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:1200]
            raise RuntimeError(f"external extractor failed ({proc.returncode}): {detail}")

        markdown = _read_external_markdown(output_dir, proc.stdout)
        images = _collect_output_images(output_dir)
        metadata = _read_external_metadata(output_dir)
        if not markdown.strip():
            raise RuntimeError("external extractor produced no markdown")
        cleanup_dirs = list(metadata.get("cleanup_dirs") or [])
        cleanup_dirs.append(str(tmp_path))
        metadata["cleanup_dirs"] = cleanup_dirs
        return ExtractionResult(
            markdown=markdown,
            extractor=metadata.get("extractor") or "external",
            filename=filename,
            warnings=metadata.get("warnings") or [],
            images=images,
            metadata=metadata,
        )
    except Exception:
        shutil.rmtree(tmp_path, ignore_errors=True)
        raise


def _build_external_args(cmd_template: str, input_path: Path, output_dir: Path, filename: str) -> list[str]:
    values = {
        "input": str(input_path),
        "output_dir": str(output_dir),
        "filename": filename,
    }
    try:
        loaded = json.loads(cmd_template)
        if isinstance(loaded, list):
            return [str(x).format(**values) for x in loaded]
    except json.JSONDecodeError:
        pass
    formatted = cmd_template.format(**values)
    return shlex.split(formatted, posix=(os.name != "nt"))


def _read_external_markdown(output_dir: Path, stdout: str) -> str:
    for name in ("content.md", "document.md", "output.md"):
        path = output_dir / name
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    md_files = sorted(output_dir.rglob("*.md"))
    if md_files:
        return md_files[0].read_text(encoding="utf-8", errors="replace")
    stdout = (stdout or "").strip()
    if stdout.startswith("#") or "\n#" in stdout:
        return stdout
    return ""


def _read_external_metadata(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "metadata.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _collect_output_images(output_dir: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        rel = path.relative_to(output_dir).as_posix()
        out.append({
            "path": str(path),
            "filename": rel,
            "label": path.stem.replace("_", " ").replace("-", " ").strip() or path.name,
            "kind": "image",
        })
    return out


def _extract_office_com(filename: str, raw: bytes) -> ExtractionResult:
    if platform.system().lower() != "windows":
        raise RuntimeError("Office COM extractor is only available on Windows")

    ext = Path(filename).suffix.lower()
    if ext not in OFFICE_EXTENSIONS:
        raise RuntimeError(f"Office COM extractor does not support {ext or 'this file type'}")

    try:
        import pythoncom
        import win32com.client
    except Exception as e:
        raise RuntimeError("pywin32 is required for Office COM extraction") from e

    visible = _env_on("CODE_ASSIST_OFFICE_VISIBLE", False)
    width = int(os.environ.get("CODE_ASSIST_PPT_EXPORT_WIDTH", "1920") or "1920")
    height = int(os.environ.get("CODE_ASSIST_PPT_EXPORT_HEIGHT", "1080") or "1080")

    tmp_path = Path(tempfile.mkdtemp(prefix="sb-office-"))
    try:
        input_path = tmp_path / _safe_filename(filename)
        image_dir = tmp_path / "images"
        image_dir.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(raw)

        pythoncom.CoInitialize()
        try:
            if ext in {".ppt", ".pptx"}:
                markdown, images = _extract_powerpoint_com(win32com.client, input_path, image_dir, visible, width, height)
                extractor = "office-com-powerpoint"
            elif ext in {".doc", ".docx"}:
                markdown, images = _extract_word_com(win32com.client, input_path, image_dir, visible)
                extractor = "office-com-word"
            elif ext in {".xls", ".xlsx"}:
                markdown, images = _extract_excel_com(win32com.client, input_path, image_dir, visible)
                extractor = "office-com-excel"
            else:
                raise RuntimeError(f"Unsupported Office extension: {ext}")
        finally:
            pythoncom.CoUninitialize()

        return ExtractionResult(
            markdown=markdown,
            extractor=extractor,
            filename=filename,
            images=images,
            metadata={"cleanup_dirs": [str(tmp_path)]},
        )
    except Exception:
        shutil.rmtree(tmp_path, ignore_errors=True)
        raise


def _extract_powerpoint_com(
    win32_client: Any,
    input_path: Path,
    image_dir: Path,
    visible: bool,
    width: int,
    height: int,
) -> tuple[str, list[dict[str, Any]]]:
    app = None
    presentation = None
    extraction_error: Exception | None = None
    images: list[dict[str, Any]] = []
    parts = ["# Imported PowerPoint"]
    try:
        app = win32_client.DispatchEx("PowerPoint.Application")
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        if visible:
            app.Visible = True
        presentation = app.Presentations.Open(str(input_path), True, False, visible)
        for idx in range(1, int(presentation.Slides.Count) + 1):
            slide = presentation.Slides(idx)
            texts = _shape_texts(slide.Shapes)
            title = texts[0] if texts else f"Slide {idx}"
            body = "\n".join(f"- {t}" for t in texts[1:])
            parts.append(f"## Slide {idx}: {title}\n\n{body}".strip())
            image_path = image_dir / f"slide_{idx:03d}.png"
            try:
                slide.Export(str(image_path), "PNG", width, height)
                if not image_path.is_file() or image_path.stat().st_size <= 8:
                    raise ValueError("PowerPoint did not produce a valid PNG header")
                with image_path.open("rb") as rendered:
                    if rendered.read(8) != b"\x89PNG\r\n\x1a\n":
                        raise ValueError("PowerPoint did not produce a valid PNG header")
                images.append({
                    "path": str(image_path),
                    "filename": image_path.name,
                    "label": f"Slide {idx}",
                    "kind": "slide",
                })
            except Exception as error:
                raise RuntimeError(
                    f"PowerPoint slide {idx} render failed; no partial source was published"
                ) from error
    except Exception as error:
        extraction_error = error
        raise
    finally:
        cleanup_errors: list[Exception] = []
        if presentation is not None:
            try:
                presentation.Close()
            except Exception as error:
                cleanup_errors.append(error)
        if app is not None:
            try:
                app.Quit()
            except Exception as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            if extraction_error is not None:
                if hasattr(extraction_error, "add_note"):
                    extraction_error.add_note(
                        "PowerPoint COM cleanup also failed: "
                        + "; ".join(str(error) for error in cleanup_errors)
                    )
            else:
                raise RuntimeError(
                    "PowerPoint COM cleanup failed; no source was published"
                ) from cleanup_errors[0]
    return "\n\n".join(parts), images


def _shape_texts(shapes: Any) -> list[str]:
    texts: list[str] = []
    count = int(shapes.Count)
    for idx in range(1, count + 1):
        shape = shapes(idx)
        texts.extend(_single_shape_texts(shape))
    return [re.sub(r"\s+", " ", t).strip() for t in texts if str(t).strip()]


def _single_shape_texts(shape: Any) -> list[str]:
    texts: list[str] = []
    if shape.HasTextFrame and shape.TextFrame.HasText:
        text = str(shape.TextFrame.TextRange.Text or "").strip()
        if text:
            texts.append(text)
    if shape.HasTable:
        table = shape.Table
        for row in range(1, int(table.Rows.Count) + 1):
            cells = [str(table.Cell(row, col).Shape.TextFrame.TextRange.Text or "").strip()
                     for col in range(1, int(table.Columns.Count) + 1)]
            # Preserve blank rows too: their position can carry meaning.
            texts.append(" | ".join(cells))
    if int(shape.Type) == 6:  # msoGroup; non-group GroupItems access raises normally.
        texts.extend(_shape_texts(shape.GroupItems))
    return texts


def _extract_word_com(win32_client: Any, input_path: Path, image_dir: Path, visible: bool) -> tuple[str, list[dict[str, Any]]]:
    app = None
    doc = None
    extraction_error: Exception | None = None
    try:
        app = win32_client.DispatchEx("Word.Application")
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        app.Visible = visible
        doc = app.Documents.Open(str(input_path), False, True, False)
        text = str(doc.Content.Text or "").strip()
        tables = []
        try:
            for t_idx in range(1, int(doc.Tables.Count) + 1):
                table = doc.Tables(t_idx)
                rows = []
                for r_idx in range(1, int(table.Rows.Count) + 1):
                    cells = []
                    for c_idx in range(1, int(table.Columns.Count) + 1):
                        cell_text = str(table.Cell(r_idx, c_idx).Range.Text or "")
                        cell_text = cell_text.replace("\r\x07", "").replace("\x07", "").strip()
                        cells.append(cell_text)
                    if any(cells):
                        rows.append("| " + " | ".join(cells) + " |")
                if rows:
                    tables.append(f"## Table {t_idx}\n\n" + "\n".join(rows))
        except Exception as error:
            raise RuntimeError(
                "Word COM table extraction failed; no partial source was published"
            ) from error
        parts = ["# Imported Word Document", text]
        parts.extend(tables)
        return "\n\n".join(p for p in parts if p.strip()), []
    except Exception as error:
        extraction_error = error
        raise
    finally:
        cleanup_errors: list[Exception] = []
        if doc is not None:
            try:
                doc.Close(False)
            except Exception as error:
                cleanup_errors.append(error)
        if app is not None:
            try:
                app.Quit()
            except Exception as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            if extraction_error is not None:
                if hasattr(extraction_error, "add_note"):
                    extraction_error.add_note(
                        "Word COM cleanup also failed: "
                        + "; ".join(str(error) for error in cleanup_errors)
                    )
            else:
                raise RuntimeError(
                    "Word COM cleanup failed; no source was published"
                ) from cleanup_errors[0]


def _extract_excel_com(win32_client: Any, input_path: Path, image_dir: Path, visible: bool) -> tuple[str, list[dict[str, Any]]]:
    app = None
    workbook = None
    extraction_error: Exception | None = None
    parts = ["# Imported Excel Workbook"]
    try:
        app = win32_client.DispatchEx("Excel.Application")
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        app.Visible = visible
        app.DisplayAlerts = False
        workbook = app.Workbooks.Open(str(input_path), False, True)
        for idx in range(1, int(workbook.Worksheets.Count) + 1):
            sheet = workbook.Worksheets(idx)
            parts.append(f"## Sheet {idx}: {sheet.Name}")
            # A failed worksheet read must not publish a plausible partial Wiki.
            used_range = sheet.UsedRange
            count_nonempty = app.WorksheetFunction.CountA
            expected_nonempty = count_nonempty(used_range)
            parts.append(_excel_com_range_to_markdown(
                used_range, expected_nonempty=expected_nonempty,
                count_nonempty=count_nonempty,
            ))
        return "\n\n".join(p for p in parts if str(p).strip()), []
    except Exception as error:
        extraction_error = error
        raise
    finally:
        cleanup_errors: list[Exception] = []
        if workbook is not None:
            try:
                workbook.Close(False)
            except Exception as error:
                cleanup_errors.append(error)
        if app is not None:
            try:
                app.Quit()
            except Exception as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            if extraction_error is not None:
                if hasattr(extraction_error, "add_note"):
                    extraction_error.add_note(
                        "Excel COM cleanup also failed: "
                        + "; ".join(str(error) for error in cleanup_errors)
                    )
            else:
                raise RuntimeError(
                    "Excel COM cleanup failed; no source was published"
                ) from cleanup_errors[0]


def _excel_com_range_to_markdown(
    used_range: Any, *, expected_nonempty: int | float | None = None,
    count_nonempty: Any = None, _deep_count: bool = False,
) -> str:
    """Read a COM UsedRange in bounded calls without silently dropping cells."""
    first_row = int(used_range.Row)
    first_column = int(used_range.Column)
    row_count = int(used_range.Rows.Count)
    column_count = int(used_range.Columns.Count)
    if not (1 <= first_row <= 1_048_576 and 1 <= first_column <= 16_384):
        raise ValueError("Excel used-range origin is out of bounds")
    if row_count < 1 or column_count < 1:
        raise ValueError("Excel used range has invalid dimensions")
    if first_row + row_count - 1 > 1_048_576 or first_column + column_count - 1 > 16_384:
        raise ValueError("Excel used range exceeds the worksheet boundary")
    # An immense formatting-only UsedRange must fail explicitly rather than
    # monopolize the process or produce a partial Wiki.
    if row_count * column_count > 10_000_000:
        raise ValueError("Excel used range exceeds the safe scan limit; no cells were truncated")
    rows: list[tuple[int, dict[int, str]]] = []
    source_bytes = 0
    for row_offset in range(0, row_count, 128):
        block_rows = min(128, row_count - row_offset)
        block: list[dict[int, str]] = [{} for _ in range(block_rows)]
        for column_offset in range(0, column_count, 64):
            block_columns = min(64, column_count - column_offset)
            chunk = used_range.Cells(row_offset + 1, column_offset + 1).Resize(
                block_rows, block_columns
            )
            matrix = _excel_com_read_matrix(
                used_range, row_offset + 1, column_offset + 1,
                block_rows, block_columns, "Value", initial_chunk=chunk,
                count_nonempty=count_nonempty if _deep_count else None,
            )
            # COM Value returns only a formula's cached result. Read Formula
            # only for blocks that may contain formulas so a successful source
            # snapshot does not silently discard the original expression.
            has_formula = getattr(chunk, "HasFormula", None)
            formula_matrix = (
                _excel_com_read_matrix(
                    used_range, row_offset + 1, column_offset + 1,
                    block_rows, block_columns, "Formula", initial_chunk=chunk,
                )
                if has_formula is not False else None
            )
            for local_row, raw_row in enumerate(matrix):
                for local_column, value in enumerate(raw_row):
                    cell_text = str(value) if value is not None else ""
                    formula = (formula_matrix[local_row][local_column]
                               if formula_matrix is not None else None)
                    if isinstance(formula, str) and formula.startswith("="):
                        cell_text = (
                            f"{formula} (cached: {cell_text})" if cell_text
                            else f"{formula} (no cached result)"
                        )
                    if not cell_text:
                        continue
                    source_bytes += len(cell_text.encode("utf-8"))
                    if source_bytes > _EXCEL_MAX_SOURCE_MARKDOWN_BYTES:
                        raise ValueError("Excel source Markdown exceeds the publication limit; no cells were truncated")
                    block[local_row][first_column + column_offset + local_column] = cell_text
        rows.extend(
            (first_row + row_offset + local_row, cells)
            for local_row, cells in enumerate(block) if cells
        )
    markdown = _xlsx_rows_to_markdown(rows) if rows else ""
    if expected_nonempty is not None:
        if (isinstance(expected_nonempty, bool)
                or not isinstance(expected_nonempty, (int, float))
                or expected_nonempty < 0
                or int(expected_nonempty) != expected_nonempty):
            raise ValueError("Excel COM returned an invalid CountA result")
        actual_nonempty = sum(len(cells) for _, cells in rows)
        if actual_nonempty != int(expected_nonempty):
            if count_nonempty is not None and not _deep_count:
                # Only anomalous sheets pay for per-block CountA. Re-read
                # exact subranges to locate cells that a COM Value call hid.
                return _excel_com_range_to_markdown(
                    used_range, expected_nonempty=expected_nonempty,
                    count_nonempty=count_nonempty, _deep_count=True,
                )
            raise ValueError(
                "Excel COM nonempty cell count differs from CountA; no partial source published"
            )
    if len(markdown.encode("utf-8")) > _EXCEL_MAX_SOURCE_MARKDOWN_BYTES:
        raise ValueError("Excel source Markdown exceeds the publication limit; no cells were truncated")
    return markdown


def _excel_com_read_matrix(
    used_range: Any, row: int, column: int, rows: int, columns: int,
    field: str, *, initial_chunk: Any = None, count_nonempty: Any = None,
) -> tuple[tuple[Any, ...], ...]:
    """Locate a scalar returned for a sparse multi-cell COM range exactly."""
    chunk = (initial_chunk if initial_chunk is not None else
             used_range.Cells(row, column).Resize(rows, columns))
    values = getattr(chunk, field, None)
    expected = None
    if count_nonempty is not None:
        expected = count_nonempty(chunk)
        if (isinstance(expected, bool) or not isinstance(expected, (int, float))
                or expected < 0 or int(expected) != expected):
            raise ValueError("Excel COM returned an invalid CountA result")
    try:
        matrix = _excel_com_matrix(values, rows, columns)
    except ValueError:
        # Some Excel COM providers collapse a range with one populated cell
        # to that scalar. Never assign it to the block's top-left by guess.
        # Split only that anomalous block until the physical cell is known.
        if isinstance(values, (tuple, list)) or rows * columns == 1:
            raise
        matrix = None
    if matrix is not None and expected is not None:
        observed = sum(value is not None and str(value) != ""
                       for values_row in matrix for value in values_row)
        if observed != int(expected):
            matrix = None
    if matrix is not None:
        return matrix
    if rows * columns == 1:
        # A formula with no cached result is still a populated cell.
        formula = getattr(chunk, "Formula", None)
        if expected == 1 and isinstance(formula, str) and formula.startswith("="):
            return ((values,),)
        raise ValueError("Excel COM cell value differs from CountA; no partial source published")
    if rows >= columns and rows > 1:
        upper = rows // 2
        return (
            _excel_com_read_matrix(
                used_range, row, column, upper, columns, field,
                count_nonempty=count_nonempty,
            )
            + _excel_com_read_matrix(
                used_range, row + upper, column, rows - upper, columns, field,
                count_nonempty=count_nonempty,
            )
        )
    left = columns // 2
    first = _excel_com_read_matrix(
        used_range, row, column, rows, left, field,
        count_nonempty=count_nonempty,
    )
    second = _excel_com_read_matrix(
        used_range, row, column + left, rows, columns - left, field,
        count_nonempty=count_nonempty,
    )
    return tuple(a + b for a, b in zip(first, second))


def _excel_com_matrix(values: Any, rows: int, columns: int) -> tuple[tuple[Any, ...], ...]:
    # Excel can return a scalar None for a rectangular range with no values.
    if values is None:
        return ((None,) * columns,) * rows
    if rows == columns == 1 and not isinstance(values, tuple):
        return ((values,),)
    if not isinstance(values, tuple):
        raise ValueError("Excel COM returned an invalid range shape")
    if rows == columns == 1 and len(values) == 1 and not isinstance(values[0], tuple):
        return (values,)
    if rows == 1 and columns > 1 and len(values) == columns and not isinstance(values[0], tuple):
        return (values,)
    if columns == 1 and len(values) == rows and values and not isinstance(values[0], tuple):
        return tuple((value,) for value in values)
    if len(values) != rows:
        raise ValueError("Excel COM returned an invalid range shape")
    if any(not isinstance(row, tuple) or len(row) != columns for row in values):
        raise ValueError("Excel COM returned an invalid range shape")
    return values


def _excel_values_to_markdown(
    values: Any, *, first_row: int = 1, first_column: int = 1
) -> str:
    if values is None:
        return ""
    if not (1 <= first_row <= 1_048_576 and 1 <= first_column <= 16_384):
        raise ValueError("Excel used-range origin is out of bounds")
    raw_rows = (values if values and isinstance(values[0], tuple) else (values,)) if isinstance(values, tuple) else ((values,),)
    rows: list[tuple[int, dict[int, str]]] = []
    for row_offset, raw_row in enumerate(raw_rows):
        row_number = first_row + row_offset
        if row_number > 1_048_576:
            raise ValueError("Excel used range exceeds the worksheet row boundary")
        cells: dict[int, str] = {}
        for column_offset, value in enumerate(raw_row):
            column = first_column + column_offset
            if column > 16_384:
                raise ValueError("Excel used range exceeds the worksheet column boundary")
            if value is not None and str(value):
                cells[column] = str(value)
        if cells:
            rows.append((row_number, cells))
    markdown = _xlsx_rows_to_markdown(rows) if rows else ""
    if len(markdown.encode("utf-8")) > _EXCEL_MAX_SOURCE_MARKDOWN_BYTES:
        raise ValueError("Excel source Markdown exceeds the publication limit; no cells were truncated")
    return markdown


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _source_text_to_markdown(filename: str, text: str, ext: str) -> str:
    language = _language_for_extension(ext)
    safe_text = text.replace("````", "``` `")
    return f"# Source File: {filename}\n\n````{language}\n{safe_text.rstrip()}\n````\n"


def _extract_ipynb_markdown(filename: str, raw: bytes) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    try:
        notebook = json.loads(_decode_text(raw))
    except Exception as e:
        raise ValueError(f"Invalid ipynb JSON: {e}") from e

    metadata = notebook.get("metadata") if isinstance(notebook, dict) else {}
    kernelspec = metadata.get("kernelspec") if isinstance(metadata, dict) else {}
    language = (
        (kernelspec or {}).get("language")
        or ((metadata.get("language_info") or {}).get("name") if isinstance(metadata, dict) else "")
        or "python"
    )
    cells = notebook.get("cells") if isinstance(notebook, dict) else []
    if not isinstance(cells, list):
        raise ValueError("Invalid ipynb: cells must be a list")

    image_dir: Path | None = None
    images: list[dict[str, Any]] = []
    parts = [f"# Imported Notebook: {filename}"]
    for idx, cell in enumerate(cells, start=1):
        if not isinstance(cell, dict):
            continue
        cell_type = str(cell.get("cell_type") or "raw").lower()
        source = _cell_source_text(cell.get("source"))
        outputs = cell.get("outputs")
        if not source.strip() and not outputs:
            continue
        if cell_type == "markdown":
            parts.append(f"## Markdown cell {idx}\n\n{source.strip()}")
        elif cell_type == "code":
            code = source.rstrip().replace("````", "``` `")
            block = [f"## Code cell {idx}", f"````{language}\n{code}\n````"]
            output_text = _ipynb_outputs_text(outputs)
            if output_text:
                block.append(f"### Output cell {idx}\n\n````text\n{output_text}\n````")
            parts.append("\n\n".join(block))
        else:
            raw_text = source.rstrip().replace("````", "``` `")
            parts.append(f"## {cell_type.title()} cell {idx}\n\n````text\n{raw_text}\n````")
        image_dir, new_images = _extract_ipynb_output_images(outputs, idx, image_dir)
        images.extend(new_images)

    cleanup_dirs = [str(image_dir)] if image_dir else []
    markdown = "\n\n".join(p for p in parts if str(p).strip()).strip() + "\n"
    return markdown, images, {"cleanup_dirs": cleanup_dirs}


def _cell_source_text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(x) for x in value)
    return str(value or "")


def _ipynb_outputs_text(outputs: Any, max_chars: int = 4000) -> str:
    if not isinstance(outputs, list):
        return ""
    chunks: list[str] = []
    for output in outputs:
        if not isinstance(output, dict):
            continue
        if output.get("output_type") == "stream":
            text = _cell_source_text(output.get("text"))
        elif "text/plain" in (output.get("data") or {}):
            text = _cell_source_text((output.get("data") or {}).get("text/plain"))
        elif output.get("ename") or output.get("evalue"):
            text = f"{output.get('ename', '')}: {output.get('evalue', '')}".strip(": ")
        else:
            text = ""
        if text.strip():
            chunks.append(text.strip())
        if sum(len(x) for x in chunks) >= max_chars:
            break
    text = "\n\n".join(chunks).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n...[truncated]"
    return text


def _extract_ipynb_output_images(outputs: Any, cell_index: int, image_dir: Path | None) -> tuple[Path | None, list[dict[str, Any]]]:
    if not isinstance(outputs, list):
        return image_dir, []
    images: list[dict[str, Any]] = []
    for output in outputs:
        data = output.get("data") if isinstance(output, dict) else {}
        if not isinstance(data, dict):
            continue
        for mime, ext in (("image/png", ".png"), ("image/jpeg", ".jpg"), ("image/webp", ".webp")):
            encoded = data.get(mime)
            if not encoded:
                continue
            if image_dir is None:
                image_dir = Path(tempfile.mkdtemp(prefix="sb-ipynb-images-"))
            image_index = len(images) + 1
            path = image_dir / f"cell_{cell_index:03d}_output_{image_index:02d}{ext}"
            if isinstance(encoded, list):
                encoded = "".join(str(x) for x in encoded)
            try:
                path.write_bytes(base64.b64decode(str(encoded)))
            except Exception:
                continue
            images.append({
                "path": str(path),
                "filename": path.name,
                "label": f"Cell {cell_index} output {image_index}",
                "kind": "notebook-output",
            })
    return image_dir, images


def _language_for_extension(ext: str) -> str:
    return {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "jsx",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".vue": "vue",
        ".svelte": "svelte",
        ".java": "java",
        ".kt": "kotlin",
        ".c": "c",
        ".cc": "cpp",
        ".cpp": "cpp",
        ".cxx": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".hh": "cpp",
        ".cs": "csharp",
        ".go": "go",
        ".rs": "rust",
        ".rb": "ruby",
        ".php": "php",
        ".sh": "bash",
        ".bash": "bash",
        ".zsh": "zsh",
        ".ps1": "powershell",
        ".bat": "bat",
        ".r": "r",
        ".m": "matlab",
        ".scala": "scala",
        ".swift": "swift",
        ".lua": "lua",
        ".pl": "perl",
    }.get(ext, "")


def _validate_ooxml_archive(zf: zipfile.ZipFile) -> list[str]:
    infos = zf.infolist()
    if len(infos) > _OOXML_MAX_ENTRIES:
        raise ValueError("Invalid OOXML: archive entry limit exceeded")
    names = [info.filename for info in infos]
    if len(set(names)) != len(names):
        raise ValueError("Invalid OOXML: duplicate archive entry")
    total = 0
    for info in infos:
        if info.flag_bits & 0x1:
            raise ValueError("Invalid OOXML: encrypted archive entry")
        if info.file_size < 0 or info.file_size > _OOXML_MAX_PART_BYTES:
            raise ValueError("Invalid OOXML: archive part size limit exceeded")
        total += info.file_size
        if total > _OOXML_MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Invalid OOXML: archive expansion limit exceeded")
    return names


def _read_ooxml_part(zf: zipfile.ZipFile, name: str) -> bytes:
    try:
        info = zf.getinfo(name)
    except KeyError:
        raise ValueError("Invalid OOXML: required archive part missing") from None
    if info.file_size < 0 or info.file_size > _OOXML_MAX_PART_BYTES:
        raise ValueError("Invalid OOXML: archive part size limit exceeded")
    with zf.open(info, "r") as source:
        content = source.read(_OOXML_MAX_PART_BYTES + 1)
    if len(content) > _OOXML_MAX_PART_BYTES or len(content) != info.file_size:
        raise ValueError("Invalid OOXML: archive part size mismatch")
    return content


def _parse_ooxml_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    content = _read_ooxml_part(zf, name)
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("Invalid OOXML: XML declarations are not permitted")
    try:
        return ET.fromstring(content)
    except (ET.ParseError, RecursionError) as exc:
        raise ValueError("Invalid OOXML XML") from exc


def _normalize_ooxml_text(value: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in value.splitlines()]
    return "\n".join(line for line in lines if line)


def _docx_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        if element.tag == _W + "t" and element.text:
            parts.append(element.text)
        elif element.tag == _W + "tab":
            parts.append("\t")
        elif element.tag in {_W + "br", _W + "cr"}:
            parts.append("\n")
    return _normalize_ooxml_text("".join(parts))


def _docx_paragraph_markdown(paragraph: ET.Element) -> str:
    text = _docx_paragraph_text(paragraph)
    if not text:
        return ""
    style = paragraph.find(f"./{_W}pPr/{_W}pStyle")
    style_id = "" if style is None else (style.get(_W + "val") or "").strip()
    if style_id.casefold() == "title":
        return f"# {text}"
    heading = re.fullmatch(r"heading([1-6])", style_id, flags=re.IGNORECASE)
    if heading is not None:
        level = int(heading.group(1))
        return f"{'#' * level} {text}"
    return text


def _enabled_ooxml_property(element: ET.Element | None, attribute: str) -> bool:
    if element is None:
        return False
    value = element.get(attribute)
    return value is None or value.strip().casefold() not in _OOXML_FALSE


def _positive_span(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        span = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid OOXML: malformed {name}") from None
    if not 1 <= span <= 16_384:
        raise ValueError(f"Invalid OOXML: malformed {name}")
    return span


def _docx_table_rows(table: ET.Element):
    for child in table:
        if child.tag == _W + "tr":
            yield child
        elif child.tag in _DOCX_CONTENT_WRAPPERS:
            yield from _docx_table_rows(child)


def _docx_row_cells(row: ET.Element):
    for child in row:
        if child.tag == _W + "tc":
            yield child
        elif child.tag in _DOCX_CONTENT_WRAPPERS:
            yield from _docx_row_cells(child)


def _docx_cell_html(cell: ET.Element, table_counter: list[int]) -> str:
    def blocks_in(container: ET.Element):
        for child in container:
            if child.tag == _W + "p":
                text = _docx_paragraph_text(child)
                if text:
                    yield html.escape(text).replace("\n", "<br>")
            elif child.tag == _W + "tbl":
                table_counter[0] += 1
                nested_number = table_counter[0]
                yield (
                    f"<span>Nested table {nested_number}</span>"
                    + _docx_table_html(child, table_counter)
                )
            elif child.tag in _DOCX_CONTENT_WRAPPERS:
                yield from blocks_in(child)

    blocks = list(blocks_in(cell))
    return "<br>".join(blocks)


def _docx_table_html(table: ET.Element, table_counter: list[int]) -> str:
    rows = list(_docx_table_rows(table))
    if not rows:
        raise ValueError("Invalid docx: table has no rows")
    rendered = ["<table>"]
    for row in rows:
        header = _enabled_ooxml_property(
            row.find(f"./{_W}trPr/{_W}tblHeader"), _W + "val"
        )
        rendered.append(f'  <tr data-header-row="{str(header).lower()}">')
        cells = list(_docx_row_cells(row))
        if not cells:
            raise ValueError("Invalid docx: table row has no cells")
        tag = "th" if header else "td"
        for cell in cells:
            span_node = cell.find(f"./{_W}tcPr/{_W}gridSpan")
            span = _positive_span(
                None if span_node is None else span_node.get(_W + "val"),
                "DOCX column span",
            )
            span_attr = f' colspan="{span}"' if span and span > 1 else ""
            rendered.append(f"    <{tag}{span_attr}>{_docx_cell_html(cell, table_counter)}</{tag}>")
        rendered.append("  </tr>")
    rendered.append("</table>")
    return "\n".join(rendered)


def _bounded_table_label(table: str, number: int) -> str:
    # This label is owned by the renderer, not inferred from source prose. Its
    # HTML scope ends with the actual table; it cannot become the heading of a
    # later paragraph. Original captions and headings remain untouched.
    if not table.startswith("<table>\n"):
        raise ValueError("Invalid native table wrapper")
    return ("<table>\n" + f'<caption data-origin="extractor-position">Table {number}</caption>\n'
        + table[len("<table>\n"):])


def _docx_body_blocks(container: ET.Element, table_counter: list[int], *, bounded_table_labels=False):
    for child in container:
        if child.tag == _W + "p":
            block = _docx_paragraph_markdown(child)
            if block:
                yield block
        elif child.tag == _W + "tbl":
            table_counter[0] += 1
            number = table_counter[0]
            table = _docx_table_html(child, table_counter)
            yield (_bounded_table_label(table, number) if bounded_table_labels
                else f"### Table {number}\n\n{table}")
        elif child.tag in _DOCX_CONTENT_WRAPPERS:
            yield from _docx_body_blocks(child, table_counter, bounded_table_labels=bounded_table_labels)


def _extract_docx_xml(raw: bytes, *, include_format_label: bool = True,
                      bounded_table_labels: bool = False) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = _validate_ooxml_archive(zf)
        if "word/document.xml" not in names:
            raise ValueError("Invalid docx: word/document.xml missing")
        root = _parse_ooxml_xml(zf, "word/document.xml")
        body = root.find(f"./{_W}body")
        if body is None:
            raise ValueError("Invalid docx: document body missing")
        blocks = list(_docx_body_blocks(body, [0], bounded_table_labels=bounded_table_labels))
        images, cleanup_dirs = _extract_zip_media(
            zf, names, "word/media/", "docx_image", "docx-image", "Document image",
            bounded_ooxml=True,
        )
    # Only control the renderer-owned wrapper. Never strip matching text from
    # user paragraphs/headings: the same words may be genuine source content.
    markdown = "\n\n".join((["# Imported DOCX"] if include_format_label else []) + blocks)
    return markdown, images, {"cleanup_dirs": cleanup_dirs}


def _pptx_paragraph_text(paragraph: ET.Element) -> str:
    parts: list[str] = []
    for element in paragraph.iter():
        if element.tag == _A + "t" and element.text:
            parts.append(element.text)
        elif element.tag in {_A + "br", _A + "tab"}:
            parts.append("\n" if element.tag == _A + "br" else "\t")
    return _normalize_ooxml_text("".join(parts))


def _pptx_table_cell_html(cell: ET.Element) -> str:
    body = cell.find(f"./{_A}txBody")
    if body is None:
        return ""
    paragraphs = [
        _pptx_paragraph_text(paragraph)
        for paragraph in body.findall(f"./{_A}p")
    ]
    return "<br>".join(html.escape(text).replace("\n", "<br>") for text in paragraphs if text)


def _pptx_table_html(table: ET.Element) -> str:
    rows = table.findall(f"./{_A}tr")
    if not rows:
        raise ValueError("Invalid pptx: table has no rows")
    properties = table.find(f"./{_A}tblPr")
    first_row_header = (
        properties is not None
        and properties.get("firstRow") is not None
        and _enabled_ooxml_property(properties, "firstRow")
    )
    rendered = ["<table>"]
    for row_index, row in enumerate(rows):
        header = first_row_header and row_index == 0
        rendered.append(f'  <tr data-header-row="{str(header).lower()}">')
        cells = row.findall(f"./{_A}tc")
        if not cells:
            raise ValueError("Invalid pptx: table row has no cells")
        tag = "th" if header else "td"
        for cell in cells:
            colspan = _positive_span(cell.get("gridSpan"), "PPTX column span")
            rowspan = _positive_span(cell.get("rowSpan"), "PPTX row span")
            attrs = ""
            if colspan and colspan > 1:
                attrs += f' colspan="{colspan}"'
            if rowspan and rowspan > 1:
                attrs += f' rowspan="{rowspan}"'
            rendered.append(f"    <{tag}{attrs}>{_pptx_table_cell_html(cell)}</{tag}>")
        rendered.append("  </tr>")
    rendered.append("</table>")
    return "\n".join(rendered)


def _pptx_shape_blocks(container: ET.Element, table_counter: list[int], *, bounded_table_labels=False):
    for child in container:
        if child.tag in {_P + "sp", _P + "cxnSp"}:
            text_body = child.find(f"./{_P}txBody")
            if text_body is not None:
                for paragraph in text_body.findall(f"./{_A}p"):
                    text = _pptx_paragraph_text(paragraph)
                    if text:
                        yield text
        elif child.tag == _P + "graphicFrame":
            table = child.find(f".//{_A}tbl")
            if table is not None:
                table_counter[0] += 1
                body = _pptx_table_html(table)
                yield (_bounded_table_label(body, table_counter[0]) if bounded_table_labels
                    else f"### Table {table_counter[0]}\n\n{body}")
        elif child.tag == _P + "grpSp":
            yield from _pptx_shape_blocks(child, table_counter, bounded_table_labels=bounded_table_labels)


def _extract_pptx_xml(filename: str, raw: bytes, *, include_format_label: bool = True,
                      bounded_table_labels: bool = False) -> tuple[str, list[dict[str, Any]], dict[str, Any], list[str]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = _validate_ooxml_archive(zf)
        slide_names = sorted(
            [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)),
        )
        if not slide_names:
            raise ValueError("Invalid pptx: no slides found")
        parts = ["# Imported PPTX"] if include_format_label else []
        table_counter = [0]
        for idx, name in enumerate(slide_names, start=1):
            root = _parse_ooxml_xml(zf, name)
            shape_tree = root.find(f"./{_P}cSld/{_P}spTree")
            if shape_tree is None:
                raise ValueError("Invalid pptx: slide shape tree missing")
            slide_blocks = list(_pptx_shape_blocks(shape_tree, table_counter, bounded_table_labels=bounded_table_labels))
            slide = f"## Slide {idx}"
            if slide_blocks:
                slide += "\n\n" + "\n\n".join(slide_blocks)
            parts.append(slide)
        images, cleanup_dirs = _extract_zip_media(
            zf, names, "ppt/media/", "pptx_image", "pptx-image", "PPTX embedded image",
            bounded_ooxml=True,
        )

    warnings: list[str] = []
    if _env_on("CODE_ASSIST_RENDER_PPTX_SLIDES", True):
        try:
            rendered = _extract_office_com(filename, raw)
            images = _merge_images(images, rendered.images)
            cleanup_dirs.extend((rendered.metadata or {}).get("cleanup_dirs") or [])
        except Exception as e:
            warnings.append(f"PPTX slide rendering skipped: {e}")
    return "\n\n".join(parts), images, {"cleanup_dirs": cleanup_dirs}, warnings


def _extract_xlsx_xml(raw: bytes, *, include_format_label: bool = True) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = _validate_ooxml_archive(zf)
        shared = _xlsx_shared_strings(zf, names)
        worksheet_members = sorted(
            [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)],
            key=lambda n: int(re.search(r"sheet(\d+)\.xml", n).group(1)),
        )
        if not worksheet_members:
            raise ValueError("Invalid xlsx: no sheets found")
        sheets = _xlsx_sheet_entries(zf, names, worksheet_members)
        parts = ["# Imported XLSX"] if include_format_label else []
        for idx, (sheet_member, display_name) in enumerate(sheets, start=1):
            root = _parse_ooxml_xml(zf, sheet_member)
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            rows: list[tuple[int, dict[int, str]]] = []
            previous_row = 0
            for row in root.findall(".//s:row", ns):
                raw_row = row.get("r")
                row_number = int(raw_row) if raw_row and raw_row.isdecimal() else previous_row + 1
                if row_number <= previous_row or row_number > 1_048_576:
                    raise ValueError("Invalid xlsx: worksheet rows are out of range or order")
                if raw_row and not raw_row.isdecimal():
                    raise ValueError("Invalid xlsx: worksheet row reference is malformed")
                previous_row = row_number
                cells: dict[int, str] = {}
                previous_column = 0
                for cell in row.findall("s:c", ns):
                    reference = cell.get("r")
                    if reference:
                        match = _XLSX_CELL_REFERENCE.fullmatch(reference)
                        if match is None or int(match.group(2)) != row_number:
                            raise ValueError("Invalid xlsx: cell reference is malformed")
                        column = _xlsx_column_number(match.group(1))
                    else:
                        column = previous_column + 1
                    if column <= previous_column or column > 16_384:
                        raise ValueError("Invalid xlsx: cell columns are out of range or order")
                    previous_column = column
                    v_el = cell.find("s:v", ns)
                    formula = cell.find("s:f", ns)
                    cell_type = cell.get("t")
                    if cell_type == "inlineStr":
                        inline = cell.find("s:is", ns)
                        value = ("".join(part.text or "" for part in inline.findall(".//s:t", ns))
                                 if inline is not None else "")
                    elif cell_type == "s":
                        if v_el is None or v_el.text is None or not v_el.text.isdecimal():
                            raise ValueError("Invalid xlsx: shared string reference is malformed")
                        pos = int(v_el.text)
                        if pos >= len(shared):
                            raise ValueError("Invalid xlsx: shared string reference is out of range")
                        value = shared[pos]
                    else:
                        value = v_el.text if v_el is not None and v_el.text is not None else ""
                        if cell_type == "b" and value in {"0", "1"}:
                            value = "TRUE" if value == "1" else "FALSE"
                    if formula is not None:
                        if not formula.text or not formula.text.strip():
                            raise ValueError("Invalid xlsx: unresolved shared or empty formula")
                        expression = "=" + formula.text.strip()
                        value = f"{expression} (cached: {value})" if value else f"{expression} (no cached result)"
                    if value:
                        cells[column] = value
                if cells:
                    rows.append((row_number, cells))
            title = f"## Sheet {idx}: {display_name}" if display_name else f"## Sheet {idx}"
            parts.append(title + "\n\n" + _xlsx_rows_to_markdown(rows))
        chart_parts = _xlsx_chart_parts(zf, names)
        if chart_parts:
            parts.append("## Workbook charts\n\n"
                         "Chart titles, axes, series and source references are retained below. "
                         "Visual layout and any unlabeled visual relationship are not verified.\n\n"
                         + "\n\n".join(chart_parts))
        markdown = "\n\n".join(parts)
        if len(markdown.encode("utf-8")) > _EXCEL_MAX_SOURCE_MARKDOWN_BYTES:
            raise ValueError("XLSX source Markdown exceeds the publication limit; no cells were truncated")
        images, cleanup_dirs = _extract_zip_media(zf, names, "xl/media/", "xlsx_image", "xlsx-image", "Workbook image")
    return markdown, images, {"cleanup_dirs": cleanup_dirs,
                              "xlsx_chart_count": len(chart_parts),
                              "xlsx_chart_visual_coverage": "metadata_only" if chart_parts else "none"}


def _xlsx_chart_parts(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    """Retain connected chart semantics without claiming that XML is a render."""
    package_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    chart = f"{{{_CHART_NS}}}"
    linked: list[str] = []
    for rels_name in names:
        if (not rels_name.startswith("xl/") or "/_rels/" not in rels_name
                or not rels_name.endswith(".rels")):
            continue
        owner_dir, rels_file = posixpath.split(rels_name)
        if not owner_dir.endswith("/_rels"):
            continue
        source_dir = owner_dir.removesuffix("/_rels")
        relationships = _parse_ooxml_xml(zf, rels_name)
        for relation in relationships.findall(package_rel):
            if not (relation.get("Type") or "").endswith("/chart"):
                continue
            target = relation.get("Target")
            if (not target or relation.get("TargetMode") == "External"
                    or not rels_file.endswith(".xml.rels")):
                raise ValueError("Invalid xlsx: chart relationship is malformed")
            member = posixpath.normpath(
                target.lstrip("/") if target.startswith("/")
                else posixpath.join(source_dir, target)
            )
            if not member.startswith("xl/") or member not in names:
                raise ValueError("Invalid xlsx: chart relationship target is missing")
            if member not in linked:
                linked.append(member)

    parts: list[str] = []
    for ordinal, member in enumerate(linked, start=1):
        root = _parse_ooxml_xml(zf, member)
        if root.tag != chart + "chartSpace":
            raise ValueError("Invalid xlsx: chart part is malformed")
        chart_node = root.find(chart + "chart")
        plot = chart_node.find(chart + "plotArea") if chart_node is not None else None
        if plot is None:
            raise ValueError("Invalid xlsx: chart plot is missing")
        plot_types = [node for node in plot
                      if node.tag.startswith(chart) and node.tag.endswith("Chart")]
        if not plot_types:
            raise ValueError("Invalid xlsx: chart type is missing")
        kinds = ", ".join(node.tag.removeprefix(chart) for node in plot_types)
        lines = [f"### Chart {ordinal} ({kinds})"]
        title = _xlsx_chart_text(chart_node.find(chart + "title"))
        lines.append("- Title: " + (title or "[not supplied]"))
        for axis_type, label in (("catAx", "Category axis"),
                                 ("dateAx", "Date axis"),
                                 ("valAx", "Value axis"),
                                 ("serAx", "Series axis")):
            for axis in plot.findall(chart + axis_type):
                axis_title = _xlsx_chart_text(axis.find(chart + "title"))
                if axis_title:
                    lines.append(f"- {label}: {axis_title}")
        series_ordinal = 0
        for plot_type in plot_types:
            for series in plot_type.findall(chart + "ser"):
                series_ordinal += 1
                name = _xlsx_chart_text(series.find(chart + "tx"))
                lines.append(f"- Series {series_ordinal}: {name or '[not supplied]'}")
                for field, label in (("cat", "Categories"), ("val", "Values"),
                                     ("xVal", "X values"), ("yVal", "Y values"),
                                     ("bubbleSize", "Bubble sizes")):
                    field_node = series.find(chart + field)
                    if field_node is None:
                        continue
                    formulas = [(node.text or "").strip() for node in field_node.iter(chart + "f")]
                    if any(not formula or "#REF!" in formula
                           or re.search(r"\[[^\]]+\.xls(?:x|m|b)?\]", formula, re.I)
                           for formula in formulas):
                        raise ValueError("Invalid xlsx: chart data reference is unresolved")
                    for formula in formulas:
                        lines.append(f"  - {label} source: {formula}")
                    if not formulas:
                        points = []
                        for point in field_node.iter(chart + "pt"):
                            value = point.find(chart + "v")
                            if value is not None and value.text is not None:
                                points.append(f"{point.get('idx', '?')}:{value.text}")
                        if points:
                            lines.append(f"  - {label} cached points: " + "; ".join(points))
        if series_ordinal == 0:
            raise ValueError("Invalid xlsx: chart has no data series")
        parts.append("\n".join(lines))
    return parts


def _xlsx_chart_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    accepted = {f"{{{_DRAWING_NS}}}t", f"{{{_CHART_NS}}}v",
                f"{{{_CHART_NS}}}f"}
    return _normalize_ooxml_text(" ".join(
        child.text for child in node.iter()
        if child.tag in accepted and child.text
    )).replace("|", "&#124;")


def _xlsx_sheet_entries(
    zf: zipfile.ZipFile, names: list[str], worksheet_members: list[str],
) -> list[tuple[str, str]]:
    workbook_member = "xl/workbook.xml"
    relationships_member = "xl/_rels/workbook.xml.rels"
    if workbook_member not in names and relationships_member not in names:
        return [(member, "") for member in worksheet_members]
    if workbook_member not in names or relationships_member not in names:
        raise ValueError("Invalid xlsx: workbook sheet catalog is incomplete")

    workbook = _parse_ooxml_xml(zf, workbook_member)
    relationships = _parse_ooxml_xml(zf, relationships_member)
    sheet_ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    package_rel = "{http://schemas.openxmlformats.org/package/2006/relationships}Relationship"
    targets: dict[str, str] = {}
    for relation in relationships.findall(package_rel):
        if not (relation.get("Type") or "").endswith("/worksheet"):
            continue
        relation_id = relation.get("Id")
        target = relation.get("Target")
        if (not relation_id or not target or relation.get("TargetMode") == "External"
                or relation_id in targets):
            raise ValueError("Invalid xlsx: worksheet relationship is malformed")
        member = posixpath.normpath(
            target.lstrip("/") if target.startswith("/") else "xl/" + target
        )
        if member not in worksheet_members:
            raise ValueError("Invalid xlsx: worksheet relationship target is missing")
        targets[relation_id] = member

    entries: list[tuple[str, str]] = []
    for sheet in workbook.findall(f"./{{{sheet_ns}}}sheets/{{{sheet_ns}}}sheet"):
        relation_id = sheet.get(rel_attr)
        if relation_id not in targets:
            continue
        title = sheet.get("name")
        if not title or not title.strip():
            raise ValueError("Invalid xlsx: worksheet title is missing")
        entries.append((targets[relation_id], title.replace("\r", " ").replace("\n", " ")))
    if (len(entries) != len(worksheet_members)
            or {member for member, _ in entries} != set(worksheet_members)):
        raise ValueError("Invalid xlsx: workbook worksheet catalog does not match its sheets")
    return entries


def _xlsx_column_number(label: str) -> int:
    number = 0
    for letter in label:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def _xlsx_column_label(number: int) -> str:
    letters = []
    while number:
        number, remainder = divmod(number - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _xlsx_markdown_cell(value: str) -> str:
    return value.replace("|", "&#124;").replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


def _xlsx_rows_to_markdown(rows: list[tuple[int, dict[int, str]]]) -> str:
    if not rows:
        return "[No nonempty worksheet cells]"
    maximum_column = max(max(cells) for _, cells in rows)
    nonempty_cells = sum(len(cells) for _, cells in rows)
    # A mostly empty wide grid hides useful A1 addresses in blank columns and
    # spends the authoring budget on separators. Represent those cells directly.
    if (maximum_column <= _XLSX_DENSE_GRID_MAX_COLUMNS
            and len(rows) * maximum_column <= _XLSX_DENSE_GRID_MAX_CELLS
            and nonempty_cells * 5 >= len(rows) * maximum_column):
        header = "| Row | " + " | ".join(
            _xlsx_column_label(column) for column in range(1, maximum_column + 1)
        ) + " |"
        separator = "| --- | " + " | ".join(
            "---" for _ in range(maximum_column)
        ) + " |"
        body = [
            "| " + str(row_number) + " | " + " | ".join(
                _xlsx_markdown_cell(cells.get(column, ""))
                for column in range(1, maximum_column + 1)
            ) + " |"
            for row_number, cells in rows
        ]
        return "\n".join((header, separator, *body))
    body = [
        f"| {_xlsx_column_label(column)}{row_number} | {_xlsx_markdown_cell(value)} |"
        for row_number, cells in rows for column, value in cells.items()
    ]
    return "\n".join(("| Cell | Value |", "| --- | --- |", *body))


def _xlsx_shared_strings(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = _parse_ooxml_xml(zf, "xl/sharedStrings.xml")
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = []
    for si in root.findall(".//s:si", ns):
        texts = [t.text or "" for t in si.findall(".//s:t", ns)]
        shared.append("".join(texts))
    return shared


_PDF_UNREADABLE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _normalize_pdf_extracted_text(text: str) -> tuple[str, int]:
    """Mark undecodable PDF control glyphs without discarding neighboring text.

    pypdf can return NUL and other C0 controls for unmapped font glyphs.
    Canonical Markdown must not contain NUL; replacing these glyphs with the
    Unicode replacement character makes the uncertainty visible while leaving
    ordinary characters, whitespace and page order intact.
    """
    if not isinstance(text, str):
        raise TypeError("PDF extracted text must be a string.")
    return _PDF_UNREADABLE_CONTROL_RE.sub("\ufffd", text), len(
        _PDF_UNREADABLE_CONTROL_RE.findall(text)
    )


def _extract_pdf_pypdf(raw: bytes, *, include_format_label: bool = True,
                       defer_embedded_images: bool = False) -> tuple[str, list[dict[str, Any]], dict[str, Any], list[str]]:
    try:
        from pypdf import PdfReader
    except Exception as e:
        raise RuntimeError("PDF extraction requires pypdf") from e

    reader = PdfReader(io.BytesIO(raw))
    if getattr(reader, "is_encrypted", False):
        raise RuntimeError("Encrypted PDF cannot be extracted by the default PDF reader.")

    parts = []
    text_pages: list[int] = []
    images: list[dict[str, Any]] = []
    cleanup_dirs: list[str] = []
    image_dir: Path | None = None
    unreadable_glyphs = 0
    try:
        for idx, page in enumerate(reader.pages, start=1):
            text, count = _normalize_pdf_extracted_text(page.extract_text() or "")
            unreadable_glyphs += count
            if text.strip():
                parts.append(f"[Page {idx}]\n{text.strip()}")
                text_pages.append(idx)
            if not defer_embedded_images:
                image_dir, page_images = _extract_pdf_page_images(page, idx, image_dir)
                images.extend(page_images)
        if image_dir:
            cleanup_dirs.append(str(image_dir))

        warnings: list[str] = (
            ["PDF text contains replacement markers for unreadable control glyphs."]
            if unreadable_glyphs else []
        )
        render_mode = os.environ.get("CODE_ASSIST_RENDER_PDF_PAGES", "auto").strip().lower()
        # The registered visual workflow separately renders EVERY page from
        # the retained original in its bounded worker. Do not decompress large
        # embedded XObjects here too, or create a second partial page inventory.
        should_render = not defer_embedded_images and (render_mode in {"1", "true", "yes", "on", "all"} or (render_mode == "auto" and not parts))
        if defer_embedded_images:
            warnings.append("PDF embedded images deferred to required full-page visual authoring.")
        if should_render:
            try:
                rendered_images, rendered_cleanup = _render_pdf_pages(raw)
            except Exception as e:
                warnings.append(f"PDF page rendering skipped: {e}")
            else:
                # Own returned files before any later operation can fail. A
                # merge failure is not renderer unavailability or partial success.
                cleanup_dirs.extend(rendered_cleanup)
                images = _merge_images(images, rendered_images)

        markdown = "\n\n".join((["# Imported PDF"] if include_format_label else []) + parts)
        return markdown.strip() + "\n", images, {
            "cleanup_dirs": cleanup_dirs,
            "pdf_page_count": len(reader.pages),
            "pdf_text_pages": text_pages,
            **({"pdf_images_deferred": True} if defer_embedded_images else {}),
        }, warnings
    except BaseException:
        # A failed extraction never returns metadata to the caller's cleanup.
        # Only paths created by our image/render helpers are owned here.
        owned_dirs = set(cleanup_dirs)
        if image_dir is not None:
            owned_dirs.add(str(image_dir))
        for owned_dir in owned_dirs:
            shutil.rmtree(owned_dir, ignore_errors=True)
        raise


def _extract_pdf_page_images(page: Any, page_index: int, image_dir: Path | None) -> tuple[Path | None, list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []
    try:
        page_images = list(page.images)
    except Exception as exc:
        raise RuntimeError(f"PDF page {page_index} image enumeration failed.") from exc
    created_here = False
    try:
        for idx, image in enumerate(page_images, start=1):
            data = getattr(image, "data", None)
            if not isinstance(data, bytes) or not data:
                raise RuntimeError(f"PDF page {page_index} image {idx} has no retained bytes.")
            source_name = getattr(image, "name", "")
            ext = Path(_safe_filename(source_name)).suffix.lower() if isinstance(source_name, str) else ""
            if ext not in IMAGE_EXTENSIONS:
                # Relabelling arbitrary bytes as PNG does not convert them.
                raise RuntimeError(f"PDF page {page_index} image {idx} has an unsupported format.")
            # PDF resource names are local to a page/form, not document-global.
            # Never use those names as the destination, even after sanitization.
            name = f"page_{page_index:03d}_image_{idx:03d}{ext}"
            if image_dir is None:
                image_dir = Path(tempfile.mkdtemp(prefix="sb-pdf-images-"))
                created_here = True
            path = image_dir / name
            path.write_bytes(data)
            images.append({
                "path": str(path),
                "filename": path.name,
                "label": f"Page {page_index} image {idx}",
                "kind": "pdf-image",
            })
        return image_dir, images
    except BaseException:
        # If this call created the directory, the parent has not received it.
        # A pre-existing directory remains owned by the parent extraction.
        if created_here:
            shutil.rmtree(image_dir, ignore_errors=True)
        raise


def _render_pdf_pages(raw: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    import fitz  # PyMuPDF

    max_pages = int(os.environ.get("CODE_ASSIST_PDF_RENDER_MAX_PAGES", "12") or "12")
    image_dir = Path(tempfile.mkdtemp(prefix="sb-pdf-pages-"))
    images: list[dict[str, Any]] = []
    try:
        doc = fitz.open(stream=raw, filetype="pdf")
        for idx in range(min(len(doc), max_pages)):
            page = doc[idx]
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
            path = image_dir / f"page_{idx + 1:03d}.png"
            pix.save(str(path))
            images.append({
                "path": str(path),
                "filename": path.name,
                "label": f"Page {idx + 1}",
                "kind": "page",
            })
        doc.close()
    except Exception:
        shutil.rmtree(image_dir, ignore_errors=True)
        raise
    return images, [str(image_dir)]


def _extract_zip_media(
    zf: zipfile.ZipFile,
    names: list[str],
    prefix: str,
    filename_prefix: str,
    kind: str,
    label_prefix: str,
    *,
    bounded_ooxml: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    image_names = [n for n in names if n.lower().startswith(prefix.lower()) and Path(n).suffix.lower() in IMAGE_EXTENSIONS]
    if not image_names:
        return [], []
    image_dir = Path(tempfile.mkdtemp(prefix="sb-ooxml-images-"))
    images: list[dict[str, Any]] = []
    try:
        for idx, name in enumerate(sorted(image_names), start=1):
            ext = Path(name).suffix.lower() or ".png"
            path = image_dir / f"{filename_prefix}_{idx:03d}{ext}"
            path.write_bytes(
                _read_ooxml_part(zf, name) if bounded_ooxml else zf.read(name)
            )
            images.append({
                "path": str(path),
                "filename": path.name,
                "label": f"{label_prefix} {idx}",
                "kind": kind,
                "source_name": name,
            })
    except Exception:
        shutil.rmtree(image_dir, ignore_errors=True)
        raise
    return images, [str(image_dir)]


def _merge_images(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for image in list(first or []) + list(second or []):
        key = (str(image.get("kind") or ""), str(image.get("filename") or image.get("path") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(image)
    return out


def _env_on(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_filename(filename: str) -> str:
    name = Path(filename or "uploaded").name
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*\\/]+", "-", name).strip(". ")
    return name or "uploaded"
