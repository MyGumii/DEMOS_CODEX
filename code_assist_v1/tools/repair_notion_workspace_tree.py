"""Explicit, guarded maintenance tool for a Notion workspace page tree.

The default mode is a strictly read-only inspection. Mutation requires an
exact user/database target, ``--apply``, and the user-specific confirmation
string printed by dry-run. Apply creates and verifies an adjacent SQLite
backup before invoking the store's explicit repair operation.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any, Sequence
import uuid

from code_assist_v1 import notion_store as ns
from code_assist_v1.auth import safe_user_id
from code_assist_v1.config import USER_DATA_DIR


CONFIRMATION_PREFIX = "REPAIR-NOTION-WORKSPACE-TREE"


def required_confirmation(user_id: str) -> str:
    return f"{CONFIRMATION_PREFIX}:{user_id}"


def _resolved_db_path(user_id: str) -> Path:
    return (Path(USER_DATA_DIR) / user_id / "notion" / "notion.db").resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_connection(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _cycle_components(parent_by_id: dict[str, str]) -> list[tuple[str, ...]]:
    """Return unique directed cycles after invalid parent edges are removed."""
    cycles: set[tuple[str, ...]] = set()
    for start in sorted(parent_by_id):
        trail: list[str] = []
        positions: dict[str, int] = {}
        current = start
        while current and current in parent_by_id and current not in positions:
            positions[current] = len(trail)
            trail.append(current)
            current = parent_by_id.get(current, "")
        if current in positions:
            members = trail[positions[current]:]
            if members:
                rotations = [
                    tuple(members[index:] + members[:index])
                    for index in range(len(members))
                ]
                cycles.add(min(rotations))
    return sorted(cycles)


def inspect_tree(db_path: Path) -> dict[str, Any]:
    con = _read_only_connection(db_path)
    try:
        pages_exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pages'"
        ).fetchone()
        if not pages_exists:
            raise RuntimeError("workspace database is missing required pages table")
        columns = {
            str(row[1]) for row in con.execute("PRAGMA table_info(pages)").fetchall()
        }
        required_columns = {"id", "parent_id", "archived"}
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            raise RuntimeError(
                "pages table is missing required columns: " + ", ".join(missing_columns)
            )
        rows = con.execute(
            "SELECT id, parent_id FROM pages WHERE archived=0 ORDER BY id"
        ).fetchall()
    finally:
        con.close()

    parent_by_id = {
        str(row["id"]): str(row["parent_id"] or "")
        for row in rows
    }
    valid_ids = set(parent_by_id)
    self_parent_ids = sorted(
        page_id
        for page_id, parent_id in parent_by_id.items()
        if parent_id == page_id
    )
    dangling_parent_ids = sorted(
        page_id
        for page_id, parent_id in parent_by_id.items()
        if parent_id and parent_id != page_id and parent_id not in valid_ids
    )
    normalized = dict(parent_by_id)
    for page_id in [*self_parent_ids, *dangling_parent_ids]:
        normalized[page_id] = ""
    cycles = _cycle_components(normalized)
    cycle_page_ids = sorted({page_id for cycle in cycles for page_id in cycle})
    affected_page_ids = sorted(
        {*self_parent_ids, *dangling_parent_ids, *cycle_page_ids}
    )
    issue_count = len(self_parent_ids) + len(dangling_parent_ids) + len(cycles)
    return {
        "active_page_count": len(parent_by_id),
        "self_parent_count": len(self_parent_ids),
        "dangling_parent_count": len(dangling_parent_ids),
        "cycle_count": len(cycles),
        "cycle_page_count": len(cycle_page_ids),
        "affected_page_count": len(affected_page_ids),
        "issue_count": issue_count,
        "healthy": issue_count == 0,
    }


def _sidecar_report(db_path: Path) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for suffix in ("-wal", "-shm", "-journal"):
        path = Path(f"{db_path}{suffix}")
        if path.is_file():
            output.append(
                {
                    "name": path.name,
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return output


def _database_content_fingerprint(db_path: Path) -> str:
    """Fingerprint durable SQLite content, excluding lock-only shared memory."""
    content: list[dict[str, Any]] = []
    for suffix in ("", "-wal", "-journal"):
        path = Path(f"{db_path}{suffix}")
        if path.is_file():
            content.append(
                {"suffix": suffix, "size": path.stat().st_size, "sha256": _sha256(path)}
            )
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _reserve_backup_path(db_path: Path, source_sha256: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid.uuid4().hex[:8]
    backup_path = db_path.with_name(
        f"{db_path.name}.tree-repair.{timestamp}.{source_sha256[:12]}.{suffix}.bak"
    )
    descriptor = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    return backup_path


def create_verified_backup(db_path: Path, source_sha256: str) -> dict[str, Any]:
    backup_path = _reserve_backup_path(db_path, source_sha256)
    source: sqlite3.Connection | None = None
    destination: sqlite3.Connection | None = None
    try:
        source = _read_only_connection(db_path)
        destination = sqlite3.connect(backup_path)
        source.backup(destination)
        destination.commit()
        quick_check = destination.execute("PRAGMA quick_check").fetchone()
        if not quick_check or str(quick_check[0]).lower() != "ok":
            raise RuntimeError("adjacent backup failed SQLite quick_check")
    except Exception:
        if destination is not None:
            destination.close()
            destination = None
        if source is not None:
            source.close()
            source = None
        try:
            backup_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()

    backup_issues = inspect_tree(backup_path)
    return {
        "created": True,
        "path": str(backup_path),
        "sha256": _sha256(backup_path),
        "size": backup_path.stat().st_size,
        "quick_check": "ok",
        "tree_snapshot": backup_issues,
    }


def _apply_repair(
    db_path: Path,
    user_id: str,
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Acquire SQLite's writer lock before the final target check and repair."""
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        if _database_content_fingerprint(db_path) != expected_fingerprint:
            con.rollback()
            raise RuntimeError("workspace database changed after inspection; repair aborted")
        ns._repair_page_tree(con)
        if con.in_transaction:
            con.commit()
        row = con.execute("SELECT COUNT(*) AS c FROM pages WHERE archived=0").fetchone()
        page_count = int(row["c"] if row else 0)
    except Exception:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()
    return {"user_id": user_id, "page_count": page_count, "status": "ok"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly repair one Notion workspace page tree"
    )
    parser.add_argument("--user-id", required=True, help="Exact workspace owner ID")
    parser.add_argument(
        "--expected-db-path",
        required=True,
        help="Exact resolved notion.db target printed by dry-run",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create an adjacent backup and apply repair; default is read-only",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Required with --apply; exact user-specific confirmation from dry-run",
    )
    return parser


