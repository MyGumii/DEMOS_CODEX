from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat as stat_module
import subprocess
import sys
import tempfile
from typing import Any, Iterable

import pytest

from code_assist_v1.tools.legacy_inventory_contract import (
    CATEGORY_IDS,
    TERMINAL_CLASSIFICATIONS,
    inventory_contract_errors,
    manifest_payload,
)


_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
_WORKSPACE_ROOT = _PACKAGE_ROOT.parent
_INVENTORY_MODULE = "code_assist_v1.tools.legacy_inventory"
_EXPECTED_TOP_LEVEL_FIELDS = {
    "report_schema",
    "schema_version",
    "tool",
    "run",
    "source_snapshot",
    "categories",
    "records",
    "relationships",
    "code_coverage",
    "errors",
    "exclusions",
    "limitations",
    "summary",
    "complete",
    "manifest_sha256",
    "owners",
    "reconciliation",
    "runtime",
}
_SNAPSHOT_KIND = "frozen_copy"
_SNAPSHOT_CREATED_AT_UTC = "2026-08-12T00:00:00.000000Z"
_SNAPSHOT_ATTESTATION_SHA256 = "a" * 64
_FIXTURE_PASSWORD_HASH = "fixture-password-hash"
_SECRET_VALUES = (
    "fixture-document-body-never-report-7f3a",
    "fixture-password-hash-never-report-91ce",
    "fixture-access-token-never-report-3d8b",
    "fixture-role-secret-6ac1",
    "fixture-job-status-secret-2e7d",
    "fixture-candidate-kind-secret-4f92",
    "fixture-candidate-action-secret-08bf",
    "fixture-candidate-status-secret-a540",
    "fixture-graph-type-secret-0b39",
)


@dataclass(frozen=True)
class _Snapshot:
    case_root: Path
    legacy_root: Path
    knowledge_roots: tuple[Path, ...]
    runtime_root: Path
    user_data_root: Path
    sessions_root: Path
    workspace_root: Path
    output_root: Path
    scratch_root: Path

    @property
    def source_roots(self) -> tuple[Path, ...]:
        return (
            self.legacy_root,
            *self.knowledge_roots,
            self.runtime_root,
            self.user_data_root,
            self.sessions_root,
            self.workspace_root,
        )


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8", newline="\n")


def _write_json(path: Path, value: Any) -> None:
    _write_text(
        path,
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n",
    )


def _install_complete_code_coverage_fixture(legacy_root: Path) -> None:
    calls = "\n".join(
        f"    code_assist_v1.job_manager.submit('fixture-{index}')"
        for index in range(8)
    )
    _write_text(
        legacy_root / "synthetic_jobs.py",
        "def enumerate_legacy_submitters():\n" + calls + "\n",
    )
    mapping_path = (
        legacy_root
        / "docs"
        / "knowledge_spaces"
        / "CURRENT_SYSTEM_INVENTORY_MAP.json"
    )
    mapping = {
        "expected_callsite_ids_sha256": "0" * 64,
        "expected_submitter_count": 8,
        "mapping_rules": [
            {
                "disposition": "replace",
                "mapping_id": "synthetic-job-submitters",
                "priority": 100,
                "reason": "sanitized fixture covers the eight legacy submitters",
                "replacement_phase": "P5",
                "scope_owner": "durable-job-service",
                "selector": {
                    "callee_prefixes": ["code_assist_v1.job_manager.submit"],
                    "files": ["synthetic_jobs.py"],
                    "kinds": ["job_submit"],
                },
            }
        ],
        "schema_version": 1,
    }
    _write_json(mapping_path, mapping)

    from code_assist_v1.tools.legacy_inventory_code import scan_code_coverage

    discovery = scan_code_coverage(legacy_root)
    mapping["expected_callsite_ids_sha256"] = discovery["mapping"][
        "callsite_ids_sha256"
    ]
    _write_json(mapping_path, mapping)


def _reseal_code_coverage_fixture(legacy_root: Path) -> None:
    from code_assist_v1.tools.legacy_inventory_code import scan_code_coverage

    mapping_path = (
        legacy_root
        / "docs"
        / "knowledge_spaces"
        / "CURRENT_SYSTEM_INVENTORY_MAP.json"
    )
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    mapping["expected_callsite_ids_sha256"] = "0" * 64
    _write_json(mapping_path, mapping)
    discovery = scan_code_coverage(legacy_root)
    mapping["expected_callsite_ids_sha256"] = discovery["mapping"][
        "callsite_ids_sha256"
    ]
    _write_json(mapping_path, mapping)


def _new_snapshot(isolated_data_root: Path, name: str) -> _Snapshot:
    case_root = Path(
        tempfile.mkdtemp(prefix=f"legacy-inventory-{name}-", dir=isolated_data_root)
    ).resolve(strict=True)
    snapshot = _Snapshot(
        case_root=case_root,
        legacy_root=case_root / "legacy application with spaces",
        knowledge_roots=(
            case_root / "knowledge alpha",
            case_root / "knowledge beta",
        ),
        runtime_root=case_root / "runtime snapshot",
        user_data_root=case_root / "user data snapshot",
        sessions_root=case_root / "global sessions snapshot",
        workspace_root=case_root / "workspace snapshot",
        output_root=case_root / "inventory reports",
        scratch_root=case_root / "inventory scratch",
    )
    for root in (*snapshot.source_roots, snapshot.output_root, snapshot.scratch_root):
        root.mkdir(parents=True, exist_ok=False)
    _install_complete_code_coverage_fixture(snapshot.legacy_root)
    return snapshot


def _populate_minimal_sensitive_snapshot(snapshot: _Snapshot) -> None:
    _write_json(
        snapshot.runtime_root / "users.json",
        {
            "users": {
                "fixture-user": {
                    "disabled": False,
                    "password_hash": _SECRET_VALUES[1],
                    "role": _SECRET_VALUES[3],
                }
            }
        },
    )
    _write_text(
        snapshot.knowledge_roots[0] / "fixture-user" / "note.md",
        "# Sanitized fixture\n\n" + _SECRET_VALUES[0] + "\n",
    )
    _write_json(
        snapshot.user_data_root / "fixture-user" / "sessions" / "session-1.json",
        {
            "id": "session-1",
            "messages": [{"role": "user", "content": _SECRET_VALUES[0]}],
            "token": _SECRET_VALUES[2],
        },
    )


def _install_fixture_user_registry(snapshot: _Snapshot) -> None:
    _write_json(
        snapshot.runtime_root / "users.json",
        {
            "users": {
                "fixture-user": {
                    "disabled": False,
                    "password_hash": _FIXTURE_PASSWORD_HASH,
                    "role": "user",
                }
            }
        },
    )


def _populate_incomplete_snapshot(snapshot: _Snapshot) -> None:
    _write_text(snapshot.runtime_root / "users.json", "{not-valid-json\n")
    _write_text(
        snapshot.runtime_root / "audit.jsonl",
        '{"event":"valid"}\n{not-valid-jsonl\n',
    )


def _populate_realistic_snapshot(snapshot: _Snapshot) -> Path:
    _write_json(
        snapshot.runtime_root / "users.json",
        {
            "users": {
                "fixture-user": {
                    "disabled": False,
                    "password_hash": _SECRET_VALUES[1],
                    "role": "user",
                }
            }
        },
    )
    _write_text(
        snapshot.runtime_root / "audit.jsonl",
        '{"kind":"login","status":"success"}\n',
    )

    user = snapshot.knowledge_roots[0] / "fixture-user"
    wiki = user / "wiki"
    _write_text(user / "legacy-note.md", "# Legacy\n\nSanitized body.\n")
    _write_text(
        wiki / "sources" / "source-a.md",
        "# Source A\n\n[asset](../assets/referenced.txt)\n",
    )
    _write_text(wiki / "pages" / "hidden-a.md", "# Hidden A\n")
    _write_text(wiki / "assets" / "referenced.txt", "asset bytes\n")
    _write_text(wiki / "assets" / "unreferenced.txt", "unused asset bytes\n")
    _write_text(wiki / "index.md", "# Derived index\n")
    _write_text(wiki / "log.md", "# Legacy history\n")
    _write_text(wiki / "wiki_index.sqlite", "sanitized derived index\n")
    _write_json(
        wiki / "graph" / "graph.json",
        {
            "edges": [
                {
                    "source": "source/source-a.md",
                    "target": "hidden-a.md",
                    "type": "related",
                }
            ],
            "nodes": [
                {
                    "filename": "source-a.md",
                    "id": "source/source-a.md",
                    "rel_path": "sources/source-a.md",
                },
                {
                    "filename": "hidden-a.md",
                    "id": "hidden-a.md",
                    "rel_path": "pages/hidden-a.md",
                },
            ],
        },
    )
    _write_json(wiki / "graph" / "backlinks.json", {"node-a": ["node-b"]})
    _write_json(
        wiki / "graph" / "pending_diffs.json",
        [
            {
                "action": "add_edge",
                "kind": "graph_candidate",
                "source": {"source_id": "source/source-a.md"},
                "status": "pending",
                "target_id": "hidden-a.md",
            }
        ],
    )
    _write_json(wiki / "health" / "status.json", {"status": "ok"})
    _write_json(user / "thoughts" / "thoughts.json", [{"id": "thought-a"}])
    _write_json(user / "edges" / "edges.json", [])
    _write_json(user / "inbox" / "inbox.json", [])
    _write_json(user / "raw" / "raw.json", [])
    _write_json(user / "tombstones" / "deleted.json", [])

    user_data = snapshot.user_data_root / "fixture-user"
    _write_json(
        user_data / "sessions" / "session-1.json",
        {
            "bindings": [{"kind": "notion", "page_id": "page-1"}],
            "id": "session-1",
            "messages": [
                {"content": "sanitized question", "role": "user"},
                {"content": "sanitized answer", "role": "assistant"},
            ],
            "metadata": {"title": "sanitized"},
            "summary": "sanitized summary",
            "uploads": [{"name": "sanitized.txt"}],
        },
    )
    _write_text(
        user_data / "memory" / "memories.jsonl",
        '{"kind":"memory","status":"active"}\n',
    )
    _write_text(
        user_data / "memory" / "events.jsonl",
        '{"kind":"created","status":"complete"}\n',
    )
    for job_id, status in (
        ("job-queued", "queued"),
        ("job-running", "running"),
        ("job-complete", "succeeded"),
        ("job-failed", "failed"),
    ):
        _write_json(
            user_data / "jobs" / f"{job_id}.json",
            {
                "id": job_id,
                "kind": "outlook_sync",
                "status": status,
                "user_id": "fixture-user",
            },
        )
    _write_text(user_data / "notion" / "files" / "export.txt", "export\n")
    _write_text(user_data / "skills" / "skill.md", "# Skill\n")
    _write_text(
        user_data / "projects" / "project-a" / "context" / "sources.jsonl",
        '{"kind":"text","status":"indexed"}\n',
    )
    _write_text(
        user_data / "projects" / "project-a" / "context" / "scores.jsonl",
        '{"kind":"score","status":"current"}\n',
    )
    _write_json(
        user_data / "projects" / "project-a" / "context" / "index" / "chunk.json",
        {"kind": "chunk"},
    )
    _write_text(
        user_data / "projects" / "project-a" / "context" / "assets" / "asset.txt",
        "project asset\n",
    )
    _write_json(
        snapshot.sessions_root / "global-session.json",
        {"id": "global-session", "messages": []},
    )
    _write_text(snapshot.workspace_root / "README.txt", "workspace\n")
    return _create_notion_database(snapshot)


