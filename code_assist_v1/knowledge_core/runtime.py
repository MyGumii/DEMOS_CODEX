"""Single-source SQLite runtime contract for preflight and canonical access."""

from __future__ import annotations


APPROVED_SQLITE_VERSION = "3.53.4"
APPROVED_SQLITE_SOURCE_ID = (
    "2026-07-24 19:02:57 "
    "bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc"
)

# Values are SQLite's read-back representation, not the input spelling.
EXPECTED_CONNECTION_PRAGMAS: dict[str, int | str] = {
    "foreign_keys": 1,
    "busy_timeout": 10000,
    "synchronous": 2,
    "trusted_schema": 0,
    "recursive_triggers": 1,
    "temp_store": 2,
    "wal_autocheckpoint": 0,
    "journal_mode": "wal",
}

DB_NOW_MS_SQL = "CAST(unixepoch('subsec') * 1000 AS INTEGER)"
FAILPOINT_EXACT_NAME = "CODE_ASSIST_FAILPOINTS"
FAILPOINT_PREFIX = "CODE_ASSIST_FAILPOINT_"

__all__ = [
    "APPROVED_SQLITE_SOURCE_ID",
    "APPROVED_SQLITE_VERSION",
    "DB_NOW_MS_SQL",
    "EXPECTED_CONNECTION_PRAGMAS",
    "FAILPOINT_EXACT_NAME",
    "FAILPOINT_PREFIX",
]
