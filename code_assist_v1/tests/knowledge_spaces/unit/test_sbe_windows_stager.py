from __future__ import annotations

import hashlib
import multiprocessing
import os
from pathlib import Path
import queue
from typing import Any

import pytest

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.legacy.blob_stager import (
    FilesystemLegacyBlobStager,
    LegacyBlobStagingError,
)
from code_assist_v1.knowledge_core.legacy import blob_stager
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
    SingleMigrator,
)
from code_assist_v1.tools.legacy_inventory import _directory_anchor_access_mask
from code_assist_v1.tools import legacy_inventory


pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name != "nt", reason="Windows handle contract"),
]


def _storage_layout_from_paths(paths: tuple[str, str, str, str]) -> MigrationStorageLayout:
    data_root, database, blob_root, projection_root = paths
    return MigrationStorageLayout(
        data_root=Path(data_root),
        database=Path(database),
        blob_root=Path(blob_root),
        projection_root=Path(projection_root),
    )


def _storage_paths(layout: Any) -> tuple[str, str, str, str]:
    return (
        os.fspath(layout.root),
        os.fspath(layout.core_db_path),
        os.fspath(layout.blobs_dir),
        os.fspath(layout.projections_dir),
    )


def _initialize_layout(layout: Any) -> MigrationStorageLayout:
    storage = _storage_layout_from_paths(_storage_paths(layout))
    factory = ConnectionFactory._for_isolated_tests(
        layout.core_db_path,
        CANONICAL_MIGRATION_CATALOG,
    )
    SingleMigrator(
        factory,
        app_version="sbe-3.0-windows-stager-test",
        storage_layout=storage,
    ).run()
    return storage


def _stage_process(
    paths: tuple[str, str, str, str],
    content: bytes,
    start: Any,
    results: Any,
) -> None:
    try:
        if not start.wait(20):
            raise TimeoutError("start_barrier_timeout")
        digest = hashlib.sha256(content).hexdigest()
        observation = FilesystemLegacyBlobStager(
            _storage_layout_from_paths(paths)
        ).stage_and_verify(
            content,
            expected_sha256=digest,
            media_type="application/octet-stream",
            effective_at_ms=100,
            normalized_content_sha256=None,
            normalizer_version=None,
        )
        try:
            results.put(("ok", observation.blob.relative_path))
        finally:
            observation.lease.close()
    except BaseException as exc:  # pragma: no cover - asserted in parent
        results.put(
            (
                "error",
                type(exc).__name__,
                str(getattr(exc, "code", "")),
                str(exc),
            )
        )


