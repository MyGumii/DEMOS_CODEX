"""Reproduce the reviewed schema-v1 fingerprint with an explicit SQLite CLI.

This is an offline build/review tool.  It never opens the canonical database;
all DDL is executed in the supplied executable's in-memory database.  Supplying
the official CLI proves the physical manifest under the exact SQLite build but
does not qualify the application's Python SQLite binding for production.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any, Sequence

from ..knowledge_core.migrations.v001_catalog import (
    V001_EXPECTED_SCHEMA_SHA256,
    V001_STATEMENTS,
)
from ..knowledge_core.runtime import (
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _schema_sha256(rows: Sequence[dict[str, Any]]) -> str:
    normalized: list[tuple[str, str, str, str]] = []
    for row in rows:
        if set(row) != {"type", "name", "tbl_name", "sql"}:
            raise ValueError("SQLite returned an unexpected schema row shape.")
        values = (row["type"], row["name"], row["tbl_name"], row["sql"])
        if any(not isinstance(value, str) for value in values):
            raise ValueError("SQLite returned a non-text schema value.")
        normalized.append(values)
    encoded = json.dumps(
        normalized,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _run_sqlite(executable: Path, arguments: list[str], *, input_sql: str) -> str:
    completed = subprocess.run(
        [os.fspath(executable), *arguments],
        input=input_sql,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if completed.returncode != 0 or completed.stderr.strip():
        raise RuntimeError("The SQLite CLI rejected the schema-v1 probe.")
    return completed.stdout


def build_report(sqlite_executable: str | os.PathLike[str]) -> tuple[dict[str, Any], int]:
    executable = Path(sqlite_executable)
    if not executable.is_absolute() or not executable.is_file():
        return {
            "error": "sqlite_executable_invalid",
            "format": "code_assist.schema_v1_fingerprint.v1",
            "status": "failed",
        }, 2

    try:
        runtime_output = _run_sqlite(
            executable,
            [":memory:"],
            input_sql=(
                ".bail on\n"
                "SELECT sqlite_version()||char(9)||sqlite_source_id()||char(9)||"
                "sqlite_compileoption_used('ENABLE_FTS5')||char(9)||json_valid('{}');\n"
            ),
        ).strip()
        runtime_parts = runtime_output.split("\t")
        if len(runtime_parts) != 4:
            raise ValueError("SQLite returned an invalid runtime probe.")
        version, source_id, fts5, json1 = runtime_parts

        input_sql = "\n".join(
            (
                ".bail on",
                "PRAGMA foreign_keys=ON;",
                "PRAGMA trusted_schema=OFF;",
                "PRAGMA recursive_triggers=ON;",
                "BEGIN EXCLUSIVE;",
                *(statement.rstrip(";") + ";" for statement in V001_STATEMENTS),
                ".mode json",
                "SELECT type,name,tbl_name,coalesce(sql,'') AS sql "
                "FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' "
                "ORDER BY type,name,tbl_name,sql;",
                "ROLLBACK;",
            )
        )
        schema_output = _run_sqlite(
            executable,
            [":memory:"],
            input_sql=input_sql,
        ).strip()
        rows = json.loads(schema_output)
        if not isinstance(rows, list):
            raise ValueError("SQLite returned an invalid schema manifest.")
        observed_sha256 = _schema_sha256(rows)
        counts = {
            kind: sum(1 for row in rows if row.get("type") == kind)
            for kind in ("index", "table", "trigger")
        }
        runtime_match = (
            version == APPROVED_SQLITE_VERSION
            and source_id == APPROVED_SQLITE_SOURCE_ID
            and fts5 == "1"
            and json1 == "1"
        )
        schema_match = observed_sha256 == V001_EXPECTED_SCHEMA_SHA256
        passed = runtime_match and schema_match and counts == {
            "index": 120,
            "table": 51,
            "trigger": 134,
        }
        return {
            "expected_schema_sha256": V001_EXPECTED_SCHEMA_SHA256,
            "format": "code_assist.schema_v1_fingerprint.v1",
            "object_counts": counts,
            "observed_schema_sha256": observed_sha256,
            "runtime": {
                "fts5": fts5 == "1",
                "json": json1 == "1",
                "source_id": source_id,
                "version": version,
            },
            "schema_object_count": len(rows),
            "statement_count": len(V001_STATEMENTS),
            "status": "passed" if passed else "failed",
        }, 0 if passed else 2
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return {
            "error": "schema_fingerprint_probe_failed",
            "format": "code_assist.schema_v1_fingerprint.v1",
            "status": "failed",
        }, 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-exe", required=True)
    try:
        arguments = parser.parse_args(argv)
        report, exit_code = build_report(arguments.sqlite_exe)
    except SystemExit as exc:
        if exc.code == 0:
            return 0
        report, exit_code = {
            "error": "invalid_cli_arguments",
            "format": "code_assist.schema_v1_fingerprint.v1",
            "status": "failed",
        }, 2
    sys.stdout.write(_canonical_json(report) + "\n")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["build_report", "main"]
