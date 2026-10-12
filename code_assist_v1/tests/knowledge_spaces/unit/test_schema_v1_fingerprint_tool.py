from __future__ import annotations

import json
import sqlite3

import pytest

from code_assist_v1.knowledge_core.migrations.v001_catalog import V001_STATEMENTS
from code_assist_v1.knowledge_core.runtime import (
    APPROVED_SQLITE_SOURCE_ID,
    APPROVED_SQLITE_VERSION,
)
from code_assist_v1.tools import schema_v1_fingerprint


pytestmark = [pytest.mark.unit, pytest.mark.scenario("SCHEMA-008")]


def _schema_rows() -> list[dict[str, str]]:
    connection = sqlite3.connect(":memory:")
    try:
        for statement in V001_STATEMENTS:
            connection.execute(statement)
        rows = connection.execute(
            "SELECT type,name,tbl_name,coalesce(sql,'') FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name,tbl_name,sql"
        ).fetchall()
        return [
            {"type": row[0], "name": row[1], "tbl_name": row[2], "sql": row[3]}
            for row in rows
        ]
    finally:
        connection.close()


def test_schema_008_offline_fingerprint_report_is_canonical_and_exact(
    tmp_path,
    monkeypatch,
) -> None:
    executable = tmp_path / "sqlite3.exe"
    executable.write_bytes(b"isolated fixture")
    responses = iter(
        (
            f"{APPROVED_SQLITE_VERSION}\t{APPROVED_SQLITE_SOURCE_ID}\t1\t1\n",
            json.dumps(_schema_rows(), ensure_ascii=True, separators=(",", ":")),
        )
    )
    monkeypatch.setattr(
        schema_v1_fingerprint,
        "_run_sqlite",
        lambda *_args, **_kwargs: next(responses),
    )

    report, exit_code = schema_v1_fingerprint.build_report(executable)
    assert exit_code == 0
    assert report["status"] == "passed"
    assert report["schema_object_count"] == 305
    assert report["object_counts"] == {"index": 120, "table": 51, "trigger": 134}
    assert report["observed_schema_sha256"] == report["expected_schema_sha256"]


def test_schema_008_offline_fingerprint_rejects_missing_executable(tmp_path) -> None:
    report, exit_code = schema_v1_fingerprint.build_report(
        tmp_path / "missing-sqlite3.exe"
    )
    assert exit_code == 2
    assert report == {
        "error": "sqlite_executable_invalid",
        "format": "code_assist.schema_v1_fingerprint.v1",
        "status": "failed",
    }
