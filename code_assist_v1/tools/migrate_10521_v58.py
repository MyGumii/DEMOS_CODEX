"""Offline v52→v58 migration for the isolated 10521 root only.

Requires a verified full v52 recovery bundle. This tool never stops a running
service, issues the v58 activation receipt, or changes 10011.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    MigrationStorageLayout, SingleMigrator, verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.temporal_root_rebinding_catalog import (
    TEMPORAL_ROOT_REBINDING_MIGRATION_CATALOG as V52,
)
from code_assist_v1.knowledge_core.migrations.office_hybrid_publication_body_catalog import (
    OFFICE_HYBRID_PUBLICATION_BODY_MIGRATION_CATALOG as V58,
)
from code_assist_v1.tools.clean_10521_runtime import (
    DATA_ROOT, V52_ACTIVATION_NAME, V58_ACTIVATION_NAME,
    _v52_selection_for, is_clean_root,
)
from code_assist_v1.tools.migrate_10521_v51 import (
    _active_worker_pids, _listener_active, _verify,
)
from code_assist_v1.tools.service_recovery_bundle import verify_bundle


def _backup(database: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("The pre-v58 SQLite snapshot already exists; inspect before retrying.")
    handle = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(destination)
    try:
        source.backup(copy)
        if (copy.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or copy.execute("PRAGMA user_version").fetchone()[0] != 52):
            raise ValueError("The pre-v58 SQLite snapshot is not a sound v52 database.")
    finally:
        copy.close()
        source.close()


def _verified_v52_bundle(root: Path, bundle_path: Path,
        manifest_sha256: str) -> None:
    value = verify_bundle(bundle=bundle_path, expected_sha256=manifest_sha256,
        schema_catalog="temporal-root-rebinding-v52")
    if (Path(value["source_code_root"]) != Path(__file__).resolve().parents[1]
            or Path(value["source_data_root"]) != root
            or value["schema_catalog_checksum"] != V52.checksum_sha256):
        raise ValueError("The verified v52 rollback bundle does not identify this 10521 root/code.")


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
        _verify(root, V58)
        print(json.dumps({"status": "verified", "schema_version": 58,
            "data_root": str(root)}, sort_keys=True))
        return 0
    if args.backup_bundle is None or args.backup_manifest_sha256 is None:
        raise ValueError("Apply requires the verified offline v52 full service bundle and external manifest hash.")
    if _listener_active() or _active_worker_pids(root):
        raise ValueError("Stop the 10521 web service and all child workers before migration.")
    if (root / ".runtime" / V58_ACTIVATION_NAME).exists():
        raise ValueError("The v58 activation already exists; use --check.")
    if (_v52_selection_for(root) is None
            or not (root / ".runtime" / V52_ACTIVATION_NAME).is_file()):
        raise ValueError("The exact v52 predecessor activation is required.")
    _verify(root, V52)
    _verified_v52_bundle(root, args.backup_bundle,
        args.backup_manifest_sha256)
    snapshot = root.parent / "knowledge-core-pre-v58.sqlite3"
    _backup(root / "core/knowledge_core.db", snapshot)
    layout = MigrationStorageLayout(data_root=root,
        database=root / "core/knowledge_core.db",
        blob_root=root / "blobs", projection_root=root / "projections")
    result = SingleMigrator(ConnectionFactory(layout.database, V58),
        app_version="second-brain-10521-v58", storage_layout=layout).run()
    if result.applied_versions != (53, 54, 55, 56, 57, 58):
        raise ValueError("The forward migration did not apply exactly v53–v58.")
    _verify(root, V58)
    print(json.dumps({"status": "migrated", "schema_version": 58,
        "snapshot": str(snapshot), "migration": asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
