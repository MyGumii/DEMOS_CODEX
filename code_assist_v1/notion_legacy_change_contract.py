"""Side-effect-free read contract for migrated legacy change tracking.

This module deliberately does not import the executable migration module.  It
contains the frozen identifiers needed to authenticate the installed objects
and the minimum exact-path, read-only opening helpers used by offline readers.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any


MIGRATION_VERSION = 2026081301
MIGRATION_NAME = "notion_legacy_change_tracking_v1"
MIGRATION_CHECKSUM = (
    "sha256:d110d59a175f98cae7851cc3fd130efdb2ae16f221657ceccc7689368166033b"
)
EXPECTED_OBJECT_HASH = (
    "sha256:020c9cd1288120668e084d97f4e6304a7c9332ddb468436973496a4af301590e"
)
CHANGE_PAGE = 1
CHANGE_BLOCK = 2
CHANGE_FILE = 4
CHANGE_PUBLIC = 8
CHANGE_SHARE = 16
CHANGE_ALL = 31
MAX_GENERATION = 9223372036854775806

_OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{1,63}$")
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


class LegacyChangeContractError(Exception):
    """Sanitized failure to authenticate or open the read contract."""


def _is_link(path: Path) -> bool:
    junction = getattr(path, "is_junction", lambda: False)
    return path.is_symlink() or bool(junction())


def _assert_link_free(path: Path, stop: Path) -> None:
    current = path
    while True:
        if _is_link(current):
            raise LegacyChangeContractError("legacy change path contains a link")
        if current == stop:
            return
        parent = current.parent
        if parent == current:
            raise LegacyChangeContractError("legacy change path escaped its root")
        current = parent


def resolve_legacy_change_paths(
    *,
    user_data_root: str | os.PathLike[str],
    source_owner_id: str,
    expected_source_db_path: str | os.PathLike[str],
) -> dict[str, Path]:
    """Resolve one explicitly named owner shard without creating anything."""
    owner = str(source_owner_id or "").strip().lower()
    if not _OWNER_RE.fullmatch(owner):
        raise LegacyChangeContractError("valid source_owner_id required")
    try:
        root_raw = Path(user_data_root)
        if not root_raw.is_absolute() or _is_link(root_raw):
            raise LegacyChangeContractError("exact user_data_root required")
        root = root_raw.resolve(strict=True)
        expected_raw = Path(expected_source_db_path)
        if not expected_raw.is_absolute():
            raise LegacyChangeContractError("exact source database path required")
        exact_raw = root / owner / "notion" / "notion.db"
        _assert_link_free(exact_raw, root)
        _assert_link_free(expected_raw, root)
        exact = exact_raw.resolve(strict=True)
        expected = expected_raw.resolve(strict=True)
        if exact != expected or not exact.is_file():
            raise LegacyChangeContractError("exact source database path required")
        if exact.stat().st_nlink != 1:
            raise LegacyChangeContractError("source database must have one link")
    except LegacyChangeContractError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise LegacyChangeContractError("legacy change source is unavailable") from exc
    return {"root": root, "source": exact, "owner": Path(owner)}


def legacy_source_file_identity(path: Path) -> tuple[int, int, int, int, int]:
    try:
        stat = path.stat()
        return (
            int(stat.st_dev),
            int(stat.st_ino),
            int(stat.st_nlink),
            int(stat.st_size),
            int(stat.st_mtime_ns),
        )
    except OSError as exc:
        raise LegacyChangeContractError("legacy change source is unavailable") from exc


def assert_legacy_rollback_journal(path: Path) -> None:
    """Require a checkpointed rollback-journal image with no sidecars."""
    try:
        with path.open("rb") as handle:
            header = handle.read(20)
        if len(header) < 20 or header[18:20] != b"\x01\x01":
            raise LegacyChangeContractError(
                "legacy change source must be checkpointed"
            )
        if any(
            Path(str(path) + suffix).exists()
            for suffix in ("-wal", "-shm", "-journal")
        ):
            raise LegacyChangeContractError("legacy change source sidecar is present")
    except LegacyChangeContractError:
        raise
    except OSError as exc:
        raise LegacyChangeContractError("legacy change source is unavailable") from exc


def _windows_final_path(handle: int) -> Path:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    size = kernel32.GetFinalPathNameByHandleW(handle, None, 0, 0)
    if not size:
        raise LegacyChangeContractError("legacy change path pin failed")
    buffer = ctypes.create_unicode_buffer(size + 1)
    if not kernel32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0):
        raise LegacyChangeContractError("legacy change path pin failed")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value)


def _pin_windows_path(path: Path, *, directory: bool) -> int:
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
    handle = create_file(
        os.fspath(path),
        0x0080 | 0x00020000,  # READ_ATTRIBUTES | READ_CONTROL
        0x00000001,  # share reads, but not writes/deletes
        None,
        3,  # OPEN_EXISTING
        0x00200000 | (0x02000000 if directory else 0),
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    if handle in (None, 0, invalid):
        raise LegacyChangeContractError("legacy change path pin failed")
    try:
        attributes = kernel32.GetFileAttributesW(os.fspath(path))
        if attributes == 0xFFFFFFFF or attributes & 0x00000400:
            raise LegacyChangeContractError("legacy change path contains a link")
        if _windows_final_path(int(handle)).resolve(strict=True) != path.resolve(strict=True):
            raise LegacyChangeContractError("legacy change path pin failed")
        return int(handle)
    except Exception:
        kernel32.CloseHandle(handle)
        raise


def _close_pins(pins: list[tuple[str, int]]) -> None:
    for kind, handle in reversed(pins):
        try:
            if kind == "windows":
                import ctypes

                ctypes.windll.kernel32.CloseHandle(handle)
            else:
                os.close(handle)
        except Exception:
            pass


def _acquire_exact_pins(
    *, root: Path, target: Path, expected_identity: tuple[int, int, int, int, int]
) -> tuple[list[tuple[str, int]], str]:
    if os.name == "nt":
        try:
            relative = target.parent.resolve(strict=True).relative_to(
                root.resolve(strict=True)
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise LegacyChangeContractError(
                "legacy change path escaped its root"
            ) from exc
        components = [root.resolve(strict=True)]
        current = components[0]
        for part in relative.parts:
            current = current / part
            components.append(current)
        pins: list[tuple[str, int]] = []
        try:
            for component in components:
                pins.append(("windows", _pin_windows_path(component, directory=True)))
            pins.append(("windows", _pin_windows_path(target, directory=False)))
            return pins, target.as_uri() + "?mode=ro&immutable=1"
        except Exception:
            _close_pins(pins)
            raise

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        handle = os.open(target, flags)
        stat = os.fstat(handle)
        identity = (
            int(stat.st_dev), int(stat.st_ino), int(stat.st_nlink),
            int(stat.st_size), int(stat.st_mtime_ns),
        )
        if identity != expected_identity or identity[2] != 1:
            raise LegacyChangeContractError("legacy change source identity changed")
        descriptor_path = Path(f"/proc/self/fd/{handle}")
        if not descriptor_path.exists():
            raise LegacyChangeContractError(
                "exact legacy change reads are unavailable on this platform"
            )
        return [("posix", handle)], descriptor_path.as_uri() + "?mode=ro&immutable=1"
    except Exception:
        if "handle" in locals():
            try:
                os.close(handle)
            except OSError:
                pass
        raise


class _ExactReadConnection(sqlite3.Connection):
    _legacy_change_pins: list[tuple[str, int]] | None = None

    def close(self) -> None:
        pins = self._legacy_change_pins
        self._legacy_change_pins = None
        try:
            super().close()
        finally:
            if pins:
                _close_pins(pins)


def open_exact_legacy_change_source(
    path: Path,
    *,
    root: Path,
    expected_resolved: Path,
    expected_identity: tuple[int, int, int, int, int],
) -> sqlite3.Connection:
    """Open one pinned, immutable, read-only source image."""
    try:
        _assert_link_free(path, root)
        if path.resolve(strict=True) != expected_resolved:
            raise LegacyChangeContractError("legacy change source path changed")
        if legacy_source_file_identity(path) != expected_identity:
            raise LegacyChangeContractError("legacy change source identity changed")
        pins, uri = _acquire_exact_pins(
            root=root, target=path, expected_identity=expected_identity
        )
        try:
            con = sqlite3.connect(uri, uri=True, factory=_ExactReadConnection)
            con._legacy_change_pins = pins
            pins = []
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA query_only=ON")
            if legacy_source_file_identity(path) != expected_identity:
                raise LegacyChangeContractError("legacy change source identity changed")
            return con
        except Exception:
            _close_pins(pins)
            if "con" in locals():
                con.close()
            raise
    except LegacyChangeContractError:
        raise
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as exc:
        raise LegacyChangeContractError("legacy change source is unavailable") from exc


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object_hash(con: sqlite3.Connection) -> str:
    placeholders = ",".join("?" for _ in _EXPECTED_OBJECTS)
    rows = con.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        tuple(sorted(_EXPECTED_OBJECTS)),
    ).fetchall()
    payload = _canonical_json([tuple(row) for row in rows])
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inspect_legacy_change_contract(
    con: sqlite3.Connection, *, verify_integrity: bool = True
) -> dict[str, Any]:
    """Authenticate the frozen migration objects in the current snapshot."""
    try:
        if verify_integrity:
            if str(con.execute("PRAGMA quick_check").fetchone()[0]) != "ok":
                raise LegacyChangeContractError("legacy change source quick_check failed")
            if con.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise LegacyChangeContractError(
                    "legacy change source foreign keys are invalid"
                )
        for table, required in _REQUIRED_COLUMNS.items():
            rows = con.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns = {str(row[1]): row for row in rows}
            if not required.issubset(columns):
                raise LegacyChangeContractError("legacy change source schema is unsupported")
            for column, expected_type in _REQUIRED_COLUMN_TYPES[table].items():
                if str(columns[column][2] or "").upper() != expected_type:
                    raise LegacyChangeContractError(
                        "legacy change source schema is unsupported"
                    )
            if table != "pages" and int(columns["page_id"][3] or 0) != 1:
                if int(columns["page_id"][5] or 0) < 1:
                    raise LegacyChangeContractError(
                        "legacy change source schema is unsupported"
                    )
        for table in _CHILD_TABLES:
            foreign_keys = con.execute(
                f'PRAGMA foreign_key_list("{table}")'
            ).fetchall()
            if not any(
                str(row[2]) == "pages"
                and str(row[3]) == "page_id"
                and str(row[4]) == "id"
                and str(row[6]).upper() == "CASCADE"
                for row in foreign_keys
            ):
                raise LegacyChangeContractError(
                    "legacy change source schema is unsupported"
                )
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
            raise LegacyChangeContractError("legacy change schema is partial or unknown")
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 "
            "FROM notion_legacy_schema_migrations WHERE version=?",
            (MIGRATION_VERSION,),
        ).fetchone()
        actual_hash = _object_hash(con)
        if (
            not marker
            or str(marker["name"]) != MIGRATION_NAME
            or str(marker["checksum"]) != MIGRATION_CHECKSUM
            or str(marker["actual_schema_sha256"]) != EXPECTED_OBJECT_HASH
            or actual_hash != EXPECTED_OBJECT_HASH
        ):
            raise LegacyChangeContractError("legacy change schema is invalid")
        state_rows = con.execute(
            "SELECT singleton,source_epoch,full_sweep_generation "
            "FROM notion_legacy_change_state"
        ).fetchall()
        if (
            len(state_rows) != 1
            or tuple(state_rows[0])[:1] != (1,)
            or type(state_rows[0]["source_epoch"]) is not int
            or not 1 <= state_rows[0]["source_epoch"] <= MAX_GENERATION
            or type(state_rows[0]["full_sweep_generation"]) is not int
            or not 1
            <= state_rows[0]["full_sweep_generation"]
            <= MAX_GENERATION
        ):
            raise LegacyChangeContractError("legacy change schema is invalid")
        return {"installed": True, "object_hash": actual_hash}
    except LegacyChangeContractError:
        raise
    except (IndexError, KeyError, sqlite3.Error, TypeError, ValueError) as exc:
        raise LegacyChangeContractError("legacy change schema is invalid") from exc


__all__ = [
    "CHANGE_ALL",
    "CHANGE_BLOCK",
    "CHANGE_FILE",
    "CHANGE_PAGE",
    "CHANGE_PUBLIC",
    "CHANGE_SHARE",
    "EXPECTED_OBJECT_HASH",
    "LegacyChangeContractError",
    "MAX_GENERATION",
    "MIGRATION_CHECKSUM",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "assert_legacy_rollback_journal",
    "inspect_legacy_change_contract",
    "legacy_source_file_identity",
    "open_exact_legacy_change_source",
    "resolve_legacy_change_paths",
]
