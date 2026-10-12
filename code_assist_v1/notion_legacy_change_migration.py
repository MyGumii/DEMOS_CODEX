"""Guarded installation of shard-local legacy Notion change watermarks.

The migration is never called by application startup or request handling.  It
installs coalescing SQLite triggers in one explicitly named owner shard.  The
watermark payload contains only page identity, a monotonic generation, a
coarse bit mask, timestamp, and active/tombstone state; titles, content, file
names/paths, and public bearer tokens never enter it.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable
import uuid


PLAN_VERSION = "notion.legacy-change-tracking.plan.v1"
MIGRATION_VERSION = 2026081301
MIGRATION_NAME = "notion_legacy_change_tracking_v1"
CHANGE_PAGE = 1
CHANGE_BLOCK = 2
CHANGE_FILE = 4
CHANGE_PUBLIC = 8
CHANGE_SHARE = 16
CHANGE_ALL = 31
_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
_MAX_GENERATION = 9223372036854775806


class LegacyChangeMigrationRefused(Exception):
    """A sanitized fail-closed migration refusal."""


def _close_path_pins(handles: list[int]) -> None:
    if os.name != "nt":
        return
    import ctypes

    close_handle = ctypes.windll.kernel32.CloseHandle
    for handle in reversed(handles):
        try:
            close_handle(handle)
        except Exception:
            pass


def _windows_final_path(handle: int) -> Path:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    size = kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not size:
        raise LegacyChangeMigrationRefused("secure migration path pin failed")
    buffer = ctypes.create_unicode_buffer(size + 1)
    if not kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
        raise LegacyChangeMigrationRefused("secure migration path pin failed")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _pin_windows_path(
    path: Path,
    *,
    directory: bool,
    allow_delete: bool = False,
    share_write: bool = True,
) -> int:
    """Open a non-reparse handle without FILE_SHARE_DELETE.

    Holding every component handle prevents rename/delete substitution while a
    pathname-based SQLite connection is live.
    """
    if os.name != "nt":
        raise LegacyChangeMigrationRefused(
            "secure migration path pinning is unavailable on this platform"
        )
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
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
    file_read_attributes = 0x0080 | 0x00020000  # READ_ATTRIBUTES | READ_CONTROL
    share_read_write = (
        0x00000001
        | (0x00000002 if share_write else 0)
        | (0x00000004 if allow_delete else 0)
    )
    open_existing = 3
    open_reparse_point = 0x00200000
    backup_semantics = 0x02000000 if directory else 0
    handle = create_file(
        os.fspath(path),
        file_read_attributes,
        share_read_write,
        None,
        open_existing,
        open_reparse_point | backup_semantics,
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, 0, invalid):
        raise LegacyChangeMigrationRefused("secure migration path pin failed")
    attributes = kernel32.GetFileAttributesW(os.fspath(path))
    if attributes == 0xFFFFFFFF or attributes & 0x00000400:
        kernel32.CloseHandle(handle)
        raise LegacyChangeMigrationRefused("migration path contains a link")
    try:
        final = _windows_final_path(handle).resolve(strict=True)
        if final != path.resolve(strict=True):
            raise LegacyChangeMigrationRefused("secure migration path pin failed")
        return int(handle)
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def _windows_security_metadata(
    handle: int,
) -> tuple[str, int, tuple[int, int, int]]:
    """Return canonical owner/group/DACL/label and security-relevant attrs."""
    if os.name != "nt":
        raise LegacyChangeMigrationRefused(
            "secure migration metadata inspection is unavailable"
        )
    import ctypes
    from ctypes import wintypes

    security_information = 0x00000001 | 0x00000002 | 0x00000004 | 0x00000010
    descriptor = ctypes.c_void_p()
    result = ctypes.windll.advapi32.GetSecurityInfo(
        wintypes.HANDLE(handle),
        1,  # SE_FILE_OBJECT
        security_information,
        None,
        None,
        None,
        None,
        ctypes.byref(descriptor),
    )
    if result != 0 or not descriptor.value:
        raise LegacyChangeMigrationRefused("file security metadata is unavailable")
    string_pointer = wintypes.LPWSTR()
    try:
        if not ctypes.windll.advapi32.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor,
            1,
            security_information,
            ctypes.byref(string_pointer),
            None,
        ):
            raise LegacyChangeMigrationRefused("file security metadata is unavailable")
        sddl = str(string_pointer.value or "")
        if not sddl:
            raise LegacyChangeMigrationRefused("file security metadata is unavailable")
    finally:
        if string_pointer:
            ctypes.windll.kernel32.LocalFree(string_pointer)
        ctypes.windll.kernel32.LocalFree(descriptor)

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    information = ByHandleFileInformation()
    if not ctypes.windll.kernel32.GetFileInformationByHandle(
        wintypes.HANDLE(handle), ctypes.byref(information)
    ):
        raise LegacyChangeMigrationRefused("file security metadata is unavailable")
    security_attribute_mask = (
        0x00000001  # READONLY
        | 0x00000002  # HIDDEN
        | 0x00000004  # SYSTEM
        | 0x00000200  # SPARSE_FILE
        | 0x00000400  # REPARSE_POINT
        | 0x00000800  # COMPRESSED
        | 0x00001000  # OFFLINE
        | 0x00004000  # ENCRYPTED
        | 0x00008000  # INTEGRITY_STREAM
        | 0x00020000  # NO_SCRUB_DATA
    )
    file_index = (int(information.nFileIndexHigh) << 32) | int(
        information.nFileIndexLow
    )
    return (
        sddl,
        int(information.dwFileAttributes) & security_attribute_mask,
        (
            int(information.dwVolumeSerialNumber),
            file_index,
            int(information.nNumberOfLinks),
        ),
    )


def _create_pinned_windows_file(
    path: Path,
    *,
    parent_handle: int,
    exclusive: bool = False,
) -> int:
    """Atomically create a file relative to an already opened directory.

    Native relative creation is the important property: even if the pathname
    is concurrently replaced, the new file is created in the verified directory
    object represented by ``parent_handle`` and never through the replacement.
    """
    if os.name != "nt":
        raise LegacyChangeMigrationRefused(
            "secure migration path pinning is unavailable on this platform"
        )
    import ctypes
    from ctypes import wintypes

    name = path.name
    reserved_stems = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    stem = name.split(".", 1)[0].upper()
    if (
        not name
        or name in {".", ".."}
        or len(name.encode("utf-16-le")) > 510
        or name[-1:] in {".", " "}
        or stem in reserved_stems
        or any(ord(character) < 32 for character in name)
        or any(character in '<>:"/\\|?*' for character in name)
    ):
        raise LegacyChangeMigrationRefused("valid backup file name required")

    class UnicodeString(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]

    class ObjectAttributes(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.ULONG),
            ("RootDirectory", wintypes.HANDLE),
            ("ObjectName", ctypes.POINTER(UnicodeString)),
            ("Attributes", wintypes.ULONG),
            ("SecurityDescriptor", wintypes.LPVOID),
            ("SecurityQualityOfService", wintypes.LPVOID),
        ]

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    buffer = ctypes.create_unicode_buffer(name)
    unicode_name = UnicodeString(
        len(name.encode("utf-16-le")),
        len(name.encode("utf-16-le")) + 2,
        ctypes.cast(buffer, wintypes.LPWSTR),
    )
    attributes = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes),
        wintypes.HANDLE(parent_handle),
        ctypes.pointer(unicode_name),
        0x00000040 | 0x00001000,  # OBJ_CASE_INSENSITIVE | OBJ_DONT_REPARSE
        None,
        None,
    )
    status_block = IoStatusBlock()
    handle = wintypes.HANDLE()
    nt_create = ctypes.windll.ntdll.NtCreateFile
    nt_create.argtypes = (
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.ULONG,
        ctypes.POINTER(ObjectAttributes),
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        wintypes.ULONG,
        ctypes.c_void_p,
        wintypes.ULONG,
    )
    nt_create.restype = ctypes.c_long
    status = nt_create(
        ctypes.byref(handle),
        0x0012019F | (0x00010000 if exclusive else 0),
        # FILE_GENERIC_READ | FILE_GENERIC_WRITE | SYNCHRONIZE [+ DELETE]
        ctypes.byref(attributes),
        ctypes.byref(status_block),
        None,
        0x00000080,  # FILE_ATTRIBUTE_NORMAL
        0 if exclusive else (0x00000001 | 0x00000002),
        2,  # FILE_CREATE (must not exist)
        0x00000020 | 0x00000040 | 0x00200000,
        # synchronous, non-directory, open-reparse-point
        None,
        0,
    )
    if status < 0 or not handle.value:
        raise LegacyChangeMigrationRefused("backup target must be a new exact path")
    return int(handle.value)


def _write_flush_windows_handle(handle: int, payload: bytes) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    if not kernel32.SetFilePointerEx(wintypes.HANDLE(handle), 0, None, 0):
        raise LegacyChangeMigrationRefused("secure staging write failed")
    if not kernel32.SetEndOfFile(wintypes.HANDLE(handle)):
        raise LegacyChangeMigrationRefused("secure staging write failed")
    offset = 0
    while offset < len(payload):
        chunk = payload[offset : offset + 1024 * 1024]
        written = wintypes.DWORD()
        buffer = ctypes.create_string_buffer(chunk)
        if not kernel32.WriteFile(
            wintypes.HANDLE(handle),
            buffer,
            len(chunk),
            ctypes.byref(written),
            None,
        ) or written.value != len(chunk):
            raise LegacyChangeMigrationRefused("secure staging write failed")
        offset += written.value
    if not kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
        raise LegacyChangeMigrationRefused("secure staging flush failed")


def _flush_windows_handle(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    if not ctypes.windll.kernel32.FlushFileBuffers(wintypes.HANDLE(handle)):
        raise LegacyChangeMigrationRefused(
            "replacement durability flush failed; manual audit required"
        )


def _sha256_windows_handle(handle: int) -> str:
    """Hash an already pinned file without reopening its pathname."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    size = ctypes.c_longlong()
    if not kernel32.GetFileSizeEx(wintypes.HANDLE(handle), ctypes.byref(size)):
        raise LegacyChangeMigrationRefused("verified backup read failed")
    if size.value < 0:
        raise LegacyChangeMigrationRefused("verified backup read failed")
    if not kernel32.SetFilePointerEx(wintypes.HANDLE(handle), 0, None, 0):
        raise LegacyChangeMigrationRefused("verified backup read failed")
    digest = hashlib.sha256()
    remaining = int(size.value)
    while remaining:
        amount = min(remaining, 1024 * 1024)
        buffer = ctypes.create_string_buffer(amount)
        read = wintypes.DWORD()
        if not kernel32.ReadFile(
            wintypes.HANDLE(handle),
            buffer,
            amount,
            ctypes.byref(read),
            None,
        ) or read.value <= 0:
            raise LegacyChangeMigrationRefused("verified backup read failed")
        digest.update(buffer.raw[: read.value])
        remaining -= int(read.value)
    return "sha256:" + digest.hexdigest()


