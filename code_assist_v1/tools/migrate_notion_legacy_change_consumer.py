"""CLI for the guarded exact legacy v2 -> consumer v3 migration."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from code_assist_v1.notion_legacy_change_consumer_migration import (
    LegacyChangeConsumerMigrationRefused,
    apply_plan,
    build_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dry-run or explicitly install legacy Notion change-consumer state"
    )
    parser.add_argument("--user-data-root", required=True)
    parser.add_argument("--source-owner-id", required=True)
    parser.add_argument("--source-db", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-checksum", default="")
    parser.add_argument("--backup-path", default="")
    parser.add_argument("--source-quiesced-confirmed", action="store_true")
    parser.add_argument("--backup-confirmed", action="store_true")
    parser.add_argument("--compact", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = build_plan(
            user_data_root=args.user_data_root,
            source_owner_id=args.source_owner_id,
            expected_source_db_path=args.source_db,
        )
        result = (
            apply_plan(
                plan,
                confirm_checksum=args.confirm_checksum,
                source_quiesced_confirmed=args.source_quiesced_confirmed,
                backup_confirmed=args.backup_confirmed,
                expected_backup_path=args.backup_path,
            )
            if args.apply
            else plan
        )
        print(json.dumps(
            result, ensure_ascii=False, sort_keys=True,
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
        ))
        return 0
    except (
        LegacyChangeConsumerMigrationRefused,
        OSError,
        ValueError,
        sqlite3.Error,
    ) as exc:
        print(json.dumps(
            {"status": "refused", "error": type(exc).__name__},
            sort_keys=True, separators=(",", ":"),
        ), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