def _late_winner_process(
    paths: tuple[str, str, str, str],
    content: bytes,
    temporary_written: Any,
    continue_rename: Any,
    results: Any,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    original_write = legacy_inventory._write_windows_handle

    def paused_write(handle: int, payload: bytes, *, flush: bool = True) -> None:
        original_write(handle, payload, flush=flush)
        temporary_written.set()
        if not continue_rename.wait(20):
            raise TimeoutError("late_winner_release_timeout")

    legacy_inventory._write_windows_handle = paused_write
    try:
        digest = hashlib.sha256(content).hexdigest()
        observation = FilesystemLegacyBlobStager(
            _storage_layout_from_paths(paths)
        ).stage_and_verify(
            content,
            expected_sha256=digest,
            media_type="application/octet-stream",
            effective_at_ms=100,
            normalized_content_sha256=None,
            normalizer_version=None,
        )
        try:
            results.put(("unexpected_success", observation.blob.relative_path))
        finally:
            observation.lease.close()
    except BaseException as exc:  # pragma: no cover - asserted in parent
        results.put(
            (
                "error",
                type(exc).__name__,
                str(getattr(exc, "code", "")),
                str(exc),
            )
        )
    finally:
        legacy_inventory._write_windows_handle = original_write


def _retained_read_process(
    paths: tuple[str, str, str, str],
    digest: str,
    size: int,
    ready: Any,
    release: Any,
    results: Any,
) -> None:
    observation = None
    try:
        observation, content = FilesystemLegacyBlobStager(
            _storage_layout_from_paths(paths)
        ).verify_existing(
            expected_sha256=digest,
            expected_size=size,
            media_type="application/octet-stream",
            normalized_content_sha256=None,
            normalizer_version=None,
        )
        ready.set()
        if not release.wait(20):
            raise TimeoutError("retained_read_release_timeout")
        results.put(("ok", hashlib.sha256(content).hexdigest()))
    except BaseException as exc:  # pragma: no cover - asserted in parent
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        if observation is not None:
            observation.lease.close()


def _join(process: multiprocessing.Process, *, timeout: float = 30.0) -> None:
    process.join(timeout)
    if process.is_alive():
        process.terminate()
        process.join(5)
        pytest.fail("SBE-3.0 child process did not terminate")
    assert process.exitcode == 0


def _get_result(results: Any) -> tuple[Any, ...]:
    try:
        return results.get(timeout=20)
    except queue.Empty:
        pytest.fail("SBE-3.0 child process produced no result")


@pytest.mark.scenario("UPLOAD-001")
def test_upload_001_directory_masks_exclude_file_delete_child_and_stage(
    isolated_knowledge_layout: Any,
) -> None:
    storage = _initialize_layout(isolated_knowledge_layout)

    read_mask = _directory_anchor_access_mask(
        writable=False,
        delete_access=False,
    )
    writable_mask = _directory_anchor_access_mask(
        writable=True,
        delete_access=False,
    )
    created_shard_mask = _directory_anchor_access_mask(
        writable=True,
        delete_access=True,
    )
    assert all(mask & 0x0040 == 0 for mask in (read_mask, writable_mask, created_shard_mask))
    assert writable_mask & 0x0002  # FILE_ADD_FILE
    assert writable_mask & 0x0004  # FILE_ADD_SUBDIRECTORY
    assert created_shard_mask & 0x00010000  # DELETE on the created handle only

    content = b"standard modify CAS staging\n"
    digest = hashlib.sha256(content).hexdigest()
    observation = FilesystemLegacyBlobStager(storage).stage_and_verify(
        content,
        expected_sha256=digest,
        media_type="application/octet-stream",
        effective_at_ms=100,
        normalized_content_sha256=None,
        normalizer_version=None,
    )
    observation.lease.close()
    assert (
        isolated_knowledge_layout.blobs_dir
        / digest[:2]
        / digest[2:4]
        / digest
    ).read_bytes() == content


@pytest.mark.scenario("UPLOAD-002")
def test_upload_002_hardlink_target_is_rejected_without_source_write(
    isolated_knowledge_layout: Any,
) -> None:
    storage = _initialize_layout(isolated_knowledge_layout)
    source = isolated_knowledge_layout.root / "source-canary.bin"
    content = b"source must remain immutable\n"
    source.write_bytes(content)
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    digest = hashlib.sha256(content).hexdigest()
    target = isolated_knowledge_layout.blobs_dir / digest[:2] / digest[2:4] / digest
    target.parent.mkdir(parents=True)
    os.link(source, target)

    with pytest.raises(LegacyBlobStagingError, match="multiply linked"):
        FilesystemLegacyBlobStager(storage).stage_and_verify(
            content,
            expected_sha256=digest,
            media_type="application/octet-stream",
            effective_at_ms=100,
            normalized_content_sha256=None,
            normalizer_version=None,
        )

    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert target.read_bytes() == content
    assert source.stat().st_nlink == 2


@pytest.mark.scenario("UPLOAD-002")
def test_upload_002_shared_root_pin_blocks_identity_swap_and_rejects_reparse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "pinned-cas-root"
    root.mkdir()
    swapped = tmp_path / "swapped-cas-root"
    with legacy_inventory._pinned_directory(root, share_write=True):
        with pytest.raises(OSError):
            root.rename(swapped)

    shard = root / "aa"
    shard.mkdir()
    monkeypatch.setattr(
        legacy_inventory,
        "_windows_final_path",
        lambda _handle: shard,
    )
    monkeypatch.setattr(
        blob_stager,
        "_file_information",
        lambda _handle: (0x10 | 0x400, 1, 0),
    )
    with pytest.raises(LegacyBlobStagingError, match="reparse-backed"):
        blob_stager._verify_directory_handle(123, expected_path=shard)

    for unsafe in ("payload:stream", "CON", "..", "trailing."):
        with pytest.raises(OSError):
            legacy_inventory._validate_simple_windows_component(unsafe)


@pytest.mark.scenario("UPLOAD-003")
def test_upload_003_bounded_existing_blob_open_retries_sharing_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def racing_open(_root_handle: int, _name: str, **_kwargs: object) -> int:
        nonlocal calls
        calls += 1
        if calls < 4:
            raise OSError(32, "synthetic sharing race")
        return 456

    monkeypatch.setattr(blob_stager, "_nt_open_relative", racing_open)
    monkeypatch.setattr(blob_stager.time, "sleep", lambda _seconds: None)

    assert blob_stager._open_existing_blob_with_retry(123, "a" * 64) == 456
    assert calls == 4


@pytest.mark.scenario("UPLOAD-003")
def test_upload_003_real_process_same_bytes_are_idempotent(
    isolated_knowledge_layout: Any,
) -> None:
    _initialize_layout(isolated_knowledge_layout)
    paths = _storage_paths(isolated_knowledge_layout)
    content = b"real process same-byte winner\n"
    digest = hashlib.sha256(content).hexdigest()
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [
        context.Process(
            target=_stage_process,
            args=(paths, content, start, results),
        )
        for _ in range(8)
    ]
    for process in processes:
        process.start()
    start.set()
    observed = [_get_result(results) for _ in processes]
    for process in processes:
        _join(process)

    expected_path = f"{digest[:2]}/{digest[2:4]}/{digest}"
    assert observed == [("ok", expected_path)] * len(processes)
    target = isolated_knowledge_layout.blobs_dir / Path(expected_path)
    assert target.read_bytes() == content
    assert not list(isolated_knowledge_layout.blobs_dir.rglob(".p2-3-blob-*.tmp"))


@pytest.mark.scenario("UPLOAD-003")
def test_upload_003_real_process_different_late_winner_returns_stable_drift(
    isolated_knowledge_layout: Any,
) -> None:
    _initialize_layout(isolated_knowledge_layout)
    paths = _storage_paths(isolated_knowledge_layout)
    content = b"reviewed bytes\n"
    digest = hashlib.sha256(content).hexdigest()
    target = isolated_knowledge_layout.blobs_dir / digest[:2] / digest[2:4] / digest
    context = multiprocessing.get_context("spawn")
    temporary_written = context.Event()
    continue_rename = context.Event()
    results = context.Queue()
    process = context.Process(
        target=_late_winner_process,
        args=(paths, content, temporary_written, continue_rename, results),
    )
    process.start()
    assert temporary_written.wait(20)
    target.write_bytes(b"x" * len(content))
    continue_rename.set()
    observed = _get_result(results)
    _join(process)

    assert observed[0:3] == (
        "error",
        "LegacyBlobStagingError",
        "LEGACY_BLOB_STAGING_FAILED",
    )
    assert observed[3] == "The staged blob bytes do not match their content address."
    assert target.read_bytes() == b"x" * len(content)
    assert not list(isolated_knowledge_layout.blobs_dir.rglob(".p2-3-blob-*.tmp"))


@pytest.mark.scenario("UPLOAD-004")
def test_upload_004_real_process_read_lease_blocks_late_mutation(
    isolated_knowledge_layout: Any,
) -> None:
    storage = _initialize_layout(isolated_knowledge_layout)
    content = b"retained no-follow read\n"
    digest = hashlib.sha256(content).hexdigest()
    staged = FilesystemLegacyBlobStager(storage).stage_and_verify(
        content,
        expected_sha256=digest,
        media_type="application/octet-stream",
        effective_at_ms=100,
        normalized_content_sha256=None,
        normalizer_version=None,
    )
    staged.lease.close()
    target = isolated_knowledge_layout.blobs_dir / staged.blob.relative_path

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    results = context.Queue()
    process = context.Process(
        target=_retained_read_process,
        args=(
            _storage_paths(isolated_knowledge_layout),
            digest,
            len(content),
            ready,
            release,
            results,
        ),
    )
    process.start()
    assert ready.wait(20)
    try:
        with pytest.raises(OSError):
            with target.open("r+b", buffering=0) as stream:
                stream.write(b"x")
        with pytest.raises(OSError):
            target.unlink()
    finally:
        release.set()
    observed = _get_result(results)
    _join(process)

    assert observed == ("ok", digest)
    assert target.read_bytes() == content
