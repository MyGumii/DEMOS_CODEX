"""
Document extraction framework for Second Brain imports.

Supported sources are normalized into markdown plus optional image artifacts.
Native extractors are used first. Optional local converters can be configured
for richer Office rendering or custom import pipelines.
"""
from __future__ import annotations

import base64
import io
import json
import os
import platform
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
) -> ExtractionResult:
    """Extract a source document into markdown plus optional image artifacts."""
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_SOURCE_EXTENSIONS:
        raise ValueError(f"Unsupported source file extension: {ext}")

    try:
        return _extract_native(filename, raw)
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


def _extract_native(filename: str, raw: bytes) -> ExtractionResult:
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
        markdown, images, metadata = _extract_docx_xml(raw)
        return ExtractionResult(markdown, "docx-xml", filename, images=images, metadata=metadata)
    if ext == ".pptx":
        markdown, images, metadata, warnings = _extract_pptx_xml(filename, raw)
        return ExtractionResult(markdown, "pptx-xml", filename, warnings=warnings, images=images, metadata=metadata)
    if ext == ".xlsx":
        markdown, images, metadata = _extract_xlsx_xml(raw)
        return ExtractionResult(markdown, "xlsx-xml", filename, images=images, metadata=metadata)
    if ext == ".pdf":
        markdown, images, metadata, warnings = _extract_pdf_pypdf(raw)
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
    images: list[dict[str, Any]] = []
    parts = ["# Imported PowerPoint"]
    try:
        app = win32_client.DispatchEx("PowerPoint.Application")
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
                if image_path.is_file():
                    images.append({
                        "path": str(image_path),
                        "filename": image_path.name,
                        "label": f"Slide {idx}",
                        "kind": "slide",
                    })
            except Exception:
                pass
    finally:
        try:
            if presentation is not None:
                presentation.Close()
        finally:
            if app is not None:
                app.Quit()
    return "\n\n".join(parts), images


def _shape_texts(shapes: Any) -> list[str]:
    texts: list[str] = []
    try:
        count = int(shapes.Count)
    except Exception:
        return texts
    for idx in range(1, count + 1):
        try:
            shape = shapes(idx)
            texts.extend(_single_shape_texts(shape))
        except Exception:
            continue
    return [re.sub(r"\s+", " ", t).strip() for t in texts if str(t).strip()]


def _single_shape_texts(shape: Any) -> list[str]:
    texts: list[str] = []
    try:
        if getattr(shape, "HasTextFrame", 0) and shape.TextFrame.HasText:
            text = str(shape.TextFrame.TextRange.Text or "").strip()
            if text:
                texts.append(text)
    except Exception:
        pass
    try:
        if getattr(shape, "HasTable", 0):
            table = shape.Table
            for row in range(1, int(table.Rows.Count) + 1):
                cells = []
                for col in range(1, int(table.Columns.Count) + 1):
                    try:
                        cells.append(str(table.Cell(row, col).Shape.TextFrame.TextRange.Text or "").strip())
                    except Exception:
                        cells.append("")
                if any(cells):
                    texts.append(" | ".join(cells))
    except Exception:
        pass
    try:
        group_items = shape.GroupItems
        texts.extend(_shape_texts(group_items))
    except Exception:
        pass
    return texts


def _extract_word_com(win32_client: Any, input_path: Path, image_dir: Path, visible: bool) -> tuple[str, list[dict[str, Any]]]:
    app = None
    doc = None
    try:
        app = win32_client.DispatchEx("Word.Application")
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
                        try:
                            cell_text = str(table.Cell(r_idx, c_idx).Range.Text or "")
                            cell_text = cell_text.replace("\r\x07", "").replace("\x07", "").strip()
                            cells.append(cell_text)
                        except Exception:
                            cells.append("")
                    if any(cells):
                        rows.append("| " + " | ".join(cells) + " |")
                if rows:
                    tables.append(f"## Table {t_idx}\n\n" + "\n".join(rows))
        except Exception:
            pass
        parts = ["# Imported Word Document", text]
        parts.extend(tables)
        return "\n\n".join(p for p in parts if p.strip()), []
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        finally:
            if app is not None:
                app.Quit()


