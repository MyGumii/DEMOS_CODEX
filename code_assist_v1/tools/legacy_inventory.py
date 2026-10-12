"""P0.5 deterministic, read-only inventory of legacy stores.

The scanner never imports ``config``, an application module, or a legacy
store.  Those modules have write/startup side effects.  All source inspection
is performed with the Python standard library against caller-supplied roots.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
import ntpath
import os
import platform
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import stat as stat_module
import sys
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
from typing import Any

try:
    from .legacy_inventory_contract import (
        CATEGORY_IDS,
        REPORT_SCHEMA,
        SCHEMA_VERSION,
        TOOL_NAME,
        TOOL_VERSION,
        canonical_json_bytes,
        category_definition,
        classification_authority,
        empty_category_rows,
        has_hard_errors,
        inventory_contract_errors,
        json_safe,
        manifest_sha256,
        proposed_terminal_classification,
        sha256_canonical,
        TERMINAL_CLASSIFICATIONS,
    )
except ImportError:  # pragma: no cover - direct script fallback
    from legacy_inventory_contract import (  # type: ignore[no-redef]
        CATEGORY_IDS,
        REPORT_SCHEMA,
        SCHEMA_VERSION,
        TOOL_NAME,
        TOOL_VERSION,
        canonical_json_bytes,
        category_definition,
        classification_authority,
        empty_category_rows,
        has_hard_errors,
        inventory_contract_errors,
        json_safe,
        manifest_sha256,
        proposed_terminal_classification,
        sha256_canonical,
        TERMINAL_CLASSIFICATIONS,
    )


Clock = Callable[[], datetime]
CodeCoverageProvider = Callable[[Path], Mapping[str, Any]]
RootProbe = Callable[[str], Mapping[str, Any]]

_BUFFER_SIZE = 1024 * 1024
_MARKDOWN_LINK_RE = re.compile(r"\]\(([^)]+)\)")
_NOTION_BACKUP_RE = re.compile(
    r"^notion\.db(?:(?:[-.]?(?:bak|backup|old|copy|tmp|temp))(?:[-_.].*)?|"
    r"(?:[-_.].+)\.(?:bak|backup|old|copy|tmp|temp))$",
    re.IGNORECASE,
)
_TEMP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".tmp", ".temp", "~")
_NOTION_TABLES = frozenset(
    {
    "blocks",
    "comments",
    "files",
    "notion_schema_migrations",
    "page_save_operations",
    "page_shares",
    "page_versions",
    "pages",
    "public_links",
    "session_pages",
    "teamspace_memberships",
    "teamspaces",
    "workspace_audit_events",
    "workspace_file_sources",
    "workspace_group_members",
    "workspace_groups",
    "workspace_guests",
    "workspace_memberships",
    "workspace_operations",
    "workspace_outbox",
    "workspace_outbox_deliveries",
    "workspace_page_source_projections",
    "workspace_page_sources",
    "workspace_public_sources",
    "workspace_schema_migrations",
    "workspaces",
    }
)
_CODE_SUMMARY_KEYS = (
    "discovered",
    "mapped",
    "retained_excluded",
    "unclassified",
    "submitter_count",
    "unmapped_submitters",
    "pass",
)
_SAFE_ENUM_VALUES = frozenset(
    {
        "active", "admin", "administrator", "applied", "archived",
        "cancelled", "candidate", "child", "contradicts", "conversation",
        "decision", "deleted", "depends_on", "derived_from", "done",
        "duplicate", "example_of", "extends", "failed", "graph", "import", "lesson", "member",
        "memory", "merge", "note", "parent", "part_of", "pending",
        "personal", "preference", "project", "queued", "reference",
        "refine", "rejected", "related", "replace", "replaces", "restore",
        "references", "requires", "running", "similar", "skipped", "source", "source-of", "supports", "supersedes", "system",
        "thought", "unknown", "user",
    }
)
_USER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_GRAPH_EDGE_TYPES = frozenset(
    {"contradicts", "extends", "references", "related", "requires", "source-of", "supersedes", "supports"}
)
_JOB_KINDS = frozenset(
    {
        "outlook_daily_digest",
        "outlook_initial_sync",
        "outlook_sync",
        "project_context.import_source",
        "project_context.import_text",
        "second_brain.import_source",
        "second_brain.import_source_batch",
    }
)
_SNAPSHOT_KINDS = frozenset({"frozen_copy", "vss"})
_LOWER_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _Root:
    root_id: str
    role: str
    configured: Path
    resolved: Path
    path_source: str


@dataclass(frozen=True)
class _Entry:
    root: _Root
    path: Path
    relative_path: str
    kind: str
    stat: os.stat_result | None


@dataclass
class _State:
    records: list[dict[str, Any]] = field(default_factory=list)
    relationships: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    exclusions: list[dict[str, Any]] = field(default_factory=list)
    declared_assets: dict[str, tuple[str, str, list[str]]] = field(default_factory=dict)
    assets: dict[tuple[str, str, str], str] = field(default_factory=dict)
    path_keys: dict[tuple[str, str], str] = field(default_factory=dict)
    # A normalized key maps to the exact canonical registry spelling.  ``None``
    # means two registry IDs collide under the Windows/NFKC identity contract.
    registry_user_ids: dict[str, str | None] = field(default_factory=dict)
    registry_accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    scratch_root: Path | None = None
    scratch_descriptor: _Root | None = None
    scratch_fact: dict[str, Any] | None = None
    root_probe: RootProbe | None = None
    graph_nodes: list[dict[str, Any]] = field(default_factory=list)
    graph_edges: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    sessions: dict[tuple[str, str], str] = field(default_factory=dict)
    notion_links: list[dict[str, Any]] = field(default_factory=list)
    source_roots: list[_Root] = field(default_factory=list)
    scan_hashes: dict[tuple[str, str], str] = field(default_factory=dict)
    sqlite_snapshot_hashes: dict[tuple[str, str], str] = field(default_factory=dict)
    scratch_anchor: "_WindowsDirectoryAnchor | None" = None

    def error(
        self,
        code: str,
        *,
        root_id: str = "",
        relative_path: str = "",
        operation: str,
        severity: str = "error",
        reason_codes: Iterable[str] = (),
    ) -> None:
        self.errors.append(
            {
                "code": code,
                "operation": operation,
                "reason_codes": sorted(set(reason_codes)),
                "relative_path": relative_path,
                "root_id": root_id,
                "severity": severity,
            }
        )


class _InventoryArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover - exercised via main
        raise ValueError("invalid_cli_arguments")


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(clock: Clock) -> str:
    value = clock()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _validated_utc_timestamp(value: str | None) -> str | None:
    """Return one canonical UTC timestamp or ``None`` for invalid input."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _deduplicate_errors(errors: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Canonicalize repeated before/scan/after observations of one failure."""
    unique: dict[str, dict[str, Any]] = {}
    for error in errors:
        safe = json_safe(error)
        unique.setdefault(sha256_canonical(safe), safe)
    return sorted(
        unique.values(),
        key=lambda row: (
            row["code"],
            row["root_id"],
            row["relative_path"],
            row["operation"],
            tuple(row["reason_codes"]),
            row["severity"],
        ),
    )


def _sqlite_source_id() -> str | None:
    try:
        connection = sqlite3.connect(":memory:")
        try:
            row = connection.execute("SELECT sqlite_source_id()").fetchone()
        finally:
            connection.close()
        return str(row[0]) if row and isinstance(row[0], str) else None
    except sqlite3.Error:
        return None


def _tool_code_revision() -> str | None:
    try:
        rows = []
        for path in (Path(__file__), Path(__file__).with_name("legacy_inventory_contract.py")):
            rows.append((path.name, hashlib.sha256(path.read_bytes()).hexdigest()))
        return sha256_canonical(rows)
    except OSError:
        return None


def _is_unc(path: str) -> bool:
    normalized = path.replace("/", "\\")
    folded = normalized.casefold()
    if folded.startswith(("\\\\?\\unc\\", "\\\\.\\unc\\", "\\??\\unc\\")):
        return True
    if folded.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        return False
    return normalized.startswith("\\\\")


def _path_key(relative_path: str) -> str:
    return unicodedata.normalize("NFKC", relative_path).casefold()


def _display_windows_path(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    folded = value.casefold()
    if folded.startswith("\\\\?\\unc\\"):
        return "\\\\" + value[8:]
    if folded.startswith("\\\\?\\"):
        return value[4:]
    return value


def _extended_windows_path(path: Path) -> Path:
    if os.name != "nt":
        return path
    value = os.fspath(path)
    folded = value.casefold()
    if folded.startswith(("\\\\?\\", "\\??\\")):
        return Path(value)
    value = os.path.abspath(value)
    # Preserve ordinary path spelling and opt into the extended namespace
    # only when a directory operation is near the traditional Windows limit.
    if len(value) < 248:
        return Path(value)
    if value.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + value[2:])
    return Path("\\\\?\\" + value)


def _relative(path: Path, root: Path) -> str:
    relative = os.path.relpath(
        _display_windows_path(path), _display_windows_path(root)
    )
    if relative == ".":
        return ""
    return Path(relative).as_posix()


def _resolved_windows_path(path: Path) -> Path:
    """Resolve filesystem spelling while retaining the logical path identity."""
    return Path(_display_windows_path(_extended_windows_path(path).resolve(strict=True)))


def _contains(parent: Path, child: Path) -> bool:
    parent_value = _display_windows_path(parent)
    child_value = _display_windows_path(child)
    try:
        os.path.commonpath((parent_value, child_value)) == parent_value
    except ValueError:
        return False
    return os.path.normcase(os.path.commonpath((parent_value, child_value))) == os.path.normcase(
        parent_value
    )


def _default_root_probe(path: str) -> Mapping[str, Any]:
    if os.name != "nt":
        return {
            "eligible": True,
            "exists": os.path.isdir(path),
            "reason_codes": [],
            "resolved_path": str(Path(path).resolve(strict=True)),
            "syntactic_unc": False,
            "volume": {"drive_type": "fixed", "filesystem": None, "root": "/"},
        }
    module = importlib.import_module("code_assist_v1.tools.windows_volume")
    return module.probe_path(path)


def _root_fact(root: _Root, probe: Mapping[str, Any]) -> dict[str, Any]:
    volume = probe.get("volume") if isinstance(probe.get("volume"), Mapping) else {}
    reparse = probe.get("reparse") if isinstance(probe.get("reparse"), Mapping) else {}
    root_stat = root.configured.stat(follow_symlinks=False)
    return {
        "configured_path": str(root.configured),
        "filesystem_identity": {
            "device": int(root_stat.st_dev),
            "inode": int(root_stat.st_ino),
        },
        "path_source": root.path_source,
        "resolved_path": str(root.resolved),
        "role": root.role,
        "root_id": root.root_id,
        "reparse": {
            "components": sorted(
                str(item) for item in reparse.get("components", ()) if isinstance(item, str)
            ),
            "present": bool(reparse.get("present", False)),
        },
        "volume": {
            "drive_type": volume.get("drive_type"),
            "filesystem": volume.get("filesystem"),
            "root": volume.get("root"),
        },
    }


def _observe_root_fact(root: _Root, probe: RootProbe) -> dict[str, Any]:
    """Re-observe an approved root without writing scanner error state."""
    observation = probe(str(root.configured))
    reparse = (
        observation.get("reparse")
        if isinstance(observation.get("reparse"), Mapping)
        else {}
    )
    if (
        observation.get("eligible") is not True
        or observation.get("exists") is not True
        or reparse.get("present") is True
    ):
        raise OSError("root_no_longer_eligible")
    resolved_value = observation.get("resolved_path")
    resolved = Path(
        str(resolved_value) if resolved_value else root.configured
    ).resolve(strict=True)
    if resolved != root.resolved:
        raise OSError("root_final_path_changed")
    return _root_fact(root, observation)


def _assert_scratch_identity(state: _State) -> None:
    root = state.scratch_descriptor
    expected = state.scratch_fact
    probe = state.root_probe
    if root is None or expected is None or probe is None:
        raise OSError("scratch_not_attested")
    if _observe_root_fact(root, probe) != expected:
        raise OSError("scratch_root_identity_changed")


def _directory_identity(path: Path, *, parent: Path | None = None) -> tuple[str, int, int]:
    before = path.stat(follow_symlinks=False)
    if _is_reparse(path, before) or not path.is_dir():
        raise OSError("unsafe_directory")
    resolved = path.resolve(strict=True)
    if parent is not None and not _contains(parent, resolved):
        raise OSError("directory_escaped_parent")
    after = path.stat(follow_symlinks=False)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise OSError("directory_identity_changed")
    return str(resolved), int(after.st_dev), int(after.st_ino)


_WINDOWS_RESERVED_COMPONENTS = frozenset(
    {"aux", "con", "nul", "prn"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def _validate_simple_windows_component(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or value.endswith((" ", "."))
        or any(character in value for character in "\\/:\0")
        or any(ord(character) < 32 for character in value)
        or len(value.encode("utf-16-le")) > 510
        or value.split(".", 1)[0].rstrip(" .").casefold()
        in _WINDOWS_RESERVED_COMPONENTS
    ):
        raise OSError("unsafe_windows_path_component")
    return value


def _windows_final_path(handle: int) -> Path:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final = kernel32.GetFinalPathNameByHandleW
    get_final.argtypes = (
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    )
    get_final.restype = wintypes.DWORD
    size = get_final(wintypes.HANDLE(handle), None, 0, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size + 1)
    result = get_final(wintypes.HANDLE(handle), buffer, len(buffer), 0)
    if not result or result >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(os.path.normpath(value))


def _raise_ntstatus(status: int, operation: str) -> None:
    if status >= 0:
        return
    import ctypes
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    translate = ntdll.RtlNtStatusToDosError
    translate.argtypes = (wintypes.ULONG,)
    translate.restype = wintypes.ULONG
    error = int(translate(status & 0xFFFFFFFF))
    raise OSError(error, operation)


def _directory_anchor_access_mask(
    *, writable: bool, delete_access: bool
) -> int:
    """Return the least-privilege access mask for a pinned directory.

    Creating children requires ``FILE_ADD_FILE``/``FILE_ADD_SUBDIRECTORY`` on
    the parent.  It does not require ``FILE_DELETE_CHILD`` (0x0040); requesting
    that right made an otherwise ordinary NTFS ``Modify`` service account fail
    while opening a CAS root or shard.
    """

    desired = 0x0001 | 0x0020 | 0x0080 | 0x00100000
    if writable:
        desired |= 0x0002 | 0x0004
    if delete_access:
        desired |= 0x00010000
    return desired


def _nt_create_relative(
    root_handle: int,
    name: str,
    *,
    directory: bool,
    share_write: bool = False,
    delete_access: bool = True,
) -> int:
    import ctypes
    from ctypes import wintypes

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
        0x40,  # OBJ_CASE_INSENSITIVE
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
    desired = (
        _directory_anchor_access_mask(
            writable=True,
            delete_access=delete_access,
        )
        if directory
        else 0x0002 | 0x0080 | 0x00100000 | (0x00010000 if delete_access else 0)
    )
    options = 0x00000020 | 0x00200000
    options |= 0x00000001 if directory else 0x00000040
    status = int(
        create_file(
            ctypes.byref(handle),
            desired,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x10 if directory else 0x80,
            0x0001 | (0x0002 if share_write else 0),
            # The migration bootstrap may share writes on its newly created
            # database handle while still denying delete/rename. Other users
            # keep FILE_SHARE_READ only.
            2,  # FILE_CREATE
            options,
            None,
            0,
        )
    )
    _raise_ntstatus(status, "NtCreateFile")
    if handle.value is None:
        raise OSError("relative_create_missing_handle")
    return int(handle.value)


def _nt_set_disposition_value(handle: int, delete: bool) -> None:
    import ctypes
    from ctypes import wintypes

    class _IO_STATUS_UNION(ctypes.Union):
        _fields_ = (("Status", ctypes.c_long), ("Pointer", wintypes.LPVOID))

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = (("Value", _IO_STATUS_UNION), ("Information", ctypes.c_size_t))

    class _FILE_DISPOSITION_INFORMATION(ctypes.Structure):
        _fields_ = (("DeleteFile", ctypes.c_ubyte),)

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_IO_STATUS_BLOCK),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
    )
    set_information.restype = ctypes.c_long
    io_status = _IO_STATUS_BLOCK()
    information = _FILE_DISPOSITION_INFORMATION(int(delete))
    status = int(
        set_information(
            wintypes.HANDLE(handle),
            ctypes.byref(io_status),
            ctypes.byref(information),
            ctypes.sizeof(information),
            13,  # FileDispositionInformation
        )
    )
    _raise_ntstatus(status, "NtSetInformationFile(disposition)")


def _nt_set_disposition(handle: int) -> None:
    _nt_set_disposition_value(handle, True)


def _nt_clear_disposition(handle: int) -> None:
    _nt_set_disposition_value(handle, False)


def _nt_rename_relative(
    handle: int,
    root_handle: int,
    name: str,
    *,
    replace: bool,
) -> None:
    import ctypes
    from ctypes import wintypes

    name = _validate_simple_windows_component(name)

    class _IO_STATUS_UNION(ctypes.Union):
        _fields_ = (("Status", ctypes.c_long), ("Pointer", wintypes.LPVOID))

    class _IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = (("Value", _IO_STATUS_UNION), ("Information", ctypes.c_size_t))

    class _FILE_RENAME_INFORMATION(ctypes.Structure):
        _fields_ = (
            ("ReplaceIfExists", ctypes.c_ubyte),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.ULONG),
            ("FileName", ctypes.c_wchar * (len(name) + 1)),
        )

    information = _FILE_RENAME_INFORMATION()
    information.ReplaceIfExists = int(replace)
    # A simple name with a NULL RootDirectory renames within the source
    # file's already pinned parent.  Passing the parent handle here makes
    # NTFS reopen the directory for write/delete and conflicts with the
    # anchor's deliberately restrictive share mode.
    information.RootDirectory = None
    information.FileNameLength = len(name.encode("utf-16-le"))
    information.FileName = name
    io_status = _IO_STATUS_BLOCK()
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    set_information = ntdll.NtSetInformationFile
    set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_IO_STATUS_BLOCK),
        wintypes.LPVOID,
        wintypes.ULONG,
        wintypes.ULONG,
    )
    set_information.restype = ctypes.c_long
    status = int(
        set_information(
            wintypes.HANDLE(handle),
            ctypes.byref(io_status),
            ctypes.byref(information),
            ctypes.sizeof(information),
            10,  # FileRenameInformation
        )
    )
    _raise_ntstatus(status, "NtSetInformationFile(rename)")


