"""Optional, supervised Windows desktop Office-to-PDF render provider.

This adapter handles ordinary OOXML documents only. It is not a DRM opener
and does not run in the default web worker. The helper enters a kill-on-close
Windows Job Object before it is allowed to start Office. Out-of-process COM
servers may not inherit that job, so this is not unattended multi-user
Office-process cleanup proof.
"""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePath, PurePosixPath
import subprocess
import sys
import tempfile
from typing import Callable
from xml.etree import ElementTree
from xml.parsers import expat
import zipfile

from pypdf import PdfReader

from .. import document_extractors as extractors
from .office_visual_source import AuthorizedOfficePdfRender, OfficeVisualSourceError


_RELS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_OFFICE_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_DRAWING = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_HYPERLINK_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
_MAX_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_DERIVATIVE_BYTES = 64 * 1024 * 1024
_RENDER_TIMEOUT_SECONDS = 90


class OfficeApplicationUnavailableError(OfficeVisualSourceError):
    """The required ordinary Office ProgID is absent in both registry views."""

    code = "OFFICE_APPLICATION_UNAVAILABLE"


def _office_application_registered(kind: str) -> bool | None:
    """Read registration only; uncertainty never means the application is absent."""
    progid = {"docx": "Word.Application", "pptx": "PowerPoint.Application"}[kind]
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None
    observed = []
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid, 0, winreg.KEY_READ | view):
                observed.append(True)
        except FileNotFoundError:
            observed.append(False)
        except OSError:
            observed.append(None)
    return True if True in observed else False if observed == [False, False] else None


def _checked_ordinary_ooxml(filename: str, source: bytes) -> str:
    if (type(filename) is not str or not filename
            or PurePath(filename).name != filename
            or type(source) is not bytes
            or not 1 <= len(source) <= _MAX_SOURCE_BYTES):
        raise OfficeVisualSourceError("The Office source name or size is invalid.")
    kind = PurePath(filename).suffix.lower().removeprefix(".")
    if kind not in {"docx", "pptx"}:
        raise OfficeVisualSourceError("This renderer accepts only ordinary DOCX/PPTX.")
    try:
        with zipfile.ZipFile(BytesIO(source)) as archive:
            names = extractors._validate_ooxml_archive(archive)
            expected = "word/document.xml" if kind == "docx" else "ppt/presentation.xml"
            if expected not in names:
                raise OfficeVisualSourceError("The Office main document is missing.")
            for name in names:
                if not name.endswith(".rels"):
                    continue
                root = ElementTree.fromstring(archive.read(name))
                if any(node.tag == _RELS + "Relationship"
                        and node.get("TargetMode", "").lower() == "external"
                        for node in root.iter()):
                    raise OfficeVisualSourceError(
                        "Office content with external links needs a separate approved opener.")
    except OfficeVisualSourceError:
        raise
    except Exception as exc:
        raise OfficeVisualSourceError("The ordinary Office package is invalid.") from exc
    return kind


def _xml_without_nodes(content: bytes, remove: Callable[[str, dict], bool]) -> tuple[bytes, int]:
    """Remove exact XML element byte spans; do not rewrite namespace prefixes.

    OOXML may retain namespace names in mc:Ignorable attributes. Re-serializing
    an ElementTree can discard those declarations, so unrelated bytes must be
    copied exactly. Only single-byte XML markup encodings are admitted here.
    """
    if b"\x00" in content:
        raise OfficeVisualSourceError("The static hyperlink XML encoding is unsupported.")
    spans: list[tuple[int, int]] = []
    stack: list[tuple[int, int, bool, bool]] = []
    parser = expat.ParserCreate(namespace_separator="}")

    def tag_end(start: int) -> int:
        quote = None
        for position in range(start, len(content)):
            value = content[position]
            if quote is not None:
                if value == quote:
                    quote = None
            elif value in (34, 39):
                quote = value
            elif value == 62:
                return position + 1
        raise OfficeVisualSourceError("The static hyperlink XML is incomplete.")

    def qualified(name: str) -> str:
        return "{" + name if "}" in name else name

    def start(name, attributes):
        position = parser.CurrentByteIndex
        end = tag_end(position)
        selected = remove(qualified(name), {
            qualified(key): value for key, value in attributes.items()
        })
        stack.append((position, end, content[position:end].rstrip().endswith(b"/>"), selected))

    def finish(_name):
        position, start_end, empty, selected = stack.pop()
        if selected:
            spans.append((position, start_end if empty else tag_end(parser.CurrentByteIndex)))

    def forbidden(*_args):
        raise OfficeVisualSourceError("The static hyperlink XML declarations are unsupported.")

    parser.StartElementHandler = start
    parser.EndElementHandler = finish
    parser.StartDoctypeDeclHandler = forbidden
    parser.EntityDeclHandler = forbidden
    parser.ExternalEntityRefHandler = forbidden
    try:
        parser.Parse(content, True)
    except OfficeVisualSourceError:
        raise
    except (expat.ExpatError, ValueError, IndexError) as exc:
        raise OfficeVisualSourceError("The static hyperlink XML is invalid.") from exc
    output = []
    previous = 0
    for begin, end in sorted(spans):
        if begin < previous:
            raise OfficeVisualSourceError("The static hyperlink XML removals overlap.")
        output.append(content[previous:begin])
        previous = end
    output.append(content[previous:])
    return b"".join(output), len(spans)


