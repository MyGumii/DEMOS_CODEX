"""Offline v49→v50 schema migration for the one isolated 10521 root.

Stop the 10521 web and child workers first.  This tool creates an exclusive
pre-migration SQLite backup beside the isolated code/data root, then uses the
canonical forward migrator.  Activation is a separate explicit step.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import socket
import sqlite3

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    MigrationStorageLayout, SingleMigrator, verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import (
    SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG as V49,
)
from code_assist_v1.knowledge_core.migrations.protected_extraction_provenance_catalog import (
    PROTECTED_EXTRACTION_PROVENANCE_MIGRATION_CATALOG as V50,
)
from code_assist_v1.tools.clean_10521_runtime import (
    DATA_ROOT, V50_ACTIVATION_NAME, _v49_selection_for, is_clean_root,
)


BACKUP = DATA_ROOT.parent / "knowledge-core-pre-v50.sqlite3"


def _verify(root: Path, catalog):
    factory = ConnectionFactory(root / "core" / "knowledge_core.db", catalog)
    connection = factory.open(read_only=True)
    try:
        verify_canonical_integrity(connection, catalog)
        return factory
    finally:
        connection.close()


def _listener_active() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", 10521)) == 0


def _backup(database: Path) -> None:
    if BACKUP.exists():
        raise ValueError("The pre-v50 backup already exists; inspect it before retrying.")
    handle = os.open(BACKUP, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(handle)
    source = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(BACKUP)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("The pre-v50 backup did not pass quick_check.")
        if destination.execute("PRAGMA user_version").fetchone()[0] != 49:
            raise ValueError("The pre-v50 backup is not a v49 schema.")
    finally:
        destination.close()
        source.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    root = DATA_ROOT.resolve(strict=True)
    if not is_clean_root(root):
        raise ValueError("The 10521 root is not the registered isolated target.")
    if args.check:
        _verify(root, V50)
        print(json.dumps({"status": "verified", "schema_version": 50,
                          "backup": str(BACKUP), "data_root": str(root)}, sort_keys=True))
        return 0
    if _listener_active():
        raise ValueError("The 10521 listener must be stopped before migration.")
    if (root / ".runtime" / V50_ACTIVATION_NAME).exists():
        raise ValueError("The v50 activation already exists; use --check.")
    if _v49_selection_for(root) is None:
        raise ValueError("The exact v49 predecessor activation is required.")
    _verify(root, V49)
    _backup(root / "core" / "knowledge_core.db")
    layout = MigrationStorageLayout(
        data_root=root, database=root / "core" / "knowledge_core.db",
        blob_root=root / "blobs", projection_root=root / "projections",
    )
    factory = ConnectionFactory(layout.database, V50)
    result = SingleMigrator(
        factory, app_version="second-brain-10521-v50", storage_layout=layout,
    ).run()
    if result.applied_versions != (50,):
        raise ValueError("The forward migration did not apply exactly v50.")
    _verify(root, V50)
    print(json.dumps({"status": "migrated", "schema_version": 50,
                      "backup": str(BACKUP), "migration": asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
