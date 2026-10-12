"""CLI for the explicit NP-2A 1307 -> 1308 quarantine migration."""
from __future__ import annotations

import argparse
import json
import sys

from code_assist_v1 import notion_workspace_quarantine_migration as migration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog-path", required=True)
    parser.add_argument("--user-data-root", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-confirmed", action="store_true")
    parser.add_argument("--catalog-quiesced-confirmed", action="store_true")
    parser.add_argument("--exact-plan-checksum", "--confirm-checksum", default="")
    parser.add_argument("--backup-path", default="")
    return parser


def run(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.apply:
        if (
            args.backup_confirmed
            or args.catalog_quiesced_confirmed
            or args.exact_plan_checksum
            or args.backup_path
        ):
            parser.error(
                "apply-only confirmation, checksum, and backup arguments require --apply"
            )
        return {
            "mode": "dry-run",
            **migration.build_plan(args.catalog_path, args.user_data_root),
        }, 0
    report = migration.apply_plan(
        args.catalog_path,
        args.user_data_root,
        exact_plan_checksum=args.exact_plan_checksum,
        backup_confirmed=args.backup_confirmed,
        catalog_quiesced_confirmed=args.catalog_quiesced_confirmed,
        backup_path=args.backup_path,
    )
    return {"mode": "apply", **report}, 0


def main(argv=None) -> int:
    try:
        report, code = run(argv)
    except migration.MigrationRefused as exc:
        report, code = {"error": str(exc)}, 2
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
