"""Explicit, pinned LibreOffice render of already-authorized ordinary OOXML.

This alternate renderer does not install an engine, change the default COM
route, open protected Office files, or fall back to text.  A PDF page inventory
proves a bounded derivative, not font/animation or visual-semantic fidelity.
The Windows job owns engine descendants; it is not a security sandbox.  The
entrypoint is pinned, not every dependency in the trusted engine distribution.
"""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path, PurePath
import re
import stat
import tempfile
import threading
import time
from typing import Callable
from zipfile import ZipFile

from pypdf import PdfReader

from .._document_pdf_windows import WindowsPdfProcess
from ..document_source_tables import _presentation_parts
from .office_visual_source import AuthorizedOfficePdfRender, OfficeVisualSourceError
from .office_visual_windows import _ordinary_render_input


_MAX_DERIVATIVE_BYTES = 64 * 1024 * 1024
_MAX_EXECUTABLE_BYTES = 64 * 1024 * 1024
_MAX_PROCESS_OUTPUT_BYTES = 1024 * 1024
_PROCESS_MEMORY_BYTES = 1024 * 1024 * 1024
_MAX_PROCESSES = 6
_RENDER_TIMEOUT_SECONDS = 90
_CLEANUP_SECONDS = 5
_HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@contextmanager
def _pinned_executable(path: Path, expected_sha256: str):
    """Hold a read-only Windows lease denying entrypoint writes/deletes.

    Launch using the handle's final path rather than re-resolving a directory
    alias after hashing.  This is an entrypoint identity/TOCTOU check, not a
    package signature or a pin of LibreOffice's sibling binaries and DLLs.
    """
    if os.name != "nt":
        raise OfficeVisualSourceError("LibreOffice job rendering requires Windows.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
        wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE,
        wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel.ReadFile.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = None
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or not 1 <= resolved.stat().st_size <= _MAX_EXECUTABLE_BYTES:
            raise ValueError("Invalid executable inventory")
        # GENERIC_READ, FILE_SHARE_READ only, OPEN_EXISTING.
        handle = kernel.CreateFileW(str(resolved), 0x80000000, 1, None, 3, 0, None)
        if handle == ctypes.c_void_p(-1).value or handle is None:
            handle = None
            raise OSError("The executable could not be pinned")
        length = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not 1 <= length <= 32768:
            raise OSError("Invalid executable final path")
        buffer = ctypes.create_unicode_buffer(length + 1)
        actual = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not 1 <= actual < len(buffer):
            raise OSError("Invalid executable final path")
        final_name = buffer.value
        ordinary_name = final_name[4:] if final_name.startswith("\\\\?\\") else final_name
        if (ordinary_name.startswith("UNC\\")
                or os.path.normcase(ordinary_name) != os.path.normcase(str(resolved))):
            raise ValueError("The executable final path changed")
        digest, count = sha256(), 0
        chunk = ctypes.create_string_buffer(65536)
        read = wintypes.DWORD()
        while True:
            if not kernel.ReadFile(handle, chunk, len(chunk), ctypes.byref(read), None):
                raise OSError("The pinned executable could not be read")
            if not read.value:
                break
            count += read.value
            if count > _MAX_EXECUTABLE_BYTES:
                raise ValueError("The executable exceeds its limit")
            digest.update(chunk.raw[:read.value])
        if not count or digest.hexdigest() != expected_sha256:
            raise ValueError("The executable SHA-256 changed")
        yield Path(final_name)
    except OfficeVisualSourceError:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise OfficeVisualSourceError("The configured LibreOffice executable is not the pinned binary.") from exc
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def _pptx_slide_count(kind: str, render_input: bytes) -> int | None:
    if kind != "pptx":
        return None
    try:
        with ZipFile(BytesIO(render_input)) as archive:
            parts = _presentation_parts(archive)
            if not 1 <= len(parts) <= 1000 or any(part not in archive.namelist() for part in parts):
                raise ValueError("Invalid declared slide inventory")
        return len(parts)
    except Exception as exc:
        raise OfficeVisualSourceError("The ordinary PPTX slide inventory is invalid.") from exc


def _run_libreoffice(command: list[str], *, cwd: Path, output_path: Path,
        timeout_seconds: int) -> None:
    """Bound engine lifetime, output, memory and the entire owned process job.

    Drain both pipes without retaining source-bearing engine messages.  No
    environment inheritance, shell, global process kill, or recursive retry.
    """
    process = None
    readers = []
    overflow = threading.Event()
    read_failed = threading.Event()
    until = time.monotonic() + timeout_seconds

    def drain(stream):
        count = 0
        try:
            while chunk := stream.read(65536):
                count += len(chunk)
                if count > _MAX_PROCESS_OUTPUT_BYTES:
                    overflow.set()
        except (OSError, ValueError):
            read_failed.set()

    try:
        process = WindowsPdfProcess(command, cwd=str(cwd),
            memory_bytes=_PROCESS_MEMORY_BYTES, max_processes=_MAX_PROCESSES,
            cleanup_seconds=_CLEANUP_SECONDS)
        process.stdin.close()
        for stream in (process.stdout, process.stderr):
            thread = threading.Thread(target=drain, args=(stream,), daemon=True)
            readers.append(thread)
            thread.start()
        while True:
            if overflow.is_set() or read_failed.is_set():
                raise OfficeVisualSourceError("The supervised LibreOffice process output exceeded its limit or failed.")
            if output_path.exists() and output_path.stat().st_size > _MAX_DERIVATIVE_BYTES:
                raise OfficeVisualSourceError("The LibreOffice PDF render exceeds its limit.")
            code = process.poll()
            if code is not None and process.metrics()["active_processes"] == 0:
                if code != 0:
                    raise OfficeVisualSourceError("The supervised LibreOffice render failed.")
                break
            if time.monotonic() >= until:
                raise OfficeVisualSourceError("The supervised LibreOffice render timed out.")
            time.sleep(0.02)
        for thread in readers:
            thread.join(max(0, until - time.monotonic()))
        if any(thread.is_alive() for thread in readers) or overflow.is_set() or read_failed.is_set():
            raise OfficeVisualSourceError("The supervised LibreOffice output did not finish within its limit.")
    except OfficeVisualSourceError:
        raise
    except Exception as exc:
        raise OfficeVisualSourceError("The supervised LibreOffice render could not start or finish.") from exc
    finally:
        if process is not None:
            cleanup_error = None
            try:
                # Even success closes a kill-on-close job; every error first
                # terminates the owned family and observes bounded cleanup.
                try:
                    if process.poll() is None or process.metrics()["active_processes"]:
                        process.stop()
                finally:
                    # A stop error must not skip observing the job's terminal
                    # state. The close below remains a kill-on-close backstop.
                    process.wait_stopped(_CLEANUP_SECONDS)
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    process.close()
                except Exception as exc:
                    cleanup_error = exc
                finally:
                    for thread in readers:
                        thread.join(0.1)
            if cleanup_error is not None:
                raise OfficeVisualSourceError("The supervised LibreOffice job cleanup could not be verified.") from cleanup_error


def _checked_pdf(output_path: Path, slide_count: int | None) -> tuple[bytes, int]:
    try:
        if output_path.is_symlink() or not stat.S_ISREG(output_path.stat().st_mode):
            raise ValueError("Not an ordinary output file")
        if not 1 <= output_path.stat().st_size <= _MAX_DERIVATIVE_BYTES:
            raise ValueError("Invalid output size")
        with output_path.open("rb") as stream:
            derivative = stream.read(_MAX_DERIVATIVE_BYTES + 1)
        if not derivative.startswith(b"%PDF-") or not 1 <= len(derivative) <= _MAX_DERIVATIVE_BYTES:
            raise ValueError("Invalid PDF bytes")
        reader = PdfReader(BytesIO(derivative), strict=True)
        pages = len(reader.pages)
        if reader.is_encrypted or not 1 <= pages <= 1000 or (slide_count is not None and pages != slide_count):
            raise ValueError("Invalid PDF page inventory")
        return derivative, pages
    except Exception as exc:
        raise OfficeVisualSourceError("The LibreOffice PDF page inventory is invalid or incomplete.") from exc


class LibreOfficePdfRenderer:
    """An explicitly selected, pinned alternative to the ordinary COM renderer."""

    def __init__(self, may_export: Callable[[str, str, str], bool], *,
            executable: str | Path, executable_sha256: str,
            timeout_seconds: int = _RENDER_TIMEOUT_SECONDS):
        if not callable(may_export):
            raise TypeError("An export authorization decision is required.")
        if (not isinstance(executable, (str, Path)) or not str(executable)
                or not Path(executable).is_absolute()
                or str(executable).startswith("\\\\")
                or Path(executable).name.lower() not in {"soffice.exe", "soffice.com"}
                or type(executable_sha256) is not str or _HEX_SHA256.fullmatch(executable_sha256) is None
                or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= _RENDER_TIMEOUT_SECONDS):
            raise OfficeVisualSourceError("An explicit pinned LibreOffice executable and bounded timeout are required.")
        self._may_export = may_export
        self._executable = Path(executable)
        self._executable_sha256 = executable_sha256
        self._timeout_seconds = timeout_seconds

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
        slide_count = _pptx_slide_count(kind, render_input)
        with _pinned_executable(self._executable, self._executable_sha256) as executable:
            with tempfile.TemporaryDirectory(prefix="sb-office-libreoffice-") as temporary:
                folder = Path(temporary)
                source_path = folder / ("source." + kind)
                output_folder = folder / "output"
                profile = folder / "profile"
                output_folder.mkdir()
                profile.mkdir()
                source_path.write_bytes(render_input)
                output_path = output_folder / "source.pdf"
                pdf_filter = "impress_pdf_Export" if kind == "pptx" else "writer_pdf_Export"
                _run_libreoffice([str(executable), "-env:UserInstallation=" + profile.as_uri(),
                    "--headless", "--nologo", "--nodefault", "--nolockcheck", "--norestore",
                    "--convert-to", "pdf:" + pdf_filter, "--outdir", str(output_folder),
                    str(source_path)], cwd=executable.parent, output_path=output_path,
                    timeout_seconds=self._timeout_seconds)
                if tuple(output_folder.iterdir()) != (output_path,):
                    raise OfficeVisualSourceError("LibreOffice returned an unexpected output inventory.")
                derivative, pages = _checked_pdf(output_path, slide_count)
        self._require_current_export_right(actor_user_id, original_filename, original_sha)
        method = ("libreoffice-impress-pinned-job-pdf-v1" if kind == "pptx"
            else "libreoffice-writer-pinned-job-pdf-v1")
        if static_hyperlinks:
            method = "libreoffice-impress-pinned-job-static-hyperlinks-v1"
        render = AuthorizedOfficePdfRender(actor_user_id=actor_user_id,
            original_filename=original_filename, original_sha256=original_sha,
            original_format=kind, derivative_pdf=derivative,
            derivative_sha256=sha256(derivative).hexdigest(), expected_pages=pages,
            render_method=method, export_permitted=True)
        render.checked(actor_user_id=actor_user_id,
            original_filename=original_filename, original=original)
        return render


__all__ = ["LibreOfficePdfRenderer"]