def _dispose_windows_handle_on_close(handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    class FileDispositionInfo(ctypes.Structure):
        _fields_ = [("DeleteFile", wintypes.BOOL)]

    info = FileDispositionInfo(True)
    if not ctypes.windll.kernel32.SetFileInformationByHandle(
        wintypes.HANDLE(handle),
        4,  # FileDispositionInfo
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        raise LegacyChangeMigrationRefused(
            "secure staging cleanup failed; manual audit required"
        )


def _replace_windows_handle_relative(
    handle: int,
    *,
    parent_handle: int,
    target_name: str,
) -> None:
    """Atomically rename an open stage over a sibling using RootDirectory."""
    import ctypes
    from ctypes import wintypes

    if (
        not target_name
        or target_name in {".", ".."}
        or any(separator in target_name for separator in ("/", "\\"))
    ):
        raise LegacyChangeMigrationRefused("valid replacement target required")

    class IoStatusBlock(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_size_t)]

    class FileRenameInfoEx(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("RootDirectory", wintypes.HANDLE),
            ("FileNameLength", wintypes.DWORD),
            ("FileName", wintypes.WCHAR * (len(target_name) + 1)),
        ]

    info = FileRenameInfoEx()
    info.Flags = 0x00000001 | 0x00000002 | 0x00000040
    # REPLACE_IF_EXISTS | POSIX_SEMANTICS | IGNORE_READONLY_ATTRIBUTE
    info.RootDirectory = wintypes.HANDLE(parent_handle)
    info.FileNameLength = len(target_name.encode("utf-16-le"))
    info.FileName = target_name
    iosb = IoStatusBlock()
    nt_set_information = ctypes.windll.ntdll.NtSetInformationFile
    nt_set_information.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(IoStatusBlock),
        ctypes.c_void_p,
        wintypes.ULONG,
        wintypes.ULONG,
    )
    nt_set_information.restype = ctypes.c_long
    status = nt_set_information(
        wintypes.HANDLE(handle),
        ctypes.byref(iosb),
        ctypes.byref(info),
        ctypes.sizeof(info),
        65,  # FileRenameInformationEx
    )
    if status < 0:
        raise LegacyChangeMigrationRefused("atomic source replacement failed")


def _pinned_directory_chain(root: Path, parent: Path) -> list[int]:
    try:
        relative = parent.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, RuntimeError, ValueError) as exc:
        raise LegacyChangeMigrationRefused("migration path escaped its root") from exc
    paths = [root.resolve(strict=True)]
    current = paths[0]
    for part in relative.parts:
        current = current / part
        paths.append(current)
    handles: list[int] = []
    try:
        for path in paths:
            handles.append(_pin_windows_path(path, directory=True))
        return handles
    except Exception:
        _close_path_pins(handles)
        raise


