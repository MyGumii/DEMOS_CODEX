from __future__ import annotations

from datetime import datetime, timezone
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from code_assist_v1.knowledge_core.cli import _write_artifact
from code_assist_v1.tools.legacy_inventory import inventory_snapshot
from code_assist_v1.tools.legacy_inventory_contract import (
    canonical_json_bytes,
    inventory_contract_errors,
)


_WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
_PYTHON = _WORKSPACE_ROOT / "venv" / "Scripts" / "python.exe"
_CLI_MODULE = "code_assist_v1.knowledge_core.cli"
_ATTESTATION = "a" * 64


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _probe(path: str) -> dict[str, Any]:
    resolved = str(Path(path).resolve(strict=True))
    stat = Path(path).stat()
    return {
        "eligible": True,
        "exists": True,
        "filesystem_identity": {
            "device": int(stat.st_dev),
            "inode": int(stat.st_ino),
        },
        "reason_codes": [],
        "reparse": {"components": [], "present": False},
        "resolved_path": resolved,
        "syntactic_unc": False,
        "volume": {"drive_type": "fixed", "filesystem": "NTFS", "root": "D:\\"},
    }


def _coverage(_: Path) -> dict[str, Any]:
    files = [{"language": "python", "path": "fixture.py", "sha256": "a" * 64}]
    calls = []
    for index in range(1, 9):
        callee = f"fixture.submit.{index}"
        identity = json.dumps(
            ["fixture.py", "fixture", callee, 1],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        calls.append(
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
    calls.sort(key=lambda row: row["callsite_id"])
    call_ids = hashlib.sha256(
        json.dumps(
            sorted(row["callsite_id"] for row in calls),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    source_fingerprint = hashlib.sha256(
        json.dumps(
            [["fixture.py", "a" * 64]],
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "callsites": calls,
        "errors": [],
        "files": files,
        "mapping": {
            "callsite_ids_sha256": call_ids,
            "expected_callsite_ids_sha256": call_ids,
            "expected_submitter_count": 8,
            "path": "fixture-map.json",
            "schema_version": 1,
            "sha256": "b" * 64,
            "unused_mapping_ids": [],
        },
        "report_schema": "code_assist.legacy_inventory.code_coverage.v1",
        "source_fingerprint_sha256": source_fingerprint,
        "status": "complete",
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


def _clock() -> datetime:
    return datetime(2026, 8, 13, 0, 0, 0, tzinfo=timezone.utc)


def _inventory(case: Path) -> tuple[dict[str, Any], tuple[Path, ...]]:
    roots = {
        "legacy": case / "legacy",
        "knowledge": [case / "knowledge-a", case / "knowledge-b"],
        "runtime": case / "runtime",
        "user_data": case / "user-data",
        "sessions": case / "sessions",
        "workspace": case / "workspace",
        "scratch": case / "scratch",
    }
    all_roots = (
        roots["legacy"],
        *roots["knowledge"],
        roots["runtime"],
        roots["user_data"],
        roots["sessions"],
        roots["workspace"],
        roots["scratch"],
    )
    for path in all_roots:
        path.mkdir(parents=True)
    _write(
        roots["runtime"] / "users.json",
        b'{"users":{"fixture-user":{"disabled":false,"password_hash":"fixture-hash","role":"admin"}}}',
    )
    report, exit_code = inventory_snapshot(
        roots["legacy"],
        roots["knowledge"],
        "p2-2-sanitized-snapshot",
        snapshot_kind="frozen_copy",
        snapshot_created_at_utc="2026-08-13T00:00:00.000000Z",
        snapshot_attestation_sha256=_ATTESTATION,
        runtime_root=roots["runtime"],
        user_data_root=roots["user_data"],
        sessions_root=roots["sessions"],
        workspace_root=roots["workspace"],
        scratch_root=roots["scratch"],
        clock=_clock,
        code_coverage_provider=_coverage,
        root_probe=_probe,
    )
    assert exit_code == 0, report["errors"]
    assert inventory_contract_errors(report) == ()
    return report, tuple(all_roots[:-1])


def _mapping_document() -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    writer.writerow(
        [
            "mapping_schema_version",
            "source_owner_claim",
            "source_record_id",
            "decision",
            "user_id",
            "system_role",
            "account_status",
            "responsibility_code",
            "responsibility_id",
            "team_code",
            "team_id",
            "part_code",
            "part_id",
            "is_part_leader",
        ]
    )
    writer.writerow(
        ["1", "fixture-user", "", "personal", "fixture-user", "admin", "active", "", "", "", "", "", "", "false"]
    )
    return stream.getvalue().encode("utf-8")


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "TEMP": r"D:\CodexTestTemp",
            "TMP": r"D:\CodexTestTemp",
            "TMPDIR": r"D:\CodexTestTemp",
        }
    )
    return subprocess.run(
        [str(_PYTHON), "-E", "-m", _CLI_MODULE, *arguments],
        cwd=_WORKSPACE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _concurrent_artifact_writes(
    output: Path, artifacts: list[dict[str, Any]]
) -> list[str]:
    gate = output.parent / "start-concurrent-writes"
    script = r'''
import json
from pathlib import Path
import sys
import time
from code_assist_v1.knowledge_core.cli import _write_artifact

output = sys.argv[1]
artifact = json.loads(sys.argv[2])
gate = Path(sys.argv[3])
deadline = time.monotonic() + 20
while not gate.exists():
    if time.monotonic() >= deadline:
        raise SystemExit(97)
    time.sleep(0.001)
result = _write_artifact(output, artifact, protected_inputs=())
print(result or "OK")
'''
    processes = [
        subprocess.Popen(
            [
                str(_PYTHON),
                "-E",
                "-c",
                script,
                str(output),
                json.dumps(artifact, separators=(",", ":"), sort_keys=True),
                str(gate),
            ],
            cwd=_WORKSPACE_ROOT,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "TEMP": r"D:\CodexTestTemp",
                "TMP": r"D:\CodexTestTemp",
                "TMPDIR": r"D:\CodexTestTemp",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for artifact in artifacts
    ]
    gate.write_text("start", encoding="ascii")
    results: list[str] = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        results.append(stdout.strip())
    gate.unlink()
    return results


def _plan_files(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any], tuple[Path, ...]]:
    report, sources = _inventory(tmp_path / "source")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    inventory = artifacts / "inventory.json"
    mapping = artifacts / "organization-map.csv"
    output = artifacts / "draft.json"
    _write(inventory, canonical_json_bytes(report) + b"\n")
    _write(mapping, _mapping_document())
    return inventory, mapping, output, report, sources


def _plan_arguments(
    inventory: Path, mapping: Path, output: Path, report: dict[str, Any]
) -> list[str]:
    return [
        "migrate",
        "plan",
        "--inventory",
        str(inventory),
        "--inventory-manifest-sha256",
        report["manifest_sha256"],
        "--snapshot-attestation-sha256",
        report["source_snapshot"]["attestation_sha256"],
        "--root-binding-sha256",
        report["source_snapshot"]["root_binding_sha256"],
        "--org-map",
        str(mapping),
        "--org-map-sha256",
        hashlib.sha256(mapping.read_bytes()).hexdigest(),
        "--output",
        str(output),
    ]


def test_p2_plan_cli_builds_and_confirms_a_hash_bound_reviewed_plan_without_source_writes(
    tmp_path: Path,
) -> None:
    inventory, mapping, draft_path, report, sources = _plan_files(tmp_path)
    source_before = {
        str(path): sorted(
            (str(row.relative_to(path)), row.read_bytes())
            for row in path.rglob("*")
            if row.is_file()
        )
        for path in sources
    }

    planned = _run(_plan_arguments(inventory, mapping, draft_path, report))

    assert planned.returncode == 0, planned.stdout + planned.stderr
    assert planned.stdout == ""
    assert planned.stderr == ""
    draft_bytes = draft_path.read_bytes()
    draft = json.loads(draft_bytes)
    assert draft["stage"] == "migration_plan_draft"
    assert draft["reconciliation"]["unclassified_records"] == 0
    assert draft["reconciliation"]["target_part_record_count"] == 0
    assert all(row["target_space_kind"] in (None, "personal") for row in draft["records"])

    reviewed_path = draft_path.with_name("reviewed-plan.json")
    confirmed = _run(
        [
            "migrate",
            "confirm-plan",
            "--draft",
            str(draft_path),
            "--draft-sha256",
            hashlib.sha256(draft_bytes).hexdigest(),
            "--review-attestation-sha256",
            "c" * 64,
            "--reviewed-at-utc",
            "2026-08-13T01:02:03.000000Z",
            "--reviewer-id",
            "reviewer-01",
            "--output",
            str(reviewed_path),
        ]
    )

    assert confirmed.returncode == 0, confirmed.stdout + confirmed.stderr
    reviewed = json.loads(reviewed_path.read_bytes())
    assert reviewed["stage"] == "reviewed_migration_plan"
    assert reviewed["review"]["confirmed_technical_plan_sha256"] == draft["technical_plan_sha256"]
    assert reviewed["draft"] == draft
    assert source_before == {
        str(path): sorted(
            (str(row.relative_to(path)), row.read_bytes())
            for row in path.rglob("*")
            if row.is_file()
        )
        for path in sources
    }


def test_p2_plan_cli_is_idempotent_for_identical_bytes_and_never_clobbers_a_different_winner(
    tmp_path: Path,
) -> None:
    inventory, mapping, output, report, _ = _plan_files(tmp_path)
    arguments = _plan_arguments(inventory, mapping, output, report)
    first = _run(arguments)
    expected = output.read_bytes()

    replay = _run(arguments)
    assert first.returncode == 0
    assert replay.returncode == 0
    assert output.read_bytes() == expected

    winner = output.with_name("winner.json")
    winner.write_bytes(b'{"winner":"external"}\n')
    drift = _run(_plan_arguments(inventory, mapping, winner, report))
    assert drift.returncode == 2
    assert json.loads(drift.stdout)["error"]["code"] == "PLAN_DRIFT"
    assert winner.read_bytes() == b'{"winner":"external"}\n'


def test_output_preflight_late_winner_is_exact_replay_or_plan_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import code_assist_v1.tools.legacy_inventory as inventory_module

    output = tmp_path / "artifacts" / "draft.json"
    output.parent.mkdir()
    artifact = {"source_snapshot": {"roots": []}, "stage": "migration_plan_draft"}
    expected = canonical_json_bytes(artifact) + b"\n"

    def identical_winner(*_: Any, **__: Any) -> tuple[None, str]:
        output.write_bytes(expected)
        return None, "OUTPUT_EXISTS"

    monkeypatch.setattr(
        inventory_module, "_prepare_output_guard", identical_winner
    )
    assert (
        _write_artifact(str(output), artifact, protected_inputs=()) is None
    )
    assert output.read_bytes() == expected

    output.unlink()

    def different_winner(*_: Any, **__: Any) -> tuple[None, str]:
        output.write_bytes(b'{"winner":"external"}\n')
        return None, "OUTPUT_EXISTS"

    monkeypatch.setattr(
        inventory_module, "_prepare_output_guard", different_winner
    )
    assert (
        _write_artifact(str(output), artifact, protected_inputs=())
        == "PLAN_DRIFT"
    )
    assert output.read_bytes() == b'{"winner":"external"}\n'


def test_declared_missing_source_root_still_blocks_offline_output_overlap(
    tmp_path: Path,
) -> None:
    missing_source = tmp_path / "frozen-source" / "child"
    artifact = {
        "source_snapshot": {
            "roots": [{"resolved_path": str(missing_source)}]
        },
        "stage": "migration_plan_draft",
    }
    output = tmp_path / "frozen-source" / "draft.json"
    output.parent.mkdir()

    assert not missing_source.exists()
    assert (
        _write_artifact(str(output), artifact, protected_inputs=())
        == "OUTPUT_INSIDE_SOURCE"
    )
    assert not output.exists()


def test_cross_process_artifact_create_and_exact_replay_converge_on_one_winner(
    tmp_path: Path,
) -> None:
    output = tmp_path / "artifacts" / "concurrent.json"
    output.parent.mkdir()
    first = {
        "source_snapshot": {"roots": []},
        "stage": "migration_plan_draft",
        "variant": "first",
    }
    second = {
        "source_snapshot": {"roots": []},
        "stage": "migration_plan_draft",
        "variant": "second",
    }

    assert _concurrent_artifact_writes(output, [first] * 8) == ["OK"] * 8
    assert output.read_bytes() == canonical_json_bytes(first) + b"\n"

    assert _concurrent_artifact_writes(output, [first] * 8) == ["OK"] * 8
    assert output.read_bytes() == canonical_json_bytes(first) + b"\n"

    output.unlink()
    observed = _concurrent_artifact_writes(output, [first] * 4 + [second] * 4)
    winner = json.loads(output.read_bytes())
    expected = [
        "OK" if artifact == winner else "PLAN_DRIFT"
        for artifact in [first] * 4 + [second] * 4
    ]
    assert observed == expected
    assert output.read_bytes() == canonical_json_bytes(winner) + b"\n"


def test_cross_process_bound_input_readers_are_compatible(tmp_path: Path) -> None:
    source = tmp_path / "artifacts" / "bound-input.bin"
    source.parent.mkdir()
    payload = b"sanitized-bound-input\n" * 200_000
    source.write_bytes(payload)
    expected = hashlib.sha256(payload).hexdigest()
    gate = source.parent / "start-concurrent-reads"
    script = r'''
import hashlib
from pathlib import Path
import sys
import time
from code_assist_v1.knowledge_core.cli import _read_bound_file

source = sys.argv[1]
gate = Path(sys.argv[2])
deadline = time.monotonic() + 20
while not gate.exists():
    if time.monotonic() >= deadline:
        raise SystemExit(97)
    time.sleep(0.001)
value = _read_bound_file(source, maximum_bytes=8 * 1024 * 1024)
print(hashlib.sha256(value).hexdigest())
'''
    processes = [
        subprocess.Popen(
            [str(_PYTHON), "-E", "-c", script, str(source), str(gate)],
            cwd=_WORKSPACE_ROOT,
            env={
                **os.environ,
                "PYTHONIOENCODING": "utf-8",
                "TEMP": r"D:\CodexTestTemp",
                "TMP": r"D:\CodexTestTemp",
                "TMPDIR": r"D:\CodexTestTemp",
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        for _ in range(8)
    ]
    gate.write_text("start", encoding="ascii")
    for process in processes:
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        assert stdout.strip() == expected


def test_p2_confirm_plan_rejects_stale_hash_without_creating_output(tmp_path: Path) -> None:
    inventory, mapping, draft_path, report, _ = _plan_files(tmp_path)
    assert _run(_plan_arguments(inventory, mapping, draft_path, report)).returncode == 0
    output = draft_path.with_name("reviewed.json")

    result = _run(
        [
            "migrate",
            "confirm-plan",
            "--draft",
            str(draft_path),
            "--draft-sha256",
            "d" * 64,
            "--review-attestation-sha256",
            "e" * 64,
            "--reviewed-at-utc",
            "2026-08-13T01:02:03.000000Z",
            "--reviewer-id",
            "reviewer-01",
            "--output",
            str(output),
        ]
    )

    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] in {"PLAN_INVALID", "REVIEW_INVALID"}
    assert not output.exists()


def test_p2_plan_cli_import_is_inert() -> None:
    script = r'''
import sqlite3
import threading

before = {thread.ident for thread in threading.enumerate()}
opened = False
original = sqlite3.connect
def forbidden(*args, **kwargs):
    global opened
    opened = True
    raise AssertionError("database opened during import")
sqlite3.connect = forbidden
try:
    import code_assist_v1.knowledge_core.cli
    import code_assist_v1.knowledge_core.legacy.plan_contract
    import code_assist_v1.knowledge_core.legacy.planner
finally:
    sqlite3.connect = original
after = {thread.ident for thread in threading.enumerate()}
assert not opened
assert after == before
'''
    result = subprocess.run(
        [str(_PYTHON), "-E", "-c", script],
        cwd=_WORKSPACE_ROOT,
        env={**os.environ, "TEMP": r"D:\CodexTestTemp", "TMP": r"D:\CodexTestTemp"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
