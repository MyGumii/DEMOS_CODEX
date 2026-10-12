"""Exact-owner NP-2A legacy page inventory and guarded locator backfill CLI.

Dry-run is the default and is strictly read-only.  This module is never
imported by either application startup path.  A partial run is resumed only
after a new dry-run and explicit approval of the new checksum/version.
"""
from __future__ import annotations

import argparse
import json
import sys

from code_assist_v1.notion_workspace_inventory import (
    WorkspaceBackfillFailure,
    WorkspaceInventoryError,
    apply_backfill_plan,
    build_backfill_plan,
    dry_run_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory one exact legacy Notion owner and backfill NP-2A locators"
    )
    parser.add_argument("--user-data-root", required=True)
    parser.add_argument("--source-owner-id", required=True)
    parser.add_argument("--actor-subject-id", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--expected-source-db-path", required=True)
    parser.add_argument("--expected-catalog-db-path", required=True)
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply the approved plan; omitted means strictly read-only dry-run",
    )
    parser.add_argument(
        "--backup-confirmed", action="store_true",
        help="Confirm the operator has an external restore-tested backup",
    )
    parser.add_argument(
        "--source-quiesced-confirmed", action="store_true",
        help="Confirm writes to the exact legacy source shard are stopped",
    )
    parser.add_argument(
        "--confirm-checksum", default="",
        help="Exact deterministic plan checksum printed by dry-run",
    )
    parser.add_argument(
        "--confirm-workspace-version", type=int,
        help="Exact workspace version printed by dry-run",
    )
    return parser


def _print(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        plan = build_backfill_plan(
            user_data_root=args.user_data_root,
            source_owner_id=args.source_owner_id,
            actor_subject_id=args.actor_subject_id,
            workspace_id=args.workspace_id,
            expected_source_db_path=args.expected_source_db_path,
            expected_catalog_db_path=args.expected_catalog_db_path,
        )
        if not args.apply:
            if (
                args.backup_confirmed
                or args.source_quiesced_confirmed
                or args.confirm_checksum
                or args.confirm_workspace_version is not None
            ):
                raise WorkspaceInventoryError(
                    "apply confirmations are invalid without --apply"
                )
            _print(dry_run_report(plan))
            return 0
        if not args.backup_confirmed:
            raise WorkspaceInventoryError("--apply requires --backup-confirmed")
        if not args.source_quiesced_confirmed:
            raise WorkspaceInventoryError("--apply requires --source-quiesced-confirmed")
        if not args.confirm_checksum:
            raise WorkspaceInventoryError("--apply requires --confirm-checksum")
        if args.confirm_workspace_version is None:
            raise WorkspaceInventoryError("--apply requires --confirm-workspace-version")
        report = apply_backfill_plan(
            plan,
            backup_confirmed=True,
            source_quiesced_confirmed=True,
            confirm_checksum=args.confirm_checksum,
            confirm_workspace_version=args.confirm_workspace_version,
        )
        _print(report)
        return 0
    except WorkspaceBackfillFailure as exc:
        _print(exc.report)
        print(str(exc), file=sys.stderr)
        return 1
    except (WorkspaceInventoryError, ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
