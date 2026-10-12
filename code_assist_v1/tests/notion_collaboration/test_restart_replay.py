from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from code_assist_v1.notion_collaboration.canonical_authority_prototype import (
    DisposableAuthority,
    initialize_disposable_authority,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

_SAVE_WORKER = r"""
import json
import os
import sys

from code_assist_v1.notion_collaboration.canonical_authority_prototype import (
    DisposableAuthority,
    InjectedFailure,
    OperationConflict,
    RevisionConflict,
)

root, operation_id, text, revision, fault = sys.argv[1:]
authority = DisposableAuthority.open(root)
try:
    result = authority.save_page(
        "owner",
        "page-a",
        {"blocks": [{"id": "block-a", "text": text}]},
        expected_revision=int(revision),
        expected_workspace_epoch=1,
        expected_page_epoch=1,
        operation_id=operation_id,
        fault=None if fault == "none" else fault,
    )
except InjectedFailure as exc:
    print(json.dumps({"pid": os.getpid(), "error": "InjectedFailure", "detail": str(exc)}, sort_keys=True), flush=True)
    raise SystemExit(73)
except OperationConflict as exc:
    print(json.dumps({"pid": os.getpid(), "error": "OperationConflict", "detail": str(exc)}, sort_keys=True), flush=True)
    raise SystemExit(74)
except RevisionConflict as exc:
    print(json.dumps({"pid": os.getpid(), "error": "RevisionConflict", "revision": exc.current_revision}, sort_keys=True), flush=True)
    raise SystemExit(75)
else:
    print(json.dumps({"pid": os.getpid(), "result": result}, sort_keys=True), flush=True)
"""


def _seed(tmp_path: Path) -> DisposableAuthority:
    authority = initialize_disposable_authority(tmp_path / "restart-realm")
    authority.create_workspace(
        "owner", "workspace-a", operation_id="op-workspace-create-restart-0001"
    )
    authority.create_page(
        "owner",
        "workspace-a",
        "page-a",
        "Restart",
        {"blocks": [{"id": "block-a", "text": "base"}]},
        expected_workspace_epoch=1,
        operation_id="op-page-create-restart-0001",
    )
    return authority


def _command(
    root: Path,
    operation_id: str,
    text: str,
    revision: int,
    fault: str = "none",
) -> list[str]:
    return [
        sys.executable,
        "-E",
        "-c",
        _SAVE_WORKER,
        os.fspath(root),
        operation_id,
        text,
        str(revision),
        fault,
    ]


def _payload(stdout: str) -> dict[str, object]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert lines, "worker emitted no JSON receipt"
    value = json.loads(lines[-1])
    assert isinstance(value, dict)
    return value


@pytest.mark.integration
@pytest.mark.concurrency
def test_a0a_p2_same_operation_replays_exactly_once_across_real_processes(
    tmp_path: Path,
) -> None:
    authority = _seed(tmp_path)
    before = authority.counts()
    command = _command(
        authority.root,
        "op-save-cross-process-replay-0001",
        "restart replay",
        1,
    )

    first = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    second = subprocess.Popen(
        command,
        cwd=REPOSITORY_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=20)
    second_stdout, second_stderr = second.communicate(timeout=20)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr
    assert first.pid != second.pid
    assert first.pid != os.getpid() and second.pid != os.getpid()
    first_receipt = _payload(first_stdout)
    second_receipt = _payload(second_stdout)
    # On Windows a venv launcher PID may differ from the interpreter worker PID.
    # The receipts come from inside each worker and therefore prove the actual
    # processes that opened the SQLite authority.
    assert first_receipt["pid"] != second_receipt["pid"]
    assert first_receipt["pid"] != os.getpid()
    assert second_receipt["pid"] != os.getpid()
    assert first_receipt["result"] == second_receipt["result"]

    reopened = DisposableAuthority.open(authority.root)
    after = reopened.counts()
    for name in ("operation_dedup", "durable_events", "audit_events", "outbox_events"):
        assert after[name] == before[name] + 1
    page = reopened.read_page(
        "owner", "page-a", expected_workspace_epoch=1, expected_page_epoch=1
    )
    assert page["revision"] == 2
    assert len(
        reopened.event_rows(operation_id="op-save-cross-process-replay-0001")
    ) == 1

    conflict = subprocess.run(
        _command(
            authority.root,
            "op-save-cross-process-replay-0001",
            "different request",
            1,
        ),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert conflict.returncode == 74, conflict.stderr
    assert _payload(conflict.stdout)["error"] == "OperationConflict"
    assert reopened.counts() == after


@pytest.mark.integration
@pytest.mark.fault
def test_a0a_p2_failed_child_rolls_back_and_clean_restart_can_retry(tmp_path: Path) -> None:
    authority = _seed(tmp_path)
    before = authority.counts()
    before_page = authority.read_page(
        "owner", "page-a", expected_workspace_epoch=1, expected_page_epoch=1
    )
    operation_id = "op-save-cross-process-fault-0001"

    failed = subprocess.run(
        _command(authority.root, operation_id, "must roll back", 1, "after_outbox"),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert failed.returncode == 73, failed.stderr
    failed_receipt = _payload(failed.stdout)
    assert failed_receipt["pid"] != os.getpid()
    assert failed_receipt["error"] == "InjectedFailure"

    reopened = DisposableAuthority.open(authority.root)
    assert reopened.counts() == before
    assert reopened.operation_result("owner", "page.saved", operation_id) is None
    assert reopened.event_rows(operation_id=operation_id) == []
    assert reopened.outbox_rows(operation_id=operation_id) == []
    assert reopened.read_page(
        "owner", "page-a", expected_workspace_epoch=1, expected_page_epoch=1
    ) == before_page
    assert reopened.integrity()["quick_check"] == "ok"

    recovered = subprocess.run(
        _command(authority.root, operation_id, "must roll back", 1),
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert recovered.returncode == 0, recovered.stderr
    recovered_result = _payload(recovered.stdout)["result"]
    assert isinstance(recovered_result, dict)
    assert recovered_result["revision"] == 2
    assert len(reopened.event_rows(operation_id=operation_id)) == 1
    assert len(reopened.outbox_rows(operation_id=operation_id)) == 1