def _populate_dirty_graph_and_assets(snapshot: _Snapshot) -> None:
    wiki = snapshot.knowledge_roots[0] / "fixture-user" / "wiki"
    _write_text(
        wiki / "sources" / "dirty.md",
        "# Dirty\n\n[missing](../assets/missing-file.bin)\n",
    )
    _write_text(wiki / "assets" / "orphaned.bin", "unreferenced\n")
    _write_json(
        wiki / "graph" / "graph.json",
        {
            "edges": [
                {
                    "source": "source/source-a.md",
                    "target": "hidden-a.md",
                    "type": "related",
                },
                {
                    "source": "source/source-a.md",
                    "target": "hidden-a.md",
                    "type": "related",
                },
                {
                    "source": "source/source-a.md",
                    "target": "missing",
                    "type": "related",
                },
                {
                    "metadata": {"auto_graph": True},
                    "source": "hidden-a.md",
                    "target": "source/source-a.md",
                    "type": _SECRET_VALUES[8],
                },
            ],
            "nodes": [
                {
                    "filename": "source-a.md",
                    "id": "source/source-a.md",
                    "rel_path": "sources/source-a.md",
                },
                {
                    "filename": "hidden-a.md",
                    "id": "hidden-a.md",
                    "rel_path": "pages/hidden-a.md",
                },
            ],
        },
    )


def _write_session_variant(snapshot: _Snapshot, marker: str) -> None:
    _write_json(
        snapshot.user_data_root
        / "fixture-user"
        / "sessions"
        / "session-1.json",
        {
            "bindings": [
                {"kind": "notion", "page_id": f"{marker}-binding-secret"}
            ],
            "id": "session-1",
            "messages": [
                {"content": f"{marker}-message-one-secret", "role": "user"},
                {
                    "content": f"{marker}-message-two-secret",
                    "role": "assistant",
                },
            ],
            "metadata": {"title": f"{marker}-metadata-secret"},
            "summary": f"{marker}-summary-secret",
            "updated_at": f"{marker}-updated-at-secret",
            "uploads": [{"name": f"{marker}-upload-secret.txt"}],
        },
    )