def _acquire_path_pins(
    *,
    root: Path,
    target: Path,
    include_target: bool,
) -> list[int]:
    """Pin every existing component from root through target on Windows."""
    if os.name != "nt":
        raise LegacyChangeMigrationRefused(
            "secure migration path pinning is unavailable on this platform"
        )
    resolved_root = root.resolve(strict=True)
    resolved_target = target.resolve(strict=True) if include_target else target
    parent = resolved_target.parent
    try:
        relative_parent = parent.resolve(strict=True).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LegacyChangeMigrationRefused("migration path escaped its root") from exc
    components = [resolved_root]
    current = resolved_root
    for part in relative_parent.parts:
        current = current / part
        components.append(current)
    handles: list[int] = []
    try:
        for component in components:
            handles.append(_pin_windows_path(component, directory=True))
        if include_target:
            handles.append(_pin_windows_path(resolved_target, directory=False))
        return handles
    except Exception:
        _close_path_pins(handles)
        raise


class _PinnedConnection(sqlite3.Connection):
    _migration_path_pins: list[int] | None = None

    def close(self) -> None:
        pins = self._migration_path_pins
        self._migration_path_pins = None
        try:
            super().close()
        finally:
            if pins:
                _close_path_pins(pins)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _watermark_statement(page_expression: str, mask: int) -> str:
    exists_expression = (
        "CASE WHEN EXISTS(SELECT 1 FROM pages WHERE id="
        + page_expression
        + " AND archived=0) THEN 1 ELSE 0 END"
    )
    return f"""
      INSERT INTO notion_page_change_watermarks(
        page_id,change_generation,change_mask,changed_at,source_exists
      ) VALUES(
        {page_expression},1,{mask},CAST(strftime('%s','now') AS INTEGER),
        {exists_expression}
      )
      ON CONFLICT(page_id) DO UPDATE SET
        change_generation=CASE
          WHEN change_generation>={_MAX_GENERATION}
          THEN RAISE(ABORT,'legacy change generation exhausted')
          ELSE change_generation+1 END,
        change_mask=(change_mask | excluded.change_mask),
        changed_at=excluded.changed_at,
        source_exists=excluded.source_exists
    """.strip()


def _watermark_subtree_statement(page_expression: str, mask: int) -> str:
    """Coalesce a page and every current descendant into the watermark set."""
    return f"""
      INSERT INTO notion_page_change_watermarks(
        page_id,change_generation,change_mask,changed_at,source_exists
      )
      WITH RECURSIVE subtree(id) AS (
        SELECT {page_expression}
        UNION
        SELECT pages.id FROM pages JOIN subtree ON pages.parent_id=subtree.id
      )
      SELECT subtree.id,1,{mask},CAST(strftime('%s','now') AS INTEGER),
        CASE WHEN EXISTS(
          SELECT 1 FROM pages active_page
          WHERE active_page.id=subtree.id AND active_page.archived=0
        ) THEN 1 ELSE 0 END
      FROM subtree WHERE subtree.id IS NOT NULL
      ON CONFLICT(page_id) DO UPDATE SET
        change_generation=CASE
          WHEN change_generation>={_MAX_GENERATION}
          THEN RAISE(ABORT,'legacy change generation exhausted')
          ELSE change_generation+1 END,
        change_mask=(change_mask | excluded.change_mask),
        changed_at=excluded.changed_at,
        source_exists=excluded.source_exists
    """.strip()


def _epoch_statement(*, full_sweep: bool) -> str:
    full_sweep_sql = (
        ",full_sweep_generation=CASE "
        f"WHEN full_sweep_generation>={_MAX_GENERATION} "
        "THEN RAISE(ABORT,'legacy full sweep generation exhausted') "
        "ELSE full_sweep_generation+1 END"
        if full_sweep
        else ""
    )
    return (
        "UPDATE notion_legacy_change_state SET source_epoch=CASE "
        f"WHEN source_epoch>={_MAX_GENERATION} "
        "THEN RAISE(ABORT,'legacy source epoch exhausted') "
        "ELSE source_epoch+1 END"
        + full_sweep_sql
        + " WHERE singleton=1"
    )


def _trigger(name: str, timing: str, table: str, body: str, when: str = "") -> str:
    predicate = f" WHEN {when}" if when else ""
    return (
        f"CREATE TRIGGER {name} {timing} ON {table}{predicate} "
        f"BEGIN {body}; END"
    )


_DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE notion_legacy_schema_migrations(
      version INTEGER PRIMARY KEY,
      name TEXT NOT NULL,
      checksum TEXT NOT NULL,
      actual_schema_sha256 TEXT NOT NULL,
      applied_at INTEGER NOT NULL CHECK(applied_at>=0)
    )
    """.strip(),
    f"""
    CREATE TABLE notion_legacy_change_state(
      singleton INTEGER PRIMARY KEY CHECK(singleton=1),
      source_epoch INTEGER NOT NULL CHECK(
        typeof(source_epoch)='integer' AND source_epoch BETWEEN 1 AND {_MAX_GENERATION}
      ),
      full_sweep_generation INTEGER NOT NULL CHECK(
        typeof(full_sweep_generation)='integer' AND
        full_sweep_generation BETWEEN 1 AND {_MAX_GENERATION}
      )
    )
    """.strip(),
    "INSERT INTO notion_legacy_change_state(singleton,source_epoch,full_sweep_generation) "
    "VALUES(1,1,1)",
    f"""
    CREATE TABLE notion_page_change_watermarks(
      page_id TEXT PRIMARY KEY CHECK(
        typeof(page_id)='text' AND length(page_id) BETWEEN 1 AND 160
      ),
      change_generation INTEGER NOT NULL DEFAULT 1 CHECK(
        typeof(change_generation)='integer' AND
        change_generation BETWEEN 1 AND {_MAX_GENERATION}
      ),
      change_mask INTEGER NOT NULL CHECK(
        typeof(change_mask)='integer' AND change_mask BETWEEN 1 AND {CHANGE_ALL}
      ),
      changed_at INTEGER NOT NULL CHECK(
        typeof(changed_at)='integer' AND changed_at>=0
      ),
      source_exists INTEGER NOT NULL CHECK(source_exists IN (0,1))
    )
    """.strip(),
    "CREATE INDEX idx_notion_page_change_watermarks_generation "
    "ON notion_page_change_watermarks(change_generation,page_id)",
    _trigger(
        "trg_notion_change_pages_insert",
        "AFTER INSERT",
        "pages",
        _watermark_statement("NEW.id", CHANGE_PAGE)
        + "; "
        + _epoch_statement(full_sweep=True),
    ),
    _trigger(
        "trg_notion_change_pages_update",
        "AFTER UPDATE",
        "pages",
        _watermark_statement("NEW.id", CHANGE_PAGE)
        + "; INSERT INTO notion_page_change_watermarks("
        "page_id,change_generation,change_mask,changed_at,source_exists) "
        f"SELECT OLD.id,1,{CHANGE_PAGE},CAST(strftime('%s','now') AS INTEGER),0 "
        "WHERE OLD.id<>NEW.id ON CONFLICT(page_id) DO UPDATE SET "
        "change_generation=CASE WHEN change_generation>="
        f"{_MAX_GENERATION} THEN RAISE(ABORT,'legacy change generation exhausted') "
        "ELSE change_generation+1 END,change_mask=(change_mask | excluded.change_mask),"
        "changed_at=excluded.changed_at,source_exists=0; "
        + _epoch_statement(full_sweep=True),
    ),
    _trigger(
        "trg_notion_change_pages_parent_update",
        "AFTER UPDATE OF parent_id",
        "pages",
        _watermark_subtree_statement("NEW.id", CHANGE_PAGE),
        "OLD.parent_id IS NOT NEW.parent_id",
    ),
    _trigger(
        "trg_notion_change_pages_delete",
        "AFTER DELETE",
        "pages",
        f"""
        INSERT INTO notion_page_change_watermarks(
          page_id,change_generation,change_mask,changed_at,source_exists
        ) VALUES(OLD.id,1,{CHANGE_PAGE},CAST(strftime('%s','now') AS INTEGER),0)
        ON CONFLICT(page_id) DO UPDATE SET
          change_generation=CASE WHEN change_generation>={_MAX_GENERATION}
            THEN RAISE(ABORT,'legacy change generation exhausted')
            ELSE change_generation+1 END,
          change_mask=(change_mask | excluded.change_mask),
          changed_at=excluded.changed_at,source_exists=0
        """.strip()
        + "; "
        + _epoch_statement(full_sweep=True),
    ),
    *tuple(
        statement
        for table, bit in (
            ("blocks", CHANGE_BLOCK),
            ("files", CHANGE_FILE),
            ("public_links", CHANGE_PUBLIC),
            ("page_shares", CHANGE_SHARE),
        )
        for statement in (
            _trigger(
                f"trg_notion_change_{table}_insert",
                "AFTER INSERT",
                table,
                (
                    _watermark_subtree_statement("NEW.page_id", bit)
                    if table in {"public_links", "page_shares"}
                    else _watermark_statement("NEW.page_id", bit)
                )
                + "; "
                + _epoch_statement(full_sweep=table in {"public_links", "page_shares"}),
            ),
            _trigger(
                f"trg_notion_change_{table}_update",
                "AFTER UPDATE",
                table,
                (
                    _watermark_subtree_statement("NEW.page_id", bit)
                    if table in {"public_links", "page_shares"}
                    else _watermark_statement("NEW.page_id", bit)
                )
                + "; "
                + (
                    _watermark_subtree_statement("OLD.page_id", bit)
                    if table in {"public_links", "page_shares"}
                    else _watermark_statement("OLD.page_id", bit)
                )
                + "; "
                + _epoch_statement(full_sweep=table in {"public_links", "page_shares"}),
                "OLD.page_id<>NEW.page_id",
            ),
            _trigger(
                f"trg_notion_change_{table}_update_same_page",
                "AFTER UPDATE",
                table,
                (
                    _watermark_subtree_statement("NEW.page_id", bit)
                    if table in {"public_links", "page_shares"}
                    else _watermark_statement("NEW.page_id", bit)
                )
                + "; "
                + _epoch_statement(full_sweep=table in {"public_links", "page_shares"}),
                "OLD.page_id=NEW.page_id",
            ),
            _trigger(
                f"trg_notion_change_{table}_delete",
                "AFTER DELETE",
                table,
                (
                    _watermark_subtree_statement("OLD.page_id", bit)
                    if table in {"public_links", "page_shares"}
                    else _watermark_statement("OLD.page_id", bit)
                )
                + "; "
                + _epoch_statement(full_sweep=table in {"public_links", "page_shares"}),
            ),
        )
    ),
)

_MIGRATION_CHECKSUM = (
    "sha256:d110d59a175f98cae7851cc3fd130efdb2ae16f221657ceccc7689368166033b"
)
if _sha256_text("\n-- statement --\n".join(_DDL_STATEMENTS)) != _MIGRATION_CHECKSUM:
    raise RuntimeError("frozen legacy change-tracking DDL drifted")
_EXPECTED_OBJECTS = {
    "notion_legacy_schema_migrations",
    "notion_legacy_change_state",
    "notion_page_change_watermarks",
    "idx_notion_page_change_watermarks_generation",
    "trg_notion_change_pages_insert",
    "trg_notion_change_pages_update",
    "trg_notion_change_pages_parent_update",
    "trg_notion_change_pages_delete",
    *{
        f"trg_notion_change_{table}_{suffix}"
        for table in ("blocks", "files", "public_links", "page_shares")
        for suffix in ("insert", "update", "update_same_page", "delete")
    },
}
_REQUIRED_COLUMNS = {
    "pages": {"id", "parent_id", "archived"},
    "blocks": {"page_id"},
    "files": {"page_id"},
    "public_links": {"page_id"},
    "page_shares": {"page_id"},
}
_REQUIRED_COLUMN_TYPES = {
    "pages": {"id": "TEXT", "parent_id": "TEXT", "archived": "INTEGER"},
    "blocks": {"page_id": "TEXT"},
    "files": {"page_id": "TEXT"},
    "public_links": {"page_id": "TEXT"},
    "page_shares": {"page_id": "TEXT"},
}
_CHILD_TABLES = ("blocks", "files", "public_links", "page_shares")


def _object_hash(con: sqlite3.Connection) -> str:
    placeholders = ",".join("?" for _ in _EXPECTED_OBJECTS)
    rows = con.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        tuple(sorted(_EXPECTED_OBJECTS)),
    ).fetchall()
    return _sha256_text(_canonical_json([tuple(row) for row in rows]))


def _expected_object_hash() -> str:
    con = sqlite3.connect(":memory:")
    try:
        con.executescript(
            """
            CREATE TABLE pages(id TEXT PRIMARY KEY,archived INTEGER);
            CREATE TABLE blocks(page_id TEXT);
            CREATE TABLE files(page_id TEXT);
            CREATE TABLE public_links(page_id TEXT);
            CREATE TABLE page_shares(page_id TEXT);
            """
        )
        for statement in _DDL_STATEMENTS:
            con.execute(statement)
        return _object_hash(con)
    finally:
        con.close()


_EXPECTED_OBJECT_HASH = (
    "sha256:020c9cd1288120668e084d97f4e6304a7c9332ddb468436973496a4af301590e"
)
if _expected_object_hash() != _EXPECTED_OBJECT_HASH:
    raise RuntimeError("frozen legacy change-tracking object contract drifted")


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(junction())


def _assert_link_free(path: Path, stop: Path) -> None:
    current = path
    while True:
        if _is_link(current):
            raise LegacyChangeMigrationRefused("migration path contains a link")
        if current == stop:
            return
        parent = current.parent
        if parent == current:
            raise LegacyChangeMigrationRefused("migration path escaped its root")
        current = parent


def resolve_paths(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> dict[str, Path]:
    owner = str(source_owner_id or "").strip().lower()
    if not _OWNER_RE.fullmatch(owner):
        raise LegacyChangeMigrationRefused("valid source_owner_id required")
    try:
        root_raw = Path(user_data_root)
        if not root_raw.is_absolute() or _is_link(root_raw):
            raise LegacyChangeMigrationRefused("exact user_data_root required")
        root = root_raw.resolve(strict=True)
        expected_raw = Path(expected_source_db_path)
        if not expected_raw.is_absolute():
            raise LegacyChangeMigrationRefused("exact source database path required")
        exact_raw = root / owner / "notion" / "notion.db"
        _assert_link_free(exact_raw, root)
        _assert_link_free(expected_raw, root)
        exact = exact_raw.resolve(strict=True)
        expected = expected_raw.resolve(strict=True)
        if exact != expected or not exact.is_file():
            raise LegacyChangeMigrationRefused("exact source database path required")
        stat = exact.stat()
        if stat.st_nlink != 1:
            raise LegacyChangeMigrationRefused("source database must have one link")
    except LegacyChangeMigrationRefused:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise LegacyChangeMigrationRefused("source database is unavailable") from exc
    return {"root": root, "source": exact, "owner": Path(owner)}


def _file_identity(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return (
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_nlink),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _assert_exact_file(
    path: Path,
    *,
    root: Path,
    expected_resolved: Path,
    expected_identity: tuple[int, int, int, int, int] | None = None,
    content_identity: bool = True,
) -> tuple[int, int, int, int, int]:
    """Revalidate containment, link state, canonical path and file identity."""
    try:
        _assert_link_free(path, root)
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
        if resolved != expected_resolved or not resolved.is_file():
            raise LegacyChangeMigrationRefused("exact migration file path changed")
        identity = _file_identity(resolved)
        if identity[2] != 1:
            raise LegacyChangeMigrationRefused("migration file must have one link")
        if expected_identity is not None:
            expected = expected_identity if content_identity else expected_identity[:3]
            actual = identity if content_identity else identity[:3]
            if actual != expected:
                raise LegacyChangeMigrationRefused("migration file identity changed")
        return identity
    except LegacyChangeMigrationRefused:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise LegacyChangeMigrationRefused("migration file is unavailable") from exc


def _assert_rollback_journal(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
        if len(header) < 20 or header[18:20] != b"\x01\x01":
            raise LegacyChangeMigrationRefused(
                "source database must be checkpointed in rollback-journal mode"
            )
        if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
            raise LegacyChangeMigrationRefused("source database sidecar is present")
    except LegacyChangeMigrationRefused:
        raise
    except OSError as exc:
        raise LegacyChangeMigrationRefused("source database is unavailable") from exc


def _open(path: Path, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    else:
        con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=5000")
    if read_only:
        con.execute("PRAGMA query_only=ON")
    return con


def _open_exact(
    path: Path,
    *,
    root: Path,
    expected_resolved: Path,
    read_only: bool,
    expected_identity: tuple[int, int, int, int, int] | None = None,
    content_identity: bool = True,
) -> sqlite3.Connection:
    _assert_exact_file(
        path,
        root=root,
        expected_resolved=expected_resolved,
        expected_identity=expected_identity,
        content_identity=content_identity,
    )
    pins = _acquire_path_pins(root=root, target=path, include_target=True)
    try:
        if read_only:
            con = sqlite3.connect(
                path.as_uri() + "?mode=ro",
                uri=True,
                factory=_PinnedConnection,
            )
        else:
            con = sqlite3.connect(path, factory=_PinnedConnection)
        con._migration_path_pins = pins
        pins = []
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        if read_only:
            con.execute("PRAGMA query_only=ON")
    except Exception:
        _close_path_pins(pins)
        raise
    try:
        main = next(
            (str(row[2] or "") for row in con.execute("PRAGMA database_list") if row[1] == "main"),
            "",
        )
        if not main or Path(main).resolve(strict=True) != expected_resolved:
            raise LegacyChangeMigrationRefused("SQLite opened an unexpected database")
        _assert_exact_file(
            path,
            root=root,
            expected_resolved=expected_resolved,
            expected_identity=expected_identity,
            content_identity=content_identity,
        )
        return con
    except Exception:
        con.close()
        raise


def _logical_fingerprint(con: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for line in con.iterdump():
        digest.update(line.encode("utf-8"))
        digest.update(b"\n")
    return "sha256:" + digest.hexdigest()


def _inspect(
    con: sqlite3.Connection,
    *,
    verify_integrity: bool = True,
) -> dict[str, Any]:
    if verify_integrity:
        if str(con.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
            raise LegacyChangeMigrationRefused("source database quick_check failed")
        if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise LegacyChangeMigrationRefused("source database foreign keys are invalid")
    for table, required in _REQUIRED_COLUMNS.items():
        column_rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
        columns = {str(row[1]): row for row in column_rows}
        if not required.issubset(columns):
            raise LegacyChangeMigrationRefused("source database schema is unsupported")
        for column, expected_type in _REQUIRED_COLUMN_TYPES[table].items():
            row = columns[column]
            if str(row[2] or "").upper() != expected_type:
                raise LegacyChangeMigrationRefused("source database schema is unsupported")
        if table != "pages" and int(columns["page_id"][3] or 0) != 1:
            # A PRIMARY KEY is implicitly non-null even when PRAGMA reports 0.
            if int(columns["page_id"][5] or 0) < 1:
                raise LegacyChangeMigrationRefused("source database schema is unsupported")
    for table in _CHILD_TABLES:
        foreign_keys = con.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
        if not any(
            str(row[2]) == "pages"
            and str(row[3]) == "page_id"
            and str(row[4]) == "id"
            and str(row[6]).upper() == "CASCADE"
            for row in foreign_keys
        ):
            raise LegacyChangeMigrationRefused("source database schema is unsupported")
    present = {
        str(row[0])
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE name IN ("
            + ",".join("?" for _ in _EXPECTED_OBJECTS)
            + ")",
            tuple(sorted(_EXPECTED_OBJECTS)),
        ).fetchall()
    }
    if not present:
        return {"installed": False, "object_hash": ""}
    if present != _EXPECTED_OBJECTS:
        raise LegacyChangeMigrationRefused("legacy change schema is partial or unknown")
    marker = con.execute(
        "SELECT name,checksum,actual_schema_sha256 FROM notion_legacy_schema_migrations "
        "WHERE version=?",
        (MIGRATION_VERSION,),
    ).fetchone()
    actual_hash = _object_hash(con)
    if (
        not marker
        or str(marker["name"]) != MIGRATION_NAME
        or str(marker["checksum"]) != _MIGRATION_CHECKSUM
        or str(marker["actual_schema_sha256"]) != _EXPECTED_OBJECT_HASH
        or actual_hash != _EXPECTED_OBJECT_HASH
    ):
        raise LegacyChangeMigrationRefused("legacy change schema is invalid")
    state_rows = con.execute(
        "SELECT singleton,source_epoch,full_sweep_generation "
        "FROM notion_legacy_change_state"
    ).fetchall()
    if (
        len(state_rows) != 1
        or tuple(state_rows[0])[:1] != (1,)
        or type(state_rows[0]["source_epoch"]) is not int
        or not 1 <= state_rows[0]["source_epoch"] <= _MAX_GENERATION
        or type(state_rows[0]["full_sweep_generation"]) is not int
        or not 1 <= state_rows[0]["full_sweep_generation"] <= _MAX_GENERATION
    ):
        raise LegacyChangeMigrationRefused("legacy change schema is invalid")
    return {"installed": True, "object_hash": actual_hash}


def _plan_body(paths: dict[str, Path], inspection: dict[str, Any], fingerprint: str) -> dict[str, Any]:
    return {
        "plan_version": PLAN_VERSION,
        "migration_version": MIGRATION_VERSION,
        "migration_name": MIGRATION_NAME,
        "migration_checksum": _MIGRATION_CHECKSUM,
        "expected_object_hash": _EXPECTED_OBJECT_HASH,
        "user_data_root": os.fspath(paths["root"]),
        "source_owner_id": paths["owner"].name,
        "source_db_path": os.fspath(paths["source"]),
        "source_database_fingerprint": fingerprint,
        "action": "already_current" if inspection["installed"] else "install",
        "quiescence_required": True,
        "backup_required": True,
    }


def build_plan(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> dict[str, Any]:
    paths = resolve_paths(
        user_data_root=user_data_root,
        source_owner_id=source_owner_id,
        expected_source_db_path=expected_source_db_path,
    )
    _assert_rollback_journal(paths["source"])
    source_identity = _file_identity(paths["source"])
    con = _open_exact(
        paths["source"],
        root=paths["root"],
        expected_resolved=paths["source"],
        read_only=True,
        expected_identity=source_identity,
    )
    try:
        inspection = _inspect(con)
        fingerprint = _logical_fingerprint(con)
    finally:
        con.close()
    _assert_rollback_journal(paths["source"])
    body = _plan_body(paths, inspection, fingerprint)
    return {**body, "plan_checksum": _sha256_text(_canonical_json(body))}


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict) or plan.get("plan_version") != PLAN_VERSION:
        raise LegacyChangeMigrationRefused("valid migration plan required")
    body = {key: value for key, value in plan.items() if key != "plan_checksum"}
    if str(plan.get("plan_checksum") or "") != _sha256_text(_canonical_json(body)):
        raise LegacyChangeMigrationRefused("migration plan checksum mismatch")


def _backup(
    source: Path,
    backup_path: Path,
    root: Path,
    *,
    expected_source_identity: tuple[int, int, int, int, int],
    expected_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create and retain an immutable, metadata-equivalent backup guard.

    The source is copied into an in-memory SQLite database and serialized.  No
    filesystem SQLite writer is opened for the backup, so no journal/WAL leaf
    can escape the pinned namespace.  The returned native handle remains open
    without write/delete sharing until the source postcondition is verified.
    """
    parent_pins: list[int] = []
    created_handle: int | None = None
    source_guard: int | None = None
    verified = False
    try:
        if not backup_path.is_absolute() or backup_path.exists():
            raise LegacyChangeMigrationRefused("backup target must be a new exact path")
        raw_parent = backup_path.parent
        _assert_link_free(raw_parent, root)
        parent = raw_parent.resolve(strict=True)
        parent.relative_to(root)
        if parent == source.parent or backup_path == source:
            raise LegacyChangeMigrationRefused("backup target must be separate")
        canonical_backup = parent / backup_path.name

        source_guard = _pin_windows_path(
            source,
            directory=False,
            allow_delete=False,
            share_write=False,
        )
        source_metadata = _windows_security_metadata(source_guard)
        if (
            source_metadata[2][1] != expected_source_identity[1]
            or source_metadata[2][2] != expected_source_identity[2]
        ):
            raise LegacyChangeMigrationRefused("source database identity changed")
        source_con = _open_exact(
            source,
            root=root,
            expected_resolved=source,
            read_only=True,
            expected_identity=expected_source_identity,
        )
        memory = sqlite3.connect(":memory:")
        memory.row_factory = sqlite3.Row
        memory.execute("PRAGMA foreign_keys=ON")
        try:
            source_con.backup(memory)
            _inspect(memory)
            fingerprint = _logical_fingerprint(memory)
            if fingerprint != expected_fingerprint:
                raise LegacyChangeMigrationRefused(
                    "verified backup does not match source"
                )
            image = memory.serialize()
        finally:
            memory.close()
            source_con.close()

        parent_pins = _acquire_path_pins(
            root=root,
            target=canonical_backup,
            include_target=False,
        )
        if canonical_backup.parent.resolve(strict=True) != parent:
            raise LegacyChangeMigrationRefused("backup parent changed")
        created_handle = _create_pinned_windows_file(
            canonical_backup,
            parent_handle=parent_pins[-1],
            exclusive=True,
        )
        backup_metadata = _windows_security_metadata(created_handle)
        if backup_metadata[:2] != source_metadata[:2]:
            raise LegacyChangeMigrationRefused(
                "backup security metadata does not match source"
            )
        _write_flush_windows_handle(created_handle, image)
        raw_sha256 = "sha256:" + hashlib.sha256(image).hexdigest()
        if _sha256_windows_handle(created_handle) != raw_sha256:
            raise LegacyChangeMigrationRefused("verified backup write failed")
        copied_identity = _assert_exact_file(
            canonical_backup,
            root=root,
            expected_resolved=canonical_backup,
        )
        current_metadata = _windows_security_metadata(created_handle)
        if (
            current_metadata[:2] != source_metadata[:2]
            or current_metadata[2][1] != copied_identity[1]
            or current_metadata[2][2] != copied_identity[2]
        ):
            raise LegacyChangeMigrationRefused(
                "backup security metadata changed"
            )
        public = {
            "path": os.fspath(canonical_backup),
            "canonical_path": os.fspath(canonical_backup),
            "fingerprint": fingerprint,
            "identity": copied_identity,
        }
        guard = {
            "handle": created_handle,
            "parent_pins": parent_pins,
            "source_handle": source_guard,
            "source_security_metadata": source_metadata,
            "raw_sha256": raw_sha256,
            "security_metadata": current_metadata,
        }
        created_handle = None
        source_guard = None
        parent_pins = []
        verified = True
        return public, guard
    except LegacyChangeMigrationRefused:
        raise
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise LegacyChangeMigrationRefused("verified backup failed") from exc
    finally:
        cleanup_error: BaseException | None = None
        if created_handle is not None:
            if not verified:
                try:
                    _dispose_windows_handle_on_close(created_handle)
                except LegacyChangeMigrationRefused as exc:
                    cleanup_error = exc
            if created_handle is not None:
                _close_path_pins([created_handle])
        if source_guard is not None:
            _close_path_pins([source_guard])
        _close_path_pins(parent_pins)
        if cleanup_error is not None:
            raise cleanup_error


