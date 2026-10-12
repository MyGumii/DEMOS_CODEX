"""Create or verify only the isolated 10521 v49 canonical schema.

This owns no account seed, activation receipt, model configuration or server.
It refuses non-empty targets and never writes to the retained services.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    MigrationStorageLayout, SingleMigrator, verify_canonical_integrity,
)
from code_assist_v1.knowledge_core.migrations.semantic_rename_reseed_catalog import (
    SEMANTIC_RENAME_RESEED_MIGRATION_CATALOG as CATALOG,
)


DATA_ROOT = Path(__file__).resolve().parents[2] / "data-root"


def _layout() -> MigrationStorageLayout:
    root = DATA_ROOT.absolute()
    return MigrationStorageLayout(
        data_root=root,
        database=root / "core" / "knowledge_core.db",
        blob_root=root / "blobs",
        projection_root=root / "projections",
    )


def _factory(layout: MigrationStorageLayout) -> ConnectionFactory:
    return ConnectionFactory(layout.database, CATALOG)


def _verify(factory: ConnectionFactory) -> dict[str, object]:
    connection = factory.open(read_only=True)
    try:
        verify_canonical_integrity(connection, CATALOG)
        version = int(connection.execute("SELECT max(version) FROM schema_migration").fetchone()[0])
    finally:
        connection.close()
    return {
        "status": "verified",
        "data_root": str(DATA_ROOT),
        "schema_version": version,
        "catalog_checksum_sha256": CATALOG.checksum_sha256,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--apply", action="store_true")
    choice.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    layout = _layout()
    if args.check:
        print(json.dumps(_verify(_factory(layout)), sort_keys=True))
        return 0
    if DATA_ROOT.exists() and any(DATA_ROOT.iterdir()):
        raise ValueError("The isolated 10521 data root is not empty; use --check or inspect it.")
    for path in (DATA_ROOT, layout.database.parent, layout.blob_root, layout.projection_root):
        path.mkdir(exist_ok=True)
    factory = _factory(layout)
    result = SingleMigrator(
        factory,
        app_version="second-brain-10521-v49",
        storage_layout=layout,
        require_fresh_bootstrap=True,
    ).run()
    verified = _verify(factory)
    print(json.dumps({**verified, "migration": asdict(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
