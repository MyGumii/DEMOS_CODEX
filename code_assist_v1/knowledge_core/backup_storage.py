"""Pinned Windows filesystem boundary for P7.4 backup and restore evidence."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import ntpath
import os
from pathlib import Path
import re
import sqlite3
import stat
import uuid
from typing import Iterator, Mapping

from .backup_manifest import (
    BACKUP_SNAPSHOT_RELATIVE_PATH,
    backup_manifest_errors,
    canonical_json_bytes,
)
from .db import ManagedConnection, online_backup_to_existing_file
from .errors import KnowledgeCoreDatabasePathError, KnowledgeCoreSchemaMismatchError
from .migrations.canonical_catalog import CANONICAL_MIGRATION_CATALOG
from .migrations.fingerprint import schema_sha256
from .migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from .runtime import APPROVED_SQLITE_SOURCE_ID, APPROVED_SQLITE_VERSION


_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SNAPSHOT_NAME = Path(BACKUP_SNAPSHOT_RELATIVE_PATH).name


class BackupFilesystemError(KnowledgeCoreDatabasePathError):
    code = "KNOWLEDGE_CORE_BACKUP_FILESYSTEM_ERROR"


class BackupSnapshotError(KnowledgeCoreSchemaMismatchError):
    code = "KNOWLEDGE_CORE_BACKUP_SNAPSHOT_INVALID"


@dataclass(frozen=True, slots=True, order=True)
class SnapshotBlob:
    sha256: str
    size_bytes: int
    media_type: str
    source_relative_path: str


@dataclass(frozen=True, slots=True)
class SnapshotObservation:
    sha256: str
    size_bytes: int
    audit_high_water: int
    user_version: int
    physical_schema_sha256: str
    quick_check: str
    foreign_key_violation_count: int
    blobs: tuple[SnapshotBlob, ...]


class _RestoreTargetLease:
    __slots__ = ("anchors", "boundary", "identities", "layout", "storage")

    def __init__(
        self,
        storage: "FilesystemBackupStorage",
        layout: MigrationStorageLayout,
        boundary: MigrationStorageBoundary,
        anchors: tuple[object, ...],
        identities: tuple[tuple[int, int], ...],
    ) -> None:
        self.storage = storage
        self.layout = layout
        self.boundary = boundary
        self.anchors = anchors
        self.identities = identities

    def verify(self, *, require_database: bool) -> MigrationStorageBoundary:
        observed = verify_migration_storage_layout(
            self.layout,
            require_database=require_database,
            expected=self.boundary,
        )
        paths = (
            self.layout.data_root,
            self.layout.database.parent,
            self.layout.blob_root,
            self.layout.projection_root,
        )
        if tuple(_directory_identity(value) for value in paths) != self.identities:
            raise BackupFilesystemError(
                "The isolated restore directory identity changed."
            )
        from ..tools.legacy_inventory import _windows_final_path

        expected_paths = (
            self.boundary.data_root.resolved_path,
            self.boundary.database_parent.resolved_path,
            self.boundary.blob_root.resolved_path,
            self.boundary.projection_root.resolved_path,
        )
        for anchor, expected in zip(self.anchors, expected_paths, strict=True):
            if os.path.normcase(os.fspath(_windows_final_path(anchor.handle))) != os.path.normcase(  # type: ignore[attr-defined]
                expected
            ):
                raise BackupFilesystemError(
                    "The isolated restore directory escaped its pinned target."
                )
        return observed

    def close(self) -> None:
        for anchor in reversed(self.anchors):
            anchor.close()  # type: ignore[attr-defined]


def _same_or_descendant(candidate: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((candidate, parent)) == os.fspath(parent)
    except (OSError, ValueError):
        return False


def _directory_identity(path: Path) -> tuple[int, int]:
    facts = path.stat(follow_symlinks=False)
    attributes = int(getattr(facts, "st_file_attributes", 0))
    if (
        not stat.S_ISDIR(facts.st_mode)
        or attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    ):
        raise BackupFilesystemError("A backup storage directory is unsafe.")
    return int(facts.st_dev), int(facts.st_ino)


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("backup_run_id must be a canonical UUID string.")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("backup_run_id must be a canonical UUID string.") from exc
    if canonical != value:
        raise ValueError("backup_run_id must be a canonical UUID string.")
    return value


def _backup_root(
    source: MigrationStorageBoundary,
    requested: Path,
) -> tuple[Path, str]:
    if os.name != "nt":
        raise BackupFilesystemError(
            "Knowledge Core backup storage requires the approved Windows host."
        )
    if not requested.is_absolute():
        raise BackupFilesystemError("The backup root must be absolute.")
    from ..tools.windows_volume import probe_path

    report = probe_path(requested)
    volume = report.get("volume") if isinstance(report.get("volume"), Mapping) else {}
    reparse = report.get("reparse") if isinstance(report.get("reparse"), Mapping) else {}
    if (
        report.get("eligible") is not True
        or report.get("exists") is not True
        or reparse.get("present") is not False
        or not isinstance(report.get("resolved_path"), str)
        or volume.get("drive_type_code") != 3
        or str(volume.get("filesystem", "")).casefold() != "ntfs"
        or not isinstance(volume.get("root"), str)
    ):
        raise BackupFilesystemError(
            "The backup root must be an existing non-reparse local NTFS directory."
        )
    resolved = Path(str(report["resolved_path"])).resolve(strict=True)
    if not resolved.is_dir():
        raise BackupFilesystemError("The backup root must be a directory.")
    data = Path(source.data_root.resolved_path)
    if _same_or_descendant(resolved, data) or _same_or_descendant(data, resolved):
        raise BackupFilesystemError(
            "The backup root cannot overlap canonical production storage."
        )
    source_volume = ntpath.normcase(source.data_root.volume_root)
    target_volume = ntpath.normcase(str(volume["root"]))
    if source_volume == target_volume and os.environ.get("CODE_ASSIST_TEST_MODE") != "1":
        raise BackupFilesystemError(
            "Production backups must use a volume separate from canonical storage."
        )
    return resolved, target_volume


def _file_facts(handle: int) -> tuple[int, int, int]:
    from .legacy.blob_stager import _file_information

    return _file_information(handle)


def _verify_file_handle(
    handle: int,
    *,
    expected_path: Path,
    expected_size: int | None = None,
) -> int:
    from ..tools.legacy_inventory import _windows_final_path

    if os.path.normcase(os.fspath(_windows_final_path(handle))) != os.path.normcase(
        os.fspath(expected_path.resolve(strict=True))
    ):
        raise BackupFilesystemError("A backup file escaped its pinned directory.")
    attributes, links, size = _file_facts(handle)
    if attributes & 0x400 or links != 1:
        raise BackupFilesystemError(
            "A backup file is reparse-backed or multiply linked."
        )
    if expected_size is not None and size != expected_size:
        raise BackupFilesystemError("A backup file size differs from its manifest.")
    return size


def _stream(
    source_handle: int,
    destination_handle: int | None = None,
) -> tuple[str, int]:
    import ctypes
    from ctypes import wintypes

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
    write_file = kernel32.WriteFile
    write_file.argtypes = (
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPVOID,
    )
    write_file.restype = wintypes.BOOL
    digest = hashlib.sha256()
    size = 0
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        if not read_file(
            wintypes.HANDLE(source_handle),
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        if read.value == 0:
            break
        chunk = buffer.raw[: read.value]
        digest.update(chunk)
        size += len(chunk)
        if destination_handle is not None:
            offset = 0
            while offset < len(chunk):
                written = wintypes.DWORD()
                view = ctypes.create_string_buffer(chunk[offset:])
                if not write_file(
                    wintypes.HANDLE(destination_handle),
                    view,
                    len(chunk) - offset,
                    ctypes.byref(written),
                    None,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                if written.value <= 0:
                    raise OSError("backup_write_made_no_progress")
                offset += int(written.value)
    if destination_handle is not None:
        from ..tools.legacy_inventory import _flush_windows_handle

        _flush_windows_handle(destination_handle)
    return digest.hexdigest(), size


def _open_relative(
    anchor: object,
    name: str,
    *,
    delete_access: bool = False,
) -> int:
    from .legacy.blob_stager import _nt_open_relative

    return _nt_open_relative(  # type: ignore[attr-defined]
        anchor.handle,
        name,
        delete_access=delete_access,
    )


def _close(handle: int) -> None:
    from ..tools.legacy_inventory import _close_windows_handle

    _close_windows_handle(handle)


def _open_or_create_directory(parent: object, name: str) -> object:
    from ..tools.legacy_inventory import _WindowsDirectoryAnchor

    target = parent.path / name  # type: ignore[attr-defined]
    if os.path.lexists(target):
        return _WindowsDirectoryAnchor.open(target)
    try:
        return _WindowsDirectoryAnchor.create_relative_directory(parent, name)
    except OSError:
        if not os.path.lexists(target):
            raise
        return _WindowsDirectoryAnchor.open(target)


def _open_directory(
    parent: object,
    name: str,
    *,
    delete_access: bool = False,
) -> object:
    from ..tools.legacy_inventory import _WindowsDirectoryAnchor

    target = parent.path / name  # type: ignore[attr-defined]
    if not os.path.lexists(target):
        raise BackupFilesystemError("A required backup directory is missing.")
    return _WindowsDirectoryAnchor.open(target, delete_access=delete_access)


def _atomic_write(anchor: object, name: str, payload: bytes) -> None:
    from ..tools.legacy_inventory import (
        _close_windows_handle,
        _nt_clear_disposition,
        _nt_rename_relative,
        _nt_set_disposition,
        _write_windows_handle,
    )
    import secrets

    target = anchor.path / name  # type: ignore[attr-defined]
    if os.path.lexists(target):
        raise BackupFilesystemError("A backup staging file already exists.")
    temporary_name = ".backup-write-" + secrets.token_hex(16) + ".tmp"
    handle = anchor.create_relative_file(temporary_name)  # type: ignore[attr-defined]
    committed = False
    try:
        _nt_set_disposition(handle)
        _write_windows_handle(handle, payload)
        _nt_clear_disposition(handle)
        _nt_rename_relative(handle, anchor.handle, name, replace=False)  # type: ignore[attr-defined]
        committed = True
    finally:
        if not committed:
            try:
                _nt_set_disposition(handle)
            except OSError:
                pass
        _close_windows_handle(handle)


def _snapshot_ledger(raw: sqlite3.Connection) -> None:
    catalog = CANONICAL_MIGRATION_CATALOG
    rows = raw.execute(
        """SELECT version,name,checksum_sha256,applied_at,app_version,
                  sqlite_version,sqlite_source_id
             FROM schema_migration ORDER BY version"""
    ).fetchall()
    if len(rows) != catalog.latest_version:
        raise BackupSnapshotError("The snapshot migration ledger is incomplete.")
    for index, row in enumerate(rows, start=1):
        migration = catalog.migration(index)
        if (
            tuple(row[:3]) != (index, migration.name, migration.checksum_sha256)
            or not isinstance(row[3], int)
            or row[3] < 0
            or not isinstance(row[4], str)
            or not row[4].strip()
            or row[5] != APPROVED_SQLITE_VERSION
            or row[6] != APPROVED_SQLITE_SOURCE_ID
        ):
            raise BackupSnapshotError(
                "The snapshot migration ledger differs from this build."
            )


def _snapshot_blobs(raw: sqlite3.Connection) -> tuple[SnapshotBlob, ...]:
    rows = raw.execute(
        """WITH referenced(sha256) AS (
               SELECT content_sha256 FROM artifact_blob_body
               UNION SELECT content_blob_hash FROM extraction_snapshot_body
                     WHERE content_blob_hash IS NOT NULL
               UNION SELECT content_blob_hash FROM knowledge_revision_body
                     WHERE content_blob_hash IS NOT NULL
               UNION SELECT proposed_content_blob_hash FROM change_proposal
                     WHERE proposed_content_blob_hash IS NOT NULL
           )
           SELECT r.sha256,b.size_bytes,b.media_type,b.relative_path,
                  b.verification_status
             FROM referenced r LEFT JOIN blob_object b ON b.sha256=r.sha256
            ORDER BY r.sha256"""
    ).fetchall()
    result: list[SnapshotBlob] = []
    for row in rows:
        if (
            len(row) != 5
            or not isinstance(row[0], str)
            or _SHA256_RE.fullmatch(row[0]) is None
            or isinstance(row[1], bool)
            or not isinstance(row[1], int)
            or row[1] < 0
            or not isinstance(row[2], str)
            or not row[2].strip()
            or not isinstance(row[3], str)
            or row[3] != f"{row[0][:2]}/{row[0][2:4]}/{row[0]}"
            or row[4] != "verified"
        ):
            raise BackupSnapshotError(
                "The snapshot references an invalid immutable blob."
            )
        result.append(SnapshotBlob(row[0], row[1], row[2], row[3]))
    if len({value.sha256 for value in result}) != len(result):
        raise BackupSnapshotError("The snapshot blob set is not unique.")
    return tuple(result)


def _exact_tree(root: Path, manifest: Mapping[str, object]) -> None:
    rows = manifest.get("blobs")
    if not isinstance(rows, list):
        raise BackupFilesystemError("The completed backup blob list is invalid.")
    expected_files = {
        "manifest.json",
        BACKUP_SNAPSHOT_RELATIVE_PATH,
        *(str(row["relative_path"]) for row in rows if isinstance(row, Mapping)),
    }
    expected_directories = {"database", "blobs"}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        relative = str(row.get("relative_path", ""))
        parts = relative.split("/")
        for index in range(1, len(parts)):
            expected_directories.add("/".join(parts[:index]))
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    pending = [root]
    while pending:
        parent = pending.pop()
        for child in parent.iterdir():
            facts = child.stat(follow_symlinks=False)
            attributes = int(getattr(facts, "st_file_attributes", 0))
            if attributes & int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise BackupFilesystemError(
                    "A completed backup contains a reparse entry."
                )
            relative = child.relative_to(root).as_posix()
            if stat.S_ISDIR(facts.st_mode):
                observed_directories.add(relative)
                pending.append(child)
            elif stat.S_ISREG(facts.st_mode) and int(facts.st_nlink) == 1:
                observed_files.add(relative)
            else:
                raise BackupFilesystemError(
                    "A completed backup contains an unsafe filesystem entry."
                )
    if observed_files != expected_files or observed_directories != expected_directories:
        raise BackupFilesystemError(
            "The completed backup tree differs from its manifest."
        )


class _BackupDirectory:
    __slots__ = (
        "database",
        "directory",
        "blobs",
        "root",
        "storage",
    )

    def __init__(
        self,
        storage: "FilesystemBackupStorage",
        name: str,
        *,
        create: bool,
        delete_access: bool = False,
    ) -> None:
        from ..tools.legacy_inventory import _WindowsDirectoryAnchor

        self.storage = storage
        self.root = _WindowsDirectoryAnchor.open(storage.root)
        if _directory_identity(self.root.path) != storage._root_identity:
            self.root.close()
            raise BackupFilesystemError("The backup root identity changed.")
        target = self.root.path / name
        try:
            if os.path.lexists(target):
                self.directory = _WindowsDirectoryAnchor.open(
                    target, delete_access=(create or delete_access)
                )
            elif create:
                self.directory = _WindowsDirectoryAnchor.create_relative_directory(
                    self.root, name
                )
            else:
                raise BackupFilesystemError("The backup directory is unavailable.")
            if create:
                self.database = _open_or_create_directory(self.directory, "database")
                self.blobs = _open_or_create_directory(self.directory, "blobs")
            else:
                self.database = _open_directory(
                    self.directory, "database", delete_access=delete_access
                )
                self.blobs = _open_directory(
                    self.directory, "blobs", delete_access=delete_access
                )
        except Exception:
            for value in (
                getattr(self, "blobs", None),
                getattr(self, "database", None),
                getattr(self, "directory", None),
                self.root,
            ):
                if value is not None:
                    value.close()
            raise

    @property
    def path(self) -> Path:
        return self.directory.path

    @property
    def snapshot_path(self) -> Path:
        return self.database.path / _SNAPSHOT_NAME

    @property
    def manifest_path(self) -> Path:
        return self.directory.path / "manifest.json"

    def close_children(self) -> None:
        for name in ("blobs", "database"):
            value = getattr(self, name, None)
            if value is not None:
                value.close()
                setattr(self, name, None)

    def close(self) -> None:
        self.close_children()
        if getattr(self, "directory", None) is not None:
            self.directory.close()
            self.directory = None
        if getattr(self, "root", None) is not None:
            self.root.close()
            self.root = None

    def inspect_snapshot(self) -> SnapshotObservation:
        if not self.snapshot_path.is_file():
            raise BackupSnapshotError("The backup SQLite snapshot is missing.")
        handle = _open_relative(self.database, _SNAPSHOT_NAME)
        raw: sqlite3.Connection | None = None
        try:
            size = _verify_file_handle(
                handle, expected_path=self.snapshot_path
            )
            digest, streamed_size = _stream(handle)
            if streamed_size != size:
                raise BackupSnapshotError("The snapshot changed while hashing.")
            raw = sqlite3.connect(
                f"{self.snapshot_path.as_uri()}?mode=ro&immutable=1",
                uri=True,
                isolation_level=None,
                check_same_thread=True,
                cached_statements=0,
            )
            raw.row_factory = sqlite3.Row
            quick_rows = raw.execute("PRAGMA quick_check").fetchall()
            quick = (
                "ok"
                if len(quick_rows) == 1 and tuple(quick_rows[0]) == ("ok",)
                else "failed"
            )
            foreign_key_count = len(raw.execute("PRAGMA foreign_key_check").fetchall())
            user_version = int(raw.execute("PRAGMA user_version").fetchone()[0])
            if user_version != CANONICAL_MIGRATION_CATALOG.latest_version:
                raise BackupSnapshotError("The snapshot schema version is unsupported.")
            _snapshot_ledger(raw)
            physical = schema_sha256(raw)
            expected_physical = CANONICAL_MIGRATION_CATALOG.migration(
                user_version
            ).expected_schema_sha256
            if physical != expected_physical:
                raise BackupSnapshotError("The snapshot physical schema differs.")
            audit_row = raw.execute("SELECT coalesce(max(id),0) FROM audit_event").fetchone()
            if audit_row is None or not isinstance(audit_row[0], int) or audit_row[0] < 0:
                raise BackupSnapshotError("The snapshot audit high-water is invalid.")
            blobs = _snapshot_blobs(raw)
            if quick != "ok" or foreign_key_count != 0:
                raise BackupSnapshotError("The snapshot failed SQLite integrity checks.")
            return SnapshotObservation(
                sha256=digest,
                size_bytes=size,
                audit_high_water=int(audit_row[0]),
                user_version=user_version,
                physical_schema_sha256=physical,
                quick_check=quick,
                foreign_key_violation_count=foreign_key_count,
                blobs=blobs,
            )
        except sqlite3.Error as exc:
            raise BackupSnapshotError("The backup SQLite snapshot is unreadable.") from exc
        finally:
            if raw is not None:
                raw.close()
            _close(handle)

    def create_snapshot(self, source: ManagedConnection) -> SnapshotObservation:
        if self.snapshot_path.exists():
            return self.inspect_snapshot()
        handle = self.database.create_relative_file(
            _SNAPSHOT_NAME, share_write=True, delete_access=False
        )
        try:
            facts = self.snapshot_path.stat(follow_symlinks=False)
            identity = (int(facts.st_dev), int(facts.st_ino))
            result = online_backup_to_existing_file(
                source,
                self.snapshot_path,
                expected_destination_identity=identity,
            )
            if result.destination_identity != identity:
                raise BackupSnapshotError("The online backup target identity changed.")
        finally:
            _close(handle)
        observed = self.inspect_snapshot()
        if (
            observed.size_bytes != result.file_size_bytes
            or observed.audit_high_water != result.audit_high_water
            or observed.quick_check != result.quick_check
            or observed.foreign_key_violation_count
            != result.foreign_key_violation_count
            or observed.user_version != result.user_version
        ):
            raise BackupSnapshotError(
                "The sealed snapshot differs from the online backup observation."
            )
        return observed

    def copy_blob(self, blob: SnapshotBlob) -> None:
        if not isinstance(blob, SnapshotBlob):
            raise TypeError("copy_blob requires a SnapshotBlob.")
        first = _open_or_create_directory(self.blobs, blob.sha256[:2])
        second = None
        source_root = source_first = source_second = None
        source_handle = destination_handle = None
        try:
            second = _open_or_create_directory(first, blob.sha256[2:4])
            destination = second.path / blob.sha256
            if os.path.lexists(destination):
                destination_handle = _open_relative(second, blob.sha256)
                _verify_file_handle(
                    destination_handle,
                    expected_path=destination,
                    expected_size=blob.size_bytes,
                )
                digest, size = _stream(destination_handle)
                if digest != blob.sha256 or size != blob.size_bytes:
                    raise BackupFilesystemError(
                        "A resumed backup blob differs from its snapshot identity."
                    )
                return
            from ..tools.legacy_inventory import _WindowsDirectoryAnchor

            source_root = _WindowsDirectoryAnchor.open(
                self.storage.layout.blob_root, writable=False
            )
            if (
                _directory_identity(source_root.path)
                != self.storage._source_blob_identity
            ):
                raise BackupFilesystemError("The canonical blob root identity changed.")
            source_first = _WindowsDirectoryAnchor.open(
                source_root.path / blob.sha256[:2], writable=False
            )
            source_second = _WindowsDirectoryAnchor.open(
                source_first.path / blob.sha256[2:4], writable=False
            )
            source_path = source_second.path / blob.sha256
            if os.path.normcase(os.fspath(source_path.relative_to(source_root.path))) != os.path.normcase(
                blob.source_relative_path
            ):
                raise BackupFilesystemError("The source blob path is non-canonical.")
            source_handle = _open_relative(source_second, blob.sha256)
            _verify_file_handle(
                source_handle,
                expected_path=source_path,
                expected_size=blob.size_bytes,
            )
            destination_handle = second.create_relative_file(
                blob.sha256, delete_access=False
            )
            digest, size = _stream(source_handle, destination_handle)
            if digest != blob.sha256 or size != blob.size_bytes:
                raise BackupFilesystemError(
                    "The source blob bytes differ from the snapshot manifest."
                )
            _verify_file_handle(
                destination_handle,
                expected_path=destination,
                expected_size=blob.size_bytes,
            )
        finally:
            for handle in (destination_handle, source_handle):
                if handle is not None:
                    _close(handle)
            for anchor in (source_second, source_first, source_root, second, first):
                if anchor is not None:
                    anchor.close()

    def write_manifest(self, manifest: Mapping[str, object]) -> bytes:
        if backup_manifest_errors(manifest):
            raise BackupFilesystemError("The backup manifest is invalid.")
        payload = canonical_json_bytes(manifest) + b"\n"
        if self.manifest_path.exists():
            handle = _open_relative(self.directory, "manifest.json")
            try:
                _verify_file_handle(
                    handle,
                    expected_path=self.manifest_path,
                    expected_size=len(payload),
                )
                from .legacy.blob_stager import _read_handle

                if _read_handle(handle) != payload:
                    raise BackupFilesystemError(
                        "The staged backup manifest differs from its replay."
                    )
                return payload
            finally:
                _close(handle)
        _atomic_write(self.directory, "manifest.json", payload)
        return payload

    def read_manifest(self) -> dict[str, object] | None:
        if not self.manifest_path.exists():
            return None
        handle = _open_relative(self.directory, "manifest.json")
        try:
            _verify_file_handle(handle, expected_path=self.manifest_path)
            from .legacy.blob_stager import _read_handle

            payload = _read_handle(handle)
        finally:
            _close(handle)
        if not payload.endswith(b"\n") or payload.count(b"\n") != 1:
            raise BackupFilesystemError("The backup manifest framing is invalid.")
        try:
            manifest = json.loads(payload[:-1])
        except (UnicodeDecodeError, ValueError) as exc:
            raise BackupFilesystemError("The backup manifest is unreadable.") from exc
        if backup_manifest_errors(manifest):
            raise BackupFilesystemError("The backup manifest contract is invalid.")
        return dict(manifest)


class _BackupWorkspace(_BackupDirectory):
    __slots__ = ("final_name", "staging_name")

    def __init__(self, storage: "FilesystemBackupStorage", run_id: str) -> None:
        self.staging_name = f".backup-{run_id}.partial"
        self.final_name = f"backup-{run_id}"
        if os.path.lexists(storage.root / self.final_name):
            raise BackupFilesystemError("The completed backup already exists.")
        super().__init__(storage, self.staging_name, create=True)

    def finalize(self) -> Path:
        from ..tools.legacy_inventory import _nt_rename_relative

        if not self.manifest_path.is_file() or not self.snapshot_path.is_file():
            raise BackupFilesystemError("The backup staging directory is incomplete.")
        self.close_children()
        _nt_rename_relative(
            self.directory.handle,
            self.root.handle,
            self.final_name,
            replace=False,
        )
        return self.storage.root / self.final_name


class FilesystemBackupStorage:
    """Production backup root bound to a distinct, pinned local NTFS target."""

    __slots__ = (
        "_root_identity",
        "_source_blob_identity",
        "_source_boundary",
        "_target_volume",
        "layout",
        "root",
    )

    def __init__(self, layout: MigrationStorageLayout, backup_root: Path) -> None:
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("FilesystemBackupStorage requires MigrationStorageLayout.")
        self.layout = layout
        self._source_boundary = verify_migration_storage_layout(
            layout, require_database=True
        )
        self.root, self._target_volume = _backup_root(
            self._source_boundary, Path(backup_root)
        )
        self._root_identity = _directory_identity(self.root)
        self._source_blob_identity = _directory_identity(
            self.layout.blob_root.resolve(strict=True)
        )

    def _verify(self) -> None:
        verify_migration_storage_layout(
            self.layout,
            require_database=True,
            expected=self._source_boundary,
        )
        observed, volume = _backup_root(self._source_boundary, self.root)
        if (
            observed != self.root
            or volume != self._target_volume
            or _directory_identity(self.root) != self._root_identity
            or _directory_identity(self.layout.blob_root.resolve(strict=True))
            != self._source_blob_identity
        ):
            raise BackupFilesystemError("The backup root binding changed.")

    def staging_exists(self, backup_run_id: str) -> bool:
        run_id = _canonical_uuid(backup_run_id)
        return os.path.lexists(self.root / f".backup-{run_id}.partial")

    def completed_exists(self, backup_run_id: str) -> bool:
        run_id = _canonical_uuid(backup_run_id)
        return os.path.lexists(self.root / f"backup-{run_id}")

    @contextmanager
    def restore_target(
        self,
        target_layout: MigrationStorageLayout,
    ) -> Iterator[_RestoreTargetLease]:
        """Pin every isolated target directory across copy and rebuild."""

        if not isinstance(target_layout, MigrationStorageLayout):
            raise TypeError("restore target requires MigrationStorageLayout.")
        if target_layout.database.exists() or os.path.lexists(target_layout.database):
            raise BackupFilesystemError(
                "The isolated restore database target must be absent."
            )
        boundary = verify_migration_storage_layout(
            target_layout, require_database=False
        )
        target_data = Path(boundary.data_root.resolved_path)
        source_data = Path(self._source_boundary.data_root.resolved_path)
        if (
            _same_or_descendant(target_data, self.root)
            or _same_or_descendant(self.root, target_data)
            or _same_or_descendant(target_data, source_data)
            or _same_or_descendant(source_data, target_data)
        ):
            raise BackupFilesystemError(
                "The isolated restore root overlaps source or backup storage."
            )
        if any(target_layout.blob_root.iterdir()) or any(
            target_layout.projection_root.iterdir()
        ):
            raise BackupFilesystemError(
                "The isolated restore blob and projection roots must be empty."
            )
        from ..tools.legacy_inventory import _WindowsDirectoryAnchor

        paths = (
            target_layout.data_root,
            target_layout.database.parent,
            target_layout.blob_root,
            target_layout.projection_root,
        )
        expected_paths = (
            boundary.data_root.resolved_path,
            boundary.database_parent.resolved_path,
            boundary.blob_root.resolved_path,
            boundary.projection_root.resolved_path,
        )
        identities = tuple(_directory_identity(value) for value in paths)
        opened: list[object] = []
        try:
            for path, expected, identity in zip(
                paths, expected_paths, identities, strict=True
            ):
                anchor = _WindowsDirectoryAnchor.open(
                    path, writable=False, share_write=True
                )
                if (
                    os.path.normcase(os.fspath(anchor.path.resolve(strict=True)))
                    != os.path.normcase(expected)
                    or _directory_identity(anchor.path) != identity
                ):
                    anchor.close()
                    raise BackupFilesystemError(
                        "The isolated restore target changed before pinning."
                    )
                opened.append(anchor)
            lease = _RestoreTargetLease(
                self, target_layout, boundary, tuple(opened), identities
            )
            lease.verify(require_database=False)
            yield lease
            lease.verify(require_database=True)
        finally:
            for anchor in reversed(opened):
                anchor.close()  # type: ignore[attr-defined]

    @contextmanager
    def workspace(self, backup_run_id: str) -> Iterator[_BackupWorkspace]:
        run_id = _canonical_uuid(backup_run_id)
        self._verify()
        workspace = _BackupWorkspace(self, run_id)
        try:
            yield workspace
            self._verify()
        finally:
            workspace.close()

    def verify_completed(
        self, backup_run_id: str
    ) -> tuple[dict[str, object], SnapshotObservation]:
        run_id = _canonical_uuid(backup_run_id)
        self._verify()
        directory = _BackupDirectory(self, f"backup-{run_id}", create=False)
        try:
            manifest = directory.read_manifest()
            if manifest is None:
                raise BackupFilesystemError("The completed manifest is missing.")
            if manifest.get("backup_run_id") != run_id:
                raise BackupFilesystemError("The completed manifest run binding differs.")
            _exact_tree(directory.path, manifest)
            snapshot = directory.inspect_snapshot()
            if (
                manifest.get("snapshot_db_sha256") != snapshot.sha256
                or manifest.get("snapshot_db_size_bytes") != snapshot.size_bytes
                or manifest.get("snapshot_audit_high_water")
                != snapshot.audit_high_water
                or manifest.get("physical_schema_version") != snapshot.user_version
                or manifest.get("physical_schema_sha256")
                != snapshot.physical_schema_sha256
                or manifest.get("quick_check") != snapshot.quick_check
                or manifest.get("foreign_key_violation_count")
                != snapshot.foreign_key_violation_count
            ):
                raise BackupFilesystemError(
                    "The completed manifest differs from its SQLite snapshot."
                )
            rows = manifest.get("blobs")
            if not isinstance(rows, list) or len(rows) != len(snapshot.blobs):
                raise BackupFilesystemError("The completed blob set is incomplete.")
            expected = {value.sha256: value for value in snapshot.blobs}
            for row in rows:
                digest = row["sha256"]
                blob = expected.get(digest)
                if blob is None or row["size_bytes"] != blob.size_bytes:
                    raise BackupFilesystemError("The completed blob identity differs.")
                first = _open_directory(directory.blobs, digest[:2])
                second = None
                blob_handle = None
                try:
                    second = _open_directory(first, digest[2:4])
                    path = second.path / digest
                    blob_handle = _open_relative(second, digest)
                    _verify_file_handle(
                        blob_handle,
                        expected_path=path,
                        expected_size=blob.size_bytes,
                    )
                    observed_digest, observed_size = _stream(blob_handle)
                    if observed_digest != digest or observed_size != blob.size_bytes:
                        raise BackupFilesystemError(
                            "A completed backup blob failed content verification."
                        )
                finally:
                    if blob_handle is not None:
                        _close(blob_handle)
                    if second is not None:
                        second.close()
                    first.close()
            self._verify()
            return dict(manifest), snapshot
        finally:
            directory.close()

    def delete_completed(self, backup_run_id: str) -> None:
        """Delete one fully verified retained backup through pinned handles.

        The directory is first renamed to a non-backup pruning name.  A failure
        after that point can leave only a fail-closed quarantine directory; it
        cannot leave a partially deleted tree discoverable as a restorable
        backup.
        """

        run_id = _canonical_uuid(backup_run_id)
        manifest, snapshot = self.verify_completed(run_id)
        pruning_name = f".pruning-{run_id}.partial"
        if os.path.lexists(self.root / pruning_name):
            raise BackupFilesystemError(
                "A prior quarantined retention deletion requires operator review."
            )
        directory = _BackupDirectory(
            self,
            f"backup-{run_id}",
            create=False,
            delete_access=True,
        )
        renamed = False
        try:
            replay_manifest = directory.read_manifest()
            if replay_manifest != manifest:
                raise BackupFilesystemError(
                    "The retained backup changed before deletion."
                )
            _exact_tree(directory.path, manifest)
            replay_snapshot = directory.inspect_snapshot()
            if replay_snapshot != snapshot:
                raise BackupFilesystemError(
                    "The retained snapshot changed before deletion."
                )
            from ..tools.legacy_inventory import (
                _nt_rename_relative,
                _nt_set_disposition,
            )
            from .legacy.blob_stager import _read_handle

            # Child directory handles deliberately deny delete sharing.  Close
            # them before renaming the already verified parent, then reopen
            # them relative to that still-pinned parent handle.
            directory.close_children()
            _nt_rename_relative(
                directory.directory.handle,
                directory.root.handle,
                pruning_name,
                replace=False,
            )
            renamed = True
            directory.directory.path = directory.root.path / pruning_name
            directory.database = _open_directory(
                directory.directory, "database", delete_access=True
            )
            directory.blobs = _open_directory(
                directory.directory, "blobs", delete_access=True
            )

            rows = manifest.get("blobs")
            if not isinstance(rows, list):
                raise BackupFilesystemError(
                    "The retained backup blob manifest is invalid."
                )
            pairs: set[tuple[str, str]] = set()
            for row in rows:
                if not isinstance(row, Mapping):
                    raise BackupFilesystemError(
                        "The retained backup blob row is invalid."
                    )
                digest = str(row.get("sha256", ""))
                expected_size = int(row.get("size_bytes", -1))
                first_name, second_name = digest[:2], digest[2:4]
                pairs.add((first_name, second_name))
                first = _open_directory(
                    directory.blobs, first_name, delete_access=True
                )
                second = handle = None
                try:
                    second = _open_directory(
                        first, second_name, delete_access=True
                    )
                    path = second.path / digest
                    handle = _open_relative(
                        second, digest, delete_access=True
                    )
                    _verify_file_handle(
                        handle,
                        expected_path=path,
                        expected_size=expected_size,
                    )
                    observed, size = _stream(handle)
                    if observed != digest or size != expected_size:
                        raise BackupFilesystemError(
                            "A retained blob changed before deletion."
                        )
                    _nt_set_disposition(handle)
                finally:
                    if handle is not None:
                        _close(handle)
                    if second is not None:
                        second.close()
                    first.close()

            snapshot_handle = _open_relative(
                directory.database,
                _SNAPSHOT_NAME,
                delete_access=True,
            )
            try:
                _verify_file_handle(
                    snapshot_handle,
                    expected_path=directory.snapshot_path,
                    expected_size=snapshot.size_bytes,
                )
                observed, size = _stream(snapshot_handle)
                if observed != snapshot.sha256 or size != snapshot.size_bytes:
                    raise BackupFilesystemError(
                        "The retained snapshot changed before deletion."
                    )
                _nt_set_disposition(snapshot_handle)
            finally:
                _close(snapshot_handle)

            expected_manifest = canonical_json_bytes(manifest) + b"\n"
            manifest_handle = _open_relative(
                directory.directory,
                "manifest.json",
                delete_access=True,
            )
            try:
                _verify_file_handle(
                    manifest_handle,
                    expected_path=directory.manifest_path,
                    expected_size=len(expected_manifest),
                )
                if _read_handle(manifest_handle) != expected_manifest:
                    raise BackupFilesystemError(
                        "The retained manifest changed before deletion."
                    )
                _nt_set_disposition(manifest_handle)
            finally:
                _close(manifest_handle)

            for first_name, second_name in sorted(pairs, reverse=True):
                first = _open_directory(
                    directory.blobs, first_name, delete_access=True
                )
                second = None
                try:
                    second = _open_directory(
                        first, second_name, delete_access=True
                    )
                    if any(second.path.iterdir()):
                        raise BackupFilesystemError(
                            "A retained blob directory is not empty after deletion."
                        )
                    second.dispose()
                finally:
                    if second is not None:
                        second.close()
                    first.close()
            for first_name in sorted({value[0] for value in pairs}, reverse=True):
                first = _open_directory(
                    directory.blobs, first_name, delete_access=True
                )
                try:
                    if any(first.path.iterdir()):
                        raise BackupFilesystemError(
                            "A retained blob prefix is not empty after deletion."
                        )
                    first.dispose()
                finally:
                    first.close()

            if any(directory.database.path.iterdir()) or any(
                directory.blobs.path.iterdir()
            ):
                raise BackupFilesystemError(
                    "The retained backup contains unexpected deletion residue."
                )
            directory.database.dispose()
            directory.blobs.dispose()
            directory.close_children()
            if any(directory.directory.path.iterdir()):
                raise BackupFilesystemError(
                    "The retained backup root contains deletion residue."
                )
            directory.directory.dispose()
        finally:
            directory.close()
        self._verify()
        if os.path.lexists(self.root / f"backup-{run_id}") or os.path.lexists(
            self.root / pruning_name
        ):
            qualifier = "quarantined " if renamed else ""
            raise BackupFilesystemError(
                f"The {qualifier}retained backup was not fully deleted."
            )

    def restore_completed(
        self,
        backup_run_id: str,
        target: _RestoreTargetLease,
    ) -> tuple[dict[str, object], SnapshotObservation, MigrationStorageBoundary]:
        """Copy a verified backup into an empty isolated canonical layout."""

        if not isinstance(target, _RestoreTargetLease) or target.storage is not self:
            raise TypeError("restore target requires this storage's active lease.")
        target_layout = target.layout
        target.verify(require_database=False)
        manifest, snapshot = self.verify_completed(backup_run_id)
        run_id = _canonical_uuid(backup_run_id)
        source = _BackupDirectory(self, f"backup-{run_id}", create=False)
        database_parent = target_blob_root = None
        source_snapshot_handle = target_snapshot_handle = None
        try:
            from ..tools.legacy_inventory import _WindowsDirectoryAnchor

            database_parent = _WindowsDirectoryAnchor.open(
                target_layout.database.parent
            )
            target_blob_root = _WindowsDirectoryAnchor.open(target_layout.blob_root)
            target_snapshot_handle = database_parent.create_relative_file(
                target_layout.database.name,
                delete_access=False,
            )
            source_snapshot_handle = _open_relative(source.database, _SNAPSHOT_NAME)
            _verify_file_handle(
                source_snapshot_handle,
                expected_path=source.snapshot_path,
                expected_size=snapshot.size_bytes,
            )
            digest, size = _stream(source_snapshot_handle, target_snapshot_handle)
            if digest != snapshot.sha256 or size != snapshot.size_bytes:
                raise BackupFilesystemError(
                    "The restored SQLite bytes differ from the verified snapshot."
                )
            _verify_file_handle(
                target_snapshot_handle,
                expected_path=target_layout.database,
                expected_size=snapshot.size_bytes,
            )
            _close(source_snapshot_handle)
            source_snapshot_handle = None
            _close(target_snapshot_handle)
            target_snapshot_handle = None

            rows = manifest.get("blobs")
            if not isinstance(rows, list):
                raise BackupFilesystemError("The restore blob manifest is invalid.")
            for row in rows:
                digest = str(row["sha256"])
                expected_size = int(row["size_bytes"])
                source_first = _open_directory(source.blobs, digest[:2])
                source_second = target_first = target_second = None
                source_handle = target_handle = None
                try:
                    source_second = _open_directory(source_first, digest[2:4])
                    target_first = _open_or_create_directory(
                        target_blob_root, digest[:2]
                    )
                    target_second = _open_or_create_directory(
                        target_first, digest[2:4]
                    )
                    source_path = source_second.path / digest
                    target_path = target_second.path / digest
                    source_handle = _open_relative(source_second, digest)
                    _verify_file_handle(
                        source_handle,
                        expected_path=source_path,
                        expected_size=expected_size,
                    )
                    target_handle = target_second.create_relative_file(
                        digest, delete_access=False
                    )
                    observed, observed_size = _stream(source_handle, target_handle)
                    if observed != digest or observed_size != expected_size:
                        raise BackupFilesystemError(
                            "A restored blob differs from its verified backup."
                        )
                    _verify_file_handle(
                        target_handle,
                        expected_path=target_path,
                        expected_size=expected_size,
                    )
                finally:
                    for handle in (target_handle, source_handle):
                        if handle is not None:
                            _close(handle)
                    for anchor in (
                        target_second,
                        target_first,
                        source_second,
                        source_first,
                    ):
                        if anchor is not None:
                            anchor.close()
        finally:
            for handle in (target_snapshot_handle, source_snapshot_handle):
                if handle is not None:
                    _close(handle)
            if target_blob_root is not None:
                target_blob_root.close()
            if database_parent is not None:
                database_parent.close()
            source.close()

        raw: sqlite3.Connection | None = None
        try:
            raw = sqlite3.connect(
                f"{target_layout.database.as_uri()}?mode=rw",
                uri=True,
                isolation_level=None,
                check_same_thread=True,
                cached_statements=0,
            )
            returned = raw.execute("PRAGMA journal_mode=WAL").fetchone()
            if returned is None or str(returned[0]).casefold() != "wal":
                raise BackupFilesystemError(
                    "The restored database could not enter canonical WAL mode."
                )
            raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as exc:
            raise BackupFilesystemError(
                "The restored database could not be activated."
            ) from exc
        finally:
            if raw is not None:
                raw.close()
        restored_boundary = verify_migration_storage_layout(
            target_layout,
            require_database=True,
            expected=target.boundary,
        )
        target.verify(require_database=True)
        self._verify()
        return manifest, snapshot, restored_boundary


__all__ = [
    "BackupFilesystemError",
    "BackupSnapshotError",
    "FilesystemBackupStorage",
    "SnapshotBlob",
    "SnapshotObservation",
]
