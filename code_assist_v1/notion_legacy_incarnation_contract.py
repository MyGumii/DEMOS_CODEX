"""Side-effect-free v2 contract for legacy page incarnations.

The v1 change-watermark objects remain frozen and independently authenticated.
This contract adds an append-only page incarnation history so a physical delete
followed by reuse of the same page id cannot be mistaken for restoration of the
old source object.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from typing import Any, Callable

from code_assist_v1.notion_legacy_change_contract import (
    LegacyChangeContractError,
    MAX_GENERATION,
    inspect_legacy_change_contract,
)
from code_assist_v1 import notion_legacy_change_contract as _v1_contract


MIGRATION_VERSION = 2026081302
MIGRATION_NAME = "notion_legacy_page_incarnations_v2"
MIGRATION_CHECKSUM = (
    "sha256:db029078d58d1220fb382a29840cf236149c5191b1c0c83fb26291fe1d8e7903"
)
EXPECTED_OBJECT_HASH = (
    "sha256:207c56ae66ae10d0cd0e32753993572df0cbe4add779cb9f93379a459b73f42d"
)
INCARNATION_UDF_NAME = "notion_new_incarnation_id"
INCARNATION_WRITE_CONTEXT_UDF_NAME = "notion_incarnation_write_context"

_INCARNATION_RE = re.compile(r"^inc_[0-9a-f]{32}$")
_EXPECTED_OBJECTS = {
    "notion_page_incarnation_history",
    "idx_notion_page_incarnation_history_active",
    "trg_notion_incarnation_history_insert_guard",
    "trg_notion_incarnation_history_update_guard",
    "trg_notion_incarnation_history_delete_guard",
    "trg_notion_incarnation_pages_insert",
    "trg_notion_incarnation_pages_id_update",
    "trg_notion_incarnation_pages_delete",
}


class LegacyIncarnationContractError(LegacyChangeContractError):
    """Sanitized failure to authenticate the v2 incarnation contract."""


def new_legacy_page_incarnation_id() -> str:
    """Return one unpredictable id accepted by the frozen v2 SQL contract."""
    return "inc_" + secrets.token_hex(16)


def register_legacy_incarnation_udf(
    con: sqlite3.Connection,
    *,
    allocator: Callable[[], str] = new_legacy_page_incarnation_id,
) -> None:
    """Register the sole write capability required by v2 page triggers.

    This function mutates only the explicitly supplied SQLite connection.  The
    module performs no registration, filesystem access, or migration on import.
    """
    if not isinstance(con, sqlite3.Connection) or not callable(allocator):
        raise LegacyIncarnationContractError(
            "valid legacy incarnation connection required"
        )
    con.create_function(INCARNATION_UDF_NAME, 0, allocator, deterministic=False)


def register_legacy_incarnation_write_context(con: sqlite3.Connection) -> None:
    """Register the configured-writer fence consumed by lifecycle triggers."""
    if not isinstance(con, sqlite3.Connection):
        raise LegacyIncarnationContractError(
            "valid legacy incarnation connection required"
        )
    con.create_function(
        INCARNATION_WRITE_CONTEXT_UDF_NAME, 0, lambda: 1, deterministic=True
    )


def is_legacy_page_incarnation_id(value: object) -> bool:
    return isinstance(value, str) and _INCARNATION_RE.fullmatch(value) is not None


def require_legacy_page_incarnation_id(
    value: object, *, field: str = "incarnation_id"
) -> str:
    if not is_legacy_page_incarnation_id(value):
        raise LegacyIncarnationContractError(f"valid {field} required")
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _object_hash(con: sqlite3.Connection) -> str:
    placeholders = ",".join("?" for _ in _EXPECTED_OBJECTS)
    rows = con.execute(
        f"SELECT type,name,sql FROM sqlite_master WHERE name IN ({placeholders}) "
        "ORDER BY type,name",
        tuple(sorted(_EXPECTED_OBJECTS)),
    ).fetchall()
    payload = _canonical_json([tuple(row) for row in rows])
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def inspect_legacy_incarnation_contract(
    con: sqlite3.Connection,
    *,
    verify_integrity: bool = True,
    additional_expected_triggers: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Authenticate v1 plus frozen v2 objects and history invariants.

    A later additive contract may pass its exact frozen trigger names.  The
    default remains the byte-for-byte v2 trigger allowlist, so ordinary v2
    callers still reject every unexpected persistent trigger.
    """
    try:
        if not isinstance(additional_expected_triggers, frozenset) or any(
            not isinstance(name, str)
            or not name.startswith("trg_")
            or not name
            for name in additional_expected_triggers
        ):
            raise LegacyIncarnationContractError(
                "legacy incarnation trigger set is invalid"
            )
        v1 = inspect_legacy_change_contract(
            con, verify_integrity=verify_integrity
        )
        if not v1["installed"]:
            raise LegacyIncarnationContractError(
                "legacy change tracking is unavailable"
            )
        present = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE name IN ("
                + ",".join("?" for _ in _EXPECTED_OBJECTS)
                + ")",
                tuple(sorted(_EXPECTED_OBJECTS)),
            ).fetchall()
        }
        persistent_triggers = {
            str(row[0])
            for row in con.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            ).fetchall()
        }
        v1_triggers = {
            name for name in _v1_contract._EXPECTED_OBJECTS
            if name.startswith("trg_")
        }
        if not present:
            if persistent_triggers != v1_triggers:
                raise LegacyIncarnationContractError(
                    "legacy incarnation trigger set is invalid"
                )
            return {
                "installed": False,
                "object_hash": "",
                "v1_object_hash": str(v1["object_hash"]),
            }
        if present != _EXPECTED_OBJECTS:
            raise LegacyIncarnationContractError(
                "legacy incarnation schema is partial or unknown"
            )
        expected_triggers = v1_triggers | {
            name for name in _EXPECTED_OBJECTS if name.startswith("trg_")
        } | set(additional_expected_triggers)
        if persistent_triggers != expected_triggers:
            raise LegacyIncarnationContractError(
                "legacy incarnation trigger set is invalid"
            )
        marker = con.execute(
            "SELECT name,checksum,actual_schema_sha256 "
            "FROM notion_legacy_schema_migrations WHERE version=?",
            (MIGRATION_VERSION,),
        ).fetchone()
        actual_hash = _object_hash(con)
        if (
            not marker
            or str(marker["name"]) != MIGRATION_NAME
            or str(marker["checksum"]) != MIGRATION_CHECKSUM
            or str(marker["actual_schema_sha256"]) != EXPECTED_OBJECT_HASH
            or actual_hash != EXPECTED_OBJECT_HASH
        ):
            raise LegacyIncarnationContractError(
                "legacy incarnation schema is invalid"
            )

        columns = {
            str(row[1]): row
            for row in con.execute(
                'PRAGMA table_info("notion_page_incarnation_history")'
            ).fetchall()
        }
        expected_columns = {
            "page_id": "TEXT",
            "incarnation_generation": "INTEGER",
            "incarnation_id": "TEXT",
            "created_at": "INTEGER",
            "retired_at": "INTEGER",
        }
        if set(columns) != set(expected_columns) or any(
            str(columns[name][2] or "").upper() != sql_type
            for name, sql_type in expected_columns.items()
        ):
            raise LegacyIncarnationContractError(
                "legacy incarnation schema is invalid"
            )

        invalid = con.execute(
            """
            SELECT 1 FROM notion_page_incarnation_history h
            WHERE typeof(h.page_id)<>'text' OR length(h.page_id) NOT BETWEEN 1 AND 160
               OR typeof(h.incarnation_generation)<>'integer'
               OR h.incarnation_generation NOT BETWEEN 1 AND ?
               OR typeof(h.incarnation_id)<>'text'
               OR length(h.incarnation_id)<>36
               OR substr(h.incarnation_id,1,4)<>'inc_'
               OR substr(h.incarnation_id,5) GLOB '*[^0-9a-f]*'
               OR typeof(h.created_at)<>'integer' OR h.created_at<0
               OR (h.retired_at IS NOT NULL AND
                   (typeof(h.retired_at)<>'integer' OR h.retired_at<h.created_at))
            LIMIT 1
            """,
            (MAX_GENERATION,),
        ).fetchone()
        if invalid is not None:
            raise LegacyIncarnationContractError(
                "legacy incarnation history is invalid"
            )

        # History starts at generation one and has no gaps.  A live page owns
        # exactly the latest active generation; an absent page has no active
        # generation.  Every v1 watermark, including old tombstones, remains
        # representable by a latest incarnation receipt.
        inconsistent = con.execute(
            """
            WITH summary AS (
              SELECT page_id,COUNT(*) AS n,MAX(incarnation_generation) AS max_g,
                     SUM(CASE WHEN retired_at IS NULL THEN 1 ELSE 0 END) AS active_n
              FROM notion_page_incarnation_history GROUP BY page_id
            ), latest AS (
              SELECT h.page_id,h.retired_at
              FROM notion_page_incarnation_history h
              JOIN summary s ON s.page_id=h.page_id
                            AND s.max_g=h.incarnation_generation
            )
            SELECT 1
            FROM summary s
            JOIN latest l ON l.page_id=s.page_id
            LEFT JOIN pages p ON p.id=s.page_id
            WHERE s.n<>s.max_g
               OR (p.id IS NOT NULL AND (s.active_n<>1 OR l.retired_at IS NOT NULL))
               OR (p.id IS NULL AND (s.active_n<>0 OR l.retired_at IS NULL))
               OR NOT EXISTS(
                    SELECT 1 FROM notion_page_change_watermarks w
                    WHERE w.page_id=s.page_id
                  )
            LIMIT 1
            """
        ).fetchone()
        missing_page = con.execute(
            """
            SELECT 1 FROM pages p
            WHERE NOT EXISTS(
              SELECT 1 FROM notion_page_incarnation_history h
              WHERE h.page_id=p.id AND h.retired_at IS NULL
            ) LIMIT 1
            """
        ).fetchone()
        missing_watermark = con.execute(
            """
            SELECT 1 FROM notion_page_change_watermarks w
            WHERE NOT EXISTS(
              SELECT 1 FROM notion_page_incarnation_history h
              WHERE h.page_id=w.page_id
            ) LIMIT 1
            """
        ).fetchone()
        if inconsistent is not None or missing_page is not None or missing_watermark is not None:
            raise LegacyIncarnationContractError(
                "legacy incarnation history is invalid"
            )
        return {
            "installed": True,
            "object_hash": actual_hash,
            "v1_object_hash": str(v1["object_hash"]),
        }
    except LegacyIncarnationContractError:
        raise
    except LegacyChangeContractError as exc:
        raise LegacyIncarnationContractError(
            "legacy incarnation schema is invalid"
        ) from exc
    except (IndexError, KeyError, sqlite3.Error, TypeError, ValueError) as exc:
        raise LegacyIncarnationContractError(
            "legacy incarnation schema is invalid"
        ) from exc


__all__ = [
    "EXPECTED_OBJECT_HASH",
    "INCARNATION_UDF_NAME",
    "INCARNATION_WRITE_CONTEXT_UDF_NAME",
    "LegacyIncarnationContractError",
    "MIGRATION_CHECKSUM",
    "MIGRATION_NAME",
    "MIGRATION_VERSION",
    "inspect_legacy_incarnation_contract",
    "is_legacy_page_incarnation_id",
    "new_legacy_page_incarnation_id",
    "register_legacy_incarnation_udf",
    "register_legacy_incarnation_write_context",
    "require_legacy_page_incarnation_id",
]