def _ordinary_render_input(filename: str, source: bytes) -> tuple[str, bytes, bool]:
    """Prepare a PPTX-only static display derivative, never an external opener.

    The original strict guard is unchanged. Only external hyperlink activation
    metadata may be removed from a temporary copy. Text, geometry, style,
    images, tables and every other ZIP member remain untouched; external
    images, OLE, templates or unexpected references still fail closed.
    """
    try:
        kind = _checked_ordinary_ooxml(filename, source)
        return kind, source, False
    except OfficeVisualSourceError:
        # Ordinary DOCX and malformed source/name/size keep the existing gate.
        if (type(filename) is not str or not filename
                or PurePath(filename).name != filename
                or PurePath(filename).suffix.lower() != ".pptx"
                or type(source) is not bytes
                or not 1 <= len(source) <= _MAX_SOURCE_BYTES):
            raise
    try:
        replacements: dict[str, bytes] = {}
        with zipfile.ZipFile(BytesIO(source)) as archive:
            names = extractors._validate_ooxml_archive(archive)
            if "ppt/presentation.xml" not in names:
                raise OfficeVisualSourceError("The Office main document is missing.")
            external_count = 0
            for name in names:
                if not name.endswith(".rels"):
                    continue
                root = extractors._parse_ooxml_xml(archive, name)
                external = [node for node in root.iter()
                    if node.tag == _RELS + "Relationship"
                    and node.get("TargetMode", "").lower() == "external"]
                if not external:
                    continue
                if (root.tag != _RELS + "Relationships"
                        or any(node not in list(root) or node.get("Type") != _HYPERLINK_REL
                            or not node.get("Id") or len(node) for node in external)):
                    raise OfficeVisualSourceError("The Office external content is not a static hyperlink.")
                ids = [node.get("Id") for node in root if node.tag == _RELS + "Relationship"]
                if len(ids) != len(set(ids)):
                    raise OfficeVisualSourceError("The Office relationship identity is ambiguous.")
                path = PurePosixPath(name)
                if (path.is_absolute() or ".." in path.parts or "\\" in name
                        or path.parent.name != "_rels" or path.name == ".rels"):
                    raise OfficeVisualSourceError("The Office hyperlink owner is unsupported.")
                owner = str(path.parent.parent / path.name[:-5])
                if owner not in names or not owner.startswith("ppt/") or not owner.endswith(".xml"):
                    raise OfficeVisualSourceError("The Office hyperlink owner is unavailable.")
                external_ids = {node.get("Id") for node in external}
                owner_root = extractors._parse_ooxml_xml(archive, owner)
                expected_nodes = 0
                for node in owner_root.iter():
                    references = [key for key, value in node.attrib.items()
                        if key.startswith(_OFFICE_REL) and value in external_ids]
                    if not references:
                        continue
                    if (node.tag not in {_DRAWING + "hlinkClick", _DRAWING + "hlinkHover"}
                            or references != [_OFFICE_REL + "id"] or len(node)
                            or (node.text is not None and node.text.strip())):
                        raise OfficeVisualSourceError("The Office external hyperlink has a non-display reference.")
                    expected_nodes += 1
                owner_bytes, removed = _xml_without_nodes(archive.read(owner),
                    lambda tag, attrs: tag in {_DRAWING + "hlinkClick", _DRAWING + "hlinkHover"}
                        and attrs.get(_OFFICE_REL + "id") in external_ids)
                if removed != expected_nodes:
                    raise OfficeVisualSourceError("The Office hyperlink removal coverage changed.")
                rel_bytes, removed_rels = _xml_without_nodes(archive.read(name),
                    lambda tag, attrs: tag == _RELS + "Relationship"
                        and attrs.get("Id") in external_ids)
                if removed_rels != len(external):
                    raise OfficeVisualSourceError("The Office hyperlink relationship coverage changed.")
                replacements[owner] = owner_bytes
                replacements[name] = rel_bytes
                external_count += removed_rels
            if not external_count:
                raise OfficeVisualSourceError("The ordinary Office package is invalid.")
            output = BytesIO()
            with zipfile.ZipFile(output, "w") as derivative:
                for info in archive.infolist():
                    derivative.writestr(info, replacements.get(info.filename, archive.read(info.filename)))
            rendered_source = output.getvalue()
        if _checked_ordinary_ooxml(filename, rendered_source) != "pptx":
            raise OfficeVisualSourceError("The static Office derivative is invalid.")
        return "pptx", rendered_source, True
    except OfficeVisualSourceError:
        raise
    except Exception as exc:
        raise OfficeVisualSourceError("The static Office hyperlink derivative could not be prepared.") from exc


