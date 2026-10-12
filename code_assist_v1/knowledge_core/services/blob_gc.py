"""Non-scheduling P5.6 asynchronous blob-GC execution boundary."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from typing import Protocol, Literal

from ..db import ConnectionFactory, db_now_ms, transaction
from ..errors import KnowledgeCoreDatabasePathError, KnowledgeCoreError
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..repositories.blob_gc import BlobGcClaim, BlobGcIntegrityError, BlobGcRepository


_SHA_RE = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class BlobGcStorage(Protocol):
    """Root-bound provider that verifies expected identity before deletion."""

    def delete_verified(
        self,
        *,
        sha256: str,
        size_bytes: int,
        relative_path: str,
    ) -> Literal["deleted", "missing_verified"]: ...


class BlobGcServiceError(KnowledgeCoreError):
    code = "INTERNAL_ERROR"
    http_status = 500
    retryable = True


class BlobGcStorageError(BlobGcServiceError):
    pass


class BlobGcFilesystemError(KnowledgeCoreDatabasePathError):
    """The root-bound CAS object could not be verified and deleted safely."""

    code = "BLOB_GC_STORAGE_INTEGRITY_ERROR"


def _open_delete_handle(path: Path) -> int | None:
    """Open one non-following file handle or return None for exact absence."""

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
    raw = create_file(
        str(path),
        0x80000000 | 0x00010000 | 0x0080 | 0x00100000,
        0x0001,  # read sharing only; deny write/delete/rename while verified.
        None,
        3,  # OPEN_EXISTING
        0x00200000 | 0x00000080,  # OPEN_REPARSE_POINT | SEQUENTIAL_SCAN
        None,
    )
    if raw in (None, ctypes.c_void_p(-1).value):
        error = ctypes.get_last_error()
        if error in {2, 3}:  # ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND
            return None
        raise ctypes.WinError(error)
    return int(raw)


def _handle_facts(handle: int) -> tuple[int, int, int]:
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
    size = (int(information.size_high) << 32) | int(information.size_low)
    return int(information.attributes), int(information.links), size


def _hash_handle(handle: int) -> tuple[str, int]:
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
    digest = hashlib.sha256()
    total = 0
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        if not read_file(
            wintypes.HANDLE(handle), buffer, len(buffer), ctypes.byref(read), None
        ):
            error = ctypes.get_last_error()
            if error == 38:  # ERROR_HANDLE_EOF
                break
            raise ctypes.WinError(error)
        if read.value == 0:
            break
        chunk = buffer.raw[: read.value]
        digest.update(chunk)
        total += len(chunk)
    return digest.hexdigest(), total


class FilesystemBlobGcStorage:
    """Delete one exact single-link CAS object through a pinned Windows handle."""

    __slots__ = ("_boundary", "_layout", "_root")

    def __init__(self, layout: MigrationStorageLayout) -> None:
        if os.name != "nt":
            raise BlobGcFilesystemError(
                "Filesystem blob GC requires the approved Windows host."
            )
        if not isinstance(layout, MigrationStorageLayout):
            raise TypeError("FilesystemBlobGcStorage requires a storage layout.")
        self._layout = layout
        self._boundary = verify_migration_storage_layout(
            layout, require_database=True
        )
        self._root = Path(self._boundary.blob_root.resolved_path)

    def _verify_layout(self) -> MigrationStorageBoundary:
        try:
            return verify_migration_storage_layout(
                self._layout,
                require_database=True,
                expected=self._boundary,
            )
        except Exception as exc:
            raise BlobGcFilesystemError(
                "The canonical storage layout changed before blob deletion."
            ) from exc

    def delete_verified(
        self,
        *,
        sha256: str,
        size_bytes: int,
        relative_path: str,
    ) -> Literal["deleted", "missing_verified"]:
        if (
            not isinstance(sha256, str)
            or _SHA_RE.fullmatch(sha256) is None
            or isinstance(size_bytes, bool)
            or not isinstance(size_bytes, int)
            or size_bytes < 0
            or relative_path != f"{sha256[:2]}/{sha256[2:4]}/{sha256}"
        ):
            raise BlobGcFilesystemError("The requested CAS identity is invalid.")
        self._verify_layout()

        from ...tools.legacy_inventory import (
            _WindowsDirectoryAnchor,
            _close_windows_handle,
            _nt_set_disposition,
            _windows_final_path,
        )

        first_path = self._root / sha256[:2]
        second_path = first_path / sha256[2:4]
        target = second_path / sha256
        root_anchor = _WindowsDirectoryAnchor.open(self._root)
        first_anchor = None
        second_anchor = None
        handle: int | None = None
        missing = False
        deleted = False
        try:
            try:
                first_anchor = _WindowsDirectoryAnchor.open(first_path)
                second_anchor = _WindowsDirectoryAnchor.open(second_path)
            except FileNotFoundError:
                missing = True
            except OSError as exc:
                if getattr(exc, "winerror", None) in {2, 3}:
                    missing = True
                else:
                    raise
            if not missing:
                handle = _open_delete_handle(target)
                if handle is None:
                    missing = True
                else:
                    final_path = _windows_final_path(handle)
                    if os.path.normcase(str(final_path)) != os.path.normcase(str(target)):
                        raise BlobGcFilesystemError(
                            "The CAS object final path escaped its bound root."
                        )
                    attributes, links, observed_size = _handle_facts(handle)
                    if attributes & 0x400 or links != 1 or observed_size != size_bytes:
                        raise BlobGcFilesystemError(
                            "The CAS object is reparse-backed, multiply linked, or resized."
                        )
                    observed_sha256, streamed_size = _hash_handle(handle)
                    if observed_sha256 != sha256 or streamed_size != size_bytes:
                        raise BlobGcFilesystemError(
                            "The CAS object bytes do not match their verified identity."
                        )
                    # Recheck immutable handle facts immediately before marking
                    # the opened object delete-pending. No path-based unlink is used.
                    attributes, links, observed_size = _handle_facts(handle)
                    if attributes & 0x400 or links != 1 or observed_size != size_bytes:
                        raise BlobGcFilesystemError(
                            "The CAS object identity changed before deletion."
                        )
                    self._verify_layout()
                    _nt_set_disposition(handle)
                    deleted = True
        except BlobGcFilesystemError:
            raise
        except Exception as exc:
            raise BlobGcFilesystemError(
                "The CAS object could not be verified and deleted."
            ) from exc
        finally:
            if handle is not None:
                try:
                    _close_windows_handle(handle)
                except OSError:
                    if not deleted:
                        raise
            if second_anchor is not None:
                second_anchor.close()
            if first_anchor is not None:
                first_anchor.close()
            root_anchor.close()
        self._verify_layout()
        if deleted:
            if os.path.lexists(target):
                raise BlobGcFilesystemError(
                    "The CAS path was recreated before deletion completed."
                )
            return "deleted"
        if missing:
            return "missing_verified"
        raise BlobGcFilesystemError("The CAS deletion outcome is unavailable.")


@dataclass(frozen=True, slots=True)
class BlobGcRunResult:
    status: Literal["idle", "deleted", "missing_verified", "retry", "dead"]
    blob_sha256: str | None
    attempt_count: int | None


class BlobGcService:
    """Claim one candidate, perform I/O outside SQLite, then finalize briefly.

    The service owns no timer, scheduler, thread, or worker registration.  A P7
    worker may call ``run_once`` explicitly; importing or constructing this
    object never starts GC.
    """

    __slots__ = ("_factory", "_repository", "_storage")

    def __init__(
        self,
        factory: ConnectionFactory,
        storage: BlobGcStorage,
        *,
        repository: BlobGcRepository | None = None,
    ) -> None:
        if not isinstance(factory, ConnectionFactory):
            raise TypeError("BlobGcService requires a ConnectionFactory.")
        if not callable(getattr(storage, "delete_verified", None)):
            raise TypeError("BlobGcService requires a verified-delete provider.")
        self._factory = factory
        self._storage = storage
        self._repository = repository or BlobGcRepository()

    def run_once(self, *, claim_owner: str) -> BlobGcRunResult:
        connection = self._factory.open()
        try:
            with transaction(connection):
                claim = self._repository.claim_next(
                    connection,
                    claim_owner=claim_owner,
                    now_ms=db_now_ms(connection),
                )
        except BlobGcIntegrityError as exc:
            raise BlobGcServiceError(str(exc)) from exc
        finally:
            connection.close()
        if claim is None:
            return BlobGcRunResult("idle", None, None)

        try:
            outcome = self._storage.delete_verified(
                sha256=claim.blob_sha256,
                size_bytes=claim.size_bytes,
                relative_path=claim.relative_path,
            )
            if outcome not in {"deleted", "missing_verified"}:
                raise ValueError("invalid verified-delete outcome")
        except Exception as exc:
            self._record_failure(claim, retryable=True, error_code="BLOB_DELETE_FAILED")
            raise BlobGcStorageError("Verified blob deletion failed.") from exc

        connection = self._factory.open()
        try:
            with transaction(connection):
                self._repository.finalize_deleted(
                    connection, claim, now_ms=db_now_ms(connection)
                )
        except BlobGcIntegrityError as exc:
            # The physical path is already absent.  Preserve the claimed row;
            # expiry/retry lets a later idempotent missing_verified pass finish
            # only after the canonical/backup fences are again satisfied.
            raise BlobGcServiceError(str(exc)) from exc
        finally:
            connection.close()
        return BlobGcRunResult(outcome, claim.blob_sha256, claim.attempt_count)

    def _record_failure(
        self,
        claim: BlobGcClaim,
        *,
        retryable: bool,
        error_code: str,
    ) -> None:
        connection = self._factory.open()
        try:
            with transaction(connection):
                self._repository.record_failure(
                    connection,
                    claim,
                    now_ms=db_now_ms(connection),
                    retryable=retryable,
                    error_code=error_code,
                )
        except BlobGcIntegrityError as exc:
            raise BlobGcServiceError(str(exc)) from exc
        finally:
            connection.close()


__all__ = [
    "BlobGcFilesystemError",
    "BlobGcRunResult",
    "BlobGcService",
    "BlobGcServiceError",
    "BlobGcStorage",
    "BlobGcStorageError",
    "FilesystemBlobGcStorage",
]
