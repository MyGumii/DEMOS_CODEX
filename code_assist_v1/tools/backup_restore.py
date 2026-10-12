"""Explicit operator CLI for verified Knowledge Core backup and restore drills."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
from typing import Sequence

from code_assist_v1.knowledge_core.db import ConnectionFactory
from code_assist_v1.knowledge_core.migrations import (
    CANONICAL_MIGRATION_CATALOG,
    MigrationStorageLayout,
)
from code_assist_v1.knowledge_core.services.backup import (
    BackupService,
    BackupServiceConfiguration,
)
from code_assist_v1.knowledge_core.services.backup_retention import (
    BackupRetentionService,
)
from code_assist_v1.knowledge_core.services.restore import RestoreDrillService
from code_assist_v1.tools.legacy_inventory_contract import canonical_json_bytes


class _ArgumentError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise _ArgumentError("invalid_arguments")


def _absolute(value: str) -> Path:
    if not isinstance(value, str) or not value or not os.path.isabs(value):
        raise _ArgumentError("absolute_path_required")
    return Path(os.path.normpath(value))


def _layout(namespace: argparse.Namespace, *, prefix: str = "") -> MigrationStorageLayout:
    return MigrationStorageLayout(
        data_root=_absolute(getattr(namespace, prefix + "data_root")),
        database=_absolute(getattr(namespace, prefix + "database")),
        blob_root=_absolute(getattr(namespace, prefix + "blob_root")),
        projection_root=_absolute(getattr(namespace, prefix + "projection_root")),
    )


def _factory(layout: MigrationStorageLayout) -> ConnectionFactory:
    if os.environ.get("CODE_ASSIST_TEST_MODE") == "1":
        return ConnectionFactory._for_isolated_tests(
            layout.database, CANONICAL_MIGRATION_CATALOG
        )
    return ConnectionFactory(layout.database, CANONICAL_MIGRATION_CATALOG)


def _add_source_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--blob-root", required=True)
    parser.add_argument("--projection-root", required=True)
    parser.add_argument("--backup-root", required=True)


def _parser() -> _Parser:
    parser = _Parser(prog="knowledge-backup")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    _add_source_paths(create)
    create.add_argument("--backup-run-id", required=True)
    create.add_argument("--app-version", required=True)
    create.add_argument("--build-revision", required=True)
    create.add_argument("--configuration-version", required=True)
    create.add_argument("--lease-hmac-key-set-version", required=True)

    restore = commands.add_parser("restore-drill")
    _add_source_paths(restore)
    restore.add_argument("--backup-run-id", required=True)
    restore.add_argument("--target-data-root", required=True)
    restore.add_argument("--target-database", required=True)
    restore.add_argument("--target-blob-root", required=True)
    restore.add_argument("--target-projection-root", required=True)
    restore.add_argument("--max-rpo-minutes", type=int, default=60)
    restore.add_argument("--max-rto-minutes", type=int, default=240)

    prune = commands.add_parser("prune")
    _add_source_paths(prune)
    return parser


def _write(value: object) -> None:
    payload = canonical_json_bytes(value) + b"\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # pragma: no cover - StringIO doubles
        sys.stdout.write(payload.decode("utf-8"))
        sys.stdout.flush()
        return
    buffer.write(payload)
    buffer.flush()


def _execute(namespace: argparse.Namespace) -> dict[str, object]:
    source = _layout(namespace)
    factory = _factory(source)
    backup_root = _absolute(namespace.backup_root)
    if namespace.command == "create":
        result = BackupService(
            factory,
            source,
            backup_root=backup_root,
            configuration=BackupServiceConfiguration(
                app_version=namespace.app_version,
                build_revision=namespace.build_revision,
                configuration_version=namespace.configuration_version,
                lease_hmac_key_set_version=namespace.lease_hmac_key_set_version,
            ),
        ).create(backup_run_id=namespace.backup_run_id)
    elif namespace.command == "restore-drill":
        result = RestoreDrillService(
            factory,
            source,
            backup_root=backup_root,
        ).restore(
            backup_run_id=namespace.backup_run_id,
            target_layout=_layout(namespace, prefix="target_"),
            assumed_incident_at_ms=int(time.time() * 1000),
            max_rpo_minutes=namespace.max_rpo_minutes,
            max_rto_minutes=namespace.max_rto_minutes,
        )
    elif namespace.command == "prune":
        result = BackupRetentionService(
            factory,
            source,
            backup_root=backup_root,
        ).prune()
    else:  # pragma: no cover - argparse closes the enum
        raise _ArgumentError("unknown_command")
    return {
        "operation": namespace.command,
        "result": asdict(result),
        "status": "pass",
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        namespace = _parser().parse_args(argv)
        _write(_execute(namespace))
        return 0
    except _ArgumentError:
        _write(
            {
                "error": {
                    "code": "CLI_INVALID_ARGUMENT",
                    "message": "The backup operation arguments are invalid.",
                },
                "status": "error",
            }
        )
        return 2
    except Exception:
        _write(
            {
                "error": {
                    "code": "BACKUP_OPERATION_FAILED",
                    "message": "The backup operation did not complete.",
                },
                "status": "error",
            }
        )
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
