"""Offline v59→v60 policy-provenance migration for isolated 10521.

Requires a verified full v59 recovery bundle. This never stops/starts the
service, issues an activation receipt, or changes 10011.
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
    MigrationStorageLayout, SingleMigrator,
)
from code_assist_v1.knowledge_core.migrations.office_personal_writer_part_catalog import (
    OFFICE_PERSONAL_WRITER_PART_MIGRATION_CATALOG as V59,
)
from code_assist_v1.knowledge_core.migrations.company_policy_provenance_catalog import (
    COMPANY_POLICY_PROVENANCE_MIGRATION_CATALOG as V60,
)
from code_assist_v1.tools.clean_10521_runtime import (
    DATA_ROOT, V59_ACTIVATION_NAME, V60_ACTIVATION_NAME,
    catalog_for, is_clean_root, selection_for,
)
from code_assist_v1.tools.migrate_10521_v51 import (
    _active_worker_pids, _listener_active, _verify,
)
from code_assist_v1.tools.service_recovery_bundle import verify_bundle


def _backup(database: Path, destination: Path) -> None:
    if destination.exists():
        raise ValueError("The pre-v60 SQLite snapshot exists; inspect before retrying.")
    handle = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(destination)
    try:
        source.backup(copy)
        if (copy.execute("PRAGMA quick_check").fetchone()[0] != "ok"
                or copy.execute("PRAGMA user_version").fetchone()[0] != 59):
            raise ValueError("The pre-v60 SQLite snapshot is not a sound v59 database.")
    finally:
        copy.close()
        source.close()


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
        _verify(root, V60)
        print(json.dumps({"status": "verified", "schema_version": 60,
            "data_root": str(root)}, sort_keys=True))
        return 0
    if args.backup_bundle is None or args.backup_manifest_sha256 is None:
        raise ValueError("Apply requires a verified offline v59 full service bundle and manifest hash.")
    if _listener_active() or _active_worker_pids(root):
        raise ValueError("Stop the 10521 web service and child workers before migration.")
    if (root / ".runtime" / V60_ACTIVATION_NAME).exists():
        raise ValueError("The v60 activation already exists; use --check.")
    if (catalog_for(root) != V59 or selection_for(root) is None
            or not (root / ".runtime" / V59_ACTIVATION_NAME).is_file()):
        raise ValueError("The exact v59 predecessor activation is required.")
    _verify(root, V59)
    value = verify_bundle(bundle=args.backup_bundle,
        expected_sha256=args.backup_manifest_sha256,
        schema_catalog="office-personal-writer-part-v59")
    if (Path(value["source_code_root"]) != Path(__file__).resolve().parents[1]
            or Path(value["source_data_root"]) != root
            or value["schema_catalog_checksum"] != V59.checksum_sha256):
        raise ValueError("The verified v59 rollback bundle does not identify this 10521 root/code.")
    snapshot = root.parent / "knowledge-core-pre-v60.sqlite3"
    _backup(root / "core/knowledge_core.db", snapshot)
    layout = MigrationStorageLayout(data_root=root,
        database=root / "core/knowledge_core.db",
        blob_root=root / "blobs", projection_root=root / "projections")
    result = SingleMigrator(ConnectionFactory(layout.database, V60),
        app_version="second-brain-10521-v60", storage_layout=layout).run()
    if result.applied_versions != (60,):
        raise ValueError("The forward migration did not apply exactly v60.")
    _verify(root, V60)
    print(json.dumps({"status": "migrated", "schema_version": 60,
        "snapshot": str(snapshot), "migration": asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
