"""Root-bound, read-only source acquisition for the offline P2.3 importer.

Every path component is opened relative to its already-open parent without
following reparses.  The complete handle chain is retained until the caller's
database outcome so reviewed bytes cannot be exchanged after verification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ntpath
import os
from pathlib import Path
import re
from typing import Mapping

from ..errors import KnowledgeCoreDatabasePathError
from ..repositories.migration import VerifiedBlobEvidence
from .importer import _reviewed_plan


_RECORD_ID_RE = re.compile(r"rec_[0-9a-f]{24}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_WINDOWS_EPOCH_TICKS = 116_444_736_000_000_000
MAX_BOUND_SOURCE_BYTES = 256 * 1024 * 1024


class LegacySourceBindingError(KnowledgeCoreDatabasePathError):
    code = "LEGACY_SOURCE_BINDING_FAILED"


class _SourceHandleLease:
    __slots__ = ("_closed", "_handles")

    def __init__(self, handles: list[int]) -> None:
        self._handles = handles
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        from ...tools.legacy_inventory import _close_windows_handle

        first_error: OSError | None = None
        for handle in reversed(self._handles):
            try:
                _close_windows_handle(handle)
            except OSError as exc:
                first_error = first_error or exc
        self._handles.clear()
        if first_error is not None:
            raise first_error


@dataclass(frozen=True)
class BoundSourceObservation:
    """Reviewed bytes and the lease that keeps their source object immutable."""

    record_id: str
    content: bytes
    raw_sha256: str
    size_bytes: int
    mtime_ns: int
    lease: object

    def verified_blob_evidence(
        self,
        *,
        media_type: str,
        normalized_content_sha256: str | None = None,
        normalizer_version: str | None = None,
    ) -> VerifiedBlobEvidence:
        return VerifiedBlobEvidence(
            raw_sha256=self.raw_sha256,
            size_bytes=self.size_bytes,
            media_type=media_type,
            normalized_content_sha256=normalized_content_sha256,
            normalizer_version=normalizer_version,
        )

    def close(self) -> None:
        closer = getattr(self.lease, "close", None)
        if callable(closer):
            closer()


@dataclass(frozen=True)
class _HandleInformation:
    attributes: int
    file_index: int
    links: int
    mtime_ns: int
    size_bytes: int
    volume_serial: int


def _raise_ntstatus(status: int, operation: str) -> None:
    if status >= 0:
        return
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    translate = ntdll.RtlNtStatusToDosError
    translate.argtypes = (wintypes.ULONG,)
    translate.restype = wintypes.ULONG
    raise OSError(int(translate(status & 0xFFFFFFFF)), operation)


def _nt_open_relative(root_handle: int, name: str, *, directory: bool) -> int:
    import ctypes
    from ctypes import wintypes

    from ...tools.legacy_inventory import _validate_simple_windows_component

    name = _validate_simple_windows_component(name)

    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        )

    class _OBJECT_ATTRIBUTES(ctypes.Structure):
        _fields_ = (
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(_UNICODE_STRING)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        )

    class _IO_STATUS_UNION(ctypes.Union):
        _fields_ = (("Status", ctypes.c_long), ("Pointer", wintypes.LPVOID))

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = (("Value", _IO_STATUS_UNION), ("Information", ctypes.c_size_t))

    encoded = name.encode("utf-16-le")
    name_buffer = ctypes.create_unicode_buffer(name)
    unicode_name = _UNICODE_STRING(
        len(encoded), len(encoded), ctypes.cast(name_buffer, wintypes.LPWSTR)
    )
    attributes = _OBJECT_ATTRIBUTES(
        ctypes.sizeof(_OBJECT_ATTRIBUTES),
        wintypes.HANDLE(root_handle),
        ctypes.pointer(unicode_name),
        0x40,
        None,
        None,
    )
    io_status = _IO_STATUS_BLOCK()
    handle = wintypes.HANDLE()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    create_file = ntdll.NtCreateFile
    create_file.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(_OBJECT_ATTRIBUTES),
        ctypes.POINTER(_IO_STATUS_BLOCK),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.LPVOID,
        wintypes.ULONG,
    )
    create_file.restype = ctypes.c_long
    desired_access = 0x0001 | 0x0080 | 0x00100000
    if directory:
        desired_access |= 0x0020
    options = 0x00000020 | 0x00200000
    options |= 0x00000001 if directory else 0x00000040
    status = int(
        create_file(
            ctypes.byref(handle),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x10 if directory else 0x80,
            0x0001,
            1,
            options,
            None,
            0,
        )
    )
    _raise_ntstatus(status, "NtCreateFile(open reviewed source)")
    if handle.value is None:
        raise OSError("reviewed_source_missing_handle")
    return int(handle.value)


def _handle_information(handle: int) -> _HandleInformation:
    import ctypes
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = (("low", wintypes.DWORD), ("high", wintypes.DWORD))

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("attributes", wintypes.DWORD),
            ("creation", _FILETIME),
            ("access", _FILETIME),
            ("write", _FILETIME),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("index_high", wintypes.DWORD),
            ("index_low", wintypes.DWORD),
        )

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel32.GetFileInformationByHandle
    get_info.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
    )
    get_info.restype = wintypes.BOOL
    information = _BY_HANDLE_FILE_INFORMATION()
    if not get_info(wintypes.HANDLE(handle), ctypes.byref(information)):
        raise ctypes.WinError(ctypes.get_last_error())
    write_ticks = (int(information.write.high) << 32) | int(information.write.low)
    return _HandleInformation(
        attributes=int(information.attributes),
        file_index=(int(information.index_high) << 32) | int(information.index_low),
        links=int(information.links),
        mtime_ns=(write_ticks - _WINDOWS_EPOCH_TICKS) * 100,
        size_bytes=(int(information.size_high) << 32) | int(information.size_low),
        volume_serial=int(information.volume_serial),
    )


def _read_handle(handle: int, *, expected_size: int) -> bytes:
    import ctypes
    from ctypes import wintypes

    if expected_size > MAX_BOUND_SOURCE_BYTES:
        raise OSError("reviewed_source_too_large")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    read_file = kernel32.ReadFile
    read_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    read_file.restype = wintypes.BOOL
    chunks: list[bytes] = []
    total = 0
    while True:
        remaining = expected_size - total
        buffer = ctypes.create_string_buffer(min(1024 * 1024, remaining + 1))
        read = wintypes.DWORD()
        if not read_file(
            wintypes.HANDLE(handle), buffer, len(buffer), ctypes.byref(read), None
        ):
            error = ctypes.get_last_error()
            if error == 38:
                break
            raise ctypes.WinError(error)
        if read.value == 0:
            break
        total += int(read.value)
        if total > expected_size:
            raise OSError("reviewed_source_grew")
        chunks.append(buffer.raw[: read.value])
    return b"".join(chunks)


def _norm_windows_path(value: str | Path) -> str:
    return ntpath.normcase(ntpath.normpath(str(value).replace("/", "\\")))


def _relative_parts(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or value.startswith("/")
        or re.match(r"^[A-Za-z]:", value) is not None
    ):
        raise LegacySourceBindingError(
            "Reviewed source path is not canonical POSIX relative form."
        )
    parts = tuple(value.split("/"))
    if any(not part or part in {".", ".."} for part in parts):
        raise LegacySourceBindingError(
            "Reviewed source path contains an unsafe component."
        )
    return parts


def _root_rows(draft: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    snapshot = draft.get("source_snapshot")
    rows = snapshot.get("roots") if isinstance(snapshot, Mapping) else None
    if not isinstance(rows, list):
        raise LegacySourceBindingError("Reviewed source roots are absent.")
    result: dict[str, Mapping[str, object]] = {}
    for row in rows:
        root_id = row.get("root_id") if isinstance(row, Mapping) else None
        if not isinstance(root_id, str) or root_id in result:
            raise LegacySourceBindingError("Reviewed source-root identities are invalid.")
        result[root_id] = row
    return result


class ReviewedLegacySourceReader:
    """Read exact physical plan records through their reviewed root boundary."""

    __slots__ = ("_draft", "_records", "_roots")

    def __init__(self, reviewed_plan: bytes, *, expected_plan_sha256: str) -> None:
        if not isinstance(reviewed_plan, bytes):
            raise TypeError("reviewed_plan must be canonical artifact bytes.")
        plan = _reviewed_plan(reviewed_plan, expected_plan_sha256=expected_plan_sha256)
        draft = plan.get("draft")
        records = draft.get("records") if isinstance(draft, Mapping) else None
        if not isinstance(draft, Mapping) or not isinstance(records, list):
            raise LegacySourceBindingError("Reviewed plan records are invalid.")
        self._draft = draft
        self._roots = _root_rows(draft)
        self._records = {
            str(row["record_id"]): row
            for row in records
            if isinstance(row, Mapping) and isinstance(row.get("record_id"), str)
        }
        if len(self._records) != len(records):
            raise LegacySourceBindingError("Reviewed plan record identities are invalid.")

    def _open_root(self, row: Mapping[str, object]) -> tuple[int, Path]:
        if os.name != "nt":
            raise LegacySourceBindingError(
                "Reviewed source acquisition requires the approved Windows host."
            )
        configured = row.get("configured_path")
        resolved = row.get("resolved_path")
        identity = row.get("filesystem_identity")
        expected_volume = row.get("volume")
        if (
            not isinstance(configured, str)
            or not os.path.isabs(configured)
            or not isinstance(resolved, str)
            or not os.path.isabs(resolved)
            or not isinstance(identity, Mapping)
            or set(identity) != {"device", "inode"}
            or not isinstance(expected_volume, Mapping)
        ):
            raise LegacySourceBindingError("Reviewed source-root facts are invalid.")
        from ...tools.legacy_inventory import (
            _WindowsDirectoryAnchor,
            _windows_final_path,
        )
        from ...tools.windows_volume import probe_path

        try:
            probe = probe_path(configured)
            volume = probe.get("volume") if isinstance(probe, Mapping) else None
            reparse = probe.get("reparse") if isinstance(probe, Mapping) else None
            if (
                probe.get("eligible") is not True
                or probe.get("exists") is not True
                or _norm_windows_path(str(probe.get("resolved_path")))
                != _norm_windows_path(resolved)
                or not isinstance(volume, Mapping)
                or str(volume.get("drive_type") or "").casefold() != "fixed"
                or str(volume.get("filesystem") or "").casefold() != "ntfs"
                or _norm_windows_path(str(volume.get("root")))
                != _norm_windows_path(str(expected_volume.get("root")))
                or not isinstance(reparse, Mapping)
                or reparse.get("present") is not False
                or reparse.get("components") != []
            ):
                raise OSError("reviewed_root_probe_changed")
            path = Path(configured)
            before = path.stat(follow_symlinks=False)
            expected_identity = (int(identity["device"]), int(identity["inode"]))
            if (
                (int(before.st_dev), int(before.st_ino)) != expected_identity
                or not path.is_dir()
            ):
                raise OSError("reviewed_root_identity_changed")
            anchor = _WindowsDirectoryAnchor.open(path, writable=False)
            try:
                after = path.stat(follow_symlinks=False)
                information = _handle_information(anchor.handle)
                if (
                    (int(after.st_dev), int(after.st_ino)) != expected_identity
                    or information.attributes & 0x400
                    or not information.attributes & 0x10
                    or _norm_windows_path(_windows_final_path(anchor.handle))
                    != _norm_windows_path(resolved)
                ):
                    raise OSError("reviewed_root_handle_changed")
                return anchor.handle, Path(resolved)
            except Exception:
                anchor.close()
                raise
        except LegacySourceBindingError:
            raise
        except Exception:
            raise LegacySourceBindingError(
                "A reviewed source root no longer matches its frozen identity."
            ) from None

    def read_record(self, record_id: str) -> BoundSourceObservation:
        if not isinstance(record_id, str) or _RECORD_ID_RE.fullmatch(record_id) is None:
            raise LegacySourceBindingError("Reviewed record ID is invalid.")
        row = self._records.get(record_id)
        source = row.get("source_record") if isinstance(row, Mapping) else None
        if (
            not isinstance(source, Mapping)
            or source.get("record_id") != record_id
            or source.get("record_kind") != "physical_file"
            or source.get("entry_type") != "file"
            or source.get("source_ordinal") != 0
        ):
            raise LegacySourceBindingError(
                "Only reviewed physical source files may be acquired."
            )
        root_id = source.get("root_id")
        root_row = self._roots.get(str(root_id))
        expected_sha256 = source.get("sha256")
        expected_size = source.get("size_bytes")
        stat = source.get("stat")
        expected_mtime = stat.get("mtime_ns") if isinstance(stat, Mapping) else None
        if (
            root_row is None
            or not isinstance(expected_sha256, str)
            or _SHA256_RE.fullmatch(expected_sha256) is None
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or not isinstance(expected_mtime, int)
            or isinstance(expected_mtime, bool)
            or expected_mtime < 0
        ):
            raise LegacySourceBindingError("Reviewed source-file facts are invalid.")
        parts = _relative_parts(source.get("relative_path"))
        handles: list[int] = []
        try:
            root_handle, root_final = self._open_root(root_row)
            handles.append(root_handle)
            parent_handle = root_handle
            expected_path = root_final
            from ...tools.legacy_inventory import _windows_final_path

            for index, part in enumerate(parts):
                directory = index < len(parts) - 1
                handle = _nt_open_relative(parent_handle, part, directory=directory)
                handles.append(handle)
                expected_path = expected_path / part
                information = _handle_information(handle)
                if (
                    information.attributes & 0x400
                    or bool(information.attributes & 0x10) != directory
                    or _norm_windows_path(_windows_final_path(handle))
                    != _norm_windows_path(expected_path)
                ):
                    raise OSError("reviewed_source_component_changed")
                parent_handle = handle
            file_handle = handles[-1]
            before = _handle_information(file_handle)
            if (
                before.size_bytes != expected_size
                or before.mtime_ns != expected_mtime
                or before.attributes & 0x400
                or before.attributes & 0x10
            ):
                raise OSError("reviewed_source_stat_changed")
            raw = _read_handle(file_handle, expected_size=expected_size)
            after = _handle_information(file_handle)
            if (
                after != before
                or len(raw) != expected_size
                or hashlib.sha256(raw).hexdigest() != expected_sha256
                or _norm_windows_path(_windows_final_path(file_handle))
                != _norm_windows_path(expected_path)
            ):
                raise OSError("reviewed_source_bytes_changed")
            lease = _SourceHandleLease(handles)
            handles = []
            return BoundSourceObservation(
                record_id=record_id,
                content=raw,
                raw_sha256=expected_sha256,
                size_bytes=expected_size,
                mtime_ns=expected_mtime,
                lease=lease,
            )
        except LegacySourceBindingError:
            raise
        except Exception:
            raise LegacySourceBindingError(
                "A reviewed source file no longer matches its frozen bytes."
            ) from None
        finally:
            if handles:
                from ...tools.legacy_inventory import _close_windows_handle

                for handle in reversed(handles):
                    try:
                        _close_windows_handle(handle)
                    except OSError:
                        pass

    def verify_snapshot_high_water(self) -> None:
        """Recompute every import-bearing physical root before the first write.

        The ``legacy`` role's high-water mark is the P2.1 code-coverage ledger,
        not a physical source-tree capture, and that role is never a P2.3 data
        source.  All remaining reviewed roots use the scanner's exact capture
        algorithm and must still equal the frozen ``after`` mark.
        """

        snapshot = self._draft.get("source_snapshot")
        marks = snapshot.get("high_water_marks") if isinstance(snapshot, Mapping) else None
        if not isinstance(marks, list):
            raise LegacySourceBindingError("Reviewed source high-water marks are absent.")
        by_root = {
            str(row.get("root_id")): row
            for row in marks
            if isinstance(row, Mapping) and isinstance(row.get("root_id"), str)
        }
        if len(by_root) != len(marks):
            raise LegacySourceBindingError("Reviewed source high-water marks are invalid.")
        from ...tools.legacy_inventory import (
            _Root,
            _State,
            _capture,
            _close_windows_handle,
        )

        for root_id in sorted(self._roots):
            row = self._roots[root_id]
            if row.get("role") == "legacy":
                continue
            mark = by_root.get(root_id)
            expected = mark.get("after") if isinstance(mark, Mapping) else None
            if not isinstance(expected, Mapping) or mark.get("changed") is not False:
                raise LegacySourceBindingError(
                    "Reviewed source high-water evidence is invalid."
                )
            handle: int | None = None
            try:
                handle, resolved = self._open_root(row)
                descriptor = _Root(
                    root_id=root_id,
                    role=str(row["role"]),
                    configured=Path(str(row["configured_path"])),
                    resolved=resolved,
                    path_source=str(row["path_source"]),
                )
                state = _State()
                observed = _capture(descriptor, state, "p2_3_verify_snapshot")
                if state.errors or observed != dict(expected):
                    raise OSError("reviewed_source_high_water_changed")
            except LegacySourceBindingError:
                raise
            except Exception:
                raise LegacySourceBindingError(
                    "A reviewed source root changed after inventory approval."
                ) from None
            finally:
                if handle is not None:
                    try:
                        _close_windows_handle(handle)
                    except OSError:
                        pass

    def read_records(
        self, record_ids: tuple[str, ...]
    ) -> dict[str, BoundSourceObservation]:
        if (
            not isinstance(record_ids, tuple)
            or record_ids != tuple(sorted(set(record_ids)))
        ):
            raise LegacySourceBindingError(
                "Reviewed record IDs must be sorted and unique."
            )
        observations: dict[str, BoundSourceObservation] = {}
        try:
            for record_id in record_ids:
                observations[record_id] = self.read_record(record_id)
            return observations
        except Exception:
            for observation in reversed(tuple(observations.values())):
                try:
                    observation.close()
                except OSError:
                    pass
            raise


__all__ = [
    "BoundSourceObservation",
    "LegacySourceBindingError",
    "MAX_BOUND_SOURCE_BYTES",
    "ReviewedLegacySourceReader",
]
