from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

import pytest


_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_WORKSPACE_ROOT = _PACKAGE_ROOT.parent
_PREFLIGHT_MODULE = "code_assist_v1.tools.runtime_preflight"
_PREFLIGHT_WRAPPER = _PACKAGE_ROOT / "ops" / "preflight.ps1"
_ROLLOUT_FLAGS = (
    "KNOWLEDGE_CORE_ENABLED",
    "KNOWLEDGE_CORE_MULTI_WIKI_SCHEMA",
    "KNOWLEDGE_CORE_MULTI_WIKI_READS",
    "KNOWLEDGE_CORE_MULTI_WIKI_WRITES",
    "KNOWLEDGE_CORE_CONVERSATION_WIKI_BINDING",
    "KNOWLEDGE_CORE_READS",
    "KNOWLEDGE_CORE_PERSONAL_WRITES",
    "KNOWLEDGE_CORE_PART_READS",
    "KNOWLEDGE_CORE_PART_WRITES",
    "KNOWLEDGE_CORE_PROJECTIONS",
    "KNOWLEDGE_CORE_PROJECTION_FOOTER_V2",
    "KNOWLEDGE_CORE_HIGHER_SPACES",
    "KNOWLEDGE_CORE_AGGREGATE_GRAPH_READS",
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_LOCAL",
    "KNOWLEDGE_CORE_AUTO_SEMANTIC_EDGES_AGGREGATE",
    "KNOWLEDGE_CORE_HYBRID_RERANK",
    "KNOWLEDGE_CORE_LOCAL_CROSS_ENCODER",
    "KNOWLEDGE_CORE_TEMPORAL_RANKING",
    "KNOWLEDGE_CORE_TEAM_WRITES",
    "KNOWLEDGE_CORE_RESPONSIBILITY_WRITES",
)
_SQLITE_MISMATCH_REASONS = (
    "sqlite_source_id_mismatch",
    "sqlite_version_mismatch",
)
_APPROVED_SQLITE_VERSION = "3.53.4"
_APPROVED_SQLITE_SOURCE_ID = (
    "2026-07-24 19:02:57 "
    "bf7c7f30031888f4e796e429ab3978879485813aaca6f641c7b33e4e09459bcc"
)
_IMPORT_PROBE = r"""
import json
import sys

from code_assist_v1.tools import runtime_preflight

exit_code = runtime_preflight.main(sys.argv[1:])
forbidden_prefixes = (
    "code_assist_v1.app_code",
    "code_assist_v1.app_notion",
    "code_assist_v1.auth",
    "code_assist_v1.config",
    "code_assist_v1.job_manager",
    "code_assist_v1.knowledge_store",
    "code_assist_v1.memory_store",
    "code_assist_v1.notion_store",
    "code_assist_v1.project_context_store",
    "code_assist_v1.second_brain_store",
)
loaded = sorted(
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(prefix + ".") for prefix in forbidden_prefixes)
)
if loaded:
    sys.stderr.write("preflight imported application config/store modules: ")
    sys.stderr.write(json.dumps(loaded, sort_keys=True))
    sys.stderr.write("\n")
    raise SystemExit(97)
raise SystemExit(exit_code)
"""


def _sanitized_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in _ROLLOUT_FLAGS:
        environment[name] = "0"
    for name in tuple(environment):
        if name == "CODE_ASSIST_FAILPOINTS" or name.startswith(
            "CODE_ASSIST_FAILPOINT_"
        ):
            environment.pop(name, None)
    return environment


def _prepare_runtime_inputs(isolated_knowledge_layout) -> tuple[Path, Path]:
    input_dir = isolated_knowledge_layout.runtime_dir / "preflight inputs with spaces"
    input_dir.mkdir(parents=True, exist_ok=True)
    runtime_lock = input_dir / "requirements runtime.lock"
    shutil.copyfile(_PACKAGE_ROOT / "requirements-runtime.lock", runtime_lock)
    rollout_record = input_dir / "part write rollout record.json"
    return runtime_lock, rollout_record


def _cli_arguments(
    mode: str,
    isolated_knowledge_layout,
    runtime_lock: Path,
    rollout_record: Path,
) -> list[str]:
    return [
        "--mode",
        mode,
        "--data-root",
        os.fspath(isolated_knowledge_layout.root),
        "--database",
        os.fspath(isolated_knowledge_layout.core_db_path),
        "--blob-root",
        os.fspath(isolated_knowledge_layout.blobs_dir),
        "--projection-root",
        os.fspath(isolated_knowledge_layout.projections_dir),
        "--runtime-lock",
        os.fspath(runtime_lock),
        "--part-write-rollout-record",
        os.fspath(rollout_record),
    ]


