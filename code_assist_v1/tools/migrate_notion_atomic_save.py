"""Explicit deploy tool for the Notion atomic-save schema.

The command is read-only unless all of --apply, --backup-confirmed and the
current migration checksum are supplied. It is never imported by app startup.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

from code_assist_v1 import notion_store as ns
from code_assist_v1.auth import safe_user_id
from code_assist_v1.config import USER_DATA_DIR


def _resolved_db_path(user_id: str) -> Path:
    return (Path(USER_DATA_DIR) / user_id / "notion" / "notion.db").resolve()


def _inspect(db_path: Path) -> dict[str, object]:
    con = sqlite3.connect(f"{db_path.as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        page_columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(pages)").fetchall()
        } if "pages" in tables else set()
        page_count = int(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]) if "pages" in tables else 0
        ready = ns._atomic_schema_ready(con)
    finally:
        con.close()
    return {
        "atomic_save": ready,
        "has_pages_table": "pages" in tables,
        "has_revision_column": "revision" in page_columns,
        "has_operation_ledger": "page_save_operations" in tables,
        "has_migration_ledger": "notion_schema_migrations" in tables,
        "page_count": page_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or apply the Notion atomic-save schema migration")
    parser.add_argument("--user-id", required=True, help="Exact workspace owner user ID")
    parser.add_argument("--expected-db-path", required=True, help="Exact resolved notion.db path")
    parser.add_argument("--apply", action="store_true", help="Apply the migration; default is read-only dry-run")
    parser.add_argument(
        "--backup-confirmed",
        action="store_true",
        help="Confirm an application-consistent backup was taken and restore-tested",
    )
    parser.add_argument(
        "--confirm-checksum",
        default="",
        help="Required with --apply; must equal the migration checksum shown by dry-run",
    )
    args = parser.parse_args()

    user_id = safe_user_id(args.user_id)
    if not user_id or user_id != args.user_id:
        parser.error("--user-id must be the exact sanitized workspace owner ID")
    db_path = _resolved_db_path(user_id)
    expected_path = Path(args.expected_db_path).resolve()
    if os.path.normcase(str(db_path)) != os.path.normcase(str(expected_path)):
        parser.error(f"--expected-db-path does not match resolved target: {db_path}")
    if not db_path.is_file():
        parser.error(f"workspace database does not exist: {db_path}")

    version, name, checksum = ns.ATOMIC_SAVE_MIGRATION
    before = _inspect(db_path)
    report: dict[str, object] = {
        "mode": "apply" if args.apply else "dry-run",
        "user_id": user_id,
        "db_path": str(db_path),
        "migration": {"version": version, "name": name, "checksum": checksum},
        "before": before,
        "backup_guidance": (
            "Stop Notion writes, take an application-consistent copy of notion.db and its -wal/-shm files "
            "when present, then verify restore before using --apply."
        ),
    }
    if not args.apply:
        report["would_change"] = not bool(before["atomic_save"])
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    if not args.backup_confirmed:
        parser.error("--apply requires --backup-confirmed")
    if args.confirm_checksum != checksum:
        parser.error(f"--confirm-checksum must exactly equal {checksum}")

    result = ns.migrate_atomic_save_schema(user_id)
    after = _inspect(db_path)
    if not after["atomic_save"]:
        raise RuntimeError("migration postcondition failed: atomic_save is not ready")
    report["result"] = result
    report["after"] = after
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