def _create_notion_database(snapshot: _Snapshot) -> Path:
    database = snapshot.user_data_root / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE session_pages (
                session_id TEXT NOT NULL,
                page_id TEXT NOT NULL,
                owner_user_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY(session_id, page_id)
            );
            INSERT INTO pages VALUES (
                'page-1', 'sanitized', 'fixture-user', 1, '2026-01-01T00:00:00Z'
            );
            INSERT INTO session_pages VALUES (
                'session-1', 'page-1', 'fixture-user', '2026-01-01T00:00:00Z'
            );
            """
        )
        connection.commit()
    finally:
        connection.close()
    return database


def _create_open_wal_notion_database(
    snapshot: _Snapshot,
) -> tuple[Path, sqlite3.Connection]:
    _write_json(
        snapshot.user_data_root
        / "fixture-user"
        / "sessions"
        / "session-wal.json",
        {"id": "session-wal", "messages": []},
    )
    database = snapshot.user_data_root / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    writer = sqlite3.connect(database)
    assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.executescript(
        """
        CREATE TABLE pages (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE session_pages (
            session_id TEXT NOT NULL,
            page_id TEXT NOT NULL,
            owner_user_id TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(session_id, page_id)
        );
        INSERT INTO pages VALUES (
            'page-wal', 'sanitized', 'fixture-user', 1, '2026-01-01T00:00:00Z'
        );
        INSERT INTO session_pages VALUES (
            'session-wal', 'page-wal', 'fixture-user', '2026-01-01T00:00:00Z'
        );
        """
    )
    writer.commit()
    assert Path(str(database) + "-wal").is_file()
    assert Path(str(database) + "-shm").is_file()
    return database, writer


def _complete_code_coverage(_: Path) -> dict[str, Any]:
    files = [{"language": "python", "path": "fixture.py", "sha256": "a" * 64}]
    callsites = []
    for index in range(1, 9):
        callee = f"fixture.submit.{index}"
        identity = json.dumps(
            ["fixture.py", "fixture", callee, 1],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        callsites.append(
            {
                "access": "submit",
                "callee": callee,
                "callsite_id": "callsite-"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "column": 0,
                "disposition": "migrate",
                "file": "fixture.py",
                "file_sha256": "a" * 64,
                "kind": "job_submit",
                "line": index,
                "mapping_id": f"fixture-map-{index}",
                "ordinal": 1,
                "reason": "sanitized test coverage",
                "replacement_phase": "P2",
                "scope_owner": "fixture",
                "symbol": "fixture",
            }
        )
    callsites.sort(key=lambda row: row["callsite_id"])
    callsite_ids_sha256 = hashlib.sha256(
        json.dumps(
            sorted(row["callsite_id"] for row in callsites),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    source_fingerprint_sha256 = hashlib.sha256(
        json.dumps(
            [["fixture.py", "a" * 64]],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "callsites": callsites,
        "errors": [],
        "files": files,
        "mapping": {
            "callsite_ids_sha256": callsite_ids_sha256,
            "expected_callsite_ids_sha256": callsite_ids_sha256,
            "expected_submitter_count": 8,
            "path": "fixture-map.json",
            "schema_version": 1,
            "sha256": "b" * 64,
            "unused_mapping_ids": [],
        },
        "report_schema": "code_assist.legacy_inventory.code_coverage.v1",
        "source_fingerprint_sha256": source_fingerprint_sha256,
        "summary": {
            "discovered": 8,
            "mapped": 8,
            "pass": True,
            "retained_excluded": 0,
            "submitter_count": 8,
            "unclassified": 0,
            "unmapped_submitters": 0,
        },
        "unclassified_callsite_ids": [],
        "unmapped_submitter_ids": [],
    }


def _snapshot_contract_arguments(snapshot: _Snapshot) -> dict[str, Any]:
    return {
        "scratch_root": snapshot.scratch_root,
        "snapshot_attestation_sha256": _SNAPSHOT_ATTESTATION_SHA256,
        "snapshot_created_at_utc": _SNAPSHOT_CREATED_AT_UTC,
        "snapshot_kind": _SNAPSHOT_KIND,
    }


def _sanitized_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for name in tuple(environment):
        if name == "CODE_ASSIST_FAILPOINTS" or name.startswith(
            "CODE_ASSIST_FAILPOINT_"
        ):
            environment.pop(name, None)
    return environment


def _cli_arguments(
    snapshot: _Snapshot,
    *,
    output: str | Path = "-",
    allow_partial: bool = False,
    replace_output: bool = False,
    legacy_root: str | Path | None = None,
    knowledge_roots: Iterable[Path] | None = None,
    snapshot_id: str = "sanitized-fixture-snapshot",
) -> list[str]:
    arguments = [
        "inventory",
        "--legacy-root",
        os.fspath(snapshot.legacy_root if legacy_root is None else legacy_root),
    ]
    for knowledge_root in knowledge_roots or snapshot.knowledge_roots:
        arguments.extend(("--knowledge-root", os.fspath(knowledge_root)))
    arguments.extend(
        (
            "--snapshot-id",
            snapshot_id,
            "--snapshot-kind",
            _SNAPSHOT_KIND,
            "--snapshot-created-at-utc",
            _SNAPSHOT_CREATED_AT_UTC,
            "--snapshot-attestation-sha256",
            _SNAPSHOT_ATTESTATION_SHA256,
            "--output",
            os.fspath(output),
            "--runtime-root",
            os.fspath(snapshot.runtime_root),
            "--user-data-root",
            os.fspath(snapshot.user_data_root),
            "--sessions-root",
            os.fspath(snapshot.sessions_root),
            "--workspace-root",
            os.fspath(snapshot.workspace_root),
            "--scratch-root",
            os.fspath(snapshot.scratch_root),
        )
    )
    if allow_partial:
        arguments.append("--allow-partial")
    if replace_output:
        arguments.append("--replace-output")
    return arguments


def _run_cli(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-E", "-m", _INVENTORY_MODULE, *arguments],
        cwd=_WORKSPACE_ROOT,
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _without_cli_option(arguments: list[str], option: str) -> list[str]:
    result = list(arguments)
    index = result.index(option)
    del result[index : index + 2]
    return result


def _replace_cli_option(arguments: list[str], option: str, value: str | Path) -> list[str]:
    result = list(arguments)
    result[result.index(option) + 1] = os.fspath(value)
    return result


_IMPORT_PROBE = r"""
import json
import sys

from code_assist_v1.tools import legacy_inventory

exit_code = legacy_inventory.main(sys.argv[1:])
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
    sys.stderr.write(json.dumps(loaded, sort_keys=True) + "\n")
    raise SystemExit(97)
raise SystemExit(exit_code)
"""


def _run_import_probe(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-E", "-c", _IMPORT_PROBE, *arguments],
        cwd=_WORKSPACE_ROOT,
        env=_sanitized_environment(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=60,
    )


def _single_json_object(stdout: str) -> dict[str, Any]:
    assert stdout.endswith("\n")
    assert len(stdout.splitlines()) == 1
    assert stdout == stdout.lstrip()
    report = json.loads(stdout)
    assert isinstance(report, dict)
    return report


def _source_state(snapshot: _Snapshot) -> dict[str, tuple[int, int, int, str | None]]:
    state: dict[str, tuple[int, int, int, str | None]] = {}
    for root in snapshot.source_roots:
        paths = [root, *root.rglob("*")]
        for path in sorted(paths, key=lambda item: item.as_posix()):
            stat = path.stat(follow_symlinks=False)
            digest = (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            )
            key = f"{root.name}/{path.relative_to(root).as_posix()}"
            state[key] = (stat.st_mode, stat.st_size, stat.st_mtime_ns, digest)
    return state


def _sqlite_sidecars(snapshot: _Snapshot) -> set[str]:
    return {
        path.relative_to(snapshot.case_root).as_posix()
        for root in snapshot.source_roots
        for pattern in ("*-wal", "*-shm")
        for path in root.rglob(pattern)
    }


def _records(
    report: dict[str, Any],
    category: str,
    *,
    entry_type: str | None = None,
) -> list[dict[str, Any]]:
    records = [
        row for row in report["records"] if row["category"] == category
    ]
    if entry_type is not None:
        records = [row for row in records if row["entry_type"] == entry_type]
    return records


def _sqlite_schema(database: Path) -> tuple[str, list[tuple[Any, ...]]]:
    connection = sqlite3.connect(database)
    try:
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
        schema = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "ORDER BY type, name, tbl_name"
        ).fetchall()
    finally:
        connection.close()
    return journal_mode, schema


def _assert_public_report(report: dict[str, Any]) -> None:
    assert set(report) == _EXPECTED_TOP_LEVEL_FIELDS
    assert {row["category"] for row in report["categories"]} == CATEGORY_IDS
    assert len(report["categories"]) == len(CATEGORY_IDS)
    assert all(
        isinstance(row["source_rows"], int)
        and row["source_rows"] >= 0
        and isinstance(row["error_count"], int)
        and row["error_count"] >= 0
        for row in report["categories"]
    )
    assert report["schema_version"] == 2
    assert report["reconciliation"]["stage"] == "inventory_proposal"
    assert report["reconciliation"]["total_records"] == len(report["records"])
    assert report["reconciliation"]["proposed_classified_records"] == len(
        report["records"]
    )
    assert report["reconciliation"]["unclassified_records"] == 0
    assert all(
        row["proposed_terminal_classification"] in TERMINAL_CLASSIFICATIONS
        and row["classification_authority"] in {"planner_required", "scanner"}
        and row["record_kind"]
        in {"logical_record", "physical_file", "unavailable"}
        for row in report["records"]
    )
    assert inventory_contract_errors(report) == ()
    assert report["manifest_sha256"] == hashlib.sha256(
        json.dumps(
            manifest_payload(report),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


@pytest.mark.integration
@pytest.mark.migration
def test_inventory_empty_cli_stdout_is_complete_deterministic_and_read_only(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "empty")
    before = _source_state(snapshot)
    sidecars_before = _sqlite_sidecars(snapshot)

    first = _run_cli(_cli_arguments(snapshot))
    second = _run_cli(_cli_arguments(snapshot))

    first_report = _single_json_object(first.stdout)
    second_report = _single_json_object(second.stdout)
    assert first.returncode == second.returncode == 0, (
        first_report["errors"],
        second_report["errors"],
        first_report["code_coverage"],
    )
    assert first.stderr == second.stderr == ""
    _assert_public_report(first_report)
    _assert_public_report(second_report)
    assert first_report["complete"] is True
    assert second_report["complete"] is True
    assert first_report["source_snapshot"]["kind"] == _SNAPSHOT_KIND
    assert (
        first_report["source_snapshot"]["created_at_utc"]
        == _SNAPSHOT_CREATED_AT_UTC
    )
    assert (
        first_report["source_snapshot"]["attestation_sha256"]
        == _SNAPSHOT_ATTESTATION_SHA256
    )
    assert first_report["source_snapshot"]["scratch_root"]["root_id"] == "scratch"
    assert first_report["manifest_sha256"] == second_report["manifest_sha256"]
    assert manifest_payload(first_report) == manifest_payload(second_report)
    assert [row["category"] for row in first_report["categories"]] == sorted(
        CATEGORY_IDS
    )
    category_counts = {
        row["category"]: row["records"] for row in first_report["categories"]
    }
    assert category_counts["unavailable.browser_pins"] == 1
    assert all(
        count == 0
        for category, count in category_counts.items()
        if category != "unavailable.browser_pins"
    )
    assert _source_state(snapshot) == before
    assert _sqlite_sidecars(snapshot) == sidecars_before == set()


@pytest.mark.integration
@pytest.mark.migration
def test_cli_report_never_contains_source_content_or_credentials(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "sensitive")
    _populate_minimal_sensitive_snapshot(snapshot)
    _create_notion_database(snapshot)
    before = _source_state(snapshot)
    sidecars_before = _sqlite_sidecars(snapshot)

    completed = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = _single_json_object(completed.stdout)
    _assert_public_report(report)
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for secret in _SECRET_VALUES:
        assert secret not in serialized
    assert "Traceback (most recent call last)" not in serialized
    assert _source_state(snapshot) == before
    assert _sqlite_sidecars(snapshot) == sidecars_before


@pytest.mark.integration
@pytest.mark.migration
def test_inventory_strict_and_allow_partial_surface_corrupt_json_and_jsonl(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "dirty")
    _populate_incomplete_snapshot(snapshot)
    before = _source_state(snapshot)

    strict = _run_cli(_cli_arguments(snapshot))
    partial = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert strict.returncode == 2
    assert partial.returncode == 0
    assert strict.stderr == partial.stderr == ""
    strict_report = _single_json_object(strict.stdout)
    partial_report = _single_json_object(partial.stdout)
    _assert_public_report(strict_report)
    _assert_public_report(partial_report)
    assert strict_report["complete"] is False
    assert partial_report["complete"] is False
    error_codes = {error["code"] for error in strict_report["errors"]}
    assert {"INVALID_JSON", "INVALID_JSONL"}.issubset(error_codes)
    assert manifest_payload(strict_report) == manifest_payload(partial_report)
    assert _source_state(snapshot) == before


@pytest.mark.integration
@pytest.mark.migration
def test_non_finite_json_and_jsonl_numbers_fail_closed_without_leakage(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "non-finite-json")
    _write_text(
        snapshot.runtime_root / "users.json",
        '{"users":{"fixture-user":{"active":NaN,"role":"user"}}}\n',
    )
    _write_text(
        snapshot.runtime_root / "audit.jsonl",
        '{"kind":"audit","status":Infinity}\n',
    )

    strict = _run_cli(_cli_arguments(snapshot))
    partial = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert strict.returncode == 2
    assert partial.returncode == 0
    strict_report = _single_json_object(strict.stdout)
    partial_report = _single_json_object(partial.stdout)
    codes = {row["code"] for row in strict_report["errors"]}
    assert {"INVALID_JSON", "INVALID_JSONL"}.issubset(codes)
    differing = [
        key
        for key in manifest_payload(strict_report)
        if manifest_payload(strict_report)[key]
        != manifest_payload(partial_report)[key]
    ]
    assert strict_report["manifest_sha256"] == partial_report["manifest_sha256"], (
        differing,
        {key: (strict_report[key], partial_report[key]) for key in differing},
    )
    serialized = json.dumps(strict_report, ensure_ascii=False, sort_keys=True)
    assert "NaN" not in serialized
    assert "Infinity" not in serialized


@pytest.mark.integration
@pytest.mark.migration
def test_inventory_realistic_snapshot_reconciles_children_links_assets_and_jobs(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "realistic")
    notion_database = _populate_realistic_snapshot(snapshot)
    before = _source_state(snapshot)
    sqlite_before = _sqlite_schema(notion_database)
    sidecars_before = _sqlite_sidecars(snapshot)

    completed = _run_cli(_cli_arguments(snapshot))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = _single_json_object(completed.stdout)
    _assert_public_report(report)
    assert report["complete"] is True
    assert len(report["source_snapshot"]["root_binding_sha256"]) == 64
    assert all(
        row["status"] == "present" for row in report["source_snapshot"]["roles"]
    )
    assert len(report["runtime"]["app_revision"]) == 64
    assert (
        report["runtime"]["app_revision"]
        == report["code_coverage"]["source_fingerprint_sha256"]
    )
    assert len(report["tool"]["code_revision"]) == 64
    assert len(report["tool"]["code_tree_sha256"]) == 64
    assert len(report["tool"]["mapping_sha256"]) == 64
    assert (
        report["tool"]["mapping_sha256"]
        == report["code_coverage"]["mapping"]["sha256"]
    )
    fixture_owner = next(
        row for row in report["owners"] if row["user_id"] == "fixture-user"
    )
    assert fixture_owner["account_status"] == "active"
    assert fixture_owner["mapping_status"] == "confirmed"
    assert fixture_owner["role"] == "user"
    assert fixture_owner["registry_record_id"].startswith("rec_")
    assert fixture_owner["claimed_user_ids"] == ["fixture-user"]
    assert fixture_owner["source_paths"]
    assert report["summary"]["account_count"] == 1
    assert report["summary"]["user_count"] == 1
    root_ids = {root["root_id"] for root in report["source_snapshot"]["roots"]}
    assert {"knowledge_000", "knowledge_001"}.issubset(root_ids)

    graph = _records(
        report, "knowledge.wiki_graph_edges", entry_type="file"
    )[0]
    assert graph["parse"]["graph"] == {
        "duplicate": 0,
        "edges": 1,
        "legacy_auto": 0,
        "nodes": 2,
        "orphan": 0,
        "valid": 1,
    }

    session = _records(
        report, "conversation.user_sessions", entry_type="file"
    )[0]
    assert session["parse"]["child_counts"] == {
        "bindings": 1,
        "messages": 2,
        "metadata": 1,
        "session_core_metadata": 1,
        "summaries": 1,
        "uploads": 1,
    }
    assert session["parse"]["child_count"] == 7

    notion = _records(report, "external.notion_database", entry_type="file")[0]
    assert notion["parse"]["table_counts"] == {"pages": 1, "session_pages": 1}
    notion_links = [
        row
        for row in report["relationships"]
        if row["relationship_type"] == "notion_session_page"
    ]
    assert len(notion_links) == 1
    notion_link_record = _records(
        report, "external.notion_session_links", entry_type="logical_record"
    )[0]
    assert notion_links[0]["status"] == "valid"
    assert notion_links[0]["from_record_id"] == session["record_id"]
    assert notion_links[0]["source_record_id"] == notion_link_record["record_id"]
    assert notion_link_record["link_resolution"]["session_record_id"] == session[
        "record_id"
    ]
    assert len(notion_link_record["link_resolution"]["page_identity_sha256"]) == 64

    jobs = _records(report, "operations.jobs", entry_type="file")
    assert len(jobs) == 4
    assert sum(row["status"] == "abandoned_legacy" for row in jobs) == 2
    for row in jobs:
        if row["status"] == "abandoned_legacy":
            assert row["reason_codes"] == ["legacy_job_not_replayable"]
            assert "replay" not in row

    sources = _records(report, "knowledge.wiki_sources", entry_type="file")
    assert len(sources) == 1
    assert sources[0]["references"] == {
        "declared": 1,
        "missing": 0,
        "resolved": 1,
    }
    assets = _records(report, "knowledge.wiki_assets", entry_type="file")
    assert len(assets) == 2
    assert sum("unreferenced_asset" in row["reason_codes"] for row in assets) == 1

    assert _source_state(snapshot) == before
    assert _sqlite_schema(notion_database) == sqlite_before
    assert _sqlite_sidecars(snapshot) == sidecars_before


@pytest.mark.integration
@pytest.mark.migration
def test_inventory_dirty_graph_and_assets_are_counted_without_raw_type_leakage(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "dirty-graph")
    _populate_realistic_snapshot(snapshot)
    _populate_dirty_graph_and_assets(snapshot)
    before = _source_state(snapshot)

    strict = _run_cli(_cli_arguments(snapshot))
    partial = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert strict.returncode == 2
    assert partial.returncode == 0
    assert strict.stderr == partial.stderr == ""
    strict_report = _single_json_object(strict.stdout)
    partial_report = _single_json_object(partial.stdout)
    differing = [
        key
        for key in manifest_payload(strict_report)
        if manifest_payload(strict_report)[key]
        != manifest_payload(partial_report)[key]
    ]
    assert strict_report["manifest_sha256"] == partial_report["manifest_sha256"], (
        differing,
        {key: (strict_report[key], partial_report[key]) for key in differing},
    )
    graph = _records(
        strict_report, "knowledge.wiki_graph_edges", entry_type="file"
    )[0]
    assert graph["parse"]["graph"] == {
        "duplicate": 1,
        "edges": 4,
        "legacy_auto": 0,
        "nodes": 2,
        "orphan": 2,
        "valid": 1,
    }
    graph_relationships = [
        row
        for row in strict_report["relationships"]
        if row["relationship_type"] in {"related", "unknown"}
    ]
    assert len(graph_relationships) == 4
    assert sum(row["status"] == "duplicate" for row in graph_relationships) == 1
    assert sum(row["status"] == "orphan" for row in graph_relationships) == 2
    assert sum(row["status"] == "legacy_auto" for row in graph_relationships) == 0

    dirty_source = next(
        row
        for row in _records(
            strict_report, "knowledge.wiki_sources", entry_type="file"
        )
        if row["references"]["missing"] == 1
    )
    assert dirty_source["references"] == {
        "declared": 1,
        "missing": 1,
        "resolved": 0,
    }
    assert sum(
        "unreferenced_asset" in row["reason_codes"]
        for row in _records(
            strict_report, "knowledge.wiki_assets", entry_type="file"
        )
    ) == 2
    serialized = json.dumps(strict_report, ensure_ascii=False, sort_keys=True)
    for secret in _SECRET_VALUES[3:]:
        assert secret not in serialized
    assert _source_state(snapshot) == before


@pytest.mark.integration
@pytest.mark.migration
def test_session_child_hashes_change_with_payload_but_never_disclose_it(
    isolated_data_root: Path,
) -> None:
    snapshots = (
        _new_snapshot(isolated_data_root, "session-variant-a"),
        _new_snapshot(isolated_data_root, "session-variant-b"),
    )
    reports: list[dict[str, Any]] = []
    for snapshot, marker in zip(snapshots, ("variant-a", "variant-b"), strict=True):
        _install_fixture_user_registry(snapshot)
        _write_session_variant(snapshot, marker)
        completed = _run_cli(_cli_arguments(snapshot))
        assert completed.returncode == 0, completed.stderr
        reports.append(_single_json_object(completed.stdout))
        serialized = json.dumps(reports[-1], ensure_ascii=False, sort_keys=True)
        for suffix in (
            "binding-secret",
            "message-one-secret",
            "message-two-secret",
            "metadata-secret",
            "summary-secret",
            "updated-at-secret",
            "upload-secret.txt",
        ):
            assert f"{marker}-{suffix}" not in serialized

    sources = [
        _records(report, "conversation.user_sessions", entry_type="file")[0]
        for report in reports
    ]
    expected_counts = {
        "bindings": 1,
        "messages": 2,
        "metadata": 1,
        "session_core_metadata": 1,
        "summaries": 1,
        "uploads": 1,
    }
    for source in sources:
        assert source["parse"]["child_counts"] == expected_counts
        assert source["parse"]["child_count"] == sum(expected_counts.values())
        assert set(source["parse"]["child_aggregate_sha256"]) == set(
            expected_counts
        )
    assert all(
        sources[0]["parse"]["child_aggregate_sha256"][kind]
        != sources[1]["parse"]["child_aggregate_sha256"][kind]
        for kind in expected_counts
    )

    child_sets = [
        _records(report, "conversation.user_sessions", entry_type="logical_record")
        for report in reports
    ]
    for children in child_sets:
        assert len(children) == sum(expected_counts.values())
        assert {
            kind: sum(row["child_kind"] == kind for row in children)
            for kind in expected_counts
        } == expected_counts
        assert {row["child_kind"] for row in children}.issubset(expected_counts)
    for kind in expected_counts:
        first_digests = sorted(
            row["sha256"]
            for row in child_sets[0]
            if row["child_kind"] == kind
        )
        second_digests = sorted(
            row["sha256"]
            for row in child_sets[1]
            if row["child_kind"] == kind
        )
        assert first_digests != second_digests


@pytest.mark.integration
@pytest.mark.migration
def test_notion_database_is_opened_ro_query_only_and_clock_is_injectable(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "notion-read-only")
    notion_database = _populate_realistic_snapshot(snapshot)
    before = _source_state(snapshot)
    sqlite_before = _sqlite_schema(notion_database)
    sidecars_before = _sqlite_sidecars(snapshot)
    original_connect = sqlite3.connect
    connection_calls: list[tuple[str, bool]] = []
    statements: list[str] = []

    class _ConnectionProxy:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, *args: Any) -> sqlite3.Cursor:
            statements.append(" ".join(statement.split()).upper())
            return self._connection.execute(statement, *args)

        def close(self) -> None:
            self._connection.close()

    def tracking_connect(database: str, *args: Any, **kwargs: Any) -> _ConnectionProxy:
        connection_calls.append((os.fspath(database), kwargs.get("uri") is True))
        return _ConnectionProxy(original_connect(database, *args, **kwargs))

    monkeypatch.setattr(legacy_inventory.sqlite3, "connect", tracking_connect)
    fixed_time = datetime(2026, 8, 13, 3, 4, 5, tzinfo=timezone.utc)
    keyword_arguments = {
        **_snapshot_contract_arguments(snapshot),
        "runtime_root": snapshot.runtime_root,
        "user_data_root": snapshot.user_data_root,
        "sessions_root": snapshot.sessions_root,
        "workspace_root": snapshot.workspace_root,
        "clock": lambda: fixed_time,
    }

    first, first_exit = legacy_inventory.inventory_snapshot(
        snapshot.legacy_root,
        snapshot.knowledge_roots,
        "fixed-snapshot",
        **keyword_arguments,
    )
    second, second_exit = legacy_inventory.inventory_snapshot(
        snapshot.legacy_root,
        snapshot.knowledge_roots,
        "fixed-snapshot",
        **keyword_arguments,
    )

    assert first_exit == second_exit == 0
    assert first == second
    assert first["run"] == {
        "completed_at_utc": "2026-08-13T03:04:05.000000Z",
        "run_id": first["run"]["run_id"],
        "started_at_utc": "2026-08-13T03:04:05.000000Z",
    }
    assert connection_calls
    notion_calls = [path for path, uri in connection_calls if uri]
    assert notion_calls
    assert all(path.endswith("notion.db?mode=ro") for path in notion_calls)
    assert "PRAGMA QUERY_ONLY=ON" in statements
    assert "PRAGMA QUERY_ONLY" in statements
    assert _source_state(snapshot) == before
    assert _sqlite_schema(notion_database) == sqlite_before
    assert _sqlite_sidecars(snapshot) == sidecars_before


@pytest.mark.integration
@pytest.mark.migration
def test_notion_empty_tables_are_accounted_and_unknown_tables_fail_strict(
    isolated_data_root: Path,
) -> None:
    empty_snapshot = _new_snapshot(isolated_data_root, "notion-empty")
    _install_fixture_user_registry(empty_snapshot)
    empty_database = (
        empty_snapshot.user_data_root
        / "fixture-user"
        / "notion"
        / "notion.db"
    )
    empty_database.parent.mkdir(parents=True)
    connection = sqlite3.connect(empty_database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              title TEXT,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            """
        )
        connection.commit()
    finally:
        connection.close()

    completed = _run_cli(_cli_arguments(empty_snapshot))
    assert completed.returncode == 0, completed.stderr
    report = _single_json_object(completed.stdout)
    notion = _records(report, "external.notion_database", entry_type="file")[0]
    assert notion["parse"]["table_counts"] == {"pages": 0, "session_pages": 0}
    assert notion["parse"]["table_row_count"] == 0
    assert _records(
        report, "external.notion_session_links", entry_type="logical_record"
    ) == []

    unknown_snapshot = _new_snapshot(isolated_data_root, "notion-unknown-table")
    _install_fixture_user_registry(unknown_snapshot)
    database = _create_notion_database(unknown_snapshot)
    unknown_name = "private_table_name_never_report"
    connection = sqlite3.connect(database)
    try:
        connection.execute(f'CREATE TABLE "{unknown_name}" (secret TEXT)')
        connection.commit()
    finally:
        connection.close()

    strict = _run_cli(_cli_arguments(unknown_snapshot))
    partial = _run_cli(_cli_arguments(unknown_snapshot, allow_partial=True))
    assert strict.returncode == 2
    assert partial.returncode == 0
    strict_report = _single_json_object(strict.stdout)
    partial_report = _single_json_object(partial.stdout)
    assert "UNKNOWN_SQLITE_TABLE" in {
        row["code"] for row in strict_report["errors"]
    }
    assert strict_report["manifest_sha256"] == partial_report["manifest_sha256"]
    assert unknown_name not in json.dumps(strict_report, ensure_ascii=False)


