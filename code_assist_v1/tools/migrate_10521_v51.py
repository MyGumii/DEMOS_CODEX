"""Offline v50→v51 migration of only the isolated 10521 data root.

The source web service and child workers must be stopped. Apply requires a
separately retained, verified full v50 service bundle; the SQLite-only sibling
snapshot is an additional rollback aid, not a replacement for that bundle.
Activation and service restart remain separate operator steps.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    MigrationStorageLayout, SingleMigrator, verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import (
    PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG as V50,
)
from code_assist_v1.knowledge_core.migrations.protected_pdf_visual_binding_catalog import (
    PROTECTED_PDF_VISUAL_BINDING_MIGRATION_CATALOG as V51,
)
from code_assist_v1.tools.clean_10521_runtime import (
    DATA_ROOT, V50_ACTIVATION_NAME, V51_ACTIVATION_NAME,
    _v50_selection_for, is_clean_root,
)
from code_assist_v1.tools.full_second_brain_preview_workers import _parent_alive
from code_assist_v1.tools.recovered_preview_binding import _read
from code_assist_v1.tools.service_recovery_bundle import verify_bundle


def _verify(root: Path, catalog):
    factory = ConnectionFactory(root / "core/knowledge_core.db", catalog)
    connection = factory.open(read_only=True)
    try:
        verify_canonical_integrity(connection, catalog)
    finally:
        connection.close()


def _listener_active() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", 10521)) == 0


def _pid_precedes_receipt(pid: int, receipt: Path) -> bool:
    """Treat a reused Windows PID as stale, but fail closed if it cannot be timed."""
    if sys.platform != "win32":
        return True
    import ctypes

    class FileTime(ctypes.Structure):
        _fields_ = (("low", ctypes.c_uint32), ("high", ctypes.c_uint32))

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    get_times = kernel32.GetProcessTimes
    get_times.argtypes = (ctypes.c_void_p, ctypes.POINTER(FileTime),
                          ctypes.POINTER(FileTime), ctypes.POINTER(FileTime),
                          ctypes.POINTER(FileTime))
    get_times.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    handle = open_process(0x1000, 0, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return True
    try:
        created, exited, kernel, user = (FileTime() for _ in range(4))
        if not get_times(handle, ctypes.byref(created), ctypes.byref(exited),
                         ctypes.byref(kernel), ctypes.byref(user)):
            return True
        ticks = (created.high << 32) | created.low
        created_ns = (ticks - 116444736000000000) * 100
        return created_ns <= receipt.stat().st_mtime_ns
    finally:
        close_handle(handle)


def _active_worker_pids(root: Path) -> tuple[int, ...]:
    ready_dir = root / ".runtime/full-preview-workers"
    if not ready_dir.exists():
        return ()
    active = []
    for path in ready_dir.glob("*.ready.json"):
        value = json.loads(_read(path))
        pid = value.get("pid") if type(value) is dict else None
        if type(pid) is not int or pid < 1 or value.get("kind") not in {"durable", "projector"}:
            raise ValueError("Invalid 10521 worker readiness receipt; inspect offline status.")
        if _parent_alive(pid) and _pid_precedes_receipt(pid, path):
            active.append(pid)
    return tuple(sorted(set(active)))


def _backup(database: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("The pre-v51 SQLite snapshot already exists; inspect it before retrying.")
    handle = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(destination)
    try:
        source.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("The pre-v51 SQLite snapshot did not pass quick_check.")
        if copy.execute("PRAGMA user_version").fetchone()[0] != 50:
            raise ValueError("The pre-v51 SQLite snapshot is not a v50 schema.")
    finally:
        copy.close()
        source.close()


def _verified_v50_bundle(root: Path, bundle_path: Path, manifest_sha256: str) -> None:
    value = verify_bundle(
        bundle=bundle_path, expected_sha256=manifest_sha256,
        schema_catalog="protected-extraction-provenance-v50",
    )
    if (Path(value["source_code_root"]) != Path(__file__).resolve().parents[1]
            or Path(value["source_data_root"]) != root
            or value["schema_catalog_checksum"] != V50.checksum_sha256):
        raise ValueError("The verified v50 rollback bundle does not identify this 10521 root/code.")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--backup-bundle", type=Path)
    parser.add_argument("--backup-manifest-sha256")
    args = parser.parse_args(argv)
    root = DATA_ROOT.resolve(strict=True)
    if not is_clean_root(root):
        raise ValueError("The 10521 root is not the registered isolated target.")
    if args.check:
        _verify(root, V51)
        print(json.dumps({"status": "verified", "schema_version": 51,
                          "data_root": str(root)}, sort_keys=True))
        return 0
    if args.backup_bundle is None or args.backup_manifest_sha256 is None:
        raise ValueError("Apply requires the verified offline v50 full service bundle and external manifest hash.")
    if _listener_active() or _active_worker_pids(root):
        raise ValueError("Stop the 10521 web service and all child workers before migration.")
    if (root / ".runtime" / V51_ACTIVATION_NAME).exists():
        raise ValueError("The v51 activation already exists; use --check.")
    if _v50_selection_for(root) is None or not (root / ".runtime" / V50_ACTIVATION_NAME).is_file():
        raise ValueError("The exact v50 predecessor activation is required.")
    _verify(root, V50)
    _verified_v50_bundle(root, args.backup_bundle, args.backup_manifest_sha256)
    snapshot = root.parent / "knowledge-core-pre-v51.sqlite3"
    _backup(root / "core/knowledge_core.db", snapshot)
    layout = MigrationStorageLayout(
        data_root=root, database=root / "core/knowledge_core.db",
        blob_root=root / "blobs", projection_root=root / "projections",
    )
    factory = ConnectionFactory(layout.database, V51)
    result = SingleMigrator(
        factory, app_version="second-brain-10521-v51", storage_layout=layout,
    ).run()
    if result.applied_versions != (51,):
        raise ValueError("The forward migration did not apply exactly v51.")
    _verify(root, V51)
    print(json.dumps({"status": "migrated", "schema_version": 51,
                      "snapshot": str(snapshot), "migration": asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