def _render_com_to_path(kind: str, source_path: Path, output_path: Path) -> None:
    """One Office instance inside a single-use, supervised helper process."""
    import pythoncom
    from win32com.client import DispatchEx

    application = None
    document = None
    pythoncom.CoInitialize()
    try:
        if kind == "docx":
            application = DispatchEx("Word.Application")
            application.Visible = False
            application.DisplayAlerts = 0
            application.AutomationSecurity = 3
            document = application.Documents.Open(
                FileName=str(source_path), ConfirmConversions=False,
                ReadOnly=True, AddToRecentFiles=False, Visible=False,
                OpenAndRepair=False, NoEncodingDialog=True,
            )
            document.ExportAsFixedFormat(str(output_path), 17)
        else:
            application = DispatchEx("PowerPoint.Application")
            application.AutomationSecurity = 3
            application.DisplayAlerts = 1
            document = application.Presentations.Open(
                str(source_path), True, False, False)
            document.SaveAs(str(output_path), 32)
    finally:
        try:
            if document is not None:
                if kind == "docx":
                    document.Close(False)
                else:
                    document.Saved = True
                    document.Close()
        finally:
            document = None
            try:
                if application is not None:
                    application.Quit()
            finally:
                application = None
                # PowerPoint's out-of-process COM server sometimes reports
                # RPC_E_DISCONNECTED during apartment uninitialization after
                # a successful Quit. This helper exits immediately, so the OS
                # releases its single-use apartment instead. Word unwinds
                # cleanly and retains normal uninitialization.
                if kind == "docx":
                    pythoncom.CoUninitialize()


def _helper(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in {"docx", "pptx"}:
        return 2
    # The parent must first assign this helper to a kill-on-close job. A
    # direct or prematurely released child cannot launch Office automation.
    if sys.stdin.buffer.read(1) != b"\x01":
        return 2
    kind, source_name, output_name = argv
    source_path = Path(source_name).resolve(strict=True)
    output_path = Path(output_name).resolve(strict=False)
    if (source_path.parent != output_path.parent
            or source_path.suffix.lower() != "." + kind
            or output_path.name != "rendered.pdf"
            or output_path.exists()):
        return 2
    if _checked_ordinary_ooxml(source_path.name, source_path.read_bytes()) != kind:
        return 2
    _render_com_to_path(kind, source_path, output_path)
    return 0 if output_path.is_file() else 1


def _run_office_helper(command: list[str], *, timeout_seconds: int) -> subprocess.CompletedProcess:
    """Put the helper in a Windows job before releasing its startup gate.

    COM may start an Office local server outside the helper's process tree.
    This job therefore guarantees helper/ordinary-child cleanup, not Office
    server ownership; production composition must prove that separately.
    """
    if sys.platform != "win32":
        raise OfficeVisualSourceError("Windows Office rendering requires Windows.")
    try:
        # Embedded Python does not process the shared pywin32 .pth file.
        # Load the versioned DLL through its official bootstrap module before
        # importing extensions which depend on it. Never weaken the job gate.
        import pywintypes  # noqa: F401
        import win32api
        import win32con
        import win32job
    except ImportError as exc:
        raise OfficeVisualSourceError(
            "OFFICE_RUNTIME_DEPENDENCY_UNAVAILABLE: Windows Office helper modules are unavailable."
        ) from exc

    job = None
    process = None
    process_handle = None
    try:
        job = win32job.CreateJobObject(None, "")
        limits = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation)
        limits["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, limits)
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        process_handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
            False, process.pid,
        )
        win32job.AssignProcessToJobObject(job, process_handle)
        try:
            stdout, stderr = process.communicate(input=b"\x01", timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            win32job.TerminateJobObject(job, 1)
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)
            raise OfficeVisualSourceError("The supervised Office render timed out.") from exc
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    except OfficeVisualSourceError:
        raise
    except Exception as exc:
        raise OfficeVisualSourceError("The supervised Office render could not start or finish.") from exc
    finally:
        # Closing the last job handle also terminates any helper descendants
        # that survived an exception before/after communicate.
        if job is not None:
            job.Close()
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if process_handle is not None:
            process_handle.Close()