def run(argv: Sequence[str] | None = None) -> tuple[dict[str, Any], int]:
    parser = build_parser()
    args = parser.parse_args(argv)
    user_id = safe_user_id(args.user_id)
    if not user_id or user_id != args.user_id:
        parser.error("--user-id must be the exact sanitized workspace owner ID")

    db_path = _resolved_db_path(user_id)
    expected_path = Path(args.expected_db_path).resolve()
    if os.path.normcase(str(db_path)) != os.path.normcase(str(expected_path)):
        parser.error(f"--expected-db-path does not match resolved target: {db_path}")
    if not db_path.is_file():
        parser.error(f"workspace database does not exist: {db_path}")

    confirmation = required_confirmation(user_id)
    before_sha256 = _sha256(db_path)
    before = inspect_tree(db_path)
    before_fingerprint = _database_content_fingerprint(db_path)
    report: dict[str, Any] = {
        "report_schema_version": 1,
        "operation": "notion_workspace_tree_repair",
        "mode": "apply" if args.apply else "dry-run",
        "user_id": user_id,
        "db_path": str(db_path),
        "required_confirmation": confirmation,
        "before": {
            "database_sha256": before_sha256,
            "content_fingerprint": before_fingerprint,
            "database_size": db_path.stat().st_size,
            "tree": before,
            "sidecars": _sidecar_report(db_path),
        },
        "backup": {
            "created": False,
            "path": "",
            "guidance": (
                "Stop Notion writes first. Apply creates a verified adjacent SQLite "
                "backup before repair; retain it until the workspace is validated."
            ),
        },
        "would_change": not bool(before["healthy"]),
    }
    if not args.apply:
        return report, 0
    if args.confirm != confirmation:
        parser.error(f"--confirm must exactly equal {confirmation}")

    backup = create_verified_backup(db_path, before_sha256)
    report["backup"] = {
        **backup,
        "guidance": report["backup"]["guidance"],
    }
    if backup["tree_snapshot"] != before:
        raise RuntimeError("adjacent backup tree snapshot does not match the source")
    repair_result = _apply_repair(
        db_path,
        user_id,
        expected_fingerprint=before_fingerprint,
    )
    after = inspect_tree(db_path)
    after_sha256 = _sha256(db_path)
    if not after["healthy"]:
        raise RuntimeError("tree repair postcondition failed: issues remain")
    report["result"] = repair_result
    report["after"] = {
        "database_sha256": after_sha256,
        "content_fingerprint": _database_content_fingerprint(db_path),
        "database_size": db_path.stat().st_size,
        "tree": after,
        "sidecars": _sidecar_report(db_path),
    }
    report["changed"] = after_sha256 != before_sha256 or after != before
    return report, 0


def main(argv: Sequence[str] | None = None) -> int:
    report, exit_code = run(argv)
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
