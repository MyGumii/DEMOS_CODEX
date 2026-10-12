"""Opt-in resource-isolated PDF geometry; no server/authoring defaults changed."""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePath
import queue
import struct
import threading
import time

from .document_pdf_geometry import PdfGeometryError, PdfGeometryLimits
from .document_pdf_geometry_codec import _decode


class PdfProcessError(PdfGeometryError):
    def __init__(self, reason, *, pid=0, returncode=None):
        self.reason, self.pid, self.returncode = reason, pid, returncode
        super().__init__(f"Isolated PDF worker failed: {reason}")


@dataclass(frozen=True, slots=True)
class PdfProcessPolicy:
    memory_bytes: int = 512 * 1024 * 1024
    timeout_seconds: float = 60.0
    stderr_bytes: int = 64 * 1024
    max_processes: int = 4  # May include launcher, interpreter and headless conhost.
    cleanup_seconds: float = 5.0

    def __post_init__(self):
        for name in ("memory_bytes", "stderr_bytes", "max_processes"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError("PDF process integer limits must be positive")
        for name in ("timeout_seconds", "cleanup_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("PDF process time limits must be finite and positive")
        if (self.memory_bytes > 16 * 1024**3 or self.max_processes > 4
                or self.stderr_bytes > 1024 * 1024 or self.timeout_seconds > 300
                or self.cleanup_seconds > 10):
            raise ValueError("PDF process policy exceeds its supported resource range")


@dataclass(frozen=True, slots=True)
class PdfProcessResult:
    document: object
    pid: int
    elapsed_seconds: float
    peak_committed_bytes: int
    total_processes: int


def _run_worker(python, script, parts, *, policy, output_bytes, cancel_event=None):
    """Private process transport; production caller pins the worker script path.

    Deadline includes elapsed startup time, but cannot preempt synchronous
    Windows CreateProcess/Job APIs. Cleanup has a separate bounded grace period.
    """
    from ._document_pdf_windows import WindowsPdfProcess
    if os.name != "nt":
        raise PdfProcessError("UNSUPPORTED_PLATFORM")
    started = time.monotonic()
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError("CANCELLED")
    try:
        process = WindowsPdfProcess([str(python), "-I", "-B", "-u", str(script)],
            cwd=str(script.parent), memory_bytes=policy.memory_bytes,
            max_processes=policy.max_processes, cleanup_seconds=policy.cleanup_seconds)
    except OSError as exc:
        raise PdfProcessError("START_FAILED") from exc
    errors = queue.Queue()
    buffers = [bytearray(), bytearray()]

    def read(stream, target, limit):
        try:
            while chunk := stream.read(32768):
                if len(target) + len(chunk) > limit:
                    errors.put("OUTPUT_LIMIT")
                    return
                target.extend(chunk)
        except OSError:
            errors.put("PIPE_FAILED")

    def write():
        try:
            for part in parts:
                remaining = memoryview(part)
                while remaining:
                    written = process.stdin.write(remaining[:32768])
                    if not written:
                        raise OSError("PDF input pipe stopped")
                    remaining = remaining[written:]
        except OSError:
            errors.put("PIPE_FAILED")
        finally:
            process.stdin.close()

    threads = [threading.Thread(target=read, args=(process.stdout, buffers[0], output_bytes), daemon=True),
        threading.Thread(target=read, args=(process.stderr, buffers[1], policy.stderr_bytes), daemon=True),
        threading.Thread(target=write, daemon=True)]
    metrics = None
    try:
        for thread in threads:
            thread.start()
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise PdfProcessError("CANCELLED", pid=process.pid)
            if time.monotonic() - started > policy.timeout_seconds:
                raise PdfProcessError("TIMEOUT", pid=process.pid)
            if not errors.empty():
                raise PdfProcessError(errors.get_nowait(), pid=process.pid)
            code = process.poll()
            if code is not None and not any(thread.is_alive() for thread in threads):
                if not errors.empty():
                    raise PdfProcessError(errors.get_nowait(), pid=process.pid, returncode=code)
                if code != 0:
                    raise PdfProcessError("PROCESS_FAILED", pid=process.pid, returncode=code)
                metrics = process.metrics()
                break
            time.sleep(0.01)
        return bytes(buffers[0]), process.pid, time.monotonic() - started, metrics
    finally:
        cleanup_until = time.monotonic() + policy.cleanup_seconds
        cleanup_failed = False
        try:
            try:
                process.stop()
            except OSError:
                cleanup_failed = True
            try:
                process.wait_stopped(max(0, cleanup_until - time.monotonic()))
            except OSError:
                cleanup_failed = True
            # If explicit stop failed, kill-on-close is still applied before
            # joining the pipes. An unverified cleanup is never a success.
            process.close_job()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(max(0, cleanup_until - time.monotonic()))
            if cleanup_failed or any(thread.is_alive() for thread in threads):
                raise PdfProcessError("CLEANUP_FAILED", pid=process.pid)
        finally:
            process.close()


def extract_pdf_geometry_isolated(filename, raw, *, python_executable,
        saved_manifest=None, limits=PdfGeometryLimits(), policy=PdfProcessPolicy(),
        cancel_event=None, allow_visual_only=False, allow_ambiguous_grid=False):
    """Extract or rebind inside a bounded Windows job, then decode its trusted output.

    Not an OS permission/network sandbox. Executable/code are trusted server-owned
    configuration, never supplied by an upload. Stored manifests are re-extracted
    inside the child; the parent does not invoke the PDF parser a second time.
    """
    if (type(limits) is not PdfGeometryLimits or type(policy) is not PdfProcessPolicy
            or type(allow_visual_only) is not bool or type(allow_ambiguous_grid) is not bool
            or (allow_ambiguous_grid and not allow_visual_only)):
        raise PdfProcessError("INPUT_INVALID")
    if (type(filename) is not str or PurePath(filename).suffix.lower() != ".pdf"
            or type(raw) is not bytes or not 0 < len(raw) <= limits.max_source_bytes):
        raise PdfProcessError("INPUT_INVALID")
    if saved_manifest is not None and (type(saved_manifest) is not bytes
            or not 0 < len(saved_manifest) <= limits.max_manifest_bytes):
        raise PdfProcessError("INPUT_INVALID")
    if not isinstance(python_executable, (str, Path)):
        raise PdfProcessError("INPUT_INVALID")
    python = Path(python_executable)
    if not python.is_absolute() or not python.is_file():
        raise PdfProcessError("INPUT_INVALID")
    # The worker accepts bounded policies, not arbitrary extended parser budgets.
    defaults = asdict(PdfGeometryLimits())
    if any(value > defaults[name] for name, value in asdict(limits).items()):
        raise PdfProcessError("INPUT_INVALID")
    try:
        header = json.dumps({"version": 3 if allow_ambiguous_grid else 2 if allow_visual_only else 1,
            **({"allow_visual_only": True} if allow_visual_only else {}),
            **({"allow_ambiguous_grid": True} if allow_ambiguous_grid else {}),
            "filename": filename, "raw_bytes": len(raw),
            "manifest_bytes": len(saved_manifest) if saved_manifest is not None else 0,
            "limits": asdict(limits)}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise PdfProcessError("INPUT_INVALID") from exc
    if len(header) > 8192:
        raise PdfProcessError("INPUT_INVALID")
    call_started = time.monotonic()
    wire, pid, elapsed, metrics = _run_worker(python,
        Path(__file__).with_name("_document_pdf_worker.py"),
        (struct.pack("!I", len(header)), header, raw, saved_manifest or b""),
        policy=policy, output_bytes=limits.max_manifest_bytes, cancel_event=cancel_event)
    # Windows may include a headless console host as well as the venv launcher
    # and interpreter. Report actual job count rather than asserting exactly
    # two processes. This is a resource boundary, not a security sandbox.
    # This is output from our completed owned worker, NOT a user-stored sidecar.
    # The trusted worker performed raw extraction (and mandatory rebind on load).
    try:
        document = _decode(wire, limits)
        if document.source_sha256 != sha256(raw).hexdigest():
            raise PdfGeometryError("Foreign PDF worker output")
    except PdfGeometryError as exc:
        raise PdfProcessError("PROTOCOL_ERROR", pid=pid) from exc
    if cancel_event is not None and cancel_event.is_set():
        raise PdfProcessError("CANCELLED", pid=pid)
    # Decoding is byte/depth bounded and cannot be preempted mid-JSON call in
    # the parent. Reject a late result; do not claim a real-time API deadline.
    elapsed = time.monotonic() - call_started
    if elapsed > policy.timeout_seconds:
        raise PdfProcessError("TIMEOUT", pid=pid)
    return PdfProcessResult(document, pid, elapsed, metrics["peak_committed_bytes"], metrics["total_processes"])
