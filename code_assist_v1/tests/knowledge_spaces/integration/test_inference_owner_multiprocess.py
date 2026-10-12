from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from code_assist_v1.model_runtime import (
    InferenceOwnerBusyError,
    hold_inference_ownership,
)


pytestmark = pytest.mark.integration


def _wait_for_line(process: subprocess.Popen[str], timeout: float = 15.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = process.stdout.readline() if process.stdout is not None else ""
        if line:
            return line.strip()
        if process.poll() is not None:
            break
        time.sleep(0.02)
    stderr = process.stderr.read() if process.stderr is not None else ""
    raise AssertionError(
        f"inference owner child did not become ready: rc={process.poll()} stderr={stderr}"
    )


@pytest.mark.scenario("JOB-005")
def test_two_web_processes_cannot_become_local_inference_owners(
    isolated_knowledge_layout,
) -> None:
    project_parent = Path(__file__).resolve().parents[4]
    script = r"""
import sys
sys.path.insert(0, sys.argv[1])
from code_assist_v1.model_runtime import LocalInferenceOwnershipError, assert_local_inference_allowed
try:
    assert_local_inference_allowed()
except LocalInferenceOwnershipError:
    print('BLOCKED', flush=True)
    raise SystemExit(0)
raise SystemExit(9)
"""
    processes = tuple(
        subprocess.Popen(
            [sys.executable, "-E", "-c", script, os.fspath(project_parent)],
            cwd=os.fspath(isolated_knowledge_layout.root),
            env={**os.environ, "CODE_ASSIST_PROCESS_ROLE": "web"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(2)
    )
    results = tuple(process.communicate(timeout=15) for process in processes)
    assert [process.returncode for process in processes] == [0, 0]
    assert [stdout.strip() for stdout, _ in results] == ["BLOCKED", "BLOCKED"]
    assert [stderr for _, stderr in results] == ["", ""]


@pytest.mark.scenario("JOB-005")
def test_only_one_process_can_own_local_inference_for_the_gpu_server(
    monkeypatch: pytest.MonkeyPatch,
    isolated_knowledge_layout,
) -> None:
    monkeypatch.setenv("CODE_ASSIST_PROCESS_ROLE", "worker")
    project_parent = Path(__file__).resolve().parents[4]
    database_path = isolated_knowledge_layout.core_db_path
    stop_path = isolated_knowledge_layout.root / "release-owner"
    script = r"""
import os,sys,time
sys.path.insert(0, sys.argv[1])
from code_assist_v1.model_runtime import hold_inference_ownership
os.environ['CODE_ASSIST_PROCESS_ROLE']='worker'
with hold_inference_ownership(sys.argv[2]):
    print('READY', flush=True)
    while not os.path.exists(sys.argv[3]):
        time.sleep(0.02)
"""
    child = subprocess.Popen(
        [
            sys.executable,
            "-E",
            "-c",
            script,
            os.fspath(project_parent),
            os.fspath(database_path),
            os.fspath(stop_path),
        ],
        cwd=os.fspath(isolated_knowledge_layout.root),
        env={**os.environ, "CODE_ASSIST_PROCESS_ROLE": "worker"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert _wait_for_line(child) == "READY"
        # A different database configuration is not a second GPU lane.
        with pytest.raises(InferenceOwnerBusyError):
            with hold_inference_ownership(
                isolated_knowledge_layout.root / "other-core.sqlite3"
            ):
                pass
        # Simulate a service crash: no context-manager release runs in child.
        child.kill()
        child.wait(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=15)
    assert child.returncode != 0

    # The kernel closes the crashed process handle; restart can take over.
    with hold_inference_ownership(database_path):
        pass