@pytest.mark.integration
@pytest.mark.migration
def test_notion_links_resolve_sessions_and_fail_closed_on_owner_or_target_mismatch(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "notion-link-resolution")
    _install_fixture_user_registry(snapshot)
    _write_json(
        snapshot.user_data_root
        / "fixture-user"
        / "sessions"
        / "session-1.json",
        {"id": "session-1", "messages": []},
    )
    database = snapshot.user_data_root / "fixture-user" / "notion" / "notion.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """
            CREATE TABLE pages (
              id TEXT PRIMARY KEY,
              title TEXT,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL
            );
            CREATE TABLE session_pages (
              session_id TEXT NOT NULL,
              page_id TEXT NOT NULL,
              owner_user_id TEXT NOT NULL,
              created_at INTEGER NOT NULL,
              PRIMARY KEY(session_id, page_id)
            );
            INSERT INTO pages VALUES ('page-1', 'sanitized', 'fixture-user', 1);
            INSERT INTO session_pages VALUES ('session-1', 'page-1', 'ghost-user', 1);
            INSERT INTO session_pages VALUES ('missing-session', 'page-1', 'fixture-user', 2);
            INSERT INTO session_pages VALUES ('session-1', 'missing-page', 'fixture-user', 3);
            """
        )
        connection.commit()
    finally:
        connection.close()

    strict = _run_cli(_cli_arguments(snapshot))
    partial = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert strict.returncode == 2
    assert partial.returncode == 0, partial.stdout
    report = _single_json_object(strict.stdout)
    links = _records(
        report, "external.notion_session_links", entry_type="logical_record"
    )
    assert len(links) == 3
    assert {row["planned_action"] for row in links} == {
        "quarantine_candidate",
        "report_only",
    }
    assert {row["proposed_terminal_classification"] for row in links} == {
        "quarantined",
        "unavailable",
    }
    missing = [row for row in links if row["status"] == "unavailable"]
    assert len(missing) == 2
    assert all(row["planned_action"] == "report_only" for row in missing)
    assert any(
        row["link_resolution"]["session_record_id"] is None for row in missing
    )
    mismatch = next(row for row in links if row["status"] != "unavailable")
    assert mismatch["planned_action"] == "quarantine_candidate"
    assert "MISSING_REFERENCE" in {row["code"] for row in report["errors"]}
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert "ghost-user" not in serialized
    assert "missing-session" not in serialized
    assert "missing-page" not in serialized


@pytest.mark.integration
@pytest.mark.migration
def test_open_wal_notion_snapshot_is_copied_before_read_and_never_mutated(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "notion-open-wal")
    _install_fixture_user_registry(snapshot)
    database, writer = _create_open_wal_notion_database(snapshot)
    original_connect = sqlite3.connect
    connection_calls: list[tuple[str, bool]] = []

    class _ConnectionProxy:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, statement: str, *args: Any) -> sqlite3.Cursor:
            return self._connection.execute(statement, *args)

        def close(self) -> None:
            self._connection.close()

    def tracking_connect(database_value: str, *args: Any, **kwargs: Any) -> _ConnectionProxy:
        connection_calls.append(
            (os.fspath(database_value), kwargs.get("uri") is True)
        )
        return _ConnectionProxy(original_connect(database_value, *args, **kwargs))

    try:
        before = _source_state(snapshot)
        sidecars_before = _sqlite_sidecars(snapshot)
        temporary_before = {
            path.resolve()
            for path in snapshot.scratch_root.glob("legacy-inventory-sqlite-*")
        }
        monkeypatch.setattr(legacy_inventory.sqlite3, "connect", tracking_connect)

        report, exit_code = legacy_inventory.inventory_snapshot(
            snapshot.legacy_root,
            snapshot.knowledge_roots,
            "open-wal-snapshot",
            runtime_root=snapshot.runtime_root,
            user_data_root=snapshot.user_data_root,
            sessions_root=snapshot.sessions_root,
            workspace_root=snapshot.workspace_root,
            code_coverage_provider=_complete_code_coverage,
            clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
            **_snapshot_contract_arguments(snapshot),
        )

        assert exit_code == 0, report["errors"]
        notion = _records(
            report, "external.notion_database", entry_type="file"
        )[0]
        assert notion["parse"]["status"] == "valid"
        read_only_uris = [path for path, uri in connection_calls if uri]
        assert read_only_uris
        source_uri = database.resolve().as_uri()
        assert all(not path.startswith(source_uri) for path in read_only_uris)
        assert _source_state(snapshot) == before
        assert _sqlite_sidecars(snapshot) == sidecars_before
        assert {
            path.resolve()
            for path in snapshot.scratch_root.glob("legacy-inventory-sqlite-*")
        } == temporary_before
    finally:
        writer.close()


@pytest.mark.integration
@pytest.mark.migration
def test_same_size_same_mtime_source_change_is_detected_by_high_water_hash(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "same-metadata-change")
    source = snapshot.knowledge_roots[0] / "fixture-user" / "note.md"
    original = b"original-payload"
    replacement = b"modified-payload"
    assert len(original) == len(replacement)
    source.parent.mkdir(parents=True)
    source.write_bytes(original)
    _write_json(
        snapshot.runtime_root / "users.json",
        {
            "users": {
                "fixture-user": {
                    "disabled": False,
                    "password_hash": _FIXTURE_PASSWORD_HASH,
                    "role": "user",
                }
            }
        },
    )
    original_stat = source.stat()
    mutated = False
    original_scan_root = legacy_inventory._scan_root

    def mutate_after_before_capture(root: Any, state: Any) -> None:
        nonlocal mutated
        if not mutated:
            source.write_bytes(replacement)
            os.utime(
                source,
                ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
            )
            mutated = True
        original_scan_root(root, state)

    try:
        monkeypatch.setattr(
            legacy_inventory,
            "_scan_root",
            mutate_after_before_capture,
        )
        report, exit_code = legacy_inventory.inventory_snapshot(
            snapshot.legacy_root,
            snapshot.knowledge_roots,
            "same-metadata-change",
            runtime_root=snapshot.runtime_root,
            user_data_root=snapshot.user_data_root,
            sessions_root=snapshot.sessions_root,
            workspace_root=snapshot.workspace_root,
            allow_partial=True,
            code_coverage_provider=_complete_code_coverage,
            clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
            **_snapshot_contract_arguments(snapshot),
        )

        assert mutated is True
        after_stat = source.stat()
        assert (after_stat.st_size, after_stat.st_mtime_ns) == (
            original_stat.st_size,
            original_stat.st_mtime_ns,
        )
        assert source.read_bytes() == replacement
        assert exit_code == 2
        assert "SOURCE_CHANGED" in {row["code"] for row in report["errors"]}
        high_water = next(
            row
            for row in report["source_snapshot"]["high_water_marks"]
            if row["root_id"] == "knowledge_000"
        )
        assert high_water["changed"] is True
        assert high_water["before"]["tree_sha256"] != high_water["after"][
            "tree_sha256"
        ]
    finally:
        source.write_bytes(original)
        os.utime(
            source,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )


@pytest.mark.integration
@pytest.mark.migration
def test_cli_imports_no_application_config_or_store_modules(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "import-probe")

    completed = _run_import_probe(_cli_arguments(snapshot))

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    report = _single_json_object(completed.stdout)
    assert report["complete"] is True


@pytest.mark.integration
@pytest.mark.migration
def test_cli_rejects_unc_relative_roots_and_empty_snapshot_id(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "invalid-roots")

    unc = _run_cli(
        _cli_arguments(snapshot, legacy_root=r"\\fixture-server\legacy-share")
    )
    relative = _run_cli(_cli_arguments(snapshot, legacy_root="relative-root"))
    empty_snapshot = _run_cli(_cli_arguments(snapshot, snapshot_id=""))

    assert unc.returncode == relative.returncode == empty_snapshot.returncode == 2
    unc_report = _single_json_object(unc.stdout)
    relative_report = _single_json_object(relative.stdout)
    empty_report = _single_json_object(empty_snapshot.stdout)
    assert "ROOT_INELIGIBLE" in {row["code"] for row in unc_report["errors"]}
    assert "ROOT_NOT_ABSOLUTE" in {
        row["code"] for row in relative_report["errors"]
    }
    assert "CLI_INVALID_ARGUMENT" in {
        row["code"] for row in empty_report["errors"]
    }

    missing_required = _run_cli(
        [
            "inventory",
            "--legacy-root",
            os.fspath(snapshot.legacy_root),
            "--snapshot-id",
            "missing-knowledge-root",
            "--output",
            "-",
        ]
    )
    assert missing_required.returncode == 2
    assert {row["code"] for row in _single_json_object(missing_required.stdout)["errors"]} == {
        "CLI_INVALID_ARGUMENT"
    }


@pytest.mark.integration
@pytest.mark.migration
def test_cli_requires_frozen_snapshot_attestation_and_all_source_roles(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "snapshot-contract")
    base = _cli_arguments(snapshot)

    invalid_cases = [
        _without_cli_option(base, "--snapshot-kind"),
        _without_cli_option(base, "--snapshot-created-at-utc"),
        _without_cli_option(base, "--snapshot-attestation-sha256"),
        _without_cli_option(base, "--scratch-root"),
        _replace_cli_option(base, "--snapshot-kind", "live_directory"),
        _replace_cli_option(base, "--snapshot-created-at-utc", "not-a-timestamp"),
        _replace_cli_option(base, "--snapshot-attestation-sha256", "A" * 64),
        _replace_cli_option(base, "--snapshot-attestation-sha256", "a" * 63),
    ]
    for arguments in invalid_cases:
        completed = _run_cli(arguments)
        assert completed.returncode == 2
        assert completed.stderr == ""
        assert "CLI_INVALID_ARGUMENT" in {
            row["code"] for row in _single_json_object(completed.stdout)["errors"]
        }

    for option, root_id in (
        ("--runtime-root", "runtime"),
        ("--user-data-root", "user_data"),
        ("--sessions-root", "sessions"),
        ("--workspace-root", "workspace"),
    ):
        completed = _run_cli(_without_cli_option(base, option))
        assert completed.returncode == 2
        report = _single_json_object(completed.stdout)
        error = next(
            row
            for row in report["errors"]
            if row["code"] == "ROOT_MISSING" and row["root_id"] == root_id
        )
        assert error["operation"] == "validate_required_role"
        assert error["reason_codes"] == ["source_role_not_declared"]


@pytest.mark.integration
@pytest.mark.migration
def test_scratch_root_inside_source_or_reparse_is_rejected(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "scratch-boundary")
    before = _source_state(snapshot)
    inside = _run_cli(
        _replace_cli_option(
            _cli_arguments(snapshot),
            "--scratch-root",
            snapshot.knowledge_roots[0],
        )
    )
    assert inside.returncode == 2
    inside_report = _single_json_object(inside.stdout)
    assert "ROOT_OVERLAP" in {row["code"] for row in inside_report["errors"]}
    assert _source_state(snapshot) == before

    target = snapshot.output_root / "scratch-target"
    target.mkdir()
    link = snapshot.case_root / "scratch-reparse"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert created.returncode == 0, created.stderr
    try:
        reparse = _run_cli(
            _replace_cli_option(
                _cli_arguments(snapshot), "--scratch-root", link
            )
        )
        assert reparse.returncode == 2
        reparse_report = _single_json_object(reparse.stdout)
        assert {row["code"] for row in reparse_report["errors"]} & {
            "ROOT_INELIGIBLE",
            "UNSAFE_REPARSE_ENTRY",
        }
    finally:
        if os.path.lexists(link):
            os.rmdir(link)


@pytest.mark.integration
@pytest.mark.migration
def test_reparse_escape_is_rejected_without_reading_target(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "reparse-escape")
    outside = snapshot.case_root / "outside all source roots"
    outside.mkdir()
    marker = "outside-target-content-never-read-458d"
    _write_text(outside / "secret.txt", marker)
    link = snapshot.knowledge_roots[0] / "fixture-user" / "escape"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        assert created.returncode == 0, created.stderr

    try:
        completed = _run_cli(_cli_arguments(snapshot))
        assert completed.returncode == 2
        assert completed.stderr == ""
        report = _single_json_object(completed.stdout)
        codes = {row["code"] for row in report["errors"]}
        assert codes & {"PATH_ESCAPES_ROOT", "UNSAFE_REPARSE_ENTRY"}
        assert marker not in json.dumps(report, ensure_ascii=False, sort_keys=True)
    finally:
        if os.path.lexists(link):
            os.rmdir(link)


@pytest.mark.integration
@pytest.mark.migration
def test_derived_physical_roots_nested_under_legacy_are_scanned_once(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "derived-roots")
    _write_json(
        snapshot.legacy_root / ".runtime" / "users.json",
        {
            "users": {
                "fixture-user": {
                    "disabled": False,
                    "password_hash": _FIXTURE_PASSWORD_HASH,
                    "role": "user",
                }
            }
        },
    )
    for child in ("user_data", "sessions", "workspace"):
        (snapshot.legacy_root / child).mkdir()
    before = _source_state(snapshot)
    arguments = [
        "inventory",
        "--legacy-root",
        os.fspath(snapshot.legacy_root),
        "--knowledge-root",
        os.fspath(snapshot.knowledge_roots[0]),
        "--snapshot-id",
        "derived-root-snapshot",
        "--snapshot-kind",
        _SNAPSHOT_KIND,
        "--snapshot-created-at-utc",
        _SNAPSHOT_CREATED_AT_UTC,
        "--snapshot-attestation-sha256",
        _SNAPSHOT_ATTESTATION_SHA256,
        "--scratch-root",
        os.fspath(snapshot.scratch_root),
        "--output",
        "-",
    ]

    completed = _run_cli(arguments)

    assert completed.returncode == 0, completed.stderr
    report = _single_json_object(completed.stdout)
    roots = report["source_snapshot"]["roots"]
    assert len({row["root_id"] for row in roots}) == len(roots)
    derived = {row["root_id"] for row in roots if row["path_source"] != "explicit"}
    assert derived == {"runtime", "sessions", "user_data", "workspace"}
    user_records = _records(report, "identity.users_registry")
    assert len([row for row in user_records if row["entry_type"] == "file"]) == 1
    assert len(
        [row for row in user_records if row["entry_type"] == "logical_record"]
    ) == 1
    assert _source_state(snapshot) == before


@pytest.mark.integration
@pytest.mark.migration
def test_embedded_physical_root_is_not_hidden_by_an_explicit_external_root(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "embedded-plus-explicit")
    marker = "embedded-runtime-content-never-report"
    _write_text(
        snapshot.legacy_root / ".runtime" / "audit.jsonl",
        json.dumps({"event": "login", "status": "success", "detail": marker})
        + "\n",
    )
    before = _source_state(snapshot)

    completed = _run_cli(_cli_arguments(snapshot))

    assert completed.returncode == 2
    report = _single_json_object(completed.stdout)
    assert report["complete"] is False
    duplicate = next(row for row in report["errors"] if row["code"] == "ROOT_DUPLICATE")
    assert duplicate["operation"] == "validate_required_role"
    assert duplicate["reason_codes"] == ["multiple_role_roots"]
    root_ids = {row["root_id"] for row in report["source_snapshot"]["roots"]}
    assert {"runtime", "runtime_embedded"}.issubset(root_ids)
    assert marker not in json.dumps(report, ensure_ascii=False, sort_keys=True)
    assert _source_state(snapshot) == before


@pytest.mark.integration
@pytest.mark.migration
def test_embedded_logs_and_knowledge_roots_are_each_scanned_once(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "embedded-data-roots")
    _install_fixture_user_registry(snapshot)
    _write_text(snapshot.legacy_root / "logs" / "server.log", "sanitized log\n")
    _write_text(
        snapshot.legacy_root / "knowledge" / "fixture-user" / "embedded.md",
        "# Embedded knowledge\n",
    )
    _write_text(
        snapshot.legacy_root
        / "user_knowledge"
        / "fixture-user"
        / "user-embedded.md",
        "# User embedded knowledge\n",
    )
    before = _source_state(snapshot)

    completed = _run_cli(_cli_arguments(snapshot))

    assert completed.returncode == 0, completed.stderr
    report = _single_json_object(completed.stdout)
    root_ids = {row["root_id"] for row in report["source_snapshot"]["roots"]}
    assert {"logs", "knowledge_embedded", "knowledge_user_embedded"}.issubset(
        root_ids
    )
    assert sum(
        row["root_id"] == "logs" and row["category"] == "operations.server_logs"
        for row in report["records"]
    ) == 1
    assert sum(
        row["root_id"] == "knowledge_embedded"
        and row["relative_path"] == "fixture-user/embedded.md"
        for row in report["records"]
    ) == 1
    assert sum(
        row["root_id"] == "knowledge_user_embedded"
        and row["relative_path"] == "fixture-user/user-embedded.md"
        for row in report["records"]
    ) == 1
    assert _source_state(snapshot) == before


@pytest.mark.integration
@pytest.mark.migration
def test_unregistered_path_owners_are_unresolved_and_never_confirmed_personal(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "unresolved-owner")
    _install_fixture_user_registry(snapshot)
    _write_text(
        snapshot.knowledge_roots[0] / "ghost-user" / "note.md",
        "# Unmapped owner\n",
    )
    _write_json(
        snapshot.user_data_root / "ghost-user" / "jobs" / "job.json",
        {"id": "ghost-job", "status": "queued"},
    )

    completed = _run_cli(_cli_arguments(snapshot))
    partial = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert completed.returncode == 2
    assert partial.returncode == 0
    report = _single_json_object(completed.stdout)
    partial_report = _single_json_object(partial.stdout)
    assert partial_report["complete"] is False
    assert "MISSING_REFERENCE" in {
        row["code"] for row in partial_report["errors"]
    }
    unresolved = [
        row
        for row in report["records"]
        if row["root_id"] in {"knowledge_000", "user_data"}
        and row["entry_type"] == "file"
    ]
    assert len(unresolved) == 2
    assert all(
        row["owner"]
        == {
            "claimed_user_id": "ghost-user",
            "kind": "unresolved_path_owner",
            "mapping_status": "unresolved",
            "user_id": None,
        }
        for row in unresolved
    )
    assert all(row["planned_action"] == "quarantine_candidate" for row in unresolved)
    assert sum(
        row["owner"]["kind"] == "personal"
        and row["owner"]["mapping_status"] == "confirmed"
        for row in unresolved
    ) == 0
    missing_owner_errors = [
        row
        for row in report["errors"]
        if row["code"] == "MISSING_REFERENCE"
        and row["reason_codes"] == ["user_not_in_registry"]
    ]
    assert len(missing_owner_errors) == 2


@pytest.mark.integration
@pytest.mark.migration
def test_exact_legacy_taxonomy_routes_known_files_and_near_misses_fail_closed(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "taxonomy")
    _install_fixture_user_registry(snapshot)
    _write_text(
        snapshot.knowledge_roots[0] / "edges.jsonl",
        '{"source":"a","target":"b","type":"related"}\n',
    )
    _write_text(
        snapshot.knowledge_roots[0] / "tombstones.jsonl",
        '{"kind":"deleted","status":"archived"}\n',
    )
    notion = snapshot.user_data_root / "fixture-user" / "notion"
    _write_text(notion / "notion.db.bak_20260101", "backup\n")
    _write_text(notion / "notion.db-wal", "wal\n")
    _write_text(notion / "notion.db-shm", "shm\n")
    _write_json(
        snapshot.user_data_root / "fixture-user" / "outlook" / "state.json",
        {"status": "active"},
    )
    _write_text(snapshot.runtime_root / "server.log", "runtime log\n")
    _write_text(snapshot.user_data_root / "server.log", "user-data root log\n")

    _write_text(notion / "notion.db.bakery", "near miss\n")
    _write_json(
        snapshot.user_data_root
        / "fixture-user"
        / "outlook"
        / "state.json.extra",
        {},
    )
    _write_text(snapshot.runtime_root / "logs" / "server.log", "nested near miss\n")

    completed = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert completed.returncode == 0
    report = _single_json_object(completed.stdout)
    assert report["complete"] is False
    rows = {
        (row["root_id"], row["relative_path"]): (
            row["category"],
            row["planned_action"],
        )
        for row in report["records"]
        if row["entry_type"] == "file"
    }
    assert rows[("knowledge_000", "edges.jsonl")] == (
        "knowledge.legacy_edges",
        "validate_or_quarantine",
    )
    assert rows[("knowledge_000", "tombstones.jsonl")] == (
        "knowledge.tombstones",
        "preserve_history",
    )
    assert rows[("user_data", "fixture-user/notion/notion.db.bak_20260101")] == (
        "external.notion_backup_candidates",
        "quarantine_candidate",
    )
    for sidecar in ("notion.db-wal", "notion.db-shm"):
        assert rows[("user_data", f"fixture-user/notion/{sidecar}")] == (
            "external.notion_database_sidecars",
            "retain_external",
        )
    assert rows[("user_data", "fixture-user/outlook/state.json")] == (
        "external.outlook_state",
        "retain_external",
    )
    assert rows[("runtime", "server.log")] == (
        "operations.server_logs",
        "quarantine_candidate",
    )
    assert rows[("user_data", "server.log")] == (
        "operations.server_logs",
        "quarantine_candidate",
    )
    for key in (
        ("user_data", "fixture-user/notion/notion.db.bakery"),
        ("user_data", "fixture-user/outlook/state.json.extra"),
        ("runtime", "logs/server.log"),
    ):
        assert rows[key] == ("unknown.unclassified", "quarantine")
    assert sum(row["code"] == "UNKNOWN_ENTRY" for row in report["errors"]) == 3


@pytest.mark.integration
@pytest.mark.migration
def test_legacy_edges_and_pending_candidates_keep_only_hashed_evidence_and_counts(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "edges-candidates")
    _install_fixture_user_registry(snapshot)
    endpoint_a = "raw-endpoint-a-never-report"
    endpoint_b = "raw-endpoint-b-never-report"
    endpoint_c = "raw-endpoint-c-never-report"
    _write_text(
        snapshot.knowledge_roots[0] / "fixture-user" / "edges.jsonl",
        "".join(
            (
                json.dumps(
                    {"source": endpoint_a, "target": endpoint_b, "type": "related"}
                )
                + "\n",
                json.dumps(
                    {"source": endpoint_a, "target": endpoint_b, "type": "related"}
                )
                + "\n",
                json.dumps(
                    {"source": endpoint_b, "target": endpoint_c, "type": "supports"}
                )
                + "\n",
            )
        ),
    )
    source_secret = "candidate-source-evidence-never-report"
    target_secret = "candidate-target-evidence-never-report"
    revision_secret = "candidate-revision-evidence-never-report"
    _write_json(
        snapshot.knowledge_roots[0]
        / "fixture-user"
        / "wiki"
        / "graph"
        / "pending_diffs.json",
        [
            {
                "action": "add_edge",
                "base_revision": revision_secret,
                "candidate_kind": "graph_candidate",
                "source": {"filename": source_secret},
                "status": "pending",
                "target_id": target_secret,
            },
            {
                "action": "create",
                "kind": "wiki_diff",
                "source": {"raw_path": source_secret + "-two"},
                "status": "rejected",
            },
        ],
    )

    completed = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert completed.returncode == 0, completed.stderr
    report = _single_json_object(completed.stdout)
    assert report["complete"] is False
    assert "MISSING_REFERENCE" in {row["code"] for row in report["errors"]}
    edge_source = _records(
        report, "knowledge.legacy_edges", entry_type="file"
    )[0]
    assert edge_source["parse"]["status_counts"] == {
        "duplicate": 1,
        "unresolved_legacy_reference": 2,
    }
    assert edge_source["parse"]["kind_counts"] == {
        "related": 2,
        "supports": 1,
    }
    edge_relationships = [
        row
        for row in report["relationships"]
        if row["source_record_id"]
        in {
            child["record_id"]
            for child in _records(
                report, "knowledge.legacy_edges", entry_type="logical_record"
            )
        }
    ]
    assert len(edge_relationships) == 3
    assert sum(row["status"] == "duplicate" for row in edge_relationships) == 1
    expected_endpoint_hashes = {
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for value in (endpoint_a, endpoint_b, endpoint_c)
    }
    assert {
        value
        for row in edge_relationships
        for value in (row["from_ref_sha256"], row["to_ref_sha256"])
    } == expected_endpoint_hashes

    candidate_source = _records(
        report, "knowledge.wiki_candidates", entry_type="file"
    )[0]
    assert candidate_source["parse"]["candidate_counts"] == {
        "actions": {"add_edge": 1, "create": 1},
        "evidence": {
            "base_or_revision": 1,
            "identity_evidence": 1,
            "resolved": 0,
            "source": 2,
            "target": 1,
        },
        "kinds": {"graph_candidate": 1, "wiki_diff": 1},
        "statuses": {"pending": 1, "rejected": 1},
    }
    candidates = _records(
        report, "knowledge.wiki_candidates", entry_type="logical_record"
    )
    assert [row["candidate"]["action"] for row in candidates] == [
        "add_edge",
        "create",
    ]
    assert [row["candidate"]["kind"] for row in candidates] == [
        "graph_candidate",
        "wiki_diff",
    ]
    assert [row["candidate"]["status"] for row in candidates] == [
        "pending",
        "rejected",
    ]
    assert all(row["planned_action"] == "quarantine_candidate" for row in candidates)
    assert all(row["candidate"]["resolution"]["status"] == "unresolved" for row in candidates)
    serialized = json.dumps(report, ensure_ascii=False, sort_keys=True)
    for raw in (
        endpoint_a,
        endpoint_b,
        endpoint_c,
        source_secret,
        source_secret + "-two",
        target_secret,
        revision_secret,
    ):
        assert raw not in serialized


@pytest.mark.integration
@pytest.mark.migration
def test_windows_reparse_attribute_is_rejected_when_path_apis_report_false(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "synthetic-reparse-attribute")
    candidate = snapshot.knowledge_roots[0] / "reparse-candidate.txt"
    _write_text(candidate, "ordinary file with synthetic attributes\n")
    assert candidate.is_symlink() is False
    junction_api = getattr(os.path, "isjunction", lambda _: False)
    assert junction_api(candidate) is False
    original_scandir = os.scandir
    reparse_flag = int(
        getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )

    class _StatProxy:
        def __init__(self, value: os.stat_result) -> None:
            self._value = value
            self.st_file_attributes = reparse_flag

        def __getattr__(self, name: str) -> Any:
            return getattr(self._value, name)

    class _EntryProxy:
        def __init__(self, value: os.DirEntry[str]) -> None:
            self._value = value
            self.name = value.name
            self.path = value.path

        def stat(self, *, follow_symlinks: bool = True) -> Any:
            value = self._value.stat(follow_symlinks=follow_symlinks)
            return _StatProxy(value) if Path(self.path) == candidate else value

        def is_dir(self, *, follow_symlinks: bool = True) -> bool:
            return self._value.is_dir(follow_symlinks=follow_symlinks)

        def is_file(self, *, follow_symlinks: bool = True) -> bool:
            return self._value.is_file(follow_symlinks=follow_symlinks)

    class _ScandirProxy:
        def __init__(self, path: str | os.PathLike[str]) -> None:
            self._delegate = original_scandir(path)

        def __enter__(self) -> _ScandirProxy:
            self._delegate.__enter__()
            return self

        def __exit__(self, *args: Any) -> Any:
            return self._delegate.__exit__(*args)

        def __iter__(self) -> Any:
            return (_EntryProxy(entry) for entry in self._delegate)

    monkeypatch.setattr(legacy_inventory.os, "scandir", _ScandirProxy)

    report, exit_code = legacy_inventory.inventory_snapshot(
        snapshot.legacy_root,
        snapshot.knowledge_roots,
        "synthetic-reparse-attribute",
        runtime_root=snapshot.runtime_root,
        user_data_root=snapshot.user_data_root,
        sessions_root=snapshot.sessions_root,
        workspace_root=snapshot.workspace_root,
        allow_partial=True,
        code_coverage_provider=_complete_code_coverage,
        clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        **_snapshot_contract_arguments(snapshot),
    )

    assert exit_code == 2
    assert "UNSAFE_REPARSE_ENTRY" in {row["code"] for row in report["errors"]}
    unsafe = next(
        row for row in report["records"] if row["relative_path"] == candidate.name
    )
    assert unsafe["entry_type"] == "reparse"
    assert unsafe["record_kind"] == "unavailable"
    assert unsafe["planned_action"] == "quarantine"
    assert unsafe["reason_codes"] == ["unsafe_reparse_entry"]
    _assert_public_report(report)


@pytest.mark.integration
@pytest.mark.migration
def test_allow_partial_never_bypasses_missing_or_invalid_root_gate(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "hard-root-gate")
    missing = snapshot.case_root / "missing knowledge root"
    file_root = snapshot.case_root / "knowledge-root-is-a-file"
    _write_text(file_root, "not a directory\n")

    cases = (
        (
            _cli_arguments(
                snapshot,
                allow_partial=True,
                knowledge_roots=(missing,),
            ),
            "ROOT_MISSING",
        ),
        (
            _cli_arguments(
                snapshot,
                allow_partial=True,
                knowledge_roots=(file_root,),
            ),
            "ROOT_NOT_DIRECTORY",
        ),
        (
            _cli_arguments(
                snapshot,
                allow_partial=True,
                legacy_root=snapshot.case_root / "missing legacy root",
            ),
            "ROOT_MISSING",
        ),
    )
    for arguments, expected_code in cases:
        completed = _run_cli(arguments)
        assert completed.returncode == 2
        report = _single_json_object(completed.stdout)
        assert report["complete"] is False
        assert expected_code in {row["code"] for row in report["errors"]}


@pytest.mark.integration
@pytest.mark.migration
def test_allow_partial_never_bypasses_code_coverage_integrity_gate(
    isolated_data_root: Path,
) -> None:
    seal_snapshot = _new_snapshot(isolated_data_root, "hard-code-seal")
    seal_mapping = (
        seal_snapshot.legacy_root
        / "docs"
        / "knowledge_spaces"
        / "CURRENT_SYSTEM_INVENTORY_MAP.json"
    )
    mapping = json.loads(seal_mapping.read_text(encoding="utf-8"))
    mapping["expected_callsite_ids_sha256"] = "0" * 64
    _write_json(seal_mapping, mapping)

    unclassified_snapshot = _new_snapshot(
        isolated_data_root, "hard-code-unclassified"
    )
    _write_text(
        unclassified_snapshot.legacy_root / "unclassified_store_call.py",
        "def unresolved_callsite():\n"
        "    code_assist_v1.memory_store.load_memory('fixture')\n",
    )
    _reseal_code_coverage_fixture(unclassified_snapshot.legacy_root)

    seal = _run_cli(_cli_arguments(seal_snapshot, allow_partial=True))
    unclassified = _run_cli(
        _cli_arguments(unclassified_snapshot, allow_partial=True)
    )

    assert seal.returncode == unclassified.returncode == 2
    seal_report = _single_json_object(seal.stdout)
    unclassified_report = _single_json_object(unclassified.stdout)
    assert "UNMAPPED_CALLSITE" in {
        row["code"] for row in seal_report["errors"]
    }
    assert "mapping_callsite_set_mismatch" in {
        row["code"] for row in seal_report["code_coverage"]["errors"]
    }
    assert "UNMAPPED_CALLSITE" in {
        row["code"] for row in unclassified_report["errors"]
    }
    assert unclassified_report["code_coverage"]["summary"]["unclassified"] == 1


@pytest.mark.integration
@pytest.mark.migration
def test_allow_partial_never_bypasses_normalized_path_collision_gate(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "hard-path-collision")
    _install_fixture_user_registry(snapshot)
    first = snapshot.knowledge_roots[0] / "fixture-user" / "note①.md"
    second = snapshot.knowledge_roots[0] / "fixture-user" / "note1.md"
    _write_text(first, "first normalization variant\n")
    _write_text(second, "second normalization variant\n")
    assert first != second
    assert first.is_file() and second.is_file()

    completed = _run_cli(_cli_arguments(snapshot, allow_partial=True))

    assert completed.returncode == 2
    report = _single_json_object(completed.stdout)
    assert report["complete"] is False
    assert "PATH_KEY_COLLISION" in {row["code"] for row in report["errors"]}


@pytest.mark.integration
@pytest.mark.migration
def test_output_is_atomic_and_requires_explicit_replace(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "output-policy")
    output = snapshot.output_root / "inventory report.json"
    sentinel = b'{"sentinel":true}\n'
    output.write_bytes(sentinel)
    source_before = _source_state(snapshot)

    refused = _run_cli(_cli_arguments(snapshot, output=output))

    assert refused.returncode == 2
    assert output.read_bytes() == sentinel
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
    replaced = _run_cli(
        _cli_arguments(snapshot, output=output, replace_output=True)
    )
    assert replaced.returncode == 0, replaced.stderr
    assert replaced.stderr == ""
    assert replaced.stdout == ""
    output_text = output.read_text(encoding="utf-8")
    assert output_text.endswith("\n")
    assert len(output_text.splitlines()) == 1
    report = json.loads(output_text)
    _assert_public_report(report)
    assert report["complete"] is True
    assert output.read_bytes() != sentinel
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
    assert _source_state(snapshot) == source_before


@pytest.mark.integration
@pytest.mark.migration
def test_output_created_after_validation_is_never_clobbered(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "output-race")
    output = snapshot.output_root / "race-winner.json"
    winner = b'{"winner":"external-writer"}\n'

    def create_winner_after_validation(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], int]:
        output.write_bytes(winner)
        return legacy_inventory._error_report("CLI_INVALID_ARGUMENT"), 2

    monkeypatch.setattr(
        legacy_inventory,
        "inventory_snapshot",
        create_winner_after_validation,
    )

    exit_code = legacy_inventory.main(_cli_arguments(snapshot, output=output))
    captured = capsys.readouterr()

    assert exit_code == 3
    assert output.read_bytes() == winner
    assert captured.err == ""
    report = _single_json_object(captured.out)
    assert {row["code"] for row in report["errors"]} == {"OUTPUT_WRITE_FAILED"}
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))


@pytest.mark.integration
@pytest.mark.migration
def test_output_late_winner_at_handle_commit_is_preserved(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "output-late-winner")
    output = snapshot.output_root / "inventory.json"
    winner = b'{"winner":"late-external-writer"}\n'
    source_before = _source_state(snapshot)
    original_rename = legacy_inventory._nt_rename_relative
    attempted = False

    def racing_rename(
        handle: int,
        root_handle: int,
        name: str,
        *,
        replace: bool,
    ) -> None:
        nonlocal attempted
        attempted = True
        assert replace is False
        output.write_bytes(winner)
        original_rename(
            handle,
            root_handle,
            name,
            replace=replace,
        )

    monkeypatch.setattr(
        legacy_inventory, "_nt_rename_relative", racing_rename
    )

    exit_code = legacy_inventory.main(_cli_arguments(snapshot, output=output))
    captured = capsys.readouterr()

    assert attempted is True
    assert exit_code == 3
    assert captured.err == ""
    report = _single_json_object(captured.out)
    assert {row["code"] for row in report["errors"]} == {
        "OUTPUT_WRITE_FAILED"
    }
    assert output.read_bytes() == winner
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
    assert _source_state(snapshot) == source_before


@pytest.mark.integration
@pytest.mark.migration
@pytest.mark.parametrize("parent_change", ("identity", "reparse"))
def test_output_parent_change_after_validation_fails_without_any_report_write(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    parent_change: str,
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, f"output-parent-{parent_change}")
    output = snapshot.output_root / "inventory.json"
    approved_parent = snapshot.case_root / "approved-output-parent-original"
    reparse_target = snapshot.case_root / "replacement-output-target"
    changed = False

    def replace_parent_after_validation(
        *args: Any, **kwargs: Any
    ) -> tuple[dict[str, Any], int]:
        nonlocal changed
        snapshot.output_root.rename(approved_parent)
        if parent_change == "identity":
            snapshot.output_root.mkdir()
        else:
            reparse_target.mkdir()
            try:
                snapshot.output_root.symlink_to(
                    reparse_target, target_is_directory=True
                )
            except OSError:
                created = subprocess.run(
                    [
                        "cmd.exe",
                        "/d",
                        "/c",
                        "mklink",
                        "/J",
                        os.fspath(snapshot.output_root),
                        os.fspath(reparse_target),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                if created.returncode != 0:
                    pytest.skip("Windows output-parent reparse creation unavailable")
        changed = True
        return legacy_inventory._error_report("CLI_INVALID_ARGUMENT"), 2

    monkeypatch.setattr(
        legacy_inventory,
        "inventory_snapshot",
        replace_parent_after_validation,
    )

    try:
        exit_code = legacy_inventory.main(_cli_arguments(snapshot, output=output))
        captured = capsys.readouterr()

        assert changed is True
        assert exit_code == 3
        assert captured.err == ""
        report = _single_json_object(captured.out)
        assert {row["code"] for row in report["errors"]} == {
            "OUTPUT_WRITE_FAILED"
        }
        assert not output.exists()
        assert not (approved_parent / output.name).exists()
        assert not list(approved_parent.glob(".legacy-inventory-*.tmp"))
        if parent_change == "identity":
            assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
        else:
            assert not list(reparse_target.glob(".legacy-inventory-*.tmp"))
    finally:
        if parent_change == "reparse" and os.path.lexists(snapshot.output_root):
            os.rmdir(snapshot.output_root)


@pytest.mark.integration
@pytest.mark.migration
def test_output_parent_is_handle_pinned_before_temp_file_creation(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "output-parent-pinned")
    output = snapshot.output_root / "inventory.json"
    displaced = snapshot.case_root / "displaced-output-parent"
    original_create = legacy_inventory._WindowsDirectoryAnchor.create_relative_file
    attempted = False

    def racing_create(self: Any, name: str) -> int:
        nonlocal attempted
        attempted = True
        with pytest.raises(OSError):
            snapshot.output_root.rename(displaced)
        return original_create(self, name)

    monkeypatch.setattr(
        legacy_inventory._WindowsDirectoryAnchor,
        "create_relative_file",
        racing_create,
    )

    exit_code = legacy_inventory.main(_cli_arguments(snapshot, output=output))
    captured = capsys.readouterr()

    assert attempted is True
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert output.is_file()
    assert not displaced.exists()
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))


@pytest.mark.integration
@pytest.mark.migration
def test_output_temp_handle_cannot_be_moved_into_a_source_before_commit(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "output-temp-pinned")
    output = snapshot.output_root / "inventory.json"
    source_before = _source_state(snapshot)
    original_write = legacy_inventory._write_windows_handle
    attempted = False

    def racing_write(handle: int, payload: bytes) -> None:
        nonlocal attempted
        attempted = True
        temporary_files = list(
            snapshot.output_root.glob(".legacy-inventory-*.tmp")
        )
        assert len(temporary_files) == 1
        escaped = snapshot.knowledge_roots[0] / temporary_files[0].name
        with pytest.raises(OSError):
            temporary_files[0].rename(escaped)
        assert not escaped.exists()
        original_write(handle, payload)

    monkeypatch.setattr(
        legacy_inventory, "_write_windows_handle", racing_write
    )

    exit_code = legacy_inventory.main(_cli_arguments(snapshot, output=output))
    captured = capsys.readouterr()

    assert attempted is True
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert output.is_file()
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
    assert _source_state(snapshot) == source_before


@pytest.mark.integration
@pytest.mark.migration
def test_output_temp_handle_cannot_be_hardlinked_into_a_source_while_writing(
    isolated_data_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from code_assist_v1.tools import legacy_inventory

    snapshot = _new_snapshot(isolated_data_root, "output-temp-unlinkable")
    output = snapshot.output_root / "inventory.json"
    source_before = _source_state(snapshot)
    original_write = legacy_inventory._write_windows_handle
    attempted = False

    def racing_write(handle: int, payload: bytes, *, flush: bool = True) -> None:
        nonlocal attempted
        attempted = True
        temporary_files = list(
            snapshot.output_root.glob(".legacy-inventory-*.tmp")
        )
        assert len(temporary_files) == 1
        escaped = snapshot.knowledge_roots[0] / temporary_files[0].name
        with pytest.raises(OSError):
            os.link(temporary_files[0], escaped)
        assert not escaped.exists()
        original_write(handle, payload, flush=flush)

    monkeypatch.setattr(
        legacy_inventory, "_write_windows_handle", racing_write
    )

    exit_code = legacy_inventory.main(_cli_arguments(snapshot, output=output))
    captured = capsys.readouterr()

    assert attempted is True
    assert exit_code == 0
    assert captured.out == ""
    assert captured.err == ""
    assert output.is_file()
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
    assert _source_state(snapshot) == source_before


@pytest.mark.integration
@pytest.mark.migration
def test_output_inside_any_source_root_is_rejected_without_writing(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "inside-source")
    output = snapshot.knowledge_roots[1] / "inventory.json"
    before = _source_state(snapshot)

    completed = _run_cli(_cli_arguments(snapshot, output=output))

    assert completed.returncode == 2
    assert not output.exists()
    assert _source_state(snapshot) == before


@pytest.mark.integration
@pytest.mark.migration
def test_missing_output_parent_and_write_failure_use_exit_three(
    isolated_data_root: Path,
) -> None:
    snapshot = _new_snapshot(isolated_data_root, "missing-output-parent")
    output = snapshot.output_root / "missing" / "inventory.json"

    completed = _run_cli(_cli_arguments(snapshot, output=output))

    assert completed.returncode == 3
    assert not output.exists()

    directory_target = snapshot.output_root / "directory-target"
    directory_target.mkdir()
    write_failure = _run_cli(
        _cli_arguments(
            snapshot,
            output=directory_target,
            replace_output=True,
        )
    )
    assert write_failure.returncode == 3
    report = _single_json_object(write_failure.stdout)
    assert {error["code"] for error in report["errors"]} == {
        "OUTPUT_WRITE_FAILED"
    }
    assert not list(snapshot.output_root.glob(".legacy-inventory-*.tmp"))