def _close_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    if not close_handle(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


@dataclass
class _WindowsDirectoryAnchor:
    path: Path
    handle: int
    closed: bool = False

    @classmethod
    def open(
        cls,
        path: Path,
        *,
        writable: bool = True,
        share_write: bool = False,
        delete_access: bool = False,
    ) -> "_WindowsDirectoryAnchor":
        if os.name != "nt":
            raise OSError("directory_anchor_unsupported")
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_file = kernel32.CreateFileW
        create_file.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        )
        create_file.restype = wintypes.HANDLE
        desired = _directory_anchor_access_mask(
            writable=writable,
            delete_access=delete_access,
        )
        raw_handle = create_file(
            str(_extended_windows_path(path)),
            desired,
            0x0001 | (0x0002 if share_write else 0),
            # Bootstrap allows child-file writes while still denying delete/
            # rename of the pinned parent. Other paths remain read-share only.
            None,
            3,
            0x02000000 | 0x00200000,
            None,
        )
        if raw_handle in (None, ctypes.c_void_p(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        anchor = cls(path=path, handle=int(raw_handle))
        try:
            if os.path.normcase(str(_windows_final_path(anchor.handle))) != os.path.normcase(
                str(_resolved_windows_path(path))
            ):
                raise OSError("directory_anchor_final_path_changed")
            return anchor
        except Exception:
            anchor.close()
            raise

    @classmethod
    def create_relative_directory(
        cls,
        parent: "_WindowsDirectoryAnchor",
        name: str,
        *,
        share_write: bool = False,
    ) -> "_WindowsDirectoryAnchor":
        handle = _nt_create_relative(
            parent.handle,
            name,
            directory=True,
            share_write=share_write,
        )
        anchor = cls(path=parent.path / name, handle=handle)
        try:
            if os.path.normcase(str(_windows_final_path(handle))) != os.path.normcase(
                str(_resolved_windows_path(anchor.path))
            ):
                raise OSError("relative_directory_final_path_changed")
            return anchor
        except Exception:
            try:
                _nt_set_disposition(handle)
            finally:
                anchor.close()
            raise

    def create_relative_file(
        self,
        name: str,
        *,
        share_write: bool = False,
        delete_access: bool = True,
    ) -> int:
        return _nt_create_relative(
            self.handle,
            name,
            directory=False,
            share_write=share_write,
            delete_access=delete_access,
        )

    def dispose(self) -> None:
        if self.closed:
            raise OSError("directory_anchor_closed")
        _nt_set_disposition(self.handle)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        _close_windows_handle(self.handle)


@contextmanager
def _pinned_directory(
    path: Path,
    *,
    writable: bool = True,
    share_write: bool = False,
) -> Iterable[_WindowsDirectoryAnchor]:
    anchor = _WindowsDirectoryAnchor.open(
        path,
        writable=writable,
        share_write=share_write,
    )
    try:
        yield anchor
    finally:
        anchor.close()


@contextmanager
def _validated_scratch_directory(state: _State) -> Iterable[Path]:
    """Create and remove one scratch child only while its parent is unchanged."""
    _assert_scratch_identity(state)
    if state.scratch_root is None:
        raise OSError("scratch_not_configured")
    with _pinned_directory(state.scratch_root) as root_anchor:
        _assert_scratch_identity(state)
        temporary_name = (
            "legacy-inventory-sqlite-" + secrets.token_hex(16)
        )
        temporary_anchor = _WindowsDirectoryAnchor.create_relative_directory(
            root_anchor, temporary_name
        )
        temporary = temporary_anchor.path
        _assert_scratch_identity(state)
        identity = _directory_identity(temporary, parent=state.scratch_root)
        state.scratch_anchor = temporary_anchor
        try:
            yield temporary
        finally:
            state.scratch_anchor = None
            # Never recursively clean a path after either the approved parent
            # or the temporary directory identity has changed.
            _assert_scratch_identity(state)
            if _directory_identity(temporary, parent=state.scratch_root) != identity:
                raise OSError("scratch_directory_identity_changed")
            cleanup_error: Exception | None = None
            try:
                for child in sorted(temporary.iterdir(), key=lambda item: item.name):
                    child_stat = child.stat(follow_symlinks=False)
                    if _is_reparse(child, child_stat) or not child.is_file():
                        raise OSError("unsafe_scratch_cleanup_entry")
                    child.unlink()
                temporary_anchor.dispose()
            except Exception as exc:
                cleanup_error = exc
            finally:
                temporary_anchor.close()
            _assert_scratch_identity(state)
            if cleanup_error is not None:
                raise cleanup_error


def _validate_one_root(
    root_id: str,
    role: str,
    value: str | os.PathLike[str],
    path_source: str,
    probe: RootProbe,
    state: _State,
) -> tuple[_Root | None, dict[str, Any] | None]:
    raw = os.fsdecode(os.fspath(value))
    if not os.path.isabs(raw):
        state.error("ROOT_NOT_ABSOLUTE", root_id=root_id, operation="validate_root")
        return None, None
    if _is_unc(raw):
        state.error("ROOT_INELIGIBLE", root_id=root_id, operation="validate_root", reason_codes=("syntactic_unc",))
        return None, None
    configured = Path(os.path.normpath(raw))
    if not configured.exists():
        state.error("ROOT_MISSING", root_id=root_id, operation="validate_root")
        return None, None
    if not configured.is_dir():
        state.error("ROOT_NOT_DIRECTORY", root_id=root_id, operation="validate_root")
        return None, None
    try:
        observation = dict(probe(str(configured)))
    except Exception:
        state.error("ROOT_INELIGIBLE", root_id=root_id, operation="probe_root", reason_codes=("probe_failed",))
        return None, None
    reasons = tuple(str(item) for item in observation.get("reason_codes", ()) if isinstance(item, str))
    reparse = observation.get("reparse") if isinstance(observation.get("reparse"), Mapping) else {}
    volume = observation.get("volume") if isinstance(observation.get("volume"), Mapping) else {}
    local = volume.get("drive_type") in (None, "fixed")
    ntfs = str(volume.get("filesystem") or "").upper() == "NTFS"
    if (
        not observation.get("exists", True)
        or observation.get("syntactic_unc") is True
        or reparse.get("present") is True
        or not local
        or not ntfs
        or observation.get("eligible") is False
    ):
        state.error(
            "ROOT_INELIGIBLE",
            root_id=root_id,
            operation="probe_root",
            reason_codes=reasons or (
                ("root_reparse_present",)
                if reparse.get("present") is True
                else (("root_not_ntfs",) if not ntfs else ("nonlocal_or_ineligible",))
            ),
        )
        return None, None
    final = observation.get("resolved_path")
    try:
        resolved = Path(str(final) if final else configured).resolve(strict=True)
    except (OSError, ValueError):
        state.error("ROOT_INELIGIBLE", root_id=root_id, operation="resolve_root", reason_codes=("final_path_unavailable",))
        return None, None
    root = _Root(root_id, role, configured, resolved, path_source)
    return root, _root_fact(root, observation)


def _is_reparse(path: Path, file_stat: os.stat_result) -> bool:
    try:
        attributes = int(getattr(file_stat, "st_file_attributes", 0))
        reparse_flag = int(getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if attributes & reparse_flag:
            return True
        if path.is_symlink():
            return True
        checker = getattr(os.path, "isjunction", None)
        return bool(checker and checker(path))
    except OSError:
        return True


def _walk(root: _Root, state: _State, operation: str) -> Iterable[_Entry]:
    pending = [root.configured]
    while pending:
        directory = pending.pop()
        access_directory = _extended_windows_path(directory)
        try:
            with os.scandir(access_directory) as iterator:
                entries = sorted(iterator, key=lambda item: (item.name.casefold(), item.name))
        except OSError:
            rel = _relative(directory, root.configured)
            state.error("ENUMERATION_DENIED", root_id=root.root_id, relative_path=rel, operation=operation)
            # Preserve the fact that an enumerated source location could not
            # be inspected.  Capture passes hash this marker and the scan pass
            # turns it into one terminal unavailable record.
            yield _Entry(
                root,
                Path(_display_windows_path(access_directory)),
                rel,
                "unavailable",
                None,
            )
            continue
        child_directories: list[Path] = []
        for item in entries:
            access_path = Path(item.path)
            path = Path(_display_windows_path(access_path))
            rel = _relative(path, root.configured)
            try:
                stat = item.stat(follow_symlinks=False)
            except OSError:
                state.error("FILE_DISAPPEARED", root_id=root.root_id, relative_path=rel, operation=operation)
                yield _Entry(root, path, rel, "missing", None)
                continue
            if _is_reparse(path, stat):
                yield _Entry(root, path, rel, "reparse", stat)
            elif item.is_dir(follow_symlinks=False):
                yield _Entry(root, path, rel, "directory", stat)
                child_directories.append(access_path)
            elif item.is_file(follow_symlinks=False):
                yield _Entry(root, path, rel, "file", stat)
            else:
                yield _Entry(root, path, rel, "special", stat)
        pending.extend(reversed(child_directories))


def _capture(root: _Root, state: _State, operation: str) -> dict[str, Any]:
    rows = []
    files = 0
    directories = 0
    for entry in _walk(root, state, operation):
        stat = entry.stat
        rows.append(
            {
                "kind": entry.kind,
                # Windows can apply a parent directory timestamp update after
                # the creating process returns.  Directory membership is the
                # stable high-water fact; file mtime/size still detect edits.
                "mtime_ns": None if stat is None or entry.kind == "directory" else stat.st_mtime_ns,
                "path": entry.relative_path,
                "sha256": _stream_sha256(entry, state) if entry.kind == "file" else None,
                "size": None if stat is None else stat.st_size,
            }
        )
        files += entry.kind == "file"
        directories += entry.kind == "directory"
    return {
        "directories": directories,
        "entries": len(rows),
        "files": files,
        "tree_sha256": sha256_canonical(rows),
    }


def _stream_sha256(entry: _Entry, state: _State) -> str | None:
    access_path = _extended_windows_path(entry.path)
    try:
        before = access_path.stat(follow_symlinks=False)
        if _is_reparse(access_path, before):
            state.error(
                "UNSAFE_REPARSE_ENTRY",
                root_id=entry.root.root_id,
                relative_path=entry.relative_path,
                operation="hash_file",
            )
            return None
        resolved_before = access_path.resolve(strict=True)
        if not _contains(entry.root.resolved, resolved_before):
            state.error(
                "PATH_ESCAPES_ROOT",
                root_id=entry.root.root_id,
                relative_path=entry.relative_path,
                operation="hash_file",
            )
            return None
        digest = hashlib.sha256()
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(access_path, flags)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("opened_file_identity_changed")
            while True:
                chunk = stream.read(_BUFFER_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
        after = access_path.stat(follow_symlinks=False)
        resolved_after = access_path.resolve(strict=True)
    except OSError:
        state.error("HASH_FAILED", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="hash_file")
        return None
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or resolved_before != resolved_after
        or not _contains(entry.root.resolved, resolved_after)
    ):
        state.error("SOURCE_CHANGED", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="hash_file")
        return None
    return digest.hexdigest()


def _read_verified_bytes(
    entry: _Entry,
    state: _State,
    expected_sha256: str | None,
    *,
    operation: str,
) -> bytes | None:
    """Read one file through a no-follow descriptor and bind it to its record hash."""
    access_path = _extended_windows_path(entry.path)
    try:
        before = access_path.stat(follow_symlinks=False)
        if _is_reparse(access_path, before):
            raise OSError("source_reparse")
        resolved_before = access_path.resolve(strict=True)
        if not _contains(entry.root.resolved, resolved_before):
            raise OSError("source_escaped_root")
        flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
        flags |= int(getattr(os, "O_NOFOLLOW", 0))
        descriptor = os.open(access_path, flags)
        with os.fdopen(descriptor, "rb", buffering=0) as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise OSError("source_identity_changed")
            raw = stream.read()
        after = access_path.stat(follow_symlinks=False)
        resolved_after = access_path.resolve(strict=True)
    except OSError:
        state.error(
            "READ_FAILED",
            root_id=entry.root.root_id,
            relative_path=entry.relative_path,
            operation=operation,
            reason_codes=("verified_read_failed",),
        )
        return None
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if (
        expected_sha256 is None
        or observed_sha256 != expected_sha256
        or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        )
        != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        )
        or resolved_before != resolved_after
        or not _contains(entry.root.resolved, resolved_after)
    ):
        state.error(
            "SOURCE_CHANGED",
            root_id=entry.root.root_id,
            relative_path=entry.relative_path,
            operation=operation,
            reason_codes=("parsed_bytes_do_not_match_record",),
        )
        return None
    return raw


def _owner(kind: str, user_id: str | None = None) -> dict[str, Any]:
    status = "confirmed" if kind == "system" else "unresolved"
    if kind == "personal":
        status = "unverified"
    return {
        "claimed_user_id": user_id,
        "kind": kind,
        "mapping_status": status,
        "user_id": user_id,
    }


def _confirmed_personal_owner(user_id: str) -> dict[str, Any]:
    return {
        "claimed_user_id": user_id,
        "kind": "personal",
        "mapping_status": "confirmed",
        "user_id": user_id,
    }


def _confirmed_claimed_owner(claimed_user_id: str, canonical_user_id: str) -> dict[str, Any]:
    return {
        "claimed_user_id": claimed_user_id,
        "kind": "personal",
        "mapping_status": "confirmed",
        "user_id": canonical_user_id,
    }


def _owner_key(user_id: str) -> str:
    return unicodedata.normalize("NFKC", user_id).casefold()


def _resolve_path_owner(owner: dict[str, Any], state: _State) -> dict[str, Any]:
    if owner.get("kind") != "personal":
        return owner
    user_id = str(owner.get("user_id") or "")
    normalized = _owner_key(user_id)
    if normalized in state.registry_user_ids:
        canonical = state.registry_user_ids[normalized]
        if canonical is not None:
            return _confirmed_claimed_owner(user_id, canonical)
        return {
            "claimed_user_id": user_id or None,
            "kind": "ambiguous_registry_owner",
            "mapping_status": "ambiguous",
            "user_id": None,
        }
    return {
        "claimed_user_id": user_id or None,
        "kind": "unresolved_path_owner",
        "mapping_status": "unresolved",
        "user_id": None,
    }


def _is_temp(name: str) -> bool:
    folded = name.casefold()
    return folded.endswith(_TEMP_SUFFIXES) or folded.startswith(".~")


def _classify(root: _Root, relative_path: str) -> tuple[str, dict[str, Any], str | None]:
    parts = relative_path.split("/")
    folded = [part.casefold() for part in parts]
    filename = folded[-1]
    if root.role == "logs":
        if filename.endswith((".log", ".out", ".err")) or ".log." in filename:
            return "operations.server_logs", _owner("system"), None
        if _is_temp(filename):
            return "operations.temp_recovery", _owner("system"), None
        return "unknown.unclassified", _owner("system"), None
    if root.role == "runtime":
        if filename == "users.json" and len(parts) == 1:
            return "identity.users_registry", _owner("system"), None
        if filename == "audit.jsonl" and len(parts) == 1:
            return "identity.auth_audit", _owner("system"), None
        if filename in {"secret.key", "token", "token.txt", "token.json"} or "credential" in filename:
            return "", _owner("system"), "sensitive_credential"
        if len(parts) == 1 and filename.endswith(".log"):
            return "operations.server_logs", _owner("system"), None
        if _is_temp(filename):
            return "operations.temp_recovery", _owner("system"), None
        return "unknown.unclassified", _owner("system"), None
    if root.role == "sessions":
        category = "conversation.global_sessions" if filename.endswith(".json") else "unknown.unclassified"
        return category, _owner("unknown"), None
    if root.role == "workspace":
        return "context.workspace_files", _owner("unknown"), None
    if root.role == "user_data":
        if len(parts) == 1 and filename.endswith(".log"):
            return "operations.server_logs", _owner("system"), None
        uid = parts[0] if len(parts) > 1 else None
        tail = folded[1:]
        owner = _owner("personal" if uid else "unknown", uid)
        if not tail:
            return "unknown.unclassified", owner, None
        if tail[0] == "sessions" and filename.endswith(".json"):
            return "conversation.user_sessions", owner, None
        if tail[0] == "workspace":
            return "context.workspace_files", owner, None
        if tail[0] == "skills":
            return "context.user_skills", owner, None
        if tail[:2] == ["memory", "memories.jsonl"]:
            return "context.memory", owner, None
        if tail[:2] == ["memory", "events.jsonl"]:
            return "context.memory_events", owner, None
        if tail[0] == "jobs" and filename.endswith(".json"):
            return "operations.jobs", owner, None
        if tail == ["outlook", "state.json"]:
            return "external.outlook_state", owner, None
        if tail[0] == "notion":
            if filename == "notion.db":
                return "external.notion_database", owner, None
            if filename in {"notion.db-journal", "notion.db-shm", "notion.db-wal"}:
                return "external.notion_database_sidecars", owner, None
            if _NOTION_BACKUP_RE.fullmatch(filename):
                return "external.notion_backup_candidates", owner, None
            if len(tail) > 1 and tail[1] == "files":
                return "external.notion_files", owner, None
        if len(tail) >= 4 and tail[0] == "projects" and tail[2] == "context":
            context_tail = tail[3:]
            if context_tail == ["sources.jsonl"] or context_tail[0] == "raw":
                return "context.project_sources", owner, None
            if context_tail == ["scores.jsonl"]:
                return "context.project_scores", owner, None
            if context_tail[0] == "index":
                return "context.project_chunks", owner, None
            if context_tail[0] == "assets":
                return "context.project_assets", owner, None
        if _is_temp(filename):
            return "operations.temp_recovery", owner, None
        return "unknown.unclassified", owner, None
    if root.role == "knowledge":
        unowned_roots = {
            ".deleted", "deleted", "edges", "inbox", "raw", "raw-chunks",
            "raw_chunks", "thoughts", "tombstones", "wiki",
        }
        if folded[0] in unowned_roots:
            uid = None
            tail = folded
        else:
            uid = parts[0] if len(parts) > 1 else None
            tail = folded[1:] if uid else folded
        owner = _owner("personal" if uid else "unknown", uid)
        if len(tail) >= 2 and tail[0] == "wiki":
            wiki = tail[1:]
            if wiki[0] == "sources" and filename.endswith(".md"):
                return "knowledge.wiki_sources", owner, None
            if wiki[0] == "sources":
                return "unknown.unclassified", owner, None
            if wiki[0] == "pages" and filename.endswith(".md"):
                return "knowledge.wiki_hidden_pages", owner, None
            if wiki[0] == "pages":
                return "unknown.unclassified", owner, None
            if wiki[0] == "assets":
                return "knowledge.wiki_assets", owner, None
            if wiki[0] == "health":
                return "knowledge.wiki_health", owner, None
            if wiki == ["index.md"]:
                return "knowledge.wiki_index_markdown", owner, None
            if wiki == ["log.md"]:
                return "knowledge.wiki_log", owner, None
            if wiki == ["wiki_index.sqlite"] or filename.startswith("wiki_index.sqlite-"):
                return "knowledge.wiki_index_sqlite", owner, None
            if wiki == ["graph", "graph.json"]:
                return "knowledge.wiki_graph_edges", owner, None
            if wiki == ["graph", "backlinks.json"]:
                return "knowledge.wiki_backlinks", owner, None
            if wiki == ["graph", "pending_diffs.json"]:
                return "knowledge.wiki_candidates", owner, None
        if tail and tail[0] == "thoughts":
            return "knowledge.legacy_thoughts", owner, None
        if tail and (tail[0] == "edges" or filename in {"edges.json", "edges.jsonl"}):
            return "knowledge.legacy_edges", owner, None
        if tail and tail[0] == "inbox":
            return "knowledge.legacy_inbox", owner, None
        if tail and tail[0] in {"raw_chunks", "raw-chunks"}:
            return "knowledge.legacy_raw_chunks", owner, None
        if tail and tail[0] == "raw":
            return "knowledge.legacy_raw", owner, None
        if any(part in {"deleted", ".deleted", "tombstones"} for part in tail) or filename in {
            ".tombstones.json", ".tombstones.jsonl", "tombstones.json", "tombstones.jsonl",
        }:
            return "knowledge.tombstones", owner, None
        if len(tail) == 1 and filename.endswith(".md"):
            return "knowledge.legacy_flat_markdown", owner, None
        if _is_temp(filename):
            return "operations.temp_recovery", owner, None
        return "unknown.unclassified", owner, None
    return "unknown.unclassified", _owner("unknown"), None


def _new_record(
    entry: _Entry,
    category: str,
    owner: dict[str, Any],
    digest: str | None,
    state: _State,
    *,
    entry_type: str = "file",
    source_ordinal: int = 0,
    size_bytes: int | None = None,
) -> dict[str, Any]:
    key = _path_key(entry.relative_path)
    collision_key = (entry.root.root_id, key)
    previous = state.path_keys.get(collision_key)
    if previous is not None and previous != entry.relative_path and source_ordinal == 0:
        state.error("PATH_KEY_COLLISION", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="classify_path")
    state.path_keys.setdefault(collision_key, entry.relative_path)
    identity = {
        "category": category,
        "owner": owner,
        "path": entry.relative_path,
        "root_id": entry.root.root_id,
        "source_ordinal": source_ordinal,
    }
    definition = category_definition(category)
    stat = entry.stat
    record_kind = {
        "file": "physical_file",
        "logical_record": "logical_record",
        "unavailable": "unavailable",
    }.get(entry_type, "unavailable")
    return {
        "category": category,
        "classification_authority": None,
        "classification_reason_codes": [],
        "entry_type": entry_type,
        "owner": owner,
        "parent_record_id": None,
        "parse": {"child_count": 0, "logical_sha256": None, "record_count": 0, "schema_version": None, "status": "not_applicable"},
        "path_key": key,
        "planned_action": definition["planned_action"],
        "reason_codes": [],
        "record_id": "rec_" + sha256_canonical(identity)[:24],
        "record_kind": record_kind,
        "references": {"declared": 0, "missing": 0, "resolved": 0},
        "relative_path": entry.relative_path,
        "root_id": entry.root.root_id,
        "sha256": digest,
        "size_bytes": stat.st_size if size_bytes is None and stat is not None else size_bytes,
        "source_class": definition["source_class"],
        "source_identity": {
            "path_key": key,
            "root_id": entry.root.root_id,
            "source_ordinal": source_ordinal,
        },
        "source_ordinal": source_ordinal,
        "stat": {"mtime_ns": None if stat is None else stat.st_mtime_ns},
        "status": "active" if digest is not None else "unreadable",
        "proposed_terminal_classification": None,
    }


def _read_json(
    entry: _Entry, state: _State, expected_sha256: str | None
) -> Any | None:
    raw = _read_verified_bytes(
        entry,
        state,
        expected_sha256,
        operation="parse_json",
    )
    if raw is None:
        return None
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_members,
            parse_constant=_reject_json_constant,
        )
    except UnicodeError:
        state.error("INVALID_UTF8", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_json")
    except (json.JSONDecodeError, ValueError):
        state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_json")
    except OSError:
        state.error("READ_FAILED", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_json")
    return None


def _safe_enum(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if not text:
        return None
    return text if text in _SAFE_ENUM_VALUES else "unknown"


def _reject_json_constant(value: str) -> None:
    raise ValueError("non_finite_json_number")


def _reject_duplicate_json_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_member")
        result[key] = value
    return result


def _job_status(value: Any) -> str:
    text = str(value or "").strip().casefold()
    aliases = {
        "canceled": "cancelled",
        "done": "succeeded",
        "success": "succeeded",
    }
    text = aliases.get(text, text)
    return text if text in {"cancelled", "failed", "queued", "running", "succeeded"} else "unknown"


def _jsonl_semantic_errors(category: str, item: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the frozen authority-bearing subset of legacy JSONL rows.

    Opaque payload fields are still hashed byte-for-byte.  Only fields that
    drive migration status/type policy are interpreted here.
    """
    reasons: list[str] = []
    status = str(item.get("status") or "").strip().casefold()
    if category == "identity.auth_audit" and status and status not in {
        "denied", "error", "failed", "ok", "success"
    }:
        reasons.append("unknown_auth_audit_status")
    elif category == "context.memory" and status and status not in {
        "active", "archived", "candidate", "deleted"
    }:
        reasons.append("unknown_memory_status")
    elif category == "context.memory_events" and status and status not in {
        "active", "archived", "candidate", "complete", "deleted"
    }:
        reasons.append("unknown_memory_event_status")
    return tuple(reasons)


def _child_record(entry: _Entry, category: str, owner: dict[str, Any], source_digest: str, ordinal: int, safe_shape: Mapping[str, Any], state: _State, *, parent_record_id: str | None = None) -> dict[str, Any]:
    digest = sha256_canonical({"ordinal": ordinal, "shape": safe_shape, "source_sha256": source_digest})
    record = _new_record(entry, category, owner, digest, state, entry_type="logical_record", source_ordinal=ordinal, size_bytes=0)
    record["parent_record_id"] = parent_record_id
    record["parse"] = {"child_count": 0, "logical_sha256": digest, "record_count": 1, "schema_version": None, "status": "valid"}
    return record


def _parse_jsonl(entry: _Entry, record: dict[str, Any], state: _State) -> None:
    children = []
    invalid = False
    raw = _read_verified_bytes(
        entry,
        state,
        record.get("sha256"),
        operation="parse_jsonl",
    )
    if raw is None:
        record["parse"]["status"] = "invalid"
        return
    try:
        with io.StringIO(raw.decode("utf-8")) as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(
                        line,
                        object_pairs_hook=_reject_duplicate_json_members,
                        parse_constant=_reject_json_constant,
                    )
                except (json.JSONDecodeError, ValueError):
                    state.error("INVALID_JSONL", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_jsonl", reason_codes=(f"line_{number}",))
                    invalid = True
                    child = _child_record(
                        entry,
                        record["category"],
                        record["owner"],
                        record["sha256"],
                        number,
                        {"parse_status": "invalid", "payload_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()},
                        state,
                        parent_record_id=record["record_id"],
                    )
                    child["planned_action"] = "quarantine_candidate"
                    child["reason_codes"] = ["invalid_jsonl"]
                    child["status"] = "invalid"
                    children.append(child)
                    continue
                if not isinstance(item, dict):
                    state.error(
                        "INVALID_JSONL",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="validate_jsonl_row",
                        reason_codes=(f"line_{number}", "row_not_object"),
                    )
                    invalid = True
                    child = _child_record(
                        entry,
                        record["category"],
                        record["owner"],
                        record["sha256"],
                        number,
                        {"parse_status": "invalid", "payload_sha256": sha256_canonical(item)},
                        state,
                        parent_record_id=record["record_id"],
                    )
                    child["planned_action"] = "quarantine_candidate"
                    child["reason_codes"] = ["invalid_jsonl_row"]
                    child["status"] = "invalid"
                    children.append(child)
                    continue
                semantic_errors = _jsonl_semantic_errors(record["category"], item)
                shape = {"kind": _safe_enum(item.get("type") or item.get("kind")), "status": _safe_enum(item.get("status"))}
                shape["payload_sha256"] = sha256_canonical(item)
                child = _child_record(
                    entry,
                    record["category"],
                    record["owner"],
                    record["sha256"],
                    number,
                    shape,
                    state,
                    parent_record_id=record["record_id"],
                )
                if semantic_errors:
                    state.error(
                        "UNKNOWN_ENTRY",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="validate_jsonl_semantics",
                        reason_codes=semantic_errors + (f"line_{number}",),
                    )
                    invalid = True
                    child["planned_action"] = "quarantine_candidate"
                    child["reason_codes"] = list(semantic_errors)
                    child["status"] = "unknown"
                children.append(child)
    except UnicodeError:
        state.error("INVALID_UTF8", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_jsonl")
        invalid = True
    except OSError:
        state.error("READ_FAILED", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_jsonl")
        invalid = True
    state.records.extend(children)
    record["parse"] = {"child_count": len(children), "logical_sha256": sha256_canonical([child["sha256"] for child in children]), "record_count": len(children), "schema_version": None, "status": "invalid" if invalid else "valid"}


def _parse_legacy_edges_jsonl(entry: _Entry, record: dict[str, Any], state: _State) -> None:
    children: list[dict[str, Any]] = []
    invalid = False
    seen: set[tuple[str, str, str]] = set()
    status_counts = {"duplicate": 0, "unresolved_legacy_reference": 0}
    kind_counts: dict[str, int] = {}
    raw = _read_verified_bytes(
        entry,
        state,
        record.get("sha256"),
        operation="parse_legacy_edges",
    )
    if raw is None:
        record["parse"]["status"] = "invalid"
        return
    try:
        with io.StringIO(raw.decode("utf-8")) as stream:
            for number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    item = json.loads(
                        line,
                        object_pairs_hook=_reject_duplicate_json_members,
                        parse_constant=_reject_json_constant,
                    )
                except (json.JSONDecodeError, ValueError):
                    state.error("INVALID_JSONL", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_legacy_edges", reason_codes=(f"line_{number}",))
                    invalid = True
                    child = _child_record(
                        entry,
                        record["category"],
                        record["owner"],
                        record["sha256"],
                        number,
                        {"parse_status": "invalid", "payload_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest()},
                        state,
                        parent_record_id=record["record_id"],
                    )
                    child["planned_action"] = "quarantine_candidate"
                    child["reason_codes"] = ["invalid_jsonl"]
                    child["status"] = "invalid"
                    children.append(child)
                    continue
                if not isinstance(item, dict):
                    state.error("INVALID_JSONL", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_legacy_edge", reason_codes=(f"line_{number}",))
                    invalid = True
                    child = _child_record(
                        entry,
                        record["category"],
                        record["owner"],
                        record["sha256"],
                        number,
                        {"parse_status": "invalid"},
                        state,
                        parent_record_id=record["record_id"],
                    )
                    child["planned_action"] = "quarantine_candidate"
                    child["reason_codes"] = ["invalid_legacy_edge"]
                    child["status"] = "invalid"
                    children.append(child)
                    continue
                source = str(item.get("source") or item.get("from") or "")
                target = str(item.get("target") or item.get("to") or "")
                raw_edge_type = str(item.get("edge_type") or item.get("type") or "").strip().casefold()
                edge_type = raw_edge_type if raw_edge_type in _GRAPH_EDGE_TYPES else "unknown"
                if edge_type == "unknown":
                    state.error(
                        "UNKNOWN_ENTRY",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="validate_legacy_edge_type",
                        reason_codes=(f"line_{number}", "unknown_graph_edge_type"),
                    )
                    invalid = True
                identity = (
                    unicodedata.normalize("NFKC", source).casefold(),
                    unicodedata.normalize("NFKC", target).casefold(),
                    edge_type,
                )
                status = "duplicate" if identity in seen else "unresolved_legacy_reference"
                seen.add(identity)
                status_counts[status] += 1
                kind_counts[edge_type] = kind_counts.get(edge_type, 0) + 1
                shape = {
                    "edge_type": edge_type,
                    "payload_sha256": sha256_canonical(item),
                    "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                    "status": status,
                    "target_sha256": hashlib.sha256(target.encode("utf-8")).hexdigest(),
                }
                child = _child_record(
                    entry,
                    record["category"],
                    record["owner"],
                    record["sha256"],
                    number,
                    shape,
                    state,
                    parent_record_id=record["record_id"],
                )
                child["planned_action"] = "quarantine_candidate"
                child["reason_codes"] = [status]
                child["status"] = status
                children.append(child)
                state.relationships.append(
                    {
                        "from_ref_sha256": shape["source_sha256"],
                        "reason_codes": [status],
                        "relationship_id": "rel_" + sha256_canonical({"record": child["record_id"], "shape": shape})[:24],
                        "relationship_type": edge_type,
                        "source_record_id": child["record_id"],
                        "status": status,
                        "to_ref_sha256": shape["target_sha256"],
                    }
                )
    except UnicodeError:
        state.error("INVALID_UTF8", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_legacy_edges")
        invalid = True
    except OSError:
        state.error("READ_FAILED", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_legacy_edges")
        invalid = True
    state.records.extend(children)
    record["parse"] = {
        "child_count": len(children),
        "kind_counts": {key: kind_counts[key] for key in sorted(kind_counts)},
        "logical_sha256": sha256_canonical([child["sha256"] for child in children]),
        "record_count": len(children),
        "schema_version": None,
        "status": "invalid" if invalid else "valid",
        "status_counts": status_counts,
    }


def _candidate_enum(value: Any, allowed: set[str]) -> str:
    text = str(value or "").strip().casefold()
    return text if text in allowed else "unknown"


def _parse_json_record(entry: _Entry, record: dict[str, Any], state: _State) -> None:
    data = _read_json(entry, state, record.get("sha256"))
    if data is None:
        record["parse"]["status"] = "invalid"
        return
    children: list[dict[str, Any]] = []
    category = record["category"]
    if category == "identity.users_registry":
        users = data.get("users") if isinstance(data, dict) else None
        if not isinstance(users, dict):
            state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_users_schema")
            record["parse"]["status"] = "invalid"
            return
        normalized_users: dict[str, list[tuple[str, str, dict[str, Any]]]] = {}
        invalid_registry_keys: set[str] = set()
        for raw_user_id, raw_user in users.items():
            claimed_user_id = str(raw_user_id)
            normalized_key = claimed_user_id.strip().lower()
            supplied_id = (
                str(raw_user.get("id") or claimed_user_id).strip().lower()
                if isinstance(raw_user, dict)
                else ""
            )
            password_hash = raw_user.get("password_hash") if isinstance(raw_user, dict) else None
            explicit_role = (
                str(raw_user.get("system_role") or raw_user.get("role") or "").strip().casefold()
                if isinstance(raw_user, dict)
                else ""
            )
            explicit_status = (
                str(raw_user.get("account_status") or raw_user.get("status") or "").strip().casefold()
                if isinstance(raw_user, dict)
                else ""
            )
            valid = (
                isinstance(raw_user, dict)
                and _USER_ID_RE.fullmatch(normalized_key) is not None
                and supplied_id == normalized_key
                and isinstance(password_hash, str)
                and bool(password_hash.strip())
                and (not explicit_role or explicit_role in {"admin", "user"})
                and (not explicit_status or explicit_status in {"active", "disabled"})
            )
            normalized = _owner_key(claimed_user_id)
            if not valid:
                invalid_registry_keys.add(normalized)
                state.registry_user_ids[normalized] = None
                state.error(
                    "INVALID_JSON",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="validate_user_registry_entry",
                    reason_codes=("invalid_legacy_user",),
                )
                continue
            normalized_users.setdefault(normalized, []).append(
                (claimed_user_id, normalized_key, raw_user)
            )
        for normalized, entries in normalized_users.items():
            if normalized in invalid_registry_keys or len(entries) != 1:
                state.registry_user_ids[normalized] = None
                state.error(
                    "OWNER_ID_COLLISION",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="validate_user_registry",
                    reason_codes=("nfkc_casefold_collision",),
                )
            else:
                state.registry_user_ids[normalized] = entries[0][1]
        ordered_users = sorted(users.items(), key=lambda item: (_owner_key(str(item[0])), str(item[0])))
        for ordinal, (raw_user_id, user) in enumerate(ordered_users, 1):
            user_id = str(raw_user_id)
            normalized = _owner_key(user_id)
            canonical_user_id = state.registry_user_ids.get(normalized)
            if isinstance(user, dict):
                explicit_status = str(user.get("account_status") or user.get("status") or "").strip().casefold()
                raw_status = (
                    "disabled"
                    if bool(user.get("disabled")) or user.get("active") is False
                    else (explicit_status or "active")
                )
                raw_role = str(user.get("system_role") or user.get("role") or "").strip().casefold()
                role = raw_role if raw_role in {"admin", "user"} else "unassigned_legacy"
            else:
                raw_status = "unknown"
                role = None
            shape = {
                "account_status": raw_status,
                "payload_sha256": sha256_canonical(user),
                "role": role,
                "user_id_sha256": hashlib.sha256(user_id.encode("utf-8")).hexdigest(),
            }
            if canonical_user_id is None:
                child_owner = {
                    "claimed_user_id": user_id,
                    "kind": "ambiguous_registry_owner",
                    "mapping_status": "ambiguous",
                    "user_id": None,
                }
            else:
                child_owner = _confirmed_personal_owner(canonical_user_id)
                state.registry_accounts[canonical_user_id] = {
                    "account_status": raw_status,
                    "registry_record_id": None,
                    "role": role,
                }
            child = _child_record(
                entry,
                category,
                child_owner,
                record["sha256"],
                ordinal,
                shape,
                state,
                parent_record_id=record["record_id"],
            )
            child["parse"]["registry_account"] = shape
            if canonical_user_id is not None:
                state.registry_accounts[canonical_user_id]["registry_record_id"] = child["record_id"]
                if role == "unassigned_legacy":
                    child["planned_action"] = "classify_or_quarantine"
                    child["reason_codes"] = ["legacy_role_requires_plan"]
            else:
                child["planned_action"] = "quarantine_candidate"
                child["reason_codes"] = ["invalid_or_ambiguous_legacy_user"]
                child["status"] = "invalid"
            children.append(child)
    elif category == "knowledge.wiki_graph_edges":
        if not isinstance(data, dict) or not isinstance(data.get("nodes", []), list) or not isinstance(data.get("edges", []), list):
            state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_graph_schema")
            record["parse"]["status"] = "invalid"
            return
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        node_occurrences: dict[str, int] = {}
        for node in nodes:
            node_id = str(node.get("id") or "") if isinstance(node, dict) else ""
            node_key = _path_key(node_id) if node_id else ""
            node_occurrences[node_key] = node_occurrences.get(node_key, 0) + 1
        for ordinal, node in enumerate(nodes, 1):
            shape = {"id_sha256": hashlib.sha256(str(node.get("id", "")).encode("utf-8")).hexdigest(), "payload_sha256": sha256_canonical(node)} if isinstance(node, dict) else {"payload_sha256": sha256_canonical(node)}
            node_id = str(node.get("id") or "") if isinstance(node, dict) else ""
            node_key = _path_key(node_id) if node_id else ""
            invalid_node = not node_id or node_occurrences[node_key] != 1
            child = _child_record(entry, "knowledge.wiki_graph_nodes", record["owner"], record["sha256"], ordinal, shape, state, parent_record_id=record["record_id"])
            if invalid_node:
                child["planned_action"] = "quarantine_candidate"
                child["reason_codes"] = ["invalid_or_duplicate_graph_node"]
                child["status"] = "invalid"
                state.error(
                    "UNKNOWN_ENTRY",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="validate_graph_node",
                    reason_codes=("invalid_or_duplicate_graph_node",),
                )
            children.append(child)
            state.graph_nodes.append(
                {
                    "child": child,
                    "node_id": node_id,
                    "owner": record["owner"],
                    "parent": record,
                    "valid_shape": not invalid_node,
                }
            )
        node_ids = {node_id for node_id, count in node_occurrences.items() if node_id and count == 1}
        seen_edges: set[tuple[str, str, str]] = set()
        quality = {"duplicate": 0, "legacy_auto": 0, "orphan": 0, "valid": 0}
        offset = len(nodes)
        for index, edge in enumerate(edges, 1):
            source = str(edge.get("source") or edge.get("from") or "") if isinstance(edge, dict) else ""
            target = str(edge.get("target") or edge.get("to") or "") if isinstance(edge, dict) else ""
            raw_relation_type = (
                str(edge.get("edge_type") or edge.get("type") or edge.get("kind") or "")
                .strip()
                .casefold()
                if isinstance(edge, dict)
                else ""
            )
            relation_type = raw_relation_type if raw_relation_type in _GRAPH_EDGE_TYPES else "unknown"
            metadata = edge.get("metadata") if isinstance(edge, dict) and isinstance(edge.get("metadata"), dict) else {}
            reason = str(metadata.get("reason") or (edge.get("reason") if isinstance(edge, dict) else "") or "").strip().casefold()
            identity = (
                unicodedata.normalize("NFKC", source).casefold(),
                unicodedata.normalize("NFKC", target).casefold(),
                relation_type or "unknown",
            )
            if relation_type in {None, "unknown"}:
                state.error(
                    "UNKNOWN_ENTRY",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="validate_graph_edge_type",
                    reason_codes=("unknown_graph_edge_type",),
                )
            if _path_key(source) not in node_ids or _path_key(target) not in node_ids:
                disposition = "orphan"
            elif identity in seen_edges:
                disposition = "duplicate"
            elif relation_type in {None, "unknown"}:
                disposition = "orphan"
            elif (
                reason == "keyword overlap during wiki import"
                or metadata.get("auto_graph") is True
                or metadata.get("auto_generated") is True
            ):
                disposition = "legacy_auto"
            else:
                disposition = "valid"
            seen_edges.add(identity)
            if disposition == "valid":
                quality["valid"] += 1
            else:
                quality[disposition] += 1
            shape = {"disposition": disposition, "payload_sha256": sha256_canonical(edge), "relation_type": relation_type, "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(), "target_sha256": hashlib.sha256(target.encode("utf-8")).hexdigest()}
            child = _child_record(entry, category, record["owner"], record["sha256"], offset + index, shape, state, parent_record_id=record["record_id"])
            child["graph_edge"] = dict(shape)
            if disposition != "valid":
                child["planned_action"] = "quarantine_candidate"
                child["reason_codes"] = [f"graph_edge_{disposition}"]
            children.append(child)
            relationship = {"from_ref_sha256": shape["source_sha256"], "reason_codes": [] if disposition == "valid" else [f"graph_edge_{disposition}"], "relationship_id": "rel_" + sha256_canonical({"record": child["record_id"], "shape": shape})[:24], "relationship_type": shape["relation_type"] or "unknown", "source_record_id": child["record_id"], "status": disposition, "to_ref_sha256": shape["target_sha256"]}
            state.relationships.append(relationship)
            state.graph_edges.append(
                {
                    "child": child,
                    "identity": identity,
                    "legacy_auto": (
                        reason == "keyword overlap during wiki import"
                        or metadata.get("auto_graph") is True
                        or metadata.get("auto_generated") is True
                    ),
                    "parent": record,
                    "relation_type": relation_type,
                    "relationship": relationship,
                    "source": source,
                    "target": target,
                }
            )
    elif category == "knowledge.wiki_candidates":
        if not isinstance(data, list):
            state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_candidates_schema")
            record["parse"]["status"] = "invalid"
            return
        action_counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        # Resolution is finalized after every document alias has been scanned.
        # Keep the public aggregate shape stable even when the candidate file
        # is empty; non-empty parents have these two counters replaced with
        # their reconciled values below.
        evidence_counts = {
            "base_or_revision": 0,
            "identity_evidence": 0,
            "resolved": 0,
            "source": 0,
            "target": 0,
        }
        for ordinal, item in enumerate(data, 1):
            if not isinstance(item, dict):
                state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_candidate_schema")
                child = _child_record(
                    entry,
                    category,
                    record["owner"],
                    record["sha256"],
                    ordinal,
                    {"parse_status": "invalid", "payload_sha256": sha256_canonical(item)},
                    state,
                    parent_record_id=record["record_id"],
                )
                child["planned_action"] = "quarantine_candidate"
                child["reason_codes"] = ["invalid_candidate"]
                child["status"] = "invalid"
                child["candidate"] = {
                    "action": "unknown",
                    "base_or_revision_evidence": False,
                    "identity_evidence": False,
                    "kind": "unknown",
                    "payload_sha256": sha256_canonical(item),
                    "resolution": {
                        "source_record_id": None,
                        "status": "unresolved",
                        "target_record_id": None,
                    },
                    "source_artifact_sha256": None,
                    "status": "unknown",
                    "target_sha256": None,
                }
                children.append(child)
                continue
            kind = _candidate_enum(item.get("candidate_kind") or item.get("kind"), {"graph_candidate", "wiki_diff"})
            action = _candidate_enum(item.get("action"), {"add_edge", "create"})
            status = _candidate_enum(item.get("status"), {"applied", "deleted", "pending", "rejected", "skipped"})
            unknown_fields = tuple(
                name
                for name, value in (("kind", kind), ("action", action), ("status", status))
                if value == "unknown"
            )
            if unknown_fields:
                state.error(
                    "UNKNOWN_ENTRY",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="validate_candidate_enum",
                    reason_codes=tuple(f"unknown_candidate_{name}" for name in unknown_fields),
                )
            edge = item.get("edge") if isinstance(item.get("edge"), dict) else {}
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            source_evidence = {
                key: source[key]
                for key in ("filename", "raw_path", "source_id")
                if source.get(key) not in (None, "")
            }
            target_evidence = item.get("target_id") or item.get("target_path") or edge.get("target")
            has_base_revision = any(
                item.get(key) not in (None, "")
                for key in ("base_hash", "base_revision", "expected_revision", "revision")
            )
            evidence_counts["source"] += bool(source_evidence)
            evidence_counts["target"] += target_evidence not in (None, "")
            evidence_counts["base_or_revision"] += has_base_revision
            action_counts[action] = action_counts.get(action, 0) + 1
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            status_counts[status] = status_counts.get(status, 0) + 1
            shape = {
                "action": action,
                "base_or_revision_evidence": has_base_revision,
                "kind": kind,
                "payload_sha256": sha256_canonical(item),
                "source_artifact_sha256": sha256_canonical(source_evidence) if source_evidence else None,
                "status": status,
                "target_sha256": sha256_canonical(target_evidence) if target_evidence not in (None, "") else None,
            }
            child = _child_record(entry, category, record["owner"], record["sha256"], ordinal, shape, state, parent_record_id=record["record_id"])
            child["candidate"] = shape
            if unknown_fields:
                child["planned_action"] = "quarantine_candidate"
                child["reason_codes"] = sorted(
                    {f"unknown_candidate_{name}" for name in unknown_fields}
                )
                child["status"] = "unknown"
            children.append(child)
            state.candidates.append(
                {
                    "child": child,
                    "parent": record,
                    "source_ids": tuple(
                        str(value)
                        for value in (source.get("source_id"), source.get("filename"), source.get("raw_path"))
                        if value not in (None, "")
                    ),
                    "target_ids": tuple(
                        str(value)
                        for value in (target_evidence, item.get("target_filename"), item.get("target_path"))
                        if value not in (None, "")
                    ),
                    "unknown_fields": unknown_fields,
                }
            )
    elif category in {"conversation.user_sessions", "conversation.global_sessions"}:
        if not isinstance(data, dict):
            state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_session_schema")
            record["parse"]["status"] = "invalid"
            return
        messages = data.get("messages", [])
        uploaded_files = data.get("uploaded_files", data.get("uploads", []))
        skills_used = data.get("skills_used", [])
        metadata = data.get("metadata", {})
        if not isinstance(messages, list) or not isinstance(uploaded_files, list) or not isinstance(skills_used, list) or not isinstance(metadata, dict):
            state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_session_schema")
            record["parse"]["status"] = "invalid"
            return
        session_id = str(data.get("session_id") or data.get("id") or "").strip()
        filename_session_id = Path(entry.relative_path).stem
        session_reasons: list[str] = []
        identity_aliases = [
            str(data[key]).strip()
            for key in ("session_id", "id")
            if data.get(key) not in (None, "")
        ]
        if len({_path_key(value) for value in identity_aliases}) > 1:
            session_reasons.append("conflicting_session_identity_aliases")
        if not session_id or session_id != filename_session_id:
            session_reasons.append("session_id_filename_mismatch")
        if "uploaded_files" in data and "uploads" in data and (
            data.get("uploaded_files") != data.get("uploads")
        ):
            session_reasons.append("conflicting_session_upload_aliases")
        summary_aliases = [
            value
            for value in (
                metadata.get("compact_summary"),
                data.get("compact_summary"),
                data.get("summary"),
            )
            if value not in (None, "")
        ]
        if len({sha256_canonical(value) for value in summary_aliases}) > 1:
            session_reasons.append("conflicting_session_summary_aliases")
        declared_count = data.get("message_count")
        if declared_count is not None and (
            isinstance(declared_count, bool)
            or not isinstance(declared_count, int)
            or declared_count != len(messages)
        ):
            session_reasons.append("declared_message_count_mismatch")
        if any(
            not isinstance(message, dict)
            or not isinstance(message.get("role"), str)
            or message.get("role") not in {"system", "user", "assistant", "tool"}
            for message in messages
        ):
            session_reasons.append("invalid_session_message")
        if any(not isinstance(upload, dict) for upload in uploaded_files):
            session_reasons.append("invalid_session_upload")
        if any(not isinstance(skill, (str, dict)) for skill in skills_used):
            session_reasons.append("invalid_session_skill")
        if "timestamp" in data and (
            isinstance(data["timestamp"], bool)
            or not isinstance(data["timestamp"], (int, float))
        ):
            session_reasons.append("invalid_session_timestamp")
        if "title" in data and not isinstance(data["title"], str):
            session_reasons.append("invalid_session_title")
        binding_keys = (
            "knowledge_files", "knowledge_space_id", "memory_ids",
            "notion_page_ids", "notion_pages", "pinned_thoughts",
            "pinned_wiki_pages", "project_context_ids", "project_id", "space_id",
        )
        binding_payloads: list[Any] = []
        top_level_bindings = data.get("bindings", [])
        if top_level_bindings is not None and not isinstance(top_level_bindings, list):
            state.error("INVALID_JSON", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="validate_session_schema")
            record["parse"]["status"] = "invalid"
            return
        binding_payloads.extend(top_level_bindings or [])
        for key in binding_keys:
            metadata_value = metadata.get(key)
            top_level_value = data.get(key)
            if (
                metadata_value not in (None, "", [])
                and top_level_value not in (None, "", [])
                and metadata_value != top_level_value
            ):
                session_reasons.append(f"conflicting_session_{key}_aliases")
            value = metadata_value if metadata_value is not None else top_level_value
            if isinstance(value, list):
                binding_payloads.extend(value)
            elif isinstance(value, (str, int)) and str(value).strip():
                binding_payloads.append(value)
        summary_payload = (
            metadata.get("compact_summary")
            or data.get("compact_summary")
            or data.get("summary")
        )
        collection_keys = {
            "bindings", "compact_summary", "messages", "metadata", "skills_used",
            "summary", "uploaded_files", "uploads", *binding_keys,
        }
        session_core = {
            key: data[key]
            for key in sorted(data)
            if key not in collection_keys
        }
        child_payloads: dict[str, list[Any]] = {
            "session_core_metadata": [session_core],
            "messages": list(messages),
            "metadata": ([{"metadata": metadata, "skills_used": skills_used}] if metadata or skills_used else []),
            "summaries": ([summary_payload] if summary_payload else []),
            "uploads": list(uploaded_files),
            "bindings": binding_payloads,
        }
        child_counts = {
            kind: len(child_payloads[kind])
            for kind in ("session_core_metadata", "messages", "metadata", "summaries", "uploads", "bindings")
        }
        session_children: list[dict[str, Any]] = []
        aggregates: dict[str, str] = {}
        ordinal = 0
        for kind in ("session_core_metadata", "messages", "metadata", "summaries", "uploads", "bindings"):
            payload_hashes = []
            for payload in child_payloads[kind]:
                ordinal += 1
                payload_sha256 = sha256_canonical(payload)
                payload_hashes.append(payload_sha256)
                child = _child_record(
                    entry,
                    category,
                    record["owner"],
                    record["sha256"],
                    ordinal,
                    {"child_kind": kind, "payload_sha256": payload_sha256},
                    state,
                    parent_record_id=record["record_id"],
                )
                child["child_kind"] = kind
                child["payload_sha256"] = payload_sha256
                if session_reasons:
                    child["planned_action"] = "quarantine_candidate"
                    child["reason_codes"] = sorted(set(session_reasons))
                    child["status"] = "invalid"
                session_children.append(child)
            aggregates[kind] = sha256_canonical(payload_hashes)
        state.records.extend(session_children)
        if session_reasons:
            record["planned_action"] = "quarantine_candidate"
            record["reason_codes"] = sorted(set(session_reasons))
            record["status"] = "invalid"
            state.error(
                "INVALID_JSON",
                root_id=entry.root.root_id,
                relative_path=entry.relative_path,
                operation="validate_session_semantics",
                reason_codes=session_reasons,
            )
        elif record["owner"].get("mapping_status") == "confirmed" and record["owner"].get("user_id"):
            key = (str(record["owner"]["user_id"]), session_id)
            if key in state.sessions:
                record["planned_action"] = "quarantine_candidate"
                record["reason_codes"] = ["duplicate_session_identity"]
                record["status"] = "invalid"
                state.error(
                    "DUPLICATE_RELATIONSHIP",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="register_session_identity",
                    reason_codes=("duplicate_session_identity",),
                )
            else:
                state.sessions[key] = record["record_id"]
        record["parse"] = {"child_aggregate_sha256": aggregates, "child_count": len(session_children), "child_counts": child_counts, "logical_sha256": sha256_canonical(aggregates), "record_count": len(session_children), "schema_version": None, "session_id_sha256": hashlib.sha256(session_id.encode("utf-8")).hexdigest() if session_id else None, "status": "invalid" if session_reasons else "valid"}
        return
    else:
        source_member_count = len(data) if isinstance(data, (dict, list)) else 1
        record["parse"] = {
            "child_count": 0,
            "logical_sha256": sha256_canonical(
                {"container_type": type(data).__name__, "payload_sha256": sha256_canonical(data)}
            ),
            "record_count": 1,
            "schema_version": None,
            "source_member_count": source_member_count,
            "status": "valid",
        }
        if category == "operations.jobs" and not isinstance(data, dict):
            record["planned_action"] = "quarantine_candidate"
            record["reason_codes"] = ["job_record_not_object"]
            record["status"] = "invalid"
            record["parse"]["status"] = "invalid"
            state.error(
                "INVALID_JSON",
                root_id=entry.root.root_id,
                relative_path=entry.relative_path,
                operation="validate_job_identity",
                reason_codes=("job_record_not_object",),
            )
        elif category == "operations.jobs":
            status = _job_status(data.get("status"))
            record["parse"]["source_status"] = status or "unknown"
            identity_reasons: list[str] = []
            job_id = str(data.get("job_id") or data.get("id") or "").strip()
            job_identity_aliases = [
                str(data[key]).strip()
                for key in ("job_id", "id")
                if data.get(key) not in (None, "")
            ]
            if len({_path_key(value) for value in job_identity_aliases}) > 1:
                identity_reasons.append("conflicting_job_identity_aliases")
            if not job_id or job_id != Path(entry.relative_path).stem:
                identity_reasons.append("job_id_filename_mismatch")
            job_kind = str(data.get("kind") or "").strip()
            if job_kind not in _JOB_KINDS:
                identity_reasons.append("unknown_job_kind")
            embedded_user_id = str(data.get("user_id") or "").strip()
            canonical_owner = record["owner"].get("user_id")
            record["parse"]["job_identity"] = {
                "aliases_consistent": len(
                    {_path_key(value) for value in job_identity_aliases}
                )
                <= 1,
                "job_id_sha256": (
                    hashlib.sha256(job_id.encode("utf-8")).hexdigest()
                    if job_id
                    else None
                ),
                "kind": job_kind if job_kind in _JOB_KINDS else "unknown",
                "owner_user_id_sha256": (
                    hashlib.sha256(embedded_user_id.encode("utf-8")).hexdigest()
                    if embedded_user_id
                    else None
                ),
                "payload_sha256": sha256_canonical(data),
            }
            if not embedded_user_id or canonical_owner is None or embedded_user_id != canonical_owner:
                identity_reasons.append("job_owner_mismatch")
            if status == "unknown":
                identity_reasons.append("unknown_job_status")
            if identity_reasons:
                record["planned_action"] = "quarantine_candidate"
                record["reason_codes"] = sorted(set(identity_reasons))
                record["status"] = "unknown"
                state.error(
                    "UNKNOWN_ENTRY",
                    root_id=entry.root.root_id,
                    relative_path=entry.relative_path,
                    operation="validate_job_identity",
                    reason_codes=identity_reasons,
                )
            elif status in {"queued", "running"}:
                record["status"] = "abandoned_legacy"
                record["reason_codes"] = ["legacy_job_not_replayable"]
        return
    state.records.extend(children)
    record["parse"] = {"child_count": len(children), "logical_sha256": sha256_canonical([child["sha256"] for child in children]), "record_count": len(children), "schema_version": None, "status": "valid"}
    if category == "knowledge.wiki_graph_edges":
        record["parse"]["graph"] = {
            "duplicate": quality["duplicate"],
            "edges": len(edges),
            "legacy_auto": quality["legacy_auto"],
            "nodes": len(nodes),
            "orphan": quality["orphan"],
            "valid": quality["valid"],
        }
    elif category == "knowledge.wiki_candidates":
        record["parse"]["candidate_counts"] = {
            "actions": {key: action_counts[key] for key in sorted(action_counts)},
            "evidence": evidence_counts,
            "kinds": {key: kind_counts[key] for key in sorted(kind_counts)},
            "statuses": {key: status_counts[key] for key in sorted(status_counts)},
        }


def _parse_markdown_assets(entry: _Entry, record: dict[str, Any], state: _State) -> None:
    def mark_invalid(reason: str) -> None:
        record["parse"]["status"] = "invalid"
        record["planned_action"] = "quarantine_candidate"
        record["reason_codes"] = sorted(set(record["reason_codes"] + [reason]))
        record["status"] = "invalid"
        record["asset_reference_evidence"] = {
            "declared_path_sha256": [],
            "status": "invalid",
            "syntax": "known_producer_inline_markdown_v1",
        }

    raw_bytes = _read_verified_bytes(
        entry,
        state,
        record.get("sha256"),
        operation="parse_markdown_references",
    )
    if raw_bytes is None:
        mark_invalid("asset_reference_parse_failed")
        return
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeError:
        state.error("INVALID_UTF8", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_markdown_references")
        mark_invalid("asset_reference_parse_failed")
        return
    except OSError:
        state.error("READ_FAILED", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="parse_markdown_references")
        mark_invalid("asset_reference_parse_failed")
        return
    references = []
    base = Path(entry.relative_path).parent
    source_path = entry.relative_path.replace("\\", "/")
    wiki_marker = "/wiki/"
    wiki_index = source_path.casefold().find(wiki_marker)
    owner_wiki_prefix = (
        source_path[: wiki_index + len("/wiki")]
        if wiki_index >= 0
        else ""
    )
    for raw in _MARKDOWN_LINK_RE.findall(text):
        target = unquote(raw.strip().strip("<>"))
        split = urlsplit(target)
        if split.scheme or split.netloc or not split.path:
            continue
        normalized = os.path.normpath(str(base / split.path)).replace("\\", "/")
        if normalized.startswith("../") or "/assets/" not in "/" + normalized.casefold():
            continue
        if (
            not owner_wiki_prefix
            or not _path_key(normalized).startswith(
                _path_key(owner_wiki_prefix + "/assets/")
            )
        ):
            record["planned_action"] = "quarantine_candidate"
            record["reason_codes"] = sorted(
                set(record["reason_codes"] + ["cross_owner_asset_reference"])
            )
            record["status"] = "invalid"
            state.error(
                "MISSING_REFERENCE",
                root_id=entry.root.root_id,
                relative_path=entry.relative_path,
                operation="parse_markdown_references",
                reason_codes=("cross_owner_asset_reference",),
            )
            continue
        references.append(normalized)
    owner_id = str(record["owner"].get("user_id") or "")
    state.declared_assets[record["record_id"]] = (
        entry.root.root_id,
        owner_id,
        sorted(set(references), key=lambda value: (value.casefold(), value)),
    )
    # Preserve a body-free authority digest so the relationship ledger and
    # mutable counters cannot both be deleted while retaining a valid source
    # record.  Paths are represented only by normalized SHA-256 identities.
    normalized_references = state.declared_assets[record["record_id"]][2]
    record["asset_reference_evidence"] = {
        "declared_path_sha256": sorted(
            hashlib.sha256(_path_key(value).encode("utf-8")).hexdigest()
            for value in normalized_references
        ),
        "status": "valid",
        "syntax": "known_producer_inline_markdown_v1",
    }


def _sqlite_uri(path: Path) -> str:
    return path.resolve(strict=True).as_uri() + "?mode=ro"


def _source_file_fact(path: Path) -> tuple[int, int, int, int, str]:
    before = path.stat(follow_symlinks=False)
    if _is_reparse(path, before):
        raise OSError("sqlite_source_reparse")
    digest = hashlib.sha256()
    flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb", buffering=0) as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise OSError("sqlite_source_identity_changed")
        while True:
            chunk = stream.read(_BUFFER_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    ):
        raise OSError("source_changed")
    return (
        int(after.st_dev),
        int(after.st_ino),
        int(after.st_size),
        int(after.st_mtime_ns),
        digest.hexdigest(),
    )


def _copy_sqlite_snapshot(
    entry: _Entry, destination_root: Path, state: _State
) -> Path:
    source = _extended_windows_path(entry.path)
    wal = Path(str(source) + "-wal")
    shm = Path(str(source) + "-shm")
    journal = Path(str(source) + "-journal")
    if (journal.is_file() and (wal.is_file() or shm.is_file())) or (
        shm.is_file() and not wal.is_file()
    ):
        raise OSError("conflicting_sqlite_sidecars")
    sources = [candidate for candidate in (source, wal, shm, journal) if candidate.is_file()]
    before = {candidate.name: _source_file_fact(candidate) for candidate in sources}
    for candidate in sources:
        relative = _relative(candidate, entry.root.configured)
        key = (entry.root.root_id, _path_key(relative))
        copied_sha256 = before[candidate.name][4]
        scanned_sha256 = state.scan_hashes.get(key)
        if scanned_sha256 is not None and scanned_sha256 != copied_sha256:
            raise OSError("sqlite_snapshot_does_not_match_record")
        state.sqlite_snapshot_hashes[key] = copied_sha256
    _assert_scratch_identity(state)
    destination_identity = _directory_identity(
        destination_root, parent=state.scratch_root
    )
    destination_anchor = state.scratch_anchor
    if (
        destination_anchor is None
        or destination_anchor.closed
        or destination_anchor.path != destination_root
    ):
        raise OSError("scratch_directory_not_anchored")
    for candidate in sources:
        _assert_scratch_identity(state)
        if _directory_identity(
            destination_root, parent=state.scratch_root
        ) != destination_identity:
            raise OSError("scratch_directory_identity_changed")
        source_flags = os.O_RDONLY | int(getattr(os, "O_BINARY", 0))
        source_flags |= int(getattr(os, "O_NOFOLLOW", 0))
        source_descriptor = os.open(candidate, source_flags)
        target_handle: int | None = None
        target_committed = False
        try:
            opened_source = os.fstat(source_descriptor)
            expected_source = before[candidate.name]
            if (opened_source.st_dev, opened_source.st_ino) != expected_source[:2]:
                raise OSError("sqlite_source_identity_changed")
            target_handle = destination_anchor.create_relative_file(candidate.name)
            # Delete-pending removes the linkability window while the handle
            # remains writable.  The snapshot gets a final name only after all
            # bytes have been flushed through this exact handle.
            _nt_set_disposition(target_handle)
            try:
                with os.fdopen(source_descriptor, "rb", buffering=0) as source_stream:
                    source_descriptor = -1
                    while True:
                        chunk = source_stream.read(_BUFFER_SIZE)
                        if not chunk:
                            break
                        _write_windows_handle(target_handle, chunk, flush=False)
                _flush_windows_handle(target_handle)
                _nt_clear_disposition(target_handle)
                _nt_rename_relative(
                    target_handle,
                    destination_anchor.handle,
                    candidate.name,
                    replace=False,
                )
                target_committed = True
            finally:
                if target_handle is not None:
                    cleanup_error: Exception | None = None
                    if not target_committed:
                        try:
                            _nt_set_disposition(target_handle)
                        except Exception as exc:
                            cleanup_error = exc
                    try:
                        _close_windows_handle(target_handle)
                    except Exception as exc:
                        cleanup_error = cleanup_error or exc
                    target_handle = None
                    if cleanup_error is not None:
                        raise cleanup_error
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)
        _assert_scratch_identity(state)
        if _directory_identity(
            destination_root, parent=state.scratch_root
        ) != destination_identity:
            raise OSError("scratch_directory_identity_changed")
    after = {candidate.name: _source_file_fact(candidate) for candidate in sources}
    if before != after:
        raise OSError("source_changed")
    return destination_root / source.name


def _quote_sqlite_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sqlite_cell_fact(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, bytes):
        return {"blob_bytes": len(value), "blob_sha256": hashlib.sha256(value).hexdigest()}
    raise ValueError("unsupported_sqlite_value")


def _sqlite_table_facts(
    connection: sqlite3.Connection, names: set[str]
) -> tuple[dict[str, int], dict[str, str], dict[str, tuple[str, ...]]]:
    counts: dict[str, int] = {}
    hashes: dict[str, str] = {}
    columns_by_table: dict[str, tuple[str, ...]] = {}
    for table in sorted(_NOTION_TABLES & names):
        quoted = _quote_sqlite_identifier(table)
        columns = tuple(
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({quoted})")
        )
        columns_by_table[table] = columns
        rows = connection.execute(f"SELECT * FROM {quoted}").fetchall()
        row_hashes = sorted(
            sha256_canonical([_sqlite_cell_fact(value) for value in row])
            for row in rows
        )
        counts[table] = len(rows)
        hashes[table] = sha256_canonical(row_hashes)
    return counts, hashes, columns_by_table


def _sqlite_table_shape(
    connection: sqlite3.Connection, table: str
) -> dict[str, dict[str, Any]]:
    quoted = _quote_sqlite_identifier(table)
    return {
        str(row[1]): {
            "declared_type": str(row[2] or "").upper(),
            "not_null": int(row[3]),
            "primary_key_position": int(row[5]),
        }
        for row in connection.execute(f"PRAGMA table_info({quoted})")
    }


def _notion_anchor_schema_reasons(
    connection: sqlite3.Connection, names: set[str]
) -> tuple[str, ...]:
    reasons: list[str] = []
    if {"pages", "session_pages"} - names:
        return ("missing_required_notion_table",)
    pages = _sqlite_table_shape(connection, "pages")
    links = _sqlite_table_shape(connection, "session_pages")
    for column in ("id", "owner_user_id", "created_at"):
        if column not in pages:
            reasons.append(f"pages_missing_{column}")
    if "id" in pages and (
        pages["id"]["declared_type"] != "TEXT"
        or pages["id"]["primary_key_position"] != 1
    ):
        reasons.append("pages_id_not_text_primary_key")
    if "owner_user_id" in pages and (
        pages["owner_user_id"]["declared_type"] != "TEXT"
        or pages["owner_user_id"]["not_null"] != 1
    ):
        reasons.append("pages_owner_not_required_text")
    if "created_at" in pages and (
        pages["created_at"]["declared_type"] != "INTEGER"
        or pages["created_at"]["not_null"] != 1
    ):
        reasons.append("pages_created_at_not_required_integer")
    required_links = {
        "session_id": ("TEXT", 1),
        "page_id": ("TEXT", 2),
        "owner_user_id": ("TEXT", 0),
        "created_at": ("INTEGER", 0),
    }
    for column, (declared_type, primary_key_position) in required_links.items():
        shape = links.get(column)
        if shape is None:
            reasons.append(f"session_pages_missing_{column}")
            continue
        if shape["declared_type"] != declared_type or shape["not_null"] != 1:
            reasons.append(f"session_pages_{column}_shape_invalid")
        if shape["primary_key_position"] != primary_key_position:
            reasons.append(f"session_pages_{column}_key_invalid")
    return tuple(sorted(set(reasons)))


def _parse_notion(entry: _Entry, record: dict[str, Any], state: _State) -> None:
    schema_reasons: tuple[str, ...] = ()
    duplicate_links: set[tuple[str, str]] = set()
    try:
        with _validated_scratch_directory(state) as temporary:
            snapshot = _copy_sqlite_snapshot(entry, temporary, state)
            connection = sqlite3.connect(_sqlite_uri(snapshot), uri=True)
            try:
                connection.execute("PRAGMA query_only=ON")
                query_only = int(connection.execute("PRAGMA query_only").fetchone()[0])
                if query_only != 1:
                    raise sqlite3.DatabaseError("query_only_not_enabled")
                user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                unknown_tables = sorted(
                    name
                    for name in names
                    if isinstance(name, str)
                    and not name.startswith("sqlite_")
                    and name not in _NOTION_TABLES
                )
                if unknown_tables:
                    state.error(
                        "UNKNOWN_SQLITE_TABLE",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="inventory_notion_tables",
                        reason_codes=("unaccounted_user_table",),
                    )
                required_tables = {"pages", "session_pages"}
                missing_required = sorted(required_tables - names)
                if missing_required:
                    state.error(
                        "INVALID_SQLITE",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="validate_notion_schema",
                        reason_codes=("missing_required_notion_table",),
                    )
                schema_reasons = _notion_anchor_schema_reasons(connection, names)
                if schema_reasons:
                    state.error(
                        "INVALID_SQLITE",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="validate_notion_schema",
                        reason_codes=schema_reasons,
                    )
                quick_check = connection.execute("PRAGMA quick_check").fetchall()
                quick_check_ok = len(quick_check) == 1 and str(quick_check[0][0]).casefold() == "ok"
                foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if not quick_check_ok or foreign_key_violations:
                    state.error(
                        "INVALID_SQLITE",
                        root_id=entry.root.root_id,
                        relative_path=entry.relative_path,
                        operation="validate_notion_integrity",
                        reason_codes=tuple(
                            reason
                            for reason, present in (
                                ("quick_check_failed", not quick_check_ok),
                                ("foreign_key_violation", bool(foreign_key_violations)),
                            )
                            if present
                        ),
                    )
                schema_rows = [
                    [str(row[0]), str(row[1]), str(row[2] or "")]
                    for row in connection.execute(
                        "SELECT type,name,sql FROM sqlite_master "
                        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
                    )
                ]
                schema_sha256 = sha256_canonical(schema_rows)
                counts, table_hashes, columns_by_table = _sqlite_table_facts(connection, names)
                page_owners = {
                    str(page_id): str(owner_id)
                    for page_id, owner_id in (
                        connection.execute("SELECT id,owner_user_id FROM pages").fetchall()
                        if "pages" in names
                        and {"id", "owner_user_id"}.issubset(columns_by_table.get("pages", ()))
                        else ()
                    )
                }
                if "session_pages" in names:
                    columns = columns_by_table.get("session_pages", ())
                    required_columns = {"session_id", "page_id", "owner_user_id"}
                    if not required_columns.issubset(columns):
                        raise sqlite3.DatabaseError("invalid_session_pages_shape")
                    raw_rows = connection.execute(
                        f"SELECT * FROM {_quote_sqlite_identifier('session_pages')}"
                    ).fetchall()
                    indexes = {name: columns.index(name) for name in required_columns}
                    rows = sorted(
                        raw_rows,
                        key=lambda row: (
                            str(row[indexes["session_id"]]),
                            str(row[indexes["page_id"]]),
                            str(row[indexes["owner_user_id"]]),
                        ),
                    )
                    link_counts: dict[tuple[str, str], int] = {}
                    for row in rows:
                        link_key = (
                            str(row[indexes["session_id"]]),
                            str(row[indexes["page_id"]]),
                        )
                        link_counts[link_key] = link_counts.get(link_key, 0) + 1
                    duplicate_links = {
                        key for key, count in link_counts.items() if count > 1
                    }
                    if duplicate_links:
                        state.error(
                            "DUPLICATE_RELATIONSHIP",
                            root_id=entry.root.root_id,
                            relative_path=entry.relative_path,
                            operation="validate_notion_link_identity",
                            reason_codes=("duplicate_notion_session_page",),
                        )
                    for ordinal, row in enumerate(rows, 1):
                        session_id = str(row[indexes["session_id"]])
                        page_id = str(row[indexes["page_id"]])
                        owner_id = str(row[indexes["owner_user_id"]])
                        shape = {"owner_sha256": hashlib.sha256(owner_id.encode("utf-8")).hexdigest(), "page_sha256": hashlib.sha256(page_id.encode("utf-8")).hexdigest(), "row_sha256": sha256_canonical([_sqlite_cell_fact(value) for value in row]), "session_sha256": hashlib.sha256(session_id.encode("utf-8")).hexdigest()}
                        child = _child_record(entry, "external.notion_session_links", record["owner"], record["sha256"], ordinal, shape, state, parent_record_id=record["record_id"])
                        if (session_id, page_id) in duplicate_links:
                            child["planned_action"] = "quarantine_candidate"
                            child["reason_codes"] = ["duplicate_notion_session_page"]
                            child["status"] = "invalid"
                        state.records.append(child)
                        relationship_id = "rel_" + sha256_canonical({"notion": child["record_id"], "shape": shape})[:24]
                        relationship = {"from_ref_sha256": shape["session_sha256"], "reason_codes": [], "relationship_id": relationship_id, "relationship_type": "notion_session_page", "source_record_id": child["record_id"], "status": "pending_resolution", "to_ref_sha256": shape["page_sha256"]}
                        state.relationships.append(relationship)
                        state.notion_links.append(
                            {
                                "child": child,
                                "owner_id": owner_id,
                                "page_id": page_id,
                                "page_owner_id": page_owners.get(page_id),
                                "parent": record,
                                "relationship": relationship,
                                "session_id": session_id,
                                "duplicate": (session_id, page_id) in duplicate_links,
                            }
                        )
            finally:
                connection.close()
    except OSError:
        state.error("SQLITE_SNAPSHOT_INCONSISTENT", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="copy_or_read_sqlite")
        record["parse"]["status"] = "invalid"
        return
    except (sqlite3.Error, ValueError):
        state.error("INVALID_SQLITE", root_id=entry.root.root_id, relative_path=entry.relative_path, operation="read_sqlite_snapshot")
        record["parse"]["status"] = "invalid"
        return
    emitted_children = int(counts.get("session_pages", 0))
    notion_invalid = bool(
        unknown_tables
        or missing_required
        or schema_reasons
        or duplicate_links
        or not quick_check_ok
        or foreign_key_violations
    )
    record["parse"] = {
        "child_count": emitted_children,
        "foreign_key_violation_count": len(foreign_key_violations),
        "logical_sha256": sha256_canonical({"schema_sha256": schema_sha256, "table_counts": counts, "table_sha256": table_hashes}),
        "quick_check_ok": quick_check_ok,
        "record_count": sum(counts.values()),
        "schema_sha256": schema_sha256,
        "schema_version": user_version,
        "status": "invalid" if notion_invalid else "valid",
        "table_counts": counts,
        "table_sha256": table_hashes,
        "table_row_count": sum(counts.values()),
        "unknown_table_count": len(unknown_tables),
        "unknown_table_names_sha256": sha256_canonical(unknown_tables),
    }
    if notion_invalid:
        record["planned_action"] = "quarantine_candidate"
        record["reason_codes"] = sorted(
            reason
            for reason, present in (
                ("foreign_key_violation", bool(foreign_key_violations)),
                ("missing_required_notion_table", bool(missing_required)),
                ("invalid_notion_anchor_schema", bool(schema_reasons)),
                ("duplicate_notion_session_page", bool(duplicate_links)),
                ("quick_check_failed", not quick_check_ok),
                ("unknown_sqlite_table", bool(unknown_tables)),
            )
            if present
        )


def _scan_root(root: _Root, state: _State) -> None:
    for entry in _walk(root, state, "scan_tree"):
        if entry.kind == "directory":
            continue
        if entry.kind in {"missing", "unavailable"}:
            category, owner, _ = _classify(root, entry.relative_path)
            owner = _resolve_path_owner(owner, state)
            record = _new_record(
                entry, category or "unknown.unclassified", owner, None, state,
                entry_type="unavailable",
            )
            record["planned_action"] = "report_only"
            record["reason_codes"] = ["source_entry_unavailable"]
            record["status"] = "unavailable"
            state.records.append(record)
            continue
        if entry.kind != "file":
            record = _new_record(entry, "unknown.unclassified", _owner("unknown"), None, state, entry_type=entry.kind)
            record["reason_codes"] = ["unsafe_reparse_entry" if entry.kind == "reparse" else "unsupported_entry_type"]
            state.records.append(record)
            state.error("UNSAFE_REPARSE_ENTRY" if entry.kind == "reparse" else "UNKNOWN_ENTRY", root_id=root.root_id, relative_path=entry.relative_path, operation="scan_tree")
            continue
        category, owner, exclusion = _classify(root, entry.relative_path)
        if exclusion:
            state.exclusions.append({"reason_code": exclusion, "relative_path": entry.relative_path, "root_id": root.root_id})
            continue
        owner = _resolve_path_owner(owner, state)
        digest = _stream_sha256(entry, state)
        scan_key = (root.root_id, _path_key(entry.relative_path))
        snapshot_digest = state.sqlite_snapshot_hashes.get(scan_key)
        if (
            digest is not None
            and snapshot_digest is not None
            and digest != snapshot_digest
        ):
            state.error(
                "SOURCE_CHANGED",
                root_id=root.root_id,
                relative_path=entry.relative_path,
                operation="bind_sqlite_snapshot_to_record",
                reason_codes=("sqlite_snapshot_does_not_match_record",),
            )
            digest = None
        if digest is not None:
            state.scan_hashes[scan_key] = digest
        record = _new_record(entry, category, owner, digest, state)
        state.records.append(record)
        if owner["mapping_status"] == "ambiguous":
            record["planned_action"] = "quarantine_candidate"
            record["reason_codes"] = ["ambiguous_registry_owner"]
            state.error(
                "OWNER_ID_COLLISION",
                root_id=root.root_id,
                relative_path=entry.relative_path,
                operation="resolve_path_owner",
                reason_codes=("ambiguous_registry_owner",),
            )
        if owner["mapping_status"] == "unresolved" and owner["kind"] == "unresolved_path_owner":
            record["planned_action"] = "quarantine_candidate"
            record["reason_codes"] = ["unconfirmed_path_owner"]
            state.error("MISSING_REFERENCE", root_id=root.root_id, relative_path=entry.relative_path, operation="resolve_path_owner", reason_codes=("user_not_in_registry",))
        if category == "unknown.unclassified":
            record["reason_codes"] = ["unclassified_legacy_entry"]
            state.error("UNKNOWN_ENTRY", root_id=root.root_id, relative_path=entry.relative_path, operation="classify_path")
        if digest is None:
            continue
        if category == "knowledge.wiki_assets":
            state.assets[
                (
                    root.root_id,
                    str(record["owner"].get("user_id") or ""),
                    record["path_key"],
                )
            ] = record["record_id"]
        if category == "knowledge.legacy_edges" and entry.path.suffix.casefold() == ".jsonl":
            _parse_legacy_edges_jsonl(entry, record, state)
        elif entry.path.suffix.casefold() == ".jsonl":
            _parse_jsonl(entry, record, state)
        elif entry.path.suffix.casefold() == ".json":
            _parse_json_record(entry, record, state)
        elif category == "external.notion_database":
            _parse_notion(entry, record, state)
        elif category in {"knowledge.wiki_sources", "knowledge.wiki_hidden_pages"} and entry.path.suffix.casefold() == ".md":
            _parse_markdown_assets(entry, record, state)


def _new_unavailable_record(
    root: _Root,
    relative_path: str,
    category: str,
    owner: dict[str, Any],
    state: _State,
    *,
    reason_code: str,
) -> dict[str, Any]:
    entry = _Entry(root, root.configured / Path(relative_path), relative_path, "unavailable", None)
    record = _new_record(entry, category, owner, None, state, entry_type="unavailable")
    record["planned_action"] = "report_only"
    record["reason_codes"] = [reason_code]
    record["status"] = "unavailable"
    state.records.append(record)
    return record


def _reconcile_assets(state: _State) -> None:
    referenced: set[str] = set()
    by_id = {record["record_id"]: record for record in state.records}
    missing_records: dict[tuple[str, str, str], dict[str, Any]] = {}
    for source_id, (root_id, owner_id, references) in sorted(state.declared_assets.items()):
        source = by_id[source_id]
        source["references"]["declared"] = len(references)
        for reference in references:
            asset_id = state.assets.get((root_id, owner_id, _path_key(reference)))
            if asset_id is None:
                source["references"]["missing"] += 1
                state.error("MISSING_REFERENCE", root_id=root_id, relative_path=source["relative_path"], operation="resolve_asset_reference")
                missing_key = (root_id, owner_id, _path_key(reference))
                missing = missing_records.get(missing_key)
                if missing is None:
                    # The source's root is already an approved frozen root; a
                    # missing target is an inventory fact, never a path to open.
                    source_root = next(
                        root
                        for root in state.source_roots
                        if root.root_id == root_id
                    )
                    missing = _new_unavailable_record(
                        source_root,
                        reference,
                        "knowledge.wiki_assets",
                        dict(source["owner"]),
                        state,
                        reason_code="missing_asset_reference",
                    )
                    missing_records[missing_key] = missing
                    by_id[missing["record_id"]] = missing
                state.relationships.append(
                    {
                        "from_record_id": source_id,
                        "reason_codes": ["missing_asset_reference"],
                        "relationship_id": "rel_" + sha256_canonical(
                            {"from": source_id, "to": missing["record_id"], "type": "asset_reference"}
                        )[:24],
                        "relationship_type": "asset_reference",
                        "status": "unavailable",
                        "to_record_id": missing["record_id"],
                    }
                )
                continue
            source["references"]["resolved"] += 1
            referenced.add(asset_id)
            state.relationships.append({"from_record_id": source_id, "reason_codes": [], "relationship_id": "rel_" + sha256_canonical({"from": source_id, "to": asset_id, "type": "asset_reference"})[:24], "relationship_type": "asset_reference", "status": "valid", "to_record_id": asset_id})
    for asset_id in sorted(set(state.assets.values()) - referenced):
        record = by_id[asset_id]
        record["planned_action"] = "quarantine_candidate"
        record["reason_codes"] = sorted(set(record["reason_codes"] + ["unreferenced_asset"]))


def _markdown_doc_name(filename: str) -> str:
    path = Path(filename)
    suffix = path.suffix.casefold()
    if not suffix:
        return path.name + ".md"
    if suffix in {".markdown", ".text", ".txt"}:
        return path.stem + ".md"
    return path.name if suffix == ".md" else path.stem + ".md"


def _document_aliases(record: Mapping[str, Any]) -> set[str]:
    relative_path = str(record["relative_path"])
    filename = _markdown_doc_name(Path(relative_path).name)
    category = str(record["category"])
    doc_id = filename if category == "knowledge.wiki_hidden_pages" else f"source/{filename}"
    aliases = {doc_id, relative_path}
    if category == "knowledge.wiki_hidden_pages":
        aliases.add(filename)
    folded = relative_path.replace("\\", "/")
    marker = "/wiki/"
    index = folded.casefold().find(marker)
    if index >= 0:
        aliases.add(folded[index + len(marker) :])
    return {_path_key(alias) for alias in aliases if alias}


def _add_relationship_once(state: _State, relationship: dict[str, Any]) -> None:
    identity_fields = (
        "relationship_type",
        "from_record_id",
        "source_record_id",
        "to_record_id",
        "from_ref_sha256",
        "to_ref_sha256",
    )
    identity = tuple(relationship.get(field) for field in identity_fields)
    if any(tuple(item.get(field) for field in identity_fields) == identity for item in state.relationships):
        return
    state.relationships.append(relationship)


def _reconcile_structural_references(state: _State) -> None:
    """Resolve graph/candidate/Notion identities against inventory records."""
    documents: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in state.records:
        if record["category"] not in {"knowledge.wiki_sources", "knowledge.wiki_hidden_pages"}:
            continue
        owner_id = record["owner"].get("user_id")
        if record["record_kind"] != "physical_file" or not owner_id or not record.get("sha256"):
            continue
        for alias in _document_aliases(record):
            documents.setdefault((str(owner_id), alias), []).append(record)

    resolved_nodes: dict[tuple[str, str], dict[str, Any]] = {}
    for fact in state.graph_nodes:
        child = fact["child"]
        owner_id = fact["owner"].get("user_id")
        candidates = documents.get((str(owner_id), _path_key(str(fact["node_id"]))), []) if owner_id else []
        if fact["valid_shape"] and len(candidates) == 1:
            target = candidates[0]
            resolved_nodes[(str(owner_id), _path_key(str(fact["node_id"])))] = target
            child["graph_node_resolution"] = {"source_record_id": target["record_id"], "status": "resolved"}
            _add_relationship_once(
                state,
                {
                    "from_record_id": child["record_id"],
                    "reason_codes": [],
                    "relationship_id": "rel_" + sha256_canonical(
                        {"from": child["record_id"], "to": target["record_id"], "type": "graph_node_source"}
                    )[:24],
                    "relationship_type": "graph_node_source",
                    "status": "valid",
                    "to_record_id": target["record_id"],
                },
            )
        else:
            child["planned_action"] = "quarantine_candidate"
            child["reason_codes"] = sorted(set(child["reason_codes"] + ["graph_node_source_unresolved"]))
            child["status"] = "orphan"
            child["graph_node_resolution"] = {"source_record_id": None, "status": "unresolved"}
            state.error(
                "MISSING_REFERENCE",
                root_id=str(child["root_id"]),
                relative_path=str(child["relative_path"]),
                operation="resolve_graph_node_source",
                reason_codes=("missing_or_ambiguous_graph_source",),
            )

    edge_quality: dict[str, dict[str, int]] = {}
    seen_by_parent: dict[str, set[tuple[str, str, str]]] = {}
    for fact in state.graph_edges:
        child = fact["child"]
        parent = fact["parent"]
        parent_id = str(parent["record_id"])
        owner_id = str(parent["owner"].get("user_id") or "")
        source_record = resolved_nodes.get((owner_id, _path_key(str(fact["source"]))))
        target_record = resolved_nodes.get((owner_id, _path_key(str(fact["target"]))))
        seen = seen_by_parent.setdefault(parent_id, set())
        if fact["relation_type"] == "unknown" or source_record is None or target_record is None:
            disposition = "orphan"
        elif fact["identity"] in seen:
            disposition = "duplicate"
        elif fact["legacy_auto"]:
            disposition = "legacy_auto"
        else:
            disposition = "valid"
        seen.add(fact["identity"])
        quality = edge_quality.setdefault(
            parent_id, {"duplicate": 0, "legacy_auto": 0, "orphan": 0, "valid": 0}
        )
        quality[disposition] += 1
        relationship = fact["relationship"]
        relationship["status"] = disposition
        relationship["reason_codes"] = [] if disposition == "valid" else [f"graph_edge_{disposition}"]
        if source_record is not None:
            relationship["from_record_id"] = source_record["record_id"]
        if target_record is not None:
            relationship["to_record_id"] = target_record["record_id"]
        child["graph_edge_resolution"] = {
            "from_record_id": (
                source_record["record_id"] if source_record is not None else None
            ),
            "status": disposition,
            "to_record_id": (
                target_record["record_id"] if target_record is not None else None
            ),
        }
        if disposition == "valid":
            child["status"] = "active"
            child["reason_codes"] = []
        else:
            child["planned_action"] = "quarantine_candidate"
            child["reason_codes"] = [f"graph_edge_{disposition}"]
            child["status"] = disposition
            error_code = {
                "duplicate": "DUPLICATE_RELATIONSHIP",
                "legacy_auto": "LEGACY_AUTO_RELATIONSHIP",
                "orphan": "MISSING_REFERENCE",
            }[disposition]
            state.error(
                error_code,
                root_id=str(child["root_id"]),
                relative_path=str(child["relative_path"]),
                operation="resolve_graph_edge",
                reason_codes=(f"graph_edge_{disposition}",),
            )
    for fact in state.graph_edges:
        parent = fact["parent"]
        quality = edge_quality[str(parent["record_id"])]
        parent["parse"]["graph"].update(quality)

    candidate_counts: dict[str, dict[str, int]] = {}
    for fact in state.candidates:
        child = fact["child"]
        owner_id = str(fact["parent"]["owner"].get("user_id") or "")
        source_matches = {
            row["record_id"]: row
            for alias in fact["source_ids"]
            for row in documents.get((owner_id, _path_key(alias)), [])
        }
        target_matches = {
            row["record_id"]: row
            for alias in fact["target_ids"]
            for row in documents.get((owner_id, _path_key(alias)), [])
        }
        identity_evidence = bool(fact["source_ids"] and fact["target_ids"])
        resolved = len(source_matches) == 1 and len(target_matches) == 1 and not fact["unknown_fields"]
        child["candidate"]["identity_evidence"] = identity_evidence
        child["candidate"]["resolution"] = {
            "source_record_id": next(iter(source_matches), None) if len(source_matches) == 1 else None,
            "status": "resolved" if resolved else "unresolved",
            "target_record_id": next(iter(target_matches), None) if len(target_matches) == 1 else None,
        }
        parent_id = str(fact["parent"]["record_id"])
        counts = candidate_counts.setdefault(parent_id, {"identity_evidence": 0, "resolved": 0})
        counts["identity_evidence"] += int(identity_evidence)
        counts["resolved"] += int(resolved)
        if resolved:
            source_record = next(iter(source_matches.values()))
            target_record = next(iter(target_matches.values()))
            for relationship_type, target in (
                ("candidate_source", source_record), ("candidate_target", target_record)
            ):
                _add_relationship_once(
                    state,
                    {
                        "from_record_id": child["record_id"],
                        "reason_codes": [],
                        "relationship_id": "rel_" + sha256_canonical(
                            {"from": child["record_id"], "to": target["record_id"], "type": relationship_type}
                        )[:24],
                        "relationship_type": relationship_type,
                        "status": "valid",
                        "to_record_id": target["record_id"],
                    },
                )
        elif not fact["unknown_fields"]:
            child["planned_action"] = "quarantine_candidate"
            child["reason_codes"] = sorted(set(child["reason_codes"] + ["candidate_reference_unresolved"]))
            child["status"] = "unresolved"
            state.error(
                "MISSING_REFERENCE",
                root_id=str(child["root_id"]),
                relative_path=str(child["relative_path"]),
                operation="resolve_candidate_reference",
                reason_codes=("candidate_reference_unresolved",),
            )
    for fact in state.candidates:
        parent = fact["parent"]
        counts = candidate_counts[str(parent["record_id"])]
        evidence = parent["parse"]["candidate_counts"]["evidence"]
        evidence.update(counts)

    # Hidden pages are not automatically imported, but exact source matches
    # are explicit planner evidence and never require re-reading content.
    by_content: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in state.records:
        if record["category"] == "knowledge.wiki_sources" and record.get("sha256"):
            by_content.setdefault(
                (str(record["owner"].get("user_id") or ""), str(record["sha256"])), []
            ).append(record)
    for hidden in state.records:
        if hidden["category"] != "knowledge.wiki_hidden_pages" or not hidden.get("sha256"):
            continue
        matches = by_content.get(
            (str(hidden["owner"].get("user_id") or ""), str(hidden["sha256"])), []
        )
        hidden["exact_source_duplicate_record_ids"] = sorted(row["record_id"] for row in matches)
        for source in matches:
            _add_relationship_once(
                state,
                {
                    "from_record_id": hidden["record_id"],
                    "reason_codes": [],
                    "relationship_id": "rel_" + sha256_canonical(
                        {"from": hidden["record_id"], "to": source["record_id"], "type": "exact_content_duplicate"}
                    )[:24],
                    "relationship_type": "exact_content_duplicate",
                    "status": "valid",
                    "to_record_id": source["record_id"],
                },
            )

    for fact in state.notion_links:
        child = fact["child"]
        parent_owner = fact["parent"]["owner"].get("user_id")
        claimed_owner = state.registry_user_ids.get(_owner_key(str(fact["owner_id"])))
        page_owner = (
            state.registry_user_ids.get(_owner_key(str(fact["page_owner_id"])))
            if fact["page_owner_id"] is not None
            else None
        )
        session_record_id = state.sessions.get((str(parent_owner), str(fact["session_id"]))) if parent_owner else None
        owner_valid = bool(
            parent_owner
            and claimed_owner == parent_owner
            and (fact["page_owner_id"] is None or page_owner == parent_owner)
        )
        page_exists = fact["page_owner_id"] is not None
        parent_valid = fact["parent"]["parse"].get("status") == "valid"
        if owner_valid and page_exists and session_record_id and parent_valid:
            child["link_resolution"] = {
                "page_identity_sha256": hashlib.sha256(str(fact["page_id"]).encode("utf-8")).hexdigest(),
                "page_exists": True,
                "session_record_id": session_record_id,
                "status": "resolved",
            }
            child["status"] = "active"
            fact["relationship"].update(
                {"from_record_id": session_record_id, "status": "valid", "to_record_id": child["record_id"]}
            )
        elif not owner_valid or not parent_valid:
            child["planned_action"] = "quarantine_candidate"
            link_reason = "notion_link_owner_mismatch" if not owner_valid else "notion_parent_invalid"
            child["reason_codes"] = [link_reason]
            child["status"] = "invalid"
            child["link_resolution"] = {
                "page_identity_sha256": hashlib.sha256(str(fact["page_id"]).encode("utf-8")).hexdigest(),
                "page_exists": page_exists,
                "session_record_id": session_record_id,
                "status": "owner_mismatch" if not owner_valid else "parent_invalid",
            }
            fact["relationship"]["reason_codes"] = [link_reason]
            fact["relationship"]["status"] = "invalid"
            state.error(
                "MISSING_REFERENCE",
                root_id=str(child["root_id"]),
                relative_path=str(child["relative_path"]),
                operation="resolve_notion_session_link",
                reason_codes=(link_reason,),
            )
        elif not page_exists or session_record_id is None:
            child["planned_action"] = "report_only"
            child["reason_codes"] = ["notion_link_target_unavailable"]
            child["status"] = "unavailable"
            child["link_resolution"] = {
                "page_identity_sha256": hashlib.sha256(str(fact["page_id"]).encode("utf-8")).hexdigest(),
                "page_exists": page_exists,
                "session_record_id": session_record_id,
                "status": "unavailable",
            }
            fact["relationship"]["reason_codes"] = ["notion_link_target_unavailable"]
            fact["relationship"]["status"] = "unavailable"
            state.error(
                "MISSING_REFERENCE",
                root_id=str(child["root_id"]),
                relative_path=str(child["relative_path"]),
                operation="resolve_notion_session_link",
                reason_codes=("notion_link_target_unavailable",),
            )


def _load_code_coverage(legacy_root: Path, provider: CodeCoverageProvider | None, state: _State) -> dict[str, Any]:
    try:
        if provider is None:
            module = importlib.import_module("code_assist_v1.tools.legacy_inventory_code")
            provider = module.scan_code_coverage
        result = json_safe(provider(legacy_root))
        if not isinstance(result, dict) or not isinstance(result.get("summary"), dict):
            raise ValueError("invalid_code_coverage")
        summary = result["summary"]
        if any(key not in summary for key in _CODE_SUMMARY_KEYS):
            raise ValueError("invalid_code_coverage_summary")
        if not summary["pass"] or int(summary["unclassified"]) or int(summary["unmapped_submitters"]):
            state.error("UNMAPPED_CALLSITE", root_id="legacy", operation="scan_code_coverage")
        result.setdefault("status", "complete")
        return result
    except Exception:
        state.error("CODE_SCAN_FAILED", root_id="legacy", operation="scan_code_coverage")
        return {"status": "error", "summary": {key: False if key == "pass" else 0 for key in _CODE_SUMMARY_KEYS}}


def _code_high_water(code_coverage: Mapping[str, Any]) -> dict[str, Any]:
    files = code_coverage.get("files")
    file_count = len(files) if isinstance(files, list) else 0
    source_fingerprint = code_coverage.get("source_fingerprint_sha256")
    mapping = code_coverage.get("mapping")
    mapping_sha256 = mapping.get("sha256") if isinstance(mapping, Mapping) else None
    if not isinstance(source_fingerprint, str):
        source_fingerprint = sha256_canonical(code_coverage)
    return {
        "directories": None,
        "entries": file_count + int(isinstance(mapping_sha256, str)),
        "files": file_count,
        "tree_sha256": sha256_canonical(
            {
                "mapping_sha256": mapping_sha256,
                "source_fingerprint_sha256": source_fingerprint,
            }
        ),
    }


def _record_legacy_exclusions(legacy: _Root, physical: Sequence[_Root], state: _State) -> None:
    recorded: set[str] = set()
    for root in physical:
        if _contains(legacy.configured, root.configured):
            relative = _relative(root.configured, legacy.configured)
            state.exclusions.append(
                {
                    "reason_code": "scanned_as_separate_root",
                    "relative_path": relative,
                    "root_id": legacy.root_id,
                }
            )
            recorded.add(_path_key(relative))
    for name in (
        ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".venv",
        "MODEL_GGUF", "__pycache__", "node_modules", "venv",
    ):
        candidate = legacy.configured / name
        if candidate.exists() and _path_key(name) not in recorded:
            state.exclusions.append(
                {
                    "reason_code": "non_code_bulk_root",
                    "relative_path": name,
                    "root_id": legacy.root_id,
                }
            )


def _category_rows(
    records: Sequence[Mapping[str, Any]], errors: Sequence[Mapping[str, Any]] = ()
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {category: [] for category in CATEGORY_IDS}
    for record in records:
        grouped[str(record["category"])].append(record)
    rows = []
    for category in sorted(CATEGORY_IDS):
        items = grouped[category]
        definition = category_definition(category)
        aggregate = sorted(
            [
                (
                item["record_id"],
                item["sha256"],
                item["size_bytes"],
                )
                for item in items
            ],
            key=lambda value: (
            str(value[0]),
            "" if value[1] is None else str(value[1]),
            str(value[2]),
            ),
        )
        action_counts: dict[str, int] = {}
        authority_counts: dict[str, int] = {}
        kind_counts: dict[str, int] = {}
        owner_counts: dict[str, int] = {}
        parse_status_counts: dict[str, int] = {}
        terminal_counts = {key: 0 for key in sorted(TERMINAL_CLASSIFICATIONS)}
        item_locations = {
            (str(item["root_id"]), str(item["relative_path"])) for item in items
        }
        for item in items:
            for counts, value in (
                (action_counts, str(item["planned_action"])),
                (authority_counts, str(item["classification_authority"])),
                (kind_counts, str(item["record_kind"])),
                (owner_counts, str(item["owner"]["kind"])),
                (parse_status_counts, str(item["parse"]["status"])),
            ):
                counts[value] = counts.get(value, 0) + 1
            terminal = str(item["proposed_terminal_classification"])
            if terminal in terminal_counts:
                terminal_counts[terminal] += 1
        rows.append(
            {
                **definition,
                "action_counts": {key: action_counts[key] for key in sorted(action_counts)},
                "aggregate_sha256": sha256_canonical(aggregate),
                "bytes": sum(int(item["size_bytes"] or 0) for item in items),
                "classification_authority_counts": {key: authority_counts[key] for key in sorted(authority_counts)},
                "error_count": sum(
                    error.get("severity", "error") == "error"
                    and (str(error.get("root_id") or ""), str(error.get("relative_path") or "")) in item_locations
                    for error in errors
                ),
                "parse_status_counts": {key: parse_status_counts[key] for key in sorted(parse_status_counts)},
                "owner_kind_counts": {key: owner_counts[key] for key in sorted(owner_counts)},
                "record_kind_counts": {key: kind_counts[key] for key in sorted(kind_counts)},
                "records": len(items),
                "source_rows": sum(
                    int(item["parse"].get("record_count") or 0)
                    for item in items
                    if item["record_kind"] == "physical_file"
                ),
                "terminal_classification_counts": terminal_counts,
            }
        )
    return rows


def _finalize_reconciliation(state: _State) -> dict[str, Any]:
    """Assign one proposed terminal class and prove record-tree closure."""
    ordered_record_ids = [str(record["record_id"]) for record in state.records]
    record_ids = set(ordered_record_ids)
    if len(record_ids) != len(ordered_record_ids):
        state.error(
            "RECONCILIATION_INCOMPLETE",
            operation="validate_record_identity",
            reason_codes=("duplicate_record_id",),
        )
    children_by_parent: dict[str, int] = {}
    terminal_counts = {key: 0 for key in sorted(TERMINAL_CLASSIFICATIONS)}
    authority_counts: dict[str, int] = {}
    record_kind_counts: dict[str, int] = {}
    for record in state.records:
        reasons = list(record["reason_codes"])
        invalid = (
            record["status"] in {"invalid", "unknown", "unreadable"}
            or record["parse"]["status"] == "invalid"
            or record["owner"]["mapping_status"] in {"ambiguous", "unresolved"}
        )
        if invalid:
            terminal = "quarantined"
            authority = "scanner"
            reasons.append("scanner_fail_closed")
        else:
            terminal = proposed_terminal_classification(str(record["planned_action"]))
            authority = classification_authority(str(record["planned_action"]))
        record["classification_authority"] = authority
        record["classification_reason_codes"] = sorted(set(reasons))
        record["proposed_terminal_classification"] = terminal
        terminal_counts[terminal] += 1
        authority_counts[authority] = authority_counts.get(authority, 0) + 1
        record_kind = str(record["record_kind"])
        record_kind_counts[record_kind] = record_kind_counts.get(record_kind, 0) + 1
        parent_id = record.get("parent_record_id")
        if record_kind == "logical_record":
            if not isinstance(parent_id, str) or parent_id not in record_ids:
                state.error(
                    "RECONCILIATION_INCOMPLETE",
                    root_id=str(record["root_id"]),
                    relative_path=str(record["relative_path"]),
                    operation="validate_record_parent",
                    reason_codes=("missing_parent_record",),
                )
            else:
                children_by_parent[parent_id] = children_by_parent.get(parent_id, 0) + 1
        elif parent_id is not None:
            state.error(
                "RECONCILIATION_INCOMPLETE",
                root_id=str(record["root_id"]),
                relative_path=str(record["relative_path"]),
                operation="validate_record_parent",
                reason_codes=("unexpected_parent_record",),
            )
    for record in state.records:
        expected = int(record["parse"].get("child_count") or 0)
        observed = children_by_parent.get(str(record["record_id"]), 0)
        if expected != observed:
            state.error(
                "RECONCILIATION_INCOMPLETE",
                root_id=str(record["root_id"]),
                relative_path=str(record["relative_path"]),
                operation="validate_child_count",
                reason_codes=("child_count_mismatch",),
            )
    relationship_ids: set[str] = set()
    for relationship in state.relationships:
        relationship["reason_codes"] = sorted(
            set(str(value) for value in relationship.get("reason_codes", ()) if value)
        )
        relationship_type = relationship.get("relationship_type")
        if relationship.get("status") == "valid":
            for field in ("from_ref_sha256", "to_ref_sha256"):
                relationship.pop(field, None)
            if relationship_type not in _GRAPH_EDGE_TYPES | {"notion_session_page"}:
                relationship.pop("source_record_id", None)
            relationship_identity = {
                "relationship_type": relationship_type,
                "from_record_id": relationship.get("from_record_id"),
                "to_record_id": relationship.get("to_record_id"),
            }
        else:
            relationship_identity = {
                key: relationship.get(key)
                for key in (
                    "relationship_type",
                    "source_record_id",
                    "from_record_id",
                    "to_record_id",
                    "from_ref_sha256",
                    "to_ref_sha256",
                    "status",
                )
                if relationship.get(key) is not None
            }
        relationship_id = "rel_" + sha256_canonical(relationship_identity)[:24]
        relationship["relationship_id"] = relationship_id
        if relationship_id in relationship_ids:
            state.error(
                "RECONCILIATION_INCOMPLETE",
                operation="validate_relationship_identity",
                reason_codes=("duplicate_or_missing_relationship_id",),
            )
        relationship_ids.add(relationship_id)
        for field in ("from_record_id", "source_record_id", "to_record_id"):
            value = relationship.get(field)
            if value is not None and str(value) not in record_ids:
                state.error(
                    "RECONCILIATION_INCOMPLETE",
                    operation="validate_relationship_endpoint",
                    reason_codes=(f"missing_{field}",),
                )
    return {
        "classification_authority_counts": {key: authority_counts[key] for key in sorted(authority_counts)},
        "final_plan_required": authority_counts.get("planner_required", 0) > 0,
        "proposed_classified_records": sum(terminal_counts.values()),
        "proposed_terminal_classification_counts": terminal_counts,
        "record_kind_counts": {key: record_kind_counts[key] for key in sorted(record_kind_counts)},
        "stage": "inventory_proposal",
        "total_records": len(state.records),
        "unclassified_records": len(state.records) - sum(terminal_counts.values()),
    }


def _sort_report(report: dict[str, Any]) -> dict[str, Any]:
    report["records"].sort(key=lambda row: (row["category"], row["owner"]["kind"], row["owner"].get("user_id") or "", row["root_id"], row["path_key"], row["source_ordinal"], row["record_id"]))
    report["relationships"].sort(key=lambda row: (row["relationship_type"], row.get("from_record_id", row.get("from_ref_sha256", "")), row.get("to_record_id", row.get("to_ref_sha256", "")), row["relationship_id"]))
    report["errors"].sort(key=lambda row: (row["code"], row["root_id"], row["relative_path"], row["operation"]))
    report["exclusions"].sort(key=lambda row: (row["reason_code"], row["root_id"], row["relative_path"]))
    report["source_snapshot"]["roots"].sort(key=lambda row: row["root_id"])
    report["source_snapshot"]["high_water_marks"].sort(key=lambda row: row["root_id"])
    return json_safe(report)


def _base_report(snapshot_id: str, started_at: str) -> dict[str, Any]:
    return {
        "categories": empty_category_rows(),
        "code_coverage": {"status": "skipped"},
        "complete": False,
        "errors": [],
        "exclusions": [],
        "limitations": [
            {"code": "BROWSER_PINS_UNAVAILABLE", "category": "unavailable.browser_pins"},
            {
                "code": "MARKDOWN_ASSET_SYNTAX_SEALED_TO_KNOWN_PRODUCER",
                "category": "knowledge.wiki_assets",
                "syntax": "known_producer_inline_markdown_v1",
            },
        ],
        "manifest_sha256": None,
        "owners": [],
        "reconciliation": {
            "classification_authority_counts": {},
            "final_plan_required": False,
            "proposed_classified_records": 0,
            "proposed_terminal_classification_counts": {key: 0 for key in sorted(TERMINAL_CLASSIFICATIONS)},
            "record_kind_counts": {},
            "stage": "inventory_proposal",
            "total_records": 0,
            "unclassified_records": 0,
        },
        "records": [],
        "relationships": [],
        "report_schema": REPORT_SCHEMA,
        "run": {"completed_at_utc": None, "run_id": None, "started_at_utc": started_at},
        "runtime": {
            "app_revision": None,
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "sqlite_source_id": _sqlite_source_id(),
            "sqlite_version": sqlite3.sqlite_version,
        },
        "schema_version": SCHEMA_VERSION,
        "source_snapshot": {
            "attestation_sha256": None,
            "created_at_utc": None,
            "high_water_marks": [],
            "kind": None,
            "roles": [],
            "root_binding_sha256": None,
            "roots": [],
            "scratch_root": None,
            "snapshot_id": snapshot_id,
        },
        "summary": {"account_count": 0, "bytes": 0, "categories": len(CATEGORY_IDS), "errors": 0, "job_states": {}, "owners": 0, "records": 0, "relationships": 0, "user_count": 0, "warnings": 0},
        "tool": {"code_revision": _tool_code_revision(), "code_tree_sha256": None, "mapping_sha256": None, "name": TOOL_NAME, "version": TOOL_VERSION},
    }


def inventory_snapshot(
    legacy_root: str | os.PathLike[str],
    knowledge_roots: Sequence[str | os.PathLike[str]],
    snapshot_id: str,
    *,
    runtime_root: str | os.PathLike[str] | None = None,
    user_data_root: str | os.PathLike[str] | None = None,
    sessions_root: str | os.PathLike[str] | None = None,
    workspace_root: str | os.PathLike[str] | None = None,
    allow_partial: bool = False,
    clock: Clock | None = None,
    code_coverage_provider: CodeCoverageProvider | None = None,
    root_probe: RootProbe | None = None,
    scratch_root: str | os.PathLike[str] | None = None,
    snapshot_kind: str = "frozen_copy",
    snapshot_created_at_utc: str | None = None,
    snapshot_attestation_sha256: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Inventory frozen legacy roots and return ``(report, exit_code)``.

    ``clock``, ``code_coverage_provider``, and ``root_probe`` are injectable so
    unit tests never need live data, an application import, or a real volume.
    """
    selected_clock = clock or _default_clock
    selected_probe = root_probe or _default_root_probe
    started = _timestamp(selected_clock)
    report = _base_report(str(snapshot_id), started)
    state = _State()
    state.root_probe = selected_probe
    if not str(snapshot_id).strip():
        state.error("CLI_INVALID_ARGUMENT", operation="validate_snapshot_id")
    canonical_snapshot_time = _validated_utc_timestamp(snapshot_created_at_utc)
    if snapshot_kind not in _SNAPSHOT_KINDS:
        state.error(
            "CLI_INVALID_ARGUMENT",
            operation="validate_snapshot_attestation",
            reason_codes=("invalid_snapshot_kind",),
        )
    if canonical_snapshot_time is None:
        state.error(
            "CLI_INVALID_ARGUMENT",
            operation="validate_snapshot_attestation",
            reason_codes=("invalid_snapshot_created_at",),
        )
    elif canonical_snapshot_time > started:
        state.error(
            "CLI_INVALID_ARGUMENT",
            operation="validate_snapshot_attestation",
            reason_codes=("snapshot_created_after_inventory_start",),
        )
    if not isinstance(snapshot_attestation_sha256, str) or _LOWER_SHA256_RE.fullmatch(snapshot_attestation_sha256) is None:
        state.error(
            "CLI_INVALID_ARGUMENT",
            operation="validate_snapshot_attestation",
            reason_codes=("invalid_snapshot_attestation_sha256",),
        )
    report["source_snapshot"].update(
        {
            "attestation_sha256": snapshot_attestation_sha256,
            "created_at_utc": canonical_snapshot_time,
            "kind": snapshot_kind,
        }
    )
    report["run"]["run_id"] = "run_" + sha256_canonical(
        {
            "attestation_sha256": snapshot_attestation_sha256,
            "snapshot_id": str(snapshot_id),
            "snapshot_kind": snapshot_kind,
            "started_at_utc": started,
        }
    )[:24]

    if scratch_root is None:
        state.error(
            "ROOT_MISSING",
            root_id="scratch",
            operation="validate_scratch_root",
            reason_codes=("explicit_scratch_root_required",),
        )
        scratch = None
        scratch_fact = None
    else:
        scratch, scratch_fact = _validate_one_root(
            "scratch", "scratch", scratch_root, "explicit", selected_probe, state
        )
        if scratch is not None:
            state.scratch_root = scratch.resolved
            state.scratch_descriptor = scratch
            state.scratch_fact = scratch_fact
        report["source_snapshot"]["scratch_root"] = scratch_fact

    requested: list[tuple[str, str, str | os.PathLike[str], str]] = [("legacy", "legacy", legacy_root, "explicit")]
    for index, root in enumerate(knowledge_roots):
        requested.append((f"knowledge_{index:03d}", "knowledge", root, "explicit"))
    legacy_raw = Path(os.fsdecode(os.fspath(legacy_root)))
    derived = (
        ("runtime", "runtime", runtime_root, ".runtime"),
        ("user_data", "user_data", user_data_root, "user_data"),
        ("sessions", "sessions", sessions_root, "sessions"),
        ("workspace", "workspace", workspace_root, "workspace"),
        ("logs", "logs", None, "logs"),
    )
    for root_id, role, explicit, child in derived:
        if explicit is not None:
            requested.append((root_id, role, explicit, "explicit"))
        embedded = legacy_raw / child
        if legacy_raw.is_absolute() and embedded.is_dir():
            explicit_normalized = (
                os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(explicit))))
                if explicit is not None
                else None
            )
            if explicit_normalized != os.path.normcase(os.path.abspath(os.fspath(embedded))):
                requested.append(
                    (root_id if explicit is None else f"{root_id}_embedded", role, embedded, "derived")
                )
    for child, root_id in (("knowledge", "knowledge_embedded"), ("user_knowledge", "knowledge_user_embedded")):
        embedded = legacy_raw / child
        if not legacy_raw.is_absolute() or not embedded.is_dir():
            continue
        embedded_key = os.path.normcase(os.path.abspath(os.fspath(embedded)))
        explicit_keys = {
            os.path.normcase(os.path.abspath(os.fsdecode(os.fspath(value))))
            for value in knowledge_roots
        }
        if embedded_key not in explicit_keys:
            requested.append((root_id, "knowledge", embedded, "derived"))

    roots: list[_Root] = []
    root_facts = []
    for root_id, role, value, source in requested:
        root, fact = _validate_one_root(root_id, role, value, source, selected_probe, state)
        if root is not None and fact is not None:
            roots.append(root)
            root_facts.append(fact)

    seen: dict[str, str] = {}
    for root in roots:
        key = os.path.normcase(str(root.resolved))
        if key in seen:
            state.error("ROOT_DUPLICATE", root_id=root.root_id, operation="validate_roots", reason_codes=(seen[key],))
        seen[key] = root.root_id
    physical = [root for root in roots if root.role != "legacy"]
    if state.scratch_root is not None:
        for root in roots:
            if _contains(root.resolved, state.scratch_root) or _contains(state.scratch_root, root.resolved):
                state.error(
                    "ROOT_OVERLAP",
                    root_id="scratch",
                    operation="validate_scratch_root",
                    reason_codes=(root.root_id,),
                )
    for index, left in enumerate(physical):
        for right in physical[index + 1 :]:
            if _contains(left.resolved, right.resolved) or _contains(right.resolved, left.resolved):
                state.error("ROOT_OVERLAP", root_id=right.root_id, operation="validate_roots", reason_codes=(left.root_id,))

    state.source_roots = list(physical)
    report["source_snapshot"]["roots"] = root_facts
    required_roles = ("legacy", "knowledge", "runtime", "sessions", "user_data", "workspace")
    report["source_snapshot"]["roles"] = [
        {
            "role": role,
            "root_ids": sorted(root.root_id for root in roots if root.role == role),
            "status": "present" if any(root.role == role for root in roots) else "missing",
        }
        for role in required_roles
    ]
    report["source_snapshot"]["root_binding_sha256"] = sha256_canonical(
        {
            "attestation_sha256": snapshot_attestation_sha256,
            "created_at_utc": canonical_snapshot_time,
            "kind": snapshot_kind,
            "roots": sorted(root_facts, key=lambda fact: str(fact["root_id"])),
            "scratch_root": scratch_fact,
            "snapshot_id": str(snapshot_id),
        }
    )
    for role in required_roles:
        if not any(root.role == role for root in roots):
            state.error(
                "ROOT_MISSING",
                root_id=role,
                operation="validate_required_role",
                reason_codes=("source_role_not_declared",),
            )
    for role in ("runtime", "sessions", "user_data", "workspace"):
        role_roots = [root for root in roots if root.role == role]
        if len(role_roots) > 1:
            state.error(
                "ROOT_DUPLICATE",
                root_id=role,
                operation="validate_required_role",
                reason_codes=("multiple_role_roots",),
            )

    fatal_precondition = bool(state.errors)
    if not fatal_precondition:
        legacy = next(root for root in roots if root.role == "legacy")
        report["code_coverage"] = _load_code_coverage(legacy.configured, code_coverage_provider, state)
        code_before = _code_high_water(report["code_coverage"])
        report["tool"]["code_tree_sha256"] = code_before["tree_sha256"]
        source_fingerprint = report["code_coverage"].get("source_fingerprint_sha256")
        report["runtime"]["app_revision"] = (
            source_fingerprint if isinstance(source_fingerprint, str) else code_before["tree_sha256"]
        )
        mapping = report["code_coverage"].get("mapping")
        if isinstance(mapping, Mapping) and isinstance(mapping.get("sha256"), str):
            report["tool"]["mapping_sha256"] = mapping["sha256"]
        _record_legacy_exclusions(legacy, physical, state)
        before = {root.root_id: _capture(root, state, "snapshot_before") for root in physical}
        scan_order = {"runtime": 0, "user_data": 1, "knowledge": 2, "sessions": 3, "workspace": 4, "logs": 5}
        for root in sorted(physical, key=lambda item: (scan_order.get(item.role, 99), item.root_id)):
            try:
                _observe_root_fact(root, selected_probe)
            except OSError:
                state.error(
                    "SOURCE_CHANGED",
                    root_id=root.root_id,
                    operation="verify_root_before_scan",
                    reason_codes=("root_identity_changed",),
                )
                continue
            _scan_root(root, state)
        for snapshot_key, snapshot_sha256 in sorted(state.sqlite_snapshot_hashes.items()):
            if state.scan_hashes.get(snapshot_key) != snapshot_sha256:
                state.error(
                    "SQLITE_SNAPSHOT_INCONSISTENT",
                    root_id=snapshot_key[0],
                    relative_path=snapshot_key[1],
                    operation="bind_sqlite_snapshot_to_inventory",
                    reason_codes=("sqlite_component_not_in_scan",),
                )
        _reconcile_assets(state)
        _reconcile_structural_references(state)

        # Browser-only pins had no persisted legacy source.  Keeping the
        # unavailable fact in the same record ledger makes the proposal ratio
        # and category totals genuinely complete.
        legacy = next(root for root in roots if root.role == "legacy")
        _new_unavailable_record(
            legacy,
            "@unavailable/browser-pins",
            "unavailable.browser_pins",
            _owner("system"),
            state,
            reason_code="browser_pins_not_persisted",
        )
        after = {root.root_id: _capture(root, state, "snapshot_after") for root in physical}
        verification_state = _State()
        code_after_report = _load_code_coverage(
            legacy.configured, code_coverage_provider, verification_state
        )
        code_after = _code_high_water(code_after_report)
        state.errors.extend(verification_state.errors)
        code_changed = code_before != code_after
        report["source_snapshot"]["high_water_marks"].append(
            {
                "after": code_after,
                "before": code_before,
                "changed": code_changed,
                "root_id": legacy.root_id,
            }
        )
        if code_changed:
            state.error("SOURCE_CHANGED", root_id=legacy.root_id, operation="verify_code_snapshot")
        for root in physical:
            changed = before[root.root_id] != after[root.root_id]
            report["source_snapshot"]["high_water_marks"].append({"after": after[root.root_id], "before": before[root.root_id], "changed": changed, "root_id": root.root_id})
            if changed:
                state.error("SOURCE_CHANGED", root_id=root.root_id, operation="verify_snapshot")

        # The approved source paths/final targets must still be the same after
        # all reads.  Any reparse or final-path change is a hard source gate.
        initial_facts = {str(fact["root_id"]): fact for fact in root_facts}
        for root in roots:
            _, final_fact = _validate_one_root(
                root.root_id,
                root.role,
                root.configured,
                root.path_source,
                selected_probe,
                state,
            )
            if final_fact != initial_facts.get(root.root_id):
                state.error(
                    "SOURCE_CHANGED",
                    root_id=root.root_id,
                    operation="verify_final_root",
                    reason_codes=("root_identity_changed",),
                )
        if scratch is not None and scratch_fact is not None:
            _, final_scratch_fact = _validate_one_root(
                scratch.root_id,
                scratch.role,
                scratch.configured,
                scratch.path_source,
                selected_probe,
                state,
            )
            if final_scratch_fact != scratch_fact:
                state.error(
                    "SOURCE_CHANGED",
                    root_id="scratch",
                    operation="verify_final_scratch_root",
                    reason_codes=("scratch_root_identity_changed",),
                )

    report["records"] = state.records
    report["relationships"] = state.relationships
    report["exclusions"] = state.exclusions
    report["reconciliation"] = _finalize_reconciliation(state)
    state.errors = _deduplicate_errors(state.errors)
    report["errors"] = state.errors
    report["categories"] = _category_rows(state.records, state.errors)
    error_count = sum(row["severity"] == "error" for row in state.errors)
    warning_count = sum(row["severity"] != "error" for row in state.errors)
    report["complete"] = error_count == 0
    job_states: dict[str, int] = {}
    for row in state.records:
        if row["category"] == "operations.jobs" and row["entry_type"] == "file":
            status = str(row["parse"].get("source_status") or "unknown")
            job_states[status] = job_states.get(status, 0) + 1
    owner_sources: dict[tuple[str, str | None], dict[str, Any]] = {}
    for user_id, account in sorted(state.registry_accounts.items()):
        owner_sources[("personal", user_id)] = {
            "account_status": account["account_status"],
            "claimed_user_ids": [],
            "kind": "personal",
            "mapping_status": "confirmed",
            "record_count": 0,
            "registry_record_id": account["registry_record_id"],
            "role": account["role"],
            "source_paths": [],
            "user_id": user_id,
        }
    for row in state.records:
        owner = row["owner"]
        canonical_user_id = owner.get("user_id")
        key = (str(owner["kind"]), canonical_user_id)
        fact = owner_sources.setdefault(
            key,
            {
                "account_status": None,
                "claimed_user_ids": [],
                "kind": owner["kind"],
                "mapping_status": owner["mapping_status"],
                "record_count": 0,
                "registry_record_id": None,
                "role": None,
                "source_paths": [],
                "user_id": canonical_user_id,
            },
        )
        claimed = owner.get("claimed_user_id")
        if claimed is not None and claimed not in fact["claimed_user_ids"]:
            fact["claimed_user_ids"].append(claimed)
        fact["record_count"] += 1
        if row["entry_type"] == "file":
            fact["source_paths"].append(
                {"category": row["category"], "path_key": row["path_key"], "root_id": row["root_id"]}
            )
    report["owners"] = sorted(
        (
            {
                **fact,
                "claimed_user_ids": sorted(fact["claimed_user_ids"], key=lambda value: (_owner_key(value), value)),
                "source_paths": sorted(
                    fact["source_paths"], key=lambda item: (item["root_id"], item["path_key"], item["category"])
                ),
            }
            for fact in owner_sources.values()
        ),
        key=lambda item: (item["kind"], item.get("user_id") or "", tuple(item["claimed_user_ids"])),
    )
    report["summary"] = {
        "bytes": sum(int(row["size_bytes"] or 0) for row in state.records),
        "categories": len(CATEGORY_IDS),
        "errors": error_count,
        "job_states": {key: job_states[key] for key in sorted(job_states)},
        "account_count": len(state.registry_accounts),
        "owners": len(report["owners"]),
        "records": len(state.records),
        "relationships": len(state.relationships),
        "warnings": warning_count,
        "user_count": len(state.registry_accounts),
    }
    report["run"]["completed_at_utc"] = _timestamp(selected_clock)
    report = _sort_report(report)
    report["manifest_sha256"] = manifest_sha256(report)
    contract_errors = inventory_contract_errors(report)
    if contract_errors:
        report["errors"].append(
            {
                "code": "RECONCILIATION_INCOMPLETE",
                "operation": "validate_report_contract",
                "reason_codes": list(contract_errors),
                "relative_path": "",
                "root_id": "",
                "severity": "error",
            }
        )
        report["errors"] = _deduplicate_errors(report["errors"])
        report["complete"] = False
        report["summary"]["errors"] = sum(
            row["severity"] == "error" for row in report["errors"]
        )
        report = _sort_report(report)
        report["manifest_sha256"] = manifest_sha256(report)
    hard_failure = has_hard_errors(report["errors"])
    exit_code = 0 if not hard_failure and (report["complete"] or allow_partial) else 2
    return report, exit_code


@dataclass(frozen=True)
class _OutputGuard:
    parent: Path
    parent_resolved: Path
    filesystem_identity: tuple[int, int]
    source_roots: tuple[Path, ...]


def _observe_output_parent(parent: Path) -> tuple[Path, tuple[int, int]]:
    observation = _default_root_probe(str(parent))
    reparse = (
        observation.get("reparse")
        if isinstance(observation.get("reparse"), Mapping)
        else {}
    )
    if (
        observation.get("eligible") is not True
        or observation.get("exists") is not True
        or reparse.get("present") is True
    ):
        raise OSError("unsafe_output_parent")
    stat = parent.stat(follow_symlinks=False)
    if _is_reparse(parent, stat) or not parent.is_dir():
        raise OSError("unsafe_output_parent")
    final = observation.get("resolved_path")
    resolved = Path(str(final) if final else parent).resolve(strict=True)
    return resolved, (int(stat.st_dev), int(stat.st_ino))


def _prepare_output_guard(
    output: str,
    replace: bool,
    roots: Sequence[str | os.PathLike[str]],
) -> tuple[_OutputGuard | None, str | None]:
    if output == "-":
        return None, None
    if not os.path.isabs(output) or _is_unc(output):
        return None, "CLI_INVALID_ARGUMENT"
    path = Path(output)
    try:
        _validate_simple_windows_component(path.name)
    except OSError:
        return None, "CLI_INVALID_ARGUMENT"
    if not path.parent.is_dir():
        return None, "OUTPUT_PARENT_MISSING"
    try:
        parent_resolved, identity = _observe_output_parent(path.parent)
    except OSError:
        return None, "OUTPUT_PARENT_INELIGIBLE"
    source_roots: list[Path] = []
    for value in roots:
        try:
            root = Path(value).resolve(strict=True)
        except (OSError, ValueError):
            continue
        source_roots.append(root)
        if _contains(root, parent_resolved) or _contains(parent_resolved, root):
            return None, "OUTPUT_INSIDE_SOURCE"
    if path.exists() and not replace:
        return None, "OUTPUT_EXISTS"
    if path.exists():
        try:
            existing_stat = path.stat(follow_symlinks=False)
            if _is_reparse(path, existing_stat) or not path.is_file():
                return None, "OUTPUT_WRITE_FAILED"
            resolved_existing = path.resolve(strict=True)
            if not _contains(parent_resolved, resolved_existing):
                return None, "OUTPUT_WRITE_FAILED"
        except OSError:
            return None, "OUTPUT_WRITE_FAILED"
    return (
        _OutputGuard(
            parent=path.parent,
            parent_resolved=parent_resolved,
            filesystem_identity=identity,
            source_roots=tuple(source_roots),
        ),
        None,
    )


def _validate_output(output: str, replace: bool, roots: Sequence[str | os.PathLike[str]]) -> str | None:
    """Compatibility wrapper for the public CLI's preflight error contract."""
    _, error = _prepare_output_guard(output, replace, roots)
    return error


def _assert_output_guard(guard: _OutputGuard) -> None:
    resolved, identity = _observe_output_parent(guard.parent)
    if resolved != guard.parent_resolved or identity != guard.filesystem_identity:
        raise OSError("output_parent_identity_changed")
    if any(
        _contains(root, resolved) or _contains(resolved, root)
        for root in guard.source_roots
    ):
        raise OSError("output_parent_overlap_changed")


def _write_stdout(value: Mapping[str, Any]) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(payload)
        buffer.flush()
    else:  # StringIO used by unit tests
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()


def _flush_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flush = kernel32.FlushFileBuffers
    flush.argtypes = (wintypes.HANDLE,)
    flush.restype = wintypes.BOOL
    if not flush(wintypes.HANDLE(handle)):
        raise ctypes.WinError(ctypes.get_last_error())


def _write_windows_handle(
    handle: int, payload: bytes, *, flush: bool = True
) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    write_file.restype = wintypes.BOOL
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + _BUFFER_SIZE]
        buffer = ctypes.create_string_buffer(chunk)
        written = wintypes.DWORD()
        if not write_file(
            wintypes.HANDLE(handle),
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if written.value != len(chunk):
            raise OSError("short_output_write")
        offset += written.value
    if flush:
        _flush_windows_handle(handle)


def _write_file_in_pinned_parent(
    path: Path,
    report: Mapping[str, Any],
    replace: bool,
    guard: _OutputGuard,
    anchor: _WindowsDirectoryAnchor,
) -> None:
    _assert_output_guard(guard)
    final_name = _validate_simple_windows_component(path.name)
    if path.exists() and not replace:
        raise FileExistsError
    temporary_name = ".legacy-inventory-" + secrets.token_hex(16) + ".tmp"
    handle = anchor.create_relative_file(temporary_name)
    committed = False
    try:
        # A delete-pending file cannot be hard-linked or renamed through its
        # path while bytes are being written.  Clear the disposition only for
        # the atomic same-directory handle rename.
        _nt_set_disposition(handle)
        _assert_output_guard(guard)
        _write_windows_handle(handle, canonical_json_bytes(report) + b"\n")
        _assert_output_guard(guard)
        _nt_clear_disposition(handle)
        _nt_rename_relative(
            handle,
            anchor.handle,
            final_name,
            replace=replace,
        )
        committed = True
        _assert_output_guard(guard)
        if os.path.normcase(str(_windows_final_path(handle))) != os.path.normcase(
            str(path.resolve(strict=True))
        ):
            raise OSError("output_final_path_changed")
    except Exception as original:
        cleanup_error: Exception | None = None
        if not committed:
            try:
                _nt_set_disposition(handle)
            except Exception as exc:
                cleanup_error = exc
        try:
            _close_windows_handle(handle)
        except Exception as exc:
            cleanup_error = cleanup_error or exc
        if cleanup_error is not None:
            raise cleanup_error from original
        raise
    else:
        _close_windows_handle(handle)


def _write_file(
    path: Path,
    report: Mapping[str, Any],
    replace: bool,
    guard: _OutputGuard,
) -> None:
    _assert_output_guard(guard)
    with _pinned_directory(guard.parent) as anchor:
        # If the path was exchanged before CreateFileW acquired the pin, this
        # second observation detects it.  Once it matches, the denied delete
        # share keeps the approved parent identity stable through creation.
        _assert_output_guard(guard)
        _write_file_in_pinned_parent(path, report, replace, guard, anchor)


def _parser() -> argparse.ArgumentParser:
    parser = _InventoryArgumentParser(prog="legacy_inventory", add_help=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inventory = subparsers.add_parser("inventory", add_help=True)
    inventory.add_argument("--legacy-root", required=True)
    inventory.add_argument("--knowledge-root", action="append", required=True)
    inventory.add_argument("--snapshot-id", required=True)
    inventory.add_argument("--snapshot-kind", choices=sorted(_SNAPSHOT_KINDS), required=True)
    inventory.add_argument("--snapshot-created-at-utc", required=True)
    inventory.add_argument("--snapshot-attestation-sha256", required=True)
    inventory.add_argument("--output", required=True)
    inventory.add_argument("--runtime-root")
    inventory.add_argument("--user-data-root")
    inventory.add_argument("--sessions-root")
    inventory.add_argument("--workspace-root")
    inventory.add_argument("--scratch-root", required=True)
    inventory.add_argument("--allow-partial", action="store_true")
    inventory.add_argument("--replace-output", action="store_true")
    return parser


def _error_report(code: str) -> dict[str, Any]:
    now = _timestamp(_default_clock)
    report = _base_report("", now)
    report["run"]["completed_at_utc"] = now
    report["errors"] = [{"code": code, "operation": "cli", "reason_codes": [], "relative_path": "", "root_id": "", "severity": "error"}]
    report["summary"]["errors"] = 1
    report = _sort_report(report)
    report["manifest_sha256"] = manifest_sha256(report)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
    except ValueError:
        _write_stdout(_error_report("CLI_INVALID_ARGUMENT"))
        return 2
    except SystemExit as exc:
        return int(exc.code or 0)
    all_roots = [arguments.legacy_root, *arguments.knowledge_root]
    all_roots.extend(value for value in (arguments.runtime_root, arguments.user_data_root, arguments.sessions_root, arguments.workspace_root) if value)
    all_roots.append(arguments.scratch_root)
    output_guard, output_error = _prepare_output_guard(
        arguments.output, arguments.replace_output, all_roots
    )
    if output_error:
        _write_stdout(_error_report(output_error))
        return 3 if output_error in {
            "OUTPUT_PARENT_MISSING",
            "OUTPUT_WRITE_FAILED",
        } else 2
    try:
        report, exit_code = inventory_snapshot(
            arguments.legacy_root,
            arguments.knowledge_root,
            arguments.snapshot_id,
            snapshot_kind=arguments.snapshot_kind,
            snapshot_created_at_utc=arguments.snapshot_created_at_utc,
            snapshot_attestation_sha256=arguments.snapshot_attestation_sha256,
            runtime_root=arguments.runtime_root,
            user_data_root=arguments.user_data_root,
            sessions_root=arguments.sessions_root,
            workspace_root=arguments.workspace_root,
            scratch_root=arguments.scratch_root,
            allow_partial=arguments.allow_partial,
        )
    except Exception:
        _write_stdout(_error_report("CLI_INVALID_ARGUMENT"))
        return 2
    if arguments.output == "-":
        _write_stdout(report)
        return exit_code
    try:
        if output_guard is None:
            raise OSError("output_guard_missing")
        _write_file(
            Path(arguments.output),
            report,
            arguments.replace_output,
            output_guard,
        )
    except Exception:
        _write_stdout(_error_report("OUTPUT_WRITE_FAILED"))
        return 3
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["inventory_snapshot", "main"]
