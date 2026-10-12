"""Deterministic Windows path and volume eligibility probing.

The P0.4 probe is deliberately read-only and uses only the Python standard
library.  It follows an existing path to its final target, records reparse
points in the requested path, and accepts that path only when the final volume
is fixed, local NTFS.  Reparse points are diagnostic at this phase: a local
reparse target on fixed NTFS is still eligible.

Only stable reason codes are returned.  Win32 error text and numeric error
values are intentionally kept out of the report.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import ntpath
import os
import sys
from typing import Any


_IS_WINDOWS = sys.platform == "win32"

_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_DRIVE_TYPES = {
    0: "unknown",
    1: "no_root",
    2: "removable",
    3: "fixed",
    4: "remote",
    5: "cdrom",
    6: "ramdisk",
}
_DRIVE_FIXED = 3

# Win32 values that mean the current lexical component does not exist.  They
# are used only for control flow and are never emitted in the public result.
_PATH_NOT_FOUND_ERRORS = {2, 3, 15, 53, 67, 123}


class _Win32CallError(Exception):
    """Internal, sanitized Win32 failure used for fail-closed decisions."""

    def __init__(self, operation: str, error_code: int | None = None) -> None:
        super().__init__(operation)
        self.operation = operation
        self.error_code = error_code


class _Win32Api:
    """Small ctypes boundary kept replaceable for deterministic unit tests."""

    def __init__(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._create_file = kernel32.CreateFileW
        self._create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        self._create_file.restype = wintypes.HANDLE

        self._close_handle = kernel32.CloseHandle
        self._close_handle.argtypes = (wintypes.HANDLE,)
        self._close_handle.restype = wintypes.BOOL

        self._get_final_path = kernel32.GetFinalPathNameByHandleW
        self._get_final_path.argtypes = (
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._get_final_path.restype = wintypes.DWORD

        self._get_volume_path = kernel32.GetVolumePathNameW
        self._get_volume_path.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
        )
        self._get_volume_path.restype = wintypes.BOOL

        self._get_drive_type = kernel32.GetDriveTypeW
        self._get_drive_type.argtypes = (wintypes.LPCWSTR,)
        self._get_drive_type.restype = wintypes.UINT

        self._get_volume_information = kernel32.GetVolumeInformationW
        self._get_volume_information.argtypes = (
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            wintypes.DWORD,
        )
        self._get_volume_information.restype = wintypes.BOOL

        self._get_file_attributes = kernel32.GetFileAttributesW
        self._get_file_attributes.argtypes = (wintypes.LPCWSTR,)
        self._get_file_attributes.restype = wintypes.DWORD

    def file_attributes(self, path: str) -> int:
        attributes = int(self._get_file_attributes(_win32_api_path(path)))
        if attributes == _INVALID_FILE_ATTRIBUTES:
            raise _Win32CallError(
                "file_attributes", int(ctypes.get_last_error())
            )
        return attributes

    def final_path(self, path: str) -> str:
        handle = self._create_file(
            _win32_api_path(path),
            0,
            _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle in (None, _INVALID_HANDLE_VALUE):
            raise _Win32CallError("open_path", int(ctypes.get_last_error()))

        try:
            size = 512
            while size <= 32768:
                buffer = ctypes.create_unicode_buffer(size)
                length = int(self._get_final_path(handle, buffer, size, 0))
                if length == 0:
                    raise _Win32CallError(
                        "final_path", int(ctypes.get_last_error())
                    )
                if length < size:
                    return _strip_extended_prefix(buffer.value)
                size = length + 1
            raise _Win32CallError("final_path")
        finally:
            self._close_handle(handle)

    def volume_root(self, path: str) -> str:
        buffer = ctypes.create_unicode_buffer(32768)
        succeeded = self._get_volume_path(
            _win32_api_path(path), buffer, len(buffer)
        )
        if not succeeded:
            raise _Win32CallError("volume_root", int(ctypes.get_last_error()))
        return _strip_extended_prefix(buffer.value)

    def drive_type(self, volume_root: str) -> int:
        return int(self._get_drive_type(_win32_api_path(volume_root)))

    def filesystem(self, volume_root: str) -> str:
        volume_name = ctypes.create_unicode_buffer(261)
        filesystem_name = ctypes.create_unicode_buffer(261)
        serial_number = wintypes.DWORD()
        maximum_component_length = wintypes.DWORD()
        filesystem_flags = wintypes.DWORD()
        succeeded = self._get_volume_information(
            _win32_api_path(volume_root),
            volume_name,
            len(volume_name),
            ctypes.byref(serial_number),
            ctypes.byref(maximum_component_length),
            ctypes.byref(filesystem_flags),
            filesystem_name,
            len(filesystem_name),
        )
        if not succeeded:
            raise _Win32CallError("filesystem", int(ctypes.get_last_error()))
        return filesystem_name.value.strip()


def _make_win32_api() -> _Win32Api:
    return _Win32Api()


@dataclass(frozen=True)
class _PathInspection:
    target_exists: bool
    complete: bool
    deepest_existing: str | None
    reparse_components: tuple[str, ...]


def _strip_extended_prefix(path: str) -> str:
    normalized = path.replace("/", "\\")
    folded = normalized.casefold()
    for prefix in ("\\\\?\\unc\\", "\\\\.\\unc\\", "\\??\\unc\\"):
        if folded.startswith(prefix.casefold()):
            return "\\\\" + normalized[len(prefix) :]
    for prefix in ("\\\\?\\", "\\??\\"):
        if folded.startswith(prefix.casefold()):
            return normalized[len(prefix) :]
    return normalized


def _win32_api_path(path: str) -> str:
    """Add a long-path prefix only when the ordinary path needs one."""
    normalized = path.replace("/", "\\")
    folded = normalized.casefold()
    if folded.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return normalized
    if len(normalized) < 248:
        return normalized
    if normalized.startswith("\\\\"):
        return "\\\\?\\UNC\\" + normalized[2:]
    if ntpath.isabs(normalized):
        return "\\\\?\\" + normalized
    return normalized


def _is_syntactic_unc(path: str) -> bool:
    normalized = path.replace("/", "\\")
    folded = normalized.casefold()
    if folded.startswith(("\\\\?\\unc\\", "\\\\.\\unc\\", "\\??\\unc\\")):
        return True
    if folded.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return False
    return normalized.startswith("\\\\")


def is_syntactic_unc_path(path: str | os.PathLike[str]) -> bool:
    """Return whether *path* names a UNC location without touching it.

    This lexical check is public so callers can reject an explicitly remote
    path before ``Path.resolve()``, ``exists()``, or another operation that may
    contact an SMB server.  Invalid path-like values are not classified as UNC;
    the caller's ordinary invalid-input handling remains authoritative.
    """
    try:
        requested_path = os.fsdecode(os.fspath(path))
    except (TypeError, ValueError, OSError):
        return False
    return _is_syntactic_unc(requested_path)


def _absolute_windows_path(path: str) -> str:
    if ntpath.isabs(path):
        return ntpath.normpath(path)
    return ntpath.normpath(ntpath.abspath(path))


def _windows_components(path: str) -> tuple[str, ...]:
    """Return lexical components from volume root to the requested target."""
    normalized = ntpath.normpath(path)
    drive, tail = ntpath.splitdrive(normalized)
    separators_removed = tail.lstrip("\\/")
    parts = tuple(part for part in separators_removed.replace("/", "\\").split("\\") if part)

    components: list[str] = []
    if drive:
        current = drive + "\\"
        components.append(current)
    elif tail.startswith(("\\", "/")):
        current = "\\"
        components.append(current)
    else:
        current = ""

    for part in parts:
        current = ntpath.join(current, part) if current else part
        components.append(ntpath.normpath(current))

    if not components:
        components.append(normalized)

    deduplicated: list[str] = []
    seen: set[str] = set()
    for component in components:
        key = ntpath.normcase(component)
        if key not in seen:
            seen.add(key)
            deduplicated.append(component)
    return tuple(deduplicated)


def _inspect_path(path: str, api: Any) -> _PathInspection:
    components = _windows_components(path)
    deepest_existing: str | None = None
    reparse_components: list[str] = []
    complete = True
    target_exists = False

    for index, component in enumerate(components):
        try:
            attributes = int(api.file_attributes(component))
        except _Win32CallError as exc:
            if exc.error_code in _PATH_NOT_FOUND_ERRORS:
                break
            complete = False
            break
        except Exception:
            complete = False
            break

        deepest_existing = component
        if attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
            reparse_components.append(component)
        if index == len(components) - 1:
            target_exists = True

    ordered_reparse = tuple(
        sorted(set(reparse_components), key=lambda value: (value.casefold(), value))
    )
    return _PathInspection(
        target_exists=target_exists,
        complete=complete,
        deepest_existing=deepest_existing,
        reparse_components=ordered_reparse,
    )


def _resolved_missing_path(
    absolute_path: str, deepest_existing: str, resolved_ancestor: str
) -> str:
    try:
        suffix = ntpath.relpath(absolute_path, deepest_existing)
    except (OSError, ValueError):
        return resolved_ancestor
    if suffix == "." or suffix.startswith(".."):
        return resolved_ancestor
    return ntpath.normpath(ntpath.join(resolved_ancestor, suffix))


def _sorted_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sorted_json(value[key]) for key in sorted(value)}
    if isinstance(value, tuple):
        return [_sorted_json(item) for item in value]
    if isinstance(value, list):
        return [_sorted_json(item) for item in value]
    return value


def _base_report(requested_path: str | None, syntactic_unc: bool) -> dict[str, Any]:
    return {
        "eligible": False,
        "exists": False,
        "reason_codes": [],
        "reparse": {"components": [], "present": False},
        "requested_path": requested_path,
        "resolved_path": None,
        "syntactic_unc": syntactic_unc,
        "volume": {
            "drive_type": "unavailable",
            "drive_type_code": None,
            "filesystem": None,
            "root": None,
        },
    }


def _probe_windows(requested_path: str, absolute_path: str, api: Any) -> dict[str, Any]:
    syntactic_unc = _is_syntactic_unc(requested_path)
    report = _base_report(requested_path, syntactic_unc)
    report["resolved_path"] = absolute_path
    reasons: set[str] = set()

    if syntactic_unc:
        reasons.add("syntactic_unc")

    inspection = _inspect_path(absolute_path, api)
    report["exists"] = inspection.target_exists
    report["reparse"] = {
        "components": list(inspection.reparse_components),
        "present": bool(inspection.reparse_components),
    }
    if not inspection.complete:
        reasons.add("reparse_inspection_unavailable")
    if not inspection.target_exists:
        reasons.add("path_not_found")

    final_path: str | None = None
    volume_probe_path: str | None = None
    if inspection.deepest_existing is not None:
        try:
            resolved_existing = _strip_extended_prefix(
                str(api.final_path(inspection.deepest_existing))
            )
        except Exception:
            reasons.add("final_path_unavailable")
        else:
            volume_probe_path = resolved_existing
            if inspection.target_exists:
                final_path = resolved_existing
            else:
                final_path = _resolved_missing_path(
                    absolute_path,
                    inspection.deepest_existing,
                    resolved_existing,
                )
            report["resolved_path"] = final_path
    else:
        reasons.add("final_path_unavailable")

    if final_path is not None and _is_syntactic_unc(final_path):
        reasons.add("final_target_unc")

    volume_root: str | None = None
    if volume_probe_path is not None:
        try:
            volume_root = _strip_extended_prefix(
                str(api.volume_root(volume_probe_path))
            )
        except Exception:
            reasons.add("volume_root_unavailable")
    else:
        reasons.add("volume_root_unavailable")
    report["volume"]["root"] = volume_root

    drive_type_code: int | None = None
    if volume_root is not None:
        try:
            drive_type_code = int(api.drive_type(volume_root))
        except Exception:
            reasons.add("drive_type_unavailable")
    else:
        reasons.add("drive_type_unavailable")

    drive_type = _DRIVE_TYPES.get(drive_type_code, "other") if drive_type_code is not None else "unavailable"
    report["volume"]["drive_type_code"] = drive_type_code
    report["volume"]["drive_type"] = drive_type
    if drive_type_code is not None and drive_type_code != _DRIVE_FIXED:
        reasons.add(f"drive_type_{drive_type}")

    filesystem: str | None = None
    if volume_root is not None:
        try:
            candidate = str(api.filesystem(volume_root)).strip()
            filesystem = candidate or None
        except Exception:
            reasons.add("filesystem_unavailable")
    else:
        reasons.add("filesystem_unavailable")
    report["volume"]["filesystem"] = filesystem
    if filesystem is None:
        reasons.add("filesystem_unavailable")
    elif filesystem.casefold() != "ntfs":
        reasons.add("filesystem_not_ntfs")

    report["eligible"] = bool(
        inspection.target_exists
        and inspection.complete
        and final_path is not None
        and not syntactic_unc
        and not _is_syntactic_unc(final_path)
        and volume_root is not None
        and drive_type_code == _DRIVE_FIXED
        and filesystem is not None
        and filesystem.casefold() == "ntfs"
    )
    report["reason_codes"] = sorted(reasons)
    return _sorted_json(report)


def _probe_non_windows(requested_path: str) -> dict[str, Any]:
    report = _base_report(requested_path, _is_syntactic_unc(requested_path))
    try:
        absolute_path = os.path.abspath(requested_path)
        report["resolved_path"] = os.path.realpath(absolute_path)
        report["exists"] = os.path.exists(absolute_path)
    except (OSError, ValueError):
        report["resolved_path"] = None
    report["volume"]["drive_type"] = "unsupported"
    report["reason_codes"] = ["unsupported_platform"]
    return _sorted_json(report)


def probe_path(path: str | os.PathLike[str]) -> dict[str, Any]:
    """Return a deterministic, JSON-safe local-NTFS eligibility report.

    The function never creates a directory or file.  Invalid paths and Win32
    failures are represented by stable reason codes rather than exception text.
    """
    try:
        raw_path = os.fspath(path)
        requested_path = os.fsdecode(raw_path)
    except (TypeError, ValueError, OSError):
        report = _base_report(None, False)
        report["reason_codes"] = ["invalid_path"]
        return _sorted_json(report)

    if not requested_path or "\x00" in requested_path:
        report = _base_report(requested_path, _is_syntactic_unc(requested_path))
        report["reason_codes"] = ["invalid_path"]
        return _sorted_json(report)

    if not _IS_WINDOWS:
        return _probe_non_windows(requested_path)

    try:
        absolute_path = _absolute_windows_path(requested_path)
    except Exception:
        report = _base_report(requested_path, _is_syntactic_unc(requested_path))
        report["reason_codes"] = ["invalid_path"]
        return _sorted_json(report)

    # An explicit UNC path is already ineligible.  Stop before constructing the
    # Win32 boundary or querying attributes so preflight never initiates an SMB
    # access merely to explain the rejection.
    if _is_syntactic_unc(requested_path):
        report = _base_report(requested_path, True)
        report["resolved_path"] = absolute_path
        report["reason_codes"] = ["syntactic_unc"]
        return _sorted_json(report)

    try:
        api = _make_win32_api()
    except Exception:
        report = _base_report(requested_path, _is_syntactic_unc(requested_path))
        report["reason_codes"] = ["win32_api_unavailable"]
        return _sorted_json(report)
    return _probe_windows(requested_path, absolute_path, api)


__all__ = ["is_syntactic_unc_path", "probe_path"]