def _run_cli(
    arguments: list[str], *, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-E", "-m", _PREFLIGHT_MODULE, *arguments],
        cwd=_WORKSPACE_ROOT,
        env=_sanitized_environment() if environment is None else environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _run_import_probe(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", _IMPORT_PROBE, *arguments],
        cwd=_WORKSPACE_ROOT,
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _single_json_object(stdout: str) -> dict[str, object]:
    assert stdout.endswith("\n")
    assert len(stdout.splitlines()) == 1
    assert stdout == stdout.lstrip()
    report = json.loads(stdout)
    assert isinstance(report, dict)
    return report


def _database_state(database: Path) -> tuple[str, list[tuple[object, ...]]]:
    connection = sqlite3.connect(database)
    try:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql "
            "FROM sqlite_schema ORDER BY type, name, tbl_name"
        ).fetchall()
    finally:
        connection.close()
    return journal_mode, schema


def _relative_files(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }


def _assert_current_sqlite_mismatch_only(
    report: dict[str, object], *, mode: str, status: str, exit_code: int
) -> None:
    assert report["report_schema"] == "code_assist.runtime_preflight.v1"
    assert report["scope"] == "p0.4-runtime-preflight-only"
    assert report["mode"] == mode
    gate = report["gate"]
    assert isinstance(gate, dict)
    assert gate["status"] == status
    assert gate["exit_code"] == exit_code
    assert tuple(sorted(gate["reasons"])) == _SQLITE_MISMATCH_REASONS
    assert report["feature_flags"] == {name: 0 for name in _ROLLOUT_FLAGS}
    assert report["failpoints"] == {"names": [], "present": False}

    sqlite_report = report["sqlite"]
    assert isinstance(sqlite_report, dict)
    assert sqlite_report["version"] == sqlite3.sqlite_version
    connection = sqlite3.connect(":memory:")
    try:
        source_id = connection.execute("SELECT sqlite_source_id()").fetchone()[0]
    finally:
        connection.close()
    assert sqlite_report["source_id"] == source_id
    assert (
        sqlite_report["version"],
        sqlite_report["source_id"],
    ) != (_APPROVED_SQLITE_VERSION, _APPROVED_SQLITE_SOURCE_ID)


@pytest.mark.integration
@pytest.mark.operations
@pytest.mark.scenario("OPS-004")
def test_ops_004_cli_warns_or_fails_only_for_current_sqlite_and_is_read_only(
    isolated_sqlite_db: Path,
    isolated_blob_dir: Path,
    isolated_projection_dir: Path,
    isolated_knowledge_layout,
) -> None:
    assert isolated_blob_dir.is_dir()
    assert isolated_projection_dir.is_dir()
    assert isolated_sqlite_db == isolated_knowledge_layout.core_db_path

    connection = sqlite3.connect(isolated_sqlite_db)
    try:
        configured_mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    finally:
        connection.close()
    assert str(configured_mode).lower() == "wal"

    runtime_lock, rollout_record = _prepare_runtime_inputs(isolated_knowledge_layout)
    state_before = _database_state(isolated_sqlite_db)
    files_before = _relative_files(isolated_knowledge_layout.root)
    assert state_before[0].lower() == "wal"

    development = _run_cli(
        _cli_arguments(
            "development",
            isolated_knowledge_layout,
            runtime_lock,
            rollout_record,
        )
    )
    assert development.returncode == 0, development.stderr
    assert development.stderr == ""
    development_report = _single_json_object(development.stdout)
    assert development_report["paths"] == {
        "blob_root": os.fspath(isolated_blob_dir.resolve()),
        "data_root": os.fspath(isolated_knowledge_layout.root.resolve()),
        "database": os.fspath(isolated_sqlite_db.resolve()),
        "part_write_rollout_record": os.fspath(rollout_record.resolve()),
        "projection_root": os.fspath(isolated_projection_dir.resolve()),
        "runtime_lock": os.fspath(runtime_lock.resolve()),
    }
    _assert_current_sqlite_mismatch_only(
        development_report,
        mode="development",
        status="warning",
        exit_code=0,
    )
    assert _database_state(isolated_sqlite_db) == state_before
    assert _relative_files(isolated_knowledge_layout.root) == files_before

    production = _run_cli(
        _cli_arguments(
            "production",
            isolated_knowledge_layout,
            runtime_lock,
            rollout_record,
        )
    )
    assert production.returncode == 2, production.stderr
    assert production.stderr == ""
    production_report = _single_json_object(production.stdout)
    assert production_report["paths"] == development_report["paths"]
    _assert_current_sqlite_mismatch_only(
        production_report,
        mode="production",
        status="fail",
        exit_code=2,
    )
    assert production_report["gate"]["reasons"] == development_report["gate"][
        "reasons"
    ]

    state_after = _database_state(isolated_sqlite_db)
    assert state_after == state_before
    assert state_after[0].lower() == "wal"
    assert state_after[1] == []
    assert _relative_files(isolated_knowledge_layout.root) == files_before


@pytest.mark.unit
@pytest.mark.operations
def test_preflight_powershell_wrapper_is_a_thin_static_adapter() -> None:
    script = _PREFLIGHT_WRAPPER.read_text(encoding="utf-8")
    compact = " ".join(script.split())

    assert script.count("[Parameter(Mandatory = $true)]") == 7
    assert '[ValidateSet("development", "production")]' in script
    for parameter in (
        "Mode",
        "DataRoot",
        "Database",
        "BlobRoot",
        "ProjectionRoot",
        "RuntimeLock",
        "PartWriteRolloutRecord",
    ):
        assert f"[string]${parameter}" in compact

    assert '"venv\\Scripts\\python.exe"' in script
    assert '"-E"' in script
    assert '"-m", "code_assist_v1.tools.runtime_preflight"' in script
    for cli_option, parameter in (
        ("--mode", "Mode"),
        ("--data-root", "DataRoot"),
        ("--database", "Database"),
        ("--blob-root", "BlobRoot"),
        ("--projection-root", "ProjectionRoot"),
        ("--runtime-lock", "RuntimeLock"),
        ("--part-write-rollout-record", "PartWriteRolloutRecord"),
    ):
        assert f'"{cli_option}", ${parameter}' in script
    assert "& $python @moduleArguments" in script
    assert "Push-Location -LiteralPath $workspaceRoot" in script
    assert "Pop-Location" in script
    assert "if ($null -ne $LASTEXITCODE)" in script
    assert "$processExitCode = [int]$LASTEXITCODE" in script
    assert "exit $processExitCode" in script

    lowered = script.lower()
    for forbidden in (
        "$env:",
        "test-path",
        "get-volume",
        "new-item",
        "set-content",
        "out-file",
        "remove-item",
        "start-process",
    ):
        assert forbidden not in lowered


@pytest.mark.integration
@pytest.mark.operations
def test_preflight_module_resolution_ignores_injected_pythonpath(
    isolated_sqlite_db: Path,
    isolated_blob_dir: Path,
    isolated_projection_dir: Path,
    isolated_knowledge_layout,
) -> None:
    connection = sqlite3.connect(isolated_sqlite_db)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        connection.close()

    runtime_lock, rollout_record = _prepare_runtime_inputs(isolated_knowledge_layout)
    shadow_root = isolated_knowledge_layout.runtime_dir / "untrusted-pythonpath"
    shadow_package = shadow_root / "code_assist_v1"
    shadow_package.mkdir(parents=True)
    (shadow_package / "__init__.py").write_text(
        "raise RuntimeError('injected module was imported')\n", encoding="utf-8"
    )
    environment = _sanitized_environment()
    environment["PYTHONPATH"] = os.fspath(shadow_root)

    completed = _run_cli(
        _cli_arguments(
            "development",
            isolated_knowledge_layout,
            runtime_lock,
            rollout_record,
        ),
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = _single_json_object(completed.stdout)
    _assert_current_sqlite_mismatch_only(
        report,
        mode="development",
        status="warning",
        exit_code=0,
    )


@pytest.mark.integration
@pytest.mark.operations
def test_runtime_preflight_cli_does_not_import_application_config_or_stores(
    isolated_sqlite_db: Path,
    isolated_blob_dir: Path,
    isolated_projection_dir: Path,
    isolated_knowledge_layout,
) -> None:
    assert isolated_blob_dir.is_dir()
    assert isolated_projection_dir.is_dir()
    connection = sqlite3.connect(isolated_sqlite_db)
    try:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    finally:
        connection.close()

    runtime_lock, rollout_record = _prepare_runtime_inputs(isolated_knowledge_layout)
    completed = _run_import_probe(
        _cli_arguments(
            "development",
            isolated_knowledge_layout,
            runtime_lock,
            rollout_record,
        )
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = _single_json_object(completed.stdout)
    _assert_current_sqlite_mismatch_only(
        report,
        mode="development",
        status="warning",
        exit_code=0,
    )