def _extract_excel_com(win32_client: Any, input_path: Path, image_dir: Path, visible: bool) -> tuple[str, list[dict[str, Any]]]:
    app = None
    workbook = None
    parts = ["# Imported Excel Workbook"]
    try:
        app = win32_client.DispatchEx("Excel.Application")
        app.Visible = visible
        app.DisplayAlerts = False
        workbook = app.Workbooks.Open(str(input_path), False, True)
        for idx in range(1, int(workbook.Worksheets.Count) + 1):
            sheet = workbook.Worksheets(idx)
            parts.append(f"## Sheet {idx}: {sheet.Name}")
            try:
                values = sheet.UsedRange.Value
                parts.append(_excel_values_to_markdown(values))
            except Exception:
                pass
        return "\n\n".join(p for p in parts if str(p).strip()), []
    finally:
        try:
            if workbook is not None:
                workbook.Close(False)
        finally:
            if app is not None:
                app.Quit()


def _excel_values_to_markdown(values: Any) -> str:
    if values is None:
        return ""
    if not isinstance(values, tuple):
        return str(values)
    rows = values if values and isinstance(values[0], tuple) else (values,)
    lines = []
    for row in rows[:200]:
        cells = [str(c if c is not None else "").replace("\n", " ").strip() for c in row[:40]]
        if any(cells):
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


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


def _extract_docx_xml(raw: bytes) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if "word/document.xml" not in names:
            raise ValueError("Invalid docx: word/document.xml missing")
        root = ET.fromstring(zf.read("word/document.xml"))
        images, cleanup_dirs = _extract_zip_media(zf, names, "word/media/", "docx_image", "docx-image", "Document image")
    paragraphs = []
    for para in root.iter():
        if not para.tag.endswith("}p"):
            continue
        texts = [elem.text for elem in para.iter() if elem.tag.endswith("}t") and elem.text]
        text = "".join(texts).strip()
        if text:
            paragraphs.append(re.sub(r"\s+", " ", text))
    return "# Imported DOCX\n\n" + "\n\n".join(paragraphs).strip(), images, {"cleanup_dirs": cleanup_dirs}


