"""Windows, content-addressed blob staging for the offline P2.3 importer.

All filesystem work completes before SQLite is opened.  A verified file handle
is retained with write/delete sharing denied until the service-owned database
transaction commits or rolls back, closing the verification-to-commit race
without performing file I/O inside that transaction.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import time
from typing import Iterator

from ..errors import KnowledgeCoreDatabasePathError
from ..migrations.storage_preflight import (
    MigrationStorageBoundary,
    MigrationStorageLayout,
    verify_migration_storage_layout,
)
from ..repositories.artifacts import StagedVerifiedBlob
from ..repositories.migration import VerifiedBlobEvidence
from .content_importer import StagedBlobObservation


class LegacyBlobStagingError(KnowledgeCoreDatabasePathError):
    code = "LEGACY_BLOB_STAGING_FAILED"


class _WindowsBlobLease:
    __slots__ = ("_handle", "_closed")

    def __init__(self, handle: int) -> None:
        self._handle = handle
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        from ...tools.legacy_inventory import _close_windows_handle

        _close_windows_handle(self._handle)


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


def _nt_open_relative(
    root_handle: int,
    name: str,
    *,
    delete_access: bool = False,
    directory: bool = False,
    writable: bool = False,
    share_write: bool = False,
) -> int:
    import ctypes
    from ctypes import wintypes

    from ...tools.legacy_inventory import (
        _directory_anchor_access_mask,
        _validate_simple_windows_component,
    )

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
    desired_access = (
        _directory_anchor_access_mask(
            writable=writable,
            delete_access=delete_access,
        )
        if directory
        else 0x0001 | 0x0080 | 0x00100000 | (0x00010000 if delete_access else 0)
    )
    status = int(
        create_file(
            ctypes.byref(handle),
            desired_access,
            ctypes.byref(attributes),
            ctypes.byref(io_status),
            None,
            0x80,
            0x0001 | (0x0002 if share_write else 0),
            # Directory anchors may share child-write access because every
            # traversal remains relative to the retained no-follow handle.
            # Blob handles keep FILE_SHARE_READ only through the DB outcome.
            1,  # FILE_OPEN
            0x00000020
            | 0x00200000
            | (0x00000001 if directory else 0x00000040),
            None,
            0,
        )
    )
    _raise_ntstatus(status, "NtCreateFile(open verified blob)")
    if handle.value is None:
        raise OSError("verified_blob_missing_handle")
    return int(handle.value)


def _file_information(handle: int) -> tuple[int, int, int]:
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


def _read_handle(handle: int) -> bytes:
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
    result: list[bytes] = []
    while True:
        buffer = ctypes.create_string_buffer(1024 * 1024)
        read = wintypes.DWORD()
        if not read_file(
            wintypes.HANDLE(handle),
            buffer,
            len(buffer),
            ctypes.byref(read),
            None,
        ):
            error = ctypes.get_last_error()
            if error == 38:  # ERROR_HANDLE_EOF
                break
            raise ctypes.WinError(error)
        if read.value == 0:
            break
        result.append(buffer.raw[: read.value])
    return b"".join(result)


_RACE_RETRY_LIMIT = 64
_RACE_RETRY_SECONDS = 0.002
_NOT_FOUND_ERRORS = frozenset({2, 3})
_SHARING_ERRORS = frozenset({32})


def _windows_error_code(exc: OSError) -> int | None:
    value = getattr(exc, "winerror", None)
    if isinstance(value, int):
        return value
    return exc.errno if isinstance(exc.errno, int) else None


def _verify_directory_handle(handle: int, *, expected_path: Path) -> None:
    from ...tools.legacy_inventory import _windows_final_path

    final = _windows_final_path(handle)
    if os.path.normcase(str(final)) != os.path.normcase(
        str(expected_path.resolve(strict=True))
    ):
        raise LegacyBlobStagingError(
            "A CAS shard directory changed outside the bound blob root."
        )
    attributes, _links, _size = _file_information(handle)
    if attributes & 0x400 or not attributes & 0x10:
        raise LegacyBlobStagingError(
            "A CAS shard directory is reparse-backed or not a directory."
        )


def _open_existing_child_anchor(
    parent: object,
    name: str,
    *,
    writable: bool = True,
) -> object:
    from ...tools.legacy_inventory import _WindowsDirectoryAnchor

    child_path = parent.path / name  # type: ignore[attr-defined]
    last_error: OSError | None = None
    for attempt in range(_RACE_RETRY_LIMIT):
        handle: int | None = None
        try:
            handle = _nt_open_relative(
                parent.handle,  # type: ignore[attr-defined]
                name,
                directory=True,
                writable=writable,
                share_write=True,
            )
            _verify_directory_handle(handle, expected_path=child_path)
            return _WindowsDirectoryAnchor(path=child_path, handle=handle)
        except Exception as exc:
            if handle is not None:
                from ...tools.legacy_inventory import _close_windows_handle

                _close_windows_handle(handle)
            if not isinstance(exc, OSError):
                raise
            last_error = exc
            if _windows_error_code(exc) not in _SHARING_ERRORS:
                raise
            if attempt + 1 < _RACE_RETRY_LIMIT:
                time.sleep(_RACE_RETRY_SECONDS)
    if last_error is not None:  # pragma: no cover - loop invariant
        raise last_error
    raise OSError("cas_shard_open_retry_exhausted")  # pragma: no cover


@contextmanager
def _child_anchor(parent: object, name: str) -> Iterator[object]:
    from ...tools.legacy_inventory import _WindowsDirectoryAnchor

    child_path = parent.path / name  # type: ignore[attr-defined]
    child = None
    try:
        child = _WindowsDirectoryAnchor.create_relative_directory(
            parent,
            name,
            share_write=True,
        )
        _verify_directory_handle(child.handle, expected_path=child_path)
    except Exception as create_error:
        if child is not None:
            child.close()
            child = None
        if not isinstance(create_error, OSError):
            raise
        if _windows_error_code(create_error) not in {2, 3, 32, 80, 183}:
            raise
        child = _open_existing_child_anchor(parent, name)
    try:
        yield child
    finally:
        child.close()


def _open_existing_blob_with_retry(root_handle: int, name: str) -> int:
    last_error: OSError | None = None
    for attempt in range(_RACE_RETRY_LIMIT):
        try:
            return _nt_open_relative(root_handle, name)
        except OSError as exc:
            last_error = exc
            if _windows_error_code(exc) not in (_NOT_FOUND_ERRORS | _SHARING_ERRORS):
                raise
            if attempt + 1 < _RACE_RETRY_LIMIT:
                time.sleep(_RACE_RETRY_SECONDS)
    if last_error is not None:  # pragma: no cover - loop invariant
        raise last_error
    raise OSError("cas_blob_open_retry_exhausted")  # pragma: no cover


class FilesystemLegacyBlobStager:
    """Stage immutable CAS bytes under an exact migration-storage binding."""

    __slots__ = ("_boundary", "_layout", "_root")

    def __init__(self, layout: MigrationStorageLayout) -> None:
        if os.name != "nt":
            raise LegacyBlobStagingError(
                "The P2.3 filesystem stager requires the approved Windows host."
            )
        self._layout = layout
        self._boundary = verify_migration_storage_layout(
            layout, require_database=True
        )
        self._root = layout.blob_root.resolve(strict=True)

    @property
    def resolved_root(self) -> str:
        return str(self._root)

    def _verify_layout(self) -> MigrationStorageBoundary:
        return verify_migration_storage_layout(
            self._layout,
            require_database=True,
            expected=self._boundary,
        )

    def _verify_open_handle(
        self,
        handle: int,
        *,
        expected_path: Path,
        expected_sha256: str,
        expected_size: int,
        read_content: bool,
    ) -> None:
        from ...tools.legacy_inventory import _windows_final_path

        final = _windows_final_path(handle)
        if os.path.normcase(str(final)) != os.path.normcase(
            str(expected_path.resolve(strict=True))
        ):
            raise LegacyBlobStagingError(
                "The staged blob final path changed outside the CAS root."
            )
        attributes, links, size = _file_information(handle)
        if attributes & 0x400 or links != 1 or size != expected_size:
            raise LegacyBlobStagingError(
                "The staged blob is reparse-backed, multiply linked, or truncated."
            )
        if read_content:
            content = _read_handle(handle)
            if (
                len(content) != expected_size
                or hashlib.sha256(content).hexdigest() != expected_sha256
            ):
                raise LegacyBlobStagingError(
                    "The staged blob bytes do not match their content address."
                )

    def stage_and_verify(
        self,
        content: bytes,
        *,
        expected_sha256: str,
        media_type: str,
        effective_at_ms: int,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> StagedBlobObservation:
        if (
            not isinstance(content, bytes)
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            raise LegacyBlobStagingError(
                "Blob bytes do not match the reviewed content address."
            )
        self._verify_layout()
        from ...tools.legacy_inventory import (
            _WindowsDirectoryAnchor,
            _close_windows_handle,
            _nt_clear_disposition,
            _nt_rename_relative,
            _nt_set_disposition,
            _pinned_directory,
            _write_windows_handle,
        )

        first = expected_sha256[:2]
        second = expected_sha256[2:4]
        handle: int | None = None
        with _pinned_directory(self._root, share_write=True) as root_anchor:
            _verify_directory_handle(root_anchor.handle, expected_path=self._root)
            with _child_anchor(root_anchor, first) as first_anchor:
                with _child_anchor(first_anchor, second) as leaf_anchor:
                    target = leaf_anchor.path / expected_sha256
                    try:
                        handle = _nt_open_relative(
                            leaf_anchor.handle,
                            expected_sha256,
                        )
                    except OSError as open_error:
                        if _windows_error_code(open_error) not in _NOT_FOUND_ERRORS:
                            raise
                    if handle is not None:
                        try:
                            self._verify_open_handle(
                                handle,
                                expected_path=target,
                                expected_sha256=expected_sha256,
                                expected_size=len(content),
                                read_content=True,
                            )
                        except Exception:
                            _close_windows_handle(handle)
                            handle = None
                            raise
                    else:
                        temporary = ".p2-3-blob-" + secrets.token_hex(16) + ".tmp"
                        created: int | None = leaf_anchor.create_relative_file(temporary)
                        committed = False
                        try:
                            _nt_set_disposition(created)
                            _write_windows_handle(created, content)
                            _nt_clear_disposition(created)
                            _nt_rename_relative(
                                created,
                                leaf_anchor.handle,
                                expected_sha256,
                                replace=False,
                            )
                            committed = True
                            self._verify_open_handle(
                                created,
                                expected_path=target,
                                expected_sha256=expected_sha256,
                                expected_size=len(content),
                                read_content=False,
                            )
                            # The create/rename handle needs DELETE access.  A
                            # long-lived verification handle must not retain
                            # that access or share it, so close and immediately
                            # reopen the final object read-only, then hash it
                            # again before returning the retained lease.
                            _close_windows_handle(created)
                            created = None
                            handle = _open_existing_blob_with_retry(
                                leaf_anchor.handle, expected_sha256
                            )
                            self._verify_open_handle(
                                handle,
                                expected_path=target,
                                expected_sha256=expected_sha256,
                                expected_size=len(content),
                                read_content=True,
                            )
                        except OSError:
                            if not committed:
                                try:
                                    if created is not None:
                                        _nt_set_disposition(created)
                                finally:
                                    if created is not None:
                                        _close_windows_handle(created)
                                        created = None
                                # A concurrent identical CAS writer may have
                                # won.  Open and verify that exact winner.
                                handle = _open_existing_blob_with_retry(
                                    leaf_anchor.handle, expected_sha256
                                )
                                try:
                                    self._verify_open_handle(
                                        handle,
                                        expected_path=target,
                                        expected_sha256=expected_sha256,
                                        expected_size=len(content),
                                        read_content=True,
                                    )
                                except Exception:
                                    _close_windows_handle(handle)
                                    handle = None
                                    raise
                            else:
                                if created is not None:
                                    _close_windows_handle(created)
                                if handle is not None:
                                    _close_windows_handle(handle)
                                handle = None
                                raise
        if handle is None:
            raise LegacyBlobStagingError("The staged blob handle was not retained.")
        try:
            self._verify_layout()
        except Exception:
            _WindowsBlobLease(handle).close()
            raise
        relative = f"{first}/{second}/{expected_sha256}"
        return StagedBlobObservation(
            blob=StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                relative_path=relative,
                created_at_ms=effective_at_ms,
                verified_at_ms=effective_at_ms,
            ),
            evidence=VerifiedBlobEvidence(
                raw_sha256=expected_sha256,
                size_bytes=len(content),
                media_type=media_type,
                normalized_content_sha256=normalized_content_sha256,
                normalizer_version=normalizer_version,
            ),
            lease=_WindowsBlobLease(handle),
        )

    def verify_existing(
        self,
        *,
        expected_sha256: str,
        expected_size: int,
        media_type: str,
        normalized_content_sha256: str | None,
        normalizer_version: str | None,
    ) -> tuple[StagedBlobObservation, bytes]:
        """Observe an existing CAS object without creating or changing it."""

        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(value not in "0123456789abcdef" for value in expected_sha256)
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or not isinstance(media_type, str)
            or not media_type.strip()
        ):
            raise LegacyBlobStagingError("Existing blob evidence is invalid.")
        self._verify_layout()
        from ...tools.legacy_inventory import _WindowsDirectoryAnchor

        first = expected_sha256[:2]
        second = expected_sha256[2:4]
        target = self._root / first / second / expected_sha256
        handle: int | None = None
        root_anchor = _WindowsDirectoryAnchor.open(
            self._root,
            writable=False,
            share_write=True,
        )
        first_anchor = None
        second_anchor = None
        try:
            _verify_directory_handle(root_anchor.handle, expected_path=self._root)
            first_anchor = _open_existing_child_anchor(
                root_anchor,
                first,
                writable=False,
            )
            second_anchor = _open_existing_child_anchor(
                first_anchor,
                second,
                writable=False,
            )
            handle = _open_existing_blob_with_retry(
                second_anchor.handle,
                expected_sha256,
            )
            self._verify_open_handle(
                handle,
                expected_path=target,
                expected_sha256=expected_sha256,
                expected_size=expected_size,
                read_content=False,
            )
            content = _read_handle(handle)
            if (
                len(content) != expected_size
                or hashlib.sha256(content).hexdigest() != expected_sha256
            ):
                raise LegacyBlobStagingError(
                    "The existing CAS bytes do not match their content address."
                )
            if normalizer_version is None:
                if normalized_content_sha256 is not None:
                    raise LegacyBlobStagingError(
                        "Normalized evidence requires a normalizer."
                    )
            elif normalizer_version == "markdown_lf_v1":
                observed_normalized = hashlib.sha256(
                    content.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                ).hexdigest()
                if observed_normalized != normalized_content_sha256:
                    raise LegacyBlobStagingError(
                        "The existing CAS normalized digest differs."
                    )
            else:
                raise LegacyBlobStagingError(
                    "The existing CAS normalizer is unsupported."
                )
        except Exception as exc:
            if handle is not None:
                _WindowsBlobLease(handle).close()
                handle = None
            if (
                isinstance(exc, OSError)
                and _windows_error_code(exc) in _NOT_FOUND_ERRORS
            ):
                raise LegacyBlobStagingError(
                    "The expected CAS object is missing."
                ) from None
            raise
        finally:
            if second_anchor is not None:
                second_anchor.close()
            if first_anchor is not None:
                first_anchor.close()
            root_anchor.close()
        if handle is None:
            raise LegacyBlobStagingError("The existing CAS handle was not retained.")
        try:
            self._verify_layout()
        except Exception:
            _WindowsBlobLease(handle).close()
            raise
        relative = f"{first}/{second}/{expected_sha256}"
        observation = StagedBlobObservation(
            blob=StagedVerifiedBlob(
                sha256=expected_sha256,
                size_bytes=expected_size,
                media_type=media_type,
                relative_path=relative,
                created_at_ms=0,
                verified_at_ms=0,
            ),
            evidence=VerifiedBlobEvidence(
                raw_sha256=expected_sha256,
                size_bytes=expected_size,
                media_type=media_type,
                normalized_content_sha256=normalized_content_sha256,
                normalizer_version=normalizer_version,
            ),
            lease=_WindowsBlobLease(handle),
        )
        return observation, content



__all__ = ["FilesystemLegacyBlobStager", "LegacyBlobStagingError"]