def _close_backup_guard(guard: dict[str, Any] | None) -> None:
    if not guard:
        return
    source_handle = guard.pop("source_handle", None)
    if source_handle is not None:
        _close_path_pins([int(source_handle)])
    handle = guard.pop("handle", None)
    if handle is not None:
        _close_path_pins([int(handle)])
    pins = guard.pop("parent_pins", [])
    _close_path_pins([int(item) for item in pins])


def _invalidate_backup_guard(guard: dict[str, Any]) -> None:
    handle = guard.get("handle")
    if handle is not None:
        _dispose_windows_handle_on_close(int(handle))


def _revalidate_backup(
    backup: dict[str, Any],
    guard: dict[str, Any],
    *,
    root: Path,
    expected_fingerprint: str,
    verify_source_metadata: bool = True,
    invalidate_on_security_change: bool = True,
) -> None:
    try:
        path = Path(str(backup["path"]))
        canonical = Path(str(backup["canonical_path"]))
        identity = tuple(backup["identity"])
        _assert_exact_file(
            path,
            root=root,
            expected_resolved=canonical,
            expected_identity=identity,
        )
        handle = int(guard["handle"])
        metadata = _windows_security_metadata(handle)
        source_metadata = (
            _windows_security_metadata(int(guard["source_handle"]))
            if verify_source_metadata
            else guard["source_security_metadata"]
        )
        security_changed = (
            metadata != guard["security_metadata"]
            or (
                verify_source_metadata
                and source_metadata[:2] != guard["source_security_metadata"][:2]
            )
            or metadata[:2] != source_metadata[:2]
        )
        if security_changed:
            if invalidate_on_security_change:
                _invalidate_backup_guard(guard)
            raise LegacyChangeMigrationRefused(
                "verified backup security metadata changed before commit"
            )
        if (
            metadata[2][1] != identity[1]
            or metadata[2][2] != identity[2]
            or _sha256_windows_handle(handle) != guard["raw_sha256"]
            or str(backup.get("fingerprint") or "") != expected_fingerprint
        ):
            raise LegacyChangeMigrationRefused("verified backup changed before commit")
    except (LegacyChangeMigrationRefused, OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise LegacyChangeMigrationRefused(
            "verified backup changed before commit"
        ) from exc


def _build_migrated_image(
    source: sqlite3.Connection,
    *,
    expected_source_fingerprint: str,
    fault_injector: Callable[[str], None] | None,
) -> tuple[bytes, str]:
    """Build and validate the target wholly in memory; source stays read-only."""
    target = sqlite3.connect(":memory:")
    target.row_factory = sqlite3.Row
    target.execute("PRAGMA foreign_keys=ON")
    target.execute("PRAGMA temp_store=MEMORY")
    try:
        source.backup(target)
        if _logical_fingerprint(target) != expected_source_fingerprint:
            raise LegacyChangeMigrationRefused("source changed before migration")
        if _inspect(target)["installed"]:
            raise LegacyChangeMigrationRefused("source schema changed before migration")
        target.execute("BEGIN IMMEDIATE")
        for statement in _DDL_STATEMENTS:
            target.execute(statement)
        now = int(
            target.execute("SELECT CAST(strftime('%s','now') AS INTEGER)").fetchone()[0]
        )
        target.execute(
            """
            INSERT INTO notion_page_change_watermarks(
              page_id,change_generation,change_mask,changed_at,source_exists
            )
            SELECT id,1,?, ?,CASE WHEN archived=0 THEN 1 ELSE 0 END FROM pages
            """,
            (CHANGE_ALL, now),
        )
        target.execute(
            """
            INSERT INTO notion_legacy_schema_migrations(
              version,name,checksum,actual_schema_sha256,applied_at
            ) VALUES(?,?,?,?,?)
            """,
            (
                MIGRATION_VERSION,
                MIGRATION_NAME,
                _MIGRATION_CHECKSUM,
                _EXPECTED_OBJECT_HASH,
                now,
            ),
        )
        if fault_injector:
            fault_injector("after_schema")
        target.commit()
        if not _inspect(target)["installed"]:
            raise LegacyChangeMigrationRefused("migration postcondition failed")
        final_fingerprint = _logical_fingerprint(target)
        image = target.serialize()
        if not image:
            raise LegacyChangeMigrationRefused("migration image is empty")
        return image, final_fingerprint
    except Exception:
        if target.in_transaction:
            target.rollback()
        raise
    finally:
        target.close()


def apply_plan(
    plan: dict[str, Any],
    *,
    confirm_checksum: str,
    source_quiesced_confirmed: bool,
    backup_confirmed: bool,
    expected_backup_path: str | os.PathLike[str],
    fault_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    _validate_plan(plan)
    if source_quiesced_confirmed is not True:
        raise LegacyChangeMigrationRefused("apply requires source quiescence")
    if backup_confirmed is not True:
        raise LegacyChangeMigrationRefused("apply requires backup confirmation")
    if confirm_checksum != plan["plan_checksum"]:
        raise LegacyChangeMigrationRefused("confirm_checksum does not match the plan")
    fresh = build_plan(
        user_data_root=plan["user_data_root"],
        source_owner_id=plan["source_owner_id"],
        expected_source_db_path=plan["source_db_path"],
    )
    if fresh != plan:
        raise LegacyChangeMigrationRefused("source changed after the approved dry-run")
    if plan["action"] == "already_current":
        return {
            "status": "already_current",
            "migration_version": MIGRATION_VERSION,
            "plan_checksum": plan["plan_checksum"],
            "commit_recovered": False,
        }
    paths = resolve_paths(
        user_data_root=plan["user_data_root"],
        source_owner_id=plan["source_owner_id"],
        expected_source_db_path=plan["source_db_path"],
    )
    source_identity = _file_identity(paths["source"])
    backup_path = Path(expected_backup_path)
    backup, backup_guard = _backup(
        paths["source"],
        backup_path,
        paths["root"],
        expected_source_identity=source_identity,
        expected_fingerprint=plan["source_database_fingerprint"],
    )
    try:
        if fault_injector:
            fault_injector("after_backup")
        if _file_identity(paths["source"]) != source_identity:
            raise LegacyChangeMigrationRefused("source database identity changed")
        _assert_rollback_journal(paths["source"])
        _revalidate_backup(
            backup,
            backup_guard,
            root=paths["root"],
            expected_fingerprint=plan["source_database_fingerprint"],
        )
    except BaseException:
        _close_backup_guard(backup_guard)
        raise

    backup_commit_pins: list[int] = []
    directory_chain: list[int] = []
    stage_handle: int | None = None
    source_security_handle: int | None = None
    stage_location = "absent"
    commit_recovered = False
    final_fingerprint = ""
    try:
        # No SQLite write connection is ever opened against the source. Every
        # namespace component is fenced while an in-memory target is built.
        directory_chain = _pinned_directory_chain(
            paths["root"], paths["source"].parent
        )
        _assert_rollback_journal(paths["source"])
        source_security_handle = _pin_windows_path(
            paths["source"],
            directory=False,
            allow_delete=True,
            share_write=False,
        )
        source_security_metadata = _windows_security_metadata(
            source_security_handle
        )
        if (
            source_security_metadata[2][1] != source_identity[1]
            or source_security_metadata[2][2] != source_identity[2]
        ):
            raise LegacyChangeMigrationRefused(
                "source security metadata identity mismatch"
            )
        if source_security_metadata != backup_guard["source_security_metadata"]:
            try:
                _dispose_windows_handle_on_close(int(backup_guard["handle"]))
            finally:
                _close_backup_guard(backup_guard)
            raise LegacyChangeMigrationRefused(
                "source security metadata changed after backup"
            )
        source = _open_exact(
            paths["source"],
            root=paths["root"],
            expected_resolved=paths["source"],
            read_only=True,
            expected_identity=source_identity,
        )
        try:
            image, final_fingerprint = _build_migrated_image(
                source,
                expected_source_fingerprint=plan["source_database_fingerprint"],
                fault_injector=fault_injector,
            )
        finally:
            source.close()

        stage_path = paths["source"].with_name(
            f".{paths['source'].name}.legacy-change-{uuid.uuid4().hex}.stage"
        )
        stage_handle = _create_pinned_windows_file(
            stage_path,
            parent_handle=directory_chain[-1],
            exclusive=True,
        )
        stage_location = "stage"
        _write_flush_windows_handle(stage_handle, image)
        stage_security_metadata = _windows_security_metadata(stage_handle)
        if stage_security_metadata[:2] != source_security_metadata[:2]:
            raise LegacyChangeMigrationRefused(
                "source security metadata is not safely replaceable"
            )
        if fault_injector:
            fault_injector("before_commit")
        _revalidate_backup(
            backup,
            backup_guard,
            root=paths["root"],
            expected_fingerprint=plan["source_database_fingerprint"],
        )
        _assert_exact_file(
            paths["source"],
            root=paths["root"],
            expected_resolved=paths["source"],
            expected_identity=source_identity,
            content_identity=False,
        )
        if _windows_security_metadata(source_security_handle) != source_security_metadata:
            _invalidate_backup_guard(backup_guard)
            raise LegacyChangeMigrationRefused(
                "source security metadata changed before replacement"
            )
        if _windows_security_metadata(stage_handle) != stage_security_metadata:
            raise LegacyChangeMigrationRefused(
                "staging security metadata changed before replacement"
            )
        verify_source = _open_exact(
            paths["source"],
            root=paths["root"],
            expected_resolved=paths["source"],
            read_only=True,
            expected_identity=source_identity,
        )
        try:
            if (
                _inspect(verify_source)["installed"]
                or _logical_fingerprint(verify_source)
                != plan["source_database_fingerprint"]
            ):
                raise LegacyChangeMigrationRefused("source changed before replacement")
        finally:
            verify_source.close()

        stage_location = "unknown"
        try:
            _replace_windows_handle_relative(
                stage_handle,
                parent_handle=directory_chain[-1],
                target_name=paths["source"].name,
            )
            stage_location = "target"
        except LegacyChangeMigrationRefused as replace_error:
            # Use the still-open stage handle as the authority.  A pathname
            # reopen can be denied by its exclusive share even after the
            # native rename has already made it the live source.
            try:
                converged = (
                    os.path.normcase(
                        os.path.abspath(
                            os.fspath(_windows_final_path(stage_handle))
                        )
                    )
                    == os.path.normcase(
                        os.path.abspath(os.fspath(paths["source"]))
                    )
                    and _sha256_windows_handle(stage_handle)
                    == "sha256:" + hashlib.sha256(image).hexdigest()
                )
            except Exception:
                converged = False
            if not converged:
                raise replace_error
            stage_location = "target"
            commit_recovered = True
        if _windows_security_metadata(stage_handle)[:2] != source_security_metadata[:2]:
            raise LegacyChangeMigrationRefused(
                "replacement security metadata changed; manual audit required"
            )
        _flush_windows_handle(stage_handle)
        if fault_injector:
            fault_injector("after_replacement")
        replacement_metadata = _windows_security_metadata(stage_handle)
        if replacement_metadata != stage_security_metadata:
            raise LegacyChangeMigrationRefused(
                "replacement security metadata changed; manual audit required"
            )
        expected_image_sha256 = "sha256:" + hashlib.sha256(image).hexdigest()
        if _sha256_windows_handle(stage_handle) != expected_image_sha256:
            raise LegacyChangeMigrationRefused(
                "replacement content changed; restore from verified backup"
            )
        final_handle_path = os.path.normcase(
            os.path.abspath(os.fspath(_windows_final_path(stage_handle)))
        )
        expected_handle_path = os.path.normcase(
            os.path.abspath(os.fspath(paths["source"]))
        )
        if final_handle_path != expected_handle_path:
            raise LegacyChangeMigrationRefused(
                "replacement path changed; restore from verified backup"
            )
    except BaseException:
        _close_backup_guard(backup_guard)
        if stage_location == "unknown":
            raise LegacyChangeMigrationRefused(
                "replacement_outcome_indeterminate: manual audit required"
            ) from None
        raise
    finally:
        cleanup_error: BaseException | None = None
        if stage_handle is not None:
            if stage_location == "stage":
                try:
                    _dispose_windows_handle_on_close(stage_handle)
                except LegacyChangeMigrationRefused as exc:
                    cleanup_error = exc
            # Unknown may already be the live database. Never set delete-on-
            # close unless the handle is proven to remain at the stage name.
            if stage_handle is not None:
                _close_path_pins([stage_handle])
        if source_security_handle is not None:
            _close_path_pins([source_security_handle])
        _close_path_pins(directory_chain)
        _close_path_pins(backup_commit_pins)
        if cleanup_error is not None:
            raise cleanup_error

    try:
        _revalidate_backup(
            backup,
            backup_guard,
            root=paths["root"],
            expected_fingerprint=plan["source_database_fingerprint"],
            verify_source_metadata=False,
            invalidate_on_security_change=False,
        )
        _assert_rollback_journal(paths["source"])
        final_identity = _file_identity(paths["source"])
        final_security_handle: int | None = None
        verify_final: sqlite3.Connection | None = None
        try:
            final_security_handle = _pin_windows_path(
                paths["source"],
                directory=False,
                allow_delete=False,
                share_write=False,
            )
            verify_final = _open_exact(
                paths["source"],
                root=paths["root"],
                expected_resolved=paths["source"],
                read_only=True,
                expected_identity=final_identity,
            )
            final_metadata = _windows_security_metadata(final_security_handle)
            if (
                final_metadata[:2] != replacement_metadata[:2]
                or final_metadata[2][1] != replacement_metadata[2][1]
                or final_metadata[2][2] != replacement_metadata[2][2]
                or final_metadata[2][1] != final_identity[1]
                or final_metadata[2][2] != final_identity[2]
            ):
                raise LegacyChangeMigrationRefused(
                    "replacement security metadata changed; restore from verified backup"
                )
            if (
                not _inspect(verify_final)["installed"]
                or _logical_fingerprint(verify_final) != final_fingerprint
            ):
                raise LegacyChangeMigrationRefused(
                    "replacement postcondition failed; restore from verified backup"
                )
        finally:
            try:
                if verify_final is not None:
                    verify_final.close()
            finally:
                if final_security_handle is not None:
                    _close_path_pins([final_security_handle])
    finally:
        _close_backup_guard(backup_guard)
    return {
        "status": "applied",
        "migration_version": MIGRATION_VERSION,
        "plan_checksum": plan["plan_checksum"],
        "source_database_fingerprint": final_fingerprint,
        "backup": backup,
        "commit_recovered": commit_recovered,
    }


__all__ = [
    "CHANGE_ALL",
    "CHANGE_BLOCK",
    "CHANGE_FILE",
    "CHANGE_PAGE",
    "CHANGE_PUBLIC",
    "CHANGE_SHARE",
    "LegacyChangeMigrationRefused",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "PLAN_VERSION",
    "apply_plan",
    "build_plan",
    "resolve_paths",
]