class WindowsOfficePdfRenderer:
    """Read-only, out-of-process COM render; no silent fallback or retry.

    ``may_export`` is a server-owned decision function. A client-supplied flag
    is not sufficient to grant access. Any Office error stays a typed failure;
    there is no screenshot or raw-text fallback that could bypass policy.
    """

    def __init__(self, may_export: Callable[[str, str, str], bool]):
        if not callable(may_export):
            raise TypeError("An export authorization decision is required.")
        self._may_export = may_export

    def _require_current_export_right(self, actor_user_id: str,
            original_filename: str, original_sha: str) -> None:
        try:
            allowed = self._may_export(actor_user_id, original_filename, original_sha) is True
        except Exception:
            allowed = False
        if not allowed:
            raise PermissionError("The Office export is not authorized.")

    def render_for_actor(self, original_filename: str, original: bytes,
            *, actor_user_id: str) -> AuthorizedOfficePdfRender:
        if (type(actor_user_id) is not str or not actor_user_id
                or type(original) is not bytes or not original
                or type(original_filename) is not str or not original_filename
                or PurePath(original_filename).name != original_filename):
            raise OfficeVisualSourceError("The Office actor is invalid.")
        original_sha = sha256(original).hexdigest()
        self._require_current_export_right(actor_user_id, original_filename, original_sha)
        kind, render_input, static_hyperlinks = _ordinary_render_input(original_filename, original)
        if _office_application_registered(kind) is False:
            raise OfficeApplicationUnavailableError(
                "The required Office application is not installed or COM-registered.")

        with tempfile.TemporaryDirectory(prefix="sb-office-visual-") as temp:
            folder = Path(temp)
            source_path = folder / ("source." + kind)
            output_path = folder / "rendered.pdf"
            source_path.write_bytes(render_input)
            completed = _run_office_helper(
                [sys.executable, "-m", "code_assist_v1.knowledge_core.office_visual_windows",
                 kind, str(source_path), str(output_path)],
                timeout_seconds=_RENDER_TIMEOUT_SECONDS,
            )
            if completed.returncode != 0:
                raise OfficeVisualSourceError("The supervised Office render failed.")

            method = ("word-com-supervised-pdf-export-v1" if kind == "docx"
                else "powerpoint-com-supervised-pdf-save-v1")
            if static_hyperlinks:
                method = "powerpoint-com-supervised-pdf-static-hyperlinks-v2"

            if not output_path.is_file():
                raise OfficeVisualSourceError("Office returned no PDF render.")
            size = output_path.stat().st_size
            if not 1 <= size <= _MAX_DERIVATIVE_BYTES:
                raise OfficeVisualSourceError("The Office PDF render exceeds its limit.")
            derivative = output_path.read_bytes()
            try:
                reader = PdfReader(BytesIO(derivative), strict=True)
                pages = len(reader.pages)
                if reader.is_encrypted or not 1 <= pages <= 1000:
                    raise ValueError("Invalid PDF page inventory")
            except Exception as exc:
                raise OfficeVisualSourceError("The Office PDF page inventory is invalid.") from exc

        # The initial grant authorizes starting the Office process, not handing
        # its resulting clear derivative to the next authoring stage.
        self._require_current_export_right(actor_user_id, original_filename, original_sha)

        render = AuthorizedOfficePdfRender(
            actor_user_id=actor_user_id,
            original_filename=original_filename,
            original_sha256=original_sha,
            original_format=kind,
            derivative_pdf=derivative,
            derivative_sha256=sha256(derivative).hexdigest(),
            expected_pages=pages,
            render_method=method,
            export_permitted=True,
        )
        render.checked(actor_user_id=actor_user_id,
            original_filename=original_filename, original=original)
        return render


__all__ = ["WindowsOfficePdfRenderer", "OfficeApplicationUnavailableError"]


if __name__ == "__main__":
    raise SystemExit(_helper(sys.argv[1:]))