def _extract_pptx_xml(filename: str, raw: bytes) -> tuple[str, list[dict[str, Any]], dict[str, Any], list[str]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        slide_names = sorted(
            [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda n: int(re.search(r"slide(\d+)\.xml", n).group(1)),
        )
        if not slide_names:
            raise ValueError("Invalid pptx: no slides found")
        parts = ["# Imported PPTX"]
        for idx, name in enumerate(slide_names, start=1):
            root = ET.fromstring(zf.read(name))
            texts = [elem.text.strip() for elem in root.iter() if elem.tag.endswith("}t") and elem.text and elem.text.strip()]
            if texts:
                title = texts[0]
                body = "\n".join(f"- {t}" for t in texts[1:])
                parts.append(f"## Slide {idx}: {title}\n\n{body}".strip())
            else:
                parts.append(f"## Slide {idx}\n\n")
        images, cleanup_dirs = _extract_zip_media(zf, names, "ppt/media/", "pptx_image", "pptx-image", "PPTX embedded image")

    warnings: list[str] = []
    if _env_on("CODE_ASSIST_RENDER_PPTX_SLIDES", True):
        try:
            rendered = _extract_office_com(filename, raw)
            images = _merge_images(images, rendered.images)
            cleanup_dirs.extend((rendered.metadata or {}).get("cleanup_dirs") or [])
        except Exception as e:
            warnings.append(f"PPTX slide rendering skipped: {e}")
    return "\n\n".join(parts), images, {"cleanup_dirs": cleanup_dirs}, warnings


def _extract_xlsx_xml(raw: bytes) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        shared = _xlsx_shared_strings(zf, names)
        sheets = sorted(
            [n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)],
            key=lambda n: int(re.search(r"sheet(\d+)\.xml", n).group(1)),
        )
        if not sheets:
            raise ValueError("Invalid xlsx: no sheets found")
        parts = ["# Imported XLSX"]
        for idx, sheet_name in enumerate(sheets, start=1):
            root = ET.fromstring(zf.read(sheet_name))
            ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            rows = []
            for row in root.findall(".//s:row", ns)[:200]:
                cells = []
                for cell in row.findall("s:c", ns)[:40]:
                    v_el = cell.find("s:v", ns)
                    if v_el is None or v_el.text is None:
                        cells.append("")
                        continue
                    if cell.get("t") == "s" and v_el.text.isdigit():
                        pos = int(v_el.text)
                        cells.append(shared[pos] if pos < len(shared) else v_el.text)
                    else:
                        cells.append(v_el.text)
                if any(cells):
                    rows.append("| " + " | ".join(cells) + " |")
            parts.append(f"## Sheet {idx}\n\n" + "\n".join(rows))
        images, cleanup_dirs = _extract_zip_media(zf, names, "xl/media/", "xlsx_image", "xlsx-image", "Workbook image")
    return "\n\n".join(parts), images, {"cleanup_dirs": cleanup_dirs}


def _xlsx_shared_strings(zf: zipfile.ZipFile, names: list[str]) -> list[str]:
    if "xl/sharedStrings.xml" not in names:
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = []
    for si in root.findall(".//s:si", ns):
        texts = [t.text or "" for t in si.findall(".//s:t", ns)]
        shared.append("".join(texts))
    return shared


def _extract_pdf_pypdf(raw: bytes) -> tuple[str, list[dict[str, Any]], dict[str, Any], list[str]]:
    try:
        from pypdf import PdfReader
    except Exception as e:
        raise RuntimeError("PDF extraction requires pypdf") from e

    reader = PdfReader(io.BytesIO(raw))
    if getattr(reader, "is_encrypted", False):
        raise RuntimeError("Encrypted PDF cannot be extracted by the default PDF reader.")

    parts = []
    images: list[dict[str, Any]] = []
    cleanup_dirs: list[str] = []
    image_dir: Path | None = None
    for idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"[Page {idx}]\n{text.strip()}")
        image_dir, page_images = _extract_pdf_page_images(page, idx, image_dir)
        images.extend(page_images)
    if image_dir:
        cleanup_dirs.append(str(image_dir))

    warnings: list[str] = []
    render_mode = os.environ.get("CODE_ASSIST_RENDER_PDF_PAGES", "auto").strip().lower()
    should_render = render_mode in {"1", "true", "yes", "on", "all"} or (render_mode == "auto" and not parts)
    if should_render:
        try:
            rendered_images, rendered_cleanup = _render_pdf_pages(raw)
            images = _merge_images(images, rendered_images)
            cleanup_dirs.extend(rendered_cleanup)
        except Exception as e:
            warnings.append(f"PDF page rendering skipped: {e}")

    markdown = "# Imported PDF\n\n" + "\n\n".join(parts)
    return markdown.strip() + "\n", images, {"cleanup_dirs": cleanup_dirs}, warnings


def _extract_pdf_page_images(page: Any, page_index: int, image_dir: Path | None) -> tuple[Path | None, list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []
    try:
        page_images = list(getattr(page, "images", []) or [])
    except Exception:
        page_images = []
    for idx, image in enumerate(page_images, start=1):
        data = getattr(image, "data", None)
        if not data:
            continue
        name = _safe_filename(getattr(image, "name", "") or f"page_{page_index:03d}_image_{idx:02d}.bin")
        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            ext = ".png"
            name = f"page_{page_index:03d}_image_{idx:02d}{ext}"
        if image_dir is None:
            image_dir = Path(tempfile.mkdtemp(prefix="sb-pdf-images-"))
        path = image_dir / name
        try:
            path.write_bytes(data)
        except Exception:
            continue
        images.append({
            "path": str(path),
            "filename": path.name,
            "label": f"Page {page_index} image {idx}",
            "kind": "pdf-image",
        })
    return image_dir, images


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
            path.write_bytes(zf.read(name))
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
